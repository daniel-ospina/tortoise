"""#9399 — the §13 "primary search path" claim must name the scope it was MEASURED over.

THE DEFECT THIS PINS
--------------------
`docs/architecture/STORAGE-ARCHITECTURE.md` §13 carries the sentence that the
`aboutObject` ruling rests on:

    **The primary search path does NOT read it.** `rg -n 'aboutObject'
    tortoise/retrieval.py tortoise/search_engine.py tortoise/session_reinjection.py`
    -> exit 1, zero matches. Hindsight's premise holds for this path.

The measurement is TRUE **and the sentence is unscoped**. The grep covers exactly
three modules, and `tortoise/ranking.py` — which holds both product ranking read
paths, and therefore the reads behind the default recall DOOR — is not one of
them. So the sentence is true of the default FTS/vector/RRF retrieval *lane* and
false of `tortoise_recall` / `recall_state`, which is the door a reader of §13
would take "the primary search path" to mean (and the door whose four reads made
the count decisive in the first place, #9399).

That is not a pedantic distinction: **the count is the whole argument** (§13 says
so itself) and the ruling is KEEP the stored edges. A reader who checks only the
quoted command concludes the primary path never touches the edge; a reader who
stops there undercounts the blast radius of the very change the section rules on.

WHAT THIS PINS
--------------
1. `test_the_quoted_command_is_true_over_the_files_it_names` — the claim is not
   merely asserted: the file list is parsed OUT of the sentence, each file must
   exist, and the scan is re-run over exactly those files (zero matches,
   case-insensitive). If a future edit widens the list to a module that does read
   the edge, the claim reds here instead of quietly becoming false.
2. `test_the_claim_names_the_module_it_excludes` — the sentence must state the
   scope it was measured over: `ranking.py` named as OUTSIDE the file list, and
   the recall door named as the reading path. This is the arm that is RED before
   the fix and GREEN after it — a test that passes without the fix proves nothing.
3. `test_the_excluded_module_really_reads_the_edge` — non-vacuity for arm 2: the
   named exclusions must actually read the edge, at HEAD, so the scoping is
   load-bearing rather than decoration. If `ranking.py` stops reading it, the
   qualifier reds here rather than surviving as a stale claim of its own.

NOT promised:
- No count is asserted here. The #7948 census table is #7948's to pin
  (`tests/test_7948_about_object_read_sites.py`); this file pins the SCOPE of the
  headline sentence only, so it is independent of how many rows the table ends up
  carrying and does not have to be re-cut when that number moves.
- No absence assertions on the old wording: a sibling lane may legitimately carry
  the pre-qualification sentence while re-deriving the table, and this file must
  not red on a PR whose subject is elsewhere. Every arm here asserts a PRESENCE.

Static, stdlib-only, no network / DB / graph.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOC = ROOT / "docs" / "architecture" / "STORAGE-ARCHITECTURE.md"

#: The quoted command is the anchor: it appears in the claim BEFORE and AFTER the
#: fix, so locating the claim does not depend on the wording this file pins.
CLAIM_ANCHOR = "rg -n 'aboutObject'"

#: `rg -n 'aboutObject' tortoise/a.py tortoise/b.py` -> the file list.
QUOTED_FILES_RE = re.compile(r"rg -n 'aboutObject'\s+([^`]+)`")

#: The module the three-file measurement cannot see, and the door that reads the
#: edge through it. Both are asserted by name in the claim (arm 2).
EXCLUDED_MODULE = "ranking.py"
EXCLUDED_MODULE_PATH = ROOT / "tortoise" / "ranking.py"
SDK_PATH = ROOT / "tortoise" / "sdk.py"


def _claim_line() -> str:
    """The single markdown line carrying §13's quoted measurement."""
    matches = [
        line for line in DOC.read_text(encoding="utf-8").splitlines() if CLAIM_ANCHOR in line
    ]
    assert matches, (
        f"§13's quoted measurement is gone from {DOC.name}: no line contains "
        f"{CLAIM_ANCHOR!r}. The evidence for the KEEP ruling must stay re-runnable."
    )
    assert len(matches) == 1, (
        "expected exactly one line to carry the §13 quoted measurement, found "
        f"{len(matches)} — an ambiguous anchor makes every arm below guess which "
        "claim it is checking"
    )
    return matches[0]


