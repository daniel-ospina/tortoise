"""#3329 — the public capacity page stays bound to the system it describes.

`docs/capacity.md` is the public answer to "what does Tortoise scale to?".
A capacity number is the kind of claim that rots silently: nobody re-runs a
benchmark when a default changes, and the person who finds out is a customer.
The failure this file prevents is not hypothetical, and it is not new —
`docs/quickstart-selfhosted.md` and `.env.example` both said the Docker lane was
"integration-tested to 2 concurrent writers" from 2026-08-13 (`fd9921736`,
#1217) while `tests/test_embedded_concurrency.py` had covered **five** writer
processes since `8d37e6764` (#942/#974), one day earlier. Two months of an
understated figure that nothing could detect, because nothing bound the prose to
the test.

So the page is pinned, not trusted:

1. **Reachability** — the page is linked from `README.md` and
   `website/self-hosted.html`, the two surfaces the issue names.
2. **Sources resolve** — every table row on the page whose last column is
   `Source` must name at least one repo path, and every path it names must
   exist. A number whose artifact was deleted fails here instead of shipping.
3. **The verified figure is derived, not restated** — the published write
   concurrency is read out of the integration test's own coverage and asserted
   equal in every place it is published. Change the test to six writers and the
   docs red, naming the file.
4. **The unmeasured dimensions are still named** — the issue's whole point is
   that "unmeasured" is a published answer, so a page that quietly stops saying
   it fails.

Unconditional (no network, no browser, no DB): plain text reads on committed
files, in the harness style of `tests/test_website_docs_consistency.py`.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

CAPACITY = ROOT / "docs" / "capacity.md"
README = ROOT / "README.md"
QUICKSTART = ROOT / "docs" / "quickstart-selfhosted.md"
SELF_HOSTED_HTML = ROOT / "website" / "self-hosted.html"
FAQ_HTML = ROOT / "website" / "faq.html"
ENV_EXAMPLE = ROOT / ".env.example"
CONCURRENCY_TEST = ROOT / "tests" / "test_embedded_concurrency.py"

#: The one integration test that measures concurrent writers on a live graph.
LIVE_WRITER_TEST = "test_concurrent_writers_live_falkor_no_lost_writes"

#: Every surface that publishes the verified write-concurrency figure. One
#: tuple, shared by BOTH writer scans below: the figure was published from a
#: surface this list had never heard of (`website/faq.html`), so the binding was
#: asleep for it — adding a surface to one list and forgetting the other is the
#: same defect again (#9517 review 5).
_WRITER_SURFACES = (CAPACITY, QUICKSTART, ENV_EXAMPLE, SELF_HOSTED_HTML, FAQ_HTML)

#: The page's GitHub-rendered URL, which is what the website links to.
CAPACITY_URL = (
    "https://github.com/daniel-ospina/tortoise/blob/main/docs/capacity.md"
)

#: Path-like tokens inside a `Source` cell, e.g. ``tortoise/hosted_api.py``.
_PATH_TOKEN = re.compile(r"`([A-Za-z0-9_][\w./-]*\.(?:md|py|json|yml|html|ts|js))`")
#: A `Source` cell that declares the row unmeasured instead of citing an artifact.
#: This is the OTHER half of #3329's requirement — "a source citation **or an
#: explicit 'unmeasured' label**" — so it is an allowed outcome, not an escape:
#: the row has to say the word.
_UNMEASURED_CELL = re.compile(r"unmeasured", re.IGNORECASE)
#: Every phrasing the public surfaces use to publish the verified write-
#: concurrency figure. The #8492 drift entered through a phrasing the guard did
#: not know ("concurrent writers verified at 2 processes"), so this is the union
#: of the wordings in the tree rather than one literal:
#:   "integration-tested to 5 concurrent writers"  (docs)
#:   "integration-tested to 5 writers"             (`.env.example`, FAQ)
#:   "concurrent writers verified at 5 processes"  (quickstart env table)
#:   "verified to 5 concurrent writer processes"   (website)
#:   "5 writer processes on one graph"             (capacity §2)
#: One scan therefore returns **every** published statement of the figure,
#: whichever wording a surface chose.
_PUBLISHED_WRITERS = re.compile(
    r"integration-tested to (\d+) (?:concurrent )?writers"
    r"|verified (?:at|to) (\d+) (?:concurrent )?(?:writer )?process(?:es)?"
    r"|(\d+) (?:concurrent )?writer processes"
)


#: The fewest sourced rows each top-level section of `docs/capacity.md` must
#: keep. Keyed on the heading so each section is checked on its own: the global
#: floor this replaced could not see §6's five rows being deleted while 24 rows
#: elsewhere kept the total above it (#8492 ISSUE 3). Raise a number when you add
#: rows; never lower one without deleting the section.
_SECTION_MIN_ROWS = {
    "## 1. Pick the deployment mode first": 0,  # mode table has no Source column
    "## 2. Write concurrency": 3,
    "## 3. Request rate limits": 5,
    "## 4. Graph size (hosted node cap)": 5,
    "## 5. Retrieval latency": 7,  # §5.1 targets (5) + §5.2 measured paths (2)
    "## 6. Storage footprint per element": 5,
    "## 7. Belief propagation and dreaming": 6,
}


def _published_writer_counts(text: str) -> list[int]:
    """Every write-concurrency number published in ``text`` (any phrasing)."""
    return [
        int(value)
        for match in _PUBLISHED_WRITERS.finditer(text)
        for value in match.groups()
        if value
    ]


#: Markdown emphasis/code formatting is not meaning: strip it so bolding a
#: number cannot hide it from the writer scans.
_MARKDOWN_EMPHASIS = re.compile(r"[*`]")
#: The one historical citation the page deliberately carries: §9 quotes the
#: predecessor claim's figure. Exempted **by name**, not by "it sits inside
#: quotes" — a stale figure in a quoted span anywhere else is still published.
_HISTORICAL_CITATION = re.compile(r'"2 concurrent writers"', re.IGNORECASE)
#: Any digit qualifying a writer term, in either order and across light markdown
#: punctuation ("5 writers", "0 — concurrent writers", "writers: 2"). The union
#: above knows only the phrasings in the tree today; this catch-all makes a NEW
#: wording ("we tested 2 writers concurrently") a red test instead of an escape.
_WRITER_NUMBER = re.compile(
    r"(\d+)[^\w\n]{0,6}(?:concurrent\s+)?writers?"
    r"|writers?[^\w\n]{0,20}(\d+)",
    re.IGNORECASE,
)


def _strip_markdown(text: str) -> str:
    """Drop emphasis/backtick formatting so a bolded number is still scanned."""
    return _MARKDOWN_EMPHASIS.sub("", text)


def _writer_scan_text(path: Path, text: str) -> str:
    """The text to scan for writer numbers, formatted away and §9-aware.

    The historical citation is exempted **by name**, and only on the page that
    carries it: §9 of `docs/capacity.md` quotes the predecessor claim. It is not
    "a figure inside quotes" (a stale figure quoted elsewhere is still
    published), and it is not every surface's (a quoted `"2 concurrent
    writers"` copied into the quickstart must still be a recognised stale
    figure) — #8492 fourth review, observation.
    """
    text = _strip_markdown(text)
    if path == CAPACITY:
        text = _HISTORICAL_CITATION.sub("", text)
    return text


def _read(path: Path) -> str:
    assert path.is_file(), f"missing public surface: {path}"
    return path.read_text(encoding="utf-8")


def _source_tables(text: str) -> list[list[list[str]]]:
    """Every markdown table whose last header cell is `Source` → its data rows.

    Keyed on the header, not on the section, so a new measured table is covered
    the moment it is added — and a section that renames its source column is a
    deliberate act rather than a silent escape from this check.
    """
    lines = text.splitlines()
    tables: list[tuple[list[str], list[list[str]]]] = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not (line.startswith("|") and line.endswith("|")):
            i += 1
            continue
        header = [c.strip() for c in line.strip("|").split("|")]
        data: list[list[str]] = []
        i += 1
        # The `|---|---|` separator, if present.
        if i < len(lines):
            sep = lines[i].strip().replace("|", "").strip()
            if sep and set(sep) <= set("-: "):
                i += 1
        while i < len(lines):
            row = lines[i].strip()
            if not (row.startswith("|") and row.endswith("|")):
                break
            data.append([c.strip() for c in row.strip("|").split("|")])
            i += 1
        tables.append((header, data))
    return [d for h, d in tables if h and h[-1] == "Source"]


def _sections(text: str) -> dict[str, str]:
    """Top-level (``## ``) section heading → that section's body text."""
    sections: dict[str, str] = {}
    current: str | None = None
    for line in text.splitlines():
        if line.startswith("## "):
            current = line.strip()
            sections[current] = ""
        elif current is not None:
            sections[current] += line + "\n"
    return sections


def _integration_writer_coverage() -> int:
    """How many concurrent writer processes the live-graph test actually runs.

    Derived from the test's own source so the docs cannot drift from it: if the
    loop bound moves, this returns the new value and the doc assertions below
    fail against the stale prose.
    """
    src = _read(CONCURRENCY_TEST)
    assert f"def {LIVE_WRITER_TEST}" in src, (
        f"{CONCURRENCY_TEST} no longer defines {LIVE_WRITER_TEST} — the "
        "capacity page's write-concurrency figure has no source"
    )
    body = src.split(f"def {LIVE_WRITER_TEST}", 1)[1].split("\ndef ", 1)[0]
    m = re.search(r"_spawn_live_writer\(i[^\]]*for i in range\((\d+)\)\]", body)
    assert m, (
        f"{LIVE_WRITER_TEST} no longer spawns writers via "
        "`[_spawn_live_writer(i, …) for i in range(N)]` — the coverage this "
        "reads cannot be determined"
    )
    return int(m.group(1))


# ── Reachability ─────────────────────────────────────────────────────────────


def test_capacity_page_exists():
    assert CAPACITY.is_file(), (
        "docs/capacity.md is missing — the public capacity surface it is the "
        "only home of is silent again (#3329)"
    )


def test_capacity_page_is_linked_from_readme():
    text = _read(README)
    assert "docs/capacity.md" in text, (
        "README no longer links the capacity page (#3329 target: linked from README)"
    )


def test_capacity_page_is_linked_from_the_self_hosted_page():
    text = _read(SELF_HOSTED_HTML)
    assert CAPACITY_URL in text, (
        "website/self-hosted.html no longer links the capacity page "
        "(#3329 target: linked from website/self-hosted.html)"
    )


def test_capacity_page_is_linked_from_the_faq_where_it_was_promised():
    """The FAQ carried the promise; it must now carry the delivery."""
    text = _read(ROOT / "website" / "faq.html")
    assert CAPACITY_URL in text, (
        "website/faq.html still promises a capacity page is 'being prepared' "
        "— point it at the published one instead"
    )
    assert "is being prepared" not in text, (
        "website/faq.html still says the capacity page is being prepared"
    )


# ── Every measured number names a source that exists ─────────────────────────


def test_every_measured_row_names_a_source_that_exists():
    text = _read(CAPACITY)
    tables = _source_tables(text)
    assert tables, (
        "docs/capacity.md has no table with a `Source` column — the "
        "measured-from-a-committed-artifact contract is gone"
    )
    missing: list[str] = []
    unlinked: list[str] = []
    measured = 0
    for table in tables:
        for row in table:
            cell = row[-1]
            if _UNMEASURED_CELL.search(cell):
                continue
            tokens = _PATH_TOKEN.findall(cell)
            if not tokens:
                unlinked.append(" | ".join(row))
                continue
            measured += 1
            missing.extend(t for t in tokens if not (ROOT / t).exists())
    assert not unlinked, (
        f"these measured rows name no source path and are not labeled unmeasured, "
        f"so nothing binds the number to the system: {unlinked}"
    )
    assert measured >= 20, (
        f"only {measured} rows cite a source that exists; the page publishes more"
    )
    assert not missing, (
        "docs/capacity.md cites source paths that do not exist in the tree — "
        f"the artifact behind the number is gone: {sorted(set(missing))}"
    )


def test_the_vector_cost_row_cites_an_artifact_that_owns_the_figure():
    """#8492 ISSUE 2: a Source cell may not point at a file denying the number.

    The `1.50 KB stored / 2.05 KB resident` row cited
    `docs/research/2026-09-25-4503-edge-accounting/measurement.md`, whose own
    results table says the embedded-Point comparator is "**not measured in this
    probe** — `#4333` §3.4 owns it". A citation that resolves but disowns the
    figure is exactly what a path-existence check cannot see.
    """
    rows = [row for table in _source_tables(_read(CAPACITY)) for row in table]
    row = next((r for r in rows if "1.50 KB" in " ".join(r)), None)
    assert row is not None, (
        "docs/capacity.md no longer publishes the per-vector cost row "
        "(1.50 KB stored / 2.05 KB resident)"
    )
    source = row[-1]
    assert "2026-09-25-4503-edge-accounting" not in source, (
        "the vector-cost row is attributed to the 4503 edge-accounting probe, "
        "which states the embedded-Point comparator is NOT measured there and "
        "assigns the figure to #4333 §3.4"
    )
    assert (
        "STORAGE-ARCHITECTURE.md" in source
        or "4333-node-volume-storage-cost" in source
    ), (
        "the vector-cost row must name the artifact that owns the figure "
        "(docs/architecture/STORAGE-ARCHITECTURE.md or the #4333 brief §3.4); "
        f"it names: {source}"
    )


def test_the_e2e8_latency_figure_is_a_sourced_row_not_a_prose_escape():
    """#9517 ISSUE 1: the 345–460 ms p95 HAS an artifact — ADR-009 — so it is a row.

    The figure entered §5.2 as a prose bullet announcing that it had "no lineage"
    and was staying out of the measured table. Both halves were wrong, and the
    placement is why nothing caught it: `test_every_measured_row_names_a_source_that_exists`
    walks `_source_tables`, so a bullet carrying a number publishes it *past* the
    one check that binds a number to a committed artifact rather than through it.
    `docs/adr/ADR-009-embedder-selection.md` names the run (E2E-8 post-swap re-run,
    2026-08-21), its environment (the contended benchmark laptop, under full-suite
    contention) and its paired MiniLM control (418–464 ms under identical
    conditions), so the row's entry condition is satisfiable and the caveats that
    decide applicability travel with the number.
    """
    text = _read(CAPACITY)
    rows = [row for table in _source_tables(text) for row in table]
    row = next(
        (r for r in rows if "345" in " ".join(r) and "460" in " ".join(r)), None
    )
    assert row is not None, (
        "docs/capacity.md no longer publishes the 345–460 ms E2E-8 p95 as a "
        "Source-headed row. Prose is not a citation: `_source_tables` walks "
        "tables only, so a prose bullet publishes a number past the source- "
        "binding check instead of satisfying it (#9517)."
    )
    joined = " ".join(row)
    assert "ADR-009" in row[-1], (
        "the 345–460 ms row must name the artifact that owns the figure — "
        "docs/adr/ADR-009-embedder-selection.md; the Source cell reads: "
        f"{row[-1]!r}"
    )
    assert "418" in joined and "464" in joined, (
        "the 345–460 ms row drops the paired MiniLM control (418–464 ms under "
        "identical conditions). Without it the figure reads as a bare over-band "
        "result instead of a comparison against the status quo, which is what "
        f"decides whether it applies: {joined!r}"
    )
    assert "no lineage" not in text, (
        "docs/capacity.md claims the 345–460 ms figure has 'no lineage'. ADR-009 "
        "carries the run, its date, its environment and its control arm, so the "
        "claim is false as published — and it contradicts the page's own rule "
        "that a published number names the artifact it came from (#9517)."
    )


def test_the_ep_frontier_guard_row_names_claims_not_operators():
    """#8492 (third review): `_EP_GUARD_FRONTIER_CAP` bounds new **claims** per hop.

    `tortoise/ep.py:36` states the guard as "one hop producing > cap new
    claims", and it fires on `len(new_frontier) > _EP_GUARD_FRONTIER_CAP` where
    `new_frontier` holds claim ids (`ep.py:1126`). The row's Dimension text said
    "operators per hop", contradicting both its own subject ("affected-claim
    frontier guard") and its cited source, and conflating the constant with the
    separate 200-operator cap `tortoise/analyze.py::_bfs_select_operators`
    applies to the ranking's operator input.
    """
    rows = [row for table in _source_tables(_read(CAPACITY)) for row in table]
    row = next((r for r in rows if "_EP_GUARD_FRONTIER_CAP" in " ".join(r)), None)
    assert row is not None, (
        "docs/capacity.md no longer publishes the EP frontier-guard row "
        "(`_EP_GUARD_FRONTIER_CAP`)"
    )
    dimension = row[0]
    assert "claim" in dimension.lower(), (
        "the `_EP_GUARD_FRONTIER_CAP` row must name the thing the constant "
        f"bounds (new claims per hop); it reads: {dimension!r}"
    )
    assert not re.search(r"\boperators?\b", dimension, re.IGNORECASE), (
        "the `_EP_GUARD_FRONTIER_CAP` row describes the constant as bounding "
        "operators; ep.py's guard bounds new CLAIMS per hop. The operator cap is "
        "a different guard (`tortoise/analyze.py` op_cap) and belongs in its own "
        f"row; the dimension reads: {dimension!r}"
    )


def test_the_operator_cap_has_its_own_row_sourced_to_analyze():
    """The operator 200 is a different bound, and must not hide behind ep.py.

    `tortoise/analyze.py::_bfs_select_operators(op_cap=200)` truncates the
    collected operator set for an explicit-k BFS. #8492 found the page naming
    operators as if `ep.py::_EP_GUARD_FRONTIER_CAP` owned that number; publishing
    the operator cap against `analyze.py` keeps the two 200s distinguishable.
    """
    rows = [row for table in _source_tables(_read(CAPACITY)) for row in table]
    owned = [
        row
        for row in rows
        if "analyze.py" in row[-1]
        and re.search(r"\boperators?\b", row[0], re.IGNORECASE)
    ]
    assert owned, (
        "docs/capacity.md does not publish the explicit-k operator cap sourced "
        "to `tortoise/analyze.py`; the operator 200 the §7 failure threshold "
        "discusses has no row of its own, so it reads as ep.py's claim cap"
    )


def test_the_page_has_measured_content_in_every_section_it_claims():
    """A page that keeps its headings and empties its tables must fail.

    Per-section, not global: deleting §6's five measured rows (heading and prose
    kept) left the global total at 24 and all ten tests green (#8492 ISSUE 3).
    """
    sections = _sections(_read(CAPACITY))
    for heading, minimum in _SECTION_MIN_ROWS.items():
        assert heading in sections, (
            f"docs/capacity.md lost the section '{heading}', which published "
            f"{minimum} sourced rows"
        )
        count = sum(len(table) for table in _source_tables(sections[heading]))
        assert count >= minimum, (
            f"docs/capacity.md section '{heading}' carries {count} sourced rows; "
            f"it published {minimum}. A section that keeps its heading and "
            "empties its table must fail."
        )
    # A Source-headed table with no data rows is an emptied table anywhere.
    for heading, body in sections.items():
        for table in _source_tables(body):
            assert table, (
                f"docs/capacity.md section '{heading}' has a Source-headed "
                "table with no data rows"
            )


# ── The verified figure is derived, not restated ─────────────────────────────


def test_published_write_concurrency_matches_the_integration_test():
    """The check that would have caught "2" while the test covered 5.

    Applied to **every** surface that publishes the figure, in **any** of the
    phrasings the docs use. `docs/quickstart-selfhosted.md`'s env table said
    "concurrent writers verified at 2 processes" while the single-literal pattern
    this replaces could not see it (#8492 ISSUE 1).
    """
    n = _integration_writer_coverage()
    for path in _WRITER_SURFACES:
        found = _published_writer_counts(_strip_markdown(_read(path)))
        assert found, (
            f"{path.relative_to(ROOT)} no longer publishes a verified "
            f"write-concurrency figure; {LIVE_WRITER_TEST} covers {n}"
        )
        assert all(v == n for v in found), (
            f"{path.relative_to(ROOT)} publishes write concurrency "
            f"{found} but {LIVE_WRITER_TEST} covers {n} concurrent writers — "
            "update the prose to the measured coverage"
        )


def test_the_writer_count_pattern_recognizes_every_phrasing_the_docs_use():
    """Pin the phrasings, so a new wording is a red test, not a silent escape.

    The #8492 drift re-entered through `verified at N processes`, which the
    single-literal pattern did not match. Each phrasing the public surfaces use
    must resolve to the same number, or the binding above is asleep.
    """
    for phrasing in (
        "integration-tested to 5 concurrent writers",
        "integration-tested to 5 writers",
        "concurrent writers verified at 5 processes",
        "verified to 5 concurrent writer processes",
        "5 writer processes on one graph",
    ):
        assert _published_writer_counts(phrasing) == [5], phrasing


def test_no_writer_number_escapes_the_binding_on_a_public_surface():
    """A phrasing the union does not know must still not publish a stale number.

    The union covers the phrasings in the tree today; this catch-all makes a NEW
    wording ("we tested 2 writers concurrently") red instead of escaping the
    binding (#8492 follow-up). Markdown emphasis is stripped first, so bolding a
    number cannot hide it, and §9's historical citation is exempted **by name**
    (not by "it is in quotes"), so a stale figure inside a quoted span elsewhere
    is still caught. The embedded lane is 0 writers by contract, so a 0 is
    accepted only where the line says `embedded`; every other writer count must
    be the derived coverage.
    """
    n = _integration_writer_coverage()
    for path in _WRITER_SURFACES:
        text = _writer_scan_text(path, _read(path))
        for line in text.splitlines():
            embedded_line = bool(re.search(r"\bembedded\b", line, re.IGNORECASE))
            for match in _WRITER_NUMBER.finditer(line):
                value = int(match.group(1) or match.group(2))
                if value == 0:
                    assert embedded_line, (
                        f"{path.relative_to(ROOT)} publishes {match.group(0)!r}: "
                        "0 writers is the embedded lane's contract and must be "
                        "stated on an embedded line"
                    )
                else:
                    assert value == n, (
                        f"{path.relative_to(ROOT)} publishes {match.group(0)!r}, "
                        "a write-concurrency statement the binding does not "
                        f"recognise; every verified writer count must be {n}"
                    )


def test_the_historical_citation_is_exempted_only_where_it_lives():
    """§9's exemption is `docs/capacity.md`'s alone — not every surface's.

    #8492's fourth review observed that stripping the citation from all four
    surfaces let a quoted `"2 concurrent writers"` copied into the quickstart
    escape both writer scans. Keyed to the page that carries it, the same
    string anywhere else is a recognised stale figure again.
    """
    quoted = 'the predecessor said "2 concurrent writers" while the test covered 5'
    assert not _WRITER_NUMBER.search(_writer_scan_text(CAPACITY, quoted)), (
        "docs/capacity.md §9's historical citation is its one legitimate use"
    )
    assert _WRITER_NUMBER.search(_writer_scan_text(QUICKSTART, quoted)), (
        "a quoted stale figure on another surface escaped the catch-all — the "
        "exemption must not travel with the string"
    )


# ── "Unmeasured" is a published answer, and must stay published ──────────────


def test_the_issue_dimensions_all_appear():
    """#3329 names five dimensions the page must cover."""
    text = _read(CAPACITY).lower()
    for dimension in (
        "write concurrency",
        "rate limit",
        "retrieval latency",
        "storage footprint",
        "belief propagation",
    ):
        assert dimension in text, (
            f"docs/capacity.md no longer covers '{dimension}' — one of the five "
            "dimensions #3329 requires"
        )


def test_the_unmeasured_dimensions_are_still_named():
    text = _read(CAPACITY)
    assert "Explicitly unmeasured" in text, (
        "docs/capacity.md dropped its 'Explicitly unmeasured' section — the "
        "honest answer #3329 requires in place of an estimate"
    )
    for gap in (
        "Corpus-scaling latency curves",
        "Memory footprint per deployment tier",
        "Query throughput",
    ):
        assert gap in text, (
            f"docs/capacity.md no longer names the unmeasured dimension "
            f"'{gap}'"
        )


# ── The held cap says so, at the cap (#10175) ───────────────────────────────

#: The clauses that stop a lane meeting a 402 from clearing it. Every entry is
#: a phrase ONLY the added clause supplies, checked against the page as it stood
#: before this relay — where each of them occurred **zero** times. That is the
#: load-bearing part: loose keywords are satisfied by text that was already on the
#: page (`402`, `429` and `max_points` all predate the relay), so a keyword pin
#: stayed green with the clause's whole stock-vs-flow bullet deleted. Each entry
#: names a distinct requirement of the ruling and its 2026-11-02 amendment.
#:
#: Written WITHOUT markup and matched through `_plain()`, because the substance is
#: the words and not the formatting: a re-emphasised sentence is a meaning-preserving
#: edit, and a pin that reds on one is the false alarm that gets a guard deleted.
#: Markup-neutral matching also lets the entries bind a full conclusion rather than
#: stopping at the delimiters — `", so the graph stays"` and `"only after …"` are
#: exactly the halves a lane acts on.
_HELD_CAP_CLAUSES = (
    "overrides: the default engineering reflex",  # the marker + what it overrides
    "is refused (owner ruling",  # the ruling itself, not merely a marker
    "not an outage to remediate",  # the 402 is the correct state
    "the refusal is terminal",  # a stock, not a flow
    "backoff is not a remedy",  # the retry branch that must not be shared
    "a max_points override",  # the remedy that is forbidden
    "the field is not out of an operator's hands",  # an override is reachable
    "permits removing that one graph",  # the 2026-11-02 amendment authorises it
    "only after a count-verified export to durable local storage",  # ...exclusively after this
    "and with a journalled delete",  # ...and only journalled
    "delete path exists yet, so the graph stays",  # ...and it therefore stays
    "not planned",  # the storage limb's owner is closed, not open
    "no backfill of the old graph",  # survives the amendment
    "no reading of its 402 as a defect",  # survives the amendment
)

#: The claims the ruling's 2026-11-02 amendment SUPERSEDED, and the two the first
#: relay got wrong. Asserted ABSENT, because a presence-only pin cannot catch the
#: superseded sentence being put back beside the corrected one: the phrases the
#: pin looks for survive verbatim in the amended text, so the page could state
#: both the permission and the withdrawn prohibition and stay green.
_HELD_CAP_SUPERSEDED = (
    # The pre-amendment half: an export exists, so this is false. Bound as the
    # CLAIM ("is the only copy"), never as the bare substring "only copy": a
    # future editor's *correct* clarification — "the only sample, not the only
    # copy" — contains the substring and would red on right text, which is the
    # false alarm this file's doctrine says gets a guard deleted.
    "is the only copy",
    "sole writer",  # false: `sdk.org_update` accepts the field
    "never on an operator override",  # false, and the opposite of the ruling
    "deletes the measurement",  # the predicate the withdrawn sentence acted on
)

#: The ONE permission §4 grants — the sanctioned route — and the scope the ruling
#: puts on it. The prohibition alone is not enough: a lane holding the 402 reads
#: the *permission*, so a pin set that carried only the prohibitions stayed green
#: while the page licensed the exact move the ruling refuses. Each entry is a
#: claim-shaped phrase, so rewording or deleting that sentence reds here.
_HELD_CAP_LEVER = (
    "the sanctioned lever is the plan",  # the lever is named at all
    "max_points := tier_limits(tier)",  # ...and the code path that moves it
    "never on the held one",  # ...scoped OFF this org
    "in place is refused",  # ...the in-place raise above all
)

#: The lift-condition's storage limb, pinned as a TRUTH VALUE. Written as an
#: ownership fact ("#10017 is closed, so no open issue owns this limb") it is
#: neither satisfied nor outstanding, so "is the conjunction false?" becomes
#: unanswerable from the page and a reader waits for a prerequisite that is
#: already deployed.
_HELD_CAP_STORAGE_LIMB = (
    "is satisfied today",  # the limb's truth value, stated
    "record.armed=true",  # ...and the measurement it rests on
)

#: The clause that binds the finding to §4 — its own heading, which no other
#: section carries. Deliberately NOT one of the phrases `_HELD_CAP_CLAUSES`
#: checks, so this pin can fail on its own rather than only in the presence
#: pin's shadow.
_HELD_CAP_TITLE = "### A held cap is not a defect — one tenant is refused on purpose"

_SEC4_HEADING = "## 4. Graph size (hosted node cap)"
_SEC4_NEXT_HEADING = "## 5. Retrieval latency"

#: Prose re-wraps; a clause must not depend on where a line broke. Matching is
#: done on whitespace-collapsed text so reflowing a paragraph cannot red the
#: guard into a false alarm — a guard that cries wolf gets deleted, which is the
#: outcome it exists to prevent.
_WHITESPACE = re.compile(r"\s+")


def _collapsed(text: str) -> str:
    return _WHITESPACE.sub(" ", text)


def _plain(text: str) -> str:
    """Collapsed, lowercased, with markdown emphasis / code spans stripped.

    Both scans match through this, so typography can shift neither: a withdrawn
    claim restored as ``the only *copy*`` (or ``only **copy**``, or a code span)
    is still the withdrawn claim, and a still-correct clause that is re-emphasised
    is still correct. The page uses both ``*old*`` and ``**only**``, so this is a
    live variant, not a hypothetical one.

    Accepted residual: HTML tags, entities, zero-width characters and `_word_`
    emphasis are NOT stripped. Underscores are deliberately left alone — in this
    page they are identifier characters (`max_points`, `graph_size_cap`), so
    stripping them would corrupt the very phrases the pin looks for. `docs/capacity.md`
    contains no HTML and no zero-width characters, so a tag or entity is an
    adversarial construction rather than an edit someone would make here; the
    docstring names the gap instead of growing a regex for it.
    """
    return re.sub(r"[*`~]", "", _collapsed(text)).lower()


def _section4(text: str) -> str:
    """The §4 slice, failing (not erroring) if a heading was renamed.

    `str.index` on a missing heading raises `ValueError`, which pytest reports as
    an ERROR with no diagnostic that says what happened. The heading is part of
    what this file binds, so its absence is an assertion, not a crash.
    """
    for heading in (_SEC4_HEADING, _SEC4_NEXT_HEADING):
        assert heading in text, (
            f"docs/capacity.md no longer carries the heading {heading!r} — the "
            "held-cap clause is pinned to §4, the section that owns the cap, so "
            "a renamed heading must be re-pinned here rather than silently "
            "dropped from the check"
        )
    return text[text.index(_SEC4_HEADING) : text.index(_SEC4_NEXT_HEADING)]


def test_the_held_capture_cap_states_that_clearing_it_is_refused():
    """#10175 is an owner ruling in force, and §4 is where a lane MEETS the cap.

    The ruling lives on the issue; the cap it protects is documented here. A
    lane holding a 402 arrives through this page — it names the cap, its value,
    and its enforcement — so a page that states the enforcement without stating
    the refusal hands that lane the remedy (`max_points`) and withholds the
    prohibition. That asymmetry is the failure: the reflex it triggers is a
    `max_points` override, a refill for headroom, or a prune.

    Scoped to §4 and matched on clause-only phrases, deliberately: whole-page
    keyword matching would be satisfied by the enforcement text this page already
    carried, and would stay green through a rewrite that kept the vocabulary and
    dropped the ruling.
    """
    text = _plain(_section4(_read(CAPACITY)))
    dropped = [clause for clause in _HELD_CAP_CLAUSES if clause not in text]
    assert not dropped, (
        "docs/capacity.md §4 no longer carries the #10175 refusal clause — "
        f"missing: {dropped}. The page still documents the cap's value and its "
        "402 enforcement, so a lane reading it is told the cap is real and is "
        "not told that clearing it is refused (owner ruling 2026-10-27). Relay "
        "the clause back instead of deleting it: an OVERRIDES line on a closed "
        "issue is invisible to the lane that meets the refusal."
    )


def test_the_held_cap_clause_is_in_the_section_that_owns_the_cap():
    """Relocated into §9 or the appendix, it stops being read at the cap.

    Anchored on the clause's own heading rather than on the `OVERRIDES` marker: a
    lone marker left in §4 would satisfy a marker test while the clause sat at
    the end of the file.
    """
    text = _read(CAPACITY)
    assert _HELD_CAP_TITLE in text, (
        "docs/capacity.md no longer carries the held-cap clause heading "
        f"{_HELD_CAP_TITLE!r} — the clause is pinned to §4 by this heading, so a "
        "retitled clause must be re-pinned here rather than silently escaping "
        "the placement check"
    )
    assert _HELD_CAP_TITLE in _section4(text), (
        "the #10175 refusal clause is no longer inside §4 (Graph size, the "
        "hosted node cap) — it is the section a lane holding a 402 reads, and "
        "the clause is load-bearing only where the cap is documented"
    )


def test_the_sanctioned_lever_is_scoped_off_the_held_org():
    """The page names a lever; the ruling scopes it, and both halves must survive.

    `tortoise/billing.py::apply_limits` raises **this** org's cap, and the owner's
    2026-11-07 answer on `#10175` refuses exactly that: raising this org's tier in
    place is "the exact move the prohibition names". So the sentence naming the
    lever is the sentence that can re-open the prohibition, and a pin set holding
    only the prohibitions cannot see it — the licence is the permission, not a
    restatement of the refusal.

    Scoped to §4 and matched through `_plain()`, like its siblings: the clause is
    load-bearing where the cap is documented, and re-emphasis is not a regression.
    """
    text = _plain(_section4(_read(CAPACITY)))
    dropped = [phrase for phrase in _HELD_CAP_LEVER if phrase not in text]
    assert not dropped, (
        "docs/capacity.md §4 no longer carries the sanctioned lever with the "
        f"ruling's scope on it — missing: {dropped}. The lever is "
        "`tortoise/billing.py::apply_limits`, and it is sanctioned on a new org "
        "or the switchover's new graph, never on the held one: a tier change on "
        "this org in place is refused for the same reason an override is "
        "(owner answer 2026-11-07 on #10175). Naming the lever unscoped hands a "
        "lane holding the 402 the remedy the ruling refuses — restore the scope "
        "rather than deleting either half."
    )


def test_the_lift_condition_states_the_storage_limbs_truth_value():
    """A limb is either satisfied or outstanding, and the page must say which.

    The storage half of the switchover was recorded as an ownership fact —
    `#10017` closed *not planned*, "this limb is not owned by an open issue" —
    inside a conjunction the page says the hold stands until it is all true. That
    leaves the reader unable to evaluate the limb at all. It is in fact deployed:
    the hosted record is armed and writing, so the limb is satisfied and does not
    hold the conjunction false. Asserting the truth value keeps a later edit from
    reverting it to a prerequisite nobody owns.
    """
    text = _plain(_section4(_read(CAPACITY)))
    dropped = [phrase for phrase in _HELD_CAP_STORAGE_LIMB if phrase not in text]
    assert not dropped, (
        "docs/capacity.md §4 no longer states the storage limb of the lift "
        f"condition as satisfied — missing: {dropped}. `#10017` closed *not "
        "planned* because the local half was a no-op; the hosted record it "
        "deferred to is armed (`/health` `record.armed=true`, "
        "`record.ok=true`, `record_writes_applied` non-zero). State that it is "
        "satisfied, or name the issue that owns it if it stops being true — an "
        "unowned prerequisite inside the conjunction makes the hold "
        "unevaluable."
    )


def test_the_held_cap_clause_does_not_restate_a_superseded_claim():
    """The relay must not carry the claim AND its retraction side by side.

    The owner's 2026-11-02 amendment superseded the no-prune prohibition for one
    graph, and the first relay's "sole writer" claim was simply false. Presence
    pins cannot catch either regression: they look for phrases that the corrected
    text keeps, so a sentence restoring the superseded claim passes them. This is
    the half that fails when the old text is put back.

    It is a pin on the specific superseded WORDINGS, not a proof that no wording
    can restate them: a rewrite that keeps the sentence asserting an override is
    reachable and only rewords a closing clause stays green, which is why the
    presence pin binds that sentence as well.
    """
    section = _plain(_section4(_read(CAPACITY)))
    restated = [claim for claim in _HELD_CAP_SUPERSEDED if claim in section]
    assert not restated, (
        "docs/capacity.md §4 restates a claim this ruling already corrected: "
        f"{restated}. The graph is the only *sample* of the old extractor's "
        "output, not the only copy (an export is verified by count and the "
        "removal is journalled, #10175 amendment 2026-11-02); and an operator "
        "override is reachable — a stored `max_points` column is honoured with "
        "precedence over `graph_size_cap` and `sdk.org_update` accepts the "
        "field — which is why the ruling forbids it rather than claiming it "
        "cannot be done."
    )
