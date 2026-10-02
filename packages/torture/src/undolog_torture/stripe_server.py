"""Out-of-process mock Stripe server (TS-04-partial).

The same FastAPI app the in-process suite uses (world/stripe.py:
build_stripe_app) runs under uvicorn on an ephemeral localhost port, with
the StripeWorld state persisted to a JSON file after every mutation. A
SIGKILL therefore loses nothing committed, and a restart reloads the world
— which is what lets the TS-04-partial test prove the idempotency
guarantee across a real process (and network) boundary.

Server side:
    python -m undolog_torture.stripe_server --state-file F --port P

Client side (used by the test and by StripeExecutor):
    HttpStripe(base_url)  — charges()/refunds()/balance() readers plus
    create_refund(...), the surface StripeExecutor needs.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Optional

import httpx

from .world.stripe import StripeWorld

DEFAULT_BIND = "127.0.0.1"


# --------------------------------------------------------------------------
# Server entry point
# --------------------------------------------------------------------------

def _load_world(state_file: Path) -> StripeWorld:
    world = StripeWorld()
    if state_file.exists():
        data = json.loads(state_file.read_text(encoding="utf-8"))
        world.charges = [dict(c) for c in data.get("charges", [])]
        world.refunds = [dict(r) for r in data.get("refunds", [])]
    return world


def _persist_world(world: StripeWorld, state_file: Path) -> None:
    tmp = state_file.with_suffix(".tmp")
    tmp.write_text(json.dumps({
        "charges": world.charges,
        "refunds": world.refunds,
    }), encoding="utf-8")
    os.replace(tmp, state_file)   # atomic: a SIGKILL mid-write cannot
                                  # produce a torn state file


class _StatePersistASGI:
    """Save the world after every mutating (POST) request."""

    def __init__(self, app, world: StripeWorld, state_file: Path):
        self._app = app
        self._world = world
        self._state_file = state_file

    async def __call__(self, scope, receive, send):
        await self._app(scope, receive, send)
        if scope["type"] == "http" and scope["method"] == "POST":
            _persist_world(self._world, self._state_file)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m undolog_torture.stripe_server")
    parser.add_argument("--state-file", required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--host", default=DEFAULT_BIND)
    args = parser.parse_args(argv)

    import uvicorn
    from .world import build_stripe_app

    state_file = Path(args.state_file)
    world = _load_world(state_file)

    def _persist_middleware(app):
        return _StatePersistASGI(app, world, state_file)

    app = build_stripe_app(world)
    app.add_middleware(_persist_middleware)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())


# --------------------------------------------------------------------------
# Client shim — the surface StripeExecutor and the test need, over HTTP
# --------------------------------------------------------------------------

class HttpStripe:
    """HTTP client for the out-of-process mock Stripe.

    Exposes the same methods StripeExecutor uses on an in-process
    StripeWorld (create_refund) plus read accessors for assertions.
    """

    def __init__(self, base_url: str, timeout: float = 10.0):
        self._base = base_url.rstrip("/")
        self._timeout = timeout

    # -- reads -------------------------------------------------------------
    def charges(self) -> list[dict[str, Any]]:
        r = httpx.get(f"{self._base}/charges", timeout=self._timeout)
        r.raise_for_status()
        return r.json()

    def refunds(self) -> list[dict[str, Any]]:
        r = httpx.get(f"{self._base}/refunds", timeout=self._timeout)
        r.raise_for_status()
        return r.json()

    def balance(self) -> int:
        r = httpx.get(f"{self._base}/balance", timeout=self._timeout)
        r.raise_for_status()
        return r.json()["balance"]

    # -- writes (executor surface) ------------------------------------------
    def create_refund(self, charge_id: str,
                      idempotency_key: Optional[str] = None) -> dict[str, Any]:
        r = httpx.post(f"{self._base}/refunds",
                       json={"charge_id": charge_id,
                             "idempotency_key": idempotency_key},
                       timeout=self._timeout)
        if r.status_code == 404:
            raise KeyError(f"no such charge {charge_id!r}")
        if r.status_code == 409:
            raise ValueError(f"charge {charge_id} already refunded")
        r.raise_for_status()
        return r.json()


# --------------------------------------------------------------------------
# Process manager (test fixture side)
# --------------------------------------------------------------------------

def _free_port(host: str = DEFAULT_BIND) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        return s.getsockname()[1]


class MockStripeServer:
    """Start/kill/restart the mock Stripe server as a real process.

    State lives in ``state_file``; kill() is SIGKILL (no cleanup, nothing
    flushed — persistence happens per-request in the server), and start()
    after kill() reloads the world from disk, proving the boundary.
    """

    def __init__(self, state_file, host: str = DEFAULT_BIND, port: int = 0):
        self.state_file = Path(state_file)
        self.host = host
        self._port = port
        self.proc: Optional[subprocess.Popen] = None

    # -- lifecycle ---------------------------------------------------------
    def start(self, timeout: float = 20.0) -> None:
        if self.proc is not None and self.proc.poll() is None:
            return
        if not self._port:
            self._port = _free_port(self.host)
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "undolog_torture.stripe_server",
             "--state-file", str(self.state_file),
             "--host", self.host, "--port", str(self._port)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        self._wait_ready(timeout)

    def _wait_ready(self, timeout: float) -> None:
        deadline = time.perf_counter() + timeout
        while time.perf_counter() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(
                    f"stripe server exited early rc={self.proc.returncode}")
            try:
                httpx.get(f"{self.base_url}/charges", timeout=1.0)
                return
            except (httpx.ConnectError, httpx.TimeoutException):
                time.sleep(0.05)
        self.kill()
        raise RuntimeError("stripe server did not become ready")

    def kill(self) -> None:
        """SIGKILL: the process dies with no chance to flush anything."""
        if self.proc is None:
            return
        self.proc.send_signal(signal.SIGKILL)
        self.proc.wait(timeout=10)
        self.proc = None

    def stop(self) -> None:
        if self.proc is None:
            return
        self.proc.terminate()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.kill()
        self.proc = None

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self._port}"
