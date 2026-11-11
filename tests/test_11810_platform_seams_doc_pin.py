"""#11810 review (P2) — `platform-seams.md` and the shipped install message cannot drift apart.

WHY THIS PIN EXISTS. `docs/research/2026-09-01-gbrain-learnings/platform-seams.md` §3
cites the `tortoise install cursor` message as the evidence that the cloud-agent
exclusion is **HOSTED**-scoped, and that record is what a future lane cites for
#11807. The r6 review measured the drift in the DOC direction: the record quoted
*"a session running in a cloud agent VM"* — the UNSCOPED universal #11806 carried
and this PR removes — while the shipped message says *"in a HOSTED cloud agent VM"*.
A fragment in double quotes that is not in its source reads as evidence FOR the
over-assertion it sits next to.

WHY ITS OWN FILE, REGISTERED IN `tier1` (as well as `api` and `core`). The guarded
artifact is under `docs/`, and `docs/` is in `NON_PYTHON_PREFIXES`, so a
**docs-only** diff selects NO surface and takes the tier1-only early return. The pin
originally shipped inside `tests/test_capture_install.py` — which is registered on
`api` + `core` and NOT in `tier1` — so on a docs-only edit of this very record it ran
**zero times**: a lane doing #11807's doc half could restore the unscoped quote,
merge green, and only red `main` post-merge (the `push` build takes the full
selection). That is the #4309/#8474/#8614 class, and every sibling doc pin
(`test_10000_cache_projection_doc_pin.py`, `test_10191_record_move_doc_pin.py`,
`test_8093_registry_namespace_doc.py`, …) is dual/triple-registered for exactly this
reason.

`api` is registered for the message-direction arm: a change to
`tortoise/__main__.py` matches `SOURCE_PATTERNS["api"]`, which REPLACES the `core`
fallback — measured, `printf 'tortoise/__main__.py\\n' | tools/ci_selection.py
--changed-files -` → `surfaces: ['api']`.

⚠️ Not claimed: that `api` (or `core`) is what makes the pin RUN. Review r10 (P3)
measured the opposite and this file now states it: `select()` unions `tier1` into
**every** PR selection (`files = set(tier1)`, then the surface's members), so this
file's `tier1` row ALONE selects the pin on both the docs-only diff and the
`__main__.py` diff, and the two surface rows add no coverage. They are kept
belt-and-braces as the durable home — `tier1` is a curated smoke list, and one
edit there would drop the pin silently — not because either row carries an arm.
`tests/test_ci_selection.py` measures both facts instead of asserting them in prose.

WHAT IT ASSERTS, in BOTH directions:

  (1) the record quotes the HOSTED-scoped fragment, and the unscoped WORDS do not
      reappear **in any form** — the r6 defect;
  (2) the shipped message really contains the fragment the record quotes — so
      dropping `hosted` from the message (or restoring the unscoped quote in the
      record) REDs instead of passing silently.

`_SHIPPED_FRAGMENT` is the single string both arms bind to, so the two cannot be
corrected in opposite directions without this file noticing.

⛔ BOTH ARMS MATCH ON `_normalised()` — ONLY THE WORDS ARE THE CLAIM. Three
successive rounds shipped a form-exactness hole here, each one level deeper: r7
matched `("…")` only (a re-emphasis walked through); r8's bare-words fix was
still whitespace- and case-exact (a hard wrap inside the phrase walked through —
this record is hard-wrapped, line 315 is 95 columns); r9 stripped whitespace and
case but not PUNCTUATION, so a delimiter INSIDE the phrase walked through. Every
one of these is the same words and the same claim.

So `_normalised()` reduces text to its word sequence — HTML tags are stripped,
HTML entities are decoded, anything else that is not a letter or digit becomes a
separator, and case is folded. That closes every SEPARATOR-VALUED and
ENTITY/TAG-VALUED spelling of the same words: reflow, tab/`\r\n`, literal nbsp,
zero-width space, case, hyphenation, markdown emphasis anywhere in the phrase
(whole-phrase `*"…"*`, or in-phrase `**cloud agent VM**`, `` `cloud agent VM` ``,
`*cloud*`, `~~…~~`, `_…_`), and `&nbsp;` / `&#32;` / `&mdash;` / `<span>` / `<b>`
— the entity and tag forms render as the identical sentence, so a pin that only
blocked the literal characters would call the rendered artifact safe.

WHAT IT STILL DOES NOT CATCH, stated so the docstring is not the next over-claim:

  * a PARAPHRASE, a synonym, a different word order, or a JOINED token
    (`cloudagent`) — beyond any string guard. `test_capture_install.py` keeps an
    ENUMERATED list for that and says so; #11812 holds the structural form.
  * a delimiter INSIDE a word (`clou\u200bd`, `clou\u00add`, `cl<wbr>oud`) splits
    the word rather than separating it, so the fragment is no longer a word
    subsequence. No editor produces these; measured as missed, recorded rather
    than claimed.
  * a HOMOGLYPH (`\u0441loud` rendering as `cloud`). That needs a confusables
    table; it is out of proportion for a docs pin, so it is a recorded residual
    rather than a fix. (Measured: entities/tags now caught, homoglyphs not.)
  * this arm is deliberately OVER-BROAD in the safe direction: it bans the words
    wherever they appear in any order-adjacent form, so an HONEST correction that
    quotes the retracted wording to reject it — this record's own house style,
    see line 309 — REDs. Reword such an entry instead of reproducing the phrase
    verbatim. Likewise the presence arm answers "are these words in this file",
    not "is this an actual quote", so it cannot tell a quote from prose.

MUTATION (each verified RED at this head, restoring byte-identical):
  * restore the unscoped quote at `platform-seams.md:315`      → (1) RED
  * re-add with OTHER delimiters, whole-phrase (`*"…"*`)      → (1) RED (r8)
  * re-add with a LINE BREAK / double space / capital           → (1) RED (r8)
  * re-add with in-phrase `**`, `` ` ``, `*`, `~~`, `_`         → (1) RED (r9)
  * re-add with a zero-width space or a hyphen                 → (1) RED (r9)
  * re-add with `&nbsp;`/`&#32;`/`&mdash;`/`<span>`/`<b>`        → (1) RED (r10)
  * correct quote with `**hosted**` emphasised → now PASSES    → was RED (P3)
  * drop `hosted` from `tortoise/__main__.py`'s message         → (2) RED
  * delete the record's quoted sentence                         → (1) RED
  * widen the tag strip back to `<[^>]+>` and inject one
    "a hook count of < 5 is suspect" before §3's quote          → new tag test RED
"""
from __future__ import annotations

