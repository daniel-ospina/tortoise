"""Canonical onboarding FLOW-state module (#2001, W5).

ONE shared source of truth for the onboarding state machine: the canonical
step list, the card subset, the per-key-type semantics table, the fork-aware
completion gate, and the graph read/write primitives (OnboardingState node +
per-step OnboardingStep nodes + COMPLETED_STEP edges, all keyed by org_id).

Pins (scope doc §4, plan T1): per-step FWW edges; fork/compact set-once;
last_decide_attempt LWW; status/version server-owned + monotonic;
member_progress JSON-string map-merge (FalkorDB maps-not-storable, #498);
FLOW keys never enter jsonb (the router strips them — see hosted_api).

The module is graph-agnostic: every writer/reader takes a ``graph`` handle
with a ``.query(cypher, **params)`` surface (FalkorProjection) returning a
result with ``.result_set`` and ``.stats``. Importable without hosted_api
(no circular import).
"""
from __future__ import annotations

import json
import threading
from collections.abc import Iterable
from typing import Any

# ── canonical vocabulary ─────────────────────────────────────

# Display order = definition order (the Setup-guide card renders in this
# order; the completion gate is order-independent).
STEP_IDS: tuple[str, ...] = (
    "team-named",            # satisfied at org-create (name REQUIRED)
    "harness-connected",     # W2: agent harness connected
    "first-points-filed",    # W3 seed: org-anchor Subject filed
    "decide-completed",      # W3 decide (real decide protocol)
    "capture-disclosed",     # W6: memory-capture disclosure
    "catalog-presented",     # W8: catalog card presented
)
ONBOARDING_STEPS: frozenset[str] = frozenset(STEP_IDS)

# Card COUNTED subset (⊆ canonical): the rows the Setup-guide card counts
# toward N-of-M. capture-disclosed is canonical but NEVER a counted row —
# "capture-disclosed before decide must NOT render '4 of 4'" (#2001 pin).
# decide-completed and catalog-presented are fork-exclusive display rows
# (self shows decide; build shows catalog) — per-fork M = 3, never 4.
CARD_STEPS: tuple[str, ...] = (
    "harness-connected",
    "first-points-filed",
    "decide-completed",
    "catalog-presented",
)

STATUS_ACTIVE = "active"
STATUS_COMPLETE = "complete"
STATUS_VALUES = {STATUS_ACTIVE, STATUS_COMPLETE}

FORK_SELF = "self"
FORK_BUILD = "build"
FORK_VALUES = {FORK_SELF, FORK_BUILD}

# per-key semantics enum
FWW = "first-write-wins"      # step edges: idempotent keyed MERGE {org_id, step_id}
SET_ONCE = "set-once"         # fork / compact: first write wins, changed → 409
LWW = "last-write-wins"       # last_decide_attempt (with conditional skip)
SERVER_OWNED = "server-owned" # status / version: never client-writable, monotonic
MAP_MERGE = "map-merge"       # member_progress: user-scoped JSON-string merge

# FLOW keys — the graph-owned set. jsonb NEVER holds these (router strips
# them before the allowlist filter; the registration-split negatives pin it).
FLOW_KEYS: frozenset[str] = frozenset({
    "fork", "status", "version", "completed_steps",
    "member_progress", "last_decide_attempt", "compact",
})

PER_KEY_SEMANTICS: dict[str, str] = {step: FWW for step in STEP_IDS}
PER_KEY_SEMANTICS.update({
    "fork": SET_ONCE,
    "compact": SET_ONCE,
    "last_decide_attempt": LWW,
    "status": SERVER_OWNED,
    "version": SERVER_OWNED,
    "member_progress": MAP_MERGE,
})

# gate definitions (epic plan §2 WF-4, scope pin 12) — compact-first
_GATE_SELF: frozenset[str] = frozenset({
    "team-named", "harness-connected", "first-points-filed", "decide-completed",
})
_GATE_BUILD: frozenset[str] = frozenset({
    "harness-connected", "first-points-filed", "catalog-presented",
})
_GATE_COMPACT: frozenset[str] = frozenset({
    "harness-connected", "first-points-filed",
})
_GATES: dict[str, frozenset[str]] = {
    FORK_SELF: _GATE_SELF,
    FORK_BUILD: _GATE_BUILD,
}

