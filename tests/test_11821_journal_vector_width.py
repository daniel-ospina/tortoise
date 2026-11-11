"""#11821 P0 — the journal stores a vector at the width the embedder produced.

The measured defect (`docs/evidence/5612-journal-growth`, `STORAGE-ARCHITECTURE.md`
§5.2): a creating record was 9,176 B and **8,414 B of it was 384 floats printed at
21.9 chars/float**, because the pipeline widened the embedder's **float32** output
to float64 and let `json.dumps` print float64's shortest repr (17 significant
digits) for a value that carries ~7. That is WIDENING, not accuracy — float64
stores no information the model produced.

What this file pins, in the order the decisions were made:

1. a vector our embedder produced is stored at float32, as the shortest decimal
   that rounds back to it (`f32`), and the TEXT gets smaller;
2. the decode is EXACT — `json.loads(text) == payload["embedding"]`, not merely
   equal at the store's width — so a replay and a live write put the same numbers
   in the record's `jsonb`;
3. a CALLER-owned (`embedding_verbatim`) or PRESERVED (`embedding_preserved`)
   vector is NEVER narrowed, and declares `f64`. **This is the test that reds if
   the verbatim population is narrowed** — the issue's own correction to P0;
4. a record written BEFORE this change still reads and replays unchanged;
5. the marker is a payload description only: never a node property, never a
   record column, never callable-settable;
6. the journal line carries no insignificant whitespace (its own commit, its own
   delta).
"""
from __future__ import annotations

import json
import struct

import numpy as np
import pytest

from tortoise.embeddings import (
    JOURNAL_VECTOR_WIDTH_F32,
    JOURNAL_VECTOR_WIDTH_F64,
    JOURNAL_VECTOR_WIDTH_KEY,
    shortest_float32_vector,
    stamp_journal_embedding,
)
from tortoise.log import EventLog

#: The pinned embedder's width (`EMBEDDING_DIM`); the corpus measurement's shape.
WIDTH = 384


def _vector(width: int = WIDTH, seed: int = 11821) -> list[float]:
    """A deterministic unit vector on the float32 grid — what the embedder emits.

    The writers hand `float(x)` of a numpy float32 to the seam, so the input is
    exactly this: float32 values widened to Python floats (17-digit `repr`).
    """
    rng = np.random.default_rng(seed)
    vec = rng.standard_normal(width).astype(np.float32)
    return [float(x) for x in (vec / np.linalg.norm(vec)).astype(np.float32)]


def _payload(vector: list[float] | None, **extra) -> dict:
    payload = {"id": "p-11821", "content": "a point", "embedding": vector}
    payload.update(extra)
    return payload


def _f32_bits(values) -> list[bytes]:
    return [struct.pack("<f", float(v)) for v in values]


def _text(item) -> str:
    """The journal's own serialisation of a value (the writer is compact)."""
    return json.dumps(item, ensure_ascii=False, separators=(",", ":"))


# ── 1. the width and the saving ──────────────────────────────────────────────


def test_a_generated_vector_is_stored_at_float32_with_its_width_declared():
    widened = _vector()
    stamped = stamp_journal_embedding(_payload(list(widened)), creating=True)

    assert stamped[JOURNAL_VECTOR_WIDTH_KEY] == JOURNAL_VECTOR_WIDTH_F32
    after = _text(stamped["embedding"])
    before = _text(widened)
    assert len(after) < len(before)
    assert len(after) / WIDTH < 14.0, (
        f"{len(after) / WIDTH:.1f} chars/float — the defect this change removes "
        "was 21.9, and the float32 floor is ~12")
    assert len(after) <= 0.65 * len(before), (
        f"{len(before):,} B → {len(after):,} B — the measured saving on the "
        "pinned corpus is 8,414 → 5,072 B (39.7 %) at the same separators")


def test_every_element_keeps_its_float32_bits():
    widened = _vector()
    stamped = stamp_journal_embedding(_payload(list(widened)), creating=True)

    assert _f32_bits(stamped["embedding"]) == _f32_bits(widened), (
        "the narrowing must be lossless AT FLOAT32 — that is the only precision "
        "the embedder ever produced")


