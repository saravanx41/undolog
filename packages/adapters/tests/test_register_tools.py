"""register_tools: the developer-facing entry point.

    ledger = Ledger(engine)
    for tool in ["github.create_issue", "s3.put_object", ...]:
        register_tools(ledger, tool, env=os.environ)

Effect: ledger.registry learns each tool's class (seed + bundled dir
merged), and the returned mapping gives the RestAdapter for capture (via
``rt.bind(...)`` / ``rt.wrap_call(...)``) and rollback (passed as the
executors mapping). Tools with ``adapter: null`` (slack) map to None —
ledger + containment only, no HTTP ever happens.
"""
import os
from uuid import uuid4

import httpx
import pytest
from sqlmodel import Session, select
from undolog_core.ledger import Ledger
from undolog_core.models import LedgerEntry

from undolog_adapters import (
    DEFAULT_REGISTRY_DIR,
    RestAdapter,
    RestError,
    register_tools,
)

from conftest import stub_env
from stub_api import StubWorld, make_httpx_handler

BASE = "http://stub"
ALL_FIVE = [
    "github.create_issue",
    "s3.put_object",
    "gcal.events_insert",
    "hubspot.update_contact",
    "slack.chat_postMessage",
]


@pytest.fixture()
def world():
    return StubWorld()


@pytest.fixture()
def client(world):
    return httpx.Client(transport=httpx.MockTransport(make_httpx_handler(world)),
                        base_url=BASE, timeout=10)


@pytest.fixture(autouse=True)
def patch_default_client(world, monkeypatch):
    """Adapters created by register_tools (no explicit client) must also
    hit the stub, not the real network."""
    stub_client = httpx.Client(
        transport=httpx.MockTransport(make_httpx_handler(world)),
        base_url=BASE, timeout=10,
    )
    monkeypatch.setattr(
        "undolog_adapters.rest.default_client", lambda: stub_client
    )


def _rows(engine, thread_id):
    with Session(engine) as session:
        return session.exec(
            select(LedgerEntry).where(LedgerEntry.thread_id == thread_id)
        ).all()


def test_register_tools_loop_registers_all_five(engine):
    ledger = Ledger(engine)
    env = stub_env({})
    for tool in ALL_FIVE:
        register_tools(ledger, tool, env=env)
    for tool in ALL_FIVE:
        assert ledger.registry.lookup(tool) is not None, tool
    # seed intact + five new = 15
    assert len(ledger.registry) == 15
    assert ledger.registry.lookup("gmail.send").entry_class == "irreversible"
    assert ledger.registry.lookup("github.create_issue").entry_class == "compensatable"
    assert ledger.registry.lookup("s3.put_object").entry_class == "reversible"
    assert ledger.registry.lookup("slack.chat_postMessage").entry_class == "irreversible"


def test_register_tools_accepts_a_list(engine):
    ledger = Ledger(engine)
    adapters = register_tools(ledger, ALL_FIVE, env=stub_env({}))
    assert set(adapters) == set(ALL_FIVE)
    assert isinstance(adapters["github.create_issue"], RestAdapter)
    assert adapters["slack.chat_postMessage"] is None  # adapter: null


def test_wrap_records_the_registry_class_for_each_tool(engine, client, world):
    world.contacts["9"] = {"properties": {"stage": "new"}}
    ledger = Ledger(engine)
    env = stub_env({
        "GITHUB_TOKEN": "t", "GITHUB_BASE_URL": BASE,
        "GOOGLE_TOKEN": "t", "GOOGLE_BASE_URL": BASE,
        "HUBSPOT_TOKEN": "t", "HUBSPOT_BASE_URL": BASE,
        "AWS_SIGNATURE": "t",
    })
    adapters = register_tools(ledger, ALL_FIVE, env=env)

    tl = ledger.for_thread(f"reg-loop-{uuid4().hex[:8]}")

    adapters["github.create_issue"].wrap_call(
        tl, lambda: client.post("/repos/a/b/issues", json={}).json(),
        repo="a/b", title="t")
    adapters["s3.put_object"].wrap_call(
        tl, lambda: client.put("/b/k", content=b"x").json(),
        endpoint=BASE, Bucket="b", Key="k")
    adapters["gcal.events_insert"].wrap_call(
        tl, lambda: client.post("/calendars/c/events", json={}).json(),
        calendarId="c")
    adapters["hubspot.update_contact"].wrap_call(
        tl, lambda: client.patch("/crm/v3/objects/contacts/9",
                                 json={"properties": {}}).json(),
        contactId="9")

    @tl.wrap(tool_name="slack.chat_postMessage")
    def post_message(channel, text):
        return {"ok": True}

    post_message("#c", "hi")

    rows = {r.tool_name: r for r in _rows(engine, tl.thread_id)}
    assert rows["github.create_issue"].class_ == "compensatable"
    assert rows["s3.put_object"].class_ == "reversible"
    assert rows["gcal.events_insert"].class_ == "compensatable"
    assert rows["hubspot.update_contact"].class_ == "reversible"
    assert rows["slack.chat_postMessage"].class_ == "irreversible"