# edge labels / node labels
ONBOARDING_NODE_LABEL = "OnboardingState"
ONBOARDING_STEP_LABEL = "OnboardingStep"
COMPLETED_STEP_EDGE = "COMPLETED_STEP"
ONBOARDS_EDGE = "onboards"  # OnboardingState → Organization Subject (W3 seed writes)

# default node shape (byte-identical across eager init + create-on-write)
_NODE_DEFAULTS: dict[str, Any] = {
    "status": STATUS_ACTIVE,
    "version": 1,
    "member_progress": "{}",          # JSON string — FalkorDB maps-not-storable
    "last_decide_attempt": None,      # absent until first attempt
    "compact": False,
}

# ── pure helpers ─────────────────────────────────────────────

def validate_step_id(step_id: str) -> bool:
    """True iff step_id is a canonical onboarding step."""
    return isinstance(step_id, str) and step_id in ONBOARDING_STEPS


def completion_gate_satisfied(completed_steps: Iterable[str],
                              fork: str | None,
                              compact: bool) -> bool:
    """Fork-aware completion gate (epic §2 WF-4, scope pin 12).

    compact-first: a compact org needs only the reduced checklist regardless
    of fork. fork=None/unknown → 'self' (read-time default — the J6 rule:
    fork is only persisted on explicit opt-in).
    """
    done = set(completed_steps)
    required = _GATE_COMPACT if compact else _GATES.get(fork or FORK_SELF, _GATE_SELF)
    return required <= done


def flow_defaults() -> dict[str, Any]:
    """FLOW read defaults for node-absent orgs (grandfathered pre-backfill)
    — served read-only, NEVER written by the read path (pin 4)."""
    return {
        "fork": None,
        "status": STATUS_ACTIVE,
        "version": 1,
        "completed_steps": [],
        "member_progress": {},
        "last_decide_attempt": None,
        "compact": False,
    }


def flow_unavailable() -> dict[str, Any]:
    """FLOW markers for graph-down reads (200, never fabricated defaults)."""
    return {k: "unavailable" for k in flow_defaults()}


def parse_member_progress(raw: Any) -> dict[str, list[str]]:
    """Decode the member_progress JSON-string property (FalkorDB
    maps-not-storable, #498)."""
    if isinstance(raw, dict):
        return dict(raw)
    if not isinstance(raw, str):
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def resolve_wire_completion(node_status: str | None,
                            raw_complete: bool,
                            completed_steps: Iterable[str]) -> bool:
    """NODE-AWARE wire completion (scope pins 4/13 + cycle-2 P1-1 fix):

    1. node.status == 'complete' → True (server-owned, gate-written).
    2. Grandfathered-window guard: node present but NOT complete, ZERO
       AGENT step edges (the team-named edge is auto-satisfied at init and
       never counts), and jsonb onboarding_complete=true → True — kills the
       poisoned-false window for orgs completing via the legacy wizard
       during the T2→T7 carve-out. One-directional and self-terminating:
       the FIRST agent step edge flips control to the node.
    3. Otherwise → False (a node-present org without gate-complete status
       is never re-onboarded via the flag; accept-and-drop makes the jsonb
       writer inert post-W1)."""
    if node_status == STATUS_COMPLETE:
        return True
    agent_steps = [s for s in completed_steps if s != "team-named"]
    return bool(raw_complete and not agent_steps)


# ── graph Cypher fragments ───────────────────────────────────

def onboarding_node_init_fragment(*, fork: str | None = None,
                                  compact: bool = False,
                                  team_named_edge: bool = True) -> str:
    """Eager-init Cypher suffix — byte-identical for every TeamMeta lane
    (register_user ×2, create_team, sdk.team_create, provision_tenant),
    the write-time create-on-write seam, and the backfill.

    The same string is APPENDED to the lane's existing TeamMeta statement
    (multi-statement query) so node + TeamMeta land in ONE Cypher round trip
    (graph-side atomicity, scope pin 10).
    """
    sets = [
        "n.status = $os_status",
        "n.version = 1",
        "n.member_progress = $os_member_progress",
        "n.compact = $os_compact",
    ]
    if fork is not None:
        sets.append("n.fork = $os_fork")
    lines = [
        "MERGE (n:OnboardingState {org_id: $org_id}) "
        "ON CREATE SET " + ", ".join(sets),
    ]
    if team_named_edge:
        lines.append(
            "MERGE (s_tn:OnboardingStep {org_id: $org_id, step_id: 'team-named'})"
        )
        lines.append("MERGE (n)-[:COMPLETED_STEP]->(s_tn)")
    return "\n".join(lines)


