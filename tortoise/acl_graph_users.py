"""C4 (#2113): FalkorDB per-graph ACL users — defense-in-depth data boundary.

The empirical recipe (falkordb 4.20.4, 2026-09-01) implemented exactly:

    ACL SETUSER tenant_<gid> on ><pw> ~org_{tid}_{gid} \\
        +GRAPH.QUERY +GRAPH.RO_QUERY +PING

- ONE user per graph (username `tenant_<gid>` — gid is unique so usernames
  are unique). The key pattern is the graph's exact namespace
  (`org_{tid}_{gid}`, the shape registry ``_graph_create`` and supabase
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

The layer is now DECLARED per deployment (:func:`layer_provisionable`,
``TORTOISE_ACL_LAYER``) instead of being discovered per mint (#10093, the
owner decision recorded on #8617, 2026-10-26). On FalkorDB **Cloud** no
credential the app may hold can run ``ACL`` at all (no preset grants ``+ACL``
and ``ACL`` is not in the vendor's allowed-commands list), so the old
"reachable but refused ⇒ fail the mint" arm rejected every hosted graph
creation — the one error class that must NOT read as "roll back" is a
permission refusal, and it now has its own type (:class:`AclLayerDenied`) and
its own reported state. ``create_acl_user`` returns a discriminated result
(``provisioned`` / ``absent`` / ``denied``) instead of bare ``None``, so a
caller can RECORD why the layer is missing instead of inferring it.
"""

from __future__ import annotations

import logging
import os
import re
from urllib.parse import urlparse

logger = logging.getLogger("tortoise.acl")

_SUPPORTED_URI_SCHEMES = {"docker", "redis", "rediss"}

# ── The declared layer posture (#10093) ──────────────────────────────────
# "off"          — this deployment CANNOT provision the layer (FalkorDB Cloud:
#                  no credential may run ACL). No attempt is made, nothing is
#                  faked, the absence is recorded as `absent`/by_design.
# "best_effort"  — attempt it (the layer may work here), record a refusal, and
#                  never fail a mint for it.
# "strict"       — the pre-#10093 contract and the DEFAULT: the layer is
#                  declared provisionable on this deployment, so a reachable
#                  server that refuses the recipe fails the mint (rollback).
_ACL_LAYER_ENV = "TORTOISE_ACL_LAYER"
_ACL_LAYER_MODES = ("off", "best_effort", "strict")
_ACL_LAYER_DEFAULT = "strict"
_unknown_mode_warned = False

# The tri-state the graph-module probe answers (#10093) — the three reasons the
# ACL layer can be missing, which used to collapse into `False`:
#   present — the falkordb module is there; the layer is attemptable.
#   absent  — no server / embedded / bare redis / unreachable: nothing to do.
#   denied  — the server answered NOPERM: present but not ours to use.
ACL_LAYER_PRESENT = "present"
ACL_LAYER_ABSENT = "absent"
ACL_LAYER_DENIED = "denied"

# The discriminated `create_acl_user` outcomes.
ACL_USER_PROVISIONED = "provisioned"
ACL_USER_ABSENT = "absent"
ACL_USER_DENIED = "denied"


def layer_provisionable() -> str:
    """The DECLARED posture of the per-graph ACL layer on this deployment:
    ``off`` / ``best_effort`` / ``strict``.

    Read from ``TORTOISE_ACL_LAYER`` on every call (cheap env read, so a test
    or an operator can flip it without a fresh import). Default ``strict`` =
    the pre-#10093 behaviour; an UNRECOGNISED value also resolves to
    ``strict`` and warns — a typo must never silently weaken a security layer
    to ``off`` (fail-closed).

    Why a declaration at all (#10093/#8617): the managed lane discovers the
    refusal per mint, which is both an outage (the mint fails) and a bad record
    (the reconcile report could not say WHY the layer was missing).
    """
    global _unknown_mode_warned
    raw = os.environ.get(_ACL_LAYER_ENV)
    if raw is None:
        return _ACL_LAYER_DEFAULT
    value = raw.strip().lower()
    if value in _ACL_LAYER_MODES:
        return value
    if not _unknown_mode_warned:
        _unknown_mode_warned = True
        logger.warning(
            "%s=%r is not one of %s — treating it as %r (fail-closed: an "
            "unrecognised value must never weaken the layer to 'off')",
            _ACL_LAYER_ENV, raw, "/".join(_ACL_LAYER_MODES), _ACL_LAYER_DEFAULT)
    return _ACL_LAYER_DEFAULT


