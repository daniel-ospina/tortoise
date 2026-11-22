"""#14277 — the cost measurement's own arithmetic.

The measurement is only worth what its arithmetic is worth: the two constants
it reports are a least-squares fit, the cost tables are derived from that fit,
and the allowance re-statement divides by the measured token count. A silent
sign error in any of those produces a number that looks like a result.

No API calls and no corpus walk here — these pin the pure functions against
hand-computable inputs, so the tool can be trusted on the day it is run and
re-run without spending money to check itself.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from tools import extractor_cost_measure as m  # noqa: E402


def test_least_squares_recovers_a_line_it_was_given():
    xs = [0.0, 1.0, 2.0, 3.0]
    ys = [5.0 + 2.0 * x for x in xs]
    slope, intercept = m._least_squares(xs, ys)
    assert slope == pytest.approx(2.0)
    assert intercept == pytest.approx(5.0)


def test_least_squares_on_a_degenerate_range_does_not_divide_by_zero():
    """Every item the same size is a real possibility in a narrow corpus; the
    fit must degrade to the mean rather than raise."""
    slope, intercept = m._least_squares([3.0, 3.0, 3.0], [7.0, 9.0, 11.0])
    assert slope == 0.0
    assert intercept == pytest.approx(9.0)


def test_a_perfect_fit_reports_r2_of_one_and_zero_residual():
    points = [(1.0, 10.0), (2.0, 20.0), (3.0, 30.0)]
    fit = m._fit(points)
    assert fit["slope"] == pytest.approx(10.0)
    assert fit["intercept"] == pytest.approx(0.0)
    assert fit["r2"] == pytest.approx(1.0)
    assert fit["max_abs_resid"] == pytest.approx(0.0)


def test_item_cost_splits_the_prompt_at_the_measured_hit_rate():
    """The whole cache question in one assertion: at a 50% hit share, half the
    prompt pays the input rate and half the cache rate, and the output is
    untouched by caching (there is nothing to cache on the emission side)."""
    rates = {"input": 1.0, "output": 2.0, "cache_hit": 0.1}
    cold = m._item_cost(1_000_000, 0, 0.0, rates)
    half = m._item_cost(1_000_000, 0, 0.5, rates)
    warm = m._item_cost(1_000_000, 0, 1.0, rates)
    assert cold == pytest.approx(1.0)
    assert half == pytest.approx(0.55)
    assert warm == pytest.approx(0.1)
    # a hit share outside [0, 1] is clamped, never extrapolated
    assert m._item_cost(1_000_000, 0, 1.5, rates) == pytest.approx(0.1)
    assert m._item_cost(1_000_000, 0, -1.0, rates) == pytest.approx(1.0)


def test_the_issue_model_is_the_model_the_issue_states():
    """`#14275`'s numbers are the thing under test, so they are restated in ONE
    place: `1.5T + 7k` input, `0.2T` output. A change here is a change to the
    claim being tested, which is why it is pinned rather than inline."""
    tin, tout = m._issue_model(5_162)
    assert tin == pytest.approx(1.5 * 5_162 + 7_000)
    assert tout == pytest.approx(0.2 * 5_162)


def _row(**kw) -> dict:
    base = {"kind": "doc", "arm": "cold", "item_tokens": 5_000,
            "prompt_tokens": 50_000, "completion_tokens": 16_000,
            "cache_hit_tokens": 25_000, "cache_miss_tokens": 25_000,
            "calls": 4, "seconds": 1.0,
            "stages": {"s1": {"calls": 1, "prompt_tokens": 12_000,
                              "completion_tokens": 2_000,
                              "cache_hit_tokens": 4_000,
                              "cache_miss_tokens": 8_000},
                       "s2": {"calls": 1, "prompt_tokens": 15_000,
                              "completion_tokens": 11_000,
                              "cache_hit_tokens": 9_000,
                              "cache_miss_tokens": 6_000},
                       "s4": {"calls": 1, "prompt_tokens": 23_000,
                              "completion_tokens": 3_000,
                              "cache_hit_tokens": 12_000,
                              "cache_miss_tokens": 11_000}}}
    return {**base, **kw}


def test_measurement_cost_uses_the_measured_cache_share():
    rates = {"input": 1.0, "output": 2.0, "cache_hit": 0.1}
    rows = [{"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000,
             "cache_hit_tokens": 500_000, "calls": 3},
            {"prompt_tokens": 0, "completion_tokens": 0,
             "cache_hit_tokens": 0, "calls": 0}]
    cost = m.measurement_cost(rows, rates, rates)
    assert cost["items"] == 2
    assert cost["hit_ratio"] == pytest.approx(0.5)
    # 0.5M miss at 1.0/M + 0.5M hit at 0.1/M + 1M out at 2.0/M
    assert cost["usd_peak"] == pytest.approx(0.5 + 0.05 + 2.0)
    assert cost["usd_peak_no_cache"] == pytest.approx(1.0 + 2.0)


def test_a_failed_item_is_counted_but_never_priced():
    """A run that half-failed must not report the surviving half's cost as the
    run's cost, and must not hide the failure either."""
    rows = [{"prompt_tokens": 1_000_000, "completion_tokens": 0,
             "cache_hit_tokens": 0, "calls": 1},
            {"failed": True, "error": "HTTP 500"}]
    cost = m.measurement_cost(rows, {"input": 1.0, "output": 1.0,
                                     "cache_hit": 0.0},
                              {"input": 1.0, "output": 1.0, "cache_hit": 0.0})
    assert cost["items"] == 1
    report = m.build_report(rows)
    assert report["failed"] == 1
    assert report["rows"] == 2


