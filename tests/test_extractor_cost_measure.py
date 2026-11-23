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

import re
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
    import json as _json

    from tools.extractor_cost_measure import build_report  # noqa: F401
    path = (_REPO_ROOT / "docs" / "evidence" / "14277-extractor-cost"
            / "measurements.jsonl")
    return [_json.loads(line) for line in path.read_text().splitlines()
            if line.strip()]


def test_the_static_regions_of_a_prompt_are_its_own_bytes():
    """Every static region plus the template prose must equal the render.

    The table's first section is only evidence if it is exhaustive: a region
    that the renderer splices but this helper does not count would show up as
    template prose and be attributed to the wrong party.
    """
    from tortoise import extractor_v2 as X
    statics = m._render_regions()
    enc = m._encoding()
    master = X.build_master_list()
    date = "2026-11-21T00:00:00+00:00"

    s1 = (X.S1_TMPL
          .replace("{memory_granularity}", X._granularity_text(master))
          .replace("{date_anchor}", X._date_anchor(date)))
    assert sum(statics["s1"].values()) == len(enc.encode(s1))
    # S2/S4: the region map is captured from the renderer's own fill map, so
    # the equality is the property that the capture is complete.
    for stage, rendered in (("s2", X.render_s2_prompt(master, session_date=date,
                                                      story="", edus=None)),
                            ("s4", X.render_s4_prompt("", {}, {}, master,
                                                      session_date=date,
                                                      edus=None))):
        assert sum(statics[stage].values()) == len(enc.encode(rendered)), stage


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
    """The table's parts must sum to what it claims, residual disclosed."""
    dec = m.decompose(_evidence_rows())
    assert sum(dec["regions"].values()) == pytest.approx(
        dec["composition_total"], abs=1.0)
    assert dec["unattributed"] == pytest.approx(
        dec["fitted_constant"] - dec["composition_total"], abs=1.0)
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
