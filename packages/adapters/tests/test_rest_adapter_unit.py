"""RestAdapter unit + integration tests over an httpx MockTransport.

The wrapped fns perform their own side-effect HTTP calls through a client
wired to the shared stub world; the adapter is bound per call with the
tool kwargs (``rt.bind(...)`` / ``rt.wrap_call(...)``) because core's
capture protocol passes no call arguments. Rollback drives the SAME
RestAdapter instance as the RollbackExecutor.
"""
from uuid import uuid4

import httpx
import pytest
from sqlmodel import Session, select
from undolog_core.ledger import Ledger
from undolog_core.models import EntryClass, LedgerEntry
from undolog_core.registry import load_registry

from undolog_adapters import (
    DEFAULT_REGISTRY_DIR,
    RestAdapter,
    RestError,
)

from conftest import stub_env
from stub_api import StubWorld, make_httpx_handler

BASE = "http://stub"


@pytest.fixture()
def world():
    return StubWorld()


@pytest.fixture()
def client(world):
    return httpx.Client(transport=httpx.MockTransport(make_httpx_handler(world)),
                        base_url=BASE, timeout=10)


def tool_spec(name):
    spec = load_registry(registry_dir=DEFAULT_REGISTRY_DIR).lookup(name)
    assert spec is not None, f"bundled registry is missing {name}"
    return spec.adapter


def make_adapter(name, env, client):
    return RestAdapter(name, tool_spec(name), env=stub_env(env), client=client)


def ledger_row(engine, thread_id):
    with Session(engine) as session:
        return session.exec(
            select(LedgerEntry).where(LedgerEntry.thread_id == thread_id)
        ).one()


# --------------------------------------------------------------------------
# github.create_issue — compensatable
# --------------------------------------------------------------------------

def test_github_create_issue_wrap_then_rollback_closes_exactly_once(
    engine, world, client
):
    env = {"GITHUB_TOKEN": "gh-tok", "GITHUB_BASE_URL": BASE}
    rt = make_adapter("github.create_issue", env, client)
    ledger = Ledger(engine, registry=load_registry(registry_dir=DEFAULT_REGISTRY_DIR))
    tl = ledger.for_thread(f"rest-gh-{uuid4().hex[:8]}")

    def create_issue():
        r = client.post("/repos/acme/web/issues", json={"title": "bug"})
        r.raise_for_status()
        return r.json()

    result = rt.wrap_call(tl, create_issue, repo="acme/web", title="bug")
    assert result["number"] == 1

    row = ledger_row(engine, tl.thread_id)
    assert row.class_ == "compensatable"
    assert row.after_jsonb["result"]["number"] == 1
    assert row.before_jsonb is None  # creates have no before-value

    report = ledger.rollback(tl.thread_id, 0, {"github.create_issue": rt})
    assert report.complete
    assert world.issues["acme/web#1"]["state"] == "closed"
    patches = world.applied("PATCH", "/issues/1")
    assert len(patches) == 1, "compensation PATCHes exactly once"
    assert patches[0]["idempotency_key"] == f"comp-{row.id}"
    assert patches[0]["body"] == {"state": "closed"}

    # Engine-level replay: already-compensated entries are skipped.
    report2 = ledger.rollback(tl.thread_id, 0, {"github.create_issue": rt})
    assert report2.complete
    assert len(world.applied("PATCH", "/issues/1")) == 1

    # Executor-level replay with the same key: stub idempotency dedups.
    rt.compensate(row, f"comp-{row.id}")
    assert len(world.applied("PATCH", "/issues/1")) == 1


def test_github_sends_bearer_token_from_env(engine, world, client):
    env = {"GITHUB_TOKEN": "gh-tok", "GITHUB_BASE_URL": BASE}
    rt = make_adapter("github.create_issue", env, client)
    assert rt._auth_headers() == {"Authorization": "Bearer gh-tok"}
    ledger = Ledger(engine, registry=load_registry(registry_dir=DEFAULT_REGISTRY_DIR))
    tl = ledger.for_thread(f"rest-gh2-{uuid4().hex[:8]}")

    def create_issue():
        r = client.post("/repos/acme/web/issues", json={"title": "x"})
        r.raise_for_status()
        return r.json()

    rt.wrap_call(tl, create_issue, repo="acme/web", title="x")
    ledger.rollback(tl.thread_id, 0, {"github.create_issue": rt})
    assert len(world.applied("PATCH", "/issues/1")) == 1


