"""#13528 — a READ must not spend its budget acquiring the embedder.

THE DEFECT THIS PINS
--------------------
``GET /v1/search`` and ``POST /v1/context`` breached their budget on the hosted
box with the stage instrument naming ``embed_get`` while ``pre_handler`` had
already eaten 7.2–9.3 s::

    stage=embed_get  stages_ms={'pre_handler': 9295.0}   HTTP=504   t=11.52s

``pre_handler`` is measured from the transport ARRIVAL to the first stage mark,
i.e. it includes *"queueing for a worker slot"* (``tortoise/read_stages.py``).
The read lane is 8 daemon workers (``CONTROL_PLANE_READ_WORKERS``); the read
itself runs on it (``hosted_api._read_offload`` → ``pool="read"``), and
``sdk.tortoise_fts_query`` calls ``EmbeddingModel.get()`` **synchronously** on
that worker. ``get()`` blocks for the WHOLE one-time load — the caller's own
``load_timeout`` cannot bound it, because the time is spent waiting for
``cls._lock``, which the loading thread holds across ``__init__`` — and the
recorded figures are 13.5 s on a warm cache and ~57 s cold (#13224/#1349).

So a cold or slow embedder parks every read worker in the same lock, the reads
behind them queue, and each one then pays the same wait a second time inside
``embed_get``. The two halves of the measurement are the same root.

#13224 fixed the WRITE half of this (``EmbeddingModel.await_write_ready_reason``:
a create kicks the load, waits a small budget and writes its row without a
vector). This file pins the READ half, to the same shape.

WHAT IS ASSERTED
----------------
The invariant, not the mechanism: **while an embedder load is in flight, a read
returns quickly, with the vector leg absent and its cause RECORDED**, instead of
waiting for the load. A future refactor that puts the blocking ``get()`` back
fails here regardless of how it is written. The counter-tests keep the fix
honest in both directions — a WARM read must still run its dense leg (the fix may
not become "stop embedding"), the declared ``TORTOISE_EMBEDDER_WARMUP=0`` opt-out
must keep the old synchronous contract, and ``TORTOISE_EMBEDDING_MODEL_REQUIRED``
must still fail LOUD rather than hand an armed process a keyword-only read.

⛔ Indicator 5 of the issue — the instrument must SURVIVE the fix — is pinned
directly: ``embed_get`` and ``embed_encode`` stay distinct marks, and the cold
read charges the load to neither stage (it never waits), which is the reading a
future breach has to reproduce.

Run (embedded lane; URI-less by design)::

    TORTOISE_TEST_CARVE_OUT=1 .venv/bin/python -m pytest \\
        tests/test_13528_read_never_blocks_on_embedder.py -q
"""
from __future__ import annotations

import contextlib
import sys
import threading
import time
from pathlib import Path
from unittest import mock

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise import embeddings, read_stages
from tortoise.embeddings import (
    READ_EMBED_BUDGET_S,
    EmbeddingModel,
)
from tortoise.exceptions import EmbedderUnavailableError
from tortoise.read_stages import (
    STAGE_EMBED_ENCODE,
    STAGE_EMBED_GET,
    begin,
    request_stages,
)
from tortoise.sdk import TortoiseSDK

#: How long the simulated cold load takes. Two jobs at once: long enough that
#: "blocked on the load" is unmistakable in a wall-clock assertion with margin
#: over an unloaded box's read cost, and short enough to keep the suite quick.
#: The real figure is 13.5 s warm-cache / ~57 s cold (#1349), so no test may use
#: it — the DISCRIMINATOR is the comparison against this number, never the
#: number itself.
_LOAD_S = 8.0

_DIM = 384

#: The read the tests drive. No store content is needed: the vector leg is
#: decided BEFORE any retrieval runs, so an empty graph is exactly the shape
#: that isolates "did the read acquire the embedder?" from retrieval work.
_QUERY = "retention window"


class _FakeEncoder:
    """A loaded-model stand-in: encodes without torch, deterministically."""

    def encode(self, texts, **kwargs):
        return np.array([[0.1] * _DIM for _ in texts], dtype=np.float32)


def _fake_slow_init(load_s: float):
    """``EmbeddingModel.__init__`` that takes ``load_s`` and then succeeds."""

    def _init(self, load_timeout=None):
        time.sleep(load_s)
        self._model = _FakeEncoder()

    return _init


