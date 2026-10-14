"""Shared live-only status predicate for EP factor extraction (#780, #2422).

Draft Points/operators must never contribute to live posteriors: extraction
(Phase 2) writes Points as ``status: draft``, and without a filter at
factor-extraction time EP would propagate drafts into live posteriors
(nuclear-risk mitigation R1/R7/R8, insight-mining epic §4.5).

Terminal Points (``retracted`` / ``superseded`` / ``archived`` / ``outdated``
status, the legacy ``deprecated`` status, or the legacy ``outdated=true`` flag
that ``invalidate_point`` writes without touching status) must NEVER
contribute — a dead claim's ghost must not vote (eval-spec P6.3; ontology §5
terminal states; #2422). The canonical set is ``TERMINAL_EXCLUDED_STATUSES``
(``TERMINAL_STATUS_VALUES | {"deprecated"}``). The terminal exclusion is
UNCONDITIONAL: ``include_draft=True`` re-includes DRAFTS only, never terminal
points.

The predicate is shared across ALL FOUR call sites so ``include_draft=True``
re-includes drafts identically everywhere:

- ``TortoiseEP._affected_claims`` / ``TortoiseEP._affected_factors`` (ep.py)
- ``extract_svbp_factors`` (projection/__init__.py — graph-wide SVBP path)
- ``_bfs_select_operators`` (analyze.py)
- ``_select_subgraph`` (sdk.py)
"""

from __future__ import annotations

# ══════════════════════════════════════════════════════════════════════════
# #2901 — THE canonical Point-status partition. ONE declaration, imported by
# every reader that must skip non-current Points.
#
# Do NOT re-declare a status set at a call site. A hand-written subset is how
# ``outdated`` was omitted from three reader filters (#2901: github_indexer,
# audit_beta_gate, 1714_dedup_observation) and a superseded/outdated claim was
# then served as the CURRENT statement. Import ``TERMINAL_EXCLUDED_STATUSES``
# from HERE instead.
#
# The partition has TWO canonical halves, and BOTH are imported, never
# re-spelled: the terminal DENY-list (``TERMINAL_EXCLUDED_STATUSES`` /
# ``_terminal_excluded``) and the CURRENT allow-list
# (``CURRENT_POINT_STATUS_VALUES`` / ``_current_status_clause``). A hand-written
# ``IN ['draft', 'live']`` at a call site is the allow-list's mirror of the same
# defect — a status added to the vocabulary is silently DROPPED — and three
# such literals lived in ``tortoise/sdk.py`` until #4058.
#
# live.py is the LEAF status module: ``tortoise/sdk.py`` imports it (sdk.py:
# ``from .live import TERMINAL_EXCLUDED_STATUSES``), so the module can be
# imported by every consumer without a cycle. Because it is below sdk.py it
# cannot import the full vocabulary (``POINT_STATUS_VALUES``, canonical in
# ``tortoise/sdk.py``) at import time. The partition below is therefore held
# to that vocabulary by ``tests/test_terminal_status_vocabulary.py``, which
# DERIVES the expected terminal set as
# ``POINT_STATUS_VALUES - CURRENT_POINT_STATUS_VALUES`` and asserts equality —
# so adding a status to the vocabulary without classifying it here REDs that
# test instead of silently defaulting to "current" on every read surface.
# (A parity test, not import-time coercion, is the guard: the alternative —
# declaring POINT_STATUS_VALUES here — would need sdk.py to stop declaring it,
# and a circular import makes the reverse direction impossible.)
# ══════════════════════════════════════════════════════════════════════════

#: The staged (not-yet-published) status, as ONE literal. A draft is CURRENT
#: (not-yet-published, not dead) but EP-INERT: every factor-extraction call site
#: excludes it via ``_live_only``, so EP never measures a draft and no posterior
#: is ever computed for one. Read surfaces that must RENDER a draft (label it,
#: demote it below live, refuse to show a belief number) import THIS rather than
#: re-spelling ``'draft'`` at the call site (#2598) — the same "one literal, no
#: per-surface re-declaration" rule ``TERMINAL_EXCLUDED_STATUSES`` above is under.
DRAFT_STATUS = "draft"

