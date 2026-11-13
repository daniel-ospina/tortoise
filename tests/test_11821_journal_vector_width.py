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
from decimal import Decimal

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
from tortoise.percent import PercentValueError

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


def test_the_quantiser_leaves_process_wide_warning_state_alone(monkeypatch):
    """`warnings.catch_warnings()` mutates PROCESS-GLOBAL filter state and is
    documented as not thread-safe; the SDK documents concurrent writers (#5612).
    Two interleaved blocks can leave a leaked `ignore` entry behind for the life
    of the process, muting the numeric warnings that surface a bad vector.

    The range refusal is done in plain Python BEFORE any numpyd cast precisely
    so that no suppression is needed at all.

    ⛔ TWO OBSERVABLES, and the SECOND is the one that makes this pin real
    (#11859). The regression this test names is "the quantiser wraps its casts
    in `catch_warnings` + `simplefilter('ignore', RuntimeWarning)`" — and a
    restored suppression is INVISIBLE to both assertions that used to stand
    here alone: the `ignore` entry is scoped to the block, so
    `warnings.filters == before` still holds on `__exit__` (per
    <https://docs.python.org/3/library/warnings.html>, `catch_warnings`
    RESTORES the module's filters on exit — only the list identity moves), and
    the `RuntimeWarning` is swallowed before this test's own `record=True` block
    can see it.

    MEASURED before this fix, on the pre-`833272921b` shape restored (range
    refusal deleted **and** the suppression put back): `1 passed` for this row,
    and **51 passed** for the whole file — a pin that could not fail for its own
    docstring. After adding the ban below, the same mutant reds here.

    So the primary assertion asks the only question that makes the claim true:
    **did the quantiser enter `catch_warnings` at all?** A `catch_warnings` call
    on this path IS the defect, whether or not its `ignore` entry leaks — one
    interleaved with a concurrent writer is already the recorded hazard.
    """
    import warnings

    def _boom(*args, **kwargs):
        raise AssertionError(
            "the quantiser entered `warnings.catch_warnings` — the path must "
            "refuse an out-of-range value in plain Python, before any numpyd "
            "cast, so it needs no process-global suppression at all (#5612)")

    before = list(warnings.filters)
    outcomes: list = []
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")     # make the defect's warning VISIBLE
        # THE PRIMARY OBSERVABLE (#11859): the regression restores a
        # suppression block; entering one is red even if its filters are
        # restored on `__exit__` (which is why `filters == before` is blind).
        #
        # ⛔ SCOPED WITH `monkeypatch.context()`, never the shared instance
        # (#11859 review). `monkeypatch.undo()` would undo EVERY patch the
        # fixture holds — including the ones `tests/conftest.py`'s autouse
        # fixtures installed (`TORTOISE_RECORD_*`, `TORTOISE_API_URL/KEY`,
        # `TORTOISE_AUDIT_FALLBACK_DIR`, …) — so anything this test did after
        # that point would run OUTSIDE the suite's isolation, silently. A
        # context undoes only what was patched through it.
        #
        # …and it must not outlive the CALLS either: pytest's own
        # failure-reporting path calls `warnings.catch_warnings`, so a ban still
        # installed while a failure is formatted turns a clean "1 failed" into a
        # run-aborting INTERNALERROR — measured on BOTH mutants below, and it
        # hides which assertion actually failed. Hence: calls inside the
        # context, every assertion outside it.
        with monkeypatch.context() as ban:
            ban.setattr(warnings, "catch_warnings", _boom)
            # out of range: the case that warned before the plain-Python refusal
            outcomes.append(shortest_float32_vector([1e300, 0.5]))
            # underflowing to zero: the other cast-collision shape
            outcomes.append(shortest_float32_vector([1e-50, 0.5]))
            # the on-grid path must still narrow (so the assertions above cannot
            # be satisfied by a function that refuses everything)
            outcomes.append(shortest_float32_vector([float(np.float32(0.1)), 0.5]))

    assert outcomes[0] is None, (
        "an out-of-range element must be refused, not narrowed")
    assert outcomes[1] is None, (
        "an underflowing element must be refused, not collapsed to 0.0")
    assert outcomes[2] == [0.1, 0.5], (
        "the on-grid path must still narrow — otherwise the refusals above are "
        "satisfied by a function that refuses everything")
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
    store holds and shortens the text. ⛔ The declaration is NOT the only thing
    that moves, and an earlier wording here said it was ("The width declaration
    is what moves") — it contradicted the ⛔ paragraph directly below it (#11945),
    and a reader who believed the summary alone would delete the truth-side
    narrowing in `consistency._as_f32` as redundant and reinstate #8615.

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
                       ("event_id", "forged-event"),
                       # ⛔ `projection_version` is the one envelope name that is
                       # NOT identity but BEHAVIOUR: the fold reads it and skips
                       # the `#49` context strip. The doors pass it as a KEYWORD
                       # (`projection_version=2`), so a value inside the RECORD
                       # is always the caller's and `_norm`'s splice makes it
                       # win. Measured at `59fc1deb15`: `add_point(...,
                       # projection_version=0, context={...})` journalled `2` on
                       # the envelope, `0` in the record, and the fold kept the
                       # context — the caller defeating the v2 stop-write. Kept
                       # out of the shared set on purpose: a passthrough door
                       # may legitimately pass it as a keyword.
                       ("projection_version", 0)):
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
    from tortoise.api import EventAPI, provenance
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
    # …and the PERCENT family, the SAME divergence a third time: #11955's
    # `refuse_malformed_percent_in_payload` reached `_emit` and no door, so at
    # the merge head this door returned None with the journal empty while every
    # sibling door raised on the identical payload. Pinned at BOTH doors for
    # the reason the money and kind rows are: the enumeration rots, the pin
    # does not.
    with pytest.raises(PercentValueError, match="percent value refused"):
        api.emit_belief("ConfidenceChanged", id="node-1", basisPoints=20000)
    with pytest.raises(PercentValueError, match="percent value refused"):
        api.add_object("an object", "skill", id="obj-pct", basisPoints=20000)
    # …and the record facts a caller must not set on EITHER channel of the
    # funnel: no seam call here mints them, and each corrupts something
    # of its own — "of its own", not "a different thing": the three source-ref
    # keys SHARE one harm, which is why they share one clause (#11996 review
    # round 14 — this comment kept the wording `api.py` records as corrected,
    # and then contradicted itself two clauses later by grouping those three).
    # `embedding_preserved` asserts a precedent capture nothing on
    # this door can contradict (⛔ NOT an attestation suppressor here: no channel
    # of `_emit` attests a record — `_point` and `sdk._write_capture_turns` do,
    # and that is where the suppression it is known for actually happens),
    # `embedding_verbatim` ALSO writes the node's declared property (so the lie
    # survives on both sides), `evidence_fold` makes a flat record read as a fold
    # (`projection.record_is_evidence_fold`), `embeddingText` forges the
    # text↔vector identity, and the three source-ref keys put the payload's
    # edges AHEAD of the `:Source` resolution the seam performs.
    # Measured before the guard: `_emit("PointAdded", {"...": "..",
    # "embedding_preserved": True, "embedding_model": "not-a-real-model"})`
    # journalled the identity key VERBATIM — by CARRIAGE (this channel carries
    # whatever it is handed and stamps nothing), not by suppression — and
    # `_emit("PointAdded", evidence_fold=True)` — the KEYWORD channel —
    # journalled a record `record_is_evidence_fold` reads as a fold.
    for marker in ("embedding_preserved", "embedding_verbatim",
                   "evidence_fold", "embeddingText",
                   "extractedFromAll", "sourceVersion", "sourceVersions"):
        with pytest.raises(ValueError, match="server-minted record facts"):
            api._emit("PointAdded", {
                "id": f"forged-{marker}", "content": "c",
                "embedding": [0.1], marker: True})
        if marker in ("embedding_preserved", "embedding_verbatim"):
            # …already refused on this channel by the vector family's own guard,
            # with its own message — the two must not be merged into one claim.
            with pytest.raises(ValueError, match="server-managed"):
                api._emit("PointAdded", **{marker: True})
        else:
            # …and the same key on the keyword payload, which is the more
            # dangerous channel on a flat record (`evidence_fold` there IS a fold:
            # `projection.record_is_evidence_fold` reads it and the replay's
            # recreate anchor is skipped).
            with pytest.raises(ValueError, match="server-minted record facts"):
                api._emit("PointAdded", **{marker: True})
    # The sibling that looks identical but is NOT refused: `add_point` mints it,
    # so it is a seam fact rather than a caller's claim. Asserted as set
    # membership rather than by journalling a record — this test's last
    # assertion is that the refusals above left the journal EMPTY.
    from tortoise.api import _UNCALLERABLE_RECORD_KEYS
    from tortoise.projection import EVIDENCE_FOLD_KEY

    assert "sourceVersionTransit" not in _UNCALLERABLE_RECORD_KEYS
    assert {"extractedFromAll", "sourceVersion", "sourceVersions"} <= (
        _UNCALLERABLE_RECORD_KEYS)
    # ⛔ …AND THE EXEMPTION IS SCOPED TO THE CHANNEL THAT MINTS IT (#11996 review
    # round 6, measured). `sourceVersionTransit` is exempt from the SET because
    # `add_point` mints it INTO ITS RECORD; no seam call mints it on a keyword
    # payload, where the replay consumes it as the `:Source` read-version anchor
    # — the same harm `sourceVersion`/`sourceVersions` are refused for:
    #
    #     api._emit("PointAdded", id="p1", content="c", extractedFrom=["src:1"],
    #               sourceVersionTransit={"src:1": "h-forged"})   -> LANDED
    #     api._emit("PointAdded", id="p1", content="c", sourceVersion="x")
    #                                                             -> REFUSED
    #
    # Each door is exercised because each splats its OWN kwargs; the funnel is
    # the one place that knows the channel, so the refusal lives there.
    with pytest.raises(ValueError, match="sourceVersionTransit"):
        api._emit("PointAdded", id="p-transit", content="c",
                  sourceVersionTransit={"src:1": "h-forged"})
    with pytest.raises(ValueError, match="sourceVersionTransit"):
        api.emit_belief("ConfidenceChanged", id="n-transit",
                        sourceVersionTransit={"src:1": "h-forged"})
    with pytest.raises(ValueError, match="sourceVersionTransit"):
        api.add_object("obj-transit", "skill",
                       sourceVersionTransit={"src:1": "h-forged"})
    with pytest.raises(ValueError, match="sourceVersionTransit"):
        api.add_event("ev-transit", "something_happened",
                      sourceVersionTransit={"src:1": "h-forged"})
    # ⛔ AND THE KEYS ARE WRITTEN BY VALUE WHILE THE GUARDS READ THEM BY HASH
    # (#11996 review round 8, measured). A `str` SUBCLASS whose `__hash__` differs
    # from `str`'s passes every `k in frozenset(...)` test in `api.py` and is
    # journalled under the name it claims:
    #
    #     class _K(str):
    #         def __hash__(self): return 0x5f3a1
    #     _K("evidence_fold") == "evidence_fold"              # True
    #     _K("evidence_fold") in frozenset({"evidence_fold"}) # False  ← the guard
    #     json.dumps({_K("evidence_fold"): True})             # '{"evidence_fold":true}'
    #
    # Measured before the guard: this LANDED on the positional record and on the
    # keyword payload, `record_is_evidence_fold` read it as a fold, and the same
    # carrier re-typed (`_K("type")`) and re-identified (`_K("event_id")`) the
    # record. Falsifier: delete `_refuse_non_str_keys` and every row below FAILS —
    # on the MESSAGE, not by landing: `_named_from_caller` carries its own
    # `type(k) is not str` clause, so the carrier is still refused, as a
    # RECORD-SHAPED / envelope field, which is a message these rows do not match
    # (#11996 review round 12, measured — the earlier text claimed they would
    # land, which reads as "this arm is unpinned"). What the rows pin is this
    # arm's OWN diagnostic, so they red if it is removed and they red if it
    # starts letting the carrier through.
    class _K(str):
        def __hash__(self):  # deliberately NOT str's hash
            return 0x5F3A1

    for _forged in ("evidence_fold", "type", "event_id",
                    "sourceVersionTransit", "embedding_width"):
        with pytest.raises(ValueError, match="not an exact `str`"):
            api._emit("PointAdded", {"id": f"p-key-{_forged}", "content": "c",
                                     _K(_forged): True})
        with pytest.raises(ValueError, match="not an exact `str`"):
            api._emit("PointAdded", id=f"p-keyw-{_forged}", content="c",
                      **{_K(_forged): True})
    with pytest.raises(ValueError, match="not an exact `str`"):
        api.add_object("o-key", "skill", **{_K("extractedFromAll"): ["src:1"]})
    with pytest.raises(ValueError, match="not an exact `str`"):
        api.emit_belief("ConfidenceChanged", id="n-key",
                        **{_K("evidence_fold"): True})
    with pytest.raises(ValueError, match="not an exact `str`"):
        api.add_event("ev-key", "something_happened",
                      **{_K("sourceVersionTransit"): {"src:1": "x"}})
    # ⛔ AND THE RECORD CHANNEL IS NOT EXEMPT FROM THE TRANSIT REFUSAL UNLESS THE
    # DOOR SAYS IT MINTED IT (#11996 review round 9, measured). Scoping the
    # exemption by CHANNEL NAME left it forgeable by any direct `_emit` caller,
    # because `_emit` cannot tell `add_point`'s record from a caller-built one:
    #
    #     _emit("PointAdded", {"id": "p1", "content": "c",
    #                          "sourceVersionTransit": [["src:1", "h-forged"]]})
    #       -> LANDED, and `projection.entities._point_source_transit` consumed it
    #          as the `:Source` read-version anchor
    #
    # The exemption is a DERIVED PREDICATE, not a declared signal: `_emit`
    # re-derives `_source_version_transit(resolve_source_versions(g,
    # extractedFrom))` and refuses a mismatch or an underivable value (the
    # `_DERIVE_ON_RECORD` arm of `_emit`), and it checks the SHAPE
    # (`type(...) is list` of exact `[str, str]` pairs) BEFORE comparing, because
    # `!=` would delegate to a caller's `__eq__`. Both earlier designs are recorded
    # in that same comment as MEASURED caller-suppliable — a channel-name scope AND
    # a caller-passable `seam_mints` keyword (`_emit(…, _seam_mints=True)` LANDED
    # the same forgery) — so neither is a mechanism and this comment must not name
    # one. Falsifier: replace the derive arm with either of those signals and this
    # row lands.
    # ⛔ AND `projection_version` IS REFUSED AT THE DOORS TOO, not only inside the
    # record (#11996 review round 10, measured): putting the key ONE LEVEL OUT
    # defeated the #49 v2 stop-write on a public door —
    #
    #     api.emit_belief("PointRevised", id=pid, new_content="c2",
    #                     new_context={"secret": "leaked"}, projection_version=1)
    #       -> LANDED, and the fold KEPT `context` (pv=1) where the seam's own
    #          pv=2 strips it
    #
    # so all three doors refuse it now. Falsifier: drop `record=True` from the
    # funnel's envelope call and these three rows land.
    for _door, _call in (
            ("emit_belief", lambda: api.emit_belief(
                "PointRevised", id="n-pv", new_content="c2",
                new_context={"s": "leaked"}, projection_version=1)),
            ("add_event", lambda: api.add_event("e-pv", "something_happened",
                                                projection_version=1)),
            ("add_object", lambda: api.add_object("o-pv", "skill",
                                                  projection_version=1))):
        with pytest.raises(ValueError, match="projection_version"):
            _call()
    # ⛔ …AND `_emit`'s OWN KEYWORD CHANNEL CANNOT REFUSE PRESENCE, SO IT REFUSES
    # THE TYPE (#11996 review round 18, measured). `projection_version` is out of
    # `_ENVELOPE_KEYS` because `add_point`/`add_operator` pass THEIR OWN int through
    # this channel — but nothing then checked the type, and this append is the
    # commit point:
    #
    #     api._emit("PointAdded", id="p", content="c", projection_version="x")
    #       -> JOURNALLED with a `str` version, and the flat record is then folded
    #          AWAY: `fold([ev])` returns `{}` with NO exception (no `point` in a
    #          flat record, so `_apply_one` returns at its non-dict-point guard),
    #          i.e. the write survives and the fold silently loses the point. The
    #          LOUD shape is record-positional — `_emit("PointAdded", {"id": …,
    #          "content": …}, projection_version="x")` — which raises
    #          TypeError: '>=' not supported between instances of 'str' and 'int'
    #          on every replay (both measured at this head).
    #
    # A record every replay refuses is the durable-corruption shape this whole fix
    # exists to prevent. Falsifier: delete the `type(_pv) is not int` arm and the
    # four rows below land (measured).
    for _pv in ("x", 1.5, True, None):
        with pytest.raises(ValueError, match="must be an exact `int`"):
            api._emit("PointAdded", id=f"p-pv-type-{_pv}", content="c",
                      projection_version=_pv)
    # ⛔ …and the two ints that DO land are stated, not implied — but they are
    # pinned in their OWN test (`test_emit_keyword_pins_the_int_projection_
    # versions_that_still_land`), because everything above this line asserts that
    # no refusal left a record behind and these two DO write.

    with pytest.raises(ValueError, match="sourceVersionTransit"):
        api._emit("PointAdded", {"id": "p-transit-rec", "content": "c",
                                 "embedding": [0.5],
                                 "sourceVersionTransit": [["src:1", "h-forged"]],
                                 JOURNAL_VECTOR_WIDTH_KEY: JOURNAL_VECTOR_WIDTH_F32})

    # ⛔ …and a MAPPING SUBCLASS that shows the guards one set of keys while the
    # encoder writes another is refused as a container, not inspected:
    #
    #     _emit("PointAdded", _Hidden({"id": "p", "content": "c",
    #                                  "evidence_fold": True}, {"id", "content"}))
    #       -> LANDED before the guard, and read as a fold
    class _Hidden(dict):
        def __init__(self, data, shown):
            super().__init__(data)
            self._shown = shown

        def __iter__(self):
            return iter(self._shown)

        def keys(self):
            return list(self._shown)

    with pytest.raises(ValueError, match="exact `dict`"):
        api._emit("PointAdded", _Hidden({"id": "p-hidden", "content": "c",
                                         "evidence_fold": True},
                                        {"id", "content"}))
    assert EVIDENCE_FOLD_KEY in _UNCALLERABLE_RECORD_KEYS
    # The fold key is the READER's constant, not a second spelling of it.
    # ⛔ This assert alone does NOT prove it: api.py IMPORTS the symbol, so a
    # rename at the consumer moves both sides together and the assert stays
    # green. What actually pins the identity is the loop below, whose literal
    # "evidence_fold" — the name the projection READS — would red on a consumer
    # rename. This row is a cheap spelling check; the loop is the pin. (Review
    # note at #11996: this comment used to sit ~140 lines above the assert it
    # describes, so "this assert" / "this line" had no referent — round 17 moved
    # it here and kept the note.)
    # …and `emit_belief` must RAISE these rather than drop them: its refusal is
    # armed before the best-effort arm, which is the whole point of #11948 F2.
    for marker in ("evidence_fold", "embeddingText", "extractedFromAll",
                   "sourceVersion", "sourceVersions"):
        with pytest.raises(ValueError, match="server-minted record facts"):
            api.emit_belief("ConfidenceChanged", id="n-belief-forged",
                            **{marker: True})
    # …and the TWO ownership markers are refused here too, but by the MARKER arm,
    # whose message is different — `_refuse_caller_named_funnel_keys` runs the
    # marker family BEFORE the uncallerable one, so pinning the order is the
    # point of these two rows (#11996 review round 14: the loop above covers
    # five of the seven keys, and the PR body claimed "the same keys at
    # emit_belief" — true of the refusal, not of the message).
    for marker in ("embedding_preserved", "embedding_verbatim"):
        with pytest.raises(ValueError, match="server-managed journal fields"):
            api.emit_belief("ConfidenceChanged", id="n-belief-owner",
                            **{marker: True})
    assert not journal.exists() or not journal.read_text().strip(), (
        "the refusal must not leave a half-record behind")

    # …and the door's ordinary job still lands (the best-effort posture is
    # narrowed, not removed — `test_2884`'s f5 pins the I/O arm).
    api.emit_belief("ConfidenceChanged", id="node-2", confidence=0.5)
    records = [json.loads(ln) for ln in
               journal.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert [r["type"] for r in records] == ["ConfidenceChanged"]
    assert records[0]["confidence"] == 0.5

    # …and `add_point` still lands, so the refusal above is not a refusal of the
    # door's own record. ⛔ THIS ROW IS A SMOKE ROW, NOT THE MINT'S PIN: this
    # `EventAPI` is built with `projection=None`, so `add_point` mints nothing and
    # the derivation arm is never reached here — measured, wrapping `_emit` so no
    # door supplies the value leaves this row PASSING. The mint itself is pinned
    # where it can be: `tests/test_source_version_extractedfrom_5038.py::
    # test_eventapi_add_point_anchors_the_version` (a real graph handle, the
    # journalled `sourceVersionTransit` compared to the Source's hash). Stated
    # rather than implied, because "pinned here" was an over-claim.
    _minted = api.add_point("a point with a source",
                            provenance("src:9", [0, 1], "q"),
                            embedding=[0.5], fields={"extractedFrom": ["src:9"]})
    assert _minted


def test_emit_keyword_pins_the_int_projection_versions_that_still_land(tmp_path):
    """`_emit`'s keyword channel refuses a NON-int `projection_version`, not the key.

    `projection_version` cannot be refused by PRESENCE on this channel: `add_point`
    and `add_operator` pass their OWN `2` here, so the arm is a TYPE check (#11996
    review round 18, measured). What that leaves — an `int` a caller supplies — is
    the residual, and it is pinned as LANDING rather than described in prose, so
    nobody reads "projection_version is guarded" and assumes more than the type
    arm buys: `2` is the producers' own value and `1` suppresses the `#49` v2
    context strip on a record the fold then reads.
    """
    from tortoise.api import EventAPI
    from tortoise.log import EventLog

    api = EventAPI(EventLog(tmp_path / "events.jsonl"),
                   initiated_by="extractor", projection=None)
    for _pv in (2, 1):
        api._emit("PointAdded", id=f"p-pv-int-{_pv}", content="c",
                  projection_version=_pv)
    assert len((tmp_path / "events.jsonl").read_text().splitlines()) == 2


def test_the_transit_anchor_is_derived_and_a_caller_cannot_forge_it(tmp_path):
    """#11996 review round 11: the record channel DERIVES `sourceVersionTransit`.

    `add_point` mints the `:Source` read-version anchor into the record it hands
    `_emit`, so the key cannot be refused outright on that channel — but every
    TRUST signal is caller-suppliable (the channel name, a `seam_mints` boolean),
    and the comparison itself was untyped, so a `str` subclass answering `==` for
    anything landed a forged anchor:

        class _Eq(str):
            def __eq__(self, other): return True
        _emit("PointAdded", {… "extractedFrom": [DOC],
              "sourceVersionTransit": [[DOC, _Eq("h-FORGED")]]})
          -> LANDED, and the replay stamped `h-FORGED` as the `:Source` read-version

    The arm now checks the SHAPE (`[[exact str, exact str], …]`) before it
    compares, which is what a caller cannot lie about: `_source_version_transit`
    mints nothing else. This test is the live-graph half — without it the
    comparison branch is unreachable, because no OTHER row here presents a
    `sourceVersionTransit` on a record whose `EventAPI` has a bound graph handle
    (three rows do build one — `_added_point_with_caller`,
    `_ApprovedGraphProjection`, `_UnreadableProjection` — so the reason is "no
    row exercises the arm", not "every row is projection-less": #11996 review
    round 16 corrected the earlier wording, which the file itself contradicted).

    FAILS IF: the forged rows land (no derivation, or an untyped compare), or the
    truthful row is refused (the arm refusing the seam's own record).
    """
    from tortoise.api import EventAPI, provenance
    from tortoise.sdk import TortoiseSDK

    doc = "https://doc.example/transit"
    events = tmp_path / "transit-events"
    events.mkdir()
    log_path = events / "events.jsonl"
    sdk = TortoiseSDK(str(tmp_path / "transit.db"), event_log_path=str(log_path))
    try:
        sdk.create_source(doc, "document", contentHash="h1")
        api = EventAPI(EventLog(log_path), initiated_by="extractor",
                       projection=sdk._get_proj())

        # 1. The TRUTHFUL value the seam would mint lands (the control that stops
        #    the derive arm from becoming a blanket refusal of the channel).
        api.add_point("a point with a source",
                      provenance(doc, [0, 4], "q"),
                      embedding=[0.5], extractedFrom=[doc])
        # 2. A plain forged pair is refused: the derived value disagrees.
        with pytest.raises(ValueError, match="sourceVersionTransit"):
            api._emit("PointAdded", {"id": "p-forged-plain", "content": "c",
                                     "embedding": [0.5],
                                     "extractedFrom": [doc],
                                     "sourceVersionTransit": [[doc, "h-forged"]],
                                     JOURNAL_VECTOR_WIDTH_KEY:
                                         JOURNAL_VECTOR_WIDTH_F32})
        # 3. …and so is the `__eq__`-lying carrier, which is what the SHAPE arm
        #    is for: without it the comparison delegates to the caller and lands.
        class _Eq(str):
            def __eq__(self, other):
                return True

        with pytest.raises(ValueError, match="sourceVersionTransit"):
            api._emit("PointAdded", {"id": "p-forged-eq", "content": "c",
                                     "embedding": [0.5],
                                     "extractedFrom": [doc],
                                     "sourceVersionTransit": [[doc, _Eq("h-x")]],
                                     JOURNAL_VECTOR_WIDTH_KEY:
                                         JOURNAL_VECTOR_WIDTH_F32})
        # 4. …and a CONTAINER that lies the same way is refused by the same arm.
        class _LyingPairs(list):
            def __eq__(self, other):
                return True

            def __ne__(self, other):
                return False

        with pytest.raises(ValueError, match="sourceVersionTransit"):
            api._emit("PointAdded", {"id": "p-forged-list", "content": "c",
                                     "embedding": [0.5],
                                     "extractedFrom": [doc],
                                     "sourceVersionTransit":
                                         _LyingPairs([[doc, "h-x"]]),
                                     JOURNAL_VECTOR_WIDTH_KEY:
                                         JOURNAL_VECTOR_WIDTH_F32})
        # 5. ⛔ A `tuple` PAIR IS A SHAPE REFUSAL, not a value refusal (#11996
        #    review round 16, measured): `_source_version_transit` mints
        #    `[[ref, h], …]` (`[[…] for … in versions.items()]`), so the shape arm
        #    admits `list` pairs only. It used to admit `(list, tuple)`, and a
        #    tuple pair then failed the VALUE comparison — the caller was told
        #    "this seam did not mint for it" about a value whose shape was the
        #    problem. Falsifier: restore `type(_pair) in (list, tuple)` and this
        #    row matches the other message instead (measured).
        with pytest.raises(ValueError, match="is not the shape"):
            api._emit("PointAdded", {"id": "p-tuple-pair", "content": "c",
                                     "embedding": [0.5],
                                     "extractedFrom": [doc],
                                     "sourceVersionTransit": [(doc, "h-x")],
                                     JOURNAL_VECTOR_WIDTH_KEY:
                                         JOURNAL_VECTOR_WIDTH_F32})
        # 6. ⛔ AND AN `extractedFrom` THE RESOLVER CANNOT ITERATE IS A
        #    `ValueError`, not a `TypeError` escaping the arm (#11996 review round
        #    16, measured): `resolve_source_versions` starts with
        #    `list(source_ref)`, so `extractedFrom=123` raised a bare `TypeError`
        #    from a guard whose every other exit is a documented refusal.
        #    Falsifier: drop the `except TypeError` and this row reads `TypeError`
        #    (measured).
        with pytest.raises(ValueError, match="cannot resolve"):
            api._emit("PointAdded", {"id": "p-bad-refs", "content": "c",
                                     "embedding": [0.5],
                                     "extractedFrom": 123,
                                     "sourceVersionTransit": [[doc, "h-x"]],
                                     JOURNAL_VECTOR_WIDTH_KEY:
                                         JOURNAL_VECTOR_WIDTH_F32})
    finally:
        sdk.close()


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
    # ⛔ …AND THE EXCEPTION IS SCOPED TO THAT ONE TYPE, NOT THAT ONE PRODUCER
    # (corrected in review round 22: "producer" was a claim the code cannot make —
    # `_emit` has no caller identity to check, so a direct `_emit("DocumentCreated",
    # …, event_id=…)` still lands and may duplicate an id. That is the residual the
    # arm states; what this row pins is the `#125`/`#167` half). Excluding
    # `event_id` from `_ENVELOPE_KEYS` had applied to EVERY type
    # on the funnel's keyword channel, so a caller could choose the identity of its
    # own record:
    #
    #     api._emit("PointAdded", id="p1", content="c", event_id="forged-id")
    #       -> JOURNALLED {"event_id": "forged-id", …, "type": "PointAdded"}
    #
    # `projection._warn_entity_mutation_fold_miss`'s docstring calls `event_id`
    # "the only handle that locates the diverging journal line", and the record
    # channel and all three splatting doors already refused it — this was the
    # last open channel for those types.
    # Falsifier: replace the `type_ != "DocumentCreated"` scope with an impossible
    # one (e.g. `type_ is None`) and the two rows below land while the
    # `add_document` assertions above still pass (measured, round 22).
    before = len(journal.read_text(encoding="utf-8").splitlines())
    for _type in ("PointAdded", "ConfidenceChanged"):
        with pytest.raises(ValueError, match="ENVELOPE"):
            api._emit(_type, id="p-event-id", content="c", event_id="forged-id")
    assert len(journal.read_text(encoding="utf-8").splitlines()) == before, (
        "a refused `event_id` must not leave a record behind")


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


def test_the_belief_door_reports_a_refused_pack_instead_of_logging_it_away(
        tmp_path, monkeypatch):
    """#11948 F2, the last family: the APPROVAL half is refused, not dropped.

    It is the one payload-shape gate that still runs INSIDE `emit_belief`'s arm —
    it needs a whole-graph read, and the door's eager call is pure by contract
    (`graph_reads=False`, pinned by the outage row above). An `except
    Exception` that logged it away therefore restored exactly the divergence F2
    was filed for, one family over, measured at `8ee861cbaa`:

        add_object(..., eventKind="marketing:campaign") -> raised
        emit_belief(..., eventKind="marketing:campaign") -> journal EMPTY, log line

    The arm now re-raises the NAMED refusal (`UnapprovedKindNamespace`) and
    keeps swallowing everything else, which is what the row above pins — the two
    rows are a pair, and a fix that bought either by deleting the other would
    pass a one-sided test.
    """
    from tortoise import pack_state
    from tortoise.api import EventAPI
    from tortoise.log import EventLog
    from tortoise.sdk import UnapprovedKindNamespace

    class _ApprovedGraphProjection:
        """A bound handle whose approval set is just `dev` — no `marketing`."""

        class g:
            pass

    def _refuse(payload, approved, *, spells=None):
        raise UnapprovedKindNamespace(
            "eventKind='marketing:campaign' names an unapproved pack",
            prop="eventKind", kind="marketing:campaign",
            namespace="marketing", installed=approved)

    monkeypatch.setattr(pack_state, "graph_approval_namespaces",
                        lambda g: frozenset({"dev"}))
    monkeypatch.setattr(pack_state, "payload_names_pack",
                        lambda payload: {"eventKind": "marketing"})
    monkeypatch.setattr(pack_state, "refuse_unapproved_kinds_in_payload", _refuse)

    events = tmp_path / "belief-refused-pack"
    events.mkdir(parents=True)
    journal = events / "events.jsonl"
    api = EventAPI(EventLog(journal), initiated_by="extractor",
                   projection=_ApprovedGraphProjection())

    with pytest.raises(UnapprovedKindNamespace):
        api.emit_belief("ConfidenceChanged", id="node-9",
                        eventKind="marketing:campaign")
    assert not journal.exists() or not journal.read_text().strip(), (
        "a refused pack must leave NOTHING to replay AND must be reported")


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


def test_a_forged_width_cannot_ride_the_funnels_positional_record(tmp_path):
    """The POSITIONAL channel verifies the marker instead of refusing it — and
    it is the verification that closes the last lie.

    Round 8's review found the family covered on `_emit`'s KEYWORD payload
    (`_refuse_caller_named_markers`) and not on its POSITIONAL record, which is
    the one channel built to be unreachable from any door. Measured at
    `13d6361efc`:

        _emit("PointAdded", {"id": "p1", "content": "c",
                             "embedding": [0.1234567890123, 1/3],
                             "embedding_width": "f32"})
          -> journalled, values [0.1234567890123, 0.3333333333333333], `f32`

    ⛔ The obvious fix — refuse `_SERVER_MANAGED_EMBEDDING_KEYS` on `record[0]`
    too — is WRONG, and this row is what says so: the record doors legitimately
    carry the markers, because each stamps its own payload through
    `stamp_journal_embedding` (`_point` with `creating=True`, `add_point` with
    `creating=False` after popping a stale width). A refusal there reds every
    vector-bearing `PointAdded`. So the funnel re-DECIDES from the values and
    refuses only a disagreement — the marker-lie shape, and nothing else.
    """
    from tortoise.api import EventAPI, provenance
    from tortoise.log import EventLog

    events = tmp_path / "positional-marker"
    events.mkdir(parents=True)
    journal = events / "events.jsonl"
    api = EventAPI(EventLog(journal), initiated_by="extractor", projection=None)

    # 1. The lie is refused: `f32` over values the seam decides are `f64`.
    with pytest.raises(ValueError, match="is not what the values say"):
        api._emit("PointAdded", {"id": "p-forged", "content": "c",
                                 "embedding": [0.1234567890123, 1 / 3],
                                 JOURNAL_VECTOR_WIDTH_KEY:
                                     JOURNAL_VECTOR_WIDTH_F32})
    assert not journal.exists() or not journal.read_text().strip(), (
        "the refusal precedes the commit point, so nothing may be journalled")

    # 2. A marker that AGREES with the values is not refused — it is the seam's
    #    own answer, which is what every `add_point` record carries.
    api._emit("PointAdded", {"id": "p-honest", "content": "c",
                             "embedding": [0.1, 0.2],
                             JOURNAL_VECTOR_WIDTH_KEY: "f64"})
    api.add_point("a real point", provenance("src:1", [0, 4], "q"),
                  embedding=[0.1, 0.2])
    # 3. …and the record door still declares ITS OWN answer, untouched.
    records = [json.loads(ln) for ln in
               journal.read_text(encoding="utf-8").splitlines() if ln.strip()]
    widths = [r["point"].get(JOURNAL_VECTOR_WIDTH_KEY) for r in records]
    assert widths == ["f64", "f64"], (
        "the honest marker and the door's own stamp must both survive; a "
        f"blanket refusal on the positional channel would break the door, got {widths}")
    assert records[1]["point"]["embedding"] == [0.1, 0.2], (
        "the door's vector must reach the journal untouched")

    # 4. ⛔ THE VALUES ARE THE MERGED VIEW'S, NOT THE RECORD'S (#11835 review
    #    round 9, P1 — measured). `_norm` splices the record OVER the envelope,
    #    so the fold reads the width from the record and the vector from
    #    wherever it happens to be. A record that declares `f32` while its
    #    vector rides the KEYWORD payload (which `add_event` documents as
    #    allowed, so the funnel must cope) passed the record-only read as "no
    #    vector to check" and journalled a marker the fold then honoured —
    #    while the seam's own stamp wrote the truthful `f64` on the envelope in
    #    the SAME line. Two contradictory declarations, one record.
    with pytest.raises(ValueError, match="is not what the values say"):
        api._emit("PointAdded",
                  {"id": "p-split", "content": "c",
                   JOURNAL_VECTOR_WIDTH_KEY: JOURNAL_VECTOR_WIDTH_F32},
                  embedding=[0.1234567890123, 1 / 3])

    # 5. …and a declared width over NO vector anywhere is refused — for EITHER
    #    of the seam's two widths, not only `f32` (#11996 review round 2,
    #    measured: inside the `f32` arm only, `f64` over no vector LANDED and
    #    journalled `embedding_width: "f64"`). The reasoning is
    #    width-INDEPENDENT — `_stamp_journal_vector_width` POPS the key when
    #    there is no vector, so a width with no vector is a state the seam can
    #    never have minted — which is why the refusal now precedes the `f64`
    #    return. Falsifier: move it back below that return and the `f64` row
    #    below reds on the MESSAGE, not on `DID NOT RAISE` — the `f64` arm's own
    #    seam probe refuses the absent vector FIRST, with "can only DROP"
    #    (measured, review round 20). The ordering is still pinned; the message
    #    is what changes.
    for _declared in (JOURNAL_VECTOR_WIDTH_F32, JOURNAL_VECTOR_WIDTH_F64):
        with pytest.raises(ValueError, match="declared over NO vector"):
            api._emit("PointAdded",
                      {"id": f"p-nowidth-{_declared}", "content": "c",
                       JOURNAL_VECTOR_WIDTH_KEY: _declared})
    assert [json.loads(ln) for ln in
            journal.read_text(encoding="utf-8").splitlines() if ln.strip()] == records, (
        "both refusals precede the commit point — the journal must be unchanged")

    # 5b. ⛔ THE SAME REFUSAL FOR `f64` — THE ARM IS INDIFFERENT TO WHICH WIDTH
    #     IS DECLARED (#12059). The refusal's own justification above is that
    #     `_stamp_journal_vector_width` POPS the key when there is no vector, so
    #     "width over no vector" is a state the seam can never mint — and that
    #     argument never mentions `f32`. It sat AFTER the `declared != f32`
    #     early return, so half the declaration surface was unchecked: a
    #     caller-declared `f64` over no vector was ACCEPTED and journalled, a
    #     durable width no door can produce. Measured at `25127b61b6` before the
    #     fix — this row is the pin. ⛔ AND THE MUTANT'S OBSERVABLE IS THE MESSAGE:
    #     move the check back below the `declared != _F32` return and THIS row reds
    #     on "can only DROP" (the `f64` arm's seam probe refusing the absent vector
    #     first), NOT on a missing refusal; delete the whole pre-stamp block and it
    #     is an `f32` row above that reds, on "is not what the values say"
    #     (measured, review round 20 — "reds it while every `f32` row above still
    #     passes" was false on both halves).
    with pytest.raises(ValueError, match="declared over NO vector"):
        api._emit("PointAdded",
                  {"id": "p-nowidth-f64", "content": "c",
                   JOURNAL_VECTOR_WIDTH_KEY: JOURNAL_VECTOR_WIDTH_F64})
    # …and a declared `f64` WITH a vector stays the accepted, always-truthful
    # shape (it only forgoes narrowing) — the refusal above is about the absent
    # vector, not about the width, so this row is what keeps the fix from
    # becoming an over-block on the one declaration every raw vector carries.
    api._emit("PointAdded", {"id": "p-f64-vector", "content": "c",
                             "embedding": [0.1234567890123, 1 / 3],
                             JOURNAL_VECTOR_WIDTH_KEY: JOURNAL_VECTOR_WIDTH_F64})
    records = [json.loads(ln) for ln in
               journal.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(records) == 3 and records[-1]["point"].get(
        JOURNAL_VECTOR_WIDTH_KEY) == JOURNAL_VECTOR_WIDTH_F64, (
        "a declared f64 beside a vector is the seam's own raw answer and must land")

    # 5c. ⛔ A DECLARED `f64` OVER A VECTOR THE SEAM DROPS (#12059 review, P3 —
    #     measured on `main` before the rebase; re-measured here). A declared
    #     `f64` beside a non-numeric vector used to reach NEITHER refusal: the
    #     merged view still carried the junk (so not the no-vector arm) and
    #     `f64` was not value-checked (so not the grid arm). On `main` only the
    #     POST-stamp belt caught it, one call later; on THIS branch the
    #     pre-stamp `f64` arm asks the seam and refuses it first, with the
    #     narrower reason — so either message is a valid refusal of the same
    #     state, and the belt is now defence in depth for `f64` rather than
    #     independently pinned here. Both are asserted so the row pins the
    #     REFUSAL, which is the property that matters, not which of two guards
    #     on the same path gets there first.
    with pytest.raises(ValueError,
                       match=r"can only DROP|declared over NO vector"):
        api._emit("PointAdded",
                  {"id": "p-dropped-f64", "content": "c",
                   JOURNAL_VECTOR_WIDTH_KEY: JOURNAL_VECTOR_WIDTH_F64},
                  embedding="abc")

    # 6. The merged view agrees when the vector is on the ENVELOPE and the
    #    record declares the width the seam would also declare for it.
    api._emit("PointAdded", {"id": "p-split-ok", "content": "c",
                             JOURNAL_VECTOR_WIDTH_KEY: "f64"},
              embedding=[0.1, 0.2])
    records = [json.loads(ln) for ln in
               journal.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(records) == 4 and records[-1]["point"]["embedding_width"] == "f64", (
        "a truthful declaration over the envelope's vector must still land")

    # 7. ⛔ A VECTOR THE SEAM CAN ONLY DROP IS A VECTOR NO WIDTH DESCRIBES
    #    (#12000 round-10 review, F1 — measured). `_is_shortest_f32_text` is
    #    fail-CLOSED on non-numeric input and returns False; asking
    #    `shortest_float32_vector` about the same input RAISES, and a shared
    #    `except: return` read that as "the seam cannot decide" — over a value
    #    the seam describes exactly, by dropping it. Measured at `bf1957fb9`:
    #
    #        _emit("PointAdded", {"id": "p", "content": "c", KEY: "f32"},
    #              embedding="abc")  -> journalled, fold reads f32 over 'abc'
    #
    #    …and in the same call the seam then dropped that vector and popped its
    #    own width, leaving the record's `f32` over `None`: the exact state
    #    arm 5 above refuses, reached one call later. Both are refused now.
    for _bad in ("abc", 7, {"0": 0.1}, [float("nan")]):
        with pytest.raises(
                ValueError,
                match=r"is not what the values say|declared over NO vector"):
            api._emit("PointAdded", {"id": "p-junk", "content": "c",
                                    JOURNAL_VECTOR_WIDTH_KEY: JOURNAL_VECTOR_WIDTH_F32},
                      embedding=_bad)
    # …and the post-stamp re-check: a record declaring `f32` cannot survive a
    # payload whose vector the seam DROPS. The non-numeric shapes above are the
    # reachable ones today, so this asserts the ordering, not a second path.
    with pytest.raises(ValueError):
        api._emit("PointAdded",
                  {"id": "p-dropped", "content": "c",
                   JOURNAL_VECTOR_WIDTH_KEY: JOURNAL_VECTOR_WIDTH_F32},
                  embedding="abc")

    # ⛔ …AND THE BELT HAS ITS OWN ROW, which is the one shape IN THIS TEST that
    #    reds when it is removed (#12000 mutation pass, agent-infra#3442 —
    #    measured: with the post-stamp call disabled, every other arm above still
    #    passes, because they are all refused by the PRE-stamp check). ⛔ NOT the
    #    belt's ONLY pin: `p-merged-lie` below reds on that same mutation and no
    #    other arm can see it, because neither channel declared a width there
    #    (measured, review round 18 — the earlier wording claimed "the one shape"
    #    for the whole belt and was false). An EMPTY vector is the
    #    case that separates them: `_is_shortest_f32_text([])` is `True`
    #    ("representable at every width" — `tortoise/embeddings.py`), so the
    #    pre-stamp arm passes it truthfully, the seam then DROPS it to an owned
    #    `None`, and the belt is what refuses the resulting `f32` over `None`.
    #    Without this row the belt is unexercised BY THIS TEST — a guard with no
    #    attributable mutant here, which is how five PRs in one night were
    #    reviewed clean over restored bugs.
    with pytest.raises(ValueError, match="declared over NO vector"):
        api._emit("PointAdded",
                  {"id": "p-empty", "content": "c",
                   JOURNAL_VECTOR_WIDTH_KEY: JOURNAL_VECTOR_WIDTH_F32},
                  embedding=[])

    # 8. ⛔ THE SAME JUNK CARRIED ON THE RECORD, which is the shape the arms
    #    above MISS (#12000 round-10 re-review, F1' — measured). This is NOT a
    #    channel the belt cannot see: the belt is handed `event["point"]` as the
    #    record and the merged view lets the record win, so it ran on this
    #    vector too and let it through because it ran the SAME permissive
    #    predicate (that is the defect arm 8 fixes, and the reason the two arms
    #    are not duplicates of each other). The grid arm called
    #    `shortest_float32_vector(values)`, which reads its argument as
    #    `[float(x) for x in vector]`: a `str` is a sequence of CHARACTERS
    #    (`"123"` → `[1.0, 2.0, 3.0]`), `bytes` a sequence of INTS, a `dict` a
    #    sequence of KEYS (`{"0": 0.1}` → `[0.0]`) — all ON the grid, so the
    #    arm answered "truthful" about the wrong object. Measured at
    #    `0a86cb5a87`: both journalled, and the FOLD stored the junk as the
    #    Point's vector under `f32`. `_is_shortest_f32_text` refuses exactly
    #    these three types for exactly this reason; the grid arm now does too.
    for _junk in ("123", {"0": 0.1}, b"12"):
        with pytest.raises(ValueError, match="is not what the values say"):
            api._emit("PointAdded",
                      {"id": "p-record-junk", "content": "c",
                       "embedding": _junk,
                       JOURNAL_VECTOR_WIDTH_KEY: JOURNAL_VECTOR_WIDTH_F32})

    # 9. ⛔ AND THE `f64` ARM IS NOT A LICENCE TO CARRY A NON-VECTOR (#11996
    #    review round 3, measured). `f64` answers "this vector is the raw
    #    float64 form, not narrowed"; it says nothing about a value that is not
    #    a vector at all. Before this row, the `f64` arm returned before any
    #    predicate ran, so every shape below LANDED and the FOLD stored it as
    #    the point's embedding:
    #
    #        _emit("PointAdded", {… "embedding": "123", KEY: "f64"})
    #          -> journalled `embedding: "123"` with `width: "f64"`
    #
    #    The refusal asks the SEAM (`stamp_journal_embedding` sets an unusable
    #    vector to an owned `None`) rather than re-implementing its
    #    normalisation, and only what the seam would DROP is refused. Falsifier:
    #    delete the `dropped` arm and every shape below reads `DID NOT RAISE`.
    for _junk in ("123", {"0": 0.1}, b"12", 7, True, [float("nan")],
                  [float("inf")], []):
        with pytest.raises(ValueError, match=r"can only.*DROP"):
            api._emit("PointAdded",
                      {"id": "p-f64-junk", "content": "c",
                       "embedding": _junk,
                       JOURNAL_VECTOR_WIDTH_KEY: JOURNAL_VECTOR_WIDTH_F64})
    # ⛔ A LIST IS REFUSED BY THE ELEMENT ARM, not by the DROP arm, and the two
    # messages must not be merged into one claim: `["abc"]` and `[[0.1]]` are
    # LIST vectors (so the element arm sees them) while a bare `"123"` is not.
    # Measured: this is the ordering the code has, and an earlier revision of
    # this row asserted the DROP message for both and so would have failed on a
    # correct tree -- which is why the two lists are separate here.
    for _junk in (["abc"], [[0.1]]):
        with pytest.raises(ValueError, match="not an exact"):
            api._emit("PointAdded",
                      {"id": "p-f64-elem", "content": "c",
                       "embedding": _junk,
                       JOURNAL_VECTOR_WIDTH_KEY: JOURNAL_VECTOR_WIDTH_F64})
    assert [json.loads(ln) for ln in
            journal.read_text(encoding="utf-8").splitlines() if ln.strip()] == records, (
        "every refusal precedes the commit point — the journal is unchanged")

    # 9b. …and the CONTROL that stops the new arm becoming a blanket refusal of
    #     the `f64` channel: a vector float32 cannot HOLD is exactly what `f64`
    #     is for, and it must still land (measured: `stamp_journal_embedding`
    #     keeps `[1e300, -1e300]` and declares `f64`).
    api._emit("PointAdded", {"id": "p-f64-huge", "content": "c",
                             "embedding": [1e300, -1e300],
                             JOURNAL_VECTOR_WIDTH_KEY: JOURNAL_VECTOR_WIDTH_F64})
    records = [json.loads(ln) for ln in
               journal.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert records[-1]["point"][JOURNAL_VECTOR_WIDTH_KEY] == JOURNAL_VECTOR_WIDTH_F64
    assert records[-1]["point"]["embedding"] == [1e300, -1e300], (
        "an f64 vector float32 overflows must survive the new arm untouched")

    # 10. ⛔ THE `f32` ARM HAS THE SAME HOLE FOR AN **EMPTY** VECTOR (#11996
    #     review round 4, measured). `_is_shortest_f32_text([])` is True by
    #     design ("representable at every width") and the grid arm finds `[]` on
    #     the grid, so the two questions above answer truthfully about a vector
    #     `stamp_journal_embedding` DROPS:
    #
    #         _emit("PointAdded", {… "embedding": [], KEY: "f32"})  -> LANDED
    #
    #     The refusal is gated on the RECORD carrying the vector, and the reason
    #     is the BELT's ROW, not its blindness (#11996 review round 5, measured):
    #     the belt calls this same function with the same record, so it DOES see
    #     the record-carried shape and refuses it with the same message. What the
    #     gate preserves is the ENVELOPE-carried `[]` — the belt's own row and its
    #     only attributable mutant (`declared over NO vector`, pinned separately);
    #     refusing here unconditionally would take that row before the stamp runs.
    #     Falsifier: delete the `empty` arm and this row reads `DID NOT RAISE`.
    with pytest.raises(ValueError, match="over an EMPTY vector"):
        api._emit("PointAdded",
                  {"id": "p-f32-empty", "content": "c", "embedding": [],
                   JOURNAL_VECTOR_WIDTH_KEY: JOURNAL_VECTOR_WIDTH_F32})
    # …and the belt keeps its own attributable mutant: an ENVELOPE-carried empty
    # vector is refused by the POST-stamp belt (after the stamp has made it
    # absent), NOT by the pre-stamp arm above, which is what keeps that arm
    # from becoming the belt's shadow.
    with pytest.raises(ValueError, match="declared over NO vector"):
        api._emit("PointAdded",
                  {"id": "p-belt-empty", "content": "c",
                   JOURNAL_VECTOR_WIDTH_KEY: JOURNAL_VECTOR_WIDTH_F32},
                  embedding=[])

    # 11. ⛔ EVERY ELEMENT IS READ BY TWO DIFFERENT PATHS, so a vector may only
    #     carry elements BOTH readers agree on — an exact `int` / `float`
    #     (#11996 review rounds 6 and 7, each measured). The guard asks the SEAM,
    #     which converts with `float(x)`; the journal's JSON encoder writes the
    #     element AS IT IS. Every arm above therefore answers truthfully about a
    #     value the journal may not store:
    #
    #         class _Sneaky(float):
    #             def __float__(self): return 0.5        # what the SEAM reads
    #         json.dumps([_Sneaky(nan)])            -> [NaN]   # what the JOURNAL writes
    #         json.dumps([_Sneaky(0.1234567890123)])-> [0.1234567890123]
    #         json.dumps(["0.5", "0.25"])           -> ["0.5", "0.25"]
    #
    #         _emit("PointAdded", {… "embedding": [_Sneaky(nan)], KEY: "f32"})
    #           -> LANDED, journalled `f32` over [NaN]     (the plain [nan] is refused)
    #         _emit("PointAdded", {… "embedding": [_Sneaky(0.1234567890123)], KEY: "f32"})
    #           -> LANDED, journalled `f32` over an OFF-GRID double ← #5004 round-7
    #         _emit("PointAdded", {… "embedding": ["0.5", "0.25"], KEY: "f32"})
    #           -> LANDED, journalled `f32` over STRINGS, which the FOLD then stored
    #              as the Point's embedding ← the same lie, one reader over
    #
    #     Falsifier: WEAKEN the arm to `isinstance(x, (int, float)) and
    #     type(x) not in (int, float)` (a hypothetical loosening, NOT an earlier
    #     revision — every revision on this branch has used exact `type(...)`,
    #     #11996 review round 17) and the `str` row reads `DID NOT RAISE` while the
    #     subclass rows stay refused (measured). ⛔ NOT every row: `bool` is an
    #     `int` subclass, so the widened arm catches it itself, and a nested
    #     sequence is refused by the grid / DROP arm instead — those rows red on
    #     the MESSAGE, not on a missing refusal. The rows this arm alone decides
    #     are the `str` and `Decimal` ones; the `_Sneaky`/`_Unfloatable` rows are
    #     `float` SUBCLASSES, so the widened arm catches them exactly as it
    #     catches `bool` (measured: same `not an exact` message, still green).
    class _Sneaky(float):
        def __float__(self):
            return 0.5

    class _Unfloatable(float):
        def __float__(self):
            raise RuntimeError("boom")

    for _width in (JOURNAL_VECTOR_WIDTH_F32, JOURNAL_VECTOR_WIDTH_F64):
        for _elem in (_Sneaky(float("nan")), _Sneaky(0.1234567890123),
                      _Unfloatable(float("nan")), "0.5", True, [0.5],
                      Decimal("0.5")):
            with pytest.raises(ValueError, match="not an exact"):
                api._emit("PointAdded",
                          {"id": f"p-subclass-{_width}", "content": "c",
                           "embedding": [_elem],
                           JOURNAL_VECTOR_WIDTH_KEY: _width})
    #     ⛔ …and the CONTROL that keeps this a refusal of the ELEMENT, not of
    #     vectors: exact `int`/`float` elements still land under both widths, so
    #     the arm cannot quietly become a blanket refusal of vectors.
    api._emit("PointAdded", {"id": "p-exact-f32", "content": "c",
                             "embedding": [0.5, 1],
                             JOURNAL_VECTOR_WIDTH_KEY: JOURNAL_VECTOR_WIDTH_F32})
    api._emit("PointAdded", {"id": "p-exact-f64", "content": "c",
                             "embedding": [1e300, -1e300],
                             JOURNAL_VECTOR_WIDTH_KEY: JOURNAL_VECTOR_WIDTH_F64})
    # 11b. ⛔ …AND THE CONTAINER AND THE INTEGER ARE HELD TO THE SAME RULE
    #      (#11996 review round 8, measured), because both were measured to
    #      disagree between the guard's read and the encoder's write:
    #
    #        * a `list` SUBCLASS whose `__iter__` stages its answer landed `[NaN]`
    #          under a declared `f32` (seven "good" iterations for the guards, the
    #          eighth for the encoder);
    #        * an `int` above 2**53 is read as `float(x)` and written EXACTLY, so
    #          `[2**53 + 1]` landed under `f32` with the width describing the
    #          rounded value.
    #
    #      Falsifier: drop the `type(values) not in (list, tuple)` arm and the
    #      staged row lands; drop the exactness test's clause and the big-int row
    #      lands (both measured).
    #
    #      ⛔ AND THE EXACTNESS TEST IS TOTAL (#11996 review round 12, measured):
    #      `float()` raises for an `int` above ~1.8e308, so the inline
    #      `float(x) != x` left this ONE arm raising `OverflowError` while every
    #      neighbouring arm raises the documented `ValueError` —
    #
    #          _emit(… "embedding": [2 ** 2000], KEY: "f32")
    #            -> OverflowError: int too large to convert to float
    #
    #      Nothing is journalled either way, so the harm was the TYPE of the
    #      refusal: a caller wrapping a door in `except ValueError` saw a crash.
    #      Falsifier for the `except` branch: revert
    #      `_int_is_float_exact` to `float(value) == value` and the rows below
    #      raise `OverflowError` (measured).
    class _Staged(list):
        def __init__(self, good, bad, good_reads):
            super().__init__([0.5])
            self._good, self._bad, self._left, self.calls = (
                good, bad, good_reads, 0)

        def __iter__(self):
            self.calls += 1
            return iter(list(self._good) if self.calls <= self._left
                        else list(self._bad))

    with pytest.raises(ValueError, match="only an exact `list`"):
        api._emit("PointAdded",
                  {"id": "p-staged", "content": "c",
                   "embedding": _Staged([0.5], [float("nan")], 6),
                   JOURNAL_VECTOR_WIDTH_KEY: JOURNAL_VECTOR_WIDTH_F32})
    # 11c. ⛔ AND THE TYPE TESTS ARE IDENTITY, NOT MEMBERSHIP (#11996 review
    #      round 17, measured). `type(x) not in (list, tuple)` is
    #      `not (list == type(x) or tuple == type(x))`, so it runs the REFLECTED
    #      `type(x).__eq__(list)` — the METACLASS's `__eq__` — and a carrier whose
    #      metaclass answers `True` passed BOTH arms while the encoder wrote what
    #      the guard never read:
    #
    #        class _M(type):
    #            def __eq__(cls, other): return True
    #            def __hash__(cls): return id(cls)
    #
    #      Measured before this row existed: a `_Staged` under such a metaclass
    #      landed `f32` over `[NaN]`, and a `_Sneaky`-shaped `float` subclass
    #      landed it with no staging at all. Falsifier: restore either
    #      `not in (...)` membership test and the matching row below lands
    #      (measured). ⛔ Every neighbouring arm already compared by identity for
    #      this reason, which is why the two membership forms were the exception
    #      rather than the rule.
    class _LyingMeta(type):
        def __eq__(cls, other):
            return True

        def __hash__(cls):
            return id(cls)

    class _MetaStaged(list, metaclass=_LyingMeta):
        def __iter__(self):
            return iter([0.5])

    class _MetaSneaky(float, metaclass=_LyingMeta):
        def __float__(self):
            return 0.5

    for _width in (JOURNAL_VECTOR_WIDTH_F32, JOURNAL_VECTOR_WIDTH_F64):
        with pytest.raises(ValueError, match="only an exact `list`"):
            api._emit("PointAdded",
                      {"id": f"p-meta-list-{_width}", "content": "c",
                       "embedding": _MetaStaged([float("nan"), 0.5]),
                       JOURNAL_VECTOR_WIDTH_KEY: _width})
        with pytest.raises(ValueError, match="not an exact"):
            api._emit("PointAdded",
                      {"id": f"p-meta-float-{_width}", "content": "c",
                       "embedding": [_MetaSneaky(float("nan"))],
                       JOURNAL_VECTOR_WIDTH_KEY: _width})
    with pytest.raises(ValueError, match="no float reproduces exactly"):
        api._emit("PointAdded", {"id": "p-bigint", "content": "c",
                                 "embedding": [2 ** 53 + 1],
                                 JOURNAL_VECTOR_WIDTH_KEY: JOURNAL_VECTOR_WIDTH_F32})
    # …and an `int` the float conversion cannot represent AT ALL is the same
    # verdict, with the same exception TYPE — both widths, because the element
    # arm runs before the `f32` narrowing.
    # ⛔ AND BOTH HAVE THEIR OWN MESSAGE (#11996 review round 13, measured): these
    # are exact `int`s, so `match="not an exact"` pinned a sentence that
    # contradicted itself ("…that is not an exact `int` / `float` (int)") and told
    # the caller to resend the same value. The TYPE arm and the MAGNITUDE arm are
    # separate refusals now, and these rows pin the magnitude one.
    for _width in (JOURNAL_VECTOR_WIDTH_F32, JOURNAL_VECTOR_WIDTH_F64):
        with pytest.raises(ValueError, match="no float reproduces exactly"):
            api._emit("PointAdded",
                      {"id": f"p-hugeint-{_width}", "content": "c",
                       "embedding": [2 ** 2000],
                       JOURNAL_VECTOR_WIDTH_KEY: _width})
    # 12. ⛔ THE TWO ABSTENTION ARMS ARE DEFENCE IN DEPTH, and this is the row
    #     that reaches them (#11996 review round 7, measured). The element arm
    #     above refuses every LIST element that is not an exact `int`/`float`, so
    #     the only vector left for the seam arms is one this guard cannot
    #     enumerate — here a numpy object array, whose single element raises
    #     from `float()`. Without the fix each row below is not a forged record
    #     but a `TypeError` at the JSON encoder (the array is not serialisable),
    #     which is exactly why the element arm, and not this one, is the guard
    #     that closes the harm: Falsifier — revert the import/call split in
    #     `_verify_record_vector_width` and the `f64` row here reads `TypeError`;
    #     revert the `f32` arm's `truthful = False` to `return` and the `f32` row
    #     does the same (both measured).
    class _Boom:
        def __float__(self):
            raise RuntimeError("boom")

    for _width, _match in ((JOURNAL_VECTOR_WIDTH_F64, "cannot normalise"),
                           (JOURNAL_VECTOR_WIDTH_F32, "is not what the values say")):
        with pytest.raises(ValueError, match=_match):
            api._emit("PointAdded",
                      {"id": f"p-boom-{_width}", "content": "c",
                       "embedding": np.array([_Boom()], dtype=object),
                       JOURNAL_VECTOR_WIDTH_KEY: _width})
    _tail = [json.loads(ln) for ln in
             journal.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert [_r["point"][JOURNAL_VECTOR_WIDTH_KEY] for _r in _tail[-2:]] == [
        JOURNAL_VECTOR_WIDTH_F32, JOURNAL_VECTOR_WIDTH_F64]


def test_a_declared_width_the_seam_never_mints_is_refused_not_ignored(tmp_path):
    """#11948 F1: `_emit`'s positional record ENUMERATES the declared width.

    The guard used to compare against `f32` and return for everything else, so
    every OTHER declaration was worse than unverified — it was UNEXAMINED, on
    the one channel built to be unreachable from any door. Measured at
    `cb868c43ff` (the head this row was added at), through the private channel:

        _emit("PointAdded", {"id": "p", "content": "c",
                             "embedding": [0.1], "embedding_width": "f16"})
          -> journalled with `embedding_width: "f16"`   ← same for "f32 ",
             "F32", "garbage" and a non-string declaration

    That is not "undecidable input" (which keeps the pre-guard posture): it is a
    definite claim `stamp_journal_embedding` can never produce — it writes
    exactly `f32` or `f64` — and it becomes a durable journal FACT that a later
    width/base64 migration trusts. The seam's mint enumeration is closed, so the
    declaration is refused; the production widths are untouched.

    Falsifier: restore `if declared is None or declared != _F32: return` and every
    DECLARATION row below (1–1d) reads `DID NOT RAISE`. ⛔ Row 5 does NOT — the
    mutant disables the PRE-stamp arm only, so the post-stamp belt still refuses
    the merged-view lie with the same message row 5 matches (measured, review
    round 21). "Every row below" was an over-claim, and it mattered: row 5 is the
    only row that pins the belt's independent reach, which the end of this test
    asserts.
    """
    from tortoise.api import EventAPI, provenance
    from tortoise.log import EventLog

    events = tmp_path / "unenumerated-width"
    events.mkdir(parents=True)
    journal = events / "events.jsonl"
    api = EventAPI(EventLog(journal), initiated_by="extractor", projection=None)

    # 1. Every declaration the seam cannot mint is REFUSED — not journalled.
    for bogus in ("f16", "f32 ", "F32", "garbage", "", "f64 "):
        with pytest.raises(ValueError, match="is not a width this seam mints"):
            api._emit("PointAdded", {"id": f"p-{bogus!r}", "content": "c",
                                     "embedding": [0.1, 0.2],
                                     JOURNAL_VECTOR_WIDTH_KEY: bogus})

    # 1b. …and a NON-STRING declaration is refused as the same refusal. The
    #     guarantee this row pins is that the enumeration is `str`-ONLY: written
    #     as `isinstance(declared, str) and declared not in (…)` these shapes
    #     would LAND (`32`, `True` and `["f32"]` all compare unequal and so pass
    #     the value half). ⛔ NOT "a `TypeError` escaping the membership test" —
    #     an earlier revision said that and it is false: the container is a
    #     TUPLE, so membership is an `==` scan that never hashes, and none of
    #     these shapes raises. Row 1d is the one that pins the exact-TYPE arm
    #     (`type(declared) is not str`); this row pins the refuse-non-`str` half.
    for bogus in (["f32"], {"f32"}, 32, True):
        with pytest.raises(ValueError, match="is not a width this seam mints"):
            api._emit("PointAdded", {"id": "p-nonstr", "content": "c",
                                     "embedding": [0.1, 0.2],
                                     JOURNAL_VECTOR_WIDTH_KEY: bogus})

    # 1b'. …and `None` is a DECLARATION, not an absence (#11996 review round
    #      14, measured). Written as `declared = record.get(KEY); if declared is
    #      None: return`, an explicit `embedding_width: None` was read as "no
    #      declaration" and journalled as `"embedding_width": null` — the one
    #      value that is neither of the seam's two widths, and a durable fact the
    #      seam can never mint (it POPS the key when there is no vector).
    #      Falsifier: restore the `record.get` form and this row reads
    #      `DID NOT RAISE ValueError` (measured). Whether ABSENCE stays accepted
    #      is pinned at the end of this row set, so this refusal cannot be bought
    #      by refusing a record that simply declares nothing.
    with pytest.raises(ValueError, match="is not a width this seam mints"):
        api._emit("PointAdded", {"id": "p-none-width", "content": "c",
                                 "embedding": [0.1, 0.2],
                                 JOURNAL_VECTOR_WIDTH_KEY: None})

    # 1c. ⛔ THE TYPE ARM, which needs its own shape to be falsifiable: a
    #     non-`str` whose `__eq__` answers `True` for `"f32"` passes tuple
    #     membership (`Liar() in ("f32", "f64")` is `True`). Falsifier: change
    #     `type(declared) is not str` to `not isinstance(declared, str)` in
    #     `_verify_record_vector_width`, and row 1d reads `DID NOT RAISE`
    #     (measured).
    #     ⛔ The liar subclasses `float` deliberately, so it is JSON-serialisable:
    #     a non-serialisable liar reds the same row for the WRONG reason (the
    #     mutant would die at the append with `TypeError: … not JSON
    #     serializable`), and a row that reds for the wrong reason is not a pin.
    #     With the type arm dropped, `declared != F32` is TRUE for this liar
    #     (`float.__ne__` is not overridden), so it is routed to the `f64` arm and
    #     JOURNALLED as `0.0` (`_LyingWidth()` is `float()`; `json` renders the subclass
    #     by its float value) — it does not reach the `f32` lie check.
    class _LyingWidth(float):
        def __eq__(self, other):
            return other in (JOURNAL_VECTOR_WIDTH_F32, JOURNAL_VECTOR_WIDTH_F64)

        def __repr__(self):
            return "<a width the seam never mints>"

    with pytest.raises(ValueError, match="is not a width this seam mints"):
        api._emit("PointAdded", {"id": "p-lying-eq", "content": "c",
                                 "embedding": [0.1, 0.2],
                                 JOURNAL_VECTOR_WIDTH_KEY: _LyingWidth()})

    # 1d. ⛔ AND THE SAME LIE FROM A `str` SUBCLASS, which is why the type arm is
    #     `type(declared) is not str` and NOT `isinstance` (#11996 review round 4,
    #     measured): Python gives the SUBCLASS's `__eq__` priority in the
    #     membership scan, so an `isinstance` form passes BOTH halves and the
    #     record is journalled as `"zz"` — a width the seam never mints. Falsifier:
    #     change the arm back to `not isinstance(declared, str) or` and this row
    #     reads `DID NOT RAISE`.
    class _StrLyingWidth(str):
        def __eq__(self, other):
            return other in (JOURNAL_VECTOR_WIDTH_F32, JOURNAL_VECTOR_WIDTH_F64)

        def __repr__(self):
            return "<a str width the seam never mints>"

    with pytest.raises(ValueError, match="is not a width this seam mints"):
        api._emit("PointAdded", {"id": "p-str-lying-eq", "content": "c",
                                 "embedding": [0.1, 0.2],
                                 JOURNAL_VECTOR_WIDTH_KEY: _StrLyingWidth("zz")})

    # 2. The refusal precedes the COMMIT POINT: nothing above reached the log.
    assert not journal.exists() or not journal.read_text().strip(), (
        "a refused declaration was journalled anyway — the guard must run "
        "before `self.log.append`, or the claim is already durable")

    # 3. The seam's OWN two widths are still accepted, so the enumeration did
    #    not become a blanket refusal of the channel.
    api._emit("PointAdded", {"id": "p-f64", "content": "c",
                             "embedding": [0.1, 0.2],
                             JOURNAL_VECTOR_WIDTH_KEY: JOURNAL_VECTOR_WIDTH_F64})
    api.add_point("a real point", provenance("src:2", [0, 4], "q"),
                  embedding=[0.1, 0.2])
    records = [json.loads(ln) for ln in
               journal.read_text(encoding="utf-8").splitlines() if ln.strip()]
    widths = [r["point"].get(JOURNAL_VECTOR_WIDTH_KEY) for r in records]
    assert widths == ["f64", "f64"], (
        "a declared `f64` and the door's own stamp must both still land — the "
        f"enumeration refuses only what the seam cannot mint, got {widths}")
    assert all(r["point"].get("embedding") == [0.1, 0.2] for r in records), (
        "the accepted records' vectors must reach the journal untouched")

    # 4. …and a record that declares NO width at all still lands, untouched: the
    #    `None` row above must not have been bought by refusing absence.
    api._emit("PointAdded", {"id": "p-absent-width", "content": "c",
                             "embedding": [0.1, 0.2]})
    tail = [json.loads(ln) for ln in
            journal.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert tail[-1]["point"]["id"] == "p-absent-width"
    assert JOURNAL_VECTOR_WIDTH_KEY not in tail[-1]["point"], (
        "a record that declares no width must land without the guard adding "
        "one — `embedding_width` is the seam's to mint, not this guard's")

    # 5. ⛔ THE MERGED VIEW DECLARES THE WIDTH, not the record alone (#11996
    #    review round 15, measured). The READER takes `{**ev, **ev["point"]}`,
    #    so when the ENVELOPE carries the width (which is where the seam mints
    #    it) and the RECORD carries the vector, reading only the record's
    #    declaration abstained on the pair the fold actually sees:
    #
    #      _emit("PointAdded", {"id": "h", "content": "c",
    #                           "embedding": [0.1234567890123, 1/3]},
    #            embedding=[0.5, 0.25])   -> journalled `f32` over the OFF-GRID
    #                                        record vector the fold then reads
    #
    #    Falsifier: restore the RECORD-ONLY read (`declared = record.get(KEY)`
    #    plus `if declared is None: return`, which is how it was written) and this
    #    row lands. ⛔ Restoring the ASSIGNMENT ALONE reds it on a different
    #    message instead — "embedding_width=None is not a width this seam mints"
    #    — because the run then reads `payload.get(KEY)` as `None` and the
    #    enumeration arm fires first (measured, review round 21: the two-part
    #    mutant is what the comment must name, or a maintainer runs half of it and
    #    reads a refusal about `None` over a record that declared nothing).
    before = len([ln for ln in
                  journal.read_text(encoding="utf-8").splitlines() if ln.strip()])
    with pytest.raises(ValueError, match="is not what the values say"):
        api._emit("PointAdded", {"id": "p-merged-lie", "content": "c",
                                 "embedding": [0.1234567890123, 1 / 3]},
                  embedding=[0.5, 0.25])
    assert len([ln for ln in
                journal.read_text(encoding="utf-8").splitlines() if ln.strip()]) == before, (
        "the merged-pair refusal must precede the append")
    # …and the TRUTHFUL version of the same shape still lands, so the row above
    # is a refusal of the lie rather than of the shape.
    api._emit("PointAdded", {"id": "p-merged-truth", "content": "c",
                             "embedding": [0.5, 0.25]},
              embedding=[0.25, 0.5])
    merged = [json.loads(ln) for ln in
              journal.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert merged[-1]["point"]["id"] == "p-merged-truth"


def test_a_declared_width_reads_a_one_shot_iterable_exactly_once(tmp_path):
    """#11996 round 14: the guard's seam probe must not EAT the caller's vector.

    Every arm of `_verify_record_vector_width` asks the seam, and the seam reads
    with `list(raw)`; the stamp that runs afterwards asks it a second time. A
    generator is exhausted by the first read, so the second saw an empty vector:
    the probe's own reading was fine, the stamp dropped it, and the post-stamp
    belt then refused with "declared over NO vector". Measured consequence — a
    write that LANDED on `main` became a refusal:

        _emit("PointAdded", {… KEY: "f64"}, embedding=(x for x in [0.1, 0.2]))
          -> `main`: journalled `f64` over [0.1, 0.2]
          -> without materialisation: "declared over NO vector", nothing journalled

    Falsifier: delete the `seam_values = list(values)` materialisation (leave the
    probes reading `values`) and this row reds with that belt refusal, because the
    stamp consumed the generator before the belt ran (measured).

    Both channels are pinned, because the write-back that makes this work is
    PER-CHANNEL and one of them was missing (#11996 review round 23, measured).
    The round-14 write-back covered the ENVELOPE only, so a RECORD-carried
    generator kept the exhausted iterator and the post-stamp belt refused a value
    the seam ACCEPTS — under `f64` as "declared over a value the seam can only
    DROP", under `f32` as "declared over an EMPTY vector", while
    `stamp_journal_embedding({"embedding": (x for x in [0.1, 0.2])}, creating=False)`
    returns `[0.1, 0.2]`. Both messages named a cause that is not the cause.
    Falsifier for
    the record rows: drop the record write-back and this row reds at the FIRST
    record emit — `p-rec-gen-f64`, "declared over a value the seam can only DROP"
    (measured). ⛔ The `f32` "declared over an EMPTY vector" refusal is real but is
    NOT co-observed here: the row aborts on the `f64` emit before the `f32` one
    runs, so it reproduces only when that shape is probed alone (measured). An
    earlier revision of this sentence claimed the record rows red "with those two
    belt refusals", which this row's own control flow falsifies (#11996 review
    round 24).

    ⛔ What is NOT pinned here, stated rather than implied (review round 24,
    measured) — `iter(x) is x` is the arm's CONSERVATIVE test for a one-shot
    iterator, not the boundary of one:

    * a CONTAINER whose `__iter__` hands out a CACHED iterator is also one-shot
      (`iter(shared) is not shared` — the same object) and is NOT caught, so the
      record channel still raises the fabricated "the seam can only DROP" / "an
      EMPTY vector" message for it while the same object lands on the envelope
      channel;
    * a RE-ITERABLE non-container (a numpy array) is left as it is on purpose —
      on the record channel it never reaches the element arm (that arm's container
      test skips it) and fails later at the JSON encoder with `TypeError: not JSON
      serializable`, while the envelope channel lands it.

    Both are the stated residual `#11948-non-list-iterable`. ⛔ Widening the gate
    would NOT turn it into an over-block — an earlier revision of this paragraph
    said so, and measurement says the opposite (#11996 review round 25): the
    cached-iterator container would LAND on the record channel and the numpy
    array's refusal would move from the JSON encoder to the element arm. Widening
    is therefore a DECISION recorded in the PR body's `residuals=` line, and this
    docstring does not assume it in either direction.
    """
    from tortoise.api import EventAPI
    from tortoise.log import EventLog

    events = tmp_path / "one-shot-vector"
    events.mkdir(parents=True)
    journal = events / "events.jsonl"
    api = EventAPI(EventLog(journal), initiated_by="extractor", projection=None)

    api._emit("PointAdded", {"id": "p-gen-f64", "content": "c",
                             JOURNAL_VECTOR_WIDTH_KEY: JOURNAL_VECTOR_WIDTH_F64},
              embedding=(x for x in [0.1, 0.2]))
    api._emit("PointAdded", {"id": "p-gen-f32", "content": "c",
                             JOURNAL_VECTOR_WIDTH_KEY: JOURNAL_VECTOR_WIDTH_F32},
              embedding=(x for x in shortest_float32_vector([0.5, 0.25])))
    # …and the same two on the RECORD channel, which had no write-back at all
    # until round 23 — the channel the merged view lets WIN.
    api._emit("PointAdded", {"id": "p-rec-gen-f64", "content": "c",
                             JOURNAL_VECTOR_WIDTH_KEY: JOURNAL_VECTOR_WIDTH_F64,
                             "embedding": (x for x in [0.1, 0.2])})
    api._emit("PointAdded", {"id": "p-rec-gen-f32", "content": "c",
                             JOURNAL_VECTOR_WIDTH_KEY: JOURNAL_VECTOR_WIDTH_F32,
                             "embedding": (x for x in
                                           shortest_float32_vector([0.5, 0.25]))})
    api._emit("PointAdded", {"id": "p-rec-iter", "content": "c",
                             JOURNAL_VECTOR_WIDTH_KEY: JOURNAL_VECTOR_WIDTH_F64,
                             "embedding": iter([0.1, 0.2])})

    records = [json.loads(ln) for ln in
               journal.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert [r["point"]["id"] for r in records] == [
        "p-gen-f64", "p-gen-f32", "p-rec-gen-f64", "p-rec-gen-f32",
        "p-rec-iter"], (
        "a declared width over a one-shot iterable must land exactly like the "
        "same values spelled as a list — on BOTH channels — the guard reads it "
        "ONCE and hands the stamp what it read")
    assert records[0]["embedding"] == [0.1, 0.2], (
        "the materialised vector must be the one journalled, not an exhausted "
        f"iterator, got {records[0].get('embedding')!r}")
    assert records[0]["point"][JOURNAL_VECTOR_WIDTH_KEY] == JOURNAL_VECTOR_WIDTH_F64
    assert records[1]["point"][JOURNAL_VECTOR_WIDTH_KEY] == JOURNAL_VECTOR_WIDTH_F32
    # The record channel journals under `point` (the merged view the fold reads),
    # so the materialised list must be THERE — an exhausted iterator left on the
    # record is what the belt re-read as empty.
    assert records[2]["point"]["embedding"] == [0.1, 0.2], (
        "a RECORD-carried one-shot iterable must be written back, not left "
        f"exhausted on the record, got {records[2]['point'].get('embedding')!r}")
    assert records[2]["point"][JOURNAL_VECTOR_WIDTH_KEY] == JOURNAL_VECTOR_WIDTH_F64
    assert records[3]["point"][JOURNAL_VECTOR_WIDTH_KEY] == JOURNAL_VECTOR_WIDTH_F32
    assert records[4]["point"]["embedding"] == [0.1, 0.2], (
        "every one-shot iterator type must be written back, not only `generator` "
        f"— got {records[4]['point'].get('embedding')!r}")


def test_the_two_one_shot_shapes_outside_the_write_back_keep_their_residual(tmp_path):
    """The residual `#11948-non-list-iterable`, PINNED as a disposition.

    `_verify_record_vector_width`'s record write-back is gated on
    `iter(x) is x` — a CONSERVATIVE test for "a second read is EMPTY, not merely
    redundant" (#11996 review round 24, measured). Two shapes fall outside it and
    this row pins what happens to them, so the prose in that arm cannot go stale
    the way "it reaches the element check" did:

    1. a CONTAINER whose `__iter__` hands out a CACHED iterator — also one-shot,
       `iter(shared) is not shared`, so the gate does not catch it and
       the record channel refuses a vector the seam ACCEPTS (the message it raises
       names the wrong cause: the seam does not DROP it — that is the residual's
       cost, not a guarantee of the arm);
    2. a RE-ITERABLE non-container (a numpy array) — never reaches the element arm
       on this channel (the arm's container test skips it), so it dies at the JSON
       encoder instead.

    BOTH land on the ENVELOPE channel, which is what makes this a CHANNEL
    asymmetry rather than a rule about the value. ⛔ WIDENING THE GATE IS A
    DECISION and this row is its tripwire — but NOT because widening would
    over-block (an earlier revision of this docstring said that; measured, review
    round 25, it is the opposite): widening to the identity half would LAND the
    cached-iterator container on the record channel and MOVE the numpy array's
    refusal from the JSON encoder to the element arm. It is recorded as deferred
    in the PR body's `residuals=` line — if someone widens it, come here,
    re-measure, and update both the arm's comment and this docstring.

    FAILS IF: either shape starts LANDING on the record channel (the residual was
    widened without the decision), the envelope control stops landing (the
    asymmetry claim itself is then false), or a refusal's MESSAGE moves — both
    `raises` carry `match=` on the width-specific text the arm's comment quotes,
    at both widths, because a reworded belt would otherwise leave that prose stale
    with the row still green (#11996 review round 25).
    """
    import numpy as np

    from tortoise.api import EventAPI
    from tortoise.log import EventLog

    class Shared:
        """A one-shot vector that is NOT its own iterator (`iter(x) is not x`)."""

        def __init__(self, values):
            self._it = iter(values)

        def __iter__(self):
            return self._it

    events = tmp_path / "residual-shapes"
    events.mkdir(parents=True)
    journal = events / "events.jsonl"
    api = EventAPI(EventLog(journal), initiated_by="extractor", projection=None)

    shared = Shared([0.1, 0.2])
    assert iter(shared) is not shared, (
        "the premise of the gate: a cached-iterator CONTAINER is not its own "
        "iterator, so `iter(x) is x` cannot see it")

    # 1. The cached-iterator container is REFUSED on the record channel…
    # ⛔ `match=` on BOTH widths, because the messages are the CLAIM this row's
    # docstring makes (the seam does not DROP the vector; the refusal says it
    # does). Without `match=` any `ValueError` satisfies it, so the belt could
    # reword itself — or start refusing `f32` differently — and leave the prose
    # stale, which is the exact failure this row exists to prevent
    # (#11996 review round 25).
    with pytest.raises(ValueError, match="declared over a value the seam can only DROP"):
        api._emit("PointAdded", {"id": "p-shared-rec", "content": "c",
                                 JOURNAL_VECTOR_WIDTH_KEY: JOURNAL_VECTOR_WIDTH_F64,
                                 "embedding": Shared([0.1, 0.2])})
    with pytest.raises(ValueError, match="declared over an EMPTY vector"):
        api._emit("PointAdded", {"id": "p-shared-rec-f32", "content": "c",
                                 JOURNAL_VECTOR_WIDTH_KEY: JOURNAL_VECTOR_WIDTH_F32,
                                 "embedding": Shared([0.5, 0.25])})
    # …and LANDS on the envelope channel, at both widths.
    api._emit("PointAdded", {"id": "p-shared-env", "content": "c",
                             JOURNAL_VECTOR_WIDTH_KEY: JOURNAL_VECTOR_WIDTH_F64},
              embedding=Shared([0.1, 0.2]))
    api._emit("PointAdded", {"id": "p-shared-env-f32", "content": "c",
                             JOURNAL_VECTOR_WIDTH_KEY: JOURNAL_VECTOR_WIDTH_F32},
              embedding=Shared([0.5, 0.25]))

    # 2. The re-iterable non-container dies at the ENCODER on the record channel…
    with pytest.raises(TypeError, match="not JSON serializable"):
        api._emit("PointAdded", {"id": "p-nd-rec", "content": "c",
                                 JOURNAL_VECTOR_WIDTH_KEY: JOURNAL_VECTOR_WIDTH_F64,
                                 "embedding": np.array([0.1, 0.2])})
    # …and LANDS on the envelope channel.
    api._emit("PointAdded", {"id": "p-nd-env", "content": "c",
                             JOURNAL_VECTOR_WIDTH_KEY: JOURNAL_VECTOR_WIDTH_F64},
              embedding=np.array([0.1, 0.2]))

    records = [json.loads(ln) for ln in
               journal.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert [r["point"]["id"] for r in records] == [
        "p-shared-env", "p-shared-env-f32", "p-nd-env"], (
        "only the envelope-channel controls may land; a record-channel LANDING "
        "here means the residual was widened and this row must be re-decided, "
        f"got {[r['point']['id'] for r in records]}")
    assert records[0]["embedding"] == [0.1, 0.2], (
        "the envelope channel must journal the materialised vector, not the "
        f"one-shot object, got {records[0].get('embedding')!r}")


def test_an_envelope_carried_one_shot_iterable_is_not_written_onto_the_record(
        tmp_path):
    """The write-back is scoped by IDENTITY, not by "the value is one-shot".

    The round-23 write-back exists because the vector is read a SECOND time after
    this guard returns, on whichever channel carried it, and the second reader is
    CHANNEL-specific: the stamp re-reads the ENVELOPE's top-level `embedding`, and
    the post-stamp belt — the second `_verify_record_vector_width` call inside
    `_emit` — re-reads the MERGED view (`{**payload, **record}`, the record winning
    per key), so only the BELT can re-read a RECORD-carried vector (measured,
    #11996 review round 25: a record-carried generator is still unconsumed after
    the stamp returns). A channel left holding the object the guard consumed is
    therefore read a second time and refuses a value the guard had just ACCEPTED.
    Hence the scope is the channel that actually CARRIED the vector:
    `record.get("embedding") is values`.

    ⛔ THE IDENTITY HALF WAS UNPINNED until this row (#11996 review round 24,
    measured): the neighbouring rows exercise the arm's OTHER half — `iter(x) is
    x` — so replacing the whole gate with `if iter(values) is values:` leaves this
    row as the SOLE red in this file (measured; every other row stays green at
    both heads this was measured on). What that mutation changes is not cosmetic,
    because the RECORD WINS the merge: a one-shot generator riding the ENVELOPE
    gets written onto the RECORD as well, and the journalled `point` gains an
    `embedding` key it never carried.

    FAILS IF: the record gains an `embedding` it did not carry (the gate lost its
    identity half), or the envelope stops carrying the materialised vector (the
    write-back was removed rather than scoped).
    """
    from tortoise.api import EventAPI
    from tortoise.log import EventLog

    events = tmp_path / "envelope-one-shot"
    events.mkdir(parents=True)
    journal = events / "events.jsonl"
    api = EventAPI(EventLog(journal), initiated_by="extractor", projection=None)

    # The ENVELOPE carries the generator; the RECORD carries the WIDTH and nothing
    # else — no `embedding` key at all, so nothing on the record is the object the
    # guard consumed and there is nothing there to write back onto.
    api._emit("PointAdded", {"id": "p-env", "content": "c",
                             JOURNAL_VECTOR_WIDTH_KEY: JOURNAL_VECTOR_WIDTH_F64},
              embedding=(x for x in [0.1, 0.2]))

    records = [json.loads(ln) for ln in
               journal.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(records) == 1, f"one emit must journal one line, got {len(records)}"
    assert records[0]["embedding"] == [0.1, 0.2], (
        "the ENVELOPE still carries the materialised vector — the write-back is "
        f"scoped by channel, not a no-op; got {records[0].get('embedding')!r}")
    assert "embedding" not in records[0]["point"], (
        "the RECORD did not carry the vector, so the write-back must not put one "
        "there: the record WINS the merged view, so adding it changes what lands — "
        f"got {records[0]['point'].get('embedding')!r}")
