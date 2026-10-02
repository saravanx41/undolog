"""Generic REST adapter driven by per-tool YAML specs.

A RestAdapter is built from one tool's ``adapter:`` mapping (see
registry/tools/*.yaml) and implements BOTH protocols core needs:

* capture — ``bind(**args)`` returns a per-call object exposing
  ``capture_before()`` / ``capture_after(result)`` for ``tl.wrap(...)``;
* rollback — the adapter itself is the RollbackExecutor:
  ``restore_before(entry)`` / ``compensate(entry, idempotency_key)``.

YAML semantics implemented here:

* ``base_url`` — a template (may reference ``{args.X}``, e.g. S3's
  ``{args.endpoint}``); if it renders to nothing usable, ``default_base_url``
  is used instead. ``base_url_env`` names an env var that, when set,
  overrides the base URL entirely (used to point tests at stub servers).
* ``auth`` — ``{type: bearer|oauth, env: NAME}`` send
  ``Authorization: Bearer $NAME``; ``{type: header, name: H, env: NAME}``
  sends ``H: $NAME``; ``{type: none}`` / absent sends nothing. The env var
  is read lazily at the first HTTP call; a missing token raises a clear
  ``RestError``.
* ``capture_before`` — ``{method, path, response: json|text,
  on_missing: null, store: dotted.path}``. Runs inside wrap before fn, so a
  404 with ``on_missing: null`` yields a null before-value (the tool will
  create the object). ``store`` selects a subtree of the JSON response.
* ``capture_after`` — ``record_result`` stores ``{"args", "result"}``.
* ``restore`` — ``{method, path, body, on_null}``. Body values may be the
  sentinel ``before`` (the captured before-value) or templates. When the
  captured before-value is null and ``on_null`` is given, that spec is used
  instead (S3: restoring an overwrite of a non-existent object deletes the
  created object).
* ``compensate`` — ``{method, path, body, idempotent}``. ``idempotent: true``
  sends the engine's compensation key as an ``Idempotency-Key`` header and
  treats 409 as already-done success.
"""
from __future__ import annotations

import os
import re
from typing import Any, Callable, Mapping, Optional

import httpx

from undolog_core.models import LedgerEntry

_TOKEN = re.compile(r"\{([A-Za-z_][\w.]*)\}")
_NO_BODY = object()


def default_client() -> httpx.Client:
    """The httpx client used when the adapter is not handed one explicitly
 (tests monkeypatch this to inject a MockTransport)."""
    return httpx.Client(timeout=10)


class RestError(RuntimeError):
    """Configuration or HTTP failure of a RestAdapter-backed tool."""


def _dig(node: Any, dotted: str, what: str) -> Any:
    for part in dotted.split("."):
        if not isinstance(node, Mapping) or part not in node:
            raise RestError(f"{what}: key {part!r} not found in {dotted!r}")
        node = node[part]
    return node


