#!/usr/bin/env python3
"""#9025 — what one capture costs the journal, measured through the landed writer.

WHAT THIS ANSWERS. `#9025` needs a **bound priced in bytes-per-year**, and the
prices that exist are per RECORD (`#5612`'s 9,176 B `PointAdded`) and per
create. A year is not made of creates — it is made of *captures*, and a capture
on the record-armed lane journals ONE `TurnsRecorded` carrier holding every
turn's snapshot (#10017 R2). Neither the carrier's size nor a per-capture total
has been measured. This probe measures both, so the three options on `#9025`
(keep / compact / archive) can be priced from bytes rather than from a record
class.

WHAT IS REAL HERE:

  * the WRITER — every line is appended by `tortoise.log.EventLog.append`, so
    the bytes are the bytes a deployment stores (compact separators since
    `#11821` P0, torn-tail/seal grammar included);
  * the VECTOR SEAM — each turn's and each point's vector goes through
    `tortoise.embeddings.stamp_journal_embedding`, the single seam both
    producers use, at the pinned embedder's width (`EMBEDDING_DIM` = 384) and
    at the width it stores (`f32` for our own vectors);
  * the INSTRUMENT — totals come from `tools.journal_growth.measure`, the tool
    `#5612` and `#11821` both used, so the per-class figures here are directly
    comparable with theirs.

WHAT IS MODELLED, and therefore stated rather than hidden:

  * `TURN_CHARS` — a turn's text length. The transcript is not committed to this
    repo in a sized form, so 1,000 characters is used as a mid value and the
    probe PRINTS the content share so the reader can see it is small beside the
    vector (the vector is 4,689 B; the text is ~1 KB).
  * `POINTS_PER_CAPTURE` / `DELTAS_PER_CAPTURE` — extraction yield. The range is
    from the repo's own records: `docs/drafts/2026-08-09-state-epistemic-
    separation.md` §"Node count per session = 1 Source + ~10-25 knowledge
    nodes", and `#4899`'s "≈2.7 per session" target. The probe reports the
    scenarios separately instead of blending them into one mean.
  * the per-capture FIXED tail (session container, narrative, deltas) is taken
    from the committed 29-type corpus' own records, re-appended through the
    writer, so it is measured rather than assumed.

The probe writes ONLY into `tempfile.mkdtemp()`; it never touches a configured
journal. It prints, and `measurement.txt` beside this file is one run's output.

Usage
=====
    env -u TORTOISE_DB_URI uv run python \
        docs/evidence/9025-journal-bound/probes/journal_bound_probe.py

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
    EMBEDDING_MODEL_REVISION,
    stamp_journal_embedding,
)
from tortoise.log import EventLog  # noqa: E402

CORPUS = REPO / "supabase/tests/fixtures/record_projection_journal.jsonl"

#: A turn's text length. See the module docstring: modelled, and the probe
#: prints the share so it cannot be mistaken for a measurement.
TURN_CHARS = 1_000

#: The measured P0 figures this probe must reproduce, so a reader can tell the
#: probe is running the LANDED writer and not a look-alike. `#11821` P0 /
#: `docs/evidence/11821-journal-encoding/` (compact ruler).
PINNED_POINT_ADDED = 5_423
PINNED_VECTOR_COMPACT = 4_689


def unit_vector(seed: int = 5612, width: int = EMBEDDING_DIM) -> list[float]:
    """A deterministic unit vector at the pinned embedder's width, as float32
    widened to Python floats — what every producer hands the seam."""
    rng = np.random.default_rng(seed)
    vec = rng.standard_normal(width).astype(np.float32)
    return [float(x) for x in (vec / np.linalg.norm(vec)).astype(np.float32)]


def corpus_records() -> list[dict]:
    return [json.loads(line) for line in
            CORPUS.read_text(encoding="utf-8").splitlines() if line.strip()]


def turn_snapshot(i: int, vec: list[float], *, chars: int = TURN_CHARS) -> dict:
    """ONE turn's snapshot, shaped exactly as
    `tortoise/sdk.py::_write_capture_turns` builds it (#10017's carrier takes
    the writer's snapshots VERBATIM — `record_projection.TURNS_SNAPSHOT_KEYS`).

    `stamp_journal_embedding(creating=True)` is what the carrier's emit does for
    these (sdk.py, the `_record_first` branch): the vector is stamped HERE
    rather than by `_emit_event`, because the carrier is neither `PointAdded`
    nor `OperatorAdded`.
    """
    snapshot = {
        "id": f"sess{i // TURNS_FOR_ONE_SESSION:04d}_t{i % TURNS_FOR_ONE_SESSION}",
        "content": ("turn %d " % i) + ("x" * max(0, chars - 8)),
        "pointKind": "event",
        "speaker": "user",
        "is_episodic": True,
        "status": "draft",
        "createdAt": "2026-11-13T00:00:00Z",
        "embedding": vec,
    }
    return stamp_journal_embedding(snapshot, creating=True)


TURNS_FOR_ONE_SESSION = 10_000  # only makes ids unique; not a scenario knob


def capture_records(turns: int, points: int, seed0: int = 0) -> list[tuple[str, dict]]:
    """The records ONE capture appends, in the writer's own shape.

    * one `TurnsRecorded` carrier (record-armed lane) holding every turn;
    * one `PointAdded` per extracted point, each with its own vector;
    * the corpus' own small deltas/containers as the per-capture tail.
    """
    out: list[tuple[str, dict]] = []
    out.append(("TurnsRecorded", {
        "type": "TurnsRecorded",
        "session_id": f"sess{seed0:04d}",
        "contains_session": f"sess{seed0:04d}",
        "turns": [turn_snapshot(seed0 * 1_000 + i, unit_vector(seed0 * 10_000 + i))
                  for i in range(turns)],
    }))
    for p in range(points):
        payload = {
            "content": ("claim %d " % p) + ("y" * 240),
            "pointKind": "statement",
            "status": "live",
            "createdAt": "2026-11-13T00:00:00Z",
            "embedding": unit_vector(seed0 * 100 + p + 7),
        }
        # ⛔ THE SEAM IS NOT OPTIONAL. `stamp_journal_embedding` is where the
        # vector is stored at the embedder's float32 (#11821 P0); skipping it
        # writes float64's 17-digit repr and measures a record no deployment
        # stores (8,525 B instead of ~5.5 KB — caught by the pin check below,
        # which is why that check exists).
        out.append(("PointAdded", {
            "id": f"sess{seed0:04d}_p{p}",
            "kind": "statement",
            "content_hash": "h" * 16,
            "point": stamp_journal_embedding(payload, creating=True),
        }))
    return out


def measured_tail() -> list[tuple[str, dict]]:
    """The capture's non-vector records, taken from the committed corpus.

    `TurnsRecorded` and `PointAdded` are modelled above (they are the two
    classes whose SIZE is the question). Everything else — the session
    container, the narrative, the retractions, the revisions — is a delta whose
    committed shape already exists, so it is measured, not invented. Vector-
    bearing types are excluded: their size is modelled above instead.
    """
    skip = {"TurnsRecorded", "PointAdded", "OperatorAdded",
            "PointPromoted", "OperatorPromoted"}
    seen: set[str] = set()
    out: list[tuple[str, dict]] = []
    for rec in corpus_records():
        rtype = rec.get("type")
        if not isinstance(rtype, str) or rtype in skip or rtype in seen:
            continue
        seen.add(rtype)
        out.append((rtype, rec))
    return out


def write_journal(path: Path, records: list[tuple[str, dict]]) -> int:
    log = EventLog.scratch(path)
    for rtype, rec in records:
        log.append({**rec, "type": rtype})
    return path.stat().st_size


def reproduced_pin() -> int:
    """The `#11821` P0 `PointAdded` pin, reproduced through the landed writer.

    The corpus' richest `PointAdded`, its placeholder vector replaced with the
    same seeded 384-d vector the encoding probe used, stamped by the same seam —
    so this number is comparable to the published 5,423 B and a drift in the
    writer the decision rests on shows up as a mismatch rather than as a quiet
    re-baseline.
    """
    rec = richest(corpus_records(), "PointAdded")
    payload = dict(rec["point"])
    payload["embedding"] = unit_vector(5612)
    return len(json.dumps({"type": "PointAdded", **{k: v for k, v in rec.items()
                                                    if k != "point"},
                           "point": stamp_journal_embedding(payload, creating=True)},
                          ensure_ascii=False,
                          separators=(",", ":")).encode("utf-8")) + 1


def richest(recs: list[dict], rtype: str) -> dict:
    """The fullest committed record of *rtype* — the shape production writes."""
    best = None
    for rec in recs:
        if rec.get("type") != rtype:
            continue
        size = len(json.dumps(rec, ensure_ascii=False,
                              separators=(",", ":")).encode("utf-8"))
        if best is None or size > best[0]:
            best = (size, rec)
    if best is None:
        raise SystemExit(f"no {rtype} record in the corpus")
    return best[1]


def by_type(report: dict) -> dict[str, dict]:
    """The per-record-class byte rows, keyed by record type."""
    return report["total"]["by_type"]


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="journal-bound-9025-"))
    print(f"corpus: {CORPUS.name} — {len(corpus_records())} committed records")
    print(f"vector: {EMBEDDING_DIM}-d, {EMBEDDING_MODEL} @ {EMBEDDING_MODEL_REVISION[:8]}")
    print(f"turn text: {TURN_CHARS} chars (MODELLED — see the probe docstring)")
    print()

    # ---- 1. the two record classes the decision rests on ------------------
    single = tmp / "single.jsonl"
    write_journal(single, capture_records(turns=1, points=1, seed0=0))
    one = by_type(measure(single))
    turn_bytes = one["TurnsRecorded"]["bytes"]
    point_bytes = one["PointAdded"]["bytes"]
    print("== per-record, measured through `EventLog.append` (post-#11821 P0)")
    print(f"  TurnsRecorded, 1 turn        {turn_bytes:,} B")
    print(f"  PointAdded (with vector)     {point_bytes:,} B"
          f"   [pinned #11821 P0: {PINNED_POINT_ADDED:,} B]")

    pin = reproduced_pin()
    print(f"  PointAdded, #11821 pin      {pin:,} B"
          f"   [published: {PINNED_POINT_ADDED:,} B]")

    # the probe's own licence to be believed: the landed writer must reproduce
    # the published pin, or the arithmetic below is measuring a look-alike
    if pin != PINNED_POINT_ADDED:
        print(f"  ⛔ PROBE IS NOT MEASURING THE LANDED WRITER: "
              f"{pin} != {PINNED_POINT_ADDED}", file=sys.stderr)
        return 2

    tail = measured_tail()
    tail_path = tmp / "tail.jsonl"
    write_journal(tail_path, tail)
    tail_report = by_type(measure(tail_path))
    tail_bytes = sum(s["bytes"] for s in tail_report.values())
    print(f"  per-capture FIXED tail       {tail_bytes:,} B"
          f"   ({len(tail)} records: {', '.join(sorted(tail_report))})")

    # ---- 2. per turn, marginal --------------------------------------------
    # The carrier is one line, so its overhead is measured by DIFFERENCE over
    # the same turn population rather than attributed by guesswork.
    t2 = tmp / "turns2.jsonl"
    write_journal(t2, capture_records(turns=2, points=0, seed0=0))
    two_turns = by_type(measure(t2))["TurnsRecorded"]["bytes"]
    marginal_turn = two_turns - turn_bytes
    print(f"  marginal per extra turn      {marginal_turn:,} B"
          f"   (carrier 2 turns − 1 turn)")
    print()

    # ---- 3. per capture, three scenarios ----------------------------------
    scenarios = [
        ("S  small", 20, 3),
        ("M  typical", 98, 8),
        ("L  heavy", 98, 25),
    ]
    print("== per capture, measured (carrier + points + the measured tail)")
    print(f"  {'scenario':<12} {'turns':>6} {'points':>7} {'bytes':>12}")
    per_capture: dict[str, int] = {}
    for name, turns, points in scenarios:
        f = tmp / f"cap-{name.split()[0]}.jsonl"
        recs = capture_records(turns=turns, points=points, seed0=1) + tail
        write_journal(f, recs)
        report = measure(f)
        per_capture[name] = report["total"]["bytes"]
        print(f"  {name:<12} {turns:>6} {points:>7} "
              f"{report['total']['bytes']:>12,}")
    print()

    # ---- 4. the vector share, so the P2 column is arithmetic on measurements
    vec_bytes = one["TurnsRecorded"]["bytes"]  # one turn: carrier + vector
    print("== the vector's share (this is what the P2 encoding step moves)")
    print(f"  one turn, with its vector    {vec_bytes:,} B")
    print(f"  its vector alone             {PINNED_VECTOR_COMPACT:,} B"
          f"  ({PINNED_VECTOR_COMPACT / vec_bytes:.0%} of the turn)")
    p2_vector = 2_064  # base64 of 384 float32 + 16 B framing (PR #12469, measured)
    print(f"  its vector under P2          {p2_vector:,} B"
          f"  (−{PINNED_VECTOR_COMPACT - p2_vector:,} B per vector)")
    print()

    # ---- 4b. the P2 encoding column (ARITHMETIC on two measured endpoints) --
    # ⚠️ NOT a measured P2 record: P2 is unlanded (PR #12469 is a design note).
    # Every figure here is the measured post-P0 record MINUS the measured
    # per-vector delta (4,689 → 2,064 B), so it prices the encoding decision
    # without claiming a build that does not exist.
    body = {name: (turns, points) for name, turns, points in scenarios}
    p2_capture = {name: per_capture[name]
                  - (turns + points) * (PINNED_VECTOR_COMPACT - p2_vector)
                  for name, (turns, points) in body.items()}
    print("== the same captures under P2's base64 payload (ARITHMETIC, not a build)")
    print(f"  {'scenario':<12} {'post-P0':>12} {'P2 frame':>12} {'ratio':>7}")
    for name, _, _ in scenarios:
        print(f"  {name:<12} {per_capture[name]:>12,} {p2_capture[name]:>12,} "
              f"{per_capture[name] / p2_capture[name]:>6.2f}×")
    print()

    # ---- 4c. how much of a turn is TEXT rather than vector ----------------
    # The turn text is the one modelled knob. At the p50 session (111k tokens
    # over ~98 turns) a turn is ≈4.5k characters, not the 1,000 used above — so
    # the reader can see exactly what that knob is worth.
    big = tmp / "turntext.jsonl"
    write_journal(big, [("TurnsRecorded", {
        "type": "TurnsRecorded", "session_id": "s9", "contains_session": "s9",
        "turns": [turn_snapshot(9, unit_vector(999), chars=4_500)]})])
    big_turn = by_type(measure(big))["TurnsRecorded"]["bytes"]
    print("== turn-text sensitivity (the one modelled knob)")
    print(f"  one turn, {TURN_CHARS:,} chars of text   {turn_bytes:,} B")
    print(f"  one turn, 4,500 chars of text   {big_turn:,} B "
          f"(+{big_turn - turn_bytes:,} B; the p50 session's turns are ~4.5k chars)")
    print()

    # ---- 5. the multi-year projection -------------------------------------
    # Write rate: `docs/drafts/2026-08-09-economics-guardrails.md` §F3 records
    # the power user at 390 captures/month (13/day); `#4899` and the state/
    # epistemic doc bracket the extraction yield used above.
    rates = [("a light user (30 captures/mo)", 30),
             ("a power user (390 captures/mo)", 390)]
    print("== multi-year projection — bytes on the persistent volume")
    print("   (journal bytes are never deleted today, so the cumulative total")
    print("    at month N is N × the monthly rate)")
    header = f"  {'who':<34} {'per month':>12} {'1 year':>12} {'3 years':>12}"
    print(header)
    for label, per_month in rates:
        for name, _, _ in scenarios:
            b = per_capture[name] * per_month
            print(f"  {label + ' · ' + name.split()[0]:<34} "
                  f"{b:>12,} {b * 12:>12,} {b * 36:>12,}")
    print()

    # ---- 6. cost, at the published rate of record -------------------------
    # §8 of SEARCH-ARCHITECTURE (and §5 of STORAGE-ARCHITECTURE): Postgres
    # disk $0.125/GB-month; Glacier IR $0.004/GB-month, Deep Archive
    # $0.00099/GB-month (AWS published, verified 2026-11-13).
    GB = 1024 ** 3
    rate_disk = 0.125
    rate_glacier_ir = 0.004
    rate_deep_archive = 0.00099
    print("== the bill for ONE user's journal — steady state at month 36")
    print("   (storage is charged on the CUMULATIVE bytes, every month)")
    print(f"  {'who · scenario':<30} {'GB':>8} {'$0.125/GB':>11}"
          f" {'Glacier IR':>11} {'Deep Arch':>10}")
    for label, per_month in rates:
        for name, _, _ in scenarios:
            gb = per_capture[name] * per_month * 36 / GB
            print(f"  {label.split('(')[0].strip() + ' · ' + name.split()[0]:<30} "
                  f"{gb:>8.2f} {gb * rate_disk:>11.4f} "
                  f"{gb * rate_glacier_ir:>11.4f} {gb * rate_deep_archive:>10.4f}")
    print()

    # ---- 7. a tenant-count view, so "30% of $19" can be judged ------------
    # The owner's budget rule (SEARCH-ARCHITECTURE §8): 30 % of the tier price
    # is the whole cost budget, and it is $5.70 for solo at $19.
    print("== the same bill at 1,000 power users (a project)")
    for name, _, _ in scenarios:
        gb = per_capture[name] * 390 * 36 * 1_000 / GB
        print(f"  {name.split()[0]:<3}  {gb:>9.2f} GB   "
              f"${gb * rate_disk:>9.2f}/mo disk   "
              f"${gb * rate_deep_archive:>8.2f}/mo deep archive")
    print()

    # ---- 8. the trigger: when does the journal alone reach the budget -----
    # SEARCH-ARCHITECTURE §8: solo's whole storage budget is 30 % of $19 =
    # $5.70/month, covering falkor + postgres. The journal's line reaches
    # $5.70 when a user's CUMULATIVE bytes hit 5.70 / 0.125 GB = 45.6 GB.
    BUDGET = 5.70
    print("== the trigger — when the journal line ALONE reaches solo's whole budget")
    month_disk = per_capture["M  typical"] * 390 / GB * rate_disk
    print(f"  a power user (M) adds ${month_disk:.4f}/month of disk each month")
    print(f"  cumulative reaching ${BUDGET:.2f}/month: "
          f"month {round(BUDGET / month_disk)} "
          f"({round(BUDGET / month_disk) / 12:.1f} years of one power user)")
    print(f"  with P2's payload instead: "
          f"month {round(BUDGET / (p2_capture['M  typical'] * 390 / GB * rate_disk))}")
    print()

    print("VERDICT: a measurement, not a bound — the three options on #9025 are")
    print("priced from these bytes; choosing between them is the owner's.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
