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
#: A published write-concurrency figure, in either doc phrasing.
_PUBLISHED_WRITERS = re.compile(r"integration-tested to (\d+) (?:concurrent )?writers")
#: The page/website phrasing for the verified writer-process coverage.
_WRITER_PROCESSES = re.compile(r"(\d+) (?:concurrent )?writer processes")


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


def test_the_page_has_measured_content_in_every_section_it_claims():
    """A page that keeps its headings and empties its tables must fail."""
    text = _read(CAPACITY)
    rows = [row for table in _source_tables(text) for row in table]
    # §1 modes, §2 concurrency, §3 rate limits, §4 caps, §5.1 targets,
    # §5.2 measured read path, §6 storage, §7 EP.
    assert len(rows) >= 20, (
        f"docs/capacity.md carries only {len(rows)} sourced rows; the page "
        "publishes measurements across eight sections"
    )


# ── The verified figure is derived, not restated ─────────────────────────────


def test_published_write_concurrency_matches_the_integration_test():
    """The check that would have caught "2" while the test covered 5."""
    n = _integration_writer_coverage()
    for path in (CAPACITY, QUICKSTART, ENV_EXAMPLE):
        text = _read(path)
        found = [int(v) for v in _PUBLISHED_WRITERS.findall(text)]
        assert found, (
            f"{path.relative_to(ROOT)} no longer publishes a verified "
            f"write-concurrency figure; {LIVE_WRITER_TEST} covers {n}"
        )
        assert all(v == n for v in found), (
            f"{path.relative_to(ROOT)} publishes write concurrency "
            f"{found} but {LIVE_WRITER_TEST} covers {n} concurrent writers — "
            "update the prose to the measured coverage"
        )


def test_capacity_page_and_website_publish_the_verified_writer_count():
    n = _integration_writer_coverage()
    for path in (CAPACITY, SELF_HOSTED_HTML):
        found = [int(v) for v in _WRITER_PROCESSES.findall(_read(path))]
        assert found, (
            f"{path.relative_to(ROOT)} does not state the verified writer-process "
            f"coverage ({n}) in runnable terms"
        )
        assert all(v == n for v in found), (
            f"{path.relative_to(ROOT)} states {found} writer processes but "
            f"{LIVE_WRITER_TEST} runs {n}"
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
