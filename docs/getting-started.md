# Getting started

Three commands from a clone:

```sh
uv pip install -e .               # or pip install undolog once PyPI lands
docker compose up -d              # Postgres 16
undolog demo                      # rogue agent -> freeze -> rollback
```

(Once the PyPI upload lands, the first command is just `pip install undolog`.)

## What you should see

`undolog demo` runs a 50-action scenario: a CRM agent goes rogue. Six rogue
actions land before the watchdog freezes the thread; the freeze rejects a
further six attempted calls with `FrozenError`. The engine then dry-runs and
executes a rollback, ending with a summary like:

```
actions frozen:    6 rogue calls rejected (freeze at #18)
restored:          3 entries (CRM records, rogue note)
refunded:          1 fraudulent charge(s) (stripe compensations)
irreversible:      2 irreversible email(s) — see BLAST RADIUS
```

The blast-radius section lists each irreversible email with recipient,
subject, content hash, and why it cannot be undone. A world-state hash
before corruption, after corruption, and after rollback closes the run —
the post-rollback hash differs from the pre-corruption hash only through
the irreversible residue.

`undolog demo --no-rollback` stops after the corruption phase (seq 13-18
stay `applied`), which is handy for inspecting a live thread in the web UI
and rolling it back yourself.

## The web UI

```sh
cd packages/web && npm install && npm run dev   # http://localhost:3000
```

- **Threads page** — every thread in the ledger with entry count, last
  activity, and freeze status.
- **Thread timeline** — every ledger row in order: seq, tool, class
  (reversible / compensatable / irreversible / unknown), status, and the
  captured before/after JSON.
- **Rollback panel** — picks a target seq, shows the real dry-run report
  computed by the engine (restored / compensated / refused), then confirms
  and executes the rollback.

Next: [how-it-works.md](how-it-works.md).
