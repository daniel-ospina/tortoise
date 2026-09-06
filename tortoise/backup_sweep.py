"""Backup sweep — enumerate teams and back up each team's knowledge GRAPHS
(default + custom, #2313).

The sweep is the driver's core action. It is decoupled from the alert store:
conditions that need an operator's attention (size-guard abort, data-loss
candidate, P0-guard failure, team-universe shrink) are RETURNED as incidents;
the caller (the internal endpoint, Task 7) routes them to the alert store.

Team enumeration is the seam for the #669 control-plane migration: the
source is an adapter exposing ``query()`` — the FalkorDB registry graph
handle (``registry_control_plane``, pre-#669) or the Supabase control plane
(``teams`` table, post-#669). The dialect is auto-detected; a CI fake
(``tests.fake_control_plane.FakeControlPlane``) implements the same
interface so the #596 suite runs with zero network. Fail-closed: an
enumeration failure is NEVER classified as the chronic NO_TEAMS state.

Per-graph protection (all guards from the reviewed plan; each guard is
per-GRAPH — state, dumps, and retention are graph-scoped, #2313):
- Size guard: abort before dump if the graph exceeds the configured max nodes.
- P0 guard: ``manifest.graph_name`` must equal the seam-derived graph name
  (``team_{id}`` from the registry, ``teams.graph_name`` post-#669 —
  independent of the dump projection) AND ``node_count >= 1`` — a backup
  of the wrong/empty graph never stands; the just-uploaded objects are deleted.
- Empty-content transition guard: DATA_LOSS_CANDIDATE fires only on a
  transition (>0 → 0 nodes, or >50% drop vs the team's prior persisted count);
  steady-0 is a signal, not an incident. On fire, state.json is NOT written.
- Label-level drift guard (#661): per-label node-count checks with
  absolute-count-aware thresholds — a <50% wipe of a low-count label
  (e.g. Invitation) that passes the overall >50% guard still fires
  DATA_LOSS_CANDIDATE.
- Enumeration-delta guard: a prior team count > 0 → 0 fires an incident (a
  wiped enumeration source must not degrade silently to chronic NO_TEAMS).
- Per-team serialization via the caller's lock factory.
"""

from __future__ import annotations

import json
import logging
import os
from contextlib import nullcontext
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Callable  # noqa: UP035

from .backup_config import BackupConfig
from .hosted_backup import _is_supabase_source, create_backup, prune_backups

logger = logging.getLogger(__name__)

OPS_STATE_KEY = "ops/state.json"
_TEAM_STATE_PREFIX = "ops/teams/"

# ── Per-label DATA_LOSS thresholds (#661) ───────────────────────────────────
# Absolute-count-aware: small labels use a stricter guard so a <50% wipe of a
# low-count label (e.g. 3 → 1 Invitation nodes = 67% drop, 5 Invitation nodes
# on a 500-node graph) can't hide behind the overall node-count ratio.
#
# Configuration (env-driven, sensible defaults):
#   REGISTRY_LABEL_FLOOR (default 10): labels with fewer than this many nodes
#       use the absolute floor — ANY drop fires DATA_LOSS_CANDIDATE.
#   REGISTRY_LABEL_DRIFT_PCT (default 40): labels at or above the floor use
#       this relative threshold (a drop >= this % fires).


def _label_drift_floor() -> int:
    raw = os.environ.get("REGISTRY_LABEL_FLOOR", "10").strip()
    try:
        return int(raw)
    except ValueError:
        return 10


def _label_drift_pct() -> float:
    raw = os.environ.get("REGISTRY_LABEL_DRIFT_PCT", "40").strip()
    try:
        return float(raw) / 100.0
    except ValueError:
        return 0.4


def _enum_delta_suppressed() -> bool:
    """#669 flip window (P3-4, #771): suppression flag for ENUM_DELTA.

    At the flip the FalkorDB control-plane registry is deleted while Supabase
    (still empty at zero data) becomes the enumeration source — the team
    universe legitimately drops to 0, and the enumeration-delta guard would
    otherwise file a spurious incident ("wiped enumeration source"). The
    operator sets ``TORTOISE_SUPPRESS_ENUM_DELTA=1`` for the flip window
    ONLY (the pre-deploy gate asserts BOTH stores are empty, so suppressing
    during the flip cannot hide a real wipe); it must be unset after
    post-flip verification (#766) so the guard's protection is restored.
    """
    return os.environ.get("TORTOISE_SUPPRESS_ENUM_DELTA", "").strip().lower() in (
        "1", "true", "yes")


