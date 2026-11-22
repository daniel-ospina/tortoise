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
_READ_NAMES = ("tortoise_fts_query", "_tortoise_fts_query")


def _read_call_contract() -> tuple[set[str], set[str]]:
    """`(levers, instruments)` — DERIVED from the read's own signature, not remembered.

    A *lever* changes what the read returns (so a mirror must not name it); an
    *instrument* is a caller-owned out-sink that leaves the result alone (so a
    faithful mirror may name it). The split is read off `_tortoise_fts_query`'s
    signature: a boolean-defaulted parameter is a lever, a `None`-defaulted one is an
    out-sink. ⛔ The earlier shape used "any `_`-prefixed keyword" as the lever test,
    which forbade `_truncation_out` — an instrument the `/v1/search` door this file
    mirrors actually passes (`hosted_api.py`) — and would have false-red a faithful
    mirror while its message claimed the door names no lever.
    """
    tree = ast.parse(_SDK.read_text())
    defs = [n for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            and n.name == "_tortoise_fts_query"]
    assert len(defs) == 1, f"sdk.py holds {len(defs)} `_tortoise_fts_query` definitions"
    spec = defs[0].args
    pairs = [(a, d) for a, d in zip(spec.args[len(spec.args) - len(spec.defaults):],
                                    spec.defaults, strict=True)]
    pairs += list(zip(spec.kwonlyargs, spec.kw_defaults, strict=True))
    levers, instruments = set(), set()
    for arg, default in pairs:
        if default is None:
            continue
        if isinstance(default, ast.Constant) and isinstance(default.value, bool):
            levers.add(arg.arg)
        elif isinstance(default, ast.Constant) and default.value is None:
            instruments.add(arg.arg)
    assert {"_rerank_candidates", "_record_reads", "_valid_time_rerank"} <= levers, levers
    assert {"leg_trace", "_truncation_out"} <= instruments, instruments
    return levers, instruments


def test_the_harness_reads_the_SHIPPED_door_and_names_NO_lever_flags():
    """The harness must time the read the PRODUCT runs — and name no LEVER.

    `tests/test_rerank_pool.py`'s census refuses an un-opted-out site in `tools/`
    unless it is on its `intentional_keep` list, and this file is granted on the
    ground that it is a MIRROR of the shipped door: it measures
    ``TortoiseSDK._tortoise_fts_query`` — what ``/v1/search`` and the MCP
    ``tortoise_search`` call — and its verdict is the ORDERED id list, so a
    rerank-free reader would time a door the product never opens and would hide the
    very page permutation the rank-identity check exists to catch.

    A grant can only say "this file MAY run the lever", so the mirror is asserted here
    instead of assumed. Lever vs instrument is DERIVED from the read's signature
    (``_read_call_contract``), so this pin cannot rot into a remembered deny-list and
    cannot forbid an out-sink the mirrored door itself passes.

    ⛔ EVERY MENTION OF THE READ IN THIS FILE IS CENSUSED, not just the two calls: a
    bound-method alias (``_read = sdk._tortoise_fts_query`` then ``_read(…)``) and a
    ``getattr(sdk, "_tortoise_fts_query")`` both left a lexer that only read
    ``Call.func`` green with a lever-carrying third read (measured), and a bare
    attribute mention with no call at all would be invisible to the census as well.
    """
    tree = ast.parse(_TOOL.read_text())
    parents: dict[int, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[id(child)] = node

    def _owner(node: ast.AST) -> str:
        parent = parents.get(id(node))
        while parent is not None:
            if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)):
                return parent.name
            parent = parents.get(id(parent))
        return "<module>"

    mentions = [n for n in ast.walk(tree)
                if (isinstance(n, ast.Attribute) and n.attr in _READ_NAMES)
                or (isinstance(n, ast.Name) and n.id in _READ_NAMES)]
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and (getattr(n.func, "attr", None) or getattr(n.func, "id", None)) in _READ_NAMES]
    # Anti-vacuity: TWO mentions, both calls, both the reads this pin is about (the
    # warm-up and the per-query measurement), and both inside `run()`.
    assert len(calls) == 2, (
        f"tools/read_wave_latency.py holds {len(calls)} read calls — this pin knows the "
        "warm-up and the per-query one")
    assert len(mentions) == 2, (
        f"the tool MENTIONS the read {len(mentions)} times but calls it {len(calls)} — an "
        f"alias or a `getattr` reference is unaccounted for: "
        f"{[(ast.unparse(m), m.lineno) for m in mentions]}")
    assert all(_owner(m) == "run" for m in mentions), (
        f"the read is mentioned outside `run()`: {[(_owner(m), m.lineno) for m in mentions]}")
    indirect = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                and (getattr(n.func, "id", None) or getattr(n.func, "attr", None))
                in ("getattr", "setattr")
                and len(n.args) >= 2 and isinstance(n.args[1], ast.Constant)
                and n.args[1].value in _READ_NAMES]
    assert not indirect, (
        f"the tool reaches the read through {indirect[0].func.id}() by name — that bypasses "
        "every check in this pin")
    levers, instruments = _read_call_contract()
    traced = 0
    for call in calls:
        named = {k.arg: k.value for k in call.keywords if k.arg is not None}
        handed = sorted(set(named) & levers)
        assert not handed, (
            f"line {call.lineno} hands {handed} to the read — a lever changes WHICH rows "
            "survive `limit`, so a mirror that names one times a reader `/v1/search` does "
            "not run (and hides the page permutation the verdict exists to catch)")
        unknown = sorted(n for n in named
                         if n.startswith("_") and n not in levers and n not in instruments)
        assert not unknown, (
            f"line {call.lineno} hands {unknown}, which `_tortoise_fts_query`'s signature "
            "does not classify as a lever or an out-sink — decide which it is in "
            "`_read_call_contract` before trusting this pin")
        assert not any(k.arg is None for k in call.keywords), (
            f"line {call.lineno} splats a mapping into the read, so which levers it "
            "carries is invisible to this pin")
        if "leg_trace" in named:
            traced += 1
    assert traced == 1, (
        f"{traced} of the two reads collect a `leg_trace` — the per-query read declares "
        "the legs it measured from that trace, and the warm-up is the one that does not")
