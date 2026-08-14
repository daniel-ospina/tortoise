"""In-memory fake of tortoise.supabase_control.SupabaseControlPlane (#767).

Implements the SAME ``query(table, select, filters, method, json_body, order,
limit)`` interface as the real PostgREST client, over plain dict rows — so
the shared resolution logic (resolve_api_key, user_memberships, ...) runs
verbatim in CI with zero network. Mirrors the backup-seam fake pattern
(plan Task 5 / P1-3): an adapter exposing query() over in-memory rows.

Filter ops: eq | neq | is (None → IS NULL) | gt | lt | lte (all ordered
ops NULL-excluding, SQL semantics). PATCH applies json_body to matching
rows; POST appends a row (return=representation semantics); DELETE
removes matching rows (mirrors PostgREST service-role deletes, #302).

``rpc(fn, body)`` simulates PostgREST RPC calls — currently ``provision_team``
(#765 plan Task 8: the atomic teams + team_memberships + api_keys upsert,
migration 0010), mirroring the SQL semantics the real function executes
(idempotent upserts, identity anchor rows, deterministic api_keys id).
"""
from __future__ import annotations

import uuid
from typing import Any


class FakeControlPlane:
    def __init__(self, tables: dict[str, list[dict]] | None = None,
                 *, missing_columns: dict[str, set[str]] | None = None):
        # rows are stored as dicts keyed by column name
        self.tables: dict[str, list[dict]] = tables or {}
        self.query_count = 0
        self.rpc_calls: list[tuple[str, dict]] = []
        # #1096 drift mode: columns that are absent from the "schema" of a
        # table (mirrors PostgREST 400 PGRST204 on select/filter of an
        # absent column). Default None → behavior identical to before.
        self.missing_columns: dict[str, set[str]] | None = missing_columns

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

        #308: also emulates migration 0015 — the api_keys INSERT trigger
        (key_create abuse_events row unless created_via='bootstrap') and the
        abuse_suspend/abuse_unsuspend RPCs (teams.suspended_at/flagged_at).
        """
        self.rpc_calls.append((fn, dict(body or {})))
        if fn == "abuse_suspend":
            # Mirrors the SQL: set suspended_at only when NULL; flagged_at is
            # NOT touched (the engine's flag-episode state is event-derived).
            tid = (body or {}).get("p_team_id")
            for t in self.tables.get("teams", []):
                if t.get("id") == tid and t.get("suspended_at") is None:
                    from datetime import datetime, timezone
                    t["suspended_at"] = datetime.now(timezone.utc).isoformat()
            from datetime import datetime, timezone
            self.tables.setdefault("abuse_events", []).append(
                {"team_id": tid, "event_type": "suspend", "weight": 1,
                 "created_at": datetime.now(timezone.utc).isoformat()})
            return None
        if fn == "abuse_unsuspend":
            tid = (body or {}).get("p_team_id")
            for t in self.tables.get("teams", []):
                if t.get("id") == tid:
                    t["suspended_at"] = None
                    t["flagged_at"] = None
            from datetime import datetime, timezone
            now_iso = datetime.now(timezone.utc).isoformat()
            events = self.tables.setdefault("abuse_events", [])
            events.append({"team_id": tid, "event_type": "unsuspend",
                           "weight": 1, "created_at": now_iso})
            # end every flag episode (mirrors the SQL)
            for rule in ("point_create", "key_create"):
                events.append({"team_id": tid, "event_type": "flag_clear",
                               "rule": rule, "weight": 1,
                               "created_at": now_iso})
            return None
        if fn == "metering_increment":
            # Emulate migration 0014's SQL function: atomic upsert + increment.
            p = body or {}
            rows = self.tables.setdefault("metering_records", [])
            row = next((r for r in rows if r["team_id"] == p.get("p_team_id")
                        and r["period"] == p.get("p_period")), None)
            n = int(p.get("p_n") or 1)
            if row:
                row["write_ops"] = row.get("write_ops", 0) + n
            else:
                rows.append({"team_id": p.get("p_team_id"),
                             "period": p.get("p_period"),
                             "write_ops": n})
            return None  # PostgREST minimal — no echo
        if fn == "claim_membership":
            # Emulate migration 20260813000004's SQL semantics over the
            # in-memory rows (mirrors the real SECURITY DEFINER function):
            #  1. resolve team from api_keys (lookup_hash + revoked_at IS
            #     NULL; REJECT created_via='bootstrap' session keys and
            #     expired keys)
            #  2. idempotent re-claim: owner (team_id, user_id) → noop
            #  3. find the NULL-user_id active owner row → 409 already_claimed
            #     when absent
            #  4. merge/promote when a (user_id, team_id) row exists (drop
            #     identity row first, copy key material, reactivate) else
            #     plain link (user_id set, identity NULL)
            #  5. teams.email overwrite (unconditional); cross-team collision
            #     → email_in_use (uq_teams_email parity)
            #  6. drop leftover placeholder (team_id='')
            # Errors raise RuntimeError with the RPC code embedded (the real
            # RPC raises → PostgREST 400 → supabase_control.claim_membership
            # maps the code via _CLAIM_ERROR_CODES).
            p = body or {}
            lookup = p.get("p_lookup_hash") or ""
            user_id = p.get("p_user_id")
            email = p.get("p_email")
            if not lookup:
                raise RuntimeError("claim_membership:key_required")
            if not user_id:
                raise RuntimeError("claim_membership:user_required")
            key = next((k for k in self.tables.get("api_keys", [])
                        if k.get("lookup_hash") == lookup
                        and k.get("revoked_at") is None), None)
            if key is None:
                raise RuntimeError("claim_membership:key_not_found")
            if key.get("created_via") == "bootstrap":
                raise RuntimeError("claim_membership:key_not_claimable")
            from datetime import datetime, timezone
            exp = key.get("expires_at")
            if exp is not None and isinstance(exp, str) \
                    and exp <= datetime.now(timezone.utc).isoformat():
                raise RuntimeError("claim_membership:key_expired")
            team_id = key["team_id"]
            mem_rows = self.tables.setdefault("team_memberships", [])

            # idempotent re-claim (noop success)
            if any(m.get("team_id") == team_id and m.get("user_id") == user_id
                   and m.get("role") == "owner" and m.get("status") == "active"
                   for m in mem_rows):
                self._claim_set_email(team_id, email)
                return None

            owner = next((m for m in mem_rows
                          if m.get("team_id") == team_id
                          and m.get("role") == "owner"
                          and m.get("user_id") is None
                          and m.get("status") == "active"), None)
            if owner is None:
                raise RuntimeError("claim_membership:already_claimed")

            # uq_teams_email parity: verify the email is free BEFORE any
            # mutation — the real RPC's unique violation rolls the whole
            # transaction back (owner stays anon on email_in_use).
            if any(t.get("id") != team_id and t.get("email") == email
                   for t in self.tables.get("teams", [])):
                raise RuntimeError("claim_membership:email_in_use")

            existing = next((m for m in mem_rows
                             if m.get("user_id") == user_id
                             and m.get("team_id") == team_id), None)
            if existing is not None:
                # merge/promote: drop identity row FIRST, then promote
                mem_rows.remove(owner)
                existing.update({"role": "owner", "status": "active",
                                 "identity": None,
                                 "lookup_hash": existing.get("lookup_hash")
                                                or owner.get("lookup_hash"),
                                 "key_hash": existing.get("key_hash")
                                             or owner.get("key_hash")})
            else:
                owner["user_id"] = user_id
                owner["identity"] = None

            # drop leftover placeholder (team_id='') for the user
            mem_rows[:] = [m for m in mem_rows
                           if not (m.get("user_id") == user_id
                                   and m.get("team_id") == "")]
            # email overwrite (collision already verified above)
            for t in self.tables.get("teams", []):
                if t.get("id") == team_id:
                    t["email"] = email
            return None
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
                    "graph_size_cap": p.get("p_graph_size_cap", 10000),
                    # #1148: dashboard key-login acceptance (migration default true)
                    "dashboard_key_login": True}
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
        # (a re-provision inserts nothing → the 0015 trigger fires nothing —
        # no duplicate key_create event; cycle-2 test note).
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
            self._trigger_key_create(team_id, f"key_{team_id}_{lookup[:12]}",
                                     "provisioned")
        return None

    def _claim_set_email(self, team_id: str, email: str) -> None:
        """uq_teams_email parity: teams.email overwrite, cross-team collision
        → claim_membership:email_in_use (the fake mirrors the unique index)."""
        for t in self.tables.get("teams", []):
            if t.get("id") != team_id and t.get("email") == email:
                raise RuntimeError("claim_membership:email_in_use")
        for t in self.tables.get("teams", []):
            if t.get("id") == team_id:
                t["email"] = email

    def _trigger_key_create(self, team_id: str, key_id: str,
                            created_via: str | None) -> None:
        """Migration 0015 trigger emulation: AFTER INSERT on api_keys →
        key_create abuse event, EXCLUDING created_via='bootstrap'."""
        if created_via == "bootstrap":
            return
        from datetime import datetime, timezone
        self.tables.setdefault("abuse_events", []).append(
            {"team_id": team_id, "event_type": "key_create", "weight": 1,
             "key_id": key_id,
             "created_at": datetime.now(timezone.utc).isoformat()})

    def query(self, table: str, *, select: list[str] | None = None,
              filters: list[tuple[str, str, object]] | None = None,
              method: str = "GET", json_body: dict | None = None,
              order: str | None = None, limit: int | None = None) -> list[dict]:
        self.query_count += 1
        if method == "PATCH":
            # mutate the STORED rows (mirrors PostgREST update semantics);
            # return=representation when a select is given → the UPDATED
            # rows ([] when nothing matched), the atomic-claim path used by
            # OAuth single-use codes / rotation (PR #1264 review P2).
            updated: list[dict] = []
            for r in self.tables.get(table, []):
                if _matches(r, filters or []):
                    r.update(json_body or {})
                    if select is not None:
                        updated.append({k: r.get(k) for k in select})
            return updated if select is not None else []
        if method == "POST":
            row = dict(json_body or {})
            if table == "abuse_events" and row.get("created_at") is None:
                # mirror the DB column default now() — window gt-filters need it
                from datetime import datetime, timezone
                row["created_at"] = datetime.now(timezone.utc).isoformat()
            self.tables.setdefault(table, []).append(row)
            if table == "api_keys":
                # migration 0015 trigger emulation (#308)
                self._trigger_key_create(row.get("team_id", ""),
                                         row.get("id", ""),
                                         row.get("created_via"))
            return [row]
        if method == "DELETE":
            # PostgREST row-delete semantics (used by the #302 purge).
            self.tables[table] = [
                r for r in self.tables.get(table, []) if not _matches(r, filters or [])
            ]
            return []
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
            elif op == "lte":
                rows = [r for r in rows
                        if r.get(col) is not None and r.get(col) <= value]
            else:
                raise ValueError(f"unsupported filter op {op!r}")
        if method == "GET":
            if (self.missing_columns and table in self.missing_columns
                    and select and self.missing_columns[table] & set(select)):
                # Mirrors the #1001 failure: PostgREST HTTP 400 for an
                # absent column (PGRST204 per the error reference); the real
                # seam discards the body, so only HTTP 400 surfaces.
                raise RuntimeError(
                    f"Supabase control-plane query failed ({table}): HTTP 400")
            if (self.missing_columns and table in self.missing_columns
                    and filters
                    and self.missing_columns[table] & {c for c, _, _ in filters}):
                # Filter-column drift mirrors the same seam (the #302 sweeps
                # filter deleted_at). Out-of-slice scaffolding for the
                # escalation decomposition's sweep/health tests.
                raise RuntimeError(
                    f"Supabase control-plane query failed ({table}): HTTP 400")
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
        if op == "lte" and (row.get(col) is None or row.get(col) > value):
            # ISO-8601 cutoff (mirrors the GET path — #302 purge).
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