def _check_per_label_drift(
    *,
    prev_counts: dict[str, int],
    current_counts: dict[str, int],
    floor: int | None = None,
    drift_pct: float | None = None,
) -> dict[str, dict[str, int]] | None:
    """Check per-label node-count drift against absolute-count-aware thresholds.

    Returns a dict of label→{previous, now, drop_pct} for every label that
    breached its threshold, or None if all labels are within bounds.

    Guards (applied per-label):
      - prev count < floor: ANY drop fires (absolute floor — small labels
        can't silently dwindle).
      - prev count >= floor: drop >= drift_pct fires (relative threshold).
    """
    floor = _label_drift_floor() if floor is None else floor
    drift_pct = _label_drift_pct() if drift_pct is None else drift_pct
    breaches: dict[str, dict[str, int]] = {}
    for label, prev_n in prev_counts.items():
        now_n = current_counts.get(label, 0)
        if prev_n <= 0:
            continue
        if now_n >= prev_n:
            continue
        if prev_n < floor:
            # Absolute floor: any loss fires for small labels.
            if now_n < prev_n:
                breaches[label] = {
                    "previous": prev_n, "now": now_n,
                    "drop_pct": round((1 - now_n / prev_n) * 100, 1),
                    "threshold": f"floor={floor} (any drop)",
                }
        else:
            drop = (prev_n - now_n) / prev_n
            if drop >= drift_pct:
                breaches[label] = {
                    "previous": prev_n, "now": now_n,
                    "drop_pct": round(drop * 100, 1),
                    "threshold": f"drift>{drift_pct*100:.0f}%",
                }
    return breaches or None


def enumerate_teams(source) -> list[str]:
    """Seam: list Team ids from the control-plane source (#669).

    ``source`` is an adapter exposing ``query()``: the FalkorDB registry
    graph handle (pre-#669) or the Supabase control plane (post-#669) — the
    single swap point for the migration, dialect auto-detected. Supabase
    mode selects ``graph_name`` alongside ``id``: the sweep reads the graph
    name from ``teams`` (the column is the source of truth — SDK team
    creation names graphs ``team_{name}``, not ``team_{id}``; see #770).

    Confirmed-empty returns []; a query failure raises RuntimeError
    (fail-closed — never chronic NO_TEAMS).
    """
    try:
        if _is_supabase_source(source):
            rows = source.query("teams", select=["id", "graph_name"])
            return [str(r["id"]) for r in rows if r.get("id")]
        rows = source.query("MATCH (t:Team) RETURN t.id").result_set
        return [str(r[0]) for r in rows if r and r[0]]
    except Exception as e:
        raise RuntimeError(f"team enumeration failed: {e}") from e


def enumerate_eligible_teams(source) -> list[str]:
    """List Pro teams eligible for hosted backup (#655).

    Eligibility: tier != 'free' AND backup_enabled = true — registry Cypher
    or Supabase ``teams`` filters, dialect auto-detected (same seam as
    ``enumerate_teams``).

    Fail-closed: a query failure raises RuntimeError (same contract as
    ``enumerate_teams``).
    """
    try:
        if _is_supabase_source(source):
            rows = source.query(
                "teams",
                select=["id", "graph_name"],
                filters=[("tier", "neq", "free"), ("backup_enabled", "eq", True)],
            )
            return [str(r["id"]) for r in rows if r.get("id")]
        rows = source.query(
            "MATCH (t:Team) WHERE t.tier <> 'free' AND t.backup_enabled = true RETURN t.id"
        ).result_set
        return [str(r[0]) for r in rows if r and r[0]]
    except Exception as e:
        raise RuntimeError(f"eligible-team enumeration failed: {e}") from e


def team_graph_name(source, team_id: str) -> str:
    """Graph name for a team, read from the seam source (#669).

    Registry mode: deterministic ``team_{id}`` (the registry stores no graph
    name — provision writes ``team_{team_id}``, #770). Supabase mode:
    ``teams.graph_name`` is the source of truth (SDK team creation names
    graphs ``team_{name}`` — a sweep that assumed ``team_{id}`` would back up
    a nonexistent graph for SDK-created teams). Since #1903 the Supabase-lane
    provisions (create_team, onboarding sub-team) mint ``team_{team_id}``, so
    ``teams.graph_name`` now resolves ``team_{team_id}`` for dashboard teams
    and ``team_{name}`` only for registry-lane (sdk.team_create) teams (#2023).

    Fail-closed: a query error or a vanished/missing ``graph_name`` row
    raises RuntimeError — the sweep never guesses a graph name.
    """
    if _is_supabase_source(source):
        try:
            rows = source.query(
                "teams", select=["graph_name"], filters=[("id", "eq", team_id)]
            )
        except Exception as e:
            raise RuntimeError(
                f"team graph-name lookup failed for {team_id}: {e}"
            ) from e
        if not rows:
            raise RuntimeError(f"team {team_id} vanished from the control plane")
        graph_name = rows[0].get("graph_name")
        if not graph_name:
            raise RuntimeError(
                f"team {team_id} has no graph_name in the control plane"
            )
        return str(graph_name)
    return f"team_{team_id}"


