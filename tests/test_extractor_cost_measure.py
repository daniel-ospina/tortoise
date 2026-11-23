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

import json
import os
import re
import statistics
import sys
import tomllib
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


def test_r2_falls_below_one_when_the_fit_has_residuals():
    """The perfect-fit row above is satisfied by a constant ``r2 = 1.0``.

    ``r2`` is printed in the report as the evidence that the two-constant
    model actually tracks the data, so a formula that returns 1 for every
    input would report a perfect fit for a fit that is not one — a false
    PASS on the deliverable's own quality claim (#14277 review, measured
    WEAK row). Pinned against a hand-computable non-perfect fit so the
    mutation ``"r2": (1 - ss_res / ss_tot)`` → ``"r2": 1.0`` reddens THIS
    row: mean y = 2.25, ss_tot = 4.75, ss_res = 0.7, so r2 = 0.8526.
    """
    fit = m._fit([(1.0, 1.0), (2.0, 2.0), (3.0, 2.0), (4.0, 4.0)])
    assert fit["slope"] == pytest.approx(0.9)
    assert fit["intercept"] == pytest.approx(0.0)
    assert fit["max_abs_resid"] == pytest.approx(0.7)
    assert fit["r2"] == pytest.approx(0.8526315789473684)
    assert fit["r2"] < 1.0, "a fit with residuals cannot report a perfect fit"


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


def test_the_corpus_seed_actually_selects_the_sample():
    """Both calls above pass ``seed=7``, which IS the default — so they stay
    green under ``random.Random(seed)`` → ``random.Random(7)``, i.e. with the
    caller's ``--seed`` silently ignored (#14277 review, measured WEAK row).

    The tool's whole covariance claim rests on cold and warm measuring the
    SAME corpus, so a seed the caller cannot change would let two arms claim
    they shared a sample while being free to diverge. Pin the plumbing: two
    different seeds must not draw the same sample.
    """
    seven = [r["path"] for r in m.pick_corpus(limit=20, seed=7)]
    ninetynine = [r["path"] for r in m.pick_corpus(limit=20, seed=99)]
    assert seven != ninetynine, (
        "seed=99 drew the seed=7 sample — the caller's seed is not reaching "
        "random.Random, so --seed is inert")
    # ...and the same seed still reproduces exactly, which is the property the
    # determinism row above relies on (asserted here against the OTHER seed so
    # the pin cannot pass by both calls being hardcoded to 7).
    assert [r["path"] for r in m.pick_corpus(limit=20, seed=99)] == ninetynine


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


_FENCED = {
    # (id, document, the span that must survive byte for byte)
    "backtick": ("# t\n\ntotal $5\n\n```\necho $HOME\n```\n\nafter $6\n",
                 "```\necho $HOME\n```"),
    "tilde": ("# t\n\ntotal $5\n\n~~~\necho $HOME\n~~~\n\nafter $6\n",
              "~~~\necho $HOME\n~~~"),
    "info string": ("# t\n\n```sh\necho $HOME\n```\n", "```sh\necho $HOME\n```"),
    # An opener of FOUR backticks: a three-backtick line inside it is CONTENT,
    # so `$HOME` must stay bare. Truncating the opener to three (the first cut
    # of this fix did) closes the fence early and escapes it.
    "four-backtick opener": ("# t\n\n````\nexample:\n```\necho $HOME\n```\n"
                             "````\n\nafter $6\n",
                             "````\nexample:\n```\necho $HOME\n```\n````"),
    # A closer with trailing text is not a closer (CommonMark: only whitespace
    # may follow), so the block continues and `$C` is content.
    "closer with trailing text": ("# t\n\n```sh\necho $A\n``` not a fence $B\n"
                                 "echo $C\n```\n\nz $2\n",
                                 "```sh\necho $A\n``` not a fence $B\necho $C\n```"),
    "blockquote": ("# t\n\ntotal $5\n\n> ```\n> echo $HOME\n> ```\n\n"
                   "after $6\n",
                   "> ```\n> echo $HOME\n> ```"),
    "list-indented (2 spaces, still a fence)": (
        "# t\n\n  ```\n  echo $HOME\n  ```\n\nafter $6\n",
        "  ```\n  echo $HOME\n  ```"),
    # REGRESSION GUARD: a blockquote-fenced EXAMPLE inside a fence is content.
    # Stripping `>` markers on the closer (rather than only to the opener's own
    # depth) let `> ``` ` close the enclosing fence, after which every `$` in the
    # rest of the document stayed bare — the silent direction. The closer must
    # be tested at the OPENER's depth, which here is zero.
    "quoted fence inside a fence": (
        "# t\n\n```markdown\n> ```bash\n> echo $HOME\n> ```\n```\n$5 total\n",
        "```markdown\n> ```bash\n> echo $HOME\n> ```\n```"),
    "nested blockquote fence": (
        "# t\n\na $1\n\n> > ```\n> > echo $HOME\n> > ```\n\nb $2\n",
        "> > ```\n> > echo $HOME\n> > ```"),
    # The blockquote ENDING closes the fence it held: CommonMark ends the
    # container, it does not carry a fence on past it. Without this the prose
    # after the quote was left bare — the silent direction.
    "container ends after a quoted fence": (
        "# t\n\n> ```\n> echo $HOME\na $5\n",
        "> ```\n> echo $HOME"),
    "container ends after a nested quoted fence": (
        "# t\n\n> ```\n> > ```\na $5\n",
        "> ```\n> > ```"),
    # A quoted fence inside a LIST ITEM: the markers are offset by the item's
    # content indent, and one content line sits at column 4. A marker regex
    # capped at {0,3} read that line as depth 0, ended the fence on it, and
    # re-read the real closer as a NEW opener — escaping a code line AND leaving
    # the following prose bare. Graded against a CommonMark renderer.
    "quoted fence inside a list item": (
        "- item\n  > ```\n  > a $1\n    > b $2\n  > ```\n  > prose $P\n",
        "  > ```\n  > a $1\n    > b $2\n  > ```"),
    # An unclosed fence makes the REST of the document code — deliberate, and
    # stated in the docstring.
    "unclosed fence": ("# t\n\ntotal $5\n\n```\necho $HOME\nmore $7 code\n",
                       "```\necho $HOME\nmore $7 code\n"),
}


@pytest.mark.parametrize("case", sorted(_FENCED))
def test_a_fenced_block_is_never_escaped(case):
    """A backslash is literal inside a fence, so escaping `$VAR` there prints a
    backslash the author never wrote — and a fence-blind scan is what breaks
    "prose `$` can always be escaped"."""
    doc, must_survive = _FENCED[case]
    out = m.escape_dollars_outside_code(doc)
    assert must_survive in out, f"{case}: fenced `$` must survive byte for byte"
    # Every prose figure this case carries outside the fence must be escaped,
    # INCLUDING one after the fence — that is what proves the fence state was
    # left, rather than escaping being switched off for the rest of the doc.
    for before, after in (("total $5", "total \\$5"), ("z $2", "z \\$2"),
                          ("$5 total", "\\$5 total"), ("a $5\n", "a \\$5"),
                          ("$P", "\\$P")):
        if before in doc:
            assert after in out, f"{case}: prose {before!r} must be escaped"
    if case == "nested blockquote fence":
        assert "b \\$2" in out, "prose after a nested quoted fence must be escaped"
    if case == "quoted fence inside a list item":
        assert "b $2" in out, (
            "a code line at column 4 inside a list-item quote must stay bare")
    if case != "unclosed fence" and "after $6" in doc:
        assert "after \\$6" in out, (
            f"{case}: prose AFTER the fence must be escaped again — a fence "
            f"that is never left switches escaping off for the rest of the doc")


def test_an_indented_code_block_is_escaped_and_that_is_stated():
    """4+ spaces is an indented code block, not a fence. The first cut of this
    fix stripped all leading whitespace, so such a line OPENED a fence and
    silently switched escaping off for the rest of the document — the silent
    direction. It must not open a fence; the `$` inside it is escaped, which is
    the residual the docstring names."""
    doc = "# t\n\ntotal $5\n\n        ```\n        $HOME\n\n\nafter $6\n"
    out = m.escape_dollars_outside_code(doc)
    assert "after \\$6" in out, (
        "an indented ``` must NOT open a fence — prose after it is still prose")
    assert "        \\$HOME" in out, (
        "the residual, as documented: an indented code block is not tracked")


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
    # The check must NOT be the escaper judging itself: a document the escaper
    # leaves alone is a fixed point whether or not it needed escaping (that is
    # how the odd-backtick direction stays silent). So strip code spans and
    # fenced blocks HERE, independently, and assert the prose carries no bare
    # `$` at all.
    import re as _re
    prose_bare: list[str] = []
    in_fence = False
    for number, line in enumerate(report.split("\n"), 1):
        if line.lstrip().startswith(("```", "~~~")):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        assert len(_re.findall(r"`+", line)) % 2 == 0, (
            f"report.md:{number} has an odd number of backtick runs — its tail "
            f"reads as code, and the escaper leaves bare `$` there (silently)")
        outside_code = _re.sub(r"`+[^`]*`+", "", line)
        if _re.search(r"(?<!\\)\$", outside_code):
            prose_bare.append(f"{number}: {outside_code.strip()[:70]}")
    assert not prose_bare, (
        "the shipped report carries a bare `$` in prose — markdownlint pairs it "
        "as math and re-pairs the surrounding delimiters (MD037/MD038); "
        "regenerate it with `uv run python tools/extractor_cost_measure.py "
        f"report …`: {prose_bare[:3]}")
    assert m.escape_dollars_outside_code(report) == report, (
        "regenerate the report: it is not a fixed point of the escaper, so it "
        "was hand-edited or written by a stale tool")

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


