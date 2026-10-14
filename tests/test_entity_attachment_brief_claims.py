"""#8639 review P1 — the entity-attachment brief must not cite a REFUSED pack slot.

THE DEFECT THIS PINS
--------------------
`docs/research/2026-09-24-2730-entity-attachment/research-brief.md` §6.7 told
implementers that "the pack's `entityCues`, `entityPatterns`/`excludePatterns`
and `valueGate` slots (#1026) already declare what an entity name looks like".
Two of those three are not declaration surfaces at all:

* `entityPatterns` / `excludePatterns` are REFUSED keys — declaring either is a
  manifest-validation **ERROR** under the `#1026` owner ruling (*"no name
  patterns; nothing is dropped at mint"*), carried in
  `pack_registry.REFUSED_EXTRACTION_KEYS`;
* `entityCues` is declarable but deliberately has **NO consumer** (`#4894`);
* `valueGate` judges *utterance* value, not entity-name shape.

And the authority the sentence cited for its "no parallel rule set" rule
(`#1509 §2`) does not exist — `#1509` carries no numbered §2.

So the passage pointed a caller at the exact mechanism a recorded decision
rejected, from inside a brief whose own §5 contradiction test exists to prevent
exactly that. It is the `#8506` failure mode — a citation stale relative to a
later recorded decision — applied to a *decision* reference rather than a line
number. Per-kind surface forms already have ONE read home:
`ontology.kindDefs.<kind>.synonyms` / `.examples`, which
`compile_kind_index_spec` (`tortoise/value_extractor.py`) renders into the
classification surface.

WHAT THIS PINS
--------------
1. `test_the_retracted_passage_does_not_reappear` — the pre-fix sentence is
   matched as a literal. This is the NON-VACUITY half: it was present in the
   pre-fix document, so this file is red on it.
2. `test_the_corrected_passage_names_the_read_surface` — the repaired §6.7 must
   positively name the read surface *and* state the refusal; a revert of the
   wording reds. The PRESENT check is scoped to the §6.7 *section*, not the
   whole file: both literals are also carried by the §5 contradiction row, so a
   whole-file membership test stays green when §6.7 is silently DELETED — the
   exact mutation this guard's own message names (PR #8639 review, test-scope).
3. `test_the_code_still_agrees_with_the_corrected_passage` — the refusal set
   still refuses both keys, `entityCues` is still the *declarable* slot, and
   the read surface is still the one `compile_kind_index_spec` READS from the
   pack's kindDefs and RENDERS into the classification surface. If the
   underlying `#1026`/`#4894` work is reversed, this reds instead of the brief
   silently re-rotating.

Static, stdlib-only, no network / DB / graph.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BRIEF = (
    ROOT
    / "docs"
    / "research"
    / "2026-09-24-2730-entity-attachment"
    / "research-brief.md"
)
PACK_REGISTRY = ROOT / "tortoise" / "pack_registry.py"
VALUE_EXTRACTOR = ROOT / "tortoise" / "value_extractor.py"

#: The pre-fix §6.7 sentence, matched as a literal. It was live in the document
#: this guard was filed against, so the guard is red on it.
RETRACTED = (
    "`entityCues`, `entityPatterns`/`excludePatterns` and `valueGate` slots "
    "(#1026) already declare what an entity name looks like"
)

#: What the repaired passage must state. A revert, or a reword that drops either
#: half, reds: the read surface AND the refusal that closed the pattern slot.
LANDED = (
    "`ontology.kindDefs.<kind>.synonyms`",
    "no name patterns; nothing is dropped at mint",
)


def _brief_text() -> str:
    return BRIEF.read_text(encoding="utf-8")


def _block(source: str, pattern: str, what: str) -> str:
    match = re.search(pattern, source, re.S)
    assert match, f"{what} has been renamed or removed — re-verify §6.7"
    return match.group(1)


#: §6.7's body up to the next level-2 heading. The guard's PRESENT assertions are
#: scoped to THIS slice: the retracted sentence's replacement is §6.7's job, and
#: both LANDED literals are also carried by the §5 contradiction row, so a
#: whole-file check cannot tell "§6.7 corrected" from "§6.7 deleted".
_SECTION_67 = r"### 6\.7 Alias resolution(.*?)(?=\n## )"


def test_the_retracted_passage_does_not_reappear():
    assert RETRACTED not in _brief_text(), (
        "§6.7 names refused name-pattern keys as existing declaration surfaces. "
        "`entityPatterns`/`excludePatterns` are refused at manifest validation "
        "(`pack_registry.REFUSED_EXTRACTION_KEYS`) under the #1026 ruling, and "
        "`entityCues`/`valueGate` do not declare entity-name shape — the "
        "passage re-opens a recorded decision from inside a brief whose §5 "
        "exists to prevent exactly that. Point the alias fold at "
        "`ontology.kindDefs.<kind>.synonyms`/`.examples` instead."
    )


def test_the_corrected_passage_names_the_read_surface():
    section = _block(_brief_text(), _SECTION_67, "§6.7")
    missing = [literal for literal in LANDED if literal not in section]
    assert not missing, (
        "the #8639 repair made these claims in §6.7 and that passage no longer "
        "carries them — it was reverted or silently deleted rather than "
        "corrected (the §5 contradiction row carrying the same literals is not "
        "a substitute for §6.7):\n  - " + "\n  - ".join(missing)
    )


def test_the_code_still_agrees_with_the_corrected_passage():
    """Each repaired claim is checked against the code it cites.

    A revert of the underlying work reds here — which is the point: the brief
    is the artifact that re-rotates, but the code is the authority it
    re-rotates against.
    """
    registry = PACK_REGISTRY.read_text(encoding="utf-8")

    # The pattern slots are REFUSED — that is what makes §6.7's corrected
    # sentence true, and what the retracted sentence got wrong.
    refused = _block(
        registry,
        r"REFUSED_EXTRACTION_KEYS\s*=\s*\{(.*?)\n\}",
        "`REFUSED_EXTRACTION_KEYS`",
    )
    for key in ("entityPatterns", "excludePatterns"):
        assert f'"{key}"' in refused, (
            f"§6.7 now says a name-pattern declaration is refused at manifest "
            f"validation; `{key}` is no longer in REFUSED_EXTRACTION_KEYS."
        )

    # ... and the refusal is REAL: the keys are not in the declarable set.
    declarable = _block(
        registry,
        r"VALID_EXTRACTION_KEYS\s*=\s*frozenset\(\{(.*?)\}\)",
        "`VALID_EXTRACTION_KEYS`",
    )
    assert '"entityCues"' in declarable, (
        "§6.7 says `entityCues` is the declarable-but-unconsumed slot; it is no "
        "longer declarable."
    )
    for key in ("entityPatterns", "excludePatterns"):
        assert f'"{key}"' not in declarable, (
            f"§6.7 says a name-pattern declaration is refused rather than "
            f"declarable; `{key}` is back in VALID_EXTRACTION_KEYS."
        )

    # The read surface is real: `compile_kind_index_spec` READS the kindDefs
    # synonyms/examples AND PASSES them into the rendered surface.
    #
    # Assert the read + the render CALL SHAPE, never a bare substring: the
    # function's docstring already mentions both field names, so a
    # `field in body` check stays green when the render call is neutralised
    # (`_surface(k, desc, syns, exs)` -> `_surface(k, desc, [], [])`) — an
    # assertion passing for a reason it does not state (PR #8639 review,
    # non-blocking observation). The mutation is applied below.
    extractor = VALUE_EXTRACTOR.read_text(encoding="utf-8")
    body = _block(
        extractor,
        r"(def compile_kind_index_spec\(.*?)(?=\ndef |\nclass )",
        "`compile_kind_index_spec`",
    )
    for field in ("synonyms", "examples"):
        assert f'kd.get("{field}")' in body, (
            "§6.7 now names `ontology.kindDefs.<kind>."
            f"{field}` as the read home; `compile_kind_index_spec` no longer "
            "reads it from the pack's kindDefs."
        )
    render = re.compile(r"_surface\(\s*k\s*,\s*desc\s*,\s*syns\s*,\s*exs\s*\)")
    assert render.search(body), (
        "§6.7 now names the per-kind surface forms as rendered INTO the "
        "classification surface; `compile_kind_index_spec` no longer PASSES "
        "the kindDefs synonyms/examples into `_surface` — a docstring mention "
        "is not the behaviour."
    )
    # Prove the clause above is not vacuous: apply the exact mutation that
    # leaves the docstring's wording intact and confirm the check reds on it.
    neutralised = render.sub("_surface(k, desc, [], [])", body, count=1)
    assert neutralised != body, (
        "the render-call check did not match the real source — it cannot "
        "certify the render it claims to guard."
    )
    assert not render.search(neutralised), (
        "the render-call check stays green on a neutralised render "
        "(`_surface(k, desc, [], [])`), so it passes for a reason it does not "
        "state."
    )