import html
import os
import re
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_PLATFORM_SEAMS_DOC = (_REPO_ROOT / "docs" / "research"
                       / "2026-09-01-gbrain-learnings" / "platform-seams.md")
#: The fragment the record quotes AND the message must carry. One constant, so a
#: correction applied to one side only cannot satisfy both arms.
_SHIPPED_FRAGMENT = "a session running in a hosted cloud agent VM"
_UNSCOPED_FRAGMENT = "a session running in a cloud agent VM"
_PY = sys.executable

#: A TAG-SHAPED strip, deliberately not `<[^>]+>`. The loose form spans any `<` …
#: `>` anywhere in a 50 KB document, so one ordinary sentence — "a hook count of
#: < 5 is suspect" — swallows everything up to the next `>`. Measured on THIS
#: record with that sentence injected before §3's quote: the loose strip removed
#: 2,755 bytes and took the quoted words with them, which would false-RED the
#: presence arm on a record that states the right thing. (It also strips 130
#: bytes of legitimate `<harness>` / `<ws>` prose that this form keeps.) This
#: form cannot cross a `<`, a `>`, or a line break.
_TAG = r"</?[A-Za-z][A-Za-z0-9-]*(\s[^<>\n]*)?/?>"


def _normalised(text: str) -> str:
    """The WORDS of `text`, for BOTH arms — markup, punctuation and case gone.

    HTML tags are removed and HTML entities decoded BEFORE the word collapse, so
    `&nbsp;`, `&#32;`, `&mdash;`, `<span>` and `<b>` — which RENDER as the same
    sentence — cannot carry a banned phrase past the guard. Everything that is
    then not a letter or digit becomes a separator, and case is folded, which
    makes reflow, tab/`\\r\\n`/literal nbsp, zero-width space, hyphenation and
    markdown emphasis INDISTINGUISHABLE from the plain words, including a
    delimiter in the MIDDLE of the phrase.

    What it deliberately does NOT do, so this docstring cannot become the next
    over-claim: no accent folding, no synonym or word-order handling (a
    paraphrase is out of reach of any string guard), and no HOMOGLYPH folding —
    `\\u0441loud` renders as `cloud` but needs a confusables table, which is out of
    proportion here and is a recorded residual.

    One function for both arms, so they cannot drift apart — the failure this
    file exists to catch, one level up.
    """
    text = re.sub(_TAG, " ", text)
    return re.sub(r"[\W_]+", " ", html.unescape(text).lower()).strip()