def test_the_first_measured_document_is_labelled_not_a_cold_prefix():
    """The section used to be titled "the true cold call (first document,
    before any prefix cached)" over a row that is 83% cache HITS.

    The provider's prefix cache outlives a single call and six workers run
    concurrently against the same per-stage system prompts, so the cold arm's
    first document was already 83% cached when it was measured — the bundle
    therefore contains no true-cold datum, and a heading that says otherwise
    presents a measurement artifact as the cold reading (#14277 review, finding
    2). The heading is the claim, so the heading is pinned.

    ⛔ Do NOT explain the warmth as a RESUME. `run_measurement` skips rows
    already in `_existing_rows(out)`, so a resumed row is never re-measured,
    and every committed row's `measured_at` falls inside one 23-minute window —
    nothing in this bundle is a re-entry into an earlier run, and asserting
    that it is would be a provenance claim the data refutes.
    """
    report = m.report_from_rows(m.load_rows(_EVIDENCE / "measurements.jsonl"))
    fc = report["first_call"]
    hits = sum(b["hit"] for b in fc["stages"].values())
    prompts = sum(b["prompt"] for b in fc["stages"].values())
    assert hits / prompts > 0.5, (
        "this pin assumes the committed first row is heavily cached; if a "
        "future run really does start cold, relabel the section deliberately "
        "rather than deleting the check")

    text = m.report_markdown(report)
    assert "NOT a cold prefix" in text
    assert "true cold call" not in text
    assert f"{hits / prompts:.1%} of this item's prompt tokens" in text
    assert f"{hits:,} hit of {prompts:,}" in text


def test_the_cost_table_reads_one_arm_and_not_a_pool_of_both():
    """#14295 review, P1: `cost_table` priced the POOLED cold+warm `_rows`
    while taking `hit_ratio` from the cold arm alone — and the headline fits
    (`45,503 + 2.159T`) are fitted on the cold arm, so the table's measured
    columns were neither arm's statistic. It showed up as one generated
    document contradicting itself: the prose reported the cold-arm median
    session (30,647 / 5,768) and the cost table reported the pooled one
    (31,229 / 5,387).

    This fixture makes the two answers diverge so the pooling cannot come
    back silently: cold rows are 50k/16k, warm rows are 90k/40k, so the
    pooled median (70k/28k) is a third value that matches NEITHER arm.
    """
    rows = ([_row(arm="cold", prompt_tokens=50_000, completion_tokens=16_000,
                  cache_hit_tokens=25_000, cache_miss_tokens=25_000)
             for _ in range(3)]
            # a DIFFERENT cache split too (75% vs 50%), so the hit-ratio
            # assertion below cannot pass on a wrong arm that happens to share
            # the cold arm's ratio
            + [_row(arm="warm", prompt_tokens=90_000, completion_tokens=40_000,
                    cache_hit_tokens=67_500, cache_miss_tokens=22_500)
               for _ in range(3)])
    report = m.report_from_rows(rows)
    cost = report["costs"]
    median = next(r for r in cost["rows"] if r["item"] == "median document")

    assert (median["measured_in"], median["measured_out"]) == (50_000, 16_000), (
        f"the cost table must read the arm that supplies hit_ratio and the "
        f"fits (cold); got {median['measured_in']}/{median['measured_out']}")
    # the pooled answer, spelled out, so the assertion above cannot pass by
    # both arms happening to share a median
    assert (median["measured_in"], median["measured_out"]) != (70_000, 28_000)
    assert cost["hit_ratio"] == report["arms"]["cold"]["doc"]["cache"]["hit_ratio"]


def test_a_warm_only_bundle_renders_and_says_it_is_the_warm_arm():
    """#14295 review, P2: `--arms warm` is a documented flag, and its bundle
    crashed the renderer (`KeyError: 'cold'` from `render_verdict`) or, one
    function over, labelled the warm arm's cache ratio as the COLD arm's — the
    same mislabelling the pooling fix removed, one level up in the render.
    """
    rows = [_row(arm="warm", prompt_tokens=90_000, completion_tokens=40_000,
                 cache_hit_tokens=67_500, cache_miss_tokens=22_500)
            for _ in range(3)]
    report = m.report_from_rows(rows)
    assert list(report["arms"]) == ["warm"]

    text = m.report_markdown(report)          # used to raise KeyError: 'cold'
    assert report["costs"]["arm"] == "warm"
    assert "**75.0%** (warm arm)" in text, (
        "the cost section must name the arm it measured, not the literal 'cold'")
    assert "(cold arm)" not in text
    # and the fits it quotes are that arm's own
    assert f"{report['arms']['warm']['input_fit']['intercept']:,.0f} + " in text


def test_a_warm_only_render_does_not_name_one_arm_twice():
    """#14295 review, P4: the share line prints the ROW arm's figure and then the
    WARM arm's, which are the same measurement when the rows came from the warm
    arm — so a `--arms warm` bundle rendered `**75.0%** (warm arm) · 75.0% (warm
    arm)`, reading as if two arms had been measured. The second clause is the
    cross-arm comparison and belongs only where there are two arms to compare.
    """
    rows = [_row(arm="warm", prompt_tokens=90_000, completion_tokens=40_000,
                 cache_hit_tokens=67_500, cache_miss_tokens=22_500)
            for _ in range(3)]
    text = m.report_markdown(m.report_from_rows(rows))
    share_lines = [ln for ln in text.splitlines()
                   if "Measured cache-hit share" in ln]
    assert len(share_lines) == 1, share_lines
    assert share_lines[0].count("(warm arm)") == 1, (
        f"one arm was measured, so its share is named once; got {share_lines[0]}")
    assert share_lines[0].count("%") == 1, (
        f"the same measurement must not be quoted twice; got {share_lines[0]}")


def test_a_cold_only_render_invents_no_warm_arm():
    """#14295 review, P3: the mirror of the warm-only case. `--arms` is free-form,
    and `cost['warm_hit_ratio']` falls back to 0.0 when no warm row exists — so a
    `--arms cold` bundle rendered `**50.0%** (cold arm) · 0.0% (warm arm)` and a
    lever paragraph about "the warm arm's measured 0.0% hit share": a measurement
    attributed to an arm the bundle never ran. Both clauses are now guarded on the
    warm arm being present, which is also what the warm-only guard's rationale
    ("only where there are two arms to compare") promises.
    """
    rows = [_row(arm="cold") for _ in range(3)]
    report = m.report_from_rows(rows)
    assert list(report["arms"]) == ["cold"]
    assert "warm" not in report.get("costs", {}).get("arm", "")

    text = m.report_markdown(report)
    assert "0.0% (warm arm)" not in text, (
        "a cold-only bundle has no warm arm; quoting its 0.0 fallback as a "
        "measured share attributes a measurement to an arm that never ran")
    assert "(warm arm)" not in text
    share_lines = [ln for ln in text.splitlines()
                   if "Measured cache-hit share" in ln]
    assert len(share_lines) == 1, share_lines
    assert "(cold arm)" in share_lines[0]

    # ⛔ The SECOND clause the fix guards is the cache-lever sentence, which says
    # "The warm arm's measured X% hit share reaches $Y" — it spells the arm out
    # in prose, so `"(warm arm)" not in text` above does NOT reach it. Measured:
    # reverting only `:1238`'s `if has_warm else ""` back to an unconditional
    # emit left every assertion in this test GREEN, so half the fabrication this
    # test exists to prevent was unpinned (#14295 review, M2).
    lever = [ln for ln in text.splitlines() if "How big the cache lever" in ln]
    assert len(lever) == 1, lever
    assert "warm arm" not in lever[0], (
        "no warm arm was measured, so the lever paragraph cannot quote its hit "
        f"share; got {lever[0]}")

    # the two-arm bundle is unaffected: guarded, not deleted
    both = m.report_from_rows(rows + [_row(arm="warm") for _ in range(3)])
    both_text = m.report_markdown(both)
    assert "(warm arm)" in both_text
    both_lever = [ln for ln in both_text.splitlines()
                  if "How big the cache lever" in ln]
    assert len(both_lever) == 1, both_lever
    assert "warm arm" in both_lever[0], (
        "with a warm arm measured, the lever paragraph must still quote it; "
        f"got {both_lever[0]}")


