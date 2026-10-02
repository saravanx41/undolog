"""Mock CRM: seeded in-memory records + FastAPI app builder.

Records live in a dict {record_id: data}; PUT overwrites the whole record.
Seeding is driven by an injected random.Random so identical seeds give
identical worlds.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any, Optional

from fastapi import FastAPI, HTTPException

CRM_RECORD_COUNT = 50

_FIRST = ["Ada", "Grace", "Alan", "Edsger", "Barbara", "Donald", "Radia",
          "Katherine", "Margaret", "Dennis"]
_LAST = ["Lovelace", "Hopper", "Turing", "Dijkstra", "Liskov", "Knuth",
         "Perlman", "Johnson", "Hamilton", "Ritchie"]
_TIERS = ["free", "pro", "enterprise"]
_COMPANIES = ["initech", "globex", "umbrella", "stark", "wayne", "acme"]


def seed_crm_records(rng: random.Random,
                     count: int = CRM_RECORD_COUNT) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    for i in range(count):
        first = rng.choice(_FIRST)
        last = rng.choice(_LAST)
        records[f"rec_{i:03d}"] = {
            "name": f"{first} {last}",
            "email": f"{first}.{last}{i}@{rng.choice(_COMPANIES)}.example".lower(),
            "tier": rng.choice(_TIERS),
            "spend": rng.randrange(0, 100_000),
        }
    return records


@dataclass
class CRMWorld:
    records: dict[str, dict[str, Any]] = field(default_factory=dict)

    @classmethod
    def seeded(cls, rng: random.Random,
               count: int = CRM_RECORD_COUNT) -> "CRMWorld":
        return cls(records=seed_crm_records(rng, count))

    def get(self, record_id: str) -> dict[str, Any]:
        if record_id not in self.records:
            raise KeyError(f"no such record {record_id!r}")
        return {"id": record_id, "data": dict(self.records[record_id])}

    def update(self, record_id: str, data: dict[str, Any]) -> dict[str, Any]:
        if record_id not in self.records:
            raise KeyError(f"no such record {record_id!r}")
        self.records[record_id] = dict(data)
        return {"id": record_id, "data": dict(data)}

    def update_many(self, updates: dict[str, dict[str, Any]]) -> dict[str, Any]:
        updated = {}
        for record_id, data in updates.items():
            updated[record_id] = self.update(record_id, data)["data"]
        return {"updated": updated}

    def list_records(self) -> list[dict[str, Any]]:
        return [
            {"id": rid, "data": dict(self.records[rid])}
            for rid in sorted(self.records)
        ]


def build_crm_app(world: CRMWorld) -> FastAPI:
    app = FastAPI(title="mock-crm")

    @app.get("/records/{record_id}")
    def get_record(record_id: str) -> dict[str, Any]:
        try:
            return world.get(record_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.put("/records/{record_id}")
    def put_record(record_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return world.update(record_id, payload["data"])
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/records")
    def list_records() -> list[dict[str, Any]]:
        return world.list_records()

    return app
