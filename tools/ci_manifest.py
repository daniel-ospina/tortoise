#!/usr/bin/env python3
"""The regenerable, value-validated selection manifest (#5050).

One generator, one validator, one contract. This module owns the DERIVED half
of ``config/ci-surfaces.yml``:

* :func:`sweep` re-derives the ``durations`` block from the junit artifacts the
  CI runs already upload (the #3395/#4766 sweep rule), writing the machine
  readable measurement record ``config/ci-durations-source.json``. The
  human-facing map is a strict projection of that record — nothing else may
  author a weight, so a rot (#3400) can no longer hide in a hand-edit.
* :func:`check` value-validates the committed manifest against the record: a
  wrong value, a missing row, a key that is not packable, a stale ``unmeasured``
  marker, and the leg **partition** (every classified file in exactly one leg)
  are all failures. It is called by ``tools/ci_selection.py --integrity`` so
  there is no parallel gate.

Why a record and not just the map: a map with no machine-readable provenance
cannot be checked by value (``0.1`` and ``1880`` both pass — #4783), and a
regeneration path that only prints YAML cannot detect drift (#4766). The record
carries the source run ids, the per-run samples, the carrying-leg rule and the
explicit ``unmeasured``/pinned/retained declarations; the map is the rendering.

The bootstrap trap (#4348/#4364/#4817): a file that has never run in CI cannot
have a measurement. ``--register`` therefore records it EXPLICITLY as
``unmeasured`` with a provisional weight — never a silent flat default — and the
next sweep replaces the provisional value and clears the marker.

CLI::

    python3 tools/ci_manifest.py sweep  --junit-dir D [--junit-dir D ...] [--write]
    python3 tools/ci_manifest.py check
    python3 tools/ci_manifest.py guard-audit   # report-only derived candidates
"""

from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import sys
import textwrap
import xml.etree.ElementTree as ET
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MANIFEST = REPO / "config" / "ci-surfaces.yml"
RECORD = REPO / "config" / "ci-durations-source.json"
SCHEMA_VERSION = 1

# The documented carrying-leg rule (#3395/#4766). A file's weight is what it
# costs the leg that CARRIES it in the split.
#
#   fast-pool file -> pytest-log-test-a / -test-b (max of the two within a run)
#   carve-out     -> pytest-log-test-carve-out (takes precedence over slow: a
#                    dual slow+carve file RUNS in the carve-out job)
#   slow-only     -> pytest-log-test-slow
#
# `test-track-b` and `d14-hosted-api` are EXCLUDED deliberately — they select
# different marker sets (`-m 'not track_b and not live'`) than the halves, so
# summing them double-counts files that also run there.
LEG_ARTIFACTS = {
    "fast": ("pytest-log-test-a", "pytest-log-test-b"),
    "carve_out": ("pytest-log-test-carve-out",),
    # #5050: the two slow matrix legs used to upload under ONE name
    # (`pytest-log-${{ github.job }}`, no half suffix — unlike the fast job), so
    # `gh run download -n` resolved the collision non-deterministically and a
    # full slow sweep was impossible. The workflow now names them per half; the
    # bare name is kept so a re-sweep over the pre-fix 2026-09 runs still reads
    # whatever `gh run download` extracted for them.
    "slow": ("pytest-log-test-slow-a", "pytest-log-test-slow-b",
             "pytest-log-test-slow"),
}
EXCLUDED_ARTIFACTS = (
    "pytest-log-test-track-b",
    "pytest-log-d14-hosted-api",
)
# The sweep "takes the larger across runs" (#3395): a file's measured cost
# depends on how loaded its leg is, and the split is a bound for a fixed
# watchdog, so the larger value wins. Floor + precision.
VALUE_FLOOR = 0.1
VALUE_DECIMALS = 1

# Rows no source artifact can re-derive keep their committed value AND an
# explicit marker — never silently dropped, never defaulted (#4766 RETAINED
# paragraph). Each entry is the note rendered next to the row.
RETAINED = {
    "test_selfhost_health_probe_executor.py":
        "#4602 embedded-lane timing retained deliberately: the CI fast legs "
        "measure 2.5-9.5s, below the 28.1s embedded-lane bound the split is "
        "sized against, so the larger value is kept",
}
# Test-enforced equality (tests/test_mcp_rename_table.py); the sweep must not
# break it.
PINS = {"test_mcp_rename_table.py": "test_bridge_table.py"}
# The explanation rendered ABOVE a pinned row. It states the condition the
# equality models (the shared import cost and a load-varied run), because a bare
# "equal to X" reads as a measurement — and the test that enforces the pair also
# asserts the rendered comment states that condition. Data, like RETAINED.
PIN_NOTES = {
    "test_mcp_rename_table.py":
        "pinned equal to test_bridge_table.py (test-enforced): the two generator "
        "suites pay the same shared-conftest + generator import cost, so one weight "
        "for the pair keeps the split independent of which of the two a load-varied "
        "run happens to slow down. Not this file's own measurement.",
}

