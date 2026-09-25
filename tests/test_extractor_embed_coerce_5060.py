"""#5060 — `execute_embed` must not raise on a non-string name/content.

The slice at the three `_minted_kind_report` report sites (`[:60]`) and at the
two MITIGATES strength warnings (`[:40]`) was applied to the RAW
model-supplied value, while every OTHER read of the same field on the same
path coerces with `str(...)`. The report was therefore STRICTER than the write
gate it reports on: a `None` name / `0` content is writable (as `"None"` /
`"0"`), but slicing it raised `TypeError`.

Because `extract_session_v2` wraps S5 fail-open ("S5 must NEVER block"), the
raise did not crash the session — it set `payload = None` and discarded the
WHOLE session's extraction, not just the one odd item. `_parse_json_robust`
rung 1 returns parsed JSON without `_validate_output_shape`, so such values
reach these sites from real model output.

Each case below FAILS (TypeError) before the fix and passes after it.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise import extractor_v2 as v2  # noqa: E402, RUF100


def _embed(embed_list: dict) -> dict:
    return v2.execute_embed(embed_list, {}, session_id="bug13")


class TestMintedKindReportCoerces5060:
    """The three `[:60]` sites in `_minted_kind_report`."""

    def test_entity_name_none_returns_a_payload(self):
        out = _embed({"entities": [{"name": None, "kind": None}]})
        assert out["payload"] is not None
        assert out["minted_kinds"] == ["None (entity 'None')"]

    def test_event_content_none_returns_a_payload(self):
        out = _embed({"events": [{"content": None, "eventKind": "minted:x"}]})
        assert out["payload"] is not None
        assert out["minted_kinds"] == ["minted:x (event 'None')"]

    def test_point_content_zero_returns_a_payload(self):
        out = _embed({"points": [{"content": 0, "pointKind": "minted:kind"}]})
        assert out["payload"] is not None
        assert out["minted_kinds"] == ["minted:kind (point '0')"]

    def test_report_degrades_to_the_coerced_text(self):
        """The direct report surface: all three odd items in one list, in the
        report's own lane order (entities → events → points)."""
        assert v2._minted_kind_report({
            "entities": [{"name": None, "kind": None}],
            "events": [{"content": None, "eventKind": "minted:x"}],
            "points": [{"content": 0, "pointKind": "minted:kind"}],
        }) == ["None (entity 'None')",
               "minted:x (event 'None')",
               "minted:kind (point '0')"]

    def test_well_formed_field_rendering_is_unchanged(self):
        """A regression guard on the bound itself: the coercion is a no-op for
        the strings the gate already accepted, and the 60-char cut survives."""
        assert v2._minted_kind_report(
            {"entities": [{"name": "x" * 100, "kind": "bug13:unknown"}]}
        ) == [f"bug13:unknown (entity '{'x' * 60}')"]


class TestMitigatesWarningCoerces5060:
    """The same defect shape on `execute_embed`'s MITIGATES strength warnings.

    Reachable from real model output: an int `src` whose `str()` resolves to a
    numeric POINT CONTENT survives the resolution pre-gate, so the raw value
    reaches the `[:40]` read.
    """

    @staticmethod
    def _embed_list(strength: object) -> dict:
        return {
            "points": [{"content": "12345", "pointKind": "statement"},
                       {"content": "dst point", "pointKind": "statement"}],
            "operators": [{"src": 12345, "dst": "dst point",
                           "op_type": "MITIGATES", "strength": strength}],
        }

    def test_non_numeric_strength_returns_a_payload(self):
        out = _embed(self._embed_list("n/a"))
        assert out["payload"] is not None
        assert any("MITIGATES strength 'n/a' not numeric" in w
                   for w in out["warnings"])
        assert any("('12345'→'dst point')" in w for w in out["warnings"])

    def test_out_of_range_strength_returns_a_payload(self):
        out = _embed(self._embed_list(9.0))
        assert out["payload"] is not None
        assert any("outside [0.10, 0.50]" in w
                   and "('12345'→'dst point')" in w
                   for w in out["warnings"])


class TestClipHelper:
    """The one coercion the report sites now share."""

    def test_default_limit(self):
        assert v2._clip(None) == "None"
        assert v2._clip("y" * 100) == "y" * 60

    def test_explicit_limit_and_non_string_value(self):
        assert v2._clip(0, 40) == "0"
        assert v2._clip("y" * 100, 40) == "y" * 40