#: The non-terminal members of the Point vocabulary — a Point in one of these
#: is CURRENT (draft is not-yet-published, not dead).
CURRENT_POINT_STATUS_VALUES = frozenset({DRAFT_STATUS, "live"})

#: The vocabulary's terminal members (ontology §5: retracted/superseded are
#: terminal; ``outdated`` is the legacy supersede/invalidate status;
#: ``archived`` is reserved — no v1 write path).
TERMINAL_STATUS_VALUES = frozenset(
    {"retracted", "superseded", "outdated", "archived"})

#: ``deprecated`` is deliberately NOT in ``POINT_STATUS_VALUES``: no SDK/API
#: write path emits it (only direct graph writes / legacy assessment paths
#: ever did — see tests/test_lifecycle_guards.py, which asserts its absence),
#: so it is not a legal create-time status. It IS present in legacy graphs and
#: must never be served as current, so it joins the EXCLUSION set below but
#: not ``TERMINAL_STATUS_VALUES``. #2901 inverse-shape ruling: the vocabulary
#: is right (no writer) and the recall_state test that sets ``n.status =
#: 'deprecated'`` directly is a legitimate simulation of legacy graph data.
LEGACY_NON_CURRENT_STATUS_VALUES = frozenset({"deprecated"})

#: Every status a read surface (FTS/vector/structural, EP factor extraction,
#: recall_state, the SDK query paths, the indexers) must treat as NOT current.
#: ``None``/absent status is separately LIVE (legacy nodes — see
#: ``_terminal_excluded``'s NULL handling).
TERMINAL_EXCLUDED_STATUSES = (
    TERMINAL_STATUS_VALUES | LEGACY_NON_CURRENT_STATUS_VALUES)


def _terminal_excluded(clause: str, excluded=None, *,
                       include_outdated_flag: bool = True) -> str:
    """Cypher predicate: the node's status is NOT terminal AND its legacy
    ``outdated`` flag is not true.

    ``clause`` is an alias-qualified status reference (``"n.status"``); the
    flag lives on the same alias (``"n.outdated"``). ``outdated=true`` is a
    second, flag-based dead marker (``invalidate_point`` sets the flag and
    leaves status untouched) so both must be excluded. Legacy nodes without a
    stored status are LIVE (the entity write path defaults
    ``coalesce($st, n.status, 'live')``), hence the NULL check.

    ``excluded`` — the vocabulary to exclude. Default ``None`` = the canonical
    ``TERMINAL_EXCLUDED_STATUSES`` (the POINT vocabulary), so the default call
    is byte-identical to the pre-#3301 composition and every existing caller
    is unaffected. The OBJECT read surfaces (#3301) pass
    ``commit_ops.OBJECT_TERMINAL_STATUSES`` — a different family with no
    ``outdated`` member — and must pass ``include_outdated_flag=False``: no
    Object writer sets ``outdated``, so ANDing that Point conjunct would hide
    an Object nobody can mark, and the two families' vocabularies would be
    conflated in one predicate.
    """
    if excluded is None:
        excluded = TERMINAL_EXCLUDED_STATUSES
    alias = clause.split(".", 1)[0] if "." in clause else clause
    flag = f"{alias}.outdated"
    chain = " AND ".join(
        f"{clause} <> '{s}'" for s in sorted(excluded))
    status_part = (f"({clause} IS NULL OR ({chain}))"
                   if chain else "true")
    if not include_outdated_flag:
        return status_part
    return f"({status_part} AND coalesce({flag}, false) = false)"


