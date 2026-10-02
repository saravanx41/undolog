# Integrations

A tool is described by one YAML file. `register_tools(ledger, tool, env=...)`
reads the spec, wires the generic REST adapter around the tool name, and the
ledger records every call with its proofs and class. No per-tool code.

## Reference

One file per tool in `registry/tools/<tool_name>.yaml` — the filename stem
is the tool name:

```yaml
# registry/tools/github.create_issue.yaml
class: compensatable           # reversible | compensatable | irreversible | unknown
compensation: {type: rest, action: compensate}
snapshot_capable: false
adapter:
  type: rest
  base_url: https://api.github.com      # optional base_url_env to override
  auth:                                # one of:
    {type: bearer, env: GITHUB_TOKEN}  #   Authorization: Bearer $ENV
    {type: header, name: X-Api-Key, env: API_KEY}
    {type: oauth, env: GOOGLE_TOKEN}   #   Bearer alias for OAuth tokens
    {type: none}                       #   unauthenticated endpoints
  capture_before: null                 # creates have no before-value
  capture_after: record_result         # keep the tool result as the after-proof
  compensate:
    method: PATCH
    path: /repos/{args.repo}/issues/{result.number}
    body: {state: closed}
    idempotent: true                   # engine sends comp-{entry.id} as
                                       #   Idempotency-Key; 409 tolerated
```

Auth env vars resolve lazily at the first HTTP call; a missing var raises a
clear `RestError` naming the tool and the var.

### Reversible tools

`class: reversible` + `compensation: {type: restore_before}` adds a
`capture_before` block (GET the current state first) and a `restore` block
(put it back):

```yaml
  capture_before:
    method: GET
    path: /crm/v3/objects/contacts/{args.contactId}
    store: properties        # dotted-path selector of the response to keep
    on_missing: null         # key present + null: a 404 stores before = null
  restore:
    method: PATCH
    path: /crm/v3/objects/contacts/{args.contactId}
    body: {properties: before}
```

### Null means delete

When `capture_before.on_missing: null` recorded that the object did not
exist before the call, the tool effectively created it — so restoring
deletes it. Put an `on_null` block on `restore` to say how:

```yaml
  restore:
    method: PUT
    path: /{args.Bucket}/{args.Key}
    body: before               # put the old bytes back
    on_null:                   # ...unless before was null (we created it)
      method: DELETE
      path: /{args.Bucket}/{args.Key}
```

### Templating

Paths and bodies are templates with two namespaces, plus `before`:

- `{args.x}` — the tool call's arguments (e.g. `{args.repo}`).
- `{result.y}` — fields from the tool call's response (e.g. `{result.number}`).
- `before` — the captured before-value (restore bodies; absent when the
  object didn't exist — see `on_null`).

Because the args snapshot is stored in the ledger proofs, rollback templates
render entirely from `before_jsonb`/`after_jsonb` — a replayed rollback is
self-sufficient.

### Irreversible tools

`class: irreversible` with `adapter: null`: ledger + containment only. No
HTTP ever happens for the tool; rollback refuses with a blast-radius entry.
Undo does not exist; honesty is the feature.

### Capture failure

If `capture_before` itself fails (timeout, 500), the call still proceeds but
the ledger row is written log-only with class `unknown` — rollback will
never claim to restore a before-state it never saw.

## Shipped examples

The generic-REST registry ships with five specs
(`packages/adapters/registry/tools/`):

- `github.create_issue` — compensatable; closes the created issue (PATCH
  `state: closed`, idempotent).
- `s3.put_object` — reversible; restores the prior object version, or
  DELETEs the object the call created (`restore.on_null`).
- `gcal.events_insert` — compensatable; deletes the created event.
- `hubspot.update_contact` — reversible; restores captured contact
  properties.
- `slack.chat_postMessage` — irreversible; `adapter: null`, blast-radius
  report only.

The community registry is
[github.com/saravanx41/tool-safety-registry](https://github.com/saravanx41/tool-safety-registry);
a tool graduates from `unknown` only when its recipe passes the torture
suite.
