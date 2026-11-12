#!/usr/bin/env python3
"""#11821 P0 — measure the journal's vector payload before and after the change.

WHAT THIS ANSWERS, and in whose terms. `tools/journal_growth.py` measures a
journal that exists on disk; the committed 29-type corpus
(`supabase/tests/fixtures/record_projection_journal.jsonl`) is a set of RECORDS,
not a journal. So this probe WRITES two journals from that corpus — one in the
pre-#11821 writer's form, one through the writer as it stands now — and runs the
committed instrument over each, so the delta is measured by the tool the
acceptance names rather than by arithmetic inside this file.

  * **before/** — the pre-#11821 writer, REPRODUCED: every vector element is the
    embedder's float32 value widened to a Python float (what
    `stamp_journal_embedding` did: `float(x)` of a numpy float32), serialised
    with `json.dumps(..., ensure_ascii=False)`'s default separators. That is
    byte-for-byte what the old writer appended, which is why it reproduces the
    published 9,176 B creating record.
  * **after/** — the writer AS IT IS: `stamp_journal_embedding` (float32 +
    `embedding_width`) and `EventLog.append` (compact separators).

Neither directory is committed: the probe prints the instrument's own report for
each, plus the per-class delta, and `measurement.txt` beside this file is one
run's output.

Usage
=====
    env -u TORTOISE_DB_URI uv run python \
        docs/evidence/11821-journal-encoding/probes/journal_encoding_probe.py

Exit: 0 measured · 2 usage.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    print("requires Python >= 3.12; run it with `uv run python`", file=sys.stderr)
    raise SystemExit(2)

import numpy as np  # noqa: E402

from tools.journal_growth import measure  # noqa: E402
from tortoise.embeddings import (  # noqa: E402
    EMBEDDING_DIM,
    EMBEDDING_MODEL,
    JOURNAL_VECTOR_WIDTH_KEY,
    stamp_journal_embedding,
)
from tortoise.log import EventLog  # noqa: E402

CORPUS = REPO / "supabase/tests/fixtures/record_projection_journal.jsonl"

#: The types a writer stamps a vector onto (see `stamp_journal_embedding`).
VECTOR_TYPES = ("PointAdded", "OperatorAdded",          # creating: + identity
                "PointPromoted", "OperatorPromoted")    # re-emitted snapshot


def unit_vector(width: int = EMBEDDING_DIM) -> list[float]:
    """A deterministic unit vector at the pinned embedder's width.

    float32, like the embedder's output — handed over as `float(x)`, which is
    what every producer does.
    """
    rng = np.random.default_rng(5612)
    vec = rng.standard_normal(width).astype(np.float32)
    return [float(x) for x in (vec / np.linalg.norm(vec)).astype(np.float32)]


def records() -> list[dict]:
    return [json.loads(line) for line in
            CORPUS.read_text(encoding="utf-8").splitlines() if line.strip()]


def richest_by_type(recs: list[dict]) -> dict[str, dict]:
    """The FULLEST record of each type — the shape production actually writes.

    Same rule as `docs/evidence/5612-journal-growth/probes/corpus_growth_probe.py`
    (`richest_by_type`), so the two measurements describe the same record shapes.
    """
    out: dict[str, dict] = {}
    for rec in recs:
        rtype = rec.get("type")
        if not isinstance(rtype, str) or not rtype:
            continue
        if rtype not in out or _line_len(rec, legacy=True) > _line_len(
                out[rtype], legacy=True):
            out[rtype] = rec
    return out


def _line_len(record: dict, *, legacy: bool) -> int:
    if legacy:
        return len(json.dumps(record, ensure_ascii=False).encode("utf-8")) + 1
    return len(json.dumps(record, ensure_ascii=False,
                          separators=(",", ":")).encode("utf-8")) + 1


def _fixed_width(vector: list[float]) -> list[float]:
    """The embedder's float32, widened to Python floats — the PRE-#11821 write.

    `stamp_journal_embedding` narrowed nothing, so what reached `json.dumps` was
    `float(numpy_float32)`, i.e. the float64 whose `repr` is 17 significant
    digits. Reproduced here so the baseline is the OLD writer's bytes, not an
    estimate.
    """
    return [float(np.float32(x)) for x in vector]


def before_record(record: dict, vec: list[float]) -> dict:
    """The record as the PRE-#11821 writer journalled it."""
    rec = json.loads(json.dumps(record))
    if rec.get("type") not in VECTOR_TYPES:
        return rec
    payload = rec.get("point")
    if not isinstance(payload, dict):
        return rec
    payload["embedding"] = _fixed_width(vec)
    rec["point"] = stamp_journal_embedding(
        payload, creating=rec.get("type") in ("PointAdded", "OperatorAdded"))
    # …and strip what this change added, so the baseline is the old shape.
    rec["point"].pop(JOURNAL_VECTOR_WIDTH_KEY, None)
    rec["point"]["embedding"] = _fixed_width(vec)
    return rec