def test_the_tokenizer_the_tool_needs_is_declared_on_both_install_paths():
    """`#14277` — `_encoding()` imports `tiktoken` lazily, so only DECLARATIONS make it reachable.

    `tools/extractor_cost_measure.py::_encoding` imports `tiktoken` inside the function body,
    so the module imports fine on an install that lacks it and the failure lands on the
    tokenizing cases alone — which is exactly how this file was RED on `main` before the
    declaration existed (measured at `#14277`: 2 of 35 cases, `ModuleNotFoundError` at
    `tools/extractor_cost_measure.py:120`). A lazy import is what makes this a declaration
    problem rather than an import-time crash, so it is pinned as one.

    Both install paths are pinned because BOTH are installed in practice (`#2985`): CI's
    `pip install -e '.[test]'` and the canonical dev install `uv sync --extra embeddings
    --extra parity`, which does NOT include the `test` extra. A `dev`-only entry leaves the
    required CI legs red; a `test`-only entry strands the re-measure recipe the recorded
    `_S7_TOKEN_CELLS` cells point at. This mirrors
    `test_closes_landing.py::test_the_commonmark_oracle_cannot_be_skipped_out_of_the_install`.

    Non-vacuous: at the pre-fix head `project.optional-dependencies.test` carries no
    `tiktoken` entry at all, so the first path reds before the second is ever reached.

    ⛔ THERE IS A THIRD PATH, and pinning the two declarations is not enough without it.
    `post-merge-validation.yml` hand-enumerates its install as
    `pip install -e '.[embeddings]' <packages…>` — it has never taken the `test` extra
    (#493) — and then runs the fast tree on the AMBIENT interpreter
    (`tests/` minus `tests/e2e`, minus `slow_files`, minus `-m 'not track_b'`). This file is
    not in `slow_files`, so that leg collects it, and a package reachable only through
    `dev` or the `test` extra is missing there. `tiktoken` was exactly that gap (#14277):
    the two corpus cases red on `main` on a warn-only workflow. So the arm below asserts
    the leg either installs the extra or names the package — it is not satisfied by the
    pyproject declarations alone.
    """
    pyproject = tomllib.loads((_REPO_ROOT / "pyproject.toml").read_text())
    for where, declarations in (
        ("`project.optional-dependencies.test` (CI: pip install -e '.[test]')",
         pyproject["project"]["optional-dependencies"]["test"]),
        ("`dependency-groups.dev` (uv sync --extra embeddings --extra parity)",
         pyproject["dependency-groups"]["dev"]),
    ):
        assert any(d.startswith("tiktoken") for d in declarations), (
            f"tiktoken is missing from {where} — `tools/extractor_cost_measure.py::_encoding` "
            f"needs it, and because the import is lazy the tokenizing cases fail with "
            f"`ModuleNotFoundError` instead of being skipped (#14277). Declarations: "
            f"{declarations}")

    # Third path: the hand-enumerated post-merge leg (see the docstring). It is allowed to
    # switch to the extra later; what it may NOT do is install neither. Comment lines are
    # skipped so that a FUTURE commented-out install command cannot red this arm — the file
    # holds none today (measured: every revision of it has exactly one live `pip install -e`
    # line, never a commented one); the guard is against the note that documenting the switch
    # to `'.[test,embeddings]'` would add. The extras test is a real parse rather than a
    # substring: `".[test]"` does NOT contain `".test"` — the dot is followed by `[` — so a
    # substring arm would be dead code and would red the very remediation the docstring
    # blesses.
    pmv = (_REPO_ROOT / ".github" / "workflows" / "post-merge-validation.yml").read_text()
    live = [ln for ln in pmv.splitlines() if not ln.lstrip().startswith("#")]
    installs = [ln for ln in live if "pip install -e" in ln]
    if not installs:
        # The leg may legitimately be rewritten to `uv sync … --extra test`, which installs the
        # extra without any `pip install -e` line. Refusing that shape would red the very
        # remediation this docstring blesses.
        installs = [ln for ln in live if "uv sync" in ln and "--extra test" in ln]
    assert installs, (
        "post-merge-validation.yml has neither a `pip install -e` line nor a `uv sync … "
        "--extra test` line, so this arm cannot judge it either way — if the leg moved to a "
        "third install shape, extend this guard rather than deleting it (#14277)")
    test_extra = re.compile(r"\.\[[^\]]*\btest\b[^\]]*\]")
    for line in installs:
        assert (test_extra.search(line) or "--extra test" in line
                or "tiktoken" in line), (
            "post-merge-validation.yml installs this leg WITHOUT the `test` extra and "
            "without `tiktoken`, but it runs `python -m pytest tests/` over the fast tree "
            "on the ambient interpreter and `tests/test_extractor_cost_measure.py` is not "
            "in `slow_files` — so its two corpus cases die with `ModuleNotFoundError` "
            f"(#14277). Install line: {line.strip()}")


# ── #14343: the decomposition of the two constants ────────────────────────
#
# `decompose` answers a different question from `run`: not "how much" but "of
# what". Its table is the deliverable, so the properties that make it EVIDENCE
# are pinned here — a table that silently stopped summing, or that attributed
# a region the prompt does not contain, would read as a result.

def _evidence_rows() -> list[dict]:
    import json

    path = (_REPO_ROOT / "docs" / "evidence" / "14277-extractor-cost"
            / "measurements.jsonl")
    return [json.loads(line) for line in path.read_text().splitlines()
            if line.strip()]


def test_the_static_regions_of_a_prompt_are_its_own_bytes():
    """Every spliced region is counted, and the prose is the rest.

    ⛔ A "the parts sum to the render" assertion is VACUOUS as written in the
    first revision of this file: `_render_regions` defines `template_prose` as
    `render − Σ(regions)`, so the sum equals the render for ANY region
    arithmetic — a region dropped or miscounted is absorbed into the prose and
    the equality still holds. So the exhaustive property is pinned on its two
    real parts instead:

      1. the region MAP is exactly the renderer's fill map (a spliced value
         that is not a key is a region attributed to the wrong party);
      2. each region's size is the size of ITS OWN value (a wrong arithmetic
         shows up here, not in the residual);
      3. the prose is the template with the slots EMPTIED — measured
         independently of the regions, so it can disagree with them.
    """
    from tortoise import extractor_v2 as X
    enc = m._encoding()
    n = lambda text: len(enc.encode(text or ""))  # noqa: E731
    statics = m._render_regions()
    master = X.build_master_list()
    date = "2026-11-21T00:00:00+00:00"
    # S1 is the stage whose slots are counted BY HAND (S2/S4 come from the
    # renderer's own fill map), so a NEW `{slot}` in S1_TMPL is the live risk:
    # it would render unsubstituted and be counted as prose.
    assert set(re.findall(r"\{(\w+)\}", X.S1_TMPL)) == {
        "memory_granularity", "date_anchor"}, (
        "S1_TMPL grew a placeholder; `_render_regions`' hand-counted S1 branch "
        "does not know it, so its tokens would be filed as template prose")

    # S1 substitutes with `.replace`; its two slots are real, and the prose is
    # the template with both slots blanked.
    granularity = X._granularity_text(master)
    anchor = X._date_anchor(date)
    prose_s1 = (X.S1_TMPL.replace("{memory_granularity}", "")
                .replace("{date_anchor}", ""))
    assert set(statics["s1"]) == {"memory_granularity", "date_anchor",
                                  "template_prose"}
    assert statics["s1"]["memory_granularity"] == n(granularity)
    assert statics["s1"]["date_anchor"] == n(anchor)
    assert statics["s1"]["template_prose"] == pytest.approx(n(prose_s1), abs=8)

    # S2/S4: the region map is the renderer's OWN fill map, captured by
    # wrapping `_fill_template`. Compare each region against an independent
    # computation of the same value, and the prose against the template with
    # every slot emptied.
    captured: list[dict] = []
    orig = X._fill_template

    def _fill(tmpl, values):
        captured.append({"tmpl": tmpl, "values": dict(values)})
        return orig(tmpl, values)

    X._fill_template = _fill
    try:
        X.render_s2_prompt(master, session_date=date, story="", edus=None)
        X.render_s4_prompt("", {}, {}, master, session_date=date, edus=None)
    finally:
        X._fill_template = orig
    for stage, rec in zip(("s2", "s4"), captured, strict=True):
        # (1) every spliced value is a region, and every region is a spliced
        # value (plus the prose row) — nothing can hide.
        assert set(statics[stage]) == set(rec["values"]) | {"template_prose"}, (
            f"{stage}: region map and fill map disagree — "
            f"{set(statics[stage]) ^ (set(rec['values']) | {'template_prose'})}")
        # (2) each region is the size of its own value.
        for name, value in rec["values"].items():
            assert statics[stage][name] == n(value), (stage, name)
        # (3) the prose is measured INDEPENDENTLY: the same template with
        # every slot emptied, so it cannot be the residual of (2).
        blank = orig(rec["tmpl"], dict.fromkeys(rec["values"], ""))
        assert statics[stage]["template_prose"] == pytest.approx(n(blank), abs=12), (
            f"{stage}: template prose {statics[stage]['template_prose']} vs "
            f"independently-rendered {n(blank)} — a splice leaked into prose")


