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
from tortoise.projection import (  # noqa: E402
    _PREWIPE_SYNTHETIC_REASON,
    _graph_metadata_write_scope,
    _prewipe_restore_write_scope,
    _prewipe_synthetic_write_scope,
    _replay_write_scope,
)
from tortoise.projection.edges import (  # noqa: E402
    _structural_edge_write_scope,
)
from tortoise.sdk import TortoiseSDK  # noqa: E402

#: How long the simulated cold load takes. Long enough that "blocked on the
#: load" is unmistakable in a wall-clock assertion, long enough to keep 3-4x
#: margin over an unloaded box's create cost (measured 1.31 s for two creates when
#: this was 3.0), short enough to keep the suite quick. The real figure is 13.5 s
#: warm-cache / ~57 s cold, so a test may not use it; the DISCRIMINATOR is the
#: comparison against this number, not the number itself.
_LOAD_S = 6.0

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
    # test's singleton. ⛔ Join the object captured at start, never a re-read of
    # `EmbeddingModel._warm_up_thread`: a body that calls `_reset()` (the
    # end-to-end row did) clears that class attribute, so re-reading it here
    # silently skipped the join and leaked the load into the following test.
    if started is not None and started.is_alive():
        started.join(timeout=_LOAD_S + 10)
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


def test_the_opt_out_wins_over_a_designed_absence(monkeypatch):
    """#13229 review F1: the opt-out arm must be decided BEFORE the other two.

    ⛔ THE ORDERING IS THE DEFECT, and it was invisible to every row this file had:
    `test_the_opt_out_keeps_the_pre_13224_synchronous_contract` calls `_reset()`
    first, so it only ever exercises the CLEAN state (no failure kind, no
    cooldown) — the one state in which the opt-out arm was reachable at all.
    With the flag OFF and either `_last_failure_kind == "not_installed"` (the
    DOCUMENTED default install: a plain `uv sync` is keyword-only) or a load that
    failed inside `_FAIL_COOLDOWN_S`, `await_write_ready` used to return False,
    so `encode_for_row(bounded=True)` returned None WITHOUT calling
    `encode_for_store` → `compute_embedding`.

    That is not "no vector either way": `compute_embedding` is a REPLACEABLE
    seam (an installed `EncodeCache`, a test stub), so a replacement that CAN
    produce a vector silently stopped being consulted for the four derived-entity
    folds. The opt-out's documented contract is that it keeps the PRE-#13224
    synchronous contract EXACTLY, and pre-#13224 the seam was reached
    unconditionally — so this row asserts the SEAM WAS REACHED, not merely that a
    call returned.
    """
    monkeypatch.setenv("TORTOISE_EMBEDDER_WARMUP", "0")
    EmbeddingModel._reset()
    try:
        # (a) the designed absence — the documented default install
        EmbeddingModel._last_failure_kind = "not_installed"
        EmbeddingModel._last_failed_at = (
            time.monotonic() - EmbeddingModel._FAIL_COOLDOWN_S - 1)
        assert EmbeddingModel.await_write_ready(0.0) is True, (
            "with the background lane opted OUT, a designed absence must not "
            "answer 'not ready' — there is no lane to wait on, so the caller "
            "loads synchronously as it did before #13224"
        )
        assert EmbeddingModel._warm_up_started is False, (
            "the opt-out must not kick a background load"
        )
        seen: list[str] = []
        with mock.patch("tortoise.embeddings.compute_embedding",
                        lambda text, **kw: seen.append(text) or [0.7] * _DIM):
            vec = encode_for_row(f"optout absent {time.time_ns()}", _DIM,
                                 bounded=True)
        assert seen, (
            "the `compute_embedding` SEAM was never consulted — the opt-out path "
            "declined the row instead of loading synchronously, so an installed "
            "EncodeCache or test stub silently stops producing vectors"
        )
        assert vec is not None, (
            "a replacement encoder CAN produce a vector here; the opt-out must "
            "reach it"
        )

        # (b) a load that FAILED inside the negative-cache cooldown
        EmbeddingModel._reset()
        EmbeddingModel._last_failed_at = time.monotonic()
        assert EmbeddingModel.await_write_ready(0.0) is True, (
            "with the background lane opted OUT, the failure cooldown must not "
            "answer 'not ready' — the cooldown exists to stop re-kicking a "
            "background load, and there is none"
        )
        seen.clear()
        with mock.patch("tortoise.embeddings.compute_embedding",
                        lambda text, **kw: seen.append(text) or [0.7] * _DIM):
            vec = encode_for_row(f"optout cooldown {time.time_ns()}", _DIM,
                                 bounded=True)
        assert seen and vec is not None, (
            "the cooldown arm decided before the opt-out arm, so the seam was "
            "skipped under the opt-out"
        )
    finally:
        EmbeddingModel._reset()


