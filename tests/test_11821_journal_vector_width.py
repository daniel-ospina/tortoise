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
6. the journal line carries no insignificant whitespace (its own delta —
   attributable by measurement, because both changes share ONE commit);
7. the `EventRecorded` DOOR (`sdk._journal_event_raw_write`, the fourth
   vector-bearing producer) declares its own width — the one stamp whose
   absence no other pin could see.
"""
from __future__ import annotations

import contextlib
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
    ours. "Off the embedder's float32 grid" is: `encode_batch_for_store` returns
    `vec.tolist()` of a float32 array, so nothing it emits can be off it.

    Every value below is IN RANGE, so a guard that compares both sides AFTER a
    float32 cast accepts all of them and silently rewrites the record:
    `16777217.0` loses a unit, `1e-50` becomes `0.0`, and a caller's digits are
    rounded away.
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

    The range refusal is done in plain Python BEFORE any numpyd cast precisely
    so that no suppression is needed at all — and THAT is the observable this
    pins: with the cast reached, numpy emits `RuntimeWarning: overflow
    encountered in cast`. Asserting only on `warnings.filters` could not see it
    (a `catch_warnings` block restores the filters on `__exit__`, so the leak
    this test names is invisible to a before/after comparison — measured: the
    removed defect restored, 20 passed).
    """
    import warnings

    before = list(warnings.filters)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")     # make the defect's warning VISIBLE
        # out of range: the case that warned before the plain-Python refusal
        assert shortest_float32_vector([1e300, 0.5]) is None
        # underflowing to zero: the other cast-collision shape
        assert shortest_float32_vector([1e-50, 0.5]) is None
        # the on-grid path must still narrow (so the assertions above cannot be
        # satisfied by a function that refuses everything)
        assert shortest_float32_vector([float(np.float32(0.1)), 0.5]) == [0.1, 0.5]
    numeric = [str(w.message) for w in caught
               if issubclass(w.category, RuntimeWarning)]
    assert not numeric, (
        "the range refusal must run BEFORE the numpyd cast — a RuntimeWarning "
        f"means the cast was reached and a warning fired: {numeric}")
    assert warnings.filters == before, (
        "the quantiser changed the process's warning filters")


def test_a_decimal_literal_off_the_grid_is_refused_and_its_widening_is_not():
    """The ownership boundary, both sides, stated once.

    `0.1` as a float64 is NOT a float32 widening (`np.float32(0.1)` widened is
    `0.10000000149011612`), so it is somebody's value, not a widening to undo,
    and it is refused — the docstring's rule. `float(np.float32(0.1))` IS the
    embedder's grid and narrows back to the short text `0.1`.
    """
    assert shortest_float32_vector([0.1, 0.2]) is None
    assert shortest_float32_vector([float(np.float32(0.1)), 0.5]) == [0.1, 0.5]


def test_a_vector_that_UNDERFLOWS_float32_is_left_alone():
    """The same both-sides-equal bug in the direction the first guard missed.

    `np.float32(1e-300)` is `0.0`, so a cast equality compares `0.0` to `0.0`
    and passes: the element is DESTROYED and the payload still marked `f32`
    (measured: `shortest_float32_vector([1e-300, 0.5])` → `[0.0, 0.5]`). A
    non-zero element whose float32 cast is zero is not representable at float32,
    exactly as `1e300` → `inf` is — and the payload's own recorded meaning
    ("every element is stored at the float32 the embedder produced") is a lie
    when one element is a zero the input never held.

    ⚠️ The subnormal half was RESTATED by the grid rule, and the difference is
    the point: a value that IS a float32 subnormal (`float(np.float32(1e-45))`)
    is on the grid and narrows, while the decimal literal `1e-45` is not a
    float32 at all — its nearest float32 is 40 % away, so narrowing it would
    change the number and record that as the embedder's own.
    """
    source = [1e-300, 0.5]
    stamped = stamp_journal_embedding(_payload(list(source)), creating=True)

    assert stamped[JOURNAL_VECTOR_WIDTH_KEY] == JOURNAL_VECTOR_WIDTH_F64
    assert stamped["embedding"] == source, (
        "an underflowing element was destroyed and the payload still marked f32")
    assert shortest_float32_vector(source) is None
    assert shortest_float32_vector([-1e-300, 0.5]) is None, (
        "underflow is sign-blind: -1e-300 casts to -0.0, which compares equal")
    assert shortest_float32_vector([float(np.float32(1e-45)), 0.5]) is not None, (
        "a float32 SUBNORMAL is on the grid — representable, so ours to narrow")
    assert shortest_float32_vector([1e-45, 0.5]) is None, (
        "the decimal literal 1e-45 is not a float32; narrowing it would move "
        "the value by 40 % and call the result the embedder's own")


