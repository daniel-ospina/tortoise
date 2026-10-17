#!/usr/bin/env python3
"""#5612 — measure a journal's growth, so a bound can be CHOSEN from numbers.

The hosted lane writes one JSONL rebuild journal per graph under
``TORTOISE_EVENT_LOG_BASE_DIR`` (``tortoise/log.py::EventLog``). Nothing rotates,
compacts or expires it, and ``docs/architecture/STORAGE-ARCHITECTURE.md`` §5.1
says so in one line: *"No measurement of the journal's growth exists yet."*
This tool is that measurement — it does NOT choose or apply a bound.

What it answers, per file and in total:

* **bytes and records** actually on disk (the sum of per-record sizes equals the
  file size, so nothing is unaccounted for);
* **bytes per event type** — the ``PointAdded`` snapshot carrying a ``Point``'s
  embedding (#5004) is the term that dominates, and it is a different order of
  magnitude from a delta record such as ``PointRevised`` or ``EntityLinked``;
* **the embedding share** of the bytes, attributed by walking each record for an
  ``embedding`` key at any depth;
* **size percentiles** (p50/p95/p99/max) so a "typical" record is not a mean
  hiding a long tail of full-payload snapshots;
* **malformed lines, counted and classified, never raised.** The journal is the
  recovery tool, so measuring it must not depend on it being readable the way
  ``read_all`` demands (a mid-file malformed line makes ``read_all`` refuse by
  design — #3316). A journal that cannot be replayed can still be measured.

⚠️ **A measurement is not a bound.** The journal is the rebuild source
(``derived = replay(journal)``), so discarding a prefix whose state no other
artifact has absorbed converts an unbounded-growth cost problem into silent
state loss on the next ``rebuild_all``. That is a durability-posture decision
(the owner's), not this tool's.

Usage
=====
    # one journal file, or a directory of them (the base dir layout)
    python tools/journal_growth.py /data/event-log
    python tools/journal_growth.py /data/event-log/team_tortoise.jsonl --json

A directory is read the way ``rebuild_all`` reads one: its direct ``*.jsonl``
children, NOT recursive (``projection/__init__.py`` lists with
``os.listdir`` + ``endswith('.jsonl')``, so a nested file is not part of that
graph's journal and is not counted here either).

``--json`` emits a stable schema (``files``, ``total``, ``projection``); the text
form is for a human reading a volume.

Exit: 0 measured · 2 usage (nothing to measure).
"""
from __future__ import annotations

import sys

# #5128 shape: refuse a <3.12 interpreter before the imports below — a
# module-level 3.12+-only construct would otherwise fail first, and it would fail
# with a traceback instead of this message. The gate that enforces this shape is
# tests/test_entry_point_python_guard.py, not a style rule.
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    print(
        f"tools/journal_growth.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/journal_growth.py`",
        file=sys.stderr,
    )
    raise SystemExit(2)

import argparse
import json
import math
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

#: The seal annotation ``EventLog.append`` writes after a torn tail (#5917). It
#: is not a record: counting it would inflate the record count by one per tear.
#: Imported, never re-spelled — a second copy of the marker would drift and the
#: tool would start reporting seals as untagged records. ``tortoise.log``
#: imports only the stdlib, so this costs nothing.
from tortoise.log import SEAL_SENTINEL  # noqa: E402

#: What a record with no legible ``type`` is grouped under. The journal's own
#: envelope always carries one, so a record here is an operator edit or a
#: foreign writer — visible rather than silently excluded.
UNTAGGED = "<untagged>"


def journal_files(target: Path) -> list[Path]:
    """The journal files *target* names.

    A file measures itself. A directory measures its direct ``*.jsonl``
    children — the same enumeration ``rebuild_all`` uses, so this never
    counts a file the replay would not read.
    """
    if target.is_file():
        return [target]
    if target.is_dir():
        return sorted(p for p in target.iterdir()
                      if p.is_file() and p.name.endswith(".jsonl"))
    return []


def _percentile(sorted_sizes: list[int], pct: float) -> int:
    """Nearest-rank percentile over an already-sorted non-empty list."""
    if not sorted_sizes:
        return 0
    rank = max(1, math.ceil(pct / 100.0 * len(sorted_sizes)))
    return sorted_sizes[min(rank, len(sorted_sizes)) - 1]


def _json_bytes(value) -> int:
    """Serialized size of ONE value, for attributing bytes to a field.

    Re-serialized rather than sliced out of the raw line: a record is a JSON
    object, so the byte span of one key is not contiguous-addressable. The
    numbers are therefore *attribution*, not accounting — the per-record and
    total byte figures are the accounting, and they are exact (they are the
    bytes on disk).
    """
    try:
        return len(json.dumps(value, ensure_ascii=False).encode("utf-8"))
    except (TypeError, ValueError):
        return 0


