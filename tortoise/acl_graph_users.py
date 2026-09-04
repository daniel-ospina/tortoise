"""C4 (#2113): FalkorDB per-graph ACL users — defense-in-depth data boundary.

The empirical recipe (falkordb 4.20.4, 2026-09-01) implemented exactly:

    ACL SETUSER tenant_<gid> on ><pw> ~team_{tid}_{gid} \\
        +GRAPH.QUERY +GRAPH.RO_QUERY +PING

- ONE user per graph (username `tenant_<gid>` — gid is unique so usernames
  are unique). The key pattern is the graph's exact namespace
  (`team_{tid}_{gid}`, the shape registry ``_graph_create`` and supabase
  ``_provision_graph`` both derive) — NOT the research doc's ``~tenant_a``
  shorthand (that matched repro graphs literally named tenant_a).
- NEVER ``+@all`` / GRAPH.LIST / KEYS / SCAN / CONFIG / DEBUG / UDF / AUTH;
  ``%R~``/``%W~`` fine-grained perms are documented-but-broken (4.20.4) so
  read-only is command-level ``+GRAPH.RO_QUERY``.
- The default user must be password-secured (``nopass`` absent) — the layer
  is theater without it.
- ACL users are memory-only until ``aclfile`` + ``ACL SAVE`` (selfhost);
  every mutation issues ACL SAVE best-effort (a server without an aclfile
  errors — logged; true restart durability is a deployment config).

Layer posture: defense-in-depth BENEATH the app-layer spine (the app-layer
ownership/scope check is authoritative and works with ACL off). Fail-soft
contract: embedded redislite (no URI) and bare redis (no falkordb module)
no-op — never a single point of failure. A server-reachable SETUSER failure
raises :class:`AclLayerError` so the PROVISIONING mint (strict) rolls back
(no graph without its ACL user); standalone key mints to existing graphs are
fail-soft (the graph's user was created at graph-mint).
"""

from __future__ import annotations

import logging
import os
import re
from urllib.parse import urlparse

logger = logging.getLogger("tortoise.acl")

_SUPPORTED_URI_SCHEMES = {"docker", "redis", "rediss"}

# The exact command allowlist — never widen without re-verifying key specs
# (GRAPH.LIST registers firstkey=0 → key-pattern ACL never applies, #2652).
_GRAPH_COMMANDS = ("+GRAPH.QUERY", "+GRAPH.RO_QUERY", "+PING")
_GRAPH_DENY = ("+GRAPH.LIST", "+GRAPH.CONFIG", "+GRAPH.DEBUG", "+GRAPH.UDF",
               "+KEYS", "+SCAN", "+@all", "+AUTH")


class AclLayerError(RuntimeError):
    """ACL create failed while the server is reachable — a real recipe or
    permission failure (NOT a down layer: down/absent → fail-soft no-op)."""


def _username_for(graph_id: str) -> str:
    """tenant_<gid> — gid is unique per graph (uuid/sha-derived), so the
    username is unique server-wide and derivable (drop needs no storage).
    Fail-closed charset guard: SETUSER/DELUSER rule args are space-split
    server-side, so a caller-supplied id carrying spaces/quotes/globs would
    be a rule-injection primitive — reject anything outside the safe id
    charset ([0-9A-Za-z_-]; ids in this codebase are hex or dash-ids)."""
    if not re.fullmatch(r"[0-9A-Za-z_-]+", graph_id):
        raise AclLayerError(
            f"refusing to build an ACL username from unsafe graph id "
            f"{graph_id!r} — ids must be [0-9A-Za-z_-].")
    return f"tenant_{graph_id}"


def _graph_namespace(team_id: str, graph_id: str) -> str:
    """The FalkorDB graph name the ACL pattern must match — the exact
    namespace both control-plane modes derive (registry _graph_create +
    supabase _provision_graph): team_{team_id}_{graph_id}."""
    if not re.fullmatch(r"[0-9A-Za-z_-]+", team_id):
        raise AclLayerError(
            f"refusing to build an ACL pattern from unsafe team id "
            f"{team_id!r} — ids must be [0-9A-Za-z_-].")
    return f"team_{team_id}_{graph_id}"


