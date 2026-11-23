"""#14192 — the measurement harness's own verdict must be able to go RED.

The first cut of ``tools/read_wave_latency.py`` recorded the fused order as
``getattr(r, "id", None)`` while ``_tortoise_fts_query`` returns **dicts**, so
every hit recorded ``None`` and the rank-identity verdict compared two lists of
nulls: it reported ``rank_identical: True`` on every change, including one that
permuted every page. A verdict that cannot fail is not a verdict, and it was the
only evidence offered for half of the issue's *Done when*.

These tests pin the two halves of the repair — a real id is recorded from both
return shapes, and ``compare()`` REFUSES a verdict (rather than passing) when an
arm's ids did not record. Origin: #14192 review (reviewer C).
"""
from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path
from typing import NamedTuple

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TOOL = _REPO_ROOT / "tools" / "read_wave_latency.py"


def _load_tool():
    spec = importlib.util.spec_from_file_location("read_wave_latency_14192", _TOOL)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


tool = _load_tool()


def _query(qid: str, order: list, total: float = 100.0) -> dict:
    return {
        "qid": qid,
        "query": "q",
        "total_ms": total,
        "stages": {},
        "members": {},
        "member_calls": {},
        "legs": [],
        "order": order,
    }


def _report(per_query: list) -> dict:
    return {
        "arm": "test",
        "git_sha": "deadbeef",
        "db_uri": "",
        "points": 10,
        "corpus_seed": 42,
        "queries": len(per_query),
        "stage_table": {},
        "member_table": {},
        "legs_declared": {},
        "per_query": per_query,
    }


def test_the_result_id_is_read_from_a_dict_and_from_an_object() -> None:
    """``_tortoise_fts_query`` returns dicts; the harness must not care."""
    assert tool._result_id({"id": "p-1"}) == "p-1"
    assert tool._result_id({"point_id": "p-2"}) == "p-2"

    class Hit:
        id = "p-3"

    assert tool._result_id(Hit()) == "p-3"
    # An unknown shape records None, which ``compare`` then refuses to grade.
    assert tool._result_id({"content": "no id"}) is None


def test_rank_identity_goes_red_when_a_page_is_permuted() -> None:
    """The whole point: same ids, different order → a declared divergence."""
    base = _report([_query("q001", ["a", "b", "c"]), _query("q002", ["d", "e"])])
    after = _report([_query("q001", ["b", "a", "c"]), _query("q002", ["d", "e"])])

    verdict = tool.compare(after, base)
    assert verdict["comparable"] is True
    assert verdict["rank_identical"] is False
    assert verdict["rank_divergent_queries"][0]["qid"] == "q001"
    assert verdict["rank_divergent_queries"][0]["first_divergence_idx"] == 0


def test_a_shorter_page_diverges_at_its_length() -> None:
    """A hit-count change is an ANSWER change — and it must not raise.

    ``limit=10`` is a cap, so a page is ``min(10, hits)`` long and the two arms
    returning a different NUMBER of hits is exactly the class this verdict
    exists to report. A comparing ``zip`` sees no differing PAIR when one page
    is a prefix of the other, so without the length term the divergence index
    reads ``None`` — "nothing diverged" on pages that do. Raising instead would
    be worse: ``main()``'s ``--ab-full-read-from`` arm calls ``compare`` BEFORE
    it writes the artifact, so a raise discards both finished runs.
    """
    base = _report([_query("q001", ["a", "b", "c", "d"])])
    after = _report([_query("q001", ["a", "b", "c"])])

    verdict = tool.compare(after, base)
    assert verdict["comparable"] is True
    assert verdict["rank_identical"] is False
    assert verdict["rank_divergent_queries"][0]["first_divergence_idx"] == 3
    # The other direction is the same divergence at the same index.
    verdict = tool.compare(base, after)
    assert verdict["rank_identical"] is False
    assert verdict["rank_divergent_queries"][0]["first_divergence_idx"] == 3


def test_identical_pages_stay_green() -> None:
    base = _report([_query("q001", ["a", "b", "c"])])
    after = _report([_query("q001", ["a", "b", "c"])])
    assert tool.compare(after, base)["rank_identical"] is True


def test_an_unrecorded_order_refuses_the_verdict_instead_of_passing() -> None:
    """The exact defect: all-None orders compared EQUAL. Must now refuse."""
    base = _report([_query("q001", [None, None, None])])
    after = _report([_query("q001", [None, None, None])])

    verdict = tool.compare(after, base)
    assert verdict["comparable"] is False
    assert "no result ids" in verdict["reason"]
    # …and the same for an empty page, which is the same silence.
    verdict = tool.compare(_report([_query("q001", [])]),
                           _report([_query("q001", [])]))
    assert verdict["comparable"] is False


def test_a_different_corpus_is_not_compared() -> None:
    """A delta across two corpora would be a number pretending to be a change."""
    base = _report([_query("q001", ["a"])])
    after = _report([_query("q001", ["a"])])
    after["points"] = 999
    verdict = tool.compare(after, base)
    assert verdict["comparable"] is False
    assert "corpus" in verdict["reason"] or "query set" in verdict["reason"]


def test_a_tool_only_diff_selects_this_pin():
    """`tools/` is in `NON_PYTHON_PREFIXES`, so a tool-only path is filtered out
    BEFORE the "not matched -> core" fallback and `select()` takes the docs-only
    early return. MEASURED before the `CORE_ALSO` entry at `5c9cfe540761`:

        --changed-files tools/read_wave_latency.py --event pull_request
            -> surfaces: [], full: false

    i.e. this file was NOT selected, so the one diff able to re-introduce the
    defect these pins exist for — recording every fused id as `None`, which is
    how a verdict that can never go red shipped — merged with every pin unrun.
    This pins the entry that closes it, in the file it protects.
    """
    from tools import ci_selection

    manifest = ci_selection.load_manifest()
    selection = ci_selection.select(
        ["tools/read_wave_latency.py"], "pull_request", manifest
    )
    assert "core" in (selection.get("surfaces") or []), selection.get("surfaces")
    files = selection.get("test_files")
    if files == "ALL":
        return
    assert "test_14192_read_wave_tool.py" in files, (
        "a diff to tools/read_wave_latency.py no longer selects its own pins — the "
        "`CORE_ALSO` entry was dropped or the prefix behaviour changed"
    )
    _assert_not_tier1(manifest)


def _assert_not_tier1(manifest: dict) -> None:
    """NON-VACUITY of the surface rows the two selection pins above assert on.

    `tier1` is unioned into EVERY PR selection, so if this file ever joins `tier1` the
    `api`/`core` rows stop being what selects it — and both pins would then pass over a
    LOST arm, which is the silent direction (#14281 review finding 3). Asserted rather
    than left implicit; the idiom is `tests/test_ci_selection.py`'s (#9778 review P2),
    applied in the file whose own non-vacuity it protects.
    """
    assert "tier1" in manifest, (
        "the manifest no longer carries `tier1` — this non-vacuity guard has lost its "
        "subject, so it must be re-derived rather than left passing on nothing")
    assert "test_14192_read_wave_tool.py" not in (manifest["tier1"] or []), (
        "this file joined `tier1` — the `api`/`core` rows are now decorative, so the "
        "selection pins above must be RE-DERIVED rather than assumed still load-bearing")


def test_a_door_diff_selects_this_pin():
    """The other half of the same guard: the door's own diff must select this pin.

    `_door_read_contract()` takes its ENTIRE right-hand side from
    `tortoise/hosted_api.py::search` — `_HOSTED` above, the `door_calls` walk, and the
    `assert len(door_calls) == 1` that says `hosted_api.py::search holds 1 read call`.
    So this file makes a statement about `hosted_api.py`, and a `hosted_api.py`-only
    diff is the one diff class able to red it.

    ⛔ MEASURED before this row existed (#14281 review F1) — a `hosted_api.py`-only
    diff ran the pin NOWHERE:

        select(['tortoise/hosted_api.py'], 'pull_request', m)
            -> surfaces ['api', 'onboarding'], test_14192_read_wave_tool.py ABSENT

    `tortoise/hosted_api.py` is the first `api` SOURCE_PATTERNS entry, and a
    named-surface match REPLACES the `core` fallback in `select()`, so the `core`
    registration alone is asleep on exactly the diff that can break the contract. It is
    not a `SHARED_MODULES` member either (that carries `tortoise/sdk.py`, the OTHER half
    of the same contract, which is why the `sdk` half was already covered).

    The concrete shape this leaves open: hoisting the door's own `lambda` into a nested
    `_do_search()` inside `search` changes what `_owner()` answers, reds
    `assert len(door_calls) == 1`, and — without this row — merges green with the pin
    unrun, so `main` goes red on the next unrelated PR. That is #14279's recurrence
    shape, one layer out.
    """
    from tools import ci_selection

    manifest = ci_selection.load_manifest()
    selection = ci_selection.select(
        ["tortoise/hosted_api.py"], "pull_request", manifest
    )
    assert "api" in (selection.get("surfaces") or []), selection.get("surfaces")
    files = selection.get("test_files")
    if files == "ALL":
        return
    assert "test_14192_read_wave_tool.py" in files, (
        "a diff to tortoise/hosted_api.py no longer selects the pin that asserts on "
        "the door's own read contract — the `api` row in config/ci-surfaces.yml was "
        "dropped, or hosted_api.py stopped resolving to the `api` surface"
    )
    _assert_not_tier1(manifest)


_SDK = _REPO_ROOT / "tortoise" / "sdk.py"
_HOSTED = _REPO_ROOT / "tortoise" / "hosted_api.py"
_READ_NAMES = ("tortoise_fts_query", "_tortoise_fts_query")


def _read_calls(tree: ast.AST) -> list[ast.Call]:
    return [n for n in ast.walk(tree) if isinstance(n, ast.Call)
            and (getattr(n.func, "attr", None) or getattr(n.func, "id", None)) in _READ_NAMES]


class _ResolutionDidNotConverge(RuntimeError):
    """The bind fixpoint hit its pass budget: the resolver REFUSES rather than spins.

    ⛔ REFUSING IS THE FAIL-CLOSED DIRECTION (#14326 P1). A form whose statically possible
    values grow on every pass (`_a = _b + "y"` beside `_b = _a + "z"` — a MUTUAL
    accumulation, which the per-target self-reference staging below cannot see) has no
    finite product at all. Spinning is not a verdict: the pin would burn the shard's whole
    budget and time out, which reads to every consumer as neither green nor red. This error
    is an ordinary test FAILURE naming the shape, so the answer is a red the next author
    can act on.
    """


#: Passes the fixpoint may take before it refuses. A real file resolves a chain in one pass
#: per link; 64 links is far past anything in `tools/`, and the budget is what makes a
#: diverging shape a failed test instead of a hung one.
_FIXPOINT_MAX_PASSES = 64

# ⛔ THE BUDGET ITSELF IS GUARDED (#14326 review P1, measured: raising it to 10**9 makes
# `test_a_BIND_CYCLE_REFUSES_instead_of_spinning` HANG — `rc=124` after 40 s — instead of
# failing, so the row that exercises the budget reports neither green nor red, which is
# verbatim the defect the budget was added to close). A hang is not a verdict, so the
# constant is asserted to be BOUNDED and POSITIVE here, at its own definition.
assert 0 < _FIXPOINT_MAX_PASSES <= 64, (
    f"_FIXPOINT_MAX_PASSES={_FIXPOINT_MAX_PASSES} — the fixpoint budget must stay bounded "
    "and positive: an unbounded one turns the bind-cycle row into a HANG (no verdict at "
    "all), and a zero/negative one refuses every source at the first pass")

#: Combinations the JOIN forms (an f-string's parts, a `%` template's operands, a
#: `.format` call's arguments) may build before the resolver refuses. A real file joins a
#: handful of literals — 4096 combinations is far past anything in `tools/` — and the same
#: argument as `_FIXPOINT_MAX_PASSES` applies: an unbounded product is a timeout, not a red.
_PRODUCT_MAX_COMBINATIONS = 4096

#: ⛔ A PASS BUDGET BOUNDS PASSES, NOT WORK — measured, and this is the second half of the
#: guard (#14326 round 14). `_a = "xx"; _b = _a * 2; _a = _b * 2` is all-static, has no
#: `+=`, and DOUBLES a string every pass: at `_FIXPOINT_MAX_PASSES = 64` it raised
#: `MemoryError` (3 GB `ulimit -v`, one probe) instead of `_ResolutionDidNotConverge`,
#: because pass 40 is a 1 TB string and the budget is never reached. A kill is not a red
#: either, so the resolver caps the PRODUCT too: a name whose statically possible values
#: exceed these bounds makes it REFUSE. Real spellings are nowhere near them — the three
#: files the pin parses hold 2, 7 and 4 passes' worth of small literals — and a read name is
#: never 8 kB long.
_VALUE_MAX_CHARS_PER_NAME = 8192
_VALUE_MAX_PER_NAME = 512


def _str_constants(tree: ast.AST) -> dict[str, frozenset[str]]:
    """Every statically POSSIBLE string literal each name may hold, anywhere in the file.

    ⛔ EVERY STATIC BINDING FORM (#14281 review P1, measured GREEN over the whole pin file).
    This resolved a single-`Name` `Assign` only, with `setdefault` keeping the FIRST
    binding, so each of

        _N: str = "_tortoise_fts_query"          # AnnAssign
        (_N,) = ("_tortoise_fts_query",)         # unpack target
        _N = _M = "_tortoise_fts_query"          # chained
        (_N := "_tortoise_fts_query")            # walrus
        for _N in ("_tortoise_fts_query",):      # `for` target — a bind with NO Assign node
        [_N for _N in ("_tortoise_fts_query",)]  # comprehension target, same

    left the name unresolved, and `getattr(sdk, _N)(…, kind="statement")` — a third reach
    carrying a lever — left the pin GREEN while the grant stood. The `for`/comprehension
    forms are the ones this file's SIBLING guards already carry (`_aliases_of(…,
    loops=True)`, and the mapping seed that feeds `_environ_mutations`), so their absence
    here was an inconsistency inside one file, not a declared boundary (#14279 review F1).
    An annotated or iterated literal is statically visible; it is not the "computed at
    runtime" residual.

    ⛔ THE VALUE IS THE SET OF EVERY BINDING, NOT THE LAST ONE (#14279 review P1, both
    mechanisms measured GREEN with a third reach executing the product read). `ast.walk` is
    breadth-first, not source order, and this map is file-global, so "last bind wins" was a
    flow-insensitive justification with two fail-open consequences:

        _N = "_tortoise_fts_query"; getattr(sdk, _N)(…, kind="statement"); _N = "done"
        _N = "_tortoise_fts_query"; _counts[_N] = "zero"; getattr(sdk, _N)(…)

    The first re-binds the same scratch name — or plants a decoy in an unrelated function —
    and SILENCES the genuine binding; the second binds `_N` through a SUBSCRIPT target,
    though `_N` is never assigned at runtime. Fail-closed is the opposite polarity: a name
    is a reach if ANY value it can statically hold is a read name, so a later bind can only
    ADD candidates. Assignment targets are `Name`/`Tuple`/`List` — the shape the `_bind` in
    `_aliases_of` accepts — because a store into a container or an attribute binds no name.

    Name-to-name and unpack propagation is a FIXPOINT, so a chain of any length resolves:
    `_alias = _N`, `(_pair,) = (_N,)` and `_pair = (sdk, _N)` all leave `_pair` resolving to
    the read name (#14279 review F2/F3 — the container-carried name that the
    executable-parent narrowing in `_is_read_ref` no longer reaches directly).

    ⛔ THE BIND FORMS WITHOUT AN `Assign` NODE ARE NOT EXHAUSTED BY `for` (#14326, each
    MEASURED GREEN at `3b98c08ce2` — a lever reached the read and the pin passed `1 passed`):

        match "_tortoise_fts_query":              # a `match` CAPTURE
            case _N: …getattr(sdk, _N)(…, kind="statement")
        _P = ("_tortoise_fts_query", "x")         # an unpack of a NAME-HELD sequence
        _N, _M = _P                               # (the pairing arm needs a literal VALUE)

    A capture binds a PART OF THE SUBJECT it matches (`case _N:` the whole of it, a mapping
    value, a sequence element, an attribute, `rest=`/`*_r` the remainder) — never a pattern
    literal, since an OR-pattern may not mix a literal with a capture. So the subject is the
    source, bound lazily: the fixpoint re-reads it, which is what catches `match _V: case
    _N:` where `_V` holds the literal, and it is what keeps `case {"read":
    "_tortoise_fts_query"}: _mode = 1` beside a capture in ANOTHER case green. An unpack
    whose sequence is a name/literal has NO statically knowable pairing, so every element
    (nested, starred included) takes the UNION of the sequence's values. All of it is
    over-approximate, which is the fail-closed direction this map exists to take; the
    `for`-target arm above is the same argument one statement earlier.

    RESIDUALS, stated rather than implied: a value COMPUTED at runtime (`"".join(…)`,
    `_N + "_x"` **with an UNRESOLVED `_N`** — a resolvable `_N` DOES resolve, measured, so the
    qualifier is part of the claim), a method call other than `str.format`, a parameter), a
    value read out of a
    **NAME-held** container or an attribute (`_CFG["read"]`, `self._read`) and an unpack
    whose RIGHT-HAND SIDE is an attribute or a call (`_N, _M = cfg.pair`, `_a, _b = pair()`)
    are not resolved here — nothing about them is statically pairable, and they are named
    rather than implied (#14326 review P3). ⛔ A value SELECTED out of a **LITERAL** container
    is NOT residual and IS resolved — `('x', '…')[1]`, `{'k': '…'}['k']`,
    `'…_extra'[:-6]` — because every element is a `Constant` in the source, the argument that
    admitted `+`, `*`, `%` and `.format`; see `_subscript_strings` (round 16, the four shapes
    MEASURED GREEN at `0162ab067e`). The NAME-held form stays declined on purpose: resolving
    it would false-red the provenance lookup the `_is_read_ref` narrowing keeps green.

    ⛔ AN f-STRING WHOSE **CONSTANT FRAGMENT IS THE READ NAME** IS OVER-APPROXIMATED, AND AN
    f-STRING **ASSEMBLED FROM PARTS** IS RESIDUAL — two different classes, and both earlier
    wordings got one of them wrong (#14326 rounds 5 and 8, MEASURED at `6f66ac53` and
    `9762589e`). The `JoinedStr` arms feed every part to the resolver, so a `FormattedValue`
    contributes nothing while a CONSTANT fragment IS resolved: `_N = f"{p}_tortoise_fts_query"`
    yields `{'_tortoise_fts_query'}`, which REDS when the name is executed (`indirect=1`) and
    stays GREEN as an inert label (`_lbl = f"{p}_tortoise_fts_query"; logging.info(_lbl)`,
    measured `refuses=False`, because the `Name` arm requires an executable position).

    ⛔ BUT AN f-STRING WITH A PART THIS FILE CANNOT RESOLVE IS **NOT** CAUGHT — and this note
    used to say that about an ASSEMBLED f-string whose every part DOES resolve, which was false
    and unpinned (#14326 round 21, MEASURED at `ff85c6ab73`: `_p = "_tortoise";
    _N = f"{_p}_fts_query"` now resolves `_N` to
    `{'_fts_query', '_tortoise', '_tortoise_fts_query'}` — the full name among them — so the
    pin REFUSES it; `_joined_strings` closed that in round 13 and the paragraph was never
    corrected). THE TRUE RESIDUAL is a part that does not resolve: `f"{queries[0]}_fts_query"`
    yields only `{'_fts_query'}`, so the assembled name is absent and a lever over `_N` passes.
    That is the declared residual, together with `"".join(…)` (measured: `[]`), a `+` whose
    operand does not resolve (`_X = _N + "_x"` RESOLVES when `_N` does — only an unresolved
    operand is residual, the qualifier the sibling notes keep and this sentence had dropped),
    and a parameter; the fragment over-approximation buys ONE spelling at the cost of a possible
    over-red, it does not close the class. Recorded so the next lane does not read the arm above
    as covering an unresolvable part — and does not read this note as saying a fully-literal
    assembly is open, which it is not.

    ⛔ A PLACEHOLDER-FREE f-STRING IS NOT RESIDUAL, AND NEITHER IS A SELECTION BETWEEN
    LITERALS (#14326 review P6, each MEASURED GREEN at `8f13cb24`): `f"_tortoise_fts_query"`
    parses to `JoinedStr([Constant(...)])` — nothing is computed — and `IfExp`/`BoolOp` only
    SELECT between literal operands, which this file's own `_collection_operands` already
    expands. All three resolve here now, in `_values` AND in `_possible_strings` (the
    inline-operand path). A placeholder-bearing f-string is over-approximated by its
    CONSTANT fragments rather than left unresolved — see the residual note above.

    A literal ITERABLE is not residual, whatever its type: a `for`/comprehension target over
    a tuple, a list, a SET or a DICT resolves, because CPython's grammar gives `iter` as an
    arbitrary `expr` and iteration over a literal is statically visible (#14326 review F2).
    """
    def _self_referential(target: ast.AST, value: ast.AST) -> bool:
        """True when `value` LOADS the name `target` binds.

        ⛔ THESE MUST BE STAGED, NOT FIXPOINTED (#14326 P1, MEASURED: the pin HUNG). With
        `Add` on the bind path, `_s = "_tortoise"; _s = _s + "_fts_query"` fed the fixpoint
        its own output — every pass appended one more `_fts_query` — so `changed` never went
        False and `_str_constants` never returned (`rc=124` after 60 s; `origin/main`'s
        version, which had no `Add` arm, returned `{'_s': '_tortoise'}`). The `+=` arm
        directly below documents this exact hazard and stages for it; a plain `Assign` whose
        value re-reads its own target is the same accumulation written the other way.
        """
        if not isinstance(target, ast.Name):
            return False
        return any(isinstance(node, ast.Name) and node.id == target.id
                   and isinstance(node.ctx, ast.Load) for node in ast.walk(value))

    binds: list[tuple[ast.AST, ast.AST]] = []
    #: ONE list for every bind that must be applied EXACTLY ONCE, carrying its SOURCE
    #: POSITION. `+=` and a self-referential `=` are the same accumulation written two ways,
    #: and a file may mix them in either order, so they are applied in one pass sorted by
    #: position (#14326 round 14). Applying them in two phases was a FAIL-OPEN, measured:
    #: `_S = "_tortoise"; _S += "_fts"; _S = _S + "_query"` resolved to
    #: `{'…','_tortoise_query','_tortoise_fts','_tortoise_query_fts'}` — the string the code
    #: actually builds (`_tortoise_fts_query`) was ABSENT, and a third `kind="statement"`
    #: reach was refused=False, i.e. the pin passed while the lever shipped. Order is the
    #: semantics: staging is an over-approximation only if it runs in the order the runtime
    #: does.
    #: ⛔ THE APPLIED-COUNT CLASS IS A DECLARED RESIDUAL, AND THE DECLARATION IS A
    #: MEASUREMENT (#14326 round 16). A bind inside a loop can run many times, and this pass
    #: applies it ONCE, so a name built over several iterations is resolved only to its
    #: first step:
    #:
    #:     _S = "_tortoise"
    #:     for _p in ("_fts", "_query"): _S = _S + _p   ->  {'_tortoise','_tortoise_fts','_tortoise_query'}
    #:
    #: `_tortoise_fts_query` is ABSENT and a lever-carrying reach over `_S` is NOT refused.
    #: ENUMERATING the count was implemented and REVERTED, because the product is the power
    #: set, not the answer: a 5-element literal loop needs 3,125 spellings (5^5), and a
    #: perfectly ordinary accumulation in the artifact under test
    #:
    #:     _order = ""
    #:     for _col in ("arm_id", "qid", "rank", "score", "leg"):
    #:         _order = _order + _col + ", "
    #:
    #: made `_str_constants` raise `_ResolutionDidNotConverge` — a FALSE RED on inert code,
    #: which is the failure class this whole pin exists to remove (measured: the pin ran 1
    #: failed / 36 passed over that source). A guard that refuses ordinary code is worse than
    #: one that under-approximates: the first reds every PR, the second is named here.
    #:
    #: SO THE CLASS IS NAMED, NOT SILENT, and it is wider than "a loop": the applied count is
    #: not enumerated for a `For` body (`AsyncFor` is the same arm one node type over), a
    #: comprehension's element OR `if` clause, a `while` body, or a loop whose iterable is a
    #: NAME, an attribute, a call or a STARRED literal (`for _p in (*("_fts","_query"),)`).
    #: Each of those is pinned GREEN by
    #: `test_an_accumulation_over_several_ITERATIONS_is_the_DECLARED_residual`, so a later
    #: change that starts resolving them must edit the note instead of leaving it stale.
    #: ⛔ THAT NAME IS SPELLED IN ITS REAL CASE ON PURPOSE (#14326 round 18): this note used to
    #: say `..._several_iterations_...`, which is NO TEST — the real one capitalises
    #: `ITERATIONS`, so the sentence pointed the next author at a red they could never get and
    #: at nothing they could edit. A reference that resolves to no test is the same stale-claim
    #: class as this file's other residual notes, and round 17's reviewer measured it and
    #: passed it; it is corrected here rather than carried.
    #: ⛔ THE `AsyncFor` *BIND* IS PINNED SOMEWHERE ELSE, AND SAYING "EACH IS PINNED BELOW"
    #: WAS FALSE FOR IT (#14326 round 17, MEASURED at `689708686b`: deleting `ast.AsyncFor`
    #: from the bind tuple below left the whole file at **39 passed**). The residual test
    #: cannot carry it — its rows are wrapped in a plain `def run(…)`, where `async for` is a
    #: SyntaxError — so the arm is pinned by the `async for target` assertion in
    #: `test_EVERY_static_binding_form_is_a_READ_reach`, which REDS when that member is
    #: dropped. Both properties are pinned; neither is claimed here as the other's.
    accruals: list[tuple[int, int, str, ast.Name, ast.AST]] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if _self_referential(target, node.value):
                    accruals.append(
                        (node.lineno, node.col_offset, "expr", target, node.value))
                else:
                    binds.append((target, node.value))
        elif isinstance(node, (ast.AnnAssign, ast.NamedExpr)) and node.value is not None:
            if _self_referential(node.target, node.value):
                accruals.append((node.lineno, node.col_offset, "expr",
                                 node.target, node.value))
            else:
                binds.append((node.target, node.value))
        # ⛔ AN AUGMENTED BIND IS A BIND (#14326 review at `a29f3cd8`, MEASURED GREEN at
        # `80d2ab52`): `_N = "_tortoise_"; _N += "fts_query"` then
        # `getattr(sdk, _N)(…, kind="statement")` left the pin GREEN (mentions 2, calls 2,
        # indirect 0) while every OTHER enumerator in this file already carries
        # `AugAssign` (`_rebound_callee_names`, `_extra_bindings`, `_rebound_after`) — an
        # in-file asymmetry, not a declared boundary. It is collected SEPARATELY because its
        # value is the PREVIOUS value PLUS the right-hand side; folding it into `binds` would
        # feed the accumulation its own output forever (see the staging note below).
        elif (isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name)
                and isinstance(node.op, ast.Add)):
            accruals.append((node.lineno, node.col_offset, "aug",
                             node.target, node.value))
        # ⛔ A `for`/COMPREHENSION TARGET IS A BIND TOO (#14279 review F1): the spelling
        # carries no `Assign` node at all, so no assignment arm above could ever see it.
        elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
            binds.append((node.target, node.iter))
        # ⛔ A PARAMETER DEFAULT IS A STATIC BIND TOO (#14326 review P6, MEASURED GREEN):
        # `def _f(_N="_tortoise_fts_query"): getattr(sdk, _N)(…)` binds a literal with no
        # `Assign` node — the last static form without one. Positional defaults align to the
        # TAIL of the positional parameters; keyword-only defaults are per-name with `None`
        # standing for "no default"; `lambda` carries the same `arguments` node. The runtime
        # PARAMETER (a passed-in value) stays a residual below — a default does not.
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            positional = [*node.args.posonlyargs, *node.args.args]
            first = len(positional) - len(node.args.defaults)
            for index, default in enumerate(node.args.defaults):
                binds.append((ast.Name(id=positional[first + index].arg,
                                       ctx=ast.Store()), default))
            for index, kdefault in enumerate(node.args.kw_defaults):
                if kdefault is not None:
                    binds.append((ast.Name(id=node.args.kwonlyargs[index].arg,
                                           ctx=ast.Store()), kdefault))
        # ⛔ SO IS A `match` CAPTURE (#14326 same-class, MEASURED GREEN at 3b98c08):
        # `match "_tortoise_fts_query": case _N: getattr(sdk, _N)(…, kind="statement")`
        # bound nothing, so a lever reached the read and the pin passed 1 passed — the
        # `for`-target arm's own rationale, one statement later in the language.
        # ⛔ THE SUBJECT IS THE ONLY SOURCE (#14326 review P2, cycle 2 — MEASURED GREEN for a
        # dict/set-literal subject, and a FALSE RED when pattern literals were credited): a
        # capture binds a PART of the subject — the whole subject (`case _N:`), a mapping
        # value, a sequence element, an attribute, the remainder (`rest=`/`*_r`) — and a
        # pattern LITERAL never binds a name (an OR-pattern may not mix a literal with a
        # capture). Crediting pattern literals was therefore both unsound and cross-branch:
        # `case {"read": "_tortoise_fts_query", "n": _v}` red `_v` on a KEY it can never
        # receive. The subject's whole literal is the source, so a capture may be any string
        # the subject holds — over-approximate, which is the declared polarity here.
        elif isinstance(node, ast.Match):
            for case in node.cases:
                for capture in ast.walk(case.pattern):
                    name = getattr(capture, "name", None) or getattr(capture, "rest", None)
                    # ⛔ NO `isinstance(name, str)` GUARD (#14326 round 6): `MatchAs.name` is
                    # `identifier?` and `MatchMapping.rest` an identifier, so a non-str name
                    # cannot reach here, and `ast.Name(id=None)` is never read back by
                    # `_possible_strings` anyway — the guard was a clause no mutant could
                    # red, and this file deletes those.
                    if name is None:
                        continue  # `case 1:` / `case _:` bind nothing
                    binds.append((ast.Name(id=name, ctx=ast.Store()), node.subject))
        # ⛔ A `with` TARGET IS A BIND TOO (#14326 round 20, MEASURED at `57735ab47`: `_r =
        # "_tortoise_fts_query"` then `with _r as _N: getattr(sdk, _N)(…, kind="statement")`
        # left `_N` UNRESOLVED — mentions 2 / calls 2 / indirect 0, i.e. the census saw only
        # the pin's OWN two reads and the lever contributed nothing (restated #14326 round 22:
        # "0 / 0 / 0" was the lever's DELTA, not the totals, and this file states totals) —
        # while the `for`/`match`/parameter-default spellings of the same bind all
        # landed on `indirect=1`. The target binds what `__enter__` RETURNS, which is not
        # statically knowable, so the CONTEXT EXPRESSION is the over-approximation: the same
        # rule `_aliases_of` applies to this node.
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                if item.optional_vars is not None:
                    binds.append((item.optional_vars, item.context_expr))
    out: dict[str, set[str]] = {}

    def _values(value: ast.AST) -> set[str]:
        """Every string `value` can statically hold."""
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            return {value.value}
        if isinstance(value, ast.Name):
            return set(out.get(value.id, ()))  # a chain resolves on the next pass
        # ⛔ A WALRUS ON THE RIGHT-HAND SIDE IS THE SAME BIND AS `a = b = literal`
        # (#14326 round 8, MEASURED GREEN at `9762589e`): `_N = (_M := "_tortoise_fts_query")`
        # left `_N` UNRESOLVED while `_str_constants` already treats `NamedExpr` as a bind
        # (`binds.append(…)` above), so the asymmetry opened the pin: with a third read
        # `getattr(sdk, _N)(…, kind="statement")` the census stayed at mentions 2 / calls 2 /
        # indirect 0, i.e. `intentional_keep` shipped a lever the door does not name. The same
        # value written `_N = _M = …` reds correctly.
        if isinstance(value, ast.NamedExpr):
            return _values(value.value)
        # ⛔ A SPLATTED LITERAL IS THAT LITERAL (#14326 review P5, MEASURED GREEN):
        # `_P = [sdk, *("_tortoise_fts_query",)]; getattr(*_P)(…)` left `_P` unresolved, and
        # the sibling `_possible_strings` in this file already carries this arm (#14279 review
        # F3) — an in-file asymmetry, not a declared boundary.
        if isinstance(value, ast.Starred):
            return _values(value.value)
        if isinstance(value, (ast.Tuple, ast.List)):
            # A `for`/COMPREHENSION target over a literal sequence binds each element in
            # turn, so every element is a possible value — the fail-closed reading, and the
            # one that makes `for _N in ("_tortoise_fts_query",)` resolvable at all.
            return set().union(*(_values(elt) for elt in value.elts))
        # ⛔ A SET/DICT LITERAL IS THE SAME BIND SPELLING (#14326 review F2, measured GREEN
        # without this): CPython's grammar gives `For.iter` and `comprehension.iter` as an
        # arbitrary `expr`, so `for _N in {"_tortoise_fts_query"}` and
        # `for _N in {"_tortoise_fts_query": 1}` are binds with no `Assign` node whose
        # value is statically visible — not the "computed at runtime" residual. Iterating a
        # dict yields its KEYS and a `**spread` key is `None`; a CAPTURE over a mapping reads
        # its VALUES (`case {"read": _N}`) and `rest=` the remaining KEYS, so BOTH sides are
        # candidates here. The union deliberately over-approximates the iteration spelling
        # (a value can never be yielded by `for _N in {…}:`) — the fail-closed direction this
        # map exists to take, and what keeps `match _m: case {"read": _N}:` resolvable.
        if isinstance(value, ast.Set):
            return set().union(*(_values(elt) for elt in value.elts))
        if isinstance(value, ast.Dict):
            sides = [*value.keys, *value.values]
            return set().union(*(_values(item) for item in sides if item is not None))
        # ⛔ A SELECTION BETWEEN LITERALS IS STATIC, AND A PLACEHOLDER-FREE f-STRING IS A
        # CONSTANT (#14326 review P6, MEASURED GREEN without these three arms):
        #     getattr(sdk, f"_tortoise_fts_query")(…, kind="statement")
        #     getattr(sdk, ("_tortoise_fts_query" if q else "x"))(…, kind="statement")
        #     _N = "_tortoise_fts_query" if q else "x"; getattr(sdk, _N)(…, kind="statement")
        #     _N = q or "_tortoise_fts_query";            getattr(sdk, _N)(…, kind="statement")
        # all left the pin GREEN (mentions 2, calls 2, indirect 0). None is the stated
        # "computed at runtime" residual: `f"…"` with no `FormattedValue` parses to a
        # `JoinedStr` holding ONE `Constant`, and `IfExp`/`BoolOp` merely SELECT between
        # literals — this file's own `_collection_operands` already expands exactly those two
        # node types, so their absence here was an in-file asymmetry rather than a boundary
        # (the same argument this file used for `Starred`, `for` targets and set/dict).
        if isinstance(value, ast.IfExp):
            return _values(value.body) | _values(value.orelse)
        if isinstance(value, ast.BoolOp):
            return set().union(*(_values(operand) for operand in value.values))
        if isinstance(value, ast.JoinedStr):
            # ⛔ EVERY CONSTANT FRAGMENT IS STATICALLY KNOWN, AND A `FormattedValue` PART
            # CONTRIBUTES NOTHING OF ITS OWN — it does NOT disable the whole arm (#14326
            # round 5, MEASURED at `6f66ac53`): `f"{p}_tortoise_fts_query"` therefore
            # yields `{'_tortoise_fts_query'}`, which REDS when the name is executed and
            # leaves an inert label GREEN (the `Name` arm needs an executable position).
            # This comment previously claimed the placeholder case "resolves to nothing",
            # which contradicted the code and the residual list — the over-approximation is
            # the fail-closed direction and is deliberate; narrowing it to `Constant`-only
            # would fail OPEN on a runtime-computed read name.
            # ⛔ AND THE PARTS ARE JOINED AS WELL AS UNIONED (#14326 round 13, MEASURED GREEN
            # at `b6be33738f` while every `+` spelling of the same string was refused) — see
            # `_joined_strings`; the union is kept beside it for the placeholder case.
            _parts = [_values(part) for part in value.values]
            return set().union(*_parts) | _joined_strings(_parts)
        # The expression an f-string part interpolates. `format_spec` is itself a `JoinedStr`
        # of parts, so a `{x:>10}` spec can contribute only its own literal fragments.
        if isinstance(value, ast.FormattedValue):
            return (_values(value.value)
                    | (_values(value.format_spec) if value.format_spec is not None else set()))
        # ⛔ `%` FORMATTING AND `str.format` ARE THE SAME STATIC LITERAL (#14326 round 13,
        # MEASURED GREEN at `b6be33738f`, each at `m=2 c=2 i=0` while the `+` spelling of the
        # same string was refused):
        #     _N = "%s_fts_query" % "_tortoise"                    # bound, then read
        #     getattr(sdk, "%s_fts_query" % "_tortoise")(…)        # inline
        #     _N = "_tortoise_fts_query".format()                   # bound, then read
        # Semgrep's constant propagation folds Python f-strings, `%` formatting, `.format`
        # and `+` over constants into a plain string, which is the convergent standard for a
        # guard that must not fail open; and this is the argument this file already accepted
        # for a literal-only `+` — nothing it cannot SEE statically is being computed. Only
        # resolvable operands enter, so `_N + "_x"` (a parameter) stays the declared residual.
        if isinstance(value, ast.BinOp) and isinstance(value.op, ast.Mod):
            _operands = _mod_operands(value.right, _values)
            return _percent_strings(_values(value.left), _operands) if _operands else set()
        # ⛔ THE `.format` ARM ABOVE, ONE METHOD AND ONE DELIVERY SHORT (#14326 round 15, the
        # four shapes MEASURED GREEN at `1044c2a0ea5d` — `refuses=False`, `m=2 c=2 i=0`, a
        # third `kind="statement"` reach executing the product read):
        #     "{k}".format(**{"k": …})"  "{k}".format_map({"k": …})   both BOUND and INLINE
        # The class was enumerated by a HARD-CODED method name, so `str.format_map` — the
        # same template-plus-mapping build under the other method name — matched no arm at
        # all, and the `**` spread was DROPPED on sight ("keys are not statically visible",
        # true of a name-held mapping and false of a display). `_format_call_strings` is now
        # the single entry point for BOTH resolvers, so neither can carry one spelling and
        # not the other — the asymmetry this file has paid for three times (`+`, `%`,
        # `.format`).
        if (isinstance(value, ast.Call) and isinstance(value.func, ast.Attribute)
                and value.func.attr in _FORMAT_SPELLINGS):
            return _format_call_strings(value, _values)
        # ⛔ THE SAME `+` ARM, ON THE BIND PATH (#14326 round 12, MEASURED at `c380075b4`):
        # `_N = "_tortoise_" + "fts_query"; getattr(sdk, _N)(…, kind="statement")` left the pin
        # GREEN while the INLINE spelling of the same sum was refused — the verdict was decided
        # by whether the sum was bound first. `_possible_strings` grew the `BinOp`/`Add` arm in
        # round 9, but `_values` (the BIND resolver) did not, so every round-9/10 `+` row is
        # INLINE and nothing exercised this path. Both operands here are `Constant`s (or a name
        # bound to one), which is NOT the declared residual (`_N + "_x"`, an UNRESOLVABLE
        # operand) — that wording is what made the gap read as covered. Same PRODUCT semantics
        # as `_possible_strings`: a union is wrong in both directions (round 10).
        if isinstance(value, ast.BinOp) and isinstance(value.op, ast.Add):
            _left, _right = _values(value.left), _values(value.right)
            return {a + b for a in _left for b in _right}
        # ⛔ AND THE REPETITION THAT BUILDS THE NAME (#14326 review at `a29f3cd8`, MEASURED
        # GREEN at `80d2ab52`): `_N = "_tortoise_fts_query" * 1` bound nothing, so
        # `getattr(sdk, _N)(…, kind="statement")` left the pin GREEN — the inline sibling is
        # refused through `_possible_strings`, which made this an in-file asymmetry exactly
        # like the `+` arm above one round earlier.
        if isinstance(value, ast.BinOp) and isinstance(value.op, ast.Mult):
            return (_repetitions(_values(value.left), _int_literals(value.right))
                    | _repetitions(_values(value.right), _int_literals(value.left)))
        # ⛔ THE SAME SUBSCRIPT ARM AS `_possible_strings` (#14326 round 16, MEASURED GREEN at
        # `0162ab067e`): `_N = ('x', '_tortoise_fts_query')[1]` and
        # `_N = '_tortoise_fts_query_extra'[:-6]` bound nothing, so a `getattr(sdk, _N)` reach
        # shipped. Values are SELECTED out of a literal container — nothing is computed that
        # the source does not spell — so this is not the "read out of a container" residual
        # (that one is a NAME-held mapping: `_CFG["read"]`, declined by the helper).
        if isinstance(value, ast.Subscript):
            return _subscript_strings(value, _values)
        return set()

    def _store_names(target: ast.AST) -> list[ast.Name]:
        """Every NAME a (possibly NESTED) unpack target actually binds.

        `(_a, (_N, *_rest))` binds all three (#14326 review P1); `box[_i]` and `obj._n`
        bind no name — the names inside them are LOADs — so only
        `Name`/`Starred`/`Tuple`/`List` are walked.
        """
        if isinstance(target, ast.Name):
            return [target]
        if isinstance(target, ast.Starred):
            return _store_names(target.value)
        if isinstance(target, (ast.Tuple, ast.List)):
            return [name for element in target.elts for name in _store_names(element)]
        return []

    def _check_budget(name: str, have: set[str]) -> None:
        """Refuse when a name's statically possible values outgrow the product budget.

        ⛔ THIS IS WHAT MAKES THE DIVERGENCE A RED (#14326 round 14, MEASURED): without it
        `_a = "xx"; _b = _a * 2; _a = _b * 2` runs to `MemoryError`, which is a killed
        shard — neither green nor red — rather than a failed test.
        """
        if len(have) > _VALUE_MAX_PER_NAME or sum(map(len, have)) > _VALUE_MAX_CHARS_PER_NAME:
            raise _ResolutionDidNotConverge(
                f"`{name}` has more statically possible values than the resolver will hold "
                f"(> {_VALUE_MAX_PER_NAME} spellings or > {_VALUE_MAX_CHARS_PER_NAME} "
                "characters total). A bind that grows without a finite product is a MUTUAL "
                "accumulation, and the pin cannot certify it — so it REFUSES rather than "
                "allocate its way to a kill.")

    def _bind(target: ast.AST, value: ast.AST) -> bool:
        """Add every possible value of `value` to `target`; True when the map GREW."""
        if isinstance(target, ast.Name):
            have = out.setdefault(target.id, set())
            before = len(have)
            have |= _values(value)
            _check_budget(target.id, have)
            return len(have) != before
        # ⛔ AN UNPACKED LITERAL PAIRS ELEMENT-WISE (`(_N,) = ("…",)`), so the target shape
        # and the value shape are walked together — the flat form treated the tuple VALUE as
        # the literal and bound nothing (measured GREEN). Indexed rather than `zip`: the
        # length guard above makes `zip(strict=True)` a check that could never fire, and
        # ruff's B905 requires it (#14279 review P3).
        if (isinstance(target, (ast.Tuple, ast.List))
                and isinstance(value, (ast.Tuple, ast.List))
                and len(target.elts) == len(value.elts)
                # ⛔ A STARRED TARGET IS NOT PAIRABLE (#14326 review P4, MEASURED GREEN):
                # `(*_r, _b) = ("_tortoise_fts_query", "x")` has equal LENGTHS, so the
                # pairing arm took it and `_bind(Starred)` bound nothing — `_r` stayed
                # unresolved and a lever reached the read with the pin green. A star makes
                # the element positions depend on the RUNTIME length, so fall through to the
                # union arm below, which walks `_store_names`.
                and not any(isinstance(item, ast.Starred) for item in ast.walk(target))):
            grew = False
            for index in range(len(target.elts)):
                grew = _bind(target.elts[index], value.elts[index]) or grew
            return grew
        # ⛔ …AND WHEN THE SEQUENCE IS NOT A LITERAL THE PAIRING IS UNKNOWABLE, so every
        # element gets the UNION (#14326, MEASURED GREEN at 3b98c08):
        # `_P = ("_tortoise_fts_query", "x"); _N, _M = _P` left `_N` unresolved because
        # the pairing arm above requires a `Tuple`/`List` VALUE, and the unpack is the P2
        # class — a name carried through a container — one direction further out. Every
        # element gets every value the sequence can hold: over-approximate (fail-closed),
        # never silent.
        if isinstance(target, (ast.Tuple, ast.List)):
            values = _values(value)
            grew = False
            for element in target.elts:
                # ⛔ A NESTED TARGET IS AN UNPACK TOO (#14326 review P1, MEASURED GREEN):
                # `(_a, (_N, _o)) = _P` left `_N` unresolved when only the top level was
                # read. Every STORE name under the element takes the union — a subscript or
                # attribute target binds no name, so only `Name`/`Starred`/sequence shapes
                # are walked (a name inside `box[_i] = …` is a LOAD, not a bind).
                for name in _store_names(element):
                    have = out.setdefault(name.id, set())
                    before = len(have)
                    have |= values
                    _check_budget(name.id, have)
                    grew = len(have) != before or grew
            return grew
        return False

    def _fixpoint() -> None:
        """Propagate every ordinary bind until nothing grows, within a bounded budget.

        ⛔ THE BUDGET IS THE VERDICT (#14326 P1): an unbounded `while changed` over a shape
        whose product grows every pass never terminates, and a hung pin answers nothing.
        """
        changed = True
        passes = 0
        while changed:  # fixed point: a chain of any length resolves in one call
            passes += 1
            if passes > _FIXPOINT_MAX_PASSES:
                raise _ResolutionDidNotConverge(
                    f"the bind resolver did not converge in {_FIXPOINT_MAX_PASSES} passes: "
                    "a statically possible value is growing every pass, which is a MUTUAL "
                    "accumulation (`_a = _b + 'x'` beside `_b = _a + 'y'`) and has no finite "
                    "product. The pin cannot certify this source, so it REFUSES rather than "
                    "spin — an unbounded fixpoint is a timeout, not a red.")
            changed = False
            for target, value in binds:
                if _bind(target, value):
                    changed = True

    _fixpoint()
    # ⛔ `+=` IS APPLIED ONCE, AGAINST THE PRE-AUGMENT VALUES (#14326 review at `a29f3cd8`):
    # `_N += "x"` makes `_N` hold the concatenation of what it held BEFORE with `"x"`, so the
    # product is taken from the map as it stands here. Re-entering it per pass would feed the
    # accumulation its own output (`_a` + `x` + `x` + …) and would never converge — the growth
    # is why this is STAGED, not a precision choice. The propagation below is what lets
    # `_M = _N` (written after the `+=`) resolve to the augmented value.
    # ⛔ A SELF-REFERENTIAL BIND IS APPLIED ONCE, EXACTLY LIKE `augments` (#14326 P1): the
    # product is taken against the values the name held BEFORE this bind, so an accumulation
    # is bounded instead of feeding the fixpoint its own output. `_values` already computes
    # the product for this shape (`_s + "_x"` -> every value of `_s`, suffixed) — prefixing
    # `acc` again here would DOUBLE-COUNT the left operand (measured while writing this:
    # `{'_tortoise_tortoise_fts_query', '_tortoise'}` for `_s = "_tortoise"; _s = _s + …`).
    # Over-approximate on purpose: the earlier values stay in the set, which is this map's
    # declared fail-closed polarity (a later bind can only ADD candidates), and an INERT use
    # of the name stays green because the `Name` arm still requires an executable position.
    # ⛔ ONE PASS, IN SOURCE ORDER (#14326 round 14). Every bind whose value can depend on
    # the map is applied exactly ONCE — that is what bounds an accumulation — but the ORDER
    # between `+=` and a self-referential `=` is the code's own order, because the product
    # differs by it: measured, the two-phase form dropped the string the source actually
    # builds and the pin went GREEN on a lever-carrying third reach. `_values` already
    # computes the product against the map as it stands for BOTH spellings, so the only
    # difference between them here is that `+=` names only the RHS while `=` carries the
    # whole expression.
    for _lineno, _col, kind, _target, _value in sorted(
            accruals, key=lambda item: (item[0], item[1])):
        _acc = out.get(_target.id)
        if _acc is None:
            _acc = out[_target.id] = set()
        if kind == "aug":
            _bases = set(_acc)
            _acc |= {base + suffix for base in _bases for suffix in _values(_value)}
        else:
            _acc |= _values(_value)
        _check_budget(_target.id, _acc)
    _fixpoint()
    return {name: frozenset(values) for name, values in out.items() if values}


def _parents_of(tree: ast.AST) -> dict[int, ast.AST]:
    parents: dict[int, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[id(child)] = node
    return parents


def _int_literals(node: ast.AST) -> frozenset[int]:
    """Every INT `node` can statically be — a literal, or a literal SELECTION of one.

    The companion of `_possible_strings` for the ONE operand a sequence REPETITION needs.
    A count bound to a NAME is not resolved here (it is not in the string map); that is the
    stated residual, and it is the same class as `_N + "_x"`: an operand whose value this
    file cannot see statically.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, int) and not isinstance(
            node.value, bool):
        return frozenset((node.value,))
    if isinstance(node, ast.IfExp):
        return _int_literals(node.body) | _int_literals(node.orelse)
    if isinstance(node, ast.BoolOp):
        return frozenset().union(*(_int_literals(operand) for operand in node.values))
    # ⛔ A SIGN IS NOT PART OF THE LITERAL (#14326 round 16, MEASURED GREEN): `-1` parses as
    # `UnaryOp(USub, Constant(1))`, NOT as `Constant(-1)`, so `('x', '_tortoise_fts_query')[-1]`
    # and `'_tortoise_fts_query_extra'[:-6]` were declined by every caller of this helper —
    # the NEGATIVE half of the same static spelling. The sign is applied here so the two
    # subscript arms (and `_repetitions`' count) see one integer per literal.
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        inner = _int_literals(node.operand)
        return frozenset(-value if isinstance(node.op, ast.USub) else value
                         for value in inner)
    return frozenset()


def _repetitions(strings: frozenset[str], counts: frozenset[int]) -> frozenset[str]:
    """The strings a `s * n` can be — the EXACT set, not an over-approximation.

    ⛔ `"_tortoise_fts_query" * 1` IS THE READ NAME (#14326 review at `a29f3cd8`, MEASURED
    GREEN at `80d2ab52` on BOTH the bind path and inline): `_possible_strings` carried the
    `+` arm only, so a repetition built the name and the census stayed at mentions 2 / calls
    2 / indirect 0. Expanding to `{s * n}` for the static counts keeps the precision the `+`
    arm's PRODUCT semantics bought: `"print" * 2` is `"printprint"` and must NOT drop
    `print` from the allow-lists.
    """
    if not strings or not counts:
        return frozenset()
    # ⛔ ONE `*` CAN ALLOCATE THE WHOLE BUDGET (#14326 round 14): `_s * 100000000` is a
    # single token, so a cap applied only per pass sees nothing to stop. Refuse before the
    # product is built, not after.
    if any(len(s) * max(counts) > _VALUE_MAX_CHARS_PER_NAME for s in strings):
        raise _ResolutionDidNotConverge(
            f"a static repetition would build a string past "
            f"_VALUE_MAX_CHARS_PER_NAME={_VALUE_MAX_CHARS_PER_NAME}: the pin cannot census "
            "a value that size, so it REFUSES rather than allocate it.")
    return frozenset(s * n for s in strings for n in counts)


def _string_products(option_sets) -> list[tuple[str, ...]]:
    """Every ordered combination of the per-operand string sets.

    The shared core of the three JOIN forms below (an f-string's parts, a ``%`` template's
    operands, ``str.format``'s arguments). Returning ``[]`` when ONE operand is unresolvable
    is what keeps the declared residual (``_N + "_x"`` with a parameter ``_N``) out of the
    census without a guard clause restating it (#14326 round 11).

    ⛔ THE PRODUCT IS BUDGETED, like the bind fixpoint (#14326 round 13): an unbounded
    cartesian product over a pathological source is a HANG, and a hung pin answers nothing —
    the same defect `_FIXPOINT_MAX_PASSES` refuses one function up. A source whose parts
    multiply past the budget has no useful verdict to give here, so it REFUSES.
    """
    combos: list[tuple[str, ...]] = [()]
    for options in option_sets:
        if not options:
            return []
        if len(combos) * len(options) > _PRODUCT_MAX_COMBINATIONS:
            raise _ResolutionDidNotConverge(
                f"the string JOIN resolver would build more than "
                f"{_PRODUCT_MAX_COMBINATIONS} combinations from {len(option_sets)} operands "
                "(an f-string, a `%` template or a `.format` call whose parts are each a "
                "selection of literals): no useful verdict comes from materialising it, so "
                "the pin REFUSES rather than spin — an unbounded product is a timeout, not "
                "a red.")
        combos = [(*combo, option) for combo in combos for option in sorted(options)]
    return combos


def _joined_strings(part_sets) -> set[str]:
    """Every string an f-string builds by JOINING its statically resolvable parts.

    ⛔ THE PARTS ARE JOINED, NOT ONLY UNIONED (#14326 round 13, MEASURED GREEN at
    `b6be33738f`: ``getattr(sdk, f"{'_tortoise_fts_query'}")(…, kind="statement")`` passed
    the pin at ``m=2 c=2 i=0``, as did ``f"{'_tortoise_'}{'fts_query'}"``). A
    ``FormattedValue`` part contributed nothing of its own, so the spelling that builds the
    name ENTIRELY out of literals — nothing is computed at runtime, the same argument this
    file accepted for a literal-only ``+`` — was invisible to both resolvers. The callers
    keep the old UNION beside this join, because that over-approximation is what reds a
    placeholder part (``f"{p}_tortoise_fts_query"``) and is deliberate.
    """
    return {"".join(combo) for combo in _string_products(part_sets)}


def _mod_operands(node: ast.AST, resolve) -> list | None:
    """Candidate right-hand operands of a ``%`` template, or ``None`` when unresolvable.

    The three shapes CPython's ``%`` accepts from a statically visible operand: one value
    (``"%s_fts_query" % p``), a literal sequence (``"%s_%s" % (a, b)``, PAIRED element-wise
    like ``_bind``'s unpack arm — a union would format the wrong arity) and a literal mapping
    (``"%(k)s" % {"k": v}``, which needs each value to be a singleton to name its key).
    """
    if isinstance(node, (ast.Tuple, ast.List)):
        return [tuple(combo) for combo in
                _string_products([resolve(elt) for elt in node.elts])] or None
    if isinstance(node, ast.Dict):
        keys = [key.value if isinstance(key, ast.Constant) and isinstance(key.value, str)
                else None for key in node.keys]
        values = [resolve(value) for value in node.values]
        if any(key is None for key in keys) or any(len(value) != 1 for value in values):
            return None
        return [{key: next(iter(value)) for key, value in zip(keys, values, strict=True)}]
    return sorted(resolve(node)) or None


def _percent_strings(templates, operands) -> set[str]:
    """Every string a literal-only ``%`` template builds from ``operands``."""
    out: set[str] = set()
    for template in templates:
        for operand in operands:
            try:
                rendered = template % operand
            except (TypeError, ValueError, KeyError, IndexError, AttributeError):
                continue
            if isinstance(rendered, str):
                out.add(rendered)
    return out


#: The two `str` FORMATTING methods that build a string out of a template and a mapping:
#: `.format(…)` takes keywords (and one `**` spread) and `.format_map(m)` takes the mapping
#: positionally. Both fill the template's `{field}`s from the same place, so they are ONE
#: spelling of the same static literal — see `_format_call_strings`.
_FORMAT_SPELLINGS = frozenset({"format", "format_map"})


def _mapping_option_sets(node: ast.AST, resolve) -> list[dict[str, str]] | None:
    """Every statically possible KEY→VALUE mapping a DICT DISPLAY can deliver.

    ⛔ THIS IS WHAT A `**` SPREAD AND `.format_map` NEED, AND THE TWO PATHS THAT READ A
    MAPPING ALREADY RESOLVE IT (#14326 round 15, EACH MEASURED GREEN at
    `1044c2a0ea5d` — `refuses=False`, `m=2 c=2 i=0`, a third reach executing the read):

        _N = "{k}".format(**{"k": "_tortoise_fts_query"})       # BOUND,  GREEN
        getattr(sdk, "{k}".format(**{"k": "_tortoise_fts_query"}))(…)   # INLINE, GREEN
        _N = "{k}".format_map({"k": "_tortoise_fts_query"})     # BOUND,  GREEN
        getattr(sdk, "{k}".format_map({"k": "_tortoise_fts_query"}))(…) # INLINE, GREEN

    `_format_operands` BAILED on `keyword.arg is None` ("its keys are not statically
    visible"), which is true of a NAME-held mapping and false of a display — and
    `_mapping_keys_in_keywords` resolves the display for a namespace `update(**{…})`, so the
    verdict was decided by which call the same display was handed to. `.format_map` matched
    no arm at all: the formatting class round 13 closed was enumerated by METHOD NAME and
    stopped one method short, the same shape `%` → `.format` cost in that round.

    Only a DISPLAY is read: a `**` spread of a NAME-held mapping, a `.format_map` over one,
    and a pair ITERABLE stay the stated residual (nothing about their pairing is statically
    visible here), and a display whose keys or values do not resolve yields `None` — the
    caller then treats the whole call as unresolved rather than guessing.
    """
    if not isinstance(node, ast.Dict):
        return None
    options: list[dict[str, str]] = [{}]
    for key, value in zip(node.keys, node.values, strict=True):
        if key is None:  # a `**spread` INSIDE the display, one level out from the caller
            inner = _mapping_option_sets(value, resolve)
            if inner is None:
                return None
            merged = [{**left, **right} for left in options for right in inner]
        else:
            keys = resolve(key)
            if len(keys) != 1:  # a computed key has no static NAME to pair with
                return None
            name = next(iter(keys))
            values = resolve(value)
            if not values:
                return None
            merged = [{**option, name: text} for option in options
                      for text in sorted(values)]
        if len(merged) > _PRODUCT_MAX_COMBINATIONS:
            # the same refusal `_string_products` makes: an unbudgeted product is a hang,
            # and a hung pin answers neither green nor red.
            raise _ResolutionDidNotConverge(
                f"a mapping display would build more than {_PRODUCT_MAX_COMBINATIONS} "
                "key→value pairings: no useful verdict comes from materialising it, so the "
                "pin REFUSES rather than spin")
        options = merged
    return options


def _format_operands(args, keywords, resolve,
                     map_spelling: bool = False) -> tuple[list, list] | None:
    """The ``str.format``/``str.format_map`` call's (positional combos, mappings), or ``None``.

    ``map_spelling`` is `.format_map`, whose ONE positional argument IS the mapping and which
    takes no keywords at all — so its positional half is empty by construction, not by an
    argument being unresolvable.
    """
    if map_spelling:
        if len(args) != 1 or keywords:
            return None
        mappings = _mapping_option_sets(args[0], resolve)
        return ([()], mappings) if mappings else None
    positional = [tuple(combo) for combo in
                  _string_products([resolve(arg) for arg in args])]
    mappings: list[dict[str, str]] = [{}]
    for keyword in keywords:
        if keyword.arg is None:
            spread = _mapping_option_sets(keyword.value, resolve)
            if spread is None:
                return None  # a `**mapping` whose contents are not statically visible
            mappings = [{**left, **right} for left in mappings for right in spread]
        else:
            values = resolve(keyword.value)
            if not values:
                return None
            mappings = [{**mapping, keyword.arg: text} for mapping in mappings
                        for text in sorted(values)]
    if (args and not positional) or (keywords and not mappings):
        return None
    return positional or [()], mappings or [{}]


def _format_strings(templates, operands) -> set[str]:
    """Every string a literal ``"…".format(…)`` builds from ``operands``."""
    if not operands:
        return set()
    positional_options, mapping_options = operands
    out: set[str] = set()
    for template in templates:
        for positional in positional_options:
            for mapping in mapping_options:
                try:
                    rendered = template.format(*positional, **mapping)
                except (IndexError, KeyError, ValueError, AttributeError, TypeError):
                    continue
                if isinstance(rendered, str):
                    out.add(rendered)
    return out


def _format_call_strings(call: ast.Call, resolve) -> set[str]:
    """Every string a literal ``template.format(…)`` / ``template.format_map(…)`` builds.

    ⛔ ONE ENTRY POINT FOR BOTH RESOLVERS (#14326 round 15). The two recursions are separate
    (`_values` for a BIND, `_possible_strings` for an INLINE operand) and this file has paid
    for fixing one and leaving the other open three times — `+`, `%`, `.format`. Sharing the
    call keeps the `.format_map` spelling from being added to one and not the other, and the
    `map_spelling` flag is the ONLY difference between the two methods here.
    """
    operands = _format_operands(call.args, call.keywords, resolve,
                               map_spelling=call.func.attr == "format_map")
    if not operands:
        return set()
    return _format_strings(resolve(call.func.value), operands)


def _constant_slice_bounds(slice_node: ast.Slice) -> tuple[int | None, int | None, int | None] | None:
    """`(start, stop, step)` when every bound is a Constant int or OMITTED; else None.

    An omitted bound is `None` and is statically meaningful (`value[:-6]`); a non-constant
    one is not, and the caller must then fall back to the fail-closed union.
    """
    bounds: list[int | None] = []
    for part in (slice_node.lower, slice_node.upper, slice_node.step):
        if part is None:
            bounds.append(None)
            continue
        # ⛔ VIA `_int_literals`, NOT AN ISINSTANCE ON `Constant` (#14326 round 16): a NEGATIVE
        # bound is a `UnaryOp`, so `value[:-6]` carried no constant bound at all and the arm
        # declined the spelled slice. A multi-valued bound (a selection) is not a constant.
        values = _int_literals(part)
        if len(values) != 1:
            return None
        bounds.append(next(iter(values)))
    if bounds[2] == 0:  # a runtime ValueError, not a string
        return None
    return (bounds[0], bounds[1], bounds[2])


def _subscript_strings(node: ast.Subscript, resolve) -> set[str]:
    """Every string a SUBSCRIPT over a STATICALLY-VISIBLE container can select.

    ⛔ THE VERDICT WAS DECIDED BY THE DELIVERY, NOT BY THE VALUE (#14326 round 16, every
    shape below MEASURED GREEN at `0162ab067e` — `refuses=False`, `m=2 c=2 i=0`, with a third
    reach carrying `kind="statement"` that executes the product read):

        ('x', '_tortoise_fts_query')[1]        # literal sequence, constant index
        {'k': '_tortoise_fts_query'}['k']      # literal mapping, constant key
        '_tortoise_fts_query_extra'[:-6]       # literal string, constant slice
        [*('_tortoise_fts_query',)][0]         # the same, through a Starred

    None is the declared "computed at runtime" residual: every element is a `Constant` in
    the source, exactly the argument that admitted `+`, `*`, `%`, `.format` and the
    placeholder-free f-string. This file ALREADY expands literal sequences — for `for`
    targets (`_values`), for splats (`_possible_strings`) and for `**{…}` displays
    (`_dict_display_leaves`) — so declining them here was an in-file asymmetry, not a
    boundary (the shape the f-string and `.format_map` rounds closed).

    ⛔ THE RESIDUAL IS A NAME-HELD CONTAINER, AND KEEPING IT IS LOAD-BEARING: `_CFG["read"]`
    and `self._read` are not statically pairable, and resolving a NAME's whole value set here
    would FALSE RED the provenance lookup the `_is_read_ref` narrowing exists to keep green
    (`_READ = {"name": "…"}` then `print(f"measuring {_READ['name']}")`, pinned below). Only
    a `Constant`/`Tuple`/`List`/`Dict` CONTAINER resolves.

    A LITERAL SEQUENCE with a runtime index hands back the union of its elements: the index
    is runtime-decided but the VALUE is one of them, which is the fail-closed direction this
    map takes everywhere else. A LITERAL STRING with a runtime index selects ONE CHARACTER,
    which no read name can be, so it resolves to nothing rather than to the whole string
    (that would false-red every `_ALPHABET[_i]` in a file).
    """
    container, index = node.value, node.slice
    if isinstance(container, ast.Subscript):
        # ⛔ A SPLIT BUILD IS THE SAME BUILD (#14326 round 16, MEASURED GREEN):
        # `'_tortoise_fts_query_x'[::-1][::-1][:-2]` reached the read while each step is a
        # literal operation over a literal, so the verdict was decided by whether the string
        # was sliced in ONE step or THREE. Index the values the inner subscript resolved.
        parts = resolve(container)
        if isinstance(index, ast.Slice):
            bounds = _constant_slice_bounds(index)
            return {value[slice(*bounds)] for value in parts} if bounds is not None else set()
        return {value[position] for value in parts for position in _int_literals(index)
                if -len(value) <= position < len(value)}
    if isinstance(container, ast.Constant) and isinstance(container.value, str):
        if isinstance(index, ast.Slice):
            bounds = _constant_slice_bounds(index)
            return {container.value[slice(*bounds)]} if bounds is not None else set()
        positions = _int_literals(index)
        return {container.value[position] for position in positions
                if -len(container.value) <= position < len(container.value)}
    if isinstance(container, (ast.Tuple, ast.List)):
        elements = container.elts
        if isinstance(index, ast.Slice):
            bounds = _constant_slice_bounds(index)
            if bounds is not None:
                # A constant slice SELECTS, so only the selected elements are candidates —
                # the precision `_bind`'s unpair rules exist to keep (a false red here is
                # the direction this file calls expensive).
                return set().union(*(resolve(elements[position])
                                     for position in range(len(elements))[slice(*bounds)]))
            return set().union(*(resolve(element) for element in elements))
        positions = _int_literals(index)
        if positions:
            return {value for position in positions
                    if -len(elements) <= position < len(elements)
                    for value in resolve(elements[position])}
        return set().union(*(resolve(element) for element in elements))
    if isinstance(container, ast.Dict):
        # ⛔ NO `value is not None` FILTER (#14326 round 16, this file's own rule: a clause no
        # mutant can red is deleted, not kept). `ast.Dict.values` holds ONE expression per key
        # and is never `None` — only `keys` carries `None`, for a `**spread` — so the filter
        # was unreachable.
        if (isinstance(index, ast.Constant) and isinstance(index.value, str)
                and not any(key is None for key in container.keys)):
            # A fully-literal display with a constant KEY selects exactly that key's value —
            # and selects NOTHING when the key is absent (the runtime `KeyError`).
            return set().union(*(
                resolve(value)
                for key, value in zip(container.keys, container.values, strict=True)
                if isinstance(key, ast.Constant) and key.value == index.value))
        # A `**spread` (`key is None`) or a runtime key selects something this cannot pair:
        # every VALUE of the display is a candidate.
        return set().union(*(resolve(value) for value in container.values))
    return set()


def _possible_strings(node: ast.AST, consts: dict[str, frozenset[str]]) -> frozenset[str]:
    """Every string value `node` can statically hold, a literal container expanded."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return frozenset((node.value,))
    if isinstance(node, ast.Name):
        return consts.get(node.id, frozenset())  # a literal bound to a name, in ANY scope
    if isinstance(node, ast.Starred):
        return _possible_strings(node.value, consts)
    if isinstance(node, (ast.Tuple, ast.List)):
        return frozenset().union(*(_possible_strings(elt, consts) for elt in node.elts))
    # ⛔ THE SAME LITERAL-ITERABLE ARMS `_values` CARRIES (#14326 review F2): an operand
    # carrying the read's name inside a set/dict literal is that name here too, so a
    # `getattr(*{sdk, _N})` / `getattr(*{_N: 1})` splat cannot slip the `indirect` arm.
    # ⛔ AND EACH ARM IS PINNED BY ITS OWN ROW (#14326 review F1, measured at `285984005a`,
    # where the claim above was still only a comment): deleting both left every executable
    # pin test GREEN (`test_EVERY_static_binding_form_is_a_READ_reach` included) while
    #     _N = "_tortoise_fts_query"; getattr(*{sdk, _N})(…, kind="statement")
    # executed the product read — mentions 2, calls 2, indirect 0, `refuses=False`. Note
    # which arms those rows reach: the set/dict *for-target* rows go through `_values`,
    # a DIFFERENT function, so they can never red these two arms. The rows are
    # `SET literal splatted into getattr` and `DICT literal splatted into getattr` below.
    if isinstance(node, ast.Set):
        return frozenset().union(*(_possible_strings(elt, consts) for elt in node.elts))
    if isinstance(node, ast.Dict):
        # ⛔ NO `key is not None` FILTER (#14326 review P3, measured: deleting it left the
        # whole pin GREEN). A `**` spread's key IS `None`, and `_possible_strings(None)`
        # already falls through every arm to `frozenset()` — so the filter restated the
        # fall-through and no mutant could red it. This file deletes clauses like that.
        return frozenset().union(*(_possible_strings(key, consts) for key in node.keys))
    # ⛔ THE `NamedExpr` ARM MATCHES `_values` (#14326 round 8): an inline walrus operand,
    # `getattr(sdk, (_x := "_tortoise_fts_query"))`, is a static literal reached without any
    # bind node — the inline path must resolve it too, or the `indirect` arm cannot fire.
    if isinstance(node, ast.NamedExpr):
        return _possible_strings(node.value, consts)
    # ⛔ THE SAME `IfExp` / `BoolOp` / `JoinedStr` ARMS `_values` CARRIES (#14326 review P6):
    # the inline-operand path must see the same literal spellings, or
    # `getattr(sdk, f"_tortoise_fts_query")(…)` and `getattr(sdk, (A if c else B))(…)`
    # reach the read with the name inline and never touch `_values` at all. `_collection_operands`
    # already expands the first two, so resolving them here closes the asymmetry; the
    # `JoinedStr` arm feeds every part through, so a CONSTANT fragment is resolved while a
    # `FormattedValue` contributes nothing — `f"{x}_tortoise_fts_query"` is therefore
    # OVER-APPROXIMATED to the name (fail-closed, RED when executed, GREEN as an inert
    # label), NOT residual (#14326 round 5, measured at `6f66ac53`; the claim that it
    # "resolves to nothing" contradicted this very code).
    if isinstance(node, ast.IfExp):
        return _possible_strings(node.body, consts) | _possible_strings(node.orelse, consts)
    if isinstance(node, ast.BoolOp):
        return frozenset().union(*(_possible_strings(operand, consts)
                                   for operand in node.values))
    if isinstance(node, ast.JoinedStr):
        # ⛔ JOINED AS WELL AS UNIONED — the same fix as `_values`' arm (#14326 round 13):
        # `getattr(sdk, f"{'_tortoise_fts_query'}")(…)` reaches the read with the name inline
        # and never touches `_values`, so both resolvers need the join.
        _parts = [_possible_strings(part, consts) for part in node.values]
        return frozenset(set().union(*_parts) | _joined_strings(_parts))
    if isinstance(node, ast.FormattedValue):
        return (_possible_strings(node.value, consts)
                | (_possible_strings(node.format_spec, consts)
                   if node.format_spec is not None else frozenset()))
    # ⛔ `%` AND `.format` ON THE INLINE PATH, the mirror of `_values`' arms (#14326 round 13):
    # `getattr(sdk, "%s_fts_query" % "_tortoise")(…)` was GREEN at `b6be33738f` at
    # `m=2 c=2 i=0` while a `+` that builds the same string was refused.
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
        _operands = _mod_operands(node.right,
                                  lambda operand: _possible_strings(operand, consts))
        return (frozenset(_percent_strings(_possible_strings(node.left, consts), _operands))
                if _operands else frozenset())
    # ⛔ `%` AND `.format` ON THE INLINE PATH, the mirror of `_values`' arms (#14326 round 13):
    # `getattr(sdk, "%s_fts_query" % "_tortoise")(…)` was GREEN at `b6be33738f` at
    # `m=2 c=2 i=0` while a `+` that builds the same string was refused. ⛔ AND `.format_map`
    # IS THE SAME SPELLING, reached through the SHARED entry point so the two resolvers
    # cannot disagree about it (#14326 round 15).
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr in _FORMAT_SPELLINGS):
        return frozenset(_format_call_strings(
            node, lambda operand: _possible_strings(operand, consts)))
    # ⛔ A LITERAL-ONLY `+` IS A LITERAL (#14326 round 9, MEASURED GREEN at `d6ed6799`):
    # `getattr(sdk, "_tortoise_" + "fts_query")(…, kind="statement")` passed the pin at
    # `m=2 c=2 i=0`, while the IMPLICIT-CONCAT spelling of the same string (which `compile()`
    # folds to ONE `LOAD_CONST`) was refused. Nothing is computed at runtime — the operands
    # are `Constant`s — so this is the same argument that admitted `IfExp`/`BoolOp`: a
    # SELECTION between literals is statically the set of its literals. Only a `+` whose
    # operands are BOTH statically resolvable is joined; `_N + "_x"` (a NAME operand) stays
    # the declared residual.
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        _left, _right = _possible_strings(node.left, consts), _possible_strings(node.right, consts)
        # ⛔ NO EXPLICIT EMPTY-OPERAND GUARD (#14326 round 11, measured: deleting it leaves 27
        # passed and all eight shape verdicts identical). The product below already yields the
        # empty set when either side is unresolvable — that is what keeps the residual
        # (`_N + "_x"` with a parameter `_N`) out of the census, and a guard that restates it
        # is a clause no mutant can red.
        # ⛔ THE PRODUCT, NOT A UNION (#14326 round 10, MEASURED BOTH WAYS): a UNION was wrong
        # in both directions on the fallback — it emitted strings that are not concatenations
        # (`("x" if queries else "_tortoise_") + "fts_query"` passed the pin, `m=2 c=2 i=0`,
        # because neither operand is a singleton so the join never happened) AND it refused a
        # concatenation that can never equal the read name
        # (`("_tortoise_fts_query" if queries else "x") + "_y"`, whose every value ends in
        # `_y`). Every combination of the two statically-possible sets is a possible value of
        # the sum, so the product is both the fail-closed and the false-red-correct reading.
        return frozenset(a + b for a in _left for b in _right)
    # ⛔ AND A REPETITION BY A STATIC COUNT IS THE SAME LITERAL (#14326 review at
    # `a29f3cd8`, MEASURED GREEN at `80d2ab52`): `getattr(sdk, "_tortoise_fts_query" * 1)`
    # reached the read while the `+` spelling was refused. Either operand can be the sequence.
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
        return (_repetitions(_possible_strings(node.left, consts), _int_literals(node.right))
                | _repetitions(_possible_strings(node.right, consts),
                               _int_literals(node.left)))
    # ⛔ A SUBSCRIPT OF A LITERAL CONTAINER IS THE SAME STATIC LITERAL (#14326 round 16,
    # MEASURED GREEN at `0162ab067e` for a literal sequence, a literal mapping and a constant
    # slice of a literal string — `refuses=False`, `m=2 c=2 i=0`, the product read executed by
    # a third `kind="statement"` reach). The two resolvers must carry the SAME arms or the
    # bound and inline spellings disagree — the `+`, `%` and `.format` rounds, one delivery
    # over — so both call the shared `_subscript_strings`. The declared residual is a
    # subscript of a NAME-held container (`_CFG["read"]`), which the helper declines.
    if isinstance(node, ast.Subscript):
        return frozenset(_subscript_strings(
            node, lambda operand: _possible_strings(operand, consts)))
    return frozenset()  # e.g. a parameter — not statically knowable here, see the docstring


def _bind_target_names(target: ast.AST) -> set[str]:
    """Every callee NAME a bind TARGET can introduce — a `Name` id or an attribute's last
    segment.

    ⛔ THE ATTRIBUTE SEGMENT IS THE POINT (#14326 round 7, MEASURED GREEN at `d790e1f0`):
    both allow-lists below are consulted through
    `getattr(func, "id", None) or getattr(func, "attr", None)`, so an ATTRIBUTE call is
    allow-listed by its last segment exactly as a bare name is: `_buf.write = getattr`
    then `_buf.write(sdk, "_tortoise_fts_query")(…, kind="statement")` passed the pin
    (`m=2 c=2 i=0`), and so did `_mod.print = getattr` + `_mod.print(sdk, …)`. Rebinding
    the NAME closed only half the hole the name allow-list opened. A store into a
    container binds no callee, and over-collecting its LOAD names only ever drops a name
    from an allow-list — the fail-closed direction.
    """
    out: set[str] = set()
    for sub in ast.walk(target):
        if isinstance(sub, ast.Name):
            out.add(sub.id)
        elif isinstance(sub, ast.Attribute):
            out.add(sub.attr)
    return out


_NAMESPACE_CALLS = frozenset({"globals", "locals", "vars"})
_SETATTR_SPELLINGS = frozenset({"setattr"})
_MAPPING_MUTATORS = frozenset({"update", "setdefault"})
#: The registry's OWN mutator vocabulary (#14326 round 20, MEASURED GREEN: `sys.modules
#: .__setitem__("tortoise.sdk", …)` and `sys.modules.__ior__({…})` left the pin green while the
#: subscript store was red — and this file ALREADY reds `_t.__dict__.__setitem__("TortoiseSDK", …)`,
#: the same operation on the other namespace mapping). Kept SEPARATE from `_MAPPING_MUTATORS`
#: because that set is also consulted with `_is_namespace_expr`, where a `__setitem__` receiver is
#: a dict-like namespace and the key vocabulary differs; widening the shared set would move both.
# ⛔ AND `__init__` MERGES, SO IT IS A STORE (#14326 round 32 P2, MEASURED GREEN end-to-end:
# `sys.modules.__init__({"tortoise": _pkg})`, the unbound `dict.__init__(sys.modules, ...)` and
# the registry-alias twin each re-executed a planted `pkg/mod.py` after `del sys.modules["pkg.mod"]`
# with the pin GREEN. `dict.__init__` MERGES ({1:2}.__init__({3:4}) -> {1:2, 3:4}), so this is an
# ancestor-key STORE, not the whole-registry REPLACEMENT — which is its own declared
# residual, in the note above `_registry_replacement`. ⛔ NOT "residual (a)": this file
# has no lettered residual list, so that reference pointed at nothing (#14326 round 33
# P3, `grep`-verified — the same dangling-cross-reference class as "the note below").)
_REGISTRY_MUTATORS = frozenset({"update", "setdefault", "__setitem__", "__ior__", "__init__"})
#: `setdefault(key, default)` takes ONE key positionally; `update(mapping, …)` takes every
#: positional as a mapping. The split is what keeps a setdefault DEFAULT (a value) out of the
#: key census — `_COUNTS.setdefault(literal, 0)` is the accepted false red this file states.
_SETDEFAULT_SPELLINGS = frozenset({"setdefault"})
#: A mapping BUILT by a call: `dict(print=getattr)` carries the key in its `keyword` node.
#: Only `dict` is read this way — an arbitrary callee's keyword is not statically a mapping
#: (the same narrowing `_name_reaching_operands` states for its `**dict(…)` arm).
_DICT_SPELLINGS = frozenset({"dict"})
_PARTIAL_SPELLINGS = frozenset({"partial"})


def _callee_names_of(node: ast.AST) -> set[str]:
    """The names an expression can be CALLED by — a bare `Name` or an attribute's segment."""
    name = getattr(node, "id", None) or getattr(node, "attr", None)
    return {name} if name else set()


def _string_keys_of(node: ast.AST | None,
                    consts: dict[str, frozenset[str]]) -> set[str]:
    """Every STATIC string `node` can be — an inline constant, or a NAME bound to one.

    ⛔ THE NAME CASE IS NOT OPTIONAL (#14326 review F3, MEASURED GREEN): a first cut took an
    `ast.Constant` only, so `_p = "print"; setattr(_bi, _p, getattr)` laundered the name —
    while `_str_constants` exists precisely to resolve "a name whose statically possible
    value is a string" for every other consumer in this file.
    """
    if node is None:
        return set()
    # ⛔ THE KEY IS RESOLVED LIKE ANY OTHER STATIC STRING (#14326 review F3, MEASURED
    # GREEN): `setattr(_bi, "pr" + "int", getattr)` is a LITERAL-ONLY `+` — the file already
    # argues (round 9) that nothing is computed at runtime there — so the key goes through
    # `_possible_strings`, the SAME resolver every other consumer uses, rather than a second
    # Constant/Name matcher that drifts from it.
    return set(_possible_strings(node, consts))


def _is_namespace_expr(node: ast.AST, namespaces: frozenset[str] = frozenset()) -> bool:
    """A live namespace MAPPING — `globals()`/`locals()`/`vars(x)`/`x.__dict__`, or an ALIAS.

    ⛔ THE RECEIVER IS RESOLVED, NOT SPELLED (#14326 review F4, MEASURED GREEN):
    `_ns = _bi.__dict__; _ns["print"] = getattr` walked past a purely syntactic test — the
    same one-bind alias `_aliases_of` already resolves for every collection in this file.
    """
    if isinstance(node, ast.Attribute):
        return node.attr == "__dict__"
    if isinstance(node, ast.Name):
        return node.id in namespaces
    if not isinstance(node, ast.Call):
        return False
    _fn = getattr(node.func, "id", None)
    if _fn in _NAMESPACE_CALLS:
        return True
    # ⛔ `getattr(x, "__dict__")` IS THE MAPPING (#14326 review F1a, MEASURED GREEN): the
    # resolved spelling of `x.__dict__`, which this predicate already accepts.
    return bool(_fn == "getattr" and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
                and node.args[1].value == "__dict__")


#: The builtins the registry's resolved forms are reached THROUGH. Kept as a set so a name
#: ALIAS of one (`_g = getattr`, `_v = vars`, `_d = dict`) is accepted by the same arms
#: (#14326 round 24, MEASURED GREEN: `_g = getattr; _g(sys, "modules")[…] = _Mirror`,
#: `_v = vars; _v(sys)["modules"][…] = …` and `_d = dict; _d.get(sys.__dict__, "modules")[…]`
#: each inserted the entry). Over-matching across the three is harmless: every arm that reads
#: this set ALSO requires the `sys` receiver and the `modules`/`__dict__` key, so no working
#: code can be reddened (`vars(sys, "modules")` is a TypeError).
_REGISTRY_BUILTINS = frozenset({"dict", "getattr", "vars"})


def _builtin_name_aliases(tree: ast.AST) -> frozenset[str]:
    """Names STATICALLY bound to one of `_REGISTRY_BUILTINS` — `_gr = getattr`, `_v = vars`.

    ⛔ THE SPELLING OF THE BUILTIN IS NOT THE BUILTIN (#14326 round 35, MEASURED: with the
    literal test alone, `_v = vars; _v(builtins)["exec"]` and `_gr = getattr;
    _gr(builtins, "__dict__")["exec"]` both executed the read with the pin GREEN). The
    sibling lane's `getattr_fetchers` / `dict_fetchers` cover the CALL spellings
    (`getattr(builtins, "exec")`, `dict.get(m, k)`); they never reach the mapping-COMPOSITION
    spellings, which is the half this set closes. Both are needed: the fetchers name the
    callee, this set names the builtin used to BUILD the mapping.

    Deliberately does NOT reuse `builtin_aliases` from the registry ctx: that set is built BY
    `_builtin_lookup_name` at its own construction site, so consuming it here would be a
    fixpoint. This is a plain transitive name walk, available to every caller.

    ⛔ OVER-MATCHING ACROSS THE THREE IS HARMLESS, as `_REGISTRY_BUILTINS`' own note argues:
    every arm that consumes this set ALSO requires the builtins MODULE as its receiver and
    `"__dict__"` as its key, so the only thing the union can red is `_d(builtins, "__dict__")`
    — a `dict` alias called where `getattr` is meant, which is a TypeError and cannot run.
    """
    return frozenset(_aliases_of(
        tree,
        lambda n: (isinstance(n, ast.Name) and n.id in _REGISTRY_BUILTINS)
        # ⛔ AND THE MODULE-ATTRIBUTE SPELLING (#14326 review cycle 5, MEASURED GREEN:
        # `from builtins import dict as _dd; _x = _dd(builtins.__dict__)` executed the read
        # with the pin GREEN, while `_dd = dict` was RED). `builtins.dict` is the same
        # builtin, and the `ImportFrom` arm synthesizes exactly this Attribute — so without
        # this disjunct the import spelling binds a name no launderer arm can resolve, one
        # node-shape from the assignment spelling that works.
        or (isinstance(n, ast.Attribute) and n.attr in _REGISTRY_BUILTINS
            and isinstance(n.value, ast.Name) and n.value.id == "builtins"),
        loops=True))


def _is_builtins_dict_of(node: ast.AST, names: frozenset[str],
                         launderers: frozenset[str],
                         consts: dict[str, frozenset[str]]) -> bool:
    """True when `node` denotes a `builtins` MODULE's own `__dict__` mapping.

    The three compositions `_is_sys_dict_mapping` already resolves one axis over —
    `X.__dict__`, `vars(X)`, `getattr(X, "__dict__")` — pinned to a name that statically
    denotes the `builtins` module or an alias of it (#14326 round 30).

    ⛔ `launderers` IS REQUIRED, NO DEFAULT (#14326 round 35, the `_RegistryCtx` round-24
    rule): a defaulted argument restores the "call site passed nothing" hazard that bundle
    exists to make impossible, and here it would silently re-open the alias hole this
    parameter closes.
    """
    if isinstance(node, ast.Attribute):
        return (node.attr == "__dict__" and isinstance(node.value, ast.Name)
                and node.value.id in names)
    if not isinstance(node, ast.Call):
        return False
    if (isinstance(node.func, ast.Name)
            and (node.func.id == "vars" or node.func.id in launderers)
            and len(node.args) == 1 and isinstance(node.args[0], ast.Name)
            and node.args[0].id in names):
        return True
    return bool(isinstance(node.func, ast.Name)
                and (node.func.id == "getattr" or node.func.id in launderers)
                and len(node.args) >= 2 and isinstance(node.args[0], ast.Name)
                and node.args[0].id in names
                and "__dict__" in _string_keys_of(node.args[1], consts))


#: The mapping METHODS that RETURN the member stored under a key. `get`/`pop`/`setdefault`
#: all hand back that object, so each is a read of the mapping in the same sense `m[k]` is —
#: the bounded FAMILY rather than one spelling (`keys`/`items`/`values` take no key and cannot
#: name a member). #14326 round 35, MEASURED GREEN at `a12902a5d4` while the `m["exec"]`
#: subscript of the identical mapping was RED.
_BUILTINS_MAPPING_READS = frozenset({"get", "pop", "setdefault", "__getitem__"})


def _is_builtins_mapping(node: ast.AST, names: frozenset[str],
                         dict_names: frozenset[str],
                         launderers: frozenset[str],
                         consts: dict[str, frozenset[str]]) -> bool:
    """True when `node` denotes a `builtins` MAPPING — its module's `__dict__`, or a name
    statically bound to the literal `__builtins__` (which IS that mapping in an imported
    module). The two arms the SUBSCRIPT case already takes, extracted so the method-call cases
    take them too; that shared predicate is what keeps the spellings from diverging again
    (#14326 round 35). The RECEIVER was the half left raw: `builtins.__dict__.get("exec")`
    stayed GREEN while `builtins.__dict__["exec"]` was RED, which is the in-file asymmetry
    the registry axis deleted for `sys.__dict__.get("modules")` at its round 22.

    ⛔ AND A COPY OF THE MAPPING IS A LIVE LEVER, NOT A BOUNDARY (#14326 round 35, MEASURED
    GREEN: `_d = dict(builtins.__dict__); _d["exec"](<read>)`). The copy holds the SAME
    function objects, so reading `exec` out of it executes exactly what reading it out of the
    original does — `dict(<mapping>)` and `<mapping>.copy()` are one launder, not a
    different object graph.
    """
    # ⛔ THE COPY FAMILY IS DECIDED BY SHAPE, NOT BY TWO LITERAL SPELLINGS (#14326 round 35
    # review cycle 4, MEASURED GREEN: `{**builtins.__dict__}`, `builtins.__dict__ | {}`,
    # `dict({**m})` and `_dc = dict; _dc(m)` all executed the read). A `**` spread, a `|` union
    # and ANY dict-ish callee (including an alias of `dict`) each yield a new mapping holding
    # the SAME function objects — one launder, several spellings.
    # ⛔ EACH BRANCH RETURNS ONLY ON A POSITIVE MATCH. An early `return False` here SHORT-
    # CIRCUITS the composition arm below: `_v(builtins)` has a launderer callee and one
    # argument, so a `return <False>` would answer for the whole predicate and drop the `vars`
    # composition back to GREEN — the "one arm answers for another" defect this file deletes
    # on sight.
    if isinstance(node, ast.Dict) and any(
            k is None and _is_builtins_mapping(v, names, dict_names, launderers, consts)
            for k, v in zip(node.keys, node.values, strict=True)):
        return True
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr) and (
            _is_builtins_mapping(node.left, names, dict_names, launderers, consts)
            or _is_builtins_mapping(node.right, names, dict_names, launderers, consts)):
        return True
    # ⛔ THE KEYWORD (`**`) TWIN OF THE SAME CONSTRUCTOR (#14326 round 35 review cycle 5,
    # MEASURED GREEN: `dict(**builtins.__dict__)["exec"] is exec` at runtime, and the pin saw
    # `m=2 c=2 i=0`). The `ast.Dict` arm above already reads `k is None`; the `dict(…)` arm
    # read `node.args` only, and CPython puts a `**` spread in `node.keywords` with `arg is
    # None` — the same in-file asymmetry `_mapping_keys_in_keywords` exists to avoid.
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and (node.func.id == "dict" or node.func.id in launderers)
            and ((len(node.args) == 1
                  and _is_builtins_mapping(node.args[0], names, dict_names, launderers, consts))
                 or any(kw.arg is None
                        and _is_builtins_mapping(kw.value, names, dict_names, launderers, consts)
                        for kw in node.keywords))):
        return True
    # ⛔ BOUND *AND* UNBOUND `.copy`, and the `|` DUNDER (#14326 round 35 review cycle 5,
    # EACH MEASURED GREEN: `dict.copy(builtins.__dict__)` and `{}.__or__(builtins.__dict__)`
    # both yielded a mapping holding the real `exec`). These are spellings of an ENUMERATED
    # constructor, not the arbitrary-constructor class the residual table declares — the
    # `BinOp` arm reads the operator form and nothing read its `__or__` dunder twin.
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)) and (
            (node.func.attr == "copy"
             and _is_builtins_mapping(node.func.value, names, dict_names, launderers, consts))
            or (node.func.attr == "__or__" and any(
                _is_builtins_mapping(part, names, dict_names, launderers, consts)
                for part in (node.func.value, *node.args)))):
        return True
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"copy", "deepcopy"} and len(node.args) == 1
            and _is_builtins_mapping(node.args[0], names, dict_names, launderers, consts)):
        # the UNBOUND `dict.copy(m)` / `copy.deepcopy(m)` — the callee is a constructor here,
        # so `node.func.value` is that constructor and the mapping is argument ONE.
        # ⛔ `deepcopy` IS THE SAME LEVER (#14326 review cycle 5, MEASURED GREEN: `_x =
        # copy.deepcopy(builtins.__dict__); _x["exec"](<read>)` executed the read while
        # `copy.copy(m)` was RED). A deep copy of the mapping still holds the SAME function
        # objects at its leaves — nothing about the depth changes what reading `exec` out of
        # it executes — so the arm reads the copy FAMILY, not one member of it.
        return True
    return ((isinstance(node, ast.Name) and node.id in dict_names)
            or _is_builtins_dict_of(node, names, launderers, consts))


def _builtins_root_names(tree: ast.AST) -> tuple[frozenset[str], frozenset[str]]:
    """`(builtins_names, builtins_dict_names)` — the module names and the MAPPING names.

    ONE OWNER (#14326 review cycle 2). The registry axis computed these inline, so when the
    code-execution axis needed the same two sets (`exec` resolved through
    `builtins.__dict__["exec"]`), the alternative was a SECOND copy of the root enumeration —
    the drift this file deletes on sight. Both callers now resolve the roots here.

    ⛔ `__builtins__` IS A ROOT, not an alias of `builtins` (#14326 round 30): CPython injects
    it into every module's globals, so it needs no import — unlike a bare `builtins`, which
    raises `NameError` without one (that round's rows pinned a lever that could not RUN).
    """
    roots = {"builtins", "__builtins__"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "builtins" and alias.asname:
                    roots.add(alias.asname)
    builtins_names = frozenset(roots) | frozenset(_aliases_of(
        tree, lambda n: isinstance(n, ast.Name) and n.id in roots, loops=True))
    #: The names that can statically BE the builtins mapping rather than the module — the
    #: literal `__builtins__` (a `__dict__` in an imported module) and its one-bind aliases.
    #: Only these accept the BARE subscript form; see `_builtin_lookup_name`.
    #: ⛔ AND EVERY RESOLVED SPELLING OF THE MAPPING ITSELF (#14326 review cycle 3, EACH
    #: MEASURED GREEN while the read executed): `_d = builtins.__dict__; _d["exec"](…)`,
    #: `_d = vars(builtins); _d["exec"](…)`, `_d = getattr(builtins, "__dict__")`. The sibling
    #: axis has graded `__d = sys.__dict__` RED since round 21, so leaving the mapping alias
    #: raw here was an in-file asymmetry, not a boundary. Widening this set can only ADD reds
    #: (it feeds the bare-subscript branch of `_builtin_lookup_name` and the receiver test).
    _bu_launderers = _builtin_name_aliases(tree)
    #: The string-key resolver for the `"__dict__"` argument (`_K = "__dict__"`
    #: resolves like every other key in this file — `_builtin_lookup_name`'s own rule).
    _bu_consts = _str_constants(tree)
    builtins_dict_names = (frozenset({"__builtins__"})
                           | frozenset(_aliases_of(
                               tree, lambda n: isinstance(n, ast.Name)
                               and n.id == "__builtins__", loops=True))
                           | frozenset(_aliases_of(
                               tree, lambda n: _is_builtins_dict_of(
                                   n, builtins_names, _bu_launderers, _bu_consts),
                               loops=True))
                           # ⛔ A COPY IS THE MAPPING FOR THIS QUESTION (#14326 round 35,
                           # MEASURED: `_d = dict(builtins.__dict__); _d["exec"](<read>)`
                           # executed the read with the pin GREEN). `_aliases_of`'s default
                           # SHALLOW-COPY EXEMPTION exists because a copy is not the same
                           # OBJECT; here the question is whether the copy holds the same
                           # function objects, and it does — so the exemption is the wrong
                           # answer and `copy_is_alias=True` is the right one.
                           | frozenset(_aliases_of(
                               tree, lambda n: _is_builtins_mapping(
                                   n, builtins_names, frozenset(), _bu_launderers,
                                   _bu_consts),
                               loops=True, copy_is_alias=True)))
    return builtins_names, builtins_dict_names


def _builtin_lookup_name(node: ast.AST, names: frozenset[str],
                         consts: dict[str, frozenset[str]],
                         dict_names: frozenset[str],
                         launderers: frozenset[str],
                         members: frozenset[str] | None = None,
                         getattr_fetchers: frozenset[str] = frozenset({"getattr"}),
                         dict_fetchers: frozenset[str] = frozenset({"dict"})) -> str | None:
    """The `members` (default `_REGISTRY_BUILTINS`) member a callee expression resolves to
    THROUGH builtins' mapping.

    ⛔ `members` IS TESTED WITH `is not None`, NOT `or` (#14326 review cycle 3, measured):
    `members or _REGISTRY_BUILTINS` reinterprets an EMPTY member set as the REGISTRY set, so
    a caller that meant "resolve nothing" would silently get the registry's answer — the
    empty-set-at-a-construction-site hazard `_RegistryCtx` exists to make impossible.
    ⛔ NO MUTANT CAN RED THIS, AND THAT IS NOT AN INVITATION TO DELETE IT: it is a guard on a
    CALL-SITE mistake (measured: reverting to `or` leaves all 43 tests green, because no
    caller passes an empty set today), not a reach-detection clause. The file's "delete what
    no mutant can red" rule is about arms that pretend to detect a shape; this one prevents
    the next lane's empty set from silently meaning its opposite.

    ⛔ AND THE FETCHER NAME IS RESOLVABLE (#14326 review cycle 3, EACH MEASURED GREEN while
    the read executed): `_g = getattr; _g(builtins, "exec")(…)` and
    `_dg = dict; _dg.get(_d, "exec")(…)`. The sibling axis resolves exactly this one spell
    over (`_g = getattr` then `_g(sys, "__dict__")` has been RED since round 21), so the
    fetcher name is a parameter rather than a hard-coded literal. The defaults are the
    literals, so every existing caller is unchanged.

    ⛔ THE MAPPING LOOKUP IS THE NEXT CALLEE SPELLING (#14326 round 30, EACH MEASURED GREEN at
    `de42a75ac4b5`), while `builtins.getattr` itself was RED:

        _g = builtins.__dict__["getattr"]              GREEN
        _g = vars(builtins)["getattr"]                 GREEN
        _g = getattr(builtins, "getattr")              GREEN
        _g = dict.get(builtins.__dict__, "getattr")    GREEN

    Each is the same one-hop callee launder the alias axis had already closed one spelling
    over — the resolved forms here are the ones `_is_namespace_expr` and
    `_is_sys_dict_mapping` already accept on the sibling axes, so the asymmetry was in-file.
    The keys go through `_string_keys_of` like every other key in this file, so a NAME bound to
    a literal resolves too. Narrowed to a name that is provably a `builtins` module one hop
    up: `_cfg["getattr"]` and `vars(_cfg)["getattr"]` stay GREEN.

    ⛔ AND THE BARE SUBSCRIPT, FOR THE ONE NAME THAT CAN BE THE DICT ITSELF (#14326 round 30).
    `__builtins__` is the builtins MODULE in `__main__` and its `__dict__` in an IMPORTED module,
    so `__builtins__["getattr"]` is a live lever in exactly the context a launderer controls —
    and leaving it GREEN while `__builtins__.getattr` is RED would be the in-file asymmetry
    ("SPELLING IS NOT IDENTITY", round 20) this file deletes on sight. It is scoped to
    `dict_names` — every name that statically denotes the MAPPING: the literal
    `__builtins__` and its aliases, PLUS the resolved compositions `builtins.__dict__` /
    `vars(builtins)` / `getattr(builtins, "__dict__")` and their COPIES
    (`_builtins_root_names`, #14326 round 35) — deliberately:
    `builtins["getattr"]` is a TypeError (`'module' object is not subscriptable`), so accepting
    a bare subscript on every root name would red code that cannot run.
    """
    _members = members if members is not None else _REGISTRY_BUILTINS
    if isinstance(node, ast.Subscript):
        keys = _string_keys_of(node.slice, consts) & _members
        if not _is_builtins_mapping(node.value, names, dict_names, launderers, consts):
            return None
        return sorted(keys)[0] if keys else None
    if not isinstance(node, ast.Call):
        return None
    # ⛔ THE FETCHER IS A RESOLVED FORM TOO, NOT A BARE `Name` (#14326 round 35, MEASURED:
    # `builtins.getattr(builtins, "exec")(<read>)` and
    # `__builtins__["getattr"](builtins, "exec")(<read>)` both executed the read with the pin
    # GREEN, while `builtins.eval(<read>)` was RED solely because the CALLEE set is read from
    # `func.attr` and the FETCHER set was read from `func.id`). The registry axis has no such
    # hole — `builtins.getattr(sys, "modules")` reds there — so this was an in-file
    # asymmetry. Accept the attribute spelling, and let the mapping lookup resolve a subscript
    # spelling through the SAME `_builtin_lookup_name` the rest of the file uses.
    if (isinstance(node.func, ast.Name) and node.func.id in getattr_fetchers) \
            or (isinstance(node.func, ast.Attribute)
                and node.func.attr in getattr_fetchers
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in (names | dict_names)) \
            or (_builtin_lookup_name(node.func, names, consts, dict_names, launderers,
                                     members=getattr_fetchers,
                                     getattr_fetchers=getattr_fetchers,
                                     dict_fetchers=dict_fetchers) in getattr_fetchers):
        # ⛔ THE RECEIVER MAY BE THE MAPPING ITSELF (#14326 review cycle 3, measured):
        # `getattr(_d, "exec")` with `_d = builtins.__dict__` was GREEN. `dict_names` already
        # holds every resolved spelling of that mapping, so the receiver accepts either set.
        if (len(node.args) >= 2
                and ((isinstance(node.args[0], ast.Name)
                      and node.args[0].id in (names | dict_names))
                     or _is_builtins_mapping(node.args[0], names, dict_names, launderers,
                                             consts))):
            keys = _string_keys_of(node.args[1], consts) & _members
            return sorted(keys)[0] if keys else None
        return None
    # ⛔ THE UNBOUND SPELLINGS DECIDE BY ARGUMENT SHAPE, NOT BY CALLEE (#14326 round 35, and
    # MEASURED: the sibling lane's `attr == "get"` + `value.id in dict_fetchers` form left
    # `dict.pop(builtins.__dict__, "exec")` and `dict.setdefault(builtins.__dict__, "exec")`
    # GREEN while their BOUND twins were closed by the very same `_BUILTINS_MAPPING_READS`
    # family — an in-file asymmetry by this file's own definition). The registry axis reached
    # the same conclusion at its round 26 after rounds 24-25 chased an unbounded callee list (a
    # lambda, `operator.getitem`, `_dg = dict.get`, an alias of an alias): the shape is the
    # identity. `_d = dict; _d.pop(...)` and `builtins.dict.pop(...)` come free.
    if (isinstance(node.func, ast.Attribute)
            and node.func.attr in _BUILTINS_MAPPING_READS
            and len(node.args) >= 2
            and _is_builtins_mapping(node.args[0], names, dict_names, launderers, consts)):
        keys = _string_keys_of(node.args[1], consts) & _members
        return sorted(keys)[0] if keys else None
    # ⛔ AND THE MAPPING'S OWN METHODS, WHICH ARE THE SUBSCRIPT ONE SPELLING OVER (#14326
    # round 35, EACH MEASURED GREEN at `a12902a5d4` while the `m["exec"]` subscript of the
    # IDENTICAL mapping was RED):
    #
    #     builtins.__dict__.get("exec")             GREEN   -> closed here
    #     builtins.__dict__.pop("exec")             GREEN   -> closed here
    #     vars(builtins).get("exec")                GREEN   -> closed here
    #     __builtins__.get("exec")                  GREEN   -> closed here
    #
    # The arm above is the UNBOUND `dict.get(mapping, key)`; this is the RECEIVER spelling, and
    # it was the surviving half: the unbound arm keys on `node.func.value.id in dict_fetchers`,
    # which an ATTRIBUTE receiver (`builtins.__dict__`) can never satisfy. Arity is `>= 1` — a
    # `pop`/`setdefault` DEFAULT never fires when the key EXISTS, the same reasoning the registry
    # axis' `.get` arm uses for its optional second argument.
    if (isinstance(node.func, ast.Attribute)
            and node.func.attr in _BUILTINS_MAPPING_READS
            and len(node.args) >= 1
            and _is_builtins_mapping(node.func.value, names, dict_names, launderers, consts)):
        keys = _string_keys_of(node.args[0], consts) & _members
        return sorted(keys)[0] if keys else None
    return None


class _RegistryCtx(NamedTuple):
    """Everything `_is_module_registry` needs, in ONE object.

    ⛔ THE BUNDLE IS THE FIX (#14326 round 24, MEASURED): the predicate had grown to three
    positional parameters and a call site that passed an EMPTY set for one of them silently
    re-opened the hole it exists to close — emptying three of the five sites left the file at
    `41 passed` while real shapes flipped RED → GREEN:

        __d = sys.__dict__; __d["modules"] |= {"tortoise.sdk": _Mirror}
        __d["modules"].update({"tortoise.sdk": _Mirror})
        _m = __d["modules"]; _m |= {"tortoise.sdk": _Mirror}

    One object cannot be passed "partly", so the hazard is gone by construction rather than by
    a row per call site.

    ⛔ `builtins_names` IS THE MODULE SET, AND IT IS REQUIRED (#14326 round 30): the arms that
    resolve a CALLEE through builtins' mapping (`_callee_names_in`) need the names that denote
    the `builtins` MODULE, exactly as `sys_names` is needed for `sys`. It has NO default on
    purpose — a defaulted field would restore the "call site passed an EMPTY set" hazard this
    bundle was created to remove, and both construction sites fill it.
    """

    sys_names: frozenset[str]
    sys_dict_names: frozenset[str]
    consts: dict[str, frozenset[str]]
    builtin_aliases: frozenset[str]
    builtins_names: frozenset[str]
    builtins_dict_names: frozenset[str]


def _callee_names_in(node: ast.AST, ctx: _RegistryCtx) -> set[str]:
    """`_callee_names_of`, PLUS the builtin a MAPPING LOOKUP resolves to (#14326 round 30).

    A callee reached as `builtins.__dict__["getattr"]` has no `Name`/`Attribute` segment of
    its own, so `_callee_names_of` returns the empty set and every arm that intersects it
    walked past the call — measured GREEN for the DIRECT spelling while the attribute twin
    was RED. Threading the set through the ONE bundle every registry arm already takes keeps
    the widening to the registry axis; the other `_callee_names_of` consumers (the setattr
    and `dict`-mutator axes) are untouched.
    """
    names = _callee_names_of(node)
    lookup = _builtin_lookup_name(node, ctx.builtins_names, ctx.consts,
                                  ctx.builtins_dict_names, ctx.builtin_aliases)
    if lookup:
        names.add(lookup)
    return names


def _is_modules_key(node: ast.AST | None, ctx: _RegistryCtx) -> bool:
    """True when `node` can statically be the string `"modules"` — a literal OR a resolved NAME."""
    return bool(_string_keys_of(node, ctx.consts) & {"modules"})


def _is_dunder_dict_key(node: ast.AST | None, ctx: _RegistryCtx) -> bool:
    """True when `node` can statically be `"__dict__"` — the same resolver, one key over."""
    return bool(_string_keys_of(node, ctx.consts) & {"__dict__"})


# ⛔ THE REPLACEMENT RESIDUAL, NAMED RATHER THAN IMPLIED (#14326 round 26). The registry
# REPLACEMENT is red in its ASSIGNMENT spellings — `sys.modules = {…}`,
# `sys.__dict__["modules"] = {…}`, `vars(sys)["modules"] = {…}`, the unpack and `for` targets.
# It is NOT red when the same store is written as a CALL with `"modules"` as the key:
#
#     setattr(sys, "modules", {…})                       GREEN
#     sys.__dict__.update(modules={…})                    GREEN
#     operator.setitem(sys.__dict__, "modules", {…})      GREEN
#     partial(setattr, sys)("modules", {…})               GREEN
#
# A half-finished arm for these was written and REMOVED: it red the unbound non-splat
# spelling while blessing its bound twin (`sys.__dict__.__setitem__("modules", …)`), and a
# partial arm is worse than none — it IMPLIES coverage the pin does not have, which is the
# blessed-fail-open shape this predicate's docstring already names. The residual is therefore
# stated here, and the honest fix is a DYNAMIC identity check (run the harness and assert the
# object it reads is the shipped one), not a longer list of store spellings: an exhaustive
# static enumeration of "how could this mapping be written" does not terminate, which 26
# review rounds on this axis have now demonstrated.
#
# ⛔ AND A CALLEE OBTAINED BY A NON-ENUMERATED ROUTE (#14326 round 28 P3, MEASURED GREEN:
# a lambda and `operator.attrgetter("modules")` both reach `getattr(sys, "modules")`). The bound
# `getattr(sys, "modules")` read still identifies its callee by an enumerated NAME, so any
# route outside that set escapes. This is the same unbounded class as the two above it — the
# call-spelling residual of the REPLACEMENT note and this callee-route one, which the note
# numbered (a)/(b) with no lettered list behind it (#14326 round 33 P3); the list of ways to
# name a callable does not terminate either.
#
# ⛔ TWO OF THE ROUTES THIS NOTE ORIGINALLY LISTED ARE CLOSED, AND THE NOTE IS CORRECTED
# RATHER THAN LEFT (the file's own rule: a marker that a claim was corrected is not evidence
# that the correction was right — so the claim is re-MEASURED at this head and the surviving
# list restated): `_g = builtins.__dict__["getattr"]` and `_g = __builtins__["getattr"]` are
# RED now, because the MAPPING LOOKUP is a resolved form `_builtin_lookup_name` collects and
# `_callee_names_in` resolves (#14326 round 30). What survives is the genuinely
# non-enumerable class: a lambda, `operator.attrgetter`, `object.__getattribute__(sys,
# "__dict__")`, `partial(getattr, sys)("__dict__")`, `getattr.__call__`, and
# `builtins.__getattribute__("getattr")` — all MEASURED GREEN at `d57c774e41`.
#
# ⛔ AND IT IS THE SAME ROUTE ON THE MAPPING AXIS, WHICH IS A SEPARATE ARM (#14326 round 28,
# MEASURED at `3652689c4`). `_is_sys_dict_mapping` decides `f(sys, "__dict__")` and `vars(sys)`
# by the same enumerated NAME, so the laundering that reds `getattr(sys, "modules")`'s callee
# does not red this one. GREEN with the mapping axis's four RESOLVED spellings
# (`__d = sys.__dict__`, `getattr(sys, "__dict__")`, `_g = getattr` then `_g(sys, "__dict__")`,
# `vars(sys)`) RED:
#
#     __d = object.__getattribute__(sys, "__dict__"); __d["modules"] = {…}
#     __d = sys.__getattribute__("__dict__"); __d["modules"] = {…}
#     __d = operator.attrgetter("__dict__")(sys); __d["modules"] = {…}
#     __d = functools.partial(getattr, sys)("__dict__"); __d["modules"] = {…}
#     __d = getattr.__call__(sys, "__dict__"); __d["modules"] = {…}
#
# plus each one's `vars` twin (`_v = functools.partial(vars, sys)()`,
# `operator.attrgetter("__dict__")(sys)`, `(lambda o: vars(o))(sys)`) — all MEASURED GREEN at
# `d57c774e41`.
# ⛔ THE SIXTH ENTRY THIS LIST CARRIED IS GONE FROM IT, CORRECTED AT THE SOURCE (#14326 round
# 29, MEASURED at `d57c774e41`): `_g = builtins.__dict__["getattr"]; __d = _g(sys, "__dict__")`
# is RED now — the mapping lookup is a resolved callee form `_builtin_lookup_name` collects and
# `_callee_names_in` resolves, so it stopped being an instance of this residual. It was listed
# NOT chased here either, for the reason this note already gives: the ways to name a callable
# do not terminate, and the round-24/#14326 P3 shape arm cannot be reused — it is callee-blind
# only where the FIRST argument is already `sys.__dict__`, and making it callee-blind for
# `(sys, "__dict__")` would red the pinned-GREEN foreign-`getattr` control
# (`from mypkg import getattr as _g`), the expensive direction. The fix is the same DYNAMIC
# identity check named above, not a 29th spelling.
#
# ⛔ AND THE SAME FOR `sys` OBTAINED BY A CALL: `importlib.import_module("sys").modules[…]`
# and `__import__("sys").modules[…]` are GREEN. Contrived (a harness author spells
# `sys.modules`), and the same argument applies — enumerating the ways to NAME `sys` does not
# terminate either.
#
# Both are recorded here so the next round starts from the state rather than re-deriving it.


def _registry_replacement(target: ast.AST, ctx: _RegistryCtx) -> ast.AST | None:
    """The part of `target` that REPLACES the registry outright, or None.

    ⛔ THE TARGET IS DESTRUCTURED, NEVER DEEP-WALKED (#14326 round 25): an unpack target stores
    to each ELEMENT (`sys.modules, _x = {...}, 1`), while a deep `ast.walk` would also find the
    INNER `sys.modules` attribute of the shipped tool's legitimate
    `sys.modules[_AB_MODULE] = module` and red it — a false red, the expensive direction here
    (measured: the walk form left `2 failed, 39 passed` on that shipped line).
    """
    elements = target.elts if isinstance(target, (ast.Tuple, ast.List)) else [target]
    for element in elements:
        if isinstance(element, (ast.Attribute, ast.Subscript)) \
                and _is_module_registry(element, ctx):
            return element
    return None


def _is_module_registry(node: ast.AST | None, ctx: _RegistryCtx) -> bool:
    """`sys.modules` — the IMPORT REGISTRY a deferred `from … import …` resolves THROUGH.

    ⛔ THE STORE THE PIN WAS BLIND TO (#14326 review P1, MEASURED GREEN): `run()` imports
    `TortoiseSDK` INSIDE its body, so the import consults `sys.modules["tortoise.sdk"]` at
    CALL time — an entry swapped before the call constructs the mirror exactly as an
    attribute write would. The pin's own control row asserted this shape was GREEN ("an
    unrelated KEY is not this store"), so the fail-open was not merely uncaught: it was
    blessed by a test. Aliases are resolved by the caller (`_aliases_of`).
    """
    if isinstance(node, ast.Attribute):
        # ⛔ AND THE BASE MUST BE `sys` (#14326 round 21, MEASURED: `other.modules["tortoise
        # .sdk"] = …` was RED — an unrelated module's own `modules` attribute reddened a pin
        # that exists to protect a different object). An attribute named `modules` is not the
        # import registry; `sys`'s is.
        return node.attr == "modules" and _is_sys_ref(node.value, ctx.sys_names)
    # ⛔ SPELLING IS NOT IDENTITY (#14326 round 20, MEASURED GREEN at `57735ab47` for every
    # form below while `import sys as _s; _s.modules[…]` was RED): the registry was recognised
    # only as an `x.modules` ATTRIBUTE, so the SAME OBJECT reached by an import, a `getattr`
    # or a `__dict__` index walked past — each verified `is sys.modules` at runtime, and each
    # the identical store. In-file asymmetry, not a boundary: `_environ_mutations` already
    # resolves BOTH `from os import environ` and `getattr(os, "environ")`.
    # ⛔ EVERY FORM IS PINNED TO `sys` BY NAME, deliberately. A general
    # `getattr(<anything>, "modules")` or `x.__dict__["modules"]` rule would red an unrelated
    # module's `modules` attribute — a false red is the expensive direction in this file.
    # ⛔ AND THE CALLEE RESOLVES THROUGH AN ALIAS (#14326 round 24, MEASURED GREEN:
    # `_g = getattr; _g(sys, "modules")[…]`, `_v = vars; _v(sys)["modules"][…]` and
    # `_d = dict; _d.get(sys.__dict__, "modules")[…]` each inserted the entry). The callee name
    # was a SPELLING here, the defect `launderers` exists for on the setattr axis.
    if (isinstance(node, ast.Call)
            and _callee_names_in(node.func, ctx) & ({"getattr"} | ctx.builtin_aliases)
            and len(node.args) >= 2
            and _is_modules_key(node.args[1], ctx)
            and _is_sys_ref(node.args[0], ctx.sys_names)):
        return True
    if isinstance(node, ast.Subscript):
        if not _is_modules_key(node.slice, ctx):
            return False
        return _is_sys_dict_mapping(node.value, ctx)
    # ⛔ `sys.__dict__.get("modules")` IS THE SAME READ (#14326 round 22, MEASURED GREEN at
    # `f6214869`: both `.get(…)` spellings below inserted the entry with the pin GREEN while
    # the `[…]` subscript of the identical mapping was RED). A `.get("modules")` on the
    # module's OWN namespace is a read of the registry in the same sense `getattr(sys,
    # "modules")` already is — the forms are distinguished by spelling, not by object.
    # ⛔ AND A `.get` DEFAULT DOES NOT SAVE IT (#14326 round 23, MEASURED GREEN:
    # `sys.__dict__.get("modules", {})[…]` and `vars(sys).get("modules", {})[…]` both
    # inserted the entry). The default never fires when the key EXISTS, so the arity is `>= 1`
    # — the same `>= 2` the `getattr` arm above takes its optional default into account with.
    if (isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and len(node.args) >= 1
            and _is_modules_key(node.args[0], ctx)
            and _is_sys_dict_mapping(node.func.value, ctx)):
        return True
    # ⛔ THE UNBOUND READ SPELLINGS (#14326 round 23, MEASURED GREEN: `dict.get(sys.__dict__,
    # "modules")[…] = …` and `dict.__getitem__(sys.__dict__, "modules")[…] = …` each inserted
    # the entry while the bound spelling was RED). The store axis already resolves its own
    # unbound twins (`_REGISTRY_MUTATORS` with `offset=1`); the READ axis is the surviving
    # asymmetry, and both are `dict.get(sys.__dict__, "modules") is sys.modules` at runtime.
    # ⛔ THE ARGUMENT SHAPE IS THE IDENTITY, AND THE CALLEE IS IRRELEVANT (#14326 round 26,
    # MEASURED). Rounds 24-25 tried to decide WHICH CALLable this is — `dict` by name, then
    # `builtin_aliases`, then a callee CARRIED in a name (`_dg = dict.get`) — and every round
    # found the next spelling GREEN: `builtins.dict.get(sys.__dict__, "modules")`,
    # `_bg = builtins.dict.get`, `_g = builtins.getattr`, `_v = builtins.vars`,
    # `partial(dict.get)`, `_mget = getattr(dict, "get")`, `dict.__dict__["get"]`. That list
    # is UNBOUNDED (a lambda, an `operator.getitem`, a class attribute, an alias of an alias),
    # so enumerating it cannot converge — it is the same defect as matching a KEY by spelling
    # instead of resolving it, one level up.
    #
    # The callee never needed identifying: `args[0]`/`args[1]` are what select the mapping, and
    # `_is_sys_dict_mapping(node.args[0])` already restricts the READ to `sys`'s own mapping.
    # So the arm matches the SHAPE — *any* call that reads `(<sys dict>, "modules")` — and the
    # callee, its spelling, its aliases and its laundering are all out of scope by
    # construction. This is strictly SIMPLER than the spelling match it replaces, which is the
    # tell that it is the right predicate.
    #
    # ⛔ THE ACCEPTED COST, stated rather than implied: `f(sys.__dict__, "modules")` reds for
    # ANY `f` that returns a mapping. That is a contrived shape (its arguments are literals
    # naming `sys`'s own `__dict__` and the key `"modules"`) and the over-red is the SAFE
    # direction against a store into the import registry — the same trade the `with`-target arm
    # makes by binding the context expression. An unrelated MAPPING stays GREEN, which is the
    # control that matters: `dict.get(_cfg, "modules")` and `_cfg.get("modules")` below.
    return bool(isinstance(node, ast.Call)
                # The arity guard keeps `node.args[1]` from an IndexError on a 1-arg
                # `dict.get(<mapping>)`; it is unreachable-by-mutant and SAID SO (#14326
                # round 24, MEASURED: relaxing it also left `41 passed`).
                and len(node.args) >= 2
                and _is_modules_key(node.args[1], ctx)
                and _is_sys_dict_mapping(node.args[0], ctx))


def _is_sys_dict_mapping(node: ast.AST, ctx: _RegistryCtx) -> bool:
    """`sys`'s OWN namespace mapping — `sys.__dict__`, `getattr(sys, "__dict__")`, `vars(sys)`.

    ⛔ THE COMPOSITION IS THE SAME MAPPING (#14326 round 22, MEASURED GREEN at `f6214869`:
    `getattr(sys, "__dict__")["modules"][…] = …` left the pin GREEN while the plainly
    spelled `sys.__dict__["modules"][…]` was RED). It is one `getattr` away from a spelling
    this arm already resolved — the exact asymmetry `_is_namespace_expr` had already closed
    on the sibling mapping ("`getattr(x, "__dict__")` IS THE MAPPING", #14326 review F1a).
    Narrowed to `sys` by name, as every form of `_is_module_registry` is: an unrelated
    module's own `__dict__["modules"]` is not the import registry and must stay GREEN.

    ⛔ AND AN ALIAS OF THE MAPPING IS THE MAPPING (#14326 round 23, MEASURED GREEN:
    `__d = sys.__dict__; __d["modules"]["tortoise.sdk"] = _Mirror` and the `.get` spelling of
    it each inserted the entry). `_aliases_of` already resolves `_ns = _bi.__dict__` for the
    NAMESPACE axis, so the datum existed and was never consulted. `sys_dict_names` is that
    set, and it is restricted to aliases of **`sys`'s** dict: accepting any name that is a
    namespace would red `_ns = globals(); _ns["modules"]`, which is a false red (measured
    GREEN before and after this change).
    """
    if isinstance(node, ast.Attribute) and node.attr == "__dict__":
        return _is_sys_ref(node.value, ctx.sys_names)
    if isinstance(node, ast.Name):
        return node.id in ctx.sys_dict_names
    if not isinstance(node, ast.Call):
        return False
    if (_callee_names_in(node.func, ctx) & ({"vars"} | ctx.builtin_aliases)
            # ⛔ `== 1`, NOT `>= 1`, AND IT IS UNREACHABLE-BY-MUTANT (#14326 round 28,
            # MEASURED: relaxing it left `41 passed`). The only 2-arg spelling it excludes is
            # `vars(<obj>, <name>)`, which is a TypeError — red-ing it would be a false red on
            # code that cannot run, so no row writes one and the arity stays exact.
            and len(node.args) == 1
            and _is_sys_ref(node.args[0], ctx.sys_names)):
        return True
    return bool(_callee_names_in(node.func, ctx) & ({"getattr"} | ctx.builtin_aliases)
                and len(node.args) >= 2
                and _is_dunder_dict_key(node.args[1], ctx)
                and _is_sys_ref(node.args[0], ctx.sys_names))


def _sys_names_of(tree: ast.AST) -> frozenset[str]:
    """Every name in `tree` that denotes the `sys` MODULE — `sys` plus its ALIASES.

    ⛔ `import sys as _s` BINDS `sys` TO ANOTHER NAME, AND THE RESOLVED FORMS FOLLOWED THE
    LITERAL SPELLING ONLY (#14326 round 21, MEASURED GREEN: `import sys as _s` then
    `getattr(_s, "modules")[…] = …` / `vars(_s)["modules"][…] = …` / `_s.__dict__["modules"]
    [… ] = …` each left the pin GREEN while `_s.modules[…]` was RED). A set threaded into
    `_is_module_registry` is how those three close without widening the rule beyond `sys` —
    which is the narrowing that keeps `other.modules[…]` from red-ing (see that predicate).

    ⛔ AND `_s = sys` IS THE SAME BIND ONE SPELLING OVER (#14326 round 22, MEASURED: pinning
    this set to IMPORT aliases alone made `_s = sys; _s.modules["tortoise.sdk"] = _Mirror`
    GREEN — it was RED before the narrowing, so the first version of this function traded a
    false red for a FAIL-OPEN, the wrong direction).

    ⛔ AND IT IS RESOLVED BY `_aliases_of`, NOT BY A HAND-WRITTEN WALK (#14326 round 23,
    MEASURED: the previous version accepted `Assign`/`AnnAssign`/`NamedExpr` but only a BARE
    `Name` target, so NINE other binds of a name to `sys` left the pin GREEN — `_s, _t = sys,
    None`, `(_s, _t) = (sys, None)`, `[_s] = [sys]`, `_s, *_r = sys, 1`, `for _s in (sys,)`,
    `with sys as _s:`, `match sys: case _s:`, `def _f(_s=sys):`, and the unpack one driving a
    mutator — every one of them RED at `ff85c6ab73af`). `_str_constants` had ALREADY enumerated
    exactly those forms; re-implementing three of them here is how the other six were missed.
    This file resolves binds through ONE walk, so a later spelling is closed once.
    ⛔ THAT UNION ALSO SUBSUMES A SIBLING LANE'S LOOP-TARGET ARM (`35bd48ec`, which resolved
    `for _s in (sys,)` by hand — MEASURED GREEN at `2975fdf32165`, and closed there). Keeping
    both would be two mechanisms for one bind; `_aliases_of(…, loops=True)` reaches `for`
    targets through `_loop_target_sources`, so the same shape stays RED here without a second
    walk. Recorded so the next lane does not re-add it.
    """
    names = {"sys"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "sys" and alias.asname:
                    names.add(alias.asname)
    # The same machinery `_aliases_of` gives every other axis: unpack, chained, walrus,
    # `for`/`async for`, `with`, `match` capture, parameter default — all of them, to a fixpoint.
    names |= _aliases_of(
        tree,
        lambda n: (isinstance(n, ast.Name) and n.id in names)
        or (isinstance(n, ast.Attribute) and n.attr == "sys"),
        loops=True)
    return frozenset(names)


def _is_sys_ref(node: ast.AST, sys_names: frozenset[str]) -> bool:
    """`sys`, seen through the aliases `_sys_names_of` resolves.

    ⛔ NARROWER THAN `_aliases_of` ON PURPOSE (#14326 round 21): this decides "the registry"
    and a false positive reds an unrelated module, so a bare `Name` must be a name that IS
    `sys`. A `x.sys` ATTRIBUTE is accepted (round 22): `os.sys.modules` IS the registry, so the
    over-red is the safe direction. ⛔ IT IS NOT WHAT HOLDS `_s = sys; _s.modules[…]` (#14326
    round 23, MEASURED: deleting only this arm leaves that row RED — `_sys_names_of`'s
    `_aliases_of` fixpoint supplies `_s`); the ONE row this arm holds is `os.sys.modules`. What
    stays rejected is `other.modules`, whose base is neither a `sys` name nor a `sys` attribute.
    """
    if isinstance(node, ast.Name):
        return node.id in sys_names
    return isinstance(node, ast.Attribute) and node.attr == "sys"


def _is_setattr_like(value: ast.AST) -> bool:
    """A value that IS `setattr`, or a `functools.partial` of it — the LAUNDERER.

    ⛔ THE LAUNDERER IS NOT ALWAYS SPELLED `setattr` (#14326 review F1/F1b, EACH MEASURED
    GREEN): `_sa = setattr; _sa(_bi, "print", getattr)` and
    `functools.partial(setattr, _bi)("print", getattr)` both rebind a callee name through a
    spelling that matched no arm.
    """
    if _callee_names_of(value) & _SETATTR_SPELLINGS:
        return True
    return bool(isinstance(value, ast.Call)
                and _callee_names_of(value.func) & _PARTIAL_SPELLINGS
                and value.args
                and _callee_names_of(value.args[0]) & _SETATTR_SPELLINGS)


def _is_partial_setattr(value: ast.AST) -> bool:
    """`functools.partial(setattr, …)` — the LAUNDERER with its receiver PRE-BOUND.

    ⛔ TWO SHAPES, ONE PREDICATE, AND THE KEY MOVES (#14326 review P1, EACH MEASURED GREEN
    through the pin's own predicate at `b6be337`): a bare `setattr` carries the attribute
    NAME at `args[1]`, while `partial(setattr, mod)` carries it at the CALL's `args[0]` —
    so an arm that read a fixed index missed the other shape. Worse, an arm that tested
    `callee & launderers` FIRST classified an ALIAS of `partial(setattr, …)` as a bare
    launderer and read the WRONG positional, so both the alias and the pre-bound key
    walked past.

        _p = functools.partial(setattr, _bi);  _p("print", getattr)      -> key at args[0]
        functools.partial(setattr, _bi, "print")(getattr)                -> key PRE-BOUND
        _sa = setattr;  _sa(_bi, "print", getattr)                      -> key at args[1]

    This predicate names the third one; `_setattr_key_operands` is the resolver.
    """
    return bool(
        isinstance(value, ast.Call)
        and _callee_names_of(value.func) & _PARTIAL_SPELLINGS
        and value.args
        and _callee_names_of(value.args[0]) & _SETATTR_SPELLINGS
    )


def _is_setattr_family(expr: ast.AST, launderers: frozenset[str] = frozenset()) -> bool:
    """The callee IS a `setattr`-family launderer — spelled, aliased, or a `partial`."""
    return bool(
        _callee_names_of(expr) & (_SETATTR_SPELLINGS | launderers) or _is_partial_setattr(expr)
    )


def _setattr_key_operands(
    callee: ast.AST, call: ast.Call, launderers: frozenset[str] = frozenset()
) -> list[ast.AST]:
    """Every AST node that can carry the attribute NAME a `setattr`-family call binds.

    ⛔ THE KEY POSITION IS A PROPERTY OF THE SPELLING (#14326 review P1):

      - bare `setattr(obj, "<name>", v)`          -> `args[1]` (a SPLAT makes the arity
        unknowable, so every arg is a candidate — this file's splat polarity);
      - a `partial(setattr, …)` (inline or aliased) -> the partial's own `args[1:]` (a
        PRE-BOUND key) **or** the call's `args[0]`;
      - an ALIAS (`_sa = setattr`) does NOT say which of the two it is, and resolving the
        binding is more machinery than the question needs — take BOTH positions, which can
        only over-collect (fail closed).

    Returning too MUCH can only RED a namespace store whose key is an allow-listed name;
    returning too little is the fail-open measured at `b6be337`.
    """
    names = _callee_names_of(callee)
    if names & _SETATTR_SPELLINGS:
        if len(call.args) >= 2:
            return [call.args[1]]
        return list(call.args) if any(isinstance(a, ast.Starred) for a in call.args) else []
    if names & launderers:
        return list(call.args[:2])
    # an INLINE `partial(setattr, …)`: only the call's first arg is read here. Its
    # PRE-BOUND key needs nothing from this resolver — the walk visits the partial
    # EXPRESSION itself, which `_namespace_rebind_names`' `_is_partial_setattr` arm reads
    # (measured: dropping `args[1:]` from this return leaves 31 passed, i.e. it was a
    # second spelling of one resolution and this file deletes clauses no mutant can red).
    return list(call.args[:1])


def _mapping_keys_in_keywords(call: ast.Call,
                              consts: dict[str, frozenset[str]]) -> set[str]:
    """Every STATIC string KEY a call's `keyword` list puts into a mapping.

    ⛔ CPYTHON KEEPS A KEYWORD IN A SEPARATE NODE LIST (#14326 review cycle 3, MEASURED
    GREEN at `80d2ab52`: `pin_refuses=False`, `m=2 c=2 i=0`, and the read EXECUTED at
    runtime): `Call(expr func, expr* args, keyword* keywords)`, and `keyword.arg` is `None`
    for a `**` spread (PEP 448), so `ns.update(print=getattr)` and
    `ns.update(**{"print": getattr})` never reached the key resolver — it read `args` only.
    `_name_reaching_operands` already reads `keywords` for the operand path, which is what
    makes this an IN-FILE asymmetry rather than a declared residual.

    A keyword's NAME is the key it delivers (`update(print=…)` → key `"print"`); a `**`
    spread delivers the keys of the mapping it expands, so its VALUE is resolved.
    """
    out: set[str] = set()
    for kw in call.keywords:
        if kw.arg is not None:
            out.add(kw.arg)
        else:
            out |= _mapping_keys_of(kw.value, consts)
    return out


def _mapping_keys_of(node: ast.AST | None,
                     consts: dict[str, frozenset[str]]) -> set[str]:
    """Every STATIC string KEY an expression delivers into a mapping.

    ⛔ THE KEYS ARRIVE IN THREE PLACES, NOT ONE (#14326 review cycle 3, EACH MEASURED GREEN
    at `80d2ab52` — the predicate GREEN, the read executed): a `Dict` DISPLAY, a `dict(…)`
    CALL whose keywords ARE its keys, and the CALL's own `keyword` nodes. A `**` spread
    inside a display (`{**{"print": …}}`) delivers the SPREAD's keys, so the recursion is
    on the VALUES; `_dict_display_leaves` recurses for the same reason one level over.

    The residual this does NOT resolve, stated rather than implied: `dict([("print", v)])`
    — a pair ITERABLE — is not a keyword list and is not read here.
    """
    if node is None:
        return set()
    if isinstance(node, ast.Dict):
        out: set[str] = set()
        for key, value in zip(node.keys, node.values, strict=True):
            out |= (_mapping_keys_of(value, consts) if key is None
                    else _string_keys_of(key, consts))
        return out
    if isinstance(node, ast.Call) and _callee_names_of(node.func) & _DICT_SPELLINGS:
        # ⛔ AND ITS POSITIONAL DISPLAY (#14326 review cycle 3, MEASURED GREEN at
        # `8e4ffbc5a0`: `builtins.__dict__.update(dict({"print": getattr}))` rebinds
        # `print` and the pin stayed at `m=2 c=2 i=0` while the read executed — the
        # keyword half above was resolved, the display handed to the SAME call was not, so
        # the verdict was decided by which argument slot the mapping arrived in).
        # ONLY A DISPLAY is taken: a pair ITERABLE stays the stated residual below, because
        # `_string_keys_of` over a list of pairs credits the values too — `dict([("leg",
        # "print")])` would drop `print` from the allow-lists and false-red.
        out = _mapping_keys_in_keywords(node, consts)
        for arg in node.args:
            if isinstance(arg, ast.Dict):
                out |= _mapping_keys_of(arg, consts)
        return out
    # a static string handed over bare — `setdefault("print", …)`, `setdefault(_p, …)`
    return _string_keys_of(node, consts)


def _mapping_mutation_keys(call: ast.Call, consts: dict[str, frozenset[str]],
                           offset: int) -> set[str]:
    """The keys an `update`/`setdefault` call delivers, from `call.args[offset:]`.

    `offset` is how the call is SPELLED, not what it does: a BOUND call carries the
    namespace in `func.value` and starts at 0, an UNBOUND one carries it in `args[0]`
    (`dict.setdefault(_ns, "print", getattr)`) and starts at 1.
    """
    args = list(call.args)[offset:]
    if _callee_names_of(call.func) & _SETDEFAULT_SPELLINGS:
        args = args[:1]
    out: set[str] = set()
    for arg in args:
        out |= _mapping_keys_of(arg, consts)
    out |= _mapping_keys_in_keywords(call, consts)
    return out


def _namespace_rebind_names(node: ast.AST, consts: dict[str, frozenset[str]],
                            namespaces: frozenset[str], launderers: frozenset[str]) -> set[str]:
    """Callee NAMES bound into a namespace by a STRING KEY — resolved, fail-closed.

    ⛔ THE STRING KEY IS THE AXIS `_bind_target_names` NEVER LEARNED (#14326 review, EACH
    MEASURED GREEN at `c380075b`: `m=2 c=2 i=0` with a third reach executing the read):

        setattr(builtins, "print", getattr)      # or globals()["print"] = getattr
        print(sdk, "_tortoise_fts_query")(…, kind="statement")

    Round 7 taught `_bind_target_names` to take an ATTRIBUTE SEGMENT (`_mod.print = …`),
    which closed half the hole the name allow-lists opened. It never learned the spellings
    that carry the SAME name as a VALUE: a `setattr` name argument, a string-keyed store
    into the namespace mapping, `.update({…})` on it, and `.__setitem__`. Each launders an
    allow-listed name into `getattr` exactly as the attribute form does — so the pin stayed
    GREEN while the `intentional_keep` grant for `run()` stood on a pin that could not go
    red.

    ⛔ ONE ROOT, NOT SEVEN CLAUSES (#14326 review): a `setattr`-family arm, an aliased
    LAUNDERER, an aliased RECEIVER and a NAME-carried KEY are all the same question — "does
    this statement put an allow-listed name into a namespace?" — so each is RESOLVED with
    machinery this file already owns rather than matched as another constant.

    ⛔ `setattr` CARRIES NO RECEIVER TEST, ON PURPOSE. The allow-lists are consulted by LAST
    SEGMENT, so `setattr(_page, "write", getattr)` → `_page.write(sdk, …)` genuinely reaches
    the read; scoping it to modules would open a new fail-open (measured, review (B)).
    The mapping arms DO test the receiver: `_counts["write"] = 0` binds nothing, and the
    control beside the rows pins that it stays green.
    """
    out: set[str] = set()
    if isinstance(node, ast.Call):
        callee = _callee_names_of(node.func)
        # `setattr(<obj>, "<name>", …)`, its ALIASES, and `partial(setattr, …)(…)` — ONE arm
        # (#14326 review P1): the key POSITION differs per spelling, so it is RESOLVED by
        # `_setattr_key_operands` instead of read off a fixed index.
        if _is_setattr_family(node.func, launderers):
            for arg in _setattr_key_operands(node.func, node, launderers):
                out |= _string_keys_of(arg, consts)
        elif _is_partial_setattr(node):
            # ⛔ A PRE-BOUND KEY IS BOUND WHEN THE PARTIAL IS BUILT, not when it is called
            # (#14326 review P1, MEASURED GREEN: `_p = functools.partial(setattr, _bi,
            # "print")` then `_p(getattr)` left the pin at `m=2 c=2 i=0`). The CALL SITE is
            # a bare name this pin does not follow, so the key is read where it actually is:
            # on the partial expression the walk already visits.
            for arg in node.args[1:]:
                out |= _string_keys_of(arg, consts)
        # `<ns>.update({"<name>": …})`, `<ns>.update(<name>=…)`, `<ns>.update(**{…})`,
        # `<ns>.setdefault("<name>", …)` — and the UNBOUND spelling of each.
        elif (callee & _MAPPING_MUTATORS
                and _is_namespace_expr(getattr(node.func, "value", None), namespaces)):
            # ⛔ `setdefault` SETS WHEN THE KEY IS ABSENT (#14326 review F1c, MEASURED GREEN):
            # it was outside the closed `{update}` list even on a proper namespace receiver —
            # and unlike `update({…})` it takes the key POSITIONALLY.
            out |= _mapping_mutation_keys(node, consts, offset=0)
        elif (callee & _MAPPING_MUTATORS and node.args
                and _is_namespace_expr(node.args[0], namespaces)):
            # ⛔ THE UNBOUND SPELLING WAS MIRRORED FOR `__setitem__` ONLY (#14326 review
            # cycle 3, MEASURED GREEN: `dict.setdefault(builtins.__dict__, "print", getattr)`
            # → `pin_refuses=False` while the bound form above was REFUSED). The namespace is
            # `args[0]` — ONE positional further in, which is the whole of `offset=1`.
            out |= _mapping_mutation_keys(node, consts, offset=1)
        elif "__setitem__" in callee and node.args:
            # ⛔ THE UNBOUND SPELLING PUTS THE NAMESPACE IN arg0 (#14326 review F1b, MEASURED
            # GREEN): `dict.__setitem__(builtins.__dict__, "print", getattr)` has receiver
            # `dict`, so a receiver-position test alone never sees the namespace.
            if _is_namespace_expr(node.func.value, namespaces):
                out |= _string_keys_of(node.args[0], consts)
            elif len(node.args) >= 2 and _is_namespace_expr(node.args[0], namespaces):
                out |= _string_keys_of(node.args[1], consts)
    elif isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store) \
            and _is_namespace_expr(node.value, namespaces):
        out |= _string_keys_of(node.slice, consts)
    return out


def _rebound_callee_names(tree: ast.AST,
                          consts: dict[str, frozenset[str]] | None = None) -> frozenset[str]:
    """Allow-listed CALLEE NAMES the source REBINDS — they are not pure any more.

    ⛔ THE PURITY TESTS ARE BY NAME, SO THE NAME CAN BE REBOUND (#14326 round 6, MEASURED
    GREEN at `81973329`): `print = getattr` then `print(sdk, _N)(query=…, kind="statement")`
    passed the pin (`m=2 c=2 i=0`) — and the same for `dict = getattr` and `type = getattr`
    with the literal inline — while the file's own comment claimed "a name alias of
    `getattr` does not become a way through". It did, through every name this file trusts.
    A shadowed/assigned pure name is therefore dropped from the allow-list: fail-closed.

    ⛔ AND THE BIND FORMS ARE NOT EXHAUSTED BY `Assign` (#14326 round 7, each MEASURED
    GREEN at `d790e1f0` while `print = getattr` was REFUSED — the same laundering, one
    spelling over):

        from builtins import getattr as print   # an IMPORT binds the name
        print(sdk, "_tortoise_fts_query")(…, kind="statement")

        match getattr:                          # a MATCH CAPTURE binds the name
            case print:
                print(sdk, "_tortoise_fts_query")(…, kind="statement")

    The import arm takes `asname`, else the first dotted component (`import a.b` binds
    `a`); `from x import *` binds an UNKNOWABLE set, so it drops EVERY allow-listed name —
    the polarity this map already takes for a computed value. The capture arm mirrors
    `_str_constants`' own `MatchAs`/`MatchMapping.rest` enumeration.

    ⛔ AND A `with` BINDS ITS `optional_vars`, NOT ITS BODY (#14326 round 7, MEASURED — the
    arm walked `ast.walk(node)` for `With`/`AsyncWith`, which has no `.target`, so EVERY
    NAME IN THE WITH-BODY counted as a bind):

        with open("/dev/null") as _fh:
            print(_fh)                     # a LOAD of `print`…
        print("_tortoise_fts_query")       # …so this RECORDING call was REFUSED

    — a false RED on the pin's own recording-direction control, decided by whether an
    unrelated line happened to sit inside a `with`. `items[*].optional_vars` is the bind.
    """
    consts = _str_constants(tree) if consts is None else consts
    # ⛔ BOTH RECEIVER AND LAUNDERER ARE RESOLVED, NOT SPELLED (#14326 review F1/F1b/F4):
    # a one-bind alias of `setattr` or of the namespace mapping walked past the first cut.
    namespaces = frozenset(_aliases_of(tree, _is_namespace_expr, loops=True))
    launderers = frozenset(_aliases_of(tree, _is_setattr_like, loops=True))
    out: set[str] = set()
    for node in ast.walk(tree):
        # ⛔ NO GENERIC `Name(Store)` WALK (#14326 round 8, measured: adding one and then
        # removing it leaves 25 passed — the per-form arms below already reach every bind,
        # because `_bind_target_names` takes a bare `Name` as well as an attribute segment).
        # The four spellings a previous round believed it had lost (an `AnnAssign`, a walrus,
        # a `for` target, an augmented bind) are each a ROW below, so they red whichever arm
        # covers them rather than standing on a walk nobody can red.
        if isinstance(node, ast.Assign):
            for target in node.targets:
                out |= _bind_target_names(target)
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign, ast.NamedExpr,
                               ast.For, ast.AsyncFor, ast.comprehension)):
            # Every arm here binds through `.target` — an annotated/augmented/walrus bind,
            # and a `for`/comprehension TARGET, which carries no `Assign` node at all.
            out |= _bind_target_names(node.target)
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            # ⛔ `optional_vars` IS THE BIND (`with … as X`); the ITEMS' context expressions
            # and the BODY are neither.
            for item in node.items:
                if item.optional_vars is not None:
                    out |= _bind_target_names(item.optional_vars)
        elif isinstance(node, ast.ExceptHandler):
            if node.name:
                out.add(node.name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(node.name)
        elif isinstance(node, ast.arg):
            out.add(node.arg)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                if alias.name == "*":
                    # `from x import *` binds an unknowable set — fail closed.
                    out |= _PURE_READS | _RECORDING_CALLEES
                else:
                    out.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, (ast.Call, ast.Subscript)):
            # ⛔ THE SAME NAME, CARRIED AS A STRING CONSTANT (#14326 review at `c380075b`) —
            # `setattr(builtins, "print", getattr)` and a string-keyed store into
            # `globals()`/`vars(x)`/`x.__dict__` bind the callee name exactly as the
            # attribute arm above does. See `_namespace_rebind_names`.
            out |= _namespace_rebind_names(node, consts, namespaces, launderers)
        elif isinstance(node, ast.Match):
            for case in node.cases:
                for capture in ast.walk(case.pattern):
                    name = (getattr(capture, "name", None)
                            or getattr(capture, "rest", None))
                    if name:
                        out.add(name)
    # ⛔ NO ALLOW-LIST INTERSECTION (#14326 round 8, measured: `frozenset(out)` leaves 25
    # passed) — all three consumers test `_callee in rebound` only on the branch where
    # `_callee` is already an allow-listed name, so extra entries cannot change a
    # verdict. A filter no mutant can red reads as a guard and is not one.
    return frozenset(out)


#: Callees that RECORD a string and cannot execute it. Kept separate from `_PURE_READS`
#: because that set also grades MUTATION (`_PURE_CALLEES`), where a logger method must stay
#: a mutator-adjacent unknown; this set is used by the CENSUS arms ONLY.
#: ⛔ EACH ENTRY IS A CENSUS EXEMPTION, i.e. a potential FAIL-OPEN, so each must have a
#: recording control that reds if it is dropped — the controls below cover the inline,
#: NAME-carried, splatted and keyword spellings.
#: ⛔ `setdefault` IS A FETCH, NOT A RECORDING (#14326 round 7, MEASURED GREEN with it here):
#: `dict.setdefault(k)` RETURNS the value at `k`, so
#:     _FETCHERS.setdefault("_tortoise_fts_query")(sdk)(…, kind="statement")
#: passed the pin (mentions 2, calls 2, indirect 0) while the identical lookup spelled
#: `_FETCHERS.get(…)` was refused. The exemption is granted on the NAME, so it could not tell
#: that apart from `_COUNTS.setdefault(literal, 0)` — whose SECOND ARGUMENT is what makes it
#: inert. REMOVED: a row refuses the fetch spelling, and the inert one is an ACCEPTED,
#: STATED false red rather than bought with an exemption that also admits a fetch.
_RECORDING_CALLEES = frozenset({"info", "write"})


def _carries_read_name(node: ast.AST, consts: dict[str, frozenset[str]]) -> bool:
    """True when `node` may statically BE one of the product read names."""
    return not _possible_strings(node, consts).isdisjoint(_READ_NAMES)


#: Callees that turn a STRING into EXECUTED CODE. Fail-closed by name: the census asks
#: whether an operand IS the read name, and a code string only CONTAINS it — widening the
#: census to substring containment would restore the blanket mention false red (#14326
#: review P0-3, measured: `exec`/`eval`/`compile` over a literal were GREEN while the read
#: executed). `__import__` is NOT here: its argument is a module name, not code.
_CODE_EXECUTING_CALLEES = frozenset({"exec", "eval", "compile"})


def _is_read_ref(node: ast.AST, parents: dict[int, ast.AST],
                 consts: dict[str, frozenset[str]],
                 rebound: frozenset[str] = frozenset()) -> bool:
    """True when `node` is a REACH on the read — a mention the census must account for."""
    if isinstance(node, ast.Attribute) and node.attr in _READ_NAMES:
        return True
    # ⛔ A SUBSCRIPT WHOSE KEY RESOLVES TO THE READ NAME IS ITSELF A REACH (#14326 review
    # P0-2, measured). The `Constant` arm below grades the EXACT literal, and this file's
    # docstring claims a value "SELECTED out of a LITERAL container or slice IS resolved",
    # but a key ASSEMBLED from literals is statically resolvable and was not consulted:
    #
    #     vars(type(sdk))["_tortoise" + "_fts_query"](sdk, query=…, kind="statement")
    #     → mentions=2 calls=2 indirect=0, refuses=False
    #
    # while the direct spelling of the SAME key is refused. `_possible_strings` already
    # resolves `+`/f-string/selection/sequence subscripts, so this arm asks the resolver the
    # question the `Constant` arm asks the literal — one axis, one form over. The `ctx` test
    # is preserved: a subscript STORE (`_counts[_N] = 0`) stays green. ⛔ IT MUST SIT HERE,
    # ABOVE the `Constant` gate: that gate returns False for every non-`Constant` node, so
    # an arm placed below it is DEAD CODE that its own row would still pass (measured — the
    # row went red only after the arm moved up).
    if isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Load) \
            and not _possible_strings(node.slice, consts).isdisjoint(_READ_NAMES):
        return True
    # ⛔ A NAME IS ONLY A REACH WHERE IT CAN BE EXECUTED (#14281 review P2, measured): the
    # Name arm was blanket, so a provenance constant beside `git_sha`/`db_uri` —
    # `_NOTE = "_tortoise_fts_query"` then `print(f"measuring {_NOTE}")` — red the census
    # with "an alias, a string reference or a getattr", the exact false red the Constant
    # arm was narrowed to remove. Same executable-parent test, same reason. ⛔ The parent is
    # tested for being ABLE TO EXECUTE, so a name carried through a container into a
    # `getattr(*…)` splat is not this arm's — it is `_indirect_reads`' (#14279 review F3).
    if isinstance(node, ast.Name) and _carries_read_name(node, consts):
        _np = parents.get(id(node))
        # ⛔ THE NAME MUST BE THE SUBSCRIPT'S **INDEX**, NOT ITS CONTAINER (#14326 review
        # P2, measured RED at `399ddc2`): the arm credited ANY `Load` subscript over a name
        # that resolves to the literal, so an INERT provenance container red the census —
        #     _READ = {"name": "_tortoise_fts_query"}
        #     print(f"measuring {_READ['name']}")
        # went from 2 mentions to 3 (`[('_READ', 308)]`) purely because `_values`' DICT arm
        # binds the WHOLE mapping to its name (deliberately: it is what keeps `match _m:`
        # captures resolvable). The lever shape is the opposite position — the read NAME as
        # the KEY that fetches the callable (`vars(type(sdk))[_N]`) — so only the SLICE
        # counts. `_READ.get("name")` already stayed green (its Name's parent is the
        # `Attribute`), which is what makes the un-narrowed arm's polarity syntax-decided
        # rather than executable-decided: `_READ["name"]` and `_READ.get("name")` are the
        # same non-read. Both directions are pinned in
        # `test_EVERY_static_binding_form_is_a_READ_reach`.
        # ⛔ A REBOUND PURE NAME IS NOT PURE (#14326 round 6, measured): `print = getattr`
        # then `print(sdk, _N)(…)` passed the pin. `rebound` drops every allow-listed
        # callee name the source rebinds — `_PURE_READS` or `_RECORDING_CALLEES`, by NAME
        # or (round 7) by the ATTRIBUTE segment a call is allow-listed by — so neither list
        # can be laundered by shadowing.
        if isinstance(_np, ast.Call):
            _callee = (getattr(_np.func, "id", None)
                       or getattr(_np.func, "attr", None))
            if ((_callee not in _PURE_READS and _callee not in _RECORDING_CALLEES)
                    or _callee in rebound):
                return True
        return (isinstance(_np, ast.Subscript) and _np.slice is node
                and isinstance(_np.ctx, ast.Load))
    # ⛔ A STRING LITERAL MATTERS ONLY WHERE IT CAN BE EXECUTED. The census counts mentions
    # because a bare name can be handed to `getattr`; a `Constant` in dict-value position is
    # INERT — and recording provenance is the direction #14289 asks for. Measured RED
    # before this narrowing, on both of:
    #     "read": "_tortoise_fts_query",
    #     "measured_by": f"{'_tortoise_fts_query'}",
    # which the message called "an alias, a string reference or a getattr" — for a string
    # that could not reach the read.
    # ⛔ …AND IT IS KEPT UNDER A `Subscript` TOO (#14281 review P2, measured): the first form
    # kept only `Call` parents, so a THIRD reach added beside the two real ones —
    #     _lever = vars(type(sdk))["_tortoise_fts_query"](query=q["query"], limit=10)
    # — left the census at 2 mentions and `_read_calls` at 2 calls, and the pin passed while
    # a lever reached a read the door does not name. The literal's parent there is the
    # SUBSCRIPT, not the call, so the executed spelling spans both.
    # ⛔ …AND THE `Call` PARENT MUST BE ABLE TO EXECUTE IT (#14281 review P2, measured): a
    # blanket `Call` parent also admitted `print("_tortoise_fts_query")`, which cannot reach
    # the read — the very false red the dict-value narrowing removed. `getattr`/`setattr` are
    # caught earlier by `_indirect_reads`; they are kept here so the census stays honest if
    # that guard is ever reordered again.
    _parent = parents.get(id(node))
    if not isinstance(node, ast.Constant) or node.value not in _READ_NAMES:
        return False
    # ⛔ A WALRUS IS TRANSPARENT HERE (#14326 round 5, measured):
    #     vars(type(sdk))[(_N := "_tortoise_fts_query")](…)   # kind="statement"
    # delivered the literal to the same fetch position the plain `vars(...)["…"]` reaches,
    # but the `Constant`'s immediate parent is the `NamedExpr`, so the `Subscript` test below
    # never saw it — mentions=2, calls=2, `refuses=False` while the DIRECT spelling is
    # refused. Step out of the walrus to grade the position the value actually lands in.
    if isinstance(_parent, ast.NamedExpr):
        _parent = parents.get(id(_parent)) or _parent
    if isinstance(_parent, ast.Subscript):
        # ⛔ A STORE CANNOT BE CALLED (#14281 review P2, measured): counting every subscript
        # red `_counts["_tortoise_fts_query"] = 0` and
        # `_counts2["_tortoise_fts_query"] += 1` — the recording direction #14289 wants, and
        # the same "can this be EXECUTED?" rule the `Call` arm follows.
        return isinstance(_parent.ctx, ast.Load)
    # ⛔ FAIL-CLOSED ON AN UNKNOWN CALLEE (#14281 review P1, three spellings measured GREEN
    # against the allow-list this replaced): `_g = getattr;
    # _g(sdk, "_tortoise_fts_query")(…)`, `getattr.__call__(…)` and
    # `dict.__getitem__(vars(type(sdk)), "_tortoise_fts_query")(…)` each carry a lever. The
    # allow-list answered the `print("…")` false red by OPENING the hole — the wrong
    # direction. `_PURE_READS` is the set this file already trusts not to reach a callee by
    # name (it contains `print`, `len`, `dict`, …), so `print("…")` stays green and a name
    # alias of `getattr` does not become a way through.
    # ⛔ A REBOUND PURE NAME IS NOT PURE, AND A RECORDING CALLEE IS NOT AN EXECUTOR
    # (#14326 round 6, both measured at `81973329`): `print = getattr` then
    # `print(sdk, _N)(…)` passed the pin, while `_LOG.info("_tortoise_fts_query")` and
    # `_COUNTS.setdefault("_tortoise_fts_query", 0)` were FALSE REDS although the same
    # recording act written as a subscript STORE must stay green. `rebound` carries the
    # bind forms BOTH lists need — see `_rebound_callee_names`, which owns that enumeration
    # (round 7 added imports, `match` captures, `with` targets and attribute segments).
    if not isinstance(_parent, ast.Call):
        return False
    _callee = getattr(_parent.func, "id", None) or getattr(_parent.func, "attr", None)
    return ((_callee not in _PURE_READS and _callee not in _RECORDING_CALLEES)
            or _callee in rebound)


def _dict_display_leaves(node: ast.Dict) -> list[ast.AST]:
    """Every node a `**{…}` display delivers — its KEYS and its VALUES, RECURSIVELY.

    ⛔ THE ONE-LEVEL FORM WAS A FAIL-OPEN (#14326 round 8, MEASURED GREEN with the pin at 25
    passed): `_PageFetcher(**{**{"name": "_tortoise_fts_query"}})` delivered the read's name
    one nesting level down and neither this branch nor `_possible_strings`' `Dict` arm (which
    unions KEYS only) could see it — so the verdict was decided by a nesting level. A display
    nested in a display is the same delivery.
    """
    out: list[ast.AST] = [k for k in node.keys if k is not None]
    for value in node.values:
        if value is None:
            continue
        out.extend(_dict_display_leaves(value) if isinstance(value, ast.Dict) else [value])
    return out


def _name_reaching_operands(call: ast.Call) -> list[ast.AST]:
    """EVERY operand of `call` — every positional (the first one included) and every keyword.

    ⛔ THE FIRST POSITIONAL IS AN OPERAND, AND EXCLUDING IT WAS A MEASURED FAIL-OPEN
    (#14326 review P0-1). The exclusion rested on a claim that is false for any spelling
    that does not fold to a single `Constant`:

        a literal in the FIRST position is already a mention through the `Constant` arm,
        so the slice only ever subtracts an operand the census has already counted

    — the `Constant` arm keys on EXACT equality with `_READ_NAMES`, so `"_tortoise" +
    "_fts_query"` in the first position is two `Constant`s, neither in that set, and the
    slice subtracted an operand the census had NOT counted:

        sdk.__getattribute__("_tortoise" + "_fts_query")(…, kind="statement")   GREEN
        vars(type(sdk)).get("_tortoise" + "_fts_query")(sdk, …)                GREEN
        _helper(sdk, "_tortoise" + "_fts_query")                                 GREEN

    All three `m=2 c=2 i=0` with a third reach executing the read, while the DIRECT
    spelling of each is refused. A splat is included for the same reason `getattr(*_pair)`
    is fail-closed (#14279 review F3): its arity and position are not statically knowable
    here, so a one-element positional list is still inspected.
    """
    # ⛔ THE LITERAL-SPLAT EXPANSION IS GONE (#14326 review P3): `_splat_operands`
    # expanded a `Starred` over a literal one level, and NO mutant could red those two
    # lines — the Starred itself is in this list, and `_possible_strings` already resolves
    # THROUGH a `Starred` into its literal container (measured: deleting the expansion
    # leaves `getattr(*[sdk, "…"])` red). A clause no mutant can red reads to the next lane
    # as an independent check; this file deletes them instead.
    # ⛔ NO `[1:]` (#14326 review P0-1, measured — see the docstring). Round 8 recorded the
    # slice as "UNPINNED either way" on the strength of a false premise: widening left 25
    # passed because NO row held a literal assembled in the first position. Rows do now, and
    # the slice reds them back to GREEN, so the exclusion is a fail-open rather than a
    # narrower statement of intent. `list(call.args)` includes every `Starred` already (a
    # splat is an expression in this list), so the separate extend is gone with it.
    operands = list(call.args)
    # ⛔ A KEYWORD IS A DELIVERY TOO (#14326 round 6, MEASURED GREEN at `81973329`):
    # `_name_reaching_operands` scanned `call.args` only, and CPython's grammar puts a
    # keyword in a SEPARATE `keyword` node (`Call(expr func, expr* args, keyword*
    # keywords)`), so a literal handed over by name never reached the census or
    # `_indirect_reads`:
    #     _PageFetcher(name="_tortoise_fts_query")          -> mentions=2 calls=2 indirect=0
    #     _PageFetcher(**{"name": "_tortoise_fts_query"})   -> same
    # — identical to the positional-splat row that WAS pinned, so the delivery class looked
    # closed. `keyword.arg` is `None` for a `**` mapping, so the value is inspected either
    # way.
    for kw in call.keywords:
        if kw.arg is None and isinstance(kw.value, ast.Dict):
            # `**{"name": "_tortoise_fts_query"}`: the VALUES are the argument values, and a
            # KEY can be the read's NAME too (`**{_N: 1}`, round 7 MEASURED GREEN) — so BOTH
            # sides are walked. `_possible_strings` unions a dict literal's KEYS only (the
            # polarity `for` targets need), which is why this position is walked here.
            operands.extend(_dict_display_leaves(kw.value))
        elif kw.arg is None and isinstance(kw.value, ast.Call) and \
                (getattr(kw.value.func, "id", None)
                 or getattr(kw.value.func, "attr", None)) == "dict":
            # `**dict(name="_tortoise_fts_query")` — the same delivery built by a CALL
            # (round 7, MEASURED GREEN). Only `dict` is read this way: an arbitrary callee's
            # keyword is not statically a mapping.
            operands.extend(k.value for k in kw.value.keywords)
        else:
            operands.append(kw.value)
    return operands


def _indirect_reads(tree: ast.AST, consts: dict[str, frozenset[str]],
                     rebound: frozenset[str] = frozenset()) -> list[ast.Call]:
    """Calls that reach the read BY NAME through an argument — the census's own diagnosis.

    ⛔ MEASURED BYPASS (#14279 review F3/P2), each GREEN before this arm resolved a name
    carried through a container: `_pair, = (_N,)` or `_pair = (sdk, _N)` followed by
    `getattr(sdk, _pair)(…)` / `getattr(*_pair)(…)`, and `getattr(*[sdk, _N])(…)`. The
    census cannot recover them (the `_is_read_ref` Name arm requires an executable parent),
    so this fires FIRST, under its own diagnosis, before `len(mentions) == 2` can mask it.

        # ⛔ THE FETCHER NAME IS NOT THE HOLE; THE CONTAINER IS (#14326 round 5). Four
        # escapes were MEASURED GREEN at `8f13cb24` (`refuses=False`, mentions=2, calls=2)
        # while the DIRECT spelling of each callee was refused:
        #
        #     _g = sdk.__getattribute__(*("_tortoise_fts_query",)); _g(query=…, kind="statement")
        #     _A = ("_tortoise_fts_query",); operator.attrgetter(*_A)(sdk)(…, kind="statement")
        #     _A = (…); dict.get(*[sdk.__dict__, *_A])(…, kind="statement")
        #     vars(type(sdk))[(_N := "_tortoise_fts_query")](…, kind="statement")   [NamedExpr]
        #
        # The first three deliver through a SPLAT (the arm below); the fourth is the
        # `Constant` arm's NamedExpr step. Each is a row in
        # `test_EVERY_static_binding_form_is_a_READ_reach`.
    """
    out: list[ast.Call] = []
    # ⛔ THE CODE-EXECUTING NAMES ARE RESOLVED, NOT ENUMERATED (#14326 review cycle 2,
    # MEASURED: with the bare set alone `_run = exec; _run("<read>")`,
    # `getattr(builtins, "exec")(…)` and `builtins.__dict__["exec"](…)` all left
    # `mentions=2 calls=2 indirect=0` while the read executed, end-to-end on the shipped
    # tool). The spelling axis cannot converge (a lambda, a `getitem`, an alias of an
    # alias), which is this file's own reason for resolving rather than listing — the same
    # machinery the getattr/setattr laundering axis uses, asking a different question.
    _builtins_names, _builtins_dict_names = _builtins_root_names(tree)
    # ⛔ A `from builtins import <callee> as …` NEEDS NO ARM HERE (#14326 merge with the
    # sibling lane's `490ae765a6`). This function carried one, and a mutant MEASURED it INERT
    # on the merged tree: `_aliases_of`'s `ImportFrom` arm now resolves that bind for EVERY
    # axis (its `builtins` member set IS `_CODE_EXECUTING_CALLEES | _REGISTRY_BUILTINS |
    # {"__dict__"}` — it was `_CODE_EXECUTING_CALLEES` alone until #14326 review cycle 5 added
    # the FETCHER (`getattr`) and the mapping (`__dict__`) spellings; a lane trusting a stale
    # note here reverts that widening believing it redundant), so the arm reddened no
    # row — and the file's own rule is to DELETE a clause no mutant can red rather than leave
    # it reading as an independent check. The row it pinned is the sibling's
    # `IMPORTED `exec` (`from builtins import exec as _run`)`; a second row for one clause is
    # the double-pinning the file declines.
    #: The FETCHER names resolved too: `_g = getattr; _g(builtins, "exec")(…)` and
    #: `_dg = dict; _dg.get(_d, "exec")(…)` were GREEN, and the sibling axis has resolved
    #: `_g = getattr` since round 21 (a hard-coded name there would be the same asymmetry).
    #: Statically-visible aliases of the three builtins used to BUILD the mapping (#14326
    #: round 35) — the composition half `getattr_fetchers`/`dict_fetchers` do not reach.
    _builtins_launderers = _builtin_name_aliases(tree)
    _getattr_literal = frozenset({"getattr"})
    _getattr_fetchers = frozenset({"getattr"}) | frozenset(_aliases_of(
        tree,
        lambda n: (isinstance(n, ast.Name) and n.id == "getattr")
        or (isinstance(n, ast.Attribute) and n.attr == "getattr"
            and isinstance(n.value, ast.Name) and n.value.id in _builtins_names)
        or _builtin_lookup_name(n, _builtins_names, consts, _builtins_dict_names,
                                _builtins_launderers, members=_getattr_literal,
                                getattr_fetchers=_getattr_literal,
                                dict_fetchers=frozenset({"dict"})) in _getattr_literal,
        loops=True))
    _dict_fetchers = frozenset({"dict"}) | frozenset(_aliases_of(
        tree, lambda n: isinstance(n, ast.Name) and n.id == "dict", loops=True))
    _code_executing_names = _CODE_EXECUTING_CALLEES | frozenset(_aliases_of(
        tree,
        lambda n: (isinstance(n, ast.Name) and n.id in _CODE_EXECUTING_CALLEES)
        or (isinstance(n, ast.Attribute) and n.attr in _CODE_EXECUTING_CALLEES
            and isinstance(n.value, ast.Name) and n.value.id in _builtins_names)
        # ⛔ THE FETCHERS RESOLVE HERE TOO (#14326 round 35, MEASURED: `_gx = _gr(builtins,
        # "exec"); _gx(<read>)` was GREEN with `_gr = getattr`, while the ONE-hop
        # `_gr(builtins, "exec")(<read>)` was RED — the fetcher set was threaded into the
        # per-call lookup below but not into THIS alias closure, so a callee that the closure
        # would have collected was never collected. Same axis, same asymmetry class this file
        # deletes on sight.)
        or _builtin_lookup_name(n, _builtins_names, consts, _builtins_dict_names,
                                _builtins_launderers,
                                members=_CODE_EXECUTING_CALLEES,
                                getattr_fetchers=_getattr_fetchers,
                                dict_fetchers=_dict_fetchers) is not None,
        loops=True))
    for n in ast.walk(tree):
        if not isinstance(n, ast.Call):
            continue
        _callee = getattr(n.func, "id", None) or getattr(n.func, "attr", None)
        # A pure or RECORDING callee cannot fetch by name — the `print("…")` control —
        # unless the source REBOUND that name (#14326 round 6).
        if (_callee in _PURE_READS or _callee in _RECORDING_CALLEES) \
                and _callee not in rebound:
            continue
        # ⛔ CODE BUILT IN A STRING IS A REACH THE CENSUS CANNOT SEE (#14326 review P0-3,
        # measured). Every arm below asks whether an OPERAND *IS* the read name; a code
        # string only CONTAINS it, and widening the census to substring containment is the
        # blanket mention red this file deleted (a provenance note that merely names the read
        # would red). `exec`/`eval`/`compile` are therefore fail-closed by RESOLVED name — a
        # door mirror has no reason to build code at runtime — and the three shapes were
        # MEASURED GREEN through the pin's own predicate while the read executed:
        #
        #     exec("results = sdk._tortoise_fts_query(query=…, limit=10)")   m=2 c=2 i=0
        #     eval("sdk._tortoise_fts_query(query=…, limit=10)")            m=2 c=2 i=0
        #     exec(compile(<literal>, "<s>", "exec"))                      m=2 c=2 i=0
        #
        # ⛔ "BY RESOLVED NAME" IS THE WHOLE POINT (#14326 review cycle 2, EACH MEASURED
        # GREEN with the bare-name arm alone in place): `_run = exec; _run("<code>")`,
        # `getattr(builtins, "exec")(…)` and `builtins.__dict__["exec"](…)` are the SAME
        # callee one launder further out. The file's own rule (`_builtin_lookup_name`'s
        # docstring, and `_aliases_of`) is that a STATICALLY VISIBLE alias is not the
        # declared "computed at runtime" residual, so the arm resolves the set with the
        # machinery the getattr/setattr laundering axis already uses rather than enumerating
        # spellings.
        #
        # NOT fail-closed, deliberately: `__import__` / `importlib.import_module`, whose
        # argument is a MODULE name (the import path axis, graded by
        # `_is_import_path_object`), not code.
        #
        # ⛔ THE REMAINING RESIDUAL IS THE FILE'S ALREADY-DECLARED ONE, AND IT IS PINNED GREEN
        # (#14326 review cycle 3). A callee obtained by a NON-ENUMERABLE route escapes every
        # resolved set here — `operator.attrgetter("exec")(builtins)`, `partial(exec)`,
        # `(lambda f: f)(exec)`, `getattr(sys.modules["builtins"], "exec")`,
        # `__import__("builtins").__dict__["exec"]`. That is not a new hole: it is the
        # "list of ways to name a callable does not terminate" residual this file states for
        # the registry axis (#14326 round 28/33, `_is_modules_key` above), and it is pinned
        # GREEN by `test_the_CODE_EXECUTION_axis_residuals_are_NAMED_and_pinned_GREEN` so a
        # later lane cannot read this arm as covering it. Closing one spelling of it would be
        # the half-arm the round-26 note rejects as worse than none.
        if _callee in _code_executing_names or \
                _builtin_lookup_name(n.func, _builtins_names, consts, _builtins_dict_names,
                                     _builtins_launderers,
                                     members=_CODE_EXECUTING_CALLEES,
                                     getattr_fetchers=_getattr_fetchers,
                                     dict_fetchers=_dict_fetchers) is not None:
            out.append(n)
            continue
        if not any(_carries_read_name(arg, consts)
                   for arg in _name_reaching_operands(n)):
            continue
        # ⛔ THE OPERAND TEST IS THE WHOLE TEST — NO CALLEE-SHAPE GATE (#14326 review P0-1,
        # measured). This used to require `_callee in _NAMEFETCH_CALLEES or n.keywords or
        # <a splat>`, on a claim that named fetchers other than `getattr`/`setattr` "all
        # deliver through a SPLAT" and were therefore DEAD. That claim is false: they also
        # deliver through ONE PLAIN POSITIONAL, which the gate then dropped — the split-name
        # spelling of each callee measured GREEN (`refuses=False`, mentions=2, calls=2,
        # indirect=0) while its direct-literal spelling was refused:
        #
        #     sdk.__getattribute__("_tortoise" + "_fts_query")(…, kind="statement")
        #     vars(type(sdk)).get("_tortoise" + "_fts_query")(sdk, …)
        #     operator.attrgetter("_tortoise" + "_fts_query")(sdk)(…)
        #     _helper(sdk, "_tortoise" + "_fts_query")            # the name in arg 1
        #     exec("results = sdk._tortoise_fts_query(…)")         # static code in a string
        #
        # So `_NAMEFETCH_CALLEES` is DELETED: once the operand carries the read name, every
        # non-pure callee is fail-closed, which is the rule the splat and keyword arms had
        # already reached by different routes. The `print("…")` control is unaffected —
        # purity is decided by `_PURE_READS`/`_RECORDING_CALLEES` ABOVE, not here.
        out.append(n)
    return out


def _read_reach(tree: ast.AST, consts: dict[str, frozenset[str]] | None = None,
                parents: dict[int, ast.AST] | None = None,
                ) -> tuple[list[ast.AST], list[ast.Call], list[ast.Call]]:
    """`(mentions, calls, indirect)` — THE reach census, for the pin AND its own tests.

    ⛔ ONE FUNCTION (#14279 review F2). Every mutant table in this file resolves the read
    through THIS call, so a test cannot pass by using a different (looser) copy of the walk
    — the failure mode rule 3 names, and the reason `_collection_alias_predicate` exists.

    ⛔ `indirect` IS EVALUATED *BEFORE* THE MENTIONS CENSUS (#14281 review F3). It used to
    sit after it, where it was DEAD: every node satisfying it is also `_is_read_ref`-true (a
    `Constant` in `_READ_NAMES`, or a `Name` bound to one), so the census at
    `len(mentions) == 2` always red first — measured three ways, all of them red at the
    census: `getattr(...)` replacing the warm-up, `getattr(...)` added alongside, and a
    bound-method alias. The class was covered, so nothing was a false PASS; but an
    assertion that can never be the failing row reads to the next lane as an independent
    guard, and it is not one. Returned first, so a caller can fire it first.
    """
    consts = _str_constants(tree) if consts is None else consts
    parents = _parents_of(tree) if parents is None else parents
    rebound = _rebound_callee_names(tree, consts)
    mentions = [n for n in ast.walk(tree) if _is_read_ref(n, parents, consts, rebound)]
    return mentions, _read_calls(tree), _indirect_reads(tree, consts, rebound)


#: The kwargs `tortoise/hosted_api.py::search` hands the read, as of this pin. A LITERAL
#: on purpose: `_door_read_contract()` derives the same set from the door, and this is the
#: independent statement that reds when the DOOR moves — a derivation checked only against
#: itself cannot tell "unchanged" from "widened".
_EXPECTED_DOOR_ARGS = frozenset({"query", "limit", "read_status_out", "_truncation_out"})


def _door_read_contract(
    hosted_source: str | None = None, sdk_source: str | None = None
) -> set[str]:
    """The kwargs the SHIPPED door hands the read — DERIVED from `hosted_api.py`.

    A mirror must not name an argument the door does not name: `kind` selects a
    different mode entirely (omit `query` and it returns every Point of that kind),
    `_elevated_timeout_ms` is the read's collective budget, `pool_size` / `order_by` /
    `claim_class` / `exclude_status` each change WHICH rows survive `limit`. So the
    allow-list is not "the read's parameters" (that is 40-odd names, most of them
    levers) and not "private keywords" (that forbids the door's own `_truncation_out`
    out-sink): it is **exactly what `hosted_api.py::search` passes**, plus the
    out-sink this harness needs to declare the legs it measured.

    ⛔ Two successive shapes of this check were falsified by review before this one: a
    "any `_`-prefixed keyword" test false-red a faithful mirror passing the door's
    `_truncation_out`, and a "`None`-defaulted parameter is an out-sink" test let 18
    levers through (`kind`, `_elevated_timeout_ms`, `pool_size`, `claim_class`,
    `order_by`, `exclude_status`…). The door's own call site is the one authority that
    is neither too narrow nor too wide, and it is measured, not remembered.

    ⛔ THE TWO SOURCES ARE PARAMETERS (#14326 review P1, measured: replacing each of the
    four assertions below with `True` left **27 passed**). Every clause here is a
    fail-closed guard on the door — `len(door_calls) == 1` is what stops a hoisted
    `_do_search()` from widening the allow-list, `allowed == _EXPECTED_DOOR_ARGS` is the
    backstop for a lever the bool-lever intersection cannot see — and NONE of them could
    be red by any row while the sources were hard-wired to the two real files: a row can
    only mutate a file it is allowed to write. `test_every_door_contract_clause_reds`
    therefore synthesises the mutation it needs from the REAL source text and passes it in,
    so each clause now has a row that fails when the clause is deleted.
    """
    sdk_tree = ast.parse(sdk_source if sdk_source is not None else _SDK.read_text())
    defs = [n for n in ast.walk(sdk_tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            and n.name == "_tortoise_fts_query"]
    assert len(defs) == 1, f"sdk.py holds {len(defs)} `_tortoise_fts_query` definitions"
    spec = defs[0].args
    positional = [a.arg for a in spec.args if a.arg != "self"]
    bool_levers = {a.arg for a, d in
                   list(zip(positional[len(positional) - len(spec.defaults):],
                            spec.defaults, strict=True))
                   + list(zip(spec.kwonlyargs, spec.kw_defaults, strict=True))
                   if isinstance(d, ast.Constant) and isinstance(d.value, bool)}
    assert {"_rerank_candidates", "_record_reads", "_valid_time_rerank",
            "include_terminal"} <= bool_levers, sorted(bool_levers)

    hosted_tree = ast.parse(hosted_source if hosted_source is not None else _HOSTED.read_text())
    hosted_parents: dict[int, ast.AST] = {}
    for node in ast.walk(hosted_tree):
        for child in ast.iter_child_nodes(node):
            hosted_parents[id(child)] = node
    door_calls = [c for c in _read_calls(hosted_tree)
                  if _owner(c, hosted_parents) == "search"]
    # ⛔ EXACTLY the `/v1/search` handler's call: a union over every read call in the file
    # let an unrelated endpoint's `kind="statement"` widen the allow-list, after which the
    # harness could name `kind` on the measured read and stay green (measured).
    assert len(door_calls) == 1, (
        f"`hosted_api.py::search` holds {len(door_calls)} read calls — this contract is "
        "derived from exactly one")
    allowed: set[str] = set()
    for call in door_calls:
        allowed |= {k.arg for k in call.keywords if k.arg is not None}
        allowed |= set(positional[:len(call.args)])  # positional → the read's own names
    # ⛔ THE `named_levers` BACKSTOP RUNS *FIRST* (#14281 review F4, measured). It used to
    # sit BELOW the equality, where it could never fire: the equality pins `allowed` to a
    # four-name literal, so `allowed & bool_levers` is non-empty only for a class the
    # equality already reds — measured, adding `include_terminal=False` to the door reds at
    # the equality and NEVER here, and a `_rerank_candidates` door change was reported by
    # the equality's "re-derive" text instead of this one's "the door already names a
    # lever" diagnosis. Ordered this way, each assertion reports its own class.
    named_levers = allowed & bool_levers
    assert not named_levers, (
        f"the door itself already names {sorted(named_levers)} — the mirror cannot be "
        "distinguished from a lever-carrying reader; re-derive this contract")
    # ⛔ THE DERIVATION IS ITSELF PINNED TO A LITERAL (#14281 review P2, measured). The
    # checks below are DERIVED, so they cannot see the door MOVING: `named_levers` is an
    # intersection with `bool_levers`, which holds only bool-defaulted parameters, so of
    # the six levers this file's docstring enumerates (`kind`, `_elevated_timeout_ms`,
    # `pool_size`, `order_by`, `claim_class`, `exclude_status`) exactly ZERO are visible
    # to it. MEASURED on the pre-fix form: `order_by="relevance"` added to
    # `hosted_api.py::search`'s read call AND to the harness's measured read left the pin
    # GREEN — the door widened the allow-list, which is the one direction that matters,
    # because the harness is then free to name the new lever and keep passing.
    #
    # This is the fail-closed backstop for every lever the intersection cannot see: a door
    # change now reds REGARDLESS of the parameter's type or default, and the red says to
    # re-derive rather than silently widening. The accepted cost is a FALSE RED on a door
    # change that adds a kwarg without meaning to — cheap and loud, against a silent
    # widening that no diff class would ever surface.
    assert allowed == _EXPECTED_DOOR_ARGS, (
        f"`hosted_api.py::search` now hands the read {sorted(allowed)}, not "
        f"{sorted(_EXPECTED_DOOR_ARGS)} — re-derive this contract and the harness's "
        "allowed arguments together, deliberately: an added keyword here WIDENS what the "
        "mirror may name, so a silent door change would let a lever in without a red")
    # `leg_trace` is an out-sink (the harness reads the legs it measured from it); it is
    # NOT a lever — nothing about which rows survive `limit` depends on it.
    allowed.add("leg_trace")
    return allowed


def _enclosing(node: ast.AST, parents: dict[int, ast.AST]) -> ast.AST | None:
    return parents.get(id(node))


def _owner(node: ast.AST, parents: dict[int, ast.AST]) -> str:
    parent = _enclosing(node, parents)
    while parent is not None:
        if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return parent.name
        parent = _enclosing(parent, parents)
    return "<module>"


def _in_loop(node: ast.AST, parents: dict[int, ast.AST]) -> bool:
    parent = _enclosing(node, parents)
    while parent is not None:
        if isinstance(parent, (ast.For, ast.AsyncFor)):
            return True
        if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return False
        parent = _enclosing(parent, parents)
    return False


def _names_in(node: ast.AST) -> set[str]:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def _artifact_entries(run_def: ast.AST) -> tuple[dict[str, ast.AST], ast.Call]:
    """The `"key": value` pairs of the ONE per-query artifact dict `run()` appends.

    ⛔ `EXACTLY ONE`: a first-match lookup validated a decoy — a `_debug_rows.append({
    "order": [], "legs": []})` placed before the real `per_query.append` satisfied every
    later assertion while the artifact itself held an empty page (measured).
    """
    found: list[tuple[dict, ast.Call]] = []
    for node in ast.walk(run_def):
        if (isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "append"
                and node.args and isinstance(node.args[0], ast.Dict)):
            pairs = {k.value: v for k, v in zip(
                node.args[0].keys, node.args[0].values, strict=True)
                if isinstance(k, ast.Constant) and isinstance(k.value, str)}
            if "legs" in pairs and "order" in pairs:
                found.append((pairs, node))
    assert len(found) == 1, (
        f"run() appends {len(found)} artifact dicts carrying BOTH `legs` and `order` — this "
        "pin cannot tell which one is the artifact")
    return found[0]


#: The tool's own id mapping — the ONE wrapper the `order` element check blesses.
_ID_MAPPERS = {"_result_id"}


def _extra_bindings(tree: ast.AST, names: set[str]) -> list[str]:
    """Binding sites for `names` other than the ONE module-level `def` of each.

    ⛔ THE ELEMENT CHECK BLESSES A NAME, AND A NAME CAN BE RE-BOUND (#14281 review P1,
    measured GREEN with every `order` a list of constants and `compare()` reporting
    `rank_identical: True` over a permuted page). One line inside `run()`:

        _result_id = lambda _r: "X"

    shadowed the mapper the check blesses — a rename-preserving refactor
    (`_result_id = _id_of`) reaches the same place by accident. The tool defines it once,
    at module level, so any second BIND is a finding — a nested def, an assignment, a
    duplicate top-level def, a parameter of that name.
    """
    out: list[str] = []

    def _flag(name: str, node: ast.AST) -> None:
        out.append(f"{name}@{getattr(node, 'lineno', '?')}")

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
            # ⛔ A SECOND TOP-LEVEL `def` IS A SECOND SITE TOO (#14281 review P3, measured):
            # the first form skipped every top-level def, so a duplicate
            # `def _result_id(r): return "X"` at the bottom of the module passed this guard.
            _seen = [n for n in getattr(tree, "body", [])
                     if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                     and n.name == node.name]
            if node not in getattr(tree, "body", []) or _seen[0] is not node:
                _flag(node.name, node)
            continue
        if isinstance(node, ast.ClassDef) and node.name in names:
            _flag(node.name, node)
            continue
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.NamedExpr,
                             ast.Delete)):
            targets = list(getattr(node, "targets", None)
                           or [getattr(node, "target", None)])
        elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
            targets = [getattr(node, "target", None)]
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            targets = [i.optional_vars for i in node.items if i.optional_vars is not None]
        elif isinstance(node, ast.arguments):
            targets = [ast.Name(id=a.arg) for a in
                       list(node.posonlyargs) + list(node.args) + list(node.kwonlyargs)
                       + [node.vararg, node.kwarg] if a is not None]
        else:
            continue
        for target in targets:
            if target is None:
                continue
            for inner in ast.walk(target):
                if isinstance(inner, ast.Name) and inner.id in names:
                    _flag(inner.id, node)
    return out

_NON_MUTATING = {"list", "tuple", "len", "sorted", "id", "str", "bool", "any", "all",
                 "isinstance"}

#: Callees that only READ their argument, so a name handed to one is not handed to a
#: mutation. `print(f"nhits={len(results)}")` is the natural next edit to `run()`'s
#: existing per-query print line, and it mutates nothing.
_PURE_READS = _NON_MUTATING | {"repr", "int", "float", "type", "min", "max", "sum",
                               "format", "round", "abs", "set", "dict", "frozenset",
                               # Diagnostics: `print(results)` is the natural next edit to
                               # `run()`'s existing per-query print line and mutates nothing.
                               "print",
                               # ⛔ A SERIALISER IS A PURE READ (#14281 review F5, measured).
                               # `payload = json.dumps(results)` and
                               # `pprint.pformat(results)` were reported as "line(s) [N]
                               # mutate a collection AFTER the measured read — an in-place
                               # sort/clear reaches the artifact". None of them mutates its
                               # argument: they READ it and build a new string/copy. Each was
                               # an unstated false red in a file whose stated contract is to
                               # enumerate its false reds.
                               "dumps", "dump", "pformat", "copy", "deepcopy"}


def _names_handed(node: ast.AST) -> set[str]:
    """Names in `node` at a position that could HAND the collection to a mutation.

    ⛔ DOES NOT DESCEND INTO A PURE READ (#14281 review P2, measured). The plain
    `_names_in(arg)` walk red-flagged `print(f"nhits={len(results)}")` — 1 failed,
    10 passed at 66880dace8 — because `results` appears inside a `len(...)`. Nothing is
    handed and nothing is mutated there, and the failure message misdiagnosed it as an
    in-place sort. Stopping at a known-pure callee keeps the shapes this branch exists
    for: `random.shuffle(results)` and `_helper(results + other)` still red (the name is
    not under a pure call), while `len(results)` / `str(results)` do not.
    """
    if isinstance(node, ast.Name):
        return {node.id}
    if isinstance(node, ast.Call):
        callee = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        if callee in _PURE_READS:
            return set()
    out: set[str] = set()
    for child in ast.iter_child_nodes(node):
        out |= _names_handed(child)
    return out


def _collection_operands(value: ast.AST) -> list[ast.AST]:
    """The sub-expressions that may BE the collection in a fallback expression.

    ⛔ `a1 = results or []` and `a1 = results if flag else []` bind the SAME collection
    when the left arm wins, and BOTH read GREEN on the name-only bind (#14281 review P1,
    measured: `aliases=[]`, then `a1.sort()` permuted the recorded page). Over-collecting
    here can only RED, which is the cheap direction; a `Subscript` (`results[0]`) is an
    ELEMENT and is deliberately not included.

    Returns the operand NODES, not their name-sets: the caller tests them with the same
    predicate it uses for a direct bind, so a BoolOp whose left arm IS the measured page
    resolves exactly like `a1 = results` does.
    """
    if isinstance(value, ast.BoolOp):
        return list(value.values)
    if isinstance(value, ast.IfExp):
        return [value.body, value.orelse]
    return []


#: Callees that return a NEW CONTAINER built FROM their argument — a SHALLOW copy:
#: `list(results)` / `sorted(results)` / `dict(row)` / `results.copy()` all build a new
#: container whose SLOTS hold the page's OWN element objects.
#:
#: ⛔ NOT `_PURE_READS` (#14326 review P0, MEASURED at `36a6451d4e`): the copy exemption was
#: `callee in _PURE_READS`, which also carries `min`/`max` — and those return an ELEMENT of
#: the page, not a new container, so `_r = min(results, key=str); _r["id"] = "X"` was
#: exempted as a "copy" and measured GREEN. `str`/`repr`/`len`/`sum`/`type`/`bool`/… build
#: no container at all, so exempting them bought nothing and widened the hole. The set is
#: the container BUILDERS and nothing else.
_SHALLOW_COPY_CALLEES = frozenset({"list", "tuple", "sorted", "set", "frozenset",
                                   "dict", "copy", "deepcopy"})


#: The allow-listed pure reads that build NO CONTAINER AT ALL — a string, a number, a
#: bool, a class, or `None`.
#:
#: ⛔ STILL EXEMPT (#14326 review P0): `payload = json.dumps(results)` / `_s = str(row)`
#: cannot be written through to the recorded page, and the round-9 blanket exemption kept
#: them green. NARROWING the copy set must not re-red them, so they are their own class
#: rather than left to fall through to the alias arm.
#: ⛔ AND `min`/`max` ARE DELIBERATELY **NOT** IN IT: they return an ELEMENT of the page,
#: which is exactly why the blanket `callee in _PURE_READS` exemption was the P0.
_SCALAR_CALLEES = frozenset(_PURE_READS - _SHALLOW_COPY_CALLEES - {"min", "max"})


def _is_scalar_producer(value: ast.AST) -> bool:
    """The value is a pure read that returns a scalar/text, never the page or its rows."""
    return (isinstance(value, ast.Call)
            and (getattr(value.func, "id", None) or getattr(value.func, "attr", None))
            in _SCALAR_CALLEES)


def _builds_new_container(value: ast.AST, rebound: frozenset[str] = frozenset()) -> bool:
    """The value is a COPY — a call to a container BUILDER, or a comprehension.

    ⛔ A COPY CANNOT MUTATE THE COLLECTION IT COPIED (#14326 round 9, MEASURED at
    `88859180`): `_copy = list(results); _copy.sort()` measured `hits=[5]` and
    `_ids = [_result_id(r) for r in results]; _ids.sort()` measured `hits=[5]` — the pin
    RED-ed two edits that mutate nothing the artifact records, while this file's own
    docstring declares exactly those shapes "correctly exempt". The mention rule below
    counts any page name inside the value, and both spellings CONTAIN one, so the copy
    became an alias of its source. A LITERAL container is deliberately NOT exempt —
    `_box = [results]` really does hold the page, and its own mutations stay the stated
    accepted false red.

    ⛔ AND THE COPY'S **ELEMENTS** ARE THE PAGE'S OWN OBJECTS (#14326 review P0, MEASURED
    at `36a6451d4e` and reproduced end-to-end on the shipped tool): every shape above is a
    SHALLOW copy — `_c = list(results); _c[0]["id"] = "X"` permuted the recorded page
    while this predicate exempted `_c` from BOTH arms, and the pin reported PASS. So this
    predicate answers only "is the CONTAINER new"; the caller collects the copy names
    separately (`_mutated_after`'s `shallow_copies`) so a write THROUGH a slot still reds.

    ⛔ AND THIS PURITY TEST IS BY NAME, SO THE NAME CAN BE REBOUND (#14326 round 12, MEASURED
    at `c380075b4`): `list = lambda _x: results; _copy = list(1); _copy.sort()` measured GREEN
    — a real in-place permutation of the page — because the test keyed on the callee NAME
    alone. `_is_read_ref` and `_indirect_reads` both consult `_rebound_callee_names` (rounds
    6-7 exist for exactly this); this exemption did not, so it was the one route by which a
    shadowed reader stayed "pure". `rebound` is passed in because this predicate has no
    `tree`; over-approximating purity here is the fail-open direction. RESIDUAL: the caller
    passes `_rebound_callee_names(tree)` without `consts`, so a rebind laundered through a
    STRING KEY (`setattr(builtins, "list", …)`) is not seen here — the name-form rebind is
    what this arm closes, and widening it needs `consts` threaded through `_aliases_of`.

    ⛔ ONLY THE TWO CALL SITES WHOSE PREDICATE CAN RESOLVE A COPY CALLEE PASS `rebound`
    (`aliases`, `rows`), and each of those threads is pinned by a mutant that reds a test. The
    other three (`shallow_copies`, `bound_mutator`, `page_container`) carried the argument for
    review rounds without any row able to red its removal — measured: deleting all three left
    **31 passed** (#14326 review at `a29f3cd8`, P2). They no longer pass it, because a thread
    that cannot change a verdict is the same defect this file deletes unpinnable clauses for.
    `shallow_copies` is the provable case: `copy_is_alias=True` short-circuits this predicate
    in `_aliases_of`, so there is no exemption left on that path for a rebound callee to
    disarm.
    """
    if isinstance(value, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
        return True
    if isinstance(value, ast.Call):
        # ⛔ `rebound` IS A SET OF NAMES, SO IT CAN ONLY SHADOW A NAME (#14326 review at
        # `a29f3cd8`, MEASURED false red at `80d2ab52`): `results.copy()` is reached through
        # an ATTRIBUTE, and no bind of the bare name `copy` changes what `results.copy`
        # resolves to — yet consulting `rebound` unconditionally dropped `copy` from
        # `_SHALLOW_COPY_CALLEES` and red the INERT `_c = results.copy(); _c.sort(key=str)`.
        # The thread is consulted only for the one spelling a name bind can launder.
        is_name = isinstance(value.func, ast.Name)
        callee = getattr(value.func, "id", None) or getattr(value.func, "attr", None)
        return callee in _SHALLOW_COPY_CALLEES and not (is_name and callee in rebound)
    # ⛔ A DISPLAY THAT ONLY SPREADS THE PAGE COPIES IT (#14326 round 12, MEASURED on the REAL
    # tool: `_copy = [*results]; _copy.sort()` was `1 failed` while `list(results)` passed).
    # `[*results]` builds a NEW list over the same element objects, so sorting it cannot permute
    # the page — while `[results]` HOLDS the list itself (`_box[0].sort()` is correctly RED and
    # stays so). The property is OBJECT-based, not shape-based. `results * n` and
    # `copy + []` are the same: a list `+`/`*` always allocates, so neither can alias its
    # operand. The shallow-copy contract above is unchanged — the caller still collects these
    # names so a write THROUGH a slot reds.
    if isinstance(value, (ast.List, ast.Tuple)):
        return bool(value.elts) and all(isinstance(_e, ast.Starred) for _e in value.elts)
    return isinstance(value, ast.BinOp) and isinstance(value.op, (ast.Add, ast.Mult))


def _loop_target_sources(iterable: ast.AST) -> list[ast.AST]:
    """What a `for`/`AsyncFor` TARGET binds — one entry per possible value.

    ⛔ A LOOP TARGET TAKES AN ELEMENT, NOT THE CONTAINER (#14326 round 17, MEASURED).
    Binding `target ↔ node.iter` is right for a NAME iterable (the target may be any
    element, so treating it as the collection itself over-approximates) but WRONG for a
    literal one: `for _x in (sys.modules,):` bound `_x` to the TUPLE, so the predicate
    that recognises the import registry never fired and the alias axis lost the name —
    measured GREEN at `689708686ba6` for

        for _x in (sys.modules,): _x |= {"tortoise.sdk": _Mirror}   # GREEN
        for _x in [sys.modules]:  _x |= {"tortoise.sdk": _Mirror}   # GREEN
        for _x in (_t.__dict__,): _x |= {"TortoiseSDK": _Mirror}    # GREEN

    while the direct store and the pre-bound alias (`_R = sys.modules; for _x in _R:`)
    were RED. CPython's grammar gives `iter` an arbitrary `expr`, so a literal sequence
    is statically expanded — the SAME rule `_str_constants`/`_values` already applies to
    a `for` target over a literal (there it unions the elements' VALUES, which is why the
    string axis was never affected). Iterating a dict yields its KEYS, `None` being a
    `**spread` and no key at all; anything else binds the iterable itself.

    ⛔ A `*`/`**` INSIDE THE LITERAL IS THE SAME ELEMENT ONE SPLICE OVER (#14326 round 18,
    MEASURED at `7ba358467252`: `(*(sys.modules,),)` and `{**{sys.modules: 1}}` both left
    the pin GREEN while `(sys.modules,)` was RED). A `Starred` element is not a value the
    target can take — the splice is — so a starred LITERAL is expanded and a starred
    ANYTHING-ELSE binds that expression (the `for _x in _R:` over-approximation already
    used for a NAME iterable). The same holds for a `**spread`: `{**d}` yields `d`'s keys,
    so a spread of a dict LITERAL is expanded and a spread of anything else binds it.
    ⛔ AND THE RESIDUAL IS NAMED, NOT IMPLIED: an iterable whose elements this does not
    expand — a `IfExp`/`BoolOp`/`BinOp` over literals, a comprehension or generator, a
    subscript, or a NESTED literal (only a `Starred` element is spliced: `((sys.modules,),)`
    binds the INNER TUPLE, not `sys.modules`, so `for _x in ((sys.modules,),)` is GREEN while
    `for _x in (sys.modules,)` is RED — MEASURED at `57735ab47947`) — binds the container
    expression,
    ⛔ AND IT IS UNNUMBERABLE, **NOT HARMLESS** (#14326 round 30 P2): this paragraph used to
    say "`|=` on a tuple raises `TypeError`, so that shape cannot be a working mirror lever".
    That was MEASURED FALSE and is retracted. The shape needs no `|=` at all —
    `for _x in ((sys.modules,),): _x[0][...] = _Mirror` is a plain subscript store that raises
    nothing — and the `|=` spelling mutates the INNER dict before the `TypeError` raises, so a
    suppressed error keeps it. `_x[0] |= {…}` and `_x[0][...] = _Mirror` both resolved a
    planted class end-to-end with the pin GREEN. The residual is real and stays declared; what
    was wrong was justifying it by a mechanism that does not hold — the third instance of this
    lane's one recurring defect, and a residual is not exempt from being measured.
    so every SHAPE predicate
    (`_is_module_registry`, `_is_namespace_expr`, `_is_setattr_like`) is blind to it. It is
    deliberately NOT enumerated here (the power-set blow-up the `_str_constants` note
    measures). It is pinned GREEN, and named, by
    `test_the_ALIAS_axis_iterable_residuals_are_NAMED_and_pinned_GREEN` — not claimed as
    coverage by this docstring.
    """
    if isinstance(iterable, (ast.Tuple, ast.List, ast.Set)):
        expanded: list[ast.AST] = []
        for element in iterable.elts:
            if not isinstance(element, ast.Starred):
                expanded.append(element)
            elif isinstance(element.value, (ast.Tuple, ast.List, ast.Set, ast.Dict)):
                expanded.extend(_loop_target_sources(element.value))
            else:
                expanded.append(element.value)
        return expanded
    if isinstance(iterable, ast.Dict):
        keys: list[ast.AST] = []
        # `ast.Dict` guarantees `len(keys) == len(values)`, so `strict=True` states the
        # invariant rather than tolerating a silently shorter `values`
        for key, value in zip(iterable.keys, iterable.values, strict=True):
            if key is not None:
                keys.append(key)
            elif isinstance(value, ast.Dict):
                keys.extend(_loop_target_sources(value))
            else:
                keys.append(value)
        return keys
    return [iterable]


def _aliases_of(tree: ast.AST, predicate, loops: bool = False,
                copy_is_alias: bool = False,
                rebound: frozenset[str] = frozenset()) -> set[str]:
    """Names bound to a matching expression, TRANSITIVELY — `a = results`, `b = a`,
    `(c := results)`, `env = os.environ`, `env2 = env`.

    `copy_is_alias=True` skips the SHALLOW-COPY exemption below, so a name bound to
    `list(results)` / `sorted(results)` / a comprehension is COLLECTED rather than
    excluded. That is how `_mutated_after` finds the copies themselves: a copy is not the
    page, but its slots hold the page's own elements.

    ⛔ TRANSITIVE AND WALRUS-AWARE (#14281 review P2, measured). A one-hop `=` walk was
    GREEN on BOTH of these against a genuinely permuted page:

        a1 = results; a2 = a1; a2.sort()      -> 7 passed, rank_identical: True
        (r2 := results); r2.sort()            -> 7 passed, rank_identical: True

    The one-hop walk widened `watched` with `a1` but never with `a2`, so `a2.sort()`
    missed the receiver **and** the "handed to a call" branch; the walrus target was
    never an alias at all. Both names are STATICALLY VISIBLE, so neither is the pin's
    declared "computed at runtime" residual — this is the same false-PASS class the F2
    fix closed, one hop further out.

    ⛔ UNPACK TARGETS TOO (`(page,) = (results,)`), which a `len(targets) == 1 and
    isinstance(target, ast.Name)` bind never sees — measured GREEN on the form above
    while `page.sort()` permuted the recorded page.

    ⛔ AND A FALLBACK EXPRESSION (`a1 = results or []`, `a1 = results if c else []`):
    the name-only test read GREEN on both (`aliases=[]`) while `a1.sort()` permuted the
    recorded page, because the collection was never resolved as the alias at all.
    `_collection_operands` closes it, and over-collecting there can only RED.

    ⛔ AND SO IS A `for` TARGET (`loops=True`) — a bind with no `Assign` node (#14281
    review F2/F3, both measured GREEN):

        for _e in (os.environ,): _p = _e.pop; _p("TORTOISE_FUSED_RERANK")   -> []
        for _pq in per_query: _s = _pq["order"]; _s.sort()                  -> []; []

    Both are the same defect — the resolver had no arm for the one bind spelling that
    carries no assignment — so the target names join the fixpoint exactly as an `Assign`
    target does. ⛔ THE FIRST OF THE TWO IS A CONTAINING LITERAL, AND BINDING THE TARGET
    TO THE ITERABLE DID NOT CLOSE IT (#14326 round 17, MEASURED GREEN at `689708686ba6`:
    `_x |= {"tortoise.sdk": _Mirror}` inside `for _x in (sys.modules,)`, while the same
    store one spelling over red); the shape only works for the ENV axis because
    `_environ_mutations` passes the wider `_mentions_mapping` predicate, which matches a
    container that HOLDS the mapping. The registry axis passes a shape predicate, so the
    target has to reach the ELEMENT — `_loop_target_sources` below expands a literal
    iterable, which is CPython's own semantics (the target takes each element in turn).
    `comprehension` is deliberately NOT included: the artifact's own
    `[_result_id(r) for r in results]` hands each ELEMENT to a call, and treating a
    comprehension target as an alias of the collection would red the tool the pin exists
    to protect (measured before adding the arm).

    ⛔ THREE BIND FORMS `_str_constants` ALREADY RESOLVED WERE MISSING ON THIS AXIS (#14326
    round 19, MEASURED GREEN at `9b48318eedc3` against the pin's own predicate): a `match`
    CAPTURE, a parameter DEFAULT, and a `with` TARGET. Each binds a name to a statically
    visible value with no `Assign` node, so the `AugAssign` store arm was fed a receiver that
    never resolved and

        match sys.modules:
            case _x: _x |= {"tortoise.sdk": _Mirror}      # GREEN end-to-end
        def _f(_registry=sys.modules):
            _registry |= {"tortoise.sdk": _Mirror}       # GREEN end-to-end

    reached the deferred import while the pin passed — the same store as the RED
    `sys.modules |= …` row, one bind form over. The subject/default/context expression is the
    source in every case, which is the over-approximation this axis already makes for a NAME
    iterable; a `match` pattern LITERAL still binds nothing (`_str_constants`' own rule).
    """
    binds: list[tuple[ast.AST, ast.AST]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets: list[ast.AST] = list(node.targets)
            value: ast.AST = node.value
        elif isinstance(node, (ast.AnnAssign, ast.NamedExpr)) and node.value is not None:
            targets, value = [node.target], node.value
        elif loops and isinstance(node, (ast.For, ast.AsyncFor)):
            for source in _loop_target_sources(node.iter):
                binds.append((node.target, source))
            continue
        # ⛔ A PARAMETER DEFAULT IS A STATIC BIND ON THIS AXIS TOO (#14326 round 19).
        # `_str_constants` has resolved this form since #14326 review P6; this arm — the one
        # that feeds the `AugAssign` store — did not, so a registry store through a default
        # was GREEN end-to-end.
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            positional = [*node.args.posonlyargs, *node.args.args]
            first = len(positional) - len(node.args.defaults)
            for index, default in enumerate(node.args.defaults):
                binds.append((ast.Name(id=positional[first + index].arg,
                                       ctx=ast.Store()), default))
            for index, kdefault in enumerate(node.args.kw_defaults):
                if kdefault is not None:
                    binds.append((ast.Name(id=node.args.kwonlyargs[index].arg,
                                           ctx=ast.Store()), kdefault))
            continue
        # ⛔ …AND SO IS A `match` CAPTURE (#14326 round 19). Same subject-only rule as
        # `_str_constants`: a capture binds a PART of the subject, so the SUBJECT is the
        # source and a pattern LITERAL never binds a name.
        elif isinstance(node, ast.Match):
            for case in node.cases:
                for capture in ast.walk(case.pattern):
                    name = getattr(capture, "name", None) or getattr(capture, "rest", None)
                    if name is None:
                        continue
                    binds.append((ast.Name(id=name, ctx=ast.Store()), node.subject))
            continue
        # ⛔ …AND A `with` TARGET (#14326 round 19): `with sys.modules as _x:` binds `_x`
        # with no `Assign` node, the third spelling of the same bind. The target binds what
        # `__enter__` RETURNS, which is not statically knowable in general, so binding it to
        # the CONTEXT EXPRESSION is the over-approximation — and it is the only one that
        # closes the shape, because a call there (`with _cm(sys.modules) as _x:`) binds an
        # unknown value and stays GREEN, correctly.
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                if item.optional_vars is not None:
                    binds.append((item.optional_vars, item.context_expr))
            continue
        # ⛔ AN IMPORT ALIAS OF THE REGISTRY IS THE REGISTRY (#14326 round 20, MEASURED GREEN:
        # `from sys import modules as _m; _m["tortoise.sdk"] = _Mirror` left the pin green
        # while `import sys as _s; _s.modules[…]` was RED). `_aliases_of` walks assignment
        # nodes only, so an `ImportFrom` bound nothing. The synthesized `sys.modules`
        # Attribute is exactly what the runtime binds and exactly what the predicate reads;
        # restricted to the MODULES AND MEMBERS an axis in this file grades, so no other
        # import can be mistaken for the object a predicate is looking for.
        #
        # ⛔ AND `sys`/`modules` WAS THE WHOLE LIST UNTIL THIS LANE (#14326 fix lane at
        # `a12902a5`, each MEASURED GREEN through the pin's own predicate at that head,
        # `mentions=2 calls=2 indirect=0` while a planted `tortoise/sdk.py` resolved
        # end-to-end):
        #
        #     from sys import path as _p; _p.insert(0, "/tmp/evil"); del sys.modules[…]  GREEN
        #     from builtins import exec as _run; _run("<read call>")                     GREEN
        #
        # Each is the SAME static alias of the SAME object the file already refuses one
        # spelling over — `_p = sys.path` + `_p.insert` and `_run = exec` are both RED — so
        # an arm that enumerates ONE module and TWO members is an in-file asymmetry, not a
        # declared residual (the file's own rule: a statically visible alias is not the
        # "computed at runtime" residual). The member sets below ARE the sets their axes
        # own (`_PATH_ATTRS`, `_CODE_EXECUTING_CALLEES`, `_REGISTRY_BUILTINS` — plus
        # `__dict__`, the mapping root both axes read), so this is not a second copy of
        # any list, and a member no axis grades (`from sys import argv as _a`) still binds
        # nothing that can widen a predicate.
        elif isinstance(node, ast.ImportFrom) and node.module in {"sys", "builtins"}:
            _imported_members = (frozenset({"modules", "__dict__"}) | _PATH_ATTRS
                                 if node.module == "sys"
                                 else _CODE_EXECUTING_CALLEES | _REGISTRY_BUILTINS
                                 | frozenset({"__dict__"}))
            for alias in node.names:
                # ⛔ `__dict__` IS BOUND THE SAME WAY (#14326 round 28 P2, MEASURED GREEN:
                # `from sys import __dict__ as _d; _d["modules"] = {…}` and
                # `_d |= {"modules": …}` both replaced the registry while the ASSIGNMENT bind
                # `_d = sys.__dict__` was RED). `from sys import __dict__` binds the LIVE dict
                # (`_d is sys.__dict__`), so it is the same one-clause rule the `modules`
                # import already carried, not a new axis.
                if alias.name not in _imported_members:
                    continue
                binds.append((
                    ast.Name(id=alias.asname or alias.name, ctx=ast.Store()),
                    ast.Attribute(value=ast.Name(id=node.module, ctx=ast.Load()),
                                  attr=alias.name, ctx=ast.Load())))
            continue
        else:
            continue
        for target in targets:
            binds.append((target, value))

    out: set[str] = set()

    def _bind(target: ast.AST, value: ast.AST) -> bool:
        """Resolve one (target, value) pair; True when it named something new."""
        if isinstance(target, ast.Name):
            if target.id in out:
                return False
            # ⛔ A MENTION, not only a bare `Name`: `_box = [results]` and
            # `_row = per_query[-1]` are aliases of the collection inside them, and the
            # bare-Name form left `_box[0].sort()` / `_row["order"] = …` green (measured).
            # …but a COPY (`list(results)`, a comprehension) is NOT an alias — see
            # `_builds_new_container` above. `copy_is_alias=True` collects the copies. A
            # SCALAR read (`json.dumps(results)`, `str(row)`) is not an alias EITHER
            # (`_is_scalar_producer`), and that gate stays on in both modes.
            resolves = (not _is_scalar_producer(value)
                        and (copy_is_alias
                             or not _builds_new_container(value, rebound)) and (
                            predicate(value)
                            or bool(_names_in(value) & out)
                            or any(predicate(operand)
                                   or (isinstance(operand, ast.Name) and operand.id in out)
                                   or bool(_names_in(operand) & out)
                                   for operand in _collection_operands(value))
                        ))
            if resolves:
                out.add(target.id)
                return True
            return False
        if isinstance(target, (ast.Tuple, ast.List)) \
                and isinstance(value, (ast.Tuple, ast.List)) \
                and len(target.elts) == len(value.elts):
            grew = False
            # Indexed, not `zip(strict=True)`: the length guard above makes the strict
            # check unreachable, and it was dead weight in this sibling walk too
            # (#14279 review P3).
            for index in range(len(target.elts)):
                grew = _bind(target.elts[index], value.elts[index]) or grew
            return grew
        return False

    changed = True
    while changed:  # fixed point: a chain of any length resolves in one call
        changed = False
        for target, value in binds:
            if _bind(target, value):
                changed = True
    return out


#: The mutator names a reader is LIKELY to write. ⛔ NOT THE GATE — see
#: `_is_mutator_method` below, which is fail-CLOSED: this set is documentation and the
#: anchor the recorded-rows test uses to find "the mutating line", and a name it omits
#: (``__init__``, ``__delitem__``, ``__iadd__``, ``__ior__`` …) no longer reads GREEN.
_MUTATORS = {"append", "clear", "extend", "insert", "pop", "remove", "reverse", "sort",
             "update", "__setitem__", "popitem", "setdefault"}

#: Methods whose WHOLE contract is to observe the receiver — the READ side of the
#: collection protocol. Everything else is a mutation.
#:
#: ⛔ THE DEFAULT IS INVERTED, AND THAT IS THE FIX. The mutator test used to be
#: ``name in _MUTATORS`` — a closed ALLOW-list — so every mutator it did not name was
#: GREEN, and Python's data model carries more of them than any list a reader writes.
#: FOUR measured GREEN at `3e6e5e61de1b` through the full pin (each permutes or rewrites
#: the recorded page while ``compare()`` still reports ``rank_identical: True``, because
#: the mutation is in the HARNESS and erases the difference the verdict exists to see):
#:
#:     results.__init__(sorted(results, key=_result_id, reverse=True))   replaced in place
#:     results.__delitem__(slice(None))                                  emptied
#:     results.__iadd__([{"id": "REDACTED"}])                           appended
#:     results[0].__init__({"id": "REDACTED"})                          row replaced
#:
#: ``list.__init__`` re-initialises the SAME list object, so the artifact records the
#: canonicalised page — the exact false PASS this pin exists to prevent, reached without
#: a single binding-form indirection. That is why this is a NAME-LIST polarity fix and
#: not a fifteenth arm: an unknown method name is now a mutation, and a false red costs
#: an edit here while a false green costs the verdict.
_PURE_METHODS = frozenset({
    # container observation
    "count", "index", "copy", "get", "keys", "values", "items", "__len__",
    "__getitem__", "__iter__", "__contains__",
    # identity / display / comparison
    "__eq__", "__ne__", "__hash__", "__str__", "__repr__", "__format__",
    # str reads (a tainted for-target is not always a collection). ⛔ The common ones were
    # MISSING and red as "an in-place sort/clear" (#14281 review P3, measured:
    # `print(results[0]["query"].strip())`, `_lbl = results[0]["query"].lower()`).
    "startswith", "endswith", "find", "rfind", "isdigit", "isalpha",
    "strip", "lstrip", "rstrip", "lower", "upper", "casefold", "split", "rsplit",
    "replace", "format", "join", "title", "capitalize", "zfill", "center",
})

#: The callees a tainted ELEMENT may be handed to without that being a mutation of the
#: collection: a pure read, a pure METHOD, or the blessed id mapper. Anything else is
#: fail-closed, because a helper is free to write the element it was given (#14326 review
#: P2, measured: an element write through a helper left the recorded page permuted with
#: `rank_identical: True`, in every wrapper spelling).
_PURE_CALLEES = _PURE_READS | _PURE_METHODS | _ID_MAPPERS


def _is_mutator_method(name: str | None) -> bool:
    """True when ``<receiver>.<name>(…)`` may MUTATE the receiver.

    Fail-closed on purpose: an unknown name is a mutation, because the alternative is a
    closed allow-list of mutators that any unlisted dunder walks around (see
    ``_PURE_METHODS``). ``None`` (a call whose callee is not an attribute at all) is not a
    method call, so it is not a mutation.
    """
    if name is None:
        return False
    return name not in _PURE_METHODS


def _constant_key(node: ast.AST) -> str | None:
    """The LITERAL key of a `Subscript` (`_sl["s"]` -> `"'s'"`), or `None` when computed.

    Used to compare a slot STORE with the slot a callee is CALLED through, so
    `_sl["s"] = results.sort; _sl["label"]()` is not a mutation (#14326 review P2,
    MEASURED at `36a6451d4e`: `hits=[6]` while the shape calls nothing that touches the
    page). The spelling must be identical on both sides, which `repr()` gives for every
    hashable constant (a string, an int, a tuple of them) without assuming a type.
    """
    if isinstance(node, ast.Constant):
        return repr(node.value)
    return None


def _collection_alias_predicate(var: str):
    """The ONE predicate for "this value IS the collection `var`" — used by the guard
    and by the pin that checks the guard, so the two cannot drift (#14281 review F6).

    ⛔ AN ELEMENT IS NOT THE COLLECTION (#14281 review F5/F6, measured). The predicate
    used to be `bool(_names_in(value) & {var})` — a MENTION test — so `first = results[0]`
    resolved `first` as an alias of the page. Two consequences, both measured:

      `first = results[0]; print(first)`  -> RED, "in-place sort/clear reaches the artifact"
                                             (a false red — `print` mutates nothing)
      the pin's `assert "first" not in _aliases_of(run_def, lambda v: … v.id == "results")`
      passed only because it ran a DIFFERENT lambda from the one production runs — no
      mutant of the guard could red it (rule 3).

    A bare `Subscript` value is that element, so `_aliases_of` does NOT resolve it, and
    `first = results[0]; first.sort()` is GREEN on the PAGE pass — the guard's own unit test
    asserts exactly that (`test_…: assert not _mutated_after(run_def, "results", …)`), because
    `first.sort()` re-orders the element's own inner list, which the artifact's
    `_result_id(r)` does not read. ⛔ IT IS STILL RED ON THE FULL PIN, through the TRACE pass:
    the page assignment's value mentions `trace` (`leg_trace=trace`), so `results` is an
    alias of the trace there and `first` inherits it. Two reviewers measured the two halves
    separately and reached opposite conclusions from this sentence — so both facts are
    stated here, and the file no longer argues with itself. The LITERAL
    `results[0].sort()` is RED through the receiver arm — a subscript expression is not a
    bind — and `_box[0]`, whose base is a LITERAL CONTAINER of the page, is not an element
    either.
    """
    def _predicate(value: ast.AST) -> bool:
        if isinstance(value, ast.Subscript):
            return False
        return bool(_names_in(value) & {var})

    return _predicate


def _scalar_bound_names(tree: ast.AST) -> set[str]:
    """Names whose EVERY binding is a scalar — an f-string, a literal, arithmetic on them.

    ⛔ AN ALIAS IS NOT A COLLECTION (#14281 review P1, measured false RED at line 340 of
    the real tool). `_aliases_of(…, loops=True)` resolves a `for`-bound element, so

        for pq in per_query:
            for leg in pq["legs"]:
                key = f"{leg['leg']}:{leg['reason'] or 'ok'}"
                declared[key] = declared.get(key, 0) + 1

    made `key` an alias of `leg` — it MENTIONS one — and handing that STRING to `.get()`
    was then reported as "mutates a collection AFTER the measured read". A `str` cannot be
    sorted back into the measured page, so a name bound only to scalars is not a
    collection.
    """

    def _scalar(node: ast.AST) -> bool:
        if isinstance(node, (ast.Constant, ast.JoinedStr, ast.FormattedValue)):
            return True
        if isinstance(node, (ast.BinOp, ast.BoolOp, ast.UnaryOp, ast.Compare)):
            return True
        if isinstance(node, ast.IfExp):
            return _scalar(node.body) and _scalar(node.orelse)
        if isinstance(node, ast.Name):
            return node.id in scalar_names  # a chain of scalar binds
        return False

    scalar_names: set[str] = set()
    seen: dict[str, list[bool]] = {}
    for _pass in range(3):  # tiny fixpoint: `a = f"{b}"; b = str(c)`
        for node in ast.walk(tree):
            pairs: list[tuple[ast.AST, ast.AST]] = []
            if isinstance(node, ast.Assign):
                pairs = [(t, node.value) for t in node.targets]
            elif isinstance(node, (ast.AnnAssign, ast.NamedExpr)) and node.value is not None:
                pairs = [(node.target, node.value)]
            for target, value in pairs:
                for name in _names_in(target):
                    seen.setdefault(name, []).append(_scalar(value))
        scalar_names = {n for n, flags in seen.items() if flags and all(flags)}
    return scalar_names


def _cannot_carry(node: ast.AST, scalar_names: set[str]) -> bool:
    """True when a stored value cannot CARRY the measured collection or one of its rows.

    Used by the container-slot arm: a slot store propagates taint to the container
    (aggregate/field propagation — the rule every production taint analyzer carries,
    because a container is reached through its slots, not through its name), and this
    predicate is what keeps that rule from tainting a container over a value that is a
    COPY of nothing: a number, a formatted string, an arithmetic result, or a known pure
    read (`len(results)`, `dict(os.environ)`).

    ⛔ NO `Call` EXEMPTION BEYOND `_PURE_READS`: `_tally["v"] = stats(results)` may hand
    the collection to a callee that keeps it, so the container is tainted — conservative
    in the same direction as the rest of this pin (a false red on a container that is
    later written but never mutated, against a GREEN permuted page).
    """
    if isinstance(node, (ast.Constant, ast.JoinedStr, ast.FormattedValue)):
        return True
    if isinstance(node, (ast.BinOp, ast.BoolOp, ast.UnaryOp, ast.Compare)):
        return True
    if isinstance(node, ast.IfExp):
        return (_cannot_carry(node.body, scalar_names)
                and _cannot_carry(node.orelse, scalar_names))
    if isinstance(node, ast.Name):
        return node.id in scalar_names
    if isinstance(node, ast.Call):
        callee = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        return callee in _PURE_READS
    return False


def _mutated_after(run_def: ast.AST, var: str, lineno: int,
                   artifact_append: ast.Call,
                   tree: ast.AST | None = None) -> list[int]:
    """Lines after the read that mutate `var` (or the recorded rows) IN PLACE.

    ⛔ In-place mutation is the other half of rebinding: `results.sort()` /
    `trace.clear()` / `per_query[-1]["order"].sort()` all leave a mutated collection
    feeding the artifact while every rebinding check passes (measured). ⛔ AND AN ALIAS IS
    THE SAME COLLECTION: `alias = results; alias.sort()` and `random.shuffle(results)` —
    a call handed the list, which may sort it in place — defeated a receiver-name-exact
    test (measured), so aliases are resolved and the collection may not be handed to a
    call that is not a known non-mutator.

    ⛔ THE "HANDED" TEST IS AN INTERSECTION, NEVER SET-EQUALITY (#14281 review F2).
    It used to compare each argument's name-set against the WHOLE `watched` set for
    exact equality, which made the branch INERT the moment one alias existed: with
    `page_alias = results`, `watched = {"results", "page_alias"}`, so the argument of
    `random.shuffle(results)` was `frozenset({"results"})` and the intersection was
    empty for every single-name argument, forever. The alias resolution above is what
    DISABLED the check it was added to serve — a guard that happened to work only
    because the tool carries no alias. Measured mutant that was GREEN before this fix
    and reds now: `page_alias = results` + `random.shuffle(results)`. The intersection
    also catches an argument that merely CONTAINS the collection — `_helper(results +
    other)` was GREEN under the equality form (its name-set is two names, not one) and
    hits now. ⛔ That is NOT an arity claim: the comparison was always per ARGUMENT, so
    `_helper(results[0])` was caught before too (measured) — the equality form failed on
    a name-set that was not exactly `watched`, never on the number of arguments.

    RESIDUAL, stated rather than implied: the walk is over `run()`'s OWN body. A helper
    defined OUTSIDE `run()` and called with NO argument (`_tidy()` closing over a module
    global) is not followed — passing the collection (`_tidy(results)`) IS caught, and so
    is a helper nested inside `run()` even when it is defined above the read (measured:
    the scan is not line-filtered, so `def _tidy(): results.sort()` above the read and
    `_tidy()` after it reds at `results.sort()`). Interprocedural reach is out of scope
    for an AST pin, and `tools/read_wave_latency.py` carries no such helper today.

    ⛔ THE PIN'S THREAT SURFACE IS DECLARED HERE, NOT DISCOVERED BY THE NEXT REVIEWER
    (agent-infra `proportional-gates` §"Adversarial domain" — the budget for a guard whose
    correctness is "an attacker cannot make it fail open" is bounded by its DECLARED
    threat surface, not by reviewer exhaustion; residuals are NAMED, not chased one
    spelling per round).

    **Covered (each with a test that reds when its arm is removed):** the collection
    itself, a transitive alias, a walrus bind, EVERY target of an unpack, a `for` target,
    a `lambda`/`def` default, an augmented assignment, a container or object SLOT store
    (`_c[k] = results[0]`, `self._page = results[0]`) — and the same set one level down,
    for the RECORDED ROWS — plus a bound mutator (`_s = results.sort`) reached directly, or
    through a LITERAL container, an OBJECT slot, a slot STORE (`_sl["s"] = results.sort`) or a
    second hop out of a container holding one (`_b.s = _d["s"]`).
    ⛔ AND A WRITE THROUGH A SHALLOW COPY (#14326 review P0, MEASURED at `36a6451d4e`):
    `_c = list(results); _c[0]["id"] = "X"` measured `hits=[]` while the recorded `order`
    carried `"X"` and the pin reported PASS — the copy exemption covered the whole bind, so
    a container whose slots ARE the page's own elements was exempt from the element arm too.
    `sorted(…)`, `[r for r in results]`, `min(…)`/`max(…)` (an ELEMENT, never a copy) and a
    second name for the copy, and a `for` over the copy, all measured the same way and are
    covered by one rule: the copy's NAME joins `holders` ("a container reached THROUGH its
    slots"), while the container-level operations (`_c.sort()`) stay exempt. The SLOT KEY is
    compared there too — a mutator stored in `_sl["s"]` does not make `_sl["label"]()` a
    mutation (#14326 review P2, measured).

    ⛔ STATED RESIDUAL, not implied coverage (#14326 round 9): a bound mutator reached through
    a DYNAMIC attribute (`getattr(_b, "s")(key=…)`, `_b.__dict__["s"]()`) is NOT resolved —
    the attribute name is computed at runtime, which is the same boundary the read-name census
    declares. Everything above is a statically visible attribute/slot spelling; this one is
    not, and a future edit that relies on it must be graded by hand.
    ⛔ A METHOD NAME IS NOT ENUMERATED, IT IS DEFAULTED (#14281 review round 14, four
    shapes measured GREEN at `3e6e5e61de1b`): the mutator test is `_is_mutator_method`,
    which is FAIL-CLOSED — `_PURE_METHODS` names the read side of the collection protocol
    (`count`, `index`, `copy`, `get`, `__len__`, …) and every other name, including one
    Python adds later, is a mutation. It used to be `name in _MUTATORS`, so
    `results.__init__(sorted(results, …))` / `results.__delitem__(slice(None))` /
    `results.__iadd__([…])` / `results[0].__init__({…})` all read GREEN while each one
    replaced the page the artifact records — the same closed-list fail-open the environ arm
    carried for `os.environ.__init__`. This one is NOT a binding-form indirection and it
    does not grow a clause per spelling: it is a polarity, and the two are different
    things. The residual list below is therefore about REACH, not about names.

    **NOT covered, by construction — every one of these is an indirection a static `ast`
    walk cannot resolve, and adding a clause per spelling is how eleven rounds produced
    eleven spellings:**

      - a value COMPUTED at runtime from the collection and then mutated —
        `next(iter(results)).clear()`, `[… for …][0].sort()`, a row returned by a callee;
      - an alias created INSIDE a callee, or in a module-level helper called with no
        argument (interprocedural reach, above);
      - an element reached through a function RETURN, a generator, or a global;
      - a DEEP copy (`deepcopy(results)`), whose elements are genuinely new: it is treated
        exactly like a shallow one, so `_c = deepcopy(results); _c[0]["id"] = "X"` is a
        STATED FALSE RED — the two spellings are indistinguishable statically and the
        file's polarity is the false red;
      - the KEY of a LITERAL container of a mutator (`_es = [results.sort]; _es[1]()` reds
        though it calls something else): `bound_mutator` carries the container NAME for the
        literal spelling, and only the slot-STORE spelling (`_sl["s"] = results.sort`) is
        key-compared. Stated, tolerated, and the reason a computed key is fail-closed
        (`_sl[_k] = results.sort` reds every call through `_sl`).

    ⛔ TWO SURVIVORS MEASURED AT THIS HEAD, named so the list above is evidence rather
    than a category (each permutes the recorded page while `compare()` still reads
    `rank_identical: True`). A third — `_m = [results][0]; _m.sort()` — was listed here and
    is NOT a survivor at this head (measured RED), and the mechanism is worth stating because
    it took three attempts to get right: the PAGE pass leaves it alone (a bare `Subscript`
    value is not an alias), but the TRACE pass does not — the page assignment's value mentions
    `trace` (`leg_trace=trace`), so `results` is an alias of the trace there and `_m` is then a
    name bound to an expression that mentions a watched name, which the alias rule's MENTION
    clause resolves. Renaming it does not help (same mention chain).

        globals()["results"].sort()        the page's NAME is computed at runtime
        operator.iadd(results[0], {…})     a module-level mutator handed an ELEMENT, and
                                           `_element_only()` must exempt subscripts (the
                                           artifact builds `[_result_id(r) for r in
                                           results]`)

    ⛔ `list.sort(results)` and `getattr(results, "sort")()` are NOT survivors — measured
    RED, because the receiver arm resolves the page through them.

    ⛔ THE DURABLE FIX IS NOT A THIRTEENTH CLAUSE — it is to make "the recorded page IS the
    measured page" a RUNTIME property (record the page, assert its identity in
    `compare()`), which cannot be evaded by a spelling at all. That direction was
    recommended at #14281 review round 11 and belongs to the tool, not to this pin; it is
    filed as #14316 so the residual has a durable home (#14315 was a duplicate of the same
    direction, filed from a parallel lane). This pin is the static half, and the honest
    form of a static half is to say where it stops.
    """
    alias_rebinds = _rebound_callee_names(run_def if tree is None else tree)
    #: ⛔ ONE OF THE TWO SITES THAT KEEP THE THREAD (#14326 review at `a29f3cd8`, P2): a
    #: shadowed reader (`list = lambda _x: results`) must not make `list(results)` a "pure"
    #: new container, which would drop the alias `_c = list(results)` from `watched`. Pinned
    #: by a mutant that deletes `rebound=` here and reds a test.
    aliases = _aliases_of(run_def, _collection_alias_predicate(var), loops=True,
                          rebound=alias_rebinds)
    watched = {var} | aliases
    # ⛔ THE RECORDED ROWS ARE A COLLECTION TOO: `_row = per_query[-1]` then
    # `_row["order"] = _row["order"][:5]` truncated the page the verdict reads, with
    # every `"per_query" in receiver` clause passing (measured). Resolve names bound to
    # ANY expression mentioning `per_query`.
    scalars = _scalar_bound_names(run_def)
    #: ⛔ THE SECOND LOAD-BEARING SITE (#14326 review at `a29f3cd8`, P2 — this one was
    #: UNPINNED until `8e4ffbc5a0`): without the thread, a module-level
    #: `list = lambda _x: per_query` + `_r = list(1)` + `_r[-1]["order"].sort()` measured
    #: `hits=[]` on a permuted page. Pinned by a mutant that deletes `rebound=` here.
    rows = ({"per_query"} | _aliases_of(
        run_def, lambda v: "per_query" in _names_in(v), loops=True,
        rebound=alias_rebinds)) - scalars
    # ⛔ ITERATION TARGETS ARE THE SAME COLLECTION, TO A FIXPOINT — for the MEASURED
    # collections as well as the recorded rows (#14281 review P1, measured). A `for`
    # target binds nothing that `_aliases_of` resolves, so an element rewrite through one
    # went straight through BOTH halves:
    #
    #     for _r in results: _r["id"] = "REDACTED"         -> 16 passed
    #     for _e in trace:  _e["reason"] = "ok"             -> 16 passed
    #     for row in per_query:
    #         for cell in row.values(): cell.sort()          -> 16 passed
    #
    # The first turns every `order` into a list of constants, so `compare()` compares
    # equal lists for ever — #14192's "a verdict that cannot fail" reached through a `for`
    # instead of a `getattr` — and the `intentional_keep` grant for this tool rests on
    # exactly that verdict. ONE LEVEL of taint (the previous form) missed the cascade, so
    # this is a fixed point over `watched | rows`.
    #
    # ⛔ THE TAINT IS A RECEIVER SET, NEVER A "HANDED" SET. The tool's own artifact builds
    # `[_result_id(r) for r in results]` and `{… e.get("leg") … for e in trace}`: handing
    # a tainted ELEMENT to a call is how the artifact is SUPPOSED to be built, so only a
    # mutator receiver or a `Store`/`Del` subscript on the tainted name is red. `handed`
    # keeps using `watched` — that is the COLLECTION itself, and passing it to a call may
    # sort it in place. Measured: the fixpoint leaves the real tool green.
    muts = watched | rows
    # ⛔ A SHALLOW COPY IS NOT THE PAGE, BUT ITS SLOTS ARE THE PAGE'S OWN ELEMENTS (#14326
    # review P0, MEASURED at `36a6451d4e` and reproduced end-to-end on the shipped tool):
    #
    #     _c = list(results); _c[0]["id"] = "X"      artifact records "X", pin PASSED
    #
    # `_c` is a NEW list holding the SAME row dicts, so `_c.sort()` is innocent (round 9's
    # measured false red, preserved) while `_c[0][…] = …` / `_c[0].sort()` rewrite what the
    # artifact records. `_builds_new_container` therefore decides only that the CONTAINER
    # is new; the copy's NAME goes in `holders` — the set that already means "a container
    # through whose SLOTS the page is reached" — so every existing arm that reaches
    # THROUGH a slot fires, and a bare `_c` stays what it is (a copy, not the page).
    # ⛔ AND A DEEP COPY IS TREATED THE SAME WAY, which is a stated FALSE RED: `deepcopy`
    # really does rebuild the elements, but a static walk cannot prove which of the two
    # spellings produced a given name, and the file's polarity is the false red (the
    # reviewer's own reading: "only a deep copy is [an exemption], and a deep copy cannot
    # be proven statically here").
    #: ⛔ NO `rebound=` THREAD HERE (#14326 review at `a29f3cd8`, P2; MEASURED): this site
    #: passes `copy_is_alias=True`, which makes `_aliases_of` COLLECT the copies rather than
    #: exempt them, so `_builds_new_container` — and with it the rebound callee set — is never
    #: consulted on this path. The argument was dead by construction.
    shallow_copies = _aliases_of(
        run_def,
        lambda v: _builds_new_container(v) and bool(_names_in(v) & watched),
        loops=True, copy_is_alias=True) - watched
    # ⛔ A CONTAINER THAT HOLDS THE COLLECTION IS NOT THE COLLECTION — it is an INDEX into
    # the same object graph, so it joins a SECOND set (`holders`), never `muts` (#14281
    # review P1, measured). The distinction is load-bearing in both directions:
    #
    #     _box[0] = results[0]        a SLOT REPLACEMENT — a bind, mutates nothing
    #     _box[0] |= {…}              a write INTO the held object — a page mutation
    #
    # ⛔ ACCEPTED FALSE RED, stated because this file's contract is to state them: when the
    # container is bound to a LITERAL that MENTIONS the page (`_box = [results]`), the alias
    # rule puts `_box` in `watched`, so the container's OWN operations are red as if the box
    # were the page — `_box.clear()`, `_box.append(None)`, `_box[0] = results[0]` (all three
    # measured RED at `11f34db125`). Tolerable: each is a no-op for the artifact and the
    # container ops that matter (`_box[0].sort()`, `_box[0][k] = v`) are mutations anyway.
    # A container built EMPTY first (`_box = [None]`) is not in `watched`, which is the shape
    # `holders` was written for.
    #
    # Folding holders into `muts` (the first form of this arm) reds the bind line and every
    # container-only operation above — the bind-line over-fire this file has been cured of
    # three times, and its contract is to state its false reds rather than add them.
    # `_tainted()` below lets a holder through only where the expression REACHES THROUGH
    # the slot (`_d["k"]`, `_o.a`, `self._page`).
    #: A LOCAL CALLABLE the scan can name: `_key = lambda _e: …` and a nested `def`. Used
    #: below to taint a CALLBACK's parameters — the callback is handed the collection's
    #: ELEMENTS, and until this map existed nothing tied its parameter to the page.
    callable_params: dict[str, ast.AST] = {}
    for _n in ast.walk(run_def):
        if isinstance(_n, (ast.Assign, ast.AnnAssign)) and isinstance(_n.value, ast.Lambda):
            for _t in (list(_n.targets) if isinstance(_n, ast.Assign) else [_n.target]):
                if isinstance(_t, ast.Name):
                    callable_params[_t.id] = _n.value
        elif isinstance(_n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            callable_params.setdefault(_n.name, _n)
    holders: set[str] = set(shallow_copies)
    changed = True
    while changed:
        changed = False
        for _node in ast.walk(run_def):
            if isinstance(_node, (ast.For, ast.AsyncFor, ast.comprehension)):
                _iter, _target = _node.iter, _node.target
                # ⛔ THE ITERABLE MUST MENTION A WATCHED NAME (#14281 review P2, measured).
                # Without this gate EVERY loop target in `run()` joined `muts`, so
                # `_m = [results][0]; _m.sort()` red only because `run()` happens to use
                # `_m` as an unrelated comprehension target (`member_table = {… for _m, _f,
                # name in TIMED}`) — a false red decided by VARIABLE NAMING, and any
                # `for row in report: row["x"] = 1` would red too. The fixpoint still
                # cascades (`for row in per_query:` then `for cell in row.values():`) because
                # `row` is in `muts` by the second pass.
                if not (_names_in(_iter) & (muts | holders)):
                    continue
            elif isinstance(_node, (ast.Assign, ast.AnnAssign)) and _node.value is not None:
                # ⛔ AN UNPACKED BIND IS AN ELEMENT BIND (#14281 review P1, measured).
                # `(_a,) = results` takes the FIRST ELEMENT, and `_a |= {"id": …}` then
                # rewrote it — MEASURED GREEN (16 passed) even after the `for`-target
                # fixpoint, because `_aliases_of` resolves an unpack only when the VALUE
                # is a literal Tuple/List (`(page,) = (results,)`), and a Name there falls
                # through. The element of a tainted collection is a tainted element, so the
                # target names join `muts` exactly as a `for` target does.
                _targets = (list(_node.targets) if isinstance(_node, ast.Assign)
                            else [_node.target])
                _iter = _node.value
                if not (_names_in(_iter) & (muts | holders)):
                    continue
                _unpacked = [t for t in _targets if isinstance(t, (ast.Tuple, ast.List))]
                if _unpacked:
                    # ⛔ EVERY TARGET OF A CHAINED UNPACK, not only the first (#14281
                    # review P2 at `06bdae1a7926`, measured GREEN): `(_a,) = (_b,) =
                    # results` binds the SAME collection to BOTH names, and keying on
                    # `_unpacked[0]` made the verdict depend on target ORDER — `_a |=
                    # {"id": "REDACTED"}` was RED while `_b |= {"id": "REDACTED"}`,
                    # the identical rewrite one name over, was GREEN (`compare()` then
                    # calls a genuinely permuted pair `rank_identical: True`).
                    _target = ast.Tuple(
                        elts=[e for t in _unpacked for e in t.elts], ctx=ast.Load())
                else:
                    # ⛔ A CONTAINER THAT HOLDS THE COLLECTION IS THE COLLECTION (#14281
                    # review P1 at `06bdae1a7926`, four shapes measured GREEN):
                    #
                    #     _slots = {};  _slots["k"] = results[0]; _slots["k"] |= {…}
                    #     _box = [None]; _box[0] = results[0];   _box[0] |= {…}
                    #     _d = {};      _d["k"] = results[0];    _d["k"].update({…})
                    #     _d["k"]["order"].sort()
                    #
                    # `_slots` is bound to an EMPTY container, so `_aliases_of` resolves
                    # no name (there is nothing to mention yet) and the ONLY bind that
                    # makes the container reachable is the SLOT STORE — a target with no
                    # `Name` at all, which no binding-form arm looked at. This is not a
                    # twelfth spelling: it is the aggregate/field propagation rule every
                    # production taint analyzer carries (a container is reached through
                    # its slots), applied to the two target shapes that ARE slots — a
                    # `Subscript` (`_slots[k]`, `_box[0]`) and an `Attribute`
                    # (`self._page`). The container name joins `holders`; every existing
                    # arm then sees it through `_tainted()` on the receiver, exactly as it
                    # sees `_box = [results]` today — but only when the receiver REACHES
                    # THROUGH the slot, so the bind that makes the page reachable stays
                    # GREEN.
                    _slots = [t for t in _targets
                              if isinstance(t, (ast.Subscript, ast.Attribute))]
                    if not _slots:
                        # ⛔ A CONTAINER ASSIGNED FROM A HOLDER IS THE SAME CONTAINER
                        # (#14281 review P1's family, measured GREEN at `6fe2a1f5c83`):
                        #
                        #     _d = {}; _d["k"] = results[0]
                        #     _t = _d; _t["k"] |= {"id": "REDACTED"}   -> hits=[]
                        #     _t["k"].update({"id": "REDACTED"})       -> hits=[]
                        #
                        # The slot store put the element in `_d`; `_t` is a second NAME
                        # for that same index, exactly as `a1 = results` is a second
                        # name for the page — and the hop was missing, so the carrier
                        # arm covered only the name the store happened to mention.
                        # Measured through `compare()`: a genuinely permuted pair reads
                        # `rank_identical: True`.
                        if isinstance(_iter, ast.Name) and _iter.id in holders:
                            _fresh = {b.id for t in _targets for b in ast.walk(t)
                                      if isinstance(b, ast.Name)} - holders
                            if _fresh:
                                holders |= _fresh
                                changed = True
                        continue
                    if _cannot_carry(_iter, scalars):
                        continue
                    _fresh = set().union(
                        *(_names_in(s.value) for s in _slots)) - holders
                    if _fresh:
                        holders |= _fresh
                        changed = True
                    continue
            elif isinstance(_node, (ast.Lambda, ast.FunctionDef, ast.AsyncFunctionDef)):
                # ⛔ A DEFAULT ARGUMENT BINDS THE COLLECTION INTO THE CALLEE (#14281
                # review P1, measured GREEN): `_f = (lambda _l=results: _l.sort()); _f()`
                # permutes the page and every other arm passes, because inside the lambda
                # the receiver is a PARAMETER and nothing ties it to `results`. A default
                # that mentions a tainted name taints the parameter names of that scope —
                # conservative on purpose: one tainted default reds every parameter, and a
                # false red needs a lambda in `run()` that this tool does not have.
                _args = _node.args
                _defaults = list(_args.defaults) + [d for d in _args.kw_defaults
                                                    if d is not None]
                if not any(_names_in(d) & muts for d in _defaults):
                    continue
                _target = ast.Tuple(elts=[ast.Name(id=a.arg) for a in
                                          list(_args.posonlyargs) + list(_args.args)
                                          + list(_args.kwonlyargs) + [a for a in
                                          (_args.vararg, _args.kwarg) if a is not None]],
                                    ctx=ast.Load())
            elif isinstance(_node, ast.Call) and any(
                    _names_in(_a) & (muts | holders)
                    for _a in list(_node.args) + [_k.value for _k in _node.keywords]):
                # ⛔ A CALLBACK RECEIVES THE COLLECTION'S ELEMENTS (#14326 review P2,
                # measured GREEN): `sorted(results, key=<lambda that writes an element>)`
                # hands the page to a PURE callee, so the element-hand-off arm cannot speak
                # for the CALL — and the write happens inside a callback whose parameter
                # nothing tainted. The callable is in the SAME call's argument list, so its
                # parameters ARE the tainted elements. Both spellings: the inline lambda, and
                # a local name bound to one (`_key = lambda …`) or to a nested `def`.
                for _a in list(_node.args) + [_k.value for _k in _node.keywords]:
                    _callables = [w for w in ast.walk(_a) if isinstance(w, ast.Lambda)]
                    if isinstance(_a, ast.Name) and _a.id in callable_params:
                        _callables.append(callable_params[_a.id])
                    for _cb in _callables:
                        _cargs = _cb.args
                        fresh = {a.arg for a in
                                 list(_cargs.posonlyargs) + list(_cargs.args)
                                 + list(_cargs.kwonlyargs)} - muts
                        if fresh:
                            muts |= fresh
                            changed = True
                continue  # this branch taints directly; there is no `_target` to add
            else:
                continue
            fresh = _names_in(_target) - muts
            if fresh:
                muts |= fresh
                changed = True
    # ⛔ A BOUND MUTATOR OF THE PAGE IS THE SAME POINTER (#14281 review P1, four shapes
    # measured GREEN and each permuting the page before `order` reads it, so `compare()`
    # reports `rank_identical: True` over a genuinely permuted pair):
    #
    #     _s = results.sort; _s()          _a = results; _s = _a.sort; _s()
    #     _f = (lambda _l=results: _l.sort()); _f()          _c = trace.clear; _c()
    #
    # `results.sort` is an `Attribute` on the value, not an `Attribute` call, so the
    # receiver arm below never fired; `_aliases_of` binds the NAME `_s` and the call is a
    # bare `Name`. `_environ_mutations` already carries this arm for `os.environ.pop`, and
    # `_per_query_element_mutations` calls the same shape a hole rather than a residual.
    def _tainted(node: ast.AST) -> set[str]:
        """The names on `node` that are the page — or a SLOT'S VALUE when it REACHES
        THROUGH a container (`_d["k"]`, `_o.a`, `self._page`), never the container itself.

        A bare holder name is the CONTAINER, so it never counts as the page here — but this
        function is not the only arm that sees it: when the container was bound to a LITERAL
        that MENTIONS the page (`_box = [results]`, the shape this file itself uses), the
        alias rule already put `_box` in `watched`, so the container's OWN operations are red
        as if the box were the page. ⛔ ACCEPTED FALSE RED, measured: `_box.clear()`,
        `_box.append(None)` and `_box[0] = results[0]` all red at `11f34db125`, none of them
        mutates what the artifact reads. This docstring used to claim they were GREEN; the
        file's contract is to state its false reds, so they are stated here as well as at the
        comment above. A container built EMPTY first (`_box = [None]`) is not in `watched`
        and is the shape `holders` exists for. The distinction that still matters is the same
        one the `Subscript` STORE arm needs: a slot that HOLDS the page vs. a write INTO it.
        """
        names = _names_in(node)
        if isinstance(node, (ast.Subscript, ast.Attribute)):
            return names & (muts | holders)
        return names & muts

    def _bound_mutator_expr(v: ast.AST) -> bool:
        """A bound mutator, or a LITERAL CONTAINER of one (`_d = {"s": results.sort}`).

        ⛔ The COMPOSITION was uncaught (#14281 review P2, measured GREEN with the page
        permuted): the seed matched only a bare `Attribute`, so `_d["s"]()` reached the
        page through a slot nothing resolved. Same seed-vs-check asymmetry `_environ_
        mutations` closed for `{"p": os.environ.pop}`.
        """
        if isinstance(v, ast.Attribute):
            return _is_mutator_method(v.attr) and bool(_tainted(v.value))
        if isinstance(v, ast.Dict):
            return any(e is not None and _bound_mutator_expr(e) for e in v.values)
        if isinstance(v, (ast.List, ast.Tuple, ast.Set)):
            return any(_bound_mutator_expr(e) for e in v.elts)
        return False

    # ⛔ NO `rebound=` HERE (#14326 review at `a29f3cd8`, P2; MEASURED): `_bound_mutator_expr`
    # resolves an `Attribute` mutator or a LITERAL container of one — never a copy CALLEE — so
    # the `_builds_new_container` gate the thread feeds cannot drop a name this predicate
    # matched, and deleting the argument left 31 passed.
    bound_mutator = _aliases_of(run_def, _bound_mutator_expr, loops=True)

    def _mutator_slot_keys(tree: ast.AST) -> dict[str, frozenset[str] | None]:
        """CONTAINER name -> the SLOT KEYS that were ASSIGNED a bound mutator.

        ⛔ THE SLOT-STORE SPELLING WAS GREEN (#14326 round 9, MEASURED at `88859180`):
        `_sl = {}; _sl["s"] = results.sort; _sl["s"](key=…)` measured `hits=[]` while the
        LITERAL spelling of the same thing (`_d = {"s": results.sort}; _d["s"](…)`) reds.
        The literal is resolved by `_bound_mutator_expr`'s `Dict` arm, but a STORE into a
        slot reaches neither `_aliases_of` (name targets only) nor the `Attribute`/`Name`
        targets `_rebound_mutator_slots` collects — so nothing knew `_sl` held a mutator.
        The same gap made the second hop (`_b.s = _d["s"]; _b.s(…)`) green.

        ⛔ AND THE KEY, NOT ONLY THE CONTAINER (#14326 review P2, MEASURED at
        `36a6451d4e`): collecting the container NAME put every call through ANY slot of it
        in the mutation arm —

            _sl = {}; _sl["s"] = results.sort; _sl["label"]()     hits=[6]  (false RED)
            _sl = {}; _sl["s"] = results.sort; _sl["s"](key=str)   hits=[6]  (correct)

        — the second is the mutation and the first calls nothing that can reach the page.
        The key is statically visible on BOTH sides, so the arm compares them. A store
        whose key is COMPUTED (`_sl[_k] = results.sort`) records `None`, which reds every
        call through that container: that is the one shape where the keys cannot be
        compared, and it keeps the arm fail-closed.
        """
        found: dict[str, set[str]] = {}
        unknown: set[str] = set()
        for sub in ast.walk(tree):
            targets: list[ast.AST] = []
            value: ast.AST | None = None
            if isinstance(sub, ast.Assign):
                targets, value = list(sub.targets), sub.value
            elif isinstance(sub, ast.AnnAssign) and sub.value is not None:
                targets, value = [sub.target], sub.value
            if value is None or not _bound_mutator_expr(value):
                continue
            for target in targets:
                if not isinstance(target, ast.Subscript):
                    continue
                names = _names_in(target.value)
                key = _constant_key(target.slice)
                if key is None:
                    unknown |= names
                else:
                    for name in names:
                        found.setdefault(name, set()).add(key)
        keys_out: dict[str, frozenset[str] | None] = {
            name: frozenset(keys) for name, keys in found.items()}
        for name in unknown:
            keys_out[name] = None
        return keys_out

    mutator_slot_keys = _mutator_slot_keys(run_def)

    def _mutator_slot_call(func: ast.Subscript) -> bool:
        """True when `func` selects a SLOT a bound mutator was STORED into.

        Keyed for the stored spellings (#14326 review P2). A LITERAL container
        (`_es = [results.sort]; _es[0](…)`) is decided by `bound_mutator` — the container
        NAME — and keeps its stated FALSE RED: `_es[1]()` reds though it calls something
        else, because the literal's own key set is not carried here.
        """
        names = _names_in(func.value)
        if names & bound_mutator:
            return True
        key = _constant_key(func.slice)
        for name in names & set(mutator_slot_keys):
            keys = mutator_slot_keys[name]
            if keys is None or key is None or key in keys:
                return True
        return False

    def _rebound_mutator_slots(tree: ast.AST) -> frozenset[str]:
        """CALLEE names bound to a BOUND MUTATOR — `_b.s = results.sort`, `_x.count = results.sort`.

        ⛔ THE OBJECT-SLOT SPELLING WAS GREEN WHILE THE DICT-SLOT SPELLING RED (#14326 round 8,
        MEASURED at `9762589e`): `_b.s = results.sort; _b.s(key=…)` gives `hits=[]` while
        `_d = {"s": results.sort}; _d["s"](key=…)` gives `hits=[5]`. The seed above binds only a
        NAME target, so an ATTRIBUTE store never reached `bound_mutator` at all, and a
        `_PURE_METHODS` name (`_x.count = results.sort; _x.count()`) is allow-listed by NAME
        with its VALUE never read. Collected as a name set — the same attribute-segment
        semantics the round-7 rebind work applies to the allow-lists — because over-collecting
        here can only RED, the cheap direction.
        """
        found: set[str] = set()
        for sub in ast.walk(tree):
            targets: list[ast.AST] = []
            value: ast.AST | None = None
            if isinstance(sub, ast.Assign):
                targets, value = list(sub.targets), sub.value
            elif isinstance(sub, ast.AnnAssign) and sub.value is not None:
                targets, value = [sub.target], sub.value
            if value is None:
                continue
            # ⛔ A VALUE READ OUT OF A CONTAINER THAT HOLDS A MUTATOR IS THAT MUTATOR (#14326
            # round 9, MEASURED at `88859180`: `_d = {"s": results.sort}; _b.s = _d["s"];`
            # `_b.s(key=str)` was GREEN). `_d` is in `bound_mutator` (the literal-dict arm), so
            # the slot is handed the same pointer one hop out; accepting the `Subscript` value
            # here is what lets the `Attribute` target below record it.
            if not (_bound_mutator_expr(value)
                    or (isinstance(value, ast.Subscript)
                        and bool(_names_in(value.value) & bound_mutator))):
                continue
            for target in targets:
                # ⛔ `Name` TARGETS ARE DELETED, NOT KEPT (#14326 round 9, measured: removing
                # this arm leaves the suite green and changes no `_mutations_of` verdict,
                # because `_aliases_of(…, loops=True)`'s `bound_mutator` seed already
                # supplies every `Name` target). This arm was the SLOT case's own addition —
                # an `Assign`/`AnnAssign` into an OBJECT slot — and a clause no mutant can
                # red reads as coverage and is not.
                if isinstance(target, ast.Attribute):
                    found.add(target.attr)
        return frozenset(found)

    rebound_mutators = _rebound_mutator_slots(run_def)

    def _container_of_page(v: ast.AST) -> bool:
        """A LITERAL container assigned the page: `_box = [results]`, `_d = {"p": results}`."""
        if isinstance(v, (ast.List, ast.Tuple, ast.Set)):
            return any(bool(_names_in(e) & muts) for e in v.elts)
        if isinstance(v, ast.Dict):
            return any(e is not None and bool(_names_in(e) & muts)
                       for e in list(v.values) + list(v.keys))
        return False

    #: `holders` tracks SLOTS; this tracks a container NAMED in an assignment, and
    #: `_element_only` needs both — `_box = [results]; random.shuffle(_box[0])` was GREEN
    #: with only `holders` (#14281 review P2, measured).
    # ⛔ NO `rebound=` HERE EITHER (#14326 review at `a29f3cd8`, P2; MEASURED: deleting it
    # left 31 passed): `_container_of_page` matches a container that MENTIONS the page, and a
    # copy callee is already excluded by `_builds_new_container` before `rebound` can matter.
    page_container = holders | _aliases_of(run_def, _container_of_page, loops=True)
    hits: list[int] = []
    for node in ast.walk(run_def):
        if node is artifact_append:
            continue
        # ⛔ NOT LINE-FILTERED. `def _tidy(): results.sort()` written ABOVE the read and
        # `_tidy()` called after it is "after" in EXECUTION but not in LINE NUMBER, and a
        # `lineno` filter cannot do scope work (measured: `functools.partial(
        # random.shuffle, results)` bound above the read stayed green).
        if isinstance(node, ast.Call) and (getattr(node.func, "attr", None) in _READ_NAMES
                                          or getattr(node.func, "id", None) in _READ_NAMES):
            continue  # the read itself COLLECTS into the trace; it is not a mutation of it
        receiver: set[str] = set()
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr in rebound_mutators:
            # ⛔ A BOUND MUTATOR REACHED THROUGH AN OBJECT SLOT (#14326 round 8, MEASURED
            # GREEN): `_b.s = results.sort; _b.s(key=…)` permutes the page in place, and the
            # artifact then records the CANONICALISED order so `compare()` reports
            # `rank_identical: True` over a genuinely permuted pair — #14192's false PASS,
            # reached through the guard built to stop it. The credit is the CALLEE NAME: the
            # slot HOLDS the mutator, so `_tainted(node.func.value)` (which only accepts a
            # page-holding receiver) is the wrong test here.
            receiver = _names_in(node.func)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and _is_mutator_method(node.func.attr):
            # A method called ON a slot's value (`_d["k"].update(…)`) mutates the held
            # object, so a container the page was stored into counts here — but only when
            # the receiver REACHES THROUGH it (`_tainted`).
            receiver = _tainted(node.func.value)
        elif isinstance(node, ast.Subscript) and isinstance(node.ctx, (ast.Store, ast.Del)):
            # `results[0] = …` writes INTO the page → a mutation. `_box[0] = results[0]`
            # REPLACES a slot of a container that only holds the page → a bind.
            receiver = _tainted(node.value)
        elif isinstance(node, ast.Call) and (
                (isinstance(node.func, ast.Name) and node.func.id in bound_mutator)
                or (isinstance(node.func, ast.Subscript)
                    and _mutator_slot_call(node.func))):
            # ⛔ AND THROUGH A CONTAINER SLOT (#14281 review P2, measured GREEN):
            # `_d = {"s": results.sort}; _d["s"](key=…, reverse=True)` and
            # `_es = [results.sort]; _es[0](…)` permute the PAGE before the artifact reads
            # it. The bound mutator is the SAME pointer in a slot, so the seed resolves a
            # literal container of one and this arm accepts a `Subscript` function.
            receiver = _names_in(node.func)  # a bound mutator CALL is the mutation
        # ⛔ THE AUGMENTED SPELLING IS THE SAME MECHANISM (#14281 review P1, measured).
        # `dict |= other` is PEP 584's in-place merge — the exact semantics of `update`,
        # which `_MUTATORS` already carries — but an `AugAssign` whose target is a BARE
        # tainted name matched NEITHER receiver arm, so `for _r in results: _r |= {"id":
        # "REDACTED"}` was GREEN (16 passed) and `compare()` then reported
        # `rank_identical: True` on a permuted pair: the ids are `"REDACTED"`, not absent,
        # so its `if not any(pq["order"])` refusal passes too. Same for `_r0 = results[0];
        # _r0 |= …`, an unpack target, and the `per_query` cascade. Not a new class, and
        # the asymmetry was the defect: `_per_query_element_mutations` already carries this
        # arm for the recorded rows. `_r["id"] += "X"` was already red through the
        # Subscript arm above; this is only the bare-name target. The real `run()` holds
        # zero `AugAssign` nodes, so this cannot false-red the tool (measured).
        elif isinstance(node, ast.AugAssign):
            # A bare-name target counts only when it IS the page/alias; a container is
            # reachable through a slot too (`_slots["k"] |= …` merges into the held
            # element), while `_slots |= …` on the container mutates nothing the artifact
            # reads (#14281 review P1).
            _t = node.target
            if isinstance(_t, ast.Name):
                receiver = {_t.id} if _t.id in muts else set()
            else:
                receiver = _tainted(_t)
        callee = getattr(node.func, "id", None) or getattr(node.func, "attr", None) \
            if isinstance(node, ast.Call) else None
        def _element_only(arg: ast.AST) -> bool:
            """`results[0]` is an ELEMENT, not the collection (this file says so twice).

            ⛔ UNLESS THE BASE IS A CONTAINER OF THE PAGE (#14281 review P2, measured):
            `_box = [results]; random.shuffle(_box[0])` shuffles the WHOLE page through a
            slot, and the blanket `return True` exempted it. `_box[0]` is the page; only a
            subscript over the collection itself is that collection's element.
            """
            if isinstance(arg, ast.Subscript):
                # ⛔ UNLESS THE BASE IS A CONTAINER OF THE PAGE (#14281 review P2,
                # measured): `_box = [results]; random.shuffle(_box[0])` shuffles the WHOLE
                # page through a slot, and the blanket `return True` exempted it.
                # `page_container` is `holders` (slot-tracked containers) UNION the names
                # bound to a LITERAL container of the page (`_box = [results]`, which the
                # mention rule puts in `watched` instead), so a subscript over either is NOT
                # an element of the page.
                return not bool(_names_in(arg.value) & page_container)
            if isinstance(arg, ast.Call):
                callee_id = getattr(arg.func, "id", None) or getattr(arg.func, "attr", None)
                return callee_id in _PURE_READS or any(
                    _element_only(a) for a in arg.args)
            return isinstance(arg, ast.JoinedStr)  # an f-string mutates nothing it interpolates

        def _row_ref(arg: ast.AST) -> bool:
            """`per_query[-1]` and `per_query[-1]["order"]` are RECORDED OBJECTS.

            ⛔ `handed` tests `watched` — the measured PAGE and TRACE. The recorded rows
            are a SECOND collection, and handing one of them to a call sorts what
            `compare()` reads (#14281 review P1, measured GREEN with the recorded page
            permuted, `rank_identical: True`):

                def _scrub(row): row["order"].sort()
                _scrub(per_query[-1])          # and `_flip(per_query[-1])`
                def _scrub3(rows): rows.sort(key=str)
                _scrub3(per_query)

            `_element_only()` cannot decide this one: it exempts a subscript over the
            collection ITSELF (right for `results[0]` — and no longer ANY subscript, since a
            container slot like `_box[0]` is the page), while `per_query[-1]` is a dict
            carrying the order.
            """
            if isinstance(arg, ast.Name):
                return arg.id in rows
            if isinstance(arg, ast.Subscript):
                return _row_ref(arg.value)
            # ⛔ NOT `_names_handed(arg) & rows`: `stats([pq["stages"].get(…) for pq in
            # per_query])` builds a NEW list of floats (measured false RED on the real
            # tool at lines 329/333), and the rows it iterates never reach the callee.
            return False

        def _derived(arg: ast.AST) -> bool:
            """A comprehension BUILDS A NEW collection, so it cannot mutate its source.

            ⛔ Measured RED without this (#14281 review P2): `", ".join(e.get("leg") or ""
            for e in trace)` and `statistics.fmean(len(r) for r in results)` were reported
            as "an in-place sort/clear" by the `handed` arm, decided by the CALLEE's name
            rather than by any mutation.

            ⛔ THE BODY IS GRADED **ELSEWHERE**, AND THAT IS DELIBERATE (#14326 review P2).
            A body-grading loop lived here for one round and could only ever run for a
            NON-pure callee: `handed` short-circuits on `callee not in _PURE_READS` BEFORE
            consulting this helper, so `any(...)`/`sorted(...)`/`min(...)` wrappers and a
            comprehension with no callee at all were untouched by it. The class is closed at
            the SITE instead — `_element_handed` below — so a tainted ELEMENT handed to a
            callee that is not `_PURE_CALLEES` reds whatever the wrapper is. Keeping both
            would leave a loop no mutant could red.
            """
            return isinstance(arg, (ast.ListComp, ast.GeneratorExp, ast.SetComp,
                                    ast.DictComp))

        handed = isinstance(node, ast.Call) and callee not in _PURE_READS and any(
            _names_handed(arg) & watched and not _element_only(arg) and not _derived(arg)
            for arg in list(node.args) + [k.value for k in node.keywords])

        def _element_handed(call: ast.AST, call_callee: str | None) -> bool:
            """A TAINTED ELEMENT handed to a call that is not a known non-mutator.

            ⛔ THE COLLECTION'S ELEMENTS ARE THE COLLECTION'S ROWS (#14326 review P2,
            measured GREEN with `compare()` then reporting `rank_identical: True` over a
            genuinely permuted pair):

                def _scrub(_e): _e["id"] = "REDACTED"
                _flag = any(_scrub(r) for r in results)
                _j    = ", ".join(_scrub(r) for r in results)
                _x    = [r for r in results if _scrub(r)]
                _sorted = sorted(results, key=<lambda that writes an element>)

            `handed` above cannot see these: it tests `watched` (the collection ITSELF), and
            an element is deliberately NOT that (the artifact hands `_result_id(r)` the
            elements it is supposed to record). `muts` holds them — the comprehension/`for`
            target taint — but a tainted name is only a MUTATION when something writes it.
            So this arm asks the question `handed` asks, one level down: is a tainted ELEMENT
            handed to a callee that is not `_PURE_CALLEES`? A pure read, a pure method and the
            blessed id mapper are exempt (`len(r)`, `e.get(…)`, `_result_id(r)` — the real
            tool's own artifact), everything else is fail-closed, because an unknown helper is
            free to write the row it was given.

            ⛔ THE CLASS NEEDS NO WRAPPER-SHAPED CLAUSE. An earlier attempt graded the
            comprehension's BODY inside `_derived`, which could only ever fire for a NON-pure
            callee (`handed` short-circuits before `_derived` is consulted — see `_derived`)
            and left `any(...)`, `sorted(...)`, `min(...)` and a comprehension with NO callee
            at all uncovered. Grading the SITE covers every wrapper at once, and `_derived` is
            left as what it always was: the exemption for a comprehension that hands a NEW
            collection.
            """
            if not isinstance(call, ast.Call) or call_callee in _PURE_CALLEES:
                return False
            return any(
                _names_handed(arg) & muts and not _element_only(arg) and not _derived(arg)
                for arg in list(call.args) + [k.value for k in call.keywords])
        def _slot_ref(arg: ast.AST) -> bool:
            """A slot's VALUE handed to a call is the object stored there (#14281 P1).

            `_element_only()` exempts a subscript over the collection ITSELF, because
            `results[0]` is an element of the page and the artifact hands ELEMENTS to
            `_result_id`; it does NOT exempt a subscript over a holder (`_box[0]`), and a
            holder bound to `[None]` and filled by a SLOT STORE is covered too (`holders`
            tracks the store). What is still not followed is a holder filled by an
            UNTRACKED route — `_box = [None]; _fill(_box); _box[0].sort()` is GREEN
            (measured), because the page entered the box inside a callee. Only the REACHING
            form counts — `_d` alone is the container, and shuffling it changes nothing the
            artifact reads.
            """
            return (isinstance(arg, (ast.Subscript, ast.Attribute))
                    and bool(_names_in(arg) & holders))

        row_handed = isinstance(node, ast.Call) and callee not in _PURE_READS and any(
            _row_ref(arg) for arg in list(node.args) + [k.value for k in node.keywords])
        slot_handed = isinstance(node, ast.Call) and callee not in _PURE_READS and any(
            _slot_ref(arg) for arg in list(node.args) + [k.value for k in node.keywords])
        # ⛔ EVERY `receiver` ARM ABOVE IS ALREADY RESOLVED AGAINST THE SET IT BELONGS TO
        # (`muts`, or `muts | holders` for a container slot), so this is a truthiness
        # test — re-intersecting with `muts` here is what kept a slot mutation GREEN after
        # its arm existed, because a holder is never a tainted name (#14281 review P1).
        if (receiver or handed or row_handed or slot_handed
                or _element_handed(node, callee)):
            hits.append(node.lineno)
    return hits


def _per_query_element_mutations(run_def: ast.AST, after_lineno: int) -> list[int]:
    """Lines that mutate the RECORDED entries through an ALIAS of a `per_query` element.

    ⛔ MEASURED GREEN at `e66d23f539d4` — every one of these leaves `run()` returning
    pages the pin never inspected, while `compare()` reads exactly them:

        for _pq in per_query: _pq["order"].sort()
        for _pq in per_query: _pq["order"] = sorted(_pq["order"], key=str)
        for _i, _pq in enumerate(per_query): _pq["order"].sort()
        for _pq in per_query: _s = _pq["order"]; _s.sort()
        (zed,) = per_query[-1:]; zed["order"].sort()

    ⛔ IT IS NOT THE COVERAGE — IT IS THE MESSAGE, AND THAT HAD TO BE MEASURED (#14281
    review P2). This said the UNPACKED target was "the only listed shape it alone
    catches"; substituting `element_mutations = []` leaves ALL FIVE shapes listed above RED
    through `_mutated_after`'s `rows`/`muts` fixpoint (measured), including
    `(zed,) = per_query[-1:]; zed["order"].sort()` — the fixpoint keys on the TARGET being
    a Tuple, not on a value of matching length. What this guard uniquely reds is a REBIND
    of an element name (`(zed,) = per_query[-1:]; zed = {}`), which mutates nothing: an
    accepted, conservative false red. It stays because it reports the element-specific
    diagnosis FIRST and is asserted on its own; it is not the guard that holds the class.

    `_mutated_after` covers a LITERAL receiver (`per_query[-1]["order"].sort()`) and, since
    `bb14ebfc501c`, a name bound to `per_query[-1]` and a `for` target — all through
    `rows`. `rows` is `{"per_query"} | …` on every call, so the older
    `or "per_query" in receiver` term was provably subsumed and has been deleted rather
    than left standing as a clause nothing can red.

    ⛔ AND THE ONE THIS GUARD USED TO CARRY IS CLOSED: a BOUND MUTATOR of an element
    (`for _pq in per_query: _s = _pq["order"].sort; _s()`) was GREEN here while the
    sibling `_mutated_after` already carried the arm for the page — this residual list
    contradicted the guard one function above it. The arm lives below now, and the shape
    is a test.
    """
    element_names: set[str] = set()
    #: Nodes that BIND an element name. A bind is not a mutation (#14281 review F5,
    #: measured): `pq = per_query[-1]` was reported as "mutate a RECORDED entry" on the
    #: very line that only names it, so an innocent `pq = per_query[-1]; print(pq["order"])`
    #: red the pin with a wrong diagnosis.
    defining: set[int] = set()
    for node in ast.walk(run_def):
        # ⛔ ONLY the two shapes that name the ELEMENT ITSELF: `pq = per_query[-1]` and a
        # STATEMENT-LEVEL `for _pq in per_query:`. A name bound to a DERIVED collection
        # (`stage_table = {… for pq in per_query}`, or any comprehension) is not the
        # element, and treating it as one false-reds ordinary report-building code —
        # measured: the first cut of this guard red the real tool at
        # `tools/read_wave_latency.py:328,332`, which build the artifact dict itself.
        if isinstance(node, ast.Assign):
            targets: list[ast.AST] = list(node.targets)
            value: ast.AST = node.value
        elif isinstance(node, (ast.AnnAssign, ast.NamedExpr)) and node.value is not None:
            targets, value = [node.target], node.value
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            targets, value = [node.target], node.iter
        else:
            continue
        if "per_query" not in _names_in(value):
            continue
        # ⛔ THE VALUE MAY BE THE COLLECTION ITSELF, not only a slice of it: `(_a,) =
        # (_b,) = per_query` binds the FIRST ELEMENT to both names, and a bare `Name`
        # value was rejected here (the guard accepted only a `Subscript`), so a chained
        # unpack of the WHOLE list named no element and `_b["order"].sort()` mutated a
        # recorded page invisibly — measured GREEN at `f44e79ed36ee` (#14281 review P2).
        # A name DERIVED from the collection (`_rows = [x for x in per_query]`) is still
        # rejected: only the collection and its own aliases are accepted.
        if not (isinstance(node, (ast.For, ast.AsyncFor))
                or isinstance(value, ast.Subscript)
                or (isinstance(value, ast.Name)
                    and value.id in ({"per_query"} | _aliases_of(
                        run_def, _collection_alias_predicate("per_query"), loops=True)))):
            continue
        # ⛔ EVERY NAME EVERY TARGET BINDS, not only a bare `Name` and not only the FIRST
        # target — `for _i, _pq in enumerate(per_query):`, `(zed,) = per_query[-1:]`, and
        # the CHAINED `(_a,) = (_b,) = per_query` all name the ELEMENT. The last was
        # GREEN (`_b["order"].sort()` mutated a recorded page invisibly) because this
        # loop accepted only `len(node.targets) == 1`, so the second target of a chain was
        # never walked — measured at `f44e79ed36ee` (#14281 review P2, target ORDER
        # deciding the verdict).
        # ⛔ `_mutated_after` DOES cover them (measured with this guard neutered: all five
        # shapes stay RED) — the text here claimed its unpack branch needed a Tuple/List
        # VALUE of matching length, and it keys on the TARGET instead (#14281 review P2).
        # A tuple target over a CALL marks the index too (fail-closed: a false red on an
        # index that is never mutated, against a GREEN permuted page).
        for target in targets:
            for bound in ast.walk(target):
                if isinstance(bound, ast.Name):
                    element_names.add(bound.id)
                    defining.add(id(node))
    # ⛔ AN ALIAS OF AN ELEMENT'S INNER LIST IS THAT INNER LIST (#14281 review F3,
    # measured GREEN in BOTH guards):
    #
    #     for _pq in per_query: _s = _pq["order"]; _s.sort()   -> mutated_after=[] element=[]
    #     for _pq in per_query: _pq["order"].sort()            -> mutated_after=[N] element=[N]
    #
    # Every query's page could be canonicalised before `compare()` read it — #14192's
    # "a verdict that cannot fail" — while the direct spelling is caught, so the defect
    # was the missing hop, not the class. Resolved to a FIXPOINT over the element names
    # exactly as `_aliases_of` resolves a chain, and only for statically visible binds
    # (`Assign` / `AnnAssign` / walrus / `for`), which is what a reader writes.
    def _reaches_element(value: ast.AST, names: set[str]) -> bool:
        """`_pq["order"]` / `_pq["order"].legs` — an ACCESS OF an element, never an
        expression that merely MENTIONS one.

        ⛔ A DERIVED COLLECTION IS NOT THE ELEMENT. `stage_table = {name: stats([
        pq["stages"]… for pq in per_query]) for name in STAGE_ORDER}` mentions `pq` all
        over it, and treating it as an element alias reds the artifact-building code the
        pin exists to protect — this guard's first cut already made that exact mistake at
        `tools/read_wave_latency.py:328,332` (recorded in the docstring above). Only a
        `Subscript`/`Attribute` CHAIN rooted at an element name is that element's inner
        object, so only that joins the fixpoint.
        """
        if isinstance(value, ast.Name):
            return value.id in names
        if isinstance(value, (ast.Subscript, ast.Attribute)):
            return _reaches_element(value.value, names)
        return False

    derived = set(element_names)
    changed = True
    while changed:
        changed = False
        for node in ast.walk(run_def):
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                targets: list[ast.AST] = list(node.targets)
                value: ast.AST = node.value
            elif isinstance(node, (ast.AnnAssign, ast.NamedExpr)) and node.value is not None:
                targets, value = [node.target], node.value
            elif isinstance(node, (ast.For, ast.AsyncFor)):
                targets, value = [node.target], node.iter
            else:
                continue
            if not _reaches_element(value, derived):
                continue
            fresh = {b.id for target in targets for b in ast.walk(target)
                     if isinstance(b, ast.Name) and b.id not in derived}
            if fresh:
                derived |= fresh
                defining.add(id(node))
                changed = True
    if not derived:
        return []
    # ⛔ A BOUND MUTATOR OF AN ELEMENT'S INNER LIST IS THAT MUTATOR (#14281 review P1,
    # the shape `_mutated_after` closed for the PAGE, still GREEN here):
    #
    #     for _pq in per_query: _s = _pq["order"].sort; _s()
    #
    # The bind names a FUNCTION, so nothing in the fixpoint above resolves `_s`, and the
    # call is a bare `Name` the receiver arms never saw — the page's own guard carries
    # this arm and this one did not, which left the residual list here contradicting the
    # sibling guard one function above. Conservative in the same direction as the rest:
    # a bound mutator of a row is a mutation of the recorded page.
    bound_mutator: set[str] = set()
    for node in ast.walk(run_def):
        if isinstance(node, ast.Assign):
            _t: list[ast.AST] = list(node.targets)
            _v: ast.AST = node.value
        elif isinstance(node, (ast.AnnAssign, ast.NamedExpr)) and node.value is not None:
            _t, _v = [node.target], node.value
        else:
            continue
        if isinstance(_v, ast.Attribute) and _is_mutator_method(_v.attr) \
                and _reaches_element(_v.value, derived):
            bound_mutator |= {b.id for t in _t for b in ast.walk(t)
                              if isinstance(b, ast.Name)}
    hits: list[int] = []
    for node in ast.walk(run_def):
        if getattr(node, "lineno", -1) <= after_lineno:
            continue
        if id(node) in defining:
            continue  # naming an element (or a slice of it) is not mutating it
        touched = False
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and _is_mutator_method(node.func.attr):
            touched = bool(_names_in(node.func.value) & derived)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id in bound_mutator:
            touched = True
        elif isinstance(node, ast.Subscript) and isinstance(node.ctx, (ast.Store, ast.Del)):
            touched = bool(_names_in(node.value) & derived)
        elif isinstance(node, ast.Assign):
            touched = bool(set().union(*(_names_in(t) for t in node.targets))
                           & element_names)
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            touched = bool(_names_in(node.target) & element_names)
        if touched:
            hits.append(node.lineno)
    return hits


def _is_exactly(node: ast.AST, var: str) -> bool:
    """`var`, or a transparent `list(var)` / `tuple(var)` — nothing derived, nothing else."""
    if isinstance(node, ast.Name):
        return node.id == var
    return (isinstance(node, ast.Call) and getattr(node.func, "id", None) in ("list", "tuple")
            and len(node.args) == 1 and not node.keywords
            and isinstance(node.args[0], ast.Name) and node.args[0].id == var)


def _rebound_after(run_def: ast.AST, var: str, lineno: int) -> list[int]:
    """Line numbers where `var` is rebound after the read — a sorted/emptied page lies.

    ⛔ A TRANSPARENT COPY IS NOT A REBINDING (#14281 review P2, measured). `_is_exactly`
    already accepts `list(var)` / `tuple(var)` as the collection itself where it ITERATES,
    so the same expression as a defensive snapshot — `results = list(results)` after the
    read, which cannot alter anything — must not red either. Before this, it reported
    "a sorted/emptied copy would reach the artifact", which misdiagnoses a no-op; that is
    an unstated false red in a file whose contract is to state its false reds.
    """
    hits: list[int] = []
    for node in ast.walk(run_def):
        if getattr(node, "lineno", -1) <= lineno:
            continue
        if isinstance(node, ast.Assign):
            targets: list[ast.AST] = list(node.targets)
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign, ast.NamedExpr,
                               ast.For, ast.AsyncFor)):
            targets = [node.target]
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            targets = [i.optional_vars for i in node.items if i.optional_vars is not None]
        else:
            continue
        # ⛔ A SUBSCRIPT-ROOTED TARGET IS NOT A REBINDING (#14281 review P3, measured):
        # `results[0]["id"] = "X"` reported "`results` is rebound at line(s) [307] — a
        # sorted/emptied copy would reach the artifact", which names the wrong mechanism
        # for a mutation that `_mutated_after`'s Store arm reports correctly. A `Store`
        # into the collection cannot replace the name, so it is skipped here.
        if any(var in _names_in(t) and not isinstance(t, ast.Subscript) for t in targets):
            if (isinstance(node, ast.Assign) and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Name)
                    and node.targets[0].id == var and _is_exactly(node.value, var)):
                continue  # `var = list(var)` is a transparent snapshot, not a rebinding
            hits.append(node.lineno)
    return hits


def _environ_mutations(tree: ast.AST, parents: dict[int, ast.AST]) -> list[int]:
    """Every line that can change `os.environ`.

    ⛔ The guard is on the RECEIVER, and the receiver is resolved through ALIASES
    (`env = os.environ; env["TORTOISE_FUSED_RERANK"] = "1"` defeated a name-exact receiver
    test — measured), plus the dunder entries `__setitem__` / `__delitem__`.
    `os.environ` handed to an UNKNOWN call (positional or keyword) is red too: handing the
    mapping to a callee this file cannot see into is how it gets changed. ⛔ The callee has
    to be unknown — `dict(os.environ)` and `len(os.environ)` take a COPY or a count and
    cannot write back, and the blanket form reported both as "mutates `os.environ`"
    (#14281 review P2, measured GREEN after the exemption). Residual, stated rather than
    implied: a helper
    that reaches `os.environ` inside its own body is not followed. The scan is over the
    WHOLE FILE, so a module-level helper that mutates `os.environ` IS caught — the hole is
    the statically dead reach, not the file's scope.

    ⛔ AND THE OTHER STATIC SPELLINGS OF THE SAME OBJECT (#14281 review F1, measured):
    `_aliases_of` walks `Assign`/`AnnAssign`/`NamedExpr` only, so an IMPORT binding was
    not a name at all — and `getattr(os, "environ")` is a `Call`, not an `Attribute`.
    Both were GREEN against a genuinely flipped gate:

        from os import environ; environ["TORTOISE_FUSED_RERANK"] = "1"
        from os import environ as env; env["TORTOISE_FUSED_RERANK"] = "1"
        getattr(os, "environ")["TORTOISE_FUSED_RERANK"] = "1"

    `from os import environ` is the IDIOMATIC spelling, so this is not an exotic shape —
    it is the one a reader reaches for. Import bindings are now resolved, and a `getattr`
    whose name argument is the literal `"environ"` is treated as the mapping itself.

    The only exemption is the module-level setup pops of the two API variables — the
    harness removes them BEFORE building the SDK so it measures the embedded graph. The
    exemption is scoped: the same `pop` inside `run()` is a mutation like any other.
    """
    # ⛔ FAIL-CLOSED HERE TOO (#14281, the same polarity fix as the page guard's
    # `_PURE_METHODS`): this was a closed allow-list `{clear, pop, popitem, setdefault,
    # __delitem__, __setitem__, update}`, so `os.environ.__init__({…})` — which
    # RE-INITIALISES the same mapping in place — and `os.environ.__ior__(…)` were GREEN
    # while the `os.environ.update(…)` one word over was RED. An unknown method on the
    # mapping is now a mutation; the module-level setup pops stay exempt through
    # `module_pop` below, which tests the NAME, not the set.
    setup_pops = {"TORTOISE_API_URL", "TORTOISE_API_KEY"}
    # ⛔ THE OTHER STATIC SPELLING IS A SEED, NOT A TERMINAL NAME (#14281 review).
    # `from os import environ` / `... as env` binds the name with NO `Assign` node, so
    # `_aliases_of` cannot discover it — and adding it to `env_alias` AFTER the fixpoint
    # made it a dead end: `_e = environ; _alias = _e; _alias[...] = "1"` and
    # `_pop = environ.pop; _pop(...)` stayed GREEN, while the same shapes written as
    # `os.environ` were RED. Seeding BEFORE the call is what makes the imported spelling
    # as transitive as the attribute one.
    imported_environ: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "os":
            for spec in node.names:
                if spec.name == "environ":
                    imported_environ.add(spec.asname or "environ")

    def _mentions_mapping(value: ast.AST) -> bool:
        """The SEED predicate. It must be as wide as `_is_environ` below, or the seed
        resolves one spelling and the check catches another (#14281): `getattr(os,
        "environ")` was in the CHECK and not in the SEED, so

            _e = getattr(os, "environ"); _e["TORTOISE_FUSED_RERANK"] = "1"

        was GREEN while the inline spelling was RED — the same its-predicate-is-narrower
        shape that made the import a terminal name instead of a seed. Measured: four
        variants (alias + subscript, alias + `pop`, alias + bound mutator) all GREEN
        before this clause, all RED after, and the real tool unchanged (`[]`).
        """
        return any((isinstance(n, ast.Attribute) and n.attr == "environ")
                   or (isinstance(n, ast.Name) and n.id in imported_environ)
                   or (isinstance(n, ast.Call)
                       and getattr(n.func, "id", None) == "getattr"
                       and len(n.args) >= 2
                       and isinstance(n.args[1], ast.Constant)
                       and n.args[1].value == "environ")
                   for n in ast.walk(value))


    # ⛔ To a FIXPOINT, and through CONTAINERS: `_envs = [os.environ]` then
    # `_envs[0][...] = ...`, `_envs = [environ]` after an import, and
    # `_e1 = os.environ; _e2 = _e1; _e3 = _e2` were all measured green against the
    # one-hop, bare-Name form.
    env_alias = _aliases_of(tree, _mentions_mapping, loops=True)
    env_alias |= imported_environ

    def _is_environ(node: ast.AST | None) -> bool:
        if isinstance(node, ast.Attribute):
            return node.attr == "environ"
        if isinstance(node, ast.Name):
            return node.id in env_alias
        if isinstance(node, ast.Subscript):
            # ⛔ `_envs[0]` IS THE MAPPING when `_envs` is a container of it (the seed
            # accepts containers). Without this clause `_envs = [environ];
            # _envs[0][k] = v` was caught only by `rebound` firing on the ALIAS
            # ASSIGNMENT one line above — a false red on a harmless line, carrying a real
            # class by accident (#14281 review P2: narrowing `rebound` exposed it).
            return _is_environ(node.value)
        return (isinstance(node, ast.Call)
                and getattr(node.func, "id", None) == "getattr"
                and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
                and node.args[1].value == "environ")

    def _is_mapping_itself(node: ast.AST | None) -> bool:
        """The mapping SPELLING — `os.environ` / `from os import environ` / `getattr`,
        but never an ALIAS NAME of it (#14281 review F5, measured).

        `rebound` asked `_is_environ`, which resolves aliases, so the innocent

            `_env = os.environ`

        was reported as "the harness mutates `os.environ`" — a *bind* is not a
        replacement of the mapping, and the message is simply wrong. Only a write whose
        TARGET is the mapping itself (or the imported name for it) replaces it.
        """
        return (isinstance(node, ast.Attribute) and node.attr == "environ") \
            or (isinstance(node, ast.Name) and node.id in imported_environ) \
            or (isinstance(node, ast.Call)
                and getattr(node.func, "id", None) == "getattr"
                and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
                and node.args[1].value == "environ")

    def _reaches_mapping(node: ast.AST | None) -> bool:
        """An ALIAS of the mapping, or a `[i]`/`[...]` slot holding it — a write to that
        slot is a write to `os.environ` (#14281 review F4).

        Measured: `_envs = [environ]; _envs[0]["TORTOISE_FUSED_RERANK"] = "1"` was the one
        case in the environ table whose MUTATING line was never in `hits` — the table
        passed on the over-fire at the BIND line instead, so the clause it named was
        uncovered.
        """
        if node is None:
            return False
        if _is_environ(node):
            return True
        if isinstance(node, ast.Subscript):
            return _reaches_mapping(node.value)
        return False

    # ⛔ A BOUND METHOD IS THE SAME POINTER: `_set = os.environ.__setitem__` then
    # `_set(...)`, and `_pop = os.environ.pop` then `_pop(...)`, were both measured green.
    # ⛔ AND THE SEED MUST BE THE CHECK, NOT THE SEED PREDICATE (#14281 review F2,
    # measured). This read `_mentions_mapping(v.value)`, which resolves an `ast.Name` only
    # through `imported_environ` — while the CHECK (`_is_environ`) resolves it through
    # `env_alias`. So a bound mutator of a plain ALIAS was a terminal name again:
    #
    #     for _e in (os.environ,): _p = _e.pop; _p("TORTOISE_FUSED_RERANK")   -> []
    #     _p = os.environ.pop;     _p("TORTOISE_FUSED_RERANK")                -> [1, 2]
    #
    # The identical "its-predicate-is-narrower-than-its-check" shape the docstring above
    # says it fixed for `getattr`, left standing one spelling over. `loops=True` is what
    # resolves the `for`-bound spelling.
    # ⛔ …AND THROUGH A CONTAINER SLOT (#14281 review P2, measured GREEN):
    #     _ops = {"p": os.environ.pop}; _ops["p"]("TORTOISE_FUSED_RERANK")
    # The docstring advertised "alias/container/bound-mutator chains" while the seed
    # matched only a bare `Attribute`, so the COMPOSITION was uncaught — each ingredient
    # alone was. The seed is now container-aware and the call arm resolves a `Subscript`
    # function back to the container.
    def _bound_mutator_expr(v: ast.AST) -> bool:
        if isinstance(v, ast.Attribute):
            return _is_mutator_method(v.attr) and _reaches_mapping(v.value)
        if isinstance(v, ast.Dict):
            return any(e is not None and _bound_mutator_expr(e) for e in v.values)
        if isinstance(v, (ast.List, ast.Tuple, ast.Set)):
            return any(_bound_mutator_expr(e) for e in v.elts)
        return False

    bound = _aliases_of(tree, _bound_mutator_expr, loops=True)

    hits: list[int] = []
    for node in ast.walk(tree):
        subscript = (isinstance(node, ast.Subscript)
                     and isinstance(node.ctx, (ast.Store, ast.Del))
                     and _reaches_mapping(node.value))
        method = (isinstance(node, ast.Call)
                  and _is_mutator_method(getattr(node.func, "attr", None))
                  and _is_environ(getattr(node.func, "value", None)))
        aug = isinstance(node, ast.AugAssign) and _is_environ(node.target)
        bound_call = (isinstance(node, ast.Call) and (
            (isinstance(node.func, ast.Name) and node.func.id in bound)
            or (isinstance(node.func, ast.Subscript)
                and bool(_names_in(node.func.value) & bound))))
        # ⛔ …and so does a setattr/vars() spelling of the same assignment (#14281 review
        # P2, both measured green): `setattr(os, "environ", {…})` and
        # `vars(os)["environ"] = {…}` swap the whole mapping without an `Assign` target.
        setattr_environ = (isinstance(node, ast.Call)
                           and getattr(node.func, "id", None) in ("setattr", "delattr")
                           and len(node.args) >= 2
                           and isinstance(node.args[1], ast.Constant)
                           and node.args[1].value == "environ")
        vars_environ = (isinstance(node, ast.Subscript)
                        and isinstance(node.ctx, (ast.Store, ast.Del))
                        and isinstance(node.slice, ast.Constant)
                        and node.slice.value == "environ"
                        and ((isinstance(node.value, ast.Call)
                              and getattr(node.value.func, "id", None) == "vars")
                             or (isinstance(node.value, ast.Attribute)
                                 and node.value.attr == "__dict__")))
        # ⛔ `os.environ = {…}` REPLACES the mapping: a rebinding, not a write. Only the
        # mapping SPELLING counts — `_env = os.environ` binds a name and replaces nothing
        # (#14281 review F5, measured false red).
        rebound = (isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.Delete))
                   and any(_is_mapping_itself(t) for t in (
                       getattr(node, "targets", None) or [getattr(node, "target", None)])))
        # ⛔ A PURE READ TAKES A COPY, NOT THE MAPPING (#14281 review P2, measured RED):
        # `_snap = dict(os.environ)` is the natural way to record the gate state #14289
        # asks for, and `len(os.environ)` reads it. Same exemption, same reason, as the
        # page guard's `_PURE_READS`: a `dict(...)`/`len(...)` receiver cannot write back.
        _env_callee = (getattr(node.func, "id", None) or getattr(node.func, "attr", None)
                       if isinstance(node, ast.Call) else None)
        handed = (isinstance(node, ast.Call) and _env_callee not in _PURE_READS
                  and any(_reaches_mapping(a)
                          for a in list(node.args) + [k.value for k in node.keywords]))
        if not (subscript or method or aug or bound_call or rebound or handed
                or setattr_environ or vars_environ):
            continue
        module_pop = (isinstance(node, ast.Call)
                      and getattr(node.func, "attr", None) == "pop"
                      and node.args and isinstance(node.args[0], ast.Constant)
                      and node.args[0].value in setup_pops
                      and _owner(node, parents) == "<module>")
        if module_pop:
            continue
        hits.append(node.lineno)
    return hits


def test_handing_an_ALIASED_page_to_a_call_is_red() -> None:
    """The alias widening must not disable the "handed to a call" branch (#14281 F2).

    ⛔ THE DEFECT, measured on the previous form: `_mutated_after` compared each
    argument's name-set to the WHOLE `watched` set for EXACT equality. With no alias,
    `watched == {"results"}` and a bare `results` argument hit; with ONE alias,
    `watched == {"results", "page_alias"}` and every single-name argument missed — so
    the branch was inert for every shape, forever. That means the round-7 alias
    resolution DISABLED the check it was added to serve, and it read green only because
    `tools/read_wave_latency.py` happens to carry no alias: a guard right by accident.

    This pins the intersection form directly (synthetic source, no repo state), so a
    later widening of `watched` cannot silently re-break it.
    """
    src = (
        "def run(sdk):\n"
        "    results = sdk._tortoise_fts_query(query='q', limit=10)\n"
        "    page_alias = results\n"
        "    random.shuffle(results)\n"
    )
    run_def = ast.parse(src).body[0]
    read = next(n for n in ast.walk(run_def)
                if isinstance(n, ast.Call)
                and getattr(n.func, "attr", None) == "_tortoise_fts_query")
    shuffle = next(n for n in ast.walk(run_def)
                   if isinstance(n, ast.Call)
                   and getattr(n.func, "attr", None) == "shuffle")
    assert _aliases_of(run_def, lambda v: isinstance(v, ast.Name) and v.id == "results"), (
        "the fixture no longer carries an alias, so it cannot exercise the branch")
    hits = _mutated_after(run_def, "results", read.lineno, artifact_append=None)
    # ⛔ The shuffle LINE, not merely "something was found": the receiver half and the
    # `per_query` guard both sit in the same loop, so a bare `assert hits` is satisfied by
    # a shape that does not involve aliases at all (measured — the first cut of this test
    # passed against the pre-fix set-equality form and was therefore vacuous).
    assert shuffle.lineno in hits, (
        f"`page_alias = results` + `random.shuffle(results)` is GREEN (hits={hits}) — the "
        "'handed to a call' branch is comparing each argument against the whole watched "
        "set instead of intersecting with it, so one alias disables it"
    )


def test_a_TWO_HOP_or_WALRUS_alias_still_reaches_the_sort() -> None:
    """Alias resolution is TRANSITIVE and WALRUS-AWARE (#14281 review P2).

    ⛔ MEASURED on the one-hop, `=`-only form: both of these read GREEN (7 passed) with
    the page genuinely permuted, so `compare()` reported `rank_identical: True` on a page
    the pin was supposed to prove untouched:

        a1 = results; a2 = a1; a2.sort()
        (r2 := results); r2.sort()

    The one-hop walk widened `watched` to `{results, a1}` and then looked for `a2`, which
    matched neither the receiver half nor the "handed" half; the walrus target never
    entered `binds` at all. Both names are statically visible, so neither is the pin's
    declared "computed at runtime" residual.
    """
    for label, body in (
        ("two-hop", "    a1 = results\n    a2 = a1\n    a2.sort()\n"),
        ("walrus", "    (r2 := results)\n    r2.sort()\n"),
    ):
        src = ("def run(sdk):\n"
               "    results = sdk._tortoise_fts_query(query='q', limit=10)\n" + body)
        run_def = ast.parse(src).body[0]
        read = next(n for n in ast.walk(run_def)
                    if isinstance(n, ast.Call)
                    and getattr(n.func, "attr", None) == "_tortoise_fts_query")
        sort = next(n for n in ast.walk(run_def)
                    if isinstance(n, ast.Call)
                    and getattr(n.func, "attr", None) == "sort")
        aliases = _aliases_of(run_def, lambda v: isinstance(v, ast.Name) and v.id == "results")
        assert aliases, f"{label}: the fixture carries no alias — it cannot exercise the walk"
        hits = _mutated_after(run_def, "results", read.lineno, artifact_append=None)
        assert sort.lineno in hits, (
            f"{label} alias is GREEN (aliases={sorted(aliases)}, hits={hits}) — alias "
            "resolution is not transitive / walrus-aware, so a sorted page reaches the "
            "artifact while the pin reads the measured page")


def test_an_UNPACKED_alias_still_reaches_the_sort() -> None:
    """An UNPACK target is an alias too — `(page,) = (results,)` (#14281 finding 2).

    ⛔ MEASURED GREEN on the named-target-only bind (`len(targets) == 1 and
    isinstance(target, ast.Name)`): `aliases=[]`, so `page.sort()` permuted the recorded
    page while the receiver and "handed" halves both saw nothing. Same false-PASS class
    as the two-hop chain beside it; statically visible, so not the declared residual.
    """
    src = ("def run(sdk):\n"
           "    results = sdk._tortoise_fts_query(query='q', limit=10)\n"
           "    (page,) = (results,)\n"
           "    page.sort()\n")
    run_def = ast.parse(src).body[0]
    read = next(n for n in ast.walk(run_def)
                if isinstance(n, ast.Call)
                and getattr(n.func, "attr", None) == "_tortoise_fts_query")
    sort = next(n for n in ast.walk(run_def)
                if isinstance(n, ast.Call) and getattr(n.func, "attr", None) == "sort")
    aliases = _aliases_of(run_def, lambda v: isinstance(v, ast.Name) and v.id == "results")
    assert "page" in aliases, (
        f"the unpack target is not resolved as an alias (aliases={sorted(aliases)}) — "
        "`(page,) = (results,)` binds the same collection and a `page.sort()` reaches "
        "the artifact invisibly")
    hits = _mutated_after(run_def, "results", read.lineno, artifact_append=None)
    assert sort.lineno in hits, (
        f"an UNPACKED alias is GREEN (aliases={sorted(aliases)}, hits={hits}) — the "
        "page is permuted in place before it is recorded")


def _mutating_line(src: str, marker: str) -> int:
    """The 1-based line of the ONE line carrying `marker` (asserts it is unique)."""
    hits = [i for i, line in enumerate(src.splitlines(), start=1) if marker in line]
    assert len(hits) == 1, f"{marker!r} must appear on exactly one line, got {hits}"
    return hits[0]


def test_a_CONTAINER_SLOT_holding_an_element_is_that_element() -> None:
    """A container (or an object) that HOLDS the collection IS the collection.

    ⛔ ALL FIVE SHAPES MEASURED GREEN at `06bdae1a7926` (#14281 review P1), each through
    the full pin (16 passed) with `compare()` reporting `rank_identical: True` on a
    genuinely permuted pair: the container is bound to an EMPTY literal, so
    `_aliases_of` resolves nothing (there is no name to mention yet) and the ONLY bind
    that makes the collection reachable is a SLOT STORE — a target carrying no `Name`,
    which no binding-form arm looked at.

    The arm is the aggregate/field propagation rule every production taint analyzer
    carries (a container is reached through its SLOTS), so it is one rule for every
    container spelling, not a clause per shape: `Subscript` and `Attribute` are the two
    target shapes that are slots. The scalar control below is what keeps it from being a
    blanket red — `_tally["n"] = len(results)` is a COPY of an int.
    """
    for label, src, marker in (
        ("dict slot, augmented element rewrite",
         "def run(sdk):\n"
         "    results = sdk._tortoise_fts_query(query='q', limit=10)\n"
         "    _slots = {}\n"
         "    _slots['k'] = results[0]\n"
         "    _slots['k'] |= {'id': 'REDACTED'}\n",
         "_slots['k'] |= "),
        ("list slot, augmented element rewrite",
         "def run(sdk):\n"
         "    results = sdk._tortoise_fts_query(query='q', limit=10)\n"
         "    _box = [None]\n"
         "    _box[0] = results[0]\n"
         "    _box[0] |= {'id': 'REDACTED'}\n",
         "_box[0] |= "),
        ("dict slot, update() on the element",
         "def run(sdk):\n"
         "    results = sdk._tortoise_fts_query(query='q', limit=10)\n"
         "    _d = {}\n"
         "    _d['k'] = results[0]\n"
         "    _d['k'].update({'id': 'REDACTED'})\n",
         "_d['k'].update("),
        ("dict slot, sort() on the element's inner list",
         "def run(sdk):\n"
         "    results = sdk._tortoise_fts_query(query='q', limit=10)\n"
         "    _d = {}\n"
         "    _d['k'] = results[0]\n"
         "    _d['k']['order'].sort()\n",
         "_d['k']['order'].sort()"),
        ("object attribute slot",
         "def run(sdk):\n"
         "    results = sdk._tortoise_fts_query(query='q', limit=10)\n"
         "    self._page = results[0]\n"
         "    self._page |= {'id': 'REDACTED'}\n",
         "self._page |= "),
    ):
        run_def = ast.parse(src).body[0]
        read = next(n for n in ast.walk(run_def)
                    if isinstance(n, ast.Call)
                    and getattr(n.func, "attr", None) == "_tortoise_fts_query")
        line = _mutating_line(src, marker)
        hits = _mutated_after(run_def, "results", read.lineno, artifact_append=None)
        assert line in hits, (
            f"[{label}] a container that HOLDS a measured element is GREEN "
            f"(hits={hits}, wanted {line}) — the page can be permuted through it while "
            "`compare()` still calls the pair rank-identical")

    # ⛔ THE SCALAR CONTROL: a container that holds a COPY of nothing is not tainted.
    # `len(results)` cannot carry the page, and this assertion is what stops the arm
    # above from redding every dict that merely mentions the read.
    src = ("def run(sdk):\n"
           "    results = sdk._tortoise_fts_query(query='q', limit=10)\n"
           "    _tally = {}\n"
           "    _tally['n'] = len(results)\n"
           "    _tally['n'] += 1\n")
    run_def = ast.parse(src).body[0]
    read = next(n for n in ast.walk(run_def)
                if isinstance(n, ast.Call)
                and getattr(n.func, "attr", None) == "_tortoise_fts_query")
    assert not _mutated_after(run_def, "results", read.lineno, artifact_append=None), (
        "`_tally['n'] = len(results)` counts the page, it does not hold it — a counter "
        "incremented afterwards is not a mutation of the measured page (false red)")


def test_an_UNLISTED_METHOD_on_the_page_is_a_MUTATION_not_a_pass() -> None:
    """The mutator test is FAIL-CLOSED: an unknown method name is a mutation.

    ⛔ FOUR SHAPES MEASURED GREEN at `3e6e5e61de1b` through the FULL pin (21 passed), each
    with the page genuinely replaced, and `compare()` reporting `rank_identical: True`:

        results.__init__(sorted(results, key=_result_id, reverse=True))   -> GREEN
        results.__delitem__(slice(None))                                 -> GREEN
        results.__iadd__([{"id": "REDACTED"}])                           -> GREEN
        results[0].__init__({"id": "REDACTED"})                          -> GREEN

    The test was `name in _MUTATORS` — a closed ALLOW-list — so every mutator it did not
    name was GREEN. `list.__init__` RE-INITIALISES the same list object, so the artifact
    records the canonicalised page and the harness erases exactly the difference the
    verdict exists to see. ⛔ This is NOT a binding-form indirection (the class the file
    declares it cannot enumerate): it is a name-list POLARITY bug, so the default is now
    inverted — `_PURE_METHODS` is the read side, and everything else is a mutation.

    The measured REDs below are asserted at the MUTATING line (a bind-line over-fire is
    what let the environ table stay green with half its clauses deleted), and the
    controls are what stop the inverted default from being a blanket red.
    """
    cases = {
        "list.__init__ replaces the page in place": (
            "    results.__init__(sorted(results, key=_result_id, reverse=True))\n",
            "results.__init__("),
        "list.__delitem__ empties the page": (
            "    results.__delitem__(slice(None))\n",
            "results.__delitem__("),
        "list.__iadd__ appends to the page in place": (
            "    results.__iadd__([{'id': 'REDACTED'}])\n",
            "results.__iadd__("),
        "dict.__init__ on the recorded row": (
            "    results[0].__init__({'id': 'REDACTED'})\n",
            "results[0].__init__("),
    }
    for label, (line_src, marker) in cases.items():
        src = ("def run(sdk):\n"
               "    results = sdk._tortoise_fts_query(query='q', limit=10)\n"
               + line_src)
        run_def = ast.parse(src).body[0]
        read = next(n for n in ast.walk(run_def)
                    if isinstance(n, ast.Call)
                    and getattr(n.func, "attr", None) == "_tortoise_fts_query")
        line = _mutating_line(src, marker)
        hits = _mutated_after(run_def, "results", read.lineno, artifact_append=None)
        assert line in hits, (
            f"[{label}] an UNLISTED method on the measured page is GREEN (hits={hits}, "
            f"wanted {line}) — `__init__`/`__delitem__`/`__iadd__` mutate the SAME object "
            "the artifact records, so `compare()` calls a canonicalised page "
            "`rank_identical: True`")

    # ⛔ THE CONTROLS, so the inverted default is not a blanket red: the READ side of the
    # collection protocol must stay GREEN, or the pin reds the tool it exists to protect.
    for label, line_src in {
        "count() observes the page": "    _n = results.count('x')\n",
        "index() observes the page": "    _i = results.index('x')\n",
        "copy() takes a copy": "    _c = results.copy()\n",
        "an element's get() observes it": "    _v = results[0].get('id')\n",
        "len() counts the page": "    _l = len(results)\n",
    }.items():
        src = ("def run(sdk):\n"
               "    results = sdk._tortoise_fts_query(query='q', limit=10)\n"
               + line_src)
        run_def = ast.parse(src).body[0]
        read = next(n for n in ast.walk(run_def)
                    if isinstance(n, ast.Call)
                    and getattr(n.func, "attr", None) == "_tortoise_fts_query")
        assert not _mutated_after(run_def, "results", read.lineno,
                                  artifact_append=None), (
            f"[{label}] is a pure READ of the page, not a mutation — the fail-closed "
            "default must name its read side, or every diagnostic in `run()` reds")

    # The RECORDED ROWS carry the same closed list, one level down.
    src = ("def run():\n"
           "    per_query = []\n"
           "    for _pq in per_query:\n"
           "        _pq['order'].__init__([1, 2, 3])\n")
    run_def = ast.parse(src).body[0]
    line = _mutating_line(src, "_pq['order'].__init__(")
    assert line in _per_query_element_mutations(run_def, 0), (
        "`_pq['order'].__init__([…])` RE-INITIALISES the recorded page in place "
        "— the recorded-rows guard carried the same closed mutator list, so it was GREEN "
        "while the `…['order'].sort()` one word away was RED")

    # ⛔ AND THE ENVIRON ARM, which had its own closed list (`{clear, pop, __setitem__, …}`)
    # so `os.environ.__init__({…})` — `dict.__init__`, in place — was GREEN while
    # `os.environ.update({…})` was RED. The module-level setup pops stay exempt.
    src = ("def run():\n"
           "    os.environ.__init__({'TORTOISE_FUSED_RERANK': '1'})\n")
    tree = ast.parse(src)
    parents: dict[int, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[id(child)] = node
    line = _mutating_line(src, "os.environ.__init__(")
    hits = _environ_mutations(tree, parents)
    assert line in hits, (
        f"`os.environ.__init__({{…}})` replaces the gate mapping in place (hits={hits}) "
        "— the environ guard's closed mutator list did not name it")

    # …and the exempt module-level setup pop is still exempt (it is not part of `run()`).
    setup = ("import os\n"
             "os.environ.pop('TORTOISE_API_URL', None)\n"
             "os.environ.pop('TORTOISE_API_KEY', None)\n")
    tree = ast.parse(setup)
    parents = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[id(child)] = node
    assert _environ_mutations(tree, parents) == [], (
        "the harness's own module-level API-variable pops are the ONE documented "
        "exemption — redding them would red the tool this pin protects")


def test_a_CHAINED_UNPACK_binds_EVERY_target() -> None:
    """`(_a,) = (_b,) = results` binds the SAME collection to BOTH names.

    ⛔ MEASURED GREEN for the SECOND target at `06bdae1a7926` (#14281 review P2): the
    page guard tainted only `_unpacked[0]`, so `_a |= {"id": …}` was RED and the
    identical rewrite through `_b` was GREEN — the verdict depended on target ORDER.
    ⛔ AND THE RECORDED-ROWS GUARD MISSED THE WHOLE STATEMENT: it accepted only
    `len(node.targets) == 1`, so `(_a,) = (_b,) = per_query; _b["order"].sort()`
    canonicalised every page invisibly (measured GREEN at `f44e79ed36ee`).
    """
    # The measured page: BOTH targets of the chain are the collection.
    src = ("def run(sdk):\n"
           "    results = sdk._tortoise_fts_query(query='q', limit=10)\n"
           "    (_a,) = (_b,) = results\n"
           "    _b |= {'id': 'REDACTED'}\n")
    run_def = ast.parse(src).body[0]
    read = next(n for n in ast.walk(run_def)
                if isinstance(n, ast.Call)
                and getattr(n.func, "attr", None) == "_tortoise_fts_query")
    line = _mutating_line(src, "_b |= ")
    hits = _mutated_after(run_def, "results", read.lineno, artifact_append=None)
    assert line in hits, (
        f"the SECOND target of a chained unpack is GREEN (hits={hits}) — the page is "
        "rewritten through a name the guard never bound, so `compare()` sees the "
        "REWRITTEN ids and calls a permuted pair rank-identical")

    # The recorded rows: the same chain, one level down.
    src = ("def run(sdk):\n"
           "    per_query = []\n"
           "    (_a,) = (_b,) = per_query\n"
           "    _b['order'].sort()\n")
    run_def = ast.parse(src).body[0]
    line = _mutating_line(src, "_b['order'].sort()")
    assert line in _per_query_element_mutations(run_def, 0), (
        "`(_a,) = (_b,) = per_query` names the element through BOTH targets, and the "
        "recorded-rows guard walked only a single-target `Assign` — a sorted recorded "
        "page is exactly what `compare()` reads")

    # ⛔ A DERIVED COLLECTION IS STILL NOT AN ELEMENT: `_rows = [x for x in per_query]`
    # must not make `_rows[0]["order"].sort()` a row mutation (the guard's own
    # false-red history at `tools/read_wave_latency.py:328,332`).
    src = ("def run(sdk):\n"
           "    per_query = []\n"
           "    _rows = [dict(x) for x in per_query]\n"
           "    _rows[0]['order'].sort()\n")
    run_def = ast.parse(src).body[0]
    assert not _per_query_element_mutations(run_def, 0), (
        "a list COMPREHENSION over `per_query` builds new dicts — only the shallow copies "
        "it holds are touched, and redding it would red the artifact-building code this "
        "pin exists to protect (measured false red, #14281 review F5)")


def test_a_CONTAINER_that_only_HOLDS_the_page_is_NOT_the_page() -> None:
    """The slot rule is precise: HOLDING the page is not BEING the page.

    ⛔ The first form of the container-slot arm folded the container into `muts`, which
    reds every one of these INNOCENT lines with "line(s) [N] mutate a collection AFTER the
    measured read" (measured at `a962da9cacc3`):

        _box[0] = results[0]; _box[0] = None          a SLOT REPLACEMENT is a bind
        _box[0] = results[0]; _box.append(None)       a container operation
        _d["k"] = results[0]; _d.clear()              a container operation
        _d["k"] = results[0]; _c = _d.clear; _c()     a BOUND container mutator
        _d["k"] = results[0]; print(_d)               a pure read
        (_a,) = (_b,) = results; print(_a, _b)        an unpack bind, no mutation

    None of them can reorder the page, and the pin's own contract is to state its false
    reds rather than add them — three review rounds were spent removing exactly this
    class. So the container goes in a SECOND set (`holders`) and `_tainted()` lets it
    through only where an expression REACHES THROUGH a slot; the RED cases below are the
    other half of that same distinction, so the precision cannot be bought by going blind.
    """
    def _hits(src: str) -> list[int]:
        run_def = ast.parse(src).body[0]
        read = next(n for n in ast.walk(run_def)
                    if isinstance(n, ast.Call)
                    and getattr(n.func, "attr", None) == "_tortoise_fts_query")
        return _mutated_after(run_def, "results", read.lineno, artifact_append=None)

    def _src(body: str) -> str:
        return ("def run(sdk):\n"
                "    results = sdk._tortoise_fts_query(query='q', limit=10)\n" + body)

    # ⛔ MUST STAY GREEN: the bind line, and every container-only operation.
    for label, body in {
        "slot replacement is a bind": (
            "    _box = [None]\n    _box[0] = results[0]\n    _box[0] = None\n"),
        "container append": (
            "    _box = [None]\n    _box[0] = results[0]\n    _box.append(None)\n"),
        "container clear": (
            "    _d = {}\n    _d['k'] = results[0]\n    _d.clear()\n"),
        "bound CONTAINER mutator": (
            "    _d = {}\n    _d['k'] = results[0]\n    _c = _d.clear\n    _c()\n"),
        "pure read of the container": (
            "    _d = {}\n    _d['k'] = results[0]\n    print(_d)\n"),
        "unpack bind with no mutation": (
            "    (_a,) = (_b,) = results\n    print(_a, _b)\n"),
    }.items():
        hits = _hits(_src(body))
        assert not hits, (
            f"[{label}] FALSE RED (hits={hits}) — a container that holds the page is not "
            "the page, and replacing a slot (or appending/clearing the container) mutates "
            "nothing the artifact reads; reding it costs the next lane an edit to a "
            "2 400-line pin for an innocent one-liner and misdiagnoses itself")

    # ⛔ AND MUST STILL BE RED: the same container, reached THROUGH the slot.
    for label, body, marker in (
        ("held element handed to a call",
         "    _d = {}\n    _d['k'] = results[0]\n    random.shuffle(_d['k'])\n",
         "random.shuffle(_d['k'])"),
        ("bound mutator of the held element",
         "    _d = {}\n    _d['k'] = results[0]\n    _s = _d['k'].sort\n    _s()\n",
         "    _s()"),
        ("method ON the held element",
         "    _box = [None]\n    _box[0] = results[0]\n    _box[0].sort()\n",
         "_box[0].sort()"),
        ("write through the slot",
         "    _d = {}\n    _d['k'] = results[0]\n    _d['k']['id'] = 'REDACTED'\n",
         "_d['k']['id'] = "),
        # ⛔ THE SAME SLOT, REACHED THROUGH A SECOND NAME FOR THE CONTAINER (#14281 review
        # P1's family, measured GREEN at `6fe2a1f5c83`): the slot store put the element in
        # `_d`, and `_t` IS `_d` — exactly as `a1 = results` is a second name for the page.
        # Without the hop the carrier arm covered only the name the store mentioned.
        ("held element, container ALIASED first",
         "    _d = {}\n    _d['k'] = results[0]\n    _t = _d\n"
         "    _t['k'] |= {'id': 'REDACTED'}\n",
         "_t['k'] |= "),
        ("held element, container ALIASED then updated",
         "    _d = {}\n    _d['k'] = results[0]\n    _t = _d\n"
         "    _t['k'].update({'id': 'REDACTED'})\n",
         "_t['k'].update("),
        ("three-deep chained unpack, last target",
         "    (_a,) = (_b,) = (_c,) = results\n    _c |= {'id': 'REDACTED'}\n",
         "_c |= "),
        ("chained unpack, first target",
         "    (_a,) = (_b,) = results\n    _a |= {'id': 'REDACTED'}\n",
         "_a |= "),
    ):
        src = _src(body)
        line = _mutating_line(src, marker)
        hits = _hits(src)
        assert line in hits, (
            f"[{label}] GREEN (hits={hits}, wanted {line}) — reaching THROUGH the slot is "
            "the page being mutated, so tightening the bind controls on the other side of "
            "this test must not blind the arm")


def test_a_BOUND_MUTATOR_of_a_recorded_row_is_red() -> None:
    """`_s = _pq["order"].sort; _s()` is a mutation of the RECORDED page.

    ⛔ MEASURED GREEN at `f44e79ed36ee` and DECLARED as a residual in this guard's own
    docstring, while the sibling `_mutated_after` already carried the arm for the measured
    page — the residual contradicted the guard one function above it. The bind names a
    FUNCTION, so no alias fixpoint resolves it and the call is a bare `Name`.
    """
    src = ("def run(sdk):\n"
           "    per_query = []\n"
           "    for _pq in per_query:\n"
           "        _s = _pq['order'].sort\n"
           "        _s()\n")
    run_def = ast.parse(src).body[0]
    line = _mutating_line(src, "_s()")
    hits = _per_query_element_mutations(run_def, 0)
    assert line in hits, (
        f"a bound mutator of a recorded row is GREEN (hits={hits}) — the recorded page is "
        "sorted through a name bound to its `.sort`, which `compare()` reads as the order")


def test_a_BOUND_MUTATOR_in_an_OBJECT_SLOT_is_red() -> None:
    """`_b.s = results.sort; _b.s()` — the OBJECT-slot spelling of the dict-slot arm.

    ⛔ MEASURED GREEN at `9762589e` (#14326 round 8) while the DICT-slot spelling of the same
    thing red (`_d = {"s": results.sort}; _d["s"]()` -> `hits=[5]`): the bound-mutator seed
    binds only NAME targets, so an ATTRIBUTE store never reached `bound_mutator`, and a
    `_PURE_METHODS` name rebound to a mutator (`_x.count = results.sort`) is allow-listed by
    NAME with its VALUE never read. Either shape permutes the page in place, after which the
    artifact records the canonicalised order and `compare()` reports `rank_identical: True`
    over a genuinely permuted pair — #14192's false PASS, through the guard built to stop it.
    """
    for label, body in (
        ("object slot", "    _b.s = results.sort\n    _b.s()\n"),
        # ⛔ THE ANNOTATED SPELLING (#14326 round 9, MEASURED: deleting the `AnnAssign` arm
        # leaves the suite green while this shape goes `hits=[5]` -> `[]`, i.e. an in-place
        # page sort through an annotated slot reads as a pass). The rows above all spell
        # `Assign`, so the arm was load-bearing and unpinned.
        ("object slot, ANNOTATED", "    _b.s: object = results.sort\n    _b.s()\n"),
        ("rebound pure method name", "    _x.count = results.sort\n    _x.count()\n"),
        ("object slot, keyword call", '    _b.s = results.sort\n    _b.s(key=_r.get("id"))\n'),
        # ⛔ A STORE INTO A SLOT (#14326 round 9, MEASURED at `88859180`: `hits=[]` while the
        # LITERAL spelling of the same thing reds). `_sl["s"] = results.sort` reaches neither
        # the name-target seed nor the `Attribute` targets above, so the container never became
        # a mutator holder; the second hop through a literal dict was green for the same reason.
        ("slot STORE then call", '    _sl = {}\n    _sl["s"] = results.sort\n    _sl["s"](key=str)\n'),
        ("second hop through a literal dict",
         '    _d = {"s": results.sort}\n    _b.s = _d["s"]\n    _b.s(key=str)\n'),
    ):
        assert _mutations_of(body), (
            f"[{label}] a bound mutator stored in an OBJECT SLOT is GREEN (hits=[]) — the "
            "page is sorted through a slot nothing resolves, and `compare()` then reports "
            "`rank_identical: True` over a permuted pair")
    # ⛔ THE OTHER POLARITY: a slot is not per se a mutation. Grading it as one would red
    # every helper assigned to an object, so these must stay GREEN.
    for label, body in (
        ("pure callee", "    _b.label = len\n    _b.label()\n"),
        ("a read method of the page", "    _b.get = results.get\n    _b.get('x')\n"),
        # ⛔ THE SLOT **KEY** IS COMPARED, NOT ONLY THE CONTAINER (#14326 review P2,
        # MEASURED at `36a6451d4e`: `hits=[6]`): a mutator stored in ONE slot of a container
        # said nothing about what the OTHER slots hold, and the arm used to red any call
        # through any of them.
        ("a DIFFERENT slot of a mutator-holding container",
         '    _sl = {}\n    _sl["s"] = results.sort\n    _sl["label"]()\n'),
    ):
        assert not _mutations_of(body), (
            f"[{label}] an OBJECT SLOT holding a NON-mutator must stay green — over-collecting "
            "the slot would red an inert edit")
    # ⛔ AND THE KEYED ARM STAYS FAIL-CLOSED where the key cannot be compared: a store with
    # a COMPUTED key says nothing about which slot holds the mutator, so every call through
    # that container reds.
    assert _mutations_of('    _sl = {}\n    _sl[_k] = results.sort\n    _sl["label"]()\n'), (
        "a mutator stored at a COMPUTED key must red EVERY call through the container — the "
        "key cannot be compared, and reading that as 'a different slot' is a fail-open")


def test_a_COPY_of_the_page_is_NOT_an_alias() -> None:
    """`_copy = list(results); _copy.sort()` mutates a NEW object, so it must stay GREEN.

    ⛔ MEASURED RED at `88859180` (#14326 round 9): the alias mention rule counts any page
    name inside the bound value, so `list(results)` made `_copy` an alias of `results` and
    sorting it red the pin — while this file's own docstring declares exactly these shapes
    "correctly exempt, and a copy cannot mutate what it copied". A false red on the natural
    "collect the rows" edit is the direction that makes the next author edit the PIN instead
    of the tool. `_builds_new_container` exempts the container BUILDERS and comprehensions —
    and nothing else, because the exemption is on the CONTAINER, not on its elements: the
    fail-closed half is asserted below (#14326 review P0).
    """
    for label, body in (
        ("list() copy", "    _copy = list(results)\n    _copy.sort()\n"),
        ("sorted()", "    _copy = sorted(results, key=str)\n    _copy.sort()\n"),
        ("dict() copy of a row", "    _copy = dict(results[0])\n    _copy.update({'x': 1})\n"),
        ("comprehension over the page",
         "    _ids = [_result_id(r) for r in results]\n    _ids.sort()\n"),
        # ⛔ THE SHAPE-BASED EXEMPTION LEFT THREE INERT EDITS RED (#14326 round 12, MEASURED on
        # the REAL tool: each `1 failed` while `list(results)` passed). `_builds_new_container`
        # treated a List display as "holds the page" — true for `[results]`, FALSE for
        # `[*results]`, which builds a new list over the same element objects. A list `+`/`*`
        # always allocates, so neither can alias its operand.
        ("starred spread", "    _copy = [*results]\n    _copy.sort(key=str)\n"),
        ("repetition", "    _copy = results * 1\n    _copy.sort(key=str)\n"),
        ("copy concatenated with an empty list",
         "    _copy = list(results) + []\n    _copy.sort(key=str)\n"),
    ):
        assert not _mutations_of(body), (
            f"[{label}] a COPY of the page is being graded as a mutation of the page — the "
            "value BUILDS A NEW OBJECT, so nothing the artifact records can have changed")
    # ⛔ AND THE OTHER POLARITY, so the exemption cannot swallow a real alias: a LITERAL
    # container HOLDS the page (`_box = [results]`), and a direct alias IS the page.
    for label, body in (
        ("literal container holding the page", "    _box = [results]\n    _box.sort()\n"),
        ("direct alias", "    _a = results\n    _a.sort()\n"),
    ):
        assert _mutations_of(body), (
            f"[{label}] must stay RED — the copy exemption must not widen into 'nothing is an "
            "alias'")
    # ⛔ AND THIS PURITY TEST IS BY NAME, SO THE NAME CAN BE REBOUND (#14326 round 12, P1,
    # MEASURED at `c380075b4`: `list = lambda _x: results; _copy = list(1); _copy.sort()` was
    # GREEN — a real in-place permutation of the page). `_is_read_ref` and `_indirect_reads`
    # both consult `_rebound_callee_names` (rounds 6-7 exist for exactly this); the copy
    # exemption did not, so it was the one route by which a shadowed reader stayed "pure".
    for label, body in (
        ("shadowed `list`",
         "    list = lambda _x: results\n    _copy = list(1)\n    _copy.sort()\n"),
        ("shadowed `sorted`",
         "    sorted = lambda _x, **k: results\n    _copy = sorted(1)\n    _copy.sort()\n"),
    ):
        assert _mutations_of(body), (
            f"[{label}] a REBOUND copy callee must not license the exemption — it returns the "
            "page, so sorting the result permutes what the artifact records")
    # ⛔ A BIND OF THE NAME `copy` IS NOT A REBIND OF `results.copy()` (#14326 review at
    # `a29f3cd8`, item 3 — MEASURED false red at `80d2ab52`: `hits=[6]` on the first body
    # below, whose sort is INERT). `rebound` is a set of NAMES, so consulting it
    # unconditionally dropped `copy` from `_SHALLOW_COPY_CALLEES`; an ATTRIBUTE callee
    # cannot be shadowed by a bind of the bare name, and the false red is the direction this
    # file spends rows deleting.
    for label, body in (
        ("a local bind of the NAME `copy`",
         "    copy = results[0]\n    _c = results.copy()\n    _c.sort(key=str)\n"),
        ("a `def copy` shadowing the name",
         "    def copy(_d):\n        return dict(_d)\n"
         "    _c = results.copy()\n    _c.sort(key=str)\n"),
    ):
        assert not _mutations_of(body), (
            f"[{label}] a bind of the bare name `copy` cannot change what `results.copy` "
            "resolves to — grading it as a rebind reds an INERT sort")
    # ⛔ AND A MODULE-LEVEL REBIND IS THE SAME REBIND (#14326 review at `a29f3cd8`, item 2 —
    # MEASURED `hits=[]` at `80d2ab52` on a genuinely permuted page): all five `rebound=`
    # threads were built from `run_def`, so a bind OUTSIDE `run()` was invisible — the same
    # exemption-by-name fail-open, one SCOPE out. The shipped pin now passes the module tree;
    # `run_def` alone cannot see the bind at all, which is why this row is built here.
    module = ast.parse(
        "list = lambda _x: results\n"
        "def run(sdk, queries):\n"
        "    sdk._tortoise_fts_query(query=queries[0]['query'], limit=10)\n"
        "    results = sdk._tortoise_fts_query(query=queries[0]['query'], limit=10, "
        "leg_trace=None)\n"
        "    _c = list(results)\n"
        "    _c.sort()\n"
        "    return results\n")
    module_run = module.body[1]
    module_read = max((n for n in ast.walk(module_run) if isinstance(n, ast.Call)
                       and getattr(n.func, "attr", None) == "_tortoise_fts_query"),
                      key=lambda n: n.lineno)
    assert _mutated_after(module_run, "results", module_read.lineno,
                          artifact_append=None, tree=module), (
        "a MODULE-LEVEL rebind of the copy callee must not license the exemption — the name "
        "bind sits outside `run()`, so a `run_def`-scoped resolution cannot see it")
    # ⛔ `rows` IS LOAD-BEARING AND IT HAD NO ROW (#14326 review at `a29f3cd8`, item 4 —
    # MEASURED: removing `rebound=` at that site reopened the fail-open while the suite
    # stayed green). The recorded ROW LIST is as mutable as the page, so this row reds on
    # that deletion; the three other `rebound=` sites the review named as inert are left in
    # place with their own rows, because a thread nothing can red is not a guard.
    assert _mutations_of(
        '    list = lambda _x: per_query\n    _r = list(1)\n'
        '    _r[-1]["order"].sort()\n'), (
        "a REBOUND copy callee over the RECORDED ROWS must red — `_r = list(1)` returns "
        "`per_query` itself, so the row sort permutes what the artifact records")
    # ⛔ THE COPY'S **ELEMENTS** ARE THE PAGE'S OWN OBJECTS (#14326 review P0, MEASURED at
    # `36a6451d4e`): every shape above is a SHALLOW copy, so its slots hold the SAME row
    # dicts the artifact records. The exemption used to cover the whole BIND, so
    #
    #     _c = list(results); _c[0]["id"] = "X"
    #
    # measured `hits=[]` — the pin reported PASS while the recorded `order` carried "X" —
    # and reproduced end-to-end on the shipped tool (inserted after the measured read, the
    # pin stayed green). The rows below are the fail-closed half: the CONTAINER is new, the
    # ELEMENTS are not.
    for label, body in (
        ("list() copy, element write",
         '    _c = list(results)\n    _c[0]["id"] = "X"\n'),
        ("sorted() copy, element write",
         '    _c = sorted(results, key=str)\n    _c[0]["id"] = "X"\n'),
        ("comprehension copy, element write",
         '    _c = [r for r in results]\n    _c[0]["id"] = "X"\n'),
        ("copy, a row's own list sorted",
         '    _c = list(results)\n    _c[0]["order"].sort()\n'),
        ("copy, element write through a SECOND name for it",
         '    _c = list(results)\n    _d = _c\n    _d[0]["id"] = "X"\n'),
        ("copy, element write through ITERATION of it",
         '    _c = list(results)\n    for _e in _c:\n        _e["id"] = "X"\n'),
        # ⛔ `min`/`max` ARE NOT COPIES AT ALL — they return an ELEMENT of the page, and the
        # old exemption (`callee in _PURE_READS`, which carries both) treated one as a new
        # container, so these two measured GREEN.
        ("min() returns an ELEMENT of the page",
         '    _r = min(results, key=str)\n    _r["id"] = "X"\n'),
        ("max() returns an ELEMENT of the page",
         '    _r = max(results, key=str)\n    _r["id"] = "X"\n'),
    ):
        assert _mutations_of(body), (
            f"[{label}] GREEN (hits=[]) — a shallow copy holds the page's OWN elements, so a "
            "write through one of its slots rewrites what the artifact records, and "
            "`compare()` then reads `rank_identical: True` over a permuted page")
    # ⛔ AND THE COPY IS STILL NOT THE PAGE, one slot in: READING through the copy must not
    # red, or the arm above is just `_builds_new_container` deleted (the round-9 false red
    # coming back in the other direction).
    for label, body in (
        ("element READ through a copy",
         '    _c = dict(results[0])\n    print(_c["id"])\n'),
        ("copy handed to a pure read",
         '    _c = list(results)\n    print(len(_c))\n'),
        # ⛔ AND A SCALAR READ IS NOT AN ALIAS EITHER — the class the narrowed copy set must
        # not swallow (#14326 review P0): `json.dumps(results)` builds a STRING, and
        # `encode` is not in `_PURE_METHODS`, so `_is_scalar_producer` is the only thing
        # keeping this innocent pair green. Without it the scalar falls through to the alias
        # arm and the pin reports "an in-place sort/clear reaches the artifact".
        ("serialiser bound, then a str method off the allow-list",
         '    _s = json.dumps(results)\n    _s.encode("utf-8")\n'),
    ):
        assert not _mutations_of(body), (
            f"[{label}] FALSE RED (hits={_mutations_of(body)}) — the copy is a NEW container "
            "and reading one of its slots mutates nothing the artifact records")


def test_a_FALLBACK_expression_alias_still_reaches_the_sort() -> None:
    """A fallback expression binds the SAME collection — `results or []`, `results if c else []`.

    ⛔ MEASURED GREEN on the name-only bind (#14281 review P1): `aliases=[]` for both
    shapes, so `a1.sort()` permuted the recorded page while the receiver half and the
    "handed" half both saw nothing. `results or []` returns `results` whenever it is
    non-empty, which is every measured arm. `results[0]` is an ELEMENT and is
    deliberately NOT an alias, so it must stay green.
    """
    for label, value_expr in (("or", "results or []"),
                              ("ifexp", "results if flag else []")):
        src = ("def run(sdk):\n"
               "    results = sdk._tortoise_fts_query(query='q', limit=10)\n"
               f"    a1 = {value_expr}\n"
               "    a1.sort()\n")
        run_def = ast.parse(src).body[0]
        read = next(n for n in ast.walk(run_def)
                    if isinstance(n, ast.Call)
                    and getattr(n.func, "attr", None) == "_tortoise_fts_query")
        sort = next(n for n in ast.walk(run_def)
                    if isinstance(n, ast.Call) and getattr(n.func, "attr", None) == "sort")
        aliases = _aliases_of(run_def, lambda v: isinstance(v, ast.Name) and v.id == "results")
        assert "a1" in aliases, (
            f"[{label}] `a1 = {value_expr}` is not resolved as an alias "
            f"(aliases={sorted(aliases)}) — the fallback returns the measured page "
            "whenever the left arm wins, so `a1.sort()` permutes the recorded page "
            "invisibly")
        hits = _mutated_after(run_def, "results", read.lineno, artifact_append=None)
        assert sort.lineno in hits, (
            f"[{label}] a FALLBACK alias is GREEN (aliases={sorted(aliases)}, "
            f"hits={hits}) — a sorted page reaches the artifact while the pin reads "
            "the measured page")

    # A SUBSCRIPT is an element, not the collection: it must NOT be treated as an alias.
    src = ("def run(sdk):\n"
           "    results = sdk._tortoise_fts_query(query='q', limit=10)\n"
           "    first = results[0]\n"
           "    first.sort()\n")
    run_def = ast.parse(src).body[0]
    # ⛔ THE PIN RUNS PRODUCTION'S PREDICATE (#14281 review F6, measured). This used
    # `lambda v: isinstance(v, ast.Name) and v.id == "results"` — a predicate production
    # NEVER calls — while `_mutated_after` resolved aliases with a MENTION test. So
    # `first = results[0]` WAS an alias in production (`_mutated_after(...) -> [2]`, the
    # "in-place sort/clear" verdict) and this assertion was true only of the lambda beside
    # it: no change to the guard could red it, which is rule 3 by definition. The pin and
    # the guard now share `_collection_alias_predicate`, so the claim and the code cannot
    # drift apart again — and the claim is now TRUE in production.
    assert "first" not in _aliases_of(run_def, _collection_alias_predicate("results")), (
        "`first = results[0]` is an ELEMENT of the page, not the page — treating it as "
        "an alias would red an innocent lookup")
    read = next(n for n in ast.walk(run_def)
                if isinstance(n, ast.Call)
                and getattr(n.func, "attr", None) == "_tortoise_fts_query")
    assert not _mutated_after(run_def, "results", read.lineno, artifact_append=None), (
        "`first = results[0]` / `first.sort()` re-orders the ELEMENT's own list, not the "
        "measured page, and the artifact records `_result_id(r)` — this is one of the "
        "false reds #14281 review F5 measured")


def test_a_PURE_READ_in_a_log_line_is_NOT_a_handed_mutation() -> None:
    """`print(f"nhits={len(results)}")` mutates nothing and must stay GREEN.

    ⛔ MEASURED FALSE RED at 66880dace8 (#14281 review P2): `_mutated_after` walked the
    argument for NAMES, so `len(results)` inside the per-query print — the natural next
    edit to the print line `run()` already has — reported "line(s) [323] mutate a
    collection AFTER the measured read", which is neither true nor the shape the branch
    exists for. `_names_handed` stops at a known-pure callee.

    The catches the branch EXISTS for must survive, so both are asserted here too.
    """
    def _hits(body: str) -> list[int]:
        src = ("def run(sdk):\n"
               "    results = sdk._tortoise_fts_query(query='q', limit=10)\n" + body)
        run_def = ast.parse(src).body[0]
        read = next(n for n in ast.walk(run_def)
                    if isinstance(n, ast.Call)
                    and getattr(n.func, "attr", None) == "_tortoise_fts_query")
        return _mutated_after(run_def, "results", read.lineno, artifact_append=None)

    for innocent in ('    print(f"nhits={len(results)}")\n',
                     "    log(str(len(results)))\n"):
        assert not _hits(innocent), (
            f"{innocent.strip()!r} is a FALSE RED (hits={_hits(innocent)}) — a pure read "
            "inside a log line hands the page to nothing, and the message misdiagnoses "
            "it as an in-place mutation")

    for caught in ("    random.shuffle(results)\n",
                   "    helper(results + other)\n"):
        assert _hits(caught), (
            f"{caught.strip()!r} must stay RED — the page IS handed to a call that may "
            "mutate it, and stopping at pure reads must not disable that")


def test_a_BENIGN_bind_or_serialiser_is_NOT_a_mutation() -> None:
    """Naming a thing, or serialising it, is not mutating it (#14281 review F5).

    ⛔ MEASURED FALSE REDS at `3b587cb1`, each on a line that mutates nothing and each
    reported with a verdict that NAMES a mutation:

        `_env = os.environ`        -> "the harness mutates `os.environ` at line(s) [N]"
        `print(results)`           -> "line(s) [N] mutate a collection AFTER the read"
        `payload = json.dumps(results)` -> same
        `pprint.pformat(results)`  -> same
        `pq = per_query[-1]; print(pq["order"])`
                                   -> "mutate a RECORDED entry through an alias"

    Three distinct over-fires and one shared defect: a BIND or a PURE READ was graded as
    a mutation. `rebound` resolved ALIASES (`_env` is in `env_alias`, so `_env = os.environ`
    looked like `os.environ = …`), the element guard flagged the node that BINDS the
    element, and `_PURE_READS` did not carry the serialisers.

    By this file's own stated contract — "an unstated false red in a file whose contract
    is to state its false reds" — these are defects rather than taste: each costs a lane
    an edit to a 1350-line pin for an innocent one-liner, and each misdiagnoses itself.
    The positive controls keep the mutations the guards exist for RED.
    """
    # `_env = os.environ` is a BIND of the mapping, not a replacement of it.
    env_src = "import os\n_env = os.environ\n"
    env_tree = ast.parse(env_src)
    assert not _environ_mutations(env_tree, {
        id(c): p for p in ast.walk(env_tree) for c in ast.iter_child_nodes(p)}), (
        "`_env = os.environ` is reported as a MUTATION of `os.environ` — a bind is not a "
        "replacement, and the message is wrong about the line it names")

    def _hits(body: str) -> list[int]:
        src = ("def run(sdk):\n"
               "    results = sdk._tortoise_fts_query(query='q', limit=10)\n" + body
               + "    per_query = []\n    per_query.append({})\n")
        run_def = ast.parse(src).body[0]
        read = next(n for n in ast.walk(run_def)
                    if isinstance(n, ast.Call)
                    and getattr(n.func, "attr", None) == "_tortoise_fts_query")
        return _mutated_after(run_def, "results", read.lineno,
                              artifact_append=next(
                                  n for n in ast.walk(run_def)
                                  if isinstance(n, ast.Call)
                                  and getattr(n.func, "attr", None) == "append"))

    for innocent in ("    print(results)\n",
                     "    payload = json.dumps(results)\n",
                     "    z = pprint.pformat(results)\n"):
        assert not _hits(innocent), (
            f"{innocent.strip()!r} is a FALSE RED (hits={_hits(innocent)}) — it reads the "
            "page and builds a new value, so the 'in-place sort/clear' verdict is wrong")

    # ...while an actual in-place mutation through the same call shape stays RED.
    assert _hits("    random.shuffle(results)\n"), (
        "`random.shuffle(results)` must stay RED — a pure-read allow-list must not "
        "disable the branch that catches a genuinely in-place call")

    # The element guard must not flag the line that BINDS the element.
    pq_src = ("def run():\n    per_query = []\n    per_query.append({})\n"
              '    pq = per_query[-1]\n    print(pq["order"])\n')
    pq_def = ast.parse(pq_src).body[0]
    pq_append = next(n for n in ast.walk(pq_def) if isinstance(n, ast.Call)
                     and getattr(n.func, "attr", None) == "append")
    assert not _per_query_element_mutations(pq_def, pq_append.lineno), (
        "`pq = per_query[-1]; print(pq[\"order\"])` reports 'mutate a RECORDED entry' — "
        "the hit is the BIND line, and printing an element's list mutates nothing")
    assert _per_query_element_mutations(ast.parse(
        "def run():\n    per_query = []\n    per_query.append({})\n"
        '    pq = per_query[-1]\n    pq["order"].sort()\n').body[0], 1), (
        "`pq = per_query[-1]; pq[\"order\"].sort()` must stay RED — dropping the bind "
        "over-fire must not drop the mutation it sits next to")

def test_an_IMPORTED_or_GETATTR__environ_is_still_the_mapping() -> None:
    """The mapping is recognised through the STATIC spellings this repo can carry.

    ⛔ `os.environ`, `from os import environ` (incl. `as`), `getattr(os, "environ")`, and
    ANY alias/container/bound-mutator chain over those, to a fixpoint (#14281 review F1).
    `vars(os)["environ"]` and `os.__dict__["environ"]` ARE recognised (measured RED), so
    they are deliberately NOT listed as residuals — this docstring said they were not
    recognised while the code caught both (#14281 review P2). Residual, stated rather than
    implied: a name COMPUTED at runtime is not recognised, and a helper that reaches
    `os.environ` inside its own body is not followed — `tools/read_wave_latency.py`
    carries neither.

    ⛔ MEASURED GREEN before this fix, each against a genuinely flipped
    `TORTOISE_FUSED_RERANK` gate — which the artifact does not record (#14289), so a
    baseline/after pair straddling it still compares as "comparable":

        from os import environ; environ["TORTOISE_FUSED_RERANK"] = "1"
        from os import environ as env; env["TORTOISE_FUSED_RERANK"] = "1"
        getattr(os, "environ")["TORTOISE_FUSED_RERANK"] = "1"

    `_aliases_of` walks `Assign`/`AnnAssign`/`NamedExpr` only, so an `ImportFrom` bound
    the name without ever creating a node it looks at; and `getattr(os, "environ")` is a
    `Call`, not an `Attribute`. `from os import environ` is the IDIOMATIC spelling, so
    this is the shape a reader reaches for first — not an exotic one.
    """
    #: ⛔ (SOURCE, THE MUTATING LINE). The line is the assertion, not the label
    #: (#14281 review F4, measured): this table used to assert only TRUTHINESS, and four
    #: of its clauses could be DELETED with the test still GREEN, because the hit it got
    #: was an over-fire on a NEIGHBOURING BIND line (`_e = getattr(...)`, `_p = _e.pop`)
    #: rather than the mutation the case names:
    #:
    #:     delete `method`     -> still GREEN      delete `bound_call` -> still GREEN
    #:     delete `handed`     -> still GREEN      delete `bound = …`  -> still GREEN
    #:
    #: (`subscript` and `rebound` DID red — the two clauses that were already anchored.)
    #: The container case was worse than uncovered: its mutating line was never in `hits`
    #: at all — it passed on the `_envs = [environ]` bind — so the write through `_envs[0]`
    #: that flips the gate was not covered by any clause. Anchoring each case on its
    #: mutating line is what makes a deleted clause RED, and it required dropping the
    #: bind over-fires too (`_is_mapping_itself`).
    cases = {
        "plain import": (
            "from os import environ\n"
            'environ["TORTOISE_FUSED_RERANK"] = "1"\n', 2),
        "aliased import": (
            "from os import environ as env\n"
            'env["TORTOISE_FUSED_RERANK"] = "1"\n', 2),
        "getattr": (
            "import os\n"
            'getattr(os, "environ")["TORTOISE_FUSED_RERANK"] = "1"\n', 2),
        # ⛔ THE SEED MUST BE AS WIDE AS THE CHECK (#14281 review): `getattr(os, "environ")`
        # was recognised by `_is_environ` and NOT by `_mentions_mapping`, so the inline
        # spelling red and every ALIASED spelling of it stayed green — the same
        # narrower-predicate-than-check shape that made the import a terminal name.
        "getattr, then aliased": (
            "import os\n"
            '_e = getattr(os, "environ")\n'
            '_e["TORTOISE_FUSED_RERANK"] = "1"\n', 3),
        "getattr, aliased bound mutator": (
            "import os\n"
            '_e = getattr(os, "environ")\n'
            "_p = _e.pop\n"
            '_p("TORTOISE_FUSED_RERANK")\n', 4),
        # ⛔ SECOND-ORDER, and these are what makes the import a SEED rather than a
        # terminal name (#14281 review): adding the imported name to `env_alias` AFTER
        # `_aliases_of` had already run left every one of these GREEN, while the same
        # shapes written as `os.environ` were RED — the guard was transitive for one
        # spelling and one-hop for the spelling this commit adds.
        "imported, then aliased": (
            "from os import environ as _e\n"
            "_alias = _e\n"
            '_alias["TORTOISE_FUSED_RERANK"] = "1"\n', 3),
        "imported, bound mutator": (
            "from os import environ as _e\n"
            "_pop = _e.pop\n"
            '_pop("TORTOISE_FUSED_RERANK")\n', 3),
        "imported, through a container": (
            "from os import environ\n"
            "_envs = [environ]\n"
            '_envs[0]["TORTOISE_FUSED_RERANK"] = "1"\n', 3),
        # ⛔ A `for` TARGET IS A BIND TOO (#14281 review F2, measured GREEN). `_e` is an
        # alias of the mapping through the one bind spelling that carries no `Assign`
        # node, so `_p = _e.pop` never resolved and the bound mutator was a terminal name
        # — the same "seed narrower than the check" shape, one spelling over:
        #
        #     for _e in (os.environ,): _p = _e.pop; _p("TORTOISE_FUSED_RERANK") -> []
        #     from os import environ;   _p = environ.pop; _p(…)               -> [2, 3]
        "for-bound alias of the mapping": (
            "import os\n"
            "for _e in (os.environ,):\n"
            "    _p = _e.pop\n"
            '    _p("TORTOISE_FUSED_RERANK")\n', 4),
    }
    for label, case in cases.items():
        src, mutating_line = case
        tree = ast.parse(src)
        parents: dict[int, ast.AST] = {
            id(c): p for p in ast.walk(tree) for c in ast.iter_child_nodes(p)}
        hits = _environ_mutations(tree, parents)
        assert mutating_line in hits, (
            f"[{label}] the MUTATING line {mutating_line} is not in hits={hits} — either "
            "the env guard does not resolve this spelling of `os.environ`, or it fires only "
            "on the neighbouring BIND line, which is not coverage: the fused-rerank gate "
            "can be flipped inside `run()` and the artifact would not record which arm was "
            "in force (#14289)")


def test_mutating_a_RECORDED_entry_through_an_alias_is_red() -> None:
    """The recorded `per_query` entries are watched through element aliases (#14281 F2).

    ⛔ MEASURED GREEN at `e66d23f539d4`; every shape below leaves `run()` returning a page
    the pin never inspected, and `compare()` reads exactly it:

        for _pq in per_query: _pq["order"].sort()
        for _pq in per_query: _pq["order"] = sorted(_pq["order"], key=str)
        for _i, _pq in enumerate(per_query): _pq["order"].sort()
        (zed,) = per_query[-1:]; zed["order"].sort()

    ⛔ RE-MEASURED rather than carried: the two ASSIGNMENT spellings
    (`pq = per_query[-1]; pq["order"].sort()` and its `= sorted(…)` variant) were GREEN
    when this guard was written and are RED at `e66d23f539d4`, because that commit's
    `rows` clause in `_mutated_after` reaches them. They are pinned below as a CONTROL —
    if `rows` is dropped they fall back to this guard — but they are not this guard's
    subject and are not claimed as measured-green.
    """
    cases = {
        "loop target, in-place sort": (
            'for _pq in per_query:\n    _pq["order"].sort()\n'),
        "loop target, reassigned": (
            'for _pq in per_query:\n    _pq["order"] = sorted(_pq["order"], key=str)\n'),
        "enumerate target, in-place sort": (
            'for _i, _pq in enumerate(per_query):\n    _pq["order"].sort()\n'),
        "unpacked slice target": (
            '(zed,) = per_query[-1:]\nzed["order"].sort()\n'),
        # ⛔ AND AN ALIAS OF THE ELEMENT'S INNER LIST (#14281 review F3, measured GREEN in
        # BOTH guards) — the same family as the bound-mutator spelling the docstring above
        # declares, reached by binding the INNER LIST instead of its method:
        #
        #     for _pq in per_query: _s = _pq["order"]; _s.sort()   -> mutated_after=[] element=[]
        #     for _pq in per_query: _pq["order"].sort()            -> mutated_after=[N] element=[N]
        #
        # Every query's page could be canonicalised before `compare()` read it while the
        # direct spelling was caught, so this was a missing HOP, not a missing class.
        "inner-list alias of a loop target": (
            'for _pq in per_query:\n    _s = _pq["order"]\n    _s.sort()\n'),
    }
    for label, body in cases.items():
        src = "def run():\n    per_query.append({})\n" + "".join(
            "    " + line + "\n" for line in body.splitlines())
        run_def = ast.parse(src).body[0]
        append = next(n for n in ast.walk(run_def) if isinstance(n, ast.Call)
                      and getattr(n.func, "attr", None) == "append")
        hits = _per_query_element_mutations(run_def, append.lineno)
        assert hits, (
            f"[{label}] GREEN — a recorded `per_query` entry is re-ordered through an "
            "alias after it was appended, so the returned report carries a page the pin "
            "never inspected")
        # ⛔ ANCHORED ON THE MUTATING LINE (#14281 review F4's rule, applied here): the hit
        # must be the statement that MUTATES, never the bind that merely names the element
        # or its inner list — a bind-line over-fire is what let the environ table stay
        # green with half its clauses deleted.
        mutating = [n.lineno for n in ast.walk(run_def) if n is not append
                    and ((isinstance(n, ast.Call)
                          and getattr(n.func, "attr", None) in _MUTATORS)
                         or (isinstance(n, ast.Subscript)
                             and isinstance(n.ctx, ast.Store)))]
        assert min(mutating) in hits, (
            f"[{label}] hits={hits} do not include the mutating line {min(mutating)} — the "
            "guard is firing on a neighbouring bind instead of the mutation")

    # CONTROLS, so a future reader can tell these two clauses are still covered elsewhere
    # and are not what this guard exists for.
    ctrl = ast.parse(
        "def run():\n    per_query.append({})\n    per_query[-1][\"order\"].sort()\n").body[0]
    ctrl_append = next(n for n in ast.walk(ctrl) if isinstance(n, ast.Call)
                       and getattr(n.func, "attr", None) == "append")
    # ⛔ AND THE OTHER GUARD'S HALF OF THE SAME SHAPE (#14281 review F3, measured): before
    # `_aliases_of` had a `for` arm, `_s = _pq["order"]` resolved to nothing at all, so
    # `_s.sort()` was GREEN here too. Both halves are asserted because either one alone
    # leaves every query's page re-orderable.
    alias_src = ("def run():\n    per_query.append({})\n"
                 '    for _pq in per_query:\n        _s = _pq["order"]\n'
                 "        _s.sort()\n")
    alias_def = ast.parse(alias_src).body[0]
    alias_append = next(n for n in ast.walk(alias_def) if isinstance(n, ast.Call)
                        and getattr(n.func, "attr", None) == "append")
    alias_hits = _mutated_after(alias_def, "per_query", alias_append.lineno, alias_append)
    assert alias_hits, (
        "`for _pq in per_query: _s = _pq[\"order\"]; _s.sort()` is GREEN in "
        "`_mutated_after` — an alias of an element's inner list is that inner list, so "
        "every query's page can be canonicalised before `compare()` reads it")
    ctrl_hits = _mutated_after(ctrl, "results", 0, ctrl_append)
    assert ctrl_hits, (
        "the LITERAL `per_query[-1][\"order\"].sort()` is no longer caught by "
        "`_mutated_after`'s `rows` clause — that clause is the coverage for this class "
        "(the older `or \"per_query\" in receiver` term was subsumed by it and has been "
        "deleted; this control names `rows`, which is what actually reds)")
    rows_ctrl = ast.parse(
        "def run():\n    per_query.append({})\n    pq = per_query[-1]\n"
        '    pq["order"].sort()\n').body[0]
    rows_append = next(n for n in ast.walk(rows_ctrl) if isinstance(n, ast.Call)
                       and getattr(n.func, "attr", None) == "append")
    assert _mutated_after(rows_ctrl, "results", 0, rows_append), (
        "`_mutated_after`'s `rows` clause no longer catches a name bound to "
        "`per_query[-1]` — it and this guard are the two halves of the same class, and "
        "losing one silently widens the other")


def _pin_source(body: str) -> ast.AST:
    """A minimal module carrying the two real reads plus `body`, for the pin's own census.

    The same shape the pin inspects in the artifact — a warm-up call, the measured call
    assigned to a name, then whatever the case under test adds — so a table row exercises
    the PRODUCTION predicate against a source the pin would have to refuse.
    """
    return ast.parse(
        "def run(sdk, queries):\n"
        "    sdk._tortoise_fts_query(query=queries[0]['query'], limit=10)\n"
        "    results = sdk._tortoise_fts_query(query=queries[0]['query'], limit=10, "
        "leg_trace=None)\n" + body + "    return results\n")


def _pin_source_async(body: str) -> ast.AST:
    """`_pin_source`'s wrapper for the shapes that only parse inside an `async def`.

    ⛔ SAME TWO REAL READS, ONE KEYWORD APART (#14326 round 17). `_pin_source` opens a plain
    `def run(…)`, and `async for` under a plain `def` is a SyntaxError, so the `AsyncFor`
    target row cannot ride the shared table without a second wrapper. Nothing else differs:
    the census walks the AST and never looks at the enclosing function's kind.
    """
    return ast.parse(
        "async def run(sdk, queries):\n"
        "    sdk._tortoise_fts_query(query=queries[0]['query'], limit=10)\n"
        "    results = sdk._tortoise_fts_query(query=queries[0]['query'], limit=10, "
        "leg_trace=None)\n" + body + "    return results\n")


def _pin_refuses(source: ast.AST) -> bool:
    """True when the pin's OWN three reach assertions on `source` would be RED.

    ⛔ THE SAME FUNCTION THE PIN RUNS (`_read_reach`), and the same three conditions the
    pin asserts (`not indirect`, `len(calls) == 2`, `len(mentions) == 2`) — a table that
    resolved the read with its own walk could pass while the pin failed (#14279 review F2,
    the drift `_collection_alias_predicate` exists to prevent).
    """
    mentions, calls, indirect = _read_reach(source)
    return bool(indirect) or len(calls) != 2 or len(mentions) != 2


def _mutations_of(body: str) -> list[int]:
    """`_mutated_after`'s hits for `body` appended after the measured read."""
    run_def = _pin_source(body).body[0]
    # The MEASURED call is the LAST read in the source `_pin_source` builds (the warm-up is
    # the first), so anchor the scan there — the pin's own `holder.lineno`.
    read = max((n for n in ast.walk(run_def)
                if isinstance(n, ast.Call)
                and getattr(n.func, "attr", None) == "_tortoise_fts_query"),
               key=lambda n: n.lineno)
    return _mutated_after(run_def, "results", read.lineno, artifact_append=None)


def test_a_SELF_REFERENTIAL_bind_TERMINATES_and_still_reaches() -> None:
    """`_s = _s + "…"` is an ACCUMULATION: bounded, over-approximate, and still a reach.

    ⛔ MEASURED BEFORE THIS FIX (#14326 P1): `_str_constants` never returned on this source
    — `rc=124` after 60 s — once `_values` grew the `Add` arm, because the fixpoint fed the
    product its own output (one more `_fts_query` per pass, so `changed` never went False).
    `origin/main`'s version returned `{'_s': '_tortoise'}`: it had no `Add` arm at all, so
    this is a REGRESSION that arm introduced, not a pre-existing gap. The verdict stays
    fail-CLOSED either way — the earlier value remains in the set, this map's declared
    polarity (a later bind can only ADD candidates) — so the reach is refused and an inert
    use of the same name is not.
    """
    consts = _str_constants(ast.parse(
        'def f():\n    _s = "_tortoise"\n    _s = _s + "_fts_query"\n'))
    assert "_tortoise_fts_query" in consts["_s"]

    # ⛔ AND THE ORDER BETWEEN `+=` AND A SELF-REFERENTIAL `=` IS THE SEMANTICS (#14326
    # round 14, MEASURED as a FAIL-OPEN in the two-phase apply): with `+=` staged before
    # every `=`, the product of `_S = _S + "_query"` was taken against `{'_tortoise'}` only,
    # so the string the code actually BUILDS — `'_tortoise_fts_query'` — was ABSENT from the
    # set (`{'_tortoise','_tortoise_query','_tortoise_fts','_tortoise_query_fts'}`), and a
    # third `kind="statement"` reach was `refuses=False`. One pass, sorted by source
    # position, is what makes the over-approximation still contain the truth.
    mixed = _str_constants(ast.parse(
        'def f():\n    _S = "_tortoise"\n    _S += "_fts"\n    _S = _S + "_query"\n'))
    assert "_tortoise_fts_query" in mixed["_S"]

    reach = ('    _s = "_tortoise"\n    _s = _s + "_fts_query"\n'
             '    getattr(sdk, _s)(query=queries[0]["query"], limit=10, kind="statement")\n')
    assert _pin_refuses(_pin_source(reach)) is True
    inert = '    _s = "_tortoise"\n    _s = _s + "x"\n    print(f"note {_s}")\n'
    assert _pin_refuses(_pin_source(inert)) is False


@pytest.mark.timeout(20)
def test_a_BIND_CYCLE_REFUSES_instead_of_spinning() -> None:
    """Two names accumulating through each other have no finite product — refuse.

    ⛔ A MUTUAL accumulation is NOT the per-target case above (neither bind loads its OWN
    target), so staging cannot see it and the fixpoint budget is the only guard. The
    `pytest.mark.timeout` is load-bearing and `pytest.raises` alone is NOT enough (#14326
    round 14, review P2): with the budget deleted this test HANGS rather than failing, so
    without a per-item bound its own regression reads as a shard timeout — the non-verdict
    the budget exists to remove.
    """
    with pytest.raises(_ResolutionDidNotConverge):
        _str_constants(ast.parse(
            'def run():\n    _a = "x"\n    _b = _a + "y"\n    _a = _b + "z"\n'))


@pytest.mark.timeout(20)
def test_a_MUTUAL_PRODUCT_REFUSES_before_it_allocates() -> None:
    """A PASS budget bounds passes, not WORK — the product budget is what makes this a red.

    ⛔ MEASURED (#14326 round 14): `_a = "xx"; _b = _a * 2; _a = _b * 2` is three
    statements, all-static, no `+=` — and it DOUBLES a string every pass, so with only
    `_FIXPOINT_MAX_PASSES = 64` it raised `MemoryError` under a 3 GB `ulimit -v`: pass 40 is
    a 1 TB string and the pass budget is never reached. A killed shard answers neither green
    nor red, which is why the resolver caps a name's PRODUCT as well.
    """
    with pytest.raises(_ResolutionDidNotConverge):
        _str_constants(ast.parse(
            'def run():\n    _a = "xx"\n    _b = _a * 2\n    _a = _b * 2\n'))


@pytest.mark.timeout(20)
def test_the_CONCAT_doubling_and_the_ONE_TOKEN_repetition_REFUSE() -> None:
    """Each budget clause has a shape only IT stops — otherwise neither is a guard.

    ⛔ MEASURED (#14326 round 15, review P2): deleting `_check_budget`'s raise left the whole
    file **35 passed**, because `_repetitions`' own guard already refused the `* 2` doubling
    the row above uses — so that row pinned `_repetitions`, not the clause it was added for.
    The shape ONLY `_check_budget` stops is a CONCATENATION doubling (no `*` at all):

        _a = "xx"; _b = _a + _a; _a = _b + _b

    and the shape only `_repetitions` stops is one token that allocates the budget in a
    single call (`_s * 100000000`). Both are pinned here, so deleting either guard reds.
    """
    with pytest.raises(_ResolutionDidNotConverge):
        _str_constants(ast.parse(
            'def run():\n    _a = "xx"\n    _b = _a + _a\n    _a = _b + _b\n'))
    # The literal is 12 digits ON PURPOSE: `_s * 100000000` is only 800 MB, so a build that
    # SKIPS the pre-allocation guard still ends in `_check_budget`'s refusal and this row
    # would stay green (MEASURED — deleting that guard red nothing until the literal was
    # raised). At 999999999999 the same build is a `MemoryError`, which is a kill, not a red.
    with pytest.raises(_ResolutionDidNotConverge):
        _str_constants(ast.parse(
            'def run():\n    _s = "_tortoise"\n    _s = _s * 999999999999\n'))


def test_an_accumulation_over_several_ITERATIONS_is_the_DECLARED_residual() -> None:
    """Every shape whose APPLIED COUNT is not enumerated is named, and pinned GREEN.

    ⛔ THE DECLARATION IS A MEASUREMENT, NOT A DODGE (#14326 round 16). Applying a bind once
    under-approximates a loop body — `_S = "_tortoise"` then
    `for _p in ("_fts", "_query"): _S = _S + _p` builds `_tortoise_fts_query` and resolves to
    `{'_tortoise','_tortoise_fts','_tortoise_query'}`, so a lever-carrying reach over `_S` is
    not refused. ENUMERATING the count was implemented and REVERTED: the product is the power
    set (a 5-element loop needs 5^5 = 3,125 spellings), and a plain accumulation

        _order = ""
        for _col in ("arm_id", "qid", "rank", "score", "leg"):
            _order = _order + _col + ", "

    then made `_str_constants` RAISE `_ResolutionDidNotConverge` — a FALSE RED on inert code,
    the exact class this pin exists to remove (measured: 1 failed / 36 passed over it). A
    guard that refuses ordinary code reds every PR; one that under-approximates is named.

    ⛔ WHAT EACH ROW CAN AND CANNOT CATCH (round-5 review P2, MEASURED both ways). The two
    literal-`for` rows and the comprehension `if` clause FLIP when the applied count is
    enumerated again — replayed against the reverted enumerating implementation (a version of
    this file that never shipped), `absent` becomes False for exactly those THREE — so they
    are regression detectors for the enumeration class. The remaining rows return the SAME
    set under both implementations: they are not detectors, they pin the boundary AS IT
    STANDS, so the docstring cannot drift without a red. Both jobs are stated here rather
    than implied, and the docstring's residual note is scoped to match.
    """
    cases = {
        "literal `for` body": ('    _S = "_tortoise"\n'
                               '    for _p in ("_fts", "_query"):\n'
                               '        _S = _S + _p\n'),
        "`+=` in a `for` body": ('    _S = "_tortoise"\n'
                                 '    for _p in ("_fts", "_query"):\n'
                                 '        _S += _p\n'),
        "loop over a NAME": ('    _SEQ = ("_fts", "_query")\n    _S = "_tortoise"\n'
                             '    for _p in _SEQ:\n        _S = _S + _p\n'),
        # ⛔ THE OTHER TWO ITERABLE KINDS THE DOCSTRING NAMES (#14326 round 17). The note at
        # the bind arm lists "a NAME, an attribute, a call or a STARRED literal", but only
        # NAME and STARRED had a row — the sentence claimed coverage the row set did not
        # have, which is the drift class round 5 fixed one commit earlier. MEASURED under
        # BOTH implementations: neither flips (an attribute/call iterable resolves to nothing,
        # so `_S` is `{'_tortoise'}` either way) — these are boundary pins, not detectors.
        "loop over an ATTRIBUTE": ('    _S = "_tortoise"\n    for _p in _cfg.parts:\n'
                                   '        _S = _S + _p\n'),
        "loop over a CALL": ('    _S = "_tortoise"\n    for _p in _parts():\n'
                             '        _S = _S + _p\n'),
        "STARRED literal iterable": ('    _S = "_tortoise"\n'
                                     '    for _p in (*("_fts", "_query"),):\n'
                                     '        _S = _S + _p\n'),
        "comprehension ELEMENT (a walrus)": ('    _S = "_tortoise"\n'
                                             '    [(_S := _S + _p) for _p in ("_fts", "_query")]\n'),
        "comprehension `if` clause": ('    _S = "_tortoise"\n'
                                       '    [_p for _p in ("_fts", "_query")'
                                       ' if (_S := _S + _p)]\n'),
    }
    for label, source in cases.items():
        consts = _str_constants(ast.parse("def run():\n" + source))
        # ⛔ THE NAME MUST BE PRESENT (a missing name would satisfy every "not in" below
        # vacuously — round-5 review P2), and the READ NAME must not be among its values.
        assert "_S" in consts, f"{label}: `_S` did not resolve at all — vacuous row"
        assert "_tortoise_fts_query" not in consts["_S"], label
    # ⛔ THE `AsyncFor` APPLIED COUNT IS ITS OWN ROW (#14326 round 19, MEASURED at
    # `9b48318eedc3`: `_S` resolved to `{'_tortoise', '_tortoise_fts', '_tortoise_query'}` —
    # the READ NAME ABSENT, so a lever over `_S` passes). Every row in `cases` above is wrapped
    # in a plain `def run(…)`, where `async for` is a SyntaxError, so the `async` half of the
    # sentence at the bind arm carried NO row while the sentence claimed one — the same
    # stale-claim class this round corrected one member of and left standing for this one.
    async_consts = _str_constants(ast.parse(
        "async def run():\n"
        '    _S = "_tortoise"\n'
        '    async for _p in ("_fts", "_query"):\n'
        "        _S = _S + _p\n"))
    assert "_S" in async_consts, "`_S` did not resolve for an `async for` — vacuous row"
    assert "_tortoise_fts_query" not in async_consts["_S"], (
        "an `async for` applied count is the DECLARED residual — if it starts resolving, "
        "delete the residual note instead of leaving it contradicting the code")
    # ⛔ AND THE `while` ROW MEASURES THE APPLIED COUNT, not just the read name: a loop that
    # appends `_fts` can only build `_tortoise` / `_tortoise_fts` / `_tortoise_fts_fts`, so
    # "the read name is absent" is trivially true there whatever the count. The value that
    # MOVES with the count is the doubled suffix (round-5 review P2; measured: two
    # applications produce it, one does not).
    while_src = ('    _S = "_tortoise"\n    _n = 0\n'
                 '    while _n < 2:\n        _S = _S + "_fts"\n        _n += 1\n')
    while_consts = _str_constants(ast.parse("def run():\n" + while_src))
    assert "_tortoise_fts_fts" not in while_consts["_S"]


def test_the_pin_DOES_NOT_REFUSE_on_ordinary_loop_accumulation() -> None:
    """The false-red half: a `", ".join`-style order clause must resolve, not raise.

    ⛔ MEASURED REGRESSION (#14326 round 16): while the applied count WAS enumerated, this
    exact snippet — lifted from the shape `tools/read_wave_latency.py` uses to build an
    `order_by` clause — made `_str_constants` raise `_ResolutionDidNotConverge` and the pin
    report `1 failed, 36 passed`. An inert accumulation is not a read and must not cost a
    verification run. This row keeps that regression out: it fails if the product budget ever
    starts firing on ordinary loops again.
    """
    consts = _str_constants(ast.parse(
        'def _order_clause():\n'
        '    _order = ""\n'
        '    for _col in ("arm_id", "qid", "rank", "score", "leg"):\n'
        '        _order = _order + _col + ", "\n'
        '    return _order.rstrip(", ")\n'))
    assert consts["_order"], "the accumulation must resolve to at least its first step"
    # ...and the same loop standing INERT beside the real read must not change the verdict.
    body = ('    _order = ""\n'
            '    for _col in ("arm_id", "qid", "rank", "score", "leg"):\n'
            '        _order = _order + _col + ", "\n'
            '    logging.info(_order)\n')
    assert _pin_refuses(_pin_source(body)) is False


def test_EVERY_static_binding_form_is_a_READ_reach() -> None:
    """Each way a name can hold the read's NAME must be a reach the pin refuses.

    ⛔ THIS IS THE ASSERTION THE PREVIOUS COMMITS DID NOT HAVE (#14279 review F2). Four
    fix hunks shipped with zero tests: reverting any of them left the file byte-identical to
    `origin/main`, which the pin runs GREEN at 22 passed — so the fail-open re-opened under
    any later refactor and nothing reddened. Measured non-vacuity, each mutant run against
    THIS table:

      - revert `_str_constants` to its previous form -> the `for`-target, comprehension,
        re-bind, subscript-store, name-chain, tuple-unpack and name-carried rows all pass
        (the third reach goes uncensused), as does every #14326 row added below (the
        `match`-capture pair, the NAME-HELD and NESTED unpacks, the dict/set and SPLATTED
        literals, the STARRED target, and the PARAMETER DEFAULT);
      - revert `_values`' SET/DICT arms -> the three literal-iterable rows pass (#14326
        review F2: a `for` target over `{"…"}` / `{"…": 1}` was GREEN);
      - revert `_indirect_reads` to its arity test -> the two `getattr(*…)` splat rows pass;
      - delete `_possible_strings`' SET/DICT arms -> the two literal-splat rows pass
        (#14326 review F1, measured: `getattr(*{sdk, _N})` / `getattr(*{_N: 1})` executing
        the read with `kind="statement"` was GREEN against a pin carrying no row for
        either shape, so the arms were unreachable by any test — the set/dict *for-target*
        rows reach `_values`, not `_possible_strings`, and cannot red them);
      - remove the `IfExp` / `BoolOp` / `JoinedStr` arms from `_values` AND
        `_possible_strings` -> the five SELECTION/f-string rows pass (#14326 review P6,
        MEASURED both ways at `8f13cb24`+this commit: with the arms present all five REFUSE
        because `indirect` fires; with them removed all five are GREEN at mentions 2, calls
        2, indirect 0, so a third reach carrying `kind="statement"` shipped the lever). The
        two INLINE rows pin `_possible_strings` — a literal written inline in the `getattr`
        operand is not a bind at all, so it never reaches `_values`;
      - at `origin/main` -> the `AnnAssign`/unpack/chained/walrus rows pass as well;
      - delete one arm of `_rebound_callee_names` -> that arm's row goes GREEN (#14326 round
        7, EACH MEASURED at `d790e1f0` with the arm removed and restored byte-identically:
        `from x import … as print` / `from x import *` / `match getattr: case print:` /
        `with _mk() as print:` / `_buf.write = getattr` + `_buf.write(sdk, …)` all reached
        the read at mentions 2, calls 2, indirect 0 — while the `print = getattr` row was
        REFUSED, so the enumeration was incomplete rather than the class being open).
        Reverting that function's `with` arm to the whole-node walk instead REDS the
        with-body control, which is why both directions carry a row: the same arm decided a
        false PASS and a false RED.
      - delete `ast.AsyncFor` from `_str_constants`' bind tuple -> the `async for target`
        row goes GREEN (round 17, MEASURED at `689708686b`: the deletion left the whole file
        at **39 passed**, so the member was an unpinned clause; with the row, 1 failed);
      - the three clauses a fresh-context review MEASURED unpinned at `bb89a48cee7e` each
        leave the whole file **25 passed** when reverted, and each now reds exactly one row
        (applied in place, restored byte-identically — sha256 `c52c28a4cf65`): the
        `_is_read_ref` Name-`Call` half (`_dispatch(_N)(sdk)(…)` — the only row whose third
        reach bypasses `getattr`, so `_indirect_reads` cannot cover for the census);
        `first = len(positional) - len(node.args.defaults)` → `first = 0` (the PADDED
        positional default; every older default row had one parameter, so the subtraction
        was never exercised); and `kwonlyargs[index]` → `kwonlyargs[0]` (the SECOND kw-only
        parameter, for the same reason).

    The control at the end is the OTHER direction: a provenance constant whose value merely
    NAMES the read (`print(f"measuring {_NOTE}")`) must stay GREEN, or the narrowing that
    removed that false red is gone.
    """
    reach = 'getattr(sdk, {name})(query=queries[0]["query"], limit=10, kind="statement")'
    rows = {
        "AnnAssign": f'    _N: str = "_tortoise_fts_query"\n    {reach.format(name="_N")}\n',
        "unpack target": f'    (_N,) = ("_tortoise_fts_query",)\n    {reach.format(name="_N")}\n',
        "chained assign": f'    _N = _M = "_tortoise_fts_query"\n    {reach.format(name="_N")}\n',
        # ⛔ A `with` TARGET (#14326 round 20, MEASURED at `57735ab47`: `_N` stayed
        # UNRESOLVED — totals mentions 2 / calls 2 / indirect 0, so the lever contributed no
        # mention and reached the read — while the `for`/`match`/parameter-default spellings
        # of the same bind all gave `mentions=3, indirect=1`). The target binds what
        # `__enter__` returns, so the CONTEXT EXPRESSION
        # is the over-approximation: the rule `_aliases_of` already applied to this node.
        "`with` target": (f'    _r = "_tortoise_fts_query"\n'
                          f'    with _r as _N:\n'
                          f'        {reach.format(name="_N")}\n'),
        # ⛔ AND ITS `async` HALF IS A SEPARATE ASSERTION BELOW (#14326 round 21, MEASURED:
        # replacing `(ast.With, ast.AsyncWith)` with `ast.With` in `_str_constants`' arm left
        # the whole file at **41 passed**, while the same mutant drops `async with _r as _N:`
        # back to mentions 2 / calls 2 / indirect 0 — a lever-carrying read passing silently).
        # It cannot ride this dict: every body here is wrapped in a plain `def run(…)`, where
        # `async with` is a SyntaxError.
        "walrus": f'    (_N := "_tortoise_fts_query")\n    {reach.format(name="_N")}\n',
        # ⛔ A WALRUS ON THE RIGHT-HAND SIDE OF A BIND (#14326 round 8, MEASURED GREEN at
        # `9762589e`): `_str_constants` already treats `NamedExpr` as a bind, so `_values`
        # lacking the arm was an in-file asymmetry — the same literal written `_N = _M = …`
        # reds (row above), which is what made this one visible.
        "walrus on the RHS of a bind": (
            '    _N = (_M := "_tortoise_fts_query")\n'
            f'    {reach.format(name="_N")}\n'),
        "walrus INLINE in the getattr": (
            '    getattr(sdk, (_M := "_tortoise_fts_query"))'
            '(query=queries[0]["query"], limit=10, kind="statement")\n'),
        # ⛔ THE `_possible_strings` `NamedExpr` ARM NEEDS ITS OWN ROWS (#14326 round 9,
        # MEASURED: deleting the arm leaves the suite green). The row above does NOT pin it —
        # that reach also reds at the mentions census (`m=3`), so it refuses either way. These
        # four go through `getattr(*…)`/a keyword, where the operand path is the ONLY thing
        # that can see the walrus.
        "walrus INLINE, splatted": (
            '    getattr(*{sdk, (_M := "_tortoise_fts_query")})'
            '(query=queries[0]["query"], limit=10, kind="statement")\n'),
        "walrus INLINE, tuple-splatted": (
            '    getattr(*((_M := "_tortoise_fts_query"),))(sdk)'
            '(query=queries[0]["query"], limit=10, kind="statement")\n'),
        "walrus INLINE, in a SELECTION": (
            '    getattr(sdk, ((_M := "_tortoise_fts_query") if queries else "x"))'
            '(query=queries[0]["query"], limit=10, kind="statement")\n'),
        "walrus INLINE, as a KEYWORD": (
            '    _PageFetcher(name=(_M := "_tortoise_fts_query"))(sdk)'
            '(query=queries[0]["query"], limit=10, kind="statement")\n'),
        # ⛔ AND A LITERAL-ONLY `+` (#14326 round 9, MEASURED GREEN at `d6ed6799` while the
        # implicit-concat spelling was refused — the same string, folded by `compile()` to one
        # `LOAD_CONST`): nothing is computed, so it is the `IfExp`/`BoolOp` case exactly.
        "literal-only `+` (a compile-time fold)": (
            '    getattr(sdk, "_tortoise_" + "fts_query")'
            '(query=queries[0]["query"], limit=10, kind="statement")\n'),
        # ⛔ THE SAME SUM ONE BIND AWAY (#14326 round 12, MEASURED at `c380075b4`: BOTH rows
        # below left the pin GREEN while the INLINE spelling above was refused, so the verdict
        # hinged on whether the sum was bound first). `_possible_strings` grew the `BinOp`/`Add`
        # arm in round 9 but `_values` — the BIND resolver — did not, and every round-9/10 `+`
        # row is INLINE, so nothing exercised that path. Both operands are `Constant`s (or a
        # name bound to one), which is NOT the declared residual (`_N + "_x"`, an UNRESOLVABLE
        # operand).
        "bound literal-only `+`": (
            '    _N = "_tortoise_" + "fts_query"\n'
            f'    {reach.format(name="_N")}\n'),
        "bound NAME+literal `+`": (
            '    _P = "_tortoise_"\n'
            '    _N = _P + "fts_query"\n'
            f'    {reach.format(name="_N")}\n'),
        # ⛔ THE PRODUCT, BOTH DIRECTIONS (#14326 round 10, each MEASURED): a statically
        # resolvable operand that is a SELECTION joins to every combination, so a sum that CAN
        # be the read name is refused… (`("x" if q else "_tortoise_") + "fts_query"`,
        # `"_tortoise_" + ("fts_query" if q else "zzz")`, and a `for`-target prefix all
        # measured GREEN while the fallback was a UNION).
        "SELECTED operand joined with a literal": (
            '    getattr(sdk, ("x" if queries else "_tortoise_") + "fts_query")'
            '(query=queries[0]["query"], limit=10, kind="statement")\n'),
        "for-TARGET prefix joined with a literal": (
            '    for _P in ("x", "_tortoise_"):\n'
            '        getattr(sdk, _P + "fts_query")'
            '(query=queries[0]["query"], limit=10, kind="statement")\n'),
        "for target": ('    for _N in ("_tortoise_fts_query",):\n        '
                       f'{reach.format(name="_N")}\n'),
        "comprehension target": (f'    [_N for _N in ("_tortoise_fts_query",)]\n    '
                                 f'{reach.format(name="_N")}\n'),
        # ⛔ A SET/DICT LITERAL IS THE SAME BIND SPELLING (#14326 review F2, each measured
        # GREEN with a third reach carrying `kind="statement"`): `For.iter` is an arbitrary
        # expression, so these are binds with no `Assign` node whose value is statically
        # visible — not the "computed at runtime" residual.
        "set literal for target": (f'    for _N in {{"_tortoise_fts_query"}}:\n        '
                                   f'{reach.format(name="_N")}\n'),
        "dict literal for target": (f'    for _N in {{"_tortoise_fts_query": 1}}:\n        '
                                    f'{reach.format(name="_N")}\n'),
        "set bound, then iterated": (f'    _S = {{"_tortoise_fts_query"}}\n'
                                     f'    for _N in _S:\n        {reach.format(name="_N")}\n'),
        "re-bind AFTER the reach": (f'    _N = "_tortoise_fts_query"\n    {reach.format(name="_N")}\n'
                                    '    _N = "done"\n'),
        # ⛔ `+=` THEN A SELF-REFERENTIAL `=` (#14326 round 14). This row is the fail-open
        # the two-phase apply produced: with every `+=` staged BEFORE every `=`, the product
        # of `_S = _S + "_query"` never saw `_tortoise_fts`, so the name the code actually
        # builds was missing from the set and this reach was refused=False (measured).
        "`+=` THEN a self-referential `=`": (
            '    _S = "_tortoise"\n    _S += "_fts"\n    _S = _S + "_query"\n'
            f'    {reach.format(name="_S")}\n'),
        # ⛔ A SELF-REFERENTIAL `+` BIND IS THE SAME REACH WRITTEN AS AN ACCUMULATION
        # (#14326 P1). Measured at the head this was found on, this row did not pass and did
        # not fail — `_str_constants` never returned, so the shard TIMED OUT. The resolver now
        # stages it once against the pre-bind values (`_s` keeps both spellings, the
        # fail-closed direction) and the reach is refused.
        "self-referential `+` bind": ('    _s = "_tortoise"\n    _s = _s + "_fts_query"\n'
                                      f'    {reach.format(name="_s")}\n'),
        "subscript STORE target": ('    _N = "_tortoise_fts_query"\n    _counts = {}\n'
                                   f'    _counts[_N] = "zero"\n    {reach.format(name="_N")}\n'),
        "name chained to name": ('    _N = "_tortoise_fts_query"\n    _A = _N\n'
                                 f'    {reach.format(name="_A")}\n'),
        "name carried in a tuple": ('    _N = "_tortoise_fts_query"\n    _pair, = (_N,)\n'
                                    f'    {reach.format(name="_pair")}\n'),
        "name carried into a splat": ('    _N = "_tortoise_fts_query"\n    _pair = (sdk, _N)\n'
                                      '    getattr(*_pair)(query=queries[0]["query"], limit=10, '
                                      'kind="statement")\n'),
        "literal carried into a splat": ('    _N = "_tortoise_fts_query"\n'
                                         '    getattr(*[sdk, _N])(query=queries[0]["query"], '
                                         'limit=10, kind="statement")\n'),
        # ⛔ THE BIND FORMS THAT CARRY NO `Assign` NODE AT ALL (#14326, each MEASURED green
        # at `3b98c08ce2` — a lever reached the read and this pin passed):
        "match capture of a bound name": ('    _V = "_tortoise_fts_query"\n'
                                          '    match _V:\n'
                                          '        case _N:\n'
                                          f'            {reach.format(name="_N")}\n'),
        "match capture of the literal": ('    match "_tortoise_fts_query":\n'
                                         '        case _N:\n'
                                         f'            {reach.format(name="_N")}\n'),
        "unpack from a NAME-HELD sequence": ('    _P = ("_tortoise_fts_query", "x")\n'
                                            '    _N, _M = _P\n'
                                            f'    {reach.format(name="_N")}\n'),
        "NESTED unpack of a NAME-HELD sequence": (
            '    _P = (("a", "b"), ("_tortoise_fts_query", "x"))\n'
            '    (_a, (_N, _o)) = _P\n'
            f'    {reach.format(name="_N")}\n'),
        "match capture of a DICT literal's value": (
            '    _m = {"read": "_tortoise_fts_query"}\n'
            '    match _m:\n'
            '        case {"read": _N}:\n'
            f'            {reach.format(name="_N")}\n'),
        "STARRED target against a literal": (
            '    (*_r, _b) = ("_tortoise_fts_query", "x")\n'
            f'    {reach.format(name="_r")}\n'),
        "SPLATTED literal into a container": (
            '    _P = [sdk, *("_tortoise_fts_query",)]\n'
            '    getattr(*_P)(query=queries[0]["query"], limit=10, kind="statement")\n'),
        "PARAMETER DEFAULT": (
            '    def _f(_N="_tortoise_fts_query", *, _k="x"):\n'
            '        getattr(sdk, _N)(query=queries[0]["query"], limit=10, '
            'kind="statement")\n'
            '    _f()\n'),
        # ⛔ EVERY OTHER SUB-CLAUSE OF THE DEFAULT ARM GETS ITS OWN ROW (#14326 review F1).
        # The row above is POSITIONAL only, and each of the three clauses below was MEASURED
        # deletable with the whole file still **25 passed** while the shape it guards went
        # fail-open: delete the `kw_defaults` loop -> `def _f(*, _N="…")` reaches the read
        # with the pin GREEN; drop `ast.Lambda` from the tuple -> the `lambda` default
        # GREEN; drop `posonlyargs` from the alignment -> `def _f(_N="…", /)` raises
        # `IndexError` out of `_str_constants` instead of resolving. A clause no row can red
        # is not a guard — the same finding `28598400` deleted `_splat_operands`' expansion
        # for, in the two arms added one commit later.
        "KW-ONLY parameter default": (
            '    def _f(*, _N="_tortoise_fts_query"):\n'
            f'        {reach.format(name="_N")}\n'
            '    _f()\n'),
        "LAMBDA parameter default": (
            '    (_f := lambda _N="_tortoise_fts_query": '
            f'{reach.format(name="_N")})()\n'),
        "POS-ONLY parameter default": (
            '    def _f(_N="_tortoise_fts_query", /):\n'
            f'        {reach.format(name="_N")}\n'
            '    _f()\n'),
        # ⛔ THE DEFAULT ARM'S **ALIGNMENT** ARITHMETIC, not just its presence (#14326
        # review, MEASURED at `bb89a48cee7e`): every pre-existing default row has exactly
        # ONE positional parameter, so `first = len(positional) - len(node.args.defaults)`
        # is always `0` and the subtraction was never exercised — replacing it with
        # `first = 0` left the whole file **25 passed** while `_N` went unresolved and a
        # third reach carrying `kind="statement"` was GREEN. Positional defaults align to
        # the TAIL of the positional parameters, so a PAD in front of the default is what
        # reds the clause.
        "PADDED positional default": (
            '    def _f(_pad, _N="_tortoise_fts_query"):\n'
            f'        {reach.format(name="_N")}\n'
            '    _f(1)\n'),
        # ⛔ …AND ITS KEYWORD-ONLY INDEX (#14326 review, MEASURED at `bb89a48cee7e`):
        # every kw-only default row has one parameter, so `kwonlyargs[index]` is always
        # `kwonlyargs[0]` and the index was unpinned — `kwonlyargs[0]` left **25 passed**
        # while `_N` (the SECOND kw-only parameter) resolved to nothing.
        "SECOND kw-only parameter default": (
            '    def _f(*, _a="x", _N="_tortoise_fts_query"):\n'
            f'        {reach.format(name="_N")}\n'
            '    _f()\n'),
        # ⛔ …AND THE MATCH ARM'S `rest=` HALF (#14326 review F1): `case {**rest}` binds the
        # remaining KEYS through `MatchMapping.rest`, which is an `identifier?` and NOT an
        # `identifier`-named capture, so `getattr(capture, "name")` alone never sees it.
        # Dropping the `rest` fallback left all 25 tests green and this shape GREEN.
        "match MAPPING REST capture": (
            '    _m = {"a": 1, "_tortoise_fts_query": 2}\n'
            '    match _m:\n'
            '        case {"a": 1, **rest}:\n'
            f'            {reach.format(name="rest")}\n'),
        # ⛔ A TRUE LITERAL splat (#14326 review P3): the row above is labelled "literal"
        # while binding a NAME, so nothing red the literal carrier itself.
        "LITERAL carried into a splat": ('    getattr(*[sdk, "_tortoise_fts_query"])'

                                         '(query=queries[0]["query"], limit=10, '
                                         'kind="statement")\n'),
        # ⛔ THE SHAPES `_possible_strings`' SET/DICT ARMS EXIST FOR (#14326 review F1,
        # each MEASURED GREEN at `285984005a` with the arms deleted and a third reach
        # carrying `kind="statement"`): a splat is a positional list, so a literal
        # container handed to it delivers operands no other row builds. `indirect` is the
        # firing condition for both — the label says which arm it pins. ⛔ Neither of these
        # is covered by the set/dict *for-target* rows, which reach `_values`, a DIFFERENT
        # function.
        "SET literal splatted into getattr": (
            '    _N = "_tortoise_fts_query"\n'
            '    getattr(*{sdk, _N})(query=queries[0]["query"], limit=10, '
            'kind="statement")\n'),
        "DICT literal splatted into getattr": (
            '    _N = "_tortoise_fts_query"\n'
            '    getattr(*{_N: 1})(query=queries[0]["query"], limit=10, '
            'kind="statement")\n'),
        # ⛔ A SELECTION BETWEEN LITERALS AND A PLACEHOLDER-FREE f-STRING ARE STATIC
        # (#14326 review P6, each MEASURED GREEN at `8f13cb24` — mentions 2, calls 2,
        # indirect 0 — so a third reach carrying `kind="statement"` passed the pin):
        # `f"_tortoise_fts_query"` parses to `JoinedStr([Constant(...)])` (nothing is
        # computed), and `IfExp`/`BoolOp` only SELECT between literals. The first two rows
        # pin the `_values` arms (a name-held value), the last three the `_possible_strings`
        # arms (the literal inline in the operand, which never reaches `_values`).
        "f-string bound to a name": (
            '    _N = f"_tortoise_fts_query"\n'
            f'    {reach.format(name="_N")}\n'),
        "IfExp bound to a name": (
            '    _N = "_tortoise_fts_query" if queries else "x"\n'
            f'    {reach.format(name="_N")}\n'),
        "BoolOp bound to a name": (
            '    _N = queries or "_tortoise_fts_query"\n'
            f'    {reach.format(name="_N")}\n'),
        "f-string INLINE in the getattr": (
            '    getattr(sdk, f"_tortoise_fts_query")'
            '(query=queries[0]["query"], limit=10, kind="statement")\n'),
        "IfExp INLINE in the getattr": (
            '    getattr(sdk, ("_tortoise_fts_query" if queries else "x"))'
            '(query=queries[0]["query"], limit=10, kind="statement")\n'),
        # ⛔ AND THE INLINE `BoolOp`, which had NO row (#14326 round 5, measured: deleting
        # `_possible_strings`' `BoolOp` arm left all 25 tests green — the bound row above
        # reaches `_values`, a DIFFERENT function, exactly as this file's own history records
        # for the set/dict arms). The inline selection never reaches `_values`, so the
        # `_possible_strings` arm was unpinned until this row existed.
        "BoolOp INLINE in the getattr": (
            '    getattr(sdk, (queries or "_tortoise_fts_query"))'
            '(query=queries[0]["query"], limit=10, kind="statement")\n'),
        # ⛔ BOTH ROWS BELOW BIND **BEFORE** THEIR SOURCE (#14326 review P3, measured):
        # `ast.walk` is breadth-first, not source order, so a single pass resolves only the
        # FAVOURABLE order. The fixpoint is load-bearing and had no row that red it — with
        # the loop replaced by one pass both of these stayed GREEN.
        "chain bound BEFORE its source": ('    _A = _N\n    _N = "_tortoise_fts_query"\n'
                                          f'    {reach.format(name="_A")}\n'),
        "unpack bound BEFORE its source": ('    _pair, = (_N,)\n'
                                           '    _N = "_tortoise_fts_query"\n'
                                           f'    {reach.format(name="_pair")}\n'),
        # ⛔ THE FETCHER NAME IS NOT THE HOLE; THE CONTAINER IS (#14326 round 5, EACH
        # MEASURED GREEN at `8f13cb24`: `refuses=False`, mentions=2, calls=2 — while the
        # DIRECT spelling of the same callee was refused, which is what proves it). The
        # literal rides inside a tuple/list that is SPLATTED into a callee that fetches an
        # attribute by name, so `_is_read_ref` sees the `Constant`'s parent as the container,
        # never the `Call`. All four rows below pin the SPLAT delivery, which is fail-closed
        # on ANY callee that is not provably pure — that is the arm whose deletion reds them.
        "`__getattribute__` over a splatted literal": (
            '    _g = sdk.__getattribute__(*("_tortoise_fts_query",))\n'
            '    _g(query=queries[0]["query"], limit=10, kind="statement")\n'),
        "`operator.attrgetter` over a NAME-carried tuple": (
            '    _A = ("_tortoise_fts_query",)\n'
            '    _lever = operator.attrgetter(*_A)(sdk)\n'
            '    _lever(query=queries[0]["query"], limit=10, kind="statement")\n'),
        "`dict.get` over a splatted mixed container": (
            '    _A = ("_tortoise_fts_query",)\n'
            '    _lever = dict.get(*[sdk.__dict__, *_A])\n'
            '    _lever(sdk)(query=queries[0]["query"], limit=10, kind="statement")\n'),
        "WALRUS inside the fetch subscript": (
            '    vars(type(sdk))[(_N := "_tortoise_fts_query")]'
            '(query=queries[0]["query"], limit=10, kind="statement")\n'),
        # ⛔ THE SAME ARM UNDER A CALLEE NAME NO SET COULD ENUMERATE (#14326 round 5): a
        # callable object fetches by name behind a splat, so a named-fetcher list cannot see
        # it — the ``case is the container, not the callee. This row is the arm's own
        # attribution: deleting the splat arm reds it.
        "UNKNOWN callee over a splatted literal": (
            '    _fetcher = _PageFetcher()\n'
            '    _lever = _fetcher(*("_tortoise_fts_query",))\n'
            '    _lever(sdk)(query=queries[0]["query"], limit=10, kind="statement")\n'),
        # ⛔ A KEYWORD IS A DELIVERY (#14326 round 6, BOTH MEASURED GREEN at `81973329`:
        # mentions=2, calls=2, indirect=0): `_name_reaching_operands` scanned `call.args`
        # only, and CPython puts a keyword in its own `keyword` node, so a literal handed to
        # a non-pure callee BY NAME escaped both the census and `_indirect_reads`. The second
        # row is a `**` mapping (`keyword.arg` is None there), so the value is inspected.
        "literal as a KEYWORD argument": (
            '    _fetcher = _PageFetcher()\n'
            '    _lever = _fetcher(name="_tortoise_fts_query")\n'
            '    _lever(sdk)(query=queries[0]["query"], limit=10, kind="statement")\n'),
        "literal inside a **MAPPING": (
            '    _fetcher = _PageFetcher()\n'
            '    _lever = _fetcher(**{"name": "_tortoise_fts_query"})\n'
            '    _lever(sdk)(query=queries[0]["query"], limit=10, kind="statement")\n'),
        # ⛔ THE OPERAND POSITION AND THE CALLEE NAME WERE BOTH WRONG (#14326 review P0-1,
        # EACH MEASURED GREEN at `cb5c2c81d9`: `refuses=False`, mentions=2, calls=2,
        # indirect=0, while the DIRECT spelling of each callee was refused). Two independent
        # clauses are pinned here, one row each, because they revert independently:
        #   * `[1:]` in `_name_reaching_operands` — a name ASSEMBLED from literals in the
        #     FIRST position is not the `Constant` arm's (that arm keys on exact equality with
        #     `_READ_NAMES`, and neither `"_tortoise"` nor `"_fts_query"` is in it), so the
        #     slice subtracted an operand the census had NOT counted;
        #   * `_NAMEFETCH_CALLEES` — the claim that `__getattribute__`/`.get`/any unknown
        #     callee "all deliver through a SPLAT" is false for the non-splat POSITIONAL
        #     delivery, so the shape gate dropped the fetch entirely.
        #
        # The `getattr` row reds on the POSITION clause. ⛔ THE PREVIOUS WORDING WAS FALSE and
        # is corrected at the source (#14326 review cycle 3, MEASURED by reverting each clause
        # and grading every row): ALL THREE of `getattr`, `__getattribute__` and `.get` carry the
        # assembled name at POSITION 0, so all three flip GREEN under a reversion of `[1:]`
        # (plus the `Starred` extend it deleted) — 16 rows flip in total, and `_helper` does not.
        # `__getattribute__`, `.get` and `_helper` ALSO red under a reversion of the
        # callee-shape gate; the `getattr` row does NOT, because its callee is in the restored
        # named pair. So the slice is pinned by the three position-0 rows and the gate by the
        # three whose callee the gate would have dropped.
        "`getattr` with the name assembled in the FIRST position": (
            '    getattr("_tortoise" + "_fts_query", sdk)'
            '(query=queries[0]["query"], limit=10, kind="statement")\n'),
        "`__getattribute__` over a split literal, NO keyword": (
            '    _g = sdk.__getattribute__("_tortoise" + "_fts_query")\n'
            '    _g(query=queries[0]["query"], limit=10, kind="statement")\n'),
        "`vars(type(sdk)).get(…)` over a split literal": (
            '    vars(type(sdk)).get("_tortoise" + "_fts_query")(sdk)'
            '(query=queries[0]["query"], limit=10, kind="statement")\n'),
        "UNKNOWN helper with the name in a LATER argument": (
            '    _helper(sdk, "_tortoise" + "_fts_query")\n'),
        # ⛔ CODE BUILT IN A STRING (#14326 review P0-3, EACH MEASURED GREEN at
        # `cb5c2c81d9`: mentions=2, calls=2, indirect=0 while the read executed). The census
        # asks whether an operand **IS** the read name; a code string only CONTAINS it, so
        # no operand arm can see it — `_CODE_EXECUTING_CALLEES` is fail-closed BY NAME. One
        # row per member, because a member with no row is a member a later lane can delete
        # with the suite green (this file's own round-21 finding).
        "`exec` of a static code string": (
            '    exec(\'results = sdk._tortoise_fts_query(query=queries[0]["query"], '
            'limit=10)\')\n'),
        "`eval` of a static expression string": (
            '    eval(\'sdk._tortoise_fts_query(query=queries[0]["query"], limit=10)\')\n'),
        "`compile` of a static code string": (
            '    _code = compile("sdk._tortoise_fts_query(query=x)", "<pin>", "exec")\n'
            '    exec(_code)\n'),
        # ⛔ AND THE RESOLVED SPELLINGS OF THE SAME CALLEE (#14326 review cycle 2, EACH
        # MEASURED GREEN with the bare-name arm alone: `mentions=2 calls=2 indirect=0`, and
        # `_run = exec` measured GREEN END-TO-END on the shipped tool). A statically visible
        # alias is not the pin's declared "computed at runtime" residual — this file's own
        # rule — so `_indirect_reads` resolves the set with `_aliases_of` /
        # `_builtin_lookup_name` instead of enumerating spellings. Deleting the resolution
        # (leaving `_CODE_EXECUTING_CALLEES` alone) reds these three rows and only these.
        "ALIASED `exec`": (
            '    _run = exec\n'
            "    _run('results = sdk._tortoise_fts_query(query=queries[0][\"query\"])')\n"),
        # ⛔ AN IMPORT BINDS THE NAME THE SAME WAY, AND THAT SPELLING WAS OPEN (#14326 fix
        # lane at `a12902a5`, EACH MEASURED GREEN at that head: `mentions=2 calls=2
        # indirect=0` while the read executed, and the planted `tortoise/sdk.py` resolved
        # end-to-end). This file's own rule is that a statically visible alias is NOT the
        # "computed at runtime" residual — yet the registry axis has collected
        # `from builtins import <member> as alias` since round 27 and `_aliases_of`'s SYS arm
        # has collected `from sys import modules as _m` since round 20, so the code-execution
        # axis reading only the ASSIGNMENT spelling was an in-file asymmetry, one module
        # over. Deleting the `builtins` half of that arm reds these three rows and only
        # these; one row per form, because a form with no row is one a later lane can drop
        # with the suite green.
        "IMPORTED `exec` (`from builtins import exec as _run`)": (
            '    from builtins import exec as _run\n'
            "    _run('results = sdk._tortoise_fts_query(query=queries[0][\"query\"])')\n"),
        "IMPORTED `compile`, handed to an ALIASED `exec`": (
            '    from builtins import compile as _c\n'
            '    _run = exec\n'
            "    _run(_c('sdk._tortoise_fts_query(query=x)', '<pin>', 'exec'))\n"),
        "an ALIAS OF AN IMPORTED `exec`": (
            '    from builtins import exec as _run\n'
            '    _r2 = _run\n'
            "    _r2('results = sdk._tortoise_fts_query(query=queries[0][\"query\"])')\n"),
        "`getattr(builtins, \"exec\")` — the module's own MAPPING": (
            '    import builtins\n'
            '    getattr(builtins, "exec")('
            "'results = sdk._tortoise_fts_query(query=queries[0][\"query\"])')\n"),
        # ⛔ THE RECEIVER, THE KEY AND THE IMPORTED FETCHER (#14326 cross-lane review of
        # `f7f5b763fb`, EACH MEASURED GREEN while the read executed — and each with a RED twin
        # in this file, which is what makes it an asymmetry rather than a boundary):
        #
        #     getattr(builtins.__dict__, "exec")(<read>)              GREEN (dict.get took it)
        #     _K = "__dict__"; _d = getattr(builtins, _K); _d[..]     GREEN (`_string_keys_of`
        #                                                              resolves every OTHER key)
        #     from builtins import getattr as _g; _g(builtins, "exec") GREEN (the `_g = getattr`
        #                                                              twin is RED)
        #
        # Deleting the receiver's `_is_builtins_mapping` disjunct, the `_string_keys_of` key
        # resolution, or `_REGISTRY_BUILTINS` from the ImportFrom member set reds exactly its
        # own row below, measured.
        "INLINE mapping as `getattr`'s receiver": (
            '    import builtins\n'
            '    getattr(builtins.__dict__, "exec")('
            "'results = sdk._tortoise_fts_query(query=queries[0][\"query\"])')\n"),
        "NAME-bound `__dict__` key": (
            '    import builtins\n'
            '    _K = "__dict__"\n'
            '    _d = getattr(builtins, _K)\n'
            '    _d["exec"]('
            "'results = sdk._tortoise_fts_query(query=queries[0][\"query\"])')\n"),
        "FETCHER imported from the module": (
            '    from builtins import getattr as _g\n'
            '    _g(builtins, "exec")('
            "'results = sdk._tortoise_fts_query(query=queries[0][\"query\"])')\n"),
        # ⛔ THE MAPPING IMPORTED FROM THE MODULE, AND ITS TWO REMAINING COPY SPELLINGS
        # (#14326 review cycle 5, EACH MEASURED GREEN while a third reach executed the read,
        # EACH with a RED twin one node-shape over): the builtins `ImportFrom` member set
        # carried `__dict__` for `sys` since round 28 but not for `builtins`, while
        # `_builtins_root_names`' whole job is to resolve every spelling of that mapping;
        # `copy.deepcopy(m)` was GREEN while `copy.copy(m)` was RED (a deep copy still holds
        # the same function objects at its leaves); and the IMPORT arm synthesizes
        # `builtins.dict`, a node shape `_builtin_name_aliases` could not resolve — so the
        # imported constructor was GREEN while the assigned one was RED. Deleting
        # `__dict__` from the member set reds the first row, the `deepcopy` member from either
        # copy arm the second, and the module-attribute disjunct the third.
        "MAPPING imported from the module (`from builtins import __dict__`)": (
            '    from builtins import __dict__ as _d\n'
            '    _d["exec"]('
            "'results = sdk._tortoise_fts_query(query=queries[0][\"query\"])')\n"),
        "COPY of the mapping via `copy.deepcopy`": (
            '    import builtins, copy\n'
            '    _x = copy.deepcopy(builtins.__dict__)\n'
            '    _x["exec"]("sdk._tortoise_fts_query(query=q, limit=10)")\n'),
        "COPY of the mapping via the IMPORTED `dict`": (
            '    from builtins import dict as _dd\n'
            '    _x = _dd(builtins.__dict__)\n'
            '    _x["exec"]("sdk._tortoise_fts_query(query=q, limit=10)")\n'),
        "`builtins.__dict__[\"exec\"]` — the mapping SUBSCRIPT": (
            '    import builtins\n'
            '    builtins.__dict__["exec"]('
            "'results = sdk._tortoise_fts_query(query=queries[0][\"query\"])')\n"),
        # ⛔ THE MAPPING'S OWN ALIAS, THE IMPORTED CALLEE, AND THE ALIASED FETCHER (#14326
        # review cycle 3, EACH MEASURED GREEN while the read executed):
        #
        #     _d = builtins.__dict__; _d["exec"](<read>)          m=2 c=2 i=0
        #     from builtins import exec as _e; _e(<read>)         m=2 c=2 i=0
        #     _g = getattr; _g(builtins, "exec")(<read>)          m=2 c=2 i=0
        #     dict.get(_d, "exec")(<read>)                        m=2 c=2 i=0
        #
        # Each is a STATIC bind the file's own resolvers already handle one axis over
        # (`__d = sys.__dict__` is RED; `imported_builtins` collects `from builtins import …`;
        # `_g = getattr` is RED on the registry axis since round 21), so leaving them raw here
        # was an in-file asymmetry. One row per clause: `_builtins_root_names`' mapping
        # aliases, the `from builtins import` arm, and `_builtin_lookup_name`'s resolvable
        # FETCHER names.
        "ALIASED builtins MAPPING": (
            '    import builtins\n'
            '    _d = builtins.__dict__\n'
            '    _d["exec"]("results = sdk._tortoise_fts_query(query=q, limit=10)")\n'),
        "ALIASED `getattr` fetching `exec`": (
            '    import builtins\n'
            '    _g = getattr\n'
            '    _g(builtins, "exec")("sdk._tortoise_fts_query(query=q, limit=10)")\n'),
        # ⛔ AND THE SAME FETCHER ONE HOP FURTHER OUT (#14326 round 35, MEASURED GREEN while
        # its one-hop sibling above was RED): the fetcher set was threaded into the PER-CALL
        # lookup but not into `_code_executing_names`' alias CLOSURE, so a callee the closure
        # would have collected was never collected. Passing the set to both is what makes the
        # one-hop row above and this two-hop row the SAME decision rather than two.
        "ALIASED `getattr` result RE-BOUND to a name": (
            '    import builtins\n'
            '    _g = getattr\n'
            '    _x = _g(builtins, "exec")\n'
            '    _x("sdk._tortoise_fts_query(query=q, limit=10)")\n'),
        "`dict.get` over the ALIASED mapping": (
            '    import builtins\n'
            '    _d = builtins.__dict__\n'
            '    dict.get(_d, "exec")("sdk._tortoise_fts_query(query=q, limit=10)")\n'),
        # ⛔ AND THE MAPPING'S OWN METHODS, ONE SPELLING OVER THAT SUBSCRIPT (#14326 round 35,
        # EACH MEASURED GREEN at `a12902a5d4` while the `m["exec"]` subscript of the IDENTICAL
        # mapping was RED). No row here can ride the `dict.get(mapping, key)` rows above: that
        # arm keys on `node.func.value.id in dict_fetchers`, which an ATTRIBUTE receiver
        # (`builtins.__dict__`) can never satisfy — so the RECEIVER half needed its own arm,
        # not a wider fetcher set. One row per spelling, because a spelling with no row is one
        # a later lane deletes with the suite green (this file's own round-21 finding).
        "`builtins.__dict__.get(\"exec\")` — the mapping's BOUND `.get`": (
            '    import builtins\n'
            '    builtins.__dict__.get("exec")('
            "'results = sdk._tortoise_fts_query(query=q, limit=10)')\n"),
        "`builtins.__dict__.pop(\"exec\")` — the mapping's BOUND `.pop`": (
            '    import builtins\n'
            '    builtins.__dict__.pop("exec")('
            "'results = sdk._tortoise_fts_query(query=q, limit=10)')\n"),
        "`vars(builtins).get(\"exec\")` — BOUND `.get` on the `vars` composition": (
            '    import builtins\n'
            '    vars(builtins).get("exec")('
            "'results = sdk._tortoise_fts_query(query=q, limit=10)')\n"),
        # ⛔ AND THE BINDING-FREE TWIN: `__builtins__` IS the mapping in an imported module, so
        # the receiver is a NAME in `dict_names` rather than an attribute — the arm the
        # subscript case has taken since round 30, which the method-call case was missing.
        "`__builtins__.get(\"exec\")` — the mapping by NAME, NO import": (
            '    _d = __builtins__\n'
            '    _d.get("exec")('
            "'results = sdk._tortoise_fts_query(query=q, limit=10)')\n"),
        # ⛔ AND THE SPELLINGS THE FIRST MERGE REVERTED (#14326 round 35, MEASURED GREEN at
        # `a05827d2351c` and RED again here). The merge that combined this lane with the
        # sibling's fetcher work kept the fetchers and dropped the COMPOSITION half, so each of
        # these executed the read with the pin GREEN. They are rows, not prose, because the
        # reversion left the suite at 43 passed — an arm with no row is one a merge can drop.
        "ALIASED `vars` composing the builtins MAPPING": (
            '    _v = vars\n'
            '    _v(builtins)["exec"]('
            "'results = sdk._tortoise_fts_query(query=q, limit=10)')\n"),
        "ALIASED `getattr` composing the builtins MAPPING": (
            '    _gr = getattr\n'
            '    _gr(builtins, "__dict__")["exec"]('
            "'results = sdk._tortoise_fts_query(query=q, limit=10)')\n"),
        # ⛔ THE UNBOUND FAMILY IS DECIDED BY ARGUMENT SHAPE, NOT BY CALLEE (#14326 round 35):
        # a `attr == "get"` + `value.id in dict_fetchers` gate left these GREEN while their
        # BOUND twins were closed by the same `_BUILTINS_MAPPING_READS` set.
        "`dict.pop(builtins.__dict__, \"exec\")` — UNBOUND, callee irrelevant": (
            '    import builtins\n'
            '    dict.pop(builtins.__dict__, "exec")('
            "'results = sdk._tortoise_fts_query(query=q, limit=10)')\n"),
        "`dict.setdefault(builtins.__dict__, \"exec\")` — UNBOUND": (
            '    import builtins\n'
            '    dict.setdefault(builtins.__dict__, "exec")('
            "'results = sdk._tortoise_fts_query(query=q, limit=10)')\n"),
        # ⛔ THE FETCHER IN ITS ATTRIBUTE SPELLING, AND AN ALIAS OF IT (#14326 round 35): the
        # fetcher set was read off `func.id`, so the identical attribute spelling of the
        # identical builtin was invisible while the registry axis reds `builtins.getattr(...)`.
        "`builtins.getattr` — the fetcher as an ATTRIBUTE": (
            '    import builtins\n'
            '    builtins.getattr(builtins, "exec")('
            "'results = sdk._tortoise_fts_query(query=q, limit=10)')\n"),
        "`__builtins__[\"getattr\"]` — the fetcher through the MAPPING": (
            '    __builtins__["getattr"](builtins, "exec")('
            "'results = sdk._tortoise_fts_query(query=q, limit=10)')\n"),
        "ALIASED `builtins.getattr`": (
            '    import builtins\n'
            '    _gf = builtins.getattr\n'
            '    _gf(builtins, "exec")('
            "'results = sdk._tortoise_fts_query(query=q, limit=10)')\n"),
        # ⛔ AND A COPY OF THE MAPPING IS THE SAME LEVER (#14326 round 35, MEASURED GREEN):
        # the copy holds the SAME function objects, so reading `exec` out of it executes
        # exactly what reading it out of the original does.
        "`dict(builtins.__dict__)` — a COPY of the mapping": (
            '    import builtins\n'
            '    _d = dict(builtins.__dict__)\n'
            '    _d["exec"]("results = sdk._tortoise_fts_query(query=q, limit=10)")\n'),
        "`builtins.__dict__.copy()`": (
            '    import builtins\n'
            '    _d = builtins.__dict__.copy()\n'
            '    _d.get("exec")("results = sdk._tortoise_fts_query(query=q, limit=10)")\n'),
        # ⛔ THE COPY FAMILY BY SHAPE, AND `__getitem__` (#14326 round 35 review cycle 4,
        # EACH MEASURED GREEN while a third reach executed the read). Resolving only the two
        # literal copy spellings left the canonical `{**m}` GREEN; `__getitem__` is the
        # SUBSCRIPT'S own dunder and `_BUILTINS_MAPPING_READS`' rule ("methods that RETURN the
        # member stored under a key") names it — the registry axis already reds
        # `dict.__getitem__(sys.__dict__, "modules")` one axis over.
        "`{**builtins.__dict__}` — a `**` SPREAD": (
            '    import builtins\n'
            '    _d = {**builtins.__dict__}\n'
            '    _d["exec"]("results = sdk._tortoise_fts_query(query=q, limit=10)")\n'),
        "`builtins.__dict__ | {}` — a UNION copy": (
            '    import builtins\n'
            '    _d = builtins.__dict__ | {}\n'
            '    _d["exec"]("results = sdk._tortoise_fts_query(query=q, limit=10)")\n'),
        "`_dc = dict; _dc(builtins.__dict__)` — an ALIASED constructor": (
            '    import builtins\n'
            '    _dc = dict\n'
            '    _d = _dc(builtins.__dict__)\n'
            '    _d["exec"]("results = sdk._tortoise_fts_query(query=q, limit=10)")\n'),
        "`builtins.__dict__.__getitem__(\"exec\")`": (
            '    import builtins\n'
            '    builtins.__dict__.__getitem__("exec")('
            "'results = sdk._tortoise_fts_query(query=q, limit=10)')\n"),
        "`dict.__getitem__(builtins.__dict__, \"exec\")`": (
            '    import builtins\n'
            '    dict.__getitem__(builtins.__dict__, "exec")('
            "'results = sdk._tortoise_fts_query(query=q, limit=10)')\n"),
        # ⛔ THE INTRA-PREDICATE TWINS OF THE CONSTRUCTORS ALREADY CLOSED (#14326 round 35
        # review cycle 5, EACH MEASURED GREEN: `dict(**m)["exec"] is exec` at RUNTIME, and the
        # pin saw `m=2 c=2 i=0`). A `**` spread lives in `node.keywords` with `arg is None`, an
        # unbound `.copy` puts the mapping in argument ONE, and `|` has an `__or__` dunder —
        # three twins of three arms that were otherwise closed.
        "`dict(**builtins.__dict__)` — the KEYWORD twin": (
            '    import builtins\n'
            '    _d = dict(**builtins.__dict__)\n'
            '    _d["exec"]("results = sdk._tortoise_fts_query(query=q, limit=10)")\n'),
        "`dict.copy(builtins.__dict__)` — the UNBOUND twin": (
            '    import builtins\n'
            '    _d = dict.copy(builtins.__dict__)\n'
            '    _d["exec"]("results = sdk._tortoise_fts_query(query=q, limit=10)")\n'),
        "`{}.__or__(builtins.__dict__)` — the DUNDER twin of `| `": (
            '    import builtins\n'
            '    _d = {}.__or__(builtins.__dict__)\n'
            '    _d["exec"]("results = sdk._tortoise_fts_query(query=q, limit=10)")\n'),
        # ⛔ AND A SUBSCRIPT WHOSE KEY IS ASSEMBLED (#14326 review P0-2, MEASURED GREEN at
        # `cb5c2c81d9`: mentions=2, calls=2, indirect=0) — while the DIRECT key is refused by
        # the `Constant` arm and the SLICED key by `_possible_strings`. The key is the same
        # axis one form over, so the arm asks `_possible_strings` the question the `Constant`
        # arm asks the literal. Deleting the arm reds this row; the store twin below it stays
        # GREEN (`ctx` is preserved), which is the polarity the recording direction needs.
        "subscript key ASSEMBLED from literals": (
            '    vars(type(sdk))["_tortoise" + "_fts_query"](sdk)'
            '(query=queries[0]["query"], limit=10, kind="statement")\n'),
        "subscript key assembled INLINE as an f-string": (
            '    vars(type(sdk))[f"_tortoise_{\'fts_query\'}"](sdk)'
            '(query=queries[0]["query"], limit=10, kind="statement")\n'),
        # ⛔ THE LEVER THE `Name` ARM EXISTS FOR (#14326 round 6, measured): NO row
        # exercised it — every other row was refused by `indirect`, so deleting the whole
        # `Name` arm left 25 green while `vars(type(sdk))[_N]` (its own comment's example)
        # went from REFUSED to `refuses=False`. This row is that arm's attribution.
        "NAME as the fetch subscript's INDEX": (
            '    _N = "_tortoise_fts_query"\n'
            '    vars(type(sdk))[_N]'
            '(query=queries[0]["query"], limit=10, kind="statement")\n'),
        # ⛔ THE `Name` ARM'S **CALL** HALF, which no row reached (#14326 review, MEASURED
        # at `bb89a48cee7e`): every other row routes its third reach through `getattr(sdk,
        # …)`, and `_indirect_reads` runs BEFORE the mentions census, so the Name-`Call`
        # clause was never the failing clause for any row — deleting it left the whole file
        # **25 passed** while a bound literal handed to an unknown DISPATCHER (which then
        # fetches and executes the read) went uncensused. The callee is deliberately unknown
        # rather than `getattr`, so `_indirect_reads` cannot cover for the census.
        "NAME handed to an unknown dispatcher": (
            '    _N = "_tortoise_fts_query"\n'
            '    _dispatch(_N)(sdk)'
            '(query=queries[0]["query"], limit=10, kind="statement")\n'),
        # ⛔ A REBOUND PURE NAME IS NOT PURE (#14326 round 6, measured GREEN): the purity
        # tests decide by NAME, so `print = getattr` launder the alias through an allow-
        # listed name — `_g = getattr` was already refused, this spelling was not. A
        # shadowed `_PURE_READS` name is graded as an unknown (fail-closed) instead.
        "REBOUND pure name as the fetcher": (
            '    print = getattr\n'
            '    print(sdk, "_tortoise_fts_query")'
            '(query=queries[0]["query"], limit=10, kind="statement")\n'),
        # ⛔ THE PAIRING ARM NEEDS EQUAL LENGTHS (#14326 round 6, measured): without the
        # guard a SHORTER target silently pairs against a LONGER value, so the second
        # element is never bound and the lever goes uncensused.
        "SHORTER target against a longer literal tuple": (
            '    (_N,) = ("a", "_tortoise_fts_query")\n'
            f'    {reach.format(name="_N")}\n'),
        # ⛔ `setattr` HAS NO POSITIONAL ROW ANYWHERE ELSE (#14326 round 6, measured):
        # removing it from the named pair left 25 green, because every other row reaches
        # the pair through a SPLAT. A fetcher that WRITES by name reaches the read's name too.
        "`setattr` over an f-string name": (
            '    setattr(sdk, f"_tortoise_fts_query", 1)\n'),
        # ⛔ A REBOUND PURE NAME IS ONE SPELLING OUT (#14326 round 7, EACH MEASURED GREEN at
        # `d790e1f0` while the `print = getattr` row above was REFUSED): `_rebound_callee_names`
        # enumerated `Assign`/`AnnAssign`/`AugAssign`/`NamedExpr`/`for`/`except`/`def`/`class`/
        # `arg` — every bind form EXCEPT an import, a `match` capture and a `with` target, so
        # the same laundering came back through each of them. Every row below reds on
        # deleting its arm, measured.
        "from-import binding a pure name": (
            '    from builtins import getattr as print\n'
            '    print(sdk, "_tortoise_fts_query")'
            '(query=queries[0]["query"], limit=10, kind="statement")\n'),
        "`from x import *` binds any allow-listed name": (
            '    from builtins import *\n'
            '    print(sdk, "_tortoise_fts_query")'
            '(query=queries[0]["query"], limit=10, kind="statement")\n'),
        "`match` CAPTURE binding a pure name": (
            '    match getattr:\n'
            '        case print:\n'
            '            print(sdk, "_tortoise_fts_query")'
            '(query=queries[0]["query"], limit=10, kind="statement")\n'),
        "`with … as` binding a pure name": (
            '    with _mk() as print:\n'
            '        print(sdk, "_tortoise_fts_query")'
            '(query=queries[0]["query"], limit=10, kind="statement")\n'),
        # ⛔ THE ALLOW-LISTS ARE CONSULTED BY `id` **OR** `attr`, SO AN ATTRIBUTE REBIND
        # LAUNDERS THEM TOO (#14326 round 7, EACH MEASURED GREEN at `d790e1f0`): dropping a
        # NAME the source assigns closed only half the hole — `_buf.write = getattr` then
        # `_buf.write(sdk, …)` passed (`m=2 c=2 i=0`), and so did `_mod.print = getattr`,
        # because `write` is a `_RECORDING_CALLEES` entry and `print` is a `_PURE_READS` one.
        # `_bind_target_names` therefore takes an attribute target's LAST SEGMENT.
        "ATTRIBUTE rebind of a RECORDING callee": (
            '    _buf.write = getattr\n'
            '    _buf.write(sdk, "_tortoise_fts_query")'
            '(query=queries[0]["query"], limit=10, kind="statement")\n'),
        "ATTRIBUTE rebind of a PURE callee": (
            '    _mod.print = getattr\n'
            '    _mod.print(sdk, "_tortoise_fts_query")'
            '(query=queries[0]["query"], limit=10, kind="statement")\n'),
        # ⛔ AND THE SAME NAME CARRIED AS A STRING CONSTANT (#14326 review at `c380075b`,
        # EACH MEASURED GREEN: `m=2 c=2 i=0` with a third reach executing the read). The
        # attribute spelling directly above was REFUSED while these were not, which is what
        # makes it an IN-FILE ASYMMETRY rather than a declared boundary: a `setattr` name
        # argument and a string-keyed store into the namespace mapping bind the callee name
        # exactly as an attribute segment does. `_namespace_rebind_names` takes all three.
        "setattr() rebinding a PURE callee": (
            '    import builtins as _bi\n'
            '    setattr(_bi, "print", getattr)\n'
            '    print(sdk, "_tortoise_fts_query")'
            '(query=queries[0]["query"], limit=10, kind="statement")\n'),
        "`globals()` string-key store rebinding a PURE callee": (
            '    globals()["print"] = getattr\n'
            '    print(sdk, "_tortoise_fts_query")'
            '(query=queries[0]["query"], limit=10, kind="statement")\n'),
        "`vars()` string-key store rebinding a RECORDING callee": (
            '    import builtins\n'
            '    vars(builtins)["write"] = getattr\n'
            '    write(sdk, "_tortoise_fts_query")'
            '(query=queries[0]["query"], kind="statement")\n'),
        "`__dict__` string-key store rebinding a PURE callee": (
            '    import builtins\n'
            '    builtins.__dict__["print"] = getattr\n'
            '    print(sdk, "_tortoise_fts_query")'
            '(query=queries[0]["query"], limit=10, kind="statement")\n'),
        "`__dict__.update({…})` rebinding a PURE callee": (
            '    import builtins\n'
            '    builtins.__dict__.update({"print": getattr})\n'
            '    print(sdk, "_tortoise_fts_query")'
            '(query=queries[0]["query"], limit=10, kind="statement")\n'),
        # ⛔ AND THE SPELLINGS A SYNTAX-MATCHING ARM MISSED (#14326 review at `36a6451d`,
        # EACH MEASURED GREEN through the pin's own predicate AND end-to-end on the real
        # tool): the LAUNDERER, the RECEIVER, and the KEY are each resolvable, so a match on
        # the literal `setattr`/`__dict__`/constant only moves the bypass one hop out. Each
        # row below is one of those hops; `_is_setattr_like`, `_is_namespace_expr` and
        # `_string_keys_of` resolve them with machinery this file already owns.
        "ALIASED `setattr` (a name bound to the launderer)": (
            '    import builtins as _bi\n'
            '    _sa = setattr\n'
            '    _sa(_bi, "print", getattr)\n'
            '    print(sdk, "_tortoise_fts_query")'
            '(query=queries[0]["query"], limit=10, kind="statement")\n'),
        "`setattr` through `functools.partial`": (
            '    import builtins as _bi, functools\n'
            '    functools.partial(setattr, _bi)("print", getattr)\n'
            '    print(sdk, "_tortoise_fts_query")'
            '(query=queries[0]["query"], limit=10, kind="statement")\n'),
        "NAME-carried key in a `setattr`": (
            '    import builtins as _bi\n'
            '    _p = "print"\n'
            '    setattr(_bi, _p, getattr)\n'
            '    print(sdk, "_tortoise_fts_query")'
            '(query=queries[0]["query"], limit=10, kind="statement")\n'),
        # ⛔ THE KEY'S POSITION IS THE SPELLING'S (#14326 review P1, EACH MEASURED GREEN at
        # `b6be337` through the pin's own predicate: `census=(2, 2, 0)`, `refuses=False`, the
        # read executing with `kind="statement"`). The inline `partial` row above passed only
        # because the arm read the CALL's `args[0]`; an ALIAS of the partial was classified
        # as a bare launderer and read `args[1]` — the VALUE — while a key PRE-BOUND in the
        # partial was never read at all. `_setattr_key_operands` resolves the position.
        "ALIASED `functools.partial(setattr, …)` (key at the call)": (
            '    import builtins as _bi, functools\n'
            '    _p = functools.partial(setattr, _bi)\n'
            '    _p("print", getattr)\n'
            '    print(sdk, "_tortoise_fts_query")'
            '(query=queries[0]["query"], limit=10, kind="statement")\n'),
        "PRE-BOUND key in `functools.partial(setattr, …)`": (
            '    import builtins as _bi, functools\n'
            '    functools.partial(setattr, _bi, "print")(getattr)\n'
            '    print(sdk, "_tortoise_fts_query")'
            '(query=queries[0]["query"], limit=10, kind="statement")\n'),
        "ALIASED `partial` with a PRE-BOUND key": (
            '    import builtins as _bi, functools\n'
            '    _p = functools.partial(setattr, _bi, "print")\n'
            '    _p(getattr)\n'
            '    print(sdk, "_tortoise_fts_query")'
            '(query=queries[0]["query"], limit=10, kind="statement")\n'),
        "`globals().update({…})` rebinding a PURE callee": (
            '    globals().update({"print": getattr})\n'
            '    print(sdk, "_tortoise_fts_query")'
            '(query=queries[0]["query"], limit=10, kind="statement")\n'),
        "`vars(x).update({…})` rebinding a PURE callee": (
            '    import builtins as _bi\n'
            '    vars(_bi).update({"print": getattr})\n'
            '    print(sdk, "_tortoise_fts_query")'
            '(query=queries[0]["query"], limit=10, kind="statement")\n'),
        "ALIASED namespace RECEIVER": (
            '    import builtins as _bi\n'
            '    _ns = _bi.__dict__\n'
            '    _ns["print"] = getattr\n'
            '    print(sdk, "_tortoise_fts_query")'
            '(query=queries[0]["query"], limit=10, kind="statement")\n'),
        "`__setitem__` spelling of the namespace store": (
            '    globals().__setitem__("print", getattr)\n'
            '    print(sdk, "_tortoise_fts_query")'
            '(query=queries[0]["query"], limit=10, kind="statement")\n'),
        # ⛔ AND THE RESOLVED RECEIVER, THE UNBOUND SPELLING, THE SPLAT AND THE `+` KEY
        # (#14326 review cycle 3 at `1a1e6a47`, EACH MEASURED GREEN through the predicate
        # and at runtime — the rebind is what made the reach execute): each is the same
        # root one resolution further out, and each is closed by the resolver rather than by
        # another constant-match.
        "`getattr(x, \"__dict__\")` RECEIVER": (
            '    import builtins\n'
            '    getattr(builtins, "__dict__")["print"] = getattr\n'
            '    print(sdk, "_tortoise_fts_query")'
            '(query=queries[0]["query"], limit=10, kind="statement")\n'),
        "UNBOUND `dict.__setitem__(<ns>, …)`": (
            '    import builtins\n'
            '    dict.__setitem__(builtins.__dict__, "print", getattr)\n'
            '    print(sdk, "_tortoise_fts_query")'
            '(query=queries[0]["query"], limit=10, kind="statement")\n'),
        "`setdefault` on the NAMESPACE (positional key)": (
            '    import builtins\n'
            '    builtins.__dict__.setdefault("print", getattr)\n'
            '    print(sdk, "_tortoise_fts_query")'
            '(query=queries[0]["query"], limit=10, kind="statement")\n'),
        # ⛔ THE KEY IS NOT ALWAYS POSITIONAL NOR ALWAYS A DISPLAY (#14326 review cycle 3 at
        # `80d2ab52`, EACH MEASURED GREEN through the predicate AND at runtime — the read
        # executed: `pin_refuses=False`, `m=2 c=2 i=0`). All four are the same root one
        # resolution further out, and each reds on deleting the arm it needs: a keyword's
        # NAME is a key, `dict(…)`'s keyword is a key, a `**` display's keys are keys, and an
        # UNBOUND mutator carries the namespace in `args[0]`.
        "`update(<name>=…)` KEYWORD key": (
            '    import builtins\n'
            '    builtins.__dict__.update(print=getattr)\n'
            '    print(sdk, "_tortoise_fts_query")'
            '(query=queries[0]["query"], limit=10, kind="statement")\n'),
        "`update(dict(<name>=…))` CALL-built mapping": (
            '    import builtins\n'
            '    builtins.__dict__.update(dict(print=getattr))\n'
            '    print(sdk, "_tortoise_fts_query")'
            '(query=queries[0]["query"], limit=10, kind="statement")\n'),
        # ⛔ AND THE SAME `dict(…)` CALL WITH ITS MAPPING **POSITIONALLY** (#14326 review
        # cycle 3, MEASURED GREEN at `8e4ffbc5a0`: `pin_refuses=False`, `m=2 c=2 i=0` with the
        # read executing). The keyword half of a `dict(…)` was resolved and its positional
        # display was not, so the verdict was decided by the argument SLOT the mapping
        # arrived in — the `dict(<name>=…)` row directly above cannot red this one.
        "`update(dict({…}))` POSITIONAL display": (
            '    import builtins\n'
            '    builtins.__dict__.update(dict({"print": getattr}))\n'
            '    print(sdk, "_tortoise_fts_query")'
            '(query=queries[0]["query"], limit=10, kind="statement")\n'),
        "`update(**{…})` SPREAD mapping": (
            '    import builtins\n'
            '    builtins.__dict__.update(**{"print": getattr})\n'
            '    print(sdk, "_tortoise_fts_query")'
            '(query=queries[0]["query"], limit=10, kind="statement")\n'),
        # ⛔ A NESTED `**` DISPLAY INSIDE ONE IS THE SAME DELIVERY (#14326 review cycle 3):
        # `update({**{"print": getattr}})` puts the key one display down, so the `key is
        # None` arm recurses on the VALUE — skipping it left this row GREEN.
        "NESTED `**` display inside an `update` mapping": (
            '    import builtins\n'
            '    builtins.__dict__.update({**{"print": getattr}})\n'
            '    print(sdk, "_tortoise_fts_query")'
            '(query=queries[0]["query"], limit=10, kind="statement")\n'),
        "UNBOUND `dict.setdefault(<ns>, …)`": (
            '    import builtins\n'
            '    dict.setdefault(builtins.__dict__, "print", getattr)\n'
            '    print(sdk, "_tortoise_fts_query")'
            '(query=queries[0]["query"], limit=10, kind="statement")\n'),
        "SPLATTED `setattr(*[…])` argument list": (
            '    import builtins as _bi\n'
            '    setattr(*[_bi, "print", getattr])\n'
            '    print(sdk, "_tortoise_fts_query")'
            '(query=queries[0]["query"], limit=10, kind="statement")\n'),
        "literal-only `+` KEY in a `setattr`": (
            '    import builtins as _bi\n'
            '    setattr(_bi, "pr" + "int", getattr)\n'
            '    print(sdk, "_tortoise_fts_query")'
            '(query=queries[0]["query"], limit=10, kind="statement")\n'),
        # ⛔ THE REMAINING BIND FORMS AND DELIVERY SPELLINGS (#14326 round 7, EACH MEASURED
        # GREEN while its arm was missing). The rebind audit reaches the census from THREE
        # places — the `Name` arm, the `Constant` arm and `_indirect_reads` — and a laundering
        # alias combined with a name-carried or splatted container was refused by only the
        # last of them until these rows existed.
        "REBOUND pure name + NAME-carried literal": (
            '    print = getattr\n'
            '    _N = "_tortoise_fts_query"\n'
            '    print(sdk, _N)(query=queries[0]["query"], limit=10, kind="statement")\n'),
        "REBOUND pure name + splatted container": (
            '    print = getattr\n'
            '    _p = (sdk, "_tortoise_fts_query")\n'
            '    print(*_p)(query=queries[0]["query"], limit=10, kind="statement")\n'),
        # ⛔ THE GENERIC `Name(Store)` SPELLINGS (#14326 round 8, EACH MEASURED GREEN through
        # the real lane after the merge adopted a per-FORM enumeration): an annotated bind, a
        # walrus, a `for` target and an augmented bind are all `Name(Store)` nodes — one walk
        # covers them, and each was a live fail-open without it.
        "AnnAssign rebinding a pure callee": (
            '    print: object = getattr\n'
            '    print(sdk, "_tortoise_fts_query")'
            '(query=queries[0]["query"], limit=10, kind="statement")\n'),
        "WALRUS rebinding a pure callee": (
            '    (print := getattr)\n'
            '    print(sdk, "_tortoise_fts_query")'
            '(query=queries[0]["query"], limit=10, kind="statement")\n'),
        "`for` TARGET rebinding a pure callee": (
            '    for print in (getattr,):\n'
            '        print(sdk, "_tortoise_fts_query")'
            '(query=queries[0]["query"], limit=10, kind="statement")\n'),
        "AUGMENTED bind rebinding a pure callee": (
            '    print += getattr\n'
            '    print(sdk, "_tortoise_fts_query")'
            '(query=queries[0]["query"], limit=10, kind="statement")\n'),
        # ⛔ THE LITERAL IS BUILT, NOT WRITTEN (#14326 review at `a29f3cd8`, each MEASURED
        # GREEN at `80d2ab52` while the `+` spellings beside them were refused):
        # `_str_constants` enumerated every bind form EXCEPT `AugAssign` — while every other
        # enumerator in this file carries it — and neither resolver had a `*`, so the verdict
        # was decided by an operator. `+=` is applied ONCE against the pre-augment values
        # (re-feeding it would never converge); `* n` expands for a LITERAL count, which is
        # exact rather than over-approximate (`"print" * 2` is `"printprint"`, so it must not
        # drop `print` from the allow-lists).
        "AUGMENTED `+=` builds the read name": (
            '    _N = "_tortoise_"\n'
            '    _N += "fts_query"\n'
            f'    {reach.format(name="_N")}\n'),
        "REPETITION by 1 builds the read name": (
            '    _N = "_tortoise_fts_query" * 1\n'
            f'    {reach.format(name="_N")}\n'),
        "INLINE repetition by 1 in the fetch": (
            '    getattr(sdk, "_tortoise_fts_query" * 1)'
            '(query=queries[0]["query"], limit=10, kind="statement")\n'),
        # ⛔ THE SEQUENCE CAN BE THE **RIGHT** OPERAND (#14326 review P2, MEASURED: deleting
        # the `right * left` disjunct left the whole pin GREEN). `_possible_strings`' `Mult`
        # arm says "either operand can be the sequence" in its own comment, but every row
        # put the literal on the LEFT — a mirrored arm with no row is an unpinned half.
        "INLINE repetition with the SEQUENCE ON THE RIGHT": (
            '    getattr(sdk, 1 * "_tortoise_fts_query")'
            '(query=queries[0]["query"], limit=10, kind="statement")\n'),
        "BIND-path repetition with the SEQUENCE ON THE RIGHT": (
            '    _N = 1 * "_tortoise_fts_query"\n'
            f'    {reach.format(name="_N")}\n'),
        # ⛔ A `match` MAPPING PATTERN's `**rest` BINDS A NAME (#14326 review P3, MEASURED:
        # dropping the `rest` fallback at `_rebound_callee_names` left the pin GREEN, so the
        # row that claimed to pin it only red the `_str_constants` copy of the enumeration).
        "`match` `**rest` capture rebinding a PURE callee": (
            '    match _obj:\n'
            '        case {"a": 1, **print}:\n'
            '            print(sdk, "_tortoise_fts_query")'
            '(query=queries[0]["query"], limit=10, kind="statement")\n'),
        "PARAMETER named as a pure callee": (
            '    def _f(print=getattr):\n'
            '        print(sdk, "_tortoise_fts_query")'
            '(query=queries[0]["query"], limit=10, kind="statement")\n'
            '    _f()\n'),
        "`def` NAME shadowing a pure callee": (
            '    def print(*_a):\n'
            '        return _a\n'
            '    print(sdk, "_tortoise_fts_query")'
            '(query=queries[0]["query"], limit=10, kind="statement")\n'),
        "`except … as` shadowing a pure callee": (
            '    try:\n'
            '        _raise()\n'
            '    except Exception as print:\n'
            '        print(sdk, "_tortoise_fts_query")'
            '(query=queries[0]["query"], limit=10, kind="statement")\n'),
        # ⛔ A `**` MAPPING'S KEY AND A `dict(...)` SPREAD (#14326 round 7, both MEASURED
        # GREEN: mentions 2, calls 2, indirect 0) while the positional mirror
        # `getattr(*{_N: 1})` was already refused through `_possible_strings`' `Dict` KEYS arm
        # — the two polarities of one shape disagreed.
        "literal as a **KEY of the spread mapping": (
            '    _N = "_tortoise_fts_query"\n'
            '    _l = _PageFetcher(**{_N: 1})\n'
            '    _l(sdk)(query=queries[0]["query"], limit=10, kind="statement")\n'),
        "literal inside a `dict(...)` spread": (
            '    _l = _PageFetcher(**dict(name="_tortoise_fts_query"))\n'
            '    _l(sdk)(query=queries[0]["query"], limit=10, kind="statement")\n'),
        # ⛔ A NESTED `**` DISPLAY IS THE SAME DELIVERY (#14326 round 8, MEASURED GREEN at
        # `bb89a48`: m=2 c=2 i=0 while the ONE-level sibling above was refused — the verdict
        # was decided by a nesting level).
        "literal one level down in a nested ** display": (
            '    _l = _PageFetcher(**{**{"name": "_tortoise_fts_query"}})\n'
            '    _l(sdk)(query=queries[0]["query"], limit=10, kind="statement")\n'),
        # ⛔ A KEYWORD-ONLY INDEX — row DELETED as a duplicate, not left beside its twin.
        # The peer lane's identical row above ("SECOND kw-only parameter default") is the one
        # that stands. Two distinct keys with identical bodies BOTH execute, so neither was
        # shadowed — but a row that cannot be the only failing row is exactly the defect this
        # file deletes clauses for (the positional pair was deduped in `d6ed679`).
        # ⛔ A BARE-NAME THIRD READ IS A READ (#14326 round 8, PRE-EXISTING and unpinned:
        # dropping `_read_calls`' `id` alternative left 25 passed, and this reach went
        # `refuses=True` (c=3) -> `refuses=False` (m=2 c=2)). The `calls == 2` assertion the
        # whole pin rests on has an `id` half nobody had ever reddened.
        "bare-name read call": (
            '    _tortoise_fts_query(query=queries[0]["query"], limit=10, '
            'kind="statement")\n'),
        # ⛔ THE OTHER THREE PURE-STRING SPELLINGS (#14326 round 13, each MEASURED GREEN at
        # `b6be33738f` — `m=2 c=2 i=0` — while every `+` spelling of the same string was
        # refused): the verdict was being decided by WHICH OPERATOR built the name. An
        # f-string whose parts are all literals computes nothing at runtime, `%` and
        # `.format` are the two formatting forms of the same literal, and Semgrep's constant
        # propagation folds all four (`+`, f-strings, `%`, `.format`) into a plain string.
        # The BOUND rows red the `_values` half (`_N` resolved from the built string), the
        # INLINE rows the `_possible_strings` half — the two resolvers are separate recursions
        # and a fix to one alone leaves the other spelling open (that asymmetry is what the
        # `+` arm cost in round 12).
        "f-string of ONE literal part": (
            '    getattr(sdk, f"{\'_tortoise_fts_query\'}")'
            '(query=queries[0]["query"], limit=10, kind="statement")\n'),
        "f-string JOINING two literal parts": (
            '    getattr(sdk, f"{\'_tortoise_\'}{\'fts_query\'}")'
            '(query=queries[0]["query"], limit=10, kind="statement")\n'),
        "f-string part JOINED with a constant fragment": (
            '    getattr(sdk, f"{\'_tortoise\'}_fts_query")'
            '(query=queries[0]["query"], limit=10, kind="statement")\n'),
        "f-string joined parts BOUND to a name": (
            '    _N = f"{\'_tortoise_\'}{\'fts_query\'}"\n'
            f'    {reach.format(name="_N")}\n'),
        "f-string part+constant BOUND to a name": (
            '    _N = f"{\'_tortoise\'}_fts_query"\n'
            f'    {reach.format(name="_N")}\n'),
        "literal-only `%` template INLINE": (
            '    getattr(sdk, "%s_fts_query" % "_tortoise")'
            '(query=queries[0]["query"], limit=10, kind="statement")\n'),
        "literal-only `%` template BOUND to a name": (
            '    _N = "%s_fts_query" % "_tortoise"\n'
            f'    {reach.format(name="_N")}\n'),
        "`%` over a literal TUPLE": (
            '    getattr(sdk, "%s_%s" % ("_tortoise", "fts_query"))'
            '(query=queries[0]["query"], limit=10, kind="statement")\n'),
        "`%` over a literal MAPPING": (
            '    getattr(sdk, "%(k)s" % {"k": "_tortoise_fts_query"})'
            '(query=queries[0]["query"], limit=10, kind="statement")\n'),
        "`.format()` on a literal": (
            '    getattr(sdk, "_tortoise_fts_query".format())'
            '(query=queries[0]["query"], limit=10, kind="statement")\n'),
        "`.format(…)` over a literal argument": (
            '    getattr(sdk, "{}".format("_tortoise_fts_query"))'
            '(query=queries[0]["query"], limit=10, kind="statement")\n'),
        "`.format(…)` over a literal KEYWORD": (
            '    getattr(sdk, "{k}".format(k="_tortoise_fts_query"))'
            '(query=queries[0]["query"], limit=10, kind="statement")\n'),
        "`.format()` BOUND to a name": (
            '    _N = "_tortoise_fts_query".format()\n'
            f'    {reach.format(name="_N")}\n'),
        # ⛔ THE `.format` CLASS WAS ENUMERATED BY METHOD NAME, AND ONE DELIVERY SHORT (#14326
        # round 15, all five MEASURED GREEN at `1044c2a0ea5d` — `refuses=False`, `m=2 c=2
        # i=0`, with a third reach carrying `kind="statement"`): `str.format_map` is the SAME
        # template-plus-mapping build under the other method name and matched no arm at all,
        # and a `**` DISPLAY is statically visible even though a name-held mapping is not —
        # `_mapping_keys_in_keywords` already resolves the display for a namespace
        # `update(**{…})`, so the verdict was decided by which call the same display was
        # handed to (the `%` → `.format` shape of round 13, one method and one delivery over).
        # The BOUND rows red `_values`, the INLINE rows `_possible_strings`; both go through
        # the shared `_format_call_strings`, so neither resolver can carry one spelling only.
        "`.format(**{…})` spread BOUND to a name": (
            '    _N = "{k}".format(**{"k": "_tortoise_fts_query"})\n'
            f'    {reach.format(name="_N")}\n'),
        "`.format(**{…})` spread INLINE": (
            '    getattr(sdk, "{k}".format(**{"k": "_tortoise_fts_query"}))'
            '(query=queries[0]["query"], limit=10, kind="statement")\n'),
        "`.format(**{**{…}})` NESTED spread INLINE": (
            '    getattr(sdk, "{k}".format(**{**{"k": "_tortoise_fts_query"}}))'
            '(query=queries[0]["query"], limit=10, kind="statement")\n'),
        "`.format_map({…})` BOUND to a name": (
            '    _N = "{k}".format_map({"k": "_tortoise_fts_query"})\n'
            f'    {reach.format(name="_N")}\n'),
        "`.format_map({…})` INLINE": (
            '    getattr(sdk, "{k}".format_map({"k": "_tortoise_fts_query"}))'
            '(query=queries[0]["query"], limit=10, kind="statement")\n'),
        # ⛔ A VALUE SELECTED OUT OF A **LITERAL** CONTAINER (#14326 round 16, every shape
        # MEASURED GREEN at `0162ab067e` — `refuses=False`, `m=2 c=2 i=0`, the product read
        # executed by a third `kind="statement"` reach). Every element is a `Constant` in the
        # source — the argument that admitted `+`, `*`, `%`, `.format` and the f-string — and
        # this file already expands literal sequences for `for` targets, splats and `**{…}`
        # displays, so declining them was an in-file asymmetry. The residual stays a
        # NAME-held container (`_CFG["read"]`), which `_subscript_strings` declines on
        # purpose; `test_a_value_READ_OUT_of_a_LITERAL_container_is_a_reach` pins both the
        # SELECTION precision and that boundary. BOUND rows red `_values`, INLINE rows red
        # `_possible_strings` — both through the one shared helper, so neither resolver can
        # carry a spelling the other lacks (the `+`/`%`/`.format` lesson).
        "literal SEQUENCE indexed by a constant BOUND to a name": (
            '    _N = ("x", "_tortoise_fts_query")[1]\n'
            f'    {reach.format(name="_N")}\n'),
        "literal SEQUENCE indexed by a constant INLINE": (
            '    getattr(sdk, ["x", "_tortoise_fts_query"][1])'
            '(query=queries[0]["query"], limit=10, kind="statement")\n'),
        "literal SEQUENCE indexed by a NEGATIVE constant": (
            '    getattr(sdk, ("_tortoise_fts_query", "x")[-2])'
            '(query=queries[0]["query"], limit=10, kind="statement")\n'),
        "literal SEQUENCE through a STARRED element": (
            '    getattr(sdk, [*("_tortoise_fts_query",)][0])'
            '(query=queries[0]["query"], limit=10, kind="statement")\n'),
        "literal MAPPING indexed by a constant key": (
            '    _N = {"k": "_tortoise_fts_query"}["k"]\n'
            f'    {reach.format(name="_N")}\n'),
        "literal STRING sliced by a constant NEGATIVE bound": (
            '    _N = "_tortoise_fts_query_extra"[:-6]\n'
            f'    {reach.format(name="_N")}\n'),
        "literal STRING sliced IN STEPS": (
            '    getattr(sdk, "_tortoise_fts_query_x"[::-1][::-1][:-2])'
            '(query=queries[0]["query"], limit=10, kind="statement")\n'),
    }
    for label, body in rows.items():
        assert _pin_refuses(_pin_source(body)), (
            f"[{label}] a third reach executes the product read with `kind=\"statement\"` and "
            "the pin is GREEN on it — every static binding of the literal must resolve, or "
            "the `intentional_keep` grant for `run()` stands on a pin that cannot go red")

    # ⛔ AN `async for` TARGET IS THE SAME BIND ARM AND IT WAS UNPINNED (#14326 round 17,
    # MEASURED at `689708686b`: deleting `ast.AsyncFor` from `_str_constants`' bind tuple —
    # `elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension))` — left this whole
    # file at **39 passed**, so the member was a clause no mutant could red, which is the
    # defect this table exists to delete clauses for). It cannot ride the table above: the
    # wrapper there is a plain `def run(…)`, where `async for` is a SyntaxError. Same shape,
    # same verdict, `_pin_source_async`.
    assert _pin_refuses(_pin_source_async(
        '    async for _N in ("_tortoise_fts_query",):\n'
        f'        {reach.format(name="_N")}\n')), (
        "an `async for` target carrying the read name is a reach — `ast.AsyncFor` is in the "
        "same bind arm as `ast.For`, and removing it must red this row rather than leave "
        "the arm unpinned")

    # ⛔ AND AN `async with` TARGET, FOR THE SAME REASON ONE NODE TYPE OVER (#14326 round 21,
    # MEASURED at `ff85c6ab73`: replacing `(ast.With, ast.AsyncWith)` with `ast.With` in
    # `_str_constants`' arm left the whole file at **41 passed**, while the mutant drops this
    # shape back to mentions 2 / calls 2 / indirect 0 — a lever-carrying read passing silently).
    # The `` `with` `` row in the table above is wrapped in a plain `def run(…)` and therefore
    # CANNOT carry it; `_pin_source_async` is what makes the row possible.
    assert _pin_refuses(_pin_source_async(
        '    _r = "_tortoise_fts_query"\n'
        '    async with _r as _N:\n'
        f'        {reach.format(name="_N")}\n')), (
        "an `async with` target carrying the read name is a reach — `ast.AsyncWith` is in the "
        "same bind arm as `ast.With`, and removing it must red this row")

    # ⛔ THE *ALIAS* ARM'S `ast.AsyncFor` MEMBER IS PINNED BY THE SIBLING LANE, NOT HERE
    # (#14326 round 18). Dropping it from `_aliases_of`'s `(ast.For, ast.AsyncFor)` tuple left
    # this whole file at **39 passed** (re-measured by this lane before it saw the peer's
    # work), and `45a7d205c7c4` pins that member end-to-end through `aug_levers` with the
    # `async for` lever above; `c54f4f22d3cd` pins the other two LOAD-BEARING `ast.AsyncFor`
    # members (`_in_loop`, `_extra_bindings`) and NAMES the four that are redundant. A second
    # row asserting the same deletion from the resolver side would add no discriminating power
    # — two rows for one member is the double-pinning the peer's own note declines — so the
    # finding is recorded here and the pin is theirs. The `_pin_source_async` wrapper that
    # makes such a row possible is still exercised by round 17's `_str_constants` row.

    # ⛔ THE CONTROL, in the false-red direction: a constant that only NAMES the read.
    inert = ('    _NOTE = "_tortoise_fts_query"\n'
             '    print(f"measuring {_NOTE}")\n')
    assert not _pin_refuses(_pin_source(inert)), (
        "a provenance constant beside `git_sha`/`db_uri` is a FALSE RED — the read is named "
        "and never executed, and this pin's job is to bound executable reaches")

    # ⛔ THE SECOND AND THIRD CONTROLS: PATTERN LITERALS ARE NOT BINDS (#14326 review P2,
    # measured). A capture binds a PART OF THE SUBJECT and can never receive a pattern
    # literal, so crediting one reds the recording direction #14289 asks for. Both
    # directions are pinned — a literal in an UNRELATED case, and a literal in a mapping
    # KEY of the SAME case whose capture reads a VALUE; a code path that credited pattern
    # literals would red the second one while the first stayed green.
    other_case = ('    _cfg = {"read": "x"}\n'
                  '    match _cfg:\n'
                  '        case {"read": "_tortoise_fts_query"}:\n'
                  '            _mode = 1\n'
                  '        case _label:\n'
                  '            _legs.append(_label)\n')
    same_case = ('    _cfg = {"read": "x", "n": "y"}\n'
                 '    match _cfg:\n'
                 '        case {"read": "_tortoise_fts_query", "n": _v}:\n'
                 '            _legs.append(_v)\n')
    for label, body in (("another case", other_case), ("the same case's KEY", same_case)):
        assert not _pin_refuses(_pin_source(body)), (
            f"a `match` literal in {label} is a FALSE RED — the capture takes the SUBJECT's "
            "values only, so a pattern literal does not bind it")

    # ⛔ THE FOURTH CONTROL: a DEFAULT that only NAMES the read (#14326 review P6). Resolving
    # defaults must not turn `def _f(_N="_tortoise_fts_query"): print(_N)` into a red — the
    # name is handed to a known non-executor, exactly like the provenance constant above.
    named_default = ('    def _f(_N="_tortoise_fts_query"):\n'
                     '        print(f"measuring {_N}")\n'
                     '    _f()\n')
    assert not _pin_refuses(_pin_source(named_default)), (
        "a parameter default that is merely PRINTED is a FALSE RED — the default is a bind, "
        "and this pin still bounds EXECUTABLE reaches")

    # ⛔ THE FIFTH CONTROL — A CONTAINER THAT HOLDS THE LITERAL IS NOT A REACH (#14326
    # review P2, measured RED at `399ddc2` before `_is_read_ref`'s subscript arm was
    # narrowed to the slice). `_values`' DICT arm deliberately binds the WHOLE mapping to
    # its name (that is what keeps `match _m: case {"read": _N}:` resolvable), so the
    # container's name resolves to the literal too — and a `Load` subscript over that NAME
    # was credited as a reach, so this inert provenance line went 2 mentions -> 3. The
    # LEVER is the opposite position (`vars(type(sdk))[_N]`, pinned by the rows above); a
    # read used as a dict's VALUE is not executed.
    provenance_box = ('    _READ = {"name": "_tortoise_fts_query"}\n'
                      '    print(f"measuring {_READ[\'name\']}")\n')
    assert not _pin_refuses(_pin_source(provenance_box)), (
        "a container that HOLDS the read name and is only subscripted is a FALSE RED — "
        "nothing is executed, and the recording direction #14289 asks for must not red the "
        "census just because `_values` binds a mapping's whole literal to its name")

    # ⛔ THE NAMESPACE ARM IS SCOPED, AND THIS IS ITS OTHER POLARITY (#14326 review at
    # `c380075b`): `_namespace_rebind_names` takes a string key only when the RECEIVER is a
    # live namespace (`globals()`/`locals()`/`vars(x)`/`x.__dict__`). A string-keyed store
    # into an ORDINARY container binds no callee, so dropping the allow-listed name `write`
    # — or `print` — for it would be the false-red family this pin spends rows deleting.
    container_store = ('    _counts = {}\n'
                       '    _counts["write"] = 0\n'
                       '    _counts["print"] = 0\n'
                       '    _cfg = {}\n'
                       '    _cfg.update({"write": 0})\n'
                       '    _LOG.write("_tortoise_fts_query")\n')
    assert not _pin_refuses(_pin_source(container_store)), (
        "a string-keyed store into an ordinary CONTAINER is not a callee rebind — treating "
        "it as one would drop `write`/`print` from the allow-lists and RED the recording "
        "direction; only a NAMESPACE receiver carries a module-level bind")

    # ⛔ THE SIXTH CONTROL — A MAPPING IS ONLY A REACH WHEN IT HOLDS THE NAME (#14326 round
    # 15, MEASURED `refuses=False` before AND after the `**`/`format_map` arms). Resolving a
    # display must red the templated READ, not every templated string: a label table, and a
    # NAME-HELD mapping (which the resolution deliberately does not follow), both stay green.
    label_displays = ('    _sdk = "{k}".format_map({"k": "tortoise.sdk"})\n'
                      '    print(f"note {_sdk}")\n'
                      '    _lbl = "{k}".format(**{"k": "tortoise.other"})\n'
                      '    print(f"note {_lbl}")\n'
                      '    _MAP = {"k": "tortoise.legacy"}\n'
                      '    _old = "{k}".format_map(_MAP)\n'
                      '    print(f"note {_old}")\n')
    assert not _pin_refuses(_pin_source(label_displays)), (
        "a display whose values are ordinary labels is a FALSE RED — the `**` and "
        "`format_map` arms must red the templated READ, not every templated string")

    # ⛔ THE `setdefault` DEFAULT IS A VALUE, NOT A KEY (#14326 review cycle 3, measured):
    # `setdefault(key, default)` takes ONE key positionally, so a static string in the SECOND
    # position — `builtins.__dict__.setdefault("_x", "print")` — binds nothing. Grading it
    # as a key drops `print` from the allow-lists and reds the recording call beside it, so
    # this is the false-red polarity of the same arm (measured: deleting the `args[:1]` slice
    # turns this control RED while every namespace row stays green).
    setdefault_default = ('    import builtins\n'
                          '    builtins.__dict__.setdefault("_x", "print")\n'
                          '    print("_tortoise_fts_query")\n')
    assert not _pin_refuses(_pin_source(setdefault_default)), (
        "a `setdefault` DEFAULT that is a callee name binds nothing — the second positional "
        "is the VALUE, and only the FIRST is a key")

    # ⛔ THE SIXTH CONTROL, the OTHER polarity of the same clause: a subscript STORE under
    # the literal (`_counts["_tortoise_fts_query"] = 0`) is a recording write, not a read
    # — the `Constant` arm's `isinstance(_parent.ctx, ast.Load)` guard exists for it and
    # had NO row in either direction (#14326 review P3, measured: replacing that guard
    # with `True` left all 25 tests green). A store cannot be CALLED.
    # ⛔ …AND A CONCATENATION THAT CAN NEVER EQUAL THE READ NAME STAYS GREEN (#14326 round 10,
    # MEASURED REFUSED while the fallback was a UNION — it emitted the bare read name from an
    # operand set whose every concatenation ends in `_y`):
    never_read = ('    getattr(sdk, ("_tortoise_fts_query" if queries else "x") + "_y")'
                  '(query=queries[0]["query"], limit=10, kind="statement")\n')
    assert not _pin_refuses(_pin_source(never_read)), (
        "a sum whose EVERY possible value ends in `_y` is not the read name — the `BinOp` arm "
        "must join the operands, not union their literal sets")
    literal_store = '    _counts["_tortoise_fts_query"] = 0\n'
    assert not _pin_refuses(_pin_source(literal_store)), (
        "a subscript STORE whose key is the read literal is a FALSE RED — it records under "
        "the name and executes nothing; the Constant arm's Load guard is what keeps the "
        "census counting CALLABLE spellings")
    # ⛔ THE NAME-CARRIED TWIN OF THAT CONTROL (#14326 review P1, MEASURED: deleting the
    # NAME arm's `ctx is Load` guard left the whole pin GREEN). The Constant arm had a row in
    # both directions and the Name arm had none, so a recording store that carries the
    # literal through a NAME could start REDDING with no test noticing.
    name_carried_store = ('    _N = "_tortoise_fts_query"\n'
                          '    _d = {}\n'
                          '    _d[_N] = 0\n')
    assert not _pin_refuses(_pin_source(name_carried_store)), (
        "a subscript STORE whose key is a NAME bound to the read literal is the same false "
        "RED as the literal spelling — the Name arm's Load guard is its twin")

    # ⛔ THE RECORDING DIRECTION, IN THE CALL SPELLINGS (#14326 round 6, all four MEASURED
    # REFUSED at `81973329`): the Constant arm treated every callee outside `_PURE_READS` as
    # an executor, so the identical recording act was graded two ways depending on syntax —
    # green as a subscript store (above), red as a call. `_RECORDING_CALLEES` carries the
    # spellings that take a string and cannot execute it, and `print` (already allow-listed)
    # gets its own row so the purity test itself cannot silently invert.
    for label, body in (
            ("logger method", '    _LOG.info("_tortoise_fts_query")\n'),
            ("stderr write", '    sys.stderr.write("_tortoise_fts_query")\n'),
            # the NAME-carried and CONTAINER spellings reach the other two arms, so they are
            # pinned here as well (round 7, each measured GREEN->REFUSED without its exemption)
            ("logger method, name-carried",
             '    _N = "_tortoise_fts_query"\n    _LOG.info(_N)\n'),
            ("logger method, splatted", '    _LOG.info(*("_tortoise_fts_query",))\n'),
            ("logger method, keyword", '    _LOG.info(msg="_tortoise_fts_query")\n'),
            ("print, inline", '    print("_tortoise_fts_query")\n'),
            # ⛔ A WITH-BODY LOAD IS NOT A BIND (#14326 round 7, MEASURED RED at `d790e1f0`):
            # the `with` arm walked the whole STATEMENT (a `With` node has no `.target`), so
            # any pure-name load inside a `with` body dropped that name from the allow-list —
            # and the SAME recording call below was then refused, decided by an unrelated
            # line sitting inside a `with`. This row is the arm's false-red attribution; the
            # `with … as` row above is its fail-open attribution.
            ("print, beside a with-body LOAD of `print`",
             '    with open("/dev/null") as _fh:\n'
             '        print(_fh)\n'
             '    print("_tortoise_fts_query")\n')):
        assert not _pin_refuses(_pin_source(body)), (
            f"[{label}] recording the read's NAME is a FALSE RED — nothing is executed, and "
            "the recording direction #14289 asks for must not depend on whether the act is "
            "spelled as a call or as a subscript store")
    # ⛔ BUT A DICT LOOKUP IS A FETCH, NOT A RECORDING (#14326 round 7, MEASURED GREEN with
    # `setdefault` exempted): `dict.setdefault(k)` RETURNS the value at `k`, so the exemption
    # admitted `_FETCHERS.setdefault(literal)(sdk)(…, kind="statement")` while the identical
    # lookup spelled `.get(…)` was refused. The entry is REMOVED rather than narrowed,
    # because an exemption is granted on the NAME and cannot see the difference.
    assert _pin_refuses(_pin_source(
        '    _FETCHERS.setdefault("_tortoise_fts_query")(sdk)'
        '(query=queries[0]["query"], limit=10, kind="statement")\n')), (
        "`dict.setdefault(read_name)` RETURNS the value at that key, so it can hand back a "
        "callable — exempting `setdefault` from the census exempts a real fetch")
    # ⛔ …AND THE `mentions == 2` ASSERTION THAT USED TO SIT HERE IS DELETED, NOT KEPT
    # (#14326 round 5): `_pin_refuses` is `bool(indirect) or len(calls) != 2 or len(mentions)
    # != 2` over the same `_read_reach` on an identically re-parsed source, so the control
    # above passing ALGEBRAICALLY implies it — measured: with the `Load` guard replaced by
    # `True` the failure lands on the control, and the extra assertion only fires once the
    # control is removed. A row that can never be the failing row reads to the next lane as
    # an independent check and is not one; this file deletes those rather than keeping them
    # `_name_reaching_operands`' literal-splat expansion was deleted for the same reason.

    # ⛔ THE SEVENTH CONTROL: a literal unpack PAIRS ELEMENT-WISE, so a target that is not the
    # read name's position can never hold it (#14326 review F2, measured). `_pair` is the
    # FIRST element of a two-element literal and can only ever hold `"a"`; crediting the
    # whole container's values to it is a FALSE RED. Removing `_bind`'s pairing arm left all
    # 25 tests green AND turned this shape RED — a PRECISION guard no row could see, in the
    # one direction this file calls expensive.
    paired_unpack = ('    _pair, _x = ("a", "_tortoise_fts_query")\n'
                     f'    {reach.format(name="_pair")}\n')
    assert not _pin_refuses(_pin_source(paired_unpack)), (
        "a literal unpack pairs ELEMENT-WISE — `_pair` is the FIRST element and can only "
        "hold `\"a\"`, so binding it the container's whole value set is a FALSE RED")


def test_a_value_READ_OUT_of_a_LITERAL_container_is_a_reach() -> None:
    """A SUBSCRIPT of a literal is a literal — plus the two directions that bound it.

    ⛔ MEASURED BEFORE THIS FIX (#14326 round 16, at `0162ab067e`): `('x', '_tortoise_fts_query')[1]`,
    `{'k': '_tortoise_fts_query'}['k']`, `'_tortoise_fts_query_extra'[:-6]` and
    `[*('_tortoise_fts_query',)][0]` all shipped GREEN (`refuses=False`, `m=2 c=2 i=0`) with a
    third `kind="statement"` reach executing the product read. The row table in
    `test_EVERY_static_binding_form_is_a_READ_reach` is the fail-open attribution; the asserts
    here are the directions that keep the new arms from becoming a blunt instrument.

    Measured non-vacuity (each mutant applied in place on this file, suite re-run, restored
    byte-identically):

      - `_values`' `Subscript` arm deleted -> the BOUND rows of the table go GREEN;
      - `_possible_strings`' `Subscript` arm deleted -> the INLINE rows go GREEN (so the two
        arms are each load-bearing for their own delivery, not one for the other);
      - `_int_literals`' `UnaryOp` arm deleted -> the negative-index, negative-slice and
        stepped-slice rows go GREEN (a negative literal is a `UnaryOp`, never a `Constant`);
      - the nested-`Subscript` branch deleted -> the stepped-slice row goes GREEN;
      - the mapping's constant-KEY selection replaced by the whole value union -> the
        ABSENT-key control below goes RED (a false red on a display that selects nothing).
    """
    reach = 'getattr(sdk, {name})(query=queries[0]["query"], limit=10, kind="statement")'
    # ⛔ SELECTION, NOT UNION: a constant index picks ONE element, so crediting the container's
    # whole value set to it is the false red this file calls expensive (the same rule `_bind`'s
    # unpair arm follows: `_pair, _x = ("a", "_tortoise_fts_query")` stays green above).
    selected_elsewhere = ('    _N = ("a", "_tortoise_fts_query")[0]\n'
                          f'    {reach.format(name="_N")}\n')
    assert not _pin_refuses(_pin_source(selected_elsewhere)), (
        "a literal sequence indexed at a constant SELECTS that element — `_N` can only hold "
        "`a`, so resolving it to the container's whole value set is a FALSE RED")
    # ⛔ …AND A CONSTANT KEY THE DISPLAY DOES NOT CARRY SELECTS NOTHING (the runtime
    # `KeyError`), so a union of every value there is a false red too.
    absent_key = ('    _N = {"k": "_tortoise_fts_query"}["other"]\n'
                  f'    {reach.format(name="_N")}\n')
    assert not _pin_refuses(_pin_source(absent_key)), (
        "a literal mapping indexed by a constant key it does not carry selects nothing — "
        "crediting it every VALUE of the display is a FALSE RED")
    # ⛔ THE DECLARED RESIDUAL, PINNED GREEN SO THE NOTE CANNOT GO STALE (`_subscript_strings`):
    # a NAME-held container is not statically pairable, and resolving a name's whole value set
    # here would false-red the provenance lookup the `_is_read_ref` narrowing exists to keep
    # green. An author who makes this resolve must DELETE the residual note, not leave it
    # contradicting the code — the reason
    # `test_an_accumulation_over_several_ITERATIONS_is_the_DECLARED_residual` (its `loop over a
    # NAME` row) pins its own boundary the same way. ⛔ IT USED TO NAME
    # `test_a_loop_over_a_NAME_stays_the_DECLARED_residual`, WHICH DOES NOT EXIST (#14326 round
    # 18, MEASURED: no such `def test_`, in this file or under `tests/`) — a stale reference is
    # a claim no test can red, which is the defect class this file deletes clauses for.
    name_held = ('    _CFG = {"read": "_tortoise_fts_query"}\n'
                 '    _N = _CFG["read"]\n'
                 f'    {reach.format(name="_N")}\n')
    assert not _pin_refuses(_pin_source(name_held)), (
        "a value read out of a NAME-held container is the DECLARED residual — nothing about "
        "it is statically pairable, and resolving it would false-red `_READ['name']`")
    # ⛔ THE ONE-CHARACTER CASE: a literal STRING indexed at a RUNTIME position selects a
    # character, which no read name can be, so the arm must not widen to the whole string —
    # it resolves to nothing. (This cannot be a fail-open either way: the literal IS the read
    # name and sits in an executed subscript, so the `Constant` arm censuses it — which is why
    # this is asserted on the RESOLVER rather than through `_pin_refuses`.)
    assert not _possible_strings(ast.parse('"_tortoise_fts_query"[_i]').body[0].value, {})
    # ⛔ AND A RUNTIME-BOUND SLICE MUST NOT WIDEN TO THE WHOLE LITERAL (#14326 round 16):
    # `'…'[i:j]` selects a SUBSTRING this cannot pair, and over-approximating to the whole
    # string would false-red every `_LABEL[_start:_end]` in a file — the same cost asymmetry
    # the single-character case above follows. MEASURED: making `_constant_slice_bounds`
    # return `(None, None, None)` for a non-constant bound left all 38 tests green, so this row
    # is what makes that clause falsifiable rather than decorative.
    assert not _possible_strings(ast.parse('"_tortoise_fts_query"[_i:_j]').body[0].value, {})
    # ⛔ AND THE RESIDUAL THAT SHARES THE CLASS: a METHOD CALL that is not `str.format`/
    # `str.format_map` (`"".join(…)`, `.replace(…)`, `.lower()`, `str(…)`, `.decode()`).
    # Measured GREEN here rather than left silent: `_possible_strings`' docstring names the
    # class, and a shape that starts resolving must red this row so the note is updated with it.
    for label, expr in (
            ("str.join over literals", "''.join(['_tortoise', '_fts_query'])"),
            ("str.strip", "'_tortoise_fts_query '.strip()"),
            ("str.lower", "'_TORTOISE_FTS_QUERY'.lower()"),
            ("str() of a literal", "str('_tortoise_fts_query')"),
            ("bytes.decode", "b'_tortoise_fts_query'.decode()")):
        body = (f'    _N = {expr}\n'
                f'    {reach.format(name="_N")}\n')
        assert not _pin_refuses(_pin_source(body)), (
            f"[{label}] a method call other than `str.format`/`str.format_map` is a DECLARED "
            "residual — if this now resolves, update `_possible_strings`' residual note "
            "instead of leaving it contradicting the code")
    # ⛔ AND THE OTHER DECLARED CARRIERS, NAMED BY SPELLING SO THE NOTE CANNOT GO STALE
    # (#14326 round 16, each MEASURED GREEN at `203737ebf`): a value held in an ATTRIBUTE or
    # produced by a CALL. `_str_constants` models NAME bindings, not attribute namespaces, so
    # `_C.name` and `self.read` are out of its reach by construction — the same statement the
    # docstring makes in prose. Rows rather than prose because the next lane's change is what
    # un-stales them: if one of these starts resolving, the residual note is wrong and this
    # reds instead of leaving a sentence that contradicts the code.
    for label, body in (
            ("class attribute",
             '    class _C:\n        name = "_tortoise_fts_query"\n'
             f'    {reach.format(name="_C.name")}\n'),
            ("instance attribute",
             '    self = type("S", (), {})()\n'
             '    self.read = "_tortoise_fts_query"\n'
             f'    {reach.format(name="self.read")}\n'),
            ("call returning the literal",
             '    def _mk():\n        return "_tortoise_fts_query"\n'
             f'    {reach.format(name="_mk()")}\n'),
            ("lambda default carrier",
             '    _f = lambda _x="_tortoise_fts_query": _x\n'
             f'    {reach.format(name="_f()")}\n')):
        assert not _pin_refuses(_pin_source(body)), (
            f"[{label}] a value held in an ATTRIBUTE or produced by a CALL is a DECLARED "
            "residual — if this now resolves, update the residual note in "
            "`_str_constants`/`_possible_strings` rather than leaving it stale")


def test_a_PRODUCT_that_outgrows_its_budget_REFUSES_instead_of_spinning() -> None:
    """An unbounded JOIN product is a timeout, not a red — so the resolver REFUSES.

    ⛔ THE BUDGET IS THE VERDICT (#14326 round 13, the rule `_FIXPOINT_MAX_PASSES` states for
    the bind fixpoint): an f-string whose every part is a SELECTION of literals is a LINEAR
    source that costs an EXPONENTIAL walk — 40 two-valued parts are 10^12 combinations and
    never return — so a guard that materialises it HANGS instead of answering. Measured with
    `_PRODUCT_MAX_COMBINATIONS` deleted: this 13-part source resolves its 8192 combinations
    and the next part multiplies again; with it, the resolver raises the ordinary test
    failure the pin already uses for a diverging bind.
    """
    many = "".join("{'a', 'b'}" for _ in range(13))
    body = (f'    getattr(sdk, f"{many}")'
            '(query=queries[0]["query"], limit=10, kind="statement")\n')
    with pytest.raises(_ResolutionDidNotConverge):
        _read_reach(_pin_source(body))


def test_the_READ_side_of_str_is_NOT_a_MUTATION() -> None:
    """`strip`/`lower`/`split` return a NEW object; they do not mutate the tainted receiver.

    ⛔ MEASURED FALSE REDS at `66880dace8` (#14281 review P3): `print(results[0]["query"]
    .strip())` and `_lbl = results[0]["query"].lower()` were reported as "an in-place
    sort/clear", because the mutator polarity is FAIL-CLOSED and the str read side was not
    listed. Adding those names shipped with no test (#14279 review F2) — reverting the hunk
    left the pin GREEN, so the false red could come back silently.

    Both directions are asserted: the reads stay green, and the fail-closed polarity that
    makes an unlisted name a mutation is untouched.
    """
    for name in ("strip", "lstrip", "rstrip", "lower", "upper", "casefold", "split",
                 "rsplit", "replace", "format", "join", "title", "capitalize", "zfill",
                 "center", "startswith", "endswith", "find", "rfind", "isdigit", "isalpha"):
        assert not _is_mutator_method(name), (
            f"`{name}()` is a str READ that returns a new object, and this pin calls it a "
            "mutation of the tainted receiver (a false red on the natural next edit to the "
            "tool's per-query print line)")
    for name in ("sort", "clear", "append", "extend", "update", "__delitem__", "__init__",
                 "__iadd__", "a_method_python_adds_later"):
        assert _is_mutator_method(name), (
            f"`{name}` is not in `_PURE_METHODS`, so the fail-closed polarity must call it a "
            "mutation — an unlisted name reading green is the closed-list defect this "
            "polarity replaced")
    assert not _is_mutator_method(None), (
        "a call whose callee is not an attribute at all is not a method call")
    # End to end, through the detector the pin actually runs.
    for body in ('    print(results[0]["query"].strip())\n',
                 '    _lbl = results[0]["query"].lower()\n'):
        assert not _mutations_of(body), (
            f"{body.strip()!r} mutates nothing (`hits={_mutations_of(body)}`) — a str read on "
            "a tainted row is not an in-place mutation of the page")
    for body in ('    results[0]["order"].sort()\n',
                 '    helper(results)\n'):
        assert _mutations_of(body), (
            f"{body.strip()!r} MUST stay red — adding the str reads to `_PURE_METHODS` must "
            "not disable the arm that catches a real mutation")


def test_a_COMPREHENSION_argument_is_NOT_the_collection() -> None:
    """A comprehension BUILDS A NEW collection, so it cannot mutate its source.

    ⛔ MEASURED FALSE REDs at `66880dace8` (#14281 review P2): `", ".join(e.get(\"leg\") or
    \"\" for e in trace)` and `statistics.fmean(len(r) … for r in results)` were reported as
    "an in-place sort/clear reaches the artifact", decided by the CALLEE's name. The
    exemption shipped with no test (#14279 review F2); reverting it left the pin GREEN.

    ⛔ AND THE EXEMPTION IS NARROW: a mutation INSIDE the comprehension is still a mutation —
    the comprehension's own receiver arm catches it, so this cannot become a blanket
    exoneration of every expression that mentions the page.
    """
    for body in ('    print(", ".join(str(e) for e in results))\n',
                 '    print(statistics.fmean(len(r) for r in results))\n'):
        assert not _mutations_of(body), (
            f"{body.strip()!r} hands a NEW collection to the callee, not the page "
            f"(`hits={_mutations_of(body)}`) — a comprehension over the page is not a "
            "mutation of it")
    # ⛔ AND THE BODY IS GRADED AT THE SITE, whatever the wrapper (#14326 review P2,
    # measured GREEN at `df5f67017c`/`b0cea320` with `compare()` then reporting
    # `rank_identical: True` over a permuted pair): a helper that WRITES an element ran
    # inside the comprehension, and the exemption covered the comprehension. `any(...)`,
    # `sorted(key=…)` and `min(key=…)` take a PURE callee, so `handed` never looked at them
    # at all, and a comprehension with no callee has no call to look at.
    for body in (
            '    def _scrub(_e):\n        _e["id"] = "REDACTED"\n'
            '    _flag = any(_scrub(r) for r in results)\n',
            '    _scrub = lambda _e: _e.__setitem__("id", "REDACTED")\n'
            '    _x = [r for r in results if _scrub(r)]\n',
            '    _key = lambda _e: (_e.__setitem__("id", "X"), 0)[1]\n'
            '    _sorted = sorted(results, key=_key)\n',
            '    _min = min(results, key=lambda _e: (_e.__setitem__("id", "X"), 0)[1])\n',
            '    def _scrub(_e):\n        _e["id"] = "REDACTED"\n'
            '    _j = ", ".join(_scrub(r) for r in results)\n'):
        assert _mutations_of(body), (
            f"{body.strip()!r} hands the page's ELEMENTS to a callee that is not a known "
            "non-mutator, and `compare()` then reads `rank_identical: True` over a genuinely "
            "permuted pair — a pure wrapper (`any`, `sorted`, `min`) does not make the BODY "
            "safe")

    # ⛔ A CALLBACK PASSED **BY NAME** AS A NESTED `def` — the row the `callable_params`
    # seed exists for, and it had none (#14326 review P1, measured). `sorted(results,
    # key=_key)` hands the collection to a PURE callee (`sorted`, so `handed`
    # short-circuits) and hands the ELEMENTS to `_key` only through the call SITE; the
    # callback's own `_e["id"] = …` is then invisible unless
    # `callable_params.setdefault(_n.name, _n)` tied `_key`'s parameter to the page. The
    # LAMBDA spelling beside it (`_key = lambda _e: …`) is pinned by the row above, so the
    # `def` spelling read as covered while nothing red it.
    #
    # Measured at this head (`tools/read_wave_latency.py` mutated in place and restored):
    # the tool mutant `def _key(_e): _e["id"] = "REDACTED"` + `_sorted = sorted(results,
    # key=_key)` reds the pin (`line(s) [308] mutate a collection AFTER the measured read`);
    # with ONLY the `setdefault` line replaced by `pass`, the SAME tool mutant is GREEN and
    # every test in this file stays green — so the seed was load-bearing and unpinned, the
    # round-3 F1 defect class recurring inside the commit written to close it.
    def_callback = ('    def _key(_e):\n'
                    '        _e["id"] = "REDACTED"\n'
                    '    _sorted = sorted(results, key=_key)\n')
    assert _mutations_of(def_callback), (
        "a nested `def` used as `sorted(key=…)` receives the page's ELEMENTS and rewrites "
        "every `id` before `order` records them, so `compare()` reads `rank_identical: "
        "True` over a permuted pair — the `callable_params` seed is what taints its "
        "parameter, and deleting that seed must red THIS row")
    assert not _mutations_of('    def _key(_e):\n        return len(_e)\n'
                             '    _sorted = sorted(results, key=_key)\n'), (
        "a nested `def` that only READS the element must stay exempt — tainting a "
        "callback's parameter is not a blanket red on every named callback")
    for body in ('    _x = [results.clear() for r in results]\n',
                 '    _y = [results.sort() or r for r in results]\n'):
        assert _mutations_of(body), (
            f"{body.strip()!r} MUST stay red — the exemption is for a comprehension that "
            "only READS the page, and a mutation written inside one still mutates it")

    # ⛔ THE EXEMPTION GRADES THE COMPREHENSION'S BODY, NOT ONLY ITS COLLECTION (#14326
    # review F1): an ELEMENT write through a helper is invisible to the mutator arm, because
    # the helper's parameter is not the page in THIS scope:
    #
    #     def _scrub(_e): _e["id"] = "REDACTED"
    #     _j = ", ".join(_scrub(r) for r in results)
    #
    # ⛔ `_derived` ITSELF HAS NO BODY LOOP AT THIS HEAD, and the comment here used to claim it
    # did (#14326 review P7, stale pointer): the loop ran one round and could only fire for a
    # NON-pure callee, so it was replaced by a test at the SITE — `_element_handed`, consulted
    # in `_mutated_after`'s hit condition at:
    #
    #     if (receiver or handed or row_handed or slot_handed
    #             or _element_handed(node, callee)):
    #
    # The node that fires for this shape is the INNER `_scrub(r)` call — it hands an ELEMENT
    # of the page to a callee that is not in `_PURE_CALLEES` — while `join` early-returns as a
    # pure callee. MEASURED mutant: dropping `or _element_handed(node, callee)` from that
    # condition takes `_mutations_of(scrub)` from `[7]` to `[]`, so this block's assertion is
    # non-vacuous against the guard that actually closes the class. The PURE control beside it
    # is the other polarity: the exemption must survive a body that only READS the elements.
    #
    # ⛔ THE `_j = ", ".join(` AND ITS GENERATOR ARE ON SEPARATE LINES ON PURPOSE (#14326
    # review P8): on one line the outer site and the inner call share a line number, so an
    # `assert join_line in hits` passes while crediting the node it claims to exclude. Split,
    # the two sites are distinguishable and both polarities are assertable.
    scrub = ('    def _scrub(_e):\n'
             '        _e["id"] = "REDACTED"\n'
             '    _j = ", ".join(\n'
             '        _scrub(r) for r in results)\n')
    scrub_hits = _mutations_of(scrub)
    assert scrub_hits, (
        "a comprehension BODY that writes an element through a helper is not reported — the "
        "collection the outer callee receives is fresh, but the body already rewrote every "
        "`id` before `order` recorded it, so `compare()` reads `rank_identical: True` over "
        "a permuted pair")
    # ANCHORED, AND DISCRIMINATING: the credit belongs to the inner call that hands the
    # ELEMENT, not to the pure `join` that receives a fresh collection.
    scrub_run = _pin_source(scrub).body[0]
    scrub_calls = [n for n in ast.walk(scrub_run) if isinstance(n, ast.Call)]
    join_line = next(n.lineno for n in scrub_calls
                     if getattr(n.func, "attr", None) == "join")
    inner_line = next(n.lineno for n in scrub_calls
                      if getattr(n.func, "id", None) == "_scrub")
    assert inner_line in scrub_hits, (
        f"hits={scrub_hits} do not include the element-handing call's own line {inner_line} "
        "— the body check is not firing where the tainted element is handed to the helper")
    assert join_line not in scrub_hits, (
        f"hits={scrub_hits} credit the comprehension's own line {join_line}: `join` is a "
        "PURE callee and early-returns, so the hit must come from the inner element-handing "
        "call — a hit on the wrapper would mean the exemption's outer arm is what fires, "
        "which is the false red this file's `_derived` arm exists to prevent")
    assert not _mutations_of('    _j = ", ".join(str(e) for e in results)\n'), (
        "a PURE comprehension body (`str(e)`, a listed non-mutator) must stay exempt — the "
        "body check NARROWS the exemption, it does not remove it")


def test_the_harness_reads_the_SHIPPED_door_and_names_NO_lever_flags():
    """The harness must time the read the PRODUCT runs — with the door's own arguments.

    `tests/test_rerank_pool.py`'s census refuses an un-opted-out site in `tools/`
    unless it is on its `intentional_keep` list, and this file is granted on the
    ground that it is a MIRROR of the shipped door: it measures
    ``TortoiseSDK._tortoise_fts_query`` — the read ``/v1/search`` makes — and its
    verdict is the ORDERED id list, so a rerank-free reader would time a door the
    product never opens and would hide the very page permutation the rank-identity
    check exists to catch.

    ⛔ `/v1/search` ONLY, and deliberately so: the MCP ``tortoise_search`` tool calls
    the SAME read with a DIFFERENT argument set — seven levers the door does not name
    (``kind``, ``threshold``, ``entity_type``, ``min_confidence``, ``order_by``,
    ``relationship_filter``, ``traversal_path`` — ``tortoise/mcp_server.py``), and
    NEITHER of the door's two out-sinks (``read_status_out``, ``_truncation_out``) — so
    the two are INCOMPARABLE, not ordered. The allow-list below is derived from the
    ``/v1/search`` door alone, so it does NOT describe the MCP arm; a harness that
    mirrors THAT arm needs its own contract, not a widened one here.

    A grant can only say "this file MAY run the lever", so the mirror is asserted here
    instead of assumed, against the door's OWN call site (``_door_read_contract``):
    every keyword must be one the door names (plus the ``leg_trace`` out-sink), every
    argument must be a keyword (a positional one is invisible to a kwargs check), the
    measured read must be the one inside the loop, its page and trace must be iterated
    AS THEMSELVES by the ``order``/``legs`` comprehensions of the one artifact dict (and
    never rebound after the read), and no ``os.environ`` mutation may happen ANYWHERE in
    the file (only the two module-level setup pops are exempt — this text said ``in
    ``run()``,`` while the scan is over the whole file, #14281 review P3) — which arm of
    the fused-rerank gate was in force is not recorded (#14289).
    That last clause is scoped to the STATIC spellings the guard resolves (`os.environ`,
    `from os import environ`, `getattr(os, "environ")` and their alias/container chains);
    its declared residuals are on `_environ_mutations`.

    Accepted FALSE-REDs, because the pin follows SHAPES and a shape shift costs an edit
    here rather than a silent change there — collating the id mapping
    (``"order": _ids(results)``) or the leg table into a helper, hoisting the artifact dict
    to a local (``row = {…}; per_query.append(row)``), ``per_query.extend([{…}])``, and
    RENAMING the collected list (this pin hard-codes the collector's name ``per_query`` on
    purpose — it is what stops ``_p = list(reversed(per_query)); return {"per_query": _p}``).
    A red there costs an edit to this pin, which is the cheap direction — the expensive
    direction is a sorted page reaching the verdict as ``rank_identical: True`` (measured
    green against earlier shapes of this check).

    ⛔ EVERY STATIC REACH IS CENSUSED, not just the two calls: a bound-method alias
    (``_read = sdk._tortoise_fts_query``), a string LITERAL naming the read
    (``sdk.__getattribute__("_tortoise_fts_query")``, ``operator.methodcaller(...)``,
    ``vars(type(sdk))[...]``), a name BOUND to that literal, and a bare attribute
    mention with no call all left earlier shapes of this pin green while carrying a
    lever (each measured). Residual, stated rather than implied: a name COMPUTED at
    runtime (``"".join(…)``, a method call other than ``str.format``/``str.format_map``, an
    f-string part whose expression this file cannot resolve, a parameter) or READ OUT OF a
    **NAME-held** container or an attribute (``_CFG["read"]``, ``self._read``) cannot be
    resolved statically, so this pin bounds the static reaches. ⛔ A value SELECTED out of a
    LITERAL container or slice IS resolved (#14326 round 16: ``('x', '…')[1]``,
    ``{'k': '…'}['k']``, ``'…_extra'[:-6]``, ``[*(…,)][0]`` were each measured GREEN at
    ``m=2 c=2 i=0`` before ``_subscript_strings`` — every element is a `Constant` in the
    source, like the ``+``/``*``/``%``/``.format`` spellings above), so the residual is the
    NAME-held form, not the subscript. ⛔ AN f-STRING IS RESOLVED WHEN ITS PARTS ARE (#14326 round
    13, measured: ``f"{'_tortoise_fts_query'}"`` and ``"%s_fts_query" % "_tortoise"`` and
    ``"_tortoise_fts_query".format()`` each reached the read at ``m=2 c=2 i=0``), so the
    residual is the UNRESOLVABLE part, not the spelling — the same narrowing the ``+`` arm
    made and this sentence previously contradicted by naming "an f-string" wholesale.
    ⛔ REACHES THROUGH A CONTAINER ARE **COVERED**, not residual
    (#14279 review F2/F3, both measured GREEN before the fix): a name bound to the literal
    and then carried through another name or a tuple into a ``getattr`` — ``_pair, = (_N,)``
    / ``_pair = (sdk, _N)`` / ``getattr(*[sdk, _N])`` — fires `_indirect_reads`, and the
    resolution itself is set-valued (``_str_constants``), so neither a later re-bind of the
    scratch name nor a subscript store can silence a genuine binding.
    Aliases ARE resolved on both halves (`alias = results;
    alias.sort()`, `env = os.environ; env[…] = …`), and handing either collection to a call
    that is not a known non-mutator is red. ⛔ The env-write half is one-sided on purpose: the
    harness does not RECORD which arm of `TORTOISE_FUSED_RERANK` was in force, so a
    baseline/after pair could straddle the gate and `compare()` would still call them
    comparable — tracked as #14289, not asserted here.
    """
    tree = ast.parse(_TOOL.read_text())
    parents = _parents_of(tree)
    consts = _str_constants(tree)
    # ⛔ THE CENSUS IS ONE FUNCTION, SHARED WITH THE TESTS THAT CHECK IT (#14279 review F2):
    # `_read_reach` is what this pin and every mutant table below call, so a test cannot
    # pass by resolving the read with a DIFFERENT (looser) copy of the walk.
    mentions, calls, indirect = _read_reach(tree, consts, parents)
    # ⛔ `indirect` FIRES FIRST, with its own diagnosis (#14281 review F3 — the comment that
    # explains why is on `_read_reach`, which owns the ordering).
    assert not indirect, (
        f"the tool reaches the read through "
        f"{getattr(indirect[0].func, 'id', None) or indirect[0].func.attr}() by name — that bypasses "
        "every check in this pin")
    # Anti-vacuity: TWO mentions, both calls, both the reads this pin is about (the
    # warm-up and the per-query measurement), and both inside `run()`.
    assert len(calls) == 2, (
        f"tools/read_wave_latency.py holds {len(calls)} read calls — this pin knows the "
        "warm-up and the per-query one")
    assert len(mentions) == 2, (
        f"the tool MENTIONS the read {len(mentions)} times but calls it {len(calls)} — an "
        f"alias, a string reference or a `getattr` is unaccounted for: "
        f"{[(ast.unparse(m), m.lineno) for m in mentions]}")
    assert all(_owner(m, parents) == "run" for m in mentions), (
        f"the read is mentioned outside `run()`: "
        f"{[(_owner(m, parents), m.lineno) for m in mentions]}")

    allowed = _door_read_contract()
    looped = [c for c in calls if _in_loop(c, parents)]
    assert len(looped) == 1, (
        f"expected exactly one read inside the measurement loop, found {len(looped)} of "
        f"{len(calls)}")
    measured = looped[0]
    warm = next(c for c in calls if c is not measured)
    # ⛔ ONE PREDICATE, SHARED WITH THE ROWS THAT PIN IT (#14326 review P1, measured:
    # replacing each of these three asserts with `True` left **27 passed**). A clause inside
    # this test body can only be red by a row that RE-IMPLEMENTS the walk, which is the
    # drift `_read_reach` exists to prevent; the predicate lives at module level so
    # `test_every_argument_clause_reds` runs the SAME code the pin runs.
    argument_findings = _call_argument_findings(calls, allowed)
    assert not argument_findings, argument_findings[0]
    # `limit` is the ONE argument that sets the survival window, and the pin's own message
    # says every forbidden argument "changes WHICH rows survive `limit`" — yet its VALUE was
    # unpinned, so `limit=1` in the measured read handed `compare()` a one-element page and
    # no reorder below position 0 could ever be observed: MEASURED GREEN (14 passed) on the
    # previous form. That re-vacuums the very verdict #14192 built this pin to protect, so
    # the depth is asserted, not merely named.
    limit_kw = next((k for k in measured.keywords if k.arg == "limit"), None)
    assert (limit_kw is not None and isinstance(limit_kw.value, ast.Constant)
            and limit_kw.value.value == 10), (
        f"the measured read (line {measured.lineno}) does not pin `limit=10` "
        f"({ast.unparse(limit_kw.value) if limit_kw is not None else 'omitted'}) — the "
        "DEPTH is a deliberate shape pin: the verdict assumes the same page size on every "
        "arm and on the rerankable pool (`DEFAULT_FUSED_RERANK_POOL = 40`), so a changed "
        "depth silently re-scopes what `rank_identical` is a claim about")
    assert "leg_trace" not in {k.arg for k in warm.keywords if k.arg}, (
        f"the warm-up (line {warm.lineno}) collects a `leg_trace` — its result is "
        "discarded, so that trace is not the measurement")

    # ⛔ THE MEASURED READ MUST FEED THE ARTIFACT, both halves of it: its page is the
    # `order` list the rank-identity verdict reads, and its trace is what the `legs`
    # table is built from. Counting keyword names (or iterating the sink somewhere)
    # was not enough — `results` discarded, `leg_trace=[]`, and a `_throwaway` looped
    # over by a dead `for` all stayed green while the artifact lost the measurement.
    run_defs = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                and n.name == "run"]
    assert len(run_defs) == 1, f"tools/read_wave_latency.py defines `run` {len(run_defs)}×"
    # ⛔ THE RECEIVER, NOT ONLY THE NAME (#14326 review P1, measured GREEN at `4e966eb`):
    # `_read_calls` matches `attr`/`id`, so `sdk._fast._tortoise_fts_query(…)` and
    # `sdk = _FastMirror(TortoiseSDK())` both satisfied every reach check while the harness
    # timed a door the product never opens. Shared with its rows, like `_read_reach`.
    receiver_findings = _receiver_findings(run_defs[0])
    assert not receiver_findings, receiver_findings[0]
    # ⛔ THE CONSTRUCTOR'S PROVENANCE (#14326 review P1, both MEASURED GREEN at `91db903`):
    # `_receiver_findings` proves the NAME `TortoiseSDK` is what `run()` constructs, and a
    # foreign import or a same-named subclass satisfies that while timing — or silently
    # re-ordering — a door the product never opens.
    constructor_findings = _constructor_provenance_findings(tree)
    assert not constructor_findings, constructor_findings[0]
    # ⛔ THE BLESSED MAPPER IS A NAME, SO IT CAN BE SHADOWED (#14281 review P1, measured
    # GREEN): `_result_id = lambda _r: "X"` inside `run()` made every `order` a list of
    # constants while the element check — which blesses the NAME — stayed satisfied.
    assert not _extra_bindings(tree, _ID_MAPPERS), (
        f"tools/read_wave_latency.py re-binds the blessed id mapper at "
        f"{_extra_bindings(tree, _ID_MAPPERS)} — the `order` element check blesses the "
        "name, so a shadow inside `run()` records a constant page and `compare()` reports "
        "`rank_identical: True` for ever")
    holder = parents.get(id(measured))
    while holder is not None and not isinstance(holder, (ast.Assign, ast.AnnAssign)):
        holder = parents.get(id(holder))
    # ⛔ AnnAssign COUNTS: `results: list[dict] = sdk._tortoise_fts_query(…)` IS assigned to
    # a name, and it was a false RED (measured) while the function's own `trace: list[dict]
    # = []` two lines above uses exactly that style.
    _holder_target = (holder.targets[0] if isinstance(holder, ast.Assign)
                      else getattr(holder, "target", None))
    assert isinstance(_holder_target, ast.Name), (
        f"the measured read (line {measured.lineno}) is not assigned to a name — the pin "
        "cannot follow its page into the artifact")
    page_var = _holder_target.id
    lt = next((k for k in measured.keywords if k.arg == "leg_trace"), None)
    assert lt is not None and isinstance(lt.value, ast.Name), (
        f"the measured read (line {measured.lineno}) collects `leg_trace` into "
        f"{ast.unparse(lt.value) if lt else 'nothing'} — the pin cannot follow its trace "
        "into the artifact, so a discarded sink would pass")
    trace_var = lt.value.id
    entries, artifact_append = _artifact_entries(run_defs[0])
    for key, var, what in (("order", page_var, "page"), ("legs", trace_var, "trace")):
        value = entries[key]
        assert isinstance(value, ast.ListComp), (
            f"the artifact's `{key}` is {ast.unparse(value)} — this pin follows a "
            f"comprehension over the measured {what}")
        # ⛔ The iterable must BE the variable. A name-PRESENCE test (`var in names(iter)`)
        # was measured GREEN with `for r in _stable(results)` — a helper that sorts the
        # page, which turns a genuine permutation into `rank_identical: True` — with
        # `_empty(results)`, and with `(results and _decoy)`. A false-red is the accepted
        # cost: `"order": _ids(results)` now reds too, because this pin cannot prove the
        # helper is the identity (the message says so).
        assert len(value.generators) == 1, (
            f"the artifact's `{key}` has {len(value.generators)} `for` clauses — the pin "
            f"cannot follow which one carries the measured {what}: {ast.unparse(value)}")
        gen = value.generators[0]
        # ⛔ THE GENERATOR'S FILTER CLAUSES, via the shared predicate (#14326 review P1,
        # measured: `assert not gen.ifs` replaced by `True` left 27 passed).
        filter_findings = _generator_filter_findings(key, gen)
        assert not filter_findings, filter_findings[0]
        assert _is_exactly(gen.iter, var), (
            f"the artifact's `{key}` iterates {ast.unparse(gen.iter)}, not `{var}` — the "
            f"measured read's {what} must be iterated AS ITSELF (a helper call there cannot "
            f"be proven not to sort/drop it): {ast.unparse(value)}")
        # ⛔ A MENTION IS NOT A USE: `_DUMMY if r else _DUMMY` names `r` and records a
        # constant page (measured green). For a conditional element only the VALUE branch
        # counts, so the target cannot sit purely in the condition.
        # ⛔ `_used` was the deleted `_used & _names_in(gen.target)` clause's only reader
        # (#14326 review P2): computing it here and never reading it is exactly the
        # "sentence that reads as a guard" shape this file removes.
        if key == "order":
            # ⛔ STRUCTURAL, because a mention is not a use (#14281 review P1, all three
            # measured GREEN and each records a CONSTANT page, so `compare()` reports
            # `rank_identical: True` over a permuted one):
            #     (r and None) or "X"        (_result_id(r), "X")[1]        r if False else "X"
            # The element must BE the target, or the target through the blessed id mapper.
            _targets = _names_in(gen.target)
            _ok = (isinstance(value.elt, ast.Name) and value.elt.id in _targets) or (
                isinstance(value.elt, ast.Call) and len(value.elt.args) == 1
                and not value.elt.keywords
                and getattr(value.elt.func, "id", None) in _ID_MAPPERS
                and isinstance(value.elt.args[0], ast.Name)
                and value.elt.args[0].id in _targets)
            assert _ok, (
                f"the artifact's `order` maps each hit through {ast.unparse(value.elt)} — "
                "only the hit itself or its id mapper is provably the measured page; a "
                "mention (a BoolOp arm, a tuple index, a constant branch) can record a "
                "constant and make a permuted page compare as identical")
        if key == "legs":
            # ⛔ DELETED (#14326 review P2, measured: replacing it with `True` left 27
            # passed). The move recorded here bought nothing: the `leg_item` assertion BELOW
            # already implies it — `leg_item` is a VALUE of `value.elt`, and that assertion
            # requires `_names_in(leg_item.func.value) & _names_in(gen.target)`, so the
            # element necessarily names the target. A clause that can never be the failing
            # row reads to the next lane as an independent guard and is not one — the reason
            # two other clauses were deleted earlier in this file.
            # `leg` IS the declared-legs key: a constant there folds every leg into one
            # name, and `compare()` never reads `legs`, so nothing downstream notices.
            leg_item = next((v for k, v in zip(value.elt.keys, value.elt.values, strict=True)
                             if isinstance(k, ast.Constant) and k.value == "leg"), None)
            assert (isinstance(leg_item, ast.Call)
                    and getattr(leg_item.func, "attr", None) == "get"
                    and len(leg_item.args) == 1 and not leg_item.keywords
                    and isinstance(leg_item.args[0], ast.Constant)
                    and leg_item.args[0].value == "leg"
                    and _names_in(leg_item.func.value) & _names_in(gen.target)), (
                f"the artifact's `legs` reports the leg id as "
                f"{ast.unparse(leg_item) if leg_item is not None else 'nothing'} — it must be "
                f"`{ast.unparse(gen.target)}.get(\"leg\")`, the trace entry's own id")
        rebinds = _rebound_after(run_defs[0], var, holder.lineno)
        assert not rebinds, (
            f"`{var}` is rebound at line(s) {rebinds} after the read — a sorted/emptied "
            f"copy would reach the artifact while the measured {what} was discarded")
        # ⛔ In-place mutation is the other half: `results.sort()` / `trace.clear()` /
        # `per_query[-1]["order"].sort()` all leave a mutated collection feeding the
        # artifact while every rebinding check passes (measured).
        # ⛔ THE REBIND RESOLUTION IS MODULE-SCOPED HERE, NOT `run()`-SCOPED (#14326 review at
        # `a29f3cd8`, item 2 — MEASURED GREEN: a module-level `list = lambda _x: results`
        # plus `_c = list(results); _c.sort()` inside `run()` was `1 passed` on a genuinely
        # permuted page, while the same two lines INSIDE `run()` red). The exemption is by
        # NAME, so the name can be rebound anywhere the module can see it; `tree` is what the
        # shipped pin has and `run_def` alone cannot. The row tests pass `run_def` themselves
        # and are unaffected.
        mutations = _mutated_after(run_defs[0], var, holder.lineno, artifact_append, tree)
        assert not mutations, (
            f"line(s) {mutations} mutate a collection AFTER the measured read — an in-place "
            f"sort/clear reaches the artifact while the measured {what} no longer matches it")

    # ⛔ AND MUTATING AN ELEMENT THROUGH AN ALIAS. The clause this comment used to name
    # (`"per_query" in receiver`) was PROVABLY SUBSUMED — `rows` is `{"per_query"} | …`
    # on every call — so it was deleted rather than left as a clause no mutant can red
    # (#14281 review F3, measured: deleting it red nothing). What covers the shapes is
    # `rows`: the literal receiver, a name bound to `per_query[-1]`, and (since
    # `bb14ebfc501c`) every `for`/comprehension target over `per_query`. NOTHING is
    # uniquely `_per_query_element_mutations`'s but its message and an element REBIND
    # (measured with it neutered: every listed shape stays RED — #14281 review P2). Both
    # halves are assertions; neither is a sentence.
    #
    # ⛔ BOTH ANCHORED AT THE READ, NOT AT THE `append` (#14281 review P1, measured). The
    # first form filtered on `artifact_append.lineno`, so a rebind placed INSIDE the loop
    # just BEFORE the `append` —
    #
    #     if per_query:
    #         per_query = [{**pq, "order": list(reversed(pq["order"]))} for pq in per_query]
    #     per_query.append({…})
    #
    # — was never inspected: MEASURED GREEN (14 passed) with every earlier row's page
    # reversed, and the same window re-opens the element shape
    # (`pq = per_query[-1]; pq["order"].sort()` placed there re-orders rows already
    # collected). That is the class whose removal `_mutated_after`'s docstring already
    # records ("a `lineno` filter cannot do scope work"); a filter here was asleep one
    # statement before the code it protects. Anchoring at `holder.lineno` (the measured
    # read) keeps the tool's own `per_query = []` initialiser legal: it sits at
    # tools/read_wave_latency.py:298, BEFORE the read at :305.
    per_query_rebinds = _rebound_after(run_defs[0], "per_query", holder.lineno)
    assert not per_query_rebinds, (
        f"`per_query` is rebound at line(s) {per_query_rebinds} after the measured read — "
        "a re-sorted/reversed copy would reach the returned report while every page the "
        "pin inspected was the measured one")
    # ⛔ AND THE RETURNED DICT ITSELF (#14281 review P1, measured). Everything above
    # inspects the dict that `per_query.append(...)` builds and the `per_query` NAME; but
    # `run()` RETURNS `{"per_query": <value>}`, and a re-derivation placed THERE is neither
    # a rebinding of the name nor a mutation of a recorded element:
    #
    #     "per_query": [dict(_pq, order=sorted(_pq["order"], key=str)) for _pq in per_query],
    #
    # MEASURED GREEN at cac5a1a5fa (16 passed) — and end-to-end that mutant turns a
    # genuinely permuted page into this module's own worst outcome: `compare()` returns
    # `rank_identical: True, rank_divergent_queries: []` and `main()` prints "fused order:
    # RANK-IDENTICAL on every query". That is exactly the false-PASS class the file exists
    # for, and the shape is statically visible, so it is not the declared "a name COMPUTED
    # at runtime" residual. `per_query[:-1]`, `list(reversed(per_query))` and
    # `_reordered(per_query)` are green the same way.
    returns = [n for n in ast.walk(run_defs[0]) if isinstance(n, ast.Return)]
    assert len(returns) == 1, (
        f"`run()` has {len(returns)} `return` statements — this pin follows the ONE dict "
        "that carries the report")
    report = returns[0].value
    assert isinstance(report, ast.Dict), (
        f"`run()` returns {ast.unparse(report)} — this pin follows the report dict")
    reported = {k.value: v for k, v in zip(report.keys, report.values, strict=True)
                if isinstance(k, ast.Constant) and isinstance(k.value, str)}
    assert "per_query" in reported, (
        "`run()`'s report dict no longer carries `per_query` — the verdict has lost its "
        "subject, so this pin must be re-derived rather than left passing on nothing")
    assert _is_exactly(reported["per_query"], "per_query"), (
        f"`run()` returns `per_query` as {ast.unparse(reported['per_query'])} — it must be "
        "the collected list ITSELF. A re-derivation here (a sorted or truncated copy, a "
        "helper call) re-orders the pages `compare()` reads while every check above still "
        "passes: measured GREEN with `rank_identical: True` on a genuinely permuted page. "
        "⛔ IF YOU ARE HERE BECAUSE YOU RENAMED THE LIST: this pin follows the collector by "
        "its NAME `per_query` (unlike `results`/`trace`, whose names it DERIVES from the AST), "
        "so renaming it is an ACCEPTED false red — edit this pin rather than hunting a "
        "permutation bug that does not exist (#14326 round 35 review cycle 5, measured: a "
        "rename-only edit reds here while the returned value IS the collected list)")
    element_mutations = _per_query_element_mutations(run_defs[0], holder.lineno)
    assert not element_mutations, (
        f"line(s) {element_mutations} mutate a RECORDED entry through an alias of a "
        "`per_query` element after the measured read — the returned report would carry a "
        "page the pin never inspected (a literal receiver is covered by "
        "`_mutated_after`; this is the aliased spelling, e.g. `pq = per_query[-1]; "
        "pq[\"order\"].sort()`, and the window includes the loop body before the "
        "`append`)")

    # ⛔ No `os.environ` mutation ANYWHERE (except the module-level setup pops): reports
    # `git_sha`/`db_uri`/`points`/
    # `corpus_seed` but NOT which arm of `TORTOISE_FUSED_RERANK` (or its pool/model
    # gates) was in force, so an armed or disarmed arm would still compare as
    # comparable — see #14289. The check is on the RECEIVER, because enumerating the
    # key shapes is how the previous version ended up with an inert `update` branch.
    mutated = _environ_mutations(tree, parents)
    assert not mutated, (
        f"the harness mutates `os.environ` at line(s) {mutated} (only the module-level "
        "`TORTOISE_API_URL`/`TORTOISE_API_KEY` pops are allowed) — the artifact does not "
        "record which arm of the fused-rerank gate was in force, so a baseline/after pair "
        "could straddle it and `compare()` would still call them comparable (#14289)")


# ── shared predicates for the clauses above (#14326 review P1/P2) ──────────────────────
#
# WHY THEY ARE MODULE-LEVEL FUNCTIONS AND NOT INLINE ASSERTS. A clause written inside
# `test_the_harness_reads_the_SHIPPED_door_and_names_NO_lever_flags` can only be made
# REDDENABLE by a row that RE-IMPLEMENTS the walk over a synthetic artifact — a second
# definition of the check, which is the drift `_read_reach`'s own docstring exists to
# prevent ("a table that resolved the read with its own walk could pass while the pin
# failed"). So the clauses the review measured as silence-able are predicates here, the
# pin calls them, and their rows call the SAME code on a mutated source. MEASURED before
# this refactor (each clause replaced by `True` in turn, all six times): **27 passed**.


def _call_argument_findings(calls: list[ast.Call], allowed: set[str]) -> list[str]:
    """Every reason a read CALL SITE names an argument this pin cannot see.

    Three clauses, each of which the review measured as un-red by any row
    (#14326 review P1): a POSITIONAL argument (the pin reads keywords, so a positional
    `kind` would be invisible), a SPLAT (`**opts` puts which arguments it carries beyond
    static reach), and an argument the `/v1/search` door does not name (`kind`,
    `threshold`, `order_by` … all change WHICH rows survive `limit`).
    """
    out: list[str] = []
    for call in calls:
        if call.args:
            out.append(
                f"line {call.lineno} hands the read {len(call.args)} POSITIONAL argument(s) — "
                "this pin reads keywords, so a positional `kind`/`limit` would be invisible")
        if any(k.arg is None for k in call.keywords):
            out.append(
                f"line {call.lineno} splats a mapping into the read, so which arguments it "
                "carries is invisible to this pin")
        extra = sorted({k.arg for k in call.keywords if k.arg is not None} - allowed)
        if extra:
            out.append(
                f"line {call.lineno} hands {extra} to the read — the `/v1/search` door names "
                f"only {sorted(allowed - {'leg_trace'})}, and each of these changes WHICH rows "
                "survive `limit`, so a mirror that names one times a reader the product does "
                "not run (and hides the page permutation the verdict exists to catch)")
    return out


_SDK_CONSTRUCTOR = "TortoiseSDK"
#: The MODULE `run()` imports that class from, inside its body. A `sys.modules` store under
#: this path is the same store as an attribute write, one level up (#14326 review P1).
_SHIPPED_MODULE = "tortoise.sdk"


def _receiver_findings(run_def: ast.AST) -> list[str]:
    """Every reason the measured read is not provably read OFF the shipped SDK object.

    ⛔ `_read_calls` MATCHES THE NAME, NOT THE OBJECT (#14326 review P1; BOTH spellings
    measured GREEN at `4e966eb` with this pin untouched, and the tool restored
    byte-identically afterwards):

        results = sdk._fast._tortoise_fts_query(…)   # a mirror/private door
        sdk = _FastMirror(TortoiseSDK())             # a wrapper around the door

    Both keep mentions=2 / calls=2 / the door's own argument names, so every reach check in
    this file passes while the harness times a reader the product never opens — the exact
    class this pin exists to catch, one layer BELOW the name it was checking. So the
    receiver is pinned too: every read call is an ATTRIBUTE of ONE bare name, that name is
    bound by `run()` itself (not taken as a parameter), exactly ONCE, and that one binding
    is a call of `TortoiseSDK` — not a call through anything else.
    """
    reads = [c for c in ast.walk(run_def) if isinstance(c, ast.Call)
             and (getattr(c.func, "attr", None) or getattr(c.func, "id", None)) in _READ_NAMES]
    out: list[str] = []
    receivers: set[str] = set()
    for call in reads:
        recv = call.func.value if isinstance(call.func, ast.Attribute) else None
        if not isinstance(recv, ast.Name):
            out.append(
                f"line {call.lineno} reaches the read through {ast.unparse(call.func)} — the "
                "receiver must be the one bare name holding the shipped `TortoiseSDK()`, so a "
                "computed receiver (an attribute segment, a call result, `getattr(sdk, …)("
                "…)`) can be a mirror door this pin cannot see through")
            continue
        receivers.add(recv.id)
    if len(receivers) > 1:
        out.append(
            f"the read is reached through {sorted(receivers)} — more than one receiver means "
            "at least one of them is not the SDK object `run()` constructs")
    params = {a.arg for a in run_def.args.posonlyargs} | {a.arg for a in run_def.args.args} \
        | {a.arg for a in run_def.args.kwonlyargs}
    for name in sorted(receivers):
        if name in params:
            out.append(
                f"`run` takes `{name}` as a PARAMETER — the receiver is then whatever the "
                "caller passes, and nothing in this file ties the measured read to the object "
                "the product's door reads off")
            continue
        stores = [n for n in ast.walk(run_def)
                  if isinstance(n, ast.Name) and n.id == name
                  and isinstance(n.ctx, ast.Store)]
        if len(stores) != 1:
            out.append(
                f"`{name}` is bound {len(stores)} time(s) inside `run()` — the receiver must "
                "be bound exactly once, because a later re-bind (`sdk = _FastMirror(sdk)`) "
                "swaps the object the read is measured on while every name-based check stays "
                "satisfied")
            continue
        stmt = next((s for s in ast.walk(run_def) if isinstance(s, (ast.Assign, ast.AnnAssign))
                     and any(n is stores[0] for n in ast.walk(s))), None)
        value = getattr(stmt, "value", None)
        if not (isinstance(value, ast.Call)
                and isinstance(value.func, ast.Name)
                and value.func.id == _SDK_CONSTRUCTOR):
            out.append(
                f"`{name}` is bound to {ast.unparse(value) if value is not None else 'a form '
                'this pin cannot follow'}, not `{_SDK_CONSTRUCTOR}()` — the pin proves which "
                "read DOOR is opened only while the receiver is the shipped SDK object")
    return out


def _generator_filter_findings(key: str, gen: ast.comprehension) -> list[str]:
    """Every reason the artifact's `key` comprehension reports something but the page.

    `order` must be the WHOLE measured page (a filter clause reports a subset while the
    pinned contract, "iterated AS ITSELF", still reads satisfied); `legs` is filtered by the
    tool's own documented rule and that rule is pinned as a SHAPE, so an added condition
    cannot quietly drop rows from the table. Both clauses are measured un-red by any row in
    their inline form (#14326 review P1/P2: `assert not gen.ifs` replaced by `True` left
    27 passed).
    """
    if key == "order":
        if gen.ifs:
            return [f"the artifact's `order` FILTERS the measured page "
                    f"(`if {ast.unparse(gen.ifs[0])}`) — the rank-identity verdict would "
                    "compare a subset of the page"]
        return []
    if len(gen.ifs) != 1:
        return [f"the artifact's `legs` has {len(gen.ifs)} filter clauses — the tool's rule "
                'is `if <entry>.get("leg")` and nothing else']
    cond = gen.ifs[0]
    target = ast.unparse(gen.target)
    # ⛔ ONE MESSAGE PER SUB-CLAUSE (#14326 review P1, measured at `4ed436353f`): the five
    # conjuncts below were written as ONE combined message, and deleting any four of them
    # (`attr == "get"`, `len(cond.args) == 1`, `not cond.keywords`, the
    # `_names_in(...) & _names_in(gen.target)` intersection) left **31 passed** — a row can
    # only red the clause whose message it asserts, so a combined verdict is one row wide.
    # Each refused shape now names ITS OWN clause, and `test_the_artifact_generator_filter_
    # clauses_red` carries one row that asserts exactly that clause's message.
    if not (isinstance(cond, ast.Call) and getattr(cond.func, "attr", None) == "get"):
        return [f"the artifact's `legs` filters on {ast.unparse(cond)}, not a `.get(...)` "
                f"CALL — the tool's rule is `{target}.get(\"leg\")`, which drops exactly the "
                "trace entries that record no leg"]
    if cond.keywords:
        return [f"the artifact's `legs` calls `.get(...)` with the keyword argument(s) "
                f"{sorted(k.arg or '**' for k in cond.keywords)} — the tool's rule names the "
                'key `"leg"` positionally and nothing else']
    if len(cond.args) != 1:
        return [f"the artifact's `legs` calls `.get(...)` with {len(cond.args)} positional "
                'argument(s) — the tool\'s rule is exactly `.get("leg")`']
    if not (isinstance(cond.args[0], ast.Constant) and cond.args[0].value == "leg"):
        return [f"the artifact's `legs` filters on {ast.unparse(cond)} — the tool's rule "
                f"is `{target}.get(\"leg\")`, which drops exactly the trace entries "
                "that record no leg"]
    if not _names_in(cond.func.value) & _names_in(gen.target):
        return [f"the artifact's `legs` filters on {ast.unparse(cond)}, whose `.get(\"leg\")` "
                f"is not read off the iterated entry (`{target}`) — a filter over a different "
                "object reports a table that is not the measured page's"]
    return []


#: The import-search-path ATTRIBUTES on `sys`: the objects a re-created registry entry is
#: READ from, i.e. the other half of every "what is written into `sys.modules`" arm.
_PATH_ATTRS = frozenset({"path", "meta_path", "path_hooks"})


def _is_import_path_object(node: ast.AST | None, ctx: _RegistryCtx) -> bool:
    """`sys.path` / `sys.meta_path` / `sys.path_hooks` — the search path OBJECT.

    ⛔ AND A READ OF IT THROUGH `sys`'s OWN MAPPING IS THE SAME OBJECT (#14326 round 32,
    MEASURED GREEN at `f7ff126b2c5a`: `sys.__dict__["path"] = ["/tmp/evil"] + sys.path`, and
    its `vars(sys)`/alias twins, each resolved a planted `tortoise/sdk.py` end-to-end while
    the pin stayed GREEN). The registry axis already learned this one key over —
    `sys.__dict__["modules"]` is `sys.modules` (round 24) — so the search-path predicate
    reading only the ATTRIBUTE spelling was the in-file asymmetry this file keeps removing.
    `ctx.Load` only: a STORE target is a mutation, and it is graded by the write arms below.
    """
    if isinstance(node, ast.Attribute):
        return node.attr in _PATH_ATTRS and _is_sys_ref(node.value, ctx.sys_names)
    if (isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Load)
            and _is_sys_dict_mapping(node.value, ctx)):
        return bool(_string_keys_of(node.slice, ctx.consts) & _PATH_ATTRS)
    return False


def _is_path_expr(node: ast.AST | None, ctx: _RegistryCtx,
                  path_names: frozenset[str]) -> bool:
    """Is this expression the search path — or ONE HOP out from it?

    Three hops, all of them the SAME object: the attribute (`sys.path`), a one-bind NAME
    alias of it (`_p = sys.path`), and an ELEMENT of it (`sys.path[0]`, the receiver in
    `sys.path[0].__init__`). A COPY is deliberately NOT here — `_p = sys.path[:]`,
    `list(sys.path)` and `copy.copy(sys.path)` all leave `path_names` empty (measured), so
    widening the alias axis cannot re-open the shallow-copy false-red class this file was
    cured of at `c380075b45ab`.
    """
    if _is_import_path_object(node, ctx):
        return True
    if isinstance(node, ast.Name):
        return node.id in path_names
    if isinstance(node, ast.Subscript):
        return _is_path_expr(node.value, ctx, path_names)
    return False


def _is_import_path_mutation(node: ast.AST, ctx: _RegistryCtx,
                             path_names: frozenset[str]) -> bool:
    """A write to the IMPORT SEARCH PATH — `sys.path` / `sys.meta_path` / `sys.path_hooks`.

    ⛔ THE AXIS THE PIN HAD NO VIEW OF (#14326 round 29 P2, MEASURED GREEN). Every other arm
    pins what is written INTO `sys.modules`; none pins WHERE a re-created entry is read FROM.
    With the search path poisoned, `del sys.modules["tortoise.sdk"]` stops being the inert no-op
    this file used to assert it was: the deferred `from tortoise.sdk import …` re-imports, and a
    planted `tortoise/sdk.py` earlier on the path becomes the constructor (measured end-to-end:
    the fake class resolved, the pin GREEN). Coarse here can only over-RED code that
    simultaneously rewrites the import path and empties the registry while the pin is watching,
    which is the premise.

    ⛔ THE RESIDUAL IS DECLARED HERE, NOT ASSUMED INTO SOMEONE ELSE'S (#14326 round 32 P3).
    "The note below" had no referent: the peer commit at `22ad6aa2c5` rewrote this
    cross-reference to point at a lettered residual list, and `grep` finds no such list (the
    two other `residual (a)`/`(b)` citations in this file were dangling for the same reason
    and are NAMED instead now). The reference the sentence needs is THIS one, and the pin
    that keeps it honest is the test named at the end of it.
    Rounds 31 and 32 both said "the unbounded class this file already declares as residual" and
    `grep` disproves it: this file declares residuals for CALLEE computation (a lambda,
    `operator.attrgetter`, `partial(getattr, …)`) and for string resolution, and nothing at all
    for finders or path shapes. The forms that genuinely remain GREEN here, each MEASURED at
    `f7ff126b2c5a` as a live lever, are the CALLEE-composition class applied to the path —
    not a distinct finder vocabulary:

        sys.__dict__.update(path=["/tmp/evil"] + sys.path)    (the `.update(**kw)` spelling)
        builtins.__dict__["setattr"](sys, "path", […])         (a callee via builtins' mapping)
        _p = operator.attrgetter("path")(sys); _p.insert(0, …)  (a computed path object)

    They are the same UNNUMBERABLE route the file already refuses to enumerate for the
    registry, so they are declared as residual and pinned GREEN in
    `test_the_IMPORT_PATH_axis_residuals_are_NAMED_and_pinned_GREEN` — not chased one spelling
    at a time. Closing any of them must EDIT that table rather than leave it contradicting the
    code.
    """
    if isinstance(node, ast.Call):
        func = node.func
        # ⛔ ONLY WHAT ADDS TO THE SEARCH PATH (#14326 round 31 P3, MEASURED). `remove`/
        # `clear`/`pop` shorten the path; they cannot point the import at an attacker
        # directory, and red-ing them redded ordinary teardown
        # (`sys.path.remove(_REPO_ROOT); del sys.modules["tortoise.sdk"]`) while the identical
        # `sys.path.pop(0)` stayed GREEN — so the verdict was an accident of vocabulary, not a
        # decision. Every member of this set is an ADDITION (or a wholesale refill).
        # ⛔ ONE ARM, BOTH POSITIONS, ONE PREDICATE (#14326 round 33). The spelling has two
        # places to name the path object — the RECEIVER (`sys.path.insert(…)`,
        # `_p.__setitem__(…)`) and `args[0]` (`list.insert(sys.path, …)`,
        # `list.__init__(_p, …)`) — and rounds 31-32 kept closing one position at a time:
        # the alias was resolved for the receiver and not for `args[0]` (named OPEN at
        # `22ad6aa2c5`), `insert`/`append`/`extend` were in the receiver set and `__init__`/
        # `__setitem__`/`__iadd__` in the arguments set, so the CROSS-PRODUCT was twice missed.
        # `_is_path_expr` answers "is this the path" the same way in both positions, and the
        # sets are one set, so the product cannot be half-covered again.
        if (isinstance(func, ast.Attribute)
                and func.attr in {"insert", "append", "extend",
                                  "__init__", "__setitem__", "__iadd__"}
                and ((node.args and _is_path_expr(node.args[0], ctx, path_names))
                     or _is_path_expr(func.value, ctx, path_names))):
            return True
        # ⛔ `setattr(sys, "path", …)` IS THE SAME WRITE (#14326 round 31 P2, MEASURED GREEN
        # end-to-end). The file already owns a `launderers` axis for exactly this spelling on
        # the attribute-set axis; the search path needs it too.
        return bool(_callee_names_of(func) & ({"setattr"} | ctx.builtin_aliases)
                    and len(node.args) >= 2 and _is_sys_ref(node.args[0], ctx.sys_names)
                    and (_string_keys_of(node.args[1], ctx.consts) & _PATH_ATTRS))
    # ⛔ `ast.AugAssign` IS IN THIS TUPLE DELIBERATELY, and a SEPARATE `AugAssign` arm was
    # measured VACUOUS (#14326 round 30, mutation-tested): `sys.path += […]` is an AugAssign
    # whose target is the same `sys.path` Attribute, so this branch already covers it once the
    # target is read correctly, and deleting the dedicated arm left `41 passed`. Only the
    # METHOD spelling `sys.path.__iadd__(…)` is a `Call` and is handled above.
    if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            # ⛔ THE TARGET ITSELF, NOT ITS BASE (#14326 round 30 P1, MEASURED). This read
            # `getattr(target, "value", None)`, which for a plain `sys.path = […]` yields the
            # `Name` `sys` — never an `Attribute` — so the branch fired for a SUBSCRIPT target
            # only, and `sys.path = …` / `sys.path += …` / `sys.meta_path = …` /
            # `sys.path_hooks = …` were all measured GREEN end-to-end (a planted
            # `/tmp/evil/tortoise/sdk.py` resolved, pin GREEN). The commit that added it
            # claimed "a plain assign" — that claim was FALSE, which is the third instance of
            # this lane's one recurring defect: a mechanism asserted rather than measured.
            if isinstance(target, ast.Subscript):
                # ⛔ `sys.__dict__["path"] = …` IS THE SAME STORE (#14326 round 32 P2,
                # MEASURED GREEN at `f7ff126b2c5a`: it and its `vars(sys)`/alias twins each
                # resolved a planted `tortoise/sdk.py` end-to-end). The registry axis has
                # graded this spelling since round 24 (`sys.__dict__["modules"] = …`); the
                # path axis reading only the ATTRIBUTE spelling was the asymmetry, one key
                # over. Narrowed to `sys`'s own mapping, as every form of
                # `_is_sys_dict_mapping` is, so `_cfg["path"] = …` stays GREEN.
                if (_is_sys_dict_mapping(target.value, ctx)
                        and (_string_keys_of(target.slice, ctx.consts) & _PATH_ATTRS)):
                    return True
                element: ast.AST | None = target.value
            else:
                element = target
            if not _is_path_expr(element, ctx, path_names):
                continue
            # ⛔ A REBIND OF THE ALIAS NAME IS NOT A MUTATION OF THE PATH (#14326 round 32,
            # the FALSE-RED guard). `_p = ["/tmp/evil"]` (and `_p: list = […]`) replaces this
            # local NAME and leaves `sys.path` untouched — it is the same rebind as
            # `_p = sys.path` and must stay GREEN. `_p += […]` is `list.__iadd__`, which DOES
            # mutate the shared list in place (measured: the entry lands on `sys.path`), so
            # only a plain `Name` under a non-`AugAssign` statement is skipped. An ATTRIBUTE
            # target is never skipped: `sys.path = …` IS the write (round 30 P1).
            if (element is target and isinstance(target, ast.Name)
                    and not isinstance(node, ast.AugAssign)):
                continue
            return True
    return False


def _empties_the_registry(node: ast.AST, ctx: _RegistryCtx,
                          registry_aliases: frozenset[str]) -> bool:
    """The registry is EMPTIED — by `del`, or by the four call spellings of the same thing.

    ⛔ NOT AN ARM ON ITS OWN, AND THAT IS STILL THE POINT (#14326 round 29 P2). On its own the
    registry is re-created from the shipped class, or the import fails. It is an arm only
    TOGETHER with an import-path mutation, which is why the caller requires both.
    ⛔ AND "ON ITS OWN" IS NARROWER THAN IT READS (#14326 round 30 P3, MEASURED GREEN):
    `_PATH_ATTRS` covers `sys.path` / `sys.meta_path` / `sys.path_hooks` only, so a package's own
    `tortoise.__path__ = ["/tmp/evil"] + tortoise.__path__` paired with a `del` is a lever this
    arm does not see. That is the SAME axis (where a re-created entry is read from), just one
    receiver over, and it is DECLARED as residual in `_is_import_path_mutation`'s docstring
    (pinned GREEN by `test_the_IMPORT_PATH_axis_residuals_are_NAMED_and_pinned_GREEN`) rather than
    chased. ⛔ NOT "residual (e) in the note below": that wording named a lettered list this file
    does not contain, so it was a cross-reference to nothing — the same defect one hop over.

    ⛔ BUT "EMPTIED" IS NOT `ast.Delete` (#14326 round 30 P2, MEASURED GREEN): `clear()` and
    `pop()` empty the registry just as completely while being no `Delete` node at all —
    `sys.path.insert(0, "/tmp/evil"); sys.modules.clear()` and the `pop` twin both resolved a
    planted class end-to-end with the pin GREEN, as did `dict.clear(sys.modules)` and
    `sys.modules.__delitem__(…)`. The stated premise of this arm was "the registry is EMPTIED",
    so the implementation has to mean what the premise says rather than name one syntax.

    ⛔ AND AN ALIAS OF THE REGISTRY IS THE REGISTRY (#14326 round 30 P2, MEASURED GREEN:
    `_m = sys.modules; sys.path.insert(0, "/tmp/evil"); del _m["tortoise.sdk"]` was GREEN).
    Every STORE arm already resolves `registry_aliases` — this one did not, the exact in-file
    asymmetry the round-21 note exists to remove. Aliases are PASSED IN rather than recomputed
    here so the set has one owner, the same reason `_RegistryCtx` exists.
    """
    def _is_reg(expr: ast.AST | None) -> bool:
        if expr is None:
            return False
        return _is_module_registry(expr, ctx) or (
            isinstance(expr, ast.Name) and expr.id in registry_aliases)

    def _drops_the_deferred_target(key: ast.AST | None) -> bool:
        """The key removed is one whose loss makes `from tortoise.sdk import …` RE-EXECUTE.

        ⛔ REMOVING A *tortoise SUBMODULE* IS NOT THE SAME AS REMOVING THE PACKAGE (#14326
        round 30, MEASURED — a FALSE RED on the shipped artifact, the expensive direction).
        The coarse "any tortoise* key" rule reddened `tools/read_wave_latency.py`, whose
        `sys.modules.pop(_AB_MODULE, None)` cleans up its OWN registration after
        `loader.exec_module` — and `_AB_MODULE` is `"tortoise._ab_14192_old_search_engine"`,
        which `"tortoise.sdk"` never resolves through. Deleting it leaves `tortoise` and
        `tortoise.sdk` cached, so nothing re-imports and there is no lever. Only an ANCESTOR
        of the deferred target (`tortoise`, `tortoise.sdk`) forces the re-execution, because
        the dotted import walks each prefix — which is exactly why the reviewer's
        `del sys.modules["tortoise"], sys.modules["tortoise.sdk"]` IS a lever and this is not.
        """
        keys = _string_keys_of(key, ctx.consts)
        return any(k == _SHIPPED_MODULE or _SHIPPED_MODULE.startswith(k + ".")
                   for k in keys)

    if isinstance(node, ast.Delete):
        for t in node.targets:
            if _is_reg(t):
                return True
            if isinstance(t, ast.Subscript) and _is_reg(t.value) \
                    and _drops_the_deferred_target(t.slice):
                return True
        return False
    if isinstance(node, ast.Call):
        func = node.func
        # ⛔ `clear()` empties EVERYTHING; `popitem()` does NOT — it removes ONE ARBITRARY
        # entry (MEASURED: 73 -> 72 on `sys.modules`), which round 30 wrongly described as
        # "empty EVERYTHING, so they always take the target with them". It is kept in this
        # set because the arbitrary entry CAN be the target, so red-ing is the safe direction
        # for a clause that cannot be proven safe — but the mechanism is now stated as what it
        # is, and a row pins it rather than leaving it reachable-but-unmeasured.
        if (isinstance(func, ast.Attribute) and func.attr in {"clear", "popitem"}
                and _is_reg(func.value)):
            return True
        if (isinstance(func, ast.Attribute) and func.attr in {"pop", "__delitem__"}
                and _is_reg(func.value) and node.args
                and _drops_the_deferred_target(node.args[0])):
            return True
        # `dict.clear(registry)` / `dict.pop(registry, key)` — the unbound spellings
        if (isinstance(func, ast.Attribute) and _callee_names_of(func.value) & {"dict"}
                and node.args and _is_reg(node.args[0])):
            if func.attr in {"clear", "popitem"}:
                return True
            if func.attr in {"pop", "__delitem__"} and len(node.args) > 1 \
                    and _drops_the_deferred_target(node.args[1]):
                return True
    return False


def _constructor_provenance_findings(tree: ast.AST) -> list[str]:
    """Every reason the name `TortoiseSDK` is not provably the class the product ships.

    ⛔ THE RECEIVER CLAUSE PINNED A NAME, NOT A CLASS (#14326 review P1, both spellings
    MEASURED GREEN at `91db903`): `_receiver_findings` proves `sdk = TortoiseSDK()`, so the
    NAME is satisfied by anything that carries it —

        from _compat.sdk import TortoiseSDK          # a foreign constructor
        from tortoise.sdk import TortoiseSDK as _R   # …and a class-level wrapper:
        class TortoiseSDK(_R):                      # every page re-sorted before `order`
            __getattr__ = staticmethod(
                lambda _s, _n: (lambda *a, **k: sorted(_R.__getattribute__(_s, _n)(*a, **k),
                                                       key=lambda _r: str(_r.get("id")))))

    Both left the pin GREEN (`31 passed`) while the harness timed — or silently re-ordered
    — a door the product never opens. So the NAME's provenance is pinned too: the only
    binding of `TortoiseSDK` anywhere in the file must be `from tortoise.sdk import
    TortoiseSDK`, and no class/def/assignment may carry that name.
    """
    out: list[str] = []
    bound_from_shipped = 0
    # ⛔ ONE ROOT, NOT SIX SPELLINGS (#14326 review P1, EACH MEASURED GREEN at `b6be337`).
    # The three arms below matched a LITERAL (`ast.Constant` key, a bare `setattr` name, an
    # `ast.Name` receiver) while the file already owned resolvers for exactly these hops —
    # `_string_keys_of`/`_possible_strings` for the KEY, `_namespace_rebind_names` for the
    # mapping mutators, `_aliases_of` for the receiver and the LAUNDERER. Matching the
    # spelling instead of resolving it is the same in-file asymmetry that let
    # `_p = partial(setattr, …); _p("print", getattr)` past the READ census.
    #
    # The artifact's `run()` imports the class INSIDE its body, so EVERY one of these is a
    # store the deferred import resolves at CALL time, and the pin cannot prove WHICH object
    # receives it — so the store reds:
    #
    #   _t.__dict__.update(TortoiseSDK=_Mirror)        vars(_t).update(TortoiseSDK=_Mirror)
    #   _t.__dict__.__setitem__("TortoiseSDK", _Mirror) sys.modules["tortoise.sdk"] = _Mirror
    #   setattr(tortoise.sdk, "TortoiseSDK", _Mirror)    setattr(_t, _k, _Mirror)
    #   setattr(*[_t, "TortoiseSDK", _Mirror])           _t.__dict__["Tortoise" + "SDK"] = …
    consts = _str_constants(tree)
    #: Every name in this tree that denotes the `sys` module — `sys` plus `import sys as _s`
    #: aliases. Threaded into `_is_module_registry` so the RESOLVED registry forms follow an
    #: alias instead of the literal spelling (#14326 round 21).
    sys_names = _sys_names_of(tree)
    #: Every name in this tree bound to `sys`'s OWN `__dict__` (`_d = sys.__dict__`), so the
    #: mapping alias is the mapping — the same one-hop rule the registry and namespace axes
    #: already apply (#14326 round 23).
    #: One call, not a loop: `_aliases_of` is a fixpoint internally, so an outer round is
    #: unreachable (measured `41 passed` with the loop collapsed — #14326 round 23).
    #: Names bound to one of `_REGISTRY_BUILTINS`, so a callee ALIAS resolves (#14326 round 24).
    #: ⛔ COMPUTED BEFORE `sys_dict_names`, WHICH READS IT (#14326 round 25, MEASURED GREEN:
    #: building the set with `builtin_aliases=frozenset()` left
    #: `_g = getattr; __d = _g(sys, "__dict__"); __d["modules"] = {"tortoise.sdk": _Mirror}`
    #: and its `vars`/`|=`/`.update` siblings GREEN — an alias of the mapping reached through
    #: an alias of the CALLEE was never collected, so three arms downstream were blind to it).
    #: A ctx field passed empty at the one site that fills it is exactly the hazard
    #: `_RegistryCtx` exists to remove, one ordering hop out.
    #: ⛔ THE MODULE-QUALIFIED VALUE IS THE BUILTIN (#14326 round 26, MEASURED GREEN:
    #: `_g = builtins.getattr; _g(sys, "modules")[…] = …` and `_v = builtins.vars;
    #: _v(sys)["modules"][…] = …` each replaced the registry while the INLINE
    #: `builtins.getattr(...)`/`builtins.vars(...)` spellings were RED — the alias set
    #: collected a bare `Name` only, so the module-qualified VALUE matched nothing).
    #: ⛔ AND THE MODULE ITSELF IS RESOLVED, NOT SPELLED (#14326 round 27, MEASURED GREEN:
    #: `from builtins import getattr as _g; _g(sys, "modules")[…] = …`,
    #: `import builtins as _b; _g = _b.getattr; _g(sys, "modules")[…] = …` and the `vars`
    #: twins each installed a mirror while the `import builtins` spelling was RED). Fixing
    #: the value's spelling one round earlier left the MODULE's spelling open — the same
    #: asymmetry one hop out, so `builtins` is aliased (`import builtins as _b`, `_b =
    #: builtins`) the way `sys` already is, and a direct `from builtins import <builtin>`
    #: is collected too.
    #: ⛔ THE ROOT SET CARRIES `__builtins__`, WHICH IS NOT A SPELLING OF `builtins` BUT THE
    #: ONLY BINDING-FREE WAY TO REACH IT (#14326 round 30, EACH MEASURED GREEN at
    #: `de42a75ac4b5` while the `import builtins` twin was RED — the whole alias axis, BOTH
    #: axes, walked past every `__builtins__` spelling):
    #:
    #:     __builtins__.getattr(sys, "modules")[…] = _Mirror          GREEN (direct is RED:
    #:                                                                  only the ALIAS was open)
    #:     _b = __builtins__; _g = _b.getattr; _g(sys, "modules")[…]   GREEN
    #:     _g = __builtins__.getattr; __d = _g(sys, "__dict__")        GREEN (STORE axis)
    #:     __builtins__.vars(sys)["modules"][…] = _Mirror              GREEN
    #:
    #: CPython injects `__builtins__` into EVERY module's globals, so unlike the round-28
    #: `_b = builtins` rows — which raise `NameError` without an `import builtins` and therefore
    #: pinned a lever that CANNOT RUN — this one needs no import at all. It is a ROOT, not an
    #: alias: seeding it here is the same one-word resolve `sys` got in round 21.
    builtins_names, builtins_dict_names = _builtins_root_names(tree)
    imported_builtins = frozenset(
        (alias.asname or alias.name)
        for _node in ast.walk(tree)
        if isinstance(_node, ast.ImportFrom) and _node.module == "builtins"
        for alias in _node.names
        if alias.name in _REGISTRY_BUILTINS)
    builtin_aliases = imported_builtins | frozenset(_aliases_of(
        tree,
        lambda _n: (isinstance(_n, ast.Name) and _n.id in _REGISTRY_BUILTINS)
        or (isinstance(_n, ast.Attribute) and _n.attr in _REGISTRY_BUILTINS
            and isinstance(_n.value, ast.Name) and _n.value.id in builtins_names)
        # ⛔ AND THE BUILTIN REACHED THROUGH the module's own MAPPING (#14326 round 30):
        # `_g = builtins.__dict__["getattr"]` and its `vars(…)` / `getattr(…)` /
        # `dict.get(…)` twins are the CALLEE laundered one composition further out, so they
        # join the same set every arm already intersects rather than each needing an arm.
        or _builtin_lookup_name(_n, builtins_names, consts, builtins_dict_names,
                                _builtin_name_aliases(tree)) is not None,
        loops=True))
    sys_dict_names = frozenset(_aliases_of(
        tree, lambda _n: _is_sys_dict_mapping(
            # ⛔ `builtins_dict_names` IS FILLED HERE EVEN THOUGH IT IS INERT-BY-MUTANT AT THIS
            # ONE SITE (#14326 round 30, MEASURED: passing `frozenset()` here leaves
            # `99 passed` — the alias walk this ctx feeds reaches no callee-lookup shape). It
            # is filled rather than defaulted because an EMPTY set at a construction site is
            # precisely the hazard `_RegistryCtx` exists to make impossible; the field is
            # required, so every site must say what it means.
            _n, _RegistryCtx(sys_names, frozenset(), consts, builtin_aliases, builtins_names,
                             builtins_dict_names)),
        loops=True))
    #: ONE object for every resolved-registry question — see `_RegistryCtx` for why the
    #: positional form was a hazard (#14326 round 24).
    ctx = _RegistryCtx(sys_names, sys_dict_names, consts, builtin_aliases, builtins_names,
                       builtins_dict_names)
    # The namespace MAPPINGS, plus the IMPORT REGISTRY `from tortoise.sdk import …`
    # resolves THROUGH (`sys.modules`), each with its one-bind aliases resolved.
    namespaces = frozenset(
        _aliases_of(tree, lambda _n: _is_namespace_expr(_n)
                    or _is_module_registry(_n, ctx), loops=True)
    )
    launderers = frozenset(_aliases_of(tree, _is_setattr_like, loops=True))
    # ⛔ A NAME ALIAS OF THE REGISTRY IS THE REGISTRY (#14326 review P1, measured:
    # `_sm = sys.modules; _sm |= {"tortoise.sdk": _Mirror}` was GREEN while the direct
    # spelling red — the alias axis, not a new spelling).
    registry_aliases = frozenset(_aliases_of(
        tree, lambda _n: _is_module_registry(_n, ctx), loops=True))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name != _SDK_CONSTRUCTOR:
                    continue
                bound = alias.asname or alias.name
                if node.module != "tortoise.sdk" or alias.asname is not None:
                    out.append(
                        f"line {node.lineno} binds `{_SDK_CONSTRUCTOR}` from "
                        f"`{node.module}` as `{bound}` — the pin names the CLASS the product "
                        f'ships, so its only binding may be `from tortoise.sdk import '
                        f"{_SDK_CONSTRUCTOR}` — an ALIASED import is its own red because the "
                        "receiver clause needs the bare name, so either the alias is unused "
                        "(and the bare name is bound somewhere else) or it is a second "
                        "spelling this pin cannot resolve")
                else:
                    bound_from_shipped += 1
        elif isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name == _SDK_CONSTRUCTOR:
            out.append(
                f"line {node.lineno} DEFINES `{_SDK_CONSTRUCTOR}` — a subclass or wrapper "
                "wearing the shipped class's name satisfies the receiver clause while every "
                "page it returns can be rewritten (measured: a `__getattr__` that sorts each "
                "page left the pin GREEN before this clause)")
        elif isinstance(node, ast.AugAssign):
            # ⛔ THE SAME STORE, ONE STATEMENT KIND OVER (#14326 review P1, MEASURED GREEN
            # end-to-end at `346c11d6`):
            #
            #     _d = _tsdk.__dict__
            #     _d |= {"TortoiseSDK": _Mirror}      # `run()`'s deferred import resolves
            #                                         # to the mirror; pin 35 passed
            #
            # Both halves were ALREADY resolved by the machinery above — `_d` lands in
            # `namespaces` (`_aliases_of` over `_is_namespace_expr`) and the `Dict` key is a
            # constant — so the ONLY thing blocking the red was that this arm's sibling
            # enumerates `(Assign, AnnAssign, NamedExpr)` and `AugAssign` carries `.target`
            # SINGULAR, not `.targets`. The asymmetry is in-file, not a boundary: five other
            # guards here enumerate `AugAssign` as a bind form, and an enumeration that took
            # every form EXCEPT it is the exact defect `_str_constants` records.
            target = node.target
            # ⛔ THE KEY AXIS DEPENDS ON THE RECEIVER (#14326 review P1, MEASURED GREEN at
            # `474d19f2`): a module NAMESPACE (`x.__dict__`, an alias of it) is keyed by the
            # CLASS NAME, but the IMPORT REGISTRY is keyed by the MODULE NAME — so
            # `sys.modules |= {"tortoise.sdk": _Mirror}` replaces the class `run()`'s
            # deferred import resolves and was GREEN while the constructor-keyed spelling was
            # red. One receiver, two key vocabularies; the filter follows the receiver.
            keys = set(_mapping_keys_of(node.value, consts))
            registry = _is_module_registry(target, ctx) or (
                isinstance(target, ast.Name) and target.id in registry_aliases)
            receiver = registry or (isinstance(target, ast.Attribute)
                                    and target.attr == "__dict__") or \
                (isinstance(target, ast.Name) and target.id in namespaces)
            # ⛔ `_d = sys.__dict__; _d |= {"modules": {...}}` REPLACES THE REGISTRY (#14326
            # round 27, MEASURED GREEN: it replaced `sys.modules` at runtime while
            # `_d["modules"] = {…}` and `_d["modules"] |= {…}` were both RED). The key here
            # is `"modules"`, which is neither the class key nor the module key, so the
            # receiver-keyed vocabulary below could not see it — the third vocabulary is a
            # WHOLE-REGISTRY REPLACEMENT, and it only exists on `sys`'s own mapping.
            # ⛔ AND THE DIRECT FORM IS A LEVER TOO — MY ROUND-27 CLAIM WAS FALSE (#14326
            # round 28 P1, MEASURED). Round 27 left `sys.__dict__ |= {…}` GREEN on the theory
            # that it "raises `AttributeError: readonly attribute` and cannot be a lever". It
            # raises — AFTER `dict.__ior__` has already mutated the dict in place:
            #
            #     sys.__dict__ |= {"modules": {…}}   ->  AttributeError: readonly attribute
            #     "modules" in sys.__dict__          ->  True
            #
            # so the one-line `try/except AttributeError: pass` (or `contextlib.suppress`)
            # keeps the mutation and drops the error, and `run()`'s deferred import constructs
            # the mirror. Both were MEASURED GREEN end-to-end. The over-red now lands on code
            # that CANNOT COMPLETE SUCCESSFULLY anyway, so it is the safe direction, and the
            # false-red control that pinned the GREEN is replaced by a fail-open row.
            if ("modules" in keys
                    and ((isinstance(target, ast.Name) and target.id in sys_dict_names)
                         or _is_sys_dict_mapping(target, ctx))):
                out.append(
                    f"line {node.lineno} REPLACES `modules` on `{ast.unparse(target)}` — "
                    "`|=` here stores the key `modules` on `sys`'s own namespace, so "
                    "`run()`'s deferred `from tortoise.sdk import …` resolves THROUGH a "
                    "swapped registry at CALL time"
                )
                # ⛔ UNREACHABLE-BY-MUTANT, AND SAID SO (#14326 round 28). Removing this
                # `continue` leaves the file at `41 passed`: the `hit` test below needs a key
                # from `_SHIPPED_MODULE`/`_SDK_CONSTRUCTOR`, and this arm enters only on the
                # `modules` key. It stays because it makes the TWO MESSAGES mutually exclusive
                # by construction (a mapping spelling both keys would otherwise be reported as
                # REPLACES *and* as a keyed store for one node), which is the property rows
                # assert with `any(…)` and so cannot pin.
                continue
            # ⛔ AND AN ANCESTOR IS THE TARGET HERE TOO (#14326 round 31 P2, MEASURED GREEN:
            # `sys.modules |= {"tortoise": _pkg}` followed by `del sys.modules["tortoise.sdk"]`
            # re-executed the planted package's `sdk.py`). The exact-key test above was fixed
            # one statement kind over and this twin was missed — the same asymmetry, twice.
            hit = {k for k in keys
                   if (k in ({_SHIPPED_MODULE} if registry else {_SDK_CONSTRUCTOR})
                       or (registry and _SHIPPED_MODULE.startswith(k + ".")))}
            if receiver and hit:
                out.append(
                    f"line {node.lineno} AUGMENTS `{ast.unparse(target)}` with "
                    f"`{sorted(hit)[0]}` — `|=` on a namespace mapping is the same store as "
                    "an attribute write, and `run()`'s deferred import resolves it at CALL "
                    "time (measured GREEN before this arm)")
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                # ⛔ REPLACING THE WHOLE REGISTRY IS THE SAME STORE (#14326 round 24, MEASURED
                # GREEN at `bce008ce`: `sys.modules = {"tortoise.sdk": _Mirror}`,
                # `sys.__dict__["modules"] = {…}`, `vars(sys)["modules"] = {…}` and
                # `__d = sys.__dict__; __d["modules"] = {…}` all left the pin GREEN, while
                # every INSERTION spelling was RED). It needs no key vocabulary at all: the
                # object `run()`'s deferred import resolves is replaced outright, which is the
                # simplest possible spelling of the premise this pin exists for. The check is
                # receiver-only, so it cannot red an unrelated store.
                # ⛔ AND AN UNPACK TARGET STORES TO EACH ELEMENT (#14326 round 25, MEASURED
                # GREEN: `sys.modules, _x = {"tortoise.sdk": _Mirror}, 1`, `[sys.modules] = […]`
                # and `sys.__dict__["modules"], _x = …` each replaced the registry with the pin
                # GREEN). ELEMENTS ONLY, never `ast.walk(target)`: a deep walk would also visit
                # the INNER `sys.modules` attribute of the shipped tool's own legitimate
                # `sys.modules[_AB_MODULE] = module`, which is measured GREEN and must stay so.
                # ⛔ `del` IS INERT **ONLY WHILE THE IMPORT PATH IS UNMODIFIED** (#14326 round
                # 29, MEASURED). Round 28 wrote here that deleting an entry "makes the
                # deferred import resolve the genuine class, or fail — it cannot install a
                # mirror", and pinned `del sys.modules[...]` GREEN on that sentence. The
                # sentence is FALSE as written and was never measured: with the search path
                # poisoned, the re-import resolves an ATTACKER's class —
                #
                #     sys.path.insert(0, "/tmp/evil")                    # or meta_path.insert
                #     del sys.modules["tortoise"], sys.modules["tortoise.sdk"]
                #
                # resolved `<class 'tortoise.sdk.TortoiseSDK'>` from `/tmp/evil/tortoise/sdk.py`
                # with the pin GREEN. So `del` STAYS inert (it is, on its own), and the ARM goes
                # on the path axis that makes it live — see `_swaps_the_import_path` below.
                # ⛔ This is the SAME defect round 28 P1 corrected one axis over: a mechanism
                # argued from theory and then pinned as a control instead of measured.
                replaced = _registry_replacement(target, ctx)
                if replaced is not None:
                    out.append(
                        f"line {node.lineno} REPLACES `{ast.unparse(replaced)}` — `run()`'s "
                        "deferred `from tortoise.sdk import …` resolves THROUGH this mapping "
                        "at CALL time, so assigning it outright swaps the class without "
                        "naming the constructor (measured GREEN before this arm)")
                    continue
                if isinstance(target, ast.Attribute):
                    # ⛔ A STORE ON A MODULE REBINDS THE DEFERRED IMPORT (#14326 review P1,
                    # MEASURED GREEN at `a77e4fe3`): `run()` imports the class INSIDE the
                    # body, so it resolves the attribute at CALL time — a module-level
                    #
                    #     import tortoise.sdk as _t
                    #     _t.TortoiseSDK = _Mirror
                    #
                    # makes `sdk = TortoiseSDK()` construct the mirror while every clause
                    # here passes (the ClassDef arm sees `_Mirror`, and the in-`run`
                    # import satisfies `bound_from_shipped`). This pin cannot prove WHICH
                    # object receives an attribute store, so the store reds.
                    if target.attr == _SDK_CONSTRUCTOR:
                        out.append(
                            f"line {node.lineno} STORES `{_SDK_CONSTRUCTOR}` on "
                            f"`{ast.unparse(target.value)}` — `run()` resolves the class at "
                            "CALL time, so a module-attribute store swaps the object the "
                            "receiver clause trusts (measured: `import tortoise.sdk as _t;"
                            " _t.TortoiseSDK = _Mirror` left the pin GREEN)")
                    continue
                if _SDK_CONSTRUCTOR in _bind_target_names(target):
                    out.append(
                        f"line {node.lineno} ASSIGNS `{_SDK_CONSTRUCTOR}` — the receiver "
                        "clause would accept whatever was assigned there as the shipped SDK")
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            # ⛔ A LOOP TARGET IS A STORE TOO (#14326 round 25, MEASURED GREEN:
            # `for sys.modules in ({"tortoise.sdk": _Mirror},): pass` replaced the registry
            # with the pin GREEN — every other bind form the file resolves, this one missing).
            # ⛔ AND THE `AsyncFor` MEMBER IS PINNED, NOT ASSUMED (#14326 round 27: dropping it
            # from this tuple left the file at `41 passed` while the `async for` twin of that
            # lever stayed GREEN — the row is in `registry_replacements`, and the census in
            # `test_the_ASYNC_for_arms_…` now names this arm).
            replaced = _registry_replacement(node.target, ctx)
            if replaced is not None:
                out.append(
                    f"line {node.lineno} REPLACES `{ast.unparse(replaced)}` — a `for` target "
                    "is a store, and rebinding the registry there swaps the class `run()`'s "
                    "deferred import resolves at CALL time (measured GREEN before this arm)")
        elif isinstance(node, (ast.Call, ast.Subscript)):
            # ⛔ THE SAME STORE, CARRIED AS A STRING / A MAPPING / A PARTIAL (#14326 review
            # P1 — FIVE spellings, EACH MEASURED GREEN at `b6be337`). One root: the store
            # was matched as a SPELLING (`ast.Constant` key, a bare `setattr`, an
            # `ast.Name` receiver). It is now RESOLVED with the machinery this file already
            # owns, so a receiver alias, a laundered key, a `partial`, a splat and the
            # `sys.modules` registry all land in the same red.
            keys = set(_namespace_rebind_names(node, consts, namespaces, launderers))
            #: `sys.modules` itself, or a NAME ALIAS of it (`_sm = sys.modules`) — the same
            #: union the `AugAssign` arm above already accepts (#14326 round 21).
            def _is_registry_receiver(expr: ast.AST | None) -> bool:
                return _is_module_registry(expr, ctx) or bool(
                    isinstance(expr, ast.Name) and expr.id in registry_aliases)

            if (
                isinstance(node, ast.Call)
                and _callee_names_of(node.func) & _REGISTRY_MUTATORS
                and _is_registry_receiver(getattr(node.func, "value", None))
            ):
                # The DIRECT registry receiver — `_is_namespace_expr` deliberately does not
                # call `sys.modules` a namespace (a `sys.modules["print"]` key binds no
                # builtin), so the mapping-mutation spelling is resolved HERE, scoped to the
                # registry, rather than by widening the shared predicate. The receiver is
                # resolved through `registry_aliases` too: `_m = sys.modules;
                # _m.__ior__({…})` was GREEN while `_m |= {…}` was RED (#14326 round 21).
                keys |= _mapping_mutation_keys(node, consts, offset=0)
            elif (
                isinstance(node, ast.Call)
                and _callee_names_of(node.func) & _REGISTRY_MUTATORS
                and node.args
                and _is_registry_receiver(node.args[0])
            ):
                # ⛔ THE UNBOUND SPELLING PUTS THE REGISTRY IN arg0 (#14326 round 21,
                # MEASURED GREEN: `dict.__setitem__(sys.modules, "tortoise.sdk", _Mirror)`,
                # `dict.update(sys.modules, {…})` and `dict.setdefault(sys.modules, …)` all
                # inserted the entry with the pin green). This is the asymmetry the file
                # ALREADY pins on the namespace axis ("UNBOUND `dict.__setitem__(<ns>, …)`"),
                # one predicate over — `offset=1` is the whole of it.
                keys |= _mapping_mutation_keys(node, consts, offset=1)
            if (
                isinstance(node, ast.Subscript)
                and isinstance(node.ctx, ast.Store)
                and _is_module_registry(node.value, ctx)
            ):
                # ⛔ THE REGISTRY IS THE OTHER SIDE OF THE SAME STORE (#14326 review P1,
                # MEASURED GREEN): `run()`'s deferred `from tortoise.sdk import …` resolves
                # THROUGH `sys.modules`, so replacing that entry replaces the class just as
                # an attribute write does.
                # ⛔ NOT name-alone (#14326 review, measured: a name-alone union left 31
                # passed when dropped, because the namespace path already resolved every
                # row — and it would FALSE RED `_CACHE["TortoiseSDK"]`, a dict store that
                # binds no class. `__builtins__["TortoiseSDK"]` is not a lever either:
                # `run()`'s own `from … import` binds a LOCAL that shadows a builtin).
                keys |= _string_keys_of(node.slice, consts)
            # ⛔ AN ANCESTOR OF THE DEFERRED TARGET IS THE TARGET (#14326 round 31 P2,
            # MEASURED GREEN end-to-end): `sys.modules["tortoise"] = _pkg` followed by
            # `del sys.modules["tortoise.sdk"]` re-executed `tortoise/sdk.py` from the planted
            # package's `__path__` — with no `sys.path` touch at all — and the pin stayed
            # GREEN, as it did for the `|=` and `__setitem__` spellings. The rule the DELETE
            # axis already applies (only an ancestor forces the re-execution, because the
            # dotted import walks each prefix) is one axis over from here, and this arm was
            # still testing EXACT keys only — the in-file asymmetry this lane keeps removing.
            hits = {k for k in keys
                    if k in {_SDK_CONSTRUCTOR, _SHIPPED_MODULE}
                    or _SHIPPED_MODULE.startswith(k + ".")}
            if not hits:
                continue
            if isinstance(node, ast.Call) and _is_setattr_family(node.func, launderers):
                # `setattr`'s position for the key is the SPELLING's, not a constant index.
                out.append(
                    f"line {node.lineno} `setattr`s "
                    f"`{'` / `'.join(sorted(hits))}` — the same store as an attribute "
                    "write, carried as a string constant, so `run()`'s deferred import "
                    "can resolve to a different class"
                )
            else:
                # The RECEIVER, not the mutator: for a call the store's target is
                # `func.value` (`_t.__dict__`), for a subscript it is `value`.
                receiver = (
                    node.value
                    if isinstance(node, ast.Subscript)
                    else getattr(node.func, "value", node.func)
                )
                out.append(
                    f"line {node.lineno} stores "
                    f"`{'` / `'.join(sorted(hits))}` under a string key on "
                    f"`{ast.unparse(receiver)}` — the same rebind as an attribute store")
    # ⛔ THE IMPORT-PATH AXIS (#14326 round 29 P2, MEASURED GREEN). Every other arm pins what
    # is written INTO `sys.modules`; this one pins WHERE the deferred import would read a
    # re-created entry from. `del` alone is inert, and `sys.path` alone is inert, but together
    # they install a mirror — and the axis is not hypothetical, the artifact's own
    # `tools/read_wave_latency.py` already does `sys.path.insert(0, _REPO_ROOT)`.
    #
    # The clause is deliberately coarse — "the search path is mutated AND the registry loses an
    # ANCESTOR of the deferred target" — and the fine-grained alternative IS to enumerate finders
    # and path shapes. ⛔ THIS COMMENT USED TO SAY THAT CLASS WAS "the unbounded class this file
    # already declares as residual", WHICH WAS FALSE and is retracted at its source (#14326 round
    # 32 P3; `grep` finds no such declaration). The residual that DOES exist is now named in
    # `_is_import_path_mutation`'s docstring and pinned GREEN by
    # `test_the_IMPORT_PATH_axis_residuals_are_NAMED_and_pinned_GREEN`. ⛔ AND "emptied of a
    # `tortoise*` key" WAS ONE RULE STALE (#14326 round 30 fixed the rule, round 31 claimed to
    # fix this sentence and did not): the code grades ANCESTORS only (`tortoise`,
    # `tortoise.sdk`), because dropping a `tortoise._ab_…` submodule re-imports nothing and
    # redding it was a FALSE RED on the shipped `tools/read_wave_latency.py`. Coarse here can
    # only over-RED code that simultaneously rewrites the import path and drops the package
    # from the cache while the pin is watching, which is the premise itself.
    path_names = frozenset(_aliases_of(tree, lambda _n: _is_import_path_object(_n, ctx),
                                       loops=True))
    _path_mutations = [
        node for node in ast.walk(tree)
        if _is_import_path_mutation(node, ctx, path_names)]
    _dels = [
        node for node in ast.walk(tree)
        if _empties_the_registry(node, ctx, registry_aliases)]
    if _path_mutations and _dels:
        out.append(
            f"line {min(n.lineno for n in _path_mutations)} mutates the import search path "
            f"(`{ast.unparse(_path_mutations[0])[:80]}`) AND line "
            f"{min(n.lineno for n in _dels)} drops a registry entry — the deferred "
            "`from tortoise.sdk import …` then RE-IMPORTS from wherever the mutated path "
            "points, so a planted module becomes the constructor with the pin GREEN "
            "(measured: a fake `tortoise/sdk.py` resolved instead of the shipped class)")
    if not bound_from_shipped:
        out.append(
            f"no `from tortoise.sdk import {_SDK_CONSTRUCTOR}` binding exists in this file — "
            "the receiver clause then proves nothing about WHICH class `run()` constructs")
    return out


def _findings_of_run(source: str, predicate) -> list[str]:
    """`predicate(run_def)` for the single `run` defined by `source` — the row harness."""
    run_def = next(n for n in ast.walk(ast.parse(source))
                   if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "run")
    return predicate(run_def)


def test_the_ASYNC_for_arms_that_CARRY_the_class_are_pinned_and_the_redundant_ones_are_NAMED() -> None:
    """The two `ast.AsyncFor` members THIS ROW pins, and the nine it does not.

    ⛔ MEASURED AT `57735ab47947` (this file, 41 rows). `ast.AsyncFor` appears in TEN code
    arms of this harness — counted by walking the AST for every `Attribute(attr="AsyncFor")`
    whose enclosing function is a harness arm, never by grepping prose. Dropping it from each
    arm in turn and running the whole file:

        `_str_constants` bind tuple            1 failed   pinned, round 17
        `_rebound_callee_names`                41 passed  redundant (re-measured below)
        `_in_loop`                             1 failed   <- pinned HERE
        `_extra_bindings` target collection    1 failed   <- pinned HERE
        `_aliases_of` loop arm                 1 failed   pinned, `45a7d205`
        holders fixpoint (in `_mutated_after`) 41 passed  redundant (re-measured below)
        `_per_query_element_mutations`: names  41 passed  redundant (re-measured below)
        `_per_query_element_mutations`: gate   41 passed  redundant (re-measured below)
        `_per_query_element_mutations`: deriv. 41 passed  redundant (re-measured below)
        `_rebound_after`                       41 passed  redundant (re-measured below)

    ⛔ RE-CENSUSED AT `77a7f04ee44f` (#14326 round 27). This is the third correction of this
    table, and it found a LIVE fail-open rather than another stale sentence: the count is
    ELEVEN, because `_constructor_provenance_findings` gained a loop-target arm in round 25
    (`7508592c24`, the commit that made unpack and `for` targets store to the registry) — ONE
    arm, landed AFTER the `4473c96648ca` re-census recorded below, which is why the count
    DRIFTED rather than being mis-taken. Its verdict is NOT redundant:

        `_constructor_provenance_findings`
               loop-target arm               1 failed   <- pinned HERE (round 27)

    Measured: deleting `ast.AsyncFor` from that arm left this file at **41 passed** while
    `async for sys.modules in ({"tortoise.sdk": _Mirror},):` stayed GREEN — the sync `for`
    spelling was RED, so the defect was exactly one node type over, i.e. this row's own class
    reproduced inside the one arm the census had missed. It is pinned now by the `async for`
    row in `registry_replacements` below, and that row is the only NEW row the re-census added;
    a second `async for` spelling was measured, found to red on the SAME single mutant as the
    first, and therefore not added — a row that changes no verdict is this file's other
    deletion rule.

    ⛔ RE-CENSUSED AT `4473c96648ca`, the peer commit that lands ON TOP of this row: still TEN,
    the same ten arms, every verdict below unchanged. ⛔ AND THE REASON IS STATED FROM THE DIFF, NOT
    ASSUMED (#14326 round 22, MEASURED: this paragraph used to say that commit "rebuilt
    `_rebound_callee_names` (+56 lines …)" — it did NOT. That function is byte-identical across
    `57735ab47947` → `4473c96648ca` → `ff85c6ab73af` (97 lines, sha1 `9d658a74eb0d`), and that
    commit's numstat is `113/3` with no hunk mentioning it; a reader following the old sentence
    inspected an unmodified function). What that commit actually rebuilt is `_is_module_registry`
    plus the new `_is_sys_ref`, `_aliases_of`'s `ImportFrom` arm, the `_REGISTRY_MUTATORS`
    vocabulary, and `_str_constants`' `With`/`AsyncWith` bind arm — none of them in this row's
    census, which is why every one of the ten verdicts below was re-run rather than assumed and
    all ten are unchanged. Only the line numbers moved (again), which is why the arms are named
    by symbol and none is cited by `:NNNN`.

    ⛔ THIS HEADER SAID "EIGHT code arms" AND "the four that are not" AND WAS FALSE (#14326
    round 19 P2). The census behind it missed `_rebound_callee_names` and `_rebound_after`, so
    the sole completeness claim in the one row whose purpose is "so the next round does not
    mistake them for pinned clauses" was short by two arms — the stale/false-claim class this
    file deletes clauses for, and a map with two holes in it is that class. Both omissions are
    REDUNDANT (each measured `41 passed` at this head, so no fail-open hides behind them):
    what was wrong was the COUNT, not the guard. The nine non-THIS-row arms are 2 pinned by
    sibling rows + 6 redundant + 1 pinned by the round-27 row above = 9, and 2 + 9 = the
    eleven the census now names.

    ⛔ THE ARMS ARE NAMED, NOT NUMBERED, ON PURPOSE. A `:NNNN` in this docstring is the drift
    class this file keeps being cured of: the numbers in the first version of this table were
    `45a7d205`'s, and a peer commit (`9b48318e`, round 18) moved four of them before this row
    was even pushed. Read the arm by the function it lives in; re-grep the arm before quoting
    a line.

    The two this row pins are not tidiness. `_in_loop` decides whether a read sits inside
    the measurement loop, and `_extra_bindings` finds a SECOND bind of the blessed id
    mapper — the fail-open #14281 review P1 closed. With the `AsyncFor` member gone, an
    `async for` body counts as "not in a loop" and an `async for _result_id in …` shadow is
    invisible, while every gate stays green: an arm whose deletion leaves the file fully
    green is a clause no mutant can red, which is the defect class this file deletes clauses
    for.

    ⛔ THE OTHER SIX ARE REDUNDANCY, NOT COVERAGE, AND THAT IS MEASURED — not assumed. For
    the `async` element-mutation lever
    (`async for _pq in per_query: _pq["order"].sort()`), deleting `ast.AsyncFor` from ANY of
    the four arms in `_mutated_after`'s holders fixpoint or
    `_per_query_element_mutations` leaves BOTH `_per_query_element_mutations` and
    `_mutated_after` returning the same hit list (`[4]`) — the sibling arms still catch it.
    Re-measured at `57735ab47947`: those four deletions AND the two the old census missed —
    `_rebound_callee_names` and `_rebound_after` — each leave `41 passed`. So none of the six
    is individually reddenable, and pinning one would mean asserting something already
    asserted. They stay (deleting a fail-closed arm narrows the guard for no gain) and are
    NAMED here so the next round does not re-derive the same measurement or mistake them for
    pinned clauses.
    """
    # ── `_in_loop`: an `async for` body IS inside a loop ──────────────────────────────
    async_run = (
        "async def run(sdk, queries):\n"
        "    async for _p in queries:\n"
        "        sdk._tortoise_fts_query(query=_p['query'], limit=10)\n"
        "    sdk._tortoise_fts_query(query='after', limit=10)\n")
    tree = ast.parse(async_run)
    parents = _parents_of(tree)
    calls = sorted((n for n in ast.walk(tree) if isinstance(n, ast.Call)),
                   key=lambda n: n.lineno)
    assert len(calls) == 2, f"the lever should carry two reads, found {len(calls)}"
    assert _in_loop(calls[0], parents), (
        "a read inside an `async for` body is not reported as in-loop — `ast.AsyncFor` has "
        "been dropped from `_in_loop`'s arm, so the pin would look for the measurement read "
        "outside the loop it actually runs in, and every async shape would be graded against "
        "the wrong census")
    # …and the CONTROL, in the other direction: the arm must not answer `True` for a read
    # that follows the loop. Without this row "always True" reds nothing either.
    assert not _in_loop(calls[1], parents), (
        "`_in_loop` reports a read AFTER the loop as inside it — the arm has widened from "
        "`For`/`AsyncFor` to any statement in the function")

    # ── `_extra_bindings`: an `async for` target is a SECOND BIND too ─────────────────
    shadow_async = ast.parse(
        "async def run():\n"
        "    async for _result_id in range(3):\n"
        "        print(_result_id)\n")
    assert _extra_bindings(shadow_async, _ID_MAPPERS), (
        "an `async for _result_id in …` target is not reported as a second bind of the "
        "blessed id mapper — `ast.AsyncFor` has been dropped from `_extra_bindings`' target "
        "arm, and that shadow records a constant page while the `order` element check stays "
        "satisfied (#14281 review P1's exact fail-open, one node type over)")
    # The plain spelling is the SAME arm, and it is asserted here so the two cannot drift
    # apart: a row that pins only the async half leaves the sync half un-reddenable.
    assert _extra_bindings(
        ast.parse("def run():\n    for _result_id in range(3):\n        print(_result_id)\n"),
        _ID_MAPPERS), (
        "a `for _result_id in …` target is no longer reported as a second bind — the sync "
        "and async halves of this arm are one clause")
    # …and the CONTROL: the ONE module-level `def` (either kind) is not a second bind, so
    # the arm cannot be satisfied by flagging every function that returns a constant.
    for kind in ("def", "async def"):
        assert not _extra_bindings(
            ast.parse(f"{kind} _result_id(r):\n    return r['id']\n"), _ID_MAPPERS), (
            f"a module-level `{kind} _result_id` alone is reported as a second bind — the "
            "arm must flag extra bindINGS, not the mapper's own definition")


def test_every_argument_clause_reds() -> None:
    """Each `_call_argument_findings` clause must be RED by its own row (#14326 review P1).

    Measured before this row existed: `assert not call.args`, `splat` and `assert not extra`
    each replaced by `True` in turn left **27 passed**. The `extra` clause is the one the
    review proved composes with a real lever — `kind="statement"` on the measured read reds
    the pin ONLY through it — so a silent deletion of both the clause and a later lever
    would read as green forever.
    """
    allowed = _door_read_contract()
    calls = lambda body: [n for n in ast.walk(ast.parse(body)) if isinstance(n, ast.Call)]  # noqa: E731

    faithful = ('sdk._tortoise_fts_query(query=q, limit=10, read_status_out=st, '
                '_truncation_out=tr, leg_trace=tr2)')
    assert _call_argument_findings(calls(faithful), allowed) == []

    positional = 'sdk._tortoise_fts_query(q, limit=10)'
    assert any("POSITIONAL" in f
               for f in _call_argument_findings(calls(positional), allowed)), positional

    splat = 'sdk._tortoise_fts_query(**opts)'
    assert any("splats a mapping" in f
               for f in _call_argument_findings(calls(splat), allowed)), splat

    lever = 'sdk._tortoise_fts_query(query=q, limit=10, kind="statement")'
    assert any("kind" in f
               for f in _call_argument_findings(calls(lever), allowed)), lever


def test_the_read_is_pinned_to_the_SDK_OBJECT_not_only_its_name() -> None:
    """The receiver clause must be RED by its own row, and GREEN on the faithful shape.

    The row this closes (#14326 review P1) is the grant's own class: a harness that times
    `sdk._fast._tortoise_fts_query` — or wraps the SDK before reading — is not a mirror of
    the shipped `/v1/search` door, and the name-based reach checks could not tell.
    """
    faithful = (
        "def run(queries):\n"
        "    sdk = TortoiseSDK()\n"
        "    sdk._tortoise_fts_query(query=queries[0]['query'], limit=10)\n"
        "    results = sdk._tortoise_fts_query(query=queries[0]['query'], limit=10, "
        "leg_trace=None)\n"
        "    return results\n")
    assert _findings_of_run(faithful, _receiver_findings) == []

    mirror_door = faithful.replace("results = sdk._tortoise_fts_query(",
                                   "results = sdk._fast._tortoise_fts_query(", 1)
    got = _findings_of_run(mirror_door, _receiver_findings)
    assert got and "receiver must be the one bare name" in got[0], got

    wrapper = faithful.replace("    sdk = TortoiseSDK()\n",
                               "    sdk = TortoiseSDK()\n    sdk = _FastMirror(sdk)\n", 1)
    got = _findings_of_run(wrapper, _receiver_findings)
    assert got and "bound 2 time(s)" in got[0], got

    # ⛔ TWO receivers, EACH bound once to `TortoiseSDK()` (#14326 review P1, measured at
    # `4ed436353f`): deleting the `len(receivers) > 1` clause left **31 passed**, because
    # every other row reaches the read through exactly one name and so reds a different
    # clause first. Two SDK objects are indistinguishable from a mirror by shape alone —
    # the pin proves the measured read is off THE object `run()` constructs, and it can
    # only prove that while there is one.
    two_receivers = faithful.replace(
        "    results = sdk._tortoise_fts_query(",
        "    other = TortoiseSDK()\n    results = other._tortoise_fts_query(", 1)
    got = _findings_of_run(two_receivers, _receiver_findings)
    assert got and "more than one receiver" in got[0], got

    foreign = faithful.replace("    sdk = TortoiseSDK()\n", "    sdk = get_sdk()\n", 1)
    got = _findings_of_run(foreign, _receiver_findings)
    assert got and "not `TortoiseSDK()`" in got[0], got

    parameter = faithful.replace("def run(queries):", "def run(sdk, queries):", 1)
    got = _findings_of_run(parameter, _receiver_findings)
    assert got and "as a PARAMETER" in got[0], got

    # ⛔ THE CLASS, NOT ONLY THE NAME (#14326 review P1, both MEASURED GREEN at `91db903`
    # with `_receiver_findings` alone): the receiver clause is satisfied by ANY name spelled
    # `TortoiseSDK`, so the name's PROVENANCE is pinned too. Each row below is the lever the
    # review actually applied to `tools/read_wave_latency.py`, measured end-to-end:
    #
    #   `from _compat.sdk import TortoiseSDK`                 -> 31 passed (foreign ctor)
    #   `from tortoise.sdk import TortoiseSDK as _R` +        -> 31 passed (a subclass whose
    #   `class TortoiseSDK(_R): __getattr__ = … sorted(…)`         every page is re-sorted)
    def ctor_findings(source: str) -> list[str]:
        return _constructor_provenance_findings(ast.parse(source))

    shipped = ("from tortoise.sdk import TortoiseSDK\n"
               "def run(queries):\n"
               "    sdk = TortoiseSDK()\n")
    assert ctor_findings(shipped) == []

    foreign_ctor = shipped.replace("from tortoise.sdk import TortoiseSDK",
                                   "from _compat.sdk import TortoiseSDK", 1)
    got = ctor_findings(foreign_ctor)
    assert got and "from `_compat.sdk`" in got[0], got

    subclass = ("from tortoise.sdk import TortoiseSDK as _R\n"
                "class TortoiseSDK(_R):\n"
                "    __getattr__ = staticmethod(lambda *_a, **_k: None)\n"
                "def run(queries):\n"
                "    sdk = TortoiseSDK()\n")
    got = ctor_findings(subclass)
    assert got and any("DEFINES" in g or "as `_R`" in g for g in got), got

    assigned = shipped + "TortoiseSDK = _Compat\n"
    got = ctor_findings(assigned)
    assert got and any("ASSIGNS" in g for g in got), got

    # ⛔ THE THREE STORES THAT REBIND THE CLASS `run()` RESOLVES AT CALL TIME (#14326
    # review P1, the first one MEASURED GREEN at `a77e4fe3` with the pin untouched):
    # `run()` imports the class INSIDE the body, so a store performed before the call is
    # what the import resolves.
    module_attr = ("import tortoise.sdk as _t\n"
                   "class _Mirror:\n    pass\n"
                   "_t.TortoiseSDK = _Mirror\n")
    got = ctor_findings(module_attr)
    assert got and any("STORES" in g for g in got), got

    setattr_store = (shipped
                     + 'setattr(_t, "TortoiseSDK", _Mirror)\n')
    got = ctor_findings(setattr_store)
    assert got and any("setattr`s" in g for g in got), got

    keyed_ctor = (shipped + '_t.__dict__["TortoiseSDK"] = _Mirror\n')
    got = ctor_findings(keyed_ctor)
    assert got and any("under a string key" in g for g in got), got

    # ⛔ THE STORE WAS MATCHED AS A SPELLING, AND FIVE MORE SPELLINGS WALKED PAST (#14326
    # review P1). EACH of the shapes below was MEASURED GREEN (`31 passed`, the real tool
    # mutated at module level, restored byte-identically) at `b6be337`, whose three arms
    # matched a literal `ast.Constant` key, a bare `setattr` name and an `ast.Name` receiver:
    #
    #   _t.__dict__.update(TortoiseSDK=_Mirror)             -> 31 passed
    #   vars(_t).update(TortoiseSDK=_Mirror)                -> 31 passed
    #   _t.__dict__.__setitem__("TortoiseSDK", _Mirror)      -> 31 passed
    #   setattr(tortoise.sdk, "TortoiseSDK", _Mirror)        -> 31 passed  (dotted receiver)
    #   setattr(_t, _k, _Mirror)                            -> 31 passed  (laundered key)
    #   setattr(*[_t, "TortoiseSDK", _Mirror])               -> 31 passed  (splat arity)
    #   functools.partial(setattr, _t, "TortoiseSDK")(_M)   -> 31 passed  (pre-bound key)
    #
    # …and the `sys.modules` swap below was not merely uncaught, it was BLESSED: the control
    # row that stood here asserted `sys.modules["tortoise.sdk"] = _M` was GREEN ("an
    # unrelated KEY is not this store"). It is the same store one level up — `run()`'s
    # deferred `from tortoise.sdk import …` resolves THROUGH that entry — so the row is
    # re-pointed at a key that genuinely cannot reach `run()`.
    unresolved = "tortoise._compat"  # a module swap that cannot touch `tortoise.sdk`
    assert ctor_findings(shipped + f"sys.modules[{unresolved!r}] = _M\n") == []

    module_swap = shipped + 'sys.modules["tortoise.sdk"] = _Mirror\n'
    got = ctor_findings(module_swap)
    assert got and any("under a string key" in g for g in got), got

    # The registry reached through a one-bind alias, and a registry MAPPING-MUTATOR store.
    aliased_registry = shipped + '_REG = sys.modules\n_REG["tortoise.sdk"] = _Mirror\n'
    got = ctor_findings(aliased_registry)
    assert got and any("under a string key" in g for g in got), got

    # Every one of the store spellings above, each its OWN row — a spelling the previous
    # round matched literally, so the deletion of any single resolution arm below reds.
    store_spellings = {
        "module-attribute update (keywords)":
            '_t.__dict__.update(TortoiseSDK=_Mirror)\n',
        "module-attribute update (mapping)":
            '_t.__dict__.update({"TortoiseSDK": _Mirror})\n',
        "`vars()` mapping mutator":
            'vars(_t).update(TortoiseSDK=_Mirror)\n',
        "`__setitem__` spelling":
            '_t.__dict__.__setitem__("TortoiseSDK", _Mirror)\n',
        "KEY carried by a NAME":
            '_k = "TortoiseSDK"\n_t.__dict__[_k] = _Mirror\n',
        "KEY is a literal-only `+`":
            '_t.__dict__["Tortoise" + "SDK"] = _Mirror\n',
        "setattr KEY carried by a NAME":
            '_k = "TortoiseSDK"\nsetattr(_t, _k, _Mirror)\n',
        "SPLATTED setattr argument list":
            'setattr(*[_t, "TortoiseSDK", _Mirror])\n',
        "setattr through `functools.partial` (pre-bound key)":
            'import functools\nfunctools.partial(setattr, _t, "TortoiseSDK")(_Mirror)\n',
        "ALIASED `setattr` (a name bound to the launderer)":
            '_sa = setattr\n_sa(_t, "TortoiseSDK", _Mirror)\n',
        "ALIASED namespace RECEIVER":
            '_ns = _t.__dict__\n_ns.update(TortoiseSDK=_Mirror)\n',
        "registry MAPPING-MUTATOR store":
            'sys.modules.update({"tortoise.sdk": _Mirror})\n',
        # ⛔ THE MODULE-DOTTED AND INDEXED RECEIVERS OF THE SAME `setattr` (independent
        # round, MEASURED GREEN at `b6be3373`): requiring the receiver to be a bare `Name`
        # exempted `setattr(tortoise.sdk, "TortoiseSDK", _Mirror)` — one line, the same
        # rebind. There is no receiver test now, which is also why the artifact's own
        # `setattr(os, …)` calls stay green (their key is a VARIABLE, not this name).
        "setattr on a MODULE-DOTTED receiver":
            'import tortoise.sdk\nsetattr(tortoise.sdk, "TortoiseSDK", _Mirror)\n',
        "setattr on the sys.modules ENTRY":
            'setattr(sys.modules["tortoise.sdk"], "TortoiseSDK", _Mirror)\n',
        "`.update(**{…})` splat spelling":
            '_t.__dict__.update(**{"TortoiseSDK": _Mirror})\n',
    }
    for name, lever in store_spellings.items():
        got = ctor_findings(shipped + "class _Mirror:\n    pass\n" + lever)
        assert got, f"the store `{name}` was not refused"

    # ⛔ THE FALSE-RED CONTROL FOR THE MAPPING ARM (#14326 review P2, measured): a mapping
    # that merely HOLDS the name stores nothing, so the `.update` arm must be scoped to a
    # namespace RECEIVER — `_LABELS` is a plain dict, not a module's `__dict__`.
    labels = (shipped + "_LABELS = {}\n"
                       '_LABELS.update({"TortoiseSDK": "tortoise.sdk"})\n')
    assert ctor_findings(labels) == []               # a label table stores nothing

    # ⛔ `|=` ON A NAMESPACE ALIAS (#14326 review P1, MEASURED GREEN end-to-end at
    # `346c11d6`: the deferred import resolved the mirror and the pin stayed at 35 passed).
    # Both halves were already resolved by the machinery above — only the STATEMENT KIND was
    # missing, because `AugAssign` carries `.target` singular. Same class as the `.update`
    # arm's missing kwarg spelling: one statement/method over, and the store still lands.
    # ⛔ EVERY DISJUNCT AND BOTH VALUE SHAPES GETS ITS OWN ROW (#14326 review P1, MEASURED:
    # deleting the `__dict__`-Attribute disjunct left **35 passed**, because both fail rows
    # above reach the arm through the NAME-ALIAS disjunct only). And the registry is a
    # DIFFERENT KEY VOCABULARY — it is keyed by the MODULE name, never by the class name, so
    # `sys.modules |= {"tortoise.sdk": _Mirror}` was GREEN at `474d19f2` while the
    # constructor-keyed spelling of the same store was red.
    aug_levers = {
        "namespace name-alias, dict display":
            '_d = _t.__dict__\n_d |= {"TortoiseSDK": _Mirror}\n',
        "namespace name-alias, `dict(...)` value":
            '_d = _t.__dict__\n_d |= dict(TortoiseSDK=_Mirror)\n',
        "namespace ATTRIBUTE (the disjunct no row reached)":
            '_t.__dict__ |= {"TortoiseSDK": _Mirror}\n',
        "import registry, MODULE key":
            'sys.modules |= {"tortoise.sdk": _Mirror}\n',
        "import registry, MODULE key, via a name alias":
            '_sm = sys.modules\n_sm |= {"tortoise.sdk": _Mirror}\n',
        # ⛔ A `for` TARGET OVER A CONTAINING LITERAL (#14326 round 17, MEASURED GREEN at
        # `689708686ba6` for all three, while the direct store and the PRE-BOUND alias
        # (`_R = sys.modules; for _x in _R:`) both red). The loop target binds an ELEMENT,
        # and binding it to the ITERABLE instead handed the alias axis the tuple, so the
        # registry predicate never fired. The env axis was never affected because it passes
        # the wider `_mentions_mapping` predicate, which matches a container HOLDING the
        # mapping — an in-file asymmetry, not a boundary. One row per container SHAPE and
        # per KEY VOCABULARY, because the receiver and the key are separate disjuncts.
        "import registry via a `for` target over a TUPLE literal":
            'for _x in (sys.modules,):\n    _x |= {"tortoise.sdk": _Mirror}\n',
        "import registry via a `for` target over a LIST literal":
            'for _x in [sys.modules]:\n    _x |= {"tortoise.sdk": _Mirror}\n',
        "namespace via a `for` target over a TUPLE literal":
            'for _x in (_t.__dict__,):\n    _x |= {"TortoiseSDK": _Mirror}\n',
        # …and the SET and DICT iterables are their OWN rows: `_loop_target_sources`
        # carries an arm per container type (a `Dict` iteration yields its KEYS, `None`
        # being a `**spread`), and a clause no row can red is the defect this file deletes
        # on sight.
        "import registry via a `for` target over a SET literal":
            'for _x in {sys.modules}:\n    _x |= {"tortoise.sdk": _Mirror}\n',
        "import registry via a `for` target over a DICT literal (keys)":
            'for _x in {sys.modules: 1}:\n    _x |= {"tortoise.sdk": _Mirror}\n',
        # ⛔ `async for` IS THE SAME ARM ONE NODE TYPE OVER, AND IT WAS AN UNPINNED CLAUSE
        # (#14326 round 17, MEASURED: deleting `ast.AsyncFor` from the arm left the whole
        # file at **1 passed** for this test — i.e. no row red — which is the defect this
        # table deletes clauses for). It cannot ride the rows above: an `async for` at
        # module level is a SyntaxError, so the lever supplies its own `async def`. A
        # SIBLING lane pinned the same member in `_str_constants`' bind tuple in the same
        # round; the two arms are independent, so each needs its own row.
        "import registry via an `async for` target over a TUPLE literal":
            'async def _a():\n    async for _x in (sys.modules,):\n'
            '        _x |= {"tortoise.sdk": _Mirror}\n',
        # ⛔ A SPLICE INSIDE THE LITERAL IS THE SAME ELEMENT ONE `*`/`**` OVER (#14326 round
        # 18, MEASURED GREEN at `7ba358467252` while the plain tuple/spread rows above were
        # RED). `_loop_target_sources` returned the `Starred` NODE as the value the target
        # binds, and DROPPED a `**spread` key entirely (`keys` carries `None` there) — so the
        # two shapes round 17 closed stayed open one operator over, and the docstring's "no
        # key at all" read as coverage. One row per splice kind. The `async for` row above is
        # the SIBLING lane's (round 18, `45a7d205`); these two are disjoint from it.
        "import registry via a `for` target over a STARRED tuple literal":
            'for _x in (*(sys.modules,),):\n    _x |= {"tortoise.sdk": _Mirror}\n',
        "import registry via a `for` target over a `**` dict spread":
            'for _x in {**{sys.modules: 1}}:\n    _x |= {"tortoise.sdk": _Mirror}\n',
        # ⛔ …and through a MODULE ALIAS (#14326 round 22, MEASURED RED→GREEN REGRESSION: the
        # import-alias-only `_sys_names_of` made this GREEN, and it was RED before).
        "import registry via a `for` target over a `_s = sys` alias":
            '_s = sys\nfor _x in (_s.modules,):\n    _x |= {"tortoise.sdk": _Mirror}\n',
        # ⛔ THREE BIND FORMS WITH NO `Assign` NODE (#14326 round 19, MEASURED GREEN at
        # `9b48318eedc3`). `_str_constants` already resolved all three; `_aliases_of` did not,
        # so the `AugAssign` store arm received a receiver that never resolved and the exact
        # store these rows carry reached the deferred import while the pin passed. One row
        # per form, because each is a separate arm and a clause no row can red is the defect
        # this table deletes clauses for.
        "import registry via a `match` CAPTURE":
            'match sys.modules:\n    case _x:\n        _x |= {"tortoise.sdk": _Mirror}\n',
        "import registry via a parameter DEFAULT":
            'def _f(_registry=sys.modules):\n'
            '    _registry |= {"tortoise.sdk": _Mirror}\n',
        "import registry via a `with` TARGET":
            'with sys.modules as _x:\n    _x |= {"tortoise.sdk": _Mirror}\n',
    }
    for name, lever in aug_levers.items():
        got = ctor_findings(shipped + "class _Mirror:\n    pass\n" + lever)
        assert got and any("AUGMENTS" in g for g in got), (name, lever, got)

    # ⛔ THE REGISTRY'S OWN MUTATOR AND RECEIVER SPELLINGS (#14326 round 20, MEASURED GREEN at
    # `57735ab47` for every row below while the plain subscript store above was RED). Two
    # asymptries, one root: the store was matched as a SPELLING — a `{update, setdefault}`
    # mutator vocabulary and an `x.modules` ATTRIBUTE receiver — so the same object reached by
    # a dunder mutator, an import alias, a `getattr` or a `__dict__` index walked past. Each
    # spelling below is verified `is sys.modules` at runtime and writes the identical entry.
    registry_spellings = {
        "`__setitem__` on the registry":
            'sys.modules.__setitem__("tortoise.sdk", _Mirror)\n',
        "`__ior__` on the registry":
            'sys.modules.__ior__({"tortoise.sdk": _Mirror})\n',
        "`from sys import modules` alias":
            'from sys import modules as _m\n_m["tortoise.sdk"] = _Mirror\n',
        "`getattr(sys, \"modules\")`":
            'getattr(sys, "modules")["tortoise.sdk"] = _Mirror\n',
        "`sys.__dict__[\"modules\"]`":
            'sys.__dict__["modules"]["tortoise.sdk"] = _Mirror\n',
        "`vars(sys)[\"modules\"]`":
            'vars(sys)["modules"]["tortoise.sdk"] = _Mirror\n',
        # ⛔ THE ALIAS HALF OF EVERY FORM ABOVE (#14326 round 21, MEASURED GREEN at
        # `ff85c6ab73`: the resolved forms followed the LITERAL `sys` spelling only, so the
        # same three shapes through `import sys as _s` walked past while `_s.modules[…]` was
        # RED). One row per form; the set they share is `_sys_names_of`'s.
        "`getattr` on an IMPORT ALIAS":
            'import sys as _s\ngetattr(_s, "modules")["tortoise.sdk"] = _Mirror\n',
        "`vars` on an IMPORT ALIAS":
            'import sys as _s\nvars(_s)["modules"]["tortoise.sdk"] = _Mirror\n',
        "`__dict__` on an IMPORT ALIAS":
            'import sys as _s\n_s.__dict__["modules"]["tortoise.sdk"] = _Mirror\n',
        # ⛔ `_s = sys` AND `os.sys.modules` (#14326 round 22, MEASURED RED→GREEN REGRESSION):
        # the first version of `_sys_names_of` collected IMPORT aliases only, and narrowing the
        # ATTRIBUTE arm to that set turned these two from RED into GREEN — a fail-open bought
        # with a false-red fix, the wrong direction. The rows below pin the recovery, and the
        # `_s = sys` one is the alias a later refactor would most plausibly re-drop.
        "`_s = sys` MODULE ALIAS":
            '_s = sys\n_s.modules["tortoise.sdk"] = _Mirror\n',
        "`os.sys.modules` ATTRIBUTE":
            'os.sys.modules["tortoise.sdk"] = _Mirror\n',
        # ⛔ THE BIND FORMS `_aliases_of` GIVES FOR FREE, and the reason `_sys_names_of` now
        # calls it instead of walking assignments itself (#14326 round 23, MEASURED: a bespoke
        # walk accepting only a BARE `Name` target left these RED→GREEN). Two rows per bind
        # SHAPE class — one `AnnAssign`, one walrus — plus the unpack row below, because the
        # previously-hand-written disjuncts were the two clauses no mutant could red.
        "`_s: str = sys` ANNASSIGN alias":
            '_s: object = sys\n_s.modules["tortoise.sdk"] = _Mirror\n',
        "`(_s := sys)` WALRUS alias":
            '(_s := sys)\n_s.modules["tortoise.sdk"] = _Mirror\n',
        "`_s, _t = sys, None` UNPACKED alias":
            '_s, _t = sys, None\n_s.modules["tortoise.sdk"] = _Mirror\n',
        # ⛔ AND THE UNBOUND MUTATOR SPELLINGS (#14326 round 21, MEASURED GREEN: all three
        # INSERTED into `sys.modules` with the pin green). This is the asymmetry the file
        # already pins on the NAMESPACE axis — `dict.__setitem__(<ns>, …)` has its own row
        # there — so the registry axis owed the same three.
        "UNBOUND `dict.__setitem__(sys.modules, …)`":
            'dict.__setitem__(sys.modules, "tortoise.sdk", _Mirror)\n',
        "UNBOUND `dict.update(sys.modules, …)`":
            'dict.update(sys.modules, {"tortoise.sdk": _Mirror})\n',
        "UNBOUND `dict.setdefault(sys.modules, …)`":
            'dict.setdefault(sys.modules, "tortoise.sdk", _Mirror)\n',
        # …and the ALIAS RECEIVER for the mutator arm (#14326 round 21, MEASURED GREEN: the
        # receiver test looked only at the literal `sys.modules` expression while the
        # `AugAssign` arm already unioned `registry_aliases`).
        "`__ior__` on a NAME ALIAS of the registry":
            '_sm = sys.modules\n_sm.__ior__({"tortoise.sdk": _Mirror})\n',
        # ⛔ ONE HOP, ONE CHAIN AND ONE LOOP TARGET for the module alias the row above pins
        # (#14326 round 22b): the chain pins the fixpoint, and the loop target is the SAME
        # bind with no `Assign` node — `_s = sys` was RED while `for _s in (sys,)` stayed
        # GREEN, an in-file asymmetry on the bind form this axis already resolves elsewhere
        # (`_sm = sys.modules` is resolved by `_aliases_of`'s `loops=True`).
        "assignment-alias CHAIN `_a = sys; _b = _a`":
            '_a = sys\n_b = _a\n_b.modules["tortoise.sdk"] = _Mirror\n',
        "`for _s in (sys,)` TARGET":
            'for _s in (sys,):\n    _s.modules["tortoise.sdk"] = _Mirror\n',
        # ⛔ THE COMPOSED `__dict__` SPELLINGS (#14326 round 22, MEASURED GREEN at `f6214869`:
        # both inserted the entry while `sys.__dict__["modules"]` — the arm this one is a
        # `getattr`/`.get` away from — was RED). `_is_namespace_expr` already resolves
        # `getattr(x, "__dict__")` on the sibling mapping, so the registry axis owes it.
        "`getattr(sys, \"__dict__\")[\"modules\"]`":
            'getattr(sys, "__dict__")["modules"]["tortoise.sdk"] = _Mirror\n',
        "`sys.__dict__.get(\"modules\")`":
            'sys.__dict__.get("modules")["tortoise.sdk"] = _Mirror\n',
        "`getattr(sys, \"__dict__\").get(\"modules\")`":
            'getattr(sys, "__dict__").get("modules")["tortoise.sdk"] = _Mirror\n',
        # ⛔ A `.get` DEFAULT DOES NOT SAVE IT (#14326 round 23, MEASURED GREEN at `35bd48ec`:
        # both rows below inserted the entry while the no-default spelling was RED). The
        # default never fires because the key EXISTS, so the arity is `>= 1`.
        "`sys.__dict__.get(\"modules\", {})`":
            'sys.__dict__.get("modules", {})["tortoise.sdk"] = _Mirror\n',
        "`vars(sys).get(\"modules\", {})`":
            'vars(sys).get("modules", {})["tortoise.sdk"] = _Mirror\n',
        # ⛔ THE KEY IS A STATIC STRING LIKE EVERY OTHER KEY IN THIS FILE (#14326 round 24,
        # EACH MEASURED GREEN at `bce008ce9b` while the plainly-spelled twin was RED). Every
        # other key the pin reads — `setattr(...)`, `__dict__[...]`, `_CACHE[...]` — goes
        # through `_string_keys_of`/`_possible_strings`, which resolve a NAME bound to a
        # literal and a literal-only `+`. The five registry shapes below still matched a raw
        # `ast.Constant`, so the SAME key one bind away walked past:
        #
        #     _a = "modules";  getattr(sys, _a)[…]          -> 41 passed
        #     getattr(sys, "mod" + "ules")[….]                 -> 41 passed
        #     _k = "modules";  sys.__dict__[_k][…]          -> 41 passed
        #     sys.__dict__.get("mod" + "ules")[…]             -> 41 passed
        #     dict.get(sys.__dict__, "mod" + "ules")[…]      -> 41 passed
        #
        # The five now share `_string_keys_of`, so the registry axis speaks the same key
        # vocabulary as the rest of the file; the narrowing is untouched (every form is still
        # pinned to `sys`), and the inert controls below prove the key resolution does not
        # compose past an unrelated module.
        # ⛔ THE UNBOUND READ'S RECEIVER IS NOT WHAT IS READ (#14326 round 25 P1, MEASURED
        # GREEN at `7508592c24` while the literal `dict.get` twin was RED):
        # `X.get(sys.__dict__, "modules") is sys.modules` is `True` for EVERY spelling of the
        # receiver, because `args[0]`/`args[1]` choose the mapping. Round 24 narrowed the arm
        # to `dict`/`builtin_aliases` and then pinned that narrowing with a "control" row —
        # the blessed-fail-open shape this predicate's own docstring names. These rows red if
        # the receiver clause ever comes back; the last two are the CALLEE carried in a name,
        # which the attribute-shaped arm cannot see at all.
        # ⛔ THE CALLEE IS IRRELEVANT — THE ARGUMENT SHAPE IS THE IDENTITY (#14326 round 26).
        # Each of these replaced the registry at `e1cd3cad95` while the INLINE spelling was
        # RED. Enumerating callees cannot converge (a lambda, an `operator.getitem`, an alias
        # of an alias), so the arm now matches `(<sys dict>, "modules")` by SHAPE and these
        # rows are what red if a callee test is ever reintroduced.
        "`builtins.dict.get` CARRIED in a name":
            'import builtins\n_bg = builtins.dict.get\n'
            '_bg(sys.__dict__, "modules")["tortoise.sdk"] = _Mirror\n',
        # ⛔ AND THE `builtins` MODULE ITSELF IS ALIASED, NOT SPELLED (#14326 round 27, EACH
        # MEASURED GREEN at `2b17619cd9` while the `import builtins` spelling was RED).
        # Resolving the VALUE's spelling left the MODULE's spelling open — the same asymmetry
        # one hop out, which is why `sys` was already aliased this way.
        "`from builtins import getattr as _g`":
            'from builtins import getattr as _g\n'
            '_g(sys, "modules")["tortoise.sdk"] = _Mirror\n',
        # ⛔ AND THE MODULE ALIAS BY ASSIGNMENT, which is what the `builtins_names` fixpoint is
        # for (#14326 round 28 P3, MEASURED: reverting the fixpoint to the import aliases alone
        # left `41 passed` — these two rows are what red it).
        # ⛔ AND IT CARRIES `import builtins` NOW, WHICH IT DID NOT (#14326 round 30):
        # `_bm = builtins` with no import raises `NameError: name 'builtins' is not defined`
        # (measured by executing the row source), so the clause it pinned was exercised by a
        # spelling that could never run — the same defect the sibling round-28 rows had.
        "`_bm = builtins` then `_bm.getattr`":
            'import builtins\n_bm = builtins\n_g = _bm.getattr\n'
            '_g(sys, "modules")["tortoise.sdk"] = _Mirror\n',
        "`_bb = _b` CHAIN then `_bb.getattr`":
            'import builtins as _b\n_bb = _b\n_g = _bb.getattr\n'
            '_g(sys, "modules")["tortoise.sdk"] = _Mirror\n',
        "`import builtins as _b` then `_b.getattr`":
            'import builtins as _b\n_g = _b.getattr\n'
            '_g(sys, "modules")["tortoise.sdk"] = _Mirror\n',
        "`import builtins as _b` then `_b.vars`":
            'import builtins as _b\n_v = _b.vars\n'
            '_v(sys)["modules"]["tortoise.sdk"] = _Mirror\n',
        # ⛔ AND THE `builtins` MODULE REACHED BY ASSIGNMENT, WHICH IS A DIFFERENT BIND FORM
        # FROM AN IMPORT AND WAS UNPINNED (#14326 round 28, MEASURED: `builtins_names`'s
        # `_aliases_of(… builtin_roots …)` union could be DELETED with the file at
        # `41 passed` — the docstring above claims `_b = builtins` is resolved "the way `sys`
        # already is", and no row exercised it). Both rows below are RED at the head and
        # GREEN when that union is removed, so the resolve is pinned rather than asserted.
        # ⛔ AND BOTH ROWS NOW CARRY `import builtins`, WHICH THEY DID NOT (#14326 round 30):
        # without it `_b = builtins` raises `NameError: name 'builtins' is not defined`, so the
        # pin they claimed was over a lever that CANNOT RUN — measured by executing the row
        # source — while `_b = __builtins__` (no import needed) stayed GREEN. Same intent, an
        # executable spelling.
        "`_b = builtins` (ASSIGNMENT) then `_b.getattr`":
            'import builtins\n_b = builtins\n_g = _b.getattr\n'
            '_g(sys, "modules")["tortoise.sdk"] = _Mirror\n',
        "`_b = builtins` (ASSIGNMENT) then `_b.vars`":
            'import builtins\n_b = builtins\n_v = _b.vars\n'
            '_v(sys)["modules"]["tortoise.sdk"] = _Mirror\n',
        # ⛔ AND `__builtins__`, WHICH IS NOT A SPELLING OF `builtins` BUT THE ONLY BINDING-FREE
        # WAY TO REACH IT (#14326 round 30, EVERY ROW BELOW MEASURED GREEN at `de42a75ac4b5`
        # while the `import builtins` twins were RED). CPython injects `__builtins__` into every
        # module's globals, so these need NO import at all — the cheapest callee launder there
        # is, on the axis rounds 24-28 built one spelling at a time. `builtin_roots` now seeds
        # it, so the SAME resolve the round-27 `asname` import gets applies here.
        "`__builtins__.getattr` (NO import)":
            '_g = __builtins__.getattr\n'
            '_g(sys, "modules")["tortoise.sdk"] = _Mirror\n',
        "`_b = __builtins__` then `_b.getattr`":
            '_b = __builtins__\n_g = _b.getattr\n'
            '_g(sys, "modules")["tortoise.sdk"] = _Mirror\n',
        "`__builtins__.vars` (NO import)":
            '_v = __builtins__.vars\n'
            '_v(sys)["modules"]["tortoise.sdk"] = _Mirror\n',
        # ⛔ AND THE BARE SUBSCRIPT, WHICH IS THE OTHER FORM `__builtins__` TAKES: the MODULE in
        # `__main__`, its `__dict__` in an IMPORTED module — so `__builtins__["getattr"]` is a
        # live lever in exactly the context a launderer controls, and leaving it GREEN while the
        # attribute twin reds would be the in-file asymmetry round 20 names. Scoped to the
        # `__builtins__` names and its aliases, because `builtins["getattr"]` is a TypeError.
        "`__builtins__[\"getattr\"]` BARE SUBSCRIPT":
            '_g = __builtins__["getattr"]\n'
            '_g(sys, "modules")["tortoise.sdk"] = _Mirror\n',
        "`_b = __builtins__` then `_b[\"vars\"]`":
            '_b = __builtins__\n_v = _b["vars"]\n'
            '_v(sys)["modules"]["tortoise.sdk"] = _Mirror\n',
        # ⛔ AND THE BUILTIN REACHED THROUGH THE MODULE'S OWN MAPPING (#14326 round 30, EACH
        # MEASURED GREEN at `de42a75ac4b5` while `builtins.getattr` itself was RED). The value
        # is the builtin one COMPOSITION further out, which `_aliases_of` never resolved — the
        # same "resolved forms" set `_is_namespace_expr` and `_is_sys_dict_mapping` already
        # speak on the sibling axes. All four go through `_string_keys_of`, like every other
        # key here, so the name-bound key row below pins that clause rather than assuming it.
        "`builtins.__dict__[\"getattr\"]` MAPPING LOOKUP":
            'import builtins\n_g = builtins.__dict__["getattr"]\n'
            '_g(sys, "modules")["tortoise.sdk"] = _Mirror\n',
        "`vars(builtins)[\"getattr\"]` MAPPING LOOKUP":
            'import builtins\n_g = vars(builtins)["getattr"]\n'
            '_g(sys, "modules")["tortoise.sdk"] = _Mirror\n',
        "`getattr(builtins, \"getattr\")` MAPPING LOOKUP":
            'import builtins\n_g = getattr(builtins, "getattr")\n'
            '_g(sys, "modules")["tortoise.sdk"] = _Mirror\n',
        "`dict.get(builtins.__dict__, \"getattr\")` MAPPING LOOKUP":
            'import builtins\n_g = dict.get(builtins.__dict__, "getattr")\n'
            '_g(sys, "modules")["tortoise.sdk"] = _Mirror\n',
        "MAPPING LOOKUP with a NAME-BOUND key":
            'import builtins\n_k = "getattr"\n_g = builtins.__dict__[_k]\n'
            '_g(sys, "modules")["tortoise.sdk"] = _Mirror\n',
        # …and the DIRECT spelling, with no intermediate name: its callee has no `Name` or
        # `Attribute` segment at all, so `_callee_names_of` returned the empty set and every
        # arm that intersects it walked past — which is what `_callee_names_in` closes.
        "`builtins.__dict__[\"getattr\"](…)` DIRECT":
            'import builtins\n'
            'builtins.__dict__["getattr"](sys, "modules")["tortoise.sdk"] = _Mirror\n',
        "`__builtins__[\"getattr\"](…)` DIRECT":
            '__builtins__["getattr"](sys, "modules")["tortoise.sdk"] = _Mirror\n',
        # ⛔ AND AN ALIAS OF AN ALIAS (#14326 round 28, MEASURED: collapsing
        # `builtin_aliases`' `_aliases_of(…, loops=True)` to a SINGLE PASS left `41 passed`,
        # so the fixpoint was unpinned and `_h = _g` — a statically visible one-hop alias of
        # an aliased builtin — would have re-opened the fail-open the `getattr`/`vars` arms
        # and the unbound-read shape arm all resolve through that set. Two hops, on the READ
        # axis, which is the axis the arm reads it on.
        "`_g = getattr` then `_h = _g` (ALIAS OF AN ALIAS)":
            '_g = getattr\n_h = _g\n'
            '_h(sys, "modules")["tortoise.sdk"] = _Mirror\n',
        "`_v = vars` then `_w = _v` (ALIAS OF AN ALIAS)":
            '_v = vars\n_w = _v\n'
            '_w(sys)["modules"]["tortoise.sdk"] = _Mirror\n',
        # ⛔ AND THE LOOP-TARGET BIND OF A CALLEE ALIAS (#14326 round 28, MEASURED: the
        # `loops=True` on `builtin_aliases`' `_aliases_of` could be relaxed to a single CENSUS
        # with the file at `41 passed` — `loops` is what admits a `for` TARGET as a bind, and
        # no row bound a builtin through one). The shape is RED at the head and GREEN once
        # `loops` is off, so it is the clause's own row rather than a neighbouring spelling's.
        "`for _g in (getattr,)` TARGET":
            'for _g in (getattr,):\n'
            '    _g(sys, "modules")["tortoise.sdk"] = _Mirror\n',
        "`for _v in (vars,)` TARGET":
            'for _v in (vars,):\n'
            '    _v(sys)["modules"]["tortoise.sdk"] = _Mirror\n',
        "`async for _g in (getattr,)` TARGET":
            'async def _f(it):\n'
            '    async for _g in (getattr,):\n'
            '        _g(sys, "modules")["tortoise.sdk"] = _Mirror\n',
        "`builtins.getattr` CARRIED in a name":
            'import builtins\n_g = builtins.getattr\n'
            '_g(sys, "modules")["tortoise.sdk"] = _Mirror\n',
        "`builtins.vars` CARRIED in a name":
            'import builtins\n_v = builtins.vars\n'
            '_v(sys)["modules"]["tortoise.sdk"] = _Mirror\n',
        "`partial(dict.get)`":
            'from functools import partial\n_p = partial(dict.get)\n'
            '_p(sys.__dict__, "modules")["tortoise.sdk"] = _Mirror\n',
        "`getattr(dict, \"get\")` carried in a name":
            '_mget = getattr(dict, "get")\n'
            '_mget(sys.__dict__, "modules")["tortoise.sdk"] = _Mirror\n',
        "`dict.__dict__[\"get\"]` carried in a name":
            '_mget = dict.__dict__["get"]\n'
            '_mget(sys.__dict__, "modules")["tortoise.sdk"] = _Mirror\n',
        "`for _dg in (dict.get,)` TARGET":
            'for _dg in (dict.get,):\n'
            '    _dg(sys.__dict__, "modules")["tortoise.sdk"] = _Mirror\n',
        "UNBOUND `.get` through `builtins.dict`":
            'builtins.dict.get(sys.__dict__, "modules")["tortoise.sdk"] = _Mirror\n',
        "UNBOUND `dict.get` CARRIED in a name":
            '_dg = dict.get\n_dg(sys.__dict__, "modules")["tortoise.sdk"] = _Mirror\n',
        "UNBOUND `dict.__getitem__` CARRIED in a name":
            '_di = dict.__getitem__\n'
            '_di(sys.__dict__, "modules")["tortoise.sdk"] = _Mirror\n',
        "`getattr` with a NAME-CARRIED attribute":
            '_a = "modules"\ngetattr(sys, _a)["tortoise.sdk"] = _Mirror\n',
        "`getattr` with a literal-only `+` attribute":
            'getattr(sys, "mod" + "ules")["tortoise.sdk"] = _Mirror\n',
        "`sys.__dict__` with a NAME-CARRIED key":
            '_k = "modules"\nsys.__dict__[_k]["tortoise.sdk"] = _Mirror\n',
        "`__dict__.get` with a literal-only `+` key":
            'sys.__dict__.get("mod" + "ules")["tortoise.sdk"] = _Mirror\n',
        "`getattr` with a NAME-CARRIED `__dict__` arg":
            '_d = "__dict__"\ngetattr(sys, _d)["modules"]["tortoise.sdk"] = _Mirror\n',
        "UNBOUND `dict.get(sys.__dict__, …)` with a `+` key":
            'dict.get(sys.__dict__, "mod" + "ules")["tortoise.sdk"] = _Mirror\n',
        # ⛔ AND EVERY OTHER BIND FORM THE SIBLING AXES ALREADY RESOLVE (#14326 round 23, EACH
        # MEASURED GREEN at `35bd48ec`): a `match` CAPTURE, a parameter DEFAULT, a TUPLE
        # UNPACK, a `with` TARGET, a TUPLE loop target and an ALIAS OF A `sys` ATTRIBUTE.
        # `_sys_names_of` goes through `_aliases_of` (`loops=True`), which is where those
        # arms live, so these six are the rows that make that reuse load-bearing rather than
        # an implementation detail a later rewrite could quietly narrow again.
        "`match sys: case _s:` CAPTURE":
            'match sys:\n    case _s:\n        _s.modules["tortoise.sdk"] = _Mirror\n',
        "parameter DEFAULT `_r=sys`":
            'def _g(_r=sys):\n    _r.modules["tortoise.sdk"] = _Mirror\n',
        "TUPLE UNPACK `(_s,) = (sys,)`":
            '(_s,) = (sys,)\n_s.modules["tortoise.sdk"] = _Mirror\n',
        "`with sys as _s` TARGET":
            'with sys as _s:\n    _s.modules["tortoise.sdk"] = _Mirror\n',
        "TUPLE loop target `for _s, _m in ((sys, 1),)`":
            'for _s, _m in ((sys, 1),):\n    _s.modules["tortoise.sdk"] = _Mirror\n',
        "`_s = os.sys` ATTRIBUTE alias":
            'import os\n_s = os.sys\n_s.modules["tortoise.sdk"] = _Mirror\n',
        # ⛔ AN ALIAS OF THE `__dict__` MAPPING IS THE MAPPING (#14326 round 23, MEASURED GREEN:
        # the datum was already in `namespaces` — `_ns = _bi.__dict__` is resolved on the
        # namespace axis — and was never consulted for the registry read).
        "`__d = sys.__dict__` SUBSCRIPT":
            '__d = sys.__dict__\n__d["modules"]["tortoise.sdk"] = _Mirror\n',
        "`__d = sys.__dict__` `.get`":
            '__d = sys.__dict__\n__d.get("modules")["tortoise.sdk"] = _Mirror\n',
        "`__d` from a LOOP over `(sys.__dict__,)`":
            'for __d in (sys.__dict__,):\n    __d["modules"]["tortoise.sdk"] = _Mirror\n',
        # ⛔ THE UNBOUND READ TWINS (#14326 round 23, MEASURED GREEN while the bound spelling
        # was RED) — the store axis already pins its own unbound twins, `offset=1`.
        "UNBOUND `dict.get(sys.__dict__, …)`":
            'dict.get(sys.__dict__, "modules")["tortoise.sdk"] = _Mirror\n',
        "UNBOUND `dict.__getitem__(sys.__dict__, …)`":
            'dict.__getitem__(sys.__dict__, "modules")["tortoise.sdk"] = _Mirror\n',
        # ⛔ AND A BACK-ORDERED CHAIN, which is what makes `_aliases_of`'s fixpoint load-bearing
        # (#14326 round 23: the FORWARD chain resolves in one pass, so a single-pass walk
        # satisfied every earlier row — measured: `range(2)` → `range(1)` left the whole file
        # at 41 passed. This row reds under a one-pass walk and passes at the fixpoint).
        "BACK-ORDERED chain `_b = _a` before `_a = sys`":
            '_b = _a\n_a = sys\n_b.modules["tortoise.sdk"] = _Mirror\n',
        # ⛔ AND A CALLEE ALIAS IS NOT A SPELLING (#14326 round 24, MEASURED GREEN at
        # `bce008ce`: `_d = dict; _d.get(sys.__dict__, "modules")[…]`,
        # `_g = getattr; _g(sys, "modules")[…]`, `_g(sys, "__dict__")[…]` and
        # `_v = vars; _v(sys)["modules"][…]` each inserted the entry). `launderers` exists
        # for exactly this on the SETATTR axis; the registry's resolved forms matched the
        # builtin's NAME instead.
        "`_d = dict` then `_d.get(sys.__dict__, …)`":
            '_d = dict\n_d.get(sys.__dict__, "modules")["tortoise.sdk"] = _Mirror\n',
        "`_g = getattr` then `_g(sys, \"modules\")`":
            '_g = getattr\n_g(sys, "modules")["tortoise.sdk"] = _Mirror\n',
        "`_g = getattr` then `_g(sys, \"__dict__\")`":
            '_g = getattr\n_g(sys, "__dict__")["modules"]["tortoise.sdk"] = _Mirror\n',
        "`_v = vars` then `_v(sys)[\"modules\"]`":
            '_v = vars\n_v(sys)["modules"]["tortoise.sdk"] = _Mirror\n',
    }
    # ⛔ REPLACING THE WHOLE REGISTRY, WITH NO KEY VOCABULARY AT ALL (#14326 round 24,
    # MEASURED GREEN at `bce008ce`: every spelling below, while every INSERTION spelling was
    # RED). It defeats the pin's own premise in the simplest way available — `run()`'s deferred
    # import resolves `sys.modules` at CALL time — so the arm is receiver-only, and its message
    # says REPLACES rather than naming a key.
    registry_replacements = {
        "`sys.modules = {…}`":
            'sys.modules = {"tortoise.sdk": _Mirror}\n',
        "`sys.__dict__[\"modules\"] = {…}`":
            'sys.__dict__["modules"] = {"tortoise.sdk": _Mirror}\n',
        "`__d = sys.__dict__` then `__d[\"modules\"] = {…}`":
            '__d = sys.__dict__\n__d["modules"] = {"tortoise.sdk": _Mirror}\n',
        # ⛔ AND THE UNPACK / LOOP-TARGET SPELLINGS OF THE SAME REPLACEMENT (#14326 round 25,
        # MEASURED GREEN at `1515591f`: every row below replaced the registry with the pin
        # GREEN, while the plain `sys.modules = {…}` row was RED). An unpack target stores to
        # each ELEMENT, and a `for` target is a store with no `Assign` node at all.
        "`sys.modules, _x = {…}, 1` (UNPACK)":
            'sys.modules, _x = {"tortoise.sdk": _Mirror}, 1\n',
        "`[sys.modules] = [{…}]` (LIST unpack)":
            '[sys.modules] = [{"tortoise.sdk": _Mirror}]\n',
        "`sys.__dict__[\"modules\"], _x = {…}, 1`":
            'sys.__dict__["modules"], _x = {"tortoise.sdk": _Mirror}, 1\n',
        "`for sys.modules in ({…},)` TARGET":
            'for sys.modules in ({"tortoise.sdk": _Mirror},):\n    pass\n',
        # ⛔ `async for` IS THE SAME ARM ONE NODE TYPE OVER, AND IT WAS UNPINNED (#14326
        # round 27, MEASURED at `77a7f04ee44f`: dropping `ast.AsyncFor` from
        # `_constructor_provenance_findings`' loop-target arm left this file at **41 passed**
        # while the lever below stayed GREEN — a live fail-open behind a clause no mutant
        # could red, which is the defect class this file deletes clauses for). The `for` row
        # above cannot carry it: the sync spelling never touches the `AsyncFor` member.
        "`async for sys.modules in ({…},)` TARGET":
            'async def _a():\n'
            '    async for sys.modules in ({"tortoise.sdk": _Mirror},):\n        pass\n',
        # ⛔ AND A `sys.__dict__` ALIAS PRODUCED BY AN ALIASED CALLEE (#14326 round 25,
        # MEASURED GREEN at `1515591f`: `builtin_aliases` was built AFTER `sys_dict_names`, so
        # the one site that FILLS that field read it empty and three arms downstream were
        # blind to the alias).
        # ⛔ `_d = sys.__dict__; _d |= {"modules": {…}}` REPLACES (#14326 round 27, MEASURED
        # GREEN at `2b17619cd9` while `_d["modules"] = {…}` and `_d["modules"] |= {…}` were
        # both RED). The key is `"modules"` — neither the class key nor the module key — so the
        # receiver-keyed vocabulary below could not see it.
        # ⛔ AND THE DIRECT FORM, WHOSE ROUND-27 EXEMPTION WAS FALSE (#14326 round 28 P1):
        # `dict.__ior__` mutates `sys.__dict__` in place and only the trailing `STORE_ATTR`
        # raises, so `try/except AttributeError: pass` — or `contextlib.suppress` — keeps the
        # mutation. Both were measured installing the mirror end-to-end.
        "`sys.__dict__ |= {\"modules\": {…}}` (swallowed AttributeError)":
            'try:\n    sys.__dict__ |= {"modules": {"tortoise.sdk": _Mirror}}\n'
            'except AttributeError:\n    pass\n',
        # ⛔ `from sys import __dict__` BINDS THE LIVE MAPPING (#14326 round 28 P2, MEASURED
        # GREEN while the `_d = sys.__dict__` ASSIGNMENT bind was RED — the same one-clause
        # rule the `from sys import modules as _m` row already carried).
        "`from sys import __dict__ as _d` then SUBSCRIPT store":
            'from sys import __dict__ as _d\n'
            '_d["modules"] = {"tortoise.sdk": _Mirror}\n',
        "`from sys import __dict__ as _d` then `|=`":
            'from sys import __dict__ as _d\n'
            '_d |= {"modules": {"tortoise.sdk": _Mirror}}\n',
        "`_d = sys.__dict__` then `_d |= {\"modules\": {…}}`":
            '_d = sys.__dict__\n'
            '_d |= {"modules": {"tortoise.sdk": _Mirror}}\n',
        "`_d = getattr` then `__d = _g(sys, \"__dict__\")` REPLACES":
            '_g = getattr\n__d = _g(sys, "__dict__")\n'
            '__d["modules"] = {"tortoise.sdk": _Mirror}\n',
        # ⛔ AND THE TWO RESOLVE PATHS THE `builtins_names` UNION AND THE `builtin_aliases`
        # FIXPOINT CARRY, ON THE STORE AXIS (#14326 round 28). The read axis pins them one
        # test up; the replacement arm reaches the same alias set through
        # `sys_dict_names`, so the union and the fixpoint are load-bearing on BOTH axes and
        # the rows below red when either is removed.
        # ⛔ AND THIS ROW CARRIES `import builtins` NOW (#14326 round 30): `_b = builtins` with
        # no import raises `NameError`, so it pinned its clause with a lever that cannot run.
        "`_b = builtins` then `_g = _b.getattr` REPLACES":
            'import builtins\n_b = builtins\n_g = _b.getattr\n__d = _g(sys, "__dict__")\n'
            '__d["modules"] = {"tortoise.sdk": _Mirror}\n',
        "`_g = getattr` then `_h = _g` REPLACES":
            '_g = getattr\n_h = _g\n__d = _h(sys, "__dict__")\n'
            '__d["modules"] = {"tortoise.sdk": _Mirror}\n',
        # ⛔ AND A CALLEE ALIAS BOUND BY A LOOP TARGET, ON THE STORE AXIS (#14326 round 28):
        # `sys_dict_names` reads the same `builtin_aliases`, so the `for`-target bind is
        # load-bearing here too and reds when `loops` is off.
        # ⛔ AND IT NOW CARRIES `import builtins`, WHICH IT DID NOT (#14326 round 30): without
        # it `(builtins,)` raises `NameError`, so the clause it pinned was exercised by a lever
        # that CANNOT RUN. Same clause, an executable spelling.
        "`for _b in (builtins,)` TARGET then `_b.getattr` REPLACES":
            'import builtins\nfor _b in (builtins,):\n'
            '    _g = _b.getattr\n'
            '    __d = _g(sys, "__dict__")\n'
            '    __d["modules"] = {"tortoise.sdk": _Mirror}\n',
        # ⛔ AND THE STORE AXIS OF `__builtins__` (#14326 round 30, BOTH MEASURED GREEN at
        # `de42a75ac4b5`): the READ axis above pins the same resolve one spelling over, so the
        # root seed is load-bearing on BOTH axes and these rows red if it is removed.
        "`_g = __builtins__.getattr` then `__d` REPLACES":
            '_g = __builtins__.getattr\n'
            '__d = _g(sys, "__dict__")\n'
            '__d["modules"] = {"tortoise.sdk": _Mirror}\n',
        "`_g = builtins.__dict__[\"getattr\"]` then `__d` REPLACES":
            'import builtins\n_g = builtins.__dict__["getattr"]\n'
            '__d = _g(sys, "__dict__")\n'
            '__d["modules"] = {"tortoise.sdk": _Mirror}\n',
    }
    for name, lever in registry_replacements.items():
        got = ctor_findings(shipped + "class _Mirror:\n    pass\n" + lever)
        assert got and any("REPLACES" in g for g in got), (name, lever, got)
    # ⛔ THE IMPORT-PATH AXIS, WHICH HAS ITS OWN MESSAGE (#14326 round 29 P2). `del` alone is
    # inert (its words are in the controls below); `sys.path` alone is inert; together they
    # re-import a planted class. Measured end-to-end: a fake `tortoise/sdk.py` resolved as the
    # constructor with the pin GREEN, and the artifact's own `tools/read_wave_latency.py`
    # already calls `sys.path.insert(0, _REPO_ROOT)`, so the axis is live rather than academic.
    path_levers = {
        "`sys.path.insert` + `del sys.modules[...]` re-imports a planted class":
            'sys.path.insert(0, "/tmp/evil")\n'
            'del sys.modules["tortoise.sdk"]\n',
        "`sys.meta_path.insert` + `del` (a finder, not a path)":
            'sys.meta_path.insert(0, _Evil())\n'
            'del sys.modules["tortoise.sdk"]\n',
        "`sys.path.__setitem__` slice + `del`":
            'sys.path.__setitem__(slice(0, 0), ["/tmp/evil"])\n'
            'del sys.modules["tortoise"], sys.modules["tortoise.sdk"]\n',
        # ⛔ AND THE SPELLINGS ROUND 29's OWN CLAUSE COULD NOT SEE (#14326 round 30 P1/P2,
        # each MEASURED GREEN end-to-end: a planted `/tmp/evil/tortoise/sdk.py` resolved).
        # The assign branch tested the BASE of the target instead of the target, so a plain
        # assign was dead code; `clear`/`pop` empty the registry without being a `Delete` node;
        # and an alias of the registry was not the registry on this axis.
        "`sys.path.__init__(...)` + `del`":
            'sys.path.__init__(["/tmp/evil"] + sys.path)\n'
            'del sys.modules["tortoise.sdk"]\n',
        "`list.__init__(sys.path, ...)` unbound + `del`":
            'list.__init__(sys.path, ["/tmp/evil"] + sys.path)\n'
            'del sys.modules["tortoise.sdk"]\n',
        "`setattr(sys, \"path\", ...)` + `del`":
            'setattr(sys, "path", ["/tmp/evil"] + sys.path)\n'
            'del sys.modules["tortoise.sdk"]\n',
        "a plain ALIAS `_p = sys.path` then `_p.insert` + `del`":
            '_p = sys.path\n_p.insert(0, "/tmp/evil")\n'
            'del sys.modules["tortoise.sdk"]\n',
        # ⛔ AN IMPORT BINDS `sys.path` TOO (#14326 fix lane at `a12902a5`, MEASURED GREEN
        # at that head — a planted `/tmp/evil/tortoise/sdk.py` resolved end-to-end — while
        # the ASSIGNMENT spelling above was RED). `from sys import path as _p` binds exactly
        # the object the row above binds; only the BIND FORM differed, and `_aliases_of`'s
        # import arm enumerated `{modules, __dict__}`, so the path axis was blind to it.
        # Deleting `_PATH_ATTRS` from that arm's member set reds this row and `meta_path`
        # below; the `from sys import argv` control stays GREEN in
        # `test_the_IMPORT_PATH_axis_residuals_are_NAMED_and_pinned_GREEN`'s neighbours.
        "an IMPORT ALIAS `from sys import path as _p` then `_p.insert` + `del`":
            'from sys import path as _p\n_p.insert(0, "/tmp/evil")\n'
            'del sys.modules["tortoise.sdk"]\n',
        "an IMPORT ALIAS `from sys import meta_path as _mp` then `_mp.insert` + `del`":
            'from sys import meta_path as _mp\n_mp.insert(0, None)\n'
            'del sys.modules["tortoise.sdk"]\n',
        # ⛔ ROUND 32: `__init__` MERGES so it is an ancestor STORE; and the UNBOUND
        # insert/append/extend on the path was the cross-product member two individually
        # complete sets both missed.
        # ⛔ THE UNBOUND `insert`/`append`/`extend` ON THE PATH (#14326 round 32 P2): the
        # cross-product member of two sets that were each individually complete.
        # ⛔ AND THE ALIAS IN THAT POSITION IS NOW CLOSED RATHER THAN "STILL OPEN AND NAMED"
        # (this lane, round 33, MEASURED GREEN at `22ad6aa2c5` and RED here): the unbound
        # spelling takes the path as `args[0]`, so it needs the SAME `_is_path_expr` the
        # receiver arm uses — threading it by hand is what left the two arms asymmetric. The
        # three rows below are the alias twins of the three above.
        "`list.insert(sys.path, ...)` unbound + `del`":
            'list.insert(sys.path, 0, "/tmp/evil")\n'
            'del sys.modules["tortoise.sdk"]\n',
        "`list.append(sys.path, ...)` unbound + `del`":
            'list.append(sys.path, "/tmp/evil")\n'
            'del sys.modules["tortoise.sdk"]\n',
        "`list.extend(sys.path, ...)` unbound + `del`":
            'list.extend(sys.path, ["/tmp/evil"])\n'
            'del sys.modules["tortoise.sdk"]\n',
        "`_p = sys.path` then `list.insert(_p, ...)` unbound alias + `del`":
            '_p = sys.path\nlist.insert(_p, 0, "/tmp/evil")\n'
            'del sys.modules["tortoise.sdk"]\n',
        "`_p = sys.path` then `list.append(_p, ...)` unbound alias + `del`":
            '_p = sys.path\nlist.append(_p, "/tmp/evil")\n'
            'del sys.modules["tortoise.sdk"]\n',
        "`_p = sys.path` then `list.extend(_p, ...)` unbound alias + `del`":
            '_p = sys.path\nlist.extend(_p, ["/tmp/evil"])\n'
            'del sys.modules["tortoise.sdk"]\n',
        "`sys.path = [...] + sys.path` + `del`":
            'sys.path = ["/tmp/evil"] + sys.path\n'
            'del sys.modules["tortoise"], sys.modules["tortoise.sdk"]\n',
        "`sys.path += [...]` (AugAssign, not the `__iadd__` call) + `del`":
            'sys.path += ["/tmp/evil"]\n'
            'del sys.modules["tortoise"], sys.modules["tortoise.sdk"]\n',
        "`sys.meta_path = [...] + sys.meta_path` + `del`":
            'sys.meta_path = [_Evil()] + sys.meta_path\n'
            'del sys.modules["tortoise.sdk"]\n',
        "`sys.path_hooks = [...] + sys.path_hooks` + `del`":
            'sys.path_hooks = [_Hook()] + sys.path_hooks\n'
            'del sys.modules["tortoise.sdk"]\n',
        "`sys.modules.clear()` after a poisoned path (no `Delete` node)":
            'sys.path.insert(0, "/tmp/evil")\n'
            'sys.modules.clear()\n',
        "`sys.modules.pop(...)` of the package after a poisoned path":
            'sys.path.insert(0, "/tmp/evil")\n'
            'sys.modules.pop("tortoise"); sys.modules.pop("tortoise.sdk")\n',
        "`dict.clear(sys.modules)` — the unbound spelling":
            'dict.clear(sys.modules)\n'
            'sys.path.insert(0, "/tmp/evil")\n',
        "`sys.modules.__delitem__(...)` after a poisoned path":
            'sys.modules.__delitem__("tortoise.sdk")\n'
            'sys.path.insert(0, "/tmp/evil")\n',
        "an ALIAS of the registry `del`ed after a poisoned path":
            '_m = sys.modules\n'
            'sys.path.insert(0, "/tmp/evil")\n'
            'del _m["tortoise"], _m["tortoise.sdk"]\n',
        "`sys.path[0:0] = [...]` slice assign + `del`":
            'sys.path[0:0] = ["/tmp/evil"]\n'
            'del sys.modules["tortoise.sdk"]\n',
        # ⛔ AND THE SIX ALIAS SPELLINGS ROUND 31 LEFT RAW (#14326 round 32 P1, EACH MEASURED
        # GREEN at `f7ff126b2c5a` AND END-TO-END: a planted `/tmp/evil14326/tortoise/sdk.py`
        # was the resolved constructor — `_p[0:0] = [...]`, `_p.__setitem__(0, ...)`,
        # `_p.__init__([...] + _p[1:])` and `list.__init__(_p, ...)` each printed
        # FAKE-CLASS-RESOLVED; `_p += [...]` / `_p.__iadd__(...)` are the in-place additions
        # that `sys.path.append` already reds). Round 31 resolved the one-bind alias for
        # `insert`/`append`/`extend` and left every OTHER form of the same object reading the
        # bare attribute predicate — the in-file asymmetry this lane exists to remove.
        "a plain ALIAS `_p = sys.path` then `_p[0:0] = [...]` + `del`":
            '_p = sys.path\n_p[0:0] = ["/tmp/evil"]\n'
            'del sys.modules["tortoise.sdk"]\n',
        "a plain ALIAS `_p = sys.path` then `_p.__setitem__` + `del`":
            '_p = sys.path\n_p.__setitem__(0, "/tmp/evil")\n'
            'del sys.modules["tortoise.sdk"]\n',
        "a plain ALIAS `_p = sys.path` then `_p.__init__` + `del`":
            '_p = sys.path\n_p.__init__(["/tmp/evil"] + _p[1:])\n'
            'del sys.modules["tortoise.sdk"]\n',
        "a plain ALIAS `_p = sys.path` then `list.__init__(_p, ...)` + `del`":
            '_p = sys.path\nlist.__init__(_p, ["/tmp/evil"] + _p[1:])\n'
            'del sys.modules["tortoise.sdk"]\n',
        "a plain ALIAS `_p = sys.path` then `_p.__iadd__` + `del`":
            '_p = sys.path\n_p.__iadd__(["/tmp/evil"])\n'
            'del sys.modules["tortoise.sdk"]\n',
        "a plain ALIAS `_p = sys.path` then `_p += [...]` + `del`":
            '_p = sys.path\n_p += ["/tmp/evil"]\n'
            'del sys.modules["tortoise.sdk"]\n',
        # ⛔ AND THE `sys.__dict__["path"]` MAPPING SPELLINGS (#14326 round 32 P2, EACH
        # MEASURED GREEN at `f7ff126b2c5a`, and `sys.__dict__["path"] = [...]` measured
        # end-to-end as a planted class). The REGISTRY axis has graded this spelling since
        # round 24 (`sys.__dict__["modules"] = ...`); the path axis read only the ATTRIBUTE,
        # which is the same asymmetry one key over.
        "`sys.__dict__[\"path\"] = [...]` store + `del`":
            'sys.__dict__["path"] = ["/tmp/evil"] + sys.path\n'
            'del sys.modules["tortoise.sdk"]\n',
        "`vars(sys)[\"path\"] = [...]` store + `del`":
            'vars(sys)["path"] = ["/tmp/evil"] + sys.path\n'
            'del sys.modules["tortoise.sdk"]\n',
        "`_d = sys.__dict__` then `_d[\"path\"] = [...]` + `del`":
            '_d = sys.__dict__\n_d["path"] = ["/tmp/evil"] + _d["path"]\n'
            'del sys.modules["tortoise.sdk"]\n',
        "`sys.__dict__[\"path\"].insert` + `del`":
            'sys.__dict__["path"].insert(0, "/tmp/evil")\n'
            'del sys.modules["tortoise.sdk"]\n',
        "`_p = sys.__dict__[\"path\"]` then `_p.insert` + `del`":
            '_p = sys.__dict__["path"]\n_p.insert(0, "/tmp/evil")\n'
            'del sys.modules["tortoise.sdk"]\n',
    }
    for name, lever in path_levers.items():
        got = ctor_findings(shipped + "class _Mirror:\n    pass\n" + lever)
        assert got and any("import search path" in g for g in got), (name, lever, got)
    # ⛔ AN ANCESTOR OF THE DEFERRED TARGET IS THE TARGET (#14326 round 31 P2). The DELETE axis
    # already applied this rule; the store and `|=` arms still tested EXACT keys, so
    # `sys.modules["tortoise"] = _pkg` (or the `|=` twin) followed by
    # `del sys.modules["tortoise.sdk"]` re-executed the planted package's `sdk.py` with the pin
    # GREEN. These land on the string-key arm, so they carry its message.
    ancestor_stores = {
        "ancestor registry STORE + `del` of the target":
            'sys.modules["tortoise"] = _pkg\n'
            'del sys.modules["tortoise.sdk"]\n',
        "ancestor registry `__init__` store + `del`":
            'sys.modules.__init__({"tortoise": _pkg})\n'
            'del sys.modules["tortoise.sdk"]\n',
        "`dict.__init__(sys.modules, ...)` unbound + `del`":
            'dict.__init__(sys.modules, {"tortoise": _pkg})\n'
            'del sys.modules["tortoise.sdk"]\n',
        "ancestor registry `|=` + `del` of the target":
            'sys.modules |= {"tortoise": _pkg}\n'
            'del sys.modules["tortoise.sdk"]\n',
    }
    for name, lever in ancestor_stores.items():
        got = ctor_findings(shipped + "class _Mirror:\n    pass\n" + lever)
        assert got and any("under a string key" in g or "AUGMENTS" in g for g in got), \
            (name, lever, got)
    # ⛔ THE CALL SITES THE `sys_dict_names` THREADING ACTUALLY FEEDS (#14326 round 24, each
    # RED at `bce008ce`): the review emptied three of the five sites and the file stayed at
    # `41 passed`, so the threading was load-bearing and unpinned. `_RegistryCtx` removes the
    # class by construction; these rows pin what it feeds. The message differs by arm
    # (`AUGMENTS` for the `|=` arm, `under a string key` for the mutator arms).
    registry_store_arms = {
        "`__d[\"modules\"] |=` (AugAssign arm)":
            '__d = sys.__dict__\n__d["modules"] |= {"tortoise.sdk": _Mirror}\n',
        "`__d[\"modules\"].update(…)` (mutator arm)":
            '__d = sys.__dict__\n__d["modules"].update({"tortoise.sdk": _Mirror})\n',
        "`dict.update(__d[\"modules\"], …)` (UNBOUND mutator)":
            '__d = sys.__dict__\ndict.update(__d["modules"], {"tortoise.sdk": _Mirror})\n',
        "`_m = __d[\"modules\"]` then `_m |=` (registry_aliases)":
            '__d = sys.__dict__\n_m = __d["modules"]\n_m |= {"tortoise.sdk": _Mirror}\n',
    }
    for name, lever in registry_store_arms.items():
        got = ctor_findings(shipped + "class _Mirror:\n    pass\n" + lever)
        assert got and any(("under a string key" in g) or ("AUGMENTS" in g)
                           or ("REPLACES" in g) for g in got), (name, lever, got)
    for name, lever in registry_spellings.items():
        got = ctor_findings(shipped + "class _Mirror:\n    pass\n" + lever)
        assert got and any("under a string key" in g for g in got), (name, lever, got)
    # ⛔ AND THE NARROWING CONTROLS, in the false-red direction: every resolved form above is
    # pinned to `sys` BY NAME on purpose, so the SAME SPELLING on another module must stay
    # GREEN — a general `getattr(<anything>, "modules")` rule would red ordinary code, which
    # is the expensive direction here.
    for inert in ('from mypkg import modules as _m\n_m["tortoise.sdk"] = _Mirror\n',
                  'getattr(other, "modules")["tortoise.sdk"] = _Mirror\n',
                  'vars(other)["modules"]["tortoise.sdk"] = _Mirror\n',
                  # ⛔ THE ATTRIBUTE FORM OF THE SAME CONTROL (#14326 round 21, MEASURED: this
                  # was RED before the base-name arm was narrowed to a `sys` name — an
                  # unrelated module's own `modules` attribute reddened a pin that exists to
                  # protect a different object, which is the expensive direction here).
                  'other.modules["tortoise.sdk"] = _Mirror\n',
                  # ⛔ AND THE UNRELATED-RECEIVER CONTROLS for the unbound-read arm (#14326
                  # round 25): dropping the receiver clause must not red an ordinary mapping
                  # lookup. Both choose a DIFFERENT mapping, so they are not the registry read
                  # and must stay GREEN.
                  'dict.get(_cfg, "modules")["tortoise.sdk"] = _Mirror\n',
                  '_cfg.get("modules")["tortoise.sdk"] = _Mirror\n',
                  # …and a DIFFERENT KEY on sys's own dict is not the registry read, which is
                  # what pins the read arm's `_is_modules_key(node.args[1])` clause (#14326
                  # round 26, MEASURED: dropping that clause left the file at `41 passed`).
                  # ⛔ AND THE ALIAS CONTROLS FOR ROUND 27: an aliased module that is NOT
                  # `builtins`, and an unrelated `__dict__` ending in `|=`.
                  # ⛔ THE DIRECT `sys.__dict__ |= {…}` IS NOT HERE ANY MORE — round 27 put it
                  # in this list on a FALSE premise (round 28 P1: `dict.__ior__` mutates
                  # before the `STORE_ATTR` raises, so a swallowed error keeps the mutation).
                  # It is a fail-open row now.
                  'from mypkg import getattr as _g\n_g(sys, "modules")["tortoise.sdk"] = _Mirror\n',
                  '_d = other.__dict__\n'
                  '_d |= {"modules": {"tortoise.sdk": _Mirror}}\n',
                  'dict.get(sys.__dict__, "os")["tortoise.sdk"] = _Mirror\n',
                  'sys.__dict__.get("path")["tortoise.sdk"] = _Mirror\n',
                  # ⛔ AND THE ALIAS/COMPOSITION CONTROLS FOR ROUND 22, for the same reason: an
                  # assignment alias of ANOTHER module, and the `__dict__` compositions on
                  # one, must all stay GREEN or the narrowing has been lost.
                  '_o = other\n_o.modules["tortoise.sdk"] = _Mirror\n',
                  'getattr(other, "__dict__")["modules"]["tortoise.sdk"] = _Mirror\n',
                  'other.__dict__.get("modules")["tortoise.sdk"] = _Mirror\n',
                  # ⛔ AND THE ROUND-23 CONTROLS. The `sys`-dict alias arm accepts a name bound
                  # to `sys.__dict__` ONLY: an ordinary NAMESPACE name is not that mapping, and a
                  # `with` target whose context expression is an unknown call binds an unknown
                  # value. Both must stay GREEN, as must the unbound `.get` on an unrelated dict.
                  '_ns = globals()\n_ns["modules"]["tortoise.sdk"] = _Mirror\n',
                  '_od = other.__dict__\n_od["modules"]["tortoise.sdk"] = _Mirror\n',
                  'dict.get(_cfg, "modules")["tortoise.sdk"] = _Mirror\n',
                  'with _cm(sys) as _s:\n    _s.modules["tortoise.sdk"] = _Mirror\n',
                  # ⛔ AND THE KEY-RESOLUTION CONTROLS (#14326 round 24). Resolving the KEY
                  # must not resolve it past `sys`: the same name-carried / `+` key on an
                  # unrelated module or an unrelated dict is ordinary code and must stay
                  # GREEN, exactly as the literal spellings do.
                  '_a = "modules"\ngetattr(other, _a)["tortoise.sdk"] = _Mirror\n',
                  '_k = "modules"\nother.__dict__[_k]["tortoise.sdk"] = _Mirror\n',
                  'getattr(other, "mod" + "ules")["tortoise.sdk"] = _Mirror\n',
                  '_d = "__dict__"\ngetattr(other, _d)["modules"]["tortoise.sdk"] = _Mirror\n',
                  'dict.get(_cfg, "mod" + "ules")["tortoise.sdk"] = _Mirror\n',
                  # ⛔ AND THE ROUND-24 CONTROLS: a resolved callee ALIAS on an unrelated
                  # mapping, and a plain READ of the registry (the new arm reds a REPLACEMENT,
                  # never a read).
                  '_d = dict\n_d.get(_cfg, "modules")["tortoise.sdk"] = _Mirror\n',
                  # ⛔ AND THE ROUND-29 CONTROLS. The mapping lookup resolves only a name that is
                  # provably a `builtins` MODULE one hop up, so the SAME composition on an
                  # ordinary dict or an ordinary module — code that cannot install a mirror —
                  # must stay GREEN. The last two pin the narrowing's other two clauses:
                  # `builtins["getattr"]` is a TypeError (`'module' object is not subscriptable`)
                  # so the bare subscripts are accepted for `__builtins__` NAMES only, and a key
                  # outside `_REGISTRY_BUILTINS` is not a callee this pin cares about.
                  '_cfg = {}\n_g = _cfg["getattr"]\n_g(sys, "modules")["tortoise.sdk"] = _Mirror\n',
                  '_cfg = {}\n_g = vars(_cfg)["getattr"]\n'
                  '_g(sys, "modules")["tortoise.sdk"] = _Mirror\n',
                  '_od = other.__dict__\n_g = _od["getattr"]\n'
                  '_g(sys, "modules")["tortoise.sdk"] = _Mirror\n',
                  '_o = other\n_g = getattr(_o, "getattr")\n'
                  '_g(sys, "modules")["tortoise.sdk"] = _Mirror\n',
                  '_cfg = {}\n_g = dict.get(_cfg, "getattr")\n'
                  '_g(sys, "modules")["tortoise.sdk"] = _Mirror\n',
                  'import builtins\n_g = builtins["getattr"]\n'
                  '_g(sys, "modules")["tortoise.sdk"] = _Mirror\n',
                  'import builtins\n_g = builtins.__dict__["print"]\n_g("x")\n',
                  'x = sys.modules\nprint(len(x))\n',
                  'sys.modules.get(_k)\n',
                  'dict.update(_cfg, {"tortoise.sdk": _Mirror})\n',
                  # ⛔ AND THE ROUND-25 CONTROLS. `del` is deliberately NOT an arm — deleting an
                  # entry makes the deferred import resolve the genuine class, or fail, so it
                  # cannot install a mirror — and an unpack of ORDINARY names, a loop over
                  # ordinary names, and the shipped tool's own
                  # `sys.modules[_AB_MODULE] = module` (a legitimate non-`tortoise.sdk` key)
                  # must all stay GREEN. The last one is why the REPLACES arm destructures the
                  # target instead of `ast.walk`-ing it (a deep walk reds it: `2 failed, 39
                  # passed`, measured).
                  'del sys.modules["tortoise.sdk"]\n',
                  'del sys.modules\n',
                  'sys.modules.clear()\n',
                  # ⛔ AND THE TWO HALVES OF THE IMPORT-PATH AXIS ARE EACH INERT ALONE (#14326
                  # round 29): a poisoned search path with nothing removed from the registry
                  # resolves the CACHED shipped class, and a `del` with an untouched path
                  # re-imports the genuine module. Only the COMBINATION is a lever, and the
                  # arm above requires both — so these two must stay GREEN or the clause has
                  # over-redded ordinary code.
                  'sys.path.insert(0, "/tmp/evil")\n',
                  'sys.meta_path.insert(0, _Evil())\n',
                  'sys.path[0:0] = ["/tmp/evil"]\n',
                  # ⛔ AND REMOVING A tortoise SUBMODULE IS NOT REMOVING THE PACKAGE (#14326
                  # round 30, a FALSE RED on the shipped artifact): the coarse "any tortoise*
                  # key" rule redded `tools/read_wave_latency.py`, whose `sys.modules.pop(
                  # _AB_MODULE, None)` cleans up its own `tortoise._ab_14192_old_search_engine`
                  # registration. `from tortoise.sdk import …` never resolves through it, so
                  # nothing re-imports and there is no lever. Only an ANCESTOR (`tortoise`,
                  # `tortoise.sdk`) forces the re-execution.
                  # ⛔ AND A SHORTENING OF THE PATH IS NOT A MUTATION THAT CAN INSTALL A MIRROR
                  # (#14326 round 31 P3): `remove`/`clear`/`pop` were graded as "rewrites the
                  # import path", so ordinary teardown red — while the identical `pop(0)` was
                  # GREEN, i.e. the verdict was an accident of vocabulary. Only ADDITIONS count.
                  'sys.path.remove(_REPO_ROOT)\ndel sys.modules["tortoise.sdk"]\n',
                  'sys.path.clear()\ndel sys.modules["tortoise.sdk"]\n',
                  'sys.path.pop(0)\ndel sys.modules["tortoise.sdk"]\n',
                  'sys.path.insert(0, "/tmp/evil")\nsys.modules.pop(_AB_MODULE, None)\n',
                  'sys.path.insert(0, "/tmp/evil")\ndel sys.modules[_AB_MODULE]\n',
                  'sys.modules[_AB_MODULE] = module\n',
                  # ⛔ AND THE FALSE-RED CONTROLS FOR THE WIDENED PATH AXIS (#14326 round 32):
                  # a COPY is NOT the path (`_p = sys.path[:]` / `list(sys.path)` /
                  # `copy.copy(sys.path)` all leave `path_names` empty — measured), a REBIND
                  # of the alias NAME is not a mutation of the shared list, a SHORTENING stays
                  # inert, and the same spellings on an unrelated list, an unrelated dict and
                  # an unrelated `__dict__` are ordinary code. Each must stay GREEN or the
                  # widening bought a false red, which is the expensive direction here.
                  '_p = sys.path[:]\n_p.insert(0, "/tmp/evil")\n',
                  '_p = list(sys.path)\n_p.insert(0, "/tmp/evil")\n',
                  '_p = sys.path\n_p = ["/tmp/evil"]\n',
                  # ⛔ AND THE SAME CONTROLS WITH THE OTHER HALF OF THE CLAUSE PRESENT (#14326
                  # round 32, MEASURED — without a `del` the clause cannot fire at all, so the
                  # pair above pins nothing and the rebind guard read as inert). These are the
                  # rows that red when the `Name`-rebind guard is deleted (`42 passed` ->
                  # `1 failed`) and when the `sys`-dict narrowing is widened to ANY name.
                  '_p = sys.path\n_p = ["/tmp/evil"]\ndel sys.modules["tortoise"]\n',
                  '_cfg = {}\n_cfg["path"] = ["/tmp/evil"]\n'
                  'del sys.modules["tortoise"]\n',
                  '_od = other.__dict__\n_od["path"] = ["/tmp/evil"]\n'
                  'del sys.modules["tortoise"]\n',
                  '_p = sys.path\n_p: list = ["/tmp/evil"]\n',
                  '_p = sys.path\n_p.remove("/tmp/evil")\n',
                  '_p = sys.path\n_p.pop(0)\n',
                  '_l = ["a"]\n_l[0:0] = ["/tmp/evil"]\n',
                  '_l = ["a"]\n_l.__setitem__(0, "/tmp/evil")\n',
                  '_l = ["a"]\nlist.__init__(_l, ["/tmp/evil"])\n',
                  '_cfg = {}\n_cfg["path"] = ["/tmp/evil"]\n',
                  '_od = other.__dict__\n_od["path"] = ["/tmp/evil"]\n',
                  'sys.__dict__["flags"] = 1\n',
                  'x = sys.__dict__["path"]\nprint(len(x))\n',
                  '_x["modules"] = {"tortoise.sdk": _Mirror}\n',
                  'a, b = 1, 2\nprint(a, b)\n',
                  'for _k in ("a", "b"):\n    print(_k)\n'):
        assert ctor_findings(shipped + "class _Mirror:\n    pass\n" + inert) == [], inert

    # …and the false-red control: `|=` on an ORDINARY container stores a label, not a
    # binding, so the receiver scope is what keeps this green (measured `35 passed`).
    for inert in ('_ctr = {}\n_ctr |= {"TortoiseSDK": "label"}\n',
                  'sys.modules |= {"tortoise.other": _M}\n',
                  '_copy = _t.__dict__.copy()\n_copy |= {"TortoiseSDK": _Mirror}\n',
                  # …and the element-wise `for` rule stays GREEN on ordinary loops: an
                  # ORDINARY container reached through a containing literal, a literal of
                  # unrelated NAMES, and the artifact's own tuple-of-tuples loop shape
                  # (`for label, arm in (("baseline", base), ("after", new)):` in
                  # `read_wave_latency.run`) — the loop spelling is not itself the store.
                  'for _c in ({},):\n    _c |= {"TortoiseSDK": "label"}\n',
                  'for _e in (_OTHER,):\n    _e |= {"tortoise.sdk": _Mirror}\n',
                  '_pairs = (("baseline", _base), ("after", _new))\n'
                  'for _lbl, _arm in (("baseline", _base), ("after", _new)):\n'
                  '    _arm |= {"tortoise.sdk": "label"}\n'):
        assert ctor_findings(shipped + "class _Mirror:\n    pass\n" + inert) == [], inert

    # ⛔ THE `asname` HALF IS ITS OWN CLAUSE (#14326 review P2, measured: deleting it left
    # 31 passed, because the `subclass` row above satisfies the arm's FIRST disjunct).
    # Over-block only — `_receiver_findings` still requires a bare `TortoiseSDK()` — but a
    # clause no row can red is not a guard.
    aliased_only = "from tortoise.sdk import TortoiseSDK as _R\ndef run():\n    sdk = _R()\n"
    got = ctor_findings(aliased_only)
    assert got and any("as `_R`" in g for g in got), got

    no_import = "def run(queries):\n    sdk = TortoiseSDK()\n"
    got = ctor_findings(no_import)
    assert got and "no `from tortoise.sdk import TortoiseSDK`" in got[0], got


def test_the_IMPORT_PATH_axis_residuals_are_NAMED_and_pinned_GREEN() -> None:
    """The import-path axis' DECLARED residual, measured and pinned GREEN (#14326 round 32 P3).

    ⛔ WHY THIS ROW EXISTS AT ALL. Rounds 31 and 32 both wrote that the search-path axis was
    "deliberately coarse" because the fine-grained alternative "is the unbounded class this
    file already declares as residual" — and `grep` disproves it: this file declares residuals
    for CALLEE computation and for string resolution, and nothing at all for finders or path
    shapes. The sentence was doing real work (it excused four levers, three of them live) while
    naming a declaration that did not exist. It is retracted at both sites, and what DOES remain
    open is named here instead — the CALLEE-composition class applied to the path, which is the
    same UNNUMBERABLE route the file already refuses to enumerate for the registry.

    ⛔ EACH ROW BELOW WAS MEASURED AT `f7ff126b2c5a` as a LIVE lever — `sys.__dict__.update(
    path=["/tmp/evil"] + sys.path)` followed by an ancestor `del` resolved a planted
    `tortoise/sdk.py` end-to-end. They are pinned GREEN so the note cannot drift: closing any of
    them must EDIT this table, not leave it contradicting the code. A future round that wants to
    resolve one should START from the callee-composition residual this file already owns rather
    than growing another spelling list.
    """
    shipped = ("import sys\n"
               "from tortoise.sdk import TortoiseSDK\n"
               "def run(queries):\n"
               "    sdk = TortoiseSDK()\n"
               "    return queries\n")

    def reds(lever: str) -> bool:
        return bool(_constructor_provenance_findings(ast.parse(shipped + lever)))

    # The two failures this row must NOT bless: the widened alias and mapping axes.
    assert reds('_p = sys.path\n_p[0:0] = ["/tmp/evil"]\n'
                'del sys.modules["tortoise"]\n')
    assert reds('sys.__dict__["path"] = ["/tmp/evil"] + sys.path\n'
                'del sys.modules["tortoise"]\n')

    declared_residual = {
        # the `.update(**kw)` spelling of the mapping store
        "`sys.__dict__.update(path=[...])`":
            'sys.__dict__.update(path=["/tmp/evil"] + sys.path)\n'
            'del sys.modules["tortoise"]\n',
        # a callee reached through builtins' MAPPING (the round-30 residual, this axis)
        "`builtins.__dict__[\"setattr\"](sys, \"path\", ...)`":
            'import builtins\n'
            'builtins.__dict__["setattr"](sys, "path", ["/tmp/evil"] + sys.path)\n'
            'del sys.modules["tortoise"]\n',
        # a COMPUTED path object — the same non-enumerable route the callee axis declares
        "`_p = operator.attrgetter(\"path\")(sys)` then `_p.insert`":
            'import operator\n_p = operator.attrgetter("path")(sys)\n'
            '_p.insert(0, "/tmp/evil")\n'
            'del sys.modules["tortoise"]\n',
        "`_p = (lambda o: o.path)(sys)` then `_p.insert`":
            '_p = (lambda o: o.path)(sys)\n_p.insert(0, "/tmp/evil")\n'
            'del sys.modules["tortoise"]\n',
    }
    for name, lever in declared_residual.items():
        assert not reds(lever), (
            f"{name} now RED — the declared residual in `_is_import_path_mutation` is stale; "
            "EDIT the note and this table instead of leaving them contradicting the code")


def test_the_ALIAS_axis_iterable_residuals_are_NAMED_and_pinned_GREEN() -> None:
    """The alias axis' closed set and its DECLARED residual, each pinned (#14326 round 18).

    ⛔ TWO SHAPES WERE FAIL-OPEN ONE OPERATOR OVER FROM THE ROUND-17 FIX, MEASURED at
    `7ba358467252`: `_loop_target_sources` returned a `Starred` element AS ITSELF (matching no
    shape predicate) and DROPPED a `**spread` key entirely, because `ast.Dict.keys` carries
    `None` there and the old arm filtered those out. So `for _x in (*(sys.modules,),):` and
    `for _x in {**{sys.modules: 1}}:` left the pin GREEN while `for _x in (sys.modules,):` was
    RED — the same bind, the same class, one splice over. Both are closed above (the splices
    are expanded), and the first block below is what reds if either arm regresses.

    ⛔ AND THE RESIDUAL IS NAMED RATHER THAN IMPLIED. An iterable whose elements this does not
    expand binds the CONTAINER expression, so the SHAPE predicates (`_is_module_registry`,
    `_is_namespace_expr`, `_is_setattr_like`) are blind to it — the same over-approximation
    the axis already makes for a NAME iterable, not a new boundary, but a real one, and the
    second block pins it GREEN so the note cannot drift. Closing any of them (e.g. widening a
    shape predicate to walk a container, as `_mentions_mapping` already does) must EDIT this
    table, not leave it contradicting the code.
    """
    shipped = ("from tortoise.sdk import TortoiseSDK\n"
               "def run(queries):\n"
               "    sdk = TortoiseSDK()\n")

    def reds(lever: str) -> bool:
        return bool(_constructor_provenance_findings(ast.parse(shipped + lever)))

    closed = {
        "tuple literal": 'for _x in (sys.modules,):\n    _x |= {"tortoise.sdk": _Mirror}\n',
        "list literal": 'for _x in [sys.modules]:\n    _x |= {"tortoise.sdk": _Mirror}\n',
        "set literal": 'for _x in {sys.modules}:\n    _x |= {"tortoise.sdk": _Mirror}\n',
        "dict literal (keys)":
            'for _x in {sys.modules: 1}:\n    _x |= {"tortoise.sdk": _Mirror}\n',
        "STARRED literal":
            'for _x in (*(sys.modules,),):\n    _x |= {"tortoise.sdk": _Mirror}\n',
        "`**` dict spread":
            'for _x in {**{sys.modules: 1}}:\n    _x |= {"tortoise.sdk": _Mirror}\n',
        "STARRED NAME (the alias fixpoint, not the literal arm)":
            '_R = sys.modules\nfor _x in (*_R,):\n    _x |= {"tortoise.sdk": _Mirror}\n',
        # ⛔ THE `else` HALVES ARE CLAUSES TOO (#14326 round 19, MEASURED: replacing
        # `expanded.append(element.value)` with `element`, or deleting `keys.append(value)`,
        # left the whole file at 41 passed while `(*sys.modules,)` / `{**sys.modules}` flipped
        # RED -> GREEN). The `STARRED NAME` row above cannot red the first half —
        # `_names_in(Starred)` still sees `_R` — so each half needs its own row.
        "STARRED registry ATTRIBUTE":
            'for _x in (*sys.modules,):\n    _x |= {"tortoise.sdk": _Mirror}\n',
        "`**` spread of a registry ATTRIBUTE":
            'for _x in {**sys.modules}:\n    _x |= {"tortoise.sdk": _Mirror}\n',
        # ⛔ `async with` IS THE SAME ARM ONE NODE TYPE OVER, AND IT WAS UNPINNED TOO (#14326
        # round 21, MEASURED: dropping `ast.AsyncWith` from `_aliases_of`'s With arm left the
        # file at **41 passed** while `async with sys.modules as _x: _x |= {…}` lost its
        # finding entirely). The `with` row above cannot carry it — the harness here is a
        # module-level statement list, where `async with` is a SyntaxError — so the lever
        # supplies its own `async def`.
        "import registry via an `async with` TARGET":
            'async def _a():\n    async with sys.modules as _x:\n'
            '        _x |= {"tortoise.sdk": _Mirror}\n',
    }
    for name, lever in closed.items():
        assert reds(lever), (name, lever)

    residual = {
        "IfExp over literals":
            'for _x in ([sys.modules] if queries else []):\n'
            '    _x |= {"tortoise.sdk": _Mirror}\n',
        "BoolOp over literals":
            'for _x in ([sys.modules] or []):\n    _x |= {"tortoise.sdk": _Mirror}\n',
        "BinOp concat":
            'for _x in ([sys.modules] + []):\n    _x |= {"tortoise.sdk": _Mirror}\n',
        "list comprehension":
            'for _x in [g for g in (sys.modules,)]:\n    _x |= {"tortoise.sdk": _Mirror}\n',
        "generator expression":
            'for _x in (g for g in (sys.modules,)):\n    _x |= {"tortoise.sdk": _Mirror}\n',
        "call": 'for _x in list((sys.modules,)):\n    _x |= {"tortoise.sdk": _Mirror}\n',
        "subscript":
            'for _x in ((sys.modules,),)[0]:\n    _x |= {"tortoise.sdk": _Mirror}\n',
        # ⛔ THE NESTED LITERAL IS A RESIDUAL TOO, AND THIS TABLE DID NOT NAME IT (#14326
        # round 19 P3, MEASURED at `57735ab47947`: GREEN here, `RED` one nesting shallower).
        # Only a `*`/`**` element is spliced; a PLAIN nested container is one element, so the
        # target binds the inner tuple. ⛔ NOT "harmless": this comment used to claim `|=` on a
        # tuple raises `TypeError` so the shape "cannot be a working mirror lever" — MEASURED
        # FALSE (#14326 round 30 P2): `_x[0][...] = _Mirror` raises nothing at all, and
        # `_x[0] |= {…}` mutates the inner dict before the raise. UNNUMBERABLE, not inert.
        "nested literal (only a STARRED element is spliced)":
            'for _x in ((sys.modules,),):\n    _x |= {"tortoise.sdk": _Mirror}\n',
    }
    for name, lever in residual.items():
        assert not reds(lever), ("declared residual went CLOSED without this table being "
                                 "edited", name, lever)


def test_every_door_contract_clause_reds() -> None:
    """Each fail-closed clause of `_door_read_contract` must be RED by a row (#14326 P1).

    Measured before this test: each of the four clauses — `len(door_calls) == 1`,
    `{"_rerank_candidates", …} <= bool_levers`, `not named_levers`, and the equality
    against `_EXPECTED_DOOR_ARGS` — replaced by `True` in turn left **27 passed**, because
    nothing in the file ever mutated `hosted_api.py` or `sdk.py`. Each row below mutates the
    REAL source text, so deleting the clause makes the row fail by NOT raising.
    """
    hosted = _HOSTED.read_text()
    sdk = _SDK.read_text()

    # Control / non-vacuity: the real sources satisfy the contract, and it carries the
    # door's four arguments plus this harness's out-sink.
    assert _door_read_contract() == set(_EXPECTED_DOOR_ARGS) | {"leg_trace"}

    def raised(needle: str, *, hosted_source: str | None = None,
               sdk_source: str | None = None) -> str:
        with pytest.raises(AssertionError) as exc:
            _door_read_contract(hosted_source=hosted_source, sdk_source=sdk_source)
        message = str(exc.value)
        assert needle in message, (needle, message)
        return message

    anchor = "_truncation_out=trunc_out),"
    assert hosted.count(anchor) == 1

    # 1. the door itself names a BOOL lever -> `not named_levers`
    raised("the door itself already names",
           hosted_source=hosted.replace(
               anchor, "_truncation_out=trunc_out, _rerank_candidates=False),", 1))

    # 2. the door names a lever the bool-lever intersection cannot see -> the equality
    raised("re-derive this contract and the harness's",
           hosted_source=hosted.replace(anchor, "_truncation_out=trunc_out, "
                                               'kind="statement"),', 1))

    # 3. a second read call inside `search` (a hoisted `_do_search`, an added arm) -> the
    #    cardinality clause, which is what stops the allow-list being a UNION
    raised("read calls — this contract is derived from exactly one",
           hosted_source=hosted.replace(
               anchor,
               "_truncation_out=trunc_out),\n"
               "                lambda: sdk._tortoise_fts_query(q, limit=limit),", 1))

    # 4. `_rerank_candidates` stops being a bool-defaulted parameter -> the bool-lever seed,
    #    which every lever clause above is an intersection against
    assert sdk.count("_rerank_candidates: bool = True") == 1
    raised("", sdk_source=sdk.replace("_rerank_candidates: bool = True",
                                      "_rerank_candidates: bool | None = None", 1))

    # 5. a SECOND `_tortoise_fts_query` definition in `sdk.py` (#14326 review P3, measured:
    #    replacing the cardinality assert with `assert defs` left 31 passed). Two
    #    definitions mean the door's parameter list may be derived from the wrong one —
    #    the constructor half of the same "exactly one authority" rule.
    second_def = ('\n\ndef _tortoise_fts_query(self, query=None, limit=10):\n'
                  '    return []\n')
    raised("_tortoise_fts_query` definitions",
           sdk_source=sdk + second_def)


def test_the_artifact_generator_filter_clauses_red() -> None:
    """The `order`/`legs` filter clauses must be RED by their own rows (#14326 P1/P2).

    Measured before this row existed: `assert not gen.ifs` (order) and the `legs` filter
    shape both replaced by `True`/`pass` left **27 passed**.
    """
    def findings(key: str, item: str) -> list[str]:
        order = item if key == "order" else "[_result_id(r) for r in results]"
        legs = item if key == "legs" else '[e for e in trace if e.get("leg")]'
        source = ("def run(results, trace):\n"
                  "    per_query = []\n"
                  f'    per_query.append({{"order": {order}, "legs": {legs}}})\n')
        run_def = next(n for n in ast.walk(ast.parse(source))
                       if isinstance(n, ast.FunctionDef) and n.name == "run")
        entries, _append = _artifact_entries(run_def)
        value = entries[key]
        assert isinstance(value, ast.ListComp), value
        return _generator_filter_findings(key, value.generators[0])

    assert findings("order", "[_result_id(r) for r in results]") == []
    assert findings("legs", '[e for e in trace if e.get("leg")]') == []

    got = findings("order", "[_result_id(r) for r in results if r]")
    assert got and "FILTERS the measured page" in got[0], got

    got = findings("legs", "[e for e in trace]")
    assert got and "0 filter clauses" in got[0], got

    got = findings("legs", '[e for e in trace if e.get("leg") and True]')
    assert got and "not a `.get(...)` CALL" in got[0], got

    got = findings("legs", '[e for e in trace if e.get("kind")]')
    assert got and "filters on" in got[0], got

    # The `legs` SHAPE is four further sub-clauses, each measured UN-RED by any row at
    # `4ed436353f` (replacing it with `True` left **31 passed**). Each row below asserts the
    # message of ITS OWN clause, so deleting that clause makes exactly this row fail.
    got = findings("legs", "[e for e in trace if e.legs(\"leg\")]")
    assert got and "not a `.get(...)` CALL" in got[0], got

    got = findings("legs", '[e for e in trace if e.get("leg", default=0)]')
    assert got and "keyword argument(s)" in got[0], got

    got = findings("legs", '[e for e in trace if e.get("leg", 0)]')
    assert got and "positional argument(s)" in got[0], got

    got = findings("legs", '[e for e in trace if probe.get("leg")]')
    assert got and "not read off the iterated entry" in got[0], got


def test_the_CODE_EXECUTION_axis_residuals_are_NAMED_and_pinned_GREEN() -> None:
    """The code-execution axis' DECLARED residual, measured and pinned GREEN (#14326 cycle 3).

    ⛔ WHY THIS EXISTS AT ALL. The arm that fail-closes `exec`/`eval`/`compile` RESOLVES the
    callee through `_aliases_of` and `_builtin_lookup_name`, and three review cycles found one
    more launder each time: the bare name, then `_run = exec`, then `_d = builtins.__dict__` /
    `from builtins import exec as _e` / `_g = getattr`. Those are closed. What is NOT closed is
    a callee obtained by a route that is not statically enumerable at all — the same class this
    file already states for the registry axis (#14326 round 28/33: "the list of ways to name a
    callable does not terminate"). Leaving that unnamed is the defect the round-33 review found
    in the import-path axis: a sentence excusing a lever while naming a declaration that did not
    exist.

    ⛔ EACH ROW BELOW WAS MEASURED AS A LIVE LEVER at `7b1589ef9e` — a third reach executing the
    product read while the pin passed (`m=2 c=2 i=0`). They are pinned GREEN so the note cannot
    drift: resolving one must EDIT this table, not leave it contradicting the code. A future
    round that wants to close one should start from the callee-composition residual this file
    already owns rather than growing another spelling list.
    """
    read = 'sdk._tortoise_fts_query(query=q)'

    def body(*lines: str) -> str:
        return "".join("    " + line + "\n" for line in lines)

    def refused(*lines: str) -> bool:
        return _pin_refuses(_pin_source(body(*lines)))

    declared_residual = {
        # a callee COMPOSED at the call site — `operator.attrgetter` returns the attribute
        "`operator.attrgetter(\"exec\")(builtins)`": (
            'import builtins, operator',
            f'operator.attrgetter("exec")(builtins)("results = {read}")'),
        # the same, bound first
        "`functools.partial(exec)`": (
            'import functools',
            f'functools.partial(exec)("results = {read}")'),
        # a lambda is a callee with no name at all
        "`(lambda f: f)(exec)`": (
            f'(lambda f: f)(exec)("results = {read}")',),
        # the module reached THROUGH the import registry, not through a root name
        "`getattr(sys.modules[\"builtins\"], \"exec\")`": (
            'import sys',
            f'getattr(sys.modules["builtins"], "exec")("results = {read}")'),
        # …and through a CALL — `__import__` is not graded as code (its argument is a module)
        "`__import__(\"builtins\").__dict__[\"exec\"]`": (
            f'__import__("builtins").__dict__["exec"]("results = {read}")',),
        # ⛔ AND THE UNENUMERABLE COPY-CONSTRUCTOR CLASS (#14326 round 35 review cycle 4,
        # EACH MEASURED GREEN with a third reach executing the read). The SHAPE-detectable
        # copies ARE closed — `dict(m)`, `{**m}`, `m | {}`, `m.copy()`, an alias of `dict`.
        # What does not close is a copy produced by an arbitrary MAPPING CONSTRUCTOR: the copy
        # still holds the SAME function objects, so it is a live lever, but "the ways to make a
        # mapping from a mapping" terminates no more than "the ways to name a callable" does.
        # Same DECLARED class as the rows above, not a spelling list to grow.
        "`types.MappingProxyType(builtins.__dict__)`": (
            'import builtins, types', '_d = types.MappingProxyType(builtins.__dict__)',
            f'_d["exec"]("results = {read}")'),
        "`_c = builtins.__dict__.copy; _c()`": (
            'import builtins', '_c = builtins.__dict__.copy', '_d = _c()',
            f'_d["exec"]("results = {read}")'),
        # ⛔ AND A SELECTION OUT OF A LITERAL CONTAINER (#14326 round 35 review cycle 5,
        # MEASURED GREEN: `[builtins.__dict__][0]["exec"](<read>)`). Not the callee-composition
        # class and not an arbitrary constructor — it is a mapping reached by indexing a
        # LITERAL. The STRING axis resolves exactly this shape (`_subscript_strings`, round 16);
        # the mapping predicate does not, so it is DECLARED here rather than left silent. The
        # same sentence bounds it: a selection out of a NAME-held container is residual, and so
        # is one out of a literal until this predicate learns that resolution.
        "`[builtins.__dict__][0][\"exec\"]`": (
            'import builtins', '_d = [builtins.__dict__][0]',
            f'_d["exec"]("results = {read}")'),
        "`(builtins.__dict__,)[0][\"exec\"]`": (
            'import builtins', '_d = (builtins.__dict__,)[0]',
            f'_d["exec"]("results = {read}")'),
    }
    for label, lines in declared_residual.items():
        assert not refused(*lines), (
            f"[{label}] is now REFUSED — this row pins it GREEN as the DECLARED callee-"
            "composition residual, so closing it must edit this table rather than leave the "
            "note in `_indirect_reads` contradicting the code")

    # ⛔ THE OTHER DIRECTION, so the table cannot be satisfied by an arm that reds everything:
    # each RESOLVED spelling of the same callee must be RED. These mirror rows in
    # `test_EVERY_static_binding_form_is_a_READ_reach`, asserted here at the boundary.
    for label, lines in {
        "bare `exec`": (f'exec("results = {read}")',),
        "`_run = exec`": ('_run = exec', f'_run("results = {read}")'),
        '`getattr(builtins, "exec")`': (
            'import builtins',
            f'getattr(builtins, "exec")("results = {read}")'),
        '`_d = builtins.__dict__; _d["exec"]`': (
            'import builtins', '_d = builtins.__dict__', f'_d["exec"]("results = {read}")'),
        "`from builtins import exec as _e`": (
            'from builtins import exec as _e', f'_e("results = {read}")'),
        # `copy.copy(m)` was declared residual here until the UNBOUND `.copy` arm closed it
        # (#14326 round 35 review cycle 5): `copy.copy(m)` and `dict.copy(m)` are the same
        # one-argument call shape, so closing one closed both. It moves to the REFUSED set.
        "`copy.copy(builtins.__dict__)`": (
            'import builtins, copy', '_d = copy.copy(builtins.__dict__)',
            f'_d["exec"]("results = {read}")'),
    }.items():
        assert refused(*lines), (
            f"[{label}] is NOT refused — it is a statically visible spelling of the callee and "
            "belongs in the refused set, not in the residual table above")
