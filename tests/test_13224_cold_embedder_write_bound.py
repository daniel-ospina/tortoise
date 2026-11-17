"""#13224 — a create must not block on a COLD embedder (the 20 s subject stall).

THE DEFECT THIS PINS
--------------------
Production, 2026-11-17: ``POST /v1/subjects`` returned **504 at 18.4 s** and
**200 at 22.9 s** on the retry, while ``POST /v1/points`` was 1.1 s and
``POST /v1/objects`` 1.4 s on the same box. The write is not the cost — an
in-process subject create is 8-10 ms warm.

``EmbeddingModel.get()`` loads ``BAAI/bge-small-en-v1.5`` in a worker thread and
its caller blocks on the join for up to ``_LOAD_TIMEOUT_S`` (90 s); during a warm
window it blocks on the class lock for the WHOLE load, so the caller's own
``load_timeout`` cannot bound it. The hosted lifespan pre-warm is a DAEMON
THREAD and does not gate readiness, so a freshly started container serves writes
while the model is still loading, and the FIRST write pays the load on its own
request thread. Measured on a warm model cache with no download: **13.5 s**; the
recorded cold figure is ~57 s (#1349). A warm encode is 25-43 ms.

The write path reaches that load through ``encode_for_row`` — the one seam every
writer asks "should this row carry a vector?" through (``_upsert_subject``,
``_upsert_object``, ``_upsert_point``). ``Subject`` was simply the door that
arrived first; the same stall is reachable from any write.

WHAT IS ASSERTED
----------------
The invariant, not the mechanism: **while an embedder load is in flight, a create
returns quickly and writes its row**, instead of waiting for the load. A future
refactor that puts the blocking ``get()`` back fails here regardless of how it is
written. The counter-tests keep the fix honest in both directions — the warm path
must still EMBED (the fix may not become "stop embedding"), and the declared
``TORTOISE_EMBEDDER_WARMUP=0`` opt-out must keep the old synchronous contract so
no test or dev lane changes behaviour.

Run (embedded lane; URI-less by design)::

    TORTOISE_TEST_CARVE_OUT=1 .venv/bin/python -m pytest \\
        tests/test_13224_cold_embedder_write_bound.py -q
"""
from __future__ import annotations

import logging
import sys
import threading
import time
from pathlib import Path
from unittest import mock

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise import embeddings  # noqa: E402
from tortoise.embeddings import (  # noqa: E402
    WRITE_EMBED_BUDGET_S,
    EmbeddingModel,
    encode_for_row,
)
from tortoise.exceptions import EmbedderUnavailableError  # noqa: E402
from tortoise.sdk import TortoiseSDK  # noqa: E402

#: How long the simulated cold load takes. Long enough that "blocked on the
#: load" is unmistakable in a wall-clock assertion, short enough to keep the
#: suite quick. The real figure is 13.5 s warm-cache / ~57 s cold; a test may not
#: sleep either, so the DISCRIMINATOR is the ratio below, not this number.
_LOAD_S = 3.0

_DIM = 384


class _FakeEncoder:
    """A loaded-model stand-in: encodes without torch, deterministically."""

    def encode(self, texts, **kwargs):  # noqa: ANN001, ANN003
        return np.array([[0.1] * _DIM for _ in texts], dtype=np.float32)


def _fake_slow_init(load_s: float):
    """``EmbeddingModel.__init__`` that takes ``load_s`` and then succeeds."""

    def _init(self, load_timeout=None):  # noqa: ANN001
        time.sleep(load_s)
        self._model = _FakeEncoder()

    return _init


@pytest.fixture
def cold_embedder(monkeypatch):
    """Run the body with a load IN FLIGHT and the model still cold, then unwind.

    Mirrors the hosted deployment exactly: the pre-warm thread is started and the
    model is not yet available when the write arrives.

    ⛔ ``TORTOISE_EMBEDDER_WARMUP=1`` is asserted HERE rather than inherited:
    ``tests/conftest.py`` pins it **off** for the whole suite (so a background
    load cannot race explicit embedder stubs), and with the opt-out on the
    bounded path is deliberately inert — the pre-#13224 synchronous contract
    stands. That opt-out is itself pinned by its own test below; these tests are
    about the HOSTED configuration, where the flag is on.
    """
    monkeypatch.setenv("TORTOISE_EMBEDDER_WARMUP", "1")
    EmbeddingModel._reset()
    with mock.patch.object(EmbeddingModel, "__init__", _fake_slow_init(_LOAD_S)):
        EmbeddingModel.start_warm_up()  # what hosted_api._lifespan does
        try:
            yield
        finally:
            pass
    # The daemon load thread outlives the patch — join it before resetting, or it
    # would stamp a fake model onto the NEXT test's singleton.
    t = EmbeddingModel._warm_up_thread
    if t is not None and t.is_alive():
        t.join(timeout=_LOAD_S + 5)
    EmbeddingModel._reset()


