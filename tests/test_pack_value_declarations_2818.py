"""#2818 — pack-declared per-kind VALUE FIELDS (`kindDefs.<kind>.values`).

The defect this pins: `kindDefs` used to accept exactly the seven keys in
`VALID_KINDDEF_KEYS` and hard-reject everything else, so a pack could not say
*"a `fundingTranche` carries `committedAmountMinor` as a money value"* — the
seam #2782's value model needs. The declaration surface added here is
DELIBERATELY closed and validated (indicator 2: a *declared* surface, not an
open props bag), reaches the extraction prompt, and is kept OUT of the
classification index text so the npz cache key never rotates.

Every assertion in this file is an owner-visible indicator from the issue:

* ``values`` validates, with the documented schema (name / valueKind /
  required? / cardinality?).
* the closed vocabulary SURVIVES — an unknown key is still a hard error, both
  at the `kindDefs` level and inside an entry; there is no props bag.
* all shipped starter packs validate UNCHANGED (no pack declares a value
  field yet, so their briefs and prompts are byte-identical).
* a malformed declaration produces a PRECISE error — never an opaque
  dropped pack or a ``TypeError``.
* the declaration reaches the prompt: ``compile_value_brief`` carries it,
  ``_render_master*`` renders it, and the S2/S4 rule slot states the policy.
* ``compile_kind_index_spec`` (and therefore ``kind_index.cache_key_for``) is
  BYTE-IDENTICAL with and without the declaration present.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from tortoise import extractor_v2 as v2
from tortoise.kind_index import cache_key_for
from tortoise.pack_registry import PackRegistry, default_packs_dir
from tortoise.pack_values import (
    VALUE_CARDINALITIES,
    VALUE_FIELD_KEYS,
    VALUE_KINDS,
    declared_value_fields,
)
from tortoise.value_extractor import compile_kind_index_spec, compile_value_brief

KIND = "fundingTranche"
NS = "acme"


# ── fixtures ───────────────────────────────────────────────────────────────


def _values() -> list[dict]:
    """The canonical declaration #2782 needs, in the documented schema."""
    return [
        {"name": "committedAmountMinor", "valueKind": "money",
         "required": True},
        {"name": "trancheLabel", "valueKind": "quantity",
         "cardinality": "many"},
    ]


def _raw(values: object = None, *, extra_kind_keys: dict | None = None) -> dict:
    """A minimal valid pack manifest, optionally declaring per-kind values."""
    spec: dict = {
        "description": "A tranche of a financing round",
        "nearMisses": [],
    }
    if values is not None:
        spec["values"] = values
    if extra_kind_keys:
        spec.update(extra_kind_keys)
    return {
        "namespace": NS,
        "name": "Acme",
        "version": "0.1.0",
        "tier": "free",
        "ontology": {
            "extends": "core",
            "objectKinds": [KIND],
            "kindDefs": {KIND: spec},
        },
    }


def _validate(raw: dict) -> list[str]:
    """Validate a manifest dict through the ONE validator (no files)."""
    registry = PackRegistry("/tmp/nonexistent")
    return registry._validate(raw)


def _write_pack(packs_dir: Path, manifest: dict) -> Path:
    pack_dir = packs_dir / manifest["namespace"]
    pack_dir.mkdir(parents=True)
    (pack_dir / "manifest.yaml").write_text(yaml.safe_dump(manifest))
    return pack_dir


# ── indicator 1: the declaration validates ─────────────────────────────────


class TestDeclarationValidates:
    def test_values_with_the_documented_schema_validates(self):
        errors = _validate(_raw(_values()))
        assert errors == [], errors

    def test_defaults_are_optional_required_false_and_cardinality_one(self):
        errors = _validate(_raw([{"name": "committedAmountMinor",
                                  "valueKind": "money"}]))
        assert errors == [], errors

    def test_every_value_kind_is_accepted(self):
        for value_kind in sorted(VALUE_KINDS):
            errors = _validate(_raw([{"name": "someField",
                                      "valueKind": value_kind}]))
            assert errors == [], (value_kind, errors)

    def test_every_cardinality_is_accepted(self):
        for cardinality in sorted(VALUE_CARDINALITIES):
            errors = _validate(_raw([{"name": "someField",
                                      "valueKind": "money",
                                      "cardinality": cardinality}]))
            assert errors == [], (cardinality, errors)

    def test_all_four_entry_keys_are_the_closed_set(self):
        # The schema is name/valueKind/required/cardinality — nothing else.
        assert frozenset(
            {"name", "valueKind", "required", "cardinality"}) == \
            VALUE_FIELD_KEYS


