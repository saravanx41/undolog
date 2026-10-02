# Go-live checklist

Ordered. Do not skip steps 1–2: everything downstream quotes them.

## Pre-flight (code & gates)

- [ ] `pytest` green: TS-01..TS-08, full suite (`tests` + `packages`) — ~100s
- [ ] `undolog chaos --iterations 100` green (the torture gate; keep the
      JSON report as the release artifact)
- [ ] TS-04 against real Stripe test mode: set `STRIPE_SECRET_KEY`, run,
      confirm exactly-once charge + exactly-once refund
- [ ] `uv pip install --python .venv/bin/python -e .` from a clean venv
      smoke-tested; `undolog demo --fast` and `undolog chaos
      --iterations 3 --seed 1 --json out.json` both exit 0
- [ ] Version bump sanity: all four pyprojects (root meta + core,
      adapters, torture) agree on 0.1.0

## Launch assets

- [ ] Record the 90-second demo video per `launch/video-script.md`
      (console lines are verbatim from seed 20260 — re-run
      `undolog demo` and re-verify before recording)
- [ ] Final pass on `launch/HN-post.md` (word count < 400) and
      `launch/PRODUCT_HUNT.md` (tagline ≤ 60 chars — currently 53)
- [ ] GitHub repo description + topics set to match the PH tagline

## Registry (the moat)

- [ ] Publish `launch/tool-safety-registry/` as its own public repo
      (README + `tools.yaml` seed)
- [ ] Vendor note: core keeps shipping its snapshot of `tools.yaml`;
      add CI check that core and registry seed don't silently diverge
- [ ] Seed 5–10 additional community tools (Salesforce, HubSpot,
      Notion create/delete, GitHub) with at least `unknown` entries so
      the graduation workflow is visible

## Ship

- [ ] Publish `undolog-core` / `undolog-adapters` / `undolog-torture`
      to PyPI (or document the monorepo install path if staying
      source-only for v0.1)
- [ ] Post Product Hunt (use maker comment + FAQ from
      `launch/PRODUCT_HUNT.md`)
- [ ] Post Show HN (use `launch/HN-post.md`; be in the thread for the
      first 4 hours)
- [ ] Reply to first comments with the FAQ answers; link the torture
      spec `packages/torture/SPEC.md` when asked "how do you know it
      works"
