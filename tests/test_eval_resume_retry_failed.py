"""#1786 (R3): resume re-attempts of transient-failed questions (--retry-failed).

Covers the pinned Task 2 matrix: atomic remove-on-success, read-through
merge reconciliation (stamps + attempts max), the resume gate (retryable
field + legacy repr rescue + near-miss reprs), counter transitions, the
resume-internal R2 suppression, corrupt-checkpoint quarantine + schema
validation, the flocked claim CAS (liveness + TTL clock-advance), recovery-
tier class preservation, and the class-excluded warning.

Run-based tests seed the transient failure via a REAL prior failing run
(same fingerprint by construction); unit-level tests hand-write checkpoints
(no run_evaluation — no fingerprint gate involved).

Runs fully offline (embedded FalkorDBLite graphs, mocked extractor,
mocked reader/judge) — no docker container required.
"""
from __future__ import annotations

import contextlib
import json
import os
import sys
import tempfile
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import redis.exceptions as redis_exc

import tortoise.extractor_v2 as ev2
import tortoise.projection as proj_mod
from tools.longmem_eval import run as runner
from tools.longmem_eval.judge import MockJudge
from tools.longmem_eval.reader import MockReader
from tortoise.sdk import TortoiseSDK

MINI = Path(__file__).parent / "fixtures" / "longmemeval_mini.json"

# The TRUE original _GuardedGraph.query — captured at import time (before any
# test monkeypatch) so per-call re-installation is exact.
_ORIGINAL_QUERY = proj_mod._GuardedGraph.query


def _mini() -> list[dict]:
    return json.loads(MINI.read_text(encoding="utf-8"))


# ── #1988: one embedded server per pytest process (not per question) ────────
# run_evaluation calls runner._make_question_sdk(db_uri=None) PER QUESTION —
# a fresh embedded redislite server each time. In the PR fast-matrix process
# (hundreds of embedded servers already spawned before this suite runs late
# in the leg) the server process can no longer start →
# RedisLiteServerStartError → every question fails with zero outcomes. The
# resume rigs hand the pipeline this shared module-level SDK instead (one
# server per process). The pipeline closes the per-question SDK after every
# question (run.py finally: sdk.close()), so the shared instance's close()
# is a no-op and _embedded_shared_teardown owns the real teardown at module
# end.
_embedded_shared: tuple[TortoiseSDK, tempfile.TemporaryDirectory] | None = None


def _noop_close() -> None:
    """No-op close for the shared SDK — see the #1988 note above."""
    return None


def _shared_embedded_sdk() -> TortoiseSDK:
    """Lazily create the one embedded SDK shared by every run_evaluation
    question in this module (issue #1988)."""
    global _embedded_shared
    if _embedded_shared is None or not _shared_alive(_embedded_shared[0]):
        # #1988 (self-heal): the redislite-hygiene reaper kills long-lived
        # IDLE embedded servers mid-suite (it classifies them as orphans) —
        # if the shared server died, tear it down and rebuild.
        _discard_shared()
        td = tempfile.TemporaryDirectory(prefix="lme-shared-")
        sdk = TortoiseSDK(os.path.join(td.name, "lme.db"))
        sdk.close = _noop_close  # type: ignore[method-assign]
        # Eager server start at the quietest point (SDK creation), with the
        # #1944 60s start budget — a lazy first start inside a loaded
        # run_evaluation can exceed the vendored 10s default on this host.
        import redislite.client as _rc
        if _rc.Redis.start_timeout < 60:
            _rc.Redis.start_timeout = 60
        with contextlib.suppress(Exception):
            sdk._get_proj().g.query("RETURN 1 AS one")
        _embedded_shared = (sdk, td)
    return _embedded_shared[0]


def _shared_alive(sdk) -> bool:
    """True if the shared embedded server still answers (the reaper can kill
    idle servers mid-suite — #1988 self-heal)."""
    try:
        sdk._get_proj().g.query("RETURN 1 AS one")
        return True
    except Exception:
        return False


def _discard_shared() -> None:
    global _embedded_shared
    if _embedded_shared is not None:
        sdk, td = _embedded_shared
        try:
            with contextlib.suppress(Exception):
                del sdk.close  # restore the real close (was no-op'd)
            with contextlib.suppress(Exception):
                sdk.close()
        finally:
            with contextlib.suppress(Exception):
                td.cleanup()
        _embedded_shared = None