@pytest.fixture
def cold_embedder(monkeypatch):
    """Run the body with a load IN FLIGHT and the model still cold, then unwind.

    Mirrors the hosted deployment exactly: the pre-warm thread is started and the
    model is not yet available when the read arrives.

    ⛔ ``TORTOISE_EMBEDDER_WARMUP=1`` is asserted HERE rather than inherited:
    ``tests/conftest.py`` pins it **off** for the whole suite (so a background
    load cannot race explicit embedder stubs), and with the opt-out on the
    bounded path is deliberately inert — the pre-#13528 synchronous contract
    stands. That opt-out is itself pinned by its own test below.
    """
    monkeypatch.setenv("TORTOISE_EMBEDDER_WARMUP", "1")
    EmbeddingModel._reset()
    started = None
    with mock.patch.object(EmbeddingModel, "__init__", _fake_slow_init(_LOAD_S)):
        # what hosted_api._lifespan does
        started = EmbeddingModel.start_warm_up()
        try:
            yield
        finally:
            pass
    # The daemon load thread outlives the patch — join the one this fixture
    # STARTED before resetting, or it would stamp a fake model onto the NEXT
    # test's singleton. Join the object captured at start, never a re-read of
    # ``EmbeddingModel._warm_up_thread`` (a body that calls ``_reset()`` clears
    # that attribute and the join would be silently skipped — #13224's own note).
    if started is not None and started.is_alive():
        started.join(timeout=_LOAD_S + 10)
    EmbeddingModel._reset()


@pytest.fixture
def warm_embedder(monkeypatch):
    """Run the body with the model LOADED (the steady-state hosted shape)."""
    monkeypatch.setenv("TORTOISE_EMBEDDER_WARMUP", "1")
    EmbeddingModel._reset()
    with mock.patch.object(EmbeddingModel, "__init__", _fake_slow_init(0.0)):
        EmbeddingModel.warm_up()
    try:
        yield
    finally:
        EmbeddingModel._reset()


@pytest.fixture
def sdk(tmp_path):
    sdk = TortoiseSDK(db_path=str(tmp_path / "read13528.db"))
    try:
        # ⛔ The embedder state is made DETERMINISTIC before anything else. In a
        # single-file run the singleton starts cold, but the carve-out lane runs
        # this file in one process with ~17 others, several of which leave a REAL
        # model loaded — so "cold" is not a property of the process here, it is a
        # property this fixture has to establish. (Measured on `test-carve-out`
        # for PR #13531: the precondition below fired on all four cases because a
        # previous file's warm singleton survived into this one.)
        EmbeddingModel._reset()
        # PRIME the one-time cost OUTSIDE the cold window. The first read in a
        # process pays start-up that has nothing to do with the embedder — the
        # projection open, `kind_layers.section_of`'s spec compile (measured
        # 811 ms in #13224) — and it can exceed a simulated load outright, which
        # would make every cold-window assertion below measure an SDK open
        # instead of the acquire. Charging that to these tests would make them
        # load-sensitive, which is exactly what #13224's note tells a lane to keep
        # out (it summed two writes for the same reason).
        #
        # ⛔ The fixtures are ORDER DEPENDENT on purpose: every test lists `sdk`
        # BEFORE `cold_embedder` / `warm_embedder`, so this runs while the
        # simulated load has not started. `get` is patched to the absent answer so
        # priming cannot trigger a REAL load into the window either — and
        # `await_read_ready_reason` is patched to the same answer for the same
        # reason: with `TORTOISE_EMBEDDER_WARMUP` restored to 1 it would otherwise
        # kick a background load and leave the embedder warm for the cold tests.
        with mock.patch.object(EmbeddingModel, "get", lambda *_a, **_k: None), \
                mock.patch.object(EmbeddingModel, "await_read_ready_reason",
                                  classmethod(lambda _cls, _budget: None)):
            sdk.tortoise_fts_query(_QUERY, limit=3)
        assert not EmbeddingModel.loaded(), (
            "priming must not load the embedder — the cold-window tests need it COLD")
        yield sdk
    finally:
        # Teardown must not mask a result (#13229): a close() fault would turn a
        # PASSING row into a teardown ERROR. Same shape test_13224 uses.
        with contextlib.suppress(Exception):
            sdk.close()


def _read(sdk, *, trace: list | None = None):
    """One read through the ONE read path, with the trace sink the SDK uses."""
    return sdk.tortoise_fts_query(_QUERY, limit=3, leg_trace=trace)


