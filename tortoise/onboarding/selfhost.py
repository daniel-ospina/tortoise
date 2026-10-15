"""Self-hosted onboarding path (#2007, W12).

The SELF-HOSTED sibling of the hosted onboarding lanes (``hosted_api``'s
``/v1/onboarding/*`` routes + ``mcp_server``'s ``tortoise_onboarding_*``
tools). It ships the three acts the epic plan's J7 / DE2E-10 require, WITHOUT
the Supabase control plane (self-hosted has no control plane at all):

1. **OnboardingState node init** — ``ensure_selfhost_onboarding`` is the ONE
   owned write point (``fork='self'`` default, ``compact=False``, idempotent
   no-op when the node is present). It rides the selfhost SDK/API init seams
   (``selfhost_api._sdk`` for the REST surface, ``mcp_auth._get_org_sdk`` for
   the selfhost MCP transport) — call it once per process per target via
   ``ensure_selfhost_onboarding_once``.
2. **Two-prompt seed** — ``seed_selfhost_anchors`` files the Organization
   (Subject/organization) + User (Subject/naturalPerson) anchors linked
   ``memberOf`` from the two prompts the SELF-HOSTED user answers ("what's
   your name?" / "what's your organization called?"). Reuses the W3 seed core
   (``tortoise/onboarding/seed.py``) — same collision rule, same
   never-invented-identity rule, same ontology kinds.
3. **Decide observation** — ``observe_selfhost_write`` mirrors the hosted
   ``mcp_server._maybe_onboarding_auto_complete`` for the selfhost org: a
   successful agent write observes ``harness-connected`` + ``first-points-
   filed``, and a decision-shaped write observes ``decide-completed``; the
   canonical server-owned gate (``state.recompute_completion``) then decides
   completion. No separate ceremony, and no step the write did not observe
   (#3784).

Design pins this module exists to hold (epic #1976 §6 J7/DE2E-10, scope §W12):

- **fork='self' ALWAYS, and the fork card is NEVER surfaced.** ``fork`` is
  written at init, so the read-time fork is never null and no fork card can
  render (R2-2: self-hosted is a DEPLOYMENT MODE, decoupled from the
  build-an-app fork — a self-hosted normal user is on the self-use path).
- **No Supabase, no hosted_api.** Every read and write here goes through
  ``tortoise/onboarding/state.py`` + ``seed.py`` and the caller's graph handle.
  ``tests/test_onboarding_selfhost_w12.py`` pins this twice (behaviourally,
  with the control plane turned into a tripwire, and structurally over this
  file's source).
- **No invite / settings / telemetry surfaces** — self-hosted has none, so
  this path never reaches for one.

Known residual (inherited, not introduced here): the W3 seed core keys an
anchor by NAME (``MERGE ... {name}``), so a later seed with a differently
spelled organization or person name files a SECOND anchor and re-points the
node's ``onboards`` edge. That is W3's documented name-keyed semantics (the
same on the hosted lane — a typo'd name fragments the anchor, #1999 §DM-3),
not something the self-hosted lane is allowed to silently change; the agent
asks for confirmation before filing (the two prompts).

``team-named`` is written by the SEED, not by the init: on self-hosted the
organization is named by prompt #2 (``ensure_onboarding_state_node(...,
org_named_edge=False)``), whereas every hosted eager-init lane satisfies it at
org-create because the name is REQUIRED there. Writing the step at init would
claim a name the user has not given yet.
"""
from __future__ import annotations

import logging
import threading
from typing import Any

from . import seed as _seed
from . import state as _state

_logger = logging.getLogger(__name__)

#: Steps the self-hosted seed itself observes: the two prompts name the org
#: (``team-named``) and file the org anchor (``first-points-filed``); the
#: harness that answered them demonstrably reached this daemon
#: (``harness-connected``). Self-hosted has no separate harness-install
#: ceremony — R2-2 makes this the whole connect story.
_SEED_OBSERVED_STEPS: tuple[str, ...] = (
    "team-named", "harness-connected", "first-points-filed")

#: Steps ANY successful selfhost agent write observes (the W12 twin of
#: ``mcp_server._maybe_onboarding_auto_complete``'s ``observed`` list).
_WRITE_OBSERVED_STEPS: tuple[str, ...] = (
    "harness-connected", "first-points-filed")


def selfhost_org_id() -> str:
    """The self-host placeholder org id.

    Imported from its ONE home (``tortoise.mcp_auth.SELFHOST_ORG_ID``) rather
    than re-literalled here, so the selfhost REST surface, the selfhost MCP
    transport and this module can never drift onto different org ids. The
    import is function-level to keep this module importable without mcp_auth
    (which imports the SDK)."""
    from tortoise.mcp_auth import SELFHOST_ORG_ID
    return SELFHOST_ORG_ID