# The flat weight a never-measured file packs at. Mirrors
# ci_selection.DEFAULT_FAST_WEIGHT; a provisional row is EXPLICIT and marked,
# which is the whole difference from the silent default #3400 removed.
PROVISIONAL_WEIGHT = 2.0


def _ci_selection():
    """The loaded ``ci_selection`` module, whichever name it was imported under.

    ``ci_manifest`` imports it lazily (it imports this module back), and under
    pytest it may already be loaded as ``tools.ci_selection`` — reusing the
    loaded module keeps monkeypatching and module state consistent instead of
    creating a second copy under a different name.
    """
    for name in ("tools.ci_selection", "ci_selection"):
        mod = sys.modules.get(name)
        if mod is not None and hasattr(mod, "fast_pool"):
            return mod
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    if str(REPO / "tools") not in sys.path:
        sys.path.insert(0, str(REPO / "tools"))
    try:
        from tools import ci_selection as mod
    except ImportError:  # pragma: no cover - direct `tools/` runtime
        import ci_selection as mod
    return mod


def _bare(path_or_name: str) -> str:
    """junit `file` attribute / manifest name -> the manifest's bare key.

    junit writes ``tests/eval/retrieval/test_x.py``; the manifest keys are
    ``eval/retrieval/test_x.py``. Both map to the same key.
    """
    name = path_or_name.replace("\\", "/")
    if name.startswith("tests/"):
        name = name[len("tests/"):]
    return name


def _round(value: float) -> float:
    return round(max(float(value), VALUE_FLOOR), VALUE_DECIMALS)


# ── the sweep (generator) ────────────────────────────────────────────────


def parse_junit(path: Path) -> dict[str, float]:
    """Per-file wall time (sum of ``testcase/@time``) from a junit XML."""
    sums: dict[str, float] = {}
    root = ET.parse(path).getroot()
    for case in root.iter("testcase"):
        file_attr = case.get("file")
        if not file_attr:
            continue
        try:
            t = float(case.get("time") or 0.0)
        except ValueError:
            t = 0.0
        key = _bare(file_attr)
        sums[key] = sums.get(key, 0.0) + t
    return sums


def collect_run(junit_dir: Path) -> dict[str, dict[str, float]]:
    """One source run's per-leg per-file sums.

    Looks EXACTLY one level down (``<dir>/<artifact-name>/junit.xml``) — the
    ``gh run download`` layout — so there is no recursive walk.
    """
    legs: dict[str, dict[str, float]] = {}
    for leg, artifacts in LEG_ARTIFACTS.items():
        merged: dict[str, float] = {}
        for artifact in artifacts:
            xml = junit_dir / artifact / "junit.xml"
            if not xml.exists():
                continue
            for key, value in parse_junit(xml).items():
                # fast legs merge by max (a file runs in exactly one half, but
                # a stale artifact may carry both); other legs have one source.
                merged[key] = max(merged.get(key, 0.0), value)
        legs[leg] = merged
    return legs


def carrying_leg(name: str, fast: set[str], slow: set[str],
                 carve: set[str]) -> str | None:
    """The leg whose artifact measures ``name`` (carve-out wins over slow)."""
    if name in carve:
        return "carve_out"
    if name in fast:
        return "fast"
    if name in slow:
        return "slow"
    return None


def _parse_source_meta(entries: list[str]) -> dict[str, dict[str, str]]:
    """``RUNID=HEAD,KIND`` (either field may be empty) -> metadata."""
    meta: dict[str, dict[str, str]] = {}
    for entry in entries:
        run_id, _, rest = entry.partition("=")
        head, _, kind = rest.partition(",")
        meta[run_id.strip()] = {"head": head.strip(), "kind": kind.strip()}
    return meta