def enumerate_team_graphs(source, team_id: str) -> list[dict[str, Any]]:
    """Per-team ACTIVE graph list — the per-graph sweep seam (#2313).

    Returns registry-shaped rows ``[{graph_id, kind, namespace}]``
    default-first (the DEFAULT graph always first, then customs by id) so
    the per-graph sweep loop can dump each active graph. Supabase mode
    delegates to the shared ``graph_metadata`` seam (derives the default
    from ``teams.graph_name`` + reads active kind='custom' rows) — one
    definition, no drift. Registry mode reads the Graph nodes in the
    registry graph and filters ``status == 'deleted'`` in-process (the
    registry lane returns tombstones; callers filter — same contract as
    GET /v1/graphs, hosted_api.py). The registry default node (random gid,
    kind='default') is normalized to the literal ``"default"`` so object
    keys are stable across lanes (Q4 owner decision).

    Fail-closed: a query failure raises RuntimeError — the sweep never
    guesses a graph list (same contract as ``enumerate_teams``).
    """
    if _is_supabase_source(source):
        try:
            from .supabase_control import graph_metadata

            rows = graph_metadata(source, team_id)
        except Exception as e:
            raise RuntimeError(
                f"team-graph enumeration failed for {team_id}: {e}") from e
        out = [
            {"graph_id": r["graph_id"], "kind": r["kind"],
             "namespace": r["namespace"]}
            for r in rows
        ]
        # graph_metadata is default-first + active-only already; keep a
        # deterministic order regardless of seam version.
        out.sort(key=lambda g: (0 if g["kind"] == "default" else 1,
                                g["graph_id"]))
        return out
    try:
        rows = source.query(
            "MATCH (g:Graph {team_id:$tid}) RETURN properties(g)",
            params={"tid": team_id},
        ).result_set
    except Exception as e:
        raise RuntimeError(
            f"team-graph enumeration failed for {team_id}: {e}") from e
    out = []
    for (props,) in rows:
        if (props.get("status") or "active") == "deleted":
            continue  # tombstones are never swept (#2304 quarantine)
        kind = props.get("kind") or "custom"
        out.append({
            # default-first key normalization (Q4): the registry default
            # node's random gid maps to the stable literal "default".
            "graph_id": "default" if kind == "default" else props.get("id"),
            "kind": kind,
            "namespace": props.get("namespace"),
        })
    out.sort(key=lambda g: (0 if g["kind"] == "default" else 1,
                            str(g["graph_id"] or "")))
    return out


def _read_json(storage, key: str) -> dict[str, Any]:
    try:
        parsed = json.loads(storage.download(key))
        return parsed if isinstance(parsed, dict) else {}
    except (KeyError, ValueError):
        return {}


def _write_json(storage, key: str, data: dict[str, Any]) -> None:
    storage.upload(key, json.dumps(data, indent=2).encode("utf-8"), content_type="application/json")


def read_ops_state(storage) -> dict[str, Any]:
    return _read_json(storage, OPS_STATE_KEY)


def read_team_state(storage, team_id: str) -> dict[str, Any]:
    return _read_json(storage, f"{_TEAM_STATE_PREFIX}{team_id}/state.json")


# ── #2370: legacy flat classification index ────────────────────────────────
# Pre-#2313 team-level ("flat") archives (backups/{team}/{ts}_{rnd}/…) carry
# their graph in the manifest graph_name but no graph_id key segment. The
# sweep classifies the flat pool ONCE per run (it already lists the team)
# and persists {backup_id: {graph_name, graph_id}} under
# ops/legacy-flat-index/{team}.json — flat manifest KEYS are immutable
# (prune only deletes, nothing rewrites an existing manifest), so a written
# classification never goes stale while the key exists. Consumers:
#   • the watcher's per-poll freshness reads ONE index object instead of
#     downloading every flat manifest per poll;
#   • GET /backups buckets legacy flats from the index instead of running a
#     control-plane reverse lookup per request;
#   • the legacy drain can tell default archives from C5-era custom flats.
# graph_id: the ACTIVE graph's id when the manifest names a custom namespace
# (list under that graph), "default" when it names the default namespace,
# or "" when unresolvable (deleted graph / drifted name — the default
# bucket stands, fail-soft).
_LEGACY_FLAT_INDEX_PREFIX = "ops/legacy-flat-index/"
_LEGACY_FLAT_INDEX_MAX = 2000


def _legacy_flat_index_key(team_id: str) -> str:
    return f"{_LEGACY_FLAT_INDEX_PREFIX}{team_id}.json"


def read_legacy_flat_index(storage, team_id: str) -> dict[str, Any]:
    return _read_json(storage, _legacy_flat_index_key(team_id))