def _vector_entry(trace: list) -> dict:
    """The vector leg's own trace entry (the first one — the primary leg)."""
    entries = [e for e in trace if e.get("leg") == "vector"]
    assert entries, f"the read must record a vector entry, got {trace!r}"
    return entries[0]


# ── the invariant: a read does not wait for the load ──────────────────────


def test_a_read_does_not_block_on_a_cold_embedder(sdk, cold_embedder):
    """⚠ THE REGRESSION CATCHER — the whole point of #13528.

    Pre-#13528 the read's ``EmbeddingModel.get()`` waits for the WHOLE load:
    the caller's own ``load_timeout`` cannot bound it, because the wait is on
    ``cls._lock`` (held across ``__init__`` by the loading thread), which is why
    production measured ``stage=embed_get`` at 11.52 s against an 8 s bound.

    The discriminator is stated against the LOAD, not a stopwatch constant:
    **a read issued inside a cold window must cost less than the load it
    overlaps.** Pre-fix the read is AT LEAST the load; post-fix it is well
    under, because it never waits. That comparison cannot be satisfied by a
    fast host and cannot fail on a slow one — it fails only if the read path
    waits on the load, which is the defect.

    ⛔ THE BAR IS HALF THE LOAD, NOT THE WHOLE LOAD (#13528 review P2-3).
    ``elapsed < _LOAD_S`` left 0.6 ms of margin against the pre-fix figure this
    change's own title quotes (``embed_get`` 7999.4 ms of an 8000 ms load), so
    the pre-fix code could pass it. Half the load is unambiguous in both
    directions: a read that waits is ~the whole load, a read that declines is
    ~the budget (measured 251.0 ms).
    """
    assert not EmbeddingModel.loaded(), (
        "precondition: the embedder must be COLD when the read arrives")

    t0 = time.perf_counter()
    _read(sdk)
    elapsed = time.perf_counter() - t0

    assert not EmbeddingModel.loaded(), (
        f"the {_LOAD_S:.1f}s load must still be IN FLIGHT — if it finished, this "
        f"run measured the warm path and proves nothing about the cold one")
    assert elapsed < _LOAD_S / 2, (
        f"a read inside a {_LOAD_S:.1f}s cold window took {elapsed:.2f}s (bar: "
        f"{_LOAD_S / 2:.1f}s) — the read path is waiting on the model load again "
        f"(#13528: production measured stage=embed_get at HTTP=504, t=11.52s; "
        f"pre-fix this read was 7999.4 ms of the load)")


def test_the_cold_read_records_its_absent_reason(sdk, cold_embedder):
    """The degrade must be DECLARED, and the declaration must not lie.

    A read that answered without its dense leg is not hybrid retrieval. The leg
    trace is where that is said, and the reason must name THIS state — a load
    is running and the read declined to wait. It is deliberately **not**
    ``no_embedder`` (that is the designed absence, the conflation #5275/#1542
    forbid) and deliberately not ``load_timeout`` either: that term already means
    the load ATTEMPT ran out of its ``_LOAD_TIMEOUT_S`` and BROKE (#13528 review
    P2-1), which needs the opposite operator response — repair the load, not
    read again in a moment.
    """
    trace: list[dict] = []
    _read(sdk, trace=trace)

    entry = _vector_entry(trace)
    assert entry["ran"] is False, "the vector leg cannot have run on a cold embedder"
    assert entry["degraded"] is True, (
        "an absent vector leg is a degraded read, and must be published as one")
    assert entry["reason"] == "load_in_flight", (
        f"the recorded reason must name the cause (a load still RUNNING that the "
        f"read declined to wait for), got {entry['reason']!r}")


