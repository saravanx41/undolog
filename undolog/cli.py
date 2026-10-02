"""Console entry point for the undolog meta-package.

Thin wrappers over the torture package's runners so the whole system is
one command after `pip install undolog`.
"""
from __future__ import annotations

import argparse
import sys


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="undolog",
        description="Append-only ledger and rollback engine for agent "
                    "side effects.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser(
        "demo",
        help="run the narrated corruption + freeze + rollback scenario "
             "(creates its own fresh 'undolog_demo' schema)",
        parents=[_demo_parent()],
        add_help=False,
    )

    sub.add_parser(
        "chaos",
        help="run the seeded chaos gate over the TS-01 scenario",
        parents=[_chaos_parent()],
        add_help=False,
    )

    args = parser.parse_args(argv)
    if args.command == "demo":
        return _run_demo(args)
    return _run_chaos(args)


def _demo_parent() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--fast", action="store_true",
                   help="no narration sleeps (used by tests)")
    p.add_argument("--seed", type=int, default=20260)
    p.add_argument("--schema", default="undolog_demo",
                   help="schema to create fresh and run against")
    p.add_argument("--keep-schema", action="store_true",
                   help="do not drop the schema afterwards")
    return p


def _chaos_parent() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--iterations", type=int, default=100)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--json", dest="json_path", default=None,
                   help="write the per-run JSON report here")
    p.add_argument("--schema", default=None,
                   help="existing migrated schema to run against "
                        "(default: create and drop a fresh one)")
    return p


def _run_demo(args: argparse.Namespace) -> int:
    from undolog_torture.demo import main as demo_main

    argv = ["--seed", str(args.seed), "--schema", args.schema]
    if args.fast:
        argv.append("--fast")
    if args.keep_schema:
        argv.append("--keep-schema")
    return demo_main(argv)


def _run_chaos(args: argparse.Namespace) -> int:
    from undolog_torture.chaos import main as chaos_main

    argv = ["--iterations", str(args.iterations),
            "--seed", str(args.seed)]
    if args.json_path:
        argv += ["--json", args.json_path]
    if args.schema:
        argv += ["--schema", args.schema]
    return chaos_main(argv)


if __name__ == "__main__":
    sys.exit(main())
