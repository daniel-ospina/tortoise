"""#13634 — the *"declared priors are the open decision in #9063"* claim must not come back.

WHY THIS FILE EXISTS

PR #13634 shipped a changelog entry reading *"Whether entity declared priors should exist at all
remains open (#9063)"*. **#9063 is CLOSED / COMPLETED (2026-10-20)** — it *raised* the question (which
node kinds must carry a prior?) and **reserved it to the owner**, then closed **without deciding it**
(`docs/ONTOLOGY-CHANGELOG.md`'s correction appended by #13641; the same reading is stated in the code at
`_CALIBRATION_EVIDENCE_KINDS`). So *"remains open (#9063)"* points a reader at a CLOSED issue for a
question that is still undecided and tracked nowhere. That is the #5063 class this repo already treats
as a defect: *a pointer that does not resolve*.

The merged PR's own second commit corrected that changelog line. **The same claim survived in a place
the review never read** — the refusal a caller actually receives from
`TortoiseSDK.set_point_baseline`, which announced that non-Point priors "are the open belief-model
decision in #9063". A user-facing error message that offloads an unresolved question onto a closed issue
is the same dangling pointer, aimed at a caller instead of a doc reader.

WHAT IS ASSERTED, AND WHAT IS DELIBERATELY *NOT*

Both tests assert the **negative** — that the false pointer is absent — and neither pins the wording that
replaced it. That asymmetry is the point: if someone later FILES an issue for declared priors and cites
it in the changelog entry, that is a *correct* future state and this file must stay green. Pinning the
replacement text would convert this guard into a false block on the repair it exists to protect.

⛔ Four measurement decisions, each of which a plausible cheaper version got wrong:

* The changelog check collapses the entry's line-wrapping before searching: the entry is hard-wrapped at
  ~100 columns, so a per-line search is evaded by an ordinary re-wrap of the very claim it forbids
  (measured: `"… remains"` / `"> open (#9063)."` reproduces the false claim green).
* It matches **claim forms**, not "the words `open` and `#9063` within N characters of each other". A
  proximity window reds on legitimate text — *"should exist remains open (#14100); the code once cited
  #9063"*, or the repo's own correction convention, which quotes the withdrawn wording it disowns.
* It strips a quoted span only when a CORRECTION MARKER introduces it as its object (*"as merged it
  read «…remains open (#9063)»"*), for that second reason: a correction that RECORDS the old claim
  is the fix, not a regression. It does **not** strip every quoted span — an ordinary quotation is
  not a correction, so the claim surviving inside "…" is still the claim (measuring every span was a
  false pass) — and the marker must be adjacent, because the entry is collapsed to one line and a
  free window after `says` / `reads` laundered an assertion. Both directions are pinned by the last
  two tests in this file.
* It reads **every** `v3.38` block, not the first one. This file's append practice can add a second block
  with the same heading (`v3.33` already has two), and a claim restored there would otherwise stay green.

Both tests are offline — neither calls GitHub, so no tracker state is re-derived here. What is pinned is
the CLAIM, which is the thing that was wrong.
"""
import os
import re
import shutil
import tempfile
from pathlib import Path

import pytest

from tortoise.sdk import TortoiseSDK

ROOT = Path(__file__).resolve().parents[1]

#: Spans that RECORD a claim rather than assert one: a quotation the marker introduces as its
#: OBJECT — `as merged it read "…"`, `used to read «…»`, `carried *"…"*`, `withdrawn "…"`.
#: Two edges this rule has to hold at once, both measured (#13675 review, cycle 2):
#:   * stripping EVERY quoted span is a false pass — an assertion inside an ordinary quotation
#:     (`… should exist is "remains open (#9063)" today`) is still the assertion;
#:   * a FREE window after the marker is also a false pass — the entry is collapsed to one line, so
#:     `says nothing about it: "<claim>"` and `reads the predicate; … "<claim>"` would be stripped.
#: So the marker must be close (≤ ~14 chars: `*`, a space, `claim`, a colon) AND the vocabulary must
#: cover the repo's correction convention (`⚠️ CORRECTION APPENDED … It carried *"…"*`), or a real
#: correction turns into a false BLOCK. Both directions are pinned by the two tests at the end.
#: Every glyph family the repo writes a correction in: `"…"`, `“…”`, `‘…’`, `«…»`, `` `…` ``.
#: MARKER VOCABULARY — report VERBS, withdrawals, and `as merged` / `used to read` / `carried`. A
#: present-tense assertion verb (`asserts`, `claims`, `says`) is NOT a record marker: `The v3.38 entry
#: asserts "remains open (#9063)"` is the claim with a narrator in front of it (measured GREEN when it
#: was a marker). Nor are NOUNS/ADVERBS (`wording`, `correction`, `formerly`, `previously`): they do not
#: TAKE the quotation as their object, so `the entry's wording is "…"` was stripped and stayed GREEN
#: (measured). `read`/`reads` stay because they name the artifact's own text, not the author's position.
#: The tuple IS the vocabulary — the regex is built from it, and one pin per entry (the last test) means
#: an alternative can neither be dropped nor narrowed without reddening a row.
_RECORD_MARKERS = (
    "read",
    "reads",
    "reading",
    "used to read",
    "as merged",
    "withdrawn",
    "withdraws",
    "retired",
    "asserted",
    "claimed",
    "said",
    "stated",
    "wrote",
    "written",
    "carried",
)
_QUOTED_IN_CORRECTION = re.compile(
    r"\b(?:"
    + "|".join(marker.replace(" ", r"\s+") for marker in _RECORD_MARKERS)
    + r")\b[^\"\n]{0,14}?"
    r'(?:"[^"\n]*"|\u201c[^\u201d\n]*\u201d|\u2018[^\u2019\n]*\u2019|\u00ab[^\u00bb\n]*\u00bb|`[^`\n]*`)',
    re.I,
)