class AclUserResult(dict):
    """The discriminated outcome of :func:`create_acl_user` (#10093).

    A dict on purpose: every pre-#10093 caller and test reads the credential
    keys as a mapping (``r["username"]`` / ``r["password"]``), and the
    provisioning seams stubbed them with plain dicts — one shape keeps both
    working. ``status`` is the discriminator:

      ``provisioned`` — the user was created/upserted (credential keys set).
      ``absent``      — the layer is not there to provision, or this deployment
                        DECLARED it unavailable (``reason`` says which).
      ``denied``      — the server refused the command (``NOPERM``): the layer
                        exists and is not ours to use here.

    ``absent`` and ``denied`` are deliberately separate: "not provisionable
    here" is a deployment fact, "refused" is a credential fact, and a report
    that conflates them cannot tell a misconfiguration from a boundary.
    """

    @property
    def status(self) -> str:
        return self["status"]

    @property
    def reason(self) -> str | None:
        return self.get("reason")


# The exact command allowlist — never widen without re-verifying key specs
# (GRAPH.LIST registers firstkey=0 → key-pattern ACL never applies, #2652).
# Denial is the -@all base the full-reset SETUSER composes (redis 8 adds
# -@all when commands are granted with no category); never add an explicit
# "deny" list here — a +prefixed entry would GRANT.
_GRAPH_COMMANDS = ("+GRAPH.QUERY", "+GRAPH.RO_QUERY", "+PING")


class AclLayerError(RuntimeError):
    """ACL create failed while the server is reachable — a real RECIPE failure
    (NOT a down layer: down/absent → fail-soft no-op, and NOT a permission
    refusal: that is :class:`AclLayerDenied`).

    This class keeps its pre-#10093 meaning: the recipe is ours to fix, so a
    strict mint rolls back rather than shipping a graph without its ACL user."""


class AclLayerDenied(AclLayerError):
    """The server answered ``NOPERM`` — the CREDENTIAL we are connected as is
    not permitted to run the command. Distinct from :class:`AclLayerError`
    because the two call for opposite responses: a recipe error is ours to fix
    (a strict mint rolls back), while a permission refusal cannot be fixed from
    inside the process at all — on FalkorDB Cloud no credential the app may
    hold can be granted ``+ACL`` (#8617), so rolling a hosted mint back for it
    is an outage, not a safeguard (#10093).

    A subclass of :class:`AclLayerError` on purpose: an un-migrated handler
    still catches it and takes the pre-#10093 path instead of crashing on an
    unknown type — every current consumer branches on this class first."""


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


def _graph_namespace(org_id: str, graph_id: str) -> str:
    """The FalkorDB graph name the ACL pattern must match — the exact
    namespace both control-plane modes derive (registry _graph_create +
    supabase _provision_graph): org_{org_id}_{graph_id}."""
    if not re.fullmatch(r"[0-9A-Za-z_-]+", org_id):
        raise AclLayerError(
            f"refusing to build an ACL pattern from unsafe team id "
            f"{org_id!r} — ids must be [0-9A-Za-z_-].")
    return f"org_{org_id}_{graph_id}"


