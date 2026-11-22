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


def _str_constants(tree: ast.AST) -> dict[str, str]:
    """Every name bound to a string LITERAL anywhere in the file, module or local scope."""
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)):
            out.setdefault(node.targets[0].id, node.value.value)
    return out


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

_NON_MUTATING = {"list", "tuple", "len", "sorted", "id", "str", "bool", "any", "all",
                 "isinstance"}

#: Callees that only READ their argument, so a name handed to one is not handed to a
#: mutation. `print(f"nhits={len(results)}")` is the natural next edit to `run()`'s
#: existing per-query print line, and it mutates nothing.
_PURE_READS = _NON_MUTATING | {"repr", "int", "float", "type", "min", "max", "sum",
                               "format", "round", "abs", "set", "dict", "frozenset",
                               # Diagnostics: `print(results)` is the natural next edit to
                               # `run()`'s existing per-query print line and mutates nothing.
                               "print"}


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


def _aliases_of(tree: ast.AST, predicate) -> set[str]:
    """Names bound to a matching expression, TRANSITIVELY — `a = results`, `b = a`,
    `(c := results)`, `env = os.environ`, `env2 = env`.

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
    """
    binds: list[tuple[ast.AST, ast.AST]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets: list[ast.AST] = list(node.targets)
        elif isinstance(node, (ast.AnnAssign, ast.NamedExpr)) and node.value is not None:
            targets = [node.target]
        else:
            continue
        for target in targets:
            binds.append((target, node.value))

    out: set[str] = set()

    def _bind(target: ast.AST, value: ast.AST) -> bool:
        """Resolve one (target, value) pair; True when it named something new."""
        if isinstance(target, ast.Name):
            if target.id in out:
                return False
            # ⛔ A MENTION, not only a bare `Name`: `_box = [results]` and
            # `_row = per_query[-1]` are aliases of the collection inside them, and the
            # bare-Name form left `_box[0].sort()` / `_row["order"] = …` green (measured).
            resolves = (
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
            for target_elt, value_elt in zip(target.elts, value.elts, strict=True):
                grew = _bind(target_elt, value_elt) or grew
            return grew
        return False

    changed = True
    while changed:  # fixed point: a chain of any length resolves in one call
        changed = False
        for target, value in binds:
            if _bind(target, value):
                changed = True
    return out


_MUTATORS = {"append", "clear", "extend", "insert", "pop", "remove", "reverse", "sort",
             "update", "__setitem__", "popitem", "setdefault"}


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
    """
    aliases = _aliases_of(run_def, lambda v: bool(_names_in(v) & {var}))
    watched = {var} | aliases
    # ⛔ THE RECORDED ROWS ARE A COLLECTION TOO: `_row = per_query[-1]` then
    # `_row["order"] = _row["order"][:5]` truncated the page the verdict reads, with
    # every `"per_query" in receiver` clause passing (measured). Resolve names bound to
    # ANY expression mentioning `per_query`.
    rows = {"per_query"} | _aliases_of(run_def, lambda v: "per_query" in _names_in(v))
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
    changed = True
    while changed:
        changed = False
        for _node in ast.walk(run_def):
            if isinstance(_node, (ast.For, ast.AsyncFor, ast.comprehension)):
                _iter, _target = _node.iter, _node.target
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
                _unpacked = [t for t in _targets if isinstance(t, (ast.Tuple, ast.List))]
                if not _unpacked or not (_names_in(_iter) & muts):
                    continue
                _target = _unpacked[0]
            else:
                continue
            fresh = _names_in(_target) - muts
            if fresh:
                muts |= fresh
                changed = True
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
                and node.func.attr in _MUTATORS:
            receiver = _names_in(node.func.value)
        elif isinstance(node, ast.Subscript) and isinstance(node.ctx, (ast.Store, ast.Del)):
            receiver = _names_in(node.value)
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
            receiver = _names_in(node.target)
        callee = getattr(node.func, "id", None) or getattr(node.func, "attr", None) \
            if isinstance(node, ast.Call) else None
        def _element_only(arg: ast.AST) -> bool:
            """`results[0]` is an ELEMENT, not the collection (this file says so twice)."""
            if isinstance(arg, ast.Subscript):
                return True
            if isinstance(arg, ast.Call):
                callee_id = getattr(arg.func, "id", None) or getattr(arg.func, "attr", None)
                return callee_id in _PURE_READS or any(
                    _element_only(a) for a in arg.args)
            return isinstance(arg, ast.JoinedStr)  # an f-string mutates nothing it interpolates

        handed = isinstance(node, ast.Call) and callee not in _PURE_READS and any(
            _names_handed(arg) & watched and not _element_only(arg)
            for arg in list(node.args) + [k.value for k in node.keywords])
        if receiver & muts or handed:
            hits.append(node.lineno)
    return hits


def _per_query_element_mutations(run_def: ast.AST, after_lineno: int) -> list[int]:
    """Lines that mutate the RECORDED entries through an ALIAS of a `per_query` element.

    ⛔ MEASURED GREEN at `e66d23f539d4` — every one of these leaves `run()` returning
    pages the pin never inspected, while `compare()` reads exactly them:

        for _pq in per_query: _pq["order"].sort()
        for _pq in per_query: _pq["order"] = sorted(_pq["order"], key=str)
        for _i, _pq in enumerate(per_query): _pq["order"].sort()
        (zed,) = per_query[-1:]; zed["order"].sort()

    ⛔ WHAT IS UNIQUELY THIS GUARD'S IS THE UNPACKED TARGET — re-measured at
    `bb14ebfc501c`, not carried. The `for` shapes below are PINS of this guard, but their
    coverage is `_mutated_after`'s `rows` clause (that commit widened `rows` with every
    loop/comprehension target over `per_query`), which fires FIRST and reports its own
    message. Deleting the element guard still leaves them RED. The unpacked slice is the
    only listed shape it alone catches.

    `_mutated_after` covers a LITERAL receiver (`per_query[-1]["order"].sort()`) and, since
    `bb14ebfc501c`, a name bound to `per_query[-1]` and a `for` target — all through
    `rows`. `rows` is `{"per_query"} | …` on every call, so the older
    `or "per_query" in receiver` term was provably subsumed and has been deleted rather
    than left standing as a clause nothing can red.

    Residual, stated rather than implied: a BOUND MUTATOR of an element is not followed —
    `for _pq in per_query: _s = _pq["order"].sort; _s()` is GREEN. Same family as the
    `environ` half's `_set = os.environ.__setitem__`, which IS handled there; here it is
    not, and saying so is the difference between a residual and a hole.
    """
    element_names: set[str] = set()
    for node in ast.walk(run_def):
        # ⛔ ONLY the two shapes that name the ELEMENT ITSELF: `pq = per_query[-1]` and a
        # STATEMENT-LEVEL `for _pq in per_query:`. A name bound to a DERIVED collection
        # (`stage_table = {… for pq in per_query}`, or any comprehension) is not the
        # element, and treating it as one false-reds ordinary report-building code —
        # measured: the first cut of this guard red the real tool at
        # `tools/read_wave_latency.py:328,332`, which build the artifact dict itself.
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target: ast.AST = node.targets[0]
            value: ast.AST = node.value
        elif isinstance(node, (ast.AnnAssign, ast.NamedExpr)) and node.value is not None:
            target, value = node.target, node.value
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            target, value = node.target, node.iter
        else:
            continue
        if "per_query" not in _names_in(value):
            continue
        if not (isinstance(node, (ast.For, ast.AsyncFor))
                or isinstance(value, ast.Subscript)):
            continue
        # ⛔ EVERY NAME THE TARGET BINDS, not only a bare `Name` — `for _i, _pq in
        # enumerate(per_query):` and `(zed,) = per_query[-1:]` both name the ELEMENT and
        # were measured GREEN while only `isinstance(target, ast.Name)` was accepted.
        # `_mutated_after` does not cover them either: its unpack branch needs a
        # Tuple/List VALUE of matching length, and these values are a Call / a Slice.
        # A tuple target over a CALL marks the index too (fail-closed: a false red on an
        # index that is never mutated, against a GREEN permuted page).
        for bound in ast.walk(target):
            if isinstance(bound, ast.Name):
                element_names.add(bound.id)
    if not element_names:
        return []
    hits: list[int] = []
    for node in ast.walk(run_def):
        if getattr(node, "lineno", -1) <= after_lineno:
            continue
        touched = False
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr in _MUTATORS:
            touched = bool(_names_in(node.func.value) & element_names)
        elif isinstance(node, ast.Subscript) and isinstance(node.ctx, (ast.Store, ast.Del)):
            touched = bool(_names_in(node.value) & element_names)
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
        if any(var in _names_in(t) for t in targets):
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
    `os.environ` handed to any call (positional or keyword) is red too: the only reason to
    hand the mapping over is to change it. Residual, stated rather than implied: a helper
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
    mutators = {"clear", "pop", "popitem", "setdefault", "__delitem__", "__setitem__",
                "update"}
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
    env_alias = _aliases_of(tree, _mentions_mapping)
    env_alias |= imported_environ

    def _is_environ(node: ast.AST | None) -> bool:
        if isinstance(node, ast.Attribute):
            return node.attr == "environ"
        if isinstance(node, ast.Name):
            return node.id in env_alias
        return (isinstance(node, ast.Call)
                and getattr(node.func, "id", None) == "getattr"
                and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
                and node.args[1].value == "environ")

    # ⛔ A BOUND METHOD IS THE SAME POINTER: `_set = os.environ.__setitem__` then
    # `_set(...)`, and `_pop = os.environ.pop` then `_pop(...)`, were both measured green.
    bound = _aliases_of(
        tree, lambda v: (isinstance(v, ast.Attribute) and v.attr in mutators
                         and _mentions_mapping(v.value)))

    hits: list[int] = []
    for node in ast.walk(tree):
        subscript = (isinstance(node, ast.Subscript)
                     and isinstance(node.ctx, (ast.Store, ast.Del))
                     and _is_environ(node.value))
        method = (isinstance(node, ast.Call)
                  and getattr(node.func, "attr", None) in mutators
                  and _is_environ(getattr(node.func, "value", None)))
        aug = isinstance(node, ast.AugAssign) and _is_environ(node.target)
        bound_call = (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                      and node.func.id in bound)
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
        # ⛔ `os.environ = {…}` REPLACES the mapping: a rebinding, not a write.
        rebound = (isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.Delete))
                   and any(_is_environ(t) for t in (
                       getattr(node, "targets", None) or [getattr(node, "target", None)])))
        handed = isinstance(node, ast.Call) and any(
            _is_environ(a) for a in list(node.args) + [k.value for k in node.keywords])
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
    assert "first" not in _aliases_of(
        run_def, lambda v: isinstance(v, ast.Name) and v.id == "results"), (
        "`first = results[0]` is an ELEMENT of the page, not the page — treating it as "
        "an alias would red an innocent lookup")


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
def test_an_IMPORTED_or_GETATTR__environ_is_still_the_mapping() -> None:
    """The mapping is recognised through the STATIC spellings this repo can carry.

    ⛔ `os.environ`, `from os import environ` (incl. `as`), `getattr(os, "environ")`, and
    ANY alias/container/bound-mutator chain over those, to a fixpoint (#14281 review F1).
    Residual, stated rather than implied: `vars(os)["environ"]`, `os.__dict__["environ"]`
    and a name COMPUTED at runtime are not recognised, and a helper that reaches
    `os.environ` inside its own body is not followed — `tools/read_wave_latency.py`
    carries none of these.

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
    cases = {
        "plain import": (
            "from os import environ\n"
            'environ["TORTOISE_FUSED_RERANK"] = "1"\n'),
        "aliased import": (
            "from os import environ as env\n"
            'env["TORTOISE_FUSED_RERANK"] = "1"\n'),
        "getattr": (
            "import os\n"
            'getattr(os, "environ")["TORTOISE_FUSED_RERANK"] = "1"\n'),
        # ⛔ THE SEED MUST BE AS WIDE AS THE CHECK (#14281 review): `getattr(os, "environ")`
        # was recognised by `_is_environ` and NOT by `_mentions_mapping`, so the inline
        # spelling red and every ALIASED spelling of it stayed green — the same
        # narrower-predicate-than-check shape that made the import a terminal name.
        "getattr, then aliased": (
            "import os\n"
            '_e = getattr(os, "environ")\n'
            '_e["TORTOISE_FUSED_RERANK"] = "1"\n'),
        "getattr, aliased bound mutator": (
            "import os\n"
            '_e = getattr(os, "environ")\n'
            "_p = _e.pop\n"
            '_p("TORTOISE_FUSED_RERANK")\n'),
        # ⛔ SECOND-ORDER, and these are what makes the import a SEED rather than a
        # terminal name (#14281 review): adding the imported name to `env_alias` AFTER
        # `_aliases_of` had already run left every one of these GREEN, while the same
        # shapes written as `os.environ` were RED — the guard was transitive for one
        # spelling and one-hop for the spelling this commit adds.
        "imported, then aliased": (
            "from os import environ as _e\n"
            "_alias = _e\n"
            '_alias["TORTOISE_FUSED_RERANK"] = "1"\n'),
        "imported, bound mutator": (
            "from os import environ as _e\n"
            "_pop = _e.pop\n"
            '_pop("TORTOISE_FUSED_RERANK")\n'),
        "imported, through a container": (
            "from os import environ\n"
            "_envs = [environ]\n"
            '_envs[0]["TORTOISE_FUSED_RERANK"] = "1"\n'),
    }
    for label, src in cases.items():
        tree = ast.parse(src)
        parents: dict[int, ast.AST] = {
            id(c): p for p in ast.walk(tree) for c in ast.iter_child_nodes(p)}
        assert _environ_mutations(tree, parents), (
            f"[{label}] GREEN — the env guard does not resolve this spelling of "
            "`os.environ`, so the fused-rerank gate can be flipped inside `run()` and "
            "the artifact would not record which arm was in force")


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

    # CONTROLS, so a future reader can tell these two clauses are still covered elsewhere
    # and are not what this guard exists for.
    ctrl = ast.parse(
        "def run():\n    per_query.append({})\n    per_query[-1][\"order\"].sort()\n").body[0]
    ctrl_append = next(n for n in ast.walk(ctrl) if isinstance(n, ast.Call)
                       and getattr(n.func, "attr", None) == "append")
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
    never rebound after the read), and no ``os.environ`` mutation may happen in ``run()``
    at all — which arm of the fused-rerank gate was in force is not recorded (#14289).
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
    runtime (``"".join(…)``, a parameter) cannot be resolved statically, so this pin
    bounds the static reaches. Aliases ARE resolved on both halves (`alias = results;
    alias.sort()`, `env = os.environ; env[…] = …`), and handing either collection to a call
    that is not a known non-mutator is red. ⛔ The env-write half is one-sided on purpose: the
    harness does not RECORD which arm of `TORTOISE_FUSED_RERANK` was in force, so a
    baseline/after pair could straddle the gate and `compare()` would still call them
    comparable — tracked as #14289, not asserted here.
    """
    tree = ast.parse(_TOOL.read_text())
    parents: dict[int, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[id(child)] = node
    consts = _str_constants(tree)

    def _resolved(node: ast.AST) -> str | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.Name):
            return consts.get(node.id)  # a literal bound to a name, in ANY scope
        return None  # e.g. a parameter — not statically knowable here, see the docstring

    def _is_read_ref(node: ast.AST) -> bool:
        if isinstance(node, ast.Attribute) and node.attr in _READ_NAMES:
            return True
        if isinstance(node, ast.Name) and _resolved(node) in _READ_NAMES:
            return True
        return isinstance(node, ast.Constant) and node.value in _READ_NAMES

    # ⛔ ⛔ `indirect` IS EVALUATED *BEFORE* THE MENTIONS CENSUS (#14281 review F3). It used
    # to sit after it, where it was DEAD: every node satisfying `indirect` is also
    # `_is_read_ref`-true (a `Constant` in `_READ_NAMES`, or a `Name` bound to one), so the
    # census at `len(mentions) == 2` always red first — measured three ways, all of them
    # red at the census: `getattr(...)` replacing the warm-up, `getattr(...)` added
    # alongside, and a bound-method alias. The class was covered, so nothing was a false
    # PASS; but an assertion that can never be the failing row reads to the next lane as an
    # independent guard, and it is not one. First, it fires with its own diagnosis.
    indirect = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                and (getattr(n.func, "id", None) or getattr(n.func, "attr", None))
                in ("getattr", "setattr")
                and len(n.args) >= 2 and _resolved(n.args[1]) in _READ_NAMES]
    assert not indirect, (
        f"the tool reaches the read through {indirect[0].func.id}() by name — that bypasses "
        "every check in this pin")

    mentions = [n for n in ast.walk(tree) if _is_read_ref(n)]
    calls = _read_calls(tree)
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
        "artifact would hold a shallower page than the verdict assumes, and a reorder "
        "below the surviving depth becomes unobservable")
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
    holder = parents.get(id(measured))
    while holder is not None and not isinstance(holder, ast.Assign):
        holder = parents.get(id(holder))
    assert isinstance(holder, ast.Assign) and len(holder.targets) == 1 \
        and isinstance(holder.targets[0], ast.Name), (
        f"the measured read (line {measured.lineno}) is not assigned to a name — the pin "
        "cannot follow its page into the artifact")
    page_var = holder.targets[0].id
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
        assert _used & _names_in(gen.target), (
            f"the artifact's `{key}` does not derive its element from the loop target "
            f"`{ast.unparse(gen.target)}`: {ast.unparse(value.elt)}")
        if key == "legs":
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
    # `bb14ebfc501c`) every `for`/comprehension target over `per_query`. What is uniquely
    # `_per_query_element_mutations`'s is the UNPACKED target. Both halves are assertions;
    # neither is a sentence.
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
