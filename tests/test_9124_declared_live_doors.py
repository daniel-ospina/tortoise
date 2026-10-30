"""#9124 part 1 — the §17.1 declaration census, enumerated in CODE, not by hand.

WHAT THIS SUITE IS FOR. §17.1's third door (`_GuardedGraph`, i.e. `proj.g`) now
DETECTS a mutating statement that arrives with no declared lane. Detection is
only worth having if the surface is *accounted for*: every entry point that
reaches the handle must be either

* DECLARED — a `_live_write_scope` / `_replay_write_scope` /
  `_graph_metadata_write_scope` in the function body, or
  `@_declares_live_write_lane` on the door itself; or
* INVENTORIED — named in ONE of the pinned inventories below, with the reason it
  cannot declare a lane yet.

The enumeration is derived from the SOURCE of `tortoise/sdk.py` and
`tortoise/event_store.py`, never from a hand-maintained list of call sites, so a
function added later cannot slip in undeclared: it fails here until someone
declares it or adds it to an inventory (which is a reviewed line in this file,
not a silent omission). The inventories are pinned EXACTLY, so they can only
shrink — a stale entry (a function that has since been declared, renamed or
deleted) fails too.

⛔ THE GUARD IS MEASURING, NOT REFUSING (#9124 part 1). `_HANDLE_GUARD_ENFORCES`
is `False`: an undeclared write is counted (`undeclared_handle_writes()`) and
logged, not raised. That is the owner's option B — declare the mechanical
journal-first doors first, keep the apply-first arms for their own change, and
flip the switch only when the surface is fully declared. The flip is not left to
memory either: `test_arming_the_guard_requires_a_fully_declared_surface` reads
every module in the package that reaches the handle and refuses to let the
switch be True while anything is still undeclared.

WHAT COUNTS AS A MUTATING CALL. The verb list is `_HANDLE_MUTATING_VERBS`, the
SAME constant the guard's own enumeration test reads, and the statement text is
run through `_statement_writes`, the SAME classifier the guard consults — so
this census and the runtime door cannot disagree about what a write is (the
#3595 shared-decision precedent). A statement whose text the enumerator cannot
resolve (built by a helper, passed in from a generator) is counted as a WRITE:
the classifier's own polarity is "anything not provably read-only is a write".
That OVER-report polarity is what `_text` implements — every unresolved part of
an f-string or a `+` skeleton returns `None` rather than a placeholder, because
a placeholder lets `f"MATCH (n:Point {{id:$id}}) {action}"` read as a non-write
and drop the whole function out of the census. `_UNRESOLVED` lists them for that reason, not because they are believed to
write.

WHICH EXPRESSION IS THE HANDLE. `proj.g`, `self._get_proj().g` (any attribute
chain whose LAST attribute is `g`), `getattr(x, "g")`, and any name bound from
one of those by `h = proj.g`, `h: Any = proj.g`, `h, _ = proj.g, 1` or
`raw = getattr(graph, "g", None)` — the shapes the tree actually uses
(`event_store.py`, `onboarding/state.py`, `onboarding/seed.py`,
`fallback_snapshot.py`, `weights.py`, `why.py`).
⛔ KNOWN LIMIT — what the census cannot see, stated rather than implied. A handle
passed as an ARGUMENT is resolved as the handle only when it arrives on a
PARAMETER of the receiving function (any parameter except `self`/`cls`), which is
the over-report direction: `clear_config_reset(self._get_proj().g)` → `clear_config_reset(g)` issues a
`DETACH DELETE` at projection/__init__.py:1519 (`clear_config_reset`, def at 1509) and is invisible without it
(measured: without the rule 76 functions leave the census entirely, 28 of them
carrying a provable write). `functools.partial` over a verb, and a
handle stashed on an object attribute (`self._h = proj.g`) then used later, are
NOT resolved. Three more shapes sit outside the census ENTIRELY: a handle
reached one hop down (`proj.g._g.query(...)` — the last attribute must be `g`),
a graph resolved off the client (`proj.db.select_graph(<name>).query(...)`), and a
mutating call at MODULE or CLASS-BODY level (the walk only classifies inside a
function). The first two bypass `_GuardedGraph`, so they are escapes 2 and 3 of
§17.1's door (`tortoise/projection/__init__.py`), documented as out of scope; the
third WOULD pass through the guarded handle, so it is a real gap in the flip gate
and is named rather than claimed away. Measured on this tree: sdk.py has 4 verb calls on a parameter
receiver — 3 resolved read-only `MATCH … RETURN` probes
(`_holds_role_available`, `_refuse_inverted_point_window`,
`_refuse_inconsistent_validity_precision`) and 1 unresolved read
(`_source_reachable_by`) — and none is a write. `self.query(...)` calls are NOT
in that population, because `self`/`cls` are never treated as handle parameters.
A NESTED function writing through an alias captured from its parent is also not
resolved (aliases are tracked per function; measured 0 live instances). A new write through one of the unresolved shapes would be invisible here,
so the shapes are named in this docstring instead of being claimed away.

WHY `sdk.py` AND `event_store.py`, AND NOT EVERY MODULE. These two are what part
1 DECLARES: the SDK's journal-first doors and the `:GraphEvent` store. The other
modules that reach the handle (`hosted_api.py`, `mining.py`, `ingest.py`,
`commit_idempotency.py`, the connectors, ...) are measured on #9124 and belong to
part 2's arming — which is exactly what
`test_arming_the_guard_requires_a_fully_declared_surface` enforces: it scans
them all and names every undeclared function, so arming cannot happen on a
surface this suite never looked at.
"""

from __future__ import annotations

import ast
import builtins
import io
import re
import sys
import tokenize
from pathlib import Path

import pytest

from tortoise.projection import (
    _HANDLE_GUARD_ENFORCES,
    _HANDLE_MUTATING_VERBS,
    # The repo's ONE definition of a write clause, and the literal/comment
    # stripper it reads (see `_assembled_write_evidence`): a local copy of this
    # regex drifted from it twice over — fail-open on the clauses it omitted and
    # false-red on the property names it stopped excluding (#7174) — which is the
    # #3595 shared-decision rule this file's docstring already invokes.
    _LITERAL_OR_COMMENT_RE,
    _WRITE_CLAUSE_RE,
    _statement_writes,
)

ROOT = Path(__file__).resolve().parent.parent
SDK_PY = ROOT / "tortoise" / "sdk.py"
EVENT_STORE_PY = ROOT / "tortoise" / "event_store.py"

#: A callable that APPENDS a journal record (the commit point the lane declares
#: itself after). Pattern-matched rather than enumerated, so a NEW append helper
#: (`_emit_*`, `_journal_*`, ...) is recognised instead of silently reclassifying
#: a journal-first door as "appends nothing".
_APPEND_CALL_RE = re.compile(
    r"^(?:_?emit_event|_?apply_and_journal|_?journal_\w+|_?emit_\w+_record"
    r"|_?emit_edge_message(?:_clear)?|append_event)$")

#: Names the pattern above would call an append but which append NO record — a
#: helper that normalises params or reads a timestamp. Counting one of these as
#: the commit point would make `_journal_first_violation` pass on a door whose
#: record never landed (the #9124 review round-3 P2, measured: `_journal_instant`
#: is called before the folds' writes). The list may only grow with PROOF, and
#: `test_the_append_exclusions_append_nothing` is that proof — it re-reads each
#: definition and fails if the body calls a real append helper.
_APPEND_NOT_A_RECORD = {
    "_journal_instant",       # entities.py: reads the record's instant
    "_journal_safe_params",   # projection/__init__.py: sanitises params
    # #9206 review P2: `sdk.py`'s "a journal append failed" RECEIPT warning —
    # it RETURNS a string and touches no journal. It matched `_?journal_\w+`,
    # and it was the ONLY pattern-matching call in `capture_session`, so the
    # census credited that door with a commit point it does not have and filed
    # it as apply-first on the strength of a warning builder. A `_journal_*`
    # NAME is not a journal write; that is exactly what this set exists to
    # refuse. `capture_session` was its only other pattern-matching call and is
    # no longer on the census at all (#9349 moved its writes into declared
    # doors), so this exclusion now guards the census against a warning builder
    # wherever one appears, not that door's lane.
    "_journal_write_failure_warning",  # sdk.py: builds a receipt warning string
}

#: #9206 — the EP engine's two record carriers, added to `_APPEND_CALL_RE`'s
#: family above. `tortoise/ep.py` journals every committed belief/message write
#: through `self._emit_edge_message(...)` / `self._emit_edge_message_clear(...)`
#: (#5380), and neither name matched the old pattern — so the census read the
#: two EP doors as "appends NO record" the moment they declared a lane, which is
#: the exact instrument gap this file's own header warns about ("an opener
#: missing from `_SCOPE_CALL_NAMES` reported a writer that DOES declare as
#: undeclared"). Both are real appends: each builds and emits the JSONL record
#: (`_emit_edge_message` -> `EdgeMessageRecorded`, `_emit_edge_message_clear` ->
#: `EdgeMessageCleared`), which is why widening the pattern is the fix rather
#: than adding them to `_APPEND_NOT_A_RECORD`.

#: The scope openers, PLUS the decorator that opens one around a whole door.
#: `_onboarding_write_scope` is the onboarding funnel's own opener (`with
#: _onboarding_write_scope(cypher):` around the dispatch in
#: `tortoise/onboarding/state.py`) — a lane declared through a module-local
#: helper is still declared, and leaving it out reported that module as
#: undeclared while it was the opposite. `_prewipe_restore_write_scope` is
#: `#9210`'s restore funnel (`FalkorProjection._restore_write`): omitted, the
#: census classified a writer that DOES declare a lane as undeclared and left
#: it in the `provable` remainder the owner's last-step flip requires cleared.
_SCOPE_CALL_NAMES = (
    "_live_write_scope",
    "_replay_write_scope",
    "_graph_metadata_write_scope",
    "_onboarding_write_scope",
    "_prewipe_restore_write_scope",
    # #9391: the HANDLE-ONLY helper (`_handle_only_write_scope`) — the opener
    # BOTH `replay` declaration sites now use: `_replay_write_scope()` returns
    # it, and `event_store.reestablish_watermark` opens it directly. Same reason
    # as the openers above, and it was a live LATENT-RED rather than a
    # cosmetic omission: `reestablish_watermark` writes
    # `scope = (_handle_only_write_scope(...) if ... else _cache_write_authorized(...))`,
    # so the census classified its `MERGE` as declared only through the ELSE
    # arm of the same conditional and attributed the WRONG lane to it, while the
    # first lane to write the natural `with _handle_only_write_scope(...):` form
    # would have been reported UNDECLARED on correct product code — the exact
    # false positive this tuple's docstring exists to prevent. Pinned by
    # `test_the_handle_only_opener_is_visible_to_the_census`.
    "_handle_only_write_scope",
    "_cache_write_authorized",
    "_declares_live_write_lane",
    # #9206: the APPLY-FIRST lane and its decorator. A declaration, so the
    # census must SEE it — an opener missing from this tuple reports a writer
    # that DOES declare as undeclared, and the flip gate would then instruct the
    # next lane to add a lane that is already there (the #9124 review P2/#9210
    # shape, one lane over).
    "_apply_first_write_scope",
    "_declares_apply_first_write_lane",
    # #9206: the DERIVED-RECOMPUTE lane (`resolve_source_key`'s adopt-on-touch —
    # a value re-derived from journal-carried input, appended nowhere). Same
    # reason as the apply-first pair above: an opener the census cannot see
    # reports a DECLARED writer as undeclared.
    "_derived_recompute_write_scope",
)
#: The subset that claims "this door appends its record, THEN writes the cache".
#: Only these are held to the journal-first ordering pin below: a replay or
#: graph-metadata lane makes no such claim.
_LIVE_LANE_NAMES = ("_live_write_scope", "_declares_live_write_lane")
#: The subset that claims the INVERSE order — the cache write is the commit
#: point and the record that describes it follows, BY DECISION (#9206). Its own
#: pin (`test_every_apply_first_declared_door_states_an_order_and_a_record`)
#: requires the door to append a record anyway and to be GENUINELY apply-first,
#: so the lane can never become an amnesty for an unjournalled cache write.
_APPLY_FIRST_LANE_NAMES = (
    "_apply_first_write_scope", "_declares_apply_first_write_lane")
#: Every lane DECORATOR — the openers whose scope spans the whole function body
#: (a `with` inside the body spans only its block, handled separately).
_DECLARATION_DECORATORS = (*_LIVE_LANE_NAMES, *_APPLY_FIRST_LANE_NAMES)


# ── the inventories: everything part 1 does NOT declare, and why ───────────
#
# Each entry is `function name -> the reason it cannot declare a lane yet`.
# Grouped by reason so the census reads as a census; pinned EXACTLY by the
# tests below, so an entry can only leave this file by being declared (or by
# disappearing), never by drifting.

#: Apply-first PUBLIC doors: the record that describes the write is emitted
#: AFTER it, so a `_live_write_scope` here would authorize a cache write with no
#: committed record — the #9068 defect, and the opposite of what §17.1 requires.
#: Fixing one means moving the record above the write AND deciding its failure
#: contract (does a failed append abort the write?), which is #9124 part 2.
#:
#: ⚠️ #9206 DECLARED ten doors on the apply-first lane
#: (`_declares_apply_first_write_lane`) rather than leaving their writes
#: undeclared, so `create_direct_edge`, `create_operator`, `delete_point`,
#: `_delete_entity`, `_materialize_session_source`, `_update_entity`,
#: `_write_capture_turns` and `update_point` have LEFT these inventories —
#: THREE from this one (`_APPLY_FIRST_DOORS`: `create_direct_edge`,
#: `create_operator`, `delete_point`), FOUR from `_APPLY_FIRST_HELPERS`
#: (`_delete_entity`, `_materialize_session_source`, `_update_entity`,
#: `_write_capture_turns`) and ONE from `_MIXED_ARM_DOORS` (`update_point`) —
#: eight names, and the three-source split matters: a reader who follows only
#: this set's pointer will not find the other five. The remaining TWO
#: (`_stamp_capture_attempt_state`, `_stamp_entity_link_counters`) were never
#: inventory entries at all: they were inline statements inside
#: `capture_session`, which appends NO record of its own: its four provenance
#: stamps (`#9350`) and its extraction reaches are doors of their own now, and
#: the parent door left the census in #9349 — a
#: function holding BOTH scoped and unscoped writes is refused by this census
#: ("presence is not containment"), so the stamps became doors of their own.
#: A declaration does not convert the order: the entries that remain are still
#: apply-first, and the lane's own pin requires each declared door to append its
#: record and to be genuinely apply-first.
_APPLY_FIRST_DOORS = {
    "approve_merge",         # SET reviewed flags, then PointRevised/… records
    "assess_source",         # SET reliability, then the assessment record
    "compute_confidence",    # SET n.confidence per row, then ConfidenceChanged
    "mitigate_operator",     # CREATE the mitigation, then its records
    "promote_point",         # CAS SET status='live', then PointPromoted
    "set_point_baseline",    # SET the baseline, then its record
}

#: Apply-first INTERNAL helpers — the same ordering as the doors above, reached
#: from them (or from capture/ingest). Same part-2 decision, same reason.
_APPLY_FIRST_HELPERS = {
    "_apply_capture_ingest_ep",
    "_apply_source_inheritance",
    "_dedup_content_candidates",   # marks the candidate, then records it
    "_embedding_repair_attempt",
    "_promote_incident_operators",
    "_session_event_write",
    "_stamp_batch_id",             # SET n.batch_id, then _emit_batch_id_record
}

#: Journal-first BY ORDER but holding an arm whose record is deliberately stated
#: after its guarded write — EMPTY since #9206, and kept so the reason the class
#: existed is still readable rather than deleted along with its last member:
#:
#: * `update_point` was the entry: its `status='live'` promote arm appends its
#:   record AFTER the guarded `SET` (#8830), while the non-promote arm appends
#:   first. #9206 declared the whole door on the apply-first lane, which states
#:   exactly that (a write whose record follows, by decision) without
#:   overclaiming for the promote arm.
#: * `capture_session` was the other, and it turned out to be MISCLASSIFIED:
#:   the `append` the census saw was the `_emit_event` call inside the
#:   `on_session_merged=lambda record: self._emit_event(...)` argument — a
#:   callback the CALLEE invokes, not a record this door appends. #9206 removed
#:   that lambda (the shared writer journals its own `:Session` now) AND the
#:   door's two state writes (they are declared doors now), and what is left is
#:   stamps only: it appends NO record, so it belongs in `_NO_JOURNAL` — its
#:   first attempt at a home, `_APPLY_FIRST_DOORS`, was only reachable by
#:   counting `_journal_write_failure_warning` (a warning-string builder) as an
#:   append. See that entry's comment.
#:   ⛔ AND #9349 THEN MOVED THE STAMPS OUT TOO: the door has no write-like reach
#:   of its own any more, so it is in NEITHER set — `_NO_JOURNAL`'s note records
#:   why, and its pin asserts the absence rather than a row.
_MIXED_ARM_DOORS: set[str] = set()

#: Reach the handle with a write-like statement and append NO record at all, so
#: there is no lane to declare: each needs either a named graph-metadata lane
#: (the `_advance_ep_version` / `onboarding.metadata` pattern) or a move onto the
#: fold. Measured on #9124; this is the "19 writers that journal nothing" class
#: in the issue's landing path, at the count the source actually holds.
_NO_JOURNAL = {
    # ⛔ #9206/#9349 — `capture_session` LEFT this set, and the reason is the
    # history this list keeps: #9206 put it here after excluding
    # `_journal_write_failure_warning` (a receipt-warning STRING BUILDER) as a
    # commit point, because what remained at the handle was four provenance
    # stamps. #9349 then moved those stamps to their OWN declared doors
    # (`_stamp_capture_attempt_state` / `_stamp_entity_link_counters`, both
    # `apply-first-declared`) and the door now reaches no mutating handle call
    # at all — so a row here would be a STALE census entry, which
    # `test_the_inventories_hold_no_stale_entry` reds on. A door that issues no
    # handle write has no lane to name, and the two successors carry the stamps'
    # declaration.
    #   The pin that used to be cited HERE
    #   (`test_the_capture_door_delegates_its_writes_to_declared_doors`) was
    #   deleted by #4282 together with the door it inspected — keeping the
    #   citation would advertise a guard that no longer runs. What holds now is
    #   the census itself: the two stamp doors are declared members of the
    #   apply-first lane, and a successor that drops off this surface reds
    #   below.
    #   The five doors those writes MOVED to are pinned as declared doors by
    #   `test_every_mutating_sdk_entry_point_is_declared_or_inventoried` below,
    #   so a successor that drops off the census surface reds there instead of
    #   being absorbed here. ⚠️ Do NOT cite the apply-first pin for the whole
    #   set: of the three named above only `_materialize_session_source` is an
    #   apply-first member — `_stamp_capture_provenance` and
    #   `_write_session_and_turns` declare the LIVE (journal-first) lane, which
    #   is the stronger claim, and adding them here would be rejected by that
    #   pin by design.

    "_clear_reliability_cache",
    "_connect_issue_objects",
    "_extract_session_llm",
    "_extract_session_v2",
    "_index_source_merge",
    "_invalidate_inheritance_gate",
    "_mark_dirty",
    "_reapply_operator_promotion",
    "_record_embedding_marker",
    "_retry_cap_verdict",
    "_stamp_authored_word",
    "_sweep_dirty_roots",
    "_touch_inherited_at",
    "_write_props",
    "_write_reliability_cache",
    "backfill_v25",
    "ingest",
    "set_source_tier",
}

#: A statement the enumerator cannot PROVE read-only, in a function that appends
#: no record. Two shapes produce them: a statement built by a helper or yielded
#: by a generator, and one whose f-string interpolates a COMPUTED fragment (a
#: call, or a name bound from a parameter or a loop) that cannot be resolved. The
#: classifier's polarity — "anything not provably read-only is a write" — counts
#: both as writes, and they are listed here (rather than being silently called
#: reads) because that IS the census being honest about what it can prove. Every
#: entry below was checked by hand: the resolvable part of each statement is a
#: `MATCH … RETURN` read with no write keyword in it.
_UNRESOLVED = {
    "_assert_lifecycle_guard",
    # #9962: the ONLY handle reach in this function is its redelivery read (the
    # prior-mitigation census: id/content/strength plus
    # `live._terminal_excluded('m.status')` as `is_current`). The unresolvable
    # fragment is that helper's rendered predicate — the canonical terminal
    # vocabulary, which this row must NOT hand-roll into a literal here (that
    # is the #2901 defect class the predicate exists to stop), so the statement
    # cannot be flattened for the resolver. Checked by hand: the statement is a
    # `MATCH (op:Point {id:$oid})-[:mitigated_by]->(m:Point) RETURN …` read with
    # no SET/CREATE/MERGE/DELETE/REMOVE and no write procedure, and it appends
    # no record. What the function WRITES it does not write through the handle:
    # it delegates to `mitigate_operator`, which is its own censused door — so
    # this row declares a read and blesses no undeclared write.
    "_apply_ingest_mitigation",
    "_graph_presence",
    "_hop",
    # #7819: the statement is the shared `live.LIFECYCLE_FANOUT_QUERY` constant,
    # which this resolver reads only as a module-level string of the scanned
    # file — so it reports '<unresolved>' here (the same call inside
    # `_assert_lifecycle_guard` is reported the same way). By hand:
    # `MATCH (n:Point {id:$id}) RETURN n.is_operator, n.status,
    # coalesce(n.outdated, false)` — a read.
    "_lifecycle_terminal_marker",
    "_window_closure",
    "expand_relationships",
    # f-string interpolates a computed fragment (`{_label}`, `{relationship_type}`,
    # `{_capture_passthrough_read_fields()}`, …) — the #9124 review's P1 shape,
    # which a placeholder substitution used to flatten into a false READ.
    "_count_ep_factors",
    # #9063: both calibration readers hide their statement from the resolver, in
    # two DIFFERENT shapes — so "they build their Cypher the same way" is false
    # for one of them, which is why the shapes are spelled out separately here
    # rather than as one shared sentence. Both were re-read in `tortoise/sdk.py`
    # for this note:
    #   `calibrate_summary` binds a `where` LOCAL (`f"WHERE {not_operator} …"`)
    #   and interpolates it straight into the call — `proj.g.query(f"MATCH
    #   (n:Point) {where} … RETURN …")` — so the fragment it cannot unfold is
    #   that `where` TEXT.
    #   `_uncalibrated_ep_participants` composes a per-label `query` VARIABLE
    #   (`f"MATCH (n:{label}) WHERE {where} RETURN n.id"`, an `IN $ids` form, and
    #   a trailing `LIMIT` appended with `+=`) and passes the VARIABLE —
    #   `proj.g.query(query, params=params)` — so here the unresolvable fragment
    #   is the whole ASSEMBLED statement, not one bound piece of it.
    # What the two share is only WHY they are unresolvable, not what they look
    # like. Checked by hand: the resolvable part of
    # both statements is a `MATCH … RETURN` read with no write keyword (they
    # list uncalibrated points; neither writes the cache and neither appends a
    # record). Listed here rather than silently called a read, per this set's
    # own rule. (These two rows are why the census reddened on `origin/main`
    # at e08a669b2 — the entries, not the statements, were missing.)
    # ⚠️ The human step covers the WHOLE statement, not just its resolvable
    # part — for these rows the unresolvable fragment IS the statement text the
    # resolver cannot unfold (the `where` local for `calibrate_summary`, the
    # whole assembled `query` for `_uncalibrated_ep_participants`), so
    # "the readable half has no write keyword" would not settle it. Measured,
    # each function's only handle reach is ONE read:
    #   `calibrate_summary` — `MATCH … OPTIONAL MATCH … RETURN` (a read)
    #   `_uncalibrated_ep_participants` — `MATCH … RETURN n.id` (a read)
    # with no SET/CREATE/MERGE/DELETE/REMOVE in the assembled query and no CALL
    # to any `sdk.py` function the census proves reaches the write handle
    # (`calibrate_summary` names `set_point_baseline` only inside advice strings
    # — it never calls it, and neither function calls any other writer).
    # That second claim is pinned against the CENSUS's own handle surface —
    # "write OR not-proven-read", because that surface also keys a function whose
    # mutating call it could not RESOLVE (45 of its 83 entries are that shape),
    # so calling it "the list of writing functions" overstates it; the names this
    # file has already proven READ are subtracted — and not against a
    # hand-written list of method names: a check that
    # knew only `set_point_baseline` stayed green while the sentence it was cited
    # for went false (`self.invalidate_point(…)` is not a `set_point_baseline`
    # call and not a `<handle>.<verb>` call either).
    # ⛔ CITED BY SYMBOL, NEVER BY `sdk.py:<line>`. The numbers this note first
    # carried were 268 lines off within hours of being measured (they landed in
    # `_apply_source_inheritance`, whose `MATCH … REMOVE` is the opposite of the
    # claim) — the §7/a citation-rot class: a reader who follows a stale line
    # number cannot tell a rotten citation from a false claim. The symbol is
    # authoritative; `test_the_unresolved_hand_check_cites_symbols_not_line_numbers`
    # and `test_the_calibration_readers_assemble_no_write_clause` keep this note
    # honest instead of trusting it.
    "calibrate_summary",
    "_uncalibrated_ep_participants",
    "_cross_lens_pool",
    "_entity_name_holders",
    "_epistemic_label",
    "_find_operator",
    "_find_point_by_content",
    "_find_points_by_key",
    "_find_terminal_dedup_hit",
    # #8400 review P3: ONE query now backs both key legs. It was hoisted out of
    # the per-leg copies — those callers no longer reach the graph handle
    # themselves and are not scan entry points any more — so the two legs stop
    # carrying byte-identical copies of the terminal probe (the fork class the
    # `_dedup_match_clauses` extraction exists to kill). Hand check, same as
    # before: a literal `MATCH (n:Point) WHERE n.id IN $ids AND <terminal
    # predicate> RETURN n.id LIMIT 1` is a pure read that appends no record and
    # writes nothing; it only decides whether a fold WOULD be refused.
    "_terminal_holder_among",
    # #9350: `_capture_stamp_targets` runs the CALLER's statement prefix (it is
    # handed the shared `MATCH … WHERE` half so the read and the guarded write
    # cannot drift), so the resolver cannot unfold it. Checked by hand: the
    # resolvable part is a `MATCH … RETURN DISTINCT <id>` read with no write
    # keyword — it lists the ids a no-clobber provenance stamp WOULD write,
    # appends no record of its own, and writes nothing.
    "_capture_stamp_targets",
    "_read_capture_passthrough_props",
    "_reachable_claim_count",
    "_review_pool",
    "_review_prune",
    "_reverse_bfs_neighbors",
    "_semantic_dedup",
    "_source_reachable_by",
    # #9962: the state view's mitigation read interpolates
    # `live._live_only('m.status')` — the DAMPENER's own predicate
    # (`weights.py`'s `compute_operator_weight`), which is the point of the row:
    # the surfaced text and the computed weight must be one question, so the
    # predicate is shared rather than re-spelled as a literal. The resolver
    # cannot unfold a call, hence '<unresolved>'. Checked by hand: the statement
    # is `MATCH (op:Point {is_operator:true})-[r:mitigated_by]->(m:Point) WHERE
    # op.id IN $ids AND <live-only predicate> RETURN …` — a read, the only one
    # this function issues, with no write clause and no write procedure.
    "_state_mitigations",
    "_state_related_objects",
    "_state_related_points",
    "_unsweepable_dirty_roots",
    "list_batch",
    "paginated_query",
    "query",
    "search_sessions",
    "suggest_entry_points",
    "tortoise_fts_query",
    # ⛔ NO SECOND ROW FOR `calibrate_summary` / `_uncalibrated_ep_participants`.
    # They are declared ONCE, above (the #9063 entry). Both PRs that landed on
    # 2026-10-20 declared them at different offsets, so the merge kept both
    # copies: a duplicate element in a SET is silently collapsed by Python — the
    # intent still holds — but `ruff` B033 flags it, and the lint shard reds for
    # every PR that inherits it. The duplicates have since been removed twice
    # over (#9249 on `main`, and the same deletion here), so what this note
    # guards is the RULE rather than that repair: adding a row for a name that
    # is already in the set is the same defect as #9259's duplicate DICT key,
    # one container over — the declaration reads as two decisions where there
    # is one.
}

_INVENTORY = {
    "apply-first door (part 2)": _APPLY_FIRST_DOORS,
    "apply-first helper (part 2)": _APPLY_FIRST_HELPERS,
    "mixed arm (part 2)": _MIXED_ARM_DOORS,
    "no journal record to declare": _NO_JOURNAL,
    "statement not provably read-only": _UNRESOLVED,
}


# ── the enumerator ────────────────────────────────────────────────────────

def _runs_in_a_branch(node, field: str) -> bool:
    """Is `node.<field>` a BRANCH BODY (as opposed to a condition/iterator)?"""
    if isinstance(node, (ast.If, ast.While, ast.For, ast.AsyncFor)):
        return field in ("body", "orelse")
    if isinstance(node, (ast.Try, ast.TryStar)):
        return field in ("body", "handlers", "orelse", "finalbody")
    if isinstance(node, ast.ExceptHandler):
        return field == "body"
    if isinstance(node, ast.Match):
        return field == "cases"
    if isinstance(node, ast.match_case):
        return field == "body"
    return False


#: Distinguishes "never bound in this function" from "bound, but unknown".
_UNBOUND = object()


def _text(expr, consts, local):
    """Best-effort statement text for an argument expression, or ``None``.

    Resolves string literals, f-strings (RECURSING into each substituted
    expression, so a write clause carried by a name or a module constant is
    resolved instead of flattened away), string concatenation, module-level
    string constants and string locals assigned earlier in the function.

    ``None`` means "cannot resolve", and it is the SAFE answer: the caller
    counts an unresolvable statement as a WRITE. Substituting a placeholder for
    an unresolved part instead would make
    ``f"MATCH (n:Point {{id:$id}}) {action}"`` read as a non-write and drop the
    function out of the census entirely — the under-report direction.
    """
    if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
        return expr.value
    if isinstance(expr, ast.JoinedStr):
        parts = []
        for part in expr.values:
            if isinstance(part, ast.FormattedValue):
                resolved = _text(part.value, consts, local)
            elif isinstance(part, ast.Constant) and isinstance(part.value, str):
                resolved = part.value
            else:
                resolved = None
            if resolved is None:
                return None
            parts.append(resolved)
        return "".join(parts)
    if isinstance(expr, ast.Name):
        # A name BOUND in this function but with an unresolvable value must NOT
        # fall back to a module constant of the same name: that would classify
        # the statement from a literal that is not its runtime value (the
        # under-report direction).
        if expr.id in local:
            return local[expr.id]
        return consts.get(expr.id)
    if isinstance(expr, ast.BinOp):
        left, right = _text(expr.left, consts, local), _text(expr.right, consts, local)
        if left is None or right is None:
            return None
        return left + right
    return None