def resolve_init_fork_compact(prior_active: bool,
                              earliest_prior_fork: str | None) -> tuple[str | None, bool]:
    """Eager-init discriminator (scope pin 11): compact = the creator has
    prior memberships; fork = the earliest prior org's fork, 'self' fallback
    (never re-asks the fork card); None when the creator has NO prior orgs
    (first org → the fork card is asked exactly once, set-once persists)."""
    if not prior_active:
        return None, False
    return (earliest_prior_fork if earliest_prior_fork in FORK_VALUES else FORK_SELF), True


def read_prior_org_fork(graph: Any, prior_org_id: str) -> str | None:
    """Read a prior org's OnboardingState.fork for inheritance. None on
    absence or graph failure — callers fall back to 'self' (never re-asks)."""
    try:
        node = read_onboarding_node(graph, prior_org_id)
    except Exception:
        return None
    if not node:
        return None
    fork = node.get("fork")
    return fork if fork in FORK_VALUES else None


def eager_init_query(team_meta_cypher: str, team_meta_params: dict[str, Any], *,
                     org_id: str, fork: str | None = None,
                     compact: bool = False) -> tuple[str, dict[str, Any]]:
    """Append the OnboardingState init to a TeamMeta CREATE so both land in
    ONE Cypher query (graph-side atomicity, scope pin 10). Returns the
    combined query + merged params for the lane's ``graph.query`` call."""
    fragment = onboarding_node_init_fragment(fork=fork, compact=compact)
    params: dict[str, Any] = dict(team_meta_params)
    params["org_id"] = org_id
    params.update(_node_init_params(fork=fork, compact=compact))
    return f"{team_meta_cypher}\n{fragment}", params


def _node_init_params(*, fork: str | None = None, compact: bool = False,
                      status: str = STATUS_ACTIVE) -> dict[str, Any]:
    params: dict[str, Any] = {
        "os_status": status,
        "os_member_progress": _NODE_DEFAULTS["member_progress"],
        "os_compact": bool(compact),
    }
    if fork is not None:
        params["os_fork"] = fork
    return params


# ── per-org write serialization (embedded MERGE re-fire caveat,
#    sdk.py:830-872: concurrent same-key MERGEs re-fire ON CREATE and both
#    report created:1 — the per-org lock keeps the W11 created-signal honest
#    in the embedded lane; the docker lane's bolt:// stats are honest
#    natively) ────────────────────────────────────────────────

_locks_guard = threading.Lock()
_org_locks: dict[str, threading.Lock] = {}


def _org_lock(org_id: str) -> threading.Lock:
    with _locks_guard:
        lock = _org_locks.get(org_id)
        if lock is None:
            lock = _org_locks[org_id] = threading.Lock()
        return lock


def _run(graph: Any, cypher: str, params: dict[str, Any] | None = None):
    """Run a query on the injected graph handle — tolerant of both the
    FalkorProjection surface (``.query(cypher, **params)``) and the raw
    graph surface (``.query(cypher, params=...)``). A cypher/DB error
    raises a ResponseError (never a TypeError), so the fallback only fires
    on a signature mismatch."""
    params = params or {}
    try:
        return graph.query(cypher, **params)
    except TypeError:
        return graph.query(cypher, params=params)


def _relations_created(result) -> int:
    """Edge creations reported by the query — the FalkorDB client exposes
    flat ``relationships_created`` on the QueryResult (docker + embedded)."""
    val = getattr(result, "relationships_created", None)
    if val is None:
        stats = getattr(result, "stats", None)
        if stats is not None:
            val = getattr(stats, "relations_created", None)
    return int(val or 0)


# ── graph writers / readers ──────────────────────────────────

