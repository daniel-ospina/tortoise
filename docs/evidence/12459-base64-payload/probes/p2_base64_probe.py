#!/usr/bin/env python3
"""#12459 P2 — measure the base64-float32 frame against the landed P0 journal.

WHAT THIS ANSWERS. The P0 design note's figures are already committed
(`docs/evidence/11821-journal-encoding/`); this probe measures the P2
PROJECTION on the same corpus, with the same instrument convention, so every
figure the note draws FROM THIS PROBE is reproducible by one command. ⚠️ Not every
number in the note: the P0 baseline has its own probe
(`docs/evidence/11821-journal-encoding/`), and the note's appendix carries five
numbered blocks (this probe, the P0 probe, the 9-digit round-trip script, the
raw-byte scan, the key-order scan) — each with its own command. §0's external
survey is not reproducible here at all, and the note says so.

It writes the P0 journal with the REAL writer (`EventLog.append` +
`stamp_journal_embedding`), then measures, per vector-bearing record:

  * the record's on-disk bytes as P0 writes it today;
  * the `embedding` value's on-disk span (same ruler as the record: compact);
  * the span the P2 frame WOULD take for the same vector, computed from the
    float32 BITS the P0 record already names — never from the JSON digits;
  * the record's bytes under P2 (arithmetic over two measured spans, stated).

and then the two claims the format must survive:

  * **DECODE EQUIVALENCE** — the float32 bits recovered from the base64 frame
    are identical to the float32 bits the P0 text names, for every element of
    every vector-bearing record in the corpus;
  * **THE f64 POPULATION IS NOT NARROWED** — a caller-owned / preserved vector
    (declared `f64`) encoded as base64 of packed float64 round-trips to the
    SAME float64 bits, so `#5004` round-7's raw-form rule is preserved by the
    encoding rather than overridden by it.

⚠️ THIS IS NOT AN IMPLEMENTATION. It encodes nothing into a PRODUCT journal and
changes no payload — but it is not writer-free either, and saying so is the
point: it writes a throwaway journal under `tempfile.mkdtemp()` with the
COMMITTED writer (`EventLog(...).append`), then re-reads that file, because a
baseline measured off the real writer's bytes is the only baseline that answers
"what would this cost". ⚠️ Precisely: every **P0** figure it prints is measured on
that tempdir copy (the corpus line, the `journal_growth` report, and the per-class
P0 record/vector spans, `raw_decode`d out of the written lines). The P2 columns,
the six refusals, the default-decoder arm, the truncation sweep, the six-alphabet
and degenerate-shape tables and the `f64` fixtures are computed **in memory** from
that journal's vector and from synthetic fixtures — they are the framed candidate,
and the tables label them as such. `tools/journal_growth.py` cannot answer the vector questions (it
re-serialises with SPACED separators — its own docstring calls the output
attribution; see #11886), so the P0 vector spans here are `raw_decode`d out of the
journal this probe actually wrote, on ONE ruler, and the compact re-serialisation is
kept only as an ASSERTION that the two rulers agree.

Usage
=====
    env -u TORTOISE_DB_URI uv run python \
        docs/evidence/12459-base64-payload/probes/p2_base64_probe.py

Exit: 0 measured · 1 a claim was FALSIFIED (a control or an assertion failed — every
guard prints what it falsified before returning) · 2 the interpreter is older than
3.12, so the probe refused to run at all.
"""
from __future__ import annotations

import base64
import json
import os
import struct
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
    JOURNAL_VECTOR_WIDTH_F32,
    JOURNAL_VECTOR_WIDTH_KEY,
    shortest_float32_vector,
    stamp_journal_embedding,
)
from tortoise.log import EventLog  # noqa: E402

CORPUS = REPO / "supabase/tests/fixtures/record_projection_journal.jsonl"

#: The types a writer stamps a vector onto (see `stamp_journal_embedding`).
VECTOR_TYPES = ("PointAdded", "OperatorAdded",
                "PointPromoted", "OperatorPromoted")

#: The frame under test. `b64` is the in-band version marker: one token that
#: cannot be a legitimate `embedding` value (the seam drops every string,
#: `_drop_journalled_embedding`), a version number so a future frame is
#: distinguishable, the declared width (checked against `embedding_width`), the
#: element count (so a short decode is refused, not silently short), then the
#: standard base64 alphabet.
FRAME_PREFIX = "b64"
FRAME_VERSION = 1


