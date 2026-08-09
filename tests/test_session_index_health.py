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


def test_ingest_continues_on_symlink_lock_path(env, sdk):
    """Robustness (#280 review P2): a planted symlink at one session's lock
    path must not abort the batch sweep — that file is skipped with a
    retryable error, the remaining files still index, and the symlink target
    is never truncated."""
    victim = env.parent / "victim.txt"
    victim.write_text("precious data")
    _write_session(env, "sess-bad")
    _write_session(env, "sess-good")
    # Plant a symlink at the lock path of sess-bad (attacker-controlled).
    lock = SessionIndexLock("sess-bad",
                            lock_dir=os.environ["TORTOISE_INDEX_LOCK_DIR"])
    lock.path.symlink_to(victim)

    rep = _ingest(sdk, env)  # must not raise / abort the batch
    assert rep["ingested"] == 1          # good file still indexed
    assert victim.read_text() == "precious data"  # never truncated
    bad = [e for e in rep["errors"] if "sess-bad" in e["file"]]
    assert len(bad) == 1 and bad[0]["retryable"] is True
    assert "lock" in bad[0]["error"].lower()


def test_ingest_continues_on_unusable_lock_dir(env, sdk, monkeypatch, tmp_path):
    """Robustness (#280 review P2): an unusable lock dir (EACCES/EROFS/
    EMFILE-class failure — here a non-directory blocking the path) must not
    abort the batch sweep: every file is recorded with a retryable error and
    the sweep continues (no traceback, exit-0 contract preserved)."""
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x")
    monkeypatch.setenv("TORTOISE_INDEX_LOCK_DIR", str(blocker / "locks"))
    _write_session(env, "sess-a")
    _write_session(env, "sess-b")

    rep = _ingest(sdk, env)
    assert rep["ingested"] == 0
    assert len(rep["errors"]) == 2
    assert all(e["retryable"] for e in rep["errors"])
    assert all("lock" in e["error"].lower() for e in rep["errors"])


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


# ── duplicate sessionIds (#280 review P2) ────────────────────────────


def test_falsy_session_id_coerces_to_match_ingest(env, sdk):
    """Review round 2 P2: sessionId: 0 / false are falsy raw but coercible —
    extract_session_id must str-coerce (like ingest) so health derives the
    SAME event_id and the sweep converges."""
    f = env / "falsy-zero.md"
    f.write_text("---\nsessionId: 0\ntitle: T\n---\nUser: hi\n")
    # Ingest coerces sessionId -> "0" → event_id session_0
    sdk.ingest_corpus(str(env), eventKind="AgentSession")
    h = sdk.session_index_health(str(env))
    assert h["unindexed"] == [], f"falsy sessionId not matched: {h}"
    assert h["duplicates"] == []
    # Second sweep converges (nothing to do)
    h2 = sdk.session_index_health(str(env))
    assert h2["up_to_date"], h2

def test_extract_session_id_utf8_under_c_locale(env, sdk):
    """Review round 10 P2: extract_session_id must read UTF-8 explicitly —
    under LC_ALL=C (cron/systemd) the locale default encoding would throw
    UnicodeDecodeError → None → a different event_id than ingest → permanent
    sweep non-convergence."""
    f = env / "utf8-sess.md"
    f.write_bytes("---\nsessionId: utf8-s\ntitle: caf\u00e9\n---\nUser: hi\n".encode("utf-8"))
    sdk.ingest_corpus(str(env), eventKind="AgentSession")
    h = sdk.session_index_health(str(env))
    assert h["unindexed"] == [], f"utf-8 session not matched: {h}"
    h2 = sdk.session_index_health(str(env))
    assert h2["up_to_date"], h2


def test_empty_session_id_with_alt_key_uses_alt(env, sdk):
    """Review round 4 P2: sessionId: "" + session_id: foo — the or-collapse
    on ingest uses session_id ("" is falsy); extract must mirror that and
    derive session_foo, not file_<stem>, or the sweep never converges."""
    f = env / "empty-alt.md"
    f.write_text('---\nsessionId: ""\nsession_id: foo\ntitle: T\n---\nUser: hi\n')
    sdk.ingest_corpus(str(env), eventKind="AgentSession")
    h = sdk.session_index_health(str(env))
    assert h["unindexed"] == [], f"alt-key not matched: {h}"
    h2 = sdk.session_index_health(str(env))
    assert h2["up_to_date"], h2