def _classify_flat_pool(storage, team_id: str,
                        rows: list[dict[str, Any]]) -> dict[str, Any]:
    """#2370: read the team's legacy FLAT manifests and classify each by its
    manifest graph_name against the ACTIVE graph rows (namespace → graph_id).
    Only flat keys (backups/{team}/{key}/manifest.json — 4 segments) are
    indexed; nested (default + custom) manifests never appear here. Keys whose
    manifest read fails are skipped (retried next run; a classification is
    never guessed). Returns {backup_id: {graph_name, graph_id}}."""
    ns_to_gid = {
        str(r.get("namespace") or ""): r.get("graph_id")
        for r in rows if not r.get("_invalid")
    }
    out: dict[str, Any] = {}
    try:
        for k in storage.list(f"backups/{team_id}/"):
            parts = k.split("/")
            if len(parts) != 4 or not k.endswith("/manifest.json"):
                continue
            try:
                m = json.loads(storage.download(k))
            except Exception:
                continue  # transient — retried next run
            if not isinstance(m, dict):
                continue
            bid = str(m.get("backup_id") or "")
            name = str(m.get("graph_name") or "")
            if not bid:
                continue
            out[bid] = {"graph_name": name,
                        "graph_id": str(ns_to_gid.get(name) or "")}
            if len(out) >= _LEGACY_FLAT_INDEX_MAX:
                break
    except Exception as e:
        logger.warning("legacy flat classification failed for %s: %s",
                       team_id, e)
    return out


def _graph_state_key(team_id: str, graph_id: str) -> str:
    return f"{_TEAM_STATE_PREFIX}{team_id}/graphs/{graph_id}/state.json"


def read_graph_state(storage, team_id: str, graph_id: str) -> dict[str, Any]:
    """Per-graph sweep state (#2313). For the DEFAULT graph, an absent
    per-graph file falls back to the legacy team-level state file (the
    pre-#2313 bridge — first per-graph run inherits the transition-guard
    baseline; the sweep mirrors team-state for the default graph every run,
    so the fallback only ever fires for teams whose sweep never wrote a
    graph file before). Custom graphs have no legacy file — empty dict.
    """
    state = _read_json(storage, _graph_state_key(team_id, graph_id))
    if state or graph_id != "default":
        return state
    return _read_json(storage, f"{_TEAM_STATE_PREFIX}{team_id}/state.json")


def _delete_uploaded(storage, team_id: str, backup_id: str) -> None:
    """Best-effort removal of a just-uploaded (guard-rejected) backup."""
    for suffix in ("dump.enc", "manifest.json"):
        try:
            storage.delete(f"backups/{backup_id}/{suffix}")
        except Exception as e:
            logger.warning("cleanup of %s/%s failed: %s", backup_id, suffix, e)


def _sweep_graph_list(source, team_id: str) -> list[dict[str, Any]]:
    """Per-team ACTIVE sweep target list — default graph always first (#2313).

    The default graph is synthesized from the authoritative graph-name seam
    (``team_graph_name`` — registry ``team_{id}`` / Supabase
    ``teams.graph_name``), NOT from a kind='default' Graph row: pre-#2083
    teams have no Graph nodes at all, and for registry-lane SDK teams the
    seam is the name the sweep has always dumped (behavior-preserving; see
    #770/#2023). Custom graphs come from ``enumerate_team_graphs``; the row
    ``namespace`` is the FalkorDB graph to select/dump (customs are
    ``team_{tid}_{gid}``). kind='default' rows from the seam are skipped
    (the default is synthesized — never doubled).

    Rows: ``[{graph_id, kind, namespace, graph_name}]`` where
    ``graph_name`` is the dump/select name (namespace for customs; the
    seam-resolved default name). Fail-closed: any seam failure raises
    RuntimeError (the caller's resolution-failure isolation).
    """
    default_name = team_graph_name(source, team_id)  # raises → per-team resolution failure
    rows = enumerate_team_graphs(source, team_id)  # raises RuntimeError fail-closed
    graphs: list[dict[str, Any]] = [{
        "graph_id": "default", "kind": "default",
        "namespace": default_name, "graph_name": default_name,
    }]
    for r in rows:
        if r.get("kind") == "default":
            continue  # synthesized above — never doubled
        gid = r.get("graph_id")
        ns = r.get("namespace")
        if not gid or not ns:
            # A custom Graph row missing id/namespace cannot be dumped or
            # keyed — fail that graph closed (visible per-graph error), the
            # sweep never guesses a namespace.
            graphs.append({
                "graph_id": gid or f"custom_{len(graphs)}",
                "kind": r.get("kind", "custom"),
                "namespace": "", "graph_name": "", "_invalid": True,
            })
            continue
        graphs.append({
            "graph_id": gid, "kind": r.get("kind", "custom"),
            "namespace": ns, "graph_name": ns,
        })
    return graphs