@pytest.fixture
def sdk(tmp_path):
    sdk = TortoiseSDK(db_path=str(tmp_path / "cold13224.db"))
    try:
        yield sdk
    finally:
        try:
            sdk.close()
        except Exception:  # noqa: BLE001 — teardown must not mask a result
            pass


# ── the invariant: a create does not wait for the load ────────────────────


def test_create_does_not_block_on_a_cold_embedder(cold_embedder, sdk):
    """⚠ THE 20× REGRESSION CATCHER.

    Pre-#13224 the first write in this window waited for the WHOLE model load
    (here ``_LOAD_S``; in production 18.4 s → 504).

    The discriminator is stated against the LOAD, not a stopwatch constant: **two
    writes issued inside a cold window must together cost less than the load they
    overlap.** Pre-fix the total is AT LEAST the load (the first write waits it
    out in full) and then adds the writes; post-fix it is well under, because
    neither write waits for it. That comparison cannot be satisfied by a fast
    host and cannot fail on a slow one — it fails only if the write path waits on
    the load, which is the defect.

    Two writes, not one, because the FIRST call in a process also pays one-time
    start-up the load has nothing to do with (``kind_layers.section_of``'s spec
    compile — measured 811 ms, and separately attributable, see #13224's phase
    breakdown). Charging that to this assertion would make it a load-sensitive
    timing test; summing two writes keeps the comparison honest either way.
    """
    assert not EmbeddingModel.loaded(), (
        "precondition: the embedder must be COLD when the write arrives")

    t0 = time.perf_counter()
    ids = []
    for i in range(2):
        node = sdk.create_subject(f"cold-window-{i}-{time.time_ns()}",
                                 subjectKind="naturalPerson")
        ids.append(node.get("id"))
    elapsed = time.perf_counter() - t0

    assert all(ids), f"every row must still be written, got {ids!r}"
    assert not EmbeddingModel.loaded(), (
        f"the {_LOAD_S:.1f}s load must still be IN FLIGHT — if it finished, this "
        f"run measured the warm path and proves nothing about the cold one"
    )
    assert elapsed < _LOAD_S, (
        f"two creates inside a {_LOAD_S:.1f}s cold window took {elapsed:.2f}s — "
        f"the write path is waiting on the model load again (#13224: production "
        f"saw 504 at 18.4 s and 200 at 22.9 s on the retry)"
    )


def test_the_cold_window_row_is_written_without_a_vector(cold_embedder, sdk):
    """What the bound TRADES, stated so it cannot be lost in a refactor.

    The row is stored and every reader sees it; it carries no vector, so the
    dense leg misses it until ``graph-scripts/backfill_embeddings.py`` re-encodes
    it. That is the degrade ``encode_for_row`` already documents for ``None``
    (and #5008's gate produces by policy) — not a new one.
    """
    node = sdk.create_subject(f"cold-novector-{time.time_ns()}",
                             subjectKind="naturalPerson")
    stored = sdk._get_proj().g.query(
        "MATCH (s:Subject {id:$id}) RETURN s.embedding IS NOT NULL",
        params={"id": node["id"]})
    assert stored.result_set, "the Subject row must EXIST — the write is not optional"
    assert stored.result_set[0][0] is False, (
        "a row written inside the cold window must carry no vector (the traded "
        "consistency), so a reader is told the dense leg has it not"
    )