def test_the_journal_text_decodes_back_to_the_same_values_exactly():
    """The property a text-only encoder could NOT give.

    Quantising the value (instead of formatting at the printer) is what keeps
    `live == replay` exact: the payload in memory and the payload read back off
    disk are the same float64s, so a replay writes the same `jsonb` the live
    write did.
    """
    stamped = stamp_journal_embedding(_payload(_vector()), creating=True)
    line = _text(stamped)
    assert json.loads(line)["embedding"] == stamped["embedding"]


def test_the_quantiser_is_the_shortest_float32_round_trip_not_a_fixed_width():
    """`str(float32)` is shortest-per-element, so small magnitudes earn their digits.

    A fixed `%.9g` would pad every element to nine significant digits; the
    shortest form drops the ones that are not needed (measured: ~1 char/float on
    the corpus, and it is the difference between 5,072 B and 5,190 B).
    """
    # On the float32 grid (what the embedder emits), so the quantiser accepts
    # them; their SHORTEST texts are what is asserted. A fixed `%.9g` would pad
    # every one of these to nine significant digits.
    values = [float(np.float32(v)) for v in (0.5, 0.25, 0.125, 0.1, 1.0, -0.0)]
    quantised = shortest_float32_vector(values)
    assert quantised is not None
    assert [_text(v) for v in quantised] == [
        "0.5", "0.25", "0.125", "0.1", "1.0", "-0.0"]


def test_a_vector_that_does_not_fit_float32_is_left_alone():
    """A record is a FILE; `1e300` must degrade, never become `inf`.

    `np.asarray([1e300], float32)` OVERFLOWS to `inf` instead of raising, so the
    round-trip check alone passed and the payload came back holding `inf` with
    the width marked `f32` (measured before the guard). A vector that
    is not representable at float32 is not ours to narrow.
    """
    source = [1e300, 0.5]
    stamped = stamp_journal_embedding(_payload(list(source)), creating=True)

    assert stamped[JOURNAL_VECTOR_WIDTH_KEY] == JOURNAL_VECTOR_WIDTH_F64
    assert stamped["embedding"] == source
    assert shortest_float32_vector(source) is None


def test_a_vector_off_the_float32_grid_is_not_ours_to_narrow():
    """THE OWNERSHIP TEST, and the one the marker cannot make for us.

    `EventAPI.add_point(fields=…)` hands a CALLER's vector to this seam with no
    `embedding_verbatim` (that door refuses the marker as forged, because it
    mints it), so "the marker is absent" is not evidence that the vector is
    ours. "Off the embedder's float32 grid" is: nothing `encode_for_row` emits
    can be off it.

    The earlier guard compared both sides AFTER a float32 cast, so it could only
    ever detect overflow — every value below is IN RANGE and would have been
    silently rewritten: `16777217.0` loses a unit, `1e-50` becomes `0.0`, and a
    caller's digits are rounded away.
    """
    off_grid = [0.1234567890123, 16777217.0, 1e-50, 2.718281828459045]
    assert shortest_float32_vector(off_grid) is None

    stamped = stamp_journal_embedding(_payload(list(off_grid)), creating=True)
    assert stamped["embedding"] == off_grid, (
        "a caller's off-grid vector was rewritten in the durability record")
    assert stamped[JOURNAL_VECTOR_WIDTH_KEY] == JOURNAL_VECTOR_WIDTH_F64


def test_the_quantiser_leaves_process_wide_warning_state_alone():
    """`warnings.catch_warnings()` mutates PROCESS-GLOBAL filter state and is
    documented as not thread-safe; the SDK documents concurrent writers (#5612).
    Two interleaved blocks can leave a leaked `ignore` entry behind for the life
    of the process, muting the numeric warnings that surface a bad vector.

    The refusal is done in plain Python BEFORE any numpyd cast precisely so no
    suppression is needed at all.
    """
    import warnings

    before = list(warnings.filters)
    shortest_float32_vector([1e300, 0.5])      # out of range: used to warn
    shortest_float32_vector([0.1, 0.2])        # the normal path
    shortest_float32_vector([1e-50])           # underflowing to zero
    assert warnings.filters == before, (
        "the quantiser changed the process's warning filters")