def test_the_kind_vocabulary_is_a_measured_region_not_prose():
    """The master render is the region the issue names; it must be separable."""
    statics = m._render_regions()
    assert statics["s2"]["master_list"] > 1000, statics["s2"]
    assert statics["s4"]["master_list"] == statics["s2"]["master_list"], (
        "S2 and S4 render the SAME vocabulary — two different sizes would mean "
        "the capture paired a region with the wrong stage")


def test_every_document_region_is_charged_once_per_call():
    """A per-document figure must use the measured call counts, not one call.

    S1 makes more than one call per document (68% of the corpus does), and the
    static prompt is paid on EACH — a roll-up that assumed one call per stage
    would understate the constant by ~40% on S1 and by the retry rate on S2/S4.
    """
    dec = m.decompose(_evidence_rows())
    assert dec["calls_per_doc"]["s1"] > 1.0
    s1_slot = dec["statics"]["s1"]["memory_granularity"]
    rounds = next(v for k, v in dec["regions"].items()
                  if k.startswith("memory granularity"))
    assert rounds == pytest.approx(dec["calls_per_doc"]["s1"] * s1_slot,
                                   rel=0.02)


def test_the_composition_sums_and_states_its_unattributed_remainder():
    """Every region in the published breakdown is accounted for, and the
    remainder is disclosed rather than pro-rated.

    ⛔ `composition_total` IS `sum(regions.values())` and `unattributed` IS
    `fitted - total` (`tools/extractor_cost_measure.py:1940-1942`), so asserting
    those two relationships is an identity and cannot fail. What CAN fail — and
    is the property the table needs — is that no region silently goes MISSING
    from the breakdown (a dropped `regions` entry leaves both identities intact
    and only shrinks the total), so the labels are pinned exactly.
    """
    dec = m.decompose(_evidence_rows())
    assert set(dec["regions"]) == {
        "**S2's embed list, re-sent to S4**",
        "**S1's story, re-sent to S2 and S4**",
        "kind vocabulary (`master_list`)",
        "stage instructions (template prose)",
        "mapping rules (`anti_routine`)",
        "output schema (`output_contract`)",
        "memory granularity (S1 only)",
        "date anchor",
        "chains",
        "graph search results (S4)"}
    # The remainder is stated, not hidden: it is the OLS intercept's leverage,
    # and a table that closed the gap by construction would be a rounder lie.
    assert abs(dec["unattributed"]) < 0.25 * dec["fitted_constant"]


def test_the_reemitted_embed_list_is_the_largest_single_region():
    """#14343's finding, pinned: the biggest input term is the pipeline's OWN
    emission re-read, not any static instruction set."""
    dec = m.decompose(_evidence_rows())
    ranked = list(dec["regions"].items())
    assert ranked[0][0].startswith("**S2's embed list"), ranked[:3]
    assert dec["solved"]["embed_over_emission"] > 1.0, (
        "S4 serializes S2's list at indent=1 and after the normaliser; a ratio "
        "<= 1 would mean the region is smaller than the emission, which the "
        "measured prompts contradict")


def test_the_output_constant_is_dominated_by_the_mapping_stage():
    dec = m.decompose(_evidence_rows())
    assert dec["output"]["s2_share"] > 50.0, dec["output"]


def test_decompose_refuses_an_arm_the_evidence_does_not_carry():
    with pytest.raises(SystemExit):
        m.decompose(_evidence_rows(), arm="tepid")


def test_the_documented_reproduce_command_regenerates_the_committed_bundle(
        monkeypatch):
    """The bundle's README says these two files are reproducible; pin it.

    An evidence bundle whose own documented command cannot regenerate it is a
    false pass on the one property the bundle exists to carry. The README used
    to omit `--receipts`, which silently dropped the whole serialization
    section — the headline 32.5% finding — while still exiting 0. The command
    below is the README's, verbatim.

    The one env pin is deliberate: `tests/conftest.py` sets
    `TORTOISE_S4_DELTA=0` to keep the suite on the LEGACY complete-list
    contract, while PRODUCTION defaults it ON — and the two render different
    S4 prose (1,903 vs 1,666 tokens). The bundle was generated under the
    production default, so the pin is restored here (the pattern conftest
    documents for exactly this). Without it the comparison fails on the S4
    statics and the whole embed-list region, and a reader would reasonably
    conclude the artifacts had rotted.
    """
    monkeypatch.delenv("TORTOISE_S4_DELTA", raising=False)
    # ⛔ The other render switches are AMBIENT too: a dev shell in compact mode
    # (`TORTOISE_EXTRACTOR_PROMPT=compact`), in classify-later
    # (`TORTOISE_CLASSIFY_LATER=1`), or with the referentiality contract armed
    # (`TORTOISE_REFERENTIALITY=1`) renders different S1/S2 statics, and without
    # these pins the failure would read as a rotted artifact. The bundle was
    # generated under the production defaults. The `TORTOISE_REFERENTIALITY`
    # pin is the FOURTH, found by measuring rather than by reading the three the
    # README named: measured, it moves `output_contract` 951 -> 1,019 and the
    # S1 story 2,656 -> 2,588. `docs/evidence/7949-capstone/probes/
    # capstone_measure.py`'s `DOOR_PINNED_OFF` already listed it as an ambient
    # prompt-contract switch, which is why the `#14343` capstone session — the
    # one the README sends a re-runner into — is exactly the session that
    # carries it.
    monkeypatch.delenv("TORTOISE_EXTRACTOR_PROMPT", raising=False)
    monkeypatch.delenv("TORTOISE_CLASSIFY_LATER", raising=False)
    monkeypatch.delenv("TORTOISE_REFERENTIALITY", raising=False)
    # ⛔ The FIFTH is a PATH knob, not a posture flag, which is why the four
    # above named only flags. `TORTOISE_PACKS_DIR` pointing at a valid
    # alternative pack tree renders a different `master_list` (3,187 → 2,601)
    # and `memory_granularity` (923 → 845), so a shell carrying it reds this pin
    # with the same false-red "the artifact rotted" message the other four were
    # pinned for (measured, review cycle 6: the set was instrumented and this
    # was the one name of the nine it consults that still reached the pin).
    # `packs/` must resolve from the in-tree tree — `tortoise/domain_loader.py`
    # is where the env leg is read.
    monkeypatch.delenv("TORTOISE_PACKS_DIR", raising=False)
    bundle = _REPO_ROOT / "docs" / "evidence" / "14343-amplify"
    committed = json.loads((bundle / "decomposition.json").read_text())
    regenerated = m.decompose(
        m.load_rows(_REPO_ROOT / "docs" / "evidence" / "14277-extractor-cost"
                    / "measurements.jsonl"),
        receipts=(_REPO_ROOT / "tests" / "eval" / "write_path" / "receipts"
                  / "7947-arms-2026-10-18"))
    assert regenerated == committed, (
        "the documented reproduce command no longer regenerates the committed "
        "decomposition — regenerate the artifact or fix the command")
    # …and the MARKDOWN too: it is committed and claimed regenerated, so a pin
    # that only compared the JSON would let it rot in place.
    assert (m.finalize_markdown(m.render_decompose(regenerated))
            == (bundle / "decomposition.md").read_text(encoding="utf-8")), (
        "decomposition.md is stale against the tool — the JSON pin is not "
        "enough, the markdown is the artifact a reader actually reads")


def test_an_explicitly_passed_receipts_path_that_yields_nothing_is_refused(
        tmp_path):
    """An explicit `--receipts` that matches nothing must not quietly emit an
    artifact whose serialization section is missing — that is the same false
    pass as omitting the flag from the documented command."""
    with pytest.raises(SystemExit, match="no usable write-path receipts"):
        m.main([
            "decompose",
            "--evidence", str(_REPO_ROOT / "docs" / "evidence"
                              / "14277-extractor-cost" / "measurements.jsonl"),
            "--receipts", str(tmp_path / "does-not-exist"),
            "--out", str(tmp_path / "out.md"),
            "--json-out", str(tmp_path / "out.json"),
        ])


def test_omitting_the_receipts_flag_is_REFUSED_not_a_quieter_artifact(
        tmp_path, capsys):
    """`#14343` review F2: OMITTING `--receipts` used to emit the artifact at exit 0.

    The flag is the only source of the `serialization` section — the 32.5 %
    finding — so a decomposition without it is the same false pass as a path
    that matches nothing, only harder to notice: the artifact exists, the
    headline row is absent, and nothing says so. This bundle demonstrated the
    defect in its own README, where the reproduce command passed the flag and
    the `--arm after` recipe did not (two documented commands, opposite
    postures). The flag is now `required=True`; this pins it, so deleting that
    cannot bring the silent artifact back.
    """
    with pytest.raises(SystemExit) as exc:
        m.main([
            "decompose",
            "--evidence", str(_REPO_ROOT / "docs" / "evidence"
                              / "14277-extractor-cost" / "measurements.jsonl"),
            "--out", str(tmp_path / "out.md"),
        ])
    assert exc.value.code == 2, (
        f"argparse must refuse a missing required flag with exit 2, got {exc.value.code}")
    assert "--receipts" in capsys.readouterr().err, (
        "the refusal must NAME the missing flag — a bare usage line leaves the "
        "reader to guess which of the three paths the tool wanted")
    assert not (tmp_path / "out.md").exists(), (
        "a refused decompose must write no artifact")


