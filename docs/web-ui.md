# Web UI

A Next.js timeline over the append-only ledger. It reads the same Postgres
the engine writes; it never mutates anything the ledger owns.

## Run

```sh
docker compose up -d
undolog demo --no-rollback     # leave a live corrupted thread behind
cd packages/web && npm install && npm run dev
```

Open http://localhost:3000.

## Env vars

- `DATABASE_URL` — Postgres connection string. Defaults to
  `postgresql://undolog:undolog@localhost:5432/undolog` (the docker-compose
  value), so `docker compose up -d` needs no configuration.
- `UNDOLOG_SCHEMA` — schema the UI reads, default `public`. Validated as a
  plain identifier. Set it when the engine runs in a non-default schema
  (the torture bridge sets it the same way).
- `UNDOLOG_PYTHON` — Python executable used to compute dry-run reports,
  default `python3` from `PATH`.

## Feature tour

- **Threads page** — every thread in the ledger: entry count, last
  activity, freeze status.
- **Thread timeline** (`/threads/[threadId]`) — every row in seq order:
  tool name, class chip (reversible / compensatable / irreversible /
  unknown), status, and the captured before/after JSON per entry.
- **Rollback panel** — pick a target seq; the panel asks the real engine
  for a dry-run report (restored / compensated / refused lists), then
  confirms and executes. The refusal list is the blast radius, shown
  before you commit.

## The --no-rollback trick

`undolog demo` ends with a completed rollback, which leaves a tidy ledger.
Run `undolog demo --no-rollback` first: the rogue seq 13-18 stay
`status=applied`, so the timeline shows the corruption as it happened and
the rollback panel has something real to undo. Do the rollback in the UI
and watch the entries flip to `compensated` in the timeline.
