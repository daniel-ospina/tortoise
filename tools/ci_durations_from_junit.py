#!/usr/bin/env python3
"""Extract the `config/ci-surfaces.yml` `durations` map from CI junit.xml (#3400).

`durations` is the weight table `split_fast_gate` (LPT) packs the push halves
by. It must be regenerated from the wall time each test FILE actually took in
a real CI run. Two candidate sources exist, and only one can produce this map:

  * `tools/ci_timing.py` — derives per-file time from pytest's
    ``--durations=15`` block (at most 15 tests per job) and keys by BASENAME.
    It cannot produce this map: every test below a job's top-15 cutoff is
    invisible (498 files cannot be covered from 15 tests/job), and basename
    keys lose the directory (``bench/``, ``eval/retrieval/``) that the
    manifest's ``tests/``-relative keys carry. Its own docstring calls the
    per-file table "derived, not measured".
  * the ``junit.xml`` that ``python-ci.yml`` already writes
    (``--junitxml=/tmp/junit.xml -o junit_family=xunit1``) and uploads inside
    every ``pytest-log-*`` artifact. It carries EVERY testcase with a
    ``file="tests/..."`` attribute, so all 498 files aggregate exactly — this
    is how the committed 498-entry map was produced (run 34725816431).

This tool reads the junit artifacts and writes the map.

Usage:
    # from a directory of downloaded artifacts (recursive: every junit.xml)
    python3 tools/ci_durations_from_junit.py logs/ --out durations.yml

    # explicit files
    python3 tools/ci_durations_from_junit.py a/junit.xml b/junit.xml

    # human-readable summary of the low-confidence entries
    python3 tools/ci_durations_from_junit.py logs/ --summary

Output: ``tests/``-relative keys, one decimal, sorted — paste-compatible with
the ``durations:`` block of ``config/ci-surfaces.yml``.

#3400 P2-2: a file with ZERO executed testcases (every ``<testcase>`` carries
a ``<skipped>`` child) is emitted at ``DEFAULT_FAST_WEIGHT`` (2.0), NOT
``0.0``. Its junit ``time`` is ~0 because the run never executed it — a
skipped collection or an absent optional extra — so a ``0.0`` would assert
the file is free while a gate that does pay for it appears with no
counterweight. Real examples in the measured run: ``test_crash_recovery_e2e``
and ``test_event_log``. Such entries are also flagged in the output so the
coverage guard / a human can tell them from genuine sub-0.05s measurements.
"""
from __future__ import annotations

import argparse
import contextlib
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# Single source of truth for the default weight — never restate 2.0 here.
from ci_selection import DEFAULT_FAST_WEIGHT

DEFAULT_PRECISION = 1
LOW_CONFIDENCE_NOTE = ("  # no executed testcases in the measured run — "
                       "packed at DEFAULT_FAST_WEIGHT")


def key_for(file_attr: str) -> str:
    """Map a junit ``file`` attribute to the manifest's ``tests/``-relative key.

    ``tests/test_event_log.py`` -> ``test_event_log.py``. Also tolerates an
    absolute runner path (…/work/tortoise/tortoise/tests/x.py) so the tool
    works on a junit.xml pulled from anywhere.
    """
    p = (file_attr or "").replace("\\", "/").strip()
    if p.startswith("tests/"):
        return p[len("tests/"):]
    idx = p.find("/tests/")
    if idx >= 0:
        return p[idx + len("/tests/"):]
    return p


def discover(paths: list[Path]) -> list[Path]:
    """junit.xml files from explicit paths and/or directories (recursive)."""
    found: list[Path] = []
    for p in paths:
        if p.is_dir():
            found.extend(sorted(p.rglob("junit.xml")))
        elif p.is_file():
            found.append(p)
    # de-dupe, keep deterministic order
    return sorted(dict.fromkeys(found))


def extract(paths: list[Path]) -> dict[str, dict]:
    """Aggregate per-file ``{total_s, testcases, executed}`` from junit files.

    ``executed`` counts testcases WITHOUT a ``<skipped>`` child — a ``<failure>``
    or ``<error>`` testcase still ran, so it counts. A file whose every
    testcase was skipped has ``executed == 0``.
    """
    agg: dict[str, dict] = {}
    for path in paths:
        root = ET.parse(path).getroot()
        for tc in root.iter("testcase"):
            key = key_for(tc.get("file") or "")
            if not key:
                continue
            st = agg.setdefault(key, {"total_s": 0.0, "testcases": 0, "executed": 0})
            st["testcases"] += 1
            with contextlib.suppress(TypeError, ValueError):
                st["total_s"] += float(tc.get("time") or 0.0)
            if tc.find("skipped") is None:
                st["executed"] += 1
    return agg


def weights(agg: dict[str, dict], precision: int = DEFAULT_PRECISION,
            default: float = DEFAULT_FAST_WEIGHT) -> dict[str, float]:
    """Weight per file: the measured total, or `default` when nothing executed."""
    out: dict[str, float] = {}
    for key, st in agg.items():
        if st["executed"] == 0:
            out[key] = default
        else:
            out[key] = round(st["total_s"], precision)
    return out


def low_confidence(agg: dict[str, dict]) -> set[str]:
    """Files with zero executed testcases — weights that are a placeholder."""
    return {k for k, st in agg.items() if st["executed"] == 0}


def render(ws: dict[str, float], flagged: set[str],
           precision: int = DEFAULT_PRECISION, indent: str = "  ") -> str:
    """Sorted ``key: value`` YAML lines, paste-compatible with the map."""
    lines = []
    for key in sorted(ws):
        note = LOW_CONFIDENCE_NOTE if key in flagged else ""
        lines.append(f"{indent}{key}: {ws[key]:.{precision}f}{note}")
    return "\n".join(lines) + ("\n" if lines else "")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Extract the config/ci-surfaces.yml `durations` map from junit.xml (#3400)")
    ap.add_argument("paths", nargs="+", type=Path,
                    help="junit.xml file(s) and/or artifact directory(ies)")
    ap.add_argument("--out", type=Path, default=None,
                    help="write the map here (default: stdout)")
    ap.add_argument("--precision", type=int, default=DEFAULT_PRECISION,
                    help=f"decimal places (default {DEFAULT_PRECISION})")
    ap.add_argument("--indent", default="  ", help="YAML indent (default 2 spaces)")
    ap.add_argument("--summary", action="store_true",
                    help="print a summary to stderr (file count, low-confidence entries)")
    args = ap.parse_args(argv)

    junits = discover(args.paths)
    if not junits:
        print(f"::error::no junit.xml found under {[str(p) for p in args.paths]}",
              file=sys.stderr)
        return 1

    agg = extract(junits)
    if not agg:
        print(f"::error::no testcases with a `file` attribute in {len(junits)} junit.xml",
              file=sys.stderr)
        return 1
    ws = weights(agg, args.precision)
    flagged = low_confidence(agg)

    body = render(ws, flagged, args.precision, args.indent)
    if args.out:
        args.out.write_text(body)
        print(f"wrote {args.out} ({len(ws)} files from {len(junits)} junit.xml)")
    else:
        sys.stdout.write(body)

    if args.summary:
        zeros = sorted(k for k, v in ws.items() if v == 0)
        print(f"junit files: {len(junits)}", file=sys.stderr)
        print(f"files: {len(ws)} · measured >0: {len(ws) - len(zeros)} · "
              f"zero-valued: {len(zeros)}", file=sys.stderr)
        print(f"low-confidence (0 executed testcases → {DEFAULT_FAST_WEIGHT}s): "
              f"{len(flagged)} {sorted(flagged)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