def test_a_failed_load_answers_from_the_negative_cache_inside_its_cooldown(
        cold_embedder):
    """`#399`'s cooldown answers without waiting — the WITHIN-window half only.

    ⛔ SCOPE, stated so this row is not read as coverage it does not have: setting
    `_last_failed_at` to *now* puts the call in the one window where the cooldown
    branch is guaranteed to answer first, so this pins the "cache answers
    immediately" half and NOTHING about the window after `_FAIL_COOLDOWN_S`
    expires. The post-cooldown half — where the once-per-process load attempt is
    already spent and a naive implementation sleeps the whole budget forever — is
    pinned by `test_a_spent_load_attempt_does_not_charge_the_budget` below, and
    that is the row that reds if the re-arm arm is removed (measured on the
    pre-fix source: this row stayed GREEN with `return False` substituted for
    everything after the opt-out check, which is exactly why the defect it was
    once named for reached review un-caught).
    """
    EmbeddingModel._reset()
    try:
        EmbeddingModel._last_failed_at = time.monotonic()
        t0 = time.perf_counter()
        assert EmbeddingModel.await_write_ready(5.0) is False
        elapsed = time.perf_counter() - t0
        assert elapsed < 0.5, (
            f"a write waited {elapsed:.2f}s on an embedder whose load already "
            f"FAILED inside the cooldown — the negative cache must answer "
            f"without waiting"
        )
    finally:
        EmbeddingModel._reset()


def test_await_write_ready_reports_the_cold_answer_within_the_budget(cold_embedder):
    """Cold ⇒ False within the budget (not the 90 s load timeout)."""
    t0 = time.perf_counter()
    assert EmbeddingModel.await_write_ready(0.1) is False
    elapsed = time.perf_counter() - t0
    assert elapsed < 1.0, f"the bounded wait overran its 0.1s budget: {elapsed:.2f}s"


# ── the seam itself: every writer gets the bound, not just Subject ────────


def test_encode_for_row_is_the_seam_that_carries_the_bound(cold_embedder):
    """Called directly with ``bounded=True`` the seam declines instead of waiting.

    ⛔ SCOPE, stated because the previous version of this docstring claimed more
    than it tests: this row calls the SEAM, so it proves the seam honours the
    opt-in — it proves nothing about which folds PASS it. That is
    ``test_every_derived_entity_fold_opt_into_the_bound`` below, which drives
    each door through the SDK (dropping `bounded=True` from the Object,
    Document and Event folds left the rest of this file green — the review's M2).

    ⛔ `bounded=True` is the OPT-IN the derived-entity folds pass; the default is
    deliberately unbounded (see the record-truth test below).
    """
    t0 = time.perf_counter()
    assert encode_for_row(f"cold seam {time.time_ns()}", _DIM, bounded=True) is None
    elapsed = time.perf_counter() - t0
    assert elapsed < _LOAD_S, (
        f"encode_for_row blocked {elapsed:.2f}s on a cold embedder"
    )


#: The three OTHER DERIVED-entity doors — the ones whose `bounded=True` opt-in
#: was unpinned, so a regression that dropped it shipped green (the review's M2:
#: removing `bounded=True` from `_upsert_object`, `_upsert_document` and the Event
#: fold, keeping Subject, left the file 16/16 GREEN). Each entry drives the public
#: SDK door and names the label/key that door's row lands under, so the assertion
#: is about the ROW the fold wrote and not about a folded event dictionary.
_DOOR_CASES: tuple[tuple[str, object, str, str], ...] = (
    ("object",
     lambda sdk: sdk.create_object(f"cold-door-object-{time.time_ns()}",
                                   objectKind="other"),
     "Object", "id"),
    ("document",
     lambda sdk: sdk.create_document(f"cold-door-doc-{time.time_ns()}",
                                     documentKind="report"),
     "Source", "url"),
    ("event",
     lambda sdk: sdk.create_event(f"cold-door-event-{time.time_ns()}",
                                  eventKind="occurrence"),
     "Event", "id"),
)