# #2490 (terminal posterior freeze): a terminalized claim's posterior pins at
# its pre-terminal value (the 0.904 repro) unless the terminalizing WRITER
# neutralizes it — decay to vacuity atomically with the status/flag write.
# ep_alpha/ep_beta are deliberately NOT decayed: they are the persisted prior
# history and the SOLE recovery vector should a terminal state ever be reversed
# (no unsupersede path exists today). Decay is UNIFORM across #2421 Case-1
# restatement and Case-2 correction (the old claim is terminal either way; the
# successor recomputes independently). Defined HERE (live.py is a leaf — sdk.py
# imports live.py and projection/entities.py can import it without a cycle via
# sdk.py:31's `from .projection import`).

#: The belief half of a terminalizing write, as DATA. ``decay_clause`` renders
#: it into Cypher and the in-memory fold (``projection._apply_one``) writes it
#: into its ``{id: point}`` index, so those two representations of the SAME
#: write cannot drift apart. They did: the pure fold's ``PointRetracted`` arm
#: tombstoned without decaying, so ``fold()`` kept the pre-retract belief while
#: ``rebuild_all`` held the vacuous one — a divergence on EVERY retract
#: (#4542).
#:
#: ⚠️ This is NOT yet the only declaration, and nothing here should be read as
#: claiming it is. TWO hand-declared copies of the same triple remain besides
#: this one: ``tortoise/consistency.py``'s ``_DECAY`` (the JOURNAL side of the
#: divergence detector that measures this very invariant; pinned to this
#: declaration by ``tests/test_4542_retract_fold_decay.py``) and the
#: ``assess_source`` sweep's ``ConfidenceChanged`` payload in ``tortoise/sdk.py``
#: (whose values ``test_2884_ep_state_journaled.py``'s
#: ``test_f3_assess_source_decay_is_journaled`` holds to the contract, though not
#: to this declaration). Read the values from here; do not add a third.
VACUITY_BELIEF: dict[str, float] = {
    "confidence": 0.5,
    "posterior_alpha": 1.0,
    "posterior_beta": 1.0,
}


def decay_clause(alias: str) -> str:
    """Cypher SET fragment decaying a terminalizing claim to vacuity.

    Renders ``VACUITY_BELIEF`` as ``{alias}.confidence=0.5,
    {alias}.posterior_alpha=1.0, {alias}.posterior_beta=1.0`` for a SET clause
    — crash-atomic with the
    status/flag write it rides (single statement). ``alias`` is the node
    variable (``"n"`` for retract/supersede/invalidate/folds, ``"p"`` for
    assess_source's older-assessment SET). Reading a decayed claim back:
    coalesce(posterior_alpha, ep_alpha, 1.0) = 1.0 and confidence = 0.5 —
    the vacuous Beta(1,1) posterior mean.
    """
    return ", ".join(f"{alias}.{k}={v}" for k, v in VACUITY_BELIEF.items())