def test_empty_session_id_falls_back_to_file_stem(env, sdk):
    """Review round 3 P2: sessionId: "" must fall back to file_<stem> on BOTH
    sides (extract_session_id and ingest's or-collapse) — an empty string is
    absent, not a session id; otherwise health derives session_'' while
    ingest derives session_file_<stem> and the sweep never converges."""
    f = env / "empty-sid.md"
    f.write_text('---\nsessionId: ""\ntitle: T\n---\nUser: hi\n')
    sdk.ingest_corpus(str(env), eventKind="AgentSession")
    h = sdk.session_index_health(str(env))
    assert h["unindexed"] == [], f"empty sessionId not matched: {h}"
    assert h["duplicates"] == []
    # Second sweep converges
    h2 = sdk.session_index_health(str(env))
    assert h2["up_to_date"], h2


def test_falsy_boolean_session_id_coerces(env, sdk):
    f = env / "falsy-bool.md"
    f.write_text("---\nsessionId: false\ntitle: T\n---\nUser: hi\n")
    sdk.ingest_corpus(str(env), eventKind="AgentSession")
    h = sdk.session_index_health(str(env))
    assert h["unindexed"] == [], f"sessionId:false not matched: {h}"


def test_duplicate_session_files_surfaced_and_convergent(env, sdk):
    """Regression (#280 review P2): two corpus files sharing a sessionId used
    to make the sweep permanently non-convergent (MERGE is last-writer-wins;
    the losing copy is forever hash-stale, so every run re-merges both). Now
    the duplicates are surfaced in a `duplicates` bucket and ingest_corpus
    dedupes to one primary file per sessionId → the sweep converges."""
    env.joinpath("a.md").write_text(
        "---\nsessionId: dup-sess\ntitle: A\n---\nUser: hi\n")
    env.joinpath("b.md").write_text(
        "---\nsessionId: dup-sess\ntitle: B\n---\nUser: yo\n")

    h = sdk.session_index_health()
    assert len(h["duplicates"]) == 1
    d = h["duplicates"][0]
    assert d["session_id"] == "dup-sess"
    assert d["event_id"] == "session_dup-sess"
    assert len(d["files"]) == 2
    # Only the primary file drives the delta — one unindexed, not two
    assert len(h["unindexed"]) == 1

    rep1 = sdk.reconcile_sessions(str(env))
    assert rep1["reindex"]["ingested"] == 1   # one primary indexed
    assert rep1["reindex"]["skipped"] == 1    # non-primary copy deduped

    h2 = sdk.session_index_health()
    assert h2["matched"] == 1
    assert h2["unindexed"] == [] and h2["stale"] == []
    assert len(h2["duplicates"]) == 1  # still surfaced (corpus condition)

    # Second sweep: nothing to do — converged
    rep2 = sdk.reconcile_sessions(str(env))
    assert rep2["reindex"] == {}


def test_indexed_events_corpus_scoped(env, sdk):
    """Regression (#280 review P3): indexed_events counts events whose
    eventId matches a corpus file — NOT all AgentSession Events in the graph
    (a non-corpus session in the same graph would make the doctor arithmetic
    `fc files vs N Events` misleading)."""
    _write_session(env, "sess-a")
    _ingest(sdk, env)
    # A non-corpus AgentSession Event in the same graph (other project).
    sdk._get_proj().g.query(
        "CREATE (e:Event {eventId:'session_elsewhere', eventKind:'AgentSession'})"
    )
    h = sdk.session_index_health()
    assert h["indexed_events"] == 1   # corpus-scoped, not 2
    assert h["matched"] == 1
    assert h["unindexed"] == []
    assert h["up_to_date"] == [str(env / "sess-a.md")]


def test_session_indexer_cli_lock_error_exits_zero(monkeypatch, tmp_path, capsys):
    """Review follow-up P3: the single-file index CLI surfaces a lock-path
    OSError as {"status": "error"} and returns (exit 0) — never a traceback,
    preserving the hook exit-0 contract."""
    import json as _json
    import sys as _sys
    import tortoise.session_indexer as si

    f = tmp_path / "sess.md"
    f.write_text("---\nsessionId: cli-sess\ntitle: T\n---\nUser: hi\n")
    blocker = tmp_path / "blocker"
    blocker.write_text("x")
    monkeypatch.setenv("TORTOISE_INDEX_LOCK_DIR", str(blocker / "locks"))
    monkeypatch.setenv("TORTOISE_DB_URI", "docker://localhost:16379/x")

    class _FakeSDK:
        def __init__(self, *a, **k):
            pass
    monkeypatch.setattr("tortoise.sdk.TortoiseSDK", _FakeSDK)
    monkeypatch.setattr(_sys, "argv",
                        ["tortoise-index", "--no-llm", "--db",
                         "docker://localhost:16379/x", str(f)])

    si.main()  # must print JSON and return, not raise / sys.exit(1)
    payload = _json.loads(capsys.readouterr().out)
    assert payload["status"] == "error"
    assert "lock" in payload["reason"].lower()