@pytest.mark.parametrize("door,create,label,key", _DOOR_CASES,
                         ids=[c[0] for c in _DOOR_CASES])
def test_every_derived_entity_fold_opt_into_the_bound(
        door, create, label, key, cold_embedder, sdk):
    """⚠ EVERY derived-entity door must OPT IN, not just ``Subject``.

    The invariant is the first row's, per door: **the write returns while the
    load is still in flight and the row is still written**. The mutant this row
    exists for is dropping `bounded=True` from that fold — the fold then falls
    back to the synchronous `compute_embedding` → `get()`, which blocks on the
    class lock for the WHOLE load, so `elapsed` reaches `_LOAD_S` and the load has
    landed by the time the create returns. Both halves are asserted for that
    reason: on the mutant the wall-clock bound AND the still-cold precondition
    fail together.

    ``Subject`` has its own end-to-end rows above; this row covers the other
    three, and ``_upsert_event``'s default sub-payload is why its kind is not
    ``meeting`` (the fold skips that one by construction).
    """
    assert not EmbeddingModel.loaded(), (
        "precondition: the embedder must be COLD when the write arrives")

    t0 = time.perf_counter()
    node = create(sdk)
    elapsed = time.perf_counter() - t0

    assert node.get("id"), f"the {door} row must still be written, got {node!r}"
    assert not EmbeddingModel.loaded(), (
        f"the {_LOAD_S:.1f}s load must still be IN FLIGHT after the {door} create — "
        f"if it finished, that write waited it out (or the run measured a warm path)"
    )
    assert elapsed < _LOAD_S, (
        f"create_{door} took {elapsed:.2f}s against a {_LOAD_S:.1f}s cold load — the "
        f"fold is not passing `bounded=True` to encode_for_row, so the cold stall "
        f"#13224 removed from Subject is still reachable from this door"
    )

    stored = sdk._get_proj().g.query(
        f"MATCH (n:{label} {{{key}:$id}}) RETURN n.embedding IS NOT NULL",
        params={"id": node["id"]})
    assert stored.result_set, f"the {door} row must EXIST under {label}.{key}"
    assert stored.result_set[0][0] is False, (
        f"a {door} row written inside the cold window must carry no vector (the "
        f"traded consistency), or this run measured the warm path"
    )


def test_the_default_caller_never_consults_the_bound(monkeypatch):
    """⚠ RECORD-TRUTH vectors must never be dropped.

    `bounded` defaults to FALSE so every pre-existing caller — the Point journal
    stamp (`PRESENCE IS OWNERSHIP`, #5004), the legacy replay recompute, the turn
    writer — keeps the synchronous contract. A cold `None` on the Point path is
    recorded as an explicit CLEAR that a rebuild reproduces, and no graph-side
    backfill can reach it, so this default is load-bearing data integrity, not
    style.
    """
    consulted: list[float] = []
    monkeypatch.setattr(
        EmbeddingModel, "await_write_ready_reason",
        classmethod(
            lambda cls, budget: consulted.append(budget) or "budget_expired"))
    with mock.patch("tortoise.embeddings.compute_embedding",
                    lambda text, **kw: [0.5] * _DIM):
        vec = encode_for_row(f"record truth {time.time_ns()}", _DIM)
    assert vec is not None, (
        "the DEFAULT caller must still embed — a record-truth vector is never "
        "dropped for a slow embedder (#5004 presence-is-ownership)"
    )
    assert consulted == [], (
        "the default path must not even ASK the bound — asking is how a cold "
        "window silently becomes a recorded vector deletion"
    )


