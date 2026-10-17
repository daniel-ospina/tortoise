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
    # a live BASE dir — TORTOISE_EVENT_LOG_BASE_DIR, one journal per graph
    python tools/journal_growth.py /data/events
    # one graph's journal directory, or one journal file
    python tools/journal_growth.py /data/events/team_tortoise
    python tools/journal_growth.py /data/events/team_tortoise/events.jsonl --json

A directory is read in one of the two layouts the deployment writes — the
layout is chosen by the directory's own shape, never by scanning for both:

* a BASE dir — ``<base>/<graph>/events.jsonl``, one journal per graph
  (``config._resolve_event_log_path``: ``os.path.join(base, key,
  "events.jsonl")``; ``entrypoint.sh`` / ``docker-compose.yml`` point
  ``TORTOISE_EVENT_LOG_BASE_DIR`` at ``/data/events``) — taken when the dir has
  NO direct ``*.jsonl`` children, and
* a per-graph dir (or any dir of journals) — its direct ``*.jsonl`` children,
  the enumeration ``rebuild_all`` uses (``projection/__init__.py`` lists with
  ``os.listdir`` + ``endswith('.jsonl')``), NOT recursive: a nested
  ``archive/old.jsonl`` OR ``archive/events.jsonl`` is not part of any graph's
  journal and is not counted.

Both are needed because the base dir's direct children are graph
DIRECTORIES; listing only ``*.jsonl`` there found nothing and reported "no
journal to measure" on exactly the journal an operator is trying to size.
Each per-graph file is labelled with its graph key in the report. Scanning the
``<subdir>/events.jsonl`` level under a per-graph dir (the previous behaviour)
counted a retained/archived slice the replay never reads — an over-report, the
direction that argues for a tighter bound than the journal requires.

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

#: The seal annotation ``EventLog.append`` writes after a torn tail (#5917).
#: It is not a record: counting it would inflate the record count by one per
#: tear. The CLASSIFIER is imported, never re-spelled — a second copy of the
#: grammar would drift and the tool would start reporting seals (and torn seal
#: prefixes, which a crash leaves behind) as malformed records.
#: ``tortoise.log`` imports only the stdlib, so this costs nothing.
from tortoise.log import _is_seal_prefix  # noqa: E402

#: What a record with no legible ``type`` is grouped under. The journal's own
#: envelope always carries one, so a record here is an operator edit or a
#: foreign writer — visible rather than silently excluded.
UNTAGGED = "<untagged>"

#: The per-graph journal's file name (`config._resolve_event_log_path`).
JOURNAL_NAME = "events.jsonl"


def _is_seal(text: str) -> bool:
    """True for the seal annotation — complete OR TORN, ``tortoise.log``'s grammar.

    A crash can leave a PREFIX of the marker (``__Torn``) where the complete
    annotation was intended; ``read_all`` tolerates that and so must the
    measurer, or every tear is reported to the operator as a malformed record.
    The sentinel is deliberately not JSON, so no torn RECORD fragment can be a
    prefix of it.

    Applied to EVERY non-empty line here, not only to the successor of a torn
    fragment as `_is_seal_prefix`'s own docstring describes — recorded, because
    the two may look divergent and are not. `read_all` reaches the same verdict
    for the same bytes: a COMPLETE marker is skipped before any parse, and any
    line that fails to parse is handed to `_tolerate_or_raise`, whose FIRST arm
    is the same prefix test (the torn-seal-not-directly-after-its-fragment case
    it documents). No prefix of the sentinel is valid JSON, so the pre-parse
    placement here cannot reclassify a line `read_all` would call malformed.
    `test_seal_prefix_is_classified_as_the_reader_classifies_it` pins the
    agreement rather than leaving it to this paragraph.
    """
    return bool(text) and _is_seal_prefix(text)


def journal_files(target: Path) -> list[Path]:
    """The journal files *target* names, in both layouts the deployment writes.

    The two layouts are MUTUALLY EXCLUSIVE, and which one applies is decided by
    the target's own shape — never by scanning for both:

    * a **per-graph dir** (or any dir of journals) has direct ``*.jsonl``
      children, and that is exactly the enumeration ``rebuild_all`` uses
      (``os.listdir`` + ``endswith('.jsonl')``, NOT recursive). Only the direct
      children are returned; a nested ``<subdir>/events.jsonl`` (a retained or
      archived journal — the natural name when slicing one) is not part of any
      graph's journal and is NOT counted.
    * a **BASE dir** (``TORTOISE_EVENT_LOG_BASE_DIR``) has NO direct
      ``*.jsonl`` children — its children are graph DIRECTORIES — so the
      ``<graph>/events.jsonl`` one level down is the journal, and every graph is
      measured instead of reporting "no journal".

    Scanning both unconditionally (the previous behaviour) counted
    ``<dir>/<subdir>/events.jsonl`` under a per-graph dir, which the replay never
    reads: an over-estimate, and over-reporting is the direction that argues for
    a *tighter* bound than the journal requires — whose discarded prefix is
    state loss on the next ``rebuild_all``. Pinned by
    ``test_directory_enumeration_matches_rebuild_all``.
    """
    if target.is_file():
        return [target]
    if not target.is_dir():
        return []
    children = list(target.iterdir())
    direct = sorted(p for p in children
                    if p.is_file() and p.name.endswith(".jsonl"))
    if direct:                      # a per-graph dir (or a dir of journals)
        return direct
    return sorted(p / JOURNAL_NAME for p in children
                  if p.is_dir() and (p / JOURNAL_NAME).is_file())