def _reset_shared_graph(instances) -> None:
    """Restore the per-question fresh-namespace semantics the census gate
    relies on: the shared embedded graph is wiped of every instance qid's
    nodes BEFORE each run (the db_uri path performs the same per-question
    wipe — the embedded path relied on fresh tempdirs, which a shared
    server cannot provide; a dirty re-ingest of the same qid trips the
    watchdog's hard-invalid census arm)."""
    if not instances:
        return
    sdk = _shared_embedded_sdk()
    proj = sdk._get_proj()
    for inst in instances:
        with contextlib.suppress(Exception):
            proj.g.query(
                "MATCH (n) WHERE n.lme_question_id = $q DETACH DELETE n",
                params={"q": inst["question_id"]})


@pytest.fixture(scope="module", autouse=True)
def _embedded_shared_teardown():
    """Real teardown of the shared embedded SDK at module end (the pipeline's
    per-question close() is neutralized above; the redislite atexit cleanup
    would kill the server anyway, this also removes the tempdir)."""
    yield
    if _embedded_shared is not None:
        sdk, td = _embedded_shared
        try:
            del sdk.close  # restore the real close for the teardown
            with contextlib.suppress(Exception):
                sdk.close()
        finally:
            with contextlib.suppress(Exception):
                td.cleanup()


@pytest.fixture(autouse=True)
def _patch_extractor(monkeypatch):
    """Every v2 ingest in this file uses a fake extractor — the resume tests
    exercise the WRITE path + checkpoint lifecycle, never the real LLM."""
    def _fake_extract(model, conversation, **kw):
        payload = {
            "entities": [{"name": "the strategy", "kind": "core:strategy"}],
            "events": [{"content": "we decided X", "eventKind": "core:decision"}],
            "points": [
                {"id": "pt_alpha", "content": "the quantum observation is "
                 "the key fact", "pointKind": "statement",
                 "quote": "quantum observation is key",
                 "source_turn_id": 0},
            ],
            "operators": [],
        }
        return {"payload": payload, "minted_kinds": [], "supersessions": [],
                "errors": [], "warnings": [],
                "stats": {"llm": {"calls": 1, "retries": 0, "truncated": 0}},
                "error_census": {}}

    monkeypatch.setattr(ev2, "extract_session_v2", _fake_extract)


def _transient_entry(qid: str = "mini_ie_user_001", **overrides) -> dict:
    entry = {
        "question_id": qid,
        "question_type": "single-session-user",
        "error": "network:TimeoutError('stall')",
        "error_class": "ingest:retries_exhausted",
        "retryable": True,
        "attempts": 0,
        "failed_at_utc": "2026-08-27T02:10:34.088+00:00",
        "in_progress": None,
    }
    entry.update(overrides)
    return entry


def _resume_fingerprint() -> dict:
    """The exact fingerprint run_evaluation computes for the resume runs in
    this file (extractor_model=None, max_retries=0, ingest_mode=v2) — a
    hand-written checkpoint must carry it or the load refuses as stale."""
    rr = runner._resolve_rerank(rerank=None, rerank_model=None,
                                rerank_pool=None, per_session_cap=None,
                                mmr_lambda=None, max_k=5)
    return runner._build_fingerprint(
        reader_model="mock-reader", judge_model="mock-judge",
        ks=(5,), top_k=5, split="s", ingest_mode="v2",
        extractor_model=None, max_retries=0,
        dataset_fingerprint="unknown", rerank_config=rr["config"],
        context_item_cap=runner._env_int("TORTOISE_LME_CONTEXT_ITEMS",
                                         runner.DEFAULT_CONTEXT_ITEM_CAP),
        evidence_boost=False, evidence_boost_verbatim=None,
        evidence_boost_source=None,
        max_chunks_per_session=runner._env_int(
            "TORTOISE_LME_MAX_CHUNKS_PER_SESSION",
            runner.DEFAULT_MAX_CHUNKS_PER_SESSION))


def _write_checkpoint(path, *, outcomes=None, failures=None):
    Path(path).write_text(json.dumps({
        "format": runner.CHECKPOINT_FORMAT,
        "run_key": "embedded__hybrid__default__default",
        "surface": "embedded",
        "retriever": "hybrid",
        "model": "default",
        "prompt": "default",
        "fingerprint": _resume_fingerprint(),
        "outcomes": outcomes or [],
        "failures": failures or [],
        "updated_at_utc": "2026-08-27T02:10:34.088+00:00",
    }), encoding="utf-8")


