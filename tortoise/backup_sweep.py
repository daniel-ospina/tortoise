"""Backup sweep — enumerate teams and back up each team's knowledge graph.

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

Per-team protection (all guards from the reviewed plan):
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


def _delete_uploaded(storage, team_id: str, backup_id: str) -> None:
    """Best-effort removal of a just-uploaded (guard-rejected) backup."""
    for suffix in ("dump.enc", "manifest.json"):
        try:
            storage.delete(f"backups/{backup_id}/{suffix}")
        except Exception as e:
            logger.warning("cleanup of %s/%s failed: %s", backup_id, suffix, e)


def _backup_team(
    *,
    db,
    registry,
    storage,
    config: BackupConfig,
    team_id: str,
    graph_name: str,
    now: datetime,
    incidents: list[dict[str, Any]],
) -> dict[str, Any]:
    # ── Size guard: cheap COUNT before any dump (the #545 OOM blast radius). ──
    try:
        g = db.select_graph(graph_name)
        count = int(g.query("MATCH (n) RETURN count(n)").result_set[0][0])
    except Exception as e:
        return {"status": "error", "team_id": team_id, "error": str(e)}
    if count > config.size_guard_max_nodes:
        incidents.append(
            {
                "kind": "SIZE_GUARD_ABORT",
                "team_id": team_id,
                "detail": {"count": count, "max": config.size_guard_max_nodes},
            }
        )
        return {"status": "aborted_size_guard", "team_id": team_id, "count": count}

    prior = read_team_state(storage, team_id)
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
        logger.warning("per-label count query failed for %s — continuing", team_id)

    # ── Dump via the shipped pipeline (registry-stream-key, not GH-key, #661). ──
    proj = SimpleNamespace(g=g)
    if not config.registry_stream_key or len(config.registry_stream_key) != 32:
        return {"status": "error", "team_id": team_id,
                "error": "REGISTRY_STREAM_KEY missing or invalid — fail-closed (#661)"}
    try:
        manifest = create_backup(
            proj, registry, storage,
            team_id=team_id, graph_name=graph_name, key=config.registry_stream_key,
        )
    except Exception as e:
        return {"status": "error", "team_id": team_id, "error": str(e)}

    # ── P0 guard: the manifest must name the seam-derived graph and carry data.
    # The graph name comes from the enumeration seam (registry: team_{id};
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
                "detail": {
                    "manifest_graph_name": manifest.get("graph_name"),
                    "node_count": manifest.get("node_count"),
                },
            }
        )
        return {"status": "p0_guard_failed", "team_id": team_id}

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
                    "detail": {"previous": prev_node_count, "now": 0, "drop_pct": 100},
                }
            )
            return {"status": "data_loss_candidate", "team_id": team_id, "node_count": 0}
        # Steady-0 (chronic empty team) is a signal, never an incident.
        return {"status": "empty_skipped", "team_id": team_id, "node_count": 0}
    if prev_node_count > 0 and node_count < prev_node_count * 0.5:
        _delete_uploaded(storage, team_id, manifest.get("backup_id", ""))
        incidents.append(
            {
                "kind": "DATA_LOSS_CANDIDATE",
                "team_id": team_id,
                "detail": {
                    "previous": prev_node_count,
                    "now": node_count,
                    "drop_pct": round((1 - node_count / prev_node_count) * 100, 1),
                },
            }
        )
        return {"status": "data_loss_candidate", "team_id": team_id, "node_count": node_count}

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
                    "node_count": node_count}

    # ── Persist team state (counts feed the transition guard next run). ──
    _write_json(
        storage,
        f"{_TEAM_STATE_PREFIX}{team_id}/state.json",
        {
            "source": "backup",
            "latest_backup_at": manifest.get("created_at"),
            "latest_object_key": f"backups/{manifest.get('backup_id')}/dump.enc",
            "node_count": node_count,
            "counts": {"nodes": node_count, "edges": manifest.get("edge_count")},
            "label_counts": label_counts,
            "updated_at": now.isoformat(),
        },
    )

    # ── Retention. ──
    try:
        deleted = prune_backups(
            storage, team_id,
            keep_daily=config.retention_daily,
            keep_weekly=config.retention_weekly,
            keep_hourly=config.retention_hourly,
        )
    except Exception as e:
        logger.warning("prune failed for %s: %s", team_id, e)
        deleted = []

    return {
        "status": "backed_up",
        "team_id": team_id,
        "node_count": node_count,
        "pruned": len(deleted),
    }


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
    """Back up every team's knowledge graph. Returns the run result.

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
            # Seam per-team resolution (#669): read graph_name from the
            # control plane BEFORE the dump (Supabase mode reads
            # teams.graph_name; registry mode is the deterministic team_{id}
            # and never raises). Resolution failures are per-team isolated
            # like every other error.
            try:
                graph_name = team_graph_name(registry, team_id)
            except Exception as e:
                resolution_failures += 1
                results[team_id] = {
                    "status": "error", "team_id": team_id, "error": str(e),
                }
                continue
            try:
                results[team_id] = _backup_team(
                    db=db, registry=registry, storage=storage, config=config,
                    team_id=team_id, graph_name=graph_name, now=now,
                    incidents=incidents,
                )
            except Exception as e:  # per-team isolation: one bad team never
                # aborts the sweep for the others (review P3-2)
                logger.exception("sweep of %s failed: %s", team_id, e)
                results[team_id] = {"status": "error", "team_id": team_id, "error": str(e)}

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

    backed_up = sum(1 for r in results.values() if r.get("status") == "backed_up")
    return {
        "status": "backed_up" if backed_up else "no_work",
        "teams_backed_up": backed_up,
        "results": results,
        "incidents": incidents,
    }