def test_allowance_table_derives_documents_from_the_measured_token_count():
    """`#14275`'s 3M-token allowance buys `3M / tokens-per-document` items —
    the arithmetic that turned ≈200 documents into ~47. It is derived here,
    never asserted, so a re-measured token count moves the answer."""
    rows = [_row(item_tokens=5_000 + i, prompt_tokens=48_000,
                 completion_tokens=16_000) for i in range(5)]
    report = m.build_report(rows)
    report["_rows"] = [r for r in rows]
    cost = m.cost_table(report, m.ISSUE_RATES["peak"],
                        m.ISSUE_RATES["off_peak"])
    per_doc = cost["tokens_per_median_doc"]
    assert per_doc == 64_000
    table = m.allowance_table(cost, per_doc)
    solo = next(a for a in table if a["tier"] == "solo")
    assert solo["documents"] == round(3_000_000 / 64_000)
    assert solo["documents"] == 47
    assert solo["usd_peak"] > 0.0
    assert solo["pct_peak"] > 0.0
    free = next(a for a in table if a["tier"] == "free")
    assert free["documents"] == 0 and free["usd_peak"] == 0.0


def test_the_corpus_is_deterministic_and_carries_the_length_spread():
    """Cold and warm arms are comparable only because they measure the SAME
    corpus; and a corpus of 50 similar files would answer a different
    question than the one asked."""
    first = m.pick_corpus(limit=20, seed=7)
    second = m.pick_corpus(limit=20, seed=7)
    assert [r["path"] for r in first] == [r["path"] for r in second]
    sizes = [r["tokens"] for r in first]
    assert sizes == sorted(sizes)
    inventory = m.doc_inventory()
    assert sizes[-1] == max(r["tokens"] for r in inventory), (
        "the longest document in the repo must be in the sample — the "
        "constant's leverage on long documents is the thing being measured")
    assert sizes[0] < 2_000, "the small end of the spread must be present"
    assert max(sizes) > 10 * sizes[0], "the spread must span an order of magnitude"


def test_a_literal_dollar_is_escaped_outside_code_spans_only():
    """MD037/MD038 came from bare `$` figures, not from the sentence they
    landed on (#14295).

    markdownlint resolves `$…$` spans, which re-pairs the surrounding
    delimiters — so the findings MOVED when unrelated prose on the line
    changed, and hand-editing the generated report could not keep them fixed.
    The fix is to emit `\\$` in prose (renders as `$`). Inside a code span a
    backslash is literal, so `$` there must be left alone — escaping it would
    print `\\$` to the reader.
    """
    line = ("- a `$0.0057` figure → **$0.0265** and a lone `$` span; "
            "also ``$x`` stays")
    out = m.escape_dollars_outside_code(line)
    assert "`$0.0057`" in out, "a code span's `$` must survive unescaped"
    assert "`$`" in out, "the lone `$` code span must survive unescaped"
    assert "``$x``" in out, "a double-backtick span must survive unescaped"
    assert "**\\$0.0265**" in out, "prose `$` must be escaped"
    assert "`` \\$" not in out
    # …and the else-branch really is reachable: the prose `$` count is what the
    # linter pairs, so every one of them outside a code span has to be escaped.
    assert out.count("\\$") == 1


