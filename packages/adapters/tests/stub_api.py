"""In-process stub of the five external APIs, with a request audit log.

Shared by the MockTransport unit tests (make_httpx_handler) and the
real-HTTP uvicorn test (build_app), so both exercise identical semantics:

* Idempotency: a request carrying an ``Idempotency-Key`` header replays the
  first response WITHOUT re-applying the effect (Stripe-style); every
  request is still audited with ``applied: bool`` so tests can assert
  exact effect counts (e.g. exactly one PATCH).
* Unknown paths return 404, which surfaces in tests as an adapter error.
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

import httpx
from fastapi import FastAPI, Request, Response


class StubWorld:
    def __init__(self):
        self.issues: dict[str, dict] = {}
        self._next_issue = 0
        self.objects: dict[str, bytes] = {}
        self.events: dict[str, dict] = {}
        self._next_event = 0
        self.contacts: dict[str, dict] = {}
        self.requests: list[dict] = []
        self._idempotent: dict[tuple, tuple] = {}

    # -- dispatch ----------------------------------------------------------

    def handle(self, method: str, path: str, headers: dict,
               content: Optional[bytes]) -> tuple[int, Any, str]:
        """Return (status, body, content_type); body is dict|str|bytes|None."""
        idem = headers.get("idempotency-key")
        audit = {
            "method": method,
            "path": path,
            "idempotency_key": idem,
            "body": self._peek(content),
        }
        self.requests.append(audit)
        key = (method, path, idem)
        if idem and key in self._idempotent:
            audit["applied"] = False
            return self._idempotent[key]
        status, body, ctype = self._dispatch(method, path, content)
        audit["applied"] = True
        if idem:
            self._idempotent[key] = (status, body, ctype)
        return status, body, ctype

    @staticmethod
    def _peek(content: Optional[bytes]) -> Any:
        if not content:
            return None
        try:
            return json.loads(content)
        except Exception:
            return {"bytes": len(content)}

    def _dispatch(self, method, path, content):
        try:
            body = json.loads(content) if content else None
        except Exception:
            body = None  # non-JSON body (e.g. raw object bytes)

        m = re.fullmatch(r"/repos/(.+)/issues", path)
        if m and method == "POST":
            self._next_issue += 1
            n = str(self._next_issue)
            self.issues[f"{m.group(1)}#{n}"] = {
                "number": int(n), "repo": m.group(1), "state": "open",
            }
            return 201, dict(self.issues[f"{m.group(1)}#{n}"]), "application/json"

        m = re.fullmatch(r"/repos/(.+)/issues/(\d+)", path)
        if m and method == "PATCH":
            issue = self.issues.get(f"{m.group(1)}#{m.group(2)}")
            if issue is None:
                return 404, {"message": "not found"}, "application/json"
            issue.update({k: v for k, v in (body or {}).items()})
            return 200, dict(issue), "application/json"

        m = re.fullmatch(r"/([^/]+)/([^/]+)", path)
        if m and method in ("GET", "PUT", "DELETE"):
            k = f"{m.group(1)}/{m.group(2)}"
            if method == "GET":
                if k not in self.objects:
                    return 404, b"not found", "text/plain"
                return 200, self.objects[k], "application/octet-stream"
            if method == "PUT":
                self.objects[k] = content or b""
                return 200, {"ok": True}, "application/json"
            self.objects.pop(k, None)
            return 204, None, "application/json"

        m = re.fullmatch(r"/calendars/([^/]+)/events", path)
        if m and method == "POST":
            self._next_event += 1
            eid = f"evt_{self._next_event}"
            self.events[f"{m.group(1)}#{eid}"] = {
                "id": eid, "calendarId": m.group(1),
            }
            return 200, dict(self.events[f"{m.group(1)}#{eid}"]), "application/json"

        m = re.fullmatch(r"/calendars/([^/]+)/events/([^/]+)", path)
        if m and method == "DELETE":
            self.events.pop(f"{m.group(1)}#{m.group(2)}", None)
            return 204, None, "application/json"

        m = re.fullmatch(r"/crm/v3/objects/contacts/([^/]+)", path)
        if m and method == "GET":
            contact = self.contacts.get(m.group(1))
            if contact is None:
                return 404, {"message": "not found"}, "application/json"
            return 200, {"id": m.group(1),
                         "properties": dict(contact["properties"])}, "application/json"
        if m and method == "PATCH":
            contact = self.contacts.setdefault(m.group(1), {"properties": {}})
            contact["properties"].update((body or {}).get("properties") or {})
            return 200, {"id": m.group(1),
                         "properties": dict(contact["properties"])}, "application/json"

        return 404, {"message": f"stub: no route for {method} {path}"}, "application/json"

    # -- assertions --------------------------------------------------------

    def applied(self, method: str, path_suffix: str = "") -> list[dict]:
        return [
            r for r in self.requests
            if r["applied"] and r["method"] == method
            and r["path"].endswith(path_suffix)
        ]


def make_httpx_handler(world: StubWorld):
    """Adapt the stub world to an httpx MockTransport handler."""
    def handler(request: httpx.Request) -> httpx.Response:
        content = request.content
        status, body, ctype = world.handle(
            request.method, request.url.path,
            dict(request.headers), content,
        )
        if body is None:
            return httpx.Response(status, headers={"Content-Type": ctype})
        if isinstance(body, (dict, list)):
            return httpx.Response(status, json=body)
        if isinstance(body, str):
            body = body.encode()
        return httpx.Response(status, content=body)
    return handler


def build_app(world: StubWorld):
    """FastAPI app serving the stub world over real HTTP (uvicorn)."""
    app = FastAPI(title="undolog-rest-stub")

    @app.api_route("/{full_path:path}",
                   methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    async def catch_all(full_path: str, request: Request) -> Response:
        content = await request.body()
        status, body, ctype = world.handle(
            request.method, "/" + full_path,
            dict(request.headers), content or None,
        )
        if body is None:
            payload = b""
        elif isinstance(body, (dict, list)):
            ctype = "application/json"
            payload = json.dumps(body).encode()
        elif isinstance(body, str):
            payload = body.encode()
        else:
            payload = body
        return Response(content=payload, status_code=status,
                        media_type=ctype)

    return app
