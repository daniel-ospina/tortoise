"""Manifest v3.1 extraction behaviour slots (#1026 §1.4/§1.5).

The slots let a pack shape extraction for its own domain while the engine stays
pack-agnostic. Two rules from the #1026 ruling are load-bearing here and are
tested as first-class behaviour rather than left implicit:

  * ``entityCues`` is DECLARATIVE — its keys must name a kind this pack or core
    already declares.
  * ``entityPatterns``/``excludePatterns`` are REFUSED — "no name patterns;
    nothing is dropped at mint". The refusal carries its REASON, so an author
    arriving from a pattern-filtering design is told why at the point of
    adoption instead of having to infer it from "unknown key".

Mutation check (each assertion below must stay true): deleting the refusal
branch in ``_validate`` turns the two ``TestUnknownAndRefusedKeys`` refusal
tests from the named reason into a bare unknown-key error; deleting the shape
check lets ``":kind"``, ``"ns:"`` and ``"a:b:c"`` pass unexamined by every
pass.

Kind refs are checked at TWO levels, and the tests are split the same way:
SHAPE in ``_validate`` (per-manifest — all it can decide alone), and
RESOLUTION in ``_validate_cross_pack_refs`` (a bare name may belong to exactly
one other loaded pack). See ``TestCrossPackResolution``.
"""
from __future__ import annotations

import os
import sys

import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tortoise.pack_registry import (  # noqa: E402
    MAX_PROMPT_FRAGMENT_CHARS,
    MAX_PROMPT_FRAGMENT_TOKENS,
    MAX_PROMPT_FRAGMENTS_TOKENS,
    PackRegistry,
    prompt_token_proxy,
)


def _manifest(**extraction):
    """A minimal VALID manifest declaring one pack-local kind.

    ``domainThing`` is deliberately not a core kind, so a test that accepts it
    proves the pack's own declaration is what validated it.
    """
    return {
        "namespace": "tst",
        "name": "Test Pack",
        "ontology": {"extends": "core", "pointKinds": ["domainThing"]},
        "extraction": extraction,
    }


def _errors(**extraction):
    return PackRegistry("/tmp/nonexistent")._validate(_manifest(**extraction))


def _extraction_errors(**extraction):
    """Only the ``extraction.*`` errors — the signal these tests are about."""
    return [e for e in _errors(**extraction) if e.startswith("extraction")]


class TestBackwardCompatibility:
    """A v3 manifest with no v3.1 keys must validate and normalize unchanged."""

    def test_v3_manifest_without_v31_keys_validates_clean(self):
        assert _extraction_errors() == []

    def test_normalize_yields_empty_v31_slots(self):
        shape = PackRegistry._normalize_extraction(None)
        assert shape["entityCues"] == {}
        assert shape["relationTemplates"] == []
        assert shape["valueGate"] == {}
        assert shape["promptFragments"] == []

    def test_normalize_never_raises_on_a_malformed_section(self):
        """Validation reports the shape error; normalization still returns a map.

        A caller reading ``extraction`` must never meet a non-dict where the
        manifest documents one, whatever the input was.
        """
        shape = PackRegistry._normalize_extraction(["not", "a", "map"])
        assert shape["entityCues"] == {}
        assert shape["valueGate"] == {}
        assert shape["active"] is True

    def test_a_string_source_types_is_not_split_into_characters(self):
        """`list("conversation")` used to become 12 one-character source kinds.

        The shape error must be reported, and normalization must not silently
        manufacture a list the manifest never expressed.
        """
        errors = _errors(sourceTypes="conversation")
        assert any("sourceTypes must be a list" in e for e in errors), errors
        assert PackRegistry._normalize_extraction(
            {"sourceTypes": "conversation"})["sourceTypes"] == []


class TestUnknownAndRefusedKeys:
    def test_unknown_key_is_an_error(self):
        """Before v3.1 the top-level `extraction` map accepted anything, so a
        singular/plural typo parsed clean and silently did nothing."""
        errors = _errors(sourceType=["conversation"])
        assert any("unknown key 'sourceType'" in e for e in errors), errors

    def test_unknown_key_error_lists_what_is_allowed(self):
        errors = _errors(sourceType=["conversation"])
        assert any("entityCues" in e and "promptFragments" in e for e in errors)

    def test_exclude_patterns_is_refused_with_its_reason(self):
        errors = _errors(excludePatterns=["^the ", "\\d+$"])
        assert any("extraction.excludePatterns" in e for e in errors), errors
        assert any("nothing is dropped at mint" in e for e in errors), errors
        # It must read as a DECISION, not as a typo.
        assert not any("unknown key 'excludePatterns'" in e for e in errors)

    def test_entity_patterns_is_refused_with_its_reason(self):
        errors = _errors(entityPatterns=["^the "])
        assert any("nothing is dropped at mint" in e for e in errors), errors
        assert not any("unknown key 'entityPatterns'" in e for e in errors)


