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


_SDK = _REPO_ROOT / "tortoise" / "sdk.py"
_HOSTED = _REPO_ROOT / "tortoise" / "hosted_api.py"
_RERANK = _REPO_ROOT / "tortoise" / "rerank.py"
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

    door_calls = _read_calls(ast.parse(_HOSTED.read_text()))
    assert door_calls, "hosted_api.py no longer calls the read — this contract is stale"
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


def _rerank_env_names() -> set[str]:
    """The env gates that change WHICH rows the read returns, read off `rerank.py`."""
    tree = ast.parse(_RERANK.read_text())
    names: set[str] = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)
                and node.value.value.startswith("TORTOISE_")):
            names.add(node.value.value)
    assert {"TORTOISE_FUSED_RERANK", "TORTOISE_FUSED_RERANK_POOL"} <= names, sorted(names)
    return names


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
    """The `"key": value` pairs of the per-query artifact dict `run()` appends."""
    for node in ast.walk(run_def):
        if (isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "append"
                and node.args and isinstance(node.args[0], ast.Dict)):
            keys = [k.value for k in node.args[0].keys
                    if isinstance(k, ast.Constant) and isinstance(k.value, str)]
            if "legs" in keys and "order" in keys:
                return {k.value: v for k, v in zip(
                    [k for k in node.args[0].keys], node.args[0].values, strict=True)
                    if isinstance(k, ast.Constant) and isinstance(k.value, str)}
    raise AssertionError("run() no longer appends a per-query artifact dict with "
                         "`legs` and `order` — this pin is stale")


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
    measured read must be the one inside the loop, its page must be the ``order`` list
    the artifact reports, its trace must be the one the ``legs`` table is built from,
    and no env write may arm a rerank gate the artifact does not record.

    ⛔ EVERY STATIC REACH IS CENSUSED, not just the two calls: a bound-method alias
    (``_read = sdk._tortoise_fts_query``), a string LITERAL naming the read
    (``sdk.__getattribute__("_tortoise_fts_query")``, ``operator.methodcaller(...)``,
    ``vars(type(sdk))[...]``), a name BOUND to that literal, and a bare attribute
    mention with no call all left earlier shapes of this pin green while carrying a
    lever (each measured). Residual, stated rather than implied: a name COMPUTED at
    runtime (``"".join(…)``, a parameter) cannot be resolved statically, so this pin
    bounds the static reaches. ⛔ The env-write half is one-sided on purpose: the
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
    entries = _artifact_entries(run_defs[0])
    for key, var, what in (("order", page_var, "page"), ("legs", trace_var, "trace")):
        value = entries[key]
        assert isinstance(value, ast.ListComp), (
            f"the artifact's `{key}` is {ast.unparse(value)} — this pin follows a "
            f"comprehension over the measured {what}")
        assert any(var in _names_in(g.iter) for g in value.generators), (
            f"the artifact's `{key}` is not built from `{var}`, the measured read's "
            f"{what} — the verdict would report on something else: {ast.unparse(value)}")

    # ⛔ No env write may arm a rerank gate: `run()` already mutates `os.environ`, so
    # this is a plausible edit, and the artifact does not record which arm was in force.
    env_names = _rerank_env_names()
    armed = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store)
                and getattr(node.value, "attr", None) == "environ"
                and _resolved(node.slice) in env_names):
            armed.append((node.lineno, _resolved(node.slice)))
        if (isinstance(node, ast.Call)
                and getattr(node.func, "attr", None) in ("setdefault", "update", "pop")
                and getattr(getattr(node.func, "value", None), "attr", None) == "environ"
                and node.args and _resolved(node.args[0]) in env_names):
            armed.append((node.lineno, _resolved(node.args[0])))
    assert not armed, (
        f"the harness writes the rerank gate(s) {armed} — the artifact does not record "
        "which arm was in force, so a baseline/after pair could straddle the gate and "
        "`compare()` would still call it comparable")
