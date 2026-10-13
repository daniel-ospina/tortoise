"""Renders `docs/blog-authoring.md` with a real CommonMark parser (#8379 finding 2).

The authoring contract is the ONE page an author reads before writing a body, and
every example in it lives in a fenced code block. Its fences were all closed with
```` ```text ```` instead of a bare ```` ``` ```` — and a CommonMark closing fence
"may be followed only by spaces or tabs", so ```` ```text ```` does NOT close the
block. Every example, and every heading after the first one, published as a single
giant code block: the page that exists to be the contract was the one page an
author could not read.

Nothing caught it. `markdownlint-cli2` has no rule for an unterminated fence, so
its green run said nothing about whether the page renders — the false-confidence
shape this repo keeps re-learning. A real parser is the instrument that sees it,
which is why this module renders the document instead of regexing it.
"""
from __future__ import annotations

import re
from pathlib import Path

from markdown_it import MarkdownIt

REPO_ROOT = Path(__file__).resolve().parent.parent
DOC = REPO_ROOT / "docs" / "blog-authoring.md"

# The sections an author must be able to REACH. A fence that never closes swallows
# every heading beneath it into the code block, so this set is the reader-visible
# consequence of the defect — not a restatement of the file's contents. `*not*`
# is emphasis in the source, so it renders as `<em>`.
EXPECTED_H2 = (
    "Basics",
    "Table of contents",
    "Callouts (including the answer box)",
    "Key takeaways",
    "FAQ section",
    "Video",
    "What is <em>not</em> supported",
)
EXPECTED_H3 = ("The answer box, house style", "Transcripts")


def _render(text: str) -> str:
    # The `commonmark` preset, so the fence rule under test is CommonMark's and
    # not a GFM extension's.
    return MarkdownIt("commonmark").render(text)


def _headings(html: str, level: int) -> set[str]:
    return set(re.findall(rf"<h{level}>(.*?)</h{level}>", html))


def test_every_section_renders_as_a_heading() -> None:
    """The page is readable: each documented section is a real heading."""
    assert DOC.exists(), f"the authoring contract is gone: {DOC}"
    html = _render(DOC.read_text(encoding="utf-8"))

    h2 = _headings(html, 2)
    missing = [h for h in EXPECTED_H2 if h not in h2]
    assert missing == [], (
        f"these `##` sections of docs/blog-authoring.md do not render as headings: {missing}. "
        "The usual cause is a code fence closed with ```text (or any info string) — a "
        "CommonMark closing fence may be followed only by spaces or tabs, so the block "
        "never closes and everything after it publishes as literal code."
    )

    h3 = _headings(html, 3)
    missing_h3 = [h for h in EXPECTED_H3 if h not in h3]
    assert missing_h3 == [], f"these `###` subsections do not render as headings: {missing_h3}"


def test_closing_fences_carry_no_info_string() -> None:
    """Every fenced block is CLOSED by a bare fence, so the blocks are balanced.

    This is the CommonMark rule the defect violated, asserted directly on the
    document so the failure message names the offending line instead of reporting
    "a heading is missing".
    """
    lines = DOC.read_text(encoding="utf-8").splitlines()
    fences = [
        (i + 1, line) for i, line in enumerate(lines) if re.match(r"^ {0,3}```", line)
    ]
    assert fences, "docs/blog-authoring.md has no fenced examples to check"
    assert len(fences) % 2 == 0, (
        f"docs/blog-authoring.md has {len(fences)} fence delimiter lines (odd) — the "
        "last block is never closed"
    )
    for index, (lineno, line) in enumerate(fences):
        if index % 2 == 1:  # a closing delimiter
            assert re.fullmatch(r" {0,3}```\s*", line), (
                f"docs/blog-authoring.md:{lineno} closes a fence with {line!r}; a "
                "CommonMark closing fence may be followed only by spaces or tabs, so "
                "this does not close the block"
            )


def test_the_rendered_block_count_matches_the_opening_fence_count() -> None:
    """The parser agrees with the delimiter scan: no block is left open.

    A non-vacuous cross-check between the rendered result and the source shape —
    if a fence failed to close, the parser collapses the remaining blocks into one
    while the source still shows one opening per example.
    """
    text = DOC.read_text(encoding="utf-8")
    html = _render(text)
    openings = sum(1 for line in text.splitlines() if re.match(r"^ {0,3}```\S", line))
    assert html.count("<pre><code") == openings, (
        f"docs/blog-authoring.md renders {html.count('<pre><code')} fenced code blocks "
        f"from {openings} opening fences — a block is unterminated"
    )
