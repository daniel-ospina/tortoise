"""The catalogue-settle wait the fixtures share (#10807) — pinned without a server.

WHY THIS FILE EXISTS
--------------------
`tests/_index_settle.index_rows_after_settle` is the ONE definition of "wait until
the FalkorDB index catalogue has SETTLED", shared by the fixtures that each lost
the same race in a different file:

  * `tests/test_9079_handle_write_authority.py` drops `:Point(pointKind)` and then
    reads the catalogue to prove the drop — and a rebuild in flight still lists
    the retired field, so the read said "the drop did not happen" about a drop
    that had (`test (g)`, run 41817275671, on a loaded CI runner).
  * `tests/test_search_engine.py` drops `:Object(name)` FULLTEXT and asserts
    `_schema_is_current() is False` — the same async retirement, read through the
    product's own probe, which correctly answered True about a drift that had not
    been APPLIED yet (`test (b)`, run 41868531069).

Neither failure is reproducible on request (both windows are milliseconds on an
idle lane and outlive the read only under CI load), so the MECHANISM is pinned
here with a scripted fake: no server, no timing, no flake. What the live lanes
prove is that the fixtures call it; this file proves it waits for the right
thing, and that a bound which expires returns the LAST read rather than a
tolerant pass — the property the callers' unchanged assertions depend on.
"""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

from tests import _index_settle as _settle
from tests._index_settle import (
    INDEX_SETTLE_TIMEOUT_S,
    index_cells,
    index_kind_absent,
    index_kind_present,
    index_rows_after_settle,
    point_index_drops_visible,
)

#: The catalogue row shape `CALL db.indexes()` yields: [label, properties,
#: {prop: [kind, ...]}, ...]. Only the first three columns are read by the
#: helper, so the fakes carry exactly those (a fuller row adds nothing here).
_OBJECT_WITH_FULLTEXT = [
    ["Object", ["id", "name"],
     {"id": ["RANGE"], "name": ["RANGE", "FULLTEXT"]}],
]
_OBJECT_WITHOUT_FULLTEXT = [
    ["Object", ["id", "name"], {"id": ["RANGE"], "name": ["RANGE"]}],
]
_POINT_REBUILDING = [
    ["Point", ["id", "pointKind"], {"id": ["RANGE"], "pointKind": ["RANGE"]},
     None, "english", [], "NODE", "[Indexing] 3/7: UNDER CONSTRUCTION"],
]
_POINT_SETTLED = [
    ["Point", ["id", "pointKind"], {"id": ["RANGE"], "pointKind": ["RANGE"]},
     None, "english", [], "NODE", "OPERATIONAL"],
]
#: `test (g)` the moment after its two `DROP` statements return and BEFORE the
#: engine's indexer thread applies them: idle, no rebuild status, and both
#: retired definitions still named. Quiescence alone treats this as settled.
_POINT_BEFORE_THE_DROPS_APPLIED = [
    ["Point", ["id", "pointKind"], {"id": ["RANGE"], "pointKind": ["RANGE"]},
     None, "english", [], "NODE", "OPERATIONAL"],
    ["Point", ["content", "search_keys"],
     {"content": ["FULLTEXT"], "search_keys": ["FULLTEXT"]},
     None, "english", [], "NODE", "OPERATIONAL"],
]
#: The same label once BOTH drops are applied: `pointKind` and the legacy
#: two-field fulltext index are gone — what `test (g)`'s guards require.
_POINT_AFTER_THE_DROPS_APPLIED = [
    ["Point", ["id"], {"id": ["RANGE"]},
     None, "english", [], "NODE", "OPERATIONAL"],
]


class _ScriptedProj:
    """A projection whose catalogue answers from a script, one read per query.

    The last entry repeats, so a wait that never settles keeps reading the
    final state instead of raising IndexError — the shape a real hung rebuild
    presents.
    """

    def __init__(self, *answers):
        self._answers = list(answers)
        self.reads = 0
        self.g = SimpleNamespace(query=self._query)

    def _query(self, _cypher):
        index = min(self.reads, len(self._answers) - 1)
        self.reads += 1
        return SimpleNamespace(result_set=self._answers[index])


def test_a_settled_catalogue_costs_exactly_one_read():
    """The steady state — every pass in the suite — must not poll.

    A helper that always slept first would add 0.1 s to every caller and hide a
    contract violation behind a sleep; one read is the whole cost of "settled".
    """
    proj = _ScriptedProj(_OBJECT_WITHOUT_FULLTEXT)
    assert index_rows_after_settle(proj) == _OBJECT_WITHOUT_FULLTEXT
    assert proj.reads == 1


def test_a_rebuild_in_flight_is_waited_out():
    """`UNDER CONSTRUCTION` anywhere in a row means the catalogue is mid-rebuild.

    This is the `test (g)` mechanism: the retired row is still there, so a read
    taken now would report the drop as failed.
    """
    proj = _ScriptedProj(_POINT_REBUILDING, _POINT_SETTLED)
    assert index_rows_after_settle(proj) == _POINT_SETTLED
    assert proj.reads == 2


def test_a_stale_pre_drop_row_is_waited_out_not_returned():
    """The `test (b)` shape: NOT every un-applied drop shows `UNDER CONSTRUCTION`.

    A catalogue read taken before the engine applied the drop is internally
    consistent — no rebuild status, the idle `OPERATIONAL` one — so quiescence
    ALONE returns it and the caller asserts about a drop that has not happened
    yet. `until` is what makes that read wait.
    """
    proj = _ScriptedProj(_OBJECT_WITH_FULLTEXT, _OBJECT_WITHOUT_FULLTEXT)
    rows = index_rows_after_settle(
        proj, until=lambda r: index_kind_absent(r, "Object", "name", "FULLTEXT"))
    assert rows == _OBJECT_WITHOUT_FULLTEXT
    assert proj.reads == 2


def test_a_bound_that_expires_returns_the_LAST_read_never_a_tolerant_pass():
    """The fail-closed half: never settle on a state the caller did not ask for.

    On expiry the callers' own assertions run against the mid-flight catalogue
    and RED — a drop that genuinely cannot happen is still reported as failed
    rather than passing vacuously. `timeout=0` bounds it here; the shipped
    default is a runaway guard, not an expected wait.
    """
    proj = _ScriptedProj(_OBJECT_WITH_FULLTEXT)
    rows = index_rows_after_settle(
        proj,
        until=lambda r: index_kind_absent(r, "Object", "name", "FULLTEXT"),
        timeout=0)
    assert rows == _OBJECT_WITH_FULLTEXT, (
        "an expired bound must hand back the unsettled read so the caller's "
        "assertion fires — returning [] or a fabricated clean state would turn "
        "a real failure into a silent pass")
    assert INDEX_SETTLE_TIMEOUT_S > 1.0, (
        "the shipped bound is a runaway guard for a loaded runner, not the "
        "millisecond window measured idle")