def _admin_client():
    """redis-py client to the FalkorDB server the app uses (TORTOISE_DB_URI
    admin creds). None when: no URI (embedded redislite), a hostless URI, or
    an unsupported scheme — every ACL fn then no-ops (fail-soft). Port-less
    URIs default to 16379 (the repo-wide convention — projection/session
    parsers; docker-compose maps 6379 for containers WITH the port)."""
    uri = os.environ.get("TORTOISE_DB_URI")
    if not uri:
        return None
    scheme = uri.split("://", 1)[0]
    if scheme not in _SUPPORTED_URI_SCHEMES:
        return None
    parsed = urlparse(uri)
    if not parsed.hostname:
        return None
    import redis
    return redis.Redis(
        host=parsed.hostname,
        port=parsed.port or 16379,
        username=parsed.username or None,
        password=parsed.password or None,
        ssl=(parsed.scheme == "rediss"),
        socket_connect_timeout=3,
        socket_timeout=5,
        decode_responses=False,
    )


def _falkordb_present(client) -> bool:
    """Bare redis (no falkordb module) has ACL but no graph commands — the
    per-graph layer is meaningless there (no-op, fail-soft)."""
    try:
        rows = client.execute_command("MODULE", "LIST") or []
        tokens: list = []
        for row in rows:
            if not isinstance(row, (list, tuple)):
                tokens.append(row)
                continue
            for t in row:
                tokens.append(t)
        flat = b" ".join(
            t if isinstance(t, bytes) else str(t).encode()
            for t in tokens if t is not None
        )
        return b"graph" in flat
    except Exception:
        return False


def _acl_save(client) -> None:
    """ACL SAVE after every mutation (selfhost persistence, R15). Best-effort:
    a server without an aclfile errors — logged, never fatal."""
    try:
        client.execute_command("ACL", "SAVE")
    except Exception as e:
        logger.warning("ACL SAVE failed (aclfile not configured?): %s", e)


def _parse_getuser(raw) -> tuple[list, list, list, list]:
    """Parse ACL GETUSER into (flags, keys, commands, passwords). Handles
    BOTH shapes: redis 8 returns a LABELED form
    (``[flags, [...], passwords, [...], commands, ..., keys, ...]``) while
    RESP2 flat tokens (``~...``/``+...``/#hash/flags) also occur — labels win
    when present."""
    flags: list = []
    keys: list = []
    commands: list = []
    passwords: list = []

    def _expand(v: bytes) -> None:
        """redis 8 returns the ACL rule as ONE selector string
        ("-@all +graph.query +graph.ro_query +ping"); older RESP2 returns
        one token per rule. Split space-separated tokens and normalize to
        lowercase rule names so consumers compare version-stably."""
        for piece in v.decode().split():
            piece = piece.lower()
            if piece.startswith("~"):
                keys.append(piece)
            elif piece.startswith(("+", "-", "@")):
                commands.append(piece)
            elif piece.startswith("#"):
                passwords.append(piece)
            else:
                flags.append(piece)

    if isinstance(raw, dict):
        for label, val in raw.items():
            vals = val if isinstance(val, (list, tuple)) else [val]
            for v in vals:
                if not isinstance(v, bytes):
                    continue
                if label in (b"keys", b"commands"):
                    _expand(v)
                elif label == b"flags":
                    flags.append(v.decode())
                elif label == b"passwords":
                    passwords.append(v.decode())
        return flags, keys, commands, passwords
    if raw and isinstance(raw[0], bytes) and raw[0] in (
            b"flags", b"passwords", b"commands", b"keys"):
        it = iter(raw)
        for label in it:
            val = next(it, None)
            vals = val if isinstance(val, (list, tuple)) else [val]
            for v in vals:
                if not isinstance(v, bytes):
                    continue
                if label == b"flags":
                    flags.append(v.decode())
                elif label == b"passwords":
                    passwords.append(v.decode())
                else:  # keys / commands — may be a rule string
                    _expand(v)
        return flags, keys, commands, passwords
    for token in raw or []:
        if isinstance(token, bytes):
            _expand(token)
    return flags, keys, commands, passwords


