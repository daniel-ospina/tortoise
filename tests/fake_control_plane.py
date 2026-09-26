"""In-memory fake of tortoise.supabase_control.SupabaseControlPlane (#767).

Implements the SAME ``query(table, select, filters, method, json_body, order,
limit)`` interface as the real PostgREST client, over plain dict rows — so
the shared resolution logic (resolve_api_key, user_memberships, ...) runs
verbatim in CI with zero network. Mirrors the backup-seam fake pattern
(plan Task 5 / P1-3): an adapter exposing query() over in-memory rows.

Filter ops: eq | neq | is (None → IS NULL) | gt | gte | lt | lte (all ordered
ops NULL-excluding, SQL semantics). PATCH applies json_body to matching
rows; POST appends a row (return=representation semantics); DELETE
removes matching rows (mirrors PostgREST service-role deletes, #302).

``rpc(fn, body)`` simulates PostgREST RPC calls — ``provision_team``
(#765 plan Task 8: the atomic teams + org_memberships + api_keys upsert,
migration 0010; #1716: all-NULL key params → keyless provision, NO
api_keys row) plus the #1709 agent-signup-token trio
(``provision_team_with_token`` / ``resolve_signup_token`` /
``recover_team_key``, migration 20260814000001 — the recovery mint is
serialized under a process lock, emulating the RPC's FOR UPDATE row lock),
mirroring the SQL semantics the real functions execute
(idempotent upserts, identity anchor rows, deterministic api_keys id).
"""
from __future__ import annotations

import uuid
from datetime import UTC
from typing import Any

# #1719 (Task 3): columns whose PostgREST filter values are cast to uuid —
# a non-UUID literal 22P02s (HTTP 400) in prod. Default-on fidelity makes
# the fake raise the SAME RuntimeError surface the real query() raises, so
# a future unsanitized call site fails CI instead of silently no-matching
# ("CI green while prod 500s"). Extendable registry (mirrors missing_columns).
UUID_FILTER_COLUMNS: set[tuple[str, str]] = {("org_memberships", "user_id")}


def _assert_uuid_fidelity(table: str, filters: list[tuple[str, str, object]] | None) -> None:
    """Raise RuntimeError("... HTTP 400") when a filter value on a registered
    uuid column is a non-None non-UUID string — mirroring PostgREST 22P02.
    None (is.null) and real UUIDs pass. Only eq/neq carry a cast; is does not."""
    if not filters:
        return
    for col, op, value in filters:
        if op not in ("eq", "neq"):
            continue
        if (table, col) not in UUID_FILTER_COLUMNS:
            continue
        if value is None or not isinstance(value, str):
            continue
        try:
            import uuid as _uuid
            _uuid.UUID(value)
        except (ValueError, TypeError, AttributeError):
            raise RuntimeError(
                f"Supabase control-plane query failed ({table}): HTTP 400"
            ) from None


# #4037: PostgREST's `order` grammar is a comma-separated list of
# `field[.asc|.desc][.nullsfirst|.nullslast]`. The fake used to speak a private
# `-col` dialect (`col = order.lstrip("-")`), which ACCEPTED the form PostgREST
# rejects (a leading `-` is not part of the grammar → PGRST100 / HTTP 400) AND
# silently NO-OP'd the form it accepts (`col.desc` looked up a column literally
# named `"created_at.desc"`). So `SupabaseAbuseStore`'s `order="-created_at"`
# was green in CI while 400ing in prod: Stage-2 suspension never ran and
# `/v1/team/alerts` was permanently empty (the 400 is swallowed fail-soft).
#
# This parser is deliberately an INDEPENDENT oracle — it must NOT import a
# production order helper, because a shared implementation would share its
# blind spot, which is the exact failure #4037 fixes. It is a stated SUBSET of
# the wire grammar: JSON-path (`col->>key`) and embedded-resource ordering raise
# loudly rather than being silently accepted, and no call site uses them.
_ORDER_DIRECTIONS = {"asc": False, "desc": True}
_ORDER_NULLS = {"nullsfirst": True, "nullslast": False}


def _is_order_field(token: str) -> bool:
    """A PostgREST field name, minus the JSON-path/embedded-resource forms the
    fake does not model. A leading `-` is refused — that is the #4037 defect."""
    if not token or token[0] == "-":
        return False
    if not (token[0].isalpha() or token[0] == "_"):
        return False
    return all(c.isalnum() or c in "_$-" for c in token)


def _parse_order(table: str, order: str) -> list[tuple[str, bool, bool | None]]:
    """Parse a PostgREST ``order`` string into ``(field, descending,
    nulls_first|None)`` terms. An unparseable term raises the same
    ``RuntimeError(... HTTP 400)`` surface the real client produces when
    PostgREST rejects the query string (``PGRST100``)."""
    terms: list[tuple[str, bool, bool | None]] = []
    for raw in order.split(","):
        parts = raw.split(".")
        field = parts.pop(0)
        if not _is_order_field(field):
            raise RuntimeError(
                f"Supabase control-plane query failed ({table}): HTTP 400")
        descending = False
        nulls_first: bool | None = None
        if parts and parts[0] in _ORDER_DIRECTIONS:
            descending = _ORDER_DIRECTIONS[parts.pop(0)]
        if parts and parts[0] in _ORDER_NULLS:
            nulls_first = _ORDER_NULLS[parts.pop(0)]
        if parts:
            raise RuntimeError(
                f"Supabase control-plane query failed ({table}): HTTP 400")
        terms.append((field, descending, nulls_first))
    return terms


