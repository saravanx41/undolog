# undolog

An append-only ledger, a 3-class taxonomy, and a freeze/rollback engine for
AI-agent side effects. When an agent goes rogue — corrupting your CRM,
double-charging cards, spamming customers — undolog freezes the thread,
rolls back everything undoable, and hands you an honest blast-radius report
of what it couldn't undo.

## Run it in 3 commands

```sh
pip install undolog          # until the PyPI upload lands: uv pip install -e .
docker compose up -d         # Postgres 16
undolog demo                 # rogue agent -> freeze -> rollback -> blast radius
```

Then open the UI:

```sh
cd packages/web && npm install && npm run dev   # http://localhost:3000
```

<!-- The 90-second demo video lands here as docs/demo.gif -->
![demo](docs/demo.gif)

## Architecture

```
agent ──► wrap() interceptor ──► append-only ledger (Postgres)
                                      │
        freeze / rollback engine ◄────┤   ← REVOKE-enforced, gapless seq
                                      ▼
              adapters (Stripe, Postgres, REST)
                        ▲
web timeline reads the ledger; langgraph is a plugin on the same core
```

`undolog-core` has zero framework dependencies — no LangGraph, no FastAPI.
Everything else (langgraph adapter, web UI, torture suite) builds on it.

## How integrations work

A tool is described by one YAML file; everything else is generated:

```yaml
# registry/tools/github.create_issue.yaml — filename stem is the tool name
class: compensatable          # creates have no before-value; the inverse exists
adapter:
  type: rest
  base_url: https://api.github.com
  auth: {type: bearer, env: GITHUB_TOKEN}
  capture_before: null
  compensate:
    method: PATCH
    path: /repos/{args.repo}/issues/{result.number}
    body: {state: closed}
    idempotent: true          # engine sends comp-{entry.id} as Idempotency-Key
```

```python
from undolog_adapters import register_tools
ledger = Ledger(engine)
for tool in ["github.create_issue", "s3.put_object", "gcal.events_insert",
             "hubspot.update_contact", "slack.chat_postMessage"]:
    register_tools(ledger, tool, env=os.environ)
```

The class comes from the registry, capture/compensate come from the YAML,
and rollback uses both. Ships with 5 example tool specs; the full YAML
reference is [docs/integrations.md](docs/integrations.md). Community
registry: [github.com/saravanx41/tool-safety-registry](https://github.com/saravanx41/tool-safety-registry).

## The taxonomy

- **reversible** — restore the captured before-state (update a record back).
- **compensatable** — execute an inverse action with a deterministic
  idempotency key (a Stripe refund), applied exactly once.
- **irreversible** — no compensation exists (a sent email). undolog never
  pretends otherwise: these land in the blast-radius report and are never
  "restored".

Tools without a tested recipe are recorded as **unknown** and graduate only
with evidence — a compensation recipe that passes torture tests.

## Torture

```sh
undolog chaos --iterations 100 --seed N
```

The chaos gate replays TS-01..TS-08 with seeded fault injection; 500/500
seeded iterations across 5 seeds pass. Spec: [docs/torture-spec.md](docs/torture-spec.md).

## Links

- Full docs: [docs/getting-started.md](docs/getting-started.md)
- Tool Safety Registry: https://github.com/saravanx41/tool-safety-registry
- Issue [#1](https://github.com/saravanx41/undolog/issues/1): the real-Stripe
  test (TS-04) skips until `STRIPE_SECRET_KEY` is set
- License: MIT