def ensure_onboarding_state_node(graph: Any, org_id: str, *,
                                 fork: str | None = None,
                                 compact: bool = False,
                                 status_from_mirror: bool | None = None,
                                 team_named_edge: bool = True) -> None:
    """Idempotent keyed-MERGE init (write-time create-on-write seam).

    Mirrors jsonb ``onboarding_complete`` → status ONE-DIRECTIONALLY at
    creation (never clobbers an existing node's status; never jsonb-false →
    complete).
    """
    status = STATUS_COMPLETE if status_from_mirror is True else STATUS_ACTIVE
    cypher = onboarding_node_init_fragment(
        fork=fork, compact=compact, team_named_edge=team_named_edge)
    params = {"org_id": org_id}
    params.update(_node_init_params(fork=fork, compact=compact, status=status))
    with _org_lock(org_id):
        _run(graph, cypher, params)


def read_onboarding_node(graph: Any, org_id: str) -> dict[str, Any] | None:
    """Raw OnboardingState node properties; None when absent. Raises on graph
    failure — callers decide 'unavailable' vs defaults (projection T3)."""
    res = _run(graph,
               f"MATCH (n:{ONBOARDING_NODE_LABEL} {{org_id: $org_id}}) RETURN n",
               {"org_id": org_id})
    rows = res.result_set
    if not rows:
        return None
    node = rows[0][0]
    return dict(node) if isinstance(node, dict) else _node_props(node)


def _node_props(node: Any) -> dict[str, Any]:
    """FalkorDB Node objects expose ``.properties`` (dict); a plain dict is
    the safe universal fallback."""
    props = getattr(node, "properties", None)
    if isinstance(props, dict):
        return dict(props)
    if isinstance(node, dict):
        return dict(node)
    return {}


def completed_steps(graph: Any, org_id: str) -> list[str]:
    """Canonical completed-step ids from the edge set (never a second store)."""
    res = _run(graph,
               f"MATCH (n:{ONBOARDING_NODE_LABEL} {{org_id: $org_id}})"
               f"-[:{COMPLETED_STEP_EDGE}]->(s:{ONBOARDING_STEP_LABEL}) "
               "RETURN s.step_id",
               {"org_id": org_id})
    return [row[0] for row in res.result_set]


def write_completed_step(graph: Any, org_id: str, step_id: str, *,
                         status_from_mirror: bool | None = None) -> dict[str, Any]:
    """Idempotent keyed-MERGE step write (FWW). Returns the W11 created-signal:
    ``{"created": bool, "step_id": step_id}`` — True iff the edge was NEW
    (docker lane: bolt:// MERGE stats; embedded lane: honest under the
    per-org lock)."""
    if not validate_step_id(step_id):
        raise ValueError(f"unknown onboarding step: {step_id!r}")
    # write-time create-on-write seam first (byte-identical eager init).
    ensure_onboarding_state_node(graph, org_id,
                                 status_from_mirror=status_from_mirror)
    with _org_lock(org_id):
        res = _run(graph,
                   f"MERGE (s:{ONBOARDING_STEP_LABEL} "
                   "{org_id: $org_id, step_id: $step_id}) "
                   f"WITH s "
                   f"MERGE (n:{ONBOARDING_NODE_LABEL} {{org_id: $org_id}}) "
                   "WITH n, s "
                   f"MERGE (n)-[:{COMPLETED_STEP_EDGE}]->(s)",
                   {"org_id": org_id, "step_id": step_id})
    return {"created": _relations_created(res) == 1, "step_id": step_id}


