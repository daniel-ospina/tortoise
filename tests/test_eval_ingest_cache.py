"""#2080 ingest-cache seam — deterministic tests.

The seam persists clean per-question v2 ingests under their (question,
model-run) namespace keyed by an INGEST fingerprint (extractor code version
+ extraction model id + extraction prompt mode + question content +
chunk_turns) and — when ``--cache-ingest`` is on and the fingerprint
matches — skips the LLM extraction (the ~4.5 min/question wall-clock
dominant phase of the LongMemEval-S eval) on later runs.

Hermetic (no server, no API): fingerprint stability + invalidation, the
pure hit/miss decision (marker-less partial graphs → miss), snapshot
restore/serialization, and the tri-state CLI/env resolution (fail-safe
OFF).

Docker lane (real FalkorDB — the namespace machinery + cross-RUN graph
persistence require it): cache-hit (extractor skipped, ``ingest_cached``
recorded), cache-miss (ingest + marker written + graph persists after the
run), stale fingerprint (re-ingest + marker refreshed), the --no-cache-ingest
default (byte-identical: always ingest + never markers), the --sweep-cache
hygiene escape hatch, and the marker-file peer guard (a live foreign run
marker refuses the cleanup AND the cache marker — a peer's mid-write graph
is never wiped, reused or cached). Mock reader/judge + a mocked
``extract_session_v2`` counting extractor calls — zero API spend.

Env-lane note: this module never mutates TORTOISE_DB_URI (the docker tests
pass ``db_uri=`` explicitly, mirroring test_integrity_gate_docker), so it
needs no DELIBERATE_URI_MUTATIONS entry.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import socket
import uuid
from datetime import UTC, datetime

import pytest

from tests._live_utils import live_uri
from tests.longmem_eval.test_vector_arm import _mini
from tools.longmem_eval import run as runner
from tools.longmem_eval.judge import MockJudge
from tools.longmem_eval.reader import MockReader
from tortoise.config import is_db_uri as _is_db_uri
from tortoise.sdk import TortoiseSDK

# ── docker-lane only: reads TORTOISE_DB_URI at import and constructs bare
# TortoiseSDK() (env-driven) in its helpers, so on a URI-less tier-2 leg it
# would exercise the embedded backend and mis-assert the cache lifecycle.
# The gate reads the RAW env, not `live_uri()`: `live_uri()` falls back to the
# docker default when the variable is unset OR empty (the #2815 lane contract),
# so it can never answer "is a live lane configured?".
if not _is_db_uri(os.environ.get("TORTOISE_DB_URI")):
    pytest.skip(
        "requires TORTOISE_DB_URI (docker-lane eval-ingest-cache; "
        "tier-2 embedded legs skip)",
        allow_module_level=True,
    )

# live_uri() applies the lane contract to the resolved value: CI's tier-2 leg
# exports TORTOISE_DB_URI="" (empty means unset — #2815), and the docker-lane
# default carries the password python-ci.yml's falkordb service requires
# (`--requirepass falkordb`); local passwordless instances can override.
DB_URI = live_uri()


def _falkordb_up() -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1.0)
        return s.connect_ex(("127.0.0.1", 6379)) == 0


class _StableModel:
    """Adapter-like model identity (``.id`` + ``.model_id``) whose
    ``_model_id`` fingerprint is stable across instances/processes — the
    real CLI path builds adapters with stable ids (M7 #1739); a bare
    object() would repr with a memory address and churn the fingerprint
    between the two runs of a cache test."""

    def __init__(self, tag: str = "mock"):
        self.id = self.model_id = f"mock-{tag}"


# ── hermetic: fingerprint + decision + snapshot + CLI resolution ────────────

def _q(question_id: str = "fq-1") -> dict:
    return {
        "question_id": question_id,
        "question": "who?",
        "answer": "x",
        "question_type": "single-session-user",
        "haystack_session_ids": ["s0"],
        "answer_session_ids": ["s0"],
        "haystack_dates": ["2025-01-01"],
        "haystack_sessions": [
            [{"role": "user", "content": "a"}, {"role": "assistant",
                                                "content": "b",
                                                "has_answer": True}],
        ],
    }


def test_ingest_fingerprint_stable_same_inputs():
    """(a) fingerprint stability: identical inputs → identical hash across
    invocations (deterministic — no repr/address)."""
    fp1 = runner.ingest_cache_fingerprint(
        question=_q(), extractor_model=_StableModel("deepseek"),
        code_hash="c" * 64, prompt_digest="p" * 16, chunk_turns=2)
    fp2 = runner.ingest_cache_fingerprint(
        question=_q(), extractor_model=_StableModel("deepseek"),
        code_hash="c" * 64, prompt_digest="p" * 16, chunk_turns=2)
    assert fp1 == fp2
    assert fp1 != "0" * 64


def test_ingest_fingerprint_sensitive_to_every_input(tmp_path):
    """(a) fingerprint invalidation: each dimension (extractor code version,
    extraction model, prompt, question id/content, chunk_turns) changes the
    hash — any extractor change auto-invalidates cached ingests."""
    base = runner.ingest_cache_fingerprint(
        question=_q(), extractor_model=_StableModel("deepseek"),
        code_hash="c" * 64, prompt_digest="p" * 16, chunk_turns=2)
    assert runner.ingest_cache_fingerprint(
        question=_q(), extractor_model=_StableModel("other"),
        code_hash="c" * 64, prompt_digest="p" * 16, chunk_turns=2) != base
    assert runner.ingest_cache_fingerprint(
        question=_q(), extractor_model=_StableModel("deepseek"),
        code_hash="d" * 64, prompt_digest="p" * 16, chunk_turns=2) != base
    assert runner.ingest_cache_fingerprint(
        question=_q(), extractor_model=_StableModel("deepseek"),
        code_hash="c" * 64, prompt_digest="q" * 16, chunk_turns=2) != base
    assert runner.ingest_cache_fingerprint(
        question=_q("fq-2"), extractor_model=_StableModel("deepseek"),
        code_hash="c" * 64, prompt_digest="p" * 16, chunk_turns=2) != base
    changed = _q()
    changed["haystack_sessions"][0].append(
        {"role": "user", "content": "a content revision"})
    assert runner.ingest_cache_fingerprint(
        question=changed, extractor_model=_StableModel("deepseek"),
        code_hash="c" * 64, prompt_digest="p" * 16, chunk_turns=2) != base
    assert runner.ingest_cache_fingerprint(
        question=_q(), extractor_model=_StableModel("deepseek"),
        code_hash="c" * 64, prompt_digest="p" * 16, chunk_turns=4) != base


def test_ingest_code_fingerprint_content_hash(tmp_path):
    """(a) the extractor code version is a CONTENT hash of the extractor
    pipeline files: same content → same hash, a byte change in either file
    → different hash (an extractor edit auto-invalidates every cached
    graph)."""
    a = tmp_path / "extractor_v2.py"
    b = tmp_path / "ingest_v2.py"
    a.write_text("S1 story summary\n", encoding="utf-8")
    b.write_text("phase A raw chunks\n", encoding="utf-8")
    h1 = runner.ingest_code_fingerprint(paths=(a, b))
    h2 = runner.ingest_code_fingerprint(paths=(a, b))
    assert h1 == h2
    b.write_text("phase A raw chunks + extra payload writer line\n",
                 encoding="utf-8")
    assert runner.ingest_code_fingerprint(paths=(a, b)) != h1


def test_ingest_code_fingerprint_real_files_deterministic():
    """(a) the LIVE repo files hash deterministically within the process
    (the value the runner reads at run start)."""
    assert runner.ingest_code_fingerprint() == \
        runner.ingest_code_fingerprint()


def test_cache_hit_decision_rules():
    """(b/c/d + design 4) pure hit/miss decision: a graph is cached+valid
    iff the stored marker carries the matching fingerprint AND content is
    present. A partial graph WITHOUT the marker (mid-write / crashed
    ingest) and a marker WITHOUT content are both MISSES → re-ingest."""
    fp = "f" * 64
    assert runner.cache_hit_decision(marker_fp=fp, graph_present=True,
                                     current_fp=fp) is True
    assert runner.cache_hit_decision(marker_fp=None, graph_present=True,
                                     current_fp=fp) is False  # mid-write
    assert runner.cache_hit_decision(marker_fp=fp, graph_present=False,
                                     current_fp=fp) is False  # bare marker
    assert runner.cache_hit_decision(marker_fp="0" * 64, graph_present=True,
                                     current_fp=fp) is False  # stale


def test_cache_snapshot_restore_zeroes_live_costs():
    """Cache-hit stats restore the stored graph-provenance counts (gate
    denominators + graph content) with THIS run's live costs zeroed — the
    extraction did not run here (llm calls / retries / write-stage retries
    are honestly 0; only clean ingests are ever cached, so errors are
    empty)."""
    stats = {
        "sessions": 2, "turns": 9, "chunks": 4, "points": 3,
        "evidence_points": 1, "errors": [],
        "llm": {"calls": 7, "retries": 2, "truncated": 0},
        "recovery": {"escalated": 1}, "ingest_retries": 1,
    }
    restored = runner._cache_restore_stats(runner._cache_snapshot(stats))
    assert restored["turns"] == 9 and restored["points"] == 3
    assert restored["llm"] == {"calls": 0, "retries": 0, "truncated": 0}
    assert restored["recovery"] == {}
    assert restored["ingest_retries"] == 0
    assert restored["errors"] == [] and restored["error_census"] == {}


def test_cache_snapshot_restore_corrupt_zeros_shape():
    """A corrupt marker snapshot restores to a defensive zeroed shape (not a
    crash, not random data): live costs are 0 and no gate denominator is
    fabricated — the question's integrity gate then flags census_error
    (fail-loud) and the graph is never cached under a bogus snapshot."""
    for bad in ("not-json{{", "[1, 2]", 42):
        restored = runner._cache_restore_stats(bad)
        assert "turns" not in restored and "points" not in restored
        assert restored["errors"] == [] and restored["llm"] == {
            "calls": 0, "retries": 0, "truncated": 0}
        assert restored["recovery"] == {} and restored["ingest_retries"] == 0


def test_cache_ingest_env_resolution(monkeypatch):
    """(e) fail-safe OFF: default / unset env / falsy env → disabled; the
    env enables only on 1/true/yes/on; an explicit CLI value wins over the
    env."""
    monkeypatch.delenv("TORTOISE_LME_CACHE_INGEST", raising=False)
    assert runner.cache_ingest_enabled(None) is False
    assert runner.cache_ingest_enabled(True) is True
    assert runner.cache_ingest_enabled(False) is False
    for truthy in ("1", "true", "TRUE", "yes", "on"):
        monkeypatch.setenv("TORTOISE_LME_CACHE_INGEST", truthy)
        assert runner.cache_ingest_enabled(None) is True
        assert runner.cache_ingest_enabled(False) is False  # CLI beats env
    monkeypatch.setenv("TORTOISE_LME_CACHE_INGEST", "0")
    assert runner.cache_ingest_enabled(None) is False
    monkeypatch.setenv("TORTOISE_LME_CACHE_INGEST", "garbage")
    assert runner.cache_ingest_enabled(None) is False


def test_cache_cli_parser_tristate():
    """The CLI exposes the tri-state pair --cache-ingest / --no-cache-ingest
    (None default so the env still applies) + --sweep-cache."""
    p = runner._build_parser()
    assert p.parse_args(["--cache-ingest"]).cache_ingest is True
    assert p.parse_args(["--no-cache-ingest"]).cache_ingest is False
    assert p.parse_args([]).cache_ingest is None
    assert p.parse_args(["--sweep-cache"]).sweep_cache is True
    assert p.parse_args([]).sweep_cache is False
    with pytest.raises(SystemExit):
        p.parse_args(["--cache-ingest", "--no-cache-ingest"])


# ── docker lane: cache lifecycle over the real namespace machinery ─────────

pytestmark = pytest.mark.skipif(
    not _falkordb_up(), reason="FalkorDB not reachable at docker://localhost:6379"
)


@pytest.fixture(autouse=True)
def _no_embedder(monkeypatch):
    """Pin the sparse-only retrieval path (deterministic): no EmbeddingModel
    → no vector leg / no breaker noise — the assertions (extractor call
    counts, markers, gate health) are embedding-independent."""
    from tortoise.embeddings import EmbeddingModel
    monkeypatch.setattr(EmbeddingModel, "get",
                        staticmethod(lambda load_timeout=None: None))


def _unique_model(tag: str) -> str:
    """Unique per-test model name → unique per-question graph namespace."""
    return f"icache-{tag}-{uuid.uuid4().hex[:8]}"


def _clean_question(namespace: str, qid: str) -> None:
    """Remove the test's own graph (content + marker) + drop the empty key."""
    sdk = TortoiseSDK(namespace=namespace)
    try:
        sdk._get_proj().g.query(
            "MATCH (n) WHERE n.lme_question_id = $q DETACH DELETE n",
            params={"q": qid})
    finally:
        sdk.close()
    with contextlib.suppress(Exception):
        TortoiseSDK(namespace=namespace)._get_proj().db.select_graph(
            f"team_{namespace}").delete()


def _fake_extract_factory(calls: list):
    """A mocked extract_session_v2 (deterministic payload, one statement
    point per session) recording every extraction call into ``calls``."""
    def _fake(model, conversation, **kw):
        calls.append(1)
        content = " ".join(t["content"] for t in conversation)
        pid = "pt_" + hashlib.sha256(content.encode()).hexdigest()[:12]
        return {"payload": {"entities": [], "events": [],
                            "points": [{"id": pid, "content": content,
                                        "pointKind": "statement"}],
                            "operators": []},
                "minted_kinds": [], "supersessions": [], "errors": [],
                "warnings": []}
    return _fake


def _run_one_question(monkeypatch, tmp_path, model: str, qid: str,
                      calls: list, *, cache_ingest: bool, sweep_cache=False,
                      tag: str = "mock", work_dir=None):
    """One run_evaluation over the single question on the docker lane with
    the mocked extractor (counts into ``calls``). Returns (outcomes, _)."""
    import tortoise.extractor_v2 as ev2
    monkeypatch.setattr(ev2, "extract_session_v2", _fake_extract_factory(calls))
    q = next(x for x in _mini() if x["question_id"] == qid)
    return runner.run_evaluation(
        [q], reader=MockReader(), judge=MockJudge(), ks=(5,), top_k=5,
        split="s", work_dir=str(work_dir or tmp_path),
        ingest_mode="v2", db_uri=DB_URI, model=model,
        dataset_fingerprint="icache-test",
        cache_ingest=cache_ingest, sweep_cache=sweep_cache,
        extractor_model=_StableModel(tag))


def _marker_rows(namespace: str) -> list:
    sdk = TortoiseSDK(namespace=namespace)
    try:
        return sdk._get_proj().g.query(
            "MATCH (m:lme_ingest_cache {namespace:$ns}) RETURN m.fp, m.snapshot",
            params={"ns": namespace}).result_set
    finally:
        sdk.close()


def test_cache_miss_ingests_writes_marker_and_persists(monkeypatch, tmp_path):
    """(c) cache-miss path: an absent graph ingests (extractor called), the
    fingerprint marker is written, and the graph PERSISTS after the run
    (no end-of-question cleanup — it IS the cache)."""
    qid = _mini()[0]["question_id"]
    model = _unique_model("miss")
    ns = runner.question_graph_namespace(model, None, qid)
    try:
        calls: list = []
        outcomes, _ = _run_one_question(
            monkeypatch, tmp_path, model, qid, calls, cache_ingest=True)
        assert len(calls) >= 1
        assert outcomes[0]["valid"] is True
        assert outcomes[0]["gate_reasons"] == []
        assert outcomes[0].get("ingest_cached") is False
        rows = _marker_rows(ns)
        assert len(rows) == 1  # marker written
        assert rows[0][0] == runner.ingest_cache_fingerprint(
            question=next(x for x in _mini() if x["question_id"] == qid),
            extractor_model=_StableModel("mock"),
            code_hash=runner.ingest_code_fingerprint(),
            prompt_digest=runner.extractor_prompt_digest(), chunk_turns=2)
        # the graph is still there AFTER the run (persisted as the cache)
        sdk = TortoiseSDK(namespace=ns)
        try:
            pts = sdk._get_proj().g.query(
                "MATCH (p:Point {lme_question_id:$q}) RETURN count(p)",
                params={"q": qid}).result_set
        finally:
            sdk.close()
        assert pts[0][0] > 0
    finally:
        _clean_question(ns, qid)


def test_cache_hit_skips_extraction(monkeypatch, tmp_path):
    """(b) cache-hit path: a pre-ingested + marked graph causes the ingest
    (extractor calls) to be SKIPPED on the matching second run and the
    outcome carries ingest_cached: true — with identical gate health and
    pool to the ingest run."""
    qid = _mini()[1]["question_id"]
    model = _unique_model("hit")
    ns = runner.question_graph_namespace(model, None, qid)
    try:
        calls1: list = []
        o1, _ = _run_one_question(monkeypatch, tmp_path, model, qid, calls1,
                                  cache_ingest=True)
        assert len(calls1) >= 1
        calls2: list = []
        o2, _ = _run_one_question(monkeypatch, tmp_path, model, qid, calls2,
                                  cache_ingest=True)
        assert len(calls2) == 0  # extraction SKIPPED — zero extractor calls
        assert o2[0].get("ingest_cached") is True
        assert o2[0]["valid"] is True
        assert o2[0]["gate_reasons"] == []
        assert o2[0]["post_retrieval_reasons"] == []
        assert o2[0]["pool_size"] == o1[0]["pool_size"]
        assert o2[0]["llm_calls"] == 0  # this run spent nothing
    finally:
        _clean_question(ns, qid)


def test_cache_stale_fingerprint_reen_ingests(monkeypatch, tmp_path):
    """(d) stale path: a graph carrying a DIFFERENT fingerprint re-ingests
    (the wipe removes the stale content + marker, the ingest refreshes the
    marker to the current fingerprint)."""
    qid = _mini()[2]["question_id"]
    model = _unique_model("stale")
    ns = runner.question_graph_namespace(model, None, qid)
    try:
        calls1: list = []
        o1, _ = _run_one_question(monkeypatch, tmp_path, model, qid, calls1,
                                  cache_ingest=True)
        assert len(calls1) >= 1
        # forge an OLD fingerprint on the marker (an extractor-code change
        # that postdates the cached graph) → the next run must re-extract
        sdk = TortoiseSDK(namespace=ns)
        try:
            sdk._get_proj().g.query(
                "MATCH (m:lme_ingest_cache {namespace:$ns}) "
                "SET m.fp = $fp", params={"ns": ns, "fp": "0" * 64})
        finally:
            sdk.close()
        calls2: list = []
        o2, _ = _run_one_question(monkeypatch, tmp_path, model, qid, calls2,
                                  cache_ingest=True)
        assert len(calls2) >= 1  # stale → re-ingest
        assert o2[0].get("ingest_cached") is False
        assert o2[0]["gate_reasons"] == []
        rows = _marker_rows(ns)
        assert len(rows) == 1
        assert rows[0][0] != "0" * 64  # marker refreshed to the CURRENT fp
        assert o2[0]["pool_size"] == o1[0]["pool_size"]
    finally:
        _clean_question(ns, qid)


def test_cache_off_default_always_ingests_and_cleans(monkeypatch, tmp_path):
    """(e) --no-cache-ingest default: byte-identical behavior to today —
    every run ingests (extractor called each time, no marker ever), a
    second run cleans the first's namespace at its question start, and the
    outcomes carry no ingest_cached key (outcome shape unchanged)."""
    qid = _mini()[3]["question_id"]
    model = _unique_model("default")
    ns = runner.question_graph_namespace(model, None, qid)
    try:
        calls1: list = []
        o1, _ = _run_one_question(monkeypatch, tmp_path, model, qid, calls1,
                                  cache_ingest=False)
        calls2: list = []
        o2, _ = _run_one_question(monkeypatch, tmp_path, model, qid, calls2,
                                  cache_ingest=False)
        assert len(calls1) >= 1 and len(calls2) >= 1  # always ingest
        assert "ingest_cached" not in o1[0]
        assert "ingest_cached" not in o2[0]
        assert _marker_rows(ns) == []  # never a marker
        assert o1[0]["pool_size"] == o2[0]["pool_size"]
        assert o1[0]["session_recall@k"] == o2[0]["session_recall@k"]
        assert o1[0]["turn_recall@k"] == o2[0]["turn_recall@k"]
    finally:
        _clean_question(ns, qid)


def test_sweep_cache_wipes_graph_after_use(monkeypatch, tmp_path):
    """(--sweep-cache hygiene escape hatch) the question's graph — content
    AND cache marker — is removed right after the question finishes."""
    qid = _mini()[0]["question_id"]
    model = _unique_model("sweep")
    ns = runner.question_graph_namespace(model, None, qid)
    try:
        calls: list = []
        outcomes, _ = _run_one_question(monkeypatch, tmp_path, model, qid,
                                        calls, cache_ingest=True,
                                        sweep_cache=True)
        assert len(calls) >= 1
        assert outcomes[0]["valid"] is True
        assert _marker_rows(ns) == []
        sdk = TortoiseSDK(namespace=ns)
        try:
            pts = sdk._get_proj().g.query(
                "MATCH (p:Point {lme_question_id:$q}) RETURN count(p)",
                params={"q": qid}).result_set
        finally:
            sdk.close()
        assert pts[0][0] == 0  # content gone
    finally:
        _clean_question(ns, qid)


def test_marker_peer_guard_hermetic_refusal(tmp_path):
    """(f) the marker-file peer guard still holds in the cache seam: a LIVE
    foreign run marker on the namespace makes the fresh-run cleanup REFUSE
    (a peer is mid-question — never clobber) and our marker write never
    overwrites the peer's."""
    import os as _os

    namespace = "icache-peer-hermetic"
    marker = runner._marker_file(str(tmp_path), namespace)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({
        "run_key": "peer-run", "pid": _os.getpid() + 1,
        "heartbeat_utc": datetime.now(UTC).isoformat(),
    }), encoding="utf-8")
    assert not runner._namespace_cleanup_allowed(
        str(tmp_path), namespace, "k")
    runner._write_run_marker(str(tmp_path), namespace, "k")
    data = json.loads(marker.read_text(encoding="utf-8"))
    assert data["pid"] == _os.getpid() + 1  # peer marker untouched


