"""Chaos runner (Task 2.4): N seeded iterations of the TS-01 scenario under
randomly enabled fault hooks, each verified against the exact expected
post-rollback world state, with row-conservation and double-compensation
checks. Writes a per-run JSON report.

CLI:
    python -m undolog_torture.chaos --iterations 100 --seed S --json out.json

Exit code 0 iff every iteration passed the gate.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from typing import Optional

from sqlmodel import Session, select

from undolog_core import Ledger
from undolog_core.models import LedgerEntry

from .agent import run_scenario
from .dbutil import DEFAULT_DATABASE_URL, drop_schema, engine_for_schema, setup_schema
from .executors import build_executors
from .faults import FaultConfig
from .registry_ext import load_torture_registry
from .snapshot import canonical_state, hash_world
from .verify import expected_post_rollback


@dataclass
class ChaosIteration:
    iteration: int
    seed: int
    thread_id: str
    faults: dict
    passed: bool
    problems: list[str] = field(default_factory=list)
    world_hash: str = ""
    expected_hash: str = ""
    rows_conserved: bool = False
    refunds: int = 0
    duration: float = 0.0


@dataclass
class ChaosReport:
    iterations: list[ChaosIteration]

    @property
    def all_passed(self) -> bool:
        return all(it.passed for it in self.iterations)


def run_one_iteration(engine, iteration: int, seed: int,
                      base_backoff: float = 0.01) -> ChaosIteration:
    thread_id = f"chaos-{iteration}-{seed}"
    faults = FaultConfig.for_seed(seed)
    t0 = time.perf_counter()
    result = run_scenario(engine, seed=seed, thread_id=thread_id,
                          faults=faults)
    ledger = Ledger(engine, registry=load_torture_registry())
    executors = build_executors(result.world, faults=faults)
    report = ledger.rollback(thread_id, 33, executors, max_attempts=4,
                             base_backoff=base_backoff)
    duration = time.perf_counter() - t0

    problems: list[str] = []

    # Lost-row check: re-read the ledger from the DB.
    with Session(engine) as session:
        db_rows = session.exec(
            select(LedgerEntry)
            .where(LedgerEntry.thread_id == thread_id)
            .order_by(LedgerEntry.seq)
        ).all()
    seqs = [r.seq for r in db_rows]
    rows_conserved = seqs == list(range(1, 51))
    if not rows_conserved:
        problems.append(f"ledger rows not conserved: {seqs[:8]}... "
                        f"({len(db_rows)} rows)")

    # Rollback must complete (comp_fail_n <= 2 < max_attempts).
    if not report.complete:
        problems.append(f"rollback incomplete: failed={report.failed}")

    # World state must equal the exact expected post-rollback state.
    actual = canonical_state(result.world)
    expected = expected_post_rollback(result, report)
    world_hash, expected_hash = hash_world(actual), hash_world(expected)
    if world_hash != expected_hash:
        problems.append("world state does not match expected post-rollback "
                        "state")

    # Double-compensation check: a fresh idempotent re-rollback must not
    # refund or restore anything more, and the world must not change.
    ledger.rollback(thread_id, 33, build_executors(result.world),
                    max_attempts=4, base_backoff=base_backoff)
    if hash_world(canonical_state(result.world)) != world_hash:
        problems.append("re-rollback changed the world (double compensation)")

    return ChaosIteration(
        iteration=iteration, seed=seed, thread_id=thread_id,
        faults={k: v for k, v in vars(faults).items()},
        passed=not problems, problems=problems,
        world_hash=world_hash, expected_hash=expected_hash,
        rows_conserved=rows_conserved,
        refunds=len(result.world.stripe.refunds), duration=duration,
    )


def run_chaos(engine, iterations: int = 100, seed: int = 0,
              json_path: Optional[str] = None,
              base_backoff: float = 0.01) -> ChaosReport:
    report = ChaosReport(iterations=[
        run_one_iteration(engine, i, seed + i, base_backoff=base_backoff)
        for i in range(iterations)
    ])
    if json_path:
        with open(json_path, "w", encoding="utf-8") as fh:
            json.dump({
                "iterations": iterations,
                "seed": seed,
                "all_passed": report.all_passed,
                "runs": [asdict(it) for it in report.iterations],
            }, fh, indent=2)
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m undolog_torture.chaos",
        description="Seeded chaos runner over the TS-01 corruption scenario.")
    parser.add_argument("--iterations", type=int, default=100)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--json", dest="json_path", default=None,
                        help="write the per-run JSON report here")
    parser.add_argument("--schema", default=None,
                        help="existing migrated schema to run against "
                             "(default: create and drop a fresh one)")
    args = parser.parse_args(argv)

    url = os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)
    schema = args.schema or f"undolog_chaos_{os.getpid()}"
    if args.schema is None:
        setup_schema(url, schema)
    engine = engine_for_schema(url, schema)
    try:
        report = run_chaos(engine, iterations=args.iterations,
                           seed=args.seed, json_path=args.json_path)
    finally:
        engine.dispose()
        if args.schema is None:
            drop_schema(url, schema)

    failed = [it for it in report.iterations if not it.passed]
    print(f"chaos: {len(report.iterations) - len(failed)}/"
          f"{len(report.iterations)} iterations passed "
          f"(seed base {args.seed})")
    for it in failed[:10]:
        print(f"  FAILED iter {it.iteration} seed {it.seed}: {it.problems}")
    return 0 if report.all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
