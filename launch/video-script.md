# Video script — undolog demo (90 seconds, Task 3.4)

Format: single terminal capture of `undolog demo` (Postgres 16 running in
docker compose). All console lines below are verbatim from the actual run
(seed 20260). Voiceover is recorded over the terminal capture; sections in
[square brackets] are screen only.

---

**0:00–0:08 — setup**

Screen: title card "undolog — undo a rogue agent's side effects", then a
shell prompt. Type: `docker compose up -d`, then `undolog demo`.

VO: "An AI agent is doing customer onboarding. It can read the CRM, charge
cards, send email. What happens when it goes rogue?"

**0:08–0:25 — minute 1 (benign)**

Screen: terminal prints, line by line:

```
=== PocketOS demo — agent thread 'pocketos-demo' ===
[minute 1] benign onboarding work: reads, updates, a slot hold, a plan note, the onboarding charge, the welcome email
[snapshot] world-state hash after 12 benign actions: 297c557e212edc87…
```

VO: "Twelve normal actions — updates, a calendar hold, the onboarding
charge, the welcome email. Every call goes through the ledger: before-
state captured, classified, recorded. The world-state hash is our
ground truth."

**0:25–0:38 — minute 2 (rogue)**

Screen:

```
[minute 2] ROGUE BEHAVIOR DETECTED — the agent starts overwriting CRM records, double-charging, and blasting emails
[alert] anomaly monitor: exfil pattern + duplicate charge — FREEZE at action #18 (ledger seq continues, no further side effects)
```

VO: "Then it goes rogue. CRM overwrites, a fraudulent charge,
exfiltration emails to an outside address. An anomaly alert freezes the
thread at action eighteen."

**0:38–0:48 — freeze holds**

Screen:

```
[freeze] 6 rogue tool calls rejected with FrozenError; zero side effects, zero ledger rows
```

VO: "Six more rogue attempts — all rejected. Zero side effects, zero
ledger rows. The freeze holds even while we roll back."

**0:48–1:05 — dry-run, then rollback**

Screen:

```
[DRY-RUN] rollback preview to seq 12: 3 restore(s), 1 compensation(s), 2 irreversible blast-radius entr(ies) — nothing executed
[execute] rollback complete=True: 3 restored, 1 compensated, 2 refused as irreversible
```

VO: "First a dry-run: exactly what would happen, nothing executed. Then
the real rollback. Three entries restored from captured before-state. One
fraudulent charge refunded — idempotently, so a retry can never refund
twice. Two effects refused as irreversible."

**1:05–1:15 — blast radius**

Screen:

```
[BLAST RADIUS] irreversible effects (cannot be undone):
  - email to attacker@evil.example (subject '[rogue] exfil: billing', content sha256 456553e38998298a…): irreversible/unknown class: no compensation exists; blast-radius report entry, external effect must be mitigated out of band
  - email to attacker@evil.example (subject '[rogue] exfil: crm segment', content sha256 70e988e8b2f00c8b…): irreversible/unknown class: no compensation exists; blast-radius report entry, external effect must be mitigated out of band
```

VO: "The two emails are gone — we won't pretend otherwise. You get
recipients, content hashes, and why. That's the blast radius. Honest by
design."

**1:15–1:25 — summary**

Screen:

```
=== SUMMARY ===
actions frozen:    6 rogue calls rejected (freeze at #18)
restored:          3 entries (CRM records, rogue note)
refunded:          1 fraudulent charge(s) (stripe compensations)
irreversible:      2 irreversible email(s) — see BLAST RADIUS
world-state hash:  297c557e212edc87… (pre-corruption)
                   a9bb1b0c5671d695… (post-corruption)
                   245478b877661ed5… (post-rollback)
```

VO: "Six attacks stopped, CRM and charge recovered, the un-undoable
precisely documented — in seconds, not in a restore-from-backup
incident."

**1:25–1:30 — the gate**

Screen: `undolog chaos --iterations 100` runs; end card:
"TS-01..TS-08: 500/500 seeded chaos iterations across 5 seeds. Zero lost
rows. Zero double-refunds. github.com/undolog/undolog"

VO: "And it's torture-tested: a hundred seeded chaos runs per scenario,
across five seeds — killed mid-refund, replayed calls, racing agents.
Undolog, or it didn't happen."