def sweep(sources: list[tuple[str, dict[str, dict[str, float]]]],
          manifest: dict, prior: dict | None = None,
          meta: dict[str, dict[str, str]] | None = None) -> dict:
    """Derive the measurement record from junit sources + the manifest.

    ``sources`` is ``[(run_id, legs), ...]``. ``prior`` is the previously
    committed record (used to carry forward values the sweep cannot measure);
    it is REQUIRED for a faithful regeneration — a PARTIAL artifact set retains
    the committed value and marks the row, and only a sample set at least as
    complete as the prior's may move a weight (never silently down when a leg
    was cut, #3395/#5050).
    """
    cs = _ci_selection()
    fast = set(cs.fast_pool(manifest))
    slow = set(manifest.get("slow_files", []))
    carve = cs.carve_out_files(manifest)
    allowed = fast | slow  # a carve-ONLY key is dead (#4783): never recorded
    prior_rows = (prior or {}).get("rows", {})

    sources_meta = []
    for run_id, _legs in sources:
        entry = {"run_id": run_id}
        entry.update((meta or {}).get(run_id, {}))
        sources_meta.append(entry)

    rows: dict[str, dict] = {}
    for name in sorted(allowed):
        leg = carrying_leg(name, fast, slow, carve)
        samples: dict[str, float] = {}
        for run_id, legs in sources:
            value = legs.get(leg, {}).get(name) if leg else None
            if value is not None:
                samples[run_id] = round(value, 2)
        prior_row = prior_rows.get(name) or {}
        prior_samples = {k: float(v)
                         for k, v in (prior_row.get("samples") or {}).items()}
        prior_value = prior_row.get("value")
        missing = set(prior_samples) - set(samples)
        row: dict = {"leg": leg, "samples": samples}
        if missing:
            # PARTIAL artifact set (#5050/#3395): a run that measured this row
            # before is absent now (a leg was cut, or — until the workflow
            # named them — the two slow legs collided on one artifact name).
            # The record must REMEMBER that run: replacing its sample with the
            # partial set erases the very gap the next sweep compares against,
            # so a second partial sweep finds no missing run and silently drops
            # the value. Carry the absent runs' samples forward
            # UNCONDITIONALLY — the marker/floor below apply only when the
            # reading is also lower, but the sample universe must be a fixed
            # point of the sweep either way.
            row["samples"] = {**prior_samples, **samples}
        if name in RETAINED:
            # A row no source artifact can re-derive keeps its committed value
            # AND an explicit marker — it is never silently re-derived (#4766).
            row["value"] = _unmeasured_value(name, prior_rows)
            row["unmeasured"] = True
            row["retained"] = True
            row["note"] = RETAINED[name]
        elif not samples:
            # No leg measured the row this sweep. Carry the committed value
            # forward; when the prior had measured it, keep its samples too and
            # mark the row retained (an unmeasured row WITH samples is a carried
            # forward row, not a stale marker).
            row["value"] = _unmeasured_value(name, prior_rows)
            row["unmeasured"] = True
            if row["samples"]:
                row["retained"] = True
            note = prior_rows.get(name, {}).get("note")
            if note is None:
                note = ("no source leg measured it — carried forward, "
                        "refresh from the next sweep")
            row["note"] = note
        elif missing and isinstance(prior_value, (int, float)) and \
                _round(max(samples.values())) < float(prior_value):
            # The partial reading is BELOW the committed value, so it may not
            # move the weight down: retain the committed value and mark it. A
            # sweep may lower a value only when its sample set is at least as
            # complete as the prior's.
            row["value"] = float(prior_value)
            row["unmeasured"] = True
            row["retained"] = True
            row["note"] = (
                f"partial artifact set — measured by {len(samples)} of "
                f"{len(prior_samples)} prior source runs; committed value "
                f"{float(prior_value):g} retained")
        else:
            # A complete (or raising) reading: the value is the max over the
            # row's whole sample universe, which after the merge above includes
            # the runs a partial sweep could not re-measure.
            row["value"] = _round(max(row["samples"].values()))
        rows[name] = row

    # Pinned equality: the sweep must not break the test-enforced pair.
    for target, source in PINS.items():
        if target in rows and source in rows:
            rows[target]["value"] = rows[source]["value"]
            rows[target]["pinned_to"] = source
            if target in PIN_NOTES:
                rows[target]["note"] = PIN_NOTES[target]

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_by": "tools/ci_manifest.py sweep",
        "leg_rule": {leg: list(arts) for leg, arts in LEG_ARTIFACTS.items()},
        "excluded_artifacts": list(EXCLUDED_ARTIFACTS),
        "sources": sources_meta,
        "pins": dict(PINS),
        "rows": rows,
    }


def _unmeasured_value(name: str, prior_rows: dict) -> float:
    """Carry a prior value forward; otherwise the explicit provisional weight."""
    prior = prior_rows.get(name)
    if isinstance(prior, dict) and isinstance(prior.get("value"), (int, float)):
        return float(prior["value"])
    return PROVISIONAL_WEIGHT