def _terminal_expression(clause: str) -> str:
    """Cypher boolean expression: TRUE when the node is terminal.

    Composes the SAME vocabulary as ``_terminal_excluded`` (status in
    TERMINAL_EXCLUDED_STATUSES OR the legacy ``outdated=true`` flag) but in
    the POSITIVE direction, usable in RETURN projections, CASE conditions and
    WHERE OR-chains.

    It is the EXACT De Morgan dual of ``_terminal_excluded`` — by
    construction, not by hand-written correspondence: the flag half below is
    ``coalesce(flag, false) <> false``, the negation of that predicate's
    ``coalesce(flag, false) = false``, and the status half negates its
    ``(status IS NULL OR status <> '…')`` NULL-guard. ``_alive_flag`` (this
    expression negated) therefore cannot report ALIVE a node the keep-filter
    drops, on any stored value (#7075). Before #7075 the flag half was
    ``= true``, which is NOT ``NOT (… = false)``: FalkorDB compares types
    strictly, so a non-boolean ``outdated`` matched NEITHER comparison and
    such a node was simultaneously "not terminal" here and "not live" in
    ``_terminal_excluded`` — an excluded middle. ``= true`` and ``<> false``
    agree on every boolean and on NULL, so in-contract graphs are unaffected.

    ⚠️ ``outdated`` is contractually a BOOLEAN: it is server-managed (#2491),
    ``_sanitize_props`` refuses it on the props passthrough, and the journal
    fold enforces the type (``_belief_bool_value_ok``). Only a hand-edited or
    pre-#2491 graph can hold a non-boolean. The Python mirror
    ``is_terminal_status`` reads that flag by ``bool()`` truthiness, so it
    agrees with this expression on every boolean, NULL, non-empty string and
    non-zero number. The two can still differ when Python's ``bool()`` is
    falsy while ``coalesce(v, false) <> false`` holds — e.g. numeric
    ``0``/``0.0`` or the empty string ``""``, which are terminal here (and in
    ``_terminal_excluded``) but live in the Python mirror. Out of contract; no
    in-repo writer can produce such a value. This is a stated
    contract, NOT a promise that arbitrary hand-edited data is interpreted
    consistently.
    """
    alias = clause.split(".", 1)[0] if "." in clause else clause
    members = " OR ".join(
        f"{alias}.status = '{s}'" for s in sorted(TERMINAL_EXCLUDED_STATUSES))
    return (f"(({alias}.status IS NOT NULL AND ({members})) "
            f"OR coalesce({alias}.outdated, false) <> false)")


def _alive_flag(clause: str) -> str:
    """Cypher projection boolean: TRUE when the node is NOT terminal.

    CASE-wrapped inverse of ``_terminal_expression`` — bare boolean chains
    can be rejected in RETURN projection positions, so the WHERE-in-CALL
    adapter keeps the shared composition well-formed as an expression
    (``_terminal_expression`` itself is the same composition already wrapped
    in a single parenthesized boolean, safe to negate).
    """
    return f"(CASE WHEN {_terminal_expression(clause)} THEN false ELSE true END)"


def _current_status_clause(clause: str) -> str:
    """Cypher predicate: the node's status is CURRENT (or absent).

    The ALLOW-LIST half of the #2901 partition — the exact counterpart of
    ``_terminal_excluded``'s deny-list — derived from
    ``CURRENT_POINT_STATUS_VALUES``. A status added to the Point vocabulary and
    classified current by the parity test in
    ``tests/test_terminal_status_vocabulary.py`` is admitted here with no second
    edit; a hand-written ``IN ['draft', 'live']`` at a call site silently DROPS
    it (the mirror image of the #2901 defect — three such literals lived in
    ``tortoise/sdk.py`` until #4058).

    An ABSENT status is current (legacy nodes — the same NULL handling as
    ``_terminal_excluded``). The allow-list is deliberately NOT the negation of
    the deny-list: an UNKNOWN status (neither declared current nor terminal) is
    dropped here but admitted by ``NOT _terminal_excluded``, so the two are not
    interchangeable — a caller that wants "not terminal" must call
    ``_terminal_excluded``.
    """
    members = ", ".join(f"'{s}'" for s in sorted(CURRENT_POINT_STATUS_VALUES))
    return f"({clause} IS NULL OR {clause} IN [{members}])"


def is_draft_status(status) -> bool:
    """True when ``status`` is the staged (not-yet-published) status.

    The Python twin of the ``'{DRAFT_STATUS}'`` literal ``_live_only``
    excludes: a reader that must LABEL or DEMOTE a draft (rather than drop it)
    calls this instead of comparing against a re-spelled string.
    """
    return (status or "") == DRAFT_STATUS


