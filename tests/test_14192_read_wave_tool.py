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
