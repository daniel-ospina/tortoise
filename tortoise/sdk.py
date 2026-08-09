"""Tortoise SDK — Layer 1 facade for Tortoise epistemic graph interaction.

Wraps FalkorProjection (Docker/server FalkorDB by default, embedded via path argument).
Lazy-opens on first call. Returns structured dicts, never raw FalkorDB result sets.
"""
from __future__ import annotations

import hashlib
import json as _json
import logging
import re
from typing import Any

from .domain_loader import known_kinds, register_kind
from .ids import ulid
from . import monitoring
from .projection import FalkorProjection
from .quota import MAX_EXTRACTIONS_PER_TURN, MAX_SESSION_TURNS
import threading

# P0 Group 3: register custom kinds for diary + checkpoint
register_kind("diary")
register_kind("checkpoint-item")
register_kind("option")    # used by file_decision (#133)
register_kind("evidence")  # used by file_decision (#133)

# Valid status values for Point nodes (used by update_point status validation)
# #432: claim lifecycle vocabulary — draft → live → retracted → superseded
# (plus outdated/archived). challenged is a DERIVED condition (NAND operator
# edge on a live point), NOT a stored status.
POINT_STATUS_VALUES = frozenset({'draft', 'live', 'retracted', 'superseded', 'outdated', 'archived'})

# #432: declarative spec for the retract/supersede transition guards. NOT
# consulted by update_point per-call (update_point only promotes draft→live);
# every claim transition is observable via a Task 3 emit hook, and no
# transition can slip through update_point unemitted.
_ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    # P2 (code-review): guards allow draft→retracted/superseded (a draft point
    # can be terminal before ever going live); keep the declarative spec
    # aligned with the retract/supersede guards.
    "draft": frozenset({"live", "retracted", "superseded"}),  # promote + terminal
    "live": frozenset({"retracted", "superseded"}),    # via retract_point / supersede_point
    "retracted": frozenset(),                            # terminal
    "superseded": frozenset(),                           # terminal
    "outdated": frozenset({"retracted"}),               # outdated stays a flag; retract allowed
    "archived": frozenset(),                             # terminal (reserved — no v1 write path)
}

# Event types that write to the :GraphEvent store (#432). Other types
# (e.g. PointRevised) only write to the JSONL event log (#548).
_GRAPH_EVENT_TYPES = frozenset({
    "PointAdded",
    "OperatorAdded",
    "PointRetracted",
    "PointSuperseded",
    "OperatorAnnotated",
})


def _raise_update_point_status_error(proj, id: str) -> None:
    """#432: error path for the update_point draft→live promote guard.

    Runs a diagnostic existence read ONLY when the guarded SET returned no
    rows, so the happy path stays a single round trip. Missing point →
    ValueError matching the historical missing-point behavior; present but
    not draft → illegal-transition ValueError.
    """
    exists = proj.g.query(
        "MATCH (n:Point {id:$id}) RETURN count(n)", params={"id": id},
    ).result_set[0][0]
    if not exists:
        raise ValueError(f"No point {id!r}")
    raise ValueError(
        f"Illegal status transition — update_point only promotes draft→live; "
        f"use retract_point()/supersede_point() for lifecycle transitions"
    )

_logger = logging.getLogger(__name__)


def _sanitize_props(props: dict, *, reject_id: bool = False) -> dict:
    """#329: reject server-managed fields on tenant write surfaces.

    ``sourcePath``/``source_path`` are server-filesystem fields consumed by the
    operator ``--upgrade-all`` path (projection maps ``source_path`` →
    ``d.sourcePath`` via ``_DOCUMENT_HANDLED``); a tenant setting them turns the
    graph into a file-read oracle. ``id`` overrides on entity surfaces mutate
    node identity / mint tenant-chosen Document ids. Both are rejected with a
    clear ValueError (fail-closed). ``api.add_document``'s explicit
    ``source_path`` parameter is UNTOUCHED — this only guards props passthrough.
    """
    props = dict(props)
    for key in ("sourcePath", "source_path"):
        if key in props:
            raise ValueError(
                f"{key!r} is a server-managed field and cannot be set via props."
            )
    if reject_id and "id" in props:
        raise ValueError("'id' is server-managed and cannot be set via props.")
    return props


def _coerce_props(props: dict) -> dict:
    """Flatten a nested 'props' dict into top-level keyword props, in place.

    The MCP server passes props= through as-is (shallow copy, no flatten), so
    this helper is the single place that handles both conventions. Direct SDK
    callers naturally mirror the MCP tool signature and pass props={"k": v}.
    FalkorDB rejects non-primitive property values, so a dict-valued 'props'
    keyword would otherwise fail with
    "Property values can only be of primitive types". Accept both conventions:

      - props={"k": v}  -> k, v merged into top-level props
      - props=None       -> no-op (MCP convention for absent props)
      - flattened kwargs -> unchanged

    A non-dict, non-None 'props' value (e.g. a string) is preserved as a literal
    property named 'props' — scalars are legal FalkorDB property values.
    """
    nested = props.pop("props", None)
    if isinstance(nested, dict):
        # Explicit top-level kwargs are the caller's more specific intent —
        # they win over nested props on collision (mirrors the MCP server,
        # where explicit tool args override user-supplied props).
        props.update({k: v for k, v in nested.items() if k not in props})
    elif nested is not None:
        # Scalar 'props' value — restore as a literal property.
        props["props"] = nested
    return props


# ── ULID validation (Issue #52) ──
# Canonical format (from tortoise/ids.py): <timestamp-hex>-<uuid12>
_ULID_RE = re.compile(r"^[0-9a-f]+-[0-9a-f]{12}$")
# Standard Crockford base32 ULID (26 chars) — recognized as valid
_CROCKFORD_ULID_RE = re.compile(r"^[0-7][0-9A-HJKMNP-TV-Z]{25}$", re.IGNORECASE)


def _is_ulid(s: str) -> bool:
    """Return True if *s* matches a valid ULID format (canonical or Crockford)."""
    return bool(_ULID_RE.match(s) or _CROCKFORD_ULID_RE.match(s))


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _to_iso_utc(value) -> str:
    """Normalize a datetime or ISO-8601 string to UTC ISO-8601 (with +00:00).

    AgentSession startedAt values are stored via
    ``datetime.now(timezone.utc).isoformat()`` (e.g.
    ``2026-08-07T01:20:50.123456+00:00``), so a canonical, timezone-stripped-
to-UTC string keeps ``>=`` / ``<=`` lexicographic comparisons valid regardless
of the caller's local timezone or whether they passed ``Z`` or an offset.
    """
    from datetime import datetime, timezone
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        return dt.isoformat()
    # ISO-8601 string — normalize any timezone/offset to UTC. A naive string
    # (no offset suffix) is treated as UTC, mirroring the naive-datetime
    # branch — NOT as local time (which would shift the filter window by the
    # caller's offset; #243/#244 review).
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _save_progress(progress_file: str, directory: str, total: int, processed: int,
                   ingested: int, updated: int, skipped: int, failed: int,
                   errors: list[dict],
                   completed_files: list[str] | None = None) -> None:
    """Save batch indexing progress for resumability."""
    from datetime import datetime, timezone
    try:
        with open(progress_file, 'w') as f:
            _json.dump({
                "started": datetime.now(timezone.utc).isoformat(),
                "directory": directory,
                "total_files": total,
                "processed": processed,
                "ingested": ingested,
                "updated": updated,
                "skipped": skipped,
                "failed": failed,
                "completed_files": completed_files or [],
                "errors": errors[-20:],  # keep last 20 errors
            }, f, indent=2)
    except Exception:
        pass  # progress file is best-effort


# Module-level cached registry for kind expansion
_registry_cache: "PackRegistry | None" = None
_registry_lock = threading.Lock()


def _get_kind_expander():
    """Return cached PackRegistry with pre-computed expansion table."""
    global _registry_cache
    if _registry_cache is None:
        with _registry_lock:
            if _registry_cache is None:
                from .pack_registry import PackRegistry
                from pathlib import Path as _Path
                packs_dir = _Path(__file__).resolve().parent.parent / "packs"
                _registry_cache = PackRegistry(packs_dir)
                _registry_cache.load_all()
    return _registry_cache