def _embedding_bytes(value, depth: int = 0) -> int:
    """Bytes under any ``embedding`` key, at any depth, in one record.

    The vector rides INSIDE the writer's snapshot (``record["point"]
    ["embedding"]`` — see ``sdk._emit_event`` and ``stamp_journal_embedding``),
    so a top-level-only scan would report the embedding share as zero on
    exactly the records that carry it.
    """
    if isinstance(value, dict):
        total = 0
        for k, v in value.items():
            if k == "embedding":
                total += _json_bytes(v)
            else:
                total += _embedding_bytes(v, depth + 1)
        return total
    if isinstance(value, list):
        return sum(_embedding_bytes(v, depth + 1) for v in value)
    return 0


def _blank() -> dict:
    return {
        "records": 0,
        "bytes": 0,
        "malformed": 0,
        "seal_annotations": 0,
        "empty_lines": 0,
        "embedding_bytes": 0,
        "embedding_records": 0,
        "sizes": [],
        "by_type": {},
        "field_bytes": {},
    }


def measure_file(path: Path) -> dict:
    """Measure one journal file. Never raises on a journal it cannot parse."""
    data = path.read_bytes()
    agg = _blank()
    terminated = data.endswith(b"\n")
    pieces = data.split(b"\n")
    if terminated:
        pieces = pieces[:-1]
    last = len(pieces) - 1
    for idx, raw in enumerate(pieces):
        # The terminator belongs to the record it ends, so the per-record sizes
        # sum to the file size exactly (that equality is asserted in the tests).
        size = len(raw) + (0 if (idx == last and not terminated) else 1)
        stripped = raw.strip()
        if not stripped:
            agg["empty_lines"] += 1
            continue
        try:
            text = raw.decode("utf-8").strip()
        except UnicodeDecodeError:
            text = ""
        if text and text == SEAL_SENTINEL:
            agg["seal_annotations"] += 1
            continue
        try:
            record = json.loads(text)
        except ValueError:
            agg["malformed"] += 1
            continue
        if not isinstance(record, dict):
            agg["malformed"] += 1
            continue
        rtype = record.get("type")
        rtype = rtype if isinstance(rtype, str) and rtype else UNTAGGED
        agg["records"] += 1
        agg["bytes"] += size
        agg["sizes"].append(size)
        slot = agg["by_type"].setdefault(
            rtype, {"count": 0, "bytes": 0, "sizes": []})
        slot["count"] += 1
        slot["bytes"] += size
        slot["sizes"].append(size)
        for key, value in record.items():
            agg["field_bytes"][key] = (
                agg["field_bytes"].get(key, 0) + _json_bytes(value))
        emb = _embedding_bytes(record)
        if emb:
            agg["embedding_bytes"] += emb
            agg["embedding_records"] += 1
    return {"path": str(path), "_raw": agg,
            "file_bytes": len(data)}


def _finalize(raw: dict, file_bytes: int) -> dict:
    sizes = sorted(raw["sizes"])
    n = len(sizes)
    by_type = {}
    for name, slot in sorted(raw["by_type"].items(),
                             key=lambda kv: -kv[1]["bytes"]):
        tsizes = sorted(slot["sizes"])
        by_type[name] = {
            "count": slot["count"],
            "bytes": slot["bytes"],
            "mean": round(slot["bytes"] / slot["count"], 1),
            "p95": _percentile(tsizes, 95),
        }
    return {
        "records": n,
        "bytes": raw["bytes"],
        # Everything on disk that is NOT a well-formed record's bytes: torn
        # fragments, seal annotations, empty lines. `bytes + other_bytes` is
        # the file size exactly (asserted in the tests), so a journal's growth
        # can never include bytes this report did not account for.
        "other_bytes": file_bytes - raw["bytes"],
        "malformed": raw["malformed"],
        "seal_annotations": raw["seal_annotations"],
        "empty_lines": raw["empty_lines"],
        "embedding_bytes": raw["embedding_bytes"],
        "embedding_records": raw["embedding_records"],
        "record_bytes": {
            "min": sizes[0] if n else 0,
            "mean": round(raw["bytes"] / n, 1) if n else 0.0,
            "p50": _percentile(sizes, 50),
            "p95": _percentile(sizes, 95),
            "p99": _percentile(sizes, 99),
            "max": sizes[-1] if n else 0,
        },
        "by_type": by_type,
        "field_bytes": dict(sorted(raw["field_bytes"].items(),
                                   key=lambda kv: -kv[1])),
    }


