"""In-memory fake of tortoise.supabase_control.SupabaseControlPlane (#767).

Implements the SAME ``query(table, select, filters, method, json_body, order,
limit)`` interface as the real PostgREST client, over plain dict rows — so
the shared resolution logic (resolve_api_key, user_memberships, ...) runs
verbatim in CI with zero network. Mirrors the backup-seam fake pattern
(plan Task 5 / P1-3): an adapter exposing query() over in-memory rows.

Filter ops: eq | neq | is (None → IS NULL) | gt | lt (NULL-excluding, SQL
semantics). PATCH applies json_body to matching rows; POST appends a row
(return=representation semantics).

``rpc(fn, body)`` simulates PostgREST RPC calls — currently ``provision_team``
(#765 plan Task 8: the atomic teams + team_memberships + api_keys upsert,
migration 0010), mirroring the SQL semantics the real function executes
(idempotent upserts, identity anchor rows, deterministic api_keys id).
"""
from __future__ import annotations

import uuid
from typing import Any


class FakeControlPlane:
    def __init__(self, tables: dict[str, list[dict]] | None = None):
        # rows are stored as dicts keyed by column name
        self.tables: dict[str, list[dict]] = tables or {}
        self.query_count = 0
        self.rpc_calls: list[tuple[str, dict]] = []

    def seed(self, table: str, rows: list[dict]) -> "FakeControlPlane":
        self.tables.setdefault(table, []).extend(rows)
        return self

    def rpc(self, fn: str, body: dict | None = None) -> dict | None:
        """Simulate provision_team (migration 0010) over the in-memory rows.

        Mirrors the real SECURITY DEFINER function's observable effects:
        teams upsert on id (name/email refreshed), exactly one membership
        row per (user|identity, team) with owner/active + key material,
        api_keys row with the deterministic id 'key_<team>_<hash12>' and
        ON CONFLICT (lookup_hash) DO NOTHING. All writes happen on the
        shared row store, so auth resolution and listing see the rows.
        """
        self.rpc_calls.append((fn, dict(body or {})))
        if fn != "provision_team":
            return None
        p = body or {}
        team_id = p.get("p_team_id") or ""
        team_name = p.get("p_team_name") or ""
        api_key = p.get("p_api_key") or ""
        lookup = p.get("p_lookup_hash") or ""
        user_id = p.get("p_user_id")
        identity = p.get("p_identity")
        if not team_id or not team_name or not api_key or not lookup:
            raise RuntimeError("provision_team: required parameters missing")
        if (user_id is None) == (identity is None):
            raise RuntimeError(
                "provision_team: exactly one of p_user_id / p_identity is required")

        # teams upsert on id (exactly one row)
        team_rows = self.tables.setdefault("teams", [])
        team = next((t for t in team_rows if t.get("id") == team_id), None)
        if team is None:
            team = {"id": team_id, "name": team_name, "tier": p.get("p_tier", "free"),
                    "graph_name": p.get("p_graph_name", f"team_{team_id}"),
                    "max_users": p.get("p_max_users", 1),
                    "max_graphs": p.get("p_max_graphs", 1),
                    "ops_allowance": p.get("p_ops_allowance", 10000),
                    "graph_size_cap": p.get("p_graph_size_cap", 10000)}
            if p.get("p_email"):
                team["email"] = p["p_email"]
            team_rows.append(team)
        else:
            team["name"] = team_name
            if p.get("p_email"):
                team["email"] = p["p_email"]

        # membership: refresh in place (user_id,team_id) or (identity,team_id),
        # else insert exactly one row
        mem_rows = self.tables.setdefault("team_memberships", [])
        if user_id is not None:
            mem = next((m for m in mem_rows
                        if m.get("user_id") == user_id and m.get("team_id") == team_id),
                       None)
            anchor = {"user_id": user_id}
        else:
            mem = next((m for m in mem_rows
                        if m.get("identity") == identity and m.get("team_id") == team_id),
                       None)
            anchor = {"user_id": None, "identity": identity}
        if mem is None:
            mem = {"id": uuid.uuid4().hex[:26], "team_id": team_id,
                   "team_name": team_name, "api_key": api_key,
                   "key_hash": p.get("p_key_hash") or "",
                   "lookup_hash": lookup,
                   "graph_name": p.get("p_graph_name", f"team_{team_id}"),
                   "role": "owner", "status": "active",
                   "created_at": None}
            mem.update(anchor)
            mem_rows.append(mem)
        else:
            mem.update({"team_name": team_name, "api_key": api_key,
                        "key_hash": p.get("p_key_hash") or "",
                        "lookup_hash": lookup, "role": "owner",
                        "status": "active"})

        # api_keys: deterministic id, ON CONFLICT (lookup_hash) DO NOTHING
        key_rows = self.tables.setdefault("api_keys", [])
        if not any(k.get("lookup_hash") == lookup for k in key_rows):
            key_rows.append({
                "id": f"key_{team_id}_{lookup[:12]}",
                "team_id": team_id,
                "lookup_hash": lookup,
                "key_prefix": p.get("p_key_prefix") or team_id[:8],
                "created_via": "provisioned",
                "created_by": str(user_id) if user_id is not None else identity,
                "created_at": None,
                "expires_at": None,
                "revoked_at": None,
            })
        return None

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
            elif op == "gt":
                # SQL semantics: NULL never matches an ordered comparison
                rows = [r for r in rows
                        if r.get(col) is not None and r.get(col) > value]
            elif op == "lt":
                rows = [r for r in rows
                        if r.get(col) is not None and r.get(col) < value]
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
        if op == "gt" and (row.get(col) is None or row.get(col) <= value):
            return False
        if op == "lt" and (row.get(col) is None or row.get(col) >= value):
            return False
    return True


class ErrorControlPlane(FakeControlPlane):
    """Control plane whose query() always raises — fail-closed testing.

    Keeps the PostgREST dialect signature (first positional = ``table``) so
    the #669 backup seam's dialect detection recognizes it as a Supabase
    source.
    """

    def __init__(self, exc: Exception | None = None):
        super().__init__()
        self._exc = exc or RuntimeError("Supabase unreachable (simulated)")

    def query(self, table: str, *args: Any, **kwargs: Any) -> list[dict]:
        raise self._exc

    def rpc(self, fn: str, body: dict | None = None) -> dict | None:
        raise self._exc
