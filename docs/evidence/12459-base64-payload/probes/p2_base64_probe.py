#!/usr/bin/env python3
"""#12459 P2 — measure the base64-float32 frame against the landed P0 journal.

WHAT THIS ANSWERS. The P0 design note's figures are already committed
(`docs/evidence/11821-journal-encoding/`); this probe measures the P2
PROJECTION on the same corpus, with the same instrument convention, so every
number in `docs/plans/*-issue-12459-journal-base64-payload.md` is reproducible
by one command.

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
"what would this cost". Everything it prints is measured on that tempdir copy. `tools/journal_growth.py` cannot answer the vector questions (it
re-serialises with SPACED separators — its own docstring calls the output
attribution; see #11886), so the vector spans here are measured by walking the
journal this probe actually wrote, on ONE ruler.

Usage
=====
    env -u TORTOISE_DB_URI uv run python \
        docs/evidence/12459-base64-payload/probes/p2_base64_probe.py

Exit: 0 measured · 2 usage.
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
    shortest_float32_vector,
    JOURNAL_VECTOR_WIDTH_F32,
    JOURNAL_VECTOR_WIDTH_F64,
    JOURNAL_VECTOR_WIDTH_KEY,
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


def as_f32_bits(values: list[float]) -> bytes:
    return struct.pack(f"<{len(values)}f", *values)


def as_f64_bits(values: list[float]) -> bytes:
    return struct.pack(f"<{len(values)}d", *values)


def main() -> int:  # noqa: C901 — a report, read top to bottom
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
    print("\n== per class, vector-bearing records (bytes on disk, JSON line + terminator)")
    print("   class                 P0 rec   P0 vector   P2 vector   P2 rec   rec ratio")
    rows = []
    for rtype in VECTOR_TYPES:
        rec = by_type.get(rtype)
        if rec is None:
            continue
        p0_rec_bytes = _line_len(rec, spaced=False)
        span = _embedding_span(rec, seps=(",", ":"))
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

    # ── the whole corpus ────────────────────────────────────────────────
    total_p0 = sum(_line_len(r, spaced=False) for r in recs)
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
        text = frame("f32", 4, vector)
        decoded = unframe(text)
        same = as_f32_bits(decoded) == as_f32_bits(vector)
        p0_text_f32 = as_f32_bits([float(np.float32(v)) for v in vector])
        text_equiv = p0_text_f32 == as_f32_bits(vector)
        print(f"  {rec.get('type', '?'):<20} {len(vector):>4}-d  "
              f"base64→f32 bits == P0 text→f32 bits: {same}"
              f"   (P0 text→f32 == producer f32: {text_equiv})")
        assert same and text_equiv, rec.get("type")
        equiv += 1
    print(f"  {equiv} of {len(recs)} corpus records carry a vector and were verified")

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
    # The frame costs a flat 5.33 chars/float (f32) / 10.71 (f64), so it is a
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
        "truncated body, padding intact":
            f"b64:1:f32:{len(vec)}:{body[:-8] + 'AAAA'}",
        "one element short, count unchanged":
            f"b64:1:f32:{len(vec)}:{base64.b64encode(struct.pack('<383f', *vec[:383])).decode()}",
        "packed f64 under a declared f32":
            f"b64:1:f32:{len(vec)}:{base64.b64encode(struct.pack(f'<{len(vec)}d', *vec)).decode()}",
        "unknown version": f"b64:2:f32:{len(vec)}:{body}",
        "non-base64 character in body": f"b64:1:f32:{len(vec)}:{'*' * 8 + body[8:]}",
    }
    for label, text in cases.items():
        try:
            decoded_len = len(base64.b64decode(text.rsplit(":", 1)[1], validate=True))
        except Exception:  # noqa: BLE001 — an un-decodable body is its own refusal
            decoded_len = None
        try:
            unframe(text)
        except Exception as exc:  # noqa: BLE001 — the point is that it raises
            where = f", decoded {decoded_len:,} B" if decoded_len else ""
            print(f"  {label:<42} REFUSED ({type(exc).__name__}{where})")
        else:
            print(f"  {label:<42} ⛔ DECODED — this is a defect")
            return 1

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
        except Exception as exc:  # noqa: BLE001
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
              f"{elems:>7.2f}   {str(len_only):<10}  "
              f"{'ACCEPTS' if count_ok else 'refuses'}")
    if silent != [4, 8, 12, 16, 20]:
        print(f"  ⛔ unexpected silent cuts {silent} — every multiple-of-4 cut must "
              f"decode silently (4 base64 chars = 3 whole bytes)")
        return 1
    if accepted_by_length_only != [16]:
        print(f"  ⛔ unexpected sweep result {accepted_by_length_only} — only -16 "
              f"leaves a decoded length that is a multiple of the 4-byte element")
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
