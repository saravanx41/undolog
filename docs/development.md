# Development

## Setup

```sh
uv venv && source .venv/bin/activate
uv pip install -e .                 # meta-package with the `undolog` CLI
uv pip install -e packages/core -e packages/adapters \
               -e packages/langgraph -e packages/torture
docker compose up -d                # Postgres 16 for tests and demos
```

## Test

```sh
pytest                              # TS-01..TS-08 + unit tests
```

Current status: 123 passed, 1 skipped — the real-Stripe test (TS-04) skips
until `STRIPE_SECRET_KEY` is set (issue #1).

## Chaos gate

```sh
undolog chaos --iterations 100 --seed N     # N = 1..5
UNDOLOG_TORTURE_RUNS=100 pytest             # same gate inside pytest
```

500/500 seeded iterations across 5 seeds pass before release.

## Migrations

Alembic lives in `packages/core/alembic` (`alembic.ini` next to it). The
initial migration creates the ledger schema and revokes UPDATE/DELETE/
TRUNCATE from the application role — append-only is enforced by Postgres,
not by convention.

## Repo layout

```
undolog/cli.py        # the `undolog` console command (this meta-package)
packages/core         # ledger, taxonomy, freeze/rollback — zero framework deps
packages/adapters     # Stripe, Postgres, generic REST + registry/tools/*.yaml
packages/langgraph    # LangGraph plugin (wraps core)
packages/torture      # TS-01..TS-08 scenarios + chaos runner + demo
packages/web          # Next.js timeline UI
docs/                 # user-facing docs (this directory)
launch/               # HN/PH drafts, video script, go-live checklist
```

## The iron-clad rule

`packages/core` has zero framework dependencies — no LangGraph, no FastAPI,
nothing beyond Python + SQL. Everything else (langgraph adapter, web UI,
torture suite) is a consumer. If a change to core needs a framework import,
the change is wrong. This is what keeps the engine testable without the
frameworks your agent happens to run on.
