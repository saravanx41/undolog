"""Task 4.1 web bridge: expose the core rollback engine to the Next.js UI.

The web app (packages/web) shells out to this module for rollback
previews/execution so the timeline UI shows exactly what the real core
would do — never a reimplementation:

    python -m undolog_torture.api rollback-preview \
        --thread pocketos-demo --to-seq 12 --json
    python -m undolog_torture.api rollback-execute \
        --thread pocketos-demo --to-seq 12 --json

Env honoured (inherited from the calling API route):
    DATABASE_URL      default postgresql+psycopg://undolog:undolog@localhost:5432/undolog
    UNDOLOG_SCHEMA    schema the engine searches (default "public")

NOTE: the ledger describes the CORRUPTED post-freeze demo world, so the
bridge reconstructs that world truthfully (a scratch-schema replay of
run_demo(rollback=False), cached per seed) before building executors —
restores/compensations run against the world the ledger actually
describes, while the ledger row statuses in Postgres update for real.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
from typing import Any, Optional

from undolog_core import Ledger

from .dbutil import (DEFAULT_DATABASE_URL, drop_schema, engine_for_schema,
                     setup_schema)
from .demo import run_demo
from .executors import build_executors
from .registry_ext import load_torture_registry
from .world import World

DEFAULT_SEED = 20260

# World reconstructions are valid for the process lifetime (one CLI call
# per invocation; the web route is a long-lived server and benefits).
_WORLD_CACHE: dict[int, World] = {}


def _normalize_url(url: str) -> str:
    """Accept plain ``postgresql://`` (as handed over by Node) too."""
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


def _ledger() -> Ledger:
    url = _normalize_url(os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL))
    schema = os.environ.get("UNDOLOG_SCHEMA", "public")
    engine = engine_for_schema(url, schema)
    return Ledger(engine, registry=load_torture_registry())


def _report_dict(report, dry_run: bool) -> dict[str, Any]:
    def item(i):
        d = dataclasses.asdict(i)
        d["entry_id"] = str(i.entry_id) if i.entry_id is not None else None
        return d

    return {
        "thread_id": report.thread_id,
        "to_seq": report.to_seq,
        "dry_run": dry_run,
        "complete": report.complete,
        "restored": [item(i) for i in report.restored],
        "compensated": [item(i) for i in report.compensated],
        "blast_radius": [item(i) for i in report.blast_radius],
        "failed": [item(i) for i in report.failed],
        "summary": {
            "restored": len(report.restored),
            "compensated": len(report.compensated),
            "irreversible": len(report.blast_radius),
            "failed": len(report.failed),
        },
    }


def _demo_world(seed: int) -> World:
    """The demo world in the state the ledger describes: corrupted, frozen,
    NOT rolled back. Replaying run_demo(rollback=False) in a throwaway
    schema yields exactly that world (~0.5s).

    The scratch schema is unique per (pid, seed) so it can never collide
    with the real ledger schema, and it is dropped in a finally block even
    when the replay raises. The demo's fixed thread_id and unique
    idempotency keys live and die entirely inside the scratch schema.
    """
    cached = _WORLD_CACHE.get(seed)
    if cached is not None:
        return cached
    url = _normalize_url(os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL))
    scratch = f"undolog_api_scratch_{os.getpid()}_{seed}"
    setup_schema(url, scratch)
    try:
        engine = engine_for_schema(url, scratch)
        try:
            world = run_demo(engine, seed=seed, fast=True, narrate=None,
                             rollback=False).world
        finally:
            engine.dispose()
    finally:
        drop_schema(url, scratch)
    _WORLD_CACHE[seed] = world
    return world


def rollback(thread_id: str, to_seq: int, *, dry_run: bool,
             seed: int = DEFAULT_SEED) -> dict[str, Any]:
    ledger = _ledger()
    # Even a dry run needs the executor mapping: the core refuses entries
    # whose tool has no registered executor before it reaches the dry-run
    # short-circuit, and the report must reflect the real plan. Crucially
    # the executors must target the world the LEDGER describes (the
    # corrupted one) — a fresh benign world makes restores/compensations
    # fail against state that was never written.
    executors = build_executors(_demo_world(seed))
    report = ledger.rollback(thread_id, to_seq, executors, dry_run=dry_run)
    return _report_dict(report, dry_run)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="undolog_torture.api",
                                     description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("rollback-preview", "rollback-execute"):
        p = sub.add_parser(name)
        p.add_argument("--thread", required=True)
        p.add_argument("--to-seq", type=int, required=True)
        p.add_argument("--seed", type=int, default=DEFAULT_SEED)
        p.add_argument("--json", action="store_true",
                       help="print the RollbackReport as JSON (default on)")
    args = parser.parse_args(argv)

    dry_run = args.command == "rollback-preview"
    result = rollback(args.thread, args.to_seq, dry_run=dry_run,
                      seed=args.seed)
    json.dump(result, sys.stdout)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