# ── indicator 2: the closed vocabulary SURVIVES ────────────────────────────


class TestClosedVocabularySurvives:
    def test_unknown_kinddef_key_still_errors(self):
        # The pin that stops `values` becoming the thin end of an open props
        # bag: an unrelated key is still refused, with the allowed-key list.
        errors = _validate(_raw(None, extra_kind_keys={"color": "blue"}))
        assert any("unknown key 'color'" in e for e in errors), errors
        assert any("allowed: " in e and "values" in e for e in errors), errors

    def test_a_plausible_synonym_spelling_is_not_accepted(self):
        # `valueFields` / `props` are NOT aliases — one declared key, one
        # spelling. A pack author who guessed gets the closed list.
        for wrong in ("valueFields", "value_fields", "props", "properties"):
            errors = _validate(_raw(None, extra_kind_keys={wrong: []}))
            assert any(f"unknown key '{wrong}'" in e for e in errors), (
                wrong, errors)

    def test_unknown_entry_key_errors(self):
        errors = _validate(_raw([{"name": "amountMinor",
                                  "valueKind": "money",
                                  "valueType": "integer"}]))
        assert any("values[0]" in e and "valueType" in e for e in errors), errors
        assert any("allowed: cardinality, name, required, valueKind" in e
                   for e in errors), errors

    def test_unknown_value_kind_errors(self):
        errors = _validate(_raw([{"name": "amountMinor",
                                  "valueKind": "currency"}]))
        assert any("unknown valueKind 'currency'" in e for e in errors), errors
        assert any("money" in e and "quantity" in e for e in errors), errors


# ── indicator 3: shipped packs validate UNCHANGED ──────────────────────────


class TestShippedPacksUnchanged:
    def test_all_shipped_packs_validate_with_no_errors(self):
        registry = PackRegistry(default_packs_dir())
        registry.load_all()
        assert not registry.errors, registry.errors
        # The starter catalog, not just "some packs": the five original
        # domains plus venture (#2725) is the shipped set at this HEAD.
        assert {"dev", "product-strategy", "marketing", "pm", "agent-ops",
                "venture"} <= set(registry.packs)

    def test_no_shipped_pack_declares_a_value_field_yet(self):
        # Which is WHY their brief / master / prompts stay byte-identical.
        brief = compile_value_brief(default_packs_dir())
        declared = {k for k, entry in brief.items()
                    if isinstance(entry, dict) and entry.get("values")}
        assert declared == set(), declared

    def test_the_catalog_master_renders_no_values_segment(self):
        master = v2._build_master_from_brief(
            compile_value_brief(default_packs_dir()))
        assert master["pack_values"] == {}
        for rendered in (v2._render_master_verbose(master),
                         v2._render_master_compact(master, None)):
            assert "[values:" not in rendered
        assert "KIND VALUE FIELDS" not in v2._s2s4_rules(master)


# ── indicator 4: malformed declarations give PRECISE errors ────────────────