def test_the_warm_path_still_embeds(sdk, monkeypatch):
    """The fix must not become "stop embedding" — warm rows carry their vector."""
    monkeypatch.setenv("TORTOISE_EMBEDDER_WARMUP", "1")
    EmbeddingModel._reset()
    try:
        with mock.patch.object(EmbeddingModel, "__init__", _fake_slow_init(0.0)):
            EmbeddingModel.warm_up()          # the load completes immediately
        assert EmbeddingModel.loaded(), "precondition: the embedder is warm"

        node = sdk.create_subject(f"warm-vector-{time.time_ns()}",
                                 subjectKind="naturalPerson")
        stored = sdk._get_proj().g.query(
            "MATCH (s:Subject {id:$id}) RETURN s.embedding IS NOT NULL",
            params={"id": node["id"]})
        assert stored.result_set[0][0] is True, (
            "a WARM embedder must still produce the row's vector — the bound is "
            "a ceiling on the WAIT, never a ceiling on the vector"
        )
    finally:
        EmbeddingModel._reset()


def test_the_write_budget_is_bounded_and_documented():
    """The budget is a constant, small, and inside the path's public target.

    #13224's target for this path is p95 < 1 s; a budget at or above that would
    just move the stall back behind a different number.
    """
    assert isinstance(WRITE_EMBED_BUDGET_S, float)
    assert 0.0 < WRITE_EMBED_BUDGET_S < 1.0, WRITE_EMBED_BUDGET_S


# ── the two contracts the bound must not break ────────────────────────────


def test_the_opt_out_keeps_the_pre_13224_synchronous_contract(monkeypatch):
    """``TORTOISE_EMBEDDER_WARMUP=0`` (declared test/dev opt-out) ⇒ unchanged.

    With no background lane to hand the load to, the write path must keep
    loading synchronously — otherwise a lane that opts out of the background
    load would silently stop producing vectors.
    """
    monkeypatch.setenv("TORTOISE_EMBEDDER_WARMUP", "0")
    EmbeddingModel._reset()
    try:
        t0 = time.perf_counter()
        assert EmbeddingModel.await_write_ready(0.0) is True
        assert time.perf_counter() - t0 < 0.5
    finally:
        EmbeddingModel._reset()


def test_a_failed_load_does_not_recharge_the_budget_on_every_write(cold_embedder):
    """A broken embedder must not add the budget to EVERY write (#399's cache)."""
    EmbeddingModel._reset()
    try:
        EmbeddingModel._last_failed_at = time.monotonic()
        t0 = time.perf_counter()
        assert EmbeddingModel.await_write_ready(5.0) is False
        elapsed = time.perf_counter() - t0
        assert elapsed < 0.5, (
            f"a write waited {elapsed:.2f}s on an embedder whose load already "
            f"FAILED — the negative cache must answer without waiting"
        )
    finally:
        EmbeddingModel._reset()


def test_a_failed_load_declines_AFTER_the_cooldown_without_waiting(cold_embedder):
    """F5/F2: the half of "no budget re-charge" the cooldown test cannot reach.

    ``_FAIL_COOLDOWN_S`` only fast-declines for 60 s after a failure. One minute
    later the stamp has aged out and NOTHING on the write path re-stamps it
    (``get()`` is the only writer, and the bounded path no longer reaches it), so
    the pre-F2 loop spun the whole budget on a load that could never arrive: the
    ``start_warm_up()`` one-shot had already been consumed, and its return value
    was discarded. That is 0.25 s of `time.sleep` per row, forever, on the plain
    ``uv sync`` keyword-only install (``not_installed``) or any OOM/offline load —
    pre-fix those rows declined in no time at all (``get()``'s negative cache).

    ⛔ State modelled EXACTLY, because the defect is a state and not a timestamp:
    the load FAILED, its stamp is past the cooldown, and the one-shot warm-up is
    spent with no thread alive. A version that leaves the one-shot armed would
    start a real (here simulated) 3 s load and measure that instead.
    """
    EmbeddingModel._reset()
    try:
        EmbeddingModel._last_failed_at = time.monotonic() - (
            EmbeddingModel._FAIL_COOLDOWN_S + 1.0)
        EmbeddingModel._warm_up_started = True   # one-shot already consumed
        EmbeddingModel._warm_up_thread = None    # ...and no load in flight

        t0 = time.perf_counter()
        assert EmbeddingModel.await_write_ready(5.0) is False
        elapsed = time.perf_counter() - t0
        assert elapsed < 0.5, (
            f"a write waited {elapsed:.2f}s on a load that CANNOT arrive once "
            f"the failed-load cooldown has elapsed — with no load in flight the "
            f"wait is pure sleep on every row of a keyword-only process (#13224)"
        )
    finally:
        EmbeddingModel._reset()