def selfhost_local_user_ref() -> str:
    """Stable identity ref for the self-hosted operator's person anchor.

    The seed's collision rule treats a person anchor as "ours" only when a
    stable ref matches (``seed.is_own_subject``), and a ref-less anchor is
    deliberately identity-unprovable → collision. Self-hosted has no JWT user
    id and no email lane (the two prompts are all the anchor data there is),
    so the deployment's own placeholder namespace supplies the ref — the same
    reserved value the node's ``org_id`` uses, distinguished by the ref field
    it rides. Without it, re-running the seed (resume, never restart) would
    raise ``SubjectCollision`` against the Subject the first run filed."""
    return f"{selfhost_org_id()}:local-user"


def _proj(handle: Any) -> Any:
    """The graph handle for an SDK or a raw graph.

    Accepts the selfhost SDK (``._get_proj()``) and bare graph handles, so the
    module stays usable from tests and from any future caller that already
    holds a projection — the same duck-typing ``state.py``/``seed.py`` use
    for their writers."""
    getter = getattr(handle, "_get_proj", None)
    if callable(getter):
        return getter()
    return handle


class _SdkSeedSurface:
    """Duck-typed seed surface over a selfhost SDK (the W12 twin of
    ``hosted_api._OrgSeedSurface``): ``find_subject_by_name`` needs raw
    Cypher (projection), the entity writes need the SDK (SubjectAdded events
    + journal + embedding + #452 name-MERGE)."""

    def __init__(self, sdk: Any):
        self._sdk = sdk

    def query(self, cypher, **params):
        return _proj(self._sdk).query(cypher, **params)

    def create_subject(self, name, subjectKind="other", **props):
        return self._sdk.create_subject(name, subjectKind=subjectKind, **props)

    def create_edge(self, relation, from_id, to_id):
        return self._sdk.create_edge(relation, from_id, to_id)


# ── the ONE owned write point ────────────────────────────────

def ensure_selfhost_onboarding(handle: Any) -> dict[str, Any] | None:
    """Initialize the selfhost OnboardingState node — THE one owned write
    point of the self-hosted onboarding path.

    Idempotent by construction: ``state.ensure_onboarding_state_node`` is a
    keyed ``MERGE ... ON CREATE SET``, so a node that already carries FLOW
    state (fork included) is a no-op — a restart, a second request, or a
    second lane can never clobber it. Always ``fork='self'`` and
    ``compact=False``; ``team-named`` is deliberately NOT written here (see
    the module docstring). Returns the node as read after the write."""
    proj = _proj(handle)
    org_id = selfhost_org_id()
    _state.ensure_onboarding_state_node(
        proj, org_id, fork=_state.FORK_SELF, compact=False,
        org_named_edge=False)
    return _state.read_onboarding_node(proj, org_id)


# Once-per-process-per-target memo for the SDK/API init seams. A target that
# fails is NOT memoized — the next selfhost SDK init retries (the node init is
# best-effort and must never break the request that triggered it). The failure
# WARNING is once per target per process: during a graph outage every selfhost
# tool call retries the init, and a full traceback per call is noise, while the
# retry itself is exactly what must keep happening.
_INITIALIZED: set[str] = set()
_INIT_FAILURE_WARNED: set[str] = set()
_INIT_LOCK = threading.Lock()


def ensure_selfhost_onboarding_once(handle: Any, *, target: str) -> None:
    """Best-effort, memoized wrapper of ``ensure_selfhost_onboarding`` for the
    hot SDK-init seams. Fires at most once per process per ``target`` (the db
    path / URI the SDK is bound to) and never raises: a graph failure must not
    fail an unrelated selfhost request — the node self-heals on the next SDK
    init."""
    with _INIT_LOCK:
        if target in _INITIALIZED:
            return
    try:
        ensure_selfhost_onboarding(handle)
    except Exception:  # graph failure must never break the triggering request
        with _INIT_LOCK:
            first = target not in _INIT_FAILURE_WARNED
            _INIT_FAILURE_WARNED.add(target)
        if first:
            _logger.warning(
                "selfhost onboarding init failed for %s — retries on the next "
                "selfhost SDK init", target, exc_info=True)
        return
    with _INIT_LOCK:
        _INITIALIZED.add(target)


def reset_selfhost_onboarding_init_memo() -> None:
    """Test hook: forget the memoized init targets (a test that re-points the
    selfhost SDK at a fresh graph in the same process must be able to observe
    the init firing again)."""
    with _INIT_LOCK:
        _INITIALIZED.clear()
        _INIT_FAILURE_WARNED.clear()