class TestMalformedDeclarationIsPrecise:
    @pytest.mark.parametrize("values, expected", [
        ("committedAmountMinor", "'values' must be a list"),
        ({"name": "amountMinor"}, "'values' must be a list"),
        ([["amountMinor"]], "values[0] must be a map"),
        ([{"valueKind": "money"}], "'name' is required"),
        ([{"name": "", "valueKind": "money"}], "'name' must be a non-empty"),
        ([{"name": 3, "valueKind": "money"}], "'name' must be a non-empty"),
        ([{"name": "a b", "valueKind": "money"}],
         "must be a bare property identifier"),
        ([{"name": " amountMinor ", "valueKind": "money"}],
         "must be a bare property identifier"),
        ([{"name": "a, b", "valueKind": "money"}],
         "must be a bare property identifier"),
        ([{"name": "a\nb", "valueKind": "money"}],
         "must be a bare property identifier"),
        ([{"name": "1amount", "valueKind": "money"}],
         "must be a bare property identifier"),
        ([{"name": "amountMinor"}], "'valueKind' is required"),
        ([{"name": "amountMinor", "valueKind": "money",
           "required": "yes"}], "'required' must be a boolean"),
        ([{"name": "amountMinor", "valueKind": "money",
           "cardinality": "two"}], "'cardinality' must be one of"),
        ([{"name": "a", "valueKind": "money"},
          {"name": "a", "valueKind": "percent"}],
         "duplicate value field 'a'"),
    ])
    def test_error_message_names_the_offending_entry(self, values, expected):
        errors = _validate(_raw(values))
        assert any(expected in e for e in errors), (expected, errors)
        # Every message is scoped to the kind and, for an entry-level problem,
        # to the entry index.
        for error in errors:
            assert error.startswith(f"kindDefs '{KIND}': "), error
        if isinstance(values, list) and values and isinstance(values[0], dict):
            assert all("values[" in e for e in errors), errors

    def test_a_namespaced_name_is_refused(self):
        # OD2: property names are BARE — the pack namespaces KINDS only.
        errors = _validate(_raw([{"name": f"{NS}:committedAmountMinor",
                                  "valueKind": "money"}]))
        assert any("must be BARE" in e and "OD2" in e for e in errors), errors

    def test_a_malformed_pack_is_ISOLATED_not_a_typeerror(self, tmp_path):
        # The acceptance bar: a bad declaration isolates the pack (a reported
        # error), never an opaque drop or a raise on the way to the prompt.
        _write_pack(tmp_path, _raw([{"name": "", "valueKind": "money"}]))
        good = _raw(None)
        good["namespace"] = "good"
        _write_pack(tmp_path, good)
        registry = PackRegistry(tmp_path)
        registry.load_all()  # must not raise
        assert "acme" not in registry.packs
        flattened = "\n".join(
            msg for msgs in registry.errors.values() for msg in msgs)
        assert "'name' must be a non-empty" in flattened, registry.errors
        assert "good" in registry.packs

    def test_the_compiler_degrades_a_malformed_section_not_raises(self):
        # compile_value_brief reads RAW yaml and must never raise into the
        # prompt path — a malformed declaration reads as absent (the validator
        # is what reports it).
        assert declared_value_fields({"values": "nope"}) == []
        assert declared_value_fields({"values": [None, {"name": "x"}]}) == []
        assert declared_value_fields({"values": [
            {"name": "a", "valueKind": "not-a-kind"}]}) == []
        assert declared_value_fields("not a dict") == []

    def test_the_compiler_drops_a_non_bare_name(self):
        # The field name is rendered RAW into the prompt, so a colon, a comma,
        # a newline or a bracket is dropped by the compiler just as the
        # validator refuses it (the compiler reads raw YAML, including a
        # tenant manifest that may never have passed the validator).
        for bad in (f"{NS}:committedAmountMinor", "a, b", "a\nb", "a(b",
                    "a b", "1amount", " amountMinor ", "amountMinor\t"):
            assert declared_value_fields({"values": [
                {"name": bad, "valueKind": "money"}]}) == [], bad
        assert declared_value_fields({"values": [
            {"name": "committedAmountMinor",
             "valueKind": "money"}]})

    @pytest.mark.parametrize("field, bad", [
        ("valueKind", ["money"]),
        ("valueKind", {"kind": "money"}),
        ("cardinality", ["one"]),
        ("cardinality", {"n": 1}),
    ])
    def test_an_unhashable_yaml_value_is_a_PRECISE_error_not_a_TypeError(
            self, field, bad):
        # `x not in frozenset` RAISES TypeError for a list/dict value; the
        # validator's contract is to RETURN its error list, so a malformed
        # YAML value must not surface as an opaque dropped pack.
        errors = _validate(_raw([{"name": "amountMinor",
                                  "valueKind": "money", field: bad}]))
        assert errors, "expected an error, got a clean validation"
        assert any(field in e for e in errors), errors
        assert all("TypeError" not in e for e in errors), errors

    def test_the_compiler_resolves_defaults_and_keeps_order(self):
        got = declared_value_fields({"values": [
            {"name": "b", "valueKind": "percent", "cardinality": "many"},
            {"name": "a", "valueKind": "money", "required": True},
        ]})
        assert got == [
            {"name": "b", "valueKind": "percent", "required": False,
             "cardinality": "many"},
            {"name": "a", "valueKind": "money", "required": True,
             "cardinality": "one"},
        ]


# ── indicator 5: the declaration REACHES THE PROMPT ────────────────────────


