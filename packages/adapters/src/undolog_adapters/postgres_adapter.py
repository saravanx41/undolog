"""Postgres row-snapshot adapter + row-precise restore executor.

PostgresAdapter captures before/after proof for a tool that writes rows of
one table inside a caller-managed transaction:

* ``capture_before()`` runs in the caller's open transaction on ``conn``
  and takes ``SELECT ... FOR UPDATE`` row locks on EXACTLY the target rows,
  snapshotting them (absent rows snapshot as None — i.e. the tool will
  INSERT them). The wrapped fn then performs its writes on the same
  connection: capture and write share one transaction and one lock set.
* ``capture_after(result)`` re-reads the same rows after fn ran.

Which rows: ``rows`` is either a sequence of primary-key values or a
callable returning one, evaluated at each capture (a callable lets an
INSERT declare its new keys lazily). The caller commits the tool
transaction with ``adapter.commit()`` after wrap() returns.

PostgresRestoreExecutor restores a captured before-proof row-precisely,
never table-swap: in a single transaction it re-locks each proof row with
``FOR UPDATE`` and then UPDATEs it back to the before values, DELETEs it if
the tool had inserted it (absent before, present now), or re-INSERTs it if
the tool had deleted it. Concurrent writers to other rows are untouched.
"""
from __future__ import annotations

from typing import Any, Callable, Optional, Sequence

from psycopg.sql import SQL, Identifier

from undolog_core.models import LedgerEntry

RowsSpec = Sequence[Any] | Callable[[], Sequence[Any]]


def _pk_value(key: str) -> Any:
    """JSONB object keys are strings; recover int pks for = comparisons."""
    return int(key) if str(key).lstrip("-").isdigit() else key


class PostgresAdapter:
    """Row-level FOR UPDATE snapshot of a tool's target rows."""

    def __init__(self, conn, table: str, rows: RowsSpec, pk: str = "id"):
        self._conn = conn
        self._table = table
        self._pk = pk
        self._rows = rows

    def _row_ids(self) -> list:
        return list(self._rows() if callable(self._rows) else self._rows)

    def _select(self, row_ids: list, lock: bool) -> dict:
        if not row_ids:
            return {}
        stmt = SQL("SELECT * FROM {} WHERE {} = ANY(%s){}").format(
            Identifier(self._table),
            Identifier(self._pk),
            SQL(" FOR UPDATE") if lock else SQL(""),
        )
        found = {
            str(r[self._pk]): dict(r)
            for r in self._conn.execute(stmt, (row_ids,)).fetchall()
        }
        return {str(rid): found.get(str(rid)) for rid in row_ids}

    def capture_before(self) -> dict:
        before = self._select(self._row_ids(), lock=True)
        return {"table": self._table, "pk": self._pk, "before": before}

    def capture_after(self, result: Any) -> dict:
        return {"after": self._select(self._row_ids(), lock=False)}

    def commit(self) -> None:
        """Commit the tool transaction (releases the capture locks)."""
        self._conn.commit()


class PostgresRestoreExecutor:
    """Row-precise restore of a PostgresAdapter before-proof.

    ``connect`` is a zero-arg callable returning a fresh, schema-bound
    psycopg connection; all restores for one entry run in a single
    transaction on it.
    """

    def __init__(self, connect: Callable[[], Any]):
        self._connect = connect

    def restore_before(self, entry: LedgerEntry) -> None:
        proof = entry.before_jsonb or {}
        try:
            table, pk = proof["table"], proof["pk"]
        except KeyError as exc:
            raise RuntimeError(
                f"postgres entry before-proof is missing {exc!s}"
            ) from exc
        before = proof.get("before") or {}
        conn = self._connect()
        try:
            with conn.transaction():
                for key, wanted in before.items():
                    self._restore_row(conn, table, pk, key, wanted)
        finally:
            conn.close()

    def compensate(self, entry: LedgerEntry, idempotency_key: str) -> None:
        raise RuntimeError(
            "postgres entries are reversible; "
            "compensate() must never be called"
        )

    def _restore_row(self, conn, table: str, pk: str, key: str,
                     wanted: Optional[dict]) -> None:
        lock = SQL("SELECT 1 FROM {} WHERE {} = %s FOR UPDATE").format(
            Identifier(table), Identifier(pk)
        )
        exists = conn.execute(lock, (_pk_value(key),)).fetchone() is not None
        if wanted is None:
            if exists:  # the tool INSERTed this row: undo it
                conn.execute(
                    SQL("DELETE FROM {} WHERE {} = %s").format(
                        Identifier(table), Identifier(pk)
                    ),
                    (_pk_value(key),),
                )
            return
        wanted = dict(wanted)
        if not exists:
            # The tool (or a later writer) DELETEd this row: re-INSERT it.
            cols = list(wanted)
            stmt = SQL("INSERT INTO {} ({}) VALUES ({})").format(
                Identifier(table),
                SQL(", ").join(Identifier(c) for c in cols),
                SQL(", ").join(SQL("%s") for _ in cols),
            )
            conn.execute(stmt, [wanted[c] for c in cols])
            return
        sets = [c for c in wanted if c != pk]
        stmt = SQL("UPDATE {} SET {} WHERE {} = %s").format(
            Identifier(table),
            SQL(", ").join(SQL("{} = %s").format(Identifier(c)) for c in sets),
            Identifier(pk),
        )
        conn.execute(stmt, [wanted[c] for c in sets] + [_pk_value(key)])