def test_the_cold_read_charges_the_load_to_neither_stage(sdk, cold_embedder):
    """Indicator 5 — the instrument survives the fix, and reports the fix.

    ``embed_get`` / ``embed_encode`` stay DISTINCT marks (a future breach must
    still be able to say which half it died in), and on the cold path NEITHER
    may hold the load: the read declines instead of waiting, so ``embed_get``
    is a fast decision rather than a stall, and ``embed_encode`` never opens at
    all.

    ⛔ ``pre_handler`` IS PUBLISHED, BUT NOT ASSERTED (#13535 review F-1). It is
    the arrival-to-first-mark span that published 7.2–9.3 s in production, and
    that figure was reads QUEUED behind workers parked in the load — a
    multi-worker phenomenon this harness cannot reproduce (one thread, no pool).
    Measured with the defect REVERTED and with it fixed: ``pre_handler`` is
    ``0.1`` ms in BOTH, because the first mark is ``embed_get`` and it is emitted
    before the acquire. A bar there would therefore pass under the very defect
    its message names — a guard that cannot speak. What does cover a stall
    before the first mark is the wall-clock bar in
    ``test_a_read_does_not_block_on_a_cold_embedder`` (it starts before any mark
    and fails on a read that waits for the load anywhere).
    """
    timeline = begin()
    with request_stages(timeline):
        _read(sdk)

    spent = timeline.spent_ms()
    assert STAGE_EMBED_GET in spent, (
        f"the cold read must still mark embed_get, got {spent!r}")
    assert STAGE_EMBED_ENCODE not in spent, (
        f"nothing was encoded, so embed_encode must never open, got {spent!r}")
    assert spent[STAGE_EMBED_GET] < _LOAD_S / 2 * 1000.0, (
        f"embed_get took {spent[STAGE_EMBED_GET]} ms of a {_LOAD_S * 1000:.0f} ms "
        f"load — the read is waiting on it again instead of declining inside its "
        f"{READ_EMBED_BUDGET_S * 1000:.0f} ms budget (measured pre-fix: 7857.3 ms)")


# ── the counter-tests: the fix may not become "stop embedding" ────────────


def test_a_warm_read_still_runs_its_dense_leg(sdk, warm_embedder):
    """The bound is a ceiling on the WAIT, never a ceiling on the vector."""
    assert EmbeddingModel.loaded(), "precondition: the embedder must be WARM"

    timeline = begin()
    with request_stages(timeline):
        _read(sdk)

    spent = timeline.spent_ms()
    assert STAGE_EMBED_ENCODE in spent, (
        f"a WARM embedder must still encode, got {spent!r} — the fix may not "
        f"become 'never use the dense leg'")


def test_the_opt_out_keeps_the_pre_13528_synchronous_contract(monkeypatch):
    """``TORTOISE_EMBEDDER_WARMUP=0`` (declared test/dev opt-out) ⇒ unchanged.

    With no background lane to hand the load to, the read must keep loading
    synchronously — otherwise a lane that opts out of the background load would
    silently stop producing vectors. The predicate answers ``None`` ("acquire
    it, the old way") rather than a decline, which is the whole of the contract.

    ⛔ ``tests/conftest.py`` pins this flag off for the WHOLE suite, so this arm
    is also what keeps every existing retrieval test on the synchronous path.
    """
    monkeypatch.setenv("TORTOISE_EMBEDDER_WARMUP", "0")
    EmbeddingModel._reset()
    try:
        assert EmbeddingModel.await_read_ready_reason(0.0) is None, (
            "with the background lane opted out there is nothing to bound, so "
            "the read must keep its pre-#13528 synchronous acquire")
    finally:
        EmbeddingModel._reset()


def test_the_required_flag_still_fails_loud(sdk, cold_embedder, monkeypatch):
    """#4861/#5275 — an ARMED process must never be handed a keyword-only read.

    The read opt-in is ``TORTOISE_EMBEDDING_MODEL_REQUIRED``: it converts the
    silent single-leg degrade into a named refusal. A bounded acquire must not
    quietly convert it back — the decline has to go through the SAME single exit
    every other ``None`` arm uses, or the flag's contract dies with the wait.

    ⛔ THE KIND IS ASSERTED, NOT JUST THE CLASS (#13528 review P1-2). Asserting
    only ``pytest.raises(EmbedderUnavailableError)`` passed on a refusal carrying
    ANY cause — which is exactly how ``failure_kind="no_embedder"`` shipped: the
    trace term was handed straight to a field that only accepts
    ``not_installed`` / ``load_failed`` / ``load_timeout`` / ``model_unavailable``.
    A refusal that names the wrong dependency is the defect #5275 exists to
    prevent, so the name is the assertion.
    """
    monkeypatch.setenv("TORTOISE_EMBEDDING_MODEL_REQUIRED", "1")
    assert not EmbeddingModel.loaded(), "precondition: cold embedder"

    t0 = time.perf_counter()
    with pytest.raises(EmbedderUnavailableError) as excinfo:
        _read(sdk)
    elapsed = time.perf_counter() - t0

    assert excinfo.value.failure_kind == "model_unavailable", (
        f"a load still IN FLIGHT is not a broken load and not a designed "
        f"absence — the refusal must carry model_unavailable, got "
        f"{excinfo.value.failure_kind!r}")
    assert elapsed < _LOAD_S / 2, (
        f"the armed refusal took {elapsed:.2f}s (bar: {_LOAD_S / 2:.1f}s) — it "
        f"waited for the load instead of failing fast with its recorded reason")