def seed_from_manifest(manifest: dict) -> dict:
    """A record skeleton built from the committed ``durations`` map.

    Used on the FIRST sweep (before a record exists) so rows the artifacts
    cannot measure — and the deliberately RETAINED row — are carried forward
    from the committed map rather than dropped to the provisional default.
    """
    cs = _ci_selection()
    fast = set(cs.fast_pool(manifest))
    slow = set(manifest.get("slow_files", []))
    carve = cs.carve_out_files(manifest)
    rows: dict[str, dict] = {}
    for name, value in _manifest_durations(manifest).items():
        if name not in fast | slow:
            continue  # a carve-only row is dead and is dropped (#4783)
        rows[name] = {
            "leg": carrying_leg(name, fast, slow, carve),
            "samples": {},
            "value": float(value),
            "unmeasured": True,
        }
    return {"rows": rows}


# ── rendering / rewriting the manifest ───────────────────────────────────


def render_rows(record: dict) -> str:
    """The `durations:` rows, rendered from the record (sorted by weight)."""
    rows = record.get("rows", {})
    ordered = sorted(rows.items(), key=lambda kv: (-float(kv[1]["value"]), kv[0]))
    lines = []
    for name, row in ordered:
        if row.get("pinned_to"):
            # A pin's explanation goes ABOVE the row (a reader must see why the
            # value is borrowed before reading it); `test_mcp_rename_table.py`
            # asserts this block exists and states its condition.
            note = row.get("note") or (
                f"pinned equal to {row['pinned_to']} (test-enforced)")
            for chunk in textwrap.wrap(note, 78) or [note]:
                lines.append(f"  # {chunk}")
            lines.append(f"  {name}: {float(row['value']):g}")
            continue
        line = f"  {name}: {float(row['value']):g}"
        if row.get("unmeasured"):
            line += f"  # unmeasured — {row.get('note', 'carried forward')}"
        lines.append(line)
    return "\n".join(lines) + "\n"


def split_durations_block(text: str) -> tuple[str, str, str]:
    """``(before_rows, header, after)`` for the manifest's ``durations:`` block.

    ``before_rows`` is everything up to and including the comment header that
    documents the map — the generator does NOT own that prose (the decisions it
    records cannot be re-derived) and preserves it byte-for-byte. ``after`` is
    any top-level key that follows ``durations`` (today: none).

    The header ends at the first row-shaped line, and an INDENTED comment
    (`  # …`, which ``render_rows`` emits above a pinned row) is row content,
    not header: `lstrip()` here would absorb a rendered pin comment into
    ``before_rows`` and then re-render it, duplicating the block on every
    `--write` and breaking idempotency.
    """
    lines = text.splitlines(keepends=True)
    start = None
    for i, line in enumerate(lines):
        if line.rstrip("\n") == "durations:":
            start = i
            break
    if start is None:
        raise ValueError("manifest has no top-level `durations:` block")
    # consume the unindented comment/blank header immediately after `durations:`
    j = start + 1
    while j < len(lines) and (lines[j].strip() == "" or lines[j].startswith("#")):
        j += 1
    before_rows = "".join(lines[:j])
    # rows run until the next top-level key
    k = j
    while k < len(lines) and not re.match(r"^[A-Za-z_][A-Za-z0-9_-]*:", lines[k]):
        k += 1
    after = "".join(lines[k:])
    return before_rows, "", after


def rewrite_manifest(manifest_path: Path, record: dict) -> None:
    """Replace the `durations:` rows with the record's rendering, keep prose."""
    text = manifest_path.read_text()
    before_rows, _header, after = split_durations_block(text)
    if after and not after.endswith("\n"):
        after += "\n"
    manifest_path.write_text(before_rows + render_rows(record) + after)


def write_record(record: dict, path: Path | None = None) -> None:
    path = path or RECORD
    path.write_text(json.dumps(record, indent=2, sort_keys=False) + "\n")


def load_record(path: Path | None = None) -> dict | None:
    path = path or RECORD
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return None


# ── validation (the contract) ────────────────────────────────────────────


def _manifest_durations(manifest: dict) -> dict:
    raw = manifest.get("durations")
    return raw if isinstance(raw, dict) else {}


def _differs(got, want) -> bool:
    """Total numeric inequality — never raises on a malformed value (#3407).

    An int beyond float range raises ``OverflowError`` on conversion, which a
    naive ``abs(float(got) - want)`` turns into a traceback inside the gate
    that exists to NAME the bad row. A non-finite value (``NaN``/``inf``) is a
    difference too: ``abs(nan - 1) > 1e-9`` is False, so an unguarded compare
    would read NaN as EQUAL and let a corrupted row silently back its value.
    Any non-comparable pair is a difference.
    """
    try:
        lhs, rhs = float(got), float(want)
    except (OverflowError, ValueError, TypeError):
        return True
    if not (math.isfinite(lhs) and math.isfinite(rhs)):
        return True
    return abs(lhs - rhs) > 1e-9