def test_point_index_drops_visible_requires_BOTH_of_test_g_drops():
    """The `test (g)` precondition, both halves (#10807).

    One predicate, not two ad-hoc lambdas: the wait and the two vacuity guards
    below it must agree about what "the drops are visible" means, or the wait
    can settle on a catalogue the guards reject — the false red, restored.
    """
    assert point_index_drops_visible(_POINT_BEFORE_THE_DROPS_APPLIED) is False
    # Either drop still visible keeps the fixture waiting.
    assert point_index_drops_visible([_POINT_SETTLED[0]]) is False, (
        "the range drop alone must not certify")
    assert point_index_drops_visible([_POINT_BEFORE_THE_DROPS_APPLIED[1]]) is False, (
        "the fulltext drop alone must not certify")
    assert point_index_drops_visible(_POINT_AFTER_THE_DROPS_APPLIED) is True
    # A catalogue that names neither index cannot hide either of them.
    assert point_index_drops_visible([]) is True


def test_the_test_g_wait_closes_the_pre_drop_window_quiescence_misses():
    """The finding this pins: `test (g)`'s window is the one with NO status.

    When the `DROP` has returned but the indexer thread has not picked it up, the
    catalogue is internally consistent — pre-drop definition, no
    `UNDER CONSTRUCTION` — so a quiescence-only wait returns it on the FIRST
    read and the fixture then refuses a drop that landed. Armed with its own
    precondition, the same read is waited out.
    """
    stale = _ScriptedProj(_POINT_BEFORE_THE_DROPS_APPLIED,
                          _POINT_AFTER_THE_DROPS_APPLIED)
    assert index_rows_after_settle(stale) == _POINT_BEFORE_THE_DROPS_APPLIED, (
        "this is the defect, not the fix: without `until` the pre-drop read is "
        "returned immediately and `test (g)`'s guards red on a LANDED drop")
    assert stale.reads == 1

    armed = _ScriptedProj(_POINT_BEFORE_THE_DROPS_APPLIED,
                          _POINT_AFTER_THE_DROPS_APPLIED)
    rows = index_rows_after_settle(armed, until=point_index_drops_visible)
    assert rows == _POINT_AFTER_THE_DROPS_APPLIED
    assert armed.reads == 2


def test_index_kind_absent_reads_the_kinds_map_and_fails_closed():
    """The predicate itself, including the shapes that must NOT certify a drop."""
    assert index_kind_absent(_OBJECT_WITH_FULLTEXT, "Object", "name", "FULLTEXT") is False
    assert index_kind_absent(_OBJECT_WITHOUT_FULLTEXT, "Object", "name", "FULLTEXT") is True
    # A row for ANOTHER label says nothing about this one.
    assert index_kind_absent(_POINT_SETTLED, "Object", "name", "FULLTEXT") is True
    # A kinds map that is absent (or not a map) cannot prove the drop.
    assert index_kind_absent([["Object", ["name"], None]], "Object", "name",
                             "FULLTEXT") is False
    assert index_kind_absent([["Object"]], "Object", "name", "FULLTEXT") is False
    # The engine has spelled a single kind as a bare string; accept both.
    assert index_kind_absent(
        [["Object", ["name"], {"name": "FULLTEXT"}]], "Object", "name",
        "FULLTEXT") is False
    # Case is the engine's to choose; the comparison is kind, not spelling.
    assert index_kind_absent(
        [["Object", ["name"], {"name": ["fulltext"]}]], "Object", "name",
        "FULLTEXT") is False


def test_index_kind_present_reads_the_kinds_map_and_fails_closed():
    """The positive predicate's own shapes, INCLUDING the ones that must not
    certify a create.

    Mirror of `test_index_kind_absent_reads_the_kinds_map_and_fails_closed`:
    `index_kind_present` gates a wait that RELEASES an assertion, so a predicate
    that answered True on a row it cannot read would let the caller assert about
    a create the engine has not applied. Added at #12856, where the predicate
    became a consumer-facing requirement rather than a local convenience.
    """
    assert index_kind_present(_OBJECT_WITH_FULLTEXT, "Object", "name",
                              "FULLTEXT") is True
    assert index_kind_present(_OBJECT_WITHOUT_FULLTEXT, "Object", "name",
                              "FULLTEXT") is False
    # A row for ANOTHER label says nothing about this one.
    assert index_kind_present(_OBJECT_WITH_FULLTEXT, "Point", "name",
                              "FULLTEXT") is False
    # …including a label that CONTAINS this one: the match is the label, not a
    # substring of it (`ObjectMeta`'s row must not answer for `Object`).
    assert index_kind_present([["ObjectMeta", ["name"], {"name": ["FULLTEXT"]}]],
                              "Object", "name", "FULLTEXT") is False
    # …and it is not case-folded either: a label is the engine's exact spelling.
    assert index_kind_present([["object", ["name"], {"name": ["FULLTEXT"]}]],
                              "Object", "name", "FULLTEXT") is False
    # A kinds map that is absent (or not a map) cannot prove the create.
    assert index_kind_present([["Object", ["name"], None]], "Object", "name",
                              "FULLTEXT") is False
    assert index_kind_present([["Object"]], "Object", "name", "FULLTEXT") is False
    # The field present under a DIFFERENT kind is not this kind.
    assert index_kind_present([["Object", ["name"], {"name": ["RANGE"]}]],
                              "Object", "name", "FULLTEXT") is False
    # The engine has spelled a single kind as a bare string; accept both.
    assert index_kind_present(
        [["Object", ["name"], {"name": "FULLTEXT"}]], "Object", "name",
        "FULLTEXT") is True
    # Case is the engine's to choose; the comparison is kind, not spelling.
    assert index_kind_present(
        [["Object", ["name"], {"name": ["fulltext"]}]], "Object", "name",
        "FULLTEXT") is True


def _foldable_bool(node) -> tuple[bool, bool | None]:
    """Fold a CONSTANT-BOOLEAN expression: ``(True, value)`` when it is decidable.

    One step per node, no evaluation of anything that reads data: literals,
    ``not``, and ``and``/``or`` chains of already-foldable parts. This is what
    lets the arming rule be an ALLOW-LIST without blessing `lambda r: not
    False`, `lambda r: True or False`, or a name bound to either.

    ⛔ AND `and`/`or` SHORT-CIRCUIT, so ONE operand decides the whole expression
    no matter what the others read: `False and X` is False, `X or True` is True.
    Requiring EVERY operand to fold instead reported those as undecidable, and
    the structural rule reads "undecidable" as "an allowed form that mentions
    `rows`, so it is a predicate" — so `lambda r: True or <predicate>` and
    `lambda r: r[0] or True` were ARMED while returning on the FIRST catalogue
    read (#12859's review, measured at `cd7a4b3f31`: rewriting a real documented
    site to `until=lambda rows: True or index_kind_absent(...)` left 14 passed).
    """
    if isinstance(node, ast.Constant):
        return True, bool(node.value)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        ok, value = _foldable_bool(node.operand)
        return (True, not value) if ok else (False, None)
    if isinstance(node, ast.BoolOp):
        parts = [_foldable_bool(v) for v in node.values]
        is_and = isinstance(node.op, ast.And)
        if is_and and any(ok and value is False for ok, value in parts):
            return True, False
        if not is_and and any(ok and value is True for ok, value in parts):
            return True, True
        if not all(ok for ok, _ in parts):
            return False, None
        values = [v for _, v in parts]
        return True, (all(values) if is_and else any(values))
    return False, None


