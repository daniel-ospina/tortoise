#!/usr/bin/env python3
"""#5612 — what one journal record costs, measured on the real record shapes.

`STORAGE-ARCHITECTURE.md` §5.1 has said since 2026-09-23 that *"No measurement of
the journal's growth exists yet"*. This probe produces it, and nothing else: it
does not rotate, bound or choose a retention policy.

Method, stated so a reader can judge it
=======================================
The **record shapes** are real — they are the committed corpus
`supabase/tests/fixtures/record_projection_journal.jsonl`, which carries one
record of each event type the writers emit. The **vector** is real in the two
properties that decide its size: the width is the pinned embedder's
(`tortoise.embeddings.EMBEDDING_DIM`, derived here, never a literal) and it is
stamped through the SAME seam the writers use
(`tortoise.embeddings.stamp_journal_embedding`, called from
`TortoiseSDK._emit_event`), so the encoded form — the float reprs plus the
`embedding_model` / `embedding_revision` / `embedding_text_hash` identity keys —
is produced by production code, not re-implemented here. Its *values* are a
seeded unit vector: embeddings of the same width and range encode to the same
number of bytes, so nothing below depends on which text was encoded.

Only the record types that a writer actually stamps a vector onto are stamped:
`PointAdded` / `OperatorAdded` (creating — `stamp_journal_embedding(creating=
True)`) and `PointPromoted` / `OperatorPromoted` (a re-emitted snapshot, which
carries the node's vector without attesting an origin). `PointRevised` is left
as committed on purpose: `update_point` STRIPS a vector it derived from
`new_content` (#4208 — the replay re-encodes it), so a content revision is a
small delta, and only a CALLER-supplied vector rides the record.

What it measures is therefore ENCODING COST per record class — bytes on disk —
which is what the `/data` volume pays and what a bound would be chosen against.

Run:
    env -u TORTOISE_DB_URI TORTOISE_TEST_CARVE_OUT=1 \
        uv run python docs/evidence/5612-journal-growth/probes/corpus_growth_probe.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import numpy as np  # noqa: E402

from tools.journal_growth import measure  # noqa: E402
from tortoise.embeddings import (  # noqa: E402
    EMBEDDING_DIM,
    EMBEDDING_MODEL,
    stamp_journal_embedding,
)

CORPUS = REPO / "supabase/tests/fixtures/record_projection_journal.jsonl"
#: The pinned embedder's width, DERIVED — never a literal. `tortoise/embeddings.py`
#: says "Rotating the embedder = editing this line"; a literal here would keep
#: stamping 384 floats while printing the new model's name, understating the
#: vector-dominated record (~2.7x low for a 1024-d model) and publishing a figure
#: attributed to a model that does not produce it. Pinned by
#: `test_probe_vector_width_is_derived_from_the_embedder`.
VECTOR_WIDTH = EMBEDDING_DIM
#: The types a writer stamps a vector onto (see `stamp_journal_embedding`).
VECTOR_TYPES = ("PointAdded", "OperatorAdded",          # creating: + identity
                "PointPromoted", "OperatorPromoted")    # re-emitted snapshot


def unit_vector(width: int = VECTOR_WIDTH) -> list[float]:
    """A deterministic vector of the pinned embedder's width.

    float32, like the embedder's output — the writers hand ``float(x)`` of a
    float32 to ``stamp_journal_embedding``, and Python's float repr of that
    value is what lands on disk (that is the cost being measured).
    """
    rng = np.random.default_rng(5612)
    vec = rng.standard_normal(width).astype(np.float32)
    vec = (vec / np.linalg.norm(vec)).astype(np.float32)
    return [float(x) for x in vec]


def stamp(record: dict, vec: list[float]) -> dict:
    """Attach the vector to a snapshot record, through the writer's seam."""
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


def line_bytes(record: dict) -> int:
    """Bytes this record occupies in the journal (line + its terminator)."""
    return len(json.dumps(record, ensure_ascii=False).encode("utf-8")) + 1


def richest_by_type(records: list[dict]) -> dict[str, dict]:
    """The FULLEST record of each type — the shape production actually writes.

    The corpus holds 39 records for 29 types, so 10 types appear more than once
    and a plain dict comprehension keeps the LAST one. For `PointAdded` that is
    the MINIMAL `{id, content, pointKind, status, createdAt}` shape — ~300 B
    smaller than the production-shaped `PointAdded`, which also carries the
    `kind` / `contains_session` / `point.subtype` / `ep_alpha` fields. Taking
    the last one therefore published a create cost ~3.4% LOW, i.e. in the
    understating direction this measurement exists to avoid, and the reported
    "as committed" baseline was a record shape no writer emits.
    """
    out: dict[str, dict] = {}
    for rec in records:
        rtype = rec.get("type")
        if not isinstance(rtype, str) or not rtype:
            continue
        if rtype not in out or line_bytes(rec) > line_bytes(out[rtype]):
            out[rtype] = rec
    return out