def test_a_replay_scope_keeps_the_synchronous_contract(cold_embedder):
    """A journal REPLAY must always embed, so rebuilds stay deterministic.

    Entity vectors are DERIVED IN THE FOLD and ride no journal record, so
    `derived = replay(journal)` holds only while live and replay make the same
    encode decision. A timing-dependent replay would give two rebuilds of one
    journal different graphs.
    """
    with _replay_write_scope():
        t0 = time.perf_counter()
        vec = encode_for_row(f"replay seam {time.time_ns()}", _DIM, bounded=True)
        elapsed = time.perf_counter() - t0
    assert vec is not None, (
        "a replay-scoped fold must embed even with bounded=True, or two "
        "rebuilds of the same journal produce different graphs"
    )
    assert elapsed >= _LOAD_S * 0.5, (
        f"the replay did not wait for the load ({elapsed:.2f}s) — the bound leaked "
        f"into the replay path"
    )


#: The INNER lanes a replay engine opens inside its own window — the set
#: `projection.replay_window_open()`'s docstring names (`#9391`/`#9553`). Each one
#: SHADOWS the reason in `_CACHE_WRITE_AUTHORIZED`, which is why the window has to
#: be read from the depth and not from the innermost lane.
_INNER_LANES: tuple[tuple[str, object], ...] = (
    ("graph_metadata",
     lambda: _graph_metadata_write_scope("schema-ddl")),
    ("prewipe_restore",
     lambda: _prewipe_restore_write_scope(":Batch markers (#990)")),
    ("prewipe_synthetic",
     lambda: _prewipe_synthetic_write_scope(_PREWIPE_SYNTHETIC_REASON)),
    ("structural_edge",
     lambda: _structural_edge_write_scope()),
)


@pytest.mark.parametrize("inner", [c[1] for c in _INNER_LANES],
                         ids=[c[0] for c in _INNER_LANES])
def test_a_nested_lane_inside_a_replay_is_still_replay(inner):
    """⚠ The replay test must read the WINDOW (a depth), never the inner LANE.

    `rebuild_all` opens `_replay_write_scope()` around its whole body and then
    opens inner lanes per restore leg, per synthetic pass, and for schema DDL —
    each of which SHADOWS the reason in `_CACHE_WRITE_AUTHORIZED`. MEASURED on
    the shape this row was written against (a direct `== _REPLAY_WRITE_LANE`
    comparison): all four inner lanes read `False` while the replay window was
    still open, so the bound re-engaged and a cold rebuild wrote derived rows
    without vectors — the live/replay divergence `encode_for_row` forbids.

    ``projection.replay_window_open()`` is the primitive that already documents
    this trap: `_RAW_QUERY_CLOSED_DEPTH` is inherited by nested scopes and cannot
    be cleared by one.
    """
    # Not in a replay: the bound is allowed (this is the LIVE door shape).
    assert embeddings._in_replay_scope() is False
    with _replay_write_scope():
        assert embeddings._in_replay_scope() is True
        with inner():
            assert embeddings._in_replay_scope() is True, (
                "a lane nested inside a replay window shadowed the reason, so "
                "_in_replay_scope() read False while the replay was still "
                "running — read `replay_window_open()` (the depth), not the "
                "innermost lane"
            )


def test_a_nested_lane_inside_a_replay_still_embeds(cold_embedder):
    """The behavioural half: the dispatch above really suppresses the bound.

    `test_a_nested_lane_inside_a_replay_is_still_replay` proves the predicate;
    this row proves what the predicate is FOR — a nested fold inside a cold
    rebuild waits for the load and produces its vector, so two rebuilds of one
    journal cannot disagree on timing.
    """
    with _replay_write_scope(), _graph_metadata_write_scope("schema-ddl"):
        t0 = time.perf_counter()
        vec = encode_for_row(
            f"nested replay seam {time.time_ns()}", _DIM, bounded=True)
        elapsed = time.perf_counter() - t0
    assert vec is not None, (
        "a fold nested inside a replay window dropped its vector — a cold rebuild "
        "is not reproducible then"
    )
    assert elapsed >= _LOAD_S * 0.5, (
        f"the nested fold did not wait for the load ({elapsed:.2f}s) — the bound "
        f"leaked into the replay window"
    )


