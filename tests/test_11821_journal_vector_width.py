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
   absence no other pin could see;
8. the declaration is IDEMPOTENT: a second application of the single declared
   seam does not flip `f32` to `f64` over values it did not change;
9. …and that idempotence NEVER trusts the marker on its own: a vector replaced
   after the stamp (`EventAPI.add_point`'s `p.update(fields)`) re-decides the
   width from the text actually in the payload.
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
    _is_shortest_f32_text,
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


def test_the_width_declaration_is_idempotent_under_a_second_stamp():
    """A payload re-stamped by the same seam must not flip `f32` -> `f64`.

    #11835 review (P3), measured before the guard: the narrowed values are the
    round-tripping DECIMALS, not the widened float32s, so they sit off the
    float32 grid the ownership test asks for. A second application therefore
    read them as "not ours to narrow" and re-declared `f64` while leaving the
    values byte-identical — a payload stating a width its text does not have,
    which is the one thing the marker exists to prevent.
    """
    once = stamp_journal_embedding(_payload(_vector()), creating=True)
    assert once[JOURNAL_VECTOR_WIDTH_KEY] == JOURNAL_VECTOR_WIDTH_F32

    twice = stamp_journal_embedding(dict(once), creating=False)

    assert twice[JOURNAL_VECTOR_WIDTH_KEY] == JOURNAL_VECTOR_WIDTH_F32, (
        "a second application of the single declared seam flipped the "
        "declaration to f64 — these values are that seam's own output, so the "
        "width it declares must not move")
    assert twice["embedding"] == once["embedding"], (
        "the second application must leave the values untouched")


def test_a_preserved_vector_still_declares_f64_even_beside_a_stale_f32():
    """The idempotency guard must not swallow the verbatim arm.

    The guard returns early on an `f32` marker, so if it were placed ABOVE the
    verbatim arm a preserved vector carrying a stale `f32` would keep it — and
    #5004 round-7's population would be declared narrowed while it must stay
    raw. Order is the assertion.
    """
    stale = _payload(_vector(), embedding_preserved=True,
                     **{JOURNAL_VECTOR_WIDTH_KEY: JOURNAL_VECTOR_WIDTH_F32})
    stamped = stamp_journal_embedding(stale, creating=False)

    assert stamped[JOURNAL_VECTOR_WIDTH_KEY] == JOURNAL_VECTOR_WIDTH_F64, (
        "a preserved vector declared f32 — the guard ran before the verbatim arm")


def test_a_stale_f32_marker_never_survives_a_vector_replaced_after_the_stamp():
    """#11835 review (P0): the guard must VERIFY the text, never trust the marker.

    `EventAPI.add_point` is a home where the vector is replaced AFTER the stamp:
    `_point` stamps its own (narrowed, `f32`) vector, `p.update(fields)` swaps
    in the caller's, and the re-stamp returned on the `f32` marker alone. The
    caller's off-grid text was therefore declared `f32` — the one thing the
    marker exists to prevent (*"a caller-set value would describe a width the
    payload's text does not have"*).

    The seam-level reproduction of that home: stamp our own vector, replace the
    values with a caller's, stamp again. Written to RED if the arm goes back to
    returning on the marker without asking the text (measured: it does).
    """
    stamped = stamp_journal_embedding(_payload(_vector()), creating=True)
    assert stamped[JOURNAL_VECTOR_WIDTH_KEY] == JOURNAL_VECTOR_WIDTH_F32, (
        "premise: the seam declared our own vector f32")
    caller = [0.1234567890123, 2.718281828459045, 3.141592653589793,
              float(np.float32(0.5))]

    stamped["embedding"] = list(caller)          # a writer replacing the vector
    again = stamp_journal_embedding(stamped, creating=False)

    assert again[JOURNAL_VECTOR_WIDTH_KEY] == JOURNAL_VECTOR_WIDTH_F64, (
        "an off-grid caller vector kept the `f32` stamp written for the SERVER "
        "vector it replaced — a payload declaring a width its text does not "
        "have")
    assert again["embedding"] == caller, (
        "a replaced vector belongs to its caller: leave it exactly as written, "
        "never narrowed into a claim about the embedder")


def test_a_stale_f32_marker_still_stands_when_the_text_really_is_f32():
    """…and the verification does not re-open the idempotence bug it guards.

    The arm exists because a second stamp over its OWN output must not flip
    `f32` -> `f64`. Verifying the text must therefore still accept that output —
    asserted here on the exact values a first stamp produces, so a future
    tightening of the predicate cannot silently restore the flip.
    """
    once = stamp_journal_embedding(_payload(_vector()), creating=True)
    assert once[JOURNAL_VECTOR_WIDTH_KEY] == JOURNAL_VECTOR_WIDTH_F32

    # The predicate is asked directly, so this pins the VERIFICATION and not
    # only the arm's observable outcome.
    assert _is_shortest_f32_text(once["embedding"]), (
        "the seam's own narrowed output failed its own f32-text test — the "
        "idempotence arm would flip it back to f64")
    assert not _is_shortest_f32_text(_vector()), (
        "a WIDENED float32 vector passed the shortest-text test — the test is "
        "not discriminating between the seam's output and its input")


def test_the_idempotence_holds_at_the_top_of_the_float32_range():
    """#11835 review (P2): the range gate must ask "is this a float32?", not
    "is this ≤ the float32 maximum?".

    The largest float32's SHORTEST DECIMAL is `3.4028235e38`, and as a float64
    that is **greater** than `finfo(float32).max` (`3.4028234663852886e38`). A
    gate written against the binary maximum therefore rejects this seam's own
    output for that one value, falls through to the quantiser — which refuses it
    in turn — and re-declares `f64` over byte-identical values: exactly the
    flip the idempotence arm exists to prevent, reachable only at the top of the
    range (measured: no other float32 in the top 20,000 trips it).
    """
    top = float(np.finfo(np.float32).max)
    once = stamp_journal_embedding(_payload([top]), creating=True)

    assert once[JOURNAL_VECTOR_WIDTH_KEY] == JOURNAL_VECTOR_WIDTH_F32, (
        "the seam narrowed the largest float32 but did not declare it f32")
    assert once["embedding"] == [float(str(np.float32(top)))]
    assert once["embedding"][0] > top, (
        "premise: the shortest DECIMAL of float32 max is greater than float32 "
        "max as a float64 — that is what makes a binary-max gate wrong")

    twice = stamp_journal_embedding(dict(once), creating=False)

    assert twice[JOURNAL_VECTOR_WIDTH_KEY] == JOURNAL_VECTOR_WIDTH_F32, (
        "a second stamp flipped float32 max's own text back to f64 — the "
        "range gate is comparing against the binary maximum instead of asking "
        "whether the value is a float32 at all")
    assert twice["embedding"] == once["embedding"]


def test_the_f32_text_test_fails_closed_on_what_the_seam_cannot_have_written():
    """`False` for every shape the narrowing never produces (#11835 review, P3).

    Not reachable through `stamp_journal_embedding` — it normalises the payload
    first — but the predicate is the thing `f32` is now verified BY, so it must
    never answer `True` in the PERMISSIVE direction on an input it cannot read:
    `bytes` is an iterable of INTS, so `b"\\x01\\x02"` would otherwise count as
    the on-grid vector `[1.0, 2.0]`, and `float(10**400)` raises `OverflowError`
    rather than the `ValueError` this used to catch.
    """
    assert not _is_shortest_f32_text(b"\x01\x02"), (
        "bytes were read as numbers — two ints counted as an on-grid vector")
    assert not _is_shortest_f32_text("0.5")
    assert not _is_shortest_f32_text({"a": 1.0})
    assert _is_shortest_f32_text([10 ** 400]) is False, (
        "an int beyond float range must answer False, not raise OverflowError")
    assert not _is_shortest_f32_text([float("nan")])
    assert not _is_shortest_f32_text([float("inf")])
    assert not _is_shortest_f32_text([1e300])
    # …and the on-grid answer still holds for genuine numbers, so the refusals
    # above are not the predicate simply saying no to everything.
    assert _is_shortest_f32_text([0.5, 0.25, 1.0, -0.0])
    assert _is_shortest_f32_text([])


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
    `embedding_verbatim` (that door refuses the marker as forged, and never mints
    it — the only marker it mints is the WIDTH), so "the marker is absent" is not
    evidence that the vector is
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
    (`stored_form.node_statement`) — so narrowing keeps the float32 bits the
    store holds and shortens the text. The width declaration is what moves.

    ⛔ It is NOT a no-op on the float64 (#11835 review round 3, F1): the payload's
    value becomes the shortest decimal for those bits (`0.001230153371579945` →
    `0.0012301534`), which is OFF the grid, which is exactly why the record↔cache
    comparison still narrows the truth side (`stored_form.narrow_to_stored_width`,
    #8615 — see `consistency._as_f32`)."""
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
    """An OWNED `None` must not sit beside a marker that outlived its vector.

    ⛔ The fixture SEEDS the marker on purpose (#11835 review round 4, F2). It
    used not to, so the `pop` this row exists to pin was a no-op for its own
    payload and the assertion was vacuous: measured, replacing
    `_drop_journalled_embedding`'s `payload.pop(JOURNAL_VECTOR_WIDTH_KEY, None)`
    with `pass` left the whole file at `35 passed`. A row whose named mutation
    does not red it is not a pin. The shape it now covers is the reachable one
    — a payload that ALREADY declares a width (an `id=`-branch re-emit over a
    marked record) whose vector then turns out unusable.
    """
    stamped = stamp_journal_embedding(
        _payload([float("nan"), 0.5],
                 **{JOURNAL_VECTOR_WIDTH_KEY: JOURNAL_VECTOR_WIDTH_F32}),
        creating=False)
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
    """The homes `embedding_width` lives in.

    Each one is a place where an undeclared payload key would LEAK: onto a node
    (`_POINT_HANDLED` / `_META_KEYS` / `_SERVER_MANAGED_PROPS`), into the
    record's `props` jsonb tail (`_RECORD_ONLY_KEYS`), or into a caller's reach.

    There are EIGHT, and this test asserts SIX of them. The other two are pinned
    where each can actually redden, and are NOT covered by anything below:

    * `sdk._sanitize_props` -> `test_a_caller_cannot_forge_the_width` (the next
      test down in this file);
    * `api.EventAPI.add_point`'s forged-key refusal ->
      `test_journal_embedding_5004.py::test_eventapi_rejects_the_server_managed_journal_markers`.
      That set is a function-local literal, so no test can assert its
      membership; the refusal is driven through the door instead (#11835 review
      F1 — while it was uncovered, deleting the `api.py` entry left every row
      GREEN).
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

    ``sdk._journal_event_raw_write`` builds its own payload; the seam's width
    declaration for it happens ONE layer down, in `_emit_event`'s
    `id=`/`**extra` branch, because `_journal_entity_event` hands that payload
    to `_emit_event` (~130 lines above). #11835 review finding 2: an earlier
    revision of this file claimed the door "does not pass through
    `_emit_event`'s stamp" and gave it its own stamp — which was dead code (its
    output overwritten by the emit branch), and the pin below could not fail
    for the reason its docstring gave. The door stamp is gone; the pin now
    asserts the end-to-end behaviour an emit-branch mutation really breaks.

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
    float32 — or a value read back off the node, and it reaches the journal
    through `_journal_entity_event` → `_emit_event`'s `id=`/`**extra` branch.

    #11835 review finding 2: this door used to carry its OWN stamp, whose
    comment said the payload "does not pass through `_emit_event`'s stamp" —
    false, and the stamp was dead code. Measured at `d81169a9` (the reviewer's
    mutations, each reverted):

      * delete the DOOR's stamp        → ``31 passed`` — nothing reds;
      * delete the EMIT BRANCH's stamp → ``2 failed``.

    So the emit branch owns the door. The door's stamp is deleted (one stamp,
    one home); this row is now the end-to-end behaviour — delete the emit
    branch's stamp and it reds, which is what it always claimed to pin. The
    sibling row that pins the door's own hand-off to that branch is
    `test_the_event_recorded_door_hands_its_payload_to_the_emit_stamp`.

    Both halves are asserted — the DECLARATION and the NARROWING — because a
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


# ── 8. the producers that reach `_emit_event`'s `extra` branch ───────────────
#
# #11835 review F1 (P1). `_emit_event` stamped only its `point=` payload, so the
# `id=`/`**extra` branch journalled a vector with NO width: `_session_event_write`
# and `_embedding_repair_attempt` (both `EventRecorded`) measured 8,455 chars and
# `embedding_width` ABSENT, and `update_point` splats a caller vector onto the
# same top level for `PointRevised`. "Absent means PRE-CHANGE / FOREIGN record"
# was therefore false for records written AFTER the change.


def _emit_record(tmp_path, **extra):
    """Drive the REAL `_emit_event` `id=` branch and return the record written.

    `id=` and no `point=` is exactly the shape both live producers use; the
    journal is a `tmp_path` file, so this stays hermetic (no DB, no graph, no
    network) like the door helper above.
    """
    from tortoise.sdk import TortoiseSDK

    journal = tmp_path / "emit.jsonl"
    sdk = TortoiseSDK(str(tmp_path / "emit.db"), event_log_path=str(journal))
    try:
        sdk._emit_event("EventRecorded", id="evt-11821-extra", **extra)
    finally:
        with contextlib.suppress(Exception):
            sdk.close()

    records = [json.loads(ln) for ln in
               journal.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(records) == 1, f"expected exactly one record, got {records!r}"
    assert records[0]["type"] == "EventRecorded"
    return records[0]


def test_the_emit_event_extra_branch_declares_its_width(tmp_path):
    """A vector that arrives through `extra` must declare its width too.

    This is the branch `_session_event_write` and `_embedding_repair_attempt`
    journal through. Delete the stamp on that branch and this reds while every
    other row in this file stays green — the population was open precisely
    because no pin drove this spelling.
    """
    widened = _vector()
    record = _emit_record(tmp_path, embedding=list(widened),
                          eventId="evt-11821-extra")

    assert record[JOURNAL_VECTOR_WIDTH_KEY] == JOURNAL_VECTOR_WIDTH_F32, (
        "the emit_event extra branch journalled a vector with no width declared")
    assert _f32_bits(record["embedding"]) == _f32_bits(widened)
    assert len(_text(record["embedding"])) < len(_text(widened)), (
        "the branch's vector must be stored at the embedder's float32, not the "
        "widened float64 repr the seam exists to remove")


def test_the_emit_event_extra_branch_leaves_a_verbatim_vector_raw(tmp_path):
    """The seam's own verbatim arm decides here too — not a blanket narrowing.

    `update_point` splats a CALLER-supplied vector onto `PointRevised`'s top
    level with `embedding_verbatim=true` (#5004 round-7: a caller-owned vector
    keeps its RAW form). The new stamp must therefore declare `f64` and touch
    nothing.

    ⛔ THE FIXTURE IS GRID-EXACT ON PURPOSE (#11835 review round 3, F2). It used
    to be `[0.1, 1/3, 2.718281828459045]`, which is OFF the grid — so
    `shortest_float32_vector` returned `None`, the seam declared `f64` and left
    the values alone with the `is_verbatim` arm present OR DELETED: a pin that
    could not fail for the arm its docstring names. Measured by neutralising
    `stored_form.is_verbatim`: off-grid → `f64`/raw either way; grid-exact →
    `f64`/raw with the arm, `f32`/narrowed to `[0.1, -0.25, 0.33333334]`
    without it. The sibling `test_a_verbatim_vector_is_never_narrowed` above
    measured this same trap and adopted this same shape.

    (`float(str(np.float32(v)))` does NOT discriminate — it yields the plain
    literal `0.1`, still off-grid. Hand over the widening.)
    """
    caller = [float(np.float32(v)) for v in (0.1, -0.25, 1.0 / 3.0)]
    assert np.all(np.asarray(caller, dtype=np.float32).astype(np.float64)
                  == np.asarray(caller, dtype=np.float64)), (
        "the fixture must be ON the float32 grid, or this row passes without "
        "the verbatim arm — a pin that cannot fail")
    record = _emit_record(tmp_path, embedding=list(caller), eventId="evt-verbatim",
                          embedding_verbatim=True)

    assert record["embedding"] == caller, "a caller-owned vector must stay RAW"
    assert record.get(JOURNAL_VECTOR_WIDTH_KEY) == JOURNAL_VECTOR_WIDTH_F64, (
        "a caller-owned vector on the extra branch declared no width at all "
        "(absent is reserved for pre-change records)")


def _added_point_with_caller(tmp_path, caller):
    """Drive `_point` → `add_point` over a fixed SERVER vector; return the journalled point.

    Shared by the two caller-vector shapes below so the PREMISE assertion (`_point`
    really stamped the server vector `f32`, i.e. the stale marker this pin is about
    exists at all) is stated once and cannot drift apart between them. A pin whose
    premise stops holding passes without exercising anything.
    """
    from unittest import mock

    from tortoise.api import EventAPI, provenance
    from tortoise.embeddings import EMBEDDING_DIM
    from tortoise.log import EventLog

    events = tmp_path / "events"
    events.mkdir()
    log = EventLog(events / "events.jsonl")
    # An ON-GRID server vector at the store's own width, so `_point`'s stamp
    # really declares `f32` — the stale marker this pin is about only exists
    # when the first stamp fires. (`_degrade_to_width` DROPS a width-mismatched
    # vector, so a short probe vector would make this pin vacuous: no vector,
    # no first stamp, no stale marker.)
    server = [0.5] + [0.25] * (EMBEDDING_DIM - 1)
    with mock.patch("tortoise.embeddings.compute_embedding",
                    lambda text: list(server)):
        from tortoise.sdk import TortoiseSDK

        sdk = TortoiseSDK(str(tmp_path / "width-carry.db"))
        try:
            api = EventAPI(log, initiated_by="extractor",
                           projection=sdk._get_proj())
            # THE PREMISE, asserted rather than described (#11835 review, P2):
            # `_point` is called exactly as `add_point` calls it. If it stamped
            # no `f32` — an unavailable embedder, a width `_degrade_to_width`
            # drops — there is no stale marker for the door to carry forward
            # and every assertion below would hold WITHOUT exercising the
            # defect: a regression guard that silently stops guarding.
            served = api._point("caller vector probe",
                                provenance("src:1", [0, 4], "q"))
            assert served[JOURNAL_VECTOR_WIDTH_KEY] == JOURNAL_VECTOR_WIDTH_F32, (
                "premise: `_point` did not stamp the SERVER vector f32 — this "
                "pin would pass without exercising the defect")
            api.add_point("caller vector probe", provenance("src:1", [0, 4], "q"),
                          embedding=list(caller))
        finally:
            sdk.close()

    recorded = [json.loads(ln) for ln in
                (events / "events.jsonl").read_text(encoding="utf-8").splitlines()
                if ln.strip()]
    added = [e for e in recorded if e.get("type") == "PointAdded"]
    assert added, "EventAPI did not journal a PointAdded"
    return added[0]["point"]


def test_add_point_does_not_keep_the_servers_width_over_a_caller_vector(tmp_path):
    """#11835 review F2 (P1): the marker must die with the vector it described.

    `EventAPI._point` stamps the SERVER's vector; `add_point` then splats the
    caller's `fields` over it. Before the fix the second stamp hit
    `_stamp_journal_vector_width`'s idempotency guard ("already `f32` ⇒ this is
    mine") and returned early instead of re-deciding the width from the values.

    ⚠️ THIS ROW NO LONGER DISCRIMINATES THE `p.pop(...)` — and it is kept because
    of what it does discriminate. Since `61a684bf7f` the seam re-decides the width
    from the values, so an OFF-GRID caller vector is refused `f32` whether or not
    the pop ran: remove the pop and this row stays green. What reds here is the
    seam's decision rule. The pop's own load-bearing shape — a caller whose text
    is already the seam's shortest float32 round-trip, where a stale `f32` is
    HONOURED and nothing but the pop removes it — is pinned by the sibling below
    (`test_add_point_does_not_keep_the_servers_width_over_an_f32_text_caller_vector`),
    which does red with the pop removed (`'f32' == 'f64'`, measured).
    """
    caller = [0.1, 1 / 3, 2.718281828459045]
    point = _added_point_with_caller(tmp_path, caller)

    assert point["embedding"] == caller, (
        "the caller's vector must be journalled exactly as supplied")
    assert point[JOURNAL_VECTOR_WIDTH_KEY] == JOURNAL_VECTOR_WIDTH_F64, (
        "the server's `f32` marker survived the vector it described — a caller "
        "vector is now declared narrowed")


def test_add_point_does_not_keep_the_servers_width_over_an_f32_text_caller_vector(
        tmp_path):
    """The shape on which `api.add_point`'s `p.pop(...)` is LOAD-BEARING.

    A caller vector whose text is ALREADY the shortest float32 round-trip of its
    own bits (`[0.1, 0.33333334, 2.7182817]`) is exactly what the seam's
    idempotency guard reads as "my own output". So a stale `f32` riding in from
    `_point` is HONOURED — values left as they are, width declared `f32` — unless
    the door removes the marker between the swap and the re-stamp.

    Measured (mutation): delete `p.pop(JOURNAL_VECTOR_WIDTH_KEY, None)` in
    `tortoise/api.py::EventAPI.add_point` and this row reds with
    `'f32' == 'f64'`; the off-grid sibling above stays GREEN under the same
    mutation, which is why the discriminating shape has its own pin.
    """
    import numpy as np

    # shortest-float32 text is what the seam's quantiser itself emits, so these
    # values are on the guard's "already mine" side of the test.
    caller = [float(str(np.float32(v))) for v in (0.1, 1 / 3, 2.718281828459045)]
    point = _added_point_with_caller(tmp_path, caller)

    assert point["embedding"] == caller, (
        "the caller's vector must be journalled exactly as supplied")
    assert point[JOURNAL_VECTOR_WIDTH_KEY] == JOURNAL_VECTOR_WIDTH_F64, (
        "a caller vector in shortest-float32 text was declared `f32` — the "
        "server's stale marker was honoured over the values (the pop in "
        "`api.add_point` is what must remove it)")


# ── 10. the OTHER journal door: `EventAPI._emit` ─────────────────────────────
#
# #11835 review finding 1 (P2). `EventAPI.add_point` stamps its vector AND
# refuses a caller-named marker; `_emit` — the funnel `add_event(**extra)` and
# `emit_belief(**payload)` splat into — did neither, so a NEW record minted
# there carried `embedding` with `embedding_width` ABSENT (a state
# `JOURNAL_VECTOR_WIDTH_KEY` reserves for PRE-CHANGE / FOREIGN records) and
# accepted a caller's `f32` beside an off-grid float64 vector (a marker that
# LIES). Measured at the pre-fix head, driving `EventAPI(log,
# initiated_by="extractor")` against a temp journal: `evt-1` (a float32-grid
# vector) journalled with `embedding_width` ABSENT, `evt-2` (a full float64
# vector + a caller `f32`) journalled with `embedding_width: f32`.
#
# Scope honesty, stated the way the review stated it: no in-tree caller passes a
# vector through this door today (`ingest.py` is the only caller and passes
# none), so this closed an ENUMERATION hole rather than a live mis-journal — the
# class this repo treats as the real defect (a declared invariant a live door
# does not enforce).


def _emit_door_record(tmp_path, **extra):
    """Drive the REAL `EventAPI.add_event` door and return the record written.

    `projection=None` keeps this hermetic (no DB, no graph) and is still the
    production shape for the extraction/ingest lanes: the `#5475` pack gate
    abstains on a projection-less lane by design.
    """
    from tortoise.api import EventAPI

    events = tmp_path / "emit-door"
    events.mkdir(exist_ok=True)  # the refusal row drives this helper twice
    log = EventLog(events / "events.jsonl")
    api = EventAPI(log, initiated_by="extractor", projection=None)
    api.add_event("evt-11821-emit", "session", **extra)

    records = [json.loads(ln) for ln in
               (events / "events.jsonl").read_text(encoding="utf-8").splitlines()
               if ln.strip()]
    assert len(records) == 1, f"expected exactly one record, got {records!r}"
    assert records[0]["type"] == "EventRecorded"
    return records[0]


def test_the_eventapi_emit_door_declares_its_width(tmp_path):
    """A vector that reaches `_emit` must declare its width — no new silent row.

    Measured (mutation): delete the `if "embedding" in event:` stamp in
    `tortoise/api.py::EventAPI._emit` and this reds with `KeyError:
    'embedding_width'`; before the fix it redded nothing, because no suite drove
    this door with a vector.
    """
    widened = _vector()
    record = _emit_door_record(tmp_path, embedding=list(widened))

    assert record[JOURNAL_VECTOR_WIDTH_KEY] == JOURNAL_VECTOR_WIDTH_F32, (
        "EventAPI._emit journalled a vector with no width declared — a NEW "
        "record whose marker is absent, which the seam reserves for a "
        "pre-change/foreign one")
    assert _f32_bits(record["embedding"]) == _f32_bits(widened)
    assert len(_text(record["embedding"])) < len(_text(widened)), (
        "the door's vector must be stored at the embedder's float32, not the "
        "widened float64 repr the seam exists to remove")


def test_the_eventapi_emit_door_declares_f64_for_a_vector_it_cannot_narrow(
        tmp_path):
    """The other half: this door must not claim `f32` over values it did not get.

    A vector with an element float32 cannot represent is journalled UNTOUCHED
    and says `f64`, so a later width/format migration cannot narrow a population
    that was never declared as foreign. (Pre-fix the marker was ABSENT here, so
    this row reds on the assertion, not on a KeyError.)
    """
    source = [1e-300, 0.5, -1e-300]
    record = _emit_door_record(tmp_path, embedding=list(source))

    assert record["embedding"] == source, "the values must be left ALONE"
    assert record[JOURNAL_VECTOR_WIDTH_KEY] == JOURNAL_VECTOR_WIDTH_F64


def test_a_caller_cannot_name_a_record_shaped_payload_at_the_funnel_doors(
        tmp_path):
    """The vector rule is ONE NESTING LEVEL DEEP, so the nested shape is refused.

    #11835 review round 4 (F1), measured at `545a2fcaf2`: `_emit`'s two gates read
    the ENVELOPE, and `add_event`/`emit_belief` splat a caller's keys onto it — so
    `emit_belief("PointAdded", point={...})` journalled a record the replay
    folds, with neither half applied:

        PointAdded | p-nested-1 | marker: 'f32' | values: [0.1234567890123, 0.3333...]
        PointAdded | p-nested-2 | marker: None  | values: [0.001230153379945, 0.2987...]

    The first is the marker LIE (#5004 round-7's must-never-narrow population,
    declared `f32` over 17-digit values); the second is a NEW record in the state
    `JOURNAL_VECTOR_WIDTH_KEY` reserves for PRE-CHANGE / FOREIGN records.

    The funnel must NOT fix this by stamping the nested payload instead: that
    stamp is `creating=False`, which DROPS `embedding_model` /
    `embedding_revision` / `embedding_text_hash` — it would strip the origin
    attestation off every ordinary `PointAdded` (R1).

    ⛔ ROUND 5 (`6aea53bb6`): the first fix refused it at TWO doors and CALLED
    THAT THE ENUMERATION. It was not — there are FOUR doors that reach `_emit`
    with caller keys, and the two it missed were worse than the two it closed:

        add_object("n", "thing", point={...})   -> the fold DISPATCHED on the
            caller's nested record and folded it (id `p-hijack`) with the marker
            lie, discarding the Object it was asked to register
        add_point("real", prov, point={...})    -> journalled the caller's
            record NESTED inside the door's own, and returned the id of a point
            that never folds

    So the gate moved into `_emit` itself and the record payload moved to a
    POSITIONAL channel (`*record`), which no `(**kwargs)` splat can bind. This
    test therefore drives ALL FOUR doors and, separately, the record channel
    directly: a caller that reaches `point` any other way is refused whatever
    name it arrives under.
    """
    from tortoise.api import EventAPI, provenance
    from tortoise.log import EventLog

    events = tmp_path / "nested"
    events.mkdir(parents=True)
    api = EventAPI(EventLog(events / "events.jsonl"),
                   initiated_by="extractor", projection=None)
    nested = {"id": "p-nested", "content": "c",
              "embedding": [0.1234567890123, 1 / 3],
              JOURNAL_VECTOR_WIDTH_KEY: JOURNAL_VECTOR_WIDTH_F32}

    # `emit_belief` refuses BEFORE its best-effort `try`: a malformed payload is
    # not the I/O failure that arm exists to swallow, and the raise is the only
    # signal the caller gets (nothing was journalled).
    with pytest.raises(ValueError, match="RECORD-SHAPED"):
        api.emit_belief("PointAdded", point=dict(nested))
    with pytest.raises(ValueError, match="RECORD-SHAPED"):
        api.add_event("evt-nested", "AgentSession", point=dict(nested))
    # …and the two doors the round-4 fix MISSED, which is what round 5 added.
    with pytest.raises(ValueError, match="RECORD-SHAPED"):
        api.add_object("hijack", "thing", point=dict(nested))
    with pytest.raises(ValueError, match="RECORD-SHAPED"):
        api.add_point("a real point", provenance("src:9", [0, 4], "q"),
                      point=dict(nested))

    # Nothing reached the journal — a refusal after the append would leave a
    # record the replay folds (`_emit`'s own ordering argument). This is what
    # makes the `add_object` case a REFUSAL rather than a hijack: the caller's
    # point never becomes a foldable `ObjectRegistered`.
    assert not (events / "events.jsonl").exists() or not (
        events / "events.jsonl").read_text().strip(), (
        "a refused payload must leave NOTHING to replay")

    # The record CHANNEL itself: `point` reaches `_emit` positionally only, so a
    # keyword one is refused even at the funnel, and a second record is a
    # programming error rather than a silently-ignored extra.
    with pytest.raises(ValueError, match="RECORD-SHAPED"):
        api._emit("PointAdded", point=dict(nested))
    with pytest.raises(TypeError, match="at most ONE record payload"):
        api._emit("PointAdded", dict(nested), dict(nested))
    assert not (events / "events.jsonl").exists() or not (
        events / "events.jsonl").read_text().strip()

    # And the doors that BUILD the record still work, nested shape and all.
    api.add_point("a real point", provenance("src:1", [0, 4], "q"))
    records = [json.loads(ln) for ln in
               (events / "events.jsonl").read_text().splitlines() if ln.strip()]
    added = [r for r in records if r.get("type") == "PointAdded"]
    assert len(added) == 1, "`add_point` must keep journalling its nested payload"
    assert added[0]["point"]["content"] == "a real point", (
        "the record door's own nested payload must reach the journal intact")
    assert "point" not in added[0]["point"], (
        "the record door's payload must not carry a nested record")
    # ⚠️ NOT asserting the marker here: whether `_point` had a server vector to
    # stamp depends on the embedder being installed, and this suite runs both
    # ways. The width rule itself is pinned by the rows above.


def test_the_record_channel_cannot_carry_an_envelope_name(tmp_path):
    """#11835 review: the ENVELOPE family was half-closed — `_emit`'s KEYWORD
    payload refused it, its POSITIONAL record did not.

    The record belongs to `add_point` / `add_operator`, which is why the first
    fix checked it for `point` only. But the replay's `_norm` is
    `{**ev, **ev["point"]}`, so a caller keyed `type=` INSIDE the record is
    spliced OVER the envelope — it is the record's identity on replay, not a
    payload field. Measured at `95b98e6cf1`: `add_point("hello", prov,
    type="PointDeleted")` was ACCEPTED, journalled `"type":"PointAdded"` with
    the caller's `"type":"PointDeleted"` nested inside, returned a point id,
    and the replay dispatched on `PointDeleted` — an arm that matches nothing,
    so the point was lost SILENTLY. `ts=` / `initiated_by=` / `event_id=`
    correspondingly forged the replay's when / by-whom / id.
    """
    from tortoise.api import EventAPI, provenance
    from tortoise.log import EventLog

    events = tmp_path / "record-envelope"
    events.mkdir(parents=True)
    journal = events / "events.jsonl"
    api = EventAPI(EventLog(journal), initiated_by="extractor", projection=None)

    for key, value in (("type", "PointDeleted"),
                       ("ts", "1999-01-01T00:00:00Z"),
                       ("initiated_by", "somebody"),
                       ("agent_id", "agent-9"),
                       ("actor_user_id", "user-9"),
                       ("event_id", "forged-event")):
        with pytest.raises(ValueError, match="ENVELOPE"):
            api.add_point("hello", provenance("src:7", [0, 5], "q"),
                          **{key: value})
    assert not journal.exists() or not journal.read_text().strip(), (
        "a refused record must leave NOTHING to replay")

    # …and the door's OWN record still lands, envelope minted by the funnel.
    pid = api.add_point("a real point", provenance("src:8", [0, 5], "q"))
    added = [json.loads(ln) for ln in journal.read_text().splitlines()
             if ln.strip()]
    assert len(added) == 1, "the ordinary door must still journal exactly one"
    assert added[0]["type"] == "PointAdded", (
        "the funnel's own type must not be shadowed by the record")
    assert added[0]["point"]["id"] == pid
    assert not set(added[0]["point"]) & {
        "type", "ts", "initiated_by", "agent_id", "actor_user_id",
        "event_id"}, "the door's record must carry no envelope name"


def test_the_eventapi_emit_door_refuses_a_caller_named_marker(tmp_path):
    """The marker is writer-minted at this door too — `add_point`'s rule, one over.

    Two shapes, both refused BEFORE the journal append: (1) a bare marker with
    no vector for it to describe; (2) the measured "marker lies" form — a
    full-float64 off-grid vector beside a caller's `f32`. Pre-fix both were
    ACCEPTED and (2) journalled a record declaring `f32` over values that are
    not float32.
    """
    with pytest.raises(ValueError):
        _emit_door_record(tmp_path, embedding_width=JOURNAL_VECTOR_WIDTH_F32)

    off_grid = [0.1234567890123456, 2.718281828459045]
    with pytest.raises(ValueError):
        _emit_door_record(tmp_path, embedding=off_grid,
                          embedding_width=JOURNAL_VECTOR_WIDTH_F32)


def test_the_event_recorded_door_hands_its_payload_to_the_emit_stamp(tmp_path):
    """One stamp, one home — the door must NOT carry a stamp of its own.

    #11835 review finding 2's remedy, pinned from the other side: the door's
    payload reaches `_emit_event`'s `id=`/`**extra` branch UNSTAMPED, so the
    branch's stamp is the only one that decides the width. Re-adding a stamp at
    `sdk._journal_event_raw_write` reds this row (the marker would arrive
    pre-set), which is exactly the dead block the finding removed — its output
    was overwritten a moment later, so no end-to-end assertion could see it.
    """
    from tortoise.sdk import TortoiseSDK

    journal = tmp_path / "handoff.jsonl"
    sdk = TortoiseSDK(str(tmp_path / "handoff.db"), event_log_path=str(journal))
    seen = {}

    def _capture(type_, payload=None, **kwargs):
        seen["type"] = type_
        seen.update(kwargs)
        return True

    vector = [0.5, 0.25]
    try:
        sdk._emit_event = _capture
        committed = sdk._journal_event_raw_write(
            "evt-11821-handoff", {"content": "a session event"}, graph=None,
            embedding=list(vector))
    finally:
        with contextlib.suppress(Exception):
            sdk.close()

    assert committed is True
    assert seen.get("type") == "EventRecorded"
    assert seen.get("embedding") == vector, (
        "the door must HAND the vector to the emit branch, not consume it")
    assert JOURNAL_VECTOR_WIDTH_KEY not in seen, (
        "the door pre-stamped the payload — its width declaration must live in "
        "exactly one place (`_emit_event`'s `id=`/`**extra` branch), or the "
        "dead second stamp is back and no end-to-end pin can see it")


def test_add_object_is_the_third_door_and_guards_both_of_its_families(tmp_path):
    """#11948 (found at `07421bde27`): `add_object` splats `**props` and guarded none.

    The two families are refused here, both BEFORE the entity lock and before
    the journal append — every refusal below leaves the log EMPTY, so there is
    nothing for the replay to fold:

    * `point=` — the record-shaped payload from #11835 review round 4. At this
      head the call RAISES and the journal stays EMPTY; it was at
      `07421bde27` that it journalled. Nothing folded the caller's vector in
      either state: an Object's vector comes from its NAME, so the off-grid
      float64 and its `embedding_width: "f32"` marker (the #5004 round-7
      must-never-narrow population) stayed on the journal line and became no
      node property. What DID land is the id — `_norm` splices a dict `point`
      into the record, so the replay MERGEs an Object on the caller's id.
    * `type=` — the envelope collision. `_emit` spreads the payload AFTER the
      envelope, so the caller's `type` WINS and the record is re-typed: a
      `PointAdded` reached the replay that no record door built. The live fold
      does NOT fold it with the caller's `point` — this row passes NO `point`,
      so the fold SKIPS it ("skipping PointAdded with non-dict point") and the
      Object the door was asked to register is never created: a silent lost
      write plus a journal line claiming a `PointAdded` no door built.
    """
    from tortoise.api import EventAPI
    from tortoise.log import EventLog

    events = tmp_path / "add-object-door"
    events.mkdir(parents=True)
    journal = events / "events.jsonl"
    api = EventAPI(EventLog(journal), initiated_by="extractor", projection=None)

    off_grid = [0.1234567890123, 1 / 3]
    lying = {"id": "p-forged", "embedding": off_grid,
             JOURNAL_VECTOR_WIDTH_KEY: JOURNAL_VECTOR_WIDTH_F32}

    with pytest.raises(ValueError, match="RECORD-SHAPED"):
        api.add_object("obj-x", "skill", id="obj-x", point=dict(lying))
    with pytest.raises(ValueError, match="ENVELOPE"):
        api.add_object("obj-y", "skill", id="obj-y", type="PointAdded")
    with pytest.raises(ValueError, match="ENVELOPE"):
        api.add_object("obj-z", "skill", id="obj-z", event_id="forged-event")
    with pytest.raises(ValueError, match="server-managed"):
        api.add_object("obj-w", "skill", id="obj-w",
                       embedding_width=JOURNAL_VECTOR_WIDTH_F32)
    assert not journal.exists() or not journal.read_text().strip(), (
        "a refused `add_object` must leave NOTHING to replay")

    # The door still writes what it OWNS, with its envelope minted by the
    # funnel — the guard must not cost the door its ordinary shape.
    api.add_object("a real object", "skill", id="obj-ok",
                   canonical_name="A Real Object")
    records = [json.loads(ln) for ln in
               journal.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert [r["type"] for r in records] == ["ObjectRegistered"], (
        "the record's type must be the door's own, never a caller's")
    assert records[0]["id"] == "obj-ok", (
        "`id=` is the OBJECT's id and must keep reaching the payload")
    assert records[0]["canonical_name"] == "A Real Object", (
        "an ordinary extra prop must survive the guard")
    assert records[0]["event_id"] and records[0]["event_id"] != "forged-event", (
        "the envelope id is the funnel's to mint")


def test_the_collision_guard_covers_the_keyword_bound_envelope_names(tmp_path):
    """`corrects=` / `actor=` never reach the payload — they bind `_emit`'s keywords.

    So a payload-only check at the funnel cannot see them, and `add_event`'s
    `**extra` hands them straight to the envelope's provenance edge: a caller
    could forge `corrects` (the edge that says this record supersedes another)
    or `actor_user_id`. The doors check the CALLER's dict, not the built
    payload, which is the only place both families are visible at once.
    """
    from tortoise.api import EventAPI
    from tortoise.log import EventLog

    events = tmp_path / "kwargs-door"
    events.mkdir(parents=True)
    journal = events / "events.jsonl"
    api = EventAPI(EventLog(journal), initiated_by="extractor", projection=None)

    with pytest.raises(ValueError, match="ENVELOPE"):
        api.add_event("evt-1", "session", corrects="evt-forged")
    with pytest.raises(ValueError, match="ENVELOPE"):
        api.add_event("evt-1", "session", actor="someone-else")
    assert not journal.exists() or not journal.read_text().strip()


def test_emit_belief_refuses_the_marker_before_its_best_effort_arm(tmp_path):
    """#11948 F2: one class of malformed payload, ONE answer, at both doors.

    `emit_belief` swallows I/O failures on purpose (the graph write has already
    committed by the time EP returns). The marker refusal used to be raised
    inside `_emit` — i.e. INSIDE that `except` — so the belief record was
    dropped with a `logger.warning` while the identical `point=` refusal raised
    at the caller (`journal delta 0`, measured at `07421bde27`). A best-effort
    arm must not silently convert a caller's bug into a lost record: the
    refusal now happens where `point=`'s does, before the arm.
    """
    from tortoise.api import EventAPI
    from tortoise.log import EventLog

    events = tmp_path / "belief-door"
    events.mkdir(parents=True)
    journal = events / "events.jsonl"
    api = EventAPI(EventLog(journal), initiated_by="extractor", projection=None)

    with pytest.raises(ValueError, match="server-managed"):
        api.emit_belief("ConfidenceChanged", id="node-1",
                        embedding_width=JOURNAL_VECTOR_WIDTH_F32)
    with pytest.raises(ValueError, match="ENVELOPE"):
        api.emit_belief("ConfidenceChanged", id="node-1", type="PointAdded")
    # …and the MONEY family, which is the divergence the review of THIS fix
    # found one family over: the gate runs inside `_emit`, so the arm below
    # swallowed it and dropped the record, while `add_object` raised on the
    # identical payload. Same predicate at the door, so the two cannot answer
    # differently for one malformed payload.
    with pytest.raises(Exception, match="money value refused"):
        api.emit_belief("ConfidenceChanged", id="node-1", amountMinor=25_000_000)
    with pytest.raises(Exception, match="money value refused"):
        api.add_object("an object", "skill", id="obj-money",
                       amountMinor=25_000_000)
    # …and the KIND family, found the same way one family over from the money
    # fix: the two gates `_emit` runs before its commit point are exactly the
    # two this arm used to swallow. Both are pinned at BOTH doors so the next
    # reader does not have to trust the enumeration in the docstring.
    with pytest.raises(ValueError, match="pointKind must be an identifier"):
        api.emit_belief("ConfidenceChanged", id="node-1",
                        pointKind="statement\u2003")
    with pytest.raises(ValueError, match="pointKind must be an identifier"):
        api.add_event("evt-kind", "session", pointKind="statement\u2003")
    assert not journal.exists() or not journal.read_text().strip(), (
        "the refusal must not leave a half-record behind")

    # …and the door's ordinary job still lands (the best-effort posture is
    # narrowed, not removed — `test_2884`'s f5 pins the I/O arm).
    api.emit_belief("ConfidenceChanged", id="node-2", confidence=0.5)
    records = [json.loads(ln) for ln in
               journal.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert [r["type"] for r in records] == ["ConfidenceChanged"]
    assert records[0]["confidence"] == 0.5


def test_the_envelope_collision_guard_keeps_add_documents_event_id_pin(tmp_path):
    """The ONE deliberate exception, pinned so the guard cannot drift into it.

    `add_document(event_id=...)` PINS the envelope id (#125) and defaults it to
    `None` (#167 — so a partial update's `coalesce` does not wipe a live value);
    both are recorded decisions with their own tests in `tests/test_api.py`.
    `add_document` takes NAMED parameters rather than splatting a caller's
    dict, so the funnel deliberately excludes `event_id` — this row is what
    stops a later "tighten `_ENVELOPE_KEYS`" from reversing #167 while
    believing it is closing #11948.
    """
    from tortoise.api import EventAPI
    from tortoise.log import EventLog

    events = tmp_path / "document-door"
    events.mkdir(parents=True)
    journal = events / "events.jsonl"
    api = EventAPI(EventLog(journal), initiated_by="extractor", projection=None)

    api.add_document("doc-1", "Pinned", event_id="evt-1")
    api.add_document("doc-2", "Defaulted")
    records = [json.loads(ln) for ln in
               journal.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert [r["event_id"] for r in records] == ["evt-1", None], (
        "`add_document`'s event_id pin and its None default are #125/#167 "
        f"decisions, got {[r['event_id'] for r in records]!r}")


def test_the_funnel_itself_refuses_an_envelope_key_from_any_future_door(tmp_path):
    """The funnel's OWN guards, pinned directly — so they cannot rot unseen.

    The three doors above are covered, which means a mutation of `_emit`'s
    copies is invisible to them: they refuse first. That is the whole point of
    keeping the funnel's gate (a door added tomorrow inherits it, and this PR
    has already measured three times what happens when a rule lives at the
    doors only), but it also means nothing would notice a later refactor that
    deleted the funnel arm. So this row drives `_emit` itself.
    """
    from tortoise.api import EventAPI
    from tortoise.log import EventLog

    events = tmp_path / "funnel-door"
    events.mkdir(parents=True)
    journal = events / "events.jsonl"
    api = EventAPI(EventLog(journal), initiated_by="extractor", projection=None)

    with pytest.raises(ValueError, match="ENVELOPE"):
        api._emit("ConfidenceChanged", type="PointAdded")
    with pytest.raises(ValueError, match="server-managed"):
        api._emit("ConfidenceChanged", embedding_width=JOURNAL_VECTOR_WIDTH_F32)
    assert not journal.exists() or not journal.read_text().strip()


def test_the_belief_door_keeps_a_graph_outage_inside_its_own_arm(tmp_path):
    """#11948 round 2: the EAGER half must be the PURE half — no graph READ.

    `emit_belief`'s best-effort arm exists so an I/O failure never escapes
    `ep.run()` (the graph write has already committed by then). The money and
    kind refusals were moved to the door so a malformed payload is reported to
    the caller instead of being logged away — correct — but the KIND gate is not
    purely a predicate: `_refuse_unapproved_kinds` reads the whole graph once a
    payload names a pack, and it RAISES when the handle is bound but unreadable.
    Called before the arm, that read is an I/O failure ON the escape path.
    Measured at `3f5c61601` with a projection whose `g` read raises:

        {'eventKind': 'ruleRevised'}  -> ESCAPED OSError, journal lines: 0

    So the door now passes `graph_reads=False` (pure half only) and `_emit`
    re-runs the full gate inside the arm. This row pins BOTH halves: the outage
    is swallowed, and a malformed payload is still refused eagerly — a fix that
    bought the first by deleting the second would pass a one-sided test.
    """
    from tortoise.api import EventAPI
    from tortoise.log import EventLog

    class _UnreadableProjection:
        """A bound graph handle that raises on read — an outage, not an absence."""

        @property
        def g(self):
            raise OSError(5, "graph read failed (simulated outage)")

    events = tmp_path / "belief-outage"
    events.mkdir(parents=True)
    journal = events / "events.jsonl"
    api = EventAPI(EventLog(journal), initiated_by="extractor",
                   projection=_UnreadableProjection())

    # 1. The I/O half: a pack-naming kind reaches the approval read, and that
    #    read failing must be swallowed by the arm, not raised at the caller.
    api.emit_belief("ConfidenceChanged", id="node-1", eventKind="ruleRevised")

    # 2. The malformed half, unchanged and still eager: the PURE refusal runs
    #    before the arm, so a caller's bug is still reported rather than lost.
    with pytest.raises(ValueError, match="pointKind must be an identifier"):
        api.emit_belief("ConfidenceChanged", id="node-2",
                        pointKind="statement\u2003")
    with pytest.raises(Exception, match="money value refused"):
        api.emit_belief("ConfidenceChanged", id="node-3",
                        amountMinor=25_000_000)

    # 3. Nothing was journalled by the outage and nothing by the refusals.
    assert not journal.exists() or not journal.read_text().strip(), (
        "a swallowed outage and a refusal both leave NOTHING to replay")
