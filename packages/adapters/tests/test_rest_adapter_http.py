"""RestAdapter over REAL HTTP: the shared stub world served by uvicorn.

Per the TS-04-partial precedent, at least one test must cross a real
network boundary. The FastAPI app wrapping StubWorld runs in-process under
uvicorn on an ephemeral localhost port; the adapters and the wrapped tool
functions both speak to it over TCP via httpx.
"""
import threading
import time
from uuid import uuid4

import httpx
import pytest
import uvicorn
from sqlmodel import Session, select
from undolog_core.ledger import Ledger
from undolog_core.models import LedgerEntry

from undolog_adapters import register_tools

from stub_api import StubWorld, build_app


@pytest.fixture(scope="module")
def http_stub():
    """Uvicorn server on an ephemeral port, serving a fresh StubWorld."""
    world = StubWorld()
    config = uvicorn.Config(build_app(world), host="127.0.0.1", port=0,
                            log_level="warning")
    server = uvicorn.Server(config)
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    deadline = time.time() + 15
    while not server.started:
        if time.time() > deadline:
            raise RuntimeError("stub server did not start")
        time.sleep(0.01)
    port = server.servers[0].sockets[0].getsockname()[1]
    yield world, f"http://127.0.0.1:{port}"
    server.should_exit = True
    t.join(timeout=10)


def _row(engine, thread_id):
    with Session(engine) as session:
        return session.exec(
            select(LedgerEntry).where(LedgerEntry.thread_id == thread_id)
        ).one()


def test_github_create_issue_end_to_end_over_real_http(engine, http_stub):
    world, base = http_stub
    ledger = Ledger(engine)
    tools = register_tools(
        ledger,
        ["github.create_issue"],
        env={"GITHUB_TOKEN": "gh-tok", "GITHUB_BASE_URL": base},
    )
    rt = tools["github.create_issue"]
    tl = ledger.for_thread(f"http-gh-{uuid4().hex[:8]}")
    client = httpx.Client(base_url=base, timeout=10)

    def create_issue():
        r = client.post("/repos/acme/web/issues", json={"title": "bug"})
        r.raise_for_status()
        return r.json()

    result = rt.wrap_call(tl, create_issue, repo="acme/web", title="bug")
    assert result["number"] == 1

    report = ledger.rollback(tl.thread_id, 0, {"github.create_issue": rt})
    assert report.complete
    assert world.issues["acme/web#1"]["state"] == "closed"
    assert len(world.applied("PATCH", "/issues/1")) == 1
    assert world.requests, "requests must have crossed real HTTP"


def test_s3_overwrite_restore_over_real_http(engine, http_stub):
    world, base = http_stub
    world.objects["photos/cat.jpg"] = b"original-image"
    ledger = Ledger(engine)
    tools = register_tools(
        ledger, ["s3.put_object"], env={"AWS_SIGNATURE": "sig"}
    )
    rt = tools["s3.put_object"]
    tl = ledger.for_thread(f"http-s3-{uuid4().hex[:8]}")
    client = httpx.Client(base_url=base, timeout=10)

    def put_object():
        r = client.put("/photos/cat.jpg", content=b"edited-image")
        r.raise_for_status()
        return {"etag": "e2"}

    rt.wrap_call(tl, put_object, endpoint=base,
                 Bucket="photos", Key="cat.jpg")
    assert world.objects["photos/cat.jpg"] == b"edited-image"

    report = ledger.rollback(tl.thread_id, 0, {"s3.put_object": rt})
    assert report.complete
    assert world.objects["photos/cat.jpg"] == b"original-image"
    assert len(world.applied("PUT", "/photos/cat.jpg")) == 2


def test_s3_null_before_delete_over_real_http(engine, http_stub):
    world, base = http_stub
    ledger = Ledger(engine)
    tools = register_tools(
        ledger, ["s3.put_object"], env={"AWS_SIGNATURE": "sig"}
    )
    rt = tools["s3.put_object"]
    tl = ledger.for_thread(f"http-s3n-{uuid4().hex[:8]}")
    client = httpx.Client(base_url=base, timeout=10)

    def put_object():
        r = client.put("/photos/new.jpg", content=b"created")
        r.raise_for_status()
        return {"etag": "e3"}

    rt.wrap_call(tl, put_object, endpoint=base,
                 Bucket="photos", Key="new.jpg")
    row = _row(engine, tl.thread_id)
    assert row.before_jsonb["value"] is None

    report = ledger.rollback(tl.thread_id, 0, {"s3.put_object": rt})
    assert report.complete
    assert "photos/new.jpg" not in world.objects
    assert len(world.applied("DELETE", "/photos/new.jpg")) == 1