# ── reads ────────────────────────────────────────────────────

def next_selfhost_step(node: dict[str, Any] | None,
                       steps: Any) -> str | None:
    """The first incomplete self-fork step, or None when the gate is
    satisfied. Self-hosted has no build/compact fork to branch on (fork is
    pinned 'self'), so this is the self-only arm of
    ``hosted_api._next_onboarding_step``."""
    if node is None or node.get("status") == _state.STATUS_COMPLETE:
        return None
    done = set(steps or ())
    for step in ("harness-connected", "first-points-filed", "decide-completed"):
        if step not in done:
            return step
    return None


def read_selfhost_state(handle: Any) -> dict[str, Any]:
    """Onboarding projection for the selfhost org: the FLOW keys plus the
    OPERATIONAL capture-control keys.

    Graph-DOWN → the ``'unavailable'`` markers (200, never fabricated
    defaults — the same contract the hosted projection serves, DE2E-10's
    "graph-down render" negative case). Node-absent → ``flow_defaults()``
    (read-only, never written by a read path).

    #8686: the OPERATIONAL keys are served here so the capture controls are
    OBSERVABLE, not merely persisted. Before that fix the write never landed
    self-hosted, and after it the write landed but this reader — the one the
    MCP tool and ``GET /v1/onboarding/state`` use — still could not show it, so
    a user could set the switch and had no way to see the setting. The value is
    what is STORED: an absent key means the documented default applies, and
    this layer does not invent one (the disclosure renderer owns the wording
    for 'unset'). Graph-down is ``'unavailable'`` for the same reason as the
    FLOW keys — never a fabricated empty dict that reads as 'nothing set'."""
    proj = _proj(handle)
    org_id = selfhost_org_id()
    try:
        node = _state.read_onboarding_node(proj, org_id)
        steps = _state.completed_steps(proj, org_id) if node is not None else []
    except Exception:  # graph down is a VALUE here, not a failure
        _logger.warning("selfhost onboarding state unavailable", exc_info=True)
        return {
            "org_id": org_id,
            **_state.flow_unavailable(),
            "operational": "unavailable",
            "onboarding_complete": "unavailable",
            "restart_pending": "unavailable",
            "next": "unavailable",
            "node": "unavailable",
        }
    if node is None:
        return {
            "org_id": org_id,
            **_state.flow_defaults(),
            "operational": {},
            "onboarding_complete": False,
            "restart_pending": False,
            "next": "harness-connected",
            "node": False,
        }
    done = set(steps)
    return {
        "org_id": org_id,
        "fork": node.get("fork"),
        "status": node.get("status"),
        "version": node.get("version"),
        "compact": bool(node.get("compact")),
        # canonical display order, and never a step outside the vocabulary
        "completed_steps": [s for s in _state.STEP_IDS if s in done],
        "member_progress": _state.parse_member_progress(
            node.get("member_progress")),
        "last_decide_attempt": node.get("last_decide_attempt"),
        "fork_unsure_at": node.get("fork_unsure_at"),
        # the capture controls this deployment persists (#8686) — stored keys
        # only; an absent key is the default, resolved by the caller. Decoded
        # from the node ALREADY IN HAND: a second read would cost a round trip
        # and, being outside the try above, would let a failure between the two
        # reads escape this function instead of becoming 'unavailable' (#8741).
        "operational": _state.operational_from_node(node),
        "onboarding_complete": node.get("status") == _state.STATUS_COMPLETE,
        "restart_pending": _state.restart_pending(steps),
        "next": next_selfhost_step(node, steps),
        "node": True,
    }


# ── the two-prompt seed ──────────────────────────────────────