def _write_fault(monkeypatch):
    """Persistent write-statement fault (reads + the SDK health probe pass)."""
    def _flaky(self, cypher, params=None, timeout=None):
        is_probe = ("WHERE n.id IN $ids" in cypher
                    or "WHERE p.id IN $ids" in cypher)
        if not is_probe and any(kw in cypher.upper()
                                for kw in ("CREATE", "MERGE", "SET")):
            raise redis_exc.TimeoutError("simulated stall")
        return _ORIGINAL_QUERY(self, cypher, params=params, timeout=timeout)

    monkeypatch.setattr(proj_mod._GuardedGraph, "query", _flaky)


def _run(tmp_path, monkeypatch, *, retry_failed=False, fail_writes=False,
         judge_boom=False, reader_boom=False, max_retries=0):
    """One mini run with the resume-test rig. Returns (outcomes, report,
    cp_path, sdk_creations). The query patch is RE-INSTALLED per call (the
    monkeypatch persists across _run calls within one test — a later
    fail_writes=False run must restore the original)."""
    monkeypatch.setattr(runner.random, "uniform", lambda a, b: 0.0)
    monkeypatch.setattr(runner.time, "sleep", lambda _s: None)
    if fail_writes:
        _write_fault(monkeypatch)
    else:
        monkeypatch.setattr(proj_mod._GuardedGraph, "query", _ORIGINAL_QUERY)
    reader = MockReader()
    if reader_boom:
        def _rb(**kw):
            import requests
            raise requests.exceptions.Timeout("reader transient")
        reader.answer = _rb  # type: ignore[method-assign]
    judge = MockJudge()
    if judge_boom:
        def _jb(**kw):
            import requests
            raise requests.exceptions.Timeout("judge transient")
        judge.judge = _jb  # type: ignore[method-assign]
    sdk_creations = {"n": 0}
    real_make = runner._make_question_sdk

    def _counting_make(*a, **k):
        sdk_creations["n"] += 1
        # #1988: shared embedded server (see the module note above).
        if k.get("db_uri") is None:
            return _shared_embedded_sdk(), _noop_close
        return real_make(*a, **k)

    monkeypatch.setattr(runner, "_make_question_sdk", _counting_make)
    cp = tmp_path / "cp.json"
    _reset_shared_graph(_mini()[:1])  # #1988: fresh qid namespace per run
    outcomes, report = runner.run_evaluation(
        _mini()[:1], reader=reader, judge=judge, ks=(5,), top_k=5,
        split="s", work_dir=str(tmp_path), checkpoint=str(cp),
        ingest_mode="v2", extractor_model=None, retry_failed=retry_failed,
        max_retries=max_retries)
    return outcomes, report, cp, sdk_creations


# ── Task 2 test (a): merge semantics + the atomic single-write ─────────────


def test_save_checkpoint_remove_failures_atomic(tmp_path):
    """P2-10: _save_checkpoint(remove_failures=[qid]) prunes the failure
    entry IN THE SAME flocked write as the outcome save — no separate
    read-delete-write window (a kill -9 between the two would leave a stale
    failure entry for a completed qid → the next resume re-burns it)."""
    cp = tmp_path / "cp.json"
    _write_checkpoint(cp, failures=[_transient_entry()])
    outcome = {"question_id": "mini_ie_user_001", "label": True}
    runner._save_checkpoint(str(cp), [outcome], [], _resume_fingerprint(),
                            remove_failures=["mini_ie_user_001"])
    saved = json.loads(cp.read_text(encoding="utf-8"))
    assert saved["failures"] == []
    assert saved["outcomes"][0]["question_id"] == "mini_ie_user_001"
    # no stale .tmp artifact
    assert not list(tmp_path.glob("*.tmp"))


def test_merge_checkpoint_reconciles_stamp_and_attempts(tmp_path):
    """P2-1: the read-through merge preserves a live in_progress claim stamp
    on the disk base (a stale unstamped in-memory copy must not erase the
    CAS protection) and never regresses attempts below the disk value."""
    cp = tmp_path / "cp.json"
    _write_checkpoint(cp, failures=[_transient_entry(attempts=2)])
    mem_entry = _transient_entry(attempts=1, in_progress=None)
    _merged_out, merged_fail = runner._merge_checkpoint(
        Path(cp), [], [mem_entry])
    assert len(merged_fail) == 1
    assert merged_fail[0]["attempts"] == 2  # disk max wins (never regressed)

    # LIVE-pid stamp must survive a stale unstamped in-memory copy
    live_entry = _transient_entry(attempts=1, in_progress={
        "in_progress_utc": "2026-08-27T02:10:34.088+00:00",
        "pid": os.getpid(),
    })
    _write_checkpoint(cp, failures=[live_entry])
    _merged_out, merged_fail = runner._merge_checkpoint(
        Path(cp), [], [_transient_entry(attempts=1, in_progress=None)])
    assert merged_fail[0]["in_progress"] == live_entry["in_progress"]

    # tombstone: remove_failures drops the qid even when only on disk
    _write_checkpoint(cp, failures=[_transient_entry()])
    _merged_out, merged_fail = runner._merge_checkpoint(
        Path(cp), [], [], remove_failures=["mini_ie_user_001"])
    assert merged_fail == []