class TestDeclarationReachesThePrompt:
    def _brief_and_master(self, tmp_path: Path) -> tuple[dict, dict]:
        _write_pack(tmp_path, _raw(_values()))
        brief = compile_value_brief(tmp_path)
        return brief, v2._build_master_from_brief(brief)

    def test_the_brief_carries_the_declaration(self, tmp_path):
        brief, _ = self._brief_and_master(tmp_path)
        assert brief[f"{NS}:{KIND}"]["values"] == [
            {"name": "committedAmountMinor", "valueKind": "money",
             "required": True, "cardinality": "one"},
            {"name": "trancheLabel", "valueKind": "quantity",
             "required": False, "cardinality": "many"},
        ]

    def test_the_master_carries_it_in_its_own_section(self, tmp_path):
        _, master = self._brief_and_master(tmp_path)
        # A SEPARATE section: `pack_kinds` values stay description strings for
        # every existing consumer (master_kind_forms, the write gates).
        assert isinstance(master["pack_kinds"][f"{NS}:{KIND}"], str)
        assert master["pack_values"][f"{NS}:{KIND}"][0]["name"] == \
            "committedAmountMinor"

    def test_verbose_render_prints_the_values_segment(self, tmp_path):
        _, master = self._brief_and_master(tmp_path)
        rendered = v2._render_master_verbose(master)
        assert (
            f"- {NS}:{KIND} — A tranche of a financing round "
            "[values: committedAmountMinor(money, required), "
            "trancheLabel(quantity, many)]"
        ) in rendered

    def test_compact_render_prints_the_values_segment(self, tmp_path):
        _, master = self._brief_and_master(tmp_path)
        rendered = v2._render_master_compact(master, None)
        assert "committedAmountMinor(money, required)" in rendered
        assert "trancheLabel(quantity, many)" in rendered

    def test_the_dispatcher_reaches_both_render_modes(self, tmp_path,
                                                      monkeypatch):
        _, master = self._brief_and_master(tmp_path)
        assert "committedAmountMinor(money, required)" in v2._render_master(
            master, core_only=False)
        monkeypatch.setenv("TORTOISE_EXTRACTOR_PROMPT", "compact")
        assert "committedAmountMinor(money, required)" in \
            v2._render_master(master, "a tranche story", core_only=False)

    def test_render_survives_a_non_dict_pack_values_section(self, tmp_path):
        # #9003 review (P3): the render path must never raise on a malformed
        # master. `_pack_values_text` guards with isinstance; `_values_suffixes`
        # now mirrors it, so a hand-built (or JSON-derived) master whose section
        # is a list/str yields no segment instead of AttributeError-ing inside a
        # prompt render.
        _, master = self._brief_and_master(tmp_path)
        baseline = v2._render_master_verbose(
            {k: v for k, v in master.items() if k != "pack_values"})
        for bad in (["x"], "x", 7, [{"name": "a"}], {"acme:t": 7}):
            broken = dict(master, pack_values=bad)
            assert v2._values_suffixes(broken) == {}
            assert v2._render_master_verbose(broken) == baseline
            assert "KIND VALUE FIELDS" not in v2._s2s4_rules(broken)

    def test_the_rule_slot_states_the_policy(self, tmp_path):
        _, master = self._brief_and_master(tmp_path)
        rules = v2._s2s4_rules(master)
        assert "KIND VALUE FIELDS" in rules
        # POLICY only: WHICH fields exist is rendered once, per kind, in the
        # PACK KINDS list. Enumerating them here too would be a second source
        # for one vocabulary — and in compact mode it named kinds the story
        # selection had dropped from PACK KINDS.
        assert "committedAmountMinor" not in rules
        # The closed-list instruction is the point of the rule, not decoration.
        assert "The list is CLOSED" in rules
        assert "[values:" in rules
        # The output contract has no dedicated value-property slot, so the rule
        # must say WHERE a declared value goes instead of leaving the model to
        # invent a JSON key the write path would drop.
        assert "`content`" in rules

    def test_the_rule_block_names_no_kind(self, tmp_path):
        # The rule slot is POLICY, never vocabulary: WHICH fields exist is
        # rendered once, per kind, in PACK KINDS. Naming a kind here would be a
        # second source for one vocabulary — and in compact mode it would name
        # kinds the story selection had dropped. Assert it for EVERY pack kind
        # (a name-absence check on one story would be vacuous: `_select_pack_
        # kinds` fails OPEN for a namespace with no `_PACK_TRIGGERS` entry).
        _, master = self._brief_and_master(tmp_path)
        assert master["pack_kinds"], "fixture must carry a pack kind"
        rules = v2._s2s4_rules(master)
        for kind in master["pack_kinds"]:
            assert kind not in rules, kind

    def test_the_tenant_overlay_carries_the_declaration(self):
        # The hosted path compiles the SAME loop, so a tenant pack's value
        # fields reach ITS graph's prompt (unlike statuses/folds, which are
        # catalog-only and are refused on the tenant route).
        tenant_yaml = yaml.safe_dump(_raw(_values()))
        brief = compile_value_brief(
            default_packs_dir(), tenant_manifests={NS: tenant_yaml})
        assert brief[f"{NS}:{KIND}"]["values"][0]["name"] == \
            "committedAmountMinor"

    def test_classify_later_suppresses_the_declaration(self, tmp_path):
        # #1695 core-only mode takes the pack vocabulary OUT of the S2/S4
        # prompt; the value-field POLICY block must leave with it. Pin the
        # BLOCK ITSELF (not a field name — the block deliberately names no
        # field, so a name-absence assertion here would pass even if the
        # render_s2/s4 plumbing regressed), and pin the positive control so a
        # permanently-absent block cannot pass this either.
        _, master = self._brief_and_master(tmp_path)
        empty_s4 = ({"results": []}, {"items": []})
        assert "KIND VALUE FIELDS" not in v2._s2s4_rules(master,
                                                        core_only=True)
        assert "KIND VALUE FIELDS" not in v2.render_s2_prompt(
            master, core_only=True)
        assert "KIND VALUE FIELDS" not in v2.render_s4_prompt(
            "a tranche story", *empty_s4, master=master, core_only=True)
        # …and the SAME master DOES carry it when core-only is off, which is
        # what makes the absence above a suppression and not a plumbing gap.
        assert "KIND VALUE FIELDS" in v2._s2s4_rules(master)
        assert "KIND VALUE FIELDS" in v2.render_s2_prompt(
            master, core_only=False)
        assert "KIND VALUE FIELDS" in v2.render_s4_prompt(
            "a tranche story", *empty_s4, master=master, core_only=False)

    def test_a_props_less_kind_renders_exactly_as_before(self, tmp_path):
        # Byte-identity for the no-op case, in all three render entry points.
        _write_pack(tmp_path, _raw(None))
        master = v2._build_master_from_brief(compile_value_brief(tmp_path))
        assert master["pack_values"] == {}
        verbose = v2._render_master_verbose(master)
        assert f"- {NS}:{KIND} — A tranche of a financing round" in verbose
        assert "[values:" not in verbose
        assert "[values:" not in v2._render_master_compact(master, None)
        assert v2._s2s4_rules(master) == v2._s2s4_rules(None)