def _require_prompt(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(
            f"{label} is required (self-hosted onboarding ASKS the user — "
            "never invent an identity)")
    return value.strip()


def seed_selfhost_anchors(sdk: Any, *, org_name: Any,
                          person_name: Any) -> dict[str, Any]:
    """File the two anchor Subjects from the self-hosted user's two answers.

    Prompt 1 — "what's your name?" → ``person_name`` (User/naturalPerson).
    Prompt 2 — "what's your organization called?" → ``org_name``
    (Organization/organization). Both are REQUIRED: self-hosted has no JWT,
    no ``teams.name`` and no email to derive from, so a missing answer is a
    refusal, never a placeholder (DM-3 never-invented-identity).

    Reuses the W3 seed core end-to-end: the collision check runs BEFORE any
    write (a same-name Subject that is not this deployment's anchor raises
    ``SubjectCollision`` and leaves the graph untouched — the caller surfaces
    disambiguation), the anchors are Subject nodes ONLY (B1), and replay is
    idempotent (the stable local-user ref makes a re-run reuse the anchors
    rather than collide with them).

    Then links the node to the org anchor (``onboards`` edge / org_subject_id)
    and records the steps this act observes, and evaluates the server-owned
    fork-aware gate. Returns the same envelope shape the hosted W3 runner
    returns: ``status`` (``seeded`` | ``collision``), the anchor report, the
    step write signals, ``next`` and the FLOW projection."""
    org_name = _require_prompt(org_name, "org_name")
    person_name = _require_prompt(person_name, "person_name")
    proj = _proj(sdk)
    org_id = selfhost_org_id()
    # Guarantee fork='self' even when a caller reaches this before the SDK-init
    # seam ran (idempotent no-op when the node exists).
    ensure_selfhost_onboarding(sdk)
    try:
        report = _seed.seed_onboarding_anchors(
            _SdkSeedSurface(sdk),
            org_name=org_name, org_id=org_id,
            person_name=person_name,
            user_id=selfhost_local_user_ref(),
            person_email=None,
            include_person=True,
        )
    except _seed.SubjectCollision as exc:
        # All-or-nothing (the seed classifies EVERY anchor before writing):
        # zero writes for this call, retry-safe, caller asks for a
        # disambiguated name.
        return {
            "status": "collision",
            "collisions": [{
                "kind": exc.kind, "name": exc.name,
                "existing_id": exc.existing_id, "reason": exc.reason,
                "existing_refs": exc.refs,
            }],
            "org_name": org_name,
            "person_name_source": "provided",
        }
    onboards = _state.write_onboards_edge(
        proj, org_id, report["org_subject"]["id"])
    steps = {step: _state.write_completed_step(proj, org_id, step)
             for step in _SEED_OBSERVED_STEPS}
    # Server-owned status → the canonical fork-aware gate decides (a no-op on
    # an already-complete node; monotonic).
    _state.recompute_completion(proj, org_id, None)
    state = read_selfhost_state(sdk)
    return {
        "status": "seeded",
        "org_subject": report["org_subject"],
        "user_subject": report["user_subject"],
        "member_of": report["member_of"],
        "org_created": report["org_created"],
        "person_created": report["person_created"],
        "org_kind_normalized": report["org_kind_normalized"],
        "person_kind_normalized": report["person_kind_normalized"],
        "onboards": onboards,
        "steps": steps,
        "next": state["next"],
        "onboarding": state,
    }


# ── decide observation (the write path) ──────────────────────

def observe_selfhost_write(*, decision_observed: bool = False,
                           sdk: Any = None) -> None:
    """Record the onboarding facts a successful selfhost agent write is
    evidence for, then let the canonical gate decide completion.

    The selfhost twin of ``mcp_server._maybe_onboarding_auto_complete`` —
    same observation rule (#3784): any successful write observes
    ``harness-connected`` + ``first-points-filed``; a decision-shaped write
    (``decision``/``option`` pointKind, #3916) additionally observes
    ``decide-completed``. FWW edges make replay a no-op.

    FAIL-OPEN: a graph/control error here must never block the agent write
    that triggered it — the next write re-triggers the check. Never claims a
    decision it did not observe (``decision_observed`` defaults False)."""
    try:
        if sdk is None:
            from tortoise.mcp_auth import _get_org_sdk
            sdk = _get_org_sdk()
        proj = _proj(sdk)
        org_id = selfhost_org_id()
        node = _state.read_onboarding_node(proj, org_id)
        if node is not None and node.get("status") == _state.STATUS_COMPLETE:
            return  # already complete — nothing left to observe
        if node is None:
            # The node is ABSENT and the step writes below create it on write.
            # Go through the same seam the seed uses first, so fork='self' is
            # pinned: a create-on-write birth leaves `fork` unset, and because
            # `fork` is set-once (`ON CREATE SET`) a later successful init
            # cannot repair it — the node would stay forked-less forever and
            # the fork card would render on the mode that must never show it
            # (#2007). Idempotent no-op when the node exists.
            ensure_selfhost_onboarding(sdk)
        observed = list(_WRITE_OBSERVED_STEPS)
        if decision_observed:
            observed.append("decide-completed")
        for step in observed:
            _state.write_completed_step(proj, org_id, step)
        _state.recompute_completion(proj, org_id, None)
    except Exception:  # fail-open by design: never block the agent's write
        _logger.warning(
            "selfhost onboarding observation failed — the next write "
            "re-triggers it", exc_info=True)
        return
