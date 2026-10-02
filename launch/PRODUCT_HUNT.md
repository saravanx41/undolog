# Product Hunt listing — undolog

**Tagline (53 chars):**
Undo a rogue agent's damage: freeze, rollback, report

## Description

AI agents can update your CRM, charge cards, and email customers — and
when one goes rogue at 3 a.m., you find out from the chargebacks. undolog
is an append-only ledger for agent tool calls with a freeze/rollback
engine: freeze the thread mid-run, undo everything that's undoable, and
get an honest report of what wasn't.

## Key features

- **3-class taxonomy** — every tool call is reversible (restore the
  captured before-state), compensatable (tested inverse, e.g. Stripe
  refund), or irreversible (a sent email — no pretending otherwise).
- **Freeze** — one call stops a thread instantly; further tool attempts
  die with `FrozenError`. Zero side effects after the freeze point, even
  while a rollback is running.
- **Two-phase rollback** — dry-run preview first, then execution with
  retries and audit trails. Never reports success it didn't achieve.
- **Idempotent compensations** — refunds and deletes keyed by the
  original effect's id, so replayed or retried compensations apply
  exactly once.
- **Torture-tested** — TS-01..TS-07 each pass 100 consecutive seeded
  chaos runs with random fault injection (mid-rollback failures, SIGKILL
  mid-tool, racing agents). Zero lost ledger rows, zero double-applied
  compensations. Reproducible with one command: `undolog chaos`.
- **Blast-radius report** — rollback ends by enumerating every
  irreversible effect with recipients, content hashes, and why — so the
  incident email writes itself.
- **Zero framework lock-in** — core has no framework dependencies;
  LangGraph (and anything else) is a plugin.

## The honest stat

**74% of agent side effects are undoable.***

\* Internal measurement from our PocketOS demo runs: the reversible +
compensatable share of the 18 side effects a six-tool onboarding agent
performs before we freeze it, versus the irreversible share (sent emails)
that land in the blast-radius report. It's one scenario, not a market
study — which is exactly why undolog reports your *actual* blast radius
after every rollback instead of quoting averages.

## Maker comment (seed)

We built this after an agent re-segmented 4,000 CRM records overnight and
our choices were "restore from backup and lose a day" or "fix it by
hand." The uncomfortable truth we designed around: you cannot un-send an
email, and any tool that claims full rollback is lying to you. So undolog
is conservative by design — it undoes what's provably undoable and tells
you, with hashes and recipients, exactly what it couldn't. The part we're
proudest of is the torture suite: chaos-tested fault injection (kill -9
mid-refund, duplicate replays, racing agents) across 100 seeded runs per
scenario, because rollback you can't trust is worse than none.

## First-reply FAQ (seed reply)

**Q: How is this different from just wrapping tools in try/except or
writing inverse functions myself?**
A: Three things you won't rebuild in a weekend: (1) the taxonomy — a tool
only graduates to "reversible/compensatable" with a torture-tested
recipe, otherwise rollback refuses to touch it; (2) exactly-once
compensations — retries and replays can't double-refund because
everything is idempotency-keyed; (3) blast radius — you get the honest
list of irreversible effects instead of silently assuming rollback was
complete. Also: freeze works on a running thread, not just at call time.