def value_issues(manifest: dict, record: dict | None = None) -> list[str]:
    """Wrong value / missing row / dead key / stale marker — #4783/#4766."""
    durations = _manifest_durations(manifest)
    if not durations:
        # A repo that has not adopted durations is not failed (documented
        # contract of duration_coverage_issues).
        return []
    record = record if record is not None else load_record()
    if record is None:
        return [
            "durations is populated but config/ci-durations-source.json is "
            "absent — a map with no measurement record cannot be value-checked "
            "(#4783). Run `tools/ci_manifest.py sweep --junit-dir … --write`"
        ]
    if not isinstance(record, dict):
        return [f"the measurement record is {type(record).__name__}, not an "
                f"object — the map cannot be value-checked (#4783)"]
    cs = _ci_selection()
    fast = set(cs.fast_pool(manifest))
    slow = set(manifest.get("slow_files", []))
    carve = cs.carve_out_files(manifest)
    allowed = fast | slow
    rows = record.get("rows")
    rows = rows if isinstance(rows, dict) else {}
    issues: list[str] = []
    if "rows" in record and not isinstance(record["rows"], dict):
        issues.append(
            f"the measurement record's `rows` is "
            f"{type(record['rows']).__name__}, not a mapping — the record "
            f"cannot back the map (#4783)")

    # 1. dead keys: a key that is neither packed nor a declared lane row.
    for name in sorted(durations):
        if name not in allowed:
            if name in carve:
                issues.append(
                    f"durations key {name} is a carve-out file — the carve-out "
                    f"job does not consume the map, so the row is dead (#4783)")
            else:
                issues.append(
                    f"durations key {name} is not a fast-pool or slow file — "
                    f"the packer drops it before packing (#4783)")
    # 2. strict presence: EVERY fast-pool file needs a row (the 90% ratio is a
    # backstop, not the invariant — one missing heavy row is a silent default).
    missing_rows = sorted(f for f in fast if f not in durations)
    if missing_rows:
        issues.append(
            f"{len(missing_rows)} fast-pool file(s) have no durations row and "
            f"would pack at the flat {PROVISIONAL_WEIGHT}s default — a new file "
            f"must be measured or explicitly marked `unmeasured` (#3400/#3463): "
            f"{missing_rows[:8]}")
    # 3. value check: the map is a strict projection of the record.
    for name in sorted(durations):
        row = rows.get(name)
        if not isinstance(row, dict):
            issues.append(
                f"durations key {name} has no row in the measurement record — "
                f"the record is the only author of a weight (#4766)")
            continue
        want = row.get("value")
        if want is None:
            issues.append(
                f"measurement record row {name} has no `value` — a row "
                f"without a value cannot back a weight (#4766)")
            continue
        got = durations[name]
        if not isinstance(got, (int, float)) or isinstance(got, bool):
            issues.append(f"durations value for {name} is not numeric: {got!r}")
        elif _differs(got, want):
            issues.append(
                f"durations value for {name} is {got} but the measurement "
                f"record says {want} — a hand-edited value is not a "
                f"measurement (#4783)")
    # 4. the record itself may not carry dead keys, and its `leg` must be the
    #    leg the current manifest derives — a mis-registered row otherwise
    #    lies about which leg carried the file until the next sweep.
    for name in sorted(rows):
        if name not in allowed:
            issues.append(
                f"measurement record row {name} is not a fast-pool or slow "
                f"file — the record must describe the packed universe")
            continue
        row = rows.get(name)
        if isinstance(row, dict):
            want_leg = carrying_leg(name, fast, slow, carve)
            if row.get("leg") != want_leg:
                issues.append(
                    f"measurement record row {name} says leg "
                    f"{row.get('leg')!r} but the manifest derives "
                    f"{want_leg!r} — the record must describe the packed "
                    f"universe")
    # 5. row self-consistency.
    for name, row in sorted(rows.items()):
        if not isinstance(row, dict):
            issues.append(
                f"measurement record row {name} is "
                f"{type(row).__name__}, not an object")
            continue
        samples = row.get("samples") or {}
        if not isinstance(samples, dict):
            issues.append(
                f"measurement record row {name} `samples` is "
                f"{type(samples).__name__}, not a mapping")
            continue
        if row.get("pinned_to"):
            # A pinned row's value is defined by its sibling, not its samples
            # (the equality is test-enforced).
            continue
        if row.get("unmeasured"):
            if samples and not row.get("retained"):
                issues.append(
                    f"record row {name} is marked unmeasured but carries "
                    f"{len(samples)} sample(s) — a stale marker hides a "
                    f"measurement (#4364). Re-run the sweep")
            continue
        if not samples:
            issues.append(
                f"record row {name} has no samples and no unmeasured marker — "
                f"the value is unbacked")
            continue
        try:
            want = _round(max(float(v) for v in samples.values()))
        except (OverflowError, ValueError, TypeError):
            issues.append(
                f"record row {name} carries a non-numeric sample — the record "
                f"must hold measurements only")
            continue
        if _differs(row.get("value"), want):
            issues.append(
                f"record row {name} value {row.get('value')} != max(samples) "
                f"{want}")
    # 6. pinned equality.
    pins = record.get("pins")
    if pins is None:
        pins = PINS
    if not isinstance(pins, dict):
        issues.append(
            f"measurement record `pins` is {type(pins).__name__}, not a "
            f"mapping")
        pins = {}
    for target, source in pins.items():
        if target in durations and source in durations and \
                _differs(durations[target], durations[source]):
            issues.append(
                f"durations {target}={durations[target]} breaks the "
                f"test-enforced equality with {source}={durations[source]}")
    return issues


