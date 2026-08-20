"""Canonicalizer unit tests — batch_id deterministic derivation (epic #902 A4).

Pins §4.2's canonical serialization invariances (a)-(f) + the cycle-11 (h) tie
shape + the cycle-4 ref-expansion pin (ref-KEY rename → identical batch_id) +
the A4 brief's crash-retry pin (derivation independent of assigned ids) +
CYCLE-21 clock-independence. E2E-5.6 / E2E-6.6 assert the same invariances
end-to-end; these unit tests pin them at the derivation boundary.

Runnable with: .venv/bin/python -m pytest tests/test_canonical.py -v
"""
from __future__ import annotations

import copy
import json
import os
import sys
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: I001

from tortoise.canonical import (
    canonical_bundle,  # noqa: F401
    canonical_json,
    canonicalize,
    derive_batch_id,
    resolve_refs,
)

# SDK's own ULID-shaped regex (sdk.py) — batch_id must satisfy it.
import re
_CROCKFORD_ULID_RE = re.compile(r"^[0-7][0-9A-HJKMNP-TV-Z]{25}$", re.IGNORECASE)


def _bundle():
    """Canonical fixture bundle exercising all four sections + refs."""
    return {
        "points": [
            {"ref": "p1", "kind": "claim",
             "content": "Rust is memory-safe by default."},
            {"ref": "p2", "kind": "claim",
             "content": "Rust's borrow checker prevents use-after-free."},
        ],
        "entities": [
            {"ref": "s1", "type": "subject", "name": "Ferra Labs",
             "subjectKind": "organization"},
        ],
        "sources": [
            {"ref": "src1", "url": "https://example.com/rust-report",
             "sourceKind": "report", "tier": "T1"},
        ],
        "connections": [
            {"ref": "c1", "from": "p1", "to": "p2", "operator": "IMPL",
             "direction": "unidirectional", "confidence": 0.3},
            {"ref": "c2", "from": "s1", "to": "p1", "relation": "authoredBy"},
            {"ref": "c3", "from": "p1", "to": "src1",
             "relation": "extractedFrom"},
        ],
    }


def _shuffle_keys(value):
    """Recursively reverse dict key order at every level (variant (a))."""
    if isinstance(value, dict):
        return {_shuffle_keys(k): _shuffle_keys(v)
                for k, v in reversed(list(value.items()))}
    if isinstance(value, list):
        return [_shuffle_keys(v) for v in value]
    return value


def _rename_refs(bundle, mapping):
    """Rename every ref KEY (and every value addressing one) (variant (g))."""
    def _rn(x):
        if isinstance(x, dict):
            return {_rn(k): _rn(v) for k, v in x.items()}
        if isinstance(x, list):
            return [_rn(v) for v in x]
        if isinstance(x, str) and x in mapping:
            return mapping[x]
        return x
    return _rn(copy.deepcopy(bundle))


class TestBatchIdShape:
    def test_ulid_shaped_26_char_crockford(self):
        bid = derive_batch_id(_bundle())
        assert len(bid) == 26
        assert _CROCKFORD_ULID_RE.match(bid), bid

    def test_deterministic_same_bundle(self):
        assert derive_batch_id(_bundle()) == derive_batch_id(_bundle())

    def test_empty_bundle_deterministic_and_ulid_shaped(self):
        empty = {"points": [], "entities": [], "sources": [],
                 "connections": []}
        bid1 = derive_batch_id(empty)
        bid2 = derive_batch_id(empty)
        assert bid1 == bid2
        assert len(bid1) == 26 and _CROCKFORD_ULID_RE.match(bid1)

    def test_different_content_different_batch_id(self):
        a = _bundle()
        b = _bundle()
        b["points"][0]["content"] = "Different content entirely."
        assert derive_batch_id(a) != derive_batch_id(b)


class TestCanonicalInvariances:
    """E2E-5.6 variants (a)-(g) + cycle-11 (h) — one batch_id across all."""

    def test_a_shuffled_key_order(self):
        bid = derive_batch_id(_bundle())
        shuffled = _shuffle_keys(_bundle())
        assert derive_batch_id(shuffled) == bid

    def test_b_reformatted_floats(self):
        bid = derive_batch_id(_bundle())
        for formatted in (0.30, 0.3, 3e-1):
            variant = copy.deepcopy(_bundle())
            variant["connections"][0]["confidence"] = formatted
            assert derive_batch_id(variant) == bid, formatted

    def test_c_reordered_connection_list(self):
        bid = derive_batch_id(_bundle())
        variant = copy.deepcopy(_bundle())
        variant["connections"] = list(reversed(variant["connections"]))
        assert derive_batch_id(variant) == bid

    def test_d_nfc_vs_nfd(self):
        bid = derive_batch_id(_bundle())
        variant = copy.deepcopy(_bundle())
        variant["points"][0]["content"] = unicodedata.normalize(
            "NFD", variant["points"][0]["content"])
        assert derive_batch_id(variant) == bid

    def test_e_int_vs_float_numerics(self):
        int_variant = copy.deepcopy(_bundle())
        int_variant["connections"][0]["confidence"] = 1
        float_variant = copy.deepcopy(int_variant)
        float_variant["connections"][0]["confidence"] = 1.0
        assert derive_batch_id(int_variant) == derive_batch_id(float_variant)

    def test_f_reordered_item_lists(self):
        bid = derive_batch_id(_bundle())
        variant = copy.deepcopy(_bundle())
        variant["points"] = list(reversed(variant["points"]))
        variant["sources"] = list(reversed(variant["sources"]))
        variant["entities"] = list(reversed(variant["entities"]))
        assert derive_batch_id(variant) == bid

    def test_g_ref_keys_renamed(self):
        bid = derive_batch_id(_bundle())
        renamed = _rename_refs(
            _bundle(),
            {"p1": "point-alpha", "p2": "point-beta", "s1": "subject-gamma",
             "src1": "source-delta", "c1": "conn-1", "c2": "conn-2",
             "c3": "conn-3"},
        )
        assert derive_batch_id(renamed) == bid

    def test_h_same_pair_reify_label_tie_shape(self):
        """CYCLE-11 (h): two same-pair reify connections differing ONLY in
        label, connection list reordered + label/mitigation/reify reordered
        at the item level → identical batch_id (a regression dropping
        label/reify from the sort key splits this class)."""
        base = {
            "points": [
                {"ref": "p1", "kind": "claim", "content": "A"},
                {"ref": "p2", "kind": "claim", "content": "B"},
            ],
            "connections": [
                {"from": "p1", "to": "p2", "operator": "IMPL",
                 "reify": True, "label": "addresses"},
                {"from": "p1", "to": "p2", "operator": "IMPL",
                 "reify": True, "label": "opposes"},
            ],
        }
        bid = derive_batch_id(base)
        variant = {
            "points": [
                {"ref": "p2", "kind": "claim", "content": "B"},
                {"ref": "p1", "kind": "claim", "content": "A"},
            ],
            "connections": [
                {"reify": True, "label": "opposes", "operator": "IMPL",
                 "to": "p2", "from": "p1"},
                {"label": "addresses", "reify": True, "to": "p2",
                 "from": "p1", "operator": "IMPL"},
            ],
        }
        assert derive_batch_id(variant) == bid