def test_rollback_all_five_executors_and_slack_blast_radius(
    engine, client, world
):
    world.contacts["9"] = {"properties": {"stage": "new"}}
    ledger = Ledger(engine)
    env = stub_env({
        "GITHUB_TOKEN": "t", "GITHUB_BASE_URL": BASE,
        "GOOGLE_TOKEN": "t", "GOOGLE_BASE_URL": BASE,
        "HUBSPOT_TOKEN": "t", "HUBSPOT_BASE_URL": BASE,
        "AWS_SIGNATURE": "t",
    })
    adapters = register_tools(ledger, ALL_FIVE, env=env)
    tl = ledger.for_thread(f"reg-rb-{uuid4().hex[:8]}")

    adapters["github.create_issue"].wrap_call(
        tl, lambda: client.post("/repos/a/b/issues", json={}).json(),
        repo="a/b", title="t")
    adapters["s3.put_object"].wrap_call(
        tl, lambda: client.put("/b/k", content=b"x").json(),
        endpoint=BASE, Bucket="b", Key="k")
    adapters["gcal.events_insert"].wrap_call(
        tl, lambda: client.post("/calendars/c/events", json={}).json(),
        calendarId="c")
    adapters["hubspot.update_contact"].wrap_call(
        tl, lambda: client.patch("/crm/v3/objects/contacts/9",
                                 json={"properties": {"stage": "won"}}).json(),
        contactId="9")

    @tl.wrap(tool_name="slack.chat_postMessage")
    def post_message(channel, text):
        return {"ok": True}

    post_message("#c", "hi")

    executors = {k: v for k, v in adapters.items() if v is not None}
    report = ledger.rollback(tl.thread_id, 0, executors)
    assert report.complete
    assert {i.tool_name for i in report.compensated} == {
        "github.create_issue", "gcal.events_insert",
    }
    assert {i.tool_name for i in report.restored} == {
        "s3.put_object", "hubspot.update_contact",
    }
    assert [i.tool_name for i in report.blast_radius] == ["slack.chat_postMessage"]
    # Effects actually undone:
    assert world.issues["a/b#1"]["state"] == "closed"
    assert "b/k" not in world.objects
    assert not world.events
    assert world.contacts["9"]["properties"] == {"stage": "new"}


def test_unknown_tool_raises_clear_error(engine):
    ledger = Ledger(engine)
    with pytest.raises(RestError, match="nope.missing"):
        register_tools(ledger, "nope.missing", env=stub_env({}))


def test_env_defaults_to_os_environ(engine, monkeypatch):
    monkeypatch.setenv("HUBSPOT_TOKEN", "os-env-token")
    monkeypatch.setenv("HUBSPOT_BASE_URL", BASE)
    ledger = Ledger(engine)
    adapters = register_tools(ledger, "hubspot.update_contact", env=None)
    assert adapters["hubspot.update_contact"]._auth_headers() == {
        "Authorization": "Bearer os-env-token"
    }


def test_missing_token_errors_at_first_call(engine, client):
    """Documented choice: auth env vars are resolved lazily — a missing
    token raises a clear RestError on the first HTTP-touching call (for a
    create tool like github.create_issue, that is compensation)."""
    ledger = Ledger(engine)
    adapters = register_tools(
        ledger, "github.create_issue",
        env=stub_env({"GITHUB_BASE_URL": BASE}),  # no GITHUB_TOKEN
    )
    rt = adapters["github.create_issue"]
    tl = ledger.for_thread(f"reg-notok-{uuid4().hex[:8]}")
    rt.wrap_call(tl, lambda: {"number": 1}, repo="a/b", title="t")
    row = _rows(engine, tl.thread_id)[0]
    with pytest.raises(RestError, match="GITHUB_TOKEN"):
        rt.compensate(row, f"comp-{row.id}")


def test_bundled_registry_dir_contains_exactly_the_five_files():
    files = sorted(p.name for p in DEFAULT_REGISTRY_DIR.glob("*.yaml"))
    assert files == sorted(
        n + ".yaml" for n in ALL_FIVE
    ), "public registry dir must contain exactly the five new tool files"
