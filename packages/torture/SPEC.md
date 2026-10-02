# Torture-Test Spec (Week 2 deliverable)

Pass gate: TS-01 through TS-07 each pass 100 consecutive seeded runs with
random fault injection enabled. Zero lost ledger rows. Zero double-applied
compensations. Any failure = fix before Week 3.

| ID | Scenario | Procedure | Pass criteria |
|----|----------|-----------|---------------|
| TS-01 | Happy-path rollback | Run 50-action corruption scenario; rollback(to_seq=33) | CRM identical to pre-#34 snapshot; exactly 3 refunds issued; 30 emails listed as irreversible in report; world state verifiably equals the #33 snapshot |
| TS-02 | Mid-rollback compensation failure | Inject: refund fails 2× with 500, then succeeds | Engine retries with backoff; ledger marks entries failed→compensated with retry audit trail; if it exhausts retries it halts, unfreezes, and reports exact partial state — never claims success |
| TS-03 | Racing agent | Spawn thread that keeps firing tool calls while rollback runs | Every post-freeze side effect rejected; zero external effects after freeze point; agent receives FrozenError |
| TS-04 | Duplicate side effects | Replay the same tool call with the same idempotency key 5× (real Stripe test mode for Task 3.2) | Exactly 1 external effect; ledger has exactly 1 row; compensation later refunds exactly once |
| TS-05 | Blast-radius control | Runaway email loop, 400 sends; freeze triggered at send #12 | Exactly 12 sent, 388 prevented; report enumerates all 12 with recipients, content hash, and "why"; time-to-freeze < 2s |
| TS-06 | Process death mid-tool | SIGKILL during fn execution and during compensation execution | On restart: ledger shows failed/incomplete, no phantom applied rows; resume completes or cleanly reports what a human must do |
| TS-07 | Concurrent threads | 3 threads × interleaved side effects on shared mock CRM; rollback thread B only | B fully rolled back; A and C untouched; per-thread seq stays gapless |
| TS-08 | Before-capture failure | capture_before times out on 20% of calls | Writes proceed log-only, class=unknown, flagged in report; rollback never silently "restores" a value it didn't capture |

Chaos runner: seeded RNG picks fault hooks per run (all TS-02/03/04/06/08
injectors, random on/off), 100 iterations, per-scenario JSON report +
world-state hash comparison after each run.
