"""Projection — fold the event log into the current graph.

The log is the source of truth; a projection is a derived, rebuildable view.
`_apply_one` is the single source of fold semantics, shared by the pure `fold`
(batch) and every incremental backend, so an incrementally-updated projection and
`fold(read_all())` can never diverge.

Backends behind the `Projection` protocol:
  - InMemoryProjection — dict of points (statements AND operators).
  - FalkorProjection   — FalkorDB (Docker/server by default, embedded via path=);
                         same openCypher graph so portable between modes
"""
from __future__ import annotations  # noqa: I001

import re
import os
import shutil
import logging
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)

# Process-lifetime cache for FalkorProjection._get_falkordb_version (#1359
# review P2): version detection costs two network RTTs (MODULE LIST + INFO
# server) on EVERY projection open — SDK sessions, ingest, hosted per-request.
# A server's version cannot change mid-process, so cache by endpoint
# (server host/port or embedded db path). Unidentified clients (unit mocks)
# are never cached — the probe stays exact for them.
# Keyed by tuple; value is (major, minor, patch) or None (undetermined).
_FALKORDB_VERSION_CACHE: dict[tuple, tuple[int, int, int] | None] = {}


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
    # Property map in MATCH => targeted (e.g. {id:$id}, {team_id:$id})
    if "{" in cypher:
        return False
    # Real WHERE clause (property/param/CONTAINS/IN reference) => targeted
    m = re.search(r"WHERE\s+(.+) ", cypher + " ", re.IGNORECASE)
    if m and _WHERE_REAL_RE.search(m.group(1)):  # noqa: SIM103
        return False
    return True


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

from tortoise.config import RELATIVE_PATH_ERROR, SUPPORTED_URI_SCHEMES  # noqa: E402, I001
from tortoise.live import _live_only  # noqa: E402
from tortoise.embedded_lifecycle import (  # noqa: E402
    atexit_fast_close,  # #1371: registers the batch flush
    register_atexit_close,
    register_gc_close,
)

# Backward-compat alias: the canonical scheme set lives in tortoise.config
# (SUPPORTED_URI_SCHEMES) so URI-routing and connection-layer validation share
# one source of truth (#715). Kept for existing importers of the private name.
_SUPPORTED_URI_SCHEMES = SUPPORTED_URI_SCHEMES

# ── Mixins ────────────────────────────────────────────────────────────────
from tortoise.projection.entities import _EntityHandlers  # noqa: E402, I001
from tortoise.projection.edges import _EdgeHandlers  # noqa: E402
from tortoise.projection.grounding import _GroundingMixin  # noqa: E402
from tortoise.projection.propagation import _PropagationMixin  # noqa: E402

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
        p = points.get(rid) if isinstance(rid, str) else None
        if p:
            if ev.get("new_content") is not None:
                p["content"] = ev["new_content"]
            # Phase 1: discard new_context for v2+ events (#49)
            if ev.get("new_context") is not None and ev.get("projection_version", 0) < 2:
                p["context"] = ev["new_context"]
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
    # IngestStarted: no graph effect


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


