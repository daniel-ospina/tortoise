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
import io
import re
import sys
import tokenize
from pathlib import Path

import pytest

from tortoise.projection import (
    _HANDLE_GUARD_ENFORCES,
    _HANDLE_MUTATING_VERBS,
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
    r"|append_event)$")

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
}

#: The scope openers, PLUS the decorator that opens one around a whole door.
#: `_onboarding_write_scope` is the onboarding funnel's own opener (`with
#: _onboarding_write_scope(cypher):` around the dispatch in
#: `tortoise/onboarding/state.py`) — a lane declared through a module-local
#: helper is still declared, and leaving it out reported that module as
#: undeclared while it was the opposite.
_SCOPE_CALL_NAMES = (
    "_live_write_scope",
    "_replay_write_scope",
    "_graph_metadata_write_scope",
    "_onboarding_write_scope",
    "_cache_write_authorized",
    "_declares_live_write_lane",
)
#: The subset that claims "this door appends its record, THEN writes the cache".
#: Only these are held to the journal-first ordering pin below: a replay or
#: graph-metadata lane makes no such claim.
_LIVE_LANE_NAMES = ("_live_write_scope", "_declares_live_write_lane")


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
_APPLY_FIRST_DOORS = {
    "approve_merge",         # SET reviewed flags, then PointRevised/… records
    "assess_source",         # SET reliability, then the assessment record
    "compute_confidence",    # SET n.confidence per row, then ConfidenceChanged
    "create_direct_edge",    # MERGE/SET the edge, then DirectEdgeCreated
    "create_operator",       # CREATE the operator node, then OperatorAdded
    # #9207: the `DETACH DELETE` is issued FIRST and the `EntityMutated`
    # op=delete record follows a LANDED destroy, so this door carries no
    # live lane — its #9124 part-1 `@_declares_live_write_lane("delete_point")`
    # was journal-first-only and was WITHDRAWN when the order reversed (the
    # ordering pin below re-derives that from the source). ⚠️ Unlike the rows
    # above, the order here is a DECISION, not a remainder waiting to be
    # converted: #9207 chose cache-first on measured direction (append-first
    # commits a destruction the cache never performed). Honouring it under an
    # ARMED handle needs an "apply-first, by decision" lane — a §17.1 decision
    # (#9124 / #9206), not a re-purposed decorator. Until then its writes are
    # MEASURED as undeclared, and an armed guard REFUSES them
    # (`test_9079_handle_write_authority.py::
    # test_an_apply_first_door_is_refused_by_the_armed_guard`).
    "delete_point",
    "mitigate_operator",     # CREATE the mitigation, then its records
    "promote_point",         # CAS SET status='live', then PointPromoted
    "record_calibration",    # write the calibration row, then its record
    "set_point_baseline",    # SET the baseline, then its record
}

#: Apply-first INTERNAL helpers — the same ordering as the doors above, reached
#: from them (or from capture/ingest). Same part-2 decision, same reason.
_APPLY_FIRST_HELPERS = {
    "_apply_capture_ingest_ep",
    "_apply_source_inheritance",
    "_dedup_content_candidates",   # marks the candidate, then records it
    "_delete_entity",              # DETACH DELETE, then the mutation record
    "_embedding_repair_attempt",
    "_materialize_session_source",  # MERGE(:Source), then the record
    "_promote_incident_operators",
    "_session_event_write",
    "_stamp_batch_id",             # SET n.batch_id, then _emit_batch_id_record
    "_update_entity",              # SET, then _journal_entity_mutation
    "_write_capture_turns",
}

#: Journal-first BY ORDER, but holding an arm whose record is deliberately
#: stated after its guarded write — so the door is part 2's, not part 1's:
#:
#: * `update_point` — the `status='live'` promote arm (a `WHERE`-guarded SET that
#:   can legitimately match nothing and then RAISES, so journalling first would
#:   record a promotion the graph refused; the deviation is pinned by
#:   `tests/test_7951_update_point_outbox.py`). The SAME write region serves the
#:   journal-first arms, so one `with` cannot separate them.
#: * `capture_session` — its `:Session` counter writes record their outcome only
#:   AFTER the write ("the raw SET above has no journal carrier ... emit a
#:   TRAILING SessionRecorded"), which is the same shape.
_MIXED_ARM_DOORS = {
    "capture_session",
    "update_point",
}