def _ensure_default_user_secured(client) -> None:
    """Refuse to create ACL users while the default user is open (nopass) —
    the layer is theater without a secured default (epic §5.1)."""
    try:
        raw = client.execute_command("ACL", "GETUSER", "default")
    except Exception:
        # GETUSER unsupported/down — fail-soft (the SETUSER below will surface
        # a real server problem).
        return
    flags, _keys, _cmds, passwords = _parse_getuser(raw)
    if b"nopass" in {f.encode() for f in flags} and not passwords:
        raise AclLayerError(
            "default ACL user is open (nopass) — per-graph ACL users are "
            "theater without a secured default. Set a password/requirepass "
            "first.")


def _registry_store_credential(team_id: str, graph_id: str,
                               username: str, password: str) -> None:
    """Persist {username, password} on the registry Graph node (D4). The key
    itself never carries the FalkorDB password — the app resolves it
    server-side (C5 consumes via credential_for_graph). Best-effort: a store
    failure degrades the mapping (user still exists — defense-in-depth
    intact), never raises. Supabase mode: storage deferred to C5 (hosted
    platform manages DB users out-of-band)."""
    try:
        from tortoise.supabase_control import is_supabase_enabled
        if is_supabase_enabled():
            return  # C5 hosted seam reads the cloud API (D4 deferral)
        from tortoise.hosted_api import _make_sdk
        sdk = _make_sdk(namespace="registry")
        sdk._get_registry().query(
            "MATCH (g:Graph {id:$gid, team_id:$tid}) "
            "SET g.acl_user=$u, g.acl_pass=$p",
            params={"gid": graph_id, "tid": team_id, "u": username,
                    "p": password},
        )
    except Exception as e:
        logger.warning(
            "ACL credential store failed for graph %s (user exists; mapping "
            "degrades — C5 reads absence fail-closed): %s", graph_id, e)


def _stored_acl_password(team_id: str, graph_id: str) -> str | None:
    """Reuse a previously stored password when the user already exists —
    an idempotent re-run must not invalidate a stored credential (crash
    between SETUSER and store)."""
    try:
        from tortoise.supabase_control import is_supabase_enabled
        if is_supabase_enabled():
            return None
        from tortoise.hosted_api import _make_sdk
        sdk = _make_sdk(namespace="registry")
        rows = sdk._get_registry().query(
            "MATCH (g:Graph {id:$gid, team_id:$tid}) RETURN g.acl_pass",
            params={"gid": graph_id, "tid": team_id},
        ).result_set
        return rows[0][0] if rows and rows[0][0] else None
    except Exception:
        return None


def _user_exists_on(client, username: str) -> bool:
    """True when the tenant user is already configured on the server."""
    try:
        raw = client.execute_command("ACL", "GETUSER", username)
        return bool(raw)
    except Exception:
        return False


def create_acl_user(graph_id: str, team_id: str) -> dict | None:
    """Create/upsert the per-graph ACL user (hardened recipe, D1).

    Returns {username, password, graph} on success. None when the ACL layer
    is absent (no server URI / bare redis / down) — fail-soft, never a SPOF.
    Raises AclLayerError when the server is REACHABLE but the SETUSER fails
    (a real recipe/permission problem) — the strict provisioning caller
    rolls back (no graph without its ACL user).

    ONE live secret per graph (code-review): registry mode reuses the
    stored password when present (idempotent re-runs don't churn); when a
    NEW password is needed for an EXISTING user (store-miss window — the
    user exists but no stored credential), SETUSER runs ``resetpass`` first
    so the orphaned prior secret dies (``>pw`` alone APPENDS — old secrets
    stay valid). Supabase/hosted mode is create-once: further mints no-op
    when the user exists (the hosted credential story is C5's; per-mint
    throwaway secrets would accumulate live, never-revoked credentials).
    """
    client = _admin_client()
    if client is None:
        return None
    if not _falkordb_present(client):
        return None
    _ensure_default_user_secured(client)
    username = _username_for(graph_id)
    graph_name = _graph_namespace(team_id, graph_id)
    from tortoise.supabase_control import is_supabase_enabled
    if is_supabase_enabled():
        if _user_exists_on(client, username):
            # create-once: no per-mint password churn (hosted defers the
            # credential story to C5 — the platform manages DB users).
            return {"username": username, "password": None,
                    "graph": graph_name}
        password = os.urandom(24).hex()
        _setuser(client, username, graph_name, password, reset=False)
        _acl_save(client)
        return {"username": username, "password": password,
                "graph": graph_name}
    # Registry (selfhost) mode: reuse the stored password when present.
    stored = _stored_acl_password(team_id, graph_id)
    exists = _user_exists_on(client, username)
    if stored is not None:
        password = stored
        reset = False  # re-assert the SAME secret — no churn
    else:
        password = os.urandom(24).hex()
        # A fresh secret on an EXISTING user must resetpass first (the old
        # secret is gone from storage — a crash/store-miss orphan).
        reset = exists
    _setuser(client, username, graph_name, password, reset=reset)
    _registry_store_credential(team_id, graph_id, username, password)
    _acl_save(client)
    return {"username": username, "password": password, "graph": graph_name}