# ── 2. the promise that must not be broken: verbatim / preserved ─────────────


def test_a_verbatim_vector_is_never_narrowed():
    """**THE PIN.** If P0 narrowed a caller-owned vector it would silently
    change what the journal promised to keep raw (#5004 round-7: the graph-only
    id's only restorer is `projection.FalkorProjection._rebuild_all_replay`'s `embedding_verbatim`
    guard — *"a CALLER-owned vector must keep its RAW form. `vecf32()` is the ONE
    form it must not take"* — and the flag picks the storage form in
    `projection/entities.py::_upsert_point_props`'s `CASE WHEN $embedding_verbatim
    THEN $embedding ELSE vecf32($embedding)`).

    ⛔ TWO fixtures, and the second is the one that makes this the MARKER's pin
    rather than the grid rule's:

    * an OFF-GRID caller vector — a narrowing would change the VALUE; and
    * a **grid-exact** caller vector — a narrowing would change only the raw
      TEXT (`0.10000000149011612` → `0.1`), which is exactly the "keeps its RAW
      form" promise, and which the grid rule alone cannot protect.

    Measured: with the `is_verbatim` arm removed, the off-grid fixture still
    passes (the grid refuses it) and this test went GREEN — a pin that could not
    fail. With the grid-exact fixture added it reds, which is what makes the
    assertion below real.
    """
    off_grid = [0.1234567890123, 0.1, 1.0 / 3.0, 2.718281828459045]
    assert [float(np.float32(x)) for x in off_grid] != off_grid, (
        "fixture is on the float32 grid, so narrowing it would be invisible")
    grid_exact = [float(np.float32(v)) for v in (0.1, -0.25, 1.0 / 3.0)]
    assert np.all(np.asarray(grid_exact, dtype=np.float32).astype(np.float64)
                  == np.asarray(grid_exact, dtype=np.float64)), (
        "the second fixture must be ON the grid — it is the raw-TEXT half")

    for caller_owned in (off_grid, grid_exact):
        stamped = stamp_journal_embedding(
            _payload(list(caller_owned), embedding_verbatim=True), creating=True)

        assert stamped["embedding"] == caller_owned, (
            "a caller-owned vector was narrowed; the verbatim population must "
            "keep its own width AND its own text")
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
    """#11821 P0's compact separators: `", "`/`": "` were 435 B of a 9,176 B record.

    They share ONE commit with the float32 narrowing (the plan's proposed split
    would have left an intermediate red test), so the separator delta is
    attributed by measurement — the vector-less corpus rows (`PointRevised`,
    `EntityLinked`) can only be moved by whitespace — never by `git log`.

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

    Six are asserted here. The SEVENTH — `api.EventAPI._point`'s forged-key
    refusal — is pinned where it can actually redden: that set is a
    function-local literal, so no test can assert its membership; the refusal
    is driven through the door instead, by
    `test_journal_embedding_5004.py::test_eventapi_rejects_the_server_managed_journal_markers`
    (#11835 review P2 — the omission left deleting the `api.py` entry GREEN).
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


# ── 7. the fourth producer: the `EventRecorded` door ─────────────────────────


def _door_record(tmp_path, *, embedding, event_id="evt-11821"):
    """Drive the REAL door and return the record it journalled.

    ``sdk._journal_event_raw_write`` builds its own payload (it does not pass
    through `_emit_event`'s stamp), so its width declaration is a SEPARATE
    producer — the review's finding was that deleting that block reddened
    nothing, anywhere.

    ``graph=None``: the props here name no pack kind, so `#5475`'s gate takes
    its no-pack early return (`payload_names_pack(record)` is empty) and never
    reads a graph. The gate itself is pinned in
    `tests/test_9068_pack_gate_residuals.py`; this file stays hermetic
    (`tmp_path` only — no DB, no graph, no network), which is what lets its
    registration run on every lane.
    """
    from tortoise.sdk import TortoiseSDK

    journal = tmp_path / "events.jsonl"
    sdk = TortoiseSDK(str(tmp_path / "door.db"), event_log_path=str(journal))
    try:
        committed = sdk._journal_event_raw_write(
            event_id, {"content": "a session event"}, graph=None,
            embedding=list(embedding))
    finally:
        with contextlib.suppress(Exception):
            sdk.close()

    assert committed is not False, (
        "the door reports a failed append as False — nothing to assert then")
    records = [json.loads(ln) for ln in
               journal.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(records) == 1, f"expected exactly one record, got {records!r}"
    assert records[0]["type"] == "EventRecorded"
    return records[0]


def test_the_event_recorded_door_declares_its_width(tmp_path):
    """#11821 P0's FOURTH producer: `sdk._journal_event_raw_write`.

    A session `:Event`'s vector is `_session_embedding`'s — our embedder's
    float32 — or a value read back off the node, and this door builds its
    payload from `props` instead of going through `_emit_event`'s stamp. Delete
    the stamp block at `sdk.py::_journal_event_raw_write` and the whole suite
    stayed green while the door silently journalled an UNDECLARED-width vector:
    the one producer the seam's contract ("every journalled vector says what
    width it was written at") would not cover, and the population P2's base64
    migration would have to guess about.

    ⚠️ "producer", not "record": `TortoiseSDK.update_point` splats a
    CALLER-supplied `embedding` onto a `PointRevised` record's TOP level
    (`_emit_event("PointRevised", id=..., **emit_props)`), which no payload stamp
    reaches — measured, that record carries `embedding` and no
    `embedding_width`. It is a residual population, not one this file covers.

    So both halves are asserted — the DECLARATION and the NARROWING — because a
    marker written without the values it describes would be a lie the next
    reader acts on.
    """
    widened = _vector()
    record = _door_record(tmp_path, embedding=widened)

    assert record[JOURNAL_VECTOR_WIDTH_KEY] == JOURNAL_VECTOR_WIDTH_F32, (
        "the EventRecorded door journalled a vector with no width declared")
    assert _f32_bits(record["embedding"]) == _f32_bits(widened)
    assert len(_text(record["embedding"])) < len(_text(widened)), (
        "the door's vector must be stored at the embedder's float32, not the "
        "widened float64 repr the seam exists to remove")


def test_the_event_recorded_door_declares_f64_for_a_vector_it_cannot_narrow(
        tmp_path):
    """The other half of the marker at this door.

    The door does not carry a route through `creating=True`, and its vector can
    be one it did not produce (a value read back off the node). A vector with an
    element float32 cannot represent must therefore still be journalled —
    UNTOUCHED — and must say `f64`, so a later width/format migration cannot
    narrow a population nobody declared as foreign.
    """
    source = [1e-300, 0.5, -1e-300]
    record = _door_record(tmp_path, embedding=source, event_id="evt-11821-f64")

    assert record["embedding"] == source, "the values must be left ALONE"
    assert record[JOURNAL_VECTOR_WIDTH_KEY] == JOURNAL_VECTOR_WIDTH_F64
