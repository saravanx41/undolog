"""EscalatingLedger — TS-08 support.

Thin alias kept for compatibility: core's wrap() now escalates the entry
class to "unknown" whenever adapter capture fails (capture_before or
capture_after), in addition to demoting the row to log-only (NULL proof).
The capture-timeout path in TortureConfig therefore produces
class="unknown" rows through plain core behavior — no override needed.
"""
from __future__ import annotations

from undolog_core.ledger import Ledger


class EscalatingLedger(Ledger):
    """Ledger whose wrap() escalates capture-failed rows to class unknown.

    Identical to core Ledger: the escalation lives in undolog_core itself.
    """
