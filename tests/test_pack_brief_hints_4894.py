"""#4894 / #1026 — the pack extraction value-brief slots get their CONSUMER.

**The defect this file pins.** `extraction.valueGate` and
`extraction.promptFragments` are manifest v3.1 slots: `pack_registry` shape-
validates them and `pack_manifest_store` round-trips them, and **nothing read
them** — a grep for either name outside `pack_registry.py` and the tests
returned zero results. The template manifest said so in words (*"no consumer
reads the v3.1 slots yet — they validate and normalize, nothing more"*), which
is the #5064 root exactly: **a declaration read like a shipped mechanism.**

**The fix, and why this is the seam.** `compile_value_brief` is the ONE
documented path a pack's domain takes to the extractor (*"the only seam through
which a pack's domain reaches the extractor"*), so the slots ride the brief
under the reserved `pack_hints` section, and the S1/S2/S4 value-brief slot
renders them (`_granularity_text` for S1, `_s2s4_rules` for S2/S4 — the same
slot the prose `memory_granularity` declaration uses, because the structured
hints are that declaration's other form).

**What must NOT change (the byte-identity half).** No shipped pack declares
either slot, so the default brief, the default master and every default prompt
render exactly as they did before: `pack_hints` is `{}` and the renderers
return the pre-#4894 block. A regression here would change production
extraction for every tenant.

**What must NOT happen (the #1026 ruling).** A hint is an UTTERANCE-level
judgement, *"never a mint-time drop rule: no hint can remove an artefact"*. So
a `drop:` list is prompt text and may not become a predicate: this file pins
that the two mechanical gate modules (`value_gate`, `vet_gate`) do not read the
section at all, and that the reservation is real — a reserved section key must
never be readable as an objectKind, and a pack may not be NAMED after one.

Hermetic: a temp packs directory, no database, no network.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise import extractor_v2 as v2
from tortoise import value_extractor as ve

ROOT = Path(__file__).resolve().parent.parent

#: A pack declaring BOTH value-brief slots. Minimal but real: a kindDef so the
#: kind compile has something to carry, a memory_granularity so the prose
#: declaration and the structured one sit side by side (the point of the
#: structured slot), and both extraction slots populated.
HINTED_MANIFEST = """namespace: hinted
name: Hinted
version: 0.1.0
tier: free
ontology:
  extends: core
  objectKinds:
  - finding
  kindDefs:
    finding:
      description: A recorded finding
  memory_granularity: 'Durable: findings and their reasons. Ephemeral: sprint mechanics.'
extraction:
  valueGate:
    keep:
    - design rationale
    - a trade-off stated with its reason
    drop:
    - logistics
    - tool mechanics
  promptFragments:
  - Prefer domain nouns over file paths when naming an entity.
"""

#: The SAME pack with neither slot — the control for the byte-identity pin.
BARE_MANIFEST = """namespace: hinted
name: Hinted
version: 0.1.0
tier: free
ontology:
  extends: core
  objectKinds:
  - finding
  kindDefs:
    finding:
      description: A recorded finding
  memory_granularity: 'Durable: findings and their reasons. Ephemeral: sprint mechanics.'
