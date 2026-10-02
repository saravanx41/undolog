# Show HN: Undolog – undo what your AI agent did to the world

AI agents now send emails, charge cards, and overwrite CRMs. When they go
wrong — and 74% of enterprises have rolled back an agent after going live —
your tools are: database backups (restore 6am, lose all good work since),
agent checkpoints (resume the agent so it can continue the damage), and
observability dashboards (watch the fire).

Undolog is a Python SDK that wraps your agent's tool calls and keeps an
append-only ledger: before-value, after-value, why, and a registered undo
for every external side effect. Then
`ledger.rollback(thread_id, to=checkpoint)` rewinds the world.

The honest part — every tool is one of three classes:

- **Reversible** (CRM records, DB rows): truly rolled back via captured
  before-values
- **Compensatable** (charges): compensated (refund) — net effect zero, not
  erased
- **Irreversible** (sent emails): nothing can un-send these, including us —
  so we contain them (freeze at email #12, not #400) and report exactly
  what happened, with the agent's reasoning, for audit

What's proven: TS-01–TS-08 torture suite, 500/500 seeded chaos iterations
across 5 seeds, zero lost ledger rows, zero double compensations —
including SIGKILL mid-rollback, agents racing the rollback, and
replay-dedup over real HTTP with the server process killed mid-test. Our
own harness caught a real idempotency bug before any user did. (121 tests
passing; one real-Stripe sandbox test pending a key — tracked in issue #1.)

`pip install undolog` → `undolog demo` runs a full corruption + rewind
scenario. Postgres is the only dependency.

Framework adapter for LangGraph today; core has zero framework
dependencies. The Tool Safety Registry (tool → class + tested compensation
recipe) is public — PRs for the next tools are the whole point.

I'd love feedback on two things: (1) which tools should the registry cover
next, and (2) for those running agents in prod — what's your actual
recovery story today?