class TestEntityCues:
    def test_a_declared_pack_kind_is_accepted(self):
        assert _extraction_errors(
            entityCues={"domainThing": ["the domain thing"]}) == []

    def test_a_core_kind_is_accepted(self):
        assert _extraction_errors(entityCues={"Object": ["a thing"]}) == []

    def test_a_malformed_namespaced_ref_is_rejected(self):
        """`":kind"`, `"ns:"` and `"a:b:c"` are not names any pack can
        declare, so NO resolver will ever report them — this shape check is the
        only thing that can. Returning True for "contains a colon" left all
        three unchecked by every pass (review of PR #5647)."""
        for bad in (":kind", "ns:", "a:b:c", "   "):
            errors = _extraction_errors(entityCues={bad: ["a cue"]})
            assert errors, f"{bad!r} was accepted"
            assert any("well-formed" in e or "non-empty" in e
                       for e in errors), (bad, errors)

    def test_cues_must_be_non_empty_strings(self):
        assert _extraction_errors(entityCues={"domainThing": [""]})
        assert _extraction_errors(entityCues={"domainThing": "a string"})
        assert _extraction_errors(
            entityCues={"domainThing": [1, 2]})

    def test_entity_cues_must_be_a_map(self):
        errors = _extraction_errors(entityCues=["domainThing"])
        assert any("entityCues must be a map" in e for e in errors), errors


class TestRelationTemplates:
    def test_a_valid_template_is_accepted(self):
        assert _extraction_errors(relationTemplates=[{
            "predicate": "addresses",
            "mechanism": "IMPL",
            "fromKind": "domainThing",
            "toKind": "Object",
            "description": "a concept implemented by an artefact",
        }]) == []

    def test_the_mechanism_vocabulary_is_the_closed_s3_set(self):
        """A template must not describe an edge the pipeline cannot build."""
        errors = _extraction_errors(relationTemplates=[{"mechanism": "SUPPORTS"}])
        assert any("mechanism must be one of" in e for e in errors), errors

    def test_an_unknown_template_key_is_rejected(self):
        errors = _extraction_errors(
            relationTemplates=[{"predicate": "x", "weights": "up"}])
        assert any("unknown key 'weights'" in e for e in errors), errors

    def test_an_undeclared_bare_kind_is_left_to_the_cross_pack_pass(self):
        """A bare name is NOT decidable from one manifest: it may belong to
        exactly one other loaded pack, and ``_validate`` cannot see them yet.
        ``TestCrossPackResolution`` asserts it is reported once they are."""
        assert _extraction_errors(entityCues={"ghost": ["boo"]}) == []

    def test_an_undeclared_template_kind_is_left_to_the_cross_pack_pass(self):
        assert _extraction_errors(
            relationTemplates=[{"fromKind": "ghost"}]) == []

    def test_relation_templates_must_be_a_list_of_maps(self):
        assert _extraction_errors(relationTemplates={"predicate": "x"})
        assert _extraction_errors(relationTemplates=["a string"])


class TestValueGate:
    def test_keep_and_drop_hints_are_accepted(self):
        assert _extraction_errors(valueGate={
            "keep": ["design rationale"],
            "drop": ["logistics"],
        }) == []

    def test_value_gate_must_be_a_map(self):
        errors = _extraction_errors(valueGate=["keep everything"])
        assert any("valueGate must be a map" in e for e in errors), errors

    def test_a_gate_value_must_be_a_list_of_strings(self):
        assert _extraction_errors(valueGate={"keep": "rationale"})
        assert _extraction_errors(valueGate={"keep": [""]})


