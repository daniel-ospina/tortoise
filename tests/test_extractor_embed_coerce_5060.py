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

import ast
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise import extractor_v2 as v2  # noqa: E402, RUF100

_SOURCE = (Path(__file__).resolve().parent.parent / "tortoise"
           / "extractor_v2.py")

#: Raw `X.get(field, '')[:N]` slices deliberately NOT routed through `_clip`:
#: they render S3 GRAPH-search results, not model output — graph content is
#: `str`-coerced by the write gate, so these cannot raise on an LLM value.
_GRAPH_SIDE_RAW_SLICES = {
    "p.get('content', '')[:120]",
    "e.get('content', '')[:120]",
}

#: Non-string values an LLM can emit through `_parse_json_robust` rung 1
#: (parsed JSON without `_validate_output_shape`).
_NON_STRING_VALUES = [None, 0, 42, 1.5, False, [], {}]


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


class TestEveryReportLaneCoerces5060:
    """Value coverage across every EXISTING report lane and non-string value.

    The per-lane tests above pin each site's exact rendering; this sweep
    widens the VALUE axis (an LLM emits more than `null`/`0`). It is a
    hand-maintained lane list and cannot see a NEW lane — the structural
    guard against a new raw slice site is
    `test_no_raw_get_slice_outside_the_graph_side_allowlist` below.
    """

    @staticmethod
    def _embed_list(lane: str, value: object) -> dict:
        if lane == "entity":
            # A DISTINCT minted kind: with `kind: None` the report entry is
            # "None (entity '<name>')" and the `None` value case would pass
            # off the KIND prefix instead of the coerced name.
            return {"entities": [{"name": value, "kind": "minted:ekind"}]}
        if lane == "event":
            return {"events": [{"content": value, "eventKind": "minted:x"}]}
        return {"points": [{"content": value, "pointKind": "minted:kind"}]}

    @pytest.mark.parametrize("lane", ["entity", "event", "point"])
    @pytest.mark.parametrize("value", _NON_STRING_VALUES, ids=repr)
    def test_payload_survives_and_the_report_degrades(self, lane, value):
        out = _embed(self._embed_list(lane, value))
        assert out["payload"] is not None
        assert out["payload"][{"entity": "entities", "event": "events",
                               "point": "points"}[lane]], (
            f"the {lane} lane dropped the item instead of carrying it")
        assert out["minted_kinds"], (
            f"{lane} lane emitted no report entry for {value!r} — the coerced "
            "text never reached the report")
        assert str(value)[:60] in out["minted_kinds"][0]

    @pytest.mark.parametrize("field", ["src", "dst"])
    @pytest.mark.parametrize("value", _NON_STRING_VALUES, ids=repr)
    def test_operator_warning_lane_coerces(self, field, value):
        """The fifth and sixth sites: a non-string MITIGATES endpoint whose
        `str()` resolves to a numeric point content reaches the strength
        warning's `[:40]` slice. BOTH `src` and `dst` are swept — they are two
        separate slice expressions."""
        src_value, dst_value = ((value, "dst point") if field == "src"
                                else ("src point", value))
        out = _embed({
            "points": [{"content": str(src_value), "pointKind": "statement"},
                       {"content": str(dst_value), "pointKind": "statement"}],
            "operators": [{"src": src_value, "dst": dst_value,
                           "op_type": "MITIGATES", "strength": "n/a"}],
        })
        assert out["payload"] is not None
        assert any("MITIGATES strength 'n/a' not numeric" in w
                   for w in out["warnings"]), out["warnings"]
        assert any(f"('{src_value!s}'→'{dst_value!s}')" in w
                   for w in out["warnings"]), out["warnings"]


def _raw_get_slice_sites() -> list[tuple[int, str]]:
    """Every `X.get(...)[:]` slice in the module — the #5060 defect shape."""
    tree = ast.parse(_SOURCE.read_text(encoding="utf-8"))
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Subscript):
            continue
        callee = node.value
        if (isinstance(callee, ast.Call)
                and isinstance(callee.func, ast.Attribute)
                and callee.func.attr == "get"):
            hits.append((node.lineno, ast.unparse(node)))
    return sorted(hits)


def test_no_raw_get_slice_outside_the_graph_side_allowlist():
    """The STRUCTURAL guard the lane sweep cannot be: any NEW raw
    `.get(...)[:]` slice on a model-supplied field reds here, whichever lane
    or field it is added on.

    This is the artifact behind "the class cannot recur": the three
    site-scoped fixes that preceded #5060 each closed only the instance they
    were shown, because no test could see the sites nobody had looked at yet.
    """
    raw = _raw_get_slice_sites()
    # Non-vacuity: if the AST shape changes, an empty scan must red loudly
    # rather than pass silently (#4047's "permanently green, unexecuted gate").
    assert raw, "the AST scan found no `.get(...)[:]` slice — guard is vacuous"
    unexpected = [f"{line}: {code}" for line, code in raw
                  if code not in _GRAPH_SIDE_RAW_SLICES]
    assert not unexpected, (
        "raw `.get(...)[:]` slice on a model-supplied field — route it "
        f"through `_clip` so the report cannot raise (#5060): {unexpected}")
