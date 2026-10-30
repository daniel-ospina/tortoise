"""Tortoise SDK — Layer 1 facade for Tortoise epistemic graph interaction.

Wraps FalkorProjection (Docker/server FalkorDB by default, embedded via path argument).
Lazy-opens on first call. Returns structured dicts, never raw FalkorDB result sets.

Builder capability catalog note (#2004 W8 / epic #1976 DM-5): this module is
referenced in the builder capability catalog (onboarding) — catalog module
'Session recorder' (``TortoiseSDK.capture_session`` is the SDK recorder
facade) — tortoise/tool_registry.py CAPABILITY_CATALOG. If you add or rename
an extractor/indexer, update the catalog reference.

⛔ SURFACE APPROVAL MANDATE (#4282, owner ruling)
    The public methods of ``TortoiseSDK`` — every ``def``/``async def`` in the
    class body whose name does not begin with ``_`` — ARE the SDK surface. You
    may NOT add, remove, or rename one without human approval. The surface is the
    contract every agent and customer integration is built on, so changing it
    materially affects customer outcomes. GET APPROVAL FROM DANIEL FIRST — repo
    `AGENTS.md` → "USER QUESTIONS" / "DECISION RELAY" — and record it before the
    PR. The full procedure is in `CONTRIBUTING.md` ("The MCP tool surface and
    public SDK methods cannot grow by accident") and
    `docs/product/sdk-surface-declaration.md`.

    `tools/surface-guard.py` is a DRIFT control, NOT an approval gate: an expansion
    that updates this class and `config/surface-manifest.yml` consistently PASSES
    it. It cannot tell an approved addition from an unapproved one — Daniel's
    review is what carries the approval.
"""
from __future__ import annotations  # noqa: I001

import contextlib
import contextvars
import hashlib
import json as _json
import logging
import os
import re
import stat
import unicodedata
from collections.abc import Callable
from contextvars import ContextVar
from time import monotonic as _monotonic
from typing import Any, ClassVar

from .domain_loader import known_kinds, register_kind
# #9572: the "nothing was named" predicate for the identity doors, ONE home
# shared with the hosted REST/commit doors and the eval lane (stdlib-only leaf,
# no cycle — see the module docstring for why it is not a private in sdk.py).
from .kind_fallbacks import named_kind
from .numeric_domain import (  # #7174: the numeric domain lives in ONE place
    reject_unrepresentable_number as _reject_unrepresentable_number,
)
from .cross_lens import DEFAULT_THRESHOLD
# #3301: the canonical OBJECT terminal vocabulary — the SAME set the four
# search legs exclude by default. Imported here so recall_state's Object
# filter never re-literals it (a copy is how the read surface drifts from
# search).
from .commit_ops import OBJECT_TERMINAL_STATUSES as _OBJECT_TERMINAL_STATUSES
# #1423 (review P1): the DECLARED, writable Subject lifecycle (ONTOLOGY §4.2),
# used by the Subject `status` write guards in `create_entity` and
# `_update_entity`. Imported so neither surface re-literals the vocabulary.
from .commit_ops import SUBJECT_STATUS_VALUES as _SUBJECT_STATUS_VALUES
from .env_truthy import env_flag, is_truthy  # #4097: the declared truthy contract
from .ids import entity_name_id as _entity_name_id
from .ids import ulid
# #2817: the S5 money derivation (prose → typed value). Module scope because the
# capture seam's `warn_on` narrowing below is derived from the CLOSED
# `DROP_REASONS` vocabulary at import time, so a renamed reason fails here
# instead of silently muting a warning. `value_normalizer` imports only
# `tortoise.money` (no cycle; `money` is already loaded via the projection).
from .value_normalizer import DROP_REASONS, normalize_money_props
from .vector_write import (  # #4520: the ONE clear-then-set vector seam
    EMBEDDING_PROPERTY,
    clear_embedding,
    clear_then_set_prefix,
    set_embedding_clause,
)
# #4282 phase 2: retired public names fail with their replacement named (#3836 (c)).
from .sdk_retired import retirement_hint
from .validity import (  # #2350: the ONE home for the precision PROP NAMES
    FROM_PRECISION_PROP,
    TO_PRECISION_PROP,
)
from .live import TERMINAL_EXCLUDED_STATUSES  # EP terminal vocabulary (shared)
from .live import _live_only  # #6597 dream coverage closure mirrors EP liveness
from .live import decay_clause, _terminal_excluded  # #2490 vacuity decay + terminal predicate
from .live import _current_status_clause  # #4058 canonical current-status allow-list
from .live import same_mitigation_assertion  # #9962 redelivery vs re-assertion
from .live import epistemic_label_queries  # #7853 epistemic-label id sweep
from .live import epistemic_disjunction  # #7853 epistemic-label traversal clause
from .live import ACCEPTED_DIRECTION_VALUES  # #7852 Layer-1 four-value direction set
from .live import direction_flows  # #7852 (source→target, target→source) flows
from .live import direction_spellings  # #7852 stored-token equivalence class
from .live import is_default_direction  # #7852 "does not differentiate" default
from .entity_identity import (  # #3633 route-then-refuse identity resolution
    ADDRESSING_SOURCE, UnaddressableEntityName, all_name_holder_ids,
    resolve_document_target_id, resolve_entity_id)
from .live import _terminal_expression  # #3142 shared Cypher terminal predicate (POSITIVE direction)
from .live import is_terminal_status  # #2498 shared terminal predicate (Python mirror)
from .live import classify_lifecycle_fanout  # #7822 fan-out-wide lifecycle verdict
from .live import LIFECYCLE_FANOUT_QUERY  # #7822 the shared fan-out statement
from .live import EPISTEMIC_LABELS  # #7902 epistemic-label write widening (shared with #7853 read widening)
from .raw_state import (  # #3998: the absent-raw state
    WRITABLE_RAW_STATES,
    raw_entry,
    validate_raw_state,
)
from .embedded_lifecycle import atexit_fast_close  # #1371: the fast-close seam
# #5498: the session read projection lives in ONE dependency-free module so the
# self-hosted CLI can derive from it too; re-exported here because the SDK read
# and `tests/test_hosted_api.py` bind it under this name.
from .session_projection import SESSION_READ_FIELDS  # noqa: F401
# #3520: the per-session extraction-status derivation — imported from the SAME
# module as the projection it is a field of, so the SDK read and the hosted
# reads cannot disagree about one session's status.
from .session_projection import session_extraction_status
from .retrieval import (DEFAULT_POOL_SIZE, _safe_session_tag,
                        is_turn_echo_row, resolve_pool_size)
from . import monitoring
# #4489: the read funnel's retrieval meter. ``metered_query`` wraps
# ``tortoise_fts_query`` so ONE product read notes ONE query and its wall time
# without any caller cooperation; it is a no-op (one ``ContextVar.get()``) when
# no tally is armed. Imported at module level because it is stdlib-only at
# import (its ``metering`` dependency is deferred), so no cycle is introduced.
from .retrieval_metering import metered_query as _metered_query
# #2351 (epic #2349 E-derived): the asserted-vs-derived claim class — the ONE
# home for the vocabulary, the override guard and the read contract.
from . import claim_class as _claim_class
from . import file_indexer  # noqa: F401 — binds the classifier/identity module (§4.4); sourceKind registration is registry-owned (source_credibility.SOURCE_KIND_DEFAULTS)
from .projection import FalkorProjection
# #7951 §17.1: the single-writer scope the live doors open around their fold.
from .projection import _declares_live_write_lane, _live_write_scope
# #9206 §17.1: the APPLY-FIRST declaration — the cache write is the commit point
# and the record that describes it follows, BY DECISION. A declaration, not a
# bypass: it is honest only where a record IS still appended (the census pin
# refuses a door that appends none — the #9068 defect).
from .projection import (
    _declares_apply_first_write_lane,
)
# #9123 §17.1: the GRAPH-METADATA lane — a write with no journal record that
# describes it, and whose loss a rebuild absorbs.
from .projection import _graph_metadata_write_scope
# #7951 review P1: the journal payload is built from the WRITE INTENT, so it
# must state what the store will HOLD — the transport model lives in the
# projection package (one home), not in a second copy at this call site.
from .projection import _stored_prop_value, _UNSTORED
from .projection.entities import (  # #3998: the declared :Source surface
    _SOURCE_NODE_PROP_NAMES,
    _SOURCE_SERVER_MANAGED_PROPS,
    filter_source_props,
)
# #8006: the `search_keys` flatten rule has ONE home — the projection module that
# owns the Point prop contract (`_POINT_DECLARED_PROPS` / `_POINT_LIST_PROPS` /
# `_upsert_point_props`). The SDK write doors below call it under their
# historical private name, so the three write doors and the replay share ONE
# implementation instead of two that can drift — which is exactly what #8006
# was: the SDK doors flattened, the shared projection writer did not, so
# `EventAPI.add_point` stored NOTHING.
from .projection.entities import (
    flatten_search_keys_prop as _flatten_search_keys_prop,
)
# #10122: the FULLTEXT index FIELD TABLE (the one that CREATES the indexes).
# The Point `name` advisory below reads the Point's fields OFF it rather than
# re-typing `content`/`search_keys` — so the advisory cannot drift from the
# index, and an index change that adds a field retires it with no second edit.
from .projection import _FULLTEXT_INDEX_LABEL_FIELDS
from .projection import _ANNOTATOR_PROPS as _ANNOTATOR_PROP_NAMES
from .projection import _ENTITY_ID_PROP  # #3998: the write's own Source predicate
from .projection import is_missing_graph_error  # #2163: absent-graph family == success
from .graph_ops import counts_capture_ops, phased  # #3359: per-capture graph-op accounting
from .quota import (
    DOCUMENT_METER_PREDICATE,
    MAX_EXTRACTIONS_PER_TURN,
    MAX_SESSION_TURNS,
    is_metered_document_kind,
    refuse_document_writer_demotion,  # #5140: the document writers' loud refusal
    stored_source_document_kinds,     # #5140: ONE reader of the meter key
)
from .canonical import derive_batch_id
# #4911: the ONE credential scrubber for stored turn text. Stdlib-only module
# (no `tortoise` imports), so this adds no import-cycle risk to this hot module.
from .security import redact_secrets
import threading
from datetime import UTC

# P0 Group 3: register custom kinds for diary + checkpoint
register_kind("diary")
register_kind("checkpoint-item")
register_kind("option")    # used by file_decision (#133)
register_kind("evidence")  # used by file_decision (#133)
# #6671: `criterion` is a DECIDE_PART_KINDS member (the decide rubric's
# criteria axis: options/criteria/findings) and was the ONE decide-part kind
# missing from this block — so a criterion write from the decide flow
# (`graph-scripts/decide.py`, `tortoise decide`; NOT `file_decision`, which
# writes decision/option/evidence only) warned "Unrecognized pointKind" while
# the SDK itself already treated it as a decision part.
register_kind("criterion")

# Valid status values for Point nodes (used by update_point status validation)
# #432: claim lifecycle vocabulary — draft → live → retracted → superseded
# (plus outdated/archived). challenged is a DERIVED condition (NAND operator
# edge on a live point), NOT a stored status.
POINT_STATUS_VALUES = frozenset({'draft', 'live', 'retracted', 'superseded', 'outdated', 'archived'})

# ── #2199 decide-part calibration + baseline provenance ────────────────
# Decision parts filed through the decide tooling (create_point / commit_ops /
# mitigate_operator) are HUMAN-AUTHORED judgment — they carry no document
# Source to inherit belief from (a Source only imparts belief when tiered
# T0-T4, at EP-run time). They are born LIVE with an EXPLICIT, provenance-
# recorded starting belief so the documented decide flow ranks on the first
# attempt (no promote + calibrate chores, no CalibrationError). The default
# prior is the ladder's middle rung 'medium' = Beta(3,1), mean 0.75 (issue
# #2199 knock-on decision 2: reuse the ladder, do not invent a new number).
DECIDE_PART_KINDS = frozenset({"decision", "option", "criterion", "evidence"})
DECIDE_DEFAULT_CREDIBILITY = "medium"  # → Beta(3,1) via source_credibility

# #7856: a GOAL's ACHIEVEMENT state — a SEPARATE axis from `status` (the
# draft/live lifetime). `status` says whether a claim is promoted for use;
# `goalState` says whether the goal is met. The two were conflated: the
# objective map's state was an unvalidated free string written by a throwaway
# script (`unmet`/`active`/`met`/`guideline`), so a reader could not tell a
# belief about a goal from its state, and `update_entity(id, status="met")`
# was accepted (the #2977 OBJECT_STATUS_VALUES guard is not on main). The
# vocabulary is the owner's closed set (the #7871 read-side comment: `met` /
# `active` / `blocked` / `unmet` / `superseded` / `abandoned`) plus
# `guideline` — the value the objective map already uses for the demoted
# <10-min-CI "target" (tortoise#6792 form ruling: a guideline cannot outrank a
# goal, so it is recorded as a goal state no reader treats as pass/fail).
GOAL_STATE_VALUES = frozenset({
    "met",         # achieved
    "active",      # being pursued
    "blocked",     # pursued but gated by an unmet dependency
    "unmet",       # not yet achieved and not yet being pursued
    "superseded",  # replaced by a newer commitment
    "abandoned",   # deliberately dropped
    "guideline",   # directional, no pass/fail (owner ruling #6792)
})

# #7856 defect 2: the objective map's TOP node was a `goal` POINT created
# without an explicit status, so it defaulted to `draft`. A draft point that
# is only ever a TARGET is EP-inert — `create_operator` promotes only the
# SOURCE (#131) — so the top of the cascade could never influence a verdict
# while `converged=True` was still reported. A goal that can never influence a
# verdict must not be creatable by default: goal-kind Points are born LIVE
# unless the caller passes an explicit status (the extraction path passes
# `status="draft"`, so it is unaffected). The canonical representation of a
# goal remains an OBJECT (ONTOLOGY §5), which is born live already; this is
# the legacy-Point-leg safety net.
GOAL_KINDS = frozenset({"goal"})


def _baseline_create_fields() -> list[tuple[str, str]]:
    """Extra CREATE-map (key, expression) pairs for the #2199 baseline (#2952:
    returned as pairs, not a pre-rendered fragment, so create_point can
    merge them with the caller's props without producing duplicate keys)."""
    return [("ep_alpha", "$ba"), ("ep_beta", "$bb"),
            ("baseline_set", "true"), ("baseline_source", "$bsrc")]


def _baseline_create_params(baseline: tuple[float, float, str]) -> dict:
    """CREATE params for a resolved (#2199) baseline: (alpha, beta, source)."""
    return {"ba": baseline[0], "bb": baseline[1], "bsrc": baseline[2]}


def _journal_snapshot_from_create_map(
        create_map: dict[str, str], create_params: dict) -> dict:
    """The journal `point` payload for a fresh Point, built from the WRITE INTENT.

    #7951 §17.1 (docs/architecture/STORAGE-ARCHITECTURE.md) requires the record
    (truth) to be appended BEFORE the cache write, so `create_point` can no
    longer journal the post-`CREATE` `get_point` read-back: the read-back does
    not exist until the cache has already been written. This builds the same
    property map from `_create_map` (key -> Cypher expression) and
    `_create_params` (the values) — i.e. exactly the properties the `CREATE`
    writes — so the record is complete without touching the cache.

    The evaluation is DELIBERATELY closed: a `_create_map` expression form this
    function does not know raises rather than guessing. A future expression
    form therefore fails the write loudly instead of silently mis-journaling a
    record that a replay would then materialise as a different node.

    Every value is put through `_stored_prop_value`, so the record states what
    the store will actually HOLD rather than the caller's raw object
    (#7951 review P1): a JSON-native value rides verbatim; a driver-coerced
    number (`Decimal`, `np.int64`, `Fraction`, ...) rides as the literal the
    engine will parse; and a value the store keeps no property for — a null, a
    map/bytes/set, a non-finite float, a NUL string, a null inside an array —
    is OMITTED, exactly as the historic read-back omitted it. A raw
    non-JSON-encodable prop reaching `json.dumps` used to raise inside
    `_emit_event`, and the new commit gate then skipped the cache write — one
    unlucky prop value silently destroyed the whole Point.

    EMBEDDING is not an exception, and the RAW form is DELIBERATE. A
    `vecf32($embedding)` vector rides RAW — `_stored_prop_value` never narrows
    one. (This DIFFERS from the pre-#7951 record, whose `embedding` came from
    the post-CREATE `get_point` read-back and was therefore `vecf32`-narrowed;
    the raw intent is the more faithful record.) It is replay-safe: the replay
    re-narrows it (`projection/entities.py::_upsert_point_props`), `consistency`
    compares at the stored width, and `embedding_verbatim` already tells the
    replay which of the two forms was written. A null or
    unusable vector is omitted here and re-added as an explicit `None` by the
    producer's own `setdefault` in `_emit_event` (PRESENCE IS OWNERSHIP,
    #5004), so the key is still on every snapshot a PointAdded emits.
    """
    out: dict = {}
    for key, expr in create_map.items():
        if expr == "true":
            val = True
        elif expr == "false":
            val = False
        elif expr.startswith("$"):
            val = create_params[expr[1:]]
        elif expr.startswith("vecf32($") and expr.endswith(")"):
            val = create_params[expr[len("vecf32($"):-1]]
        else:
            raise ValueError(
                "#7951: `_create_map` carries an expression form the journal "
                f"snapshot builder does not know: {key!r} -> {expr!r}. A new "
                "form must be taught here (and its replay clause checked) "
                "before create_point can journal a complete record."
            )
        stored = _stored_prop_value(val)
        if stored is not _UNSTORED:
            out[key] = stored
    return out

# baseline_source token family (issue #2199 direction, 2026-09-03):
#   set-by-author         — belief stated directly by whoever filed the point
#                           (human OR agent; the species is already tracked in
#                           authoredBy, so the token stays author-agnostic)
#   inherited-from-source — belief inherited from the source's trust rating at
#                           confidence time
#   system-default        — no belief stated when filed; the system applied its
#                           standard starting belief (decide-part default)
# Renamed from the pre-#2199 spellings 'explicit' → 'set-by-author' and
# 'inherited' → 'inherited-from-source' (one-time upgrade migration:
# graph-scripts/2199_baseline_source_rename.py — old and new spellings never
# coexist). system-default is new (no deployed data, no migration needed).
BASELINE_SOURCE_SET_BY_AUTHOR = "set-by-author"
BASELINE_SOURCE_INHERITED = "inherited-from-source"
BASELINE_SOURCE_SYSTEM_DEFAULT = "system-default"
BASELINE_SOURCE_TOKENS = frozenset({
    BASELINE_SOURCE_SET_BY_AUTHOR,
    BASELINE_SOURCE_INHERITED,
    BASELINE_SOURCE_SYSTEM_DEFAULT,
})

# Human display copy for the provenance tokens — ONE shared anchor phrase
# ('Starting belief:') with per-token full form (issue #2199 design pin).
# calibrate_summary and audits route every token through this map so the copy
# never drifts per surface.
BASELINE_SOURCE_DISPLAY: dict[str, str] = {
    BASELINE_SOURCE_SET_BY_AUTHOR: (
        "Starting belief: stated by the author when this was filed."
    ),
    BASELINE_SOURCE_INHERITED: (
        "Starting belief: inherited from its source's trust rating "
        "(e.g. high), applied when confidence is computed."
    ),
    BASELINE_SOURCE_SYSTEM_DEFAULT: (
        "Starting belief: none was stated when this was filed, so the system "
        "applied its standard starting belief (medium). You can set your own "
        "any time — e.g. recreate with credibility=, or call "
        "set_point_baseline()."
    ),
}


# ── #9063: THE CALIBRATION GATE'S DEMAND UNIVERSE, stated once ──────────
#
# The gate (_ensure_calibrated) promises "no EP surface runs uncalibrated EP
# silently" (#1157/#344/#7478), so its universe must be the node set EP
# CONSUMES — not whatever a human AUDIT surface happens to list. It used to be
# the second: the gate intersected ``calibrate_summary()``'s rows, so every
# filter that belongs to the audit silently became a blind spot in the gate.
# #9063 measured a legacy evidence Point (``is_operator`` absent — a shape EP's
# own participant predicate ADMITS, ep.py / #3139 / #3154) being consumed and
# STAMPED with a posterior while the gate saw nothing. The query in
# ``_uncalibrated_ep_participants`` is now the ONE declaration of that universe,
# anchored on EP's own non-operator / live shape.
#
# TWO filters survive ON PURPOSE, and neither is this gate's call to change:
#   * EVIDENCE KINDS: a Point of a DERIVED-belief kind (decision, plan,
#     strategy, vision, goal, target...), or the ``assessment`` record
#     ``assess_source`` writes (#398), is not an evidence claim; demanding a
#     prior of it is a different belief model.
#   * ``:Point`` only: EP propagates confidence across Point/Subject/Object/
#     Event (#7813 owner ruling, #7853 read widening) and the prior READ path
#     is already four-label, but an entity cannot be calibrated at all today
#     (``set_point_baseline`` writes :Point), so gating the labels BEFORE
#     making them calibratable is a #8175-shaped trap — a gate that can never
#     be satisfied.
#
# Both are the SAME open question — which node kinds must carry a prior? —
# which #9063 reserves to the owner (belief model). WIDENING EITHER TUPLE IS
# THAT DECISION; they are single constants because the query is already
# id-anchored and EP-predicated.
_CALIBRATION_EVIDENCE_KINDS = ("statement", "observation", "hypothesis")
_CALIBRATION_GATED_LABELS = ("Point",)

#: The Cypher set of leading/trailing characters that make a string id
#: UNCALIBRATABLE (#9063) — the MEASURED unresolvable class, not a guess.
#: ``trim()`` is NOT usable here: on this engine it strips ASCII space only
#: (``trim('\tabc\t')`` returns the string unchanged), which left tab- and
#: newline-padded ids in the universe while the scoped, index-anchored arm
#: (``n.id IN $ids``) could not resolve them — the arms disagreeing in the
#: fail-open direction.
#:
#: The membership is measured, in BOTH directions (embedded engine and a
#: FalkorDB server, #9261 review): exactly the ASCII whitespace characters are
#: unresolvable by a labelled id equality. Everything else must stay IN the
#: universe — in particular NBSP, which ``create_point`` ACCEPTS, which
#: ``MATCH (n:Point {id:…})`` resolves, and which ``set_point_baseline``
#: satisfies; excluding it made the gate pass while EP stamped the node
#: (measured fail-open). ``\v``/``\f`` are the mirror case: refused by every
#: writer and unresolvable, so a demand there is unsatisfiable (#8175).
#: ``left``/``right`` are supported on both engines.
#: The whitespace family a gate conjunct strips: the ID classifier and the kind
#: normalisation SHARE the ASCII base; only the kind normalisation adds NBSP (see
#: the two constants below, each with the measurement that set it).
#:
#: Why enumerate, rather than ``trim()``: ``trim()`` strips ASCII space ONLY
#: here, and ``=~`` (regex) is unavailable on this surface (measured:
#: UnsupportedCypherOperatorError).
#:
#: ⚠️ DECLARED BOUNDARY: Unicode spaces beyond NBSP (em space, ideographic
#: space, ZWSP, BOM…) are NOT in this family. A ``pointKind`` padded with one of
#: those escapes the kind normalisation while EP consumes the node and stamps it
#: — measured, and pre-existing (``origin/main`` compared the raw kind, so every
#: spelling escaped). Each further code point costs one ``replace()`` on every
#: scanned row (measured: seven characters ≈ +5.7 ms at 2,000 Points; the full
#: Unicode list timed out on an eight-node fixture), so the boundary stays
#: recorded here rather than paid for on the hot path.
#:
#: #9287 closed the REACHABLE half of that boundary at the WRITE DOOR instead:
#: ``create_point``/``update_point`` now REFUSE a ``pointKind`` carrying any
#: non-identifier character (``_refuse_invisible_kind`` below), so the SDK can
#: no longer mint a kind this filter cannot classify. Reachability of the
#: remaining spellings is therefore an IMPORTED / hand-written row (a raw Cypher
#: ``SET n.pointKind = …``), which is exactly what this filter is for: such a
#: node IS demandable and clearable, so the gap is one of coverage, not of an
#: unsatisfiable demand. The two halves are documented together on purpose —
#: widening this family without touching the door (or vice versa) re-opens
#: #9287, where the demand and the door disagreed on what a kind may be.
#: For IDS: exactly the ASCII whitespace class — measured to be the set an id
#: equality cannot resolve. NBSP is deliberately NOT here: ``create_point``
#: ACCEPTS an NBSP-padded id, the labelled equality resolves it and
#: ``set_point_baseline`` satisfies it, so excluding it made the gate pass while
#: EP stamped the node (measured fail-open, one review cycle ago).
_WHITESPACE_CHARS = (" ", "\t", "\n", "\v", "\f", "\r")

#: For KINDS: the same class plus NBSP (a kind is matched by string membership,
#: not by an id equality, so the resolvability argument above does not apply).
_KIND_WHITESPACE_CHARS = (*_WHITESPACE_CHARS, "\u00a0")

#: Cypher string literals for the control characters (a raw one would break the
#: query); the rest of the family is embedded verbatim — none of them is a quote
#: or a backslash.
_CYPHER_CHAR_LITERAL = {
    "\t": r"'\t'", "\n": r"'\n'", "\r": r"'\r'",
    "\v": r"'\v'", "\f": r"'\f'",
}


def _cypher_ws_literal(ch: str) -> str:
    return _CYPHER_CHAR_LITERAL.get(ch) or f"'{ch}'"


#: The same family as a Cypher list literal.
_ID_WHITESPACE_SET = ", ".join(_cypher_ws_literal(c) for c in _WHITESPACE_CHARS)


def _strip_whitespace(expr: str) -> str:
    """Cypher: ``expr`` with every whitespace character of the family removed.

    ``trim()`` is NOT enough — on this engine it strips space only, so a
    ``pointKind`` padded with any other member of the family escaped an
    exact-membership check while EP (which reads no kind at all) consumed and
    stamped the node (measured, and NOT a regression: ``origin/main`` compared
    the raw kind, so it escaped every spelling). Removing the characters
    wherever they occur also folds an INTERNAL separator (``'state ment'``),
    which is fail-closed: EP consumes that node too, so demanding a prior for it
    is the safe direction.
    """
    out = expr
    for ch in _KIND_WHITESPACE_CHARS:
        out = f"replace({out}, {_cypher_ws_literal(ch)}, '')"
    return out


#: #9287: what a ``pointKind`` MAY contain — the WRITE-DOOR half of the
#: boundary ``_KIND_WHITESPACE_CHARS`` declares above.
#:
#: A kind is an IDENTIFIER, not prose. The rule is stated POSITIVELY and over
#: PROPERTIES, not examples: a character is admissible when it is PRINTABLE
#: (``str.isprintable`` is False across ``Cc``/``Cf``/``Cs``/``Co``/``Cn`` and
#: the ``Z*`` separators — the whole invisible half of Unicode, where every
#: escape in this issue lived) and is not one of the measured NON-RENDERING code
#: points below (Unicode files those under printable categories, so nothing
#: decidable reaches them). The kind is probed in NFC form, so a decomposed
#: accent (``'cafe\\u0301'``) is the same identifier as its composed spelling;
#: the STORED value is never rewritten.
#:
#: Why properties and not a blacklist: this fix was opened TWICE on enumerated
#: rules. A category blacklist missed ``U+3164`` HANGUL FILLER (``Lo``, renders
#: blank); the enumerated fix for that missed ``U+E0100``..``U+E01EF``
#: (variation selectors-17+) and ``U+1D159`` (MUSICAL SYMBOL NULL NOTEHEAD,
#: ``So``) — each measured accepted-and-gate-escaping on the head it was found on
#: (#9591 review cycles 2 and 3).
#:
#: ⚠️ THE RESIDUAL, declared rather than implied: a printable code point whose
#: glyph renders nothing (or renders a no-op) cannot be recognised by any Unicode
#: property — "does this draw ink" is not machine-decidable. So that class is an
#: explicit MEASURED list below, and the tests pin both halves: the generative
#: `test_the_door_accepts_no_invisible_code_point` walks all 0x110000 code points
#: (accepted ⇒ printable, at BOTH an edge and an interior position) and asserts
#: every listed code point is refused, so the next member of the class cannot
#: slip in silently.
#:
#: The other direction is pinned too, and it is the one that matters for the
#: calibration gate: the read path's normalisation family must be a SUBSET of
#: what this door refuses — `test_the_door_refuses_every_character_the_gate_strips`.
#: (The reverse is not true and must not be claimed: the door accepts 'a', '-'
#: and ':', which the read path does not strip and has no reason to.)
#:
#: Why the READ path stays a small enumeration and the DOOR carries the rule:
#: each code point the read path strips costs one ``replace()`` on every scanned
#: row (measured: seven characters ≈ +5.7 ms at 2,000 Points; the full Unicode
#: space list timed out the gate on an eight-node fixture), while the door pays
#: it once per write.
_KIND_NON_RENDERING_CHARS = frozenset({
    "\u115f",  # HANGUL CHOSEONG FILLER (Lo) — renders blank
    "\u1160",  # HANGUL JUNGSEONG FILLER (Lo)
    "\u3164",  # HANGUL FILLER (Lo)
    "\uffa0",  # HALFWIDTH HANGUL FILLER (Lo)
    "\u2800",  # BRAILLE PATTERN BLANK (So)
    "\U0001d159",  # MUSICAL SYMBOL NULL NOTEHEAD (So)
    "\u034f",  # COMBINING GRAPHEME JOINER (Mn) — a visible no-op
    "\u17b4",  # KHMER VOWEL INHERENT AQ (Mn)
    "\u17b5",  # KHMER VOWEL INHERENT AA (Mn)
}) | frozenset(chr(cp) for cp in range(0x180b, 0x1810)) \
  | frozenset(chr(cp) for cp in range(0xfe00, 0xfe10)) \
  | frozenset(chr(cp) for cp in range(0xe0100, 0xe01f0))


def _kind_non_identifier_char(kind: str, i: int) -> bool:
    """True when ``kind[i]`` cannot be part of an identifier.

    The POSITIVE rule (see ``_KIND_NON_RENDERING_CHARS``): a character is part
    of an identifier when it is PRINTABLE and does not render blank, or when it
    is a plain INTERIOR space. Everything else — separators, controls, format
    marks, unassigned and non-rendering code points — is refused.

    ONE predicate for both halves of this module's kind rule: the refusal
    (``_kind_invisible_char``) and the suggested fix (``_kind_suggestion``) read
    it, so the value a refusal offers is by construction a value the door
    accepts — a suggestion the caller cannot use is the loop #9591's first
    review found (``strip()`` cannot remove a ``Cf`` mark, and ``repr()``
    escaping hands back a DIFFERENT string).
    """
    ch = kind[i]
    if ch == " ":
        # A plain INTERIOR space is a legitimate separator in an
        # open-vocabulary kind ('technical debt') and the gate strips it
        # wherever it occurs, so it can never be an escape. A LEADING/trailing
        # one is the padding this door exists to refuse.
        return not (0 < i < len(kind) - 1)
    if ch in _KIND_NON_RENDERING_CHARS:
        return True
    return not ch.isprintable()


def _kind_invisible_char(kind: str) -> str | None:
    """The first character of ``kind`` that cannot be part of an identifier.

    ``' '`` is exempted when it is INSIDE the string: a plain interior space is
    a legitimate separator in an open-vocabulary kind (``'technical debt'``),
    and it can never make a node escape the gate's classification — the read
    path strips it wherever it occurs, so ``'state ment'`` is already demanded
    as the ``statement`` it is. Every character the POSITIVE rule above does not
    admit (see ``_KIND_NON_RENDERING_CHARS``)
    is refused in any position, including the interior: ``'state\\u2003ment'`` is
    either a separator or padding, and guessing is how a kind silently becomes
    a different kind.
    """
    # NFC probe: a DECOMPOSED spelling ('cafe' + U+0301) is the same
    # identifier as its composed form, and refusing it would make the door
    # depend on which normalisation form the caller happened to produce. The
    # stored value is untouched — normalising what we WRITE would move the dedup
    # identity `(pointKind, content_hash)` behind the caller's back, which is why
    # this refuses rather than rewrites in the first place.
    probe = unicodedata.normalize("NFC", kind)
    for i in range(len(probe)):
        if _kind_non_identifier_char(probe, i):
            return probe[i]
    return None


def _kind_suggestion(kind: str) -> str:
    """``kind`` with its non-identifier characters removed, for the message.

    One home with ``_kind_invisible_char``: the same categories and the same
    interior-space exemption, so the value a refusal SUGGESTS is a value the
    door accepts — a suggestion the caller cannot use is the loop #9287's
    review found (``strip()`` cannot remove a ``Cf`` mark).

    An interior SPACE SEPARATOR (``Zs``/``Zl``/``Zp`` — em space, ideographic
    space, NBSP…) is folded to ONE ASCII space instead of being deleted (#9591
    review cycle 7, P3): deleting it joins the words around it, so
    ``'technical\\u2003debt'`` was suggested back as ``'technicaldebt'`` — a
    silently DIFFERENT identifier, which is the hazard the refusal exists to
    avoid. At an EDGE the separator is padding (the door refuses it there), so
    it is dropped; ``Cf`` marks (ZWSP, BOM, soft hyphen) are joiners/no-ops and
    are still deleted — ``'state\\u200bment'`` really is ``'statement'``.
    """
    out: list[str] = []
    for i, ch in enumerate(kind):
        if not _kind_non_identifier_char(kind, i):
            out.append(ch)
            continue
        if (0 < i < len(kind) - 1
                and unicodedata.category(ch) in ("Zs", "Zl", "Zp")
                and out and out[-1] != " "):
            out.append(" ")
    return "".join(out).strip()


def _clean_model_kind(value, *, default: str = "statement",
                      door: str = "extractor") -> str:
    """#9287: canonicalise a kind an LLM produced, BEFORE a write door sees it.

    Model output is UNTRUSTED DATA, not an authored spelling, and the two write
    seams it reaches are opposites: the SDK doors / the journal seam REFUSE an
    invisible-character kind (the durable guarantee), while a capture must not
    abort because a model emitted a stray em-space or zero-width mark inside
    its JSON. So the producer keeps its own output in contract here — cleaned
    once, with a WARNING naming the value — instead of either storing the
    padulation (which the calibration gate cannot classify while EP consumes
    the node) or failing the whole extraction.

    An empty/blank kind falls back to ``default`` for the same reason: nothing
    a model returns should be able to mint a kind the gate cannot demand
    (#9592 is the gate-side half of that).
    """
    kind = "" if value is None else str(value)
    if kind and _kind_invisible_char(kind) is None:
        return kind
    fixed = _kind_suggestion(kind) or default
    _logger.warning(
        "%s: model returned pointKind %r — using %r (#9287)",
        door, kind, fixed,
    )
    return fixed


def _refuse_invisible_kind(prop: str, value, *, door: str) -> None:
    """#9287: refuse a kind the calibration gate cannot classify.

    The gate demands a prior for every node EP consumes whose ``pointKind``
    normalises to an evidence kind; EP itself reads NO kind. So a kind the gate
    cannot classify is a node that is consumed and stamped while the gate stays
    silent — measured at ``HEAD``: ``create_point('\\u2003statement\\u2003',
    'x')`` was stored verbatim, ``_uncalibrated_ep_participants(None)`` answered
    ``[]``, and a ``require_calibration=True`` run stamped it
    (``ep_alpha 1.287``/``ep_beta 1.242`` — the ``Beta(1, 1)``-as-calibrated
    class #7478 exists to prevent).

    Refusing is the repair the issue asks for (option 1) and it is the door's
    own posture for a value its post-write check cannot resolve: it NAMES the
    argument and the offending code point, so the author fixes the spelling,
    instead of a silent rewrite of what they asked for (``pointKind`` is part of
    the dedup identity ``(pointKind, content_hash)``, so a silent canonical
    form would also move the identity behind the caller's back).

    Non-string kinds are NOT examined: ``create_point(42, …)`` is out of
    contract, writable on ``origin/main``, and the gate's ``toLower(typeof(…))``
    guard already keeps it out of the universe (#9063); refusing it here would
    be a different change with its own blast radius.
    """
    if not isinstance(value, str):
        return
    bad = _kind_invisible_char(value)
    if bad is None:
        return
    # The suggestion is derived from the refusal itself, never ``value.strip()``:
    # ``strip()`` removes WHITESPACE only, so for the ``Cf`` half of the family
    # (ZWSP, BOM, soft hyphen, ZWNJ/ZWJ) it returns the value UNCHANGED and a
    # caller who follows the message verbatim loops on the identical error
    # (measured: ``'\ufeffstatement'.strip() == '\ufeffstatement'``).
    suggestion = _kind_suggestion(value)
    # ⛔ NOT ``{suggestion!r}``: ``repr`` ESCAPES a ``Cf`` character into its
    # six-character backslash form, so the message would hand the caller a
    # DIFFERENT string (`'\\ufeffstatement'` — literally backslash-u-…) that the
    # door happily accepts and stores under a name they never had. The suggestion
    # is interpolated raw, inside quotes, so it is the value itself.
    tail = (
        f"Pass the kind without the {bad!r} — e.g. '{suggestion}'."
        if suggestion
        else "The kind is only invisible characters — supply a name."
    )
    # ⛔ STATE THE REASON THAT IS TRUE FOR THIS CHARACTER (#9591 review cycle 4):
    # the read path HAPPENS to normalise the ASCII/NBSP family, so claiming "the
    # gate would not demand a prior" for those is false — measured: an imported
    # row with `' statement'` IS demanded (pinned by
    # `test_out_of_contract_kind_shapes_do_not_abort_the_gate`). The door is the
    # guarantee either way; only the out-of-family half is the measured fail-open.
    reason = (
        "the read path's own whitespace normalisation happens to fold this "
        "spelling today, but the door — not a per-row scan — is the guarantee, "
        "and a kind is an identifier"
        if bad in _KIND_WHITESPACE_CHARS
        else "the calibration gate classifies kinds after its own normalisation, "
             "so this spelling is a DIFFERENT kind: the gate would not demand a "
             "prior for it while EP still consumed and stamped the node"
    )
    raise ValueError(
        f"{door}: {prop} must be an identifier, but {value!r} carries "
        f"{bad!r} (U+{ord(bad):04X}). A kind is matched by exact membership, "
        f"so {reason} (#9287). {tail}"
    )


#: id types an id-equality can resolve. A hand-written array id is not one of
#: them (and is unhashable in Python, which crashed the post-pass before this
#: allow-list); ``integer``/``float``/``boolean`` ids ARE resolvable and
#: satisfiable (measured), so they stay in the universe.
_ID_RESOLVABLE_TYPES = "['string', 'integer', 'float', 'boolean']"


# #913: statuses that make a Point STALE for review_connections(mode=prune)
# — terminal states plus the legacy outdated flag (supersede/invalidate set
# status='superseded'/'outdated' AND/OR outdated=true). draft/live are the
# only statuses a connection to is NOT stale.
#
# #4058: derived from the canonical Point exclusion, never a second literal.
# The by-value copy that used to sit here was ``live.TERMINAL_STATUS_VALUES``,
# so it OMITTED the legacy ``deprecated``: a connection to a deprecated Point
# was not flagged stale while every read surface (search, recall, EP) excluded
# that Point as non-current — the same "a reader re-declares a subset" class as
# #2901. The canonical set is
# live.TERMINAL_EXCLUDED_STATUSES = TERMINAL_STATUS_VALUES | {deprecated}.
STALE_TERMINAL_STATUSES = TERMINAL_EXCLUDED_STATUSES

# The structural rels `supersede_point` transfers in its 2b leg (#122). Single
# source of truth for the WRITER and for every mirror of it: the MCP dry-run
# preview imports this name rather than re-listing the rels, so a rel added to
# (or dropped from) the transfer cannot drift out of the preview. Superset of
# `projection.edges.STRUCTURAL_REL_LABELS` (which carries only the
# snapshot-derivable subset): `aboutAction` (Action dissolved in Ontology v3.0)
# and `wasDerivedFrom` (A10 raw family) transfer but are never snapshot-recreated.
SUPERSEDE_STRUCTURAL_RELS = (
    'aboutSubject', 'aboutObject', 'aboutAction', 'aboutEvent',
    'aboutPoint', 'aboutDocument', 'extractedFrom', 'wasDerivedFrom',
)

# Epic #902 W4 A0 — single-source valid-value sets for ingest() (consumed by
# the SDK validation AND the MCP pre-validation so the two layers cannot
# drift; INGEST_CONTRACT.md §2/§5 pins the exact values + error shapes).
INGEST_GRANULARITIES = ("bulk", "granular")
INGEST_PROMOTION_POLICIES = ("gated", "auto")


# ── Session LLM extraction (epic #909 M2 — issue #822) ─────────────────────
# The deterministic regex extraction loop is REMOVED as a product path: LLM
# extraction is the DEFAULT (and only) capture extraction. These helpers build
# the M2 two-stage LLMExtractor (tortoise/extractor.py) from the configured
# provider and run it over a conversation; both hosted_api.capture_session and
# TortoiseSDK.capture_session consume them so the two copies stay in sync.

# Provider priority when MULTIPLE keys are set (first configured wins — the
# deploy-time decision is which key is present; a key is only ever sent to the
# provider that issued it, #329). Order mirrors tortoise.ingest._PROVIDERS.
_SESSION_LLM_PROVIDER_PRIORITY = ("openrouter", "deepseek", "openai", "gemini")

# Default model per provider when TORTOISE_SESSION_LLM_MODEL is unset. The
# provider/model choice is a product decision (deploy-time) — these are
# cheap-tier defaults matching the analyzer's model choices (analyze.py
# _LLM_PROVIDERS) and session_indexer's whitelist family.
#
# #4129: every id here must be one the provider actually SERVES, because a
# provider answers 200 to a retired id and silently serves a different model.
# api.deepseek.com still accepts "deepseek-chat" and serves "deepseek-flash",
# so naming the retired id did not fail — it silently ran a model nobody
# configured. Verified against GET /models on 2026-10-05, which serves exactly
# ["deepseek-flash", "deepseek-v4-pro"]. Re-check with that endpoint before
# changing an id; models.OpenAICompatModel also warns at call time when the
# served id diverges from the requested one.
_SESSION_LLM_DEFAULT_MODELS = {
    "openrouter": "deepseek/deepseek-chat",
    "deepseek": "deepseek-flash",
    "openai": "gpt-4o-mini",
    "gemini": "gemini-2.0-flash",
}

#: #3892 (owner ruling 2026-09-18): the additive warning a KEYLESS capture
#: carries — the session's turns were stored (and are therefore searchable),
#: but no memory points were extracted. One canonical string so the receipt
#: and its tests cannot drift, and so a keyless capture is NEVER silent.
_CAPTURE_NO_PROVIDER_WARNING = (
    "no LLM provider key configured (set e.g. OPENROUTER_API_KEY or "
    "DEEPSEEK_API_KEY) — the session's turns were STORED and remain "
    "searchable, but LLM extraction into memory points was skipped"
)

#: #3892: the truthful ``extraction_mode`` for the keyless capture. Named so
#: a consumer can tell "no provider configured" apart from the other
#: zero-extraction states ("empty", "error", "replayed").
_CAPTURE_NO_PROVIDER_MODE = "no-provider"


def _capture_redaction_warning(count: int) -> str:
    """#4911: the additive warning a capture carries when it redacted a secret.

    One canonical string, shared with ``hosted_api._capture_session_impl``
    (byte-parity: both lanes must describe the same event identically), naming
    WHAT happened and that the stored text is therefore INCOMPLETE — the
    "visible, never a silent cut" half of the control.
    """
    return (
        f"{count} credential-shaped span(s) were redacted from this "
        f"capture's turn text before it was stored (marked "
        f"`[REDACTED:<kind>]` in place) — the stored turns are complete "
        f"except for those spans"
    )


def _journal_write_failure_warning(count: int) -> str:
    """#4240 review F1: the additive warning a capture carries when a journal
    append failed on a CONFIGURED journal.

    One canonical string, shared with ``hosted_api._capture_session_impl``
    (byte-parity: both lanes must describe the same event identically — the
    hosted lane builds its OWN receipt and never calls ``capture_session``, so
    without this the disclosure would exist only on the SDK lane), naming that
    THIS capture's records never reached the journal and a later rebuild will
    therefore lose them. Deliberately independent of the capture's ``ok`` flag:
    it NAMES the journal residue; the outcome is decided by what actually
    persisted. (#7951: since §17.1's outbox ordering a failed append means the
    cache write is skipped too, so a capture that raises this warning is now
    normally ``partial`` — the warning remains additive, never a substitute for
    the outcome.)
    """
    return (
        f"journal append failed {count}x — this capture is live-only and will "
        f"not survive a rebuild (#4240)"
    )


#: #3892: a keyless session re-captured WITH a key while the deployment is on
#: the NON-convergent M2 lane. The re-attempt is refused (re-running M2 could
#: mint duplicate claims), so the re-capture replays — said OUT LOUD, because
#: "already captured" would be a false statement of this state and the remedy
#: is one env var away. Shared by ``sdk.capture_session`` and the hosted
#: capture lane so the two surfaces disclose the SAME state in the SAME words.
_CAPTURE_KEYLESS_UPGRADE_REFUSED_WARNING = (
    "this session's turns were stored WITHOUT a provider key and no "
    "extraction has ever run for it; extraction was NOT re-attempted "
    "because TORTOISE_SESSION_EXTRACTOR=m2 selects a non-convergent lane "
    "(re-running it could mint duplicate claims) — unset it and re-capture, "
    "or capture the session under a convergent lane, to extract"
)

#: #4258 (owner ruling on #3892, comment 5723832861 — user-configurable,
#: default ON; reaffirmed by 5737715963): the truthful
#: ``extraction_mode`` for a capture whose org turned extraction OFF
#: (onboarding_state.capture_extract = false). A member of the SAME
#: ``extraction_mode`` vocabulary as ``_CAPTURE_NO_PROVIDER_MODE`` — a
#: distinct name because the reason differs (the USER chose it; a provider may
#: well be configured), so it must be folded into neither "no-provider"
#: (false) nor "llm" (would claim an extraction that did not run).
_CAPTURE_EXTRACTION_DISABLED_MODE = "extraction-disabled"

#: #4258: the canonical additive warning for the extraction-disabled capture.
#: Mirrors ``_CAPTURE_NO_PROVIDER_WARNING``'s "STORED … searchable" shape so
#: the "stored, not extracted" state reads the same in every surface's words,
#: and names the remedy (turn it back on, re-capture).
_CAPTURE_EXTRACTION_DISABLED_WARNING = (
    "extraction into memory is turned OFF for this team "
    "('capture_extract'); the session's turns were STORED and remain "
    "searchable, but LLM extraction into memory points was skipped — turn "
    "extraction back on (dashboard: Memory sources > Extract sessions into "
    "memory) and re-capture the session to extract"
)

#: #4258: the sibling of ``_CAPTURE_KEYLESS_UPGRADE_REFUSED_WARNING`` for a
#: prior stored with extraction TURNED OFF (session lane ``"disabled"``) that
#: is re-captured while the deployment sits on the non-convergent M2 lane. It
#: exists because the keyless warning would be a FALSE diagnosis here — a
#: provider key IS configured, the user's own setting was the reason nothing
#: was extracted. Names every lever that actually applies.
_CAPTURE_EXTRACTION_DISABLED_UPGRADE_REFUSED_WARNING = (
    "this session's turns were stored with extraction turned OFF "
    "('capture_extract') and no extraction has ever run for it; extraction "
    "was NOT re-attempted because TORTOISE_SESSION_EXTRACTOR=m2 selects a "
    "non-convergent lane (re-running it could mint duplicate claims) — unset "
    "it, turn extraction back on, and re-capture the session to extract"
)

#: #4258: the Session `capture_extractor` lane recorded when the team's
#: extraction setting (not a missing key) is why nothing was extracted. A
#: DISTINCT value from "none" (the keyless lane) on purpose: the M2-replay
#: disclosure treats `capture_extractor == "none"` as proof of a keyless prior,
#: so overloading it would emit the keyless warning ("stored WITHOUT a provider
#: key") for a team whose key IS configured. Shared by BOTH capture lanes so the
#: retry gate cannot diverge (a value one lane writes, the other must read).
_CAPTURE_EXTRACTOR_LANE_DISABLED = "disabled"

#: #4258: the lanes a FAILED prior capture may be re-attempted from. All three
#: minted no claims of their own and their turn ids are deterministic, so the
#: re-attempt converges. Shared (hosted + SDK) — see the lane constant above.
_CAPTURE_EXTRACTOR_LANES_RETRYABLE = (
    "v2", "none", _CAPTURE_EXTRACTOR_LANE_DISABLED)


def _session_llm_provider() -> str | None:
    """First configured session-extraction provider, or None when no provider
    key is set (the no-extraction case, #3892 — the capture itself still
    stores its turns). Mirrors ingest._PROVIDERS exactly — the same key
    set hosted_api._llm_provider_available() reports (#722 parity)."""
    from tortoise.ingest import _PROVIDERS

    for provider in _SESSION_LLM_PROVIDER_PRIORITY:
        if provider in _PROVIDERS:
            _base_url, key_env = _PROVIDERS[provider]
            if os.environ.get(key_env):
                return provider
    return None


def _session_llm_mock_enabled() -> bool:
    """TORTOISE_SESSION_LLM_MOCK=1 test seam — normalized exactly like the
    sibling gates (sdk._build_session_llm_extractor, hosted_api.
    _llm_provider_available): strip + lower, so a padded env value (" 1 ")
    can't diverge the outer gates from _extract_session_v2's inner gate
    (divergence = ValueError → HTTP 500, the #1468 failure class)."""
    return os.environ.get(
        "TORTOISE_SESSION_LLM_MOCK", "").strip().lower() == "1"


class _SessionLLMCallCounter:
    """#3824: a transparent pass-through that counts model completions.

    Calls the M2 session lane issues that produced no surviving USAGE
    (a failed request, or a model with no #2185 seam) leave no evidence at
    the cost emitter that a provider call happened — "made billed calls" and
    "made none" are the same shape (an empty ``stats``). #3747 gives the
    lane a real cost roll-up for the calls that DO report usage; this
    counter remains the disclosure for the ones that do not. The emitter
    cannot recover that fact
    from the roll-up, because the roll-up is exactly what is missing; it has
    to come from the CALL site. This wrapper is that call site:
    ``complete()`` is invoked once per provider request by ``_PointStage`` /
    ``_RelationStage`` / ``_DocumentPointStage``, and the count survives
    whatever the lane then does with the response.

    Deliberately NOT a second cost/usage accumulator: it holds no tokens, no
    charge and no per-stage envelope, so it cannot drift from a real
    roll-up. It answers one question the roll-up cannot answer about
    itself — "did this capture reach the provider at all?". Every other
    PUBLIC attribute round-trips to the wrapped model — reads delegate, and
    so do writes (``id``, ``provider``, ``usage_sink``), so
    ``LLMExtractor.version`` and the #2185 usage seam are unaffected.
    """

    def __init__(self, model):
        self._model = model
        self.count = 0

    def __getattr__(self, name):
        # A leading underscore is resolved on THIS object only, so a missing
        # ``_model`` raises instead of re-entering __getattr__ forever.
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self._model, name)

    def __setattr__(self, name, value):
        # Public attribute WRITES must reach the wrapped model too, or the
        # wrapper silently swallows them: the #2185 usage seam is attached by
        # assignment (``model.usage_sink = sink``), and a read-only
        # ``__getattr__`` would leave the UNDERLYING model's sink unset —
        # dropping every usage block the wrapper exists to keep visible.
        # ``_model`` and the wrapper-local ``count`` are NOT forwarded.
        if name.startswith("_") or name == "count":
            object.__setattr__(self, name, value)
        else:
            setattr(self._model, name, value)

    def complete(self, *args, **kwargs):
        self.count += 1
        return self._model.complete(*args, **kwargs)


def _session_llm_usage_sink(stats: dict):
    """#3747: the #2185 usage sink for the M2 session lane.

    The lane's real provider calls go through ``OpenAICompatModel.complete``,
    which fires ``usage_sink`` with the response-local usage block
    (``models._emit_usage_sink``) — but nothing ever bound a sink on this
    path, so the tokens/charge were dropped and the #3359 ``capture_cost``
    row could only DISCLOSE the calls (#3824 ``unattributed``), never price
    them. This sink accumulates each call into the SAME stage-shaped cost
    accumulator the v2 lane's ``_accumulate_call_cost`` / ``_rollup_llm``
    consume, so the emitted row is priced by the existing report path with
    no new cost concept and no change to ``hosted_api``.

    ``attempts`` rides alongside ``cost`` because ``_rollup_llm`` reads the
    call COUNT from ``stage_stats["attempts"]`` (the cost accumulator's own
    ``calls`` key stays in ``by_stage``); a sink fire IS a completed provider
    call, so the two increment together. ``usage`` may be ``None``/``{}``
    when the provider sent none — ``_accumulate_call_cost`` then discloses
    ``calls_without_usage`` instead of inventing tokens.

    ⚠️ ORDER IS LOAD-BEARING (#5822 review P2): the count is bumped AFTER
    ``_accumulate_call_cost``, never before. ``_emit_usage_sink`` swallows an
    accumulator raise by design ("a metering observer must NEVER flip a call
    outcome"), so a malformed provider payload — ``{"prompt_tokens":
    "abc"}``, or ``1e309`` which parses to ``inf`` and overflows ``int()`` —
    raises INSIDE the accumulator. Bumping the counter first would leave
    ``calls == calls_made``, so ``unattributed = max(0, calls_made - calls)
    == 0``, the roll-up stays empty, and ``_capture_cost_props`` returns
    ``None``: the call is ERASED from the report, which is strictly worse
    than the #3824 ``unattributed`` disclosure this lane had before. Counting
    after keeps the residual honest — an unaccounted-for call is disclosed on
    a row, never erased.
    """
    from tortoise.extractor_v2 import _accumulate_call_cost

    def _sink(*, provider, model_id, usage, usage_present):
        u = usage if isinstance(usage, dict) else {}
        # Count AFTER accumulating (see the ORDER note above): a raise here
        # must leave ``attempts`` untouched so the caller's residual still
        # discloses the call.
        _accumulate_call_cost(
            stats,
            prompt_tokens=u.get("prompt_tokens"),
            completion_tokens=u.get("completion_tokens"),
            cost_usd=u.get("cost"),
            provider=provider, model=model_id)
        stats["attempts"] = int(stats.get("attempts", 0)) + 1

    return _sink


def _session_llm_extractor(point_model, relation_model, *, provider=None):
    """Build the M2 ``LLMExtractor`` with #3824 call-evidence counters and
    the #3747 usage sink.

    The counters ride on the extractor (``_call_counters``) because that is
    the object ``_extract_session_llm`` holds; a fresh pair is built per
    call to this helper, so a capture's count can never leak into the next
    one (the extractor is built inside ``_extract_session_llm``, once per
    capture). The SAME lifetime rule gives the cost accumulator
    (``_cost_stats``): one per capture, never shared across captures.

    The sink is assigned through the counters, whose ``__setattr__``
    forwards public writes to the wrapped model (that forwarding exists
    precisely for this seam). A model with no ``usage_sink`` attribute
    (``MockModel`` — the offline seam) simply never fires it, so the lane
    degrades to the #3824 call-count disclosure rather than a fake roll-up.

    ``provider`` is the configured provider id, stamped on both models: the
    ``(provider, model)`` route is what ``_capture_cost_props``' ``by_stage``
    envelope is repriced from at report time (#3359), and
    ``OpenAICompatModel`` does not carry one of its own (the eval harness
    binds it at registration — no such step exists on this lane), so without
    it a cost-SILENT provider (deepseek-direct reports no ``usage.cost``)
    would land under ``unknown`` and never reprice from the versioned map.
    """
    from tortoise.extractor import LLMExtractor

    if provider is not None:
        point_model.provider = provider
        relation_model.provider = provider
    counters = [_SessionLLMCallCounter(point_model),
                _SessionLLMCallCounter(relation_model)]
    extractor = LLMExtractor(counters[0], counters[1])
    extractor._call_counters = counters
    cost_stats: dict = {}
    sink = _session_llm_usage_sink(cost_stats)
    counters[0].usage_sink = sink
    counters[1].usage_sink = sink
    extractor._cost_stats = cost_stats
    return extractor


def _build_session_llm_extractor():
    """Build the M2 LLMExtractor for session capture from the configured
    provider (or None when no provider key is set — the no-key case STORES
    the session's turns and skips ONLY the extraction, #3892; it no longer
    refuses). TORTOISE_SESSION_LLM_MOCK=1 is a test seam (precedent:
    TORTOISE_BACKUP_STORAGE=memory / RATE_LIMIT_DISABLED) that swaps in the
    deterministic MockModel so the E2E/unit suites exercise the real LLM
    pipeline shape with zero network."""
    if os.environ.get("TORTOISE_SESSION_LLM_MOCK", "").strip().lower() == "1":
        from tortoise.extractor import MockModel

        return _session_llm_extractor(
            MockModel("mock-point"), MockModel("mock-relation"))
    provider = _session_llm_provider()
    if provider is None:
        return None
    from tortoise.ingest import _PROVIDERS
    from tortoise.models import OpenAICompatModel

    base_url, key_env = _PROVIDERS[provider]
    model_id = _SESSION_LLM_DEFAULT_MODELS[provider]
    spec = os.environ.get("TORTOISE_SESSION_LLM_MODEL", "").strip()
    if spec:
        if ":" not in spec:
            # Bare <model> — resolves against the configured provider key. The
            # error message promises <model> or <provider>:<model>; before
            # #1194 a bare name fell into the partition() branch where p=<model>
            # and m="", so it could NEVER be accepted (misleading provider
            # mismatch error, or "bad model spec" when it equaled the provider
            # name) — surfaced as an uncaught 500 on every hosted capture.
            model_id = spec
        else:
            p, _, m = spec.partition(":")
            if p and p != provider:
                raise ValueError(
                    f"TORTOISE_SESSION_LLM_MODEL={spec!r} names provider {p!r} but "
                    f"{provider!r} is the configured session provider (its key is set)."
                )
            if not m:
                raise ValueError(f"bad model spec {spec!r}; expected <model> or <provider>:<model>")
            model_id = m
    return _session_llm_extractor(
        OpenAICompatModel(id=model_id, base_url=base_url, api_key_env=key_env),
        OpenAICompatModel(id=model_id, base_url=base_url, api_key_env=key_env),
        provider=provider,
    )


def _session_llm_effective_model(spec: str, provider: str | None) -> str:
    """Effective model id that would be sent to the provider wire.

    ``spec`` is the raw TORTOISE_SESSION_LLM_MODEL value (`<provider>:<model>`
    or bare `<model>`); the provider prefix is stripped for the wire id, and an
    unset spec resolves to the provider's default. The extractor build
    validates provider matching separately (ValueError on mismatch); this
    helper only resolves the model id for reporting/shape checks."""
    if not spec:
        return _SESSION_LLM_DEFAULT_MODELS.get(provider or "", "")
    _p, _, m = spec.partition(":")
    return m if (_p and m) else spec


def _session_llm_model_shape_warning(spec: str, provider: str | None) -> str | None:
    """OpenRouter route-shape warning — None when the model id is routable.

    OpenRouter addresses models as `<family>/<model>` (e.g. deepseek/deepseek-chat);
    a bare `<model>` spec (`openrouter:deepseek-chat`) builds an extractor fine
    but the FIRST capture 404s at call time — the route id is not routable even
    though the provider key is configured. Warn, never fail: only the route
    shape is suspect, the configuration itself is valid."""
    if provider != "openrouter":
        return None
    eff = _session_llm_effective_model(spec, provider)
    if "/" in eff:
        return None
    return (
        f"model {eff!r} lacks <family>/<model> shape — OpenRouter routes are "
        f"family-prefixed (e.g. deepseek/deepseek-chat); the first capture "
        f"would 404 at call time. Set "
        f"TORTOISE_SESSION_LLM_MODEL=openrouter:<family>/<model>."
    )


class _InMemoryEventLog:
    """Duck-typed EventLog (append/read_all) backing the session-capture
    extraction readback. The graph (via the EventAPI projection) is the
    durable store; this log only carries the events of the current capture so
    the caller can read the extracted Points back. No file I/O, no temp files.

    ``durable = False`` is a CONTRACT, not a comment (#9188 review): these
    records are appended here and NOWHERE else, so `EventAPI._emit` must not
    hand them to the Postgres record. The record is ``replay(journal)`` — a row
    from this buffer has no journal record behind it, could never be
    reproduced, and no replay could ever correct it (the extraction's own
    in-capture dedup even DELETEs a repeated Point from the graph, leaving the
    row asserting a Point neither the graph nor any journal holds). The door
    reads this attribute; every other log defaults to durable.
    """

    durable = False

    def __init__(self):
        self._events: list[dict] = []

    def append(self, event: dict) -> None:
        self._events.append(event)

    def read_all(self) -> list[dict]:
        return list(self._events)


# #1529 P1 (E3 owner note): whitelist of point properties that pass through
# the capture response's `props` — E3 writes source_turn_id /
# search_keys / when / quote (plus E4's span_start/span_end) via the v2
# payload point dict / the M2 folded statement dict. ONE contract for BOTH
# capture lanes (#3778): the response reports what the resolved NODE STORES,
# read back from the graph — never the payload. On the v2 INGEST SEAM a create
# must never silently drop a value they carry (the two presence normalizations
# — a None value, which the graph does not store, and an empty/blank
# `search_keys` list/tuple, popped by _flatten_search_keys_prop — are mirrored
# out of the response, so presence still agrees; a create may echo those raw
# values because it handed them to create_point), and a v2 dedup HIT writes
# NONE of these props (the session CONTAINS edge is still MERGEd), so its
# response reports the canonical's STORED props via
# `_read_capture_passthrough_props`. The env-gated M2 lane read-backs for
# EVERY entry — create and in-capture fold alike (#3778) — because its write is
# the shared EventAPI → projection path, which persists a SUBSET of the folded
# dict's fields (a list `search_keys` is dropped by the projection's
# undeclared-list policy, #2795 D2; there is no `_flatten_search_keys_prop` on
# that door), so the payload echo could advertise a field the node does not
# hold — the #2813 symptom — and a fold describes the canonical, not the
# duplicate. Either way a field the node does not hold is omitted, never
# echoed from the payload. Deliberately a
# WHITELIST (not a blacklist): folded statement dicts carry internal
# projection state (provenance run_id/source, status, createdAt, operator,
# speaker) that must never leak into the public capture response. E3 (#1535)
# landed search_keys/when/quote on the v2 payload — extend _CAPTURE_
# PASSTHROUGH_ORDER below, keep the no-clobber turn-point guard (MERGE SET
# list excludes these).
#
# #2949 (review F4): ONE ordered declaration is the source of truth. The write
# path selects by membership in the derived whitelist; the dedup-hit read-back
# builds its RETURN clause AND its zip order from this SAME tuple. Adding a
# fifth E3 field is then a one-line change here — the node stores it and the
# response reads it back — instead of the old split (frozenset whitelist +
# hand-written inline RETURN) where a newly whitelisted field was stored but
# silently omitted from the dedup reply, re-minting the #2813 class on the
# dedup path.
#
# #3945 (deliberate exception): the capture CREATE site also persists ONE
# non-whitelist property — `validFrom`, mapped from the `when` slot
# (docs/ONTOLOGY.md §4.7: one slot, two spellings). It is intentionally
# outside this declaration and outside the dedup read-back/response surface:
# the whitelist stays the source of truth for the passthrough fields only —
# E3 quote/when/search_keys/source_turn_id plus E4 span_start/span_end.
# Do NOT "fix" the divergence by adding `validFrom` here — that would
# widen the public capture response and re-open the #2949 create/dedup
# response-parity asymmetry.
_CAPTURE_PASSTHROUGH_ORDER = ("quote", "when", "search_keys",
                              "source_turn_id",
                              # E4 (#5007): the verbatim span pointer
                              "span_start", "span_end")
_CAPTURE_PASSTHROUGH_PROPS = frozenset(_CAPTURE_PASSTHROUGH_ORDER)

#: #2817 route (2) — the drop reasons the CAPTURE WRITE SEAM does NOT report.
#: This partition is the one recorded on #2817 (comment `6359826543`):
#: `not_an_iso4217_code` is the noisy one in business prose (`"ISO 9001"`,
#: `"SOC 2"`, `"Q3 target 30 KPI"`) and `no_candidate` fires on every sentence
#: with no number at all. Every other reason means "a number was here and it
#: was NOT safely typed" and stays reported — including `no_currency_anchor`.
#: `refused_by_layer1` is reported too: a value the store cannot hold is exactly
#: the abstention a reader must see (the #2817 review's over-range amount).
#:
#: ⚠️ Measured residual (2026-10-23): the vocabulary conflates a NON-money bare
#: number (`"Sprint 14 closed on time"` → `no_currency_anchor`) with a
#: money-shaped amount in an unsupported currency (`"800 BTC"` →
#: `not_an_iso4217_code`, so silent here). That partition is a decision's, not
#: this lane's — a reason-level split is the fix, filed against #2817.
_CAPTURE_MONEY_WARN_SILENT: frozenset[str] = frozenset({
    "no_candidate", "not_an_iso4217_code"})
# Import-time truth of the comment above: a RENAMED reason in the silent set
# must fail here, never silently re-partition the warning policy. An explicit
# `raise`, not an `assert`: `python -O` strips asserts, and a guard a runtime
# flag can delete is not a guard (the shape `extractor_v2.py:5155` states).
if not _CAPTURE_MONEY_WARN_SILENT <= DROP_REASONS:
    raise RuntimeError(
        "capture money warn policy names a reason outside DROP_REASONS: "
        f"{sorted(_CAPTURE_MONEY_WARN_SILENT - DROP_REASONS)!r}")
_CAPTURE_MONEY_WARN_ON: frozenset[str] = DROP_REASONS - _CAPTURE_MONEY_WARN_SILENT

#: #2817 (review F1) — the capture seam reports its money abstentions ONCE per
#: capture, aggregated by reason, never one line per point. The bound is
#: structural: one entry per call, with at most one bucket per closed-set
#: reason (``DROP_REASONS``), so no point count can lengthen the receipt. This
#: is the same shape as the #5654 bounded supersession channel in the same
#: function — the difference is that this channel needs no withheld count,
#: because the summary reports the TRUE total it counts.
_CAPTURE_MONEY_DROP_SUMMARY_PREFIX = (
    "value-normalizer: {total} point(s) carried an amount that was not "
    "typed — ")


def _capture_money_drop_summary(drops: dict[str, int]) -> str:
    """ONE bounded receipt entry for a capture's money abstentions.

    Deterministic (reasons sorted) so two captures of byte-identical content
    produce a byte-identical receipt — including a create and a dedup hit,
    which is what #2817 review F1's create/dedup asymmetry was about.
    """
    reasons = ", ".join(f"{reason} \u00d7{count}"
                        for reason, count in sorted(drops.items()))
    return (_CAPTURE_MONEY_DROP_SUMMARY_PREFIX.format(
                total=sum(drops.values()))
            + reasons
            + " (prose only — each value is a drop, never a guess)")


def _capture_passthrough_read_fields() -> str:
    """Build the dedup-hit read-back RETURN field list from the ordered
    declaration above — never hand-written inline (review F4)."""
    return ", ".join(f"n.{k}" for k in _CAPTURE_PASSTHROUGH_ORDER)


# Drift guard (review F4): the DERIVED read-back must cover EVERY whitelisted
# field, exactly once. A hand-rewritten RETURN (or a whitelist extension that
# bypasses the derivation) fails at import, before a dedup hit can answer with
# props the node holds but the response omits.
_capture_passthrough_read_clause = _capture_passthrough_read_fields()
assert (
    _capture_passthrough_read_clause.count("n.")
    == len(_CAPTURE_PASSTHROUGH_PROPS)
    and all(
        f"n.{k}" in _capture_passthrough_read_clause
        for k in _CAPTURE_PASSTHROUGH_PROPS
    )
), (
    "capture passthrough read-back/whitelist drift: "
    f"{_capture_passthrough_read_clause!r} does not cover "
    f"{sorted(_CAPTURE_PASSTHROUGH_PROPS)!r}"
)


def _session_llm_transcript(conversation: list[dict]) -> tuple[str, int]:
    """Build the LLM-extraction transcript + pre-write estimate for a
    conversation (#822). One ``Speaker: text`` line per turn (the
    extractor._utterances segmenter format; non-name roles fall back to
    ``Speaker``), content from the SAME stored window the turn Points hold
    (#721 parity — a phrase past the cut has no home in any stored turn),
    newlines flattened so multi-line turns are still extracted, sentences
    capped per turn at MAX_EXTRACTIONS_PER_TURN (the #329 flood gate — the
    removed regex loop capped extracted Points per turn the same way).

    Returns ``(transcript, estimate)`` where ``estimate`` is the fail-closed
    upper bound on NEW non-episodic nodes: extracted Points (one per sentence)
    ×2 for the M2 relations stage's IMPL/NAND operator nodes (the regex loop
    never created operators). The ×2 is a true ceiling only because the session
    extractor clamps operators ≤ points (LLMExtractor.run dedupe+cap, #1194) —
    a permissive relation model can no longer write more operator nodes than
    points, which would have bypassed this estimate and the 402 gate it feeds.

    ⛔ CALLER CONTRACT — pass the WINDOWED conversation (``_capture_turn_window``),
    never the raw one. This function FLATTENS but does NOT re-clip, and that is
    load-bearing (#4897): the clip and the marker's TRUE length are decided ONCE,
    on the PRE-redaction text, so re-applying the cap here would re-derive the
    length from text a #4911 replacement has already changed — the marker would
    report the scrubbed size instead of what the user said. The stored window is
    exactly what the LLM must see, so the two cannot drift.
    ``_session_extraction_estimate`` windows its own input for the callers that
    hand it a raw one.

    The sentence predicate lives in :func:`_extractable_sentences` — one home,
    shared with the v2 lane's marker-only-turn blanking (#4897), so the two
    lanes cannot disagree about which turns have anything to say.
    """
    lines: list[str] = []
    n_sentences = 0
    for turn in conversation:
        # The ONE role-normalization funnel (#5445): a second, inline copy here
        # was redundant, and removing it fixed NOTHING on its own — the deleted
        # form was semantically identical to `_normalize_turn_role` for every
        # input (#5445 review round 12). What actually protects this sink is the
        # SCRUB: the caller hands in a role that already passed `redact_secrets`
        # at `_redact_turn_contents`, the one chokepoint. Do not read this funnel
        # as the protection — deleting the scrub while keeping the normalization
        # would reopen the sink.
        #
        # On the capture path the conversation handed in is already role-scrubbed
        # (``_materialize_session_source`` passes ``_redact_turn_contents``'s
        # output), so this consumes the redacted role rather than re-scrubbing.
        role = _normalize_turn_role(turn.get("role"))
        speaker = role.title()
        if not re.match(r"^[A-Z][\w .'-]{0,40}$", speaker):
            # non-word roles (123, dict str, weird casing) still extract under
            # a generic speaker label — never drop the turn's content.
            speaker = "Speaker"
        raw = turn.get("content")
        content = raw if isinstance(raw, str) else ("" if raw is None else str(raw))
        # #6246: the "does this turn have anything to say" predicate has ONE
        # home (_extractable_sentences) — the same one the shared extraction
        # view blanks on, so the m2 lane and the default v2 lane cannot
        # disagree about which turns contribute a unit.
        #
        # ⛔ A TURN WITH NO EXTRACTABLE CONTENT OF ITS OWN YIELDS NO CLAIMS (#4897 review
        # round 14, P1; predicate corrected in review round 15). The appended marker reserves
        # ``37 + len(str(total))`` characters of the cap, so a turn whose real text sits in
        # that band is stored as ``" " * keep + marker`` — its whole readable body IS the
        # marker. ``_SENT`` parses ``…[truncated: original length 5971 chars]`` as a sentence,
        # so the v1 extractor minted a Point whose entire content was this module's own marker
        # (reproduced in review). That is not "the marker reaches the model" — which the CALLER
        # CONTRACT below requires for a turn that HAS content — it is a turn with nothing to say
        # creating a claim. Skipping it leaves the marker riding through untouched for every
        # turn that does have content.
        #
        # ⛔ THE TEST IS EXTRACTABILITY, NOT WHITESPACE (#4897 review round 15, P1). Round 14
        # skipped on a blank marker-free body, which let a clipped turn whose real body is
        # non-whitespace but holds no >=3-char SENTENCE through: ``body`` is built from the
        # WHOLE content (marker included), so ``_SENT`` then joined the 1-char body to the
        # marker and turned the MARKER into the sentence — minting a synthetic claim again.
        # Measured on a mixed 3-turn conversation with the m2 extractor: the whitespace-only
        # test extracted 3 points where pre-#4897 extracted 1, the two extra being the two
        # markers. Judging the MARKER-FREE body with ``_extractable_sentences`` — the SAME
        # >=3-char predicate the sentence filter below applies, and #6246's ONE home for it —
        # is what makes the skip agree with what the turn can contribute.
        # ⛔ AND THE MARKER IS ATTACHED, NOT APPENDED (#4897 review round 16, P1). Testing the
        # marker-free body is NECESSARY BUT NOT SUFFICIENT. The round-15 form still built the
        # line from the WHOLE content, and `_SENT` takes a run of NON-terminators plus AT MOST
        # one terminator — so for a clipped turn whose retained body happens to END with a
        # sentence terminator (keep = cap - len(marker), i.e. "character `keep` is a '.'",
        # ~1 in 80 in prose) the marker sits AFTER that terminator and `_SENT` carves it into a
        # SECOND match. `_utterances` then yields it as its own utterance and `LLMExtractor.run`
        # mints one Point per utterance 1:1 — a Point whose entire content is this module's own
        # marker. Measured on the MIXED test conversation (a real turn plus two clipped ones):
        # this branch extracted 3 points where `origin/main` extracted 2, the extra being the
        # marker. On the single fixture `"a" * 4958 + "." + " " * 500` the same defect measured
        # 2 against 1 — a different count because it is a different conversation, and attributing
        # the 3/2 to the fixture was itself a defect (round 17, P3). Appending the marker to the
        # last sentence does NOT fix it (the terminator is between them); it is placed INSIDE the
        # final sentence, immediately before that sentence's own terminator, so marker and sentence
        # are ONE `_SENT` match. The node still stores the marker verbatim as written; this is the
        # TRANSCRIPT view, and relocating it within the turn is what lets it reach the model
        # WITHOUT becoming a claim of its own.
        _body, marker = _split_truncation_marker(content)
        sents = _extractable_sentences(content)
        if not sents:
            continue
        # Cap BEFORE attaching: `MAX_EXTRACTIONS_PER_TURN` keeps the FIRST 200 sentences and
        # the marker belongs to the LAST of them, so attaching first let a flood turn drop the
        # marker from the transcript while the stored node still carried it.
        capped = sents[:MAX_EXTRACTIONS_PER_TURN]
        if marker:
            last = capped[-1]
            capped[-1] = (f"{last[:-1]} {marker}{last[-1]}" if last[-1] in ".!?"
                          else f"{last} {marker}")
        n_sentences += len(capped)
        if capped:
            lines.append(f"{speaker}: {' '.join(capped)}")
    return "\n".join(lines), n_sentences * 2


def _normalize_turn_role(raw) -> str:
    """Shared turn-role normalization (#1532 D2): None -> 'unknown', truthy
    non-strings -> str() — never a raw non-string stored as `speaker`
    (#721) and never None stored as-is (SDK/hosted drift)."""
    if isinstance(raw, str):
        return raw
    return "unknown" if raw is None else str(raw)


#: The stored-window cap (#1532 D1). Named once because four consumers now
#: depend on the SAME number: the stored turn text, the session Source
#: transcript (_session_llm_transcript flattens the same window), the
#: #4911 scrubber's per-turn bound, and the Pi client's ``TURN_MAX_CHARS`` —
#: a TypeScript literal that CANNOT import this module (it is shipped
#: standalone) and is therefore pinned to this constant by
#: ``tests/test_pi_capture_hooks.py`` so a divergence reds a test instead of
#: storing a different window per lane. A divergence between any of them would
#: either scan text that is never persisted or persist text that was never
#: scanned.
_CAPTURE_TURN_CAP = 5000

#: Sentinel prefix of the truncation marker appended when a turn is clipped at
#: the stored-window cap (#4897). A READER of a stored turn detects a cut by
#: this prefix, and the marker also carries the turn's TRUE length, so a
#: consumer can tell "the user said this much" from "we cut it here". This is
#: the Python half of a two-language contract; the identical literal lives in
#: ``tortoise/pi-hooks/tortoise-capture.ts`` (``TRUNCATION_SENTINEL``) and the
#: two are pinned together by ``tests/test_pi_capture_hooks.py``.
#:
#: ⛔ WHY THE MARKER IS IN THE TEXT, NOT A NODE PROPERTY. Both shapes are
#: available (the #4897 issue offers ``truncated: true`` + ``full_length: n``
#: OR an appended marker), and the text marker is chosen for two reasons:
#: (1) FIDELITY — a node property is invisible to the extraction input
#: (`_session_llm_transcript`), so the LLM would still receive a silently-cut
#: sentence; the marker reaches the store AND the model, which is what keeps
#: the stored-source parity invariant (#721) true rather than merely intact;
#: (2) MACHINERY — a property must be threaded through the turn write Cypher,
#: the read whitelist, `get_session_detail` and the client confirmation, each a
#: fresh drift surface, whereas the marker rides the ``content`` property that
#: already round-trips. The true length is therefore carried in the text only.
#:
#: ⛔ NO RETROACTIVE MARKING. Turns already stored at the old silent cut stay
#: unmarked and are indistinguishable from a complete maximal turn; a marker can
#: only be written where the cut is DECIDED, and the original length is already
#: gone by the time such a node is read. That is a deliberate no-migration
#: boundary (#4897 is the marking half, #4894 the storage redesign), not a
#: promise this marker will later cover the backlog.
_CAPTURE_TRUNCATION_SENTINEL = "…[truncated:"


def _capture_truncation_marker(total: int) -> str:
    """The marker appended INSIDE the window when a turn is cut (#4897).

    ``total`` is the ORIGINAL character count (before the cut) — the fact the
    cut itself destroys — so a reader can always tell how much conversation
    the stored turn no longer holds. The marker's length is a function of
    ``total`` ALONE (``37 + len(str(total))``), which is what lets
    :func:`_clip_capture_turn_content` reserve exactly its width without
    iterating to a fixpoint.
    """
    return f" {_CAPTURE_TRUNCATION_SENTINEL} original length {total} chars]"


#: Code points treated as BLANK by :func:`_clip_capture_turn_content` — the set
#: that decides "a whitespace-only retention is not marked" (#4897 review round
#: 15, P3). Spelled out explicitly rather than left to ``str.strip()`` because
#: this is the Python half of a TWO-LANGUAGE contract: JS
#: ``String.prototype.trim`` strips U+FEFF but NOT U+001C-U+001F / U+0085, while
#: Python's ``str.strip()`` does the reverse — so the two clippers disagreed on
#: exactly those five code points and the "byte-identical stored turns" claim
#: was false for them. This is the UNION of the two sets, so no input either side
#: called blank becomes non-blank on the other. The identical literal lives in
#: ``tortoise/pi-hooks/tortoise-capture.ts`` (``BLANK_CHARS``); it is pinned
#: together with :data:`_CAPTURE_NONBLANK_RE` by the cross-language parity case
#: in ``tests/test_pi_capture_hooks.py`` and by
#: ``tests/test_capture_session.py::test_the_clippers_blank_set_is_explicit``.
_CAPTURE_BLANK_CHARS = (
    "\u0009\u000a\u000b\u000c\u000d"          # tab, LF, VT, FF, CR
    "\u001c\u001d\u001e\u001f"                # file/group/record/unit separator
    "\u0020\u0085\u00a0\u1680"
    "\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a"
    "\u2028\u2029\u202f\u205f\u3000\ufeff"
)

#: ``content`` is blank iff this finds nothing — one negated class rather than a
#: per-character loop so the test stays C-fast on a multi-MB caller body.
_CAPTURE_NONBLANK_RE = re.compile(f"[^{_CAPTURE_BLANK_CHARS}]")


#: The FULL truncation marker, anchored at the tail (``_split_truncation_marker``
#: also accepts the bare-sentinel fallback that ``cap`` too small to carry the
#: message falls back to). Kept strict so a marker is only recognised when it is
#: the one this module wrote — never a user's lookalike prose.
_TRUNCATION_MARKER_FULL_RE = re.compile(
    re.escape(_CAPTURE_TRUNCATION_SENTINEL) + r" original length \d+ chars\]")


def _capture_gate_window(windowed: list[dict]) -> list[dict]:
    """Return ``windowed`` with synthetic truncation markers stripped (#4897).

    ⛔ WHY THE GATE NEEDS ITS OWN VIEW (#4897; rationale corrected in review
    round 14): the strip is NOT redundant with the clipper's blank-retention
    branch, but for a narrower reason than the original text gave. The original
    example — ``" " * 6001`` — is now handled by the clipper itself, which returns
    a blank window with NO marker, so it never reaches the gate as non-blank.
    What the strip actually decides is a clipped turn whose body holds no
    >=3-char SENTENCE (``"X" + " " * 6000`` clips to a lone ``X``; ``"  .  " *
    2000`` to single dots) or a caller-supplied lookalike with trailing
    whitespace. In those the marker's own text is the only sentence the
    transcript would hold, so without the strip a turn with nothing extractable
    reads as content and gets admitted — measured: exactly four probe inputs
    change verdict when the strip is removed. The gate must judge the REAL text.

    Only the gate uses this. The extractors keep the marker: it is the evidence
    of what was cut, and they sit downstream of the gate, so a conversation the
    gate admits is unaffected.

    A caller who supplies a turn whose whole content is itself a marker
    lookalike is stripped to a body that is BLANK once stripped — the marker's
    own leading space is all that precedes the sentinel — and the gate refuses.
    Correct (such a turn has no extractable content) and fail-closed, which is
    the safe direction.

    ⛔ TRAILING WHITESPACE IS TOLERATED BEFORE THE MARKER SPLIT (#4897 review
    round 13, P3). ``_split_truncation_marker`` recognises a marker only as the
    exact tail, so a caller-supplied ``marker + " "`` escaped the strip and was
    admitted as a non-blank turn holding only marker text — the claim above was
    one character wide. Right-stripping first closes it in the FAIL-CLOSED
    direction (a lookalike is stripped more readily, never less); real clipped
    text is unaffected because the module writes its marker with nothing after
    it, and dropping trailing whitespace from a blankness signal cannot turn a
    non-blank conversation blank.
    """
    out: list[dict] = []
    for turn in windowed:
        content = turn.get("content") if isinstance(turn, dict) else None
        if not isinstance(content, str):
            out.append(turn)
            continue
        body, _marker = _split_truncation_marker(content.rstrip())
        out.append({**turn, "content": body} if body != content else turn)
    return out


def _split_truncation_marker(content: str) -> tuple[str, str]:
    """Split a turn's TRAILING truncation marker off its body (#4897).

    Returns ``(body, marker)`` with ``body + marker == content``. ``marker`` is
    the string :func:`_clip_capture_turn_content` appended — the full
    ``…[truncated: original length N chars]``, or the bare-sentinel fallback —
    or ``""`` when the turn was not cut.

    ⛔ ``marker`` IS ONE CHARACTER SHORTER THAN THE APPENDED TEXT (#4897 review
    round 14, P3; width corrected in round 15): the appended text is
    `` …[truncated: …]`` with a LEADING SPACE, and ``rfind`` starts at the
    sentinel, so that space stays in ``body``. The appended text is
    ``37 + len(str(total))`` characters and ``marker`` is ``36 + len(str(total))``
    — 41/40 only for a 4-digit total, and 42/41 for the ``"  .  " * 2000``
    fixture whose total is 10000. ``body + marker == content`` still holds (it is
    the whole reason the space is not lost), but the leading space is handed to
    :func:`security.redact_secrets` along with the body — the marker text proper
    is not. Callers that need the marker-free text must ``.strip()`` the body;
    both blank gates do.

    ⛔ WHY SPLIT AT ALL: the marker must NEVER be part of the text handed to
    :func:`security.redact_secrets`. The ``private_key`` rule's fail-closed
    branch matches to ``\\Z``, so a dangling PEM header consumes everything after
    it — the marker included — and a cut turn is then stored UNMARKED (the
    round-2 P2: 5,000-char window, 4,823-char stored body, no sentinel). Keeping
    the marker out of the scan makes its survival STRUCTURAL for EVERY rule
    instead of a property each rule must separately be proved not to violate,
    and it preserves a prior cut's TRUE ``total`` verbatim when the body is
    re-scanned — an expanding #4911 replacement can push the body past ``cap``,
    and re-clipping that would re-derive a FALSE total from the scrubbed length
    (the round-1 P1 this whole file is about).
    """
    idx = content.rfind(_CAPTURE_TRUNCATION_SENTINEL)
    if idx < 0:
        return content, ""
    tail = content[idx:]
    if tail == _CAPTURE_TRUNCATION_SENTINEL \
            or _TRUNCATION_MARKER_FULL_RE.fullmatch(tail):
        return content[:idx], tail
    return content, ""


def _clip_capture_turn_content(
    content: str, cap: int = _CAPTURE_TURN_CAP) -> str:
    """``content`` unchanged when it fits ``cap``; otherwise cut AND marked.

    This is the ONE place a capture turn is shortened (#4897). Every writer of
    stored turn text routes through :func:`_capture_turn_window`, so no cut is
    silent: the result is always ``<= cap`` characters and, whenever the input
    was longer, ends with :func:`_capture_truncation_marker` carrying the true
    original length.

    ⛔ THE CAP IS TESTED ON THE TOTAL ``len(content)``, marker included. A
    turn carrying a caller's marker-shaped tail (a sentinel plus an unbounded
    ``original length \\d+`` run — ``_TRUNCATION_MARKER_FULL_RE`` admits any
    width) is longer than ``cap`` and is CLIPPED like any other over-cap turn,
    with a fresh marker derived from the length actually seen. Testing a
    marker-free ``body`` instead would leave a 100,137-char turn (101 chars plus
    a 100,036-char "marker") unbounded in the store — the round-5 P2.

    ⛔ THE MARKER IS RESERVED INSIDE THE CAP — the whole point. The Pi client
    caps before it POSTs, so the server can receive an already-clipped turn; a
    marker appended AFTER a full-width cut would be lost the moment that body is
    re-windowed. ``keep = cap - len(marker)`` makes the result exactly ``cap``
    characters, so ``result[:cap] == result`` and a second application to the
    SAME pre-redaction text is a true no-op. A clipped turn therefore reaches
    storage with its marker intact.

    Only an ACTUAL cut marks: a turn exactly at ``cap`` is complete and is
    returned verbatim, so the marker means "there is more", never "we reached
    the boundary".

    ⛔ APPLY THIS TO THE PRE-REDACTION TEXT, EXACTLY ONCE, AND NEVER AGAIN. A
    second application to a body the #4911 scrubber has already changed would
    recompute ``total`` from the SCRUBBED length — the false-marker defect of
    #4897's P1 — and a redaction replacement longer than the span it replaces can
    push a complete turn past ``cap``, so re-applying here would mark a complete
    turn. :func:`_capture_turn_window` is the only caller, and it is applied when
    a turn is taken from the RAW conversation (the capture entry points,
    ``_materialize_session_source``, ``_extract_session_llm``,
    ``_extract_session_v2``) — never to a redacted body.
    """
    if len(content) <= cap:
        return content
    # ⛔ A WHITESPACE-ONLY RETENTION IS NOT MARKED (#4897 review round 12). The
    # marker means "there is more", and for a blank body that statement is
    # misleading: the retaining window holds nothing, so the marker is the ONLY
    # text the turn carries. That mattered — the gate reads a marker-STRIPPED
    # view, so an all-blank conversation is refused, but a MIXED one
    # (a real turn plus a blank over-cap turn) still STORED a marker-only turn,
    # and the v1 extractor then turned that marker into a Point whose entire
    # content was synthetic (reproduced in review). Returning the blank
    # retention silently is the same value the store would hold minus the
    # misleading sentence, and it keeps the window idempotent.
    #
    # ⛔ THE PREDICATE MUST TEST THE SLICE IT RETURNS (#4897 review round 13,
    # P1). Round 12 tested ``content[:keep]`` — the width left after reserving
    # the marker — while RETURNING ``content[:cap]``. Real content in the
    # reserved band ``[keep, cap)`` therefore made the premise ("the retention
    # is blank") FALSE while the turn came back UNMARKED: a 5,971-char turn of
    # 4,960 spaces + "REALCONTENT" + 1,000 further chars was stored as 5,000
    # chars with no sentinel, silently dropping the rest — reintroducing exactly
    # the mid-word cut #4897 exists to end. Testing ``content[:cap]`` — the
    # slice actually returned — makes the cut marked whenever ANY retained
    # character is non-whitespace, so the band is never dropped silently.
    #
    # ⛔ AND THE SLICE IS NOT ENOUGH (#4897 review round 14, P1): testing
    # ``content[:cap]`` still missed real text PAST the cap when everything
    # retained was whitespace — ``" " * 5000 + "REALCONTENT"`` returned
    # ``" " * 5000`` unmarked and silently dropped the word. The test is
    # therefore on the WHOLE content: if the turn holds no non-whitespace
    # ANYWHERE there is nothing to mark and nothing was lost; if it holds any,
    # it is marked and ``keep`` is reserved for the marker.
    #
    # The residual: a turn whose only non-whitespace lives IN the band is marked
    # and spends the band on the marker — the reserved width
    # (``37 + len(str(total))``) and the content compete for the same characters.
    # That turn is stored marker-only, which is why `_session_llm_transcript`
    # skips a turn whose marker-free body holds no sentence rather than minting
    # the marker into a Point.
    #
    # ⛔ THE PREDICATE IS ``_CAPTURE_NONBLANK_RE``, NOT ``str.strip()`` (#4897
    # review round 15, P3). Blankness is a TWO-LANGUAGE contract with
    # ``tortoise-capture.ts``, and ``str.strip()`` and JS ``trim()`` disagree on
    # five code points (Python strips U+001C-U+001F and U+0085 where JS does not;
    # JS strips U+FEFF where Python does not). ``_CAPTURE_BLANK_CHARS`` is the
    # UNION of both sets and the TS side already uses it, so the Python side must
    # use the SAME class or the two clippers store different turns for the same
    # input — the parity the client/server contract promises.
    if not _CAPTURE_NONBLANK_RE.search(content):
        return content[:cap]
    marker = _capture_truncation_marker(len(content))
    keep = cap - len(marker)
    if keep < 0:
        # ``cap`` too small to carry the full marker — never the production
        # 5000, and unreachable from any caller in the tree. Falls back to the
        # bare sentinel; note the final ``[:cap]`` can still truncate THAT when
        # ``cap < len(_CAPTURE_TRUNCATION_SENTINEL)`` (cap=5 → "…[tru"), so the
        # cut is not guaranteed visible in that corner. Kept as a defensive
        # branch: reverting to a silent ``content[:cap]`` is no better.
        marker = _CAPTURE_TRUNCATION_SENTINEL
        keep = max(cap - len(marker), 0)
    return (content[:keep] + marker)[:cap]


def _extractable_sentences(content: str) -> list[str]:
    """The >=3-char sentences *content* contributes, from its MARKER-FREE body.

    The ONE home of "does this turn have anything to say" (#6246, #4897). It is
    the exact predicate ``_session_llm_transcript`` has always applied inline
    (and the transcript is the lane that already REFUSED a turn it yields
    nothing for), so extracting it here lets the shared extraction view apply
    the SAME test before the default v2 lane sees the turns and the two lanes
    cannot drift apart about emptiness. A clipped turn whose real body holds no
    >=3-char sentence has nothing to contribute — the marker's own text is all
    that is left — so the marker is split OFF first and every extraction lane
    treats it as empty. Judging the marker-free body with the SAME predicate the
    transcript's sentence filter applies is what makes "empty" mean the same
    thing here as it does there.
    """
    from tortoise.extractor import _SENT
    body_only, _marker = _split_truncation_marker(content)
    real = " ".join(body_only.split())
    return [s.group(0).strip() for s in _SENT.finditer(real)
            if len(s.group(0).strip()) >= 3]


def _blank_unextractable_marked_turns(
        conversation: list[dict],
) -> list[dict]:
    """Blank CUT turns that have nothing to say, for the v2 lane (#4897).

    The v2 lane hands the raw turn text to ``extractor_v2``, whose edu builder
    keeps any turn with truthy content. So a clipped turn whose real body is a
    lone ``X`` reached the model as ``"X …[truncated: …]"`` — a turn with
    nothing to say whose only sentence is this module's own marker, the exact
    shape the m2/transcript lane learned to SKIP in review rounds 14-16. The
    default lane was left as the unguarded sibling; this makes the two agree.

    ⛔ NARROW BY CONSTRUCTION: only a turn that CARRIES a marker is eligible, so
    an unmarked short turn (``"ok"``) is untouched — this does not change how
    the v2 lane treats ordinary short turns. The list LENGTH is preserved (the
    turn is blanked, never removed) because
    ``extractor_v2._edus_from_conversation`` derives each edu's turn index from
    its position and ``_resolve_source_turn`` anchors quotes to those indices —
    shortening the list would renumber every later turn.
    """
    out: list[dict] = []
    for turn in conversation:
        content = str(turn.get("content") or "")
        _body, marker = _split_truncation_marker(content)
        if marker and not _extractable_sentences(content):
            out.append({**turn, "content": ""})
        else:
            out.append(turn)
    return out


def _capture_turn_window(
    conversation: list[dict], cap: int = _CAPTURE_TURN_CAP) -> list[dict]:
    """Clip each turn's content to the stored-window cap, MARKED (#1532 D1).

    Returns a NEW list; the windowed conversation feeds BOTH the turn-store
    loop and the extraction call so the LLM never sees a phrase with no home
    in any stored turn (stored-source parity, #721). Content coercion matches
    the store loop: None -> '', non-strings -> str() (isinstance-first,
    #721). The ROLE is coerced with ``_normalize_turn_role`` and capped the same
    way (#5445/#5775), so the scanned and the persisted role are the same bytes
    and a non-string role cannot skip the bound. Idempotent when the caller
    already truncated — and since #4897 a clip leaves a marker inside the cap,
    so a re-applied window preserves the marker rather than letting it fall off
    the end.
    """
    out: list[dict] = []
    for turn in conversation:
        t = dict(turn)
        raw = t.get("content")
        content = raw if isinstance(raw, str) else ("" if raw is None else str(raw))
        t["content"] = _clip_capture_turn_content(content, cap)
        # ⛔ The ROLE is capped here too (#5445 review round 12). It is persisted as
        # text by the same frame AND scrubbed by ``_redact_turn_contents``, so a
        # client-controlled role was the one scan on this path the cap did not
        # reach: ``redact_secrets`` ran over the WHOLE role (measured ~2.7 s for a
        # 500,000-char role, and ``cap`` changed nothing), and the module's own
        # "``cap`` bounds the text scanned per turn" claim was false for it. Capping
        # at the window keeps the scanned and the persisted role the SAME bytes.
        #
        # ⛔ COERCE BEFORE THE CAP (#5775). The guard here was
        # ``isinstance(t.get("role"), str)``, so a NON-STRING role skipped the cap
        # ENTIRELY while the downstream ``_redact_turn_contents`` still normalized
        # it with ``str()`` and scanned the whole thing. It is reachable from
        # untrusted input: the hosted model types ``conversation: list[dict]`` with
        # no inner validation, so ``role`` can be any JSON type. A one-element list
        # of a 1,000,000-char string stringifies to 1,000,004 chars and cost a
        # measured ~0.7-3.8 s of scanning PER REQUEST, against ~0.002 s for a
        # same-length STRING role (which the cap did cut). ``_normalize_turn_role``
        # is the SAME normalization every consumer already applies to this value
        # (None -> "unknown", any non-str -> ``str()``), so coercing before the cap
        # changes no downstream output and makes the bound total.
        t["role"] = _normalize_turn_role(t.get("role"))[:cap]
        out.append(t)
    return out


def _capture_extraction_window(
    conversation: list[dict], cap: int = _CAPTURE_TURN_CAP) -> list[dict]:
    """The SHARED extraction view of a capture conversation (#6246).

    ``_capture_turn_window`` is the STORED view: it clips (marking the cut,
    #4897) and the clipped text is what the turn Points hold. Both extraction
    lanes consume THIS view instead, and it blanks a turn that was CLIPPED and
    still has no extractable sentence of its own — so a cut whose readable body
    has nothing to say contributes no unit to EITHER lane.

    The invariant ("a turn with no extractable content of its own yields no
    claims", #4897) previously lived only inside ``_session_llm_transcript``,
    which is why the m2 lane enforced it and the default v2 lane
    (``_edus_from_conversation``, which keeps any turn with truthy content)
    did not. It is built ONCE per capture loop — selfhost ``capture_session``
    and hosted ``_capture_session_impl`` — and each loop hands the SAME list
    to whichever lane runs, so no lane re-decides emptiness for itself.

    NARROW BY CONSTRUCTION: only a turn that was CLIPPED (``len > cap``) is
    eligible, so an ordinary short turn (``"ok"``) is untouched — this is not
    "drop every sentence-less turn from v2". The list LENGTH is preserved (the
    turn is blanked, never removed): ``extractor_v2._edus_from_conversation``
    derives each edu's turn index from its POSITION and ``_resolve_source_turn``
    anchors quotes to those indices, so shortening the list would renumber
    every later turn.
    """
    windowed = _capture_turn_window(conversation, cap)
    out: list[dict] = []
    for turn, win in zip(conversation, windowed, strict=True):
        raw = turn.get("content")
        content = raw if isinstance(raw, str) else ("" if raw is None else str(raw))
        if len(content) > cap and not _extractable_sentences(win["content"]):
            win = {**win, "content": ""}
        out.append(win)
    return out


def _redact_turn_contents(
    conversation: list[dict],
    cap: int | None = None,
) -> tuple[list[dict], dict[str, int]]:
    """The ONE place capture text is scrubbed of credentials (#4911).

    Returns a copy of ``conversation`` whose turns carry a scrubbed
    ``content`` AND a scrubbed ``role``, plus the per-kind counts. Every capture
    consumer derives its text from this — the stored turn text
    (``_capture_turn_texts``), the session
    ``:Source`` metadata (``_materialize_session_source``) and the extraction
    transcript (``_extract_session_llm`` / ``_extract_session_v2``) — so the
    control sits at the single point where a message becomes persisted or
    model-visible text, instead of being duplicated per call site.

    ⛔ NO PRODUCTION CALLER CLIPS HERE (#4897 round 5). The stored window is
    decided ONCE, on the RAW text, by :func:`_capture_turn_window` /
    :func:`_clip_capture_turn_content`, the sole clippers; every capture caller
    passes NO ``cap``. This function's job on that path is redaction only: split
    a trailing truncation marker off, scrub the body, re-attach the marker
    VERBATIM.

    ⛔ ``cap`` IS RETAINED FOR THE EXPLICIT RAW-CALLER API. Main's #5445/#5472
    tests pin ``_redact_turn_contents(conv, cap=...)`` — a ``cap`` that does not
    bound the RETURNED turn is a leak, not a knob — so the parameter stays, and
    when it is passed it is applied to the RAW, marker-free body BEFORE the scan
    (never to redacted text, so it can never re-derive the marker's ``total``
    from a scrubbed length: the round-1 P1). No production caller passes it, so
    the single-clipper invariant holds on every capture lane.

    Five rounds of patches tried to make this one function clip AND redact
    unconditionally, and each traded in the same tension: a clip decision had to
    tell a marker THIS module wrote from marker-shaped text a caller supplied,
    and that discrimination is not decidable in-band. ``len(body) > cap`` is true
    for an attacker's raw over-cap body AND for a legitimately-clipped body that
    redaction markup pushed over ``cap``, so every patch chose one and broke the
    other (round 3 let a marker-shaped tail exempt the body; round 4 re-clipped
    a caller's marker). Making the clip OPT-IN (``cap is not None``) removes the
    tension on the production path — no production caller can hit it — while the
    explicit ``cap=`` path keeps main's tested bound, and re-application is exact
    for the independent reason in (2) below — the scan input is marker-free.

    ⛔ The sibling ``role`` is scrubbed HERE too (#5445). It is caller-controlled
    (the hosted model takes ``conversation: list[dict]`` with no role validation)
    and the capture path persists it as TEXT through three sinks — the
    ``f"[{role}] {content}"`` stored turn, ``:Point.speaker`` and the session
    ``:Source`` transcript (``_session_llm_transcript`` uses the role as the
    speaker label). A credential placed in ``role`` therefore reached the graph
    while the receipt reported zero. The role is scrubbed, not replaced with a
    placeholder, because there is no canonical role enum in the capture contract
    — the codebase and its own tests use ``developer``, ``toolResult``,
    ``robot`` and ``agent`` — so a whitelist would silently rewrite legitimate
    labels, i.e. over-redact ordinary text. The role scrub runs through this same
    chokepoint (one ``redact_secrets`` owner, per the #4911 control-site rule)
    and its counts join the same total.

    ⛔ CALLER CONTRACT — pass the WINDOWED conversation, never a raw over-cap
    one. Every sink that can receive a raw conversation windows it FIRST, on
    that raw text: ``_materialize_session_source`` (the session ``:Source``
    sink), ``_extract_session_llm`` and ``_extract_session_v2`` (the
    extractors), and the capture entry points (``capture_session`` / hosted
    ``_capture_session_impl``). ``_capture_turn_texts_with_redactions`` is
    redaction-only over a conversation its caller windowed. The generic
    ``commit_session`` lane (``_commit_session_v1`` / ``_commit_session_v2``)
    deliberately does NOT window: no stored-turn window exists there and the
    extractor renders each turn verbatim.

    ``cap`` bounds the text scanned per turn on the EXPLICIT-caller path (above).
    The session-``:Source``/extractor consumers pass none because they window
    first — the capture-lane extractors are handed
    ``_capture_extraction_window``'s already-clipped view, so a ``cap`` there
    would be a no-op — and the commit consumers pass none for the reason in
    ``_capture_turn_texts_with_redactions``. The bound is a CPU-cost and
    window-parity bound, NOT loop protection: in the hosted lane every
    capture-path caller of this is off the event loop (#4911 cycle 1).

    ⛔ THE WINDOW IS APPLIED ONCE, TO RAW TEXT, AND NEVER AGAIN. That
    single-application rule is what keeps the cap from being re-derived from
    text a #4911 replacement has already changed — a redacted body is never fed
    back through :func:`_capture_turn_window`. The window bounds the text SCANNED
    here at ``_CAPTURE_TURN_CAP`` per turn, which is also what keeps a
    client-controlled turn of a few MB from costing seconds of scanning
    (measured: ~3 s/MB of scanned text) and keeps the scan the same window the
    persisted text uses; it is a CPU-cost and window-parity bound, not loop
    protection (in the hosted lane every capture-path caller is off the event
    loop, #4911 cycle 1).

    ⛔ THE RETURNED TEXT MAY EXCEED THE CAP BY THE REDACTION MARKUP, on purpose
    (#4897). The window is applied to the PRE-redaction text, which is what the
    marker records; a #4911 replacement that is LONGER than the span it replaces
    (``aws_access_key_id`` 20 → 28) then grows the body past the cap.
    Re-clipping that grown body would either overwrite the true total with the
    scrubbed size or drop original text to pay for the redaction markup, so the
    body is returned as-is and the caller must NOT re-apply the cap. The overage
    is redaction markup, bounded by the credentials present in the window, not
    un-scanned conversation — and it stays under 2x: MEASURED max-density
    packing of the most-expanding rule (the slack ``xapp-`` form — 12 chars in,
    22 out, space-separated) is 1.768x, giving 8,840 chars for a body of exactly
    5,000 and 8,810 for the 6,000-char fixture in
    ``test_capped_reapplication_preserves_the_true_total`` once windowed with its
    marker re-attached.

    Idempotent in BOTH senses that matter. (1) No rule's ANCHOR GROUP can be
    satisfied inside a ``[REDACTED:<kind>]`` marker, so re-running the scrub
    adds no counts and changes no span — which is what lets the turn store, the
    Source and the extractor each apply it without multi-counting the same
    span. The reason is the ANCHOR, not the marker's character classes: the
    ``private_key`` body matches everything (including ``[``/``:``/``]``), and
    the AWS/bearer markers DO contain their anchor keyword but never the
    separator or whitespace that keyword's anchor group requires. (An earlier
    revision of this docstring gave the character-class reason, which is
    false; ``security.redact_secrets`` carries the corrected proof.)

    (2) The truncation marker is split OFF before the scan and re-attached
    VERBATIM, so re-applying to a body an expanding redaction has already pushed
    past the cap does NOT re-derive the marker's ``total`` from the scrubbed
    length (the #4897 P1). Re-application is therefore exact even though the
    returned body may exceed the cap by the redaction markup — a blanket
    "idempotent" claim that ignored this is exactly what would invite the P1
    back (a 6,927-char turn became ``original length 5,008`` on the second pass).
    """
    out: list[dict] = []
    totals: dict[str, int] = {}
    for turn in conversation:
        raw = turn.get("content")
        content = raw if isinstance(raw, str) else ("" if raw is None else str(raw))
        # #5472: emptiness is judged on the PRE-cut content. Judging it after
        # the cut made ``cap=0`` (which cuts any non-empty string to "") take
        # the genuinely-empty branch below and append the ORIGINAL, untruncated
        # turn — a cap that does not cap.
        had_content = bool(content)
        cut = False
        # #4897: split the truncation marker (if any) OFF the text that gets
        # SCANNED — a fail-closed redaction (``private_key``'s ``\Z`` branch)
        # would otherwise consume the marker and store a cut turn unmarked. The
        # split is UNCONDITIONAL and marker-agnostic: no decision here depends on
        # the marker, because this function no longer makes one (round 5).
        body, marker = _split_truncation_marker(content)
        # #4897 round-3 P1: the clip is decided on the MARKER-FREE BODY, never
        # on the marker's presence. A marker is caller-supplied, so trusting it
        # as proof of "already clipped" exempted the body from the cap and let
        # any client store an unbounded turn by appending a marker-shaped tail.
        # Clipping on ``len(body)`` bounds every caller — raw or windowed — while
        # leaving a body THIS module already clipped (and its true ``total``)
        # alone: the marker is reserved INSIDE the cap, so that body fits and is
        # not touched. A marker on a body PAST the cap cannot vouch for the
        # body, so the body is clipped and a fresh marker — derived from the
        # body's own length — replaces the caller's claim; ``_split_truncation_
        # marker`` pulls that fresh marker back out so the scan input stays
        # marker-free (the round-2 P2). Clip WITH the marker: a capped scan that
        # returned a silently-shortened string is the same defect one layer
        # down. This branch fires only when the caller passed an explicit
        # ``cap`` — production callers pass none (the window already clipped).
        if cap is not None and len(body) > cap:
            body, marker = _split_truncation_marker(
                _clip_capture_turn_content(body, cap))
            cut = True
        # #5445: the role is caller-controlled and persisted as text by three
        # sinks, so it is coerced and scrubbed through this same chokepoint. The
        # cap applies to it as well — ``_capture_turn_window`` already truncated a
        # string role, and re-applying here keeps the bound true for callers that
        # pass a raw conversation with an explicit ``cap``.
        role = _normalize_turn_role(turn.get("role"))
        if cap is not None and len(role) > cap:
            role = role[:cap]
        scrubbed_role, role_counts = redact_secrets(role)
        # #5472 emptiness is judged on the PRE-cut content (``had_content``,
        # above): a ``cap`` that cuts the turn to nothing must still emit the
        # CUT turn, not forward the original. A marker-only turn is not empty —
        # its marker is why it is stored at all — and ``had_content`` already
        # says so, so no separate body/marker test is needed here.
        if not had_content:
            # Genuinely-empty content: nothing in the body to scan. The role is
            # still caller-controlled, so it is scrubbed here too; the turn is
            # copied only when that changed.
            for kind, n in role_counts.items():
                totals[kind] = totals.get(kind, 0) + n
            if role_counts:
                out.append({**turn, "role": scrubbed_role})
            else:
                out.append(turn)
            continue
            continue
        scrubbed, counts = redact_secrets(body)
        if role_counts:
            counts = {**counts}
            for kind, n in role_counts.items():
                counts[kind] = counts.get(kind, 0) + n
        for kind, n in counts.items():
            totals[kind] = totals.get(kind, 0) + n
        # ⛔ Two reasons to emit a new object: a rule matched, or the opt-in
        # ``cap=`` path CUT the turn. On the production path (``cap is None``)
        # there is no clip branch and no ``cut``, so a no-match turn is returned
        # as the SAME object and nothing else about a stored shape moves.
        # #4897: re-attach the marker AFTER the scrub — no rule could have
        # consumed it, because it was never in the scanned text. ``marker`` is
        # the pre-scan string itself, so a prior cut's true ``total`` survives
        # a re-application exactly.
        if counts or cut:
            if role_counts:
                out.append({**turn, "content": scrubbed + marker,
                            "role": scrubbed_role})
            else:
                out.append({**turn, "content": scrubbed + marker})
        else:
            out.append(turn)
    return out, totals


def _redact_summary_strings(obj, _depth: int = 0):
    """Recursively scrub every ``str`` in a summary/payload object (#4911).

    The v1 commit path accepts a caller-supplied ``summary=`` that never passes
    through the conversation scrub, and that object is rendered into
    ``construct_graph``'s prompt (``json.dumps``) and POSTed to
    ``/v1/sessions/commit`` (whose writes carry no scrubber). Wrapping the
    object (rather than the conversation) is what keeps the ONE-control-site
    rule: this is still a single ``redact_secrets`` call per string, reached
    from one named function. Containers are copied, so the caller's object is
    never mutated.

    ⛔ KEYS are scrubbed too: ``json.dumps`` renders keys into the prompt, so an
    unscrubbed key is the same leak in a different position. Every key is passed
    through this function recursively, so a ``str`` key is redacted and a tuple
    key's ``str`` elements are redacted with it (a key that is neither a ``str``
    nor a container of them — e.g. ``bytes`` — is passed through, but such a key
    is not JSON-renderable: ``json.dumps`` raises ``TypeError`` before emitting
    any key, so it cannot carry a credential into the prompt). (If two keys
    redact to the same marker the later one wins — a duplicate key is not a
    summary shape, and the alternative is a leak.)

    ⛔ The depth guard is a REGRESSION fix, not a style choice: this runs on
    ``commit_session``, where a cyclic or very deep ``summary=`` previously
    reached ``construct_graph``, whose ``json.dumps`` raised ``ValueError`` and
    was swallowed (degraded, no crash). Unbounded recursion turned that into an
    unhandled ``RecursionError`` on a public method.

    ⛔ ...and it must FAIL CLOSED (#5446). The original guard returned the
    subtree VERBATIM past depth 64, so a credential nested deeper than 64 levels
    was rendered into the commit prompt (``json.dumps``) and POSTed unbounded.
    Past the bound a ``str`` leaf is still scrubbed (cheap at any depth) and a
    container is replaced by a visible marker. The trade-off is deliberate:
    a pathological deep summary is not a commit shape, so beyond the bound the
    subtree is COLLAPSED (legible, ``[REDACTED:depth]``) rather than silently
    omitted or returned raw. The recursion never passes depth 65, so the
    no-crash property the guard exists for still holds.
    """
    if _depth > 64:
        if isinstance(obj, str):
            return redact_secrets(obj)[0]
        return "[REDACTED:depth]"
    if isinstance(obj, str):
        return redact_secrets(obj)[0]
    if isinstance(obj, dict):
        return {_redact_summary_strings(k):
                _redact_summary_strings(v, _depth + 1) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_redact_summary_strings(v, _depth + 1) for v in obj]
    if isinstance(obj, tuple):
        return tuple(_redact_summary_strings(v, _depth + 1) for v in obj)
    return obj


def _capture_turn_frame(turn: dict) -> str:
    r"""The writer's stored frame ``"[role] <body>"`` for ONE WINDOWED turn.

    The single definition of the stored frame, shared by the writer
    (``_capture_turn_texts_with_redactions``) and — since #5394 — by the
    confirmation's RAW model of what the server stored
    (``session_confirm``), so the two cannot drift. The caller supplies an
    already-windowed turn (``_capture_turn_window``): NEITHER side re-clips
    here, because the cap was applied to the PRE-redaction text and its marker
    records that text's true length (#4897). Re-applying the cap to the
    SCRUBBED result would re-derive the length from text a #4911 replacement
    changed — writing a FALSE marker whose "original length" is the
    post-redaction size (6927 → 5008, reproduced in review) — so the stored
    body may exceed the cap by the redaction markup, which is a replacement,
    not conversation. A complete turn stays unmarked.

    ⛔ NEUTRALIZE THE FRAMING DELIMITERS IN THE ROLE (#5445 review). The frame
    is ``[<role>] <body>`` and ``<role>`` is caller-controlled, so a role
    containing ``]`` ends the frame early. That is not hypothetical: the #5445
    role scrub WRITES one — a role holding a credential becomes
    ``[REDACTED:github_token]``, and the stored text is
    ``[[REDACTED:github_token]] please review…``. The reader's inverse
    (``_CAPTURE_ROLE_PREFIX``, ``^\[([^\]]*)\]\s*``) then parses the speaker as
    ``[REDACTED:github_token`` — dropping the closing bracket — and hands the
    BODY a stray ``] ``, so ``:Point.speaker`` and the turn the read path serves
    are both corrupted on the exact path this cluster fixes. Brackets are
    rewritten in the ROLE only; the body is untouched. (The neutralization is
    also why a redacted ROLE is served as ``(REDACTED:<kind>)`` and not
    ``[REDACTED:<kind>]`` — ``session_confirm`` recognises both forms.)
    """
    role = _normalize_turn_role(turn.get("role"))
    role = role.replace("[", "(").replace("]", ")")
    raw = turn.get("content")
    content = raw if isinstance(raw, str) else ("" if raw is None else str(raw))
    return f"[{role}] {content}"


def _capture_turn_texts_with_redactions(
    windowed: list[dict],
) -> tuple[list[str], dict[str, int]]:
    """``_capture_turn_texts`` PLUS the per-kind redaction counts of the window.

    Split out (rather than returning a tuple from ``_capture_turn_texts``) so
    the five read-only callers keep the plain-list contract. The capture writer
    records the counts on the Session, and its count is the count of record
    because it scans the same window (and therefore the same spans) the
    ``:Source`` sink and the extractor do.

    ⛔ REDACTION-ONLY — this sink does NOT clip, and it REQUIRES a windowed
    conversation (its parameter is named ``windowed`` for that reason). The
    window is applied ONCE, on the RAW text, by the caller
    (``_capture_turn_window``): ``capture_session`` / hosted
    ``_capture_session_impl`` window at their entry, and
    ``session_confirm.expected_turns`` windows before calling here. This function
    then scrubs that window and must NOT re-clip the result: a #4897 marker
    carries a length a #4911 replacement cannot be allowed to rewrite, and
    re-clipping redacted text would re-derive the total from the scrubbed length
    (the round-1 P1; see ``_redact_turn_contents``). A credential that straddles
    the cut is therefore only partially there to match, and a shape still
    meeting its body floor is redacted while a fragment below the floor is not.
    Known residual, recorded in
    ``docs/scoping/2026-09-25-4911-capture-secret-redaction.md``: a credential cut
    mid-body by the window can leave a PREFIX — never the whole value, and never
    a marker — in the stored turn, and it is not counted.

    ⛔ The extraction consumers pass NO ``cap``, for two different reasons.
    ``_commit_session_v1`` / ``_commit_session_v2`` receive the RAW conversation
    and the extractor renders each turn verbatim into its prompt
    (``extractor_v2._edus_from_conversation`` does not window), so a cap there
    would both leave the tail un-scanned and silently truncate the extraction
    input — a fidelity loss with nothing to show for it (#4897's lesson); those
    two therefore pay a full linear scan of client-controlled text (measured
    ~3 s/MB). ``_extract_session_llm`` / ``_extract_session_v2`` DO window
    first — their input is the stored turn window (#721 parity), handed to them
    as ``_capture_extraction_window``'s already-clipped view — so their scan is
    bounded by that window and a ``cap`` would be a no-op. The hosted lane keeps
    the scan off the event loop, and the SDK-side call is bounded only by what
    the caller passes.
    """
    redacted, counts = _redact_turn_contents(windowed)
    # The frame (including the #5445 role-delimiter neutralization and the
    # #4897 no-re-clip rule) lives in ONE place, ``_capture_turn_frame``, so
    # the writer and the #5394 confirmation's RAW model cannot drift.
    return [_capture_turn_frame(turn) for turn in redacted], counts


def _capture_turn_texts(windowed: list[dict]) -> list[str]:
    """The exact stored turn text (``[role] <content>``) for each windowed turn.

    The stored-text definition (#4194) — the vector is computed over the string
    the node stores (the embedder applies its own 512-word cap;
    model/dimension/normalisation are shared with the read path, the cap is
    write-side), so a dense (vector) hit can never resolve to a turn whose
    stored content differs from what was encoded. The write path reaches
    ``_capture_turn_texts_with_redactions`` through ``_write_session_and_turns``;
    this function delegates to it and returns its first element. Coercion is the
    loop's own (isinstance-first: None -> "", non-strings -> ``str()``, #721).
    The cap and its marker are applied ONCE, to the pre-redaction text
    (``_capture_turn_window``), and this function must NOT re-apply them to the
    scrubbed result (#4897 — see ``_capture_turn_texts_with_redactions``);
    callers pass the windowed conversation.

    #4911: the scrub lives in ``_capture_turn_texts_with_redactions`` (via
    ``_redact_turn_contents``) — the ONE stored-text definition every writer
    reaches through ``_write_session_and_turns``; this function is its read-only
    projection. The control cannot live inside ``_write_capture_turns`` instead:
    the writer computes ``turn_embs`` from that same stored-text result, before
    ``_write_capture_turns`` stores the row, so redacting only at the write would
    store
    ``[REDACTED:…]`` while the vector described the raw secret — violating the
    #4194 invariant the docstring above states ("the vector can never describe
    different text than the node holds"). Redacting in the stored-text
    definition keeps the encoded text, the stored text and the ``content_hash``
    the same string by construction. A second, deliberate consequence: the #4675
    confirmation
    path (``tortoise/session_confirm.expected_turns``) calls THIS function to
    build what it expects the server to have stored, so the client's
    expectation and the server's stored row cannot drift for the same
    ``_SECRET_SHAPES`` table — redacting at the writer instead would make every
    session containing a secret compare unequal forever and never file. ⛔ The
    parity is VERSION-BOUND: a client running an older table than the server's
    does drift, and the deferred spool entry then never terminalises — that is
    the filed residual #5394, not an exception to the sentence above.
    """
    return _capture_turn_texts_with_redactions(windowed)[0]


#: The written turn prefix, and its inverse. A READER of a stored turn gets the
#: role and the body separately (``hosted_api.get_session_detail`` returns
#: ``role`` as its own field and the content with this prefix STRIPPED), so
#: anything comparing a stored turn against a served one must go through the
#: inverse — comparing the raw string never matches (#4675). One definition so
#: the writer's format and the reader's split cannot drift.
_CAPTURE_ROLE_PREFIX = re.compile(r"^\[([^\]]*)\]\s*")
#: ⛔ ``*``, not ``+`` (review round 12). A role can be EMPTY: ``_normalize_turn_role``
#: passes ``""`` through, the writer emits the frame ``"[] <body>"``, and a ``+``
#: quantifier refuses to match it — so the inverse returned the WHOLE string as the
#: body (``("unknown", "[] hello world")``) and the served turn was corrupted with a
#: stray ``[] `` prefix. That is the same corruption class round 11 closed for a role
#: containing a bracket, on the one remaining way a role can break the frame. An empty
#: role is recovered verbatim as ``""``, so writer and reader stay consistent.


def _capture_turn_role_text(stored: str) -> tuple[str, str]:
    """``(role, body)`` for one stored turn text — the inverse of the writer.

    Un-bracketed text reads as ``("unknown", text)``, matching what the server
    serves for it; the ``\\s*`` is significant, because the writer always emits
    exactly one space and a body that itself begins with whitespace would
    otherwise compare unequal on the read side (``"[user]  hi"`` -> ``"hi"``).
    """
    match = _CAPTURE_ROLE_PREFIX.match(stored)
    if match is None:
        return "unknown", stored
    return match.group(1), stored[match.end():]


#: The durable-session read projection (#3557) — see
#: `tortoise/session_projection.py` for the declaration and the binding notes.
#:
#: ⛔ Do NOT reintroduce a literal tuple at this site. The field list used to be
#: spelled here AND in the hosted handlers' inline columns, which is how
#: `session list` came to drop six of nine fields while its own API served
#: them (#5498). Name bound by the import at the top of this module.


def _capture_turn_embeddings(
    turn_texts: list[str],
    expected_dim: int | None,
) -> list[list[float] | None]:
    """Embed the stored turn texts with the SAME local embedder the query uses.

    (#4194) Episodic turn Points used to be written with raw Cypher and NO
    ``embedding``, so the dense leg had no material for captured turns and the
    ranking over them was keyword-only — while the read path paid to encode a
    query vector on every call. This routes the turn write through the existing
    embedder (the one ``create_point`` uses; no new mechanism, no LLM, local
    CPU only).

    ⛔ The stored vector MUST match the query encoder in MODEL, DIMENSION and
    NORMALISATION. A mismatched vector still "runs" and returns garbage, which
    is worse than the previous honest zero. So this calls the same shared
    ``compute_embeddings`` (same ``EmbeddingModel`` singleton, same
    un-normalised output) that ``compute_embedding`` delegates to — never the
    TF-IDF ``_encode`` fallback, whose dimensionality is a vocabulary size and
    would corrupt the dense leg silently. The 512-word cap is WRITE-side
    (``_truncate_for_embedding``); the read path encodes its query whole —
    model/dimension/normalisation are the shared contract, the cap is not.

    ``expected_dim`` is the CALLER's store width (``proj.required_embedding_dim``
    — :data:`EMBEDDING_DIM` when the store has a Point HNSW index, ``None`` when
    it has none, where the read path brute-force scans and any self-consistent
    width is storable). It is a REQUIRED argument, not defaulted: a default
    would have to pick one of the two answers, and the wrong one silently NULLs
    every vector on the lane that answer does not describe (#4280 review).

    The batch goes through :func:`embeddings.encode_batch_for_store`, which
    applies that width to the rows — the seam itself keeps its narrow shape so
    an installed ``EncodeCache`` (and the eval doubles) stay compatible.

    Model-load policy: this uses the embedder's OWN bound, exactly as
    ``create_point`` and the read path do — a capture does not get a shorter
    private timeout, because a timed-out load stamps the shared
    ``EmbeddingModel`` failure/cooldown state and would turn a cold capture
    into a ~60 s dense-leg outage for the READ path too. The engine-init
    warm-up (#2952) and hosted's container pre-warm mean the wait is normally
    on an already-in-flight load. A cold/unavailable model fails soft to
    ``None`` per turn (the turn is still stored; the read path declares the
    leg impaired).
    """
    try:
        from .embeddings import encode_batch_for_store
        return encode_batch_for_store(turn_texts, expected_dim)
    except Exception:  # noqa: BLE001, RUF100 — embedding is optional
        # Embedding is optional, but a WHOLE-BATCH failure must stay audible:
        # silently returning `None` per turn is indistinguishable from "the
        # leg ran and found nothing", which is how #4280 hid (review finding).
        _logger.warning(
            "turn embedding batch failed — %d turn(s) stored with no vector "
            "(the dense leg degrades to keyword-only for them).",
            len(turn_texts), exc_info=True,
        )
        return [None] * len(turn_texts)


# ── #3086: ONE turn-stream writer, shared by both capture lanes ────────────
# The per-turn store used to be a loop duplicated between
# `TortoiseSDK.capture_session` and hosted `_capture_session_impl` (kept in
# sync by convention — the drift class #2813 exists to delete), and that loop
# issued TWO FalkorDB round-trips per turn ON the event loop: a node `MERGE`
# then a `CONTAINS` edge `MERGE`. For a 500-turn capture that is ~1000
# blocking calls — the ~4.75s single-loop freeze #3086 measures.
#
# This is the ONE turn store — reached by the primitive both lanes call. The
# batch is a single
# `UNWIND $turns` statement, i.e. ONE transaction per capture: a runtime error
# anywhere in `$turns` rolls the WHOLE batch back, so a failed batch leaves no
# turn written without its CONTAINS edge (never a partially-wired session).
# Per-row idempotency is what makes a RETRY of a failed batch safe, and it is
# free here — the turn ids are already deterministic
# (`{session_id}_t{i}`), so a retried batch MERGEs the same rows and converges.
# (Precondition, stated in the docstring: the Session must already exist — the
# statement MATCHes it, so a missing Session leaves the batch's nodes
# unwired. Its caller, ``_write_session_and_turns``, MERGEs it immediately
# before.)
_TURN_WRITE_CYPHER = (
    "UNWIND $turns AS turn "
    # `MERGE (t:Point {id:...})` binds the node FIRST: a full-path
    # `MERGE (s)-[:CONTAINS]->(t)` with a missing edge makes FalkorDB create
    # the whole path from scratch, duplicating the Point (#490 review P2-2).
    "MERGE (t:Point {id: turn.id}) "
    # `prior_ch` is read per row BEFORE the SET reassigns it, so the
    # stale-vector decision (#4194) is sound on a matched node and on a
    # just-created one (prior_ch NULL => nothing to preserve).
    "WITH t, turn, t.content_hash AS prior_ch "
    # #4524/#4520: the shared clear-then-set seam clears t.embedding in the
    # SAME statement (and only when a new vector is written), so the rotation
    # self-heal this statement exists to provide is not defeated by the embedded
    # engine's silent no-op on a `vecf32` overwrite. See `tortoise/vector_write.py`.
    + clear_then_set_prefix("t", "turn.emb")
    + "SET t.content=turn.c, t.pointKind=turn.k, t.is_operator=false, "
    "    t.speaker=turn.speaker, t.is_episodic=true, "
    "    t.status=coalesce(t.status, turn.s), "
    "    t.createdAt=coalesce(t.createdAt, $now), "
    "    t.updatedAt=$now, t.content_hash=turn.ch, "
    # Three-way guard: new vector if we encoded one; else PRESERVE the stored
    # vector only when the content is UNCHANGED; else CLEAR it — a preserved
    # vector for changed text would rank the turn by text no longer on the
    # node (the dense-leg lie).
    "    t.embedding=CASE WHEN turn.emb IS NOT NULL THEN vecf32(turn.emb) "
    "        WHEN prior_ch = turn.ch THEN t.embedding ELSE NULL END "
    "WITH t, turn "
    "MATCH (s:Session {id:$sid}) "
    "MERGE (s)-[:CONTAINS]->(t) "
    # #4911: the per-session redaction count rides the SAME statement as the
    # turn write, so it cannot be separated from it by a later failure and it
    # costs no extra round trip (the #3086 hot-path query count is unchanged).
    # The value is the whole window's total, identical on every row, so the
    # per-row SET is idempotent; a re-capture OVERWRITES it with the count for
    # the content now stored (the honest reading — the property describes the
    # rows the session currently holds, not the life of the session id).
    "SET s.capture_redactions=$redactions "
    # Read back what the graph HOLDS (the COALESCE owns createdAt/status) so
    # the journal records the stored values, never the literal `now`/`draft`
    # — emitting those regresses a promoted turn to draft on replay and
    # drifts createdAt on every re-capture (#3947 review F4).
    "RETURN turn.id AS id, t.createdAt AS createdAt, t.status AS status, "
    # #5004 round-3: the embedding the write ACTUALLY left on the node. On a
    # re-capture with the embedder unavailable the CASE above PRESERVES the
    # prior vector, and this read-back is the only way to journal what
    # survived — journalling `turn.emb` (NULL) instead made the replay take
    # the recompute branch and OVERWRITE the vector the first capture
    # journalled, reintroducing exactly the divergence #5004 removes.
    "       t.embedding AS embedding"
)


# #10000: §9.1/D1 places the raw transcript in Postgres — "not in the graph, not
# a `Document`, not a `Point`". The per-turn `PointAdded` records this writer
# emits ARE that Postgres write (the projection folds them into `public.point`
# with `is_episodic = true`), so when the record can hold the turns the graph
# keeps only the Session's redaction count: one node write, no rows, no
# `CONTAINS` edges.
_SESSION_REDACTIONS_CYPHER = (
    "MATCH (s:Session {id:$sid}) SET s.capture_redactions=$redactions"
)


def _capture_turn_id(session_id: str, index: int, turn_offset: int = 0) -> str:
    """The deterministic per-turn Point id: ``{session_id}_t{index + offset}``.

    #3551: the ONE definition of the turn-id derivation for the capture lanes
    (``_write_session_and_turns`` / ``_write_capture_turns`` and the entity-link
    pass). ``turn_offset`` shifts the whole window, so an APPEND onto an existing
    turn list mints the next contiguous ids instead of re-MERGing index 0 — the
    offset-aware derivation the shared primitive owns. At the capture lanes'
    offset 0 the output is byte-identical to the pre-refactor literal
    (``f"{session_id}_t{i}"``).

    The CLIENT's constructor (``session_confirm.turn_point_id``) is deliberately
    its own copy: ``tests/test_session_confirm.py`` re-parses this module, drops
    this function's docstring, and asserts the unparsed BODY still formats
    ``{session_id}_t<index>`` — so the module's prose cannot satisfy the pin. It
    is pinned by a test rather than an import (a version-bound parity — a
    client on an older table cannot be made to import this module).
    """
    return f"{session_id}_t{index + turn_offset}"


def _capture_turn_ids(proj, session_id: str) -> list[str]:
    """Every turn-Point id currently ``CONTAINS``-wired to ``session_id``.

    #1920: turns are ``{session_id}_t{i}`` (``is_episodic=true``,
    ``pointKind='event'``). The extraction lane ALSO ``CONTAINS``-wires claim
    Points into the same :Session, so the match filters on the turn id SHAPE —
    the prefix PLUS an all-digit index — never the prefix alone: a
    ``session_id`` that itself ends in ``_t<i>`` would otherwise make another
    session's turn ids prefix-matches of this one (``sec`` vs ``sec_t0``).
    """
    prefix = f"{session_id}_t"
    rows = proj.g.query(
        "MATCH (s:Session {id:$sid})-[:CONTAINS]->(t:Point) "
        "WHERE t.is_episodic = true AND t.pointKind = 'event' "
        "AND t.id STARTS WITH $prefix RETURN t.id",
        params={"sid": session_id, "prefix": prefix},
    ).result_set
    # str-only: a non-string id would make the suffix slice raise, and no
    # writer ever stores one (the same str-only posture the replay folds take).
    # ``isdecimal()`` — NOT ``isdigit()`` — is the right guard for the class it
    # was introduced for: ``'²'.isdigit()`` is True while ``int('²')`` raises,
    # so an ``isdigit()`` guard admitted an id the sweep then could not index
    # (#3551 review). It is NOT by itself sufficient for ``int()``: ``int()``
    # refuses a decimal string longer than its conversion limit (4300 digits by
    # default), so ``isdecimal()`` being True does not imply parseable. The
    # sweep therefore does not ASSUME a total parse — it wraps its own and
    # RETAINS an id it cannot place (the guard EXCLUDES the ``²``-class; this
    # layer retains an id the guard ADMITTED), so no id returned here can make
    # the sweep raise. (``int()`` also parses
    # suffixes ``isdecimal()`` rejects, e.g.
    # ``'-1'``/``' 3'``/``'3_0'``; those are not ids this writer mints —
    # ``_capture_turn_id`` formats an ``int`` — so they are left untouched
    # rather than swept, never deleted where we cannot place them.)
    return [r[0] for r in (rows or [])
            if isinstance(r[0], str) and r[0][len(prefix):].isdecimal()]


@_declares_apply_first_write_lane("_write_capture_turns")
def _write_capture_turns(
    proj,
    sdk,
    session_id: str,
    windowed: list[dict],
    *,
    now: str,
    turn_embs: list[list[float] | None],
    session_existed: bool = True,
    texts_and_counts: tuple[list[str], dict[str, int]] | None = None,
    turn_offset: int = 0,
    prior_turn_max_index: int = -1,
) -> int:
    """Write a capture's episodic turn stream — to the RECORD when it can hold it (#10000).

    The ONE turn store, called by ``_write_session_and_turns`` — which BOTH
    capture lanes call: ``TortoiseSDK.capture_session`` (sync, no loop to free)
    and hosted ``_capture_session_impl`` (off the event loop, on
    ``_CAPTURE_EXECUTOR``). It replaces the per-turn loop the two lanes each
    kept, so the turn store can no longer fork.

    ``turn_embs`` must be the batch from ``_capture_turn_embeddings`` over
    ``_capture_turn_texts(windowed)``. The stored text is the caller-supplied
    pair (``texts_and_counts``) on the live path; when it is ``None`` this
    function re-derives it with
    ``_capture_turn_texts_with_redactions(windowed)`` (the retained branch below,
    currently unreached) — either way the vector is derived from the same text,
    so it can never describe different text than the node holds (#4194); a
    length mismatch (unreachable from the caller) drops the whole batch's
    vectors with an error log rather than misaligning them.

    ``texts_and_counts`` lets a caller that has ALREADY computed
    ``_capture_turn_texts_with_redactions(windowed)`` hand the result in rather
    than pay for a second scrub of the same client-controlled text (the scrub is
    ~3 s/MB, so a second consumer of the same texts costs seconds of CPU per
    legal-maximum capture — #4911 cycle 1). Its ONE caller is
    ``_write_session_and_turns``, which derives the pair from this same window
    and always forwards it, so the ``None`` branch is retained for direct
    callers and is currently unreached. Either way the count is read from this
    same window, so a donation cannot make the two disagree.

    ``turn_offset`` (#3551) shifts the id window: row ``i`` MERGEs
    ``_capture_turn_id(session_id, i, turn_offset)``. At the capture lanes'
    offset 0 the output is byte-identical to the pre-refactor
    ``f"{session_id}_t{i}"``; a non-zero offset is an APPEND, and the
    stale-turn sweep is bounded to ids at or beyond ``turn_offset +
    len(window)`` so it can never delete the prefix it is appending to.

    The Session MUST already exist (``_write_session_and_turns`` MERGEs it
    immediately before) — the statement both node- and edge-writes, and a
    missing Session would leave nodes unwired. ``proj`` is the caller's
    projection (the SDK lane passes ``self._get_proj()``); ``sdk`` supplies the
    journal seam.

    A SHORTER re-capture DELETES the turns the previous capture left beyond
    the new window (#1920): the merge alone keeps them on the graph AND
    ``CONTAINS``-wired, so the Session's episodic ``CONTAINS`` members stop
    matching the ``turn_count`` the same capture just stored. The removal is
    a hard ``DETACH DELETE``, journaled as an ``EntityMutated`` op=delete
    (the ``delete_point`` record) so a rebuild cannot resurrect the orphan.

    ``session_existed`` is the caller's own pre-MERGE probe: when False the
    Session had no prior capture, so there can be no stale turn to scan for
    and the prune read is skipped (the fresh-capture hot path keeps its exact
    query count — #3086 measures this path). It DEFAULTS TO TRUE — the
    conservative direction: a caller that omits it prunes, so forgetting the
    parameter cannot silently reinstate #1920.

    #4911: RETURNS the number of credential-shaped spans redacted from this
    window, and writes the same number to the Session as ``capture_redactions``
    in the batched statement (both lanes reach this writer, so the count is
    recorded per session at the ONE chokepoint). The text itself was already
    scrubbed upstream in ``_capture_turn_texts_with_redactions`` (via
    ``_redact_turn_contents``) — ``_capture_turn_texts`` carries why the control
    cannot live here — and the count is taken from the SAME
    window, so it covers the spans the session ``:Source`` sink and the
    extractor scrub over the same stored window. When the Source is handed the
    raw conversation these are different OBJECTS holding the same first
    ``_CAPTURE_TURN_CAP`` characters of each turn, which is all either of them
    persists. Both lanes surface it on their capture receipt.
    """
    # #7369 review r8: this writer holds the ONE ``UNWIND $turns`` statement,
    # whose rows MERGE on a ROW FIELD (``MERGE (t:Point {id: turn.id})``). The
    # parameter boundary deliberately does NOT null a row merge key — a null key
    # is refused by the engine, so nulling it would swap one abort for another —
    # which means the FOLD owns the skip, exactly as the top-level identity
    # anchors do via ``_writable_id``. Every row id is derived from
    # ``session_id`` (``_capture_turn_id`` → ``f"{session_id}_t{i}"``), so an
    # unwritable ``session_id`` makes EVERY row id unwritable: skip the whole
    # batch, WARN, and issue no statement. With this, no unwritable ROW merge
    # key can reach the engine from this writer (see `tests/...boundary.py`,
    # `test_an_unwritable_row_merge_key_is_skipped_by_the_fold_not_forwarded`).
    from tortoise.projection import _log_identity_skip, _writable_id
    if not _writable_id(session_id):
        _log_identity_skip("capture turn batch", session_id, "session id")
        return 0
    if texts_and_counts is None:
        turn_texts, redaction_counts = _capture_turn_texts_with_redactions(windowed)
    else:
        turn_texts, redaction_counts = texts_and_counts
    redacted_total = sum(redaction_counts.values())
    if not turn_texts:
        return redacted_total
    if len(turn_embs) != len(turn_texts):
        # Unreachable from the caller (it derives `turn_embs` from the same
        # `_capture_turn_texts_with_redactions(windowed)` result it passes as
        # `texts_and_counts`) and deliberately NOT a raise:
        # this helper runs after the caller's Session MERGE, so raising would
        # 500 a capture whose Session is already committed. Drop the whole
        # batch's vectors rather than misaligning them — the turns are still
        # stored and the read path declares the vector leg impaired (the
        # file's fail-soft posture), with the error audible in the log.
        _logger.error(
            "turn embedding batch has %d rows for %d turns — storing all %d "
            "turn(s) with no vector (the dense leg degrades to keyword-only)",
            len(turn_embs), len(turn_texts), len(turn_texts))
        turn_embs = [None] * len(turn_texts)
    turn_rows: list[dict] = []
    turn_hashes: list[str] = []
    for i, _turn in enumerate(windowed):
        text = turn_texts[i]
        text_hash = _content_hash(text)
        turn_hashes.append(text_hash)
        turn_rows.append({
            "id": _capture_turn_id(session_id, i, turn_offset),
            "c": text,
            "k": "event",
            # #5445: derived from the SCRUBBED stored text, not from the raw
            # ``turn["role"]`` — the role is caller-controlled and the `role`
            # sink must inherit `_redact_turn_contents`'s scrub. Going through
            # the reader's own inverse (`_capture_turn_role_text`) also makes
            # the stored `speaker` agree with what `get_session_detail` serves
            # for the same row (#4675 parity).
            "speaker": _capture_turn_role_text(text)[0],
            "s": "draft",
            "ch": text_hash,
            "emb": turn_embs[i],
        })

    # #7405 (audit G8): the batched turn store rides the SAME bounded write
    # retry as `create_point`'s direct writes. Before this it was a bare
    # `proj.g.query`, so a rebuild's per-query abort landed AFTER the Session
    # MERGE had committed and left the session with NO turns — the episodic leg
    # the capture exists for, missing, with the caller told only that a write
    # failed. The statement is IDEMPOTENT, which is what makes re-issuing it
    # safe: every row MERGEs `(t:Point {id: turn.id})` (the id derives from
    # `session_id`, so a re-issue targets the same node), the CONTAINS edge is
    # a MERGE, and `status`/`createdAt` use `coalesce` — a re-issued statement
    # can neither duplicate a turn nor demote or re-stamp a stored one.
    # #10000: WHICH STORE holds the turns is a CAPABILITY question, never a
    # policy flag. The per-turn `PointAdded` records below are gated on a
    # configured journal (`sdk._get_event_log()`), and the record is armed only
    # with `TORTOISE_RECORD_DSN`; if either is absent the graph is the only store
    # that can hold the turns, so this writer keeps both paths and selects by
    # what is actually available. Removing the graph write unconditionally would
    # write the transcript to NO store on an unarmed deployment — silent loss of
    # the capture's whole point.
    from tortoise.record_writer import record_dsn as _record_dsn
    _record_holds_turns = (sdk._get_event_log() is not None
                           and _record_dsn() is not None)
    if _record_holds_turns:
        sdk._graph_write_with_retry(
            lambda: proj.g.query(
                _SESSION_REDACTIONS_CYPHER,
                params={"sid": session_id, "redactions": redacted_total},
            ),
            what="capture session redaction count",
        )
        # No graph read-back: the record holds createdAt/status/embedding, and the
        # value this writer records next IS the stored value. #5004's
        # preserved-vector branch cannot be reached from here — there is no node
        # whose prior vector could have been preserved.
        stored: dict[str, tuple] = {}
    else:
        _rows = sdk._graph_write_with_retry(
            lambda: proj.g.query(
                _TURN_WRITE_CYPHER,
                params={"turns": turn_rows, "sid": session_id, "now": now,
                        "redactions": redacted_total},
            ),
            what="capture turn batch write",
        ).result_set
        stored = {r[0]: (r[1], r[2], r[3]) for r in (_rows or [])}

    # #1920, re-homed by #10000: a SHORTER re-capture must remove the previous
    # capture's turns beyond the new window, or the stored transcript keeps rows
    # the session no longer holds. The candidates come from the ID SPACE — the
    # high-water mark read before the MERGE moves it — not from a node walk,
    # because after #10000 the turns are not graph nodes to walk
    # (``_capture_turn_ids`` would return nothing and the surplus rows would keep
    # being served). A non-zero ``turn_offset`` is an APPEND, so only ids at or
    # beyond ``turn_offset + len(window)`` are stale.
    stale: list[str] = []
    if session_existed:
        _first_live = turn_offset + len(turn_rows)
        if prior_turn_max_index >= _first_live:
            stale = [_capture_turn_id(session_id, _i)
                     for _i in range(_first_live, prior_turn_max_index + 1)]
        # Which store the removal targets is the same capability question as the
        # write above: with the record holding the turns there is no node to
        # delete, and the record delete is the `PointRetracted` +
        # `EntityMutated` pair emitted below (folded as a hard delete).
        if stale and not _record_holds_turns:
            sdk._graph_write_with_retry(
                lambda: proj.g.query(
                    "MATCH (s:Session {id:$sid})-[:CONTAINS]->(t:Point) "
                    "WHERE t.id IN $ids DETACH DELETE t",
                    params={"sid": session_id, "ids": stale},
                ),
                what="capture stale-turn sweep",
            )

    # #3947: journal the turn writes so a rebuild can recreate them.
    # `contains_session` rides the EVENT ENVELOPE, not the point payload —
    # the CONTAINS link is a capture-write structural fact (ONTOLOGY §4.5),
    # restored by the projection's edge fold without inventing a node
    # property the live write never sets. Gated on a configured journal: on a
    # lane with no `event_log_path` (a hosted SDK when
    # `TORTOISE_EVENT_LOG_BASE_DIR` is unset; #4240) the JSONL half is a
    # no-op and the `:GraphEvent` half is not a rebuild source —
    # `ensure_event_schema` + `next_seq` + `append_event` per turn
    # there buys no durability on the very lane #3086 measures.
    if sdk._get_event_log() is None:
        return redacted_total
    # #1920 (durability half): the stale-turn delete above is a hard delete,
    # so it needs the SAME honest JSONL record ``delete_point`` emits —
    # ``EntityMutated`` op=delete, whose fold (``_delete_entity_by_id``) is
    # the hard delete. Without it a rebuild replays the FIRST capture's
    # ``PointAdded`` records for the deleted ids and resurrects every orphan;
    # ``PointRetracted`` is deliberately NOT emitted (its fold tombstones,
    # the wrong meaning for a removal — #3300/#3299).
    for tid in stale:
        # Two stores, one meaning each (#3300), mirroring ``delete_point``:
        # the turn write made every turn visible as a ``PointAdded``
        # GraphEvent, so the hard delete must be visible on the same
        # subscriber surface. The JSONL half is the ``EntityMutated``
        # op=delete record the replay folds — NOT ``PointRetracted``, whose
        # fold tombstones (the wrong meaning for a removal).
        sdk._emit_event("PointRetracted", _deleted_point_payload(tid))
        sdk._journal_entity_mutation("Point", tid, "delete")
    for i, _turn in enumerate(windowed):
        turn_id = _capture_turn_id(session_id, i, turn_offset)
        created_at, status, stored_emb = stored.get(turn_id, (None, None, None))
        # #5004: the turn's vector WAS stored live (`turn_embs[i]` is the `emb`
        # column of `_TURN_WRITE_CYPHER`), so it must be journalled too — this
        # is the highest-volume Point producer, and without it a replay
        # re-encoded under the configured embedder and silently changed every
        # captured turn's vector.
        #
        # #5004 round-3: when THIS capture encoded no vector (`turn_embs[i]`
        # is None — the embedder was unavailable) the live write may still
        # PRESERVE the node's prior vector (same content), or CLEAR it
        # (changed content). Journal `stored_emb`, the value the write left,
        # NOT `turn_embs[i]`: the key is ALWAYS present, so the replay is told
        # the journal OWNS this field for this id and must restore-or-leave-
        # unset rather than re-encode under whatever model is active.
        _turn_snapshot = {
            "id": turn_id,
            "content": turn_texts[i],
            "pointKind": "event",
            # #5445: the redacted role (see the turn_rows write above).
            "speaker": _capture_turn_role_text(turn_texts[i])[0],
            "is_episodic": True,
            "status": status if status is not None else "draft",
            "createdAt": created_at if created_at is not None else now,
        }
        _turn_snapshot["embedding"] = (
            turn_embs[i] if turn_embs[i] is not None else stored_emb)
        # #5004 round-3: when THIS capture encoded nothing, what rides the
        # record is the vector the write PRESERVED — computed by whatever model
        # was active at the ORIGINAL capture. Attesting the active model here
        # would record a false origin (and make a model change look silent if
        # the original record is gone), so say the vector was preserved and
        # `stamp_journal_embedding` stamps no identity.
        if turn_embs[i] is None and stored_emb is not None:
            _turn_snapshot["embedding_preserved"] = True
        sdk._emit_event(
            # Parity with `create_point`'s emission (#3947 review F5): the
            # PAYLOAD carries `content_hash`. It belongs here and NOT in the
            # `point` snapshot, which `_emit_event` strips it from
            # (`content_hash` is derived, and the replay recomputes it in
            # `_upsert_point_props`).
            "PointAdded",
            {"id": turn_id, "kind": "event",
             "content_hash": turn_hashes[i]},
            point=_turn_snapshot,
            contains_session=session_id,
        )

    # #4911: the live `SET s.capture_redactions` above has no journal carrier
    # without this record — a journal-only ``rebuild()`` / ``recover_from_log``
    # would restore the Session with the field null, and a null is exactly the
    # "no secret was ever redacted" reading this property exists to falsify.
    # The same review lesson as #3664/#3722 (`capture_ok`, the entity-link
    # counters): a raw graph SET on the capture path MUST emit a trailing
    # ``SessionRecorded`` under the same gate as the write, or live and replay
    # diverge. ``_fold_session_recorded`` carries the property too.
    sdk._emit_event(
        "SessionRecorded", id=session_id,
        capture_redactions=redacted_total)
    return redacted_total


@_declares_live_write_lane("_write_session_and_turns")
def _write_session_and_turns(
    proj,
    sdk,
    session_id: str,
    turns: list[dict],
    *,
    now: str,
    harness: str | None = None,
    capture_lane: str | None = None,
    client_captured_at: float | None = None,
    client_captured_at_source: str | None = None,
    source: str | None = None,
    actor_user_id: str | None = None,
    machine_id: str | None = None,
    model: str | None = None,
    turn_offset: int = 0,
    embed_fn: Callable[[list[str]], list[list[float] | None]] | None = None,
    turn_embs: list[list[float] | None] | None = None,
    texts_and_counts: tuple[list[str], dict[str, int]] | None = None,
    session_existed: bool = True,
    on_session_merged: Callable[[dict], None] | None = None,
) -> dict:
    """The ONE shared Session + turn store writer (#3551).

    Called from BOTH capture lanes: ``TortoiseSDK.capture_session`` (sync, no
    loop to free) and hosted ``_capture_session_impl`` (off the event loop, on
    ``_CAPTURE_EXECUTOR``). It owns everything the two lanes used to keep
    BYTE-IDENTICAL COPIES of and could therefore drift on:

      * the ``:Session`` MERGE field list (``created_at``/``turn_count``/
        ``is_episodic`` + the conditional ``harness``/``capture_lane``/
        ``actor_user_id``/``machine_id``/``model`` clauses);
      * the per-turn MERGE field list, the turn-point Cypher text, the
        ``CONTAINS`` wiring and the stale-turn sweep — all delegated to
        ``_write_capture_turns``, which holds the ONE ``UNWIND $turns``
        statement (#3086);
      * offset-aware turn-id derivation (``_capture_turn_id``), so a caller
        that appends a window does not re-MERGE index 0;
      * the optional ``embed_fn`` call — ``None`` is a NO-OP default, so the
        deferred embedding work flips a switch instead of structurally editing
        a multi-caller function.

    STAYS IN EACH CALLER (this is the boundary the lane comments cite —
    admission/reservation, quota preflight, replay-skip/``retry_failed_capture``,
    the abandoned-capture marker, receipt writing, extraction invocation and all
    HTTP/error mapping):

      * ``harness`` is RESOLVED by the caller. Hosted passes
        ``_observed_capture_harness`` (first-writer-wins against the stored
        value); the SDK passes the caller-supplied argument. This function
        writes whatever it is given, set-only-when-present — it never erases a
        stored value with ``None``.
      * ``machine_id`` / ``model`` are CLIENT-CLAIMED and HOSTED-ONLY today
        (#2599). The SDK lane deliberately passes neither: ``derive_machine_id``
        is machine-local, so stamping it at SDK/audit time would misattribute
        the machine that actually captured the session. That absence is
        RECORDED (``tests/test_write_session_and_turns_3551.py``), not a gap to
        close here.
      * ``session_existed`` is the caller's own pre-MERGE probe (it also drives
        the caller's replay/retry gate); it is forwarded to the turn writer so
        the fresh-capture hot path keeps its exact query count.
      * ``on_session_merged`` is an OBSERVER hook fired after the MERGE. It is
        NOT the journal carrier any more (#9206: this writer appends
        ``SessionRecorded`` itself, before the MERGE), so ``None`` — or any
        observer — costs nothing in durability. ⚠️ No caller passes one today (the
        SDK lane's was removed with the same change; hosted never had one), so
        the hook exists for observers only.

    ⛔ §17.1 (#9206): this writer carries `@_declares_live_write_lane` — it
    appends its OWN `SessionRecorded` before the `:Session` MERGE, so the MERGE
    is journal-first. ⚠️ It also delegates the turn store to
    `_write_capture_turns`, which carries its own APPLY-FIRST declaration
    (`@_declares_apply_first_write_lane`): the turn Points' `PointAdded` records
    follow their batched write. That nested claim is stated where it lives, not
    inherited from this door's stronger one.

    ⛔ ``machine_id`` and ``model`` MUST keep the set-if-absent ``coalesce``
    semantics. A plain ``SET s.machine_id=$mid`` would let a re-capture from a
    SECOND machine overwrite the first machine's id — the subtle regression this
    extraction exists to prevent.

    Returns the write facts the callers READ — the stored turn texts (hosted
    reuses them for its entity-link pass instead of re-scrubbing) and the total
    redaction count — plus the rest of the post-write state (the offset-aware
    turn ids, the per-kind redaction counts, the ``Session`` record and the
    MERGE params), which nothing reads today. The journal emit is THIS BODY'S
    OWN (``_emit_event("SessionRecorded", **session_record)``, before the MERGE
    — #9206); ``session_record`` is the same dict it passes, and
    ``on_session_merged`` is an observer hook that journals nothing.
    """
    turn_count = len(turns)
    # The canonical :Session field list. Order is load-bearing only in that it
    # is the byte-identical text the pre-refactor lanes emitted; the conditional
    # clauses mirror the #1727 harness rule and the #2600 actor rule.
    merge_sets = ["s.created_at=coalesce(s.created_at, $now)",
                  "s.turn_count=$tc", "s.is_episodic=true",
                  # #10000: `turn_count` is a WINDOW LENGTH (``len(turns)``), not
                  # a max id index — an APPEND at ``turn_offset=2`` writes
                  # ``_t2``/``_t3`` while the count reads 2. The stale-turn sweep
                  # needs the ID SPACE, so the high-water mark is stored beside
                  # the count and only ever moves UP (an append must not lower it).
                  "s.turn_max_index=CASE WHEN s.turn_max_index IS NULL "
                  "OR s.turn_max_index < $tmi THEN $tmi ELSE s.turn_max_index END"]
    merge_params: dict[str, Any] = {"sid": session_id, "now": now,
                                    "tc": turn_count,
                                    "tmi": turn_offset + turn_count - 1}
    session_record: dict[str, Any] = {
        "id": session_id, "created_at": now,
        "turn_count": turn_count, "is_episodic": True,
        # #10000: the id-space high-water mark rides the SAME record as the count,
        # so a record-sourced rebuild restores the property the stale-turn sweep
        # reads (a replay that dropped it would leave the sweep unable to see a
        # surplus it must remove).
        "turn_max_index": turn_offset + turn_count - 1,
    }
    if harness:
        merge_sets.append("s.harness=$harness")
        merge_params["harness"] = harness
        session_record["harness"] = harness
    # #3516 §B: the producer lane ('hook' | 'store_sync'). Set-only-when-present
    # AND first-writer-wins — the SAME effective rule as harness, whose plain
    # SET is made first-writer-wins by its caller's resolution
    # (`_observed_capture_harness`). The coalesce is load-bearing: the store-sync
    # backstop ships the SAME session AFTER the hook (#3515 piece 7), so a plain
    # SET would RELABEL a hook session to 'store_sync' and make the hook-liveness
    # check report a WORKING hook as not-live. A lane-less re-capture
    # (backfill/import, or a pre-#3516 producer) never erases either.
    if capture_lane:
        merge_sets.append("s.capture_lane=coalesce(s.capture_lane, $capture_lane)")
        merge_params["capture_lane"] = capture_lane
        session_record["capture_lane"] = capture_lane
    # #3516 §B / #3515 piece 12: the CLIENT-recorded capture instant and the
    # source it came from. Same set-only-when-present + coalesce discipline as
    # the lane above, and for the same reason: the store-sync backstop ships the
    # SAME session after the hook, so a plain SET would let the later, weaker
    # source (a file mtime) overwrite the hook's own observation time — and the
    # floor would then compare an mtime against an install instant it was never
    # about. Absent never erases a stored value.
    if client_captured_at is not None:
        # #3516 §B / #3515 piece 12: the instant and the clock it came from are
        # ONE PAIR, written together. A source NAMES the clock that PRODUCED the
        # instant, so coalescing the two INDEPENDENTLY (as this did) keeps an
        # earlier writer's instant and hangs a later writer's clock on it — and
        # because an absent source PASSES the floor while an explicit 'unknown'
        # DISABLES it, the client-observed session then reads unverifiable
        # (reproduced: PASSED -> DISABLED). This is the SINK every writer
        # converges on, so the pair has to hold here, not only in the spool.
        #
        # ORDER IS DELIBERATE, but the CASE — not the order — is what makes this
        # correct. MEASURED on FalkorDB: every `SET` right-hand side is evaluated
        # against the row as it was at the START of the `SET` clause, so
        # `s.client_captured_at` here still reads the PRE-update value no matter
        # which clause comes first. The CASE is therefore what adopts the source
        # exactly when the instant is adopted, and never otherwise. (Verified by
        # mutation in round 3: reversing the two clauses leaves every test green,
        # which is why the ordering is NOT claimed as load-bearing.)
        if client_captured_at_source:
            merge_sets.append(
                "s.client_captured_at_source=CASE WHEN s.client_captured_at IS NULL "
                "THEN $cosrc ELSE s.client_captured_at_source END")
            merge_params["cosrc"] = client_captured_at_source
        merge_sets.append(
            "s.client_captured_at=coalesce(s.client_captured_at, $cout)")
        merge_params["cout"] = client_captured_at
        session_record["client_captured_at"] = client_captured_at
        # The source rides WITH the value, never alone: a source with no instant
        # would let a later reader claim a provenance for a timestamp it does not
        # have (piece 12 — 'unknown' must be excludable from a pass).
        if client_captured_at_source:
            session_record["client_captured_at_source"] = client_captured_at_source
    # #3516 §D: the transcript stem the capture came from. Set-only-when-present
    # AND set-if-absent (``coalesce``), the machine_id/model rule below rather
    # than the harness rule: the store-sync backstop ships the SAME session
    # after the hook, and a backstop entry that carries a different (or no)
    # transcript stem must never overwrite the stem the hook observed — a
    # plain SET would let the weaker, later producer relabel the record.
    if source:
        merge_sets.append("s.source=coalesce(s.source, $src)")
        merge_params["src"] = source
        session_record["source"] = source
    if actor_user_id:
        merge_sets.append("s.actor_user_id=coalesce(s.actor_user_id, $uid)")
        merge_params["uid"] = actor_user_id
        session_record["actor_user_id"] = actor_user_id
    if machine_id:
        merge_sets.append("s.machine_id=coalesce(s.machine_id, $mid)")
        merge_params["mid"] = machine_id
        # Same set-if-absent rule in the RECORD (#9206 review P1): this MERGE is
        # declared journal-first, so every property it writes must be described
        # by the record that precedes it or a journal-only rebuild restores the
        # node WITHOUT it — measured: a hosted capture came back with neither
        # `machine_id` nor `model`, i.e. the API served null attribution for the
        # machine that captured the session. The fold's matching arm is
        # `_fold_session_recorded`'s (also a coalesce, so live first-writer-wins
        # and replay agree).
        session_record["machine_id"] = machine_id
    if model:
        merge_sets.append("s.model=coalesce(s.model, $model)")
        merge_params["model"] = model
        session_record["model"] = model
    # #9206 §17.1: THE RECORD IS APPENDED HERE, BEFORE THE MERGE, by the writer
    # itself. Two defects are closed in one line and they share a cause (a
    # record whose emission belonged to the CALLER):
    #
    #   * the HOSTED capture lane passed no `on_session_merged` at all, so its
    #     `MERGE (s:Session …)` was UNJOURNALLED — a raw graph write with no
    #     record, which a journal-only `rebuild()`/`recover_from_log` simply
    #     drops (#9206 measured it through §17.1's third door);
    #   * and with no record of its own this writer could not DECLARE a lane
    #     honestly: the census (`tests/test_9124_declared_live_doors.py`) refuses
    #     an apply-first declaration on a door that appends nothing — the #9068
    #     defect — so "every caller must remember to journal" was a precondition
    #     the type system could not keep.
    #
    # The ORDER is #3664's, moved one step earlier: the record must precede the
    # TURN WRITE (whose `PointAdded` records carry `contains_session`, so a
    # replay needs the `:Session` node to exist when it folds them), and
    # appending it before the MERGE satisfies that while ALSO making the MERGE
    # journal-first — the direction §17.1 requires (a record the cache lacks is
    # projection lag; a cache node no record describes is the second writer).
    # The fold is an idempotent `MERGE` by `id` with coalesce-preserved fields,
    # so a replayed record whose live MERGE later failed converges rather than
    # duplicating. ⚠️ The append's outcome is not checked (same residual as
    # `supersede_point`, #9124 part 2): `_emit_event` is best-effort, so a failed
    # append leaves the cache ahead of the journal — counted in
    # `_journal_write_failures`, not reported per call.
    #
    # ⛔ THE OTHER DIRECTION IS A STATED, ACCEPTED WINDOW, NOT "LAG" (#9206
    # review P2, tracked as #9390). The record is appended BEFORE the MERGE, so a
    # MERGE that ABORTS after its bounded retries (the `#7405` retry re-raises)
    # leaves an ORPHAN record: a `SessionRecorded` for a `:Session` that was
    # never written. It converges STRUCTURALLY (the fold MERGEs by `id`, so no
    # duplicate node) but NOT in its VALUES, because every coalesce-preserved
    # field here is FIRST-WRITER-WINS ON BOTH SIDES — this statement's
    # `s.<f>=coalesce(s.<f>, $v)` and `_fold_session_recorded`'s matching arm —
    # so on a journal-only rebuild the orphan's `capture_lane` / `machine_id` /
    # `model` / `source` / `client_captured_at` / `actor_user_id` WIN over the
    # values of the attempt that actually landed: lag converges on replay, this
    # does not, replay keeps the value the live graph REJECTED. Concretely
    # (#3516 piece 7): a failed hook capture followed by a landed `store_sync`
    # backstop restores as `capture_lane="hook"` — which is what the `#3516 §B`
    # hook-liveness check reads. Bounded to an ABORTED MERGE (an append that
    # fails instead only loses durability, never attribution), and #9390 owns the
    # decision: either the fold learns to tell a LANDED record from an orphan
    # (an attempt/instant it can order on) or the two become one commit point.
    # Pinned, so this cannot change silently, by
    # `tests/test_9206_apply_first_lane.py::
    # test_an_orphan_record_wins_the_replay_fold_attribution_measured`.
    sdk._emit_event("SessionRecorded", **session_record)
    # #7405 (audit G8): the `:Session` MERGE rides the bounded write retry.
    # Before this a rebuild's per-query abort raised out of this writer BEFORE
    # the turn store below, so the capture lost the whole session — row and
    # turns — and the finding it carried never reached the map.
    #
    # Re-issuing is the FIRST application, not a second one: the retry
    # predicate admits only refusals the engine ABORTED, so the statement did
    # not land — and the statement is idempotent besides (it MERGEs on `id`,
    # so it can never mint a second Session; the field list mixes
    # `coalesce(s.field, $param)` clauses with absolute SETs, and the absolute
    # ones are safe precisely because the aborted attempt left nothing to
    # overwrite).
    #
    # The probe reads `sdk._proj` (this SDK's live authoritative handle); the
    # `proj` argument is the caller's projection and every caller in-tree passes
    # `sdk._get_proj()`, so the two are the same object and the graph-existence
    # discriminator probes the graph this write targets.
    # #10000: the MERGE below moves `s.turn_max_index`, so the PREVIOUS
    # high-water mark must be read first — it is the only source of the #1920
    # stale-turn range now that the turns are record rows rather than graph rows
    # to walk. Read only on a re-capture: a fresh capture has no prior Session and
    # therefore no stale turn, so the fresh-capture hot path keeps its exact query
    # count (#3086).
    prior_turn_max_index = -1
    if session_existed:
        try:
            _prior = proj.g.query(
                "MATCH (s:Session {id:$sid}) RETURN "
                "coalesce(s.turn_max_index, s.turn_count, 0)",
                params={"sid": session_id}).result_set
            prior_turn_max_index = int(_prior[0][0]) if _prior else -1
        except Exception:
            # Fail toward deleting NOTHING rather than toward a bad range: a
            # failed read leaves the mark at -1, which sweeps no id — the same
            # conservative direction the turn-id parse guard takes ("never
            # delete where we cannot place it").
            prior_turn_max_index = -1
    sdk._graph_write_with_retry(
        lambda: proj.g.query(
            f"MERGE (s:Session {{id:$sid}}) SET {', '.join(merge_sets)}",
            params=merge_params,
        ),
        what="capture :Session MERGE",
    )
    if on_session_merged is not None:
        # An OBSERVER hook, not the journal carrier (#9206: this writer appends
        # its own `SessionRecorded` above). Both capture lanes pass it or not for
        # their own reasons; nothing depends on it for durability.
        on_session_merged(session_record)

    # The stored text is the WRITER'S OWN definition (#4911's scrubber runs
    # there), and the vector is derived from it, so the two cannot describe
    # different strings (#4194). ``texts_and_counts`` is an OPTIONAL donation for
    # a caller that has already computed the pair. NO caller passes it today —
    # the sync SDK lane and the hosted lane both take the recompute branch below
    # — so that branch is the live path for every caller.
    if texts_and_counts is None:
        turn_texts, redaction_counts = _capture_turn_texts_with_redactions(turns)
    else:
        turn_texts, redaction_counts = texts_and_counts
    if embed_fn is not None:
        turn_embs = embed_fn(turn_texts)
    elif turn_embs is None:
        turn_embs = [None] * len(turn_texts)
    redacted_total = _write_capture_turns(
        proj, sdk, session_id, turns, now=now, turn_embs=turn_embs,
        session_existed=session_existed, turn_offset=turn_offset,
        prior_turn_max_index=prior_turn_max_index,
        texts_and_counts=(turn_texts, redaction_counts))
    return {
        "turn_count": turn_count,
        "turn_ids": [_capture_turn_id(session_id, i, turn_offset)
                     for i in range(turn_count)],
        "turn_texts": turn_texts,
        "redaction_counts": redaction_counts,
        "redacted_total": redacted_total,
        "session_record": session_record,
        "merge_params": merge_params,
    }


# #1352: minimal stopword set for the cheap session-Source topic derivation —
# content-word frequency over the transcript (the metadata extractor's LLM
# path is not available on the capture path; this is the deterministic
# fallback the session Source carries). Deliberately small — domain terms
# (auth, http, deploy) must survive the filter.
_SESSION_SOURCE_STOPWORDS = frozenset({
    "a", "an", "the", "and", "or", "but", "so", "for", "with", "without",
    "from", "into", "onto", "over", "under", "about", "against", "between",
    "through", "during", "before", "after", "above", "below", "to", "of",
    "in", "on", "at", "by", "is", "are", "was", "were", "be", "been",
    "being", "have", "has", "had", "do", "does", "did", "will", "would",
    "can", "could", "should", "may", "might", "must", "shall", "not",
    "no", "yes", "ok", "it", "its", "this", "that", "these", "those",
    "i", "we", "you", "he", "she", "they", "them", "me", "us", "my",
    "our", "your", "their", "his", "her", "as", "if", "then", "than",
    "so", "too", "very", "just", "really", "there", "here", "when",  # noqa: B033
    "what", "which", "who", "whom", "why", "how", "all", "any", "both",
    "each", "few", "more", "most", "other", "some", "such", "only",
    "own", "same", "also", "because", "into", "out", "up", "down",  # noqa: B033
    "again", "once", "ago", "now", "new", "old", "first", "last",
    "user", "assistant", "speaker", "think", "say", "says", "said",
    "sure", "yeah", "agree", "agreed", "want", "need", "go", "going",
    "get", "got", "see", "know", "like", "make", "take", "come", "well",
    "right", "okay",
})


def _session_source_metadata(transcript: str) -> tuple[str, list[str]]:
    """Derive cheap capture metadata (summary + topics) for the session Source
    from the extraction transcript (#1352) — no LLM call, deterministic:
    the summary is the first substantive utterance, topics are the top
    frequent content words. Returns ("", []) for an empty transcript."""
    if not transcript.strip():
        return "", []
    summary = ""
    for line in transcript.splitlines():
        text = line.split(": ", 1)[1] if ": " in line else line
        text = text.strip()
        if len(text) >= 10:
            summary = text[:200]
            break
    if not summary:
        summary = transcript.strip()[:200]
    from collections import Counter
    words = re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}", transcript.lower())
    counts = Counter(w for w in words if w not in _SESSION_SOURCE_STOPWORDS)
    topics = [w for w, _ in counts.most_common(6)]
    return summary, topics


#: #3518: the ONE searchable-text field on a :Source — the field the Source
#: FTS index is declared on (``projection/__init__.py``) and the one
#: ``search_engine.run_fts_query(entity_type='source')`` resolves against.
SOURCE_SEARCH_TEXT_FIELD = "_searchText"


def _source_search_text(*, title: str | None = None,
                        summary: str | None = None) -> str | None:
    """#3518: the ONE rule for a Source's searchable text — the title when
    present, else the summary (``None`` when neither carries text).

    Both Source writers resolve their ``_searchText`` through here so the two
    cannot drift into different vocabularies: the indexer
    (``_index_source_merge`` → ``create_source(..., _searchText=...)``) passes
    its frontmatter ``title``; the capture path
    (``_materialize_session_source``) has no title and passes its derived
    ``summary``. Before this, the indexer set the field but the capture path
    never did, and no ``Source`` FTS index existed at all — a captured session
    was unfindable through ``tortoise_fts_query(entity_type='source')``.
    """
    text = title or summary
    return text or None


def _session_extraction_estimate(conversation: list[dict], *,
                                 extractor: str | None = None) -> int:
    """Pre-write fail-closed estimate of NEW non-episodic nodes a capture
    will produce. v2 (default, mirrors commit_session's TORTOISE_EXTRACTOR
    selection): 3 x sentence-cap — points + operators (<= points via Layer-1
    drop) + a x1 allowance for entities/events, each ~<= points in the S2/S4
    embed list (#1532 D4). m2: 2 x sentence-cap (points + operators, #822).
    Turn Points/Session/Event are episodic and never counted (#947)."""
    import os
    mode = (extractor or os.environ.get("TORTOISE_EXTRACTOR", "v2")).lower()
    factor = 3 if mode == "v2" else 2
    # #4897: window first. ``_session_llm_transcript`` no longer clips (the clip
    # and its true total are decided once, on pre-redaction text), so the
    # estimate must be taken over the same window the extractor will see —
    # otherwise a raw caller would count sentences the extractor never receives.
    # A no-op on the already-windowed input production passes.
    _transcript, est = _session_llm_transcript(  # est = sents*2
        _capture_turn_window(conversation))
    return est // 2 * factor


def _session_llm_extraction_estimate(conversation: list[dict]) -> int:
    """Deprecated-compat alias for _session_extraction_estimate (#1532 D4):
    the hosted 402 gate now calls the v2-aware estimator; this name stays for
    pre-migration callers (removed after the migration completes)."""
    return _session_extraction_estimate(conversation)


def _first_non_draft_status(points: list) -> tuple[int, object] | None:
    """Row-9 guard helper (PR #1073): return (index, effective_status) of the
    first point item whose EFFECTIVE status is not exactly 'draft'.

    The effective status is read through
    :func:`_item_declared_status` at :func:`_item_flatten_depth` — the same
    reader and the same per-door depth ``TortoiseSDK._check_gated_status``
    uses, so the two doors cannot disagree about a spelling (a level-one
    reader here let ``props:{"props":{"status":"live"}}`` reach the SDK
    refusal instead of the row-9 message — review round 4). Only the exact
    canonical string "draft" is storable under
    promotion_policy='gated': case/whitespace variants ("Draft", "draft "),
    terminal statuses, and non-str values are all rejected (EP _live_only
    excludes 'draft' AND terminal statuses/flag — #2422; junk outside the
    closed vocabulary is the residual EP-live surface).

    Used by the MCP door (`tortoise_ingest`, PR #1073); the SDK door reads the
    SAME reader at the SAME depth in ``_check_gated_status``, so the two layers
    cannot drift by spelling (they differ only in the error SHAPE: ERR_INVALID
    on the MCP pre-gate, an aggregated `BundleValidationError` from Phase 1).
    Returns None when every item is draft/absent.
    """
    for i, item in enumerate(points or []):
        if not isinstance(item, dict):
            continue
        has_status, st = _item_declared_status(
            item, _item_flatten_depth(item, "points"))
        # An explicit status key present with value None is a violation too
        # (None would otherwise be stored as NULL, which _live_only treats as
        # LIVE) — uniform row-9 message, not the vocabulary error.
        if has_status and st is None:
            return i, None
        if st is not None and str(st) != "draft":
            return i, st
    return None

# #913: whole-graph mode=add cap — pairwise scoring is O(n²) in time AND
# memory (dense cosine matrix + pair dict); a read-only MCP call must not
# OOM a hosted server (#329/#579 bounding precedent). The unscoped candidate
# pool is truncated to the cap most-recently-updated Points (deterministic:
# updatedAt desc, id tie-break); callers with larger graphs should pass a
# scope (bounded at 200 by hybrid retrieval).
REVIEW_ADD_POOL_CAP = 1000

# ── Cross-lens candidate discovery (#438, BYOA) cost bounds ─────────────
# D4: hard cap on candidates per discovery cycle (predictable per-cycle
# cost; local-model pre-filter experiments live in epic #909, NOT here).
CROSS_LENS_CANDIDATE_CAP = 200
# Pool-scan bound: the per-cycle scan is capped at the most-recently-updated
# eligible points so cost stays proportional to new data, not O(n²) over the
# whole graph (indicator I3, #438).
CROSS_LENS_POOL_CAP = 1000
# Per-point ANN recall bound (neighbors pulled per pool point over the
# vector index — HNSW on Docker, brute-force euclidean on embedded).
CROSS_LENS_ANN_TOP_K = 20
# D4-style hard cap on per-point ANN recall: an agent-passed top_k above
# this is clamped, never honored, so the per-cycle recall budget stays
# predictable (embedded brute-force recall is O(pool x total_points) —
# see the _cross_lens_pairs docstring — so an unbounded top_k would
# undermine the cost contract entirely).
CROSS_LENS_ANN_TOP_K_MAX = 100

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
    # #2498: `outdated` is TERMINAL for the shared lifecycle guard
    # (`live.TERMINAL_EXCLUDED_STATUSES` + the legacy `outdated=true` flag),
    # so it has no outgoing transition. The pre-#2498 `{"retracted"}` entry
    # diverged from the guard — a dead claim could be re-terminalized.
    "outdated": frozenset(),
    "archived": frozenset(),                             # terminal (reserved — no v1 write path)
}

# Event types that write to the :GraphEvent store (#432). Other types
# (e.g. PointRevised) only write to the JSONL event log (#548).
#
# ⚠️ THIS SET IS THE STORE'S **CLAIM-TRANSITION** CONTRACT, AND ITS SHAPE IS A
# RECORDED DECISION — do not widen it by reflex. `docs/plans/2026-08-08-432-
# subscriptions-plan.md` §Task 3: "Content edits (`update_point` non-status
# props) emit NOTHING (plan-review P1) — the emit set is exactly these five
# hooks; every claim transition maps to exactly one", pinned by
# `tests/test_event_store.py::test_content_edit_emits_nothing` and restated in
# `docs/event-catalog.md`. #2353 asks for `PointRevised` in this store, so the
# addition is drawn as an **opt-in** one at the emit hook below (`_emit_event`)
# rather than by adding the type here: the default emit SET stays unchanged (no
# new types), and the semantic change feed (`tortoise/change_feed.py`) is what
# consumes the revisions once an operator switches it on. The default stream is
# NOT byte-identical — the hard-delete `PointRetracted` payload carries the
# unconditional `deleted` marker (`_deleted_point_payload`).
_GRAPH_EVENT_TYPES = frozenset({
    "PointAdded",
    "OperatorAdded",
    "PointRetracted",
    "PointSuperseded",
    "OperatorAnnotated",
    "PointPromoted",   # #785: reviewer-gated draft→live promotion
    "OperatorPromoted",  # #785: R16 zombie-operator prevention
    "DedupeRecorded",  # #784: content-dedup candidate recorded/merged
    "DedupeRejected",  # #784: content-dedup candidate rejected
    "ObjectSuperseded",  # #1350: Object status fold source (supersession)
    "PointInvalidated",  # #2488: invalidate_point — outdated flag + CORRECTS (no status)
})


def _change_feed_enabled() -> bool:
    """Is the #2353 semantic change feed (and its PointRevised store half) on?

    One switch for both halves: the revision record is the feed's SOURCE, so a
    feed without revisions would silently under-report. Default OFF — `enabled()`
    resolves unset/blank/garbage to False (`tortoise/env_truthy.py`), which is
    what keeps the store's documented transition-only contract intact unless an
    operator opts in.
    """
    from .change_feed import enabled

    return enabled()


def _deleted_point_payload(id: str) -> dict:
    """The `PointRetracted` store payload for a HARD delete (#2353, #3300).

    `PointRetracted` has two producers with different end-states: this hard
    delete (the node is gone) and `retract_point`'s tombstone
    (`status='retracted'`, node kept). The payloads were byte-identical, so the
    semantic change feed could not tell them apart and reported the tombstone
    reading — a fabricated `after` whose `precondition` contradicted its own
    `before`.

    The `deleted` marker is written UNCONDITIONALLY. It states what THIS call
    did, so it cannot depend on whether the READING process has the change feed
    switched on: a deployment whose writer has it off and whose reader has it on
    would otherwise fold every hard delete it sees as a tombstone. Rows written
    before the marker existed keep their old `{"id": ...}` shape and are read
    as tombstones — a residual the change feed documents rather than hides.
    """
    return {"id": id, "deleted": True}


# ═══════════════════════════════════════════════════════════════════════════
# #7951 — THE JOURNAL ORDERING CONTRACT. Read this before adding a writer.
# ═══════════════════════════════════════════════════════════════════════════
#
# THE RULE:  **journal first, then project.**
#
#   A live write appends its record to the TRUTH journal and only THEN folds it
#   into the DERIVED graph. **The append is the COMMIT POINT:** if it fails,
#   the write is not committed and the fold must NOT run. `docs/architecture/
#   STORAGE-ARCHITECTURE.md` §3 states the protocol — *"Writes go to the truth
#   first, then project"* — and §17.1 mechanism (1) names its endpoint: journal
#   + projection in ONE atomic write (the #7951 chokepoint). Until that
#   transaction exists, the ORDER is the contract, and it is fixed here once
#   for the whole codebase so that no remaining site has to choose for itself.
#
# WHY — the failure-mode argument, not a preference. The design invariant is
# `derived = replay(journal)` (§3). A crash leaves exactly one of two windows:
#
#   (a) APPLY-FIRST — the process dies between the graph write and the append.
#       The DERIVED store now holds a node the journal — the TRUTH — never
#       recorded. `rebuild_all` replays the journal, does NOT reproduce the
#       node, and the write is GONE. No replay can recover it; only an
#       out-of-band repair that reconstructs the record from the graph could,
#       and none exists. The loss is SILENT (rebuild reports success), and it
#       compounds: a later mutation of the orphan journals a record whose
#       target the journal never created, i.e. a journal that is not itself
#       replayable (the "input source … does not resolve" class).
#
#   (b) JOURNAL-FIRST — the process dies between the append and the fold. The
#       journal holds the record; the derived store merely LAGS. The next
#       `rebuild_all` applies the record and the graph catches up. **Replay
#       heals it** — and healing a lagging derived store is the entire reason
#       the recovery path exists. Nothing is lost, and `derived =
#       replay(journal)` is restored by the recovery path itself.
#
#   ⇒ Choose the order whose residual window is replay-healable. Apply-first
#     leaves the DERIVED store AHEAD of the TRUTH — exactly "a derived cache
#     that is also written to directly is not derived" — and no recovery path
#     can undo it.
#
# THE CONSTRAINT THAT PUTS THE DECISION IN THE CALLER — `FalkorProjection.apply`
# (`tortoise/projection/__init__.py`) is ALSO the REPLAY TARGET: `rebuild_all`,
# `recover_from_log`, `rebuild` and `backup` drive it once per journal record.
# It can therefore NEVER journal itself — a replayed record would journal
# itself, grow the log on every rebuild, and make replay non-idempotent at the
# log level. The fold cannot know whether it is live or a replay; only the
# CALLER does. So the order is drawn at the caller-side live doors. Those that
# state it explicitly are (the authoritative inventory is the §17.1 guard's
# opener list in `tortoise/projection/__init__.py`):
#   * `EventAPI._emit`                      (tortoise/api.py)
#   * `TortoiseSDK._apply_and_journal` / `_journal_entity_event` (this module)
#   * `TortoiseSDK._doc_write`, `_create_entity`, `create_point` (this module)
# The live doors that do NOT yet conform are listed under KNOWN DEVIATIONS at
# the end of this block.
#
# THE ONLY TWO PERMITTED EXCEPTIONS — neither is "apply-first by preference":
#
#   1. REPLAY (apply-only — it must not journal at all). A fold driven by
#      READING the journal — `rebuild_all`, `recover_from_log`, `rebuild`,
#      `tortoise reconcile` (`__main__.py`), `backup`, `consistency` — applies
#      and does NOT journal. This is not the other order; it is the ABSENCE of
#      a write. Routing one of these through a live door is a BUG: it would
#      append to the journal the very record it just read from it.
#
#   2. POST-APPLY-SNAPSHOT PAYLOAD (the one real apply-first). When the record
#      cannot be constructed until the fold has produced it, there is no
#      pre-fold record to append. Such a site applies first and inherits
#      window (a); the window closes only at §17.1's one atomic write. A site
#      that CAN form its record before the fold MUST NOT take this exception.
#      #7951: `create_point` NO LONGER takes it — its `PointAdded` payload is
#      now built from the write intent (`_create_map`, the properties the
#      CREATE writes) and appended BEFORE the CREATE. No production site
#      currently relies on this exception; it is retained for the shape it
#      names, not as a licence.
#
# KNOWN DEVIATIONS in this tree (recorded, not concealed):
# `TortoiseSDK._create_entity`'s Object/Subject/Event registration blocks are
# apply-first although their payload (`event`) is fully built BEFORE the fold,
# so they qualify for neither exception; they also fold regardless of a FAILED
# append (`_emit_event` is best-effort). They are left as-is in this increment
# because reordering them rides the #2194/#2295/#2309 first-registration
# hardening and its pinned tests. (`create_point` was the other named
# deviation; #7951 moved it to the outbox ordering — append first, cache
# second — so it no longer appears here.) The `DocumentCreated` arm (#2296) DOES append
# first (its record is a plain id-keyed entity create, and its append is
# replay-healable)
# but shares the fail-soft half: one funnel applies once for every arm, and
# its Object/Subject/Event blocks pin `test_object_registered_journal.py::
# test_log_append_failure_warns_and_keeps_live` (an append failure must not
# crash the create), so a fail-closed arm inside the same function would be a
# second, contradictory convention. The chokepoint's atomic write removes both
# windows by construction.
#
# ⚠️ THE INVENTORY BELOW IS INCOMPLETE, and this paragraph used to overstate it
# by claiming every other door already conforms. It did not, and the claim was
# wrong by inspection (#7951 increment 4a). MEASURED at this tree, still
# apply-first — cache write BEFORE the record — and therefore still carrying
# contract window (a):
#   * `TortoiseSDK._delete_entity` — `DETACH DELETE` per matched label, then one
#     `EntityMutated` op=delete; a failed append RESURRECTS the deleted node on
#     replay. (Its comment states the opposite preference — post-hoc "so a
#     failed/no-op delete never leaves a phantom record" — which is the
#     phantom-record trade, NOT an owner ruling; the resurrection direction is
#     the one §17.1 forbids. Closing it needs a pre-read per label.)
#   * `TortoiseSDK._session_event_write` — the `:Event` MERGE precedes
#     `EventRecorded`.
#   * `TortoiseSDK._update_entity`, `TortoiseSDK.create_operator` — the
#     node `SET`/`CREATE` and its `EntityMutated` / `OperatorAdded` record come
#     after the write.
# Those are four doors verified line by line at this tree. ⚠️ They are NOT
# claimed to be the whole set: a full inventory of the apply-first doors is
# owed (a crude order scan flags a dozen more, several of which are reads
# rather than writes, so a count from it would be a guess). Plan §7 increment 4
# tracks the conversion; naming the four is not fixing them.
#
# ⚠️ #9206 changed what "still apply-first" COSTS, not the order: each of these
# doors now DECLARES the apply-first lane (`_declares_apply_first_write_lane`)
# instead of leaving its writes undeclared, so an armed handle no longer refuses
# the product's own write path while the per-door conversion (#9124 part 2)
# remains open. The declaration states the order; it does not close the window
# described above, and the lane's census pin refuses a door that appends no
# record.
#
# `delete_point` is apply-first BY SHAPE since #9207 (the `DETACH DELETE` is
# issued first and the `EntityMutated` op=delete record follows it, once it has
# returned) — but it is NOT one of the doors this inventory is about, and the
# difference is the point of the list. Those doors write the cache ahead of the
# journal and leave the divergence SILENT. This door writes the cache first
# precisely so a cache write that cannot be issued (a refusal, an engine error)
# appends NOTHING — the record can never claim a destruction the cache still
# serves, which is the divergence #7951's append-first ordering produced on a
# delete whose cache write did not land (#9207 measured the shape; the trigger is
# RARE TODAY because §17.1's third door — this same raw handle — is DETECTED but
# not yet REFUSING: `_HANDLE_GUARD_ENFORCES` is False in #9124 part 1). The
# window it has instead — a
# destroy that LANDED whose append then failed — is reported WHENEVER CODE RUNS
# (`WriteNotLandedError`, `record_committed=False`), and it resolves only through
# a destructive `rebuild_all` (which re-creates the Point), after which the
# delete is re-issued. ⚠️ The one window this order CANNOT report is a PROCESS
# DEATH between the destroy returning and the append (`kill -9`, OOM, power
# loss): no code runs, nothing raises, and the journal keeps only the
# `PointAdded` line, so the next `rebuild_all` RE-CREATES the Point. That is the
# trade #9207 makes — it gives up an unreportable-by-construction crash window to
# close a window that was reportable only AFTER the damage (a record for a
# destroy the cache never performed). Removing it entirely is §17.1's atomic
# write (increment 2b). #7951 increment 4a had this door the other way round.
#
# ⚠️ §17.1 DECLARATION STATE, since it is easy to read the above as "no lane is
# owed here": this door USED to carry `@_declares_live_write_lane(
# "delete_point")` (#9124 part 1). That declaration was journal-first-only —
# `_declares_live_write_lane`'s docstring says so — and #9207's reversal makes it
# false, so it was REMOVED rather than qualified. #9206 then DECLARED the order
# the door actually uses: `@_declares_apply_first_write_lane("delete_point")`,
# the lane that states "the cache write is the commit point, the record follows,
# by decision". A declaration is not an amnesty — the lane's census pin fails
# for a door that appends no record (the #9068 defect) and for a journal-first
# door that took the weaker lane — and the runtime behaviour is pinned by
# `tests/test_9079_handle_write_authority.py`
# (`test_a_declared_apply_first_door_writes_under_the_armed_guard`).
# ═══════════════════════════════════════════════════════════════════════════

# #7951: the envelope keys ``_emit_event`` OWNS. A record that is journalled
# from an already-built event dict must not carry its own copy of these — a
# caller prop of the same name would shadow the envelope on replay (see
# ``_emit_event``'s ``event.update(extra)``). Kept as one tuple so the live
# entity doors cannot drift from each other. `recorded_ts` (#5048) is the
# NAMED-PARAMETER twin of `ts` and is owned the same way: without it here, a
# caller prop of that name binds the timestamp parameter and forges the
# record's envelope `ts` (#2296 review). The caller-prop live/replay residue
# for these names generally is #8553.
_JOURNAL_ENVELOPE_KEYS = (
    "type", "id", "point", "payload",
    "event_id", "ts", "recorded_ts", "initiated_by", "projection_version",
)

#: Every `_emit_event` ENVELOPE name a caller prop must not be able to set —
# that is `_JOURNAL_ENVELOPE_KEYS` minus ``id``, which is deliberately absent:
# the entity create/update doors REJECT a caller ``id`` fail-closed
# (`_sanitize_props(reject_id=True)` raises), and ``create_point`` instead
# consumes it as its documented explicit-id path — a sanctioned contract either
# way, which a silent strip would weaken. Refused at prop INTAKE, not merely
# stripped from the record, because the collision is invisible from inside
# `_emit_event` and each name has a distinct failure:
#
# * ``recorded_ts`` / ``point`` / ``payload`` are `_emit_event` PARAMETERS, and
#   Python binds a ``**props`` key to a named parameter AT THE CALL — so any
#   door that splats caller props into the call turns the prop into that
#   parameter. ``recorded_ts`` forges the record's envelope ``ts``; ``point``
#   hands the record a CALLER-AUTHORED node snapshot, which the rebuild then
#   replays (measured on ``update_point``); ``payload`` is swallowed by the
#   parameter while the door's own node write still persists it LIVE, so the
#   value ends up live-only (the #8553 divergence) — and on a graph-event type
#   it would become the ``:GraphEvent`` payload rather than a node property (no
#   such type splats caller props today).
# * ``type`` is the key BOTH the live fold and the journal record dispatch on,
#   so a prop of that name re-types or silently SUPPRESSES the record
#   (measured: ``create_document(type="SourceCreated")`` journalled NOTHING,
#   and ``rebuild_all`` destroyed the node — the #2296 defect itself, reachable
#   through a caller prop).
# * ``ts`` / ``event_id`` / ``projection_version`` / ``initiated_by`` land in
#   ``**extra`` and are applied by ``event.update(extra)`` AFTER the envelope is
#   built, overwriting it (measured on ``update_point``: a caller ``ts``,
#   ``event_id`` and ``projection_version`` all reached the record).
#
# The set is spelled out rather than derived so an auditor sees it whole; it
# must stay equal to `_JOURNAL_ENVELOPE_KEYS` minus ``id``.
_RESERVED_ENVELOPE_PROPS = (
    "type", "point", "payload", "event_id", "ts", "recorded_ts",
    "initiated_by", "projection_version",
)

#: `_emit_event`-owned names that are NOT journal-envelope keys, yet still
#: collide with a caller prop AT THE CALL — the same fail-open the set above
#: closes, but invisible to `_journal_record_payload` (neither name is in
#: `_JOURNAL_ENVELOPE_KEYS`, so nothing else strips them):
#:
#: * ``type_`` is `_emit_event`'s FIRST PARAMETER. Every door that splats
#:   caller props into the call passes the record type positionally, so a prop
#:   of this name raises ``TypeError: got multiple values for argument
#:   'type_'`` — AFTER the live write has landed, leaving an unjournalled
#:   live-only mutation. Measured: ``update_point(pid, type_="X")`` and
#:   ``create_subject(name, type_="Y")`` both wrote the node and journaled
#:   nothing; on ``create_source`` the collision is swallowed by that door's
#:   journal ``try/except``, so the ``:Source`` was live-only and
#:   ``rebuild_all`` destroyed it — the #2296 defect itself.
#: * ``new_content`` is passed EXPLICITLY as a keyword by ``update_point``'s
#:   ``PointRevised`` emit; a caller prop of that name in the same splat raises
#:   the identical ``TypeError`` after the identical write (measured: the live
#:   node kept the prop and NO ``PointRevised`` record was written). On the
#:   ``create_source`` lane there is no call collision, but the name rode into
#:   the ``SourceCreated`` record as a caller-forged payload key (measured).
#:
#: * ``self`` is the FIRST parameter of the bound method, and it is reachable
#:   through the documented nested-``props`` convention (``_coerce_props``:
#:   direct SDK callers mirror the MCP tool signature and pass ``props={...}``):
#:   the nested dict is flattened AFTER the public method's own ``self`` is
#:   bound, so the outer-call collision does not protect it. Measured:
#:   ``create_source(url, "document", props={"self": "FORGED"})`` raised inside
#:   `_emit_event`, that door's journal ``try/except`` swallowed it, and
#:   ``rebuild_all`` destroyed the live-only ``:Source`` — the #2296 defect
#:   itself; ``update_point(pid, props={"self": …})`` and
#:   ``create_entity(..., props={"self": …})`` likewise wrote the node with no
#:   record. THIS IS WHY THE SET IS NOT HAND-TRUSTED: the test derives the
#:   required names from ``inspect.signature(TortoiseSDK._emit_event)``, so a
#:   parameter added to `_emit_event` fails the suite instead of silently
#:   re-opening this hole.
#:
#: * ``aboutLinks`` (#8398 review P1-1) is the case that derivation CANNOT see:
#:   it is not an `_emit_event` parameter, so it is unreachable via
#:   ``inspect.signature`` — it collides because ``_create_entity``'s
#:   ``EventRecorded`` emit splats TWO ``**`` bags into ONE call (the journaled
#:   payload and the write-time ``_journal_extra`` edge facts), and a caller
#:   prop of that name lands in the first while the SDK supplies the second.
#:   Measured on a38d6e775: ``create_event(..., aboutObject=oid,
#:   aboutLinks=["caller"])`` raised ``TypeError: got multiple values for
#:   keyword argument 'aboutLinks'`` AFTER the live write and BEFORE the
#:   record, so the Event was live-only and ``rebuild_all`` deleted it. The
#:   splat is merged into one dict now (so the collision cannot recur), and the
#:   name is reserved here so the caller prop never reaches the live fold
#:   either — where `apply`'s inline re-wire read it as journal data and
#:   reported a phantom link failure (measured).
#:
#: Strip it at prop INTAKE like the envelope set above, and ALSO before any
#: RELAY that forwards a caller bag into another method — the collision is at
#: the call, so `update_entity` (relays to `_update_entity`) and
#: `create_subject`/`create_object`/`create_event` (relay to `create_entity`)
#: drop the reserved names before splatting, and `create_source` — the one door
#: that bypasses intake — drops them itself. With every relay covered, the
#: collision is impossible and live == replay.
_RESERVED_EMIT_CALL_PROPS = ("type_", "new_content", "self", "aboutLinks")


def _drop_reserved_emit_props(props: dict) -> list[str]:
    """Drop every `_emit_event`-owned name from ``props``, IN PLACE.

    Returns the dropped names, sorted. Membership is decided here and only here,
    so the strip cannot drift from the sets (it did, when it was hand-written at
    each site: `self` stayed open through a RELAY splat).

    Call it wherever a caller bag is about to be SPLATTED into a method, not
    only at intake: the collision is AT THE CALL, so a relay that forwards
    ``**props`` must strip first. `_update_entity` and `create_entity` take
    ``self`` as their first parameter, so a ``self`` key raised ``TypeError`` at
    the relay — one frame BEFORE `_sanitize_props` could drop it (measured).
    """
    _dropped = sorted(
        _k for _k in props
        if _k in _RESERVED_ENVELOPE_PROPS or _k in _RESERVED_EMIT_CALL_PROPS)
    for _k in _dropped:
        props.pop(_k)
    return _dropped


def _entity_door_props(kind_key: str, name: str, props: dict) -> dict:
    """The entity door's property bag, with an UNSENT discriminator left out.

    #9572: ``subjectKind`` / ``objectKind`` is the user-authored discriminator
    and the fold writes it ON MATCH as ``coalesce($k, <stored>)`` — which is
    correct ONLY if "the caller named no kind" reaches the fold as **absent**.
    The door used to fabricate ``"other"`` (``props.pop(kind_key, "other")`` /
    an ``= "other"`` alias default), so "not supplied" and "supplied as the
    default" were the same string: a bare re-create silently reset a stored
    ``team`` / ``capability`` to ``other``, and the journal record carried the
    fabricated value, so replay reproduced the clobber.

    The ``'other'`` default belongs to the fold's ON CREATE arm (the Object arm
    already worked this way — ``entities.py::_upsert_object`` reads
    ``ev.get("object_kind")`` with a NULL coalesce). Deciding it HERE would
    re-create the defect: nothing downstream can tell a fabricated default from
    a supplied value.

    ``props`` is returned as a NEW dict in the door's established key order
    (name, kind?, status, *caller props*) so a caller-supplied ``status`` still
    overrides the ``"live"`` seed, exactly as before.
    #9572 review P2: BLANK is not a kind either. ``""`` / ``"   "`` is not
    ``None``, so it survived the ``is not None`` line and reached the fold as a
    real value — ``coalesce("", stored)`` returns ``""``, so a blank re-create
    still destroyed a stored ``team`` and a blank FIRST create stored a
    discriminator no pack declares. The blank rule is the leaf module's
    ``named_kind``, shared with the aliases, ``EventAPI``, the two hosted REST
    handlers and the two seed surfaces, so it cannot be drawn differently at
    the next door.
    """
    kind = named_kind(props.pop(kind_key, None))
    out: dict = {"name": name}
    if kind is not None:
        out[kind_key] = kind
    out["status"] = "live"
    out.update(props)
    return out


# ── #9964: the kind decides the layer ──────────────────────────────────────
#
# ONE authority answers "which layer does this kind belong to?"
# (``kind_layers``, derived from the classifier's CATALOG-UNION section partition
# — a superset of any graph-gated one, see ``kind_layers.section_of`` — plus the
# §5 vocabularies). Before this, the SDK doors labelled a node from their own
# ``type`` ARGUMENT and never looked at the kind, while the classifier's
# ``entity`` partition and the two write lanes that route by kind
# (``is_subject_kind``) said something else — MEASURED at ``4c797596d``:
# ``create_object("T3 Acme Holdings", objectKind="core:organization")`` minted a
# ``:Object`` the ``(s:Subject {subjectKind:'core:organization'})`` read cannot
# see (0 rows), permanently (#2489: ``rebuild_all`` does not repair an existing
# graph).
#
# Two outcomes, and NEITHER refuses the write (warn-not-block is #1934's
# recorded decision; a refusal would be a reopen for the owner):
#
#   * SAME-AXIS (object ↔ subject) — the kind ROUTES the label. Both are
#     ``:entity`` nodes in the classifier's partition and the two vocabularies
#     are disjoint, so nothing is lost by writing the layer the kind names
#     instead of the one the caller typed. Advisory: ``kind_layer_routed``.
#   * CROSS-AXIS (a kind declared only on another axis: an Event kind at the
#     Point door, an entity kind at the Event door) — NOT routable (a Point and
#     an Event are different records, and the kinds overlap by ruling: `goal`
#     etc. are Point AND Object, `decision`/`humanApproval` Point AND Event
#     (#7813)). The write lands as asked and the caller is TOLD: advisory
#     ``kind_other_layer`` naming the declaring layer(s) and the door that
#     writes them. That is also the fix for the point door's old behaviour —
#     the only notice was ``_logger.warning`` inside ``_validate_kind``, which
#     the caller never sees.
#
# A kind declared on the door's OWN layer (including every §5 overlap kind) and
# an UNDECLARED kind both stay silent: the vocabulary is deliberately open, and
# the overlap is the model, not residue.

#: The four layer-specific kind properties a door can name, camel form. The
#: snake spellings the fold also reads come from ``pack_state``'s ONE
#: declaration (``_KIND_PROP_SPELLINGS``), never re-spelled here.
_LAYER_KIND_PROPS: tuple[str, ...] = (
    "subjectKind", "objectKind", "eventKind", "pointKind")


def _named_layer_kinds(props: dict) -> list[tuple[str, str, str]]:
    """``[(layer, prop-spelling, kind)]`` for every layer kind the write names.

    Both spellings are read (the fold persists ``object_kind`` and
    ``objectKind`` onto the same property — the #5475 lesson at this exact
    seam), so a kind cannot escape the layer check by arriving snake-cased.
    ``sourceKind``/``documentKind``/``actionKind``/``kind`` are NOT layer
    properties (a document is a ``:Source``; genre and action are other axes)
    and are deliberately out of scope.

    ⛔ A NIL kind (``other``/``core:other``) is NOT a layer kind and is never
    returned (#9964 review P2). ``kind_layers.NIL_KINDS`` says it asserts
    NOTHING about the layer — it is the fold's ON CREATE default on EVERY
    entity label — so letting it into ``named`` made the agreement test below
    read a disagreement between a value that names no layer and the one that
    does, and the write kept a §5 Subject kind on an ``:Object``. Dropping it
    here also leaves a write that names ONLY ``other`` with ``named == []``,
    i.e. silent, which is that constant's published contract.
    """
    from tortoise.kind_layers import NIL_KINDS, PROP_LAYER
    from tortoise.pack_state import _KIND_PROP_SPELLINGS

    named: list[tuple[str, str, str]] = []
    for spelling, canonical in _KIND_PROP_SPELLINGS.items():
        if canonical not in _LAYER_KIND_PROPS or spelling not in props:
            continue
        value = props.get(spelling)
        if not isinstance(value, str) or not value.strip():
            continue
        if value.strip() in NIL_KINDS:
            continue
        named.append((PROP_LAYER[canonical], spelling, value))
    return named


#: entity layer → the node label its door mints. Entity identity is
#: LABEL-SCOPED (``ids.entity_name_id`` hashes ``f"{label}:{name}"``), so the
#: same name under the two entity labels is TWO nodes, never one entity seen
#: twice — which is why a re-route can SPLIT an entity (#9964 review).
_ENTITY_LAYER_LABEL: dict[str, str] = {"subject": "Subject", "object": "Object"}


def _door_relayed_props(etype: str, item: dict) -> dict:
    """The props the ENTITY door RELAYS to ``create_entity``, from a raw item.

    ⛔ The door does not hand ``create_entity`` the item it was given (#9964
    review P2, second round). ``create_subject``/``create_object``

    1. **bind** their OWN camel kind param out of the item, so it is not part
       of the ``**props`` they relay,
    2. ``_coerce_props`` the rest (the alias's fold),
    3. put the param back **through ``named_kind``** — a padded
       ``objectKind=" core:team "`` becomes ``core:team`` before the routing
       authority ever sees it,

    and only then does ``create_entity`` fold a second time. The strip is
    POSITIONAL and NARROW — only the alias's OWN top-level kind param is
    normalised: the sibling camel key (``subjectKind`` at the object door), the
    snake spellings (``object_kind``) and a kind one or two ``props:`` levels
    down all reach ``_kind_layer_note`` RAW, because the alias normalises only
    its own param, after its own fold and before the relay.

    MEASURED (embedded lane, pre-fix) — the two shapes whose predicted label
    disagreed with the label the write LANDED on, each re-reporting ``created``
    on every re-ingest forever because the probe queried the other entity
    label:

        {"type": "object",  "objectKind": " core:team "}   -> :Subject
        {"type": "subject", "subjectKind": " core:skill "} -> :Object

    and, in the other direction, the shapes a blanket "normalise every kind
    prop" repair would BREAK — they do NOT re-file on this head, so a probe
    that stripped them would query a label the write never lands on:

        {"type": "object", "object_kind": " core:team "}           -> :Object
        {"type": "object", "props": {"objectKind": " core:team "}} -> :Object

    That is why this mirrors the door's PIPELINE instead of normalising the
    fold's output: the probe must predict the door, not an idealised door.
    """
    from tortoise.kind_layers import KIND_PROP

    depth = _ENTITY_ITEM_FLATTEN_DEPTH.get(etype, 2)
    # Only the two ENTITY doors this probe predicts have a bind-and-normalise
    # alias; anything else is faithfully just the fold(s).
    kind_key = KIND_PROP.get(etype) if etype in _ENTITY_LAYER_LABEL else None
    if kind_key is None or depth < 1:
        relayed = dict(item)
        for _ in range(depth):
            relayed = _coerce_props(relayed)
        return relayed
    relayed = {k: v for k, v in item.items() if k != kind_key}  # 1. the bind
    relayed = _coerce_props(relayed)                           # 2. alias fold
    normalized = named_kind(item.get(kind_key))
    if normalized is not None:
        relayed[kind_key] = normalized                         # 3. the re-add
    for _ in range(depth - 1):
        relayed = _coerce_props(relayed)                       # create_entity
    return relayed


def _existing_entity_ids(g, etype: str, props: dict, name: Any) -> list:
    """The ids of the entity nodes this write will actually MERGE onto.

    ``etype`` is what the ITEM declared; the entity door re-labels the write by
    the KIND it names (#9964: ``create_object(objectKind='core:team')`` writes a
    ``:Subject``), and identity is label-scoped (``ids.py``:
    ``sha256(f"{label}:{name}")``) — so probing ``etype``'s label can measure a
    DIFFERENT node than the write touches. Measured at the review head:
    re-ingesting ``{"type": "object", "objectKind": "core:team"}`` reported
    ``created`` while the write MERGED, forever, because the ``:Object`` probe
    never matched the ``:Subject`` the door had minted.

    Asks the door's OWN authority (``_kind_layer_note``) instead of re-deriving
    type→label here, on a COPY (that function MUTATES ``props`` on the routed
    case), and fails open to ``etype`` (#1934: a probe must never refuse a
    write).

    Folded to the DOOR's depth first — the SAME rule ``_item_prop_at_depth``
    states for every other ingest reader (#7925 review P2). The entity doors
    flatten the item TWICE (``create_subject``/``create_object`` apply
    ``_coerce_props``, then relay ``**props`` to ``create_entity``, which folds
    again — ``_ENTITY_ITEM_FLATTEN_DEPTH``), so probing the RAW item was blind
    to BOTH supported nested spellings (``props: {kind: …}`` and
    ``props: {props: {kind: …}}``) while the write still re-labelled by the
    kind: ``created`` was reported on every re-ingest of a nested bundle,
    exactly as the FLAT shape was before this probe existed (#9964 review P2).
    A shallower fold leaves the depth-2 spelling wrong; a deeper one would read
    a kind the door never applies.

    ⛔ Folding is not enough, because the door NORMALISES its own kind param
    between its fold and the relay (#9964 review P2, second round):
    ``objectKind=" core:team "`` is ``core:team`` by the time routing sees it,
    so the write re-files to ``:Subject`` while a fold-only probe answered
    ``object`` and queried ``:Object`` — ``created`` on every re-ingest, forever
    (and the mirror on the subject door: ``subjectKind=" core:skill "`` writes
    an ``:Object`` the probe never looked for). ``_door_relayed_props`` replays
    the door's pipeline, at the door's positions, so the prediction cannot drift
    from the write.
    """
    if not isinstance(name, str) or not name:
        return []
    try:
        probe_props = _door_relayed_props(etype, props)
        probe = _kind_layer_note(etype, probe_props)[0]
    except Exception:  # noqa: BLE001, RUF100 — probe only, never refuses a write
        probe = etype
    label = _ENTITY_LAYER_LABEL.get(probe) or _ENTITY_LAYER_LABEL.get(etype)
    return all_name_holder_ids(g, label, name) if label else []


def _kind_layer_note(t: str, props: dict) -> tuple[str, dict | None]:
    """Route/annotate an ENTITY write by the kind it names (#9964).

    Returns ``(entity_type, note)``. ``note`` is ``None`` when the write is
    consistent (or names no kind — the doors stay open, unchanged), else
    ``{"code", "kind", "layer" (requested), "actual" (declaring layers)}``
    for the caller to materialize as an advisory once the node id exists.

    ⚠️ It reads the CATALOG-UNION partition, not this graph's: the graph gate
    is not threaded through (see ``kind_layers.section_of``), so a pack that
    ``packs/`` carries but this graph has not installed can still decide a
    label. Anything that states this answer IS the classifier's own partition
    is stating more than the code does.

    MUTATES ``props`` on the ONE routable case: every layer spelling the write
    named is removed and the kind is re-declared under the routed layer's
    canonical prop, so the fold writes the routed property and no stale
    ``object_kind`` rides along. Nothing else in the write is touched — the
    caller's name, props and door call are otherwise honoured.
    """
    from tortoise.kind_layers import ENTITY_LAYERS, KIND_PROP, entity_route

    t = (t or "").strip().lower()
    named = _named_layer_kinds(props)
    if not named:
        return t, None
    if t not in ENTITY_LAYERS:
        # Event door (the document door's kind is a Source genre, not a layer).
        return t, _first_layer_mismatch(named, t)
    # 1. Kinds that belong EXCLUSIVELY to one entity layer ROUTE the label — the
    #    kind is the decision, the door's `type` was only a hint. The authority is
    #    `kind_layers.routing_layers` (the classifier's CATALOG-UNION section —
    #    no graph gate, so a SUPERSET of this graph's; see
    #    `kind_layers.section_of` — plus the §5 Subject
    #    vocabulary), NOT the write-gate vocabularies — see that function for the
    #    P1 that ruled the latter out. Every named
    #    kind must be THE SAME kind, so a write that names two different layers
    #    is never routed on a coin flip. A NIL kind never reaches this test: a
    #    value that declares no layer cannot veto the one that does (it is
    #    dropped in `_named_layer_kinds`, #9964 review P2). Nor is a write that
    #    names ONE property under two spellings whose values disagree
    #    (`objectKind="core:organization",
    #    object_kind="core:team"`): routing would have to pick one of two values
    #    the caller gave for the same property — a silent rewrite of a prop to
    #    fit, and `named` is ordered by ``_KIND_PROP_SPELLINGS``'s dict iteration,
    #    so the survivor was an accident of declaration order. Such a write is
    #    REPORTED instead: nothing is dropped, nothing is rewritten.
    routes = [entity_route(kind) for _layer, _spelling, kind in named]
    named_kinds = {kind for _layer, _spelling, kind in named}
    target = (routes[0]
              if routes and all(r == routes[0] for r in routes)
              and len(named_kinds) == 1 else None)
    moved = named[0][2]
    if target is not None and target != t:
        # ⛔ ROUTING MUST NEVER TURN A LEGAL WRITE INTO A REFUSAL (#1934). The
        # Subject door validates `status` against ONTOLOGY §4.2; the Object door
        # validates nothing. So routing object→subject with an Object-only status
        # (`status='active'`) would RAISE where the caller's write previously
        # succeeded — a refusal invented by this fix. When that would happen the
        # write keeps the layer the caller asked for and is REPORTED instead: no
        # write is ever refused, and no prop is ever rewritten to fit.
        # ⚠️ `isinstance` FIRST, exactly as the Subject door's own validation does
        # (`_subj_status` below): `_SUBJECT_STATUS_VALUES` is a frozenset, so
        # membership HASHES the operand and an unhashable `status` (a list/dict)
        # raised `TypeError: unhashable type` HERE — a crash invented by a guard.
        # A non-str status is not routable (the Subject door would refuse it), and
        # it is not a crash either.
        _status = props.get("status", "live")
        blocked = (target == "subject"
                   and not (isinstance(_status, str)
                            and _status in _SUBJECT_STATUS_VALUES))
        if not blocked:
            for _layer, spelling, _kind in named:
                props.pop(spelling, None)
            props[KIND_PROP[target]] = moved
            return target, {"code": "kind_layer_routed", "kind": moved,
                            "layer": t, "actual": (target,)}
        return t, {"code": "kind_other_layer", "kind": moved,
                   "layer": t, "actual": (target,),
                   "why": (f"the write's status {props.get('status')!r} is not one "
                           f"of the {target.capitalize()} lifecycle's "
                           f"{', '.join(sorted(_SUBJECT_STATUS_VALUES))} values, "
                           f"and re-routing would refuse a write that is legal "
                           f"at this door")}
    # 2. A kind declared only on other layer(s) cannot be re-routed here: report
    #    it (warn-not-block) and let the write land as asked.
    return t, _first_layer_mismatch(named, t)


def _first_layer_mismatch(named: list[tuple[str, str, str]],
                          layer: str) -> dict | None:
    """The first named kind that ``layer`` does not declare, as a note.

    ``None`` when the layer declares every named kind (including the §5
    point↔object / point↔event overlaps — #7813) or when nothing declares the
    kind at all (the vocabulary is deliberately open).
    """
    from tortoise.kind_layers import LAYERS, undeclared_on

    if layer not in LAYERS:
        return None
    for _named_layer, _spelling, kind in named:
        others = undeclared_on(kind, layer)
        if others:
            return {"code": "kind_other_layer", "kind": kind,
                    "layer": layer, "actual": others}
    return None


def _kind_layer_advisory(note: dict | None, node_id: str | None,
                         name: str | None = None) -> dict:
    """The ADVISORY payload for ``_kind_layer_note``'s note (#8171 channel).

    ADVISORY ONLY: the write has already landed, nothing here raises or changes
    what was stored, and a caller that ignores it loses nothing but the notice
    (#1934 warn-not-block).

    ``name`` is the entity name, needed only when the re-route SPLIT an existing
    entity (``note['split_peer_id']``) — the message then names both ids.
    """
    if not note:
        return {}
    from tortoise.kind_layers import DOOR

    kind, layer, actual = note["kind"], note["layer"], note["actual"]
    declared = "/".join(a.capitalize() for a in actual)
    _article = "an" if declared[:1] in "AEIOU" else "a"
    if note["code"] == "kind_layer_routed":
        # ⛔ "no data was lost" is only true when the OTHER entity label holds no
        # node of this name. Identity is label-scoped, so on a graph that already
        # holds the OLD label (#2489 records no repair path) the re-route mints a
        # SECOND node and leaves the stale one answering every old-label read —
        # the caller is the only one who can reconcile that, so the advisory
        # names both ids instead of claiming losslessness (#9964 review).
        _peer = note.get("split_peer_id")
        _peer_article = "an" if layer[:1].lower() in "aeiou" else "a"
        _door = DOOR[actual[0]]
        message = (
            f"{kind!r} is {_article} {declared}-layer kind, so this write landed "
            f"as a :{declared} (with the kind on {actual[0]}Kind) instead of "
            f"the {layer.capitalize()} the door requested. The kind decides the "
            f"layer (ONTOLOGY §1); "
            # ⛔ The claim is about the GRAPH, never about THIS call. The routed
            # id is DETERMINISTIC (`_entity_name_id(<label>, name)`), so a re-run
            # of the very call that first split the entity MERGES onto the node
            # it already minted — the peer probe below only asks whether the
            # OTHER label holds the name, never whether the routed node is new,
            # so "this write created a SECOND node" was FALSE on every retry
            # (the common case: ingest and capture re-issue writes), while the
            # graph fact it is trying to convey stayed true (#9964 review).
            + (f"⚠️ {_peer_article} :{layer.capitalize()} named {name!r} ALREADY "
               f"exists ({_peer}), and entity ids are LABEL-scoped "
               f"(`sha256('<label>:<name>')`), so the graph now holds a SECOND "
               f"node ({node_id}) beside it rather than replacing it — {name!r} "
               f"has a node under EACH entity label, nothing reconciles them "
               f"for you "
               f"(#2489: no repair path), and the older one keeps answering "
               f"the {layer.capitalize()}-keyed reads. Retire the stale node "
               f"explicitly, or read this entity across both labels."
               if _peer else
               f"no data was lost, and the door that writes {_article} "
               f"{declared} explicitly is ``{_door}``.")
        )
    else:
        doors = ", ".join(f"{DOOR[a]} (e.g. {DOOR[a]}(name, "
                          f"{a}Kind={kind!r}))" for a in actual)
        message = (
            f"{kind!r} is declared on the {declared} layer, not on the "
            f"{layer.capitalize()} layer this write used. The write landed as "
            f"requested with that kind (#1934 warn-not-block); a "
            f"{declared} write is ``{doors}``."
        )
        if note.get("why"):
            message += f" It was not re-routed because {note['why']}."
    return {"advisories": [{
        "code": note["code"],
        "node_id": node_id,
        "message": message,
        "advisory": True,
    }]}


def _merge_advisories(*payloads: dict) -> dict:
    """Merge ``{"advisories": [...]}`` payloads (order preserved, no dupes)."""
    merged: list[dict] = []
    for payload in payloads:
        for note in (payload or {}).get("advisories") or []:
            if note not in merged:
                merged.append(note)
    return {"advisories": merged} if merged else {}


def _payload_entity_kind(e: dict) -> str | None:
    """The payload entity's kind to write, or ``None`` when it must NOT be.

    #9572: a v2 capture payload's ``kind`` is a CLASSIFICATION, not the caller's
    discriminator. The pipeline repairs an unclassifiable/out-of-vocab entity to
    its family fallback (``extractor_v2._ENTITY_FALLBACK`` = ``core:other``, and
    ``kind_classifier.KindClassifier._fallback`` does the same upstream), so by
    the time the door sees the payload a kind is ALWAYS present — an
    absent-key guard alone never fires on the default capture path, and the
    repaired sentinel reached the fold's ``ON MATCH … =coalesce($k, stored)`` as
    a real kind: a conversation that merely mentioned an Object reset its stored
    ``capability``.

    So the guard is on the VALUE, and it is one predicate shared with the hosted
    ``/v1/sessions/commit`` door (``tortoise.kind_fallbacks``) — the twin that
    imports nothing from this private surface is exactly how the fifth
    fabricator survived four review rounds.

    A payload entity that names a real kind still writes it, so a genuine
    re-classification is not frozen out.

    ⚠️ One stored VALUE changes, on purpose: a first create from a v2 payload
    whose kind was repaired used to store the sentinel (``objectKind =
    'core:other'``) and now stores ``other`` — the FOLD's ``ON CREATE`` spelling
    (the SDK/REST doors name no kind at all; ``'other'`` is what the fold
    supplies). Only a consumer that filters the EXACT string ``'core:other'``
    sees the move.
    """
    from .kind_fallbacks import payload_entity_kind

    return payload_entity_kind(e)

#: The canonical record types a live entity door may journal.
# `DocumentCreated` (#2296) joins the set: the last unjournaled canonical
# create, now appended by `_create_entity`'s Document arm (`.query`-invisible —
# it folds through `proj.apply`, so no raw-Cypher scan catches it).
_JOURNALED_ENTITY_EVENT_TYPES = (
    "ObjectRegistered", "SubjectAdded", "EventRecorded", "DocumentCreated",
)


def _journal_record_payload(event: dict) -> dict:
    """``event``'s payload minus the keys ``_emit_event`` owns.

    THE single source of the rule every entity-door journal call uses
    (``_create_entity`` and ``_journal_entity_event``), so the record's PAYLOAD
    is byte-identical to the live event's payload. The RECORD is not: the
    envelope keys above are added by ``_emit_event`` after this call, and the
    fold re-derives ``ingestedAt``/``updatedAt`` at replay. For an envelope name
    a write door does NOT drop from the live event, the caller prop survives
    LIVE while this strips it — the live ≠ replay residue tracked as #8553.
    ``_RESERVED_ENVELOPE_PROPS`` is NOT in that residue: every envelope name
    except ``id`` (which keeps its own fail-closed contract) is stripped at prop
    INTAKE (``_sanitize_props``) so none of them can reach ``_emit_event`` as a
    call argument or as the dispatch key, and the door that bypasses intake
    (``create_source``) drops them — plus the reserved actor keys — itself.
    """
    return {k: v for k, v in event.items() if k not in _JOURNAL_ENVELOPE_KEYS}


def _journal_vector(value):
    """An embedding as a JSON-able ``list[float]``, or ``None``.

    The journal is the sanctioned replay carrier for a stored vector
    (``_session_event_write`` journals the freshly COMPUTED one). A live node's
    value is a ``Vectorf32``, which is not JSON, so a door that re-journals the
    STORED vector has to convert it. A conversion failure degrades to ``None``
    — the record simply omits the vector, exactly as it did before this helper
    — and never raises into the caller's sweep.
    """
    if value is None:
        return None
    try:
        return [float(x) for x in value]
    except (TypeError, ValueError):
        return None


# Epic #902 A4 (§4.2): JSONL-ONLY batch_id record type — deliberately NOT in
# _GRAPH_EVENT_TYPES, so _stamp_batch_id writes the prop SET + this record and
# NO GraphEvent-store event (Q4 stays deferred). A10's rebuild pass-2b replays
# these records to restore batch_id (+ content_hash / mitigates_operator_id /
# mitigation_strength) after a rebuild.
_BATCH_ID_RECORD_TYPE = "BatchIdStamped"

# #3299: JSONL-ONLY entity-mutation record type — deliberately NOT in
# _GRAPH_EVENT_TYPES, so it rides the durability journal (rebuild replay)
# and not the :GraphEvent store. ONE record type with an ``op`` discriminator
# (the write-surface contract the #3299 design round chose over one event type
# per label×operation): replay dispatches on ``op``, so `delete`/`retract`/
# `revise`/`rename`/`restatus`/`supersede` share one fold vocabulary.
# `op="delete"` was the first operation (#3299); #3300 (MCP Point delete),
# #3312 (unjournaled entity update) and #3377 (unjournaled rename) extended the
# op set to `delete`/`rename`/`restatus`/`revise` rather than adding record
# types. The vocabulary itself lives ONCE in ``tortoise.projection``
# (``_ENTITY_MUTATION_OPS`` + the implemented/pending/state partitions), because
# a second hand-written copy at a producer is how a value gets silently omitted
# from a reader (#2901) — and `_journal_entity_mutation` is the ONE builder.
_ENTITY_MUTATION_RECORD_TYPE = "EntityMutated"

# #3377 DEFERRED (#4769): the rename op is implemented by the fold but is
# deliberately NOT produced yet — see `_update_entity`'s non-Point branch. That
# branch emits its warning on EVERY matching occurrence (not once per process):
# this class recurs, so a one-shot net would leave the second and later reverts
# silent, which is the failure mode the lane exists to remove.


def _raise_update_point_status_error(proj, id: str, *,
                                   status_only: bool = False,
                                   present: bool | None = None) -> bool:
    """#432: error path for the update_point draft→live promote guard.

    Runs a diagnostic existence read ONLY when the guarded SET returned no
    rows, so the happy path stays a single round trip. Missing point →
    ValueError matching the historical missing-point behavior; present but
    not promotable → illegal-transition ValueError.

    Returns ``True`` for the ONE outcome that does not raise — an already-live
    node on a status-only promote, the #1088 idempotent no-op — so the caller
    can finish that call the way the pre-write probe finishes it (with NO
    writes) instead of falling through to the tail's ``_mark_dirty``, which
    would advance the graph-wide EP epoch for a promote that matched nothing.
    Every other path raises and never returns.

    #1088: an already-LIVE point is short-circuited in ``update_point``
    (idempotent no-op), so apart from that window the only statuses that reach
    this error are the TERMINAL ones (``live.TERMINAL_EXCLUDED_STATUSES``:
    retracted / superseded / outdated / archived / deprecated) — a terminal
    point is never resurrected by update_point. A ``live`` node CAN reach it
    too — the replay may land it live between the pre-write short-circuit and
    the guarded ``SET`` — and that case is only an idempotent success when the
    caller asked for NOTHING BUT the status (``status_only``). With other props
    the promote matched nothing and nothing was applied, so returning silently
    would report a landed write that did not happen.

    ``present`` lets a caller that ALREADY read the node say so, so this
    diagnostic cannot probe a second time and claim ``No point`` for a node the
    caller just saw — a node vanishing between the two reads is the rebuild
    window, not a missing point.
    """
    if present is None:
        rows = proj.g.query(
            "MATCH (n:Point {id:$id}) RETURN count(n)", params={"id": id},
        ).result_set
        # `count()` is a single-row aggregate, so an EMPTY set means the
        # statement did not run. Indexing it blind raises `IndexError`, which is
        # not a retryable abort and would mask the abort that authorised the
        # retry.
        present = bool(rows[0][0]) if rows else False
    if not present:
        raise ValueError(f"No point {id!r}")
    # #1088 / #8854 review round 5: an ALREADY-LIVE node is the idempotent
    # no-op the pre-write short-circuit implements. Reaching here means the
    # replay landed the node live between the abort and the re-issue, where
    # reporting an illegal transition would contradict the door's own contract
    # — but ONLY for a status-only promote (see `status_only` above).
    status = proj.g.query(
        "MATCH (n:Point {id:$id}) RETURN n.status", params={"id": id},
    ).result_set
    node_status = status[0][0] if status else None
    if node_status == 'live':
        if status_only:
            return True
        # A live node is not an ILLEGAL TRANSITION, and pointing the caller at
        # retract_point()/supersede_point() would advise damaging a healthy
        # point (#8854 r7 P2). Say what actually happened: the promote matched
        # nothing, so the caller's other props were not applied.
        raise ValueError(
            f"Point {id!r} is already live — the promote matched nothing, so "
            "the other properties were NOT applied; re-issue without "
            "`status` to update them")
    raise ValueError(
        f"Illegal status transition — update_point only promotes draft→live; "  # noqa: F541
        f"use retract_point()/supersede_point() for lifecycle transitions"  # noqa: F541
    )


class _WriteNotLanded(Exception):
    """Internal: an AUTHORISED re-issue completed without applying anything.

    #8854 review P1. The retry predicate proves the graph KEY exists, and its
    own docstring admits the residual case: a rebuild that recreated the key
    and then failed mid-replay also reads ``EXISTS 1``. The re-issued
    ``MATCH (n:Point {id:$id}) SET …`` then matches NOTHING — the node has not
    been replayed into the new graph yet — and raises nothing, so the verb
    reported SUCCESS for a write it never made (the #7685 false-success,
    reached by a different mechanism).

    ``witness`` closes it: a write that can say whether it matched anything
    hands that answer to ``_graph_write_with_retry``, and a zero-row result on
    an attempt that was only reached BECAUSE a previous one aborted raises
    this sentinel instead of returning. It is retryable (nothing applied, so
    re-issuing cannot duplicate), and on budget exhaustion the ORIGINAL abort
    is re-raised with its TYPE unchanged, so the caller's ``except`` clause
    still catches it and ``_classify_db_failure`` still buckets it as ``db``.
    Its MESSAGE is NOT guaranteed untouched: on exhaustion the graph-swap
    family gains the #7357 legibility suffix (``retry.explain_graph_abort``)
    WHICHEVER way the graph-presence probe answers (present / absent /
    unprobeable), so an assertion on ``str(exc)`` must allow for that suffix. A
    test that pins the engine's BARE sentence belongs to the deletion arm
    instead: a deletion the predicate sees at classification time is refused
    before this arm is reached, so nothing is appended there.

    A zero-row FIRST attempt is untouched: it never reaches this class, so
    ``update_point``'s historical ``{}``-for-a-missing-id contract is safe.

    **Scope: the graph-abort family only.** ``retryable_aborted_write`` also
    admits the write-slot arm (ordinary multi-writer contention) and MISCONF,
    and NEITHER says anything about whether the target existed — so arming this
    sentinel on those would turn a benign ``update_point(<missing id>)`` into a
    stale ``ResponseError`` after four attempts. The gate in
    ``_graph_write_with_retry`` therefore LATCHES the first
    graph-deleted-or-replaced abort of the call (a later write-slot abort must
    neither arm it nor disarm it — a rebuild is itself a concurrent writer) and
    consults the witness only once that latch is set.

    Residual, stated honestly: during a rebuild, a target that genuinely never
    existed also yields zero rows, and this class raises the abort instead of
    the historical ``{}``. It cannot be told apart from "not replayed yet"
    without a read the verb would otherwise not pay for — and raising the abort
    is the truthful answer for a call whose graph was replaced mid-flight.
    Verbs whose zero-row result has its OWN documented meaning (a refused
    promote, an already-terminal retraction) do not rely on this sentinel, and
    they reach their documented error by DIFFERENT routes — worth keeping
    straight, because only one of them is a reuse of the first-attempt path:

    * the PROMOTE witness reuses ``_raise_update_point_status_error``, the same
      diagnostic ``update_point`` runs when its guarded ``SET`` returns no
      rows;
    * the RETRACTION witness must ADD a read: ``retract_point``'s first-attempt
      path raises straight from the empty CAS with no diagnostic at all. That
      added read takes ``rows[0]`` of
      ``MATCH (n:Point {id:$id}) RETURN n.status, n.outdated``, which is safe
      despite the duplicate-id fan-out: a zero-row CAS means NO node carrying
      that id satisfies ``_terminal_excluded``, so every one of them is
      terminal-or-outdated — exactly the witness's own raise condition — and
      the first row cannot disagree with the verdict.
    """

    def __init__(self, original: BaseException):
        super().__init__(str(original))
        self.original = original


def _landed_at_least_one(result) -> bool:
    """Witness for an aggregate whose row's VALUE counts matched nodes: >= 1.

    Covers both suffixes in use — ``RETURN count(n)`` and ``RETURN count(*)``.
    ``count()`` always yields one row, so the row's VALUE is the answer — an
    empty ``result_set`` would mean the statement did not run at all.
    """
    return bool(result.result_set) and result.result_set[0][0] > 0


_logger = logging.getLogger(__name__)

# #6140 — values whose unorderable-start WARNING was already emitted for the
# write in flight. A dedup-hit ``create_point`` warns on the create leg and then
# re-enters the write boundary through ``update_point`` with the same props; the
# `finally` in that branch clears the entry, so the suppression is scoped to the
# ONE write and a later write of the same value still warns. A ContextVar (not a
# module global) so concurrent callers cannot silence each other.
_WARN_SUPPRESS: contextvars.ContextVar[frozenset[str] | None] = contextvars.ContextVar(
    "_WARN_SUPPRESS", default=None
)

# ── #2600 server-resolved human actor ───────────────────────────────────────
# `_current_actor_user_id` carries the server-resolved human actor for
# session + write-event stamps. SET ONLY at auth seams (mcp_auth
# OrgResolutionMiddleware dispatch + hosted_api._data_sdk) — NEVER from
# client input. sdk.py is the neutral home: both mcp_auth and hosted_api
# already import sdk, and sdk must not import either (no import cycle).
_current_actor_user_id: ContextVar[str | None] = ContextVar(
    "_current_actor_user_id", default=None)


def _is_uuid_shape(value: object) -> bool:
    """UUID-shaped string gate for actor aliasing (#2600). ``created_by`` is
    NOT always a human id: production mints store literal ``"api"``,
    ``'st_'||hash`` recovery ids, and registry EMAIL self-signup creators.
    Alias to ``actor_user_id`` ONLY for UUID shapes. Byte-copies
    supabase_control._is_uuid semantics (the #1738 class) so the gate matches
    PostgreSQL uuid acceptance on BOTH planes: brace-strip, reject
    urn:/uuid:-prefixed forms, accept hyphenated / 32-hex-no-hyphen / braced.
    (sdk cannot import supabase_control — this is the neutral copy; keep the
    two in sync.)"""
    import uuid as _uuid
    if not isinstance(value, str) or not value:
        return False
    probe = value.strip()
    if probe.startswith("{") and probe.endswith("}"):
        probe = probe[1:-1].strip()
    if probe.startswith(("urn:", "uuid:")):
        return False
    try:
        _uuid.UUID(probe)
        return True
    except (ValueError, TypeError, AttributeError):
        return False


def _alias_actor_user_id(org: dict) -> dict:
    """#2600: canonical ``actor_user_id`` key on a resolved org dict —
    UUID-gated, never fabricated. Reads the RAW resolver fields (``user_id``
    / ``created_by`` / ``session_user_id`` — each auth lane carries whichever
    applies) and aliases to ``actor_user_id`` ONLY when UUID-shaped. Additive:
    never removes an existing key. Shared by mcp_auth.OrgResolutionMiddleware
    and the hosted_api REST DI terminals (single implementation, both planes).
    """
    raw = org.get("user_id") or org.get("created_by") or org.get(
        "session_user_id")
    if _is_uuid_shape(raw):
        org["actor_user_id"] = raw
    return org

#: C2 (#2518, #2513): max Object-spine anchors resolved from the query text
#: (the entity/fact-augmented key expansion pass's additive term source).
#: Bounded small: entity grounding needs the SUBJECT anchors, not the whole
#: Object vocabulary — a larger window would harvest noise keys from
#: co-matching unrelated entities.
_ENTITY_ANCHOR_LIMIT = 3
# Wider DB-side fetch for C2 anchor resolution: run_fts_query applies its
# own LIMIT in Cypher BEFORE the Python (score, id) re-sort, so a bare top-3
# fetch would cut equal-score ties DB-side in arbitrary order (P2 #2557
# review). Fetch a candidate pool and take the deterministic top-3 below.
_ENTITY_ANCHOR_CANDIDATES = 20
# Bound on the harvested alias pool before tokenization (P2 #2557 review:
# a high-degree anchor is otherwise the only bound — every linked Point's
# search_keys get collect()ed and tokenized per query).
_ENTITY_ALIAS_MAX = 200
_ENTITY_ALIAS_PER_ANCHOR = 64

# #1709: process-local serialization for the registry recovery mint (FalkorDB
# has no transactions; parity with the Supabase lane's FOR UPDATE row lock —
# concurrent same-token recoveries must not overshoot the non-bootstrap key
# cap). See TortoiseSDK.signup_token_recover.
_SIGNUP_TOKEN_RECOVER_LOCK = threading.Lock()


#: #2600: client-supplied actor claims are STRIP-AND-IGNORE (never a 4xx
#: — the server owns attribution). Shared by _sanitize_props (SDK backstop)
#: and the MCP boundary (_reject_server_managed_props in mcp_server.py —
#: same frozenset literal kept in sync; agent_id closes the reserved-key
#: contract for any future props surface, verified no tenant tool declares
#: it today). authoredBy is deliberately NOT here (pre-existing client
#: author-label residual — documented).
_RESERVED_ACTOR_PROPS = frozenset(
    {"actor_user_id", "owner", "initiated_by", "agent_id"})


def _sanitize_props(props: dict, *, reject_id: bool = False,
                    reject_belief_props: bool = False,
                    reject_claim_class: bool = False) -> dict:
    """#329: reject server-managed fields on tenant write surfaces.

    ``sourcePath``/``source_path`` are server-filesystem fields consumed by the
    operator ``--upgrade-all`` path (projection maps ``source_path`` →
    ``d.sourcePath`` via ``_SOURCE_HANDLED``/``_DOC_PASSTHROUGH_HANDLED``); a tenant setting them turns the
    graph into a file-read oracle. ``id`` overrides on entity surfaces mutate
    node identity / mint tenant-chosen Document ids. Both are rejected with a
    clear ValueError (fail-closed). ``api.add_document``'s explicit
    ``source_path`` parameter is UNTOUCHED — this only guards props passthrough.

    #2600: reserved actor keys (``actor_user_id``, ``owner``,
    ``initiated_by``, ``agent_id``) are STRIP-AND-IGNORE — never a 4xx,
    server owns the actor. Popped with a warning after the ``dict(props)``
    copy so the caller's dict is never mutated.

    #4647: after the reserved-key strip, every remaining value is checked
    against the store's numeric domain (INT64 int / double number) and a value
    the store would silently alter is REJECTED with a key-naming ValueError —
    this runs before the ``sourcePath``/``id`` rejects below.

    ``reject_belief_props`` (#7951): the GENERIC props surfaces
    (``update_point`` / ``create_entity`` / ``update_entity``) refuse the
    BELIEF fields — ``credibility``, and the baseline fields ``ep_alpha`` /
    ``ep_beta`` / ``baseline_set`` / ``baseline_source``. ``credibility`` is a
    documented WORD INPUT of ``create_point(credibility=…)`` /
    ``mitigate_operator(credibility=…)``; the baseline fields belong to the
    dedicated writers (``set_point_baseline`` and the source-inheritance
    recompute), which write them TOGETHER and keep the #398 label in sync.
    Through a generic bag they bypassed that accounting. #7951 is the
    structural home (ONE write chokepoint instead of per-path patching).

    ``reject_claim_class`` (#2351): the same chokepoint, one class further out.
    ``claimClass`` is PROVENANCE — the fact that this claim was COMPUTED rather
    than AUTHORED — and it is written by the write that MINTS the claim
    (``create_point``'s create map, via its explicit ``claim_class`` kwarg or
    the nested prop it pops and validates before this boundary runs). Through a
    generic update bag it was writable three ways, all measured: setting it to
    ``'asserted'`` on a derived claim RE-STAMPS it out of every guard surface
    (the EP backstop, ``create_operator``, ``create_direct_edge``,
    ``supersede_point``) so it can then override authored beliefs; passing
    ``None`` REMOVES the property outright (``SET n += $props`` with a None
    deletes it), the same escape without the marker; and a junk token persists
    an unknown third class the closed vocabulary exists to prevent. On TOP of
    that, no ``PointRevised`` arm replays the key, so a write through an update
    door is a LIVE-ONLY marker a rebuild silently drops — a derived claim
    quietly becomes authored (#9732). There is no legitimate caller intent —
    provenance is decided once, at birth, and a caller that disagrees mints a
    new claim — so this is a HARD-REJECT (never strip-and-ignore), matching
    ``baseline_system_default``/``_stub``. Both spellings are refused, the same
    way ``sourcePath``/``source_path`` are above: one class under a second name
    is how a later reader comes to treat one of them as a third state.
    """
    props = dict(props)
    # #2600: reserved actor keys are STRIP-AND-IGNORE (never a 4xx — the
    # server owns the actor). Pop BEFORE the reject checks below — the
    # dict(props) copy above makes the pop safe (the caller's dict never
    # loses a key). ``agent_id`` closes the reserved-key contract for any
    # future props surface (verified: no tenant tool declares it today —
    # it only appears as the server-side EventAPI construction arg).
    for _reserved in _RESERVED_ACTOR_PROPS:
        if _reserved in props:
            _logger.warning(
                "ignoring client-supplied %r on tenant props", _reserved)
            props.pop(_reserved)
    # #2296 review: an `_emit_event`-owned name must never survive as a caller
    # prop. Python binds a `**props` key to a named parameter (or an explicit
    # keyword) AT THE CALL, so every door that splats caller props into
    # `_emit_event` (`update_point` splats straight through) turns the prop
    # into that argument — and `_emit_event` cannot see the collision from
    # inside. TWO sets are reserved here, both strip-and-ignore:
    #   * `_RESERVED_ENVELOPE_PROPS` — the journal ENVELOPE names. A caller
    #     `recorded_ts` forges the record's envelope `ts`; `point`/`payload`
    #     bind the snapshot/payload parameters; `type` re-types or SUPPRESSES
    #     the record; `ts`/`event_id`/`projection_version`/`initiated_by`
    #     overwrite the built envelope via `event.update(extra)`.
    #   * `_RESERVED_EMIT_CALL_PROPS` — names that are not envelope keys but
    #     collide at the CALL (`type_` is `_emit_event`'s first parameter;
    #     `new_content` is passed explicitly by `update_point`'s emit), which
    #     raised a `TypeError` AFTER the live write and left the mutation
    #     live-only.
    # The strip belongs HERE, at the one intake every props surface passes
    # through (`create_point`/`update_point`/`create_entity`/`update_entity`),
    # rather than at each emitting door — the same fix shape #5196 used for the
    # parameter spelling of the index-merge run token, after a per-door patch
    # left this class of door open. Strip-and-ignore, matching the door that
    # bypasses this intake (`create_source` pops the sets itself) and the
    # reserved actor keys above: these are `_emit_event` names, not tenant
    # properties, so dropping them also keeps live == replay.
    for _emit_name in _drop_reserved_emit_props(props):
        _logger.warning(
            "ignoring client-supplied %r on tenant props "
            "(reserved `_emit_event` name)", _emit_name)
    # #4647: a value the store cannot hold without altering it must fail here,
    # not be silently clamped/rounded by FalkorDB on the way in.
    for _key, _value in props.items():
        _reject_unrepresentable_number(_key, _value)
    for key in ("sourcePath", "source_path"):
        if key in props:
            raise ValueError(
                f"{key!r} is a server-managed field and cannot be set via props."
            )
    # #3947 review (security): `contains_session` is an ENVELOPE-level capture
    # directive (it drives the `(:Session)-[:CONTAINS]->(:Point)` rebuild fold),
    # never a Point property. Reject it here as the fail-closed boundary: a
    # tenant-supplied prop of this name must not be able to reach a writer that
    # could turn it into a structural graph fact on replay.
    # #5196 round 3/4: the index-merge run token is WRITTEN to the node by
    # `_upsert_source`'s `run_clause`, so it is DECLARED — but it is
    # server-minted, so it must be refused at the props boundary. This is the
    # fail-closed backstop the MCP boundary's comment already claims exists: the
    # MCP set alone left the OPEN document passthrough writable
    # (`_upsert_document` passes no `allow_keys`, #228), and it was measured:
    # `create_document(__runId="DOC_FORGED")` persisted the forged token.
    # #5196 round 5/6: on this route `_merge_run_id` does NOT reproduce the
    # `__runId` write — only the SourceCreated projection branch pops it into the
    # run token; DocumentCreated goes to `_upsert_document`, which would persist
    # it as a LITERAL undeclared node property (measured). So the guard here stops
    # an undeclared server-ish key landing on a `:Source`, while the `__runId`
    # write is only reachable through `_upsert_source` (i.e. `create_source`).
    if "__runId" in props or "_merge_run_id" in props:
        raise ValueError(
            "'__runId'/'_merge_run_id' is a server-managed field and cannot be "
            "set via props."
        )
    if "contains_session" in props:
        raise ValueError(
            "'contains_session' is a server-managed capture field and cannot "
            "be set via props."
        )
    # #1486 (code-review P1): is_episodic is the POINT-arm discriminator of the
    # points quota — quota.py counts `is_episodic IS NULL OR = false` points, and
    # since #1911 it ALSO counts Object/Subject nodes unconditionally, so the
    # resource is no longer Points alone (#1975). For Points, a tenant
    # setting it true via props would exclude their points from the quota —
    # unlimited points past the paid-tier cap. Server-managed: internal
    # capture/extractor callers set it via the explicit `is_episodic` kwarg on
    # create_point/create_entity/create_event/update_point (bound before this
    # props passthrough runs), and the MCP tenant tools reject it at the
    # boundary — this reject is the fail-closed backstop for any other surface.
    if "is_episodic" in props:
        raise ValueError(
            "'is_episodic' is a server-managed field (quota discriminator) "
            "and cannot be set via props."
        )
    # #5004 review: the embedding is a DERIVED, server-computed field for the
    # NORMAL path. It is deliberately NOT rejected here: `create_point` has a
    # recorded decision (PR #3018 review P2) that a CALLER-supplied `embedding`
    # is stored VERBATIM — "a silent float64-list → vecf32/float32 rewrite of
    # an explicitly caller-owned value would change what a vector read
    # returns" (`_embedding_expr = "$embedding"`). Rejecting the prop would
    # reverse that ruling silently, so instead the writer marks the payload
    # `embedding_verbatim` and the REPLAY honours it (restores raw, no
    # `vecf32`) — see `stamp_journal_embedding` and `_upsert_point_props`.
    # What this boundary DOES still reject is forging the ATTESTATION: a
    # caller-supplied vector must not be able to claim a server identity.
    #
    # #5004 review: the embedding's journal IDENTITY keys are payload metadata
    # minted by the writer (`stamp_journal_embedding`). The replay drops them
    # from the payload (`_POINT_HANDLED`), so a caller-supplied value would
    # persist LIVE and vanish on a rebuild — a live/replay parity break. They
    # are brand-new names, so rejecting them cannot break an existing caller;
    # this closes the only route by which they could become node properties.
    _embed_identity_keys = set(props) & {
        "embedding_model", "embedding_revision", "embedding_text_hash",
        # #5004 round-3/4: the verbatim marker is server-owned too. It is a
        # declared node property (so a re-emit carries it), which means a
        # tenant-supplied value WOULD persist live — and it both flips the
        # replay's storage form and silences the R1 model-change attestation.
        # `embedding_preserved` is the same argument: truthy makes
        # `stamp_journal_embedding` skip the identity on a CREATING record, so
        # a caller could silence the model-change record R1 exists to keep
        # (and, living only on the live side, it is a parity break too).
        "embedding_verbatim", "embedding_preserved"}
    if _embed_identity_keys:
        raise ValueError(
            f"{sorted(_embed_identity_keys)} are server-managed embedding "
            "journal fields and cannot be set via props."
        )
    # #6151 (review P3): `baseline_system_default` is the RECORDED #2199 birth
    # fact — `_apply_source_inheritance` re-injects the system default ONLY on a
    # point that carries it, so a caller able to set it could self-grant the
    # +2.0 default on a point that was never born live (an EXPLICIT-status /
    # set-by-author claim), making a self-declaration look like a recorded
    # provision. HARD-REJECT rather than strip-and-ignore, following the
    # `embedding_verbatim`/`embedding_preserved` precedent above: unlike the
    # reserved ACTOR keys (where the server always has an authoritative value to
    # substitute), there is no legitimate caller intent for this key, so a
    # silent strip would let an attempted self-grant pass unnoticed. The
    # legitimate writers set it AFTER this boundary — `create_point` puts it in
    # its one-write CREATE map, and the internal baseline writer
    # `_stamp_authored_word` writes it directly — so neither re-enters here.
    if "baseline_system_default" in props:
        raise ValueError(
            "'baseline_system_default' is a server-managed baseline-provision "
            "marker and cannot be set via props."
        )
    # #7936 (review P2): `_stub` is the event-name-stub DISCRIMINATOR the
    # first-registration probe in `_create_entity` excludes — a marked stub is
    # NOT a registered node, so a later `create_entity` for the name still
    # journals its `ObjectRegistered`/`SubjectAdded` (the durability half of
    # #2194/#2295). Its VALUE therefore decides whether a registration line is
    # written, which makes it server-managed: the projection is the only writer
    # (`_event_plain_merge` sets it inside ON CREATE, `_upsert_object` /
    # `_upsert_subject` clear it inside ON MATCH), and neither path re-enters
    # this intake. Hard-reject (never strip), matching
    # `baseline_system_default`/`__runId`: there is no legitimate caller intent,
    # and a silent drop would let an attempted forgery pass unnoticed.
    # Measured before this reject: `update_entity(<stub id>, _stub=False)` was
    # ACCEPTED by `_update_entity`'s direct `SET n += $p`, journaled as an
    # `EntityMutated` (so the forged value SURVIVED `rebuild_all`), and the
    # following `create_entity` then SKIPPED the registration — re-opening the
    # node-loss + live != replay class #7936 closes (live `_stub` cleared by
    # adoption, replay `_stub=false` + `objectKind='other'`). The MCP boundary
    # (`_SERVER_MANAGED_PROPS`) refuses it BEFORE the write is attempted; this
    # is the fail-closed backstop for the direct SDK route.
    if "_stub" in props:
        raise ValueError(
            "'_stub' is a server-managed event-stub discriminator and cannot "
            "be set via props."
        )
    # Lane L5 (#5097 review P1): the SIBLING discriminator, and the same class
    # of hole. `_stub_completion` is what `_upsert_source` reads to suppress the
    # ON CREATE creator-only defaults (`id`/`version`/`externalId`) and to admit
    # `_SOURCE_CAPTURE_REPLAY_PROPS` to the `:Source` passthrough. A caller that
    # can set it can therefore forge the capture writer's provenance
    # (`sessionId`/`capturedAt`/`eventId`) AND strip a node's identity props —
    # and the key rides the journal, so a rebuild restores the forgery.
    # `create_source` refuses it in its own `**props` guard (it bypasses this
    # intake); this is the fail-closed backstop for every other props surface
    # (`create_point`/`create_entity`/`update_*`). Hard-reject, never strip,
    # matching `_stub` above: there is no legitimate caller intent.
    if "_stub_completion" in props:
        raise ValueError(
            "'_stub_completion' is a server-managed capture-completion "
            "discriminator and cannot be set via props."
        )
    # #9451: the sibling key on the same `SourceCreated` record. It states that
    # the record's `ingestedAt` is the instant the write STORED (not the capture
    # clock), which is what licenses the fold's ON MATCH arm to apply it — so a
    # caller able to set it could move a `:Source`'s `ingestedAt` on replay, i.e.
    # out of the recency/decay ordering, and the key rides the journal so every
    # rebuild would restore it. Same class and same hard-reject as
    # `_stub_completion` above; the ONE producer never re-enters this intake.
    if "_ingestedAtStored" in props:
        raise ValueError(
            "'_ingestedAtStored' is a server-managed journal discriminator and "
            "cannot be set via props."
        )
    # #7951 (review round 5): `credibility` is NOT a generic prop — it is the
    # author's declared WORD, a documented input of `create_point` /
    # `mitigate_operator`, which validate it and run the authored-assertion
    # accounting. Measured through a generic update bag: an INERT label on a
    # born-live part (frozen at 3.0 even under a real T1 source), a label with
    # no baseline and no body, and an unvalidated value stored verbatim
    # ('gold', 'banana'). Fail closed; #7951 owns the single chokepoint.
    #
    # #6151 review P1(b)/P3-2: the SAME generic surfaces must refuse the
    # BASELINE fields. `baseline_source` is the token the dedup path used to
    # read as proof of the #2199 provision, and the whole belief quartet is
    # written by the dedicated chokepoints (`set_point_baseline`, the
    # inheritance recompute) which keep `ep_alpha`/`ep_beta`/`baseline_*` and
    # the #398 `credibility` label consistent. Through a generic bag they
    # bypassed that: `baseline_source='system-default'` forged the #2199
    # provision (measured +2.0 unearned, 1.2 -> 3.2), and `ep_alpha=0.4` on a
    # word-bearing `set-by-author` row wrote a "frozen" value the recompute
    # then replaced (0.4 -> 5.1). Fail closed; #7951 owns the one chokepoint.
    #
    # #6151 review P1 (round 6): the BASELINE quartet reject is UNGATED, not
    # behind `reject_belief_props`. That flag is passed only by
    # `update_point` / `_create_entity` / `_update_entity`, so
    # `create_point`'s props bag still accepted them — and the create map is
    # built as `_create_map.update(_baseline_create_fields())` BEFORE the
    # caller's props loop (`for _key, _val in props.items()`), so a caller's
    # `ep_alpha`/`ep_beta`/`baseline_source` OVERRODE the writer's own
    # baseline values. Measured: `create_point("statement", "forged birth",
    # ep_alpha=10.0, ep_beta=1.0, baseline_set=True,
    # baseline_source="set-by-author")` persisted a FROZEN 10.0 a real T1
    # source could never move, and the `system-default` spelling forged the
    # #2199 provision at birth. Same server-managed class as
    # `baseline_system_default` above: reject, never strip, because there is no
    # legitimate caller intent. Safe to ungate — `create_point` writes its
    # baseline fields into the create map AFTER this boundary, so no internal
    # writer re-enters. `credibility` stays gated: it is a DOCUMENTED input of
    # `create_point`/`mitigate_operator` (validated and accounted there), and
    # `create_point` pops its nested spelling below.
    if reject_belief_props and "credibility" in props:
        raise ValueError(
            "'credibility' is not a generic property — it is a documented "
            "input of create_point(credibility=…) and "
            "mitigate_operator(credibility=…), which validate the ladder "
            "word and record it as the authored-assertion label. Use one "
            "of those, or set_point_baseline for an explicit belief "
            "(#7951)."
        )
    _baseline_props = [k for k in
                       ("ep_alpha", "ep_beta", "baseline_set",
                        "baseline_source")
                       if k in props]
    if _baseline_props:
        raise ValueError(
            f"{_baseline_props} are baseline fields the dedicated writers "
            "own (set_point_baseline for an explicit belief, the "
            "source-inheritance recompute for a derived one) — they are "
            "not generic properties and cannot be set via create_point's "
            "props. Use set_point_baseline(alpha, beta) (#7951)."
        )
    # #2491: outdated is the terminalizing flag written ONLY by the lifecycle
    # writers via raw cypher (invalidate_point/supersede_point SET
    # n.outdated=true) after they journal + drop EP messages. Accepting it via
    # props (update_point/create_point/_update_entity label-loop) silently
    # flag-flips a live claim to EP-dead with NO journal event and NO
    # invalidate_factor_messages drop — resurrecting the #2422 ghost class
    # (the degenerate factor never re-runs to zero the stale sibling seed).
    # Route through invalidate_point()/supersede_point() instead.
    if "outdated" in props:
        raise ValueError(
            "'outdated' is a server-managed lifecycle flag — use "
            "invalidate_point() or supersede_point() to terminalize a claim."
        )
    # #2351 (epic #2349 E-derived): the claim CLASS is provenance, not a
    # property — see `reject_claim_class` in the docstring. Gated exactly like
    # `reject_belief_props` (the #7951 chokepoint) so that EVERY generic write
    # bag refuses it by construction instead of one door being patched and the
    # next forgotten: `update_point` and `update_entity` are the two documented
    # `:Point` update doors (`commit_schema`), and `update_entity` reaches its
    # write through this same boundary. `create_point` must keep accepting the
    # nested form: it POPS `claimClass` and validates it on the way in (it
    # calls this boundary with no flags), and the MCP `tortoise_create_point`
    # route reaches the marker through exactly that `props` bag.
    for _class_key in (_claim_class.PROP, "claim_class"):
        if reject_claim_class and _class_key in props:
            raise ValueError(
                f"{_class_key!r} is provenance, not a generic property — it is "
                "written by create_point, not by an update door (#2351): no "
                "`PointRevised` arm replays it, so accepting it here would "
                "persist a live-only marker that a rebuild silently drops, and "
                "re-stamping or clearing it would move a derived claim out of "
                "the #2351 override guard (see #9732). Stamp the class on the "
                "CREATE."
            )
    # #5256: the `extractedFrom` READ-VERSION anchor is server-derived (it is
    # read from the :Source node by `resolve_source_versions` on the LIVE path
    # and carried in the Point's journaled snapshot), so a tenant must never be
    # able to set it. Accepting it would let a caller forge provenance — claim
    # a Point was read from a different version than the one recorded — and,
    # because the value persists live while the replay re-derives it, it is
    # also a live/replay parity break. Rejected on EVERY spelling
    # (`sourceVersion`, `sourceVersions`, `sourceVersionTransit`): the plural
    # spelling is the natural caller guess (mirrors `extractedFrom`'s own
    # accepts-list/str shape) and must not slip through as an unknown prop.
    _source_version_keys = [k for k in
                            ("sourceVersion", "sourceVersions",
                             "sourceVersionTransit")
                            if k in props]
    if _source_version_keys:
        raise ValueError(
            f"{_source_version_keys} are server-managed provenance fields and "
            "cannot be set via props."
        )
    # #7856: a goal's ACHIEVEMENT state is a CLOSED vocabulary — never a free
    # string. Before this `goalState` accepted any value (the objective map's
    # state was written by a throwaway script), so "what is active?" could not
    # be answered from the graph. This is the fail-closed boundary shared by
    # every SDK write surface (`create_point`, `create_object`,
    # `create_entity`, `update_point`, `update_entity`); an invalid word is
    # refused BEFORE any graph write. `None` is allowed (explicit clear).
    # The lower-level `EventAPI`/journal producer seam does NOT pass through
    # here — it is the documented no-SDK-guarantee path (see `api.py`
    # `add_point`), so the vocabulary is enforced at the SDK/MCP boundary, not
    # at the projection choke point that live writes and replay share.
    if "goalState" in props and props["goalState"] is not None:
        _goal_state = props["goalState"]
        if not isinstance(_goal_state, str) or _goal_state not in GOAL_STATE_VALUES:
            raise ValueError(
                f"Invalid goalState {_goal_state!r}. A goal's achievement "
                f"state is a closed vocabulary: {sorted(GOAL_STATE_VALUES)}. "
                "(Lifecycle is the separate `status` field — goalState is not "
                "draft/live/retracted/archived.)"
            )
    if reject_id and "id" in props:
        raise ValueError("'id' is server-managed and cannot be set via props.")
    return props


# R2 (#1541) D3: `_flatten_search_keys_prop` used to be DEFINED here. It now
# lives with the Point prop contract it serves — `tortoise/projection/
# entities.py::flatten_search_keys_prop`, imported at the top of this module —
# because the SHARED projection writer (`_upsert_point_props`) is the door every
# write reaches and it needs the same rule (#8006). `create_point`,
# `update_point` and `update_entity` still call it here, under this name.


def _point_fts_indexed_fields() -> list[str]:
    """#10122: the fields the Point FULLTEXT index actually HOLDS.

    Read off ``projection._FULLTEXT_INDEX_LABEL_FIELDS`` — the table that
    CREATES the indexes — never a second hand-written copy of the field list.
    So the advisory below cannot drift from the index, and an index change that
    adds a field (``name`` being the one #10122 is about) silences it by
    construction rather than needing a second edit.
    """
    for label, fields in _FULLTEXT_INDEX_LABEL_FIELDS:
        if label == "Point":
            return list(fields)
    return []


def _lexical_text(value) -> str:
    """#10122: the coverage-comparison key — whitespace-collapsed, casefolded.

    Accepts a list/tuple as well as a str: the doors flatten ``search_keys``
    before this runs, but the generic ``update_entity`` payload is the caller's
    own dict and a raw array is the #5482 shape, not a crash here.
    """
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        value = " ".join(str(v) for v in value)
    return " ".join(str(value).split()).casefold()


def _warn_point_name_not_lexically_indexed(
    props: dict, *, door: str, content: str | None = None,
    stored: dict | None = None,
) -> None:
    """#10122: advise, at the write door, that a Point's ``name`` is NOT
    lexically indexed. Warn, never block (#1934's precedent for a declaration
    gap) — and never mutates ``props``, because the node still stores it.

    THE DEFECT. The Point FULLTEXT index holds ``content`` + ``search_keys``
    only, while the write doors ACCEPT ``name`` on a Point and the node stores
    it. The stored name is therefore invisible to the lexical leg: a query for
    the point's OWN exact name returns nothing — MEASURED on the embedded and
    the docker lane (#10122: the name-only point is absent from
    ``run_fts_query`` while the content-carrying one is found). ``Event`` /
    ``Subject`` / ``Object`` DO index ``name`` (``_FULLTEXT_INDEX_LABEL_FIELDS``
    above), so a Point is the one label where an ACCEPTED write mints a
    silently un-findable record — the #5482 class, which this repo fixed rather
    than tolerated.

    ⛔ NOT the fix: adding ``name`` to the index. That is a RANKING change (it
    moves collection statistics and every Point's field length) and it is gated
    on the #8486 measurement, which is postponed (the ruling recorded on
    #10122). This advisory has ZERO ranking effect — it reads no score, and it
    writes nothing. The HELPER does no graph read of its own: the only
    non-payload input is the stored text the ``update_point`` door has already
    read and hands over, which is why that door's verdict covers the text the
    index WILL hold rather than only the fields the caller supplied.

    SILENCE ON THE COMMON SHAPE. A name whose tokens already appear in the
    text the index will hold does not warn — the identifier IS reachable, so
    there is nothing to advise. WHICH text that is depends on the door:
    ``create_point`` compares against the payload it is about to write (the
    whole write), while ``update_point`` compares against the STORED
    ``content``/``search_keys`` it reads in its own probe, because a name-only
    update carries no indexed text of its own. ``update_entity``'s Point branch
    still compares payload-only — an asymmetry that is deliberate for now
    (threading the same read there is its own change) and named in
    ``test_10122_point_name_not_indexed.py``.

    ``content`` is the DOOR's own content argument, supplied only where the
    payload does not carry it: ``create_point`` takes ``content`` as a required
    positional (it is not part of the ``**props`` passthrough), so without this
    a point whose title is plainly in its ``content`` would warn — the exact
    false positive the coverage check exists to avoid. ``update_point`` /
    ``update_entity`` pass ``content`` inside the props dict and need not set
    it.
    """
    name = props.get("name")
    if not isinstance(name, str):
        return
    needle = _lexical_text(name)
    if not needle:
        return
    fields = _point_fts_indexed_fields()
    if "name" in fields:
        return  # the index holds `name` after all — nothing to advise
    if not fields:
        return  # no Point index on this engine — a different (visible) state


    # ⛔ TOKEN-LEVEL, NOT SUBSTRING (#10122 review P2). The lexical leg answers
    # with an OR-UNION of the query's tokens (``build_or_query``), so a name
    # whose tokens are SCATTERED through ``content`` is findable — a substring
    # test called that uncovered and the message then asserted an absence that
    # does not hold (measured: content "the probe was a write for capstone and
    # 9664 rows" answers a query for name "capstone-9664 write probe"). The
    # honest condition is DISJOINT token sets: not one token of the name appears
    # in the text this write indexes. Anything short of that only costs RANK,
    # which is not what an advisory may promise.
    from tortoise.sparse import tokenize_sparse_query

    name_tokens = set(tokenize_sparse_query(str(name), keep_numeric=True))
    indexed_tokens: set[str] = set()
    # ⛔ PER FIELD, and the STORED component is used only where the payload
    # does NOT set that field (#10122 rounds 2-4). Two earlier versions got
    # this wrong in opposite directions: a payload-only comparison fired on
    # every name-only update, and a single UNIONED stored blob then silenced an
    # update that REPLACES content (measured: stored content carrying the name,
    # then `update_point(name=…, content="unrelated curtains here")` → silent,
    # although the tokens were only in the text being overwritten). The text
    # the index will hold after this write is therefore: the payload value
    # where the payload sets the field, else the stored component.
    effective: dict[str, object] = {}
    for field in fields:
        if field == "content" and content is not None:
            effective[field] = content
        elif field in props:
            effective[field] = props.get(field)
        elif stored:
            effective[field] = stored.get(field)
        else:
            effective[field] = None
    for field in fields:
        indexed_tokens.update(tokenize_sparse_query(
            _lexical_text(effective[field]), keep_numeric=True))
    if not any(ch.isalnum() for ch in str(name)):
        # Silent only when the name carries NO alphanumeric character at all
        # (pure punctuation/whitespace): then there is nothing to index and the
        # advice would be meaningless. ⛔ NOT "when our token list is empty":
        # ``build_or_query`` does not drop a degenerate query, it falls back to
        # the ESCAPED RAW literal (#1791), so ``build_or_query("R&D") == "R&D"``
        # and the engine still tokenizes it — measured: a point whose content
        # says "the brand R&D handles the audit" IS returned by
        # ``run_fts_query("R&D")`` at 1.0. So an `R&D`-shaped name (or a
        # stopwords-only one, since our stopword list is not the engine's) is
        # genuinely findable once its literal sits in an indexed field, and the
        # advisory must speak rather than stay silent. That was a false negative
        # in the previous round (#10122 review P2).
        return
    if name_tokens:
        if name_tokens & indexed_tokens:
            return
    else:
        # No token on our side (e.g. a one-character literal) but the name DOES
        # carry alphanumerics: fall back to a casefolded literal containment
        # test, which is what the engine's raw-literal path matches.
        folded = _lexical_text(name)
        if any(folded in _lexical_text(effective[f]) for f in fields):
            return
    # The roster is DERIVED from the same table, so a label that gains (or
    # loses) a ``name`` index changes this sentence with the index.
    other_labels = sorted(
        label for label, label_fields in _FULLTEXT_INDEX_LABEL_FIELDS
        if label != "Point" and "name" in label_fields)
    if not other_labels:
        label_clause = "no other label indexes it"
    elif len(other_labels) == 1:
        label_clause = f"{other_labels[0]} indexes it"
    else:
        label_clause = f"{'/'.join(other_labels)} index it"
    where = ("the text the Point fulltext index will hold after this write"
             if stored else
             "the text THIS write supplies for those fields (this door has no "
             "stored text to compare against)")
    basis = ("none of the name's tokens" if name_tokens
             else "the name's literal")
    _logger.warning(
        "%s: this Point stores name=%r, but %s appears in %s (%s) — so a "
        "lexical query for the name can only reach this point through other "
        "evidence, and will not rank on the name itself (#10122). Put the "
        "identifier in `content` or in `search_keys`: `name` is not lexically "
        "indexed on a Point (%s). The write is accepted as-is — "
        "warn-not-block, #1934.",
        door, name, basis, where, "/".join(fields), label_clause,
    )


def _refuse_document_kind_demotion(stored_kinds, props, *, surface: str,
                                   none_demotes: bool = False) -> None:
    """#5140 P1: `documentKind` is the documenthood/METER key, not metadata.

    THE DEFECT THIS CLOSES. The `documents` cap counts
    ``MATCH (s:Source) WHERE s.documentKind IS NOT NULL AND
    s.documentKind <> 'transcript'`` (`quota.DOCUMENT_METER_PREDICATE`) — that
    predicate is what decides whether a node is a document at all (the #5013
    Q-S2 ruling, Option B). Under D10 a document IS a `:Source` keyed
    ``url = <doc id>``, which is the node `create_source`/`update_entity` merge
    by `url`/`id`, and `documentKind` is caller-settable on both surfaces
    (`_SOURCE_EXTRA_PROPS` on the passthrough, `_SOURCE_NODE_PROPS` on the
    generic entity surface). So ONE caller call rewrote an existing document's
    genre to `transcript` and the meter dropped out from under it — repeatable
    per document, so the cap did not hold. MEASURED before this guard, 3 seeded
    document Sources: meter 3 -> 2 via
    ``create_source(did, 'document', documentKind='transcript')``, then 2 -> 1
    via ``update_entity(did, documentKind='transcript')``.

    THE RULE. A write may SET a genre where none is stored (promotion — that is
    how a document `:Source` is minted through these surfaces) and may retarget
    one metered genre to another (`report` -> `brief`, both metered), but a
    caller surface may not move a metered document OUT of the metered class
    (`None` / `'transcript'`). Demotion is the escape; the forgery direction
    (`documentKind` added to a provenance Source) is deliberately left alone
    (`tests/test_quota.py`'s NON-OVER-REACH arm pins it: "SETTING a genre where
    none is stored is how a document `:Source` is minted through these surfaces
    at all"). ⚠️ It over-counts rather than under-counts, so it does NOT defeat
    the cap — but it is not harmless, and "fails closed" would overstate it: the
    node leaves `list_sources()` (`documentKind IS NULL`) and becomes a valid
    `aboutDocument` target. That is #5140 finding 7's forge direction, left open
    by that decision, not by an oversight.

    Both spellings are refused on a METERED document, for DIFFERENT reasons —
    stated truthfully because the earlier wording claimed a mechanism the code
    did not implement. The camelCase `documentKind` is the key the meter reads,
    so refusing a demotion through it is the demotion guard itself (a promotion
    or a metered -> metered retarget still lands). One asymmetry belongs with
    it: `documentKind=None` is a REFUSAL on `update_entity` (its write is
    `SET n += $p`, where a null REMOVES the property) and a NO-OP on
    `create_source` (the passthrough skips null values, so nothing is written)
    — hence `none_demotes`. The snake_case
    `document_kind` is INERT as a node property (no reader in the tree reads
    `s.document_kind`; the meter reads `s.documentKind`), so it cannot demote by
    itself — it is refused for EVERY value, because the only thing a snake
    write can do on a document is put the genre on the node in a SECOND place,
    one of them the one every reader ignores: the `raw_state`/`rawState`
    ambiguity class (`_SOURCE_HANDLED`'s "the state in TWO places" note), and a
    `DocumentCreated` payload carrying it DOES reach the genre via
    `_upsert_document`'s `$dk`. One home for the genre, and it is
    `documentKind`.

    ⛔ That last point was the review-cycle-3 defect and it is why the rule is
    "refuse the whole SPELLING on a document" rather than "refuse the demoting
    VALUE": the value-scoped rule ADMITTED `document_kind='brief'` on a
    metered document (creating exactly the second home this docstring refuses)
    while REFUSING `document_kind=None`, the one snake write that cannot
    demote and is the cleanup of that second home. The predicate was inverted
    against its own stated purpose in both directions. The refusal is scoped to
    a METERED document because that is where the genre already has a home; on a
    non-document `:Source` the property is inert and a promotion is how a
    document is minted through these surfaces at all.

    Deliberately NOT in `_DOC_RETIRED_KEYS`: `documentKind` is LIVE (D10 §4.4,
    the genre axis) and a member there is SCRUBBED off every document. On the
    caller surfaces it is refused LOUDLY rather than dropped, because a silent
    no-op would leave the caller believing it wrote. (The DOCUMENT writer,
    `_upsert_document`, instead DROPS a demotion inside its own clause: the
    same statement is the journal replay fold, so it must stay total and must
    not abort a rebuild over a pre-guard record. Its PRODUCERS —
    `EventAPI.add_document` and `sdk._doc_write` — refuse loudly before the
    journal append, via `quota.refuse_document_writer_demotion`, so the drop
    is reached only by a REPLAY. `create_source`/`update_entity` are never
    replay, so they raise here.)
    """
    incoming = [(k, props[k]) for k in ("documentKind", "document_kind")
                if k in props]
    if not incoming:
        return
    # ⛔ FAIL CLOSED over EVERY node the write can reach: `update_entity` writes
    # `MATCH (n:Source {key:$id}) SET n += $p`, which applies to ALL matching
    # nodes, so one non-metered sibling must not be able to answer for a metered
    # one (#5140 review cycle 4 reproduced exactly that: meter 1 -> 0).
    metered = [k for k in stored_kinds if is_metered_document_kind(k)]
    if not metered:
        # Nothing metered stored to demote: a fresh node, a transcript, or a
        # non-document `:Source`. Setting/promoting the genre is allowed.
        return
    stored_kind = metered[0]
    for key, value in incoming:
        if key == "document_kind":
            # The WHOLE spelling, whatever its value — see the docstring.
            raise ValueError(
                f"`document_kind={value!r}` cannot be written to a metered "
                f"document (stored documentKind={stored_kind!r}): the genre "
                f"lives in `documentKind` ONLY, and `document_kind` is an "
                f"inert second spelling that no reader reads. Write "
                f"`documentKind`, which carries the demotion rule (#5140)."
            )
        if is_metered_document_kind(value):
            continue
        if value is None and not none_demotes:
            # `None` is PRESERVE on a passthrough that skips null values
            # (`_persist_extra_props`: "None values are excluded — Cypher null
            # semantics in SET maps are unreliable"), so it writes nothing and
            # is not a demotion. On a `SET n += $p` surface it REMOVES the
            # property, which is, and the caller passes `none_demotes=True`.
            continue
        raise ValueError(
            f"`{key}={value!r}` would move this :Source out of the metered "
            f"`documents` set (stored documentKind={stored_kind!r}; the "
            f"`documents` cap counts "
            f"`{DOCUMENT_METER_PREDICATE}`). `documentKind` is the "
            f"documenthood key, not metadata: it cannot be demoted to "
            f"`transcript`/empty through `{surface}` (#5140 — metering a node "
            f"a caller can un-meter is not a cap). Retarget among metered "
            f"genres is allowed (`report` -> `brief`). To drop a document's "
            f"metering, delete the document; to record that a source became "
            f"unavailable, use `create_source(..., raw_state=...)`."
        )


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


def _distinct_preserving_order(items) -> list:
    """``items`` with repeated entries dropped, the FIRST occurrence kept (#9750).

    The operator write path's input list is a SET, not a bag. `create_operator`
    writes its typed edges and their ``INPUT`` mirrors BY LIST POSITION (``idx``
    positions the EP source slot at 0 and the targets after it), so one edge per
    DISTINCT endpoint is the invariant the graph has to hold — a caller that
    repeats a target must not be given two weighted edges over the same claim.
    `ep.py` builds the factor from the EDGE list, so a repeat appends the same
    claim twice to the input list: it double-counts that claim's EP
    contribution and defeats the "<2 inputs" unary path (measured on this head:
    ``to: ["p2", "p2"]`` → 3 IMPL + 3 INPUT edges for a one-target operator).

    ⛔ Do NOT reach for ``set()`` directly. Order is load-bearing: ``idx`` is
    positional, so re-ordering the list would re-index the factor. First
    occurrence wins, which is the set-union answer without the re-ordering.

    ⛔ Do NOT widen this to the SOURCE slot. ``create_operator(op, a, [a])`` is
    a DELIBERATE shape (the self-support operator, ``idx: 0`` + ``idx: 1`` over
    one id — pinned behaviourally by ``tests/test_6871_operator_input_set.py``),
    so the caller collapses ``target_ids`` among THEMSELVES and never against
    ``source_id``.

    An unhashable entry (a malformed target that another guard owns and names)
    is kept verbatim: this helper decides MULTIPLICITY, never shape, and must
    not turn a later, better-worded refusal into a ``TypeError`` raised here.
    """
    seen: set = set()
    out: list = []
    for item in items:
        try:
            if item in seen:
                continue
            seen.add(item)
        except TypeError:
            pass  # unhashable: kept verbatim for the shape guard to refuse
        out.append(item)
    return out


def _item_dedup_key(item: dict) -> str | None:
    """The effective dedup ``key`` of an ingest bundle item — the SAME value
    ``create_point`` sees after ``_coerce_props`` flattens the item (#6110,
    #8400).

    ``_coerce_props`` gives explicit top-level kwargs precedence over nested
    ``props`` on collision, and that precedence is by PRESENCE, not by
    truthiness: a top-level ``key`` whose value is ``None`` still SHADOWS a
    nested ``props["key"]``. A reader that instead "fell back when the
    top-level value was None" would consult a key the writer never uses, so
    the ingest counter/guard would predict a fold for a write that mints —
    #2892's counter/writer parity broken in the opposite direction (#8400).

    A blank or non-string key is treated as absent (the historic
    content-only path), exactly as ``_resolve_dedup_hit`` treats it.
    """
    if "key" in item:
        val = item.get("key")
    else:
        nested = item.get("props")
        val = nested.get("key") if isinstance(nested, dict) else None
    return val if isinstance(val, str) and val.strip() else None


def _item_prop_at_depth(item: dict, key: str, depth: int) -> tuple[bool, object]:
    """``(declares, value)`` for ``key``, read at the depth the ITEM'S WRITE
    DOOR flattens (#7925 review P2).

    ``_coerce_props`` folds ONE nested ``props`` map per application, and a door
    may apply it more than once. A reader that stops short of the door's depth
    is blind to a key the writer still applies (fail-open → mid-write raise);
    a reader that folds FURTHER than the door refuses bundles the writer
    accepts (over-rejection). Both were measured on this PR, in each direction,
    so the depth is a property of the DOOR and is declared once, beside the
    label map:

      - ``points`` item → 2. ``create_point`` folds once and, on a DEDUP hit,
        relays the residual props to ``update_point``, which folds AGAIN. The
        mint path folds once, so a depth-2 key is refused here that a
        non-dedup mint would have dropped — a deliberate fail-closed
        over-rejection of a pathological spelling, stated rather than silent.
      - ``subject`` / ``object`` / ``event`` → 2. Step 3 calls
        ``create_subject`` / ``create_object`` / ``create_event`` (one fold)
        which relay ``**props`` to ``create_entity`` (a second).
      - ``document`` → 1. Step 3 calls ``create_document``, which folds once
        and relays to the low-level ``_create_entity`` — there is no second
        fold, so a depth-2 reader over-rejects a `:Source` (measured).

    Precedence is PRESENCE, not truthiness, at every level: a key at a shallower
    level shadows the nested one even when its value is ``None``, which is
    ``_coerce_props``' own rule (``props.update`` only fills absent keys).
    """
    cur: object = item
    for _ in range(depth):
        if not isinstance(cur, dict):
            return False, None
        if key in cur:
            return True, cur.get(key)
        cur = cur.get("props")
    if not isinstance(cur, dict):
        return False, None
    return key in cur, cur.get(key)


def _item_effective_status(item: dict, depth: int = 1) -> object:
    """The effective ``status`` of an ingest bundle item — the SAME value
    ``create_point``/``create_entity`` see after ``_coerce_props`` flattens the
    item (#7925 review P1).

    ``_coerce_props`` gives explicit top-level kwargs precedence over nested
    ``props`` on collision, by PRESENCE rather than truthiness: a top-level
    ``status: None`` SHADOWS ``props["status"]``. Mirrors ``_item_dedup_key``
    (#6110/#8400) and ``_check_gated_status`` (#1088) so a Phase-1 reader
    cannot consult a status the writer never uses. The entity terminal guard
    read the top level ALONE, so a bundle-local
    ``{"type":"subject", "props":{"status":"superseded"}}`` was born
    terminal while Phase 1 saw no declaration and let it through — the writer
    then raised mid-write after earlier bundle items had committed (measured;
    the #2422/#7925 partial-mutation class).

    ``depth`` is the number of folds the caller's write door performs — see
    :func:`_item_prop_at_depth`; the default reproduces the one-fold readers.
    """
    declares, value = _item_prop_at_depth(item, "status", depth)
    return value if declares else None


def _item_declared_status(item: dict, depth: int = 1) -> tuple[bool, object]:
    """``(declares, value)`` for an item's ``status`` — PRESENCE, not
    truthiness, with ``_coerce_props``' precedence (top-level wins).

    ``_item_effective_status`` above returns the same VALUE the writer sees,
    but collapses "absent" and "explicitly ``None``" onto ``None``. Those are
    different to the entity writers: an absent ``status`` takes the mint
    default (``"live"``), while an explicit ``None`` is VALIDATED and
    refused (``create_entity(type='subject', status=None)`` raises
    ``Invalid status None``). Phase 1 cannot claim writer agreement without
    the distinction, so it is kept here (#7925 review P3).
    """
    return _item_prop_at_depth(item, "status", depth)


def _item_declares_outdated(item: dict, depth: int = 1) -> bool:
    """True when an ingest bundle item carries the server-managed legacy
    ``outdated`` flag at the depth its write door flattens (#7925 review P1).

    ``_sanitize_props`` refuses the flag on PRESENCE (not truthiness) for every
    Point/entity writer — ``create_point`` (ingest step 2) and the entity
    writers (step 3) alike — so an item carrying it raises at write time, after
    the earlier items committed. ``_check_endpoints`` reads this over EVERY
    point AND entity item, once, in Phase 1 (route- and
    reference-independent — see its docstring), which closes that
    partial-mutation surface for BOTH item writers from ONE Phase-1 authority.
    ``depth`` closes the second-nested spelling the DEDUP relay makes writable
    for a point item — see :func:`_item_prop_at_depth`.
    """
    return _item_prop_at_depth(item, "outdated", depth)[0]


# #7925 review P2: the fold depth is a property of the WRITE DOOR, declared
# beside the label map so the two cannot drift. ``document`` is the exception
# that makes this a map and not a constant: ``create_document`` folds once and
# relays to ``_create_entity``, while the other three relays run ``_coerce_props``
# a second time inside ``create_entity``.
_ENTITY_ITEM_FLATTEN_DEPTH = {"subject": 2, "object": 2, "event": 2,
                              "document": 1}
#: ``create_point`` folds once, then the DEDUP relay ``update_point`` folds again.
_POINT_ITEM_FLATTEN_DEPTH = 2


def _item_flatten_depth(item: dict, section: str) -> int:
    """How many times the item's write door applies ``_coerce_props``."""
    if section == "points":
        return _POINT_ITEM_FLATTEN_DEPTH
    return _ENTITY_ITEM_FLATTEN_DEPTH.get(
        str(item.get("type") or "").strip().lower(), 1)


def _status_is_evaluable(value: object) -> bool:
    """True when a status value can be tested for membership.

    ``create_direct_edge`` evaluates ``status in TERMINAL_EXCLUDED_STATUSES``
    against a FROZENSET. FalkorDB stores array properties, so a node — or a
    bundle item that MINTS one — can carry a list-valued ``status``; that is
    unhashable and the membership test raises ``TypeError`` mid-write, after
    steps 2-3 committed (#7925 review P2, measured). The refusal is applied at
    BOTH authorities: the item-level bundle-wide loop in ``_check_endpoints``
    (an item that would STORE an array — see
    :func:`_status_is_stored_unhashable`) and the two graph-read legs (an
    existing node / name holder that carries one). Reading such a value as
    "not terminal" instead would only move the crash from before any write to
    after the earlier items — the partial-mutation class this gate exists to
    close.
    """
    try:
        hash(value)
    except TypeError:
        return False
    return True


def _status_is_stored_unhashable(value: object) -> bool:
    """True when this value would be STORED on the node AND cannot be tested
    for membership.

    :func:`_status_is_evaluable` alone over-refuses: a NON-persistable value is
    DROPPED by the writers, so the node never carries it and the direct-edge
    check never sees it (pinned by
    ``test_direct_route_object_and_event_status_values_not_over_rejected``).
    Only a PERSISTABLE unhashable value — an array — lands on the node.

    Note the two clauses are independent: ``bytes`` passes
    ``_status_is_evaluable`` (it IS hashable) and is dropped as
    non-persistable, so it is accepted by the first clause rather than the
    second; the non-persistable clause is what accepts ``dict``/``set``/
    ``bytearray``.
    """
    from .projection.entities import _is_persistable_prop_value

    return (not _status_is_evaluable(value)
            and _is_persistable_prop_value(value))


def _item_effective_is_operator(item: dict, depth: int = 1) -> tuple[bool, bool]:
    """``(declares, value)`` for an ingest bundle item's ``is_operator`` —
    top-level OR nested under ``props``, with ``_coerce_props``' PRESENCE
    precedence (a top-level ``is_operator: None`` shadows the nested key),
    mirroring :func:`_item_effective_status` / :func:`_item_declares_outdated`
    (#7925 review P1).

    Why it matters: ``is_operator`` is in none of the handled-prop sets, so the
    writer persists it onto the NODE — and an operator node is not a connection
    endpoint on any route (``create_direct_edge`` and ``_check_endpoint_race``
    both refuse it). Reading only the top-level key left the nested spelling to
    reach the writer, which then aborted mid-write after steps 2-3 had
    committed (measured).

    ``declares`` is separate from ``value`` because a FALSY declaration is
    meaningful: on a name-merged label the extra-props lane is ``SET n +=
    $extra`` on the merged node — LAST DECLARATION WINS — so a later
    ``is_operator: False`` clears the flag the first item (or an existing
    holder) set. An OR over the items would refuse a bundle the writer accepts
    (measured, review round 4).

    ``declares`` means THE WRITER WILL WRITE A BOOLEAN — not "the key is
    present" — or, conservatively, a value that would be operator-shaped if it
    could be written (see the last paragraph). ``_persist_extra_props`` drops ``None`` and any non-persistable
    value (a map, bytes), so such an entry performs NO write and cannot clear a
    flag an earlier item or a holder set; counting it as a declaration made
    Phase 1 pass a bundle whose step 4 then refused the still-operator-shaped
    node (measured, review round 5). The reverse case is the documented
    conservatism: a TRUTHY non-persistable value is refused even though no node
    would carry it — over-rejecting nonsense input in the fail-closed direction
    is the right trade.
    """
    from .projection.entities import _is_persistable_prop_value
    declares, value = _item_prop_at_depth(item, "is_operator", depth)
    if not declares:
        return False, False
    if value is None or _is_persistable_prop_value(value):
        # A persistable value IS written; ``None`` is dropped by the writer, so
        # it performs no write and declares nothing.
        return (value is not None), bool(value)
    # Not persistable, so no write happens either — but keep the fail-closed
    # reading for a truthy value (see the docstring).
    return bool(value), bool(value)


def _item_is_operator(item: dict, depth: int = 1) -> bool:
    """True when the item itself is operator-SHAPED — the single-item view of
    :func:`_item_effective_is_operator`, read at the item's write-door depth.
    The name-merged arms use the ``(declares, value)`` form instead, because the
    writer's outcome is the LAST declaration under the name, not any one item."""
    return _item_effective_is_operator(item, depth)[1]


# #7925: entity-item ``type`` → node label, spelled the way step 3's writer
# ladder spells it (``if etype == "subject" … elif "document"``). ONE
# declaration for the gate's Phase-1 readers — the endpoint label check and the
# bundle pre-scan below — so the two cannot disagree about which items share a
# merged name.
def _endpoint_is_terminal(info: dict) -> bool:
    """True when a resolved endpoint node is TERMINAL for the direct-edge gate.

    ONE spelling for the two graph-read legs (Phase 1 ``_check_endpoints`` and
    Phase 2 ``_check_endpoint_race``), which previously hand-wrote the same
    composition twice (#7925 review round 5). It predicts what
    ``create_direct_edge`` evaluates — ``status in TERMINAL_EXCLUDED_STATUSES``
    (the shared ``live._terminal_expression`` vocabulary) or a set ``outdated``
    flag — and it is only ever asked about a value
    :func:`_status_is_evaluable` admits: an array ``status`` raises ``TypeError``
    in the primitive, so the two call sites refuse it FIRST, with their own
    message, rather than reading it as "not terminal" here.

    Kept beside the gate (not in ``live.py``) because it is a prediction about
    the PRIMITIVE's body, which is untouched by this PR; if
    ``create_direct_edge`` widens or coerces that test, this helper and the two
    guards must move with it (the cross-check is
    ``tests/test_ingest_event_endpoint.py``'s terminal-status pins).
    """
    status = info.get("status")
    return bool(
        (isinstance(status, str) and status in TERMINAL_EXCLUDED_STATUSES)
        or info.get("outdated")
    )


_ENTITY_ITEM_LABELS = {"subject": "Subject", "object": "Object",
                       "event": "Event", "document": "Source"}


def _indefinite(phrase: str) -> str:
    """``a``/``an`` + ``phrase`` — the messages read as sentences, and "got a
    Object/Event endpoint" was both wrong and a wrong diagnosis (#7925)."""
    return f"{'an' if phrase[:1].lower() in 'aeiou' else 'a'} {phrase}"

def _bundle_entity_name_outcomes(
    bundle: dict,
) -> dict[tuple[str, str], tuple[str | None, bool, bool, str | None]]:
    """``{(label, name): (born_status, operator_declared, operator_value,
    operator_item_ref)}`` for the NAME-MERGED entity items in a
    bundle — the *simulated* state the Phase-1 endpoint gate has to answer
    against (#7925 review P1). ``operator_item_ref`` names the item whose
    declaration decides the operator axis, so a refusal can point at the item to
    fix.

    Why a whole-bundle pre-scan and not the item in hand: ``ingest`` writes the
    entity items in step 3 **in bundle order** (connections in step 4), and the
    name merge applies ``status`` **at mint only** — the projection's
    ``_upsert_subject`` / ``_upsert_object`` set ``status`` in ``ON CREATE`` and
    ``ON MATCH`` never touches it. So when no node holds the name yet, the node
    the writer mints is the one the FIRST item naming it describes, and every
    later same-name item merges onto that node and keeps its status. The
    effective terminality of the endpoint is therefore a property of the ordered
    items PLUS the graph, never of the one item a connection happens to
    reference — which is exactly the shape a name-merge bundle exists for.

    The server-managed ``outdated`` flag is deliberately NOT an axis here: it
    is order-INDEPENDENT. ``_sanitize_props`` refuses it on PRESENCE for every
    entity writer, so an item carrying it raises in step 3 whichever item it
    is, whether or not a holder exists, and whether or not any connection
    references it — a name simulation therefore adds nothing, and
    ``_check_endpoints`` refuses the flag bundle-wide, ONCE, over every point
    and entity item on every route (#7925 review P1/P2 — one arm, both item
    sections).

    ``operator_*`` is the other axis and follows the WRITER, which is neither
    "the item in hand" nor an OR over the name: ``is_operator`` rides the
    extra-props lane, whose clause is ``SET n += $extra`` on the MERGED node, so
    the LAST item under the name that declares it decides — a later
    ``is_operator: False`` clears the flag an earlier item set, and an earlier
    ``True`` taints the node every ref resolving to that name will see (and can
    mark a pre-existing holder). Both directions were measured: the OR refused
    bundles the writer accepts (review round 4), and the item-only read let
    tainted ones through (round 2).

    Only ``Subject``/``Object`` merge by name. An ``Event`` is append-only (its
    own declared ``status`` is the whole story, and the guard reads it from the
    item), and a ``document`` is a ``:Source`` — neither can be addressed here.
    """
    outcomes: dict[
        tuple[str, str], tuple[str | None, bool, bool, str | None]] = {}
    for item in bundle.get("entities") or []:
        if not isinstance(item, dict):
            continue
        elabel = _ENTITY_ITEM_LABELS.get(
            str(item.get("type") or "").strip().lower())
        name = item.get("name")
        if elabel not in ("Subject", "Object") \
                or not isinstance(name, str) or not name:
            continue
        _depth = _item_flatten_depth(item, "entities")
        key = (elabel, name)
        first = key not in outcomes
        born, op_declared, op_value, op_ref = outcomes.get(
            key, (None, False, False, None))
        if first:
            # The MINT: what the node carries if no holder exists. A first item
            # that declares nothing is decisive too — a later item's declared
            # status is NOT applied by the merge, so it must not become `born`.
            born = _item_effective_status(item, _depth)
        declares_op, declares_op_value = _item_effective_is_operator(item, _depth)
        if declares_op:
            # LAST declaration wins (`SET n += $extra` on the merged node).
            op_declared, op_value = True, declares_op_value
            op_ref = item.get("ref")
        outcomes[key] = (born, op_declared, op_value, op_ref)
    return outcomes


# ── ULID validation (Issue #52) ──
# Canonical format (from tortoise/ids.py): <timestamp-hex>-<uuid12>
_ULID_RE = re.compile(r"^[0-9a-f]+-[0-9a-f]{12}$")
# Standard Crockford base32 ULID (26 chars) — recognized as valid
_CROCKFORD_ULID_RE = re.compile(r"^[0-7][0-9A-HJKMNP-TV-Z]{25}$", re.IGNORECASE)


def _is_ulid(s: str) -> bool:
    """Return True if *s* matches a valid ULID format (canonical or Crockford)."""
    return bool(_ULID_RE.match(s) or _CROCKFORD_ULID_RE.match(s))


# #4106: a recorded session time is only a DATE when it leads with a calendar
# date in either the ingest producer's real format (``YYYY/MM/DD``, e.g.
# ``2023/05/20 (Sat) 03:29`` — measured on the frozen dataset: 23,867/23,867
# values are slash-form, zero are ISO) or ISO 8601 (``YYYY-MM-DD``). Both
# normalise to ``YYYY-MM-DD``; anything else is ABSENT. Mirrors the canonical
# ``subgraph_render._parse_date`` rule (its module imports ``tools.*``, which
# the SDK core must not), and mirrors ``assembly``'s sentinel constant for the
# same reason.
_ISO_DATE10_RE = re.compile(r"^(\d{4})[-/](\d{2})[-/](\d{2})")
#: The v2-lane undated sentinel (``tools/longmem_eval/ingest.UNDATED_SENTINEL``)
#: — a POSITIVE date value that means "no date". Rendering it would put a
#: fabricated date in front of a temporal question.
_UNDATED_SENTINEL = "1970-01-01T00:00:00Z"

#: "the caller did not pass this field" — the ONE sentinel for a partial
#: update (#4282 phase 2.3). A plain ``None`` default cannot carry this
#: meaning: for ``update_memory_graph``'s ``recording``, ``None`` is a
#: MEANINGFUL value ("clear the override → inherit the org default"), so
#: conflating it with "unspecified" would clear an override on every call
#: that only meant to rename the graph.
class _Unset:
    """The type of :data:`_UNSET` — so the parameter's contract stays narrow.

    Annotating the parameter ``bool | None | object`` would collapse to ``object``
    (a union with ``object`` IS ``object``), which silently drops the contract: a
    caller could pass ``recording="false"`` and the registry would store a STRING
    that reads back truthy, leaving recording ON.

    ⛔ The ANNOTATION is not the enforcement — ``sdk.py`` is
    ``from __future__ import annotations``, so at runtime every annotation is a
    string, and the only static consumer (``mypy tortoise/``) disables
    ``arg-type``. ``update_memory_graph`` therefore REFUSES a non-``bool``
    ``recording`` with a ``TypeError`` before any write, so the accepted set really
    is ``{True, False, None, _UNSET}``. Same rule, same wording as the REST twin
    (``hosted_api.GraphRecordingPatch._strict_bool``: *"reject anything that is not
    an actual bool"*), because ``_apply_graph_recording_override`` resolves the
    stored value with ``bool(override)`` — a stored ``"false"`` reads back ON.
    """

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return "<unset>"


_UNSET: _Unset = _Unset()


def _iso_date10(value: object) -> str:
    """``value``'s ``YYYY-MM-DD`` calendar date, else ``""`` (unknown).

    Accepts ``YYYY/MM/DD…`` and ``YYYY-MM-DD…`` (normalised to the dashed
    form), and treats the undated sentinel — in every ISO spelling of its
    ``1970-01-01`` date — as absent. Never returns a truncated non-date: a
    wrong date is worse than no date (#4106).
    """
    s = str(value).strip() if value else ""
    if not s or s == _UNDATED_SENTINEL:
        return ""
    m = _ISO_DATE10_RE.match(s)
    if not m:
        return ""
    date = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    # the sentinel's date itself (any offset spelling) is "no date"
    return "" if date == _UNDATED_SENTINEL[:10] else date


# #1516: entity ids minted by _entity_name_id / create_entity are PREFIXED
# (label[:3] + '-' + sha256[:26], e.g. ``sub-<hex26>`` / ``obj-<hex26>``).
# These are IDs, not names — a guard that only recognizes bare ULIDs treats
# them as names and runs the stub-creating keyword fallback.
#
# #3586: the digest spelling is deliberately NOT pinned to ``{26}``/lowercase.
# An opaque id's spelling is a property of the MINTER, not of the value, so a
# spelling the regex does not anticipate is not thereby a name: epic #2835
# Stage 3 mints new opaque ids, and a minted spelling this regex rejected was
# silently treated as a NAME and minted as a Subject whose name AND id are the
# identifier (`_mint_subject_stub`, projection/edges.py). The predicate is
# therefore LENIENT about casing and digest length — ``prefix`` + a 16..64 hex
# digest covers every id spelling the SDK mints or can plausibly mint — and it
# is used ONLY for the negative case at the about* seam (may an UNRESOLVED
# value be treated as a name?), never to decide that a value IS an id: that is
# provenance, decided by resolution (`_wire_about_value`). Over-refusal there
# is fail-closed (one warning, no node); the reverse mints junk a caller cannot
# distinguish from data.
#
# ``_is_canonical_entity_id`` is the STRICT, HARD-REJECTION predicate. The two
# are NOT interchangeable, and widening this one is not free: its consumer
# (`_check_refs`) fails the WHOLE bundle closed on a hit, so a digest it newly
# rejects is a previously-valid ref that stops ingesting. Keep it pinned to the
# shapes the minting surfaces actually emit — a bare ULID, or
# ``label[:3]-<sha256[:26]>`` (`_entity_name_id`). A spelling no minter can
# produce (``pr-<hex16>``) cannot shadow a real node, so rejecting it buys no
# safety and breaks a live ingest.
_ENTITY_ID_RE = re.compile(r"^[a-z]{2,8}-[0-9a-f]{16,64}$", re.IGNORECASE)
_CANONICAL_ENTITY_ID_RE = re.compile(r"^[a-z]{2,3}-[0-9a-f]{26}$")


def _is_entity_id(s: str) -> bool:
    """Return True if *s* is shaped like an entity handle (an opaque id), not
    a name: a bare ULID (canonical or Crockford) OR ``prefix-<hex digest>``.

    NOT a provenance test (#3586): a minted id and a legacy
    ``obj-<sha26(name)>`` alias are shape-identical, so shape alone cannot say
    which node — if any — a value addresses. Callers that need that answer MUST
    resolve the value first (``_wire_about_value`` does) and use this predicate
    only for the negative case: may an UNRESOLVED value be resolve-or-minted as
    a name? See ``_ENTITY_ID_RE`` for why the spelling range is lenient.

    Boundary: a NAME shaped like ``[a-z]{2,8}-<16..64 hex>`` (e.g.
    ``ab-0123456789abcdef``) is classified as an id — vanishingly rare for
    human names, and now REPORTED rather than silently dropped by the about*
    seam."""
    return bool(_is_ulid(s) or _ENTITY_ID_RE.match(s))


def _is_canonical_entity_id(s: str) -> bool:
    """Return True if *s* is shaped like an id the minting surfaces EMIT — a
    bare ULID or ``label[:3]-<sha256[:26]>`` (``_entity_name_id``) — and so
    could address (and shadow) a real node.

    The HARD-REJECTION predicate (``_check_refs`` fails the whole bundle closed
    on a hit). Do NOT widen it to anticipate a future id format: that trades a
    live, previously-accepted ingest for protection against a spelling no
    minter produces and which therefore cannot shadow anything. The lenient
    about*-seam predicate is ``_is_entity_id``."""
    return bool(_is_ulid(s) or _CANONICAL_ENTITY_ID_RE.match(s))


def _wire_about_value(proj, source_id: str, value, rel: str) -> bool:
    """Wire ONE ``about*`` prop of a freshly-written Event; True iff an edge
    was created.

    **Provenance first (#3586).** ``create_about_edge`` resolves the value
    against the id/eventId index WITHIN the label *rel* implies — the surfaces
    a HANDLE comes from — so a value that resolves to a node of that label is
    an id, whatever its spelling, and a value that resolves only to a
    DIFFERENT label is refused here and falls through to the name path below
    (it must not steal the rel from its intended target). Its result previously
    went unread and a shape test decided instead, which took the wrong branch in
    both directions:

    * a resolvable value whose spelling the shape test did not recognize (a
      minted id under a new format) fell through to the name fallback, which
      found no node NAMED after the id and minted a stub — so the edge landed
      on a junk Subject, not on the entity the caller named;
    * an unresolvable id-shaped value fell through to that same fallback, whose
      ``_mint_subject_stub`` made the identifier BOTH a name and an id;
    * an unresolvable recognized-id was a silent no-op — no edge, no node, no
      signal — which is how a cached legacy alias vanishes.

    So: only a value that resolves to nothing can be a name, and only a value
    that is not handle-shaped may then be resolve-or-minted as one. An
    unresolvable handle is a stale/unknown reference and is REPORTED, never
    minted — the #1917 precedent (a long id that does not resolve is never
    stubbed; warn and skip, projection/edges.py) applied to the about* seam.

    epic #2835 Stage 3: while legacy ``obj-<sha26(name)>`` aliases remain
    resolvable they are shape-identical to minted ids, so the alias→id lookup is
    the REQUIRED resolution branch here — extend the ``create_about_edge`` hop
    below, never this shape predicate.
    """
    from .projection.edges import STRUCTURAL_REL_LABELS
    # The rel names its target label; scope the resolution to it so a value that
    # collides with ANOTHER label's id/eventId cannot win (review of #3586 —
    # `aboutSubject="acme"` landing on the Event whose eventId is "acme" and
    # dropping the intended Subject).
    target_label = STRUCTURAL_REL_LABELS.get(rel)
    if proj.create_about_edge(source_id, value, rel, target_label=target_label):
        return True
    if not isinstance(value, str):
        return False
    if _is_entity_id(value):
        _logger.warning(
            "create_entity: %s %r is handle-shaped but does not resolve to a "
            "%s node — edge not created and no stub minted (an identifier "
            "must not become a name, #3586)", rel, value,
            target_label or "node")
        return False
    proj._create_about_edges(source_id, value)
    return True


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# #2335 WI-2: the "file a report" hook target — the bug_report.yml template
# new-issue URL (the #2409 inbound-channel default; the machine-addressable
# hook target decision lives in #2409). Surfaced on the resp ONLY when a
# capture errored, so a customer/agent can file a structured report. The
# report body carries structured fields + the harness's own logs (never the
# reporter's narrative as instructions — the #2409 killer-combo guard).
REPORT_HOOK_URL = (
    "https://github.com/daniel-ospina/tortoise/issues/new?template=bug_report.yml")


# #2335 WI-2: the customer-facing error contract. The extractor's error
# strings leak internal jargon (stage names, exception types) to the capture
# resp. This mapper converts a RAW error string to a {headline, diagnostics}
# pair AT THE RESP BOUNDARY ONLY — headline = plain-language reason (no stage
# names / exception types), diagnostics = the raw string (TypeName prefix
# preserved for P2's fatal-4xx classification). Additive: resp["errors"] stays
# a list of strings (the pinned #1529 contract); the raw detail rides a NEW
# sibling resp["diagnostics"] list. The eval lane and internal consumers
# (out["errors"], meta["errors"], write-path receipts) are UNTOUCHED.
# key = raw error string (the exact-match contract); value = the human
# headline. Review (PR #2473): dropped the dead ``diagnostics_suffix`` values
# — diagnostics is the RAW string itself (returned verbatim by
# _capture_resp_error_split), so suffix copies would be drift-hazard
# duplicates of the keys.
_CAPTURE_ERROR_CONTRACT: dict[str, str] = {
    "S2 output partial — truncated tail dropped (embed list incomplete)": (
        "The first pass of extraction produced an incomplete "
        "result. Retry the capture — the retry will re-attempt it."),
    "S4 output partial — truncated tail dropped (embed list incomplete)": (
        "The final review pass produced an incomplete result. "
        "Retry the capture — the retry will re-attempt it."),
    "no embed list produced (S2/S4 empty) — nothing to embed": (
        "Extraction could not find anything to store from this "
        "session. Retry the capture, or try again with more "
        "content."),
}


# Stage-failure families carry LIVE exception text ("S4 failed:
# ValueError: ..."), so exact-match is impossible — match on the stable
# prefix (the plan D7 Step-1 inventory). Headlines name what happened in
# plain words + the TRUE recovery ("Retry" — WI-2b makes retry real for
# failed sessions); raw detail (TypeName + message) stays in diagnostics.
_CAPTURE_ERROR_PREFIX_CONTRACT: list[tuple[str, str]] = [
    ("S1 chunk failed: ",
     "Part of the extraction failed partway through. Retry the capture — "
     "the retry will re-attempt it."),
    ("S2 failed: ",
     "The first pass of extraction failed partway through. Retry the "
     "capture — the retry will re-attempt it."),
    ("S4 failed: ",
     "The final review pass failed and the earlier result was kept as-is. "
     "Retry the capture — the retry will re-attempt it."),
    ("S5 failed: ",
     "A consolidation pass after extraction failed. Retry the capture — "
     "the retry will re-attempt it."),
]


# "{n}/{m} S1 chunks failed" — the S1 SUMMARY line (extractor_v2:4152) has a
# variable count prefix, so it needs a suffix pattern, not a prefix. Its
# per-chunk sibling ("S1 chunk failed: ...") is already prefix-mapped; the
# summary would otherwise leak the "S1" stage name on the very provider-
# outage path the contract targets (review second-model P2).
_S1_CHUNK_SUMMARY_SUFFIX = " S1 chunks failed"


def _capture_error_to_human(raw: str) -> str:
    """Map a raw capture error string to its human headline at the resp
    boundary. Unmapped errors pass through unchanged (fail-safe — a new
    extractor error must never be hidden)."""
    if raw.endswith(_S1_CHUNK_SUMMARY_SUFFIX):
        return ("Part of the extraction failed partway through. Retry the "
                "capture — the retry will re-attempt it.")
    for _raw, headline in _CAPTURE_ERROR_CONTRACT.items():
        # Exact equality (review second-model P3): the contract keys are
        # FULL error strings; substring `in` could misclassify a future
        # compound error that embeds one of them. The raw strings arrive
        # verbatim from the extractor, so equality is the correct test.
        if raw == _raw:
            return headline
    for prefix, headline in _CAPTURE_ERROR_PREFIX_CONTRACT:
        if raw.startswith(prefix):
            return headline
    return raw


def _capture_resp_error_split(errors: list[str]) -> tuple[list[str], list[str]]:
    """Split capture errors for the resp: headlines for resp["errors"]
    (strings, per #1529 — the customer-facing message) + the RAW strings
    (TypeName preserved) for resp["diagnostics"]. An unmapped error is its
    own headline AND its own diagnostic (fail-safe — never hidden)."""
    return ([_capture_error_to_human(e) for e in errors],
            list(errors))


def _attach_source_change_notices(resp: dict, meta: dict) -> None:
    """#5516: surface a capture's write-time source-change notices on its receipt.

    The v2 point loop discards each ``create_point`` result, so the notices it
    produced are aggregated into ``meta["source_change_notices"]``; both capture
    lanes call this to put them on the receipt the WRITER sees (otherwise the
    notice would be written-but-unread — the #4041 class).

    Additive by contract: the key is present ONLY when a Source actually
    changed, so an ordinary capture's receipt is unchanged. ONE home for the
    condition — the SDK and hosted receipts are kept byte-identical by
    contract, and duplicating the ``if`` in each is exactly how such a mirror
    rots silently.
    """
    notices = (meta or {}).get("source_change_notices")
    if notices:
        resp["source_change_notices"] = notices


def _emit_capture_observation(*, session_id: str, lane: str, mode: str,
                              turns: int, meta: dict) -> None:
    """#2335 WI-1d: the structured observation leg — ONE JSON line per
    capture with the size/diagnostic fields, so a GO event (>=2-chunk +
    double-residual vs the effective escalation ceiling) is DIAGNOSABLE from
    the logs when the self-surfacing failure fires. Emitted at the shared
    resp/effective-mode assembly. NOTE the mode COVERAGE is v2/m2/replayed/
    error/no-provider/extraction-disabled — an "empty" line can never fire here: the empty/blank conversation
    gate RETURNS before the Session MERGE + shared emit point on both lanes
    (no Session is written, so there is no capture to observe; the empty
    population is not a GO candidate). Same for the 402/turn-cap raise paths
    (their records live in the raise-site log lines, Task 3 — never reach
    this emit point). On BOTH
    lanes (sdk + hosted call this with their own ``lane`` tag).

    Fields: session_id / lane / mode / turns / chunks / edus / per-seam max
    out-token (recovered-case max semantics per the #2408 Task-4 handoff:
    max(sX_out_tokens, truncation_completion_tokens_sX) — base in the
    truncation key, escalated final list in the out-token key) / error_census.
    ``meta["stats"]`` carries no ``llm`` roll-up on replayed/M2 (no
    extractor_v2 telemetry) — ``{}`` on a replay and on a zero-call M2
    capture, ``{"unattributed": N}`` on an M2 capture that issued N
    completions (#3824). The line still fires with the mode + turns (a
    replay/no-op is observable).
    """
    st = meta.get("stats") or {}
    rec = st.get("recovery") or {}
    out: dict = {
        "capture_observation": True,
        "session_id": session_id,
        "lane": lane,
        "mode": mode,
        "turns": turns,
    }
    # absent-when-no-data: only present fields are reported (a tokenless
    # mock / replay must not fabricate a measured 0).
    if st:
        chunks = st.get("chunks")
        if chunks is not None:
            out["chunks"] = chunks
        edus = st.get("edus")
        if edus is not None:
            out["edus"] = edus
        for seam in ("s2", "s4"):
            tok = rec.get(f"{seam}_out_tokens")
            trunc = rec.get(f"truncation_completion_tokens_{seam}")
            vals = [v for v in (tok, trunc) if v]
            if vals:
                out[f"{seam}_max_out_tokens"] = max(vals)
        if st.get("s4_full_rescues"):
            out["s4_full_rescues"] = st["s4_full_rescues"]
        ec = meta.get("error_census")
        if ec:
            out["error_census"] = ec
    _logger.info("capture_observation %s", _json.dumps(out, sort_keys=True))


def _session_capture_event_id(session_id: str) -> str:
    """Deterministic sessionCaptured Event id (W5 Phase F #2104).

    Content-addressed from the session_id (``ev_<sha256 hex>`` — the same
    ``ev_`` prefix family as the derived-commit decision/issue event ids),
    so two concurrent FRESH-session captures of ONE session_id converge on
    a SINGLE Event node: the Event projection MERGEs on ``eventId``
    (``_upsert_event`` plain path), so the second concurrent writer's mint
    is an idempotent no-op — never a duplicate node and never a
    duplicate-key error. This closes the #1727 TOCTOU residue (two
    concurrent POSTs both observing ``session_existed=False`` and minting
    two fresh-ULID Events): sequential retries already converged via the
    session_existed replay skip (0 new nodes); the deterministic id makes
    the CONCURRENT race idempotent too. Shared by the hosted impl
    (byte-parity). Distinct session_ids hash to distinct ids (the
    session_id is the idempotency key — one Event per session).
    """
    digest = hashlib.sha256(
        f"sessionCaptured:{session_id}".encode()).hexdigest()[:62]
    return f"ev_{digest}"


def _capture_minted_ids(extracted: list[dict]) -> list[str]:
    """Ids of the points THIS capture actually minted (W5 Phase D #2104).

    A dedup-folded entry (``dedup=content_hash_hit``/``rephrase_linked``)
    resolved to an EXISTING node whose provenance + EP calibration belong to
    its original ingest — re-stamping/re-calibrating on every re-ingest
    would clobber the first session's single-``eventId`` provenance and
    churn the canonical's EP.  The provenance stamp and the ingest EP pass
    must therefore gate over the MINTED subset, never raw ``extracted``.
    Shared by the hosted impl (byte-parity).
    """
    from tortoise.write_verb import DEDUP_NEW
    return [p["id"] for p in extracted
            if p.get("dedup", DEDUP_NEW) == DEDUP_NEW and p.get("id")]


#: The §17.1 lane the capture provenance stamp writes under (#9350).
#:
#: Before this, the stamp was a raw write at the projection's handle with NO
#: record, which #9206's rule forbids declaring ("a declaration that blesses an
#: unjournalled cache write is the #9068 defect") and which a journal-only
#: rebuild silently reverted for any minted Point that is never promoted. The
#: stamp is now journalled FIRST and the write declares this lane.
#:
#: A stable literal ON PURPOSE: `tests/test_9350_capture_provenance_journalled.py`
#: reads it back AT the write handle (`_CACHE_WRITE_AUTHORIZED`), the same way
#: `test_9079_ep_epoch_declared.py` pins the graph-metadata lane, so a rename is
#: a red test rather than a silent relabelling.
_CAPTURE_PROVENANCE_LANE = "capture.provenance-stamp"


def _stamp_capture_provenance(sdk, proj, ids, *, event_id, session_id,
                              harness, ingested_at, statement, what,
                              params=None) -> bool:
    """Journal the capture provenance, then write it under its own lane.

    THE STAMP DOOR (#9350): one ``PointRevised`` per id is appended FIRST,
    then the cache write runs inside
    ``_live_write_scope(_CAPTURE_PROVENANCE_LANE)`` — the ordering every live
    door in this module takes (§17.1,
    `docs/architecture/STORAGE-ARCHITECTURE.md`). The append is IN THIS BODY
    rather than in a helper on purpose: the §17.1 census
    (`tests/test_9124_declared_live_doors.py`) reads one function at a time and
    refuses a lane declaration whose own body appends no record — a door whose
    record lives behind a call edge is a door the gate cannot check.

    ONE RECORD PER ID, not one batched record: ``PointRevised`` is id-keyed
    (``_revise_point`` and the pure fold both address a single Point), so a
    batched payload would be a record NO fold could apply — a carrier with no
    fold is the #3299 class. This is also why a minted Point's provenance
    survives a journal-only rebuild even when the Point is never promoted: the
    four fields are not in its ``PointAdded`` snapshot (that was taken at
    extraction time, before the stamp).

    ``statement`` is the caller's own SET (the three call sites differ in the
    node variable and in their no-clobber WHERE guards, and those guards are
    the reason the statement is passed rather than built here); ``params`` adds
    any guard-specific parameters beside the standard five.

    Returns ``False`` when a CONFIGURED journal refused an append, so the
    caller can skip the cache write: the derived store must never lead the
    truth (§17.1). ``True`` otherwise, including the journal-less lane — there
    is nothing to commit to there, and nothing a replay could restore. The
    caller does not inspect the value: the refusal is already disclosed to the
    customer, because ``_emit_event`` counts it on
    ``_journal_write_failures`` and the capture receipt carries the #4240
    delta warning (both lanes).
    """
    if not ids:
        return True
    if "IS NULL" in statement and "$stamp_ids" not in statement:
        # #9350 review (P2-1): a GUARDED stamp's ``$ids`` is the caller's CLAIM
        # predicate, not the set that WOULD be journalled. Without the narrow
        # clause the WRITTEN set is not a subset of the JOURNALLED set, so an id
        # that became eligible between the pre-read and the write is stamped
        # with no record — the §17.1 window this change exists to close.
        #
        # ⛔ The check runs BEFORE the appends (review round 3): raising after
        # them left the journal holding records for a write that was then
        # skipped — `rebuild()` would apply provenance the capture never wrote,
        # the mirror of the defect this issue removes. `_recorded` is computed
        # first because nothing here depends on the append.
        raise ValueError(
            f"a guarded stamp must narrow its write to $stamp_ids ({what})")
    # ONE record per id, for the ids a record CAN name. The SAME filtered list
    # binds the write (#9350 review): the record loop already skipped a non-str
    # / empty id, so binding the raw list would let the write touch an id the
    # loop had no record for — the unjournalled shape, one malformed id away.
    _recorded = [pid for pid in ids if isinstance(pid, str) and pid]
    # ONE RECORD PER ID, appended UNCONDITIONALLY for every id before the single
    # cache write below. A comprehension rather than a `for` loop ON PURPOSE:
    # the §17.1 census derives "the record precedes the write" from the AST, and
    # an append nested one control-flow level deeper than the write it covers
    # cannot be proven to cover it (the write would be reachable on a branch
    # with no record) — `tests/test_9124_declared_live_doors.py::
    # test_every_live_declared_door_is_journal_first` refuses that shape. The
    # comprehension also states the property directly: every id is emitted,
    # then the write runs once.
    appended = [
        sdk._emit_event(
            "PointRevised", id=pid,
            eventId=event_id, source_session=session_id,
            source_harness=harness, ingested_at=ingested_at)
        for pid in _recorded
    ]
    if any(ok is False for ok in appended):
        # A configured journal REFUSED the record: do NOT write the cache. The
        # write is the thing the record describes, so proceeding would leave
        # the derived store ahead of the truth with no repair path (§17.1
        # window (a)). `_emit_event` has already logged and counted it (#4240).
        return False
    bound = {"ids": list(_recorded), "eid": event_id, "sid": session_id,
             "harness": harness, "ing": ingested_at, **(params or {})}
    if "$stamp_ids" in statement:
        # #9350: a GUARDED stamp's ``$ids`` is the caller's CLAIM predicate (it
        # arrives through ``params``), so it is NOT the set this call just
        # journalled — binding the write to it alone would let an id that became
        # eligible between the pre-read and the write be stamped with NO record
        # (the #9068 shape this change exists to remove). ``$stamp_ids`` is bound
        # AFTER ``params``, so no caller can widen it: the statement's
        # ``AND <node>.id IN $stamp_ids`` makes the WRITTEN set a subset of the
        # JOURNALLED set by construction. An id that appeared in between is left
        # for the next pass rather than written unrecorded.
        bound["stamp_ids"] = list(_recorded)
    with _live_write_scope(_CAPTURE_PROVENANCE_LANE):
        sdk._graph_write_with_retry(
            lambda: proj.g.query(statement, params=bound), what=what)
    return True


def _capture_stamp_targets(sdk, proj, statement: str, params: dict,
                           what: str) -> list[str]:
    """The ids a guarded provenance stamp statement WOULD match, read first.

    The operator and retry-heal stamps carry ``eventId IS NULL`` no-clobber
    guards, so the set they write is not known before the write. #9350 needs
    one ``PointRevised`` per written id BEFORE the cache write, so the ids are
    read with the SAME predicate the write uses (``statement`` is the shared
    prefix of both, in the caller) — one extra index-served read per guarded
    statement, on the operator / retry paths only.

    A stale read is harmless to the WRITE: the write keeps its own guard, so an
    id stamped in between simply does not match and nothing is clobbered.

    ⚠ It is NOT invisible to the RECORD, and the difference is stated rather
    than papered over: the ``PointRevised`` for such an id was already appended
    from THIS read and carries THIS capture's four values, while the node now
    holds the other writer's. So a journal-only rebuild takes this capture's
    ``eventId``/``ingested_at`` for that id until the next record for it — a
    window bounded to attribution fields on an id this capture was already
    stamping, and unclosable by re-ordering (the record precedes the write by
    design). The inverse direction is benign: an id that becomes eligible
    between read and write is not healed on this pass (``if not ids`` skips the
    statement), which is what the pre-#9350 code did for the whole set.

    The read rides ``_graph_write_with_retry`` (#7405): it is part of the same
    stamp path, and a transient graph-replaced abort on the READ would otherwise
    lose the whole stamp — the #2552 ``operator_counts == {}`` signature the
    retry funnel exists to prevent, one query earlier than before.

    ⛔ ``statement`` must be READ-ONLY, and that is enforced here rather than
    trusted to the callers: the §17.1 census declares this function as an
    unresolved *read*, and a statement that writes would let an unjournalled,
    lane-less write ride a declaration that says "reads" — the #9350 review's
    P2-2 (measured: a ``SET`` smuggled into a caller's prefix left the arming
    gate reporting a fully-declared surface).
    """
    from .projection import _statement_writes

    if _statement_writes(statement):
        raise ValueError(
            "_capture_stamp_targets reads the ids a stamp WOULD write, so its "
            f"statement must be read-only; refusing a writing one ({what})")
    result = sdk._graph_write_with_retry(
        lambda: proj.g.query(statement, params=params), what=what)
    rows = getattr(result, "result_set", None) or []
    return [r[0] for r in rows if r and isinstance(r[0], str)]


def _capture_ep_target_ids(extracted: list[dict], proj) -> list[str]:
    """Ids the ingest EP pass must calibrate for one capture (Phase D).

    Normally the MINTED ids (every minted claim is promoted + calibrated by
    its own ingest pass).  A FOLDED-ONLY ingest (every claim resolved to an
    existing node) still needs a FIRST-TIME calibration when the canonical
    never got one — its own ingest's EP pass failed fail-open and left it
    UNCALIBRATED — otherwise the point stays uncalibrated forever (a folded
    re-ingest would never re-run the pass).  Calibration is decided by the
    EP MARKERS alone (``posterior_alpha IS NULL AND ep_alpha IS NULL``); the
    point's ``status`` is deliberately NOT part of the selector.  A folded
    EXPLICIT draft is intentionally left uncalibrated: #1088 never
    retro-promotes a folded id, and the EP pass excludes drafts
    (``include_draft=False``).  An uncalibrated draft (markers null) is
    already nominated by the markers arm, so a ``status='draft'`` disjunct
    could add ONLY drafts that already carry EP markers — already
    calibrated, so nothing to refresh.  A canonical that is live +
    calibrated is NEVER re-calibrated (no EP churn on re-ingest).
    Folded ids are EP/dirty targets ONLY — never promotion targets (the
    caller passes the MINTED ids to ``_apply_capture_ingest_ep``, #1088).
    Shared by the hosted impl (byte-parity).
    """
    from tortoise.write_verb import DEDUP_NEW
    minted = _capture_minted_ids(extracted)
    if minted:
        return minted
    folded = [p["id"] for p in extracted
              if p.get("id") and p.get("dedup", DEDUP_NEW) != DEDUP_NEW]
    if not folded:
        return []
    # #1088 review P2: the selector keys on the EP MARKERS, never on status.
    # A folded explicit draft is never promoted (only minted ids are, above)
    # and the EP pass excludes drafts (``include_draft=False``). An
    # uncalibrated draft (markers null) is already nominated by the markers
    # arm, so a ``status='draft'`` disjunct could add ONLY drafts that
    # already carry EP markers — already calibrated, so nothing to refresh.
    rows = proj.g.query(
        "MATCH (n:Point) WHERE n.id IN $ids AND "
        "n.posterior_alpha IS NULL AND n.ep_alpha IS NULL "
        "RETURN n.id",
        params={"ids": folded},
    ).result_set
    return [r[0] for r in rows]


@phased("belief")
def _apply_capture_ingest_ep(sdk, claim_ids: list[str], *,
                             promotion_ids: list[str],
                             operator_ids: list[str],
                             warn=None) -> None:
    """W5 Phase C (#2104, indicator 3): EP-on-ingest for one capture.

    ROOT CAUSE (verified): capture wrote the extracted claims via the #131
    draft default (pre-#1088, when a no-status create defaulted to draft)
    and wired their IMPL/NAND operator topology as draft
    (#780 extraction operators, ``promote_source=False``); EP's BFS
    expansion excludes draft subgraphs (``include_draft=False`` default), so
    the ingest EP pass could never calibrate the captured claims
    (``dream()`` total_affected 0 / coverage 0.0 / ``has_ep`` False —
    structurally, until the claims are EP-able).

    FIX — capture-scoped ONLY (the #780 draft-operator semantics of
    NON-capture extraction paths, EP semantics, and global dream routing are
    all untouched). ``promotion_ids`` (REQUIRED) is the set of CLAIM points
    this call may PROMOTE; the EP/dirty pass still runs over the full
    ``claim_ids``. Callers pass the capture's MINTED ids (see
    ``_capture_minted_ids``) so a FOLDED, pre-existing canonical — which an
    agent may have explicitly created draft — is calibrated if it needs it
    but never retro-promoted (#1088). ``operator_ids`` (REQUIRED) is the set
    of operator Points THIS capture created — a folded-only capture mints no
    claim (``promotion_ids == []``) yet can still wire a new operator, and
    keying the operator-promotion arm on claims alone left that operator
    draft and the folded claim's first calibration unable to propagate
    through it (#4936's create-then-rekey class):
      1. every MINTED extracted (non-episodic) claim of THIS capture is
         promoted draft->live (the DM-2/§4.4 status branch: draft -> live on
         the capture write path; the episodic turn stream STAYS draft — it is
         the turn stream, not beliefs);
      2. the capture's operator topology (IMPL/NAND operator Points with an
         edge to a captured claim) is promoted with its claims — a live
         claim wired through a draft operator stays EP-inert (the #780
         live-only selector never picks the draft operator), so claim-only
         promotion cannot calibrate wired claims (verified empirically);
      3. the claims are marked dirty (write-trigger, graph-persisted #1163)
         so the bounded ingest pass below has a window to refresh (the v2
         write path already marked via create_point; the M2 fold path does
         not — the uniform mark here covers both lanes);
      4. a BOUNDED ingest EP pass runs — mode="local" (write-triggered
         refresh over the dirty roots, bounded by construction — never a
         full-graph pass), require_calibration=False (EP topology on fresh
         capture content), warm_start=False (from-scratch — no cached-edge
         censoring for freshly promoted claims).

    Fail-open (P1 #1529 posture): a promotion or EP hiccup never fails a
    committed capture — every failure surfaces as an additive warning via
    ``warn`` (never a raise after partial writes). Shared by
    ``sdk.capture_session`` and hosted ``_capture_session_impl`` so the two
    capture surfaces can never drift (byte-parity).
    """
    # No-op only when there is NOTHING to do: no claim to promote/calibrate
    # AND no operator this capture created. #1088 P2 — keying this on
    # ``claim_ids`` alone blocks the folded-but-calibrated case below, where
    # a capture wired a new operator while every payload point folded onto
    # an already-calibrated canonical (``claim_ids == []``).
    if not claim_ids and not operator_ids:
        return
    # #1088: only ids MINTED by this capture may be promoted. A folded,
    # pre-existing id can still be an EP target, but an explicit draft must
    # never be retro-promoted by a later capture that merely deduped onto it.
    # ``promotion_ids`` is REQUIRED so a caller cannot silently fall back to
    # promoting the EP target set.
    promote_ids = list(promotion_ids)
    try:
        proj = sdk._get_proj()
        # 1. Claims -> live (draft-only guard mirrors create_operator's
        # promote_source clause — a terminal claim is never resurrected),
        # then REBUILD-DURABLE via the PointPromoted event (review P1): the
        # raw status flip alone would revert on JSONL replay (the #548 log
        # snapshotted the draft state at PointAdded) — the projection's
        # PointPromoted replay upserts the full live snapshot (projection/
        # __init__.py:1074). Snapshot AFTER the SET so the event carries the
        # live state. Capture auto-promotion is NOT reviewer-gated — the
        # snapshot carries the point's own props (no fabricated reviewed).
        # Candidate read FIRST (the SET below destroys the draft evidence):
        # MINTED ids only (#1088), non-operator, draft-or-unset status.
        cand_rows = proj.g.query(
            "MATCH (n:Point) WHERE n.id IN $ids "
            "AND (n.is_operator IS NULL OR n.is_operator = false) "
            "AND (n.status IS NULL OR n.status = 'draft') "
            "RETURN n.id",
            params={"ids": promote_ids},
        ).result_set
        promoted_claims = [r[0] for r in cand_rows]
        if promoted_claims:
            proj.g.query(
                "MATCH (n:Point) WHERE n.id IN $ids "
                "SET n.status = 'live'",
                params={"ids": promoted_claims},
            )
            for pid in promoted_claims:
                sdk._emit_event("PointPromoted", point=sdk.get_point(pid))
        # 2. Operators of the captured claims -> live (draft-only guard),
        # then REBUILD-DURABLE via OperatorPromoted (the R16 shape —
        # projection/__init__.py:1082 restores live on replay). Mirror
        # promote_point's _promote_incident_operators event exactly.
        # #1088: promote the operators THIS capture created (operator_ids) as
        # well as those wired to a promoted claim (promote_ids). The CLAIM
        # arm above stays minted-only (a folded explicit draft is never
        # retro-promoted), but a draft operator this call minted over a
        # FOLDED claim must go live or that claim's first calibration cannot
        # propagate through it — and a folded-only capture mints no claim,
        # so promotion_ids is empty and the claim join alone reaches nothing.
        op_rows: list = []
        _seen_op_ids: set = set()
        # #7853: c is id-anchored — sweep the epistemic labels so an operator
        # whose live endpoint is an Event/Subject/Object is promoted too. A
        # multi-labelled endpoint (``:Point:Object``) matches MORE than one
        # sweep, so collapse on ``o.id`` before the emit loop: the promoted
        # set must equal the journalled set (#395), and the loop below emits
        # one ``OperatorPromoted`` per row. The ``o.id IN $op_ids`` arm keeps
        # #1088's created-operator promotion inside this same sweep, so a
        # capture-minted operator over a FOLDED claim still goes live.
        for _q in epistemic_label_queries(
            "MATCH (o:Point {is_operator:true})-[:IMPL|NAND]->(c:{label}) "
            "WHERE (c.id IN $ids OR o.id IN $op_ids) "
            "AND (o.status IS NULL OR o.status = 'draft') "
            "RETURN DISTINCT o.id"
        ):
            for row in proj.g.query(
                    _q, params={"ids": promote_ids,
                                "op_ids": operator_ids}).result_set:
                if row[0] not in _seen_op_ids:
                    _seen_op_ids.add(row[0])
                    op_rows.append(row)
        promoted_ops = []
        for (oid,) in op_rows:
            proj.g.query(
                "MATCH (o:Point {id:$oid}) "
                "SET o.status = 'live'",
                params={"oid": oid},
            )
            sdk._emit_event("OperatorPromoted", id=oid,
                            point=sdk.get_point(oid))
            promoted_ops.append(oid)
        # 3. Write-trigger dirty-mark (claims + reverse-BFS neighbors).
        sdk._mark_dirty(list(claim_ids))
    except Exception as e:  # noqa: BLE001, RUF100
        _logger.warning(
            "capture EP promotion failed (non-fatal) for %d claims: %s",
            len(claim_ids), e, exc_info=True,
        )
        if warn is not None:
            warn(f"capture EP promotion failed: {type(e).__name__}: {e}")
        return
    try:
        # 4. BOUNDED ingest EP pass (local = dirty-root refresh; review P2:
        # mode="local" hydrates ALL graph-persisted dirty roots #1163, so an
        # explicit budget caps the request-time EP work — this capture's
        # claims calibrate well within it; any overflow stays dirty for the
        # scheduler's epoch-guarded sweep).
        sdk.dream(mode="local", require_calibration=False,
                  warm_start=False, budget=100)
    except Exception as e:  # noqa: BLE001, RUF100
        _logger.warning(
            "capture ingest EP pass failed (non-fatal) for %d claims: %s",
            len(claim_ids), e, exc_info=True,
        )
        if warn is not None:
            warn(f"ingest EP pass failed: {type(e).__name__}: {e}")


def _cosine(a: np.ndarray, b: np.ndarray) -> float:  # noqa: F821
    """Cosine similarity between two embedding vectors (#438).

    Stored embeddings round-trip as float32 lists — normalize defensively so
    the shared cosine thresholds (tortoise.embeddings DEFAULT_THRESHOLD /
    NEAR_DUPLICATE_THRESHOLD, bge-small calibration 2026-08-21) apply to
    the exact same metric cross_lens.py thresholds on.
    """
    import numpy as np

    na = float(np.linalg.norm(a))
    nb = float(np.linalg.norm(b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


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
        if dt.tzinfo is None:  # noqa: SIM108
            dt = dt.replace(tzinfo=timezone.utc)  # noqa: UP017
        else:
            dt = dt.astimezone(timezone.utc)  # noqa: UP017
        return dt.isoformat()
    # ISO-8601 string — normalize any timezone/offset to UTC. A naive string
    # (no offset suffix) is treated as UTC, mirroring the naive-datetime
    # branch — NOT as local time (which would shift the filter window by the
    # caller's offset; #243/#244 review).
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)  # noqa: UP017
    return dt.astimezone(timezone.utc).isoformat()  # noqa: UP017


def _save_progress(progress_file: str, directory: str, total: int, processed: int,
                   ingested: int, updated: int, skipped: int, failed: int,
                   errors: list[dict],
                   completed_files: list[str] | None = None) -> None:
    """Save batch indexing progress for resumability."""
    from datetime import datetime, timezone
    try:
        with open(progress_file, 'w') as f:
            _json.dump({
                "started": datetime.now(timezone.utc).isoformat(),  # noqa: UP017
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


# ── Index workflow (epic #900 T3) module helpers ──────────────────────────
# Connection-level retry budget for the bounded-abort disposition (E2E-19 /
# §6.4): DB write failures are per-file failed{retryable:true} while the
# connection can recover, up to this many consecutive failures — then the run
# aborts with a partial report (aborted/aborted_reason).
_INDEX_DB_RETRY_BUDGET = 3

# In-process per-url locks serializing the Source conditional MERGE + outcome
# detection (E2E-9 threads leg): the embedded FalkorDBLite executes concurrent
# same-key MERGEs in a parallel executor that reports "Nodes created: 1" for
# BOTH writers (and even re-fires both ON CREATE branches — commit order
# nondeterministic), so the counter-authority outcome needs the writers
# serialized. The threads leg is single-process (one daemon, per-thread SDK
# instances) — an in-process lock suffices; the bolt:// subprocess leg's stats
# are honest (and the __runId marker backstops cross-process ambiguity).
_source_merge_lock_guard = threading.Lock()
_source_merge_locks: dict[str, threading.Lock] = {}

# T6 (issue #1042): eventKind → Source sourceKind mapping for the legacy
# backfill reconciliation (CYCLE-25 v3.6 #6 spelling: agentSession).
_BACKFILL_SOURCE_KIND: dict[str, str] = {
    "AgentSession": "agentSession",
    "DocumentCreated": "document",
}

# Embedded db paths this PROCESS has opened (the §5.3 busy probe passes
# same-process daemon reuse — the registry records the DAEMON pid, which is
# never our own python pid).
_embedded_busy_known: set[str] = set()
_embedded_busy_guard = threading.Lock()


def _ep_require_calibration_default() -> bool:
    """#1157: shared default calibration posture for EP surfaces.

    Every EP surface (compute_confidence, dream, get_confidence) resolves
    ``require_calibration=None`` to this value — one knob for the #7478
    target ("0 silent uncalibrated EP runs"). Reads
    ``TORTOISE_EP_REQUIRE_CALIBRATION`` (default "1" → True — fail-closed
    since the #344 flip landed via PR #1212; set "0" to opt out). Draft
    points are excluded from the gate (#780/#1212), so draft-heavy test
    graphs stay passable under the fail-closed default.

    #4097: resolved through the declared truthy contract; `default "1"` is the
    default VALUE, so garbage still reads OFF.
    """
    return is_truthy(os.environ.get("TORTOISE_EP_REQUIRE_CALIBRATION", "1"))


# Per-corpus in-process run locks: the embedded FalkorDBLite's cross-connection
# MERGE semantics are broken under concurrency (observed: the ON CREATE branch
# re-fires against an existing key created by ANOTHER connection — the
# counter-authority outcome then lies). The threads leg (E2E-9) runs in ONE
# process, so serializing whole index_directory runs per corpus makes the
# second run's gate see the first's completed units → honest fast-path skips
# (the plan's "later completions report skipped/updated honestly"). The bolt://
# subprocess leg is cross-process (no shared lock) but server-mode stats/MERGE
# are honest there. This is an ORCHESTRATION-side serialization — the
# write-path machinery (conditional MERGE, repair carve-out) is unchanged.
_index_run_lock_guard = threading.Lock()
_index_run_locks: dict[str, threading.Lock] = {}


def _index_run_lock_for(key: str) -> threading.Lock:
    with _index_run_lock_guard:
        lock = _index_run_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _index_run_locks[key] = lock
        return lock


def _mark_embedded_opened(db_path: str) -> None:
    with _embedded_busy_guard:
        _embedded_busy_known.add(str(db_path))


def _stream_to_payload(summary: dict, session_id: str, stream: dict) -> dict:
    """Payload from the constructed graph stream (the wired structure).

    T4 (#1272): stream ids from the LLM are NOT trustworthy (the prompt says
    "the server re-derives them" but the code does not) — re-derive every
    point/event id from content (pt_<sha>/ev_<sha>) and REMAP operator
    src/dst + MITIGATES target triples to the re-derived ids, else Layer-1
    referential integrity fails. Points are enriched with the required
    fields (reason/confidence/c_cal) at the honest neutral prior 0.5/0.5
    (T11 — never derived from LLM text in v1) and status draft (EP-inert
    until #785)."""
    from tortoise.ids import content_hash

    id_map: dict[str, str] = {}
    points = []
    for p in stream.get("points", []):
        if not p.get("id", "").startswith("pt_"):
            continue
        content = p.get("content", "")[:1000]
        new_id = f"pt_{content_hash(content)[:62]}"
        id_map[p["id"]] = new_id
        points.append({
            "id": new_id, "content": content,
            "pointKind": "statement", "reason": "NEW",
            "confidence": 0.5, "c_cal": 0.5,   # neutral prior (T11), never fabricated
            "about_entities": p.get("about_entities", []) or [],
            "source_ref": "session.md", "quote": "", "status": "draft",
        })

    events = []
    for e in stream.get("events", []):
        if not e.get("id", "").startswith("ev_"):
            continue
        content = e.get("content", "")[:1000]
        new_id = f"ev_{content_hash(content)[:62]}"
        id_map[e["id"]] = new_id
        events.append({
            "id": new_id, "eventKind": e.get("eventKind", "occurrence"),
            "content": content, "confidence": 0.5,   # neutral prior (T11)
            "about_entities": e.get("about_entities", []) or [],
            "source_ref": "session.md",
        })

    def _remap(ref):
        return id_map.get(ref, ref)

    def _mapped(ref):
        """True iff the ref is in the re-derived id map. An operator whose
        src/dst/target references an LLM-fabricated id NOT emitted by this
        stream is unknowable — drop it rather than 422 the whole commit
        (P2-3 #1272 review)."""
        return ref in id_map

    operators = []
    for op in stream.get("operators", []) or []:
        o = dict(op)
        if not (_mapped(o.get("src", "")) and _mapped(o.get("dst", ""))):
            continue
        o["src"] = _remap(o.get("src", ""))
        o["dst"] = _remap(o.get("dst", ""))
        if o.get("target"):
            t = dict(o["target"])
            if not (_mapped(t.get("src", "")) and _mapped(t.get("dst", ""))):
                continue
            t["src"] = _remap(t.get("src", ""))
            t["dst"] = _remap(t.get("dst", ""))
            o["target"] = t
        operators.append(o)

    entities = [e for e in stream.get("entities", []) if e.get("name")]
    return {
        "schema_version": "1", "session_id": session_id,
        "client_commit_id": "", "captured_at": _now_iso(),
        "extractor": {"version": "value@0.4.0+construct", "mode": "byok",
                      "calibration_version": "v1"},
        "summary": (summary.get("session") or {}).get("summary", "")[:2000],
        "story_arc": "",
        "provenance_refs": [{"path": "session.md", "spans": []}],
        "sources": [], "entities": entities, "points": points,
        "events": events, "operators": operators,
        "telemetry": {"extractor": {"version": "value@0.4.0+construct", "mode": "byok"},
                      "model": {"provider": "byok", "id": "user-model", "cfg_hash": ""},
                      "counts": {"kept": len(points),
                                 "candidate": len(events),
                                 "segment": 1, "window": 1, "empty_windows": 0},
                      "keep_ratio": None, "dedup_hits": None, "frontier_calls": 2,
                      "llm_cost_usd": None, "extraction_ms": 0, "retry_count": 0,
                      "last_error_code": None, "confidence_histogram": None},
    }


def _source_merge_lock_for(url: str) -> threading.Lock:
    with _source_merge_lock_guard:
        lock = _source_merge_locks.get(url)
        if lock is None:
            lock = threading.Lock()
            _source_merge_locks[url] = lock
        return lock


def _resolve_under_base_realpath(candidate: str, base: str) -> str | None:
    """Realpath-resolved containment check (the index_file path argument gets
    the same resolved-target discipline as progress_file/directory — §6.4
    cycle-7 pin: an in-base symlink whose target resolves OUTSIDE the base
    raises; a lexical check alone would pass the #329 argument-path test)."""
    import os as _os
    try:
        real = _os.path.realpath(candidate)
        from pathlib import Path
        Path(real).relative_to(Path(_os.path.realpath(base)))
        return real
    except (ValueError, OSError):
        return None


def _classify_db_failure(e: BaseException) -> str | None:
    """Map an exception to the §6.4 DB-failure cause-class (E2E-19 naming):
    ``db`` when the write path hit the graph engine (ResponseError /
    ConnectionError / TimeoutError / ENOSPC-family); None otherwise (the
    per-file handler re-buckets it as structural)."""
    import os as _os  # noqa: F401, I001
    import errno
    try:
        import redis.exceptions as _re
        if isinstance(e, (_re.ResponseError, _re.ConnectionError,
                          _re.TimeoutError, _re.BusyLoadingError,
                          _re.InvalidResponse)):
            return "db"
    except Exception:  # noqa: BLE001, RUF100
        pass
    if isinstance(e, OSError) and getattr(e, "errno", None) in (
            getattr(errno, "ENOSPC", None), getattr(errno, "EIO", None),
            getattr(errno, "EROFS", None)):
        return "db"
    return None


# ── Module-level cached registry for kind expansion
_registry_cache: "PackRegistry | None" = None  # noqa: F821, UP037
_registry_lock = threading.Lock()


def _get_kind_expander():
    """Return cached PackRegistry with pre-computed expansion table."""
    global _registry_cache
    if _registry_cache is None:
        with _registry_lock:
            if _registry_cache is None:
                from .pack_registry import PackRegistry  # noqa: I001
                from .pack_registry import default_packs_dir
                packs_dir = default_packs_dir()
                _registry_cache = PackRegistry(packs_dir)
                _registry_cache.load_all()
    return _registry_cache


def _decorate_fallback_hits(results: list[dict], graph) -> list[dict]:
    """Attach promoted epistemic state (D8) to embedded-fallback hits — one
    batch fetch (#1353, E5 #1537). Additive, mirroring SearchResult.to_dict:
    a key is added only when its state field is present — except for #4889's
    ``subject_unavailable``, which is written when the state is *empty* on a
    graph that cannot carry ``aboutSubject`` (the loud half of a read surface
    that would otherwise be silently empty). Decoration must never break
    retrieval — a graph failure returns the hits undecorated.

    #4889: the degraded fallback is a Point-only surface that advertises the
    same promoted-state fields as the primary path, so it owes the same
    ``subject`` decoration AND the same fail-loud contract. Before this, a
    fallback hit carried no ``subject`` at all even on a graph with a producer,
    and no ``subject_unavailable`` when the binding was dead — the exact
    silent-absence shape #4889 removes on the primary path.
    """
    if not results:
        return results
    try:
        from tortoise.search_engine import (
            SUBJECT_BINDING_UNAVAILABLE,
            fetch_point_epistemic_state,
            subject_binding_available,
        )
        state = fetch_point_epistemic_state(graph, [r["id"] for r in results])
    except Exception:
        _logger.warning("embedded fallback decoration failed — returning "
                        "undecorated hits", exc_info=True)
        return results
    for r in results:
        st = state.get(r["id"]) or {}
        if st.get("status"):
            r["status"] = st["status"]
        if st.get("superseded_by"):
            r["superseded_by"] = st["superseded_by"]
        if st.get("supersedes"):
            r["supersedes"] = st["supersedes"]
        # #2294: the legacy flag half of the terminal vocabulary — the embedded
        # fallback owes the same terminality signal as the primary path.
        if st.get("outdated"):
            r["outdated"] = True
        # E6 (#1538) D7: promoted window fields — additive, only when present.
        for key in ("valid_from", "valid_to", "expired_at"):
            if st.get(key):
                r[key] = st[key]
        # #2350 (E-prec): the precision block owes every read path the same
        # 3-state end the primary decoration carries — the embedded fallback
        # must not relabel an ended-unknown window as still-true.
        # #9201: presence is the BOUND's, not the precision's. A present-but-
        # unorderable bound resolves to precision ``None``, so each gate reads
        # its end's presence datum and the key is emitted as ``null`` —
        # dropping it here is the cross-surface drift #2350 review P2 removed.
        if (st.get("valid_from_present") or st.get("valid_from")
                or st.get("valid_to") or st.get("valid_to_present")
                or st.get("valid_from_precision")
                or st.get("valid_to_precision")):
            if st.get("valid_from_precision") or st.get("valid_from_present"):
                r["valid_from_precision"] = st.get("valid_from_precision") or None
            if st.get("valid_to_precision") or st.get("valid_to_present"):
                r["valid_to_precision"] = st.get("valid_to_precision") or None
            r["end_state"] = st.get("end_state") or _resolve_end_state(
                st.get("valid_to"), st.get("valid_to_precision"))
        # #4889: same additive subject field as SearchResult.to_dict.
        if st.get("subject"):
            r["subject"] = st["subject"]
    # #4889: the same fail-loud marker as the primary path — only when the
    # whole batch resolved no subject AND the graph cannot carry one for
    # Points/Events. Fail-open (the probe returns True on any error), so a
    # broken probe never fabricates an unavailability claim.
    if not any(r.get("subject") for r in results) \
            and not subject_binding_available(graph):
        for r in results:
            r["subject_unavailable"] = SUBJECT_BINDING_UNAVAILABLE
    return results


# ── Session-context digest noise filter (#2207) ─────────────────────────
# The session-start digest (`tortoise context` → TortoiseSDK.session_context(),
# mirrored by hosted /v1/context) must surface GENUINE decision/claim points.
# Rule/config noise and markdown fragments that reach the graph via
# document/transcript extraction — HR separators ('---'), label-led rule
# bullets ('*Gate: filed as child issue…'), heading/table/quote/fence lines,
# bare 'Label: value' config residue — are filtered out at digest time
# (display-layer defense only; the extractor itself is unchanged).
#
# #2225 (post-batch bug hunt): the filter must NOT drop the SDK's OWN
# decision-writer shapes, which are label-led by construction but are
# genuine decisions — 'Decision: …' (file_decision), 'Approved: …' (human
# approval), 'Option N: …' (file_decision rows), the decide flows'
# 'Reason:'/'Finding:' leads, and date-led '2026-09-06: …' diary lines. They
# are exempted from the label-noise arm before it runs (see
# _DIGEST_GENUINE_LABEL_RE); a decisions-only graph must not read as
# "no prior sessions".
_DIGEST_STRUCTURE_RE = re.compile(r"^(?:[-*=~_`|#>]{2,}|\.{2,}|[-*+]\s*)$")
_DIGEST_MD_LEAD_RE = re.compile(r"^(?:#{1,6}\s|>{1,}|`{3,}|~{3,}|\|)")
# Genuine decision content that LOOKS label-led but must survive the digest:
# date-led lines and the SDK/decide-flow label families in their authored
# spelling — 'Decision: …'/'Option N: …' (file_decision), 'Approved: …'
# (file_human_approval), 'Reason:'/'Finding:' leads (decide flows). Every SDK
# writer capitalizes the label and no capture/extraction path lowercases point
# content before persisting (all .lower() uses are tokenization/dedup/classi-
# fication keys), so the match is EXACT-case: a generic lowercase
# 'decision: pending'-style line is config/transcript residue — NOT a decision
# — and still hits _DIGEST_LABEL_RE below (review #2434 P2-2).
_DIGEST_GENUINE_LABEL_RE = re.compile(
    r"^(?:\d{4}-\d{2}-\d{2}\s*:"            # '2026-09-06: we decided…'
    r"|(?:[-*+]\s+|\d+[.)]\s+)?"             # optional list/number marker
    r"(?:Decision|Approved|Reason|Finding|Option)\s*\d*\s*:"
    r")",
)
# Label-led rule/config lines: an optional list/number marker and optional
# emphasis, then a label ending in ':' before the value — '*Gate: filed as
# child issue…', '- model: gpt-5', '* HARD RULE: Skill Compliance',
# 'TORTOISE_DB_URI: docker://…'. All-caps continuations keep multi-word rule
# labels ('HARD RULE', 'DO NOT EDIT') together; prose claims starting
# mid-sentence are never label-led. Genuine decision shapes above never
# reach this arm (_DIGEST_GENUINE_LABEL_RE short-circuits first).
_DIGEST_LABEL_RE = re.compile(
    r"^(?:[-*+]\s+|\d+[.)]\s+)?(?:[*_]{1,2})?"
    r"[A-Za-z0-9][A-Za-z0-9_.-]*(?:\s+[A-Z][A-Z0-9_.-]*)*"
    r"\s*:(?:[*_]{1,2})?\s+\S"
)


def _is_digest_noise(content) -> bool:
    """True when a Point's content is rule/config noise rather than a
    digest-worthy decision/claim (#2207). Pure function over content so the
    local digest, hosted /v1/context and the CLI share one definition."""
    if not isinstance(content, str):
        return True
    t = content.strip()
    if not t:
        return True
    if _DIGEST_STRUCTURE_RE.fullmatch(t):
        return True
    if _DIGEST_MD_LEAD_RE.match(t):
        return True
    # #2225: the SDK's own label-led decision shapes are genuine content —
    # exempt them BEFORE the rule/config label arm so 'Decision: …' /
    # 'Approved: …' points never vanish from the digest.
    if _DIGEST_GENUINE_LABEL_RE.match(t):
        return False
    return bool(_DIGEST_LABEL_RE.match(t))


def _resolve_vector_min_similarity() -> float | None:
    """#4028 — the OPT-IN vector-leg relevance floor (cosine) for the surface.

    **Default: None (no floor — the pre-#4028 behaviour).** An absolute
    cosine floor cannot be default-on: the query->document relevant and
    unrelated bands OVERLAP for bge-small (see
    ``embeddings.VECTOR_RELEVANCE_FLOOR``), so any floor high enough to drop
    the #4028 residue also drops real answers and fails
    ``tests/test_longmem_runner.py::test_vector_strategy_verified_in_eval_path``.
    #4028's store defect is DATA (test residue), fixed by
    ``tools/purge_test_residue.py``.

    ``TORTOISE_VECTOR_MIN_SIMILARITY`` enables it (a value in (0, 1];
    ``embeddings.VECTOR_RELEVANCE_FLOOR`` is the calibrated starting point).
    Unset, 0, unparseable or out of range → None (off).

    The knob changes the behaviour of the existing ``tortoise_search`` read
    surface; it adds NO surface (#3863 surface freeze).
    """
    raw = os.environ.get("TORTOISE_VECTOR_MIN_SIMILARITY")
    if raw is None or not raw.strip():
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        # A typo must never silently ENABLE a floor that drops real answers.
        return None
    if not (0.0 < value <= 1.0):
        return None
    return value


def _index_no_network_enabled() -> bool:
    """`TORTOISE_INDEX_NO_NETWORK` — test-only: `extract_metadata=False` without network.

    #4097: the single resolution point for that knob (``TortoiseSDK._index_no_network``
    delegates here), so the declared truthy contract has one place to assert.
    """
    return env_flag("TORTOISE_INDEX_NO_NETWORK", False)


def _holds_role_available(graph) -> bool:
    """True when this graph can carry ``holdsRole`` edges at all (#4889).

    ``get_org_structure`` returns ``roles`` from a ``holdsRole`` traversal
    whose shape is ``(:Subject)-[:holdsRole]->(:Subject)``. That predicate has
    **no producer anywhere in the tree** — every reference is an edge
    allow-list, the read query itself, or an explicit
    ``create_edge``/``create_entity`` caller. On a graph where it is absent the
    empty ``roles`` list is structural, not a finding, so the method says so
    instead of returning an unqualified empty result.

    The probe counts the SAME shape the read traverses (``Subject`` source and
    target), so a hand-written non-Subject ``holdsRole`` edge cannot suppress
    the marker while ``roles`` stays permanently empty — the same asymmetry
    ``search_engine._SUBJECT_SOURCE_SCOPED_PROBE`` fixes for ``aboutSubject``.
    Bounded by ``_HOLDS_ROLE_PROBE_TIMEOUT_MS``.

    Mirrors ``search_engine.subject_binding_available``: **fail-OPEN** — a probe
    error returns True so a broken probe can never invent an unavailability
    claim. Self-clears the moment a ``holdsRole`` edge of that shape exists.
    """
    try:
        rows = graph.query(
            _HOLDS_ROLE_SCOPED_PROBE,
            timeout=_HOLDS_ROLE_PROBE_TIMEOUT_MS).result_set
        if not rows or not rows[0]:
            _logger.warning(
                "holdsRole availability probe returned no row — assuming "
                "available")
            return True
        count = rows[0][0]
        if count is None:
            _logger.warning(
                "holdsRole availability probe returned a null count — "
                "assuming available")
            return True
        return int(count) > 0
    except Exception:  # fail-open, see docstring
        _logger.warning(
            "holdsRole availability probe failed — assuming available",
            exc_info=True)
        return True


#: The exact ``holdsRole`` shape ``get_org_structure`` traverses. Scoping the
#: probe to it is what keeps a non-Subject ``holdsRole`` edge from suppressing
#: the marker on a graph where ``roles`` can never be populated.
_HOLDS_ROLE_SCOPED_PROBE = (
    "MATCH (:Subject)-[r:holdsRole]->(:Subject) RETURN count(r)")

#: Bound for the availability probe — the same 200 ms class as the search
#: assembly's decoration bound. An unbounded count on a read path is the
#: defect this probe must not reintroduce.
_HOLDS_ROLE_PROBE_TIMEOUT_MS = 200


#: #4889 — the additive ``unavailable`` reason ``get_org_structure`` returns
#: when the ``holdsRole`` leg has no producer on this graph.
HOLDS_ROLE_UNAVAILABLE = (
    "holdsRole has no producer anywhere in the tree — only an explicit "
    "create_edge/create_entity caller — so 'roles' is structurally empty "
    "rather than a finding about this Subject."
)


def _derive_graph_name(*, namespace: str | None,
                       graph_name: str | None,
                       uri_graph: str | None = None) -> str:
    """The SDK's ONE namespace/graph_name → DB graph-name derivation.

    Extracted from ``_get_proj`` (#4240 review F2) so a caller that must name
    the graph OUTSIDE a projection derives it identically instead of
    re-implementing the branches. The hosted lane's per-graph journal path is
    exactly such a caller: a second derivation there would key the journal to a
    graph the SDK never opens (a rebuild would replay the wrong file), and the
    branches are not obvious — ``namespace="test-foo"`` opens
    ``test_foo_tortoise``, NOT ``org_test-foo``.

    Precedence is unchanged from ``_get_proj`` (in order): registry control
    plane → explicit ``graph_name`` verbatim → namespace (test_*/
    tortoise_test* → ``{ns}_tortoise``, hyphenated test-* → normalized to
    ``test_<ns>_tortoise``, otherwise ``org_{ns}``) → the URI's own graph,
    else ``tortoise``.
    """
    if namespace == "registry":
        # Control-plane SDK: shared registry main graph.
        return "registry_tortoise"
    if graph_name is not None:
        # C5 #2114 (D-C5-1): explicit graph-name override (a custom
        # org_{tid}_{gid} graph). Never a namespace derivation — the name is
        # used verbatim.
        return graph_name
    if namespace:
        if namespace.startswith(("test_", "tortoise_test")):
            # Test namespace: isolate on a test-prefixed graph so the
            # _assert_test_graph guard still passes (#221). Matches the
            # historical {ns}_tortoise naming.
            return f"{namespace}_tortoise"
        if namespace.startswith("test-"):
            # Epic #1647 (T7, cycle-5 P1-5): the hyphenated test-* family
            # (test-tiers, test-invites, test-hosted, test-e1, test-org-722,
            # ...) is a TEST namespace too — normalize '-' → '_' so it maps to
            # the guard-passing test_<ns>_tortoise graph (test-tiers →
            # test_tiers_tortoise). Without the branch it falls into org_<ns>
            # (org_test-tiers) — a NON-test graph that is invisible to
            # `grep -v 'namespace="test_'` and fails _assert_test_graph on
            # bulk wipe.
            return f"{namespace.replace('-', '_')}_tortoise"
        # Org SDK: isolated org graph (matches provision's org_{org_id}
        # namespace creation, #7886).
        return f"org_{namespace}"
    # No namespace: honor the URI's own graph (the conftest session graph for
    # tests). Fixes #7886 regression that hardcoded 'tortoise' and clobbered
    # the test graph.
    return uri_graph or "tortoise"


class InvertedSupersedeWindow(ValueError):
    """#5363 — a supersede refused because its successor's window start
    precedes the predecessor's ``validFrom`` (#4021's inverted-window guard).

    Subclasses ``ValueError`` because that is the contract ``supersede_point``
    has always published (callers and tests catch ``ValueError``), but it is a
    NAMED type so a transport boundary can tell this DETERMINISTIC payload
    refusal from a transient graph failure WITHOUT parsing the message — which
    the MCP layer rewrites (``_scrub_error``) and two tests pin.

    The conflicting values ride as attributes so the boundary that renders the
    client-facing error does not have to re-derive them from the prose.
    """

    def __init__(self, message, *, old_id, new_id, successor_start,
                 predecessor_valid_from):
        super().__init__(message)
        self.old_id = old_id
        self.new_id = new_id
        self.successor_start = successor_start
        self.predecessor_valid_from = predecessor_valid_from


class UnapprovedKindNamespace(ValueError):
    """#5475 — a kind write refused because it names a pack the graph lacks.

    Subclasses ``ValueError`` because that is the contract every SDK write door
    publishes (callers and tests catch ``ValueError``), but it is a NAMED type
    so a transport boundary can tell this DETERMINISTIC payload refusal from a
    transient graph failure WITHOUT parsing the message — which the MCP layer
    rewrites (``_scrub_error``) and which the hosted commit door would
    otherwise turn into a retry-advising 500 for a payload that will refuse
    identically on every retry (the #5363 precedent, one class over).

    The facts ride as attributes (the offending ``prop``/``kind``/``namespace``
    and the graph's ``installed`` set) so a boundary that renders a
    client-facing error does not have to re-derive them from the prose.
    """

    def __init__(self, message, *, prop: str, kind: str, namespace: str,
                 installed: frozenset[str] | None):
        super().__init__(message)
        self.prop = prop
        self.kind = kind
        self.namespace = namespace
        self.installed = installed


#: #7819 — the props REFUSED on a TERMINAL claim: ONTOLOGY §4.7's four
#: caller-temporal properties — the valid-time window (both bounds and their
#: precision markers, which describe the bounds), ``expiredAt``, the
#: transaction-time termination stamp, and ``when``, the occurrence-time anchor.
#: An end written after death is the window the temporal read surfaces answer
#: from (`_covers` / `get_historical_knowledge`), so writing it here changes what
#: the dead claim asserts about WHEN it held, with no CORRECTS and no lifecycle
#: record. ``when`` carries the same harm on the occurrence axis, and it is not
#: inert metadata: ``extractor_v2`` dates a supersession decision off
#: ``existing["when"]``, so re-stamping it re-dates the dead claim for the
#: extractor as well as for the temporal reads.
#:
#: (``createdAt``/``updatedAt`` complete §4.7's temporal set but are write
#: stamps, not claims about when the assertion held: ``update_point`` stamps
#: ``updatedAt`` itself on every call and ``createdAt`` is a create-time stamp,
#: so neither belongs to the temporal record this guard protects.)
#:
#: Scoped, not blanket, because a content/metadata revision of a terminal claim
#: is a recorded capability: a ``PointRevised`` later than a terminalization is
#: the newer same-id writer and the replay fold handles it (``skip_updated_at``
#: via ``max_inline_seq``, #3305), pinned by
#: ``test_pointinvalidated_rebuild.py::test_revise_after_invalidate_skip_
#: updated_at`` and ``test_pointsuperseded_rebuild.py::test_supersede_then_
#: revision_does_not_end_at_the_supersede_ts``.
#:
#: ``outdated`` is absent because ``_sanitize_props`` already refuses it as a
#: server-managed lifecycle flag.
#:
#: #9649 covers the separate door ``update_entity`` opens onto the same window
#: (its Point arms write caller props and ``status`` with a vocabulary check and
#: no lifecycle guard, so they can also flip a ``retracted`` Point to ``live``).
#:
#: The two precision names are the CANONICAL constants from ``validity``, never
#: re-spelled here: a hand-written copy of a canonical declaration is how
#: ``outdated`` silently dropped out of three readers (#2901/#4058), and a
#: rename in ``validity`` would be followed by the #2350 precision refusal while
#: this set kept the stale spelling — the guard would then fail OPEN on the
#: markers every other reader still gates.
_TEMPORAL_RESTAMP_PROPS = frozenset({
    "validFrom", "validTo", FROM_PRECISION_PROP, TO_PRECISION_PROP,
    "expiredAt", "when",
})


def _shape_or_refuse_caller_end(props: dict) -> dict | None:
    """#9202: read the CALLER's end for intent on the way in — shape or refuse.

    Replaces the long write-boundary END warning (#6140/#9910, "stored verbatim,
    merely warned about") with the two messages the 2026-10-26 ruling asks for:
    a SHAPED value (blank or an uncertainty marker) is stored as the canonical
    un-datable form and ECHOED back in the write reply, and an ILLEGIBLE
    attempted instant (or text with no recoverable intent) is REFUSED naming the
    fault and the repair. The decision itself, and its vocabulary, live in
    ``validity.read_caller_end`` — this function only applies it to ``props``.

    ⛔ NOT wired to ``validFrom``. The start keeps the #6140 warning: that ruling
    named the START, and shaping it would re-open a reading it deliberately
    pinned. Only the END moved, and only because the owner re-decided it here.

    ⚠️ **This binds the GUARDED caller doors only** (``create_point``,
    ``update_point``), so the refusal is not airtight and §4.7 does not claim it
    is: ``update_entity``'s Point arms remain an unguarded route to a junk end
    (#9649), and ``supersede_point``'s numeric-``valid_from`` kwarg is
    deliberately exempt (it is not a caller ``validTo`` write). The shape/refuse
    rule, its vocabulary, its receipt and its count are stated HERE and in
    ``validity.read_caller_end`` rather than in the canonical ontology: they are
    write-door behaviour, not the model.

    Mutates ``props`` in place — ``validTo`` absent (``None``, which ``SET n +=
    $props`` removes) plus ``validToPrecision='unknown'`` — and returns the
    in-band RECEIPT (``{"sent": <token>, "validTo": None,
    "validToPrecision": "unknown"}``) for the door to merge into its reply, or
    ``None`` when the value was accepted (nothing to report: an accepted instant
    and a normalised format variant are both SILENT — §0's "complete it, don't
    lecture", and §1's "normalised silently").

    The receipt is not cosmetic: without it a real typo (``validTo="-"`` meant as
    a dash between two dates) would merge into "the author did not know" with no
    trace — §7 trap 1, the trap that makes shaping indefensible.

    One home, no second gate: this runs INSIDE ``_refuse_inverted_point_window``,
    the caller-bound boundary ``create_point`` / ``update_point`` already route
    through, on the predicate every read path orders with
    (``validity._names_an_instant``). A second "is this an instant" test is
    exactly the #9201/#9244 defect class.
    """
    from tortoise.validity import (
        PRECISION_UNKNOWN,
        TO_PRECISION_PROP,
        read_caller_end,
    )
    if "validTo" not in props:
        return None
    sent = props["validTo"]
    stored, shaped = read_caller_end(sent, props.get(TO_PRECISION_PROP))
    if shaped is None:
        if stored != sent:
            # Representational variance the read measure could not order
            # (`2024-3-1`, `2024/03/01`) — the SAME instant, so it is written in
            # the canonical form with no comment: finishing the caller's
            # sentence, not changing it.
            props["validTo"] = stored
        return None
    props["validTo"] = None
    props[TO_PRECISION_PROP] = PRECISION_UNKNOWN
    return {
        "sent": shaped,
        "validTo": None,
        "validToPrecision": PRECISION_UNKNOWN,
    }


def _with_end_receipt(reply: dict, receipt: dict | None) -> dict:
    """Merge the #9202 shaping RECEIPT into a write-door reply (in-band).

    Returns ``reply`` unchanged when nothing was shaped, so a write that sent a
    real instant comes back byte-identical to before this door learned to shape.
    """
    if not receipt:
        return reply
    return {**reply, "valid_to_shaped": receipt}


def _refuse_inverted_point_window(g, props: dict, *,
                                  point_id: str | None = None) -> dict | None:
    """#5359: refuse an INVERTED validity window at a Point write boundary.

    ``create_point`` / ``update_point`` accept ``validFrom``/``validTo`` as
    caller props, and ``mining._temporal_wire`` stamps ``validFrom`` on a draft
    decision point. None of those writers read the OPPOSITE bound, so a caller
    (or the miner's session date, read against a stored end) could persist
    ``validTo < validFrom`` directly — bypassing the refusal #4021 added inside
    ``supersede_point``. ``get_historical_knowledge``'s ``_covers`` then covers NO
    instant and the point vanishes from every temporal query while every read
    reports honest absence.

    The PREDICATE is delegated, never re-implemented:
    ``commit_schema.validate_validity_window`` is the ONE HOME for the measure
    (``_created_sort_key``) and the presence gate (``is not None``). (The READ
    path's START gate is presence AND orderability since #6140 — an unorderable
    start is an OPEN start there — so the two agree by both SKIPPING a bound
    that names no instant, not by sharing one predicate.) This function owns only what the declaration
    explicitly does NOT (its own scope note): resolving the pair a PARTIAL
    write will actually persist. ``point_id is None`` is a fresh create, where
    both bounds are the caller's; otherwise the caller's bound is merged with
    the STORED opposite bound.

    EVERY matching node is examined, not just the first: point ids are not
    unique (the duplicate fan-out is a tested shape) and these writers MATCH
    every node carrying the id, so a first-row-only read could pass the guard
    and still leave an inverted window on a sibling — the verdict would then
    depend on server row order. Same fan-out discipline as
    ``TortoiseSDK._assert_window_start_not_inverted`` (#5358).

    Absent bounds are legal (a lone ``validFrom`` is an open-ended window), and
    a bound ``_created_sort_key`` cannot order is deliberately not refused
    (#5360). A key PRESENT with value ``None`` is a CLEAR, not an absence:
    ``SET n += $props`` removes the property, so the effective bound is
    ``None`` and the window is open-ended — hence the key-presence test rather
    than a truthiness test.

    Since #9202 (owner ruling 2026-10-26) the CALLER's END is decided here too,
    BEFORE the pair is validated: ``_shape_or_refuse_caller_end`` shapes a value
    that declares no date and refuses one that attempts an instant and fails, so
    every value this function then validates is either an orderable instant or
    absent. The returned RECEIPT is the door's in-band echo of a shaped value.
    """
    if "validFrom" not in props and "validTo" not in props:
        return None
    # #9202: the end's decision PRECEDES the pair validation below, so the two
    # never disagree about the value being written.
    receipt = _shape_or_refuse_caller_end(props)
    # #6140 option C, as the owner ruled it — a WARNING, not a refusal, for the
    # START. A caller handing us a bound that names no instant (`""`, `"TBD"`)
    # is told what that value MEANS, and the write proceeds: refusing would make
    # a legacy point impossible to update or supersede until its window was
    # repaired. Only the CALLER's value warns — a bound merged in from the
    # stored opposite side is not being written, so
    # `commit_schema.validate_validity_window` (which sees only the merged pair)
    # deliberately stays silent.
    #
    # ⚠️ The END arm that used to sit beside this one is GONE (#9202, owner
    # ruling 2026-10-26): a caller end is now SHAPED or REFUSED above, so there
    # is no third outcome left for a warning to describe. The START keeps its
    # warning because #6140's ruling named the start and nothing has reversed
    # that half.
    #
    # Warn ONCE per value per WRITE. Two re-entry shapes reach this function
    # for a single write: a dedup-hit `create_point` (create leg, then
    # `update_point` with the same props) and a duplicate-id fan-out. The
    # create leg sets ``_WARN_SUPPRESS`` for the re-entry it causes, and the
    # update leg stays silent for a value the stored node already carries — an
    # unrelated update of a legacy point re-reads a value nobody is writing.
    # (A SHAPED end needs none of that: the create leg rewrites ``props`` to
    # absent + `unknown` before the dedup re-entry, so the update leg sees a
    # value that declares nothing and shapes nothing — one receipt, one log
    # line, by construction.)
    def _warn_unorderable_caller_bound(prop: str, bound: object) -> None:
        """Say what a caller START that names no instant MEANS, once per write.

        The gate is ``validity._names_an_instant`` — the ONE home for "does this
        value point at an instant?", the same measure ``_covers``,
        ``effective_precision``, ``end_state``, the inversion guard and
        ``_shape_or_refuse_caller_end`` all use — so this warning cannot disagree
        with the read path about which values are instants, and no second notion
        of "parseable" is introduced (#9201's rule). The value is neither
        refused nor rewritten (#6140's ruling for the START: a warning, never a
        refusal).
        """
        from tortoise.validity import _names_an_instant
        if bound is None or _names_an_instant(bound):
            return
        suppressed = _WARN_SUPPRESS.get()
        if suppressed is not None and repr(bound) in suppressed:
            return
        _logger.warning(
            "%s %r names no instant — it means NO START WAS "
            "RECORDED, so the window is OPEN below (ONTOLOGY §4.7, "
            "#6140). Pass an ISO-8601 instant, or omit the property, to "
            "make the intent explicit.",
            prop, bound,
        )

    from tortoise.commit_schema import validate_validity_window
    if point_id is None:
        _warn_unorderable_caller_bound("validFrom", props.get("validFrom"))
        validate_validity_window(props.get("validFrom"), props.get("validTo"))
        return receipt
    rows = g.query(
        "MATCH (n:Point {id:$id}) RETURN n.validFrom, n.validTo",
        params={"id": point_id},
    ).result_set
    if not rows:
        return receipt
    warned_start = False
    for stored_vf, stored_vt in rows:
        # ``get(key, default)`` uses the default only when the key is ABSENT,
        # so a key present with value None still clears the bound (matching
        # `SET n += $props`), while an absent key keeps the stored one.
        effective_vf = props.get("validFrom", stored_vf)
        effective_vt = props.get("validTo", stored_vt)
        if not warned_start and "validFrom" in props and props["validFrom"] != stored_vf:
            _warn_unorderable_caller_bound("validFrom", props["validFrom"])
            warned_start = True
        validate_validity_window(effective_vf, effective_vt)
    return receipt


def _resolve_end_state(valid_to: object, valid_to_precision: object) -> str:
    """#2350: the 3-state end, resolved by validity.py's ONE home."""
    from tortoise.validity import end_state
    return end_state(valid_to, valid_to_precision)


def _refuse_inconsistent_validity_precision(g, props: dict, *,
                                            point_id: str | None = None) -> None:
    """#2350: refuse a validity-precision declaration that contradicts its bound.

    The precision vocabulary's ONE write-boundary guard, mirroring
    ``_refuse_inverted_point_window``: it resolves the pair a PARTIAL update
    will actually persist (the caller's key when PRESENT, else the stored
    value) and delegates the predicate to ``validity.validate_validity_precision``.
    ``point_id is None`` is a fresh create, where all four values are the
    caller's.

    TWO consequences a caller will meet, both deliberate fail-closed choices:

    * ``validToPrecision='unknown'`` requires the effective ``validTo`` to be
      ABSENT — pass ``validTo=None`` alongside it to clear a stored end, since
      ``SET n += $props`` removes a property whose key is present with a
      ``None`` value.
    * moving a window OFF an unknown end (``update_point(id, validTo=…)`` on a
      point whose stored end is unknown) is refused until the caller states
      the new precision: leaving the stale ``'unknown'`` marker beside a real
      date is the fabrication the marker exists to prevent.

    Legacy windows carry no precision property; ``props.get(key, stored)``
    then yields ``None`` for both sides and the guard is inert, so no
    pre-existing write changes behaviour.
    """
    from tortoise.validity import (
        FROM_PRECISION_PROP,
        TO_PRECISION_PROP,
        validate_validity_precision,
    )
    keys = ("validFrom", "validTo", FROM_PRECISION_PROP, TO_PRECISION_PROP)
    if not any(k in props for k in keys):
        return
    if point_id is None:
        validate_validity_precision(
            props.get("validFrom"), props.get("validTo"),
            props.get(FROM_PRECISION_PROP), props.get(TO_PRECISION_PROP),
        )
        return
    rows = g.query(
        "MATCH (n:Point {id:$id}) RETURN n.validFrom, n.validTo, "
        "n.validFromPrecision, n.validToPrecision",
        params={"id": point_id},
    ).result_set
    if not rows:
        return
    # EVERY matching node, not just the first: the writers MATCH the id and
    # stamp in one query, so a first-row-only read could pass the guard and
    # still leave an inconsistent declaration on a sibling (the same duplicate
    # fan-out ``_refuse_inverted_point_window`` loops over).
    for stored_vf, stored_vt, stored_fp, stored_tp in rows:
        validate_validity_precision(
            props.get("validFrom", stored_vf),
            props.get("validTo", stored_vt),
            props.get(FROM_PRECISION_PROP, stored_fp),
            props.get(TO_PRECISION_PROP, stored_tp),
        )


#: Sentinel returned by ``_supersede_window_start_source`` when no READABLE
#: stored start exists, so the writer's own clock (``now``) decides the end.
_WRITER_CLOCK = object()


def _supersede_window_start_source(stored_vf, successor_created_at):
    """The stored successor window START ``_supersede_window_end`` will use, or
    ``_WRITER_CLOCK`` when it falls through to the writer's ``now``.

    THE ONE HOME for this predicate (#3985, #5360, #9729): THREE callers
    resolve through it — the writer (``_supersede_window_end``), the hosted
    pre-write mirror (``hosted_api._prevalidate_supersede_window``), and
    ``supersede_point``'s precision-inheritance gate, so the PREDICATE cannot
    drift. The mirror derives its INPUTS on its own, separately positioned read
    (the #7000 class); the writer and the gate share ONE read, so they cannot
    see different rows. The mirror's whole question — "does the resolution land
    on the writer's clock?" — is ``is _WRITER_CLOCK``. A duplicated predicate
    drifted once already (the mirror kept a truthiness test after #3985 made a
    falsey stored start resolvable) and deferred a resolution the writer takes:
    the successor was minted and the boundary then refused it, the #5363
    orphan.

    RETURN CONTRACT: this function returns one of its ARGUMENTS by identity —
    the ``stored_vf`` or ``successor_created_at`` object itself, never a copy or
    a normalized value. The first two callers never compare that identity: the
    mirror only asks ``is _WRITER_CLOCK`` and the writer stamps the returned
    value. The #9729 gate does compare it (``is stored_vf``) to decide whether
    the predecessor's END came from the successor's own start, and may
    therefore inherit its declared precision — so a normalization would go
    silent THERE ALONE: the END would lose the declaration with no error
    anywhere. Keep the identity when this predicate changes.

    The predicate is PRESENT (not truthy) AND ORDERABLE:

    * PRESENT, not truthy (#3985): the read path gates its start on presence
      AND orderability (``_covers``), so a numeric ``0`` is a REAL window start
      there — the
      epoch-0 instant. A truthiness test skipped it and fell back to
      ``successor_created_at``, which lands INSIDE the read path's
      ``[epoch 0, inf)`` window -> the predecessor and the successor then both
      cover -> a 2-candidate ``ambiguous`` answer. The ``is not None`` conjunct
      is kept as the explicit PRESENCE marker: inert while
      ``_created_sort_key(None)`` stays unorderable, load-bearing if it ever
      changes.
    * ORDERABLE (#5360): a stored start that is present but unorderable
      ("not-a-date", "TBD") names no instant, and persisting it would leave the
      window unbounded — ``_created_sort_key("not-a-date")`` is ``(1, text)``,
      so the #4021 inversion guard's ``end >= start`` conjunct is trivially
      satisfied while ``_covers`` cannot exclude the window either
      (``(1, x) < (0, y)`` is False). Falling through to
      ``successor_created_at`` is the SAME treatment a falsey-AND-UNORDERABLE
      stored start gets, and matches this module's own policy for the identical
      shape on the predecessor side ("SKIPPED, not refused"): ``create_point``
      accepts any caller ``validFrom``, so refusing would make a legacy point
      impossible to supersede until its window was repaired.

    ``successor_created_at`` is itself a normal caller prop (``create_point``'s
    comments record api.py, ingest.py and the source-inheritance path all pass
    one), so an unparseable ``createdAt`` is reachable through the public API
    and gets the same ORDERABLE test — it falls to ``now``, which is always ISO.
    """
    from .search_engine import _created_sort_key  # lazy — no cycle; deferred to keep sdk light
    if stored_vf is not None and _created_sort_key(stored_vf)[0] == 0:
        return stored_vf
    if successor_created_at and _created_sort_key(successor_created_at)[0] == 0:
        return successor_created_at
    return _WRITER_CLOCK


def _supersede_window_end(*, old_id, new_id, old_vfs, valid_from,
                          stored_vf, successor_created_at, now,
                          skip_inversion_guard: bool = False):
    """Resolve the predecessor's window END and refuse an inverted window (#4021).

    #4021 — the supersession stamped ``old.validTo`` from the SUCCESSOR's
    window start and never read the predecessor's own ``validFrom``, so a
    successor dated earlier than its predecessor persisted
    ``validTo < validFrom``.  ``get_historical_knowledge``'s ``_covers``
    (``sdk.py``) then rejects an inverted window for EVERY query instant, so
    the predecessor became unreachable from every read surface with no error
    anywhere — a silent, permanent loss of the old fact.

    Resolution order is the same FOUR SOURCES in the same order — the
    ``stored_vf`` predicate was WIDENED from truthy to presence (#3985) with
    its orderability conjunct kept (#5360), so the step list is not unchanged::

        str(valid_from) [a STRING must name an instant; a numeric is exempt]
                        -> stored_vf (PRESENT and ORDERABLE — #5360 + #3985)
                        -> successor_created_at (TRUTHY and ORDERABLE - #5360)
                        -> now

    ``stored_vf`` is taken when it is PRESENT and ORDERABLE. PRESENT, not
    truthy: ``_covers`` gates its start on presence AND orderability, so a
    numeric ``0`` is a real window start there — the epoch-0 instant (#3985).
    ORDERABLE, because a present-but-unparseable start carries no instant and
    persisting it would leave the window unbounded (#5360). ``successor_created_at``
    is taken only when it is truthy and ORDERABLE (#5360); otherwise the resolver
    falls to ``now``.

    #3985 changed exactly ONE case: a stored start that is FALSEY but
    orderable (``0``). The old truthiness test skipped it and fell back to
    ``successor_created_at``, which lands INSIDE the read path's
    ``[epoch 0, ∞)`` window: the predecessor and the successor then both cover
    ⇒ a 2-candidate ``ambiguous`` answer instead of the successor.

    Presence is NOT enough on its own: ``is not None`` alone would also absorb
    the falsey-but-UNPARSEABLE ``""``, stamping the predecessor's end ``""`` —
    a value that names no instant, which ``_covers`` cannot exclude, so the
    predecessor would cover every later instant and the successor would never
    become the answer. The orderability conjunct is what keeps that out. A
    truthy-but-unparseable stored start (``"TBD"``) also falls through, to
    ``successor_created_at``. (Under the #6140 ruling this writer and
    ``_covers`` treat ``""`` the SAME way — as undated — so the two paths no
    longer disagree about it; what stays closed here is persisting a valueless
    END.)

    Returns the value AS PERSISTED.  The ``stored_vf`` branches stay RAW (no
    ``str()``): a numeric stored value must keep keying as ``(0, float)``
    (``_created_sort_key`` documents numeric epochs as supported and seeded
    corpora carry them) — passing it through ``str()`` makes it unparseable
    ``(1, text)`` and REINTRODUCES the unbounded predecessor window the
    ``valid_from``-agreement guard exists to prevent.

    Refuses when ANY of the predecessor's own starts is present, ORDERABLE and
    sorts STRICTLY BEFORE the successor's resolved start — the same measure
    ``_covers`` orders with, and the same reach: a bound that names no instant
    is skipped on BOTH sides (#6140's ruling makes such a start an OPEN start on
    the read path), so the guard's boundary IS the read path's.  Strictly-before only: equality (a
    zero-length predecessor window) is well-formed and accepted.  ``old_vfs``
    is a SEQUENCE, not a scalar: a point id is not unique (the duplicate
    fan-out is a tested shape), and the stamp block below MATCHes and stamps
    EVERY node carrying the id, so reading only the first row would pass the
    guard and still leave an inverted window on a sibling node — and the
    verdict would then depend on server row order.  ``_assert_window_start_
    not_inverted`` (the #5358 sibling) documents and loops over the same
    fan-out; this mirrors it.

    The refusal fires only when BOTH instants are orderable.  A predecessor
    start that is present-but-unparseable (``""``, ``"TBD"``, ``"2026-6-1"``)
    is SKIPPED, not refused — matching ``_assert_window_start_not_inverted``,
    the #5358 sibling, which documents the same choice: the refusal's premise
    is "this end PRECEDES the predecessor's start", and an unorderable start
    names no instant to precede, so a refusal would rest on an
    ordering-fallback artefact rather than a comparison.  (Under the #6140
    ruling such a start is an OPEN start, so this write DOES bound a window
    that was previously unbounded-below — that is the supersession doing its
    job, not a fact being newly hidden.)  It also matters
    practically: ``create_point`` accepts any caller ``validFrom``, so a
    legacy or imported point carrying a non-ISO start is reachable, and
    refusing here would make such a point impossible to supersede until its
    window was repaired — foreclosing the very write a caller would use to
    move past it.  (An unparseable resolved END was #5360's residual; it is
    closed below on every route reachable under the declared ``str | None``
    type plus the numeric-epoch extension — the numeric kwarg being the
    deliberate exemption: the no-kwarg STORED-start branch requires
    orderability, a STRING ``valid_from`` kwarg that names no instant falls
    through, and the ``createdAt`` fallback likewise requires an orderable
    value and otherwise lands on ``now``. So a value naming no instant becomes
    an orderable instant rather than an end no query can order — see the branch
    comments below.)

    ``skip_inversion_guard`` (#2350): the caller is stamping NO end at all
    (``valid_to_precision='unknown'`` — the closed-unknown marker, which
    persists ``validTo = None``). The guard's own premise is "persisting
    ``succ_vf`` would leave an inverted window", and that premise is false of
    a write that stamps no end — so it is skipped, exactly as
    ``invalidate_point`` skips its #5358 sibling guard in the same case. The
    resolved ``succ_vf`` is still RETURNED (it rides the record's
    informational ``valid_from``), only the refusal is suppressed.

    Raises ``ValueError`` BEFORE any mutation at either call site, so no
    event is journaled and no half-write survives a refusal.

    Scope: this refuses the **inverted** direction.  The resolution of an
    **unparseable** end is handled in the resolution block below (#5360): the
    no-kwarg STORED-start branch requires orderability, and a STRING
    ``valid_from`` kwarg that names no instant falls through to
    ``successor_created_at`` rather than being persisted. A NUMERIC kwarg is
    deliberately exempt there.

    #6140 CLOSED this residual (owner ruling 2026-10-11, "A and C"): the two
    paths now AGREE on ``""``, without a semantic choice being forced here.
    The read path treats a start that names no instant as NO START RECORDED (an
    OPEN start), and this writer already treats it as absent — it falls through
    to ``successor_created_at``, exactly as an absent start does. Both sides
    therefore read ``""`` as "undated": no divergence to align, and the write
    clock is not borrowed in the sense §4.7 forbids, because the value carried
    no instant to begin with. What #3985 fixes is the other case — a falsey
    start both paths CAN order (``0``).

    The ``valid_from``-vs-successor agreement guard is NOT here: it is
    reachable only when a kwarg is passed, and it stays inline at its
    reviewed call site.
    """
    from .search_engine import _created_sort_key  # lazy — no cycle; deferred to keep sdk light
    if valid_from is not None and not (
        # #5360 — a STRING kwarg that names no instant must not be persisted as
        # the end either. A NUMERIC kwarg is deliberately EXEMPT from this and
        # is passed through to ``str(valid_from)`` as before: that string keys
        # ``(1, text)`` (no ``-``/``T``), and
        # ``test_window_end_numeric_kwarg_resolved_before_measure`` (B3) pins
        # that the guard measures that RESOLVED value, so the numeric case is
        # NOT an inversion. Gating on ``isinstance(valid_from, str)`` keeps B3's
        # decision intact — a blanket orderability test here, applied to
        # numbers too, replaces the numeric kwarg's end and reddens B3.
        isinstance(valid_from, str)
        and _created_sort_key(valid_from)[0] != 0
    ):
        succ_vf = str(valid_from)
    else:
        _resolved = _supersede_window_start_source(
            stored_vf, successor_created_at)
        # ORDERABILITY is decided IN ``_supersede_window_start_source``, and NOT
        # as a normalisation of the resolved value below, because such a
        # normalisation would reverse a deliberate, pinned decision: a numeric
        # ``valid_from`` kwarg is persisted as ``str(valid_from)``, which keys
        # ``(1, text)`` (no ``-``/``T``), and
        # ``test_window_end_numeric_kwarg_resolved_before_measure`` (B3) pins
        # that the guard measures that RESOLVED value — so the numeric case is
        # NOT an inversion.
        succ_vf = now if _resolved is _WRITER_CLOCK else _resolved
    if old_vfs and not skip_inversion_guard:
        k_succ = _created_sort_key(succ_vf)
        # A NUMERIC KWARG can still resolve unparseable here and is
        # DELIBERATELY left that way (see above and B3): the guard measures the
        # value the writer persists, and an unparseable resolved value is
        # skipped rather than compared, so it is not an inversion. Every other
        # route yields an orderable ``succ_vf`` — #5360 closed the no-kwarg
        # STORED-start route, the string-kwarg route, and the ``createdAt``
        # fallback.
        if k_succ[0] == 0:
            for old_vf in old_vfs:
                if old_vf is None:
                    continue
                k_old = _created_sort_key(old_vf)
                if k_old[0] != 0:
                    # Present but unorderable — skipped, NOT refused, and
                    # with no message: it buckets LAST as ``(1, text)``, so
                    # comparing it would fire for ANY successor on an
                    # ordering-fallback artefact. The #5358 sibling proceeds
                    # on this identical shape; two guards in one file must
                    # not return opposite verdicts for one input.
                    continue
                if k_succ[1] < k_old[1]:
                    # Wording is pinned by two tests: the literal substring
                    # "inverted window", and scrub-stability under
                    # mcp_server._scrub_error, whose `(host=|at |to )[\w.-]+`
                    # rule rewrites any word ending in at/to followed by a
                    # space — a scrubbed hint reaches the caller as `***`.
                    raise InvertedSupersedeWindow(
                        f"supersede_point: refusing supersede {old_id!r} - "
                        f"{new_id!r} - the successor's window start "
                        f"{succ_vf!r} precedes the predecessor's validFrom "
                        f"{old_vf!r}; persisting it would leave an inverted "
                        f"window (validTo < validFrom), which no query "
                        f"instant resolves. Give the successor a validFrom "
                        f"on-or-after {old_vf!r}, or use `retract_point()` "
                        f"(window-agnostic) for withdrawal of the predecessor",
                        old_id=old_id, new_id=new_id,
                        successor_start=succ_vf,
                        predecessor_valid_from=old_vf,
                    )
    return succ_vf


def _installed_namespaces_for_gate(sdk) -> frozenset[str] | None:
    """#5163: the graph's installed-pack set for the SDK's LOCAL compile
    gates — the v1 objectKind enforcer (``validate_summary``) and the
    client-side Layer-1 pre-check (``validate_payload_dict``).

    ``None`` = no gate (the catalog union) — the documented back-compat value
    for a graph with no ``:PackInstall`` records, and for an SDK that carries
    no graph handle at all (never ``__init__``-ed — the test seams that build
    a summary path with ``object.__new__``). A graph that IS bound but
    unreachable RAISES out of ``graph_installed_namespaces`` — an outage must
    never read as "no packs" and silently widen the gate to the union.
    """
    if not hasattr(sdk, "_proj"):
        return None
    from tortoise.pack_state import graph_installed_namespaces
    return graph_installed_namespaces(sdk)


def _read_stored_number(value, default):
    """Coerce a STORED numeric property for display/sorting; never raise (#6151).

    ``annotator_*`` and ``mitigation_strength`` are writable through the generic
    entity surface, which does not validate, so a stored string or an int past
    float range reaches the readers. ``weights.compute_operator_weight`` degrades
    such a value to a harmless default rather than aborting the run, and the state
    readers must hold the SAME contract: they read the same two properties, and
    `recall_state` is an agent-facing path, so one malformed row would otherwise
    make every state query touching that operator raise while the weight read on
    the identical input succeeded.

    ``default`` is the caller's own "nothing usable here" value, because the two
    sites disagree on it on purpose: a rating falls back to the neutral term (the
    value an unrated operator yields), while a mitigation strength falls back to
    ``None`` (the value an absent strength yields).
    """
    try:
        return float(value)
    except Exception:
        return default


def _ledger_record(label: str, rows: list[dict]) -> None:
    """#7983: feed the READ LEDGER (the instrument for the hot fraction `f`).

    Called from EVERY caller-facing row-returning read path that has a known
    LABEL. The named set is carried by §17.4.6, not duplicated here — a case
    where it was duplicated: this docstring once named only `query()`,
    `paginated_query()` and `query_points_by_tag()` while omitting six callers
    that were already here. An audit surface that under-reports its own call
    sites invites a second hook at one of them, which would record a candidate
    POOL as read and bias `f` HIGH — the direction that HURTS the split, since
    the saving is `(1 - f) x cache` and a higher `f` removes it. (`f` divides
    distinct read KEYS, so a duplicate hook moves `read_count`, not `f`; the
    mechanism is the pool, not the double count.)
    ⛔ NOT `get_point()` — it is the internal accessor the WRITE paths use
    (`_emit_event` and the dedupe/promote checks), so counting it would mark
    written-but-never-read nodes as read and bias `f` HIGH (#8877). The FTS
    funnel records its rows through the same module INLINE (it resolves the
    label per `entity_type`), so it is covered without calling this helper. ⛔ It is NOT every row-returning path:
    `traverse` and `get_historical_knowledge` read by
    direct Cypher and call nothing here (`recall_state`'s RELATED readers are
    likewise unhooked — the method itself does record the rows it serves), and
    the FTS funnel records only its
    RETURNED rows, not its candidate pool — so a deployment serving those paths
    reads an `f` that is too LOW (which flatters the split). #8877 carries the
    wiring; the coverage gaps are named in STORAGE-ARCHITECTURE.md §17.4.6.

    OFF by default and never imported when off: one env lookup (the #4097 truthy
    contract), and a ledger failure is swallowed inside the module (a measurement
    may not break the thing it measures).
    """
    if not rows or not is_truthy(os.environ.get("TORTOISE_READ_LEDGER")):
        return
    try:
        from . import read_ledger
    except Exception:  # pragma: no cover — a broken install must not break a read
        return

    read_ledger.record_rows(label, rows)


#: ``entity_type`` values that resolve to a ``:Point`` read — the only rows
#: that can carry ``claimClass`` (#2351). ``operator`` is a Point by predicate
#: and ``point`` is the ``:Point`` label itself (``graph_label`` in
#: ``tortoise_fts_query``: ``"Point" if entity_type == "operator" else
#: label``); every other value names a different label (Event / Subject /
#: Object / Source) that has no such property.
_CLAIM_CLASS_POINT_TYPES: frozenset[str] = frozenset({"point", "operator"})


class TortoiseSDK:
    """Layer 1 facade for Tortoise epistemic graph interaction.

    Args:
        db_path: Optional path to FalkorDBLite database file (None = use TORTOISE_DB_URI env var).
        namespace: Optional namespace for graph-name isolation.
        event_log_path: Optional path to JSONL event log. When set, SDK write
            paths append events so rebuild_all can restore SDK-created points
            (#548), Events (#2061), Objects (#2194), and Subjects (#2295).
            If None, no events are emitted (backward-compatible).

    Precedence: an explicitly-provided db_path wins over the TORTOISE_DB_URI
    env var. This lets tests/fixtures force a temp embedded DB even when a
    shared test URI is set in the environment (#139).
    """

    def __getattr__(self, name: str):
        """A retired public SDK name fails with its replacement named (#3836 (c)).

        Reached ONLY when normal attribute lookup fails, so it can never shadow a real
        method. The dunder is private, so it adds nothing to the public surface
        (`tools/sdk_surface.py` counts public names on the class).

        A name that was never public keeps the interpreter's own shape — only a name
        the owner actually retired gets the migration hint.
        """
        hint = retirement_hint(name)
        if hint is not None:
            raise AttributeError(hint)
        raise AttributeError(
            f"'{type(self).__name__}' object has no attribute '{name}'"
        )

    def __init__(self, db_path: str | None = None, *, namespace: str | None = None,
                 event_log_path: str | None = None,
                 graph_name: str | None = None):
        import os, re  # noqa: E401, I001
        db_uri = os.environ.get("TORTOISE_DB_URI")
        if db_uri and db_path is None:
            self._db_path = None
            self._db_uri = db_uri
        else:
            # P0: Crash early if running in production with no database configured.
            # Embedded redislite has no persistent volume → all data lost on deploy.
            # Must evaluate BEFORE resolve_db_path() fills in the default.
            if not db_uri and not db_path:  # noqa: SIM102
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
        if namespace is not None:  # noqa: SIM102
            if not re.match(r'^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$', namespace):
                raise ValueError(
                    f"Invalid namespace {namespace!r}. "
                    "Use alphanumeric, hyphens, underscores; max 64 chars."
                )
        self._namespace = namespace
        # C5 #2114 (D-C5-1): explicit FULL graph-name override — the data-plane
        # tenancy seam. Custom graphs (org_{tid}_{gid}) cannot be expressed
        # through ``namespace`` (the _get_proj derivation would prepend
        # ``org_`` → ``org_org_{tid}_{gid}``). When set, _get_proj binds the
        # projection to ``graph_name`` VERBATIM (default-graph callers never
        # pass it — they keep the namespace derivation, byte-identical).
        # Charset mirrors the namespace rule (+128 len for org_{tid}_{gid});
        # mutually exclusive with ``namespace`` by construction (the spine
        # passes exactly one).
        if graph_name is not None and not re.match(
                r'^[a-zA-Z0-9][a-zA-Z0-9_-]{0,127}$', graph_name):
            raise ValueError(
                f"Invalid graph_name {graph_name!r}. "
                "Use alphanumeric, hyphens, underscores; max 128 chars."
            )
        self._graph_name = graph_name
        self._event_log_path = event_log_path
        self._event_log = None  # lazy-init EventLog (#548)
        # ── the record's tenant for THIS SDK's writes (#9188) ───────────
        # The Postgres record files every row under `(org_id, graph_id)`, and
        # the tenant is normally ambient (`record_writer.use_record_tenant`,
        # installed per request by the MCP lane's middleware). The hosted REST
        # lane BINDS it here instead, and the reason is CONFINEMENT, not
        # propagation (measured: a contextvar set on the loop IS copied into
        # `asyncio.to_thread` / `_graph_offload`). `_data_sdk` — the only place
        # that lane resolves a tenant — runs INSIDE `_graph_offload` for the
        # capture path, so a scope entered there dies with the context copy; and
        # setting it loop-side would mean setting it WITH NO EXIT, where every
        # detached task and every other write door in that context inherits it.
        # So the value travels on the SDK the write runs on: this request's own
        # answer, unable to escape into a concurrent one. `None` (every other
        # lane) is the historical behaviour, byte-identical.
        self._record_tenant: tuple[str, str] | None = None
        # #4240 review F1: count journal appends that failed on a CONFIGURED
        # journal. A configured journal that cannot record makes the derived
        # graph live-only, so a failure is a real degradation — it is counted
        # (monitoring.record_journal_write_failure) and disclosed on the next
        # capture receipt instead of being only a log line.
        self._journal_write_failures = 0
        # Epic #900 §5.3 (cycle-21): cross-process embedded overlap probe —
        # fail-fast when another PROCESS holds this embedded store (redislite
        # pid-registry + liveness probe). Same-process threads reuse the daemon
        # by construction — the process-local opened-set passes them (the
        # registry records the DAEMON pid, which is never our own python pid).
        if self._db_uri is None and self._db_path:
            self._probe_embedded_busy(self._db_path)
            _mark_embedded_opened(self._db_path)
        self._proj: FalkorProjection | None = None
        self._ep = None  # lazy-init TortoiseEP
        self._evidence: dict[str, tuple[float, float]] = {}
        self._registry_g = None
        self._audit_logger = None
        # Issue #1005 (lifecycle): idempotent close + context-manager support
        # + atexit registration so a NORMAL process exit never orphans the
        # embedded server (the dominant leak path — sessions ending without
        # closing). No weakref.finalize: the atexit bound method keeps the
        # SDK alive until exit, so a GC finalizer could never fire.
        self._t_closed = False
        # #1371: route the atexit seam through the fast-close wrapper (see
        # FalkorDB._atexit_close). _t_close/close/__exit__ are unchanged.
        # #1475 review (P2): register through the embedded_lifecycle wrapper —
        # a bare weakref.WeakMethod is a silent NO-OP (WeakMethod.__call__
        # RETURNS the bound method instead of invoking it, so atexit never
        # runs the body). The wrapper derefs a plain weakref and invokes the
        # method only while the object is alive, keeping the SDK collectable
        # (close-on-GC) without pinning it.
        from .embedded_lifecycle import register_atexit_close
        register_atexit_close(self)
        # Dreaming (#85): dirty claim roots awaiting EP stabilization. Write
        # paths mark affected claims dirty; dream()/lazy-read consume them.
        self._dirty_roots: set[str] = set()
        self._dreamer = None  # lazy-init Dreamer
        # Epic 903-C5 (#1243): non-convergence retention state. A failed
        # pass KEEPS the affected claim-roots dirty (W4 — retry, never
        # silently drop); attempts are counted per root and capped; capped
        # roots are dropped from the dirty set and surfaced as
        # ``stale_unresolved`` (region_attempts record — consumed by the
        # 903-C7 health surface). Backoff state is recorded, not slept —
        # enforcement uses the injectable clock (tests pass a fake).
        self._retry_attempts: dict[str, int] = {}
        self._retry_backoff_until: dict[str, float] = {}
        self._stale_unresolved: dict[str, dict] = {}
        self.retry_attempt_cap: int = 3
        self._retry_clock = _monotonic  # injectable for deterministic tests
        # Epic 903-C7 (#1245): per-pass observability record — the zero-
        # output alarm's inputs + the hosted /v1/dream/health surface
        # (I5 field set). Updated by the dream adapters.
        self._dream_metrics: dict = {
            "last_pass_at": None,
            "last_pass_output": 0,
            "last_pass_mode": None,
            # #3139: why the last pass did no EP work — None (work done),
            # "no_ep_factors" (legitimately empty window), "no_dirty_roots",
            # "budget_zero", "stale_run_guard", or "silent_no_op" (the pass
            # raised DreamNoOpError). A silent no-op raises instead of
            # landing in a success shape.
            "last_pass_no_op_reason": None,
            # #3139 (review P1): belief state written by the last pass, as
            # distinct from ``last_pass_output`` (which counts stamps).
            "last_belief_write_count": None,
            "per_mode_counts": {},
            "pass_count": 0,
            "failure_count": 0,
        }

    def _probe_embedded_busy(self, db_path: str) -> None:
        """Epic #900 §5.3: fail-fast on cross-process embedded overlap.

        Reads the redislite daemon registry (``<db_path>.settings`` → the
        recorded ``pidfile``) and liveness-probes the recorded pid: a LIVE
        holder that is NOT this process raises ``EmbeddedStoreBusyError``
        (naming db_path + holder pid) — never a silent second daemon
        (two in-memory copies = split-brain on the default embedded topology).
        Same-process opens (paths in ``_embedded_busy_known`` — threads reuse
        the daemon by construction) and dead-pid leftovers (crash residue)
        pass.
        """
        import os as _os  # noqa: I001
        import json as _json
        from pathlib import Path as _Path
        if not db_path or str(db_path) == ":memory:":
            return
        if str(db_path) in _embedded_busy_known:
            return  # this process already owns/holds the daemon — threads reuse
        registry = _Path(db_path).with_name(_Path(db_path).name + ".settings")
        if not registry.is_file():
            return
        try:
            settings = _json.loads(registry.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001, RUF100
            return
        pidfile = settings.get("pidfile")
        if not pidfile or not _os.path.isfile(str(pidfile)):
            return
        try:
            pid = int(_Path(pidfile).read_text().strip())
        except Exception:  # noqa: BLE001, RUF100
            return
        if pid <= 0:
            return
        try:
            _os.kill(pid, 0)  # liveness probe
        except ProcessLookupError:
            return  # dead daemon (crash residue) — safe to open
        except PermissionError:
            pass  # exists but not ours to signal — still a live holder
        from tortoise.exceptions import EmbeddedStoreBusyError
        raise EmbeddedStoreBusyError(db_path, pid)

    def _graph_presence(self, proj) -> bool | None:
        """Whether the graph a wrapped write targets still has its KEY (#7685).

        TRI-STATE, so the two callers can ask two different questions of it:
        the retry gate needs a boolean (anything but ``True`` is "do not
        retry"), while the exhaustion message (#7357) must not claim the graph
        was DELETED when the probe merely could not ANSWER — an unresponsive
        instance is the measured state of this defect.

        The measured discriminator for the graph-abort family: after a rebuild's
        recreate ``EXISTS <name>`` is 1, after a plain deletion it is 0 (measured
        live on the v6 core, ver 60001, and the C core, ver 42004/42006 — the
        CI docker lane's image). ``retryable_aborted_write`` needs it because the
        engine's abort text cannot tell the two apart — on v6 it is literally
        one sentence — and a re-issued ``CREATE`` would otherwise land in a graph
        FalkorDB auto-created EMPTY and be reported as SUCCESS.

        This is PRESENCE, not identity: a rebuild that recreated the key and then
        failed mid-replay reads 1 and is retried (that is the rebuild's outcome,
        not the retry's). Separating the two fully would need an engine-level
        identity token — out of scope (#7685's escalation line).

        Raw ``EXISTS`` deliberately, rather than
        ``graph_delete_guard.graph_exists`` (which pages ``GRAPH.LIST``): this is
        a hot-path, per-retry probe and ``EXISTS`` is the O(1) key check, measured
        to agree with the presence semantics the guard's callers rely on.

        Fails LOUD: no projection, no graph name, or a probe that raises all
        answer ``None`` — i.e. "do not retry" through ``_graph_exists``, never
        "assume a rebuild".
        """
        if proj is None:
            return None
        name = getattr(proj, "graph_name", None)
        if not name:
            return None
        try:
            return bool(proj.g.execute_command("EXISTS", str(name)))
        except Exception:  # noqa: BLE001, RUF100 — a failed probe is not a rebuild
            _logger.warning(
                "graph-existence probe failed for %r; the write is not "
                "retried and any #7357 exhaustion message reports the "
                "graph state as UNKNOWN (a silent success is worse than a "
                "loud miss)",
                name, exc_info=True)
            return None

    def _graph_exists(self, proj) -> bool:
        """The retry gate's boolean view of :meth:`_graph_presence` (#7685).

        Anything but a confirmed presence answers False: this gates a re-issued
        bare ``CREATE``, so a false positive is a silent success into an empty
        graph, while a false negative is only a loud miss.
        """
        return self._graph_presence(proj) is True

    def _graph_write_with_retry(self, fn, *, what: str, witness=None):
        """Issue a DIRECT graph write through the bounded-retry primitive (#7405).

        A rebuild/replace aborts an IN-FLIGHT query with
        ``ResponseError("graph was deleted or replaced while the query was
        running, aborting")`` (v6 core), or ``Encountered different graph value
        when opened key <name>`` (C core). Measured against THIS client
        (``falkordb``), that abort is a **per-query race, not a poisoned
        handle**: ``Graph`` is stateless — it holds only ``name`` +
        ``execute_command`` and re-issues ``GRAPH.QUERY <name>`` on every call —
        so the SAME cached handle and client succeed on a plain re-issue
        (reviewer reproduced: abort, then retry on the same handle, one write,
        no duplicate). No re-resolution is needed, and an earlier ``on_retry``
        cache-drop was REMOVED: it rebuilt the whole ``FalkorProjection``
        (connection pool + embedding warm-up) for nothing, and its test
        assertion held with or without the drop.

        **#7685 — the retry must not mask a DELETION.** The engine reports the
        same abort for a rebuild and for a plain deletion, and FalkorDB
        auto-creates a graph on write, so an unqualified retry reported SUCCESS
        for a genuine deletion — into a fresh EMPTY graph (#6666 class). The
        predicate is therefore state-gated: the graph-abort family is retried
        ONLY while the graph key is present (``_graph_exists``), and a deletion
        raises LOUD. **Scope of that guarantee:** it covers a deletion that lands
        while the write is IN FLIGHT (the abort path). A deletion that completes
        BEFORE the write is issued produces no engine error at all — there is
        nothing for a predicate to see — and the write auto-creates the graph;
        that interleaving is not this retry's to guard (#7685). This also fixes
        the C core (the documented default lane and
        the image the CI docker lane provisions), whose rebuild literal the #7615
        predicate did not match at all — for a rebuild whose recreate has landed
        before the abort is classified. A slower C-core rebuild, still inside its
        delete window, reads ``EXISTS 0`` and stays LOUD by design (pre-fix it
        also raised, so that is not a regression).

        What this buys, stated honestly: a rebuild window of roughly 7-14 s now
        heals in place (``base=2.0``, 3 retries). The window is per CALL, not
        per verb: the whole ``fn`` is re-issued together, so a caller that
        issues several statements inside one ``fn`` (``update_point``'s
        per-key loop, ``_sync_tags``) shares that one budget. A verb that
        reaches several wrapped call sites pays one window PER SITE, so its
        caller-visible bound is one window x the number of sites that branch
        reaches. ``update_point`` is the widest, and the count is MEASURED
        rather than enumerated by hand (#8854 review P2 — the earlier "up to
        three" omitted ``_mark_dirty``, which it runs on every path that
        reaches the tail; a status-only promote on a live node returns before
        it, so it is not "always"). On a ``:Point``:
        THREE to FOUR for ``update_point(confidence=...)``; FOUR to FIVE for
        ``update_point(tags=[...])``; FIVE to SIX for
        ``update_point(content=..., tags=[...])``. A ``:Point:Object`` node
        reaches ONE FEWER on the LAST shape (measured 4 LIVE) because its single
        ``update_point SET (Object)`` arm replaces the ``:Point`` arm's pair
        (per-key + embedding); the first two shapes are the same on both. The
        low end is a LIVE point whose dirty ids ALL persist; the high end adds
        ``_mark_dirty``'s TERMINAL branch
        (``_mark_dirty terminal ep_dirty clear SET``), which runs when ANY
        existing dirty id fails the persist ``WHERE`` — the updated point
        itself OR one of its 1-hop reverse-BFS neighbors. Measured: a LIVE
        point whose operator targets a retracted claim pays it, so "the caller
        updates a terminal point" is NOT the trigger and the high end is not
        exclusive to terminal targets. At 7-14 s per window that is roughly
        21-84 s, not 21-42 s. The
        window #7405 measured lasted **over an hour**, and no bounded retry
        should wait that long — so on exhaustion the original error still
        surfaces, **unchanged in type**. Its MESSAGE gains one appended
        diagnosis when it is the graph-swap family (#7357): the same
        ``ResponseError``, with the engine's sentence kept at position 0 so the
        anchored predicate still classifies it, followed by `` — what this
        means: …`` naming the swap rather than implying data loss. A DELETION
        (key absent) is never told a retry is safe — see
        ``retry.explain_graph_abort``.

        Residual window, stated honestly. The classification-time probe can be
        stale by the time the retry runs — ``call_with_predicate`` evaluates the
        predicate BEFORE it sleeps, so the backoff is 1-8 s — so the re-issue is
        probed AGAIN immediately before the write (``_issue`` raises the captured
        abort when the key is absent). That shrinks the exposed window from the
        backoff to the probe→``GRAPH.QUERY`` round trip (the ``falkordb`` client
        pools connections, so the two probes may not share one).
        Closing it completely would need an identity token for the graph that the
        engine does not expose — an engine-side change, out of scope here
        (#7685's escalation line).

        ``retryable_aborted_write`` re-raises anything it does not recognise, so
        a deterministic failure (a malformed statement, ``WRONGTYPE``) is never
        retried and never wrapped in the sentinel.

        #8854 review P2: the budget is per CALL, and ``fn`` is the unit of
        retry — so the bound belongs to the caller's ``fn``, not to the verb.
        A multi-statement ``fn`` (``update_point``'s per-key ``SET`` loop,
        ``_sync_tags``' MERGE/read/DELETE sequence) is re-issued as a whole and
        shares ONE 7-14 s window; a verb that calls this several times pays
        one window per call — ``update_point``'s measured 3-4 / 4-5 / 5-6
        above is the SUM of its own sites plus the two (or three, on a
        terminal point) inside ``_mark_dirty``. Callers must
        keep the count of calls per verb deliberate rather than assuming a
        fixed bound per verb — the per-key loop is one call precisely so
        ``annotate_operator``'s four properties do not multiply it.

        ``witness`` (#8854 review P1): an optional ``result -> bool`` callback
        answering "did this statement do what it was asked to do?" — AT LEAST
        ONE for a single-target write, ALL-OF-N for a multi-target one
        (``witness=bool`` over the per-key loop: EVERY key must match; the tag
        sync: every wanted name must be present). It is
        consulted ONLY once this call has seen a GRAPH-REPLACED abort
        (``armed_abort``, latched on the first one) — that is the only family in
        which a zero-match re-issue can mean "not replayed yet" rather than
        "the caller's target never existed". The write-slot / MISCONF arms are
        retried without arming it, so their benign zero-row outcomes are
        unchanged, and a LATER one cannot disarm it. On exhaustion the abandoned
        abort is re-raised. A first attempt is never consulted, so verbs that
        return a benign empty result (``update_point`` on a missing id) keep
        that contract.

        A witness for a multi-statement ``fn`` whose own return value is not a
        landing proof (the tag arm: ``_sync() -> None``), or a witness that
        READS THE GRAPH to classify a zero row (``_promote_witness``,
        ``_retract_witness``, ``_cas_witness``), may answer that way — the only
        requirement is that it answers the question and mutates nothing. A
        witness reading the STATEMENT's result needs one to
        exist: such statements must RETURN something observable
        (``RETURN count(n)`` / ``RETURN r``), since a bare ``SET`` has no row to
        testify with.

        Two consequences of the classification being the witness's job rather
        than a fixed row rule, both shipped and pinned:
        - it may answer True on a ZERO row. That covers THREE shipped forms,
          and a zero row means different things in each: the state already
          SATISFIES the ask (the #1088 idempotent status-only promote); the
          state REFUSES the ask (the promote CAS's terminal or NULL status —
          `null = 'draft'` is null, not true — where True hands the answer to
          the caller's outcome branch instead of claiming a landing); or there
          was nothing to verify (a vacuous request, ``_sync_witness`` on an
          empty/None tag set). Returning False for the middle form is a DEFECT:
          it spends the window and surfaces the graph abort for a node that is
          present and simply unpromotable;
        - it may RAISE the caller's documented error instead of returning
          False to end the retry immediately (a predicate-FALSE exception
          abandons the window rather than spending it), which is how the
          promote and retract witnesses keep their documented ``ValueError``
          instead of surfacing the graph abort.

        Retry budget (#7405 P2-2). This inner loop owns the write refusals
        (graph-replaced / write-lock / MISCONF) with ``retries=3`` (~7-14 s) and
        is the ONLY retry for a replaced graph — ``retryable_transient`` (the
        eval's outer phase predicate) deliberately does not carry that arm, so a
        graph-replaced error is not multiplied 3x4. MISCONF IS still in the outer
        predicate (it predates #7405 and the eval's UNWRAPPED direct writes rely
        on the outer loop), so a MISCONF on a ``create_point`` write may nest;
        the outer loop engages only once this inner loop EXHAUSTS, bounding it at
        4 inner x 3 outer attempts (12).

        Observability (#7405 P1-1): the retry count is accumulated in
        ``self._graph_write_retry_count`` (private, monotonic) so a caller that
        measures retry behaviour — the eval's ``ingest_retries`` Layer-1 outcome
        — can observe a retry that happened INSIDE the SDK, not only the outer
        phase retry.

        #9350: the funnel is also used for the capture stamp's id PRE-READ
        (``_capture_stamp_targets``), so a re-issued read increments the same
        counter. That is deliberate — the counter measures *re-issues through
        this funnel*, not statements that mutate — but a caller reading it as
        "write retries" will over-count by at most the guarded-stamp reads of
        one capture, and only when a re-issue actually happened. Suppressing it
        here would hide real instability (a read that had to be re-issued after
        a GRAPH-REPLACED abort is the same defect the retry exists for).
        """
        from .retry import (
            WriteStageRetriesExhausted,
            call_with_predicate,
            explain_graph_abort,
            graph_abort_family,
            retryable_aborted_write,
        )

        # Read the projection AT PROBE TIME, not at entry: the caller resolves
        # it through `_get_proj()` before (or, in a lazy caller, on) the first
        # attempt, so `self._proj` is the live authoritative handle. A None still
        # answers False (`_graph_exists`), so the graph-abort family fails LOUD.
        attempts = 0
        # The FIRST graph-replaced abort, LATCHED (#8854 review round 3 P1).
        # A single "last abort" slot is overwritten by EVERY retryable abort, so
        # gating the witness on it let a write-slot abort that arrived AFTER a
        # rebuild abort (the rebuild is itself a concurrent writer) silently
        # DISARM a witness that had been legitimately armed — and the next
        # zero-row re-issue reported success again. Once this call has seen a
        # graph replacement, the ambiguity is live for the rest of the call.
        # That slot is gone rather than merely unused: a write-only name that
        # still looks load-bearing invites a later edit to re-raise it here,
        # which is the r7 P2 "retry the query" text against a deleted graph.
        graph_abort: BaseException | None = None
        armed_abort: BaseException | None = None

        def _probe() -> bool:
            return self._graph_exists(self._proj)

        def _issue():
            """Re-issue, refusing first when a STATE-GATED abort's graph is gone.

            For the graph-abort family only: the classification-time probe is
            stale by the time the retry runs (``call_with_predicate`` sleeps
            AFTER the predicate), so this is the last point at which a deletion
            that landed during the backoff can be refused instead of silently
            auto-creating an empty graph. Re-raising the captured abort keeps the
            caller's error type unchanged; the predicate then refuses it (the key
            is absent). The state-independent arms (write-slot / MISCONF) are NOT
            gated — their refusal has nothing to do with the graph's presence, so
            they must retry even when the key is missing.
            """
            nonlocal attempts
            attempts += 1
            if attempts > 1:
                if graph_abort is not None and not _probe():
                    # Raise the LATCHED graph abort, never the most recent one:
                    # after a write-slot abort landed, the latest abort is a
                    # message telling the caller to retry a graph that is gone
                    # (#8854 r7 P2).
                    raise graph_abort
                # Count only a retry that is actually re-issued, so the eval's
                # `ingest_retries` cannot over-report (review round 2).
                self._graph_write_retry_count = getattr(
                    self, "_graph_write_retry_count", 0) + 1
            result = fn()
            if (witness is not None and armed_abort is not None
                    and not witness(result)):
                # An authorised re-issue that matched nothing, after a
                # GRAPH-REPLACED abort — the rebuild-recreated-the-key window.
                # This is NOT a success: the rows this statement targets have
                # not been replayed. Raising keeps the loudness the pre-retry
                # code had, and the sentinel carries that graph abort so the
                # exhausted path can surface the error the caller would have
                # seen without the retry.
                #
                # The latch is load-bearing in BOTH directions: the
                # write-slot and MISCONF arms are retried too and say nothing
                # about node existence, so they must not ARM the witness
                # (`update_point(<missing id>)` keeps returning `{}`), and a
                # later one must not DISARM it either.
                raise _WriteNotLanded(armed_abort)
            return result

        def _predicate(exc: BaseException) -> bool:
            nonlocal armed_abort, graph_abort
            if isinstance(exc, _WriteNotLanded):
                # Nothing applied, so a re-issue is definitionally safe — and
                # the graph-state question was already answered when the
                # retry that produced this attempt was authorised.
                return True
            keep = retryable_aborted_write(exc, graph_exists=_probe)
            # LATCH the graph-replaced abort OBJECT (not merely a flag):
            # a later write-slot abort is state-INDEPENDENT and must not
            # re-open issuing into a graph the rebuild deleted — nor
            # rewrite the error the guard raises into a "retry the query"
            # instruction for a graph that is gone (#8854 r7 P2).
            if keep and graph_abort_family(exc):
                if graph_abort is None:
                    graph_abort = exc
                if witness is not None and armed_abort is None:
                    # `_WriteNotLanded` is intentionally NOT latched here:
                    # it is arm-neutral.
                    armed_abort = exc
            return keep

        try:
            return call_with_predicate(
                _issue, predicate=_predicate, retries=3,
                what=what, base=2.0, cap=8.0)
        except WriteStageRetriesExhausted as exc:
            # UNWRAP. The sentinel exists for the eval lane's R2 whole-question
            # marker, not for create_point's contract: surface the ORIGINAL error,
            # whose TYPE callers depend on — `_classify_db_failure` buckets the
            # redis family as "db" and anything else as "structural", so a new
            # wrapper type would silently misbucket every exhausted write.
            inner = exc.__cause__ or exc
            if isinstance(inner, _WriteNotLanded):
                # A re-issue that never landed: surface the abort that
                # authorised it, not the internal sentinel.
                inner = inner.original
            # #7357: the engine's graph-swap sentence reads as CORRUPTION
            # ("graph was deleted or replaced"), and a caller that treats it as
            # such goes hunting a rebuild on a healthy graph. A rebuild this
            # retry could not heal is exactly the case where the graph KEY is
            # still present — so append a legible diagnosis of the same type,
            # never a wrapper (see the TYPE note above). Only this family is
            # rewritten, and only once.
            # Pay the presence probe ONLY for the family that reads it:
            # write-slot and MISCONF exhaustion are state-INdependent, so
            # ``explain_graph_abort`` returns None for them and an eager
            # ``EXISTS`` round trip (plus the `_graph_presence` warning if it
            # raises) would be spent on an answer nothing consumes (#8954).
            legible = None
            if graph_abort_family(inner):
                legible = explain_graph_abort(
                    inner,
                    graph_present=self._graph_presence(self._proj),
                    attempts=attempts,
                )
            if legible is not None:
                inner.args = (legible,)
            raise inner from None

    def _get_proj(self) -> FalkorProjection:
        if self._proj is None:
            # Resolve the URI's own graph name first (used as the fallback
            # when no namespace is set — preserves the conftest per-session
            # test graph, #221).
            uri_graph: str | None = None
            if self._db_uri is not None:
                from urllib.parse import urlparse
                uri_graph = urlparse(self._db_uri).path.lstrip('/') or "tortoise"

            graph_name = _derive_graph_name(
                namespace=self._namespace, graph_name=self._graph_name,
                uri_graph=uri_graph)
            if self._db_uri is not None:
                # Multi-tenant isolation (#7886): pass the namespaced graph
                # name so tenants never share the URI's default graph.
                self._proj = FalkorProjection.from_uri(self._db_uri, graph_name=graph_name)
            else:
                self._proj = FalkorProjection(self._db_path, graph_name=graph_name)
            # #7951 §17.1: this projection is the DERIVED CACHE OF A JOURNAL
            # whenever one is configured, so it is under the single-writer
            # guard — a fold or a mutating raw-query on it is refused unless a
            # live door or a replay engine declared itself. Binding HERE arms
            # every path that reaches the projection THROUGH `_get_proj`,
            # without naming any of them. It does NOT reach a caller that
            # assigns `_proj` directly and never calls this method: the
            # docker-URI MCP lane at `mcp_server.py:985`
            # (`_sdk._proj = _proj`) is journal-less by construction, so a bind
            # there is a provable no-op (its own comment records this), and the
            # `_projection_for(target)` assignments in `__main__.py` hand the
            # SDK a projection that was never bound either.
            self._proj._bind_journal(self._event_log_path)
            # #9553 class 4 (R5): the structural-edge mints (`create_edge` and its
            # three typed wrappers, `create_about_edge`) wrote bare edges that
            # NOTHING journaled, so `rebuild_all` reported `edges: 0` for
            # `ownedBy`/`hasPart`/`dependsOn`/`wasDerivedFrom`/`references` and the
            # record's `relation` table never saw them. The engine already carries
            # them (`DirectEdgeCreated` + pass-2b) — what was missing was the
            # EMISSION, and this is it: the projection hands each edge it writes
            # back to this SDK, which journals the record. Bound HERE, beside
            # `_bind_journal`, because that is the same fact — this projection is a
            # live cache with a journal behind it.
            #
            # ⛔ CONDITIONAL ON THE JOURNAL, exactly as the record's own door is
            # (`record_writer.write_live_record`: a record appended to a SCRATCH
            # buffer must not become a row, because nothing could ever replay it).
            # With no journal the recorder is None, so the mint writes exactly what
            # it wrote before #9553 — no record, no half-carried edge.
            self._proj._bind_structural_edge_recorder(
                self._record_structural_edge if self._event_log_path else None)
            # (B) #2952: explicit embedder warm-up at ENGINE INIT —
            # best-effort, non-fatal, and non-blocking (daemon thread) so a
            # load failure surfaces ONCE here instead of masquerading as a
            # per-query _FAIL_COOLDOWN_S gap. The outcome is declared via
            # EmbeddingModel.status() and retrieval_legs(); the cooldown
            # itself is unchanged (never sticky-off).
            try:
                from .embeddings import EmbeddingModel
                EmbeddingModel.start_warm_up()
            except Exception:  # noqa: BLE001, RUF100 — warm-up is never fatal
                pass
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
        an isolated name derived from ``{ns}_{test_graph}_control_plane`` that
        carries an approved ``test_``/``tortoise_test_`` prefix (#3634) so
        parallel test runs stay independent (#135, #139) and the reaper owns
        the graph.
        """
        if self._registry_g is None:
            proj = self._get_proj()
            graph_name = getattr(proj, "graph_name", None)
            ns = self._namespace or ""
            # Epic #1647 (T7): the hyphenated test-* namespace family is
            # normalized in _get_proj (test-tiers → test_tiers_tortoise); the
            # control-plane path normalizes identically so it derives its name
            # from the SAME `ns` string. For a test graph the emitted name is
            # `{ns}_{test_graph}_control_plane`, prefixed with `test_` only when
            # it does not already start with `test_`/`tortoise_test_` (below).
            # So `test-hosted` emits
            # `test_hosted_test_hosted_tortoise_control_plane`, never
            # `test_test_hosted_...` — and that name therefore ALWAYS matches
            # `_SERVER_WIPE_PREFIXES`: `wipe_server` owns it by prefix. The
            # journal append below is the ADDITIONAL ownership record, read by
            # the journal sweep (`_SWEEP_OWNED_PREFIXES`). The shared-name
            # branch emits `{ns}_control_plane` / `control_plane`, never
            # prefixed — for `registry` and the no-namespace path those match
            # NEITHER set, but a `team_`/`org_` namespace DOES match
            # `_SWEEP_OWNED_PREFIXES` (never `_SERVER_WIPE_PREFIXES`).
            if ns.startswith("test-"):
                ns = ns.replace("-", "_")
            if graph_name and graph_name.startswith(("tortoise_test_", "test_")):
                registry_name = f"{ns}_{graph_name}_control_plane" if ns else f"{graph_name}_control_plane"
                # #3634 / epic CI-3 half 2: WITHOUT this the derived name matches
                # NEITHER ownership set, so every mint leaks one server graph
                # (E2E-7). Prepend only when absent — `test-hosted` derivatives
                # already comply, and double-prefixing breaks
                # tests/test_derived_names.py.
                if not registry_name.startswith(("test_", "tortoise_test_")):
                    registry_name = f"test_{registry_name}"
            elif ns:
                registry_name = f"{ns}_control_plane"      # shared — NEVER prefixed
            else:
                registry_name = "control_plane"            # shared — NEVER prefixed
            registry_graph = proj.db.select_graph(registry_name)
            # Epic #1647 (CI P2): the registry name derived from a TEST graph
            # must ALSO be journaled — the redirect only journals the main
            # graph, so without this a test-derived registry never reached
            # the session sweep. Journal the registry name too (product
            # writer: env-gated no-op outside a test session).
            # #3634: the journal append alone was not enough — the derived
            # name must also carry an approved test_/tortoise_test_ prefix
            # (applied above), or it matched neither ownership set and every
            # mint still LEAKED one server graph (E2E-7 GRAPH.LIST bound red
            # at 466+).
            from tortoise.projection import _journal_append_product
            _journal_append_product(registry_name)
            # #3214 (review P2): cache the handle only AFTER the append
            # succeeds. select_graph is client-side (no server call), so a
            # raise above mints nothing — but caching first left a
            # half-initialized handle whose next call would return it and skip
            # both the journal and the indexes, minting the registry graph
            # UNOWNED (the E2E-7 leak this append exists to stop).
            self._registry_g = registry_graph
            self._ensure_registry_indexes()
        return self._registry_g

    def _ensure_registry_indexes(self) -> None:
        """Create indexes on registry graph labels (idempotent)."""
        g = self._registry_g
        if g is None:
            return
        indexes = [
            ("Team", "name"),
            ("Membership", "org_id"),
            ("Membership", "user_id"),
            ("APIKey", "org_id"),
            ("APIKey", "key_hash"),
            ("APIKey", "key_prefix"),
            ("Invitation", "org_id"),
            ("Invitation", "token_hash"),
            ("SignupToken", "lookup_key"),
        ]
        for label, prop in indexes:
            try:
                g.query(f"CREATE INDEX FOR (n:{label}) ON (n.{prop})")
            except Exception:
                _logger.debug("Index may already exist: %s.%s", label, prop)

    # ── Core CRUD ─────────────────────────────────────────────────

    def _sync_tags(self, proj: FalkorProjection, pid: str, tags,
                   *, sweep_orphans: bool = False) -> None:
        """Reconcile TAGGED edges for a point against a tags value (#485).

        DELEGATES to the projection's own `_sync_tags` (#2897): live and
        replay must run the SAME routine, or the two drift into exactly the
        raw-list/replayed-edge disagreement this issue is about. The
        projection is the owner of the write; this method is kept as the SDK
        entry point so the existing call sites (and their
        `_graph_write_with_retry` wrapping) are unchanged.

        `sweep_orphans` is forwarded verbatim: `update_point` sets it FROM THE
        SECOND ATTEMPT ON because a retried reconciler can skip the orphan-
        `:Tag` GC otherwise (#8854 review P3 — see the projection method's
        docstring).
        """
        proj._sync_tags(pid, tags, sweep_orphans=sweep_orphans)

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
        except Exception:  # noqa: BLE001, RUF100
            raise ValueError("invalid cursor") from None

    def poll_events(self, after: str | None = None, types: list[str] | None = None,
                    limit: int = 100) -> dict:
        """Poll graph/claim events after a cursor (at-least-once; idempotent on replay).

        Returns {"events": [payload dicts ordered by seq], "next_cursor": opaque}.
        after=None → tail (oldest retained). Expired cursor → ValueError(
        'cursor expired — replay from tail'); malformed → ValueError('invalid cursor').
        Types are validated against the EventCodec registry (unknown → ValueError).
        Events live in THIS SDK's graph namespace (the org partition).

        #9208: when a change-feed write for THIS graph has been REFUSED or lost,
        this REFUSES (``event_store.ChangeFeedDegraded``) instead of returning a
        page. A short page is not a distinguishable answer: a subscriber cannot
        read it as "the stream is broken" rather than "nothing happened", which
        is how a dead `:GraphEvent` feed used to report success. The state is
        keyed by store+graph and lives in process memory (a peer process cannot
        see it, and the store is exactly where the notice could not be written);
        it is cleared only by calling ``event_store.clear_degraded(<graph>)``
        INSIDE THAT PROCESS. There is no REST/MCP/CLI acknowledgement surface
        yet, so the raise says so rather than prescribing to an HTTP client a
        call it cannot make, and ``event_store.degraded_graphs()`` is what
        ``GET /health`` reports it from (``change_feed``).

        #2353: when ``TORTOISE_CHANGE_FEED`` is truthy the response ALSO carries
        ``changes`` — the same page folded into per-claim semantic deltas with
        before/after (``tortoise/change_feed.py``), so "what changed for claim X
        since cursor C" is one call rather than a client-side replay across event
        types. It is an off-by-default additive response field (the recorded
        ``response_fields`` carve-out): with the switch off the ``changes`` key is
        absent and the reply's SHAPE is unchanged — ``events`` and ``next_cursor``
        only. It is NOT byte-identical: the hard-delete ``PointRetracted`` payload
        gained the ``deleted`` marker unconditionally, and this reply relays that
        row verbatim under ``events`` (see ``config/surface-manifest.yml``).
        """
        from .event_store import ChangeFeedDegraded, degraded, read_after
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
        # #9208: THIS graph's change-feed write was REFUSED or lost, so a short
        # page cannot be told from a quiet stream. Refusing to answer is the
        # whole point of the named degraded state: the alternative is returning
        # success over a dead feed, which is the silent deletion §17.1's third
        # door must never be allowed to produce.
        #
        # AFTER the purge deliberately: retention is housekeeping (a DELETE of
        # old rows), not an emit, and the state persists until an explicit
        # `clear_degraded` — so refusing before the purge would stop retention
        # for as long as the graph stayed degraded (indefinitely, in a worker
        # that has no one to acknowledge it) and let `:GraphEvent` grow
        # unbounded on a graph nobody can poll. The purge cannot change the
        # answer: it is `try`-wrapped and never raises.
        state = degraded(proj)
        if state is not None:
            # ⚠️ THE REMEDY IS ADDRESSED TO THE OPERATOR, NOT TO THIS CALLER
            # (#9208 review): the recipient here is an HTTP client, and the
            # acknowledgement is a Python call in the process that OBSERVED the
            # loss — one the client cannot make, and a peer process cannot make
            # either (the state is process memory). Naming the call to the client
            # prescribed an action it had no way to perform; naming the exits
            # (in-process clear, or restart this worker) plus where the state is
            # visible does not.
            # ⚠️ THE FIRST DIAGNOSIS IS REPORTED WHEN ITS STAGE DIFFERS, not
            # only the latest (#9208 review): a graph can fail twice for
            # different reasons (`append` then `append-unverified`), and the
            # FIRST is often the only provable one — `record_degraded` keeps it
            # for exactly this reader, and reporting only the latest would
            # discard it. The stage check below is what keeps the appended
            # "the stage named above is the LATEST" sentence TRUE; a same-stage
            # repeat keeps `first_error` for `degraded_graphs()` but this detail
            # names only the latest message (docs/durability-posture.md states
            # the same rule — keep the two in step).
            first = ""
            if state.get("first_stage") and state["first_stage"] != state["stage"]:
                # ⚠️ `first` is APPENDED to the message, so the latest stage is
                # named ABOVE it. An earlier wording said "the stage named
                # below", which is the one direction that sends the reader
                # looking past the end of the string for a diagnosis that is
                # not there (#9208 review).
                first = (
                    f" The FIRST failure on this graph was at stage "
                    f"{state['first_stage']!r} ({state.get('first_error')}); the "
                    f"stage named above is the LATEST.")
            raise ChangeFeedDegraded(
                "the :GraphEvent change feed for graph "
                f"{state['graph']!r} is DEGRADED: a change-feed write was "
                f"refused or lost at stage {state['stage']!r} for event type "
                f"{state['event_type']!r} ({state['error']}); {state['lost']} "
                "event(s) are unaccounted for, so this poll cannot "
                "distinguish a quiet stream from a dead one and will not "
                "answer as if it could. Fix the store first (the refusal "
                "message names the cause — e.g. an undeclared write under "
                "§17.1). Then an OPERATOR acknowledges the loss: this state "
                "lives in the memory of the process that observed it, so it "
                "cannot be cleared by this API client or by a peer process — "
                "clear it with `tortoise.event_store.clear_degraded(<graph "
                "name>)` inside that worker, or restart that worker. "
                "`GET /health` reports it under `change_feed`, so the worker "
                "holding it can be found. The missing events are NOT recovered "
                "by a later successful write (#9208)" + first)
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
        result: dict = {"events": evs, "next_cursor": self._encode_cursor(last)}
        if _change_feed_enabled():
            from .change_feed import aggregate_changes

            result["changes"] = aggregate_changes(evs)
        return result

    _EVENT_PURGE_ATTR = "_tortoise_last_purge"
    _EVENT_PURGE_LAST: float = 0.0  # process-level gate (P1 review fix)

    def _maybe_purge_events(self, proj) -> None:
        """Best-effort, interval-gated retention purge (see poll_events).

        Runs at most once per TORTOISE_EVENT_RETENTION_INTERVAL seconds per
        PROCESS (module-global monotonic — NOT per-projection: hosted REST/MCP
        build a fresh SDK+projection per request, so a per-projection gate
        would fire the purge on EVERY poll). Reads config via env with
        defaults (30d retention, 500k cap, 3600s interval).
        """
        import os
        import time

        interval = monitoring.event_retention_interval()
        now = time.monotonic()
        if now - TortoiseSDK._EVENT_PURGE_LAST < interval:
            return
        TortoiseSDK._EVENT_PURGE_LAST = now
        try:
            from .event_store import purge_expired, purge_overflow

            # Operational :GraphEvent log retention — NOT user content and NOT
            # the deletion promise. See docs/retention-and-deletion.md.
            days = int(os.environ.get("TORTOISE_EVENT_RETENTION_DAYS", "30"))
            cap = int(os.environ.get("TORTOISE_EVENT_MAX_PER_TEAM", "500000"))
            purge_expired(proj, retention_days=days)
            purge_overflow(proj, max_events=cap)
        except Exception:  # noqa: BLE001, RUF100
            _logger.warning("event retention purge failed — continuing", exc_info=True)

    def _record_structural_edge(self, descriptor: dict) -> None:
        """Journal ONE structural edge as §4.4's flat `DirectEdgeCreated` (#9553).

        Bound on the projection by :meth:`_get_proj`; called by the structural
        edge lane's two mints AFTER their MERGE (`projection/edges.py`,
        `_record_structural_edges`), once per resolved endpoint pair.

        THE SHAPE IS `create_direct_edge`'s, deliberately: `{src, tgt,
        edge_type}` flat, plus `source_label`/`target_label`. The labels are what
        `record_projection` needs — `relation.src_label`/`tgt_label` are bound as
        VALUES and `relation` carries composite FKs into `node_identity`, so the
        record must know WHICH table to resolve the endpoint in (a `Point` keys
        `node_key = id`, a `Subject` keys `id` since #9881) — while the journal fold
        IGNORES them and resolves labels itself by an id-anchored sweep
        (`rebuild_all` pass-2b). Adding them therefore changes nothing for the
        journal route and is what makes the record route agree with it.

        ⚠️ NO `direction`/`confidence`/`weight`/`label`/`batch_id`: these edges
        carry no relationship properties (§9.6 — the verbs take no value
        parameter). Emitting `direction` here would be an invention the fold
        would then SET on the rebuilt edge.

        Emission is on EVERY successful write, not created-only, exactly as
        `create_direct_edge` does: the replay MERGE is idempotent, and a
        created-only gate would lose the edge from the log forever if the process
        died between the graph write and this call.

        Never raises: `_emit_event` is best-effort by contract (a journal append
        failure is counted and logged there) and the record write's own door
        (`record_writer.write_live_record`) heals by replay.
        """
        rel = descriptor.get("edge_type")
        src, tgt = descriptor.get("src"), descriptor.get("tgt")
        # A malformed descriptor is journaled as NOTHING (a record that cannot be
        # addressed is worse than an absent one). A SELF-EDGE is skipped for the
        # reason the replay fold skips it — `E2E-11.6`'s no-self contract — so
        # journaling one would give the RECORD an edge the journal-sourced rebuild
        # never re-creates: the two routes would disagree about topology, which is
        # the defect this producer exists to end. ⛔ AND THE SKIP IS DISCLOSED once
        # per relation, like the endpoint decline: the mint DID write a live
        # self-loop, so silence here would be a live ≠ rebuild divergence with no
        # diagnostic anywhere (measured: `create_edge("related", p, p)` leaves one
        # live edge, no record, no log line).
        if not isinstance(rel, str) or not isinstance(src, str) \
                or not isinstance(tgt, str):
            return
        if src == tgt:
            from .projection.edges import _RECORD_GAP_WARNED

            if rel not in _RECORD_GAP_WARNED:
                _RECORD_GAP_WARNED.add(rel)
                _logger.warning(
                    "structural edge %r: a SELF-EDGE (%s -> %s) is written to the "
                    "graph and NOT journaled — the replay fold skips `src == tgt` "
                    "(E2E-11.6), so a rebuild cannot re-create it. Declared at "
                    "`04-projection.md` §3 rather than dropped in silence (#9553).",
                    rel, src, tgt)
            return
        self._emit_event(
            "DirectEdgeCreated",
            id=f"{src}->{tgt}:{rel}",
            src=src,
            tgt=tgt,
            edge_type=rel,
            source_label=descriptor.get("src_label"),
            target_label=descriptor.get("tgt_label"),
        )

    def _emit_event(self, type_: str, payload: dict | None = None, *,
                    point: dict | None = None,
                    id: str | None = None,
                    recorded_ts: str | None = None, **extra) -> bool | None:
        """Unified event emission: JSONL rebuild log (#548) + graph event store (#432).

        Both stores are best-effort — failures log and continue (never crash
        the mutation). #9208: the graph event store's failures are also
        RECORDED as a NAMED degraded state (`event_store.degraded()`) that
        `poll_events` refuses to answer over, so "the feed stopped" is not
        indistinguishable from "nothing happened" at the only place a consumer
        can see it.

        **Return value (the #7951 commit signal):** ``True`` when the record was
        appended to a configured JSONL journal, ``False`` when a configured
        journal's append RAISED, and ``None`` when there is nothing to log or no
        journal is configured. The per-call value — not a delta of
        ``_journal_write_failures`` — is what a live door must gate its cache
        write on: a shared instance counter can be incremented by a CONCURRENT
        writer (#5612), which would report a committed record as failed and
        invite a duplicate re-issue.

        Two call styles supported:
        1. ``_emit_event("PointAdded", {"id": ..., ...})``
           — #432: domain payload for the :GraphEvent store.
        2. ``_emit_event("PointAdded", point=point_dict)``
           — #548: full point snapshot for JSONL rebuild replay.
        3. ``_emit_event("PointRevised", id=pid, **props)``
           — #548: id + extra fields for JSONL.

        **Graph event store (#432):** written when *type_* is in
        ``_GRAPH_EVENT_TYPES`` (11 registered types — PointAdded,
        OperatorAdded, PointRetracted, PointSuperseded, OperatorAnnotated,
        PointInvalidated, plus the promote/dedupe/object-supersede types) — and,
        under ``TORTOISE_CHANGE_FEED`` only, ``PointRevised`` too (#2353; see the
        gate below).
        The payload is taken from *payload* if given, otherwise synthesized
        from ``point["id"]`` or *id* + *extra*.

        **JSONL event log (#548):** written when *point* is provided (cleaned
        and appended as the ``"point"`` key) or *id* is provided. Events with
        neither are skipped (nothing meaningful to log). The full point
        snapshot is needed for ``rebuild_all`` replay.

        *recorded_ts* (#5048, recorded from #4666): the RECORDED instant of the
        transition, minted ONCE by the caller when the same instant must also
        be written to the node (``retract_point``). ``None`` (the default)
        mints it here as before.

        The name is deliberately NOT ``ts``. ``ts`` is an established **extra**
        key, and ``extra`` is what builds the ``:GraphEvent`` payload —
        ``invalidate_point`` passes ``ts=now`` (sdk.py, kwargs form, so it
        routes into ``{"id": id, **extra}``) and the duplicate-fold tests in
        ``tests/test_object_registered_journal.py`` do the same for
        ``ObjectSuperseded``. Promoting the name to a named parameter silently
        REMOVES it from that payload — a client-visible change to a durable
        artifact (``poll_events`` returns the payload verbatim), invisible to
        every existing test. The name is RESERVED, and the reservation cannot be
        enforced HERE: Python binds a ``**props`` key to a named parameter AT
        THE CALL, so a caller prop of that name becomes *recorded_ts* and forges
        ``event["ts"]`` — this function cannot observe the collision. The strip
        therefore happens at prop INTAKE (``_sanitize_props``, the one surface
        ``create_point``/``update_point``/``create_entity``/``update_entity``
        all pass through) and explicitly at the door that bypasses intake
        (``create_source``) — see ``_RESERVED_ENVELOPE_PROPS``, which reserves
        the same way (and for the same call-time-binding reason) this
        function's other parameter names ``point``/``payload``.
        Measured before the intake strip: ``update_point``'s props splat turned
        the prop into the ``PointRevised`` record's ``ts`` (#2296 review). The
        name is also in ``_JOURNAL_ENVELOPE_KEYS``, so the record strips it
        regardless, and ``_ENTITY_MUTATION_RESERVED_KEYS`` already reserved it
        for the entity-mutation record. With the prop gone before the call, the
        default path is additive in the strict sense: every existing caller's
        envelope, graph payload and JSONL record are byte-identical. A caller
        that passes the parameter explicitly AND a prop named ``ts`` keeps the
        old ``event.update(extra)`` precedence.
        """
        # ── Graph event store (#432) ──────────────────────────────
        # #2353: `PointRevised` (content/validity/status revisions) is a
        # JSONL-only record by default — the store carries claim TRANSITIONS,
        # see `_GRAPH_EVENT_TYPES` above. It reaches the store only under the
        # change-feed switch, which is also what makes a poll able to report
        # before→after for content, validity and status. Off (the default) the
        # emitted SET is unchanged — no `PointRevised` row — so `poll_events`'
        # response SHAPE is unchanged. It is NOT byte-identical: the hard-delete
        # `PointRetracted` payload carries the `deleted` marker unconditionally
        # (`_deleted_point_payload`).
        if type_ in _GRAPH_EVENT_TYPES or (
                type_ == "PointRevised" and _change_feed_enabled()):
            # #2600: copy FIRST — a caller-reused payload dict must never
            # gain an unexpected key from the actor merge below; then merge
            # the ContextVar actor (set by mcp_auth middleware / hosted
            # _data_sdk) when present (additive — legacy/embedded lanes with
            # no server-resolved human stay byte-identical).
            actor = _current_actor_user_id.get()
            graph_payload = payload
            if graph_payload is not None:
                graph_payload = dict(graph_payload)  # copy FIRST (#2600)
            else:
                if point is not None:
                    graph_payload = {"id": point.get("id")}
                elif id is not None:
                    graph_payload = {"id": id, **extra}
                else:
                    graph_payload = {}
            if actor is not None:
                graph_payload["actor_user_id"] = actor
            # #9208: bound BEFORE the try so the handler below cannot raise
            # `NameError` on the one path where `_get_proj()` (or the import)
            # is itself what failed — an exception in an exception handler
            # would break the write this leg exists to keep best-effort.
            proj = None
            try:
                from .event_store import append_event, ensure_event_schema, next_seq
                proj = self._get_proj()
                ensure_event_schema(proj)
                seq = next_seq(proj)
                append_event(proj, seq, type_, graph_payload, self.ulid())
            except Exception as exc:  # noqa: BLE001, RUF100
                # #9208: this leg stays BEST-EFFORT — `_emit_event` is the
                # outbox's commit signal for the JSONL journal below, and a
                # raise here would make a broken change-feed store report a
                # FAILED mutation (several doors emit after their cache write).
                # It must not be SILENT, though: §17.1's third door refusing
                # one of these writes used to reach only a WARNING, so the feed
                # stopped while `poll_events` kept answering `[]` — a quiet
                # stream and a dead one, the same reply. The loss is recorded
                # as the named degraded state the feed's CONSUMER can see.
                #
                # The recorder is itself `try`-wrapped: its import is from the
                # very module named above as a possible failure (`_emit_event`
                # may have got here because `tortoise.event_store` failed to
                # exec, and an import that raised is re-executed), and a raise
                # INSIDE this handler would escape `_emit_event` and defeat the
                # best-effort contract it is here to preserve.
                try:
                    from .event_store import record_degraded
                    if proj is None:
                        # No projection ⇒ no graph to key the state to, and a
                        # reader would hit the same `_get_proj()` failure rather
                        # than a successful poll, so nothing can be hidden here.
                        # Logged loudly, not filed under a bucket no reader reads.
                        _logger.warning(
                            "event emission failed for %s and the projection "
                            "could not be resolved, so the change feed's "
                            "degraded state cannot be attributed: %s: %s",
                            type_, type(exc).__name__, exc)
                    else:
                        state = record_degraded(proj, "emit", type_, None, exc)
                        _logger.warning(
                            "event emission failed for %s — the change feed is "
                            "DEGRADED (%s lost, %s: %s); poll_events will "
                            "refuse to answer until this is acknowledged "
                            "(#9208)",
                            type_, state["lost"], type(exc).__name__, exc)
                except Exception:  # noqa: BLE001, RUF100
                    _logger.exception(
                        "event emission failed for %s — and recording the "
                        "degraded state failed too (#9208)", type_)

        # ── JSONL event log (#548) ─────────────────────────────────
        if point is None and id is None:
            return None  # nothing meaningful to log
        log = self._get_event_log()
        if log is None:
            # No journal configured: there is no rebuild source to commit to.
            # An ARMED record on a journal-less deployment therefore has nothing
            # to write AND nothing to heal it from — the #9078 hole with no
            # signal, which is why it is counted rather than left silent (the
            # journal-less lane is a supported shape: `tortoise/config.py`).
            self._record_write_without_journal(type_)
            return None
        from .ids import ulid, now_iso  # noqa: I001
        event: dict = {
            "event_id": ulid(),
            "ts": recorded_ts or now_iso(),
            "type": type_,
            "initiated_by": "sdk",
            "projection_version": 2,
        }
        # #2600: the JSONL envelope carries the actor too (additive — only
        # when a server-resolved human is present; a rebuild replay restores
        # the ORIGINAL payload dict, whose own actor_user_id — if any — was
        # caller-authored, never forged here). The actor is NOT added to any
        # replay skip-set. The merge is AFTER event.update(extra) below so
        # the server-resolved actor is always the last write — matching the
        # graph branch order (code-review #4, PR #2664).
        _jsonl_actor = _current_actor_user_id.get()
        if point is not None:
            # #5004: the embedding IS journalled. R1 (`docs/durability-posture.md`
            # → *Derived properties that are STORED, not recomputed*; design
            # source `docs/architecture/STORAGE-ARCHITECTURE.md` §3/§14.1 O1,
            # landed via #5016)
            # is that the embedding STORES —
            # it is not regenerated on replay, because a re-embed is a RE-RUN,
            # not a replay: its output depends on model identity, revision and
            # tokenizer, none of which the journal used to carry. The old strip
            # here made `derived = replay(journal)` FALSE for the largest cost
            # lever, and a changed embedder silently produced a DIFFERENT graph
            # from the same journal. The payload now carries the vector plus
            # its identity (model/revision/text-hash) so replay restores it
            # verbatim and a model change is recorded, not silent.
            #
            # content_hash is still stripped — it is a PURE function of
            # content, so it is genuinely recomputable (#2795 D2).
            clean = {k: v for k, v in point.items() if k != "content_hash"}
            # #5004 round-3: PRESENCE IS OWNERSHIP. `get_point` omits a NULL
            # property, so a point with no vector would arrive here with the
            # key ABSENT — and the replay would read that as "legacy record,
            # recompute" and invent a vector the live node does not have. The
            # producer owns this field on every snapshot it emits, so force
            # the key present (None when there is genuinely nothing).
            clean.setdefault("embedding", None)
            # THE shared seam (also used by `EventAPI._point`) — a second,
            # divergent writer of this field is the failure mode #5004 exists
            # to remove. `creating` gates the text-hash attestation: only a
            # PointAdded/OperatorAdded vector was computed from the content
            # riding with it (see `stamp_journal_embedding`).
            try:
                from .embeddings import stamp_journal_embedding
            except Exception:  # noqa: BLE001, RUF100
                # #5148 review: the seam is unimportable at this call site —
                # see `EventAPI._point` for what that does and does not cover
                # (not a missing-core-dep install; a failing import hook, and
                # the principle that a write must not depend on an import).
                # The graph mutation has ALREADY happened by the time we get
                # here, so
                # raising would hand the caller an ImportError for a write
                # that SUCCEEDED, and would then skip `log.append` below —
                # the exact contract that call site honours ("a log-write
                # failure must not crash the caller"). Skipping the stamp is
                # safe: the key is already forced present by the `setdefault`
                # above, so PRESENCE IS OWNERSHIP still holds and the replay
                # cannot invent a vector. What is lost is only the
                # NORMALISATION safety net and the text-hash attestation.
                stamp_journal_embedding = None
            if stamp_journal_embedding is not None:
                clean = stamp_journal_embedding(
                    clean, creating=type_ in ("PointAdded", "OperatorAdded"))
            # #5089: NO content synthesis here. Operators store no 'content' as
            # a node property, so synthesizing the `IMPL(inputs)` label put a
            # value in the record that NO writer ever stored — and the replay
            # persisted it, so a rebuilt operator carried a label live never had.
            # What the live SDK door actually produces is an omitted key; the
            # replay writer's `_WRITER_DEFAULTS` empty string is the journal-side
            # asymmetry `consistency` already models for operators, and this no
            # longer fabricates a label on top of it. (The display label is
            # derived at the point of display, `api._point`; nothing reads an
            # operator's content back off the node.)
            # Similarly, operators may lack 'pointKind' — default to empty.
            if "pointKind" not in clean:
                clean["pointKind"] = ""
            event["point"] = clean
        if id is not None:
            event["id"] = id
        event.update(extra)
        # #2600: server-resolved actor as the LAST write on the envelope
        # (after event.update(extra)), matching the graph branch order.
        if _jsonl_actor is not None:
            event["actor_user_id"] = _jsonl_actor
        try:
            # #9190: the append's return value IS the record's journal position;
            # it is threaded to the record so layer 1 stops being empty on live
            # writes. `None` (an in-memory log) means there is nothing to number.
            seq = log.append(event)
        except Exception as exc:
            # The graph mutation already succeeded — a log-write failure must
            # not crash the caller or pretend the write failed. Rebuild parity
            # is best-effort here: rebuild_all's graph snapshot catches any
            # point missing from the log on the next rebuild (#548).
            #
            # #4240 review F1: on a CONFIGURED journal that posture is the
            # silent-live-only hazard the journal exists to close, so the
            # failure must not be a bare WARNING. Never raise (the write
            # succeeded, and raising would hand the caller a failure for it),
            # but count it (`monitoring.record_journal_write_failure`, carried
            # by ``monitoring.metrics()``) and return it to the caller through
            # ``capture_session``'s receipt warning below.
            # Residual (deliberate, recorded on #5612): a journal that starts
            # failing AFTER the entrypoint's boot-time writability probe — the
            # probe catches a NON-ROOT mode-bit failure and EROFS/ENOSPC on any
            # uid, but NOT a mode-bit failure under root (the image has no USER,
            # so on Fly the process is uid 0; F1 cycle 2) — is still fail-soft.
            # The foreseeable trigger is ENOSPC once the journal has grown below
            # the volume's free space. The record is lost
            # from the journal, `rebuild_all` reconstructs only what the
            # journal holds (so a later rebuild cannot restore that write), and
            # the ONLY signals are this ERROR log, the failure counter and the
            # capture-receipt warning. It is not fatal by design: the graph
            # mutation stands, and failing the request would lose a write that
            # actually persisted.
            self._journal_write_failures += 1
            try:  # noqa: SIM105 — counting must never mask the original error
                monitoring.record_journal_write_failure()
            except Exception:  # noqa: BLE001, RUF100
                pass
            _logger.error(
                "failed to append %s event to SDK log %s: %s — the write is "
                "LIVE-ONLY and will not survive a rebuild (#4240)",
                type_, self._event_log_path, exc,
            )
            # #7951 review I3: the PER-CALL outcome. A caller gating a fold on
            # "committed?" must read THIS, not the shared counter above, whose
            # before/after delta can be moved by another thread's failure.
            return False
        # ── the Postgres record (#9188, epic #7879) ────────────────
        # The append COMMITTED at this line (§17.1), so the record write runs
        # after it and before any cache fold: truth first, then the projection.
        # It is fail-soft for the SAME reason the append's own failure is — the
        # graph write stands, and raising here would hand the caller a failure
        # for a record that persisted while killing the fold too. The loss is
        # observable instead (``monitoring.metrics()["record_write_failures"]``)
        # and ``replay`` heals it from the journal.
        self._write_record_projection(event, seq=seq)
        return True

    def _write_record_projection(self, record: dict,
                                 seq: int | None) -> None:
        """Hand one just-appended journal record to the Postgres record (#9188).

        THE production call site. Until this existed, `tortoise/record_projection.py`
        and `tortoise/record_writer.py` had NO non-test importer, so no user's
        data ever reached the record (epic #7879, measured on #9078). The
        behaviour — arming, tenant resolution, the commit, and the counted
        failure — lives in `record_writer.write_live_record`, because the
        journal has THREE live append doors (`_emit_event` here,
        `EventAPI._emit`, `ConversationMiner._emit_event`) and a second
        implementation of this boundary is how one door's records come to
        differ from another's.

        ``seq`` is REQUIRED, and that is the point rather than a style choice
        (#9307 review F2). It is the ordinal the journal append returned, and
        the seam treats ``None`` as "this log reported no position" — so a
        default here would make a caller that forgot to pass it take
        ``position=False`` on a live write, leaving layer 1
        (`public.journal_event`) empty with no error, no count and a green
        wiring gate: exactly the #9190 defect this change closes. Required, an
        omission is a ``TypeError`` at the call site. `None` is still a legal
        VALUE (`_InMemoryEventLog` reports no position — no row, never an
        invented number); it is the silent default that is removed, and
        `tools/record_wiring_check.py::live_call_sites_pass_the_ordinal` holds
        every caller to it.

        Deliberately fail-soft: the graph write and the journal append have
        already committed, so a record failure is a LOSS TO REPORT (counted in
        ``monitoring.metrics()["record_write_failures"]``, healed by a replay),
        never a reason to fail the caller's write.
        """
        try:
            from . import record_writer
        except Exception as exc:
            # #5148's rule, applied to this seam too: the graph write and the
            # journal append already happened, so a failing import hook must
            # not hand the caller an ImportError for a write that SUCCEEDED.
            self._record_write_failure("seam_unimportable", exc)
            return
        # DURABILITY IS READ, not assumed (#9231 review P1): no log at all is
        # NOT durable, and a log that declares itself a scratch buffer is not
        # either. The seam DECLINES it and counts the decline on
        # `monitoring.metrics()["record_non_journal_skips"]` — not on
        # `record_write_failures`, since that lane has no journal by design and
        # nothing was lost (#9231 review P2).
        log = self._event_log
        durable = log is not None and getattr(log, "durable", True)
        record_writer.write_live_record(record, durable=durable, seq=seq,
                                        tenant=self._record_tenant)

    def _bind_record_tenant(self, org_id: str, graph_id: str) -> None:
        """Bind this SDK's writes to one record tenant (#9188).

        The BY-VALUE half of the tenant carrier. `record_writer.use_record_tenant`
        is the ambient half (a ContextVar, right wherever a lane can hold a
        `with` around the work). This is the half a lane needs when it cannot:
        the hosted REST lane resolves the tenant inside `_data_sdk`, which the
        capture path submits to `_graph_offload` (a scope entered there dies
        with the copy), and setting it loop-side would mean setting it with no
        exit (the `_data_sdk` auth seam is a plain dependency; a
        `yield`-dependency could hold a `with`, and none does) — one unreleased
        value that every detached task and every other write door in that
        context inherits. Carrying it on the SDK confines it
        to the write it belongs to. (Propagation is NOT the reason: a contextvar
        set on the loop is copied into `asyncio.to_thread` / `_graph_offload`.)

        ⚠️ It is NOT a second resolution: the caller passes what
        `record_writer.record_tenant_for` derived from the SAME credential dict
        that chose the graph this SDK is bound to, so the record's tenant and
        the graph the write lands in cannot disagree by construction. A lane
        that binds a guessed or process-global tenant recreates #9192's silent
        cross-tenant write — never call this with anything but a per-request
        resolution.

        ⚠️ The binding is a PROPERTY OF THIS OBJECT, so it is only as
        request-scoped as the SDK is. Today every hosted data path builds a
        fresh SDK per request (`hosted_api._make_sdk`) and the keepalive
        anchors are never bound — but a future cache of `_data_sdk`'s result
        would carry this tenant into a later request, which ambient state could
        not do. Bind a throwaway SDK, never a shared one.

        Validated here rather than at the write because the failure mode of an
        empty half is a row filed under a graph this write never touched, and
        finding that at the seam is one drained record too late.
        """
        if not (isinstance(org_id, str) and org_id
                and isinstance(graph_id, str) and graph_id):
            raise ValueError(
                "_bind_record_tenant needs a non-empty org_id and graph_id "
                f"(got {org_id!r}, {graph_id!r})"
            )
        self._record_tenant = (org_id, graph_id)

    def _record_write_failure(self, reason: str, exc: BaseException) -> None:
        """Count and report one armed record write that did not happen (#9188).

        Never raises: the caller's append has already committed, so a record
        failure is a LOSS TO REPORT, not a capture failure. Counting is
        best-effort in the same way (a metrics failure must not mask the
        record failure).
        """
        with contextlib.suppress(Exception):
            monitoring.record_record_write_failure(reason)
        _logger.warning(
            "the record write did not happen (%s) — the journal has the record "
            "and a replay heals the record: %s", reason, exc,
        )

    def _record_write_without_journal(self, type_: str) -> None:
        """Count an ARMED record on a deployment with NO journal (#9188).

        The journal-less lane is supported and default (`tortoise/config.py`),
        so an operator can arm the record and not the journal — and then nothing
        is written, nothing is counted, and no replay can heal it, because the
        record's only sustainable source IS the journal. Counting it is the
        difference between that being a silent hole and an observable
        misconfiguration.

        Unarmed is the normal case and costs one env lookup: no counter, no
        warning. A MALFORMED DSN is not "unarmed" and must not disappear into
        the swallow below: the journal-less lane is the one shape where the
        seam's own `malformed_dsn` count can never fire (there is no
        `write_live_record` call to carry it), so a typo'd
        ``TORTOISE_RECORD_DSN`` on this lane would otherwise produce no signal
        at all — the exact silent hole this method exists to close.
        """
        try:
            from . import record_writer
        except Exception:
            return
        try:
            armed = record_writer.record_dsn() is not None
        except record_writer.RecordWriteError as exc:
            self._record_write_failure("malformed_dsn", exc)
            return
        if armed:
            self._record_write_failure(
                "no_journal",
                RuntimeError(
                    f"{type_} was not recorded: the record is armed but this "
                    "process has no JSONL journal, so the record write has no "
                    "source (and no replay could heal it)"
                ),
            )

    def _journal_entity_event(self, event: dict, *,
                              recorded_ts: str | None = None) -> bool | None:
        """Append a canonical entity record to the journal (the WRITE half).

        The emission half of :meth:`_apply_and_journal`; split out so a
        caller that must fold through a different path can still record. It
        obeys THE JOURNAL ORDERING CONTRACT (module top): appending is the
        COMMIT POINT of a live write, so this runs BEFORE the fold. No-ops for
        a non-entity record type, a missing id, or an unconfigured journal —
        ``_emit_event`` owns the last case.

        ``recorded_ts`` is the envelope instant the door ALREADY decided (#9434):
        a door that stamps ``event["ts"]`` before folding hands the SAME value
        here, so the record's envelope ``ts`` and the value the fold read are ONE
        clock read. ``None`` (every pre-#9434 caller) keeps ``_emit_event``'s own
        mint — this parameter changes nothing for them.

        ⛔ This is for a LIVE write. A REPLAY engine must never call it: the
        record being folded came FROM the journal, so journalling it again
        would grow the log on every rebuild and (worse) make replay
        non-idempotent at the log level.
        """
        type_ = event.get("type")
        if type_ not in _JOURNALED_ENTITY_EVENT_TYPES:
            return None
        id_ = event.get("id") or event.get("eventId")
        if id_ is None:
            return None
        # RETURN the append outcome: `_emit_event` is True (appended), False (a
        # configured journal's append RAISED) or None (nothing to log / no
        # journal). This is the PER-CALL value, which is what a live door must
        # gate its cache write on — never a delta of `_journal_write_failures`,
        # which a CONCURRENT writer can inflate (#5612). Existing callers
        # ignored the return and are unaffected by its arrival.
        # `recorded_ts` rides the ARGUMENT, never the payload: it is a
        # `_JOURNAL_ENVELOPE_KEYS` member, so `_journal_record_payload` strips a
        # caller-authored one — which is also why the door that stamps `ts` must
        # hand the instant over explicitly rather than expect it to travel.
        return self._emit_event(
            type_, id=id_, recorded_ts=recorded_ts,
            **_journal_record_payload(event))

    def _apply_and_journal(self, proj, event: dict, *,
                           journal: bool = True) -> None:
        """THE live-write door for a pre-built Object/Subject/Event record.

        Obeys THE JOURNAL ORDERING CONTRACT (module top): **append first, then
        fold.** `proj.apply` is the fold the REPLAY engines drive —
        `rebuild_all` / `recover_from_log` call it once per journal record — so
        it must NEVER journal itself (a replayed record would journal itself
        and double-apply). This is that fold's LIVE twin: a caller holding a
        record it means to PERSIST comes through here instead of calling
        `proj.apply` directly.

        Journal-first is not a preference — it is the order whose crash window
        is replay-healable. If the process dies after the append and before
        the fold, the journal (TRUTH) holds the record and the graph (DERIVED)
        merely lags; the next `rebuild_all` materialises it. Apply-first would
        die with the derived store AHEAD of truth, where a replay silently
        LOSES the write and no repair can recover it. §3: *"Writes go to the
        truth first, then project."* `docs/architecture/STORAGE-ARCHITECTURE.md`
        §17.1 mechanism (1) closes even this window with one atomic write.

        The one shape the module-top contract still names as exception 2
        (POST-APPLY-SNAPSHOT PAYLOAD) has NO production site today: `create_point`
        was the last, and #7951 moved it to the outbox ordering. This door's
        normal mode is never apply-first.

        ``journal=False`` folds WITHOUT appending: for a replay-safe
        RE-application of an already-registered record (the GitHub indexer's
        poll re-runs), which must not grow the journal once per run.

        The APPEND IS THE COMMIT POINT. `_emit_event` is deliberately
        best-effort (it logs ERROR + counts the failure instead of raising, so
        a log-write failure never crashes a write that DID persist — #4240),
        so this door reads the failure counter and does NOT fold when the
        append failed. Folding anyway would put the DERIVED store ahead of the
        journal on a failed append — window (a) of the contract, the one a
        replay cannot undo.
        """
        # ── #5475 (residual): the record door is a WRITE door ──
        # This is where a caller holding a PRE-BUILT record persists it, and the
        # record carries its kind in its own spelling (``eventKind`` /
        # ``object_kind``). It bypasses the create funnels by construction — the
        # GitHub indexer's provenance writes are its callers — so without this
        # it was one more way to install a pack (measured on the same class:
        # ``['dev']`` → ``['dev','marketing']`` via one ``object_kind`` record).
        # Before the append, so a refusal leaves the journal untouched and the
        # replay cannot resurrect it. Gated against `proj`'s graph — the write
        # TARGET — not this SDK's bound graph (see the reader's docstring).
        self._refuse_unapproved_kinds_in_record(event, getattr(proj, "g", None))
        if journal:
            # (#7951 P1-b) Mirror `_create_entity`'s `createdAt` synthesis on
            # the journaling path, BEFORE the append (so the record and the
            # fold carry the SAME instant). `_create_entity` stamps
            # `event["createdAt"] = now_iso()` exactly to close the #2164-P4
            # "a replay stamps rebuild time" class: without it a journaled
            # record carries no create instant, so `_upsert_subject` /
            # `_upsert_object`'s `coalesce($ca, $now)` stamps the REBUILD
            # time on replay and live != replayed for the same journal.
            # `_journal_record_payload` shares the payload-KEY rule between
            # the two doors; this shares the value-SYNTHESIS rule, which the
            # reviewer found had drifted (the Subject payloads the indexer
            # mints carry no `createdAt` of their own).
            #
            # Gate on is-None (not key-absence), exactly like `_create_entity`:
            # an explicit `createdAt=None` caller prop must not slip a NULL
            # onto the journal line (replay ON CREATE would stamp rebuild
            # time). A producer-supplied instant (`issue_to_object` carries
            # one) is kept verbatim.
            if (event.get("type") in _JOURNALED_ENTITY_EVENT_TYPES
                    and event.get("createdAt") is None):
                from .ids import now_iso
                event["createdAt"] = now_iso()
            # (#9434) The `ts` half of the same VALUE-SYNTHESIS rule, and the
            # other door `entities._event_plain_merge` names. This door appends
            # through `_emit_event`, which minted the envelope `ts` on a COPY of
            # the record — and it then folds the CALLER's dict, which carries no
            # `ts` at all (`ts` is a reserved ENVELOPE prop). So any fold-level
            # field stamped from the record's own instant took the FOLD clock
            # live and the APPEND clock on replay: `_upsert_event` reads
            # `record_ts=event.get("ts")` → `None` → `_event_plain_merge`'s
            # document `:Source` `ingestedAt` (#5140) fell back to `_now_iso()`.
            # Measured pre-fix on this door, one `objectType='Document'`
            # record: live `…26.948766` vs rebuilt `…26.925052` — the #5024
            # `derived = replay(journal)` class, on that field. Stamping HERE,
            # before the append, makes the value the fold reads and the value
            # the envelope carries ONE instant, and #9419 did the same on
            # `_create_entity` (which folds first, so its divergence ran the
            # other way).
            #
            # It does NOT move the ordering contract: the append is still the
            # commit point, and a failed append still skips the fold. Only WHEN
            # the instant is read changes, from inside the append to just before
            # it — the shape the `createdAt` synthesis above already uses.
            #
            # `ts` is a reserved ENVELOPE name and neither side stores it: it is
       