def _claimed_open(issue: int):
    """The forms in which a document ASSERTS that `issue` is open, as opposed to quoting one.

    Claim-shaped on purpose: the openness wording must name the issue in the same phrase. A proximity
    match cannot tell *"remains open (#9063)"* from *"remains open (#14100) … the code once cited
    #9063"*, or from the correction paragraph that quotes the old claim in order to disown it.
    """
    n = re.escape(str(issue))
    forms = (
        # The copula is NOT pinned to "remains": the claim family is `COPULA [still] [a] open
        # [(question|issue|tracker)] [SEP] (#N | in #N | see #N)`, and `remain` / `is` / `are` /
        # `stay` are the same assertion (measured: `remain open (#9063)` was green while `remains
        # open (#9063)` was red — a one-word evasion). The noun, the separator and the link are all
        # optional in real prose (`remains an open question (#9063)`, `remains open (see #9063)`,
        # `remains open — #9063`), each measured GREEN before it was added here.
        r"(?:remain(?:s)?|is|are|stay(?:s)?)\s+(?:still\s+)?(?:an?\s+)?open"
        r"(?:\s+(?:question|issue|tracker))?\s*(?:[\(\[]|,|;|:|\u2014)?\s*"
        r"(?:see\s+|in\s+|at\s+)?#" + n,
        # Form 2 is the MIRROR word order and gets the same copula family — widening only form 1
        # left `#9063 is open` / `#9063 stays open` / `#9063 remain open` green (measured).
        r"#" + n + r"\s+(?:(?:still|now)\s+)?(?:remain(?:s)?|is|are|stay(?:s)?)\s+"
        r"(?:(?:still|now)\s+)?(?:an?\s+)?open(?:\s+(?:question|issue|tracker))?",
        # The NOUN forms — this file is named after "the open tracker", and it was the one shape
        # the claim family did not cover (`#9063 is the open tracker`, `the open question is #9063`).
        r"open\s+(?:issue|question|tracker)\s+(?:is|in|remains)\s+#" + n,
        r"#" + n + r"\s+(?:is|remains|stays)\s+(?:(?:still|now)\s+)?the\s+open\s+(?:issue|question|tracker)",
        # The retired sdk.py wording's OWN shape, which mentions neither `open` next to the copula
        # nor a link: "…are the open belief-model decision in #9063".
        r"(?:open|unsettled)\s+(?:[\w-]+\s+){0,3}(?:decision|question)\s+in\s+#" + n,
        r"tracked\s+(?:by|in)\s+#" + n,  # tracked by #9063
    )
    return re.compile("|".join(forms), re.I)


@pytest.fixture
def sdk():
    """Fresh embedded TortoiseSDK on an isolated file DB (no Docker), per the repo's test convention."""
    db_path = os.path.join(tempfile.mkdtemp(prefix="tt_13634_"), "test.db")
    s = TortoiseSDK(db_path)
    yield s
    s.close()
    shutil.rmtree(os.path.dirname(db_path), ignore_errors=True)