# ── Task 2 test (c): the resume gate — retryable field + legacy rescue ─────


def test_retry_failed_gate_retryable_field_and_legacy_rescue():
    """P1-5/P1-F: retryable=True entries are eligible; retryable=False
    (deterministic bugs) never; legacy entries (no retryable field) use the
    repr-match rescue — matching repr → eligible, non-matching → skipped with
    a reason; near-miss reprs (substring embedded mid-repr) never re-attempt
    a retryable=False entry."""
    assert runner._retry_failed_skip_reason(_transient_entry()) is None
    assert runner._retry_failed_skip_reason(
        _transient_entry(retryable=False)) is not None
    # legacy entries without the additive fields
    legacy_match = _transient_entry()
    del legacy_match["retryable"]
    del legacy_match["attempts"]
    del legacy_match["in_progress"]
    assert runner._retry_failed_skip_reason(legacy_match) is None  # repr rescue
    legacy_nomatch = dict(legacy_match, error="KeyError('missing')")
    assert runner._retry_failed_skip_reason(legacy_nomatch) is not None
    # near-miss: the substring embedded mid-repr is only a LEGACY rescue —
    # a structured retryable=False entry stays excluded even when the repr
    # happens to contain the class name.
    assert runner._retry_failed_skip_reason(_transient_entry(
        retryable=False, error="not-a-TimeoutError-but-contains-the-word")) \
        is not None
    # attempts at/over the cap is excluded with a remediation reason
    assert runner._retry_failed_skip_reason(
        _transient_entry(attempts=runner.RESUME_ATTEMPTS_CAP)) is not None
    # non-recovery-tier classes are excluded (P2-G surface)
    assert runner._retry_failed_skip_reason(_transient_entry(
        error_class="reader:retries_exhausted")) is not None


# ── Task 2 test (b)/(d)/(m)/(n): end-to-end resume + counters ──────────────


def test_retry_failed_resume_success_removes_entry(tmp_path, monkeypatch):
    """Task 2 test (b): a checkpoint with one transient-failed qid →
    --retry-failed resume → the qid re-attempts and completes → the failure
    entry is removed in one flocked write → report n_failed == 0 and the qid
    grades clean (never double-counted)."""
    # seed the transient failure with a REAL prior failing run (write-stage
    # exhaustion → R2 fires and fails → entry attempts=1)
    _outcomes, _report, cp, _ = _run(tmp_path, monkeypatch, fail_writes=True)
    seeded = json.loads(cp.read_text(encoding="utf-8"))
    assert len(seeded["failures"]) == 1
    assert seeded["failures"][0]["attempts"] == 1
    assert seeded["failures"][0]["error_class"] == "ingest:retries_exhausted"

    outcomes, report, cp, creations = _run(tmp_path, monkeypatch,
                                           retry_failed=True)
    assert len(outcomes) == 1
    assert outcomes[0]["question_id"] == "mini_ie_user_001"
    assert outcomes[0]["whole_question_retries"] == 0  # resume re-attempt
    assert report["n_failed"] == 0
    saved = json.loads(cp.read_text(encoding="utf-8"))
    assert saved["failures"] == []  # remove-on-success (one flocked write)
    assert len(saved["outcomes"]) == 1  # the outcome was saved once
    # methodology records the resume mode
    assert report["methodology"]["retry_failed"] is True
    # the re-attempt ran the pipeline exactly once (no resume-internal R2)
    assert creations["n"] == 1