def partition_issues(manifest: dict) -> list[str]:
    """Every classified file in EXACTLY one push leg — the #1266/#4528 invariant.

    Direct set arithmetic over the derivation, not a reading of the LPT pack's
    output: an assertion on an emergent property flips on an unrelated pool
    change (#4528/#4754). Slow carve-out files run in the carve-out job, so the
    slow leg is `slow - carve_out` (a file may be declared in both config lists,
    but it runs in exactly one leg).
    """
    cs = _ci_selection()
    slow = set(manifest.get("slow_files", []))
    carve = cs.carve_out_files(manifest)
    env_broken = set(cs.ENV_BROKEN_FILES)
    classified = set()
    for files in manifest.get("surfaces", {}).values():
        classified.update(files or ())
    classified.update(manifest.get("tier1", []) or [])
    classified.update(slow)
    legs = {
        "half_a": set(cs.push_legs(manifest)["half_a"]),
        "half_b": set(cs.push_legs(manifest)["half_b"]),
        "slow": slow - carve,
        "carve_out": carve,
        "env_broken": env_broken,
    }
    bare = {leg: {f if f.endswith(".py") else f + ".py" for f in files}
            for leg, files in legs.items()}
    issues: list[str] = []
    for name in sorted(classified):
        owners = [leg for leg, files in bare.items() if name in files]
        if not owners:
            issues.append(
                f"classified file {name} is in NO push leg — it runs nowhere "
                f"(the #1266 coverage hole, now a failure)")
        elif len(owners) > 1:
            issues.append(
                f"classified file {name} is in more than one leg "
                f"{owners} — a leg partition must be total and disjoint")
    # A leg may not carry a file the manifest does not classify. `push_extra`
    # is the documented exception: `push_legs()` appends those entries to the
    # halves BY DESIGN and `leg_coverage_issues()` requires them to stay
    # unclassified, so flagging them here would red `--integrity` on the shape
    # the repo's own guard tells you to create (#4615).
    extra = {f if f.endswith(".py") else f + ".py"
             for f in (manifest.get("push_extra") or [])}
    for name in sorted(set().union(*bare.values()) - extra):
        if name not in classified and name not in env_broken:
            issues.append(f"leg entry {name} is not classified in the manifest")
    return issues


def _tracked_files(repo: Path) -> set[str]:
    """The repo's tracked path set (``git ls-files``).

    Liveness must be decided against what CI checks out, not against whatever
    sits on a developer's disk: an untracked file satisfies ``Path.exists()``
    while failing in CI — the exact false-negative the existence check exists to
    avoid (#4171). Mirrors ``tests/test_ci_selection.py``'s
    ``test_source_patterns_all_name_something_real`` tracked-set semantics.
    """
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "ls-files"],
            capture_output=True, text=True, check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError(
            f"could not list tracked files in {repo} — the guard-reachability "
            f"existence checks cannot be attested: {exc}") from exc
    return {line for line in out.splitlines() if line}


def _names_something_real(pattern: str, tracked: set[str]) -> bool:
    """A SOURCE_PATTERNS entry names a tracked path or a tracked subtree.

    ``select()`` matches with ``startswith``, so a directory entry is live with
    or without its trailing slash. No glob branch: ``select()`` has no glob
    support, so a glob-shaped entry satisfied here would still select nothing.
    """
    return pattern in tracked or any(
        f.startswith(pattern.rstrip("/") + "/") for f in tracked)