def after_record(record: dict, vec: list[float]) -> dict:
    """The record as the writer AS IT IS now journals it."""
    rec = json.loads(json.dumps(record))
    if rec.get("type") not in VECTOR_TYPES:
        return rec
    payload = rec.get("point")
    if not isinstance(payload, dict):
        return rec
    payload["embedding"] = list(vec)
    rec["point"] = stamp_journal_embedding(
        payload, creating=rec.get("type") in ("PointAdded", "OperatorAdded"))
    return rec


def write_legacy_journal(path: Path, recs: list[dict]) -> None:
    """The pre-#11821 `EventLog.append`: spaced separators, one line per record."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes("".join(
        json.dumps(r, ensure_ascii=False) + "\n" for r in recs).encode("utf-8"))


def write_current_journal(path: Path, recs: list[dict]) -> None:
    """The writer AS IT IS: the real `EventLog.append` (compact + seal logic)."""
    log = EventLog(path)
    for rec in recs:
        log.append(rec)


def per_type(total: dict) -> dict[str, int]:
    return {name: row["bytes"] for name, row in total["by_type"].items()}


def _embedding_span(value, seps, depth: int = 0) -> int:
    """One record's `embedding` values, encoded in `seps` (see the caller)."""
    if isinstance(value, dict):
        total = 0
        for k, v in value.items():
            if k == "embedding":
                if v is None or (isinstance(v, (list, tuple, dict, str))
                                 and not v):
                    continue    # the forced-absent shape, not a vector
                total += len(json.dumps(v, ensure_ascii=False,
                                        separators=seps).encode("utf-8"))
            else:
                total += _embedding_span(v, seps, depth + 1)
        return total
    if isinstance(value, list):
        return sum(_embedding_span(v, seps, depth + 1) for v in value)
    return 0


