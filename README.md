# undolog

An append-only ledger, a 3-class taxonomy, and a freeze/rollback engine for
agent side effects. When an agent goes rogue — corrupting your CRM,
double-charging cards, spamming customers — undolog freezes the thread,
rolls back everything undoable, and hands you an honest blast-radius report
of what it couldn't undo.

## Quickstart

```sh
pip install undolog            # or: uv pip install -e .
docker compose up -d           # Postgres 16
undolog demo                   # watch the corruption + rollback scenario
undolog chaos --iterations 100 # the torture gate
pytest                         # TS-01..TS-08
```

## What it is

Every tool call an agent makes is wrapped and recorded as an append-only
ledger row: before-state capture, after-proof, tool name, idempotency key.
Each tool is classified into a 3-class taxonomy — **reversible** (restore
the captured before-state), **compensatable** (execute an inverse action,
e.g. a Stripe refund, with idempotency keys so it applies exactly once),
or **irreversible** (a sent email — no compensation exists). A freeze
stops a thread instantly: further tool calls die with `FrozenError`, zero
side effects, zero ledger rows. Rollback is two-phase — a dry-run preview,
then execution with retries — and ends with a blast-radius report listing
every irreversible effect that must be mitigated out of band.

## Architecture rule

`undolog-core` has **zero framework dependencies** — no LangGraph, no
FastAPI, nothing. The ledger is plain Python + SQL. LangGraph support
(`packages/langgraph`) is a plugin that wraps the same core; any other
framework gets the same treatment. The engine is testable without the
frameworks your agent happens to run on.

## Tool Safety Registry

`undolog_core/registry/tools.yaml` is the seed of the **Tool Safety
Registry**: a community-maintained YAML of per-tool safety classes and
tested compensation recipes (restore-before, refund, delete-message, …).
A tool graduates from *unknown* to *reversible* or *compensatable* only
with evidence — a compensation recipe that passes torture tests. The
registry is the real moat: anyone can write a ledger; knowing exactly how
to safely undo `salesforce.update_record` is hard-won, tested knowledge.
See `launch/tool-safety-registry/` for the public-repo plan.

## Torture tests

The correctness spec is `packages/torture/SPEC.md`: TS-01..TS-07 must each
pass 100 consecutive seeded chaos runs (plus TS-08 for log-only writes) —
zero lost ledger rows, zero double-applied compensations, any failure =
fix before release. `undolog chaos` is that gate as a single command.

One caveat: **TS-04** (duplicate side effects) runs against real Stripe
test mode and needs `STRIPE_SECRET_KEY` set; the rest run fully offline
against Postgres.

## Layout

```
undolog/cli.py        # the `undolog` console command (this meta-package)
packages/core         # ledger, taxonomy, freeze/rollback — zero framework deps
packages/adapters     # Stripe, CRM, email adapters
packages/torture      # TS-01..TS-08 scenarios + chaos runner + demo
packages/langgraph    # LangGraph plugin (wraps core)
packages/web          # web UI
launch/               # HN/PH drafts, video script, go-live checklist
```