def test_the_offline_compose_arm_makes_no_provider_call():
    """The decomposition must be runnable with no key — that is what makes it
    reviewable. `--offline` swaps the transport, never the render."""
    rows = m.compose_one(doc={"path": "synthetic", "tokens": 3,
                              "text": "one two three"}, live=False)
    stages = {c["stage"] for c in rows["calls"]}
    assert stages == {"s1", "s2", "s4"}, stages
    assert all(c["prompt_tokens"] == 0 for c in rows["calls"]), (
        "the stub reports no provider counts; a non-zero one would be a "
        "fabricated measurement")
    # and the composition still attributes every token of the real render
    report = m.compose_report([rows])
    for stage, acc in report["rows"][0]["stages"].items():
        assert acc["system_tokens"] > 0, stage


def test_a_transcript_tail_does_not_blank_the_region_map():
    """#14343 review finding: S2/S4 append the SOURCE TRANSCRIPT **outside**
    `_fill_template`, so pairing a call to its render by string EQUALITY
    silently produced an empty region map for every small item — and the table
    still summed, because the whole prompt then prints as one prose row.

    The fixture here is deliberately tiny ("one two three": the EDUs fit
    `_SOURCE_TRANSCRIPT_CAP`, so the tail IS present), and the assertion is on
    the region map, not on the row count.
    """
    art = m.compose_one(doc={"path": "synthetic", "tokens": 3,
                             "text": "one two three"}, live=False)
    by_stage = {c["stage"]: c for c in art["calls"]}
    for stage in ("s2", "s4"):
        regions = by_stage[stage]["regions"]
        assert regions, (
            f"{stage}: the region map is empty — the call was not paired with "
            f"its render (prefix match, not equality)")
        assert "master_list" in regions, regions
        assert regions["master_list"] > 1000, regions
    # and the tail is attributed to itself rather than to the template: the
    # EDUs fit the cap, so `_render_source_transcript` returned a block and that
    # block is a region of its own (a legitimately-empty slot like
    # `pack_namespaces` in the verbose render is allowed to be 0).
    assert by_stage["s2"]["regions"].get("source_transcript", 0) > 0, (
        "the SOURCE TRANSCRIPT tail is missing from the region map — it is "
        "exactly the part that equality-pairing used to swallow")
    assert by_stage["s4"]["regions"].get("source_transcript", 0) > 0


def test_the_fitted_constant_is_derived_from_the_arm_not_hardcoded():
    """The bundle's own completion recipe runs `--arm after`; a literal
    intercept would report the BEFORE constant as the AFTER one and absorb the
    lever's whole saving into `unattributed`."""
    rows = _evidence_rows()
    docs = [r for r in rows if r.get("arm") == "cold" and r.get("kind") == "doc"
            and not r.get("failed")]
    cold = m.decompose(rows, arm="cold")

    # ⛔ The first revision of this test was VACUOUS: it relabelled the same
    # data, so both arms saw identical rows, and the expected value happened to
    # BE the old literal (45,503) — restoring `fitted = 45_503` left it green.
    # So mutate the DATA instead: dropping the one high-leverage document moves
    # this arm's own intercept 45,503 → 30,156, which a literal cannot follow.
    longest = max(docs, key=lambda r: r["item_tokens"])
    without = [r for r in rows if r is not longest]
    lean = m.decompose(without, arm="cold")
    assert lean["fitted_constant"] != cold["fitted_constant"], (
        "the fitted constant did not move when the data did — it is a literal")
    assert cold["fitted_constant"] == round(
        m._fit([(r["item_tokens"], r["prompt_tokens"]) for r in docs])["intercept"])
    assert lean["fitted_constant"] == round(
        m._fit([(r["item_tokens"], r["prompt_tokens"])
                for r in docs if r is not longest])["intercept"])

    # ⛔ The SLOPE was the same defect one line later: `fitted` came from
    # `_fit()` while `document_term.fitted_slope_x_median_doc` stayed a literal
    # `2.159`, so the bundle's own `--arm after` recipe printed the BEFORE
    # slope as the AFTER arm's. Pinned separately because a green intercept pin
    # says nothing about it — cycle 3's reviewer caught exactly that gap.
    for arm_rows, arm in ((rows, cold), (without, lean)):
        sel = [r for r in arm_rows if r.get("arm") == "cold"
               and r.get("kind") == "doc" and not r.get("failed")]
        want = m._fit([(r["item_tokens"], r["prompt_tokens"])
                       for r in sel])["slope"]
        assert arm["document_term"]["fitted_slope_x_median_doc"] == round(
            want * statistics.median([r["item_tokens"] for r in sel]), 1)
        assert arm["input_fit"]["slope"] == round(want, 3)


def test_a_retried_stage_keeps_its_region_map(monkeypatch):
    """A stage RENDERS ONCE and may CALL TWICE — the retry must still be paired.

    ⛔ `render_s2_prompt` / `render_s4_prompt` are called ONCE per stage, in
    `run_s2` / `run_s4`, and the rendered system prompt is what they hand to
    `_complete_parsed` — which re-sends the IDENTICAL `system` on the #2134
    truncation escalation (the `#2134 one-shot escalation` banner in
    `_complete_parsed`) and on the #1746 parse re-prompt
    (`_error_informed_reprompt`). Cited by NAME, never by line: three of the
    four line numbers this docstring used to carry had rotted onto
    `from tortoise.config import is_db_uri`, a gap-fill comment, and a
    `raise _ParseError(`, none of which a reader could check the claim against
    (measured, review cycle 6). A pairing that CONSUMES the
    render therefore leaves the retry unmatched: `match` stays None,
    `regions == {}`, and `render_compose` prints that whole prompt as one
    `*template prose*` row — the table still sums, so nothing looks wrong.

    This is the shape the offline arm could not previously reach (the stub
    never retried), which is why the fix shipped without a pin. The stub is
    made to emit one unparseable S2 response here, which is the real retry
    trigger.
    """
    seen = {"systems": []}

    class _FlakyStub(m._StubModel):
        def complete(self, *, system, user, max_tokens=None, **kwargs):
            if (system or "").startswith("You are the GRAPH MAPPER"):
                seen["systems"].append(system)
                if len(seen["systems"]) == 1:
                    # unparseable, finish_reason="stop" ⇒ the #1746 ladder
                    # re-prompts; a "length" would escalate instead.
                    return "not the JSON contract, so the parse fails"
            return super().complete(system=system, user=user, **kwargs)

    monkeypatch.setattr(m, "_StubModel", _FlakyStub)
    art = m.compose_one(doc={"path": "synthetic", "tokens": 3,
                             "text": "one two three"}, live=False)
    s2 = [c for c in art["calls"] if c["stage"] == "s2"]
    assert len(s2) == 2, (
        f"the S2 retry never happened ({len(s2)} call(s)) — without it this "
        f"pin is vacuous and proves nothing about retried stages")
    # `compose_one` drops `system` from the artifact (it is not part of the
    # report), so the identity is read from the transport, not the output.
    assert len(seen["systems"]) == 2, seen["systems"]
    assert seen["systems"][0] == seen["systems"][1], (
        "the retry must re-send the IDENTICAL system prompt; if it differs, "
        "the consumed-once pairing was not the bug this test names")
    for n, call in enumerate(s2, start=1):
        assert call["regions"], (
            f"S2 call {n} of {len(s2)} has an EMPTY region map — the render "
            f"was consumed by the first call, so this prompt would be printed "
            f"as one prose row while the table still summed")
        assert call["regions"].get("master_list", 0) > 1000, call["regions"]


def test_the_user_message_is_charged_as_its_own_region():
    """The user turn is a prompt region, and for S1 it is the whole document.

    ⛔ `render_compose` totals `system_tokens + user_tokens` but counted only
    the `_fill_template` slots, so the user message — S1's document (~5.2k of
    a ~5.9k "prose" figure whose real prose is 629), and S2's framed story,
    the very region `decompose` calls `**S1's story, re-sent to S2 and S4**` —
    landed in the `*template prose + boundaries*` residual. Same file, two
    tools, same tokens attributed to two different parties.
    """
    # A document-sized user turn, not three words: the finding is that S1's
    # DOCUMENT was printed as template prose, which only shows at size.
    text = ("The project moved its release date, then shipped the migration "
            "ahead of the audit. ") * 120
    art = m.compose_one(doc={"path": "synthetic", "tokens": 1_800,
                             "text": text}, live=False)
    for call in art["calls"]:
        assert call["regions"].get("user_message") == call["user_tokens"], (
            f"{call['stage']}: the user turn is not charged as a region "
            f"(regions={sorted(call['regions'])})")
        named = sum(call["regions"].values())
        assert named <= call["system_tokens"] + call["user_tokens"], (
            f"{call['stage']}: regions claim more than the prompt spent "
            f"({named} > {call['system_tokens']} + {call['user_tokens']})")
    report = m.compose_report([art])
    s1 = report["rows"][0]["stages"]["s1"]
    prose = (s1["system_tokens"] + s1["user_tokens"]
             - sum(s1["regions"].values()))
    assert s1["regions"]["user_message"] > prose, (
        f"S1's user message is the document and must dominate its prose "
        f"residual; got user_message={s1['regions']['user_message']} "
        f"vs prose={prose} — a missing user region hides the document here")