def embedding_span_on_disk(directory: Path, *, compact: bool) -> int:
    """The `embedding` VALUE's byte span in a journal this probe wrote.

    ``tools/journal_growth.py`` cannot answer this: ``_embedding_bytes``
    attributes through ``_json_bytes``, which RE-SERIALISES each value with
    ``json.dumps``' DEFAULT separators (``", "`` / ``": "``) — an
    *attribution*, as its own docstring says, and therefore a SPACED figure
    whatever the journal on disk looks like. Before #11821 P0 the writer also
    spaced, so dividing it by the journal's on-disk bytes was one ruler; the
    compact writer puts the two on different rulers, and the quotient then
    describes neither (it reads the spacing as if it were vector text).

    So this walks the journal the probe just wrote and re-encodes each
    ``embedding`` value in the separators THAT journal was written with, which
    makes numerator and denominator the same ruler. It mirrors
    ``_embedding_bytes``' walk (any ``embedding`` key at any depth, and a
    ``None``/empty value is the forced-absent shape rather than a vector)
    instead of counting top-level keys: the vector rides inside
    ``record["point"]``.
    """
    seps = (",", ":") if compact else None
    total = 0
    for path in sorted(Path(directory).glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue
            total += _embedding_span(record, seps)
    return total


def main() -> int:
    recs = records()
    by_type = richest_by_type(recs)
    vec = unit_vector()
    print(f"corpus: {CORPUS.name} — {len(recs)} records, "
          f"{len(by_type)} event types (the per-class table uses ONE record per "
          "type: the FULLEST one, the shape production writes)")
    print(f"vector: {EMBEDDING_DIM}-d, {EMBEDDING_MODEL}; written through "
          "`tortoise.embeddings.stamp_journal_embedding` (the writer's own seam)")
    print()

    with tempfile.TemporaryDirectory() as tmp:
        before_dir = Path(tmp) / "before"
        after_dir = Path(tmp) / "after"
        class_before = Path(tmp) / "class-before"
        class_after = Path(tmp) / "class-after"
        write_legacy_journal(before_dir / "graph.jsonl",
                             [before_record(r, vec) for r in recs])
        write_current_journal(after_dir / "graph.jsonl",
                              [after_record(r, vec) for r in recs])
        ordered = sorted(by_type)
        write_legacy_journal(class_before / "graph.jsonl",
                             [before_record(by_type[t], vec) for t in ordered])
        write_current_journal(class_after / "graph.jsonl",
                              [after_record(by_type[t], vec) for t in ordered])
        before = measure(before_dir)["total"]
        after = measure(after_dir)["total"]
        # Same-ruler embedding spans, taken from the journals just written.
        before_span = embedding_span_on_disk(before_dir, compact=False)
        after_span = embedding_span_on_disk(after_dir, compact=True)
        cb = measure(class_before)["total"]
        ca = measure(class_after)["total"]

    print("== tools/journal_growth.py — the WHOLE 29-type corpus (39 records)")
    for label, total in (("before #11821 P0", before), ("after  #11821 P0", after)):
        row = total["record_bytes"]
        print(f"  {label}  {total['records']:>3} records {total['bytes']:>8,} B"
              f"  mean {row['mean']:>7.1f} B  p50 {row['p50']:>6,} B"
              f"  p95 {row['p95']:>6,} B  max {row['max']:>6,} B")
    # ⛔ TWO RULERS, and each percentage is taken on ONE of them. The tool's
    # `embedding_bytes` is a SPACED re-serialisation whatever the journal looks
    # like (`tools/journal_growth.py:_json_bytes`), so on the compact AFTER
    # journal it cannot be divided by `['bytes']` — that quotient (78.9% at the
    # #11821 P0 head) mixes a spaced numerator with a compact denominator and
    # is why this line reports the on-disk spans instead.
    print("  embedding share, numerator and denominator on ONE ruler each:")
    print(f"    before  {before_span:>6,} B of {before['bytes']:>6,} B = "
          f"{100.0 * before_span / before['bytes']:.1f}% (both spaced)")
    print(f"    after   {after_span:>6,} B of {after['bytes']:>6,} B = "
          f"{100.0 * after_span / after['bytes']:.1f}% (both compact)")
    print("    ⚠️ the tool's own attribution for the AFTER journal is "
          f"{after['embedding_bytes']:,} B — a SPACED re-serialisation, so it "
          "is NOT the numerator for the compact"
          f" {after['bytes']:,} B (that mix reads "
          f"{100.0 * after['embedding_bytes'] / after['bytes']:.1f}%).")
    print()

    print("== per class (bytes on disk, JSON line + terminator)")
    print(f"  {'record class':<18}{'before':>9}{'after':>9}{'saved':>9}{'ratio':>8}")
    b, a = per_type(cb), per_type(ca)
    for name in sorted(b):
        saved = b[name] - a.get(name, b[name])
        ratio = (b[name] / a[name]) if a.get(name) else 1.0
        print(f"  {name:<18}{b[name]:>9,}{a.get(name, b[name]):>9,}"
              f"{saved:>9,}{ratio:>7.2f}x")
    print()

    point_before = b.get("PointAdded", 0)
    point_after = a.get("PointAdded", 0)
    print("== the acceptance figure (a creating record, and its vector alone)")
    print(f"  PointAdded record      {point_before:,} B → {point_after:,} B "
          f"({point_before / point_after:.2f}x)")
    emb_before = len(json.dumps(
        before_record(by_type["PointAdded"], vec)["point"]["embedding"]))
    emb_after = len(json.dumps(
        after_record(by_type["PointAdded"], vec)["point"]["embedding"],
        separators=(",", ":")))
    print(f"  its vector payload     {emb_before:,} B → {emb_after:,} B "
          f"({emb_before / emb_after:.2f}x, "
          f"{emb_before / EMBEDDING_DIM:.1f} → {emb_after / EMBEDDING_DIM:.1f} chars/float)")
    print()
    print("The two halves of the delta are separately attributable:")
    print("  * float32 shortest text   — the vector term above, and the whole")
    print("    `embedding` field, not a separator count;")
    print("  * compact separators     — ~8% on EVERY record (the `PointRevised`/")
    print("    `EntityLinked` 1.08-1.09x rows above carry NO vector and move only by it).")
    print()
    print("VERDICT: P0's measured delta. It is NOT 'half the record' — a JSON")
    print("  number encoding cannot reach 50 % of 8,414 B at float32 precision")
    print("  (~4,689 B is the floor); the 4.1x step is P2's base64 (#11821).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