# ── 2. the promise that must not be broken: verbatim / preserved ─────────────


def test_a_verbatim_vector_is_never_narrowed():
    """**THE PIN.** If P0 narrowed a caller-owned vector it would silently
    change a value the journal promised to keep raw (#5004 round-7:
    `projection._restore_derived_properties` — *"a CALLER-owned vector must keep
    its RAW form. `vecf32()` is the ONE form it must not take"*).

    The fixture is deliberately OFF the float32 grid, and the test asserts that
    a narrowing WOULD change it — so the assertion below is a real one.
    """
    caller_owned = [0.1234567890123, 0.1, 1.0 / 3.0, 2.718281828459045]
    assert [float(np.float32(x)) for x in caller_owned] != caller_owned, (
        "fixture is on the float32 grid, so narrowing it would be invisible — "
        "this test could not fail")

    stamped = stamp_journal_embedding(
        _payload(list(caller_owned), embedding_verbatim=True), creating=True)

    assert stamped["embedding"] == caller_owned, (
        "a caller-owned vector was narrowed; the verbatim population must keep "
        "its own width")
    assert stamped[JOURNAL_VECTOR_WIDTH_KEY] == JOURNAL_VECTOR_WIDTH_F64


def test_a_preserved_vector_is_never_narrowed():
    """A re-capture that encodes nothing carries the node's PRIOR vector, which
    may be a caller's — and the turn carrier does not carry the verbatim marker,
    so `embedding_preserved` is the only signal that says "not ours to narrow"."""
    prior = [0.1234567890123, 2.718281828459045]
    stamped = stamp_journal_embedding(
        _payload(list(prior), embedding_preserved=True), creating=False)

    assert stamped["embedding"] == prior
    assert stamped[JOURNAL_VECTOR_WIDTH_KEY] == JOURNAL_VECTOR_WIDTH_F64


def test_a_reemitted_snapshot_is_narrowed_because_the_store_already_did():
    """`PointPromoted` &c. carry a vector read back off the node, and the store
    writes `vecf32($embedding)` for every non-verbatim vector
    (`stored_form.node_statement`) — so narrowing is a no-op on the value and
    only shortens the text. The width declaration is what moves."""
    widened = _vector()
    stamped = stamp_journal_embedding(_payload(list(widened)), creating=False)

    assert stamped[JOURNAL_VECTOR_WIDTH_KEY] == JOURNAL_VECTOR_WIDTH_F32
    assert _f32_bits(stamped["embedding"]) == _f32_bits(widened)


# ── 3. no vector, no marker; and the old journal keeps working ───────────────


def test_no_vector_means_no_width_marker():
    none_owned = stamp_journal_embedding(_payload(None), creating=True)
    assert none_owned["embedding"] is None
    assert JOURNAL_VECTOR_WIDTH_KEY not in none_owned

    absent = stamp_journal_embedding({"id": "p-11821"}, creating=False)
    assert JOURNAL_VECTOR_WIDTH_KEY not in absent


def test_an_unusable_vector_drops_its_marker_with_the_vector():
    stamped = stamp_journal_embedding(
        _payload([float("nan"), 0.5]), creating=False)
    assert stamped["embedding"] is None
    assert JOURNAL_VECTOR_WIDTH_KEY not in stamped, (
        "a marker beside an owned None describes a payload that has nothing to "
        "describe")


