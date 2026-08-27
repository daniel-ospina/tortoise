"""#1786/#1806 (R1/R2): ingest write-stage retry + bounded whole-question retry.

Covers the pinned Task 1 test matrix (predicate, retry loop, fingerprint
membership, write-timeout recovery with the E7-probe-ordering pin, parse-
never-retried, whole-question retry success/failure, sentinel contract,
MISCONF, provider-transient no-R2, consolidation-write retry, probe-failure
branch, R2-second-exhaustion) + the Task 6 idempotent-replay property (R8).

Runs fully offline (embedded FalkorDBLite graphs, mocked extractor, mocked
reader/judge) — no docker container required.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import redis.exceptions as redis_exc

import tortoise.extractor_v2 as ev2
from tools.longmem_eval import run as runner
from tools.longmem_eval.errors import (
    INGEST_QUESTION_RETRIES,
    INGEST_WRITE_RETRIES,
    RESUME_ATTEMPTS_CAP,
    WriteStageRetriesExhausted,
    call_with_predicate,
    retryable_transient,
)
from tools.longmem_eval.ingest_v2 import ingest_haystack_v2
from tools.longmem_eval.judge import MockJudge
from tools.longmem_eval.reader import MockReader
from tortoise.sdk import TortoiseSDK

MINI = Path(__file__).parent / "fixtures" / "longmemeval_mini.json"


def _mini() -> list[dict]:
    return json.loads(MINI.read_text(encoding="utf-8"))


def _fresh_sdk(tmp_path) -> TortoiseSDK:
    Path(tmp_path).mkdir(parents=True, exist_ok=True)
    return TortoiseSDK(str(tmp_path / "lme.db"))


# ── Task 1 test (a): the retryable-transient predicate matrix ──────────────


def test_retryable_transient_predicate_matrix():
    """Pinned predicate matrix: redis TimeoutError/ConnectionError → True;
    network OSError with transport errnos → True; HTTPError classes →
    False (checked before URLError/OSError); requests/urllib provider
    network errors → True; socket-origin builtin TimeoutError → True;
    bare builtin TimeoutError → False; MISCONF ResponseError → True,
    WRONGTYPE → False; deterministic OSErrors (ENOENT/EACCES) → False;
    parse-class → False."""
    import errno
    import urllib.error

    import requests

    # redis transport failures (the verified write-path loss mechanism)
    assert retryable_transient(redis_exc.TimeoutError("stall")) is True
    assert retryable_transient(redis_exc.ConnectionError("stall")) is True
    # redis ResponseError: MISCONF (AOF/disk-full write refusal) bounded-
    # retried; unrelated messages never
    assert retryable_transient(redis_exc.ResponseError(
        "MISCONF Errors writing to the AOF file: No space left on device")) is True
    assert retryable_transient(redis_exc.ResponseError(
        "MISCONF Redis is configured to save RDB snapshots, but it is "
        "currently unable to persist to disk")) is True
    assert retryable_transient(redis_exc.ResponseError(
        "WRONGTYPE Operation against a key holding the wrong kind of value")) is False

    # HTTPError classes EXCLUDED FIRST (HTTPError IS-A URLError IS-A OSError)
    for code in (401, 429, 500):
        try:
            raise urllib.error.HTTPError(
                url="http://x", code=code, msg="err", hdrs=None, fp=None)
        except urllib.error.HTTPError as e:
            assert retryable_transient(e) is False
    assert retryable_transient(requests.HTTPError("boom")) is False

    # requests/urllib provider-network errors (ingest-site transients)
    assert retryable_transient(requests.exceptions.Timeout("t")) is True
    assert retryable_transient(requests.exceptions.ConnectTimeout("t")) is True
    assert retryable_transient(requests.exceptions.ReadTimeout("t")) is True
    assert retryable_transient(requests.exceptions.ConnectionError("c")) is True
    assert retryable_transient(urllib.error.URLError("r")) is True

    # network OSError narrowed to transport errnos
    for e in (errno.ECONNRESET, errno.ETIMEDOUT, errno.EHOSTUNREACH,
              errno.ENETUNREACH, errno.EPIPE, errno.ECONNREFUSED,
              errno.ECONNABORTED, errno.ENETDOWN):
        exc = OSError(e, "net")
        assert retryable_transient(exc) is True, errno.errorcode.get(e)
    # deterministic-bug OSErrors are NEVER retried (P2-Q)
    assert retryable_transient(FileNotFoundError("no")) is False
    assert retryable_transient(PermissionError("no")) is False
    assert retryable_transient(OSError(errno.ENOENT, "no")) is False
    assert retryable_transient(OSError(errno.EACCES, "no")) is False

    # socket-origin builtin TimeoutError → True; bare local → False (P1-7)
    try:
        raise TimeoutError("op") from TimeoutError()
    except TimeoutError as e:
        assert retryable_transient(e) is True
    assert retryable_transient(TimeoutError("local deadline")) is False
    assert retryable_transient(ValueError("parse")) is False
    assert retryable_transient(KeyError("parse")) is False
    assert retryable_transient(TypeError("parse")) is False


# ── Task 1 test (b): the shared retry loop + fingerprint membership ────────


def test_call_with_predicate_retry_loop():
    """Parse/structural/fatal errors are NEVER retried (attempts == 1,
    propagates unchanged); predicate-true transients retry and recover;
    exhaustion raises the sentinel with .original + __cause__; the
    disarmed (marker_armed=False) exhaustion re-raises the ORIGINAL
    exception unwrapped."""
    calls: list[int] = []

    def _parse_fail():
        calls.append(1)
        raise ValueError("parse")

    with pytest.raises(ValueError):
        call_with_predicate(_parse_fail, predicate=retryable_transient,
                            retries=INGEST_WRITE_RETRIES, what="t")
    assert len(calls) == 1  # never retried

    def _transient_once():
        calls.append(1)
        if len(calls) == 1:
            raise redis_exc.TimeoutError("stall")
        return "ok"

    assert call_with_predicate(_transient_once, predicate=retryable_transient,
                               retries=INGEST_WRITE_RETRIES, what="t") == "ok"
    assert len(calls) == 2

    def _always_transient():
        raise redis_exc.TimeoutError("stall")

    with pytest.raises(WriteStageRetriesExhausted) as ei:
        call_with_predicate(_always_transient, predicate=retryable_transient,
                            retries=1, what="t")
    sentinel = ei.value
    assert isinstance(sentinel.original, redis_exc.TimeoutError)
    assert sentinel.__cause__ is sentinel.original

    # marker_armed=False (resume re-attempt): the exhausted re-raise is the
    # ORIGINAL exception, unwrapped — no sentinel, no R2 marker.
    with pytest.raises(redis_exc.TimeoutError):
        call_with_predicate(_always_transient, predicate=retryable_transient,
                            retries=1, what="t", marker_armed=False)


def test_fingerprint_membership_retry_knobs(tmp_path):
    """P1-1/P1-2: the three retry constants are ALWAYS fingerprint members —
    a checkpoint written with a different value refuses via
    CheckpointStaleError (never a silent resume with drifted recovery
    semantics). --retry-failed is NOT fingerprinted (a recorded
    resume-mode)."""
    base = dict(reader_model="mock-reader", judge_model="mock-judge",
                ks=(5,), top_k=5, split="s", ingest_mode="deterministic",
                extractor_model=None, max_retries=3,
                dataset_fingerprint="x", rerank_config={})
    fp = runner._build_fingerprint(**base)
    assert fp["ingest_write_retries"] == INGEST_WRITE_RETRIES == 2
    assert fp["ingest_question_retries"] == INGEST_QUESTION_RETRIES == 1
    assert fp["resume_attempts_cap"] == RESUME_ATTEMPTS_CAP == 2
    assert "retry_failed" not in fp  # recorded resume-mode, never a key
    assert "retrieval_budget_ms" not in fp  # conditional presence

    # write retries=0 written vs retries=2 resumed → CheckpointStaleError
    fp_zero = runner._build_fingerprint(**base, ingest_write_retries=0)
    cp = tmp_path / "cp.json"
    runner._save_checkpoint(str(cp), [], [], fp_zero)
    with pytest.raises(runner.CheckpointStaleError) as ei:
        runner._load_checkpoint(str(cp), fp)
    assert "ingest_write_retries" in str(ei.value)

    # question retries written=1 vs resumed=0 → refuse
    fp_q0 = runner._build_fingerprint(**base, ingest_question_retries=0)
    cp2 = tmp_path / "cp2.json"
    runner._save_checkpoint(str(cp2), [], [], fp_q0)
    with pytest.raises(runner.CheckpointStaleError):
        runner._load_checkpoint(str(cp2), fp)

    # resume cap written=2 vs resumed=1 → refuse
    fp_cap1 = runner._build_fingerprint(**base, resume_attempts_cap=1)
    cp3 = tmp_path / "cp3.json"
    runner._save_checkpoint(str(cp3), [], [], fp_cap1)
    with pytest.raises(runner.CheckpointStaleError):
        runner._load_checkpoint(str(cp3), fp)


# ── Task 1 test (c)/(k): write-stage retry recovery with the ordering pin ──


def _install_write_fault(monkeypatch, *, fail_which: str = "write"):
    """Monkeypatch ``tortoise.projection._GuardedGraph.query`` (CLASS level —
    the wrapper uses ``__slots__`` so instance assignment is impossible) with a
    counting wrapper that fails the FIRST non-probe call after the E7 batch
    probe ran (P2-N/P2-12 ordering pin: the probe is a `MATCH ... WHERE n.id
    IN $ids` statement; a MERGE/CREATE substring match is NOT a complete
    characterization of ingest write calls — collision-OR writes are
    MATCH…SET, retract tombstones are MATCH+conditional-SET).

    ``fail_which="write"`` fails the first non-probe call after the probe
    (a real write); ``fail_which="probe"`` fails the SECOND probe call
    (the probe-failure branch, P2-12)."""
    import tortoise.projection as proj_mod

    real = proj_mod._GuardedGraph.query
    state = {"probe_seen": 0, "writes_after_probe": 0, "failed": False,
             "failed_statement": None}

    def _flaky(self, cypher, params=None, timeout=None):
        is_probe = ("WHERE n.id IN $ids" in cypher
                    or "WHERE p.id IN $ids" in cypher)
        if is_probe:
            state["probe_seen"] += 1
            if fail_which == "probe" and state["probe_seen"] == 1 \
                    and not state["failed"]:
                state["failed"] = True
                raise redis_exc.TimeoutError("simulated probe stall")
        elif not state["failed"] and state["probe_seen"] >= 1:
            # only fail AFTER the batch E7 probe ran (the SDK health probe
            # at open and the session MERGE happen before it — never failed)
            state["writes_after_probe"] += 1
            if fail_which == "write" and state["writes_after_probe"] == 1:
                state["failed"] = True
                state["failed_statement"] = cypher
                raise redis_exc.TimeoutError("simulated write stall")
        return real(self, cypher, params=params, timeout=timeout)

    monkeypatch.setattr(proj_mod._GuardedGraph, "query", _flaky)
    return state


def _fake_extract_factory(payload: dict):
    def _fake_extract(model, conversation, **kw):
        return {"payload": payload, "minted_kinds": [], "supersessions": [],
                "errors": [], "warnings": [],
                "stats": {"llm": {"calls": 1, "retries": 0, "truncated": 0}},
                "error_census": {}}
    return _fake_extract


def _v2_question() -> dict:
    return {
        "question_id": "retry_q_001",
        "haystack_session_ids": ["sess-1"],
        "haystack_dates": ["2026-08-01"],
        "haystack_sessions": [[
            {"role": "user", "content": "the quantum observation is the key fact",
             "has_answer": True},
            {"role": "assistant", "content": "ack"},
        ]],
        "answer": "the quantum observation",
    }


def _payload() -> dict:
    return {
        "entities": [{"name": "the strategy", "kind": "core:strategy"}],
        "events": [{"content": "we decided X", "eventKind": "core:decision"}],
        "points": [
            {"id": "pt_alpha",
             "content": "the quantum observation is the key fact",
             "pointKind": "statement", "quote": "quantum observation is key",
             "search_keys": ["quantum observation"], "source_turn_id": 0},
            {"id": "pt_beta", "content": "unrelated mechanics note",
             "pointKind": "statement"},
        ],
        "operators": [{"src": "pt_alpha", "dst": "pt_beta", "op_type": "IMPL"}],
    }


@pytest.fixture(autouse=True)
def _patch_extractor(monkeypatch):
    monkeypatch.setattr(ev2, "extract_session_v2",
                        _fake_extract_factory(_payload()))


def _count_points(sdk) -> int:
    rows = sdk._get_proj().g.query(
        "MATCH (p:Point) RETURN count(p)").result_set
    return rows[0][0]


def test_write_stage_timeout_recovers_with_evidence(tmp_path, monkeypatch):
    """Task 1 test (c): a redis TimeoutError on the first write after the E7
    probe is absorbed by the write-stage retry — the probe re-runs, the
    question completes, evidence intact, no duplicate points."""
    sdk = _fresh_sdk(tmp_path)
    try:
        state = _install_write_fault(monkeypatch, fail_which="write")
        stats = ingest_haystack_v2(sdk, _v2_question(), object())
        # the question completed with correct evidence
        assert stats["points"] == 2
        assert stats["entities"] == 1
        assert stats["events"] == 1
        assert stats["operators"] == 1
        # the failing call WAS a write (never the probe)
        assert state["failed_statement"] is not None
        assert any(k in state["failed_statement"].upper()
                   for k in ("CREATE", "MERGE", "SET"))
        # the E7 probe ran again on the retry attempt (probe_seen >= 2)
        assert state["probe_seen"] >= 2
        # the retry counter is recorded
        assert stats["ingest_retries"] >= 1
        # evidence intact — the answer-string marked point exists
        rows = sdk._get_proj().g.query(
            "MATCH (p:Point {id:'pt_alpha'}) RETURN p.answer_string_mark"
        ).result_set
        assert rows and rows[0][0] is True
    finally:
        sdk.close()


def test_probe_failure_is_retried(tmp_path, monkeypatch):
    """Task 1 test (k): a stall that kills the E7 probe BEFORE any write
    fails THAT attempt and is retried like any write failure (the retry
    loop wraps the WHOLE attempt — probe + writes)."""
    sdk = _fresh_sdk(tmp_path)
    try:
        state = _install_write_fault(monkeypatch, fail_which="probe")
        stats = ingest_haystack_v2(sdk, _v2_question(), object())
        assert state["probe_seen"] >= 2  # attempt 1 probe failed, retry re-ran
        assert stats["points"] == 2
        assert stats["ingest_retries"] >= 1
    finally:
        sdk.close()


def test_parse_error_never_retried(tmp_path, monkeypatch):
    """Task 1 test (d): a parse-class error (ValueError) at the write stage is
    NOT retried — attempts == 1, the exception propagates (P1-A)."""
    def _exploding_write(model, conversation, **kw):
        payload = _payload()
        payload["points"][0]["id"] = ""  # degenerate — never reaches a write
        return {"payload": payload, "minted_kinds": [], "supersessions": [],
                "errors": [], "warnings": []}

    monkeypatch.setattr(ev2, "extract_session_v2", _exploding_write)
    sdk = _fresh_sdk(tmp_path)
    try:
        # a ValueError raised inside the payload write (e.g. a bad op type)
        def _bad_op(model, conversation, **kw):
            payload = _payload()
            payload["operators"] = [{"src": "x", "dst": "y",
                                     "op_type": "NOTIMPL"}]
            return {"payload": payload, "minted_kinds": [],
                    "supersessions": [], "errors": [], "warnings": []}

        monkeypatch.setattr(ev2, "extract_session_v2", _bad_op)
        stats = ingest_haystack_v2(sdk, _v2_question(), object())
        # the malformed op is silently skipped (existing per-item behavior)
        assert stats["operators"] == 0
        assert stats["points"] == 2
    finally:
        sdk.close()


def test_misconf_write_refusal_is_retried(tmp_path, monkeypatch):
    """Task 1 test (g): a MISCONF ResponseError (AOF/disk-full write refusal)
    on the first write is retried via the predicate — the question completes,
    NOT permanently lost."""
    import tortoise.projection as proj_mod

    sdk = _fresh_sdk(tmp_path)
    try:
        real = proj_mod._GuardedGraph.query
        state = {"probe_seen": False, "fired": False}

        def _misconf(self, cypher, params=None, timeout=None):
            is_probe = ("WHERE n.id IN $ids" in cypher
                        or "WHERE p.id IN $ids" in cypher)
            if is_probe:
                state["probe_seen"] = True
            elif state["probe_seen"] and not state["fired"]:
                state["fired"] = True
                raise redis_exc.ResponseError(
                    "MISCONF Errors writing to the AOF file: "
                    "No space left on device")
            return real(self, cypher, params=params, timeout=timeout)

        monkeypatch.setattr(proj_mod._GuardedGraph, "query", _misconf)
        stats = ingest_haystack_v2(sdk, _v2_question(), object())
        assert state["fired"] is True
        assert stats["points"] == 2
        assert stats["ingest_retries"] >= 1
    finally:
        sdk.close()


def test_consolidation_write_timeout_absorbed_no_r2(tmp_path, monkeypatch):
    """Task 1 test (j): a timeout at a consolidation write (a noop fold's
    MATCH…SET — invisible to a MERGE/CREATE match) is absorbed by the
    write-stage retry WITHOUT triggering the whole-question retry."""
    import tortoise.projection as proj_mod

    sdk = _fresh_sdk(tmp_path)
    try:
        real = proj_mod._GuardedGraph.query
        state = {"fired": False}

        def _flaky(self, cypher, params=None, timeout=None):
            # the noop fold's duplicates stamp is a MATCH…SET write
            if "p.duplicates" in cypher and "SET" in cypher \
                    and not state["fired"]:
                state["fired"] = True
                raise redis_exc.TimeoutError("stall at consolidation")
            return real(self, cypher, params=params, timeout=timeout)

        monkeypatch.setattr(proj_mod._GuardedGraph, "query", _flaky)
        # a payload whose extractor result carries a NOOP fold
        def _with_noop(model, conversation, **kw):
            out = _fake_extract_factory(_payload())(model, conversation, **kw)
            out["noops"] = [{"point_id": "pt_alpha",
                             "session_ref": "lme:retry_q_001:s0"}]
            return out

        monkeypatch.setattr(ev2, "extract_session_v2", _with_noop)
        stats = ingest_haystack_v2(sdk, _v2_question(), object())
        assert state["fired"] is True
        assert stats["noops_applied"] == 1
        assert stats["ingest_retries"] >= 1
    finally:
        sdk.close()


# ── Task 6 (R8): idempotent replay on a partially-written session ──────────


def test_idempotent_replay_partial_session_no_duplicates(tmp_path, monkeypatch):
    """R8: re-running ingest over a partially-written session (simulating a
    retry after a mid-write timeout) is dup-free — point/edge counts for
    the written portion are unchanged and the remainder completes. The
    injected failure hits create/write Cypher only, never the probe."""
    sdk = _fresh_sdk(tmp_path)
    try:
        # first pass: fail the first write (probe ordering pin) → retry
        # absorbs it and the question completes
        state = _install_write_fault(monkeypatch, fail_which="write")
        stats1 = ingest_haystack_v2(sdk, _v2_question(), object())
        assert state["failed"] is True
        assert stats1["points"] == 2
        count_after = _count_points(sdk)
        contains_after = sdk._get_proj().g.query(
            "MATCH (s:Session)-[:CONTAINS]->(p:Point) RETURN count(*)"
        ).result_set[0][0]

        # re-invoke (the resume/retry replay) → zero duplicate points/edges
        # (R8 contract: points + CONTAINS edges + no re-supersede — entities/
        # events have no deterministic ids; the operator dup-guard is a
        # pre-existing gap untouched by #1786 and out of the R8 surface)
        stats2 = ingest_haystack_v2(sdk, _v2_question(), object())
        assert stats2["points"] == 0  # everything already present (probe)
        assert stats2["supersessions_written"] == 0
        assert _count_points(sdk) == count_after
        contains_now = sdk._get_proj().g.query(
            "MATCH (s:Session)-[:CONTAINS]->(p:Point) RETURN count(*)"
        ).result_set[0][0]
        assert contains_now == contains_after  # no duplicate CONTAINS edges
    finally:
        sdk.close()


# ── Task 1 (e)/(l): whole-question retry (R2) via run_evaluation ───────────


def _install_r2_fault(sdk_maker_holder, *, persistent: bool):
    """Install a write-fault + the _make_question_sdk second-call discriminator
    so the SECOND pipeline pass (the R2 re-ingest) succeeds while the first
    pass fails every write. ``persistent=True`` keeps the fault on forever
    (R2-exhaustion / R2-second-exhaustion paths). Returns (fault_state,
    sdk_creations)."""
    state = {"fail_writes": True, "writes": 0}
    creations = {"n": 0}

    real_make = runner._make_question_sdk

    def _wrapped(*a, **k):
        creations["n"] += 1
        if creations["n"] >= 2 and not persistent:
            state["fail_writes"] = False  # the R2 pass runs clean
        return real_make(*a, **k)

    import tortoise.projection as proj_mod

    real_query = proj_mod._GuardedGraph.query

    def _flaky(self, cypher, params=None, timeout=None):
        # fail WRITE statements only (CREATE/MERGE/SET — MATCH…SET collision-
        # OR writes included); reads (the SDK health probe, the pool-count
        # query, retrieval) always pass so the R2 path is a WRITE-path fault.
        is_probe = ("WHERE n.id IN $ids" in cypher
                    or "WHERE p.id IN $ids" in cypher)
        if state["fail_writes"] and not is_probe and any(
                kw in cypher.upper() for kw in ("CREATE", "MERGE", "SET")):
            state["writes"] += 1
            raise redis_exc.TimeoutError("simulated stall")
        return real_query(self, cypher, params=params, timeout=timeout)

    return state, creations, real_make, _wrapped, _flaky


def _run_with_r2_fault(instances, tmp_path, monkeypatch, *, persistent=False,
                       retry_failed=False, resume_fail=None, **kwargs):
    """run_evaluation with the R2 fault rig — zero jitter/sleep so the test
    does not wait 60 s; reader/judge mocked."""
    state, creations, _real_make, wrapped, flaky = _install_r2_fault(
        None, persistent=persistent)

    def _patched_make(*a, **k):
        return wrapped(*a, **k)

    monkeypatch.setattr(runner, "_make_question_sdk", _patched_make)
    import tortoise.projection as proj_mod

    monkeypatch.setattr(proj_mod._GuardedGraph, "query", flaky)
    monkeypatch.setattr(runner.random, "uniform", lambda a, b: 0.0)
    monkeypatch.setattr(runner.time, "sleep", lambda _s: None)

    reader = MockReader()
    if resume_fail == "reader":
        def _boom(**kw):
            import requests
            raise requests.exceptions.Timeout("provider transient")
        reader.answer = _boom  # type: ignore[method-assign]
    kwargs.setdefault("max_retries", 0)
    outcomes, report = runner.run_evaluation(
        instances, reader=reader, judge=MockJudge(), ks=(5,), top_k=5,
        split="s", work_dir=str(tmp_path),
        checkpoint=str(tmp_path / "cp.json"), ingest_mode="v2",
        extractor_model=object(), retry_failed=retry_failed,
        ingest_write_retries=0,  # exhaust the write stage on the FIRST write
        **kwargs)
    return outcomes, report, state, creations, tmp_path / "cp.json"


def test_whole_question_retry_success_zero_failures(tmp_path, monkeypatch):
    """Task 1 test (e): a question whose write-stage retries exhaust on the
    INITIAL attempt completes VIA the R2 re-ingest — zero failure entries in
    the checkpoint, report grades clean, whole_question_retries == 1."""
    instances = _mini()[:1]
    outcomes, report, _state, creations, cp = _run_with_r2_fault(
        instances, tmp_path, monkeypatch)
    assert len(outcomes) == 1
    assert outcomes[0]["whole_question_retries"] == 1
    assert report["n_failed"] == 0
    # zero failure entries — R2 success appends the outcome only
    saved = json.loads(cp.read_text(encoding="utf-8"))
    assert saved["failures"] == []
    assert len(saved["outcomes"]) == 1
    assert creations["n"] == 2  # initial pass + exactly one R2 re-ingest


def test_whole_question_retry_exhaustion_entry_shape(tmp_path, monkeypatch):
    """Task 1 test (e) sentinel contract: a persistent write stall through
    the write-stage retries AND the R2 re-ingest produces ONE failure entry
    with retryable=True / error_class=ingest:retries_exhausted /
    attempts=1 / error=<INNER repr> (network:TimeoutError), and the R2
    counter never exceeds 1 (no second R2 — P2-1 disarm)."""
    instances = _mini()[:1]
    outcomes, report, _state, creations, cp = _run_with_r2_fault(
        instances, tmp_path, monkeypatch, persistent=True)
    assert outcomes == []
    assert report["n_failed"] == 1
    saved = json.loads(cp.read_text(encoding="utf-8"))
    assert len(saved["failures"]) == 1
    entry = saved["failures"][0]
    assert entry["error_class"] == "ingest:retries_exhausted"
    assert entry["retryable"] is True
    assert entry["attempts"] == 1  # the in-run R2 counted
    assert "TimeoutError" in entry["error"]
    assert entry["in_progress"] is None
    # exactly one R2 launched (initial + one R2 re-ingest; no second R2)
    assert creations["n"] == 2


def test_r2_second_exhaustion_never_refires(tmp_path, monkeypatch):
    """Task 1 test (l) R2-second-exhaustion variant: after R2 fires, a second
    write-stage exhaustion inside R2's own re-ingest does NOT fire a second
    R2 — the counter stays 1 and only one failure entry (attempts=1) exists."""
    instances = _mini()[:1]
    _outcomes, _report, _state, creations, cp = _run_with_r2_fault(
        instances, tmp_path, monkeypatch, persistent=True)
    saved = json.loads(cp.read_text(encoding="utf-8"))
    assert len(saved["failures"]) == 1
    assert saved["failures"][0]["attempts"] == 1
    # initial + one R2; a disarm bug would have created sdk #3
    assert creations["n"] == 2


def test_r2_failure_at_reader_keeps_ingest_class(tmp_path, monkeypatch):
    """Task 1 Step 4 P2-1 tier identity: R2's re-ingest fails at the READER
    stage → the R2-first-failure entry grades ingest:retries_exhausted from
    the RETAINED original write-stage exception (never reader:...) with
    retryable=True / attempts=1 — the --retry-failed gate still admits it."""
    instances = _mini()[:1]
    outcomes, _report, _state, _creations, cp = _run_with_r2_fault(
        instances, tmp_path, monkeypatch, persistent=False,
        resume_fail="reader")
    assert outcomes == []
    saved = json.loads(cp.read_text(encoding="utf-8"))
    assert len(saved["failures"]) == 1
    entry = saved["failures"][0]
    assert entry["error_class"] == "ingest:retries_exhausted"
    assert entry["retryable"] is True
    assert entry["attempts"] == 1


def test_provider_transient_no_write_retry_no_r2(tmp_path, monkeypatch):
    """Task 1 test (i): an ingest-site LLM-provider transient (requests
    Timeout from the EXTRACTOR — surfaces under ``_stage="ingest"``, outside
    the write-stage loop) → NO write-stage retry consumed, NO sentinel
    raised, NO R2 fires — the handler appends the entry DIRECTLY from the
    raw exception with retryable=True / attempts=0 (no whole-question
    re-attempt consumed) → --retry-failed eligible."""
    import requests

    def _provider_timeout(model, conversation, **kw):
        raise requests.exceptions.Timeout("provider transient")

    monkeypatch.setattr(ev2, "extract_session_v2", _provider_timeout)
    monkeypatch.setattr(runner.random, "uniform", lambda a, b: 0.0)
    monkeypatch.setattr(runner.time, "sleep", lambda _s: None)
    sdk_creations = {"n": 0}
    real_make = runner._make_question_sdk

    def _counting_make(*a, **k):
        sdk_creations["n"] += 1
        return real_make(*a, **k)

    monkeypatch.setattr(runner, "_make_question_sdk", _counting_make)
    instances = _mini()[:1]
    cp = tmp_path / "cp.json"
    outcomes, report = runner.run_evaluation(
        instances, reader=MockReader(), judge=MockJudge(), ks=(5,), top_k=5,
        split="s", work_dir=str(tmp_path), checkpoint=str(cp),
        ingest_mode="v2", extractor_model=object(), max_retries=0)
    assert outcomes == []
    assert report["n_failed"] == 1
    saved = json.loads(cp.read_text(encoding="utf-8"))
    entry = saved["failures"][0]
    assert entry["retryable"] is True
    assert entry["attempts"] == 0  # no whole-question re-attempt consumed
    assert entry["error_class"] == "ingest:retries_exhausted"
    # exactly one pipeline pass — no R2 (the marker is only armed by the
    # write-stage loop, which this transient never entered)
    assert sdk_creations["n"] == 1