def test_a_write_declines_at_once_when_no_load_is_in_flight(cold_embedder):
    """The same rule at the seam a writer meets: no load ⇒ no wait."""
    EmbeddingModel._reset()
    try:
        EmbeddingModel._warm_up_started = True
        EmbeddingModel._warm_up_thread = None
        t0 = time.perf_counter()
        assert encode_for_row(f"no-load {time.time_ns()}", _DIM) is None
        elapsed = time.perf_counter() - t0
        assert elapsed < WRITE_EMBED_BUDGET_S, (
            f"encode_for_row spent {elapsed:.2f}s of its {WRITE_EMBED_BUDGET_S}s "
            f"budget waiting for a load that is not running"
        )
    finally:
        EmbeddingModel._reset()


def test_a_required_process_still_refuses_by_name_in_the_cold_window(cold_embedder,
                                                                   monkeypatch):
    """``TORTOISE_EMBEDDING_MODEL_REQUIRED`` keeps its refusal (#4861/#5275).

    An armed process must never be handed a keyword-only row it cannot tell
    apart from a hybrid one. The bounded path returns ``None`` BEFORE ``get()``
    is reached, so the refusal has to be taken here or the flag silently loses
    its meaning for every write inside a cold window.
    """
    monkeypatch.setenv("TORTOISE_EMBEDDING_MODEL_REQUIRED", "1")
    with pytest.raises(EmbedderUnavailableError):
        encode_for_row(f"required {time.time_ns()}", _DIM)


def test_await_write_ready_declines_a_cold_wait_inside_the_budget(cold_embedder):
    """Cold ⇒ False within the budget (not the 90 s load timeout)."""
    t0 = time.perf_counter()
    assert EmbeddingModel.await_write_ready(0.1) is False
    elapsed = time.perf_counter() - t0
    assert elapsed < 1.0, f"the bounded wait overran its 0.1s budget: {elapsed:.2f}s"


# ── F3: a REBUILD is not a REQUEST ────────────────────────────────────────


def test_a_replay_fold_keeps_the_pre_13224_synchronous_encode(cold_embedder):
    """The journal fold still produces the vectors the pre-fix fold produced.

    ``tortoise rebuild`` re-encodes each row FROM THE EVENT TEXT — it never
    restores a journaled vector for Subjects/Objects/Documents/Events — so the
    replay is the one lane where a cold embedder is GUARANTEED, and it is the
    recovery tool's own first minute. Charging the request's 0.25 s budget to it
    would silently return a less-retrievable graph than the one it replaced,
    breaking ``derived = replay(journal)``.

    Measured in BOTH directions inside one window: the request seam declines, the
    replay seam waits the (simulated 3 s) load out and encodes.
    """
    from tortoise.projection import _replay_write_scope  # noqa: PLC0415

    live = encode_for_row(f"live {time.time_ns()}", _DIM)
    assert live is None, (
        "precondition: the REQUEST seam must still decline inside the cold "
        "window — the exemption below must not become a global one")

    t0 = time.perf_counter()
    with _replay_write_scope():
        vec = encode_for_row(f"replay {time.time_ns()}", _DIM)
    elapsed = time.perf_counter() - t0

    assert vec is not None, (
        "a row folded by the replay engines must still carry its vector: the "
        "fold RE-ENCODES from the event text, so the request-shaped wait budget "
        "here drops vectors the pre-#13224 fold produced")
    assert len(vec) == _DIM
    assert elapsed > WRITE_EMBED_BUDGET_S, (
        f"the replay seam returned in {elapsed:.2f}s — it applied the request "
        f"budget instead of waiting for the load")
    assert embeddings.in_replay_write_scope() is False, (
        "the replay declaration must end with its window, or the next write on "
        "this thread inherits an exemption it was never given")


# ── F4: the "visible" half of the decline — logged once per process ─────


def _cold_write_lines(caplog) -> list[logging.LogRecord]:
    return [r for r in caplog.records if "still COLD" in r.getMessage()]