def test_the_refusal_does_not_point_at_a_closed_issue(sdk):
    """The message a caller receives must not route the question through #9063 at all.

    Driven through the real refusal (a `:Source` cannot carry a Point baseline), so this reddens if the
    shipped message reverts — either to the #13634 wording, or to any other pointer at #9063.
    `#9063` is asserted ABSENT rather than merely "not called open": #9063 is permanently the wrong
    pointer for this question (it is closed and decided nothing), and a message that says "tracked in
    #9063" is the same defect in different words. The message states the design fact only; the tracker
    history lives in the code comment beside the raise, where it can be revised without changing the
    contract a caller sees.
    """
    sdk.register_source("https://example.org/13634-declared-prior", "web", tier="T1")

    with pytest.raises(ValueError) as excinfo:
        sdk.set_point_baseline("https://example.org/13634-declared-prior", 2.0, 1.0)
    msg = str(excinfo.value)

    # The pre-existing guard (#9063): name the label that was FOUND, never ":Point".
    assert ":Source, not a :Point" in msg

    # THIS finding. One assertion, on the POINTER rather than the phrasing: the retired wording
    # ("…are the open belief-model decision in #9063") is caught by it, and so is any future rewording
    # that still routes the caller through a closed issue. Phrase-pinning is deliberately NOT done — a
    # future message may legitimately say "no open belief-model decision tracks one".
    assert "#9063" not in msg, (
        "the refusal routes the caller to #9063, which closed COMPLETED on 2026-10-20 without "
        f"deciding the question. Message: {msg!r}"
    )


def _entry_blocks(lines, version):
    """Every `**Changelog vX.Y` block for `version`, wrapping collapsed. `[]` if the heading is absent."""
    starts = [
        i for i, line in enumerate(lines) if f"**Changelog v{version}" in line
    ]
    blocks = []
    for start in starts:
        end = next(
            (i for i in range(start + 1, len(lines)) if "**Changelog v3." in lines[i]),
            len(lines),
        )
        blocks.append(" ".join(line.strip(" >") for line in lines[start:end]))
    return blocks


def test_no_v338_entry_calls_9063_the_open_tracker():
    """No v3.38 block may assert that #9063 is open, or that it tracks declared priors."""
    lines = (ROOT / "docs/ONTOLOGY-CHANGELOG.md").read_text(encoding="utf-8").splitlines()
    blocks = _entry_blocks(lines, "3.38")
    assert blocks, "the v3.38 changelog entry disappeared — re-derive what this file should pin"

    claim = _claimed_open(9063)
    offending = []
    for block in blocks:
        unquoted = _QUOTED_IN_CORRECTION.sub(" ", block)
        for match in claim.finditer(unquoted):
            offending.append(unquoted[max(0, match.start() - 60): match.end() + 60])

    assert not offending, (
        "a v3.38 entry asserts #9063 is OPEN for declared priors; #9063 raised that question, "
        "reserved it to the owner, and closed COMPLETED on 2026-10-20 WITHOUT deciding it. "
        "Offending passage(s): " + " | ".join(offending) + " — if the passage RECORDS the retired "
        "claim rather than asserting it, quote the withdrawn wording behind a record marker "
        "(`It carried *\"…\"*`, `used to read «…»`, `withdrawn \"…\"`): only a QUOTED span is "
        "treated as a record, so an unquoted re-statement reads as the claim however it is framed."
    )


# ── the strip rule itself, pinned in BOTH directions ────────────────────────────────────────────
# The two tests above are only as good as `_QUOTED_IN_CORRECTION` and `_claimed_open`, and each of
# them failed in one direction when it was written by hand (#13675 review, cycle 2: a 100-char marker
# window laundered `says nothing about it: "<claim>"`, and the marker list missed the repo's own
# `It carried *"<claim>"*` convention). Pin the two classes directly instead of trusting the regex.


@pytest.mark.parametrize(
    "passage",
    [
        'The withdrawn claim "remains open (#9063)" is retired.',
        'The v3.36 entry asserted "remains open (#9063)".',
        'It carried *"whether declared priors remain open (#9063)"*, now retired.',
        # The two alternatives a mutation sweep proved were UNPINNED (deleting either stayed green):
        # the `reads`/`read` record verb, and the single-curly glyph family.
        'The v3.38 entry reads `remains open (#9063)`; that was false.',
        "The old wording, withdrawn, read \u2018remains open (#9063)\u2019.",
        "As merged it read \u201cremains open (#9063)\u201d and that was false.",
        "The wording used to read \u00abremains open (#9063)\u00bb before the correction.",
        "The earlier text, withdrawn, used to read `remains open (#9063)`.",
    ],
)
def test_a_correction_that_RECORDS_the_claim_is_not_offending(passage):
    """A correction quotes the claim in order to DISOWN it — stripping must cover every glyph family."""
    unquoted = _QUOTED_IN_CORRECTION.sub(" ", passage)
    assert not _claimed_open(9063).search(unquoted), (
        "a correction recording the withdrawn claim was treated as an assertion: " + passage
    )