#: The ONLY `until` values armed WITHOUT further evidence: the documented row
#: predicates. ⛔ `dir(tests._index_settle)` IS NOT THIS SET (measured at
#: #12856's review): it also holds `INDEX_SETTLE_TIMEOUT_S` (the float 120.0),
#: `INDEX_SETTLE_POLL_S`, `POINT_RANGE_FIELD`, `POINT_FULLTEXT_FIELDS`,
#: `index_cells`, `index_rows_after_settle`, `time` and `annotations` — every one
#: of which is truthy on the first catalogue read, so `until=index_cells` and
#: `until=_index_settle.INDEX_SETTLE_TIMEOUT_S` were certified ARMED while being
#: constant-vacuous. The list is spelled out because a predicate set that grows
#: by ``import *`` is a predicate set nobody reviewed.
_ROW_PREDICATES = frozenset({
    "index_kind_present",
    "index_kind_absent",
    "point_index_drops_visible",
})

#: The expression FORMS a row predicate's body may take. Anything outside this
#: set is truthy regardless of the rows it is handed — a tuple/list/set/dict
#: literal, an f-string, a negative literal — and so releases on the FIRST read.
#: `lambda r: (True,)` and the one-character slip
#: `lambda r: (index_kind_absent(r, …),)` are both in this exclusion.
_PREDICATE_BODY = (ast.Call, ast.Compare, ast.BoolOp, ast.IfExp, ast.Name,
                   ast.Attribute, ast.Subscript, ast.Await)

#: CALLS that FOLD the waited-on collection into a boolean. `all(...)`/`any(...)`
#: read the ELEMENTS, so their result varies with the rows even when the argument
#: is a helper call. ⛔ `bool(...)` IS NOT ONE OF THEM: `bool(collection)` is True
#: for any NON-EMPTY collection, which is the same vacuity as writing the
#: collection itself (measured at #12859's review).
_FOLD_CALLS = frozenset({"all", "any"})


def _call_forms_a_predicate(call: ast.Call, params: set[str]) -> bool:
    """Does this CALL's own result vary with the rows? (#12859's review)

    ⛔ A CALL IS NOT A PREDICATE BECAUSE IT MENTIONS ITS ARGUMENT. On FORM alone
    (`_PREDICATE_BODY` + a row parameter) the rule certified
    ``lambda rows: _point_indexes(index_cells(rows))`` ARMED — and
    `_point_indexes` returns a two-tuple, non-empty hence TRUTHY whatever its two
    booleans say — so the wait released on the FIRST catalogue read while every
    arm of the floor stayed green (measured at `eb3463d611`). The very same value
    written as a TUPLE LITERAL was correctly UNARMED, so the exclusion the rule
    already documents was missing only for the call spelling, and getting there
    takes deleting `all(` and `)` at a real documented site.

    Two routes, each requiring evidence in the CALL itself:
      * ``all(...)``/``any(...)`` — a fold the CALLER applies to the collection
        the wait reads, and its argument must mention a row parameter so
        ``all((True,))`` cannot ride in on the wrapper;
      * a documented row predicate, called directly (`index_kind_absent(rows, …)`).
    An ``Attribute`` callee is refused here: `_lambda_is_row_predicate` has no
    import context, and certifying `other.index_kind_absent` on a name collision
    is the hole `_until_is_armed` closes with its `module_aliases` arm. Nothing
    else is ARMED, so an unlisted call form reds the pin loudly instead of
    passing — the same fail-closed fallthrough the rest of the rule uses.
    """
    func = call.func
    names = {n.id for n in ast.walk(call) if isinstance(n, ast.Name)}
    mentions_rows = bool(params & names)
    if not mentions_rows:
        return False
    if isinstance(func, ast.Name):
        return func.id in _FOLD_CALLS or func.id in _ROW_PREDICATES
    return False


def _lambda_is_row_predicate(lam: ast.Lambda) -> bool:
    """Is this lambda a predicate OVER ITS ROWS, or a truthy constant wearing one?

    ⛔ "NOT CONSTANT-FOLDABLE" IS NOT THE TEST, and reading it as one is how this
    pin kept certifying vacuous waits (#12856's review, two independent reviewers,
    converging): `_foldable_bool` decides literals/`not`/`and`-`or`, so every form
    it does not know — a tuple, a list, an f-string, a negative literal, a
    `bool(1)` call — came back "undecidable" and was therefore treated as "reads
    `rows`". Measured: `lambda r: (True,)` and
    `lambda r: (index_kind_absent(r, "Event", "id", "RANGE"),)` were both ARMED,
    and the second is TRUTHY over a PRE-DROP catalogue — the exact defect this
    pin exists to forbid, one bracket away from a correct site.

    Two conditions, both necessary:
      1. the body is one of `_PREDICATE_BODY`'s forms (a container literal, an
         f-string or an arithmetic expression is dismissed here); and
      2. the body MENTIONS one of the declared row parameters (a body that reads
         only literals — `bool(1)`, `bool(1) >= 0`, `True if True else False` —
         is constant whatever form it takes).
    A FOLDABLE body is decided first, so `lambda r: True` is refused while
    `lambda r: False` stays armed: it waits out the deadline and reds the caller's
    own assertion, which is fail-closed.
    """
    params = {a.arg for a in (*lam.args.posonlyargs, *lam.args.args,
                              *lam.args.kwonlyargs)} - {"_"}
    if not params:
        return False  # not a predicate over rows at all
    ok, constant = _foldable_bool(lam.body)
    if ok:
        return not constant
    negated = (isinstance(lam.body, ast.UnaryOp)
               and isinstance(lam.body.op, ast.Not))
    if not (negated or isinstance(lam.body, _PREDICATE_BODY)):
        return False
    # ⛔ A CALL BODY NEEDS ITS CALLEE EXAMINED, not just its shape: the form rule
    # alone certified a helper returning a non-empty tuple (truthy on the first
    # read) as a predicate, because it mentioned its `rows` argument.
    if isinstance(lam.body, ast.Call) and not _call_forms_a_predicate(
            lam.body, params):
        return False
    return bool(params & {n.id for n in ast.walk(lam.body)
                          if isinstance(n, ast.Name)})


def _foldable_number(node) -> float | None:
    """The value of a NUMERIC literal (optionally signed), else ``None``.

    Only literals: a name is a runtime value an AST cannot evaluate, and
    pretending otherwise would unarm a site over a binding it never read.
    """
    sign = 1.0
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        sign = -1.0 if isinstance(node.op, ast.USub) else 1.0
        node = node.operand
    if (isinstance(node, ast.Constant)
            and isinstance(node.value, (int, float))
            and not isinstance(node.value, bool)):
        return sign * float(node.value)
    return None