def _is_handle_ref(expr, aliases, params=()) -> bool:
    """Does this expression name the write handle?

    `proj.g`, `self._get_proj().g` (any attribute chain ending in `g`),
    `getattr(p, "g")`, a NAME already bound to one of those, or a PARAMETER of
    the enclosing function that is not `self`/`cls`. The last case is a
    deliberate over-report: a handle RECEIVED AS AN ARGUMENT has no binding to
    follow, and measured on this tree it is how a real writer hides
    (`clear_config_reset(self._get_proj().g)` → `clear_config_reset(g)` issues a
    `DETACH DELETE` at projection/__init__.py:1519 (`clear_config_reset`, def at 1509) with no declaring lane).
    Counting a parameter receiver as the handle can over-report a helper that
    takes a *projection* — which stays visible in the census — and it cannot
    hide a writer.
    """
    if isinstance(expr, ast.Attribute):
        return expr.attr == "g"
    if isinstance(expr, ast.Name):
        return expr.id in aliases or expr.id in params
    if isinstance(expr, ast.Call):
        return (getattr(expr.func, "id", None) == "getattr"
                and len(expr.args) >= 2
                and isinstance(expr.args[1], ast.Constant)
                and expr.args[1].value == "g")
    return False


def _add_alias_target(target, aliases) -> None:
    """Bind `target` (a name, or a tuple/list of names) to the handle."""
    if isinstance(target, ast.Name):
        aliases.add(target.id)
    elif isinstance(target, ast.Starred):
        _add_alias_target(target.value, aliases)
    elif isinstance(target, (ast.Tuple, ast.List)):
        for elt in target.elts:
            _add_alias_target(elt, aliases)


def _carries_handle(expr, aliases, params=()) -> bool:
    """Does this expression DELIVER the handle — directly, or through a wrapper?

    `proj.g`, an alias, a handle parameter, `getattr(p, "g")`, and the same
    inside a call's arguments (`closing(proj.g)`), a list/tuple
    (`for h in [proj.g]`), or either arm of a conditional
    (`h = proj.g if cond else None`). A wrapper is followed one level: enough for
    the shapes in this tree, and wrong in the safe direction when a call happens
    to take the handle as a side input.
    """
    if _is_handle_ref(expr, aliases, params):
        return True
    if isinstance(expr, ast.Call):
        return any(_carries_handle(arg, aliases, params) for arg in expr.args)
    if isinstance(expr, (ast.Tuple, ast.List, ast.Set)):
        return any(_carries_handle(elt, aliases, params) for elt in expr.elts)
    if isinstance(expr, ast.IfExp):
        return (_carries_handle(expr.body, aliases, params)
                or _carries_handle(expr.orelse, aliases, params))
    return False


def _bind_handle_aliases(node, aliases, params=()) -> None:
    """Every binding shape that can put the handle in a local.

    `h = proj.g`, `h: X = proj.g`, `raw = getattr(p, "g")`, `h, _ = proj.g, 1`,
    `if (h := proj.g):`, `with closing(proj.g) as h:`, `for h in [proj.g]:`,
    `h = proj.g if cond else None`. A receiver-name check alone misses every
    module that binds the handle first — which is how `event_store.py` and the
    onboarding funnel reach it.
    """
    if isinstance(node, ast.Assign):
        value, targets = node.value, node.targets
    elif isinstance(node, (ast.AnnAssign, ast.NamedExpr)):
        value, targets = node.value, [node.target]
    elif isinstance(node, ast.withitem):
        value, targets = node.context_expr, [node.optional_vars]
    elif isinstance(node, (ast.For, ast.AsyncFor)):
        value, targets = node.iter, [node.target]
    else:
        return
    if value is None or not targets:
        return
    if _carries_handle(value, aliases, params):
        for target in targets:
            if target is not None:
                _add_alias_target(target, aliases)
        return
    if isinstance(value, (ast.Tuple, ast.List)) and len(targets) == 1 \
            and targets[0] is not None:
        # `strict=False` states the existing behaviour: an unpacking target
        # SHORTER than the value tuple silently truncates, which is all this
        # alias walk needs (it is a census, not a validator).
        pairs = zip(value.elts, _flatten_target(targets[0]), strict=False)
        for part, target in pairs:
            if _carries_handle(part, aliases, params):
                _add_alias_target(target, aliases)


def _flatten_target(target):
    if isinstance(target, (ast.Tuple, ast.List)):
        return list(target.elts)
    return [target]


def _bound_names(node, consts, local):
    """``{name: text-or-None}`` for a single string-ish assignment/annotation.

    A name bound here is recorded even when its text is unresolvable (value
    ``None``), so `_text` can tell "bound, unknown" from "not bound".
    """
    if isinstance(node, ast.Assign) and len(node.targets) == 1 \
            and isinstance(node.targets[0], ast.Name):
        return {node.targets[0].id: _text(node.value, consts, local)}
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) \
            and node.value is not None:
        return {node.target.id: _text(node.value, consts, local)}
    if isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name):
        extra = _text(node.value, consts, local)
        if extra is not None and node.target.id in local \
                and local[node.target.id] is not None:
            return {node.target.id: local[node.target.id] + extra}
        return {node.target.id: None}
    return {}


def _module_consts(tree):
    """String constants bound at MODULE level, and only those.

    An `ast.walk` over the whole tree would let a string assigned inside one
    function become a module-wide constant, so a different function's `stmt`
    could be classified from a literal that is not its runtime value — the under-
    report direction this enumerator exists to avoid.
    """
    out: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            for name, text in _bound_names(node, out, out).items():
                if text is not None:
                    out.setdefault(name, text)
    return out


def enumerate_handle_surface(source: str, filename: str = "<src>") -> dict:
    """Map each innermost function to what it does at the write handle.

    Returns ``{scope-qualified name: {lineno, writes, reads, appends, scopes,
    aliases, params, def_linenos, duplicate_writer_defs}}`` for every function
    holding at least one mutating call. `def_linenos` lists every definition of
    that BARE name in this module that writes, and `duplicate_writer_defs` is
    set when there are two or more: a bare name is what the inventories key on,
    so one row would account for both writers. `_duplicate_writer_offenders`
    turns the flag into the gates' refusal.
    Both call lists carry the CONTROL-FLOW DEPTH as their last element —
    `writes` entries are ``(line, verb, statement-prefix, depth)`` and `appends`
    entries are ``(line, depth)`` — because an append only guarantees the record
    precedes a write when it is no deeper in the branch tree than that write.
    Nested functions are attributed to THEMSELVES, never to their enclosing
    function (a helper's write is not its caller's).

    ⛔ THE KEY IS SCOPE-QUALIFIED — `Door.a_door`, `FalkorProjection.rebuild_all`,
    `Outer.inner` — NOT the bare function name (#9124 review, round 2). Keying
    on the bare name collapsed a class method and a module helper of the same
    name into ONE fact, and every inventory lookup was name-only, so a single
    `_APPLY_FIRST_DOORS` entry could account for a SECOND, undeclared writer of
    that name: their writes, appends and scopes merged, and the merged fact read
    `declared` (or `journal-first`) because one of the two declared — and the
    flip gate's `accounted` skip then returned BEFORE its duplicate-def
    assertion, so that shape was not reported either. Two functions in different
    scopes cannot share a key now, and a redefinition in the SAME scope is kept
    apart with an ``@<lineno>`` suffix, so no merge is possible at all (which is
    stronger than merging and flagging it: a flag can be skipped, a collision
    that cannot happen cannot).

    The inventories are still written as bare names because that is how an SDK
    door is named, so `_inventory_of` resolves a scoped key to its LAST
    component — safe only because
    `test_no_two_census_entries_share_a_bare_function_name` fails the moment two
    censused functions would share one.
    """
    tree = ast.parse(source, filename=filename)
    consts = _module_consts(tree)
    # Keyed by DEFINITION, not by name: a module may define the same function
    # name twice (a Protocol stub plus its implementation — `apply`,
    # `exact_objects`), and merging them would report one def's line with
    # another's writes. The public surface is still keyed by name (that is what
    # an inventory entry names), and a name whose defs BOTH write is flagged.
    facts: dict[tuple[str, int], dict] = {}

    def visit(node, current, local, cond=0, scope=()):
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            # The ENCLOSING path, so the public key can be scope-qualified. It is
            # carried on the fact because the surface is assembled after the walk.
            scope = (*scope, node.name)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            current = (node.name, node.lineno)
            fact = facts.setdefault(current, {
                "name": node.name, "lineno": node.lineno, "scope": scope,
                "writes": [], "reads": 0, "appends": [], "scopes": [],
                "aliases": set(), "params": set(), "scope_spans": [],
                "end_lineno": getattr(node, "end_lineno", node.lineno)
                or node.lineno,
            })
            args = node.args
            fact["params"] = {
                arg.arg for arg in (
                    list(args.posonlyargs) + list(args.args) + list(args.kwonlyargs)
                    + ([args.vararg] if args.vararg else [])
                    + ([args.kwarg] if args.kwarg else []))
                if arg.arg not in ("self", "cls")
            }
            local = {}
            for decorator in node.decorator_list:
                if isinstance(decorator, ast.Call):
                    opener = getattr(decorator.func, "id", None) or getattr(
                        decorator.func, "attr", None)
                    if opener in _SCOPE_CALL_NAMES:
                        facts[current]["scopes"].append((decorator.lineno, opener))
                        if opener in _DECLARATION_DECORATORS:
                            # A lane DECORATOR covers the whole body — for BOTH
                            # claims (#9124's journal-first lane and #9206's
                            # apply-first one).
                            facts[current]["scope_spans"].append(
                                (node.lineno,
                                 getattr(node, "end_lineno", node.lineno) or
                                 node.lineno))
        if current is None:
            for child in ast.iter_child_nodes(node):
                visit(child, current, local, cond, scope)
            return

        fact = facts[current]
        # `g = proj.g` / `raw = getattr(graph, "g", None)` — event_store, the
        # onboarding funnel and the three read-side modules all bind the handle
        # to a LOCAL, so a receiver-name check alone would miss their writers.
        _bind_handle_aliases(node, fact["aliases"], fact["params"])
        for bound_name, bound_text in _bound_names(node, consts, local).items():
            prior = local.get(bound_name, _UNBOUND)
            if prior is not _UNBOUND and prior is not None \
                    and bound_text is not None \
                    and _statement_writes(prior) != _statement_writes(bound_text):
                # Rebound to a statement of the OTHER class on some path: which
                # one runs depends on the branch, so neither may be assumed.
                local[bound_name] = None
            else:
                local[bound_name] = bound_text

        if isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                call = item.context_expr
                if not isinstance(call, ast.Call):
                    continue
                opener = getattr(call.func, "id", None) or getattr(
                    call.func, "attr", None)
                if opener in _SCOPE_CALL_NAMES and node.body:
                    # Mark the context call so the plain-call arm below does not
                    # ALSO claim the rest of the function: a `with` releases the
                    # ContextVar at its exit, so only the body is authorized.
                    fact.setdefault("_with_calls", set()).add(id(call))
                    start = min(stmt.lineno for stmt in node.body)
                    end = max(getattr(stmt, "end_lineno", stmt.lineno) or stmt.lineno
                              for stmt in node.body)
                    if opener in _SCOPE_CALL_NAMES:
                        fact["scope_spans"].append((start, end))

        if isinstance(node, ast.Call):
            opener = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if opener and _APPEND_CALL_RE.match(opener) \
                    and opener not in _APPEND_NOT_A_RECORD:
                fact["appends"].append((node.lineno, cond))
            elif opener in _SCOPE_CALL_NAMES:
                fact["scopes"].append((node.lineno, opener))
                if id(node) not in fact.get("_with_calls", set()):
                    # `_cache_write_authorized(...)` used as a plain call sets the
                    # ContextVar for the REST of this context (it is not released
                    # until an outer scope resets it), so it covers to the end.
                    fact["scope_spans"].append((node.lineno, fact["end_lineno"]))
            elif isinstance(node.func, ast.Attribute) \
                    and node.func.attr in _HANDLE_MUTATING_VERBS \
                    and _is_handle_ref(node.func.value, fact["aliases"],
                                       fact["params"]):
                # `execute_command("GRAPH.QUERY", <graph>, <cypher>)` — the
                # statement sits at index 2 (cypher_guard's own pinned shape),
                # every other verb takes it positionally.
                index = 2 if node.func.attr == "execute_command" else 0
                arg = node.args[index] if len(node.args) > index else None
                text = _text(arg, consts, local) if arg is not None else None
                if text is None or _statement_writes(text):
                    fact["writes"].append(
                        (node.lineno, node.func.attr,
                         " ".join((text or "<unresolved>").split())[:90], cond))
                else:
                    fact["reads"] += 1

        for field, child in ast.iter_fields(node):
            # CONTROL-FLOW DEPTH counts a statement that runs only in a BRANCH,
            # not one that merely appears in a branch's CONDITION: the append in
            # `if self._journal_event_raw_write(...) is False: return False`
            # guards its own write (`_event_raw_write_door`), so counting the
            # `if` test as conditional would misread a journal-first door as
            # apply-first.
            nested = cond + (1 if _runs_in_a_branch(node, field) else 0)
            if isinstance(child, list):
                for item in child:
                    if isinstance(item, ast.AST):
                        visit(item, current, local, nested, scope)
            elif isinstance(child, ast.AST):
                visit(child, current, local, nested, scope)

    visit(tree, None, {})
    # A name defined more than once whose definitions BOTH write the handle
    # cannot carry a name-level verdict: the inventories are keyed on the BARE
    # name (`_inventory_of` resolves a scoped key to its last component), so ONE
    # row would account for both writers and the undeclared twin would pass on
    # its sibling's declaration. Flagged on EVERY writing def of the name and
    # carried into the surface, where `_duplicate_writer_offenders` is the one
    # reader the gates share. THE FLAG MUST HAVE A PRODUCER HERE: round 4 left
    # four gates reading it while nothing wrote it, so all four were tautologies
    # (`fact.get(...)` returned `None` for every entry, and `sorted()` of the
    # companion `def_linenos` was unreachable) — an inert enforcer, which is the
    # class this suite exists to prevent (#9124 review, round 5).
    writing_defs: dict[str, list[int]] = {}
    for fact in facts.values():
        if fact["writes"]:
            writing_defs.setdefault(fact["name"], []).append(fact["lineno"])

    surface: dict[str, dict] = {}
    for fact in facts.values():
        if not fact["writes"]:
            continue
        fact["writes"].sort()
        key = ".".join(fact["scope"])
        if key in surface:
            # A redefinition in the SAME scope (shadowing). Kept apart rather
            # than merged: mixing one def's lane with another def's write is
            # exactly the unsound verdict the scope-qualified key prevents.
            key = f"{key}@{fact['lineno']}"
        def_linenos = sorted(writing_defs[fact["name"]])
        surface[key] = {
            "lineno": fact["lineno"], "writes": list(fact["writes"]),
            "reads": fact["reads"], "appends": list(fact["appends"]),
            "scopes": list(fact["scopes"]), "aliases": set(fact["aliases"]),
            "params": set(fact["params"]),
            # The LINE SPANS a lane covers, so "declared" can mean the writes run
            # INSIDE the lane rather than that the function mentions one.
            "scope_spans": list(fact["scope_spans"]),
            "duplicate_writer_defs": len(def_linenos) > 1,
            "def_linenos": def_linenos,
        }
    return surface


def _writes_are_inside_a_lane(fact) -> bool:
    """Do ALL of this function's writes run INSIDE a lane it opens?

    Presence is not containment (the #9124 final-review P2): a function that
    opens `with _replay_write_scope()` for a READ and then writes outside it
    mentions a lane without holding one around the write, and arming would refuse
    that write. `scope_spans` records the line range each lane actually covers
    (a `with` body, or the whole body for a lane decorator, or the rest of the
    context for a plain `_cache_write_authorized` call), so this asks the real
    question. Measured over the whole population when it landed: 0 functions
    regress.
    """
    spans = fact.get("scope_spans") or []
    if not fact.get("scopes") or not spans:
        return False
    return all(any(start <= write[0] <= end for start, end in spans)
               for write in fact["writes"])


def _journal_first_violation(fact) -> str | None:
    """Why this function's record does not provably precede its write, or None.

    One check, run PER WRITE and existentially over the appends: for EVERY
    mutating statement there must be some append with (a) a smaller line number
    and (b) a control-flow depth no greater than that write's. (a) alone is not
    enough — an `_emit_event` inside an `if`/`try` while the write runs
    unconditionally is the "declaration blesses a cache write with no committed
    record" shape even when its line comes first; (b) alone is not enough — a
    record appended AFTER the write is not a commit point. Neither, of course,
    is sufficient: a record skipped by an earlier `return` is invisible to an
    AST.
    """
    if not fact["appends"]:
        return ("appends no record at all — a live lane is 'append first, then "
                "write the cache'")
    # PER WRITE, existentially over the appends: a write is covered when SOME
    # record both precedes it and runs no deeper in the branch tree than it does.
    # Per-write (not "against the shallowest write") because a write inside a
    # branch EARLIER than every append is still an unjournalled write — and
    # existential over the appends because a door may emit a best-effort
    # diagnostic record inside `try/except` before its real unconditional one.
    uncovered = [write for write in fact["writes"]
                 if not any(append[0] < write[0] and append[1] <= write[3]
                            for append in fact["appends"])]
    if not uncovered:
        return None
    shallowest = min(uncovered, key=lambda write: write[3])
    earliest = min(fact["appends"])
    if earliest[0] > shallowest[0]:
        return (f"writes at line {shallowest[0]} "
                f"({shallowest[1]}: {shallowest[2]!r}) BEFORE its first append "
                f"at {earliest[0]} — that is the apply-first shape, and the "
                "declaration would bless it")
    return (f"has no append that covers its write at line {shallowest[0]} "
            f"(depth {shallowest[3]}); its records are at "
            f"{[(line, depth) for line, depth in fact['appends']]}, so a branch "
            "can reach that write with no record appended")


def _kind(fact) -> str:
    """Classify one function: declared, or which inventory it belongs to.

    The two DECLARED kinds are kept apart because the claims differ: a
    journal-first lane asserts the record precedes the write, an apply-first one
    asserts the opposite ORDER by decision (#9206), and conflating them would
    let a door take the weaker claim silently — the lane-about-lane defect this
    suite keeps closing.
    """
    openers = {opener for _line, opener in fact["scopes"]}
    if openers & set(_APPLY_FIRST_LANE_NAMES) and openers & set(_LIVE_LANE_NAMES):
        # Both claims at once is not a stronger declaration: a later edit cannot
        # tell which one the door means, and the journal-first pin would pass on
        # an apply-first body.
        return "conflicting-lanes"
    if fact["scopes"]:
        if not _writes_are_inside_a_lane(fact):
            return "outside-lane"
        if openers & set(_APPLY_FIRST_LANE_NAMES):
            return "apply-first-declared"
        if openers & {"_derived_recompute_write_scope"}:
            # #9206 review P1: its own kind, NOT `declared` (the maximal-trust
            # class). This lane appends nothing BY CONSTRUCTION, so labelling it
            # `declared` would file an unjournalled write where the arming gate
            # trusts most and where no "appends a record" requirement applies.
            return "derived-recompute"
        return "declared"
    if not fact["appends"]:
        return "no-journal"
    return "apply-first" if _journal_first_violation(fact) else "journal-first"


def _bare_name(key: str) -> str:
    """The BARE function name of a scope-qualified census key.

    `TortoiseProjection.delete_point` -> `delete_point`. The `@<lineno>` suffix
    that keeps a same-scope redefinition apart is stripped too, so that suffix
    cannot be used to hide a name collision from
    `test_no_two_census_entries_share_a_bare_function_name`.
    """
    return re.sub(r"@\d+$", "", key.rsplit(".", 1)[-1])


def _inventory_of(name: str):
    """The inventory a censused function belongs to, by its BARE name.

    `name` is the scope-qualified key (`TortoiseSDK.delete_point`); the
    inventories are written as bare names because that is how an SDK door is
    named. Resolving to the last component is only safe while no two censused
    functions share one — `test_no_two_census_entries_share_a_bare_function_name`
    is the gate that keeps that true.
    """
    bare = _bare_name(name)
    for reason, members in _INVENTORY.items():
        if bare in members:
            return reason
    return None


def _all_inventoried() -> set[str]:
    return {name for members in _INVENTORY.values() for name in members}


def _duplicate_writer_offenders(surface: dict, where: str = "") -> list[str]:
    """ONE message per censused name whose DEFINITIONS all write the handle.

    The name-level verdict is what the inventories, `_kind` and the flip gate's
    `accounted` skip all speak in, so a name defined twice with both definitions
    writing has no sound single answer — it must be refused, not classified.
    `enumerate_handle_surface` sets `duplicate_writer_defs`/`def_linenos`; this
    is the ONE reader the gates share, so the flag cannot be asserted at one
    site and silently dropped at another.

    NON-VACUITY is the point of the split: the four gates used to read
    `fact.get("duplicate_writer_defs")` while nothing produced the key, so every
    one of those reads was a tautology no surface could redden. The producer is
    pinned by `test_two_writing_definitions_of_one_name_are_flagged` and this
    reader's use on the gates' path by
    `test_a_duplicate_writer_definition_reds_the_gates`.
    """
    return [
        f"{where}{name}:{fact['lineno']} is defined more than once and BOTH "
        "definitions write the handle — the name-level verdict mixes one def's "
        f"lane with another def's write (defs at {sorted(fact['def_linenos'])})"
        for name, fact in sorted(surface.items(), key=lambda kv: kv[1]["lineno"])
        if fact.get("duplicate_writer_defs")
    ]


# ── the live gate: every SDK entry point is declared or accounted for ─────

#: The kinds this gate accepts as DECLARED — the tuple the `derived-recompute`
#: row was missing (review MINOR, latent): `_kind` has THREE declared kinds, and
#: accepting only two of them reported a `_derived_recompute_write_scope` door as
#: "NO declared lane and no inventory entry", whose natural remedy is an
#: INVENTORY row — i.e. it would have converted a declared door into an
#: inventoried one and weakened it. No live instance (`_derived_recompute_write_scope`
#: is opened in `tortoise/source_identity.py`, not in `sdk.py`), but a door added
#: to `sdk.py` would have got the wrong instruction.
#: ⛔ The producer-without-consumer mirror of the round-5 `duplicate_writer_defs`
#: defect (four consumers, no producer).
_DECLARED_KINDS = frozenset(
    {"declared", "apply-first-declared", "derived-recompute"})


def test_every_mutating_sdk_entry_point_is_declared_or_inventoried():
    """THE gate. A new mutating `sdk.py` function must declare a lane.

    Non-vacuity is proven two ways: `test_the_enumerator_flags_an_undeclared_
    journal_first_door` drives this same enumerator over a synthetic module (so
    the detector is exercised even when the real tree is clean), and the change
    that added this suite was run with one declaration removed — the failure
    names `supersede_point` by function and line.
    """
    surface = enumerate_handle_surface(SDK_PY.read_text(), str(SDK_PY))
    offenders = _duplicate_writer_offenders(surface)
    for name, fact in sorted(surface.items(), key=lambda kv: kv[1]["lineno"]):
        if fact.get("duplicate_writer_defs"):
            # Already reported above, with both definitions' line numbers: a
            # merged name-level verdict is refused, never classified.
            continue
        if ("tortoise/sdk.py", name) in _PROVEN_READS:
            # Proven read-only in review: the ONE register the flip gate also
            # consults, so the two gates cannot demand contradictory bookkeeping.
            continue
        kind = _kind(fact)
        reason = _inventory_of(name)
        if kind == "conflicting-lanes":
            offenders.append(
                f"{name}:{fact['lineno']} declares BOTH a journal-first and an "
                f"apply-first lane ({fact['scopes']}) — the door must state ONE "
                "order; the stronger (journal-first) claim is the one a "
                "journal-first body earns")
            continue
        if kind == "outside-lane":
            offenders.append(
                f"{name}:{fact['lineno']} OPENS a lane ({fact['scopes']}) but "
                f"writes outside it at line {fact['writes'][0][0]} — presence is "
                "not containment; move the write inside the lane or declare one "
                "that covers it")
            continue
        if kind in _DECLARED_KINDS:
            if reason is not None:
                offenders.append(
                    f"{name}:{fact['lineno']} is DECLARED ({fact['scopes']}) but "
                    f"still inventoried as {reason!r} — drop the inventory entry")
            continue
        if reason is None:
            offenders.append(
                f"{name}:{fact['lineno']} reaches the write handle "
                f"({fact['writes'][0][1]} at line {fact['writes'][0][0]}: "
                f"{fact['writes'][0][2]!r}) with NO declared lane and no "
                f"inventory entry [{kind}]")
            continue
        expected = {
            "apply-first door (part 2)": "apply-first",
            "apply-first helper (part 2)": "apply-first",
            "mixed arm (part 2)": "journal-first",
            "no journal record to declare": "no-journal",
            "statement not provably read-only": "no-journal",
        }[reason]
        if kind != expected:
            offenders.append(
                f"{name}:{fact['lineno']} is inventoried as {reason!r} but the "
                f"source now reads {kind!r} (writes={[w[0] for w in fact['writes']]}, "
                f"appends={fact['appends']}) — the inventory reason is stale")
    assert not offenders, (
        "§17.1's write-handle surface is not fully accounted for. Declare the "
        "lane (`_live_write_scope` AFTER the journal append, or "
        "`@_declares_live_write_lane` on a journal-first door), or add the "
        "function to the matching inventory above with its reason:\n  "
        + "\n  ".join(offenders))


def test_the_inventories_hold_no_stale_entry():
    """The inventories can only shrink: a renamed/declared/deleted name fails."""
    surface = enumerate_handle_surface(SDK_PY.read_text(), str(SDK_PY))
    stale = sorted(_all_inventoried() - {_bare_name(n) for n in surface})
    assert not stale, (
        "these inventory entries no longer name a function with a mutating "
        "handle call (renamed, deleted, or the call was removed) — delete the "
        f"entries so the census stays a census: {stale}")


def test_no_two_census_entries_share_a_bare_function_name():
    """One inventory entry must not be able to cover TWO writers (#9124 r2).

    The inventories are written as BARE names (`delete_point`, not
    `TortoiseSDK.delete_point`), so `_inventory_of` resolves a scope-qualified
    key to its last component. That is sound only while no two CENSUSED functions
    share one: with `Foo.apply_first` and `Bar.apply_first` both reaching the
    handle, both would carry the single `_APPLY_FIRST_DOORS` entry and the
    undeclared one would pass its own gate. The FACTS cannot merge any more (the
    keys are scope-qualified), so this is the gate that keeps the INVENTORY
    LOOKUP honest — and it names both keys, so the next reader qualifies the
    entry instead of guessing which scope it meant.
    """
    offenders = []
    scanned = 0
    for path in (SDK_PY, EVENT_STORE_PY):
        surface = enumerate_handle_surface(path.read_text(), str(path))
        scanned += len(surface)
        by_bare: dict[str, list[str]] = {}
        for key in surface:
            by_bare.setdefault(_bare_name(key), []).append(key)
        for bare, keys in sorted(by_bare.items()):
            if len(keys) > 1 and _inventory_of(keys[0]) is not None:
                offenders.append(f"{path.name}: {bare!r} -> {sorted(keys)}")
    assert scanned > 50, (
        "the scan found almost nothing — an enumerator that returned an empty "
        f"surface would pass this gate vacuously (scanned={scanned})")
    assert not offenders, (
        "two censused functions share a bare name AND that name is in an "
        "inventory, so ONE entry accounts for both — the undeclared one is "
        "covered by the other's row. Qualify the inventory entry (or split it) "
        "and resolve it the same way in `_inventory_of`:\n  "
        + "\n  ".join(offenders))


# ── the hand-check note behind the two #9063 rows is CHECKABLE ────────────
#
# That note is the only evidence a reader has for WHY `calibrate_summary` and
# `_uncalibrated_ep_participants` sit in `_UNRESOLVED`. It first carried
# `sdk.py:<line>` anchors and they were 268 lines off: following the first one
# landed in `_apply_source_inheritance`, a function that WRITES
# (`MATCH … REMOVE`) — the opposite of what the citation was offered as evidence
# for. A reader who cannot tell a rotten citation from a false claim deletes the
# rows, and deleting them reds the census (both classify `no-journal`). So the
# note is pinned twice: no line-number anchor may come back (the #8093/#8506
# citation-rot class — the SYMBOL is authoritative, the number is a
# convenience), and the claim it makes is re-derived from `sdk.py` rather than
# believed.

#: A `sdk.py:<line>` anchor, in ANY of the shapes a human actually writes.
#: `tortoise/sdk.py` is ~37k lines and moves under the note on almost every
#: merge, so a number rots here without anything reddening.
#:
#: ⛔ WIDENED (review, measured): the first version was `r"sdk\.py:\d+"`, which
#: matched ONLY the no-whitespace form. Every ordinary spelling of the same
#: anchor went green — `sdk.py: 23508`, `sdk.py line 23508`, `sdk.py:L23508`,
#: `sdk.py, 23508`, `sdk.py 23508` — so the pin forbade a citation style it
#: let through in five of six shapes. The rot does not care about whitespace:
#: `sdk.py: 23508` decays into a pointer at whatever function holds that line
#: exactly as `sdk.py:23508` does. `test_the_line_anchor_matches_every_spelling`
#: holds the variants red-eyed.
#: ⛔ WIDENED AGAIN (review, measured): `[\s,:]*` cannot consume a CLOSING
#: BACKTICK, so the spelling this very note uses — a backticked module path — went
#: green: `` `sdk.py`:23508 ``, `` `sdk.py` line 23508 ``, `` `sdk.py` 23508 ``,
#: `` `tortoise/sdk.py`:23508 ``. The backticked form is the MOST likely next
#: spelling, since the note writes `` `tortoise/sdk.py` `` in backticks
#: throughout; `@` (a "file@rev" habit) and `#?`/`line #23508` are admitted too.
#: Same rot, same reasoning:
#: the separator is noise, the `sdk.py` + digits pair is the citation.
#: ⛔ WIDENED AGAIN (review, measured): `~` was not in the separator class, so
#: the TILDE spelling — the one this branch's own narrative uses ("a `line ~511`
#: anchor had already rotted", "`(line ~700)` in `_handle_query_calls`") — went
#: green: `sdk.py:~23508`, `sdk.py (line ~23508)` and `` `sdk.py` ~23508 `` all
#: missed. Same rot: the number still points at whatever function holds that
#: line. The tilde is a separator like any other, so it is admitted.
#: ⛔ AND THE MAGNITUDE GUARD (review, measured): admitting `~` made
#: `sdk.py ~37k lines` — a LENGTH statement, not a citation — match, because the
#: tilde consumed the separator and `37` is a digit run. The sentence above named
#: letters between the path and the digits as what kept a magnitude out;
#: `sdk.py is ~37k lines` alone does not hold that line (it passes on the word
#: "is"), and `sdk.py ~37k lines` went red with no letters at all. `(?![\dkK])`
#: is what actually does the work: `\d+` cannot backtrack into the run (`3`
#: leaves `7k`, which fails the same lookahead), so neither `sdk.py ~37k lines`
#: nor `sdk.py ~ 37k lines` matches, while every digit-final spelling above still
#: does. Both spellings are in `must_not_match`.
#: ⛔ THE SEPARATOR CLASS IS ENUMERATED, NOT "NOISE" (round 6, measured): an
#: earlier comment here reasoned as if any separator were admitted — a class only
#: admits what it lists. The em/en dash is this file's own dominant prose
#: separator (`sdk.py — 23508`) and `near line` / `near <n>` are ordinary
#: citation prose; both missed. Any FURTHER separator is a one-character change
#: in the class below, and this line is the reminder that it is not a rule.