def test_a_designed_absence_is_refused_with_not_installed(sdk, monkeypatch):
    """⛔ THE P1-1 REGRESSION — two vocabularies, one translation.

    ``absent_reason()`` publishes the LEG-TRACE term ``no_embedder`` for a
    designed absence, and ``EmbedderUnavailableError.failure_kind`` is a CLOSED
    set whose member for that state is ``not_installed``. ``no_embedder`` is not
    IN that set, and #5275 explicitly forbids an armed refusal from carrying the
    designed-absence term — so passing the trace term through by hand both broke
    the closed set and said the forbidden thing. This pins both halves at once:
    the TRACE gets ``no_embedder``, the REFUSAL gets ``not_installed``.

    Driven directly on the predicate + ``_unavailable`` (the two halves of the
    seam) rather than through a read: a process without the ``embeddings`` extra
    cannot be simulated at runtime, and the mapping is what is under test.
    """
    monkeypatch.setenv("TORTOISE_EMBEDDER_WARMUP", "1")
    EmbeddingModel._reset()
    monkeypatch.setattr(EmbeddingModel, "_last_failure_kind", "not_installed")
    try:
        decline = EmbeddingModel.await_read_ready_reason(READ_EMBED_BUDGET_S)
        assert decline == "no_embedder", (
            f"the TRACE must keep the designed-absence term, got {decline!r}")

        monkeypatch.setenv("TORTOISE_EMBEDDING_MODEL_REQUIRED", "1")
        with pytest.raises(EmbedderUnavailableError) as excinfo:
            EmbeddingModel._unavailable(context="read: designed absence",
                                       kind=decline)
        assert excinfo.value.failure_kind == "not_installed", (
            f"#5275 forbids an armed refusal from carrying the designed-absence "
            f"term — failure_kind must be not_installed, got "
            f"{excinfo.value.failure_kind!r}")
    finally:
        monkeypatch.undo()
        EmbeddingModel._reset()


# ── the re-arm arm: a SPENT attempt is RESTARTED, not reported absent ────


def test_a_spent_read_attempt_is_rearmed_and_spawned(monkeypatch):
    """#13528 review P2-2 — a SPENT load attempt is re-armed **and spawned**.

    ``start_warm_up`` latches once per process, so a read that finds the attempt
    spent re-arms exactly one background attempt and STARTS it before answering.
    Answering ``absent_reason()`` there named a designed absence (or a broken
    load) for a state where nothing was ever absent — a load was merely not
    running — and it never kicked the retry that recovers the embedder.

    ⛔ THE ROW THIS ARM DID NOT HAVE (#13528 review F1). Mutating the block back
    to ``cls._rearm_warm_up(); return cls.absent_reason()`` left every row in
    this file — and every row in ``test_13224_cold_embedder_write_bound.py``,
    whose ``attempt_spent`` arm covers the WRITE twin only — GREEN. Both
    behaviours the block adds (the spawn, and ``load_in_flight`` instead of the
    absence term) were unpinned, so a revert was invisible.

    ``_warm_up_worker`` — the thread BODY — is stubbed so the re-armed attempt
    cannot start a REAL load (~57 s cold, and it would leave the singleton warm
    for the rows after this one). The SPAWN is real: ``start_warm_up`` creates
    and starts the thread, which is what the assertions below read.
    """
    monkeypatch.setenv("TORTOISE_EMBEDDER_WARMUP", "1")
    release = threading.Event()
    try:
        EmbeddingModel._reset()
        EmbeddingModel._last_failure_kind = "load_failed"
        EmbeddingModel._last_failed_at = (
            time.monotonic() - EmbeddingModel._FAIL_COOLDOWN_S - 1)
        # The once-per-process latch SPENT with nothing in flight — the exact
        # state ``start_warm_up`` answers ``None`` for.
        EmbeddingModel._warm_up_started = True
        EmbeddingModel._warm_up_thread = None

        with mock.patch.object(EmbeddingModel, "_warm_up_worker",
                               classmethod(lambda _cls, _lt=None: release.wait(30))):
            t0 = time.perf_counter()
            decline = EmbeddingModel.await_read_ready_reason(
                READ_EMBED_BUDGET_S)
            elapsed = time.perf_counter() - t0

        assert decline == "load_in_flight", (
            f"a read that finds the attempt SPENT must re-arm and answer "
            f"load_in_flight — got {decline!r}, which names a designed absence "
            f"or a broken load for a load that was never absent")
        assert elapsed < READ_EMBED_BUDGET_S, (
            f"the re-armed read waited {elapsed:.2f}s (budget "
            f"{READ_EMBED_BUDGET_S}s) — it must SPAWN the retry and decline, "
            f"not sit out the budget for the attempt it just restarted")
        assert EmbeddingModel._warm_up_started is True, (
            "the re-arm must SPAWN the retry before answering, or the answer "
            "is a claim about a load that does not exist")
        spawned = EmbeddingModel._warm_up_thread
        assert spawned is not None and spawned.is_alive(), (
            "the re-armed attempt must be a LIVE thread, not just a cleared latch")
    finally:
        release.set()
        t = EmbeddingModel._warm_up_thread
        if t is not None and t.is_alive():
            t.join(timeout=30)
        EmbeddingModel._reset()