def _apply_order(rows: list[dict],
                 terms: list[tuple[str, bool, bool | None]]) -> list[dict]:
    """Apply PostgREST order terms (most-significant first) as successive
    STABLE sorts, so a tie on a later term keeps the earlier term's order.
    NULL placement follows Postgres: ``asc`` → nulls last, ``desc`` → nulls
    first, overridable by an explicit ``nullsfirst``/``nullslast`` token."""
    for field, descending, nulls_first in reversed(terms):
        if nulls_first is None:
            nulls_first = descending
        present = [r for r in rows if r.get(field) is not None]
        nulls = [r for r in rows if r.get(field) is None]
        present.sort(key=lambda r: r.get(field), reverse=descending)
        rows = (nulls + present) if nulls_first else (present + nulls)
    return rows


# #2863: module-level registry of every control plane a `fail_query` was installed
# on. The autouse `_no_silent_faults` guard in the OAuth fault suite reads it to
# fail a test whose injector never fired (a stale matcher is a silent green test) —
# including bare `FakeControlPlane()` instances a test built itself, not just the
# fixture's.
_FAULT_CPS: list = []


def _metering_period_label(period_start) -> str | None:
    """The DERIVED ``'YYYY-MM'`` label the SQL RPC writes for a window start
    (``to_char(period_start AT TIME ZONE 'UTC', 'YYYY-MM')``). #3825: a
    derived label, a pure function of the key — never the row key itself."""
    dt = _as_dt(period_start)
    if dt is None:
        return None
    return f"{dt.year}-{dt.month:02d}"


def _as_dt(value):
    """Parse a stored metering instant for the fake's SQL-semantics
    comparisons. ``None`` in → ``None`` out (a row with no window cannot match
    a windowed read). An epoch int is accepted because the registry lane stores
    whatever the Stripe webhook wrote, and a naive string is read as UTC — the
    fake emulates a ``timestamptz`` column and every caller in this repo writes
    UTC."""
    from datetime import datetime
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