def _backup_graph(
    *,
    db,
    registry,
    storage,
    config: BackupConfig,
    team_id: str,
    graph: dict[str, Any],
    now: datetime,
    incidents: list[dict[str, Any]],
) -> dict[str, Any]:
    """Back up ONE graph (default or custom) of a team (#2313).

    ``graph``: a ``_sweep_graph_list`` row — graph_id (object-key segment;
    ``"default"`` literal for the default graph, Q4 owner decision),
    graph_name (FalkorDB graph to select/dump). Every existing guard (size,
    P0, empty-transition, per-label drift) is per-graph: state, dumps, and
    retention are graph-scoped (Option A). For the default graph the
    artifacts/state are the graph-keyed equivalents of the pre-#2313
    team-level layout (the legacy team file is mirrored each run for the
    pre-#2313 consumers; legacy flat R2 objects drain via the team-level
    legacy prune in ``_sweep_team``).
    """
    graph_id = graph["graph_id"]
    graph_name = graph.get("graph_name") or ""
    if graph.get("_invalid"):
        return {"status": "error", "team_id": team_id, "graph_id": graph_id,
                "error": "custom Graph row missing id/namespace — cannot dump"}
    if not graph_name:
        return {"status": "error", "team_id": team_id, "graph_id": graph_id,
                "error": "no graph name resolved for graph"}

    # ── Size guard: cheap COUNT before any dump (the #545 OOM blast radius). ──
    try:
        g = db.select_graph(graph_name)
        count = int(g.query("MATCH (n) RETURN count(n)").result_set[0][0])
    except Exception as e:
        return {"status": "error", "team_id": team_id, "graph_id": graph_id,
                "error": str(e)}
    if count > config.size_guard_max_nodes:
        incidents.append(
            {
                "kind": "SIZE_GUARD_ABORT",
                "team_id": team_id,
                "graph_id": graph_id,
                "detail": {"count": count, "max": config.size_guard_max_nodes,
                           "graph_name": graph_name},
            }
        )
        return {"status": "aborted_size_guard", "team_id": team_id,
                "graph_id": graph_id, "count": count}

    prior = read_graph_state(storage, team_id, graph_id)
    prev_node_count = int(prior.get("node_count") or 0)
    prev_label_counts: dict[str, int] = prior.get("label_counts") or {}

    # ── Query per-label counts (before dump — same snapshot window). ──
    label_counts: dict[str, int] = {}
    try:
        rows = g.query(
            "MATCH (n) UNWIND labels(n) AS lbl RETURN lbl, count(*) ORDER BY lbl"
        ).result_set
        label_counts = {str(r[0]): int(r[1]) for r in rows if r and r[0]}
    except Exception:
        logger.warning("per-label count query failed for %s — continuing", graph_name)

    # ── Dump via the shipped pipeline (registry-stream-key, not GH-key, #661). ──
    proj = SimpleNamespace(g=g)
    if not config.registry_stream_key or len(config.registry_stream_key) != 32:
        return {"status": "error", "team_id": team_id, "graph_id": graph_id,
                "error": "REGISTRY_STREAM_KEY missing or invalid — fail-closed (#661)"}
    try:
        manifest = create_backup(
            proj, registry, storage,
            team_id=team_id, graph_name=graph_name, graph_id=graph_id,
            key=config.registry_stream_key,
        )
    except Exception as e:
        return {"status": "error", "team_id": team_id, "graph_id": graph_id,
                "error": str(e)}

    # ── P0 guard: the manifest must name the seam-derived graph and carry data.
    # The graph name comes from the sweep list seam (registry: team_{id};
    # Supabase: teams.graph_name — #669), so this is a broken-pipeline tripwire
    # rather than an independent wrong-graph detector — the independent teeth
    # are the non-empty requirement plus the restore-time isolation checks
    # (review P2-1). ──
    if manifest.get("graph_name") != graph_name:
        backup_id = manifest.get("backup_id", "")
        if backup_id:
            _delete_uploaded(storage, team_id, backup_id)
        incidents.append(
            {
                "kind": "P0_GUARD_FAIL",
                "team_id": team_id,
                "graph_id": graph_id,
                "detail": {
                    "manifest_graph_name": manifest.get("graph_name"),
                    "node_count": manifest.get("node_count"),
                },
            }
        )
        return {"status": "p0_guard_failed", "team_id": team_id, "graph_id": graph_id}

    node_count = int(manifest["node_count"])

    # ── Empty-content transition guard (no state.json write on fire). ──
    if node_count == 0:
        # An empty archive is never a usable backup — remove it either way.
        _delete_uploaded(storage, team_id, manifest.get("backup_id", ""))
        if prev_node_count > 0:
            # A >0 → 0 transition is the #101-empty class: incident, not signal.
            incidents.append(
                {
                    "kind": "DATA_LOSS_CANDIDATE",
                    "team_id": team_id,
                    "graph_id": graph_id,
                    "detail": {"previous": prev_node_count, "now": 0, "drop_pct": 100},
                }
            )
            return {"status": "data_loss_candidate", "team_id": team_id,
                    "graph_id": graph_id, "node_count": 0}
        # Steady-0 (chronic empty team) is a signal, never an incident.
        return {"status": "empty_skipped", "team_id": team_id,
                "graph_id": graph_id, "node_count": 0}
    if prev_node_count > 0 and node_count < prev_node_count * 0.5:
        _delete_uploaded(storage, team_id, manifest.get("backup_id", ""))
        incidents.append(
            {
                "kind": "DATA_LOSS_CANDIDATE",
                "team_id": team_id,
                "graph_id": graph_id,
                "detail": {
                    "previous": prev_node_count,
                    "now": node_count,
                    "drop_pct": round((1 - node_count / prev_node_count) * 100, 1),
                },
            }
        )
        return {"status": "data_loss_candidate", "team_id": team_id,
                "graph_id": graph_id, "node_count": node_count}

    # ── Per-label drift guard (#661): fires when the overall >50% ratio is
    # quiet but a low-count label took a hit (e.g. invitations). ──
    if prev_label_counts and label_counts:
        breaches = _check_per_label_drift(
            prev_counts=prev_label_counts, current_counts=label_counts,
        )
        if breaches:
            _delete_uploaded(storage, team_id, manifest.get("backup_id", ""))
            incidents.append(
                {
                    "kind": "DATA_LOSS_CANDIDATE",
                    "team_id": team_id,
                    "graph_id": graph_id,
                    "detail": {
                        "previous": prev_node_count,
                        "now": node_count,
                        "overall_drop_pct": round(
                            (1 - node_count / prev_node_count) * 100, 1
                        ),
                        "label_breaches": breaches,
                    },
                }
            )
            return {"status": "data_loss_candidate", "team_id": team_id,
                    "graph_id": graph_id, "node_count": node_count}

    state = {
        "source": "backup",
        "latest_backup_at": manifest.get("created_at"),
        "latest_object_key": f"backups/{manifest.get('backup_id')}/dump.enc",
        "node_count": node_count,
        "counts": {"nodes": node_count, "edges": manifest.get("edge_count")},
        "label_counts": label_counts,
        # graph_name names the dumped FalkorDB graph — the watcher uses the
        # DEFAULT graph's state graph_name to disambiguate legacy flat
        # manifests (custom-era on-demand dumps must not gate team freshness).
        "graph_name": graph_name,
        "graph_id": graph_id,
        "updated_at": now.isoformat(),
    }
    # ── Persist per-graph state (counts feed the transition guard next run).
    # The default graph ALSO mirrors the legacy team-level file (the pre-#2313
    # consumers — watcher staleness, GET /backups summary, re-baseline —
    # read it until Tasks 4/5 move them per-graph; the mirror keeps them
    # truthful about the default graph in the same PR). ──
    _write_json(storage, _graph_state_key(team_id, graph_id), state)
    if graph_id == "default":
        _write_json(storage, f"{_TEAM_STATE_PREFIX}{team_id}/state.json", state)

    # ── Retention (per-graph pool; the default graph's nested pool plus the
    # team-wide legacy flat drain in _sweep_team). ──
    try:
        deleted = prune_backups(
            storage, team_id,
            keep_daily=config.retention_daily,
            keep_weekly=config.retention_weekly,
            keep_hourly=config.retention_hourly,
            graph_id=graph_id,
        )
    except Exception as e:
        logger.warning("prune failed for %s: %s", graph_name, e)
        deleted = []

    return {
        "status": "backed_up",
        "team_id": team_id,
        "graph_id": graph_id,
        "node_count": node_count,
        "pruned": len(deleted),
    }


