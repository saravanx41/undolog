# Torture spec

The correctness contract is TS-01..TS-08: happy-path rollback, mid-rollback
compensation failure, racing agent, duplicate side effects, blast-radius
control, process death mid-tool, concurrent threads, and before-capture
failure. Each must pass 100 consecutive seeded chaos runs with fault
injection — zero lost ledger rows, zero double-applied compensations — and
any failure means fix before release. The single source of truth is
`packages/torture/SPEC.md` in this repo; read it there.
