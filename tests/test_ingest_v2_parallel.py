"""#1744: the LIVE ``ingest_haystack_v2`` must run the session-parallel
extraction phase.

History: the module briefly defined ``ingest_haystack_v2`` TWICE — an older
copy WITH the ``ThreadPoolExecutor`` extraction phase and a newer,
feature-complete copy (R1 #1786 write-stage retries + marker arming, the
#2408 ``s4_merge`` census, the #2134 ``_max``-preserving recovery keys)
WITHOUT it. Python last-def-wins, so the newer sequential copy was live and
``--session-workers > 1`` was a silent no-op (every session extracted
sequentially; measured 5/5 overnight questions hit the 90-min watchdog
having completed only 10-15 of 44-56 sessions).

The fix ports the parallel extraction into the live copy (three phases: A =
sequential raw-leg writes, B = parallel LLM extraction, C = sequential
payload writes) and deletes the dead older copy. These tests pin:

* exactly ONE ``def ingest_haystack_v2`` in the module (no shadowing);
* ``model_factory`` is invoked once per session — the pre-fix live copy
  ignored it entirely — and each worker gets its OWN model;
* the extraction phase is genuinely CONCURRENT (a barrier only releases
  when all workers are in flight simultaneously);
* a retryable transient raised in a worker propagates out of the
  ``ThreadPoolExecutor`` (#1786 P2-2 / ``--retry-failed`` contract);
* the surviving function is the feature-complete one —
  ``ingest_write_retries`` / ``write_marker_armed`` accepted and honored,
  ``s4_merge`` rolled up into stats.

Fully offline: a fake SDK (no FalkorDBLite server, no network) + a
monkeypatched ``extract_session_v2``. No LLM spend.
"""
from __future__ import annotations

import ast
import sys
import threading
import types
from pathlib import Path

import pytest
import redis.exceptions as redis_exc

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tortoise.extractor_v2 as ev2
from tools.longmem_eval.ingest_v2 import ingest_haystack_v2

MODULE = Path(__file__).resolve().parent.parent / "tools" / "longmem_eval" / "ingest_v2.py"


# ── fakes ─────────────────────────────────────────────────────────────────


class _FakeGraph:
    def __init__(self, *, fail_first_point_write: bool = False):
        self.fail_first_point_write = fail_first_point_write

    def query(self, cypher, params=None, timeout=None):
        return types.SimpleNamespace(result_set=[])


class _FakeProj:
    def __init__(self, graph: _FakeGraph):
        self.g = graph


class _FakeSDK:
    """Minimal SDK surface the ingest path touches — no DB, no redis.

    With an EMPTY extraction payload the only writes are the Phase-A
    session/turn/chunk leg; ``create_point`` is the one fault injector.
    """

    def __init__(self, *, fail_first_point_write: bool = False):
        self._graph = _FakeGraph(fail_first_point_write=fail_first_point_write)
        self._proj = _FakeProj(self._graph)
        self.points: list[tuple[str, str, dict]] = []
        self._point_writes = 0

    def _get_proj(self):
        return self._proj

    def create_point(self, kind, content, *, is_episodic=None, **props):
        self._point_writes += 1
        if self._graph.fail_first_point_write and self._point_writes == 1:
            self._graph.fail_first_point_write = False
            raise redis_exc.TimeoutError("simulated write stall")
        self.points.append((kind, content, props))

    def create_entity(self, *a, **k):
        return None

    def create_event(self, *a, **k):
        return None

    def create_operator(self, *a, **k):
        return None

    def retract_point(self, pid):
        return None


def _question(n_sessions: int = 4, turns: int = 2) -> dict:
    sessions = [
        [{"role": "user", "content": f"session {si} turn {ti}"} for ti in range(turns)]
        for si in range(n_sessions)
    ]
    return {
        "question_id": "par_q_001",
        "haystack_session_ids": [f"sess-{i}" for i in range(n_sessions)],
        "haystack_dates": ["2026-08-01"] * n_sessions,
        "haystack_sessions": sessions,
        "answer": "the answer",
    }


def _silence_retry_sleep(monkeypatch) -> None:
    """No-op ``tortoise.retry``'s backoff sleep ONLY — never the global
    ``time.sleep`` (the embedded-reaper / redislite start waits share that
    module object; a global no-op turns their socket polls into a fast spin).
    """
    import time as _time

    import tortoise.retry as retry_mod
    shim = types.ModuleType("time")
    for _name in dir(_time):
        if not _name.startswith("_"):
            setattr(shim, _name, getattr(_time, _name))
    shim.sleep = lambda _s: None
    monkeypatch.setattr(retry_mod, "time", shim)


def _extract_factory(*, result: dict | None = None, on_call=None):
    """A fake ``extract_session_v2`` returning an empty payload result."""
    base = result or {"payload": {}, "minted_kinds": [], "supersessions": [],
                      "errors": [], "stats": {}, "error_census": {}}

    def _fake(model, conversation, *, sdk=None, session_id=None,
              session_date=None):
        if on_call is not None:
            on_call(model, conversation)
        return base

    return _fake


