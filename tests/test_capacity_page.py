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
ENV_EXAMPLE = ROOT / ".env.example"
CONCURRENCY_TEST = ROOT / "tests" / "test_embedded_concurrency.py"

#: The one integration test that measures concurrent writers on a live graph.
LIVE_WRITER_TEST = "test_concurrent_writers_live_falkor_no_lost_writes"

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
#:   "integration-tested to 5 writers"             (`.env.example`)
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
    "## 5. Retrieval latency": 6,  # §5.1 targets (5) + §5.2 measured path (1)
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
    for path in (CAPACITY, QUICKSTART, ENV_EXAMPLE, SELF_HOSTED_HTML):
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
    for path in (CAPACITY, QUICKSTART, ENV_EXAMPLE, SELF_HOSTED_HTML):
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