def guard_reachability_issues(manifest: dict, repo: Path | None = None) -> list[str]:
    """A path a guard reads must SELECT the guard's surface (#4186/#3362/#4658).

    Two closed, derived classes plus the declared guard inputs:

    (a) every SOURCE_PATTERNS entry must name a real path/prefix — a dead
        entry is matched by ``startswith``, never against the filesystem, so it
        silently shrinks coverage (#4165/#4171);
    (b) a ``tools/<stem>.py`` that owns a registered ``tests/**/test_<stem>.py``
        guard must be selectable — a tool that selects no surface ships green
        with its own guard never running (#3362/#4115);
    (c) every DECLARED guard input must select the surface that owns the guard
        that reads it. Intent ("this doc is guarded") is not derivable (#2938),
        so it is declared — but the declaration is validated in both
        directions, so a rotten entry fails rather than silently un-guarding.
    """
    cs = _ci_selection()
    repo = repo or REPO
    tracked = _tracked_files(repo)
    issues: list[str] = []

    def selectable(path: str) -> bool:
        result = cs.select([path], "pull_request", manifest)
        return bool(result["full"] or result["surfaces"])

    # (a) SOURCE_PATTERNS reality check — against the TRACKED set, never the
    # working tree (an untracked file would green a dead entry locally).
    for surface, patterns in cs.SOURCE_PATTERNS.items():
        for pattern in patterns:
            if not _names_something_real(pattern, tracked):
                issues.append(
                    f"SOURCE_PATTERNS['{surface}'] entry {pattern} names no "
                    f"tracked file or subtree — a dead entry is still matched "
                    f"by `startswith`, so it silently shrinks coverage (#4165)")

    # (b) derived tool -> registered guard reachability.
    classified_tests: set[str] = set()
    for files in manifest.get("surfaces", {}).values():
        classified_tests.update(files or ())
    classified_tests.update(manifest.get("tier1", []) or [])
    for name in sorted(classified_tests):
        stem = Path(name).stem
        if not stem.startswith("test_"):
            continue
        guard_stem = stem[len("test_"):]
        for rel in (f"tools/{guard_stem}.py", f"tools/{guard_stem}.sh"):
            if rel in tracked and not selectable(rel):
                issues.append(
                    f"{rel} owns the registered guard {name} but selects NO "
                    f"CI surface — the guard never runs on the PR that edits "
                    f"it (#3362/#4115)")

    # (c) declared guard inputs.
    for surface, paths in (manifest.get("guard_inputs") or {}).items():
        for path in paths or ():
            if not _names_something_real(path, tracked):
                issues.append(
                    f"guard_inputs['{surface}'] entry {path} names no tracked "
                    f"file (dead declaration, #4186)")
                continue
            result = cs.select([path], "pull_request", manifest)
            if result["full"]:
                continue
            if surface not in result["surfaces"]:
                issues.append(
                    f"guard_inputs['{surface}'] entry {path} selects "
                    f"{result['surfaces']} — a PR editing the file this guard "
                    f"protects does not run the guard (#4658/#4186)")
    return issues


def check(manifest: dict, record: dict | None = None,
          repo: Path | None = None) -> list[str]:
    """The whole contract: value + partition + reachability."""
    return (value_issues(manifest, record)
            + partition_issues(manifest)
            + guard_reachability_issues(manifest, repo))


# ── bootstrap: atomic registration (#4348/#4364/#4817) ───────────────────


def register_provisional(names: list[str], manifest_path: Path | None = None,
                         record_path: Path | None = None) -> list[str]:
    """Give newly registered files an EXPLICIT unmeasured row.

    Registration is one step: the surface row (done by ci_selection) and a
    provisional, clearly-marked duration row. Without this the very next
    ``--integrity`` fails on the strict presence check and the only route a
    lane has is to invent a value — the bootstrap trap.
    """
    manifest_path = manifest_path or MANIFEST
    record_path = record_path or RECORD
    record = load_record(record_path)
    if record is None:
        return []
    cs = _ci_selection()
    manifest = cs._normalize_surfaces(
        __import__("yaml").safe_load(manifest_path.read_text()))
    fast = set(cs.fast_pool(manifest))
    slow = set(manifest.get("slow_files", []))
    carve = cs.carve_out_files(manifest)
    allowed = fast | slow
    added = []
    for name in names:
        if name not in allowed:
            continue
        row = record.setdefault("rows", {}).get(name)
        if row is None:
            record["rows"][name] = {
                "leg": carrying_leg(name, fast, slow, carve),
                "samples": {}, "value": PROVISIONAL_WEIGHT,
                "unmeasured": True,
                "note": "provisional — registered before CI could measure it "
                        "(#4348/#4364); the next sweep replaces this value",
            }
            added.append(name)
    if added:
        write_record(record, record_path)
        rewrite_manifest(manifest_path, record)
    return added


