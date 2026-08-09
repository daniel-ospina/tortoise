"""Session-index health + reconciliation sweep (#280 items 2-3).

Covers: corpus-vs-graph delta computation (session_index_health), the
reconciliation sweep (reconcile_sessions — scan-then-replay, idempotent),
the doctor health-check surface, and the item-1 lock integration inside
ingest_corpus (a live per-session flock makes the batch writer skip the
file with a retryable error — no lost update).
"""
from __future__ import annotations

import os

import pytest

from tortoise.index_lock import SessionIndexLock
from tortoise.sdk import TortoiseSDK


@pytest.fixture
def env(monkeypatch, tmp_path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    locks = tmp_path / "locks"
    locks.mkdir()
    monkeypatch.setenv("TORTOISE_SESSION_CORPUS", str(corpus))
    monkeypatch.setenv("TORTOISE_INDEX_LOCK_DIR", str(locks))
    return corpus


@pytest.fixture
def sdk(tmp_path):
    s = TortoiseSDK(str(tmp_path / "health.db"))
    yield s
    s.close()


def _write_session(corpus, sid, body="User: hi\nAssistant: hello\n", title="T"):
    f = corpus / f"{sid}.md"
    f.write_text(f"---\nsessionId: {sid}\ntitle: {title}\n---\n{body}")
    return f


def _ingest(sdk, env, **kw):
    return sdk.ingest_corpus(str(env), eventKind="AgentSession",
                             extract_metadata=False, **kw)


# ── session_index_health (#280 item 2) ──────────────────────────────


def test_health_empty_corpus(env, sdk):
    h = sdk.session_index_health()
    assert h["file_count"] == 0
    assert h["unindexed"] == []
    assert h["indexed_events"] == 0
    assert h["matched"] == 0


def test_health_absent_corpus(monkeypatch, sdk, tmp_path):
    monkeypatch.setenv("TORTOISE_SESSION_CORPUS", str(tmp_path / "nope"))
    h = sdk.session_index_health()
    assert h["file_count"] == 0
    assert h["unindexed"] == []


def test_health_detects_unindexed(env, sdk):
    _write_session(env, "sess-a")
    _write_session(env, "sess-b")
    h = sdk.session_index_health()
    assert h["file_count"] == 2
    assert len(h["unindexed"]) == 2
    assert h["indexed_events"] == 0


def test_health_after_index_and_hash_drift(env, sdk):
    f = _write_session(env, "sess-a")
    _ingest(sdk, env)
    h = sdk.session_index_health()
    assert h["indexed_events"] == 1
    assert h["matched"] == 1
    assert h["unindexed"] == []
    # Change the file -> Event exists but hash differs -> stale
    f.write_text("---\nsessionId: sess-a\ntitle: T2\n---\nUser: changed\n")
    h2 = sdk.session_index_health()
    assert len(h2["stale"]) == 1
    assert h2["matched"] == 0


# ── reconcile_sessions (#280 item 3) ────────────────────────────────


def test_reconcile_indexes_missing(env, sdk):
    _write_session(env, "sess-a")
    _write_session(env, "sess-b")
    rep = sdk.reconcile_sessions(str(env))
    assert len(rep["unindexed"]) == 2
    assert rep["reindex"]["ingested"] == 2
    h = sdk.session_index_health()
    assert h["matched"] == 2


def test_reconcile_idempotent(env, sdk):
    _write_session(env, "sess-a")
    sdk.reconcile_sessions(str(env))
    rep2 = sdk.reconcile_sessions(str(env))
    assert rep2["unindexed"] == []
    assert rep2["reindex"] == {}  # nothing to do — dedup is the sweep


def test_reconcile_reindexes_stale(env, sdk):
    f = _write_session(env, "sess-a")
    sdk.reconcile_sessions(str(env))
    f.write_text("---\nsessionId: sess-a\ntitle: T9\n---\nUser: updated body\n")
    rep = sdk.reconcile_sessions(str(env))
    assert len(rep["stale"]) == 1
    assert rep["reindex"]["updated"] == 1


# ── item-1 lock integration in the batch writer ────────────────────


def test_ingest_skips_locked_session(env, sdk):
    """A live per-session flock makes ingest_corpus skip the file with a
    retryable error (concurrent hook writer owns it — no lost update)."""
    _write_session(env, "sess-lock")
    lock = SessionIndexLock("sess-lock",
                            lock_dir=os.environ["TORTOISE_INDEX_LOCK_DIR"])
    assert lock.acquire() == "acquired"
    try:
        rep = _ingest(sdk, env)
        assert rep["ingested"] == 0
        assert any("session lock held" in e["error"] for e in rep["errors"])
        assert all(e["retryable"] for e in rep["errors"])
    finally:
        lock.release()
    # After release the same file indexes fine
    rep2 = _ingest(sdk, env)
    assert rep2["ingested"] == 1


# ── doctor surface (#280 item 2) ────────────────────────────────────


def test_reconcile_converges_on_crlf_files(env, sdk):
    """Regression (#280 review P1): file hashing must be text-mode normalized
    so CRLF files are not perpetually hash-stale (sweep never converging)."""
    f = env / "crlf.md"
    f.write_bytes(b"---\r\nsessionId: crlf-1\r\ntitle: CRLF\r\n---\r\nUser: hi\r\nAssistant: world\r\n")
    rep1 = sdk.reconcile_sessions(str(env))
    assert rep1["reindex"]["ingested"] == 1
    # Health must see it as up-to-date, NOT stale (hash match with ingest)
    h = sdk.session_index_health()
    assert h["matched"] == 1
    assert h["stale"] == []
    # Second sweep: nothing to do — converged
    rep2 = sdk.reconcile_sessions(str(env))
    assert rep2["reindex"] == {}


def test_doctor_includes_session_indexing(env, capsys, monkeypatch):
    """doctor surfaces the session-indexing check (corpus empty → warn)."""
    from tortoise.__main__ import main

    rc = main(["doctor"])
    out = capsys.readouterr().out
    assert "Session indexing" in out
    assert "corpus empty" in out
    assert rc in (0, 1)