def test_a_projection_fault_reads_as_replay(monkeypatch):
    """The fail-safe direction is REPLAY, so a fault can never DROP a vector.

    `_in_replay_scope()` documents `except Exception: return True` as
    load-bearing: an unreadable window is treated as replay, so the failure
    direction is *embed anyway*, never *drop a vector*. Until this row existed
    nothing exercised it — flipping the handler to `return False` reddened no
    test (measured by a fresh-context reviewer), which left the documented
    fail-safe free to be "tidied" into the direction it exists to forbid.
    """
    import tortoise.projection as projection

    def _boom() -> bool:
        raise RuntimeError("projection fault")

    monkeypatch.setattr(projection, "replay_window_open", _boom)
    assert embeddings._in_replay_scope() is True, (
        "a raising replay-window read was treated as LIVE — the bound would then "
        "engage inside an unreadable replay and drop a rebuild's vector"
    )


# ── the OPERATOR SIGNAL: one line per cause, naming the actual cause ───────

#: The phrase every decline line carries (so a filter is not coupled to severity).
_COLD_WRITE_PROBE = "DERIVED entity row"


def _decline_lines(caplog):  # noqa: ANN001, ANN202
    return [r for r in caplog.records
            if r.name == "tortoise.embeddings"
            and _COLD_WRITE_PROBE in r.getMessage()]


def test_the_cold_decline_is_logged_once_per_process_and_rearmed(cold_embedder, caplog):
    """The LOG half of the decline: ONE line, naming the budget and the repair.

    ⛔ THREE MUTANTS THIS ROW IS THE ONLY KILLER FOR (each was verified to leave the
    rest of this file green — the #13224 review's F4): delete the
    `_note_cold_write(...)` call (no line at all), delete the
    `_COLD_WRITE_REPORTED` latch (one line PER ROW, i.e. a per-row log storm on a
    bulk write), or delete `_rearm_cold_write_report()` from
    `EmbeddingModel._reset()` (the next cold window is silent forever).

    The line is not decoration: the metering term (#4488) counts the declines but
    cannot say WHY, and "a cold window" is only actionable if it names the repair an
    operator has to run — which is why the message text is asserted, not just the
    count.
    """

    def _cold_lines(records):  # noqa: ANN001, ANN202
        return [r for r in records if "still COLD" in r.getMessage()]

    with caplog.at_level(logging.WARNING, logger="tortoise.embeddings"):
        assert encode_for_row(
            f"cold log 1 {time.time_ns()}", _DIM, bounded=True) is None
        assert encode_for_row(
            f"cold log 2 {time.time_ns()}", _DIM, bounded=True) is None
        first = _cold_lines(caplog.records)
    assert len(first) == 1, (
        f"two declining rows produced {len(first)} cold-write line(s); the latch must "
        f"make it EXACTLY one per cause per process, and zero means the report was "
        f"removed"
    )
    msg = first[0].getMessage()
    assert f"{WRITE_EMBED_BUDGET_S:.2f}s" in msg, (
        f"the line must name the budget it waited ({WRITE_EMBED_BUDGET_S}s), or a "
        f"reader cannot tell a cold decline from a designed absence: {msg!r}"
    )
    assert "backfill_embeddings.py" in msg, (
        f"the line must name the REPAIR an operator runs for the derived vectors "
        f"this bound drops: {msg!r}"
    )

    # `_reset()` must RE-ARM the latch, or every later cold window is silent. The
    # load latch is spent by hand here rather than by spawning a second real load:
    # the re-arm of the LOAD attempt has its own row
    # (`test_a_spent_load_attempt_does_not_charge_the_budget`), and a 6 s join in this
    # test would buy nothing this assertion does not already carry.
    #
    # ⛔ THE STUB MUST BE AN ALIVE THREAD, and the cause must be the SAME one as
    # above (`budget_expired`). The latch is per CAUSE (#13229 F2), so a second
    # phase that answered from a DIFFERENT arm would log because its own cause is
    # new — and the mutant this row exists to kill (delete
    # `_rearm_cold_write_report()` from `_reset()`) would no longer redden it.
    # `start_warm_up()` returns a live thread only when `_warm_up_started` is set
    # AND `_warm_up_thread` is alive; with the thread None the arm below is the
    # spent-attempt one instead.
    EmbeddingModel._reset()
    cold_stub = threading.Thread(target=time.sleep, args=(_LOAD_S,), daemon=True)
    cold_stub.start()
    EmbeddingModel._warm_up_started = True
    EmbeddingModel._warm_up_thread = cold_stub
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="tortoise.embeddings"):
        assert encode_for_row(
            f"cold log 3 {time.time_ns()}", _DIM, bounded=True) is None
        later = _cold_lines(caplog.records)
    assert len(later) == 1, (
        "a cold window AFTER `EmbeddingModel._reset()` logged nothing — the latch "
        "was not re-armed (delete `_rearm_cold_write_report()` from `_reset()` and "
        "this is the only row that reds)"
    )