# ── report-only: derived guard-input candidates ──────────────────────────


def guard_candidates(repo: Path | None = None) -> dict[str, set[str]]:
    """Module-level path constants a guard reads but does not declare.

    Report-only (``guard-audit``). Intent is not derivable (#2938), so this is
    the input to a human's declaration — never a gate on its own.
    """
    import ast
    cs = _ci_selection()
    repo = repo or REPO
    roots = ("docs/", "website/", "tools/", "config/", ".github/",
             "graph-scripts/", "battery/", "validation/", "packs/",
             "services/", "integrations/", "supabase/")
    declared: set[str] = set()
    manifest = _ci_selection().load_manifest()
    for paths in (manifest.get("guard_inputs") or {}).values():
        declared.update(paths or ())
    holes: dict[str, set[str]] = {}

    def leaf_strings(node) -> list[str]:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return [node.value]
        if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
            out: list[str] = []
            for elt in node.elts:
                out.extend(leaf_strings(elt))
            return out
        return []

    for path in sorted((repo / "tests").rglob("test_*.py")):
        try:
            tree = ast.parse(path.read_text(errors="ignore"))
        except SyntaxError:
            continue
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            for value in leaf_strings(node.value):
                if not value.startswith(roots) or value in declared:
                    continue
                if not (repo / value).exists():
                    continue
                result = cs.select([value], "pull_request", manifest)
                if not result["full"] and not result["surfaces"]:
                    holes.setdefault(value, set()).add(
                        str(path.relative_to(repo)))
    return holes


# ── CLI ──────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("sweep", help="re-derive durations from junit artifacts")
    sp.add_argument("--junit-dir", action="append", default=[], required=True,
                    help="a run's artifact dir (repeatable)")
    sp.add_argument("--meta", action="append", default=[],
                    help="RUNID=HEAD,KIND (repeatable)")
    sp.add_argument("--manifest", default=str(MANIFEST))
    sp.add_argument("--record", default=str(RECORD))
    sp.add_argument("--write", action="store_true",
                    help="write the record and rewrite the manifest rows")

    cp = sub.add_parser("check", help="value-validate the committed manifest")
    cp.add_argument("--manifest", default=str(MANIFEST))
    cp.add_argument("--record", default=str(RECORD))

    sub.add_parser("guard-audit",
                   help="report-only: guarded paths a test reads but no "
                        "surface declares")

    args = ap.parse_args(argv)

    if args.cmd == "sweep":
        cs = _ci_selection()
        manifest_path = Path(args.manifest)
        record_path = Path(args.record)
        manifest = cs._normalize_surfaces(
            __import__("yaml").safe_load(manifest_path.read_text()))
        prior = load_record(record_path) or seed_from_manifest(manifest)
        sources = []
        for entry in args.junit_dir:
            d = Path(entry)
            run_id = d.name if d.name else str(d)
            sources.append((run_id, collect_run(d)))
        record = sweep(sources, manifest, prior,
                       _parse_source_meta(args.meta))
        unmeasured = sorted(n for n, r in record["rows"].items()
                            if r.get("unmeasured"))
        print(f"swept {len(sources)} source run(s) -> "
              f"{len(record['rows'])} rows; "
              f"{len(unmeasured)} unmeasured (carried forward)")
        if unmeasured:
            print("  unmeasured: " + ", ".join(unmeasured[:10])
                  + (" …" if len(unmeasured) > 10 else ""))
        if args.write:
            write_record(record, record_path)
            rewrite_manifest(manifest_path, record)
            print(f"wrote {record_path} and rewrote the `durations` rows of "
                  f"{manifest_path}")
        else:
            print(render_rows(record))
        return 0

    if args.cmd == "check":
        cs = _ci_selection()
        manifest = cs._normalize_surfaces(
            __import__("yaml").safe_load(Path(args.manifest).read_text()))
        problems = check(manifest, load_record(Path(args.record)))
        if problems:
            for problem in problems:
                print(f"❌ {problem}")
            return 1
        print("✅ manifest check: values match the record; legs partition; "
              "guards reachable")
        return 0

    # guard-audit
    holes = guard_candidates()
    if not holes:
        print("✅ no undeclared guarded paths")
        return 0
    print(f"{len(holes)} guarded path(s) a test reads but no surface declares "
          f"(report-only, #4186):")
    for path, who in sorted(holes.items()):
        print(f"  {path}  <- {', '.join(sorted(who))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