def frame(width: str, element_bytes: int, vector: list[float]) -> str:
    """The P2 encoding of *vector* at *width* — a string, plus its framing."""
    if width == JOURNAL_VECTOR_WIDTH_F32:
        packed = struct.pack(f"<{len(vector)}f", *vector)
    else:
        packed = struct.pack(f"<{len(vector)}d", *vector)
    assert len(packed) == element_bytes * len(vector)
    body = base64.b64encode(packed).decode("ascii")
    return f"{FRAME_PREFIX}:{FRAME_VERSION}:{width}:{len(vector)}:{body}"


def unframe(text: str) -> list[float]:
    """Decode a P2 frame back to Python floats (the reader being designed)."""
    marker, version, width, count, body = text.split(":", 4)
    if marker != FRAME_PREFIX:
        raise ValueError("not a base64 frame")
    if int(version) != FRAME_VERSION:
        raise ValueError(f"unknown frame version {version}")
    n = int(count)
    element_bytes = 4 if width == JOURNAL_VECTOR_WIDTH_F32 else 8
    # `validate=True`: the default (False) SILENTLY DISCARDS characters outside
    # the alphabet, so a corrupted body decodes to a SHORTER array without an
    # error — the fail-open direction, on the durability artifact.
    packed = base64.b64decode(body, validate=True)
    if len(packed) != n * element_bytes:
        raise ValueError(
            f"frame declares {n} elements at {width} ({n * element_bytes} B), "
            f"decoded {len(packed)} B")
    code = "f" if width == JOURNAL_VECTOR_WIDTH_F32 else "d"
    return list(struct.unpack(f"<{n}{code}", packed))


def unit_vector(width: int = EMBEDDING_DIM) -> list[float]:
    """A deterministic unit vector at the pinned embedder's width (as P0's probe)."""
    rng = np.random.default_rng(5612)
    vec = rng.standard_normal(width).astype(np.float32)
    return [float(x) for x in (vec / np.linalg.norm(vec)).astype(np.float32)]


def records() -> list[dict]:
    return [json.loads(line) for line in
            CORPUS.read_text(encoding="utf-8").splitlines() if line.strip()]


def richest_by_type(recs: list[dict]) -> dict[str, dict]:
    """The FULLEST record of each type — same rule as both prior probes."""
    out: dict[str, dict] = {}
    for rec in recs:
        rtype = rec.get("type")
        if not isinstance(rtype, str) or not rtype:
            continue
        if rtype not in out or _line_len(rec, spaced=True) > _line_len(
                out[rtype], spaced=True):
            out[rtype] = rec
    return out


def _line_len(record: dict, *, spaced: bool) -> int:
    seps = None if spaced else (",", ":")
    return len(json.dumps(record, ensure_ascii=False,
                          separators=seps).encode("utf-8")) + 1


def p0_records(vec: list[float]) -> list[dict]:
    """The corpus as the writer AS IT IS journals it (P0 landed)."""
    out = []
    for record in records():
        rec = json.loads(json.dumps(record))
        if rec.get("type") in VECTOR_TYPES:
            payload = rec.get("point")
            if isinstance(payload, dict):
                payload["embedding"] = list(vec)
                rec["point"] = stamp_journal_embedding(
                    payload, creating=rec.get("type") in
                    ("PointAdded", "OperatorAdded"))
        out.append(rec)
    return out


def _embedding_span(record: dict, *, seps) -> int:
    """The on-disk `embedding` span in *record*, re-encoded with *seps*."""
    total = 0

    def walk(value) -> None:
        nonlocal total
        if isinstance(value, dict):
            for k, v in value.items():
                if k == "embedding":
                    if v is None or (isinstance(v, (list, tuple, str)) and not v):
                        continue
                    total += len(json.dumps(v, ensure_ascii=False,
                                            separators=seps).encode("utf-8"))
                else:
                    walk(v)
        elif isinstance(value, list):
            for v in value:
                walk(v)

    walk(record)
    return total


def _embedding_vector(record: dict) -> list[float] | None:
    found = None

    def walk(value) -> None:
        nonlocal found
        if isinstance(value, dict):
            for k, v in value.items():
                if k == "embedding" and isinstance(v, list) and v:
                    found = v
                else:
                    walk(v)
        elif isinstance(value, list):
            for v in value:
                walk(v)

    walk(record)
    return found


def _replace_embedding(record: dict, text: str) -> dict:
    rec = json.loads(json.dumps(record))

    def walk(value) -> None:
        if isinstance(value, dict):
            for k, v in value.items():
                if k == "embedding" and isinstance(v, list) and v:
                    value[k] = text
                else:
                    walk(v)
        elif isinstance(value, list):
            for v in value:
                walk(v)

    walk(rec)
    return rec


