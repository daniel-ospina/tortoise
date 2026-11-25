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

“Ended” has TWO sources, and the predicate reads BOTH (#13034, design §6 in
``docs/plans/2026-11-15-state-lifecycle-and-pipelines.md``): the LIFECYCLE WORD
(the status vocabulary above, plus the legacy ``outdated`` flag) and the
POSITION AXIS — the durable ``isClosed`` marker, or a stage in the ``terminal``
pipeline slot (``pipeline.py``). Either axis alone is an ending. The position
half is what makes an ending durable: once ``isClosed`` is set, re-resolving
what slot the object's stage plays cannot resurrect it (``pipeline.py:924-943``).
Its two Python/Cypher surfaces are :func:`is_position_ended` and the position
conjuncts of :func:`_terminal_excluded` / :func:`is_terminal_status`.

NO reader re-spells the predicate: ``_live_only`` is the ONE spelling, and it is
called from every factor-extraction, pricing and display site (``ep``, ``sdk``,
``analyze``, ``why``, ``projection``, ``weights`` — the census grows with every
reader, so it is deliberately NOT restated here; this header said "ALL FOUR",
then "ALL FIVE", and each number was stale within a release). The list below
is the factor-extraction family that must agree on ``include_draft``, so
``include_draft=True`` re-includes drafts identically everywhere:

- ``TortoiseEP._affected_claims`` / ``TortoiseEP._affected_factors`` (ep.py)
- ``extract_svbp_factors`` (projection/__init__.py — graph-wide SVBP path)
- ``_bfs_select_operators`` (analyze.py)
- ``_select_subgraph`` (sdk.py)
- ``compute_operator_weight`` (weights.py — the ONE dampener read, #9962: a
  withdrawn or draft mitigation must not price an operator either)
