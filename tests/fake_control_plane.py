"""In-memory fake of tortoise.supabase_control.SupabaseControlPlane (#767).

Implements the SAME ``query(table, select, filters, method, json_body, order,
limit)`` interface as the real PostgREST client, over plain dict rows — so
the shared resolution logic (resolve_api_key, user_memberships, ...) runs
verbatim in CI with zero network. Mirrors the backup-seam fake pattern
(plan Task 5 / P1-3): an adapter exposing query() over in-memory rows.

Filter ops: eq | neq | is (None → IS NULL). PATCH applies json_body to
matching rows; POST appends a row (return=representation semantics).
"""
from __future__ import annotations

from typing import Any


class FakeControlPlane:
    def __init__(self, tables: dict[str, list[dict]] | None = None):
        # rows are stored as dicts keyed by column name
        self.tables: dict[str, list[dict]] = tables or {}
        self.query_count = 0

    def seed(self, table: str, rows: list[dict]) -> "FakeControlPlane":
        self.tables.setdefault(table, []).extend(rows)
        return self

    def query(self, table: str, *, select: list[str] | None = None,
              filters: list[tuple[str, str, object]] | None = None,
              method: str = "GET", json_body: dict | None = None,
              order: str | None = None, limit: int | None = None) -> list[dict]:
        self.query_count += 1
        if method == "PATCH":
            # mutate the STORED rows (mirrors PostgREST update semantics)
            for r in self.tables.get(table, []):
                if _matches(r, filters or []):
                    r.update(json_body or {})
            return []
        if method == "POST":
            row = dict(json_body or {})
            self.tables.setdefault(table, []).append(row)
            return [row]
        rows = [dict(r) for r in self.tables.get(table, [])]
        for col, op, value in filters or []:
            if op == "eq":
                rows = [r for r in rows if r.get(col) == value]
            elif op == "neq":
                rows = [r for r in rows if r.get(col) != value]
            elif op == "is":
                rows = [r for r in rows if (r.get(col) is None) == (value is None)]
            else:
                raise ValueError(f"unsupported filter op {op!r}")
        if method == "GET":
            if select:
                rows = [{k: r.get(k) for k in select} for r in rows]
            if order:
                col = order.lstrip("-")
                rows.sort(key=lambda r: r.get(col) or "", reverse=order.startswith("-"))
            if limit is not None:
                rows = rows[:limit]
            return rows
        raise ValueError(f"unsupported method {method!r}")


def _matches(row: dict, filters: list[tuple[str, str, object]]) -> bool:
    for col, op, value in filters:
        if op == "eq" and row.get(col) != value:
            return False
        if op == "neq" and row.get(col) == value:
            return False
        if op == "is" and (row.get(col) is None) != (value is None):
            return False
    return True


class ErrorControlPlane(FakeControlPlane):
    """Control plane whose query() always raises — fail-closed testing."""

    def __init__(self, exc: Exception | None = None):
        super().__init__()
        self._exc = exc or RuntimeError("Supabase unreachable (simulated)")

    def query(self, *args: Any, **kwargs: Any) -> list[dict]:
        raise self._exc