def test_resume_reattempt_r2_suppressed(tmp_path, monkeypatch):
    """Task 2 test (b) variant / P1-1: during the --retry-failed re-attempt a
    write-stage exhaustion must NOT fire R2 (the marker is DISARMED) — the
    re-attempt fails with the entry retained (retryable=True, attempts
    incremented, class preserved) and exactly ONE pipeline pass."""
    _outcomes, _report, cp, _ = _run(tmp_path, monkeypatch, fail_writes=True)
    seeded = json.loads(cp.read_text(encoding="utf-8"))
    assert seeded["failures"][0]["attempts"] == 1

    outcomes, _report, cp, creations = _run(tmp_path, monkeypatch,
                                           retry_failed=True, fail_writes=True)
    assert outcomes == []
    saved = json.loads(cp.read_text(encoding="utf-8"))
    assert len(saved["failures"]) == 1
    entry = saved["failures"][0]
    assert entry["attempts"] == 2  # one failed re-attempt increment
    assert entry["error_class"] == "ingest:retries_exhausted"  # preserved
    assert entry["retryable"] is True
    # the resume re-attempt got exactly ONE pipeline pass — a disarm bug
    # would have launched a resume-internal R2 (creations == 2)
    assert creations["n"] == 1


def test_failure_counter_transitions_and_gate_refusal(tmp_path, monkeypatch):
    """Task 2 test (d) + P1-C: R2-failed entry starts at attempts=1; a failed
    resume re-attempt increments to 2; the gate then refuses (2 < 2 is False)
    and the entry is retained at cap with the preserved class + a load-time
    WARNING (never a silent skip)."""
    _outcomes, _report, cp, _ = _run(tmp_path, monkeypatch, fail_writes=True)
    outcomes, _report2, cp, _creations = _run(tmp_path, monkeypatch,
                                              retry_failed=True, fail_writes=True)
    assert outcomes == []
    saved = json.loads(cp.read_text(encoding="utf-8"))
    entry = saved["failures"][0]
    assert entry["attempts"] == 2
    assert entry["error_class"] == "ingest:retries_exhausted"
    # the gate now refuses: a third run skips the qid (attempts at cap)
    outcomes2, report2, _cp2, _c2 = _run(tmp_path, monkeypatch,
                                         retry_failed=True)
    assert outcomes2 == []
    assert report2["n_failed"] == 1


def test_resume_fails_at_judge_class_preserved_at_cap(tmp_path, monkeypatch):
    """Task 2 test (n): a resume re-attempt admitted under the preserved
    class fails at the JUDGE stage → the counter increments to 2 → the gate
    refuses the next run; the entry keeps ingest:retries_exhausted (the tier
    is entered once and held until success or the cap)."""
    _outcomes, _report, cp, _ = _run(tmp_path, monkeypatch, fail_writes=True)
    outcomes, _report2, cp, _creations = _run(tmp_path, monkeypatch,
                                              retry_failed=True, judge_boom=True)
    assert outcomes == []
    saved = json.loads(cp.read_text(encoding="utf-8"))
    entry = saved["failures"][0]
    assert entry["attempts"] == 2
    assert entry["error_class"] == "ingest:retries_exhausted"  # preserved
    assert entry["retryable"] is True  # reflects the live judge transient
    # live inner repr (requests.Timeout reprs as Timeout(...) — the class
    # name, not TimeoutError; P1-5 documents this is why the structured
    # retryable field is authoritative for new entries)
    assert "Timeout('judge transient')" in entry["error"]
    assert _creations["n"] == 1  # no resume-internal R2


def test_retry_failed_off_warns_and_skips(tmp_path, monkeypatch, capsys):
    """Task 2 Step 1: WITHOUT --retry-failed, recoverable-class failures are
    skipped with the load-time advisory warning — never silently."""
    _outcomes, _report, _cp, _ = _run(tmp_path, monkeypatch, fail_writes=True)
    outcomes, report, _cp2, _ = _run(tmp_path, monkeypatch, retry_failed=False)
    assert outcomes == []
    assert "recoverable" in capsys.readouterr().err.lower()
    assert report["n_failed"] == 1


def test_class_excluded_entry_warns(tmp_path, monkeypatch, capsys):
    """Task 2 test (o): a genuinely non-recovery-tier entry (initial-attempt
    reader:retries_exhausted) is skipped WITH the load-time WARNING naming
    the qid — never a silent skip."""
    cp = tmp_path / "cp.json"
    _write_checkpoint(cp, failures=[_transient_entry(
        error_class="reader:retries_exhausted")])
    outcomes, report, _cp, _ = _run(tmp_path, monkeypatch, retry_failed=True)
    assert outcomes == []
    assert "reader:retries_exhausted" in capsys.readouterr().err.lower()
    assert report["n_failed"] == 1


# ── Task 2 test (i): corrupt-checkpoint quarantine + schema validation ─────