def test_marker_peer_guard_never_caches_a_live_peer_graph(
        monkeypatch, tmp_path):
    """(f) cache-mode MISS under a LIVE foreign run marker (a peer is
    mid-question on the namespace): the fresh-run cleanup REFUSES, so this
    process never owns a clean namespace — the ingest still runs (today's
    fallback semantics) but NO cache marker is written: a graph another
    process is actively writing is never wiped, reused OR cached, and the
    peer's marker file is never clobbered."""
    import os as _os

    qid = _mini()[1]["question_id"]
    model = _unique_model("peer")
    ns = runner.question_graph_namespace(model, None, qid)
    work_dir = tmp_path / "peer-wd"
    work_dir.mkdir(parents=True, exist_ok=True)
    # a LIVE foreign run marker (fresh heartbeat, foreign pid)
    marker = runner._marker_file(str(work_dir), ns)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({
        "run_key": "peer-run", "pid": _os.getpid() + 1,
        "heartbeat_utc": datetime.now(UTC).isoformat(),
    }), encoding="utf-8")
    try:
        calls: list = []
        outcomes, _ = _run_one_question(monkeypatch, tmp_path, model, qid,
                                        calls, cache_ingest=True,
                                        work_dir=str(work_dir))
        assert len(calls) >= 1  # the question still ran (miss + ingest)
        assert len(outcomes) == 1
        assert outcomes[0]["valid"] is True
        # the guard refused the wipe → we never owned a clean namespace →
        # the clean ingest is NOT persisted as a cache entry
        assert _marker_rows(ns) == []
        assert marker.exists()  # our write never clobbered the peer's marker
        data = json.loads(marker.read_text(encoding="utf-8"))
        assert data["pid"] == _os.getpid() + 1
    finally:
        _clean_question(ns, qid)