def _until_is_armed(call, ctx=None) -> bool:
    """Does this `index_rows_after_settle(...)` call pass a USABLE `until`?

    ⛔ A PRESENT KEYWORD IS NOT AN ARMED WAIT, and neither is any single spelling
    of a constant: `until=None` is the helper's own default — the quiescence-only
    read this pin exists to forbid — so a syntactic `kw.arg == "until"` test
    certified the exact defect it names. The same hole was then re-measured one
    spelling at a time (#12856 review cycles 6-9): `until=None`, `NONE = None`
    … `until=NONE`, `until=lambda r: True`, `until=bool`, `until=all`,
    `until=lambda r: not False`, `until=lambda r: True or False`, a module-level
    `ALWAYS = lambda r: True`, and `def _ALWAYS(rows): return True` — each
    certified GREEN while returning on the FIRST catalogue read. Enumerating
    spells is a deny-list, and a deny-list only ever covers the spellings
    somebody thought of.

    ⛔ SO THE DECISION IS STRUCTURAL, NOT SPELLED. Four arms, and the FALLTHROUGH
    IS UNARMED — that is what makes an unlisted form fail closed instead of
    passing:
      * `Lambda` → `_lambda_is_row_predicate` (form + reads a row parameter);
      * `Constant` → armed unless it is `None` (`until=True` is not callable, so
        it raises at the first read — loud, not silent);
      * `Name` → armed only for one of `_ROW_PREDICATES`, directly or through an
        `ImportFrom` alias;
      * `Attribute` → armed only for `_ROW_PREDICATES` reached through a name
        bound to the `tests._index_settle` MODULE, so `other.index_kind_absent`
        and `Helpers.index_kind_absent` are not certified by a name collision;
      * everything else — a `Call` (`make_always()`, `(lambda: True)()`)…, a
        tuple literal, an `IfExp`, a `Subscript`, a module-level `def`/`lambda`
        named as the value — is UNARMED.

    ⛔ AND THE BOUND IS PART OF THE ARMAMENT, not a separate argument: the
    helper builds `deadline = now + timeout` and returns on
    `settled or now >= deadline`, so `timeout=0` hands back the FIRST read
    whatever predicate the call passes — `until=None` one keyword to the right.
    Measured at #12859's review by adding `timeout=0` to BOTH waits of the red
    this PR fixes: 44 passed, byte-identical to the baseline, on a read that
    returns before the indexer applies the drop. A DECIDABLE bound of zero or
    less is therefore UNARMED; a runtime bound (`timeout=bound_s`, a name whose
    value is not in the source) stays armed — an AST cannot evaluate it, and
    that is the same accident-guard posture as the rest of this rule.

    ⛔ SO A MODULE-LEVEL `def`/`lambda`/REBINDING IS NOT A ROUTE IN AT ALL: only a
    literal lambda or a documented predicate can arm a site. That is why the
    scope-blindness that used to launder a name (`ALWAYS = lambda r: True`, then a
    later in-function `ALWAYS = index_kind_absent`) cannot arise — there is no
    name resolution to launder.

    ⛔ THE RESIDUAL, STATED AS MEASURED SO IT CANNOT BE MISTAKEN FOR COVERAGE: a
    lambda that DOES take a row parameter, DOES return an allowed form, and is
    still constant in a way the FOLDER cannot see — `lambda r: bool(r) >= 0`,
    `lambda r: len(r) >= 0` — stays armed. Whether an arbitrary expression's
    truth actually varies with its argument is not decidable from an AST, and
    this pin is an accident guard, not a prover: the aim is that every PLAUSIBLE
    and every LAZY spelling reds (they do — see
    `test_the_arming_rule_is_structural_not_a_deny_list`), not that no
    adversarial expression can ever be written. ⛔ A SHORT-CIRCUITING OPERAND IS
    NOT PART OF THIS RESIDUAL: `and`/`or` fold one deciding operand, so
    `lambda r: True or r[0]`, `lambda r: r[0] or True` and
    `lambda r: True or index_kind_absent(...)` are UNARMED, while a constant-FALSE
    predicate (`lambda r: False and r[0]`) stays ARMED because it fails closed.
    A `**{"until": None}` splat is reported UNARMED.
    """
    ctx = ctx or {}
    aliases = ctx.get("aliases", {})
    module_aliases = ctx.get("module_aliases", frozenset())
    for kw in call.keywords:
        if kw.arg == "timeout":
            bound = _foldable_number(kw.value)
            if bound is not None and bound <= 0:
                return False
    for kw in call.keywords:
        if kw.arg != "until":
            continue
        value = kw.value
        if isinstance(value, ast.Lambda):
            return _lambda_is_row_predicate(value)
        if isinstance(value, ast.Constant):
            return value.value is not None  # `until=True` raises at runtime: loud
        if isinstance(value, ast.Name):
            return (value.id in _ROW_PREDICATES
                    or aliases.get(value.id) in _ROW_PREDICATES)
        if isinstance(value, ast.Attribute):
            return (value.attr in _ROW_PREDICATES
                    and isinstance(value.value, ast.Name)
                    and value.value.id in module_aliases)
        # ⛔ THE FALLTHROUGH IS THE POINT: an unlisted form reds instead of
        # passing. `return True` here was five of the nine measured escapes.
        return False
    return False


def _settle_call_sites(paths) -> tuple[dict[str, list[int]], dict[str, list[int]]]:
    """Every `index_rows_after_settle(...)` call under `paths`: sites, and the
    ones missing their `until`.

    ⛔ BOTH CALL FORMS, because a module-prefixed call is invisible to a bare
    `ast.Name` scan (review of #10807). `index_rows_after_settle(proj)` and
    `_index_settle.index_rows_after_settle(proj)` are the SAME call — the form
    carries no meaning, only the arguments do — so a pin that reads one form stops
    covering the site it documents the day somebody writes the other.

    ⛔ AND A THIRD FORM: `from tests._index_settle import index_rows_after_settle as
    settle` then `settle(proj)` is the same call again, and it is neither `Name(
    id="index_rows_after_settle")` nor an attribute — an aliased import made the
    call INVISIBLE in both directions (no site recorded, so the floor could be met
    by unrelated sites, and no `until` check, so a pre-drop read passed the pin).
    Aliases are resolved from the module-level `ImportFrom` nodes rather than by
    matching the local name, so an alias of a DIFFERENT symbol from the same module
    is not mistaken for a call site.
    """
    missing: dict[str, list[int]] = {}
    call_sites: dict[str, list[int]] = {}
    for path in sorted(Path(p) for p in paths):
        source = path.read_bytes()
        # Cheap pre-filter: parsing all ~1,200 test modules to find two call
        # sites would cost the suite seconds for nothing.
        if b"index_rows_after_settle" not in source:
            continue
        tree = ast.parse(source)
        # local name -> the symbol it was imported as (`settle` ->
        # `index_rows_after_settle`), from any `from ._index_settle import …`.
        aliases: dict[str, str] = {}
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or not node.module:
                continue
            if not node.module.endswith("_index_settle"):
                continue
            for alias in node.names:
                aliases[alias.asname or alias.name] = alias.name
        # Names bound to the `tests._index_settle` MODULE (`import tests._index_
        # settle as _index_settle`, or any alias of it). Only these may carry a
        # `_ROW_PREDICATES` attribute: matching the attribute NAME alone would
        # certify `other.index_kind_absent` and `Helpers.index_kind_absent` — a
        # name collision is not a predicate (#12856's review).
        module_aliases: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.endswith("_index_settle"):
                        module_aliases.add(alias.asname or alias.name)
        ctx = {"aliases": aliases, "module_aliases": module_aliases}
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            named = (
                (isinstance(func, ast.Name)
                 and (func.id == "index_rows_after_settle"
                      or aliases.get(func.id) == "index_rows_after_settle"))
                or (isinstance(func, ast.Attribute)
                    and func.attr == "index_rows_after_settle")
            )
            if not named:
                continue
            call_sites.setdefault(path.name, []).append(node.lineno)
            if not _until_is_armed(node, ctx):
                missing.setdefault(path.name, []).append(node.lineno)
    return call_sites, missing