def test_the_cold_decline_is_counted_not_silent(cold_embedder, monkeypatch):
    """The degrade is VISIBLE: it lands on embed metering's "no model work" term."""
    counted: list[int] = []
    monkeypatch.setattr(embeddings, "_note_embed_skip",
                        lambda n=1: counted.append(n))
    assert encode_for_row(f"counted {time.time_ns()}", _DIM, bounded=True) is None
    assert counted, (
        "a row written without a vector because the embedder was cold must be "
        "counted (#4488), or it is indistinguishable from a designed absence"
    )


def test_a_designed_absence_is_reported_as_an_absence_not_a_cold_wait(
        monkeypatch, caplog):
    """#13229 review F2: the absence is NOT the cold window, in cause or severity.

    ⛔ FOUR CAUSES SHARE ONE DECLINE SITE, and they need opposite operator
    responses: install the extra / repair a broken load / retry later / wait
    longer. The single message this row used to emit merged all four into
    *"still COLD … after waiting 0.25s … until backfill_embeddings.py re-encodes
    it"* — which, for a DESIGNED absence, asserts a wait that never happened and
    names a repair that has no encoder to run, on the DOCUMENTED default install
    (plain `uv sync` is keyword-only). It also logged WARNING for a state
    `warm_up()` deliberately logs INFO for (`#2952`, same module, same process).

    Nothing pinned the negative direction before: the only row for this state
    (`test_a_designed_absence_answers_immediately`) calls `await_write_ready`
    DIRECTLY and asserts no log at all, so the wrong line was free to ship.
    """
    monkeypatch.setenv("TORTOISE_EMBEDDER_WARMUP", "1")
    EmbeddingModel._reset()
    try:
        EmbeddingModel._last_failure_kind = "not_installed"
        EmbeddingModel._last_failed_at = (
            time.monotonic() - EmbeddingModel._FAIL_COOLDOWN_S - 1)
        with caplog.at_level(logging.DEBUG, logger="tortoise.embeddings"):
            assert encode_for_row(f"absent log {time.time_ns()}", _DIM,
                                  bounded=True) is None
        lines = _decline_lines(caplog)
        assert len(lines) == 1, (
            f"a designed absence produced {len(lines)} decline line(s) — it must "
            f"report the absence ONCE (zero means the cause is silent again)"
        )
        msg = lines[0].getMessage()
        assert lines[0].levelno == logging.INFO, (
            f"a designed absence was logged at {lines[0].levelname} — `warm_up()` "
            f"logs the SAME state at INFO (#2952), and a WARNING sends an "
            f"operator hunting a fault that is a documented install choice: {msg!r}"
        )
        assert "not installed" in msg.lower(), (
            "the line must NAME the cause (the extra is absent), not merely that "
            f"a row lost its vector: {msg!r}"
        )
        assert "after waiting" not in msg, (
            f"nothing is waited for a designed absence — the line claims a wait "
            f"that did not happen: {msg!r}"
        )
        assert "still COLD" not in msg, (
            "a designed absence is not a cold window: there is no model to be "
            f"cold, and the two need opposite repairs: {msg!r}"
        )
    finally:
        EmbeddingModel._reset()


