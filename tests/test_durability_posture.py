"""#2881 — the durability posture, enforced at the test layer.

Canonical document: ``docs/durability-posture.md`` (design round §6).

This module is the anti-scatter falsifier for the durability authority. The
repository used to make three disagreeing "the event log is the source of
truth" statements, so the text inherited whatever the nearest comment believed;
the fix was **deletion, not reconciliation** — only the canonical doc is
allowed to pair a JSONL/journal/event-log with "source of truth".

The pattern and the roots are the owner's, verbatim (issue #2881, §6)::

    grep -rniE "(event log|journal|jsonl).{0,40}source of truth|source of truth.{0,40}(durab|journal|event log|jsonl)" \\
      tortoise/ README.md docs/durability-posture.md docs/quickstart-*.md \\
      docs/infra-runbook.md docs/data-safety.md docs/ONTOLOGY.md

The gate is deliberately **scoped, not global**: the bare phrase is used
hundreds of times repo-wide for unrelated things (pack schemas, eval specs, the
legitimate "corpus files (source of truth)" at
``docs/quickstart-selfhosted.md:232``). A global gate would be noise and would
be switched off; this one names the exact deployment-claim shape.

Unconditional (no network, no browser, no DB): plain file scan.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CANONICAL_DOC_REL = "docs/durability-posture.md"
CANONICAL_DOC = REPO / CANONICAL_DOC_REL

# The owner's ERE, verbatim, as a Python pattern. Line-at-a-time like grep, so
# `.` never crosses a newline and the two alternations keep their local scope.
CLAIM_RE = re.compile(
    r"(event log|journal|jsonl).{0,40}source of truth"
    r"|source of truth.{0,40}(durab|journal|event log|jsonl)",
    re.IGNORECASE,
)

# The owner's root list, verbatim: one recursive dir, one glob, five files.
SCAN_DIRS = ("tortoise",)
SCAN_GLOBS = ("docs/quickstart-*.md",)
SCAN_FILES = (
    "README.md",
    CANONICAL_DOC_REL,
    "docs/infra-runbook.md",
    "docs/data-safety.md",
    "docs/ONTOLOGY.md",
)

# Build output is not shipped source; a stale bytecode file would otherwise
# carry the previous docstring and manufacture a false hit (grep -r would too,
# but a fresh CI checkout has none, and a local run must agree with CI).
_SKIP_PARTS = {"__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache"}


def _scanned_files():
    seen: set[Path] = set()

    def _emit(path: Path):
        if not path.is_file():
            return
        if _SKIP_PARTS & set(path.parts):
            return
        if path not in seen:
            seen.add(path)
            return path
        return None

    for rel in SCAN_FILES:
        got = _emit(REPO / rel)
        if got is not None:
            yield got
    for pattern in SCAN_GLOBS:
        for path in sorted(REPO.glob(pattern)):
            got = _emit(path)
            if got is not None:
                yield got
    for root in SCAN_DIRS:
        base = REPO / root
        if not base.exists():
            continue
        for path in sorted(base.rglob("*")):
            got = _emit(path)
            if got is not None:
                yield got


def _claim_lines(path: Path) -> list[tuple[int, str]]:
    text = path.read_bytes().decode("utf-8", errors="ignore")
    return [
        (i, line)
        for i, line in enumerate(text.splitlines(), 1)
        if CLAIM_RE.search(line)
    ]


# ── 1. the scan reaches the declared roots ──────────────────────────────────

def test_scan_reaches_the_declared_roots():
    """Set-containment of sentinels — a narrowed root list must fail here even
    if the claim scan would otherwise pass vacuously."""
    scanned = {p.relative_to(REPO).as_posix() for p in _scanned_files()}
    for sentinel in (
        "tortoise/log.py",
        "tortoise/consistency.py",
        "tortoise/projection/__init__.py",
        "README.md",
        "docs/durability-posture.md",
        "docs/quickstart-selfhosted.md",
        "docs/quickstart-cloud.md",
        "docs/infra-runbook.md",
        "docs/data-safety.md",
        "docs/ONTOLOGY.md",
    ):
        assert sentinel in scanned, f"scan root is too narrow — missing {sentinel}"


# ── 2. the gate ─────────────────────────────────────────────────────────────

def test_no_durability_source_of_truth_claim_outside_the_canonical_doc():
    """Only ``docs/durability-posture.md`` may bind a journal/event log to
    "source of truth". Any other hit is the contradiction returning."""
    offenders: list[str] = []
    for path in _scanned_files():
        rel = path.relative_to(REPO).as_posix()
        if rel == CANONICAL_DOC_REL:
            continue
        for lineno, line in _claim_lines(path):
            offenders.append(f"{rel}:{lineno}: {line.strip()[:120]}")
    assert not offenders, (
        "the durability authority is " + CANONICAL_DOC_REL + " — delete the "
        "claim and link the canonical doc, or the contradiction is back:\n"
        + "\n".join(offenders)
    )


# ── 3. the canonical doc exists and says what the authority must say ────────

def test_canonical_doc_exists_and_states_the_rule():
    assert CANONICAL_DOC.is_file(), f"{CANONICAL_DOC_REL} must exist"
    text = CANONICAL_DOC.read_text(encoding="utf-8")
    low = text.lower()
    # The rule: store persistence + an off-box copy; the JSONL is a domain
    # event log, never the durability mechanism; :GraphEvent is the 30-day stream.
    assert "off-box" in low
    assert "domain event log" in low
    assert ":graphevent" in low
    assert "30-day" in low
    # The honesty requirement: the strongest verification actually performed,
    # and "not drilled" in those words where true.
    assert low.count("not drilled") >= 2, (
        "the posture doc must say 'not drilled' where a restore has never been "
        "performed (vendor snapshot / self-hosted / embedded)"
    )
    # The unverifiable AOF promise must be contested, not asserted.
    assert "contested" in low
    assert "settled with" in low
    # The vendor facts the owner verified.
    assert "12-hourly" in low or "12 h" in low
    assert "7-day retention" in low
    assert "new instance" in low
    assert "per-user acl" in low


def test_canonical_doc_is_itself_a_claim_hit():
    """The exemption above is only sound if the canonical doc really does make
    the claim (otherwise the whole gate could be satisfied by an empty doc)."""
    assert _claim_lines(CANONICAL_DOC), (
        f"{CANONICAL_DOC_REL} no longer contains the durability authority "
        "statement the gate exempts — fix the doc, not the gate"
    )


# ── 4. the regex fires on the defect and not on unrelated prose ─────────────

def test_claim_regex_matches_the_original_defect_forms():
    """The forms the four sites actually carried (#2881 §3)."""
    for claim in (
        "Append-only JSONL event log — the source of truth.",
        "The event log is the source of truth; the projection is a derived view.",
        "the JSONL event stream is the rebuild source of truth",
    ):
        assert CLAIM_RE.search(claim), f"scan misses a real claim: {claim!r}"
    # And the durability-worded second alternation.
    assert CLAIM_RE.search("the source of truth for durability claims")


def test_claim_regex_rejects_unrelated_source_of_truth_prose():
    """The bare phrase is legitimate elsewhere; the gate must not fire."""
    for unrelated in (
        "corpus files (source of truth)",
        "docs/ONTOLOGY.md is the single source of truth for the entity model",
        "This is the canonical source of truth.",
        "the fold is the single source of truth, module contract",
        "the derivation is the truth, the cache is a copy",
        "one source of truth (#715)",
        "the journal/event stream is the reconstruction source for Object.status",
    ):
        assert not CLAIM_RE.search(unrelated), (
            f"scan false-positives on unrelated copy: {unrelated!r}"
        )
