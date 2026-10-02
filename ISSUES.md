# Issues

Repo-local issue tracking (this repo has no git remote).

## #1 — Full external-API idempotency verification pending sandbox key (Stripe/Razorpay)

**Status:** open
**Raised by:** TS-04-partial (Week 3)

### What is covered

`tests/test_ts04_partial.py` (marker `ts04_partial`) is the interim
TS-04: the mock Stripe server (`undolog_torture/stripe_server.py`, serving
the real `build_stripe_app` FastAPI app under uvicorn) runs in a separate
process on an ephemeral localhost port with its world state persisted to
disk. The test drives the wrapped tool call and the rollback refund over
real HTTP, replays the same idempotency key 5× (exactly 1 external effect,
exactly 1 ledger row), SIGKILLs the server mid-test twice (after the
charge phase and after the refund phase), restarts it from the persisted
state, and asserts charge/refund idempotency holds across the process and
network boundary.

The original in-process TS-04 remains in `tests/test_ts_suite.py` as part
of the suite gate.

### What remains

Run the same scenario against **real Stripe test mode** (or a Razorpay
sandbox) once `STRIPE_SECRET_KEY` is available:

- point the TS-04-partial harness at the live API instead of
  `MockStripeServer` (the `HttpStripe` shim is the seam);
- replay the identical charge call 5× against Stripe test mode and assert
  1 charge / 1 ledger row;
- execute the rollback and assert exactly 1 refund (Stripe idempotency
  keys make this safe to retry);
- keep the process-kill proof by restarting the *test harness* mid-flight,
  not Stripe.

This is the launch checklist item in `launch/checklist.md` ("TS-04
against real Stripe test mode: set `STRIPE_SECRET_KEY` …"). Until the key
exists, `ts04_partial` stands in for it and is expected to run in CI.