"""

from __future__ import annotations

import contextlib
import json

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
# proves every WRITE word is current-or-hidden and that no ending a writer may
# set is served as current (#11824 S0 split the two sets — design §8); the READ
# set's absolute width is a literal pin in ``tests/test_lifecycle_guards.py``.
# An unclassified status REDs ``tests/test_terminal_status_vocabulary.py`` (not the pin above).
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

#: The READ-side terminal members (ontology §5). ⚠️ The READER's set, not the
#: writer's: `#11824` S0 narrowed the write vocabulary to four words and nothing
#: is backfilled, so a reader must still hide these (design §8).
TERMINAL_STATUS_VALUES = frozenset(
    {"retracted", "superseded", "outdated", "archived"})

#: ``deprecated`` is here because it must be HIDDEN, not because it is
#: unwritable: `#11824` S0 made it a WRITE word too (``lifecycle_vocabulary``),
#: and ``tests/test_lifecycle_guards.py:85-97`` records that reversal — the
#: pre-S0 note here claimed the opposite. ``superseded`` NAMES ITS SUCCESSOR,
#: so a row where nothing replaced the statement needs ``deprecated`` to say
#: "this ended and nothing was put in its place". #2901 inverse-shape ruling
#: stands: the EXCLUSION set below, not ``TERMINAL_STATUS_VALUES`` — the
#: recall_state test setting ``n.status = 'deprecated'`` is legacy data.
LEGACY_NON_CURRENT_STATUS_VALUES = frozenset({"deprecated"})

#: Every status a read surface (FTS/vector/structural, EP factor extraction,
#: recall_state, the SDK query paths, the indexers) must treat as NOT current.
#: ``None``/absent status is separately LIVE (legacy nodes — see
#: ``_terminal_excluded``'s NULL handling).
TERMINAL_EXCLUDED_STATUSES = (
    TERMINAL_STATUS_VALUES | LEGACY_NON_CURRENT_STATUS_VALUES)


#: The pipeline slot whose role is ENDED (design §4/§6). Declared HERE rather
#: than imported, because ``live.py`` is the LEAF: ``pipeline.py`` imports
#: ``entity_identity``, which imports this module, so importing pipeline at
#: import time would close a cycle. `pipeline.PIPELINE_SLOTS` is the authority
#: for the slot vocabulary and ``PipelineStage.is_terminal`` is the authority
#: for this role; `tests/test_13034_currency_rule_reads_position.py` pins the two
#: together, so this literal cannot drift into a slot pipeline does not have.
#: ⛔ Do not read the role from a stage NAME anywhere: a pack may call its
#: terminal stage ``released``/``written-off``/``signed`` (§4.1).
TERMINAL_STAGE_SLOT = "terminal"


def ending_axes(status, outdated: bool = False, *, is_closed=None,
                stage_slot=None) -> tuple[str, ...]:
    """WHICH sources record this thing as ENDED — the ONE declaration of the rule.

    The currency rule has two axes and four markers (§6):

    * the LIFECYCLE WORD — ``status`` in :data:`TERMINAL_EXCLUDED_STATUSES`, or
      the legacy ``outdated`` flag (``invalidate_point`` sets the flag and
      leaves the status untouched);
    * the POSITION AXIS — the durable ``isClosed`` marker, or a stage in the
      ``terminal`` pipeline slot (§4/§6).

    Any ONE of them is an ending. Empty tuple = still current.

    ⛔ WHY A TUPLE AND NOT A BOOL. The two callers of this rule need different
    halves of ONE answer, and they used to get there by re-spelling it: a
    PARTICIPATION decision needs the boolean, and a DIAGNOSTIC needs to say
    which axis ended the record — and the pre-#13450 spelling of the diagnostic
    printed a closed node as ``live`` because it only knew about the word. The
    boolean is therefore DERIVED from the names here
    (:func:`is_terminal_status` / :func:`is_position_ended`), so a predicate and
    its diagnostic cannot disagree by construction. Every marker is reported
    (not first-match-wins): a record ended on two axes must not hide one.

    ``status``/``outdated`` are the word axis, read exactly as they always were
    — ``(status or "")`` so an ABSENT status is LIVE (the legacy-node rule, and
    since #9974 R1 the ordinary birth state of a status-less
    Object/Subject), and ``outdated`` by ``bool()``. ``is_closed`` is read by
    ``bool()`` too, matching how ``advance_stage`` reads the marker
    (``bool(cur.get("isClosed"))``), so a truthy non-boolean legacy value is an
    ending on both reads. A stage the caller did not resolve (``None``) is NOT
    an ending, and neither is a slot in ``entry``/``active``/``suspended``:
    those say how far along a thing is, never whether the commitment still
    stands (§4). ⛔ The slot is compared to the ROLE, never a stage NAME — a pack
    may call its terminal stage ``released``/``written-off``/``signed`` (§4.1).

    ⛔ ``goalState`` is deliberately NOT a fifth marker: it is a legacy arm in
    ``derived_invalidation``, not part of this rule (§6).

    ⛔ THE SLOT IS COMPARED EXACTLY, NEVER STRIPPED (#13450 review, P3). An
    earlier spelling of this rule read ``stage_slot.strip() ==
    TERMINAL_STAGE_SLOT``, which made ``" terminal "`` an ending HERE while
    every Cypher reader (:func:`_terminal_excluded` /
    :func:`_terminal_expression`, ``coalesce(n.pipelineSlot, '') <>
    'terminal'``) served it as CURRENT — the silent-drop direction, and the one
    value that disagreed by construction. The exact comparison is also what
    ``pipeline`` does where it compares a SLOT — ``PipelineStage.is_terminal``
    and its declaration/transition validations, NOT the stage RESOLVER (which
    strips its probe: it resolves by id/label) — so the padded value agrees.

    The CYPHER twin is :func:`_terminal_excluded` (the KEEP direction); it is a
    second REPRESENTATION of this one rule because it has to be, and
    ``tests/test_13034_currency_rule_reads_position.py`` pins the two together
    over every BOOLEAN combination of those inputs rather than leaving agreement
    to inspection — ``" terminal "`` included, which used to fall outside the
    enumeration while the two sides disagreed on it.
    """
    axes: list[str] = []
    if (status or "") in TERMINAL_EXCLUDED_STATUSES:
        axes.append("status")
    if bool(outdated):
        axes.append("outdated")
    if is_closed is not None and bool(is_closed):
        axes.append("isClosed")
    if isinstance(stage_slot, str) and stage_slot == TERMINAL_STAGE_SLOT:
        axes.append("pipelineSlot")
    return tuple(axes)


def is_position_ended(*, is_closed=None, stage_slot=None) -> bool:
    """True when the POSITION axis records this thing as ended.

    The second of the two sources the reader rule answers from (§6): the durable
    ``isClosed`` marker, or a stage in the ``terminal`` slot. Both are ENDINGS
    and either one alone is enough — ``isClosed`` is what makes an ending
    durable (``pipeline.py:924-943``: a pack release that reclassifies the stage
    an object sits in must not resurrect an object the graph records as closed),
    while a terminal slot still ends an object whose marker was never written.

    ``is_closed`` is read by ``bool()``, matching how
    :func:`is_terminal_status` reads the legacy ``outdated`` flag and how
    ``advance_stage`` reads the marker (``bool(cur.get("isClosed"))``): a
    truthy non-boolean legacy value is an ending on every one of those reads.

    A stage the caller did not resolve (``None``) is NOT an ending, and neither
    is a slot in ``entry``/``active``/``suspended``: those say how far along a
    thing is, never whether the commitment still stands (§4).

    ⛔ This answers only the position axis. A caller asking "is this still
    served as current?" asks :func:`is_terminal_status` (or
    :func:`_terminal_excluded` in Cypher), which composes this with the
    lifecycle word — never this alone.

    Derived from :func:`ending_axes` (its two position markers) rather than
    re-testing the marker and the slot here: the rule is declared ONCE, so this
    predicate, :func:`is_terminal_status` and the axes a diagnostic prints
    cannot drift apart.
    """
    return bool(ending_axes(None, False, is_closed=is_closed,
                            stage_slot=stage_slot))


def _terminal_excluded(clause: str, excluded=None, *,
                       include_outdated_flag: bool = True) -> str:
    """Cypher predicate: the node is NOT ended on EITHER axis — its status is
    not terminal, its legacy ``outdated`` flag is not true, and its position
    axis (``isClosed`` / a ``terminal`` pipeline slot) does not record an end.

    ``clause`` is an alias-qualified status reference (``"n.status"``); the
    flag lives on the same alias (``"n.outdated"``). ``outdated=true`` is a
    second, flag-based dead marker (``invalidate_point`` sets the flag and
    leaves status untouched) so both must be excluded. An ABSENT status is LIVE
    (the legacy-node rule, and since #9974 R1 also the ordinary birth state: the
    entity fold seeds ``status=$st`` — derived-or-absent, never a fabricated
    ``'live'``), hence the NULL check.

    ``excluded`` — the vocabulary to exclude. Default ``None`` = the canonical
    ``TERMINAL_EXCLUDED_STATUSES`` (the POINT vocabulary), so the default call
    is byte-identical to the pre-#3301 composition and every existing caller
    is unaffected. The OBJECT read surfaces (#3301) pass
    ``commit_ops.OBJECT_TERMINAL_STATUSES`` — a different family with no
    ``outdated`` member — and must pass ``include_outdated_flag=False``: no
    Object writer sets ``outdated``, so ANDing that Point conjunct would hide
    an Object nobody can mark, and the two families' vocabularies would be
    conflated in one predicate.

    ⛔ THE POSITION AXIS IS NOT PART OF THAT FAMILY SEAM, and must not be made
    one (#13034, design §6). The family seam chooses WHICH STATUS WORDS end a
    thing; ``isClosed`` and the pipeline slot are a different SOURCE of the
    same answer, and the reader rule reads BOTH regardless of family — an
    ``:Object`` ended by ``isClosed`` is exactly the defect this arm closes. A
    reader may therefore pick its vocabulary and its flag conjunct; it may not
    opt out of the position axis, which is why there is no
    ``include_position_axis`` switch to wire by mistake.

    The position arms are ``TRUE`` (keep) for a node carrying neither property:
    ``coalesce(NULL, false) = false`` and ``coalesce(NULL, '') <> 'terminal'``.
    So ``:Point``, ``:Subject`` and ``:Event`` (no pipeline) are unaffected by
    construction, and a status-less Object or a legacy node still reads live.
    Skipping the slot conjunct would make the predicate depend on a property
    only one label has; reading it on all of them costs nothing and cannot
    silently exempt a label whose pack later declares a pipeline.
    """
    if excluded is None:
        excluded = TERMINAL_EXCLUDED_STATUSES
    alias = clause.split(".", 1)[0] if "." in clause else clause
    flag = f"{alias}.outdated"
    chain = " AND ".join(
        f"{clause} <> '{s}'" for s in sorted(excluded))
    status_part = (f"({clause} IS NULL OR ({chain}))"
                   if chain else "true")
    # The position axis, in the KEEP direction. ``= false`` (not ``<> true``) so
    # this stays the exact De Morgan dual of ``_terminal_expression``'s
    # ``<> false`` arm below — the #7075 excluded-middle lesson, applied to the
    # new conjunct rather than re-learned on it.
    position_part = (
        f"(coalesce({alias}.isClosed, false) = false "
        f"AND coalesce({alias}.pipelineSlot, '') "
        f"<> '{TERMINAL_STAGE_SLOT}')"
    )
    if not include_outdated_flag:
        return f"({status_part} AND {position_part})"
    return (f"({status_part} AND coalesce({flag}, false) = false "
            f"AND {position_part})")


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
    """Cypher boolean expression: TRUE when the node is ended on EITHER axis.

    Composes the SAME vocabulary as ``_terminal_excluded`` (status in
    TERMINAL_EXCLUDED_STATUSES OR the legacy ``outdated=true`` flag) PLUS the
    position axis (``isClosed`` true OR a stage in the ``terminal`` slot, #13034),
    but in the POSITIVE direction, usable in RETURN projections, CASE conditions
    and WHERE OR-chains.

    It is the EXACT De Morgan dual of ``_terminal_excluded`` — by
    construction, not by hand-written correspondence: the flag half below is
    ``coalesce(flag, false) <> false``, the negation of that predicate's
    ``coalesce(flag, false) = false``, the status half negates its
    ``(status IS NULL OR status <> '…')`` NULL-guard, and the position half is
    the negation of that predicate's two position conjuncts
    (``isClosed <> false`` for ``= false``, ``pipelineSlot = 'terminal'`` for
    ``<> 'terminal'``). ``_alive_flag`` (this
    expression negated) therefore cannot report ALIVE a node the keep-filter
    drops, on any stored value, on either axis (#7075, extended by #13034 —
    before that the dual covered only the status and flag halves, so a closed
    Object could read ALIVE here and "not current" in the keep-filter).
    Before #7075 the flag half was
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
            f"OR coalesce({alias}.outdated, false) <> false "
            # #13034 — the position axis, in the POSITIVE direction: the
            # durable ``isClosed`` marker or a stage in the ``terminal`` slot.
            f"OR coalesce({alias}.isClosed, false) <> false "
            f"OR coalesce({alias}.pipelineSlot, '') "
            f"= '{TERMINAL_STAGE_SLOT}')")


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


def is_terminal_status(status, outdated: bool = False, *,
                       is_closed=None, stage_slot=None) -> bool:
    """Python mirror of the Cypher terminal predicate: the node is ended on
    EITHER axis — status in the terminal vocab, the legacy ``outdated=true``
    flag, or the position axis (``isClosed`` / a ``terminal`` pipeline slot,
    #13034). NULL/absent status is LIVE — the legacy-node rule, and since #9974
    R1 the ordinary birth state of a status-less Object/Subject (that fold seeds
    ``status=$st``, derived-or-absent) — mirrors ``_terminal_excluded``'s NULL
    handling.

    ``outdated`` is contractually a BOOLEAN (server-managed since #2491; the
    journal fold enforces the type). This mirror reads it by ``bool()``
    truthiness while ``_terminal_expression`` negates
    ``coalesce(outdated, false) = false``; the two agree on every boolean,
    NULL, non-empty string and non-zero number, and can differ when Python's
    ``bool()`` is falsy while ``coalesce(v, false) <> false`` holds — e.g.
    numeric ``0``/``0.0`` or the empty string ``""`` (live here, terminal in
    Cypher). Out of contract; no in-repo writer can produce such a value
    (#7075).

    ``is_closed`` / ``stage_slot`` are the POSITION axis, read by
    :func:`is_position_ended` (``isClosed`` by ``bool()``; a slot equals
    ``TERMINAL_STAGE_SLOT``). They are KEYWORD-ONLY and default to ``None``
    because they are a different SOURCE of the same answer, not a different
    vocabulary: every existing call site that hands this function a
    ``(status, outdated)`` pair keeps its exact meaning, and a caller that has
    the position in hand passes it rather than re-deciding currency itself.
    ⚠️ A caller that does NOT pass them is answering from the lifecycle word
    alone — correct for a ``:Point``/``:Subject``/``:Event`` row (no pipeline),
    and the residual for an ``:Object`` row whose query did not fetch the two
    props. Fetch them and pass them; do not re-spell the test.

    The composition itself is :func:`ending_axes` — the ONE declaration — so
    this boolean and the marker list a diagnostic prints (#13450) are the same
    answer, not two spellings of it.
    """
    return bool(ending_axes(status, outdated, is_closed=is_closed,
                            stage_slot=stage_slot))


#: The display prefix a mitigation's stored content carries.
#: ``sdk.mitigate_operator`` writes ``content='[MITIGATION] <reason>'`` (both
#: its CREATE and its in-place UPDATE); ``why._mitigation_text`` strips exactly
#: one for display.
MITIGATION_REASON_PREFIX = "[MITIGATION] "

#: The strength a mitigation with NO stored ``mitigation_strength`` prices at.
#: ``weights.mitigation_dampening_factor`` is the reader that OWNS this rule (a
#: pre-convention import may omit the property) and re-exports this name; the
#: declaration lives HERE because assertion IDENTITY has to agree with that read
#: rule — a redelivery at the documented default must match the withdrawn node —
#: and ``live.py`` is the leaf module ``weights.py`` imports, never the reverse.
MITIGATION_STRENGTH_DEFAULT = 0.30


def mitigation_reason(content) -> str:
    """The REASON a stored mitigation's ``content`` carries, prefix stripped.

    Assertion identity compares REASONS; the ``[MITIGATION] `` wrap is display
    punctuation that ``why.py`` strips on the way out, so a comparison against
    the wrapped string would never match.
    """
    return (content or "").removeprefix(MITIGATION_REASON_PREFIX)


def same_mitigation_assertion(stored_content, stored_strength,
                              reason, strength) -> bool:
    """True when a stored mitigation carries the SAME assertion.

    A mitigation IS an assertion — "(reason, strength) damps this operator" —
    and its identity is that PAIR, never the node id. That is what makes a
    machine re-delivery distinguishable from a re-assertion (#9962):

    * identical pair → the graph has ALREADY recorded this assertion, so a
      machine re-submission (crash-retry, redelivery, re-extraction) writes
      nothing. Reviving a withdrawn node silently reverses a human's
      retraction, and minting a second Point falsifies ``INGEST_CONTRACT``
      §3.2 key 6 ("never duplicated"). The silence is the same one an
      unchanged re-submission already has.
    * different pair → NEW evidence. On the event this predicate exists for —
      an operator whose mitigations are all WITHDRAWN — no write door may drop
      it: it reaches ``mitigate_operator``, which mints a live mitigation (a
      retraction is terminal, never revived in place — #9962/#432/#2422; a
      draft is never retro-promoted — #1088).

    BOTH machine write doors decide THAT EVENT from this one predicate —
    ``sdk._apply_ingest_mitigation`` (the ingest bundle route) and
    ``hosted_api._load_commit_graph_state``'s MITIGATES MERGE key (the hosted
    commit route) — so a redelivery cannot reinstate a withdrawn mitigation on
    one route while the other mints a fresh one for the identical record, which
    is exactly what they did before it.

    ⚠️ The parity is scoped to the withdrawn event, and the scope is stated
    because the hosted key has a SECOND arm this predicate does not govern: any
    CURRENT mitigation satisfies it (``hosted_api``'s arm 1, pre-existing), so a
    DIFFERENT pair submitted against an operator that already carries a live
    mitigation reconciles ``merge`` there with no write, while ``ingest``
    rewrites the live node in place and reports ``mitigation_strength_change``.
    That divergence predates #9962 and is not what this predicate closes; the
    claim here is bounded to the withdrawn-node event on purpose.

    STRENGTH identity follows the same read rule the dampener uses, so the two
    cannot disagree about what the graph records: a mitigation with NO stored
    ``mitigation_strength`` is priced at ``MITIGATION_STRENGTH_DEFAULT`` (0.30)
    by ``weights.mitigation_dampening_factor``, so its assertion IS
    ``(reason, 0.30)`` and a redelivery at 0.30 matches it. Reading an absent
    strength as "different" left the one hole in "a human ``retract_point`` is
    never undone by a machine redelivery": the guard fell through and
    ``mitigate_operator`` minted a live duplicate beside the withdrawn node.
    Values that cannot be COERCED to a float remain "different" — that is a
    different answer from "absent", which has a documented reading (never a
    silent match on unrepresentable data).
    """
    if mitigation_reason(stored_content) != (reason or ""):
        return False
    try:
        stored = (MITIGATION_STRENGTH_DEFAULT if stored_strength is None
                  else float(stored_strength))
        return stored == float(strength)
    except (TypeError, ValueError):  # unrepresentable legacy value
        return False


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


def first_terminal_row(rows) -> tuple | None:
    """The ``(status, outdated)`` pair of the FIRST terminal row, or ``None``.

    The ONE place a caller that needs the offending ROW (not a verdict) asks a
    ``LIFECYCLE_FANOUT_QUERY`` result "which row made this terminal?" — because
    the pair reaches a refusal MESSAGE, and two callers picking it their own way
    is how the same fan-out comes to be explained two different ways.

    Selected by the SAME stable key ``classify_lifecycle_fanout`` sorts with
    (status text, then flag), never by position: point ids are not unique and
    server row order is not a contract (#7822).

    It carries NO operator precedence, and that is deliberate: it answers "which
    row is dead", which a caller may ask of ANY fan-out — it returns ``None`` on
    a healthy one. ``classify_lifecycle_fanout`` ranks ``operator`` ABOVE
    ``terminal``, so its verdict ALONE cannot answer this question on a mixed
    operator+terminal fan-out; the pair therefore comes from the rows, never from
    the classifier's ``status``.
    """
    return min(((st, od) for _is_op, st, od in rows
                if is_terminal_status(st, od)),
               key=lambda r: (str(r[0] or ""), bool(r[1])), default=None)


def terminal_row_marker(row) -> str:
    """The WORD a refusal/warning uses for a ``first_terminal_row`` result.

    The second half of the same problem ``first_terminal_row`` answers: picking
    the row is one decision, NAMING it is the next, and a caller that renders the
    pair itself is how one row is called ``live`` in a warning while
    ``is_terminal_status`` says it is dead (#12586 review cycle 4). The flag has
    to be rendered, not just read: a row whose status is ``NULL`` or ``live``
    while ``outdated`` is set is terminal BY THE FLAG, and ``status or "live"``
    reports exactly the opposite of the verdict that selected it.

    ``None`` renders ``live``: callers that ask on a healthy fan-out still need
    the word ``classify_lifecycle_fanout`` would have given them.
    """
    if row is None:
        return "live"
    status, outdated = row
    return ("outdated" if outdated
            and status not in TERMINAL_EXCLUDED_STATUSES
            else (status or "live"))


def _live_only(clause: str, include_draft: bool = False) -> str:
    """Return a Cypher predicate excluding draft AND terminal nodes.

    Args:
        clause: alias-qualified status reference, e.g. ``"n.status"``.
        include_draft: when True, drafts are re-included (the
            ``run(include_draft=True)`` escape hatch) — terminal nodes are
            NEVER re-included (#2422).

    An ABSENT status is LIVE — the legacy-node rule, and since #9974 R1 also the
    ordinary birth state of an Object/Subject: that fold seeds ``status=$st``
    (derived-or-absent, never a fabricated ``'live'``), while the Point fold still
    writes ``coalesce($st, n.status, 'live')``. Hence the NULL checks. This is
    also why an EPISTEMIC-LABEL widening (#7927) needs no per-label status
    branch: ``:Event`` carries no ``status`` at all — the write door refuses one
    (#13100) — so it reads LIVE here exactly as a status-less Point does.
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
#     read (#7853 owner measurement). Point/Subject/Object/Event each carry an
#     ``id`` RANGE index (Event joined them in #7927 — it carried only
#     ``eventId``, so an Event addressed by ``id`` full-scanned).
#
#     Use ``epistemic_sweep`` for the sweep itself: resolve the labels PRESENT
#     (``epistemic_labels_of``) and run the anchored query only for those. An
#     unconditional four-label sweep is what breached the #395 AC5 latency
#     bound (0.73s -> 1.50s against a 1.2s limit) — a Point-only id set must
#     stay ONE anchored read.
#
# ``:Event`` writers stamp the ``eventStatus`` INGEST marker and never a
# ``status`` (its lifecycle has no status fold —
# ``consistency._STATUS_COMPARED_LABELS``, and since #13100 the write door
# refuses a ``status`` on an Event outright; a legacy row can still carry one).
# That does NOT need a
# per-label branch on a widened leg: ``_live_only`` treats an ABSENT status as
# LIVE (``_terminal_excluded``'s NULL check), so an Event passes the same
# predicate a Point does, and the draft filter is a no-op for it by
# construction rather than by a special case (#7927).
#
# The BOUND on that claim, stated so a future status word cannot quietly widen
# it: the widened legs apply the POINT vocabulary (``TERMINAL_EXCLUDED_STATUSES``
# — the superset), bypassing ``search_engine._status_vocab_for``, which is the
# declared owner of the "which family excludes what" decision. That is
# behaviour-preserving TODAY because ``OBJECT_TERMINAL_STATUSES`` and
# ``SUBJECT_TERMINAL_STATUSES`` are subsets of the Point set and no entity writer
# sets the legacy ``outdated`` flag — so the predicate cannot drop a live entity.
# A family-ONLY terminal word (the #1423/#3301 class) would be admitted as LIVE
# on every widened leg; wire the legs through ``_status_vocab_for`` then.
# ══════════════════════════════════════════════════════════════════════════

#: The four labels on which confidence propagates (#7813 ruling). Ordered
#: Point-first: the Point query is the hot path and stays the first, indexed
#: lookup in every id-anchored sweep.
EPISTEMIC_LABELS = ("Point", "Subject", "Object", "Event")

#: The epistemic labels whose layer CARRIES a ``status`` family — every one but
#: ``:Event``, which has NO lifecycle at all: a dated fact does not end, and the
#: write door refuses a ``status`` on it (#13100). ``eventStatus`` is the ingest
#: marker (``pending`` / ``indexed``), not a lifecycle word, and a
#: ``status``-rendering read deliberately does not mix it in.
#:
#: This is the declaration behind the ``related_status`` / ``provenance``
#: contract: an ABSENT status READS live (``_terminal_excluded``'s NULL arm, and
#: since #9974 R1 also the ordinary birth state of an Object/Subject) on these
#: three, while ``:Event`` reports ``""`` — "this layer exposes no ``status``",
#: the conflation #6976's widening was added to end. ⛔ Do NOT flatten the two
#: into one ``value or 'live'`` at a call site: that reports ``live`` for an
#: ``:Event`` peer, i.e. it invents a lifecycle state on the one label that has
#: none. Use :func:`status_as_reported`.
STATUS_CARRYING_EPISTEMIC_LABELS = frozenset({"Point", "Subject", "Object"})


def status_as_reported(value: str | None, *, label: str | None = None) -> str:
    """Render a STORED status as the string a read surface REPORTS.

    ``value`` is the graph's ``status`` property (``None`` when absent).

    An ABSENT status READS live — the legacy rule, and since #9974 R1 the
    ordinary birth state of a status-less Object/Subject — so a surface that
    renders the value as a STRING must render ``live`` too. Rendering ``""``
    instead would reuse the value ``expand_relationships`` reserves for "this
    peer's layer has no ``status`` family at all", which is a different claim.

    ``label`` — the node's epistemic label, when the caller knows it. Omit it
    only where the label is fixed by construction (e.g. a ``:Subject``-anchored
    lookup). An UNNAMED label is treated as status-carrying: that is right for
    ``:Point``/``:Subject``/``:Object``, and can only be wrong for an
    ``:Event`` the caller failed to name.
    """
    if value is not None:
        return value
    if label is not None and label not in STATUS_CARRYING_EPISTEMIC_LABELS:
        return ""
    return "live"


#: The ONE refusal predicate behind #13100 (was #11825's terminal half), as a
#: sentence so every door that imports it cannot drift in what it claims to
#: enforce.
_EVENT_LIFECYCLE_STATE_REFUSAL = (
    "an :Event is a dated fact and carries NO lifecycle (#13100 / S7: the "
    "approved model, §8 — `there is no third case`). A thing that has not "
    "happened yet is not an event. "
    "WHAT TO DO: if it has not happened, create a PLAN (`core:plan`, "
    "`tortoise/planned_occurrence.py`) and settle it when it does; when it "
    "happens, write a NEW :Event and link it to the plan (`aboutObject`). To "
    "record a correction, write a NEW :Event — a dated fact is corrected by a "
    "later fact, never by a status flip on an earlier one. For ingest "
    "bookkeeping use `eventStatus` (`pending` -> `indexed`), which is a plain "
    "prop and not a lifecycle. To un-mark a legacy status on a plain :Event, "
    "pass `status=None` to the UPDATE door (`update_knowledge`) — ⛔ it is the "
    "one door that REMOVES a property; the record doors (`add_event`, "
    "`emit_belief`) drop a null on the way to the fold, so a clear sent there "
    "writes nothing. On a :Point:Event HYBRID there is NO status door that "
    "un-marks a terminal word: `update_knowledge` refuses `None` (the Point "
    "vocabulary has no null word) and refuses a dead claim, `update_point` only "
    "promotes draft->live, and the retraction verbs refuse a node carrying "
    ":Event. A hybrid's non-terminal `status` is that Point's own currency "
    "(`update_point(id, status='live')`), never an Event state; to correct what a "
    "dated fact says, write a NEW :Event — a dated fact is corrected by a later "
    "fact, not by a status flip."
)


def _status_is_discarded_by_every_writer(value: object) -> bool:
    """True only when every writer DISCARDS ``value`` on its way to the node.

    The question :func:`refuse_event_lifecycle_state` asks before typing a
    non-boolean ``outdated``: a value no writer keeps leaves the node with no
    flag, so it cannot make the Event terminal and refusing it would be #7925
    round 4's over-rejection class. (It is no longer asked of ``status`` — since
    #13100 the declaration itself is refused, whatever its value.) ⛔ But THREE paths can carry a
    ``status`` to the store and each has its OWN predicate, so asking one of
    them is not enough — measured at review cycle 3, after the first revision
    asked only the fold's:

      * ``_is_persistable_prop_value`` is the **FOLD's** filter. It refuses
        ``np.int64``/``Decimal``/``np.bool_`` LEAVES that the engine really
        DOES store, so scoping on it alone let ``update_entity(status=np.int64(7))``
        write ``7`` onto the node (HEAD refused it).
      * ``projection._value_ok`` is the **LIVE WRITE transport's** predicate —
        what the ``#7369`` gate nulls on. A value it accepts is stored.
      * the **JOURNAL encoder** is a third rule: ``bytes``/``set``/
        ``bytearray`` are not JSON-serialisable, so accepting one did not reach
        the fold at all — it raised a bare ``TypeError`` in the record append at
        ``add_event``/``_emit``, and at ``emit_belief`` it raised inside the
        best-effort ``except`` arm, which DROPPED the record silently.

    So a value is safe to accept only if the FOLD drops it, the TRANSPORT does
    not store it, and the JOURNAL CAN encode it — the third clause is a
    requirement to PASS, not to fail (an un-encodable value is refused rather
    than left to crash the append).

    Imported at CALL time so this leaf module keeps importing nothing from the
    projection at import time (the construction ``sdk._status_is_stored_unhashable``
    also uses).
    """
    from .projection import _value_ok
    from .projection.entities import _is_persistable_prop_value

    if _is_persistable_prop_value(value) or _value_ok(value):
        return False
    try:
        json.dumps(value)
    except (TypeError, ValueError):
        return False
    return True


def _event_state_key_names(key) -> tuple[str, ...]:
    """EVERY name this key can be filed under — the union of the two renderers.

    ⛔ There are TWO key renderers and they disagree for a `str` SUBCLASS:
      * the LIVE transport inlines ``str(key)`` (bytes: ``key.decode()``) —
        ``projection._map_key_ok`` states it;
      * the JOURNAL serialises the map with ``json.dumps``, which writes a
        ``str`` subclass key by its CONTENT.
    Measured (review cycle 7, P1): a key with content ``"status"`` and
    ``__str__`` returning ``"harmless"`` was ACCEPTED at every door, the live
    node kept ``harmless='superseded'``, the journalled record carried
    ``"status": "superseded"``, and ``rebuild_all`` from that journal produced a
    TERMINAL ``:Event`` — a bypass that only exists on the replay path, which is
    the path the whole half-state class is about. So the guard asks the union:
    if ANY renderer would file the key under a guarded name, it is guarded. The
    reverse (content ``"x"``, ``__str__`` ``"status"``) was already refused; this
    closes the direction that was not.

    ⛔ ONE rule per side of the pair — the KEY is rendered (``str(key)`` /
    ``.decode()``), the VALUE keeps its base content (``str.__str__``) — because
    that is what the writer does with each. A ``str`` SUBCLASS whose CONTENT is
    ``"x"`` but whose ``__str__`` returns ``"status"`` is stored under
    ``status``, so normalising a key by content let it through and the guard saw
    a different key from the writer (measured, review cycle 5, P0). ``bytes`` is
    the second half of the same rule: the driver DECODES a bytes key, so
    ``b"status"`` writes ``status`` and a guard that skipped non-``str`` keys
    missed it entirely (cycle 5, P1).

    ⛔ There is deliberately NO single-name variant (``..._names(key)[0]``): a
    door that tests ONE rendering instead of the union is exactly the hole this
    function exists to close. The superseded one-name helper was removed at the
    review of PR #12108 (finding 2) so the next door cannot wire it by mistake.
    """
    names: list[str] = []
    if isinstance(key, bytes):
        with contextlib.suppress(UnicodeDecodeError):
            names.append(key.decode())
    else:
        names.append(str(key))          # the live transport's rendering
        if isinstance(key, str):
            names.append(str.__str__(key))   # the journal's rendering (content)
        else:
            names.append(repr(key))
    # Sorted + de-duplicated only so a plain ``str`` yields exactly one name.
    return tuple(dict.fromkeys(names))


def lifecycle_props_by_rendering(props, guarded) -> dict:
    """`{guarded name: value}` for every key whose RENDERER UNION names one.

    ⛔ A door's PRESENCE TEST must be as shape-independent as the refusal it
    guards, or the guard is decoration. `"status" in props` hashes the literal
    name, so a `str` SUBCLASS key that overrides `__hash__` is missed by the
    test (and by `set(props)`) while the WRITE still files it under `status` —
    the live transport inlines `str(key)` and the journal writes the content.
    That is not hypothetical: it was measured against this module's sibling door
    (review cycles 4-8 of #11825) and then re-measured against the `#9649`
    lifecycle guard added 70 lines below it in `update_entity` —
    `update_entity(<superseded>, **{KHash("status"): "live"})` was ACCEPTED
    (resurrected, window left closed, zero journal delta) while the #11825 guard
    on the same node refused the identical call. Same door, two presence tests,
    one hole.

    FIRST-WINS, in iteration order, so a plain `str` key makes this equal to
    `props[name]` and no ordinary call changes behaviour. Read this mapping to
    ANSWER a question ("was a guarded field asked for?", "what value?") and use
    `keys_rendering_as` to DROP the keys afterwards — reading `props[name]`
    after this is exactly the hole it exists to close.
    """
    guarded = frozenset(guarded)
    out: dict = {}
    for key, value in props.items():
        for name in _event_state_key_names(key):
            if name in guarded:
                out.setdefault(name, value)
    return out


def keys_rendering_as(props, names) -> list:
    """The KEY OBJECTS in `props` that any renderer files under one of `names`.

    The companion to `lifecycle_props_by_rendering`: after the guarded write has
    been applied one way, a door must drop EVERY key that would re-write it
    through the generic `SET n += $props` bag — and `k != "status"` drops only
    the literal name, leaving the subclass key to write the field a second time
    (measured: the status arm wrote `superseded`, the drop missed the key, and
    the label loop's own write was the resurrecting one).
    """
    wanted = frozenset(names)
    return [key for key in props
            if wanted.intersection(_event_state_key_names(key))]


def event_label_names(value) -> tuple[str, ...]:
    """EVERY name this LABEL can be filed under — the union of the two renderers.

    The same asymmetry ``_event_state_key_names`` records for KEYS, one axis over,
    and it bites the same way: the LIVE writers interpolate the label into the
    Cypher pattern by ``str(label)``, while the JOURNAL serialises the record with
    ``json.dumps``, which writes a ``str`` SUBCLASS by its CONTENT. A label whose
    content is ``Event`` and whose ``__str__`` returns ``harmless`` therefore
    passes a ``str(label)`` test, lands on ``n:harmless`` live, and REPLAYS as a
    terminal ``:Event`` — measured at this gate (review cycle 8, P1): the refusal
    was skipped, the journal carried ``"label": "Event"`` + the terminal state,
    and folding it produced ``status='superseded'`` on an ``:Event`` node.
    ⛔ The guard must ask the union, and the CONTENT rendering is the dangerous
    one — it is the one the durable record keeps.
    """
    names: list[str] = []
    if isinstance(value, bytes):
        with contextlib.suppress(UnicodeDecodeError):
            names.append(value.decode())
    else:
        names.append(str(value))                 # the live writer's rendering
        if isinstance(value, str):
            names.append(str.__str__(value))     # the journal's rendering (content)
    return tuple(dict.fromkeys(names))


def label_names_an_event(value) -> bool:
    """True when ``value`` names the ``:Event`` label under ANY renderer."""
    return any(name.strip().lstrip(":").lower() == "event"
               for name in event_label_names(value))


def declares_event_state_key(props) -> bool:
    """True when ``props`` declares ``status`` OR ``outdated``, under any name.

    ⛔ The SDK door's presence test must gate on BOTH halves of the predicate.
    It gated on ``status`` alone, so `outdated` was dead code at
    ``update_entity`` for exactly the key class this change fixed for ``status``:
    a rendered-alias ``outdated`` key slipped past `_sanitize_props`' literal
    ``"outdated" in props``, the guard was skipped, and the flag was STORED
    (measured, review cycle 6 P1 — the terminal half-state #11825 records, reached
    through a declared door). `create_entity`/`add_event`/`_emit`/`emit_belief`
    refused the same key, so this was the one hole.

    The presence TEST must also be as shape-independent as the refusal it
    guards: ``"status" in props`` hashes the literal name, so a ``str`` SUBCLASS
    key that overrides ``__hash__`` is missed and ``sdk.update_entity`` then
    skips the guard entirely (measured at review cycle 4 — the VALUE axis was
    closed and the KEY axis was not).

    ⛔ The superseded ``status``-only helper was removed at the review of PR
    #12108 (finding 2): it gated exactly ONE half of the predicate, which is the
    defect above, so leaving it importable from this shared leaf module let the
    next door re-open the hole silently.
    """
    return any(_event_state_key_names(key) and
               {"status", "outdated"}.intersection(_event_state_key_names(key))
               for key in props)


def _event_state_declarations(props) -> tuple[list, list]:
    """EVERY value this map declares for ``status`` and for ``outdated``.

    ⛔ LISTS, not one value, and that is the whole point (review cycle 8, P0).
    The writer applies EVERY key of the map, so two different keys that both
    render to ``status`` are BOTH written; picking the last one let a terminal
    declaration be MASKED by a later non-terminal one and the terminal value was
    stored — live, and again after `rebuild_all` — at six of the seven doors.
    The refusal must fire if ANY declaration is terminal, which is also what the
    Phase-1 arm already does, so the two cannot disagree.

    A declaration is kept distinct from "absent" because ``status: None`` is a
    meaningful clear (the refusal text documents it).
    """
    status_values: list = []
    outdated_values: list = []
    for key, value in props.items():
        names = _event_state_key_names(key)
        if "status" in names:
            status_values.append(value)
        if "outdated" in names:
            outdated_values.append(value)
    return status_values, outdated_values


def refuse_event_lifecycle_state(props, *, door: str) -> None:
    """Refuse EVERY lifecycle ``status`` on an ``:Event`` — the whole word set.

    Write-side ONLY, and deliberately NOT an Event status family: `:Event`
    stays outside `STATUS_CARRYING_EPISTEMIC_LABELS`, and ``status_as_reported``
    still reports ``''`` for an Event — this function says which writes are
    FORBIDDEN, not which states the layer owns.

    ## Why the whole vocabulary, and not just the terminal half

    #11825 refused only the TERMINAL half and documented the reason: a
    non-terminal `status` (`draft`, `live`, `scheduled`) kept riding the prop
    lane because ``_promote_incident_operators`` read it as the live/draft gate
    (#7853, #10828). That interim boundary left the real defect standing — the
    two mutable progress words #13100 names — because it conserved the very
    conflation it was fixing: an ``:Event`` could still say "not yet", which is
    a PLAN's job (`tortoise/planned_occurrence.py`), and the field carrying that
    word was load-bearing for promotion.

    #13100 (S7) retires the field instead of narrowing it, on the approved
    design of record (§8): *an event is a dated fact, and a thing that has not
    happened yet is not an event — it is a plan … there is no third case.* Two
    consequences, both deliberate:

    * **``status=None`` is still accepted** — it is the documented un-mark route
      for a legacy row that already carries a word (the reader net stays wider
      than the write door, #2977), and a null write REMOVES the property.
    * **Suppression is re-homed, not dropped.** The live/draft gate that used
      this prop now suppresses on the plan's POSITION
      (:func:`endpoint_permits_operator_promotion`), and it still reads a stored
      legacy word, so a pre-retirement row keeps blocking promotion. Neither
      half silently disappears with the field.

    ## Not a vocabulary, and not an allowlist of values either

    This is a **key** refusal, not a value check: any non-``None`` ``status`` is
    refused, whatever its type. The value-based scoping #11825 needed
    (:func:`_status_is_discarded_by_every_writer`, kept below for the
    ``outdated`` half that still uses it) existed to avoid over-rejecting a
    bundle whose value no writer keeps. That reasoning no longer applies here,
    because the defect is the DECLARATION, not the value: a caller passing
    ``status=<anything>`` on a dated fact is asking for a lifecycle the model
    does not have, and a silently-dropped value is the "accepted and
    half-honoured" shape #11825 records. The refusal says what to do instead.

    ⛔ A ``str`` subclass cannot bypass this — there is nothing to compare, so
    no ``__eq__``/``__hash__`` override can help. The one shape check that
    remains is ``outdated``'s (below), where the value still decides.

    ⛔ RECIPROCAL INVARIANT (do not relax this guard alone): the retrieval legs
    omit `:Event` from `search_engine._STATUS_FILTERED_LABELS` BECAUSE this guard
    makes a statused Event unwritable. Relaxing the guard requires adding
    `Event` to `_STATUS_FILTERED_LABELS` AND giving
    `search_engine._status_vocab_for` an `Event` arm in the SAME change (it
    currently falls through to the Object arm, which would exclude the wrong
    vocabulary).

    ⛔ ``outdated`` must be a REAL boolean here, and this is the one door where
    that is not already guaranteed. It is contractually a boolean
    (server-managed since #2491; :func:`is_terminal_status` says so), and the
    SDK doors enforce it by refusing the key outright (``_sanitize_props``).
    ``EventAPI.add_event`` writes ``**extra`` straight onto the node with no
    such intake, so before this check it could STORE a non-boolean — and the two
    predicates already disagree about that value: the Cypher one
    (``coalesce(outdated,false) <> false``) reads ``''``/``0``/``0.0`` as
    TERMINAL while :func:`is_terminal_status` reads them as live. The
    documented "out of contract; no in-repo writer can produce such a value"
    was measurably false once this door existed, so the value is refused rather
    than half-read (review cycle 1, P1). The "#7925 round 4" over-rejection
    scoping is kept for THIS half only: a value every writer discards clears the
    property rather than storing it, so it cannot make the Event terminal.

    ``props`` is the caller's property map. The SDK doors flatten it first
    (``_coerce_props``), so a nested ``props={"status": …}`` reaches here as a
    top-level key. At ``add_event`` the flatten never runs, and the
    ``EventRecorded`` fold UNWRAPS a caller key literally named ``event``
    (``inner = event.get("event", event)``), so
    ``add_event(id, kind, event={"status": "superseded"})`` rode that lane
    straight past a top-level check (measured, #11825 review cycle 2 P0). The
    chokepoint in ``api._emit`` therefore unwraps the same key before asking
    this function, which is what makes the answer independent of which kwargs
    shape arrives. ``door`` names the surface in the refusal, per
    `docs/ax/ax-principles.md` (#10183): refused, exactly what failed, exactly
    what to do.
    """
    status_values, outdated_values = _event_state_declarations(props)
    # EVERY declaration is judged, not the last one (the #11825 review cycle 8
    # P0 class): the writer applies every key of the map, so a guard that
    # stopped at the first non-refused value let a second one through at six of
    # the seven doors.
    for _value in status_values:
        if _value is None:
            # Clearing a legacy word is the documented un-mark route, and
            # `SET n += {status: null}` removes the property.
            continue
        raise ValueError(
            f"Refused: `status={_value!r}` is not writable on an :Event "
            f"({door}). {_EVENT_LIFECYCLE_STATE_REFUSAL}"
        )
    for _value in outdated_values:
        if _value is None or isinstance(_value, bool):
            continue
        if not _status_is_discarded_by_every_writer(_value):
            raise ValueError(
                f"Refused: `outdated={_value!r}` is not a legal value on an "
                f":Event ({door}). `outdated` is contractually a BOOLEAN and it is "
                f"server-managed; a non-boolean is read as TERMINAL by the Cypher "
                f"predicate and as LIVE by the Python mirror, which is the "
                f"half-state #11825 records, reached through the one door that "
                f"writes props without `_sanitize_props`. WHAT TO DO: pass `True`, "
                f"`False`, or omit the key."
            )
    for _value in outdated_values:
        if _value is True:
            raise ValueError(
                f"Refused: `outdated={_value!r}` is not writable on an "
                f":Event ({door}). {_EVENT_LIFECYCLE_STATE_REFUSAL}"
            )


#: A plan that has not reached a terminal stage has not happened, so an operator
#: incident to it must not be promoted — the suppression #13100 re-homes from an
#: ``:Event``'s retired non-terminal ``status``. Read through
#: :func:`endpoint_permits_operator_promotion`; ``planned_occurrence`` owns the
#: kind and stage declarations, and this module takes them as arguments so the
#: predicate stays importable from a leaf.
def endpoint_permits_operator_promotion(
    status, outdated=False, *, plan_state=None,
) -> bool:
    """True when one operator endpoint lets a draft operator be promoted.

    The ONE predicate behind ``sdk._promote_incident_operators``' all-endpoints
    gate, so the four-label sweep and its rules cannot drift. Three arms:

    * **dead** — :func:`is_terminal_status` (status word or flag) ends it. EP
      strips the node, so a promotion would leave a live operator over a dead
      input.
    * **not live** — an explicitly non-``live`` stored word keeps the endpoint
      out. An ABSENT status reads live (the legacy-node rule, #9974 R1). This is
      the arm that still honours a **legacy** ``:Event`` row carrying ``draft``:
      the write door refuses that shape now (#13100), and the reader keeps
      reading it, because a stored fact is not corrected by refusing new writes
      (#2977 — the net stays wider than the door).
    * **planned, not happened** — a plan whose position is a non-terminal,
      still-resolving stage has not occurred yet, so it suppresses exactly as the
      retired ``:Event`` ``status='scheduled'`` did.

      ⛔ ``plan_state`` is the dict ``planned_occurrence.plan_position`` returns,
      i.e. the WRITER's own answer (whether the record has ENDED — the durable
      marker OR a stage that resolves terminal — and, separately, whether a stage
      MOVE could be validated) — never the stored ``pipelineSlot`` prop, which is a
      cache that a generic-door ending leaves stale. Passing the resolved state
      is why this predicate takes a dict rather than a slot string: the earlier
      signature let a settled plan keep suppressing promotion for ever, and let
      the caller re-derive terminality at the call site (the drift #2901 exists
      to stop).

      Two bounds, so a plan is not suppressed for ever: an ENDED plan (marker or
      terminal stage) is a settled record, and a plan with **no position at all**
      is not evidence of anything (an extractor-minted ``core:plan`` Object was
      promoted over before this change; inventing a "not yet" for it would block
      that operator). So the arm fires on exactly one condition: the plan HAS a
      position and has NOT ended.

      ⛔ Both UNRESOLVABLE axes fail CLOSED, deliberately. An unknown
      ``pipelineStageId`` resolves to no slot exactly as a cleared/foreign
      ``pipelineStageKey`` does, and either is ONE supported, customer-callable
      write (``update_knowledge(id, pipelineStageId="Held")`` /
      ``pipelineStageKey=None``). Reading either as "no position, so no evidence"
      switched the suppression off for an occurrence that demonstrably has not
      happened — and did so PERMANENTLY for the stage-id axis, because the plan
      writers and ``advance_stage`` refuse every move on such a plan, so it can
      never reach a terminal stage. Neither axis loses the repair route (both
      are repaired by writing a declared stage id, which
      ``planned_occurrence._unresolvable_message`` prescribes), so denying here
      is recoverable; the durability bounds above are unchanged.

    ⛔ The **not live** arm is EVALUATED FIRST, and that order is load-bearing —
    see the ordering note at the code below. The bullet order above is the
    SEMANTIC reading, not the evaluation order.
    """
    # ⛔ ORDER IS LOAD-BEARING: the NOT-LIVE arm runs first, exactly as the
    # inline gate this predicate was extracted from did
    # (`origin/main:tortoise/sdk.py`: `(st or "live") == "live" and not
    # is_terminal_status(st, bool(od))` — equality FIRST, so `and`
    # short-circuited). `is_terminal_status` is a `frozenset` MEMBERSHIP test
    # (`(status or "") in TERMINAL_EXCLUDED_STATUSES`), and membership HASHES
    # the value, so a stored ARRAY `status` raises `TypeError: unhashable type`.
    # That value is reachable by ONE supported write: an array is PERSISTABLE,
    # so `update_knowledge(id, status=[…])` stores it verbatim on an `:Object`
    # (#8926), and `:Object` is an operator endpoint since #7853 widened the
    # four-label sweep. Running the membership test first turned the old silent
    # suppression into an unnamed 500 out of `tortoise_promote_point` (#10183).
    # The equality arm answers every non-`live` value WITHOUT hashing it, so
    # this order keeps each input's pre-#13100 answer: `""`/`0`/`None` still
    # read live, a list still suppresses, terminal words still deny. Normalising
    # the array to a word instead would be WRONG — `(None or "live") == "live"`
    # promotes the array row, which the pre-change gate never did.
    if (status or "live") != "live":
        return False
    if is_terminal_status(status, bool(outdated)):
        return False
    if plan_state is not None:
        # A plan that HAS a position and has not ENDED has not happened yet.
        # ⛔ The discriminant is NOT the resolved SLOT or `unresolvable`: a plan
        # whose stored stage key is missing, or whose stage id is not declared,
        # has no slot — and treating either as fail-open let one supported write
        # on the plan switch the suppression off for an occurrence that has not
        # happened (the key axis was fixed that way; the stage-id axis is the
        # identical reachability through the identical door). `ended` already
        # encodes every terminal answer (the durable marker, and a stage that
        # RESOLVES terminal), so the slot adds nothing here but the hole.
        not_yet = plan_state["has_position"] and not plan_state["ended"]
        return not not_yet
    return True


#: The keys an epistemic node's identity may live under, in resolution order
#: (#7927). ONE declaration for a question three widened surfaces ask —
#: ``audit``'s legacy-edge repair, ``ep.get_contested_claims`` and
#: ``ranking.resolve_contested_relevance`` — each of which used to spell
#: ``coalesce(id, eventId)`` inline with nothing pinning them together.
#:
#: ``id`` FIRST, deliberately, and it DIVERGES from
#: ``projection._CANONICAL_ENTITY_ID_PROPS`` (which names ``eventId`` as the
#: Event family's PRIMARY identity key) and from
#: ``consistency._graph_view_key`` (eventId first for ``Event``): those answer
#: "which key identifies this node for the graph view", this answers "which key
#: does a READ hand back". ``id`` is the right answer for the second because the
#: belief fold addresses an Event's belief BY ``id`` (#7936), and
#: ``session_indexer`` writes ``eventId`` only — which is the case the second key
#: exists for.
#:
#: ⛔ The coalesced value is NOT a unique identity, and must not be used as one.
#: ``_upsert_event`` sets ``id = eid`` on the plain path, but
#: ``_event_guarded_merge``'s SUFFIX branch MERGEs on the suffixed ``eventId``
#: while ``id`` keeps the CANDIDATE — so two distinct Events both coalesce to the
#: candidate value. The cross-key shape ``consistency`` already models (#8879:
#: ``:Event {id:'X'}`` beside ``:Event {eventId:'X'}``) collides the same way.
#: Use this for DISPLAY and for handing a value back to a caller; use ``id(n)``
#: (the internal node id) for every COMPARISON (a self-reference guard) and every
#: DEDUPE key. Both bugs are recorded: a value-collision silently dropped a real
#: counter-claim, and a value-collision dedupe dropped a distinct Event row.
EPISTEMIC_ID_KEYS = ("id", "eventId")


def epistemic_identity(alias: str) -> str:
    """Cypher expression: ``alias``'s identity, whatever key it lives under.

    Use wherever a widened surface COMPARES or RETURNS an identity. Do not use
    it where the value will be fed back into an ``{id: …}`` anchor: an identity
    that came from ``eventId`` is not an ``id``, and an id-keyed frontier cannot
    follow it (the ``navigation._node_identity`` lesson, #8538) — such a leg must
    require ``alias.id`` instead, which is what the widened BFS/factor returns
    do (see ``analyze._bfs_select_operators``).
    """
    if len(EPISTEMIC_ID_KEYS) == 1:
        return f"{alias}.{EPISTEMIC_ID_KEYS[0]}"
    return "coalesce(" + ", ".join(
        f"{alias}.{key}" for key in EPISTEMIC_ID_KEYS) + ")"




#: The property each epistemic label stores its KIND under (#11928 review
#: round 1). One declaration for a question several reads ask, because the
#: per-label answer is NOT the same property and a read that assumes one is
#: silently blind on the others: ``create_object``/``create_event`` write
#: ``objectKind``/``eventKind`` and leave ``pointKind`` NULL, so a kind gate
#: written as ``n.pointKind`` is DEAD on exactly the labels #11928 widened to
#: (``why._assemble_tradeoffs`` had two of them; see its ``_TRADEOFFS_CYPHER``).
#:
#: ⛔ SCOPED TO THE EPISTEMIC FOUR, deliberately. ``ranking._enrich`` spells a
#: WIDER chain for its display kind (it appends ``documentKind`` and
#: ``sourceKind``, ``ranking.py``:2560) — this helper does NOT, because the
#: fields that consume it are restricted to ``EPISTEMIC_LABELS`` by
#: ``epistemic_disjunction``. A ``:Document``/``:Source`` kind read through
#: ``kind_expression`` is therefore ``''``: widen it HERE when a consumer needs
#: those labels, and never assume the two chains are the same rule.
KIND_PROPERTIES = ("pointKind", "objectKind", "subjectKind", "eventKind")


def kind_expression(alias: str) -> str:
    """Cypher expression: ``alias``'s KIND, whatever property its layer uses.

    ⚠️ "Its layer" means one of the four EPISTEMIC labels — see
    :data:`KIND_PROPERTIES` for the scope and for what a ``:Document``/
    ``:Source`` kind would answer (``''``).

    Use for any read that COMPARES or PARSES a node's kind across labels; the
    absent case coalesces to ``''`` (never NULL) so a caller's ``== "option"``
    arm is false rather than unanswerable, which is what every existing reader
    of this value does.

    ⛔ Do NOT use it as an identity, and do not confuse it with a label: an
    ``:Object {objectKind: 'option'}`` and a ``:Point {pointKind: 'option'}``
    answer the same KIND and are different layers.
    """
    return "coalesce(" + ", ".join(
        f"{alias}.{prop}" for prop in KIND_PROPERTIES) + ", '')"


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


def epistemic_labels_of(graph, ids) -> list[str]:
    """Epistemic labels PRESENT among ``ids`` — one batched query per label.

    ``graph`` is the Falkor graph handle (``projection.g``), not the
    projection wrapper.

    The NARROWING half of an id-anchored read (#7927): rather than running
    every id-anchored query once per label, resolve which labels actually
    occur and sweep only those. A Point-only id set (the overwhelming
    majority of EP reads) costs ONE query, not four.

    Resolution is FIRST-WINS in ``EPISTEMIC_LABELS`` order (Point first — the
    hot path) and MULTI-LABEL AWARE: a ``:Point:Object`` node
    (``update_point``'s promote arm mints one) resolves as ``Point`` and is
    still matched by a ``(n:Point)``-anchored query, so keeping either of its
    labels is correct. Only labels that actually resolved one of ``ids`` are
    returned, and the scan stops as soon as every id is accounted for.

    Returns at least ``EPISTEMIC_LABELS[0]`` so a caller's anchored query still
    runs for an id matching no epistemic label — exactly the pre-widening
    shape, which found nothing for such an id.
    """
    queries = dict(zip(
        EPISTEMIC_LABELS,
        epistemic_label_queries(
            "MATCH (n:{label}) WHERE n.id IN $ids RETURN n.id"),
        strict=True))
    unresolved = list(dict.fromkeys(ids))
    present: list[str] = []
    for label in EPISTEMIC_LABELS:
        if not unresolved:
            break
        rows = graph.query(
            queries[label], params={"ids": unresolved}).result_set
        found = {r[0] for r in rows} & set(unresolved)
        if found:
            present.append(label)
            unresolved = [i for i in unresolved if i not in found]
    return present or [EPISTEMIC_LABELS[0]]


def epistemic_sweep(graph, template: str, params: dict | None = None, *,
                   ids=None, labels=None) -> list:
    """Run an ID-ANCHORED template once per PRESENT epistemic label; merge rows.

    The single consumer-side entry point for an id-anchored multi-label read
    (#7927) — it composes the two primitives above rather than restating them:
    ``epistemic_label_queries`` expands the template, ``epistemic_labels_of``
    narrows the sweep to the labels actually present.

    ``graph`` is the Falkor graph handle (``projection.g``). ``ids`` defaults
    to ``params["ids"]`` (the convention every caller here follows);
    ``labels`` overrides the resolution when a caller already resolved them for
    this hop.

    DEDUPE CONTRACT: rows are PER LABEL, so a multi-labelled node
    (``:Point:Object`` — ``update_point``'s promote arm mints one) can be
    returned more than once. Key the result by node id, or dedupe — a
    list-shaped consumer that does neither will double-count.
    """
    params = params or {}
    if ids is None:
        ids = list(params.get("ids") or [])
    if labels is None:
        labels = epistemic_labels_of(graph, ids)
    rows: list = []
    for label, query in zip(
            EPISTEMIC_LABELS, epistemic_label_queries(template), strict=True):
        if label in labels:
            rows.extend(graph.query(query, params=params).result_set)
    return rows


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
    if not isinstance(value, str):
        # #10520 — a RAW-WRITTEN non-scalar `direction` (a list or map, reachable
        # by hand edit, a foreign/legacy writer or a bulk import) is UNHASHABLE,
        # so BOTH the frozenset membership test below and the alias dict lookup
        # raise `TypeError: unhashable type: 'list'` on it. That aborted every
        # reader that shares this one normalizer — including the EP LOAD path
        # (`TortoiseEP._affected_factors` -> `factor_direction` -> here), i.e. a
        # single junk property aborted a belief run it is not about. Junk is
        # ABSENT, which is the same reading `claim_class.normalize` gives a junk
        # claim-class token: `None` means absent to every consumer
        # (`direction_flows(None) == (True, True)` — the runtime's historical
        # mutual default), never a third state and never a crash.
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
