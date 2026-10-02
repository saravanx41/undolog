# Tool Safety Registry — seed

This directory is the seed of a standalone public repo:
**github.com/undolog/tool-safety-registry** (not yet published — see
`launch/checklist.md`).

The goal: a community-maintained registry of every tool an AI agent might
call, each with a **tested** safety classification and compensation recipe.
Undoing agent side effects is easy to claim and hard to actually do — a
Stripe refund has edge cases (partial refunds, disputes, webhooks already
fired), a Salesforce restore has fifty more. This registry exists so nobody
has to relearn them in production.

## The taxonomy

Every tool gets exactly one class:

- **reversible** — before-state is fully captured; rollback restores it
  (e.g. `notion.update_page`, `filesystem.write_file`, `database.execute`).
- **compensatable** — no before-state, but an inverse action exists and is
  itself idempotent (e.g. `stripe.create_charge` → refund keyed by charge id,
  `slack.post_message` → delete by message id).
- **irreversible** — no safe inverse exists (e.g. `gmail.send`,
  `http.webhook_post`). These can never be "undone", only disclosed in a
  blast-radius report.

## How a tool graduates

```
unknown → reversible / compensatable → irreversible (with evidence)
```

A tool enters the registry as **unknown**: callable, logged, but rollback
refuses to touch it. It graduates to reversible or compensatable only when
a compensation recipe lands with:

1. a concrete implementation (structured recipe in `tools.yaml`), and
2. a torture test proving it — idempotent replays, mid-compensation
   failures, exactly-once semantics — like the undolog TS-01..TS-08 suite
   (`packages/torture/SPEC.md`).

A tool is demoted to **irreversible** the moment evidence shows the
compensation is unsafe (double effects, destructive inverses). This is
deliberately conservative: a wrong "reversible" burns users; a wrong
"irreversible" only costs them a mitigation email.

`tools.yaml` in this directory is a verbatim copy of
`packages/core/src/undolog_core/registry/tools.yaml` — the seed shipped in
the undolog core package. As the public repo grows ahead of release
cadence, core will vendor snapshots from it.