_SDK_LINE_ANCHOR = re.compile(
    r"sdk\.py[\s,:`@#()~\u2014\u2013-]*"
    r"(?:(?:near\s+)?(?:at\s+)?line[\s:,#~]*)?(?:near\s+)?(?:at\s+)?[Ll]?#?\d+"
    r"(?![\dkK])",
    re.IGNORECASE)

#: ⛔ NO LOCAL WRITE-CLAUSE REGEX HERE. What counts as a write is
#: `_statement_writes` + `_WRITE_CLAUSE_RE` from `tortoise.projection` — the
#: SAME pair the runtime door consults (this file's docstring, #3595). The first
#: version of this pin declared its own `\b(?:SET|CREATE|MERGE|DELETE|DETACH|
#: REMOVE)\b` and every difference from the canonical def ran the wrong way:
#: it was case-sensitive and had no `FOREACH`/`DROP` (a lower-case `set`, a
#: `FOREACH (…) | SET …` or a `DROP INDEX` appended to these statements is a
#: write to the door and was invisible here — the note could rot false with
#: nothing red), and it dropped the `(?<![\w.$:])` lookbehind that keeps a
#: PROPERTY named like a keyword from reading as a clause (`RETURN n.SET` is a
#: well-formed READ; #7174 is the recorded false refusal that lookbehind fixed).


def _assembled_write_evidence(assembled: str) -> str | None:
    """The write evidence in an assembled statement, or ``None`` for a read.

    Delegates the VERDICT to `_statement_writes` and only uses the canonical
    `_WRITE_CLAUSE_RE` (over the same literal-stripped text `_statement_writes`
    reads) to name the clause for the failure message — so the message and the
    door cannot disagree either. A statement that writes only through a write
    PROCEDURE (`CALL db.idx.fulltext.createNodeIndex(…)`) has no clause to
    name, so it is reported by the classifier alone rather than as a read.
    """
    if not _statement_writes(assembled):
        return None
    clause = _WRITE_CLAUSE_RE.search(_LITERAL_OR_COMMENT_RE.sub(" ", assembled))
    if clause is not None:
        return repr(clause.group(0))
    return "a write PROCEDURE call (no write clause, per `_statement_writes`)"

#: The two rows the #9063 note covers, each with the read shape the note states.
_CALIBRATION_READERS: tuple[tuple[str, str], ...] = (
    ("calibrate_summary", "OPTIONAL MATCH"),
    ("_uncalibrated_ep_participants", "RETURN n.id"),
)


def _unresolved_block_span(source: str) -> str:
    """The source of the ONE module-level `_UNRESOLVED` set literal.

    Sliced by the AST's own line numbers, so the span cannot silently shrink to
    a fragment that happens to pass.

    ⛔ EXACTLY ONE assignment, asserted rather than assumed (review P3): the
    first version looped and `return`ed on the first match, so a SECOND
    `_UNRESOLVED = {…}` later in the module — the one that is live at runtime,
    because it rebinds the name — left both pins validating the DEAD first
    literal while the governing block went unverified, silently. Counting the
    matches makes a rebinding red instead of skipped.
    """
    lines = source.splitlines()
    matches = [
        node for node in ast.parse(source).body
        if isinstance(node, ast.Assign)
        and any(getattr(t, "id", None) == "_UNRESOLVED" for t in node.targets)]
    assert len(matches) == 1, (
        f"`_UNRESOLVED` is assigned {len(matches)} times at module level — a "
        "second assignment REBINDS the name, so these pins would check the "
        "dead first literal while the live block went unverified. Keep one "
        "assignment (or move the pins with the rebinding)")
    node = matches[0]
    return "\n".join(lines[node.lineno - 1:node.end_lineno])


def _unresolved_block_source() -> str:
    """The `_UNRESOLVED` block of THIS file, comments included."""
    return _unresolved_block_span(Path(__file__).read_text())


def _comments_in(block_source: str) -> str:
    """The COMMENT text inside a source block, and nothing else.

    The `#9063` note lives in comments; the two function names it must cite live
    in the SET ROWS (`"calibrate_summary",` is itself a string element of the
    literal). Searching the block as plain text therefore cannot tell the note
    from the rows: deleting the whole note while keeping the rows left the
    positive half of the symbol pin green, i.e. the exact failure it exists to
    prevent (a reader who cannot re-verify the note deletes the rows) was
    unguarded on the symbol side. Resolving the anchor against COMMENT tokens
    only makes that deletion red.

    Tokenizes with `tokenize`, so a `#` inside a string literal is not a
    comment and a `#9063`-shaped string element cannot be mistaken for one.
    """
    comments: list[str] = []
    for token in tokenize.generate_tokens(io.StringIO(block_source).readline):
        if token.type == tokenize.COMMENT:
            comments.append(token.string.lstrip("#"))
    return "\n".join(comments)


def _missing_citations(block_source: str) -> list[str]:
    """The `_CALIBRATION_READERS` rows this block's COMMENT text does not cite.

    ⛔ THE PIN'S OWN PREDICATE, and it lives HERE — module level, one definition
    (review P2). It used to be spelled out inside the pin, while the non-vacuity
    test defined its own byte-identical copy: the test therefore observed
    nothing the pin computed and stayed green when the pin's positive half was
    reverted to the pre-fix `name in block`, i.e. it could not fail on the
    regression it claims to hold. Both now drive this function, so reverting
    `_comments_in` to the raw block reds the pin AND the test.

    Returns one entry per row whose name AND read shape are not on a single
    comment line — the citation must be one line, because a name mentioned
    somewhere and a shape mentioned elsewhere does not let a reader re-verify
    the note.
    """
    note_lines = _comments_in(block_source).splitlines()
    return [name for name, shape in _CALIBRATION_READERS
            if not any(name in line and shape in line for line in note_lines)]


def _string_constants(node) -> list[str]:
    """Every string literal inside `node` (f-string fragments included)."""
    return [sub.value for sub in ast.walk(node)
            if isinstance(sub, ast.Constant) and isinstance(sub.value, str)]


def _local_bindings(func) -> dict[str, list[ast.expr]]:
    """`name -> every value assigned to it` in `func` (one entry per BRANCH).

    Every binding, not the last one: `_uncalibrated_ep_participants` builds its
    `query` in an `if`/`else` pair, and checking only the surviving branch would
    leave the other one unverified.
    """
    nodes = ast.walk(func) if isinstance(func, ast.AST) else func
    bound: dict[str, list[ast.expr]] = {}
    for node in nodes:
        if isinstance(node, ast.Assign) and node.value is not None:
            for target in node.targets:
                if isinstance(target, ast.Name):
                    bound.setdefault(target.id, []).append(node.value)
        elif (isinstance(node, ast.AnnAssign) and node.value is not None
              and isinstance(node.target, ast.Name)):
            bound.setdefault(node.target.id, []).append(node.value)
        elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
            # A loop/comprehension target is not a hole: its VALUE is its
            # iterable, so `{label}` resolves to `_CALIBRATION_GATED_LABELS`'s
            # strings instead of being reported as unreadable.
            if isinstance(node.target, ast.Name):
                bound.setdefault(node.target.id, []).append(node.iter)
        elif (isinstance(node, ast.AugAssign)
              and isinstance(node.target, ast.Name)):
            # `query += f" LIMIT {int(limit)}"` — an APPEND to the statement, and
            # `_uncalibrated_ep_participants` (one of the two rows this helper
            # exists to check) builds its statement exactly that way. Without
            # this arm the augmented fragment was never inspected, so
            # `query += " SET n.poked = 1"` produced NO offender: the read shape
            # was still satisfied by the plain assignments above it and the pin
            # stayed green while the statement wrote. Same arm, same reason, as
            # `_bound_names`'s `ast.AugAssign` branch — the census's walker had
            # it, so this helper was the narrower of the two.
            bound.setdefault(node.target.id, []).append(node.value)
        # A comprehension can also be the VALUE of a binding
        # (`_ID_WHITESPACE_SET = ", ".join(_cypher_ws_literal(c) for c in …)`);
        # without this its target `c` is an unreadable name in the statement.
        for sub in ast.walk(node):
            if isinstance(sub, ast.comprehension) and isinstance(sub.target, ast.Name):
                bound.setdefault(sub.target.id, []).append(sub.iter)
    return bound


def _params_of(func) -> set[str]:
    """Every parameter name of `func` — values the assembly cannot read.

    `self`/`cls` are excluded, exactly as `enumerate_handle_surface` does it:
    they are not values that can carry statement text, and counting them would
    make every `self.<verb>(…)` call read as a reach on a handle PARAMETER.
    """
    args = func.args
    names = {arg.arg for arg in (
        list(args.posonlyargs) + list(args.args) + list(args.kwonlyargs))}
    names |= {arg.arg for arg in (args.vararg, args.kwarg) if arg is not None}
    return {name for name in names if name not in ("self", "cls")}


def _mutated_parameters(func, module=None, path=None, *, _visited=None,
                        _depth=0) -> set[str]:
    """Parameters of `func` that it MUTATES in place — an accumulator channel.

    Narrow on purpose. Only the mutating shapes that hand a value back to the
    caller are named (`<param>.append/extend/insert/update`, `<param> += …`,
    `<param>[…] = …`); a bare call to a void helper is not enough, because that
    is ordinary code and reporting it would trade this silence for a wall of
    false FAILs. The set this feeds is REPORTED, never exempted.

    ⛔ TRANSITIVE, and that is the point of `module`/`path` (review P2, measured
    GREEN): the first version read the callee's OWN syntax only, so a callee that
    FORWARDS the parameter to a second callee which mutates it
    (`_add_clause(out)` → `_forward(out)` → `out.append(" SET …")`) reported
    nothing — the shape simply moved one hop and every arm of the row pin stayed
    green. A nested callee's mutated parameter is now folded back onto THIS
    function's parameter through the nested call site's own argument binding
    (positional + keyword, `self`/`cls` dropped), bounded by `visited` for the
    cycles and `_FRAGMENT_DEPTH_CAP` for the depth.
    """
    params = _params_of(func)
    # A callee may hand the parameter to a LOCAL first (`acc = out;
    # acc.append(...)`) — the alias arm (review round 5, measured GREEN). The
    # alias map is closed the same way `_names_feeding` closes its name set, so
    # `a = b = out` and a two-hop local are both covered.
    alias: dict[str, str] = {}
    changed = True
    while changed:
        changed = False
        for node in ast.walk(func):
            if not isinstance(node, ast.Assign):
                continue
            source = node.value
            if not isinstance(source, ast.Name):
                continue
            owner = source.id if source.id in params else alias.get(source.id)
            if owner is None:
                continue
            for target in node.targets:
                if isinstance(target, ast.Name) \
                        and target.id not in params \
                        and alias.get(target.id) != owner:
                    alias[target.id] = owner
                    changed = True

    def owning(name: str) -> str | None:
        return name if name in params else alias.get(name)

    mutated: set[str] = set()
    for node in ast.walk(func):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and isinstance(node.func.value, ast.Name) \
                and node.func.attr in _ACCUMULATOR_METHODS:
            owner = owning(node.func.value.id)
            if owner:
                mutated.add(owner)
        elif isinstance(node, ast.AugAssign) \
                and isinstance(node.target, ast.Name):
            owner = owning(node.target.id)
            if owner:
                mutated.add(owner)
        elif isinstance(node, ast.Assign):
            mutated |= {owner for owner in (
                owning(target.value.id) for target in node.targets
                if isinstance(target, ast.Subscript)
                and isinstance(target.value, ast.Name)) if owner}

    # …and one hop of INDIRECTION: a nested callee's mutated parameter is this
    # function's parameter, when this function passes its own parameter in.
    if module is None or _depth >= _FRAGMENT_DEPTH_CAP:
        return mutated
    visited = set() if _visited is None else _visited
    if id(func) in visited:
        return mutated
    visited.add(id(func))
    owned_names = set(params) | set(alias)
    for node in ast.walk(func):
        if not isinstance(node, ast.Call):
            continue
        nested_name = _callee_name(node)
        if nested_name is None:
            continue
        nested, nested_module, nested_path = _resolve_callee(
            nested_name, module, path)
        if nested is None:
            continue
        nested_mutated = _mutated_parameters(
            nested, nested_module, nested_path,
            _visited=visited, _depth=_depth + 1)
        if not nested_mutated:
            continue
        nested_params = [arg.arg for arg in (
            list(nested.args.posonlyargs) + list(nested.args.args))
            if arg.arg not in ("self", "cls")]
        passed = dict(zip(nested_params, node.args, strict=False))
        passed.update({kw.arg: kw.value for kw in node.keywords
                       if kw.arg is not None})
        for param in sorted(nested_mutated):
            value = passed.get(param)
            if isinstance(value, ast.Name) and value.id in owned_names:
                owner = owning(value.id)
                if owner:
                    mutated.add(owner)
    return mutated


def _mutated_module_bindings(func, module) -> set[str]:
    """MODULE-LEVEL names `func` accumulates into — the global accumulator.

    The same mutation channel one scope out: `_mutated_parameters` names only
    the callee's PARAMETERS, so `PARTS = []` at module scope with a callee doing
    `PARTS.append(" SET …")` was neither reported nor followed (review P2), and
    the statement `"… " + "".join(PARTS)` reads the module literal as empty. The
    intersection with `_module_bindings` is what makes the name a MODULE name
    rather than a local, so a callee's own locals cannot red this.
    """
    if module is None:
        return set()
    names: set[str] = set()
    for node in ast.walk(func):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and isinstance(node.func.value, ast.Name) \
                and node.func.attr in _ACCUMULATOR_METHODS:
            names.add(node.func.value.id)
        elif isinstance(node, ast.AugAssign) \
                and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names & set(_module_bindings(module))


def _names_feeding(expression, func) -> set[str]:
    """Every name whose VALUE can reach `expression`, closed over assignments.

    The statement expression is usually a bare local (`proj.g.query(query)`), so
    the fragment is assembled one statement away: `query = "…" + "".join(parts)`.
    A one-level reading saw only `{query}` and missed `parts` — and `parts` is
    precisely the accumulator argument (measured, review round 4). So the set is
    CLOSED: a name is added when it appears in any expression assigned to (or
    augmented into, or conditionally bound to) a name already in the set.
    """
    names = {node.id for node in ast.walk(expression)
             if isinstance(node, ast.Name)}
    changed = True
    while changed:
        changed = False
        for node in ast.walk(func):
            target: ast.expr | None = None
            value: ast.expr | None = None
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                target, value = node.targets[0], node.value
            elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
                target, value = node.target, node.value
            if target is None or value is None \
                    or not isinstance(target, ast.Name) \
                    or target.id not in names:
                continue
            for inner in ast.walk(value):
                if isinstance(inner, ast.Name) and inner.id not in names:
                    names.add(inner.id)
                    changed = True
    return names


def _callee_name(call: ast.Call) -> str | None:
    """The NAME to resolve for `call` — a bare function, or `self.<method>`.

    Attribute callees were SKIPPED (review P2, measured): `self._fill(parts)`
    resolved nowhere as far as this arm was concerned, so the dominant helper
    shape in `sdk.py` — every `self._stamp_*` / `self._materialize_*` — was
    invisible even though `_fragments_of` follows attribute callees. Only
    `self`/`cls` receivers are named: `<handle>.append(…)` is a COLLECTION
    mutation of the row's own local, not a callee whose parameter is mutated, and
    resolving `append` as a module function would be a category error.
    """
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) \
            and func.value.id in ("self", "cls"):
        return func.attr
    return None


def _accumulator_offenders(func, statement_expr, module, path) -> list[str]:
    """Calls whose callee hands text back through a MUTATED ARGUMENT.

    The expression walk cannot see this shape, and that is the point: the write
    is not inside the statement, it lands in a caller LOCAL that the statement
    then reads — `parts = []; _fill(parts); query = "… " + "".join(parts)`, with
    `_fill` doing `out.append(" SET …")`. `_fragments_of` resolves `parts` to the
    EMPTY list it was bound to, so the one handle reach reads as clean, the call
    resolves (so the unresolvable-name arm stays silent), and nothing is
    reported — measured GREEN (review round 4).

    Reported, not followed: binding what a callee appends into a caller's list is
    a dataflow problem this walk does not solve, and reported-not-skipped is this
    file's own polarity (`unread`). SCOPED to a mutated argument name that can
    reach the row's statement expression — without that, every call passing a
    dict to a helper that updates it would red a correct row.
    """
    feeding = _names_feeding(statement_expr, func)
    offenders: list[str] = []
    for node in ast.walk(func):
        if not isinstance(node, ast.Call):
            continue
        callee_name = _callee_name(node)
        if callee_name is None:
            continue
        callee, _callee_module, _callee_path = _resolve_callee(
            callee_name, module, path)
        if callee is None:
            continue
        mutated = _mutated_parameters(callee, _callee_module, _callee_path)
        # …and the module-scope flavour: a callee that accumulates into a MODULE
        # container the statement reads (`PARTS = []` / `_fill()` /
        # `"".join(PARTS)`) has no parameter to bind, so it is matched by NAME.
        for name in sorted(_mutated_module_bindings(callee, _callee_module)
                           & feeding):
            offenders.append(
                f"{callee_name}(): writes into module-level `{name}`, which "
                "feeds this row's statement — what the callee contributes could "
                "not be read out, so the row cannot be called a read on this walk")
        if not mutated:
            continue
        # `self`/`cls` are not arguments the CALL SITE passes (review P2): without
        # dropping them every positional bound one slot to the left, so a METHOD
        # callee's mutated parameter was never matched.
        params = [arg.arg for arg in
                  list(callee.args.posonlyargs) + list(callee.args.args)
                  if arg.arg not in ("self", "cls")]
        supplied = {param: value
                    for param, value in zip(params, node.args, strict=False)}
        # A KEYWORD argument binds by name whatever the position (review P2):
        # `_fill(out=parts)` was invisible while `_fill(parts)` reported.
        supplied.update({kw.arg: kw.value for kw in node.keywords
                         if kw.arg is not None})
        for param in sorted(mutated):
            value = supplied.get(param)
            if isinstance(value, ast.Name) and value.id in feeding:
                offenders.append(
                    f"{callee_name}(): writes into `{value.id}` (its "
                    f"`{param}` parameter), and `{value.id}` feeds this row's "
                    "statement — what the callee contributes could not be read "
                    "out, so the row cannot be called a read on this walk")
    return offenders


def _statement_argument(call: ast.Call) -> ast.expr | None:
    """The statement text of a handle call — the census's own argument index.

    `args[0]` for every verb except `execute_command`, whose statement sits at
    index 2 (`"GRAPH.QUERY", <graph>, <cypher>`) — the same rule
    `enumerate_handle_surface` applies; see the arm below.

    Keywords are excluded on purpose — `<handle>.query(stmt, params=params)`
    passes BIND DATA, not Cypher, so a statement's parameter dict is not part of
    the statement. Following it let an unrelated `{"reason": "MERGE"}` in that
    dict red a provably read-only row (measured: a false FAIL, which is how a
    check like this gets weakened by the next reader).
    """
    # ⛔ MIRRORS THE CENSUS (`enumerate_handle_surface`: "the statement sits at
    # index 2 … every other verb takes it positionally"). Returning `args[0]`
    # unconditionally handed back the literal `"GRAPH.QUERY"` for
    # `execute_command("GRAPH.QUERY", <graph>, <cypher>)` — the Cypher was
    # never inspected, and the row red only because its read shape was absent,
    # which blames the note for a bug in this helper.
    index = 2 if getattr(call.func, "attr", None) == "execute_command" else 0
    return call.args[index] if len(call.args) > index else None


def _module_bindings(module) -> dict[str, list[ast.expr]]:
    """Module-level bindings, so a fragment pulled from a CONSTANT is followed.

    `where` interpolates module strings (`_ID_WHITESPACE_SET`) and module tuples
    (`_CALIBRATION_GATED_LABELS`); a walk that stopped at function-local names
    left the assembled text visibly incomplete (`MATCH (n: )`) and a write
    hidden in a module constant passed the pin (measured). Cached, because the
    resolver asks per NAME.
    """
    entry = _MODULE_BINDINGS_CACHE.get(id(module))
    if entry is None or entry[0] is not module:
        entry = (module, _local_bindings(
            node for node in module.body
            if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign,
                                 ast.NamedExpr, ast.For))))
        _MODULE_BINDINGS_CACHE[id(module)] = entry
    return entry[1]


#: `id(module AST) -> (the AST itself, its module-level bindings)`.
#: ⛔ THE AST IS RETAINED IN THE ENTRY — that is what makes an identity key
#: safe here. The sdk module is held by the caller, but the IMPORTED-CALLEE hop
#: is not: `_resolve_callee` does `ast.parse(candidate.read_text())`, `_fragments_of`
#: uses it for the duration of the recursion, and then it is unreachable while
#: its `id` stays a live key — a later `ast.parse` can be allocated at the same
#: address and this cache would hand back ANOTHER module's constants. Wrong
#: fragments can resolve a write AWAY (the fail-open direction this pin exists
#: to prevent). It does not bite today (only `tortoise/live.py` is reached that
#: way and every re-parse yields identical bindings), so this is a trap defused
#: rather than a live failure — but the retention, not the comment, is what
#: holds it. `test_the_module_bindings_cache_retains_its_module` pins that.
_MODULE_BINDINGS_CACHE: dict[int, tuple[object, dict[str, list[ast.expr]]]] = {}


def _function_returns(func) -> list[ast.expr]:
    """Every expression a function RETURNS — the values its callers receive.

    YIELDS count too (review, measured): a generator has no `Return`, so a
    resolved callee that yields its text contributed NO fragments and nothing to
    `unread` — `query = "MATCH … " + "".join(_parts())` where `_parts()` yields
    `" SET n.poked = 1"` passed GREEN in both the same-module and the
    package-`__init__` hop, which is the P1 class this branch closed one branch
    over. `yield`/`yield from` are exactly as reachable as `return` here: both
    hand the caller a string it may splice into a statement.
    """
    return [node.value for node in ast.walk(func)
            if isinstance(node, (ast.Return, ast.Yield, ast.YieldFrom))
            and node.value is not None]


def _resolve_callee(name: str, module, path: Path):
    """`(FunctionDef, its module, its path)` for a NAME, here or by import.

    Two hops, both needed: `_cypher_ws_literal` / `_strip_whitespace` are defined
    in `sdk.py` itself, while `_live_only` arrives through
    `from .live import _live_only`. Following only same-module definitions left
    the imported predicate unread, so a `SET` spliced into
    `_strip_whitespace`'s returned f-string passed the pin (measured).
    """
    if module is None:
        return None, module, path
    defs = [node for node in ast.walk(module)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == name]
    if defs:
        return defs[0], module, path
    for node in ast.walk(module):
        if not isinstance(node, ast.ImportFrom):
            continue
        if not any(alias.name == name for alias in node.names):
            continue
        module_name = node.module or ""
        if node.level:
            package = path.parent.relative_to(ROOT).as_posix().replace("/", ".")
            module_name = ".".join(filter(None, [package, module_name]))
        # ⛔ PACKAGE FIRST — Python's own resolution order, and it is
        # load-bearing here: `tortoise/projection.py` exists as a 37-line shim,
        # so a module-first resolver parsed the SHIM, found no def, and never
        # reached the package `__init__.py` where `_stored_prop_value` actually
        # lives. (Verified: `import tortoise.projection` resolves to the
        # package, not the shim.)
        package = ROOT / (module_name.replace(".", "/")) / "__init__.py"
        candidate = package if package.exists() else ROOT / (
            module_name.replace(".", "/") + ".py")
        if not candidate.exists():
            continue
        other = ast.parse(candidate.read_text(), str(candidate))
        found = [node for node in ast.walk(other)
                 if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                 and node.name == name]
        if found:
            return found[0], other, candidate
    return None, module, path


#: How many indirection hops the fragment walk follows before it declares the
#: statement unreadable. A BACKSTOP, not the mechanism that keeps the walk
#: finite — `visited` does that — so it sits ABOVE the deepest chain on the
#: real tree (measured 11 for `_uncalibrated_ep_participants`, i.e. ~5 hops of
#: headroom) and, when it is reached, it REPORTS the blind spot instead of
#: returning in silence.
_FRAGMENT_DEPTH_CAP = 16

#: `dir(builtins)`, hoisted: it was rebuilt every time an UNBOUND name was
#: walked, which is exactly the arm whose cost grows with the statement.
_BUILTIN_NAMES = frozenset(dir(builtins))

#: The accumulation verbs a callee can use to hand text back through a parameter
#: instead of returning it (see `_mutated_parameters`). `append`/`extend`/`insert`
#: on a list, `update` on a dict, plus `+=`/`<param>[k] = …` handled separately.
_ACCUMULATOR_METHODS = frozenset({"append", "extend", "insert", "update"})

#: Builtins whose RESULT cannot carry statement text, so a parameter handed to
#: one genuinely IS a scalar. `str` is deliberately NOT here: it returns its
#: argument verbatim, so `f"…{str(extra)}"` would smuggle a caller's Cypher past
#: the pin (measured GREEN while the bare `{extra}` form was RED). Anything not
#: on this list — `str`, `list`, an SDK helper — leaves the argument reported.
_SCALAR_COERCIONS = frozenset(
    {"int", "float", "bool", "len", "abs", "round", "hash"})


#: Literal containers: a value whose ENTIRE content is a constant expression.
_LITERAL_CONTAINERS = (ast.Dict, ast.List, ast.Tuple, ast.Set)


def _is_literal_value(node) -> bool:
    """True when everything `node` can evaluate to is a CONSTANT.

    Read by the non-NAME callee arm to decide the one benign case: a method
    called ON a literal (`", ".join(...)`) or on a name whose every binding is
    a literal container (`_CYPHER_CHAR_LITERAL.get(ch)` — a dict literal) can
    only return text this walk has ALREADY collected, so it carries no blind
    spot. A call on anything else — `b = _make(); b.suffix()` — returns text
    from a body this walk never read, and is REPORTED.
    """
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, _LITERAL_CONTAINERS):
        children = list(node.values if isinstance(node, ast.Dict) else node.elts)
        if isinstance(node, ast.Dict):
            children += [key for key in node.keys if key is not None]
        return all(_is_literal_value(child) for child in children)
    return False


def _non_name_callee_is_benign(callee, bindings, params, module) -> bool:
    """Benign cases for a call whose `func` is not a NAME.

    Three, all measured — a blanket report reds the real pin:

    * the receiver is a LITERAL, or a name whose every binding is a literal
      container: `", ".join(_CALIBRATION_EVIDENCE_KINDS)` and
      `_CYPHER_CHAR_LITERAL.get(ch)` are the two shapes the two pinned rows
      assemble their statements with, and each value they return is drawn from
      text this walk already read;
    * the receiver is a PARAMETER: the `params` arm owns that decision (a
      followed callee's parameter was walked at the call site; otherwise the
      parameter is already reported) — `_live_only` does exactly
      `clause.split(" AND ")` on the real tree;
    * the callee is itself an ATTRIBUTE of a parameter (`self._suffix()`), where
      the receiver NAME is already reported by the bare-name arm.
    """
    if not isinstance(callee, ast.Attribute):
        return False  # Subscript / Lambda / … — nothing benign is known
    receiver = callee.value
    if isinstance(receiver, ast.Name) and receiver.id in params:
        return True
    if _is_literal_value(receiver):
        return True
    if not isinstance(receiver, ast.Name):
        return False
    values = list(bindings.get(receiver.id, ()))
    if module is not None:
        values += list(_module_bindings(module).get(receiver.id, ()))
    return bool(values) and all(_is_literal_value(value) for value in values)