def _graph_key(path: Path) -> str:
    """The graph a journal belongs to, or "" when the path is not a base-dir one."""
    return path.parent.name if path.name == JOURNAL_NAME else ""


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

    ⚠️ PRESENCE IS OWNERSHIP (#5004): ``sdk._emit_event`` forces the key present
    (``clean.setdefault("embedding", None)``) and ``_drop_journalled_embedding``
    sets it to ``None`` when there is NO usable vector — the keyword-only
    deployment always looks like that. A ``null`` value is 4 B of nothing, not a
    carried vector, so it must not be counted as one record bearing a vector.
    """
    if isinstance(value, dict):
        total = 0
        for k, v in value.items():
            if k == "embedding":
                if v is None or (isinstance(v, (list, tuple, dict, str))
                                 and not v):
                    continue    # the forced-absent shape, not a vector
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
        "other_bytes": 0,
        "malformed": 0,
        "seal_annotations": 0,
        "empty_lines": 0,
        "embedding_bytes": 0,
        "embedding_records": 0,
        "sizes": [],
        "by_type": {},
        "field_bytes": {},
    }


def _pieces(chunk: bytes) -> list[tuple[bytes, int]]:
    """Split ONE line-read chunk into ``(payload, terminator_bytes)`` pairs.

    Universal newlines, the same grammar ``EventLog.read_all`` normalises to
    (``\\r\\n`` and a bare ``\\r`` both separate records), but with each
    separator counted as the bytes it really occupies — so per-record sizes
    still sum to the file size on a CRLF or foreign-CR journal. Splitting on
    ``b"\\n"`` alone measured a bare-CR journal as ONE malformed line: a
    replayable journal reported as zero growth, which is the understating
    direction this tool must never take.
    """
    out: list[tuple[bytes, int]] = []
    start = 0
    i = 0
    end = len(chunk)
    while i < end:
        byte = chunk[i]
        if byte == 0x0D:                        # \r — CRLF is ONE separator
            width = 2 if (i + 1 < end and chunk[i + 1] == 0x0A) else 1
            out.append((chunk[start:i], width))
            i += width
            start = i
        elif byte == 0x0A:                      # \n
            out.append((chunk[start:i], 1))
            i += 1
            start = i
        else:
            i += 1
    if start < end:                             # only the final chunk can be torn
        out.append((chunk[start:], 0))
    return out


def measure_file(path: Path) -> dict:
    """Measure one journal file. Never raises on a journal it cannot parse.

    STREAMED, one line at a time: the journal this exists to size is unbounded
    by design (#5612 — ~8.5 GB per 1,000,000 creates), so a ``read_bytes()`` +
    ``split()`` pair would need ~2× that in RAM and would OOM on exactly the
    volume the measurement was landed for.
    """
    agg = _blank()
    file_bytes = 0
    with path.open("rb") as fh:
        for chunk in fh:        # the newline is INCLUDED, so len() is the size
            file_bytes += len(chunk)
            for raw, terminator in _pieces(chunk):
                # The terminator belongs to the record it ends, so the per-record
                # + junk sizes sum to the file size exactly. `other_bytes` is
                # ACCUMULATED here, never computed as a residual — a residual
                # makes that equality an identity that cannot fail.
                size = len(raw) + terminator
                stripped = raw.strip()
                if not stripped:
                    agg["empty_lines"] += 1
                    agg["other_bytes"] += size
                    continue
                try:
                    text = raw.decode("utf-8").strip()
                except UnicodeDecodeError:
                    text = ""
                if _is_seal(text):
                    agg["seal_annotations"] += 1
                    agg["other_bytes"] += size
                    continue
                try:
                    record = json.loads(text)
                except ValueError:
                    agg["malformed"] += 1
                    agg["other_bytes"] += size
                    continue
                if not isinstance(record, dict):
                    agg["malformed"] += 1
                    agg["other_bytes"] += size
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
            "file_bytes": file_bytes}


def _finalize(raw: dict) -> dict:
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
        # fragments, seal annotations, empty lines. ACCUMULATED from the lines
        # actually classified (`measure_file`), NOT computed as
        # `file_bytes - bytes`: as a residual, `bytes + other_bytes ==
        # file_bytes` would be true for ANY per-record arithmetic, including one
        # that drops a record — so the equality below would assert nothing. The
        # tests assert it against the bytes the test itself wrote.
        "other_bytes": raw["other_bytes"],
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
            f"no journal to measure at {p} (a *.jsonl file, a directory "
            f"holding *.jsonl, or a base dir holding <graph>/{JOURNAL_NAME})")
    per_file = []
    merged = _blank()
    for f in files:
        got = measure_file(f)
        raw = got["_raw"]
        per_file.append({
            "path": got["path"],
            "graph": _graph_key(Path(got["path"])),
            "file_bytes": got["file_bytes"],
            **_finalize(raw),
        })
        merged["records"] += raw["records"]
        merged["bytes"] += raw["bytes"]
        merged["other_bytes"] += raw["other_bytes"]
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
    file_bytes = sum(f["file_bytes"] for f in per_file)
    total = _finalize(merged)
    # Reported so the accounting is checkable from `--json` alone:
    # `bytes + other_bytes == file_bytes` is a real arithmetic check.
    total["file_bytes"] = file_bytes
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
    # The `bytes` column is the FILE bytes of each row, so the total must be the
    # sum of the column above it — not `total['bytes']`, which counts only
    # well-formed records and is therefore SMALLER than the rows it totals on
    # any journal carrying a torn tail, a seal or a blank line.
    file_total = sum(f["file_bytes"] for f in report["files"])
    lines.append(
        f"{'TOTAL':<44} {total['records']:>9} {file_total:>12,} "
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
