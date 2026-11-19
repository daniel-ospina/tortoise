"""#13634 — the *"declared priors are the open decision in #9063"* claim must not come back.

WHY THIS FILE EXISTS

PR #13634 shipped a changelog entry reading *"Whether entity declared priors should exist at all
remains open (#9063)"*. **#9063 is CLOSED / COMPLETED (2026-10-20)** — it *raised* the question (which
node kinds must carry a prior?) and **reserved it to the owner**, then closed **without deciding it**
(`docs/ONTOLOGY-CHANGELOG.md`'s correction appended by #13641; the question is stated as open and
reserved to the owner at `_CALIBRATION_EVIDENCE_KINDS`, which records no closure — do not read it as
one). So *"remains open (#9063)"* points a reader at a CLOSED issue for a
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

⛔ Three measurement decisions, each of which a plausible cheaper version got wrong:

* The changelog check collapses the entry's line-wrapping before searching: the entry is hard-wrapped at
  ~100 columns, so a per-line search is evaded by an ordinary re-wrap of the very claim it forbids
  (measured: `"… remains"` / `"> open (#9063)."` reproduces the false claim green).
* It matches **claim forms**, not "the words `open` and `#9063` within N characters of each other". A
  proximity window reds on legitimate text — *"should exist remains open (#14100); the code once cited
  #9063"*, or the repo's own correction convention, which quotes the withdrawn wording it disowns.
* It strips quoted spans before matching, for that second reason: a correction that RECORDS the old
  claim (*"as merged it read «…remains open (#9063)»"*) is the fix, not a regression.
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

#: Spans that RECORD a claim rather than assert one. The repo's correction convention quotes the
#: withdrawn wording (`*"…"*`); matching inside a quote would red the fix it is meant to protect. The
#: typographic forms are listed too: a correction written with `«…»` or curly quotes is the same
#: convention, and stripping only ASCII quotes false-blocks it (measured).
_QUOTED = re.compile(r'\*"[^"]*"\*|"[^"]*"|«[^»]*»|\u201c[^\u201d]*\u201d|\u2018[^\u2019]*\u2019')


def _claimed_open(issue: int):
    """The forms in which a document ASSERTS that `issue` is open, as opposed to quoting one.

    Claim-shaped on purpose: the openness wording must name the issue in the same phrase. A proximity
    match cannot tell *"remains open (#9063)"* from *"remains open (#14100) … the code once cited
    #9063"*, or from the correction paragraph that quotes the old claim in order to disown it.
    """
    n = re.escape(str(issue))
    forms = (
        r"remains\s+open\s*\(?\s*#" + n,  # remains open (#9063)
        r"remains\s+open\s*(?:[\u2014\u2013\-,;:]|\.\.\.)?\s*(?:see|cf\.?)\s+#" + n,  # remains open — see #9063
        r"#" + n + r"\s+(?:is\s+)?(?:still\s+|remains\s+)?open",  # #9063 is open
        r"open\s+issue\s+(?:is|in)\s+#" + n,  # the open issue is #9063
        r"tracked\s+(?:by|in)\s+#" + n,  # tracked by #9063
        # the retired sdk.py wording's own shape: "...are the open belief-model decision in #9063"
        r"(?:open|unsettled)\s+(?:[\w-]+\s+){0,3}(?:decision|question)\s+in\s+#" + n,
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
        unquoted = _QUOTED.sub(" ", block)
        for match in claim.finditer(unquoted):
            offending.append(unquoted[max(0, match.start() - 60): match.end() + 60])

    assert not offending, (
        "a v3.38 entry asserts #9063 is OPEN for declared priors; #9063 raised that question, "
        "reserved it to the owner, and closed COMPLETED on 2026-10-20 WITHOUT deciding it. "
        "Offending passage(s): " + " | ".join(offending)
    )