def is_terminal_status(status, outdated: bool = False) -> bool:
    """Python mirror of the Cypher terminal predicate (status in the
    terminal vocab OR the legacy ``outdated=true`` flag). NULL/absent status
    is LIVE (the entity write path defaults ``coalesce($st, n.status,
    'live')``) — mirrors ``_terminal_excluded``'s NULL handling.

    ``outdated`` is contractually a BOOLEAN (server-managed since #2491; the
    journal fold enforces the type). This mirror reads it by ``bool()``
    truthiness while ``_terminal_expression`` negates
    ``coalesce(outdated, false) = false``; the two agree on every boolean,
    NULL, non-empty string and non-zero number, and can differ when Python's
    ``bool()`` is falsy while ``coalesce(v, false) <> false`` holds — e.g.
    numeric ``0``/``0.0`` or the empty string ``""`` (live here, terminal in
    Cypher). Out of contract; no in-repo writer can produce such a value
    (#7075)."""
    return (status or "") in TERMINAL_EXCLUDED_STATUSES or bool(outdated)


#: The ONE statement that reads a Point id's lifecycle fan-out. It is run by
#: the write-side guard (``TortoiseSDK._assert_lifecycle_guard``), by the
#: hosted commit-receiver pre-check (``hosted_api._terminal_state`` — which
#: must stay in LOCKSTEP with the guard, #3366), and by the promotion-time
#: temporal-target probe. Kept here, next to the terminal vocabulary it feeds,
#: so a second copy cannot drift (#7822).
LIFECYCLE_FANOUT_QUERY = (
    "MATCH (n:Point {id:$id}) "
    "RETURN n.is_operator, n.status, coalesce(n.outdated, false)"
)


def classify_lifecycle_fanout(rows) -> tuple:
    """Classify EVERY node an id's ``LIFECYCLE_FANOUT_QUERY`` MATCH returned.

    Point ids are NOT unique (the duplicate fan-out is a tested,
    graph-reachable shape: ``create_point(..., id=X)`` twice) and every
    lifecycle writer's stamp is a bare ``MATCH (n:Point {id:$id}) SET …`` that
    binds EVERY matching node. A verdict read off ``rows[0]`` is therefore
    server-row-order dependent (#7822): on an id carrying one dead and one live
    node the same call passed or refused depending on which the server happened
    to return first. The scan is over the WHOLE fan-out:

      - any ``is_operator`` node → ``"operator"`` (outranks terminal, matching
        the pre-#7822 single-row precedence)
      - else any terminal node → ``"terminal"``
      - else every node is a non-terminal statement point → ``"ok"``
      - no rows at all → ``"absent"``

    Returns ``(kind, status, outdated)``. ``status``/``outdated`` are the
    offending node's state for ``"operator"``/``"terminal"`` and the
    representative live state for ``"ok"``. The representative is picked by a
    STABLE key (status text, then flag), never by position: these values reach
    error messages and the MCP preview's ``status.from``, and server row order
    is not a contract.
    """
    rows = [(bool(is_op), status, bool(outdated))
            for is_op, status, outdated in rows]
    if not rows:
        return ("absent", None, False)
    rows.sort(key=lambda r: (str(r[1] or ""), r[2]))
    for _is_op, status, outdated in rows:
        if _is_op:
            return ("operator", status, outdated)
    for _is_op, status, outdated in rows:
        if is_terminal_status(status, outdated):
            return ("terminal", status, outdated)
    return ("ok", rows[0][1], rows[0][2])


def _live_only(clause: str, include_draft: bool = False) -> str:
    """Return a Cypher predicate excluding draft AND terminal nodes.

    Args:
        clause: alias-qualified status reference, e.g. ``"n.status"``.
        include_draft: when True, drafts are re-included (the
            ``run(include_draft=True)`` escape hatch) — terminal nodes are
            NEVER re-included (#2422).

    Legacy nodes without a stored status are LIVE (the entity write path
    defaults ``coalesce($st, n.status, 'live')``), hence the NULL checks.
    """
    terminal = _terminal_excluded(clause)
    if include_draft:
        return terminal
    return (f"(({clause} IS NULL OR {clause} <> '{DRAFT_STATUS}') "
            f"AND {terminal})")


