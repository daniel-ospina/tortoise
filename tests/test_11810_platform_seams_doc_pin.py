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

`api` is not decoration either: a change to `tortoise/__main__.py` matches
`SOURCE_PATTERNS["api"]`, which REPLACES the `core` fallback — measured,
`printf 'tortoise/__main__.py\\n' | tools/ci_selection.py --changed-files -` →
`surfaces: ['api']`. Without the `api` row the message-direction arm would sleep on
the only diff that can break it.

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

So `_normalised()` reduces text to its word sequence — anything that is not a
letter or digit becomes a separator, and case is folded. That is what makes the
class closed rather than enumerated: reflow, tab/`\r\n`/nbsp, zero-width space,
case, hyphenation, and markdown emphasis anywhere in the phrase (whole-phrase
`*"…"*`, or in-phrase `**cloud agent VM**`, `` `cloud agent VM` ``,
`*cloud*`, `~~…~~`, `_…_`) all normalise to the same words. Measured RED on the
absence arm, and the same change cures the presence arm's mirror defect — it
used to false-RED a CORRECT quote whose `hosted` qualifier was emphasised.

WHAT IT STILL DOES NOT CATCH, stated so the docstring is not the next over-claim:
a PARAPHRASE, a synonym, or a re-worded sentence (`A Cursor cloud agent runs no
Tortoise hook at all`) is beyond any string guard. `test_capture_install.py`
carries an enumerated list for that, and marks it as enumerated — that is the
honest shape for prose, and #11812 holds the structural form.

MUTATION (each verified RED at this head, restoring byte-identical):
  * restore the unscoped quote at `platform-seams.md:315`      → (1) RED
  * re-add with OTHER delimiters, whole-phrase (`*"…"*`)      → (1) RED (r8)
  * re-add with a LINE BREAK / double space / capital           → (1) RED (r8)
  * re-add with in-phrase `**`, `` ` ``, `*`, `~~`, `_`         → (1) RED (r9)
  * re-add with a zero-width space or a hyphen                 → (1) RED (r9)
  * correct quote with `**hosted**` emphasised → now PASSES    → was RED (P3)
  * drop `hosted` from `tortoise/__main__.py`'s message         → (2) RED
  * delete the record's quoted sentence                         → (1) RED
"""
from __future__ import annotations

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


def _normalised(text: str) -> str:
    """The WORDS of `text`, for BOTH arms — punctuation, delimiters and case gone.

    Everything that is not a letter or digit becomes a separator, and case is
    folded, so the two sides are compared as word sequences. That is what makes
    reflow, tab/`\\r\\n`/nbsp, zero-width space, hyphenation and markdown
    emphasis INDISTINGUISHABLE from the plain words — including a delimiter in the
    MIDDLE of the phrase, which a whitespace-only collapse still let through.

    It deliberately does NOT strip accents or fold synonyms: a paraphrase is out
    of reach of any string guard, and pretending otherwise would be the same
    over-claim in a new place.

    One function for both arms, so they cannot drift apart — the failure this
    file exists to catch, one level up.
    """
    return re.sub(r"[\W_]+", " ", text.lower()).strip()


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
    # …and the unscoped WORDS may not reappear at all, in ANY form. Three rounds
    # shipped a form-exact leg here and each false-passed the mutation it exists to
    # catch — r7 a whole-phrase re-emphasis (`*"…"*`), r8 a hard wrap inside the
    # phrase on this 95-column-wrapped record, r9 a delimiter in the MIDDLE of the
    # phrase (`**cloud agent VM**`, `` `cloud agent VM` ``, `*cloud*`, `~~…~~`,
    # `_…_`, a zero-width space, a hyphen). Matching on the word sequence closes the
    # class instead of enumerating it. The assertion stays over-broad in the SAFE
    # direction only (it also bans a hypothetical accurate sentence carrying these
    # words), and it cannot false-RED the correct quote — the unscoped word sequence
    # is not a substring of the hosted one.
    assert _normalised(_UNSCOPED_FRAGMENT) not in normalised_doc, (
        "the record quotes the unscoped universal as though it were the message")


def test_the_shipped_message_carries_the_fragment_the_record_quotes(tmp_path):
    """Direction 2 — the message the record cites.

    Mutation: drop `hosted` from `tortoise/__main__.py`'s install message — the
    record's quoted fragment is then no longer produced, and this REDs."""
    stdout = _install_cursor_stdout(tmp_path)
    assert _normalised(_SHIPPED_FRAGMENT) in _normalised(stdout), stdout