class RestAdapter:
    """Capture adapter + RollbackExecutor for one REST tool."""

    def __init__(
        self,
        tool_name: str,
        spec: Mapping,
        env: Optional[Mapping] = None,
        client: Optional[httpx.Client] = None,
    ):
        self.tool_name = tool_name
        self._spec = dict(spec)
        self._env: Mapping = os.environ if env is None else env
        self._client = client if client is not None else default_client()

    # -- per-call capture ---------------------------------------------------

    def bind(self, **args) -> "_BoundCall":
        """Bind the tool kwargs for one call; the result feeds tl.wrap()."""
        return _BoundCall(self, dict(args))

    def wrap_call(self, thread_ledger, fn: Callable, **args):
        """Wrap AND invoke fn with the bound args (fn is nullary: its real
        inputs are the bound tool kwargs; it performs the side effect and
        returns the ledger-storable result)."""
        wrapped = thread_ledger.wrap(
            fn, adapter=self.bind(**args), tool_name=self.tool_name
        )
        return wrapped()

    # -- RollbackExecutor ----------------------------------------------------

    def restore_before(self, entry: LedgerEntry) -> None:
        proof = entry.before_jsonb or {}
        args = proof.get("args") or {}
        before = proof.get("value")
        spec = self._spec.get("restore")
        if not spec:
            raise RestError(
                f"tool {self.tool_name!r}: no restore spec in adapter config"
            )
        if before is None and spec.get("on_null"):
            spec = spec["on_null"]  # e.g. s3: undo a create with DELETE
        # Resolve auth before rendering so a missing token is the error
        # the caller sees, not a template failure.
        headers = self._auth_headers()
        path = self._render(spec["path"], {"args": args, "before": before})
        body = self._render_body(spec.get("body"), args, None, before)
        self._request(
            spec.get("method", "PUT"), path, args,
            body=body, headers=headers,
        )

    def compensate(self, entry: LedgerEntry, idempotency_key: str) -> None:
        proof = entry.after_jsonb or {}
        args = proof.get("args") or {}
        result = proof.get("result") or {}
        spec = self._spec.get("compensate")
        if not spec:
            raise RestError(
                f"tool {self.tool_name!r}: no compensate spec in adapter config"
            )
        headers = self._auth_headers()
        ctx = {"args": args, "result": result}
        path = self._render(spec["path"], ctx)
        body = self._render_body(spec.get("body"), args, result, None)
        idem = idempotency_key if spec.get("idempotent") else None
        return self._request(
            spec.get("method", "POST"), path, args,
            body=body, idempotency_key=idem, tolerate_conflict=bool(spec.get("idempotent")),
            headers=headers,
        )

    # -- internals ------------------------------------------------------------

    def _render(self, template: str, ctx: Mapping) -> str:
        def repl(match: re.Match) -> str:
            ref = match.group(1)
            root, _, rest = ref.partition(".")
            node = ctx.get(root)
            if node is None:
                raise RestError(
                    f"tool {self.tool_name!r}: template {{{ref}}} needs "
                    f"{root!r}, which is not available"
                )
            value = _dig(node, rest, f"tool {self.tool_name!r}") if rest else node
            if isinstance(value, (dict, list)):
                raise RestError(
                    f"tool {self.tool_name!r}: template {{{ref}}} resolved "
                    "to a non-scalar value"
                )
            return str(value)

        return _TOKEN.sub(repl, str(template))

    def _render_body(self, body, args, result, before):
        if body is None:
            return _NO_BODY
        ctx = {"args": args, "result": result or {}}

        def render_value(v):
            if v == "before":
                if before is None:
                    raise RestError(
                        f"tool {self.tool_name!r}: body references 'before' "
                        "but the before-value is null"
                    )
                return before
            if isinstance(v, str):
                return self._render(v, ctx)
            if isinstance(v, dict):
                return {k: render_value(x) for k, x in v.items()}
            return v

        if body == "before":
            return before
        return render_value(body)

    def _base_url(self, args: Mapping) -> str:
        override_var = self._spec.get("base_url_env")
        if override_var:
            override = self._env.get(override_var)
            if override:
                return override
        template = self._spec.get("base_url")
        if template:
            rendered = self._render(template, {"args": args, "result": {}})
            if rendered.startswith("http"):
                return rendered
        default = self._spec.get("default_base_url")
        if default:
            return default
        raise RestError(f"tool {self.tool_name!r}: no usable base_url")

    def _auth_headers(self) -> dict:
        auth = self._spec.get("auth") or {"type": "none"}
        auth_type = auth.get("type", "none")
        if auth_type == "none":
            return {}
        env_var = auth.get("env")
        token = self._env.get(env_var) if env_var else None
        if not token:
            raise RestError(
                f"tool {self.tool_name!r}: auth env var {env_var!r} is not set"
            )
        if auth_type in ("bearer", "oauth"):
            return {"Authorization": f"Bearer {token}"}
        if auth_type == "header":
            return {auth["name"]: token}
        raise RestError(
            f"tool {self.tool_name!r}: unknown auth type {auth_type!r}"
        )

    def _request(self, method, path, args, *, body=_NO_BODY,
                 idempotency_key=None, tolerate_conflict=False,
                 allow_missing=False, headers=None):
        url = self._base_url(args).rstrip("/") + "/" + path.lstrip("/")
        headers = dict(headers) if headers is not None else self._auth_headers()
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        kwargs: dict = {"headers": headers}
        if body is not _NO_BODY:
            if isinstance(body, str):
                kwargs["content"] = body.encode()
            elif isinstance(body, (bytes, bytearray)):
                kwargs["content"] = bytes(body)
            else:
                kwargs["json"] = body
        resp = self._client.request(method, url, **kwargs)
        if resp.status_code == 404:
            if allow_missing:
                return resp
            raise RestError(
                f"tool {self.tool_name!r}: {method} {path} -> 404"
            )
        if resp.status_code == 409 and tolerate_conflict:
            return resp  # replay of an already-applied idempotent effect
        if resp.is_error:
            raise RestError(
                f"tool {self.tool_name!r}: {method} {path} -> "
                f"{resp.status_code}: {resp.text[:200]}"
            )
        return resp


class _BoundCall:
    """One bound tool call: the capture protocol object for tl.wrap()."""

    def __init__(self, adapter: RestAdapter, args: dict):
        self._adapter = adapter
        self._args = args

    def capture_before(self) -> Optional[dict]:
        spec = self._adapter._spec.get("capture_before")
        if not spec:
            return None
        adapter = self._adapter
        path = adapter._render(
            spec["path"], {"args": self._args, "result": {}}
        )
        resp = adapter._request(
            spec.get("method", "GET"), path, self._args, allow_missing=True
        )
        if resp.status_code == 404 and spec.get("on_missing") in (None, "null") \
                and "on_missing" in spec:
            value = None
        else:
            if spec.get("response") == "text":
                value = resp.text
            else:
                value = resp.json() if resp.content else None
            store = spec.get("store")
            if store and value is not None:
                value = _dig(value, store, f"tool {adapter.tool_name!r}")
        return {"args": self._args, "value": value}

    def capture_after(self, result: Any) -> Optional[dict]:
        spec = self._adapter._spec.get("capture_after")
        if not spec:
            return None
        if not isinstance(result, (Mapping, list, str, int, float, bool, type(None))):
            raise RestError(
                f"tool {self._adapter.tool_name!r}: result of type "
                f"{type(result).__name__} is not ledger-storable"
            )
        return {"args": self._args, "result": result}