def _admin_client():
    """redis-py client to the FalkorDB server the app uses (TORTOISE_DB_URI
    admin creds). None when: no URI (embedded redislite), a hostless URI, or
    an unsupported scheme — every ACL fn then no-ops (fail-soft). Port-less
    URIs default to 16379 (the repo-wide convention — projection/session
    parsers; docker-compose maps 6379 for containers WITH the port).

    #7357 — built through ``guarded_client`` (the #6882 seam), so a transport
    failure on a role/ACL mutation carries the instance-outage note: the
    instance went away (a restart, an RDB reload, an OOM-kill), and for a
    mutating verb whether the command took effect is UNKNOWN — read the ACL
    state back before re-issuing it.

    THE SURFACE THAT GAINS THE NOTE IS THIS MODULE'S LOG LINE, NOT a
    caller-facing message: every ACL fn here is fail-soft and catches what the
    guarded channel raises (``except Exception`` → ``logger.warning(...)``, then
    ``False``/``None`` — see :func:`_setuser`, :func:`drop_acl_user`), so the
    noted exception is LOGGED and swallowed rather than returned to a caller,
    and an ``AclLayerError`` from a real ``ResponseError`` refusal is untouched
    and correctly carries no note (``is_instance_unavailable`` excludes it).
    What was missing before this seam is the mechanism class and the read-back
    instruction; the prefix this module already logged said "connection
    failure". A first-command failure inside :func:`_falkordb_present` is still
    swallowed there with no log at all — pre-existing, and outside this wiring
    change's scope. The HEDGE arm is the one this channel gets, deliberately:
    every verb this module sends is non-Cypher (``MODULE LIST``, ``ACL GETUSER``/
    ``SETUSER``/``DELUSER``/``SAVE``), so ``_command_mutating`` has no statement
    to classify and answers ``None``. The ACL bearer commands are also not
    operator-guarded (no Cypher reaches the ``=~`` guard); the outage note is
    the whole gain.
    """
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

    # #3039: decode userinfo through the single shared rule —
    # urlparse does NOT percent-decode, redis.Redis(...) does not either
    # (only redis.from_url does), so a raw read breaks auth silently.
    from tortoise.config import parse_uri_userinfo

    # #6882's ONE construction seam — see this function's docstring for the
    # #7357 half (the outage note) and why the guard's Cypher arm is inert here.
    from tortoise.cypher_guard import guarded_client

    # #3065: the socket bounds come from the product's ONE resolution point,
    # not from a pair of literals. The literals this replaces (3s connect /
    # 5s read) were bounded but KNOB-BLIND — `TORTOISE_DB_SOCKET_TIMEOUT`
    # never reached this client, which is the same defect one step milder.
    from tortoise.projection import _bounded_server_socket_kwargs
    username, password = parse_uri_userinfo(uri)
    return guarded_client(
        redis.Redis,
        host=parsed.hostname,
        port=parsed.port or 16379,
        username=username,
        password=password,
        ssl=(parsed.scheme == "rediss"),
        decode_responses=False,
        **_bounded_server_socket_kwargs(),
    )


def _is_permission_denial(exc: BaseException) -> bool:
    """True when the server REFUSED the command for permission reasons.

    Redis/FalkorDB spell this ``NOPERM this user has no permissions to run the
    '<cmd>' command`` (redis 6/7/8 word it differently around the quoted verb,
    so match the prefix tokens, not a full sentence). Narrow on purpose: a
    recipe error (``ERR ...``) or an unknown command is NOT a denial — it must
    keep meaning "roll the mint back" on a strict deployment (#10093).
    """
    msg = str(exc).lower()
    return "noperm" in msg or "no permissions" in msg \
        or "not permitted" in msg


def _falkordb_present(client) -> str:
    """TRI-STATE capability probe (#10093): ``present`` / ``absent`` /
    ``denied``.

    Bare redis (no falkordb module) has ACL but no graph commands — the
    per-graph layer is meaningless there (:data:`ACL_LAYER_ABSENT`). A server
    that REFUSES the probe (:data:`ACL_LAYER_DENIED`) is a different fact: the
    layer may exist and this credential may not look at it — pre-#10093 both
    collapsed into ``False``/``None``, which is how "not provisionable here"
    became indistinguishable from "absent because bare Redis".

    Strings, not an enum, so the surrounding diagnostics can print them; every
    consumer compares against the constants above, never against truthiness.
    """
    try:
        rows = client.execute_command("MODULE", "LIST") or []
    except Exception as e:  # a probe never raises — any failure is a state
        return ACL_LAYER_DENIED if _is_permission_denial(e) \
            else ACL_LAYER_ABSENT
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
    return ACL_LAYER_PRESENT if b"graph" in flat else ACL_LAYER_ABSENT


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