def _quoted_files(claim: str) -> list[str]:
    match = QUOTED_FILES_RE.search(claim)
    assert match, (
        "§13's measured file list is not parseable out of the claim line; the "
        "sentence must keep the re-runnable command shape "
        "`rg -n 'aboutObject' <files>`"
    )
    return match.group(1).split()


def test_the_quoted_command_is_true_over_the_files_it_names() -> None:
    """Re-run the measurement §13 quotes, over exactly the files it names."""
    files = _quoted_files(_claim_line())
    assert files, "the claim quotes a command with no file list"

    hits: list[str] = []
    for rel in files:
        path = ROOT / rel
        assert path.is_file(), (
            f"§13's claim greps {rel!r}, which does not exist — the measurement "
            "can no longer be re-run by a reader"
        )
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "aboutobject" in line.lower():
                hits.append(f"{rel}:{lineno}: {line.strip()}")

    assert not hits, (
        "§13 claims the quoted grep returns zero matches, but it now matches:\n  "
        + "\n  ".join(hits)
        + "\nEither the claim's file list is wrong or the lane does read the edge — "
        "both change the ruling's evidence and neither may land silently (#9399)."
    )


def test_the_claim_names_the_module_it_excludes() -> None:
    """The sentence must scope itself, not read as a whole-product claim.

    RED before the fix: the pre-#9399 sentence names neither the module outside
    the measurement nor the door that reads through it, so a reader takes "the
    primary search path" to cover `recall` — which is false.
    """
    claim = _claim_line()
    files = _quoted_files(claim)

    assert EXCLUDED_MODULE not in files, (
        f"the claim's measured file list now contains {EXCLUDED_MODULE} — if the "
        "measurement was widened, the sentence's own scoping must be re-derived "
        "rather than left describing the old three-file scope"
    )

    assert EXCLUDED_MODULE in claim, (
        f"§13's claim does not name {EXCLUDED_MODULE}, the product ranking module "
        "that sits OUTSIDE its three-file measurement while reading the edge — so "
        "the sentence is unscoped and reads as a claim about the whole search path "
        "(#9399)"
    )

    lowered = claim.lower()
    assert "recall" in lowered and "door" in lowered, (
        "§13's claim must name the recall DOOR as the path that does read the edge "
        "through the excluded module — otherwise a reader checking only the quoted "
        "grep concludes the primary recall path never touches it (#9399)"
    )

    assert "lane" in lowered, (
        "§13's claim must say which LANE the zero-match measurement bounds (the "
        "default FTS/vector/RRF retrieval lane) — the scope is the claim (#9399)"
    )


def test_the_excluded_module_really_reads_the_edge() -> None:
    """Non-vacuity: the exclusions arm 2 names must read the edge at HEAD."""
    ranking = EXCLUDED_MODULE_PATH.read_text(encoding="utf-8")
    assert "aboutObject" in ranking, (
        f"{EXCLUDED_MODULE_PATH.name} no longer mentions `aboutObject`; the §13 "
        "scoping claim (and the rows that cite this module) describe a read that "
        "does not exist — re-derive the claim instead of keeping a stale one"
    )
    assert re.search(r'ABOUT_EDGE_TYPES\s*=\s*\((?:[^)]*)"aboutObject"', ranking), (
        "`ABOUT_EDGE_TYPES` no longer carries `aboutObject`; the alternation the "
        "claim cites as the reason a literal grep misses the ranking reads is gone"
    )

    sdk = SDK_PATH.read_text(encoding="utf-8")
    assert re.search(r'_ABOUT_TYPES\s*=\s*"[^"]*aboutObject', sdk), (
        "`TortoiseSDK._ABOUT_TYPES` no longer carries `aboutObject`, so the "
        "`recall_state` enrichment readers the claim cites no longer read the edge"
    )
    for symbol in ("_state_related_objects", "_state_related_points"):
        assert f"def {symbol}(" in sdk, (
            f"`{symbol}` — the `recall_state` enrichment reader the claim names as "
            "reaching the edge through `_ABOUT_TYPES` — no longer exists"
        )