class TestPromptFragments:
    def test_a_short_fragment_is_accepted(self):
        assert _extraction_errors(
            promptFragments=["Prefer domain nouns over file paths."]) == []

    def test_a_fragment_over_the_cap_is_rejected(self):
        long_fragment = " ".join(["word"] * (MAX_PROMPT_FRAGMENT_TOKENS + 1))
        errors = _extraction_errors(promptFragments=[long_fragment])
        assert any("cap" in e and "promptFragments[0]" in e
                   for e in errors), errors

    def test_a_total_over_the_cap_is_rejected(self):
        """Per-fragment compliance must not buy an unbounded total: N legal
        fragments would otherwise inflate every brief the pack activates."""
        per = MAX_PROMPT_FRAGMENT_TOKENS - 1
        n = (MAX_PROMPT_FRAGMENTS_TOKENS // per) + 2
        # One-character words, so each fragment stays under the CHARACTER cap
        # too and only the shared word total can fail.
        fragment = " ".join(["w"] * per)
        errors = _extraction_errors(promptFragments=[fragment] * n)
        assert any("total" in e for e in errors), errors
        assert not any(
            f"promptFragments[{i}]" in e for i in range(n) for e in errors
        ), (
            "each fragment is under the per-fragment cap — only the total "
            "should fail")

    def test_fragments_must_be_non_empty_strings(self):
        assert _extraction_errors(promptFragments=[""])
        assert _extraction_errors(promptFragments=[42])

    def test_prompt_fragments_must_be_a_list(self):
        errors = _extraction_errors(promptFragments="one fragment")
        assert any("promptFragments must be a list" in e for e in errors), errors

    def test_the_token_count_is_documented_as_a_proxy(self):
        """The cap is a guard, not a token budget — the name must say so, and
        the count must behave like words rather than characters."""
        assert prompt_token_proxy("one two three") == 3
        assert prompt_token_proxy("   ") == 0
        assert prompt_token_proxy("supercalifragilistic") == 1

    def test_a_whitespace_free_blob_cannot_evade_the_caps(self):
        """The word proxy counts 1 for a blob of ANY size, so the character
        bound is what makes the guard non-evadable by deleting the spaces."""
        blob = "x" * (MAX_PROMPT_FRAGMENT_CHARS + 1)
        errors = _extraction_errors(promptFragments=[blob])
        assert any(f"{MAX_PROMPT_FRAGMENT_CHARS + 1} chars" in e
                   for e in errors), errors


class TestValueGateKeys:
    def test_an_unknown_gate_key_is_rejected(self):
        """A mistyped gate key is silent dead config of exactly the species the
        unknown-key rule exists to catch, so this slot is as strict as its
        neighbours (review of PR #5647)."""
        errors = _extraction_errors(valueGate={"keepp": ["x"]})
        assert any("unknown key 'keepp'" in e for e in errors), errors

    def test_the_documented_keys_are_accepted(self):
        assert _extraction_errors(valueGate={"keep": ["a"], "drop": ["b"]}) == []


class TestCrossPackResolution:
    """A v3.1 kind ref is RESOLVED after load_all(), not per-manifest.

    These tests exist because the slots were previously the one place a ref was
    accepted and handed to nobody: with only a shape check, a dangling
    `ns:kind` validated clean and my own test had codified that as intended.
    """

    @staticmethod
    def _load(tmp_path, cues=None, templates=None):
        (tmp_path / "alpha").mkdir()
        (tmp_path / "alpha" / "manifest.yaml").write_text(
            "namespace: alpha\nname: Alpha\nontology:\n"
            "  extends: core\n  pointKinds: [thingA]\n")
        (tmp_path / "beta").mkdir()
        (tmp_path / "beta" / "manifest.yaml").write_text(yaml.safe_dump({
            "namespace": "beta",
            "name": "Beta",
            "ontology": {"extends": "core", "pointKinds": ["thingB"]},
            "extraction": {
                "entityCues": cues or {},
                "relationTemplates": templates or [],
            },
        }))
        registry = PackRegistry(str(tmp_path))
        registry.load_all()
        return [e for e in registry.errors.get("beta", [])
                if "extraction." in e]

    def test_a_packs_own_kind_resolves(self, tmp_path):
        assert self._load(tmp_path, cues={"thingB": ["a cue"]}) == []

    def test_a_bare_name_from_exactly_one_other_pack_resolves(self, tmp_path):
        assert self._load(tmp_path, cues={"thingA": ["a cue"]}) == []

    def test_a_dangling_bare_name_is_reported(self, tmp_path):
        errors = self._load(tmp_path, cues={"ghost": ["a cue"]})
        assert any("does not resolve" in e for e in errors), errors

    def test_a_dangling_namespaced_ref_is_reported(self, tmp_path):
        errors = self._load(tmp_path, cues={"ghost:thing": ["a cue"]})
        assert any("does not resolve" in e for e in errors), errors

    def test_a_dangling_template_kind_is_reported(self, tmp_path):
        errors = self._load(tmp_path, templates=[
            {"predicate": "x", "mechanism": "IMPL",
             "fromKind": "ghost:thing"}])
        assert any("relationTemplates[0].fromKind" in e
                   and "does not resolve" in e for e in errors), errors


class TestShapeIsPinned:
    def test_dataclass_default_matches_normalize(self):
        """Two definitions of "always present with defaults" must not drift: a
        consumer indexing a slot would KeyError on whichever path omits it."""
        import dataclasses

        from tortoise.pack_registry import PackManifest

        field = next(f for f in dataclasses.fields(PackManifest)
                     if f.name == "extraction")
        assert field.default_factory() == PackRegistry._normalize_extraction(None)