#: Each zero-wait cause, set up so `await_write_ready_reason` answers with it.
_ZERO_WAIT_CAUSES = (
    ("fail_cooldown", lambda: setattr(
        EmbeddingModel, "_last_failed_at", time.monotonic())),
    ("attempt_spent", lambda: (
        setattr(EmbeddingModel, "_warm_up_started", True),
        setattr(EmbeddingModel, "_warm_up_thread", None))),
)


@pytest.mark.parametrize("setup", [c[1] for c in _ZERO_WAIT_CAUSES],
                         ids=[c[0] for c in _ZERO_WAIT_CAUSES])
def test_a_zero_wait_decline_never_claims_a_wait(setup, monkeypatch, caplog):
    """#13229 review F2: only the budget-expiry cause actually waited.

    The shared message ended *"after waiting 0.25s"* for every cause. For a load
    that already FAILED inside the cooldown, and for a spent once-per-process
    attempt (which re-arms one background retry and declines instantly, verified
    by `test_a_spent_load_attempt_does_not_charge_the_budget`), 0 s is waited —
    so the line sent an operator to look for a slow load that was never slow.
    """
    monkeypatch.setenv("TORTOISE_EMBEDDER_WARMUP", "1")
    EmbeddingModel._reset()
    try:
        setup()
        with caplog.at_level(logging.DEBUG, logger="tortoise.embeddings"):
            assert encode_for_row(f"zerowait {time.time_ns()}", _DIM,
                                  bounded=True) is None
        lines = _decline_lines(caplog)
        assert len(lines) == 1, (
            f"this decline cause produced {len(lines)} line(s); each cause must "
            f"report itself once"
        )
        msg = lines[0].getMessage()
        assert "after waiting" not in msg, (
            f"this cause waited 0 s, and the line says it waited: {msg!r}"
        )
        assert "backfill_embeddings.py" in msg, (
            f"the repair an operator runs must still be named: {msg!r}"
        )
    finally:
        EmbeddingModel._reset()


def test_a_spent_load_attempt_does_not_charge_the_budget(monkeypatch):
    """A FAILED load must not add the budget to every write, nor stop retrying.

    `start_warm_up` latches once per process, so without the re-arm arm a failed
    load charges the full budget to EVERY subsequent write forever while never
    re-trying — measured on a failing `__init__`: 0.250 s x 3, load attempts 1.

    The contract is two-sided, so both halves are asserted: the write that finds
    the attempt SPENT re-arms and declines instantly, and a write inside the
    failure cooldown declines instantly. (The write that follows a fresh re-arm
    legitimately waits its budget — a load really is in flight and may land.)
    """
    monkeypatch.setenv("TORTOISE_EMBEDDER_WARMUP", "1")
    EmbeddingModel._reset()
    try:
        EmbeddingModel._last_failed_at = time.monotonic() - 3600  # past cooldown
        EmbeddingModel._last_failure_kind = "load_failed"
        EmbeddingModel._warm_up_started = True
        EmbeddingModel._warm_up_thread = None

        t0 = time.perf_counter()
        assert EmbeddingModel.await_write_ready(5.0) is False
        spent_elapsed = time.perf_counter() - t0
        assert spent_elapsed < 0.1, (
            f"a write waited {spent_elapsed:.2f}s for a load whose "
            f"once-per-process attempt was already spent"
        )
        assert EmbeddingModel._warm_up_started is False, (
            "the spent attempt must be RE-ARMED, or the load is never retried and "
            "a transient failure disables embedding for the whole process"
        )

        # A load just failed again -> the cooldown answers without waiting.
        EmbeddingModel._last_failed_at = time.monotonic()
        t0 = time.perf_counter()
        assert EmbeddingModel.await_write_ready(5.0) is False
        cool_elapsed = time.perf_counter() - t0
        assert cool_elapsed < 0.1, (
            f"a write waited {cool_elapsed:.2f}s inside the failure cooldown"
        )
    finally:
        t = EmbeddingModel._warm_up_thread
        if t is not None and t.is_alive():
            t.join(timeout=60)
        EmbeddingModel._reset()