@pytest.mark.parametrize("fence", ["```", "~~~"])
def test_a_fenced_block_is_never_escaped(fence):
    """A backslash is literal inside a fence, so escaping `$VAR` there would
    print a backslash the author never wrote — and the fence is the construct
    that breaks "prose `$` can always be escaped" if the scan is fence-blind."""
    doc = (f"# t\n\ntotal $5\n\n{fence}\necho $HOME\n{fence}\n\n"
           f"after $6\n")
    out = m.escape_dollars_outside_code(doc)
    assert f"{fence}\necho $HOME\n{fence}" in out, (
        "a `$` inside a fenced block must survive byte for byte")
    assert "total \\$5" in out and "after \\$6" in out, (
        "prose dollars on both sides of the fence must still be escaped")


def test_finalize_markdown_leaves_exactly_one_trailing_newline():
    """Each renderer ends with a blank-line element so consecutive sections
    separate; the LAST one then ends the file on a blank line, which is
    MD012 "multiple consecutive blank lines" (#14295)."""
    assert m.finalize_markdown("## a\n\nbody\n\n") == "## a\n\nbody\n"
    assert m.finalize_markdown("## a\n\nbody") == "## a\n\nbody\n"
    assert m.finalize_markdown("## a\n\n\n\n") == "## a\n"


def test_the_shipped_report_carries_no_bare_dollar_outside_a_code_span():
    """The artifact, not just the helper: `docs/evidence/14277-extractor-cost/
    report.md` is committed and linted by the required `docs` check, so the
    invariant is pinned on the file that ships."""
    report = (_REPO_ROOT / "docs" / "evidence" / "14277-extractor-cost"
              / "report.md").read_text(encoding="utf-8")
    assert report.endswith("\n") and not report.endswith("\n\n"), (
        "the report must not end on a blank line (MD012)")
    bare = m.escape_dollars_outside_code(report)
    assert bare == report, (
        "the shipped report carries a bare `$` outside a code span — it was "
        "hand-edited, or regenerated with a stale tool; regenerate it with "
        "`uv run python tools/extractor_cost_measure.py report …`")

_EVIDENCE = _REPO_ROOT / "docs" / "evidence" / "14277-extractor-cost"


def test_the_committed_bundle_is_reproducible_by_the_shipped_renderer():
    """The README's claim, pinned: "nothing in ``report.md`` or ``report.json``
    is hand-written, and both can be regenerated from the raw rows".

    The committed pair was first written by an EARLIER revision of
    ``render()``: its ``long_split`` block was a single sentence
    ("long document split into 40 turns …") that this module no longer emits
    anywhere, and it omitted the per-row ``calls`` and stage-output figures.
    The numbers matched, so no claim was falsified — but a reader who re-ran
    ``report`` to check the bundle got a file that differed from the one
    committed, which is exactly the failure the README's sentence exists to
    rule out. Re-running the documented command fixed the artifact; this pin
    is what keeps the next revision of a renderer from silently doing it
    again (``long_split`` is the one block no other test covers).
    """
    rows = m.load_rows(_EVIDENCE / "measurements.jsonl")
    report = m.report_from_rows(rows)

    assert (_EVIDENCE / "report.md").read_text(encoding="utf-8") == \
        m.report_markdown(report)
    assert (_EVIDENCE / "report.json").read_text(encoding="utf-8") == \
        m.report_json(report)


def test_the_documented_command_regenerates_the_committed_bundle(tmp_path):
    """The pin above calls the render functions directly, so it cannot see
    ``main()`` stop using them. The README documents the *command*, and the
    drift that put the old bundle on disk was a renderer revision nobody
    re-ran it against — so the command itself is what has to reproduce the
    committed pair.
    """
    md = tmp_path / "report.md"
    js = tmp_path / "report.json"
    assert m.main(["report",
                   "--out", str(_EVIDENCE / "measurements.jsonl"),
                   "--json-out", str(js),
                   "--markdown-out", str(md)]) == 0

    assert md.read_text(encoding="utf-8") == \
        (_EVIDENCE / "report.md").read_text(encoding="utf-8")
    assert js.read_text(encoding="utf-8") == \
        (_EVIDENCE / "report.json").read_text(encoding="utf-8")