class TortoiseSDK:
    """Layer 1 facade for Tortoise epistemic graph interaction.

    Args:
        db_path: Optional path to FalkorDBLite database file (None = use TORTOISE_DB_URI env var).
        namespace: Optional namespace for graph-name isolation.
        event_log_path: Optional path to JSONL event log. When set, SDK write
            paths append events so rebuild_all can restore SDK-created points (#548).
            If None, no events are emitted (backward-compatible).

    Precedence: an explicitly-provided db_path wins over the TORTOISE_DB_URI
    env var. This lets tests/fixtures force a temp embedded DB even when a
    shared test URI is set in the environment (#139).
    """

    def __init__(self, db_path: str | None = None, *, namespace: str | None = None,
                 event_log_path: str | None = None):
        import os, re
        db_uri = os.environ.get("TORTOISE_DB_URI")
        if db_uri and db_path is None:
            self._db_path = None
            self._db_uri = db_uri
        else:
            # P0: Crash early if running in production with no database configured.
            # Embedded redislite has no persistent volume → all data lost on deploy.
            # Must evaluate BEFORE resolve_db_path() fills in the default.
            if not db_uri and not db_path:
                if os.environ.get("FLY_APP_NAME"):
                    raise RuntimeError(
                        "TORTOISE_DB_URI is empty in production. "
                        "Set FALKORDB_PASSWORD (recommended: entrypoint.sh auto-constructs the URI) "
                        "or set TORTOISE_DB_URI directly. "
                        "See docs/infra-runbook.md §1."
                    )
                # Dev/CI: proceed, will use embedded redislite (tests set their own URI)
            # Task 6 wiring (issue #176): when neither a path nor a URI is
            # given, default to the canonical embedded path via resolve_db_path()
            # so the SDK is not blind to TORTOISE_DB_PATH.
            if db_path is None and not db_uri:
                from tortoise.config import resolve_db_path
                db_path = resolve_db_path()
            self._db_path = db_path
            self._db_uri = None
        # Namespace isolation: prefix graph name to segregate data
        if namespace is not None:
            if not re.match(r'^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$', namespace):
                raise ValueError(
                    f"Invalid namespace {namespace!r}. "
                    "Use alphanumeric, hyphens, underscores; max 64 chars."
                )
        self._namespace = namespace
        self._event_log_path = event_log_path
        self._event_log = None  # lazy-init EventLog (#548)
        self._proj: FalkorProjection | None = None
        self._ep = None  # lazy-init TortoiseEP
        self._evidence: dict[str, tuple[float, float]] = {}
        self._registry_g = None
        self._audit_logger = None
        # Dreaming (#85): dirty claim roots awaiting EP stabilization. Write
        # paths mark affected claims dirty; dream()/lazy-read consume them.
        self._dirty_roots: set[str] = set()
        self._dreamer = None  # lazy-init Dreamer

    def _get_proj(self) -> FalkorProjection:
        if self._proj is None:
            # Resolve the URI's own graph name first (used as the fallback
            # when no namespace is set — preserves the conftest per-session
            # test graph, #221).
            uri_graph: str | None = None
            if self._db_uri is not None:
                from urllib.parse import urlparse
                uri_graph = urlparse(self._db_uri).path.lstrip('/') or "tortoise"

            if self._namespace == "registry":
                # Control-plane SDK: shared registry main graph.
                graph_name = "registry_tortoise"
            elif self._namespace:
                if self._namespace.startswith(("test_", "tortoise_test")):
                    # Test namespace: isolate on a test-prefixed graph so the
                    # _assert_test_graph guard still passes (#221). Matches the
                    # historical {ns}_tortoise naming.
                    graph_name = f"{self._namespace}_tortoise"
                else:
                    # Team SDK: isolated team graph (matches provision's
                    # team_{team_id} namespace creation, #7886).
                    graph_name = f"team_{self._namespace}"
            else:
                # No namespace: honor the URI's own graph (the conftest
                # session graph for tests). Fixes #7886 regression that
                # hardcoded 'tortoise' and clobbered the test graph.
                graph_name = uri_graph or "tortoise"
            if self._db_uri is not None:
                # Multi-tenant isolation (#7886): pass the namespaced graph
                # name so tenants never share the URI's default graph.
                self._proj = FalkorProjection.from_uri(self._db_uri, graph_name=graph_name)
            else:
                self._proj = FalkorProjection(self._db_path, graph_name=graph_name)
        return self._proj

    def _get_event_log(self):
        """Lazy-init the EventLog for SDK event emission (#548).

        Returns None when no event_log_path was configured — callers MUST
        handle the None case before appending.
        """
        if self._event_log is None and self._event_log_path:
            from tortoise.log import EventLog
            self._event_log = EventLog(self._event_log_path)
        return self._event_log

    def test_guard(self) -> None:
        """Assert the connected graph is safe for destructive test teardowns.

        Raises RuntimeError if the graph appears to be a production graph
        (named ``tortoise`` or ``tortoise_restored_*``).  Test fixtures
        should call this before any ``MATCH (n) DETACH DELETE n``.

        Override with ``TORTOISE_ALLOW_PRODUCTION=1``.
        """
        import os
        if os.environ.get("TORTOISE_ALLOW_PRODUCTION") == "1":
            return

        proj = self._get_proj()
        graph_name = getattr(proj, "graph_name", None)
        if graph_name is None:
            graph_name = getattr(proj.g, "name", "unknown")

        # Block destructive ops on production graphs:
        #   tortoise             — the real graph
        #   tortoise_restored_*  — restored snapshots (precious)
        blocked = (
            graph_name == "tortoise"
            or graph_name.startswith("tortoise_restored")
        )
        if blocked:
            raise RuntimeError(
                f"SAFETY GUARD: Destructive operation blocked on graph "
                f"'{graph_name}'. This appears to be a production graph. "
                f"Use an isolated test graph (e.g. "
                f"'tortoise_test_calibration') instead. "
                f"Override with TORTOISE_ALLOW_PRODUCTION=1."
            )

    def _get_registry(self):
        """Return the control_plane registry graph handle (cached).

        Uses the existing db connection — no second FalkorDB connection.
        Registry graph name is namespace-scoped (``{ns}_control_plane``) so
        different namespaces never share registry state, and test graphs get
        an isolated name (``{ns}_{test_graph}_control_plane``) so parallel
        test runs stay independent (#135, #139).
        """
        if self._registry_g is None:
            proj = self._get_proj()
            graph_name = getattr(proj, "graph_name", None)
            ns = self._namespace or ""
            if graph_name and graph_name.startswith(("tortoise_test_", "test_")):
                # Keep the test prefix so test-graph guards still apply.
                registry_name = f"{ns}_{graph_name}_control_plane" if ns else f"{graph_name}_control_plane"
            elif ns:
                registry_name = f"{ns}_control_plane"
            else:
                registry_name = "control_plane"
            self._registry_g = proj.db.select_graph(registry_name)
            self._ensure_registry_indexes()
        return self._registry_g

    def _ensure_registry_indexes(self) -> None:
        """Create indexes on registry graph labels (idempotent)."""
        g = self._registry_g
        if g is None:
            return
        indexes = [
            ("Team", "name"),
            ("Membership", "team_id"),
            ("Membership", "user_id"),
            ("APIKey", "team_id"),
            ("APIKey", "key_hash"),
            ("APIKey", "key_prefix"),
            ("Invitation", "team_id"),
            ("Invitation", "token_hash"),
        ]
        for label, prop in indexes:
            try:
                g.query(f"CREATE INDEX FOR (n:{label}) ON (n.{prop})")
            except Exception:
                _logger.debug("Index may already exist: %s.%s", label, prop)

    # ── Core CRUD ─────────────────────────────────────────────────

    def _sync_tags(self, proj: FalkorProjection, pid: str, tags) -> None:
        """Reconcile TAGGED edges for a point against a tags value (#485).

        Idempotent: MERGE creates any missing :Tag nodes + TAGGED edges, and
        edges to tags no longer in the list are deleted. Only list values are
        synced — matching create_point's behavior, a non-list tag value is
        stored as a plain property but gets no edges (and leaves existing
        edges untouched).
        """
        if not isinstance(tags, list):
            return
        for tag in tags:
            proj.g.query(
                "MATCH (p:Point {id:$pid}) "
                "MERGE (t:Tag {name:$tag}) "
                "MERGE (p)-[:TAGGED]->(t)",
                params={"pid": pid, "tag": tag},
            )
        # Delete edges to tags no longer in the list (diff via graph read to
        # avoid list-parameter IN-clause portability concerns on FalkorDB).
        stale = proj.g.query(
            "MATCH (p:Point {id:$pid})-[:TAGGED]->(t:Tag) RETURN t.name",
            params={"pid": pid},
        ).result_set
        removed = 0
        for row in stale:
            if row[0] not in tags:
                proj.g.query(
                    "MATCH (p:Point {id:$pid})-[r:TAGGED]->(t:Tag {name:$tag}) "
                    "DELETE r",
                    params={"pid": pid, "tag": row[0]},
                )
                removed += 1
        # GC orphaned :Tag nodes when the sync actually removed edges — tag
        # removal can leave a Tag with no TAGGED referrers (#485). Skipped on
        # create_point (no stale edges there), so new-point creation never pays
        # the scan.
        if removed:
            proj.g.query("MATCH (t:Tag) WHERE NOT (t)<-[:TAGGED]-() DELETE t")

    # ── Events: cursor-based poll (Task 5) ────────────────────────────

    @staticmethod
    def _encode_cursor(seq: int) -> str:
        """Opaque cursor: base64url JSON {v:1, seq:N} — ONE format for every
        cursor incl. the empty graph ({v:1, seq:0}). Plan-review P2."""
        import base64
        import json

        raw = json.dumps({"v": 1, "seq": int(seq)}, separators=(",", ":"))
        return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")

    @staticmethod
    def _decode_cursor(cursor: str) -> int:
        """Decode an opaque cursor → seq. Raises ValueError('invalid cursor')."""
        import base64
        import json

        try:
            raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
            data = json.loads(raw)
            if data.get("v") != 1 or "seq" not in data:
                raise ValueError
            seq = int(data["seq"])
            if seq < 0:  # P2 (Qwen): negative cursors must not bypass expiry
                raise ValueError
            return seq
        except Exception:  # noqa: BLE001
            raise ValueError("invalid cursor") from None

    def events_poll(self, after: str | None = None, types: list[str] | None = None,
                    limit: int = 100) -> dict:
        """Poll graph/claim events after a cursor (at-least-once; idempotent on replay).

        Returns {"events": [payload dicts ordered by seq], "next_cursor": opaque}.
        after=None → tail (oldest retained). Expired cursor → ValueError(
        'cursor expired — replay from tail'); malformed → ValueError('invalid cursor').
        Types are validated against the EventCodec registry (unknown → ValueError).
        Events live in THIS SDK's graph namespace (the team partition).
        """
        from .event_store import read_after

        after_seq = 0 if after is None else self._decode_cursor(after)
        if types:
            from .shared_state.events import event_types

            registered = event_types()
            unknown = [t for t in types if t not in registered]
            if unknown:
                raise ValueError(f"unknown event type: {unknown[0]}")
        proj = self._get_proj()
        # Lazy retention (plan-review P2 / Task 6 readOnlyHint tension):
        # maintenance purge at most once per TORTOISE_EVENT_RETENTION_INTERVAL
        # per process, so steady-state polls are read-only. Best-effort — a
        # purge failure never blocks the poll.
        self._maybe_purge_events(proj)
        # Expired-cursor check: a NON-ZERO cursor pointing below the graph's
        # min seq was purged/compacted. after_seq == 0 is the "from the start"
        # sentinel (after=None) — it never expires, it just returns all
        # retained events.
        if after_seq != 0:
            # Watermark: first_seq on GraphEventMeta (maintained by purges) —
            # a cursor below it was purged/compacted → expired (410). Works
            # even when the graph is empty after a full purge.
            rows = proj.g.query(
                "MATCH (m:GraphEventMeta) RETURN m.first_seq"
            ).result_set
            first_seq = rows[0][0] if rows and rows[0][0] is not None else None
            if first_seq is not None and after_seq < int(first_seq):
                raise ValueError("cursor expired — replay from tail")
        evs = read_after(proj, after_seq, types=types, limit=limit)
        last = evs[-1]["seq"] if evs else after_seq
        return {"events": evs, "next_cursor": self._encode_cursor(last)}

    _EVENT_PURGE_ATTR = "_tortoise_last_purge"
    _EVENT_PURGE_LAST: float = 0.0  # process-level gate (P1 review fix)

    def _maybe_purge_events(self, proj) -> None:
        """Best-effort, interval-gated retention purge (see events_poll).

        Runs at most once per TORTOISE_EVENT_RETENTION_INTERVAL seconds per
        PROCESS (module-global monotonic — NOT per-projection: hosted REST/MCP
        build a fresh SDK+projection per request, so a per-projection gate
        would fire the purge on EVERY poll). Reads config via env with
        defaults (30d retention, 500k cap, 3600s interval).
        """
        import os
        import time

        interval = int(os.environ.get("TORTOISE_EVENT_RETENTION_INTERVAL", "3600"))
        now = time.monotonic()
        if now - TortoiseSDK._EVENT_PURGE_LAST < interval:
            return
        TortoiseSDK._EVENT_PURGE_LAST = now
        try:
            from .event_store import purge_expired, purge_overflow

            days = int(os.environ.get("TORTOISE_EVENT_RETENTION_DAYS", "30"))
            cap = int(os.environ.get("TORTOISE_EVENT_MAX_PER_TEAM", "500000"))
            purge_expired(proj, retention_days=days)
            purge_overflow(proj, max_events=cap)
        except Exception:  # noqa: BLE001 — best-effort
            _logger.warning("event retention purge failed — continuing", exc_info=True)

    def _emit_event(self, type_: str, payload: dict | None = None, *,
                    point: dict | None = None,
                    id: str | None = None, **extra) -> None:
        """Unified event emission: JSONL rebuild log (#548) + graph event store (#432).

        Both stores are best-effort — failures log and continue (never crash
        the mutation).

        Two call styles supported:
        1. ``_emit_event("PointAdded", {"id": ..., ...})``
           — #432: domain payload for the :GraphEvent store.
        2. ``_emit_event("PointAdded", point=point_dict)``
           — #548: full point snapshot for JSONL rebuild replay.
        3. ``_emit_event("PointRevised", id=pid, **props)``
           — #548: id + extra fields for JSONL.

        **Graph event store (#432):** written when *type_* is in
        ``_GRAPH_EVENT_TYPES`` (PointAdded, OperatorAdded, PointRetracted,
        PointSuperseded, OperatorAnnotated). The payload is taken from
        *payload* if given, otherwise synthesized from ``point["id"]`` or
        *id* + *extra*.

        **JSONL event log (#548):** written when *point* is provided (cleaned
        and appended as the ``"point"`` key) or *id* is provided. Events with
        neither are skipped (nothing meaningful to log). The full point
        snapshot is needed for ``rebuild_all`` replay.
        """
        # ── Graph event store (#432) ──────────────────────────────
        if type_ in _GRAPH_EVENT_TYPES:
            graph_payload = payload
            if graph_payload is None:
                if point is not None:
                    graph_payload = {"id": point.get("id")}
                elif id is not None:
                    graph_payload = {"id": id, **extra}
                else:
                    graph_payload = {}
            try:
                from .event_store import append_event, ensure_event_schema, next_seq
                proj = self._get_proj()
                ensure_event_schema(proj)
                seq = next_seq(proj)
                append_event(proj, seq, type_, graph_payload, self.ulid())
            except Exception:  # noqa: BLE001 — best-effort
                _logger.warning("event emission failed for %s — continuing", type_)

        # ── JSONL event log (#548) ─────────────────────────────────
        if point is None and id is None:
            return  # nothing meaningful to log
        log = self._get_event_log()
        if log is None:
            return
        from .ids import ulid, now_iso
        event: dict = {
            "event_id": ulid(),
            "ts": now_iso(),
            "type": type_,
            "initiated_by": "sdk",
            "projection_version": 2,
        }
        if point is not None:
            # Strip embedding — it is recomputed on replay by
            # _upsert_point_props (vecf32 serialization is fragile).
            # content_hash is also stripped — it is derived from content.
            clean = {k: v for k, v in point.items()
                     if k not in ("embedding", "content_hash")}
            # Operators may not store 'content' as a node property (#548);
            # _upsert_point_props requires it — synthesize a fallback.
            if "content" not in clean:
                op_type = clean.get("op_type", "IMPL")
                op_inputs = (point.get("operator") or {}).get("inputs", [])
                clean["content"] = f"{op_type}({', '.join(op_inputs)})"
            # Similarly, operators may lack 'pointKind' — default to empty.
            if "pointKind" not in clean:
                clean["pointKind"] = ""
            event["point"] = clean
        if id is not None:
            event["id"] = id
        event.update(extra)
        try:
            log.append(event)
        except Exception as exc:
            # The graph mutation already succeeded — a log-write failure must
            # not crash the caller or pretend the write failed. Rebuild parity
            # is best-effort here: rebuild_all's graph snapshot catches any
            # point missing from the log on the next rebuild (#548).
            _logger.warning(
                "failed to append %s event to SDK log %s: %s",
                type_, self._event_log_path, exc,
            )

    def create_point(self, kind: str, content: str, **props) -> dict:
        """Create a new Point node. Raises ValueError if kind is invalid.

        Set dedup=True for idempotent creation (matches by content hash).
        """
        self._validate_kind(kind)
        _coerce_props(props)  # accept MCP-style nested props= dict (#218)
        # #329: server-managed fields rejected on the props passthrough
        # (the explicit-id path via props.pop("id") below is preserved for operators)
        props = _sanitize_props(props)
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        proj = self._get_proj()

        # #49 Phase 2: context is REMOVED — raise TypeError if passed
        if "context" in props:
            raise TypeError(
                "create_point() got unexpected keyword argument 'context'. "
                "Context has been removed. Use pointKind for filtering, "
                "anchors for EP scoping, extractedFrom for provenance. See #49."
            )

        # Calibration: pop credibility before storing as node property
        credibility = props.pop("credibility", None)
        # Always compute and store content hash — dedup flag only gates the
        # existing-point lookup, not hash persistence (fix #80).
        ch = _content_hash(content)
        props["content_hash"] = ch
        # Idempotency guard: dedup by content hash when requested
        dedup = props.pop("dedup", False)
        if dedup:
            ch = _content_hash(content)
            # P1 #49: dedup by content_hash + pointKind (NOT context, which is no longer written)
            existing = proj.g.query(
                "MATCH (n:Point {content_hash:$ch}) "
                "WHERE (n.is_operator IS NULL OR n.is_operator = false) "
                "AND n.pointKind = $kind "
                "RETURN n.id",
                params={"ch": ch, "kind": kind},
            ).result_set
            if existing:
                pid = existing[0][0]
                # Existing point already stores content_hash — don't re-write it
                # (would make the `if props:` guard always truthy and bump
                # updatedAt on every dedup hit, #80 review).
                props.pop("content_hash", None)
                if credibility is not None:
                    _logger.warning(
                        "credibility=%r ignored — point %s already exists and dedup=True",
                        credibility, pid)
                if props:
                    # Only touch the existing point when the caller passed
                    # other props — a pure dedup hit (no props) must not bump
                    # updatedAt or re-trigger EP dirty-marking (#490 review
                    # P2-1: re-capture would churn confidence for every point).
                    props["updatedAt"] = now
                    self.update_point(pid, **props)
                return self.get_point(pid)

        # Issue #52 — warn when caller passes an explicit non-ULID id
        explicit_id = props.pop("id", None)
        if explicit_id is not None:
            if not _is_ulid(explicit_id):
                _logger.warning(
                    "create_point received non-ULID id=%r — canonical format is "
                    "<timestamp-hex>-<uuid12>. This will override the auto-generated ULID. "
                    "Prefer omitting 'id' to use auto-generated ULID.",
                    explicit_id,
                )
            pid = explicit_id
        else:
            pid = ulid()
        # Points enter as draft, go live when first edge is created (#131)
        status = props.pop("status", "draft")

        # Compute embedding (Phase 1A, #7698) — stored as Point property
        embedding = None
        try:
            from .embeddings import compute_embedding
            embedding = compute_embedding(content)
        except Exception:
            pass  # Graceful — embedding is optional

        proj.g.query(
            "CREATE (n:Point {id:$id, content:$c, pointKind:$k, "
            "is_operator:false, status:$st, createdAt:$now, updatedAt:$now}) "
            "SET n.embedding = vecf32($embedding)",
            params={"id": pid, "c": content, "k": kind, "st": status, "now": now,
                    "embedding": embedding},
        )
        # Tag handling: create :Tag nodes + TAGGED edges (#215, #485)
        tags = props.get("tags") or []
        if isinstance(tags, list):
            self._sync_tags(proj, pid, tags)
        for key, val in props.items():
            proj.g.query(
                "MATCH (n:Point {id:$id}) SET n += $props",
                params={"id": pid, "props": {key: val}},
            )
        # P1-1: Ontology v2.1 — link Point → Source via extractedFrom
        if props.get("extractedFrom"):
            proj._link_source(pid, props["extractedFrom"])
            # Inheritance gate dirty-mark: a freshly-sourced point is always
            # inherit-eligible on the next EP run (no interval wait, #398).
            self._invalidate_inheritance_gate([pid])

        # Apply credibility baseline (only on new creation, not dedup)
        if credibility is not None:
            tier_map = {
                "gold": (10, 1), "T0": (10, 1), 0: (10, 1),
                "high": (5, 1), "T1": (5, 1), 1: (5, 1),
                "medium": (3, 1), "T2": (3, 1), 2: (3, 1),
                "low": (2, 1), "T3": (2, 1), 3: (2, 1),
                "unverified": (1.1, 1), "T4": (1.1, 1), 4: (1.1, 1),
            }
            alpha, beta = tier_map.get(credibility, (1, 1))
            self.set_point_baseline(pid, alpha, beta)
        # Dreaming (#85): a new point can carry confidence-affecting props;
        # mark it dirty so the next dream/lazy-read stabilizes it.
        self._mark_dirty([pid])
        # #432+#548 unified: domain payload + full point snapshot for both
        # the :GraphEvent store (subscriptions/poll) and JSONL (rebuild_all).
        self._emit_event("PointAdded", {"id": pid, "kind": kind, "content_hash": ch},
                         point=self.get_point(pid))
        return self.get_point(pid)

    def create_or_update_point(self, kind: str, content: str, **props) -> dict:
        """Idempotent create/update — matches by content hash."""
        return self.create_point(kind, content, dedup=True, **props)

    # ── Resolution helper (Issue #52) ──

    def resolve_id(self, id_str: str) -> dict | None:
        """Resolve any Point ID (legacy / numeric / ULID) to the canonical point.

        Returns the Point dict if found, None otherwise.

        Strategy:
        1. Exact match on Point.id
        2. If the id looks like a numeric reference, search by content/properties
           (best-effort — legacy numeric IDs may not have explicit mappings yet)

        Non-destructive — read-only operation.

        Limitations:
        - For legacy prefix IDs (letta-*, op-*, etc.) with no exact match,
          there is currently no migration mapping to a canonical ULID.
          This is a known gap covered by docs/migrations/id-normalization-plan.md.
        - The resolution is exact-id-first; fuzzy matching is future work.
        """
        proj = self._get_proj()

        # 1. Exact match
        rows = proj.g.query(
            "MATCH (n:Point {id: $id}) RETURN n.id, n.content, n.pointKind, n.status",
            params={"id": id_str},
        ).result_set
        if rows:
            return self.get_point(rows[0][0])

        # 2. If numeric, try finding a point whose properties reference it
        #    (best-effort — many numeric IDs are native node IDs and would have
        #     matched in step 1; this handles edge cases like internal refs)
        if id_str.isdigit():
            # Search for points whose content or any property contains the numeric ID
            rows = proj.g.query(
                "MATCH (n:Point) WHERE n.content CONTAINS $id_str "
                "RETURN n.id, n.content LIMIT 5",
                params={"id_str": id_str},
            ).result_set
            if rows:
                _logger.info(
                    "resolve_id: numeric %r not found as direct id; "
                    "returning best-match point %r", id_str, rows[0][0]
                )
                return self.get_point(rows[0][0])

        return None

    def capture_session(
        self,
        conversation: list[dict[str, str]],
        session_id: str | None = None,
        *,
        max_turns: int = MAX_SESSION_TURNS,
    ) -> dict:
        """Capture an agent session into the graph (#312 delta 4).

        Mirrors the hosted POST /v1/sessions logic minus quota/auth:
        turns become episodic Points keyed {session_id}_t{i} (deterministic +
        idempotent), decisions/claims become epistemic Points (content-hash
        dedup), plus a :Session node and an ontology-compliant
        :Event {eventKind:'sessionCaptured'} with aboutEvent edges.

        ``conversation`` is a list of {"role", "content"} dicts. Returns
        {"session_id", "turns", "extracted", "points": [...]}.
        """
        import uuid
        from datetime import datetime, timezone

        proj = self._get_proj()
        now = datetime.now(timezone.utc).isoformat()
        session_id = session_id or f"session_{uuid.uuid4().hex[:12]}"

        if len(conversation) > max_turns:
            raise ValueError(
                f"Session turn cap exceeded: {len(conversation)} > {max_turns}")

        proj.g.query(
            "MERGE (s:Session {id:$sid}) SET s.created_at=$now, s.turn_count=$tc",
            params={"sid": session_id, "now": now, "tc": len(conversation)},
        )

        decisions = [
            r"(?i)(?:let'?s|we will|we should|I will|I'm going to|decided|decision)\s+[^.!?]+[.!?]",
            r"(?i)(?:plan is|next steps?:|action item:)\s+[^.!?]+[.!?]",
        ]
        claims = [
            r"(?i)(?:I think|I believe|my understanding is|the problem is|the key insight)\s+[^.!?]+[.!?]",
            r"(?i)(?:evidence suggests|data shows|we found that|this means)\s+[^.!?]+[.!?]",
        ]

        # NOTE: this extraction loop (regexes + per-turn caps) is duplicated
        # from tortoise/hosted_api.py POST /v1/sessions. Divergences: the SDK
        # variant writes a `speaker` property on turn Points (delta 5) while
        # hosted adds quota/auth bounds; hosted has no speaker tag. SDK
        # TRUNCATES turn content > 5000 chars silently AND extracts from the
        # truncated text (stored-source parity — a phrase past the cut has no
        # home in any stored turn), hosted rejects > 5000 with 422 (Pydantic
        # field_validator failure) so its loop always sees <= 5000 chars:
        # extraction inputs align. role=None normalizes to "unknown" in the
        # SDK, hosted stores None as-is. Keep the two in sync when touching
        # either.
        extracted = []
        for i, turn in enumerate(conversation):
            # #721: same isinstance-first pattern as content below — an `or
            # "unknown"` fallback only fixes falsy roles, but TRUTHY non-string
            # roles (123, {"a": 1}) would pass raw and be stored as a
            # non-string `speaker` (contradicting the `speaker | string`
            # ontology row) — and a dict role could fail the Cypher write
            # mid-loop, leaving a partial session. Coerce via str() so the
            # speaker property is always a string; only None maps to "unknown".
            raw_role = turn.get("role")
            role = raw_role if isinstance(raw_role, str) else (
                "unknown" if raw_role is None else str(raw_role))
            raw = turn.get("content")
            # #721: defensive coercion — check isinstance FIRST so falsy
            # non-strings (0, False, {}, []) are not swallowed to "" by an
            # `or ""` fallback, then coerce via str() (0 -> "0", False ->
            # "False", [] -> "[]") before the write so the episodic point and
            # the extraction loop share one value. Only None maps to "".
            content = raw if isinstance(raw, str) else ("" if raw is None else str(raw))

            # Episodic turn point — deterministic id, structured speaker tag
            # (delta 5), content hash, session-scoped (never conflated across
            # sessions — #490).
            turn_id = f"{session_id}_t{i}"
            turn_text = f"[{role}] {content[:5000]}"
            proj.g.query(
                "MERGE (t:Point {id:$id}) "
                "SET t.content=$c, t.pointKind=$k, t.is_operator=false, "
                "    t.speaker=$speaker, "
                "    t.status=coalesce(t.status, $s), "
                "    t.createdAt=coalesce(t.createdAt, $now), "
                "    t.updatedAt=$now, t.content_hash=$ch",
                params={"id": turn_id, "c": turn_text, "k": "event",
                        "speaker": role, "s": "draft", "now": now,
                        "ch": _content_hash(turn_text)},
            )
            proj.g.query(
                "MATCH (s:Session {id:$sid}), (t:Point {id:$tid}) "
                "MERGE (s)-[:CONTAINS]->(t)",
                params={"sid": session_id, "tid": turn_id},
            )

            # Epistemic extraction (regex, same bounds as hosted). #721:
            # scan the STORED (truncated) turn text — the turn Point above
            # stores content[:5000], so the regexes must run on that same
            # slice. Scanning the full content would extract phrases past the
            # cut into session-wired Points whose source text exists in no
            # stored turn (broken provenance). Hosted can't hit this (422 on
            # content > 5000 before the loop); this keeps extraction input
            # aligned with what is actually stored.
            scan = content[:5000]
            n_dec = 0
            for pat in decisions:
                for match in re.finditer(pat, scan):
                    if n_dec >= MAX_EXTRACTIONS_PER_TURN:
                        break
                    n_dec += 1
                    text = match.group().strip()
                    p = self.create_point("decision", text[:5000], dedup=True)
                    pid = p["id"]
                    proj.g.query(
                        "MATCH (s:Session {id:$sid}), (p:Point {id:$pid}) "
                        "MERGE (s)-[:CONTAINS]->(p)",
                        params={"sid": session_id, "pid": pid},
                    )
                    extracted.append({"id": pid, "kind": "decision", "text": text[:200]})

            n_clm = 0
            for pat in claims:
                for match in re.finditer(pat, scan):
                    if n_clm >= MAX_EXTRACTIONS_PER_TURN:
                        break
                    n_clm += 1
                    text = match.group().strip()
                    p = self.create_point("statement", text[:5000], dedup=True)
                    pid = p["id"]
                    proj.g.query(
                        "MATCH (s:Session {id:$sid}), (p:Point {id:$pid}) "
                        "MERGE (s)-[:CONTAINS]->(p)",
                        params={"sid": session_id, "pid": pid},
                    )
                    extracted.append({"id": pid, "kind": "statement", "text": text[:200]})

        # Ontology episodic model (v3.1 §4.5/§3.2): Event + aboutEvent edges.
        try:
            event = self.create_event(
                f"session_{session_id}", "sessionCaptured",
                startedAt=now, endedAt=now, sessionId=session_id,
            )
            event_id = event.get("id") or event.get("eventId")
            for p in extracted:
                proj.create_about_edge(p["id"], event_id, "aboutEvent")
        except Exception as e:
            # Non-fatal — mirrors hosted behavior, but surface the failure so
            # silent event-log loss is visible (#721).
            _logger.warning(
                "capture_session: sessionCaptured Event/EventRecorded write "
                "failed (non-fatal) for session %s: %s", session_id, e,
                exc_info=True,
            )

        return {
            "session_id": session_id,
            "turns": len(conversation),
            "extracted": len(extracted),
            "points": extracted,
        }

    def update_point(self, id: str, **props) -> dict:
        """Update properties on an existing Point. Returns updated point dict.
        
        For :Object-labeled nodes, version is auto-incremented on every update.
        Status changes are validated against POINT_STATUS_VALUES.
        """
        proj = self._get_proj()
        _coerce_props(props)  # accept MCP-style nested props= dict (#218)
        # #329: id mutation + server-managed fields rejected
        props = _sanitize_props(props, reject_id=True)

        # #49 Phase 2: context is REMOVED — raise TypeError if passed
        if "context" in props:
            raise TypeError(
                "update_point() got unexpected keyword argument 'context'. "
                "Context has been removed. See #49."
            )

        # Validate status if present
        if 'status' in props and props['status'] not in POINT_STATUS_VALUES:
            raise ValueError(
                f"Invalid status {props['status']!r}. "
                f"Must be one of: {', '.join(sorted(POINT_STATUS_VALUES))}"
            )

        # #432 plan-review P1: update_point is non-status except the draft→live
        # promote (matches the create_operator promote). Any other status value
        # is rejected BEFORE the query — lifecycle transitions go through
        # retract_point()/supersede_point() (which emit events). This keeps
        # every claim transition observable via an emit hook.
        if 'status' in props:
            if props['status'] != 'live':
                raise ValueError(
                    "update_point only promotes draft→live — use "
                    "retract_point()/supersede_point() for lifecycle transitions"
                )

        # Check if node carries :Object label (entity node with version tracking)
        has_object = proj.g.query(
            "MATCH (n:Point:Object {id:$id}) RETURN count(n) > 0",
            params={"id": id},
        ).result_set[0][0]

        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()

        if has_object:
            if 'status' in props:
                # Promote guard folded INTO the WHERE clause (plan-review P2:
                # single round trip, no widened write window).
                res = proj.g.query(
                    "MATCH (n:Point:Object {id:$id}) "
                    "WHERE (n.status IS NULL OR n.status = 'draft') "
                    "SET n.status = 'live', n.updatedAt = $now, "
                    "n.version = coalesce(n.version, 0) + 1 RETURN n",
                    params={"id": id, "now": now},
                )
                if not res.result_set:
                    _raise_update_point_status_error(proj, id)
            else:
                proj.g.query(
                    "MATCH (n:Point:Object {id:$id}) "
                    "SET n += $props, n.version = coalesce(n.version, 0) + 1, n.updatedAt = $now",
                    params={"id": id, "props": props, "now": now},
                )
        else:
            if 'status' in props:
                # Promote guard folded INTO the WHERE clause (plan-review P2).
                res = proj.g.query(
                    "MATCH (n:Point {id:$id}) "
                    "WHERE (n.status IS NULL OR n.status = 'draft') "
                    "SET n.status = 'live', n.updatedAt = $now RETURN n",
                    params={"id": id, "now": now},
                )
                if not res.result_set:
                    _raise_update_point_status_error(proj, id)
            else:
                for key, val in props.items():
                    proj.g.query(
                        "MATCH (n:Point {id:$id}) SET n += $props",
                        params={"id": id, "props": {key: val}},
                    )
        # Tag sync (#485): keep TAGGED edges consistent with the n.tags
        # property — update_point previously set the property but left edges
        # stale, so query_points_by_tag missed updated points. Falsy tag
        # values (None, "") normalize to [] like create_point, so the
        # "clear tags" idiom removes edges instead of leaving them stale.
        if "tags" in props:
            self._sync_tags(proj, id, props["tags"] or [])
        # Dreaming (#85): property mutations can affect confidence.
        self._mark_dirty([id])
        result = self.get_point(id)
        # #548: emit PointRevised event for rebuild parity
        self._emit_event("PointRevised", id=id,
                         new_content=props.get("content"), **props)
        return result

    def delete_point(self, id: str) -> bool:
        """Delete a Point and its relationships. Returns True if found."""
        proj = self._get_proj()
        exists = proj.g.query(
            "MATCH (n:Point {id:$id}) RETURN count(n) > 0",
            params={"id": id},
        ).result_set[0][0]
        if not exists:
            return False
        # A tag's edge count can only change for this point's own TAGGED
        # edges — scope the orphan scan to that case (skips the global tag
        # scan on every untagged delete; #485).
        has_tag_edges = proj.g.query(
            "MATCH (n:Point {id:$id})-[:TAGGED]->() RETURN count(*) > 0",
            params={"id": id},
        ).result_set[0][0]
        proj.g.query("MATCH (n:Point {id:$id}) DETACH DELETE n", params={"id": id})
        # #548: emit PointRetracted event for rebuild parity (after delete,
        # so the graph mutation is committed before the event is written)
        self._emit_event("PointRetracted", id=id)
        # Tag GC (#485): delete orphaned :Tag nodes (no incoming TAGGED edges).
        # Idempotent — DETACH DELETE leaves count-0 tags behind that would
        # otherwise accumulate in list_tags.
        if has_tag_edges:
            proj.g.query("MATCH (t:Tag) WHERE NOT (t)<-[:TAGGED]-() DELETE t")
        # Dreaming (#85): deletion changes the graph structure around neighbors.
        self._mark_dirty([id])
        return True

    def delete_point_wrapped(self, id: str) -> dict:
        """Delete a Point. Returns dict for MCP tool consumption."""
        found = self.delete_point(id)
        return {"deleted": found, "id": id}

    # ── Invalidate / Supersede (#6999 GAP-12) ────────────────────

    def invalidate_point(self, id: str, corrected_by_id: str) -> dict:
        """Mark a Point outdated, linked to its replacement via CORRECTS edge.

        Validation contract (#330) — all checks run BEFORE any write so a
        failure can never leave a partial graph state:
        - id == corrected_by_id → ValueError (a self-CORRECTS edge poisons
          traversal/credibility chains).
        - old point missing (never existed or already deleted) →
          {"invalidated": False} with no writes (retry-friendly).
        - corrected_by point missing → ValueError (structural failure: would
          orphan an outdated point with no replacement).
        Re-invalidating a point that still EXISTS re-asserts (returns True,
        MERGE keeps a single CORRECTS edge).
        """
        from datetime import datetime, timezone
        proj = self._get_proj()
        if id == corrected_by_id:
            raise ValueError(
                f"invalidate_point: corrected_by cannot be the point itself ({id!r})"
            )
        old_exists = proj.g.query(
            "MATCH (n:Point {id:$id}) RETURN count(n) > 0", params={"id": id},
        ).result_set[0][0]
        if not old_exists:
            return {"invalidated": False, "id": id, "corrected_by": corrected_by_id}
        new_exists = proj.g.query(
            "MATCH (n:Point {id:$id}) RETURN count(n) > 0",
            params={"id": corrected_by_id},
        ).result_set[0][0]
        if not new_exists:
            raise ValueError(
                f"invalidate_point: corrected_by point {corrected_by_id!r} does not "
                f"exist — refusing to orphan outdated point {id!r}"
            )
        now = datetime.now(timezone.utc).isoformat()

        proj.g.query(
            "MATCH (n:Point {id:$id}) SET n.outdated = true, n.updatedAt = $now",
            params={"id": id, "now": now},
        )
        proj.g.query(
            "MATCH (a:Point {id:$new_id}), (b:Point {id:$old_id}) "
            "MERGE (a)-[:CORRECTS]->(b)",
            params={"new_id": corrected_by_id, "old_id": id},
        )
        # Dreaming (#85): invalidation changes the propagation graph.
        self._mark_dirty([id, corrected_by_id])
        return {"invalidated": True, "id": id, "corrected_by": corrected_by_id}

    def supersede_point(self, old_id: str, new_id: str) -> dict:
        """Atomically replace old Point with new — CORRECTS edge + outdated flag + edge transfer.

        Transfers all edges from the old point to the new point:
          - Operator edges (IMPL, NAND, hasPart) with idx
          - Plain structural edges (aboutSubject, aboutObject, aboutAction,
            aboutEvent, aboutPoint, aboutDocument, extractedFrom, etc.)
        Preserves edge type and idx (source vs target position).
        Leaves the old point outdated with only the CORRECTS edge from the new point.
        """
        from datetime import datetime, timezone
        proj = self._get_proj()
        now = datetime.now(timezone.utc).isoformat()

        # #432: transition guard — old point must exist, be a statement (not
        # an operator), and not already be terminal (mirrors the retract
        # guard; supersede is already multi-query so the read is cheap).
        guard = proj.g.query(
            "MATCH (n:Point {id:$id}) RETURN n.is_operator, n.status",
            params={"id": old_id},
        ).result_set
        if not guard:
            raise ValueError(f"No point {old_id!r}")
        is_op, cur = guard[0][0], guard[0][1]
        if is_op:
            raise ValueError(
                f"Point {old_id!r} is an operator — supersession is for statement points")
        if cur in ("retracted", "superseded", "archived"):
            raise ValueError(
                f"Point {old_id!r} is already terminal ({cur!r}) — supersession is terminal")

        # P1 (Qwen review): validate the NEW point too — it must exist, be a
        # statement, not be terminal, and differ from the old point. A missing /
        # self / terminal successor would terminalize the old point with no valid
        # replacement (phantom PointSuperseded).
        if old_id == new_id:
            raise ValueError("supersede_point: old_id and new_id must differ")
        new_guard = proj.g.query(
            "MATCH (n:Point {id:$id}) RETURN n.is_operator, n.status",
            params={"id": new_id},
        ).result_set
        if not new_guard:
            raise ValueError(f"No point {new_id!r}")
        n_is_op, n_cur = new_guard[0][0], new_guard[0][1]
        if n_is_op:
            raise ValueError(
                f"Point {new_id!r} is an operator — supersession target must be a statement")
        if n_cur in ("retracted", "superseded", "archived"):
            raise ValueError(
                f"Point {new_id!r} is already terminal ({n_cur!r}) — cannot supersede into it")

        # 0. #329: collect + validate ALL edge types BEFORE any mutation.
        #    The edge types are interpolated into query structure (no params
        #    possible) — an unvalidated type (e.g. from a crafted edge) is a
        #    Cypher injection primitive AND would cause a partial transfer.
        #    Validation strictly precedes the outdated-flag/CORRECTS writes so
        #    a failure leaves the graph untouched.
        edges_result = proj.g.query(
            "MATCH (op:Point {is_operator:true})-[r]->(old:Point {id:$old_id}) "
            "RETURN op.id, type(r), r.idx",
            params={"old_id": old_id},
        )
        from .security import validate_rel_type
        for row in edges_result.result_set:
            validate_rel_type(row[1])  # raises ValueError before any mutation

        # #432 Task 3: durable PointSuperseded event (append-before-mutation,
        # AFTER the guard + edge-type validation — P2 review fix: emitting
        # before validation produced phantoms on corrupt-edge data).
        self._emit_event("PointSuperseded", {"id": old_id, "new_id": new_id}, id=old_id)

        # 1. Mark old superseded + outdated + create CORRECTS edge (same as invalidate)
        # #432: status='superseded' alongside the legacy outdated=true flag
        # (back-compat for consumers reading the flag; #690 will consolidate).
        proj.g.query(
            "MATCH (n:Point {id:$id}) SET n.status = 'superseded', "
            "n.outdated = true, n.updatedAt = $now",
            params={"id": old_id, "now": now},
        )
        proj.g.query(
            "MATCH (a:Point {id:$new_id}), (b:Point {id:$old_id}) "
            "MERGE (a)-[:CORRECTS]->(b)",
            params={"new_id": new_id, "old_id": old_id},
        )

        # 2a. Transfer operator edges (IMPL, NAND, hasPart) — preserve provenance

        transferred = 0
        for row in edges_result.result_set:
            op_id, edge_type, idx = row[0], row[1], row[2]
            # Create new edge: operator → new point (same idx preserves source/target position)
            proj.g.query(
                f"MATCH (op:Point {{id:$op_id}}), (new:Point {{id:$new_id}}) "
                f"CREATE (op)-[:{edge_type} {{idx:$idx}}]->(new)",
                params={"op_id": op_id, "new_id": new_id, "idx": idx},
            )
            # Delete old edge (match by idx for precision)
            proj.g.query(
                f"MATCH (op:Point {{id:$op_id}})-[r:{edge_type} {{idx:$idx}}]->(old:Point {{id:$old_id}}) "
                f"DELETE r",
                params={"op_id": op_id, "idx": idx, "old_id": old_id},
            )
            transferred += 1

        # 2b. Transfer plain structural edges (#122) — about*, extractedFrom, wasDerivedFrom, etc.
        # These edges connect the Point to entities (Subject, Object, Source, etc.)
        structural_rels = [
            'aboutSubject', 'aboutObject', 'aboutAction', 'aboutEvent',
            'aboutPoint', 'aboutDocument', 'extractedFrom', 'wasDerivedFrom'
        ]
        for rel in structural_rels:
            struct_rows = proj.g.query(
                f"MATCH (old:Point {{id:$old_id}})-[r:{rel}]->(target) "
                f"RETURN id(target), target.id, labels(target)",
                params={"old_id": old_id},
            ).result_set
            for row in struct_rows:
                target_graph_id = row[0]  # FalkorDB internal node id — exact match
                # Create new edge: new point → same target (MERGE = idempotent, no dupes)
                proj.g.query(
                    f"MATCH (new:Point {{id:$new_id}}), (t) WHERE id(t) = $tid "
                    f"MERGE (new)-[:{rel}]->(t)",
                    params={"new_id": new_id, "tid": target_graph_id},
                )
                # Delete old edge (match by exact internal node id)
                proj.g.query(
                    f"MATCH (old:Point {{id:$old_id}})-[r:{rel}]->(t) WHERE id(t) = $tid "
                    f"DELETE r",
                    params={"old_id": old_id, "tid": target_graph_id},
                )
                transferred += 1

        # Dreaming (#85): supersede changes the propagation graph around both.
        self._mark_dirty([old_id, new_id])

        return {
            "invalidated": True,
            "id": old_id,
            "corrected_by": new_id,
            "edges_transferred": transferred,
        }

    def retract_point(self, id: str) -> dict:
        """Tombstone-retract a Point: status='retracted' (point stays in graph).

        #432: retraction is a TERMINAL state transition, not a deletion — the
        projection keeps the point with status='retracted' and default query
        surfaces exclude it (opt-in via include_retracted). Single atomic
        conditional query on the happy path; diagnostic read only on the error
        path.

        Raises ValueError if the point is missing, is an operator node, or is
        already terminal (retracted/superseded/archived).
        """
        from datetime import datetime, timezone
        proj = self._get_proj()
        # P1 (code-review): validate FIRST, then emit, then mutate — the emit
        # before the guard produced phantom PointRetracted events on the
        # NORMAL invalid-input path (missing / operator / terminal), which
        # poll consumers would see as retractions that never happened.
        row = proj.g.query(
            "MATCH (n:Point {id:$id}) RETURN n.is_operator, n.status",
            params={"id": id}).result_set
        if not row:
            raise ValueError(f"No point {id!r}")
        is_op, cur = row[0][0], row[0][1]
        if is_op:
            raise ValueError(
                f"Point {id!r} is an operator — retraction is for statement points")
        if cur in ("retracted", "superseded", "archived"):
            raise ValueError(
                f"Point {id!r} is already terminal ({cur!r}) — retraction is terminal")
        # #432 Task 3: durable PointRetracted event (append-before-mutation;
        # only after the input contract validates).
        self._emit_event("PointRetracted", {"id": id}, id=id)
        # P1 (Qwen review): CAS the SET — the WHERE re-checks terminal state so
        # a concurrent retract/supersede can't both pass validation and have a
        # terminal overwrite (retracted overwriting superseded, or vice versa).
        r = proj.g.query(
            "MATCH (n:Point {id:$id}) "
            "WHERE (n.status IS NULL OR NOT (n.status IN $terminal)) "
            "SET n.status = 'retracted', n.updatedAt = $now RETURN properties(n)",
            params={"id": id, "now": datetime.now(timezone.utc).isoformat(),
                    "terminal": ["retracted", "superseded", "archived"]})
        if not r.result_set:
            raise ValueError(
                f"Point {id!r} is already terminal — retraction is terminal")
        return r.result_set[0][0]  # updated node props (no trailing get_point round trip)

    # ── Operators ─────────────────────────────────────────────────

    def create_operator(self, op_type: str, source_id: str, target_ids: list[str],
                        label: str | None = None,
                        direction: str = "bidirectional") -> dict:
        """Create an operator Point with optional semantic label.

        Semantic-epistemic edge model (#7801):
          - op_type: IMPL or NAND (epistemic mechanism)
          - label: domain verb — "addresses", "hasPart", "opposes" (semantic layer)
          - direction: "bidirectional" (default) or "unidirectional" — explicit
            flag controlling EP back-propagation (ONTOLOGY v3.1 §3.1, §8).
            Default bidirectional (mutual) for all op types; pass
            "unidirectional" for a directed attack (no back-pressure).
          - Operator carries the label and direction; IMPL/NAND edges carry confidence via EP.
        """
        if op_type not in ("IMPL", "NAND", "composedOf", "decomposesInto", "contains", "wraps"):
            raise ValueError(
                f"op_type must be 'IMPL', 'NAND', or a part/whole type, got {op_type!r}"
            )
        # Direction default: bidirectional for all op types (product owner,
        # #753) — a NAND is logically "A and B can't both be true" (mutual).
        # An agent may explicitly pass "unidirectional" to declare a DIRECTED
        # attack (attacker's truth penalizes the target, no back-pressure).
        if direction is None:
            direction = "bidirectional"  # backward compat: explicit None = default
        if direction not in ("bidirectional", "unidirectional"):
            raise ValueError(
                f"direction must be 'bidirectional' or 'unidirectional', got {direction!r}"
            )
        pid = ulid()
        inputs = [source_id] + list(target_ids)
        proj = self._get_proj()

        # Validate all source/target Points exist FIRST (fail loudly, not
        # silently) — then emit, then mutate. P1 (code-review): emitting
        # before validation produced phantom OperatorAdded events on missing
        # inputs, visible to subscription poll consumers.
        existing = proj.g.query(
            "MATCH (n:Point) WHERE n.id IN $ids RETURN n.id",
            params={"ids": inputs},
        ).result_set
        existing_ids = {row[0] for row in existing}
        missing = [i for i in inputs if i not in existing_ids]
        if missing:
            raise ValueError(f"Cannot create operator: Points {missing} do not exist")

        # Build operator node with direction + optional label (context is NOT written — P1 #49)
        extra_props = []
        params = {"id": pid, "op": op_type, "direction": direction}
        if label:
            extra_props.append("label:$label")
            params["label"] = label
        props_clause = ", " + ", ".join(extra_props) if extra_props else ""
        proj.g.query(
            f"CREATE (o:Point {{id:$id, is_operator:true, op_type:$op, direction:$direction{props_clause}}})",
            params=params,
        )
        # Ontology v2.1: map part/whole ops to hasPart, remove INPUT edges
        edge_type = "hasPart" if op_type not in ("IMPL", "NAND") else op_type
        for i, inp_id in enumerate(inputs):
            proj.g.query(
                f"MATCH (o:Point {{id:$oid}}), (s:Point {{id:$sid}}) "
                f"CREATE (o)-[:{edge_type} {{idx:$i}}]->(s)",
                params={"oid": pid, "sid": inp_id, "i": i},
            )
        # Draft → live lifecycle (#131): source point goes live when first edge created
        # P1 (code-review): draft → live promote ONLY for draft/null sources —
        # an unconditional promote resurrected retracted (terminal) sources,
        # violating the terminal-state contract with no event in the stream.
        proj.g.query(
            "MATCH (s:Point {id:$sid}) "
            "WHERE (s.status IS NULL OR s.status = 'draft') "
            "SET s.status = 'live'",
            params={"sid": source_id},
        )
        # Dreaming (#85): new edges change propagation — mark all inputs dirty.
        self._mark_dirty(inputs)
        result = self.get_point(pid)
        # #432+#548 unified: domain payload + full point snapshot for both
        # the :GraphEvent store (subscriptions/poll) and JSONL (rebuild_all).
        event_point = dict(result)
        event_point["operator"] = {"op_type": op_type, "inputs": list(inputs)}
        self._emit_event("OperatorAdded", {
            "id": pid, "op_type": op_type, "source_id": source_id,
            "target_ids": list(target_ids),
        }, point=event_point)
        return result

    def annotate_operator(self, id: str, bias: float, precision: float,
                          consistency: float, directness: float) -> dict:
        """Annotate an operator Point with structured epistemic dimensions.

        Args:
            id: Operator Point ID (must have is_operator=true).
            bias: 0-1, how much hidden stake/additional interest beyond stated position.
            precision: 0-1, how narrow/well-defined the relevance claim is.
            consistency: 0-1, how stable this relevance is across contexts.
            directness: 0-1, how directly the source bears on the target.

        Raises ValueError if id not found, not an operator, or dims out of [0,1].
        """
        point = self.get_point(id)
        if not point:
            raise ValueError(f"Operator {id!r} not found")
        if not point.get("is_operator"):
            raise ValueError(f"Point {id!r} is not an operator")
        for name, val in (("bias", bias), ("precision", precision),
                          ("consistency", consistency), ("directness", directness)):
            if not 0 <= val <= 1:
                raise ValueError(f"{name} must be 0-1, got {val}")
        # #432 Task 3: durable OperatorAnnotated event (append-before-mutation).
        self._emit_event("OperatorAnnotated", {
            "id": id, "bias": bias, "precision": precision,
            "consistency": consistency, "directness": directness,
        })
        return self.update_point(id,
            annotator_bias=bias, annotator_precision=precision,
            annotator_consistency=consistency, annotator_directness=directness)

    def mitigate_operator(self, id: str, reason: str, strength: float = 0.5) -> dict:
        """Create a mitigation Point that modulates an operator's edge strength.

        Args:
            id: Operator Point ID to mitigate.
            reason: Why the edge is weaker than it appears.
            strength: 0-1, 0=fully neutralized, 1=fully intact (default 0.5).

        Raises ValueError if id not found or not an operator.
        Idempotent: second call updates existing mitigation (reason + strength),
        does not create a duplicate.
        """
        if not 0 <= strength <= 1:
            raise ValueError(f"strength must be 0-1, got {strength}")
        point = self.get_point(id)
        if not point:
            raise ValueError(f"Operator {id!r} not found")
        if not point.get("is_operator"):
            raise ValueError(f"Point {id!r} is not an operator")
        # Idempotency: check for existing mitigation
        proj = self._get_proj()
        existing = proj.g.query(
            "MATCH (op:Point {id:$id})-[r:mitigated_by]->(m:Point) RETURN m.id",
            params={"id": id},
        ).result_set
        if existing:
            mid = existing[0][0]
            return self.update_point(mid, content=f"[MITIGATION] {reason}",
                                     mitigation_strength=strength)
        # Create new mitigation Point
        mid = ulid()
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        proj.g.query(
            "CREATE (m:Point {id:$id, content:$c, pointKind:'statement', "
            "mitigation_strength:$s, is_operator:false, createdAt:$now, updatedAt:$now})",
            params={"id": mid, "c": f"[MITIGATION] {reason}", "s": strength, "now": now},
        )
        # Bidirectional link: mitigation Point -[:IMPL]-> operator, operator <-[:mitigated_by]- mitigation
        proj.g.query(
            "MATCH (m:Point {id:$mid}), (op:Point {id:$oid}) "
            "CREATE (m)-[:IMPL]->(op), (op)-[:mitigated_by]->(m)",
            params={"mid": mid, "oid": id},
        )
        # Dreaming (#85): new mitigation + IMPL edge change propagation.
        self._mark_dirty([mid, id])
        # #548: emit events for rebuild parity
        self._emit_event("PointAdded", point=self.get_point(mid))
        # Emit OperatorAdded so the IMPL edge (mitigation → operator) is
        # recreated on replay. mitigated_by edges are ancillary and
        # reconstructed separately via the operator's edge replay.
        mit_point = self.get_point(mid)
        mit_point["operator"] = {"op_type": "IMPL", "inputs": [id]}
        self._emit_event("OperatorAdded", point=mit_point)
        return self.get_point(mid)

    # ── Query ─────────────────────────────────────────────────────

    def query(self, kind: str | None = None,
              *, include_retracted: bool = False,
              **filters) -> list[dict]:
        """Query points by pointKind and/or custom property filters.

        #432 Task 2: retracted points (status='retracted') are EXCLUDED by
        default — pass include_retracted=True, or an explicit status= filter
        (e.g. status='retracted'), to surface tombstones.

        For confidence-aware queries, use tortoise_fts_query() with query=None
        for full-scan mode with EP annotation.

        Retracted points (status='retracted') are excluded. Use a raw Cypher
        query via proj.g.query() to inspect retracted tombstones.
        """
        proj = self._get_proj()
        clauses = ["(n.is_operator IS NULL OR n.is_operator = false)"]
        params: dict[str, Any] = {}
        # #432 Task 2: retracted exclusion — skipped when the caller explicitly
        # filters by status (their filter controls visibility).
        if not include_retracted and "status" not in filters:
            clauses.append("(n.status IS NULL OR n.status <> 'retracted')")
        if kind:
            expanded = self._expand_kind(kind)
            if len(expanded) == 1:
                clauses.append("n.pointKind = $kind")
                params["kind"] = expanded[0]
            else:
                placeholders = [f"$kind_{i}" for i in range(len(expanded))]
                clauses.append(f"n.pointKind IN [{', '.join(placeholders)}]")
                for i, k in enumerate(expanded):
                    params[f"kind_{i}"] = k
        for key, val in filters.items():
            # #329: strict ASCII identifier + reserved-key rejection (the old
            # isalnum check allowed Unicode keys that broke Cypher params and
            # filter keys colliding with kind/kind_N/skip/limit silently
            # overwrote auto-generated parameters).
            from .security import validate_filter_key
            validate_filter_key(key)
            clauses.append(f"n.`{key}` = ${key}")
            params[key] = val
        where = " AND ".join(clauses)
        rows = proj.g.query(
            f"MATCH (n:Point) WHERE {where} RETURN properties(n)",
            params=params,
        ).result_set
        return [r[0] for r in rows]

    def paginated_query(self, kind: str | None = None,
                        skip: int = 0, limit: int = 20,
                        *, include_retracted: bool = False,
                        **filters) -> dict:
        """Query points with pagination. Returns {results, total, hasMore}.

        #432 Task 2: retracted points (status='retracted') are EXCLUDED by
        default — pass include_retracted=True, or an explicit status= filter,
        to surface tombstones.
        """
        proj = self._get_proj()
        clauses = ["(n.is_operator IS NULL OR n.is_operator = false)"]
        params: dict[str, Any] = {}
        # #432 Task 2: retracted exclusion — skipped when the caller explicitly
        # filters by status (their filter controls visibility).
        if not include_retracted and "status" not in filters:
            clauses.append("(n.status IS NULL OR n.status <> 'retracted')")
        if kind:
            expanded = self._expand_kind(kind)
            if len(expanded) == 1:
                clauses.append("n.pointKind = $kind")
                params["kind"] = expanded[0]
            else:
                placeholders = [f"$kind_{i}" for i in range(len(expanded))]
                clauses.append(f"n.pointKind IN [{', '.join(placeholders)}]")
                for i, k in enumerate(expanded):
                    params[f"kind_{i}"] = k
        for key, val in filters.items():
            # #329: strict ASCII identifier + reserved-key rejection (the old
            # isalnum check allowed Unicode keys that broke Cypher params and
            # filter keys colliding with kind/kind_N/skip/limit silently
            # overwrote auto-generated parameters).
            from .security import validate_filter_key
            validate_filter_key(key)
            clauses.append(f"n.`{key}` = ${key}")
            params[key] = val
        where = " AND ".join(clauses)
        total = proj.g.query(
            f"MATCH (n:Point) WHERE {where} RETURN count(n)",
            params=params,
        ).result_set[0][0]
        rows = proj.g.query(
            f"MATCH (n:Point) WHERE {where} RETURN properties(n)"
            f" ORDER BY n.createdAt DESC SKIP $skip LIMIT $limit",
            params={**params, "skip": skip, "limit": limit},
        ).result_set
        results = [r[0] for r in rows]

        return {"results": results, "total": total, "hasMore": skip + limit < total}

    def get_point(self, id: str) -> dict:
        """Get a Point by ID. Returns dict of all properties, or {} if not found.

        #432: retracted points (status='retracted') ARE returned by get_point
        — they are tombstoned, not deleted. Query surfaces exclude them by
        default (opt-in via include_retracted).
        """
        proj = self._get_proj()
        rows = proj.g.query(
            "MATCH (n:Point {id:$id}) RETURN properties(n)",
            params={"id": id},
        ).result_set
        return rows[0][0] if rows else {}

    def traverse(self, id: str, relationship_type: str, direction: str = "outgoing") -> list[dict]:
        """Traverse relationships from a Point. Returns connected point dicts.

        #329: relationship_type is allowlisted (KNOWN_REL_TYPES) — it is
        interpolated into the query STRUCTURE (``-[:TYPE]->``) where
        parameterization is impossible; an unvalidated value is a Cypher
        injection primitive. direction is validated to outgoing/incoming.
        """
        proj = self._get_proj()
        # #329: validate before building any Cypher
        from .security import validate_rel_type
        validate_rel_type(relationship_type)
        if direction not in ("outgoing", "incoming"):
            raise ValueError(f"Invalid direction: {direction!r}. Use 'outgoing' or 'incoming'.")
        pat = (f"(n:Point {{id:$id}})-[:{relationship_type}]->(m:Point)"
               if direction == "outgoing" else
               f"(n:Point {{id:$id}})<-[:{relationship_type}]-(m:Point)")
        rows = proj.g.query(
            f"MATCH {pat} RETURN m.id, m.content, m.pointKind",
            params={"id": id},
        ).result_set
        return [
            {"id": r[0], "content": r[1], "pointKind": r[2]}
            for r in rows
        ]

    # ── Chain Integrity ───────────────────────────────────────────

    def check_structure(self) -> list[dict]:
        """Check Gate 0→4 chain integrity. Uses pack-aware kind expansion."""
        proj = self._get_proj()
        violations: list[dict] = []

        # Resolve kinds via pack registry (handles namespace prefixes)
        uc_kind = self._expand_kind("useCase")
        jtbd_kind = self._expand_kind("jobToBeDone")
        uj_kind = self._expand_kind("userJourney")
        wf_kind = self._expand_kind("workflow")
        req_kind = self._expand_kind("requirement")

        # Build IN clauses
        def kind_in(kinds):
            return ", ".join(f"'{k}'" for k in kinds)

        # useCase without parent JTBD
        ucs = proj.g.query(
            f"MATCH (uc:Point) WHERE uc.pointKind IN [{kind_in(uc_kind)}] RETURN uc.id, uc.uc_id"
        ).result_set
        for uc_id, uc_ref in ucs:
            parents = proj.g.query(
                f"MATCH (op:Point {{is_operator:true, op_type:'composedOf'}})"
                f"-[:hasPart]->(uc:Point {{id:$id}}), "
                f"(op)-[:hasPart]->(jtbd:Point) WHERE jtbd.pointKind IN [{kind_in(jtbd_kind)}] "
                f"RETURN jtbd.id",
                params={"id": uc_id},
            ).result_set
            if not parents:
                violations.append({
                    "type": "orphan_use_case",
                    "id": uc_id,
                    "message": f"useCase {uc_ref or uc_id} has no parent JTBD",
                })

        # userJourney dangling UC refs
        for uj_id, covered in proj.g.query(
            f"MATCH (uj:Point) WHERE uj.pointKind IN [{kind_in(uj_kind)}] RETURN uj.id, uj.covered_use_cases"
        ).result_set:
            if not covered:
                continue
            for uc_ref in covered.split(","):
                uc_ref = uc_ref.strip()
                if not proj.g.query(
                    f"MATCH (uc:Point) WHERE uc.pointKind IN [{kind_in(uc_kind)}] AND uc.uc_id=$ref RETURN count(uc) > 0",
                    params={"ref": uc_ref},
                ).result_set[0][0]:
                    violations.append({
                        "type": "dangling_use_case_ref",
                        "id": uj_id,
                        "message": f"userJourney {uj_id} refs non-existent useCase {uc_ref}",
                    })

        # Workflow dangling JTBD refs
        for wf_id, enables in proj.g.query(
            f"MATCH (wf:Point) WHERE wf.pointKind IN [{kind_in(wf_kind)}] RETURN wf.id, wf.enables_jtbd"
        ).result_set:
            if not enables:
                continue
            for jtbd_ref in enables.split(","):
                jtbd_ref = jtbd_ref.strip()
                if not proj.g.query(
                    f"MATCH (j:Point) WHERE j.pointKind IN [{kind_in(jtbd_kind)}] AND j.jtbd_id=$ref RETURN count(j) > 0",
                    params={"ref": jtbd_ref},
                ).result_set[0][0]:
                    violations.append({
                        "type": "dangling_jtbd_ref",
                        "id": wf_id,
                        "message": f"workflow {wf_id} refs non-existent JTBD {jtbd_ref}",
                    })

        # Requirement dangling Workflow refs
        for req_id, wf_ref in proj.g.query(
            f"MATCH (req:Point) WHERE req.pointKind IN [{kind_in(req_kind)}] RETURN req.id, req.enabled_workflow"
        ).result_set:
            if not wf_ref or wf_ref == "ALL":
                continue
            if not proj.g.query(
                f"MATCH (w:Point) WHERE w.pointKind IN [{kind_in(wf_kind)}] AND w.wf_id=$ref RETURN count(w) > 0",
                params={"ref": wf_ref},
            ).result_set[0][0]:
                violations.append({
                    "type": "dangling_workflow_ref",
                    "id": req_id,
                    "message": f"requirement {req_id} refs non-existent workflow {wf_ref}",
                })

        # Orphaned draft points — created but never wired (#131)
        for row in proj.g.query(
            "MATCH (n:Point {status:'draft'}) "
            "WHERE (n.is_operator IS NULL OR n.is_operator = false) "
            "AND NOT (n)--() "
            "RETURN n.id, n.content, n.pointKind, n.createdAt "
            "ORDER BY n.createdAt"
        ).result_set:
            violations.append({
                "type": "orphaned_draft",
                "id": row[0],
                "message": (
                    f"Draft point '{row[1][:80] if row[1] else ''}' "
                    f"of kind '{row[2] or 'unknown'}' has no edges "
                    f"(created {row[3] or 'unknown'})"
                ),
            })

        return violations

    def summarize_structure(self) -> dict:
        """Count points per Gate (by pointKind). Returns {gate: count, ..., total}.

        P1 #49: re-keyed from context strings (tortoise-wf-gate0..4) to pointKind
        (jobToBeDone, useCase, userJourney, workflow, requirement). Pre-existing
        experimental points that had context but no matching pointKind may show 0
        — expected under the #49 re-home (pointKind is the target vocabulary).
        """
        proj = self._get_proj()
        gates = [
            ("gate0_jtbds", "jobToBeDone"),
            ("gate1_use_cases", "useCase"),
            ("gate2_user_journeys", "userJourney"),
            ("gate3_workflows", "workflow"),
            ("gate4_requirements", "requirement"),
        ]
        result: dict[str, int] = {}
        for key, kind in gates:
            result[key] = proj.g.query(
                "MATCH (n:Point {pointKind:$k}) "
                "WHERE (n.is_operator IS NULL OR n.is_operator = false) "
                "RETURN count(n)",
                params={"k": kind},
            ).result_set[0][0]
        result["total"] = sum(result.values())
        return result

    # ── Taxonomy ─────────────────────────────────────────────────

    def taxonomy(self) -> dict[str, int]:
        """Count entities by node label. Returns {Point: N, Event: N, ...}."""
        from .taxonomy import taxonomy as _taxonomy
        return _taxonomy(self._get_proj())

    def list_pointkinds(self) -> list[dict]:
        """All pointKinds present in the graph with counts. Returns [{kind, count, pack}]."""
        proj = self._get_proj()
        rows = proj.g.query(
            "MATCH (n:Point) "
            "WHERE (n.is_operator IS NULL OR n.is_operator = false) "
            "AND n.pointKind IS NOT NULL "
            "RETURN n.pointKind, count(n) ORDER BY count(n) DESC"
        ).result_set
        result: list[dict] = []
        for row in rows:
            kind = row[0]
            count = row[1]
            pack = kind.split(":", 1)[0] if ":" in kind else ""
            result.append({"kind": kind, "count": count, "pack": pack})
        return result

    def list_sources(self) -> list[dict]:
        """All Sources with point counts. Returns [{url, sourceKind, points}]."""
        proj = self._get_proj()
        rows = proj.g.query(
            "MATCH (s:Source) "
            "OPTIONAL MATCH (p:Point)-[:extractedFrom]->(s) "
            "RETURN s.url, s.sourceKind, count(p) AS points "
            "ORDER BY points DESC"
        ).result_set
        return [
            {"url": row[0], "sourceKind": row[1], "points": row[2]}
            for row in rows
        ]

    def list_tags(self) -> list[dict]:
        """All Tag names with count of tagged Points. Returns [{name, count}].

        Orphaned :Tag nodes (no TAGGED edges) are garbage-collected when
        edges are removed (update_point tag sync + delete_point, #485), so
        count 0 entries should not normally appear.
        """
        proj = self._get_proj()
        rows = proj.g.query(
            "MATCH (t:Tag) "
            "OPTIONAL MATCH (p:Point)-[:TAGGED]->(t) "
            "RETURN t.name AS name, count(p) AS count "
            "ORDER BY name"
        ).result_set
        return [{"name": row[0], "count": row[1]} for row in rows]

    def query_points_by_tag(self, tag: str) -> list[dict]:
        """Return Points connected via TAGGED edge to the given Tag name."""
        proj = self._get_proj()
        rows = proj.g.query(
            "MATCH (p:Point)-[:TAGGED]->(t:Tag {name:$tag}) "
            "RETURN properties(p) "
            "ORDER BY p.createdAt DESC",
            params={"tag": tag},
        ).result_set
        return [r[0] for r in rows]

    def list_namespaces(self) -> list[dict]:
        """Installed pack namespaces with kind counts. Returns [{namespace, name, kind_count}]."""
        registry = _get_kind_expander()
        packs = registry.list_packs()
        return [
            {
                "namespace": p["namespace"],
                "name": p["name"],
                "kind_count": sum(p["kind_counts"].values()),
            }
            for p in packs
        ]

    def list_topics(self, entity_id: str) -> dict:
        """entityProfile lite for an entity. Returns {id, pointKind, context, neighbors, neighborCounts}."""
        from .taxonomy import list_topics as _list_topics
        return _list_topics(self._get_proj(), entity_id)

    def topic_summarize(
        self,
        topic: str,
        *,
        max_seeds: int = 50,
        max_hops: int = 1,
        include_relationships: bool = True,
    ) -> dict:
        """Epistemic topic summarization — settled vs contested structure (#592).

        For a topic query, returns the epistemic structure: what is significant
        (settled — high confidence, strong connections) and what is contested
        (elevated variance, NAND conflicts), plus the argument topology.

        Args:
            topic: Topic string (e.g. "pricing", "architecture").
            max_seeds: Max seed Points to retrieve from about* edges + content match.
            max_hops: Operator-chain expansion depth (0 = seeds only, 1 = neighbors).
            include_relationships: Whether to include argument topology.

        Returns:
            dict with keys: topic, total_points, significant, contested,
            disputed_pairs, argument_structure, meta.
        """
        from .topic_summarization import topic_summarize as _summarize
        result = _summarize(
            self._get_proj().g,
            topic,
            max_seeds=max_seeds,
            max_hops=max_hops,
            include_relationships=include_relationships,
        )
        return result.to_dict()

    # ── Bulk ──────────────────────────────────────────────────────

    def batch_create_points(self, points_list: list[dict]) -> list[dict]:
        """Create multiple points. Each dict needs {kind, content, **props}."""
        return [self.create_point(**p) for p in points_list]

    def file_decision(self, options: list[str], evidence: list[str],
                      choice: int) -> dict:
        """File a simple decision directly to the graph — no EP, no calibration,
        no research cycles. Creates decision + options + evidence + IMPL edges
        atomically. For low-stakes decisions where the answer is clear (#133).

        Args:
            options: list of option descriptions (e.g. ["JSON", "YAML"])
            evidence: list of evidence statements supporting the choice
            choice: 0-indexed index into options (the chosen option)

        Returns {decision_id, option_ids: [...], evidence_ids: [...]}.
        """
        if not options:
            raise ValueError("At least one option required")
        if choice < 0 or choice >= len(options):
            raise ValueError(f"choice={choice} out of range [0, {len(options)-1}]")

        # 1. Create decision point
        decision = self.create_point(
            "decision",
            f"Decision: {options[choice]}",
            status="live",
        )
        decision_id = decision["id"]

        # 2. Create option points + IMPL edges from decision
        option_ids = []
        for i, opt in enumerate(options):
            opt_point = self.create_point(
                "option",
                f"Option {i+1}: {opt}",
                status="live",  # options are targets, not sources — explicit live
            )
            option_ids.append(opt_point["id"])
            # IMPL edge: decision -> option ("decision considers option")
            self.create_operator("IMPL", decision_id, [opt_point["id"]])

        # 3. Create evidence points + IMPL edges to the chosen option
        evidence_ids = []
        chosen_id = option_ids[choice]
        for ev in evidence:
            ev_point = self.create_point(
                "evidence",
                ev,
            )
            evidence_ids.append(ev_point["id"])
            # IMPL edge: evidence -> chosen option ("evidence supports choice")
            self.create_operator("IMPL", ev_point["id"], [chosen_id])

        return {
            "decision_id": decision_id,
            "option_ids": option_ids,
            "evidence_ids": evidence_ids,
        }

    def file_human_approval(self, approver_id: str, artifact_id: str,
                            point_ids: list[str],
                            decision_content: str | None = None) -> dict:
        """Record a human approval of a planning artifact (#531).

        Canonical approval pattern (research #421): an Event
        (eventKind: ``humanApproval``) records the occurrence with full
        provenance — approver Subject, artifact, approved claim Points — while
        a decision Point (pointKind: ``humanApproval``) carries the epistemic
        weight: it seeds the grounding a-vector and receives an EP evidence
        prior so dependent claims strengthen. Unidirectional IMPL fan-out
        (label ``approvedBy``) links the approval Point to each approved claim
        Point — deliberately unidirectional so EP never propagates claim
        weakness back into the approval.

        Deliberately NOT stored: no ``approved`` status flag on the artifact —
        approval is derived from the event stream at query time (ONTOLOGY §2).

        Args:
            approver_id: Subject id (or name) of the human approving.
            artifact_id: Object/Document id (or name) of the artifact approved.
            point_ids: claim Point ids the approval covers (non-operator).
            decision_content: optional content override for the decision Point
                (default ``"Approved: <artifact>"``).

        Returns {event_id, decision_point_id, impl_operator_ids,
                 confidence_delta} where confidence_delta maps each approved
        claim id to its confidence change after the EP run.
        """
        from datetime import datetime, timezone
        proj = self._get_proj()

        # 1. Validate approver Subject exists (fail loudly, not silently)
        r = proj.g.query(
            "MATCH (s:Subject) WHERE s.id = $id OR s.name = $id RETURN s.id",
            params={"id": approver_id},
        ).result_set
        if not r:
            raise ValueError(
                f"Cannot file human approval: Subject {approver_id!r} does not exist"
            )

        # 2. Validate artifact exists (Object or Document)
        r = proj.g.query(
            "MATCH (n) WHERE (n:Object OR n:Document) "
            "AND (n.id = $id OR n.name = $id) RETURN labels(n), n.id",
            params={"id": artifact_id},
        ).result_set
        if not r:
            raise ValueError(
                f"Cannot file human approval: artifact {artifact_id!r} does not exist"
            )

        # 3. Validate point_ids exist and are non-operator Points
        if not point_ids:
            raise ValueError("Cannot file human approval: at least one claim Point required")
        r = proj.g.query(
            "MATCH (n:Point) WHERE n.id IN $ids "
            "AND (n.is_operator IS NULL OR n.is_operator = false) RETURN n.id",
            params={"ids": point_ids},
        ).result_set
        existing = {row[0] for row in r}
        missing = [pid for pid in point_ids if pid not in existing]
        if missing:
            raise ValueError(
                f"Cannot file human approval: Points {missing} do not exist or are operators"
            )

        now = datetime.now(timezone.utc).isoformat()

        # 4. Decision Point (pointKind humanApproval) — epistemic weight carrier
        content = decision_content or f"Approved: {artifact_id}"
        decision = self.create_point(
            "humanApproval",
            content,
            status="live",
            authoredBy=approver_id,
        )
        decision_id = decision["id"]

        # 5. Event (eventKind humanApproval) — the occurrence record
        event = self.create_event(
            name=f"human approval of {artifact_id}",
            eventKind="humanApproval",
            startedAt=now,
            eventStatus="completed",
        )
        event_id = event["eventId"]
        # Wire provenance: approver performs; uses artifact; aboutPoint each
        # approved claim; produces the decision Point (design #421).
        proj.create_edge(approver_id, event_id, "performs")
        proj.create_edge(event_id, artifact_id, "uses")
        for pid in point_ids:
            proj.create_about_edge(event_id, pid, "aboutPoint")
        proj.create_edge(event_id, decision_id, "produces")

        # 6. Unidirectional IMPL fan-out: approval → each approved claim.
        #    create_operator defaults to bidirectional — direction must be
        #    explicit so EP never back-propagates claim weakness into the
        #    approval point.
        op = self.create_operator(
            "IMPL", decision_id, point_ids,
            label="approvedBy", direction="unidirectional",
        )

        # 7. EP with evidence prior on the approval Point: Beta(10,1) is a
        #    strong positive prior — dependents strengthen (issue #531).
        before = {}
        for pid in point_ids:
            p = self.get_point(pid)
            before[pid] = p.get("confidence", 0.5) if p else 0.5
        self.compute_confidence(evidence={decision_id: (10, 1)})
        deltas = {}
        for pid in point_ids:
            p = self.get_point(pid)
            after = p.get("confidence", 0.5) if p else 0.5
            deltas[pid] = round(after - before[pid], 4)

        # 8. Approval seeds the grounding a-vector — re-compute (api.py pattern)
        if hasattr(proj, "compute_grounding"):
            proj.compute_grounding()

        return {
            "event_id": event_id,
            "decision_point_id": decision_id,
            "impl_operator_ids": [op["id"]],
            "confidence_delta": deltas,
        }

    # ── Lifecycle ─────────────────────────────────────────────────

    def list_graphs(self) -> list[str]:
        """List all graph names in the database."""
        return self._get_proj().list_graphs()

    def _audit(self, team_id: str, actor_user_id: str | None,
                operation: str, **kwargs) -> None:
        """Log an audit event. No-op if audit logger not initialized."""
        if self._audit_logger is None:
            from .audit_events import AuditLogger
            self._audit_logger = AuditLogger()
        self._audit_logger.append(
            team_id=team_id,
            actor_user_id=actor_user_id,
            operation=operation,
            **kwargs,
        )

    def list_relations(self) -> list[dict]:
        """List all relation declarations across installed packs.

        Returns [{"pack": ..., "predicate": ..., "fromKind": ..., "toKind": ...,
        "mechanism": ...}]. Pack relations describe valid edge types between
        entity kinds — use for schema discovery.
        """
        return _get_kind_expander().list_relations()

    def close(self) -> None:
        """Close the underlying database connection and audit logger."""
        if self._audit_logger is not None:
            self._audit_logger.close()
            self._audit_logger = None
        if self._proj is not None:
            self._proj.close()
            self._proj = None
        self._registry_g = None

    # ── P1-4: Entity Linking ────────────────────────────────────

    def provenance(self, point_id: str) -> dict:
        """Provenance chain — "Who decided this?" Point → Subject → delegation."""
        point = self.get_point(point_id)
        if not point:
            return {"error": f"Point {point_id} not found"}
        author = point.get("authoredBy", "")
        chain = {"point": {"id": point_id, "content": (point.get("content") or "")[:200],
                           "authoredBy": author}}
        if not author:
            return chain
        proj = self._get_proj()
        rows = proj.g.query(
            "MATCH (s:Subject) WHERE toLower(s.name) = toLower($n) RETURN properties(s)",
            params={"n": author},
        ).result_set
        if not rows:
            return {**chain, "subject": None}
        sub = rows[0][0]
        chain["subject"] = {"id": sub.get("id"), "name": sub.get("name"),
                             "kind": sub.get("subjectKind", "")}
        # ponytail: follow outgoing rels for Role → Team delegation
        rels = proj.g.query(
            "MATCH (s:Subject {id:$sid})-[r]->(n) RETURN type(r), labels(n)[0], properties(n)",
            params={"sid": sub["id"]},
        ).result_set
        chain["delegation"] = [{"via": r[0], "node_type": r[1], "props": r[2]} for r in rels]
        return chain

    # ── #7045: about edges backfill (Ontology v2.1) ──────────

    def backfill_about_entities(self) -> dict:
        """Keyword-match Points against Subject/Object/Event/Document names → about edges.

        For each Point (non-operator), checks if its content contains any Subject,
        Object, Event, or Document name/title. If yes, creates the matching about*
        edge (aboutSubject, aboutObject, aboutEvent, aboutDocument).
        Idempotent — MERGE prevents duplicates.

        Returns {scanned, updated, entities_matched}.
        """
        proj = self._get_proj()
        # Load all entity names → ids (flat dict for membership check)
        entities: dict[str, str] = {}
        # Subject + Object: matched by name property
        for label in ("Subject", "Object"):
            rows = proj.g.query(
                f"MATCH (e:{label}) WHERE e.name IS NOT NULL RETURN e.name, e.id"
            ).result_set
            for name, eid in rows:
                if name:
                    entities[name.lower()] = eid
        # Event: matched by name property (set by create_event)
        for row in proj.g.query(
            "MATCH (e:Event) WHERE e.name IS NOT NULL RETURN e.name, e.eventId"
        ).result_set:
            name, eid = row[0], row[1]
            if name:
                entities[name.lower()] = eid
        # Document: matched by title (primary display name) or name
        for row in proj.g.query(
            "MATCH (d:Document) WHERE d.title IS NOT NULL RETURN d.title, d.id"
        ).result_set:
            title, did = row[0], row[1]
            if title:
                entities[title.lower()] = did

        # Ontology v2.1: use per-type about* edges instead of property
        rows = proj.g.query(
            "MATCH (n:Point) "
            "WHERE (n.is_operator IS NULL OR n.is_operator = false) "
            "RETURN n.id, n.content"
        ).result_set

        scanned, updated, matched = 0, 0, 0
        for pid, content in rows:
            scanned += 1
            if not content:
                continue
            content_lower = content.lower()
            for name, eid in entities.items():
                if name in content_lower:
                    proj._create_about_edges(pid, name)
                    matched += 1
            if matched > 0:
                updated += 1

        return {"scanned": scanned, "updated": updated, "entities_matched": matched}

    # ── P1-3: Staleness ─────────────────────────────────────────

    def stale_points(self, days: int = 30, limit: int = 50) -> dict:
        """Return Points not updated in N days. Returns {stale: [...], count: N, cutoff: '...'}."""
        proj = self._get_proj()
        stale = proj.stale_points(days=days, limit=limit)
        return {"stale": stale, "count": len(stale),
                "cutoff": f"{days} days", "limit": limit}

    # ── EP Belief Propagation (#6908) ────────────────────────────

    def _get_ep(self):
        if self._ep is None:
            from .ep import TortoiseEP
            self._ep = TortoiseEP(self._get_proj())
        return self._ep

    # ── Dreaming (#85) ──────────────────────────────────────────────

    def _get_dreamer(self):
        """Lazy-init the Dreamer (thread-safe)."""
        if self._dreamer is None:
            from .dream import Dreamer
            self._dreamer = Dreamer(self)
        return self._dreamer

    def _mark_dirty(self, point_ids: list[str]) -> None:
        """Mark claims whose confidence is now stale after a write.

        1-hop reverse BFS (#85 contract): from the mutated point, collect the
        operators that target it, then the claims those operators target.
        The dream expands to max_hops=2 for full propagation — do not reduce
        the dream's max_hops below 2 without expanding this marking.
        """
        if not point_ids:
            return
        # The mutated points themselves are always dirty (their baseline
        # priors / properties changed).
        self._dirty_roots.update(point_ids)
        proj = self._get_proj()
        # Operators targeting the mutated points (reverse of operator→point).
        rows = proj.g.query(
            "MATCH (op:Point {is_operator:true})-[:IMPL|NAND]->(p:Point) "
            "WHERE p.id IN $ids RETURN DISTINCT op.id",
            params={"ids": list(point_ids)},
        ).result_set
        op_ids = [r[0] for r in rows]
        if not op_ids:
            return
        # Claims those operators target (1-hop forward from operators).
        rows = proj.g.query(
            "MATCH (op:Point {is_operator:true})-[:IMPL|NAND]->(c:Point) "
            "WHERE op.id IN $oids RETURN DISTINCT c.id",
            params={"oids": op_ids},
        ).result_set
        self._dirty_roots.update(r[0] for r in rows)

    def dream(self, dirty_only: bool = True, full: bool = False,
              max_hops: int = 2) -> dict:
        """Run EP stabilization (#85).

        Args:
            dirty_only: dream the accumulated dirty roots (default True).
            full: whole-graph stabilization (dream_all). Mutually exclusive
                  with dirty_only + anchors.
            max_hops: EP subgraph expansion (keep ≥2 — contract with
                      _mark_dirty).

        Returns {iterations, converged, affected_claims} or the dream_all
        summary for full=True.
        """
        dreamer = self._get_dreamer()
        if full:
            return dreamer.dream_all(max_hops=max_hops)
        if not dirty_only and not self._dirty_roots:
            return {"iterations": 0, "converged": True, "affected_claims": []}
        anchors = list(self._dirty_roots)
        result = dreamer.dream(anchors, max_hops=max_hops)
        # Clear dirty roots that converged (keep any that failed to converge
        # so a later dream retries them).
        if result.get("converged", False):
            self._dirty_roots.clear()
        else:
            affected = set(result.get("affected_claims", []))
            self._dirty_roots -= affected
        return result

    def _select_subgraph(self, anchors: list[str], max_hops: int = 1,
                         rel_filter: str = "IMPL|NAND",
                         direction: str = "both") -> list[str]:
        """BFS subgraph selection from anchor Points to collect operator IDs.

        Delegates to the shared _bfs_select_operators in tortoise.analyze.
        """
        from .analyze import _bfs_select_operators
        proj = self._get_proj()
        result = _bfs_select_operators(proj, anchors, max_hops=max_hops,
                                        rel_filter=rel_filter, direction=direction)
        return list(result)

    def compute_confidence(self, factors=None, evidence=None,
                           anchors: list[str] | None = None,
                           max_hops: int = 1,
                           rel_filter: str = "IMPL|NAND",
                           direction: str = "both",
                           require_calibration: bool = False,
                           recency_decay: float | None = None) -> dict:
        """Compute confidence via EP belief propagation. Returns {iterations, converged, confidences}.

        Args:
            factors: operator IDs (list[str]) or factor tuples. If None, auto-extracts.
            evidence: optional {claim_id: (alpha, beta)} priors.
            anchors: list of Point IDs for BFS subgraph selection.
            max_hops: BFS expansion depth when using anchors (default 1).
            rel_filter: edge types for BFS — "IMPL", "NAND", or "IMPL|NAND" (default).
            direction: IMPL edge traversal direction — "incoming", "outgoing", or "both" (default).
            require_calibration: if True, raises CalibrationError when evidence points are uncalibrated.
            recency_decay: optional recency decay factor (default 0.95 from TORTOISE_EP_RECENCY_DECAY).
                T0 sources exempt; lower tiers get gentle decay. 1.0 = no decay.

        Precedence: factors > anchors > auto-extract-all.
        """
        proj = self._get_proj()
        ep = self._get_ep()
        # Hydrate evidence from graph-persisted baselines (survives SDK restarts)
        self._hydrate_evidence()
        # Apply source-based credibility inheritance (with recency modulation #122)
        self._apply_source_inheritance(recency_decay=recency_decay)
        if evidence:
            self._evidence.update(evidence)
        # Calibration gate
        if require_calibration:
            from .exceptions import CalibrationError
            summary = self.calibrate_summary()
            evidence_kinds = {"statement", "observation", "hypothesis"}
            uncalibrated = [
                s for s in summary
                if not s["calibrated"] and s.get("pointKind") in evidence_kinds
            ]
            if uncalibrated:
                ids = [s["id"] for s in uncalibrated[:10]]
                msg = (
                    f"{len(uncalibrated)} uncalibrated evidence points. "
                    f"First 10: {ids}. Run calibrate_summary() for full guidance."
                )
                raise CalibrationError(msg)
        if factors is not None:
            operator_ids = [f if isinstance(f, str) else f[0] for f in factors]
        elif anchors is not None:
            # BFS subgraph selection from anchor points
            operator_ids = self._select_subgraph(anchors, max_hops=max_hops,
                                                  rel_filter=rel_filter,
                                                  direction=direction)
        else:
            factors_data, _ = proj.extract_svbp_factors()
            operator_ids = [f[0] for f in factors_data]
        if not operator_ids:
            return {"iterations": 0, "converged": True, "confidences": {}, "diagnostic": "no_factors"}
        # Lazy consistency (#85): if dirty roots exist and this is a
        # whole-graph/auto-extract computation, dream the dirty subgraph
        # first so the auto-extracted factors see stabilized values.
        if factors is None and anchors is None and self._dirty_roots:
            self.dream(dirty_only=True)
            # Re-extract factors after dreaming (graph may have changed).
            factors_data, _ = proj.extract_svbp_factors()
            operator_ids = [f[0] for f in factors_data]
            if not operator_ids:
                return {"iterations": 0, "converged": True, "confidences": {}, "diagnostic": "no_factors"}
        iterations, converged = ep.run(operator_ids, evidence=self._evidence)
        confidences = {}
        proj = self._get_proj()
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        for claim_id in ep._affected_claims(operator_ids):
            conf = ep.compute_confidence(claim_id)
            confidences[claim_id] = conf
            # Write back mean confidence to node property
            proj.g.query(
                "MATCH (n:Point {id:$id}) SET n.confidence = $c, n.updatedAt = $now",
                params={"id": claim_id, "c": conf["mean"], "now": now},
            )
        return {"iterations": iterations, "converged": converged, "confidences": confidences}

    def _hydrate_evidence(self) -> None:
        """Load graph-persisted baselines (baseline_set=true) into _evidence.

        Idempotent — only adds claim ids not already present. Shared by
        compute_confidence and the Dreamer (#330) so dream runs honour the
        same persistent evidence contract as explicit confidence reads.
        """
        proj = self._get_proj()
        # #689: retracted points must not feed Beta priors into EP.
        rows = proj.g.query(
            "MATCH (n:Point) WHERE n.baseline_set = true AND n.ep_alpha IS NOT NULL "
            "AND (n.status IS NULL OR n.status <> 'retracted') "
            "RETURN n.id, n.ep_alpha, n.ep_beta"
        ).result_set
        for pid, alpha, beta in rows:
            if pid not in self._evidence:
                self._evidence[pid] = (alpha, beta)

    def set_point_baseline(self, claim_id: str, alpha: float, beta: float, *,
                           source: str = "explicit") -> dict:
        """Set Beta prior evidence for a claim. Persists to graph immediately.

        ``source`` records the baseline's provenance (``baseline_source`` graph
        property): "explicit" (default) — manual/hosted baseline, NEVER
        recomputed by ``_apply_source_inheritance``; "inherited" — derived from
        Source evidence, recomputed per EP run subject to the per-point time
        gate (``n.inherited_at``). Explicit baselines are always distinguishable
        from legacy ``baseline_set=true`` rows (issue #398 2x2 mapping).
        """
        self._evidence[claim_id] = (alpha, beta)
        # Persist to graph so baselines survive SDK restarts
        proj = self._get_proj()
        proj.g.query(
            "MATCH (n:Point {id: $id}) "
            "SET n.ep_alpha = $a, n.ep_beta = $b, n.baseline_set = true, "
            "    n.baseline_source = $src",
            params={"id": claim_id, "a": alpha, "b": beta, "src": source},
        )
        # Dreaming (#85, P1): a baseline change alters the prior — neighbors
        # whose confidence derived from this claim are now stale.
        self._mark_dirty([claim_id])
        return {"claim_id": claim_id, "alpha": alpha, "beta": beta, "source": source}

    def _invalidate_inheritance_gate(self, point_ids: list[str]) -> None:
        """Dirty-mark the per-point recompute gate for inherited baselines.

        Called by write events that change the inputs of source inheritance
        (point created from a tiered source, extractedFrom edge deleted, source
        tier/assessment changed). Clears the point's ``inherited_at`` stamp so
        the next ``_apply_source_inheritance`` recomputes immediately regardless
        of the time-gate interval.
        """
        if not point_ids:
            return
        proj = self._get_proj()
        proj.g.query(
            "MATCH (n:Point) WHERE n.id IN $ids REMOVE n.inherited_at",
            params={"ids": point_ids},
        )
        self._mark_dirty(point_ids)

    def get_confidence(self, claim_id: str) -> dict:
        """Get EP confidence for a claim: {mean, variance, alpha, beta}.

        Lazy consistency (#85): if the claim is a dirty root (confidence
        diverged after writes), dream it first so reads return fresh values.
        """
        if claim_id in self._dirty_roots:
            self.dream(dirty_only=True)
        return self._get_ep().compute_confidence(claim_id)

    def _apply_source_inheritance(self, recency_decay: float | None = None,
                                   recompute_interval: float | None = None):
        """Apply source credibility (tier → Beta prior) to Points via extractedFrom.

        Issue #398 — log-scale multi-source aggregation (replaces
        highest-tier-wins) through the real graph path:

          - Tier resolution per source: explicit ``credibilityTier`` >
            ``sourceKind`` tier-form > registry default (``SOURCE_KIND_DEFAULTS``)
            > None (neutral — no inheritance, preserving the opt-in guard).
          - Aggregation: pinned formula
            ``pc_t = log2(N_t+1) * decay_t * mean_i(base_pc(tier_i) * factor_i)``
            with ``decay_t`` keyed on the tier's MOST-RECENT source (T0 exempt);
            per-source ``factor_i`` = assessment factor (1.0 until assess_source
            lands — Task 5).
          - Positive-only: NAND contradiction is EP's factor domain — inheritance
            never folds negative pseudo-counts (double-count guard).
          - Baseline provenance (2x2 mapping): explicit baselines (baseline_source
            = 'explicit' or legacy baseline_set=true) are NEVER recomputed;
            inherited baselines (baseline_source='inherited') recompute per run
            subject to the per-point time gate (``n.inherited_at``); points with
            no baseline (baseline_source IS NULL AND baseline_set IS NOT true)
            are ALWAYS eligible.
          - Gate: recompute at most once per ``recompute_interval`` (default 3600s,
            env TORTOISE_EP_REINHERIT_INTERVAL; 0 = always), unless the gate was
            dirty-marked by a write event. Epsilon guard (rel 1e-9) suppresses
            identical rewrites; ``inherited_at`` is always refreshed on a
            dirty-marked recompute so dirty points settle after one pass.
          - Assessment points (pointKind='assessment') are excluded — they are
            evidence ABOUT sources, not extracted FROM them.

        ep.py is untouched (additive-only — another issue owns EP propagation).
        """
        import os
        from datetime import datetime, timezone
        from tortoise.source_credibility import (
            aggregate_prior,
            assessment_factor,
            resolve_tier,
        )

        if recency_decay is None:
            recency_decay = float(os.environ.get("TORTOISE_EP_RECENCY_DECAY", "0.95"))
        if recompute_interval is None:
            recompute_interval = float(
                os.environ.get("TORTOISE_EP_REINHERIT_INTERVAL", "3600")
            )
        proj = self._get_proj()
        now = datetime.now(timezone.utc)
        from collections import defaultdict
        # Per-source assessment factors (latest per (url, assessor), outdated
        # filtered, reputation snapshotted at write). Batched — one query.
        factor_by_source: dict[str, float] = {}
        arows = proj.g.query(
            "MATCH (p:Point {pointKind:'assessment'}) "
            "WHERE (p.outdated IS NULL OR p.outdated = false) "
            "RETURN p.targetSource, p.assessor, p.score, "
            "coalesce(p.assessorReputation, 0.5), p.createdAt "
            "ORDER BY p.createdAt",
            params={},
        ).result_set
        latest_by_source: dict[str, dict[str, tuple[float, float]]] = defaultdict(dict)
        for tsrc, assessor, score, rep, _created in arows:
            if not tsrc:
                continue
            try:
                latest_by_source[tsrc][assessor] = (float(rep), float(score))
            except (TypeError, ValueError):
                continue
        for tsrc, by_assessor in latest_by_source.items():
            factor_by_source[tsrc] = assessment_factor(by_assessor.values())

        # Inherit-eligible points:
        #   (baseline_source IS NULL AND baseline_set IS NOT true)  → always eligible
        #   (baseline_source = 'inherited')                          → gated by inherited_at
        where = (
            "WHERE (n.is_operator IS NULL OR n.is_operator = false) "
            "AND (n.pointKind IS NULL OR n.pointKind <> 'assessment') "
            "AND ("
            "  (n.baseline_source IS NULL AND (n.baseline_set IS NULL OR n.baseline_set = false)) "
            "  OR n.baseline_source = 'inherited'"
            ") "
            "AND (s.credibilityTier IS NOT NULL OR s.sourceKind IS NOT NULL) "
        )
        rows = proj.g.query(
            f"MATCH (n:Point)-[:extractedFrom]->(s:Source) {where} "
            "RETURN n.id, s.url, s.credibilityTier, s.sourceKind, "
            "s.sourceDate, s.ingestedAt, n.baseline_source, n.inherited_at",
            params={},
        ).result_set

        # Collect per-point source evidence
        from collections import defaultdict
        point_sources: dict[str, list[dict]] = defaultdict(list)
        for pid, url, ctier, skind, sdate, ingested, bl_src, inherited_at in rows:
            tier = resolve_tier(ctier, skind)
            if tier is None:
                continue  # neutral source — no inheritance contribution
            point_sources[pid].append({
                "url": url, "tier": tier, "sourceDate": sdate,
                "ingestedAt": ingested,
            })

        # Revert: points with an inherited baseline but NO eligible sources
        # (all edges deleted or all sources neutral) return to neutral — subject
        # to the same per-point gate (dirty-marked or interval elapsed).
        if point_sources:
            sourced_ids = set(point_sources)
        else:
            sourced_ids = set()
        revert_rows = proj.g.query(
            "MATCH (n:Point) WHERE n.baseline_source = 'inherited' "
            "AND (n.pointKind IS NULL OR n.pointKind <> 'assessment') "
            "RETURN n.id, n.inherited_at",
            params={},
        ).result_set
        for pid, inherited_at in revert_rows:
            if pid in sourced_ids:
                continue
            # Gate check (same as write path)
            if inherited_at is not None and recompute_interval > 0:
                try:
                    last = datetime.fromisoformat(str(inherited_at).replace("Z", "+00:00"))
                    if last.tzinfo is None:
                        last = last.replace(tzinfo=timezone.utc)
                    age = (now - last).total_seconds()
                except (ValueError, TypeError):
                    age = recompute_interval + 1
                if age < recompute_interval:
                    continue  # within interval and not dirty-marked → keep
            proj.g.query(
                "MATCH (n:Point {id:$id}) REMOVE n.ep_alpha, n.ep_beta, "
                "n.baseline_set, n.baseline_source, n.inherited_at",
                params={"id": pid},
            )
            # Clear the stale prior from in-memory evidence cache (#652).
            # set_point_baseline writes (alpha, beta) into self._evidence
            # unconditionally, and _hydrate_evidence is additive-only — so
            # the stale entry survives the graph-level remove and gets
            # re-applied by ep.run(evidence=self._evidence).
            self._evidence.pop(pid, None)
            self._mark_dirty([pid])

        for pid, sources in point_sources.items():
            # Fetch the point's current baseline marker state
            row = proj.g.query(
                "MATCH (n:Point {id:$id}) RETURN n.baseline_source, n.inherited_at, "
                "coalesce(n.ep_alpha, 1.0), coalesce(n.ep_beta, 1.0)",
                params={"id": pid},
            ).result_set
            bl_src, inherited_at, cur_a, cur_b = row[0] if row else (None, None, 1.0, 1.0)

            is_inherited = bl_src == "inherited"
            if is_inherited and inherited_at is not None and recompute_interval > 0:
                try:
                    last = datetime.fromisoformat(str(inherited_at).replace("Z", "+00:00"))
                    if last.tzinfo is None:
                        last = last.replace(tzinfo=timezone.utc)
                    age = (now - last).total_seconds()
                except (ValueError, TypeError):
                    age = recompute_interval + 1  # stale stamp → recompute
                if age < recompute_interval:
                    continue  # within interval and not dirty-marked → skip

            # Per-source assessment factor (clamped [0.1, 2.0]); factor = 1.0
            # when no assessments — exact tier priors preserved.
            groups = [
                (src["tier"], src["sourceDate"], src["ingestedAt"],
                 factor_by_source.get(src["url"], 1.0))
                for src in sources
            ]
            alpha, beta = aggregate_prior(
                groups, recency_decay=recency_decay, now=now,
            )

            # Epsilon guard: skip identical rewrites (no dirty churn).
            if is_inherited and abs(alpha - cur_a) < 1e-9 * max(1.0, abs(alpha)) \
                    and abs(beta - cur_b) < 1e-9 * max(1.0, abs(beta)):
                # Refresh the gate stamp so dirty points settle after one pass.
                self._touch_inherited_at(pid, now)
                continue

            self.set_point_baseline(pid, alpha, beta, source="inherited")
            self._touch_inherited_at(pid, now)

    def _touch_inherited_at(self, point_id: str, now) -> None:
        """Stamp the per-point inheritance gate timestamp (graph-persisted)."""
        proj = self._get_proj()
        proj.g.query(
            "MATCH (n:Point {id:$id}) SET n.inherited_at = $ts",
            params={"id": point_id, "ts": now.isoformat()},
        )

    def calibrate_summary(self) -> list[dict]:
        """Audit graph calibration state. Returns per-point guidance.
        
        Checks baseline_set flag on non-operator Points. For uncalibrated
        points, traverses extractedFrom→Source to check for inherited credibilityTier.
        """
        proj = self._get_proj()
        where = "WHERE (n.is_operator IS NULL OR n.is_operator = false)"
        params = {}
        
        from tortoise.source_credibility import resolve_tier
        rows = proj.g.query(
            f"MATCH (n:Point) {where} "
            "AND (n.pointKind IS NULL OR n.pointKind <> 'assessment') "
            "OPTIONAL MATCH (n)-[:extractedFrom]->(s:Source) "
            "RETURN n.id, n.content, n.pointKind, "
            "coalesce(n.baseline_set, false) AS calibrated, "
            "s.credibilityTier, s.sourceKind, s.url AS src_url",
            params=params,
        ).result_set
        
        results = []
        for row in rows:
            pid, content, pk, calibrated, ctier, skind, src_url = row
            item = {"id": pid, "content": content, "pointKind": pk, "calibrated": calibrated}
            # Effective tier: explicit credibilityTier > sourceKind tier-form >
            # registry default (issue #398 Task 6 — legacy-inherited advisory).
            eff_tier = resolve_tier(ctier, skind)
            
            if not calibrated:
                if src_url and eff_tier:
                    item["suggestion"] = (
                        f"Inherited {eff_tier} from Source {src_url} — run "
                        f"compute_confidence() to apply"
                    )
                elif src_url and not eff_tier:
                    item["suggestion"] = (
                        f"Source {src_url} is untiered — call "
                        f"set_source_tier('{src_url}', 'T0'..'T4') or "
                        f"create_source(url, kind, tier=...) "
                        f"(covers all points from this source)"
                    )
                else:
                    item["suggestion"] = (
                        f"Call set_point_baseline('{pid}', alpha, beta) "
                        f"or recreate with credibility kwarg"
                    )
            else:
                # Legacy-inherited advisory: explicit/inherited baseline whose
                # source was re-tiered — suggest re-derivation via the writer.
                if src_url and ctier and item.get("calibrated"):
                    item["note"] = (
                        f"Source {src_url} tier {ctier} — baseline may predate "
                        f"issue #398; re-derive via set_source_tier"
                    )
            results.append(item)

        # Deduplicate: keep one entry per Point ID, prefer Source-based suggestions
        seen = {}
        deduped = []
        for item in results:
            pid = item["id"]
            if pid not in seen:
                seen[pid] = item
                deduped.append(item)
            elif "Source" in str(item.get("suggestion", "")) and "Source" not in str(seen[pid].get("suggestion", "")):
                for i, d in enumerate(deduped):
                    if d["id"] == pid:
                        deduped[i] = item
                        break
                seen[pid] = item
        return deduped


    # ── P0 Group 3: Checkpoint, Diary, Status, Analyze, Ingest ────

    def _content_exists(self, content: str) -> str | None:
        """Return point ID if a point with this content hash exists, else None."""
        ch = _content_hash(content)
        proj = self._get_proj()
        rows = proj.g.query(
            "MATCH (n:Point {content_hash:$ch}) "
            "WHERE (n.is_operator IS NULL OR n.is_operator = false) "
            "RETURN n.id",
            params={"ch": ch},
        ).result_set
        return rows[0][0] if rows else None

    def checkpoint(self, items: list[dict], agent_name: str = "checkpoint",
                   threshold: float = 0.95) -> dict:
        """Session batch save — two-tier dedup (content hash + embedding similarity).

        Each item: {wing, room, content}. Returns {filed: N, duplicates: M}.
        threshold: cosine similarity for semantic dedup (0.0-1.0).
                   Set to 1.0 to disable semantic dedup (hash-only).
        """
        from datetime import datetime, timezone
        filed, duplicates = 0, 0
        proj = self._get_proj()
        now = datetime.now(timezone.utc).isoformat()

        # Tier 1: content hash dedup
        to_check: list[tuple[dict, str]] = []
        seen: set[str] = set()
        for item in items:
            content = item["content"]
            ch = _content_hash(content)
            if ch in seen or self._content_exists(content):
                duplicates += 1
                continue
            seen.add(ch)
            to_check.append((item, ch))

        if not to_check:
            return {"filed": 0, "duplicates": duplicates}

        # Tier 2: embedding similarity dedup (GAP-08)
        to_file = to_check
        if threshold < 1.0:
            try:
                to_file = self._semantic_dedup(to_check, threshold)
            except ImportError:
                # Expected in zero-dependency environments — hash-only fallback.
                # #330: previously a bare `except Exception: pass` swallowed
                # real failures silently; log the designed fallback at INFO.
                _logger.info(
                    "Semantic dedup unavailable (embeddings deps missing) — "
                    "hash-only fallback"
                )
                to_file = to_check
            except Exception as e:
                # #330: real dedup backend failures must be observable — a
                # silent degrade to hash-only dedup would file duplicates.
                _logger.warning(
                    "Semantic dedup failed — falling back to hash-only dedup: %s", e
                )
                to_file = to_check

        duplicates += len(to_check) - len(to_file)

        for item, ch in to_file:
            p = self.create_point(
                "checkpoint-item", item["content"],
                wing=item.get("wing", ""),
                room=item.get("room", ""),
                content_hash=ch,
            )
            # GAP-07 (partially closed by #432 _emit_event — graph mutations
            # now emit :GraphEvent; this EventRecorded path is session-capture
            # provenance and stays separate)
            try:
                proj.apply({
                    "type": "EventRecorded",
                    "id": ulid(),
                    "eventKind": "pointAdded",
                    "subject": agent_name,
                    "object": p["id"],
                    "startedAt": now,
                })
            except Exception:
                _logger.warning("Failed to emit provenance event for point %s", p["id"])
            filed += 1

        monitoring.record_ingest()
        return {"filed": filed, "duplicates": duplicates}

    def _semantic_dedup(self, candidates: list[tuple[dict, str]],
                        threshold: float) -> list[tuple[dict, str]]:
        """Filter candidates by embedding similarity against existing checkpoint items."""
        import numpy as np
        proj = self._get_proj()
        rows = proj.g.query(
            "MATCH (n:Point {pointKind:'checkpoint-item'}) "
            "WHERE (n.is_operator IS NULL OR n.is_operator = false) "
            "RETURN n.content"
        ).result_set
        existing = [r[0] for r in rows if r[0]]
        if not existing:
            return candidates

        new_texts = [item["content"] for item, _ch in candidates]
        # ponytail: prefer sentence_transformers; TF-IDF fallback for zero-dependency path
        try:
            from sentence_transformers import SentenceTransformer
            model = SentenceTransformer("all-MiniLM-L6-v2")
            vecs = model.encode(existing + new_texts, show_progress_bar=False)
        except ImportError:
            from sklearn.feature_extraction.text import TfidfVectorizer
            vecs = TfidfVectorizer().fit_transform(existing + new_texts).toarray()

        e_vecs, n_vecs = vecs[:len(existing)], vecs[len(existing):]

        def _norm(v):
            n = np.linalg.norm(v, axis=1, keepdims=True)
            n[n == 0] = 1
            return v / n

        max_sims = (_norm(n_vecs) @ _norm(e_vecs).T).max(axis=1)
        return [(item, ch) for i, (item, ch) in enumerate(candidates)
                if max_sims[i] < threshold]

    def diary_write(self, agent_name: str, entry: str,
                    topic: str | None = None, wing: str | None = None) -> dict:
        """Write an agent diary entry. Returns the created Point."""
        from datetime import datetime, timezone
        props: dict[str, Any] = {"authoredBy": agent_name}
        if topic:
            props["topic"] = topic
        # P1 #49: use wing property only — context is deprecated
        if wing:
            props["wing"] = wing
        return self.create_point("diary", entry, **props)

    def diary_read(self, agent_name: str, last_n: int = 10,
                   wing: str | None = None) -> list[dict]:
        """Read recent diary entries for an agent, newest first."""
        proj = self._get_proj()
        if wing:
            rows = proj.g.query(
                "MATCH (n:Point {pointKind:'diary', authoredBy:$agent, wing:$wing}) "
                "WHERE (n.is_operator IS NULL OR n.is_operator = false) "
                "RETURN properties(n) ORDER BY n.createdAt DESC LIMIT $lim",
                params={"agent": agent_name, "wing": wing, "lim": last_n},
            ).result_set
        else:
            rows = proj.g.query(
                "MATCH (n:Point {pointKind:'diary', authoredBy:$agent}) "
                "WHERE (n.is_operator IS NULL OR n.is_operator = false) "
                "RETURN properties(n) ORDER BY n.createdAt DESC LIMIT $lim",
                params={"agent": agent_name, "lim": last_n},
            ).result_set
        return [r[0] for r in rows]

    def status(self) -> dict:
        """Graph health + entity counts + FalkorDB connectivity.

        Returns {connected, counts: {Point, Event, ...}, total_entities}.
        """
        proj = self._get_proj()
        connected = False
        try:
            proj.g.query("MATCH (n) RETURN count(n) LIMIT 1")
            connected = True
        except Exception:
            pass
        counts = self.taxonomy()
        total = sum(counts.values())
        result = {"connected": connected, "counts": counts, "total_entities": total}
        if self._namespace:
            result["namespace"] = self._namespace
        return result

    def ingest_corpus(self, directory: str, eventKind: str = "DocumentCreated",
                      extract_metadata: bool = False, llm_model: str | None = "gpt-5-mini",
                      progress_file: str | None = None) -> dict:
        """Batch ingestion — walk directory, parse YAML frontmatter,
        create/update Event nodes. Returns {ingested, updated, skipped, failed, errors}.

        When eventKind='AgentSession' and extract_metadata=True, runs LLM/fallback
        metadata extraction on session content before creating the Event.
        """
        import os as _os
        import json as _json
        from pathlib import Path
        from datetime import datetime, timezone

        # #329: ingest path validation — absolute, no `..`, and under the
        # optional TORTOISE_INGEST_BASE_DIR base. The stdio/CLI surface is
        # operator-trusted, but the directory walk reads host files: bound it.
        from .security import ingest_dir_is_safe, resolve_under_base
        ingest_base = None
        raw_base = _os.environ.get("TORTOISE_INGEST_BASE_DIR")
        if raw_base:
            ingest_base = _os.path.realpath(_os.path.expanduser(raw_base))
        if not ingest_dir_is_safe(directory, ingest_base):
            raise ValueError(
                f"Unsafe ingest directory: {directory!r}. Directory must be "
                f"absolute, contain no '..' components, and resolve under "
                f"TORTOISE_INGEST_BASE_DIR when set ({ingest_base or '<unset>'})."
            )
        if progress_file is not None:
            if not isinstance(progress_file, str) or not progress_file:
                raise ValueError("progress_file must be a non-empty string.")
            if not _os.path.isabs(progress_file):
                raise ValueError(f"progress_file must be absolute: {progress_file!r}")
            if ".." in Path(progress_file).parts:
                raise ValueError(f"progress_file contains '..': {progress_file!r}")
            if ingest_base is not None and resolve_under_base(progress_file, ingest_base) is None:
                raise ValueError(
                    f"progress_file {progress_file!r} not under TORTOISE_INGEST_BASE_DIR."
                )

        # Canonical boundary regex lives in session_indexer (#280 review round 6):
        # hoisted so extract/health/ingest can never drift apart again (the round-5
        # bug was exactly two copies diverging → permanent sweep non-convergence).
        from .session_indexer import _FM_RE
        ingested, updated, skipped, failed = 0, 0, 0, 0
        errors: list[dict] = []
        now = datetime.now(timezone.utc).isoformat()
        proj = self._get_proj()

        files = sorted(Path(directory).rglob("*.md"))

        # Resume from progress file
        completed_files: list[str] = []
        if progress_file:
            try:
                with open(progress_file) as pf:
                    progress = _json.load(pf)
                    completed_files = progress.get("completed_files", [])
            except Exception:
                pass
        processed_set = set(completed_files)

        # #280 review P2: two corpus files sharing a sessionId (duplicated
        # frontmatter, or rglob picking up copies) used to make the sweep
        # permanently non-convergent — MERGE is last-writer-wins, so the
        # losing copy stays hash-stale and every run re-merges both. Dedupe
        # the scan to ONE primary file per sessionId (first in sorted order);
        # non-primary copies are surfaced by session_index_health()'s
        # `duplicates` bucket instead of being re-indexed every run.
        _primary_sessions: dict[str, str] = {}

        for i, filepath in enumerate(files):
            rel_path = str(filepath)
            if rel_path in processed_set:
                skipped += 1
                continue

            try:
                text = filepath.read_text(encoding="utf-8")
            except Exception as e:
                failed += 1
                errors.append({"file": rel_path, "error": str(e), "retryable": False})
                continue

            m = _FM_RE.match(text)
            frontmatter: dict = {}
            if m:
                try:
                    import yaml as _yaml
                    parsed = _yaml.safe_load(m.group(1))
                    if isinstance(parsed, dict):
                        frontmatter = parsed
                        # YAML types (bool/int) must not leak into string fields
                        # (regression vs old line-by-line parser). Coerce known
                        # string fields to str.
                        for _k in ("doc_status", "format", "version", "title",
                                   "sessionId", "session_id", "agent"):
                            if _k in frontmatter and frontmatter[_k] is not None:
                                frontmatter[_k] = str(frontmatter[_k])
                except Exception:
                    pass  # fallback to empty dict

            # #330: content identity hash shared by both modes (hashlib is
            # module-imported). byte-identical re-ingest -> skipped.
            file_hash = hashlib.sha256(text.encode()).hexdigest()

            if eventKind == "AgentSession":
                # AgentSession branch — session indexing with metadata extraction
                session_id = frontmatter.get("sessionId") or frontmatter.get("session_id") or f"file_{filepath.stem}"
                # #280 review P2: non-primary copy of a duplicated sessionId —
                # skip deterministically (retryable:False — re-running changes
                # nothing); the duplicate is surfaced, not silently re-indexed.
                _primary = _primary_sessions.get(session_id)
                if _primary is not None and _primary != rel_path:
                    skipped += 1
                    errors.append({"file": rel_path,
                                   "error": f"duplicate sessionId '{session_id}' "
                                            f"(primary file: {_primary}) — non-primary "
                                            f"copy skipped (see session_index_health "
                                            f"'duplicates')",
                                   "retryable": False})
                    continue
                _primary_sessions.setdefault(session_id, rel_path)
                event_id = f"session_{session_id}"
                name = frontmatter.get("title", filepath.stem)
                # #280: per-session flock — serialize against the session-end hook's
                # single-file writer and concurrent sweeps (MATCH->SET is not MERGE-atomic).
                # A live holder -> skip WITHOUT marking the file complete (retried later).
                from .index_lock import SessionIndexLock
                _lock = SessionIndexLock(session_id)
                try:
                    _lock_status = _lock.acquire()
                except (OSError, AttributeError, ImportError) as _lock_err:
                    # #280 review P2 (robustness): an unusable lock path must
                    # never abort the batch sweep — unwritable/blocked lock dir
                    # (EACCES/EROFS/ENOSPC/EMFILE) or a planted symlink (ELOOP
                    # from O_NOFOLLOW) is recorded as a retryable error and the
                    # sweep continues (same as the held path).
                    skipped += 1
                    errors.append({"file": rel_path,
                                   "error": f"session lock unavailable: {_lock_err}",
                                   "retryable": True})
                    continue
                if _lock_status == "held":
                    skipped += 1
                    errors.append({"file": rel_path,
                                   "error": f"session lock held: {_lock.detail}",
                                   "retryable": True})
                    continue
                try:

                    # Check dedup
                    exists_rows = proj.g.query(
                        "MATCH (e:Event {eventId:$eid}) RETURN properties(e)",
                        params={"eid": event_id},
                    ).result_set
    
                    if exists_rows:
                        existing_props = exists_rows[0][0]
                        # #330: skip unchanged + complete events. has_keywords is the
                        # sole completeness signal (an eventStatus disjunct would skip
                        # incomplete sessions that still need enrichment).
                        has_keywords = bool(existing_props.get("keywords"))
                        if existing_props.get("file_hash") == file_hash and has_keywords:
                            skipped += 1
                            completed_files.append(rel_path)
                            continue
                        # Always extract keywords (even without LLM)
                        from .session_indexer import extract_keywords_from_frontmatter as _kw_fallback
                        if extract_metadata:
                            from .session_indexer import extract_metadata as _extract
                            try:
                                metadata = _extract(text, llm_model)
                            except Exception:
                                metadata = _kw_fallback(text)
                        else:
                            metadata = _kw_fallback(text)
    
                        merged_keywords = list(dict.fromkeys(
                            existing_props.get("keywords", []) + metadata.get("keywords", [])
                        ))[:20]
                        existing_arc = existing_props.get("content_metadata", "{}")
                        try:
                            existing_arc = _json.loads(existing_arc) if isinstance(existing_arc, str) else existing_arc
                        except Exception:
                            existing_arc = {}
                        # #330: dedup + cap the narrative arc so repeated enrichment
                        # of unchanged files never grows it unboundedly (and the
                        # state-change comparison below stays deterministic). Arc
                        # entries may be dicts (phase/topic/decisions), so dedup via
                        # a canonical JSON key (str-sorted to tolerate mixed-type
                        # keys), not dict.fromkeys.
                        _arc_seen = {}
                        for _phase in (existing_arc.get("narrative_arc", [])
                                       + metadata.get("narrative_arc", [])):
                            _key = _json.dumps(_phase, sort_keys=True, default=str)
                            _arc_seen.setdefault(_key, _phase)
                        _merged_phases = list(_arc_seen.values())
                        # Cap while preferring genuinely-new phases: keep existing
                        # ones first (already-merged), then append new ones up to
                        # the cap so a full arc never starves fresh phases.
                        _existing_phases = existing_arc.get("narrative_arc", [])
                        _new_only = [p for p in _merged_phases
                                     if _json.dumps(p, sort_keys=True, default=str)
                                     not in {_json.dumps(e, sort_keys=True, default=str)
                                             for e in _existing_phases}]
                        new_phases = (_merged_phases[:len(_existing_phases)]
                                      + _new_only)[:50]
    
                        # Normalize topics to a comparable, hashable form (#330):
                        # LLM output is unvalidated — a list-of-dicts would crash
                        # set() and abort the whole run.
                        def _norm_topics(t) -> list:
                            t = t or []
                            if not isinstance(t, list):
                                t = [t]
                            return [str(x) for x in t]
                        _new_topics = _norm_topics(metadata.get("topics",
                                                                existing_props.get("topics", [])))
                        _stored_topics = _norm_topics(existing_props.get("topics"))
                        _stored_keywords = _norm_topics(existing_props.get("keywords"))
                        _new_name = metadata.get("summary", existing_props.get("name", name))
    
                        update_props = {
                            "name": _new_name,
                            "keywords": merged_keywords,
                            "topics": _new_topics,
                            "file_hash": file_hash,
                            "content_metadata": _json.dumps({
                                "schema_version": 1,
                                "summary": metadata.get("summary", ""),
                                "narrative_arc": new_phases,
                                "issues": metadata.get("issues", []),
                                "prs": metadata.get("prs", []),
                                "critical_decisions": metadata.get("critical_decisions", []),
                            }),
                            "message_count": frontmatter.get("message_count", 0),
                        }
                        # #330: unchanged content whose enrichment produced nothing
                        # new counts as skipped, not updated (counter honesty).
                        # Compare the FULL payload that would be written (keywords,
                        # normalized topics, name, narrative_arc, issues/prs/
                        # critical_decisions — the latter feed _connect_issue_objects)
                        # so a real change in any persisted field is never
                        # miscounted as a skip.
                        if existing_props.get("file_hash") == file_hash:
                            _old_meta = existing_arc
                            changed = (
                                set(_norm_topics(merged_keywords)) != set(_stored_keywords)
                                or set(_new_topics) != set(_stored_topics)
                                or _new_name != existing_props.get("name", name)
                                or new_phases != list(existing_arc.get("narrative_arc", []))
                                or _norm_topics(metadata.get("issues", [])) != _norm_topics(_old_meta.get("issues", []))
                                or _norm_topics(metadata.get("prs", [])) != _norm_topics(_old_meta.get("prs", []))
                                or _norm_topics(metadata.get("critical_decisions", [])) != _norm_topics(_old_meta.get("critical_decisions", []))
                            )
                            if not changed:
                                skipped += 1
                                completed_files.append(rel_path)
                                continue
    
                        # #244: (re)compute the session embedding from the merged
                        # surface and store as vecf32 — None when model unavailable.
                        embedding = self._session_embedding(
                            update_props["name"], metadata.get("summary", ""),
                            merged_keywords, update_props["topics"],
                        )
                        proj.g.query(
                            "MATCH (e:Event {eventId:$eid}) SET e += $props, "
                            "e.embedding = CASE WHEN $embedding IS NOT NULL THEN vecf32($embedding) ELSE e.embedding END",
                            params={"eid": event_id, "props": update_props, "embedding": embedding},
                        )
                        updated += 1
                        completed_files.append(rel_path)
                        # Connect issue/PR references to Objects
                        self._connect_issue_objects(event_id, metadata)
                    else:
                        # New session Event — always extract keywords
                        from .session_indexer import extract_keywords_from_frontmatter as _kw_fallback
                        if extract_metadata:
                            from .session_indexer import extract_metadata as _extract
                            try:
                                metadata = _extract(text, llm_model)
                            except Exception:
                                metadata = _kw_fallback(text)
                        else:
                            metadata = _kw_fallback(text)
    
                        props = {
                            "name": metadata.get("summary", name),
                            "eventKind": eventKind,
                            "session_id": session_id,
                            "agent": frontmatter.get("agent", "pi"),
                            "source_file": rel_path,
                            "file_hash": file_hash,
                            "keywords": metadata.get("keywords", []),
                            "topics": metadata.get("topics", []),
                            "message_count": frontmatter.get("message_count", 0),
                            "startedAt": now,
                            "content_metadata": _json.dumps({
                                "schema_version": 1,
                                "summary": metadata.get("summary", ""),
                                "narrative_arc": metadata.get("narrative_arc", []),
                                "issues": metadata.get("issues", []),
                                "prs": metadata.get("prs", []),
                                "critical_decisions": metadata.get("critical_decisions", []),
                            }),
                            "eventStatus": "completed",
                            "classificationLevel": "internal",
                            "format": "markdown",
                        }
                        # #244: compute the session embedding (name + summary +
                        # keywords + topics) and store as vecf32 — None when the
                        # model is unavailable (indexing never depends on it).
                        embedding = self._session_embedding(
                            props["name"], metadata.get("summary", ""),
                            props["keywords"], props["topics"],
                        )
                        proj.g.query(
                            "MERGE (e:Event {eventId:$eid}) SET e += $props, "
                            "e.embedding = CASE WHEN $embedding IS NOT NULL THEN vecf32($embedding) ELSE e.embedding END",
                            params={"eid": event_id, "props": props, "embedding": embedding},
                        )
                        ingested += 1
                        completed_files.append(rel_path)
                        # Connect issue/PR references to Objects
                        self._connect_issue_objects(event_id, metadata)
                finally:
                    _lock.release()
            else:
                # Original DocumentCreated logic
                doc_id = str(filepath.relative_to(directory))
                title = frontmatter.get("title", filepath.stem)
                doc_kind = frontmatter.get("type", frontmatter.get("document_kind", ""))
                domain = frontmatter.get("domain", frontmatter.get("documentKnowledgeDomain", ""))

                # #330: three-way on ROW PRESENCE (new doc -> zero rows;
                # legacy event -> row with file_hash=None). RETURN e.file_hash
                # so byte-identical re-ingest is counted as skipped.
                exists_rows = proj.g.query(
                    "MATCH (e:Event {eventId:$eid}) RETURN e.file_hash",
                    params={"eid": doc_id},
                ).result_set

                props = {
                    "title": title,
                    "document_kind": doc_kind,
                    "document_knowledge_domain": domain,
                    "authored_by": frontmatter.get("authoredBy", ""),
                    "owned_by": frontmatter.get("ownedBy", ""),
                    "managed_by": frontmatter.get("managedBy", ""),
                    "governing_agreement": frontmatter.get("governedBy", frontmatter.get("governingAgreement", "")),
                    "doc_status": frontmatter.get("doc_status", "draft"),
                    "format": "markdown",
                    "version": frontmatter.get("version", ""),
                    "createdAt": frontmatter.get("created", now),
                    "updatedAt": frontmatter.get("updated", now),
                    "eventKind": "DocumentCreated",
                    "classificationLevel": "internal",
                    "file_hash": file_hash,
                }

                if not exists_rows:
                    # New document
                    proj.g.query(
                        "CREATE (e:Event {eventId:$eid}) SET e += $props",
                        params={"eid": doc_id, "props": props},
                    )
                    ingested += 1
                elif exists_rows[0][0] == file_hash:
                    # Byte-identical re-ingest — nothing to do (#330)
                    skipped += 1
                else:
                    # Changed content, or legacy event without a stored hash
                    # (None) — update and backfill the hash.
                    proj.g.query(
                        "MATCH (e:Event {eventId:$eid}) SET e += $props",
                        params={"eid": doc_id, "props": props},
                    )
                    updated += 1

            # Progress checkpoint every 100 files
            if progress_file and (i + 1) % 100 == 0:
                _save_progress(progress_file, str(directory), len(files),
                              ingested + updated + skipped + failed,
                              ingested, updated, skipped, failed, errors,
                              completed_files=completed_files)

        monitoring.record_ingest()

        if progress_file:
            _save_progress(progress_file, str(directory), len(files),
                          ingested + updated + skipped + failed,
                          ingested, updated, skipped, failed, errors,
                          completed_files=completed_files)

        return {"ingested": ingested, "updated": updated, "skipped": skipped,
                "failed": failed, "errors": errors}

    # ── Entity Resolution (GAP-01 #6987) ──────────────────────

    def suggest_entry_points(self, query: str, *, limit: int = 5,
                             kind_filter: str | None = None,
                             graph_ranker=None) -> list[dict]:
        """Entity resolution — NL query → matching entities from the graph.

        String match on content (Cypher CONTAINS) + embedding fallback.
        Returns [{id, name, kind, confidence}] sorted by confidence DESC.
        kind_filter filters by n.pointKind.
        graph_ranker: optional GraphRanker (tortoise.ranking) to rerank the
        results with graph signals (persisted EP confidence, connectivity,
        recency) — #25. Off by default for backward compatibility.
        """
        if limit < 1:
            raise ValueError(f"limit must be >= 1, got {limit}")
        q = query.strip()[:500]  # ponytail: bound to 500 chars; embedding APIs have token limits
        if not q:
            return []

        proj = self._get_proj()
        clauses = ["(n.is_operator IS NULL OR n.is_operator = false)",
                   "toLower(n.content) CONTAINS toLower($q)"]
        params = {"q": q}
        if kind_filter:
            clauses.append("n.pointKind = $kf")
            params["kf"] = kind_filter

        where = " AND ".join(clauses)
        rows = proj.g.query(
            f"MATCH (n:Point) WHERE {where} "
            "RETURN n.id, n.content, n.pointKind",
            params=params,
        ).result_set

        results = []
        q_lower = q.lower()
        for pid, content, kind in rows:
            # ponytail: guard empty content (stub nodes may have '')
            if not content:
                continue
            # Confidence formula (#22): exact match → 1.0, partial match →
            # [0.5, 1.0) via length ratio, smoothed to avoid scale collapse.
            # len(q)/len(content) alone would give 0.001 for 1-char in 1000-char
            # doc and 0.5 for 5-char in 10-char — not comparable. The 0.5 offset
            # ensures all substring matches score ≥ 0.5, reserving [0, 0.5) for
            # the hybrid fallback path (which has no substring match at all).
            # The fallback band-normalizes its RRF scores into [0, 0.5) (#22).
            if content.lower() == q_lower:
                confidence = 1.0
            else:
                ratio = len(q) / len(content)
                confidence = round(0.5 + 0.5 * ratio, 4)
            results.append({"id": pid, "name": content, "kind": kind or "", "confidence": confidence})

        results.sort(key=lambda r: r["confidence"], reverse=True)
        results = results[:limit]

        # Hybrid fallback if no string matches (Phase 0, #7748).
        # Confidence contract (#22): fallback results must live in the [0, 0.5)
        # band reserved by the substring-match formula above. Raw RRF scores are
        # NOT comparable to that band — rank-based fusion caps near 0.016 per
        # ranked list (~0.05 with 3 fused lists) while embedded FTS raw scores
        # are unbounded above — so `rrf * 0.5` landed anywhere from ~0.008 to
        # >1.0, tripping downstream conf > 0.3 thresholds. Band-normalize:
        # scale each RRF score by the set's max so the strongest fallback hit
        # lands at 0.49 (just under the 0.5 boundary) and weaker hits scale
        # proportionally. Invariant to the number of fused ranked lists (no
        # hardcoded multiplier).
        if not results:
            fts_results = self.tortoise_fts_query(q, limit=limit)
            results = []
            max_rrf = max(
                (r.get("scores", {}).get("rrf", 0.0) for r in fts_results),
                default=0.0,
            )
            # No fusion signal at all (max_rrf == 0, e.g. TF-IDF fallback with
            # all-zero similarity) → return NOTHING: every result would carry
            # confidence 0.0, which is indistinguishable from 'no match' and
            # pollutes suggest_entry_points with decoys for garbage queries
            # (stale test #test_no_match_returns_empty).
            if max_rrf <= 0:
                return []
            for r in fts_results:
                rrf = r.get("scores", {}).get("rrf", 0.0)
                confidence = round(0.49 * rrf / max_rrf, 4)
                results.append({"id": r["id"], "name": r.get("content", ""),
                                "kind": r.get("point_kind", ""), "confidence": confidence})
            results.sort(key=lambda r: r["confidence"], reverse=True)

        # #25: optional graph-informed rerank (persisted EP confidence,
        # operator connectivity, recency). Off by default (backward compat).
        if graph_ranker is not None and results:
            results = graph_ranker.rerank(results, entity_type="point")

        return results

    # ── Session Context (#6989) ──────────────────────────────

    def session_context(self) -> dict:
        """Return 'what happened last session' — diary entries, Points, Events, confidence changes.
        Returns structured dict with explicit 'no_prior_sessions' when graph is empty."""
        proj = self._get_proj()
        diary_entries = [r[0] for r in proj.g.query(
            "MATCH (n:Point {pointKind:'diary'}) "
            "WHERE (n.is_operator IS NULL OR n.is_operator = false) "
            "RETURN properties(n) ORDER BY n.createdAt DESC LIMIT 10"
        ).result_set]
        recent_points = [r[0] for r in proj.g.query(
            "MATCH (n:Point) "
            "WHERE (n.is_operator IS NULL OR n.is_operator = false) "
            "RETURN properties(n) ORDER BY n.createdAt DESC LIMIT 20"
        ).result_set]
        recent_events = [r[0] for r in proj.g.query(
            "MATCH (e:Event) RETURN properties(e) ORDER BY e.startedAt DESC LIMIT 20"
        ).result_set]
        confidence_changes = [
            {"id": r[0], "content": r[1], "pointKind": r[2],
             "confidence": r[3], "updatedAt": r[4]}
            for r in proj.g.query(
                "MATCH (n:Point) WHERE n.confidence IS NOT NULL "
                "AND (n.is_operator IS NULL OR n.is_operator = false) "
                "RETURN n.id, n.content, n.pointKind, n.confidence, n.updatedAt "
                "ORDER BY n.updatedAt DESC LIMIT 20"
            ).result_set
        ]
        no_prior = not diary_entries and not recent_points and not recent_events
        return {
            "no_prior_sessions": no_prior,
            "diary_entries": diary_entries,
            "recent_points": recent_points,
            "recent_events": recent_events,
            "confidence_changes": confidence_changes,
        }

    # ── Hybrid Search (Phase 0, #7748) ───────────────────────────

    def tortoise_fts_query(
        self,
        query: str | None = None,
        kind: str | None = None,
        *,
        entity_type: str = "point",
        min_confidence: float = 0.0,
        order_by: str = "relevance",
        graph_ranker=None,
        limit: int = 10,
        threshold: float = 0.0,
        relationship_filter: str | None = None,
        traversal_path: str | None = None,
    ) -> list[dict]:
        """Hybrid search with RRF fusion + EP annotation.

        entity_type: 'point' (default), 'event', 'subject', 'document', 'object', 'operator', or 'source'.
        Full-scan mode: omit query, set kind → all Points of that kind.
        Best-match mode: provide query → RRF fusion of FTS + vector + structural.

        Point results annotated with EP breakdown (confidence_mean + evidence + contention).
        Non-Point entities skip EP annotation.
        min_confidence defaults to 0.0 (no filter).

        relationship_filter: 'predicate:target_id' — only return points connected to
            target_id via an operator with label=predicate (e.g., 'addresses:customerSegment-1').
        traversal_path: 'FromKind→ToKind' — only return points that participate in a
            pack-declared relation chain (e.g., 'Product→Feature'). Resolved via pack registry.
        """
        from .search_engine import (
            classify_query, degradation_chain, rrf_fusion,
            annotate_ep_batch, get_relationships, fallback_tfidf,
            SearchResult, SearchScores,
            filter_by_relationship, filter_by_traversal_predicate,
        )

        if entity_type not in ("point", "event", "subject", "document", "object", "operator", "source"):
            raise ValueError(f"entity_type must be 'point', 'event', 'subject', 'document', 'object', 'operator', or 'source', got {entity_type!r}")
        if limit < 1 or limit > 10000:
            raise ValueError(f"limit must be 1-10000, got {limit}")
        if not (0.0 <= threshold <= 1.0):
            raise ValueError(f"threshold must be 0.0-1.0, got {threshold}")
        if not (0.0 <= min_confidence <= 1.0):
            raise ValueError(f"min_confidence must be 0.0-1.0, got {min_confidence}")
        if order_by not in ("relevance", "confidence", "graph"):
            raise ValueError(f"order_by must be 'relevance', 'confidence', or 'graph', got {order_by!r}")

        proj = self._get_proj()
        graph = proj.g
        label = entity_type.capitalize()  # point→Point, event→Event, subject→Subject
        # Operator: Point nodes with is_operator=true, kind=op_type
        # Source: Source nodes, kind=sourceKind
        kind_field = {"point": "pointKind", "event": "eventKind", "subject": "subjectKind", "document": "documentKind", "object": "objectKind", "operator": "op_type", "source": "sourceKind"}[entity_type]

        # 1. Classify query → determine active strategies
        strategies = classify_query(query, kind)
        is_full_scan = (query is None and kind is not None)

        # Expand kind early for pack-aware structural query + kind filter
        expanded_kinds = self._expand_kind(kind) if kind else None

        # 2. Get query vector if needed (all core entity types now have embeddings #7845)
        query_vec = None
        if strategies.get("vector") and query and query.strip():
            try:
                from .embeddings import EmbeddingModel
                model = EmbeddingModel.get()
                if model:
                    query_vec = model.encode([query])[0].tolist()
            except Exception:
                pass  # Graceful — vector strategy will degrade

        # 3. Run retrieval with degradation
        is_embedded = getattr(proj, '_is_embedded', True)
        # Full-scan mode: no truncation — return ALL Points in context (#7811 completeness)
        str_limit = limit * 2 if not is_full_scan else 100000
        raw_results = degradation_chain(
            graph, query, kind, query_vec, strategies,
            entity_type=entity_type, limit=str_limit,
            is_embedded=is_embedded,
        )

        if not raw_results:
            # All strategies failed — fallback to in-memory TF-IDF (Point only)
            if query and entity_type == "point":
                points = self.query(kind=kind)
                return fallback_tfidf(query, points, limit=limit)
            return []

        # 4. Fuse via RRF (skip if single strategy or full-scan)
        if is_full_scan or len(raw_results) == 1:
            strat_name, ranked = next(iter(raw_results.items()))
            # Apply threshold filter (score floor)
            fused = {pid: score for pid, score in ranked if score >= threshold}
            match_source = strat_name
        else:
            ranked_lists = list(raw_results.values())
            fused = rrf_fusion(ranked_lists)
            # Apply threshold filter to RRF scores
            if threshold > 0:
                fused = {pid: score for pid, score in fused.items() if score >= threshold}
            match_source = "rrf"

        # 5. Apply kind filter BEFORE truncating (skip if structural-only already filtered)
        result_ids = list(fused.keys())
        if entity_type == "source":
            id_field = "url"
        elif entity_type == "event":
            id_field = "eventId"
        else:
            id_field = "id"
        # Graph label for MATCH (operators are Point nodes with is_operator=true)
        graph_label = "Point" if entity_type == "operator" else label

        if kind and query is not None and result_ids:
            expanded = expanded_kinds
            kind_ids = set()
            extra_clause = "AND n.is_operator = true" if entity_type == "operator" else ""
            try:
                if len(expanded) == 1:
                    kind_rows = graph.query(
                        f"MATCH (n:{graph_label}) WHERE n.{kind_field} = $kind {extra_clause} AND n.{id_field} IN $ids RETURN n.{id_field}",
                        params={"kind": expanded[0], "ids": result_ids},
                    ).result_set
                else:
                    placeholders = [f"$kind_{i}" for i in range(len(expanded))]
                    params_dict: dict[str, Any] = {"ids": result_ids}
                    for i, k in enumerate(expanded):
                        params_dict[f"kind_{i}"] = k
                    kind_rows = graph.query(
                        f"MATCH (n:{graph_label}) WHERE n.{kind_field} IN [{', '.join(placeholders)}] {extra_clause} AND n.{id_field} IN $ids RETURN n.{id_field}",
                        params=params_dict,
                    ).result_set
                kind_ids = {row[0] for row in kind_rows}
            except Exception:
                kind_ids = set(result_ids)  # Pass-through on error
            result_ids = [pid for pid in result_ids if pid in kind_ids]

        # 5b. Apply relationship_filter (predicate:target_id format)
        if relationship_filter and result_ids:
            parts = relationship_filter.split(":", 1)
            if len(parts) == 2:
                pred, tid = parts[0].strip(), parts[1].strip()
                if pred and tid:
                    result_ids = filter_by_relationship(
                        graph, result_ids, pred, tid,
                        entity_type=entity_type, id_field=id_field,
                    )
                else:
                    _logger.warning("Invalid relationship_filter format: %s", relationship_filter)
            else:
                _logger.warning(
                    "relationship_filter must be 'predicate:target_id', got: %s",
                    relationship_filter,
                )

        # 5c. Apply traversal_path (e.g., 'Product→Feature') — resolve via pack registry
        if traversal_path and result_ids:
            resolved = self._resolve_traversal_path(traversal_path)
            if resolved:
                pred = resolved["predicate"]
                result_ids = filter_by_traversal_predicate(
                    graph, result_ids, pred,
                    entity_type=entity_type, id_field=id_field,
                )
            else:
                _logger.warning(
                    "traversal_path %r could not be resolved to a pack relation",
                    traversal_path,
                )

        # Truncate AFTER filtering
        result_ids = result_ids[:limit]

        # 6. EP annotation (Point only)
        ep_breakdowns = annotate_ep_batch(graph, result_ids) if entity_type == "point" else {}

        # 7. Fetch entity content in BATCH (not N+1)
        entity_data: dict[str, dict] = {}
        try:
            if entity_type == "point":
                rows = graph.query(
                    "MATCH (n:Point) WHERE n.id IN $ids RETURN n.id, n.content, n.pointKind",
                    params={"ids": result_ids},
                ).result_set
                for row in rows:
                    pid = row[0]
                    entity_data[pid] = {
                        "content": row[1],
                        "kind": row[2],
                    }
            elif entity_type == "event":
                rows = graph.query(
                    "MATCH (n:Event) WHERE n.eventId IN $ids RETURN n.eventId, n.subject, n.eventKind",
                    params={"ids": result_ids},
                ).result_set
                for row in rows:
                    eid = row[0]
                    entity_data[eid] = {
                        "content": row[1] or "",
                        "kind": row[2] or "",
                    }
            elif entity_type == "subject":
                rows = graph.query(
                    "MATCH (n:Subject) WHERE n.id IN $ids RETURN n.id, n.name, n.subjectKind",
                    params={"ids": result_ids},
                ).result_set
                for row in rows:
                    sid = row[0]
                    entity_data[sid] = {
                        "content": row[1] or "",
                        "kind": row[2] or "",
                    }
            elif entity_type == "document":
                rows = graph.query(
                    "MATCH (n:Document) WHERE n.id IN $ids "
                    "RETURN n.id, n.title, n.documentKind, n.topics, n.summary, "
                    "n.sessionId, n.eventId, n.sourcePath",
                    params={"ids": result_ids},
                ).result_set
                for row in rows:
                    did = row[0]
                    entity_data[did] = {
                        "content": row[1] or "",
                        "kind": row[2] or "",
                        "topics": row[3] or [],
                        "summary": row[4] or "",
                        "sessionId": row[5] or "",
                        "eventId": row[6] or "",
                        "sourcePath": row[7] or "" if len(row) > 7 else "",
                    }
            elif entity_type == "object":
                rows = graph.query(
                    "MATCH (n:Object) WHERE n.id IN $ids RETURN n.id, n.name, n.objectKind",
                    params={"ids": result_ids},
                ).result_set
                for row in rows:
                    oid = row[0]
                    entity_data[oid] = {
                        "content": row[1] or "",
                        "kind": row[2] or "",
                    }
            elif entity_type == "operator":
                rows = graph.query(
                    "MATCH (n:Point {is_operator: true}) WHERE n.id IN $ids RETURN n.id, n.label, n.op_type",
                    params={"ids": result_ids},
                ).result_set
                for row in rows:
                    oid = row[0]
                    entity_data[oid] = {
                        "content": row[1] or "",  # label is searchable text
                        "kind": row[2] or "",    # op_type is kind
                    }
            elif entity_type == "source":
                rows = graph.query(
                    "MATCH (n:Source) WHERE n.url IN $ids RETURN n.url, n.title, n.sourceKind",
                    params={"ids": result_ids},
                ).result_set
                for row in rows:
                    sid = row[0]
                    entity_data[sid] = {
                        "content": row[1] or "",
                        "kind": row[2] or "",
                    }
        except Exception:
            _logger.warning("Batch content fetch failed — returning results with minimal metadata")
            for pid in result_ids:
                entity_data[pid] = {"content": "", "kind": ""}

        # 7.5. Fetch relationships for result Points (Point only)
        point_relationships = get_relationships(graph, result_ids) if entity_type == "point" else {}

        # 8. Build SearchResult objects, filter, and order
        results = []
        for pid in result_ids:
            pt = entity_data.get(pid)
            if not pt:
                continue
            content, pt_kind = pt["content"], pt["kind"]
            ep = ep_breakdowns.get(pid) if entity_type == "point" else None
            # #125 capture metadata (document entity_type)
            cap_topics = pt.get("topics", [])
            cap_summary = pt.get("summary", "")
            cap_session = pt.get("sessionId", "")
            cap_event = pt.get("eventId", "")
            cap_source_path = pt.get("sourcePath", "")  # #167

            # Apply min_confidence filter (Point only; non-Point always pass)
            if entity_type == "point" and ep and ep.confidence_mean < min_confidence:
                continue

            # Build scores
            scores = SearchScores(rrf=fused.get(pid, 0.0))
            if "fts" in raw_results:
                for fid, fscore in raw_results["fts"]:
                    if fid == pid:
                        scores.fts = fscore
                        break
            if "vector" in raw_results:
                for vid, vscore in raw_results["vector"]:
                    if vid == pid:
                        scores.vector = vscore
                        break
            if "structural" in raw_results:
                for sid, sscore in raw_results["structural"]:
                    if sid == pid:
                        scores.structural = sscore
                        break

            result = SearchResult(
                id=pid,
                content=content,
                point_kind=pt_kind,
                scores=scores,
                match_source=match_source,
                ep=ep,
                relationships=point_relationships.get(pid, []),
                topics=cap_topics,
                summary=cap_summary,
                session_id=cap_session,
                event_id=cap_event,
                source_path=cap_source_path,
            )
            results.append(result)

        # 9. Order results
        if order_by == "graph":
            # #25: graph-informed rerank — weighted fusion of similarity +
            # persisted EP confidence + operator connectivity + recency decay.
            # Requires the caller to allow a large enough candidate pool (limit
            # here is the pool size; final length is capped below).
            from .ranking import GraphRanker
            ranker = graph_ranker or GraphRanker(proj)
            dicts = [r.to_dict() for r in results]
            return ranker.rerank(dicts, entity_type=entity_type)[:limit]
        if order_by == "confidence":
            # #25: sort by the PERSISTED EP confidence (n.confidence, written
            # by compute_confidence), not the structural impl/(impl+nand) proxy
            # from annotate_ep_batch (which is edge-ratio, not belief).
            from .ranking import GraphRanker
            ranker = graph_ranker or GraphRanker(proj)
            signals = ranker._fetch_signals([r.id for r in results], entity_type)
            results.sort(
                key=lambda r: signals.get(r.id, {}).get("confidence", 0.5),
                reverse=True,
            )
        # Default: RRF relevance order (already in fused order)

        return [r.to_dict() for r in results[:limit]]

    # ── Multi-tenancy (#7001) ─────────────────────────────────

    # ── Control Plane: Team CRUD ───────────────────────────────────

    def team_create(self, name: str, *, idempotency_key: str | None = None) -> dict:
        """Create a team with its own graph namespace.

        Writes to the control_plane registry graph. Creates a tenant
        graph (team_{name}) for Point/Operator storage.

        Returns {name, graph_name, api_key, id}.
        """
        import re, uuid
        from datetime import datetime, timezone
        from tortoise.auth import hash_api_key
        from .exceptions import ControlPlaneError

        # Input validation
        if not name or not name.strip():
            raise ControlPlaneError("Team name must not be empty")
        if len(name) > 64:
            raise ControlPlaneError("Team name must be 64 characters or fewer")
        if not re.match(r'^[a-zA-Z0-9][a-zA-Z0-9_-]*$', name):
            raise ControlPlaneError(
                f"Invalid team name: {name!r}. Use alphanumeric, hyphens, underscores."
            )

        api_key = f"tt_{uuid.uuid4().hex}"
        key_hash = hash_api_key(api_key)
        graph_name = f"team_{name}"
        proj = self._get_proj()
        reg = self._get_registry()
        now = datetime.now(timezone.utc).isoformat()

        # Idempotency — check registry graph for existing team
        if idempotency_key:
            existing = reg.query(
                "MATCH (t:Team {idempotency_key:$ik}) RETURN t.id, t.name",
                params={"ik": idempotency_key},
            ).result_set
            if existing:
                row = existing[0]
                return {"name": name, "graph_name": graph_name,
                        "api_key": api_key, "id": row[0],
                        "existing": True}

        # Duplicate name check
        dup = reg.query(
            "MATCH (t:Team {name:$name}) RETURN count(t) > 0",
            params={"name": name},
        ).result_set[0][0]
        if dup:
            raise ControlPlaneError(f"Team {name!r} already exists")

        tid = ulid()
        # Tier-driven limits from product/pricing.json (decision 1d) — no max_teams
        # field: multi-team is a user-level capability, NOT a tier limit.
        from tortoise.pricing import tier_limits
        lim = tier_limits("free")  # provision defaults to Free; upgrades = billing epic
        reg.query(
            "CREATE (t:Team {id:$id, name:$name, api_key:$key, "
            "graph_name:$gn, createdAt:$now, tier:'free', "
            "max_graphs:$max_graphs, max_users:$max_users, "
            "max_api_keys:$max_keys, ops_allowance:$ops, graph_size_cap:$nodes})",
            params={"id": tid, "name": name, "key": key_hash,
                    "gn": graph_name, "now": now,
                    "max_graphs": lim["max_graphs_per_team"],
                    "max_users": lim["max_users_per_team"],
                    "max_keys": lim["max_api_keys"],
                    "ops": lim["included_write_ops_per_month"],
                    "nodes": lim["max_graph_nodes"]},
        )
        if idempotency_key:
            reg.query(
                "MATCH (t:Team {id:$id}) SET t.idempotency_key = $ik",
                params={"id": tid, "ik": idempotency_key},
            )
        try:
            team_graph = proj.db.select_graph(graph_name)
            team_graph.query(
                "CREATE (:TeamMeta {name:$name, created:$now})",
                params={"name": name, "now": now},
            )
            # Graph node (team→graph 1:N, product ontology): the default graph
            self._graph_create(tid, "default", kind="default", namespace=graph_name)
        except Exception:
            try:
                reg.query("MATCH (t:Team {id:$id}) DETACH DELETE t",
                          params={"id": tid})
            except Exception:
                pass
            raise

        self._audit(tid, None, "team_create", resource_type="team", resource_id=tid)
        return {"name": name, "graph_name": graph_name, "api_key": api_key, "id": tid}

    def _graph_create(self, team_id: str, name: str, *, kind: str = "custom",
                      namespace: str | None = None) -> dict:
        """Create a Graph node in the registry (team→graph 1:N).

        The tenant namespace for a custom graph is team_{team_id}_{graph_id};
        custom namespaces are NOT minted until a consumer exists (E2E-11
        decision — v1 writes resolve the default graph only). The default
        graph's namespace is the team namespace itself (back-compat).
        """
        import uuid as _uuid
        from datetime import datetime, timezone as _tz
        reg = self._get_registry()
        gid = f"g_{_uuid.uuid4().hex[:16]}"
        ns = namespace or f"team_{team_id}_{gid}"
        now = datetime.now(_tz.utc).isoformat()
        reg.query(
            "CREATE (g:Graph {id:$gid, team_id:$tid, name:$name, kind:$kind, "
            "namespace:$ns, created_at:$now})",
            params={"gid": gid, "tid": team_id, "name": name,
                    "kind": kind, "ns": ns, "now": now},
        )
        return {"graph_id": gid, "name": name, "kind": kind, "namespace": ns}

    def graph_list(self, team_id: str) -> list[dict]:
        """List Graph nodes for a team (default graph first)."""
        reg = self._get_registry()
        rows = reg.query(
            "MATCH (g:Graph {team_id:$tid}) RETURN properties(g) "
            "ORDER BY CASE g.kind WHEN 'default' THEN 0 ELSE 1 END, g.created_at",
            params={"tid": team_id},
        ).result_set
        out = []
        for (props,) in rows:
            out.append({
                "graph_id": props.get("id"),
                "team_id": props.get("team_id"),
                "name": props.get("name"),
                "kind": props.get("kind", "custom"),
                "namespace": props.get("namespace"),
            })
        return out

    def graph_count(self, team_id: str) -> int:
        reg = self._get_registry()
        return reg.query(
            "MATCH (g:Graph {team_id:$tid}) RETURN count(g)",
            params={"tid": team_id},
        ).result_set[0][0]

    def team_get(self, team_id: str) -> dict | None:
        """Get a team by ID. Returns None if not found."""
        reg = self._get_registry()
        rows = reg.query(
            "MATCH (t:Team {id:$id}) RETURN properties(t)",
            params={"id": team_id},
        ).result_set
        return rows[0][0] if rows else None

    def team_list(self) -> list[dict]:
        """List all teams."""
        reg = self._get_registry()
        rows = reg.query(
            "MATCH (t:Team) RETURN properties(t) ORDER BY t.createdAt"
        ).result_set
        return [r[0] for r in rows]

    def team_update(self, team_id: str, **fields) -> dict:
        """Update mutable team fields."""
        from .exceptions import ControlPlaneError
        allowed = {
            "name", "tier", "stripe_customer_id", "subscription_id",
            "backup_enabled", "max_users", "max_graphs",
            # #329 relief path: quota limits settable via the control plane so
            # a team at cap can be upgraded (no REST surface exists yet — the
            # fields are SDK/registry-level; get_current_team honors them).
            "max_points", "max_api_keys", "max_sessions",
        }
        invalid = set(fields.keys()) - allowed
        if invalid:
            raise ControlPlaneError(f"Invalid team fields: {invalid}")
        reg = self._get_registry()
        reg.query(
            "MATCH (t:Team {id:$id}) SET t += $fields",
            params={"id": team_id, "fields": fields},
        )
        self._audit(team_id, None, "team_update", resource_type="team",
                     resource_id=team_id)
        return self.team_get(team_id) or {}

    def team_delete(self, team_id: str, *, confirmation: str) -> dict:
        """Delete a team and all associated control-plane entities.

        Cascading: Membership, APIKey, Invitation nodes are deleted.
        Tenant graphs are dropped (best-effort — FalkorDBLite may skip).
        Postgres audit_events are preserved (immutable).

        Requires confirmation matching the team name.
        """
        from .exceptions import ControlPlaneError
        team = self.team_get(team_id)
        if team is None:
            raise ControlPlaneError(f"Team {team_id!r} not found")
        if confirmation != team.get("name", ""):
            raise ControlPlaneError(
                "Confirmation must match team name exactly"
            )

        reg = self._get_registry()
        # Cascade delete: Membership, APIKey, Invitation
        reg.query(
            "MATCH (m:Membership {team_id:$tid}) DETACH DELETE m",
            params={"tid": team_id},
        )
        reg.query(
            "MATCH (k:APIKey {team_id:$tid}) DETACH DELETE k",
            params={"tid": team_id},
        )
        reg.query(
            "MATCH (i:Invitation {team_id:$tid}) DETACH DELETE i",
            params={"tid": team_id},
        )
        reg.query(
            "MATCH (t:Team {id:$id}) DETACH DELETE t",
            params={"id": team_id},
        )

        # Best-effort tenant graph deletion
        graph_name = team.get("graph_name", f"team_{team.get('name', '')}")
        proj = self._get_proj()
        try:
            if hasattr(proj.db, 'delete_graph'):
                proj.db.delete_graph(graph_name)
            else:
                _logger.debug("delete_graph not available (FalkorDBLite) — skipping")
        except Exception:
            _logger.debug("Failed to delete tenant graph %s — skipping", graph_name)

        self._audit(team_id, None, "team_delete", resource_type="team",
                     resource_id=team_id)
        return {"deleted": True, "team_id": team_id}

    def migrate_teams_to_registry(self) -> dict:
        """One-shot: move Team nodes from tortoise graph to control_plane graph.

        Idempotent — running twice produces the same state.
        Existing Team nodes in the tortoise graph are marked as outdated.
        """
        proj = self._get_proj()
        reg = self._get_registry()
        teams = proj.g.query("MATCH (t:Team) RETURN properties(t)").result_set
        migrated, skipped = 0, 0
        for row in teams:
            team = row[0]
            name = team.get("name", "")
            # Check if already in registry
            existing = reg.query(
                "MATCH (t:Team {name:$name}) RETURN count(t) > 0",
                params={"name": name},
            ).result_set[0][0]
            if existing:
                skipped += 1
                continue
            reg.query(
                "CREATE (t:Team {id:$id, name:$name, api_key:$key, "
                "graph_name:$gn, createdAt:$now})",
                params={
                    "id": team.get("id", ulid()),
                    "name": name,
                    "key": team.get("api_key", ""),
                    "gn": team.get("graph_name", f"team_{name}"),
                    "now": team.get("createdAt", ""),
                },
            )
            migrated += 1
        if migrated > 0:
            proj.g.query("MATCH (t:Team) SET t.status = 'outdated'")
        return {"migrated": migrated, "skipped": skipped}

    # ── Control Plane: Membership CRUD ─────────────────────────────

    def membership_create(self, team_id: str, user_id: str, role: str) -> dict:
        """Add a user to a team with a given role.

        Validates role, team existence, and max_users constraint.
        Creates BELONGS_TO edge to Team.
        """
        from datetime import datetime, timezone
        from .exceptions import ControlPlaneError

        if role not in ("owner", "admin", "member"):
            raise ControlPlaneError(
                f"Invalid role {role!r}. Must be 'owner', 'admin', or 'member'."
            )

        team = self.team_get(team_id)
        if team is None:
            raise ControlPlaneError(f"Team {team_id!r} not found")

        # Check max_users constraint
        max_users = team.get("max_users")
        if max_users is not None:
            reg = self._get_registry()
            count = reg.query(
                "MATCH (m:Membership {team_id:$tid}) "
                "WHERE m.status = 'active' RETURN count(m)",
                params={"tid": team_id},
            ).result_set[0][0]
            if count >= max_users:
                raise ControlPlaneError(
                    f"Team at max users ({max_users}). Upgrade to add more."
                )

        mid = ulid()
        now = datetime.now(timezone.utc).isoformat()
        reg = self._get_registry()
        reg.query(
            "CREATE (m:Membership {id:$id, user_id:$uid, team_id:$tid, "
            "role:$role, status:'active', joinedAt:$now, created_at:$now})",
            params={"id": mid, "uid": user_id, "tid": team_id,
                    "role": role, "now": now},
        )
        # Create BELONGS_TO edge
        reg.query(
            "MATCH (m:Membership {id:$mid}), (t:Team {id:$tid}) "
            "CREATE (m)-[:BELONGS_TO]->(t)",
            params={"mid": mid, "tid": team_id},
        )

        self._audit(team_id, user_id, "membership_create",
                     resource_type="membership", resource_id=mid)
        return {"id": mid, "team_id": team_id, "user_id": user_id, "role": role}

    def membership_get(self, membership_id: str) -> dict | None:
        """Get a membership by ID."""
        reg = self._get_registry()
        rows = reg.query(
            "MATCH (m:Membership {id:$id}) RETURN properties(m)",
            params={"id": membership_id},
        ).result_set
        return rows[0][0] if rows else None

    def membership_list(self, team_id: str) -> list[dict]:
        """List all memberships for a team."""
        reg = self._get_registry()
        rows = reg.query(
            "MATCH (m:Membership {team_id:$tid}) RETURN properties(m)",
            params={"tid": team_id},
        ).result_set
        return [r[0] for r in rows]

    def membership_update_role(self, membership_id: str,
                                new_role: str) -> dict:
        """Update a membership's role."""
        from .exceptions import ControlPlaneError
        if new_role not in ("owner", "admin", "member"):
            raise ControlPlaneError(
                f"Invalid role {new_role!r}. Must be 'owner', 'admin', or 'member'."
            )
        m = self.membership_get(membership_id)
        if m is None:
            raise ControlPlaneError(f"Membership {membership_id!r} not found")
        reg = self._get_registry()
        reg.query(
            "MATCH (m:Membership {id:$id}) SET m.role = $role",
            params={"id": membership_id, "role": new_role},
        )
        self._audit(m["team_id"], m["user_id"], "membership_update_role",
                     resource_type="membership", resource_id=membership_id)
        return self.membership_get(membership_id) or {}

    def membership_delete(self, membership_id: str) -> dict:
        """Delete a membership. Idempotent."""
        m = self.membership_get(membership_id)
        if m is None:
            return {"deleted": False, "reason": "not found"}
        reg = self._get_registry()
        reg.query(
            "MATCH (m:Membership {id:$id}) DETACH DELETE m",
            params={"id": membership_id},
        )
        self._audit(m["team_id"], m["user_id"], "membership_delete",
                     resource_type="membership", resource_id=membership_id)
        return {"deleted": True, "membership_id": membership_id}

    # ── Control Plane: APIKey CRUD ─────────────────────────────────

    def _verify_hashed_lookup(self, label: str, prop: str, plaintext: str) -> list[dict]:
        """Verify a plaintext secret against stored salted hashes in the registry.

        hash_api_key() embeds a per-key random salt ("salt:hash"), so we can
        NOT look up by exact hash match — the lookup hash would never equal the
        stored hash (same root cause as #130).

        #687: For APIKey nodes, we short-circuit the O(keys) scan by filtering
        on key_prefix (key[:10] = "tt_<8 hex chars>"). The key_prefix index
        (created in _ensure_registry_indexes) makes this O(1) per lookup.
        Falls back to full scan for legacy provision_tenant keys whose
        key_prefix was set to team_id[:8] (which won't match token[:10]).
        """
        from tortoise.auth import verify_api_key
        reg = self._get_registry()

        # #687: indexed key_prefix lookup avoids O(keys) PBKDF2 scan
        if label == "APIKey" and plaintext.startswith("tt_"):
            prefix = plaintext[:10]
            rows = reg.query(
                f"MATCH (n:{label}) WHERE n.key_prefix = $prefix "
                f"RETURN n.{prop}, properties(n)",
                params={"prefix": prefix},
            ).result_set
            out = []
            for stored_hash, props in rows:
                if verify_api_key(plaintext, stored_hash):
                    out.append(props)
            if out:
                return out
            # Fall through to full scan for legacy provision_tenant keys
            # (key_prefix = team_id[:8] won't match token[:10] = "tt_<8 hex>")

        rows = reg.query(
            f"MATCH (n:{label}) RETURN n.{prop}, properties(n)"
        ).result_set
        out = []
        for stored_hash, props in rows:
            if verify_api_key(plaintext, stored_hash):
                out.append(props)
        return out

    def apikey_create(self, team_id: str, created_by: str) -> dict:
        """Generate an API key for a team.

        Stores SHA-256 hash (never plaintext). Plaintext returned once.
        """
        import uuid
        from datetime import datetime, timezone
        from tortoise.auth import hash_api_key
        from .exceptions import ControlPlaneError

        team = self.team_get(team_id)
        if team is None:
            raise ControlPlaneError(f"Team {team_id!r} not found")

        api_key = f"tt_{uuid.uuid4().hex}"
        key_hash = hash_api_key(api_key)
        key_prefix = api_key[:10]
        kid = ulid()
        now = datetime.now(timezone.utc).isoformat()

        reg = self._get_registry()
        reg.query(
            "CREATE (k:APIKey {id:$id, team_id:$tid, key_hash:$kh, "
            "key_prefix:$kp, created_by:$cb, created_at:$now})",
            params={"id": kid, "tid": team_id, "kh": key_hash,
                    "kp": key_prefix, "cb": created_by, "now": now},
        )
        # BELONGS_TO edge
        reg.query(
            "MATCH (k:APIKey {id:$kid}), (t:Team {id:$tid}) "
            "CREATE (k)-[:BELONGS_TO]->(t)",
            params={"kid": kid, "tid": team_id},
        )

        self._audit(team_id, created_by, "apikey_create",
                     resource_type="apikey", resource_id=kid)
        return {"id": kid, "key_prefix": key_prefix, "api_key": api_key,
                "team_id": team_id, "created_at": now}

    def apikey_list(self, team_id: str) -> list[dict]:
        """List API keys for a team (no plaintext or hashes)."""
        reg = self._get_registry()
        rows = reg.query(
            "MATCH (k:APIKey {team_id:$tid}) "
            "RETURN k.id, k.key_prefix, k.created_by, k.created_at, "
            "k.last_used_at, k.revoked_at",
            params={"tid": team_id},
        ).result_set
        keys = []
        for r in rows:
            keys.append({
                "id": r[0], "key_prefix": r[1], "created_by": r[2],
                "created_at": r[3], "last_used_at": r[4], "revoked_at": r[5],
            })
        return keys

    def apikey_revoke(self, key_id: str) -> dict:
        """Revoke an API key (soft delete — sets revoked_at). Idempotent."""
        from datetime import datetime, timezone
        reg = self._get_registry()
        rows = reg.query(
            "MATCH (k:APIKey {id:$id}) RETURN k.revoked_at, k.team_id",
            params={"id": key_id},
        ).result_set
        if not rows:
            return {"revoked": False, "reason": "not found"}
        if rows[0][0] is not None:
            return {"revoked": True, "already": True, "key_id": key_id}
        now = datetime.now(timezone.utc).isoformat()
        reg.query(
            "MATCH (k:APIKey {id:$id}) SET k.revoked_at = $now",
            params={"id": key_id, "now": now},
        )
        self._audit(rows[0][1], None, "apikey_revoke",
                     resource_type="apikey", resource_id=key_id)
        return {"revoked": True, "key_id": key_id, "revoked_at": now}

    def apikey_verify(self, key_plaintext: str) -> dict | None:
        """Verify an API key against stored hashes.

        Returns {team_id, key_id} if valid, None if not found or revoked.
        Uses salted-hash verification (per-key salt means exact-hash lookup
        never matches — see #130, #139).
        """
        matches = [
            p for p in self._verify_hashed_lookup("APIKey", "key_hash", key_plaintext)
            if p.get("revoked_at") is None
        ]
        if matches:
            return {"team_id": matches[0]["team_id"], "key_id": matches[0]["id"]}
        return None

    # ── Control Plane: Invitation CRUD ─────────────────────────────

    def invitation_create(self, team_id: str, email: str, role: str,
                          created_by: str) -> dict:
        """Create an invitation with 7-day expiry.

        Token is hashed for storage; plaintext returned once.
        """
        import uuid
        from datetime import datetime, timedelta, timezone
        from tortoise.auth import hash_api_key
        from .exceptions import ControlPlaneError

        team = self.team_get(team_id)
        if team is None:
            raise ControlPlaneError(f"Team {team_id!r} not found")
        if role not in ("owner", "admin"):
            raise ControlPlaneError(
                f"Invalid role {role!r}. Must be 'owner' or 'admin'."
            )

        # Reject duplicate pending invitations for same email+team
        reg = self._get_registry()
        dup = reg.query(
            "MATCH (i:Invitation {team_id:$tid, email:$email}) "
            "WHERE i.accepted_at IS NULL AND (i.status IS NULL OR i.status <> 'revoked') "
            "RETURN count(i) > 0",
            params={"tid": team_id, "email": email},
        ).result_set[0][0]
        if dup:
            raise ControlPlaneError(
                f"Pending invitation already exists for {email} in this team"
            )

        token = str(uuid.uuid4())
        token_hash = hash_api_key(token)
        iid = ulid()
        now = datetime.now(timezone.utc).isoformat()
        expires_at = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()

        reg.query(
            "CREATE (i:Invitation {id:$id, team_id:$tid, email:$email, "
            "role:$role, token_hash:$th, created_by:$cb, "
            "created_at:$now, expires_at:$exp, accepted_at:null})",
            params={"id": iid, "tid": team_id, "email": email,
                    "role": role, "th": token_hash, "cb": created_by,
                    "now": now, "exp": expires_at},
        )
        # FOR_TEAM edge
        reg.query(
            "MATCH (i:Invitation {id:$iid}), (t:Team {id:$tid}) "
            "CREATE (i)-[:FOR_TEAM]->(t)",
            params={"iid": iid, "tid": team_id},
        )

        self._audit(team_id, created_by, "invitation_create",
                     resource_type="invitation", resource_id=iid)
        return {"id": iid, "email": email, "role": role,
                "expires_at": expires_at, "token": token}

    def invitation_list(self, team_id: str) -> list[dict]:
        """List invitations for a team (no token hashes)."""
        reg = self._get_registry()
        rows = reg.query(
            "MATCH (i:Invitation {team_id:$tid}) "
            "RETURN i.id, i.email, i.role, i.created_by, i.created_at, "
            "i.expires_at, i.accepted_at, i.status",
            params={"tid": team_id},
        ).result_set
        invs = []
        for r in rows:
            invs.append({
                "id": r[0], "email": r[1], "role": r[2],
                "created_by": r[3], "created_at": r[4],
                "expires_at": r[5], "accepted_at": r[6], "status": r[7],
            })
        return invs

    def invitation_get_by_token(self, token_plaintext: str) -> dict | None:
        """Look up an invitation by its plaintext token (salted-hash verify)."""
        matches = self._verify_hashed_lookup("Invitation", "token_hash", token_plaintext)
        return matches[0] if matches else None

    def invitation_accept(self, invitation_id: str, user_id: str) -> dict:
        """Accept an invitation and create a membership.

        Checks expiry and single-use (not already accepted).
        """
        from datetime import datetime, timezone
        from .exceptions import ControlPlaneError

        inv = self.invitation_get_by_id(invitation_id)
        if inv is None:
            raise ControlPlaneError(f"Invitation {invitation_id!r} not found")

        expires_at = inv.get("expires_at", "")
        now = datetime.now(timezone.utc)
        if expires_at:
            exp = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            if now > exp:
                raise ControlPlaneError("Invitation has expired")

        if inv.get("accepted_at"):
            raise ControlPlaneError("Invitation already accepted")

        if inv.get("status") == "revoked":
            raise ControlPlaneError("Invitation has been revoked")

        # Accept: mark as accepted + create membership
        now_iso = now.isoformat()
        reg = self._get_registry()
        reg.query(
            "MATCH (i:Invitation {id:$id}) SET i.accepted_at = $now",
            params={"id": invitation_id, "now": now_iso},
        )

        membership = self.membership_create(
            team_id=inv["team_id"],
            user_id=user_id,
            role=inv.get("role", "admin"),
        )

        self._audit(inv["team_id"], user_id, "invitation_accept",
                     resource_type="invitation", resource_id=invitation_id)
        return {"membership_id": membership["id"],
                "team_id": inv["team_id"], "accepted_at": now_iso}

    def invitation_get_by_id(self, invitation_id: str) -> dict | None:
        """Get an invitation by its ULID."""
        reg = self._get_registry()
        rows = reg.query(
            "MATCH (i:Invitation {id:$id}) RETURN properties(i)",
            params={"id": invitation_id},
        ).result_set
        return rows[0][0] if rows else None

    def invitation_revoke(self, invitation_id: str) -> dict:
        """Revoke an invitation (soft delete). Idempotent."""
        inv = self.invitation_get_by_id(invitation_id)
        if inv is None:
            return {"revoked": False, "reason": "not found"}
        if inv.get("status") == "revoked":
            return {"revoked": True, "already": True,
                    "invitation_id": invitation_id}
        reg = self._get_registry()
        reg.query(
            "MATCH (i:Invitation {id:$id}) SET i.status = 'revoked'",
            params={"id": invitation_id},
        )
        self._audit(inv["team_id"], None, "invitation_revoke",
                     resource_type="invitation", resource_id=invitation_id)
        return {"revoked": True, "invitation_id": invitation_id}

    def cleanup_expired_invitations(self) -> dict:
        """Mark expired invitations as 'expired' status.

        Returns count of cleaned invitations.
        """
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        reg = self._get_registry()
        rows = reg.query(
            "MATCH (i:Invitation) "
            "WHERE i.expires_at < $now AND i.accepted_at IS NULL "
            "AND (i.status IS NULL OR i.status <> 'expired') "
            "SET i.status = 'expired' "
            "RETURN count(i)",
            params={"now": now},
        ).result_set
        count = rows[0][0] if rows else 0
        return {"cleaned": count}

    # ── Helpers ───────────────────────────────────────────────────

    def _expand_kind(self, kind: str) -> list[str]:
        """Expand kind via subclassOf + equivalentTo for Cypher IN clause.

        Uses PackRegistry.expand_kind(). Registry is loaded once and cached.
        Returns [kind] if no packs loaded or kind is unknown.
        """
        return _get_kind_expander().expand_kind(kind)

    def _resolve_traversal_path(self, path: str) -> dict | None:
        """Resolve 'Product→Feature' to {predicate, fromKind, toKind} from pack registry.

        Matches against pack-declared relations — fromKind/toKind suffixes
        (e.g., 'product-strategy:product' matches 'Product' via kind name 'product').
        Returns None if no matching relation found.
        """
        segments = [s.strip() for s in path.split("→")]
        if len(segments) < 2:
            # Hint: user may have used ASCII '->' instead of Unicode '→'
            if "->" in path:
                _logger.warning(
                    "traversal_path uses ASCII '->' — use Unicode '→' instead "
                    "(e.g., 'Product→Feature')"
                )
            return None

        registry = _get_kind_expander()
        relations = registry.list_relations()
        if not relations:
            return None

        from_name, to_name = segments[0].strip(), segments[1].strip()

        for rel in relations:
            if "fromKind" not in rel or "toKind" not in rel:
                continue
            fk = rel["fromKind"]
            tk = rel["toKind"]
            # Extract kind name after the namespace prefix
            fk_name = fk.split(":", 1)[-1] if ":" in fk else fk
            tk_name = tk.split(":", 1)[-1] if ":" in tk else tk
            # Match case-insensitively against path segments
            if fk_name.lower() == from_name.lower() and tk_name.lower() == to_name.lower():
                return {"predicate": rel["predicate"], "fromKind": fk, "toKind": tk}

        return None

    @staticmethod
    def _validate_kind(kind: str) -> None:
        # ponytail: open-ended kind vocabularies — any string accepted.
        # Warning for unrecognized values; domain_loader.register_kind() can suppress.
        if kind not in known_kinds():
            _logger.warning(
                "Unrecognized pointKind %r. Known values: %s. "
                "Use tortoise.domain_loader.register_kind(%r) to register it.",
                kind, sorted(known_kinds()), kind,
            )


    # ── Entity CRUD (ONTOLOGY v2.5 §3, all 7 types) ──────────────────

    def _create_entity(self, label: str, id_val: str, props: dict, event_type: str) -> dict:
        """Generic entity creation. Applies to graph via projection (event log + FalkorDB)."""
        # #329: id + sourcePath/source_path are server-managed — reject
        props = _sanitize_props(props, reject_id=True)
        proj = self._get_proj()
        # Build event dict
        event = {"type": event_type, "id": id_val, **props}
        # Normalize field names for projection compatibility
        if label == "Subject" and "subjectKind" in props:
            event["subject_kind"] = props["subjectKind"]
        if label == "Object" and "objectKind" in props:
            event["object_kind"] = props["objectKind"]
        if label == "Document" and "documentKind" in props:
            event["document_kind"] = props["documentKind"]
        if label == "Event" and "eventKind" in props:
            event["eventKind"] = event.get("eventKind", props.get("eventKind"))
            if "eventId" not in event:
                event["eventId"] = id_val
        if label == "Source":
            event["url"] = id_val
        # Apply through projection (writes to JSONL + FalkorDB)
        proj.apply(event)
        # #452: Subject/Object MERGE by name (content-hash dedup).
        # When the name already exists, the fresh id_val never lands on the
        # node (ON CREATE never fires).  Re-fetch the canonical id from the
        # graph so callers get a usable return value — matching create_point
        # dedup behavior which returns the existing point id.
        canonical_id = id_val
        if label in ("Subject", "Object") and "name" in event:
            name = event["name"]
            r = proj.g.query(
                f"MATCH (n:{label} {{name: $name}}) RETURN n.id",
                params={"name": name},
            )
            if r.result_set and r.result_set[0]:
                canonical_id = r.result_set[0][0]
        # Wire edges after entity exists in graph (use canonical id)
        if props.get("authoredBy"):
            proj.create_authored_by(canonical_id, props["authoredBy"])
        if props.get("ownedBy"):
            proj.create_owned_by(canonical_id, props["ownedBy"])
        if props.get("managedBy"):
            proj.create_managed_by(canonical_id, props["managedBy"])
        return self._get_entity(canonical_id)

    def _get_entity(self, id_val: str) -> dict:
        # NOTE (issue #327): Session/APIKey/Team/Tag nodes are intentionally
        # excluded from entity resolution — only Point/Subject/Object/Document/
        # Event/Source resolve (index-backed union). On a cross-label id
        # collision the first _RESOLVE_BRANCHES match wins (Point priority) —
        # more deterministic than the previous scan-order-arbitrary LIMIT 1.
        resolved = self._get_proj()._resolve_entity(
            id_val, by_id=True, by_eventId=True, by_url=True)
        return resolved[0]["properties"] if resolved else {}

    def _update_entity(self, id_val: str, **props) -> dict:
        # #329: id + sourcePath/source_path are server-managed — reject
        props = _sanitize_props(props, reject_id=True)
        proj = self._get_proj()
        # NOTE (issue #327): like _get_entity, entity mutation covers only the
        # canonical labels (Point/Subject/Object/Document/Source/Event).
        # Session/APIKey/Team/Tag nodes are intentionally NOT updated — legacy
        # matched them via id/eventId but no caller relies on it.
        # Per-label indexed writes (id OR eventId — original predicate; no url).
        # UNION cannot carry SET, so run each branch sequentially (#327).
        for label, prop in (("Point", "id"), ("Subject", "id"), ("Object", "id"),
                            ("Document", "id"), ("Source", "id"), ("Event", "eventId")):
            proj.g.query(
                f"MATCH (n:{label} {{{prop}:$id}}) SET n += $p",
                params={"id": id_val, "p": props},
            )
        return self._get_entity(id_val)

    def _delete_entity(self, id_val: str) -> bool:
        proj = self._get_proj()
        # NOTE (issue #327): deletion covers only canonical entity labels —
        # Session/APIKey/Team/Tag nodes are intentionally NOT deleted (legacy
        # matched them by id/eventId; no caller relies on it).
        total = 0
        for label, prop in (("Point", "id"), ("Subject", "id"), ("Object", "id"),
                            ("Document", "id"), ("Source", "id"), ("Event", "eventId")):
            r = proj.g.query(
                f"MATCH (n:{label} {{{prop}:$id}}) DETACH DELETE n RETURN count(n)",
                params={"id": id_val},
            )
            if r.result_set:
                total += r.result_set[0][0]
        return bool(total)

    def create_subject(self, name: str, subjectKind: str = "other", **props) -> dict:
        _coerce_props(props)  # accept MCP-style nested props= dict (#218)
        return self._create_entity("Subject", self.ulid(), {"name": name, "subjectKind": subjectKind, "status": "live", **props}, "SubjectAdded")

    def create_object(self, name: str, objectKind: str = "other", **props) -> dict:
        _coerce_props(props)  # accept MCP-style nested props= dict (#218)
        return self._create_entity("Object", self.ulid(), {"name": name, "objectKind": objectKind, "status": "live", **props}, "ObjectRegistered")

    def create_event(self, name: str, eventKind: str, **props) -> dict:
        _coerce_props(props)  # accept MCP-style nested props= dict (#218)
        """Create an Event node.

        If aboutSubject, aboutObject, aboutPoint, or aboutDocument are provided
        in **props, they are extracted and wired as graph edges:
          (Event)-[:aboutSubject]->(Subject)
          (Event)-[:aboutObject]->(Object)
          (Event)-[:aboutPoint]->(Point)
          (Event)-[:aboutDocument]->(Document)
        rather than stored as string properties.
        """
        eid = self.ulid()
        about_subject = props.pop("aboutSubject", None)
        about_object = props.pop("aboutObject", None)
        about_point = props.pop("aboutPoint", None)
        about_document = props.pop("aboutDocument", None)
        result = self._create_entity("Event", eid, {"eventId": eid, "name": name, "eventKind": eventKind, "eventStatus": "scheduled", **props}, "EventRecorded")
        proj = self._get_proj()
        if about_subject:
            proj.create_about_edge(eid, about_subject, "aboutSubject")
            # Only name-resolve if it looks like a plain name, not an ID
            if isinstance(about_subject, str) and not _is_ulid(about_subject):
                proj._create_about_edges(eid, about_subject)
        if about_object:
            proj.create_about_edge(eid, about_object, "aboutObject")
            if isinstance(about_object, str) and not _is_ulid(about_object):
                proj._create_about_edges(eid, about_object)
        if about_point:
            proj.create_about_edge(eid, about_point, "aboutPoint")
            if isinstance(about_point, str) and not _is_ulid(about_point):
                proj._create_about_edges(eid, about_point)
        if about_document:
            proj.create_about_edge(eid, about_document, "aboutDocument")
            if isinstance(about_document, str) and not _is_ulid(about_document):
                proj._create_about_edges(eid, about_document)
        return result


    # ── Session Indexing (AgentSession) ─────────────────────────

    def _session_embedding(self, name: str, summary: str = "",
                           keywords: list[str] | None = None,
                           topics: list[str] | None = None) -> list[float] | None:
        """Compute the embedding for an AgentSession Event (#244).

        name + summary + keywords + topics → 384-dim vector, or None when the
        model is unavailable (session indexing must never depend on it).
        """
        from .session_indexer import compute_session_embedding
        return compute_session_embedding(name, summary, keywords, topics)

    def search_sessions(self, query: str, *, agent: str | None = None,
                        topics: list[str] | None = None,
                        after: "datetime | str | None" = None,
                        before: "datetime | str | None" = None,
                        limit: int = 10, offset: int = 0) -> list[dict]:
        """Search indexed agent sessions. Returns Events with metadata snippets.

        Routes through the hybrid engine (tortoise_fts_query entity_type='event'
        kind='AgentSession'): RRF fusion of FTS (Event subject+name index),
        vector (session embeddings computed at index time, #244) and structural
        (eventKind) strategies. Results are ordered by relevance with startedAt
        DESC as tiebreak.

        agent/topics post-filter the candidates. after/before bound the search
        to sessions whose ``startedAt`` falls in ``[after, before]`` (inclusive).
        Each may be a ``datetime`` or an ISO-8601 string (``Z`` or offset
        accepted); both are normalized to UTC ISO-8601 so the comparison
        against stored ``startedAt`` values is valid regardless of the caller's
        timezone. Sessions that lack a ``startedAt`` are EXCLUDED whenever a
        bound is set.

        When no semantic strategy contributed (FTS/vector unavailable — e.g.
        embedded FalkorDBLite without embeddings, prod before #160 deploys),
        falls back to the legacy keyword CONTAINS surface (name + keywords) so
        the previous behavior keeps working.
        """
        if not query or not query.strip():
            return []
        proj = self._get_proj()
        has_bound = after is not None or before is not None
        after_utc = _to_iso_utc(after) if after is not None else None
        before_utc = _to_iso_utc(before) if before is not None else None

        # ── Hybrid route: RRF fusion of FTS + vector + structural ──
        # Generous candidate pool — agent/topics/temporal filters drop rows
        # post-retrieval, so fetch beyond the caller's limit + offset.
        candidate_limit = max(limit * 5, 50) + offset
        hybrid = self.tortoise_fts_query(
            query, kind="AgentSession", entity_type="event",
            limit=candidate_limit,
        )
        has_semantic = any(
            r.get("scores")
            and (r["scores"].get("fts") is not None
                 or r["scores"].get("vector") is not None)
            for r in hybrid
        )
        if has_semantic and hybrid:
            # Precision gate (#244 review): rows with NO semantic signal — no
            # FTS score and no vector score (structural-only; the kind filter
            # matches ALL AgentSession events with score 1.0) are NOT results,
            # they only fed the RRF candidate pool. Note: vector-only rows are
            # deliberately kept — word-distinct semantic recall is the point
            # of #244 ("port migration" finds a session about changing the
            # FalkorDB default port); see docstring. The brute-force vector
            # strategy is threshold-less, so those rows are ranked nearest-
            # neighbors-first and precision drops when a query has no real
            # semantic match (documented behavior).
            ids = [r["id"] for r in hybrid]
            rows = proj.g.query(
                "MATCH (e:Event) WHERE e.eventId IN $ids RETURN properties(e)",
                params={"ids": ids},
            ).result_set
            props_by_id = {}
            for row in rows:
                props = dict(row[0])
                if props.get("eventId"):
                    props_by_id[props["eventId"]] = props
            ranked = []
            for r in hybrid:
                props = props_by_id.get(r["id"])
                if props is None:
                    continue
                if agent and props.get("agent") != agent:
                    continue
                if topics:
                    s_topics = set(props.get("topics") or [])
                    if not any(t in s_topics for t in topics):
                        continue
                if has_bound:
                    started = props.get("startedAt")
                    if not started:
                        continue  # sessions without startedAt excluded when a bound is set
                    if after_utc is not None and started < after_utc:
                        continue
                    if before_utc is not None and started > before_utc:
                        continue
                # Precision gate (#244 review): structural-only rows (no FTS,
                # no vector score) are NOT results — a session that neither
                # keyword-matches nor semantically matches must not appear.
                scores = r.get("scores") or {}
                if scores.get("fts") is None and scores.get("vector") is None:
                    continue
                ranked.append((scores.get("rrf") or 0.0, props))
            # Relevance (RRF) desc, startedAt DESC as tiebreak (missing = last)
            ranked.sort(
                key=lambda item: (item[0], item[1].get("startedAt") or ""),
                reverse=True,
            )
            return [props for _, props in ranked[offset:offset + limit]]

        # ── Legacy keyword fallback (no FTS/vector contribution) ──
        # Preserves the pre-#244 CONTAINS surface (name + keywords) plus the
        # #243 temporal filters (after/before, ISO-8601 UTC normalization,
        # startedAt IS NOT NULL exclusion when a bound is set).
        clauses = ["e.eventKind = 'AgentSession'"]
        params: dict = {"limit": max(limit * 3, 30)}
        query_lower = query.strip().lower()
        clauses.append("(toLower(e.name) CONTAINS $q OR any(kw IN e.keywords WHERE toLower(kw) CONTAINS $q))")
        params["q"] = query_lower
        if agent:
            clauses.append("e.agent = $agent")
            params["agent"] = agent
        if topics:
            topic_clauses = []
            for i, t in enumerate(topics):
                pk = f"topic{i}"
                topic_clauses.append(f"${pk} IN e.topics")
                params[pk] = t
            clauses.append(f"({' OR '.join(topic_clauses)})")
        has_bound = after is not None or before is not None
        if has_bound:
            # Explicitly drop sessions without startedAt — same outcome as
            # null-comparison semantics, but self-documenting and robust.
            clauses.append("e.startedAt IS NOT NULL")
        if after is not None:
            clauses.append("e.startedAt >= $after")
            params["after"] = _to_iso_utc(after)
        if before is not None:
            clauses.append("e.startedAt <= $before")
            params["before"] = _to_iso_utc(before)
        where = " AND ".join(clauses)
        params["offset"] = offset
        rows = proj.g.query(
            f"MATCH (e:Event) WHERE {where} "
            "RETURN properties(e) ORDER BY e.startedAt DESC SKIP $offset LIMIT $limit",
            params=params,
        ).result_set
        # Fetch a bit extra for scoring headroom, but honor the caller's limit.
        return [dict(r[0]) for r in rows[:limit]]

    def get_events(self, eventKind: str | None = None, limit: int = 20) -> list[dict]:
        """Get recent Events, optionally filtered by eventKind."""
        proj = self._get_proj()
        if eventKind:
            return [r[0] for r in proj.g.query(
                "MATCH (e:Event {eventKind: $ek}) RETURN properties(e) ORDER BY e.startedAt DESC LIMIT $lim",
                params={"ek": eventKind, "lim": limit}
            ).result_set]
        return [r[0] for r in proj.g.query(
            "MATCH (e:Event) RETURN properties(e) ORDER BY e.startedAt DESC LIMIT $lim",
            params={"lim": limit}
        ).result_set]

    def get_session(self, session_id: str) -> dict | None:
        """Get a single session Event by session_id (matches snake or camel case)."""
        if not session_id:
            return None
        proj = self._get_proj()
        rows = proj.g.query(
            "MATCH (e:Event {eventKind: 'AgentSession'}) "
            "WHERE e.session_id = $sid OR e.sessionId = $sid RETURN properties(e)",
            params={"sid": session_id}
        ).result_set
        return rows[0][0] if rows else None

    def index_sessions(self, directory: str, extract_metadata: bool = True,
                       llm_model: str | None = "gpt-5-mini",
                       progress_file: str | None = None) -> dict:
        """Index session files as AgentSession Events.
        Thin wrapper around ingest_corpus with AgentSession defaults."""
        return self.ingest_corpus(directory, eventKind="AgentSession",
                                  extract_metadata=extract_metadata,
                                  llm_model=llm_model,
                                  progress_file=progress_file)

    def session_index_health(self, directory: str | None = None) -> dict:
        """Compare session .md files against indexed AgentSession Events.

        #280 item 2 — the ``tortoise doctor`` health surface. Scans the
        canonical corpus (``~/.tortoise/docs/conversations/`` by default,
        ``TORTOISE_SESSION_CORPUS`` override) and matches each file to its
        expected Event by session_id + file_hash.

        Returns ``{directory, file_count, indexed_events, matched, unindexed,
        stale, up_to_date, duplicates}`` — ``stale`` = Event exists but hash
        differs (re-index needed); ``unindexed`` = no Event at all.

        ``indexed_events`` is CORPUS-SCOPED (#280 review P3): the count of
        AgentSession Events whose eventId matches a corpus file — not all
        AgentSession Events in the graph (other sessions would make the
        doctor arithmetic misleading). ``duplicates`` surfaces sessionIds
        claimed by more than one corpus file (rglob copies / duplicated
        frontmatter): those copies made the sweep permanently non-convergent
        (MERGE is last-writer-wins) — they are surfaced here and skipped in
        ingest_corpus, never silently re-indexed (#280 review P2).
        """
        from pathlib import Path
        from .session_indexer import (
            compute_file_hash, extract_session_id, session_corpus_dir,
        )

        dir_path = Path(directory or session_corpus_dir())
        if not dir_path.is_dir():
            return {"directory": str(dir_path), "file_count": 0,
                    "indexed_events": 0, "matched": 0,
                    "unindexed": [], "stale": [], "up_to_date": [],
                    "duplicates": []}

        proj = self._get_proj()
        rows = proj.g.query(
            "MATCH (e:Event {eventKind:'AgentSession'}) "
            "RETURN e.eventId, e.file_hash"
        ).result_set
        by_event = {r[0]: r[1] for r in rows}

        files = sorted(dir_path.rglob("*.md"))
        # Group by session id: two files may share a sessionId (rglob picking
        # up copies, or duplicated frontmatter). Classify only the PRIMARY
        # file (first in sorted order) so the delta drives the sweep to
        # convergence; non-primary copies surface in the `duplicates` bucket.
        by_sid: dict[str, list[str]] = {}
        for f in files:
            # str() coercion keeps the value hashable as a dict key (a YAML
            # list/dict frontmatter sessionId would raise TypeError) while
            # preserving the base event_id derivation (str() == repr() for
            # str/int/float/bool/list/dict), so health stays consistent with
            # ingest_corpus's frontmatter coercion. Only a read failure
            # (extract returns None) falls back to the file-stem id.
            _sid_raw = extract_session_id(str(f))
            sid = str(_sid_raw) if _sid_raw is not None else f"file_{f.stem}"
            by_sid.setdefault(sid, []).append(str(f))
        unindexed: list[str] = []
        stale: list[str] = []
        up_to_date: list[str] = []
        duplicates: list[dict] = []
        for sid, flist in by_sid.items():
            event_id = f"session_{sid}"
            if len(flist) > 1:
                duplicates.append({"session_id": sid, "event_id": event_id,
                                   "files": flist})
            primary = flist[0]
            file_hash = compute_file_hash(primary)
            existing = by_event.get(event_id)
            if existing is None:
                unindexed.append(primary)
            elif existing == file_hash:
                up_to_date.append(primary)
            else:
                stale.append(primary)
        corpus_event_ids = {f"session_{sid}" for sid in by_sid}
        return {"directory": str(dir_path),
                "file_count": len(files),
                "indexed_events": sum(1 for eid in corpus_event_ids
                                       if eid in by_event),
                "matched": len(up_to_date),
                "unindexed": unindexed,
                "stale": stale,
                "up_to_date": up_to_date,
                "duplicates": duplicates}

    def reconcile_sessions(self, directory: str | None = None,
                           extract_metadata: bool = False,
                           llm_model: str | None = "gpt-5-mini") -> dict:
        """Reconciliation sweep (#280 item 3) — scan for unindexed session
        files and re-index them.

        Scan-then-replay: ``session_index_health()`` computes the delta
        (unindexed + hash-stale files), then ``ingest_corpus()`` replays the
        directory — its dedup skips everything up-to-date and the per-session
        flock (#280 item 1) serializes against concurrent hook writers. No
        cron infra needed: the sweep triggers from the same hook/CLI surface
        (align decision) — run it manually via ``tortoise index sessions`` or
        from session-end.sh.

        ``extract_metadata`` defaults to False so sweeps use the cheap
        keyword fallback — never burn LLM tokens on bulk retry.
        """
        from pathlib import Path
        from .session_indexer import session_corpus_dir

        directory = str(Path(directory or session_corpus_dir()).resolve())
        health = self.session_index_health(directory)
        result: dict = {}
        if health["unindexed"] or health["stale"]:
            result = self.ingest_corpus(
                directory, eventKind="AgentSession",
                extract_metadata=extract_metadata, llm_model=llm_model,
            )
        return {**health, "reindex": result}


    def _connect_issue_objects(self, event_id: str, metadata: dict) -> int:
        """Create INSTANTIATES edges from an AgentSession Event to issue/PR Objects."""
        proj = self._get_proj()
        connected = 0
        for key in ("issues", "prs"):
            for item in metadata.get(key, []) or []:
                if isinstance(item, dict):
                    oid = item.get("id") or item.get("number")
                    name = item.get("title") or item.get("name") or str(item)
                else:
                    oid = None
                    name = str(item)
                if not oid:
                    # Deterministic hash (builtin hash() is salted per-process →
                    # would create duplicate Objects on every run).
                    oid = f"{key.rstrip('s')}_{hashlib.sha256(name.encode()).hexdigest()[:8]}"
                okind = "pr" if key == "prs" else "issue"
                proj.g.query(
                    "MERGE (o:Object {id:$oid}) SET o.name=$name, o.objectKind=$okind "
                    "WITH o MATCH (e:Event {eventId:$eid}) MERGE (e)-[:INSTANTIATES]->(o)",
                    params={"oid": oid, "name": name[:200], "eid": event_id, "okind": okind},
                )
                connected += 1
        return connected


    def create_document(self, title: str, documentKind: str, **props) -> dict:
        _coerce_props(props)  # accept MCP-style nested props= dict (#218)
        did = self.ulid()
        return self._create_entity("Document", did, {"title": title, "documentKind": documentKind, "objectKind": "document", "status": "draft", **props}, "DocumentCreated")

    def create_source(self, url: str, sourceKind: str, *,
                      tier: str | None = None, sourceDate: str | None = None,
                      **props) -> dict:
        """Create (or merge) a Source node (issue #398 Task 6).

        Dual-write rule (ontology v3.1 §4.6 + code reader contract):
          - ``tier`` given → stored on ``credibilityTier`` (the property the
            inheritance adapter reads); ``sourceKind`` left untouched.
          - ``sourceKind`` is itself a tier-form (T0-T4) → mirrored to
            ``credibilityTier`` as well (canonical per ontology).
          - An EXISTING Source (URL collision) NEVER has its ``sourceKind``
            overwritten — tier lands on ``credibilityTier`` only.
        ``sourceDate`` is the evidence-age clock for decay (falls back to
        ``ingestedAt`` — the documented pipeline-arrival proxy). Invalid tier
        values raise ValueError.
        """
        _coerce_props(props)  # accept MCP-style nested props= dict (#218)
        if not url or not url.strip():
            raise ValueError("url must be a non-empty string")
        from tortoise.source_credibility import TIER_PRIORS, canonical_tier
        if tier is not None:
            _orig_tier = tier
            tier = canonical_tier(tier)
            if tier is None:
                raise ValueError(
                    f"Invalid tier {_orig_tier!r} — must be T0..T4 or a legacy alias "
                    f"(gold/high/medium/low/unverified)"
                )
        if sourceKind in TIER_PRIORS and tier is None:
            tier = sourceKind  # tier-form sourceKind mirrors to credibilityTier
        ev = {
            "url": url,
            "sourceKind": sourceKind,
            "ingestedAt": __import__('datetime').datetime.now(
                __import__('datetime').timezone.utc).isoformat(),
            **props,
        }
        if tier is not None:
            ev["credibilityTier"] = tier
        if sourceDate is not None:
            ev["sourceDate"] = sourceDate
        result = self._create_entity("Source", url, ev, "SourceCreated")
        # Write events invalidate the inheritance gate + reliability cache
        self._invalidate_inheritance_gate_for_source(url)
        self._clear_reliability_cache(url)
        return result

    def set_source_tier(self, url: str, tier: str) -> dict:
        """Set (or change) a Source's credibility tier — non-destructive.

        Writes ``credibilityTier`` only; never touches ``sourceKind`` (legacy
        type strings are preserved). Mirrors to ``sourceKind`` when it is
        already a tier-form (keeps the dual-write invariant). Dirty-marks the
        inheritance gate + clears the reliability cache.
        """
        from tortoise.source_credibility import TIER_PRIORS, canonical_tier
        _orig = tier
        tier = canonical_tier(tier)
        if tier is None:
            raise ValueError(
                f"Invalid tier {_orig!r} — must be T0..T4 or a legacy alias "
                f"(gold/high/medium/low/unverified)"
            )
        proj = self._get_proj()
        rows = proj.g.query(
            "MATCH (s:Source {url:$url}) RETURN s.sourceKind",
            params={"url": url},
        ).result_set
        if not rows:
            raise ValueError(f"Source {url} does not exist")
        skind = rows[0][0]
        if skind in TIER_PRIORS:
            # Keep the dual-write invariant: tier-form sourceKind mirrors the tier
            proj.g.query(
                "MATCH (s:Source {url:$url}) SET s.credibilityTier = $t, s.sourceKind = $t",
                params={"url": url, "t": tier},
            )
        else:
            proj.g.query(
                "MATCH (s:Source {url:$url}) SET s.credibilityTier = $t",
                params={"url": url, "t": tier},
            )
        self._invalidate_inheritance_gate_for_source(url)
        self._clear_reliability_cache(url)
        return {"url": url, "credibilityTier": tier, "sourceKind": skind}

    # ── Entity Derivation (#122 Part 2) ──────────────────────────

    def create_derivation(self, src_id: str, dst_id: str) -> dict:
        """Create a wasDerivedFrom edge: (dst)-[:wasDerivedFrom]->(src).

        PROV-O entity derivation — dst was derived from src. Distinct from
        extractedFrom (claim provenance) — wasDerivedFrom is Object→Object
        entity derivation.
        """
        proj = self._get_proj()
        ok = proj.create_edge(dst_id, src_id, "wasDerivedFrom")
        return {"derived": ok, "src": src_id, "dst": dst_id}

    # ── Source reliability (issue #398 Task 4) ──────────────────────

    def _compute_source_prior(self, url: str) -> dict | None:
        """Compute a Source's effective Beta prior + components (single source of truth).

        Used by BOTH the inheritance adapter (per-point base weight) and the
        reliability cache — the two cannot drift. Returns None when the source
        is untiered (no inheritance contribution). Assessment factor is read
        from `pointKind='assessment'` Points (latest per assessor wins by
        createdAt; outdated filtered); until Task 5 lands, factor = 1.0.
        """
        import os
        from datetime import datetime, timezone
        from tortoise.source_credibility import (
            aggregate_prior,
            assessment_factor,
            resolve_tier,
        )
        proj = self._get_proj()
        rows = proj.g.query(
            "MATCH (s:Source {url:$url}) "
            "RETURN s.credibilityTier, s.sourceKind, s.sourceDate, s.ingestedAt",
            params={"url": url},
        ).result_set
        if not rows:
            return None
        ctier, skind, sdate, ingested = rows[0]
        tier = resolve_tier(ctier, skind)
        if tier is None:
            return None

        # Batched assessment aggregation (latest per (targetSource, assessor),
        # outdated filtered; assessorReputation snapshotted at write time).
        arows = proj.g.query(
            "MATCH (p:Point {pointKind:'assessment'}) "
            "WHERE p.targetSource = $url AND (p.outdated IS NULL OR p.outdated = false) "
            "RETURN p.assessor, p.score, coalesce(p.assessorReputation, 0.5), p.createdAt "
            "ORDER BY p.createdAt",
            params={"url": url},
        ).result_set
        latest: dict[str, tuple[float, float]] = {}
        for assessor, score, rep, _created in arows:
            try:
                latest[assessor] = (float(rep), float(score))
            except (TypeError, ValueError):
                continue
        assessments = list(latest.values())
        factor = assessment_factor(assessments)

        from tortoise.source_credibility import decay_factor as _decay_factor
        alpha, beta = aggregate_prior(
            [(tier, sdate, ingested, factor)],
            recency_decay=float(os.environ.get("TORTOISE_EP_RECENCY_DECAY", "0.95")),
        )
        return {
            "tier": tier,
            "sourceDate": sdate,
            "ingestedAt": ingested,
            "decay": _decay_factor(
                sdate, ingested,
                recency_decay=float(os.environ.get("TORTOISE_EP_RECENCY_DECAY", "0.95")),
                tier=tier,
            ),
            "factor": factor,
            "assessment_count": len(assessments),
            "alpha": alpha,
            "beta": beta,
        }

    def get_source_reliability(self, url: str) -> dict:
        """Derive a Source's reliability (0-1) — query-time, cache-consistency-checked.

        Returns {"url", "reliability" (float 0-1 or None), "components",
        "cache": "fresh"|"recomputed"|"miss"}. The reliability value is the mean
        of the SAME modulated prior EP uses as base weight (single source of
        truth ``_compute_source_prior``), so ``reliability == inherited prior
        mean`` for single-source points (consistency invariant). Untiered +
        unassessed → None (reason 'untiered'). The cache
        (reliability/reliabilityComponents/reliability_derived_at) is a
        documented projection — recomputed when stale (interval elapsed or tier/
        sourceDate changed vs cached components) or after write events
        (set_source_tier/assess_source/create_source(tier=)).
        """
        import os
        from datetime import datetime, timezone
        from tortoise.source_credibility import resolve_tier
        proj = self._get_proj()
        now = datetime.now(timezone.utc)
        interval = float(os.environ.get("TORTOISE_EP_REINHERIT_INTERVAL", "3600"))

        # Cache freshness check
        rows = proj.g.query(
            "MATCH (s:Source {url:$url}) "
            "RETURN s.reliability, s.reliabilityComponents, s.reliability_derived_at, "
            "s.credibilityTier, s.sourceKind, s.sourceDate, s.ingestedAt",
            params={"url": url},
        ).result_set
        cached = None
        if rows and rows[0][2]:
            cached_rel, cached_comp_raw, cached_at, c_tier, c_kind, c_sdate, c_ingested = rows[0]
            fresh = False
            try:
                derived = datetime.fromisoformat(str(cached_at).replace("Z", "+00:00"))
                if derived.tzinfo is None:
                    derived = derived.replace(tzinfo=timezone.utc)
                fresh = (now - derived).total_seconds() < interval
            except (ValueError, TypeError):
                fresh = False
            if fresh:
                try:
                    import json as _json
                    cached_comp = _json.loads(cached_comp_raw) if cached_comp_raw else {}
                    # Inputs unchanged → serve cache (tier, sourceDate, ingestedAt
                    # are the derivation inputs; assessments clear the cache at write)
                    if (cached_comp.get("tier") == resolve_tier(c_tier, c_kind)
                            and cached_comp.get("sourceDate") == c_sdate
                            and cached_comp.get("ingestedAt") == rows[0][5]):
                        cached = (cached_rel, cached_comp)
                except (TypeError, ValueError, KeyError):
                    cached = None

        if cached is not None:
            rel, comp = cached
            return {"url": url, "reliability": rel, "components": comp, "cache": "fresh"}

        # Recompute from the single source of truth
        prior = self._compute_source_prior(url)
        if prior is None:
            # Untiered: assessment-only reliability (display — never feeds EP)
            arows = proj.g.query(
                "MATCH (p:Point {pointKind:'assessment'}) "
                "WHERE p.targetSource = $url AND (p.outdated IS NULL OR p.outdated = false) "
                "RETURN p.assessor, p.score, coalesce(p.assessorReputation, 0.5), p.createdAt "
                "ORDER BY p.createdAt",
                params={"url": url},
            ).result_set
            if arows:
                # Latest per (url, assessor) — mirrors _compute_source_prior dedup
                latest: dict[str, tuple[float, float]] = {}
                for assessor, score, rep, _created in arows:
                    try:
                        latest[assessor] = (float(rep), float(score))
                    except (TypeError, ValueError):
                        continue
                reps = [r for r, _s in latest.values()]
                rep_sum = sum(reps)
                weighted = (sum(r * s for r, s in latest.values()) / rep_sum
                            if rep_sum else 0.0)
                if weighted != weighted:  # NaN guard
                    weighted = 0.0
                comp = {"tier": None, "reason": "untiered; assessment-only",
                        "assessment_count": len(arows),
                        "assessment_weighted_mean": weighted}
                self._write_reliability_cache(url, weighted, comp, now)
                return {"url": url, "reliability": weighted, "components": comp,
                        "cache": "recomputed"}
            comp = {"tier": None, "reason": "untiered", "assessment_count": 0}
            self._write_reliability_cache(url, None, comp, now)
            return {"url": url, "reliability": None, "components": comp, "cache": "miss"}

        mean = prior["alpha"] / (prior["alpha"] + prior["beta"])
        comp = {
            "tier": prior["tier"],
            "sourceDate": prior["sourceDate"],
            "ingestedAt": prior["ingestedAt"],
            "factor": prior["factor"],
            "assessment_count": prior["assessment_count"],
            "decay": prior["decay"],
            "mean": mean,
        }
        self._write_reliability_cache(url, mean, comp, now)
        return {"url": url, "reliability": mean, "components": comp, "cache": "recomputed"}

    def assess_source(self, url: str, assessor: str, score: float, rationale: str) -> dict:
        """Record an agent's assessment of a Source (issue #398 Task 5).

        Creates a ``pointKind='assessment'`` Statement Point (ontology §2 —
        evaluations of subjects are Points with EP confidence, NOT edges),
        property-linked via ``targetSource`` (never ``extractedFrom`` — it is
        evidence ABOUT the source, not extracted FROM it).

        Semantics:
          - score ∈ [0, 1] (non-numeric → ValueError); rationale required.
          - Assessor reputation is SNAPSHOTTED at write time
            (``compute_reputation(assessor).mean``, stored as
            ``assessorReputation``) so later reputation changes never rewrite
            past assessments' factors.
          - Latest-wins per (url, assessor): older assessments from the same
            assessor are marked ``outdated:true`` — the aggregation query picks
            the latest active assessment by construction (crash-safe).
          - The assessment factor is clamped [0.1, 2.0] at the read path.
          - Refreshes the reliability cache + dirty-marks the inheritance gate
            so EP recomputes promptly.
        """
        try:
            score_f = float(score)
        except (TypeError, ValueError):
            raise ValueError(f"score must be numeric, got {score!r}") from None
        if not (0.0 <= score_f <= 1.0):
            raise ValueError(f"score must be in [0, 1], got {score_f}")
        if not rationale or not str(rationale).strip():
            raise ValueError("rationale is required")
        if not assessor or not str(assessor).strip():
            raise ValueError("assessor is required")
        if not url or not str(url).strip():
            raise ValueError("url is required")

        from datetime import datetime, timezone
        rep = self.compute_reputation(str(assessor))["mean"]
        now = datetime.now(timezone.utc).isoformat()

        proj = self._get_proj()
        # Create the new assessment FIRST, then mark older ones outdated EXCLUDING
        # the new point (crash-safe: a failure between the two leaves the new
        # point active and the old one active — the read path dedupes
        # latest-per-(url, assessor) by createdAt, so no double-count; a failure
        # before the mark leaves the previous assessment intact, not orphaned).
        p = self.create_point(
            "assessment", str(rationale).strip(),
            props={
                "targetSource": url,
                "assessor": str(assessor),
                "score": score_f,
                "assessorReputation": rep,
                "createdAt": now,
            },
        )
        proj.g.query(
            "MATCH (p:Point {pointKind:'assessment'}) "
            "WHERE p.targetSource = $url AND p.assessor = $assessor "
            "  AND p.id <> $new_id "
            "  AND (p.outdated IS NULL OR p.outdated = false) "
            "SET p.outdated = true",
            params={"url": url, "assessor": str(assessor), "new_id": p["id"]},
        )
        # Refresh reliability cache (clear → next read recomputes) + dirty-mark
        # the inheritance gate so EP recomputes promptly.
        self._invalidate_inheritance_gate_for_source(url)
        self._clear_reliability_cache(url)
        return {"assessment_point_id": p["id"], "url": url, "assessor": str(assessor),
                "score": score_f, "reputation": rep}

    def _invalidate_inheritance_gate_for_source(self, url: str) -> None:
        """Dirty-mark all points extracted from a source (inheritance recompute)."""
        proj = self._get_proj()
        rows = proj.g.query(
            "MATCH (n:Point)-[:extractedFrom]->(s:Source {url:$url}) "
            "RETURN n.id",
            params={"url": url},
        ).result_set
        pids = [r[0] for r in rows]
        self._invalidate_inheritance_gate(pids)

    def _write_reliability_cache(self, url: str, reliability, components: dict, now) -> None:
        """Write-through reliability projection on the Source node (documented cache)."""
        import json as _json
        proj = self._get_proj()
        proj.g.query(
            "MATCH (s:Source {url:$url}) "
            "SET s.reliability = $r, s.reliabilityComponents = $c, "
            "s.reliability_derived_at = $ts",
            params={"url": url, "r": reliability, "c": _json.dumps(components),
                    "ts": now.isoformat()},
        )

    def _clear_reliability_cache(self, url: str) -> None:
        """Invalidate the reliability cache (next read recomputes from scratch).

        Called by write events that change the derivation inputs: assess_source,
        set_source_tier, create_source(tier=). Prevents indefinite staleness —
        the cache is a documented projection, never authoritative.
        """
        proj = self._get_proj()
        proj.g.query(
            "MATCH (s:Source {url:$url}) "
            "REMOVE s.reliability, s.reliabilityComponents, s.reliability_derived_at",
            params={"url": url},
        )

    # ── Reputation (#122 Part 4) ─────────────────────────────────

    def compute_reputation(self, subject_id: str) -> dict:
        """Derive reputation score for a Subject from event outcomes.

        Traverses: Subject -[:performs]-> Event -[:IMPL|NAND]-> Point
        Aggregates success/failure from direct event outcomes.
        Returns derived score (NOT stored).

        Returns {mean, total_events, impl_count, nand_count, alpha, beta, outcomes}.
        """
        proj = self._get_proj()
        # Try exact id match first, fall back to name if no id match (#152).
        # Prevents merging outcomes from Subject A (id='alice') with Subject B
        # (name='alice') when a subject_id collides with another Subject's name.
        id_check = proj.g.query(
            "MATCH (s:Subject {id: $sid}) RETURN count(s) > 0",
            params={"sid": subject_id},
        ).result_set
        if id_check and id_check[0][0]:
            match_clause = "s.id = $sid"
        else:
            match_clause = "s.name = $sid"

        # Direct: Event connects directly to claim Points via IMPL/NAND
        # (Operators connect ONLY epistemic targets per ONTOLOGY: Event→Point, Point→Point)
        impl_rows = proj.g.query(
            "MATCH (s:Subject)-[:performs]->(e:Event) "
            "MATCH (e)-[:IMPL]->(p:Point) "
            "WHERE (p.is_operator IS NULL OR p.is_operator = false) "
            "AND (p.outdated IS NULL OR p.outdated = false) "
            "AND e.eventKind <> 'humanApproval' "  # #531: no reputation from own approvals
            f"AND {match_clause} "
            "RETURN p.id, p.content, coalesce(p.confidence, 0.5) AS conf",
            params={"sid": subject_id},
        ).result_set
        nand_rows = proj.g.query(
            "MATCH (s:Subject)-[:performs]->(e:Event) "
            "MATCH (e)-[:NAND]->(p:Point) "
            "WHERE (p.is_operator IS NULL OR p.is_operator = false) "
            "AND (p.outdated IS NULL OR p.outdated = false) "
            "AND e.eventKind <> 'humanApproval' "  # #531: no reputation from own approvals
            f"AND {match_clause} "
            "RETURN p.id, p.content, coalesce(p.confidence, 0.5) AS conf",
            params={"sid": subject_id},
        ).result_set

        # Collect outcomes
        outcomes: list[dict] = []
        for row in impl_rows:
            outcomes.append({"point_id": row[0], "content": row[1], "confidence": float(row[2]), "outcome": "IMPL"})
        for row in nand_rows:
            outcomes.append({"point_id": row[0], "content": row[1], "confidence": float(row[2]), "outcome": "NAND"})

        total = len(outcomes)
        impl_count = sum(1 for o in outcomes if o["outcome"] == "IMPL")
        nand_count = sum(1 for o in outcomes if o["outcome"] == "NAND")

        if total == 0:
            return {"mean": 0.5, "total_events": 0, "impl_count": 0, "nand_count": 0,
                    "alpha": 1.0, "beta": 1.0, "outcomes": []}

        # Simple Beta reputation: IMPL = success, NAND = failure
        # Prior: Beta(1, 1) uniform
        alpha = 1.0 + impl_count
        beta = 1.0 + nand_count
        mean = alpha / (alpha + beta)

        return {
            "mean": round(mean, 4),
            "total_events": total,
            "impl_count": impl_count,
            "nand_count": nand_count,
            "alpha": alpha,
            "beta": beta,
            "outcomes": outcomes[:20],  # cap for readability
        }

    def get_entity(self, id_val: str) -> dict:
        return self._get_entity(id_val)

    def update_entity(self, id_val: str, **props) -> dict:
        _coerce_props(props)  # accept MCP-style nested props= dict (#218)
        return self._update_entity(id_val, **props)

    def delete_entity(self, id_val: str) -> bool:
        return self._delete_entity(id_val)

    # ── Query Helpers ─────────────────────────────────────────────

    def get_owned_entities(self, subject_id: str) -> list:
        """Return all entities owned by a Subject (governance query)."""
        proj = self._get_proj()
        # Issue #327: start from the labeled, indexed Subject (both id and name
        # are RANGE-indexed -> OR uses the index) and traverse ownedBy inward.
        # Narrowing: ownedBy/memberOf targets are canonically Subject (#216);
        # non-Subject targets are out of contract.
        r = proj.g.query(
            "MATCH (s:Subject) WHERE s.id = $sid OR s.name = $sid "
            "MATCH (s)<-[:ownedBy]-(e) RETURN properties(e) LIMIT 100",
            params={"sid": subject_id},
        )
        return [dict(row[0]) for row in r.result_set]

    def get_provenance_chain(self, point_id: str) -> list:
        """Return full provenance chain for a Point."""
        proj = self._get_proj()
        r = proj.g.query(
            "MATCH (p:Point {id:$pid})-[:extractedFrom]->(src:Source)-[:references]->(entity) "
            "RETURN properties(src) as source, properties(entity) as entity, labels(entity) as labels LIMIT 1",
            params={"pid": point_id},
        )
        return [{"source": dict(row[0]), "entity": dict(row[1]), "labels": list(row[2])} for row in r.result_set]

    def link_source_to_entity(self, source_url: str, entity_id: str, entity_label: str, source_kind: str = "document") -> None:
        """Create Source → Entity references edge (Ontology v3.1 §3.4).

        Auto-creates the Source node if it doesn't exist (MERGE + ON CREATE SET)
        so the edge works even when no Point extracted the source yet (#205).

        Args:
            source_url: the Source node's url (auto-created if missing)
            entity_id: the Document/Event/Object node id the source references
            entity_label: the entity label (Document|Event|Object) for the MATCH
            source_kind: sourceKind to set on auto-created Source (default: "document")

        Raises:
            ValueError: if entity_label is not one of Document, Event, Object
                (Action was dissolved in Ontology v3.0).
        """
        proj = self._get_proj()
        proj.link_source_to_entity(source_url, entity_id, entity_label, source_kind)

    def get_org_structure(self, subject_id: str) -> dict:
        """Return organisational structure: members, roles, sub-teams."""
        proj = self._get_proj()
        # Issue #327: labeled Subject start (id|name OR both indexed -> Index
        # Scan) then traverse outward; roles filters the source Subject p.
        members = proj.g.query(
            "MATCH (s:Subject) WHERE s.id = $sid OR s.name = $sid "
            "MATCH (p:Subject)-[:memberOf]->(s) RETURN properties(p)",
            params={"sid": subject_id},
        )
        roles = proj.g.query(
            "MATCH (p:Subject) WHERE p.id = $sid OR p.name = $sid "
            "MATCH (p)-[:holdsRole]->(r:Subject) RETURN properties(r)",
            params={"sid": subject_id},
        )
        return {
            "members": [dict(row[0]) for row in members.result_set],
            "roles": [dict(row[0]) for row in roles.result_set],
        }

    def ulid(self) -> str:
        from .ids import ulid as _ulid
        return _ulid()

    # ── Source Node Completion ────────────────────────────────────

    def complete_source(self, url: str, content: str = None, external_id: str = None) -> dict:
        """Populate Source node fields: contentHash, version, externalId."""
        import hashlib
        proj = self._get_proj()
        updates = {}
        if content is not None:
            updates["contentHash"] = hashlib.sha256(content.encode()).hexdigest()
        if external_id is not None:
            updates["externalId"] = external_id
        # Increment version
        r = proj.g.query(
            "MATCH (s:Source {url:$url}) "
            "SET s.version = coalesce(s.version, 0) + 1, s.updatedAt = $now "
            "SET s += $updates "
            "RETURN properties(s)",
            params={"url": url, "updates": updates, "now": __import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat()},
        )
        return dict(r.result_set[0][0]) if r.result_set else {}

    # ── Backfill Migration ────────────────────────────────────────

    def backfill_v25(self, dry_run: bool = False) -> dict:
        """Backfill existing tortoise.db to ONTOLOGY v2.5 schema."""
        proj = self._get_proj()
        report = {"dry_run": dry_run, "actions": []}

        # 1. Backfill status on Points
        r = proj.g.query("MATCH (n:Point) WHERE n.status IS NULL RETURN count(n)")
        missing_status = r.result_set[0][0]
        if missing_status > 0:
            report["actions"].append(f"status_backfill: {missing_status} Points")
            if not dry_run:
                proj.g.query(f"MATCH (n:Point) WHERE n.status IS NULL SET n.status = 'live'")

        # 2. Backfill pointKind
        r = proj.g.query("MATCH (n:Point) WHERE n.pointKind IS NULL RETURN count(n)")
        missing_kind = r.result_set[0][0]
        if missing_kind > 0:
            report["actions"].append(f"pointKind_backfill: {missing_kind} Points")
            if not dry_run:
                proj.g.query("MATCH (n:Point) WHERE n.pointKind IS NULL SET n.pointKind = 'statement'")

        # 3. Count existing edges
        r = proj.g.query("MATCH ()-[r]->() RETURN count(r)")
        report["edge_count"] = r.result_set[0][0]

        # 4. Verify Point count unchanged
        r = proj.g.query("MATCH (n:Point) RETURN count(n)")
        report["point_count"] = r.result_set[0][0]

        return report