def test_corrupt_checkpoint_quarantine_and_refuse(tmp_path):
    """P2-12: a JSONDecodeError checkpoint is quarantined (guarded rename,
    new timestamped name when one already exists) and the loader REFUSES with
    an actionable error — the failures list + fingerprint are never silently
    discarded (the old silent fresh-start contract is gone)."""
    cp = tmp_path / "cp.json"
    cp.write_text("{not json!!", encoding="utf-8")
    with pytest.raises(runner.CheckpointStaleError) as ei:
        runner._load_checkpoint(str(cp))
    assert "quarantined" in str(ei.value)
    assert not cp.exists()
    q1 = list(tmp_path.glob("cp.json.corrupt.*"))
    assert len(q1) == 1

    # second corruption → a NEW timestamped quarantine file, first untouched
    cp.write_text("{still not json", encoding="utf-8")
    with pytest.raises(runner.CheckpointStaleError):
        runner._load_checkpoint(str(cp))
    q2 = list(tmp_path.glob("cp.json.corrupt.*"))
    assert len(q2) == 2
    assert not list(tmp_path.glob("*.tmp"))


def test_schema_validation_cases(tmp_path):
    """P2-10/P2-3: valid-JSON-wrong-shape checkpoints REFUSE (never an
    unhandled crash); legacy entries (missing optional keys, failed_at_utc
    present) PASS — only PRESENT keys are type-checked (P2-2)."""
    cp = tmp_path / "cp.json"

    _write_checkpoint(cp, failures=[_transient_entry(attempts="2")])
    with pytest.raises(runner.CheckpointStaleError):
        runner._load_checkpoint(str(cp))

    _write_checkpoint(cp, failures=["not-a-dict"])
    with pytest.raises(runner.CheckpointStaleError):
        runner._load_checkpoint(str(cp))

    _write_checkpoint(cp, failures=[_transient_entry(in_progress={
        "in_progress_utc": "not-a-date", "pid": 123})])
    with pytest.raises(runner.CheckpointStaleError):
        runner._load_checkpoint(str(cp))

    _write_checkpoint(cp, failures=[_transient_entry(in_progress={
        "in_progress_utc": "2026-08-27T02:10:34+00:00", "pid": "abc"})])
    with pytest.raises(runner.CheckpointStaleError):
        runner._load_checkpoint(str(cp))

    # a boolean pid is NOT an int (isinstance(True, int) is True) — refuse
    # so os.kill(True, 0) can never probe pid 1
    _write_checkpoint(cp, failures=[_transient_entry(in_progress={
        "in_progress_utc": "2026-08-27T02:10:34+00:00", "pid": True})])
    with pytest.raises(runner.CheckpointStaleError):
        runner._load_checkpoint(str(cp))

    # legacy-shaped entry passes (missing retryable/attempts/in_progress)
    legacy = _transient_entry()
    del legacy["retryable"]
    del legacy["attempts"]
    del legacy["in_progress"]
    _write_checkpoint(cp, failures=[legacy])
    _done, failures = runner._load_checkpoint(str(cp))
    assert failures == [legacy]


# ── Task 2 Step 7: the flocked claim CAS (liveness + TTL clock-advance) ────


def test_claim_cas_liveness_and_ttl(tmp_path):
    """P1-3/P1-4/P2-3: a claim writes the full entry + in_progress stamp; an
    ALIVE stamp is never stolen; a DEAD pid is claimable immediately; a live
    pid past the TTL is reclaimed (clock-advance seam); a pid-reuse-looking
    live stamp is refused below the TTL and reclaimed once aged past it."""
    cp = tmp_path / "cp.json"
    now = datetime(2026, 8, 27, 2, 0, 0, tzinfo=UTC)
    # an old live-pid stamp (the test process is alive) — below the TTL
    old_live_stamp = {
        "in_progress_utc": (now - timedelta(minutes=70)).isoformat(),
        "pid": os.getpid(),
    }
    _write_checkpoint(cp, failures=[_transient_entry(
        in_progress=old_live_stamp)])

    # below the 90-min TTL: a live-pid stamp is NEVER claimable
    assert runner._claim_reattempt(str(cp), "mini_ie_user_001",
                                   runner.RESUME_ATTEMPTS_CAP, now=now) is False

    # aged past the TTL: the age branch fires → the claim IS taken
    assert runner._claim_reattempt(str(cp), "mini_ie_user_001",
                                   runner.RESUME_ATTEMPTS_CAP,
                                   now=now + timedelta(minutes=91)) is True
    saved = json.loads(cp.read_text(encoding="utf-8"))
    entry = saved["failures"][0]
    # the full entry is preserved (never a wholesale {qid, stamp} replacement)
    assert entry["error_class"] == "ingest:retries_exhausted"
    assert entry["retryable"] is True
    assert entry["attempts"] == 0
    assert entry["in_progress"]["pid"] == os.getpid()

    # a DEAD pid's stamp is claimable immediately (PID-liveness primary)
    dead_stamp = {
        "in_progress_utc": (now - timedelta(minutes=1)).isoformat(),
        "pid": 42424242,
    }
    _write_checkpoint(cp, failures=[_transient_entry(in_progress=dead_stamp)])
    assert runner._claim_reattempt(str(cp), "mini_ie_user_001",
                                   runner.RESUME_ATTEMPTS_CAP, now=now) is True

    # an ineligible entry (attempts at cap) is never claimed
    _write_checkpoint(cp, failures=[_transient_entry(
        attempts=runner.RESUME_ATTEMPTS_CAP)])
    assert runner._claim_reattempt(str(cp), "mini_ie_user_001",
                                   runner.RESUME_ATTEMPTS_CAP, now=now) is False


