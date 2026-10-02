# undolog — PLAN

Append-only ledger, three-class taxonomy, and rollback engine for agent side
effects. The only thing that matters in 4 weeks is TS-01 through TS-07 passing
100/100 — that's the product, the demo video, and the moat's first brick.

## Stack decisions (locked)

- Python 3.12, SQLModel + Alembic, pytest + testcontainers
- Next.js 15 for web
- Postgres 16 (only service in docker-compose.yml)

## Core rule

`packages/core` has **zero framework dependencies** — LangGraph is a plugin,
not a foundation. This is insurance against LangChain shipping native undo.

## Working rules

- Tests before implementation — every task starts with the failing test that
  proves the acceptance criteria.
- One task per session; paste the acceptance criteria block verbatim.

## Layout

```
undolog/
├── packages/
│   ├── core/          # ledger schema, interceptor, taxonomy, rollback engine
│   ├── adapters/      # postgres, stripe, hubspot, gmail, calendar
│   ├── langgraph/     # framework adapter only — core must never import it
│   ├── web/           # Next.js timeline UI
│   └── torture/       # mock agent, mock APIs, fault injection, chaos runner
├── docker-compose.yml # Postgres 16 only
└── PLAN.md
```

## Week 1 — Ledger Core + Mock World

- **Task 1.1 — Ledger schema + migrations.** Append-only `ledger_entries`:
  id (uuid), ts, thread_id, seq (per-thread monotonic), tool_name, args_hash,
  idempotency_key (unique), before_jsonb, after_jsonb, class
  (reversible|compensatable|irreversible), compensation_ref, status
  (applied|compensated|failed), prompt_context_ref. Plus compensations
  registry table and tool_registry (taxonomy).
  Accept: migration runs clean; insert is append-only (no UPDATE/DELETE
  grants); seq gapless per thread under 100 concurrent inserts.
- **Task 1.2 — The `ledger.wrap` interceptor.** Signature:
  `wrap(fn, compensate=None, adapter=None)`. Flow: generate idempotency key →
  adapter.capture_before() → execute → adapter.capture_after() → write row. On
  exception: write row with status=failed, re-raise. On adapter failure:
  proceed in log-only mode, class escalated to unknown.
  Accept: wrapping a plain function records before/after; killing the process
  mid-fn never produces a row claiming applied without proof.
- **Task 1.3 — Three-class taxonomy + Tool Safety Registry.** YAML registry
  (the future moat): per tool → class, tested compensation recipe, snapshot
  capability. Seed with 10 tools. wrap() validates against it; unknown tool =
  unknown class + warning.
  Accept: registering gmail.send as irreversible makes rollback refuse to
  "undo" it and instead emit a blast-radius report.
- **Task 1.4 — Mock world.** In-process fake servers (FastAPI): mock Gmail
  (send log), mock Stripe (charges/refunds with idempotency-key dedup), mock
  CRM (records with GET/PUT). A scripted "runaway agent" that executes 50
  actions including a corruption event at #34 (overwrites 20 CRM records,
  double-charges 3 cards, sends 30 emails).
  Accept: the mock world runs the corruption scenario deterministically,
  seeded, in under 5 seconds.

## Week 2 — Rollback Engine + Torture Harness

- **Task 2.1 — Freeze.** `ledger.freeze(thread_id)`: Postgres advisory lock on
  the thread + a flag the interceptor checks (reject new side effects with
  FrozenError). LangGraph interrupt comes in Week 3; core freeze is lock-only.
  Accept: wrapped calls during freeze are rejected, nothing external happens.
- **Task 2.2 — Rollback engine.** `ledger.rollback(thread_id, to_seq=N)`:
  freeze → walk entries backwards from latest to N → per class: reversible →
  restore before-value via adapter; compensatable → execute registered
  compensation with idempotency key; irreversible/unknown → add to report,
  never touch → unfreeze. Two-phase: dry-run preview then execute.
  Accept: after rolling the mock world back to N=33, CRM shows pre-corruption
  values, the 3 extra charges are refunded, and the report lists exactly 30
  emails as irreversible.
- **Task 2.3 — Fault injection framework.** Deterministic hooks:
  compensation fails (API 500, N times), process dies mid-rollback, adapter
  times out on capture_before, duplicate tool call replay.
  Accept: each hook is triggerable by env var/seed in a chaos run.
- **Task 2.4 — Torture-test suite.** Spec in `packages/torture/SPEC.md`.
  Accept: the full suite is the definition of "Week 2 done."

## Week 3 — Real Adapters + LangGraph Integration

- **Task 3.1 — LangGraph adapter.** Map LangGraph thread_id ↔ ledger thread;
  PostgresSaver as checkpointer; on rollback, interrupt() the graph before
  compensating, fork from clean checkpoint after, Command(resume=...).
  Accept: a LangGraph ReAct agent that goes rogue mid-run gets frozen, rolled
  back, and forked from the clean checkpoint — no hand-written graph changes
  beyond wrapping tools.
- **Task 3.2 — Stripe adapter (real API, test mode).** before-value = balance
  transaction read; compensation = Refund.create with idempotency key.
  Accept: TS-04 (dedup) passes against real Stripe test mode.
- **Task 3.3 — Postgres adapter.** before-value capture via
  SELECT ... FOR UPDATE snapshot in-transaction with the write.
  Accept: reversible restore exact under concurrent writers.
- **Task 3.4 — PocketOS demo scenario.** Script: agent with 6 tools,
  corruption at minute 2, alert → freeze at action #12 → rollback → report.
  This is the launch video.
  Accept: runs end-to-end from docker compose up with one command.

## Week 4 — Timeline UI + Packaging

- **Task 4.1 — Web timeline.** Thread list → action rows (class color-coded,
  before/after JSON diff, "why" expandable) → drag-to-checkpoint rollback
  preview (dry-run list) → confirm → live progress.
- **Task 4.2 — Packaging.** `pip install undolog`, README with the 5-line
  quickstart, docker-compose, `undolog demo` one-command corruption scenario.
- **Task 4.3 — Launch assets.** 90-second video from Task 3.4, "Tool Safety
  Registry" public repo (moat seed + SEO), Product Hunt / HN post drafted
  around the 74%-rollback stat.