"""


def _packs_dir(tmp_path: Path, manifest: str) -> Path:
    packs_dir = tmp_path / "packs"
    (packs_dir / "hinted").mkdir(parents=True)
    (packs_dir / "hinted" / "manifest.yaml").write_text(manifest)
    return packs_dir


# ── the brief carries them ─────────────────────────────────────────────────


def test_the_brief_carries_the_declared_slots(tmp_path):
    brief = ve.compile_value_brief(packs_dir=_packs_dir(tmp_path, HINTED_MANIFEST))
    assert brief["pack_hints"]["hinted"] == {
        "keep": ["design rationale", "a trade-off stated with its reason"],
        "drop": ["logistics", "tool mechanics"],
        "fragments": ["Prefer domain nouns over file paths when naming an entity."],
    }


def test_a_pack_declaring_neither_slot_contributes_nothing(tmp_path):
    """The key is only ever POPULATED by a pack that asked for something."""
    brief = ve.compile_value_brief(packs_dir=_packs_dir(tmp_path, BARE_MANIFEST))
    assert brief["pack_hints"] == {}


def test_the_hints_ride_the_same_namespace_gate_as_the_kinds(tmp_path):
    """A hint is pack-authored domain data: an uninstalled pack's hints must no
    more reach the prompt than its kinds may. Same gate, same loop."""
    packs_dir = _packs_dir(tmp_path, HINTED_MANIFEST)
    gated_in = ve.compile_value_brief(
        packs_dir=packs_dir, installed_namespaces={"hinted"})
    gated_out = ve.compile_value_brief(
        packs_dir=packs_dir, installed_namespaces={"someone-else"})
    assert "hinted" in gated_in["pack_hints"]
    assert gated_out["pack_hints"] == {}
    assert "hinted:finding" not in gated_out


def test_one_malformed_slot_degrades_itself_and_nothing_else(tmp_path):
    """The manifest-load contract: a non-dict/non-list section is ABSENT, not a
    crash. `pack_registry` rejects these shapes at load; the brief must not
    depend on that having run."""
    assert ve._extraction_hints({"valueGate": "keep everything"}) == {}
    assert ve._extraction_hints({"promptFragments": {"a": 1}}) == {}
    assert ve._extraction_hints(None) == {}
    # A well-formed sibling survives a malformed one.
    assert ve._extraction_hints(
        {"valueGate": ["nope"], "promptFragments": ["keep this"]}
    ) == {"fragments": ["keep this"]}


# ── the prompts carry them ─────────────────────────────────────────────────


def test_the_mapper_value_brief_renders_the_hints(tmp_path):
    brief = ve.compile_value_brief(packs_dir=_packs_dir(tmp_path, HINTED_MANIFEST))
    master = v2._build_master_from_brief(brief)

    assert master["pack_hints"]["hinted"]["keep"]
    block = v2._s2s4_rules(master)
    assert "PACK VALUE HINTS" in block
    assert "design rationale" in block
    assert "logistics" in block
    assert "Prefer domain nouns over file paths" in block
    # The prose declaration still rides the same slot (unchanged behaviour).
    assert "ANTI-ROUTINE EXCLUSION" in block


def test_both_mapping_stages_and_s1_render_the_hints(tmp_path):
    """Rendered where the VALUE POLICY is written: S1's granularity slot and
    the S2/S4 {anti_routine} slot — not the master vocabulary render."""
    brief = ve.compile_value_brief(packs_dir=_packs_dir(tmp_path, HINTED_MANIFEST))
    master = v2._build_master_from_brief(brief)

    assert "design rationale" in v2._granularity_text(master)
    assert "design rationale" not in v2._render_master(master, None)

    s2 = v2.render_s2_prompt(master, story="a session about a design trade-off")
    assert "PACK VALUE HINTS" in s2
    assert "Prefer domain nouns over file paths" in s2

    s4 = v2.render_s4_prompt(
        "a session about a design trade-off", {}, {}, master=master)
    assert "PACK VALUE HINTS" in s4
    assert "Prefer domain nouns over file paths" in s4


def test_the_hint_block_is_ordered_by_namespace(tmp_path):
    """Sorted, so the readdir-dependent pack glob cannot reorder the prompt."""
    master = v2._build_master_from_brief(
        {"pack_hints": {"zzz": {"keep": ["z"]}, "aaa": {"keep": ["a"]}}})
    text = v2._pack_hints_text(master)
    assert text.index("aaa") < text.index("zzz")


# ── byte identity on every shipped-pack path ───────────────────────────────


def test_the_default_prompt_path_is_byte_identical():
    """No shipped pack declares either slot, so nothing may move.

    This is the half that matters in production: a change here would alter
    extraction for every tenant that never used the slots.
    """
    master = v2.build_master_list()
    assert master["pack_hints"] == {}
    assert v2._pack_hints_text(master) == ""
    assert v2._s2s4_rules(master) == v2._s2s4_rules()
    assert v2._granularity_text(master) == v2._granularity_text(None)

    brief = ve.compile_value_brief()
    assert brief["pack_hints"] == {}
    assert v2._s2s4_rules(v2._build_master_from_brief(brief)) == v2._s2s4_rules()


def test_a_reserved_section_is_never_a_pack_kind():
    """`pack_kinds` is derived from the brief's non-reserved keys (the #5165
    property); a reserved section must not appear as a namespace."""
    pack_kinds = v2.build_master_list()["pack_kinds"]
    assert ve.RESERVED_BRIEF_KEYS.isdisjoint(pack_kinds)


# ── the reservation (#1026: a hint never removes anything) ─────────────────


def test_reserved_brief_keys_are_not_object_kinds():
    """A reserved section names the vocabulary; it is not IN it.

    ⚠️ Before this, `_object_kind_vocab` added EVERY brief key as an
    objectKind form — so `memory_granularity` was a writable objectKind, and
    the new section would have been one too. An allow-list admitting the
    vocabulary that describes it is an allow-list that cannot fail closed.
    """
    vocab = ve._object_kind_vocab()
    assert ve.RESERVED_BRIEF_KEYS.isdisjoint(vocab), (
        f"a reserved brief section is accepted as an objectKind: "
        f"{sorted(ve.RESERVED_BRIEF_KEYS & vocab)}"
    )
    # The reservation must not have narrowed anything real.
    assert "core:plan" in vocab and "dev:code" in vocab


def test_a_reserved_section_is_not_an_accepted_summary_kind():
    """The enforcer half: the reservation has to hold on the S1 summary path
    too, or a model could emit the section name as a kind and pass."""
    from tortoise.value_extractor import validate_summary

    summary = {"state": [{"name": "x", "objectKind": "pack_hints"}]}
    errors = validate_summary(summary)
    assert any("not in the closed vocabulary" in e for e in errors), errors


def test_a_pack_may_not_be_named_after_a_reserved_section():
    """The tenant-name half. A namespace IS a brief key prefix and (for the
    reserved sections) collides with the section key itself."""
    from tortoise.pack_manifest_store import validate_manifest

    for ns in sorted(ve.RESERVED_BRIEF_KEYS):
        r = validate_manifest(
            f"namespace: {ns}\nname: X\nversion: 0.1.0\ntier: free\n")
        assert not r.ok, f"{ns} was accepted as a tenant namespace"
        assert "reserved namespace" in r.errors[0], r.errors


def test_the_gate_modules_never_read_the_hint_section():
    """#1026's ruling, pinned as source: a hint JUDGES an utterance and may
    never remove an artefact. If a mechanical gate ever read `pack_hints`,
    a pack's prose `drop:` list would have become a mint-time drop rule —
    the exact design the #1026 ruling refuses.
    """
    for rel in ("tortoise/value_gate.py", "tortoise/vet_gate.py"):
        src = (ROOT / rel).read_text(encoding="utf-8")
        assert "pack_hints" not in src, (
            f"{rel} reads the pack hint section — hints must stay utterance "
            f"judgements, never mint-time predicates"
        )


if __name__ == "__main__":  # pragma: no cover - manual aid
    raise SystemExit(pytest.main([__file__, "-q"]))
