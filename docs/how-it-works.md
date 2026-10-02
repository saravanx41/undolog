# How it works

## The ledger

Every tool call becomes one row in `ledger_entries`, append-only in the
strong sense: the migration revokes `UPDATE`, `DELETE`, and `TRUNCATE` on
the table from the application role, so "undo" can never be faked by
mutating history. Rows carry: `thread_id`, a gapless per-thread `seq`
(backed by a `thread_counters` row), `tool_name`, an `idempotency_key`
(unique), `before_jsonb` / `after_jsonb` proofs, the taxonomy `class`,
`status` (applied / compensated / failed), and a `log_only` flag for rows
whose proof capture failed. A frozen thread sets a `thread_freezes` row;
post-freeze tool calls die with `FrozenError` before any side effect or
ledger write.

## The wrap flow

`ledger.for_thread(tid).wrap(tool_name, adapter=...)` returns a decorator.
Inside the wrapper: check the freeze flag, call the tool, capture
before/after proofs via the adapter, then write the row. If proof capture
fails (TS-08), the row is written log-only with class escalated to
`unknown` — the engine will never "restore" a before-state it didn't
capture.

## The taxonomy

- **reversible** — a before-state snapshot exists; undo = restore it.
- **compensatable** — no snapshot, but an inverse action exists (refund,
  delete-message); executed with a deterministic compensation key so it
  applies exactly once, even across retries and replays.
- **irreversible** — nothing can undo it (a sent email); it is refused by
  rollback and reported in the blast radius.

Graduation is evidence-based: a tool starts `unknown` and moves to
reversible/compensatable only when its compensation recipe passes the
torture suite. No recipe, no graduation — and the report says so.

## Freeze and rollback

`ledger.freeze(thread_id)` halts a thread instantly (all further calls
raise `FrozenError`). `ledger.rollback(thread_id, to_seq, executors,
dry_run=...)` walks applied entries newest-first, in seq order:

1. **Dry-run first** — the same walk with no side effects, producing the
   report the caller sees before confirming.
2. **Idempotent compensations** — compensation keys are deterministic
   (`comp-{entry.id}`); a re-run skips already-compensated entries, so
   retrying a rollback is a no-op.
3. **Halt on exhaustion** — a compensation that keeps failing retries with
   backoff, marks the entry `failed`, halts the walk with
   `complete=False`, and releases the freeze. The report states the exact
   partial state — it never claims success it didn't achieve.
4. **Blast radius** — irreversible entries are never executed; they appear
   in the report with per-entry reasons.

The full torture-test spec (TS-01..TS-08, pass criteria, fault injectors)
is the single source of truth at `packages/torture/SPEC.md` — read it
there, not here: [torture-spec.md](torture-spec.md).