def _sweep_team(
    *,
    db,
    registry,
    storage,
    config: BackupConfig,
    team_id: str,
    now: datetime,
    incidents: list[dict[str, Any]],
) -> dict[str, Any]:
    """Sweep ONE team's active graphs (default + customs) (#2313).

    Returns the team summary — the DEFAULT graph's result (back-compat: the
    team-level result was the default graph pre-#2313) with a ``graphs``
    sub-map of per-graph results, plus a per-team legacy-flat-pool drain
    (pre-#2313 team-level R2 objects are only pruned team-wide — nested
    per-graph keys are never touched by a team-wide prune).
    """
    try:
        graphs = _sweep_graph_list(registry, team_id)
    except Exception as e:
        return {"status": "error", "team_id": team_id, "error": str(e),
                "resolution": True}

    graph_results: dict[str, Any] = {}
    any_backed_up = False
    for graph in graphs:
        try:
            gr = _backup_graph(
                db=db, registry=registry, storage=storage, config=config,
                team_id=team_id, graph=graph, now=now, incidents=incidents,
            )
        except Exception as e:  # per-graph isolation: one bad graph never
            # aborts the team's other graphs (review P3-2)
            logger.exception("sweep of %s/%s failed: %s", team_id,
                             graph.get("graph_id"), e)
            gr = {"status": "error", "team_id": team_id,
                  "graph_id": graph.get("graph_id"), "error": str(e)}
        graph_results[graph["graph_id"]] = gr
        if gr.get("status") == "backed_up":
            any_backed_up = True

    # Legacy flat-pool drain: pre-#2313 team-level artifacts (and any
    # straggler from pre-T5 on-demand endpoints) are pruned team-wide under
    # the same retention policy. Nested per-graph keys are never touched
    # here. The drain runs ONLY when the DEFAULT graph backed up this pass —
    # the pre-#2313 prune that managed the flat pool ran exactly on a
    # successful default dump. A default stuck in data-loss/error keeps its
    # last-known-good flat archives for restore/forensics instead of the
    # drain time-eroding the recovery source.
    default_status = graph_results.get("default", {}).get("status")
    # #2370: classify the flat pool every run and persist the index (one
    # object the watcher/list read instead of per-manifest downloads / CP
    # reverse lookups). Classification itself lists + reads the flats once
    # per run — cheap at hourly cadence.
    flats = _classify_flat_pool(storage, team_id, graphs)
    if default_status == "backed_up":
        try:
            prune_backups(
                storage, team_id,
                keep_daily=config.retention_daily,
                keep_weekly=config.retention_weekly,
                keep_hourly=config.retention_hourly,
            )
        except Exception as e:
            logger.warning("legacy team-level prune failed for %s: %s",
                           team_id, e)
    else:
        # #2370 indicator 4: while the default is failing (drain frozen to
        # protect its last-good archives), C5-era custom FLAT dumps are pure
        # duplicates of the custom's own nested pool (kept under the same
        # retention in _backup_graph) — prune them regardless of default
        # health. Only flats of ACTIVE customs that backed up this pass are
        # touched (nested copy confirmed); unresolvable/deleted-graph flats
        # stay (their disposition is #2304's purge decision).
        for bid, meta in list(flats.items()):
            gid = meta.get("graph_id") or ""
            if (gid and gid != "default"
                    and graph_results.get(gid, {}).get("status") == "backed_up"):
                for suffix in ("dump.enc", "manifest.json"):
                    try:
                        storage.delete(f"backups/{bid}/{suffix}")
                    except Exception as e:
                        logger.warning(
                            "custom-era flat cleanup of %s/%s failed: %s",
                            team_id, bid, e)
                flats.pop(bid, None)
    try:
        _write_json(storage, _legacy_flat_index_key(team_id), flats)
    except Exception as e:
        logger.warning("legacy flat index write failed for %s: %s",
                       team_id, e)

    default = graph_results.get("default", {})
    team_res = dict(default)
    team_res["team_id"] = team_id
    team_res["graphs"] = graph_results
    # Back-compat status: a team is backed_up iff its default graph was
    # (the pre-#2313 semantics); ``graphs`` carries per-graph detail.
    team_res["status"] = (
        default.get("status") if default else ("backed_up" if any_backed_up else "error")
    )
    return team_res