def measure(target: str | Path, *, project_records: int = 100_000) -> dict:
    """Measure a journal file or directory and return the report dict."""
    p = Path(target)
    files = journal_files(p)
    if not files:
        raise FileNotFoundError(
            f"no journal to measure at {p} "
            f"(a file, or a directory holding *.jsonl)")
    per_file = []
    merged = _blank()
    for f in files:
        got = measure_file(f)
        raw = got["_raw"]
        per_file.append({
            "path": got["path"],
            "file_bytes": got["file_bytes"],
            **_finalize(raw, got["file_bytes"]),
        })
        merged["records"] += raw["records"]
        merged["bytes"] += raw["bytes"]
        merged["malformed"] += raw["malformed"]
        merged["seal_annotations"] += raw["seal_annotations"]
        merged["empty_lines"] += raw["empty_lines"]
        merged["embedding_bytes"] += raw["embedding_bytes"]
        merged["embedding_records"] += raw["embedding_records"]
        merged["sizes"].extend(raw["sizes"])
        for key, value in raw["field_bytes"].items():
            merged["field_bytes"][key] = merged["field_bytes"].get(key, 0) + value
        for name, slot in raw["by_type"].items():
            dst = merged["by_type"].setdefault(
                name, {"count": 0, "bytes": 0, "sizes": []})
            dst["count"] += slot["count"]
            dst["bytes"] += slot["bytes"]
            dst["sizes"].extend(slot["sizes"])
    total = _finalize(merged, sum(f["file_bytes"] for f in per_file))
    mean = total["record_bytes"]["mean"]
    return {
        "files": per_file,
        "total": total,
        "projection": {
            "records": project_records,
            "bytes": round(mean * project_records),
            "mb": round(mean * project_records / (1024 * 1024), 2),
        },
    }


def _share(part: int, whole: int) -> str:
    return f"{(100.0 * part / whole):5.1f}%" if whole else "    -"


def render(report: dict, *, top: int = 10) -> str:
    """The human form: one volume, no decoration."""
    total = report["total"]
    lines = [f"journal growth — {len(report['files'])} file(s)", ""]
    lines.append(f"{'file':<44} {'records':>9} {'bytes':>12} "
                 f"{'mean':>7} {'p95':>8} {'bad':>5}")
    for f in report["files"]:
        lines.append(
            f"{f['path'][-44:]:<44} {f['records']:>9} "
            f"{f['file_bytes']:>12,} {f['record_bytes']['mean']:>7.0f} "
            f"{f['record_bytes']['p95']:>8} {f['malformed']:>5}")
    rb = total["record_bytes"]
    lines.append(
        f"{'TOTAL':<44} {total['records']:>9} {total['bytes']:>12,} "
        f"{rb['mean']:>7.0f} {rb['p95']:>8} {total['malformed']:>5}")
    lines.append("")
    lines.append(f"record size bytes: p50 {rb['p50']} · p95 {rb['p95']} · "
                 f"p99 {rb['p99']} · max {rb['max']}")
    if total["embedding_records"]:
        lines.append(
            f"embedding: {total['embedding_records']} record(s) carry one, "
            f"{total['embedding_bytes']:,} B attributed "
            f"({_share(total['embedding_bytes'], total['bytes'])} of the "
            f"journal's bytes)")
    if total["seal_annotations"] or total["empty_lines"]:
        lines.append(f"not records: {total['seal_annotations']} seal "
                     f"annotation(s), {total['empty_lines']} empty line(s)")
    if total["other_bytes"]:
        lines.append(f"{total['other_bytes']:,} B on disk are not record bytes "
                     f"({total['malformed']} malformed line(s)); records + "
                     f"other = file size, exactly")
    by_type = list(total["by_type"].items())
    if by_type:
        lines.append("")
        lines.append(f"by event type (top {min(top, len(by_type))} of "
                     f"{len(by_type)}, by bytes)")
        lines.append(f"{'type':<32} {'count':>8} {'bytes':>12} "
                     f"{'share':>6} {'mean':>8} {'p95':>8}")
        for name, slot in by_type[:top]:
            lines.append(
                f"{name:<32} {slot['count']:>8} {slot['bytes']:>12,} "
                f"{_share(slot['bytes'], total['bytes']):>6} "
                f"{slot['mean']:>8.0f} {slot['p95']:>8}")
    fields = list(total["field_bytes"].items())
    if fields:
        lines.append("")
        lines.append(f"attributed bytes by top-level field (top "
                     f"{min(top, len(fields))})")
        for name, size in fields[:top]:
            lines.append(f"{name:<32} {size:>12,} "
                         f"{_share(size, total['bytes']):>6}")
    proj = report["projection"]
    lines.append("")
    lines.append(f"projection: {proj['records']:,} records ≈ "
                 f"{proj['bytes']:,} B ({proj['mb']} MB) at the measured "
                 f"mean of {rb['mean']:.0f} B/record")
    lines.append("")
    lines.append("a measurement, not a bound — the retention/rotation shape "
                 "is a durability-posture decision (#5612)")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Measure a hook-free JSONL rebuild journal's growth "
                    "(#5612). Does not bound, rotate or compact anything.")
    ap.add_argument("path", help="a journal .jsonl file, or a directory of them")
    ap.add_argument("--json", action="store_true",
                    help="emit the report as JSON instead of text")
    ap.add_argument("--top", type=int, default=10,
                    help="rows per breakdown table in the text form (default 10)")
    ap.add_argument("--project", type=int, default=100_000, dest="project",
                    help="record count for the growth projection "
                         "(default 100000)")
    args = ap.parse_args(argv)
    try:
        report = measure(args.path, project_records=args.project)
    except FileNotFoundError as exc:
        print(f"journal_growth: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=False))
    else:
        print(render(report, top=args.top))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