# ── the stub marker, and the fold's arithmetic ──────────────────────────────
# `7b2945619a1e` shipped four behaviour changes — `live` propagated into the
# report, the OFFLINE banner, `chars_sum` folding instead of `item_chars`, and
# the `distinct_kinds` union — with NO assertion on any of them: reverting all
# four left the tree green (measured, review cycle 6). A bundle whose argument is
# that a stub must never read as a measurement cannot pin that with prose.

def _compose_artifact(*, live: bool, calls: list[dict],
                     errors: list[str] | None = None) -> dict:
    """The minimal artifact `compose_report` folds (its own shape, no provider)."""
    return {"doc": {"path": "synthetic", "item_tokens": 12, "tokens": 12},
            "live": live, "calls": calls,
            "pipeline": {"calls": len(calls), "errors": list(errors or [])}}


def _call(*, sections: dict, regions: dict | None = None,
          stage: str = "s2") -> dict:
    return {"stage": stage, "system_tokens": 3, "user_tokens": 4,
            "completion_tokens": 5, "regions": regions or {"master_list": 3},
            "output": {"tokens": 5, "sections": sections}}


def _complete_artifact(*, live: bool = True) -> dict:
    """All three stages observed and no pipeline error — a real measurement."""
    return _compose_artifact(live=live,
                             calls=[_call(stage=s, sections={})
                                    for s in ("s1", "s2", "s4")])


def test_a_stub_run_renders_the_marker_and_a_live_run_does_not():
    """The one property the bundle exists to carry: a stub must SAY it is one.

    Both arms, because an assertion on the stub arm alone would pass for a
    renderer that always prints the banner — which would devalue every measured
    table in the same file.
    """
    stub = m.compose_report([_compose_artifact(live=False, calls=[_call(sections={})])])
    live = m.compose_report([_complete_artifact()])
    assert stub["live"] is False, "a stub artifact must report live=False"
    assert live["live"] is True, "a measured artifact must report live=True"
    assert "NOT A MEASUREMENT" in m.render_compose(stub), (
        "an --offline run rendered WITHOUT the stub marker — the table is then "
        "byte-shape identical to a measured one, which is the false pass the "
        "bundle exists to prevent")
    assert "NOT A MEASUREMENT" not in m.render_compose(live), (
        "a MEASURED run must not carry the stub marker")


def test_a_report_without_a_live_key_renders_as_a_stub():
    """⛔ FAIL-CLOSED on unknown provenance.

    `report.get("live", True)` rendered a report that does not carry the key
    with NO marker — the fail-open direction, where unknown provenance reads as
    a measurement. Latent only because today's one caller always supplies it.
    """
    report = m.compose_report([_compose_artifact(live=False, calls=[_call(sections={})])])
    del report["live"]
    assert "NOT A MEASUREMENT" in m.render_compose(report), (
        "a report with NO `live` key rendered without the stub marker — "
        "unknown provenance must never read as a measurement")


def test_merged_sections_add_the_halves_and_union_the_full_kind_histogram():
    """Two calls fold additively, and `distinct_kinds` is the UNION's size.

    `chars_sum`/`items` are the two additive halves of a per-item mean; the
    previous revision folded a per-call `item_chars` MEAN and published the sum
    of two means. And `distinct_kinds` was read off the top-12 display slice, so
    13 kinds in one call plus 5 more in another published **12** — fewer kinds
    than EITHER call knew. Both mutations leave the rest of the suite green.
    """
    first = _call(sections={"points": {
        "items": 2, "chars_sum": 100, "tokens": 7,
        "kinds": {f"k{i}": 1 for i in range(13)}, "distinct_kinds": 13}})
    second = _call(sections={"points": {
        "items": 1, "chars_sum": 50, "tokens": 3,
        "kinds": {f"k{i}": 1 for i in range(13, 18)}, "distinct_kinds": 5}})
    report = m.compose_report([_compose_artifact(live=True, calls=[first, second])])
    points = report["rows"][0]["stages"]["s2"]["output"]["sections"]["points"]
    assert (points["chars_sum"], points["items"]) == (150, 3), (
        f"chars_sum/items must fold additively, got "
        f"chars_sum={points['chars_sum']} items={points['items']}")
    assert len(points["kinds"]) == 18, (
        f"the merged histogram must carry the full union, got "
        f"{len(points['kinds'])} kinds — a 12-cap hides kinds a call knew")
    assert points["distinct_kinds"] == 18, (
        f"distinct_kinds must be the union's size (18), got "
        f"{points['distinct_kinds']} — read off the display slice it reports 12")
    assert points["distinct_kinds"] == len(points["kinds"]), (
        "the published count and the published histogram must agree")


def test_the_rendered_table_reads_the_published_chars_and_kind_keys():
    """`chars_sum` / `distinct_kinds` must have a READER, not just a JSON home.

    Both keys were published by `_output_structure`/`_merge_sections` and printed by
    NOTHING — the tables above assert they exist and fold correctly, and a reader who
    wanted the number had to open the JSON (measured, review cycle 7 P3). A derived key
    nobody renders is free to be wrong, which is why the module's own comment at
    `_output_structure` criticises exactly that shape one function over.

    The mutation this reds: drop either `bits.append(...)` arm from `render_compose`.
    """
    report = m.compose_report([_compose_artifact(live=True, calls=[_call(sections={
        "points": {"items": 2, "chars_sum": 100, "tokens": 7,
                   "kinds": {"a": 1, "b": 1}, "distinct_kinds": 2}})])])
    table = m.render_compose(report)
    assert "100 chars" in table, (
        "the rendered table must READ `chars_sum` — it is published and folded, so a "
        f"readerless key is a number nothing can see. Table:\n{table}")
    assert "2 kinds" in table, (
        "the rendered table must READ `distinct_kinds` — a published count with no "
        f"reader is the shape the module criticises. Table:\n{table}")
    assert "2 items" in table and "7 tok" in table, (
        f"the pre-existing columns must survive the new ones. Table:\n{table}")
# ── a LIVE run is not automatically a MEASUREMENT ───────────────────────────
# The provenance gate above tells a STUB transport from a live one and says
# nothing about a live run whose stages RAISED. `_Recording.complete` appends to
# `calls` only after the adapter RETURNS, so a failing stage contributes no call
# — and the rendered table then shows the survivors as the whole run (review
# cycle 7, P1). Measured on a box with no provider key: three empty tables, no
# marker, exit 0.

def test_a_live_run_that_lost_a_stage_renders_the_incomplete_marker():
    """A partial live run must SAY it is partial — the whole point of the bundle.

    Both arms, because a renderer that always prints the banner would devalue
    every complete table in the same file.
    """
    complete = _complete_artifact()
    assert "INCOMPLETE" not in m.render_compose(m.compose_report([complete])), (
        "a complete live run must not carry the INCOMPLETE marker")

    # S4 raised: it emitted no call, and the pipeline recorded the error.
    partial = _complete_artifact()
    partial["calls"] = [c for c in partial["calls"] if c["stage"] != "s4"]
    partial["pipeline"] = {"calls": 2, "errors": ["s4: 429 Too Many Requests"]}
    rendered = m.render_compose(m.compose_report([partial]))
    assert "INCOMPLETE LIVE RUN" in rendered, (
        "a live run missing a stage rendered with no marker — the surviving "
        "rows then read as a smaller, complete measurement")
    assert "NOT A MEASUREMENT" in rendered, (
        "the incomplete banner must carry the same refusal as the stub one")
    assert "`s4`" in rendered, (
        "the banner must NAME the missing stage, or a reader cannot tell what "
        "the table omits")


def test_a_missing_stage_is_incomplete_even_with_no_pipeline_error():
    """⛔ FAIL-CLOSED on the call/error bookkeeping, not just on a recorded error.

    A stage can be absent while `errors` is empty (a swallowed exception, or a
    pipeline that stopped early). An `errors`-only rule would render that run as
    a measurement, so completeness is its own predicate.
    """
    partial = _complete_artifact()
    partial["calls"] = [c for c in partial["calls"] if c["stage"] != "s1"]
    partial["pipeline"] = {"calls": 2, "errors": []}
    report = m.compose_report([partial])
    assert report["complete"] is False, (
        "a run missing `s1` with no recorded error must still be incomplete")
    assert "INCOMPLETE LIVE RUN" in m.render_compose(report)


def test_a_report_whose_calls_were_lost_is_incomplete():
    """The pipeline counted more LLM calls than the instrument recorded."""
    lost = _complete_artifact()
    lost["pipeline"] = {"calls": 7, "errors": []}
    assert m.compose_report([lost])["complete"] is False, (
        "more pipeline calls than recorded calls means calls were lost")


