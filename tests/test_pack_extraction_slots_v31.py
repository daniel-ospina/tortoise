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
branch in ``_validate`` turns the two ``RefusedSlots`` tests from the named
reason into a bare unknown-key error; deleting the declared-kind check lets an
``entityCues`` key name a kind the pack does not own.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tortoise.pack_registry import (  # noqa: E402
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

    def test_a_namespaced_ref_is_left_to_the_cross_pack_pass(self):
        """`ns:kind` is resolved by `_validate_cross_pack_refs`, which owns the
        "declared by exactly ONE other pack" rule — this check must not
        pre-empt it by rejecting the prefix."""
        assert _extraction_errors(
            entityCues={"other:thing": ["a thing"]}) == []

    def test_an_undeclared_kind_is_rejected(self):
        errors = _extraction_errors(entityCues={"ghost": ["boo"]})
        assert any("'ghost' is not a declared kind" in e for e in errors), errors

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

    def test_an_undeclared_from_kind_is_rejected(self):
        errors = _extraction_errors(relationTemplates=[{"fromKind": "ghost"}])
        assert any("fromKind" in e and "not a declared kind" in e
                   for e in errors), errors

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
        fragment = " ".join(["word"] * per)
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