def _registry_store_credential(org_id: str, graph_id: str,
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
            "MATCH (g:Graph {id:$gid, org_id:$tid}) "
            "SET g.acl_user=$u, g.acl_pass=$p",
            params={"gid": graph_id, "tid": org_id, "u": username,
                    "p": password},
        )
    except Exception as e:
        logger.warning(
            "ACL credential store failed for graph %s (user exists; mapping "
            "degrades — C5 reads absence fail-closed): %s", graph_id, e)


def _stored_acl_password(org_id: str, graph_id: str) -> str | None:
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
            "MATCH (g:Graph {id:$gid, org_id:$tid}) RETURN g.acl_pass",
            params={"gid": graph_id, "tid": org_id},
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


def create_acl_user(graph_id: str, org_id: str) -> AclUserResult:
    """Create/upsert the per-graph ACL user (hardened recipe, D1).

    Returns an :class:`AclUserResult` — a discriminated outcome (#10093):

    * ``provisioned`` — ``{username, password, graph}`` (the pre-#10093 dict
      shape, unchanged, plus the discriminator).
    * ``absent`` — the layer is not provisionable HERE, with the reason:
      the deployment declared it off (``TORTOISE_ACL_LAYER=off``), there is no
      server URI (embedded redislite), the server is bare redis (no falkordb
      module), or it went away mid-op. Fail-soft, never a SPOF.
    * ``denied`` — the server answered ``NOPERM``: the layer exists and this
      credential may not provision it. Also fail-soft: it is not the caller's
      to fix, and the caller RECORDS it (a strict mint decides separately
      whether to roll back, from the declared posture).

    Raises :class:`AclLayerError` when the server is REACHABLE but the recipe
    itself fails (e.g. the default user is open/nopass) — the strict
    provisioning caller rolls back (no graph without its ACL user).

    ONE live secret per graph + exact command/key state (second-model
    S2/S3): _setuser issues a FULL ``reset`` before rebuilding, so every
    upsert heals drift AND leaves exactly one password (re-asserting the
    same stored secret is churn-free). Registry mode reuses the stored
    password when present; a fresh one is generated otherwise (the reset
    kills any orphaned prior secret — no ``>``-append accumulation).
    Supabase/hosted mode is create-once: further mints no-op when the user
    exists and return ``password: None`` (INTENTIONAL — the hosted
    credential story is C5's / the platform manages DB users; callers must
    not treat None as an error; per-mint throwaway secrets would accumulate
    live, never-revoked credentials).
    """
    if layer_provisionable() == "off":
        return AclUserResult(
            status=ACL_USER_ABSENT,
            reason=("the per-graph ACL layer is declared unavailable on this "
                    f"deployment ({_ACL_LAYER_ENV}=off) — no credential we "
                    "may hold can provision it here (#10093/#8617)"),
        )
    client = _admin_client()
    if client is None:
        return AclUserResult(
            status=ACL_USER_ABSENT,
            reason=("no server URI (embedded redislite) or an unsupported "
                    "URI scheme — there is no per-graph ACL layer here"),
        )
    presence = _falkordb_present(client)
    if presence == ACL_LAYER_DENIED:
        return AclUserResult(
            status=ACL_USER_DENIED,
            reason=("the server refused the module probe (NOPERM) — the "
                    "credential may not inspect or provision the ACL layer"),
        )
    if presence != ACL_LAYER_PRESENT:
        return AclUserResult(
            status=ACL_USER_ABSENT,
            reason=("the server has no falkordb graph module (bare redis) — "
                    "a per-graph ACL user would protect nothing"),
        )
    _ensure_default_user_secured(client)
    username = _username_for(graph_id)
    graph_name = _graph_namespace(org_id, graph_id)
    from tortoise.supabase_control import is_supabase_enabled
    if is_supabase_enabled():
        if _user_exists_on(client, username):
            # create-once: no per-mint password churn (hosted defers the
            # credential story to C5 — the platform manages DB users).
            return AclUserResult(status=ACL_USER_PROVISIONED,
                                 username=username, password=None,
                                 graph=graph_name)
        password = os.urandom(24).hex()
        try:
            ok = _setuser(client, username, graph_name, password)
        except AclLayerDenied as e:
            return AclUserResult(status=ACL_USER_DENIED, username=username,
                                 graph=graph_name, reason=str(e))
        if not ok:
            return AclUserResult(  # layer down mid-op — fail-soft
                status=ACL_USER_ABSENT, username=username,
                graph=graph_name,
                reason="the server went away mid-provisioning (connection "
                       "failure) — the layer is down, not refused")
        _acl_save(client)
        return AclUserResult(status=ACL_USER_PROVISIONED, username=username,
                             password=password, graph=graph_name)
    # Registry (selfhost) mode: reuse the stored password when present (the
    # full-reset upsert keeps ONE live secret either way — no exists-read
    # TOCTOU, no append churn).
    stored = _stored_acl_password(org_id, graph_id)
    password = stored if stored is not None else os.urandom(24).hex()
    try:
        ok = _setuser(client, username, graph_name, password)
    except AclLayerDenied as e:
        return AclUserResult(status=ACL_USER_DENIED, username=username,
                             graph=graph_name, reason=str(e))
    if not ok:
        return AclUserResult(  # layer down mid-op — fail-soft
            status=ACL_USER_ABSENT, username=username, graph=graph_name,
            reason="the server went away mid-provisioning (connection "
                   "failure) — the layer is down, not refused")
    _registry_store_credential(org_id, graph_id, username, password)
    _acl_save(client)
    return AclUserResult(status=ACL_USER_PROVISIONED, username=username,
                         password=password, graph=graph_name)