def test_a_retried_stage_is_not_mistaken_for_lost_calls():
    """⛔ The OVER-BLOCK: `pipeline.calls` counts ATTEMPTS, not successes.

    `_rollup_llm` does `llm_stats["calls"] += stage_stats.get("attempts", 0)`
    (`tortoise/extractor_v2.py:16719`) and `_complete` sets `attempts=attempt`
    only on the SUCCESSFUL attempt, so a transient 429/5xx that succeeds on
    retry adds +1 to `pipeline.calls` with no call recorded by `_Recording`.
    Comparing the two without the retry count flagged a fully-measured run as
    INCOMPLETE and made `compose` exit 1 — refusing a valid artifact and
    sending the next lane to re-spend until it got a retry-free run.
    """
    retried = _complete_artifact()
    retried["pipeline"] = {"calls": 4, "retries": 1, "errors": []}
    report = m.compose_report([retried])
    assert report["complete"] is True, (
        "a run whose only excess is one recorded RETRY is complete")
    assert "INCOMPLETE" not in m.render_compose(report)
    # …and the excess is still a verdict when no retry explains it.
    unexcused = _complete_artifact()
    unexcused["pipeline"] = {"calls": 4, "retries": 0, "errors": []}
    assert m.compose_report([unexcused])["complete"] is False, (
        "an excess that retries do NOT explain is still lost calls")


def test_the_compose_command_exits_nonzero_on_an_incomplete_live_run(
        monkeypatch, tmp_path):
    """⛔ The exit code is the only machine-readable signal a scripted re-runner
    has. Deleting the `return 1` in main's `compose` branch left every other pin
    green while the process reported SUCCESS over a table that says
    "NOT A MEASUREMENT" (review cycle 7, P2).

    Three arms: a complete live run (0), a live run missing a stage (1), and
    `--offline` (0 — a documented shape check with its own banner).
    """
    monkeypatch.setattr(m, "pick_corpus",
                        lambda **kw: [{"path": "README.md", "tokens": 10}])

    def _run(artifact, *, offline=False):
        monkeypatch.setattr(m, "compose_one", lambda **kw: artifact)
        argv = ["compose", "--doc", "README.md", "--out",
                str(tmp_path / "compose.json")]
        if offline:
            argv.append("--offline")
        return m.main(argv)

    assert _run(_complete_artifact()) == 0, "a complete live run must exit 0"

    partial = _complete_artifact()
    partial["calls"] = [c for c in partial["calls"] if c["stage"] != "s4"]
    partial["pipeline"] = {"calls": 2, "errors": ["s4: 429"]}
    assert _run(partial) == 1, (
        "a LIVE run missing a stage must exit non-zero — exit 0 is how a "
        "failed run wrote an artifact that read as a measurement")

    stub = _compose_artifact(live=False, calls=[_call(sections={})])
    assert _run(stub, offline=True) == 0, (
        "--offline is exempt: it is a documented shape check")


def test_a_stub_run_is_exempt_from_the_incomplete_marker():
    """`--offline` is a documented SHAPE check: it has its own banner and must
    not be double-flagged, or the two markers become indistinguishable."""
    stub = _compose_artifact(live=False, calls=[_call(sections={})])
    rendered = m.render_compose(m.compose_report([stub]))
    assert "OFFLINE / STUB TRANSPORT" in rendered
    assert "INCOMPLETE LIVE RUN" not in rendered


def test_the_source_transcript_tail_is_subtracted_before_the_per_call_medians():
    """⛔ `_render_regions` renders with `edus=None`, so it carries NO transcript
    tail — while `render_s2_prompt` / `render_s4_prompt` append
    `_render_source_transcript(edus)` OUTSIDE `_fill_template` whenever the
    document's EDU text fits `_SOURCE_TRANSCRIPT_CAP`. The residual solve
    `story = pc2 - s2_sys - clause` therefore booked that block into
    "S1's story, re-sent to S2 and S4" until the per-call medians subtracted it.

    ⛔ AND IT WAS UNPINNED: reverting `pc.append(... - tail)` to the raw median
    and re-running the documented command left every test green, because
    `test_the_documented_reproduce_command_regenerates_the_committed_bundle`
    compares artifact-to-code, never code-to-truth (review cycle 7, P1). This is
    the code-to-truth half: the published per-call figure must be STRICTLY below
    the raw median it was derived from, and the tail's own count must be the
    measured one.
    """
    rows = m.load_rows(_REPO_ROOT / "docs" / "evidence" / "14277-extractor-cost"
                       / "measurements.jsonl")
    dec = m.decompose(
        rows,
        receipts=(_REPO_ROOT / "tests" / "eval" / "write_path" / "receipts"
                  / "7947-arms-2026-10-18"))
    assert dec["source_transcript"]["documents_with_a_tail"] == 9, (
        "the number of corpus documents carrying a SOURCE TRANSCRIPT tail moved "
        "— re-measure it rather than assuming the subtraction is still right")
    docs = [r for r in rows
            if r.get("arm") == "cold" and r.get("kind") == "doc"
            and not r.get("failed") and (r.get("stages") or {}).get("s2")]
    raw = statistics.median(
        r["stages"]["s2"]["prompt_tokens"] / r["stages"]["s2"]["calls"]
        for r in docs)
    assert dec["per_call"]["s2"]["prompt"] < raw, (
        f"the published S2 per-call prompt ({dec['per_call']['s2']['prompt']}) "
        f"is not below the raw median ({raw}) — the transcript tail is back "
        f"inside the story row, which is the misattribution this pins out")



# ── #14343 review cycle 10: the arm is a SETTING, and the setting is
#    PROCESS-GLOBAL — so it must be installed per BATCH, never per call ────────

def test_embed_mode_reads_the_environment_instead_of_a_label(monkeypatch):
    """`_embed_mode` must READ the env, not re-encode an arm name.

    The recorded field exists so a mislabelled row is VISIBLE. A value derived
    from the label (`"compact" if arm == "after" else "indent"`) is a function of
    that label, so it can never disagree with it — and so can never catch the
    defect the field was added for. The resolution must be the one production
    uses (`extractor_v2._embed_json_kwargs`): only `compact` arms it.
    """
    for value, expected in ((None, "indent"), ("", "indent"),
                            ("   ", "indent"), ("garbage", "indent"),
                            ("indent", "indent"),
                            ("compact", "compact"), (" COMPACT ", "compact")):
        if value is None:
            monkeypatch.delenv(m._TOGGLE, raising=False)
        else:
            monkeypatch.setenv(m._TOGGLE, value)
        assert m._embed_mode() == expected, (
            f"{value!r} must resolve to {expected!r} — the same resolution "
            f"`extractor_v2._embed_json_kwargs` uses at call time")


def test_the_arm_is_installed_once_per_batch_not_once_per_call(
        monkeypatch, tmp_path):
    """⛔ THE REGRESSION THIS PINS (review cycle 10, P2).

    `os.environ` is process-global and shared by every worker thread, so an
    install wrapped around the SINGLE `measure_one` call lets a `before` thread
    pop the flag while an `after` thread is inside its own measure. That is a
    RACE, not an arm — and at the documented `--arms before,after` with the
    DEFAULT `--workers 6` (4 in this test) it reproduces the original false
    pass: a before→after delta that is an artefact of thread scheduling.

    So two arms over eight jobs must install each arm's value exactly ONCE, and
    every row must record the flag its own batch installed.

    ⛔ WHAT THIS DOES AND DOES NOT PROVE. It pins the CONTRACT — one install per
    arm, env-derived per-row record, ambient restored — and it fails if anyone
    moves the install back into the per-call path, because the spy would then
    fire once per JOB (8) instead of once per ARM (2). It does NOT replay the
    original race itself; a timing-based reproduction would be flaky in both
    directions and would fail silently on a fast machine.
    """
    import os
    import time

    docs = [{"path": "README.md", "tokens": 10 + i} for i in range(4)]
    installed: list[str] = []

    def fake_install(arm: str) -> None:
        installed.append(arm)
        if arm == "after":
            os.environ[m._TOGGLE] = "compact"
        elif arm == "before":
            os.environ.pop(m._TOGGLE, None)

    def fake_measure(model, conversation, *, session_date):
        # Hold the call long enough that a per-call install/restore WOULD be
        # interleaved by another worker thread.
        time.sleep(0.02)
        return {"prompt_tokens": 100, "completion_tokens": 10,
                "cache_hit_tokens": 0}

    monkeypatch.setenv(m._TOGGLE, "garbage")   # ambient must come back
    monkeypatch.setattr(m, "pick_corpus", lambda limit, seed: docs)
    monkeypatch.setattr(m, "spool_sessions", lambda limit: [])
    monkeypatch.setattr(m, "_build_model", lambda: object())
    monkeypatch.setattr(m, "_install_arm", fake_install)
    monkeypatch.setattr(m, "measure_one", fake_measure)

    out = tmp_path / "measurements.jsonl"
    m.run_measurement(out=out, docs_limit=4, sessions_limit=0, workers=4,
                      seed=1, arms=("before", "after"), arm_gap_s=0.0,
                      long_split=False)

    # (1) the install is per BATCH — one call per arm, not one per job.
    assert sorted(installed) == ["after", "before"], (
        f"the arm must be installed once per BATCH (2 calls for 2 arms), got "
        f"{installed!r} — an install per JOB is the per-call race (8 jobs)")

    # (2) every row records the flag its OWN arm was installed with. `_job`
    #     reads it back from the environment, so a batch that installed the
    #     wrong value — or nothing — shows up here.
    rows = [json.loads(line) for line in out.read_text().splitlines() if line]
    assert len(rows) == 8, f"expected 8 rows, got {len(rows)}"
    assert {r["arm"] for r in rows} == {"before", "after"}
    for row in rows:
        want = "compact" if row["arm"] == "after" else "indent"
        assert row["s4_embed_json"] == want, (
            f"arm {row['arm']!r} recorded {row['s4_embed_json']!r}, expected "
            f"{want!r} — the flag was not installed for its batch")

    # (3) the ambient value is restored, so a measurement cannot leak the lever
    #     into whatever runs next in the same process.
    assert os.environ.get(m._TOGGLE) == "garbage", (
        "the ambient TORTOISE_S4_EMBED_JSON was not restored after the run — "
        "a measurement must not leave the lever armed for the next caller")


