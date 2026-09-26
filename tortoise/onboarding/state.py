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
from datetime import UTC
from typing import Any

# ── canonical vocabulary ─────────────────────────────────────

# Display order = definition order (the Setup-guide card renders in this
# order; the completion gate is order-independent). "connection-written" is
# canonical but NOT a card row (CARD_STEPS is the counted, completion-
# relevant subset — like capture-disclosed).
STEP_IDS: tuple[str, ...] = (
    "team-named",            # satisfied at org-create (name REQUIRED)
    # #3451: the MCP-config WRITE — the one step no server can verify. A
    # self-installing harness writes the config onto the user's disk and then
    # hands off to a restart (§3); the server cannot observe that file. This
    # id records the CLIENT's act (an agent-credential checkpoint), never a
    # server observation, and is in NO completion gate — completion is
    # unchanged (#3913). It exists so "config written, restart pending" is
    # distinguishable from "the write never happened": see
    # ``restart_pending`` (the condition is DERIVED from this edge, never a
    # second stored field).
    "connection-written",
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
# decide-completed is the self-fork display row; the build fork renders only
# harness-connected + first-points-filed (#3913 — the build gate is no longer
# catalog-based). Per-fork M = 3 (self) / 2 (build), never 4.
CARD_STEPS: tuple[str, ...] = (
    "harness-connected",
    "first-points-filed",
    "decide-completed",
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
LWW = "last-write-wins"       # last_decide_attempt; fork_unsure_at (re-stamp on re-ask)
SERVER_OWNED = "server-owned" # status / version: never client-writable, monotonic
MAP_MERGE = "map-merge"       # member_progress: user-scoped JSON-string merge

# FLOW keys — the graph-owned set. jsonb NEVER holds these (router strips
# them before the allowlist filter; the registration-split negatives pin it).
# fork_unsure_at (#2407, Data Model 1): the "not sure yet — decide later"
# fork-card record. Server-stamped ISO timestamp; the set-once fork value is
# NOT consumed (fork stays NULL so the card keeps rendering as answerable).
FLOW_KEYS: frozenset[str] = frozenset({
    "fork", "status", "version", "completed_steps",
    "member_progress", "last_decide_attempt", "compact",
    "fork_unsure_at",
})

PER_KEY_SEMANTICS: dict[str, str] = {step: FWW for step in STEP_IDS}
PER_KEY_SEMANTICS.update({
    "fork": SET_ONCE,
    "compact": SET_ONCE,
    "last_decide_attempt": LWW,
    "fork_unsure_at": LWW,
    "status": SERVER_OWNED,
    "version": SERVER_OWNED,
    "member_progress": MAP_MERGE,
})

# gate definitions (epic plan §2 WF-4, scope pin 12) — compact-first
# #3913 (owner ruling 2026-09-20): the build fork completes on the two acts
# the server OBSERVES — a harness reached the server (harness-connected) and
# a first point was filed (first-points-filed). `catalog-presented` is no
# longer required (the id stays accepted on the checkpoint allowlist so
# existing orgs' completed_steps remain valid — no data migration).
_GATE_SELF: frozenset[str] = frozenset({
    "team-named", "harness-connected", "first-points-filed", "decide-completed",
})
_GATE_BUILD: frozenset[str] = frozenset({
    "harness-connected", "first-points-filed",
})
_GATE_COMPACT: frozenset[str] = frozenset({
    "harness-connected", "first-points-filed",
})
_GATES: dict[str, frozenset[str]] = {
    FORK_SELF: _GATE_SELF,
    FORK_BUILD: _GATE_BUILD,
}

# Steps that are NOT evidence of agent onboarding work, and therefore do NOT
# terminate the grandfathered-window guard (``resolve_wire_completion``,
# ``recompute_completion``, ``_legacy_grandfathered``):
#
# - ``team-named`` is auto-satisfied at org-create (name REQUIRED) — never an
#   agent act.
# - ``connection-written`` (#3451) records a CLIENT-side config WRITE that the
#   server cannot observe. #3913 rests completion on the acts the server
#   OBSERVES, so a trace of a local file write must not close a completion the
#   wire still grants — it says nothing about whether a harness ever connected.
#   Without this exclusion, the §3 checkpoint alone would flip a legitimately
#   grandfathered org to incomplete before the restart was even attempted.
#
# The window closes on the first SERVER-OBSERVED agent step.
_NON_AGENT_STEPS: frozenset[str] = frozenset(
    {"team-named", "connection-written"})

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


def restart_pending(completed_steps: Iterable[str]) -> bool:
    """#3451: the config was WRITTEN and the harness is not yet verified.

    DERIVED from the recorded step set — deliberately NOT a second stored
    field (no ``restart_pending`` FLOW key, no jsonb key): the projection the
    resuming agent already reads carries the condition. Both directions are
    load-bearing:

    - ``["team-named"]`` — the write never happened → False. An abandoned
      install is NEVER reported as waiting for a restart.
    - ``["team-named", "connection-written"]`` → True. The config is in
      place; only the restart verification is outstanding, so §1 hands the
      user a relaunch instead of re-walking §2–§3.
    - ``"harness-connected"`` present → False. Verified; nothing is pending.

    The parameter is a KNOWN step collection. A caller that does not know the
    set (a graph-down read serves the literal ``'unavailable'``) must not call
    this and report its ``False`` as fact — the projection serves
    ``'unavailable'`` there instead.
    """
    if not isinstance(completed_steps, (list, tuple, set, frozenset)):
        # A graph-down 'unavailable' marker is not a step set — fail to
        # "not pending" only for a real collection, never by string scan.
        return False
    done = set(completed_steps)
    return "connection-written" in done and "harness-connected" not in done


def completion_gate_satisfied(completed_steps: Iterable[str],
                              fork: str | None,
                              compact: bool,
                              *, fork_unsure_at: bool = False) -> bool:
    """Fork-aware completion gate (epic §2 WF-4, scope pin 12).

    compact-first: a compact org needs only the reduced checklist regardless
    of fork. fork=None/unknown → 'self' (read-time default — the J6 rule:
    fork is only persisted on explicit opt-in) UNLESS the org recorded the
    #2407 unsure fork-card answer (fork_unsure_at): then the gate is NOT
    evaluable on the read-time self default — onboarding must not auto-close
    an org as 'self' while the fork question is still open (it can be
    answered later; until then the org stays active).
    """
    done = set(completed_steps)
    if compact:
        required = _GATE_COMPACT
    elif fork in FORK_VALUES:
        required = _GATES[fork]
    elif fork_unsure_at:
        return False
    else:
        required = _GATE_SELF  # J6 read-time default
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
        "fork_unsure_at": None,
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
       AGENT step edges (``_NON_AGENT_STEPS`` — the org-named edge is
       auto-satisfied at init and ``connection-written`` is a client-only
       trace, so neither counts), and jsonb onboarding_complete=true → True —
       kills the poisoned-false window for orgs completing via the legacy
       wizard during the T2→T7 carve-out. One-directional and self-terminating:
       the FIRST server-observed agent step edge flips control to the node.
    3. Otherwise → False (a node-present org without gate-complete status
       is never re-onboarded via the flag; accept-and-drop makes the jsonb
       writer inert post-W1)."""
    if node_status == STATUS_COMPLETE:
        return True
    agent_steps = [s for s in completed_steps if s not in _NON_AGENT_STEPS]
    return bool(raw_complete and not agent_steps)


# ── graph Cypher fragments ───────────────────────────────────

def onboarding_node_init_fragment(*, fork: str | None = None,
                                  compact: bool = False,
                                  org_named_edge: bool = True) -> str:
    """Eager-init Cypher suffix — byte-identical for every TeamMeta lane
    (register_user ×2, create_org, sdk.org_create, provision_tenant),
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
    if org_named_edge:
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


def eager_init_query(org_meta_cypher: str, org_meta_params: dict[str, Any], *,
                     org_id: str, fork: str | None = None,
                     compact: bool = False) -> tuple[str, dict[str, Any]]:
    """Append the OnboardingState init to a TeamMeta CREATE so both land in
    ONE Cypher query (graph-side atomicity, scope pin 10). Returns the
    combined query + merged params for the lane's ``graph.query`` call."""
    fragment = onboarding_node_init_fragment(fork=fork, compact=compact)
    params: dict[str, Any] = dict(org_meta_params)
    params["org_id"] = org_id
    params.update(_node_init_params(fork=fork, compact=compact))
    return f"{org_meta_cypher}\n{fragment}", params


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
                                 org_named_edge: bool = True) -> None:
    """Idempotent keyed-MERGE init (write-time create-on-write seam).

    Mirrors jsonb ``onboarding_complete`` → status ONE-DIRECTIONALLY at
    creation (never clobbers an existing node's status; never jsonb-false →
    complete).
    """
    status = STATUS_COMPLETE if status_from_mirror is True else STATUS_ACTIVE
    cypher = onboarding_node_init_fragment(
        fork=fork, compact=compact, org_named_edge=org_named_edge)
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


def write_onboards_edge(graph: Any, org_id: str, subject_id: str) -> dict[str, Any]:
    """DM-1 node↔anchor link (W3 seed writes, #1999): set the node's
    org_subject_id property + MERGE the onboards edge → the Organization
    Subject (the seed core creates the Subject first; this writer links).

    Idempotent: replay with the same subject_id re-SETs the same property
    and does not duplicate the edge. Returns the W11 created-signal shape:
    {"created": bool (edge NEW), "subject_id": subject_id}.

    The compact/legacy gates depend on this link (the node tracks its org
    anchor); B1: the anchor is a Subject node, never Object/Statement.
    """
    ensure_onboarding_state_node(graph, org_id)
    with _org_lock(org_id):
        res = _run(graph,
                   f"MERGE (s:Subject {{id: $sid}}) "
                   f"WITH s "
                   f"MERGE (n:{ONBOARDING_NODE_LABEL} {{org_id: $org_id}}) "
                   "SET n.org_subject_id = $sid "
                   "WITH n, s "
                   f"MERGE (n)-[:{ONBOARDS_EDGE}]->(s)",
                   {"org_id": org_id, "sid": subject_id})
    return {"created": _relations_created(res) == 1,
            "subject_id": subject_id}


def write_completed_step(graph: Any, org_id: str, step_id: str, *,
                         status_from_mirror: bool | None = None) -> dict[str, Any]:
    """Idempotent keyed-MERGE step write (FWW). Returns the W11 created-signal:
    ``{"created": bool, "step_id": step_id}`` — True iff the edge was NEW
    (docker lane: bolt:// MERGE stats; embedded lane: honest under the
    per-org lock).

    decide-completed ALSO clears the node's ``last_decide_attempt`` (epic
    I-1/DM-1: success clears to null — a completed decide can never keep a
    stale 'failed'/'dismissed' marker; retry must stay recordable)."""
    if not validate_step_id(step_id):
        raise ValueError(f"unknown onboarding step: {step_id!r}")
    # write-time create-on-write seam first (byte-identical eager init).
    ensure_onboarding_state_node(graph, org_id,
                                 status_from_mirror=status_from_mirror)
    clear_attempt = (
        "WITH n, s "
        "REMOVE n.last_decide_attempt "
        if step_id == "decide-completed" else "WITH n, s ")
    with _org_lock(org_id):
        res = _run(graph,
                   f"MERGE (s:{ONBOARDING_STEP_LABEL} "
                   "{org_id: $org_id, step_id: $step_id}) "
                   f"WITH s "
                   f"MERGE (n:{ONBOARDING_NODE_LABEL} {{org_id: $org_id}}) "
                   + clear_attempt
                   + f"MERGE (n)-[:{COMPLETED_STEP_EDGE}]->(s)",
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


def write_fork_unsure_at(graph: Any, org_id: str, at: str, *,
                         compact: bool = False,
                         status_from_mirror: bool | None = None) -> str:
    """#2407 "not sure yet — decide later" record (Data Model 1 encoding).

    Records the moment the fork question was deferred WITHOUT consuming the
    set-once fork value: ``fork`` stays NULL, so the fork card keeps
    rendering as answerable ('ask'). The timestamp is server-stamped ISO
    (the endpoint computes it); a repeat answer LWW re-stamps (never a 409).
    Returns:
      'recorded' — fork still unset and the org is not compact →
        node.fork_unsure_at = $at. (A compact org is never asked the fork
        card; its fork card does not render — recording unsure is a
        contradictory client signal.)
      'conflict' — fork already set (the org already answered) or the org
        is compact → nothing recorded.
    """
    if not isinstance(at, str) or not at:
        raise ValueError("fork_unsure_at must be a non-empty timestamp")
    status = STATUS_COMPLETE if status_from_mirror is True else STATUS_ACTIVE
    params = {"org_id": org_id, "at": at, "os_status": status}
    params.update(_node_init_params(compact=compact))
    with _org_lock(org_id):
        res = _run(graph,
                   f"MERGE (n:{ONBOARDING_NODE_LABEL} {{org_id: $org_id}}) "
                   "ON CREATE SET n.status = $os_status, n.version = 1, "
                   "n.member_progress = $os_member_progress "
                   "WITH n, CASE WHEN n.fork IS NULL "
                   "AND NOT coalesce(n.compact, false) THEN 'recorded' "
                   "ELSE 'conflict' END AS outcome "
                   "SET n.fork_unsure_at = CASE WHEN n.fork IS NULL "
                   "AND NOT coalesce(n.compact, false) THEN $at "
                   "ELSE n.fork_unsure_at END "
                   f"MERGE (s_tn:{ONBOARDING_STEP_LABEL} "
                   "{org_id: $org_id, step_id: 'team-named'}) "
                   f"MERGE (n)-[:{COMPLETED_STEP_EDGE}]->(s_tn) "
                   "RETURN outcome",
                   params)
    return res.result_set[0][0]


def clear_fork_unsure_at(graph: Any, org_id: str) -> None:
    """Remove the node's fork_unsure_at marker (#2407 invariant: the marker
    is meaningful only while fork IS NULL — the checkpoint clears it the
    moment a later self/build answer consumes the set-once fork)."""
    with _org_lock(org_id):
        _run(graph,
             f"MATCH (n:{ONBOARDING_NODE_LABEL} {{org_id: $org_id}}) "
             "REMOVE n.fork_unsure_at",
             {"org_id": org_id})


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
    """LWW write with the conditional guard: ANY attempt value ('failed'
    OR 'dismissed') is SKIPPED when the decide-completed edge exists (epic
    DM-1: success clears to null — a completed decide can never re-gain an
    attempt marker; retry reachability lives BEFORE completion, and
    dismissal alone never completes / failed never un-completes).
    Create-on-write seam first (absent-node orgs — grandfathered
    pre-backfill — must not silently no-op)."""
    if value not in (None, "failed", "dismissed"):
        raise ValueError(f"invalid last_decide_attempt: {value!r}")
    ensure_onboarding_state_node(graph, org_id,
                                 status_from_mirror=status_from_mirror)
    with _org_lock(org_id):
        if (value in ("failed", "dismissed")
                and decide_completed_edge_exists(graph, org_id)):
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
    agent_steps = [s for s in steps if s not in _NON_AGENT_STEPS]
    if legacy_complete and not agent_steps:
        write_status(graph, org_id, STATUS_COMPLETE)
        return "complete-grandfathered"
    if completion_gate_satisfied(steps, node.get("fork"),
                                 bool(node.get("compact")),
                                 fork_unsure_at=bool(node.get("fork_unsure_at"))):
        write_status(graph, org_id, STATUS_COMPLETE)
        return "complete-gate"
    return "unchanged"


# ── #3912: false-completion remediation (the audited exceptions) ─────
#
# #3784 made the WRITER fail-closed: a `decide-completed` edge is filed only
# when the server OBSERVED a decision-shaped write. That fix is FORWARD-ONLY.
# An org that received the edge before it — the old
# `mcp_server._maybe_onboarding_auto_complete` filed one on ANY successful
# point write and wrote `status = complete` directly — keeps both facts
# forever: `COMPLETED_STEP` edges are first-write-wins (never removed) and
# `status` is monotonic (complete never regresses). This section is the
# remediation path, and the ONLY place in the state machine where a
# completion fact may be REMOVED or the server-owned status may move
# BACKWARDS.
#
# It is EVIDENCE-GATED and FAIL-CLOSED: a repair requires positive proof that
# the org never evidenced a decision. Everything that could evidence one is
# treated as "a decision may have happened", so a true completion is never
# touched — and an unreadable graph is reported as unconfirmable, never
# declared decision-free.
#
# ⚠️ WHAT THE EVIDENCE TEST CAN AND CANNOT PROVE. It proves *no decision
# evidence SURVIVES in this graph*. It cannot prove a decision was never made:
# `tortoise_delete_point` can have removed the only decision Point, and
# `COMPLETED_STEP` edges carry no timestamp, so the two are indistinguishable
# after the fact. A false positive is therefore possible in exactly that case,
# and it is why the gate is deliberately generous (any option/criterion/
# evidence/humanApproval Point, any `*:decision` Event). Operators running
# `--apply` on a store where decision Points are deleted should treat that as
# the known residual.

# pointKinds that evidence a decision having been made. The decide protocols
# ship TWO shapes (#3916): `tortoise_file_decision` / `onboarding/SKILL.md`
# §5 write `decision` (+ option/evidence); `skills/tortoise-decide` runs an EP
# option → criterion → evidence set with NO `decision` point. This set is
# deliberately GENEROUS over both — every member means "do not repair".
# `pack_registry.DECISION_POINT_KINDS` is NOT reused: it is a storage-ROUTING
# set (#3916) and would drag in plan/vision/strategy/goal.
DECISION_EVIDENCE_POINT_KINDS: frozenset[str] = frozenset({
    "decision", "humanApproval",   # explicit decision points
    "option", "criterion",         # the EP decide protocols (#3916)
    "evidence",                    # both protocols' supporting findings
})

# Event kinds that evidence a decision having been made — the audit stream
# carries decision events independently of the Point set (`team_7a3b…` holds
# 155 of them with one decision Point). Matched on the LOCAL name: the
# extractor vocabulary is namespaced (`core:decision`, extractor_v2.py) and
# the prefix is only stripped on some writers, so an exact `== "decision"`
# test would miss namespaced decision events (fail-closed, but narrower than
# the safety net it claims to be).
DECISION_EVIDENCE_EVENT_KINDS: frozenset[str] = frozenset({"decision"})


def _event_kind_is_decision(kind: str) -> bool:
    return kind.rsplit(":", 1)[-1] in DECISION_EVIDENCE_EVENT_KINDS


REPAIR_REASON_FALSE_DECIDE = "false-decide-completed (#3912)"


def _utcnow_iso() -> str:
    from datetime import datetime
    return datetime.now(UTC).isoformat()


def decision_evidence(graph: Any, org_id: str) -> dict[str, Any]:
    """Positive evidence that this graph observed a decision (read-only).

    Deliberately over-inclusive (see DECISION_EVIDENCE_POINT_KINDS) so the
    falseness gate can never repair a completion a decision may have earned.
    Raises on a graph error — callers must treat that as UNCONFIRMABLE, not
    as decision-free (fail-closed)."""
    point_kinds: dict[str, int] = {}
    res = _run(graph, "MATCH (p:Point) RETURN p.pointKind, count(*)")
    for kind, count in res.result_set:
        if kind is not None:
            point_kinds[kind] = count
    event_kinds: dict[str, int] = {}
    res = _run(graph, "MATCH (e:Event) RETURN e.eventKind, count(*)")
    for kind, count in res.result_set:
        if kind is not None:
            event_kinds[kind] = count
    decision_points = sum(
        c for k, c in point_kinds.items() if k in DECISION_EVIDENCE_POINT_KINDS)
    decision_events = sum(
        c for k, c in event_kinds.items() if _event_kind_is_decision(k))
    return {
        "point_kinds": point_kinds,
        "event_kinds": event_kinds,
        "decision_points": decision_points,
        "decision_events": decision_events,
        "decision_evidenced": bool(decision_points or decision_events),
    }


def find_false_decide_completion(graph: Any, org_id: str,
                                 *, legacy_complete: bool | None = None,
                                 ) -> dict[str, Any]:
    """The #3912 falseness test for ONE graph (read-only, never writes).

    A `decide-completed` edge is FALSE when the graph holds it and holds no
    decision evidence at all. Verdicts:

    - ``absent``    — no OnboardingState node (nothing to repair)
    - ``no-edge``   — the node has no decide edge (nothing to repair)
    - ``true``      — the edge is backed by decision evidence: NEVER touch
    - ``false``     — the completion is not earned: the edge stands alone
                      with no decision behind it, OR a previous repair removed
                      the edge (its stamp is set) and neither the fork-aware
                      gate nor the grandfathered branch grants the status
                      (``half_repaired``)
    - ``unconfirmable`` — no edge, but a `status: complete` that the gate and
                      the grandfathered branch both deny and NO repair stamp
                      to attribute it to. Never silently repaired, and never
                      silently GREEN (the guard exits red) — a lost edge the
                      repair did not cause is a human decision

    The half-repaired branch is what makes the repair RETRY-SAFE: without it a
    crash between the edge removal and the status regression would leave the
    org served complete (status-driven) with no edge left to point at, and the
    guard would report GREEN over it. The removal stamp is written BEFORE the
    delete (write-ahead), so our own interrupted repair is always stamp-bearing.

    ``legacy_complete`` is the jsonb mirror when the caller can read it
    (``None`` = unknown = fail-closed); it takes part in the falseness test so
    the guard and the repair CONVERGE — a node the repair deliberately left
    complete because the grandfathered branch covers it is not re-reported as a
    repair target forever.
    """
    node = read_onboarding_node(graph, org_id)
    if node is None:
        return {"org_id": org_id, "node_present": False, "verdict": "absent",
                "false": False, "has_decide_edge": False, "half_repaired": False,
                "status": None, "completed_steps": [], "evidence": None}
    steps = completed_steps(graph, org_id)
    has_edge = "decide-completed" in steps
    half_repaired = False
    if has_edge:
        evidence = decision_evidence(graph, org_id)
        verdict = "true" if evidence["decision_evidenced"] else "false"
    else:
        # Decision evidence is consulted FIRST here too: an org that really
        # decided but lost the edge is NOT a repair target and must not sit
        # RED forever (its own evidence proves the completion). NOTE: the
        # evidence set is deliberately GENEROUS (see
        # DECISION_EVIDENCE_POINT_KINDS), so an incidental `evidence`/`option`
        # Point that is not itself a decision also clears the guard here. That
        # is the fail-closed direction for a READ-only signal (we would rather
        # not accuse than accuse wrongly); the price is that such an org is
        # silently GREEN rather than RED, and it is the deliberate trade.
        evidence = decision_evidence(graph, org_id)
        # A `status: complete` that neither the fork-aware gate nor the
        # grandfathered branch grants is not a completion. The stamp is what
        # ATTRIBUTES it: write-ahead ordering means our own interrupted repair
        # always has one, so a stamped node is ours to finish — an unstamped
        # one is not (a lost edge we did not cause is a human decision).
        false_completion = bool(
            not evidence["decision_evidenced"]
            and node.get("status") == STATUS_COMPLETE
            and not _compact_unknown(node, steps)
            and not _gate_satisfied(node, steps)
            and not _legacy_grandfathered(legacy_complete, steps))
        half_repaired = bool(false_completion
                             and node.get("decide_completed_removed_at"))
        if half_repaired:
            verdict = "false"
        elif false_completion:
            verdict = "unconfirmable"
        else:
            verdict = "no-edge"
    return {
        "org_id": org_id,
        "node_present": True,
        "verdict": verdict,
        "false": verdict == "false",
        "has_decide_edge": has_edge,
        "half_repaired": half_repaired,
        "status": node.get("status"),
        "fork": node.get("fork"),
        "completed_steps": sorted(steps),
        "evidence": evidence,
    }


def _gate_satisfied(node: dict[str, Any], steps: list[str]) -> bool:
    """The fork-aware completion gate for an already-read node."""
    return completion_gate_satisfied(
        steps, node.get("fork"), bool(node.get("compact")),
        fork_unsure_at=bool(node.get("fork_unsure_at")))


def _compact_unknown(node: dict[str, Any], steps: list[str]) -> bool:
    """True when a MISSING `compact` property could explain the completion.

    The wire gate reads a missing `compact` as False (non-compact), which is
    the safe READ default — but the repair must not turn that read default into
    a REGRESSION: two create-on-write seams (`write_fork`,
    `write_fork_unsure_at`) create the node without it, so a genuinely compact
    org reached through one of them would be read as non-compact and have its
    legitimate completion regressed.

    The suppression is valid ONLY when the COMPACT gate is actually satisfied —
    that is the completion a missing flag could be hiding. When the compact
    gate is unsatisfied too, an absent flag explains nothing, so the node must
    not be excused (it falls through to `unconfirmable` / RED rather than
    silently GREEN).
    """
    if "compact" in node and node.get("compact") is not None:
        return False
    return completion_gate_satisfied(
        steps, node.get("fork"), True,
        fork_unsure_at=bool(node.get("fork_unsure_at")))


def _prune_orphan_decide_step(graph: Any, org_id: str) -> None:
    """Drop the `decide-completed` OnboardingStep node when nothing refers to
    it. Idempotent, and called on the repair RETRY as well as the removal — a
    crash between the edge DELETE and the prune would otherwise leave the node
    behind forever."""
    _run(graph,
         f"MATCH (s:{ONBOARDING_STEP_LABEL} "
         "{org_id: $org_id, step_id: 'decide-completed'}) "
         f"OPTIONAL MATCH (x)-[e:{COMPLETED_STEP_EDGE}]->(s) "
         "WITH s, count(e) AS refs WHERE refs = 0 DELETE s",
         {"org_id": org_id})


def _legacy_grandfathered(legacy_complete: bool | None,
                          steps: list[str]) -> bool:
    """`resolve_wire_completion`'s grandfathered branch, mirrored read-only.

    THAT branch is a second, independent way an org can be legitimately
    complete without a decision: node present, status != complete, ZERO AGENT
    step edges, and the legacy jsonb flag true. It lives in jsonb, which this
    graph-only module cannot read — so ``None`` means UNKNOWN and is treated
    as *possibly complete*— the fail-closed direction. Unknown is never the
    same as false: we would rather leave a status alone than regress a
    completion the wire still grants.
    """
    agent_steps = [s for s in steps if s not in _NON_AGENT_STEPS]
    return (legacy_complete is not False) and not agent_steps


def remove_decide_completed_edge(graph: Any, org_id: str, *,
                                 reason: str, at: str | None = None) -> bool:
    """#3912 AUDITED EXCEPTION — delete an unearned `decide-completed` edge.

    `COMPLETED_STEP` edges are first-write-wins: the normal state machine has
    NO removal path, by design. This is its single sanctioned use. It also
    prunes the OnboardingStep node when the edge was its last referrer, and
    stamps the removal on the OnboardingState node so the repair is
    INSPECTABLE (a later reader can tell a repaired org from one that simply
    never reached the step).

    Only `decide-completed` is removable — the #3912 defect — never an
    arbitrary step. Returns True when an edge was actually deleted.

    ORDER MATTERS: the removal stamp is written BEFORE the edge is deleted.
    Every `_run` is a separate autocommitted round trip, so the stamp is the
    write-ahead intent — if the process dies after the delete, the stamp is
    already there and (a) ``find_false_decide_completion`` still sees the node
    as a repair target, and (b) the retry finishes the status regression.
    Stamping last would leave a node with no edge, no stamp and
    ``status: complete`` — invisible to the guard and permanently false.
    """
    at = at or _utcnow_iso()
    with _org_lock(org_id):
        res = _run(graph,
                   f"MATCH (n:{ONBOARDING_NODE_LABEL} {{org_id: $org_id}})"
                   f"-[e:{COMPLETED_STEP_EDGE}]->"
                   f"(s:{ONBOARDING_STEP_LABEL} "
                   "{org_id: $org_id, step_id: 'decide-completed'}) "
                   "RETURN count(e)",
                   {"org_id": org_id})
        removed = bool(res.result_set and res.result_set[0][0])
        if not removed:
            return False
        # Write-ahead intent BEFORE the destructive write.
        _run(graph,
             f"MATCH (n:{ONBOARDING_NODE_LABEL} {{org_id: $org_id}}) "
             "SET n.decide_completed_removed_at = $at, "
             "    n.decide_completed_removed_reason = $reason",
             {"org_id": org_id, "at": at, "reason": reason})
        _run(graph,
             f"MATCH (n:{ONBOARDING_NODE_LABEL} {{org_id: $org_id}})"
             f"-[e:{COMPLETED_STEP_EDGE}]->"
             f"(s:{ONBOARDING_STEP_LABEL} "
             "{org_id: $org_id, step_id: 'decide-completed'}) "
             "DELETE e",
             {"org_id": org_id})
        _prune_orphan_decide_step(graph, org_id)
    return True


def regress_status(graph: Any, org_id: str, *, reason: str,
                   at: str | None = None,
                   to: str = STATUS_ACTIVE) -> bool:
    """#3912 AUDITED EXCEPTION — move a server-owned status BACKWARDS.

    `write_status` is MONOTONIC by design: a grandfathered/first-FLOW-write
    org is never re-onboarded, so `complete` never regresses. A status that
    was earned by a FALSE edge, though, is not a completion at all — leaving
    it stands the served verdict false even after the edge is removed
    (`resolve_wire_completion` returns True on `status == complete` alone).

    This is the single sanctioned regression path. It is reachable ONLY for a
    node a repair has already stamped (``decide_completed_removed_at`` must be
    set), so it cannot be used as a free-standing status reset, and it only
    ever moves `complete → active` (the `WHERE` clause makes a second call a
    no-op). Returns True when the status actually moved.
    """
    if to != STATUS_ACTIVE:
        raise ValueError("regress_status may only target 'active'")
    at = at or _utcnow_iso()
    with _org_lock(org_id):
        res = _run(graph,
                   f"MATCH (n:{ONBOARDING_NODE_LABEL} {{org_id: $org_id}}) "
                   "WHERE n.status = $complete "
                   "  AND n.decide_completed_removed_at IS NOT NULL "
                   "SET n.status = $to, "
                   "    n.status_regressed_at = $at, "
                   "    n.status_regressed_from = $complete, "
                   "    n.status_regressed_reason = $reason "
                   "RETURN n.status",
                   {"org_id": org_id, "to": to, "complete": STATUS_COMPLETE,
                    "at": at, "reason": reason})
        return bool(res.result_set)


def repair_false_decide_completion(graph: Any, org_id: str, *, apply: bool = False,
                                   at: str | None = None,
                                   legacy_complete: bool | None = None,
                                   reason: str = REPAIR_REASON_FALSE_DECIDE,
                                   ) -> dict[str, Any]:
    """#3912 — the ONE remediation entry point for a single org graph.

    Re-runs the falseness test itself and REFUSES anything that is not
    ``false`` (a true completion, an absent node, a missing edge), so a
    caller cannot repair by request. ``apply=False`` (the default) is a
    read-only dry run. Returns the finding + the action taken.

    THE STATUS IS REGRESSED ONLY WHEN THE ORG IS NO LONGER COMPLETE WITHOUT
    THE EDGE. `decide-completed` is required by the SELF gate but NOT by the
    reduced gates — the compact and (post-#3913) build gates are both
    `{harness-connected, first-points-filed}` — and the pre-#3784 writer filed
    the edge on EVERY org, so a legitimately complete compact/build org can
    carry a spurious decide edge. Removing that edge is correct; regressing
    its status is not. After the removal the
    canonical fork-aware gate (and the legacy grandfathered branch — see
    ``_legacy_grandfathered``) is re-evaluated, and the status is left alone
    when either still grants completion. A half-repaired node (edge gone,
    status still complete) is reported ``false`` by
    ``find_false_decide_completion``, so re-running finishes it.

    ``legacy_complete`` is the jsonb mirror (`hosted_api._get_onboarding_state`)
    when the caller can read it; ``None`` = unknown = fail-closed.

    A graph read that raises propagates: an unconfirmable org is never
    reported as repaired.
    """
    finding = find_false_decide_completion(
        graph, org_id, legacy_complete=legacy_complete)
    if finding["verdict"] != "false":
        return {**finding, "action": f"skipped-{finding['verdict']}"}
    if not apply:
        return {**finding, "action": "would-repair"}
    removed = False
    if finding["has_decide_edge"]:
        removed = remove_decide_completed_edge(graph, org_id, reason=reason,
                                               at=at)
    node_after = read_onboarding_node(graph, org_id)
    steps_after = completed_steps(graph, org_id) if node_after else []
    _prune_orphan_decide_step(graph, org_id)  # finish an interrupted prune
    gate_ok = node_after is not None and _gate_satisfied(node_after, steps_after)
    still_complete = bool(
        gate_ok or _legacy_grandfathered(legacy_complete, steps_after)
        or (node_after is not None
            and _compact_unknown(node_after, steps_after)))
    regressed = False if still_complete else regress_status(
        graph, org_id, reason=reason, at=at)
    return {**finding, "action": "repaired", "edge_removed": removed,
            "status_regressed": regressed,
            "still_complete_without_the_edge": still_complete}