@pytest.mark.parametrize(
    "passage",
    [
        "whether declared priors should exist at all remains open (#9063).",
        "whether declared priors should exist at all remain open (#9063).",
        "whether declared priors should exist at all is still open (#9063).",
        "whether declared priors should exist at all stay open (#9063).",
        "whether declared priors should exist at all remains an open question (#9063).",
        "whether declared priors should exist at all is an open question in #9063.",
        "remains open \u2014 #9063",
        "remains open, #9063",
        "remains open: #9063",
        "remains open in #9063",
        "remains open (see #9063)",
        "#9063 is open on declared priors.",
        "#9063 stays open on declared priors.",
        "#9063 remain open on declared priors.",
        "#9063 is still open on declared priors.",
        "#9063 are open on declared priors.",
        "declared priors are the open belief-model decision in #9063",
        "declared priors are tracked by #9063",
        "declared priors are tracked in #9063",
        "#9063 is the open tracker for declared priors",
        "#9063 remains the open tracker",
        "#9063 is still the open tracker for declared priors",
        "the open tracker is #9063",
        "the open question is #9063",
        'The refusal says nothing about it: "whether declared priors exist remains open (#9063)".',
        'It previously described the gate. The tracker "remains open (#9063)".',
        'The code reads the predicate; whether declared priors should exist "remains open (#9063)".',
        'The v3.38 entry asserts "remains open (#9063)".',
        'The v3.38 entry claims "remains open (#9063)".',
        # nouns/adverbs are NOT record markers — they do not take the quote as an object (measured GREEN
        # while `wording`/`formerly` were in the vocabulary).
        'Whether entity declared priors should exist at all: the entry\'s wording is "remains open (#9063)"',
        'The entry, formerly corrected, now states "remains open (#9063)"',
    ],
)
def test_an_ASSERTION_of_the_claim_is_caught_in_every_phrasing(passage):
    """The mirror: a marker word NEARBY must not launder an assertion, and both word orders count."""
    unquoted = _QUOTED_IN_CORRECTION.sub(" ", passage)
    assert _claimed_open(9063).search(unquoted), (
        "an assertion of the #9063 claim escaped the guard: " + passage
    )


_RECORD_MARKER_PASSAGES = {
    "read": 'The v3.38 entry read "remains open (#9063)" before the correction.',
    "reads": 'The v3.38 entry reads "remains open (#9063)"; that was false.',
    "reading": 'The entry was written reading "remains open (#9063)".',
    "used to read": 'It used to read "remains open (#9063)" before the correction.',
    "as merged": 'As merged, "remains open (#9063)" was the wording recorded there.',
    "withdrawn": 'The withdrawn claim "remains open (#9063)" is retired.',
    "withdraws": 'This correction withdraws "remains open (#9063)".',
    "retired": 'The retired wording "remains open (#9063)" no longer applies.',
    "asserted": 'The v3.36 entry asserted "remains open (#9063)".',
    "claimed": 'The earlier text claimed "remains open (#9063)".',
    "said": 'The paragraph said "remains open (#9063)" and that was wrong.',
    "stated": 'The v3.38 entry stated "remains open (#9063)" at merge time.',
    "wrote": 'The author wrote "remains open (#9063)" in error.',
    "written": 'The entry was written "remains open (#9063)" in error.',
    "carried": 'It carried *"whether declared priors remain open (#9063)"*, now retired.',
}


@pytest.mark.parametrize("marker", sorted(_RECORD_MARKER_PASSAGES))
def test_every_record_marker_introduces_a_skipped_span(marker):
    """Each vocabulary entry, with its LITERAL passage, must introduce a skipped span.

    The row also asserts the entry is still in `_RECORD_MARKERS` (which the regex is built from), so
    dropping it from the tuple reds this row instead of deleting it — the first version of this test
    parametrized over the tuple itself and silently deleted its own pin (measured: `withdraws` and
    `written` vanished with the suite GREEN).
    """
    assert marker in _RECORD_MARKERS, (
        f"{marker!r} has a pin but is not in _RECORD_MARKERS — the guard lost a record marker, so a "
        f"real correction written that way is a false BLOCK"
    )
    passage = _RECORD_MARKER_PASSAGES[marker]
    unquoted = _QUOTED_IN_CORRECTION.sub(" ", passage)
    assert not _claimed_open(9063).search(unquoted), (
        f"the record marker {marker!r} no longer introduces a skipped span, so a legitimate "
        f"correction would become a false BLOCK: {passage}"
    )


def test_the_marker_vocabulary_and_its_pins_cannot_drift():
    """Every marker the regex accepts is pinned, and every pin names a marker the regex accepts."""
    assert set(_RECORD_MARKERS) == set(_RECORD_MARKER_PASSAGES), (
        "the vocabulary and its literal pins have diverged — an entry was added without a pin, or "
        "removed while its pin (and the correction it protects) stayed: "
        f"vocabulary-only={sorted(set(_RECORD_MARKERS) - set(_RECORD_MARKER_PASSAGES))} "
        f"pins-only={sorted(set(_RECORD_MARKER_PASSAGES) - set(_RECORD_MARKERS))}"
    )