class FakeControlPlane:
    def __init__(self, tables: dict[str, list[dict]] | None = None,
                 *, missing_columns: dict[str, set[str]] | None = None,
                 uuid_fidelity: bool = True):
        # rows are stored as dicts keyed by column name
        self.tables: dict[str, list[dict]] = tables or {}
        self.query_count = 0
        self.rpc_calls: list[tuple[str, dict]] = []
        # #1096 drift mode: columns that are absent from the "schema" of a
        # table (mirrors PostgREST 400 PGRST204 on select/filter of an
        # absent column). Default None → behavior identical to before.
        self.missing_columns: dict[str, set[str]] | None = missing_columns
        self.uuid_fidelity = uuid_fidelity
        # #1709: serializes recover_team_key emulation (the real RPC SELECTs
        # the token row FOR UPDATE — the fake must be atomic under the
        # concurrency E2E).
        import threading
        self._recover_lock = threading.Lock()
        # #1765: auth-side rows for user_identity_inventory/reserve_unlink
        # emulations (mirror auth.users + auth.identities shapes).
        self.auth_users: list[dict] = []
        self.auth_identities: list[dict] = []
        # #1765: serializes reserve_unlink check+insert (the real RPC's
        # partial unique index is the READ-COMMITTED backstop; the fake must
        # be atomic under the two-tab threading test).
        self._identity_lock = threading.Lock()
        # #4355: a conditional PATCH (the rotate CAS
        # ``UPDATE ... WHERE id = :id AND revoked_at IS NULL``) is ONE atomic
        # statement in Postgres — two concurrent statements serialize on the
        # row. The in-memory check-then-write below is NOT atomic under
        # threads (the `if _matches(...)` and the `r.update(...)` are separate
        # bytecodes a switch can land between), so two racing claims could
        # BOTH observe the row live and both report success — making a genuine
        # two-thread CAS test nondeterministic instead of red. Serialize the
        # write to model the statement the fake stands in for.
        self._patch_lock = threading.Lock()
        # #2863: fault injectors, consumed in order (see fail_query/_take_fault).
        self._faults: list[dict] = []

    def seed(self, table: str, rows: list[dict]) -> "FakeControlPlane":  # noqa: UP037
        self.tables.setdefault(table, []).extend(rows)
        return self

    def rpc_value(self, fn: str, body: dict | None = None):
        """Scalar-returning RPC — the read counterpart of :meth:`rpc`
        (``SupabaseControlPlane.rpc_value``). #3825: ``metering_cohort_spend``
        is reached through this method, so a test that asserts the aggregate
        over a WINDOW needs it on the double; the RPC emulation in :meth:`rpc`
        already RETURNS the scalar, so this delegates and records the call
        exactly once in ``rpc_calls``.
        """
        return self.rpc(fn, body)

    def _claim_migrate_created_by(self, org_id: str, user_id: str) -> None:
        """#1765: claim attributes anon-/reg- created_by keys in the team to
        the claimer (parenthesized predicate parity — team-scoped)."""
        for k in self.tables.get("api_keys", []):
            cb = str(k.get("created_by") or "")
            if k.get("org_id") == org_id and (cb.startswith("anon-") or cb.startswith("reg-")):
                k["created_by"] = str(user_id)

    def rpc(self, fn: str, body: dict | None = None, *,
            representation: bool = False) -> object | None:
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
            tid = (body or {}).get("p_org_id")
            for t in self.tables.get("organizations", []):
                if t.get("id") == tid and t.get("suspended_at") is None:
                    from datetime import datetime, timezone
                    t["suspended_at"] = datetime.now(timezone.utc).isoformat()  # noqa: UP017
            from datetime import datetime, timezone
            self.tables.setdefault("abuse_events", []).append(
                {"org_id": tid, "event_type": "suspend", "weight": 1,
                 "created_at": datetime.now(timezone.utc).isoformat()})  # noqa: UP017
            return None
        if fn == "abuse_unsuspend":
            tid = (body or {}).get("p_org_id")
            for t in self.tables.get("organizations", []):
                if t.get("id") == tid:
                    t["suspended_at"] = None
                    t["flagged_at"] = None
            from datetime import datetime, timezone
            now_iso = datetime.now(timezone.utc).isoformat()  # noqa: UP017
            events = self.tables.setdefault("abuse_events", [])
            events.append({"org_id": tid, "event_type": "unsuspend",
                           "weight": 1, "created_at": now_iso})
            # end every flag episode (mirrors the SQL)
            for rule in ("point_create", "key_create"):
                events.append({"org_id": tid, "event_type": "flag_clear",
                               "rule": rule, "weight": 1,
                               "created_at": now_iso})
            return None
        if fn == "metering_increment":
            # Emulate migration 0014's SQL function: atomic upsert + increment.
            p = body or {}
            rows = self.tables.setdefault("metering_records", [])
            row = next((r for r in rows if r["org_id"] == p.get("p_org_id")
                        and r.get("period_start") == p.get("p_period_start")),
                       None)
            n = int(p.get("p_n") or 1)
            if row:
                row["write_ops"] = row.get("write_ops", 0) + n
            else:
                rows.append({"org_id": p.get("p_org_id"),
                             "period_start": p.get("p_period_start"),
                             "period_end": p.get("p_period_end"),
                             "period": _metering_period_label(
                                 p.get("p_period_start")),
                             "write_ops": n})
            return None  # PostgREST minimal — no echo
        if fn == "metering_increment_ask":
            # #1987 Task 6 / #3825: the ask lane's additive upsert on the SAME
            # ``(org_id, period_start)`` row — the fake must model the shared
            # row, otherwise a test cannot tell a single-window ask+capture
            # pair from two month buckets (the defect #3825 removes).
            p = body or {}
            rows = self.tables.setdefault("metering_records", [])
            row = next((r for r in rows if r["org_id"] == p.get("p_org_id")
                        and r.get("period_start") == p.get("p_period_start")),
                       None)
            calls = int(p.get("p_calls") or 1)
            tin = int(p.get("p_tokens_in") or 0)
            tout = int(p.get("p_tokens_out") or 0)
            cost = float(p.get("p_cost_usd") or 0.0)
            if row:
                row["ask_calls"] = row.get("ask_calls", 0) + calls
                row["ask_tokens_in"] = row.get("ask_tokens_in", 0) + tin
                row["ask_tokens_out"] = row.get("ask_tokens_out", 0) + tout
                row["ask_cost_usd"] = (float(row.get("ask_cost_usd") or 0.0)
                                       + cost)
            else:
                rows.append({"org_id": p.get("p_org_id"),
                             "period_start": p.get("p_period_start"),
                             "period_end": p.get("p_period_end"),
                             "period": _metering_period_label(
                                 p.get("p_period_start")),
                             "ask_calls": calls, "ask_tokens_in": tin,
                             "ask_tokens_out": tout, "ask_cost_usd": cost})
            return None
        if fn == "metering_increment_capture_cost":
            # #3665: migration 20260917000001 — additive upsert mirroring
            # metering_increment_capture_cost (the capture lane's twin),
            # re-keyed onto the window start by 20260918000001 (#3825).
            p = body or {}
            rows = self.tables.setdefault("metering_records", [])
            row = next((r for r in rows if r["org_id"] == p.get("p_org_id")
                        and r.get("period_start") == p.get("p_period_start")),
                       None)
            calls = int(p.get("p_calls") or 0)
            cost = float(p.get("p_cost_usd") or 0.0)
            if row:
                row["capture_calls"] = row.get("capture_calls", 0) + calls
                row["capture_cost_usd"] = (
                    float(row.get("capture_cost_usd") or 0.0) + cost)
            else:
                rows.append({"org_id": p.get("p_org_id"),
                             "period_start": p.get("p_period_start"),
                             "period_end": p.get("p_period_end"),
                             "period": _metering_period_label(
                                 p.get("p_period_start")),
                             "capture_calls": calls,
                             "capture_cost_usd": cost})
            return None
        if fn == "metering_cohort_spend":
            # #3665/#3825: the SQL aggregate — one scalar, so no row cap can
            # truncate it (the reason it is an RPC and not a filtered read).
            # Mirrors the SQL's HALF-OPEN OVERLAP test
            # (``period_start < p_period_end AND period_end > p_period_start``),
            # not a ``period = p_period`` month equality.
            p = body or {}
            wanted = {str(i) for i in (p.get("p_org_ids") or [])}
            start = _as_dt(p.get("p_period_start"))
            end = _as_dt(p.get("p_period_end"))
            total = 0.0
            for r in self.tables.get("metering_records", []):
                if str(r.get("org_id")) not in wanted:
                    continue
                r_start = _as_dt(r.get("period_start"))
                r_end = _as_dt(r.get("period_end"))
                if r_start is None or r_end is None:
                    continue  # no window → not addressable by a window read
                if r_start < end and r_end > start:
                    total += float(r.get("ask_cost_usd") or 0.0)
                    total += float(r.get("capture_cost_usd") or 0.0)
            return total
        if fn == "cohort_org_ids_since":
            # #3665: array_agg over a bounded subquery — one row/one array,
            # so a row cap cannot truncate the org set. Mirror the SQL's
            # ``ORDER BY created_at, id LIMIT p_limit + 1``.
            p = body or {}
            since = str(p.get("p_since") or "")
            limit = int(p.get("p_limit") or 0)
            rows = [t for t in self.tables.get("organizations", [])
                    if t.get("id") and str(t.get("created_at") or "") > since]
            rows.sort(key=lambda t: (str(t.get("created_at") or ""),
                                     str(t["id"])))
            return [str(t["id"]) for t in rows[:limit + 1]]
        if fn == "claim_membership":
            # Emulate migration 20260813000004's SQL semantics over the
            # in-memory rows (mirrors the real SECURITY DEFINER function):
            #  1. resolve team from api_keys (lookup_hash + revoked_at IS
            #     NULL; REJECT created_via='bootstrap' session keys and
            #     expired keys)
            #  2. idempotent re-claim: owner (org_id, user_id) → noop
            #  3. find the NULL-user_id active owner row → 409 already_claimed
            #     when absent
            #  4. merge/promote when a (user_id, org_id) row exists (drop
            #     identity row first, copy key material, reactivate) else
            #     plain link (user_id set, identity NULL)
            #  5. teams.email overwrite (unconditional); cross-team collision
            #     → email_in_use (uq_teams_email parity)
            #  6. drop leftover placeholder (org_id='')
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
                    and exp <= datetime.now(timezone.utc).isoformat():  # noqa: UP017
                raise RuntimeError("claim_membership:key_expired")
            org_id = key["org_id"]
            mem_rows = self.tables.setdefault("org_memberships", [])

            # idempotent re-claim (noop success)
            if any(m.get("org_id") == org_id and m.get("user_id") == user_id
                   and m.get("role") == "owner" and m.get("status") == "active"
                   for m in mem_rows):
                self._claim_migrate_created_by(org_id, user_id)
                return None

            owner = next((m for m in mem_rows
                          if m.get("org_id") == org_id
                          and m.get("role") == "owner"
                          and m.get("user_id") is None
                          and m.get("status") == "active"), None)
            if owner is None:
                raise RuntimeError("claim_membership:already_claimed")

            existing = next((m for m in mem_rows
                             if m.get("user_id") == user_id
                             and m.get("org_id") == org_id), None)
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

            # drop leftover placeholder (org_id='') for the user
            mem_rows[:] = [m for m in mem_rows
                           if not (m.get("user_id") == user_id
                                   and m.get("org_id") == "")]
            # #1765: created_by migration — anon-/reg- keys in the claiming
            # team are attributed to the claimer (teams.email is NEVER
            # written by claim — demotion, migration 20260827000001).
            self._claim_migrate_created_by(org_id, user_id)
            return None
        if fn == "user_identity_inventory":
            # #1765: mirror migration 20260827000001's SECURITY DEFINER RPC —
            # login_methods = oauth_count + email_method where
            # email_method := (email AND confirmed) OR has_password and
            # has_password := encrypted_password IS NOT NULL AND <> ''.
            p = body or {}
            uid = p.get("p_user_id")
            user = next((u for u in self.auth_users if u.get("id") == uid), None)
            ids = [i for i in self.auth_identities if i.get("user_id") == uid]
            if user is None and not ids:
                return {"methods": [], "has_password": False,
                        "email_method": False, "login_methods": 0,
                        "keys_tier": 0, "banner": {"show": False}}
            enc = (user or {}).get("encrypted_password")
            has_pwd = enc is not None and enc != ""
            email = (user or {}).get("email")
            confirmed = (user or {}).get("email_confirmed_at") is not None
            email_method = bool(email and confirmed) or has_pwd
            oauth = [i for i in ids if i.get("provider") not in ("email",)]
            login = len(oauth) + (1 if email_method else 0)
            keys_tier = sum(1 for k in self.tables.get("api_keys", [])
                            if k.get("created_by") == str(uid)
                            and k.get("revoked_at") is None
                            and k.get("enabled", True))
            return {
                "methods": [
                    {"id": i.get("id"), "provider": i["provider"],
                     "provider_id": i["provider_id"],
                     "email_confirmed_at": (user or {}).get("email_confirmed_at")}
                    for i in ids],
                "has_password": has_pwd, "email_method": email_method,
                "login_methods": login, "keys_tier": keys_tier,
                "banner": {"show": login <= 1
                           and not (email and confirmed and has_pwd)}}
        if fn == "reserve_unlink":
            # #1765: mirror reserve_unlink — TTL aging, ownership, floor
            # (login_methods - pending - 1 >= 2), and the one-pending-permit
            # invariant (partial unique index parity) with the SAME error
            # strings as the real RPC (test_unlink_two_tab depends on it).
            # The check+insert is ATOMIC under self._identity_lock (the real
            # backstop is the DB unique index; the fake must not double-grant).
            with self._identity_lock:
                p = body or {}
                uid = p.get("p_user_id")
                iid = p.get("p_identity_id")
                from datetime import datetime, timedelta, timezone
                now = datetime.now(UTC)
                permits = self.tables.setdefault("user_unlink_permits", [])
                for perm in permits:
                    if perm.get("user_id") == uid and perm.get("consumed_at") is None:
                        created = perm.get("created_at")
                        if created and created <= (now - timedelta(minutes=5)).isoformat():
                            perm["consumed_at"] = now.isoformat()
                if not any(i.get("id") == iid and i.get("user_id") == uid
                           for i in self.auth_identities):
                    raise RuntimeError("reserve_unlink:identity_not_found")
                inv = self.rpc("user_identity_inventory", {"p_user_id": uid})
                login = int(inv["login_methods"])
                pending = sum(1 for p2 in permits
                              if p2.get("user_id") == uid and p2.get("consumed_at") is None)
                if login - pending - 1 < 2:
                    raise RuntimeError("reserve_unlink:floor_violated")
                if any(p2.get("user_id") == uid and p2.get("consumed_at") is None
                       for p2 in permits):
                    raise RuntimeError("reserve_unlink:floor_violated")
                permits.append({"id": str(uuid.uuid4()), "user_id": uid,
                                "identity_id": iid, "consumed_at": None,
                                "created_at": now.isoformat()})
                return {"status": "permit_granted", "identity_id": iid}
        if fn == "provision_team_with_token":
            # #1709 (20260814000001): the signup wrapper = provision_team + one
            # token row in the SAME emulated transaction. Unique-constraint
            # parity is checked BEFORE the provision (the real SQL rolls the
            # whole tx back on uq_agent_signup_tokens_team).
            p = body or {}
            th = p.get("p_signup_token_hash")
            tid = p.get("p_org_id") or ""
            if th:
                tokens = self.tables.setdefault("agent_signup_tokens", [])
                live = [t for t in tokens if t.get("org_id") == tid
                        and t.get("revoked_at") is None]
                if live:
                    raise RuntimeError(
                        "agent_signup_tokens: uq_agent_signup_tokens_team")
            self._provision_team_emulation(p)
            if th and not any(
                    t.get("token_hash") == th
                    for t in self.tables.get("agent_signup_tokens", [])):
                # ON CONFLICT (token_hash) DO NOTHING parity
                self.tables.setdefault("agent_signup_tokens", []).append({
                    "token_hash": th, "org_id": tid, "created_at": None,
                    "last_used_at": None, "revoked_at": None})
            return None
        if fn == "resolve_signup_token":
            # #1709: token → org_id (revocation-aware); NULL = unknown/revoked.
            from datetime import datetime, timezone
            p = body or {}
            th = p.get("p_token_hash") or ""
            tokens = self.tables.get("agent_signup_tokens", [])
            row = next((t for t in tokens
                        if t.get("token_hash") == th
                        and t.get("revoked_at") is None), None)
            if row is not None:
                row["last_used_at"] = datetime.now(timezone.utc).isoformat()  # noqa: UP017
                return row.get("org_id")
            return None
        if fn == "recover_team_key":
            # #1709: keyless recovery mint — atomic cap-check + insert under a
            # lock (emulates the RPC's FOR UPDATE row serialization so the
            # concurrency E2E can assert the cap never overshoots).
            from datetime import datetime, timezone
            p = body or {}
            th = p.get("p_token_hash") or ""
            tid = p.get("p_org_id") or ""
            lookup = p.get("p_lookup_hash") or ""
            with self._recover_lock:
                tokens = self.tables.setdefault("agent_signup_tokens", [])
                row = next((t for t in tokens
                            if t.get("token_hash") == th
                            and t.get("revoked_at") is None
                            and t.get("org_id") == tid), None)
                if row is None:
                    raise RuntimeError(
                        "recover_team_key: token not found or revoked")
                if any(t.get("id") == tid and t.get("deleted_at")
                       for t in self.tables.get("organizations", [])):
                    raise RuntimeError("recover_team_key: team deleted")
                cap = int(p.get("p_max_api_keys") or 2)
                key_rows = self.tables.setdefault("api_keys", [])
                now_iso = datetime.now(timezone.utc).isoformat()  # noqa: UP017
                if not any(k.get("lookup_hash") == lookup for k in key_rows):
                    # parity with the SQL reorder (review P2.8): the cap
                    # revoke fires ONLY when a key was genuinely inserted —
                    # a no-op retry with the same lookup_hash must never
                    # revoke a live key. (Count-before-insert with >= cap is
                    # outcome-equivalent to the SQL's count-after-insert with
                    # > cap.)
                    active = [k for k in key_rows
                              if k.get("org_id") == tid
                              and k.get("revoked_at") is None
                              and k.get("created_via") != "bootstrap"]
                    if len(active) >= cap:
                        oldest = min(active, key=lambda k: k.get("created_at") or "")
                        oldest["revoked_at"] = now_iso
                    key_rows.append({
                        "id": f"key_{tid}_{lookup[:12]}",
                        "org_id": tid,
                        "lookup_hash": lookup,
                        "key_prefix": p.get("p_key_prefix") or tid[:8],
                        "created_via": "recovery",
                        "created_by": "st_" + th[:12],
                        # #1754 (c): the real RPC's created_at defaults to
                        # now() — a None here sorts as the OLDEST key in the
                        # cap-revoke targeting (min by created_at) and would
                        # be the cap-revoke target, masking revoke-oldest
                        # regressions in the Supabase lane.
                        "created_at": now_iso,
                        "expires_at": None,
                        "revoked_at": None,
                    })
                    self._trigger_key_create(tid, f"key_{tid}_{lookup[:12]}",
                                             "recovery")
                return tid
        if fn == "revoke_signup_token":
            # #1715 (20260826000001): user-facing revocation — team-scoped +
            # idempotent (UPDATE ... WHERE token_hash AND org_id AND
            # revoked_at IS NULL); unknown/other-team/already-revoked is a
            # zero-row no-op, never an error.
            from datetime import datetime, timezone
            p = body or {}
            th = p.get("p_token_hash") or ""
            tid = p.get("p_org_id") or ""
            row = next((t for t in self.tables.get("agent_signup_tokens", [])
                        if t.get("token_hash") == th
                        and t.get("org_id") == tid), None)
            if row is not None and row.get("revoked_at") is None:
                row["revoked_at"] = datetime.now(timezone.utc).isoformat()  # noqa: UP017
            return None
        if fn == "provision_team":
            # #1765: uq_member_identity_active parity — an UNCLAIMED owner row
            # with the same identity must reject the second (the register race
            # backstop). Mirrors the 20260827000001 partial unique index.
            p = body or {}
            identity = p.get("p_identity")
            uid = p.get("p_user_id")
            if uid is None and identity:
                with self._identity_lock:  # atomic check+insert (register race)
                    mem = self.tables.setdefault("org_memberships", [])
                    # same (identity, org_id) row is an UPSERT (in-place);
                    # only a DIFFERENT team with the same unclaimed owner
                    # identity hits uq_member_identity_active
                    org_id = p.get("p_org_id")
                    if any(m.get("identity") == identity
                           and m.get("role") == "owner"
                           and m.get("status") == "active"
                           and m.get("user_id") is None
                           and m.get("org_id") != org_id
                           for m in mem):
                        raise RuntimeError(
                            "duplicate key value violates unique constraint "
                            "\"uq_member_identity_active\"")
        if fn != "provision_team":
            return None
        return self._provision_team_emulation(body or {})

    def _provision_team_emulation(self, p: dict) -> None:
        """The provision_team RPC body (migration 0010) over the in-memory
        rows — shared by the plain RPC and the #1709 wrapper."""
        org_id = p.get("p_org_id") or ""
        org_name = p.get("p_org_name") or ""
        api_key = p.get("p_api_key") or ""
        lookup = p.get("p_lookup_hash")
        user_id = p.get("p_user_id")
        identity = p.get("p_identity")
        if not org_id or not org_name:
            raise RuntimeError("provision_team: required parameters missing")
        # #1716 all-or-none key guard (mirrors the RPC's SQL IS NULL
        # semantics EXACTLY — Python truthiness would diverge on "" values:
        # falsy-but-NOT-NULL): the three key params are ALL provided (a
        # minted key) or ALL None (keyless — the onboarding sub-team path) —
        # never a partial set.
        key_params = [p.get("p_api_key"), p.get("p_key_hash"),
                      p.get("p_lookup_hash")]
        n_present = sum(v is not None for v in key_params)
        if n_present not in (0, 3):
            raise RuntimeError(
                "provision_team: p_api_key/p_key_hash/p_lookup_hash must be "
                "all provided or all NULL (keyless)")
        if (user_id is None) == (identity is None):
            raise RuntimeError(
                "provision_team: exactly one of p_user_id / p_identity is required")

        # teams upsert on id (exactly one row)
        team_rows = self.tables.setdefault("organizations", [])
        team = next((t for t in team_rows if t.get("id") == org_id), None)
        # #2789: unique-name parity (migration 0011 `uq_teams_name`). The real
        # RPC upserts ON CONFLICT (id) ONLY, so a name held by a DIFFERENT team
        # raises a unique violation → PostgREST 409. Without this the fake made
        # a real production failure (stranding a paying customer) invisible to
        # tests; both create lanes key on the "HTTP 409" marker.
        if team is None and any(
                t.get("name") == org_name and t.get("id") != org_id
                for t in team_rows):
            raise RuntimeError(
                'HTTP 409: duplicate key value violates unique constraint '
                '"uq_teams_name"')
        if team is None:
            team = {"id": org_id, "name": org_name, "tier": p.get("p_tier", "free"),
                    "graph_name": p.get("p_graph_name", f"org_{org_id}"),
                    "max_users": p.get("p_max_users", 1),
                    "max_graphs": p.get("p_max_graphs", 1),
                    "ops_allowance": p.get("p_ops_allowance", 10000),
                    "graph_size_cap": p.get("p_graph_size_cap", 10000),
                    # #1859 P3-2: points-cap override column (migration
                    # 20260817000001, nullable — NULL = use graph_size_cap).
                    "max_points": None,
                    # #1148: dashboard key-login acceptance (migration default true)
                    "dashboard_key_login": True}
            if p.get("p_email"):
                team["email"] = p["p_email"]
            team_rows.append(team)
        else:
            team["name"] = org_name
            if p.get("p_email"):
                team["email"] = p["p_email"]

        # membership: refresh in place (user_id,org_id) or (identity,org_id),
        # else insert exactly one row
        mem_rows = self.tables.setdefault("org_memberships", [])
        if user_id is not None:
            mem = next((m for m in mem_rows
                        if m.get("user_id") == user_id and m.get("org_id") == org_id),
                       None)
            anchor = {"user_id": user_id}
        else:
            mem = next((m for m in mem_rows
                        if m.get("identity") == identity and m.get("org_id") == org_id),
                       None)
            anchor = {"user_id": None, "identity": identity}
        if mem is None:
            mem = {"id": uuid.uuid4().hex[:26], "org_id": org_id,
                   "org_name": org_name, "api_key": api_key,
                   "key_hash": p.get("p_key_hash") or "",
                   "lookup_hash": lookup,
                   "graph_name": p.get("p_graph_name", f"org_{org_id}"),
                   "role": "owner", "status": "active",
                   "created_at": None}
            mem.update(anchor)
            mem_rows.append(mem)
        else:
            mem.update({"org_name": org_name, "api_key": api_key,
                        "key_hash": p.get("p_key_hash") or "",
                        "lookup_hash": lookup, "role": "owner",
                        "status": "active"})

        # api_keys: deterministic id, ON CONFLICT (lookup_hash) DO NOTHING
        # (a re-provision inserts nothing → the 0015 trigger fires nothing —
        # no duplicate key_create event; cycle-2 test note). #1716: keyless
        # provision (all-NULL key params) writes NO api_keys row — the team
        # stays keyless until a session-key mint (mirrors the RPC's
        # conditional insert; no api_keys row → no 0015 key_create event).
        if lookup is not None:
            key_rows = self.tables.setdefault("api_keys", [])
            if not any(k.get("lookup_hash") == lookup for k in key_rows):
                key_rows.append({
                    "id": f"key_{org_id}_{lookup[:12]}",
                    "org_id": org_id,
                    "lookup_hash": lookup,
                    "key_prefix": p.get("p_key_prefix") or org_id[:8],
                    "created_via": "provisioned",
                    "created_by": str(user_id) if user_id is not None else identity,
                    "created_at": None,
                    "expires_at": None,
                    "revoked_at": None,
                })
                self._trigger_key_create(org_id, f"key_{org_id}_{lookup[:12]}",
                                         "provisioned")
        return None


    def _trigger_key_create(self, org_id: str, key_id: str,
                            created_via: str | None) -> None:
        """Migration 0015 trigger emulation: AFTER INSERT on api_keys →
        key_create abuse event, EXCLUDING created_via='bootstrap'."""
        if created_via == "bootstrap":
            return
        from datetime import datetime, timezone
        self.tables.setdefault("abuse_events", []).append(
            {"org_id": org_id, "event_type": "key_create", "weight": 1,
             "key_id": key_id,
             "created_at": datetime.now(timezone.utc).isoformat()})  # noqa: UP017

    def fail_query(self, *, table=None, method=None, select=None, filters=None,
                   match=None, times: int = 1, after_mutation: bool = False,
                   exc: Exception | None = None) -> None:
        """Install a fault injector (#2863). The first unexhausted injector whose
        predicate accepts the call fires. Provide either the simple attribute matches
        or (preferred) ``match=fn(table, method, select, filters) -> bool`` — a callable
        does NOT go stale when a production select list changes (two review cycles were
        lost to hand-written select literals that never matched). ``after_mutation=True``
        applies the write THEN raises — the commit-then-lost-response case a plain
        raise-on-N cannot express."""
        self._faults.append({
            "table": table, "method": method, "select": select, "filters": filters,
            "match": match, "consumed": False,
            "times": times, "after_mutation": after_mutation,
            "exc": exc or RuntimeError("Supabase unreachable (simulated)"),
        })
        _FAULT_CPS.append(self)          # module-level registry: the guard's only view
                                         # of a bare `FakeControlPlane()` built by a test

    def unfired_faults(self) -> list[dict]:
        """Injectors that did not do what the test intended — either never fired, or
        fired fewer times than `times` asked for. A test MUST fail on these; a stale
        matcher is otherwise a silent green test."""
        return [f for f in self._faults if not f["consumed"] or f["times"] > 0]

    def _take_fault(self, table, method, select, filters):
        for fault in self._faults:
            if fault["times"] <= 0:
                continue
            # The table filter applies INDEPENDENTLY of `match` — otherwise a
            # shape-scoped injector would also fire on the other token table
            # (the rollback and lane 3 are both select-less PATCHes).
            if fault["table"] is not None and fault["table"] != table:
                continue
            if (fault["match"] is not None
                    and not fault["match"](table, method, select, filters)):
                continue
            if fault["method"] is not None and fault["method"] != method:
                continue
            if fault["select"] is not None and list(fault["select"]) != list(select or []):
                continue
            if fault["filters"] is not None and list(fault["filters"]) != list(filters or []):
                continue
            fault["times"] -= 1
            fault["consumed"] = True
            return fault
        return None

    def query(self, table: str, *, select: list[str] | None = None,
              filters: list[tuple[str, str, object]] | None = None,
              method: str = "GET", json_body: dict | None = None,
              order: str | None = None, limit: int | None = None,
              timeout: object | None = None) -> list[dict]:
        # ``timeout`` mirrors the real SupabaseControlPlane per-request
        # override (#2850/#2988): the health probe passes a composed
        # httpx.Timeout here. The fake performs no I/O, so it is accepted and
        # ignored — but it MUST be accepted, or the probe's query would raise
        # TypeError and /health/ready would 503 in tests.
        _ = timeout
        fault = self._take_fault(table, method, select, filters)
        if fault is not None and not fault["after_mutation"]:
            raise fault["exc"]
        result = self._query_impl(table, select=select, filters=filters, method=method,
                                  json_body=json_body, order=order, limit=limit)
        if fault is not None:
            raise fault["exc"]
        return result

    def _query_impl(self, table: str, *, select: list[str] | None = None,
                    filters: list[tuple[str, str, object]] | None = None,
                    method: str = "GET", json_body: dict | None = None,
                    order: str | None = None, limit: int | None = None) -> list[dict]:
        self.query_count += 1
        # #1719 (Task 3): fidelity check BEFORE method dispatch — GET builds
        # filters in the loop below, but PATCH/DELETE flow through _matches;
        # 22P02 in prod is method-agnostic.
        if self.uuid_fidelity:
            _assert_uuid_fidelity(table, filters)
        # #4037: an order term PostgREST would 400 on (PGRST100) must fail here
        # too — and, like 22P02, that is method-agnostic → parse BEFORE the
        # method dispatch below. `if order:` mirrors the real seam, which drops
        # a falsy order (`supabase_control.query`: `if order:`), so `""`/None
        # are NOT false refusals.
        order_terms = _parse_order(table, order) if order else []
        if method == "PATCH":
            # mutate the STORED rows (mirrors PostgREST update semantics);
            # return=representation when a select is given → the UPDATED
            # rows ([] when nothing matched), the atomic-claim path used by
            # OAuth single-use codes / rotation (PR #1264 review P2, and the
            # #4355 rotate claim). `_patch_lock` makes the check-then-write
            # atomic the way the real single UPDATE statement is (see
            # __init__) — required for the #4355 two-thread CAS test.
            updated: list[dict] = []
            with self._patch_lock:
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
                row["created_at"] = datetime.now(timezone.utc).isoformat()  # noqa: UP017
            if table == "oauth_codes" and row.get("id") is None:
                # mirror `id bigint GENERATED ALWAYS AS IDENTITY` (0016). #3027
                # records a redemption outcome BY id and links minted tokens
                # with `code_id`, so a fake row with no id would make the
                # durable path silently unwritable in tests. Derived from the
                # stored rows (not a counter) so it also cannot collide with a
                # row a test seeded by hand.
                numeric = [r.get("id") for r in self.tables.get(table, [])
                           if isinstance(r.get("id"), int)]
                row["id"] = (max(numeric) + 1) if numeric else 1
            self.tables.setdefault(table, []).append(row)
            if table == "api_keys":
                # migration 0015 trigger emulation (#308)
                self._trigger_key_create(row.get("org_id", ""),
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
                # SQL semantics: `col <> value` is NULL (not TRUE) when either
                # side is NULL, so a NULL column (or a NULL comparison value)
                # never matches. Python's bare `r.get(col) != value` would
                # KEEP the NULL row — a dialect divergence that would hide an
                # over-exemption regression (e.g. a `created_via=neq.bootstrap`
                # filter silently exempting legacy NULL rows — #4140 T4).
                rows = ([] if value is None else
                        [r for r in rows
                         if r.get(col) is not None and r.get(col) != value])
            elif op == "is":
                rows = [r for r in rows if (r.get(col) is None) == (value is None)]
            elif op == "gt":
                # SQL semantics: NULL never matches an ordered comparison
                rows = [r for r in rows
                        if r.get(col) is not None and r.get(col) > value]
            elif op == "gte":
                rows = [r for r in rows
                        if r.get(col) is not None and r.get(col) >= value]
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
            if (self.missing_columns and table in self.missing_columns
                    and order_terms
                    and self.missing_columns[table]
                    & {f for f, _, _ in order_terms}):
                # Ordering by an absent column is the SAME PostgREST rejection as
                # the `select`/`filter` drift above: real PostgREST 400s on an
                # undefined column (PGRST204) rather than returning the rows
                # unordered. Left accepted, it reintroduces the exact #4037 mask
                # — an invalid order term that 400s in production but is masked
                # in CI, with a fail-soft consumer reading `rows[0]` after
                # `limit=1`. The user-facing outcome is identical, so the fake
                # must not be the one place it stays invisible.
                raise RuntimeError(
                    f"Supabase control-plane query failed ({table}): HTTP 400")
            # #4037: order BEFORE the projection — PostgREST orders server-side
            # before projecting, so an ordered column need not be in `select`
            # (the old fake sorted after the projection, silently no-oping any
            # order on a non-selected column, e.g. `active_membership_org_ids`
            # `select=["org_id"], order="created_at.asc"`).
            if order_terms:
                rows = _apply_order(rows, order_terms)
            if select:
                rows = [{k: r.get(k) for k in select} for r in rows]
            if limit is not None:
                rows = rows[:limit]
            return rows
        raise ValueError(f"unsupported method {method!r}")


def _matches(row: dict, filters: list[tuple[str, str, object]]) -> bool:
    for col, op, value in filters:
        if op == "eq" and row.get(col) != value:
            return False
        if op == "neq" and (value is None or row.get(col) is None
                            or row.get(col) == value):
            return False
        if op == "is" and (row.get(col) is None) != (value is None):
            return False
        if op == "gt" and (row.get(col) is None or row.get(col) <= value):
            return False
        if op == "gte" and (row.get(col) is None or row.get(col) < value):
            return False
        if op == "lt" and (row.get(col) is None or row.get(col) >= value):
            return False
        if op == "lte" and (row.get(col) is None or row.get(col) > value):
            # ISO-8601 cutoff (mirrors the GET path — #302 purge).
            return False
        if op not in ("eq", "neq", "is", "gt", "gte", "lt", "lte"):
            # #3665 review: an op this helper does not implement must RAISE,
            # not silently no-op. Silently ignoring an op makes PATCH/DELETE
            # match on the remaining filters — i.e. the fake mutates MORE rows
            # than the real client would, and a test can pass against
            # behaviour production does not have. The GET path above already
            # raises for an unsupported op; this mirrors it.
            raise ValueError(f"unsupported filter op {op!r}")
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

    def rpc(self, fn: str, body: dict | None = None, *,
            representation: bool = False) -> object | None:
        raise self._exc