# ══════════════════════════════════════════════════════════════════════════
# #7853 — THE canonical epistemic-label set. Confidence (EP) propagates
# across :Point, :Subject, :Object and :Event (owner ruling, #7813/#7853).
# ONE declaration, imported by every reader that must admit an epistemic
# node — do NOT spell the four labels inline at a call site (the codebase
# already drifted into two notions of "epistemic label" before this landed).
#
# Two shapes, because FalkorDB does NOT accept pattern-position label
# disjunction (`MATCH (a:Point|Event)-…` → ResponseError, measured on
# FalkorDB 6.0.1 and on the embedded engine):
#
#   * TRAVERSAL positions (the node is reached from an anchored operand):
#     drop the node's own label and add ``epistemic_disjunction(alias)`` to
#     the WHERE clause. No index is lost — the traversal is anchored
#     elsewhere (the operator, or the other endpoint).
#
#   * ID-ANCHORED positions (``{id:$x}`` / ``WHERE n.id IN $ids``): keep a
#     label on the node and iterate ``epistemic_label_queries`` ONCE PER
#     LABEL. Dropping the label is the obvious widening and is a straight
#     regression: the indexes are per-(label, property), so a bare
#     ``MATCH (n {id:$id})`` has NO index anchor and full-scans on every EP
#     read (#7853 owner measurement). Point/Subject/Object each carry an
#     ``id`` RANGE index; Event carries one on ``eventId`` only, so an
#     Event addressed by ``id`` is unindexed today under EITHER shape.
# ══════════════════════════════════════════════════════════════════════════

#: The four labels on which confidence propagates (#7813 ruling). Ordered
#: Point-first: the Point query is the hot path and stays the first, indexed
#: lookup in every id-anchored sweep.
EPISTEMIC_LABELS = ("Point", "Subject", "Object", "Event")


def epistemic_disjunction(alias: str) -> str:
    """WHERE fragment: ``alias`` holds one of the epistemic labels.

    For TRAVERSAL positions only (the node is reached from an anchored
    operand). FalkorDB rejects pattern-position label disjunction, so the
    disjunction has to be a predicate. Example:

        MATCH (o:Point {is_operator:true})-[:IMPL|NAND]->(c)
        WHERE {epistemic_disjunction('c')}
    """
    return "(" + " OR ".join(
        f"{alias}:{label}" for label in EPISTEMIC_LABELS) + ")"


def epistemic_label_queries(template: str) -> list[str]:
    """Expand an ID-ANCHORED query template once per epistemic label.

    ``template`` must contain the literal token ``{label}`` in the node
    pattern's label position; only that token is substituted (the rest of
    the Cypher — including ``{id:$id}`` property maps — is untouched, which
    is why this uses ``str.replace`` and not ``str.format``). Keep the label
    on an id-anchored node so the per-(label, property) range index is used.
    """
    return [template.replace("{label}", label) for label in EPISTEMIC_LABELS]


# ══════════════════════════════════════════════════════════════════════════
# #7852 — the Layer-1 direction vocabulary: the model's four values, plus the
# two legacy aliases that remain readable.
#
# ONTOLOGY §8 records direction on the write with FOUR values:
#   ``->``  source → target    (confidence flows forward only)
#   ``<-``  target → source    (confidence flows backward only)
#   ``<->`` both ways
#   ``-``   neither — the connection exists and NO confidence transfers
#
# Before this change the runtime stored only the two legacy tokens
# (``unidirectional`` ≡ ``->``, ``bidirectional`` ≡ ``<->``) and compared them
# by NAME at each call site. The legacy tokens stay accepted (existing callers
# are byte-compatible and existing rows keep the token they were written with
# — there is NO data migration) and every READ boundary normalizes through
# ``direction_flows``, so a ``<-`` edge is never silently read as its opposite
# and a ``-`` edge transfers nothing.
#
# ONE declaration, imported by every reader — the same rule the status
# partition above is under. Do NOT re-spell these sets at a call site.
# ══════════════════════════════════════════════════════════════════════════