def resolve_active_graph(source, team_id: str, graph_id: str) -> dict[str, Any]:
    """Resolve a restore/re-baseline target graph to its ACTIVE sweep row.

    #2313 Task 5 tombstone guard: a restore target must be an ACTIVE graph
    of the team. The sweep list (``_sweep_graph_list``) contains ONLY active
    graphs — deleted (tombstoned/quarantined, #2304) and unknown graphs are
    absent, so resolution failure refuses the op with a clear error instead of
    swapping an archive into a quarantined or unregistered namespace. The
    default graph is always present (synthesized; never deletable).

    Returns the row (graph_id/kind/namespace/graph_name). Raises ValueError
    for deleted/unknown graphs (never RuntimeError — a MISSING graph is a
    client error, not a control-plane failure).
    """
    for row in _sweep_graph_list(source, team_id):
        if row.get("_invalid"):
            continue
        if row["graph_id"] == graph_id:
            return row
    raise ValueError(
        f"graph {graph_id!r} is not an active graph of team {team_id} "
        "-- restore/re-baseline refused (deleted or unknown)"
    )


def run_backup_sweep(
    *,
    db,
    registry,
    storage,
    config: BackupConfig,
    team_ids: list[str] | None = None,
    lock_for: Callable[[str], Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Back up every team's knowledge graphs (default + custom, #2313).
    Returns the run result.

    ``lock_for`` is an optional per-team lock factory (the endpoint supplies
    the asyncio-lock seam); the sweep serializes each team's dump under it.

    When ``config.team_sweep_enabled`` is True, only Pro teams (tier != 'free'
    AND backup_enabled) are enumerated (#655). A 0-eligible-teams result files
    a deduplicated NO_ELIGIBLE_TEAMS incident — the chronic-no-op alarm.
    """
    now = now or datetime.now(timezone.utc)  # noqa: UP017

    if team_ids is None:
        if config.team_sweep_enabled:
            try:
                team_ids = enumerate_eligible_teams(registry)
            except RuntimeError as e:
                return {"status": "enum_failed", "error": str(e), "teams_backed_up": 0}
        else:
            try:
                team_ids = enumerate_teams(registry)
            except RuntimeError as e:
                return {"status": "enum_failed", "error": str(e), "teams_backed_up": 0}

    incidents: list[dict[str, Any]] = []
    ops_state = read_ops_state(storage)
    prev_team_count = int(ops_state.get("last_team_count") or 0)

    if not team_ids:
        # ── Team-sweep no-op alarm (#655): enabled but 0 Pro teams ──
        if config.team_sweep_enabled:
            incidents.append(
                {
                    "kind": "NO_ELIGIBLE_TEAMS",
                    "team_id": "",
                    "detail": {"message": "team sweep enabled but 0 eligible (Pro) teams found"},
                }
            )
            _write_json(
                storage, OPS_STATE_KEY,
                {"last_team_count": 0, "updated_at": now.isoformat()},
            )
            return {
                "status": "no_eligible_teams",
                "teams_backed_up": 0,
                "results": {},
                "incidents": incidents,
            }
        # ── Legacy path: 0 ALL teams ──
        if prev_team_count > 0 and not _enum_delta_suppressed():
            # A wiped enumeration source must not degrade silently to the
            # chronic NO_TEAMS state — this is an incident, not a signal.
            # (#669 flip window P3-4: suppressed while the operator sets
            # TORTOISE_SUPPRESS_ENUM_DELTA=1 — the registry delete makes the
            # 0→0 drop legitimate; the pre-deploy gate guarantees both stores
            # are empty before the flip.)
            incidents.append(
                {
                    "kind": "ENUM_DELTA",
                    "team_id": "",
                    "detail": {"previous": prev_team_count, "now": 0},
                }
            )
        _write_json(
            storage, OPS_STATE_KEY,
            {"last_team_count": 0, "updated_at": now.isoformat()},
        )
        return {
            "status": "no_teams",
            "teams_backed_up": 0,
            "results": {},
            "incidents": incidents,
        }

    results: dict[str, Any] = {}
    resolution_failures = 0
    for team_id in sorted(team_ids):
        ctx = lock_for(team_id) if lock_for else nullcontext()
        with ctx:
            try:
                res = _sweep_team(
                    db=db, registry=registry, storage=storage, config=config,
                    team_id=team_id, now=now, incidents=incidents,
                )
            except Exception as e:  # per-team isolation: one bad team never
                # aborts the sweep for the others (review P3-2)
                logger.exception("sweep of %s failed: %s", team_id, e)
                res = {"status": "error", "team_id": team_id, "error": str(e)}
            results[team_id] = res
            if res.get("resolution"):
                # Graph-list resolution failure (vanished team, missing
                # graph_name, seam query error) — the flap guard counts it.
                resolution_failures += 1

    # ── Control-plane flapping alarm (#669): every enumerated team failing
    # graph-name resolution means the control plane died between enumeration
    # and the per-team phase. Without this, the sweep would return a clean
    # ``no_work`` with a fresh ops heartbeat — a chronic no-op that looks
    # healthy to the #596 watcher (the same silent-degradation class the
    # ENUM_DELTA / NO_ELIGIBLE_TEAMS guards exist to prevent). ──
    if resolution_failures and resolution_failures == len(team_ids):
        incidents.append(
            {
                "kind": "GRAPH_NAME_RESOLUTION_FAIL",
                "team_id": "",
                "detail": {
                    "message": (
                        "control-plane graph-name resolution failed for every "
                        "enumerated team — sweep degraded to no_work"
                    ),
                    "total": len(team_ids),
                    "failed": resolution_failures,
                },
            }
        )

    _write_json(
        storage, OPS_STATE_KEY,
        {
            "last_team_count": len(team_ids),
            "last_sweep_at": now.isoformat(),
            "updated_at": now.isoformat(),
        },
    )

    backed_up = sum(
        1 for r in results.values() if r.get("status") == "backed_up"
    )
    return {
        "status": "backed_up" if backed_up else "no_work",
        "teams_backed_up": backed_up,
        "results": results,
        "incidents": incidents,
    }