# ── FalkorProjection ──────────────────────────────────────────────────────


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

        # No-arg construction -> canonical embedded path (plan Task 9: graph-
        # scripts migrated from FalkorProjection('tortoise.db') to no-arg,
        # which must resolve to the canonical TORTOISE_DB_PATH).
        if path is None and host is None:
            from tortoise.config import resolve_db_path
            path = resolve_db_path()

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
            aof_enabled = (
                os.environ.get("TORTOISE_EMBEDDED_AOF", "").strip().lower()
                in ("1", "true", "yes")
            )
            aof_dir = (
                os.path.basename(os.path.abspath(path)) + "-appendonlydir"
            ) if (path != ":memory:" and aof_enabled) else None
            self.db = FalkorDB(
                path,
                serverconfig=(
                    {"appendonly": "yes", "appenddirname": aof_dir}
                    if (path != ":memory:" and aof_enabled) else None
                ),
            )
        elif host is not None:
            # Docker FalkorDB
            from falkordb import FalkorDB  # ponytail: lazy import, only needed for Docker mode
            self.db = FalkorDB(host=host, port=port, username=username, password=password,
                               socket_connect_timeout=5, socket_timeout=10, ssl=ssl)
        else:
            raise ValueError("Either path or host must be provided")

        self.g = _GuardedGraph(self.db.select_graph(graph_name), self)
        self.graph_name = graph_name
        self._graph_name = graph_name
        self._skip_guard = False
        self._is_embedded = (path is not None)
        self._path = path
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
        self._vector_index_api = None
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
        """Cheap connectivity probe — does the graph answer queries?"""
        try:
            self.g.query("MATCH (n) RETURN count(n) LIMIT 1")
            return True
        except Exception:
            return False

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

        The event log is the source of truth; the projection a derived view.
        Two corruption modes are caught:
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
                    logger.warning(
                        "auto-recovered empty embedded DB from %s (%s events)",
                        events_dir, result.get("log_points"))
                elif result.get("reason"):
                    # Lost-graph case but recovery declined (ambiguous/unreadable
                    # log) — warn loudly instead of silently continuing with an
                    # empty DB (ops safety #428: no silent data loss).
                    logger.warning(
                        "empty embedded DB not auto-recovered: %s",
                        result.get("reason"))

    def _recover_or_raise(self, events_dir: str) -> None:
        """Run recover_from_log and fail loud if it did not recover."""
        from tortoise.consistency import recover_from_log
        result = recover_from_log(events_dir, self)
        if not result.get("recovered"):
            raise RuntimeError(
                f"DB health check failed and recovery did not complete: "
                f"{result.get('reason')}. "
                f"See operations/skills/tortoise-rebuild/SKILL.md")

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
        from urllib.parse import urlparse
        parsed = urlparse(uri)
        _validate_uri_scheme(parsed.scheme)
        username = parsed.username or None
        password = parsed.password or None
        if graph_name is None:
            graph_name = parsed.path.lstrip('/') or "tortoise"
        return cls(host=parsed.hostname or "localhost",
                   port=parsed.port or 16379,
                   username=username,
                   password=password,
                   graph_name=graph_name,
                   ssl=(parsed.scheme == "rediss"))

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
            self._upsert(p)
        elif t == "PointRevised":
            # Phase 1: discard new_context for v2+ events (#49)
            if ev.get("projection_version", 0) >= 2:
                ev.pop("new_context", None)
            self._revise_point(ev, set_updated_at=True)
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
            p = ev.get("point")
            if isinstance(p, dict) and p.get("id"):
                self._upsert(p)
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
        elif t == "EventRecorded":
            return self._upsert_event(ev)
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

    def rebuild(self, log) -> None:
        self.g.query("MATCH (n) DETACH DELETE n")
        for ev in log.read_all():
            self.apply(ev)

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
        """
        import os  # noqa: I001
        from tortoise.log import EventLog

        # ── #548: snapshot existing graph BEFORE wiping ──────────────
        # SDK-created points written via Cypher may have no corresponding
        # event in the JSONL log. Snapshot them now so they survive the
        # wipe+replay cycle.
        synthetic_events: list[dict] = []
        try:
            rows = self.g.query(
                "MATCH (n:Point) RETURN properties(n)"
            ).result_set
            existing_points = {}
            for r in rows:
                props = r[0]
                pid = props.get("id")
                if pid:
                    existing_points[pid] = props
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
                    # Strip volatile properties that are recomputed on replay
                    clean = {k: v for k, v in props.items()
                             if k not in ("embedding", "content_hash",
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
                            inputs = [er[0] for er in edge_rows]
                        except Exception:
                            pass  # edge query may fail on corrupt graphs
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
        except Exception:
            pass  # Graph may be corrupt — skip snapshot; JSONL replay is best-effort

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
        except Exception:
            pass  # graph may be corrupt — best-effort, like the #548 snapshot

        # ── Wipe + rebuild ──────────────────────────────────────────
        # WIPE-AFTER-PARSE (epic #900 T12/T3, cycle-21 ordering pin): parse
        # ALL .jsonl into memory (line-tolerant — a torn TRAILING line from a
        # SIGKILL mid-append is skipped with a warning + count via
        # EventLog.read_all, never raised — S15) BEFORE the wipe. A
        # wipe-then-parse order would turn one torn line into TOTAL LOSS
        # (wipe lands, then the parse raises, then the #548 snapshot phase
        # swallows the same error silently).
        #
        # Collect all events from all files (synthetic first so their nodes
        # exist before JSONL events that may reference them)
        events = list(synthetic_events)
        for fname in sorted(os.listdir(log_dir)):
            if fname.endswith('.jsonl'):
                events.extend(EventLog(os.path.join(log_dir, fname)).read_all())

        self.g.query("MATCH (n) DETACH DELETE n")

        # Pass 1: create all Point/Operator nodes (skip edges) + non-edge events
        # Pass 1a: create all Point/Operator nodes first
        # (skip edges), so cross-file PointRevised always has a node to revise (#21).
        for ev in events:
            ev = self._norm(ev)
            t = ev.get("type")
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
                # Phase 1 stop-writes: strip context from v2+ events (#49)
                # (identical to apply() — parity between rebuild and apply)
                if ev.get("projection_version", 0) >= 2:
                    p.pop("context", None)
                # Property parity with apply()/apply_one (#330): the shared
                # helper writes ALL node properties incl. authoredBy,
                # embedding, validFrom/To, extractedFrom, provenanceSource.
                self._upsert_point_props(p)

        # Pass 1b: apply revisions + other non-edge events AFTER all nodes exist
        for ev in events:
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
                    self._retract(rid)
            elif t == "PointPromoted":
                # #785: rebuild parity — re-apply the promoted snapshot.
                p = ev.get("point")
                if isinstance(p, dict) and p.get("id"):
                    if ev.get("projection_version", 0) >= 2:
                        p.pop("context", None)
                    self._upsert_point_props(p)
            elif t == "OperatorPromoted":
                # #785/R16: restore the operator's live status on replay.
                p = ev.get("point")
                if isinstance(p, dict) and p.get("id"):
                    if ev.get("projection_version", 0) >= 2:
                        p.pop("context", None)
                    self._upsert_point_props(p)
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
            elif t == "PointRevised":
                # Phase 1: discard new_context for v2+ events (#49)
                if ev.get("projection_version", 0) >= 2:
                    ev.pop("new_context", None)
                # set_updated_at parity with apply() (#330)
                self._revise_point(ev, set_updated_at=True)
            elif t == "EventRecorded":
                self._upsert_event(ev)
            elif t == "SubjectAdded":
                self._upsert_subject(ev)
            elif t == "ObjectRegistered":
                self._upsert_object(ev)
            elif t == "DocumentCreated":
                self._upsert_document(ev)
            elif t == "SourceCreated":
                # #330 parity with apply(): SourceCreated was dropped by rebuild.
                self._upsert_source(ev)
            # ConfidenceChanged: no graph effect (audit-only event)

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

        # Pass 2: create edges for all operators + provenance/entity wiring
        # (shared _upsert_point_edges — single source of truth with apply, #330).
        for ev in events:
            ev = self._norm(ev)
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
                self._upsert_point_edges(p)

        node_count = self.g.query(
            "MATCH (n:Point) RETURN count(n)"
        ).result_set[0][0]
        edge_count = self.g.query(
            "MATCH ()-[r]->() RETURN count(r)"
        ).result_set[0][0]
        return {"events": len(events), "nodes": node_count, "edges": edge_count}

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
        ("Document", "id"), ("Source", "id"),
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

        Embedded (redislite) note (#522): the is_operator RANGE index is
        intentionally NOT created on embedded DBs. redislite merges
        per-property indexes into a composite whose is_operator entries are
        written with the FIRST process's type encoding; a later process
        reopening the same DB file inherits a stale composite and
        `n.is_operator = false` (which the query planner routes through the
        index) silently matches ZERO rows — verified on the crash-recovery
        reopen path (test_crash_recovery). The full label scan for
        `= false` is correct on embedded; docker/server FalkorDB keeps the
        index for the Node By Index Scan perf win.
        """
        # ── Range indexes (always safe, pre-4.x compatible) ──
        # NOTE: the single `is_operator` index is NOT created here on
        # server mode — the composite (is_operator, lastDreamedAt) below
        # subsumes it (leftmost prefix serves `n.is_operator = false`), and
        # FalkorDB rejects a composite containing an already-indexed
        # attribute ("Attribute 'is_operator' is already indexed"), which
        # silently disabled the lastDreamedAt composite (the epic-903
        # staleness-ranking index). Embedded skips is_operator entirely
        # (#522 stale-bool-type repair).
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

        # ── lastDreamedAt freshness index (epic 903-C2, #1240) ──
        # Powers the stale-first scheduler's staleness ranking
        # (ORDER BY lastDreamedAt ASC, null = stalest). Composite
        # (is_operator, lastDreamedAt) on docker/server FalkorDB; embedded
        # (redislite) gets the plain lastDreamedAt index only — a composite
        # containing is_operator is #522-unsafe on embedded (stale bool type
        # table across reopen silently zeroes `= false` lookups; the repair
        # sweep below drops such composites on open). Idempotent +
        # AOF-replay-safe (CREATE INDEX survives AOF replay —
        # tests/test_embedded_concurrency.py:532).
        if getattr(self, "_is_embedded", False):
            dreamed_props = ("lastDreamedAt",)
        else:
            dreamed_props = ("is_operator", "lastDreamedAt")
        try:
            self.g.query(
                "CREATE INDEX FOR (n:Point) ON ("
                + ", ".join(f"n.{p}" for p in dreamed_props) + ")"
            )
        except Exception as e:
            msg = str(e).lower()
            if not getattr(self, "_is_embedded", False) \
                    and ("is_operator" in msg or "lastdreamedat" in msg):
                # Server mode only: single-property indexes from older
                # _ensure_indexes runs (plain `is_operator` OR plain
                # `lastDreamedAt`) block the composite — FalkorDB rejects a
                # composite containing an already-indexed attribute.
                # The composite subsumes both singles, so drop them and
                # retry once. (Idempotent: a later startup with the
                # composite present hits "already indexed" on the composite
                # and no-ops below.) Embedded is deliberately EXCLUDED — the
                # plain lastDreamedAt index there is the correct one and must
                # not be dropped/recreated on every reopen (churn).
                try:
                    for _single in ("is_operator", "lastDreamedAt"):
                        try:
                            self.g.query(f"DROP INDEX ON :Point({_single})")
                        except Exception:
                            pass  # no such single index — fine
                    self.g.query(
                        "CREATE INDEX FOR (n:Point) ON ("
                        + ", ".join(f"n.{p}" for p in dreamed_props) + ")"
                    )
                except Exception as e2:
                    msg2 = str(e2).lower()
                    if "already indexed" in msg2 or "already exists" in msg2:
                        pass  # composite already exists from prior startup
                    else:
                        import logging
                        logging.getLogger(__name__).error(
                            "Failed to create index on :Point(%s): %s",
                            ", ".join(dreamed_props), e2)
            elif "already indexed" in msg or "already exists" in msg:
                pass  # expected — index exists from prior startup
            else:
                import logging
                logging.getLogger(__name__).error(
                    "Failed to create index on :Point(%s): %s",
                    ", ".join(dreamed_props), e)

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

        # ── Document range indexes (#125 — structural queries filter by kind) ──
        for prop in ("id", "documentKind"):
            try:
                self.g.query(f"CREATE INDEX FOR (n:Document) ON (n.{prop})")
            except Exception as e:
                msg = str(e).lower()
                if "already indexed" in msg or "already exists" in msg:
                    pass
                else:
                    import logging
                    logging.getLogger(__name__).error(
                        "Failed to create index on Document.%s: %s", prop, e)

        # ── Range indexes on canonical entity keys (issue #327) ──
        # Point/Document are created above; these enable index-backed
        # _resolve_entity lookups and faster name/url/eventId-keyed MERGE
        # upserts. Deliberately NO status/op_type/kind-field indexes — a
        # measured 3.15x write slowdown on Point upserts with status/op_type
        # outweighs the batch-read benefit (see issue #522).
        for label, props in (("Subject", ("id", "name")),
                             ("Object", ("id", "name")),
                             ("Event", ("eventId",)),
                             ("Source", ("id", "url"))):
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

        # ── Full-text & vector indexes require FalkorDB 4.x+ (#7779) ──
        _ver = getattr(self, '_falkordb_version', None)
        if _ver is None or _ver[0] >= 4:
            # ── Full-text indexes ──
            for label, fields in [("Point", ["content"]),
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
                                  ("Document", ["_searchText"])]:  # #125 Document FTS
                try:
                    fields_sql = ", ".join(f"'{f}'" for f in fields)
                    self.g.query(f"CALL db.idx.fulltext.createNodeIndex('{label}', {fields_sql})")
                except Exception as e:
                    msg = str(e).lower()
                    if "already" in msg:
                        if label == "Event":
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
                try:
                    self.g.query(
                        "CALL db.idx.vector.createNodeIndex('Point', 'embedding', 384, 'HNSW')"
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
                                "OPTIONS {dimension: 384, similarityFunction: 'cosine'}"
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

    def backfill_document_search_text(self) -> int:
        """#125: set _searchText=title on Documents missing it (idempotent).

        Covers pre-existing Documents created before the capture-fields change.
        Returns the number of Documents backfilled.
        """
        rows = self.g.query(
            "MATCH (d:Document) WHERE d._searchText IS NULL "
            "SET d._searchText = coalesce(d.title, '') "
            "RETURN count(d)"
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
            # client as explicitly closed and stay a strict no-op (a bare
            # db.close() is a redis-py pool disconnect that leaves the
            # server + socket intact). Host-mode db (no _t_close) keeps the
            # plain close.
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
        if db is not None and atexit_fast_close(getattr(db, "client", db)):
            self._closed = True
            return
        self.close()

    def __enter__(self) -> "FalkorProjection":  # noqa: UP037
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _revise_point(self, ev: dict, set_updated_at: bool = False) -> None:
        """Apply PointRevised event — update content, context, and re-compute embedding."""
        new_content = ev.get("new_content")
        new_context = ev.get("new_context")  # noqa: F841
        # #331 (review r3): NO event_id fallback — parity with _apply_one
        # (fold is the single source of truth, module contract).
        # #331 (review r4): str-only ids.
        pid = ev.get("id")
        if not isinstance(pid, str):
            # Malformed PointRevised — skip rather than crash (issue #325)
            return
        params: dict = {"id": pid, "c": new_content}

        # Re-compute embedding when content changes (even to empty — wipe stale).
        # Always set params["embedding"] so SET overwrites any stale value;
        # on compute failure, set to None rather than preserving old embedding (#19).
        if new_content is not None:
            try:
                from tortoise.embeddings import compute_embedding
                emb = compute_embedding(new_content) if new_content else None
                params["embedding"] = emb  # None = wipe stale embedding for empty content
            except Exception:
                params["embedding"] = None  # wipe stale embedding on failure (#19)

        set_clauses = ["n.content = coalesce($c, n.content)"]
        # Phase 2 #49: context removed — new_context no longer written
        if "embedding" in params:
            set_clauses.append("n.embedding = $embedding")
        if set_updated_at:
            set_clauses.append("n.updatedAt = $now")
            params["now"] = _now_iso()

        self.g.query(
            f"MATCH (n:Point {{id:$id}}) SET {', '.join(set_clauses)}",
            params=params,
        )

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
        ALL four factor-extraction call sites.
        """
        # Query 1: all operator IDs and types (single query, O(1) round-trip).
        # #689: retracted operators never feed EP factors.
        # #780: draft operators never feed EP factors (unless opted in).
        # Shared predicate from tortoise/live.py — one definition of the
        # live-only rule across all four factor-extraction call sites.
        draft_o = f"AND {_live_only('o.status', include_draft)}" if not include_draft else ""
        draft_c = f"AND {_live_only('c.status', include_draft)}" if not include_draft else ""
        op_rows = self.g.query(
            "MATCH (o:Point) WHERE o.is_operator = true "
            "AND (o.status IS NULL OR o.status <> 'retracted') "
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
            "AND (c.status IS NULL OR c.status <> 'retracted') "
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
