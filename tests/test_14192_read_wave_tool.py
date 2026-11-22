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
    `_N + "_x"`, a parameter), a value read out of a container or an attribute
    (`_CFG["read"]`, `self._read`) and an unpack whose RIGHT-HAND SIDE is an attribute or a
    call (`_N, _M = cfg.pair`, `_a, _b = pair()`) are not resolved here — nothing about them
    is statically pairable, and they are named rather than implied (#14326 review P3).

    ⛔ AN f-STRING WHOSE **CONSTANT FRAGMENT IS THE READ NAME** IS OVER-APPROXIMATED, AND AN
    f-STRING **ASSEMBLED FROM PARTS** IS RESIDUAL — two different classes, and both earlier
    wordings got one of them wrong (#14326 rounds 5 and 8, MEASURED at `6f66ac53` and
    `9762589e`). The `JoinedStr` arms feed every part to the resolver, so a `FormattedValue`
    contributes nothing while a CONSTANT fragment IS resolved: `_N = f"{p}_tortoise_fts_query"`
    yields `{'_tortoise_fts_query'}`, which REDS when the name is executed (`indirect=1`) and
    stays GREEN as an inert label (`_lbl = f"{p}_tortoise_fts_query"; logging.info(_lbl)`,
    measured `refuses=False`, because the `Name` arm requires an executable position).

    ⛔ BUT AN f-STRING WHOSE TEXT IS ASSEMBLED IS **NOT** CAUGHT, AND SAYING OTHERWISE WAS THE
    DEFECT (#14326 round 8, MEASURED): `_p = "_tortoise"; _N = f"{_p}_fts_query"` builds
    exactly `_tortoise_fts_query` at runtime and the pin stays GREEN (`refuses=False`,
    mentions 2 / calls 2 / indirect 0) — the constant fragments are `"_tortoise"` and
    `"_fts_query"`, neither of which is the name. That is the declared residual, together
    with `"".join(…)`, `_N + "_x"` and a parameter; the fragment over-approximation buys ONE
    spelling at the cost of a possible over-red, it does not close the class. Recorded so the
    next lane does not read the arm above as covering assembled names.

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
    binds: list[tuple[ast.AST, ast.AST]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            binds.extend((target, node.value) for target in node.targets)
        elif isinstance(node, (ast.AnnAssign, ast.NamedExpr)) and node.value is not None:
            binds.append((node.target, node.value))
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
            return set().union(*(_values(part) for part in value.values))
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

    def _bind(target: ast.AST, value: ast.AST) -> bool:
        """Add every possible value of `value` to `target`; True when the map GREW."""
        if isinstance(target, ast.Name):
            have = out.setdefault(target.id, set())
            before = len(have)
            have |= _values(value)
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
                    grew = len(have) != before or grew
            return grew
        return False

    changed = True
    while changed:  # fixed point: a chain of any length resolves in one call
        changed = False
        for target, value in binds:
            if _bind(target, value):
                changed = True
    return {name: frozenset(values) for name, values in out.items() if values}


def _parents_of(tree: ast.AST) -> dict[int, ast.AST]:
    parents: dict[int, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[id(child)] = node
    return parents


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
        return frozenset().union(*(_possible_strings(key, consts)
                                   for key in node.keys if key is not None))
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
        return frozenset().union(*(_possible_strings(part, consts)
                                   for part in node.values))
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


def _is_namespace_expr(node: ast.AST) -> bool:
    """True for an expression naming a LIVE namespace MAPPING — `globals()`/`locals()`/
    `vars(x)`/`x.__dict__`.

    Used only to SCOPE the string-key rebind below: a store into one of these binds a
    module-level name, while a store into an ordinary container binds nothing.
    """
    if isinstance(node, ast.Attribute):
        return node.attr == "__dict__"
    return (isinstance(node, ast.Call)
            and getattr(node.func, "id", None) in _NAMESPACE_CALLS)


def _namespace_rebind_names(node: ast.AST) -> set[str]:
    """Callee NAMES bound through a STRING KEY into a namespace — fail-closed, by name.

    ⛔ THE STRING KEY IS THE AXIS `_bind_target_names` NEVER LEARNED (#14326 review,
    EACH MEASURED GREEN at `c380075b`: `m=2 c=2 i=0` with a third reach executing the read):

        setattr(builtins, "print", getattr)      # or globals()["print"] = getattr
        print(sdk, "_tortoise_fts_query")(…, kind="statement")

    Round 7 taught `_bind_target_names` to take an ATTRIBUTE SEGMENT (`_mod.print = …`),
    which closed half the hole the name allow-lists opened. It never learned the two
    spellings that carry the SAME name as a CONSTANT: a `setattr` name argument, and a
    string-keyed store into the namespace mapping itself. Both launder an allow-listed
    name into `getattr` exactly as the attribute form does — so the pin stayed GREEN while
    the `intentional_keep` grant for `run()` stood on a pin that could not go red.

    ⛔ SCOPED TO A NAMESPACE RECEIVER ON PURPOSE. A store into an ORDINARY container
    (`_counts["write"] = 0`) binds no callee; dropping the allow-listed name `write` for it
    would be a FALSE RED, so the control beside the rows pins that it stays green.
    """
    if isinstance(node, ast.Call):
        # `setattr(<obj>, "<name>", …)` — the attribute form, spelled as a call.
        if getattr(node.func, "id", None) == "setattr" and len(node.args) >= 2:
            key = node.args[1]
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                return {key.value}
        # `<ns>.__dict__.update({"<name>": …})` binds the mapping's KEYS.
        if (getattr(node.func, "attr", None) == "update"
                and isinstance(node.func.value, ast.Attribute)
                and node.func.value.attr == "__dict__"
                and node.args and isinstance(node.args[0], ast.Dict)):
            return {k.value for k in node.args[0].keys
                    if isinstance(k, ast.Constant) and isinstance(k.value, str)}
        return set()
    if (isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store)
            and _is_namespace_expr(node.value)):
        key = node.slice
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            return {key.value}
    return set()


def _rebound_callee_names(tree: ast.AST) -> frozenset[str]:
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
            out |= _namespace_rebind_names(node)
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


def _is_read_ref(node: ast.AST, parents: dict[int, ast.AST],
                 consts: dict[str, frozenset[str]],
                 rebound: frozenset[str] = frozenset()) -> bool:
    """True when `node` is a REACH on the read — a mention the census must account for."""
    if isinstance(node, ast.Attribute) and node.attr in _READ_NAMES:
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
    """The operands of `call` that are NOT the object being read.

    Everything after the first positional, plus EVERY splat: a splat's own arity and
    position are not statically knowable here, so it is fail-closed — `getattr(*_pair)`, a
    one-element positional list, still inspects `_pair` (#14279 review F3).
    """
    # ⛔ THE LITERAL-SPLAT EXPANSION IS GONE (#14326 review P3): `_splat_operands`
    # expanded a `Starred` over a literal one level, and NO mutant could red those two
    # lines — the Starred itself is in this list, and `_possible_strings` already resolves
    # THROUGH a `Starred` into its literal container (measured: deleting the expansion
    # leaves `getattr(*[sdk, "…"])` red). A clause no mutant can red reads to the next lane
    # as an independent check; this file deletes them instead.
    # ⛔ `[1:]`, AND IT IS UNPINNED (#14326 round 8, measured BOTH ways: widening to
    # `list(call.args)` leaves 25 passed, and so does the slice) — because a literal in the
    # FIRST position is already a mention through the `Constant` arm, so the slice only ever
    # subtracts an operand the census has already counted. Kept as the narrower statement of
    # intent (`getattr`'s object position is not a name), with the measurement recorded so the
    # next lane does not read the exclusion as a guard.
    operands = list(call.args)[1:]
    operands.extend(arg for arg in call.args if isinstance(arg, ast.Starred))
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
    for n in ast.walk(tree):
        if not isinstance(n, ast.Call):
            continue
        _callee = getattr(n.func, "id", None) or getattr(n.func, "attr", None)
        # A pure or RECORDING callee cannot fetch by name — the `print("…")` control —
        # unless the source REBOUND that name (#14326 round 6).
        if (_callee in _PURE_READS or _callee in _RECORDING_CALLEES) \
                and _callee not in rebound:
            continue
        if not any(_carries_read_name(arg, consts)
                   for arg in _name_reaching_operands(n)):
            continue
        # ⛔ TWO DELIVERY SHAPES, BOTH PINNED (#14326 round 5, corrected by the merge with
        # `8d221fab93`). A SPLAT's arity and position are not statically knowable, so the
        # fetcher behind it cannot be excluded by name — fail-closed on ANY non-pure callee.
        # The NAMED pair carries the non-splat container delivery, whose operand is not a
        # `Name` and therefore produces no mention the census can see:
        #
        #     getattr(sdk, f"_tortoise_fts_query")(…)          # JoinedStr, not a Name
        #     getattr(sdk, ("_tortoise_fts_query" if q else "x"))(…)
        #
        # ⛔ MEASURED HISTORY, because it looks removable and is not: at `4044c287` emptying
        # this set left all 25 tests green (every shape it fired on was already refused by
        # the mention census), so it was deleted; the merge with `8d221fab93` added the
        # f-string / `IfExp` / `BoolOp` rows whose inline delivery ONLY reaches the pin
        # through this branch, and emptying the set now reds
        # `test_EVERY_static_binding_form_is_a_READ_reach` — which is what makes it
        # load-bearing rather than decorative.
        # ⛔ A SPLAT OR A KEYWORD IS THE CONTAINER DELIVERY (#14326 round 5, round 6). A
        # SPLAT's arity and position are not statically knowable, so the fetcher behind it
        # cannot be excluded by name. A KEYWORD delivers by name too (`_PageFetcher(name="…")`,
        # `**{"name": "…"}`), which is why `_name_reaching_operands` carries keywords.
        if (_callee in _NAMEFETCH_CALLEES
                or n.keywords
                or any(isinstance(arg, ast.Starred) for arg in n.args)):
            out.append(n)
    return out


#: Callees that can turn a STRING argument into an attribute read by that name. This pair
#: carries the NON-SPLAT container delivery, which the mention census cannot see
#: (`getattr(sdk, f"_tortoise_fts_query")` — a `JoinedStr`, not a `Name`). It is pinned by
#: the f-string / `IfExp` / `BoolOp` rows: emptying it reds
#: `test_EVERY_static_binding_form_is_a_READ_reach`. Every OTHER fetcher name tried
#: (`__getattribute__`, `attrgetter`, `methodcaller`, `get`, `__getitem__`, `__call__`) was
#: measured DEAD — those escapes all deliver through a SPLAT, which the second arm refuses
#: under any callee name — so they are deleted rather than kept as unreachable entries.
_NAMEFETCH_CALLEES = frozenset({"getattr", "setattr"})


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
    rebound = _rebound_callee_names(tree)
    mentions = [n for n in ast.walk(tree) if _is_read_ref(n, parents, consts, rebound)]
    return mentions, _read_calls(tree), _indirect_reads(tree, consts, rebound)


#: The kwargs `tortoise/hosted_api.py::search` hands the read, as of this pin. A LITERAL
#: on purpose: `_door_read_contract()` derives the same set from the door, and this is the
#: independent statement that reds when the DOOR moves — a derivation checked only against
#: itself cannot tell "unchanged" from "widened".
_EXPECTED_DOOR_ARGS = frozenset({"query", "limit", "read_status_out", "_truncation_out"})


def _door_read_contract() -> set[str]:
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
    """
    sdk_tree = ast.parse(_SDK.read_text())
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

    hosted_tree = ast.parse(_HOSTED.read_text())
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


def _builds_new_container(value: ast.AST) -> bool:
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
    """
    if isinstance(value, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
        return True
    return (isinstance(value, ast.Call)
            and (getattr(value.func, "id", None) or getattr(value.func, "attr", None))
            in _SHALLOW_COPY_CALLEES)


def _aliases_of(tree: ast.AST, predicate, loops: bool = False,
                copy_is_alias: bool = False) -> set[str]:
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
    target does. `comprehension` is deliberately NOT included: the artifact's own
    `[_result_id(r) for r in results]` hands each ELEMENT to a call, and treating a
    comprehension target as an alias of the collection would red the tool the pin exists
    to protect (measured before adding the arm).
    """
    binds: list[tuple[ast.AST, ast.AST]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets: list[ast.AST] = list(node.targets)
            value: ast.AST = node.value
        elif isinstance(node, (ast.AnnAssign, ast.NamedExpr)) and node.value is not None:
            targets, value = [node.target], node.value
        elif loops and isinstance(node, (ast.For, ast.AsyncFor)):
            targets, value = [node.target], node.iter
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
            # `_builds_new_container` above. `copy_is_alias=True` collects the copies.
            resolves = (copy_is_alias or not _builds_new_container(value)) and (
                predicate(value)
                or bool(_names_in(value) & out)
                or any(predicate(operand)
                       or (isinstance(operand, ast.Name) and operand.id in out)
                       or bool(_names_in(operand) & out)
                       for operand in _collection_operands(value))
            )
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
                   artifact_append: ast.Call) -> list[int]:
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
    aliases = _aliases_of(run_def, _collection_alias_predicate(var), loops=True)
    watched = {var} | aliases
    # ⛔ THE RECORDED ROWS ARE A COLLECTION TOO: `_row = per_query[-1]` then
    # `_row["order"] = _row["order"][:5]` truncated the page the verdict reads, with
    # every `"per_query" in receiver` clause passing (measured). Resolve names bound to
    # ANY expression mentioning `per_query`.
    scalars = _scalar_bound_names(run_def)
    rows = ({"per_query"} | _aliases_of(
        run_def, lambda v: "per_query" in _names_in(v), loops=True)) - scalars
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
    }
    for label, body in rows.items():
        assert _pin_refuses(_pin_source(body)), (
            f"[{label}] a third reach executes the product read with `kind=\"statement\"` and "
            "the pin is GREEN on it — every static binding of the literal must resolve, or "
            "the `intentional_keep` grant for `run()` stands on a pin that cannot go red")

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
                       '    _LOG.write("_tortoise_fts_query")\n')
    assert not _pin_refuses(_pin_source(container_store)), (
        "a string-keyed store into an ordinary CONTAINER is not a callee rebind — treating "
        "it as one would drop `write`/`print` from the allow-lists and RED the recording "
        "direction; only a NAMESPACE receiver carries a module-level bind")

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
    to a local (``row = {…}; per_query.append(row)``), and ``per_query.extend([{…}])``.
    A red there costs an edit to this pin, which is the cheap direction — the expensive
    direction is a sorted page reaching the verdict as ``rank_identical: True`` (measured
    green against earlier shapes of this check).

    ⛔ EVERY STATIC REACH IS CENSUSED, not just the two calls: a bound-method alias
    (``_read = sdk._tortoise_fts_query``), a string LITERAL naming the read
    (``sdk.__getattribute__("_tortoise_fts_query")``, ``operator.methodcaller(...)``,
    ``vars(type(sdk))[...]``), a name BOUND to that literal, and a bare attribute
    mention with no call all left earlier shapes of this pin green while carrying a
    lever (each measured). Residual, stated rather than implied: a name COMPUTED at
    runtime (``"".join(…)``, an f-string, a parameter) or READ OUT OF a container or an
    attribute (``_CFG["read"]``, ``self._read``) cannot be resolved statically, so this pin
    bounds the static reaches. ⛔ REACHES THROUGH A CONTAINER ARE **COVERED**, not residual
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
    for call in calls:
        assert not call.args, (
            f"line {call.lineno} hands the read {len(call.args)} POSITIONAL argument(s) — "
            "this pin reads keywords, so a positional `kind`/`limit` would be invisible")
        assert not any(k.arg is None for k in call.keywords), (
            f"line {call.lineno} splats a mapping into the read, so which arguments it "
            "carries is invisible to this pin")
        named = {k.arg for k in call.keywords}
        extra = sorted(named - allowed)
        assert not extra, (
            f"line {call.lineno} hands {extra} to the read — the `/v1/search` door names "
            f"only {sorted(allowed - {'leg_trace'})}, and each of these changes WHICH rows "
            "survive `limit`, so a mirror that names one times a reader the product does "
            "not run (and hides the page permutation the verdict exists to catch)")
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
        if key == "order":
            # The verdict's input is the WHOLE page: a filter clause reports a subset while
            # the pinned contract ("iterated AS ITSELF") reads satisfied (measured green).
            assert not gen.ifs, (
                f"the artifact's `order` FILTERS the measured page "
                f"(`if {ast.unparse(gen.ifs[0])}`) — the rank-identity verdict would compare "
                "a subset of the page")
        else:
            # The trace is filtered by the tool's own documented rule — "only rows that
            # carry a `leg`" — and that rule is pinned as a SHAPE, so an added condition
            # (`and not e.get("slow")`) cannot quietly drop rows from the table.
            assert len(gen.ifs) == 1, (
                f"the artifact's `legs` has {len(gen.ifs)} filter clauses — the tool's rule "
                "is `if <entry>.get(\"leg\")` and nothing else")
            cond = gen.ifs[0]
            assert (isinstance(cond, ast.Call) and getattr(cond.func, "attr", None) == "get"
                    and len(cond.args) == 1 and not cond.keywords
                    and isinstance(cond.args[0], ast.Constant)
                    and cond.args[0].value == "leg"
                    and _names_in(cond.func.value) & _names_in(gen.target)), (
                f"the artifact's `legs` filters on {ast.unparse(cond)} — the tool's rule is "
                f"`{ast.unparse(gen.target)}.get(\"leg\")`, which drops exactly the trace "
                "entries that record no leg")
        assert _is_exactly(gen.iter, var), (
            f"the artifact's `{key}` iterates {ast.unparse(gen.iter)}, not `{var}` — the "
            f"measured read's {what} must be iterated AS ITSELF (a helper call there cannot "
            f"be proven not to sort/drop it): {ast.unparse(value)}")
        # ⛔ A MENTION IS NOT A USE: `_DUMMY if r else _DUMMY` names `r` and records a
        # constant page (measured green). For a conditional element only the VALUE branch
        # counts, so the target cannot sit purely in the condition.
        _used = _names_in(value.elt.body) if isinstance(value.elt, ast.IfExp) \
            else _names_in(value.elt)
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
            # ⛔ MOVED INSIDE THE `legs` BRANCH, because for `order` it can never be the
            # failing row (#14281 review P3, measured: it fires only with `_ok` neutered):
            # `_ok` above already implies the element names the target. A clause that can
            # never red reads to the next lane as an independent guard and is not one — the
            # reason two other clauses were deleted earlier in this file.
            assert _used & _names_in(gen.target), (
                f"the artifact's `{key}` does not derive its element from the loop target "
                f"`{ast.unparse(gen.target)}`: {ast.unparse(value.elt)}")
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
        mutations = _mutated_after(run_defs[0], var, holder.lineno, artifact_append)
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
        "passes: measured GREEN with `rank_identical: True` on a genuinely permuted page")
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