def _fragments_of(expr, bindings, params, module, path, followed, unread,
                  depth: int = 0, visited=None,
                  params_are_inspected: bool = False) -> list[str]:
    """String fragments that can flow out of `expr`, following indirection.

    `bindings`/`params` belong to the ENCLOSING scope; a callee is followed with
    ITS OWN, so `_strip_whitespace`'s local `out` and its `ch` parameter are not
    mistaken for holes in the statement.

    Anything that cannot be resolved is added to `unread` rather than skipped —
    the census's own polarity (a fragment that cannot be shown read-only is not
    called read-only), so a blind spot cannot be inherited silently. A callee
    need not be a NAME: an `Attribute`/`Subscript`/`Lambda` callee cannot be
    resolved at all, so it is reported too (`_non_name_callee_is_benign` names
    the three benign shapes). Parameters are the exception: they carry bind data
    and integers — but only when their
    VALUE was already walked, which is true for a FOLLOWED CALLEE (its argument
    was inspected in the caller's expression) and NOT true for the top-level
    function: there the parameter is a value this walk never sees, so a directly
    interpolated one (`f"…{extra}"`) is reported, while a parameter genuinely
    used as a scalar argument (`int(limit)`, `len(ids)`) stays benign. A BUILTIN
    name is benign only when this walk holds the module it came from and the module does
    not bind that name (`format`, `set`, `next` are ordinary variable names) —
    with no module AST there is nothing to check it against, so it is reported.

    Two guards, and their POLARITY is the point:

    * `visited` — `(expression, the scope it was resolved in)` pairs already
      inspected. Without it the walk re-expanded the same subgraph on every
      path: the real `_uncalibrated_ep_participants` walk collected 275 fragments
      of which 230 were repeats of a literal it had already seen, where the
      guard collects 77 — and, because it is no longer the thing keeping the
      walk finite, 10 fragments that the old ceiling cut are now inspected.
      ⛔ BOTH id-keyed containers RETAIN the node they key on (`visited` the
      expression, `followed` the callee). `_resolve_callee` re-parses its target
      on every resolution, so a bare `set[int]` keyed on a node from a throwaway
      module could have its `id` recycled: the guard then skipped a node it had
      never walked — neither followed nor reported, the silent blind spot this
      docstring forbids. `test_the_fragment_walk_retains_the_nodes_it_keys_on`
      pins that.
    * `_FRAGMENT_DEPTH_CAP` — a backstop for a binding CYCLE, and it REPORTS.
      It used to `return fragments` in silence: past six hops a write held in a
      variable was neither inspected nor added to `unread`, so the pin stayed
      green over a writing row (`_uncalibrated_ep_participants` already assembles
      its statement at depth 7-11 — one hop from live code, not a distant
      ceiling). A cap may stop the walk; it may never call the fragment read.
    """
    if visited is None:
        visited = {}
    fragments = _string_constants(expr)
    if depth > _FRAGMENT_DEPTH_CAP:
        unread.add(
            f"<depth cap {_FRAGMENT_DEPTH_CAP} reached at "
            f"{ast.dump(expr)[:60]}>")
        return fragments
    seen = visited.setdefault(id(expr), [])
    if any(scope is bindings for _retained, scope in seen):
        return []  # already inspected in this exact scope; nothing new to add
    # ⛔ THE EXPRESSION IS RETAINED WITH THE SCOPE (review P2). Appending only
    # `bindings` pinned the SCOPE dict, never the key: `expr` (a parse-tree node
    # from `_resolve_callee`'s throwaway module) could be freed and its `id`
    # recycled, which made the guard above fire for a DIFFERENT expression — and
    # the walk then returned `[]`, neither inspected nor reported.
    seen.append((expr, bindings))
    call_names = {id(node.func) for node in ast.walk(expr)
                  if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
    # A parameter passed to an UNshadowed scalar coercion cannot carry statement
    # text; one interpolated straight into the text, or passed to `str(...)`
    # (which returns it verbatim), can. The shadowing checks MIRROR the bare-name
    # arm below — `int = str` makes `int(extra)` a verbatim pass-through, and
    # exempting it re-opened the `str(extra)` hole one binding away (measured
    # GREEN). Computed over the whole expression, so the two states cannot be
    # confused by walk order.
    scalar_arg_ids = {
        id(arg) for node in ast.walk(expr)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in _SCALAR_COERCIONS
        and node.func.id not in bindings
        and (module is None
             or node.func.id not in _module_bindings(module))
        for arg in node.args}
    for sub in ast.walk(expr):
        if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name):
            callee, callee_module, callee_path = _resolve_callee(
                sub.func.id, module, path)
            if callee is not None and id(callee) not in followed:
                # ⛔ KEYED BY id, RETAINING THE CALLEE — the same shape as
                # `_MODULE_BINDINGS_CACHE`. A bare `set[int]` kept no reference
                # to `callee`, and `_resolve_callee` re-parses its target on
                # every resolution, so a later `ast.parse` could land on the
                # freed address: the guard then skipped a callee it had never
                # followed — neither followed nor reported, the silent blind
                # spot this walk's docstring forbids.
                followed[id(callee)] = callee
                callee_bindings = _local_bindings(ast.walk(callee))
                for returned in _function_returns(callee):
                    fragments.extend(_fragments_of(
                        returned, callee_bindings, _params_of(callee),
                        callee_module, callee_path, followed, unread, depth + 1,
                        visited,
                        params_are_inspected=bool(sub.args or sub.keywords)))
                # A DEFAULT is a value the call site need not supply, so it is
                # walked here rather than blessed with the arguments: a write
                # sitting in `def _suffix(extra=" SET …")` is otherwise never
                # read (measured GREEN before this loop existed).
                for default in list(callee.args.defaults) \
                        + list(callee.args.kw_defaults):
                    if default is None:
                        continue
                    fragments.extend(_fragments_of(
                        default, callee_bindings, _params_of(callee),
                        callee_module, callee_path, followed, unread, depth + 1,
                        visited, params_are_inspected=True))
            elif callee is None and (
                    sub.func.id not in _BUILTIN_NAMES
                    or sub.func.id in bindings
                    or (module is not None
                        and sub.func.id in _module_bindings(module))):
                # ⛔ REPORTED, never skipped — this is the polarity the
                # docstring above states, and the asymmetry that made it a
                # hole: an unknown NAME reds the pin below
                # (`else: unread.add(sub.id)`), an unknown CALL did not, so a
                # write introduced in a helper's return was invisible AND
                # unannounced. A builtin is the one benign case (`int`, `len`)
                # — and only when NOTHING HERE BINDS IT, the same two-table
                # test the bare-name and scalar-coercion arms make:
                # `next = _parts` / `format = _suffix_builder` makes the name
                # a user callable, `_resolve_callee` does not follow an alias,
                # and the builtin exemption then blessed a call whose body this
                # walk never read (measured GREEN over a write in `_parts`).
                unread.add(f"{sub.func.id}()")
            continue
        if isinstance(sub, ast.Call):
            # A callee that is NOT a NAME: `Attribute` (`b.suffix()`),
            # `Subscript` (`handlers[i]()`), `Lambda`. The NAME arm above cannot
            # resolve it and the bare-name branch below skips it, so before this
            # arm it contributed NEITHER a fragment NOR an `unread` entry — the
            # defect closed for NAME callees at `aec5d1e0e`, left one branch
            # over (measured GREEN: `b = _make(); query += f"{b.suffix()}"`).
            # Not rescued by the receiver: when the receiver RESOLVES nothing
            # else reports it.
            if not _non_name_callee_is_benign(
                    sub.func, bindings, params, module):
                unread.add(f"{ast.unparse(sub.func)}()")
            continue
        if not isinstance(sub, ast.Name) or id(sub) in call_names:
            # A callee NAME is handled above; naming it again here would report
            # the function itself as an unread fragment.
            continue
        if sub.id in bindings:
            for value in bindings[sub.id]:
                fragments.extend(_fragments_of(
                    value, bindings, params, module, path, followed, unread,
                    depth + 1, visited,
                    params_are_inspected=params_are_inspected))
            if sub.id in params and not params_are_inspected \
                    and id(sub) not in scalar_arg_ids:
                # A LOCAL binding of a PARAMETER name must not HIDE it: this
                # branch is tested before the `params` arm, so a rebind (a `for`
                # target, a comprehension variable, a nested helper's
                # assignment — `_local_bindings` is a flat union) resolved the
                # name to its binding and the `params` arm was never reached,
                # so a caller-supplied fragment was neither followed nor
                # reported (measured GREEN: `f"…{extra}"` with
                # `for extra in []:`). Reported as well, which is the same
                # over-report direction `_local_bindings` already documents.
                unread.add(sub.id)
        elif module is not None and sub.id in _module_bindings(module):
            for value in _module_bindings(module)[sub.id]:
                fragments.extend(_fragments_of(
                    value, bindings, params, module, path, followed, unread,
                    depth + 1, visited,
                    params_are_inspected=params_are_inspected))
        elif sub.id in params:
            # Benign when the value was already walked (a followed callee) or the
            # name is an ARGUMENT to a call (`int(limit)`); otherwise the text is
            # assembled from a value this walk never saw, and `unread` says so
            # rather than blessing it — the census's own polarity.
            if params_are_inspected or id(sub) in scalar_arg_ids:
                continue
            unread.add(sub.id)
        elif (sub.id in _BUILTIN_NAMES and module is not None
                and sub.id not in _module_bindings(module)):
            # The builtin ITSELF, and that is a claim this walk can only make
            # when it holds the module: a name that SHADOWS a builtin is a
            # binding, and the two branches above already followed it (local
            # first, then module) — so the ORDER is what resolves a shadow, and
            # `format = " SET …"` at module level is inspected rather than
            # skipped as a scalar. With no module AST there is nothing to check
            # the name against, so it falls through to `unread`.
            continue
        else:
            unread.add(sub.id)
    return fragments


def _assembled_statement_fragments(func, call: ast.Call,
                                   module=None, path: Path = SDK_PY
                                   ) -> tuple[list[str], set[str]]:
    """String fragments reaching `call`'s statement, and the names NOT read.

    Follows the indirection transitively — `query` -> f-string -> `where` ->
    f-string -> module constant -> same-module or imported helper's returns —
    because that indirection is exactly why the census could not resolve these
    statements; a check that stopped at the literals in the call would pass on
    the readable half and prove nothing about the whole.

    `module` is the AST the function came from (the real pin passes `sdk.py`); a
    synthetic non-vacuity case may pass `None`, which disables only the
    module-constant hop.
    """
    statement = _statement_argument(call)
    if statement is None:
        return [], set()
    unread: set[str] = set()
    fragments = _fragments_of(statement, _local_bindings(ast.walk(func)),
                              _params_of(func), module, path, {}, unread)
    return fragments, unread


def _handle_aliases(func) -> set[str]:
    """Every local name in `func` bound to the write handle.

    ALIAS-AWARE, like `enumerate_handle_surface`: the census resolves
    `h = proj.g; h.query(...)`, so a receiver-name-only check would let a SECOND
    reach hide behind the "found 1 call" assertion in
    `test_the_calibration_readers_assemble_no_write_clause` — the assertion that
    is the whole point of the pin would be the thing that misses it.

    ONE cumulative pass over the function, so an alias counts for calls on
    EITHER side of its binding. That is the over-report direction, deliberately:
    a call is counted as a handle reach even when it precedes the binding, which
    can only make the "found 1 call" assertion stricter, never looser. (An
    earlier version of this docstring claimed positional scoping and sorted the
    walk by `lineno` to implement it; the sort was inert, because the alias set
    was complete before any call was tested. Stated as it behaves.)
    """
    aliases: set[str] = set()
    params = _params_of(func)
    for node in ast.walk(func):
        _bind_handle_aliases(node, aliases, params)
    return aliases


def _handle_query_calls(func) -> list[ast.Call]:
    """Every `<handle>.<verb>(...)`-shaped call in `func` — its handle reach.

    Alias-aware AND handle-parameter aware. `_is_handle_ref` counts a function
    PARAMETER as the handle — the census's documented over-report, because a
    handle RECEIVED AS AN ARGUMENT has no binding to follow and that is how a
    real writer hides (`clear_config_reset(proj.g)` → `clear_config_reset(g)`)
    — so `params` is threaded through here exactly as `enumerate_handle_surface`
    threads it (its `ast.Call` branch passes `fact["params"]` into
    `_is_handle_ref`). ⛔ cited by SYMBOL, not by line number: the first version
    of this sentence said `(line ~700)`, which is the citation-rot class this
    PR exists to remove and was already ~19 lines stale at the head (review
    MINOR). Without the `params` arm, a second reach through a handle
    parameter left the "found 1 call" assertion green over two statements.
    """
    aliases = _handle_aliases(func)
    params = _params_of(func)
    return [node for node in ast.walk(func)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in _HANDLE_MUTATING_VERBS
            and _is_handle_ref(node.func.value, aliases, params)]


def test_the_line_anchor_matches_every_spelling():
    """Non-vacuity for the widened anchor (review, measured).

    The pin in `test_the_unresolved_hand_check_cites_symbols_not_line_numbers`
    is only as wide as `_SDK_LINE_ANCHOR`. The first version matched
    `sdk.py:<digits>` and NOTHING else, so five ordinary spellings of the same
    citation passed — the pin forbade a style it admitted. Each variant below is
    a way a human writes the anchor; every one must red.

    The negative cases matter just as much: `tortoise/sdk.py` (a path, no line),
    `sdk.py` alone, and the note's own self-description `sdk.py:<line>` (not a
    digit) must NOT match, or the pin would red on a correct note.
    """
    must_match = [
        "sdk.py:23508",
        "sdk.py: 23508",
        "sdk.py:L23508",
        "sdk.py line 23508",
        "sdk.py, 23508",
        "sdk.py 23508",
        # Second spelling family (review round 2, measured): `\sline\s`
        # required the word `line` to be WHITESPACE-preceded, so a separator or
        # a word in front of it reopened the same hole one family over.
        "sdk.py, line 23508",
        "sdk.py (line 23508)",
        "sdk.py at line 23508",
        "sdk.py::23508",
        # Third spelling family (review round 3, measured): the note writes
        # `tortoise/sdk.py` in BACKTICKS throughout, and `[\s,:]*` cannot
        # consume a closing backtick — so the spelling this file is most likely
        # to grow next was the one that passed. `#?` also admits `line #23508`.
        "`sdk.py`:23508",
        "`sdk.py` line 23508",
        "`sdk.py` 23508",
        "`tortoise/sdk.py`:23508",
        "sdk.py line #23508",
        "sdk.py@23508",
        # Fourth spelling family (review round 4, measured): `#` was admitted
        # only INSIDE the `line` alternative, so the GitHub-anchor shape — which
        # the `[Ll]?` in this pattern exists for — missed entirely.
        "sdk.py#L23508",
        "sdk.py #L23508",
        "[sdk.py](../tortoise/sdk.py#L23508)",
        "sdk.py#23508",
        # Fifth spelling family (review round 5, measured): `\(?` only fired when
        # `line` followed, so the parenthesised bare line and the bare `at <n>`
        # form passed — the two a human writes next to a path.
        "sdk.py (23508)",
        "sdk.py (L23508)",
        "sdk.py at 23508",
        "tortoise/sdk.py (23508)",
        "SDK.PY:23508",
        # Sixth spelling family (review, measured): the TILDE. This branch's own
        # narrative writes `line ~511` / `(line ~700)`, so the idiom is not
        # hypothetical — and it was the one shape the separator class missed.
        "sdk.py:~23508",
        "sdk.py (line ~23508)",
        "`sdk.py` ~23508",
        "sdk.py~23508",
        # Seventh family (round 6, measured): the dash (this file's own prose
        # separator) and `near` — the class is an enumeration, so both were out.
        "sdk.py — 23508",
        "sdk.py – 23508",
        "sdk.py - 23508",
        "sdk.py near line 23508",
        "sdk.py near 23508",
    ]
    for variant in must_match:
        assert _SDK_LINE_ANCHOR.search(variant), (
            f"the line anchor misses {variant!r} — that spelling of a sdk.py "
            "citation passes the pin while rotting exactly the same way")

    must_not_match = [
        "`tortoise/sdk.py` function `calibrate_summary`",
        "the sdk.py module",
        "CITED BY SYMBOL, NEVER BY `sdk.py:<line>`",
        "sdk.py is ~37k lines",
        # ⛔ …and the MAGNITUDE statement WITHOUT the word "is" (review,
        # measured): admitting `~` made the tilde spelling match, so the control
        # above passed only because of the letters in "is". Both are length
        # statements, not citations, and both must stay green.
        "sdk.py ~37k lines",
        "sdk.py ~ 37k lines",
    ]
    for variant in must_not_match:
        assert not _SDK_LINE_ANCHOR.search(variant), (
            f"the line anchor FALSE-POSITIVES on {variant!r} — a correct note "
            "must not red")


def test_the_unresolved_hand_check_cites_symbols_not_line_numbers():
    """The #9063 note must cite the two SYMBOLS, never a line number.

    NON-VACUITY: the note as first committed carried `(sdk.py:23143)` and
    `(sdk.py:23531)`, so a `sdk.py:<n>` anchor in the `_UNRESOLVED` block reds
    this test. The positive half is what makes the note verifiable at all: both
    function names must appear IN THE COMMENT TEXT, because the symbol is the
    only anchor that survives `sdk.py` moving under it.

    ⛔ The positive half reads COMMENTS, not the block's raw text: the two rows
    are themselves string elements (`"calibrate_summary",`), so a plain
    `name in block` was satisfied by the inventory the note is ABOUT — deleting
    the whole note kept it green. `_comments_in` is what makes deleting the note
    red; `test_the_symbol_pin_reads_the_comment_not_the_rows` holds that line.
    """
    block = _unresolved_block_source()
    anchors = _SDK_LINE_ANCHOR.findall(block)
    assert not anchors, (
        "the `_UNRESOLVED` hand-check note cites sdk.py by LINE NUMBER "
        f"({sorted(set(anchors))}) — sdk.py moves under it, so the anchor rots "
        "into a pointer at whatever function happens to hold that line now "
        "(measured on this note: 268 lines off, landing in a writer). Cite the "
        "function by NAME — the symbol is authoritative, the number is not:"
        f"\n  {block}")
    note = _comments_in(block)
    missing = _missing_citations(block)
    assert not missing, (
        "the note no longer cites the function(s) it is the hand-check FOR, "
        f"with the read shape it claims ({missing}) — the citation must be ONE "
        "comment line naming the function and its shape. Both halves matter: "
        "the names are also set rows and are mentioned again in the dedupe "
        "note, so a check that accepted any mention anywhere stayed green with "
        "the citation replaced by prose (measured: \"the calibration reader — "
        "`MATCH … OPTIONAL MATCH … RETURN`\"). Without the citation the note "
        "anchors at nothing and a reader cannot re-verify it, which is exactly "
        f"how it rotted before. Comments seen:\n  {note}")


def test_the_symbol_pin_reads_the_comment_not_the_rows():
    """NON-VACUITY for the symbol pin: the ROWS alone must not satisfy it.

    The exact defect the P2 review found: `_unresolved_block_source()` returns
    the whole set literal, whose elements ARE the two names, so the pin was
    green with the entire `#9063` note deleted. This is that state, built by
    hand: two declared rows, no comment. Under the fix `_comments_in` sees no
    names and the pin would red; under the old `name in block` both names are
    "present" and it would stay green.

    ⛔ It drives `_missing_citations` — the PIN'S OWN PREDICATE, the function
    the pin calls — not a copy of it (review P2). The earlier version carried a
    private `_missing()` (twice, the first definition dead), so the test could
    not observe the pin at all and stayed green with the pin's positive half
    reverted to `name in block`. Now reverting `_missing_citations`'s
    `_comments_in(...)` call to the raw block reds this test.
    """
    rows_only = (
        '_UNRESOLVED = {\n'
        '    "calibrate_summary",\n'
        '    "_uncalibrated_ep_participants",\n'
        '}\n')
    note = _comments_in(rows_only)
    assert note == "", f"the block has no comments, yet {note!r} was read"
    # The names ARE in the raw block, on the set rows — so the assertions below
    # discriminate on WHERE the pin reads, not on whether the text exists at
    # all. A predicate that skimmed the raw block (`name in block`, the pre-fix
    # shape) would find both and stay green over a note-less block.
    assert all(name in rows_only for name, _shape in _CALIBRATION_READERS), (
        "this case is only meaningful while the block's rows carry both names")
    # ⛔ THE PIN'S OWN PREDICATE (`_missing_citations`, the function the pin
    # calls), not `name not in note`: after the line above established
    # `note == ""` that test is `True` unconditionally, so it could not fail —
    # and it held identically with the positive half reverted. Spending the
    # predicate on a comment-free block leaves both names MISSING, which is what
    # makes deleting the note red.
    assert _missing_citations(rows_only) == [
        name for name, _shape in _CALIBRATION_READERS], (
        "the symbol pin would stay green with the #9063 note deleted — its "
        "positive half is reading the block's SET ROWS, not the comments")
    # …and a name+shape pair sitting in a ROW (a string element, not a comment)
    # must NOT satisfy it: that is the difference between reading COMMENTS and
    # reading the block's raw text, which `_comments_in` exists to make.
    shapes_in_rows = (
        '_UNRESOLVED = {\n'
        '    "calibrate_summary OPTIONAL MATCH",\n'
        '    "_uncalibrated_ep_participants RETURN n.id",\n'
        '}\n')
    assert _missing_citations(shapes_in_rows) == [
        name for name, _shape in _CALIBRATION_READERS], (
        "a name+shape pair in a SET ROW satisfied the pin — its positive half "
        "is reading the block's raw text, not its comments")
    # …and the note, once present in the shape the pin requires (ONE comment
    # line carrying the name AND its read shape), satisfies it.
    with_note = rows_only.replace(
        "_UNRESOLVED = {",
        "#   `calibrate_summary` — `MATCH … OPTIONAL MATCH … RETURN` (a read)\n"
        "#   `_uncalibrated_ep_participants` — `MATCH … RETURN n.id` (a read)\n"
        "_UNRESOLVED = {")
    assert _missing_citations(with_note) == [], (
        "the pin does not even accept a note written in its own required "
        f"shape, so the assertion above proves nothing (comments: "
        f"{_comments_in(with_note)!r})")


def test_a_second_unresolved_assignment_reds_the_pin():
    """NON-VACUITY for the ONE-assignment guard in `_unresolved_block_span`.

    The span used to `return` on the first module-level `_UNRESOLVED = {…}`, so
    a SECOND assignment — which REBINDS the name, and is therefore the block
    that governs at runtime — left both pins validating the DEAD first literal,
    silently. Pre-fix this case returns the first span and the assertion below
    never fires; with the fix it reds.
    """
    one = (
        '_UNRESOLVED = {\n'
        '    "a",\n'
        '}\n')
    assert "a" in _unresolved_block_span(one), (
        "the span helper does not even find a single assignment")
    with pytest.raises(AssertionError, match="assigned 2 times"):
        _unresolved_block_span(one + "\n_UNRESOLVED = {\n    'b',\n}\n")
    # A rebinding that the OLD loop would have skipped rather than reded.
    with pytest.raises(AssertionError, match="assigned 0 times"):
        _unresolved_block_span("_UNRESOLVED: set[str] = {'a'}\n")


#: The module `_census_writer_names` censuses, as the census spells it (the same
#: `relative_to(ROOT).as_posix()` form `_modules_reaching_the_handle()` emits).
SDK_REL = "tortoise/sdk.py"


def _proven_read_names(rel: str = SDK_REL, register=None) -> frozenset[str]:
    """Bare names `register` proves read-only **for `rel`**, and only that module.

    ⛔ THE MODULE KEY IS THE WHOLE POINT (review P2, latent fail-open).
    `_PROVEN_READS` is keyed `(rel_path, scope-qualified name)` across the whole
    package, while the writer set it is subtracted from is `sdk.py`'s and the
    consumer matches BARE callee names. Subtracting without the `rel` check
    resolves the identity of the *removed* function by bare name across every
    module the register names, so a proven read added for, say,
    `tortoise/event_store.py` would silently un-guard an `sdk.py` writer that
    merely shares the bare name — and the arm would then report nothing, with
    its note reading true for the wrong reason. That is the SAME bare-name
    collision class `test_no_two_census_entries_share_a_bare_function_name`
    already gates for inventories. `test_a_proven_read_is_subtracted_only_for_
    its_own_module` reds if this filter is dropped.
    """
    register = _PROVEN_READS if register is None else register
    return frozenset(_bare_name(key) for mod, key in register if mod == rel)


def _sdk_surface() -> dict:
    """`enumerate_handle_surface` over `sdk.py`, parsed once per process.

    Cached separately from the writer set: the writer set is a DERIVED view
    (`surface − proven reads`), so the non-vacuity tests need the raw surface to
    show the subtraction is load-bearing rather than tautological.
    """
    cached = _SDK_SURFACE_CACHE.get("surface")
    if cached is None:
        cached = enumerate_handle_surface(SDK_PY.read_text(), str(SDK_PY))
        _SDK_SURFACE_CACHE["surface"] = cached
    return cached


def _census_writer_names(surface: dict | None = None,
                         register=None) -> frozenset[str]:
    """Bare names the census either PROVES reaches a write or CANNOT prove a read.

    `enumerate_handle_surface` keeps a fact for every function holding at least
    one mutating `<handle>.<verb>` call, so that surface IS the census's own
    answer to "does this name write" — using it here keeps this pin from
    introducing a second, narrower notion of "write" (#3595's shared-decision
    precedent, which this file's module docstring states). Keys are
    scope-qualified (`TortoiseSDK.set_point_baseline`) and are resolved with
    `_bare_name`, the SAME resolver `_inventory_of` uses — it strips the
    `@<lineno>` suffix too, so a same-scope redefinition cannot make a writer
    name unmatchable by accident.

    ⛔ THE POLARITY IS "WRITE **OR** NOT-PROVEN-READ", NOT "WRITE" (review,
    measured). A surface entry whose every mutating call is `<unresolved>` is a
    function the census could not settle — it is on the surface for the same
    reason `_UNRESOLVED` exists, and 45 of 83 entries here are that shape. The
    message this set drives therefore may not say "the census PROVES reaches the
    write handle": for that majority the census proves nothing at all.

    What IS subtracted is the file's own positive verdict: a name registered in
    `_PROVEN_READS` has been hand-checked AND mechanically pinned as an
    unresolved-but-read row (`test_every_proven_read_names_a_real_census_entry`
    checks both its census verdict and the exact statement kinds), so calling it
    from another row may not red on the census's silence.

    The remaining `_UNRESOLVED`-declared names still red — deliberately. Their
    hand-check covers THEIR OWN statement, not a call from someone else's row,
    and the over-report direction is the one this file prefers. What was wrong
    was the reason, not the direction.

    ⛔ the subtraction is `_proven_read_names(SDK_REL)` — `sdk.py`'s proven reads
    ONLY, because this set is matched against bare callees in `sdk.py`.

    `surface` / `register` are the non-vacuity seam: supplying either bypasses
    the cache so a test can hand in a synthetic surface and a synthetic register
    and observe the module filter. The real pin passes neither.
    """
    if surface is not None or register is not None:
        surface = _sdk_surface() if surface is None else surface
        return frozenset(_bare_name(key) for key in surface) \
            - _proven_read_names(SDK_REL, register)
    cached = _CENSUS_WRITER_NAMES.get("names")
    if cached is None:
        cached = _census_writer_names(surface=_sdk_surface())
        _CENSUS_WRITER_NAMES["names"] = cached
    return cached


#: Filled on first use: parsing `sdk.py` is ~0.25 s and every
#: `_calibration_read_offenders` call would otherwise pay it again. Kept apart
#: from the derived writer set so the raw surface stays observable.
_SDK_SURFACE_CACHE: dict[str, dict] = {}

#: The derived view (`surface − sdk.py's proven reads`), filled on first use.
_CENSUS_WRITER_NAMES: dict[str, frozenset[str]] = {}


def _calibration_read_offenders(functions, readers=_CALIBRATION_READERS,
                                module=None, path: Path = SDK_PY,
                                writers: frozenset[str] | None = None
                                ) -> list[str]:
    """The per-row checks of the note, over `functions` (name -> def node).

    Extracted so the synthetic non-vacuity tests can run the SAME code on a
    hand-built function instead of asserting against a copy of it.

    `writers` is `_census_writer_names()` — "write **OR** not-proven-read", NOT
    "the names the census proves a write": 45 of its 83 entries are functions
    the census could not settle, so the offender message this set drives may not
    claim the census PROVES a write (see that function's docstring for the full
    polarity, and `test_the_census_writer_set_states_its_own_polarity` for the
    pin). `None` means "ask the census", which is what the real pin does.
    """
    writers = _census_writer_names() if writers is None else writers
    offenders: list[str] = []
    for name, read_shape in readers:
        func = functions.get(name)
        if func is None:
            offenders.append(f"{name}: no such function in tortoise/sdk.py")
            continue
        calls = _handle_query_calls(func)
        if len(calls) != 1:
            offenders.append(
                f"{name}: the note says ONE handle reach, found {len(calls)} "
                "`<handle>.query(...)` call(s) — re-check the row before "
                "trusting either")
            continue
        fragments, unread = _assembled_statement_fragments(
            func, calls[0], module, path)
        if unread:
            offenders.append(
                f"{name}: {sorted(unread)} could not be read out of the "
                "statement at all, so this pin cannot say the row is a read — "
                "resolve them, or extend the resolver and say why (a "
                "`<depth cap …>` entry means the walk gave up on a binding "
                "cycle or a chain longer than `_FRAGMENT_DEPTH_CAP`: raise it "
                "and re-check, never delete the row on this message alone)")
        assembled = " ".join(fragments)
        write = _assembled_write_evidence(assembled)
        if write:
            offenders.append(
                f"{name}: the assembled statement carries {write} — "
                "this row is not a read any more")
        statement_expr = _statement_argument(calls[0])
        if statement_expr is not None:
            offenders.extend(_accumulator_offenders(
                func, statement_expr, module, path))
        if read_shape not in assembled:
            offenders.append(
                f"{name}: the assembled statement no longer contains "
                f"{read_shape!r}, so the note's stated read shape is stale")
        # The note's second claim: no write through ANOTHER SDK method. Pinned
        # against the census's writer list rather than one method name — a
        # `set_point_baseline`-only check left `self.invalidate_point(…)` (or
        # `record_calibration`, or `_touch_inherited_at`) invisible while the
        # note went false. The name may appear in advice STRINGS (it does), so
        # this pins CALLS. The handle reach itself is excluded here: it is the
        # call the statement above was assembled from, and `query` is on the
        # surface too (`TortoiseSDK.query`), so counting it would red every row.
        aliases = _handle_aliases(func)
        params = _params_of(func)
        for node in ast.walk(func):
            if not isinstance(node, ast.Call):
                continue
            callee = getattr(node.func, "attr", None) or getattr(
                node.func, "id", None)
            if callee not in writers:
                continue
            receiver = getattr(node.func, "value", None)
            if receiver is not None and _is_handle_ref(
                    receiver, aliases, params):
                continue
            offenders.append(
                f"{name}: CALLS `{callee}`, which the census either proves "
                "reaches the write handle or cannot prove read-only (both are "
                "on `enumerate_handle_surface`, and this message may not claim "
                "more than that) — the note says this row is a read, so either "
                "the call goes, the row moves out of `_UNRESOLVED`, or the "
                "callee is registered in `_PROVEN_READS` with its statements")
    return offenders