def write_fork(graph: Any, org_id: str, fork: str, *,
               compact: bool = False,
               status_from_mirror: bool | None = None) -> str:
    """Set-once fork write. Returns 'set' (first write), 'same' (replay of
    the same value → 200), or 'conflict' (different value → 409).
    Atomic single statement; creates the node on write if absent (the
    create-on-write seam applies to every FLOW write)."""
    if fork not in FORK_VALUES:
        raise ValueError(f"invalid fork: {fork!r}")
    status = STATUS_COMPLETE if status_from_mirror is True else STATUS_ACTIVE
    params = {"org_id": org_id, "fork": fork, "os_status": status}
    params.update(_node_init_params(compact=compact))
    with _org_lock(org_id):
        res = _run(graph,
                   f"MERGE (n:{ONBOARDING_NODE_LABEL} {{org_id: $org_id}}) "
                   "ON CREATE SET n.status = $os_status, n.version = 1, "
                   "n.member_progress = $os_member_progress "
                   f"WITH n, CASE WHEN n.fork IS NULL THEN 'set' "
                   "WHEN n.fork = $fork THEN 'same' ELSE 'conflict' END "
                   "AS outcome "
                   "SET n.fork = CASE WHEN n.fork IS NULL THEN $fork "
                   "ELSE n.fork END "
                   f"MERGE (s_tn:{ONBOARDING_STEP_LABEL} "
                   "{org_id: $org_id, step_id: 'team-named'}) "
                   f"MERGE (n)-[:{COMPLETED_STEP_EDGE}]->(s_tn) "
                   "RETURN outcome",
                   params)
    return res.result_set[0][0]


def write_compact(graph: Any, org_id: str, compact: bool, *,
                  status_from_mirror: bool | None = None) -> str:
    """Set-once compact write (same contract as write_fork)."""
    status = STATUS_COMPLETE if status_from_mirror is True else STATUS_ACTIVE
    params = {"org_id": org_id, "compact": bool(compact),
              "os_status": status}
    params.update(_node_init_params(compact=bool(compact)))
    with _org_lock(org_id):
        res = _run(graph,
                   f"MERGE (n:{ONBOARDING_NODE_LABEL} {{org_id: $org_id}}) "
                   "ON CREATE SET n.status = $os_status, n.version = 1, "
                   "n.member_progress = $os_member_progress "
                   f"WITH n, CASE WHEN n.compact IS NULL THEN 'set' "
                   "WHEN n.compact = $compact THEN 'same' ELSE 'conflict' END "
                   "AS outcome "
                   "SET n.compact = CASE WHEN n.compact IS NULL THEN $compact "
                   "ELSE n.compact END "
                   f"MERGE (s_tn:{ONBOARDING_STEP_LABEL} "
                   "{org_id: $org_id, step_id: 'team-named'}) "
                   f"MERGE (n)-[:{COMPLETED_STEP_EDGE}]->(s_tn) "
                   "RETURN outcome",
                   params)
    return res.result_set[0][0]


def decide_completed_edge_exists(graph: Any, org_id: str) -> bool:
    """True iff the decide-completed edge exists (LWW conditional)."""
    res = _run(graph,
               f"MATCH (n:{ONBOARDING_NODE_LABEL} {{org_id: $org_id}})"
               f"-[:{COMPLETED_STEP_EDGE}]->"
               f"(:{ONBOARDING_STEP_LABEL} {{step_id: 'decide-completed'}}) "
               "RETURN count(*) > 0",
               {"org_id": org_id})
    return bool(res.result_set[0][0])


def write_last_decide_attempt(graph: Any, org_id: str,
                              value: str | None, *,
                              status_from_mirror: bool | None = None) -> None:
    """LWW write with the conditional guard: 'failed' is SKIPPED when the
    decide-completed edge exists (a completed decide can never regress to
    failed — dismissal alone never completes; failed never un-completes).
    Create-on-write seam first (absent-node orgs — grandfathered pre-backfill
    — must not silently no-op)."""
    if value not in (None, "failed", "dismissed"):
        raise ValueError(f"invalid last_decide_attempt: {value!r}")
    ensure_onboarding_state_node(graph, org_id,
                                 status_from_mirror=status_from_mirror)
    with _org_lock(org_id):
        if value == "failed" and decide_completed_edge_exists(graph, org_id):
            return
        if value is None:
            _run(graph,
                 f"MATCH (n:{ONBOARDING_NODE_LABEL} {{org_id: $org_id}}) "
                 "REMOVE n.last_decide_attempt",
                 {"org_id": org_id})
            return
        _run(graph,
             f"MATCH (n:{ONBOARDING_NODE_LABEL} {{org_id: $org_id}}) "
             "SET n.last_decide_attempt = $value",
             {"org_id": org_id, "value": value})


def member_progress_map(graph: Any, org_id: str) -> dict[str, list[str]]:
    """Current member_progress as a dict (decoded JSON-string property)."""
    node = read_onboarding_node(graph, org_id)
    return parse_member_progress((node or {}).get("member_progress"))