#: Reach the handle with a write-like statement and append NO record at all, so
#: there is no lane to declare: each needs either a named graph-metadata lane
#: (the `_advance_ep_version` / `onboarding.metadata` pattern) or a move onto the
#: fold. Measured on #9124; this is the "19 writers that journal nothing" class
#: in the issue's landing path, at the count the source actually holds.
_NO_JOURNAL = {
    "_clear_reliability_cache",
    "_connect_issue_objects",
    "_extract_session_llm",
    "_extract_session_v2",
    "_index_source_merge",
    "_invalidate_inheritance_gate",
    "_mark_dirty",
    "_reapply_operator_promotion",
    "_record_embedding_marker",
    "_register_failed_attempt",
    "_stamp_authored_word",
    "_sweep_dirty_roots",
    "_touch_inherited_at",
    "_write_props",
    "_write_reliability_cache",
    "_write_session_and_turns",
    "backfill_v25",
    "complete_source",
    "ingest",
    "migrate_orgs_to_registry",
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
    "_graph_presence",
    "_hop",
    "_window_closure",
    "expand_relationships",
    # f-string interpolates a computed fragment (`{_label}`, `{relationship_type}`,
    # `{_capture_passthrough_read_fields()}`, …) — the #9124 review's P1 shape,
    # which a placeholder substitution used to flatten into a false READ.
    "_count_ep_factors",
    "_cross_lens_pool",
    "_entity_name_holders",
    "_epistemic_label",
    "_find_operator",
    "_find_point_by_content",
    "_find_points_by_key",
    "_find_terminal_dedup_hit",
    "_read_capture_passthrough_props",
    "_reachable_claim_count",
    "_review_pool",
    "_review_prune",
    "_reverse_bfs_neighbors",
    "_semantic_dedup",
    "_source_reachable_by",
    "_state_related_objects",
    "_state_related_points",
    "_unsweepable_dirty_roots",
    "backfill_about_entities",
    "list_batch",
    "paginated_query",
    "query",
    "search_sessions",
    "suggest_entry_points",
    "tortoise_fts_query",
    "traverse",
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
                        if opener in _LIVE_LANE_NAMES:
                            # A lane DECORATOR covers the whole body.
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
    """Classify one function: declared, or which inventory it belongs to."""
    if fact["scopes"]:
        return "declared" if _writes_are_inside_a_lane(fact) else "outside-lane"
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
        if kind == "outside-lane":
            offenders.append(
                f"{name}:{fact['lineno']} OPENS a lane ({fact['scopes']}) but "
                f"writes outside it at line {fact['writes'][0][0]} — presence is "
                "not containment; move the write inside the lane or declare one "
                "that covers it")
            continue
        if kind == "declared":
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


#: Prose that CLASSIFIES a door by whether it gates its CACHE WRITE on the
#: append's per-call outcome. `supersede_point`'s "ARM BY ARM" passage and
#: `retract_point`'s "Only … on their append's return" one both carry it, and each
#: NAMES the doors that do. That name list is a claim about code, so it is checked
#: against the source instead of trusted. This is the #9207 review's finding:
#: `supersede_point` still grouped `delete_point` with the append-gated doors,
#: citing a local (`_delete_committed`) the change under review had deleted.
#:
#: Phrases are matched against WHITESPACE-NORMALISED, lower-cased text: this file
#: wraps at ~78 columns, so a phrase straddling a line break ("the append's\n
#: outcome") matched nothing when the raw paragraph was searched — measured, two
#: of four phrases hit ZERO lines for that reason alone.
_APPEND_GATE_PHRASES = (
    "per-call append",
    "append outcome",
    "append's return",
    "append's outcome",
    "cache write on",
)

#: An apply-first door may still be NAMED in a gating clause, but the ASSERTION
#: itself must be disowned. POLARITY, not vocabulary: the issue number alone does
#: not exempt, because "since #9207, `delete_point` gates its cache write on the
#: append's return" is the most natural way to mis-state it — a bare-substring
#: marker passed that sentence GREEN, and so did "apply-first only in name: it
#: gates …". So the pattern is the CLAIM ("gates [its cache] write on" /
#: "gates on") and the exemption is a negation within `_NEGATION_WINDOW`
#: characters of it. Measured on this tree: no clause that names an apply-first
#: door matches the claim pattern at all, so the rule has no false positives here.
_ASSERTIVE_GATE = re.compile(
    r"\bgates?\s+(?:its\s+|the\s+)?(?:cache\s+)?write\s+on\b"
    r"|\bgates?\s+on\b"
)
_NEGATION_WINDOW = 45
#: Word-bounded on purpose: a SUBSTRING test let "Note:" clear a claim, because
#: "note" contains "not" — measured, that turned a planted contradiction GREEN.
_LOCAL_NEGATION_RE = re.compile(
    r"\b(?:not|never|no|nothing|cannot)\b|\bn't\b|\bno longer\b|\b(?:does|is|do|are) not\b"
)
#: `_delete_committed` was DELETED by #9207, so citing it is a claim about a
#: symbol a reader cannot find. A HISTORY note is legitimate (the door's own
#: comment records its withdrawn decorator the same way), so the rule is not a
#: blanket ban: the removal construction must be ADJACENT to the symbol, and a
#: live-use word ("still") vetoes it outright — otherwise "the gate was never
#: removed: `_delete_committed` still decides" would qualify as history.
_DELETED_SYMBOL = "_delete_committed"
_DELETED_SYMBOL_REMOVAL = r"(?:no longer|removed|deleted|gone|dropped|used to)"
_DELETED_SYMBOL_HISTORY = re.compile(
    _DELETED_SYMBOL_REMOVAL + r"(?:\W+\w+){0,4}?\W+" + _DELETED_SYMBOL
    + r"|" + _DELETED_SYMBOL + r"(?:\W+\w+){0,3}?\W+" + _DELETED_SYMBOL_REMOVAL
)
_DELETED_SYMBOL_LIVE_USE = ("still",)


def _prose_units(text: str):
    """Yield ``(start_line, text)`` for every COMMENT and STRING token.

    `tokenize`, not a line filter: the claim being checked is PROSE, and a
    blank-line paragraph split cannot tell prose from code. Measured without
    this, a CODE line could be reported as a documentation contradiction —
    `found = self.delete_point(id)` beside a comment containing "append's return"
    matched the door name and failed the gate. A comment may open a docstring's
    heading or a `#:` block, and both are prose here.
    """
    readline = io.StringIO(text).readline
    for token in tokenize.generate_tokens(readline):
        if token.type in (tokenize.COMMENT, tokenize.STRING):
            yield token.start[0], token.string


def _prose_clauses(unit: str):
    """Yield the CLAUSES of one prose unit, whitespace-normalised.

    A gating claim is made by a SENTENCE (or a `;`-joined clause), not by the
    block that holds it. ⚠️ Exemption is CLAUSE-scoped, never unit-scoped: a
    docstring here carries a dozen independent claims, so a marker in one must
    not clear a contradiction in another — round 1 of this guard exempted the
    whole `retract_point` paragraph, which already says "not to gate", so ANY
    later sentence added to it could never fail (the exact defect shape,
    re-introduced one docstring over, green forever).
    """
    for clause in re.split(r"(?<=[.;])\s+", " ".join(unit.split())):
        if clause.strip():
            yield clause


def _append_gating_claims(text: str) -> list[str]:
    """Clauses that ASSERT an apply-first door gates its cache write on an append.

    Two conditions, and the second is the one that mattered: the clause must hold
    the CLAIM (not merely the door's name — a door named in a sentence that says
    its gating does not apply is the correct prose), and the claim must be
    UNNEGATED within `_NEGATION_WINDOW` characters of it. Judged on the assertion
    rather than on the clause's vocabulary, because a clause can carry "#9207"
    and a "never" somewhere else and still assert the inversion.
    """
    claims = []
    for line, unit in _prose_units(text):
        flat = " ".join(unit.split()).lower()
        if not any(phrase in flat for phrase in _APPEND_GATE_PHRASES):
            continue
        for clause in _prose_clauses(unit):
            low = clause.lower()
            doors = sorted(d for d in _APPLY_FIRST_DOORS if re.search(rf"\b{d}\b", low))
            if not doors:
                continue
            for assertion in _ASSERTIVE_GATE.finditer(low):
                nearby = low[max(0, assertion.start() - _NEGATION_WINDOW):
                             assertion.end() + _NEGATION_WINDOW]
                if _LOCAL_NEGATION_RE.search(nearby):
                    continue
                claims.append(
                    f"sdk.py:{line} names {doors} and asserts "
                    f"{assertion.group(0)!r}: {clause!r}")
    return claims


def _unmarked_citations_of_the_deleted_symbol(text: str) -> list[str]:
    """Clauses citing `_delete_committed` that do not record it as REMOVED."""
    offenders = []
    for line, unit in _prose_units(text):
        for clause in _prose_clauses(unit):
            low = clause.lower()
            if _DELETED_SYMBOL not in low:
                continue
            historical = bool(_DELETED_SYMBOL_HISTORY.search(low)) and not any(
                w in low for w in _DELETED_SYMBOL_LIVE_USE)
            if not historical:
                offenders.append(f"sdk.py:{line}: {clause!r}")
    return offenders


def test_the_deleted_symbol_rule_tells_history_from_a_live_citation():
    """The rule above is pinned on SYNTHETIC clauses, so it is non-vacuous.

    `_delete_committed` occurs zero times in `tortoise/sdk.py` today — correct,
    and exactly why the corpus alone cannot demonstrate that the rule WORKS: a
    rule that returned "fine" for everything would also scan a clean tree
    cleanly. These cases are the ones a real regression takes.
    """
    def verdict(clause: str) -> bool:
        low = " ".join(clause.split()).lower()
        return bool(_DELETED_SYMBOL_HISTORY.search(low)) and not any(
            w in low for w in _DELETED_SYMBOL_LIVE_USE)

    assert not verdict(
        "`delete_point` gates on `_delete_committed = _journal_entity_mutation(...)`."), (
        "the PRE-FIX sentence must read as a live citation")
    assert not verdict(
        "# the delete gate was never removed: `_delete_committed` still decides."), (
        "a live-use word must veto an adjacent removal verb")
    assert verdict("note: #9207 removed the `_delete_committed` gate.")
    assert verdict("`_delete_committed` was deleted with the reversal (#9207).")
    assert not verdict("`_delete_committed` is the gate for this door.")


def test_no_prose_claims_an_apply_first_door_gates_its_cache_write():
    """The append-gating taxonomy in the source must agree with its own code.

    `_APPLY_FIRST_DOORS` is apply-first BY CONSTRUCTION: the record that describes
    the write is emitted after it. So a CLAUSE that lists one of them among the
    doors whose CACHE WRITE is gated on the append's return states the inverse of
    the order — the §17.1 defect this whole suite exists to measure, told as
    documentation instead of code.

    Both rules read the source, and both are clause-scoped for the reason above.
    Deliberately NOT a general "every cited local exists" scan: almost every
    backticked name in this file lives in a sibling module, and a guard with
    dozens of legitimate exemptions stops being read.
    """
    text = SDK_PY.read_text()
    units = list(_prose_units(text))
    assert len(units) > 5000, (
        "the token scan found almost no prose — an empty scan would pass every "
        f"assertion below vacuously (found {len(units)} comment/string tokens)")

    taxonomy = [
        line for line, unit in units
        if any(p in " ".join(unit.split()).lower() for p in _APPEND_GATE_PHRASES)
    ]
    assert len(taxonomy) >= 2, (
        "the append-gating taxonomy was not found — this pin has gone asleep. "
        "If the sentences were rewritten, re-point the phrases at the new "
        f"wording (found {len(taxonomy)}: {taxonomy})")

    claims = _append_gating_claims(text)
    assert not claims, (
        "an apply-first door is described as gating its cache write on its "
        "append, which is the inverse of its order (the record follows the "
        "write) — either drop it from the append-gated list or disown the gating "
        "IN THE SAME CLAUSE and say why:\n  " + "\n  ".join(claims))

    stale = _unmarked_citations_of_the_deleted_symbol(text)
    assert not stale, (
        "`_delete_committed` no longer exists in the tree (#9207 replaced the "
        "append-gated cache write with cache-first ordering), so citing it as a "
        "live gate describes a contract this code does not have — mark it as "
        "history or drop it:\n  " + "\n  ".join(stale))
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
    "invalidate_point": {"_mark_dirty"},
    "retract_point": {"_mark_dirty"},
    "supersede_point": {"_mark_dirty"},
}


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
    tree = ast.parse(SDK_PY.read_text(), str(SDK_PY))
    inventoried = (_NO_JOURNAL | _APPLY_FIRST_HELPERS | _APPLY_FIRST_DOORS
                   | _MIXED_ARM_DOORS)
    measured: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name in _DECLARED_DOOR_NESTED_INVENTORIED:
            calls = {
                callee for sub in ast.walk(node) if isinstance(sub, ast.Call)
                for callee in [getattr(sub.func, "attr", None)
                               or getattr(sub.func, "id", None)]
                if callee in inventoried
            }
            measured[node.name] = calls
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
        "seq allocator, append, retention) or `_replay_write_scope` for the "
        "wipe+replay repair. Undeclared:\n  " + "\n  ".join(
            f"{name}:{fact['lineno']} {fact['writes'][0][1]}: "
            f"{fact['writes'][0][2]!r}" for name, fact in undeclared.items()))
    # The store is small enough to pin exactly: a NEW write here must be a
    # deliberate declaration, never an accident.
    assert set(surface) == {
        "ensure_event_schema", "next_seq", "reestablish_watermark",
        "append_event", "purge_expired", "purge_overflow", "_refresh_first_seq",
    }, f"unexpected event_store handle surface: {sorted(surface)}"


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
#: interpolated fragment carries no write clause). Empty in part 1, and the flip
#: is blocked without it. Bucketing is PER FUNCTION, so of the 308 unresolved
#: statements in the population 226 sit in the "unproven" bucket, 44 sit in
#: functions that already declare a lane (no bucket), and 38 ride in a function
#: that also has a provable write (so they are listed as a missing lane, not as
#: unproven — the per-function granularity is the limit of a source-order
#: census). Filling one in is a reviewed act — and it is never the right remedy
#: to wrap a read in a write lane.
#:
#: ⛔ THE KEY IS WHAT `enumerate_handle_surface` EMITS — scope-qualified, e.g.
#: `("tortoise/sdk.py", "TortoiseSDK.delete_point")`, NOT `"delete_point"`. A
#: bare-name entry is a SILENT NO-OP: accepted into the set, it changes nothing,
#: and the gate keeps blocking the statement while its author believes it was
#: proven read-only. `test_every_proven_read_names_a_real_census_entry` pins it.
_PROVEN_READS: set[tuple[str, str]] = set()


def test_every_proven_read_names_a_real_census_entry():
    """A `_PROVEN_READS` entry must be a key the census actually emits.

    Both consumers index the register with the SCOPE-QUALIFIED key, so an entry
    in any other form would sit in the set and change nothing — the "stale flag
    that is never revalidated" failure this suite exists to prevent. Empty today;
    a part-2 entry must still prove its key.
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
            if opener in _LIVE_LANE_NAMES:
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
    so an entry in the right key form but the wrong shape must be refused.
    """
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
