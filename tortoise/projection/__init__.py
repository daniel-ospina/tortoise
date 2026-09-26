"""Projection — fold the event log into the current graph.

This is the reconstruction path: the log is folded into a derived, rebuildable
view and is NOT the durability authority (see docs/durability-posture.md).
`_apply_one` is the single source of fold semantics, shared by the pure `fold`
(batch) and every incremental backend, so an incrementally-updated projection and
`fold(read_all())` can never diverge.

Backends behind the `Projection` protocol:
  - InMemoryProjection — dict of points (statements AND operators).
  - FalkorProjection   — FalkorDB (Docker/server by default, embedded via path=);
                         same openCypher graph so portable between modes
"""
from __future__ import annotations  # noqa: I001

import contextlib
import hashlib
import json
import math
import re
import os
import shutil
import stat
import logging
import tempfile
import threading
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple, Protocol, runtime_checkable

from tortoise.env_truthy import env_flag  # #4097: the declared truthy contract

logger = logging.getLogger(__name__)


def _embedded_aof_enabled() -> bool:
    """`TORTOISE_EMBEDDED_AOF` — the embedded AOF durability opt-in.

    #4097: the single resolution point for that knob (``FalkorProjection.__init__``
    uses it), through the declared truthy contract. `=on` used to be silently inert
    (the pre-#4097 literal was {"1","true","yes"}).
    """
    return env_flag("TORTOISE_EMBEDDED_AOF", False)

# Process-lifetime cache for FalkorProjection._get_falkordb_version (#1359
# review P2): version detection costs two network RTTs (MODULE LIST + INFO
# server) on EVERY projection open — SDK sessions, ingest, hosted per-request.
# A server's version cannot change mid-process, so cache by endpoint
# (server host/port or embedded db path). Unidentified clients (unit mocks)
# are never cached — the probe stays exact for them.
# Keyed by tuple; value is (major, minor, patch) or None (undetermined).
_FALKORDB_VERSION_CACHE: dict[tuple, tuple[int, int, int] | None] = {}


# ── #2850 (P0 liveness/readiness decouple): bounded DB client timeouts ────
#
# WHY these are bounded with an env knob. Every blocking FalkorDB call runs
# with these socket timeouts, and the socket read/connect timeouts are what
# decide how long a stalled call can hold a thread (or, on the paths that
# still call the client synchronously from the event loop, how long the LOOP
# is stalled — which is what the /healthz heartbeat and the loop-stall
# watchdog observe). The pre-#2850 literals were
# ``socket_connect_timeout=5, socket_timeout=10``: a single blocked connect
# could outlast Fly's 5s check budget by itself, and connect+read (15s) summed
# to the whole 15s http_check timeout with nothing left for the response.
#
# connect: 5s → 2s. A TCP/TLS handshake to a reachable endpoint is
# milliseconds; 5s of silence means a black hole / dead DNS / filtered port,
# where waiting longer buys nothing. Lowering this is close to risk-free.
#
# read: UNCHANGED at 10s, deliberately. This timeout also bounds legitimate
# long commands — a large ``GRAPH.QUERY`` reply, a backup dump, the chunked
# ``vecf32`` restore batches (hosted_backup.py sizes its chunks around it).
# Lowering it silently turns big-but-valid queries into failures, and a
# timeout on an in-flight WRITE is not idempotent (the server may still apply
# it while the client reports an error and retries). After #2850 no health
# check waits on the DB at all, so the read timeout no longer sits in the
# liveness budget; it is now configurable for operators who need a tighter
# loop-stall ceiling (pair a lower value with a lower
# TORTOISE_LOOP_STALL_EXIT_S, see monitoring.start_stall_watchdog — note that
# self-kill is OPT-IN and disabled by default).
_DB_CONNECT_TIMEOUT_DEFAULT = 2.0
_DB_SOCKET_TIMEOUT_DEFAULT = 10.0
#: Sanity ceiling on a configured DB socket timeout (round-3 review P2).
#: ``float()`` accepts ``inf`` and ``1e308``; both are semantically "block
#: forever" — the literal #2850 failure mode — and an ``inf`` passed to
#: redis-py's ``sock.settimeout`` raises ``OverflowError`` (NOT caught by its
#: ``except OSError``), so the DB client could never connect at all. Clamp to
#: a value that still bounds a hung socket.
_DB_TIMEOUT_MAX_S = 60.0
#: Sanity floor on a configured DB socket timeout (round-4 review P2). No
#: socket round trip ever completes in microseconds. ``float()`` accepts
#: ``1e-9``, which turns every FalkorDB operation into an instant timeout —
#: ``TORTOISE_FALKORDB_SOCKET_TIMEOUT_S=1e-9`` is a typo-induced total outage
#: (fail-closed, so not #2850, but the same "finite but absurd" class floored
#: for the health-probe interval). Below the floor we fall back to the default.
_DB_TIMEOUT_MIN_S = 0.05

#: #3350: explicit, bounded retry policy for the EMBEDDED client.
#:
#: redis-py 8's client DEFAULT is ``Retry(ExponentialWithJitterBackoff(
#: base=DEFAULT_RETRY_BASE, cap=DEFAULT_RETRY_CAP), retries=10)`` — TEN
#: retries after the first attempt. Two consequences, both bad here:
#:
#:  * a read timeout costs ``11 x socket_timeout`` PLUS up to ~75s of
#:    exponential jitter backoff, so ONE wedged embedded daemon parks
#:    whatever thread is running the query for ~59s (measured, SIGSTOPped
#:    daemon, before this constant existed) — and after
#:    ``TORTOISE_FALKORDB_SOCKET_TIMEOUT_S`` was wired in (#3350) the
#:    DEFAULT 10s read timeout would have made that ~115s;
#:  * it makes the bound UN-KNOWABLE from outside: setting
#:    ``TORTOISE_FALKORDB_SOCKET_TIMEOUT_S=t`` still waited ~11t, so the
#:    knob an operator lowers to tighten the loop-stall ceiling did not
#:    tighten what actually parked the thread.
#:
#: ONE retry keeps the transient-blip recovery (a single reconnect) while
#: capping the multiplier at 2, so the embedded lane's worst case is
#: statically ``(1 + _EMBEDDED_RETRY_COUNT) * socket_timeout + backoff``
#: rather than a dependency default that can change under us. That is the
#: property ``monitoring``'s layered-timeout doctrine needs ("make the inner
#: call bounded so the worker frees itself"). The HOST branch is deliberately
#: untouched — its retry policy is not what #3350 is about.
_EMBEDDED_RETRY_COUNT = 1
_EMBEDDED_RETRY_BASE = 0.1
_EMBEDDED_RETRY_CAP = 1.0


def _embedded_retry():
    """Bounded retry policy for the embedded client (#3350).

    See ``_EMBEDDED_RETRY_COUNT`` for why this exists. Built per call rather
    than shared so no state is aliased across clients.
    """
    from redis.backoff import ExponentialWithJitterBackoff
    from redis.retry import Retry as _Retry

    return _Retry(
        backoff=ExponentialWithJitterBackoff(
            base=_EMBEDDED_RETRY_BASE, cap=_EMBEDDED_RETRY_CAP),
        retries=_EMBEDDED_RETRY_COUNT,
    )


def _socket_timeouts() -> tuple[float, float]:
    """``(socket_connect_timeout, socket_timeout)`` from env, with defaults.

    ``TORTOISE_FALKORDB_CONNECT_TIMEOUT_S`` / ``TORTOISE_FALKORDB_SOCKET_TIMEOUT_S``.
    A non-numeric, NON-FINITE (``nan``/``inf``), non-positive, below-
    ``_DB_TIMEOUT_MIN_S`` or above-``_DB_TIMEOUT_MAX_S`` value falls
    back/clamps rather than disabling the bound (a 0/None redis timeout means
    "block forever" — exactly the failure mode #2850 is about; ``inf``/``1e308``
    mean the same and an ``inf`` socket timeout raises ``OverflowError`` inside
    redis-py, bricking the client at boot; ``1e-9`` times out every operation
    before it can complete).
    """
    def _one(name: str, default: float) -> float:
        raw = os.environ.get(name)
        if raw is None or not str(raw).strip():
            return default
        try:
            v = float(raw)
        except (TypeError, ValueError):
            logger.warning("%s=%r is not a number — using %ss", name, raw, default)
            return default
        if not math.isfinite(v):
            logger.warning("%s=%r is not finite — using %ss (a non-finite "
                           "socket timeout means 'block forever' or raises "
                           "OverflowError in the client)", name, raw, default)
            return default
        if v <= 0:
            return default
        if v < _DB_TIMEOUT_MIN_S:
            logger.warning("%s=%r is below the %.2fs floor — using %ss (a "
                           "sub-floor timeout fails every DB operation before "
                           "it can complete)", name, raw, _DB_TIMEOUT_MIN_S, default)
            return default
        if v > _DB_TIMEOUT_MAX_S:
            logger.warning("%s=%r exceeds the %.0fs ceiling — clamping",
                           name, raw, _DB_TIMEOUT_MAX_S)
            return _DB_TIMEOUT_MAX_S
        return v

    return (_one("TORTOISE_FALKORDB_CONNECT_TIMEOUT_S", _DB_CONNECT_TIMEOUT_DEFAULT),
            _one("TORTOISE_FALKORDB_SOCKET_TIMEOUT_S", _DB_SOCKET_TIMEOUT_DEFAULT))




def _promotion_point_with_operator(p: dict) -> dict:
    """Restore operator-ness on an OperatorPromoted snapshot (P1 #2256).

    Promotion emitters journal ``point=get_point(id)`` — FLAT node props
    (``is_operator``/``op_type``/``direction`` at top level, NO nested
    ``operator`` key).  ``_upsert_point_props`` derives operator-ness from
    the NESTED key, so a flat-only upsert would write ``is_operator=false``
    + ``op_type=NULL`` and silently convert the operator to a claim node on
    rebuild.  Synthesize the canonical nested shape (mirroring the
    OperatorAdded snapshot) from the flat props when the nested key is
    absent — the upsert then restores the LIVE OPERATOR node faithfully.
    """
    if not isinstance(p, dict) or "operator" in p:
        return p
    if p.get("is_operator") and p.get("op_type"):
        out = dict(p)
        out["operator"] = {"op_type": p["op_type"],
                           "inputs": p.get("inputs") or []}
        return out
    return p

def _reset_falkordb_version_cache() -> None:
    """Test hook: drop all cached version probes."""
    _FALKORDB_VERSION_CACHE.clear()

# P0 guard (#99): bulk graph-wipe classifier. Restored here in #49 Phase 2 —
# the guard was lost from this (live) module during the v3.0 ontology rewrite
# (0f9e6a2) and only survived in the legacy standalone projection.py, which
# Phase 2 deletes. This is the ACTIVE wipe-protection: it must live in the
# module the SDK actually imports.
# A query is a bulk wipe when it contains DETACH DELETE but has NO property map
# ({...} — e.g. MATCH (n:Label {id:$id})) and NO real WHERE clause.
# A WHERE clause is "real" only if it references a property (n.xxx) or a
# parameter ($id) or CONTAINS/IN — tautologies (WHERE true, WHERE 1=1) don't count.
_WHERE_REAL_RE = re.compile(
    r"\b[a-zA-Z_][a-zA-Z0-9_]*\.[a-zA-Z_][a-zA-Z0-9_]*|\$[a-zA-Z_]|CONTAINS|IN\s*\(",
    re.IGNORECASE,
)


def _is_bulk_wipe(cypher: str) -> bool:
    up = cypher.upper()
    if "DETACH" not in up or "DELETE" not in up:
        return False
    # Property map in MATCH => targeted (e.g. {id:$id}, {org_id:$id})
    if "{" in cypher:
        return False
    # Real WHERE clause (property/param/CONTAINS/IN reference) => targeted
    m = re.search(r"WHERE\s+(.+) ", cypher + " ", re.IGNORECASE)
    if m and _WHERE_REAL_RE.search(m.group(1)):  # noqa: SIM103
        return False
    return True


# ── #2943: durable pre-wipe snapshot sidecar ────────────────────────────────
# #548 (graph-only Points) and #990 (:Batch quarantine markers + Point.batch_id
# links) each snapshot the live graph into an IN-MEMORY list immediately before
# `MATCH (n) DETACH DELETE n`, then restore from that list after replay. That
# list is the only record of nodes the JSONL does not carry — that is exactly
# what makes them graph-only. If the process dies between the wipe and the end
# of replay (exception, OOM, SIGKILL, timeout), the list dies with it and those
# nodes are gone for good: a retry re-reads the JSONL (by definition it has no
# events for them) and re-snapshots an already-empty graph. Permanent data
# loss on a recovery path (#2943).
#
# The fix is a durable sidecar written immediately before the wipe (after the
# JSONL has been parsed, per the WIPE-AFTER-PARSE pin) and removed only once
# replay completes. A later rebuild_all unions the leftover in (leftover wins),
# so an interrupted rebuild is recovered by simply re-running it; the embedded
# auto-recovery path (tortoise.consistency.recover_from_log) routes through
# rebuild_all while a sidecar is pending — still under its #428 single-log
# discriminator, because that route is a destructive wipe+replay.
#
# Why a sidecar and NOT appending the synthetic events to the .jsonl journal:
# replay consumes those events POSITIONALLY — `last_recreate_seq`,
# `operator_created_seq` and `max_inline_seq` are enumerate indices over the
# combined event list (synthetic events PREPENDED, #2488/#2423 fold sweeps),
# and synthetic-first ordering is what guarantees pass-1a nodes referenced by
# journal events exist. A journal append lands the events at the END of one
# file (and at an arbitrary position in the multi-file read order), which
# silently changes the supersede/invalidate survivor rules and the pass-2b
# re-point discriminator on the very run that needs them. Re-prepending from
# the sidecar keeps replay order byte-identical to the uninterrupted case.
_PREWIPE_SNAPSHOT_FILENAME = ".tortoise-prewipe-snapshot.json"
_PREWIPE_SNAPSHOT_VERSION = 4
# #2814: v2 adds the `config_snapshot` section. Reading v1 is required
# (backward compatibility): a rescue file written before this change carries
# no config record, and the union treats that exactly as the loader does —
# absent means empty (`data.get(key, [])`). The WRITE side is why the bump is
# not optional: the version is stamped by the CALLERS' payloads, not by
# `_write_prewipe_snapshot`, so without a bump a v1 build would accept this
# file and silently ignore `config_snapshot` while its wipe landed. With the
# bump that build REFUSES the rebuild instead (its `version != 1` check).
#
# #4641: v3 was picked for `onboarding_snapshot` / `onboarding_step_links`.
# The reasoning holds one increment on: WITHOUT a bump the writer would stamp
# `2`, and a v2 build would accept that file and ignore the onboarding
# sections while its wipe landed. A v1/v2 rescue file stays READABLE — it
# carries no onboarding record (the writing build did not capture the class),
# and the restore leg reports that as state-UNKNOWN rather than as a clean,
# empty restore.
#
# #4641 review round 8 — v4, NOT v3, because the format gate was made
# asymmetric by sibling contention. The version is a FORMAT gate, and THREE
# builds independently picked `3` for three different payloads: this change
# (the onboarding pair), the open #5327 (`event_meta`) and the open #5241
# (`graph_identity`). A section-set refusal inside ONE of them cannot make
# that gate symmetric: it stops a foreign-section v3 file from being consumed
# HERE, but a sibling that carries no such refusal still reads THIS build's
# file, walks only its own `_SNAPSHOT_SECTIONS`, ignores the onboarding
# sections it does not know, and lets its unconditional wipe land on the very
# class this change exists to preserve — the fail-open, in the direction that
# destroys data. Claiming a DISTINCT number closes it without depending on
# the siblings: every not-yet-updated build sees version 4 as unsupported and
# REFUSES the rebuild (fail-closed), instead of accepting the file and wiping
# over the onboarding class. v3 stays readable because a v3 file may be this
# build's own earlier write; the section-set refusal below still rejects a v3
# file carrying a foreign section (#2943, #4641).
#
# `_validate_prewipe_snapshot` therefore ALSO refuses a file carrying any
# section key outside this build's `_SNAPSHOT_SECTIONS`: a build that cannot
# restore a section must not wipe over it, whatever the version says. The two
# guards are complementary, and each covers the direction the other cannot:
# the section refusal rejects a foreign payload that claims a version we
# read; the distinct version makes a not-yet-updated build refuse OUR payload
# instead of accepting it and wiping over the sections it cannot see.
_PREWIPE_SNAPSHOT_READABLE_VERSIONS = (1, 2, 3, 4)
# Top-level keys that are METADATA, never a preserved class. The
# unknown-section refusal below subtracts these so it cannot mistake the
# envelope for a section.
_PREWIPE_SNAPSHOT_META_KEYS = frozenset(
    {"version", "created_at", "completed", "onboarding_unknown"})
# #3947 × #3010: `session_snapshot` / `session_point_links` join the durable
# sidecar for the same reason the #990 `:Batch` marker did — a `:Session`
# container and its CONTAINS edges are RAW graph writes on the capture path
# that ride no journal record on a pre-#3947 store, so the sidecar is their
# only durable record once the wipe lands. On the sidecar-RECOVERY path the
# live graph is already empty, so without them a retried rebuild recreates
# every turn Point but silently destroys every container and link.
_SNAPSHOT_SECTIONS = ("synthetic_events", "batch_snapshot",
                      "batch_point_links", "session_snapshot",
                      "session_point_links",
                      # #4641: the onboarding state machine. `:OnboardingState`
                      # node properties + `:OnboardingStep` / `COMPLETED_STEP`
                      # edges are RAW writes in `tortoise/onboarding/state.py`
                      # that ride NO journal record and are not re-derivable, so
                      # the sidecar is their only durable record once the wipe
                      # lands (the #2814/#3947 class). Enrolled as a SECTION
                      # pair rather than a `_config_classes()` row because the
                      # registry carries nodes with a single-property identity
                      # and `:OnboardingStep` is composite `(org_id, step_id)`
                      # with an edge — a node-only row would restore the node
                      # and silently drop the step edges.
                      "onboarding_snapshot", "onboarding_step_links",
                      # #2814: authoritative configuration. Enrolled here so it
                      # is validated before the wipe (:447) and so the
                      # retirement payload and `rebuild_all`'s write payload can
                      # be DERIVED from this tuple rather than re-listed.
                      "config_snapshot")
# The sidecar is read whole into memory before the wipe, so an unbounded file
# (a planted one especially — the log dir is caller-supplied) would exhaust
# memory on the recovery path. The cap is now WRITER-ENFORCED
# (`_write_prewipe_snapshot` serializes first and refuses a larger payload
# before the atomic replace), so the writer can never emit a sidecar the
# loader would reject — see the size invariant there.
_PREWIPE_SNAPSHOT_MAX_BYTES = 64 * 1024 * 1024
# Property values FalkorDB can store: primitives, or arrays of primitives.
# A sidecar carrying anything else passes a shape check but then dies INSIDE
# the driver, after the wipe (`ResponseError: Property values can only be of
# primitive types`), which is exactly the post-wipe failure this validator
# exists to prevent.
_PRIMITIVE_TYPES = (str, int, float, bool, type(None))


def _is_snapshot_primitive(value) -> bool:
    """A FalkorDB-storable property value: primitive, or array of those."""
    if isinstance(value, _PRIMITIVE_TYPES):
        return True
    if isinstance(value, (list, tuple)):
        return all(isinstance(v, _PRIMITIVE_TYPES) for v in value)
    return False


# ── #2814: authoritative-configuration durability ────────────────────────────
# The rebuild wipe (`MATCH (n) DETACH DELETE n`, :3197) is unconditional and
# only the journal is replayed. Some node classes are GRAPH-RESIDENT, ride NO
# journal record, and are not re-derivable — so before this change a rebuild
# silently reverted a configured graph to defaults, and the self-healing
# starter-pack read path masked it (`get_tenant_packs` → `ensure_tenant_packs`
# re-provisions the starter rows, so the graph looked freshly provisioned
# rather than empty; see `tests/tool_surface_capabilities.py`
# READ_THROUGH_WRITE_METHODS).
#
# The registry below is the DECLARED durability disposition of those classes:
# what the pre-wipe capture reads, what the post-replay restore writes back,
# and what `docs/durability-posture.md` must agree with (pinned bidirectionally
# by tests/test_rebuild_config_preservation.py).
#
# It is explicitly NOT a completeness gate: a class nobody ever enrolled still
# recurs, and closing that class-wide hole is #2296 (dormant — see the plan's
# §7). What it buys is that the classes this change names cannot silently
# de-enrol.
_CONFIG_RESET_KEY = "config_reset"
_CONFIG_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class _ConfigClass(NamedTuple):
    """One authoritative class the config sidecar preserves.

    ``label``         — the node label, taken from the DOMAIN constant (never
                        re-typed: a rename must not silently de-enrol the
                        class).
    ``identity_prop`` — the property identifying an instance within the label.
    ``keys``          — for a key-scoped ``:Meta`` class, the declared keys;
                        ``None`` means the label itself is the scope.
    """
    label: str
    identity_prop: str
    keys: frozenset[str] | None = None


# Populated INSIDE `_config_classes()` as a side effect (it starts EMPTY at
# module scope, deliberately — see the import-cycle note on that function).
_CONFIG_CLASS_BY_LABEL: dict[str, _ConfigClass] = {}
_config_classes_cache: tuple[_ConfigClass, ...] | None = None


def _assert_config_registry_safe() -> None:
    """Fail loudly on an unsafe or incoherent DECLARATION.

    Runs on the first `_config_classes()` call — the load/validate path and the
    capture path, both of which are PRE-wipe. Never at import time (see
    `_config_classes`).

    Labels and identity-property names are interpolated into Cypher, so both
    must be safe identifiers. An empty `:Meta` key set would silently turn the
    capture into a LABEL-WIDE read of `:Meta` — which would sweep the DERIVED
    `point_fts_v2`/`event_fts_v2` markers into the config section and restore
    them as if they were authoritative. And a section that is in
    `_SNAPSHOT_SECTIONS` without an entry check KeyErrors inside validation,
    which runs before the wipe but after the file was trusted enough to read.
    """
    for spec in _CONFIG_CLASS_BY_LABEL.values():
        if not _CONFIG_IDENTIFIER_RE.match(spec.label):
            raise RuntimeError(
                f"config registry declares label {spec.label!r}, which is not "
                f"a safe Cypher identifier (#2814)"
            )
        if not _CONFIG_IDENTIFIER_RE.match(spec.identity_prop):
            raise RuntimeError(
                f"config registry declares identity property "
                f"{spec.identity_prop!r} for {spec.label!r}, which is not a "
                f"safe Cypher identifier (#2814)"
            )
        if spec.keys is not None:
            if not spec.keys:
                raise RuntimeError(
                    f"config registry declares {spec.label!r} with an empty "
                    f"key set — that would make the capture a label-wide read "
                    f"of the label, sweeping derived markers into the config "
                    f"section (#2814)"
                )
            for key in spec.keys:
                if not isinstance(key, str) or not key:
                    raise RuntimeError(
                        f"config registry declares Meta key {key!r} for "
                        f"{spec.label!r}, which is not a non-empty string "
                        f"(#2814)"
                    )
    if set(_SNAPSHOT_SECTIONS) != set(_SNAPSHOT_ENTRY_CHECK):
        raise RuntimeError(
            "_SNAPSHOT_SECTIONS and _SNAPSHOT_ENTRY_CHECK disagree "
            f"({sorted(set(_SNAPSHOT_SECTIONS) ^ set(_SNAPSHOT_ENTRY_CHECK))}) "
            "— a section with no entry check would KeyError inside pre-wipe "
            "validation (#2814)"
        )
    if "config_snapshot" not in _SNAPSHOT_SECTIONS:
        raise RuntimeError(
            "the config registry exists but `config_snapshot` is not in "
            "_SNAPSHOT_SECTIONS — the capture would never be validated or "
            "written (#2814)"
        )


def _config_classes() -> tuple[_ConfigClass, ...]:
    """The declared config registry — the ONLY accessor (memoised).

    ⚠️ FUNCTION-LOCAL imports, and no module-scope binding or call. The three
    domain constants live behind modules that import THIS one at module level —
    `sdk.py` does `from .projection import FalkorProjection`, and
    `pack_state.py` does `from tortoise.sdk import TortoiseSDK` (with
    `pack_manifest_store.py` importing `pack_state`) — so a module-scope
    `_config_classes()` call, or a module-scope read of the constants, is the
    cycle projection → pack_state → sdk → projection. Both forms are
    ImportError (reproduced: `import tortoise.sdk` and
    `import tortoise.pack_manifest_store` break; `import tortoise.projection`
    alone still succeeds, which is why the cycle is easy to miss).
    `rebuild_all` already uses function-local imports for the same reason.

    `_assert_config_registry_safe()` runs on FIRST call, i.e. on the
    load/validate path or the capture path — never at import.
    """
    global _config_classes_cache
    if _config_classes_cache is None:
        # #5148 review — DO NOT guard these imports, and do NOT degrade to an
        # empty tuple on failure. They reach `tortoise.embeddings` transitively
        # (`pack_state` -> `sdk` -> `cross_lens`), and the failure propagating
        # is LOAD-BEARING: `_capture_config_snapshot` calls this unconditionally
        # and lets the exception reach `rebuild_all`'s `capture_failed` gate,
        # which raises BEFORE the wipe (#2943). Returning `()` here would
        # silence that gate, let the wipe proceed, and destroy every graph-only
        # config node with no durable record — turning a safe refusal into
        # silent data loss. The fail-soft discipline applied to the WRITE and
        # REPLAY seams is CORRECT THERE and WRONG HERE: there the failure is
        # advisory or a single write, here it is the only proof the wipe is
        # safe.
        from tortoise.pack_manifest_store import PACK_MANIFEST_LABEL
        from tortoise.pack_state import PACK_INSTALL_LABEL
        from tortoise.sdk import TortoiseSDK

        classes = (
            _ConfigClass(PACK_INSTALL_LABEL, "namespace"),
            _ConfigClass(PACK_MANIFEST_LABEL, "namespace"),
            # `:Meta` is SHARED with derived markers (the FTS keys), so this
            # class is key-scoped: a label-wide read would sweep them in.
            _ConfigClass("Meta", "key", frozenset({
                TortoiseSDK._CALIBRATION_MARKER_KEY, _CONFIG_RESET_KEY})),
        )
        # `_CONFIG_CLASS_BY_LABEL` is a side effect so the validator and the
        # restore path can resolve a label without repeating the import dance.
        _CONFIG_CLASS_BY_LABEL.clear()
        _CONFIG_CLASS_BY_LABEL.update({c.label: c for c in classes})
        _config_classes_cache = classes
        _assert_config_registry_safe()
    return _config_classes_cache


def _config_key(entry) -> tuple[str, str]:
    """The union key for a config entry: ``(label, identity-`` ``value)``.

    NOT the identity value alone. The section is flat, so entries of different
    labels share one key space — with an identity-only key,
    `PackInstall{namespace:'x'}` and `Meta{key:'x'}` would collide and
    `_merge_entry` would keep the left entry's label while merging the other's
    properties, silently dropping the `:Meta` entry and writing a foreign
    property onto the pack node.
    """
    spec = _CONFIG_CLASS_BY_LABEL.get(entry.get("label"))
    if spec is None:
        # Populate before concluding a label is undeclared. The map starts
        # EMPTY (see `_config_classes`), so without this an unloaded registry
        # would key every entry of a declared label as `(label, None)` and
        # collapse them all into one — silently dropping config entries in the
        # union. Memoised, so this costs nothing once loaded.
        _config_classes()
        spec = _CONFIG_CLASS_BY_LABEL.get(entry.get("label"))
    if spec is None:
        return (entry.get("label"), None)
    return (entry.get("label"), entry.get("props", {}).get(spec.identity_prop))


def _safe_config_key(entry) -> tuple | None:
    """`_config_key` for untrusted input — None when it cannot be keyed.

    The union runs on a PLANTED sidecar as well as a captured one, so a
    non-dict entry must not raise here; it is kept un-deduplicated and the
    pre-wipe validator is what refuses it.
    """
    try:
        return _config_key(entry)
    except (AttributeError, TypeError):
        return None


def _validate_config_entry(entry) -> str | None:
    """Return a complaint about a ``config_snapshot`` entry, else None.

    Called once per entry during pre-wipe validation, so this is also where the
    registry gets populated (`_config_classes()`, memoised) — meaning an empty
    section never triggers the function-local imports at all.
    """
    _config_classes()
    if not isinstance(entry, dict):
        return f"not an object ({type(entry).__name__})"
    label = entry.get("label")
    if not isinstance(label, str):
        return f"label {label!r} is not a string"
    spec = _CONFIG_CLASS_BY_LABEL.get(label)
    if spec is None:
        return (f"label {label!r} is not a declared config class "
                f"({sorted(_CONFIG_CLASS_BY_LABEL)})")
    props = entry.get("props")
    if not isinstance(props, dict):
        return f"props is {type(props).__name__}, expected object"
    identity = props.get(spec.identity_prop)
    if not isinstance(identity, str):
        return f"{spec.identity_prop} {identity!r} is not a string"
    if spec.keys is not None and identity not in spec.keys:
        return (f"{spec.identity_prop} {identity!r} is not one of the declared "
                f"keys {sorted(spec.keys)}")
    for key, value in props.items():
        if not _is_snapshot_primitive(value):
            return (f"property {key!r} value {value!r} is not a primitive "
                    f"or an array of primitives")
    return None


def _config_capture_query(spec: _ConfigClass) -> str:
    """The capture Cypher for one declared class — REGISTRY LITERALS ONLY.

    Assembled from the declaration (whose label and identity property
    `_assert_config_registry_safe` has already vetted as safe identifiers); an
    identity value or property map is never spliced in — the restore
    `$`-binds those. A key-scoped class filters on the declared keys, which is
    what keeps the derived `:Meta` markers (the FTS keys) out.
    """
    if spec.keys is None:
        return f"MATCH (n:{spec.label}) RETURN properties(n)"
    return (f"MATCH (n:{spec.label}) WHERE n.{spec.identity_prop} IN $keys "
            f"RETURN properties(n)")


def _config_reset_props(existing: dict | None, reason: str) -> dict:
    """The marker's property map — sticky and monotonic on ONE node.

    `at` is the first-set time and survives every re-set; `last_at`/`count`
    advance, so an accumulation of rebuilds is visible as a count on a single
    marker node rather than as many nodes (which is why `MERGE` is on the key
    alone, and why `count` is read back rather than assumed to be 1).
    """
    now = datetime.now(timezone.utc).isoformat()  # noqa: UP017
    prior = existing or {}
    count = prior.get("count")
    return {
        "key": _CONFIG_RESET_KEY,
        "at": prior.get("at") or now,
        "last_at": now,
        "count": count + 1 if isinstance(count, int) else 1,
        "reason": reason,
    }


def read_config_reset(g) -> dict | None:
    """The `config_reset` marker's properties, or None when never set.

    The operator-facing read: `None` means "no reset recorded", which is
    different from "config is known to have been wiped" (see
    `reason='legacy_sidecar_no_config_record'` — a state-UNKNOWN signal).
    """
    rows = g.query(
        "MATCH (n:Meta {key:$key}) RETURN properties(n)",
        params={"key": _CONFIG_RESET_KEY},
    ).result_set
    if not rows:
        return None
    props = rows[0][0]
    return props if isinstance(props, dict) and props else None


def set_config_reset_marker(g, reason: str) -> dict:
    """Record the third state — `rebuild_all` never calls this to CLEAR it.

    A keyed `:Meta` node (the repo's existing `:Meta{key:…}` idiom), not an
    inferred absence: absence is also what a never-configured graph looks
    like, so the two must be distinguishable. Deliberately NOT called a
    "tombstone" — that is a controlled `docs/ONTOLOGY.md` term for a retracted
    Point.
    """
    props = _config_reset_props(read_config_reset(g), reason)
    g.query(
        "MERGE (n:Meta {key:$key}) SET n += $props",
        params={"key": _CONFIG_RESET_KEY,
                "props": {k: v for k, v in props.items() if k != "key"}},
    )
    return props


def clear_config_reset(g) -> bool:
    """The operator's explicit clear (wired: `TortoiseSDK._clear_config_reset`).

    Returns whether a marker was there. The marker is sticky by design — only
    this call clears it — so the CLI and the rebuild runbook both name it.
    A keyed MATCH+DETACH DELETE: targeted, so `_is_bulk_wipe` (a bare
    label-less wipe detector) leaves it alone.
    """
    existed = read_config_reset(g) is not None
    if existed:
        g.query(
            "MATCH (n:Meta {key:$key}) DETACH DELETE n",
            params={"key": _CONFIG_RESET_KEY},
        )
    return existed


def _capture_config_snapshot(g) -> list[dict]:
    """Read every declared config class into flat section entries.

    Entry shape is ``{"label": str, "props": dict}`` (#2814 decision (a)):
    one flat section, so adding a class to the registry costs one tuple entry
    rather than one new section across ten sites.

    Any failure propagates: the caller funnels it into the ``capture_failed``
    gate, so a graph that cannot answer this read NEVER reaches the wipe (the
    #2943 discipline — a corrupt/heavy read failing while the light DELETE
    succeeds would otherwise destroy the config with no durable record).
    """
    entries: list[dict] = []
    seen: set[tuple] = set()
    for spec in _config_classes():
        params = {"keys": sorted(spec.keys)} if spec.keys is not None else None
        rows = g.query(_config_capture_query(spec), params).result_set
        for row in rows or []:
            props = row[0] if not isinstance(row, dict) else row
            if not isinstance(props, dict) or not props:
                continue
            identity = props.get(spec.identity_prop)
            if not isinstance(identity, str):
                # An identity-less instance cannot be addressed for restore;
                # keep it out of the section rather than capture something the
                # restore could not write back (it is not authoritative config).
                continue
            key = (spec.label, identity)
            if key in seen:
                continue
            seen.add(key)
            entries.append({"label": spec.label, "props": props})
    return entries


def _capture_onboarding_snapshot(g) -> tuple[list[dict], list[tuple[str, str]]]:
    """Read the onboarding state machine into sidecar section entries (#4641).

    Returns ``(nodes, links)``:

    * ``nodes`` — one entry per `:OnboardingState`, the property map itself
      (the ``batch_snapshot`` / ``session_snapshot`` shape), keyed by
      ``org_id``.
    * ``links`` — ``(org_id, step_id)`` pairs for every `COMPLETED_STEP` edge.

    The labels / edge type come from the DOMAIN module (never re-typed — a
    rename must not silently de-enrol the class).

    #4641 review round 4: the step-id set is deliberately NOT filtered. An
    earlier version restricted ``links`` to the canonical
    ``ONBOARDING_STEPS``, reasoning that a foreign id carried no gate
    semantics. That was backwards and unsafe: the gates
    (``resolve_wire_completion`` / ``recompute_completion``) count an
    unrecognised id as an AGENT step, so while the edge exists it BLOCKS the
    grandfathered completion, and DROPPING it can CREATE that completion —
    the exact forgery this capture exists to prevent. Every `COMPLETED_STEP`
    edge the live graph holds is therefore captured verbatim — and one whose
    endpoints are not both strings makes the capture REFUSE rather than drop
    it, so the guarantee is "carried or refused", never "silently lost".

    The pair is ``(parent state org_id, step_id)`` — the step node's OWN
    ``org_id`` is not read, and the restore re-keys the step onto its parent.
    That is faithful to every WRITER (`write_completed_step` MERGEs
    ``s.org_id`` from the parent in the same statement) and to the
    edge-traversing readers (`completed_steps`, `decide_completed_edge_exists`
    — which match through the parent edge), so the re-key is a no-op on any
    graph a writer produced. It is NOT a no-op for
    `_prune_orphan_decide_step`, the one reader that keys the step node's own
    ``org_id`` with no parent constraint (``{org_id, step_id:'decide-completed'}``):
    a raw/hand-edited graph whose step ``org_id`` DIVERGES from its parent's is
    re-keyed onto the parent, and that reader would no longer find it under
    its original org. That divergence is the documented residual in
    `docs/durability-posture.md` (#4641).

    An orphan `:OnboardingStep` node (no `COMPLETED_STEP` edge) is not
    captured: it is the ``_prune_orphan_decide_step`` cleanup's residue, it
    carries no properties beyond its `{org_id, step_id}` key, and nothing
    reads it — the edge set is the record.

    Any failure propagates: the caller funnels it into the ``capture_failed``
    gate, so a graph that cannot answer this read NEVER reaches the wipe —
    the same #2943 discipline the config capture documents.
    """
    from tortoise.onboarding.state import (
        COMPLETED_STEP_EDGE,
        ONBOARDING_NODE_LABEL,
        ONBOARDING_STEP_LABEL,
    )

    node_rows = g.query(
        f"MATCH (n:{ONBOARDING_NODE_LABEL}) RETURN properties(n)"
    ).result_set
    nodes = []
    for row in node_rows or []:
        props = row[0]
        # The capture's output MUST be loader-acceptable: a non-str `org_id`
        # (or a non-storable property) would be written into the rescue file
        # and then make it UNLOADABLE on the retry, so the sidecar — the only
        # durable record of EVERY graph-only class it carries — would be
        # refused and auto-recovery blocked until an operator deleted it.
        # Failing closed here (the caller funnels the raise into the
        # `capture_failed` gate) aborts BEFORE the wipe instead, with the bad
        # node still in place to be repaired (#2943, #4641).
        # Bind the value BEFORE formatting: the non-dict arm of the guard
        # would otherwise make the f-string raise AttributeError instead of
        # reporting the offending node it exists to name (#4641 review
        # round 6).
        _org_value = props.get("org_id") if isinstance(props, dict) else None
        if not isinstance(props, dict) or not isinstance(_org_value, str):
            raise RuntimeError(
                f"an :{ONBOARDING_NODE_LABEL} node cannot survive a rebuild "
                f"round-trip (org_id={_org_value!r} is not a string) — "
                f"writing it would make the pre-wipe snapshot unloadable on "
                f"the retry")
        for key, value in props.items():
            if not _is_snapshot_primitive(value):
                raise RuntimeError(
                    f"an :{ONBOARDING_NODE_LABEL} node (org_id="
                    f"{props.get('org_id')!r}) carries property {key!r} = "
                    f"{value!r}, which is not storable — writing it would "
                    f"make the pre-wipe snapshot unloadable on the retry")
        nodes.append(props)
    link_rows = g.query(
        f"MATCH (n:{ONBOARDING_NODE_LABEL})"
        f"-[:{COMPLETED_STEP_EDGE}]->(s:{ONBOARDING_STEP_LABEL}) "
        "RETURN n.org_id, s.step_id"
    ).result_set
    # The step id is NOT filtered to the canonical vocabulary: the earlier
    # "drop the foreign ones, they are inert" reasoning was WRONG in the
    # direction that matters. In `tortoise/onboarding/state.py`,
    # `resolve_wire_completion` and `recompute_completion` compute
    # `agent_steps = [s for s in done if s not in _NON_AGENT_STEPS]` and
    # require `not agent_steps`. An unrecognised id is NOT in
    # `_NON_AGENT_STEPS`, so it counts as an AGENT step: while the edge is
    # PRESENT it BLOCKS the grandfathered completion, and DROPPING it empties
    # `agent_steps` and can therefore FORGE that completion
    # (`resolve_wire_completion('active', True, ['made-up-step'])` is False;
    # with the edge gone it is True).
    #
    # A NON-STR `step_id` FAILS CLOSED instead of being dropped (round 5): a
    # silent drop is the same forgery — the post-restore check reads through
    # this same capture, so the loss would be invisible and the run would
    # report a clean, fully-verified restore. The tripwire mirrors the
    # `org_id` guard above: abort BEFORE the wipe with the bad edge still in
    # place to be repaired. The pair must also be loader-acceptable
    # (`_validate_onboarding_step_link` requires two strings).
    links = []
    for row in link_rows or []:
        oid, sid = row[0], row[1]
        if not isinstance(oid, str) or not isinstance(sid, str):
            raise RuntimeError(
                f"a {COMPLETED_STEP_EDGE} edge cannot survive a rebuild "
                f"round-trip (org_id={oid!r}, step_id={sid!r} — both must "
                f"be strings) — carrying it would make the pre-wipe "
                f"snapshot unloadable on the retry, and DROPPING it could "
                f"forge a grandfathered onboarding completion (#4641)")
        links.append((oid, sid))
    return nodes, links


def _validate_point_entry(entry) -> str | None:
    """Return a complaint about a ``synthetic_events`` entry, else None.

    Shape AND value types are checked, and the ``type`` must be one the
    replay dispatches on: an unknown type is silently skipped by EVERY pass
    (pass 1a, 1b and 2 all filter on it), so the sidecar would be cleared
    after a wipe that restored nothing. `operator.inputs` is the one nested
    structure the capture writes; every other value must be storable.
    """
    if not isinstance(entry, dict):
        return f"not an object ({type(entry).__name__})"
    if entry.get("type") not in ("PointAdded", "OperatorAdded"):
        return (f"type {entry.get('type')!r} is not one the replay handles "
                f"(PointAdded/OperatorAdded)")
    point = entry.get("point")
    if not isinstance(point, dict):
        return f"point is {type(point).__name__}, expected object"
    if not isinstance(point.get("id"), str):
        return f"point id {point.get('id')!r} is not a string"
    for key, value in point.items():
        if key == "operator":
            if not isinstance(value, dict):
                return f"operator is {type(value).__name__}, expected object"
            inputs = value.get("inputs")
            if inputs is not None and not (
                    isinstance(inputs, (list, tuple))
                    and all(isinstance(v, str) for v in inputs)):
                return f"operator inputs {inputs!r} is not a list of strings"
            # Every OTHER operator value is written to the node as well
            # (`n.op_type=$opt`), so it must be storable too.
            for okey, ovalue in value.items():
                if okey != "inputs" and not _is_snapshot_primitive(ovalue):
                    return (f"operator.{okey} value {ovalue!r} is not a "
                            f"primitive or an array of primitives")
            continue
        if not _is_snapshot_primitive(value):
            return (f"property {key!r} value {value!r} is not a primitive "
                    f"or an array of primitives")
    return None


def _validate_batch_entry(entry) -> str | None:
    """Return a complaint about a ``batch_snapshot`` entry, else None."""
    if not isinstance(entry, dict):
        return f"not an object ({type(entry).__name__})"
    if not isinstance(entry.get("id"), str):
        return f"batch id {entry.get('id')!r} is not a string"
    for key, value in entry.items():
        if not _is_snapshot_primitive(value):
            return (f"property {key!r} value {value!r} is not a primitive "
                    f"or an array of primitives")
    return None


def _validate_session_entry(entry) -> str | None:
    """Return a complaint about a ``session_snapshot`` entry, else None.

    A `:Session` container is the same shape as a `:Batch` marker (a str
    ``id`` plus primitive properties), so this mirrors
    ``_validate_batch_entry`` with the container's own label in the message.
    """
    if not isinstance(entry, dict):
        return f"not an object ({type(entry).__name__})"
    if not isinstance(entry.get("id"), str):
        return f"session id {entry.get('id')!r} is not a string"
    for key, value in entry.items():
        if not _is_snapshot_primitive(value):
            return (f"property {key!r} value {value!r} is not a primitive "
                    f"or an array of primitives")
    return None


def _validate_link_entry(entry) -> str | None:
    """Return a complaint about a link entry, else None.

    Used by BOTH link sections — ``batch_point_links`` (#990) and
    ``session_point_links`` (#3947 × #3010, via ``_SNAPSHOT_ENTRY_CHECK``).
    The restore loops consume the two values as Cypher NODE IDS (MATCH keys)
    and/or property values — so a longer entry (or a non-string member) must
    not reach the driver.
    """
    if not isinstance(entry, (list, tuple)) or len(entry) != 2:
        return f"{entry!r} is not a 2-element list"
    if not all(isinstance(v, str) for v in entry):
        return f"{entry!r} is not a pair of strings"
    return None


def _validate_onboarding_entry(entry) -> str | None:
    """Return a complaint about an ``onboarding_snapshot`` entry, else None.

    The entry is the `:OnboardingState` property map itself (the
    `batch_snapshot` / `session_snapshot` shape), keyed by ``org_id`` — the
    identity every onboarding writer uses. Checking the props here is what
    keeps a non-storable value (a planted sidecar's, or one the graph somehow
    holds) from reaching the driver AFTER the wipe.
    """
    if not isinstance(entry, dict):
        return f"not an object ({type(entry).__name__})"
    if not isinstance(entry.get("org_id"), str):
        return f"org_id {entry.get('org_id')!r} is not a string"
    for key, value in entry.items():
        if not _is_snapshot_primitive(value):
            return (f"property {key!r} value {value!r} is not a primitive "
                    f"or an array of primitives")
    return None


def _validate_onboarding_step_link(entry) -> str | None:
    """Return a complaint about an ``onboarding_step_links`` entry, else None.

    Shape only — a 2-element pair of strings, exactly like the other link
    sections. There is deliberately NO vocabulary-membership check.

    #4641 review round 4: an earlier version rejected a ``step_id`` outside
    ``ONBOARDING_STEPS``, on the reasoning that such an id was inert. It is
    not, and rejecting it was a data-loss path that could FORGE a completion:
    the gates in ``tortoise/onboarding/state.py``
    (``resolve_wire_completion`` / ``recompute_completion``) compute
    ``agent_steps = [s for s in done if s not in _NON_AGENT_STEPS]`` and
    require ``not agent_steps`` — an unrecognised id is an AGENT step, so
    while its edge exists it BLOCKS the grandfathered completion, and a
    rebuild that dropped it would UNBLOCK (create) it. Preserving every edge
    the live graph held is therefore the fail-safe choice; the vocabulary is
    the domain module's business, not this validator's. (The values stay
    `$`-bound and the labels stay module constants, so relaxing this cannot
    inject Cypher.)
    """
    return _validate_link_entry(entry)


_SNAPSHOT_ENTRY_CHECK = {
    "synthetic_events": _validate_point_entry,
    "batch_snapshot": _validate_batch_entry,
    "batch_point_links": _validate_link_entry,
    "session_snapshot": _validate_session_entry,
    "session_point_links": _validate_link_entry,
    "onboarding_snapshot": _validate_onboarding_entry,
    "onboarding_step_links": _validate_onboarding_step_link,
    "config_snapshot": _validate_config_entry,
}
# Node properties a snapshot Point carries that the replay does not fully
# reconstruct, restored by the pass-1b tail.
#   * ``outdated``/``expiredAt``/``posterior_alpha``/``posterior_beta`` — not in
#     `_upsert_point_props`'s fixed SET list; without them an invalidated Point
#     comes back EP-live (#2488 ghost class). Restored VERBATIM, and only for a
#     graph-only (synthetic) id — the original #2943 scope.
#   * ``content_hash`` — the DERIVED dedup key (#2795/#2971). The replay writer
#     writes it CONDITIONALLY (`coalesce($ch, n.content_hash)`, deriving no
#     value for falsy content), so the tail restores it for ANY id — synthetic
#     OR log-covered — but ONLY when no journal event owned the field. The
#     gate treats a journaled write as the newer writer; that is the design
#     assumption, and it is ORDER-BLIND for an UNJOURNALED write that
#     postdates a journaled one (the snapshot is then newer) — a filed
#     residual (#4252), deliberate here because the alternative re-breaks the
#     issue's shape H.
# Widening the SET list itself belongs to #2948/#2958 — this keeps the repair
# inside the #2943 sidecar path.
_REPLAY_GAP_PROPS = ("outdated", "expiredAt", "posterior_alpha",
                     "posterior_beta", "content_hash")


def prewipe_snapshot_path(log_dir: str) -> str:
    """Durable #548/#990 pre-wipe snapshot path for an event-log directory."""
    return os.path.join(log_dir, _PREWIPE_SNAPSHOT_FILENAME)


def _validate_prewipe_snapshot(data: dict, path: str) -> None:
    """Reject a sidecar whose shape the replay could not safely consume.

    Section types, entry shapes AND property value types are all checked. An
    entry-shape or value-type defect would otherwise surface as
    AttributeError/ValueError/ResponseError in the restore loop AFTER
    `DETACH DELETE` — i.e. after the wipe the validator exists to prevent (a
    non-primitive value reaches the driver, and an unknown event type is
    skipped by every pass, so the sidecar gets cleared with nothing restored).
    """
    version = data.get("version")
    if version is not None and version not in _PREWIPE_SNAPSHOT_READABLE_VERSIONS:
        raise RuntimeError(
            f"a pre-wipe snapshot at {path} carries unsupported version "
            f"{version!r} (this build reads "
            f"{list(_PREWIPE_SNAPSHOT_READABLE_VERSIONS)}) — "
            f"refusing to wipe the graph (#2943). Migrate or delete the file."
        )
    # #4641 review round 6: a section this build cannot restore is a REFUSAL,
    # whatever the version says. The version is a FORMAT gate, not a
    # SECTION-SET gate: two sibling builds can claim the same version with
    # different section sets, and a same-version file is then accepted while
    # its unknown section is never read (the loop below walks only
    # `_SNAPSHOT_SECTIONS` and the union reads only known keys) — so the wipe
    # lands and the class that section carried is destroyed silently. That is
    # precisely the fail-open the version bump exists to prevent, one level
    # down.
    unknown_sections = sorted(
        set(data) - set(_SNAPSHOT_SECTIONS) - _PREWIPE_SNAPSHOT_META_KEYS)
    if unknown_sections:
        raise RuntimeError(
            f"a pre-wipe snapshot at {path} carries section(s) "
            f"{unknown_sections} this build cannot restore (it reads "
            f"{list(_SNAPSHOT_SECTIONS)}) — refusing to wipe the graph "
            f"(#2943, #4641): the wipe is unconditional and only the journal "
            f"is replayed, so ignoring an unknown section would destroy the "
            f"class it carries. Land the build that carries it, or delete "
            f"the file to accept the loss."
        )
    for key in _SNAPSHOT_SECTIONS:
        section = data.get(key, [])
        if not isinstance(section, list):
            raise RuntimeError(
                f"a pre-wipe snapshot at {path} has a malformed {key!r} "
                f"section ({type(section).__name__}, expected list) — "
                f"refusing to wipe the graph (#2943). Repair or delete the "
                f"file."
            )
        check = _SNAPSHOT_ENTRY_CHECK[key]
        for entry in section:
            complaint = check(entry)
            if complaint is None:
                continue
            raise RuntimeError(
                f"a pre-wipe snapshot at {path} has a malformed {key!r} "
                f"entry ({entry!r}): {complaint} — refusing to wipe the graph "
                f"(#2943). Repair or delete the file."
            )


def _load_prewipe_snapshot(path: str) -> dict | None:
    """Read a pending pre-wipe snapshot; None when there is none.

    Raises RuntimeError when a sidecar EXISTS but cannot be trusted: it may be
    the only surviving record of an interrupted rebuild's graph-only nodes, so
    silently ignoring it and wiping anyway would turn a repairable situation
    into permanent loss. The caller aborts BEFORE the wipe.

    Opened with ``O_NOFOLLOW`` and no separate existence probe: the log
    directory is caller-supplied and may be shared, so a planted symlink must
    not be followed (and ``os.path.exists`` + ``open`` is a TOCTOU on its own).
    For the same reason the descriptor must be a REGULAR file of bounded size:
    a planted FIFO makes a plain ``O_RDONLY`` open block forever (recovery
    runs on every embedded DB open, so that would hang every opener), a
    planted device reader never ends, and an oversized file exhausts memory —
    all of it before any guard or wipe.
    """
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                     | getattr(os, "O_NONBLOCK", 0))
    except FileNotFoundError:
        return None
    except OSError as e:
        raise RuntimeError(
            f"a pre-wipe snapshot from an interrupted rebuild exists at "
            f"{path} but cannot be opened ({e}; a symlink is refused). It may "
            f"be the only surviving record of that rebuild's graph-only "
            f"Points, so this rebuild refuses to wipe the graph (#2943). "
            f"Repair the file, or delete it to accept the loss and rebuild "
            f"from the JSONL alone."
        ) from e
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            reason = (f"is not a regular file (mode "
                      f"{stat.S_IFMT(st.st_mode):#o})")
        elif st.st_size > _PREWIPE_SNAPSHOT_MAX_BYTES:
            reason = (f"is absurdly large ({st.st_size} bytes > "
                      f"{_PREWIPE_SNAPSHOT_MAX_BYTES})")
        else:
            reason = None
        if reason is not None:
            os.close(fd)
            raise RuntimeError(
                f"a pre-wipe snapshot from an interrupted rebuild exists at "
                f"{path} but {reason} — refusing to wipe the graph (#2943). "
                f"It may be the only surviving record of that rebuild's "
                f"graph-only Points; inspect it before proceeding."
            )
        with os.fdopen(fd, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as e:
        raise RuntimeError(
            f"a pre-wipe snapshot from an interrupted rebuild exists at "
            f"{path} but cannot be read ({e}). It may be the only surviving "
            f"record of that rebuild's graph-only Points, so this rebuild "
            f"refuses to wipe the graph (#2943). Repair the file, or delete "
            f"it to accept the loss and rebuild from the JSONL alone."
        ) from e
    if not isinstance(data, dict):
        raise RuntimeError(
            f"a pre-wipe snapshot from an interrupted rebuild exists at "
            f"{path} but is not a JSON object ({type(data).__name__}) — "
            f"refusing to wipe the graph (#2943). Repair or delete the file."
        )
    _validate_prewipe_snapshot(data, path)
    if not any(data.get(key) for key in _SNAPSHOT_SECTIONS):
        # A retired sidecar (see _clear_prewipe_snapshot) — entry-less by
        # construction, so there is nothing to merge and nothing to keep.
        return None
    return data


def _write_prewipe_snapshot(path: str, payload: dict) -> None:
    """Atomically + durably persist the pre-wipe snapshot.

    ``mkstemp`` in the target directory (unpredictable name, mode 0600,
    never following a planted symlink) + file fsync + ``os.replace`` +
    best-effort DIRECTORY fsync: on POSIX the rename is not durable until the
    containing directory is synced, so a host crash would otherwise reopen
    the very window this file exists to close.

    WRITER-ENFORCED SIZE INVARIANT — output ⊆ loader-acceptable. The loader
    hard-refuses a sidecar larger than ``_PREWIPE_SNAPSHOT_MAX_BYTES``, so a
    writer that could exceed it would, after an interrupted rebuild, produce
    the ONLY record of what the wipe destroyed in a form the loader will never
    accept: every later rebuild/recovery refuses, and the operator's only exit
    is to delete the rescue file and lose the graph-only Points / :Batch
    markers / :Session containers. The payload is therefore serialized FIRST
    and refused (``ValueError``) BEFORE the atomic replace — hence before the
    wipe; ``rebuild_all``'s ``except (OSError, TypeError, ValueError)`` turns
    that into its "aborted BEFORE the graph wipe" refusal.
    """
    directory = os.path.dirname(os.path.abspath(path)) or "."
    serialized = json.dumps(payload, ensure_ascii=False)
    size = len(serialized.encode("utf-8"))
    if size > _PREWIPE_SNAPSHOT_MAX_BYTES:
        raise ValueError(
            f"pre-wipe snapshot payload is {size} bytes, over the "
            f"{_PREWIPE_SNAPSHOT_MAX_BYTES}-byte loader cap — refusing to "
            f"write a rescue file the loader would reject")
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".tortoise-prewipe-",
                               suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(serialized)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    try:
        dir_fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except OSError:
        pass  # best-effort — not every platform/filesystem supports it


def _clear_prewipe_snapshot(path: str) -> None:
    """Retire the sidecar after a completed replay.

    The file is first REWRITTEN entry-less and only then removed, so neither a
    crash between the two steps nor a failed unlink can leave pre-wipe truth
    on disk: the next rebuild's union would otherwise re-merge it and roll
    back state that changed after this rebuild — resurrecting nodes deleted
    since, or re-arming a quarantine a later commit released. Atomicity comes
    from the rewrite (``os.replace``); the unlink is then just tidiness.
    """
    try:
        # #2814: DERIVED from the section tuple, never re-listed. A hand-list
        # here would go stale silently and leave the retirement artifact
        # carrying live config — which the next rebuild's union would then
        # re-merge, resurrecting nodes deleted since.
        _write_prewipe_snapshot(path, {
            "version": _PREWIPE_SNAPSHOT_VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(),  # noqa: UP017
            "completed": True,
            **{section: [] for section in _SNAPSHOT_SECTIONS},
        })
    except (OSError, TypeError, ValueError) as e:
        # ERROR, not warning: the pre-wipe payload is still on disk, so the
        # next rebuild will re-merge it and may resurrect nodes deleted since.
        logger.error(
            "could not retire the pre-wipe snapshot %s after a completed "
            "rebuild (%s) — the next rebuild will re-merge its pre-wipe "
            "values and may resurrect nodes deleted after this one; delete "
            "the file manually", path, e)
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    except OSError as e:
        # Harmless: the file left behind is the entry-less retirement payload.
        logger.warning(
            "could not remove the retired pre-wipe snapshot %s (%s) — it is "
            "entry-less, so merging it is a no-op; delete it at leisure",
            path, e)


def _merge_entry(left: dict, fresh: dict, key: str) -> dict:
    """Field-granular merge of two entries describing the SAME id.

    Fresh wins wherever it HAS a value; the leftover fills the gaps. Neither
    extreme is right on its own:

    * Leftover-wins-everything rolls back newer state whenever the sidecar
      outlived the wipe (a kill in the microsecond between the write and
      `DETACH DELETE`, or a retirement that could not be written) — the
      graph's current value is the newer one, and the leftover would put back
      a released quarantine or an older `content`/`status`.
    * Fresh-wins-everything throws away exactly what the leftover exists for.
      A partial replay recreates the node through `_upsert_point_props`, whose
      CONDITIONAL `content_hash` write derives no value for falsy content (and
      whose fixed SET list omits `outdated`/`expiredAt`/`posterior_*`) — so the
      fresh capture of that node has those properties ABSENT while the leftover
      still carries the pre-wipe values.

    `absences fill, presence wins` distinguishes them without guessing: a
    property the fresh capture lacks (or holds as null) is a replay gap; one
    it holds is current truth. `operator.inputs` is the calibrated case — a
    partial replay rebuilds no edges, so an empty fresh list is a gap and a
    non-empty one is newer (the pre-#2943 precedence, kept for this field
    only).
    """
    merged = dict(left)
    left_point = left.get("point") if key == "synthetic_events" else None
    fresh_point = fresh.get("point") if key == "synthetic_events" else None
    if isinstance(left_point, dict) and isinstance(fresh_point, dict):
        point = dict(left_point)
        for name, value in fresh_point.items():
            if value is None:
                continue
            if name == "operator":
                lo = left_point.get("operator")
                lo = lo if isinstance(lo, dict) else {}
                fo = value if isinstance(value, dict) else {}
                lo_inputs, fo_inputs = lo.get("inputs") or [], fo.get("inputs") or []
                if not fo_inputs and lo_inputs:
                    lo = {k: v for k, v in lo.items() if k != "inputs"}
                    fo = {**fo, "inputs": lo_inputs}
                point[name] = {**lo, **fo}
            else:
                point[name] = value
        merged = {**merged, **fresh, "point": point}
        return merged
    for name, value in fresh.items():
        if value is not None:
            merged[name] = value
    return merged


def _union_prewipe_snapshot(leftover: dict | None, fresh: dict) -> dict:
    """Order-stable, deduped union of a persisted snapshot with a fresh one.

    Deduped on point id / batch container id / Session container id / link
    pair, leftover order first so the synthetic prefix stays stable (the
    positional seq space the fold sweeps index on is preserved by keeping
    every leftover entry and appending only fresh-only ids). A colliding id in
    a NODE section is merged FIELD-wise by ``_merge_entry`` — fresh truth
    where it exists, leftover values where the fresh capture has none. The
    LINK sections are deduped WITHOUT a merge (``merge=False``), because an
    entry there is a two-element pair, not a property map: the surviving
    occurrence is merely kept. The pair order differs by section:
    ``batch_point_links`` is ``(point id, batch id)`` (captured
    ``RETURN p.id, p.batch_id``), while ``session_point_links`` is
    ``(session id, point id)`` (captured ``RETURN s.id, p.id``) and
    ``onboarding_step_links`` is ``(org id, step id)`` (captured
    ``RETURN n.org_id, s.step_id``).

    ``onboarding_snapshot`` (#4641) is the second PER-KEY node union: like
    ``config_snapshot`` it keeps a colliding LEFTOVER entry rather than
    field-merging it, because a self-healed default re-created for the same
    ``org_id`` must not overwrite recovered truth (see the leg's comment) —
    with exactly ONE field-level exception, ``org_subject_id`` (the org-anchor
    carrier), which is taken fresh-wins because nothing self-heals it and the
    post-restore check compares against this merged list, so swallowing a
    fresh anchor would silently destroy a live `onboards` edge.
    """
    leftover = leftover or {}

    def _union(raw, key, merge_key, merge=True):
        out: list = []
        index: dict = {}
        for entry in raw:
            try:
                k = key(entry)
            except TypeError:
                k = None
            if k is None:
                # unhashable/absent key — keep the entry, skip dedup
                out.append(entry)
                continue
            if k in index:
                if merge:
                    out[index[k]] = _merge_entry(
                        out[index[k]], entry, merge_key)
                continue
            index[k] = len(out)
            out.append(entry)
        return out

    def _point_id(e):
        p = e.get("point") if isinstance(e, dict) else None
        pid = p.get("id") if isinstance(p, dict) else None
        return pid if isinstance(pid, str) else None

    def _container_id(entry):
        """Dedup key for a :Batch / :Session container snapshot entry — id."""
        cid = entry.get("id") if isinstance(entry, dict) else None
        return cid if isinstance(cid, str) else None

    def _link_key(link):
        # exactly two, mirroring the restore loop's unpack (entry shapes are
        # already validated on load; this is the writer-side contract).
        if isinstance(link, (list, tuple)) and len(link) == 2:
            k = (link[0], link[1])
            return k if all(isinstance(v, str) for v in k) else None
        return None

    events = _union(
        list(leftover.get("synthetic_events") or [])
        + list(fresh["synthetic_events"]), _point_id, "synthetic_events")
    batches = _union(
        list(leftover.get("batch_snapshot") or [])
        + list(fresh["batch_snapshot"]), _container_id, "batch_snapshot")
    # merge=False: a link entry is a two-element PAIR — `batch_point_links`
    # is (point id, batch id), `session_point_links` is (session id, point
    # id) — so `_merge_entry`'s `dict(left)` would raise on it. The no-merge
    # policy is passed EXPLICITLY — never inferred from another section's
    # name.
    links = [tuple(entry[:2]) for entry in _union(
        list(leftover.get("batch_point_links") or [])
        + list(fresh["batch_point_links"]), _link_key, "batch_point_links",
        merge=False)
        if _link_key(entry) is not None]
    # #3947 × #3010: the :Session container snapshot rides the same sidecar,
    # deduped the same way (containers on `id`, links on the (sid, pid) pair).
    # `.get` on the fresh side keeps the union callable by a caller (e.g. an
    # offline unit test) that predates these sections; absent means empty,
    # which is exactly how the loader reads them.
    session_containers = _union(
        list(leftover.get("session_snapshot") or [])
        + list(fresh.get("session_snapshot") or []),
        _container_id, "session_snapshot")
    session_links = [tuple(entry[:2]) for entry in _union(
        list(leftover.get("session_point_links") or [])
        + list(fresh.get("session_point_links") or []),
        _link_key, "session_point_links", merge=False)
        if _link_key(entry) is not None]
    # #4641: the onboarding state machine. The NODE leg is a PER-ORG union
    # with the LEFTOVER kept (see the ONE exception below) — deliberately NOT
    # `_merge_entry`'s fresh-wins field merge, for the same reason
    # `config_snapshot` avoids it:
    # a self-healed default must not overwrite recovered truth. The concrete
    # case is `_ensure_onboarding_node_after_provision`
    # (`tortoise/supabase_control.py`) re-creating a DEFAULT
    # `{status:'active', version:1, member_progress:'{}'}` node for an org
    # between an interrupted wipe and the retry — fresh-wins would then
    # overwrite the recovered `status='complete'`/`compact`/`member_progress`
    # and silently re-onboard the org, which is the exact harm #4641 removes.
    # A FRESH-ONLY org — onboarding started after the interrupted wipe — is
    # APPENDED; dropping it would put it into the retry's own wipe with
    # nothing to restore it from. `.get` on the fresh side mirrors the session
    # sections (partial dicts are legal for the offline union tests).
    def _org_key(entry):
        oid = entry.get("org_id") if isinstance(entry, dict) else None
        return oid if isinstance(oid, str) else None

    fresh_orgs: dict = {}
    for entry in fresh.get("onboarding_snapshot") or []:
        _foid = _org_key(entry)
        if _foid is not None:
            fresh_orgs.setdefault(_foid, entry)

    onboarding_nodes: list[dict] = []
    onboarding_seen: set = set()
    for entry in (list(leftover.get("onboarding_snapshot") or [])
                  + list(fresh.get("onboarding_snapshot") or [])):
        oid = _org_key(entry)
        if oid is None:
            # Unkeyable. The CAPTURE raises on this (so a live graph can never
            # produce one); only a planted/hand-edited sidecar can, and the
            # pre-wipe validator refuses it before the wipe.
            onboarding_nodes.append(entry)
            continue
        if oid in onboarding_seen:
            # Leftover-first iteration: the LEFTOVER entry is kept verbatim.
            continue
        onboarding_seen.add(oid)
        # #4641 review round 3: the ONE field the verbatim rule must NOT
        # swallow. The node rules above exist because a self-healed DEFAULT
        # node (`_ensure_onboarding_node_after_provision`) can overwrite
        # recovered truth — but NOTHING self-heals the org-ANCHOR pointer, it
        # is the carrier of the `onboards` EDGE, and the post-restore check
        # compares the rebuilt graph against THIS merged list. So a leftover
        # entry that predates the org's anchor would drop the fresh
        # `org_subject_id`, the restore would skip the edge, and the check
        # would compare against the same stale set and report a clean, full
        # restore while a LIVE anchor edge was destroyed. Carry it fresh-wins
        # (fresh when it is a str, leftover otherwise); a fresh-only value
        # means the anchor was linked after the interrupted run captured its
        # sidecar, so it is the newer truth.
        fresh_entry = fresh_orgs.get(oid)
        fresh_sid = (fresh_entry.get("org_subject_id")
                     if isinstance(fresh_entry, dict) else None)
        if isinstance(fresh_sid, str) and \
                entry.get("org_subject_id") != fresh_sid:
            entry = {**entry, "org_subject_id": fresh_sid}
        onboarding_nodes.append(entry)
    # The LINK leg is `merge=False` like every other link section: an entry is
    # a two-element pair, not a property map, and leftover-first keeps the
    # recovered pair.
    onboarding_links = [tuple(entry[:2]) for entry in _union(
        list(leftover.get("onboarding_step_links") or [])
        + list(fresh.get("onboarding_step_links") or []),
        _link_key, "onboarding_step_links", merge=False)
        if _link_key(entry) is not None]
    # #2814: authoritative configuration. This is a PER-KEY union on
    # `_config_key` — NOT `_merge_entry` and NOT a wholesale section discard:
    #
    #  * a colliding key keeps the LEFTOVER entry VERBATIM (field-merging
    #    would let a self-healed starter default overwrite a real captured
    #    value — `ensure_tenant_packs` fires on the empty post-wipe graph and
    #    writes present values with a fresh `installed_at`);
    #  * a FRESH-ONLY key — config provisioned after an interrupted wipe but
    #    before the retry — is APPENDED. Dropping it would put it into the
    #    retry's own wipe with nothing to restore it from.
    #
    # `.get` on the fresh side mirrors the session sections (the offline union
    # tests call this with partial dicts, and absent means empty to the loader
    # too). The config leg never routes through `_merge_entry`: `_config_key`
    # includes the label, so a cross-label identity collision stays two
    # entries instead of merging a `:Meta` property set onto a pack node.
    config_entries: list[dict] = []
    config_seen: set = set()
    for entry in (list(leftover.get("config_snapshot") or [])
                  + list(fresh.get("config_snapshot") or [])):
        key = _safe_config_key(entry)
        if key is None:
            # Unkeyable — keep it (the pre-wipe validator refuses it).
            config_entries.append(entry)
            continue
        if key in config_seen:
            # A collision — and, because the leftover leg is iterated first,
            # that means the LEFTOVER entry is kept verbatim and the colliding
            # fresh entry is dropped.
            continue
        config_seen.add(key)
        config_entries.append(entry)
    return {"synthetic_events": events, "batch_snapshot": batches,
            "batch_point_links": links,
            "session_snapshot": session_containers,
            "session_point_links": session_links,
            "onboarding_snapshot": onboarding_nodes,
            "onboarding_step_links": onboarding_links,
            "config_snapshot": config_entries}


class _GuardedGraph:
    """Wrapper around the FalkorDB Graph handle that guards bulk graph-wipe queries.

    The SDK calls proj.g.query() (raw handle) throughout — the guard must wrap
    self.g itself, not FalkorProjection.query(). Restored in #49 Phase 2 after
    the v3.0 rewrite (0f9e6a2) dropped it from this live module.

    Intercepts bulk DETACH DELETE (no property map, no real WHERE) and asserts
    the graph is a test graph before allowing execution. Targeted deletes
    (MATCH (n:Label {id:$id}) ...) pass through unchanged.
    """

    __slots__ = ("_g", "_proj")

    def __init__(self, g, projection):
        self._g = g
        self._proj = projection

    def query(self, cypher: str, params=None, timeout=None):
        if _is_bulk_wipe(cypher) and not getattr(self._proj, "_skip_guard", False):
            self._proj._assert_test_graph(
                "REFUSING to run bulk DETACH DELETE on non-test graph"
            )
        return self._g.query(cypher, params=params, timeout=timeout)

    def __getattr__(self, name):
        return getattr(self._g, name)

from tortoise.config import RELATIVE_PATH_ERROR, SUPPORTED_URI_SCHEMES, LOOPBACK_HOSTS, parse_uri_userinfo  # noqa: E402, I001
from tortoise.fork_slot import is_fork_refusal  # noqa: E402
from tortoise.live import _live_only, _terminal_excluded  # noqa: E402

# #2981 — a FalkorDB/Redis server that has reached `maxmemory` with
# `noeviction` REFUSES WRITES while the graph is perfectly intact. The reply
# text is the only signal that separates "full" from "corrupt", so it is
# matched case-insensitively against the server's own wording. Reported as
# corruption, it sends an operator to `rebuild` — i.e. toward destroying
# healthy data — which is strictly worse than a vague error would be.
_WRITE_REFUSAL_MARKERS = (
    "used memory >",            # redis: "... used memory > 'maxmemory'"
    "oom command not allowed",  # redis 7 wording
    "out of memory",            # generic engine wording
)

# #3634 — a probe can fail for reasons that are neither corruption nor a
# maxmemory refusal, and each has its OWN remedy. Collapsing them (or letting
# them fall through to the rebuild advice) misattributes the failure: a
# still-hydrating server or a fork-refusing one is NOT a broken graph.
#
# The FORK family has exactly ONE classifier — ``fork_slot.is_fork_refusal``,
# which owns the marker vocabulary (its single home) and walks the
# ``__cause__``/``__context__`` chain — so this table carries only the LOADING
# cause and ``_backend_failure_message`` delegates fork detection. Do NOT
# restate fork markers here: a second, parallel list is how ``could not fork``
# (FalkorDB's own reply, ``cmd_copy.c``) went unrecognised while the table
# matched only the invented ``fork failed`` stem.
#
# The one marker is a deliberately long phrase, NOT a bare cause word: a bare
# ``"loading"`` would swallow unrelated text (a path, a docstring) and route
# it to the wrong remedy.
#
# (marker, cause_key)
_BACKEND_FAILURE_MARKERS: tuple[tuple[str, str], ...] = (
    ("redis is loading the dataset", "loading"),  # LOADING reply — RDB/AOF hydrating
)

# The per-cause body. Each NAMES its own cause and states what the cause
# actually means, so the operator does not act on a neighbour's remedy.
#
# The FORK remedy is the one cause whose body is not a single literal. Its
# refusal has TWO mechanically distinct causes with a BYTE-IDENTICAL reply
# (`GRAPH.COPY failed, could not fork` — see tools/embedded_evidence.py): a
# background RDB/AOF child in the fork slot, and a hung un-reaped
# `redis-module-fork` child from a PREVIOUS fork (#3845). And its slot cure
# exists only on the EMBEDDED lane. Asserting one cause, or prescribing
# `recover_fork_slot` where `socket_path_of(db)` is None, is wrong on both
# counts. The body is therefore built from a shared cause-analysis prefix
# plus a lane-specific action, selected by `_backend_failure_message`'s
# `embedded` flag (the call site's own `self._is_embedded` reading — the axis
# that actually decides whether `socket_path_of(db)` is non-None, NOT
# `is_prod`, which gates auto-recovery and never the manual slot cure), so an
# operator is never handed a cure their handle cannot execute.
_FORK_REMEDY_CAUSE_ANALYSIS = (
    "DB health check failed on open: the server refused a module fork "
    "(FalkorDB replies `GRAPH.COPY failed, could not fork`). Redis allows "
    "ONE module-fork child at a time, and that refusal has TWO mechanically "
    "distinct causes with a byte-identical reply: an in-flight background "
    "RDB save (or AOF rewrite) child occupies the slot, OR a hung, "
    "un-reaped `redis-module-fork` child from a PREVIOUS fork still holds "
    "it. errno 17 is EEXIST (the slot is occupied), NOT memory or process "
    "pressure; the graph is not corrupt. Discriminate before acting: if no "
    "hung `redis-module-fork` child is found, the slot is held by an "
    "in-flight save — wait for it to finish and retry. A refusal carrying "
    "EAGAIN (`Resource temporarily unavailable`) is a DIFFERENT mechanism — "
    "a real resource limit — and is not cleared by reaping a child. "
)
_FORK_REMEDY_EMBEDDED = _FORK_REMEDY_CAUSE_ANALYSIS + (
    "[embedded lane] Free a hung child with "
    "`fork_slot.recover_fork_slot(db)` (it kills this daemon's own hung "
    "child) or kill the lingering `redis-module-fork` child directly; the "
    "refusal clears as soon as Redis reaps it. Do NOT rebuild. See #3845 "
    "and #3634."
)
_FORK_REMEDY_SERVER = _FORK_REMEDY_CAUSE_ANALYSIS + (
    "[server lane — remote/docker FalkorDB] The client-side slot cure is "
    "embedded-only — it applies when the handle is a local unix socket. On "
    "the SERVER's host, reap the lingering `redis-module-fork` child or "
    "restart the FalkorDB server; the refusal clears as soon as Redis reaps "
    "it. Do NOT rebuild. See #3845 and #3634."
)
_BACKEND_FAILURE_REMEDIES: dict[str, str] = {
    "loading": (
        "DB health check failed on open: the server is still LOADING its "
        "dataset (the Redis/FalkorDB reply is `LOADING Redis is loading "
        "the dataset in memory`). The graph is neither corrupt nor full — "
        "the server has not finished reading its snapshot. Wait for the "
        "load to finish and retry. (Secondary note: a load that never "
        "completes can mean the dataset exceeds the container's memory, "
        "for which a smaller snapshot is the durable fix — but the remedy "
        "for THIS failure is simply to wait.) Do NOT treat this as "
        "corruption. See #3634."
    ),
}


def _fmt_bytes(n: int) -> str:
    """Human byte size for an operator-facing message."""
    step = 1024.0
    val = float(n)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if val < step or unit == "TiB":
            return f"{val:.0f} B" if unit == "B" else f"{val:.1f} {unit}"
        val /= step
    return f"{val:.1f} TiB"
from tortoise.embedded_lifecycle import (  # noqa: E402
    atexit_fast_close,  # #1371: registers the batch flush
    register_atexit_close,
    register_gc_close,
)

# Backward-compat alias: the canonical scheme set lives in tortoise.config
# (SUPPORTED_URI_SCHEMES) so URI-routing and connection-layer validation share
# one source of truth (#715). Kept for existing importers of the private name.
_SUPPORTED_URI_SCHEMES = SUPPORTED_URI_SCHEMES

# Epic #1647 (cycle-7 P1-3): the redirect's loopback predicate DELEGATES to
# tortoise.config's LOOPBACK_HOSTS — one implementation shared with the Task 4
# tripwire and wipe_server (pinned by test_loopback_predicate_single_source).
_LOOPBACK_HOSTS = LOOPBACK_HOSTS


def _is_supported_uri_scheme(uri: str) -> bool:
    """True when the URI's scheme is in the shared SUPPORTED_URI_SCHEMES
    (docker/redis/rediss). Single shared predicate — the redirect and any
    URI-routing check use it (plan-review P2-11: the old plan referenced a
    non-existent _is_supported_uri_scheme; _validate_uri_scheme RAISES and
    cannot be used as a predicate)."""
    return uri.split(":", 1)[0].lower() in _SUPPORTED_URI_SCHEMES


def _caller_test_stem() -> str | None:
    """Nearest calling TEST module's file stem (plan-review P0-1).

    Walks the caller stack for the FIRST (nearest) frame whose ``__file__``
    basename starts with "test_" — the exemption key for
    TORTOISE_TEST_NO_REDIRECT. Cycle-3 P2-18: the key is the NEAREST test_
    frame, NOT the outermost — a cross-test-module helper (e.g. a shared
    fixture in test_helpers.py called from test_config.py) resolves to the
    helper's stem, so a shared helper's exemption exempts ALL its callers
    (documented semantics; the exemption list is the caller-module list, so
    a helper used by both a carve-out and a migrated file must not be
    listed). Helpers (tests/_embedded.py, tests/_live_utils.py, conftest.py)
    are skipped by the prefix sniff, so a carve-out file constructing
    through a helper still resolves to its own stem. Returns None when no
    test module is in the stack — subprocess CLI children (test_export_cli's
    `python -m tortoise export`, redis-guard fixture scripts) and prod
    callers. None SUPPRESSES the redirect entirely (cycle-2 P1-1b): a child
    that inherits URI+TEST_MODE must keep the embedded lane, never silently
    test the server. Pinned by a cross-module unit test (a helper frame
    between the test and the construction resolves to the nearest stem)."""
    import inspect
    frame = inspect.currentframe()
    try:
        while frame is not None:
            mod = frame.f_globals.get("__file__")
            if mod:
                name = os.path.basename(mod)
                if name.startswith("test_") and name.endswith(".py"):
                    return name[:-3]
            frame = frame.f_back
        return None
    finally:
        del frame


# ── Epic #1686: worker-thread test-module attribution ────────────────────
# The frame-keyed carve-out exemption (TORTOISE_TEST_NO_REDIRECT) cannot
# see worker threads (TestClient portals, background threads): their stacks
# have no test_ module, so _caller_test_stem() returns None and the process
# flag (_TEST_SESSION_ACTIVE) fires the redirect even for carve-out files.
# This per-thread registry records the test module the CURRENT thread is
# executing under (populated by conftest's pytest_runtest_setup hook via
# _record_current_test_stem) and INHERITS it into spawned threads: the
# patched Thread.start stamps the spawner's stem on the new Thread instance
# BEFORE the original start() runs — the child reads it at bootstrap
# (threading.current_thread() IS the Thread object; the attribute write
# happens-before _start_new_thread, so there is no race). anyio's
# WorkerThread overrides run() WITHOUT calling super(), so patching run
# would be bypassed — start is the correct seam (starlette's portal thread
# is a plain Thread(target=...)). Installation is a CONFTEST-INVOKED
# function (install_thread_stamp) — never module-body: prod processes never
# call it, so even a leaked TORTOISE_TEST_MODE=1 env cannot patch stdlib
# (review P2-1); _record_current_test_stem additionally refuses records
# outside an active session (prod parity).
_ORIG_THREAD_START = threading.Thread.start


# Module-load install gate (review P2-1): TORTOISE_TEST_MODE=1 alone is NOT
# sufficient — it can leak into a prod process via env inheritance (shared
# .env / test-spawned subprocess promoted to prod). The patch therefore
# installs only via install_thread_stamp(), which conftest calls AFTER
# _TEST_SESSION_ACTIVE=True (the flag is always False at first import, so
# module-body install could not see it anyway). See install_thread_stamp.

def _record_current_test_stem(stem: str | None) -> None:
    """Record (or clear) the CURRENT thread's test-module stem (#1686).

    Called by conftest's pytest_runtest_setup/teardown with the running
    test module's stem (and None at teardown). Stored on the current
    Thread instance so spawned threads inherit it via the patched
    Thread.start. Prod parity: records are refused outside an active test
    session (conftest only runs in tests, so this is never called there)."""
    if stem is not None and not _TEST_SESSION_ACTIVE:
        return  # prod parity: no attribution outside a test session
    cur = threading.current_thread()
    if stem is None:
        try:  # noqa: SIM105
            del cur._tortoise_test_stem
        except AttributeError:
            pass
    else:
        cur._tortoise_test_stem = stem


def _inherited_test_stem() -> str | None:
    """The test module this thread inherited from its spawner (#1686).

    None in prod processes and subprocess CLI children (never recorded /
    never inherited) — matching the frame gate's None semantics."""
    return getattr(threading.current_thread(), "_tortoise_test_stem", None)


def _thread_start_inherit_stem(self, *args, **kwargs):
    """Thread.start wrapper: stamp the spawner's stem on the new thread.

    Written on the Thread instance BEFORE the original start() — the child
    reads it at bootstrap with no race (the attribute set happens-before
    _start_new_thread). __slots__-restricted Thread subclasses get no
    stamp (AttributeError swallowed) and fall back to frame resolution."""
    parent_stem = _inherited_test_stem()
    if parent_stem is not None and _TEST_SESSION_ACTIVE:
        try:  # noqa: SIM105
            self._tortoise_test_stem = parent_stem
        except AttributeError:
            pass
    return _ORIG_THREAD_START(self, *args, **kwargs)


def install_thread_stamp() -> None:
    """Install the Thread.start test-stem stamp (#1686).

    Called by conftest AFTER _TEST_SESSION_ACTIVE=True — a prod process can
    never satisfy that (it never runs conftest), so even a leaked
    TORTOISE_TEST_MODE=1 cannot patch stdlib. Idempotent via the
    class-attribute marker: it survives importlib.reload (module state
    resets, the marker does not), so a reload cannot double-wrap start."""
    if os.environ.get("TORTOISE_TEST_MODE") == "1" \
            and _TEST_SESSION_ACTIVE \
            and not getattr(threading.Thread, "_tortoise_stamp_installed", False):
        threading.Thread.start = _thread_start_inherit_stem
        threading.Thread._tortoise_stamp_installed = True


def _resolve_caller_stem() -> str | None:
    """Worker-thread-aware caller test stem (#1686).

    Stack-walk first (nearest test_ frame — the historical semantics,
    pinned by test_caller_test_stem_nearest_frame_semantics); falls back
    to the per-thread inherited stem for worker threads whose stack has no
    test_ module. None in prod and in subprocess CLI children — the frame
    gate's None semantics, preserved. Pure resolver (no side effects — a
    recording fallback here could clobber the conftest hook's main-thread
    stem when resolution passes through a test_-prefixed helper).

    Currency (review P2-3): the inherited stem is honored ONLY while the
    main thread's CURRENT recorded stem matches it — a long-lived worker
    spawned under a carve-out module and later reused by a non-exempt test
    would otherwise leak the stale exemption (its stack has no test_ frame,
    so the frame walk cannot catch it). Stale → None → redirect fires
    (fail-closed; conftest records land on the main thread, see
    pytest_runtest_setup)."""
    stem = _caller_test_stem()
    if stem is not None:
        return stem
    inherited = _inherited_test_stem()
    if inherited is None:
        return None
    if _main_thread_current_stem() == inherited:
        return inherited
    return None


def _main_thread_current_stem() -> str | None:
    """The CURRENT test stem recorded on the main thread (#1686).

    conftest's pytest_runtest_setup/teardown record on
    threading.current_thread(), which is the main thread for pytest-run
    tests; a worker thread never records (only inherits), so comparing the
    inherited stamp against THIS value is the currency check — a stamp
    from a test that is no longer running resolves as stale → None."""
    return getattr(threading.main_thread(), "_tortoise_test_stem", None)


def _is_loopback_host(host: str | None) -> bool:
    """Shared loopback predicate (cycle-2 P0-2).

    True for localhost/127.0.0.1/::1. Used by the redirect (fail-fast
    before the first write), the Task 4 session-start tripwire (fail before
    ANY test writes), and wipe_server (D-4) — one predicate so a typo'd or
    shared TORTOISE_DB_URI is refused at the earliest possible point.
    Cycle-7 P1-3: this helper DELEGATES to `tortoise.config`'s
    LOOPBACK_HOSTS constant (is_loopback_uri(uri) added to tortoise/config.py
    in this task is the SINGLE shared implementation — conftest/CI and the
    Task 4 tripwire import `is_loopback_uri` from tortoise.config, so BOTH
    modules must resolve the SAME host set; pinned by
    `test_loopback_predicate_single_source`, Step 1)."""
    from tortoise.config import LOOPBACK_HOSTS
    return host in LOOPBACK_HOSTS


def _journal_file_path() -> str | None:
    """Per-session created-graph journal path (epic #1647 Task 2 Step 7).

    Reads TORTOISE_TEST_JOURNAL_FILE (exported by conftest at import time,
    cycle-4 P2-9). Absent env var → None → the appender no-ops, never
    fails — the specified fallback for the P1 window (Task 2 not yet
    landed: P1-window mints are unjournaled and bounded by Task 2's
    last-suite-standing full sweep)."""
    path = os.environ.get("TORTOISE_TEST_JOURNAL_FILE")
    return path or None


# Epic #1647 (CI P2 fix): process-level test-session flag. conftest sets this
# True at import; subprocess CLI children (test_export_cli's `python -m
# tortoise export`, redis-guard fixture scripts) NEVER import conftest, so the
# flag stays False for them — exactly the subprocess-vs-TestClient distinction
# the stack-walking _caller_test_stem() cannot make (TestClient runs request
# handlers in a WORKER THREAD whose stack has no test_ module, so the frame
# gate wrongly suppresses the redirect there and the fixture-patched db_path
# SDK constructs embedded → write/read split). With the flag, the redirect
# fires in the whole test process (worker threads included); the carve-out
# exemption is frame-keyed AND, since #1686, thread-inherited: conftest's
# pytest_runtest_setup records the running module's stem per-thread and the
# patched Thread.start stamps it onto spawned threads, so worker-thread
# constructions in carve-out files resolve their own stem and stay embedded.
# The inheritance is spawn-time: module-scoped portals spawned before the
# first runtest_setup still resolve None → redirect (unchanged from today;
# test_flip_gate.py's TestClient portals are function-scoped and construct
# inside a running test — verified #1686). Long-lived workers keep their
# spawn-time stem, but _resolve_caller_stem's currency check (main thread's
# CURRENT stem must match) fails them closed once the spawning test is no
# longer current.
_TEST_SESSION_ACTIVE = False


def _journal_append_product(graph_name: str) -> None:
    """Append a minted graph name to the per-session created-graph journal.

    Product-side seam writer (cycle-6 P2-13 ownership split): the redirect
    and from_uri are PRODUCT code and cannot import tests/_embedded (import
    cycle), so the FILE journal is written here; the tests-side in-memory
    _JOURNAL/_WIPED_UP_TO delta stays tests-side only (Task 2).

    Append = makedirs(parent) + open/write/close per append (cycle-4 P2-3:
    per-append open is the atomicity boundary against torn writes; cycle-7
    P1-1: the parent dir exists only after _redislite_hygiene's session
    fixture — makedirs BEFORE every append so module-import/collect-only
    appends cannot FileNotFoundError). Absent env var → no-op, never fail
    (the specified fallback).

    Failure policy (#3214): a WRITE failure is not hygiene bookkeeping — the
    journal is the OWNERSHIP CONTRACT, so a minted graph that cannot be
    journaled is UNOWNED: a live peer's scope=None sweep finds no record of
    it and may delete it (the cross-session flake #3074 exists to stop). The
    old policy (a silent no-op logged at DEBUG) hid that state for the whole
    session, so an OSError now RAISES: the session stops at the first mint
    whose ownership could not be recorded instead of letting the shared
    server accumulate graphs a peer sweep may destroy.

    NOT fail-closed, and not claimed to be: raising neither removes nor
    protects the graph, so a caller that already created its graph holds an
    UNOWNED one and must deal with it itself. The two sdk.py call sites do:
      * the registry append (``_get_registry``) runs BEFORE the handle is
        cached and BEFORE ``_ensure_registry_indexes`` writes, and
        ``select_graph`` is client-side (no server call) — a raise there
        mints nothing and leaves no half-initialized registry behind;
      * the org-mint append (``org_create``) runs BEFORE the org graph's
        TeamMeta CREATE (the write-ahead seam, #3390), so a raise mints
        nothing — there is no unowned graph left to drop; ``org_create``'s
        own handler still rolls the registry Org node back.
    Every product mint site now journals through the write-ahead seam
    (``journal_mint_write_ahead``, #3390), so the append precedes the CREATE
    at all of them and a raise mints nothing to own. The hosted lanes' outer
    rollback handlers still drop ``graph_name`` best-effort (a no-op on a
    graph that was never created), and the projection redirect / from_uri
    seams appended BEFORE materialization already. The ordering is now a
    property of the seam rather than a per-caller contract.

    The two no-op gates above (absent path, not a test session) are
    unchanged, so production mints never reach the raise."""
    path = _journal_file_path()
    if not path:
        return
    if not _TEST_SESSION_ACTIVE:
        # review P2-1: env leakage alone (TORTOISE_TEST_JOURNAL_FILE inherited
        # by a prod process) must never journal — the journal is a TEST-SESSION
        # artifact; prod mints are unjournaled by design (their sweeps only
        # run in tests).
        return
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a") as fh:
            fh.write(graph_name + "\n")
    except OSError as e:
        # #3214: see the docstring — the journal write is the ownership
        # contract, so an unjournalable mint stops the session instead of
        # silently producing a graph no sweep can attribute to a session.
        raise RuntimeError(
            f"session journal append failed for {graph_name!r} ({path!r}): "
            f"{e!r} — the graph is minted but UNOWNED: a live peer's "
            f"scope=None sweep has no record of it and may delete it "
            f"(#3214). Stopping at the first mint whose ownership could "
            f"not be recorded; a caller that already created the graph "
            f"must drop it (see #3390 for the write-ahead fix)."
        ) from e


def journal_mint_write_ahead(graph_name: str) -> None:
    """#3390: WRITE-AHEAD mint journaling — journal the INTENDED name BEFORE
    the graph is materialized.

    Every product mint site used to materialize the graph and journal its
    ownership *afterwards*::

        graph.query(_init_q)              # effect
        _journal_append_product(name)     # ownership record

    A kill in that gap left a graph the session's journal did not own — an
    ORPHAN. The journal is the ownership record the journal-driven own/stale
    sweep (``_sweep_drop``) reads, so an orphan leaks — no journal-driven sweep
    can see it (``wipe_server`` is fail-closed to the ``test_``/``tortoise_test``
    prefixes and never touches the ``org_*``/``team_*`` product namespace) — makes the owned-set a rebuild
    produces diverge from live (live != rebuild), and is reclaimable only by
    the opt-in ``_sweep_team_strays`` pass. This seam reverses the order: the
    ownership line is journaled FIRST, so at the sites that adopt it the CREATE
    cannot run before the line — including the two
    ``_make_sdk(namespace=org_id)._get_proj()`` lanes, which journal before the
    projection is constructed (``Projection.__init__`` runs ``_ensure_indexes()``,
    a query that MATERIALIZES the graph).

    The fail-closed half is ``_journal_append_product``'s raising variant
    (#3214/#3379, landed on main): a journal WRITE failure propagates instead
    of being swallowed at DEBUG. Paired with the order this seam supplies, a
    raise means the CREATE never runs, so the fail-closed property comes from
    the two together rather than from each caller dropping its own graph
    after the fact.

    The residual window is the harmless inverse — journaled-but-never-created:
    the sweep's DETACH+DELETE of an absent graph succeeds (or logs and
    continues, ``_drop_one_graph``), and the journal file is removed only when
    every name dropped, so replay/rebuild converges. It is idempotent: a
    duplicate line is set-equivalent for the sweep's owned-set delta and the
    peer-protection read (``_live_peer_session_graphs``); a never-materialized
    line only ADDS a name to that protected set, which can spare a graph from a
    global sweep — it can never cause a delete.

    Rollback handlers must NOT compensate by removing the line: the journaled
    name is the ownership tombstone that lets the sweep clean up a graph whose
    create partially landed — removing it would re-open the orphan window this
    seam closes. Known residual (follow-up to #3390): the session's own sweep
    (``_sweep_drop``) carries no live-peer skip (unlike ``wipe_server``'s #3074
    guard), so a name whose create FAILED — or was never reached — here can be
    dropped at session end while a concurrent session's same-named graph is
    live; a pair of *successful* same-name mints already had that exposure, but
    write-ahead adds the never-created case. A second residual: a namespaced SDK
    whose namespace equals the org name (``TortoiseSDK(namespace=X)
    .org_create(X)``) has its target ``org_X`` materialized by
    ``self._get_proj()``'s ``_ensure_indexes`` before this seam at the SDK
    org-mint site — journaling earlier would over-own an EXISTING org on the
    duplicate-name early-return path, so the order is left as-is; no product
    caller uses that shape today.

    No-op outside a test session (``_journal_append_product`` is env- and
    process-flag gated), exactly as the post-create call it replaces.
    """
    _journal_append_product(graph_name)


# ── Mixins ────────────────────────────────────────────────────────────────
from tortoise.projection.entities import (  # noqa: E402, I001
    BELIEF_BOOL_PROPS,
    BELIEF_PROPS,
    _belief_bool_value_ok,
    _belief_prop_value_ok,
    _EntityHandlers,
    _is_persistable_prop_value,
)
from tortoise.projection.edges import _EdgeHandlers  # noqa: E402
from tortoise.projection.grounding import _GroundingMixin  # noqa: E402
from tortoise.projection.propagation import _PropagationMixin  # noqa: E402
# #2795: cycle-free derived-hash helper for the PointRevised replay writer.
from tortoise.ids import content_hash as _content_hash  # noqa: E402

# #244: Event FTS index migration (subject-only → subject+name) is tracked by a
# persisted DB marker (Meta node 'event_fts_v2'), not a process-local flag — a
# module-level bool resets every restart and would drop+recreate the index on
# every boot (churn + crash window on server FalkorDB). See _ensure_indexes.

# ── Module-level helpers ──────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()  # noqa: UP017


def remove_stale_aof(db_path: str | os.PathLike) -> None:
    """#915 — remove a stale AOF dir adjacent to an embedded DB.

    With AOF enabled (see FalkorProjection embedded serverconfig), Redis loads
    the AOF in PREFERENCE to the RDB on cold start. A stale AOF at the target
    path therefore makes restore/migrate silently serve pre-restore data
    (e.g. ``backup.restore`` copying an RDB snapshot into a path whose AOF
    dir still holds the OLD live graph). Restore semantics =
    "the restored snapshot wins" — call this on the target path before any
    open/copy. No-op when the DB path is ``:memory:`` or has no adjacent dir.
    """
    if not db_path or str(db_path) == ":memory:":
        return
    # The projection sets appenddirname to "<db-filename>-appendonlydir" (#915)
    # so multiple embedded DBs in one directory keep isolated AOF dirs.
    # Also tolerate the old literal "appendonlydir" sibling and the
    # "<db>-appendonlydir" suffix form (pre-appenddirname builds).
    db = Path(str(db_path))
    candidates = [
        db.with_name(db.name + "-appendonlydir"),
        db.parent / "appendonlydir",
    ]
    for d in candidates:
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)
            logger.warning("removed stale AOF dir %s (restore/migrate contract, #915)", d)


def _norm(ev: dict) -> dict:
    """Normalize event shape — tolerates API (flat) and script (nested point).

    Only splices point fields when ``point`` is a dict — a non-dict ``point``
    (e.g. a legacy string ID) must not crash normalization (issue #325).
    """
    if not isinstance(ev, dict):
        # #331 (review r4): a non-dict event (raw JSON null/array line in
        # the log) degrades to an empty event — callers skip it via the
        # type guard instead of AttributeError in ev.get().
        return {}
    if isinstance(ev.get("point"), dict):
        return {**ev, **ev["point"]}
    return ev


# #3689: the four epistemic dims ``annotate_operator`` writes onto an Operator
# Point. The SDK journals them under their NODE-property names
# (``annotator_*``) as ``OperatorAnnotated`` / ``PointRevised`` extras; the
# ``OperatorAnnotated`` :GraphEvent payload (docs/event-catalog.md) uses the
# SHORT names (``bias``/``precision``/…), so a raw producer journaling the
# documented payload shape is accepted too. The projection folded NEITHER
# before #3689 — so a wipe+replay erased the annotation with NO warning
# (the #3299 class: a live mutation with no effective journal fold).
#
# SCOPE (#2946): this is deliberately the ANNOTATOR subset, not the general
# "PointRevised extras dropped on replay" class (#2946/#2795 own that design:
# the handled-key semantics of `confidence`/`status`, the tags/TAGGED edge
# ordering across pass 2, and a shared skip-set for this module-level fold).
_ANNOTATOR_PROPS: tuple[str, ...] = (
    "annotator_bias",
    "annotator_precision",
    "annotator_consistency",
    "annotator_directness",
)
_ANNOTATOR_SHORT_ALIASES: dict[str, str] = {
    "bias": "annotator_bias",
    "precision": "annotator_precision",
    "consistency": "annotator_consistency",
    "directness": "annotator_directness",
}


def _annotator_value_ok(val) -> bool:
    """True when a journaled annotator dim can be written to FalkorDB.

    Shape rule first, so this stays in lockstep with the shared #2894/#2795
    writer (``_is_persistable_prop_value``): maps/bytes/sets and over-deep
    arrays are rejected there. On top of that, two classes are rejected at
    PARAMETER PARSE even though they round-trip through JSONL — a non-finite
    float (NaN/±Inf; ``1e400`` → ``inf``) and a string carrying NUL or a
    lone surrogates (review P1). A rejection here lands in rebuild pass-1b
    AFTER the wipe, so a malformed record must degrade to a DROPPED dim,
    never an aborted recovery.

    ``None`` is KEPT: a live ``update_point(x=None)`` clears the property,
    and replay must match by clearing it too — dropping the key would leave
    the prior value in place (a live/replay parity break).
    """
    if val is None:
        return True
    if not _is_persistable_prop_value(val):
        return False
    if isinstance(val, float):
        return math.isfinite(val)
    if isinstance(val, str):
        if "\x00" in val:
            return False
        try:
            val.encode("utf-8")
        except UnicodeEncodeError:
            return False  # lone surrogate (driver rejects at encode)
        return True
    if isinstance(val, (list, tuple)):
        return all(_annotator_value_ok(x) for x in val)
    return True


def _writable_id(val) -> bool:
    """True when ``val`` is a str FalkorDB can take as a query parameter.

    The ``id`` rides as a Cypher parameter exactly like a dim value, so it
    needs the SAME NUL/lone-surrogate gate — otherwise a corrupt journal line
    with such an id aborts ``rebuild_all`` after the wipe (review P1; the
    pre-existing PointRevised fold had the same latent hole).
    """
    return isinstance(val, str) and _annotator_value_ok(val)


def _annotator_dims(ev: dict, *, aliases: bool = False) -> dict:
    """Annotator dims PRESENT on a journal record, under their node-prop names.

    Presence-conditional (#3689): only keys the record actually carries are
    returned, so a partial ``update_point(annotator_bias=…)`` never clobbers
    the sibling dims it did not write. Canonical (long) names always win.

    ``aliases`` (default False) admits the :GraphEvent SHORT payload names
    (``bias``/``precision``/…) as aliases — and MUST be True only for an
    ``OperatorAnnotated`` record, whose documented payload uses them.
    ``update_point`` journals a ``PointRevised`` with the caller's props
    VERBATIM (``SET n += $props``), so a node prop literally named
    ``precision`` is exactly the ``precision`` property — treating it as
    ``annotator_precision`` would rename it on replay and silently clobber a
    real annotator dim (and the retrieval ordering that reads it; review P1).
    """
    # #2894/#2795 + review P1: a journal record can carry a value FalkorDB
    # cannot take (map/bytes/set, an array containing one, or a non-finite
    # float). The rejection lands in rebuild pass-1b — AFTER the wipe, on the
    # recovery path — so drop the dim here instead of aborting the rebuild
    # (the shared PointAdded writer drops the whole prop the same way).
    def _ok(val) -> bool:
        return _annotator_value_ok(val)

    dims: dict = {}
    for key in _ANNOTATOR_PROPS:
        if key in ev and _ok(ev[key]):
            dims[key] = ev[key]
    if aliases:
        for short, key in _ANNOTATOR_SHORT_ALIASES.items():
            if short in ev and key not in dims and _ok(ev[short]):
                dims[key] = ev[short]
    return dims


# Recognized journal record types the projection folds NOWHERE — audit-only
# markers plus the JSONL-only durability records replayed by a dedicated pass
# or deliberately deferred. The ``else`` warning in ``apply``/``rebuild_all``
# is reserved for a type OUTSIDE this set: a genuinely unknown record the fold
# cannot interpret (#3299 arose from exactly that — an unknown mutation
# vanishing silently under wipe+replay). Listing a type here is a claim that
# it is recognized-and-intentionally-not-folded; never add a type that has a
# real fold branch below (the branch would win anyway, but the set would then
# mislead the next reader about what is unknown).
_NO_PROJECTION_FOLD = frozenset({
    "IngestStarted",        # audit-only, no graph effect (pre-existing no-op)
    "BatchIdStamped",       # JSONL-only; replayed from the batch snapshot
                            # (rebuild_all pass-2b), not via this dispatcher
    "DirectEdgeCreated",    # JSONL-only; deliberately deferred (A10 #1048)
    "CalibrationRecorded",  # :Meta milestone marker (audit)
    "DedupeRecorded",       # #784 content-dedup audit
    "DedupeRejected",       # #784 content-dedup audit
    # #1370: a refused/suspected subject binding is an AUDIT record — the
    # edge it declined to write must NOT be replayed from it (the confidence
    # gate is a write-time policy decision, not graph state). Recognized-and-
    # intentionally-not-folded; without this entry every replay would log an
    # "unrecognized event type" warning per refusal.
    "EntityBindingRefused",
})

# ``_apply_one`` is the POINT-ONLY in-memory fold (a ``{id: point}`` dict), so
# every recognized non-point record is a no-op there as well: the non-point
# entities and the flat edge descriptor have no representation in that index.
# Union with ``_NO_PROJECTION_FOLD`` so this dispatcher's warning, like the
# Falkor ones, fires only for a type outside the projection vocabulary.
# NOTE: the point-lifecycle types folded by ``apply``/``rebuild_all``
# (PointPromoted / OperatorPromoted / PointSuperseded / PointInvalidated) are
# deliberately NOT listed — this index has no fold for them, so the warning is
# a true signal of the pre-existing in-memory-scope gap, not noise.
_NO_POINT_FOLD = _NO_PROJECTION_FOLD | frozenset({
    "EventRecorded",
    "SubjectAdded",
    "ObjectRegistered",
    "ObjectSuperseded",
    "DocumentCreated",
    "SourceCreated",
    "DirectEdgeRepoint",
    # JSONL-only siblings of the two above: both have REAL fold branches in
    # ``apply``/``rebuild_all``, but neither has a representation in this
    # ``{id: point}`` index (a Session node is not a Point; the about* edge
    # is a flat descriptor) — so without them every replayed record logged
    # the ``unrecognized event type`` warning, contradicting the set's
    # documented contract that the warning is reserved for a type OUTSIDE
    # the vocabulary.
    "EntityLinked",
    "SessionRecorded",
})


# ── #3860: the ONE ownership identity — (kind, id) ────────────────────────
# A node is owned by its (graph label, id) pair. Both the rebuild survivor
# anchor (``last_recreate_seq``) and the replay fold (``_fold_entity_mutation``
# → ``_delete_entity_by_id``) key on that pair, so a delete can never be
# suppressed by — or match — a node of another kind. For MUTATION and DELETE
# this table is the only declaration; the label→key mapping for RESOLUTION is a
# deliberate SUPERSET declared separately at ``_RESOLVE_BRANCHES`` (which adds
# ("Source", "url") for url-only ingestion stubs) and mirrored again in
# ``navigation._ROOT_BRANCHES`` — so do NOT read this as the only label→key
# table in the codebase, only as the authority for mutation/delete. A journal
# ``label`` is NEVER interpolated into the Cypher label position (it must be a
# member of ``_CANONICAL_ENTITY_LABELS``, else the fold falls back to the legacy
# id-wide delete). Mirrored by the live writers ``sdk._delete_entity`` and
# ``sdk._update_entity``.
_CANONICAL_ENTITY_ID_PROPS: tuple[tuple[str, str], ...] = (
    ("Point", "id"), ("Subject", "id"), ("Object", "id"),
    ("Source", "id"), ("Event", "eventId"),
)
_CANONICAL_ENTITY_LABELS: frozenset[str] = frozenset(
    label for label, _ in _CANONICAL_ENTITY_ID_PROPS)
_ENTITY_ID_PROP: dict[str, str] = {
    label: prop for label, prop in _CANONICAL_ENTITY_ID_PROPS}
_NON_POINT_ENTITY_LABELS: frozenset[str] = (
    _CANONICAL_ENTITY_LABELS - {"Point"})

# ── EntityMutated op vocabulary (#3299 / #3312 / #3377) ────────────────────
# THE one declaration. tortoise/live.py's #2901 lesson applies verbatim: a
# hand-written subset at a call site is how a value gets silently omitted from
# a reader filter (`outdated` was dropped from three of them that way). The
# producer imports this, both folds classify against it, and
# tests/test_unjournaled_mutation_class.py derives the relationship so that an
# op added without a fold arm REDs instead of shipping.
_ENTITY_MUTATION_OPS: tuple[str, ...] = (
    "delete", "rename", "restatus", "revise",       # fold arms exist
    "retract", "supersede",                          # recorded on #3299 — no arm yet
)
# FOLD capability, not producer reach: the `rename` arm is implemented and must
# stay so, but the PRODUCER deliberately does not emit `rename` yet — see
# `sdk._update_entity`'s `name` branch. The arm applies `state`, so a raw/legacy
# or future `rename` record folds ONLY if it carries the new name as
# `state["name"]`; a record carrying just a top-level `name` unfolds and is
# reported as a fold miss. #3377 returned to
# open; #4769 lands rename journalling WITH the structural sweep-ordering fix.
_ENTITY_MUTATION_IMPLEMENTED_OPS: frozenset[str] = frozenset(
    {"delete", "rename", "restatus", "revise"})
# The ops that carry a `state` payload (everything implemented except delete).
_ENTITY_MUTATION_STATE_OPS: frozenset[str] = frozenset(
    {"rename", "restatus", "revise"})
# HAND-MAINTAINED, not derived from the set difference: a derived set would
# auto-absorb an op added to ``_ENTITY_MUTATION_OPS`` with no classification,
# leaving the derivation test green on exactly the addition it exists to catch.
_ENTITY_MUTATION_PENDING_OPS: frozenset[str] = frozenset(
    {"retract", "supersede"})


def classify_entity_mutation_op(props: dict) -> str:
    """The write's PRIMARY INTENT → its ``EntityMutated`` ``op`` value.

    Precedence ``name`` > ``status`` > other. ``state`` always carries the
    write's full applied map, so a mixed write loses nothing by the labelling —
    a consumer wanting EVERY status transition must read ``state["status"]``
    and must NOT filter on ``op``.

    Lives here (not in ``sdk``) so the producer cannot invent a name; its return
    set is AST-asserted equal to ``_ENTITY_MUTATION_STATE_OPS`` by the test
    suite.
    """
    if "name" in props:
        return "rename"
    if "status" in props:
        return "restatus"
    return "revise"


def _warn_entity_mutation_fold_miss(op: str, rid, label, event_id) -> None:
    """The state-op fold-miss signal — the non-folded set for C1.

    Emitted INSIDE the fold, not at a call site: ``apply()`` discards the
    returned count, and ``rebuild(log)`` / ``recover_from_log`` / ``restore``'s
    JSONL fallback all fold through it, so a call-site-only warning would leave
    the non-folded-set assertion vacuous on three of the four replay engines.

    Deliberately NOT used for ``op="delete"``: a delete matching 0 rows is
    legitimately idempotent (a retried delete; restore's fallback replaying onto
    a non-empty graph) and the existing pass-1b warning already covers the
    rebuild_all case. Widening it would turn the lane's own evidence into a
    false positive on valid journals.

    ``event_id`` is carried: it is the only handle that locates the diverging
    journal line.
    """
    logger.warning(
        "rebuild: EntityMutated %s fold matched no entity (event_id=%s id=%r "
        "label=%r) — the journal claims a mutation whose entity never "
        "re-existed on this replay (post-wipe divergence or out-of-order "
        "journal)", op, event_id, rid, label)


def _owns_point(label: object) -> bool:
    """True when an ``EntityMutated``-style ``label`` may own a POINT node.

    #3860: a *known* non-Point canonical label does not own a Point; a
    missing/unknown label keeps the legacy id-wide semantics, so it is
    treated as possibly owning one (the fold falls back id-wide there too).
    """
    return not (isinstance(label, str) and label in _NON_POINT_ENTITY_LABELS)


def _apply_one(points: dict[str, dict], ev: dict) -> None:
    ev = _norm(ev)
    t = ev.get("type")
    if not isinstance(t, str):
        # #331: events without a 'type' key (or with a non-string type)
        # must be skipped, not crash the fold.
        return
    if t in ("PointAdded", "OperatorAdded"):
        # #331 (review r3): ev.get — a valid type with NO 'point' key at all
        # must be handled by the isinstance guard below, not KeyError here.
        p = ev.get("point")
        if not isinstance(p, dict):
            # Malformed event (non-dict/missing point) — skip, don't crash
            # (issue #325)
            return
        # #331: no id → nothing to index by; skip rather than KeyError.
        # #331 (review r4): str-only — a truthy non-string id (list/dict)
        # raised TypeError (unhashable) in the index op below.
        if not isinstance(p.get("id"), str):
            return
        # Phase 1 stop-writes: strip context from v2+ events (#49)
        if ev.get("projection_version", 0) >= 2:
            p.pop("context", None)
        points[p["id"]] = p
        # Flatten speaker from point's provenance into node data
        # #331: non-dict provenance (null/string) must not crash flattening.
        prov = p.get("provenance", {})
        if isinstance(prov, dict) and prov.get("speaker"):
            p["speaker"] = prov["speaker"]
    elif t == "PointRevised":
        # #331 (review r4): str-only lookup — dict.get(unhashable) raises.
        rid = ev.get("id")
        p = points.get(rid) if _writable_id(rid) else None
        if p:
            # #3689 review P2: gate the content edit EXACTLY as
            # ``_revise_point`` does — an UNWRITABLE new_content (NUL/lone-
            # surrogate str, map, non-finite float, ...) is dropped there and
            # must be dropped here too, or the pure fold and ``rebuild_all``
            # silently disagree, breaking the #330 parity contract this module
            # declares (``_apply_one`` is the single source of fold
            # semantics).
            new_content = ev.get("new_content")
            if new_content is not None and _annotator_value_ok(new_content):
                p["content"] = new_content
            # Phase 1: discard new_context for v2+ events (#49)
            if ev.get("new_context") is not None and ev.get("projection_version", 0) < 2:
                p["context"] = ev["new_context"]
            # #3689: fold the annotator dims carried as PointRevised extras
            # (update_point's emit), canonical names only. Presence-
            # conditional — never clobber a sibling dim the revision did not
            # carry.
            p.update(_annotator_dims(ev))
            # #2884 FIX-4: fold the four belief props when the revision
            # carries them. `update_point(confidence=...)` writes them LIVE
            # (`SET n += $props`) and emits them in the PointRevised payload,
            # but the replay fold built its SET clauses only from the
            # content/annotator fields and silently discarded them — so a
            # rebuild dropped a caller-set confidence. Same presence-
            # conditional shape and the SAME value gate as
            # `_fold_confidence_changed` / the ConfidenceChanged branch
            # (#330 parity).
            for _bk in BELIEF_PROPS:
                if _bk in ev and _belief_prop_value_ok(_bk, ev[_bk]):
                    p[_bk] = ev[_bk]
    elif t == "OperatorAnnotated":
        # #3689: the explicit annotation record — parity with apply() /
        # rebuild_all pass-1b. A plain property write on the operator's
        # ``{id: point}`` entry. Short payload aliases ARE admitted here.
        rid = ev.get("id")
        p = points.get(rid) if _writable_id(rid) else None
        if p:
            p.update(_annotator_dims(ev, aliases=True))
    elif t == "PointRetracted":
        # #689: tombstone instead of hard delete — retracted content stays
        # recoverable via raw graph queries. Historical data loss prior to
        # this change is irreversible (already-hard-deleted points cannot
        # be reconstructed from the event log alone — the content existed
        # only in the projection, and the projection deleted it).
        # #331 (review r4): str-only lookup — dict.get(unhashable) raises.
        rid = ev.get("id")
        p = points.get(rid) if isinstance(rid, str) else None
        if p:
            p["status"] = "retracted"
    elif t == "PointsMerged":
        # #331 (review r2): `or []` also covers an explicit "merge_ids": null
        # in the log — dict.get(key, []) only covers the missing key.
        for mid in ev.get("merge_ids") or []:
            # #331 (review r4): str-only — dict.pop(unhashable) raises.
            if isinstance(mid, str):
                points.pop(mid, None)
    elif t == "EntityMutated":
        # #3299/#3860: the write-surface mutation record. In-memory points are
        # keyed by id, so `op=delete` drops the point; other ops (rename/
        # restatus) are no-ops on this pure-point index until a sibling
        # extends them. #3860: identity is (kind, id) — a delete naming a
        # DIFFERENT canonical kind does not own a Point and must not pop it;
        # a missing/unknown label keeps the legacy id-wide delete (parity
        # with the graph fold's fallback).
        if ev.get("op") == "delete":
            rid = ev.get("id")
            if isinstance(rid, str) and _owns_point(ev.get("label")):
                points.pop(rid, None)
        elif ev.get("op") in _ENTITY_MUTATION_STATE_OPS:
            # Explicit no-op: this projection indexes POINTS only, so the five
            # canonical labels these ops mutate are outside its model. Named
            # here (not a silent fall-through) so the absence is a decision a
            # reader can see, matching the graph fold's contract.
            pass
        elif ev.get("op") in _ENTITY_MUTATION_PENDING_OPS:
            logger.warning(
                "in-memory fold: EntityMutated op %r is recorded (#3299) but "
                "has no fold arm yet — no fold applied", ev.get("op"))
        else:
            logger.warning(
                "in-memory fold: unknown EntityMutated op %r — no fold applied",
                ev.get("op"))
    elif t == "ConfidenceChanged":
        # #2884 D3: the EP/dream belief-state write-back — parity with the
        # graph folds (`FalkorProjection.apply` / `rebuild_all`).
        # Presence-conditional, exactly like `_fold_confidence_changed`: only
        # keys the record carries are written; a key present with null writes
        # null. `_belief_prop_value_ok` is the ONE value gate the graph fold
        # also uses, so the pure fold and the graph fold cannot disagree on a
        # corrupt line (#330 parity).
        rid = ev.get("id")
        p = points.get(rid) if _writable_id(rid) else None
        if p:
            for key in BELIEF_PROPS:
                if key not in ev:
                    continue
                value = ev[key]
                if not _belief_prop_value_ok(key, value):
                    continue
                p[key] = value
            # #2884 A5: the boolean flag riding the same record
            # (`assess_source`'s `outdated=true`) — same presence-conditional
            # shape and its own strict bool gate, so the pure fold and the
            # graph fold agree.
            for key in BELIEF_BOOL_PROPS:
                if key not in ev:
                    continue
                value = ev[key]
                if not _belief_bool_value_ok(value):
                    continue
                p[key] = value
    elif t in _NO_POINT_FOLD:
        # Recognized, intentionally NOT folded by this point-only index:
        # audit markers, the JSONL-only records replayed by a dedicated pass,
        # and the non-point/edge records with no ``{id: point}`` entry. This
        # warning is reserved for a type OUTSIDE the vocabulary (see the
        # ``_NO_PROJECTION_FOLD`` / ``_NO_POINT_FOLD`` rationale above).
        pass
    else:
        # P2-1 (#3299): a record type outside the recognized vocabulary must
        # not vanish silently.
        logger.warning("unrecognized event type %r — skipped", t)


def fold(events: list[dict]) -> dict[str, dict]:
    points: dict[str, dict] = {}
    for ev in events:
        _apply_one(points, ev)
    return points


def split(points: dict[str, dict]) -> tuple[list[dict], list[dict]]:
    """Partition into (statement points, operator points)."""
    statements, operators = [], []
    for p in points.values():
        (operators if p.get("operator") else statements).append(p)
    return statements, operators


# ── Protocol + InMemory ───────────────────────────────────────────────────


@runtime_checkable
class Projection(Protocol):
    def apply(self, event: dict) -> None: ...
    def rebuild(self, log) -> None: ...


class InMemoryProjection:
    def __init__(self):
        self.points: dict[str, dict] = {}

    def apply(self, event: dict) -> None:
        _apply_one(self.points, event)

    def rebuild(self, log) -> None:
        self.points = fold(log.read_all())


def _validate_uri_scheme(scheme: str) -> str:
    """Accept docker:// (local) and redis:// / rediss:// (FalkorDB Cloud) URIs.

    Raises ValueError for anything else, mirroring the historical docker://-only
    contract while making managed-instance URIs first-class. The scheme set is
    imported from tortoise.config (SUPPORTED_URI_SCHEMES) so the URI-routing
    checks (resolve_db_path / is_db_uri / __main__._resolve_db_target) and this
    connection-layer validation cannot drift (#715).
    """
    if scheme not in SUPPORTED_URI_SCHEMES:
        raise ValueError(
            f"Unsupported scheme: {scheme} "
            f"(expected docker://, redis://, or rediss://). "
            f"Example: docker://:password@localhost:6379/tortoise"
        )
    return scheme


class DbEndpoint(NamedTuple):
    """Resolved FalkorDB server endpoint (the canonical URI → client kwargs)."""

    host: str
    port: int
    username: str | None
    password: str | None
    graph_name: str
    ssl: bool


def resolve_db_endpoint(uri: str, graph_name: str | None = None) -> DbEndpoint:
    """Parse a connection URI into FalkorDB client endpoint parameters.

    THE canonical URI → endpoint derivation (#2974): ``FalkorProjection.
    from_uri`` (every product connection) and ``tortoise.backup._bgsave`` (the
    backup snapshot) both call this, so a backup can never dial a different
    instance than the product it is backing up. Before this existed the same
    parse was inlined in ``from_uri`` and independently re-implemented by
    callers — one of which hardcoded an embedded ``localhost:16379``.

    ``graph_name`` overrides the URI-path-derived name (multi-tenant
    isolation, #7886); when None the URI path is used (default "tortoise").
    Unsupported schemes raise ValueError via ``_validate_uri_scheme``.
    """
    from urllib.parse import urlparse
    parsed = urlparse(uri)
    _validate_uri_scheme(parsed.scheme)
    # #3039: urlparse does NOT percent-decode userinfo while redis.from_url
    # does. This resolver is THE canonical URI → client-kwargs derivation
    # (from_uri and backup._bgsave both call it), so the SINGLE shared decode
    # rule is applied HERE — otherwise a password needing percent-encoding
    # reaches FalkorDB as a literal %XX and auth fails misleadingly.
    username, password = parse_uri_userinfo(uri)
    return DbEndpoint(
        host=parsed.hostname or "localhost",
        port=parsed.port or 16379,
        username=username,
        password=password,
        graph_name=(graph_name if graph_name is not None
                    else (parsed.path.lstrip('/') or "tortoise")),
        ssl=(parsed.scheme == "rediss"),
    )


# ── FalkorProjection ──────────────────────────────────────────────────────


class RebuildDroppedEpisodicPoints(RuntimeError):
    """#3947: a wipe+replay rebuild destroyed episodic Points it could not rebuild.

    Raised by ``FalkorProjection.rebuild`` / ``rebuild_all`` instead of
    returning normally. A rebuild is the moment an operator believes the graph
    was RESTORED — reporting success while captured turns are gone is the
    false PASS this exception exists to make impossible.

    #3947 review: it is ALSO the refusal signal for "the pre-wipe proof could
    not be computed" (an unreadable episodic roster, or a failed ``:Session``
    snapshot read). Both cases refuse *before* any destructive statement, so
    the store is untouched — the exception is catchable and carries a
    message an operator can act on, which is why ``tortoise.__main__``'s
    ``_cmd_rebuild`` handles it rather than letting a driver traceback out.
    """


# #3947 review: the journal event types whose replay CREATES a Point/Operator
# node (an `_upsert` / `_upsert_point_props` MERGE). Kept next to the exception
# so the pre-wipe proof and `apply()`'s branches are read together — omitting a
# type here makes the proof REFUSE a rebuild the replay would have completed
# (a false block on a healthy store).
_JOURNAL_CREATING_EVENT_TYPES = frozenset({
    "PointAdded", "OperatorAdded", "PointPromoted", "OperatorPromoted",
})


# #3722 review (cycle 5) P2: the labels a journaled hard delete can actually
# REMOVE. Replay's ``EntityMutated`` op=delete replays ``_delete_entity_by_id``
# — #3860 SCOPED it to the record's own canonical ``label``, so a delete record
# removes that ONE label; only a MISSING/unknown label falls back to the legacy
# id-wide delete across all six. The live ``_delete_entity`` is the same six and
# documents "Session/APIKey/Org/Tag nodes are intentionally NOT deleted".
# ``PointsMerged`` deletes Points only. So a journaled hard delete can NEVER
# remove a ``:Session`` node, and the staleness rule must not suppress a
# Session-source link just because an unrelated Point/Object with the same id
# was deleted later. #3722 review (cycle 6) P2: the reader keys these sets
# PER LABEL under the id (``{id: {label: max_seq}}``), so a delete that cannot
# remove a label never supplies that label's boundary seq either — unioning
# them across deletes conflated an ``EntityMutated`` delete with a later
# ``PointsMerged`` and over-suppressed a live link.
_HARD_DELETE_LABELS = frozenset({
    "Point", "Subject", "Object", "Source", "Event",
})
_POINTS_MERGED_LABELS = frozenset({"Point"})


def journal_hard_delete_seqs(events) -> dict[str, dict[str, int]]:
    """Per-``(id, label)`` journal seq of the LAST hard delete that can remove
    that label — ``{id: {label: max_delete_seq}}``.

    EXACT, not conservative: the inner map records, per label the delete can
    actually REMOVE, the latest seq of such a delete, so
    ``_hard_delete_suppresses`` is literally "is there a hard delete AFTER seq
    L that can remove THIS label?" — ``entry.get(label) > L``.

    The hard-delete EVENT TYPES are the same ones ``_journal_hard_deleted_ids``
    derives (``EntityMutated`` op=delete, #3299; ``PointsMerged``, whose
    merged-away ids replay through ``_delete``) — but the ID SETS can differ:
    that helper gates each id by ``_owns_point`` (#3860), dropping an id whose
    record names a known non-Point kind. (Envelope shape is NOT a difference:
    both readers normalize, so a nested payload is seen either way — #3722.)
    Retraction is deliberately
    NOT included — ``_retract`` tombstones and the node survives. The OUTER
    key is the id; the INNER keys are the labels that delete removes:

    * ``EntityMutated`` op=delete replays ``_delete_entity_by_id``, which
      #3860 scoped to the record's own canonical ``label`` — that label ALONE
      gets the seq. A missing/unknown label falls back to the legacy id-wide
      delete across ``_HARD_DELETE_LABELS`` (all six get the seq), matching
      ``_delete_entity_by_id(label=None)``.
    * ``PointsMerged`` replays ``_delete`` — a ``:Point`` only
      (``_POINTS_MERGED_LABELS``), so only ``Point`` gets the seq.

    A ``:Session`` source is therefore never suppressed — no journaled hard
    delete can remove it — so a same-id delete of another label cannot drop a
    live ``(Session)-[:aboutObject]->(Object)`` edge (live != replay).

    #3722 review (cycle 6) P2 — the SHAPE must be per-``(id, label)``. An
    earlier revision paired the MAX delete seq for an id with a label set
    UNIONED across ALL of that id's deletes, so the boundary asked "could SOME
    delete of this id remove this label?" while comparing against the LATEST
    delete's seq. That is unsound whenever the latest delete is a
    ``PointsMerged`` (removes Points only) while the label came from an
    EARLIER ``EntityMutated`` — which at that revision recorded all six
    labels, the fold being id-wide before #3860: it OVER-suppressed, silently
    DROPPING a live edge on replay (an Object re-created under a
    deleted-then-merged id lost its ``(Point)-[:aboutObject]->(Object)`` link
    in every engine). Per-label max seq removes the cross-delete conflation
    entirely — there is no residual over-approximation to document here.

    Used by the ``EntityLinked`` fold: the fold is an unconditional
    MATCH…MERGE, so without this boundary a link whose endpoint was deleted
    and later re-created under the SAME id came back on replay while live had
    no such edge (ids are reused routinely). The seq space is the enumerate
    index over the SAME journal list the replay engines walk, so callers must
    pass the full events list, un-filtered.

    The payload is read from the NORMALIZED record (``_norm``), not the raw
    envelope. ``_norm`` splices ``ev["point"]`` over the envelope, and THAT
    is the shape ``apply()``'s ``PointsMerged`` branch and ``rebuild_all``'s
    pass-1b branch delete through. The nested shape is a SUPPORTED journal
    shape, pinned by
    ``tests/test_projection.py::test_falkor_apply_points_merged_nested_format``
    (#325) — ``{"type":"PointsMerged","point":{"keep_id":k,
    "merge_ids":[x]}}``. Reading the RAW record missed the delete entirely
    (returned ``{}``), so the staleness rule never suppressed a link whose
    endpoint was merged away, and the deleted link resurrected on the
    re-created point. A nested ``EntityMutated`` op=delete was hidden the
    same way.
    """
    out: dict[str, dict[str, int]] = {}
    for seq, ev in enumerate(events):
        if not isinstance(ev, dict):
            continue
        # #3722 review (cycle 5): read the NORMALIZED record — the shape the
        # replay folds actually delete through (see docstring SHAPE).
        ev = _norm(ev)
        t = ev.get("type")
        if t == "EntityMutated" and ev.get("op") == "delete":
            rid = ev.get("id")
            if isinstance(rid, str):
                # #3860: identity is (kind, id) — the fold is SCOPED to the
                # record's own canonical label, so the labels THIS delete can
                # remove are that label alone. A missing/unknown label falls
                # back to the legacy id-wide delete, matching
                # ``_delete_entity_by_id(label=None)``.
                label = ev.get("label")
                labels = ({label} if isinstance(label, str)
                          and label in _CANONICAL_ENTITY_LABELS
                          else _HARD_DELETE_LABELS)
                _merge_hard_delete(out, rid, seq, labels)
        elif t == "PointsMerged":
            # #331: `or []` also covers an explicit "merge_ids": null.
            for mid in ev.get("merge_ids") or []:
                if isinstance(mid, str):
                    _merge_hard_delete(out, mid, seq, _POINTS_MERGED_LABELS)
    return out


def _merge_hard_delete(out, rid, seq, labels) -> None:
    """Record a hard delete of ``rid`` at ``seq``: per-label MAX seq.

    ``labels`` is the set of labels THIS delete can remove, so each label's
    slot is advanced INDEPENDENTLY — never unioned across deletes (#3722
    review cycle 6: unioning is what conflated an ``EntityMutated`` delete
    with a later ``PointsMerged``).
    """
    by_label = out.get(rid)
    if by_label is None:
        by_label = {}
        out[rid] = by_label
    for label in labels:
        prev = by_label.get(label)
        if prev is None or seq > prev:
            by_label[label] = seq


def _hard_delete_suppresses(hard_delete_seqs, endpoint_id, label,
                            seq) -> bool:
    """True when a recorded hard delete of ``(label, endpoint_id)`` lands AFTER
    ``seq`` — exactly "a delete after L that can remove THIS label" (#3722
    review cycle 6 P2; see ``journal_hard_delete_seqs``).

    ``endpoint_id``/``label`` come from a journal FILE, so BOTH are type-gated:
    a list/dict label would raise ``TypeError: unhashable`` on the membership
    test, and a non-string id could never have been written (``_writable_id``)
    so it cannot be stale.
    """
    if not isinstance(endpoint_id, str) or not isinstance(label, str):
        return False
    entry = hard_delete_seqs.get(endpoint_id)
    if not entry:
        return False
    if isinstance(entry, dict):
        del_seq = entry.get(label)
        return del_seq is not None and del_seq > seq
    if isinstance(entry, tuple):
        # Cycle-5 ``{id: (max_seq, labels)}``: no in-repo producer emits this
        # any more; the union it carries OVER-suppresses (cycle 6), so it is
        # tolerated only for an external caller holding the old shape.
        del_seq, labels = entry
        return del_seq > seq and label in labels
    # Pre-cycle-5 ``{id: int}`` (no labels): fall back to the id-only test over
    # the removable-label set.
    return entry > seq and label in _HARD_DELETE_LABELS


class FalkorProjection(
    _EntityHandlers,
    _EdgeHandlers,
    _GroundingMixin,
    _PropagationMixin,
):
    """FalkorDB-backed projection. Supports Docker/server (default) and embedded modes.

    Embedded:  FalkorProjection(path='/tmp/tortoise.db')
    Docker:    FalkorProjection(host='localhost', port=16379, password='...')
    URI:       FalkorProjection.from_uri('docker://:pass@host:6379/graph')

    Same API regardless of backend — constructor swap is the only difference.
    """

    def __init__(self, path: str | None = None, *,
                 host: str | None = None,
                 port: int = 16379,
                 username: str | None = None,
                 password: str | None = None,
                 graph_name: str = "tortoise",
                 ssl: bool = False,
                 allow_nonstandard_path: bool = False,
                 skip_health_check: bool = False):

        # Epic #1647 (D-1=A): capture whether the caller passed an explicit
        # path BEFORE the no-arg fallback below resolves it — the redirect
        # must fire for explicit path= constructions only (no-arg keeps the
        # canonical embedded path).
        explicit_path = path is not None
        # Epic #1647 (PR #1684 CI-fix): preserve the ORIGINAL explicit path —
        # the redirect nulls `path` to fall through to the host branch, and
        # downstream derivation (pack_state's lock/target resolution for
        # explicit legacy graph names) must reproduce the redirect's hash
        # inputs (session + ORIGINAL path + name).
        self._explicit_path = path

        # No-arg construction -> canonical embedded path (plan Task 9: graph-
        # scripts migrated from FalkorProjection('tortoise.db') to no-arg,
        # which must resolve to the canonical TORTOISE_DB_PATH).
        if path is None and host is None:
            from tortoise.config import resolve_db_path
            path = resolve_db_path()

        # CI-discovered (#1684): the relative-path + tilde reject is a CLI
        # CONTRACT that must hold in BOTH lanes — hoisted from the embedded
        # branch (L584) so the test redirect cannot swallow it. A relative
        # db target is invalid whether it would have gone embedded or to the
        # server; _cmd_decide/_cmd_init's error-semantics tests
        # (test_cli_context) assert rc==1 + "Invalid DB target" and must not
        # silently redirect onto the server.
        if path is not None:
            _allow_nonstandard = (
                allow_nonstandard_path
                or os.environ.get("TORTOISE_ALLOW_NONSTANDARD_PATH") == "1")
            if path != ":memory:" and not os.path.isabs(path) and not path.startswith("~"):
                raise ValueError(RELATIVE_PATH_ERROR.format(path=path))
            if path.startswith("~") and not _allow_nonstandard:
                raise ValueError(RELATIVE_PATH_ERROR.format(path=path))

        # ── Epic #1647 (D-1=A): class-level URI-aware test redirect ────────
        # Fires ONLY in a test session (TORTOISE_TEST_MODE=1, exported by
        # conftest) with a supported TORTOISE_DB_URI AND a calling test frame
        # (cycle-2 P1-1b: subprocess CLI children inherit the env but have no
        # test_ module in their stack, so _caller_test_stem() returns None and
        # they never redirect). Prod tools (backup.py, __main__.py rebuild,
        # ingest.py, migrate_db.py, hosted_api.py, pipeline_cli.py) construct
        # with explicit paths but never run under TEST_MODE, so they never
        # redirect (P0-4) — and their entry points pop TEST_MODE at startup
        # anyway (cycle-2 P2-10). Explicit path= only (D-1 option a): no-arg
        # keeps the embedded canonical path (captured via explicit_path
        # above). TORTOISE_TEST_NO_REDIRECT (comma-separated TEST-MODULE
        # stems) exempts carve-out files via caller frame inspection (P0-1) —
        # the DB-file basename is NEVER the key.
        _uri = os.environ.get("TORTOISE_DB_URI")
        if (explicit_path and _uri
                and os.environ.get("TORTOISE_TEST_MODE") == "1"
                and _is_supported_uri_scheme(_uri)):
            _no_redirect = {
                s.strip() for s in
                os.environ.get("TORTOISE_TEST_NO_REDIRECT", "").split(",") if s.strip()
            }
            _caller_stem = _resolve_caller_stem()  # None = no test frame
            # CI P2 fix: the process flag (conftest-set) fires the redirect
            # in TestClient worker threads where the frame gate sees no test_
            # module. Since #1686 the carve-out exemption is thread-inherited
            # (conftest records the running module's stem; the patched
            # Thread.start stamps it onto spawned threads), so worker threads
            # in carve-out files DO resolve their own exempt stem. A None
            # stem is still exempt-free (subprocess CLI children: no conftest,
            # no record, no inheritance).
            if ((_caller_stem is not None and _caller_stem not in _no_redirect)
                    or (_caller_stem is None and _TEST_SESSION_ACTIVE)):
                from urllib.parse import urlparse
                _parsed = urlparse(_uri)
                _validate_uri_scheme(_parsed.scheme)
                # Cycle-8 P2-1: NO `or "localhost"` fallback — a hostless URI
                # (`docker://:pw@:6379`) must resolve host=None so the shared
                # predicate refuses it fail-closed, matching `is_loopback_uri`
                # (absent hostname → not in LOOPBACK_HOSTS → False). The old
                # fallback diverged: the redirect accepted (hostname or
                # "localhost") while the Task 4 tripwire refused.
                host = _parsed.hostname  # None when absent → not loopback
                # Cycle-2 P0-2 (fail-fast): refuse non-loopback hosts BEFORE
                # the first write — a typo'd/shared TORTOISE_DB_URI must never
                # mint test_* graphs on a remote server (wipe_server's refusal
                # is too late: it fires after every migrated construction
                # already wrote). Cycle-3 P2-6: the escape is WRITE-ONLY by
                # design — an ALLOW_REMOTE session can write on a remote
                # server but wipes still refuse (D-4 unchanged).
                # P2-2 (review): port is parsed AFTER the host refusal — a
                # bare IPv6 literal (`docker://:pw@::1:6379`) makes
                # urlparse(...).port raise ValueError before the loopback
                # check; parsing it after keeps the fail-closed RuntimeError
                # the single refusal path (hostless/bare-IPv6 alike).
                if not _is_loopback_host(host) \
                        and os.environ.get("TORTOISE_TEST_ALLOW_REMOTE") != "1":
                    raise RuntimeError(
                        f"test redirect refuses non-loopback host {host!r} — "
                        f"TORTOISE_DB_URI must point at a local docker (D-4); "
                        f"set TORTOISE_TEST_ALLOW_REMOTE=1 to override")
                port = _parsed.port or 16379
                # #3039: urlparse does NOT percent-decode userinfo — the
                # single decode rule lives in tortoise.config.
                username, password = parse_uri_userinfo(_uri)
                ssl = (_parsed.scheme == "rediss")
                _sess = os.environ.get("TORTOISE_TEST_SESSION", "")
                if path == ":memory:":
                    # P2-13: :memory: is a constant string — a path-hash would
                    # collide every :memory: construction onto one shared
                    # graph; embedded :memory: is fresh per construction, so
                    # derive a per-construction unique test_memory_* graph.
                    # Cycle-6 P2-14: os.urandom(6).hex() = 12 hex = 48 bits
                    # (was 8 hex/32 bits) — the width matches the session-
                    # nonce/hash guards (P2-1/P2-17).
                    _graph = f"test_memory_{os.urandom(6).hex()}"
                elif graph_name.startswith(("test_", "tortoise_test")):
                    # Cycle-2 P0-1b: a TEST-PREFIXED explicit name is the
                    # shared opt-in — honored verbatim (test_suite_<uuid>
                    # seam, explicit test_* names). Same name across sites =
                    # same server graph.
                    _graph = graph_name
                else:
                    # Cycle-2 P0-1b: explicit non-guard-passing names ("test",
                    # "t", "org_...") derive PER-PATH names — the parity/
                    # g_consistency pairs construct distinct paths with one
                    # shared explicit name and must land on DISTINCT server
                    # graphs (a shared rename makes the apply-vs-rebuild
                    # parity comparison a graph-vs-itself vacuous pass, #942
                    # class). Same path + same explicit name shares (the
                    # embedded same-file analog). The session nonce
                    # (conftest-exported TORTOISE_TEST_SESSION) keeps
                    # concurrent sessions' same-path derivations distinct
                    # (cycle-2 P2-3). Cycle-3 P2-5: the path-derived stem is
                    # SANITIZED to [a-zA-Z0-9_] — /tmp/seam-test-b.db →
                    # seam_test_b (hyphens/slashes must never ride into the
                    # graph name). Cycle-3 P2-17: 12 hex = 48+ bits (was
                    # 8 hex = 32 bits) — collision-safe at multi-thousand-
                    # graph scale.
                    assert path is not None  # explicit_path guarantee (mypy narrow)
                    _stem = os.path.splitext(os.path.basename(path))[0]
                    _stem = re.sub(r"[^a-zA-Z0-9_]", "_", _stem)
                    # CI P2 fix: fold the explicit graph_name into the hash.
                    # The per-path-only derivation COLLAPSED namespaces — two
                    # org_<ns> SDKs on the SAME temp path (test_hosted_api's
                    # cross-org isolation, quota tests) derived the SAME
                    # server graph, destroying org isolation. hash(path+name)
                    # keeps parity pairs (distinct paths, one shared name)
                    # distinct AND namespace pairs (one path, distinct names)
                    # distinct — the embedded same-file/same-name analog
                    # (same path + same name) still shares.
                    _graph = (f"test_{_stem}_"
                              + hashlib.sha1(
                                  (_sess + path + graph_name).encode()
                              ).hexdigest()[:12])
                path = None  # fall through to the host-mode branch below
                graph_name = _graph
                # Cycle-8 P2-5: append the mint to the product-side session
                # journal so the per-test wipe delta and the session-end
                # sweep see it. During the P1 window (Task 2 not yet landed)
                # the env var is absent → the append no-ops (never fails).
                _journal_append_product(_graph)

        if path is not None:
            # Hard-reject relative paths (plan Task 7, issue #176): a relative
            # path like 'tortoise.db' resolves per-CWD and silently creates a
            # per-directory redislite server (Category-3 leak). Relative is
            # ALWAYS rejected — the escape hatch only permits absolute
            # non-canonical paths. Env TORTOISE_ALLOW_NONSTANDARD_PATH=1
            # enables the same escape hatch without the kwarg.
            if not allow_nonstandard_path and \
                    os.environ.get("TORTOISE_ALLOW_NONSTANDARD_PATH") == "1":
                allow_nonstandard_path = True
            if path == ":memory:":
                # redislite in-memory server — not a file path, exempt from
                # the relative-path reject (test_open_kinds uses it).
                pass
            elif not os.path.isabs(path) and not path.startswith("~"):
                raise ValueError(RELATIVE_PATH_ERROR.format(path=path))
            if path.startswith("~") and not allow_nonstandard_path:
                # tilde is only valid if expanded to absolute via env;
                # unexpanded it is relative-like -> reject with hint
                raise ValueError(RELATIVE_PATH_ERROR.format(path=path))

            # Embedded mode (opt-in via path=). Use tortoise's guarded
            # FalkorDB subclass (issue #1005): the plain redislite class
            # bypasses the relative-path guard and has no lifecycle (atexit /
            # context manager) — every embedded projection client leaked its
            # server on exit. The subclass passes path/host through unchanged.
            from tortoise import FalkorDB  # lazy: keep import optional
            # #915 — embedded durability: enable AOF (appendonly) so a kill -9 of
            # the redis-server daemon loses at most the last ~1s of writes instead
            # of the whole graph since the last RDB save (RDB snapshots never fire
            # for small graphs: save 900 1 / 300 100 / 60 200 / 15 1000).
            #
            # Durability contract (embedded file-backed mode):
            #  - AOF binds at daemon COLD start (redislite reuses a live daemon
            #    via .settings without re-applying serverconfig). Restart any
            #    long-running embedded daemon after deploying this change.
            #  - AOF everysec fsync = ≤1s residual loss window on kill -9.
            #  - appenddirname is PER-DB-FILENAME (default "appendonlydir" is a
            #    per-directory name — two embedded DBs in one directory would
            #    share the AOF dir and leak nodes across graphs, #915).
            #  - The AOF dir is a LIVE durability artifact, NOT a backup
            #    artifact — restores/migrates must remove a stale one at the
            #    target path (Redis loads AOF in preference to RDB).
            #  - :memory: is exempt (no file to persist).
            aof_enabled = _embedded_aof_enabled()
            aof_dir = (
                os.path.basename(os.path.abspath(path)) + "-appendonlydir"
            ) if (path != ":memory:" and aof_enabled) else None
            # #3350: the embedded client was built with NO socket timeouts of
            # OUR choosing, so every blocking call on it was bounded only by
            # redis-py's own implicit connection default (5s) — invisible to
            # operators, and multiplied by the client's retry policy.
            # Measured against a SIGSTOPped embedded daemon: one `RETURN 1`
            # blocked ~59s, outliving the /health probe's 1.5s outer bound by
            # ~40x and parking the probe worker that whole time (#3350).
            # `TORTOISE_FALKORDB_SOCKET_TIMEOUT_S` — the knob monitoring.py's
            # budgets are documented in terms of, and the one an operator
            # lowers to tighten the loop-stall ceiling — had no effect on this
            # lane at all. Wire the SAME resolved READ timeout the host branch
            # uses (redis-py's UnixDomainSocketConnection applies
            # `socket_timeout` to the socket after connect, which is the leg a
            # wedged daemon parks on) and pair it with `_embedded_retry()` so
            # the worst case is a knowable `2 x socket_timeout`, not an
            # 11x-multiplied dependency default.
            #
            # NOT passed here: `socket_connect_timeout`. redis-py 8's
            # ConnectionPool does not forward it for a unix-domain socket
            # (verified: `Redis(unix_socket_path=..., socket_connect_timeout=
            # 1.5)` leaves `conn.socket_connect_timeout` at redis-py's own 5s
            # default), so passing it would be a bound that looks wired but
            # is not — the trap this change exists to remove. Its own 5s
            # default still applies, and a local UDS connect cannot be the
            # black hole: the read leg is. (Operators wanting that leg too
            # need a custom connection class — filed separately.)
            read_to = _socket_timeouts()[1]
            self.db = FalkorDB(
                path,
                serverconfig=(
                    {"appendonly": "yes", "appenddirname": aof_dir}
                    if (path != ":memory:" and aof_enabled) else None
                ),
                socket_timeout=read_to,
                retry=_embedded_retry(),
            )
        elif host is not None:
            # Docker FalkorDB
            from falkordb import FalkorDB  # ponytail: lazy import, only needed for Docker mode
            connect_to, read_to = _socket_timeouts()
            self.db = FalkorDB(host=host, port=port, username=username, password=password,
                               socket_connect_timeout=connect_to, socket_timeout=read_to,
                               ssl=ssl)
            # Epic #1647 (cycle-3 P0-1): record the host ON THE PROJECTION so
            # wipe_server/session sweep/tripwire read it instead of the raw
            # client (redis-py 8.1.0 has no .host on the client — the host
            # lives in connection_pool.connection_kwargs['host']). The
            # redirect's reassigned host local flows into this branch, and
            # from_uri → cls(host=...) lands here too — every server
            # projection carries its host.
            self._host = host
        else:
            raise ValueError("Either path or host must be provided")

        self.g = _GuardedGraph(self.db.select_graph(graph_name), self)
        self._probe_error: BaseException | None = None
        self.graph_name = graph_name
        self._graph_name = graph_name
        self._skip_guard = False
        self._is_embedded = (path is not None)
        self._path = path
        # #5119: `_vector_index_api` MUST exist before the health check below.
        # Recovery replays the journal through `apply()` -> `_upsert_point_props`,
        # which reads `self.required_embedding_dim` to guard a journalled
        # vector's WIDTH — and that property reads THIS attribute. Initialised
        # only further down (after `_ensure_indexes`), it raised
        # `AttributeError` on every replayed event, so `recover_from_log`
        # counted zero applied events and refused with "replay produced an
        # empty graph": a total graph loss became UNRECOVERABLE, and the only
        # signal was a warning. `None` is the property's own documented answer
        # while no index exists, and embedded (`redislite`) is brute-force by
        # design, so hoisting the initialisation is behaviour-preserving for
        # every path that reaches `_ensure_indexes`.
        self._vector_index_api = None
        # Ops safety residual (#428): auto health check on open + transparent
        # corruption recovery. Embedded DBs rebuild from their adjacent JSONL
        # event log when lost/corrupt; production (FLY_APP_NAME) and server
        # mode fail loud instead. Runs BEFORE _ensure_indexes so a corrupt
        # DB is recovered before index creation. skip_health_check is the
        # escape hatch for the `tortoise rebuild` CLI itself (it IS the
        # recovery tool — gating it on a healthy DB would be circular).
        if not skip_health_check:
            self._auto_health_recover()
        self._falkordb_version = self._get_falkordb_version()
        if self._falkordb_version is not None and self._falkordb_version[0] < 4:
            import logging
            logging.getLogger(__name__).warning(
                "FalkorDB version %s is below minimum 4.x. FTS and vector indexes will be skipped.",
                '.'.join(map(str, self._falkordb_version)))
        elif self._falkordb_version is None:
            import logging
            # #1359: a None version is NOT a failure — index creation probes
            # the engine directly (procedure vs Cypher-native fallback) and
            # embedded/older engines skip FTS/vector gracefully. Downgraded
            # from warning → info so a working engine doesn't spam.
            logging.getLogger(__name__).info(
                "Could not determine FalkorDB version — index creation will "
                "probe the engine's API directly.")
        # #1359: which vector-index API succeeded at _ensure_indexes time
        # ('procedure' | 'cypher' | None) — recorded on the projection and
        # consumed by search_engine.run_vector_query (threaded from sdk.py's
        # degradation_chain and cross-lens calls): 'cypher' engines skip the
        # failing signature-A attempt and query via signature B directly,
        # saving one failed round trip per query.
        #
        # Its `None` default is set ABOVE, before the health check — the
        # recovery replay reads it through `required_embedding_dim`, so it must
        # exist first (#5119).
        self._ensure_indexes()

        # Lifecycle hardening (plan Task 4 + issue #1005):
        # - _closed flag for idempotent close()
        # - atexit so a NORMAL process exit never orphans the server (the
        #   dominant #1005 leak path). #1475: the atexit seam is registered
        #   through a deref-and-call wrapper (register_atexit_close) so the
        #   projection stays COLLECTABLE — a strong bound method would pin
        #   it alive until exit and a GC finalizer could never fire. At
        #   exit, _atexit_close still runs whenever the object is alive;
        #   collected objects were already closed by their finalizer.
        # - #1475 close-on-GC: deterministic close for LEAKED projections
        #   (never explicitly closed) — the finalizer works around the
        #   dead-referent constraint by closing via a weakref to the pinned
        #   internal client (redislite's own atexit keeps it alive), not via
        #   the dead referent itself.
        # - NO per-instance signal handlers (atexit suffices; avoids leaks)
        self._closed = False
        # #1371: route the atexit seam through the fast-close wrapper — see
        # FalkorDB._atexit_close. close()/__exit__ are unchanged.
        register_atexit_close(self)
        # #1475: close-on-GC for leaked projections. Embedded clients only —
        # host-mode (docker/URI) clients deref to None and the finalizer
        # no-ops. The finalizer reuses the #1371 seam (ephemeral fast-close
        # gating + last-client guard), so explicit close()/__exit__ paths
        # and the atexit seam are unchanged.
        register_gc_close(self, self.db)

    # ── Ops safety (#428): health check + transparent recovery ────────────

    def _probe_ok(self) -> bool:
        """Cheap connectivity probe — does the graph answer queries?

        The failure REASON is retained on ``self._probe_error``. A
        full-but-healthy server refuses writes with an ``OOM``/``maxmemory``
        reply, and that reply must not be reported as corruption (#2981) —
        which requires keeping it rather than collapsing it to a bool.
        """
        self._probe_error = None
        try:
            self.g.query("MATCH (n) RETURN count(n) LIMIT 1")
            return True
        except Exception as exc:
            self._probe_error = exc
            return False

    def _memory_pressure(self) -> tuple[int, int] | None:
        """``(used_memory, maxmemory)`` in bytes, or ``None`` if unreadable.

        Best-effort and fully guarded: the falkordb client exposes ``.info()``
        only on some versions and the raw redis connection only on others, so
        both paths are tried. An unreadable value must never change the error
        CLASS — only how rich its message is.
        """
        info = None
        try:
            conn = getattr(self.db, "connection", None)
            if conn is not None:
                info = conn.info("memory")
            elif hasattr(self.db, "info"):
                info = self.db.info()
        except Exception:
            return None
        if not isinstance(info, dict):
            return None
        try:
            used = int(info.get("used_memory", 0) or 0)
            cap = int(info.get("maxmemory", 0) or 0)
        except (TypeError, ValueError):
            return None
        return (used, cap) if cap > 0 else None

    def _write_refusal_message(self, exc: BaseException | None) -> str | None:
        """The DISTINCT error for a ``maxmemory`` write-refusal, else ``None``.

        Returns ``None`` when the probe failed for any other reason — real
        corruption included — so the rebuild advice still applies there. That
        second direction is what keeps this branch honest: it NARROWS the
        remedy, it does not remove it.
        """
        if exc is None:
            return None
        text = str(exc).lower()
        if not any(marker in text for marker in _WRITE_REFUSAL_MARKERS):
            return None
        pressure = self._memory_pressure()
        if pressure is None:
            detail = ("server reports a maxmemory write-refusal "
                      "(used_memory/maxmemory unreadable)")
        else:
            used, cap = pressure
            detail = (f"used_memory {_fmt_bytes(used)} of maxmemory "
                      f"{_fmt_bytes(cap)}")
        return (
            "DB refused writes on open: the graph is INTACT but the server "
            f"has reached its memory ceiling ({detail}). This is NOT "
            "corruption — do NOT rebuild. Free memory first: delete "
            "ephemeral test graphs (GRAPH.LIST, then GRAPH.DELETE test_*), "
            "or raise / relieve the container's --maxmemory. See #2981 for "
            "the shared-lane form of this."
        )

    def _backend_failure_message(
        self, exc: BaseException | None, *, embedded: bool = False
    ) -> str | None:
        """Cause-specific error for a recognised NON-corruption failure.

        The maxmemory refusal has its own classifier (``_write_refusal_message``)
        and keeps its message verbatim; this covers the other causes that are
        still NOT corruption — a server that has not finished LOADING and a
        server refusing a module fork. Returns ``None`` when no cause matches,
        so a genuine corruption failure still reaches the rebuild advice.

        ``embedded`` selects the lane-specific FORK remedy and tracks ONE
        axis: whether this handle is an embedded unix-socket server, i.e.
        whether ``socket_path_of(db)`` is non-None and the slot cure is
        available. It is deliberately NOT ``is_prod``-gated — production
        disables auto-recovery, not the manual cure. Its default is ``False``
        — the conservative lane: a caller that has not stated its lane is
        never told to call an embedded-only function. ``_auto_health_recover``
        passes its own ``self._is_embedded`` reading.
        """
        if exc is None:
            return None
        # The fork family is classified by fork_slot's canonical predicate
        # (P2-1): it walks the __cause__/__context__ chain and owns the marker
        # vocabulary, so this call site and hosted_backup's can never drift.
        if is_fork_refusal(exc):
            return _FORK_REMEDY_EMBEDDED if embedded else _FORK_REMEDY_SERVER
        text = str(exc).lower()
        for marker, cause in _BACKEND_FAILURE_MARKERS:
            if marker in text:
                return _BACKEND_FAILURE_REMEDIES[cause]
        return None

    def _find_local_jsonl_dir(self) -> str | None:
        """Adjacent JSONL event-log dir (same directory as the embedded DB).

        Recovery only ever rebuilds from a log that lives next to the DB
        file — a global ~/.tortoise scan could rebuild a dev DB from an
        unrelated production log. Returns None when no *.jsonl is adjacent.
        """
        if not self._path:
            return None
        try:
            db_dir = os.path.dirname(
                os.path.abspath(os.path.expanduser(self._path)))
            if any(f.endswith(".jsonl") for f in os.listdir(db_dir)):
                return db_dir
        except OSError:
            return None
        return None

    def _auto_health_recover(self) -> None:
        """Health check on open + transparent JSONL recovery (embedded only).

        The projection is a derived view folded from the domain event log (the
        reconstruction source — not the durability authority; see
        docs/durability-posture.md). Two corruption modes are caught:
          1. Unresponsive graph (open succeeded, queries fail).
          2. Lost graph — 0 nodes while the adjacent JSONL log has events
             (redislite starts fresh when its RDB is corrupt, interrupted
             restore, manual deletion). Rebuilt via recover_from_log.

        Recovery is embedded-only and NEVER runs when FLY_APP_NAME is set
        (production): there a silent rebuild could mask an infra failure and
        a remote DB is never rebuilt from a local log. Server/URI mode and
        production fail loud with an actionable error instead.
        """
        import logging
        logger = logging.getLogger(__name__)
        is_prod = bool(os.environ.get("FLY_APP_NAME"))

        if not self._probe_ok():
            # Full-but-healthy is NOT corrupt: a maxmemory write-refusal gets
            # its own error and must never be sent down the rebuild path.
            refusal = self._write_refusal_message(self._probe_error)
            if refusal is not None:
                raise RuntimeError(refusal)
            # #3634 — the other NON-corruption causes keep their own remedy
            # instead of falling through to the rebuild advice.
            cause_message = self._backend_failure_message(
                self._probe_error,
                embedded=self._is_embedded,
            )
            if cause_message is not None:
                raise RuntimeError(cause_message)
            if is_prod or not self._is_embedded:
                raise RuntimeError(
                    "DB health check failed on open (server/production mode). "
                    "Recover manually: python -m tortoise rebuild --dir "
                    "<jsonl-dir> --db <db> — see "
                    "operations/skills/tortoise-rebuild/SKILL.md")
            events_dir = self._find_local_jsonl_dir()
            if not events_dir:
                raise RuntimeError(
                    f"DB health check failed on open and no adjacent JSONL "
                    f"event log was found for recovery ({self._path!r}). "
                    f"Restore from backup or rebuild manually — see "
                    f"operations/skills/tortoise-rebuild/SKILL.md")
            self._recover_or_raise(events_dir)
            logger.warning("auto-recovered embedded DB from %s", events_dir)
            return

        # Probe passed. Embedded dev mode: check the lost-graph divergence
        # (0 nodes + non-empty adjacent log). Server/prod skips — a remote
        # graph is never rebuilt from a local log.
        if self._is_embedded and not is_prod:
            events_dir = self._find_local_jsonl_dir()
            if events_dir:
                from tortoise.consistency import recover_from_log
                result = recover_from_log(events_dir, self)
                if result.get("recovered"):
                    # #4641: a completed recovery can still leave the graph's
                    # onboarding state NOT confirmed intact — a restore gap
                    # (raw writes no journal event carries), an unverified
                    # restore, or a state-UNKNOWN rescue file. Reporting only
                    # the clean "auto-recovered" line there IS the silent
                    # partial loss, so name it on the same line the operator
                    # reads. Which of the three it is lives in `reason` and in
                    # the rebuild ERROR log; the aggregate gap is the trigger.
                    if result.get("onboarding_gap"):
                        logger.warning(
                            "auto-recovered empty embedded DB from %s "
                            "(%s events) BUT its onboarding state is NOT "
                            "confirmed intact — re-run onboarding for the "
                            "affected org(s) and see the rebuild ERROR log "
                            "(#4641). Recovery reason: %s",
                            events_dir, result.get("log_points"),
                            result.get("reason"))
                    else:
                        logger.warning(
                            "auto-recovered empty embedded DB from %s "
                            "(%s events)",
                            events_dir, result.get("log_points"))
                elif result.get("reason"):
                    # Lost-graph case but recovery declined (ambiguous/unreadable
                    # log) — warn loudly instead of silently continuing with an
                    # empty DB (ops safety #428: no silent data loss).
                    logger.warning(
                        "empty embedded DB not auto-recovered: %s",
                        result.get("reason"))

    def _recover_or_raise(self, events_dir: str) -> None:
        """Run recover_from_log, fail loud if it did not recover.

        A completed recovery with an onboarding gap is NOT a reason to refuse
        to open the store (it is usable, and refusing would be strictly worse)
        — but it must not pass unmentioned either, since this is the caller for
        the unresponsive-graph path where no other surface reports it (#4641).
        """
        from tortoise.consistency import recover_from_log
        result = recover_from_log(events_dir, self)
        if not result.get("recovered"):
            raise RuntimeError(
                f"DB health check failed and recovery did not complete: "
                f"{result.get('reason')}. "
                f"See operations/skills/tortoise-rebuild/SKILL.md")
        if result.get("onboarding_gap"):
            logger.warning(
                "recovery completed but the rebuilt graph's onboarding state "
                "is NOT confirmed intact — re-run onboarding for the "
                "affected org(s) and see the rebuild ERROR log (#4641). "
                "Recovery reason: %s",
                result.get("reason"))

    @classmethod
    def from_uri(cls, uri: str, graph_name: str | None = None) -> "FalkorProjection":  # noqa: UP037
        """Parse a connection URI into a projection.

        Supported schemes (all treated as docker://):
          docker://:password@host:port/graph_name   — canonical local form
          redis:// or rediss://                     — FalkorDB Cloud / managed
                                                     instances use the redis
                                                     scheme; accept as aliases.

        graph_name overrides the URI path (multi-tenant isolation, #7886) —
        each tenant SDK selects its own graph instead of the URI default.

        Unsupported schemes raise ValueError with an actionable message.
        """
        endpoint = resolve_db_endpoint(uri, graph_name)
        graph_name = endpoint.graph_name
        # Epic #1647 (cycle-4 P2-2 / cycle-6 P2-13 / cycle-7 P2-9 / #1686): in
        # a TEST SESSION with a calling test frame (TORTOISE_TEST_MODE=1 AND
        # _resolve_caller_stem() is not None — the SAME predicate as the
        # redirect, now worker-thread-aware: worker-thread from_uri mints in
        # carve-out files resolve their inherited stem), append the resolved
        # graph name (URI-path default OR explicit) to the session journal —
        # the single seam point, so every from_uri-minted graph is owned by
        # its session's journal (the per-test wipe delta + the session-end/
        # stale sweep drop sets both derive from the FILE journal). TEST_MODE
        # alone is NOT the gate: frame-less subprocess CLI children inherit
        # TEST_MODE via os.environ.copy() and would become CONCURRENT
        # WRITERS to the parent's journal (torn-line hazard) while journaling
        # non-test CLI-lane graphs (cycle-7 P2-9 — pinned by
        # test_from_uri_append_gated_on_test_frame). The append is a NO-OP
        # when the journal env var is absent (P1 window / non-test process).
        if os.environ.get("TORTOISE_TEST_MODE") == "1" \
                and _resolve_caller_stem() is not None:
            _journal_append_product(graph_name)
        return cls(host=endpoint.host,
                   port=endpoint.port,
                   username=endpoint.username,
                   password=endpoint.password,
                   graph_name=graph_name,
                   ssl=endpoint.ssl)

    def _norm(self, ev: dict) -> dict:
        """Normalize event shape — tolerates API (flat) and script (nested point)."""
        if not isinstance(ev, dict):
            # #331 (review r4): non-dict event → empty event → skipped by
            # the type guard below (no AttributeError in ev.get()).
            return {}
        if isinstance(ev.get("point"), dict):
            # Script format: id lives inside point
            return {**ev, **ev["point"]}
        return ev

    def apply(self, event: dict) -> None:
        # #3947 review: read the capture's structural directive from the RAW
        # envelope, BEFORE `_norm` splices the point payload over it. `_norm`
        # is `{**ev, **ev["point"]}`, so a point key of the same name would
        # SHADOW the envelope — and a caller-supplied `contains_session` prop
        # would then forge a `:Session`/`CONTAINS` link on every replay. Read
        # it here so the point payload can never reach it (the SDK/MCP
        # boundaries reject the key too, as the fail-closed backstop).
        contains_session = (
            event.get("contains_session") if isinstance(event, dict) else None)
        ev = self._norm(event)
        t = ev.get("type")
        if not isinstance(t, str):
            # #331: malformed event (no/non-string 'type') — skip, don't crash.
            # Visible at the I/O boundary: silent skips corrupt recovery
            # accounting (recover_from_log counts "did apply raise", code-review r2).
            logger.warning(
                "FalkorProjection.apply: skipping malformed event with "
                "missing/non-string 'type' (event_id=%s)", ev.get("event_id"))
            return
        if t in ("PointAdded", "OperatorAdded"):
            # #331 (review r3): ev.get — missing 'point' key handled by the
            # isinstance guard, not KeyError.
            p = ev.get("point")
            if not isinstance(p, dict):
                # Malformed event (non-dict/missing point) — skip, don't crash
                # (issue #325)
                logger.warning(
                    "FalkorProjection.apply: skipping %s with non-dict point "
                    "(event_id=%s)", t, ev.get("event_id"))
                return
            # #331 (review r2): parity with _apply_one — no id → nothing to
            # index by; skip rather than KeyError in _upsert.
            # #331 (review r4): str-only ids (non-str would break Cypher params).
            if not isinstance(p.get("id"), str):
                logger.warning(
                    "FalkorProjection.apply: skipping %s with missing point "
                    "id (event_id=%s)", t, ev.get("event_id"))
                return
            # Phase 1 stop-writes: strip context from v2+ events (#49)
            if ev.get("projection_version", 0) >= 2:
                p.pop("context", None)
            # #3947: an episodic turn Point's journal record carries its
            # capture-session link on the ENVELOPE — replay restores the
            # `(:Session)-[:CONTAINS]->(:Point)` edge the live turn loop wrote
            # raw (and which a rebuild previously destroyed with no way back).
            # `snapshot_ids` is folded into the proof as a second recreation
            # source (the #548 snapshot handles graph-only Points), and its
            # derivation from `synthetic_events` is what keeps the proof from
            # claiming coverage the replay does not stage.
            self._upsert(p, contains_session=contains_session)
        elif t == "PointRevised":
            # Phase 1: discard new_context for v2+ events (#49)
            if ev.get("projection_version", 0) >= 2:
                ev.pop("new_context", None)
            self._revise_point(ev, set_updated_at=True)
        elif t == "OperatorAnnotated":
            # #3689: fold the explicit annotation record. Inline (a
            # non-terminalizing property SET — parity with the PointRevised
            # branch directly above); the dims have no edge/graph-shape
            # effect, so no deferred sweep is needed. No return: keep
            # ``apply()``'s declared ``-> None`` contract (the fold-miss
            # signal is consumed by rebuild_all pass-1b).
            self._apply_annotator(ev)
        elif t == "PointRetracted":
            rid = ev.get("id")
            if isinstance(rid, str):
                # #331: missing id must be skipped, not crash the handler.
                # #331 (review r2): NO event_id fallback — an event id is not
                # a point id, and the fallback diverged from _apply_one (the
                # fold is the single source of truth, module contract).
                self._retract(rid)
        elif t == "PointPromoted":
            # #785: re-apply the full promoted snapshot (status live +
            # reviewed + promotedAt) — rebuild parity for reviewer-gated
            # promotions (PointRetracted-style lifecycle event).
            p = ev.get("point")
            if isinstance(p, dict) and p.get("id"):
                self._upsert(p)
        elif t == "OperatorPromoted":
            # #785/R16: restore the operator's live status on replay.
            # (#2256 review P1): promotion emitters journal FLAT get_point
            # snapshots — for the CAPTURE path this event is the operator's
            # ONLY durable record (the m2 lane writes the live graph without
            # OperatorAdded journaling), so the replay must UPSERT, not
            # status-SET (a MATCH-SET on a never-created node is a no-op and
            # the operator is lost on rebuild).  The flat snapshot is
            # synthesized into the canonical nested-operator shape first —
            # otherwise _upsert_point_props derives operator-ness from the
            # absent nested key and writes is_operator=false (silent
            # operator→claim conversion on rebuild).  Heals the pre-existing
            # R16 emitter shape (promote_point, since #785) too.
            p = ev.get("point")
            if isinstance(p, dict) and p.get("id"):
                self._upsert(_promotion_point_with_operator(p))
            else:
                oid = ev.get("id") or ev.get("event_id")
                if oid is not None:
                    self.g.query(
                        "MATCH (n:Point {id:$id}) SET n.status = 'live'",
                        params={"id": oid},
                    )
        elif t == "PointsMerged":
            # #331 (review r2): `or []` also covers "merge_ids": null.
            for mid in ev.get("merge_ids") or []:
                # #331 (review r4): str-only ids.
                if isinstance(mid, str):
                    self._delete(mid)
        elif t == "EntityMutated":
            # #3299: replay the write-surface mutation record. Chronological
            # dispatch (rebuild()/backup/consistency) — journal order is the
            # correctness contract (a later creation event must win over an
            # earlier delete), so fold INLINE, never deferred.
            return self._fold_entity_mutation(ev)
        elif t == "EventRecorded":
            return self._upsert_event(ev)
        elif t == "EntityLinked":
            # #3664: the capture entity-attachment replay consumer — an
            # idempotent about* edge MERGE keyed on the flat logical ids
            # (Session/Point -> Object). JSONL-only record (no GraphEvent).
            # Folded INLINE here because ``apply`` sees one event and the live
            # caller's endpoints already exist; the whole-journal apply()-based
            # engines (``rebuild``/``recover_from_log``) buffer the type and
            # call ``fold_deferred_entity_links`` after the pass, matching
            # ``rebuild_all``'s trailing sweep on a forward-reference journal.
            return self._fold_entity_linked(ev)
        elif t == "SessionRecorded":
            # #3664: the :Session node's journal carrier — the capture MERGE
            # is a raw write, so without this a rebuild lost the node (and
            # made any EntityLinked edge from it unreplayable).
            return self._fold_session_recorded(ev)
        elif t == "SubjectAdded":
            self._upsert_subject(ev)
        elif t == "ObjectSuperseded":
            # #1350: fold the client-derived supersession into Object.status
            # (projection-owned cache of the event stream — §11 'derived
            # values may be CACHED'). Rebuild-replay-safe via this branch.
            return self._fold_object_superseded(ev)
        elif t == "ObjectRegistered":
            self._upsert_object(ev)
        elif t == "DocumentCreated":
            self._upsert_document(ev)
        elif t == "SourceCreated":
            # epic #900 T3: return the MERGE QueryResult so the SDK's
            # create_source write path can attribute the counter-authority
            # outcome (nodes_created) from the single statement (pin b). The
            # internal ``_merge_run_id`` key (the creator's run token — the
            # race-safe CREATE discriminator on the embedded backend) is
            # popped here so it never reaches _persist_extra_props.
            return self._upsert_source(
                ev, merge_run_id=ev.pop("_merge_run_id", None))
        elif t == "ConfidenceChanged":
            # #2884 D3: the EP/dream belief-state write-back. Inline (a
            # non-terminalizing property SET — parity with the PointRevised
            # branch above); no edge/graph-shape effect, so no deferred
            # sweep. No return: keep ``apply()``'s declared ``-> None``
            # contract.
            self._fold_confidence_changed(ev)
        elif t in _NO_PROJECTION_FOLD:
            # Recognized, intentionally folded elsewhere or not at all: the
            # audit-only markers, the JSONL-only batch snapshot replayed in
            # rebuild_all pass-2b, and the deliberately-deferred
            # DirectEdgeCreated (A10 #1048). The warning below is reserved
            # for a type OUTSIDE this vocabulary — #3299 arose from exactly
            # that (an unknown mutation vanishing under wipe+replay).
            pass
        else:
            # P2-1 (#3299): a record type outside the recognized vocabulary
            # must not be dropped silently. A type that IS recognized but has
            # no branch HERE — e.g. PointSuperseded / PointInvalidated /
            # DirectEdgeRepoint, folded only by rebuild_all's deferred pass —
            # still warns: that is a genuine rebuild-parity gap, not noise.
            logger.warning("unrecognized event type %r — skipped", t)

    def _episodic_point_ids(self) -> set[str]:
        """Ids of every ``:Point`` currently carrying ``is_episodic = true``.

        #3947 review: this read is PRE-wipe and the proof depends on it, so it
        fails **closed** — an unreadable roster means the proof cannot be
        computed, and refusing is the only honest answer. Letting the driver
        error propagate instead would surface a traceback on a supported ops
        path; degrading to the empty set (the pre-review behaviour) would
        silently disarm the guard, because an empty roster short-circuits the
        proof and the wipe then runs unverified.
        """
        try:
            rows = self.g.query(
                "MATCH (n:Point) WHERE n.is_episodic = true RETURN n.id"
            ).result_set
        except Exception as exc:  # noqa: BLE001, RUF100
            raise RebuildDroppedEpisodicPoints(
                "refusing to rebuild: the pre-wipe episodic Point roster could "
                f"not be read ({type(exc).__name__}: {exc}), so the proof that "
                "captured turns would survive cannot be computed. The graph "
                "was NOT touched. Check the database connection/health, then "
                "retry — or restore from an RDB backup."
            ) from exc
        return {r[0] for r in rows if isinstance(r[0], str)}

    @staticmethod
    def _journal_recreated_ids(events) -> set[str]:
        """Ids the journal will CREATE when it is replayed.

        Derived from ``_JOURNAL_CREATING_EVENT_TYPES`` — the event types whose
        replay reaches a node MERGE (``PointAdded``/``OperatorAdded`` via
        ``_upsert`` in ``apply``, plus the ``PointPromoted``/``OperatorPromoted``
        full-snapshot branches, which also UPSERT — #785/#2256; an
        ``OperatorPromoted`` is the capture path's only durable record for some
        operators). This is the pre-wipe counterpart of `_episodic_point_ids`:
        it answers "will the replay bring this id back?" WITHOUT running it,
        which is what makes the invariant safe to raise on.
        """
        ids: set[str] = set()
        for ev in events:
            if not isinstance(ev, dict):
                continue
            if ev.get("type") in _JOURNAL_CREATING_EVENT_TYPES:
                p = ev.get("point")
                if isinstance(p, dict) and isinstance(p.get("id"), str):
                    ids.add(p["id"])
        return ids

    @staticmethod
    def _journal_hard_deleted_ids(events) -> set[str]:
        """Ids the journal HARD-deletes (``EntityMutated`` op=delete, #3299;
        ``PointsMerged``, whose merged-away ids replay through ``_delete``).

        Replay is *supposed* to drop these — the write surface that deleted
        them journaled the destruction — so the #3947 invariant exempts them.
        Retraction is deliberately NOT in this set: ``_retract`` tombstones
        and the node survives, so it can never look like a lost Point.

        Identity is ``(kind, id)``, not bare id (#3860): only a deleted record
        that is (or may be) a Point exempts its id, so a foreign-kind delete
        can never hide an unrecreatable episodic Point and fail this guard OPEN.

        Each event is read in its NORMALIZED form (``_norm``), mirroring
        ``journal_hard_delete_seqs``: in a NESTED record the ``EntityMutated``
        payload (``id``/``op``/``label``) rides inside ``point`` while ``type``
        stays on the envelope, so reading the raw envelope sees no ``op`` and
        the delete is missed entirely — its id would then never be exempted
        (#3722).
        """
        deleted: set[str] = set()
        for ev in events:
            if not isinstance(ev, dict):
                continue
            # #3722: read the NORMALIZED record — the shape the replay folds
            # actually delete through (``_norm`` splices ``ev["point"]`` over
            # the envelope). Reading the raw envelope missed a NESTED
            # ``EntityMutated`` op=delete entirely, so its id was never
            # exempted and the pre-wipe proof REFUSED a rebuild the replay
            # would have completed (false block on a healthy store).
            ev = _norm(ev)
            t = ev.get("type")
            if t == "EntityMutated" and ev.get("op") == "delete":
                # #3860: identity is (kind, id) — only a POINT-kind delete (or
                # an unknown/missing label, treated as possibly-Point) exempts
                # a Point id from this roster. A foreign-kind record must not
                # hide an unrecreatable episodic Point (the guard would then
                # fail OPEN — permit the silent destruction it exists to
                # refuse).
                rid = ev.get("id")
                if isinstance(rid, str) and _owns_point(ev.get("label")):
                    deleted.add(rid)
            elif t == "PointsMerged":
                # #331: `or []` also covers an explicit "merge_ids": null.
                for mid in ev.get("merge_ids") or []:
                    if isinstance(mid, str):
                        deleted.add(mid)
        return deleted

    def _assert_episodic_points_recreatable(self, before: set[str], events,
                                            snapshot_ids=()) -> None:
        """#3947 rebuild invariant — refuse a rebuild that CANNOT restore the turns.

        Episodic turn Points are written by the capture loop with a raw Cypher
        MERGE, so before #3947 they never entered the journal the rebuild
        replays: a wipe+replay deleted them with no event from which to
        recreate them, while returning normally. Silent destruction of
        captured work dressed as a successful recovery (category A: silent
        destruction + false PASS).

        ⛔ **PRE-WIPE, deliberately.** This verification runs BEFORE
        ``MATCH (n) DETACH DELETE n`` and raises with the graph untouched.
        #2943 ("No loss without proof") is the recorded decision governing
        that shape: a hard-failing rebuild verification is safe only because
        it can no longer fail *after* the wipe — failing post-wipe converts a
        durability bug into permanent data loss, which is strictly worse than
        the silent-degradation bug it detects. Verifying before the mutation
        is that issue's own named remedy, so a RED here costs a refused
        rebuild, never the store.

        The required-to-be-recreatable roster is deliberately NARROW — it is
        every Point that carried ``is_episodic = true`` BEFORE the wipe, PLUS
        every Point contained by an EPISODIC ``:Session`` recorded in the
        recovered snapshot. Each must be RECREATABLE (journal record or
        caller-supplied snapshot id), minus those the journal itself
        hard-deletes. The containment leg is deliberate and is NOT narrowed to
        the Points that themselves carry ``is_episodic``: the extractor
        CONTAINS-wires non-episodic Points into a session, and the session
        membership is what survives a writer that silently drops the
        server-managed ``is_episodic`` flag. It does not police generic
        graph-only Points: a journal-only ``rebuild()`` is *defined* to
        reproduce the journal, and widening the check to every Point would
        make every unjournaled producer a false block.

        Raises:
            RebuildDroppedEpisodicPoints: naming the missing ids, so the
                operator sees WHAT would be lost instead of a clean exit.
        """
        if not before:
            return
        # NOTE: the hard-deletes exempt ids from the REQUIREMENT (they are
        # supposed to disappear) — subtracting them from `covered` instead
        # would demand recreation and turn every journaled delete into a
        # false block.
        covered = self._journal_recreated_ids(events) | set(snapshot_ids)
        missing = sorted(
            before - covered - self._journal_hard_deleted_ids(events))
        if not missing:
            return
        shown = ", ".join(missing[:10]) + (" …" if len(missing) > 10 else "")
        raise RebuildDroppedEpisodicPoints(
            f"rebuild would destroy {len(missing)} episodic Point(s) it cannot "
            f"recreate: {shown} — the journal holds no creation record for "
            "them (their write bypassed the event log). The graph was NOT "
            "touched: repair the journal or the snapshot, or restore from an "
            "RDB backup, instead of trusting this rebuild."
        )

    def rebuild(self, log) -> None:
        # #3947: read the journal FIRST (a torn/failed read must not wipe),
        # then PROVE the replay can recreate every episodic Point BEFORE the
        # wipe. #2943: verifying only after the wipe turns a durability bug
        # into permanent data loss, so the proof has to precede the mutation.
        events = list(log.read_all())
        episodic_before = self._episodic_point_ids()
        self._assert_episodic_points_recreatable(episodic_before, events)
        self.g.query("MATCH (n) DETACH DELETE n")
        # #3664: this engine feeds ``apply()`` ONE record at a time, so an
        # ``EntityLinked`` whose endpoint is created LATER in the journal would
        # fold to nothing. Defer the type to a trailing sweep — the same
        # forward-reference treatment ``rebuild_all`` gives it — so the replay
        # engines agree. The fold is an idempotent MERGE, order-free by
        # construction. Records are buffered WITH their journal seq so the
        # sweep can apply the hard-delete staleness rule (#3722 review P2): a
        # link whose endpoint was hard-deleted AFTER it must not resurrect.
        hard_delete_seqs = journal_hard_delete_seqs(events)
        entity_link_events: list[tuple[int, dict]] = []
        for seq, ev in enumerate(events):
            if isinstance(ev, dict) and ev.get("type") == "EntityLinked":
                entity_link_events.append((seq, ev))
                continue
            self.apply(ev)
        self.fold_deferred_entity_links(entity_link_events, hard_delete_seqs)

    def rebuild_all(self, log_dir: str) -> dict:
        """Rebuild from all .jsonl files in a directory. Returns counts.

        Two-pass: creates all Point nodes first (pass 1), then operator edges
        and all other event types second (pass 2), so cross-file operator→Point
        references always resolve regardless of filename sort order.

        GAP-19: Full event-type coverage — replays all EventRecorded, SubjectAdded,
        ObjectRegistered, DocumentCreated, SourceCreated, ConfidenceChanged,
        PointRevised events, not just PointAdded/OperatorAdded/PointRetracted/
        PointsMerged.

        #330 parity guarantee: for logs where SourceCreated precedes the
        PointAdded events that extract from that source (the canonical ingest
        order), rebuild produces the SAME Point node properties + edges as
        replaying through apply(). A reversed order (extractedFrom-bearing
        point before its SourceCreated) can diverge Source node version/id
        properties — known, documented limitation.

        #548 SDK parity: before wiping, snapshot the current graph to preserve
        SDK-created points that have no corresponding event in any .jsonl file.
        These are injected as synthetic PointAdded/OperatorAdded events before
        the JSONL replay so the two-pass logic handles them identically.

        #2943 durability: that snapshot (Points AND the #990 :Batch markers /
        Point.batch_id links) is ALSO persisted to a sidecar next to the event
        log IMMEDIATELY BEFORE the wipe (after the JSONL parse, so a parse
        abort writes nothing) and removed only once replay completes, so a
        crash mid-replay cannot orphan graph-only nodes — they exist nowhere
        else. The next rebuild_all unions the leftover in; the embedded
        auto-recovery path (tortoise.consistency.recover_from_log) routes here
        while a sidecar is pending (still under its single-log discriminator).
        A failed snapshot CAPTURE, a write failure, an untrustworthy sidecar,
        or a refused wipe all abort BEFORE the wipe. See
        ``_PREWIPE_SNAPSHOT_FILENAME``.
        """
        import os  # noqa: I001
        from tortoise.log import EventLog

        # #2958 review: reset the once-per-key deny-drop warning set for this
        # rebuild pass (see `_upsert_point_props`) so the report is emitted once
        # per key per pass instead of once per graph-only point.
        self._deny_drop_warned = set()
        # #5004: the same once-per-pass de-dup for the replay's embedding-identity
        # warnings (an embedder swap would otherwise emit one line per Point).
        self._embed_identity_warned = set()

        # #3947: capture the episodic Point population BEFORE the wipe, and
        # PROVE the replay can recreate it BEFORE wiping anything (#2943: a
        # verification that can fail only after the wipe is the thing that
        # turns a durability bug into permanent data loss).
        episodic_before = self._episodic_point_ids()

        # ── #548: snapshot existing graph BEFORE wiping ──────────────
        # SDK-created points written via Cypher may have no corresponding
        # event in the JSONL log. Snapshot them now so they survive the
        # wipe+replay cycle.
        synthetic_events: list[dict] = []
        capture_failed: list[str] = []
        try:
            rows = self.g.query(
                "MATCH (n:Point) RETURN properties(n)"
            ).result_set
            existing_points = {}
            non_str_ids = []
            for r in rows:
                props = r[0]
                pid = props.get("id")
                if isinstance(pid, str):
                    existing_points[pid] = props
                elif pid is not None:
                    # A non-str id cannot round-trip the sidecar (the replay
                    # indexes by str, #331 r4) — pass 1a would skip the entry,
                    # the wipe would destroy the node, and the sidecar written
                    # from it would be rejected by the loader on the NEXT run,
                    # making the directory permanently unrebuildable. Refuse
                    # before the wipe instead: the node is the only record of
                    # itself.
                    non_str_ids.append(repr(pid))
            if non_str_ids:
                capture_failed.append(
                    f"non-string Point id(s) that cannot survive a JSONL "
                    f"replay: {', '.join(sorted(non_str_ids)[:5])}")
            if existing_points:
                # Collect all JSONL events to find which IDs are already
                # represented in the log.
                log_point_ids: set[str] = set()
                for fname in sorted(os.listdir(log_dir)):
                    if fname.endswith('.jsonl'):
                        for ev in EventLog(os.path.join(log_dir, fname)).read_all():
                            if ev.get("type") in ("PointAdded", "OperatorAdded"):
                                p = ev.get("point", {})
                                if isinstance(p, dict) and p.get("id"):
                                    log_point_ids.add(p["id"])
                # Generate synthetic events for graph-only points
                for pid, props in existing_points.items():
                    if pid in log_point_ids:
                        continue  # log already covers this point
                    # Strip volatile properties the replay recomputes or that
                    # are not node properties. `content_hash` is NOT in this
                    # list: it is `_upsert_point_props`'s CONDITIONAL write, and
                    # the pass-1b tail is its carrier for the ids the journal
                    # did not derive (see _REPLAY_GAP_PROPS).
                    # `embedding` is stripped because this is a SYNTHETIC event
                    # for a GRAPH-ONLY id — the journal holds no record of it at
                    # all, so there is no journalled vector to carry (#5004 only
                    # changed the path where the journal DOES carry one). The
                    # rebuild restores it from the live pre-wipe capture in the
                    # pass-1b tail; `updatedAt` is replay-owned.
                    # `embedding_verbatim` is stripped WITH it: the tail is this
                    # path's only restorer, and carrying the marker here without
                    # the vector made `_upsert_point_props` take the verbatim
                    # branch for a recomputed value. The tail honours the
                    # marker instead (round-7).
                    clean = {k: v for k, v in props.items()
                             if k not in ("embedding", "embedding_verbatim",
                                          "updatedAt", "_nid", "_graph_id")}
                    is_op = bool(props.get("is_operator") or props.get("op_type"))
                    ev_type = "OperatorAdded" if is_op else "PointAdded"
                    if is_op:
                        # Reconstruct operator.inputs from graph edges
                        inputs: list[str] = []
                        try:
                            op_type_val = props.get("op_type", "IMPL")
                            edge_rel = "hasPart" if op_type_val not in ("IMPL", "NAND") else op_type_val
                            edge_rows = self.g.query(
                                f"MATCH (n:Point {{id:$id}})-[r:{edge_rel}]->(m:Point) "
                                f"RETURN m.id ORDER BY r.idx",
                                params={"id": pid},
                            ).result_set
                            inputs = [er[0] for er in edge_rows
                                      if isinstance(er[0], str)]
                        except Exception as e:
                            # NOT a tolerable skip: pass 2 rebuilds an
                            # operator's edges from THIS list, so a failed
                            # edge scan would persist an operator with no
                            # inputs (EP then drops the factor entirely) and
                            # wipe the real edges — silently. Fail closed
                            # with the other capture failures below, or the
                            # new gate's guarantee is false.
                            capture_failed.append(
                                f"operator inputs for {pid} "
                                f"({type(e).__name__}: {e})")
                        clean["operator"] = {"op_type": props.get("op_type", "IMPL"),
                                             "inputs": inputs}
                        # Operators may not store 'content' as a node property;
                        # _upsert_point_props requires it — synthesize fallback.
                        if "content" not in clean:
                            clean["content"] = (
                                f"{props.get('op_type', 'IMPL')}"
                                f"({', '.join(inputs)})")
                    # Ensure pointKind is present (operators often lack it)
                    if "pointKind" not in clean:
                        clean["pointKind"] = ""
                    synthetic_events.append({
                        "type": ev_type,
                        "point": clean,
                        "projection_version": 2,
                    })
        except Exception as e:
            # Graph may be corrupt — see the capture_failed gate below.
            capture_failed.append(
                f"Point/#548 snapshot ({type(e).__name__}: {e})")

        # ── :Batch marker snapshot (#990) ───────────────────────────
        # Batch lifecycle state (quarantine/commit) lives on :Batch marker
        # nodes, which are NOT :Point nodes — the #548 snapshot below only
        # covers Points. Snapshot them here so a rebuild does not silently
        # evaporate quarantine locks (a quarantined batch must stay
        # quarantined after rebuild — review #944/#990).
        batch_snapshot: list[dict] = []
        batch_point_links: list[tuple[str, str]] = []
        try:
            rows = self.g.query(
                "MATCH (b:Batch) RETURN properties(b)"
            ).result_set
            batch_snapshot = [r[0] for r in rows] if rows else []
            # The ENFORCEMENT link (Point.batch_id) is a raw graph write on
            # the mining path — it never rides the JSONL event stream, so the
            # #548 Point snapshot (which skips log-covered points) cannot
            # restore it. Snapshot the links too: without them, a rebuild
            # leaves the :Batch marker quarantined while promote_point no
            # longer sees the batch_id — the lock silently bypasses (#1025
            # review P1).
            link_rows = self.g.query(
                "MATCH (p:Point) WHERE p.batch_id IS NOT NULL "
                "RETURN p.id, p.batch_id"
            ).result_set
            batch_point_links = [(r[0], r[1]) for r in link_rows] if link_rows else []
        except Exception as e:
            # graph may be corrupt — see the capture_failed gate below.
            capture_failed.append(
                f":Batch/#990 snapshot ({type(e).__name__}: {e})")

        # ── Authoritative config snapshot (#2814) ───────────────────
        # The wipe below is unconditional and only the JOURNAL is replayed,
        # but `:PackInstall` / `:PackManifest` and the keyed `:Meta` markers
        # (`calibration_milestone`, `config_reset`) are graph-resident, ride no
        # journal record and are not re-derivable — so without this capture a
        # rebuild silently reverted a configured graph to defaults, masked by
        # the self-healing `get_tenant_packs` → `ensure_tenant_packs` read
        # path (the graph looked freshly provisioned, not empty). The class
        # list is DECLARED, not discovered: see `_config_classes()`.
        #
        # Best-effort like the two snapshots above, funneled into the SAME
        # `capture_failed` gate: a failed read must not fall through to the
        # wipe (a corrupt or merely heavy `properties(n)` read can fail while
        # the light DELETE succeeds).
        config_snapshot: list[dict] = []
        try:
            config_snapshot = _capture_config_snapshot(self.g)
        except Exception as e:
            capture_failed.append(
                f"config snapshot/#2814 ({type(e).__name__}: {e})")

        # ── Onboarding state snapshot (#4641) ───────────────────────
        # `tortoise/onboarding/state.py` writes `:OnboardingState`,
        # `:OnboardingStep` and the `COMPLETED_STEP` edges with RAW Cypher —
        # no journal record, and (before this change) `rg OnboardingState
        # tortoise/projection` was zero hits, so a replay cannot re-create any
        # of it. The writers'
        # contracts make the loss visible rather than benign: `fork` is
        # set-once and never re-asked, `status` is server-owned and
        # gate-written. Captured as a SECTION pair (node maps + step links)
        # rather than a `_config_classes()` row because the registry carries
        # nodes with a single identity property while `:OnboardingStep` is
        # composite `(org_id, step_id)` and the edge is not a node at all — a
        # node-only row would restore the node and silently drop the edges.
        #
        # Best-effort read, funneled into the SAME `capture_failed` gate: a
        # graph that cannot answer this read NEVER reaches the wipe (I3).
        onboarding_snapshot: list[dict] = []
        onboarding_step_links: list[tuple[str, str]] = []
        try:
            # Imported HERE, PRE-wipe, and REUSED by the restore leg below.
            # The restore leg runs after `MATCH (n) DETACH DELETE n`, so a
            # renamed or removed symbol would raise with the store already
            # emptied (#2943). Binding all four before the capture funnels
            # that failure into `capture_failed`, which aborts with the graph
            # untouched — the pre-wipe guard and the post-wipe consumer now
            # read the SAME symbols (#4641 review round 6).
            from tortoise.onboarding.state import (
                COMPLETED_STEP_EDGE,
                ONBOARDING_NODE_LABEL,
                ONBOARDING_STEP_LABEL,
                ONBOARDS_EDGE,
            )
            onboarding_snapshot, onboarding_step_links = (
                _capture_onboarding_snapshot(self.g))
        except Exception as e:
            capture_failed.append(
                f"onboarding snapshot/#4641 ({type(e).__name__}: {e})")

        # ── #2943: a FAILED capture must not fall through to the wipe ───
        # Both capture blocks above are best-effort by design (the graph may
        # be corrupt), but proceeding after a failed capture would wipe the
        # graph with NO durable record of its graph-only nodes — the exact
        # #2943 loss, silently. Fail closed: a graph that cannot answer a
        # property scan is not evidence the wipe is safe (a heavy
        # `properties(n)` read can fail — OOM/timeout — while the light
        # DELETE succeeds).
        if capture_failed:
            raise RuntimeError(
                "rebuild aborted BEFORE the graph wipe: the pre-wipe snapshot "
                "could not be captured (" + "; ".join(capture_failed) +
                "). Wiping now would destroy any graph-only Point or :Batch "
                "marker that has no JSONL event, with no durable record "
                "(#2943). The graph is untouched — repair the "
                "graph/connection and re-run."
            )

        # ── :Session container snapshot (#3947 review) ──────────────
        # Same class as the :Batch marker above: `:Session` nodes and their
        # `CONTAINS` edges are RAW graph writes on the capture path (the turn
        # loop in sdk.py/hosted_api.py) that ride no journal record on a
        # pre-#3947 store, and the #548 snapshot covers Points ONLY. Without
        # this, `rebuild_all` restored every turn Point into an orphan —
        # success reported, container and links destroyed (the false PASS
        # #3947 removes). Captured alongside the two snapshots above — the
        # episodic roster is read above, and all three reads are pre-wipe with
        # no mutation between them, so they describe the same graph.
        session_snapshot: list[dict] = []
        session_point_links: list[tuple[str, str]] = []
        # #3947 review (cycle 2): this read is NOT best-effort like the two
        # snapshots above. The claim this block makes — that the F2 false PASS
        # is removed — is only true if a FAILED Session read cannot be
        # mistaken for "nothing to restore": with `except: pass` the turn
        # Points replay from the journal, the proof GREENs, and `rebuild_all`
        # returns counts while every `:Session` and CONTAINS edge is gone
        # (exactly the false PASS). Refuse instead — pre-wipe, so nothing is
        # lost, and the exception is catchable (`_cmd_rebuild`).
        try:
            rows = self.g.query(
                "MATCH (s:Session) RETURN properties(s)"
            ).result_set
            session_snapshot = [r[0] for r in rows] if rows else []
            link_rows = self.g.query(
                "MATCH (s:Session)-[:CONTAINS]->(p:Point) "
                "RETURN s.id, p.id"
            ).result_set
            session_point_links = (
                [(r[0], r[1]) for r in link_rows] if link_rows else [])
        except Exception as exc:  # noqa: BLE001, RUF100
            raise RebuildDroppedEpisodicPoints(
                "refusing to rebuild: the pre-wipe :Session container "
                f"snapshot could not be read ({type(exc).__name__}: {exc}), so "
                "the capture session containers and their CONTAINS links "
                "cannot be proven recoverable. The graph was NOT touched "
                "(no wipe ran). Check the database connection/health and "
                "retry."
            ) from exc

        # ── Wipe + rebuild ──────────────────────────────────────────
        # WIPE-AFTER-PARSE (epic #900 T12/T3, cycle-21 ordering pin): parse
        # ALL .jsonl into memory (line-tolerant — a torn TRAILING line from a
        # SIGKILL mid-append is skipped with a warning + count via
        # EventLog.read_all, never raised — S15) BEFORE the sidecar write and
        # BEFORE the wipe. A wipe-then-parse order would turn one torn line
        # into TOTAL LOSS (wipe lands, then the parse raises).
        #
        # Parsed into its own list so the #2943 block below can union a
        # leftover sidecar into the synthetic events before assembling the
        # final `events` (synthetic first, so their nodes exist before JSONL
        # events that may reference them).
        journal_events: list[dict] = []
        # #4042: per-journal-file ordinal, parallel to ``journal_events`` —
        # the same-source-file chronology key the pass-1b content boundary
        # needs (see ``event_source`` at the assembly below).
        journal_source: list[int] = []
        for file_idx, fname in enumerate(sorted(os.listdir(log_dir))):
            if fname.endswith('.jsonl'):
                chunk = EventLog(os.path.join(log_dir, fname)).read_all()
                journal_events.extend(chunk)
                journal_source.extend([file_idx] * len(chunk))

        # ── #2943: durable pre-wipe snapshot (crash-safe wipe+replay) ───
        # A leftover sidecar means a previous rebuild died after the wipe
        # landed (or in the microseconds between the write below and it).
        # Union it in: the graph-only nodes it records exist nowhere else, so
        # dropping them is permanent loss. Leftover entries win — see
        # _union_prewipe_snapshot.
        snapshot_path = prewipe_snapshot_path(log_dir)
        if self._path and (os.path.dirname(os.path.abspath(self._path))
                           != os.path.dirname(os.path.abspath(snapshot_path))):
            # The durable sidecar is keyed to log_dir; embedded auto-recovery
            # looks only in the DB's own directory. Warn (do not fail) — the
            # explicit `tortoise rebuild --dir <log_dir>` retry still finds it.
            logger.warning(
                "rebuild: the event-log dir %s differs from the embedded DB "
                "dir %s — the durable pre-wipe snapshot is written next to "
                "the log, so automatic recovery on DB open will not see it; "
                "re-run `tortoise rebuild --dir %s` to recover (#2943)",
                log_dir, os.path.dirname(os.path.abspath(self._path)), log_dir)
        leftover = _load_prewipe_snapshot(snapshot_path)
        if leftover is not None:
            logger.warning(
                "rebuild: found a leftover pre-wipe snapshot at %s (%d "
                "graph-only point event(s), %d batch(es), %d batch link(s), "
                "%d session container(s), %d session link(s), "
                "%d onboarding state(s), %d onboarding step link(s), "
                "%d config entry(ies)) "
                "from an interrupted rebuild — merging it before this "
                "wipe+replay",
                snapshot_path,
                len(leftover.get("synthetic_events") or []),
                len(leftover.get("batch_snapshot") or []),
                len(leftover.get("batch_point_links") or []),
                len(leftover.get("session_snapshot") or []),
                len(leftover.get("session_point_links") or []),
                len(leftover.get("onboarding_snapshot") or []),
                len(leftover.get("onboarding_step_links") or []),
                len(leftover.get("config_snapshot") or []))
        merged = _union_prewipe_snapshot(leftover, {
            "synthetic_events": synthetic_events,
            "batch_snapshot": batch_snapshot,
            "batch_point_links": batch_point_links,
            "session_snapshot": session_snapshot,
            "session_point_links": session_point_links,
            "onboarding_snapshot": onboarding_snapshot,
            "onboarding_step_links": onboarding_step_links,
            "config_snapshot": config_snapshot,
        })
        synthetic_events = merged["synthetic_events"]
        batch_snapshot = merged["batch_snapshot"]
        batch_point_links = merged["batch_point_links"]
        # #3947 × #3010: reassign the session sections from the MERGED snapshot
        # BEFORE both consumers — the recovered-roster leg (b) below and the
        # :Session restore loops after replay. On the sidecar-recovery path the
        # live reads above returned nothing (the wipe already landed), so this
        # reassignment is what makes the containers/links recoverable at all —
        # and what lets the roster carry the session-linked turn ids the
        # `covered` set (journal ∪ synthetic snapshot) cannot account for.
        session_snapshot = merged["session_snapshot"]
        session_point_links = merged["session_point_links"]
        # #4641: the onboarding sections need the same reassignment for the
        # same reason — on the sidecar-recovery path the live capture is empty
        # (the wipe already landed), so restoring from the pre-union locals
        # would restore nothing. The write payload below is derived from
        # `merged` too, so a crash between the sidecar write and the replay
        # keeps the recovered onboarding state in the retry's rescue file.
        onboarding_snapshot = merged["onboarding_snapshot"]
        onboarding_step_links = merged["onboarding_step_links"]
        # #4641: the pre-preservation window, computed HERE (pre-wipe) so the
        # flag can be CARRIED into this run's own sidecar. A leftover with no
        # onboarding section key — or one carrying the flag a previous run
        # staged — was written by a build that did not record the class, so
        # whether the wipe destroyed onboarding state CANNOT be determined
        # from the file. `or`, not `and`: a file carrying only ONE of the two
        # keys recorded half the class. Mirrors #2814's `config_reset` T2
        # staging, and the flag is a METADATA key (see
        # `_PREWIPE_SNAPSHOT_META_KEYS`) so a second interruption does not let
        # this run's own empty sections erase the evidence.
        onboarding_unknown = bool(
            (leftover or {}).get("onboarding_unknown")) or (
            leftover is not None
            and ("onboarding_snapshot" not in leftover
                 or "onboarding_step_links" not in leftover))
        # #2814: same reason as the session sections — on the sidecar-recovery
        # path the live graph is already empty, so the leftover's config is the
        # only record of it. Assigned from `merged` (not from the capture
        # above) so the union's per-key leftover-wins rule is what the write
        # payload and the restore leg both see.
        config_snapshot = merged["config_snapshot"]
        # ── #2814 T2: a pre-preservation rescue file cannot record config ──
        # A sidecar written by a build that predates this change has no
        # `config_snapshot` section at all — so on the sidecar-RECOVERY path
        # (the live graph is already empty) nothing tells us whether the
        # destroyed graph was configured. Stage the marker into the SECTION
        # rather than writing it to the graph pre-wipe: the graph is about to
        # be replaced anyway, and the section is what the restore leg (and the
        # sidecar) already carry, so a crash before the replay re-derives the
        # marker on the next attempt instead of leaving a claim with no config
        # record beside it.
        #
        # The reason string is deliberately NOT `reset`: T2 cannot prove a
        # graph WAS configured (a v1 sidecar is written whenever the old build
        # captured any graph-only entry — #Batch, :Session, graph-only Points),
        # so it reports STATE UNKNOWN. T1 below is the proof case.
        #
        # The gate keys on the RESCUE FILE's version ONLY — deliberately NOT on
        # `not config_snapshot`. A pre-preservation wipe can be followed by the
        # self-heal this issue names (`ensure_tenant_packs` repopulating starter
        # `:PackInstall` rows before the retry), and then the fresh capture is
        # NON-empty while the real custom configuration the old build destroyed
        # is still unknown. Gating on emptiness would report that as a clean
        # `N of N restored` with no marker — a false "restored" for an unknown
        # state, which is the fail-open this state exists to prevent. A false
        # "unknown" is safe and operator-clearable; a false "restored" is not.
        leftover_version = (leftover or {}).get("version")
        config_unknown_staged: dict | None = None
        if (leftover is not None
                and (not isinstance(leftover_version, int)
                     or leftover_version < 2)
                and not any(
                    isinstance(e, dict) and e.get("label") == "Meta"
                    and (e.get("props") or {}).get("key") == _CONFIG_RESET_KEY
                    for e in config_snapshot)):
            # Assign into `merged` AND the local: the pre-wipe PAYLOAD is
            # derived from `merged`, while the restore leg reads the local. A
            # local-only rebind would leave `payload["config_snapshot"] == []`,
            # so a crash between the sidecar write and the replay would leave a
            # v2 sidecar with no config section — and the retry's T2 test
            # (`version < 2`) is then FALSE, so the marker would never be
            # restored and a state-UNKNOWN graph would report `config_reset
            # = False`, i.e. "never configured". That is precisely the window
            # the sidecar exists for.
            config_unknown_staged = {
                "label": "Meta",
                "props": _config_reset_props(
                    None, "legacy_sidecar_no_config_record"),
            }
            merged["config_snapshot"] = [*config_snapshot,
                                        config_unknown_staged]
            config_snapshot = merged["config_snapshot"]
            logger.error(
                "rebuild: the leftover pre-wipe snapshot at %s predates config "
                "preservation (version %r) and carries no config record, so "
                "whether the destroyed graph was configured CANNOT be "
                "determined. Staging the `config_reset` marker with "
                "reason='legacy_sidecar_no_config_record' — this is a "
                "state-UNKNOWN signal, not proof a reset happened. Re-provision "
                "the pack configuration if this graph had any, and see the "
                "rebuild runbook (#2814).",
                snapshot_path, leftover_version)
        events = list(synthetic_events) + journal_events
        # #4042: per-event source-file ordinal, parallel to ``events``.
        # ``None`` marks a synthetic / pre-wipe-snapshot event (it came from
        # no journal file), which never forms a same-source boundary. Built
        # HERE, not at the read loop above: ``synthetic_events`` is
        # reassigned by ``_union_prewipe_snapshot`` just before this line and
        # its length can change on the #2943 recovery path.
        event_source: list[int | None] = (
            [None] * len(synthetic_events) + journal_source)

        # Guard the wipe BEFORE persisting the sidecar: a REFUSED wipe (a
        # non-test graph in server mode) must not leave a sidecar behind, or a
        # later rebuild of this directory would re-merge it. The wipe itself
        # re-checks through _GuardedGraph (idempotent).
        if not self._skip_guard:
            self._assert_test_graph(
                "REFUSING to run bulk DETACH DELETE on non-test graph")

        # Persist immediately before the destructive wipe: after DETACH DELETE
        # these nodes exist nowhere else, so replay must be able to recover
        # them from disk even if THIS process dies. A write failure aborts the
        # rebuild rather than proceeding into an unrecoverable wipe (never
        # silently trade durability for convenience).
        #
        # #3947: the SESSION sections are fatal for the same reason as the
        # #3010 sections. The extractor-minted
        # `(:Session)-[:CONTAINS]->(:Point)` edges are RAW, UNJOURNALED writes
        # scattered through the capture/extraction path (`_link_session` in
        # tortoise/projection/entities.py; #3664/#3722), so this sidecar is
        # their ONLY durable record. A session-only write failure that
        # continued into the wipe would silently and permanently destroy those
        # edges — the exact loss class this change exists to stop. Refuse
        # before the wipe, always.
        snapshot_pending = any(merged[section] for section in _SNAPSHOT_SECTIONS)
        if snapshot_pending:
            try:
                payload: dict = {
                    "version": _PREWIPE_SNAPSHOT_VERSION,
                    "created_at": datetime.now(timezone.utc).isoformat(),  # noqa: UP017
                }
                # #2814: DERIVED from the section tuple and read out of
                # `merged`, so a section dropped from the union's return
                # literal raises KeyError HERE — pre-wipe — instead of being
                # written as `[]`. That distinction matters because the loader
                # reads `[]` and an absent key IDENTICALLY, so an omission
                # would load as empty and be destroyed by the wipe with no
                # error anywhere. (`snapshot_pending` above is likewise
                # derived: an omission there would be worse still — a
                # config-only graph would take the `elif` branch, write no
                # sidecar at all, and lose the config silently.)
                for section in _SNAPSHOT_SECTIONS:
                    payload[section] = [
                        list(entry) if isinstance(entry, tuple) else entry
                        for entry in merged[section]
                    ]
                # #4641: carry the state-UNKNOWN signal (see above) into the
                # file this run writes. Without it, a SECOND interruption
                # leaves a retry reading a sidecar whose onboarding keys are
                # now present-but-empty, which is indistinguishable from
                # "captured and empty" — and the UNKNOWN would be lost.
                if onboarding_unknown:
                    payload["onboarding_unknown"] = True
                _write_prewipe_snapshot(snapshot_path, payload)
            except (OSError, TypeError, ValueError) as e:
                raise RuntimeError(
                    f"rebuild aborted BEFORE the graph wipe: could not persist "
                    f"the pre-wipe snapshot to {snapshot_path} ({e}). Wiping "
                    f"now would destroy {len(synthetic_events)} graph-only "
                    f"Point event(s), {len(batch_snapshot)} :Batch marker(s), "
                    f"{len(batch_point_links)} batch link(s), "
                    f"{len(session_snapshot)} :Session container(s) and "
                    f"{len(session_point_links)} session link(s) with no "
                    f"durable record (#2943, #3947), the "
                    f"{len(onboarding_snapshot)} onboarding state(s) / "
                    f"{len(onboarding_step_links)} step link(s) that ride no "
                    f"journal record at all (#4641) — and the "
                    f"{len(config_snapshot)} captured authoritative config "
                    f"entr(y/ies) with them (#2814). Fix the cause — write "
                    f"permissions/space on the event-log directory, or a "
                    f"non-serializable Point property — and re-run."
                ) from e
        elif leftover is not None:
            # Nothing left to protect — do not leave a stale sidecar behind.
            _clear_prewipe_snapshot(snapshot_path)

        # #3947 review (cycle 2): the proof's coverage must come from the
        # ARTIFACT the replay will stage, never from an earlier read. Deriving
        # it from `synthetic_events` (not from the `existing_points` loop)
        # makes `covered ⊆ what replay creates` structurally true: the #548
        # block's bare `except Exception: pass` can leave the id loop complete
        # but `synthetic_events` empty, and a coverage set built from the id
        # loop would then GREEN + wipe + report success while losing every
        # graph-only Point — the category-A false PASS this PR exists to
        # remove.
        snapshot_ids = self._journal_recreated_ids(synthetic_events)
        # #3947 × #3010: the proof's ROSTER must survive the wipe too. On the
        # sidecar-recovery path the live graph is EMPTY — the interrupted
        # rebuild's wipe already landed — so `episodic_before`, the live read,
        # is the empty set and `_assert_episodic_points_recreatable` returns at
        # its `if not before` short-circuit: the invariant never even evaluates
        # on exactly the path it exists to protect. The durable pre-wipe
        # snapshot IS the record of what existed before that wipe, so union
        # its recovered turn ids into `before`: the Points an EPISODIC
        # `:Session` CONTAINed (ontology §4.5).
        #
        # This leg is CONTAINMENT, not `is_episodic`: the extractor
        # CONTAINS-wires non-episodic Points into a session, so a contained
        # Point need not itself carry the flag, and the recovered set can
        # exceed the Points whose own `is_episodic` was true. That is
        # deliberate — session membership is the independent witness that a
        # turn existed, and it survives a writer silently dropping the
        # server-managed `is_episodic` property (the #3947 failure mode).
        #
        # A sibling leg over `synthetic_events` Points carrying
        # `is_episodic = true` is deliberately ABSENT: it is subsumed by
        # `snapshot_ids`. The loader validates every `synthetic_events` entry
        # as `PointAdded`/`OperatorAdded` (``_validate_point_entry``), both of
        # which are in ``_JOURNAL_CREATING_EVENT_TYPES``, so those ids are
        # always already in `covered` and such a leg could never make the
        # guard fire.
        #
        # This widens the ROSTER only, never `covered`: session membership is
        # not a Point-recreation source (the link is restored from the
        # snapshot, not staged by the replay), and folding the session-linked
        # ids into coverage would make the proof green by construction — the
        # vacuity this re-point removes.
        episodic_session_ids = {
            s.get("id") for s in session_snapshot
            if isinstance(s, dict) and s.get("is_episodic")}
        recovered_session_turns: set[str] = set()
        for sess_id, point_id in session_point_links:
            if sess_id in episodic_session_ids and isinstance(point_id, str):
                recovered_session_turns.add(point_id)
        # The proof runs HERE — after every recreation source is assembled
        # (synthetic snapshot + every JSONL file) and BEFORE the first
        # destructive statement. `episodic_before` is the live read taken
        # before these snapshots, and both reads describe the same pre-wipe
        # graph, so the union is the full pre-wipe roster — including on the
        # empty-live-graph recovery path, where it is no longer empty.
        self._assert_episodic_points_recreatable(
            episodic_before | recovered_session_turns, events,
            snapshot_ids=snapshot_ids)

        self.g.query("MATCH (n) DETACH DELETE n")

        # Pass 1: create all Point/Operator nodes (skip edges) + non-edge events
        # Pass 1a: create all Point/Operator nodes first
        # (skip edges), so cross-file PointRevised always has a node to revise (#21).
        # #2488: last_recreate_seq[id] = journal seq of the id's LAST
        # PointAdded — the pass-1b trailing-sweep survivor anchor (recorded
        # here because pass-1a sees PointAdded in journal order over the
        # SAME events list the pass-1b sweep's enumerate indexes).
        #
        # #3689 review P2: a SECOND anchor, ``last_ann_drop_seq``, is the
        # equivalent boundary for the NON-terminalizing annotator folds —
        # a real hard-delete (EntityMutated op=delete / PointsMerged) followed
        # by a creation, tracked here in journal order. The two anchors differ
        # on a bare same-id re-emission with NO delete: terminalizing folds
        # treat any PointAdded as a boundary (a fresh snapshot clears the old
        # terminal flags — pinned by tests/test_pointinvalidated_rebuild.py),
        # but a bare re-emit only MERGEs live and never clears ``annotator_*``,
        # so gating the annotator folds on it silently dropped a live-valid
        # annotation (``update_entity``/raw-producer duplicate snapshot).
        # #3860: the identity is ``(kind, id)`` — ``kind`` is the canonical
        # graph label, so a delete for one KIND can never match or advance the
        # boundary of another kind sharing the id.
        last_recreate_seq: dict[tuple[str, str], int] = {}
        # #3860: bare-id max over kinds — the LEGACY fallback anchor for a
        # delete record with a missing/unknown label (the fold falls back
        # id-wide there, so the anchor must too, else a legitimate
        # delete→recreate Point is destroyed).
        last_recreate_seq_any: dict[str, int] = {}
        last_ann_drop_seq: dict[tuple[str, str], int] = {}
        # #4042: same-journal-file chronology anchors for the pass-1b content
        # boundary. #3860 composition: the key is ``((kind, id), source-file
        # ordinal)`` — the #3860 kind scoping applied to the #4042 source
        # ordinal. The ordinal is what makes POSITION chronology (true within
        # one append-only JSONL, false across files — #21 pins that a revision
        # in an earlier-sorted file must still fold onto a creation in a later
        # file); the ``(kind, id)`` prefix is what keeps a foreign-kind node
        # with the same id from supplying a boundary, exactly as it does for
        # ``last_recreate_seq`` above. Every creation event in this branch is
        # Point-labeled — PointAdded and OperatorAdded both hoist to a
        # ``:Point`` node — so the kind is ``"Point"``, the same value main's
        # creation branch writes into ``last_recreate_seq``. All four are
        # filled in the pass-1a creation branch below.
        #   ``last_create_seq_by_source``      — the id's last creation
        #     (``n.content``/``n.updatedAt`` are written unconditionally, so
        #     any later creation supersedes a revision's content).
        #   ``last_recreate_seq_by_source``    — the id's last creation that
        #     followed a hard delete; live that node was FRESH, so both
        #     conditional derived fields were cleared.
        #   ``last_embed_write_by_source`` / ``last_hash_write_by_source`` —
        #     the last creation that actually WROTE each conditional derived
        #     field (from ``_upsert_point_props``'s reported outcome).
        last_create_seq_by_source: dict[tuple[tuple[str, str], int], int] = {}
        last_recreate_seq_by_source: dict[tuple[tuple[str, str], int], int] = {}
        last_embed_write_by_source: dict[tuple[tuple[str, str], int], int] = {}
        last_hash_write_by_source: dict[tuple[tuple[str, str], int], int] = {}
        # #4305: id-WIDE (cross-file) journal-owned derived markers for the
        # pass-1b restore tail. The per-source anchors above are a WITHIN-file
        # chronology boundary; the restore tail runs AFTER every journal file,
        # so it needs the file-blind question "did ANY journaled event
        # determine this field?".
        #   ``journal_hash_write`` / ``journal_embed_write`` — a journaled
        #     event wrote OR explicitly cleared that conditional derived field
        #     for the id. The pre-wipe snapshot value is then the OLDER writer
        #     and must NOT be re-applied over it (shape H, point_promoted,
        #     revise_to_empty).
        #   ``journal_deleted`` — the journal HARD-deleted the id, destroying
        #     the incarnation the snapshot describes; its derived belongs to a
        #     dead node and must not be restored (delete_then_falsy_recreate).
        # A SYNTHETIC event never contributes here: its source ordinal is None,
        # so it is not a journal writer.
        journal_hash_write: set[str] = set()
        journal_embed_write: set[str] = set()
        journal_deleted: set[str] = set()
        # (kind, id) pairs hard-deleted since their last creation — a
        # following creation of the SAME kind is a RE-creation (new
        # incarnation), not a bare upsert. #3860: keyed by (kind, id), so a
        # foreign-kind delete cannot advance the POINT annotator boundary.
        pending_deleted: set[tuple[str, str]] = set()
        for seq, ev in enumerate(events):
            ev = self._norm(ev)
            t = ev.get("type")
            # #3689 review P2: observe hard deletes in the same ordered scan
            # (EntityMutated delete is the #3299 record; PointsMerged deletes
            # every merge_id in pass-1b) so a following creation can be told
            # apart from a bare upsert.
            if t == "EntityMutated" and ev.get("op") == "delete":
                rid = ev.get("id")
                # #3860: only a Point-kind delete (or a missing/unknown
                # label, which the fold treats as possibly-Point) advances the
                # POINT annotator boundary.
                if isinstance(rid, str) and _owns_point(ev.get("label")):
                    pending_deleted.add(("Point", rid))
                continue
            if t == "PointsMerged":
                for mid in ev.get("merge_ids") or []:
                    if isinstance(mid, str):
                        pending_deleted.add(("Point", mid))
                continue
            if t in ("PointAdded", "OperatorAdded"):
                # #331 (review r3): ev.get — missing 'point' key handled by
                # the isinstance guard, not KeyError.
                p = ev.get("point")
                if not isinstance(p, dict):
                    # Malformed event (non-dict/missing point) — skip (#325)
                    continue
                # #331 (review r2): parity with apply()/_apply_one — no id →
                # nothing to index by; skip rather than KeyError in
                # _upsert_point_props.
                # #331 (review r4): str-only ids.
                if not isinstance(p.get("id"), str):
                    logger.warning(
                        "rebuild: skipping %s with missing point id "
                        "(event_id=%s)", t, ev.get("event_id"))
                    continue
                # #2488/#3299: record the id's LAST hoisted-creation journal
                # seq — the cross-family survivor anchor (a re-created id's
                # pre-recreation terminalizing folds died with the deleted
                # node). PointAdded is the original #2488 anchor; #3299 adds
                # OperatorAdded because an EntityMutated delete is entity-wide
                # (an operator IS a Point node), so a delete→recreate operator
                # journal needs the identical survivor rule. PointPromoted is
                # NOT a drop boundary (promote is same-node draft→live; it
                # never clears outdated/CORRECTS, so seeding from it would
                # silently drop a pre-promote invalidate fold).
                if t in ("PointAdded", "OperatorAdded"):
                    last_recreate_seq[("Point", p["id"])] = seq
                    last_recreate_seq_any[p["id"]] = seq
                # #3689 review P2: the annotator folds' drop boundary is a
                # REAL delete→recreate, not a bare upsert (see above).
                # #3689 review P2 + #3860: the annotator folds' drop boundary
                # is a REAL delete→recreate of the SAME KIND.
                # ``pending_deleted`` is keyed ``(kind, id)``, so
                # ``is_recreate`` is True only for a Point-kind delete (or a
                # missing/unknown label, which the fold treats as
                # possibly-Point) — a foreign-kind delete cannot advance the
                # Point annotator boundary. #4042 reuses ``is_recreate`` below
                # for the recreate wipe and its per-source anchors.
                is_recreate = ("Point", p["id"]) in pending_deleted
                if is_recreate:
                    last_ann_drop_seq[("Point", p["id"])] = seq
                    pending_deleted.discard(("Point", p["id"]))
                # Phase 1 stop-writes: strip context from v2+ events (#49)
                # (identical to apply() — parity between rebuild and apply)
                if ev.get("projection_version", 0) >= 2:
                    p.pop("context", None)
                # #4042: a re-creation is a FRESH node live — the delete
                # removed it, so `_upsert_point_props`'s conditional derived
                # writers (`n.embedding = CASE WHEN $embedding IS NOT NULL …
                # ELSE n.embedding END`, `n.content_hash = coalesce($ch, …)`)
                # preserve NOTHING. The pass-1a hoist MERGEs onto the
                # still-present node instead, so without this wipe the
                # pre-delete incarnation's embedding/content_hash survive a
                # recreate that writes none (falsy content, an operator, or
                # an unavailable embedder). Targeted SET — the
                # `_GuardedGraph` bulk-wipe guard is DETACH DELETE-only.
                if is_recreate:
                    # #5004 round-4: `embedding_verbatim` is wiped here TOO.
                    # It is a declared NODE property now, and the wipe's own
                    # premise ("a re-creation is a FRESH node live") applies to
                    # it exactly as it does to `embedding`: its clause uses
                    # `CASE … ELSE n.embedding_verbatim`, which PRESERVES the
                    # dead incarnation's marker when the re-creation carries
                    # none — so the rebuilt node would hold a property the live
                    # node does not (the #330/#3312 parity break), and a leaked
                    # `true` would make a later re-emit store the new vector
                    # RAW and skip the R1 attestation.
                    self.g.query(
                        "MATCH (n:Point {id:$id}) "
                        "SET n.embedding = NULL, n.content_hash = NULL, "
                        "    n.embedding_verbatim = NULL",
                        params={"id": p["id"]})
                # Property parity with apply()/apply_one (#330): the shared
                # helper writes ALL node properties incl. authoredBy,
                # embedding, validFrom/To, extractedFrom, provenanceSource.
                # #4042: it also reports which of the two CONDITIONAL derived
                # fields it actually wrote — the pass-1b content boundary
                # needs the outcome, never a `bool(content)` guess (the
                # embedder can be unavailable while the hash still computes).
                wrote_embedding, wrote_content_hash = (
                    self._upsert_point_props(p))
                # #4042: record this creation's per-source-file chronology
                # anchors (see their declaration above). A synthetic event
                # has `src is None` and never forms a boundary. #3860
                # composition: the inner key is the kind-scoped ``(kind, id)``
                # main uses for ``last_recreate_seq`` (both PointAdded and
                # OperatorAdded hoist to a ``:Point`` node), so the full key is
                # ``((kind, id), source-file ordinal)``.
                src = event_source[seq]
                if src is not None:
                    key = (("Point", p["id"]), src)
                    last_create_seq_by_source[key] = seq
                    if is_recreate:
                        last_recreate_seq_by_source[key] = seq
                    if wrote_embedding:
                        last_embed_write_by_source[key] = seq
                    if wrote_content_hash:
                        last_hash_write_by_source[key] = seq
                    # #4305: the id-wide counterpart (see the sets'
                    # declaration). A re-creation explicitly cleared both
                    # conditional fields before the upsert, so it owns them
                    # whether or not the upsert then wrote them back.
                    #
                    # #5004 review: key PRESENCE matters INDEPENDENTLY of
                    # `wrote_embedding`. When the journal carried a vector the
                    # replay REFUSED to write (a wrong-width vector this store
                    # cannot hold), gating on `wrote_embedding` alone left the
                    # id unmarked and the pass-1b tail re-applied the OLDER
                    # pre-wipe snapshot — making `rebuild_all` a function of
                    # pre-wipe graph state (a populated store, `rebuild()`, and
                    # a fresh store then disagreed). Round-3: the mark is the
                    # KEY's presence, not its truthiness — an owned `None` is
                    # the journal saying "no vector here", so the tail must not
                    # re-apply an older one either.
                    if (is_recreate or wrote_embedding
                            or "embedding" in p):
                        journal_embed_write.add(p["id"])
                    if is_recreate or wrote_content_hash:
                        journal_hash_write.add(p["id"])

        supersede_folds: list = []  # ObjectSuperseded replays (pass-1b fold sweep)
        # #4743 review P1 — journal order across the deferral boundary.
        # `EntityMutated` STATE folds run INLINE below, but `ObjectSuperseded`
        # folds are DEFERRED to the sweep after pass 1b (deliberately: the fold
        # is an unconditional `SET o.status='superseded'` and must run after
        # every Object-creation event). Without journal order being consulted
        # here, the deferred sweep wins over a LATER state op on the same
        # Object: live=`archived` → rebuild_all=`superseded`, i.e. exactly the
        # revert this lane exists to remove — while `apply()`/`rebuild()` fold
        # both inline in journal order and land on `archived`, so the engines
        # disagree. Record the ORDERED state folds per (label, id) and the last
        # ObjectSuperseded per key, both by journal `seq`, then replay ONLY the
        # state ops the sweep actually clobbered (a supersede AFTER them must
        # still win — that is live truth).
        #
        # The list matters, not just the terminal event: the sweep clobbers the
        # WHOLE inline history for the keys it writes, so `sup → status=archived
        # → name=New` leaves `status` clobbered even though the LAST state op is
        # the rename — replaying only that one would silently drop the status
        # (round 2 of this review found exactly that).
        #
        # The supersede seq is keyed by BOTH id and name: `_fold_object_superseded`
        # falls back to matching by NAME for legacy id-less records (#2164
        # ISSUE-B), so an id-keyed map alone would treat such a journal as
        # clobber-free and could re-fold a state op over the supersede.
        # DEFENSIVE ONLY — a review round-2 repro of that regression could not be
        # reproduced in this worktree (the name-only fold did not apply here at
        # all), so this half is reasoned, not empirically pinned, and has no test.
        _state_folds: dict = {}
        _supersede_seq: dict = {}
        # #3664: EntityLinked records are deferred to a trailing sweep that
        # runs after PASS 2 (see the sweep before pass 2b). The deferral is
        # for the SOURCE endpoint: the TARGET is already created by pass-1b
        # ObjectRegistered/DocumentCreated (the same dispatch loop that
        # defers this type also folds `self._upsert_object(ev)`), but a
        # Session SOURCE may exist only because pass 2's
        # `_upsert_point_edges(contains_session=…)` recreated it
        # (`_link_session`) — folding at the end of pass 1b matched only the
        # TARGET endpoint, so it silently dropped the journaled
        # `(Session)-[:aboutObject]->(Object)` edge whenever the journal had
        # no `SessionRecorded` for it. The fold is an idempotent MERGE, so
        # running it later changes nothing else. Records are buffered WITH
        # their journal (enumerate) seq so the sweep can apply the hard-delete
        # staleness rule (#3722 review P2) in the SAME seq space pass 1a uses.
        entity_link_events: list[tuple[int, dict]] = []
        # #2488: ONE cross-family deferred list for point re-stamp folds —
        # PointSuperseded (#2423) + PointInvalidated (#2488) — carrying the
        # journal (enumerate) seq: the trailing sweep's survivor rule and
        # updatedAt seq-gate need the faithful order (ts collides within a
        # ms; JSONL carries no seq). Same events list pass-1a enumerates ⇒
        # identical seq space as last_recreate_seq.
        point_re_stamp_folds: list[tuple[int, dict]] = []
        # #2488: per-id highest journal seq of a same-id INLINE updatedAt
        # writer (PointRevised / PointPromoted — pass-1b applies both
        # inline; they never enter the deferred list); a PointInvalidated
        # fold whose seq is EARLIER than this must not clobber the newer
        # inline stamp with the older journaled invalidate ts.
        max_inline_seq: dict[str, int] = {}
        # #2423: DirectEdgeRepoint descriptors (supersede's 2a-DIRECT transfer
        # journal) — replayed in pass-2b AFTER operator edges exist.
        direct_repoint_events: list = []
        # #2884 A3 (supersede): the supersede family's BELIEF decay folds
        # INLINE at the event's own journal seq, exactly as the invalidate
        # family's does (`_decay_point_belief`). The trailing sweep runs AFTER
        # the whole pass-1b loop, so a `decay_clause` left in
        # `_fold_point_superseded` overwrote every LATER same-id belief writer
        # (a journaled ConfidenceChanged, or a PointRevised carrying
        # confidence): replay ended at the decayed 0.5 while live ended at the
        # later writer's value (write != read — the same defect the invalidate
        # fix closed).
        #
        # Resolve, PER OLD-ID, the surviving supersede whose decay applies —
        # the LAST event not obsoleted by a REAL hard-delete boundary. The
        # anchor is ``last_ann_drop_seq`` (advanced only by a real
        # ``EntityMutated op=delete`` / ``PointsMerged`` creation), NOT the
        # terminalizing ``last_recreate_seq``: a bare same-id re-emit MERGEs
        # live and keeps belief state, so gating the decay on it would drop a
        # decay live actually applied. Pass 1a has populated both anchors
        # before this runs, over the SAME ``events`` list the sweep enumerates.
        supersede_decay_seq: dict[str, int] = {}
        for seq, ev in enumerate(events):
            ev = self._norm(ev)
            if ev.get("type") != "PointSuperseded":
                continue
            _sd_rid = ev.get("id")
            # Mirror ``_fold_point_superseded``'s applicability guard
            # (``if not oid or not new_id: return 0``) EXACTLY: the sweep fold
            # IGNORES an event that lacks ``new_id``, so the inline decay must
            # not fire for one either — live never decayed for such an event,
            # and a decay here would be a belief write the graph never
            # received. The falsy-id test matters as much as the type test: an
            # EMPTY-STRING id passes ``isinstance(..., str)`` (and
            # ``_writable_id``) but is skipped by the fold, so a type-only gate
            # would decay a node the fold ignores.
            if (not isinstance(_sd_rid, str) or not _sd_rid
                    or not ev.get("new_id")):
                continue
            _sd_drop = last_ann_drop_seq.get(("Point", _sd_rid))
            if _sd_drop is not None and seq <= _sd_drop:
                continue
            supersede_decay_seq[_sd_rid] = seq
        # Pass 1b: apply revisions + other non-edge events AFTER all nodes exist
        for seq, ev in enumerate(events):
            ev = self._norm(ev)
            t = ev.get("type")
            if t in ("PointAdded", "OperatorAdded"):
                continue  # already handled in pass 1a
            elif t == "PointRetracted":
                # #689: tombstone instead of hard delete.
                # #331 (review r3): NO event_id fallback — parity with
                # apply()/_apply_one (fold is the single source of truth).
                # #331 (review r4): str-only ids.
                rid = ev.get("id")
                if isinstance(rid, str):
                    # #2488 (code-review P2-3): a retract after an invalidate
                    # is the newer writer — record it so the sweep fold's
                    # skip_updated_at gate omits updatedAt (otherwise the fold
                    # regresses the tombstone's stamp below the retract's own
                    # rebuild stamp).
                    max_inline_seq[rid] = seq
                    # #2884 A7: ``_retract`` carries ``decay_clause`` (a BELIEF
                    # write) as well as the status tombstone, so this fold is a
                    # belief writer and needs the SAME hard-delete boundary as
                    # ``ConfidenceChanged``/``PointRevised``/both decay
                    # families. Without it, a retract that predates a real
                    # delete→recreate re-applies onto the fresh incarnation and
                    # resurrects a belief the live graph never had (pass-1a
                    # hoists every ``PointAdded``, so the fresh node already
                    # exists by pass 1b). "retracted" is not lost for a bare
                    # same-id re-emit: that advances no drop boundary, so the
                    # tombstone still applies.
                    _retr_anchor = last_ann_drop_seq.get(("Point", rid))
                    if _retr_anchor is None or seq > _retr_anchor:
                        self._retract(rid)
            elif t == "PointPromoted":
                # #785: rebuild parity — re-apply the promoted snapshot.
                p = ev.get("point")
                if isinstance(p, dict) and p.get("id"):
                    if isinstance(p["id"], str):
                        # #2488: promote stamps updatedAt inline (the CAS
                        # below re-applies the snapshot) — a same-id promote
                        # is a newer writer than an earlier invalidate (the
                        # sweep skip_updated_at gate source).
                        max_inline_seq[p["id"]] = seq
                    if ev.get("projection_version", 0) >= 2:
                        p.pop("context", None)
                    # #2884 A7: a promote snapshot is ``get_point(...)`` and so
                    # carries the belief props — this fold is a belief writer
                    # too. Gate the belief half on the same hard-delete
                    # boundary: a promote that predates a real
                    # delete→recreate must not overwrite the fresh
                    # incarnation's belief with a dead one's. The non-belief
                    # props (content, status, embedding, …) are unaffected —
                    # they are the point of the fold (#785 parity).
                    _prom_props = p
                    _prom_anchor = last_ann_drop_seq.get(("Point", p["id"]))
                    if _prom_anchor is not None and seq <= _prom_anchor:
                        _prom_props = {k: v for k, v in p.items()
                                       if k not in BELIEF_PROPS}
                    wrote_embedding, wrote_content_hash = (
                        self._upsert_point_props(_prom_props))
                    # #4305: id-wide journal-owned derived marks. #5004: the
                    # payload carrying the key is itself ownership, even when
                    # the fold refused to write it (wrong width, or an owned
                    # None) — otherwise the pass-1b tail re-applies the older
                    # pre-wipe snapshot.
                    if wrote_embedding or "embedding" in p:
                        journal_embed_write.add(p["id"])
                    if wrote_content_hash:
                        journal_hash_write.add(p["id"])
            elif t == "OperatorPromoted":
                # #785/R16: fold/apply parity with the main handler
                # (#2256 review P1): UPSERT the snapshot synthesized into
                # the canonical nested-operator shape — for the capture path
                # this event is the operator's only durable record.
                p = ev.get("point")
                if isinstance(p, dict) and p.get("id"):
                    if ev.get("projection_version", 0) >= 2:
                        p.pop("context", None)
                    op_p = _promotion_point_with_operator(p)
                    wrote_embedding, wrote_content_hash = (
                        self._upsert_point_props(op_p))
                    # #4305: id-wide journal-owned derived marks. #5004: the
                    # payload carrying the key is itself ownership, even when
                    # the fold refused to write it (wrong width, or an owned
                    # None).
                    if wrote_embedding or "embedding" in p:
                        journal_embed_write.add(p["id"])
                    if wrote_content_hash:
                        journal_hash_write.add(p["id"])
                else:
                    oid = ev.get("id") or ev.get("event_id")
                    if oid is not None:
                        self.g.query(
                            "MATCH (n:Point {id:$id}) SET n.status = 'live'",
                            params={"id": oid},
                        )
            elif t == "PointsMerged":
                # #331 (review r2): `or []` also covers "merge_ids": null.
                for mid in ev.get("merge_ids") or []:
                    # #331 (review r4): str-only ids.
                    if isinstance(mid, str):
                        # #4305: a merge hard-deletes the id (see _delete) —
                        # the snapshot's derived must not be restored onto a
                        # node a later re-creation brings back.
                        journal_deleted.add(mid)
                        self._delete(mid)
            elif t == "EntityMutated":
                # #3299 pass-1b rebuild parity: apply() folds the
                # write-surface mutation record; the rebuild chain needs the
                # SAME branch or a journaled delete silently falls through
                # and the entity's creation event (pass 1a PointAdded /
                # OperatorAdded, or the pass-1b Subject/Object/Event/
                # Document/Source upsert) resurrects it.
                #
                # Inline ordering holds WITHIN pass-1b: for non-hoisted
                # labels the creation and the delete live in this same loop,
                # so a delete→recreate journal ends with the node present and
                # replaying the hard delete is idempotent.
                #
                # Hoisted Point/Operator creations do NOT: pass-1a applies
                # EVERY PointAdded/OperatorAdded before this loop runs, so a
                # naive inline fold would delete a re-created incarnation
                # (delete ALWAYS executes after every create, regardless of
                # journal order). Apply the #2488 survivor rule inverted — a
                # delete whose seq precedes the id's last hoisted creation
                # was already superseded live by that re-creation, so it must
                # not be folded (same anchor variable and comparison shape as
                # the point_re_stamp_folds sweep below).
                # #3860: identity is (kind, id). The anchor consults the
                # record's LABEL, so a Point/Operator creation can suppress
                # ONLY a delete of the same kind — a foreign-kind node sharing
                # the id no longer suppresses a legitimate delete. A missing/
                # unknown label (malformed or pre-#3299 raw record) falls back
                # to the any-kind max, preserving the legacy bare-id
                # semantics because the fold falls back id-wide there too.
                rid = ev.get("id")
                # #4305: a journaled hard delete destroys the incarnation the
                # pre-wipe snapshot describes, so its derived must not be
                # restored later. #4305 review P1: use the SAME label→ownership
                # predicate the fold below and `pending_deleted` use
                # (`_owns_point`), NOT an inline canonical-label tuple —
                # `_delete_entity_by_id` falls back to the legacy ID-WIDE delete
                # for a non-canonical/non-str label, so such a record really
                # does destroy the `:Point` and must be a restore barrier.
                if (ev.get("op") == "delete" and isinstance(rid, str)
                        and _owns_point(ev.get("label"))):
                    journal_deleted.add(rid)
                anchor = None
                if isinstance(rid, str):
                    label = ev.get("label")
                    if isinstance(label, str) and label in _CANONICAL_ENTITY_LABELS:
                        anchor = last_recreate_seq.get((label, rid))
                    else:
                        anchor = last_recreate_seq_any.get(rid)
                if anchor is not None and seq <= anchor:
                    continue
                if ev.get("op") in _ENTITY_MUTATION_STATE_OPS and isinstance(rid, str):
                    _state_folds.setdefault((ev.get("label"), rid), []).append(
                        (seq, ev))
                matched = self._fold_entity_mutation(ev)
                if matched == 0 and ev.get("op") == "delete":
                    # Fold-miss signal (the journal claims a delete whose
                    # entity never re-existed on this replay — mirrors the
                    # ObjectSuperseded / PointSuperseded / PointInvalidated
                    # 0-row warnings). #3860: the fold is now kind-scoped, so
                    # a multi-label live delete's per-label records each
                    # match their own node instead of the second one matching
                    # 0.
                    logger.warning(
                        "rebuild: EntityMutated delete fold matched no "
                        "entity (event_id=%s id=%r label=%r) — deleted "
                        "entity not re-created by any journaled event "
                        "(unjournaled creation, legacy journal, or delete "
                        "race)",
                        ev.get("event_id"), rid, ev.get("label"))
            elif t == "PointRevised":
                # Phase 1: discard new_context for v2+ events (#49)
                if ev.get("projection_version", 0) >= 2:
                    ev.pop("new_context", None)
                # #2488: revise stamps updatedAt inline (the fold below) — a
                # same-id revise later than an invalidate is the newer writer
                # (the sweep skip_updated_at gate source).
                rid = ev.get("id")
                if isinstance(rid, str):
                    max_inline_seq[rid] = seq
                # #3689 review P1/P2: a PRE-recreation revise's annotator dims
                # died with the deleted incarnation live — fold them only when
                # the revision postdates the id's last HARD-DELETE boundary
                # (``last_ann_drop_seq``). Without this gate the pass-1a hoist
                # leaks the dead incarnation's dims onto the re-created node,
                # diverging from the chronological apply()/fold() (#330);
                # gating on the terminalizing ``last_recreate_seq`` anchor
                # instead would over-suppress a bare same-id re-emit (which
                # MERGEs live and never clears a dim).
                ann_anchor = (
                    last_ann_drop_seq.get(("Point", rid))
                    if isinstance(rid, str) else None)
                # #4042: the CONTENT/derived boundary is the id's last creation
                # in the SAME journal file. Position IS chronology inside one
                # append-only JSONL, so a later same-file creation
                # demonstrably superseded this revision live:
                # `_upsert_point_props` writes `n.content`/`n.updatedAt`
                # UNCONDITIONALLY (content boundary), and writes
                # `embedding`/`content_hash` conditionally — so each derived
                # field is suppressed only when a later same-file creation
                # actually wrote it, or when a re-creation cleared it. Cross-
                # file order is NOT chronology (#21 pins that a revision in an
                # earlier-sorted file must still fold), hence the same-source
                # key and no cross-file gate. The annotator dims keep their
                # OWN boundary above (a bare re-emit never clears a dim).
                src = event_source[seq]
                superseded = False
                skip_embedding = False
                skip_hash = False
                if isinstance(rid, str) and src is not None:
                    # #3860 composition: a PointRevised revises a ``:Point``,
                    # so its anchor key carries the same kind-scoped inner key
                    # the creation branch wrote — ``((kind, id), ordinal)``.
                    key = (("Point", rid), src)
                    create_seq = last_create_seq_by_source.get(key)
                    superseded = create_seq is not None and create_seq > seq
                    if superseded:
                        skip_embedding = (
                            last_recreate_seq_by_source.get(key, -1) > seq
                            or last_embed_write_by_source.get(key, -1) > seq)
                        skip_hash = (
                            last_recreate_seq_by_source.get(key, -1) > seq
                            or last_hash_write_by_source.get(key, -1) > seq)
                wrote_embedding, wrote_content_hash = self._revise_point(
                    ev, set_updated_at=True,
                    skip_annotator_dims=(
                        ann_anchor is not None and seq <= ann_anchor),
                    # #2884 A7: the belief props ride the SAME real-hard-delete
                    # boundary as the annotator dims — a pre-recreation
                    # revision's belief value died with the deleted node live
                    # (the pure fold POPS the entry on delete; the graph must
                    # agree or #330 parity breaks). A bare same-id re-emit is
                    # NOT a boundary (it MERGEs live and keeps belief state).
                    skip_belief_props=(
                        ann_anchor is not None and seq <= ann_anchor),
                    skip_content=superseded,
                    skip_embedding=skip_embedding,
                    skip_hash=skip_hash)
                if isinstance(rid, str):
                    # #4305: id-wide journal-owned derived marks — the revision
                    # wrote (or explicitly wiped) each field it was not told
                    # to skip, so the snapshot must not overwrite it later.
                    if wrote_embedding:
                        journal_embed_write.add(rid)
                    if wrote_content_hash:
                        journal_hash_write.add(rid)
            elif t == "OperatorAnnotated":
                # #3689 pass-1b rebuild parity: apply() folds the explicit
                # annotation record, and the rebuild chain needs the SAME
                # branch — without it a journaled OperatorAnnotated falls to
                # the unrecognized-type warning AND its dims are lost whenever
                # it is the only carrier (a raw producer, or a PointRevised
                # pruned from the journal).
                #
                # #3689 review P1/P2: pass-1a hoists EVERY creation before this
                # loop, so an annotation that predates the id's last HARD-DELETE
                # boundary would otherwise fold onto the re-created incarnation
                # — its subject died with the deleted node live. Gate on
                # ``last_ann_drop_seq`` (real delete→recreate), NOT the
                # terminalizing ``last_recreate_seq``: a bare same-id re-emit
                # has no dead incarnation and must not drop the annotation
                # (#3689 review P2).
                if isinstance(ev.get("id"), str):
                    ann_anchor = last_ann_drop_seq.get(("Point", ev["id"]))
                    if ann_anchor is not None and seq <= ann_anchor:
                        continue
                if self._apply_annotator(ev) == 0:
                    # #3689: the defect was SILENT loss — an annotation that
                    # cannot be folded must be audible, mirroring the
                    # EntityMutated / PointSuperseded / PointInvalidated
                    # fold-miss warnings.
                    logger.warning(
                        "rebuild: OperatorAnnotated fold matched no Point "
                        "(event_id=%s id=%r) — operator not re-created by "
                        "any journaled event, or the record carried no "
                        "annotator dim",
                        ev.get("event_id"), ev.get("id"))
            elif t == "ConfidenceChanged":
                # #2884 D3: pass-1b rebuild parity — the EP/dream write-back's
                # durability record. Folded INLINE (chronological, journal
                # order): pass-1a already re-created every Point, so the
                # target always exists, and an id's LAST record must win (a
                # dream stamp after an EP flush). The fold may match 0 rows
                # when the target was hard-deleted / never re-created —
                # audible, mirroring the PointSuperseded / EntityMutated /
                # OperatorAnnotated fold-miss warnings.
                #
                # #2884 A7: gate on the SAME real-hard-delete boundary as the
                # annotator dims (`last_ann_drop_seq`) — a belief write that
                # predates a hard delete→recreate wrote onto an incarnation
                # live DETACH DELETEd, so the re-created node never had it.
                # Folding it would resurrect the dead node's belief; the pure
                # fold POPS the entry on delete (#330 parity). NOT gated on
                # the terminalizing `last_recreate_seq`: a bare same-id
                # re-emit MERGEs live and keeps belief state, so dropping the
                # fold there would lose a live-valid value.
                _cc_rid = ev.get("id")
                _cc_anchor = (
                    last_ann_drop_seq.get(("Point", _cc_rid))
                    if isinstance(_cc_rid, str) else None)
                if _cc_anchor is not None and seq <= _cc_anchor:
                    continue
                if self._fold_confidence_changed(ev) == 0:
                    logger.warning(
                        "rebuild: ConfidenceChanged fold matched no Point "
                        "(event_id=%s id=%r) — point not re-created by any "
                        "journaled event (legacy journal, unjournaled "
                        "producer, or delete race)",
                        ev.get("event_id"), ev.get("id"))
            elif t == "EventRecorded":
                self._upsert_event(ev)
            elif t == "SubjectAdded":
                self._upsert_subject(ev)
            elif t == "ObjectRegistered":
                self._upsert_object(ev)
            elif t == "ObjectSuperseded":
                # #2164 pass-1b rebuild parity: apply() folds ObjectSuperseded
                # into Object.status, but the rebuild chain had no branch — a
                # journaled ObjectSuperseded silently fell through and the
                # Object reverted to status='live' on JSONL wipe+rebuild.
                # Mirror the apply() dispatch — but defer the fold to a
                # trailing sweep (below, after this loop): a journaled
                # producer (connector EventRecorded → _upsert_event produces-
                # edge MERGE ON CREATE, or a later ObjectRegistered) can
                # legitimately re-create the Object AFTER the fold event sits
                # in the journal. Folding chronologically inside this loop
                # would no-op (0 rows) while the object does not yet exist,
                # then the later re-creation resurrects it status='live' —
                # a dead Object reappearing in recall_state's default view.
                # Replay the fold only once every object-creation event has
                # run: the fold is an idempotent SET, so ordering vs its own
                # registration is irrelevant and later re-creations are
                # re-folded correctly.
                supersede_folds.append(ev)
                _sid, _sname = ev.get("id"), ev.get("name")
                if isinstance(_sid, str):
                    _supersede_seq[("id", _sid)] = max(
                        seq, _supersede_seq.get(("id", _sid), -1))
                if isinstance(_sname, str):
                    _supersede_seq[("name", _sname)] = max(
                        seq, _supersede_seq.get(("name", _sname), -1))
            elif t == "PointSuperseded":
                # #2423 pass-1b rebuild parity: the POINT-side analog of
                # #2164 (ObjectSuperseded above) — apply() has no supersede
                # branch because live supersede_point mutates the graph
                # directly (sdk.py status block + CORRECTS MERGE), but the
                # REBUILD chain had NO branch either: a journaled
                # PointSuperseded silently fell through and the superseded
                # Point reverted to status='live' (its PointAdded snapshot
                # predates the supersede), losing outdated/validTo/
                # expiredAt + the CORRECTS edge (indicator 1) — a dead
                # claim reappearing in default reads. Mirror the #2164
                # pattern: defer the status/validity/CORRECTS fold to a
                # trailing sweep (below, after this loop) so a later
                # PointAdded/PointPromoted re-creation of the same id cannot
                # resurrect it — the fold is an idempotent SET, so ordering
                # vs its own creation event is irrelevant and later
                # re-creations are re-folded correctly. (The operator-edge
                # re-point + DirectEdgeRepoint replay half is pass-2b —
                # after pass-2 rebuilds edges from operator snapshots that
                # still name the OLD input.)
                #
                # #2884 A3: the BELIEF-decay half (`decay_clause`) folds INLINE
                # HERE too, at the surviving event's own journal seq (resolved
                # by the ``supersede_decay_seq`` pre-pass above) — NOT in the
                # trailing sweep. The sweep fold below no longer writes any
                # belief prop, so inline and sweep are mutually exclusive by
                # construction for the same event (only this branch writes the
                # decay). The anchor is ``last_ann_drop_seq``, NOT
                # ``last_recreate_seq`` (see the pre-pass).
                if isinstance(ev.get("id"), str):
                    if supersede_decay_seq.get(ev["id"]) == seq:
                        self._decay_point_belief(ev["id"])
                    point_re_stamp_folds.append((seq, ev))
            elif t == "PointInvalidated":
                # #2488 pass-1b rebuild parity: the POINT-side invalidate
                # analog of the PointSuperseded branch above — live
                # invalidate_point mutates the graph directly (outdated flag
                # SET + CORRECTS MERGE, no status write) and the REBUILD
                # chain had NO branch either: a journaled PointInvalidated
                # silently fell through, so a JSONL wipe+rebuild replayed
                # the pre-invalidate PointAdded and RESURRECTED the claim to
                # EP voting/reads (the #2488 ghost). Defer to the SAME
                # trailing sweep as PointSuperseded (one cross-family list)
                # with the enumerate seq — the survivor rule drops
                # pre-re-creation folds and the sweep folds survivors in
                # journal-append order. The fold applies outdated=true +
                # validTo/expiredAt/updatedAt + CORRECTS only — it never
                # writes status (an invalidated point stays status='live').
                #
                # #2884 A3: the BELIEF half (`decay_clause`) folds INLINE
                # HERE, at the event's own journal position, NOT in the
                # trailing sweep. The sweep runs after the whole pass-1b
                # loop, so a decay applied there clobbers every LATER
                # same-id inline belief writer (ConfidenceChanged / a
                # PointRevised carrying confidence): replay ended at 0.5
                # while live ended at the later writer's value. Inline
                # application makes journal order decide, as live
                # chronology does; the sweep keeps the stamp/CORRECTS half.
                # #2884 A3b: the BELIEF decay anchors on the REAL hard-delete
                # boundary (``last_ann_drop_seq``) — the same anchor the
                # supersede pre-pass and the ConfidenceChanged fold use.
                # ``last_recreate_seq`` is the STATUS half's anchor: it is
                # advanced by ANY PointAdded, including a bare same-id re-emit
                # that MERGEs live (``n.confidence = coalesce($cf,
                # n.confidence)``) and therefore KEEPS the decayed belief.
                # Gating belief on it suppressed the decay for that shape, so
                # replay ended at the pre-invalidate value while live held the
                # decayed one. The two families cannot hold opposite policies
                # for one journal shape.
                if isinstance(ev.get("id"), str):
                    _inv_rid = ev["id"]
                    _inv_anchor = last_ann_drop_seq.get(("Point", _inv_rid))
                    if _inv_anchor is None or seq > _inv_anchor:
                        self._decay_point_belief(_inv_rid)
                    point_re_stamp_folds.append((seq, ev))
            elif t == "DirectEdgeRepoint":
                # #2423: supersede's 2a-DIRECT transfer emits a flat
                # DirectEdgeRepoint descriptor {src, tgt, edge_type, attrs}
                # per repointed direct edge (sdk.py). No replay consumer
                # exists anywhere — on rebuild the transferred direct edge is
                # LOST (pass-2 only rebuilds operator-mediated edges from
                # snapshots; operator-less direct edges have no PointAdded
                # snapshot to carry them). Deferred to pass-2b (after all
                # operator edges exist) — descriptor replay is a MERGE of the
                # flat endpoints, order-independent by construction.
                direct_repoint_events.append(ev)
            elif t == "DocumentCreated":
                self._upsert_document(ev)
            elif t == "EntityLinked":
                # #3664: defer to the trailing sweep (see declaration), with
                # the journal seq the hard-delete staleness rule needs.
                entity_link_events.append((seq, ev))
            elif t == "SessionRecorded":
                # #3664: the :Session node must exist before any deferred
                # EntityLinked fold FROM it runs (the sweep below).
                self._fold_session_recorded(ev)
            elif t == "SourceCreated":
                # #330 parity with apply(): SourceCreated was dropped by rebuild.
                self._upsert_source(ev)
            elif t in _NO_PROJECTION_FOLD:
                # Recognized, intentionally not folded here — the audit-only
                # markers, the JSONL-only batch snapshot (replayed in pass
                # 2b) and the deliberately-deferred DirectEdgeCreated (A10
                # #1048). The warning below is reserved for a type outside
                # this vocabulary — see FalkorProjection.apply.
                pass
            else:
                # P2-1 (#3299): a record type outside the recognized
                # vocabulary must not be dropped silently.
                logger.warning("unrecognized event type %r — skipped", t)

        # ── Pass 1b fold sweep: ObjectSuperseded replays AFTER all object
        # creation events (see the branch above). Warn on 0-row folds — a
        # fold that matched nothing during rebuild means the journal claims
        # a supersession whose Object never re-existed. Since #2194,
        # capture-created Objects ARE journaled as ObjectRegistered when the
        # capture SDK is built with an event_log_path, so the residual
        # 0-row sources are: pre-#2194 journals (no backfill), an
        # unjournaled capture SDK / legacy raw producers (they apply without
        # journaling), and delete races (a deleted Object's registration
        # line — deletes leave no tombstone). The fold is idempotent — a
        # superseded Object that is later re-created by a future event is
        # caught on the NEXT rebuild, but this rebuild's graph is honest
        # about what it could not fold.
        for ev in supersede_folds:
            # #2242: replay folds run under the DEFAULT cas=False (blind) —
            # byte-identical to pre-CAS. The live-path CAS must not leak
            # into replay: first-wins replay would regress incarnation-reuse
            # shapes (delete→recreate→re-supersede resolves LAST-wins). The
            # tuple return: folded == 0 ≡ today's matched == 0 (cas=False
            # returns (matched, matched)).
            folded, _ = self._fold_object_superseded(ev)
            if folded == 0:
                logger.warning(
                    "rebuild: ObjectSuperseded fold matched no Object "
                    "(event_id=%s supersedes_by=%r) — object not "
                    "re-created by any journaled event (pre-#2194 journal, "
                    "unjournaled capture SDK, legacy unjournaled Object, or "
                    "delete race)",
                    ev.get("event_id"), ev.get("supersedes_by"))

        # #4743 review P1: undo the deferral's clobber, in journal order. The
        # inline state folds above were overwritten by `ObjectSuperseded` folds
        # the sweep applied UNCONDITIONALLY afterwards — correct only when the
        # supersede really came later. Replay every state op that the journal
        # puts AFTER the last supersede matching this object, in seq order, so
        # `rebuild_all` agrees with `apply()`/`rebuild()` and with live.
        #
        # A supersede is skipped outright when none matched (the `-1` default of
        # round 1 made "no supersede" indistinguishable from "supersede at seq
        # −1", so every state op was re-folded — harmless in the graph but it
        # double-reported every fold-miss warning).
        for (_ujm_label, _ujm_id), _ujm_events in _state_folds.items():
            if _ujm_label != "Object":
                # `_fold_object_superseded` is the only non-Point deferred fold
                # that writes a property a state op also writes; the point
                # sweeps below are :Point-scoped and no producer emits an
                # `EntityMutated` state op for a Point (`_update_entity`'s Point
                # branch emits PointRevised instead).
                continue
            _sup = _supersede_seq.get(("id", _ujm_id))
            _rows = self.g.query(
                "MATCH (o:Object {id:$i}) RETURN o.name",
                params={"i": _ujm_id},
            ).result_set
            _nm = _rows[0][0] if _rows else None
            if not _rows:
                # The object is GONE by this point in the replay — a journalled
                # `delete` op, or a supersede-then-delete journal. There is then
                # nothing for the sweep to have clobbered and nothing to
                # restore, so re-folding here can only MANUFACTURE a fold-miss
                # warning for a mutation the INLINE pass-1b fold already applied
                # correctly (round-6 finding: create -> supersede ->
                # update(status) -> delete warned "fold matched no entity ...
                # post-wipe divergence or out-of-order journal", both causes
                # false). The warning is this lane's non-folded-set EVIDENCE —
                # `_fold_entity_mutation` exempts `op="delete"` precisely so a
                # valid journal cannot produce a false positive — so it must not
                # be emitted for a replay that was in fact correct.
                continue
            if isinstance(_nm, str):
                _nseq = _supersede_seq.get(("name", _nm))
                if _nseq is not None and (_sup is None or _nseq > _sup):
                    _sup = _nseq
            if _sup is None:
                continue
            for _seq, _ev in _ujm_events:
                if _seq > _sup:
                    self._fold_entity_mutation(_ev)

        # ── Pass 1b fold sweep (points): cross-family re-stamp survivors ──
        # PointSuperseded replays (#2423 — status/validity/CORRECTS) +
        # PointInvalidated replays (#2488 — outdated/validity/CORRECTS, no
        # status) fold AFTER every point-creation event (PointAdded in pass
        # 1a + PointPromoted/PointRevised above) so a journaled re-creation
        # cannot resurrect the superseded/invalidated point. Warn on 0-row
        # folds (the journal claims a fold whose old Point never re-existed
        # — pre-#432 legacy journals, unjournaled producers, or delete
        # races). Folds are idempotent; the sweep mirrors the ObjectSuperseded
        # pattern above.
        #
        # Cross-family survivor rule (#2488, replaces the #2423 last_per_oid
        # dedup): per old_id, keep ONLY re-stamping folds whose journal seq
        # is AFTER the id's last PointAdded re-creation (last_recreate_seq,
        # pass-1a). A delete+recreate id-reuse wipes every pre-recreation
        # fold's stamps/CORRECTS live (they died with the deleted node) — a
        # raw producer reusing an id between two supersedes leaves TWO
        # PointSuperseded events for one old_id, and only the post-recreate
        # one is live-truth (acceptance c). A None anchor = "no re-creation
        # seen → keep all folds" (legacy journals, single-incarnation ids).
        #
        # Supersede-kind canonicalization (retained from #2423 last_per_oid):
        # a superseded point is status-TERMINAL and live supersede of a
        # terminal point raises — a second same-id PointSuperseded in one
        # journal (raw producer that did not journal its delete+recreate)
        # is never live-truth, so keep the LAST supersede survivor per old
        # id (the earlier fold's CORRECTS S1→A would ghost beside the final
        # S2→A). PointInvalidated folds ALL survive the id filter and are
        # NOT canonicalized — #2498: the SDK now REJECTS the repeat (the
        # outdated=true flag is terminal), but a raw/legacy producer can still
        # journal it, so every survivor folds (distinct corrected_by → 2
        # CORRECTS, acceptance b).
        # Chains A→B→C have distinct old ids — each link folds independently.
        supersede_last: dict[str, tuple[int, dict]] = {}
        invalidate_survivors: list[tuple[int, dict]] = []
        for fsq, ev in point_re_stamp_folds:
            anchor = last_recreate_seq.get(("Point", ev["id"]))
            if anchor is not None and fsq <= anchor:
                # Pre-re-creation fold — dropped (id-reuse survivor rule).
                continue
            if ev["type"] == "PointSuperseded":
                supersede_last[ev["id"]] = (fsq, ev)
            else:
                invalidate_survivors.append((fsq, ev))
        # Journal-append order (ascending event index). Do NOT sort by ts —
        # ts collides within the same ms and the JSONL carries no seq.
        point_sweep = sorted(
            [*supersede_last.values(), *invalidate_survivors],
            key=lambda pair: pair[0])
        for fsq, ev in point_sweep:
            if ev["type"] == "PointInvalidated":
                # #2488 updatedAt seq-gate (NOT clock comparison): pass-1a's
                # _upsert_point_props already stamped every replayed node
                # updatedAt=rebuild-now, and rebuild-now always postdates the
                # journaled invalidate ts — a `$ts >= n.updatedAt` CASE could
                # never fire. Unlike a superseded old (status terminal,
                # frozen), an invalidated point stays status='live' — a LATER
                # same-id PointRevised/PointPromoted (inline, stamped
                # rebuild-now in pass-1b) is a legitimate newer writer.
                # skip_updated_at fires when max_inline_seq[id] > this
                # invalidate's seq: the gate suppresses ONLY the updatedAt
                # column — outdated/validTo/expiredAt/CORRECTS fold always
                # (or a live-legal invalidate→PointRevised loses its outdated
                # flag and the ghost silently returns). Otherwise the fold is
                # the id's last journal writer and writes the journaled ts
                # UNCONDITIONALLY (exact live parity, supersede's precedent).
                later_inline = max_inline_seq.get(ev["id"])
                skip_ua = later_inline is not None and later_inline > fsq
                matched = self._fold_point_invalidated(
                    ev, skip_updated_at=skip_ua)
                if matched == 0:
                    logger.warning(
                        "rebuild: PointInvalidated fold matched no Point "
                        "(event_id=%s id=%r corrected_by=%r) — invalidated "
                        "point not re-created by any journaled event "
                        "(legacy journal, unjournaled producer, or delete "
                        "race)",
                        ev.get("event_id"), ev.get("id"),
                        ev.get("corrected_by"))
            else:
                matched = self._fold_point_superseded(ev)
                if matched == 0:
                    logger.warning(
                        "rebuild: PointSuperseded fold matched no Point "
                        "(event_id=%s old_id=%r new_id=%r) — superseded point "
                        "not re-created by any journaled event (legacy "
                        "journal, unjournaled producer, or delete race)",
                        ev.get("event_id"), ev.get("id"), ev.get("new_id"))
        # fold_seq[old_id] = journal seq of the id's surviving supersede fold
        # (pass-2b re-point discriminator; bound to the supersede-kind
        # survivor so a mixed invalidate→supersede never binds the invalidate
        # seq — invalidate transfers no edges).
        fold_seq: dict[str, int] = {
            ev["id"]: s for s, ev in supersede_last.values()}

        # Pass 1b tail: restore :Batch marker nodes AND the Point.batch_id
        # enforcement links from the pre-wipe snapshot (#990) — quarantine
        # locks survive rebuilds, and promote_point still sees them.
        for props in batch_snapshot:
            bid = props.get("id")
            if not bid:
                continue
            clean = {k: v for k, v in props.items() if k != "id"}
            self.g.query(
                "MERGE (b:Batch {id:$id}) SET b += $props",
                params={"id": bid, "props": clean},
            )
        for pid, bid in batch_point_links:
            self.g.query(
                "MATCH (p:Point {id:$pid}) SET p.batch_id = $bid",
                params={"pid": pid, "bid": bid},
            )

        # Pass 1b tail (#2943, #4305): re-apply the snapshot Point properties
        # the replay itself cannot rebuild. See _REPLAY_GAP_PROPS — this is what
        # keeps a graph-only Point that was invalidated (or hash-keyed) before
        # the wipe from coming back as a different node. Values are the
        # pre-wipe capture's, i.e. the state of the node this wipe destroyed.
        # An id the journal HARD-deleted is SKIPPED entirely (the
        # ``journal_deleted`` barrier below) — the snapshot describes the
        # incarnation that delete destroyed, so it must not come back.
        #
        # #4305: the derived restore is FIELD-GATED by what the journal replay
        # itself determined, instead of being re-applied unconditionally to
        # every synthetic id. Three pre-existing losses drove this:
        #   * a JOURNALED event that wrote (or explicitly cleared) a derived
        #     field OWNS it — the snapshot value is the OLDER writer and must
        #     not clobber it. Unconditional re-application was shape H (a
        #     `PointRevised` with no creating record: the synthetic #548
        #     `PointAdded`'s stale `content_hash` overwrote the revision's),
        #     `point_promoted`, and `revise_to_empty`.
        #   * an id whose journaled creation writes NO derived (falsy content,
        #     an operator payload, an unwritable revision) has no journal
        #     carrier at all — and when the id IS log-covered the #548
        #     generator emits no synthetic event for it, so the pre-wipe
        #     derived was lost entirely. Shapes G,
        #     `falsy_reemit_then_nul_revise`, `point_added_truthy_operator`.
        #   * an id the journal HARD-DELETED is a destroyed incarnation: the
        #     snapshot's derived belongs to it and must NOT be restored
        #     (`delete_then_falsy_recreate` — restoring it would be STRICTLY
        #     worse than the pre-fix behaviour, which is why the delete is a
        #     barrier rather than just another writer).
        # The source map is the pre-wipe `:Point` capture (so LOG-COVERED ids
        # are reachable), with the MERGED `synthetic_events` entries filling
        # only the fields the capture leaves ABSENT. The capture is the
        # PRIMARY source: it is the newer state, and for an id carried by BOTH
        # a leftover sidecar and a live node that has since become log-covered
        # there is no `_merge_entry` collision to resolve it — a GRAPH-ONLY live
        # node does get a fresh synthetic entry, which `_merge_entry` already
        # fresh-wins, but a log-covered one does not, so the pure-leftover entry
        # would otherwise beat the newer live value. The synthetic half is what
        # makes the #2943 sidecar-recovery
        # path work: there the live capture is a partial replay whose
        # `content_hash` the replay's CONDITIONAL writer left absent (falsy
        # content), so only the sidecar has it.
        restore_sources: dict[str, dict] = {
            pid: dict(props) for pid, props in existing_points.items()}
        synthetic_ids: set[str] = set()
        for ev in synthetic_events:
            sp = ev.get("point") if isinstance(ev, dict) else None
            if not (isinstance(sp, dict) and isinstance(sp.get("id"), str)):
                continue
            synthetic_ids.add(sp["id"])
            dst = restore_sources.get(sp["id"])
            if dst is None:
                restore_sources[sp["id"]] = dict(sp)
                continue
            for k, v in sp.items():
                if v is not None and dst.get(k) is None:
                    dst[k] = v
        restore_failures = 0
        for pid, sp in restore_sources.items():
            if pid in journal_deleted:
                continue
            gap: dict = {}
            if pid in synthetic_ids:
                # The non-derived _REPLAY_GAP_PROPS keep their ORIGINAL scope:
                # only a graph-only (synthetic) id. Widening them would let a
                # pre-wipe value overwrite a journaled invalidate/supersede
                # fold, which is not this change's subject.
                for k in _REPLAY_GAP_PROPS:
                    if k != "content_hash" and sp.get(k) is not None:
                        gap[k] = sp[k]
            if (sp.get("content_hash") is not None
                    and pid not in journal_hash_write):
                gap["content_hash"] = sp["content_hash"]
            emb = sp.get("embedding")
            # #4305 review P2: only a numeric vector is writable through
            # `vecf32()`. A corrupt/legacy store value (non-iterable, a string,
            # mixed types) must DEGRADE to "not restored" rather than raise
            # AFTER the wipe — the same recovery-path rule `_revise_point`
            # follows for an unusable vector (#19).
            restore_embedding = (
                emb is not None
                and pid not in journal_embed_write
                and isinstance(emb, (list, tuple))
                and all(isinstance(x, (int, float))
                        and not isinstance(x, bool) for x in emb)
            )
            if not gap and not restore_embedding:
                continue
            clauses: list[str] = []
            params: dict = {"pid": pid}
            if gap:
                clauses.append("n += $props")
                params["props"] = gap
            if restore_embedding:
                if sp.get("embedding_verbatim"):
                    # #5004 round-7: a CALLER-owned vector must keep its RAW
                    # form. `vecf32()` is the ONE form it must not take (it
                    # narrows a float64 list: `0.1` -> `0.10000000149011612`),
                    # and this path is the graph-only id's only restorer — the
                    # synthetic event deliberately carries neither the vector
                    # nor the marker. The marker is restored with it, or the
                    # property exists on live and not on replay (a #330/#3312
                    # break this change would otherwise introduce).
                    clauses.append("n.embedding = $emb")
                    clauses.append("n.embedding_verbatim = true")
                else:
                    # `vecf32()` cast exactly like `_upsert_point_props`: the
                    # HNSW index rejects a bare list, and a bare-list write
                    # would leave the restored vector unsearchable.
                    clauses.append("n.embedding = vecf32($emb)")
                params["emb"] = list(emb)
            try:
                self.g.query(
                    "MATCH (n:Point {id:$pid}) SET " + ", ".join(clauses),
                    params=params,
                )
            except Exception as e:
                # #4305 review P2: this runs AFTER the wipe, so a value the
                # engine/driver rejects must not strand the rebuilt graph
                # (#2943/#3689 recovery-path rule) — degrade to "not
                # restored" and leave the replayed value in place. A
                # SYSTEMATIC failure must not be indistinguishable from that
                # benign degradation, hence the count + single ERROR summary
                # after the loop.
                restore_failures += 1
                logger.warning(
                    "rebuild: snapshot derived restore for id %r failed "
                    "(%s: %s) — leaving the replayed value in place",
                    pid, type(e).__name__, e,
                )
        if restore_failures:
            logger.error(
                "rebuild: %d snapshot derived restore(s) FAILED — the "
                "pre-wipe derived of those id(s) was NOT restored. "
                "Re-derive the value from its source of truth (e.g. re-write "
                "the Point) if the indexed dedup key / vector matters. The "
                "graph is otherwise rebuilt; see the per-id warnings above",
                restore_failures,
            )

        # ── #3947 review: restore the :Session containers + their CONTAINS
        # edges from the pre-wipe snapshot ──
        # Same class as the :Batch marker above, and the same reason: the
        # capture Session is a RAW graph write that rides no journal record on
        # a pre-#3947 store, so the #548 Point snapshot (Points only) cannot
        # restore it. Without this, `rebuild_all` returned SUCCESS while
        # silently destroying the session container and every
        # `(:Session)-[:CONTAINS]->(:Point)` edge of exactly the population
        # #3947 is about — the false PASS this change exists to remove. Runs
        # after pass 1a (the Points exist) and BEFORE pass 2, so
        # `_link_session` (the later writer, reached via `_upsert_point_edges`)
        # is what re-asserts `is_episodic=true`; the two agree on that value,
        # so the ordering cannot clobber, and this block's `SET s += $props` is
        # what restores `capture_ok` / `turn_count` / `created_at`. The edge
        # MERGE is idempotent against a journaled `contains_session` replay.
        #
        # DURABILITY (review cycle 2 corrected): for `rebuild_all` the
        # `:Session` container and its CONTAINS links are NOT an in-memory-only
        # list. They are in `_SNAPSHOT_SECTIONS`, written to the durable
        # pre-wipe sidecar, and reassigned from `merged` before this block, so
        # a process death between the wipe and here IS recovered on the next
        # run (and by `recover_from_log`) — the sidecar-recovery test
        # exercises exactly that. The residual applies only to (a) a
        # journal-only `rebuild()` over a journal that carries NO
        # `SessionRecorded` (a pre-#3664 journal, or a hosted/journal-less
        # lane) — the `_link_session` stub is then the only Session state —
        # and (b) a sidecar written next to a log
        # dir the embedded opener cannot see — see the warning above the
        # sidecar write. For a post-#3664 journal the durable carrier is the
        # `SessionRecorded` record (folded by `_fold_session_recorded`), so a
        # journaled capture no longer depends on this snapshot.
        for props in session_snapshot:
            sid = props.get("id")
            if not sid:
                continue
            clean = {k: v for k, v in props.items() if k != "id"}
            self.g.query(
                "MERGE (s:Session {id:$id}) SET s += $props",
                params={"id": sid, "props": clean},
            )
        for sid, pid in session_point_links:
            self.g.query(
                "MATCH (s:Session {id:$sid}), (p:Point {id:$pid}) "
                "MERGE (s)-[:CONTAINS]->(p)",
                params={"sid": sid, "pid": pid},
            )

        # ── #4641: restore the onboarding state machine ─────────────────
        # Same class as the :Session / :Batch markers above, and the same
        # reason: `tortoise/onboarding/state.py`'s `:OnboardingState`,
        # `:OnboardingStep` and `COMPLETED_STEP` writes are RAW Cypher that
        # ride no journal record and that `projection` never re-derives. Runs
        # here — after pass 1b (the journaled `:Subject` nodes exist, so the
        # `onboards` edge has an endpoint) and before pass 2 (which does not
        # touch onboarding). Labels / edge types / ids come from the DOMAIN
        # module, never re-typed.
        #
        # The node write is `SET n += $props` (not replace): an unrelated live
        # property must not be dropped, and the captured properties are the
        # pre-wipe truth for the fields they carry.
        #
        # A failure here runs AFTER the wipe, so — like the `:Session` and
        # config restores — it must DEGRADE rather than raise (#2943: a
        # post-wipe raise leaves the store empty). Every failure is counted,
        # reported once below, and the post-restore check turns a resulting
        # gap into an ERROR.
        # `COMPLETED_STEP_EDGE` / `ONBOARDING_NODE_LABEL` /
        # `ONBOARDING_STEP_LABEL` / `ONBOARDS_EDGE` are bound by the PRE-wipe
        # import in the capture block above, so a rename fails before the
        # wipe rather than here (#4641 review round 6).
        onboarding_restore_failures = 0
        onboarding_restored_orgs: set[str] = set()
        for props in onboarding_snapshot:
            oid = props.get("org_id") if isinstance(props, dict) else None
            if not isinstance(oid, str):
                continue
            clean = {k: v for k, v in props.items() if k != "org_id"}
            try:
                self.g.query(
                    f"MERGE (n:{ONBOARDING_NODE_LABEL} {{org_id:$oid}}) "
                    "SET n += $props",
                    params={"oid": oid, "props": clean},
                )
            except Exception as e:
                onboarding_restore_failures += 1
                logger.warning(
                    "rebuild: onboarding-state restore for org %s failed "
                    "(%s: %s) — the pre-wipe state was NOT restored (#4641)",
                    oid, type(e).__name__, e,
                )
                continue
            onboarding_restored_orgs.add(oid)
            # The `onboards` edge is a RAW write too and nothing in the journal
            # carries it. It is DERIVED from the captured `org_subject_id`
            # (which rode the node entry), so no separate section is needed.
            # `MATCH` on BOTH endpoints — the anchor `:Subject` is journaled
            # (`sdk.create_subject` emits SubjectAdded) so replay re-creates
            # it; a MATCH means a genuinely absent Subject (a pre-#2194/#2295
            # or hosted write path that journals nothing) drops the edge
            # rather than minting an endpoint-less one. That drop raises NO
            # exception, so it is caught by the post-restore verification
            # below, which reads this edge and compares it to the captured
            # `(org_id, org_subject_id)` pairs (#4641).
            sid = props.get("org_subject_id")
            if isinstance(sid, str):
                try:
                    self.g.query(
                        f"MATCH (n:{ONBOARDING_NODE_LABEL} {{org_id:$oid}}), "
                        "(s:Subject {id:$sid}) "
                        f"MERGE (n)-[:{ONBOARDS_EDGE}]->(s)",
                        params={"oid": oid, "sid": sid},
                    )
                except Exception as e:
                    onboarding_restore_failures += 1
                    logger.warning(
                        "rebuild: onboarding `onboards` edge restore for "
                        "org %s (subject %s) failed (%s: %s) — the org "
                        "anchor link was NOT restored (#4641)",
                        oid, sid, type(e).__name__, e,
                    )
        for oid, step_id in onboarding_step_links:
            if oid not in onboarding_restored_orgs:
                # The node entry is absent or its restore failed: the edge's
                # endpoint does not exist. Counted (the post-restore check
                # reports the gap) rather than minting a property-less
                # `:OnboardingState` via MERGE, which would be a NEW incoherent
                # state the next rebuild would capture and persist.
                onboarding_restore_failures += 1
                continue
            try:
                self.g.query(
                    f"MATCH (n:{ONBOARDING_NODE_LABEL} {{org_id:$oid}}) "
                    f"MERGE (s:{ONBOARDING_STEP_LABEL} "
                    "{org_id:$oid, step_id:$step_id}) "
                    f"MERGE (n)-[:{COMPLETED_STEP_EDGE}]->(s)",
                    params={"oid": oid, "step_id": step_id},
                )
            except Exception as e:
                onboarding_restore_failures += 1
                logger.warning(
                    "rebuild: onboarding step edge restore for org %s step "
                    "%s failed (%s: %s) — the completed step was NOT "
                    "restored (#4641)",
                    oid, step_id, type(e).__name__, e,
                )
        # Post-restore verification: compare the REBUILT graph against the
        # captured set through the same reader the capture used, so the
        # comparison cannot drift from the capture's scope. Never raise (this
        # runs after the wipe). A failed verification READ is treated as a
        # mismatch — "could not confirm" must not read as "confirmed".
        #
        # Placement: this runs HERE, before pass 2, while the analogous config
        # verification runs after pass 2b. That is safe only because NO later
        # pass touches this class — the only bulk delete in `rebuild_all` is
        # the unconditional `MATCH (n) DETACH DELETE n` above, and every later
        # `DELETE` matches `(old:Point {id:$old})-[r]->(t)` (or the supersede
        # re-point sweep's `(op:Point)` edge), so its source endpoint is
        # always a `:Point` and it cannot match `:OnboardingState`,
        # `:OnboardingStep`, `onboards` or `COMPLETED_STEP`. A future pass-2
        # change that touched the class would silently green this check; move
        # it to the end if that happens.
        onboarding_expected_orgs = {
            e.get("org_id") for e in onboarding_snapshot
            if isinstance(e, dict) and isinstance(e.get("org_id"), str)}
        onboarding_expected_links = {
            (o, s) for o, s in onboarding_step_links}
        # The `onboards` edge is restored from each node entry's captured
        # `org_subject_id`, but the restore `MATCH`es BOTH endpoints — so when
        # the anchor `:Subject` is genuinely absent (a pre-#2194/#2295 or
        # hosted write path that journals nothing) the MATCH yields no rows,
        # the MERGE never runs, and NO exception is raised. Without this leg
        # the run would report a clean, complete restore while the edge was
        # destroyed — the silent partial restore this change exists to remove.
        onboarding_expected_onboards = {
            (e.get("org_id"), e.get("org_subject_id"))
            for e in onboarding_snapshot
            if isinstance(e, dict) and isinstance(e.get("org_id"), str)
            and isinstance(e.get("org_subject_id"), str)}
        onboarding_missing_orgs: set = set()
        onboarding_missing_links: set = set()
        onboarding_missing_onboards: set = set()
        onboarding_verified = True
        try:
            live_nodes, live_links = _capture_onboarding_snapshot(self.g)
            live_orgs = {e.get("org_id") for e in live_nodes
                         if isinstance(e, dict)}
            live_link_pairs = {(o, s) for o, s in live_links}
            live_onboards = {
                (r[0], r[1]) for r in self.g.query(
                    f"MATCH (n:{ONBOARDING_NODE_LABEL})"
                    f"-[:{ONBOARDS_EDGE}]->(s:Subject) "
                    "RETURN n.org_id, s.id").result_set}
            onboarding_missing_orgs = onboarding_expected_orgs - live_orgs
            onboarding_missing_links = (onboarding_expected_links
                                        - live_link_pairs)
            onboarding_missing_onboards = (onboarding_expected_onboards
                                           - live_onboards)
        except Exception as e:
            onboarding_verified = False
            logger.error(
                "rebuild: could not VERIFY the restored onboarding state "
                "(%s: %s) — treating it as not restored (#4641)",
                type(e).__name__, e,
            )
        # The pre-preservation UNKNOWN signal was computed PRE-wipe (see the
        # payload block: the flag rides this run's sidecar too). It is logged
        # here rather than there only because this is where the operator
        # reads the restore outcome.
        # ONE canonical gap count, returned to the callers so the projection,
        # `consistency.recover_from_log` and the CLI cannot drift apart.
        # Deliberately NOT `restore_failures + missing_*`: a failed restore
        # lands in BOTH (its node is absent), which reported a single
        # destroyed org as two gaps; and a restore that RAISED after the write
        # landed (a timeout) is a failure with nothing missing, which must not
        # read as loss.
        onboarding_missing_total = (len(onboarding_missing_orgs)
                                    + len(onboarding_missing_links)
                                    + len(onboarding_missing_onboards))
        onboarding_gap = onboarding_missing_total
        if not onboarding_verified:
            # "Could not confirm" is itself a gap, gated on the verification
            # result ALONE — never on the expected counts (#4641 review round
            # 11). Gating it on the expected sets made the reporting surfaces
            # disagree about ONE completed rebuild: the projection still
            # logged the UNVERIFIED ERROR and the CLI still printed its
            # UNVERIFIED line, while `onboarding_gap` stayed 0 — so
            # `consistency.recover_from_log` and both automatic-recovery
            # callers reported a clean success. A failed verification read
            # means the class was not confirmed intact. With nothing expected
            # that is a weak signal, which is why the expected-count nuance
            # belongs in the MESSAGE, not in the trigger.
            onboarding_gap = max(onboarding_gap, 1)
        if onboarding_unknown:
            onboarding_gap = max(onboarding_gap, 1)

        if onboarding_unknown:
            logger.error(
                "rebuild: the leftover pre-wipe snapshot at %s does not "
                "carry a usable onboarding record — it either predates "
                "onboarding preservation, carries only one of the two "
                "onboarding sections, or carries a state-UNKNOWN marker "
                "from an earlier interrupted rebuild — so whether the "
                "destroyed graph held any onboarding state CANNOT be "
                "determined. This is a state-UNKNOWN signal, not proof the "
                "state is absent. Re-run onboarding for any org whose "
                "onboarding state is uncertain (#4641).",
                snapshot_path,
            )
        if not onboarding_verified:
            # "Could not confirm" must not be reported as "confirmed" in
            # EITHER direction: the missing sets are still EMPTY here (the
            # verification read never ran, so nothing was observed absent), so
            # this branch must not print the expected denominators as "ABSENT"
            # and assert the data is gone. The `None` values for those counts
            # are produced by the return mapping below, not by the sets here.
            logger.error(
                "rebuild: onboarding-state post-restore verification "
                "COULD NOT RUN (%d restore failure(s)) — the rebuilt "
                "graph's onboarding state is UNVERIFIED: not confirmed "
                "intact, and NOT observed gone. Re-check the %d expected "
                "org state(s), %d expected step edge(s) and %d expected "
                "`onboards` anchor edge(s) before trusting them — "
                "see #4641.",
                onboarding_restore_failures,
                len(onboarding_expected_orgs),
                len(onboarding_expected_links),
                len(onboarding_expected_onboards),
            )
        elif onboarding_missing_total:
            # The MISSING SETS are the "gone" evidence — NOT the inflated
            # `onboarding_gap` (which is also raised by the UNKNOWN flag and by
            # an unverified read) and NOT `restore_failures`: a restore call
            # can raise while the server already applied the write (a timeout
            # or a connection blip), and calling that "gone" would be a false
            # loss claim over intact state (see the `elif` below). Gating on
            # the missing sets also keeps the UNKNOWN-only shape from being
            # re-described here as observed loss (#4641 review round 7).
            logger.error(
                "rebuild: onboarding-state post-restore verification "
                "FAILED — %d of %d expected org state(s), %d of %d expected "
                "step edge(s) and %d of %d expected `onboards` anchor "
                "edge(s) are ABSENT from the rebuilt graph (%d restore "
                "attempt(s) raised). This is a TRUE POSITIVE, not a silent "
                "success: the wipe is unconditional and only the journal is "
                "replayed, so those onboarding states/edges are gone. "
                "Re-run onboarding for the affected org(s) — see #4641.",
                len(onboarding_missing_orgs),
                len(onboarding_expected_orgs),
                len(onboarding_missing_links),
                len(onboarding_expected_links),
                len(onboarding_missing_onboards),
                len(onboarding_expected_onboards),
                onboarding_restore_failures,
            )
        elif onboarding_restore_failures:
            logger.warning(
                "rebuild: %d onboarding restore attempt(s) raised, but the "
                "post-restore verification confirms every expected org state "
                "and edge is PRESENT — treated as transient (an error after "
                "the write landed), not as loss (#4641).",
                onboarding_restore_failures,
            )

        # ── #2814: restore the authoritative configuration ──────────────
        # After pass-1a (so a `:PackInstall` is not clobbered by a later replay
        # write) and before pass 2, alongside the other sidecar-borne graph
        # state. Cypher is assembled from REGISTRY LITERALS only (the label and
        # the identity property, both vetted by `_assert_config_registry_safe`)
        # with the identity VALUE and the property map `$`-bound — so a planted
        # sidecar can never inject Cypher through either.
        #
        # `SET n += $props` rather than replacing the node: an unrelated live
        # property must not be dropped, and the captured properties are the
        # pre-wipe truth for the fields they carry.
        #
        # A failure here runs AFTER the wipe, so — like the derived-restore loop
        # above (#4305) — it must DEGRADE rather than raise: a post-wipe raise
        # would leave the store empty (#2943 "No loss without proof"). Every
        # failure is counted and surfaced once, and the post-restore check below
        # turns the resulting gap into the marker.
        config_restore_failures = 0
        # Populate the label→spec map before either the restore or the T1 check
        # reads it. The capture path fills it as a side effect, but the T2 path
        # stages a marker entry WITHOUT a capture, and an empty map would make
        # both the restore loop and the verification comprehension skip every
        # entry — a vacuous pass that would also swallow the staged marker.
        _config_classes()
        for entry in config_snapshot:
            spec = (_CONFIG_CLASS_BY_LABEL.get(entry.get("label"))
                    if isinstance(entry, dict) else None)
            props = entry.get("props") if isinstance(entry, dict) else None
            if spec is None or not isinstance(props, dict):
                continue
            identity = props.get(spec.identity_prop)
            if not isinstance(identity, str):
                continue
            identity_prop = spec.identity_prop
            try:
                self.g.query(
                    f"MERGE (n:{spec.label} "
                    f"{{{identity_prop}:$identity}}) SET n += $props",
                    params={"identity": identity,
                            "props": {k: v for k, v in props.items()
                                      if k != identity_prop}},
                )
            except Exception as e:
                config_restore_failures += 1
                logger.warning(
                    "rebuild: config restore for %s %s=%r failed (%s: %s) — "
                    "the pre-wipe value was NOT restored",
                    spec.label, identity_prop, identity, type(e).__name__, e,
                )
                continue
            # (No `restored_config` bookkeeping: "the write did not raise" is
            # not the same claim as "the identity is present", and T1 below
            # verifies against the GRAPH. Tracking both would leave a second,
            # weaker source of truth that a future change could mistake for
            # load-bearing.)
        if config_restore_failures:
            logger.error(
                "rebuild: %d config restore(s) FAILED — the pre-wipe "
                "configuration of those identities was NOT restored. "
                "Re-provision it (see the rebuild runbook) and check the "
                "per-entry warnings above; see the config_reset marker for "
                "what is known missing (#2814)",
                config_restore_failures,
            )

        # Pass 2: create edges for all operators + provenance/entity wiring
        # (shared _upsert_point_edges — single source of truth with apply, #330).
        # Journal-order maps for the pass-2b re-point (order-faithful
        # trailing sweep): operator_created_seq[op_id] = index of the
        # operator's OperatorAdded event in the journal; fold_seq[old_id] =
        # index of the surviving PointSuperseded event (built by the pass-1b
        # sweep above — the raw enumerate-time fold_seq was deleted in #2488:
        # pass-2b must bind to the pre-filtered supersede-kind SURVIVOR, or a
        # pre-recreate supersede dropped by the survivor filter leaves a raw
        # entry whose .get(oid) fold_seq=None — the `op_seq > fseq` guard
        # would silently disable → ghost re-point of the fresh incarnation).
        # An operator whose creation PREDATES the supersede is the
        # live-transferred set; one created AFTER it legitimately keeps its
        # terminal link (create_operator has no terminal guard) — the
        # re-point must skip those. Live operators carry NO createdAt
        # (probe: None), and rebuild stamps createdAt = rebuild-time via
        # _upsert_point_props, so timestamp comparison is unreliable — the
        # journal SEQUENCE is the faithful discriminator.
        operator_created_seq: dict[str, int] = {}
        for seq, raw_ev in enumerate(events):
            # #3947 review: the capture directive lives on the RAW envelope —
            # `_norm` splices `ev["point"]` over it, so a point prop named
            # `contains_session` could shadow (and forge on replay) the link.
            # Read it before normalising, exactly as `apply` does.
            raw_contains_session = (
                raw_ev.get("contains_session")
                if isinstance(raw_ev, dict) else None)
            ev = self._norm(raw_ev)
            if ev.get("type") in ("PointAdded", "OperatorAdded"):
                # #331 (review r3): ev.get — missing 'point' key handled by
                # the isinstance guard, not KeyError.
                p = ev.get("point")
                if not isinstance(p, dict):
                    # Malformed event (non-dict/missing point) — skip (#325)
                    continue
                # #331 (review r3): parity with apply()/pass 1a — edge
                # wiring indexes by p["id"]; skip rather than KeyError.
                # #331 (review r4): str-only ids.
                if not isinstance(p.get("id"), str):
                    logger.warning(
                        "rebuild: skipping edge wiring for event with "
                        "missing point id (event_id=%s)",
                        ev.get("event_id"))
                    continue
                if ev.get("projection_version", 0) >= 2:
                    p.pop("context", None)
                if ((ev.get("type") == "OperatorAdded"
                        or (ev.get("type") == "PointAdded"
                            and isinstance(p.get("operator"), dict)))
                        and isinstance(p.get("id"), str)):
                    # Only the creation event is the creation-order signal;
                    # a re-PointAdded of the same id (upsert) must not
                    # overwrite the operator's ORIGINAL creation position.
                    # PointAdded-carried operators (capture/raw producers
                    # that journal the operator in a point snapshot, not an
                    # OperatorAdded) must ALSO be recorded — otherwise the
                    # pass-2b order-faithful discriminator treats them as
                    # unsequenced (None → always re-point), silently
                    # disabling the guard for that class (review P2-1).
                    operator_created_seq.setdefault(p["id"], seq)
                self._upsert_point_edges(p, contains_session=raw_contains_session)

        # ── Pass 2 entity-link sweep (#3664) ──────────────────────────────
        # Fold each deferred EntityLinked record into its idempotent about*
        # edge. Placed HERE — after pass 2 recreated every `:Session`
        # container (`_upsert_point_edges(contains_session=…)` →
        # `_link_session`), and after the pre-wipe snapshot restore above —
        # and BEFORE pass 2b's create-before-transfer sweep (which the
        # DirectEdgeRepoint structural leg relies on: it re-points/deletes
        # `about*` edges whose base edge must already exist). Folding at the
        # end of pass 1b matched only the TARGET endpoint: a `:Session`
        # source that exists solely because a PointAdded carried
        # `contains_session` was not yet created there, so the journaled
        # `(Session)-[:aboutObject]->(Object)` edge was silently lost.
        #
        # A 0-row fold means EITHER endpoint was never re-created by any
        # journaled event (pre-#2194 journal, unjournaled producer, delete
        # race) — honest: the journal could not reproduce that attachment.
        # It is NOT a target-only condition: `_fold_entity_linked` MATCHes
        # BOTH endpoints, so a missing SESSION/POINT SOURCE — the exact class
        # this sweep move exists for — is the same 0-row outcome. No warning
        # for it: unlike a supersession fold-miss (which means a claim of
        # state was lost), an absent link endpoint is simply an absent
        # entity. A MALFORMED record is different and DOES warn (inside the
        # fold). A stale link suppressed by the hard-delete rule is a silent
        # skip, counted as dropped and never applied (see
        # `fold_deferred_entity_links`).
        self.fold_deferred_entity_links(
            entity_link_events, journal_hard_delete_seqs(events))

        # Pass 2b (#2423): PointSuperseded EDGE re-point replay +
        # DirectEdgeRepoint descriptor replay — AFTER pass-2 rebuilt operator
        # edges from operator PointAdded snapshots whose stored
        # operator.inputs STILL NAME THE OLD point (live supersede's transfer
        # is CREATE+DELETE graph mutation only — operator.inputs is never
        # updated and no operator snapshot is re-emitted), so every
        # transferred operator edge re-materialized at the OLD point (the
        # edge-topology half of the #2423 resurrection).
        #
        # Operator edges: re-point old → final-live-successor mirroring the
        # live 2a transfer semantics (sdk.supersede_point). Supersede chains
        # A→B→C are resolved TRANSITIVELY (each superseded point's edges end
        # on the final live point, not an intermediate terminal one) — the
        # #2249 order-independence contract. alreadyDecided operators stay
        # attached to the superseded prior (the #1080 dedup-context carve-out
        # live supersede honors). An operator whose creation PREDATES the
        # supersede is the live-transferred set — an operator created AFTER a
        # supersede legitimately keeps its terminal link (create_operator has
        # no terminal guard), so the re-point compares createdAt vs the
        # supersede's ts (order-faithful trailing sweep).
        #
        # Direct edges: DirectEdgeRepoint descriptors (supersede's 2a-DIRECT
        # journal, flat {src, tgt, edge_type, attrs}) get their replay
        # consumer — the transferred direct edge is re-created at its FINAL
        # (transitively-resolved) endpoints with its attrs. Direct-edge base
        # creation (DirectEdgeCreated) replay stays deferred (A10 #1048 —
        # plain direct edges remain lost on rebuild); the supersede-transfer
        # descriptors ARE in scope here (E2E-11.6: the successor holds the
        # transferred direct edge post-rebuild).
        # #2488: succ/repoint/fold_seq bind to the supersede-kind SURVIVOR
        # subset (supersede_last — post-recreate, last-per-id) of the one
        # pre-filtered deferred list. A PointInvalidated survivor is NOT a
        # re-point source (invalidate transfers no edges); binding a mixed
        # invalidate→supersede to the invalidate seq would skip legit re-points.
        #
        # #2489 STRUCTURAL replay: supersede's 2b transfer (structural edges —
        # extractedFrom + about*) journals the SAME flat descriptor type for
        # the snapshot-derivable rel set. The pass-2b branch below routes on
        # edge_type ∈ the derivable set BEFORE the Point→Point direct-leg
        # consumer: a structural descriptor's tgt is a bare ENTITY KEY (or,
        # delete_only, the direct successor's LOGICAL id) — running it through
        # _final(tgt) + (b:Point{id:key}) would corrupt on a superseded-
        # point-id∩key collision or silently drop. Pass-ordering constraint
        # (pinned): this branch's correctness depends on pass-2's snapshot
        # resurrection (entities.py _upsert_point_edges — name-based, NO
        # status filter) having re-created the transferred edge at OLD BEFORE
        # descriptor replay; that holds by pass structure (pass-2 resurrection
        # precedes the pass-2b sweep). If a future phase reorder moves snapshot
        # resurrection later, the delete-leg must tolerate late resurrection.
        if supersede_last or direct_repoint_events:
            from tortoise.security import validate_rel_type

            from .edges import (
                DERIVABLE_STRUCTURAL_RELS,
                STRUCTURAL_REL_LABELS,
                resolve_structural_target,
            )
            # Successor map old→new; resolve transitively to the final live
            # point (chain-safe, cycle-guarded).
            succ: dict[str, str] = {}
            for _, ev in supersede_last.values():
                oid, nid = ev.get("id"), ev.get("new_id")
                if isinstance(oid, str) and isinstance(nid, str):
                    succ[oid] = nid

            def _final(point_id: str) -> str:
                seen: set[str] = set()
                while point_id in succ and point_id not in seen:
                    seen.add(point_id)
                    point_id = succ[point_id]
                return point_id

            if supersede_last:
                # Collect per-fold operator-edge rows + validate rel types
                # BEFORE any mutation (#329 collect-then-mutate pattern).
                # Order-faithful discriminator: an operator whose
                # OperatorAdded seq PREDATES the PointSuperseded seq is the
                # live-transferred set (its edge existed at supersede time);
                # an operator created AFTER the supersede (seq > fold_seq)
                # legitimately keeps its terminal link (create_operator has
                # no terminal guard) — skip. Re-point targets the FINAL
                # live successor (transitive chain resolution), so folds
                # stay order-independent (#2249).
                repoints: list[tuple] = []  # (op_id, rel_type, idx, rid, old_id, new_id, op_label)
                for _, ev in supersede_last.values():
                    oid = ev.get("id")
                    if not isinstance(oid, str):
                        continue
                    final_id = _final(oid)
                    if final_id == oid:
                        continue
                    fseq = fold_seq.get(oid)
                    rows = self.g.query(
                        "MATCH (op:Point {is_operator:true})-[r]->(old:Point {id:$id}) "
                        "RETURN op.id, type(r), r.idx, ID(r), op.label",
                        params={"id": oid},
                    ).result_set
                    for row in rows:
                        # r.idx can be null (legacy edge) — Cypher handles a
                        # null idx in the pattern; validate ONLY the rel type.
                        validate_rel_type(row[1])
                        op_seq = operator_created_seq.get(row[0])
                        if op_seq is not None and fseq is not None \
                                and op_seq > fseq:
                            continue  # operator postdates the supersede
                        repoints.append(
                            (row[0], row[1], row[2], row[3], oid, final_id,
                             row[4]))
                repointed_seen: set[tuple] = set()
                for (op_id, rtype, idx, rid, oid, nid, op_label) in repoints:
                    # alreadyDecided ops keep their dedup-context edge on the
                    # superseded prior (#1080) — mirror live's 2a skip.
                    if op_label == "alreadyDecided":
                        continue
                    # #2423 review P2-3 (id-reuse double-supersede): the same
                    # pass-2 (op)-[type{idx}]->(old) edge can be collected by
                    # TWO folds when a raw producer deletes + re-creates a
                    # point with the SAME id between supersedes (both folds
                    # resolve old→the same final successor). CREATE would mint
                    # a parallel duplicate edge (double EP weight) — dedupe on
                    # the exact (op, type, idx, final) key.
                    key = (op_id, rtype, idx, nid)
                    if key in repointed_seen:
                        continue
                    repointed_seen.add(key)
                    self.g.query(
                        f"MATCH (op:Point {{id:$op_id}}), (new:Point {{id:$nid}}) "
                        f"CREATE (op)-[:{rtype} {{idx:$idx}}]->(new)",
                        params={"op_id": op_id, "nid": nid, "idx": idx},
                    )
                    self.g.query(
                        f"MATCH (op:Point {{id:$op_id}})-[r:{rtype} {{idx:$idx}}]->"
                        f"(old:Point {{id:$oid}}) WHERE ID(r)=$rid DELETE r",
                        params={"op_id": op_id, "oid": oid, "idx": idx,
                                "rid": rid},
                    )

            if direct_repoint_events:
                # #2489: per-rebuild seen-set for STRUCTURAL descriptor dedupe
                # (below) — keyed on (src, edge_type, RESOLVED target node
                # identity), mirroring the operator leg's (op, type, idx,
                # final). NOT descriptor id (the emission id base
                # f"{old}->{new}:{rel}" duplicates across multi-target same-rel;
                # id-keyed dedupe would collapse 2 distinct same-rel targets =
                # the exact bug class) and NOT the resolved key alone (two
                # legit transfers to a SHARED node in one journal — X1→S, X2→S
                # — must not collapse). Objects/Events/Documents are id-keyed
                # entities: two distinct same-name targets must not collapse.
                structural_seen: set[tuple] = set()

                def _src_is_terminal(sid: str) -> bool:
                    """#2489 (code-review P1): the delete-leg must ONLY clean
                    the pass-2 resurrection at a DEAD (terminal) old point.
                    Under id-reuse (#2488 survivor filter drops a pre-recreation
                    supersede fold), src is a re-created LIVE incarnation whose
                    edges are legitimately its own — deleting them clobbers a
                    live point. Terminal src = the superseded/retracted/outdated
                    incarnation whose transferred edges pass-2 resurrected."""
                    rows = self.g.query(
                        f"MATCH (x:Point {{id:$sid}}) "
                        f"WHERE {_terminal_excluded('x.status')} RETURN 1 LIMIT 1",
                        params={"sid": sid}).result_set
                    # _terminal_excluded is the NOT-terminal predicate: empty
                    # rows → the node is terminal (or absent). Absent src =
                    # nothing to clean — treat as terminal (delete is a no-op).
                    return not rows
                for ev in direct_repoint_events:
                    etype = ev.get("edge_type")
                    src, tgt = ev.get("src"), ev.get("tgt")

                    # ── #2489 delete_only structural descriptor (no-self-edge
                    # guard journal, Task 1 step 4) ── handled BEFORE the
                    # malformed-isinstance guard: the event carries a literal
                    # LOGICAL tgt id (the direct successor), so it would survive
                    # the isinstance check — but recognition must be explicit.
                    # Delete-leg keys the DIRECT successor (the descriptor's
                    # literal logical tgt), NOT _final(src): in a chain (guard
                    # fires supersede(X→Y), then Y→Z is superseded before
                    # rebuild), pass-2 resurrects the phantom at old from src's
                    # snapshot to the DIRECT successor Y while _final(X)=Z — a
                    # final-keyed delete misses and old X keeps its phantom.
                    # Resurrection is name-based (no status filter), so in a
                    # chain the phantom may attach to dead Y while a succ-
                    # resolved delete targets Z — delete BOTH the literal node
                    # and the succ-resolved node when Y was itself superseded
                    # (follow _final(Y); the direct node may be gone). Skip
                    # create (the transfer target IS the successor — creating
                    # would mint a self-edge).
                    if ev.get("delete_only") is True:
                        if (not isinstance(src, str) or not isinstance(tgt, str)
                                or not isinstance(etype, str)
                                or etype not in DERIVABLE_STRUCTURAL_RELS):
                            logger.warning(
                                "rebuild: malformed delete_only "
                                "DirectEdgeRepoint event skipped "
                                "(event_id=%s)", ev.get("event_id"))
                            continue
                        if not _src_is_terminal(src):
                            # code-review P1: src is a LIVE re-created
                            # incarnation (id-reuse) — its edges are legit;
                            # the delete-leg must not clobber. Skip the whole
                            # descriptor (create-skip is implied: a live src
                            # has no surviving supersede fold).
                            continue
                        for tgt_logical in {tgt, _final(tgt) if tgt in succ else None}:
                            if not tgt_logical:
                                continue
                            rows = self.g.query(
                                "MATCH (t:Point {id:$id}) RETURN ID(t) LIMIT 1",
                                params={"id": tgt_logical}).result_set
                            if not rows:
                                continue  # direct node gone — constrained no-op
                            # Resurrection-delete with FRESH ID capture, rel-
                            # constrained to (old:Point{id:src}) — never a
                            # graph-wide pattern delete, never a descriptor-
                            # carried rid (internal ids die at rebuild).
                            self.g.query(
                                f"MATCH (old:Point {{id:$old}})-[r:{etype}]->(t) "
                                f"WHERE ID(t) = $tid DELETE r",
                                params={"old": src, "tid": rows[0][0]})
                        continue

                    # ── #2489 normal STRUCTURAL descriptor ── route on
                    # edge_type ∈ the derivable set BEFORE the direct-leg
                    # consumer's validate_rel_type / Point→Point MERGE /
                    # _final(tgt). The fixed-set discriminator is an ALLOWLIST,
                    # not a validation substitute — validate_rel_type still
                    # runs inside the branch (single validation point).
                    if (isinstance(etype, str) and etype in DERIVABLE_STRUCTURAL_RELS
                            and isinstance(src, str) and isinstance(tgt, str)):
                        validate_rel_type(etype)
                        # Resolve tgt via the SHARED label-scoped resolver —
                        # never auto-detect _create_about_edges (Subject-first
                        # auto-detect would attach an aboutObject descriptor to
                        # a same-name Subject node; the delete-leg's rel-
                        # constrained re-query would then miss the drifted
                        # resurrection and old keeps a phantom). Create-if-
                        # missing: Subject/Source stubs MERGE by key (live
                        # wiring parity — one create path, edges.py); never
                        # mint Point stubs by name (absent Point target ⇒ skip).
                        resolved = resolve_structural_target(
                            self.g, STRUCTURAL_REL_LABELS[etype], tgt, etype)
                        if resolved is None:
                            # Target absent at replay (non-stubbable entity from
                            # an unjournaled producer): pass-2's resurrection
                            # could not have attached a same-rel edge either
                            # (name/url-based match), so nothing to clean or
                            # re-create.
                            continue
                        # Delete-leg (old-side resurrection cleanup) runs BEFORE
                        # the dedupe skip — a dropped duplicate still cleans its
                        # old-side resurrection. Constrained re-query on the
                        # resolved node identity; fresh internal ids only. P1:
                        # only when src is TERMINAL at replay — a live re-created
                        # src (id-reuse) owns its edges legitimately.
                        if not _src_is_terminal(src):
                            continue
                        self.g.query(
                            f"MATCH (old:Point {{id:$old}})-[r:{etype}]->(t) "
                            f"WHERE ID(t) = $tid DELETE r",
                            params={"old": src, "tid": resolved["internal"]})
                        # #2489 step 6b (2nd-model P1): structural descriptors
                        # must obey #2488's survivor-filtered succ map — once
                        # #2488 lands (pre-recreation supersede folds dropped),
                        # _final(src) below resolves through the survivor-only
                        # map and a dropped fold's descriptor self-skips via the
                        # src_f == src guard (its delete-leg already ran above).
                        # Base (pre-#2488) has no survivor filter — parity with
                        # the operator/direct legs' existing replay.
                        key = (src, etype, resolved["internal"])
                        if key in structural_seen:
                            continue
                        structural_seen.add(key)
                        src_f = _final(src)
                        if src_f == src:
                            # No surviving supersede fold for src (dropped by a
                            # future #2488 survivor filter / defensive) — never
                            # re-create the edge at old after the delete-leg.
                            continue
                        if resolved.get("logical") and resolved["logical"] == src_f:
                            # aboutPoint whose target IS the final successor
                            # (chain collapse X→Y→Z with target Z): the create
                            # would mint (succ)-[:aboutPoint]->(succ) — phantom
                            # self-edge. Never recreate (E2E-11.6 no-self
                            # contract); the delete-leg already cleaned the
                            # resurrection at old.
                            continue
                        # Two-step MERGE on the resolved node identity — collapses
                        # duplicate creates to one edge (about* / extractedFrom
                        # are MERGE-created live). Structural 2b transfers are
                        # bare: NO attr SET, no direction/confidence/weight.
                        self.g.query(
                            f"MATCH (s:Point {{id:$sid}}), (t) WHERE ID(t) = $tid "
                            f"MERGE (s)-[:{etype}]->(t)",
                            params={"sid": src_f, "tid": resolved["internal"]})
                        continue

                    if (not isinstance(src, str) or not isinstance(tgt, str)
                            or not isinstance(etype, str)):
                        logger.warning(
                            "rebuild: malformed DirectEdgeRepoint event skipped "
                            "(event_id=%s)", ev.get("event_id"))
                        continue
                    validate_rel_type(etype)
                    src_f, tgt_f = _final(src), _final(tgt)
                    if src_f == tgt_f:
                        # Phantom self-edge: live supersede's guard deletes an
                        # edge whose other endpoint IS the successor (tid==new)
                        # WITHOUT a descriptor; a chain A'→A'' can collapse an
                        # earlier descriptor's endpoints onto each other. Never
                        # recreate a self-edge (E2E-11.6 no-self contract).
                        continue
                    # Two-step MERGE (mirror create_direct_edge / supersede
                    # 2a-DIRECT — never creates duplicate nodes; collapses to
                    # one edge) then last-writer-wins attr SET.
                    self.g.query(
                        f"MATCH (a:Point {{id:$a}}), (b:Point {{id:$b}}) "
                        f"MERGE (a)-[:{etype}]->(b)",
                        params={"a": src_f, "b": tgt_f},
                    )
                    attrs = {k: ev[k] for k in
                             ("direction", "confidence", "weight",
                              "label", "batch_id") if k in ev}
                    if attrs:
                        self.g.query(
                            f"MATCH (a:Point {{id:$a}})-[r:{etype}]->"
                            f"(b:Point {{id:$b}}) SET r += $attrs",
                            params={"a": src_f, "b": tgt_f, "attrs": attrs},
                        )

        # #2943: replay completed and the graph now holds everything the
        # sidecar recorded — drop it BEFORE the count queries (a timeout there
        # must not leave a sidecar that the next rebuild would re-merge). A
        # failure above leaves it in place deliberately: the graph may be
        # partially wiped, and the sidecar is the rescue data.
        # #4305 review round 4: do NOT retain the sidecar on a restore failure.
        # The artifact is a single graph-wide blob, so keeping it would re-merge
        # pre-wipe truth for EVERY id it carries — including ids whose restore
        # succeeded — and a later raw delete of any of them would be resurrected
        # on every subsequent rebuild (never retiring for a permanently
        # unwritable value). The failure is surfaced by the ERROR summary in the
        # tail instead.
        # ── #2814 T1: verify the restore, then record the third state ────
        # The pre-wipe proof (above) is "every captured entry is valid and
        # restorable"; this is the POST-restore check that it actually came
        # back. Read the graph through the SAME registry query the capture
        # used, so the comparison cannot drift from the capture's scope
        # (registry literals only — no sidecar-derived Cypher here either).
        #
        # Never raise: this runs after the wipe, and a raise would leave the
        # store empty (#2943 "No loss without proof"). A mismatch is reported
        # as ERROR + the sticky marker instead. A FAILED verification read is
        # treated as a mismatch — "could not confirm" must not read as
        # "confirmed", which is the whole point of the third state.
        config_expected_set = {
            _config_key(entry) for entry in config_snapshot
            if isinstance(entry, dict)
            and entry.get("label") in _CONFIG_CLASS_BY_LABEL
            # The marker T2 STAGED is a statement about unprovability, not
            # captured configuration: counting it would report `N+1 of N+1`
            # for a graph whose real config is unknown. It is still part of the
            # section (so it is persisted and restored) — it just must not
            # inflate the counts the operator reads.
            and entry is not config_unknown_staged
        }
        # The staged marker is out of the COUNTS above but must stay inside the
        # VERIFICATION. If its own restore write failed, the graph is
        # state-UNKNOWN with no marker on it, and `config_reset` (built from
        # the read below) would say False — a clean "never configured" for an
        # unknown state, which is the false "restored" this whole state exists
        # to prevent. Verify it, so the incident is re-recorded with
        # reason='restore_incomplete' rather than disappearing.
        config_verify_set = set(config_expected_set)
        if config_unknown_staged is not None:
            config_verify_set.add(_config_key(config_unknown_staged))
        config_verified = True
        config_missing: set = set()
        try:
            live_config = {_config_key(entry)
                           for entry in _capture_config_snapshot(self.g)}
            config_missing = config_verify_set - live_config
        except Exception as e:
            config_verified = False
            logger.error(
                "rebuild: could not VERIFY the restored configuration "
                "(%s: %s) — treating it as not restored and recording the "
                "`config_reset` marker; the graph is rebuilt but its config "
                "state is unproven (#2814)",
                type(e).__name__, e,
            )
        if config_verify_set and (not config_verified or config_missing):
            logger.error(
                "rebuild: post-restore verification FAILED — %d of %d "
                "expected config identit(ies) are ABSENT from the rebuilt "
                "graph%s (the `config_reset` marker counts here when one was "
                "staged, but never in the reported counts). This is a TRUE "
                "POSITIVE, not a silent success: the pre-wipe configuration "
                "of those identities is gone (the wipe is unconditional and "
                "only the journal is replayed). Recording the sticky "
                "`config_reset` marker with reason='restore_incomplete' — "
                "re-provision the configuration, then clear the marker "
                "(#2814)",
                len(config_missing) if config_verified else len(config_verify_set),
                len(config_verify_set),
                f" ({sorted(config_missing)})" if config_missing else "",
            )
            try:
                set_config_reset_marker(self.g, "restore_incomplete")
            except Exception as e:
                logger.error(
                    "rebuild: could not write the `config_reset` marker "
                    "(%s: %s) — the mismatch above is recorded ONLY in the "
                    "log, so re-run `tortoise rebuild` to re-attempt the "
                    "restore from the still-pending sidecar (#2814)",
                    type(e).__name__, e,
                )
        config_reset_read_failed = False
        try:
            config_reset_marker = read_config_reset(self.g)
        except Exception as e:
            logger.warning(
                "rebuild: could not read the `config_reset` marker (%s: %s)",
                type(e).__name__, e,
            )
            config_reset_marker = None
            # Fail-SAFE, not fail-open: `None` must not mean both "never set"
            # and "could not be read". Reporting `config_reset: false` on an
            # unreadable marker would tell the operator the config is fine on
            # the one path that cannot check.
            config_reset_read_failed = True
        if snapshot_pending:
            _clear_prewipe_snapshot(snapshot_path)
        node_count = self.g.query(
            "MATCH (n:Point) RETURN count(n)"
        ).result_set[0][0]
        edge_count = self.g.query(
            "MATCH ()-[r]->() RETURN count(r)"
        ).result_set[0][0]
        # #3947: no post-wipe assertion — a failure here would leave the
        # store EMPTY (#2943 "No loss without proof"). The pre-wipe proof
        # above is what makes the returned counts trustworthy: reaching this
        # line means every pre-wipe episodic Point was recreatable.
        return {"events": len(events), "nodes": node_count, "edges": edge_count,
                # #2814: additive. `config_expected`/`config_restored` are
                # COUNTS of (label, identity) pairs (the staged marker is in
                # neither); `config_reset` is a BOOL — named distinctly from
                # `:Meta{key:'config_reset'}` itself so one string does not
                # carry two meanings. It is fail-SAFE: an UNREADABLE marker
                # also reports True, because `None` must not mean both "never
                # set" and "could not be read". `config_reset_read_failed`
                # therefore distinguishes the two for callers that must not
                # assert the marker IS present — the CLI's warning says the
                # state is unproven rather than naming a marker it could not
                # see, and `_clear_config_reset()` re-reads unguarded, so
                # "clear the marker" needs the read to work.
                "config_expected": len(config_expected_set),
                "config_restored": len(config_expected_set - config_missing)
                if config_verified else 0,
                "config_reset": (config_reset_marker is not None
                                 or config_reset_read_failed),
                "config_reset_read_failed": config_reset_read_failed,
                # #4641: additive, and the caller-visible half of the
                # post-restore verification. The onboarding restore's only
                # other signal is an ERROR line, and the pending sidecar is
                # retired afterwards (deliberately — #4305: retaining the
                # single graph-wide blob would re-merge pre-wipe truth for
                # EVERY id and resurrect post-wipe deletes), so without these
                # counts a programmatic caller (`consistency.recover_from_log`)
                # would report a success-shaped result over a destroyed
                # onboarding state. `onboarding_restored` is 0 when the
                # verification READ failed ("could not confirm" must not read
                # as "confirmed").
                "onboarding_expected": len(onboarding_expected_orgs),
                "onboarding_verified": onboarding_verified,
                "onboarding_missing_orgs": (
                    len(onboarding_missing_orgs)
                    if onboarding_verified else None),
                "onboarding_restored": (len(onboarding_expected_orgs)
                                       - len(onboarding_missing_orgs))
                if onboarding_verified else 0,
                "onboarding_missing_links": (len(onboarding_missing_links)
                                             if onboarding_verified else None),
                "onboarding_missing_onboards": (
                    len(onboarding_missing_onboards)
                    if onboarding_verified else None),
                "onboarding_restore_failures": onboarding_restore_failures,
                # The single canonical gap the callers read (see above), plus
                # the pre-preservation UNKNOWN signal — an old-build rescue
                # file cannot say whether the class was lost or never present.
                "onboarding_gap": onboarding_gap,
                "onboarding_missing_total": onboarding_missing_total,
                "onboarding_state_unknown": onboarding_unknown}

    def query(self, cypher: str, **params):
        # P0 guard (#99): refuse bulk graph-wipe on non-test graphs.
        # Respect _skip_guard (consistent with _GuardedGraph.query) so a
        # legitimate maintenance bypass works through either call path.
        if _is_bulk_wipe(cypher) and not self._skip_guard:
            self._assert_test_graph(
                f"REFUSING to run bulk DETACH DELETE on non-test graph "
                f"'{self._graph_name}'"
            )
        return self.g.query(cypher, params=params or None)

    def _assert_test_graph(self, reason: str = "") -> None:
        """Raise RuntimeError if the active graph is not a test graph.

        Test graphs must start with 'test_' or 'tortoise_test'.
        Embedded mode (path=) is inherently isolated (per-instance temp DB) —
        the guard does NOT apply to it. Only server mode (docker) needs the
        graph-name check, protecting the shared real graph (#99).
        """
        if getattr(self, "_is_embedded", False):
            return
        if not self._graph_name.startswith(("test_", "tortoise_test")):
            msg = (
                f"Graph guard: operation blocked on non-test graph "
                f"'{self._graph_name}'. "
                f"{reason} — destructive bulk ops require a graph name starting "
                f"with 'test_' or 'tortoise_test'."
            ).rstrip()
            raise RuntimeError(msg)

    def _falkordb_version_cache_key(self):
        """Process-lifetime cache key for version detection.

        Server mode → ('server', host, port) from the raw redis connection
        (redis Connection objects carry ``_host``/``port``). Embedded mode →
        ('embedded', db path) — redislite's bundled module version is fixed
        per binary, so the path just isolates distinct DBs. Returns None for
        unidentified clients (unit mocks with no endpoint attrs) — those are
        probed fresh every call.
        """
        conn = getattr(self.db, "connection", None)
        if conn is not None:
            host = getattr(conn, "_host", None) or getattr(conn, "host", None)
            port = getattr(conn, "port", None)
            if host is not None and port is not None:
                return ("server", str(host), int(port))
        path = getattr(self, "_path", None)
        if path is not None:
            return ("embedded", str(path))
        return None

    def _get_falkordb_version(self):
        """Parse FalkorDB version from db.info() or raw-connection probes,
        cached per endpoint for the process lifetime.

        Issue #1359: the installed ``falkordb`` python client's ``FalkorDB``
        class has NO ``.info()`` method (``hasattr → False``), so version
        detection must fall back to server-level probes that every engine
        answers. The probes (MODULE LIST + INFO server) cost 2 network RTTs
        per projection open — hot path for SDK sessions / ingest / hosted
        per-request — so the result is cached keyed by endpoint
        (_falkordb_version_cache_key); a server's version doesn't change
        mid-process.

        Returns (major, minor, patch) tuple or None if undetermined.
        """
        key = self._falkordb_version_cache_key()
        if key is not None and key in _FALKORDB_VERSION_CACHE:
            return _FALKORDB_VERSION_CACHE[key]
        version = self._probe_falkordb_version()
        if key is not None:
            _FALKORDB_VERSION_CACHE[key] = version
        return version

    def _probe_falkordb_version(self):
        """Uncached version probe — see _get_falkordb_version.

        Tried in order:
          1. ``db.info()`` — older clients that expose it.
          2. ``MODULE LIST`` via the raw redis connection — returns the
             graph module as ``['name', 'graph', 'ver', NNNNN, ...]``
             where NNNNN = major*10000 + minor*100 + patch (verified on
             falkordblite 0.10.0's bundled module: 41803 → 4.18.3).
          3. ``INFO server`` via the raw connection — dict (newer redis
             clients) or string; carries a ``falkordb_version`` key on
             engines that publish it.

        Returns (major, minor, patch) tuple or None if undetermined.
        """
        import re
        info_strs: list[str] = []

        # 1. db.info() — not present on the current falkordb client; guard anyway.
        try:
            info = self.db.info()
            info_strs.append(info if isinstance(info, str) else str(info))
        except Exception:
            pass

        # 2+3. Raw connection probes (MODULE LIST / INFO server).
        conn = getattr(self.db, "connection", None)
        if conn is not None:
            try:
                modules = conn.execute_command("MODULE", "LIST")
                # [['name', 'graph', 'ver', 41803, 'path', ..., 'args', []]]
                for mod in modules or []:
                    try:
                        if (
                            mod[0] == "name"
                            and str(mod[1]).lower() in ("graph", "falkordb")
                            and mod[2] == "ver"
                        ):
                            v = int(mod[3])
                            return (v // 10000, (v // 100) % 100, v % 100)
                    except (IndexError, TypeError, ValueError):
                        continue
            except Exception:
                pass
            try:
                server = conn.execute_command("INFO", "server")
                if isinstance(server, dict):
                    for key, val in server.items():
                        if "falkordb_version" in str(key).lower():
                            m = re.search(
                                r"(\d+)\.(\d+)(?:\.(\d+))?", str(val))
                            if m:
                                return (int(m.group(1)), int(m.group(2)),
                                        int(m.group(3) or 0))
                else:
                    info_strs.append(server if isinstance(server, str)
                                     else str(server))
            except Exception:
                pass

        # Regex over any collected info strings.
        for info_str in info_strs:
            # Module format: module:name=falkordb,ver=40000 (4.0.0)
            m = re.search(
                r'module:name=(?:falkordb|graph),ver=(\d+)',
                info_str, re.IGNORECASE)
            if m:
                v = int(m.group(1))
                return (v // 10000, (v // 100) % 100, v % 100)
            # Server section: falkordb_version:4.0
            m = re.search(r'falkordb_version:(\d+)\.(\d+)', info_str)
            if m:
                return (int(m.group(1)), int(m.group(2)), 0)

        return None

    #: (label, key-property) branches for _resolve_entity — every branch is
    #: backed by a RANGE index created in _ensure_indexes (issue #327).
    #: Event matches by eventId (Event.id == eventId for all current writes;
    #: eventId-only legacy nodes are covered). Source matches by id and/or url
    #: (url-only ingestion stubs from _link_source have no id).
    _RESOLVE_BRANCHES = (
        ("Point", "id"), ("Subject", "id"), ("Object", "id"),
        ("Source", "id"),
        ("Event", "eventId"), ("Source", "url"),
    )

    def _resolve_entity(self, id_val: str, *, by_id: bool = True,
                        by_eventId: bool = False, by_url: bool = False) -> list[dict]:
        """Index-backed entity resolution mirroring the legacy
        `MATCH (n) WHERE n.id=$id OR n.eventId=$id OR n.url=$id` semantics.

        Returns [{label, key, value, properties}, ...] — one entry per matching
        node, deduped by node identity (a Source with id==url matches two
        branches). Each branch is a labeled lookup on an indexed property, so
        the planner uses Node By Index Scan instead of All Node Scan (#327).

        ``by_id`` also covers Events via their eventId branch: Event nodes
        always carry id == eventId (both set by _upsert_event; eventId-only
        legacy nodes exist), so matching ``Event {eventId:$id}`` reproduces
        the legacy ``n.id = $id`` lookup for Events exactly.

        Per-call-site OR-sets (issue #327 scope table):
        - _get_entity:              id | eventId | url
        - _update/_delete_entity:   id | eventId
        - create_edge source:       id | eventId | url ; target: id | eventId
        - create_about_edge source: id ; target: id | eventId
        - create_owned_by / about stub links: id only
        """
        branches = []
        for label, prop in self._RESOLVE_BRANCHES:
            if prop == "id" and not by_id:
                continue
            # Event branch: enabled by by_id (Event.id == eventId invariant)
            # or explicitly by by_eventId.
            if prop == "eventId" and not (by_id or by_eventId):
                continue
            if prop == "url" and not by_url:
                continue
            branches.append((label, prop))
        if not branches:
            return []
        union_q = " UNION ".join(
            f"MATCH (n:{label}) WHERE n.{prop} = $id "
            f"RETURN '{label}' AS label, '{prop}' AS key, n.{prop} AS value, "
            f"properties(n) AS props LIMIT 1"
            for label, prop in branches
        )
        rows = self.g.query(union_q, params={"id": id_val}).result_set
        seen: set[str] = set()
        out: list[dict] = []
        for label, key, value, props in rows:
            ident = props.get("id") or props.get("eventId") or props.get("url")
            if ident in seen:
                continue
            seen.add(ident)
            out.append({"label": label, "key": key, "value": value,
                        "properties": dict(props)})
        # Defense-in-depth (issue #327 security review): consumers interpolate
        # label/key into Cypher patterns. Fail loudly here if this producer ever
        # returns anything other than the constant-tuple values — a future
        # dynamic-label branch must not become query-text injection downstream.
        allowed_labels = {lbl for lbl, _ in self._RESOLVE_BRANCHES}
        for r in out:
            if r["label"] not in allowed_labels or r["key"] not in {"id", "eventId", "url"}:
                raise RuntimeError(
                    f"_resolve_entity produced unsafe label/key: "
                    f"{r['label']!r}/{r['key']!r} (contract: constant tuple only)")
        return out

    def _ensure_indexes(self) -> None:
        """Create indexes on frequently-filtered Point properties.

        Wraps CREATE INDEX in try/except because FalkorDB raises
        "Attribute 'X' is already indexed" rather than silently no-opping.
        On subsequent startups, each CREATE INDEX errors immediately
        (O(1) check), so there is no startup penalty on large graphs.

        FTS and vector indexes are gated on FalkorDB >= 4.x.

        Boolean-index policy (#522 embedded, #3154 docker/server): the
        property ``is_operator`` is intentionally NEVER indexed, on any
        backend. For embedded (redislite) the original reason was the stale
        bool type table across reopen (#522). #3154 established the same
        hazard on docker/server FalkorDB via ``GRAPH.COPY``: a copied
        boolean index can contain no entry for ``false``, so
        ``n.is_operator = false`` silently matches ZERO rows on graphs copied
        with the index in its SDK-created position (the pre-#3154 shape — the
        boolean index built alongside the other Point indexes; verified on
        docker FalkorDB 4.20.4, the boolean-single and the
        ``(is_operator, lastDreamedAt)`` composite schemas) — and the copy
        DESTINATION cannot be healed by rebuilding: after ``DROP INDEX`` a
        fresh ``CREATE INDEX`` on ``is_operator`` is corrupt too (also
        measured). The full label scan for `= false` is correct on every
        backend; ``lastDreamedAt`` (never ``is_operator``) carries the
        staleness ordering. See the purge block below and
        ``hosted_backup._audit_copied_boolean_indexes`` for the copy-path
        verification.
        """
        # ── Range indexes (always safe, pre-4.x compatible) ──
        # NOTE: no index on `is_operator` is created here on ANY backend —
        # see the boolean-index policy in the docstring and the #3154 purge
        # below. The epic-903 staleness ordering rides on the plain
        # lastDreamedAt index.
        point_props = ("id", "pointKind", "content_hash")
        for prop in point_props:
            try:
                self.g.query(f"CREATE INDEX FOR (n:Point) ON (n.{prop})")
            except Exception as e:
                msg = str(e).lower()
                if "already indexed" in msg or "already exists" in msg:
                    pass  # expected — index exists from prior startup
                else:
                    import logging
                    logging.getLogger(__name__).error(
                        "Failed to create index on n.%s: %s", prop, e)

        # ── #3154: purge boolean `is_operator` indexes (ALL backends) ──
        # GRAPH.COPY silently drops the `false` postings of a boolean RANGE
        # index: the destination's index can have no entry for `false`, so
        # `n.is_operator = false` silently matches ZERO rows on graphs copied
        # with the boolean index in its SDK-created position (verified on
        # docker FalkorDB 4.20.4: a source built by the pre-#3154
        # `_ensure_indexes` — id/pointKind/content_hash singles then the
        # `(is_operator, lastDreamedAt)` composite — copies as 0 false + 5
        # true, while `NOT n.is_operator` still returns 10 and
        # `typeof(n.is_operator)` stays Boolean: the DATA is intact, the
        # index is corrupt; a composite-ONLY source copies healthy, so the
        # trigger is the index set, not the property alone). The copy destination is also poisoned for
        # boolean index BUILDING: a freshly CREATEd is_operator index on it
        # is corrupt too, so drop-and-recreate cannot heal a graph. The only
        # durable fix is for the index not to exist.
        #
        # Unconditional best-effort DROP on every backend: an absent index
        # raises "no such index" in O(1) (the healthy case, so there is no
        # startup penalty on large graphs), while legacy and copied graphs
        # are healed on open. Both the single `:Point(is_operator)` and the
        # composite `(is_operator, lastDreamedAt)` forms are swept. This runs
        # BEFORE the lastDreamedAt index is (re)created below so a loose
        # match that also removed lastDreamedAt cannot leave the staleness
        # ordering unindexed.
        for _stmt in ("DROP INDEX ON :Point(is_operator)",
                      "DROP INDEX ON :Point(is_operator, lastDreamedAt)"):
            try:
                self.g.query(_stmt)
            except Exception as _e:
                # Only "no such index" is the healthy case. Swallowing every
                # failure would treat a genuine drop error as "nothing to
                # drop", leaving a poisoned index in place with no diagnostic
                # — the silent-failure class #3154 exists to close (#3154
                # review P2).
                if "no such index" not in str(_e).lower():
                    logger.warning(
                        "#3154: DROP INDEX %s failed: %s", _stmt, _e,
                    )

        # ── lastDreamedAt freshness index (epic 903-C2, #1240) ──
        # Powers the stale-first scheduler's staleness ranking
        # (ORDER BY lastDreamedAt ASC, null = stalest). #3154: the PLAIN
        # lastDreamedAt index on every backend — `is_operator` is never
        # indexed (see the purge above). Idempotent + AOF-replay-safe
        # (CREATE INDEX survives AOF replay —
        # tests/test_embedded_concurrency.py:532).
        try:
            self.g.query("CREATE INDEX FOR (n:Point) ON (n.lastDreamedAt)")
        except Exception as e:
            msg = str(e).lower()
            if "already indexed" in msg or "already exists" in msg:
                pass  # expected — index exists from prior startup
            else:
                import logging
                logging.getLogger(__name__).error(
                    "Failed to create index on :Point(lastDreamedAt): %s", e)

        # ── Embedded repair: drop stale composite Point indexes (#522) ──
        # A composite index containing is_operator (created by an older
        # _ensure_indexes or the pre-#522 build) has entries typed by the
        # writing process; a later process reopening the same embedded DB
        # routes `= false` through it and gets ZERO matches (verified on the
        # crash-recovery path). Drop every Point index that includes
        # is_operator. redislite's DROP INDEX matches on the CREATE-time
        # field order and the build creates multiple overlapping composites
        # (verified: two distinct Point composites on the same DB), so all
        # permutations containing is_operator are swept. The canonical
        # per-property indexes are recreated below / on next boot; the
        # fulltext content index is created later in this function.
        if getattr(self, "_is_embedded", False):
            try:
                _rows = self.g.query("CALL db.indexes()").result_set
                _needs_repair = any(
                    _row and _row[0] == "Point"
                    and "is_operator" in str(_row[1])
                    for _row in _rows
                )
                if _needs_repair:
                    import itertools as _it
                    _fields = ("id", "pointKind", "content_hash",
                               "is_operator", "content")
                    for _n in range(2, 6):
                        for _perm in _it.permutations(_fields, _n):
                            if "is_operator" not in _perm:
                                continue
                            try:  # noqa: SIM105
                                self.g.query(
                                    "DROP INDEX ON :Point("
                                    + ", ".join(_perm) + ")"
                                )
                            except Exception:
                                pass
            except Exception:
                pass

        # ── D10 (ONTOLOGY v3.15 §4.4): the :Document label is retired — a
        # document is a :Source. No :Document range index is created. A
        # :Source(documentKind) index is deliberately NOT added: the index
        # block declines kind-field indexes (measured 3.15x write slowdown,
        # #522). ──

        # ── Range indexes on canonical entity keys (issue #327) ──
        # Point/Document are created above; these enable index-backed
        # _resolve_entity lookups and faster name/url/eventId-keyed MERGE
        # upserts. Deliberately NO status/op_type/kind-field indexes — a
        # measured 3.15x write slowdown on Point upserts with status/op_type
        # outweighs the batch-read benefit (see issue #522).
        for label, props in (("Subject", ("id", "name")),
                             ("Object", ("id", "name")),
                             ("Event", ("eventId",)),
                             # canonicalUrl (#5012 S0b): the S0a canonical
                             # identity S0b resolves against, so the resolver
                             # is an index seek, not a label scan.
                             ("Source", ("id", "url", "canonicalUrl"))):
            for prop in props:
                try:
                    self.g.query(f"CREATE INDEX FOR (n:{label}) ON (n.{prop})")
                except Exception as e:
                    msg = str(e).lower()
                    if "already indexed" in msg or "already exists" in msg:
                        pass
                    else:
                        import logging
                        logging.getLogger(__name__).error(
                            "Failed to create index on %s.%s: %s", label, prop, e)

        # ── Session.actor_user_id range index (#2600) ──
        # The actor-filtered session read (list_sessions?actor_user_id, E2E-8)
        # must be an index seek — plain string single-prop RANGE index
        # (embedded-safe, mirrors the entity string indexes above; the #522
        # composite hazard is is_operator-BOOL-specific and does not apply).
        try:
            self.g.query(
                "CREATE INDEX FOR (s:Session) ON (s.actor_user_id)")
        except Exception as e:
            msg = str(e).lower()
            if "already indexed" in msg or "already exists" in msg:
                pass
            else:
                import logging
                logging.getLogger(__name__).error(
                    "Failed to create index on Session.actor_user_id: %s", e)

        # ── Session.id index (#3947 review) ──
        # `_link_session` (the replay of a captured turn) MERGEs
        # `:Session {id:...}` once per turn, and the live capture turn loop
        # MATCHes the same key once per turn (`sdk.py` ~3399), so without an
        # index each is a label scan — replaying an N-turn session cost O(N²),
        # which is exactly the path this fix makes reachable. String RANGE
        # index, mirroring the id indexes above (the #522 composite hazard is
        # is_operator-BOOL-specific and does not apply).
        try:
            self.g.query("CREATE INDEX FOR (s:Session) ON (s.id)")
        except Exception as e:
            msg = str(e).lower()
            if "already indexed" in msg or "already exists" in msg:
                pass
            else:
                import logging
                logging.getLogger(__name__).error(
                    "Failed to create index on Session.id: %s", e)

        # ── Full-text & vector indexes require FalkorDB 4.x+ (#7779) ──
        _ver = getattr(self, '_falkordb_version', None)
        if _ver is None or _ver[0] >= 4:
            # ── Full-text indexes ──
            for label, fields in [("Point", ["content", "search_keys"]),  # R2 (#1541) D3
                                  # #244: AgentSession events populate name
                                  # (not subject) — index both so session name
                                  # matches surface through FTS.
                                  ("Event", ["subject", "name"]),
                                  ("Subject", ["name"]),
                                  ("Object", ["name"]),  # #1350 S3: the
                                  # extractor's entity search (existing items
                                  # by name) needs the Object FTS leg —
                                  # without it S3's entities bucket is dead
                                  # on the real backend.
                                  ("Source", ["_searchText"])]:  # #125 Document FTS
                                  # (D10: the doc node is a :Source, so the
                                  # full-text leg rides the Source label).
                                  # #3518: a captured session's :Source carried
                                  # NO searchable text field, so
                                  # `tortoise_fts_query(entity_type='source')`
                                  # never resolved against this label (it
                                  # degraded to `index_missing` / an empty run)
                                  # and the captured session was unfindable.
                                  # `_searchText` is the ONE searchable field
                                  # and BOTH Source writers populate it through
                                  # `sdk._source_search_text` — the indexer
                                  # (`_upsert_source`, title) and the capture
                                  # path (`_materialize_session_source`,
                                  # title-else-summary). `summary`/`topics` are
                                  # deliberately NOT indexed: a second
                                  # vocabulary the FTS surface does not read.
                try:
                    fields_sql = ", ".join(f"'{f}'" for f in fields)
                    self.g.query(f"CALL db.idx.fulltext.createNodeIndex('{label}', {fields_sql})")
                    if label == "Point":
                        # R2 (#1541) D3: a FRESH DB created the two-field
                        # index directly — mark the migration done so a later
                        # boot (create → "already") never re-enters the
                        # drop→recreate path (marker guards churn).
                        try:  # noqa: SIM105
                            self.g.query(
                                "MERGE (m:Meta {key:'point_fts_v2'}) SET m.v = true"
                            )
                        except Exception:
                            pass
                except Exception as e:
                    msg = str(e).lower()
                    if "already" in msg:
                        if label == "Point":
                            # R2 (#1541) D3: legacy single-field ('content')
                            # Point index → drop→recreate with search_keys,
                            # the #244 Event-marker pattern: the point_fts_v2
                            # Meta marker guards a ONE-TIME migration
                            # (persisted DB marker, not a per-process flag —
                            # a process-local bool would re-drop+recreate on
                            # every restart/worker: churn + a drop→recreate
                            # crash window where Point FTS degrades). The
                            # drop procedure name varies by engine
                            # (db.idx.fulltext.drop on server v4.16.7;
                            # dropIndex on some builds) — try both; an engine
                            # with neither (FalkorDBLite embedded) leaves the
                            # content-only index (its sparse path is the D4
                            # TF-IDF snapshot anyway). The same marker guard
                            # runs a ONE-TIME data fixup: pre-R2 nodes stored
                            # search_keys as an ARRAY, and FalkorDB's
                            # fulltext index does NOT index array-valued
                            # properties (verified on v4.16.7) — flatten to a
                            # flat space-joined string (the sdk write path
                            # already stores flat; this fixes existing nodes).
                            try:
                                done = self.g.query(
                                    "MATCH (m:Meta {key:'point_fts_v2'}) RETURN 1"
                                ).result_set
                                if not done:
                                    rows = self.g.query(
                                        "MATCH (n:Point) WHERE n.search_keys IS NOT NULL "
                                        "RETURN n.id, n.search_keys"
                                    ).result_set
                                    for nid, sk in rows:
                                        if isinstance(sk, (list, tuple)):
                                            flat = " ".join(
                                                str(k).strip() for k in sk
                                                if str(k).strip()
                                            )
                                            self.g.query(
                                                "MATCH (n:Point {id:$id}) "
                                                "SET n.search_keys = $flat",
                                                params={"id": nid, "flat": flat},
                                            )
                                    for drop_proc in ("db.idx.fulltext.drop",
                                                      "db.idx.fulltext.dropIndex"):
                                        try:
                                            self.g.query(
                                                f"CALL {drop_proc}('Point')"
                                            )
                                            break
                                        except Exception:
                                            continue
                                    self.g.query(
                                        "CALL db.idx.fulltext.createNodeIndex("
                                        "'Point', 'content', 'search_keys')"
                                    )
                                    self.g.query(
                                        "MERGE (m:Meta {key:'point_fts_v2'}) SET m.v = true"
                                    )
                            except Exception:
                                pass
                        elif label == "Event":
                            # #244: legacy subject-only Event FTS index —
                            # migrate to include name ONCE (persisted DB
                            # marker, not a per-process flag: a process-local
                            # bool re-drops+recreates the index on every
                            # restart/worker, causing churn + a drop→recreate
                            # crash window where Event FTS degrades).
                            # FalkorDBLite embedded lacks dropIndex — leave
                            # subject-only there (name search still covered by
                            # the keyword fallback + vector strategies).
                            try:
                                done = self.g.query(
                                    "MATCH (m:Meta {key:'event_fts_v2'}) RETURN 1"
                                ).result_set
                                if not done:
                                    self.g.query("CALL db.idx.fulltext.dropIndex('Event')")
                                    self.g.query("CALL db.idx.fulltext.createNodeIndex('Event', 'subject', 'name')")
                                    self.g.query(
                                        "MERGE (m:Meta {key:'event_fts_v2'}) SET m.v = true"
                                    )
                            except Exception:
                                pass
                    else:
                        import logging
                        logging.getLogger(__name__).warning(
                            "Failed to create fulltext index on %s.%s: %s", label, fields, e)

            # ── Vector index (HNSW) — Docker/server FalkorDB only (#7764) ──
            # Embedded mode (redislite) uses brute-force vec.euclideanDistance instead.
            # HNSW requires RediSearch module, not bundled with redislite.
            # #1359: the engine's index API varies by version — try the
            # RediSearch-style procedure first, fall back to the Cypher-native
            # form on engines that don't register it (verified: falkordblite
            # 0.10.0's bundled module exposes `CREATE VECTOR INDEX ... OPTIONS`
            # but NOT `db.idx.vector.createNodeIndex`). Record which API
            # succeeded on self._vector_index_api for the query path.
            if not getattr(self, '_is_embedded', False):
                # #4194/#4280: the width is the ONE constant the STORE declares
                # (`FalkorProjection.required_embedding_dim`), so a FRESH index
                # creation and the write path cannot disagree — a bare literal
                # here plus a rotated `EMBEDDING_DIM` would bless vectors the
                # index cannot hold (the mismatched-vector trap).
                # ⛔ This single-sources CREATION only: an EXISTING index is
                # never reconciled (both 'already' branches below assume it is
                # correct). A dimension change is still the documented
                # drop-and-recreate operation, not a constant edit.
                from ..embeddings import EMBEDDING_DIM
                try:
                    self.g.query(
                        "CALL db.idx.vector.createNodeIndex('Point', 'embedding', "
                        f"{EMBEDDING_DIM}, 'HNSW')"
                    )
                    self._vector_index_api = 'procedure'
                except Exception as e:
                    msg = str(e).lower()
                    if "already" in msg:
                        # Index already exists (prior startup). Assume the
                        # procedure API — it either created it or the engine
                        # is procedure-capable (docker/server image v4.16.7).
                        self._vector_index_api = 'procedure'
                    else:
                        # Unknown procedure / not registered / invalid args →
                        # Cypher-native form (the modern falkordb client's own
                        # create_node_vector_index emits exactly this).
                        try:
                            self.g.query(
                                "CREATE VECTOR INDEX FOR (p:Point) ON (p.embedding) "
                                f"OPTIONS {{dimension: {EMBEDDING_DIM}, "
                                "similarityFunction: 'cosine'}"
                            )
                            self._vector_index_api = 'cypher'
                        except Exception as e2:
                            msg2 = str(e2).lower()
                            if "already" in msg2:
                                self._vector_index_api = 'cypher'
                            else:
                                import logging
                                logging.getLogger(__name__).warning(
                                    "Failed to create vector index on Point.embedding: %s", e2)
        else:
            import logging
            logging.getLogger(__name__).info(
                "Skipping FTS and vector indexes: FalkorDB %s < 4.x",
                '.'.join(map(str, _ver)))

    @property
    def required_embedding_dim(self) -> int | None:
        """The embedding width THIS store can hold, for the write path to declare.

        #4280: the width constraint belongs to the Point HNSW INDEX, so this
        answers "does this store have one" — ``self._vector_index_api``, set by
        ``_ensure_indexes`` (called from ``__init__``) to the API that actually
        created or found the index. It stays ``None`` whenever no index exists:

          * embedded (FalkorDBLite) — index creation is skipped by design
            (see the vector-index block above: "Embedded mode (redislite) uses
            brute-force vec.euclideanDistance instead");
          * a FalkorDB engine older than 4.x — the whole index block is
            skipped;
          * index creation FAILED on a non-embedded store.

        Every one of those three lanes reads through
        ``search_engine.run_vector_query``'s DIMENSION-AGNOSTIC brute-force
        branch, so a self-consistent encoder of any width is fully usable there
        and is NOT dropped. Keying this on the deployment flag
        (``_is_embedded``) instead of on the index's existence silently NULLed
        usable vectors on cases 2 and 3 — the #4280 failure shape on another
        lane (review finding, PR #4280).

        With an index present the width is :data:`EMBEDDING_DIM`, the width the
        index is created with — a stored vector of any other width is a broken
        leg, not a near-miss. Callers pass this to
        ``embeddings.encode_for_store`` / ``encode_batch_for_store`` — NOT to
        the ``compute_embedding`` / ``compute_embeddings`` seam, which takes no
        width (that seam is a widely-replaced interception point; see
        ``encode_for_store``'s docstring).
        """
        if self._vector_index_api is None:
            return None  # no Point vector index → brute-force, any width
        try:
            from ..embeddings import EMBEDDING_DIM
        except Exception:  # noqa: BLE001, RUF100
            # #5148 review: this is read on the REPLAY path (`_upsert_point_props`,
            # outside any `try`), so an unimportable seam here would abort every
            # replayed event and re-enter the #5119 failure shape. The width is
            # an ENFORCEMENT guard, not data: degrading to the property's
            # documented "any width" answer restores the journalled vector and
            # keeps the graph, which is strictly better than refusing to rebuild
            # it. (An index that cannot be described is also not one this store
            # can use to reject a vector usefully.)
            return None
        return EMBEDDING_DIM

    def backfill_document_search_text(self) -> int:
        """#125: set _searchText on document Sources missing it (idempotent).

        D10: the document node is a :Source, so this targets document-bearing
        Sources (``documentKind IS NOT NULL``) — never a session/connector/
        provenance Source, which owns no _searchText.
        Returns the number of document Sources backfilled.
        """
        rows = self.g.query(
            "MATCH (s:Source) WHERE s.documentKind IS NOT NULL "
            "AND s._searchText IS NULL "
            "SET s._searchText = coalesce(s.title, '') "
            "RETURN count(s)"
        ).result_set
        return rows[0][0] if rows else 0

    def close(self) -> None:
        """Close the underlying DB connection idempotently.

        Lifecycle hardening (plan Task 4): idempotent (2nd call no-op),
        registered via weakref.finalize so GC cleans up without explicit
        close, and atexit-registered so normal process exit never orphans.
        """
        if getattr(self, "_closed", False):
            return
        self._closed = True
        try:
            # #1475: route through db._t_close (when present) so the db's
            # _t_closed flag is set — the GC finalizer must recognize this
            # client as explicitly closed and stay a strict no-op. Note the
            # installed redislite `FalkorDB.close()` is NOT a bare redis-py
            # pool disconnect: it shadows that and calls
            # `self.client._cleanup()`, tearing the server down. `_t_close`
            # is still the correct entry point here because it is the one
            # that also drives the flag plus the #3599 owner-record release
            # (a bare `db.close()` now releases the owner record too — see
            # `tortoise.FalkorDB.close`). Host-mode db (no _t_close) keeps
            # the plain close.
            close = getattr(self.db, "_t_close", None) or self.db.close
            close()
        except Exception:
            pass

    def _atexit_close(self) -> None:
        """#1371: atexit seam — collect ephemeral test servers for the
        batch flush first.

        Falls through to the normal close() when the fast path does not
        apply (server-mode clients have no fast path; non-ephemeral or
        unset flag routes through the helper's False return).
        """
        db = getattr(self, "db", None)
        # #4214: `at_exit=True` — reached only from the `atexit` registration
        # (see `register_atexit_close`), so a spent exit budget stops the
        # cascade instead of letting it block `Py_FinalizeEx`.
        if db is not None and atexit_fast_close(getattr(db, "client", db),
                                                at_exit=True):
            self._closed = True
            return
        self.close()

    def __enter__(self) -> "FalkorProjection":  # noqa: UP037
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _apply_annotator(self, ev: dict) -> int:
        """Replay an ``OperatorAnnotated`` record (#3689) — SET the dims it
        carries onto the operator Point.

        The durable counterpart of ``annotate_operator``'s live
        ``update_point`` write. A non-terminalizing property write, so it
        folds INLINE (parity with the ``PointRevised`` branch). Its pass-1b
        survivor gate is ``last_ann_drop_seq`` — the REAL hard-delete→recreate
        boundary — NOT the terminalizing folds' ``last_recreate_seq``
        (``last_recreate_seq`` advances on any creation; a bare same-id re-emit
        MERGEs live and never clears a dim, so gating the annotator fold on it
        silently dropped a live-valid annotation; #3689 review P2). The gate
        lives at the pass-1b call site; this method folds whatever the caller
        admits. The dims have no edge/graph-shape effect, so pass 2 cannot
        clobber them. Returns the matched node count (1 when the operator
        exists; 0 = unwritable/absent id, no dim present, or the Point is
        absent) — the fold-miss signal, mirroring ``_fold_entity_mutation``.
        """
        pid = ev.get("id")
        if not _writable_id(pid):
            return 0
        dims = _annotator_dims(ev, aliases=True)
        if not dims:
            return 0
        set_clauses = [f"n.{key} = ${key}" for key in dims]
        res = self.g.query(
            f"MATCH (n:Point {{id:$id}}) SET {', '.join(set_clauses)} "
            f"RETURN n.id",
            params={"id": pid, **dims},
        )
        return len(res.result_set or [])

    def _revise_point(self, ev: dict, set_updated_at: bool = False,
                      skip_annotator_dims: bool = False,
                      skip_belief_props: bool = False,
                      skip_content: bool = False,
                      skip_embedding: bool = False,
                      skip_hash: bool = False) -> tuple[bool, bool]:
        """Apply PointRevised event — update content, context, and re-compute embedding.

        ``skip_annotator_dims`` (#3689): suppress ONLY the annotator-dim
        fold. ``rebuild_all`` sets it for a revision that predates the id's
        last HARD-DELETE boundary (``last_ann_drop_seq`` — a real
        delete→recreate; the dead incarnation's dims must not leak onto the
        re-created node). A bare same-id re-emit is NOT such a boundary, so it
        never suppresses a live-valid dim. Chronological callers (``apply()``)
        leave it False. Content/embedding replay is unaffected.

        ``skip_belief_props`` (#2884 A7): the same boundary and the same
        reasoning, applied to the four belief properties. A revision that
        predates a real delete→recreate wrote its belief value onto an
        incarnation live DETACH-DELETEd, so the re-created node never had it;
        folding it would resurrect a dead node's belief (#330 parity — the
        pure fold POPS the entry on delete). ``rebuild_all`` sets it from the
        SAME ``last_ann_drop_seq`` anchor; ``apply()`` leaves it False.

        ``skip_content`` / ``skip_embedding`` / ``skip_hash`` (#4042): the
        CONTENT/derived half of the same class, with a DIFFERENT boundary.
        ``rebuild_all`` sets them for a revision that a creation LATER IN THE
        SAME JOURNAL FILE superseded. `_upsert_point_props` writes
        ``n.content``/``n.updatedAt`` UNCONDITIONALLY, so any later same-file
        creation is a content boundary (``skip_content``). The two derived
        fields are written CONDITIONALLY (``CASE``/``coalesce``), so each is
        suppressed independently — only when a later same-file creation
        actually wrote it, or when a re-creation cleared it (``skip_embedding``
        / ``skip_hash``); a bare re-emit that wrote neither leaves the
        revision's value live-valid. Chronological callers leave all three
        False.

        Returns ``(embedding_written, content_hash_written)`` (#4305) — the
        same conditional-write outcome ``_upsert_point_props`` reports, and
        for the same reason: a written field (an explicit ``None`` wipe
        included) is a JOURNAL-OWNED value the #4305 restore tail must not
        overwrite with the older pre-wipe snapshot. Every other caller ignores
        the return.
        """
        new_content = ev.get("new_content")
        new_context = ev.get("new_context")  # noqa: F841
        # #331 (review r3): NO event_id fallback — parity with _apply_one
        # (fold is the single source of truth, module contract).
        # #331 (review r4): str-only ids.
        pid = ev.get("id")
        if not _writable_id(pid):
            # Malformed PointRevised (non-str, or a NUL/lone-surrogate str the
            # engine/driver reject as a param) — skip rather than crash a
            # rebuild after the wipe (issue #325; #3689 review P1).
            return False, False
        if new_content is not None and not _annotator_value_ok(new_content):
            # A content value FalkorDB cannot take as a parameter (NUL/lone-
            # surrogate str, map, non-finite float, ...). Drop the content
            # EDIT — `coalesce($c, n.content)` then keeps the stored content —
            # but STILL fold the annotator dims below. Aborting here would
            # strand the rebuilt graph after the wipe and block every retry
            # (#3689 review P1; same parameter-writability class as the dims).
            new_content = None
        params: dict = {"id": pid}
        set_clauses: list[str] = []
        wrote_embedding = False
        wrote_content_hash = False

        # #4042: `n.content` is written UNCONDITIONALLY by
        # `_upsert_point_props`, so a creation that superseded this revision
        # already set it — `skip_content` omits the clause (and its
        # `updatedAt` stamp below).
        if not skip_content:
            params["c"] = new_content
            set_clauses.append("n.content = coalesce($c, n.content)")

        if new_content is not None:
            # Re-compute embedding when content changes (even to empty — wipe
            # stale). Always set params["embedding"] so SET overwrites any
            # stale value; on compute failure, set to None rather than
            # preserving old embedding (#19). #4042: omitted when a later
            # same-journal-file creation already wrote this field. #4194/#4280:
            # route through `encode_for_store` (the dim-declaring write seam)
            # so the stored width matches the Point HNSW index this store
            # created — `compute_embedding` takes no width. The seam itself is
            # still `compute_embedding` (module global), so installed encoder
            # doubles keep intercepting.
            if not skip_embedding:
                try:
                    from tortoise.embeddings import encode_for_store
                    emb = (encode_for_store(
                        new_content, self.required_embedding_dim)
                        if new_content else None)
                    params["embedding"] = emb  # None = wipe stale embedding for empty content
                except Exception:
                    params["embedding"] = None  # wipe stale embedding on failure (#19)
                set_clauses.append("n.embedding = $embedding")
                wrote_embedding = True
            if not skip_hash:
                # #2795: content_hash is derived from content — mirror the live
                # update_point #1904 recompute so a replayed PointRevised cannot
                # leave a STALE indexed dedup key behind (the writer now sets a
                # hash on PointAdded, so a missed recompute here would be worse
                # than the prior NULL). #2958 review: `is not None` is not a type
                # gate — a non-str new_content from a corrupt/hand-edited JSONL
                # line would raise inside sha256(text.encode) and kill the rebuild
                # pass (the recovery path). NULL degrades to create_point's
                # content-equality fallback; a stale present-but-wrong hash does
                # not — so NULL is the correct failure value.
                set_clauses.append("n.content_hash = $content_hash")
                wrote_content_hash = True
                try:
                    params["content_hash"] = _content_hash(new_content)
                except Exception:
                    params["content_hash"] = None
        # Phase 2 #49: context removed — new_context no longer written
        if set_updated_at and not skip_content:
            set_clauses.append("n.updatedAt = $now")
            params["now"] = _now_iso()
        # #3689: fold the annotator dims carried as PointRevised extras
        # (update_point's emit) — apply()/rebuild previously dropped them, so
        # rebuild_all silently erased annotate_operator's write. Presence-
        # conditional (see _annotator_dims), canonical names only: the short
        # :GraphEvent aliases belong to OperatorAnnotated, never to a
        # PointRevised (whose keys ARE the node props — review P1).
        if not skip_annotator_dims:
            for key, val in _annotator_dims(ev).items():
                set_clauses.append(f"n.{key} = ${key}")
                params[key] = val
        # #2884 FIX-4: fold the four belief props when the revision carries
        # them (`update_point(confidence=…)` / `posterior_*` / `lastDreamedAt`).
        # The live write is `SET n += $props`, and the PointRevised payload
        # carries the value — but this fold built clauses only from
        # content/context/embedding/hash/annotator dims, so a rebuild silently
        # dropped the caller's belief state (write ≠ read). Presence-
        # conditional and gated by the SAME `_belief_prop_value_ok` the
        # `_fold_confidence_changed` / ConfidenceChanged folds use (#330
        # parity). #2884 A7: suppressed by `skip_belief_props` on the same
        # real-hard-delete anchor as the annotator dims — a pre-recreation
        # revision's belief value died with the deleted incarnation live.
        if not skip_belief_props:
            for key in BELIEF_PROPS:
                if key in ev and _belief_prop_value_ok(key, ev[key]):
                    set_clauses.append(f"n.{key} = ${key}")
                    params[key] = ev[key]

        # #4042: every clause can now be suppressed at once (a superseded
        # props-only revision carrying no dims). FalkorDB rejects a `SET` with
        # an empty clause list — and this is the RECOVERY path, so that abort
        # would strand the rebuilt graph after the wipe. Nothing to write is a
        # no-op, not a crash.
        if not set_clauses:
            return False, False

        self.g.query(
            f"MATCH (n:Point {{id:$id}}) SET {', '.join(set_clauses)}",
            params=params,
        )
        return wrote_embedding, wrote_content_hash

    def _delete_entity_by_id(self, id_val: str,
                             label: str | None = None) -> int:
        """Hard-delete a canonical entity by id.

        The replay counterpart of the SDK's live ``_delete_entity`` (#3299).
        The id predicate is the identity as written (``id`` for
        Point/Subject/Object/Document/Source, ``eventId`` for Event) — never
        re-derived from a live node (the node is already gone). Returns the
        node count deleted (0 = a fold-miss: the entity was already absent).

        #3860: identity is (kind, id). When ``label`` is one of the canonical
        six the delete is SCOPED to that kind — a delete record owns only the
        node kind it names, so it can never destroy a foreign-kind node that
        happens to share the id. ``label=None`` (missing/unknown, i.e. a
        malformed or pre-#3299 raw record) keeps the legacy id-wide delete
        across all six labels, preserving the "a delete must survive replay"
        guarantee for every record shape. The label is NEVER interpolated from
        the journal: only members of ``_CANONICAL_ENTITY_ID_PROPS`` reach the
        Cypher label position.
        """
        if label is not None and label in _CANONICAL_ENTITY_LABELS:
            branches = ((label, _ENTITY_ID_PROP[label]),)
        else:
            branches = _CANONICAL_ENTITY_ID_PROPS
        total = 0
        for label, prop in branches:
            r = self.g.query(
                f"MATCH (n:{label} {{{prop}:$id}}) DETACH DELETE n "
                f"RETURN count(n)",
                params={"id": id_val},
            )
            if r.result_set:
                total += r.result_set[0][0] or 0
        return total

    def _fold_entity_mutation(self, ev: dict) -> int:
        """Replay an ``EntityMutated`` write-surface record (#3299).

        ONE record type, dispatching on the ``op`` discriminator so replay
        reproduces the exact live end-state (the design chose this over one
        event type per label×operation). ``op="delete"`` hard-deletes the
        canonical entity, mirroring the live ``_delete_entity`` — the
        ontology §5 contract: delete hard-deletes, retract tombstones.
        The sibling lanes (#3300 MCP Point delete, #3312 unjournaled update,
        #3377 unjournaled rename) extend this dispatch rather than adding
        record types.

        #3860: the live ``_delete_entity`` is id-wide but emits ONE record per
        MATCHED label; replaying each record scoped to its own label is
        therefore the same net effect (N labels present live → N records → N
        kind-scoped folds), while a record can no longer destroy a foreign-kind
        node that merely shares the id.

        Returns the affected node count (0 for an unknown op, an unimplemented
        op, or an already-absent entity).

        THE NON-FOLDED-SET CONTRACT: a mutation the journal claims and this
        fold cannot replay is a WARNING, never a silent 0 — that silence is
        this class's own defect. Three distinct messages, so a future extender
        is not told its sanctioned op is "unknown":
          * state op matching no entity → fold-miss (post-wipe divergence or an
            out-of-order journal);
          * a recorded-but-unimplemented op (``_ENTITY_MUTATION_PENDING_OPS``)
            → names itself as recorded with no arm yet;
          * anything outside the vocabulary → "unknown".
        Each carries ``event_id`` — the only handle that locates the line.

        The warning is emitted HERE, not at a call site: ``apply()`` discards
        the returned count, and ``rebuild_all`` / ``recover_from_log`` /
        ``restore``'s JSONL fallback all reach this method, so a call-site-only
        warning would be invisible on three of the four replay engines.

        ``op="delete"`` matching no entity is DELIBERATELY exempt: a
        retried/replayed delete is legitimately idempotent (pass-1b already
        warns for the ``rebuild_all`` case), and warning here would turn valid
        journals into false positives.
        """
        op = ev.get("op")
        rid = ev.get("id")
        label = ev.get("label")

        if op in _ENTITY_MUTATION_STATE_OPS:
            # #3312/#3377: replay the mutation from its applied property map.
            # The clause is the SAME `SET n += $s` the live write ran, on the
            # SAME map — so live and replay cannot disagree on removals or on
            # value typing (a `properties(n)` snapshot would: a removed key is
            # absent from it, and a VectorF32 comes back as a plain list).
            if not isinstance(rid, str):
                return 0                                   # #331 parity
            if not (isinstance(label, str) and label in _CANONICAL_ENTITY_LABELS):
                # The label is NEVER interpolated from the journal — only an
                # allowlisted member reaches the Cypher label position.
                _warn_entity_mutation_fold_miss(op, rid, label, ev.get("event_id"))
                return 0
            state = ev.get("state")
            if not isinstance(state, dict):
                _warn_entity_mutation_fold_miss(op, rid, label, ev.get("event_id"))
                return 0
            prop = _ENTITY_ID_PROP[label]
            r = self.g.query(
                f"MATCH (n:{label} {{{prop}:$id}}) SET n += $s RETURN count(n)",
                params={"id": rid, "s": state},
            )
            matched = r.result_set[0][0] if r.result_set else 0
            if not matched:
                _warn_entity_mutation_fold_miss(op, rid, label, ev.get("event_id"))
            return matched or 0

        if op != "delete":
            # Outside the recorded vocabulary, or recorded-but-unimplemented
            # (`retract`/`supersede`). LOUD: a silently dropped mutation is
            # exactly this class's defect. The two are distinguished so a
            # future extender is not told its sanctioned op is "unknown".
            if op in _ENTITY_MUTATION_PENDING_OPS:
                logger.warning(
                    "rebuild: EntityMutated op %r is recorded (#3299) but has no "
                    "fold arm yet (event_id=%s id=%r label=%r) — its mutation is "
                    "NOT replayed", op, ev.get("event_id"), rid, label)
            else:
                logger.warning(
                    "rebuild: unknown EntityMutated op %r (event_id=%s id=%r "
                    "label=%r) — no fold applied; the record's mutation is LOST "
                    "on replay", op, ev.get("event_id"), rid, label)
            return 0
        rid = ev.get("id")
        if not isinstance(rid, str):
            # #331 parity: malformed id → skip, never crash the fold.
            return 0
        # #3860: identity is (kind, id). Scope the fold to the record's
        # canonical label; a missing/unknown label falls back to the legacy
        # id-wide delete (``label=None``). The raw label never reaches the
        # Cypher label position — only the allowlisted branch does.
        label = ev.get("label")
        return self._delete_entity_by_id(
            rid, label if isinstance(label, str) else None)

    def list_graphs(self) -> list[str]:
        """List all graph names in the database."""
        return self.db.list_graphs()

    # ── SVBP integration (Gate 4) ─────────────────────────────────

    def extract_svbp_factors(self, include_draft: bool = False):
        """Extract factor list for TortoiseSVBP from the graph.

        Returns (factors, evidence) where:
          factors = [(op_id, op_type, [input_ids], weight), ...]
          evidence = {claim_id: (alpha, beta), ...}

        Uses batch I/O (2 queries regardless of operator count) to avoid
        the N+1 query pattern that timed out on graphs with 1,800+ operators
        (#400). Operators with <2 inputs are excluded with a warning.

        With include_draft=False (default, #780): draft operators and draft
        claim inputs are excluded — the shared live-only filter applied at
        ALL four factor-extraction call sites. Terminal points are excluded
        UNCONDITIONALLY (#2422): ``_live_only`` returns the terminal-exclusion
        fragment even under the include_draft escape hatch, so drafts are
        re-included but retracted/superseded/outdated/archived points (and
        the ``outdated=true`` flag) never feed SVBP factors.
        """
        # Query 1: all operator IDs and types (single query, O(1) round-trip).
        # #689/#2422: terminal operators never feed EP factors (retracted /
        # superseded / outdated flag).
        # #780: draft operators never feed EP factors (unless opted in).
        # Shared predicate from tortoise/live.py — one definition of the
        # live-only rule across all four factor-extraction call sites.
        draft_o = f"AND {_live_only('o.status', include_draft)}"
        draft_c = f"AND {_live_only('c.status', include_draft)}"
        op_rows = self.g.query(
            "MATCH (o:Point) WHERE o.is_operator = true "
            f"AND {_terminal_excluded('o.status')} "
            f"{draft_o} "
            "RETURN o.id, o.op_type"
        ).result_set

        # Query 2: all inputs for all operators in one batch query (#400)
        # Avoids the N+1 pattern: previously this was a per-operator loop.
        # #689: retracted claims never appear as operator inputs (an operator
        # whose inputs all retract becomes degenerate and is excluded below).
        # #780: draft claims never appear as inputs; draft operators' edges
        # are excluded too.
        input_rows = self.g.query(
            "MATCH (o:Point)-[r:IMPL|NAND]->(c:Point) "
            "WHERE o.is_operator = true "
            f"AND {_terminal_excluded('c.status')} "
            f"{draft_c} {draft_o} "
            "RETURN o.id, c.id "
            "ORDER BY o.id, c.id"
        ).result_set

        # Aggregate input IDs per operator
        op_inputs: dict[str, list[str]] = defaultdict(list)
        op_types: dict[str, str] = {}
        for op_id, op_type in op_rows:
            op_types[op_id] = op_type
        for op_id, claim_id in input_rows:
            op_inputs[op_id].append(claim_id)

        factors = []
        degenerate_count = 0
        for op_id, op_type in op_types.items():
            input_ids = op_inputs.get(op_id, [])
            if len(input_ids) >= 2:
                weight = 3.0 if op_type == "NAND" else 1.0
                factors.append((op_id, op_type, input_ids, weight))
            else:
                degenerate_count += 1

        if degenerate_count > 0:
            logger.warning(
                "extract_svbp_factors: %d operators with <2 inputs excluded "
                "from EP (possible silent edge drop). Run operator validation "
                "to identify affected operators.",
                degenerate_count,
            )

        return factors, {}

    # NOTE (#395 code review, PR #1273): the scoped-by-operator-ids extractor
    # `extract_factors_for_operators` shipped in this PR was REMOVED — it had
    # no production call path (the no-arg local EP runs ep.run, whose factor
    # extraction is ep._affected_factors), and its parity tests pinned it
    # against extract_svbp_factors, which is NOT the local path's reference.
    # The op_type-aware operator predicate it implemented lives on in
    # _affected_factors Batch-1 / _affected_claims (ep.py) and is covered by
    # test_vector_g_legacy_op_type_only_operator. Deleting beat wiring it
    # into production: consuming its (op_id, op_type, [inputs], weight)
    # 4-tuples with hardcoded 3.0/1.0 weights would have changed the shipping
    # local path's factor semantics (compute_operator_weight, direction,
    # label) for no consumer.

    def get_svbp(self, **svbp_kwargs):
        """Create and run TortoiseSVBP on the current graph.

        Returns a TortoiseSVBP instance with converged beliefs.
        """
        # SVBP is DEPRECATED (replaced by EP — tortoise/ep.py, scipy-based).
        # It requires the optional `quadrature` extra (jax). Without jax
        # installed, degrade to None instead of crashing the caller
        # (EventAPI.add_operator → ingest CLI): EP handles propagation.
        try:
            from tortoise.svbp import TortoiseSVBP
        except ImportError:
            logger.warning(
                "get_svbp: jax not installed (quadrature extra) — "
                "skipping deprecated SVBP; EP handles propagation"
            )
            return None
        factors, evidence = self.extract_svbp_factors()
        if not factors:
            return None
        svbp = TortoiseSVBP(**svbp_kwargs)
        svbp.run(factors, evidence=evidence)
        return svbp


def is_missing_graph_error(e: Exception) -> bool:
    """True when a graph error means the graph no longer exists (idempotent).

    #2163: GRAPH.DELETE (select_graph(name).delete()) raises on an ABSENT
    graph — real server text (v4.16.7, empirically verified): "Invalid graph
    operation on empty key". Graph-drop callers treat this family as SUCCESS
    so the deleted-org purge sweep's #926 retry anchor converges (a graph
    dropped by a previous sweep, never minted, or manually removed must not
    keep the org row poisoned forever); genuine failures (auth, dead
    connection) still propagate. Canonical prod copy — tests/_embedded.py's
    private _is_missing_graph_error is kept in sync with these patterns.
    """
    s = str(e).lower()
    return any(k in s for k in ("graph not found", "no such graph",
                                "does not exist", "unknown graph",
                                "invalid graph operation", "empty key"))