# ── indicator 6: the classification index is BYTE-IDENTICAL ────────────────


class TestIndexSpecByteIdentity:
    def _spec(self, tmp_path: Path, values: object) -> dict:
        _write_pack(tmp_path, _raw(values))
        return compile_kind_index_spec(tmp_path)

    def _raw_without_description(self, values: object) -> dict:
        raw = _raw(values)
        raw["ontology"]["kindDefs"][KIND].pop("description")
        return raw

    def test_spec_is_byte_identical_with_and_without_the_declaration(
            self, tmp_path):
        with_values = self._spec(tmp_path / "with", _values())
        without = self._spec(tmp_path / "without", None)
        assert json.dumps(with_values, sort_keys=True) == \
            json.dumps(without, sort_keys=True)
        # …and therefore the npz cache key does not rotate on install.
        assert cache_key_for(with_values) == cache_key_for(without)

    def test_spec_is_byte_identical_for_a_description_less_kind(
            self, tmp_path):
        # `description` is OPTIONAL, and the index text's fallback used to
        # stringify the whole brief ENTRY — which would carry the new `values`
        # key into the classification text and rotate the npz cache key. The
        # regression this pins is the one the general byte-identity case above
        # cannot see, because its manifest always sets a description.
        def _build(sub: Path, values: object) -> dict:
            _write_pack(sub, self._raw_without_description(values))
            return compile_kind_index_spec(sub)

        with_values = _build(tmp_path / "with", _values())
        without = _build(tmp_path / "without", None)
        assert json.dumps(with_values, sort_keys=True) == \
            json.dumps(without, sort_keys=True)
        assert cache_key_for(with_values) == cache_key_for(without)
        assert "committedAmountMinor" not in json.dumps(with_values)

    def test_values_never_leak_into_the_index_text(self, tmp_path):
        spec = self._spec(tmp_path, _values())
        entry = spec[f"{NS}:{KIND}"]
        blob = json.dumps(entry, sort_keys=True)
        assert "committedAmountMinor" not in blob
        assert "valueKind" not in blob and "values" not in blob