def test_a_rearm_that_cannot_spawn_answers_absent(monkeypatch):
    """The honesty half: no spawn ⇒ the answer must NOT claim a load in flight.

    The fallback is reachable only when the warm-up flag is switched off between
    the predicate's first check and ``start_warm_up``'s own (it is the same
    process-wide flag, read twice), so the flag is driven from a two-value
    sequence. Delete the fallback and the read answers ``load_in_flight`` for a
    lane that has just opted out — the same false claim, one state over.
    """
    monkeypatch.setenv("TORTOISE_EMBEDDER_WARMUP", "1")
    EmbeddingModel._reset()
    try:
        EmbeddingModel._last_failure_kind = "load_failed"
        EmbeddingModel._last_failed_at = (
            time.monotonic() - EmbeddingModel._FAIL_COOLDOWN_S - 1)
        EmbeddingModel._warm_up_started = True
        EmbeddingModel._warm_up_thread = None
        states = iter((True, False))
        monkeypatch.setattr(embeddings, "_embedder_warmup_enabled",
                            lambda: next(states, False))

        assert EmbeddingModel.await_read_ready_reason(
            READ_EMBED_BUDGET_S) == "load_failed", (
            "with the warm-up switched off underneath the read, nothing was "
            "spawned — so the answer must be absent_reason() (here the recorded "
            "load_failed), never a claim that a load is in flight")
        assert EmbeddingModel._warm_up_started is False, (
            "a lane that cannot spawn must not leave the latch set — a later "
            "read would read it as an attempt in flight")
    finally:
        monkeypatch.undo()
        EmbeddingModel._reset()


# ── the budget itself ─────────────────────────────────────────────────────


def test_the_read_budget_is_bounded_and_far_below_the_read_bound():
    """The budget is a constant, and small against the bound it sits inside.

    ⛔ A PIN, NOT A DISCRIMINATOR (#13528 review P2-3): it holds on the
    pre-change tree too, and is here to stop the number drifting, not to catch
    the defect. The behaviour tests are what redden.

    The read lane's wait bound is 8 s (``CONTROL_PLANE_READ_TIMEOUT_S``,
    justified by a 41–80 ms measured graph answer). A budget near that would
    just move the stall behind a different number; a warm ``get()`` is the
    fast path and a warm encode is 25–43 ms, so the steady state is untouched.
    """
    assert isinstance(READ_EMBED_BUDGET_S, float)
    assert 0.0 < READ_EMBED_BUDGET_S < 1.0, READ_EMBED_BUDGET_S


def test_the_two_stage_names_are_still_distinct():
    """Indicator 5, stated as the vocabulary property rather than the marks.

    ⛔ A PIN, NOT A DISCRIMINATOR (#13528 review P2-3) — see the budget pin
    above; the cold-read stage assertions carry the discriminating half.
    """
    assert STAGE_EMBED_GET != STAGE_EMBED_ENCODE
    assert {STAGE_EMBED_GET, STAGE_EMBED_ENCODE} <= read_stages.STAGE_SET