def write_journal(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes("".join(
        json.dumps(r, ensure_ascii=False) + "\n" for r in records).encode("utf-8"))


def main() -> int:
    records = [json.loads(line) for line in
               CORPUS.read_text(encoding="utf-8").splitlines() if line.strip()]
    types = sorted({r.get("type") for r in records if r.get("type")})
    vec = unit_vector()
    stamped = [stamp(r, vec) for r in records]
    by_type = richest_by_type(records)
    stamped_by_type = {t: stamp(r, vec) for t, r in by_type.items()}

    print(f"corpus: {CORPUS.name} — {len(records)} records, "
          f"{len(types)} event types")
    print(f"vector: {VECTOR_WIDTH}-d, {EMBEDDING_MODEL}, stamped through "
          f"stamp_journal_embedding (the writer's own seam)")
    print()
    print("== per-operation cost (bytes on disk, JSON line + terminator)")
    for rtype, what in (("PointAdded", "create_point / capture turn"),
                        ("OperatorAdded", "add_operator"),
                        ("PointPromoted", "promote (re-emitted snapshot)"),
                        ("PointRevised", "content/metadata revision (delta)")):
        if rtype not in by_type:
            continue
        plain = line_bytes(by_type[rtype])
        vector = line_bytes(stamped_by_type[rtype])
        print(f"  {what:<40} {rtype:<16} "
              f"{vector:>9,} B" + ("" if vector == plain
                                   else f"  (as committed: {plain:,} B)"))
    emb = stamped_by_type["PointAdded"]["point"]["embedding"]
    print(f"  of which the vector's bytes:            "
          f"{'':<16} {len(json.dumps(emb).encode()):>9,} B "
          f"({len(json.dumps(emb)) / VECTOR_WIDTH:.1f} chars/float)")
    print("  NOTE: 'as committed' is the committed record UNCHANGED — the")
    print("  PointAdded one already carries a 2-element placeholder vector")
    print("  [0.1, 0.2] plus #5004's identity keys at the record's TOP LEVEL (the")
    print("  fixture's 'm1'/'r1'/'th1' stubs), so it is NOT a vectorless record:")
    print("  growth over it understates the vector's own cost. The +202 B in the")
    print("  decomposition is the POINT-payload copies the stamp inserts, where")
    print("  production writes them — not a second charge for the baseline keys.")
    print()

    with tempfile.TemporaryDirectory() as tmp:
        base_dir = Path(tmp) / "baseline"
        vec_dir = Path(tmp) / "hosted"
        write_journal(base_dir / "graph.jsonl", records)
        write_journal(vec_dir / "graph.jsonl", stamped)
        base = measure(base_dir, project_records=100_000)["total"]
        hosted = measure(vec_dir, project_records=100_000)["total"]

    print("== the 29-type corpus, measured WHOLE (all 39 committed records — "
          "a record-type mix, NOT an operation mix)")
    for label, total in (("as committed", base),
                         (f"with {VECTOR_WIDTH}-d vector", hosted)):
        print(f"  {label:<18} {total['records']:>3} records  "
              f"{total['bytes']:>8,} B  mean {total['record_bytes']['mean']:>7.1f} B"
              f"  p50 {total['record_bytes']['p50']:>6,} B"
              f"  p95 {total['record_bytes']['p95']:>6,} B"
              f"  max {total['record_bytes']['max']:>6,} B")
    print(f"  embedding share of the vector-bearing corpus: "
          f"{hosted['embedding_bytes']:,} / {hosted['bytes']:,} B "
          f"({100.0 * hosted['embedding_bytes'] / hosted['bytes']:.1f}%)")
    print()

    print("== growth per 1,000 operations")
    create = line_bytes(stamped_by_type["PointAdded"])
    revise = line_bytes(by_type["PointRevised"])
    link = line_bytes(by_type["EntityLinked"])
    print(f"  1,000 point creates (vector)      {create * 1000:>12,} B  "
          f"({create * 1000 / 1024 / 1024:>6.1f} MB)")
    print(f"  1,000 content/metadata revisions  {revise * 1000:>12,} B  "
          f"({revise * 1000 / 1024 / 1024:>6.1f} MB)")
    print(f"  1,000 entity links                {link * 1000:>12,} B  "
          f"({link * 1000 / 1024 / 1024:>6.1f} MB)")
    print(f"  1,000,000 point creates           {create * 1_000_000:>12,} B  "
          f"({create * 1_000_000 / 1024 / 1024 / 1024:>6.1f} GB)")
    print()
    print("VERDICT: a measurement, not a bound — the retention/rotation/"
          "checkpoint shape is an owner (durability-posture) decision (#5612)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
