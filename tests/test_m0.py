"""M0 tests — transcript → extractor → log → HTML render end-to-end.

Runnable without pytest:  python tests/test_m0.py
(also works under pytest if installed).
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tortoise.m0 import main  # noqa: E402


def _tmp_dir() -> str:
    return tempfile.mkdtemp(prefix="tortoise_m0_")


TRANSCRIPT = """\
Alice: I think we should use BFS because graphs need traversal
Bob: But BFS is memory heavy however it can be optimized
Charlie: Therefore we should consider DFS as an alternative
"""


def test_m0_end_to_end():
    """Full pipeline: transcript → log events → HTML output with expected content."""
    d = _tmp_dir()
    transcript = Path(d) / "transcript.txt"
    out = Path(d) / "graph.html"
    log = Path(d) / "events.jsonl"
    transcript.write_text(TRANSCRIPT, encoding="utf-8")

    main(argv=[str(transcript), "--out", str(out), "--log", str(log)])

    # Output HTML exists and contains expected content
    assert out.exists(), "HTML output file not created"
    html_content = out.read_text(encoding="utf-8")
    assert "<html>" in html_content, "missing <html> tag"
    assert "<svg" in html_content, "missing <svg> element"
    assert "BFS" in html_content, "transcript content missing from HTML"
    assert "Alice" in html_content, "speaker name missing from HTML"

    # Log exists and has expected event types
    assert log.exists(), "log file not created"
    events = [json.loads(line) for line in
              log.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(events) >= 3, f"expected >=3 events, got {len(events)}"
    types = {e["type"] for e in events}
    assert "PointAdded" in types, "missing PointAdded events"
    assert "OperatorAdded" in types, "missing OperatorAdded events"

    print("PASS test_m0_end_to_end")


def test_m0_creates_output():
    """Output file is created and non-empty after running main."""
    d = _tmp_dir()
    transcript = Path(d) / "transcript.txt"
    out = Path(d) / "graph.html"
    log = Path(d) / "events.jsonl"
    transcript.write_text(TRANSCRIPT, encoding="utf-8")

    assert not out.exists()
    main(argv=[str(transcript), "--out", str(out), "--log", str(log)])
    assert out.exists() and out.stat().st_size > 0, "output file empty or missing"

    print("PASS test_m0_creates_output")


def test_m0_existing_log_deleted():
    """Pre-existing log is deleted and replaced with fresh events."""
    d = _tmp_dir()
    transcript = Path(d) / "transcript.txt"
    out = Path(d) / "graph.html"
    log = Path(d) / "events.jsonl"
    transcript.write_text(TRANSCRIPT, encoding="utf-8")

    log.write_text('{"stale": true}\n', encoding="utf-8")
    assert log.exists()

    main(argv=[str(transcript), "--out", str(out), "--log", str(log)])

    new_content = log.read_text(encoding="utf-8")
    assert '"stale"' not in new_content, "old log content not removed"
    assert "PointAdded" in new_content, "new events not written to log"

    print("PASS test_m0_existing_log_deleted")


def test_m0_log_has_events():
    """Log contains correct event counts and operator types for the test transcript."""
    d = _tmp_dir()
    transcript = Path(d) / "transcript.txt"
    out = Path(d) / "graph.html"
    log = Path(d) / "events.jsonl"
    transcript.write_text(TRANSCRIPT, encoding="utf-8")

    main(argv=[str(transcript), "--out", str(out), "--log", str(log)])

    events = [json.loads(line) for line in
              log.read_text(encoding="utf-8").splitlines() if line.strip()]

    point_events = [e for e in events if e["type"] == "PointAdded"]
    op_events = [e for e in events if e["type"] == "OperatorAdded"]

    assert len(point_events) == 3, f"expected 3 PointAdded, got {len(point_events)}"
    assert len(op_events) == 2, f"expected 2 OperatorAdded, got {len(op_events)}"

    op_types = {e["point"]["operator"]["op_type"] for e in op_events}
    assert "NAND" in op_types, "missing NAND operator"
    assert "IMPL" in op_types, "missing IMPL operator"

    print("PASS test_m0_log_has_events")


def _run_all():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("\nall M0 tests passed")


if __name__ == "__main__":
    _run_all()