# ── the table must SUMMARISE, and a share must name its denominator ───────────
# Review cycle 9, two P3s, both real and both survived seven earlier cycles:
# (a) the output table printed rows summing to 17,829 against a total of
# 17,830 — the total was formatted from the UNROUNDED sum while each row came
# from its own unrounded value, so banker's rounding split them (a column that
# does not add up, in the one table whose stated rule is "a table that does not
# sum is not evidence"); (b) the input table's `unattributed` row is a share of
# the FITTED constant (2,994 / 45,503 = 6.6 %) while the column header says the
# 42,509 composition, so the column read as 106.6 %. Pinned against the
# artifact, so a correction to the data moves them and a silent edit reds them.

def _committed_decomposition() -> dict:
    return json.loads((_REPO_ROOT / "docs" / "evidence" / "14343-amplify"
                       / "decomposition.json").read_text(encoding="utf-8"))


def _committed_markdown() -> str:
    return (_REPO_ROOT / "docs" / "evidence" / "14343-amplify"
            / "decomposition.md").read_text(encoding="utf-8")


def test_the_output_table_rows_sum_to_its_printed_total():
    """A column that does not add up is the defect the bundle's rule forbids."""
    dec = _committed_decomposition()
    block = _committed_markdown().split(
        "### The output constant, decomposed", 1)[1]
    rows, total = [], None
    for line in block.splitlines():
        if not line.startswith("|"):
            if total is not None:
                break
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) != 3 or set(cells[0]) <= {"-"} or cells[0] == "stage":
            continue
        if "composition total" in cells[0]:
            total = int(cells[1].replace(",", "").replace("*", ""))
            break
        if cells[0] in ("s1", "s2", "s4"):
            rows.append(int(cells[1].replace(",", "")))
    assert len(rows) == 3, f"the three stage rows must be present, got {rows}"
    assert total is not None, "the printed composition total must be present"
    assert sum(rows) == total, (
        f"the printed stage rows sum to {sum(rows):,} but the printed total is "
        f"{total:,} — a column that does not add up is the exact defect this "
        f"bundle's own rule forbids")
    # …and the total is the sum of the ROUNDED rows. Formatting the UNROUNDED
    # sum is precisely how 17,829 became 17,830, so pin the derivation and not
    # just today's arithmetic.
    rounded = sum(round(out) for out in
                  (dec["output"]["s1_tokens_per_doc"],
                   dec["output"]["s2_tokens_per_doc"],
                   dec["output"]["s4_tokens_per_doc"]))
    assert total == rounded, (
        f"the total must be derived from the rounded rows ({rounded:,}), not "
        f"from the unrounded sum — that off-by-one is what this pin exists for")


def test_the_unattributed_row_names_the_denominator_its_share_is_of():
    """`unattributed` is the only row in that column on a second denominator."""
    dec = _committed_decomposition()
    ev = (_REPO_ROOT / "docs" / "evidence" / "14343-amplify" / "README.md"
          ).read_text(encoding="utf-8")
    fit_share = 100.0 * dec["unattributed"] / dec["fitted_constant"]
    comp_share = 100.0 * dec["unattributed"] / dec["composition_total"]
    assert f"{fit_share:.1f} %" in ev, (
        f"the unattributed row is {fit_share:.1f} % of the FITTED constant")
    assert comp_share > fit_share + 0.2, (
        "the two denominators must genuinely differ, or this pin proves nothing")
    assert "of the fitted constant" in ev, (
        "the unattributed row must NAME its denominator: the column header says "
        "the composition, and an unlabelled 6.6 % makes the column read as "
        f"{100.0 + fit_share:.0f} %")
    assert f"{comp_share:.1f} %" in ev, (
        "the README must also state what the row would be against the "
        "composition, so a reader can see the two denominators differ")

def test_a_row_whose_render_missed_its_arm_is_REFUSED_not_mislabelled(
        monkeypatch, tmp_path):
    """Recording the observation is not enough on its own.

    Review cycle 10: a corrupted row was "indistinguishable from a good one".
    Reading the flag back per row makes the corruption VISIBLE in the artifact,
    but a row still written as a good measurement of its arm is evidence a
    reader has to distrust rather than evidence the harness refused it. So the
    two ways the observation can disagree with the arm RAISE, and `run` records
    the row as FAILED — a row that cannot be attributed to an arm is not a
    measurement of that arm.
    """
    def fake_measure(model, conversation, *, session_date):
        return {"prompt_tokens": 1, "completion_tokens": 1}

    monkeypatch.setattr(m, "_install_arm", lambda arm: None)   # installs nothing
    monkeypatch.delenv(m._TOGGLE, raising=False)
    monkeypatch.setattr(m, "pick_corpus",
                        lambda limit, seed: [{"path": "README.md", "tokens": 10}])
    monkeypatch.setattr(m, "spool_sessions", lambda limit: [])
    monkeypatch.setattr(m, "_build_model", lambda: object())
    monkeypatch.setattr(m, "measure_one", fake_measure)

    out = tmp_path / "measurements.jsonl"
    m.run_measurement(out=out, docs_limit=1, sessions_limit=0, workers=1,
                      seed=1, arms=("after",), arm_gap_s=0.0,
                      long_split=False)
    rows = [json.loads(line) for line in out.read_text().splitlines() if line]
    assert len(rows) == 1
    assert rows[0]["failed"] is True, rows[0]
    assert "cannot be attributed to an arm" in rows[0]["error"], rows[0]
    assert "s4_embed_json" not in rows[0], (
        "a refused row must not carry an arm's layout as if it had measured it")


def test_a_lever_that_moves_mid_measurement_is_refused(monkeypatch, tmp_path):
    """One layout per pipeline run, or the row describes neither."""
    def flipping_measure(model, conversation, *, session_date):
        os.environ[m._TOGGLE] = "compact"      # the leak, mid-run
        return {"prompt_tokens": 1, "completion_tokens": 1}

    monkeypatch.setattr(m, "_install_arm", lambda arm: None)
    monkeypatch.delenv(m._TOGGLE, raising=False)
    monkeypatch.setattr(m, "pick_corpus",
                        lambda limit, seed: [{"path": "README.md", "tokens": 10}])
    monkeypatch.setattr(m, "spool_sessions", lambda limit: [])
    monkeypatch.setattr(m, "_build_model", lambda: object())
    monkeypatch.setattr(m, "measure_one", flipping_measure)

    out = tmp_path / "measurements.jsonl"
    m.run_measurement(out=out, docs_limit=1, sessions_limit=0, workers=1,
                      seed=1, arms=("cold",), arm_gap_s=0.0,
                      long_split=False)
    rows = [json.loads(line) for line in out.read_text().splitlines() if line]
    assert rows[0]["failed"] is True, rows[0]
    assert "moved mid-measurement" in rows[0]["error"], rows[0]


def test_the_mode_is_productions_own_resolution_not_a_second_parser(monkeypatch):
    """`_embed_mode` delegates to the helper the RENDER calls.

    A hand-written copy of the rule (`.strip().lower() == "compact"`) agrees
    today and is free to drift the first time the toggle grows a spelling — and
    the drift would surface as a row recording a layout the render did not take.
    Pinned structurally: whatever `_embed_json_kwargs` resolves to, the recorded
    mode names it.
    """
    from tortoise import extractor_v2 as v2
    for value in (None, "", "  ", "garbage", "indent", "compact", " COMPACT "):
        if value is None:
            monkeypatch.delenv(m._TOGGLE, raising=False)
        else:
            monkeypatch.setenv(m._TOGGLE, value)
        compact = v2._embed_json_kwargs() == {"separators": (",", ":")}
        assert m._embed_mode() == ("compact" if compact else "indent"), value

    # THE DISCRIMINATOR: with the env armed, move the RENDER's own resolution to
    # the other layout. A delegation follows it; a second hand-written parser
    # keeps answering "compact" from the environment and the row would then
    # record a layout the prompt did not have.
    monkeypatch.setenv(m._TOGGLE, "compact")
    monkeypatch.setattr(v2, "_embed_json_kwargs", lambda: {"indent": 1})
    assert m._embed_mode() == "indent", (
        "the recorded mode must follow the serialization the render passes to "
        "json.dumps, not a re-parse of the environment")
