"""Supabase control-plane client for hosted auth resolution (#669 plan Task 3).

The FalkorDB ``control_plane`` registry is being retired in favor of Supabase
(teams / team_memberships / api_keys, migrations 0006–0009). This module is
the read-side seam the hosted auth paths use AFTER the flip:

- API-key resolution (REST ``get_current_team`` + MCP
  ``TeamResolutionMiddleware``) hashes the presented ``tt_`` token with
  ``tortoise.auth.lookup_hash`` (SHA-256(pepper + key), plan P1-1) and does an
  exact-match indexed lookup — O(1), no registry scan.
- ``api_keys.revoked_at`` is the authoritative revocation source (plan P1-2):
  a revoked ``api_keys`` twin REJECTS even when the matching
  ``team_memberships`` row is active.
- Tier/quota come from the ``teams`` row (max_users/max_graphs/
  graph_size_cap); fields the 0006 schema does not store (max_points →
  graph_size_cap, max_api_keys/max_sessions) fall back to
  ``tortoise.pricing.tier_limits`` defaults, mirroring the registry path.
- Invitations (plan Task 4): mint/accept/rescind live here too — pending
  invitations are redeemed by plaintext token via indexed lookup_hash
  (SHA-256(pepper + token)), accept creates the real team_memberships row
  with the INVITED role, and dedup (team, email) + 7-day expiry are
  enforced (0008 partial unique index).

Fail-closed contract (backup-seam P1-3 pattern): every query error raises
``RuntimeError`` — auth never falls back to the registry and never
authenticates on error. ``update_last_used`` is the one best-effort exception
(#685 telemetry write-through must never gate auth).

Env gating (plan Task 8 names the variable): ``TORTOISE_CONTROL_PLANE``
accepts ``supabase|registry``. When unset, Supabase resolution is the default
WHEN ``SUPABASE_URL`` + a service-role key are configured; selfhost stays
registry-backed (no Supabase creds → registry). The SDK registry code paths
in ``tortoise/sdk.py`` are untouched — this flip only covers the HOSTED auth
paths (hosted_api.py + mcp_auth.py).

Transport: plain HTTP against PostgREST via httpx (already a dependency) —
no new deps, matching the analytics-write pattern in hosted_api.py.

Query dialect: ``query(table, select, filters, method, json_body, order,
limit)`` where filters are ``(column, op, value)`` tuples with ops
``eq | neq | is`` (None → ``IS NULL``). The test fake implements the SAME
interface over in-memory rows, so the resolution logic is shared verbatim
between CI and production.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

_logger = logging.getLogger(__name__)

# Env-var names: SUPABASE_SERVICE_ROLE_KEY is the canonical name (edge
# functions, supabase/README.md); SUPABASE_SERVICE_KEY is the legacy name the
# analytics write path uses — accept either so the flip works with both.
_SERVICE_KEY_ENV = ("SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_SERVICE_KEY")

_QUOTA_SELECT = [
    "id", "name", "tier", "max_users", "max_graphs", "graph_size_cap",
    "ops_allowance",
]


def _service_key() -> str:
    for name in _SERVICE_KEY_ENV:
        val = os.environ.get(name)
        if val:
            return val
    return ""


def is_supabase_enabled() -> bool:
    """True when hosted auth should resolve from Supabase.

    ``TORTOISE_CONTROL_PLANE=registry`` forces the registry (selfhost).
    ``=supabase`` (or unset with creds configured) uses Supabase. Unset with
    no creds → registry (selfhost default, plan Task 8 env split).

    FAIL-CLOSED on explicit misconfiguration: ``TORTOISE_CONTROL_PLANE=
    supabase`` with missing creds returns True so ``get_control_plane()``
    raises → REST 500 / MCP 503 (code-review P2, PR #851). A deployment that
    opted into Supabase-only must NEVER silently authenticate via the
    registry — that would let registry-only keys pass in a Supabase-only
    deployment.
    """
    mode = os.environ.get("TORTOISE_CONTROL_PLANE", "").strip().lower()
    if mode == "registry":
        return False
    configured = bool(os.environ.get("SUPABASE_URL")) and bool(_service_key())
    if mode == "supabase":
        return True  # get_control_plane() raises when creds are missing
    return configured


class SupabaseControlPlane:
    """PostgREST client for control-plane reads/writes (service role).

    Fail-closed: non-2xx responses and transport errors raise RuntimeError,
    so callers can never mistake an outage for "key not found" (a 401) or
    silently degrade to the registry.
    """

    def __init__(self, url: str | None = None, service_key: str | None = None,
                 timeout: float = 5.0):
        self._url = (url or os.environ.get("SUPABASE_URL", "")).rstrip("/")
        self._key = service_key or _service_key()
        self._timeout = timeout
        if not self._url or not self._key:
            raise RuntimeError(
                "Supabase control plane not configured — SUPABASE_URL and a "
                "service-role key (SUPABASE_SERVICE_ROLE_KEY/SUPABASE_SERVICE_KEY) "
                "are required"
            )
        # Persistent client — one connection pool per control-plane instance
        # (created on first use via get_control_plane). Per-request clients
        # would pay a fresh TCP+TLS handshake on EVERY auth resolution
        # (resolve_api_key makes 2-3 queries per request; code-review P2,
        # PR #851). httpx.Client is safe for concurrent use.
        import httpx
        self._http = httpx.Client(timeout=self._timeout)

    def query(self, table: str, *, select: list[str] | None = None,
              filters: list[tuple[str, str, object]] | None = None,
              method: str = "GET", json_body: dict | None = None,
              order: str | None = None, limit: int | None = None) -> list[dict]:
        """Run one PostgREST call. Returns row dicts; [] for PATCH/no rows.

        Filters: (column, op, value) with ops ``eq``, ``neq``, ``is``
        (value None → ``col=is.null``). Raises RuntimeError on any failure.
        """
        url = f"{self._url}/rest/v1/{table}"
        params: dict[str, str] = {}
        if select:
            params["select"] = ",".join(select)
        for col, op, value in filters or []:
            if op == "is":
                params[col] = "is.null" if value is None else f"is.{value}"
            elif op == "eq":
                params[col] = f"eq.{value}"
            elif op == "neq":
                params[col] = f"neq.{value}"
            else:
                raise ValueError(f"unsupported filter op {op!r}")
        if order:
            params["order"] = order
        if limit is not None:
            params["limit"] = str(limit)

        headers = {
            "apikey": self._key,
            "Authorization": f"Bearer {self._key}",
        }
        try:
            import httpx
            # NOTE: do NOT use `with self._http as client:` here — for a
            # client constructed OUTSIDE the context manager, __exit__ CLOSES
            # it (httpx 0.28.1: "Cannot reopen a client instance, once it has
            # been closed"). resolve_api_key makes 2-3 queries per request;
            # the second would raise and kill every auth resolution. Direct
            # method calls on the persistent client are the documented pattern
            # (re-review P0, PR #851).
            if method == "GET":
                resp = self._http.get(url, params=params, headers=headers)
            elif method == "PATCH":
                headers["Content-Type"] = "application/json"
                headers["Prefer"] = "return=minimal"
                resp = self._http.patch(url, params=params, headers=headers,
                                        json=json_body or {})
            elif method == "POST":
                headers["Content-Type"] = "application/json"
                headers["Prefer"] = "return=representation"
                resp = self._http.post(url, params=params, headers=headers,
                                       json=json_body or {})
            else:
                raise ValueError(f"unsupported method {method!r}")
        except RuntimeError:
            raise
        except Exception as e:  # transport errors — fail closed
            raise RuntimeError(
                f"Supabase control-plane query failed ({table}): {e}"
            ) from e
        if resp.status_code >= 300:
            raise RuntimeError(
                f"Supabase control-plane query failed ({table}): "
                f"HTTP {resp.status_code}"
            )
        if method == "PATCH" or not resp.content:
            return []
        try:
            data = resp.json()
        except Exception as e:
            raise RuntimeError(
                f"Supabase control-plane bad response ({table}): {e}"
            ) from e
        return data if isinstance(data, list) else [data]


# ── Singleton ──────────────────────────────────────────────────────────────

_control_plane: SupabaseControlPlane | None = None


def get_control_plane() -> SupabaseControlPlane:
    """Lazy process-wide control-plane client (tests monkeypatch this)."""
    global _control_plane
    if _control_plane is None:
        _control_plane = SupabaseControlPlane()
    return _control_plane


# ── Shared resolution logic (REST + MCP single source of truth) ────────────

def _parse_ts(value) -> datetime | None:
    """Parse a PostgREST/registry timestamp into an aware datetime."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def resolve_api_key(cp, token: str) -> dict | None:
    """Resolve a ``tt_`` token against Supabase. None = not found/rejected.

    Order (plan P1-1/P1-2):
    1. ``api_keys`` by lookup_hash (unique index — O(1)). Reject when
       ``revoked_at`` is set (AUTHORITATIVE — a revoked twin rejects even
       when the team_memberships row is active) or ``expires_at`` passed
       (#742 semantics).
    2. ``team_memberships`` by lookup_hash AND status='active' (long-lived
       keys; bootstrap/recovery session keys only exist in api_keys).
    3. tier/quota from the ``teams`` row.

    Fail-closed: a control-plane error raises (RuntimeError) — it never
    returns None (401) and never falls back to the registry.

    Returns the same dict shape as the registry get_current_team path
    (team_id, key_id, tier, max_users, max_graphs, max_points, max_api_keys,
    max_sessions) plus additive metadata (key_prefix/created_via/created_by).
    """
    from tortoise.auth import lookup_hash
    from tortoise.quota import DEFAULT_MAX_SESSIONS

    now = datetime.now(timezone.utc)
    h = lookup_hash(token)
    team_id = key_id = created_via = created_by = key_prefix = None

    rows = cp.query(
        "api_keys",
        select=["id", "team_id", "key_prefix", "created_via", "created_by",
                "expires_at", "revoked_at"],
        filters=[("lookup_hash", "eq", h)],
    )
    if rows:
        row = rows[0]
        if row.get("revoked_at") is not None:
            # P1-2: api_keys.revoked_at is the revocation source of truth.
            return None
        expires_at = _parse_ts(row.get("expires_at"))
        if expires_at is not None and expires_at <= now:
            # #742: expired keys must NOT authenticate.
            return None
        team_id = row["team_id"]
        key_id = row["id"]
        created_via = row.get("created_via")
        created_by = row.get("created_by")
        key_prefix = row.get("key_prefix")
    else:
        memberships = cp.query(
            "team_memberships",
            select=["team_id"],
            filters=[("lookup_hash", "eq", h), ("status", "eq", "active")],
        )
        if not memberships:
            return None  # registry-only key → nothing resolves (E2E-7-negative)
        team_id = memberships[0]["team_id"]

    team_rows = cp.query(
        "teams", select=_QUOTA_SELECT, filters=[("id", "eq", team_id)]
    )
    if not team_rows:
        # Key's team vanished — fail closed (401), never authenticate.
        return None
    team_row = team_rows[0]

    tier = team_row.get("tier") or "free"
    from tortoise.pricing import tier_limits
    lim = tier_limits(tier)
    max_users = team_row.get("max_users")
    max_graphs = team_row.get("max_graphs")
    graph_size_cap = team_row.get("graph_size_cap")
    return {
        "team_id": team_id,
        "key_id": key_id,
        "tier": tier,
        # max_users/max_graphs: preserve None (unlimited, Team tier) and fall
        # back to pricing when the column is missing (mirrors registry path).
        "max_users": max_users if max_users is not None else lim["max_users_per_team"],
        "max_graphs": max_graphs if max_graphs is not None else lim["max_graphs_per_team"],
        # points counter counts graph nodes → graph_size_cap (#310 GAP-B)
        "max_points": int(graph_size_cap) if graph_size_cap is not None else lim["max_graph_nodes"],
        # 0006 teams has no max_api_keys/max_sessions columns — pricing/defaults
        "max_api_keys": lim["max_api_keys"],
        "max_sessions": DEFAULT_MAX_SESSIONS,
        # additive metadata (not part of the registry dict contract)
        "key_prefix": key_prefix,
        "created_via": created_via,
        "created_by": created_by,
    }


def update_last_used(cp, key_id: str) -> None:
    """#685 write-through on api_keys.last_used_at. Best-effort by contract —
    a telemetry write must NEVER gate authentication (registry path mirrors
    this with try/except around the SET)."""
    try:
        cp.query(
            "api_keys",
            method="PATCH",
            filters=[("id", "eq", key_id)],
            json_body={"last_used_at": datetime.now(timezone.utc).isoformat()},
        )
    except Exception:
        _logger.debug("update_last_used failed for key %s (best-effort)", key_id)


# ── Session-path helpers (get_current_user memberships, E1/E6/E8) ──────────

def user_memberships(cp, user_id: str) -> list[dict]:
    """Active memberships for a JWT user: [{team_id, role}].

    Placeholder rows (team_id='') are excluded — mirrors the registry
    ``_user_memberships`` predicate (plan §4.1 step 6).
    """
    rows = cp.query(
        "team_memberships",
        select=["team_id", "role"],
        filters=[("user_id", "eq", user_id), ("status", "eq", "active"),
                 ("team_id", "neq", "")],
    )
    return [{"team_id": r["team_id"], "role": r["role"]} for r in rows]


def membership_for_user_team(cp, user_id: str, team_id: str) -> dict | None:
    """Active membership for (user, team) → {team_id, role} | None."""
    rows = cp.query(
        "team_memberships",
        select=["role"],
        filters=[("user_id", "eq", user_id), ("team_id", "eq", team_id),
                 ("status", "eq", "active")],
    )
    if not rows:
        return None
    return {"team_id": team_id, "role": rows[0]["role"]}


def team_by_id(cp, team_id: str) -> dict | None:
    """Team row (registry-properties-shaped dict) or None."""
    rows = cp.query(
        "teams",
        select=["id", "name", "tier", "email", "graph_name", "max_users",
                "max_teams", "max_graphs", "ops_allowance", "graph_size_cap",
                "backup_enabled", "backup_latest_at", "backup_restored_at",
                "created_at"],
        filters=[("id", "eq", team_id)],
    )
    return rows[0] if rows else None


# ── Session-key mint writes (E2E-2 round-trip: mint → api_keys → resolve) ──

def active_api_keys(cp, team_id: str, *, created_via: str | None = None,
                    created_by: str | None = None) -> list[dict]:
    """Non-revoked, non-expired api_keys rows for a team (#742 expiry).

    Optional created_via/created_by filters (bootstrap cap / recovery cap
    queries). Expiry is filtered here (PostgREST dialect stays minimal).
    """
    filters: list[tuple[str, str, object]] = [
        ("team_id", "eq", team_id), ("revoked_at", "is", None),
    ]
    if created_via is not None:
        filters.append(("created_via", "eq", created_via))
    if created_by is not None:
        filters.append(("created_by", "eq", created_by))
    rows = cp.query(
        "api_keys",
        select=["id", "team_id", "created_via", "created_by", "created_at",
                "expires_at", "revoked_at"],
        filters=filters,
    )
    now = datetime.now(timezone.utc)
    return [
        r for r in rows
        if (exp := _parse_ts(r.get("expires_at"))) is None or exp > now
    ]


def insert_api_key(cp, row: dict) -> None:
    """Insert an api_keys row (session-key mint). Raises on failure (the mint
    must not claim success for a key that cannot resolve)."""
    cp.query("api_keys", method="POST", json_body=row)


def revoke_api_key(cp, key_id: str, now: str | None = None) -> None:
    """Set revoked_at on an api_keys row (recovery-cap auto-revoke)."""
    cp.query(
        "api_keys",
        method="PATCH",
        filters=[("id", "eq", key_id)],
        json_body={"revoked_at": now or datetime.now(timezone.utc).isoformat()},
    )


# ── Invitations (plan Task 4: mint / accept / rescind via lookup_hash) ────

class InvitationError(Exception):
    """Semantic invitation rejection (dedup, expiry, single-use, role).

    Carries an HTTP status so hosted_api can translate directly (409 for
    dedup/already-member, 403 for role/email mismatch, 400 for
    invalid/expired/used/revoked, 404 unknown, 422 bad role). Deliberately
    NOT a RuntimeError: these are expected client outcomes, not control-
    plane failures — the fail-closed contract (query errors → RuntimeError
    → 500) is untouched.
    """

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def invitation_mint(cp, team_id: str, email: str, role: str,
                    invited_by: str, expires_days: int = 7) -> dict:
    """Create a pending invitations row; returns the plaintext token ONCE.

    The token is minted here and stored only as ``lookup_hash`` (SHA-256(pepper
    + token), plan P1-1) — the hash is computed in-process via
    ``tortoise.auth.lookup_hash``; the plaintext is returned to the caller
    exactly once. Acceptance is an O(1) indexed lookup_hash match, no scan.

    Dedup: the 0008 partial unique index (team_id, email) WHERE
    status='pending' — a duplicate PENDING invite raises InvitationError(409);
    accepted/revoked invites don't block a fresh re-invite.

    Role: 0008 CHECK closes the enum to 'admin' | 'member' (owner is not
    invitable — single-owner model, D7 #574).
    """
    import uuid
    from datetime import datetime, timedelta
    from tortoise.auth import lookup_hash as _lookup_hash

    role = (role or "member").strip().lower()
    email = (email or "").strip().lower()
    if not team_id or not email or "@" not in email:
        raise InvitationError("team_id and valid email required", status=422)
    if role not in ("admin", "member"):
        raise InvitationError("role must be 'admin' or 'member'", status=422)

    dup = cp.query(
        "invitations",
        select=["id"],
        filters=[("team_id", "eq", team_id), ("email", "eq", email),
                 ("status", "eq", "pending")],
    )
    if dup:
        raise InvitationError(
            f"Pending invitation already exists for {email} in this team",
            status=409,
        )

    token = str(uuid.uuid4())
    iid = uuid.uuid4().hex[:26]
    now = datetime.now(timezone.utc)
    expires_at = (now + timedelta(days=expires_days)).isoformat()
    cp.query(
        "invitations",
        method="POST",
        json_body={
            "id": iid,
            "team_id": team_id,
            "lookup_hash": _lookup_hash(token),
            "role": role,
            "invited_by": invited_by,
            "email": email,
            "status": "pending",
            "expires_at": expires_at,
        },
    )
    return {"id": iid, "email": email, "role": role,
            "expires_at": expires_at, "status": "pending",
            "token": token}


def invitation_accept(cp, token: str, user_id: str,
                      user_email: str | None = None) -> dict:
    """Accept a pending invitation by plaintext token (lookup_hash verify).

    Atomic single-use (E2E-3): the invitation is PATCHed to status='accepted'
    with a conditional ``WHERE status='pending'`` filter, then re-read to
    verify — a concurrent accept loses the race and the verify fails. On
    success the REAL team_memberships row is created with the INVITED role
    (O/I/T: accepted membership carries the invited role), status='active'.

    Rejects (InvitationError): unknown token (400), expired (400), used /
    already accepted (400), revoked (400), already an active member (409),
    JWT email vs invite email mismatch when the JWT carries an email (403).

    A previously removed/invited membership row for (user, team) is
    resurrected in place (registry MERGE semantics) rather than INSERTed —
    uq_member_team (user_id, team_id) would reject a duplicate row.
    """
    import uuid
    from tortoise.auth import lookup_hash as _lookup_hash

    now = datetime.now(timezone.utc)
    h = _lookup_hash(token)
    rows = cp.query(
        "invitations",
        select=["id", "team_id", "email", "role", "status", "expires_at"],
        filters=[("lookup_hash", "eq", h)],
    )
    if not rows:
        raise InvitationError("Invalid or expired invite token")
    inv = rows[0]

    status = inv.get("status") or "pending"
    if status != "pending":
        if status == "accepted":
            raise InvitationError("Invitation has already been accepted")
        if status == "revoked":
            raise InvitationError("Invitation has been revoked")
        raise InvitationError("Invitation is not pending")
    exp = _parse_ts(inv.get("expires_at"))
    if exp is not None and exp <= now:
        raise InvitationError("Invite token expired")

    if user_email and user_email.strip().lower() != (inv.get("email") or "").lower():
        # Invitee must be the invitee's account (registry-path guard, #574).
        raise InvitationError("Invite email does not match this account",
                              status=403)

    # Existing membership for (user, team) — any status.
    existing = cp.query(
        "team_memberships",
        select=["id", "status"],
        filters=[("user_id", "eq", user_id), ("team_id", "eq", inv["team_id"])],
    )
    if existing and existing[0].get("status") == "active":
        raise InvitationError("Already a member of this team", status=409)

    # Single-use: conditional PATCH (status='pending' filter) then verify.
    cp.query(
        "invitations",
        method="PATCH",
        filters=[("id", "eq", inv["id"]), ("status", "eq", "pending")],
        json_body={"status": "accepted", "accepted_at": now.isoformat()},
    )
    check = cp.query(
        "invitations", select=["status"], filters=[("id", "eq", inv["id"])],
    )
    if not check or check[0].get("status") != "accepted":
        # Lost the accept race — the invite was consumed in between.
        raise InvitationError("Invitation has already been accepted")

    membership_id = uuid.uuid4().hex[:26]
    if existing:
        cp.query(
            "team_memberships",
            method="PATCH",
            filters=[("id", "eq", existing[0]["id"])],
            json_body={"role": inv["role"], "status": "active",
                       "invited_email": inv.get("email"),
                       "updated_at": now.isoformat()},
        )
    else:
        team = team_by_id(cp, inv["team_id"])
        cp.query(
            "team_memberships",
            method="POST",
            json_body={
                "id": membership_id,
                "user_id": user_id,
                "team_id": inv["team_id"],
                # 0001 NOT NULL columns; key_hash='pending' is the
                # reconcilable-placeholder sentinel (an invited member has no
                # key of their own — session keys live in api_keys).
                "team_name": (team or {}).get("name") or "",
                "key_hash": "pending",
                "graph_name": (team or {}).get("graph_name") or "",
                "role": inv["role"],  # invited role preserved (O/I/T)
                "status": "active",
                "invited_email": inv.get("email"),
            },
        )
    return {"team_id": inv["team_id"], "role": inv["role"]}


def invitation_rescind(cp, invitation_id: str, team_id: str,
                       actor_user_id: str) -> dict:
    """Owner/admin rescind — set status='revoked' (soft delete).

    Scoped to the actor's team (an invite id from another team is a 404).
    Idempotent for already-revoked invites (mirrors the registry SDK); an
    ACCEPTED (used) invite cannot be rescinded — the membership already
    exists.
    """
    mem = membership_for_user_team(cp, actor_user_id, team_id)
    if not mem or mem["role"] not in ("owner", "admin"):
        raise InvitationError("Requires owner or admin role in team", status=403)

    rows = cp.query(
        "invitations",
        select=["id", "status", "team_id"],
        filters=[("id", "eq", invitation_id), ("team_id", "eq", team_id)],
    )
    if not rows:
        raise InvitationError("Invitation not found", status=404)
    inv = rows[0]
    if inv.get("status") == "accepted":
        raise InvitationError(
            "Invitation already accepted — cannot rescind", status=409)
    if inv.get("status") == "revoked":
        return {"revoked": True, "already": True,
                "invitation_id": invitation_id}
    cp.query(
        "invitations",
        method="PATCH",
        filters=[("id", "eq", invitation_id)],
        json_body={"status": "revoked"},
    )
    return {"revoked": True, "invitation_id": invitation_id}


def pending_invitations(cp, team_id: str) -> list[dict]:
    """Pending (unused) invites for a team — dashboard surface, oldest first.

    The actionable set: only status='pending' rows are returned (consumed /
    revoked invites are excluded; list_members shows the resulting
    memberships).
    """
    return cp.query(
        "invitations",
        select=["id", "team_id", "role", "invited_by", "email", "status",
                "expires_at", "created_at"],
        filters=[("team_id", "eq", team_id), ("status", "eq", "pending")],
        order="created_at",
    )
