"""Render tests — _wrap + render at 100% coverage.

Runnable without pytest:  .venv/bin/python tests/test_render.py
(also works under pytest if installed).
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tortoise.render import _wrap, render  # noqa: E402


# ── helpers ────────────────────────────────────────────────────────────────
def _point(
    pid: str,
    content: str,
    created_at: str,
    speaker: str = "alice",
    operator: dict | None = None,
) -> dict:
    p: dict = {
        "id": pid,
        "content": content,
        "context": "",
        "created_at": created_at,
        "provenance": {"speaker": speaker, "source": "doc.txt"},
    }
    if operator is not None:
        p["operator"] = operator
    return p


# ── _wrap tests ────────────────────────────────────────────────────────────
def test_wrap_short_text():
    result = _wrap("hello", width=26)
    assert result == ["hello"], result
    print("PASS test_wrap_short_text")


def test_wrap_long_text():
    result = _wrap("this is a much longer text that should be split", width=12)
    assert len(result) > 1, f"expected multiple lines, got {result}"
    for ln in result[:-1]:
        assert len(ln) <= 12, f"line too long: {ln!r}"
    print("PASS test_wrap_long_text")


def test_wrap_max_lines():
    # Enough words that we hit max_lines before consuming them all
    result = _wrap("one two three four five six seven eight nine ten", width=8, max_lines=3)
    assert len(result) == 3, result
    # Last line should have an ellipsis because text was truncated
    assert "…" in result[-1], f"expected ellipsis in last line, got {result[-1]!r}"
    print("PASS test_wrap_max_lines")


def test_wrap_empty():
    result = _wrap("")
    assert result == [], result
    print("PASS test_wrap_empty")


def test_wrap_exact_width():
    # Word at width-1 fits on one line (len + space ≤ width)
    result = _wrap("1234567890123456789012345", width=26)
    assert result == ["1234567890123456789012345"], result
    print("PASS test_wrap_exact_width")


# ── render tests ───────────────────────────────────────────────────────────
def test_render_empty():
    html = render({})
    assert "0 points" in html, html[:200]
    assert "<svg" in html
    # No rect polygons (points)
    assert "<rect" not in html
    print("PASS test_render_empty")


def test_render_single_statement():
    points = {"p1": _point("p1", "Simple statement", "2026-01-01T00:00:00Z")}
    html = render(points)
    assert "<rect" in html
    assert "1 points" in html
    assert "alice" in html
    assert "Simple statement" in html
    print("PASS test_render_single_statement")


def test_render_single_operator():
    points = {
        "op1": _point(
            "op1", "", "2026-01-01T00:00:00Z",
            speaker="", operator={"op_type": "NAND", "inputs": []},
        )
    }
    html = render(points)
    assert "NAND" in html
    assert "<polygon" in html  # diamond
    assert "#c0392b" in html    # NAND red (in polygon fill)
    print("PASS test_render_single_operator")


def test_render_operator_with_inputs():
    points = {
        "a": _point("a", "statement A", "2026-01-01T00:00:00Z"),
        "b": _point("b", "statement B", "2026-01-01T00:00:01Z"),
        "op": _point(
            "op", "", "2026-01-01T00:00:02Z",
            speaker="", operator={"op_type": "NAND", "inputs": ["a", "b"]},
        ),
    }
    html = render(points)
    assert "<line" in html
    assert "3 points" in html
    # Both inputs should have lines
    lines = html.count("<line")
    assert lines == 2, f"expected 2 SVG lines, got {lines}"
    print("PASS test_render_operator_with_inputs")


def test_render_operator_input_not_found():
    points = {
        "op": _point(
            "op", "", "2026-01-01T00:00:00Z",
            speaker="", operator={"op_type": "NAND", "inputs": ["ghost"]},
        ),
    }
    html = render(points)
    # No crash, no line for missing input
    assert "<line" not in html
    assert "NAND" in html
    print("PASS test_render_operator_input_not_found")


def test_render_title_in_output():
    points = {"p1": _point("p1", "x", "2026-01-01T00:00:00Z")}
    html = render(points, title="Custom Title!!")
    assert "Custom Title!!" in html
    # Default title not present
    assert "Tortoise graph" not in html
    print("PASS test_render_title_in_output")


def test_render_custom_title():
    points = {"p1": _point("p1", "x", "2026-01-01T00:00:00Z")}
    html = render(points, title="My Graph")
    assert "<title>My Graph</title>" in html
    assert "<h1>My Graph</h1>" in html
    print("PASS test_render_custom_title")


def test_render_IMPL_operator():
    points = {
        "op": _point(
            "op", "", "2026-01-01T00:00:00Z",
            speaker="", operator={"op_type": "IMPL", "inputs": []},
        )
    }
    html = render(points)
    assert "IMPL" in html
    assert "#2e8b57" in html  # IMPL green
    # Legend always mentions NAND; only 1 occurrence (legend), not in SVG polygon
    assert html.count("NAND") == 1
    print("PASS test_render_IMPL_operator")


def test_render_multiple_points():
    points = {
        f"p{i}": _point(f"p{i}", f"point {i}", f"2026-01-01T00:00:{i:02d}Z")
        for i in range(5)
    }
    html = render(points)
    assert "5 points" in html
    assert html.count("<rect") == 5
    print("PASS test_render_multiple_points")


def test_render_no_speaker():
    p = {
        "id": "p1",
        "content": "anonymous",
        "context": "",
        "created_at": "2026-01-01T00:00:00Z",
        "provenance": {},  # no speaker key
    }
    html = render({"p1": p})
    # Should not crash and should render content
    assert "anonymous" in html
    assert "<rect" in html
    print("PASS test_render_no_speaker")


def test_render_unknown_operator_type():
    points = {
        "op": _point(
            "op", "", "2026-01-01T00:00:00Z",
            speaker="", operator={"op_type": "WEIRD", "inputs": []},
        )
    }
    html = render(points)
    assert "WEIRD" in html
    assert "#7f8c8d" in html  # fallback gray
    print("PASS test_render_unknown_operator_type")


def test_render_default_title():
    points = {"p1": _point("p1", "x", "2026-01-01T00:00:00Z")}
    html = render(points)
    assert "Tortoise graph" in html
    print("PASS test_render_default_title")


# ── runner ─────────────────────────────────────────────────────────────────
def _run_all():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("\nall render tests passed")


if __name__ == "__main__":
    _run_all()