def write_member_progress(graph: Any, org_id: str, user_id: str,
                          steps: list[str], *,
                          status_from_mirror: bool | None = None) -> dict[str, list[str]]:
    """User-scoped map-merge: sets {user_id: steps}, returns the merged map.
    Auth (session-only user vs key-auth) is enforced by the caller — the
    checkpoint endpoint rejects key-auth non-UUID users (403). Create-on-write
    seam first (absent-node orgs must not silently no-op)."""
    ensure_onboarding_state_node(graph, org_id,
                                 status_from_mirror=status_from_mirror)
    with _org_lock(org_id):
        merged = member_progress_map(graph, org_id)
        merged[user_id] = list(steps)
        _run(graph,
             f"MATCH (n:{ONBOARDING_NODE_LABEL} {{org_id: $org_id}}) "
             "SET n.member_progress = $value",
             {"org_id": org_id, "value": json.dumps(merged)})
        return merged


def write_status(graph: Any, org_id: str, status: str, *,
                 status_from_mirror: bool | None = None) -> None:
    """Server-owned, MONOTONIC status write: 'complete' can never regress to
    'active' (a grandfathered/first-FLOW-write org is never re-onboarded).
    Create-on-write seam first (absent-node orgs must not silently no-op)."""
    if status not in STATUS_VALUES:
        raise ValueError(f"invalid status: {status!r}")
    ensure_onboarding_state_node(graph, org_id,
                                 status_from_mirror=status_from_mirror)
    with _org_lock(org_id):
        _run(graph,
             f"MATCH (n:{ONBOARDING_NODE_LABEL} {{org_id: $org_id}}) "
             "SET n.status = CASE WHEN n.status = $complete THEN $complete "
             "ELSE $status END",
             {"org_id": org_id, "status": status, "complete": STATUS_COMPLETE})


def backfill_org(graph: Any, org_id: str, legacy_complete: bool, *,
                 dry_run: bool = True) -> dict[str, Any]:
    """Grandfathered backfill for ONE org (scope pin 14) — migration LAST.

    - absent-node-only: a node-present org is NEVER touched (its status is
      authoritative; backfill never clobbers).
    - jsonb onboarding_complete=true → status 'complete' (one-directional;
      never jsonb-false → complete; never status → jsonb).
    - fork stays null — the read-time default; persisted only on explicit
      opt-in (J6).
    - re-run no-op (the node exists on the second pass → skipped).

    Returns {org_id, action} for the wrapper's report."""
    node = read_onboarding_node(graph, org_id)
    if node is not None:
        return {"org_id": org_id, "action": "skipped-node-present"}
    if not legacy_complete:
        return {"org_id": org_id, "action": "skipped-not-complete"}
    if dry_run:
        return {"org_id": org_id, "action": "would-create-complete"}
    ensure_onboarding_state_node(graph, org_id, status_from_mirror=True)
    return {"org_id": org_id, "action": "created-complete"}


def recompute_completion(graph: Any, org_id: str,
                         legacy_complete: bool | None) -> str:
    """T7 recompute sweep per org (plan T7, cycle-2 P3-1 fix):

    GRANDFATHERED branch runs BEFORE gate eval: zero AGENT step edges + a
    legacy jsonb onboarding_complete=true → status stays/writes 'complete'
    (never active — a legacy-wizard completer is never re-onboarded). Then
    the fork-aware gate eval for edge-bearing orgs. Monotonic: complete
    never regresses. Returns the outcome label for the sweep report."""
    node = read_onboarding_node(graph, org_id)
    if node is None:
        return "unchanged-no-node"
    if node.get("status") == STATUS_COMPLETE:
        return "unchanged-already-complete"
    steps = completed_steps(graph, org_id)
    agent_steps = [s for s in steps if s != "team-named"]
    if legacy_complete and not agent_steps:
        write_status(graph, org_id, STATUS_COMPLETE)
        return "complete-grandfathered"
    if completion_gate_satisfied(steps, node.get("fork"),
                                 bool(node.get("compact"))):
        write_status(graph, org_id, STATUS_COMPLETE)
        return "complete-gate"
    return "unchanged"