# ── 1. no shadowing: exactly one definition ───────────────────────────────


def test_module_defines_ingest_haystack_v2_exactly_once():
    """#1744: two defs → last-def-wins → the parallel copy is unreachable.
    Pin the single definition so a re-fork cannot silently shadow again."""
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    defs = [n for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "ingest_haystack_v2"]
    assert len(defs) == 1, (
        f"expected exactly ONE def ingest_haystack_v2, found {len(defs)} "
        f"at lines {[d.lineno for d in defs]}")


def test_no_f811_suppression_remains():
    """The shadowing was marked ``# noqa: F811``. With one def, that
    suppression must be gone (a lingering noqa advertises the bug)."""
    src = MODULE.read_text(encoding="utf-8")
    assert "noqa: F811" not in src


# ── 2. model_factory is invoked once per session ──────────────────────────


def test_model_factory_invoked_once_per_session_when_parallel(monkeypatch):
    """The pre-fix live copy never referenced ``model_factory`` — this count
    was 0. Each session must now construct its OWN model (the RoutingModel's
    mutable route/truncation state must not be shared across threads)."""
    n = len(_question()["haystack_sessions"])
    built: list[object] = []

    def _factory():
        m = object()
        built.append(m)
        return m

    seen_models: list[object] = []
    monkeypatch.setattr(ev2, "extract_session_v2",
                        _extract_factory(on_call=lambda m, c: seen_models.append(m)))

    stats = ingest_haystack_v2(_FakeSDK(), _question(), object(),
                               session_workers=n, model_factory=_factory)
    assert len(built) == n, "model_factory must be called once per session"
    assert len(seen_models) == n, "every session must be extracted"
    # per-worker models — one distinct model object per session
    assert len({id(m) for m in seen_models}) == n
    assert stats["sessions"] == n and stats["errors"] == []


def test_model_factory_also_used_in_sequential_branch(monkeypatch):
    """session_workers == 1 keeps the sequential comprehension — the factory
    contract holds there too (one model per session)."""
    monkeypatch.setattr(ev2, "extract_session_v2", _extract_factory())
    n = len(_question()["haystack_sessions"])
    count = {"n": 0}

    def _factory():
        count["n"] += 1
        return object()

    stats = ingest_haystack_v2(_FakeSDK(), _question(), object(),
                               session_workers=1, model_factory=_factory)
    assert count["n"] == n
    assert stats["sessions"] == n


# ── 3. the extraction phase is genuinely concurrent ───────────────────────


def test_sequential_path_interleaves_extraction_after_each_raw_leg(monkeypatch):
    """The default ``session_workers=1`` path must stay INTERLEAVED
    (A→B→C per session): session k's extractor S3 search has to see the
    payload points sessions < k wrote, or cross-session NOOP / DELETE /
    supersession derivation is silently lost (E7 / E2E-11). The batched
    A-all → B-all → C-all order — which loses that visibility — is entered
    only with explicit session parallelism."""
    progress: list[int] = []
    ref: dict = {}

    def _on_call(model, conversation):
        progress.append(ref["sdk"]._point_writes)

    monkeypatch.setattr(ev2, "extract_session_v2",
                        _extract_factory(on_call=_on_call))
    n = 3

    sdk = _FakeSDK()
    ref["sdk"] = sdk
    ingest_haystack_v2(sdk, _question(n_sessions=n), object(),
                       session_workers=1)
    # each session's raw leg is written before its OWN extraction → the
    # cumulative write count seen by the extractor grows session by session
    assert progress == sorted(progress) and len(set(progress)) == n, progress

    progress.clear()
    sdk2 = _FakeSDK()
    ref["sdk"] = sdk2
    ingest_haystack_v2(sdk2, _question(n_sessions=n), object(),
                       session_workers=n, model_factory=lambda: object())
    # the batched parallel path writes EVERY raw leg before any extraction
    assert len(set(progress)) == 1, (
        f"batched parallel path must write all raw legs first: {progress}")


# ── 3b. the extraction phase is genuinely concurrent ───────────────────────


def test_session_workers_runs_extraction_concurrently(monkeypatch):
    """A barrier of ``session_workers`` parties only releases when that many
    extractions are in flight AT THE SAME TIME. Sequentially the first worker
    times out → BrokenBarrierError → classified into stats['errors'], so
    ``errors == []`` is a hard concurrency proof (not a timing heuristic)."""
    n = 4
    barrier = threading.Barrier(n, timeout=20)
    thread_ids: list[int] = []

    def _on_call(model, conversation):
        thread_ids.append(threading.get_ident())
        barrier.wait()

    monkeypatch.setattr(ev2, "extract_session_v2",
                        _extract_factory(on_call=_on_call))
    stats = ingest_haystack_v2(_FakeSDK(), _question(n_sessions=n), object(),
                               session_workers=n,
                               model_factory=lambda: object())
    assert stats["errors"] == [], (
        "extraction did not run concurrently (barrier broke): "
        f"{stats['errors']}")
    assert len(set(thread_ids)) == n, "each session should run on its own worker thread"
    assert stats["sessions"] == n