def _setuser(client, username: str, graph_name: str, password: str,
             *, reset: bool) -> None:
    """Issue ACL SETUSER with the hardened recipe. reset=True prepends
    resetpass so only the NEW password stays valid (no ``>``-append of a
    second live secret). Raises AclLayerError on a server-reachable
    failure."""
    cmd = ["ACL", "SETUSER", username, "on"]
    if reset:
        cmd.append("resetpass")
    cmd += [f">{password}", f"~{graph_name}", *_GRAPH_COMMANDS]
    try:
        client.execute_command(*cmd)
    except Exception as e:
        raise AclLayerError(
            f"ACL SETUSER failed for graph {graph_name} (user={username}): "
            f"{e}") from e


def drop_acl_user(graph_id: str) -> None:
    """Drop the per-graph ACL user on graph delete (E2E-8). Username is
    derivable (tenant_<gid>) — no storage read needed (the delete flow
    tombstones the row/node before this fires). Best-effort: a committed
    delete never 500s on a drop failure."""
    client = _admin_client()
    if client is None:
        return
    username = _username_for(graph_id)
    try:
        client.execute_command("ACL", "DELUSER", username)
        _acl_save(client)
    except Exception as e:
        logger.warning("ACL DELUSER failed for %s (best-effort on delete): %s",
                       username, e)


def acl_user_config(graph_id: str) -> dict | None:
    """Config-inspection helper (E2E-1 / tests): ACL GETUSER parsed into
    {username, flags, keys, commands} or None when absent/layer-down."""
    client = _admin_client()
    if client is None:
        return None
    username = _username_for(graph_id)
    try:
        raw = client.execute_command("ACL", "GETUSER", username)
    except Exception:
        return None
    if not raw:
        return None
    flags, keys, commands, _passwords = _parse_getuser(raw)
    return {"username": username, "flags": flags,
            "keys": keys, "commands": commands}


def acl_user_exists(graph_id: str) -> bool:
    """True when the tenant user exists on the server (tests / drop checks)."""
    cfg = acl_user_config(graph_id)
    return cfg is not None


def credential_for_graph(team_id: str, graph_id: str) -> dict | None:
    """C5 seam — the app-layer resolution reads {username, password, graph}
    server-side from the registry node (the per-graph key never carries the
    FalkorDB password). Returns None when unprovisioned/storage-absent
    (C5 reads absence fail-closed)."""
    try:
        from tortoise.supabase_control import is_supabase_enabled
        if is_supabase_enabled():
            return None  # hosted storage deferred (D4)
        from tortoise.hosted_api import _make_sdk
        sdk = _make_sdk(namespace="registry")
        rows = sdk._get_registry().query(
            "MATCH (g:Graph {id:$gid, team_id:$tid}) "
            "RETURN g.acl_user, g.acl_pass",
            params={"gid": graph_id, "tid": team_id},
        ).result_set
        if not rows or not rows[0][0]:
            return None
        return {"username": rows[0][0], "password": rows[0][1],
                "graph": _graph_namespace(team_id, graph_id)}
    except Exception:
        return None