def test_a_designed_absence_answers_immediately(monkeypatch):
    """The DEFAULT install is keyword-only — it must not pay the budget.

    A plain `uv sync` has no `embeddings` extra, so the model can never load
    (`not_installed`). Waiting or retrying cannot help, so the answer must be
    immediate or the bound taxes every write on the documented default install.

    ⛔ THE COOLDOWN IS DELIBERATELY EXPIRED (measured, twice, by a fresh-context
    reviewer): with `_last_failed_at` set to *now*, the COOLDOWN branch answers
    and this row stays green with the `not_installed` arm deleted — it would pin
    `#399`'s negative cache, not the designed absence, and the arm could rot
    un-noticed. Past the cooldown only the designed-absence arm can answer
    without waiting, and `_warm_up_started` is asserted FALSE so the row also
    proves the absence KICKS NO LOAD — the timestamp alone still passes on a
    load that fails fast and is answered by the loop's cooldown re-check.
    """
    monkeypatch.setenv("TORTOISE_EMBEDDER_WARMUP", "1")
    EmbeddingModel._reset()
    try:
        EmbeddingModel._last_failure_kind = "not_installed"
        EmbeddingModel._last_failed_at = (
            time.monotonic() - EmbeddingModel._FAIL_COOLDOWN_S - 1)
        t0 = time.perf_counter()
        assert EmbeddingModel.await_write_ready(5.0) is False
        elapsed = time.perf_counter() - t0
        assert elapsed < 0.05, (
            f"a designed absence waited {elapsed:.2f}s — the `not_installed` arm "
            f"must answer BEFORE the budget and before any load is kicked"
        )
        assert EmbeddingModel._warm_up_started is False, (
            "a designed absence KICKED a load attempt — waiting or retrying can "
            "never help a keyword-only install, so it must not pay for one"
        )
    finally:
        t = EmbeddingModel._warm_up_thread
        if t is not None and t.is_alive():
            t.join(timeout=60)
        EmbeddingModel._reset()


def test_loaded_is_false_for_a_half_built_instance():
    """`loaded()` must not dereference `_model` on a None instance (#13224)."""
    EmbeddingModel._reset()
    try:
        assert EmbeddingModel.loaded() is False
        inst = mock.Mock()
        inst._model = None      # the transient post-failure shape `get()` clears
        EmbeddingModel._instance = inst
        assert EmbeddingModel.loaded() is False
    finally:
        EmbeddingModel._reset()


# ── end to end, from outside the handler ──────────────────────────────────

from tests.test_hosted_api import client as client  # noqa: E402


def test_post_subjects_stays_under_the_wait_bound_while_the_embedder_is_cold(
        client, cold_embedder):
    """The customer-visible door, measured from outside the handler.

    Production returned **504** here because the request outlived the transport
    wait bound (``mcp_auth._TRANSPORT_WAIT_BOUND_S`` = 10 s) waiting for a model
    load it did not trigger.

    ⛔ ``client`` is requested BEFORE ``cold_embedder``: the hosted client builds
    its app (and its lifespan) first, and the cold window is then established on
    top of it — the reverse order lets the client's own start-up decide the
    embedder state the request meets.

    ⛔ The coldness PRECONDITION is asserted, not assumed. Without it a warm or
    leaked ``_instance`` satisfies both ``status_code == 200`` and
    ``elapsed < _LOAD_S`` while measuring the warm path entirely — the exact
    vacuity ``test_create_does_not_block_on_a_cold_embedder`` guards against on
    the SDK door.
    """
    assert not EmbeddingModel.loaded(), (
        "precondition: the embedder must be COLD when the request arrives")

    t0 = time.perf_counter()
    resp = client.post("/v1/subjects",
                       json={"name": f"cold-api-{time.time_ns()}",
                             "subjectKind": "naturalPerson"})
    elapsed = time.perf_counter() - t0

    assert resp.status_code == 200, (
        f"POST /v1/subjects answered {resp.status_code} while the embedder was "
        f"cold ({elapsed:.1f}s) — production saw 504 at 18.4s (#13224)"
    )
    # The load is 6 s here and 13.5-57 s in production; the transport wait bound
    # that produced the 504 is 10 s. A door that waits for the load cannot answer
    # inside it, so "below the load" is the discriminating comparison.
    assert elapsed < _LOAD_S, (
        f"POST /v1/subjects took {elapsed:.2f}s against a {_LOAD_S:.1f}s cold load"
    )