# --------------------------------------------------------------------------
# s3.put_object — reversible overwrite / create
# --------------------------------------------------------------------------

def test_s3_overwrite_captures_before_and_restores_old_bytes(
    engine, world, client
):
    world.objects["b/k"] = b"old-bytes"
    env = {"AWS_SIGNATURE": "sig"}
    rt = make_adapter("s3.put_object", env, client)
    ledger = Ledger(engine, registry=load_registry(registry_dir=DEFAULT_REGISTRY_DIR))
    tl = ledger.for_thread(f"rest-s3-{uuid4().hex[:8]}")

    def put_object():
        r = client.put("/b/k", content=b"new-bytes")
        r.raise_for_status()
        return {"etag": "xyz"}

    rt.wrap_call(tl, put_object, endpoint=BASE, Bucket="b", Key="k")
    assert world.objects["b/k"] == b"new-bytes"

    row = ledger_row(engine, tl.thread_id)
    assert row.class_ == "reversible"
    assert row.before_jsonb["value"] == "old-bytes", "GET body captured as before"

    report = ledger.rollback(tl.thread_id, 0, {"s3.put_object": rt})
    assert report.complete
    assert world.objects["b/k"] == b"old-bytes", "restore PUTs old bytes back"
    puts = world.applied("PUT", "/b/k")
    assert len(puts) == 2  # fn PUT + restore PUT
    assert puts[-1]["body"] == {"bytes": 9}  # restore PUT body = old bytes


def test_s3_new_object_null_before_restore_deletes(
    engine, world, client
):
    env = {"AWS_SIGNATURE": "sig"}
    rt = make_adapter("s3.put_object", env, client)
    ledger = Ledger(engine, registry=load_registry(registry_dir=DEFAULT_REGISTRY_DIR))
    tl = ledger.for_thread(f"rest-s3n-{uuid4().hex[:8]}")

    def put_object():
        r = client.put("/b/new-key", content=b"created")
        r.raise_for_status()
        return {"etag": "e1"}

    rt.wrap_call(tl, put_object, endpoint=BASE, Bucket="b", Key="new-key")
    row = ledger_row(engine, tl.thread_id)
    assert row.before_jsonb["value"] is None, "404 -> null before-value"

    report = ledger.rollback(tl.thread_id, 0, {"s3.put_object": rt})
    assert report.complete
    assert "b/new-key" not in world.objects, "null before -> DELETE"
    assert len(world.applied("DELETE", "/b/new-key")) == 1


def test_s3_sends_configured_header_auth(engine, world, client):
    env = {"AWS_SIGNATURE": "sig"}
    rt = make_adapter("s3.put_object", env, client)
    req = client.build_request(
        "GET", f"{BASE}/b/k", headers=rt._auth_headers()
    )
    assert req.headers["X-Aws-Signature"] == "sig"


# --------------------------------------------------------------------------
# gcal.events_insert — compensatable
# --------------------------------------------------------------------------

def test_gcal_insert_rollback_deletes_by_result_id(engine, world, client):
    env = {"GOOGLE_TOKEN": "g-tok", "GOOGLE_BASE_URL": BASE}
    rt = make_adapter("gcal.events_insert", env, client)
    ledger = Ledger(engine, registry=load_registry(registry_dir=DEFAULT_REGISTRY_DIR))
    tl = ledger.for_thread(f"rest-gcal-{uuid4().hex[:8]}")

    def insert_event():
        r = client.post("/calendars/primary/events",
                        json={"summary": "standup"})
        r.raise_for_status()
        return r.json()

    result = rt.wrap_call(tl, insert_event, calendarId="primary")
    assert result["id"] == "evt_1"
    assert "primary#evt_1" in world.events

    row = ledger_row(engine, tl.thread_id)
    report = ledger.rollback(tl.thread_id, 0, {"gcal.events_insert": rt})
    assert report.complete
    assert "primary#evt_1" not in world.events
    deletes = world.applied("DELETE", "/events/evt_1")
    assert len(deletes) == 1
    assert deletes[0]["idempotency_key"] == f"comp-{row.id}"