# ── 4. a worker transient propagates out of the pool (#1786 P2-2) ─────────


def test_worker_transient_propagates_out_of_thread_pool(monkeypatch):
    """A retryable provider transient raised inside a worker MUST re-raise
    (``_ex.map`` surfaces the first future exception) so run.py can mark the
    question ``--retry-failed``-eligible. Swallowing it here would complete
    the question with partial evidence and no failure entry."""
    calls = {"n": 0}

    def _fake(model, conversation, *, sdk=None, session_id=None,
              session_date=None):
        calls["n"] += 1
        if calls["n"] == 2:
            raise redis_exc.TimeoutError("simulated provider transient")
        return {"payload": {}, "minted_kinds": [], "supersessions": [],
                "errors": [], "stats": {}, "error_census": {}}

    monkeypatch.setattr(ev2, "extract_session_v2", _fake)
    with pytest.raises(redis_exc.TimeoutError):
        ingest_haystack_v2(_FakeSDK(), _question(n_sessions=4), object(),
                           session_workers=4, model_factory=lambda: object())


def test_deterministic_extractor_error_is_bookkept_not_raised(monkeypatch):
    """A deterministic (non-transient) extractor error must NOT raise — it is
    carried in the result and Phase C does the identical sequential
    bookkeeping (error list + census + one llm call)."""
    def _fake(model, conversation, *, sdk=None, session_id=None,
              session_date=None):
        raise ValueError("deterministic extractor bug")

    monkeypatch.setattr(ev2, "extract_session_v2", _fake)
    n = 3
    stats = ingest_haystack_v2(_FakeSDK(), _question(n_sessions=n), object(),
                               session_workers=n, model_factory=lambda: object())
    assert len(stats["errors"]) == n
    assert sum(stats["error_census"].values()) == n
    assert stats["llm"]["calls"] == n
    # failed sessions write no payload — turns are not counted (sequential parity)
    assert stats["turns"] == 0


# ── 5. the surviving function is the feature-complete one ─────────────────


def test_s4_merge_is_rolled_up_into_stats(monkeypatch):
    """#2408 Task 2: each session's ``stats.s4_merge`` composition census must
    be summed into the per-question stats (scalar-additive)."""
    per_session = {"payload": {}, "minted_kinds": [], "supersessions": [],
                   "errors": [], "error_census": {},
                   "stats": {"s4_merge": {"unchanged": 1, "correction": 2,
                                          "gap": 0}}}
    monkeypatch.setattr(ev2, "extract_session_v2",
                        _extract_factory(result=per_session))
    n = 3
    stats = ingest_haystack_v2(_FakeSDK(), _question(n_sessions=n), object(),
                              session_workers=n, model_factory=lambda: object())
    assert stats["s4_merge"] == {"unchanged": n, "correction": 2 * n, "gap": 0}


def test_write_retries_accepted_and_honored(monkeypatch):
    """R1 #1786: ``ingest_write_retries`` / ``write_marker_armed`` must be
    accepted by the live signature AND honored — a transient on the first
    Phase-A write is retried and counted in ``stats['ingest_retries']``."""
    monkeypatch.setattr(ev2, "extract_session_v2", _extract_factory())
    # no real backoff sleeps in the test
    _silence_retry_sleep(monkeypatch)

    sdk = _FakeSDK(fail_first_point_write=True)
    stats = ingest_haystack_v2(sdk, _question(n_sessions=1), object(),
                               ingest_write_retries=1,
                               write_marker_armed=False)
    assert stats["ingest_retries"] == 1
    assert stats["sessions"] == 1 and stats["errors"] == []


def test_write_marker_armed_controls_exhausted_sentinel(monkeypatch):
    """With the budget exhausted: ``write_marker_armed=True`` wraps the
    transient in ``WriteStageRetriesExhausted`` (the R2 whole-question
    marker); DISARMED (a resume re-attempt) re-raises the ORIGINAL — no
    second budget (P1-1)."""
    from tools.longmem_eval.errors import WriteStageRetriesExhausted
    monkeypatch.setattr(ev2, "extract_session_v2", _extract_factory())
    _silence_retry_sleep(monkeypatch)

    with pytest.raises(WriteStageRetriesExhausted):
        ingest_haystack_v2(_FakeSDK(fail_first_point_write=True),
                           _question(n_sessions=1), object(),
                           ingest_write_retries=0, write_marker_armed=True)

    with pytest.raises(redis_exc.TimeoutError):
        ingest_haystack_v2(_FakeSDK(fail_first_point_write=True),
                           _question(n_sessions=1), object(),
                           ingest_write_retries=0, write_marker_armed=False)
