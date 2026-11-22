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
    assert {"query", "limit", "read_status_out", "_truncation_out"} <= allowed, sorted(allowed)
    named_levers = allowed & bool_levers
    assert not named_levers, (
        f"the door itself already names {sorted(named_levers)} — the mirror cannot be "
        "distinguished from a lever-carrying reader; re-derive this contract")
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


def _artifact_entries(run_def: ast.AST) -> dict[str, ast.AST]:
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


_NON_MUTATING = {"list", "tuple", "len", "sorted", "id", "str", "bool", "any", "all",
                 "isinstance"}


def _aliases_of(tree: ast.AST, predicate) -> set[str]:
    """Names bound to a matching expression — `alias = results`, `env = os.environ`."""
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name) and predicate(node.value):
            out.add(node.targets[0].id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) \
                and node.value is not None and predicate(node.value):
            out.add(node.target.id)
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
    also catches `_helper(results[0])`, which the equality form missed on arity alone.
    """
    aliases = _aliases_of(run_def, lambda v: isinstance(v, ast.Name) and v.id == var)
    watched = {var} | aliases
    hits: list[int] = []
    for node in ast.walk(run_def):
        if getattr(node, "lineno", -1) <= lineno or node is artifact_append:
            continue
        receiver: set[str] = set()
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr in _MUTATORS:
            receiver = _names_in(node.func.value)
        elif isinstance(node, ast.Subscript) and isinstance(node.ctx, (ast.Store, ast.Del)):
            receiver = _names_in(node.value)
        callee = getattr(node.func, "id", None) or getattr(node.func, "attr", None) \
            if isinstance(node, ast.Call) else None
        handed = isinstance(node, ast.Call) and callee not in _NON_MUTATING and any(
            _names_in(arg) & watched
            for arg in list(node.args) + [k.value for k in node.keywords])
        if receiver & watched or "per_query" in receiver or handed:
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
    """Line numbers where `var` is rebound after the read — a sorted/emptied page lies."""
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
            hits.append(node.lineno)
    return hits


def _environ_mutations(tree: ast.AST, parents: dict[int, ast.AST]) -> list[int]:
    """Every line that can change `os.environ`.

    ⛔ The guard is on the RECEIVER, and the receiver is resolved through ALIASES
    (`env = os.environ; env["TORTOISE_FUSED_RERANK"] = "1"` defeated a name-exact receiver
    test — measured), plus the dunder entries `__setitem__` / `__delitem__`.
    `os.environ` handed to any call (positional or keyword) is red too: the only reason to
    hand the mapping over is to change it. Residual, stated rather than implied: a helper
    that reaches `os.environ` inside its own body is not followed (the tool has none).

    The only exemption is the module-level setup pops of the two API variables — the
    harness removes them BEFORE building the SDK so it measures the embedded graph. The
    exemption is scoped: the same `pop` inside `run()` is a mutation like any other.
    """
    mutators = {"clear", "pop", "popitem", "setdefault", "__delitem__", "__setitem__",
                "update"}
    setup_pops = {"TORTOISE_API_URL", "TORTOISE_API_KEY"}
    env_alias = _aliases_of(
        tree, lambda v: isinstance(v, ast.Attribute) and v.attr == "environ")
    env_alias |= _aliases_of(tree, lambda v: isinstance(v, ast.Name) and v.id in env_alias)

    def _is_environ(node: ast.AST) -> bool:
        return ((isinstance(node, ast.Attribute) and node.attr == "environ")
                or (isinstance(node, ast.Name) and node.id in env_alias))

    hits: list[int] = []
    for node in ast.walk(tree):
        subscript = (isinstance(node, ast.Subscript)
                     and isinstance(node.ctx, (ast.Store, ast.Del))
                     and _is_environ(node.value))
        method = (isinstance(node, ast.Call)
                  and getattr(node.func, "attr", None) in mutators
                  and _is_environ(getattr(node.func, "value", None)))
        aug = isinstance(node, ast.AugAssign) and _is_environ(node.target)
        handed = isinstance(node, ast.Call) and any(
            _is_environ(a) for a in list(node.args) + [k.value for k in node.keywords])
        if not (subscript or method or aug or handed):
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


def test_the_harness_reads_the_SHIPPED_door_and_names_NO_lever_flags():
    """The harness must time the read the PRODUCT runs — with the door's own arguments.

    `tests/test_rerank_pool.py`'s census refuses an un-opted-out site in `tools/`
    unless it is on its `intentional_keep` list, and this file is granted on the
    ground that it is a MIRROR of the shipped door: it measures
    ``TortoiseSDK._tortoise_fts_query`` — what ``/v1/search`` and the MCP
    ``tortoise_search`` call — and its verdict is the ORDERED id list, so a
    rerank-free reader would time a door the product never opens and would hide the
    very page permutation the rank-identity check exists to catch.

    A grant can only say "this file MAY run the lever", so the mirror is asserted here
    instead of assumed, against the door's OWN call site (``_door_read_contract``):
    every keyword must be one the door names (plus the ``leg_trace`` out-sink), every
    argument must be a keyword (a positional one is invisible to a kwargs check), the
    measured read must be the one inside the loop, its page and trace must be iterated
    AS THEMSELVES by the ``order``/``legs`` comprehensions of the one artifact dict (and
    never rebound after the read), and no ``os.environ`` mutation may happen in ``run()``
    at all — which arm of the fused-rerank gate was in force is not recorded (#14289).

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
    indirect = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                and (getattr(n.func, "id", None) or getattr(n.func, "attr", None))
                in ("getattr", "setattr")
                and len(n.args) >= 2 and _resolved(n.args[1]) in _READ_NAMES]
    assert not indirect, (
        f"the tool reaches the read through {indirect[0].func.id}() by name — that bypasses "
        "every check in this pin")

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
        assert _names_in(value.elt) & _names_in(gen.target), (
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