class TestRefExpansion:
    def test_refs_expand_to_item_content_not_ids(self):
        """REF EXPANSION PINNED: batch_id never depends on assigned ids."""
        bid = derive_batch_id(_bundle())
        # Any id-based expansion would break determinism across runs; the
        # bundle has no ids at all, so the derivation must be a pure function
        # of the bundle dict. (Crash-retry minting different ULIDs for missed
        # items → identical batch_id — the A4 brief pin.)
        assert bid == derive_batch_id(json.loads(json.dumps(_bundle())))

    def test_entity_ref_field_resolves_to_item(self):
        # An entity referencing a point by ref must hash the POINT'S CONTENT —
        # a rename of the point's ref key alone must not split batch_id.
        bundle = {
            "points": [
                {"ref": "p1", "kind": "claim", "content": "The claim"},
            ],
            "entities": [
                {"ref": "e1", "type": "subject", "name": "Author",
                 "authoredBy": "p1"},
            ],
            "connections": [],
        }
        bid = derive_batch_id(bundle)
        renamed = _rename_refs(bundle, {"p1": "renamed-ref", "e1": "e-renamed"})
        assert derive_batch_id(renamed) == bid

    def test_connection_to_list_resolves_elementwise(self):
        bundle = {
            "points": [
                {"ref": "p1", "kind": "claim", "content": "A"},
                {"ref": "p2", "kind": "claim", "content": "B"},
                {"ref": "p3", "kind": "claim", "content": "C"},
            ],
            "connections": [
                {"from": "p1", "to": ["p2", "p3"], "operator": "NAND"},
            ],
        }
        bid = derive_batch_id(bundle)
        renamed = _rename_refs(bundle, {"p1": "x", "p2": "y", "p3": "z"})
        assert derive_batch_id(renamed) == bid


class TestClockIndependence:
    """CYCLE-21 pin: batch_id contains NO time component."""

    def test_shifted_mock_clock(self, monkeypatch):
        import time as time_module
        bid = derive_batch_id(_bundle())
        real = time_module.time

        def _shifted():
            return real() + 86400 * 30  # a month later

        monkeypatch.setattr(time_module, "time", _shifted)
        assert derive_batch_id(_bundle()) == bid


class TestCanonicalPrimitives:
    def test_canonical_json_embeds_nfc_and_numeric_collapse(self):
        blob = canonical_json({"b": 1.0, "a": unicodedata.normalize(
            "NFD", "café")})
        assert "1" in blob          # 1.0 → "1" (int/float collapse)
        assert blob.index("{") < blob.index("}")  # compact separators
        assert blob == canonical_json({"a": "café", "b": 1})

    def test_resolve_refs_strips_ref_keys(self):
        resolved = resolve_refs(_bundle())
        for section in ("points", "entities", "sources", "connections"):
            for item in resolved[section]:
                assert "ref" not in item, item

    def test_resolve_refs_leaves_external_ids_literal(self):
        # A connection to an EXTERNAL (non-bundle) id passes through unchanged.
        bundle = {"points": [], "connections": [
            {"from": "external-id-123", "to": "another-id", "operator": "IMPL"}]}
        resolved = resolve_refs(bundle)
        assert resolved["connections"][0]["from"] == "external-id-123"

    def test_cyclic_refs_raise_not_loop(self):
        # Defensive: an acyclic reference graph is the supported shape; a
        # pathological entity→entity cycle (aboutSubject both ways) must raise
        # rather than recurse forever.
        with pytest.raises(ValueError):
            derive_batch_id({
                "entities": [
                    {"ref": "e1", "type": "subject", "name": "One",
                     "aboutSubject": "e2"},
                    {"ref": "e2", "type": "subject", "name": "Two",
                     "aboutSubject": "e1"},
                ],
            })

    def test_unsupported_type_raises(self):
        with pytest.raises(TypeError):
            canonicalize(object())
