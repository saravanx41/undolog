"""TS-06 child process: runs real wrapped calls against a Postgres schema,
announces progress on stdout, then sleeps at a deterministic kill point
until the parent SIGKILLs it.

Modes:
  mid_tool          — killed inside a wrapped tool fn (after 2 completed
                      charges; the 3rd call sleeps before its side effect).
  mid_compensation  — killed inside a rollback compensation (3 charges,
                      then a rollback whose first refund sleeps).

Run: python -m undolog_torture.sigchild --mode MODE --schema SCHEMA
Env: DATABASE_URL, UNDOLOG_KILL_SLEEP_SEC (sleep window at the kill point),
     UNDOLOG_KILL_POINT (echoed from FaultConfig for bookkeeping).
"""
from __future__ import annotations

import argparse
import os
import time

SEED = 1337
PROGRESS_PREFIX = "UNDULOG-CHILD"


def say(msg: str) -> None:
    print(f"{PROGRESS_PREFIX}:{msg}", flush=True)


def main(argv=None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", required=True,
                        choices=["mid_tool", "mid_compensation"])
    parser.add_argument("--schema", required=True)
    parser.add_argument("--thread", default="ts06-child")
    args = parser.parse_args(argv)

    sleep_sec = float(os.environ.get("UNDOLOG_KILL_SLEEP_SEC", "5"))
    url = os.environ.get(
        "DATABASE_URL",
        "postgresql+psycopg://undolog:undolog@localhost:5432/undolog")

    from undolog_core import Ledger
    from undolog_torture.agent import StripeChargeAdapter
    from undolog_torture.dbutil import engine_for_schema
    from undolog_torture.executors import StripeExecutor
    from undolog_torture.registry_ext import load_torture_registry
    from undolog_torture.world import build_worlds

    engine = engine_for_schema(url, args.schema)
    world = build_worlds(SEED)
    ledger = Ledger(engine, registry=load_torture_registry())
    tl = ledger.for_thread(args.thread)

    @tl.wrap(adapter=StripeChargeAdapter(world), tool_name="stripe.create_charge")
    def charge(amount, card_token, req_id, idempotency_key=None):
        if args.mode == "mid_tool" and req_id == "r3":
            say("IN_TOOL")            # parent kills us during this sleep
            time.sleep(sleep_sec)     # die before the side effect happens
        return world.stripe.create_charge(amount, card_token, idempotency_key)

    if args.mode == "mid_tool":
        for i, (amt, card) in enumerate([(1000, "c1"), (2000, "c2")], start=1):
            charge(amt, card, req_id=f"r{i}", idempotency_key=f"k{i}")
            say(f"CHARGED:{i}")
        charge(3000, "c3", req_id="r3", idempotency_key="k3")
        say("DONE")                   # unreachable when the harness works
    else:
        for i, (amt, card) in enumerate([(1000, "c1"), (2000, "c2"),
                                         (3000, "c3")], start=1):
            charge(amt, card, req_id=f"r{i}", idempotency_key=f"k{i}")
            say(f"CHARGED:{i}")

        class SlowStripe(StripeExecutor):
            def compensate(self, entry, idempotency_key):
                say("IN_COMPENSATION")   # parent kills us during this sleep
                time.sleep(sleep_sec)
                super().compensate(entry, idempotency_key)

        say("READY")
        ledger.rollback(args.thread, 0,
                        {"stripe.create_charge": SlowStripe(world.stripe)})
        say("ROLLBACK_DONE")          # unreachable when the harness works


if __name__ == "__main__":
    main()