def _settle_site_functions(paths) -> dict[str, dict[str, int]]:
    """How many settle CALL SITES does each FUNCTION of each module carry?

    The MODULE key is too coarse a floor: a module that keeps ONE armed call
    still satisfies it after every other was deleted. Measured at #12856's review
    — deleting both waits inside the test that red is named for left the module
    key green. A SET of functions is too coarse as well: removing ONE of a
    documented function's two waits left it green (measured at review cycle 7).
    Counting the call sites per function is what makes a single deletion visible.

    Nested functions and class METHODS are attributed to their enclosing name
    (``Class.method``; a nested function's sites also count for its parent,
    because the parent's body contains them), because `test_search_engine.py`
    arms its waits in methods.
    """
    out: dict[str, dict[str, int]] = {}

    def _record(path_name: str, lines, node, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = prefix + child.name
                end = child.end_lineno or child.lineno
                # ⛔ COUNT CALLS, NOT DISTINCT LINES: two waits written on one
                # line are two settle calls, and a `set` of line numbers would
                # report one — so deleting the second stayed green (measured at
                # #12856's review cycle 8).
                count = sum(1 for line in lines
                            if child.lineno <= line <= end)
                if count:
                    out.setdefault(path_name, {})[name] = count
                _record(path_name, lines, child, name + ".")
            elif isinstance(child, ast.ClassDef):
                _record(path_name, lines, child, prefix + child.name + ".")

    for path in sorted(Path(p) for p in paths):
        source = path.read_bytes()
        if b"index_rows_after_settle" not in source:
            continue
        call_sites, _missing = _settle_call_sites([path])
        lines = call_sites.get(path.name, [])
        if not lines:
            continue
        _record(path.name, lines, ast.parse(source), "")
    return out


def test_every_consumer_passes_the_caller_precondition():
    """The module's contract, applied at EVERY documented call site (#10807).

    Quiescence alone is not sufficient: a read taken before the engine applied
    the drop is internally consistent (no `UNDER CONSTRUCTION`, the idle
    `OPERATIONAL` status, the pre-drop definition), so it is returned on the
    FIRST read and the caller asserts about a drop that has not happened yet.
    That was review Finding 1 at `8a3324949`: `test (b)` passed an `until` and
    `test (g)` did not, so the false red this module exists to remove could
    still fire in the write-authority suite.

    pinned STRUCTURALLY because the defect is invisible behaviourally on an idle
    lane (`test (b)`'s window measures 3 ms) and only reds under CI load — the
    same reason this whole file exists. This module is exempt: it drives the raw
    wait on purpose, including quiescence-only.

    ⛔ THE FLOOR NAMES (MODULE, FUNCTION) PAIRS — not a count, and not a module
    (review of #10807, then of #12856): `len(call_sites) >= 2` could be satisfied
    by two NEW call sites, and a MODULE key could be satisfied by one surviving
    armed call while the documented one was deleted. Measured at #12856's review:
    removing BOTH waits inside `test_event_id_index_is_required_so_an_existing_
    graph_gets_it` left the module key green.

    ⛔ AND IT IS SET EQUALITY OVER THE DOCUMENTED MODULES, not `declared ⊆ armed`.
    A one-way check protects the pairs somebody remembered to list: measured at
    #12856's review cycle 5, FIVE of the TEN armed functions in these four
    modules were undeclared, so each could have lost its wait with this pin
    green — the same vacuity the pair granularity was added to close, one level
    down. Declaring a site is now part of arming a wait in a documented module.

    ⛔ AND THE SCOPE ITSELF IS PINNED TO A LITERAL, not merely tied to the
    ledger. `_undeclared_sites` filters its candidates on
    `_DOCUMENTED_CONSUMERS`, so emptying that set reports NO undeclared site.
    The ledger-agreement assertion below closes that for an edit to ONE of the
    two constants; this closes the remaining pair — a diff that removes a module
    from BOTH `_DOCUMENTED_CONSUMERS` and every one of its `_DOCUMENTED_SITES`
    pairs agrees with itself and quietly narrows what the floor speaks for. The
    modules are therefore named here, where a change to either constant alone
    cannot satisfy it (measured at #12859's review: `_DOCUMENTED_CONSUMERS =
    set()` alone left this file green, 44 passed / 4 skipped).

    ⛔ AND EACH PAIR DECLARES ITS SETTLE-CALL-SITE COUNT, not merely its
    existence: with a SET of pairs, FIVE of the TEN declared pairs carried TWO
    waits, so removing one of the two — the DROP, keeping its recreate — left the
    pin green (measured at review cycle 7). The count is the granularity the
    waits have. ⚠️ It counts CALL SITES, not "armed" ones: `_settle_site_
    functions` keeps `_settle_call_sites`' site list and drops its `_missing`
    set, so an `until`-less call still counts for its pair — it reds the
    `missing` assertion instead.

    ⛔ AND THE SCOPE CONSTANT IS PINNED, not trusted. `required` came from
    `_DOCUMENTED_CONSUMERS` while the ledger came from `_DOCUMENTED_SITES`, so
    an EMPTIED `_DOCUMENTED_CONSUMERS` made `required <= set(call_sites)`
    trivially true and made `_undeclared_sites` filter on an empty module set —
    the set-equality arm above went vacuous while the file stayed green
    (measured at #12856's review: emptying it left 43 passed, 4 skipped, and a
    NEWLY added undeclared armed wait in `test_indexes.py` passed with it). The
    two must now AGREE, and the scope must be non-empty.
    """
    here = Path(__file__).resolve()
    paths = [p for p in here.parent.glob("test_*.py") if p.name != here.name]
    call_sites, missing = _settle_call_sites(paths)
    required = set(_settle._DOCUMENTED_CONSUMERS)
    expected_consumers = {
        "test_9079_handle_write_authority.py",
        "test_search_engine.py",
        "test_11682_quote_indexed.py",
        "test_indexes.py",
    }
    assert required == expected_consumers, (
        "`_DOCUMENTED_CONSUMERS` must name exactly the modules this pin speaks "
        "for — it IS the filter `_undeclared_sites` applies, so emptying or "
        "narrowing it (alone, or together with its ledger pairs, which agree "
        "with themselves) voids the undeclared arm while this file stays "
        f"green: {sorted(required)}")
    assert required <= set(call_sites), (
        "the scan must find the call site in each module this file documents — a "
        f"missing one means the pin went vacuous (a rename?): {sorted(call_sites)}")
    by_function = _settle_site_functions(paths)
    have = {(mod, fn): n for mod, fns in by_function.items()
            for fn, n in fns.items()}
    declared = dict(_settle._DOCUMENTED_SITES)
    assert required and {m for m, _ in declared} == required, (
        "the pin's SCOPE constant and its declaration ledger must agree and the "
        "scope must be non-empty — a module named by one of them only is a wait "
        "this floor cannot speak for, and an emptied `_DOCUMENTED_CONSUMERS` "
        "would void the undeclared arm entirely: "
        f"scope={sorted(required)}, "
        f"declared={sorted({m for m, _ in declared})}")
    assert declared.keys() <= have.keys(), (
        "these documented (module, function) pairs no longer arm a settle — "
        "removing the wait re-opens the pre-drop race the pair exists to close: "
        f"{sorted(declared.keys() - have.keys())}")
    drifted = {pair: {"declared": count, "armed": have.get(pair)}
               for pair, count in declared.items()
               if have.get(pair) != count}
    assert not drifted, (
        "these documented (module, function) pairs carry a different number of "
        "armed settle calls than this pin declares — a wait was removed from (or "
        "added to) one of them, which re-opens the pre-drop race the pair exists "
        f"to close: {drifted}")
    undeclared = _undeclared_sites(
        by_function, _settle._DOCUMENTED_CONSUMERS, declared)
    assert not undeclared, (
        "these (module, function) pairs arm a settle in a module this pin "
        "DOCUMENTS but are absent from `_DOCUMENTED_SITES` — an undeclared wait "
        "is one the floor cannot protect, so removing it later would leave the "
        f"pin green: add them to `_DOCUMENTED_SITES`: {sorted(undeclared)}")
    assert not missing, (
        "these `index_rows_after_settle` calls pass no usable `until` (absent, or "
        "the literal `until=None`, which IS the quiescence-only default), so a "
        "stale pre-drop read (idle, no `UNDER CONSTRUCTION`) is returned on the "
        f"first read and the caller's guard can refuse a GOOD fixture: {missing}")


def _undeclared_sites(by_function, documented_modules,
                      declared) -> set[tuple[str, str]]:
    """Armed settle sites a DOCUMENTED module has that ``declared`` omits.

    The second half of the floor, kept as its own function so
    `test_the_floor_reds_on_an_armed_wait_it_does_not_declare` can drive it on
    synthetic input: an arm nobody has SEEN fire is indistinguishable from one
    that cannot fire. ``by_function`` is module -> {function: armed count} (a
    plain set of functions also works — iterating it yields the names), and
    ``declared`` is any iterable of pairs, so a count ledger and a set ledger are
    both accepted.

    Restricted to ``documented_modules`` on purpose — a module this pin does not
    document may arm a wait freely; only the modules it claims to speak for owe
    a declaration.
    """
    armed = {(mod, fn) for mod, fns in by_function.items() for fn in fns}
    return {site for site in armed if site[0] in set(documented_modules)} \
        - set(declared)


def test_the_floor_reds_on_an_armed_wait_it_does_not_declare():
    """Both directions of the SET-EQUALITY arm, on synthetic scans.

    ⛔ Pre-fix the floor was `declared ⊆ armed` only, so an armed wait missing
    from `_DOCUMENTED_SITES` was invisible: measured at #12856's review cycle 5,
    FIVE of the TEN armed functions in the four documented modules were
    undeclared and every one of them could have lost its wait with this pin
    green. The clean case is asserted too, so the arm cannot be a constant-red.
    """
    documented = {"test_indexes.py"}
    declared = {("test_indexes.py", "test_declared")}

    # A documented module with an armed wait nobody declared -> reported.
    assert _undeclared_sites(
        {"test_indexes.py": {"test_declared", "test_undeclared"}},
        documented, declared) == {("test_indexes.py", "test_undeclared")}

    # Declared AND armed -> clean (the non-vacuity control).
    assert _undeclared_sites(
        {"test_indexes.py": {"test_declared"}}, documented, declared) == set()

    # A module this pin does NOT document may arm a wait freely.
    assert _undeclared_sites(
        {"test_other.py": {"test_anything"}}, documented, declared) == set()

    # A declared site that stopped arming is the OTHER arm's finding, not this one.
    assert _undeclared_sites(
        {"test_indexes.py": set()}, documented, declared) == set()


def test_the_scan_sees_a_module_prefixed_call_and_its_missing_until(tmp_path):
    """Both directions of the ATTR form, on a file whose lines are known exactly.

    ⛔ Pre-fix the scan matched bare `ast.Name` only, so `_index_settle.
    index_rows_after_settle(proj)` was invisible in BOTH directions: it never
    reached `call_sites` (so the floor could be met by unrelated sites) and it
    never reached `missing` (so a call that can return a pre-drop read passed the
    pin). Reverting to the bare-name test makes this case report `{}` for both.

    ⛔ `test_d`/`test_e` are the THIRD form — an ALIASED import. `settle(proj)` is
    the same call as `index_rows_after_settle(proj)`, and it was invisible to both
    the `Name(id=…)` and `Attribute(attr=…)` arms, so an alias was a free pass past
    the pin. `test_f` is the control that keeps the alias map from being a name
    match: importing a DIFFERENT symbol from the same module is not a call site.

    ⛔ `test_g` is the #12856 shape: `until=None` PRESENTS the keyword while
    asking for the quiescence-only read, so it must land in `missing` (measured
    at #12856's review cycle 6: this exact edit left every check green).

    ⛔ `test_h` is the same defect ONE NAME AWAY — `NONE = None` then
    `until=NONE`; a pin that only reads literals certifies it (measured at
    review cycle 7). `test_i`/`test_j` are the PREDICATE form: a truthy constant
    body (`lambda r: True`) returns on the first read and must be reported
    missing, while a falsy one waits out the deadline and reds the caller
    (fail-closed) and stays armed (measured at review cycle 8).
    """
    mod = tmp_path / "test_synthetic_consumer.py"
    mod.write_text(
        "import tests._index_settle as _index_settle\n"
        "from tests._index_settle import index_rows_after_settle as settle\n"
        "from tests._index_settle import point_index_drops_visible as visible\n"
        "NONE = None\n"
        "def test_a(proj):\n"
        "    _index_settle.index_rows_after_settle(proj)\n"
        "def test_b(proj):\n"
        "    _index_settle.index_rows_after_settle(proj, until=visible)\n"
        "def test_c(proj):\n"
        "    index_rows_after_settle(proj, until=visible)\n"
        "def test_d(proj):\n"
        "    settle(proj)\n"
        "def test_e(proj):\n"
        "    settle(proj, until=visible)\n"
        "def test_f(proj):\n"
        "    visible(proj)\n"
        "def test_g(proj):\n"
        "    index_rows_after_settle(proj, until=None)\n"
        "def test_h(proj):\n"
        "    index_rows_after_settle(proj, until=NONE)\n"
        "def test_i(proj):\n"
        "    index_rows_after_settle(proj, until=lambda r: True)\n"
        "def test_j(proj):\n"
        "    index_rows_after_settle(proj, until=lambda r: False)\n",
        encoding="utf-8")
    call_sites, missing = _settle_call_sites([mod])
    assert call_sites == {
        "test_synthetic_consumer.py": [6, 8, 10, 12, 14, 18, 20, 22, 24]}, call_sites
    assert missing == {
        "test_synthetic_consumer.py": [6, 12, 18, 20, 22]}, missing


#: `(case name, `until=` expression source, armed?)` — the arming rule's whole
#: contract. `test_the_arming_rule_is_structural_not_a_deny_list` turns each row
#: into its own function in a synthetic module, so every row is a measurement
#: rather than a claim in a docstring.
_ARMING_CASES = (
    # ── ARMED: a real predicate over the rows ──────────────────────────────
    ("documented_name", "index_kind_absent", True),
    ("module_qualified", "_index_settle.index_kind_absent", True),
    ("lambda_call", "lambda r: index_kind_absent(r, 'Event', 'id', 'RANGE')",
     True),
    ("lambda_all", "lambda rows: all(rows)", True),
    # A FOLD over a helper call is a predicate: `all(...)` reads the ELEMENTS, so
    # the result varies with the rows even though `_point_indexes(index_cells(
    # rows))` on its own does not (it is a non-empty tuple, always truthy).
    ("lambda_all_of_helper",
     "lambda rows: all(_point_indexes(index_cells(rows)))", True),
    ("lambda_any_of_helper",
     "lambda rows: any(_point_indexes(index_cells(rows)))", True),
    ("lambda_compare", "lambda r: 'quote' in (r or set())", True),
    ("lambda_subscript", "lambda r: r[0]", True),
    ("lambda_not_call",
     "lambda r: not index_kind_present(r, 'Event', 'id', 'RANGE')", True),
    # `until=True` is not callable, so it raises at the first read: loud, and
    # refusing it would be a false red on a value that cannot pass silently.
    ("constant_true_is_loud", "True", True),
    ("lambda_falsy_stays_armed", "lambda r: False", True),

    # ── UNARMED: the class measured GREEN by review cycles 6-9 ─────────────
    ("none", "None", False),
    ("builtin_bool", "bool", False),
    ("builtin_all", "all", False),
    ("lambda_constant_true", "lambda r: True", False),
    ("lambda_not_false", "lambda r: not False", False),
    ("lambda_boolop_constant", "lambda r: True or False", False),
    ("lambda_no_rows_param", "lambda: True", False),
    ("lambda_default_only", "lambda rows=(): True", False),

    # ── UNARMED: every form whose TRUTH does not vary with the rows ───────
    ("lambda_tuple_literal", "lambda r: (True,)", False),
    ("lambda_list_literal", "lambda r: [1]", False),
    ("lambda_fstring", "lambda r: f'ready'", False),
    ("lambda_negative_int", "lambda r: -1", False),
    ("lambda_bool_call", "lambda r: bool(1)", False),
    ("lambda_compare_constant", "lambda r: bool(1) >= 0", False),
    ("lambda_ifexp_constant", "lambda r: True if True else False", False),

    # ── UNARMED: a CALL whose own value does not vary with the rows ────────
    # The tuple-literal exclusion above is the same defect one spelling over: a
    # helper returning a non-empty collection is TRUTHY whatever the rows say,
    # and `all(` / `)` around it is one character of slip from a vacuous wait.
    # Measured at #12859's review, over the real site at
    # `test_9079_handle_write_authority.py:870`.
    ("lambda_helper_tuple",
     "lambda rows: _point_indexes(index_cells(rows))", False),
    ("lambda_bool_of_collection",
     "lambda rows: bool(_point_indexes(index_cells(rows)))", False),
    ("lambda_all_constant_arg", "lambda rows: all((True,))", False),
    ("lambda_module_helper", "lambda rows: index_cells(rows)", False),

    # ── SHORT-CIRCUITING OPERANDS decide the expression on their own ───────
    # `and`/`or` evaluate left to right and stop at a deciding operand, so the
    # whole expression is a constant whatever the other operands read. Reading
    # "some operand does not fold" as "undecidable, therefore a predicate"
    # certified all of `True or r[0]`, `r[0] or True` and `True or
    # <predicate>` ARMED; the last is how a human disables a hanging wait and it
    # released on the pre-drop read with every arm of the floor green.
    ("lambda_or_true_left", "lambda r: True or r[0]", False),
    ("lambda_or_true_right", "lambda r: r[0] or (not False)", False),
    ("lambda_or_true_wraps_real",
     "lambda r: True or index_kind_absent(r, 'Event', 'id', 'RANGE')", False),
    # Controls, so the new branch cannot unarm every `and`: a constant-FALSE
    # predicate stays ARMED — it never becomes truthy, so the wait runs to its
    # deadline and the caller's own assertion reds (fail-closed).
    ("lambda_and_false_left", "lambda r: False and r[0]", True),
    ("lambda_and_false_after_real",
     "lambda r: index_kind_absent(r, 'Event', 'id', 'RANGE') and False", True),
    # ⛔ THE WORST CASE: a one-character slip that WRAPS a real predicate. The
    # tuple is truthy even when the predicate is False, so it releases on the
    # pre-drop read — and "mentions a row parameter" alone does NOT catch it.
    ("lambda_tuple_wraps_real",
     "lambda r: (index_kind_absent(r, 'Event', 'id', 'RANGE'),)", False),

    # ── UNARMED: values the scan cannot vouch for (fallthrough reds) ──────
    ("call_make_always", "make_always()", False),
    ("call_iife", "(lambda: True)()", False),
    ("tuple_value", "(True,)", False),
    ("subscript_value", "r[0]", False),
    ("foreign_attribute", "other.point_index_drops_visible", False),
    ("class_attribute", "Helpers.index_kind_absent", False),
    # Names that ARE in `dir(tests._index_settle)` and are truthy on the first
    # read — the reason the predicate set is spelled out instead of imported.
    ("module_constant", "_index_settle.INDEX_SETTLE_TIMEOUT_S", False),
    ("module_helper", "_index_settle.index_cells", False),
    ("imported_from_other_module", "_always", False),
    # A module-level binding is not a route in at all, so there is no scope to
    # launder a name through (the `ALWAYS = lambda r: True` then in-function
    # `ALWAYS = index_kind_absent` shape both reviewers measured).
    ("module_level_lambda", "ALWAYS", False),
    ("module_level_def", "_REAL_ROWS", False),
)


def test_the_arming_rule_is_structural_not_a_deny_list(tmp_path):
    """Every form whose truth does not vary with the rows is UNARMED — and the
    real predicates are not.

    Two independent reviewers converged on the same root at `da9ddfe`: "body is
    not constant-foldable" was being read as "this reads `rows`", so every form
    `_foldable_bool` did not know — a tuple, a list, an f-string, a `bool(1)`
    call, `index_cells` from `dir(_index_settle)` — came out ARMED while being
    truthy on the FIRST catalogue read. One of them measured
    `lambda r: (index_kind_absent(r, …),)`: a real predicate in a tuple, TRUTHY
    over a PRE-DROP catalogue, certified ARMED by the pin. The rule is now
    structural (`_lambda_is_row_predicate` + `_ROW_PREDICATES` + an UNARMED
    fallthrough), so these are refused by construction; the ARMED rows above
    keep it non-vacuous, and they are the shapes the repo's 15 real sites use.

    ⛔ The rows are MEASURED, not read: each becomes a function in a synthetic
    module and the row's line is put in the expected `missing` set, so a row that
    claims UNARMED and is certified armed reds here by line number.
    """
    lines = [
        "from tests._index_settle import index_rows_after_settle",
        "from tests._index_settle import index_kind_present, index_kind_absent",
        "import tests._index_settle as _index_settle",
        "from tests.test_x import _always",
        "ALWAYS = lambda r: True",
        "def _REAL_ROWS(rows):",
        "    return any(rows)",
        "class Helpers:",
        "    index_kind_absent = staticmethod(lambda rows: True)",
        "other = object()",
    ]
    expected_missing: list[int] = []
    for name, expr, armed in _ARMING_CASES:
        lines.append(f"def test_{name}(proj):")
        lines.append(f"    index_rows_after_settle(proj, until={expr})")
        if not armed:
            expected_missing.append(len(lines))
    mod = tmp_path / "test_arming_cases.py"
    mod.write_text("\n".join(lines) + "\n", encoding="utf-8")
    call_sites, missing = _settle_call_sites([mod])
    rows = call_sites["test_arming_cases.py"]
    assert len(rows) == len(_ARMING_CASES), (
        "every row must be recognised as a call site, or its verdict is untested")
    unarmed = set(missing.get("test_arming_cases.py", []))
    wrong = {name: ("UNARMED" if line in unarmed else "ARMED")
             for (name, _expr, want), line in zip(_ARMING_CASES, rows, strict=True)
             if (line not in unarmed) != want}
    assert not wrong, f"the arming rule disagrees with _ARMING_CASES: {wrong}"


def test_an_expired_bound_is_unarmed_whatever_the_predicate(tmp_path):
    """`timeout=0` is `until=None` ONE KEYWORD TO THE RIGHT.

    `index_rows_after_settle` builds `deadline = time.monotonic() + timeout` and
    returns on `settled or time.monotonic() >= deadline`, so a bound of zero (or
    a negative one) hands back the FIRST catalogue read no matter what `until`
    says. Measured at #12859's review: adding `timeout=0` to BOTH waits of the
    red this PR fixes left 44 passed, byte-identical to the baseline — the pin
    never read the keyword, so the sole detector for the pre-drop race went
    green over the exact read it exists to forbid. This is the same decidable
    one-keyword shape as `until=None` and `until=lambda r: True`.

    ⛔ NOT a refusal of bounds in general: `timeout=120` is the shipped default
    and `timeout=bound_s` is a RUNTIME value this scan cannot evaluate (the real
    site at `tests/test_9079_handle_write_authority.py` passes exactly that), so
    both stay ARMED. Only a DECIDABLE bound of zero or less reds.
    """
    mod = tmp_path / "test_expired_bounds.py"
    mod.write_text(
        "from tests._index_settle import index_rows_after_settle\n"
        "from tests._index_settle import index_kind_absent\n"
        "BOUND_S = 120.0\n"
        "def test_zero(proj):\n"
        "    index_rows_after_settle(proj, until=index_kind_absent, timeout=0)\n"
        "def test_zero_float(proj):\n"
        "    index_rows_after_settle(proj, until=index_kind_absent, timeout=0.0)\n"
        "def test_negative(proj):\n"
        "    index_rows_after_settle(proj, until=index_kind_absent, timeout=-1)\n"
        "def test_default_bound(proj):\n"
        "    index_rows_after_settle(proj, until=index_kind_absent, timeout=120)\n"
        "def test_runtime_bound(proj):\n"
        "    index_rows_after_settle(proj, until=index_kind_absent, timeout=BOUND_S)\n"
        "def test_no_bound(proj):\n"
        "    index_rows_after_settle(proj, until=index_kind_absent)\n",
        encoding="utf-8")
    call_sites, missing = _settle_call_sites([mod])
    assert call_sites == {"test_expired_bounds.py": [5, 7, 9, 11, 13, 15]}, call_sites
    assert missing == {"test_expired_bounds.py": [5, 7, 9]}, missing


def test_two_waits_on_one_line_are_counted_as_two(tmp_path):
    """The per-pair count must count CALLS, not distinct line numbers.

    A `set` of `call_sites` collapsed two waits written on one line into one, so
    deleting the second matched the declared count and the pin stayed green
    (measured at #12856's review cycle 8). Every current site is multi-line, so
    this shape is pinned synthetically rather than waited for.
    """
    mod = tmp_path / "test_synthetic_oneline.py"
    mod.write_text(
        "from tests._index_settle import index_rows_after_settle, "
        "point_index_drops_visible as visible\n"
        "def test_two(proj):\n"
        "    index_rows_after_settle(proj, until=visible); "
        "index_rows_after_settle(proj, until=visible)\n",
        encoding="utf-8")
    assert _settle_site_functions([mod]) == {
        "test_synthetic_oneline.py": {"test_two": 2}}


def test_index_cells_flattens_every_row():
    """`test (g)`'s assertion searches the cells, so the flattening is a contract.

    One string per COLUMN, not per leaf: the dict/list columns stringify whole, and
    that is load-bearing — `not any("pointKind" in c for c in before)` finds the
    retired field inside the stringified kinds map, which a leaf-wise flatten would
    also do but a per-row flatten would not.
    """
    assert index_cells([["Object", ["id"], {"name": ["FULLTEXT"]}]]) == {
        "Object", "['id']", "{'name': ['FULLTEXT']}"}
    assert index_cells([]) == set()