def test_the_cold_decline_is_logged_once_per_process(cold_embedder, caplog):
    """The once-per-process line is PINNED, not just written (#13224 F4).

    A bulk write must not storm the log with one line per row, and the line must
    exist at all: deleting ``_note_cold_write`` or its latch turns this red.
    """
    caplog.set_level(logging.WARNING, logger="tortoise.embeddings")
    for i in range(3):
        assert encode_for_row(f"latched {i} {time.time_ns()}", _DIM) is None
    lines = _cold_write_lines(caplog)
    assert len(lines) == 1, (
        f"the cold-write report must appear ONCE per process, got {len(lines)} "
        f"line(s): {[r.getMessage()[:60] for r in lines]}")
    assert str(WRITE_EMBED_BUDGET_S) in lines[0].getMessage() or "0.25" in \
        lines[0].getMessage(), (
            "the line must NAME the budget it expired (#13224)")


def test_reset_re_arms_the_cold_write_report(cold_embedder, caplog):
    """``_reset()`` re-arms the latch, so a LATER window is reportable again.

    ``_rearm_cold_write_report`` has exactly one caller (`_reset`) and existed
    with zero test callers — an unasserted hook is dead weight (#13224 F4).
    """
    caplog.set_level(logging.WARNING, logger="tortoise.embeddings")
    assert encode_for_row(f"before-reset {time.time_ns()}", _DIM) is None
    assert len(_cold_write_lines(caplog)) == 1

    EmbeddingModel._reset()          # a fresh cold window in a fresh process

    assert encode_for_row(f"after-reset {time.time_ns()}", _DIM) is None
    assert len(_cold_write_lines(caplog)) == 2, (
        "the latch stayed set across _reset() — the second cold window is "
        "silent, so a later reportable window cannot be observed")


# ── the seam itself: every writer gets the bound, not just Subject ────────


def test_encode_for_row_is_the_seam_that_carries_the_bound(cold_embedder):
    """Reaching the encoder through a different door is bounded too.

    ``_upsert_object`` / ``_upsert_point`` embed through this same function, so a
    ``Subject``-only fix would leave the stall reachable from the other doors
    (which is how it looked in production: the fast doors were only fast because
    the model was already warm).
    """
    t0 = time.perf_counter()
    assert encode_for_row(f"cold seam {time.time_ns()}", _DIM) is None
    elapsed = time.perf_counter() - t0
    assert elapsed < _LOAD_S, (
        f"encode_for_row blocked {elapsed:.2f}s on a cold embedder"
    )


def test_the_cold_decline_is_counted_not_silent(cold_embedder, monkeypatch):
    """The degrade is VISIBLE: it lands on embed metering's "no model work" term."""
    counted: list[int] = []
    monkeypatch.setattr(embeddings, "_note_embed_skip",
                        lambda n=1: counted.append(n))
    assert encode_for_row(f"counted {time.time_ns()}", _DIM) is None
    assert counted, (
        "a row written without a vector because the embedder was cold must be "
        "counted (#4488), or it is indistinguishable from a designed absence"
    )


# ── end to end, from outside the handler ──────────────────────────────────

from tests.test_hosted_api import client as client  # noqa: E402


def test_post_subjects_stays_under_the_wait_bound_while_the_embedder_is_cold(
        cold_embedder, client):
    """The customer-visible door, measured from outside the handler.

    Production returned **504** here because the request outlived the transport
    wait bound (``mcp_auth._TRANSPORT_WAIT_BOUND_S`` = 10 s) waiting for a model
    load it did not trigger.
    """
    EmbeddingModel._reset()
    with mock.patch.object(EmbeddingModel, "__init__", _fake_slow_init(_LOAD_S)):
        EmbeddingModel.start_warm_up()
        t0 = time.perf_counter()
        resp = client.post("/v1/subjects",
                           json={"name": f"cold-api-{time.time_ns()}",
                                 "subjectKind": "naturalPerson"})
        elapsed = time.perf_counter() - t0

    assert resp.status_code == 200, (
        f"POST /v1/subjects answered {resp.status_code} while the embedder was "
        f"cold ({elapsed:.1f}s) — production saw 504 at 18.4s (#13224)"
    )
    # The load is 3 s here and 13.5-57 s in production; the transport wait bound
    # that produced the 504 is 10 s. A door that waits for the load cannot answer
    # inside it, so "below the load" is the discriminating comparison.
    assert elapsed < _LOAD_S, (
        f"POST /v1/subjects took {elapsed:.2f}s against a {_LOAD_S:.1f}s cold load"
    )