def on_disk_embedding_span(line: str) -> int | None:
    """The `embedding` VALUE's raw byte span in *line*, taken from the written bytes.

    `raw_decode` starts at the byte after the key and returns the end of the value, so
    the span is exactly what the writer emitted — not a re-serialisation. This matters
    for this note in particular: #11886's whole point is that the same payload has two
    spans depending on the SEPARATORS, so a probe that prints "bytes on disk" while
    computing `json.dumps(...)` is using the weaker ruler and claiming the stronger.
    """
    key = '"embedding":'
    idx = line.find(key)
    if idx < 0:
        return None
    start = idx + len(key)
    while start < len(line) and line[start] == " ":
        start += 1
    _, end = json.JSONDecoder().raw_decode(line, start)
    return len(line[start:end].encode("utf-8"))


def as_f32_bits(values: list[float]) -> bytes:
    return struct.pack(f"<{len(values)}f", *values)


def as_f64_bits(values: list[float]) -> bytes:
    return struct.pack(f"<{len(values)}d", *values)


def main() -> int:
    # a report, read top to bottom — the length is the deliverable, not a smell
    vec = unit_vector()
    recs = p0_records(vec)
    by_type = richest_by_type(recs)

    print("== corpus")
    print(f"  {CORPUS.relative_to(REPO)}: {len(records())} records, "
          f"{len(by_type)} types")
    print(f"  vector: {len(vec)}-d, float32, produced by "
          f"{EMBEDDING_DIM}-wide generator (same vector as the P0 probe)")

    # ── the P0 journal, written by the real writer ──────────────────────
    tmp = Path(tempfile.mkdtemp(prefix="p2_measure_"))
    p0_dir = tmp / "p0"
    p0_dir.mkdir(parents=True, exist_ok=True)
    for rec in recs:
        EventLog(p0_dir / "events.jsonl").append(rec)
    p0_report = measure(p0_dir)

    print("\n== tools/journal_growth.py on the P0 journal (the committed instrument)")
    rb = p0_report["total"]["record_bytes"]
    print(f"  {p0_report['total']['records']} records  "
          f"{p0_report['total']['bytes']:,} B  "
          f"mean {rb['mean']:,.1f} B  "
          f"p95 {rb['p95']:,} B  max {rb['max']:,} B")
    print("  ⚠️ the tool's `embedding` share is a SPACED re-serialisation "
          "(#11886) — not used for any vector figure below.")

    # ── per vector-bearing record, on ONE ruler ─────────────────────────
    # The P0 columns are the WRITTEN bytes: `p0_lines[i]` is the line `EventLog.append`
    # produced for `recs[i]`, and the vector span is `raw_decode`d out of that line. The
    # compact re-serialisation is still computed, but only as an ASSERTION that the two
    # rulers agree — a probe that prints "bytes on disk" and computes `json.dumps` is
    # the #11886 defect this note exists to name.
    p0_lines = (p0_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(p0_lines) == len(recs), (len(p0_lines), len(recs))
    pos = {id(rec): i for i, rec in enumerate(recs)}
    disk_checked = 0
    print("\n== per class, vector-bearing records "
          "(P0 bytes ON DISK; P2 is the framed candidate, re-serialised)")
    print("   class                 P0 rec   P0 vector   P2 vector   P2 rec   rec ratio")
    rows = []
    for rtype in VECTOR_TYPES:
        rec = by_type.get(rtype)
        if rec is None:
            continue
        p0_line = p0_lines[pos[id(rec)]]
        # +1 for the `\n` the writer terminates each record with — `_line_len` counts it,
        # and the on-disk line as READ back has it stripped.
        p0_rec_bytes = len(p0_line.encode("utf-8")) + 1
        span = on_disk_embedding_span(p0_line)
        assert span is not None, rtype
        assert p0_rec_bytes == _line_len(rec, spaced=False), (rtype, p0_rec_bytes)
        assert span == _embedding_span(rec, seps=(",", ":")), (rtype, span)
        disk_checked += 1
        vector = _embedding_vector(rec)
        width = rec.get("point", {}).get(JOURNAL_VECTOR_WIDTH_KEY)
        assert width == JOURNAL_VECTOR_WIDTH_F32, (rtype, width)
        text = frame(JOURNAL_VECTOR_WIDTH_F32, 4, vector)
        p2_span = len(json.dumps(text, ensure_ascii=False).encode("utf-8"))
        p2_rec = _replace_embedding(rec, text)
        p2_rec_bytes = _line_len(p2_rec, spaced=False)
        rows.append((rtype, p0_rec_bytes, span, p2_span, p2_rec_bytes))
        print(f"   {rtype:<20} {p0_rec_bytes:>7,}  {span:>9,}  "
              f"{p2_span:>9,}  {p2_rec_bytes:>7,}   "
              f"{p0_rec_bytes / p2_rec_bytes:.2f}x")

    print("\n== a creating record (PointAdded), the acceptance surface")
    name, p0_rec_bytes, span, p2_span, p2_rec_bytes = rows[0]
    print(f"  record              {p0_rec_bytes:,} B → {p2_rec_bytes:,} B  "
          f"({p0_rec_bytes / p2_rec_bytes:.2f}x)")
    print(f"  its vector payload  {span:,} B → {p2_span:,} B  "
          f"({span / p2_span:.2f}x)")
    print(f"  chars/float         {span / len(vec):.1f} → {p2_span / len(vec):.1f}")
    print(f"  raw float32 packed  {len(vec) * 4:,} B  "
          f"(base64 is +{100 * (p2_span - 2 - 14 - len(vec) * 4) / (len(vec) * 4):.1f} % "
          f"framing over raw: {p2_span - 2 - 14:,} B string − "
          f"{len(vec) * 4:,} B raw = {p2_span - 2 - 14 - len(vec) * 4:,} B)")
    print(f"  pre-#11821 baseline  9,176 B → {p2_rec_bytes:,} B  "
          f"({9176 / p2_rec_bytes:.2f}x)")

    print(f"  ⇒ the P0 figures above are the WRITTEN bytes ({disk_checked} lines "
          f"`raw_decode`d out of the tempdir journal, vector span included); the "
          f"writer's compact separators reproduce each line exactly")

    # ── the whole corpus ────────────────────────────────────────────────
    total_p0 = sum(len(ln.encode("utf-8")) + 1 for ln in p0_lines)
    p2_all = 0
    for rec in recs:
        vector = _embedding_vector(rec)
        width = rec.get("point", {}).get(JOURNAL_VECTOR_WIDTH_KEY) \
            if isinstance(rec.get("point"), dict) else None
        if vector and width == JOURNAL_VECTOR_WIDTH_F32:
            p2_all += _line_len(
                _replace_embedding(rec, frame("f32", 4, vector)), spaced=False)
        else:
            p2_all += _line_len(rec, spaced=False)
    print("\n== whole corpus")
    print(f"  P0  {len(recs)} records  {total_p0:,} B")
    print(f"  P2  {len(recs)} records  {p2_all:,} B  "
          f"({total_p0 / p2_all:.2f}x vs P0, {51346 / p2_all:.2f}x vs pre-P0)")
    print(f"  delta {total_p0 - p2_all:,} B — all of it in the "
          f"{len([r for r in recs if _embedding_vector(r)])} vector-bearing records")

    # ── decode equivalence: the float32 bits must not move ──────────────
    print("\n== decode equivalence (float32 bits, not float64 values)")
    # EVERY vector-bearing record, not one per type: `richest_by_type` collapses
    # a repeated type, and the corpus holds two PointAdded records.
    equiv = 0
    for rec in recs:
        vector = _embedding_vector(rec)
        width = rec.get("point", {}).get(JOURNAL_VECTOR_WIDTH_KEY) \
            if isinstance(rec.get("point"), dict) else None
        if not vector or width != JOURNAL_VECTOR_WIDTH_F32:
            continue
        # ⛔ TWO DISTINCT links computed against something OUTSIDE the frame (both
        # facing the producer); frame-invertibility is their transitive consequence
        # and is asserted for naming only — it compares the decode to the frame's own
        # INPUT, so it can add no discriminating power. Saying "three links, each
        # outside the frame" was false for the third, in this comment, long after the
        # note was corrected. An earlier version compared `pack_f32(narrow(x)) == pack_f32(x)`,
        # which is an IDENTITY — packing as f32 rounds whether or not you round
        # first — so it held for 384 random floats with no journal at all and the
        # assertion built on it could never fail. The link that matters is against
        # `vec`, the PRODUCER's vector, which is what the graph will store.
        text = frame("f32", 4, vector)
        decoded = unframe(text)
        producer = [float(x) for x in vec]
        b64_vs_producer = as_f32_bits(decoded) == as_f32_bits(producer)
        p0text_vs_producer = as_f32_bits(vector) == as_f32_bits(producer)
        invertible = as_f32_bits(decoded) == as_f32_bits(vector)
        print(f"  {rec.get('type', '?'):<20} {len(vector):>4}-d  "
              f"base64→f32 == producer f32: {b64_vs_producer}"
              f"   P0 text→f32 == producer f32: {p0text_vs_producer}"
              f"   (frame invertible: {invertible})")
        assert b64_vs_producer and p0text_vs_producer and invertible, rec.get("type")
        # Kept so the float64-VALUE arms below run on a REAL verified vector: the
        # loop variable `vector` is None once the last record carrying no vector is
        # reached, and a control that runs on `None` is a control that cannot run.
        verified_vector = vector
        equiv += 1
    print(f"  {equiv} of {len(recs)} corpus records carry a vector and were verified")

    # ⛔ Why the assertion is on BITS, not on float64 VALUES — BOTH arms measured
    # here, because the first draft of this block computed `float(np.float64(v)) == v`
    # over the PRODUCER alone: `decoded` never appeared, so a line LABELLED
    # "decoded==producer" was a widening identity (`f32 ⊂ f64` by definition) that no
    # input can falsify, and the guard under it could never fire. That is the same
    # class as the tautological control an earlier review round of this note had to
    # fix (its round 14), in the control itself.
    # The two arms say different things, and the direction is the point:
    #   (a) against the PRODUCER the value test PASSES (384/384) — it cannot
    #       discriminate, because a faithful `f32` pack and any lossless re-encode of
    #       the same values satisfy it: an `f32` value IS exactly a `float64`, so the
    #       comparison carries no width at all. Vacuous as a DISCRIMINATOR.
    #   (b) against P0's UN-NARROWED decimal text the same test REJECTS a correct
    #       encode (0/384) — the text holds the shortest round-tripping decimal
    #       (`0.0012301534`) while the decode holds the `f32` value, so the truth side
    #       must be narrowed to the stored width (#8615) or a faithful encode reads
    #       as a failure. A false RED, which is why the same test is not "lenient":
    #       it is simply measuring the wrong thing from either side.
    producer = [float(x) for x in vec]
    decoded_last = unframe(frame("f32", 4, verified_vector))
    n_producer = sum(1 for a, b in zip(decoded_last, producer, strict=True) if a == b)
    n_p0_text = sum(
        1 for a, b in zip(decoded_last, verified_vector, strict=True) if a == b)
    f64_value_vs_producer = n_producer == len(producer)
    f64_value_vs_p0_text = n_p0_text == len(verified_vector)
    # COUNTED, not a boolean the note then re-derives a count from: the note quotes
    # "384 of 384" and "0 of 384", and a whole-list `==` returning False proves only
    # that SOME element differs. A figure with no line behind it is the defect this
    # probe has been reviewed for twice.
    print("  float64-VALUE decoded == producer (must PASS — value equality cannot "
          f"see width): {f64_value_vs_producer}  elements equal: "
          f"{n_producer:,} of {len(producer):,}")
    print("  float64-VALUE decoded == P0 un-narrowed text (must REJECT a correct "
          f"encode): {f64_value_vs_p0_text}  elements equal: "
          f"{n_p0_text:,} of {len(verified_vector):,}")
    # …and the bits assertion CAN fail — a negative control, so it is not vacuous.
    # Both are ASSERTED: a control that is only printed reports a vacuous control
    # as a pass (that is how round 14's tautology survived). The expectations are
    # inverted on purpose — the opposite of what is printed means the control is
    # broken, not that the note is right.
    perturbed = list(producer)
    perturbed[0] = float(np.nextafter(np.float32(perturbed[0]), np.float32(1e9)))
    control_flips = as_f32_bits(perturbed) == as_f32_bits(producer)
    print("  the same assertion on a 1-ULP perturbation (must be False):",
          control_flips)
    if not f64_value_vs_producer:
        print("  ⛔ the float64-VALUE arm no longer passes against the producer — "
              "the note's claim (a) is false on this vector and must be re-stated")
        return 1
    if f64_value_vs_p0_text:
        print("  ⛔ the float64-VALUE arm now AGREES with P0's un-narrowed text — "
              "the note's claim (b) (a false red) is false on this vector")
        return 1
    if control_flips:
        print("  ⛔ the 1-ULP negative control did not flip — the bits "
              "assertion is vacuous and the control is broken")
        return 1

    # ── the f64 population: NOT narrowed ────────────────────────────────
    print("\n== the caller-owned / preserved population (#5004 round-7)")
    # Off the float32 grid on purpose, like the P0 test fixture: a value that
    # a narrowing would visibly change.
    verbatim = [0.1 + i * 1e-9 for i in range(EMBEDDING_DIM)]
    f64_text = frame("f64", 8, verbatim)
    assert as_f64_bits(unframe(f64_text)) == as_f64_bits(verbatim)
    narrowed_f64_bits = as_f64_bits([float(np.float32(v)) for v in verbatim])
    off_grid = narrowed_f64_bits != as_f64_bits(verbatim)
    print(f"  float64 frame round-trip bit-identical: "
          f"{as_f64_bits(unframe(f64_text)) == as_f64_bits(verbatim)}")
    print(f"  …and it is genuinely off the float32 grid: "
          f"{off_grid}  (so a narrowing WOULD be visible)")
    assert off_grid, "the fixture must not be on the float32 grid"
    def f64_spans(label: str, values: list[float]) -> None:
        text = json.dumps(values, separators=(",", ":")).encode()
        frame_text = json.dumps(frame("f64", 8, values)).encode()
        assert as_f64_bits(unframe(frame("f64", 8, values))) == as_f64_bits(values)
        print(f"  f64 spans ({label}): today {len(text):,} B "
              f"({len(text) / len(values):.1f} chars/float) → frame "
              f"{len(frame_text):,} B ({len(frame_text) / len(values):.2f}) "
              f"= {len(text) / len(frame_text):.2f}x")

    f64_spans("the off-grid fixture", verbatim)
    # The magnitude is a property of the TEXT, not of the encoding: a realistic
    # full-precision float64 vector prints ~19.7 chars/float, not ~15.
    f64_spans("a realistic full-precision float64 vector",
              [float(x) for x in np.random.default_rng(12459).standard_normal(EMBEDDING_DIM)])

    # ── the sign of the trade is a property of the VECTOR ───────────────
    # The frame costs a flat 5.375 chars/float (f32) / 10.71 (f64) — both the FULL
    # FRAMED span (2,064/384 and 4,112/384), not the bare base64 5.33 (2,048/384),
    # so it is a
    # FLOOR the JSON text can fall below. Measured on degenerate shapes:
    print("\n== degenerate vectors: the trade's SIGN depends on the vector "
          "(ratio = text/frame; <1 means the frame is LARGER)")
    print("   shape                 f32: text     frame    ratio    "
          "f64: text     frame    ratio")
    def _len(values, width):
        """Text on EACH width's own ruler: `f32` = P0's shortest float32 text
        (what the journal writes today); `f64` = the value's full float64 repr
        (what a caller-owned vector's text is). Serialising both with the same
        `json.dumps` would report the `f32` row on a ruler the journal has never
        written — 8,031 B instead of 4,689 B for the real vector."""
        if width == "f32":
            short = shortest_float32_vector(values)
            text_values = short if short is not None else values
        else:
            text_values = values
        text = json.dumps(text_values, separators=(",", ":"))
        fr = json.dumps(frame(width, 4 if width == "f32" else 8, values))
        return len(text), len(fr)
    shapes = {
        "all zeros": [0.0] * EMBEDDING_DIM,
        "small ints 0..383": [float(i) for i in range(EMBEDDING_DIM)],
        "the probe's real vector (f32)": vec,
    }
    for label, values in shapes.items():
        t32, f32 = _len(values, "f32")
        t64, f64 = _len(values, "f64")
        print(f"   {label:<28} {t32:>9,} {f32:>9,}  {t32 / f32:>6.2f}x "
              f"{t64:>10,} {f64:>9,}  {t64 / f64:>6.2f}x")
    print("   ⛔ a ratio BELOW 1x means base64 is LARGER than the decimal text —")
    print("      the frame is chosen for bit-exactness and one reader, not for bytes.")

    # ── what the reader REFUSES (the fail-closed half) ──────────────────
    print("\n== the reader refuses (each raises, none decodes silently short)")
    body = frame("f32", 4, vec).rsplit(":", 1)[1]
    cases = {
        # ⚠️ Not "padding intact": a 1,536 B payload is 512 exact quanta, so the
        # body carries NO '=' padding at all. This fixture replaces the last
        # quantum and drops one, which is the same shape as the -4 sweep row.
        "last quantum replaced + one dropped (1,533 B)":
            f"b64:1:f32:{len(vec)}:{body[:-8] + 'AAAA'}",
        "one element short, count unchanged":
            f"b64:1:f32:{len(vec)}:{base64.b64encode(struct.pack('<383f', *vec[:383])).decode()}",
        "packed f64 under a declared f32":
            f"b64:1:f32:{len(vec)}:{base64.b64encode(struct.pack(f'<{len(vec)}d', *vec)).decode()}",
        "unknown version": f"b64:2:f32:{len(vec)}:{body}",
        "non-base64 character REPLACING alphabet chars":
            f"b64:1:f32:{len(vec)}:{'*' * 8 + body[8:]}",
        # ⛔ The case above is refused by the COUNT, not by `validate=True`: it
        # replaces 8 alphabet chars, so the body is no longer quantum-aligned and
        # the decoded length is wrong. The shape ONLY `validate=True` catches is
        # an INSERTION — still 1,536 B decoded, hence count-clean:
        "non-base64 chars INSERTED (count-clean, validate-only)":
            f"b64:1:f32:{len(vec)}:{body[:8] + '****' + body[8:]}",
    }
    for label, text in cases.items():
        try:
            decoded_len = len(base64.b64decode(text.rsplit(":", 1)[1], validate=True))
        except Exception:
            # an un-decodable body is its own refusal; the label says which
            decoded_len = None
        try:
            unframe(text)
        except Exception as exc:
            # the point is that it raises — the TYPE and the decoded width are the evidence
            where = f", decoded {decoded_len:,} B" if decoded_len else ""
            print(f"  {label:<42} REFUSED ({type(exc).__name__}{where})")
        else:
            print(f"  {label:<42} ⛔ DECODED — this is a defect")
            return 1

    # ── WHY `validate=True` is MANDATORY — the default decoder accepts an INSERTION
    # The note states this as measured; until this arm existed no artifact line
    # measured it (every other call in this probe passes `validate=True`, so the
    # insertion case only ever printed its REFUSAL). A "(measured: …)" tag with no
    # line behind it is the same species as a printed-not-asserted control.
    print("\n== the default decoder vs validate=True (why validate is mandatory)")
    inserted_body = cases[
        "non-base64 chars INSERTED (count-clean, validate-only)"].rsplit(":", 1)[1]
    packed_reference = struct.pack(f"<{len(producer)}f", *producer)
    default_decoded = base64.b64decode(inserted_body)  # validate=False (the default)
    strict_raises = False
    try:
        base64.b64decode(inserted_body, validate=True)
    except Exception:
        strict_raises = True
    default_silent = (len(default_decoded) % 4 == 0
                      and default_decoded == packed_reference)
    print(f"  4 inserted '*' chars: default decode {len(default_decoded):,} B  "
          f"len%4==0 {len(default_decoded) % 4 == 0}  "
          f"bits == the packed payload {default_decoded == packed_reference}  "
          f"validate=True raises {strict_raises}")
    if not default_silent or not strict_raises:
        print("  ⛔ the insertion case is no longer count-clean-and-silent under the "
              "default decoder, or validate=True no longer refuses it — the note's "
              "justification for validate=True must be re-stated")
        return 1
    print(f"  ⇒ only `validate=True` refuses it: an insertion is count-clean "
          f"({len(default_decoded):,} B, {len(default_decoded) // 4} whole floats, "
          f"bit-identical to the payload).")

    # ── WHY the count is load-bearing: the silent-truncation sweep ──────
    # `b64decode` REFUSES a non-quantum-aligned cut ("Incorrect padding", or
    # "cannot be 1 more than a multiple of 4"). The silent case is the
    # QUANTUM-ALIGNED cut: 4 base64 chars = 3 bytes, so a body shortened by a
    # multiple of 4 chars is still VALID base64 and simply decodes to fewer
    # bytes. 3k ≡ 0 (mod 4) iff k ≡ 0 (mod 4), so only every FOURTH such cut
    # leaves a length that is a multiple of the element size — and a reader
    # checking only `len(decoded) % 4` accepts it.
    print("\n== silent truncation: which cuts a LENGTH-ONLY check would accept")
    print("   cut   chars%4   decoded    floats   len%4==0   count check (declared n)")
    accepted_by_length_only = []
    silent = []
    for cut in (1, 2, 3, 4, 8, 12, 16, 20):
        try:
            out = base64.b64decode(body[:-cut], validate=True)
        except Exception as exc:
            print(f"   -{cut:<4} {(len(body) - cut) % 4:<8} refused by b64decode "
                  f"({exc})")
            continue
        silent.append(cut)
        elems = len(out) / 4
        len_only = len(out) % 4 == 0
        # The declared-count check is a DIFFERENT question from the length check:
        # it compares the decoded byte count against the element count the frame
        # declares. Computed separately, or the column is a duplicate of len%4
        # and the table reads as "the count check accepts a 381-float truncation"
        # — the exact inverse of what this section proves.
        count_ok = len(out) == len(vec) * 4
        if len_only:
            accepted_by_length_only.append(cut)
        print(f"   -{cut:<4} {(len(body) - cut) % 4:<8} {len(out):>7,} B  "
              f"{elems:>7.2f}   {len_only!s:<10}  "
              f"{'ACCEPTS' if count_ok else 'refuses'}")
    if silent != [4, 8, 12, 16, 20]:
        print(f"  ⛔ unexpected silent cuts {silent} — every multiple-of-4 cut must "
              f"decode silently (4 base64 chars = 3 whole bytes)")
        return 1
    if accepted_by_length_only != [16]:
        print(f"  ⛔ unexpected sweep result {accepted_by_length_only} — of the "
              f"cuts MEASURED (-1..-20) only -16 leaves a decoded length that is a "
              f"multiple of the 4-byte element (-32 would too: m=8, 1,512 B=378f)")
        return 1
    if len(vec) * 4 != len(base64.b64decode(body, validate=True)):
        print("  ⛔ the declared count does not match the intact body — the "
              "count check below would be vacuous")
        return 1
    # …and the frame REFUSES that exact cut, because of the count.
    short = f"b64:1:f32:{len(vec)}:{body[:-16]}"
    try:
        unframe(short)
    except ValueError as exc:
        print(f"  count check on the -16 frame: REFUSED ({exc})")
    else:
        print("  count check on the -16 frame: ⛔ DECODED — this is a defect")
        return 1

    # ---- text alphabets: base64 is NOT the only one that keeps the line JSON.
    # The design claims base64 for ubiquity, not for optimality, so the cheaper
    # alphabets are measured here rather than waved at. Sizes are the FIELD only
    # (frame prefix + 2 JSON quotes are the same 16 B for all of them).
    print("\n== text alphabets (body B = encoded chars; field B = 14 B frame prefix +")
    print("   the JSON-escaped string as it lands on the line, so an alphabet that")
    print("   needs escaping is charged for it)")
    print("   alphabet            body B   field B   vs b64   needs JSON escaping")
    raw = b"".join(struct.pack("<f", x) for x in vec)

    def _z85(data: bytes) -> str:
        alphabet = ("0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
                    ".-:+=^!/*?&<>()[]{}@%$#")
        pad = (-len(data)) % 4
        data = data + b"\0" * pad
        out = []
        for i in range(0, len(data), 4):
            n = int.from_bytes(data[i:i + 4], "big")
            chunk = []
            for _ in range(5):
                n, r = divmod(n, 85)
                chunk.append(alphabet[r])
            out.extend(reversed(chunk))
        return "".join(out)

    alphas = [
        ("hex", base64.b16encode(raw).decode()),
        ("base64 (RFC 4648 §4)", base64.b64encode(raw).decode()),
        ("base64url (§5)", base64.urlsafe_b64encode(raw).decode()),
        ("b85 (RFC 1924)", base64.b85encode(raw).decode()),
        ("a85 (Adobe Ascii85)", base64.a85encode(raw).decode()),
        ("z85 (ZeroMQ)", _z85(raw)),
    ]
    b64_field = len(json.dumps(base64.b64encode(raw).decode())) + 14
    for name, enc in alphas:
        needs_escape = any(c in enc for c in '"\\\n\r')
        field = len(json.dumps(enc)) + 14  # +2 quotes are inside json.dumps
        print(f"   {name:<20} {len(enc):>7,}   {field:>7,}   "
              f"{field / b64_field:>6.3f}x   {needs_escape}")
    if not any(n.startswith("b85") for n, _ in alphas):
        return 1
    print("  b85/z85 are 25 % overhead against base64's 33.3 % — cheaper by "
          f"{b64_field - (len(alphas[3][1]) + 16):,} B on this field. base64 is "
          "chosen for ubiquity and hand-inspectability (most languages' stdlibs: "
          "Python, Java, Go, C#, Node -- not Rust's or C's, the same reason b85's "
          "support is thin -- and `echo | base64 -d`), not for being the smallest.")

    print("\nVERDICT: measured. The frame is 2.27x the P0 vector span on a "
          "creating record, the float32 bits are unchanged, and the f64 "
          "population is preserved at its own width.")
    return 0


if __name__ == "__main__":
    os.environ.pop("TORTOISE_DB_URI", None)
    raise SystemExit(main())