def test_a_journal_written_before_this_change_still_reads_and_replays(tmp_path):
    """A pre-change line: widened float64 text, 17 digits, NO marker.

    The marker is a recorded FACT about the text, never a precondition for
    reading it — the read path is unchanged (JSON numbers parse to floats either
    way), so the old journal must come back exactly as it was written.
    """
    legacy_vector = _vector()          # float32 grid, widened to float64 repr
    legacy = {"type": "PointAdded",
              "point": {"id": "legacy-1", "content": "old",
                        "embedding": legacy_vector,
                        "embedding_model": "m", "embedding_revision": "r"}}
    line = json.dumps(legacy, ensure_ascii=False)      # the OLD writer's spacing
    path = tmp_path / "events.jsonl"
    path.write_text(line + "\n", encoding="utf-8")

    read = EventLog(path).read_all()
    assert read == [legacy]
    assert JOURNAL_VECTOR_WIDTH_KEY not in read[0]["point"], (
        "the read path must not invent a width for a record that never declared "
        "one")
    # …and it replays to the same float32 the graph holds.
    assert _f32_bits(read[0]["point"]["embedding"]) == _f32_bits(legacy_vector)


# ── 4. the journal line itself ───────────────────────────────────────────────


def test_the_journal_line_carries_no_insignificant_whitespace(tmp_path):
    """#11821 P0's second commit: `", "`/`": "` were 435 B of a 9,176 B record.

    Not a format change — JSON whitespace is insignificant — so the round trip is
    asserted in the same test rather than trusted.
    """
    record = {"type": "PointAdded",
              "point": {"id": "ws-1", "content": "a b", "embedding": [0.1, 0.2]}}
    path = tmp_path / "events.jsonl"
    EventLog(path).append(record)

    raw = path.read_bytes()
    assert b'", "' not in raw and b'": "' not in raw, (
        "the journal is byte-metered and never pretty-printed")
    assert EventLog(path).read_all() == [record]


# ── 5. the marker is a description, never state ──────────────────────────────


def test_the_marker_is_declared_in_every_home_so_it_cannot_become_node_state():
    """The same seven homes `embedding_preserved` lives in.

    Each one is a place where an undeclared payload key would LEAK: onto a node
    (`_POINT_HANDLED` / `_META_KEYS` / `_SERVER_MANAGED_PROPS`), into the
    record's `props` jsonb tail (`_RECORD_ONLY_KEYS`), or into a caller's reach.
    """
    from tortoise import record_projection as rp
    from tortoise.consistency import _EXCLUSION_REASONS
    from tortoise.mcp_server import _SERVER_MANAGED_PROPS
    from tortoise.projection.entities import _EntityHandlers

    assert JOURNAL_VECTOR_WIDTH_KEY in _EntityHandlers._POINT_HANDLED
    assert JOURNAL_VECTOR_WIDTH_KEY in _EntityHandlers._META_KEYS
    assert JOURNAL_VECTOR_WIDTH_KEY in rp._RECORD_ONLY_KEYS
    assert JOURNAL_VECTOR_WIDTH_KEY in rp.TURNS_SNAPSHOT_OPTIONAL_KEYS
    assert JOURNAL_VECTOR_WIDTH_KEY in _EXCLUSION_REASONS
    assert JOURNAL_VECTOR_WIDTH_KEY in _SERVER_MANAGED_PROPS


def test_a_caller_cannot_forge_the_width():
    from tortoise.sdk import _sanitize_props

    with pytest.raises(ValueError):
        _sanitize_props({JOURNAL_VECTOR_WIDTH_KEY: JOURNAL_VECTOR_WIDTH_F64})


def test_the_turn_snapshot_contract_admits_the_marker():
    """The capture-turn carrier's payload contract must accept the marker, or the
    armed lane journals a key its own pin refuses."""
    from tortoise import record_projection as rp

    snapshot = {"id": "t1", "content": "c", "pointKind": "event",
                "speaker": "user", "is_episodic": True, "status": "draft",
                "createdAt": "2026-11-11T00:00:00Z",
                "embedding": [0.1], JOURNAL_VECTOR_WIDTH_KEY: "f32"}
    undeclared = set(snapshot) - (rp.TURNS_SNAPSHOT_KEYS
                                  | rp.TURNS_SNAPSHOT_OPTIONAL_KEYS)
    assert not undeclared, (
        f"the snapshot carries undeclared keys {sorted(undeclared)}")
