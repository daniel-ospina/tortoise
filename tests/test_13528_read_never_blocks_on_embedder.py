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
import time
from pathlib import Path
from unittest import mock

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise import read_stages
from tortoise.embeddings import (
    READ_EMBED_BUDGET_S,
    EmbeddingModel,
)
from tortoise.exceptions import EmbedderUnavailableError
from tortoise.read_stages import (
    STAGE_EMBED_ENCODE,
    STAGE_EMBED_GET,
    STAGE_PRE_HANDLER,
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
        # priming cannot trigger a REAL load into the window either.
        with mock.patch.object(EmbeddingModel, "get", lambda *_a, **_k: None):
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
    **a read issued inside a cold window must cost less than HALF the load it
    overlaps.** Pre-fix the read is AT LEAST the load; post-fix it declines
    inside ``READ_EMBED_BUDGET_S`` (0.25 s), two orders of magnitude under. The
    bar is deliberately half rather than the whole load (#13528 review P2-3):
    the PR's own pre-fix figure was 7999.4 ms against a 8000 ms load, so a
    whole-load bar leaves ~0.6 ms of margin and the pre-fix read can pass on a
    fast box — a discriminator with no margin is not a discriminator. Half the
    load is still ~16× the budget, so the comparison cannot be satisfied by a
    fast host and cannot fail on a slow one — it fails only if the read path
    waits on the load, which is the defect.
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
        f"a read inside a {_LOAD_S:.1f}s cold window took {elapsed:.2f}s — more "
        f"than half the load it overlaps, so the read path is waiting on the "
        f"model load again (#13528: production measured stage=embed_get at "
        f"HTTP=504, t=11.52s; the budget is {READ_EMBED_BUDGET_S * 1000:.0f} ms)")


def test_the_cold_read_records_its_absent_reason(sdk, cold_embedder):
    """The degrade must be DECLARED, not silent, and it must name WHY.

    A read that answered without its dense leg is not hybrid retrieval. The leg
    trace is where that is said, and the reason must name WHY the leg did not
    run — a load the read declined to wait for is ``load_in_flight`` (#13528
    review P2-1), never the designed-absence term ``no_embedder`` (the
    conflation #5275/#1542 exist to forbid: "the extra was never installed" and
    "the embedder is loading RIGHT NOW" need opposite operator responses) and
    never ``load_timeout``, which ``absent_reason`` already publishes for a load
    that actually TIMED OUT — a recorded failure with a different response again.
    """
    trace: list[dict] = []
    _read(sdk, trace=trace)

    entry = _vector_entry(trace)
    assert entry["ran"] is False, "the vector leg cannot have run on a cold embedder"
    assert entry["degraded"] is True, (
        "an absent vector leg is a degraded read, and must be published as one")
    assert entry["reason"] == "load_in_flight", (
        f"the recorded reason must say WHY the leg did not run (a load the read "
        f"declined to wait for) and not borrow the load-failure or "
        f"designed-absence terms, got {entry['reason']!r}")


def test_the_re_armed_read_reports_load_in_flight(monkeypatch):
    """P2-2's OWN row — the RE-ARM arm must not answer the designed-absence word.

    The row above reaches the deadline-expiry arm (a load thread is ALIVE, so the
    poll loop runs). The other arm that returns ``load_in_flight`` is the RE-ARM
    one: ``start_warm_up()`` returns ``None`` because the once-per-process
    attempt is spent and no thread is alive, so the read re-arms one attempt and
    declines. Nothing has failed there either, so the answer must be
    ``load_in_flight`` — an unconditional ``absent_reason()`` answers
    ``no_embedder`` (``_last_failed_at`` is unset), telling the operator the extra
    was never installed while a retry is starting. That was the pre-review shape,
    and nothing in this file isolated it until this row.
    """
    monkeypatch.setenv("TORTOISE_EMBEDDER_WARMUP", "1")
    EmbeddingModel._reset()
    try:
        # The spent-attempt state, built directly: the latch is SET and no load
        # thread is alive, which is exactly what `start_warm_up` answers `None`
        # for — it returns the thread when it IS alive, and the latch then no
        # longer re-arms (`tortoise/embeddings.py`, `start_warm_up`'s
        # `_warm_up_started` branch).
        monkeypatch.setattr(EmbeddingModel, "_instance", None)
        monkeypatch.setattr(EmbeddingModel, "_last_failed_at", None)
        monkeypatch.setattr(EmbeddingModel, "_last_failure_kind", None)
        monkeypatch.setattr(EmbeddingModel, "_warm_up_started", True)
        monkeypatch.setattr(EmbeddingModel, "_warm_up_thread", None)

        assert EmbeddingModel.start_warm_up() is None, (
            "precondition: the once-per-process attempt must be SPENT with "
            "nothing in flight, or this test does not reach the re-arm arm")

        assert EmbeddingModel.await_read_ready_reason(0.0) == "load_in_flight", (
            "a read that just RE-ARMED an attempt has recorded no failure, so it "
            "must not publish the designed-absence term — `no_embedder` says the "
            "extra was never installed (#5275 conflation, one arm over)")
        assert EmbeddingModel._warm_up_started is False, (
            "the read must have RE-ARMED exactly one background attempt; "
            "latching it off is what makes the retry reachable at all")
    finally:
        EmbeddingModel._reset()


def test_the_re_armed_read_reports_a_recorded_failure(monkeypatch):
    """COUNTER-TEST to the row above: a RECORDED failure still wins (P2-2).

    The re-arm arm must not solve the conflation by hiding a real cause: when a
    load attempt failed long enough ago that the cooldown has expired, the arm is
    still reachable, and the operator's answer is the failure that was recorded —
    not "a retry is starting".
    """
    monkeypatch.setenv("TORTOISE_EMBEDDER_WARMUP", "1")
    EmbeddingModel._reset()
    try:
        monkeypatch.setattr(EmbeddingModel, "_instance", None)
        # Outside the negative-cache cooldown, so the arm is reachable.
        monkeypatch.setattr(
            EmbeddingModel, "_last_failed_at",
            time.monotonic() - (EmbeddingModel._FAIL_COOLDOWN_S + 1.0))
        monkeypatch.setattr(EmbeddingModel, "_last_failure_kind", "load_failed")
        monkeypatch.setattr(EmbeddingModel, "_warm_up_started", True)
        monkeypatch.setattr(EmbeddingModel, "_warm_up_thread", None)

        assert EmbeddingModel.await_read_ready_reason(0.0) == "load_failed", (
            "a recorded load failure must be reported even on the re-arm arm — "
            "`load_in_flight` here would hide the failure the operator has to fix")
    finally:
        EmbeddingModel._reset()


def test_the_translation_keeps_a_recorded_cause_for_a_trace_only_reason():
    """The translator's recorded-cause arm, pinned directly (round-1 P2).

    ``load_in_flight`` names no failure, but a load CAN fail between the read's
    decline and the raise that carries it, and ``_last_failure_kind`` then holds
    the operator's real answer. The earlier shape special-cased ``no_embedder``
    and returned the ``model_unavailable`` fallback for everything else, so that
    recorded cause was discarded. No cold-window ROW in this suite reaches that
    state (each leaves nothing recorded), but the READ PATH does: a failure older
    than ``_FAIL_COOLDOWN_S`` leaves ``load_in_flight`` on the trace with a
    recorded kind, and ``_as_failure_kind`` then reports that kind. It is pinned
    at the seam because no fixture reproduces that staleness — not because the
    arm is unreachable.
    """
    EmbeddingModel._reset()
    try:
        assert EmbeddingModel._as_failure_kind("load_in_flight") == \
            "model_unavailable", (
            "with nothing recorded the contract's documented fallback is the "
            "honest answer — the load was in flight, nothing broke")

        EmbeddingModel._last_failure_kind = "load_failed"
        assert EmbeddingModel._as_failure_kind("load_in_flight") == "load_failed", (
            "a cause recorded by the time the refusal is raised must be named, "
            "not replaced by the fallback")

        EmbeddingModel._last_failure_kind = "not_installed"
        assert EmbeddingModel._as_failure_kind("load_in_flight") == "not_installed", (
            "the recorded kind is the answer whatever it is, not only for "
            "`no_embedder` — the special-case shape this replaces")

        assert EmbeddingModel._as_failure_kind("load_timeout") == "load_timeout", (
            "a reason already in the refusal's vocabulary is passed through")
    finally:
        EmbeddingModel._reset()


def test_the_cold_read_charges_the_load_to_neither_stage(sdk, cold_embedder):
    """Indicator 5 — the instrument survives the fix, and reports the fix.

    ``embed_get`` / ``embed_encode`` stay DISTINCT marks (a future breach must
    still be able to say which half it died in), and on the cold path NEITHER
    may hold the load: the read declines instead of waiting, so ``embed_get``
    is a fast decision rather than a stall, and ``embed_encode`` never opens at
    all. ``pre_handler`` must stay small too — the 7.2–9.3 s it published in
    production was reads QUEUED behind workers parked in the load.
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
    assert spent.get(STAGE_PRE_HANDLER, 0.0) < _LOAD_S / 2, (
        f"pre_handler accrued {spent.get(STAGE_PRE_HANDLER)} ms — a read must not "
        f"queue behind workers parked in the embedder load")


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

    ⛔ THE REFUSAL MUST NAME ITS CAUSE (#13528 review P1-2). Asserting only the
    exception class let a wrong attribution through: the trace word
    ``load_timeout`` — a FAILURE term — was handed to the refusal as its
    ``failure_kind`` for a load that had never failed. This row now pins the
    KIND, so restoring that conflation reds here. Nothing has FAILED in this
    scenario — the load is in flight — so the honest cause is the contract's
    ``model_unavailable`` fallback, never a fabricated failure term.

    (The designed-absence half of the same class — an ARMED missing-extra
    process told ``no_embedder`` — is pinned by
    :func:`test_the_required_flag_with_no_extra_reports_not_installed`.)
    """
    monkeypatch.setenv("TORTOISE_EMBEDDING_MODEL_REQUIRED", "1")
    assert not EmbeddingModel.loaded(), "precondition: cold embedder"

    t0 = time.perf_counter()
    with pytest.raises(EmbedderUnavailableError) as ei:
        _read(sdk)
    elapsed = time.perf_counter() - t0

    assert ei.value.failure_kind == "model_unavailable", (
        f"the armed refusal must name the cause it actually resolved — a load "
        f"still in flight has recorded no failure — got "
        f"{ei.value.failure_kind!r}; ``no_embedder`` would tell the operator the "
        f"extra is not installed, which is the #5275 conflation")
    assert elapsed < _LOAD_S / 2, (
        f"the armed refusal took {elapsed:.2f}s — it waited for the load instead "
        f"of failing fast with its recorded reason")


def test_the_required_flag_with_no_extra_reports_not_installed(
        sdk, monkeypatch):
    """An ARMED missing-extra process is refused with ``not_installed`` (#5275).

    THE P1-1 DISCRIMINATOR (#13528 review). The bounded read resolves the cause
    as the leg-trace word ``no_embedder`` (the designed absence, correct on the
    trace), and the ONE exit must translate it into the ``failure_kind`` the
    contract defines for that state — ``not_installed``. On ``main`` the trace
    word itself was published as the cause, so an ARMED process was told "there
    is no embedder" (the designed-absence statement #5275 forbids) where
    ``get()`` had always said ``not_installed``. Passing the trace word through
    verbatim — the defect this pins — makes this row red, while the trace
    assertion below keeps the fix from "solving" it by changing the trace
    vocabulary instead.
    """
    monkeypatch.setenv("TORTOISE_EMBEDDER_WARMUP", "1")
    monkeypatch.setenv("TORTOISE_EMBEDDING_MODEL_REQUIRED", "1")
    EmbeddingModel._reset()
    try:
        # The state a missing-extra runner is actually in: a load attempt failed
        # with `not_installed` and the singleton is in the negative cache.
        monkeypatch.setattr(EmbeddingModel, "_instance", None)
        monkeypatch.setattr(EmbeddingModel, "_last_failed_at", time.monotonic())
        monkeypatch.setattr(EmbeddingModel, "_last_failure_kind", "not_installed")
        monkeypatch.setattr(
            EmbeddingModel, "_last_error",
            "ModuleNotFoundError: sentence-transformers is not installed")

        # The TRACE keeps the designed-absence word...
        assert EmbeddingModel.await_read_ready_reason(0.0) == "no_embedder"

        # ...and the REFUSAL names the cause, not that word.
        with pytest.raises(EmbedderUnavailableError) as ei:
            _read(sdk)
        assert ei.value.failure_kind == "not_installed", (
            f"an armed missing-extra process must be refused with "
            f"``not_installed``, got {ei.value.failure_kind!r} — "
            f"``no_embedder`` is the designed-absence TRACE term and is the "
            f"statement #5275 forbids an armed refusal from making")
        assert ei.value.context, "the refusal must be attributable without reading code"
    finally:
        EmbeddingModel._reset()


# ── the budget itself ─────────────────────────────────────────────────────


def test_the_read_budget_is_bounded_and_far_below_the_read_bound():
    """The budget is a constant, and small against the bound it sits inside.

    The read lane's wait bound is 8 s (``CONTROL_PLANE_READ_TIMEOUT_S``,
    justified by a 41–80 ms measured graph answer). A budget near that would
    just move the stall behind a different number; a warm ``get()`` is the
    fast path and a warm encode is 25–43 ms, so the steady state is untouched.
    """
    assert isinstance(READ_EMBED_BUDGET_S, float)
    assert 0.0 < READ_EMBED_BUDGET_S < 1.0, READ_EMBED_BUDGET_S


def test_the_two_stage_names_are_still_distinct():
    """Indicator 5, stated as the vocabulary property rather than the marks."""
    assert STAGE_EMBED_GET != STAGE_EMBED_ENCODE
    assert {STAGE_EMBED_GET, STAGE_EMBED_ENCODE} <= read_stages.STAGE_SET