def test_claim_rejects_live_claim_second_process(tmp_path):
    """P1-K: once claimed (live stamp), a second claim is refused — the flock
    serializes the CAS and the loser sees the winner's stamp."""
    cp = tmp_path / "cp.json"
    _write_checkpoint(cp, failures=[_transient_entry()])
    assert runner._claim_reattempt(str(cp), "mini_ie_user_001",
                                   runner.RESUME_ATTEMPTS_CAP) is True
    # the current process is alive → the stamp is live → refused
    assert runner._claim_reattempt(str(cp), "mini_ie_user_001",
                                   runner.RESUME_ATTEMPTS_CAP) is False


# ── Task 2 Step 4: failure-entry truncation + upsert single-write ──────────


def test_failure_entry_repr_truncation():
    """P2-7: a multi-MB inner exception repr is bounded (≤ 2000 chars +
    …<truncated>) and the checkpoint round-trips cleanly."""
    import requests

    big = requests.exceptions.Timeout("x" * 5000)
    entry = runner._failure_entry("q1", "single-session-user", big,
                                  stage="ingest", attempts=0)
    assert entry["retryable"] is True
    assert len(entry["error"]) <= runner.ERROR_REPR_CAP + len("…<truncated>")
    assert entry["error"].endswith("…<truncated>")
    json.dumps(entry)  # JSON round-trip stays parseable


def test_upsert_failure_replaces_never_appends(tmp_path):
    """P1-C: the append-site replaces any prior entry for the qid (never a
    duplicate) and increments attempts from the ON-DISK prior."""
    cp = tmp_path / "cp.json"
    _write_checkpoint(cp, failures=[_transient_entry(attempts=1)])
    entry = runner._upsert_failure(
        str(cp), "mini_ie_user_001",
        lambda prior: _transient_entry(attempts=int(prior["attempts"]) + 1))
    assert entry["attempts"] == 2
    saved = json.loads(cp.read_text(encoding="utf-8"))
    assert len(saved["failures"]) == 1  # replaced, never appended
    assert saved["failures"][0]["attempts"] == 2


def test_upsert_failure_fresh_file_writes_full_shape(tmp_path):
    """code-review F6: the fresh-file branch (no checkpoint exists yet) must
    emit the FULL checkpoint key set (format/run_key/surface/retriever/
    model/prompt/fingerprint) so a kill -9 before the trailing save cannot
    leave a markerless file the next resume refuses wholesale."""
    cp = tmp_path / "cp.json"
    fp = _resume_fingerprint()
    entry = runner._upsert_failure(
        str(cp), "mini_ie_user_001",
        lambda _prior: _transient_entry(),
        fingerprint=fp, run_key="embedded__hybrid__default__default",
        surface="embedded", retriever="hybrid", model="default",
        prompt="default")
    assert entry["question_id"] == "mini_ie_user_001"
    saved = json.loads(cp.read_text(encoding="utf-8"))
    assert saved["format"] == runner.CHECKPOINT_FORMAT
    assert saved["run_key"] == "embedded__hybrid__default__default"
    assert saved["fingerprint"] == fp
    assert saved["failures"][0]["question_id"] == "mini_ie_user_001"
    # the full shape passes the loader (no "predates the fingerprint" refuse)
    _done, failures = runner._load_checkpoint(str(cp))
    assert [f["question_id"] for f in failures] == ["mini_ie_user_001"]


# ── code-review F1/F2: resume limiter + TTL-aware advisory fast path ────────


