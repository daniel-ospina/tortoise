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

⛔ WHY (1) READS THE BARE WORDS AND NOT A DELIMITED QUOTE (r8 P2, blocking). It
shipped form-exact — `f'("{_UNSCOPED_FRAGMENT}")' not in doc` — and that
false-passed in the ONE direction the pin exists for: re-adding the retracted
universal with different delimiters. Review r8 reproduced it GREEN by appending
`As the message says: *"a session running in a cloud agent VM"*.` beside the
correct line 315. Matching one literal form is the fail-open direction, and it is
exactly the policy `test_capture_install.py`'s guard comment rejects one arm over.
The bare form costs nothing: the unscoped string is NOT a substring of the hosted
one (verified), so it cannot false-RED on a correct quote, and it is over-broad in
only the safe direction — like those sibling guards, it also bans a hypothetical
accurate sentence carrying those exact words.

MUTATION (each verified RED at this head, restoring byte-identical):
  * restore the unscoped quote at `platform-seams.md:315`      → (1) RED
  * re-add the unscoped words with OTHER delimiters (`*"…"*`)  → (1) RED (r8)
  * drop `hosted` from `tortoise/__main__.py`'s message         → (2) RED
  * delete the record's quoted sentence                         → (1) RED
"""
from __future__ import annotations

import os
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
    assert f'("{_SHIPPED_FRAGMENT}")' in doc, (
        "the platform-seams record must quote the HOSTED-scoped message")
    # …and the unscoped WORDS may not reappear at all, in ANY form. This leg was
    # form-exact at r7 (`("…")`) and false-passed the one mutation it exists to
    # catch: r8 appended `*"a session running in a cloud agent VM"*` and got GREEN.
    # Bare words cannot false-RED on the correct quote (the unscoped string is not
    # a substring of the hosted one), so the only cost is over-breadth in the safe
    # direction — the same deliberate trade the sibling guards make.
    assert _UNSCOPED_FRAGMENT not in doc, (
        "the record quotes the unscoped universal as though it were the message")


def test_the_shipped_message_carries_the_fragment_the_record_quotes(tmp_path):
    """Direction 2 — the message the record cites.

    Mutation: drop `hosted` from `tortoise/__main__.py`'s install message — the
    record's quoted fragment is then no longer produced, and this REDs."""
    stdout = _install_cursor_stdout(tmp_path)
    assert _SHIPPED_FRAGMENT in stdout, stdout
