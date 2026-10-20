"""#2881 — the durability posture, enforced at the test layer.

Canonical document: ``docs/durability-posture.md`` (design round §6).

This module is the anti-scatter falsifier for the durability authority. The
repository used to make three disagreeing "the event log is the source of
truth" statements, so the text inherited whatever the nearest comment believed;
the fix was **deletion, not reconciliation** — only the canonical doc is
allowed to pair a JSONL/journal/event-log with "source of truth".

The gate's pattern and roots are the owner's (issue #2881, §6), with ONE
documented extension — the owner's pattern cannot see two forms this change
also deletes: a bare ``log``/``event stream`` subject ("The log is the source of
truth" — ``tortoise/projection/__init__.py:3``) and the ``is [the] truth``
predicate ("the event log is truth" — ``tortoise/commit_ops.py:431``). The
owner's ERE, verbatim::

    grep -rniE "(event log|journal|jsonl).{0,40}source of truth|source of truth.{0,40}(durab|journal|event log|jsonl)" \\
      tortoise/ README.md docs/durability-posture.md docs/quickstart-*.md \\
      docs/infra-runbook.md docs/data-safety.md docs/ONTOLOGY.md

The extension lives in ``CLAIM_RE`` below and is pinned by the positive test.

A SECOND extension is the scan root ``graph-scripts/`` (#4308). The owner's
§6 root list covered the contract surfaces only, so the two runnable scripts
that still asserted "the JSONL is the source of truth" were invisible to the
gate. They are *executable* statements — ``add_convergence_evidence.py`` writes
its research claims into the graph — not archive records, so the authority rule
binds them, and they are now both scanned and reworded to the reconstruction
framing (``JSONL = reconstruction source``, ``store = durability authority``).
Because ``CLAIM_RE`` matches a claim SHAPE inside a 40-char window (the doc's
own scoping note), the two sentinels are ALSO pinned by window-independent
regression tests — the pre-fix residual at ``add_convergence_evidence.py:58``
sat ~80 chars from its subject and the regex could not see it (review finding).
Archive records (``docs/plans/``, ``docs/scoping/``, ``docs/epics/``) and the
test-suite's session-journal vocabulary stay out of scope: the former are
point-in-time records, the latter is a real non-durability claim (the file
journal IS the source of truth for a test session's created-graph ownership).

The gate is deliberately **scoped, not global**: the bare phrase is used
hundreds of times repo-wide for unrelated things (pack schemas, eval specs, the
legitimate "corpus files (source of truth)" at
``docs/quickstart-selfhosted.md:265``). A global gate would be noise and would
be switched off; this one names the exact deployment-claim shape.

Unconditional (no network, no browser, no DB): plain file scan.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CANONICAL_DOC_REL = "docs/durability-posture.md"
CANONICAL_DOC = REPO / CANONICAL_DOC_REL

# The owner's ERE — the first two alternations, verbatim — extended (review
# finding) with a bare `log`/`event stream` subject and the `is [the] truth`
# predicate, the two forms it cannot see. Line-at-a-time like grep, so `.`
# never crosses a newline and the alternations keep their local scope. The
# extension is scoped to the SAME paths and adds no false positives (the
# legitimate "the derivation is the truth, the cache is a copy" stays green).
#
# The LAST alternation is #9134's P2: a pre-existing red was greened by rewording
# `journal is the truth` to `the journal is the durable record`, which moves the
# claim out of the `truth` window while still ATTRIBUTING DURABILITY to the
# journal — the opposite of this doc's rule ("durability = the store's own
# persistence plus an off-box copy of it. Nothing else."; the JSONL "is never the
# durability mechanism"). Durability is now a SUBJECT-ATTRIBUTION predicate, so
# the same claim cannot come back in a different wording. The determiner slot is
# a SLOT, not the literal `the`: `the journal is A durable record` and `the event
# log is ITS durability mechanism` are the same claim, and pinning `(?:the )?`
# made the gate see only the one reword that happened to be reviewed. Measured
# over all 304 scanned files: no false positives (nothing in the tree attributes
# durability to the log in any determiner form).
_SUBJECT = r"(?:event log|journal|jsonl|\blog\b|event stream)"
# any possessive/indefinite determiner, or none
_DURAB_DET = r"(?:the |a |an |our |its |their |your )?"
CLAIM_RE = re.compile(
    rf"{_SUBJECT}.{{0,40}}source of truth"
    rf"|source of truth.{{0,40}}(?:durab|{_SUBJECT})"
    rf"|{_SUBJECT}.{{0,40}}is (?:the )?truth"
    rf"|{_SUBJECT}.{{0,40}}is {_DURAB_DET}durab(?:le|ility)",
    re.IGNORECASE,
)

# The two PREDICATE alternations, applied to whitespace-JOINED text. A line
# break is not a scope: `COMMIT POINT (the journal IS the` / `truth)` is the
# same claim as one line, and `CLAIM_RE` above — line-at-a-time like grep, so
# `.` cannot cross a newline — reads it as clean. That is not hypothetical:
# `tortoise/api.py` carried exactly that wrapped claim, one reflow away from
# firing, and this gate certified the file green for it. Only the predicate
# forms are re-scanned: the `source of truth` alternations have legitimate
# cross-line neighbours (`corpus files (source of truth), (2) the FULL
# `events/` JSONL directory`, `single source of truth with apply, #330` /
# `# Journal-order`), so joining them repo-wide would manufacture false
# positives — measured, not assumed.
WRAPPED_CLAIM_RE = re.compile(
    rf"{_SUBJECT}.{{0,40}}is (?:the )?truth"
    rf"|{_SUBJECT}.{{0,40}}is {_DURAB_DET}durab(?:le|ility)",
    re.IGNORECASE,
)

# The owner's root list, verbatim: one recursive dir, one glob, five files —
# plus `graph-scripts/` (#4308, the second documented extension: runnable
# scripts whose correctness claims about the system are executable, not
# archival).
SCAN_DIRS = ("tortoise", "graph-scripts")
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


def _wrapped_claim_lines(path: Path) -> list[tuple[int, str]]:
    """Claims the line-at-a-time gate cannot see because the subject and the
    predicate sit on different lines. Reports the line the MATCH STARTS on."""
    starts: list[tuple[int, int]] = []
    parts: list[str] = []
    pos = 0
    for lineno, line in enumerate(
        path.read_bytes().decode("utf-8", errors="ignore").splitlines(), 1
    ):
        collapsed = re.sub(r"\s+", " ", line).strip()
        if not collapsed:
            # ⛔ A blank line ENDS the paragraph: joining across it let two unrelated
            # sentences form a "… is durable" claim and red a correct edit
            # (#9134 review, P3).
            if parts:
                # ⛔ A NEWLINE, not a delete char: `WRAPPED_CLAIM_RE`'s `.{0,40}` window
                # cannot cross a newline, so the paragraph really ends here. The sentinel
                # has to be outside the window's reach, not merely present.
                parts.append("\n")
                # ⛔ 2, not 3: the element BEFORE this one already added +1 for the space
                # that follows it, so the sentinel contributes the newline plus the next
                # separator — two characters. Advancing 3 drifts one per break and shifts
                # every reported line number after it (#9134 review).
                pos += 2
            continue
        starts.append((pos, lineno))
        parts.append(collapsed)
        pos += len(collapsed) + 1
    joined = " ".join(parts)
    out: list[tuple[int, str]] = []
    for match in WRAPPED_CLAIM_RE.finditer(joined):
        lineno = next(n for p, n in reversed(starts) if p <= match.start())
        out.append((lineno, joined[max(0, match.start() - 60) : match.end() + 60]))
    return out


# ── 1. the scan reaches the declared roots ──────────────────────────────────

def test_a_blank_line_ends_the_paragraph_for_wrapped_claims(tmp_path):
    """The joined re-scan must not cross a paragraph break.

    `WRAPPED_CLAIM_RE` allows up to 40 characters between "is" and "durable", so joining
    two paragraphs let unrelated prose form a claim and would have red a correct edit.
    The boundary is a NEWLINE sentinel, which `.` cannot cross; a claim wrapped inside ONE
    paragraph is still found.
    """
    two_paragraphs = tmp_path / "two.md"
    two_paragraphs.write_text(
        "The append is the journal's commit point.\n\nThe store is durable under fsync.\n",
        encoding="utf-8",
    )
    wrapped = tmp_path / "wrapped.md"
    wrapped.write_text(
        "COMMIT POINT (the journal IS the\ntruth), so a refusal would leave a record.\n",
        encoding="utf-8",
    )

    assert _wrapped_claim_lines(two_paragraphs) == []
    assert len(_wrapped_claim_lines(wrapped)) == 1

    # The recorded line number must survive the breaks: a claim three paragraphs down is
    # at TRUE line 7. This pins the behaviour, not the arithmetic — the offset advance for
    # the sentinel was MEASURED at 2 characters (the element before it already counts the
    # separator space), because a larger advance drifts one per break; a drift that small
    # stays inside the neighbouring line's interval, so only the offset measurement
    # distinguishes it, not this assertion.
    deep = tmp_path / "deep.md"
    deep.write_text(
        "first.\n\nsecond.\n\nthird.\n\nCOMMIT POINT (the journal IS the\n"
        "truth), so a refusal would leave a record.\n",
        encoding="utf-8",
    )
    hits = _wrapped_claim_lines(deep)
    assert [line for line, _ in hits] == [7], hits


def test_scan_reaches_the_declared_roots():
    """Every declared root must exist AND contribute scanned files — a
    narrowed OR deleted root must fail here, not silently shrink the scan
    (review finding: the old hand-maintained sentinel list was not bound to
    the declared roots, so a missing/renamed root passed vacuously)."""
    scanned = {p.relative_to(REPO).as_posix() for p in _scanned_files()}
    for rel in SCAN_FILES:
        assert (REPO / rel).is_file(), f"declared scan file is missing: {rel}"
        assert rel in scanned, f"declared scan file not scanned: {rel}"
    for pattern in SCAN_GLOBS:
        matches = sorted(REPO.glob(pattern))
        assert matches, f"declared scan glob matches nothing: {pattern}"
        for path in matches:
            assert path.relative_to(REPO).as_posix() in scanned
    for root in SCAN_DIRS:
        assert (REPO / root).exists(), f"declared scan dir is missing: {root}"
        assert any(rel.startswith(root + "/") for rel in scanned), (
            f"declared scan dir contributed no files: {root}"
        )
    # And the named sentinels the gate's guarantee rests on.
    for sentinel in (
        "tortoise/log.py",
        "tortoise/consistency.py",
        "tortoise/projection/__init__.py",
        "tortoise/commit_ops.py",
        "README.md",
        "docs/durability-posture.md",
        "docs/quickstart-selfhosted.md",
        "docs/quickstart-cloud.md",
        "docs/infra-runbook.md",
        "docs/data-safety.md",
        "docs/ONTOLOGY.md",
        # #4308 — the runnable scripts that carried the claim outside §6's roots
        "graph-scripts/add_convergence_evidence.py",
        "graph-scripts/baseline_scan.py",
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


# ── 2b. the two #4308 sentinels, pinned window-independently ────────────────

# ``CLAIM_RE``'s 40-char window is the gate's SCOPE, not a licence for the
# same claim with more prose between subject and predicate (the canonical doc
# says so: "The gate matches a CLAIM SHAPE, not durability claims in general").
# #4308 closed the residual in these two runnable scripts, so pin the residual
# where it was found: in either sentinel the phrase must be absent at ANY
# distance. The pre-fix ``q1_unified`` (:58) carried it ~80 chars out and was
# invisible to the gate (review finding).
_GRAPH_SCRIPTS_SENTINELS = (
    "graph-scripts/add_convergence_evidence.py",
    "graph-scripts/baseline_scan.py",
)


def test_graph_scripts_sentinels_carry_no_source_of_truth():
    offenders: list[str] = []
    for rel in _GRAPH_SCRIPTS_SENTINELS:
        text = (REPO / rel).read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), 1):
            if "source of truth" in line.lower():
                offenders.append(f"{rel}:{lineno}: {line.strip()[:120]}")
    assert not offenders, (
        "the durability authority is " + CANONICAL_DOC_REL + " — the two "
        "#4308 scripts carry the reconstruction framing, so no window may "
        "restore 'source of truth':\n" + "\n".join(offenders)
    )


def test_convergence_q3_roles_are_not_written_as_a_contradiction():
    """``NAND`` means "A contradicts B". Under the authority the JSONL
    (reconstruction source) and the store (durability authority) are
    COMPLEMENTARY roles, so a ``NAND`` between ``q3_jsonl`` and ``q3_falkor``
    asserts a conflict the authority denies — and the same file's
    ``q3_hybrid`` supports both. The pre-fix Q3 reword left exactly that
    (review finding: the edge's content asserted non-contradiction)."""
    rel = "graph-scripts/add_convergence_evidence.py"
    offenders: list[str] = []
    for lineno, line in enumerate((REPO / rel).read_text(encoding="utf-8").splitlines(), 1):
        if not line.lstrip().startswith("NAND("):
            continue
        if {"q3_jsonl", "q3_falkor"} <= set(re.findall(r"q3_\w+", line)):
            offenders.append(f"{rel}:{lineno}: {line.strip()[:120]}")
    assert not offenders, (
        "q3_jsonl (reconstruction source) and q3_falkor (durable store) are "
        "complementary under " + CANONICAL_DOC_REL + " — a NAND between them "
        "asserts a false conflict:\n" + "\n".join(offenders)
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
    the authority statement — assert the SPECIFIC phrase, not merely that some
    regex hit exists (the gate's own description at the foot of the doc also
    matches, so a bare hit check could pass on a doc with no authority claim)."""
    assert _claim_lines(CANONICAL_DOC), (
        f"{CANONICAL_DOC_REL} no longer contains the durability authority "
        "statement the gate exempts — fix the doc, not the gate"
    )
    text = CANONICAL_DOC.read_text(encoding="utf-8")
    assert re.search(r"single source of truth for durability", text, re.I), (
        f"{CANONICAL_DOC_REL} must state that it is the single authority for "
        "durability claims, not merely contain a gate-shaped regex hit"
    )


# ── 4. the regex fires on the defect and not on unrelated prose ─────────────

# ``CLAIM_RE`` is line-at-a-time, so wrapping is the one hole left in it. These
# two tests close it: the detector must see a wrapped claim (or the file scan
# below proves nothing), and the file scan must find none anywhere.
def test_wrapped_detector_sees_a_claim_split_across_lines(tmp_path):
    """Non-vacuity for :func:`_wrapped_claim_lines`: the exact pre-fix shape at
    ``tortoise/api.py`` (subject + ``truth`` on either side of a newline) is
    reported, at the line the claim STARTS on. Deleting the join — or falling
    back to the line-at-a-time scan — must fail this."""
    target = tmp_path / "wrapped.py"
    target.write_text(
        "x = 1\n"
        "        load-bearing: the append is this seam's COMMIT POINT (the journal IS the\n"
        "        truth), so a refusal raised after it would leave a record,\n",
        encoding="utf-8",
    )
    hits = _wrapped_claim_lines(target)
    assert [lineno for lineno, _ in hits] == [2], hits
    # ...while the line-at-a-time gate really is blind to it.
    assert _claim_lines(target) == []


def test_wrapped_detector_sees_the_durability_alternation(tmp_path):
    """The `durable` half of the predicate must be in the JOINED scan too.

    The `is truth` case above left the durability alternation unexercised: with
    the `durable`/`durability` branch deleted from `WRAPPED_CLAIM_RE`, every test
    stayed green — so "the same claim cannot come back in a different wording" was
    an assertion about an untested half (#9134 review, P2).

    Non-vacuity: drop the durability alternation from `WRAPPED_CLAIM_RE` and this
    reds while the `is truth` test stays green.
    """
    target = tmp_path / "wrapped_durable.py"
    target.write_text(
        "x = 1\n"
        "        the append is this seam's COMMIT POINT (the journal IS our\n"
        "        durable record), so a refusal raised after it would leave one,\n",
        encoding="utf-8",
    )
    hits = _wrapped_claim_lines(target)
    assert [lineno for lineno, _ in hits] == [2], hits
    # The line-at-a-time gate cannot see a claim that spans the newline.
    assert _claim_lines(target) == []


def test_no_wrapped_durability_claim_anywhere_in_the_scanned_roots():
    """A line break must not hide the PREDICATE claim from the gate.

    This is a FORWARD guard, and the docstring used to say otherwise: it cited
    ``tortoise/api.py`` (`the journal IS the` / `reconstruction source`) as a
    live instance of a wrapped claim the gate would miss. That is wrong twice
    over. `api.py` carries the `source` shape, not the `is truth` / `is durable`
    predicate this scan joins — and `source` is exactly the family the SCOPE
    paragraph below says is deliberately NOT joined. Measured when this was
    corrected: the repo-wide scan finds **0** hits, so there is no live
    wrapped instance of the covered predicates anywhere in the scanned roots.

    The guard still earns its place, because the failure it forbids is silent by
    construction: a reword that wraps the predicate across a newline would leave
    a line-at-a-time scan reporting clean while the claim stands — a gate that
    fails open is worse than no gate, since it certifies what it did not read.
    What is NOT claimed is that such a claim exists today.

    SCOPE: the joined re-scan covers only the two predicate alternations
    (`is truth` / `is durable`), not `source of truth`. That is deliberate and
    measured — the `source of truth` alternations have legitimate cross-line
    neighbours in this tree (`corpus files (source of truth), (2) the FULL
    `events/` JSONL directory`, `single source of truth with apply, #330` /
    `# Journal-order`), so joining them repo-wide fabricates false positives. A
    wrapped `source of` / `truth` is therefore NOT caught here; narrowing the
    claim in that direction is what keeps this gate honest about its own reach.
    """
    offenders: list[str] = []
    for path in _scanned_files():
        rel = path.relative_to(REPO).as_posix()
        if rel == CANONICAL_DOC_REL:
            continue
        for lineno, snippet in _wrapped_claim_lines(path):
            offenders.append(f"{rel}:{lineno}: {snippet[:160]}")
    assert not offenders, (
        "the durability authority is " + CANONICAL_DOC_REL + " — a claim split "
        "across lines is the same claim; delete it and link the canonical doc:\n"
        + "\n".join(offenders)
    )


def test_claim_regex_matches_the_original_defect_forms():
    """Every form the deleted sites actually carried — including the two the
    owner's exact pattern cannot see, which this change also removes."""
    for claim in (
        "Append-only JSONL event log — the source of truth.",
        "The event log is the source of truth; the projection is a derived view.",
        "the JSONL event stream is the rebuild source of truth",
        # the two forms the owner's pattern misses (review finding):
        "The log is the source of truth; a projection is a derived view.",
        "the journal/event stream is the truth",
        "the event stream is the truth",
        "# §11: the event log is truth; replay re-truncates identically",
        # the REVERSE direction must cover the widened subjects too:
        "the source of truth is the log",
        "the source of truth is the event stream",
        # the durability-worded second alternation:
        "the source of truth for durability claims",
        # #9134's SUBJECT-ATTRIBUTION alternation (the 4th). Without these
        # cases that alternation is unpinned: deleting it left every
        # assertion green, so the wording it exists to catch went untested.
        # The first one is the exact reword that greened a real red.
        "the journal is the durable record",
        "the event log is the durability mechanism",
        # the DETERMINER slot, not just `the`: a reword that swaps the article
        # is the same claim, and pinning `(?:the )?` saw only the reviewed one.
        "the journal is a durable record",
        "the journal is our durable record",
        "the event log is its durability mechanism",
        # `_SUBJECT` includes a bare `log`, so this is DELIBERATELY a hit: it
        # attributes durability to the log, which the canonical doc denies.
        "the log is durable",
    ):
        assert CLAIM_RE.search(claim), f"scan misses a real claim: {claim!r}"


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
        # `\blog\b` must not fire inside `changelog`:
        "the changelog is the source of truth for released versions",
        # The 4th alternation's boundary: a durability statement whose subject
        # is NOT a journal/event log is the canonical doc's own framing.
        "the store is the durability authority",
        "the store's own persistence is durable; the journal is not",
    ):
        assert not CLAIM_RE.search(unrelated), (
            f"scan false-positives on unrelated copy: {unrelated!r}"
        )
