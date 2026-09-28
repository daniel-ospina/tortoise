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
"""
from __future__ import annotations

import ast
import sys
from collections import Counter
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise import extractor_v2 as v2  # noqa: E402, RUF100

_SOURCE = (Path(__file__).resolve().parent.parent / "tortoise"
           / "extractor_v2.py")

#: Raw `X.get(field, '')[:N]` slices deliberately NOT routed through `_clip`,
#: keyed `(enclosing function, unparsed expression) -> allowed occurrences`.
#: They render S3 GRAPH-search results in `_render_search_results` — a path
#: whose caller supplies graph content that `execute_embed`'s write gate has
#: `str`-coerced (`content = str(p.get("content", "")).strip()[:1000]`). The
#: slice itself is NOT inherently safe: called directly with a non-string
#: `content` it raises. The key's function name is a BARE name, so the same
#: expression text in a differently-named function still reds, and two
#: same-named scopes red as an ambiguity rather than silently sharing the
#: allowance.
#: Growth rule: an entry is added only with evidence that the site renders
#: graph content — never to silence a real defect.
_GRAPH_SIDE_RAW_SLICES: dict[tuple[str, str], int] = {
    ("_render_search_results", "p.get('content', '')[:120]"): 1,
    ("_render_search_results", "e.get('content', '')[:120]"): 1,
}

#: Values that REPRODUCE the #5060 defect: slicing each of these raised before
#: the fix. Parsed JSON can carry any of them (an LLM emits `{"name": null}`).
_DEFECT_VALUES = [None, 0, 42, 1.5, False, {}]

#: Robustness-only: a list IS sliceable, so `[][:N]` never raised and these
#: cases CANNOT red on #5060 — they only pin that a list value still survives
#: the report instead of crashing elsewhere.
_ROBUSTNESS_ONLY_VALUES = [[]]

_NON_STRING_VALUES = _DEFECT_VALUES + _ROBUSTNESS_ONLY_VALUES


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

    @pytest.mark.parametrize("strength", ["n/a", 9.0],
                             ids=["not-numeric", "out-of-range"])
    @pytest.mark.parametrize("field", ["src", "dst"])
    def test_the_40_char_cut_binds(self, strength, field):
        """With endpoints shorter than the bound the explicit limit never
        binds, so a regression that dropped it (falling back to `_clip`'s 60
        default) would pass. Exactly ONE endpoint is long, so the assertion
        isolates the `src` site from the `dst` site."""
        long_value = "S" * 100
        src_value, dst_value = ((long_value, "dst point") if field == "src"
                                else ("src point", long_value))
        out = _embed({
            "points": [{"content": src_value, "pointKind": "statement"},
                       {"content": dst_value, "pointKind": "statement"}],
            "operators": [{"src": src_value, "dst": dst_value,
                           "op_type": "MITIGATES", "strength": strength}],
        })
        assert any(f"('{src_value[:40]}'→'{dst_value[:40]}')" in w
                   for w in out["warnings"]), out["warnings"]


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
    `test_no_unallowlisted_raw_get_slice_in_the_module` below.
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

    @pytest.mark.parametrize("lane", ["entity", "event", "point"])
    def test_the_60_char_cut_binds(self, lane):
        """With values shorter than the bound the explicit limit never binds,
        so a regression that dropped it (or changed one lane's cut) would
        pass. Both edges are pinned: the 61st character must be absent, so a
        cut of 61-99 does not pass."""
        long_value = "L" * 100
        entry = _embed(self._embed_list(lane, long_value))["minted_kinds"][0]
        assert long_value[:60] in entry
        assert long_value[:61] not in entry

    @pytest.mark.parametrize("strength", ["n/a", 9.0],
                             ids=["not-numeric", "out-of-range"])
    @pytest.mark.parametrize("field", ["src", "dst"])
    @pytest.mark.parametrize("value", _NON_STRING_VALUES, ids=repr)
    def test_operator_warning_lane_coerces(self, strength, field, value):
        """`execute_embed` has TWO MITIGATES strength warnings — the
        not-numeric fallback and the out-of-range clamp — and EACH slices both
        `src` and `dst`. All four expressions are swept here: a non-string
        endpoint whose `str()` resolves to a numeric point content survives
        the resolution pre-gate and reaches the slice.
        """
        src_value, dst_value = ((value, "dst point") if field == "src"
                                else ("src point", value))
        out = _embed({
            "points": [{"content": str(src_value), "pointKind": "statement"},
                       {"content": str(dst_value), "pointKind": "statement"}],
            "operators": [{"src": src_value, "dst": dst_value,
                           "op_type": "MITIGATES", "strength": strength}],
        })
        assert out["payload"] is not None
        label = ("MITIGATES strength 'n/a' not numeric" if strength == "n/a"
                 else "MITIGATES strength 9.0 outside [0.10, 0.50]")
        assert any(label in w for w in out["warnings"]), out["warnings"]
        assert any(f"('{src_value!s}'→'{dst_value!s}')" in w
                   for w in out["warnings"]), out["warnings"]


def _raw_get_slice_sites(tree: ast.Module) -> list[tuple[int, str, str]]:
    """Every `X.get(...)[:]` slice in `tree` — the #5060 defect shape.

    Returns `(lineno, enclosing_function, unparsed_expression)`.
    """

    def enclosing(lineno: int) -> str:
        best = ""
        best_span = None
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            end = node.end_lineno or node.lineno
            if node.lineno <= lineno <= end:
                span = end - node.lineno
                if best_span is None or span < best_span:
                    best, best_span = node.name, span
        return best or "<module>"

    hits: list[tuple[int, str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Subscript):
            continue
        if not isinstance(node.slice, ast.Slice):
            continue
        callee = node.value
        if (isinstance(callee, ast.Call)
                and isinstance(callee.func, ast.Attribute)
                and callee.func.attr == "get"):
            hits.append((node.lineno, enclosing(node.lineno),
                         ast.unparse(node)))
    return sorted(hits)


def _raw_get_slice_problems(tree: ast.Module) -> list[str]:
    """Every reason `tree` violates the raw-slice rule, as readable strings.

    Empty list = clean. A fresh AST is passed in (rather than read from disk)
    so each branch below has a synthetic positive control.
    """
    raw = _raw_get_slice_sites(tree)
    if not raw:
        # Non-vacuity: an empty scan must be a problem (#4047's permanently
        # green, unexecuted gate).
        return ["the AST scan found no `.get(...)[:]` slice — guard is vacuous"]
    counts = Counter((func, code) for _, func, code in raw)
    problems: list[str] = []
    for line, func, code in raw:
        if (func, code) not in _GRAPH_SIDE_RAW_SLICES:
            problems.append(
                f"{line}: {code}  (in {func}) is not on the graph-side "
                "allowlist — route it through `_clip` (#5060), or add a "
                "reviewed entry with evidence that it renders graph content")
    for (func, code), n in counts.items():
        allowed = _GRAPH_SIDE_RAW_SLICES.get((func, code))
        # `allowed is not None` matters: for an UNallowlisted key the message
        # below would be nonsense ("appears 1x ... route the duplicate"), so an
        # unallowlisted slice is reported by the loop above ONLY.
        if allowed is not None and n > allowed:
            problems.append(
                f"{func}: {code} appears {n}x but the allowlist allows "
                f"{allowed} — route the extra occurrence(s) through `_clip` "
                "(#5060), or raise this (function, expression)'s reviewed "
                "count if every occurrence renders graph content")
    # The allowlist key uses the BARE function name, so a second same-named
    # scope would silently inherit the allowance. Red on the ambiguity.
    names = Counter(n.name for n in ast.walk(tree)
                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)))
    for name, n in names.items():
        if n > 1 and any(name == func for func, _ in _GRAPH_SIDE_RAW_SLICES):
            problems.append(
                f"{name!r} is defined {n}x — the allowlist key is the bare "
                "function name, so a same-named second scope would silently "
                "inherit the allowance; rename one, or qualify the key by "
                "scope")
    for (func, code), allowed in _GRAPH_SIDE_RAW_SLICES.items():
        if counts.get((func, code), 0) < allowed:
            problems.append(
                f"allowlist entry {func}: {code} expects {allowed} "
                f"occurrence(s), found {counts.get((func, code), 0)} — "
                "re-check whether the slice moved or a defect is being masked")
    return problems


def test_no_unallowlisted_raw_get_slice_in_the_module():
    """No raw `X.get(...)[:]` slice in the module outside the reviewed
    graph-side allowance.

    Declared scan BOUNDARY — it matches ONE shape: a `Subscript` whose
    immediate value is a `.get(...)` call AND whose slice is an `ast.Slice`.
    It does NOT see an aliased/two-step form (`t = p.get('c', ''); t[:60]`),
    a walrus, or `p['c'][:60]`. Those stay the reviewer's job.
    """
    problems = _raw_get_slice_problems(
        ast.parse(_SOURCE.read_text(encoding="utf-8")))
    assert problems == []


#: A synthetic module carrying exactly the two reviewed graph-side slices, so
#: every branch of `_raw_get_slice_problems` has a positive control below.
_GRAPH_SIDE_BASELINE = (
    "def _render_search_results(search):\n"
    "    for p in search['points']:\n"
    "        yield p, p.get('content', '')[:120]\n"
    "    for e in search['events']:\n"
    "        yield e, e.get('content', '')[:120]\n"
)


def test_the_guard_accepts_the_graph_side_baseline():
    assert _raw_get_slice_problems(ast.parse(_GRAPH_SIDE_BASELINE)) == []


def test_the_guard_flags_an_unallowlisted_slice():
    tree = ast.parse(_GRAPH_SIDE_BASELINE +
                     "def _report(e):\n"
                     "    return e.get('name', '')[:60]\n")
    problems = _raw_get_slice_problems(tree)
    # EXACTLY one problem: the count branch must not also fire for an
    # unallowlisted key (`allowed` is None) and emit a nonsense "duplicate"
    # message for a slice that appears once.
    assert len(problems) == 1, problems
    assert "_report" in problems[0]
    assert "not on the graph-side allowlist" in problems[0]


def test_the_guard_flags_an_ambiguous_allowlisted_function_name():
    """The allowlist key is a BARE function name, so two same-named scopes
    must red rather than silently share the allowance."""
    tree = ast.parse(_GRAPH_SIDE_BASELINE +
                     "def _render_search_results(other):\n"
                     "    return other.get('content', '')[:120]\n")
    assert any("is defined 2x" in p
               for p in _raw_get_slice_problems(tree))


def test_the_guard_flags_a_duplicated_graph_side_slice():
    tree = ast.parse(_GRAPH_SIDE_BASELINE.replace(
        "        yield p, p.get('content', '')[:120]\n",
        "        yield p, p.get('content', '')[:120]\n"
        "        yield p, p.get('content', '')[:120]\n"))
    assert any("appears 2x but the allowlist allows 1" in p
               for p in _raw_get_slice_problems(tree))


def test_the_guard_flags_a_missing_graph_side_slice():
    tree = ast.parse(
        "def _render_search_results(search):\n"
        "    for p in search['points']:\n"
        "        yield p, p.get('content', '')[:120]\n")
    assert any("e.get('content', '')[:120] expects 1" in p
               for p in _raw_get_slice_problems(tree))


def test_the_guard_flags_an_empty_scan():
    assert _raw_get_slice_problems(ast.parse("x = 1\n")) == [
        "the AST scan found no `.get(...)[:]` slice — guard is vacuous"]


def test_the_scanner_still_flags_a_violation():
    """Positive control for the PREDICATE itself: a matcher narrowed to the
    allowlist's shape would still pass the baseline above, so it is pinned
    here against a plain defect-shape slice."""
    synthetic = ast.parse(
        "def _report(e):\n"
        "    return e.get('name', '')[:60]\n")
    assert [(func, code) for _, func, code in _raw_get_slice_sites(synthetic)] \
        == [("_report", "e.get('name', '')[:60]")]