class _TrackingLimiter:
    """Wraps a real BoundedSemaphore(2) with concurrency counters so a test
    can assert the resume re-attempt path actually acquires AND that the cap
    holds (≤ 2 in-flight at any sampled instant)."""

    def __init__(self, n: int = 2):
        self._sem = threading.BoundedSemaphore(n)
        self._lock = threading.Lock()
        self.held = 0
        self.max_held = 0
        self.acquire_count = 0
        self.release_count = 0

    def acquire(self):
        self._sem.acquire()
        with self._lock:
            self.held += 1
            self.max_held = max(self.max_held, self.held)
            self.acquire_count += 1

    def release(self):
        with self._lock:
            self.held -= 1
            self.release_count += 1
        self._sem.release()


def test_resume_reattempt_limiter_caps_inflight(tmp_path, monkeypatch):
    """Task 2 Step 7 / P2-1: a multi-entry failures list + --retry-failed
    dispatches through the shared _REINGEST_LIMITER — the semaphore-wait
    counter test pins (a) each re-attempt ACQUIRES the limiter and (b) ≤ 2
    re-attempts are in-flight at any sampled instant (the retry-amplification
    bound, AWS REL05-BP03)."""
    entries = [
        _transient_entry(qid="mini_ie_user_001"),
        _transient_entry(qid="mini_msr_002"),
        _transient_entry(qid="mini_tr_003"),
        _transient_entry(qid="mini_ku_004"),
    ]
    cp = tmp_path / "cp.json"
    _write_checkpoint(cp, failures=entries)

    tracking = _TrackingLimiter()
    monkeypatch.setattr(runner, "_REINGEST_LIMITER", tracking)
    monkeypatch.setattr(runner.random, "uniform", lambda a, b: 0.0)
    monkeypatch.setattr(runner.time, "sleep", lambda _s: None)

    # make each re-attempt overlap: a small REAL delay (threading.Event.wait,
    # unaffected by the run's time.sleep no-op patch) inside the pipeline so
    # 4 workers contend on the limiter rather than serializing.
    real_make = runner._make_question_sdk

    def _slow_make(*a, **k):
        threading.Event().wait(0.05)
        # #1988: shared embedded server (see the module note above).
        if k.get("db_uri") is None:
            return _shared_embedded_sdk(), _noop_close
        return real_make(*a, **k)

    monkeypatch.setattr(runner, "_make_question_sdk", _slow_make)

    _reset_shared_graph(_mini()[:4])  # #1988: fresh qid namespace per run
    reader = MockReader()
    judge = MockJudge()
    outcomes, report = runner.run_evaluation(
        _mini()[:4], reader=reader, judge=judge, ks=(5,), top_k=5,
        split="s", work_dir=str(tmp_path), checkpoint=str(cp),
        ingest_mode="v2", extractor_model=None, retry_failed=True,
        max_retries=0, workers=4)
    assert len(outcomes) == 4  # all four re-attempts completed
    assert report["n_failed"] == 0
    assert tracking.acquire_count == 4  # each re-attempt acquired at claim
    assert tracking.release_count == 4  # each released on completion
    assert tracking.max_held <= 2  # the cap holds at any sampled instant
    assert tracking.max_held >= 1  # the limiter was actually exercised


def test_retry_failed_skip_reason_ttl_aware_stamp(monkeypatch):
    """code-review F2: the advisory fast path must NOT veto a claim the TTL
    age branch admits — a dead-pid stamp and a >90-min hung-live stamp both
    pass (eligible → None), while a live below-TTL stamp is skipped."""
    now = datetime(2026, 8, 27, 2, 0, 0, tzinfo=UTC)
    monkeypatch.setattr(runner, "_utc_now", lambda: now)

    # live pid (this process) aged PAST the TTL → claimable → NOT skipped
    old_live = _transient_entry(in_progress={
        "in_progress_utc": (now - timedelta(minutes=91)).isoformat(),
        "pid": os.getpid(),
    })
    assert runner._retry_failed_skip_reason(old_live) is None

    # a DEAD pid → claimable immediately (PID-liveness primary) → NOT skipped
    dead = _transient_entry(in_progress={
        "in_progress_utc": (now - timedelta(minutes=1)).isoformat(),
        "pid": 42424242,
    })
    assert runner._retry_failed_skip_reason(dead) is None

    # a LIVE pid below the TTL → NOT claimable → skipped
    live_recent = _transient_entry(in_progress={
        "in_progress_utc": (now - timedelta(minutes=1)).isoformat(),
        "pid": os.getpid(),
    })
    assert runner._retry_failed_skip_reason(live_recent) is not None