def _setuser(client, username: str, graph_name: str, password: str) -> bool:
    """Issue ACL SETUSER as a FULL RESET to the exact hardened state
    (second-model S2/S3): ``reset`` clears every prior rule (passwords,
    keys, commands, channels, on/off) so an existing user with a broader
    grant (manual admin fix, old recipe, drift) is HEALED — SETUSER would
    otherwise ACCUMULATE ``~``/``+`` rules and ``+@all``/allkeys would
    silently persist. ``reset`` also guarantees ONE live secret
    unconditionally: re-asserting the same stored password is churn-free
    (same single secret).

    Returns True on success. Raises :class:`AclLayerDenied` when the server
    answered ``NOPERM`` (the credential may not run ACL — the server is UP, so
    this is not a down layer) and :class:`AclLayerError` on any other
    server-side ResponseError (a real recipe failure — the strict caller rolls
    back). Connection/timeout failures log + return False (layer-down mid-op is
    fail-soft — never a SPOF, D3)."""
    cmd = ["ACL", "SETUSER", username, "reset", "on", f">{password}",
           f"~{graph_name}", *_GRAPH_COMMANDS]
    import redis as redis_py
    try:
        client.execute_command(*cmd)
    except redis_py.ResponseError as e:
        if _is_permission_denial(e):
            raise AclLayerDenied(
                f"ACL SETUSER refused for graph {graph_name} "
                f"(user={username}): {e}") from e
        raise AclLayerError(
            f"ACL SETUSER failed for graph {graph_name} (user={username}): "
            f"{e}") from e
    except Exception as e:
        logger.warning(
            "ACL SETUSER connection failure for %s (fail-soft, layer down "
            "mid-op): %s", username, e)
        return False
    return True


def drop_acl_user(graph_id: str) -> None:
    """Drop the per-graph ACL user on graph delete (E2E-8). Username is
    derivable (tenant_<gid>) — no storage read needed (the delete flow
    tombstones the row/node before this fires). Best-effort: a committed
    delete never 500s on a drop failure.

    #10093: when the deployment DECLARES the layer unavailable, no command is
    issued at all — a layer that cannot be created cannot need dropping, and a
    refusal logged on every delete is noise that reads like a live defect.
    """
    if layer_provisionable() == "off":
        return
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


def credential_for_graph(org_id: str, graph_id: str) -> dict | None:
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
            "MATCH (g:Graph {id:$gid, org_id:$tid}) "
            "RETURN g.acl_user, g.acl_pass",
            params={"gid": graph_id, "tid": org_id},
        ).result_set
        if not rows or not rows[0][0]:
            return None
        return {"username": rows[0][0], "password": rows[0][1],
                "graph": _graph_namespace(org_id, graph_id)}
    except Exception:
        return None
