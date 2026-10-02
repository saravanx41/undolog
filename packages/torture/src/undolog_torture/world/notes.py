"""Mock Notes: appended notes (minimal in-world fake for the registry tool
notes.add, which has no Week-1 mock backend).

Notes are reversible: the adapter snapshots the whole notes map before each
add, so a rollback can restore the pre-add state exactly.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any


@dataclass
class NotesWorld:
    notes: dict[str, dict[str, Any]] = field(default_factory=dict)
    _seq: int = 0

    def add(self, title: str, body: str) -> dict[str, Any]:
        self._seq += 1
        note = {
            "id": f"note_{self._seq}",
            "title": title,
            "body_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
        }
        self.notes[note["id"]] = note
        return dict(note)

    def snapshot(self) -> dict[str, dict[str, Any]]:
        return {nid: dict(n) for nid, n in sorted(self.notes.items())}