def _install_cursor_stdout(tmp_path) -> str:
    """The REAL `tortoise install cursor` output, on an isolated HOME.

    Not a source scrape: the pin is on what a customer is told, so it reads the
    same stdout `test_capture_install.py` reads. `TORTOISE_DB_URI=""` keeps it
    DB-free so the tier1 (URI-less) legs can run it.
    """
    root = tmp_path / "proj"
    home = tmp_path / "home"
    root.mkdir()
    home.mkdir()
    env = {
        **os.environ,
        "HOME": str(home),
        "TORTOISE_DB_URI": "",
        "TORTOISE_SECRET_PEPPER": "test-static-pepper",
    }
    r = subprocess.run(
        [_PY, "-m", "tortoise", "install", "cursor", "--dir", str(root)],
        env=env,
        cwd=str(root),
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    return r.stdout


def test_the_record_quotes_the_HOSTED_scoped_fragment():
    """Direction 1 — the record's quote, not the message's.

    Mutation: drop `hosted` from the quoted fragment (the r6 drift), or restore
    the unscoped quote beside it — both RED here."""
    doc = _PLATFORM_SEAMS_DOC.read_text(encoding="utf-8")
    normalised_doc = _normalised(doc)
    assert _normalised(_SHIPPED_FRAGMENT) in normalised_doc, (
        "the platform-seams record must quote the HOSTED-scoped message")
    # …and the unscoped WORDS may not reappear at all, in ANY form. Four rounds
    # shipped a form-exact leg here and each false-passed the mutation it exists to
    # catch — r7 a whole-phrase re-emphasis (`*"…"*`), r8 a hard wrap inside the
    # phrase on this 95-column-wrapped record, r9 a delimiter in the MIDDLE of the
    # phrase (`**cloud agent VM**`, `` `cloud agent VM` ``, `*cloud*`, `~~…~~`,
    # `_…_`, a zero-width space, a hyphen), r10 render-identical markup
    # (`&nbsp;`, `&#32;`, `&mdash;`, `<span>`, `<b>`). Matching on the word sequence
    # closes the spelling class instead of enumerating it; the docstring's residual
    # list names what it still cannot see (paraphrase, homoglyph) and the two places
    # it is deliberately over-broad.
    assert _normalised(_UNSCOPED_FRAGMENT) not in normalised_doc, (
        "the record quotes the unscoped universal as though it were the message")


def test_a_stray_angle_bracket_cannot_swallow_the_quoted_sentence():
    """The strip itself, pinned — review r10 measured this fail-open direction.

    `_normalised` is the only thing between the record and both arms, so its tag
    strip is load-bearing: the loose `<[^>]+>` runs from ANY `<` to the next `>`,
    so one ordinary half-sentence ("a hook count of < 5 is suspect") deletes
    everything up to the following `>`, including a correctly quoted sentence,
    and the pin would RED a record that is right. Hermetic: no file, no install.

    Mutation: widen `_TAG` back to `<[^>]+>` — this REDs (measured; the loose
    strip removes the quoted words from the sample below)."""
    sample = ('a hook count of < 5 is suspect. §3 says '
              '"a session running in a hosted cloud agent VM" and <b>bold</b>.')
    assert _normalised(_SHIPPED_FRAGMENT) in _normalised(sample), (
        "a stray `<` in ordinary prose must not eat the quoted sentence")
    assert _normalised(_UNSCOPED_FRAGMENT) not in _normalised(sample)


def test_the_shipped_message_carries_the_fragment_the_record_quotes(tmp_path):
    """Direction 2 — the message the record cites.

    Mutation: drop `hosted` from `tortoise/__main__.py`'s install message — the
    record's quoted fragment is then no longer produced, and this REDs."""
    stdout = _install_cursor_stdout(tmp_path)
    assert _normalised(_SHIPPED_FRAGMENT) in _normalised(stdout), stdout