# --------------------------------------------------------------------------
# hubspot.update_contact — reversible, dotted-path store
# --------------------------------------------------------------------------

def test_hubspot_update_captures_properties_and_restores_them(
    engine, world, client
):
    world.contacts["42"] = {"properties": {"email": "a@b.c", "stage": "new"}}
    env = {"HUBSPOT_TOKEN": "hs-tok", "HUBSPOT_BASE_URL": BASE}
    rt = make_adapter("hubspot.update_contact", env, client)
    ledger = Ledger(engine, registry=load_registry(registry_dir=DEFAULT_REGISTRY_DIR))
    tl = ledger.for_thread(f"rest-hs-{uuid4().hex[:8]}")

    def update_contact():
        r = client.patch("/crm/v3/objects/contacts/42",
                         json={"properties": {"stage": "won"}})
        r.raise_for_status()
        return r.json()

    rt.wrap_call(tl, update_contact, contactId="42")
    assert world.contacts["42"]["properties"]["stage"] == "won"

    row = ledger_row(engine, tl.thread_id)
    assert row.class_ == "reversible"
    assert row.before_jsonb["value"] == {"email": "a@b.c", "stage": "new"}, (
        "store: properties selector keeps only response.properties"
    )

    report = ledger.rollback(tl.thread_id, 0, {"hubspot.update_contact": rt})
    assert report.complete
    assert world.contacts["42"]["properties"] == {"email": "a@b.c", "stage": "new"}
    patches = world.applied("PATCH", "/contacts/42")
    assert patches[-1]["body"] == {"properties": {"email": "a@b.c", "stage": "new"}}


# --------------------------------------------------------------------------
# slack.chat_postMessage — irreversible, adapter null: zero HTTP
# --------------------------------------------------------------------------

def test_slack_records_irreversible_and_refuses_without_any_http(
    engine, world, client
):
    ledger = Ledger(
        engine, registry=load_registry(registry_dir=DEFAULT_REGISTRY_DIR)
    )
    tl = ledger.for_thread(f"rest-slack-{uuid4().hex[:8]}")

    @tl.wrap(tool_name="slack.chat_postMessage")
    def post_message(channel, text):
        return {"ok": True, "ts": "1.000"}

    post_message("#general", "hello")
    row = ledger_row(engine, tl.thread_id)
    assert row.class_ == "irreversible"
    assert world.requests == [], "no HTTP whatsoever for slack"

    report = ledger.rollback(tl.thread_id, 0, {})
    assert report.complete
    assert len(report.blast_radius) == 1
    assert report.blast_radius[0].tool_name == "slack.chat_postMessage"
    assert "irreversible" in report.blast_radius[0].reason
    assert world.requests == [], "rollback refused without touching HTTP"


# --------------------------------------------------------------------------
# Error contract
# --------------------------------------------------------------------------

def test_missing_auth_env_var_raises_clear_error(world, client):
    rt = make_adapter("github.create_issue", {"GITHUB_BASE_URL": BASE}, client)
    entry = LedgerEntry(
        id=uuid4(), thread_id="t", seq=1, tool_name="github.create_issue",
        args_hash="h", idempotency_key="k",
        after_jsonb={"args": {}, "result": {"number": 1}},
        class_=EntryClass.COMPENSATABLE, status="applied",
    )
    with pytest.raises(RestError, match="GITHUB_TOKEN"):
        rt.compensate(entry, "comp-x")


def test_missing_template_arg_raises_clear_error(world, client):
    env = {"AWS_SIGNATURE": "sig"}
    rt = make_adapter("s3.put_object", env, client)
    bound = rt.bind(endpoint=BASE, Bucket="b")  # missing Key
    with pytest.raises(RestError, match="Key"):
        bound.capture_before()


def test_bundled_registry_exposes_all_five_specs():
    registry = load_registry(registry_dir=DEFAULT_REGISTRY_DIR)
    for name in ("github.create_issue", "s3.put_object", "gcal.events_insert",
                 "hubspot.update_contact", "slack.chat_postMessage"):
        spec = registry.lookup(name)
        assert spec is not None, name
    assert registry.lookup("slack.chat_postMessage").adapter is None