def test_the_calibration_readers_assemble_no_write_clause():
    """Re-derive the note's claim from `sdk.py`: one handle READ each, no write.

    The claim is not decorative — it is the entire reason these two functions
    are in `_UNRESOLVED` (``statement not provably read-only``) instead of being
    declared writers. Checked mechanically so a later edit that adds a `SET` to
    one of them reds HERE (and forces the row to be re-examined) instead of
    rotting the note into a false claim.

    Every fragment that can reach the assembled statement is inspected, because
    the reason the census could not settle these is that the `WHERE` text is
    built in a LOCAL and interpolated — the same indirection is followed here,
    INCLUDING an append (`query += …`), which is how
    `_uncalibrated_ep_participants` finishes its statement.
    """
    module = ast.parse(SDK_PY.read_text(), str(SDK_PY))
    functions = {node.name: node for node in ast.walk(module)
                 if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    offenders = _calibration_read_offenders(functions, module=module)
    assert not offenders, (
        "the hand-check note above `_UNRESOLVED` no longer matches the code it "
        "describes — fix the note, or move the row out of that inventory:\n  "
        + "\n  ".join(offenders))


def test_the_write_pin_sees_a_write_appended_with_augassign():
    """NON-VACUITY for the `ast.AugAssign` arm of `_local_bindings` (P1).

    `_uncalibrated_ep_participants` builds its statement with `query = …` in an
    `if`/`else` and finishes it with `query += …`. Without an `ast.AugAssign`
    arm the appended fragment was never inspected, so a write added THAT way
    produced no offender while the stated read shape stayed satisfied by the
    plain assignments — the join in `_assembled_statement_fragments` simply
    never saw it, so the pin stayed green over a writing row. This is that
    shape, declared read-only on the first two fragments and writing on the
    third, which is precisely how the real function is laid out.
    """
    source = '''
class Door:
    def reading(self, proj, ids, *, limit=None):
        where = f"WHERE n.id IN $ids"
        query = f"MATCH (n:Point) WHERE {where} RETURN n.id"
        if limit is not None:
            query += " SET n.poked = 1"
        return proj.g.query(query, params={"ids": ids}).result_set
'''
    functions = {node.name: node for node in ast.walk(ast.parse(source))
                 if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    offenders = _calibration_read_offenders(
        functions, (("reading", "RETURN n.id"),))
    assert any("not a read any more" in offender for offender in offenders), (
        "a write appended with `query += …` produced no offender, so the note "
        f"can rot false with nothing red (offenders={offenders})")

    # …and the polarity holds: the same function with a READ-only append is clean.
    clean = source.replace('" SET n.poked = 1"', 'f" LIMIT {int(limit)}"')
    clean_functions = {
        node.name: node for node in ast.walk(ast.parse(clean))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assert not _calibration_read_offenders(
        clean_functions, (("reading", "RETURN n.id"),)), (
        "a read-only `query += …` must not red the pin")


def test_the_write_pin_uses_the_shared_write_classifier():
    r"""NON-VACUITY for the #3595 rule: no local notion of "write" (review P1).

    The pin first declared its own clause regex, which disagreed with the
    runtime door in BOTH directions: it was case-sensitive and had no
    `FOREACH`/`DROP`, so three real writes were invisible to it; and it lacked
    the `(?<![\w.$:])` lookbehind, so a property named like a keyword reddened
    it on a well-formed READ (#7174's false refusal, one layer out).
    """
    invisible_to_a_local_regex = {
        "lower-case clause":
            "MATCH (n:Point) WHERE n.id IN $ids set n.ep = 1 RETURN n.id",
        "FOREACH body":
            "MATCH (n:Point) FOREACH (x IN [1] | SET n.ep = 1) RETURN n.id",
        "DROP": "MATCH (n:Point) RETURN n.id DROP INDEX ON :Point(id)",
        "write PROCEDURE":
            "MATCH (n:Point) RETURN n.id CALL "
            "db.idx.fulltext.createNodeIndex('Point','content')",
    }
    for label, statement in invisible_to_a_local_regex.items():
        assert _assembled_write_evidence(statement) is not None, (
            f"{label}: `_statement_writes` calls this a write but the pin did "
            f"not flag it — {statement!r}")
    read_with_a_keyword_property = (
        "MATCH (n:Point) WHERE n.id IN $ids RETURN n.id, n.SET, n.DROP")
    assert _assembled_write_evidence(read_with_a_keyword_property) is None, (
        "a READ whose property names look like clauses was flagged as a write "
        "— the canonical classifier excludes `n.SET`/`n.DROP` (#7174), so a "
        "local regex without that lookbehind would false-red this pin")


def _chain_source(poke_links: int) -> str:
    """A reader whose ONLY write clause sits behind `poke_links` NAME hops.

    The read shape stays in the statement itself, so a walk that stops following
    names still sees `RETURN n.id` and still sees no write: the fragment below is
    exactly the shape where following indirection is the whole check.
    """
    lines = [
        "class Door:",
        "    def reading(self, proj):",
        '        query = (f"MATCH (n:Point) WHERE n.id IN $ids"',
        '                 " RETURN n.id ")',
        '        p0 = " SET n.poked = 1"',
    ]
    for link in range(1, poke_links):
        lines.append(f'        p{link} = f"{{p{link - 1}}}"')
    lines.append(f'        query += f"{{p{poke_links - 1}}}"')
    lines.append("        return proj.g.query(query).result_set")
    return "\n".join(lines) + "\n"


def _offenders_for(source: str, read_shape: str = "RETURN n.id",
                   with_module: bool = False) -> list[str]:
    """`_calibration_read_offenders` over a synthetic source, no census walk.

    `with_module` threads the parsed source as the module AST, which is what the
    real pin does (`sdk.py`); without it the module-constant hop is disabled and
    a name that only the MODULE binds is unreadable — the documented polarity of
    `_fragments_of`.
    """
    tree = ast.parse(source)
    functions = {node.name: node for node in ast.walk(tree)
                 if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    return _calibration_read_offenders(
        functions, (("reading", read_shape),),
        module=tree if with_module else None, writers=frozenset())


def test_the_fragment_walk_follows_a_name_past_six_hops():
    """NON-VACUITY for the depth cap in `_fragments_of` (review P2).

    The walk returned its string constants and STOPPED at `depth > 6`, without
    recording anything in `unread` — so past six hops a write held in a
    VARIABLE was neither inspected nor reported, and
    `_calibration_read_offenders` (`if unread:`) had nothing to say. Measured on
    the real tree, `_uncalibrated_ep_participants` assembles its statement at
    depth 7-11, i.e. the ceiling was already a single hop from live code: here
    the write is 12 name hops down, the read shape is satisfied at the top, and
    the pre-fix walk came back clean over a statement that writes.
    """
    offenders = _offenders_for(_chain_source(12))
    assert any("not a read any more" in offender for offender in offenders), (
        "a write reached through 12 name hops produced no offender, so the "
        "depth ceiling silently dropped it — the pin stays green over a "
        f"writing row (offenders={offenders})")


def test_the_fragment_walk_retains_the_nodes_it_keys_on():
    """NON-VACUITY for the identity guards in `_fragments_of` (review P2).

    Both guards are keyed on `id(node)`, and keying on an id is only safe when
    the entry RETAINS the node (`_MODULE_BINDINGS_CACHE` was fixed that way; its
    `test_the_module_bindings_cache_retains_its_module` holds it). Here
    `followed` was a bare `set[int]` and `visited` appended only the SCOPE dict,
    so a freed node's `id` could be recycled and the guard then fired for a
    DIFFERENT node: the walk returned `[]` for an expression it never inspected,
    neither followed nor reported. `_resolve_callee` re-parses on every
    resolution, which is exactly how such a node becomes unreachable while its
    id stays a live key.

    Pre-fix this reds deterministically: a `set` of ints has no `.items()`, and
    a `visited` entry is a bare dict, not a `(expr, scope)` pair.
    """
    source = (
        "def _parts():\n"
        '    return " SET n.poked = 1"\n'
        "\n"
        "def reading(proj):\n"
        '    query = f"MATCH (n:Point) RETURN n.id {_parts()}"\n'
        "    return proj.g.query(query).result_set\n")
    tree = ast.parse(source)
    reading = next(node for node in tree.body
                   if isinstance(node, ast.FunctionDef)
                   and node.name == "reading")
    bindings = _local_bindings(reading)
    assert "query" in bindings, "the synthetic reader binds no statement"
    followed: dict = {}
    visited: dict = {}
    _fragments_of(bindings["query"][0], bindings, _params_of(reading),
                  tree, Path("synthetic_sdk.py"), followed, set(),
                  visited=visited)
    assert followed, (
        "the walk followed no NAME callee, so this case proves nothing about "
        "the `followed` guard")
    for key, callee in followed.items():
        assert id(callee) == key, (
            "a `followed` entry is keyed by an id but does not retain the "
            "callee, so a recycled id makes the guard skip a callee it never "
            "followed — neither followed nor reported")
        assert isinstance(callee, (ast.FunctionDef, ast.AsyncFunctionDef)), (
            f"the retained value is not the callee ({callee!r})")
    assert visited, (
        "the walk recorded no `visited` entry, so this case proves nothing "
        "about that guard")
    for key, seen in visited.items():
        for expr, _scope in seen:
            assert id(expr) == key, (
                "a `visited` entry does NOT retain the expression it is "
                "keyed by (it kept the scope dict instead), so a recycled id "
                "can fire the guard for a different expression and the walk "
                "returns [] without inspecting or reporting it")


def test_the_fragment_walk_fails_closed_at_the_depth_cap():
    """NON-VACUITY for the CAP ITSELF: it must report, never return in silence.

    A cap is unavoidable (the walk follows names, and a binding cycle would
    otherwise recurse forever), but its polarity has to be the census's: a
    fragment that cannot be shown read-only is NOT called read-only. The chain
    below is deliberately deeper than any sane cap, so the pin must red with the
    blind spot named — the pre-fix `return fragments` said nothing at all and
    this same function came back clean.
    """
    offenders = _offenders_for(_chain_source(40))
    assert any("depth cap" in offender for offender in offenders), (
        "the walk hit its depth ceiling and reported nothing, so a write "
        "deeper than the ceiling is invisible AND unannounced — a silent "
        f"blind spot is the fail-open this pin exists to prevent "
        f"(offenders={offenders})")


def test_a_builtin_shadowing_name_is_resolved_not_assumed_scalar():
    """The builtin arm is BENIGN only for a name the walk can PROVE is a builtin.

    `sub.id in dir(builtins)` skipped every builtin-shaped name as though it
    carried a scalar, rebuilding `dir(builtins)` once per name. Three states are
    pinned here:

    * a MODULE-level binding that shadows a builtin (`format`, `set`, `next` are
      ordinary variable names) resolves through the module hop, so its fragment
      — a write, here — is inspected;
    * the same shadow with NO module AST is reported in `unread` rather than
      skipped: with nothing to check the name against, "this is the builtin" is
      a claim the walk cannot make. This half is NON-VACUOUS — the pre-fix arm
      skipped it in silence and the function came back clean over a write;
    * a name that really IS the builtin stays benign (no offender, no `unread`).

    The first half is a regression guard rather than a non-vacuity case: the
    local-then-module branches already preceded the builtin arm, so the ORDER
    was already resolving a shadow — what is new is that the arm states that
    dependency instead of relying on it invisibly.
    """
    shadowing = (
        'format = " SET n.poked = 1"\n'
        '\n'
        'class Door:\n'
        '    def reading(self, proj):\n'
        '        query = "MATCH (n:Point) WHERE n.id IN $ids RETURN n.id "\n'
        '        query += f"{format}"\n'
        '        return proj.g.query(query).result_set\n')
    offenders = _offenders_for(shadowing, with_module=True)
    assert any("not a read any more" in offender for offender in offenders), (
        "a module-level binding that SHADOWS a builtin was skipped as a "
        f"scalar, so its statement fragment was never inspected "
        f"(offenders={offenders})")
    blind = _offenders_for(shadowing)
    assert any("could not be read out" in offender for offender in blind), (
        "with no module AST the walk cannot tell a shadowing name from the "
        "builtin, and it skipped it anyway — a silent blind spot is the "
        f"fail-open this pin exists to prevent (offenders={blind})")

    # …and a name that IS the builtin stays benign: no offender, no `unread`.
    benign = (
        'class Door:\n'
        '    def reading(self, proj, ids):\n'
        '        query = (f"MATCH (n:Point) WHERE n.id IN $ids "\n'
        '                 f"LIMIT {int(len(ids) > 0)}")\n'
        '        query += " RETURN n.id"\n'
        '        return proj.g.query(query).result_set\n')
    assert not _offenders_for(benign, with_module=True), (
        "a genuine builtin call in the statement was reported as unread, so "
        "every `int(...)`/`len(...)` fragment would red the pin")


def test_the_handle_reach_count_follows_an_alias():
    """NON-VACUITY for the alias arm of `_handle_query_calls` (review P1).

    The census resolves `h = proj.g; h.query(…)`; a receiver-name-only check
    here would see only ONE reach in the function below and the "found 1 call"
    assertion would pass while the second statement — the one a write would be
    added to — stayed uninspected.
    """
    source = '''
class Door:
    def reading(self, proj):
        proj.g.query("MATCH (n:Point) RETURN n.id")
        h = proj.g
        h.query("MATCH (n:Point) RETURN n.id")
'''
    func = next(node for node in ast.walk(ast.parse(source))
                if isinstance(node, ast.FunctionDef))
    assert len(_handle_query_calls(func)) == 2, (
        "the handle reach through the alias `h` was not counted, so a second "
        "statement (and any write added to it) is invisible to the pin")


def test_the_handle_reach_follows_a_handle_parameter():
    """NON-VACUITY for the `params` arm of `_handle_query_calls` (review MINOR).

    The census counts a handle RECEIVED AS AN ARGUMENT — its documented
    over-report, and the shape a real writer hides in
    (`clear_config_reset(proj.g)` → `clear_config_reset(g)`).
    `_handle_query_calls` called `_is_handle_ref` WITHOUT the parameter set, so
    a reach whose receiver is a handle parameter was not a reach at all: the
    "found 1 call" assertion accounted for the statements it could see and
    stayed green over the one it could not.

    `self` is excluded from that set on purpose (`_params_of`, matching
    `enumerate_handle_surface`), so `self.<verb>(…)` is never mistaken for a
    handle reach — the second assertion holds that.
    """
    source = '''
class Door:
    def reading(self, g, ids):
        return g.query("MATCH (n:Point) WHERE n.id IN $ids RETURN n.id")
'''
    func = next(node for node in ast.walk(ast.parse(source))
                if isinstance(node, ast.FunctionDef))
    assert len(_handle_query_calls(func)) == 1, (
        "the handle reach through the handle PARAMETER `g` was not counted — "
        "a second such reach would leave the pin's one-call assertion green "
        "over an uninspected statement")
    assert "self" not in _params_of(func), (
        "`self` was counted as a handle parameter, so every `self.<verb>(…)` "
        "call would read as a handle reach and the one-call assertion would "
        "fire on a method call that touches no handle at all")


def test_an_unresolvable_callee_is_reported_not_dropped():
    """NON-VACUITY for the REPORT arm of `_fragments_of` (review P1).

    The callee arm recursed only `if callee is not None` and then `continue`d
    unconditionally, so a call the walk could not resolve contributed neither a
    fragment nor an `unread` entry — while an unknown NAME redded the pin a few
    lines below. The pin therefore stayed GREEN over a statement whose suffix
    it never read, which is the polarity `_fragments_of`'s own docstring
    forbids ("anything that cannot be resolved is added to `unread` rather
    than skipped").

    Reachable shape: `_resolve_callee` matches `ImportFrom` by `alias.name`,
    never by the alias, so `from .cypher import build_suffix as _suffix` +
    `query += _suffix()` is invisible; and a package import
    (`from .projection import _stored_prop_value`) resolved through no
    `__init__.py` hop at all.
    """
    source = (
        'class Door:\n'
        '    def reading(self, proj):\n'
        '        query = "MATCH (n:Point) RETURN n.id "\n'
        '        query += f"{_suffix()}"\n'
        '        return proj.g.query(query).result_set\n')
    offenders = _offenders_for(source)
    assert any("could not be read out" in offender for offender in offenders), (
        "a call the walk could not resolve was dropped in silence, so the pin "
        "stayed green over a statement whose suffix was never read — exactly "
        f"the fail-open this arm exists to close (offenders={offenders})")

    # …and a genuine BUILTIN call stays benign, or every `int(...)`/`len(...)`
    # fragment would red the rows this pin exists to keep green.
    benign = (
        'class Door:\n'
        '    def reading(self, proj, ids):\n'
        '        query = (f"MATCH (n:Point) WHERE n.id IN $ids "\n'
        '                 f"LIMIT {int(len(ids) > 0)}")\n'
        '        query += " RETURN n.id"\n'
        '        return proj.g.query(query).result_set\n')
    assert not _offenders_for(benign), (
        "a genuine builtin call was reported as unread, so `int(...)` would "
        "red every pinned row")


def test_a_shadowed_builtin_CALLEE_is_reported_not_blessed():
    """NON-VACUITY for the builtin half of the callee arm (review P2).

    The arm exempted every builtin-SHAPED name from `unread` without consulting
    the binding tables — while the bare-name arm and the scalar-coercion arm a
    few lines up both refuse to bless such a name unless it is UNBOUND. So
    `next = _parts` / `format = _suffix_builder` (or an alias import) made the
    name a user callable, `_resolve_callee` did not follow it, the name matched
    `_BUILTIN_NAMES`, nothing was reported, and the callee's `return` text was
    never walked: `query += f"{next()}"` came back CLEAN over a write in
    `_parts`. Measured: `[]` before this fix, both halves.
    """
    module_level = (
        'def _parts():\n'
        '    return " SET n.poked = 1"\n'
        'next = _parts\n'
        'class Door:\n'
        '    def reading(self, proj):\n'
        '        query = "MATCH (n:Point) WHERE n.id IN $ids RETURN n.id "\n'
        '        query += f"{next()}"\n'
        '        return proj.g.query(query).result_set\n')
    offenders = _offenders_for(module_level, with_module=True)
    assert any("could not be read out" in offender for offender in offenders), (
        "a MODULE binding that shadows a builtin was called as a function and "
        "the call was blessed as the builtin, so its return text — a write, "
        f"here — was never read nor reported (offenders={offenders})")

    # ⛔ THE LOCAL HALF USED TO BE VACUOUS (review P2, measured): the source
    # below bound `parts = _parts` and called `format(parts())`, so `format` was
    # genuinely UNBOUND (the builtin exemption was CORRECT there) and the
    # offender the assertion saw (`parts()`) came from the unresolvable-callee
    # arm, fixed one commit earlier — reverting `or sub.func.id in bindings` left
    # the whole file green. The local now shadows the builtin itself, so this
    # half observes the LOCAL table and nothing else: `format` IS bound in
    # `reading`, `_resolve_callee` still does not follow it, and without the
    # local-binding disjunct the call is blessed as the builtin and `_parts`'
    # return text is never read.
    local = (
        'def _parts():\n'
        '    return " SET n.poked = 1"\n'
        'class Door:\n'
        '    def reading(self, proj):\n'
        '        format = _parts\n'
        '        query = "MATCH (n:Point) WHERE n.id IN $ids RETURN n.id "\n'
        '        query += f"{format()}"\n'
        '        return proj.g.query(query).result_set\n')
    offenders = _offenders_for(local, with_module=True)
    assert any("could not be read out" in offender and "format()" in offender
               for offender in offenders), (
        "a LOCAL binding of a builtin-shaped name was blessed as the builtin, "
        f"so its call was neither followed nor reported (offenders={offenders})")


def test_an_attribute_callee_on_a_resolved_value_is_reported():
    """NON-VACUITY for the NON-NAME callee arm of `_fragments_of` (review P2).

    An `ast.Call` whose `func` is an `Attribute` matched neither the NAME arm
    nor the bare-name arm, so it contributed neither a fragment nor an `unread`
    entry — the defect closed for NAME callees at `aec5d1e0e`, one branch over.
    It is not rescued by the receiver: when the receiver RESOLVES (a local bound
    to a call, a module binding) nothing else reports it. Measured: `[]` over
    `b = _make(); query += f"{b.suffix()}"`.

    The benign half is load-bearing, not decoration: a blanket report reds the
    two real rows, which assemble their statements with `", ".join(...)` and
    `_CYPHER_CHAR_LITERAL.get(ch)`, and `_live_only` does `clause.split(…)` on a
    followed callee's parameter.
    """
    via_local = (
        'def _make():\n'
        '    return object()\n'
        'class Door:\n'
        '    def reading(self, proj):\n'
        '        query = "MATCH (n:Point) WHERE n.id IN $ids RETURN n.id "\n'
        '        b = _make()\n'
        '        query += f"{b.suffix()}"\n'
        '        return proj.g.query(query).result_set\n')
    offenders = _offenders_for(via_local, with_module=True)
    assert any("could not be read out" in offender for offender in offenders), (
        "a method call on a resolved local was dropped in silence, so a write "
        f"returned by it is invisible AND unannounced (offenders={offenders})")

    via_module = (
        'def _make():\n'
        '    return object()\n'
        'BUILDER = _make()\n'
        'class Door:\n'
        '    def reading(self, proj):\n'
        '        query = "MATCH (n:Point) WHERE n.id IN $ids RETURN n.id "\n'
        '        query += f"{BUILDER.suffix()}"\n'
        '        return proj.g.query(query).result_set\n')
    offenders = _offenders_for(via_module, with_module=True)
    assert any("could not be read out" in offender for offender in offenders), (
        "a method call on a MODULE binding was dropped in silence — the same "
        f"fail-open one hop out (offenders={offenders})")

    # The three measured benign shapes, each kept benign on purpose.
    benign = (
        "KINDS = ('statement', 'observation')\n"
        "CHARS = {'q': 'x'}\n"
        "def _live_only(clause):\n"
        "    return ' AND '.join(clause.split(' AND '))\n"
        "class Door:\n"
        "    def reading(self, proj):\n"
        '        query = "MATCH (n:Point) WHERE n.id IN $ids "\n'
        "        query += f\"RETURN n.id {' - '.join(KINDS)} {CHARS.get('q')} \"\n"
        '        query += f"{_live_only(\'a AND b\')}"\n'
        '        return proj.g.query(query).result_set\n')
    offenders = _offenders_for(benign, with_module=True)
    assert not offenders, (
        "a method call on a LITERAL, on a name bound only to a literal "
        "container, or on a followed callee's parameter was reported — those "
        "are the shapes the two pinned rows assemble their statements with "
        f"(`, `.join / dict `.get` / `_live_only`'s `clause.split`): "
        f"{offenders}")


def test_the_params_set_matches_the_census_for_a_positional_only_parameter():
    """NON-VACUITY for `_params_of`'s census parity (review P2).

    `_params_of` claimed to build its set "exactly as `enumerate_handle_surface`
    does it" but omitted `args.posonlyargs`, while the census includes it and
    `_is_handle_ref` treats a `params` member as the handle. A handle received
    as a positional-only parameter was therefore not a reach: as a SECOND reach
    it left the "found 1 call" assertion green over an uninspected statement —
    the same hole the `params` arm closed in `db73165cc`.
    """
    source = '''
class Door:
    def reading(self, g, /, proj):
        proj.g.query("MATCH (n:Point) RETURN n.id")
        g.query("MATCH (n:Point) RETURN n.id")
'''
    func = next(node for node in ast.walk(ast.parse(source))
                if isinstance(node, ast.FunctionDef))
    assert "g" in _params_of(func), (
        "a positional-only parameter was missing from `_params_of`, so a "
        "reach through it was not counted and a second statement stayed "
        "uninspected")
    assert len(_handle_query_calls(func)) == 2, (
        "the handle reach through the POSITIONAL-ONLY parameter `g` was not "
        "counted, so the one-call assertion stays green over two reaches")


def test_the_statement_argument_mirrors_the_census_for_execute_command():
    """NON-VACUITY for `_statement_argument`'s per-verb index (review P2).

    `enumerate_handle_surface` takes `execute_command`'s statement at index 2
    (`"GRAPH.QUERY", <graph>, <cypher>`); this helper returned `args[0]` for
    every verb, so the Cypher was never inspected and the row red only because
    its read shape was absent — a false FAIL whose message blames the note.
    """
    call = next(
        node for node in ast.walk(ast.parse(
            'proj.g.execute_command("GRAPH.QUERY", "g", '
            '"MATCH (n:Point) RETURN n.id")'))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "execute_command")
    statement = _statement_argument(call)
    assert isinstance(statement, ast.Constant) \
        and statement.value == "MATCH (n:Point) RETURN n.id", (
        "`execute_command`'s statement was read from index 0, so the Cypher "
        f"at index 2 was never inspected (got {ast.dump(statement) if statement else None})")


def test_a_directly_interpolated_parameter_is_reported_not_blessed():
    """NON-VACUITY for the parameter arm of `_fragments_of` (review P2).

    `elif sub.id in params: continue` blessed EVERY parameter of the enclosing
    scope, on the docstring's justification that parameters "carry bind data and
    integers". That is sound for a FOLLOWED CALLEE (its argument was walked in
    the caller's expression) but NOT for the top-level function, where there is
    no caller expression in view: a caller passing `" SET n.poked = 1"` into a
    parameter interpolated as `f"…{extra}"` produced a writing statement the pin
    called a read. A parameter used as a scalar ARGUMENT (`int(limit)`) stays
    benign, or the real rows would red.
    """
    source = (
        'class Door:\n'
        '    def reading(self, proj, extra):\n'
        '        query = f"MATCH (n:Point) RETURN n.id {extra}"\n'
        '        return proj.g.query(query).result_set\n')
    offenders = _offenders_for(source)
    assert any("could not be read out" in offender for offender in offenders), (
        "a parameter interpolated straight into the statement was blessed as "
        "read-only, so a caller-supplied write passed the pin — the top-level "
        f"scope has no argument to have inspected (offenders={offenders})")

    # ⛔ `str(extra)` is the same hole one call away: `str` is a builtin (so the
    # callee REPORT arm is silent) and returns its argument VERBATIM, so an
    # exemption that blessed "any call argument" let a caller's Cypher through.
    # Only scalar coercions (`int`, `len`, …) may bless an argument.
    via_str = (
        'class Door:\n'
        '    def reading(self, proj, extra):\n'
        '        query = f"MATCH (n:Point) RETURN n.id {str(extra)}"\n'
        '        return proj.g.query(query).result_set\n')
    assert any("could not be read out" in offender
               for offender in _offenders_for(via_str)), (
        "`str(extra)` is a builtin call, so it was treated as a benign scalar "
        "and a caller-supplied write passed through verbatim — only coercions "
        "whose result cannot carry statement text may exempt an argument")

    benign = (
        'class Door:\n'
        '    def reading(self, proj, ids):\n'
        '        query = (f"MATCH (n:Point) WHERE n.id IN $ids "\n'
        '                 f"LIMIT {int(len(ids) > 0)}")\n'
        '        query += " RETURN n.id"\n'
        '        return proj.g.query(query).result_set\n')
    assert not _offenders_for(benign), (
        "a parameter used as a scalar ARGUMENT (`int(...)`, `len(...)`) was "
        "reported as unread, so every such fragment would red a real row")


def test_the_callee_resolver_prefers_the_package_over_a_sibling_shim():
    """NON-VACUITY for `_resolve_callee`'s package preference (review P2).

    `tortoise/projection.py` exists as a 37-line shim, so a MODULE-first
    resolver parsed the shim, found no def, and never reached the package
    `__init__.py` where `_stored_prop_value` actually lives — the package hop
    added for exactly that import never fired, and nothing tested it. Python
    resolves `import tortoise.projection` to the PACKAGE, so the resolver must
    too (verified: the package wins at runtime).
    """
    tree = ast.parse(SDK_PY.read_text(), str(SDK_PY))
    func, _module, _path = _resolve_callee("_stored_prop_value", tree, SDK_PY)
    assert func is not None, (
        "`from .projection import _stored_prop_value` did not resolve: the "
        "sibling `tortoise/projection.py` shim was parsed INSTEAD of the "
        "package `__init__.py` that Python actually imports, so that callee's "
        "return was unreadable")


def test_a_write_in_a_callee_default_is_read():
    """NON-VACUITY for the DEFAULT walk in `_fragments_of` (review P2).

    `params_are_inspected` blesses a callee's parameters because "its argument
    was inspected in the caller's expression" — but a DEFAULT is a value the
    call site need not supply at all, so it was neither inspected nor reported:
    a callee called WITH an argument (so its parameters are blessed) whose other
    default carries the write came back GREEN with an empty `unread`, because
    `_function_returns` never walks defaults.
    """
    source = (
        'def _suffix(seen, extra=" SET n.poked = 1"):\n'
        '    return f"{seen}{extra}"\n'
        '\n'
        'class Door:\n'
        '    def reading(self, proj):\n'
        '        query = "MATCH (n:Point) RETURN n.id "\n'
        '        query += f"{_suffix(\"\")}"\n'
        '        return proj.g.query(query).result_set\n')
    offenders = _offenders_for(source, with_module=True)
    assert any("not a read any more" in offender for offender in offenders), (
        "a write sitting in a callee's DEFAULT was never walked, and the "
        "call's argument presence blessed the parameters — so the pin stayed "
        f"green over a writing statement (offenders={offenders})")


def test_a_local_rebind_does_not_hide_a_parameter():
    """NON-VACUITY for the `bindings`-shadows-parameter arm (review P2).

    The `bindings` branch is tested BEFORE the `params` arm, and `_local_bindings`
    is a flat union that also collects a `for` target, a comprehension variable
    and a nested helper's assignment. So rebinding a parameter name resolved it
    to the binding and the `params` arm was never reached: a caller-supplied
    fragment was neither followed nor reported (`f"…{extra}"` came back GREEN
    once `for extra in []:` was added).
    """
    source = (
        'class Door:\n'
        '    def reading(self, proj, extra):\n'
        '        query = f"MATCH (n:Point) RETURN n.id {extra}"\n'
        '        for extra in []:\n'
        '            pass\n'
        '        return proj.g.query(query).result_set\n')
    assert any("could not be read out" in offender
               for offender in _offenders_for(source)), (
        "a local rebind of a parameter name HID the parameter, so a "
        "caller-supplied fragment was neither followed nor reported — the "
        "exact hole the bare `{extra}` case is reported for")


def test_a_shadowed_scalar_coercion_does_not_exempt_its_argument():
    """NON-VACUITY for the shadowing checks on the scalar allow-list (P2).

    The exemption keyed on the call's NAME, without checking that the name still
    IS the builtin — the asymmetry the bare-name arm already handles. So
    `int = str; int(extra)` blessed its argument while `str(extra)` is reported,
    i.e. the `str` hole re-opened one binding away. Same for a module-level
    rebind (`round = str`).
    """
    local = (
        'class Door:\n'
        '    def reading(self, proj, extra):\n'
        '        int = str\n'
        '        query = f"MATCH (n:Point) RETURN n.id {int(extra)}"\n'
        '        return proj.g.query(query).result_set\n')
    assert any("could not be read out" in offender
               for offender in _offenders_for(local, with_module=True)), (
        "a locally SHADOWED scalar coercion (`int = str`) exempted its "
        "argument, re-opening the `str(extra)` pass-through one binding away")

    module_level = (
        'round = str\n'
        'class Door:\n'
        '    def reading(self, proj, extra):\n'
        '        query = f"MATCH (n:Point) RETURN n.id {round(extra)}"\n'
        '        return proj.g.query(query).result_set\n')
    assert any("could not be read out" in offender
               for offender in _offenders_for(module_level, with_module=True)), (
        "a MODULE-level rebind of a scalar coercion exempted its argument, so "
        "a caller-supplied fragment passed through the pin")


def test_a_yielding_callee_is_read_not_skipped():
    """NON-VACUITY for the generator arm of `_fragments_of` (review P2).

    The callee arm followed `_function_returns` only, so a RESOLVED callee with
    no `Return` — a generator — contributed NO fragments and nothing to
    `unread`: the resolver succeeded, so the "cannot be resolved" arm never ran
    either. Measured: a write yielded by a helper passed GREEN, same-module and
    through the package hop, which is the silent-drop class this branch closed
    for an unresolvable NAME, one branch over. `yield`/`yield from` hand the
    caller a string exactly as `return` does.

    The control is the same source with `yield` swapped for `return`: it reds,
    so this test distinguishes the arm and not merely "some offender".
    """
    yielding = (
        'def _parts():\n'
        '    yield " SET n.poked = 1"\n'
        '\n'
        'class Door:\n'
        '    def reading(self, proj):\n'
        '        query = "MATCH (n:Point) RETURN n.id " + "".join(_parts())\n'
        '        return proj.g.query(query).result_set\n')
    offenders = _offenders_for(yielding, with_module=True)
    assert any("not a read any more" in offender for offender in offenders), (
        "a write YIELDED by a resolved callee was neither read nor reported — "
        f"the generator shape is invisible (offenders={offenders})")

    # `yield from` is the second half of the fix and had NO test of its own:
    # dropping `ast.YieldFrom` left the whole file green (review round 4,
    # measured), i.e. the arm was correct and unguarded.
    delegating = (
        'def _inner():\n'
        '    yield " SET n.poked = 1"\n'
        '\n'
        'def _parts():\n'
        '    yield from _inner()\n'
        '\n'
        'class Door:\n'
        '    def reading(self, proj):\n'
        '        query = "MATCH (n:Point) RETURN n.id " + "".join(_parts())\n'
        '        return proj.g.query(query).result_set\n')
    assert any("not a read any more"
               for offender in _offenders_for(delegating, with_module=True)), (
        "a write delegated with `yield from` was neither read nor reported — "
        "`ast.YieldFrom` is the arm no test named")

    returning = yielding.replace('yield " SET n.poked = 1"',
                                 'return [" SET n.poked = 1"]')
    assert any("not a read any more"
               for offender in _offenders_for(returning, with_module=True)), (
        "the control does not red, so this test proves nothing about the "
        "yield arm")


def test_a_write_fed_through_an_accumulator_parameter_is_reported():
    """NON-VACUITY for the accumulator arm of a row (review round 4).

    The expression walk's only fragment sources are a RETURNED or YIELDED value.
    A callee that writes into a collection the caller passed in returns nothing
    and yields nothing, and because the resolver SUCCEEDED the "cannot be
    resolved" arm never ran either: `parts = []; _fill(parts)` then
    `"".join(parts)` came back GREEN with a `SET` in `_fill`. Reported rather
    than followed — what the callee appends is not a value this walk can bind.

    The control keeps it NARROW: a callee that mutates nothing must stay clean,
    or the arm reds every row that hands a dict to a helper.
    """
    accumulating = (
        'def _fill(out):\n'
        '    out.append(" SET n.poked = 1")\n'
        '\n'
        'class Door:\n'
        '    def reading(self, proj):\n'
        '        parts = []\n'
        '        _fill(parts)\n'
        '        query = "MATCH (n:Point) RETURN n.id " + "".join(parts)\n'
        '        return proj.g.query(query).result_set\n')
    offenders = _offenders_for(accumulating, with_module=True)
    assert any("writes into `parts`" in offender for offender in offenders), (
        "a write fed back through a MUTATED PARAMETER was neither read nor "
        f"reported (offenders={offenders})")

    benign = (
        'def _noop(out):\n'
        '    print(out)\n'
        '\n'
        'class Door:\n'
        '    def reading(self, proj):\n'
        '        parts = []\n'
        '        _noop(parts)\n'
        '        query = "MATCH (n:Point) RETURN n.id " + "".join(parts)\n'
        '        return proj.g.query(query).result_set\n')
    assert not _offenders_for(benign, with_module=True), (
        "a call that mutates NOTHING was reported as an accumulator — this arm "
        "must name the mutating shapes, not any call that takes a parameter")


#: Every mutating shape `_mutated_parameters` / `_names_feeding` CLAIM to see,
#: as a (callee body, call site, how the fragment reaches the statement) triple.
#: Enumeration matters: the first version of the accumulator test exercised
#: `.append` only, and four of the five declared arms could then be deleted with
#: the whole file green (review round 5, measured — `_ACCUMULATOR_METHODS`
#: narrowed to `{"append"}`, the `+=` arm dead, the subscript arm dead, and
#: `_names_feeding`'s aug-assign arm dead all passed).
_ACCUMULATOR_SHAPES: dict[str, tuple[str, str, str]] = {
    "append": (
        'def _fill(out):\n    out.append(" SET n.poked = 1")\n',
        "        _fill(parts)\n",
        '        query = "MATCH (n:Point) RETURN n.id " + "".join(parts)\n'),
    "extend": (
        'def _fill(out):\n    out.extend([" SET n.poked = 1"])\n',
        "        _fill(parts)\n",
        '        query = "MATCH (n:Point) RETURN n.id " + "".join(parts)\n'),
    "insert": (
        'def _fill(out):\n    out.insert(0, " SET n.poked = 1")\n',
        "        _fill(parts)\n",
        '        query = "MATCH (n:Point) RETURN n.id " + "".join(parts)\n'),
    "update": (
        'def _fill(out):\n    out.update({"k": " SET n.poked = 1"})\n',
        "        _fill(parts)\n",
        '        query = "MATCH (n:Point) RETURN n.id " + "".join(parts.values())\n'),
    "augassign": (
        'def _fill(out):\n    out += [" SET n.poked = 1"]\n',
        "        _fill(parts)\n",
        '        query = "MATCH (n:Point) RETURN n.id " + "".join(parts)\n'),
    "subscript": (
        'def _fill(out):\n    out[0] = " SET n.poked = 1"\n',
        "        _fill(parts)\n",
        '        query = "MATCH (n:Point) RETURN n.id " + "".join(parts)\n'),
    # …and the two CALL-SIDE shapes, which are about reaching the callee at all
    # rather than about how it mutates (review round 5):
    "keyword_argument": (
        'def _fill(out):\n    out.append(" SET n.poked = 1")\n',
        "        _fill(out=parts)\n",
        '        query = "MATCH (n:Point) RETURN n.id " + "".join(parts)\n'),
    "method_callee": (
        'class Door:\n    def _fill(self, out):\n'
        '        out.append(" SET n.poked = 1")\n',
        "        self._fill(parts)\n",
        '        query = "MATCH (n:Point) RETURN n.id " + "".join(parts)\n'),
    # ⛔ …and the callee-side ALIAS: the parameter is handed to a local before it
    # is mutated (review round 5, measured GREEN before this arm existed).
    "callee_alias": (
        'def _fill(out):\n    acc = out\n    acc.append(" SET n.poked = 1")\n',
        "        _fill(parts)\n",
        '        query = "MATCH (n:Point) RETURN n.id " + "".join(parts)\n'),
    # ⛔ …and the FORWARDED parameter (review P2, measured GREEN): the callee the
    # row calls mutates nothing itself — it hands the parameter to a second
    # callee that does. Reading only the callee's own syntax left the shape silent
    # one hop out; `_mutated_parameters` is transitive over the callee's calls.
    "forwarded_argument": (
        'def _forward(out):\n    out.append(" SET n.poked = 1")\n'
        '\n'
        'def _fill(out):\n    _forward(out)\n',
        "        _fill(parts)\n",
        '        query = "MATCH (n:Point) RETURN n.id " + "".join(parts)\n'),
    # ⛔ …and the closure's OWN declared sub-shapes (round 6, measured): with only
    # the single-hop row, `a = b = out` fell to `node.targets[:1]`, the
    # REVERSE-ordered two-hop fell to a one-pass loop, and `cls` could drop out of
    # the receiver tuple — all three with the whole file green.
    "chained_alias": (
        'def _fill(out):\n    a = b = out\n    b.append(" SET n.poked = 1")\n',
        "        _fill(parts)\n",
        '        query = "MATCH (n:Point) RETURN n.id " + "".join(parts)\n'),
    "reverse_two_hop_alias": (
        'def _fill(out):\n    b = a\n    a = out\n'
        '    b.append(" SET n.poked = 1")\n',
        "        _fill(parts)\n",
        '        query = "MATCH (n:Point) RETURN n.id " + "".join(parts)\n'),
    "classmethod_callee": (
        'class Door:\n    @classmethod\n    def _fill(cls, out):\n'
        '        out.append(" SET n.poked = 1")\n',
        "        cls._fill(parts)\n",
        '        query = "MATCH (n:Point) RETURN n.id " + "".join(parts)\n'),
    # …and the closure: `parts` reaches the statement only through an AUGMENTED
    # local, which is what `_names_feeding`'s aug-assign arm adds.
    "augassign_binding": (
        'def _fill(out):\n    out.append(" SET n.poked = 1")\n',
        "        _fill(parts)\n",
        '        query = "MATCH (n:Point) RETURN n.id "\n'
        '        query += "".join(parts)\n'),
}


def test_a_module_level_accumulator_is_reported_too():
    """The mutation channel one SCOPE out: a module container, not a parameter.

    `_mutated_parameters` binds a named parameter at the call site, so a callee
    that accumulates into a MODULE-level list and is called with NO argument had
    nothing to bind: `PARTS = []` / `_fill()` / `query = "… " +
    `"".join(PARTS)` came back clean while `_fill` appended `" SET …"`
    (review P2, measured GREEN before this arm). The name is matched through
    `_module_bindings`, so a callee's own LOCAL list cannot red a correct row.
    """
    source = (
        'PARTS = []\n'
        '\n'
        'def _fill():\n'
        '    PARTS.append(" SET n.poked = 1")\n'
        '\n'
        'class Row:\n'
        '    def reading(self, proj):\n'
        '        _fill()\n'
        '        query = "MATCH (n:Point) RETURN n.id " + "".join(PARTS)\n'
        '        return proj.g.query(query).result_set\n')
    offenders = _offenders_for(source, with_module=True)
    assert any("writes into module-level `PARTS`" in offender
               for offender in offenders), (
        "a callee accumulating into a MODULE-level container was neither read "
        f"nor reported (offenders={offenders})")

    # CONTROL: the same call over a module container that does NOT feed the
    # statement (the row reads its own local) is ordinary code, and must stay
    # green — the name match is scoped to the statement's feeding set.
    benign = (
        'OTHER = []\n'
        '\n'
        'def _fill():\n'
        '    OTHER.append(" SET n.poked = 1")\n'
        '\n'
        'class Row:\n'
        '    def reading(self, proj):\n'
        '        parts = []\n'
        '        _fill()\n'
        '        query = "MATCH (n:Point) RETURN n.id " + "".join(parts)\n'
        '        return proj.g.query(query).result_set\n')
    assert not _offenders_for(benign, with_module=True), (
        "a module accumulator that never reaches the statement was reported")


@pytest.mark.parametrize("shape", sorted(_ACCUMULATOR_SHAPES))
def test_every_declared_accumulator_shape_is_reported(shape):
    """Each mutating / call-side shape the arm CLAIMS to see must report.

    Table-driven on purpose: a hand-written case per arm is how `.append` became
    the only covered shape while the docstring named six (review round 5). Each
    row fails if its arm is deleted or narrowed, so the enumeration is the
    non-vacuity proof for the whole declared surface rather than one sample.
    """
    body, call, statement = _ACCUMULATOR_SHAPES[shape]
    source = f"{body}\nclass Row:\n    def reading(self, proj):\n        parts = []\n{call}{statement}        return proj.g.query(query).result_set\n"
    offenders = _offenders_for(source, with_module=True)
    assert any("writes into `parts`" in offender for offender in offenders), (
        f"the `{shape}` shape is declared by the arm but was neither read nor "
        f"reported (offenders={offenders})")


def test_the_census_writer_set_states_its_own_polarity():
    """The writers arm is "write OR NOT-PROVEN-READ", and its message says so.

    `enumerate_handle_surface` keys a function for ANY mutating call, including
    one it could not resolve — so 45 of 83 entries are functions the census
    proves nothing about (33 of them this file's own hand-checked `_UNRESOLVED`
    READ rows). The arm's message claimed "the census PROVES reaches the write
    handle", which is false for that majority, and a call to a name already
    proven read here (`_PROVEN_READS`) redded a correct row for it.

    So: the proven-read register is subtracted, a provable writer is not, and
    the message may not assert more than "write or not-proven-read".

    ⛔ THE OLD `assert not (names & proven)` IS GONE (review P2, inert
    assertion). `names` is `_census_writer_names()`, which IS
    `surface − proven`, so `names & proven` was empty by construction: it could
    not fail for any input, including the cross-module subtraction defect it
    named, so it never observed the property. The subtraction is now pinned by
    two assertions that CAN fail: the live register must actually be on the
    censused surface (or the subtraction is decorative), and a proven read from
    ANOTHER module must not be subtracted
    (`test_a_proven_read_is_subtracted_only_for_its_own_module`).
    """
    names = _census_writer_names()
    proven = _proven_read_names()
    assert proven, "`_PROVEN_READS` is empty, so this test proves nothing"
    raw = frozenset(_bare_name(key) for key in _sdk_surface())
    assert proven & raw, (
        "no `_PROVEN_READS` name is on the censused surface at all, so the "
        "subtraction changes nothing and the register is decorative — the "
        f"`rel` key no longer matches the census's spelling ({SDK_REL!r})")
    # WHAT GUARANTEES THE SUBTRACTION IS THE DEFINITION (`surface − proven`), not
    # an `assert not (names & proven)` here: that assertion held by construction
    # and therefore for every input. What is checkable is that the definition is
    # CONFINED to one module and LOAD-BEARING on the real tree — both above/below.
    assert "invalidate_point" in names, (
        "a name the census genuinely proves writes is missing from the set — "
        "the subtraction above cannot be the whole surface")

    source = '''
class Door:
    def reading(self, proj):
        query = "MATCH (n:Point) WHERE n.id IN $ids RETURN n.id"
        self._find_point_by_content("x")
        return proj.g.query(query).result_set
'''
    func = next(node for node in ast.walk(ast.parse(source))
                if isinstance(node, ast.FunctionDef))
    offenders = _calibration_read_offenders(
        {"reading": func}, (("reading", "RETURN n.id"),),
        writers=frozenset({"_find_point_by_content"}))
    assert offenders, "the unproven-callee arm produced nothing to inspect"
    assert all("which the census proves reaches the write handle" not in offender
               for offender in offenders), (
        "the message asserts the census PROVES a write for an entry that may "
        f"only be unproven (offenders={offenders})")
    assert all("either proves reaches the write handle or cannot prove "
               "read-only" in offender for offender in offenders), (
        "the message does not state the set's real polarity, so the next "
        f"reader will trust a claim the census does not make "
        f"(offenders={offenders})")


def test_a_proven_read_is_subtracted_only_for_its_own_module():
    """NON-VACUITY for the module half of the proven-read subtraction (P2).

    `_PROVEN_READS` is keyed `(rel_path, scope-qualified name)` across the whole
    package, but the set it is subtracted from is `sdk.py`'s surface and the
    consumer (`_calibration_read_offenders`) matches BARE callee names. Dropping
    the `rel` check — the pre-fix `{_bare_name(key) for _rel, key in
    _PROVEN_READS}` — therefore lets a proven read registered for ANY module
    un-guard an `sdk.py` writer that merely shares the bare name, in the
    fail-open direction: one module's proof silences another module's write.

    The subject is a SYNTHETIC surface and a SYNTHETIC register, so the test is
    fast and independent of what `sdk.py` currently contains; it reds on exactly
    that one-clause revert (`if mod == rel` dropped, or made unconditional).
    """
    surface = {"TortoiseSDK.invalidate_point": {}}
    foreign = {("tortoise/event_store.py", "TortoiseStore.invalidate_point")}
    still_a_writer = _census_writer_names(surface=surface, register=foreign)
    assert "invalidate_point" in still_a_writer, (
        "a proven read registered for ANOTHER module removed an `sdk.py` writer "
        "of the same bare name — one module's proof un-guarded another "
        f"module's write (writers={sorted(still_a_writer)})")
    own = {("tortoise/sdk.py", "TortoiseSDK.invalidate_point")}
    assert not _census_writer_names(surface=surface, register=own), (
        "a proven read registered for `sdk.py` ITSELF was not subtracted, so "
        "the register's whole purpose is gone — a correct row reds on the "
        "census's silence again")


def test_the_offenders_helper_docstring_states_the_real_polarity():
    """NON-VACUITY for the `writers` parameter's own docstring (review P2).

    `_census_writer_names`'s polarity was corrected to "write **OR**
    not-proven-read" (45 of 83 entries are functions the census could not
    settle), and the offender message was corrected with it — but the parameter
    DOCSTRING eleven lines below still said "the set of names the census proves
    write", i.e. the narrower, false thing. No pin covered prose, so the
    overtuned sentence could sit beside the corrected one indefinitely and tell
    every reader of the helper the wrong contract.

    Red on the pre-fix text: it contained the literal "proves write".
    """
    doc = " ".join((_calibration_read_offenders.__doc__ or "").split())
    assert doc, "`_calibration_read_offenders` lost its docstring"
    assert "proves write" not in doc, (
        "the `writers` docstring claims the census PROVES a write again — the "
        "real polarity is 'write OR not-proven-read' (45 of 83 entries are "
        f"unproven), so the parameter's stated contract is false (doc={doc!r})")
    assert "not-proven-read" in doc, (
        "the `writers` docstring no longer states the 'write OR "
        "not-proven-read' polarity it receives from `_census_writer_names` "
        f"(doc={doc!r})")
    assert "`None`" in doc, (
        "the `writers` docstring no longer documents the `None` default, which "
        f"is what the real pin passes (doc={doc!r})")


def test_the_calibration_reader_list_is_bound_to_the_rows_it_guards():
    """NON-VACUITY for `_CALIBRATION_READERS` itself (review P2).

    Both pins iterate this hand-maintained tuple, so deleting an entry silently
    UN-GUARDED that row: with `_uncalibrated_ep_participants` removed, its
    citation check and its claim re-derivation both vanished and every pin
    stayed green. Binding the tuple to the `#9063` rows in `_UNRESOLVED` makes a
    dropped (or extra) entry red instead of quiet — the same `assert scanned >
    50` idiom the symbol pin already uses.
    """
    names = {name for name, _ in _CALIBRATION_READERS}
    assert names == {"calibrate_summary", "_uncalibrated_ep_participants"}, (
        "`_CALIBRATION_READERS` no longer names exactly the two #9063 rows, "
        f"so a row is un-guarded without a red (got {sorted(names)})")
    assert names <= set(_UNRESOLVED), (
        "a `_CALIBRATION_READERS` entry is not an `_UNRESOLVED` row, so the "
        "pin would check a function the note does not cover")


def test_the_write_pin_sees_a_write_through_another_sdk_method():
    """NON-VACUITY for the cross-method arm of `_calibration_read_offenders`.

    The note claimed "no write through another SDK method" while the pin knew
    exactly one name (`set_point_baseline`). These rows are hand-checked READS
    whether or not the statement carries a clause, so a `self.invalidate_point(
    …)` (or `record_calibration`, or `_touch_inherited_at`) added to either
    function left the pin green and the note false. The arm is driven by the
    census's own writer list, and the polarity is held both ways: a call the
    census does not prove writes is clean, and the handle reach itself is never
    mistaken for a method write (`query` is on the surface too).
    """
    source = '''
class Door:
    def reading(self, proj):
        query = "MATCH (n:Point) WHERE n.id IN $ids RETURN n.id"
        self.invalidate_point("p1")
        return proj.g.query(query).result_set
'''
    func = next(node for node in ast.walk(ast.parse(source))
                if isinstance(node, ast.FunctionDef))
    offenders = _calibration_read_offenders(
        {"reading": func}, (("reading", "RETURN n.id"),),
        writers=frozenset({"invalidate_point"}))
    assert any("invalidate_point" in offender for offender in offenders), (
        "a write through ANOTHER SDK method produced no offender, so the "
        f"note's second claim can rot false with nothing red (offenders={offenders})")

    # …and the arm is not a blanket "any call": a name the census does not
    # prove writes is clean.
    clean = source.replace('self.invalidate_point("p1")', 'self._get_proj()')
    clean_func = next(node for node in ast.walk(ast.parse(clean))
                      if isinstance(node, ast.FunctionDef))
    assert not _calibration_read_offenders(
        {"reading": clean_func}, (("reading", "RETURN n.id"),),
        writers=frozenset({"invalidate_point"})), (
        "a call to a function the census does not prove writes was reported as "
        "a write — this arm must follow the census, not sound like a keyword"
        )

    # …and the handle reach is not double-counted as a method write, even though
    # `query` is itself on the census's surface (`TortoiseSDK.query`).
    handle_only = source.replace('        self.invalidate_point("p1")\n', '')
    handle_func = next(node for node in ast.walk(ast.parse(handle_only))
                       if isinstance(node, ast.FunctionDef))
    assert not _calibration_read_offenders(
        {"reading": handle_func}, (("reading", "RETURN n.id"),),
        writers=frozenset({"query"})), (
        "the handle reach `proj.g.query(...)` was reported as a write through "
        "another SDK method")


def test_the_module_bindings_cache_retains_its_module():
    """NON-VACUITY for the identity cache in `_module_bindings` (review MINOR).

    The cache is keyed on `id(module)`, and the comment used to claim the id
    "cannot be recycled underneath it". That is true only for the sdk AST the
    caller holds — NOT for the imported-callee hop, where `_resolve_callee`
    parses `tortoise/live.py`, uses it, and drops it while its id stays a live
    key: a later `ast.parse` can land on that address and the cache would hand
    back ANOTHER module's constants (wrong fragments can resolve a write away).
    The entry now RETAINS the AST, which makes the recycled id unrepresentable
    rather than merely unlikely — and the identity guard is held below too.
    """
    module = ast.parse('_WHERE = "WHERE n.id IN $ids"')
    key = id(module)
    bindings = _module_bindings(module)
    assert "_WHERE" in bindings
    entry = _MODULE_BINDINGS_CACHE[key]
    assert entry[0] is module, (
        "the cache entry does not retain the AST it was built from, so nothing "
        "keeps this `id` from being recycled by a later `ast.parse` while the "
        "key is still live")
    assert entry[1] is bindings, (
        "the cache entry does not carry the bindings it answers with")
    assert _module_bindings(module) is bindings, (
        "a repeated lookup for the SAME module must not rebuild")

    # An id that collides with an entry built from a DIFFERENT AST must be
    # rejected — that is the exact state a recycled id produces, simulated
    # deterministically.
    impostor = ast.parse('_LIVE = "AND n.status = 1"')
    _MODULE_BINDINGS_CACHE[id(impostor)] = (object(), {"_STALE": []})
    assert "_STALE" not in _module_bindings(impostor), (
        "a cache entry whose key matched but whose AST was a different object "
        "was returned, so a recycled id serves another module's constants")
    assert "_LIVE" in _module_bindings(impostor), (
        "the impostor entry was not replaced with the real module's bindings")


#: THE APPLY-FIRST LANE'S MEMBERSHIP, PINNED (#9206). One line per door, so a
#: new `_declares_apply_first_write_lane` (or an inline
#: `_apply_first_write_scope`) reds here until it is reviewed and listed. The set
#: may only change with a line in this file — which is what stops a journal-first
#: door from quietly taking the WEAKER claim because the write region was easier
#: to wrap that way.
_APPLY_FIRST_DECLARED_DOORS = {
    # The apply-first, by decision doors: the record's OUTCOME depends on the
    # write, and the decision is recorded where the door lives.
    "delete_point",          # #9207: cache-first, the destroy is the commit point
    "update_point",          # #8830: the promote arm's guarded SET, then its record
    # The mechanical apply-first doors (#9206): their record is built from the
    # node they just wrote, so the append cannot precede the write without
    # changing the door's commit contract (#9124 part 2).
    "create_operator",
    "create_direct_edge",
    "_update_entity",
    "_delete_entity",
    "_write_capture_turns",
    "_materialize_session_source",
    # #9206 review: the capture door's two state writes, each extracted into its
    # own door so the lane is narrow enough to be honest (the write's OUTCOME is
    # the record's input) and an ARMED handle completes them instead of having
    # the refusal swallowed by `capture_session`'s non-fatal `except`.
    "_stamp_capture_attempt_state",   # `s.capture_ok`/`s.capture_extractor`
    "_stamp_entity_link_counters",    # `s.entity_links_attempted/created`
}

#: The apply-first lane's membership OUTSIDE `sdk.py`, pinned per module
#: (#9206 review P1). The pin above enumerates `sdk.py` only, so before this a
#: declaration in `ep.py`/`hosted_api.py` carried NO record/order/membership
#: check at all — the lane's own stated failure mode (#9068) was unenforced
#: everywhere outside one file, while the flip gate accepted those doors as
#: declared. Enumerating the same surface and running the SAME offender function
#: over each module closes that gap; the membership is exact per module.
_APPLY_FIRST_DECLARED_DOORS_BY_MODULE = {
    "sdk.py": _APPLY_FIRST_DECLARED_DOORS,
    # #5380 states these records over the REMOVE's own committed set — the
    # record cannot precede the drop it counts.
    "ep.py": {"invalidate_messages", "invalidate_factor_messages"},
    # The session-delete sweep: `EntityMutated` op=delete per removed node
    # (keyed off the DELETE's own result) + `SessionDeleted` for the container.
    "hosted_api.py": {"_delete_session_sync"},
}


def test_an_apply_first_declaration_outside_the_sdk_is_checked_too():
    """The lane's honesty gate is PACKAGE-wide, not one-file (#9206 review P1).

    `tortoise/ep.py` and `tortoise/hosted_api.py` reach the same decorator, so
    they must pass the same four checks the `sdk.py` pin applies: a record IS
    appended, every write runs inside the lane, the door is genuinely
    apply-first, and its membership is pinned here. RED before the pins existed:
    the two EP doors were accepted by the flip gate as declared while the census
    (reading them through the `sdk.py`-only surface) never checked them, and its
    append detector could not see `_emit_edge_message_clear` at all — so the
    doors that DO append a record read as "appends nothing".
    """
    total = 0
    for module, pinned in sorted(_APPLY_FIRST_DECLARED_DOORS_BY_MODULE.items()):
        path = ROOT / "tortoise" / module
        surface = enumerate_handle_surface(path.read_text(), str(path))
        offenders, declared = _apply_first_declaration_offenders(surface)
        assert not offenders, (
            f"an apply-first declaration in {module} is not honest as written:\n  "
            + "\n  ".join(offenders))
        assert set(declared) == set(pinned), (
            f"the apply-first membership in {module} changed. It is pinned "
            "EXACTLY so the lane can only change with a reviewed line:\n"
            f"  declared but not pinned: "
            f"{sorted(set(declared) - set(pinned))}\n"
            f"  pinned but not declared: "
            f"{sorted(set(pinned) - set(declared))}")
        total += len(declared)
    assert total == sum(len(v) for v in
                        _APPLY_FIRST_DECLARED_DOORS_BY_MODULE.values()), (
        "the per-module pins disagree with each other about how many doors "
        f"carry the lane (counted {total})")


def test_the_derived_recompute_lane_has_its_own_kind():
    """The derived-recompute lane is NOT the census's maximal-trust `declared`.

    It appends nothing by construction, so letting `_kind()` return the same
    `"declared"` a journal-first door gets would file an unjournalled write in
    the category the sdk pin trusts most (#9206 review P1). It has its own kind.
    ⚠️ The arming gate (`test_arming_the_guard_requires_a_fully_declared_surface`)
    accounts by containment, not by kind, so this writer is still counted as
    DECLARED there — which is correct for a lane whose values replay re-derives,
    and is why the kind must say so explicitly instead of borrowing the
    journal-first label.
    """
    kind = _kind({"scopes": [(1, "_derived_recompute_write_scope")],
                 "writes": [(2, "query", "SET n.x = 1")],
                  "appends": [], "end_lineno": 3,
                  "scope_spans": [(1, 3)]})
    assert kind == "derived-recompute"
    # …and the FLIP gate must accept that kind as declared: dropping it
    # from `_DECLARED_KINDS` reds here (review MINOR) instead of sending
    # the author of a declared door to add an inventory row.
    assert kind in _DECLARED_KINDS, (
        "`_kind` returns a declared kind the flip gate does not accept — "
        "its remedy would be an inventory row, which weakens the door")


#: Doors whose apply-first ORDER the census cannot prove: an append attributed
#: to a NESTED function (or to another arm of the same body) satisfies
#: `_journal_first_violation` while the write that matters still precedes its
#: record. Each entry names the pin that owns the order proof, so the exemption
#: is a POINTER to evidence rather than an assertion here.
_APPLY_FIRST_PROVED_ELSEWHERE = {
    # The promote arm's `SET` lands first and `_record_promote()` appends after
    # it; the non-promote arm's append is unconditional in source order, so the
    # census reads the whole body as journal-first. The order is pinned by
    # `tests/test_7951_update_point_outbox.py::
    # test_the_promote_arm_stays_apply_first_a_recorded_deviation` and
    # `::test_a_refused_promote_journals_no_revision`.
    "update_point": "tests/test_7951_update_point_outbox.py",
}


def _apply_first_declaration_offenders(surface: dict) -> tuple[list[str], dict]:
    """The apply-first lane's honesty check, on ANY enumerated surface.

    Factored out of the pin below so the SAME check can be driven over a
    synthetic module (`test_the_apply_first_pin_flags_a_door_that_appends_
    nothing`, `..._a_journal_first_door_that_took_the_weaker_lane`). Without
    that, the pin's non-vacuity would rest on the real tree happening to contain
    a broken door — i.e. on a defect existing.
    """
    offenders = _duplicate_writer_offenders(surface)
    declared: dict[str, dict] = {}
    for name, fact in sorted(surface.items(), key=lambda kv: kv[1]["lineno"]):
        openers = {opener for _line, opener in fact["scopes"]}
        if not openers & set(_APPLY_FIRST_LANE_NAMES):
            continue
        if openers & set(_LIVE_LANE_NAMES):
            offenders.append(
                f"{name}:{fact['lineno']} declares BOTH lane families "
                f"({sorted(openers)}) — one order per door")
            continue
        declared[_bare_name(name)] = fact
        if fact.get("duplicate_writer_defs"):
            # Already refused above (two writing definitions share one name).
            continue
        if not fact["appends"]:
            offenders.append(
                f"{name}:{fact['lineno']} declares the APPLY-FIRST lane but "
                "appends NO record — that is the #9068 defect (a declaration "
                "blessing an unjournalled cache write). Journal the write or "
                "retire the path; do not declare it here")
            continue
        if not _writes_are_inside_a_lane(fact):
            offenders.append(
                f"{name}:{fact['lineno']} declares a lane but writes outside "
                f"it at line {fact['writes'][0][0]} — presence is not "
                "containment")
            continue
        if _journal_first_violation(fact) is None:
            if _bare_name(name) in _APPLY_FIRST_PROVED_ELSEWHERE:
                continue
            offenders.append(
                f"{name}:{fact['lineno']} declares the APPLY-FIRST lane, but "
                "the census reads this door as JOURNAL-FIRST (every write has "
                "an earlier record at no greater depth). Use "
                "`_declares_live_write_lane` — the stronger claim — or, if the "
                "order proof needs a dedicated test, name it in "
                "`_APPLY_FIRST_PROVED_ELSEWHERE`")
            continue
    return offenders, declared


def test_every_apply_first_declared_door_states_an_order_and_a_record():
    """An apply-first declaration is honest OR it reds (#9206).

    The lane's claim is weaker than `_live_write_scope`'s, which is exactly why
    it needs its own gate — a lane whose check is "some opener appears" would
    let any door weaken its declaration until it is vacuous. Four properties,
    each of them something a future write door could otherwise forget:

    1. **A record IS appended.** A door that writes the cache and appends
       nothing is the #9068 defect (a declaration blessing an unjournalled
       cache write). The honest remedies there are *journal it* or *retire the
       path* — never this lane.
    2. **Every write runs INSIDE the lane.** Presence is not containment; an
       opener that wraps a read while the write sits outside authorizes nothing
       and would be refused by an armed handle.
    3. **The door is GENUINELY apply-first** — a write precedes the record that
       covers it — unless its dedicated order proof is named in
       `_APPLY_FIRST_PROVED_ELSEWHERE`. A journal-first door must carry the
       STRONGER `_declares_live_write_lane`; taking this lane instead would
       understate (and eventually erode) the guarantee it can actually make.
    4. **The membership is PINNED.** The lane can only grow or shrink through a
       reviewed line in this file, so a new door cannot adopt the weaker claim
       silently.

    Non-vacuity: the check is driven over a synthetic module by the two tests
    below, AND the declared set here must be non-empty and contain at least one
    door the census PROVES apply-first.
    """
    surface = enumerate_handle_surface(SDK_PY.read_text(), str(SDK_PY))
    offenders, declared = _apply_first_declaration_offenders(surface)
    provable = sum(
        1 for fact in declared.values()
        if _journal_first_violation(fact) is not None)

    stale = sorted(set(_APPLY_FIRST_PROVED_ELSEWHERE) - set(declared))
    assert not stale, (
        "`_APPLY_FIRST_PROVED_ELSEWHERE` names a door that does not declare the "
        f"apply-first lane any more (or was renamed): {stale} — delete the row, "
        "or the exemption outlives the claim it was written for")
    assert not offenders, (
        "an apply-first declaration is not honest as written:\n  "
        + "\n  ".join(offenders))

    assert declared, (
        "no door declares the apply-first lane — the pin would pass vacuously, "
        "and the lane would be an unexercised claim")
    assert provable, (
        "the census PROVES no declared door apply-first — the classification "
        "this pin rests on has stopped working, so every row above would be "
        "exempt-via-allowlist")
    pinned = set(_APPLY_FIRST_DECLARED_DOORS)
    assert set(declared) == pinned, (
        "the apply-first lane's membership changed. It is pinned EXACTLY: a new "
        "door must be added here with its reason (which part of its record "
        "depends on the write), and a door that left must be deleted — so the "
        "lane can only change with a reviewed line, never by drift:\n"
        f"  declared but not pinned: {sorted(set(declared) - pinned)}\n"
        f"  pinned but not declared: {sorted(pinned - set(declared))}")


#: A synthetic door that declares the apply-first lane and appends NOTHING.
#: The form a future writer takes when it reaches for the lane as a way past a
#: refusal instead of as a statement about its order.
_SYNTHETIC_NO_RECORD_DOOR = '''
class Door:
    @_declares_apply_first_write_lane("no-record")
    def writes_without_a_record(self, proj):
        proj.g.query("MATCH (n:Point {id:$id}) SET n.x = 1")
'''

#: A synthetic JOURNAL-FIRST door that took the weaker lane. Its record really
#: does precede its write, so the honest declaration is `_live_write_scope`; the
#: weaker lane would leave the door's guarantee lower than what it can make.
_SYNTHETIC_JOURNAL_FIRST_DOOR = '''
class Door:
    @_declares_apply_first_write_lane("weaker-than-proven")
    def appends_then_writes(self, proj):
        self._emit_event("PointRevised", id="p1")
        proj.g.query("MATCH (n:Point {id:$id}) SET n.x = 1")
'''


def test_the_apply_first_pin_flags_a_door_that_appends_nothing():
    """Non-vacuity, first half: the #9068 shape is REFUSED (#9206).

    A door that writes the cache and appends no record cannot take any lane —
    the honest outcomes are *journal it* or *retire the path*. If this passes
    silently, the lane is an amnesty and the whole declaration programme is a
    rubber stamp.
    """
    surface = enumerate_handle_surface(
        _SYNTHETIC_NO_RECORD_DOOR, "<synthetic-no-record>")
    offenders, declared = _apply_first_declaration_offenders(surface)
    assert "writes_without_a_record" in declared, (
        "the enumerator did not see the synthetic door at all, so this "
        "non-vacuity check would pass for the wrong reason")
    assert any("appends NO record" in o for o in offenders), (
        "a door that declares the apply-first lane while appending no record "
        f"was ACCEPTED — the #9068 defect is open: {offenders}")


def test_the_apply_first_pin_flags_a_journal_first_door_that_took_the_weaker_lane():
    """Non-vacuity, second half: the LANE must match the ORDER (#9206).

    A door whose record precedes its write can make the journal-first claim, and
    a declaration is only worth the guarantee it states. Accepting the weaker
    lane here would let a future edit that needs the stronger claim keep passing
    on yesterday's weaker declaration.
    """
    surface = enumerate_handle_surface(
        _SYNTHETIC_JOURNAL_FIRST_DOOR, "<synthetic-journal-first>")
    offenders, declared = _apply_first_declaration_offenders(surface)
    assert "appends_then_writes" in declared, (
        "the enumerator did not see the synthetic door, so this check would "
        "pass for the wrong reason")
    assert any("declares the APPLY-FIRST lane, but the census reads this door "
               "as JOURNAL-FIRST" in o for o in offenders), (
        f"a journal-first door took the weaker lane unrefused: {offenders}")


def test_every_live_declared_door_is_journal_first():
    """A live lane is only HONEST when its record provably precedes its write.

    `@_declares_live_write_lane` (and any inline `_live_write_scope` around a
    whole door) authorizes everything the function writes, including anything
    above the append — so journal-first order is NECESSARY for the declaration
    to be true. It is not sufficient, and this pin states the gap it leaves
    instead of implying there is none: it reads SOURCE ORDER plus CONTROL-FLOW
    DEPTH, so an append that is unconditional while the write sits inside a
    branch passes, and an append buried in an `if`/`try` while the write runs
    unconditionally FAILS. An append skipped by an earlier `return`, or one
    guarded by a DIFFERENT condition than the write, is invisible to an AST —
    that residue is what a reviewer reads, not what this test proves.
    """
    surface = enumerate_handle_surface(SDK_PY.read_text(), str(SDK_PY))
    checked, offenders = [], _duplicate_writer_offenders(surface)
    for name, fact in surface.items():
        openers = {opener for _line, opener in fact["scopes"]}
        if not openers & set(_LIVE_LANE_NAMES):
            continue
        if fact.get("duplicate_writer_defs"):
            # Already refused above: its write/append order would compare lines
            # from two different definitions.
            continue
        violation = _journal_first_violation(fact)
        if violation:
            offenders.append(
                f"{name}:{fact['lineno']} declares a LIVE lane but {violation}")
            continue
        checked.append(name)
    assert not offenders, (
        "a live-lane declaration covers a write that precedes its record:\n  "
        + "\n  ".join(offenders))
    assert {"invalidate_point", "retract_point",
            "supersede_point"} <= {_bare_name(n) for n in checked}, (
        "the journal-first doors #9124 part 1 declared are not all being "
        f"checked ({sorted(checked)}) — the ordering pin would be vacuous for "
        "them. `delete_point` is deliberately NOT in this set: #9207 reversed "
        "its order, so it is inventoried as apply-first and carrying a live "
        "lane on it is the defect this pin exists to catch")


#: The inventoried NO-JOURNAL / apply-first helpers each whole-door live lane
#: reaches TRANSITIVELY (#9124 review, round 3 P2). `@_declares_live_write_lane`
#: covers the whole body, so a helper's write runs inside the caller's lane —
#: `_mark_dirty` is inventoried as `no-journal` (it appends nothing) yet its
#: three `proj.g.query` writes are authorized whenever one of the three doors
#: reaches it. The per-FUNCTION census cannot see the edge
#: (`enumerate_handle_surface` attributes each write to the innermost function
#: only), so the edge is pinned HERE: it can neither grow nor vanish silently.
#: Measured on this tree: all three doors reach exactly this one helper.
#: ⚠️ `delete_point` was a FOURTH until #9207 made it apply-first: it holds no
#: lane now, so the same calls (`_mark_dirty`, and the `DETACH DELETE` itself)
#: are no longer blessed by a declaration — they are measured as undeclared and
#: would be refused by an armed handle. It is therefore absent from this pin on
#: purpose: listing it would claim a coverage that no longer exists.
_DECLARED_DOOR_NESTED_INVENTORIED = {
    # #9124 part 1 (journal-first doors)
    "invalidate_point": {"_mark_dirty"},
    "retract_point": {"_mark_dirty"},
    "supersede_point": {"_mark_dirty"},
    # #9206 (apply-first doors, + the capture writer whose turn store is the
    # nested apply-first claim). MEASURED, not assumed: every one of these
    # reaches `_mark_dirty`, which is inventoried `_NO_JOURNAL` — so the lane
    # authorizes a write that appends no record, one call edge away from the
    # per-function census. Pinning the edge is what forces a reviewed line for
    # it; the write itself is a re-derivable flatten, which is why it is
    # inventoried rather than journaled.
    "update_point": {"_mark_dirty"},
    "delete_point": {"_mark_dirty"},
    "create_operator": {"_mark_dirty"},
    "create_direct_edge": {"_mark_dirty"},
    # Declared doors that reach NO inventoried helper — listed explicitly so a
    # future nested no-journal call reds HERE rather than being absorbed by an
    # unpinned door (a door absent from this dict is not measured at all).
    "_update_entity": set(),
    "_delete_entity": set(),
    "_materialize_session_source": set(),
    "_write_capture_turns": set(),
    "_write_session_and_turns": set(),
    "_stamp_capture_attempt_state": set(),
    "_stamp_entity_link_counters": set(),
    "invalidate_messages": set(),
    "invalidate_factor_messages": set(),
    "_delete_session_sync": set(),
}

#: Every module whose declared doors the nested pin above measures. The lane is
#: declared in three modules (`sdk.py`, `ep.py`, `hosted_api.py`), and a pin that
#: parses one of them measures nothing for the other two (#9206 review P1).
_DECLARED_DOOR_NESTED_MODULES = ("sdk.py", "ep.py", "hosted_api.py")


def test_a_whole_door_lane_authorizes_only_the_pinned_nested_writes():
    """The transitive hole in a whole-door declaration, pinned (#9124 r3 P2).

    A declared door's lane authorizes EVERY nested call, so a no-journal write
    added inside any helper a declared door calls is blessed silently — the
    "declaration blesses a cache write with no committed record" defect one call
    edge away. The census is per FUNCTION and therefore structurally blind to
    the edge; this pin is the compensating gate. It asserts the MEASURED set, so
    a new nested write reds and must be reviewed before it is listed — it is not
    a claim that the listed writes are justified (that is the inventory entry's
    review, and `_mark_dirty`'s writes are a re-derivable flatten).
    """
    tree = None  # per-module below; `ast.walk` needs one tree at a time
    inventoried = (_NO_JOURNAL | _APPLY_FIRST_HELPERS | _APPLY_FIRST_DOORS
                   | _MIXED_ARM_DOORS)
    measured: dict[str, set[str]] = {}
    for module in _DECLARED_DOOR_NESTED_MODULES:
        path = ROOT / "tortoise" / module
        tree = ast.parse(path.read_text(), str(path))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                    and node.name in _DECLARED_DOOR_NESTED_INVENTORIED:
                calls = {
                    callee for sub in ast.walk(node) if isinstance(sub, ast.Call)
                    for callee in [getattr(sub.func, "attr", None)
                                   or getattr(sub.func, "id", None)]
                    if callee in inventoried
                }
                measured.setdefault(node.name, set()).update(calls)
    missing = set(_DECLARED_DOOR_NESTED_INVENTORIED) - set(measured)
    assert not missing, (
        "a pinned declared door is not in the source, so the pin measures "
        f"nothing for it: {sorted(missing)}")
    assert any(measured.values()), (
        "no declared door reaches an inventoried helper at all — the pin would "
        "be vacuous and the nested-call residual would be unmeasured")
    delta = {
        door: {"added": sorted(calls - _DECLARED_DOOR_NESTED_INVENTORIED[door]),
               "gone": sorted(_DECLARED_DOOR_NESTED_INVENTORIED[door] - calls)}
        for door, calls in measured.items()
        if calls != _DECLARED_DOOR_NESTED_INVENTORIED[door]
    }
    assert not delta, (
        "a declared door's NESTED no-journal/apply-first calls changed. The "
        "door's lane authorizes every one of them and the per-function census "
        "cannot see the edge — review the write, then pin it here:\n  "
        + "\n  ".join(f"{door}: added={v['added']} gone={v['gone']}"
                      for door, v in sorted(delta.items())))


def test_the_fixture_lane_is_used_only_by_tests():
    """`_test_fixture_write_scope` is a FIXTURE lane and may not reach product code.

    The three sites it was introduced for (`#9206` review P2) are test setup that
    seeds state outside every product lane — a `:Source` hash, the
    `:Point:Object` label pair, a pre-canonical `:Source` — and each appends NO
    record. They used to be spelled `_live_write_scope`, whose whole claim is the
    OPPOSITE ("the record is appended FIRST"): a fixture taking a product lane's
    name is the copyable template the next product writer finds first, and the
    per-function census cannot see it (`enumerate_handle_surface` runs over
    `tortoise/` and `tools/`).

    So the lane's honest scope is pinned in the ONE direction that matters: only
    `tests/` may open it. A `tortoise/` or `tools/` caller would be a product
    write claiming a fixture's name — the #9068 shape with a new spelling — and
    it reds here.
    """
    offenders: dict[str, int] = {}
    for sub in ("tortoise", "tools"):
        for path in sorted((ROOT / sub).rglob("*.py")):
            rel = str(path.relative_to(ROOT))
            if rel == "tortoise/projection/__init__.py":
                # The definition itself (the scope, its reason literal and its
                # docstring) lives here; every OTHER module is a caller.
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            n = (text.count("_test_fixture_write_scope(")
                 + text.count("_TEST_FIXTURE_LANE"))
            if n:
                offenders[rel] = n
    assert not offenders, (
        "the TEST-FIXTURE lane is opened outside `tests/` — it states only \"this "
        "write exists to set up a test\" and is not a claim a product path may "
        f"make; declare an honest product lane instead: {offenders!r}")

    # …and the fixture lane really is what those sites use (a pin that could not
    # fail if a call site silently went back to a product lane's name). Matched
    # on the CALL form (`with …(`), and this file is excluded because it names
    # the helper in the assertions below.
    users = sorted(
        p.name for p in (ROOT / "tests").rglob("*.py")
        if p.name != Path(__file__).name
        and "with _test_fixture_write_scope(" in
        p.read_text(encoding="utf-8", errors="replace"))
    assert users == [
        "test_7951_create_point_outbox.py",
        "test_7951_update_point_outbox.py",
        "test_9206_apply_first_lane.py",
        "test_9206_derived_recompute_lane.py",
    ], f"the fixture-lane callers changed: {users!r}"


def test_the_event_store_declares_every_writer():
    """The `:GraphEvent` store declares its own lane (#9124 part 1).

    Its writers are not the SDK's: `_emit_event` reaches the store BEFORE the
    JSONL append, so a caller's `_live_write_scope` cannot cover them — the
    declaration has to live at the writer. Measured before this landed: on a
    journal-backed SDK the store's writes were refused and `_emit_event`'s bare
    `except Exception` swallowed the refusal, so `poll_events`/the change feed
    returned nothing while the API reported success (#9208).
    """
    surface = enumerate_handle_surface(EVENT_STORE_PY.read_text(), str(EVENT_STORE_PY))
    assert not _duplicate_writer_offenders(surface, "event_store "), (
        "an event_store name merges two writing definitions — the store's own "
        "surface must be unambiguous")
    undeclared = {
        name: fact for name, fact in surface.items() if not fact["scopes"]
    }
    assert not undeclared, (
        "every event_store write must declare a lane — "
        "`_graph_metadata_write_scope` for the store's own bookkeeping (schema, "
        "seq allocator, append, retention), or the HANDLE-ONLY helper "
        "(`_handle_only_write_scope(_REPLAY_WRITE_LANE)`, `#9391`) at the write "
        "for the wipe+replay repair (`#9224` for the replay lane, `#9210` for "
        "the restore lane, whose caller-supplied reason keeps "
        "`_cache_write_authorized(restore_reason)`; the helper opens it there "
        "because an inner scope shadows any `with` at the call site). "
        "Undeclared:\n  " + "\n  ".join(
            f"{name}:{fact['lineno']} {fact['writes'][0][1]}: "
            f"{fact['writes'][0][2]!r}" for name, fact in undeclared.items()))
    # The store is small enough to pin exactly: a NEW write here must be a
    # deliberate declaration, never an accident.
    assert set(surface) == {
        "ensure_event_schema", "next_seq", "reestablish_watermark",
        "append_event", "purge_expired", "purge_overflow", "_refresh_first_seq",
    }, f"unexpected event_store handle surface: {sorted(surface)}"


def test_the_handle_only_opener_is_visible_to_the_census():
    """`#9391`'s helper is an OPENER, so the census must SEE it.

    `_handle_only_write_scope` is the helper both `replay` declaration sites
    open: `_replay_write_scope()` returns it, and
    `event_store.reestablish_watermark` opens it directly. Leaving it out of
    `_SCOPE_CALL_NAMES` was a LATENT-RED, not a cosmetic omission: the first
    lane to spell the natural `with _handle_only_write_scope(...):` form would
    have been reported UNDECLARED on correct product code — the false positive
    the tuple's own docstring exists to prevent — and the flip gate would then
    instruct the next lane to add a lane that is already there.

    RED before the name is registered: the synthetic door below reports
    `scopes == []` (so `_kind` is not `declared`), and the real
    `reestablish_watermark` fact lists only the conditional's ELSE arm.
    """
    assert "_handle_only_write_scope" in _SCOPE_CALL_NAMES, (
        "the census cannot see `#9391`'s opener, so a writer that DOES declare "
        "through it reads as undeclared")
    synthetic = '''
class Door:
    def a_handle_only_door(self, proj, pid):
        with _handle_only_write_scope("replay"):
            proj.g.query("CREATE (n:Point {id:$id})", params={"id": pid})
'''
    fact = enumerate_handle_surface(
        synthetic, "<synthetic-handle-only>")["Door.a_handle_only_door"]
    assert {opener for _line, opener in fact["scopes"]} == {
        "_handle_only_write_scope"}, fact
    assert _kind(fact) == "declared", fact

    surface = enumerate_handle_surface(
        EVENT_STORE_PY.read_text(), str(EVENT_STORE_PY))
    openers = {opener for _line, opener
               in surface["reestablish_watermark"]["scopes"]}
    assert "_handle_only_write_scope" in openers, (
        "`reestablish_watermark` opens the handle-only helper for its `replay` "
        f"default (`#9391`), but the census sees only {sorted(openers)} — so it "
        "attributes the ELSE arm's lane to the default path's write")


def test_the_prewipe_restore_funnel_is_declared_not_a_remainder():
    """`#9210`'s funnel DECLARES a lane, so the census must not call it undeclared.

    `FalkorProjection._restore_write` reaches the handle inside
    `_prewipe_restore_write_scope(reason)`. The census recognises a lane by the
    OPENER NAME it can see, so an opener missing from `_SCOPE_CALL_NAMES`
    reported a writer that DOES declare as undeclared — an instrument that
    cannot see its own declaration, and its message would then instruct the
    next lane to declare a lane that is already there. It was not red while the
    guard was measuring (`_HANDLE_GUARD_ENFORCES is False`), but it sat in the
    `provable` remainder that the owner's last-step flip
    (`test_arming_the_guard_requires_a_fully_declared_surface`) requires to be
    cleared, so the flip would have been blocked by a false positive.

    RED before the opener is registered: `scopes == []`, the write is outside
    every span, and `_writes_are_inside_a_lane` is False — on correct product
    code.
    """
    surface = enumerate_handle_surface(
        (ROOT / "tortoise" / "projection" / "__init__.py").read_text(),
        "tortoise/projection/__init__.py")
    fact = surface["FalkorProjection._restore_write"]
    openers = {opener for _lineno, opener in fact["scopes"]}
    assert openers == {"_prewipe_restore_write_scope"}, (
        "the restore funnel's write must be seen inside its own opener — an "
        "opener the census does not know makes a declared writer look "
        f"undeclared: scopes={fact['scopes']!r}")
    assert _writes_are_inside_a_lane(fact), (
        "the census placed the restore funnel's write OUTSIDE every lane "
        f"span: writes={fact['writes']!r} spans={fact['scope_spans']!r}")


# ── non-vacuity: the detector fires on a synthetic undeclared door ────────

def test_the_enumerator_flags_an_undeclared_journal_first_door():
    """The gate's own RED test — the enumerator must SEE an undeclared write.

    Without this, a refactor that broke the enumerator (a renamed attribute, an
    alias it stopped following) would turn the gate into a green rubber stamp.
    The synthetic module carries the same shape as the real doors: a journal
    append, then a mutating handle call, and (in the second case) no declaration.
    """
    undeclared_src = '''
def _emit_event(*a, **k):
    return True


class Door:
    def a_door(self, proj, pid):
        self._emit_event("PointAdded", {"id": pid})
        proj.g.query("CREATE (n:Point {id:$id})", params={"id": pid})
'''
    surface = enumerate_handle_surface(undeclared_src, "<synthetic-undeclared>")
    fact = surface["Door.a_door"]  # scope-qualified key (#9124 review, r2)
    assert _kind(fact) == "journal-first", fact
    assert fact["appends"] and fact["writes"], fact
    assert not fact["scopes"]
    assert _inventory_of("a_door") is None, (
        "the synthetic door must NOT accidentally match a real inventory entry")

    declared_src = undeclared_src.replace(
        "        self._emit_event(\"PointAdded\", {\"id\": pid})\n"
        "        proj.g.query",
        "        self._emit_event(\"PointAdded\", {\"id\": pid})\n"
        "        with _live_write_scope(\"a_door\"):\n"
        "            proj.g.query")
    fact = enumerate_handle_surface(declared_src, "<synthetic-declared>")[
        "Door.a_door"]
    assert _kind(fact) == "declared", fact
    assert fact["scopes"] and fact["scopes"][0][1] == "_live_write_scope"


def test_the_enumerator_resolves_every_shape_that_reaches_the_handle():
    """The receivers and statements a placeholder-based scan would have missed.

    Each shape here was a REAL gap in an earlier iteration of this enumerator
    (#9124 review round 1): an f-string whose interpolated NAME carries the
    write clause read as a non-write and the function vanished from the census;
    so did a handle bound to a local (`h = proj.g`), bound by `getattr`, bound
    through a tuple target, and reached through an attribute chain. The gate is
    the deliverable, so every one of them is pinned by a synthetic module.
    """
    src = '''
DETACH = "DETACH DELETE n"


def _emit_event(*a, **k):
    return True


class Door:
    def interpolated_write(self, proj, pid, action):
        proj.g.query(f"MATCH (n:Point {{id:$id}}) {action}")

    def module_const_write(self, proj):
        proj.g.query(f"MATCH (n:Point) {DETACH}")

    def getattr_alias_write(self, graph):
        raw = getattr(graph, "g", None)
        raw.query("CREATE (n:Point)")

    def tuple_alias_write(self, proj):
        h, _ignored = proj.g, 1
        h.query("CREATE (n:Point)")

    def chained_write(self, self_proj):
        self_proj.g.query("CREATE (n:Point)")

    def parameter_write(self, g):
        g.query("CREATE (n:Point)")

    def walrus_write(self, proj):
        if (h := proj.g):
            h.query("CREATE (n:Point)")

    def with_write(self, proj):
        with closing(proj.g) as h:
            h.query("CREATE (n:Point)")

    def for_write(self, proj):
        for h in [proj.g]:
            h.query("CREATE (n:Point)")

    def ifexp_write(self, proj, cond):
        h = proj.g if cond else None
        if h is not None:
            h.query("CREATE (n:Point)")
'''
    surface = enumerate_handle_surface(src, "<synthetic-shapes>")
    missing = sorted({
        "interpolated_write", "module_const_write", "getattr_alias_write",
        "tuple_alias_write", "chained_write", "parameter_write",
        "walrus_write", "with_write", "for_write", "ifexp_write",
    } - {_bare_name(name) for name in surface})
    assert not missing, (
        "the enumerator lost these shapes — each one is a mutating statement "
        f"through the write handle, so the census would under-report: {missing}")
    for name in sorted(surface):
        assert _kind(surface[name]) == "no-journal", (name, surface[name])


def test_the_ordering_pin_catches_a_conditional_append_with_a_later_write():
    """The shape that survives a first-write-only depth check (#9124 review 2).

    `if cond: append(); write(A)` followed by an UNCONDITIONAL `write(B)` used to
    read as journal-first, because only the FIRST write's depth was compared —
    while in fact `write(B)` runs with no record whenever `cond` is false.
    """
    src = '''
def _emit_event(*a, **k):
    return True


class Door:
    def half_conditional(self, proj, cond):
        if cond:
            self._emit_event("X")
            proj.g.query("CREATE (n:A)")
        proj.g.query("CREATE (n:B)")
'''
    fact = enumerate_handle_surface(src, "<synthetic-half-conditional>")[
        "Door.half_conditional"]
    assert min(w[3] for w in fact["writes"]) == 0, fact
    assert _journal_first_violation(fact), fact
    assert _kind(fact) == "apply-first", fact


def test_the_enumerator_sees_an_apply_first_door_as_apply_first():
    """The other half of the classification: order is measured, not assumed.

    `create_direct_edge` is the real-world shape (MERGE the edge, then emit
    `DirectEdgeCreated`), and the census must call that apply-first — if it
    reported journal-first, every declaration made from it would be dishonest.
    """
    src = '''
def _emit_event(*a, **k):
    return True


class Door:
    def apply_first(self, proj, src_id, tgt_id):
        proj.g.query("MERGE (a)-[r:IMPL]->(b)")
        self._emit_event("DirectEdgeCreated", id="a->b")
'''
    fact = enumerate_handle_surface(src, "<synthetic-apply-first>")[
        "Door.apply_first"]
    assert _kind(fact) == "apply-first", fact


def test_the_census_does_not_collapse_two_same_named_writers():
    """The bare-name collision, pinned as a synthetic RED (#9124 review, r2).

    Two classes, one method name: `Foo.door` declares a lane, `Bar.door` does
    not. Under the pre-fix bare-name keying both facts merged into ONE, and the
    merged `scopes` came from `Foo.door` — so `Bar.door` read `declared`, and the
    flip gate's duplicate-def assertion could not fire either (it sits after the
    `accounted` skip). An UNDECLARED writer passed the census. The keys are
    scope-qualified now, so each def is classified on its own.
    """
    src = '''
def _emit_event(*a, **k):
    return True


class Foo:
    def door(self, proj, pid):
        self._emit_event("PointAdded", {"id": pid})
        with _live_write_scope("foo"):
            proj.g.query("CREATE (n:Point {id:$id})", params={"id": pid})


class Bar:
    def door(self, proj, pid):
        self._emit_event("PointAdded", {"id": pid})
        proj.g.query("CREATE (n:Point {id:$id})", params={"id": pid})
'''
    surface = enumerate_handle_surface(src, "<synthetic-collision>")
    assert {"Foo.door", "Bar.door"} == set(surface), sorted(surface)
    assert _kind(surface["Foo.door"]) == "declared", surface["Foo.door"]
    assert _kind(surface["Bar.door"]) == "journal-first", surface["Bar.door"]
    assert not surface["Bar.door"]["scopes"], (
        "the undeclared twin inherited a scope from its same-named sibling — "
        "that is the collapse the scope-qualified key removes")
    assert surface["Bar.door"]["writes"], surface["Bar.door"]
    # Both twins carry `duplicate_writer_defs` (`_duplicate_writer_offenders`
    # refuses a name-level verdict on them); what THIS test pins is that the
    # per-def CLASSIFICATION is still computed rather than merged.
    assert all(fact["duplicate_writer_defs"] for fact in surface.values()), surface


def test_two_writing_definitions_of_one_name_are_flagged():
    """The duplicate-definitions flag has a PRODUCER, and it can RED (#9124 r5).

    Round 4 rewrote the surface to be scope-qualified and dropped the producer
    of `duplicate_writer_defs` while keeping four gates that read it — so all
    four were tautologies: `fact.get(...)` was `None` for every entry, and the
    `sorted(fact['def_linenos'])` message was unreachable. Nothing could redden
    the hazard the flag exists for: one bare name (what every inventory is
    keyed on) carrying TWO writers, with one row accounting for both.

    Non-vacuity: with the flag never set, the `is True` assertions and the two
    offenders fail. The second half is the GREEN control — same name, one
    writing def — where the residue is nothing to refuse.
    """
    src_two_writers = '''
class Stub:
    def door(self, proj, pid):
        proj.g.query("CREATE (n:Point {id:$id})", params={"id": pid})


class Impl:
    def door(self, proj, pid):
        proj.g.query("MERGE (n:Point {id:$id})", params={"id": pid})
'''
    surface = enumerate_handle_surface(src_two_writers, "<synthetic-dupes>")
    assert set(surface) == {"Stub.door", "Impl.door"}, sorted(surface)
    def_linenos = sorted(fact["lineno"] for fact in surface.values())
    for key, fact in surface.items():
        assert fact["duplicate_writer_defs"] is True, (key, fact)
        assert fact["def_linenos"] == def_linenos, (key, fact)
    offenders = _duplicate_writer_offenders(surface, "<synthetic>::")
    assert len(offenders) == 2, offenders
    assert all(o.startswith("<synthetic>::") and "BOTH definitions write" in o
               for o in offenders), offenders
    assert all(f"defs at {def_linenos}" in o for o in offenders), (
        "the offender message must name BOTH definitions' lines — round 4's "
        "dead branch carried `sorted(fact['def_linenos'])` and could never "
        f"print it: {offenders}")

    src_one_writer = '''
class Stub:
    def door(self, proj, pid):
        proj.g.query("MATCH (n:Point {id:$id}) RETURN n", params={"id": pid})


class Impl:
    def door(self, proj, pid):
        proj.g.query("CREATE (n:Point {id:$id})", params={"id": pid})
'''
    surface = enumerate_handle_surface(src_one_writer, "<synthetic-dupes>")
    # The read-only twin never reaches the surface: only ONE definition of the
    # name writes, so the name-level verdict is unambiguous.
    assert set(surface) == {"Impl.door"}, sorted(surface)
    assert surface["Impl.door"]["duplicate_writer_defs"] is False, surface
    assert surface["Impl.door"]["def_linenos"] == [
        surface["Impl.door"]["lineno"]], surface
    assert _duplicate_writer_offenders(surface) == []


def test_a_duplicate_writer_definition_reds_the_gates(monkeypatch):
    """A flagged surface REFUSES at a real gate — the flag is on that path.

    Round 4's four gates read a key nothing produced, so no surface could make
    them fail. This poisons the enumerator for one gate
    (`test_the_event_store_declares_every_writer`) so the flag→gate path is
    exercised: the poisoned fact HAS the flag, so the gate must refuse with the
    duplicate-definitions message. Delete that refusal from the gate and this
    test fails — the fact then reads as an undeclared writer instead, and the
    `match=` no longer finds the refusal. (Production of the flag itself is
    `test_two_writing_definitions_of_one_name_are_flagged`, which reds when the
    producer is removed; this test would not.)
    """
    fake = {"Store.door": {
        "lineno": 10, "writes": [(11, "query", "CREATE (n:Point)", 0)],
        "reads": 0, "appends": [], "scopes": [], "aliases": set(),
        "params": set(), "duplicate_writer_defs": True,
        "def_linenos": [10, 30],
    }}
    monkeypatch.setattr(sys.modules[__name__], "enumerate_handle_surface",
                        lambda *a, **k: fake)
    with pytest.raises(AssertionError, match="BOTH definitions write") as raised:
        test_the_event_store_declares_every_writer()
    assert "[10, 30]" in str(raised.value), str(raised.value)


# ── the flip gate: arming requires a fully declared surface ───────────────

#: Entry points whose lane is opened by their ONLY CALLER, so a declaration in
#: the body would be redundant and misleading — `rebuild_all` wraps
#: `_rebuild_all_replay` in `_replay_write_scope`, `_ensure_indexes` wraps
#: `_ensure_indexes_declared` in `_graph_metadata_write_scope`, and
#: `rebuild_from_record` wraps `_project_record_view` in `_replay_write_scope`.
#: They are listed here rather than left in the flip gate's remainder, where part
#: 2 would read "declare a lane below" and add exactly that redundant scope.
#:
#: ⛔ PAIRS ARE `(module, SCOPE-QUALIFIED function)` (#9124 review, round 2). A
#: bare name would exempt EVERY same-named function in that module — the
#: collision the census keys away — and all three are methods of
#: `FalkorProjection`, whose module defines plenty of other helpers.
_CALLER_DECLARED = {
    ("tortoise/projection/__init__.py", "FalkorProjection._rebuild_all_replay"),
    ("tortoise/projection/__init__.py", "FalkorProjection._ensure_indexes_declared"),
    ("tortoise/projection/__init__.py", "FalkorProjection._project_record_view"),
}


def _modules_reaching_the_handle():
    r"""Every package module that issues a mutating handle verb, resolved from the AST.

    A source-text probe (`\.g\.(verb)\(`) is NOT this population: it misses every
    module that reaches the handle through a local binding — `raw =
    getattr(graph, "g"); raw.query(...)` (`tortoise/onboarding/state.py`,
    `seed.py`), `g = proj.g` (`weights.py`, `why.py`, `fallback_snapshot.py`).
    Measured when this was regex-based: the scan saw 24 modules and 154
    undeclared entry points, and `onboarding/state.py` — the #9121 write funnel,
    which DOES declare its lane — was not among them. Selection goes through the
    SAME enumerator the census uses, so population and census cannot disagree.
    A module that will not PARSE is collected, never skipped: a silent skip
    shrinks the population, and a shrunken population is how the flip gate goes
    vacuously green. Returns ``[(relative path, surface)]`` so the gate consumes
    the parse this function already paid for.
    """
    out, unparsable = [], []
    for path in sorted((ROOT / "tortoise").rglob("*.py")):
        source = path.read_text(errors="replace")
        try:
            surface = enumerate_handle_surface(source, str(path))
        except SyntaxError:
            unparsable.append(path.relative_to(ROOT).as_posix())
            continue
        if surface:
            out.append((path.relative_to(ROOT).as_posix(), surface))
    assert not unparsable, (
        "these package modules do not parse, so the population the flip gate "
        f"scans is not the whole tree: {unparsable}")
    assert out, (
        "the package-wide scan found NO module with a mutating handle call — the "
        "enumerator or the tree walk is broken, and an empty population would "
        "let the flip gate pass vacuously")
    return out


#: Package-wide `(module, SCOPE-QUALIFIED key)` pairs whose statement the census cannot
#: resolve but a human has PROVEN read-only (it is a `MATCH … RETURN` whose
#: interpolated fragment carries no write clause). Empty in part 1; it carries the
#: two `#9261` calibration READ statements today (`#9063`'s universe query and the
#: audit that reuses it — see the entry's own note).
#:
#: ⚠️ THIS REGISTER IS THE #9124 ARMING GATE'S ALLOWLIST, so an entry written
#: here pre-clears the surface `test_arming_the_guard_requires_a_fully_declared_surface`
#: requires before `_HANDLE_GUARD_ENFORCES` may flip. The two entries landed in
#: #9218's branch because `origin/main` was RED without them (that gate's
#: `test_every_mutating_sdk_entry_point_is_declared_or_inventoried` reported both
#: `#9261` functions as "NO declared lane and no inventory entry"); they are
#: `#9261`'s verdicts, named in this PR's description rather than inherited
#: silently. And the register is PER FUNCTION: an entry proves the STATEMENTS
#: the census saw in that function today, so a WRITE added inside
#: `calibrate_summary` tomorrow is exempted by this entry — a reason to keep the
#: entries verified against the function's real statements, not a reason to
#: widen them. That verification is a MECHANISM, not an intention: each entry pins
#: the statements it was proven to issue in `_PROVEN_READ_STATEMENTS` below, and
#: `test_every_proven_read_names_a_real_census_entry` refuses an entry whose
#: function reaches the handle with a different set — so a second statement added
#: to a registered name cannot inherit the old verdict.
#:
#: Bucketing is PER FUNCTION, so the statements the census cannot resolve are
#: accounted for in four places: the "unproven" bucket (a function whose every
#: mutating call is unresolved), functions that already declare a lane (no
#: bucket), functions that also hold a provable write (listed as a missing lane,
#: not as unproven — the per-function granularity is the limit of a source-order
#: census), and this register. Filling one in is a reviewed act — and it is
#: never the right remedy to wrap a read in a write lane.
#:
#: THE FIGURES BELOW ARE A READING, NOT A STATE — re-derive them (this file's own
#: `_modules_reaching_the_handle()` + bucketing) before quoting them, because they
#: went stale once already: the previous snapshot said "308 unresolved … 226 / 44
#: / 38" and #9218's review found it beside an edit that had moved two statements
#: out of the unproven bucket. MEASURED 2026-10-20 over 59 modules: 307 unresolved
#: statements in the population — 226 in the unproven bucket, 40 in functions
#: that already declare a lane (no bucket), 39 in a function that also holds a
#: provable write, and the 2 proven reads above.
#:
#: ⛔ THE KEY IS WHAT `enumerate_handle_surface` EMITS — scope-qualified, e.g.
#: `("tortoise/sdk.py", "TortoiseSDK.delete_point")`, NOT `"delete_point"`. A
#: bare-name entry is a SILENT NO-OP: accepted into the set, it changes nothing,
#: and the gate keeps blocking the statement while its author believes it was
#: proven read-only. `test_every_proven_read_names_a_real_census_entry` pins it.
_PROVEN_READS: set[tuple[str, str]] = {
    # #9261 (#9063): the calibration gate's demand universe, and the audit that
    # reuses it. Both statements are `MATCH … RETURN` READS, but the census
    # cannot resolve them statically — `_uncalibrated_ep_participants` passes a
    # `query` VARIABLE it composes per label (`name` argument, not a literal),
    # and `calibrate_summary` interpolates a `where` fragment built above the
    # call. Proven read-only in review: neither statement carries a
    # SET/CREATE/DELETE/MERGE arm, and `proj.g.query` is a pure read of the
    # graph here — declaring a lane would be a WRITE declaration on a read,
    # which the register's own note calls the wrong remedy.
    ("tortoise/sdk.py", "TortoiseSDK.calibrate_summary"),
    ("tortoise/sdk.py", "TortoiseSDK._uncalibrated_ep_participants"),
}

#: WHAT WAS PROVEN, not only whose statement was — keyed like `_PROVEN_READS`,
#: holding the census's call-kind names for that function's unresolved statements,
#: IN ORDER.
#:
#: ⛔ WHY THE REGISTER ALONE IS NOT ENOUGH. Both gates `continue` on a registered
#: name BEFORE classifying what the function now does, so a POSITIVE verdict
#: pinned to a NAME blesses every later edit of that name: a second statement
#: added to `calibrate_summary` tomorrow would inherit today's verdict and pass
#: both static gates silently (a *provable* write is still caught —
#: `_is_unresolved_only` fails — but a second UNRESOLVED one is not). The runtime
#: door classifies the real text, so it is not a fail-open on writes; it is a
#: static gate that stops looking. Pin the statements, so the verdict has an
#: extent.
_PROVEN_READ_STATEMENTS: dict[tuple[str, str], tuple[str, ...]] = {
    # one `g.query`, resolving to `MATCH (n:Point) {where} … RETURN …`
    ("tortoise/sdk.py", "TortoiseSDK.calibrate_summary"): ("query",),
    # one `g.query`, resolving to `MATCH (n:{label}) WHERE … RETURN n.id [LIMIT n]`
    ("tortoise/sdk.py", "TortoiseSDK._uncalibrated_ep_participants"): ("query",),
}


def test_every_proven_read_names_a_real_census_entry():
    """A `_PROVEN_READS` entry must be a key the census actually emits.

    Both consumers index the register with the SCOPE-QUALIFIED key, so an entry
    in any other form would sit in the set and change nothing — the "stale flag
    that is never revalidated" failure this suite exists to prevent. It held no
    entries in part 1; the `#9261` calibration reads are the first, and each one's
    key is proved here.

    The VERDICT'S EXTENT is proved too: `_PROVEN_READ_STATEMENTS` must name the
    exact call kinds of that function's unresolved statements, in order. A
    positive verdict pinned to a NAME would otherwise bless a second statement
    added to the same function later — the register is consulted before the
    census classifies anything, so the new statement would inherit the old
    verdict and pass both static gates silently.
    """
    surfaces = dict(_modules_reaching_the_handle())
    offenders = []
    for rel, key in sorted(_PROVEN_READS):
        fact = surfaces.get(rel, {}).get(key)
        if fact is None:
            offenders.append(f"{rel}::{key} names no census entry")
        elif fact["scopes"] or not _is_unresolved_only(fact):
            # A PROVABLE write blessed as a "proven read" would be skipped by
            # both gates — the verdict matters as much as the key form.
            offenders.append(
                f"{rel}::{key} is not an UNPROVEN, undeclared statement "
                f"(writes={fact['writes'][:2]}, scopes={fact['scopes']})")
    assert not offenders, (
        "these `_PROVEN_READS` entries name no census entry (wrong key form? the "
        "census key is scope-qualified — `TortoiseSDK.delete_point`, not "
        "`delete_point`) — the pin would be a silent no-op:\n  "
        + "\n  ".join(offenders))

    # ── the extent: every registered name pins WHAT it was proven to do ──
    unmatched = sorted(set(_PROVEN_READS) ^ set(_PROVEN_READ_STATEMENTS))
    assert not unmatched, (
        "`_PROVEN_READS` and `_PROVEN_READ_STATEMENTS` disagree about "
        f"{unmatched} — every proven read must pin its statements, or its verdict "
        "covers whatever that function grows into next")
    drift = []
    for (rel, key), expected in sorted(_PROVEN_READ_STATEMENTS.items()):
        fact = surfaces[rel][key]
        got = tuple(write[1] for write in fact["writes"])
        if got != expected:
            drift.append(
                f"{rel}::{key} now issues {got} (lines "
                f"{[w[0] for w in fact['writes']]}) but was proven as {expected}")
    assert not drift, (
        "a registered read-only function changed what it reaches the handle with, "
        "so the read-only verdict no longer covers it — re-prove the statements "
        "(and update `_PROVEN_READ_STATEMENTS`) or drop the entry so the census "
        "classifies it again:\n  " + "\n  ".join(drift))


def _is_unresolved_only(fact) -> bool:
    """Is every mutating call in this function an UNRESOLVED statement?"""
    return all(write[2] == "<unresolved>" for write in fact["writes"])


def test_arming_the_guard_requires_a_fully_declared_surface():
    """`_HANDLE_GUARD_ENFORCES = True` is only allowed once nothing is left.

    This is the owner's decision made executable (#9124, option B): the guard is
    flipped LAST. The scan is package-wide — `sdk.py` and `event_store.py` are
    what part 1 declared, but `hosted_api.py`, `mining.py`, `ingest.py`, the
    connectors and the rest reach the same handle, and arming while any of them
    is undeclared refuses the product (measured: 16 failed / 4 errors across
    four §17.1 suites).

    THE REMAINDER HAS TWO KINDS, and the remedy differs, so the message says
    which is which. A function whose statement the census could PROVE mutating
    needs a declared lane (or an inventory entry). A function whose statement it
    could NOT resolve needs a HUMAN verdict — prove it read-only (and pin it in
    `_PROVEN_READS`, the ONE register both tests consult) or declare a lane if it
    writes. The runtime door classifies the REAL text, so a static "unresolved"
    is not itself a refusal risk; but it is not a non-risk either, which is why
    an unproven entry blocks the flip rather than being assumed read-only.
    Telling a reader to wrap a read in a write lane is how a redundant,
    misleading scope gets added (#9124 review P2), so an unproven entry is
    reported as unproven, never as a missing lane.

    The VACUITY FLOOR runs in BOTH modes, before either branch: the population
    must be non-empty and must contain every inventoried SDK entry point —
    otherwise a broken enumerator would make the armed check pass over an empty
    set, which is the rubber stamp this whole suite exists to prevent.
    """
    population = _modules_reaching_the_handle()
    provable: dict[str, list[str]] = {}
    unproven: dict[str, list[str]] = {}
    seen_sdk: set[str] = set()
    duplicate_offenders: list[str] = []
    for rel, surface in population:
        # BEFORE any accounting: a name whose DEFINITIONS both write merges one
        # def's lane with another def's write, so its verdict is unsound
        # whatever else it looks like — including looking declared.
        duplicate_offenders.extend(_duplicate_writer_offenders(surface, f"{rel}::"))
        for name, fact in sorted(surface.items()):
            accounted = _writes_are_inside_a_lane(fact) \
                or (rel, name) in _CALLER_DECLARED
            proven_read = (rel, name) in _PROVEN_READS
            if rel == "tortoise/sdk.py":
                seen_sdk.add(_bare_name(name))
            if proven_read or accounted:
                continue
            bucket = unproven if _is_unresolved_only(fact) else provable
            bucket.setdefault(rel, []).append(f"{name}:{fact['lineno']}")

    assert not duplicate_offenders, (
        "a name is defined more than once and BOTH definitions write the handle, "
        "so its name-level verdict is unsound whatever else it looks like — "
        "including looking declared:\n  " + "\n  ".join(duplicate_offenders))

    # THE FLOOR — proven REAL in both modes, on the same population the armed
    # check uses.
    assert len(population) > 10, (
        f"the package-wide scan found only {len(population)} module(s) with a "
        "mutating handle call; a population that small means the enumerator or "
        "the tree walk is broken, and the armed check would pass vacuously")
    missing = sorted(_all_inventoried() - seen_sdk)
    assert not missing, (
        "the package scan did not see every inventoried SDK entry point — the "
        "scan itself is broken, so the arming gate would pass vacuously. "
        f"missing={missing}")

    if _HANDLE_GUARD_ENFORCES:
        blocked = []
        for rel, names in sorted(provable.items()):
            blocked.append(f"  {rel}: {len(names)} UNDECLARED writer(s), e.g. "
                           f"{names[:4]} — declare a lane, or inventory it as a "
                           "part-2 remainder")
        for rel, names in sorted(unproven.items()):
            blocked.append(f"  {rel}: {len(names)} UNPROVEN statement(s), e.g. "
                           f"{names[:4]} — the census cannot resolve these; "
                           "prove each read-only (pin it in `_PROVEN_READS`) or "
                           "declare a lane if it writes. Do NOT wrap a read in "
                           "a write lane")
        assert not blocked, (
            "`_HANDLE_GUARD_ENFORCES` is True while the write surface is NOT "
            "fully declared — arming here refuses the product's own writes. "
            "A helper whose only caller declares belongs in `_CALLER_DECLARED`. "
            "Before flipping the switch, clear both lists:\n" + "\n".join(blocked))


def _call_is_inside_a_scope(func, callee) -> bool:
    """Does a call to `callee` RUN inside a declared lane opened by `func`?

    Three ways to be covered, and nothing else: inside the body of a
    `with <scope>(...):` block (sync or async), inside a `@_declares_live_write_lane`
    window (that decorator covers the whole body), or — for a `with` — a nested
    `def`/`lambda` boundary STOPS the search, because a function defined in the
    block does not run in it.
    """
    for decorator in func.decorator_list:
        # decorator form: `@_declares_live_write_lane(...)` covers the whole body
        if isinstance(decorator, ast.Call):
            opener = getattr(decorator.func, "id", None) or getattr(
                decorator.func, "attr", None)
            if opener in _DECLARATION_DECORATORS:
                return True
    for node in _own_body_nodes(func):
        if not isinstance(node, (ast.With, ast.AsyncWith)):
            continue
        if not any(isinstance(item.context_expr, ast.Call)
                   and (getattr(item.context_expr.func, "id", None)
                        or getattr(item.context_expr.func, "attr", None))
                   in _SCOPE_CALL_NAMES for item in node.items):
            continue
        for body_node in node.body:
            if _calls_within(body_node, callee):
                return True
    return False


def _own_body_nodes(func):
    """This function's own statements, NOT crossing into a nested def/lambda.

    A `with` opened inside a NESTED function is that function's lane, not the
    caller's: walking the whole body would credit `def outer: def inner: with
    scope: callee()` to `outer`, which holds no lane at all, and an exemption
    whose callee runs outside any scope would then pass.
    """
    stack = list(ast.iter_child_nodes(func))
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        yield node
        stack.extend(ast.iter_child_nodes(node))


def _calls_within(node, callee) -> bool:
    """A call to `callee` in this subtree, not crossing a nested function boundary."""
    stack = [node]
    while stack:
        current = stack.pop()
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        if isinstance(current, ast.Call):
            target = getattr(current.func, "id", None) or getattr(
                current.func, "attr", None)
            if target == callee:
                return True
        stack.extend(ast.iter_child_nodes(current))
    return False


def test_a_lane_opened_by_a_nested_function_does_not_cover_the_caller():
    """The exemption proof is CONTAINMENT, and a nested def's lane is not ours.

    `_call_is_inside_a_scope` walks `_own_body_nodes`, which stops at every
    `def`/`lambda`: `def outer: def inner: with scope: callee()` must NOT credit
    `outer`. Without this pin the pre-fix `ast.walk(func)` restores the bug with
    the suite green (the class this file keeps closing: a guard with no producer).
    """
    src = '''
class C:
    def nested(self):
        def inner():
            with _live_write_scope("inner"):
                self._callee()
        return inner

    def proper(self):
        with _replay_write_scope():
            return self._callee()

    def exits_first(self):
        with _replay_write_scope():
            pass
        return self._callee()

    @_declares_live_write_lane("whole body")
    def decorated(self):
        return self._callee()
'''
    fns = {node.name: node for node in ast.walk(ast.parse(src))
           if isinstance(node, ast.FunctionDef)}
    assert _call_is_inside_a_scope(fns["nested"], "_callee") is False
    assert _call_is_inside_a_scope(fns["proper"], "_callee") is True
    assert _call_is_inside_a_scope(fns["exits_first"], "_callee") is False
    assert _call_is_inside_a_scope(fns["decorated"], "_callee") is True


def test_a_proven_read_entry_must_be_unproven_and_undeclared(monkeypatch):
    """The register pins the VERDICT, not just the key form.

    A provable write blessed as a "proven read" would be skipped by BOTH gates,
    so an entry in the right key form but the wrong shape must be refused. The
    third clause pins the verdict's EXTENT: a registered name whose statement set
    changed (or that pins no extent at all) is refused, because both gates consult
    the register BEFORE classifying anything, so the old verdict would otherwise
    cover whatever the function grew into.
    """
    # Captured BEFORE any patch: the real register the extent clauses probe.
    real_register = set(_PROVEN_READS)
    real_statements = dict(_PROVEN_READ_STATEMENTS)
    surfaces = dict(_modules_reaching_the_handle())
    sdk = surfaces["tortoise/sdk.py"]
    # A REAL key, so the entry reaches the VERDICT clause rather than the
    # "no such entry" clause: a DECLARED door is a provable write, not an
    # unproven read.
    declared = sorted(key for key, fact in sdk.items() if fact["scopes"])
    assert declared, "the census found no declared SDK door — the probe is stale"
    monkeypatch.setattr("test_9124_declared_live_doors._PROVEN_READS",
                        {("tortoise/sdk.py", declared[0])})
    with pytest.raises(AssertionError, match="is not an UNPROVEN, undeclared"):
        test_every_proven_read_names_a_real_census_entry()
    # ...and the FORM clause still refuses a bare name.
    monkeypatch.setattr(
        "test_9124_declared_live_doors._PROVEN_READS",
        {("tortoise/sdk.py", declared[0].split(".")[-1])})
    with pytest.raises(AssertionError, match="names no census entry"):
        test_every_proven_read_names_a_real_census_entry()

    # ── the EXTENT clauses, on the REAL register (non-empty since #9261) ──
    assert real_register, (
        "`_PROVEN_READS` is empty, so the extent probe below would pass "
        "vacuously — it is only meaningful while a verdict spans statements")
    monkeypatch.setattr("test_9124_declared_live_doors._PROVEN_READS", real_register)
    grown = dict(real_statements)
    first = sorted(grown)[0]
    # A SECOND statement appeared in a function blessed as read-only: the verdict
    # no longer covers it, and the pin must say so.
    grown[first] = grown[first] + ("query",)
    monkeypatch.setattr("test_9124_declared_live_doors._PROVEN_READ_STATEMENTS", grown)
    with pytest.raises(AssertionError, match="read-only verdict no longer covers"):
        test_every_proven_read_names_a_real_census_entry()
    # A registered name with NO pinned extent is refused the other way round.
    monkeypatch.setattr(
        "test_9124_declared_live_doors._PROVEN_READ_STATEMENTS",
        {key: value for key, value in real_statements.items() if key != first})
    with pytest.raises(AssertionError, match="every proven read must pin its statements"):
        test_every_proven_read_names_a_real_census_entry()


def test_the_append_exclusions_append_nothing():
    """`_APPEND_NOT_A_RECORD` may only hold names that append NO record.

    The pattern that recognises an append (`_journal_*`, `_emit_*`) is a
    heuristic over NAMES, and a helper like `_journal_instant` reads a timestamp.
    Counting one of those as the commit point would let
    `_journal_first_violation` pass on a door whose record never landed, so each
    exclusion is PROVEN here: the definition is found in the package and its body
    calls no append helper. An exclusion that stops existing fails too.
    """
    defs: dict[str, list[ast.AST]] = {}
    for path in sorted((ROOT / "tortoise").rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(errors="replace"))
        except SyntaxError:  # pragma: no cover — population test reports those
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                defs.setdefault(node.name, []).append(node)

    offenders = []
    for name in sorted(_APPEND_NOT_A_RECORD):
        bodies = defs.get(name)
        if not bodies:
            offenders.append(f"{name} is excluded but no longer defined anywhere")
            continue
        for body in bodies:
            for call in ast.walk(body):
                if not isinstance(call, ast.Call):
                    continue
                target = getattr(call.func, "id", None) or getattr(
                    call.func, "attr", None)
                if target and _APPEND_CALL_RE.match(target) \
                        and target not in _APPEND_NOT_A_RECORD:
                    offenders.append(
                        f"{name} (definition at line {body.lineno}) CALLS "
                        f"{target} — it appends a record, so excluding it would "
                        "hide a real commit point")
                    break
    assert not offenders, "\n  " + "\n  ".join(offenders)




def test_the_append_exclusions_cannot_buy_a_door_a_lane():
    """Non-vacuity for the exclusion above: it changes the VERDICT, nothing else.

    The same synthetic door reads `apply-first` when the call after its write is
    an ordinary `_journal_*` helper and `no-journal` when it is the excluded
    warning builder — so the exclusion is the thing doing the work, and the
    classifier is not simply blind to every `_journal_*` name.
    """
    template = '''
class Door:
    def capture(self, proj):
        proj.g.query("MATCH (n:Point) SET n.eventId=$e")
        self.{call}(1)
'''
    allowed = enumerate_handle_surface(
        template.format(call="_journal_write_failure_warning"),
        "<synthetic-warning-builder>")["Door.capture"]
    assert _kind(allowed) == "no-journal", allowed
    # Control: an ordinary append helper in the SAME position is a real commit
    # point, and the door reads apply-first (record AFTER the write).
    real = enumerate_handle_surface(
        template.format(call="_journal_stamp"),
        "<synthetic-real-append>")["Door.capture"]
    assert real["appends"], real
    assert _kind(real) == "apply-first", real


def test_the_caller_declared_exemptions_are_still_caller_declared():
    """`_CALLER_DECLARED` is an assertion, not an amnesty.

    Each entry is credited because its ONLY call site sits inside a function that
    opens one of the lanes. That is a property of the CALL GRAPH, so it is
    checked: delete the wrapper (or add a second, undeclared caller) and this
    test reddens instead of the flip gate silently green-lighting it.
    """
    offenders = []
    for rel, callee in sorted(_CALLER_DECLARED):
        path = ROOT / rel
        tree = ast.parse(path.read_text(errors="replace"))
        bare = _bare_name(callee)
        callers = []
        for func in [n for n in ast.walk(tree)
                     if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
            for call in ast.walk(func):
                if isinstance(call, ast.Call):
                    target = getattr(call.func, "id", None) or getattr(
                        call.func, "attr", None)
                    if target == bare:
                        callers.append(func)
        if not callers:
            offenders.append(f"{rel}::{callee} has no call site at all")
            continue
        for func in callers:
            # CONTAINMENT, not mere presence: the call must sit inside the body
            # of a `with <scope>(...):` — a caller that opens a lane, leaves it,
            # and only then calls the callee would be credited by a
            # "some opener appears in the body" check while the callee's writes
            # run unauthorized.
            if not _call_is_inside_a_scope(func, bare):
                offenders.append(
                    f"{rel}::{callee} is called from {func.name}:{func.lineno}, "
                    "which does not hold a lane around that call — the "
                    "exemption is stale")
    assert not offenders, ("\n  " + "\n  ".join(offenders))


def test_the_measure_mode_census_is_what_a_flip_would_refuse(armed_sdk_factory):
    """The runtime door and the static census agree about the same statement.

    A statement the enumerator calls a write must be one the handle MEASURES in
    measure mode — and, once the switch is on, one it REFUSES. That is the
    shared-classifier property this whole suite rests on (#3595).
    """
    import tortoise.projection as projection
    from tortoise.projection import (
        DirectCacheWriteRefused,
        _reset_undeclared_handle_writes,
        undeclared_handle_writes,
    )

    sdk = armed_sdk_factory()
    proj = sdk._get_proj()
    assert proj._journal_backed
    statement = "CREATE (n:Point {id:'census-9124'})"
    assert _statement_writes(statement)
    _reset_undeclared_handle_writes()
    # measure: no refusal, counted
    proj.g.query(statement)
    census = undeclared_handle_writes()
    assert any("census-9124" in key for key in census), (
        f"the measure-mode census did not record the write: {census!r}")
    # armed: refused, and not counted (a refusal is not a measurement)
    _reset_undeclared_handle_writes()
    projection._HANDLE_GUARD_ENFORCES = True
    try:
        with pytest.raises(DirectCacheWriteRefused):
            proj.g.query("CREATE (n:Point {id:'census-armed-9124'})")
        assert not undeclared_handle_writes()
    finally:
        projection._HANDLE_GUARD_ENFORCES = False


@pytest.fixture
def armed_sdk_factory(tmp_path):
    """An SDK whose projection is journal-backed, so the door is live."""
    from tortoise.sdk import TortoiseSDK

    created = []

    def _make():
        journal = tmp_path / "journal.jsonl"
        sdk = TortoiseSDK(str(tmp_path / "declared-doors.db"),
                          event_log_path=str(journal))
        created.append(sdk)
        return sdk

    try:
        yield _make
    finally:
        for sdk in created:
            sdk.close()