#: The model's four Layer-1 direction values (ONTOLOGY §8).
DIRECTION_VALUES = frozenset({"->", "<-", "<->", "-"})

#: The pre-#7852 runtime tokens — still accepted on the write and still read.
LEGACY_DIRECTION_VALUES = frozenset({"unidirectional", "bidirectional"})

#: Every token a write boundary accepts (the four model values + the two legacy
#: aliases). ``create_operator`` / ``create_direct_edge`` / the ingest contract
#: / the ``decide`` CLI all validate against THIS set.
ACCEPTED_DIRECTION_VALUES = DIRECTION_VALUES | LEGACY_DIRECTION_VALUES

#: Legacy token → the model value it means. ``unidirectional`` is source→target
#: (``->``); ``bidirectional`` is both ways (``<->``).
DIRECTION_ALIASES = {"unidirectional": "->", "bidirectional": "<->"}


def canonical_direction(value: str | None) -> str | None:
    """Normalize an accepted direction token to the model's four-value form.

    ``None`` stays ``None`` (absent — the caller canonicalizes per ``op_type``).
    A legacy token maps through ``DIRECTION_ALIASES``; an unrecognised token
    returns ``None`` so the caller decides (never a silent default).
    """
    if value is None:
        return None
    if value in DIRECTION_VALUES:
        return value
    return DIRECTION_ALIASES.get(value)


def direction_flows(value: str | None) -> tuple[bool, bool]:
    """``(source→target, target→source)`` for a stored direction token.

    This is the ONE read-side normalizer: every reader — EP's factor-message
    gating and back-message filters, the analyze BFS, the dedup comparison —
    asks here, so the four model values and the two legacy aliases cannot be
    interpreted differently in two places (the divergence that would read a
    ``<-`` edge as its opposite, or a ``-`` edge as a propagating one).

    ``->``/``unidirectional`` flow forward only; ``<-`` flows backward only;
    ``<->``/``bidirectional`` flow both ways; ``-`` flows neither (the
    connection exists and NO confidence transfers). Absent/unknown defaults
    to both ways — the runtime's historical default for a missing direction.
    """
    canonical = canonical_direction(value)
    if canonical is None:
        return (True, True)
    return (canonical in ("->", "<->"), canonical in ("<-", "<->"))


def is_default_direction(value: str | None) -> bool:
    """True when ``value`` does not DIFFERENTIATE from the mutual default.

    The signature default (``bidirectional``) and its model spelling (``<->``)
    both mean "not declared"; an absent direction does too. Used to decide
    whether a declared Layer-2 ``semantics`` may resolve the direction: the
    meaning outranks a non-declaration, never a value the caller differentiated.
    """
    canonical = canonical_direction(value)
    # `value is None` is the ONE "no direction was supplied" case; any other
    # token the vocabulary does not recognise must NOT read as default —
    # otherwise a caller's typo (`sideways`) would be silently overwritten by a
    # declared `semantics` before validation could refuse it.
    return value is None or canonical == "<->"


def direction_spellings(value: str | None) -> frozenset[str]:
    """Every stored token equivalent to ``value`` (canonical + legacy alias).

    Storage is VERBATIM (no migration), so one meaning may be spelled two ways
    on disk. A reader that compares a direction against stored values as a
    string must compare against the whole equivalence class — otherwise a
    ``->`` operator written after this change is invisible to a legacy-token
    lookup, and the idempotent ingest path mints a silent DUPLICATE.
    """
    canonical = canonical_direction(value)
    if canonical is None:
        canonical = "<->"
    return frozenset(
        {canonical}
        | {legacy for legacy, canon in DIRECTION_ALIASES.items()
           if canon == canonical}
    )
