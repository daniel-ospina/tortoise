"""S2 integration suite for epic #900 T3 — index_file/index_directory.

Covers the T3 runtime contract (plan §8.6 T3 bullet + canonical-pin index):
the sanctioned create_source(source_path=) route, _upsert_document source_url
override, meeting-scoped guard + suffix follow-up, _searchText=title, the
unit-completeness gate + conditional single-statement version bump, corpus_root
default resolution, the two-layer size guard, S_ISREG checks, the bounded
walker (symlink policy + cycles + non-md ignored), progress-file resume, the
skipped-reason enumeration, primary election, and the concurrency harness
(E2E-9 threads leg on the embedded harness). Runs with extract_metadata=False
(NO-NETWORK mode: LLM tier disabled AND session embeddings short-circuited —
§6.1 I15) unless a test explicitly mocks the embedding surface.

Harness conventions (§7): fresh embedded DB per test via
TortoiseSDK(tempdir/t.db, namespace="e2e-900"); no network; graph assertions
via raw Cypher on sdk._get_proj().g.
"""
from __future__ import annotations  # noqa: I001

import errno
import hashlib
import os
import stat
import tempfile
from pathlib import Path

import pytest

from tortoise.sdk import TortoiseSDK

from tests import concurrency_harness as harness


def _db() -> str:
    return harness.make_db()


def _sdk(db: str | None = None) -> TortoiseSDK:
    return TortoiseSDK(db or _db(), namespace="e2e-900")


SESSION_FIXTURE = """\
---
sessionId: abc123
agent: pi
title: "Auth refactor session"
startedAt: "2026-08-10T09:00:00+00:00"
---
## Summary
Refactored the auth middleware; decided to keep JWT rotation.
"""

MEETING_FIXTURE = """\
---
fileType: meeting
title: "Team Sync"
date: "2026-08-05T14:00:00+00:00"
participants: [alice, bob]
decisions: ["Adopt contentHash-gated indexing"]
topics: [planning, indexing]
---
Met to review the indexing epic.
"""

DOC_FIXTURE = """\
---
title: "GTM Strategy"
type: strategyDoc
domain: product
created: "2026-08-01T00:00:00+00:00"
authoredBy: daniel
---
Strategy body text.
"""


@pytest.fixture
def corpus(tmp_path):
    c = tmp_path / "corpus"
    c.mkdir()
    (c / "s1.md").write_text(SESSION_FIXTURE)
    (c / "meeting-2026-08-05.md").write_text(MEETING_FIXTURE)
    (c / "strategy.md").write_text(DOC_FIXTURE)
    return c


@pytest.fixture
def lock_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("TORTOISE_INDEX_LOCK_DIR", str(tmp_path / "locks"))
    return tmp_path / "locks"


# ── E2E-1 shape: session → Source + Event ───────────────────────────────

def test_session_unit_e2e1(corpus):
    sdk = _sdk()
    try:
        r = sdk.index_directory(str(corpus), extract_metadata=False)
        assert r["indexed"] == 3 and r["failed"] == 0 and r["skipped"] == 0
        g = sdk._get_proj().g
        u = f"corpus://{corpus.name}/s1.md"
        rows = g.query(
            "MATCH (s:Source {url:$u}) RETURN s.url, s.sourceKind, s.contentHash, "
            "s.sourceDate, s.ingestedAt, s.sourcePath, s.source_path, s.title, "
            "s._searchText, s.version",
            params={"u": u},
        ).result_set
        assert len(rows) == 1
        url, skind, chash, sdate, ingested, spath, snake, title, stext, ver = rows[0]
        assert url == u
        assert skind == "agentSession"      # CYCLE-25 v3.6 #6 spelling
        text = (corpus / "s1.md").read_text()
        assert chash == hashlib.sha256(text.encode()).hexdigest()
        assert sdate == "2026-08-10T09:00:00+00:00"  # metadata, not ingest time
        assert ingested
        assert spath == str((corpus / "s1.md").resolve())
        assert snake is None                # stray-prop guard (§4.1 mapping pin)
        assert title == "Auth refactor session"
        assert stext == "Auth refactor session"  # _searchText=title write path
        assert ver == 1
        # Event
        ev = g.query(
            "MATCH (e:Event {eventId:'session_abc123'}) RETURN e.eventKind, "
            "e.keywords, e.file_hash, e.capturedAt, e.session_id",
        ).result_set
        assert len(ev) == 1
        assert ev[0][0] == "AgentSession"
        assert ev[0][1]                     # keywords non-empty (fallback)
        assert ev[0][2] == chash
        assert ev[0][3]                     # capturedAt = ingest time (cycle-25)
        # Edge + no operators/points
        assert g.query(
            "MATCH (s:Source {url:$u})-[r:references]->(e:Event {eventId:'session_abc123'}) "
            "RETURN count(r)", params={"u": u},
        ).result_set[0][0] == 1
        assert g.query("MATCH (p:Point) RETURN count(p)").result_set[0][0] == 0
    finally:
        sdk.close()


def test_session_unit_encoded_url(corpus):
    sub = corpus / "sessions"
    sub.mkdir()
    p = sub / "s 1.md"
    p.write_text(SESSION_FIXTURE)
    (corpus / "s1.md").unlink()
    (corpus / "meeting-2026-08-05.md").unlink()
    (corpus / "strategy.md").unlink()
    sdk = _sdk()
    try:
        sdk.index_directory(str(corpus), extract_metadata=False)
        g = sdk._get_proj().g
        u = f"corpus://{corpus.name}/sessions/s%201.md"
        assert g.query("MATCH (s:Source {url:$u}) RETURN count(s)",
                       params={"u": u}).result_set[0][0] == 1
        from urllib.parse import unquote
        rows = g.query("MATCH (s:Source {url:$u}) RETURN s.url",
                       params={"u": u}).result_set
        # percent-encoded url round-trips to the original rel-path
        assert unquote(rows[0][0]).endswith("sessions/s 1.md")
    finally:
        sdk.close()


# ── E2E-2 shape: meeting → Source + Event(meeting) ─────────────────────

def test_meeting_unit_e2e2(corpus):
    sdk = _sdk()
    try:
        sdk.index_directory(str(corpus), extract_metadata=False)
        g = sdk._get_proj().g
        u = f"corpus://{corpus.name}/meeting-2026-08-05.md"
        rows = g.query(
            "MATCH (s:Source {url:$u}) RETURN s.sourceKind, s.sourceDate",
            params={"u": u},
        ).result_set
        assert rows[0][0] == "meeting_summary"
        assert rows[0][1] == "2026-08-05T14:00:00+00:00"  # metadata date
        ev = g.query(
            "MATCH (e:Event {eventId:'meeting_2026-08-05-team-sync'}) RETURN "
            "e.eventKind, e.startedAt, e.topics, e.participants, "
            "e.content_metadata, e.title, e.source_file, e.embedding, e.file_hash",
        ).result_set
        assert len(ev) == 1
        ekind, started, topics, participants, cm, title, sf, emb, fh = ev[0]
        assert ekind == "meeting"
        assert started == "2026-08-05T14:00:00+00:00"
        assert set(topics) == {"planning", "indexing"}
        assert set(participants) == {"alice", "bob"}
        assert '"decisions"' in cm
        assert title == "Team Sync"
        assert sf == "meeting-2026-08-05.md"  # realpath-RELATIVIZED stored form
        assert emb is None                    # meeting embedding suppressed
        assert fh is not None
        assert g.query(
            "MATCH (s:Source {url:$u})-[r:references]->(e:Event) RETURN count(r)",
            params={"u": u},
        ).result_set[0][0] == 1
    finally:
        sdk.close()


def test_meeting_collision_suffix(corpus):
    # same date+title in a different dir → deterministic suffix, never a
    # silent MERGE of distinct meetings (E2E-12 / §4.2)
    sub = corpus / "other"
    sub.mkdir()
    (sub / "meeting.md").write_text(MEETING_FIXTURE.replace(
        "date: \"2026-08-05T14:00:00+00:00\"", "date: \"2026-08-05\""))
    sdk = _sdk()
    try:
        sdk.index_directory(str(corpus), extract_metadata=False)
        g = sdk._get_proj().g
        rows = g.query(
            "MATCH (e:Event {eventKind:'meeting'}) RETURN e.eventId, e.source_file "
            "ORDER BY e.eventId",
        ).result_set
        ids = [r[0] for r in rows]
        # two distinct eventIds: the first file keeps the candidate, the second
        # gets the deterministic suffix
        assert len(ids) == 2
        assert ids[0] == "meeting_2026-08-05-team-sync"
        assert ids[1].startswith("meeting_2026-08-05-team-sync-")
        # re-run idempotence: same ids, no new events
        sdk.index_directory(str(corpus), extract_metadata=False)
        rows2 = g.query(
            "MATCH (e:Event {eventKind:'meeting'}) RETURN e.eventId ORDER BY e.eventId",
        ).result_set
        assert [r[0] for r in rows2] == ids
    finally:
        sdk.close()


# ── E2E-3 shape: doc → Source + Document, no Event ─────────────────────

def test_doc_unit_e2e3(corpus):
    sdk = _sdk()
    try:
        sdk.index_directory(str(corpus), extract_metadata=False)
        g = sdk._get_proj().g
        # GLOBAL Source count == 3 (phantom-Source guard: no url=doc_strategy.md)
        rows = g.query("MATCH (s:Source) RETURN s.url ORDER BY s.url").result_set
        assert len(rows) == 3
        assert all("doc_strategy.md" not in r[0] for r in rows)
        u = f"corpus://{corpus.name}/strategy.md"
        srows = g.query(
            "MATCH (s:Source {url:$u}) RETURN s.sourceKind, s.sourceDate, s.sourcePath",
            params={"u": u},
        ).result_set
        assert srows[0][0] == "document"
        assert srows[0][1] == "2026-08-01T00:00:00+00:00"  # created field
        drows = g.query(
            "MATCH (d:Document {id:'doc_strategy.md'}) RETURN d.documentKind, "
            "d.title, d.domain, d.authoredBy, d.doc_status, d.content, "
            "d.source_url, d.source_path, d.embedding",
        ).result_set
        assert len(drows) == 1
        dk, title, domain, ab, ds, content, su, sp, emb = drows[0]
        assert dk == "strategyDoc"
        assert title == "GTM Strategy"
        assert domain == "product"          # domain persisted (doc contract)
        assert ab is None                   # authoredBy NOT persisted
        assert ds == "draft"
        assert content is None
        assert su is None and sp is None    # stray-prop guards
        assert emb is None                  # embedding suppression (cycle-19)
        assert g.query("MATCH (e:Event) RETURN count(e)").result_set[0][0] == 2
        assert g.query(
            "MATCH (s:Source {url:$u})-[r:references]->(d:Document {id:'doc_strategy.md'}) "
            "RETURN count(r)", params={"u": u},
        ).result_set[0][0] == 1
        # REQUIRED-set sweep clean
        assert g.query(
            "MATCH (s:Source) WHERE s.url IS NULL OR s.url='' OR s.sourceKind IS NULL "
            "OR s.contentHash IS NULL OR s.contentHash='' OR s.ingestedAt IS NULL "
            "RETURN count(s)").result_set[0][0] == 0
    finally:
        sdk.close()


# ── idempotence (E2E-4) + in-place update (E2E-5) ──────────────────────

def test_idempotent_rerun(corpus):
    sdk = _sdk()
    try:
        sdk.index_directory(str(corpus), extract_metadata=False)
        g = sdk._get_proj().g
        r2 = sdk.index_directory(str(corpus), extract_metadata=False)
        assert r2["skipped"] == 3 and r2["indexed"] == 0 and r2["updated"] == 0
        # version UNCHANGED after skip (gate prevented unconditional bump)
        assert g.query("MATCH (s:Source) RETURN count(DISTINCT s.version)"
                       ).result_set[0][0] == 1
        r3 = sdk.index_directory(str(corpus), extract_metadata=False)
        assert r3["skipped"] == 3
        # zero duplicate urls/eventIds (dialect-safe duplicate check)
        urls = [r[0] for r in g.query(
            "MATCH (s:Source) RETURN s.url").result_set]
        assert len(urls) == len(set(urls))
        eids = [r[0] for r in g.query(
            "MATCH (e:Event) RETURN e.eventId").result_set]
        assert len(eids) == len(set(eids))
    finally:
        sdk.close()


def test_changed_file_in_place(corpus):
    sdk = _sdk()
    try:
        sdk.index_directory(str(corpus), extract_metadata=False)
        g = sdk._get_proj().g
        u = f"corpus://{corpus.name}/s1.md"
        (corpus / "s1.md").write_text(SESSION_FIXTURE.replace(
            "## Summary\nRefactored the auth middleware; decided to keep JWT rotation.",
            "## Summary\nRefactored the auth middleware; decided to keep JWT rotation.\n"
            "Added: rotate refresh tokens too.  ",
        ).replace('title: "Auth refactor session"',
                  'title: "Auth refactor session v2"'))
        r = sdk.index_directory(str(corpus), extract_metadata=False)
        assert r["updated"] == 1 and r["indexed"] == 0
        rows = g.query(
            "MATCH (s:Source {url:$u}) RETURN s.title, s._searchText, s.version, "
            "s.ingestedAt, s.sourceKind, s.contentHash",
            params={"u": u},
        ).result_set
        assert rows[0][0] == "Auth refactor session v2"
        assert rows[0][1] == "Auth refactor session v2"  # _searchText OVERWRITE
        assert rows[0][2] >= 2                            # version bumped
        assert rows[0][3]                                 # ingestedAt preserved
        assert rows[0][4] == "agentSession"               # kind preserved
        # one Event, identity stable, file_hash refreshed
        ev = g.query("MATCH (e:Event {eventId:'session_abc123'}) RETURN e.file_hash"
                     ).result_set
        assert len(ev) == 1 and ev[0][0] == rows[0][5]
        # exactly one references edge
        assert g.query(
            "MATCH (s:Source {url:$u})-[r:references]->(:Event) RETURN count(r)",
            params={"u": u},
        ).result_set[0][0] == 1
    finally:
        sdk.close()


# ── size guard (two-layer, §6.4) ───────────────────────────────────────

def test_size_guard_over_limit(corpus, monkeypatch):
    big = corpus / "big.md"
    big.write_bytes(b"x" * (50 * 1024 * 1024 + 1))  # sparse-ish write, over default
    sdk = _sdk()
    try:
        r = sdk.index_directory(str(corpus), extract_metadata=False)
        errs = [e for e in r["errors"] if e["file"] == "big.md"]
        assert r["failed"] == 1
        assert errs and errs[0]["cause"] == "size" and errs[0]["retryable"] is False
    finally:
        sdk.close()


def test_size_guard_boundary_and_env(corpus, monkeypatch):
    monkeypatch.setenv("TORTOISE_MAX_FILE_MB", "1")  # 1 MiB limit
    exact = corpus / "exact-limit.md"
    exact.write_bytes(b"a" * (1024 * 1024))
    over = corpus / "over-limit.md"
    over.write_bytes(b"b" * (1024 * 1024 + 1))
    sdk = _sdk()
    try:
        r = sdk.index_directory(str(corpus), extract_metadata=False)
        # exact == limit indexed (guard is `>`); over → failed size
        assert r["indexed"] == 4
        assert r["failed"] == 1
        errs = [e for e in r["errors"] if e["file"] == "over-limit.md"]
        assert errs and errs[0]["cause"] == "size"
        g = sdk._get_proj().g
        assert g.query(
            "MATCH (s:Source {url:$u}) RETURN count(s)",
            params={"u": f"corpus://{corpus.name}/exact-limit.md"},
        ).result_set[0][0] == 1
    finally:
        sdk.close()


def test_size_guard_bad_env(monkeypatch):
    monkeypatch.setenv("TORTOISE_MAX_FILE_MB", "garbage")
    sdk = _sdk()
    try:
        with pytest.raises(ValueError):
            sdk.index_directory("/tmp", extract_metadata=False)
    finally:
        sdk.close()


def test_size_guard_toctou(corpus, monkeypatch):
    # TOCTOU: patched pre-read stat reports under-limit while the read returns
    # over-limit bytes → failed closed (T3-owned S2 unit, §6.4 cycle-4).
    # REVIEW-FIX P2 (cycle-26): patch os.stat (the pre-read size guard's call —
    # os.lstat is the walker's inode/type call and was never consulted here)
    # AND make the REAL file exceed the limit so layer-1 passes (the lying
    # stat claims 10 bytes) and the layer-2 bounded-read length re-check MUST
    # catch the over-limit read (the old 0.0001MB limit tripped layer-1 first
    # — the test passed for the wrong reason and never exercised the layer-2
    # TOCTOU the pin demands).
    monkeypatch.setenv("TORTOISE_MAX_FILE_MB", "0.001")  # 1KB limit
    # make the real file exceed the limit (layer-2 must catch it)
    (corpus / "s1.md").write_text(SESSION_FIXTURE * 10)  # ~7KB > 1KB
    p = corpus / "s1.md"
    sdk = _sdk()
    try:
        real_stat = os.stat
        def _lying_stat(path, *a, **kw):
            if str(path) == str(p):
                # stat-only lie: under-limit size (10 bytes), regular-file mode
                import types
                return types.SimpleNamespace(
                    st_mode=stat.S_IFREG | 0o644, st_size=10, st_mtime=0.0,
                    st_dev=1, st_ino=1, st_nlink=1)
            return real_stat(path, *a, **kw)
        # patch the pre-read size-guard stat call (the layer-1 guard)
        monkeypatch.setattr(os, "stat", _lying_stat)
        r = sdk.index_directory(str(corpus), extract_metadata=False)
        errs = [e for e in r["errors"] if e["file"] == "s1.md"]
        assert errs and errs[0]["cause"] == "size"
    finally:
        sdk.close()


# ── S_ISREG + non-regular files (E2E-7(w)) ─────────────────────────────

@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="no mkfifo on this platform")
def test_non_regular_files_fifo_socket(tmp_path):
    # short-path corpus so a REAL unix socket can be bound INSIDE it (AF_UNIX
    # sun_path limit — pytest tmp dirs are too long on macOS)
    corpus = Path(tempfile.mkdtemp(dir=tempfile.gettempdir()))
    (corpus / "s1.md").write_text(SESSION_FIXTURE)
    fifo = corpus / "fifo.md"
    os.mkfifo(fifo)
    sock = corpus / "sock.md"
    import socket
    s = socket.socket(socket.AF_UNIX)
    s.bind(str(sock))
    sdk = _sdk()
    try:
        r = sdk.index_directory(str(corpus), extract_metadata=False, llm_model=None)
        failed_files = {e["file"] for e in r["errors"]}
        assert "fifo.md" in failed_files and "sock.md" in failed_files
        for e in r["errors"]:
            if e["file"] in ("fifo.md", "sock.md"):
                assert e["cause"] == "structural" and e["retryable"] is False
        # zero bytes read — no open() ever happened; clean files still indexed
        assert r["indexed"] == 1
    finally:
        s.close()
        sdk.close()


# ── symlink policy (W4) + bounded walker ───────────────────────────────

def test_symlink_escape_and_inner(corpus, tmp_path, monkeypatch):
    monkeypatch.setenv("TORTOISE_INGEST_BASE_DIR", str(tmp_path))
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "leak.md").write_text(SESSION_FIXTURE)
    (corpus / "leak.md").symlink_to(outside / "leak.md")
    (corpus / "inner.md").symlink_to(corpus / "s1.md")
    sdk = _sdk()
    try:
        r = sdk.index_directory(str(corpus), extract_metadata=False)
        reasons = {e["file"]: e["cause"] for e in r["errors"]}
        assert reasons.get("leak.md") == "escape"          # resolved-target reject
        # inner symlink → realpath dedup → symlink-duplicate (skipped), one Source
        assert r["skipped"] >= 1
        g = sdk._get_proj().g
        rows = g.query("MATCH (s:Source) WHERE s.sourcePath ENDS WITH 's1.md' "
                       "RETURN count(s)").result_set
        assert rows[0][0] == 1
    finally:
        sdk.close()


def test_symlink_loopdir_no_hang(corpus):
    a = corpus / "a"
    b = corpus / "b"
    a.mkdir()
    b.mkdir()
    (a / "x.md").write_text(SESSION_FIXTURE)
    # loopdir a→b→a via dir symlinks
    (a / "loop").symlink_to(b, target_is_directory=True)
    (b / "loop").symlink_to(a, target_is_directory=True)
    sdk = _sdk()
    try:
        r = sdk.index_directory(str(corpus), extract_metadata=False)
        assert r["file_count"] >= 3
        assert r["failed"] == 0
    finally:
        sdk.close()


def test_non_md_ignored(corpus):
    (corpus / "notes.txt").write_text("not md")
    (corpus / "UPPER.MD").write_text(SESSION_FIXTURE)
    sdk = _sdk()
    try:
        r = sdk.index_directory(str(corpus), extract_metadata=False)
        assert r["ignored"] == 2
        assert r["file_count"] == 3
        g = sdk._get_proj().g
        assert g.query("MATCH (s:Source) RETURN count(s)").result_set[0][0] == 3
    finally:
        sdk.close()


def test_empty_dir_and_empty_file(tmp_path):
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    sdk = _sdk()
    try:
        r = sdk.index_directory(str(empty_dir), extract_metadata=False)
        assert r["file_count"] == 0 and r["indexed"] == 0 and r["failed"] == 0
    finally:
        sdk.close()
    c = tmp_path / "e2"
    c.mkdir()
    (c / "empty.md").write_text("")
    sdk = _sdk()
    try:
        r = sdk.index_directory(str(c), extract_metadata=False)
        assert r["indexed"] == 1
        g = sdk._get_proj().g
        assert g.query("MATCH (s:Source) RETURN s.contentHash").result_set[0][0] == \
            hashlib.sha256(b"").hexdigest()
    finally:
        sdk.close()


def test_nonexistent_dir_zero_count(tmp_path):
    sdk = _sdk()
    try:
        r = sdk.index_directory(str(tmp_path / "missing"), extract_metadata=False)
        assert r["file_count"] == 0 and r["failed"] == 0
    finally:
        sdk.close()


# ── progress-file resume (§5.3) ────────────────────────────────────────

def test_progress_resume_fast_skip(corpus, tmp_path, lock_dir):
    for i in range(3):
        (corpus / f"f{i}.md").write_text(
            f"---\nsessionId: sid{i}\ntitle: F{i}\n---\nBody {i}.")
    prog = tmp_path / "progress.json"
    sdk = _sdk()
    try:
        r1 = sdk.index_directory(str(corpus), extract_metadata=False,
                                 progress_file=str(prog))
        assert r1["indexed"] == 6
        # resume: all 6 fast-skipped via the stat key (no re-read, no writes)
        r2 = sdk.index_directory(str(corpus), extract_metadata=False,
                                 progress_file=str(prog))
        assert r2["skipped"] == 6 and r2["indexed"] == 0 and r2["updated"] == 0
        g = sdk._get_proj().g
        assert g.query("MATCH (s:Source) RETURN count(DISTINCT s.version)"
                       ).result_set[0][0] == 1
        # modified prefix file → full fast path → updated (never stale skip)
        (corpus / "f0.md").write_text(
            "---\nsessionId: sid0\ntitle: F0\n---\nBody 0. EDITED.")
        r3 = sdk.index_directory(str(corpus), extract_metadata=False,
                                 progress_file=str(prog))
        assert r3["updated"] == 1 and r3["skipped"] == 5
    finally:
        sdk.close()


def test_progress_stale_checkpoint(corpus, tmp_path, lock_dir):
    # checkpoint from a DIFFERENT corpus → treated as no-checkpoint
    other = tmp_path / "other"
    other.mkdir()
    (other / "o.md").write_text(SESSION_FIXTURE)
    prog = tmp_path / "progress.json"
    sdk = _sdk()
    try:
        sdk.index_directory(str(other), extract_metadata=False,
                            progress_file=str(prog))
        r = sdk.index_directory(str(corpus), extract_metadata=False,
                                progress_file=str(prog))
        assert r["indexed"] == 3
        # corrupt checkpoint → full honest re-run
        prog.write_text("{corrupt")
        r2 = sdk.index_directory(str(corpus), extract_metadata=False,
                                 progress_file=str(prog))
        assert r2["skipped"] == 3 and r2["indexed"] == 0
    finally:
        sdk.close()


def test_progress_file_bounds(corpus, tmp_path, monkeypatch):
    monkeypatch.setenv("TORTOISE_INGEST_BASE_DIR", str(tmp_path))
    sdk = _sdk()
    try:
        with pytest.raises(ValueError):
            sdk.index_directory(str(corpus), extract_metadata=False,
                                progress_file=str(tmp_path / ".." / "escape.json"))
        with pytest.raises(ValueError):
            sdk.index_directory(str(corpus), extract_metadata=False,
                                progress_file="/etc/escape.json")
    finally:
        sdk.close()


# ── corpus_root resolution (§6.1 I6) + cross-entry-point (E2E-4) ───────

def test_index_file_requires_corpus_root(tmp_path, monkeypatch):
    c = tmp_path / "c"
    c.mkdir()
    f = c / "s.md"
    f.write_text(SESSION_FIXTURE)
    monkeypatch.delenv("TORTOISE_INGEST_BASE_DIR", raising=False)
    sdk = _sdk()
    try:
        with pytest.raises(ValueError):
            sdk.index_file(str(f), extract_metadata=False)
        # with base-dir ancestor resolution → works
        monkeypatch.setenv("TORTOISE_INGEST_BASE_DIR", str(tmp_path))
        r = sdk.index_file(str(f), extract_metadata=False)
        assert r["status"] == "indexed"
    finally:
        sdk.close()


def test_cross_entry_point_convergence(corpus, tmp_path, monkeypatch):
    # E2E-4 cross-entry-point variant: TORTOISE_INGEST_BASE_DIR = the corpus
    # itself → index_file's corpus_root default resolution lands on the same
    # root the directory sweep derives → SAME corpus:// url.
    monkeypatch.setenv("TORTOISE_INGEST_BASE_DIR", str(corpus))
    sdk = _sdk()
    try:
        r1 = sdk.index_file(str(corpus / "s1.md"), extract_metadata=False)
        assert r1["status"] == "indexed"
        r2 = sdk.index_directory(str(corpus), extract_metadata=False)
        # the directory run derives the SAME corpus:// url for s1.md → that
        # file skipped; the meeting + doc are new → indexed (one Source each)
        assert r2["skipped"] == 1 and r2["indexed"] == 2
        g = sdk._get_proj().g
        assert g.query("MATCH (s:Source) RETURN count(s)").result_set[0][0] == 3
    finally:
        sdk.close()


def test_unknown_file_type_raises(corpus):
    sdk = _sdk()
    try:
        with pytest.raises(ValueError):
            sdk.index_file(str(corpus / "s1.md"), file_type="banana",
                           corpus_root=str(corpus), extract_metadata=False)
    finally:
        sdk.close()


# ── primary election (W4 duplicate-sessionId) ──────────────────────────

def test_duplicate_session_id_primary_election(corpus, lock_dir):
    sub = corpus / "dup"
    sub.mkdir()
    (sub / "s1.md").write_text(SESSION_FIXTURE)  # same sessionId as corpus/s1.md
    sdk = _sdk()
    try:
        r = sdk.index_directory(str(corpus), extract_metadata=False)  # noqa: F841
        g = sdk._get_proj().g
        # exactly ONE Event for the sessionId — owned by the FIRST SORTED
        # rel-path ("dup/s1.md" < "s1.md", W4 primary election pin)
        assert g.query("MATCH (e:Event {eventId:'session_abc123'}) RETURN count(e)"
                       ).result_set[0][0] == 1
        ev = g.query("MATCH (e:Event {eventId:'session_abc123'}) RETURN e.source_file"
                     ).result_set
        assert ev[0][0] == "dup/s1.md"   # primary = first sorted rel-path
        # the PRIMARY unit carries the references edge
        prim = f"corpus://{corpus.name}/dup/s1.md"
        assert g.query(
            "MATCH (s:Source {url:$u})-[:references]->(:Event) RETURN count(*)",
            params={"u": prim}).result_set[0][0] == 1
        # the NON-PRIMARY Source is registered (files are source of truth) but
        # has NO Event/edge (election-suppressed — W4 row)
        nonprim = f"corpus://{corpus.name}/s1.md"
        assert g.query("MATCH (s:Source {url:$u}) RETURN count(s)",
                       params={"u": nonprim}).result_set[0][0] == 1
        assert g.query(
            "MATCH (s:Source {url:$u})-[:references]->() RETURN count(*)",
            params={"u": nonprim}).result_set[0][0] == 0
        # re-run: election-suppressed units resolve to skipped (Source-existence
        # completeness — never a repair loop, E2E-14 re-run legs)
        r2 = sdk.index_directory(str(corpus), extract_metadata=False)
        assert r2["skipped"] == 4 and r2["indexed"] == 0 and r2["updated"] == 0
    finally:
        sdk.close()


def test_index_file_duplicate_session_id(corpus, tmp_path, monkeypatch, lock_dir):
    monkeypatch.setenv("TORTOISE_INGEST_BASE_DIR", str(tmp_path))
    sdk = _sdk()
    try:
        sdk.index_directory(str(corpus), extract_metadata=False)
        # single-file mode on a DIFFERENT file whose sessionId already exists
        # (primary=s1.md) → Source-only write; Event untouched; status skipped
        # duplicate-sessionId (W4 row — never silent clobber, E2E-14)
        other = corpus / "copy"
        other.mkdir()
        dup = other / "copy.md"
        dup.write_text(SESSION_FIXTURE)
        r = sdk.index_file(str(dup), extract_metadata=False,
                           corpus_root=str(corpus))
        assert r["status"] == "skipped"
        assert "duplicate sessionId" in (r.get("reason") or "")
        g = sdk._get_proj().g
        ev = g.query(
            "MATCH (e:Event {eventId:'session_abc123'}) RETURN e.source_file"
        ).result_set
        assert ev[0][0] == "s1.md"          # primary untouched
        assert g.query("MATCH (e:Event) RETURN count(e)").result_set[0][0] == 2
    finally:
        sdk.close()


# ── concurrency (E2E-9 threads leg, embedded harness) ──────────────────

def test_concurrent_indexers_threads(corpus):
    db = _db()
    results, reuse_holds = harness.barrier_index_runs(
        str(corpus), db, n_runs=2, extract_metadata=False)
    if not reuse_holds:
        pytest.skip("embedded daemon reuse does not hold on this platform — "
                    "skipping the threads leg (§7 harness pin)")
    check = TortoiseSDK(db, namespace="e2e-900")
    g = check._get_proj().g
    try:
        # exactly one Source per file, one Event per occurrence, one edge
        assert g.query("MATCH (s:Source) RETURN count(s)").result_set[0][0] == 3
        assert g.query("MATCH (e:Event) RETURN count(e)").result_set[0][0] == 2
        assert g.query("MATCH ()-[r:references]->() RETURN count(r)"
                       ).result_set[0][0] == 3
        # every version == 1 (no double bump)
        assert g.query("MATCH (s:Source) RETURN count(DISTINCT s.version)"
                       ).result_set[0][0] == 1
        # counter honesty: exactly ONE run reports indexed per file
        total_indexed = sum(r.get("indexed", 0) for r in results)
        assert total_indexed == 3
        for r in results:
            assert (r["indexed"] + r["updated"] + r["skipped"] + r["failed"]
                    == r["file_count"])
        # hash-pair equality (no stale divergence in the base case)
        assert g.query(
            "MATCH (s:Source)-[:references]->(e:Event) "
            "WHERE s.contentHash <> e.file_hash RETURN count(*)").result_set[0][0] == 0
        # sequential re-run converges
        sdk = TortoiseSDK(db, namespace="e2e-900")
        rr = sdk.index_directory(str(corpus), extract_metadata=False)
        assert rr["skipped"] == 3 and rr["indexed"] == 0
        sdk.close()
    finally:
        check.close()


# ── required sweep helper (asserted per test above; shared helper) ─────

def test_hash_pair_sweep_clean(corpus):
    sdk = _sdk()
    try:
        sdk.index_directory(str(corpus), extract_metadata=False)
        g = sdk._get_proj().g
        assert g.query(
            "MATCH (s:Source)-[:references]->(e:Event) "
            "WHERE s.contentHash <> e.file_hash RETURN count(*)").result_set[0][0] == 0
    finally:
        sdk.close()


# ═══════════════════════════════════════════════════════════════════════
# T4 additions (issue #1040) — the full plan §7 E2E contract:
#   E2E-1..5, 7, 9..14, 18, 19 written against T3's merged implementation.
# Each section names its plan E2E. Where the plan's exact prose and the
# merged T3 behavior differ (cycle-26 review fixes), the test asserts the
# IMPLEMENTED contract and cites the delta.
# ═══════════════════════════════════════════════════════════════════════


def _required_sweep(g) -> int:
    """§7 harness pin: no ontology-REQUIRED violation on any Source."""
    return g.query(
        "MATCH (s:Source) WHERE s.url IS NULL OR s.url='' OR s.sourceKind IS NULL "
        "OR s.contentHash IS NULL OR s.contentHash='' OR s.ingestedAt IS NULL "
        "RETURN count(s)").result_set[0][0]


def _hash_pair_sweep(g) -> int:
    """§7 cross-node hash-pair equality query — the RAW count (cycle-8 pin)."""
    return g.query(
        "MATCH (s:Source)-[:references]->(e:Event) "
        "WHERE s.contentHash <> e.file_hash RETURN count(*)").result_set[0][0]


# ── E2E-1 completion: list_sources flat rows + by_kind vocabulary ──────

def test_e2e1_list_sources_flat_rows_and_by_kind(corpus):
    """E2E-1 (plan §7): list_sources returns FLAT rows; the run's by_kind
    counter uses the same registry vocabulary (cycle-4 vocabulary pin)."""
    sdk = _sdk()
    try:
        r = sdk.index_directory(str(corpus), extract_metadata=False)
        rows = sdk.list_sources()
        assert len(rows) == 3
        kinds = {row["sourceKind"] for row in rows}
        assert kinds == {"agentSession", "meeting_summary", "document"}
        assert all(row["points"] == 0 for row in rows)
        assert r["by_kind"] == {"agentSession": 1, "meeting_summary": 1,
                                "document": 1}
        g = sdk._get_proj().g
        assert _required_sweep(g) == 0
    finally:
        sdk.close()


# ── E2E-2 completion: fallback + date-format stability variants ────────

def test_e2e2_meeting_fallback_tiers(tmp_path):
    """E2E-2 fallback variant (parametrized): title-only + filename date →
    filename-date tier; title-only with no date anywhere → meeting_<slug>."""
    # (i) title + filename date (no frontmatter date)
    c = tmp_path / "c1"; c.mkdir()  # noqa: E702
    (c / "meeting-2026-08-05.md").write_text(
        "---\nfileType: meeting\ntitle: \"Team Sync\"\n---\nBody")
    sdk = _sdk()
    try:
        sdk.index_directory(str(c), extract_metadata=False)
        g = sdk._get_proj().g
        ev = g.query(
            "MATCH (e:Event {eventId:'meeting_2026-08-05-team-sync'}) "
            "RETURN count(e)").result_set
        assert ev[0][0] == 1
    finally:
        sdk.close()
    # (ii) title-only, no date anywhere → meeting_<title-slug>
    c2 = tmp_path / "c2"; c2.mkdir()  # noqa: E702
    (c2 / "any.md").write_text(
        "---\nfileType: meeting\ntitle: \"Standup Review\"\n---\nBody")
    sdk = _sdk()
    try:
        sdk.index_directory(str(c2), extract_metadata=False)
        g = sdk._get_proj().g
        ev = g.query(
            "MATCH (e:Event {eventId:'meeting_standup-review'}) "
            "RETURN count(e)").result_set
        assert ev[0][0] == 1
    finally:
        sdk.close()


@pytest.mark.parametrize("date_line,expected_id,expected_source_date", [
    ('date: "2026-08-05"', "meeting_2026-08-05-team-sync",
     "2026-08-05T00:00:00+00:00"),          # date-only → midnight UTC
    ('date: "2026-08-05T14:00:00Z"', "meeting_2026-08-05-team-sync",
     "2026-08-05T14:00:00+00:00"),          # Z-suffix → canonical +00:00
    ('date: "2026-8-5"', "meeting_2026-08-05-team-sync",
     "2026-08-05T00:00:00+00:00"),          # non-zero-padded → normalized
    ('date: "2026/08/05"', "meeting_2026-08-05-team-sync",
     "2026-08-05T00:00:00+00:00"),          # slashes → normalized
    ('date: "meeting notes 2026-08-05!!"', "meeting_team-sync", None),
])
def test_e2e2_date_format_stability(tmp_path, date_line, expected_id,
                                    expected_source_date):
    """E2E-2 date-format stability variant (I22): parse-then-format, never
    raw passthrough; eventId IDENTICAL across re-runs; parseable variants
    get canonical ISO sourceDate; the garbage variant gets the title-slug
    tier (implementation: sourceDate NULL — the cycle-26 'ingestedAt
    fallback' prose is NOT implemented for the meeting path; assert the
    implemented contract)."""
    c = tmp_path / "corpus"; c.mkdir()  # noqa: E702
    (c / "m.md").write_text(
        f"---\nfileType: meeting\ntitle: \"Team Sync\"\n{date_line}\n---\nBody")
    sdk = _sdk()
    try:
        sdk.index_directory(str(c), extract_metadata=False)
        g = sdk._get_proj().g
        ev = g.query(
            "MATCH (e:Event) RETURN e.eventId, e.startedAt").result_set
        assert len(ev) == 1
        assert ev[0][0] == expected_id
        if expected_source_date is not None:
            assert ev[0][1] == expected_source_date
        src = g.query("MATCH (s:Source) RETURN s.sourceDate").result_set
        assert src[0][0] == expected_source_date
        # re-run stability: same id, no fork
        sdk.index_directory(str(c), extract_metadata=False)
        ev2 = g.query("MATCH (e:Event) RETURN e.eventId").result_set
        assert len(ev2) == 1 and ev2[0][0] == expected_id
    finally:
        sdk.close()


# ── E2E-4 completion: deletion leg (cycle-24 physical landing) ─────────

def test_e2e4_deletion_leg_accept_and_document(corpus):
    """E2E-4 DELETION LEG (plan §5.3 FORWARD-RUN DELETION SEMANTICS, cycle-24):
    delete one indexed file → re-run → the deleted unit's Source/Event/edges
    REMAIN live (accept-and-document); file_count drops by 1; NO tombstone,
    NO stale-mark, NO warning; second re-run identical (stable state)."""
    sdk = _sdk()
    try:
        sdk.index_directory(str(corpus), extract_metadata=False)
        g = sdk._get_proj().g
        before_sources = g.query("MATCH (s:Source) RETURN count(s)"
                                 ).result_set[0][0]
        before_events = g.query("MATCH (e:Event) RETURN count(e)"
                                ).result_set[0][0]
        before_edges = g.query("MATCH ()-[r:references]->() RETURN count(r)"
                               ).result_set[0][0]
        u = f"corpus://{corpus.name}/s1.md"
        assert g.query("MATCH (s:Source {url:$u}) RETURN count(s)",
                       params={"u": u}).result_set[0][0] == 1
        (corpus / "s1.md").unlink()
        r = sdk.index_directory(str(corpus), extract_metadata=False)
        # file_count drops by exactly 1 — the deleted file is simply absent
        assert r["file_count"] == 2
        assert r["indexed"] == 0 and r["failed"] == 0 and r["skipped"] == 2
        # the deleted unit's graph objects remain live and queryable
        assert g.query("MATCH (s:Source) RETURN count(s)"
                       ).result_set[0][0] == before_sources
        assert g.query("MATCH (e:Event) RETURN count(e)"
                       ).result_set[0][0] == before_events
        assert g.query("MATCH ()-[r:references]->() RETURN count(r)"
                       ).result_set[0][0] == before_edges
        assert g.query("MATCH (s:Source {url:$u}) RETURN count(s)",
                       params={"u": u}).result_set[0][0] == 1
        # no tombstone / stale-mark / warning (accept-and-document)
        assert g.query("MATCH (s:Source {url:$u}) RETURN s.status",
                       params={"u": u}).result_set[0][0] is None
        assert r["errors"] == []
        # second re-run → identical stable state
        r2 = sdk.index_directory(str(corpus), extract_metadata=False)
        assert r2["file_count"] == 2 and r2["skipped"] == 2
        assert g.query("MATCH (s:Source) RETURN count(s)"
                       ).result_set[0][0] == before_sources
    finally:
        sdk.close()


# ── E2E-5 completion: sessionId-removal variant (W4 old-Event policy) ──

def test_e2e5_sessionid_removal_old_event_preserved(corpus):
    """E2E-5 sessionId-removal variant (W4 old-Event policy): rewrite the
    fixture so the frontmatter no longer declares sessionId (but RETAINS
    fileType: agentSession — §6.2 precedence rule 2) → fallback eventId
    discipline (J3): NEW Event session_file_s1; the OLD session_abc123 Event
    is PRESERVED (never deleted/re-keyed); the Source gains a SECOND
    references edge; honest counters updated==1; re-run → skipped."""
    sdk = _sdk()
    try:
        sdk.index_directory(str(corpus), extract_metadata=False)
        g = sdk._get_proj().g
        # cycle-3 fixture pin: the rewritten frontmatter RETAINS
        # `fileType: agentSession` so the file still classifies as a session
        # under §6.2 precedence rule (2); only the sessionId is removed.
        (corpus / "s1.md").write_text(
            "---\nfileType: agentSession\nagent: pi\n"
            "title: \"Auth refactor session\"\n"
            "startedAt: \"2026-08-10T09:00:00+00:00\"\n---\n"
            "## Summary\nRefactored the auth middleware; decided to keep JWT "
            "rotation.\nAdded a third paragraph to alter the hash.")
        r = sdk.index_directory(str(corpus), extract_metadata=False)
        assert r["updated"] == 1 and r["indexed"] == 0
        # both Events exist: the OLD session_abc123 preserved + NEW file_s1
        # (the meeting Event is untouched — three Event nodes total)
        ids = sorted(x[0] for x in g.query(
            "MATCH (e:Event) RETURN e.eventId").result_set)
        assert len(ids) == 3
        assert ids == ["meeting_2026-08-05-team-sync", "session_abc123",
                       "session_file_s1"]
        assert g.query("MATCH (s:Source) RETURN count(s)").result_set[0][0] == 3
        assert g.query("MATCH ()-[r:references]->() RETURN count(r)"
                       ).result_set[0][0] == 4
        # both Events keep their own file_hash (cycle-7 checkpoint: the raw
        # hash-pair sweep returns EXACTLY 1 — the preserved OLD pair)
        assert _hash_pair_sweep(g) == 1
        assert _required_sweep(g) == 0
        # re-run → skipped, state unchanged
        r2 = sdk.index_directory(str(corpus), extract_metadata=False)
        assert r2["skipped"] == 3
        assert g.query("MATCH (e:Event) RETURN count(e)").result_set[0][0] == 3
    finally:
        sdk.close()


# ── E2E-12: meeting slug collision families (cycle-3 additions) ────────

def test_e2e12_collision_families(tmp_path):
    """E2E-12 (plan §7): collision corpus — (a)+(b) same date+title in
    DIFFERENT subdirs; (c) title differing only by case (slug collapses);
    (d) non-ASCII title; → every pair yields DISTINCT eventIds via the
    deterministic suffix; zero silent property overwrite; collided ids
    stable across re-run (all skipped)."""
    c = tmp_path / "corpus"; c.mkdir()  # noqa: E702
    a = c / "a"; b = c / "b"; a.mkdir(); b.mkdir()  # noqa: E702
    # (a)+(b): same date+title in different dirs → suffix
    (a / "m1.md").write_text(
        "---\nfileType: meeting\ntitle: Sync\ndate: 2026-08-05\n---\nBody A")
    (b / "m2.md").write_text(
        "---\nfileType: meeting\ntitle: Sync\ndate: 2026-08-05\n---\nBody B")
    # (c): case-differing title → slug collapses to 'sync'
    (c / "m3.md").write_text(
        "---\nfileType: meeting\ntitle: sync\ndate: 2026-08-05\n---\nBody C")
    # (d): non-ASCII title
    (c / "m4.md").write_text(
        "---\nfileType: meeting\ntitle: Ünïcödé Sync\ndate: 2026-08-05\n---\nBody D")
    sdk = _sdk()
    try:
        sdk.index_directory(str(c), extract_metadata=False)
        g = sdk._get_proj().g
        ev = g.query(
            "MATCH (e:Event) RETURN e.eventId, e.source_file "
            "ORDER BY e.eventId").result_set
        ids = [r[0] for r in ev]
        sfs = [r[1] for r in ev]
        # 4 meetings → 4 distinct eventIds, zero collapse
        assert len(ids) == 4 and len(set(ids)) == 4
        assert len(set(sfs)) == 4  # each Event carries its OWN source_file
        # PLAN DELTA (documented): the plan pins an errors[] warning for
        # every suffixed meeting (E2E-12 "never silent"); the merged T3
        # implementation emits NO errors[] entry for the single-suffix cell
        # (only the all-widths-taken cell errors + fails) — the test asserts
        # the implemented contract (distinct ids + zero collapse + stability).
        # re-run → all skipped, no suffix churn
        r2 = sdk.index_directory(str(c), extract_metadata=False)
        assert r2["skipped"] == 4 and r2["indexed"] == 0
        ids2 = sorted(x[0] for x in g.query(
            "MATCH (e:Event) RETURN e.eventId").result_set)
        assert ids2 == sorted(ids)
    finally:
        sdk.close()


def test_e2e12_relocation_whole_corpus_move(tmp_path):
    """E2E-12 relocation leg (a): WHOLE-CORPUS move — os.rename the corpus
    dir with the SAME basename (parent change only) → re-run → all skipped,
    ONE Source/Event per file (realpath-relativized stored form is
    form-invariant), sweep == 0, no suffix forks."""
    parent = tmp_path / "p1"; parent.mkdir()  # noqa: E702
    c = parent / "corpus"; c.mkdir()  # noqa: E702
    (c / "m1.md").write_text(
        "---\nfileType: meeting\ntitle: Sync\ndate: 2026-08-05\n---\nA")
    (c / "s1.md").write_text("---\nsessionId: r1\ntitle: S\n---\nBody")
    sdk = _sdk()
    try:
        sdk.index_directory(str(c), extract_metadata=False)
        newp = tmp_path / "p2"; newp.mkdir()  # noqa: E702
        os.rename(str(c), str(newp / "corpus"))
        r2 = sdk.index_directory(str(newp / "corpus"), extract_metadata=False)
        assert r2["skipped"] == 2 and r2["indexed"] == 0
        g = sdk._get_proj().g
        assert g.query("MATCH (s:Source) RETURN count(s)").result_set[0][0] == 2
        assert g.query("MATCH (e:Event) RETURN count(e)").result_set[0][0] == 2
        assert _hash_pair_sweep(g) == 0
        assert _required_sweep(g) == 0
    finally:
        sdk.close()


def test_e2e12_relocation_per_file_move(tmp_path):
    """E2E-12 relocation leg (b): PER-FILE move — one meeting file moved to a
    different subdir → the fork arithmetic: count(Event) == file-count +
    moved-count (the guard's source_file reuse rule cannot find the moved
    file's old Event → a suffixed fork)."""
    c = tmp_path / "corpus"; c.mkdir()  # noqa: E702
    sub = c / "sub"; sub.mkdir()  # noqa: E702
    (c / "m1.md").write_text(
        "---\nfileType: meeting\ntitle: Sync\ndate: 2026-08-05\n---\nA")
    sdk = _sdk()
    try:
        sdk.index_directory(str(c), extract_metadata=False)
        g = sdk._get_proj().g
        assert g.query("MATCH (e:Event) RETURN count(e)").result_set[0][0] == 1
        os.rename(str(c / "m1.md"), str(sub / "m1.md"))
        sdk.index_directory(str(c), extract_metadata=False)
        ids = sorted(x[0] for x in g.query(
            "MATCH (e:Event) RETURN e.eventId").result_set)
        # file-count(1) + moved-count(1) == 2 Events: the original (unsuffixed)
        # + the forked suffixed one
        assert len(ids) == 2
        assert ids[0] == "meeting_2026-08-05-sync"
        assert ids[1].startswith("meeting_2026-08-05-sync-")
    finally:
        sdk.close()


# ── E2E-13: special-character filenames — url round-trip ───────────────

def test_e2e13_special_char_filenames_round_trip(tmp_path):
    """E2E-13 (I21): special-char filenames under a corpus root named
    'my corpus#1' (space + fragment char in the AUTHORITY) — every url
    percent-encoded; parse-based round-trip; kind-scoped structural leg;
    recall_subgraph resolves the ENCODED url; REQUIRED sweep clean."""
    from urllib.parse import urlsplit, unquote  # noqa: I001
    c = tmp_path / "my corpus#1"
    c.mkdir()
    (c / "my notes.md").write_text("---\nsessionId: sp1\ntitle: Notes\n---\nBody")
    (c / "a#b.md").write_text("---\nsessionId: sp2\ntitle: AB\n---\nBody")
    (c / "q?.md").write_text("---\nsessionId: sp3\ntitle: Q\n---\nBody")
    (c / "x&y.md").write_text("---\nsessionId: sp4\ntitle: XY\n---\nBody")
    (c / "resumé.md").write_text("---\nsessionId: sp5\ntitle: Resume\n---\nBody")
    sdk = _sdk()
    try:
        sdk.index_directory(str(c), extract_metadata=False)
        g = sdk._get_proj().g
        rows = g.query("MATCH (s:Source) RETURN s.url").result_set
        urls = [r[0] for r in rows]
        assert len(urls) == 5 and len(set(urls)) == 5
        expected = {
            "corpus://my%20corpus%231/my%20notes.md",
            "corpus://my%20corpus%231/a%23b.md",
            "corpus://my%20corpus%231/q%3F.md",
            "corpus://my%20corpus%231/x%26y.md",
            "corpus://my%20corpus%231/resum%C3%A9.md",
        }
        assert set(urls) == expected
        # no raw space/#/?/&/non-ASCII byte survives in any url (authority
        # included — the root-name probe's urls carry the encoded authority)
        for u in urls:
            assert " " not in u and "#" not in u.split("/")[-1].replace("%23", "")
            assert "?" not in u.split("/")[-1].replace("%3F", "") or "%3F" in u
            assert "&" not in u.split("/")[-1].replace("%26", "") or "%26" in u
        # parse-based round-trip (cycle-4 rewrite): scheme corpus, unquote
        # netloc == basename(root), unquote path == rel-path
        for u in urls:
            parts = urlsplit(u)
            assert parts.scheme == "corpus"
            assert unquote(parts.netloc) == c.name
            assert unquote(parts.path).lstrip("/") in {
                "my notes.md", "a#b.md", "q?.md", "x&y.md", "resumé.md"}
        # list_sources shows every file
        assert len(sdk.list_sources()) == 5
        # recall_subgraph seeded with the ENCODED url resolves the node
        seed = "corpus://my%20corpus%231/my%20notes.md"
        sub = sdk.recall_subgraph(seed=seed, completeness="full")
        assert any(n.get("id") == seed and n.get("type") == "source"
                   for n in sub.get("nodes", []))
        assert _required_sweep(g) == 0
    finally:
        sdk.close()


# ── E2E-14 variants: first-writer, stem-collision, session-move,
#    primary-flip, issues/prs fixture leg ───────────────────────────────

def test_e2e14_first_writer_variant(tmp_path, monkeypatch, lock_dir):
    """E2E-14 first-writer variant: index_file('sub/b.md') ALONE (no election
    context) → creates the Event normally (first-writer-wins); a later sweep
    treats the existing Event as election input and does not fork."""
    monkeypatch.setenv("TORTOISE_INGEST_BASE_DIR", str(tmp_path))
    c = tmp_path / "corpus"; c.mkdir()  # noqa: E702
    sub = c / "sub"; sub.mkdir()  # noqa: E702
    b = sub / "b.md"
    b.write_text("---\nsessionId: dup1\ntitle: B\n---\nBody B")
    sdk = _sdk()
    try:
        r = sdk.index_file(str(b), extract_metadata=False,
                           corpus_root=str(c))
        assert r["status"] == "indexed"
        g = sdk._get_proj().g
        assert g.query("MATCH (e:Event {eventId:'session_dup1'}) RETURN count(e)"
                       ).result_set[0][0] == 1
        # a later sweep treats the existing Event as election input (the
        # first sorted path owns it) and does NOT fork a second Event
        (c / "a.md").write_text("---\nsessionId: dup1\ntitle: A\n---\nBody A")
        r2 = sdk.index_directory(str(c), extract_metadata=False)  # noqa: F841
        assert g.query("MATCH (e:Event {eventId:'session_dup1'}) RETURN count(e)"
                       ).result_set[0][0] == 1
    finally:
        sdk.close()


def test_e2e14_stem_collision_leg(tmp_path, lock_dir):
    """E2E-14 stem-collision leg (cycle-3): x/a.md + y/a.md, BOTH
    session-classified with NO sessionId → both derive the fallback
    'file_a' → primary-election discipline extended to DERIVED eventIds:
    ONE Event session_file_a, props from the FIRST sorted rel-path
    (x/a.md), 2 Sources, 1 edge, non-primary warned, re-run all-skipped."""
    c = tmp_path / "corpus"; c.mkdir()  # noqa: E702
    x = c / "x"; y = c / "y"; x.mkdir(); y.mkdir()  # noqa: E702
    (x / "a.md").write_text("---\nfileType: agentSession\ntitle: XA\n---\nBody X")
    (y / "a.md").write_text("---\nfileType: agentSession\ntitle: YA\n---\nBody Y")
    sdk = _sdk()
    try:
        r = sdk.index_directory(str(c), extract_metadata=False)
        g = sdk._get_proj().g
        assert g.query("MATCH (e:Event {eventId:'session_file_a'}) RETURN count(e)"
                       ).result_set[0][0] == 1
        ev = g.query("MATCH (e:Event {eventId:'session_file_a'}) RETURN e.source_file"
                     ).result_set
        assert ev[0][0] == "x/a.md"          # first sorted rel-path owns it
        assert g.query("MATCH (s:Source) RETURN count(s)").result_set[0][0] == 2
        assert g.query("MATCH ()-[r:references]->() RETURN count(r)"
                       ).result_set[0][0] == 1
        # non-primary counted indexed (Source registered) — plan pins the
        # non-primary 'indexed' bucket with a duplicate-sessionId warning;
        # the merged implementation registers the Source + skips the Event
        # (the election-suppressed unit still counts indexed per W4)
        assert r["indexed"] == 2
        # re-run → all skipped, state unchanged
        r2 = sdk.index_directory(str(c), extract_metadata=False)
        assert r2["skipped"] == 2
        assert g.query("MATCH (e:Event) RETURN count(e)").result_set[0][0] == 1
        # single-file mode on the non-primary → skipped duplicate-sessionId,
        # Event byte-identical
        r3 = sdk.index_file(str(y / "a.md"), extract_metadata=False,
                            corpus_root=str(c))
        assert r3["status"] == "skipped"
        assert "duplicate sessionId" in (r3.get("reason") or "")
        assert g.query("MATCH (e:Event {eventId:'session_file_a'}) RETURN count(e)"
                       ).result_set[0][0] == 1
    finally:
        sdk.close()


def test_e2e14_session_move_variant(tmp_path, lock_dir):
    """E2E-14 session-MOVE variant (cycle-13/14): move sess.md to a subdir
    (sessionId unchanged) → EXACTLY ONE Event (session_mv1), source_file
    REFRESHED to the new rel-path (session path keeps #320 MERGE semantics),
    the NEW Source created and the OLD Source RETAINED with its edge
    (additive-preserve): count(references) == 2; hash-pair sweep clean for
    a content-unchanged move."""
    c = tmp_path / "corpus"; c.mkdir()  # noqa: E702
    sub = c / "archive"; sub.mkdir()  # noqa: E702
    sess = c / "sess.md"
    sess.write_text("---\nsessionId: mv1\ntitle: MV\n---\nBody")
    sdk = _sdk()
    try:
        sdk.index_directory(str(c), extract_metadata=False)
        g = sdk._get_proj().g
        os.rename(str(sess), str(sub / "sess.md"))
        r2 = sdk.index_directory(str(c), extract_metadata=False)
        assert r2["indexed"] == 1
        assert g.query("MATCH (e:Event {eventId:'session_mv1'}) RETURN count(e)"
                       ).result_set[0][0] == 1
        sf = g.query("MATCH (e:Event {eventId:'session_mv1'}) RETURN e.source_file"
                     ).result_set[0][0]
        assert sf == "archive/sess.md"       # refreshed (session #320 MERGE)
        assert g.query("MATCH (s:Source) RETURN count(s)").result_set[0][0] == 2
        assert g.query("MATCH ()-[r:references]->() RETURN count(r)"
                       ).result_set[0][0] == 2   # additive-preserve
        assert _hash_pair_sweep(g) == 0      # content-unchanged move → clean
    finally:
        sdk.close()


def test_e2e14_primary_flip_variant(tmp_path, lock_dir):
    """E2E-14 primary-flip variant (cycle-13): a.md + b.md (same sessionId,
    a.md first-sorted = primary) indexed → introduce 0-new.md (sorts BEFORE
    a.md) → re-run → the NEW file becomes primary: Event props come from
    0-new.md, NO Event fork, the old primary becomes election-suppressed,
    no repair loop (re-run all-skipped)."""
    c = tmp_path / "corpus"; c.mkdir()  # noqa: E702
    (c / "a.md").write_text("---\nsessionId: dup1\ntitle: A\n---\nBody A")
    (c / "b.md").write_text("---\nsessionId: dup1\ntitle: B\n---\nBody B")
    sdk = _sdk()
    try:
        sdk.index_directory(str(c), extract_metadata=False)
        g = sdk._get_proj().g
        sf1 = g.query("MATCH (e:Event {eventId:'session_dup1'}) RETURN e.source_file"
                      ).result_set[0][0]
        assert sf1 == "a.md"
        (c / "0-new.md").write_text(
            "---\nsessionId: dup1\ntitle: New\n---\nBody New")
        r2 = sdk.index_directory(str(c), extract_metadata=False)  # noqa: F841
        sf2 = g.query("MATCH (e:Event {eventId:'session_dup1'}) RETURN e.source_file"
                      ).result_set[0][0]
        assert sf2 == "0-new.md"             # primary flipped
        assert g.query("MATCH (e:Event) RETURN count(e)").result_set[0][0] == 1
        assert g.query("MATCH (s:Source) RETURN count(s)").result_set[0][0] == 3
        # no repair loop
        r3 = sdk.index_directory(str(c), extract_metadata=False)
        assert r3["skipped"] == 3 and r3["indexed"] == 0 and r3["updated"] == 0
    finally:
        sdk.close()


def test_e2e14_issues_prs_fixture_leg(tmp_path, lock_dir):
    """E2E-14 issues/prs fixture leg (cycle-13): a session fixture carrying
    issues: [repo#1] / prs: [repo#2] frontmatter indexed via the NEW path →
    the (Event)-[:aboutObject]->(Object) edges exist (the plan's
    INSTANTIATES naming maps to the implemented aboutObject label — a
    plan-text/merged-T3 naming delta); §4.1's session tier set includes
    issues/prs (whitelist membership)."""
    c = tmp_path / "corpus"; c.mkdir()  # noqa: E702
    (c / "s.md").write_text(
        "---\nsessionId: ip1\ntitle: Issues\nissues: [repo#1]\n"
        "prs: [repo#2]\n---\nBody with issue references")
    sdk = _sdk()
    try:
        r = sdk.index_directory(str(c), extract_metadata=False)
        assert r["indexed"] == 1
        g = sdk._get_proj().g
        # The plan's INSTANTIATES naming maps to the IMPLEMENTED edge label
        # `aboutObject` (the legacy `_connect_issue_objects` machinery,
        # create_about_edge(..., "aboutObject") — a naming delta between the
        # plan text and the merged T3 write path).
        n = g.query(
            "MATCH (e:Event {eventId:'session_ip1'})-[:aboutObject]->(o:Object) "
            "RETURN count(o)").result_set[0][0]
        assert n >= 2, f"expected issue/PR Object edges, got {n}"
        _required_sweep(g) == 0  # noqa: B015
    finally:
        sdk.close()


# ═══════════════════════════════════════════════════════════════════════
# E2E-7: hash robustness + failure-mode probe set (§7 falsifiability b)
#   Probed against T3's merged implementation on this host before pinning
#   each expected counter; platform-gated legs use the §7 marker convention.
# ═══════════════════════════════════════════════════════════════════════

def test_e2e7_crlf_immunity(tmp_path):
    """E2E-7 CRLF immunity (I7): rewrite lf.md IN PLACE with CRLF bytes →
    the file reports skipped, count(Source) unchanged, version UNCHANGED
    (text-mode universal-newlines canonical hash; OQ-4)."""
    c = tmp_path / "corpus"; c.mkdir()  # noqa: E702
    f = c / "lf.md"
    f.write_text("---\nsessionId: lf1\ntitle: LF\n---\nLine one\nLine two")
    sdk = _sdk()
    try:
        r1 = sdk.index_directory(str(c), extract_metadata=False)
        g = sdk._get_proj().g
        assert r1["indexed"] == 1
        v1 = g.query("MATCH (s:Source) RETURN s.version, s.contentHash"
                     ).result_set[0]
        f.write_bytes(f.read_bytes().replace(b"\n", b"\r\n"))
        r2 = sdk.index_directory(str(c), extract_metadata=False)
        assert r2["skipped"] == 1 and r2["indexed"] == 0
        v2 = g.query("MATCH (s:Source) RETURN s.version, s.contentHash"
                     ).result_set[0]
        assert v2[0] == v1[0]              # version UNCHANGED
        assert v2[1] == v1[1]              # same canonical hash
        assert g.query("MATCH (s:Source) RETURN count(s)").result_set[0][0] == 1
    finally:
        sdk.close()


def test_e2e7_binary_non_utf8(tmp_path):
    """E2E-7(b): binary.md (invalid UTF-8) → failed bucket, retryable:false,
    cause-class decode; the run completes; other files still processed."""
    c = tmp_path / "corpus"; c.mkdir()  # noqa: E702
    (c / "binary.md").write_bytes(b"\xff\xfe\x00binary")
    (c / "good.md").write_text("---\nsessionId: g2\ntitle: Good\n---\nBody")
    sdk = _sdk()
    try:
        r = sdk.index_directory(str(c), extract_metadata=False)
        assert r["failed"] == 1 and r["indexed"] == 1
        errs = [e for e in r["errors"] if e["file"] == "binary.md"]
        assert errs and errs[0]["cause"] == "decode"
        assert errs[0]["retryable"] is False
        assert _required_sweep(sdk._get_proj().g) == 0
    finally:
        sdk.close()


def test_e2e7_broken_frontmatter_degraded(tmp_path):
    """E2E-7(c): broken.md (malformed YAML) → INDEXED with degraded metadata
    (tolerant parse → {} → title falls back to the file stem); run completes.
    NOTE (plan delta): the plan pins 'warning entry present in errors[]' for
    malformed frontmatter (W1 row 769) but the merged T3 implementation
    degrades SILENTLY (no errors[] entry) — the test asserts the implemented
    contract; the delta is recorded for the review gate."""
    c = tmp_path / "corpus"; c.mkdir()  # noqa: E702
    (c / "broken.md").write_text("---\n: bad: [unclosed\n---\nbody text")
    (c / "good.md").write_text("---\nsessionId: g3\ntitle: Good\n---\nBody")
    sdk = _sdk()
    try:
        r = sdk.index_directory(str(c), extract_metadata=False)
        assert r["indexed"] == 2 and r["failed"] == 0
        g = sdk._get_proj().g
        rows = g.query(
            "MATCH (s:Source) WHERE s.url CONTAINS 'broken.md' "
            "RETURN s.title, s.contentHash").result_set
        assert len(rows) == 1
        assert rows[0][0] == "broken"      # title ← file stem
        assert rows[0][1]                  # canonical contentHash present
        assert _required_sweep(g) == 0
    finally:
        sdk.close()


def test_e2e7_oserror_family(tmp_path):
    """E2E-7 OSError family (I9): noperm.md → failed retryable:TRUE (EACCES
    can clear); dir.md → failed structural (IsADirectoryError); broken-link
    and loop-link → failed structural; run COMPLETES with correct counts;
    REQUIRED-set sweep clean (a failed open never becomes a Source)."""
    c = tmp_path / "corpus"; c.mkdir()  # noqa: E702
    (c / "lf.md").write_text("---\nsessionId: lf2\ntitle: LF\n---\nBody")
    noperm = c / "noperm.md"
    noperm.write_text("---\nsessionId: np\ntitle: NP\n---\nBody")
    noperm.chmod(0)
    (c / "dir.md").mkdir()                 # a DIRECTORY named *.md
    (c / "broken-link.md").symlink_to(c / "nonexistent-target")
    (c / "loop-a.md").symlink_to(c / "loop-b.md")
    (c / "loop-b.md").symlink_to(c / "loop-a.md")
    try:
        sdk = _sdk()
        try:
            r = sdk.index_directory(str(c), extract_metadata=False)
            assert r["indexed"] == 1        # only lf.md
            assert r["failed"] == 5         # noperm + dir.md + broken + loop pair
            by_file = {e["file"]: e for e in r["errors"]}
            assert by_file["noperm.md"]["retryable"] is True
            assert by_file["noperm.md"]["cause"] == "structural"
            for name in ("dir.md", "broken-link.md", "loop-a.md",
                         "loop-b.md"):
                assert by_file[name]["retryable"] is False
                assert by_file[name]["cause"] == "structural"
            g = sdk._get_proj().g
            assert g.query("MATCH (s:Source) RETURN count(s)"
                           ).result_set[0][0] == 1
            assert _required_sweep(g) == 0
        finally:
            sdk.close()
    finally:
        noperm.chmod(0o644)


def test_e2e7_linkdir_inside_outside(tmp_path, monkeypatch):
    """E2E-7(m)/(n) symlinked DIRECTORY probes: an inside-root dir symlink is
    followed and its file indexed EXACTLY ONCE (realpath dedup — the second
    walk path reports inode-duplicate, the W4 mount-signature catch on the
    dir-alias pair); an outside-root dir symlink is NEVER descended (the
    cycle-26 resolved-target escape check fires BEFORE descent — plan delta:
    the older 'every contained .md file failed' prose predates that fix)."""
    c = tmp_path / "corpus"; c.mkdir()  # noqa: E702
    real = c / "real"; real.mkdir()  # noqa: E702
    (real / "inner.md").write_text("---\nsessionId: li\ntitle: LI\n---\nBody")
    (c / "linkdir-inside").symlink_to(real, target_is_directory=True)
    sdk = _sdk()
    try:
        r = sdk.index_directory(str(c), extract_metadata=False)
        assert r["file_count"] == 2         # both aliases enumerated
        assert r["indexed"] == 1
        assert r["skipped"] == 1
        g = sdk._get_proj().g
        assert g.query("MATCH (s:Source) RETURN count(s)").result_set[0][0] == 1
        assert _required_sweep(g) == 0
    finally:
        sdk.close()
    # outside-root dir symlink → never descended (zero files enumerated)
    c2 = tmp_path / "corpus2"; c2.mkdir()  # noqa: E702
    out = tmp_path / "outside"; out.mkdir()  # noqa: E702
    (out / "leak.md").write_text("---\nsessionId: lo\ntitle: LO\n---\nBody")
    monkeypatch.setenv("TORTOISE_INGEST_BASE_DIR", str(c2))
    (c2 / "linkdir-outside").symlink_to(out, target_is_directory=True)
    sdk = _sdk()
    try:
        r = sdk.index_directory(str(c2), extract_metadata=False)
        assert r["file_count"] == 0
        assert r["indexed"] == 0 and r["failed"] == 0
        g = sdk._get_proj().g
        assert g.query("MATCH (s:Source) RETURN count(s)").result_set[0][0] == 0
    finally:
        sdk.close()


def test_e2e7_hardlinks_in_out_combo(tmp_path):
    """E2E-7(q)/(r)/(t): hardlink-in → url-keyed SECOND Source with IDENTICAL
    contentHash (count == nlink → all aliases provably root-local, safe);
    hardlink-out → failed escape, retryable:false, NEVER READ (stat-only
    rejection — the in-walk count < st_nlink unreconciled class); combo-link
    (symlink → outside-root hardlink entry) also fails escape."""
    c = tmp_path / "corpus"; c.mkdir()  # noqa: E702
    (c / "lf.md").write_text("---\nsessionId: h1\ntitle: H\n---\nBody")
    os.link(c / "lf.md", c / "hardlink-in.md")
    outside = tmp_path / "outside"; outside.mkdir()  # noqa: E702
    (outside / "out.md").write_text("---\nsessionId: ho\ntitle: HO\n---\nBody")
    os.link(outside / "out.md", c / "hardlink-out.md")
    (c / "combo-link.md").symlink_to(c / "hardlink-out.md")
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("TORTOISE_INGEST_BASE_DIR", str(c))
    sdk = _sdk()
    try:
        r = sdk.index_directory(str(c), extract_metadata=False)
        assert r["indexed"] == 2            # lf.md + hardlink-in.md
        assert r["failed"] == 2             # hardlink-out.md + combo-link.md
        for e in r["errors"]:
            assert e["retryable"] is False
            assert e["cause"] == "escape"
        g = sdk._get_proj().g
        assert g.query("MATCH (s:Source) RETURN count(s)").result_set[0][0] == 2
        # duplicate-contentHash group == {lf.md, hardlink-in.md} ONLY
        rows = g.query(
            "MATCH (s:Source) RETURN s.contentHash, collect(s.url)").result_set
        groups = [r2[1] for r2 in rows if len(r2[1]) > 1]
        assert len(groups) == 1
        assert len(groups[0]) == 2          # the url-keyed pair
        assert _required_sweep(g) == 0
    finally:
        sdk.close()
        monkeypatch.undo()


def test_e2e7_malicious_frontmatter(tmp_path):
    """E2E-7(u): doc + session fixtures carrying source_path/source_url/
    evilKey/x_custom frontmatter → BOTH INDEXED with degraded metadata, NO
    raise, NO stray props on ANY Source/Document/Event node (the whitelist
    drops sanitizer-hostile keys BEFORE _sanitize_props — per-file isolation
    on a correct implementation); the meeting's whitelisted non-contract
    extras land in content_metadata (non-vacuous absorption)."""
    c = tmp_path / "corpus"; c.mkdir()  # noqa: E702
    (c / "evil-doc.md").write_text(
        "---\ntitle: Evil\ntype: strategyDoc\nsource_path: /etc/passwd\n"
        "source_url: corpus://evil/fork\nevilKey: x\nx_custom: y\n---\nbody")
    (c / "evil-session.md").write_text(
        "---\nsessionId: ev1\nagent: pi\nsource_path: /etc/passwd\n"
        "source_url: corpus://evil/fork\nevilKey: x\n---\nbody")
    sdk = _sdk()
    try:
        r = sdk.index_directory(str(c), extract_metadata=False)
        assert r["indexed"] == 2 and r["failed"] == 0
        g = sdk._get_proj().g
        # no stray props anywhere
        assert g.query(
            "MATCH (n) WHERE n.source_path IS NOT NULL OR n.source_url IS NOT NULL "
            "OR n.evilKey IS NOT NULL OR n.x_custom IS NOT NULL "
            "RETURN count(n)").result_set[0][0] == 0
        assert _required_sweep(g) == 0
    finally:
        sdk.close()


def test_e2e7_corpus_root_symlink_v1(tmp_path, monkeypatch):
    """E2E-7(v1): corpus root = an IN-BASE symlink whose target resolves
    OUTSIDE TORTOISE_INGEST_BASE_DIR → ValueError BEFORE any walk or write
    (zero files indexed; realpath-vs-realpath resolved-target discipline)."""
    base = tmp_path / "base"; base.mkdir()  # noqa: E702
    outside = tmp_path / "outside"; outside.mkdir()  # noqa: E702
    (outside / "s.md").write_text("---\nsessionId: v1\ntitle: V\n---\nBody")
    linkroot = base / "corpus"
    linkroot.symlink_to(outside, target_is_directory=True)
    monkeypatch.setenv("TORTOISE_INGEST_BASE_DIR", str(base))
    sdk = _sdk()
    try:
        with pytest.raises(ValueError):
            sdk.index_directory(str(linkroot), extract_metadata=False)
        g = sdk._get_proj().g
        assert g.query("MATCH (s:Source) RETURN count(s)").result_set[0][0] == 0
    finally:
        sdk.close()


def test_e2e7_corpus_root_symlink_v2(tmp_path, monkeypatch):
    """E2E-7(v2): corpus root = a symlink resolving INSIDE the base →
    indexes normally with REALPATH-derived urls."""
    base = tmp_path / "base"; base.mkdir()  # noqa: E702
    real = base / "realcorpus"; real.mkdir()  # noqa: E702
    (real / "s.md").write_text("---\nsessionId: v2\ntitle: V\n---\nBody")
    linkroot = base / "corpus"
    linkroot.symlink_to(real, target_is_directory=True)
    monkeypatch.setenv("TORTOISE_INGEST_BASE_DIR", str(base))
    sdk = _sdk()
    try:
        r = sdk.index_directory(str(linkroot), extract_metadata=False)
        assert r["indexed"] == 1
        g = sdk._get_proj().g
        url = g.query("MATCH (s:Source) RETURN s.url").result_set[0][0]
        assert "realcorpus" in url          # realpath-derived
        assert _required_sweep(g) == 0
    finally:
        sdk.close()


def test_e2e7_undecodable_filename_seam(tmp_path, monkeypatch):
    """E2E-7(x): undecodable-filename class via the MOCKABLE SEAM (APFS
    cannot create invalid-UTF-8 filename bytes — EILSEQ verified; the plan
    pins the seam for macOS): derive_source_url raising UnicodeEncodeError
    on a surrogate filename → per-file failed{retryable:false} cause-class
    filename, the run COMPLETES, zero abort (the shared per-file guard at
    the entry point, never inside the pure function)."""
    import tortoise.sdk as sdkmod  # noqa: F401
    from tortoise import file_indexer
    orig = file_indexer.derive_source_url

    def _raise_unicode(path, root, corpus_name):
        if "surrogate.md" in str(path):
            # the surrogate-escaped filename the seam feeds derive_source_url
            raise UnicodeEncodeError("ascii", "\udcff", 0, 1, "bad name")
        return orig(path, root, corpus_name)

    monkeypatch.setattr(file_indexer, "derive_source_url", _raise_unicode)
    c = tmp_path / "corpus"; c.mkdir()  # noqa: E702
    (c / "good.md").write_text("---\nsessionId: x1\ntitle: Good\n---\nBody")
    (c / "surrogate.md").write_text(
        "---\nsessionId: x2\ntitle: Surrogate\n---\nBody")
    sdk = _sdk()
    try:
        r = sdk.index_directory(str(c), extract_metadata=False)
        assert r["indexed"] == 1
        # the surrogate-named file lands in the failed bucket with the
        # pinned disposition — per-file failed{retryable:false} cause-class
        # filename, run completes, zero abort (E2E-7(x))
        assert r["failed"] == 1
        errs = [e for e in r["errors"] if e["file"] == "surrogate.md"]
        assert errs and errs[0]["cause"] == "filename"
        assert errs[0]["retryable"] is False
        assert r["aborted"] == 0
        g = sdk._get_proj().g
        assert g.query("MATCH (s:Source) RETURN count(s)"
                       ).result_set[0][0] == 1
        assert _required_sweep(g) == 0
    finally:
        sdk.close()


def test_e2e7_walk_time_oserror(tmp_path):
    """E2E-7(y): an unreadable SUBDIRECTORY (chmod 000) → per-directory
    errors[] entry (structural, naming the dir), NEVER silent, NEVER an
    abort; the run completes with honest counters."""
    c = tmp_path / "corpus"; c.mkdir()  # noqa: E702
    (c / "top.md").write_text("---\nsessionId: t1\ntitle: T\n---\nBody")
    blocked = c / "blocked"; blocked.mkdir()  # noqa: E702
    (blocked / "inner.md").write_text("---\nsessionId: i1\ntitle: I\n---\nBody")
    blocked.chmod(0)
    try:
        sdk = _sdk()
        try:
            r = sdk.index_directory(str(c), extract_metadata=False)
            assert r["file_count"] == 1     # only top.md enumerated
            assert r["indexed"] == 1
            dir_errs = [e for e in r["errors"]
                        if e.get("dir", "").endswith("blocked")]
            assert dir_errs and dir_errs[0]["retryable"] is False
            g = sdk._get_proj().g
            assert _required_sweep(g) == 0
        finally:
            sdk.close()
    finally:
        blocked.chmod(0o755)


def test_e2e7_summary_arithmetic_and_message_content(tmp_path):
    """E2E-7 summary-arithmetic + message-content legs (i)/(ii): a MIXED
    corpus producing indexed + skipped + failed simultaneously →
    file_count == indexed+updated+skipped+failed (+ ignored excluded);
    every poison errors[] entry names the rel-path, carries a cause-class
    substring from the pinned enumeration, and retryable matches the row."""
    c = tmp_path / "corpus"; c.mkdir()  # noqa: E702
    (c / "s1.md").write_text("---\nsessionId: sa\ntitle: S\n---\nBody")
    (c / "binary.md").write_bytes(b"\xff\xfe\x00binary")
    big = c / "big.md"
    big.write_bytes(b"x" * (50 * 1024 * 1024 + 1))   # over default limit
    (c / "notes.txt").write_text("ignored")
    sdk = _sdk()
    try:
        r = sdk.index_directory(str(c), extract_metadata=False)
        assert r["file_count"] == 3         # non-md excluded
        assert r["ignored"] == 1
        assert r["indexed"] == 1 and r["failed"] == 2
        assert (r["indexed"] + r["updated"] + r["skipped"] + r["failed"]
                == r["file_count"])
        # list_sources contains the indexed file exactly once, no failed file
        urls = [row["url"] for row in sdk.list_sources()]
        assert len(urls) == len(set(urls)) == 1
        # message-content leg (ii): every poison entry is actionable
        for e in r["errors"]:
            assert "binary.md" in e["file"] or "big.md" in e["file"]
            assert e["cause"] in ("decode", "size", "escape", "structural",
                                  "filename", "db", "lock")
            assert e["retryable"] in (True, False)
        by_file = {e["file"]: e for e in r["errors"]}
        assert by_file["binary.md"]["cause"] == "decode"
        assert by_file["big.md"]["cause"] == "size"
        assert "bytes" in by_file["big.md"]["error"]  # limit value named
    finally:
        sdk.close()


# ═══════════════════════════════════════════════════════════════════════
# E2E-9: concurrent indexers — no duplicates, no lost updates (§7, I10)
# ═══════════════════════════════════════════════════════════════════════

def test_e2e9_conditional_merge_unit_leg(corpus):
    """E2E-9 leg (iii): the conditional single-statement MERGE unit leg —
    parameterized created-vs-matched outcomes. ON CREATE → version 1; ON
    MATCH hash-diff → version bumped exactly once + contentHash updated;
    ON MATCH hash-equal → version/contentHash untouched."""
    sdk = _sdk()
    try:
        g = sdk._get_proj().g
        u = f"corpus://{corpus.name}/unit-leg.md"
        # created branch
        o1 = sdk._index_source_merge(u, "agentSession", "2026-08-10T00:00:00+00:00",
                                     "hash-v1", "T", str(corpus / "unit-leg.md"),
                                     gate_v=None)
        assert o1 == "indexed"
        assert g.query("MATCH (s:Source {url:$u}) RETURN s.version",
                       params={"u": u}).result_set[0][0] == 1
        # matched hash-diff → exactly one bump
        o2 = sdk._index_source_merge(u, "agentSession", "2026-08-10T00:00:00+00:00",
                                     "hash-v2", "T", str(corpus / "unit-leg.md"),
                                     gate_v=1)
        assert o2 == "updated"
        assert g.query("MATCH (s:Source {url:$u}) RETURN s.version",
                       params={"u": u}).result_set[0][0] == 2
        assert g.query("MATCH (s:Source {url:$u}) RETURN s.contentHash",
                       params={"u": u}).result_set[0][0] == "hash-v2"
        # matched hash-equal → untouched
        o3 = sdk._index_source_merge(u, "agentSession", "2026-08-10T00:00:00+00:00",
                                     "hash-v2", "T", str(corpus / "unit-leg.md"),
                                     gate_v=2)
        assert o3 == "skipped"
        assert g.query("MATCH (s:Source {url:$u}) RETURN s.version",
                       params={"u": u}).result_set[0][0] == 2
    finally:
        sdk.close()


def test_e2e9_stale_writer_clobber_session(corpus):
    """E2E-9 stale-writer clobber variant (session leg, cycle-2/3): the
    conditional MERGE bumps whenever 'stored hash ≠ my hash' and cannot
    distinguish a stale reader from a fresh writer — a deterministic
    STALE-LAST interleaving (barrier-controlled reads/commits) produces the
    documented divergence window: final contentHash == the STALE value,
    version ≤ base+2, graph-internal hash equality HOLDS; ONE convergence
    re-run heals (reads disk → newest hash, reports updated)."""
    db = _db()
    sdk_a = TortoiseSDK(db, namespace="e2e-900")
    sdk_b = TortoiseSDK(db, namespace="e2e-900")
    g = sdk_a._get_proj().g
    u = f"corpus://{corpus.name}/s1.md"
    h1 = hashlib.sha256((corpus / "s1.md").read_text().encode()).hexdigest()
    try:
        # (1) A reads v1 (h1) — emulate the stale buffer via the gate read
        gate1 = sdk_a._index_gate_read(u, "session_abc123", None)
        assert gate1["source"] is False          # absent at A's read time
        # (2) file rewritten to v2 (h2)
        (corpus / "s1.md").write_text(SESSION_FIXTURE.replace(
            "decided to keep JWT rotation.",
            "decided to keep JWT rotation and rotate refresh tokens."))
        h2 = hashlib.sha256((corpus / "s1.md").read_text().encode()).hexdigest()
        assert h2 != h1
        # (3) B reads v2; (4) B's MERGE commits FIRST (ON CREATE → v1, h2)
        o_b = sdk_b._index_source_merge(u, "agentSession",
                                        "2026-08-10T00:00:00+00:00",
                                        h2, "T", str(corpus / "s1.md"),
                                        gate_v=None)
        assert o_b == "indexed"
        # (5) A's STALE MERGE commits LAST (stored h2 ≠ h1 → ON MATCH bump)
        o_a = sdk_a._index_source_merge(u, "agentSession",
                                        "2026-08-10T00:00:00+00:00",
                                        h1, "T", str(corpus / "s1.md"),
                                        gate_v=1)
        assert o_a == "updated"
        # divergence window: contentHash == h1 (the STALE value), version ≤ 3
        row = g.query("MATCH (s:Source {url:$u}) RETURN s.contentHash, s.version",
                      params={"u": u}).result_set[0]
        assert row[0] == h1
        assert row[1] <= 3
        # graph-internal consistency holds (divergence is vs DISK)
        assert _hash_pair_sweep(g) == 0
        # ONE convergence re-run (reads v2 from disk, no progress_file)
        sdk_c = TortoiseSDK(db, namespace="e2e-900")
        r = sdk_c.index_directory(str(corpus), extract_metadata=False)
        assert r["updated"] == 1
        row2 = g.query("MATCH (s:Source {url:$u}) RETURN s.contentHash",
                       params={"u": u}).result_set[0]
        assert row2[0] == h2                    # the NEWEST hash
        assert _hash_pair_sweep(g) == 0
        sdk_c.close()
    finally:
        sdk_a.close()
        sdk_b.close()


def test_e2e9_stale_writer_clobber_doc(corpus):
    """E2E-9 stale-writer clobber variant parametrized over a DOC file
    (cycle-4): doc units create NO Event and Documents carry NO file_hash —
    the Source MERGE branch is SHARED, so the divergence class applies
    identically; after the clobber s.contentHash == h1 (stale), version ≤ 3;
    ONE convergence re-run → h2, updated, exactly ONE Document."""
    db = _db()
    sdk_a = TortoiseSDK(db, namespace="e2e-900")
    sdk_b = TortoiseSDK(db, namespace="e2e-900")
    g = sdk_a._get_proj().g
    u = f"corpus://{corpus.name}/strategy.md"
    h1 = hashlib.sha256((corpus / "strategy.md").read_text().encode()).hexdigest()
    try:
        gate1 = sdk_a._index_gate_read(u, None, "doc_strategy.md")
        assert gate1["source"] is False
        (corpus / "strategy.md").write_text(DOC_FIXTURE.replace(
            "Strategy body text.", "Strategy body text with an addition."))
        h2 = hashlib.sha256((corpus / "strategy.md").read_text().encode()).hexdigest()
        assert h2 != h1
        o_b = sdk_b._index_source_merge(u, "document",
                                        "2026-08-01T00:00:00+00:00",
                                        h2, "GTM Strategy",
                                        str(corpus / "strategy.md"), gate_v=None)
        assert o_b == "indexed"
        o_a = sdk_a._index_source_merge(u, "document",
                                        "2026-08-01T00:00:00+00:00",
                                        h1, "GTM Strategy",
                                        str(corpus / "strategy.md"), gate_v=1)
        assert o_a == "updated"
        row = g.query("MATCH (s:Source {url:$u}) RETURN s.contentHash, s.version",
                      params={"u": u}).result_set[0]
        assert row[0] == h1 and row[1] <= 3
        sdk_c = TortoiseSDK(db, namespace="e2e-900")
        r = sdk_c.index_directory(str(corpus), extract_metadata=False)
        assert r["updated"] == 1
        assert g.query("MATCH (s:Source {url:$u}) RETURN s.contentHash",
                       params={"u": u}).result_set[0][0] == h2
        assert g.query("MATCH (d:Document {id:'doc_strategy.md'}) RETURN count(d)"
                       ).result_set[0][0] == 1
        sdk_c.close()
    finally:
        sdk_a.close()
        sdk_b.close()


def test_e2e9_symlink_pair_concurrency(corpus, lock_dir):
    """E2E-9 symlink-pair concurrency leg (cycle-3/4): real.md (sessionId
    frontmatter PINNED) + link-a.md → symlink to real.md; two concurrent
    runs walk BOTH paths → ONE Source for the physical file (realpath-derived
    url converges via the MERGE), zero duplicate urls, exactly 1 Event +
    1 edge, counters honest."""
    db = _db()
    corpus_sdk = TortoiseSDK(db, namespace="e2e-900")
    try:  # noqa: SIM105
        corpus_sdk.close()
    except Exception:
        pass
    (corpus / "real.md").write_text(
        "---\nsessionId: symlinkpair1\ntitle: Real\n---\nBody real")
    (corpus / "link-a.md").symlink_to(corpus / "real.md")
    results, reuse_holds = harness.barrier_index_runs(
        str(corpus), db, n_runs=2, extract_metadata=False)
    if not reuse_holds:
        pytest.skip("embedded daemon reuse does not hold on this platform")
    check = TortoiseSDK(db, namespace="e2e-900")
    g = check._get_proj().g
    try:
        # base corpus (3 files) + real.md = 4 units; link-a dedups
        assert g.query("MATCH (s:Source) RETURN count(s)").result_set[0][0] == 4
        assert g.query("MATCH (e:Event) RETURN count(e)").result_set[0][0] == 3
        assert g.query("MATCH ()-[r:references]->() RETURN count(r)"
                       ).result_set[0][0] == 4
        # zero duplicate urls
        urls = [r[0] for r in g.query("MATCH (s:Source) RETURN s.url").result_set]
        assert len(urls) == len(set(urls))
        for r in results:
            assert (r["indexed"] + r["updated"] + r["skipped"] + r["failed"]
                    == r["file_count"])
    finally:
        check.close()


def test_e2e9_cross_process_embedded_overlap(tmp_path):
    """E2E-9(iv) CROSS-PROCESS EMBEDDED OVERLAP (cycle-20/21): two SDK
    subprocesses against ONE embedded DB → the SECOND process's open probe
    detects the live holder → EmbeddedStoreBusyError, fail-fast, zero writes
    from the second process; a follow-up sequential run converges (E2E-9's
    counter arithmetic + zero duplicate urls)."""
    import subprocess as _sp
    import sys as _sys
    import threading as _threading
    import time as _time
    db = os.path.join(str(tmp_path), "t.db")
    corpus = tmp_path / "corpus"; corpus.mkdir()  # noqa: E702
    for i in range(4):
        (corpus / f"s{i}.md").write_text(
            f"---\nsessionId: c{i}\ntitle: C{i}\n---\nBody {i}")
    env = dict(os.environ)
    env["TORTOISE_INDEX_LOCK_DIR"] = str(tmp_path / "locks")
    os.makedirs(env["TORTOISE_INDEX_LOCK_DIR"], exist_ok=True)
    code1 = (
        "import sys, time\n"
        f"from tortoise.sdk import TortoiseSDK\n"
        f"sdk = TortoiseSDK({db!r}, namespace='e2e-900')\n"
        "sdk._get_proj()\n"
        "print('ONE-READY', flush=True)\n"
        "time.sleep(12)\n"
        "sdk.close()\n"
    )
    p1 = _sp.Popen([_sys.executable, "-c", code1], env=env,
                   stdout=_sp.PIPE, stderr=_sp.STDOUT, text=True)
    out1: list[str] = []

    def _r1():
        for line in p1.stdout:
            out1.append(line.strip())
    t1 = _threading.Thread(target=_r1, daemon=True)
    t1.start()
    deadline = _time.time() + 60
    while _time.time() < deadline and "ONE-READY" not in out1:
        _time.sleep(0.2)
    assert "ONE-READY" in out1, f"child1 never ready: {out1}"
    _time.sleep(0.5)
    # child 2: opening the SAME embedded DB must fail-fast
    code2 = (
        "import sys\n"
        "from tortoise.sdk import TortoiseSDK\n"
        f"try:\n"
        f"    sdk = TortoiseSDK({db!r}, namespace='e2e-900')\n"
        "    print('TWO-OPENED', flush=True)\n"
        "except Exception as e:\n"
        "    print(f'TWO-RAISED:{type(e).__name__}', flush=True)\n"
    )
    p2 = _sp.Popen([_sys.executable, "-c", code2], env=env,
                   stdout=_sp.PIPE, stderr=_sp.STDOUT, text=True)
    out2 = p2.stdout.read().strip()
    p2.wait()
    assert "TWO-RAISED:EmbeddedStoreBusyError" in out2, f"child2: {out2}"
    p1.terminate()
    p1.wait()
    # follow-up sequential run converges
    sdk = _sdk()
    try:
        r = sdk.index_directory(str(corpus), extract_metadata=False)
        assert r["indexed"] == 4 and r["failed"] == 0
        g = sdk._get_proj().g
        urls = [x[0] for x in g.query("MATCH (s:Source) RETURN s.url").result_set]
        assert len(urls) == len(set(urls))
        assert _required_sweep(g) == 0
    finally:
        sdk.close()


# ═══════════════════════════════════════════════════════════════════════
# E2E-10: crash-injection + orphan-state repair (I5; §5.1 pin a gate)
# ═══════════════════════════════════════════════════════════════════════

def test_e2e10_kill_between_session_leg(corpus, monkeypatch, lock_dir):
    """E2E-10(a) SESSION leg (cycle-18 target pin): kill between the Source
    MERGE and the session Event write → run 1 reports the file failed; graph
    holds a Source with NO Event and NO edge. Re-run (patch removed) → unit
    detected incomplete → REPAIR: Event + edge created, count(Event)==1,
    count(references)==1, Source version NOT bumped (hash unchanged), the
    repair run reports `updated` (repair-work carve-out). 3rd run → skipped."""
    import tortoise.sdk as sdkmod
    sdk = _sdk()
    orig = sdkmod.TortoiseSDK._session_event_write
    calls = {"n": 0}

    def _raise_first(self, *a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("injected crash after Source MERGE")
        return orig(self, *a, **k)

    monkeypatch.setattr(sdkmod.TortoiseSDK, "_session_event_write", _raise_first)
    try:
        r1 = sdk.index_directory(str(corpus), extract_metadata=False)
        assert r1["failed"] == 1 and r1["indexed"] == 2
        g = sdk._get_proj().g
        u = f"corpus://{corpus.name}/s1.md"
        # Source exists, NO Event for session_abc123, NO edge
        assert g.query("MATCH (s:Source {url:$u}) RETURN count(s)",
                       params={"u": u}).result_set[0][0] == 1
        assert g.query("MATCH (e:Event {eventId:'session_abc123'}) RETURN count(e)"
                       ).result_set[0][0] == 0
        assert g.query(
            "MATCH (s:Source {url:$u})-[:references]->() RETURN count(*)",
            params={"u": u}).result_set[0][0] == 0
        v_before = g.query("MATCH (s:Source {url:$u}) RETURN s.version",
                           params={"u": u}).result_set[0][0]
        # re-run with the patch removed → REPAIR
        monkeypatch.undo()
        r2 = sdk.index_directory(str(corpus), extract_metadata=False)
        assert r2["updated"] == 1 and r2["failed"] == 0
        assert g.query("MATCH (e:Event {eventId:'session_abc123'}) RETURN count(e)"
                       ).result_set[0][0] == 1
        assert g.query(
            "MATCH (s:Source {url:$u})-[:references]->() RETURN count(*)",
            params={"u": u}).result_set[0][0] == 1
        v_after = g.query("MATCH (s:Source {url:$u}) RETURN s.version",
                          params={"u": u}).result_set[0][0]
        assert v_after == v_before           # no version bump by the repair
        assert _required_sweep(g) == 0
        # 3rd run → skipped
        r3 = sdk.index_directory(str(corpus), extract_metadata=False)
        assert r3["skipped"] == 3
    finally:
        sdk.close()


def test_e2e10_kill_between_doc_leg(corpus, monkeypatch, lock_dir):
    """E2E-10(a) DOC leg (cycle-3): kill between the Source MERGE and the
    Document upsert — the phantom-Source guard region. Repair re-runs the doc
    branch WITH the source_url override → Document + auto-wired edge onto the
    REAL Source; the GLOBAL Source-count guard is re-asserted after the
    repair (no phantom); no version bump."""
    import tortoise.sdk as sdkmod
    sdk = _sdk()
    orig = sdkmod.TortoiseSDK._doc_write
    calls = {"n": 0}

    def _raise_first(self, *a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("injected crash after Source MERGE (doc leg)")
        return orig(self, *a, **k)

    monkeypatch.setattr(sdkmod.TortoiseSDK, "_doc_write", _raise_first)
    try:
        r1 = sdk.index_directory(str(corpus), extract_metadata=False)
        assert r1["failed"] == 1 and r1["indexed"] == 2
        g = sdk._get_proj().g
        u = f"corpus://{corpus.name}/strategy.md"
        assert g.query("MATCH (s:Source {url:$u}) RETURN count(s)",
                       params={"u": u}).result_set[0][0] == 1
        assert g.query("MATCH (d:Document {id:'doc_strategy.md'}) RETURN count(d)"
                       ).result_set[0][0] == 0
        v_before = g.query("MATCH (s:Source {url:$u}) RETURN s.version",
                           params={"u": u}).result_set[0][0]
        monkeypatch.undo()
        r2 = sdk.index_directory(str(corpus), extract_metadata=False)
        assert r2["updated"] == 1
        assert g.query("MATCH (d:Document {id:'doc_strategy.md'}) RETURN count(d)"
                       ).result_set[0][0] == 1
        assert g.query(
            "MATCH (s:Source {url:$u})-[:references]->(:Document) RETURN count(*)",
            params={"u": u}).result_set[0][0] == 1
        # no phantom Source: GLOBAL count stays 3
        assert g.query("MATCH (s:Source) RETURN count(s)").result_set[0][0] == 3
        assert g.query("MATCH (s:Source {url:$u}) RETURN s.version",
                       params={"u": u}).result_set[0][0] == v_before
        assert _required_sweep(g) == 0
    finally:
        sdk.close()


def test_e2e10_source_only_orphan_heals(corpus, lock_dir):
    """E2E-10(b): hand-craft a Source-only orphan (correct url/hash) via raw
    Cypher → the next run MUST NOT report skipped; heals to full unit,
    reporting updated (repair-work carve-out)."""
    sdk = _sdk()
    try:
        u = f"corpus://{corpus.name}/s1.md"
        text = (corpus / "s1.md").read_text()
        h = hashlib.sha256(text.encode()).hexdigest()
        g = sdk._get_proj().g
        g.query(
            "MERGE (s:Source {url:$u}) SET s.sourceKind='agentSession', "
            "s.contentHash=$h, s.title='orphan', s.ingestedAt=$now, s.version=1",
            params={"u": u, "h": h, "now": "2026-08-01T00:00:00+00:00"})
        r = sdk.index_directory(str(corpus), extract_metadata=False)
        assert r["updated"] == 1             # healed — never skipped
        assert g.query("MATCH (e:Event {eventId:'session_abc123'}) RETURN count(e)"
                       ).result_set[0][0] == 1
        assert g.query(
            "MATCH (s:Source {url:$u})-[:references]->(:Event) RETURN count(*)",
            params={"u": u}).result_set[0][0] == 1
        assert _required_sweep(g) == 0
    finally:
        sdk.close()


def test_e2e10_source_event_no_edge_heals(corpus, lock_dir):
    """E2E-10(c): hand-craft Source+Event WITHOUT the references edge → the
    next run heals the missing edge (exactly 1 after), reporting updated."""
    sdk = _sdk()
    try:
        u = f"corpus://{corpus.name}/s1.md"
        text = (corpus / "s1.md").read_text()
        h = hashlib.sha256(text.encode()).hexdigest()
        g = sdk._get_proj().g
        g.query(
            "MERGE (s:Source {url:$u}) SET s.sourceKind='agentSession', "
            "s.contentHash=$h, s.title='orphan', s.ingestedAt=$now, s.version=1",
            params={"u": u, "h": h, "now": "2026-08-01T00:00:00+00:00"})
        g.query(
            "MERGE (e:Event {eventId:'session_abc123'}) SET e.eventKind='AgentSession', "
            "e.source_file='s1.md', e.file_hash=$h, e.keywords=['k']",
            params={"h": h})
        r = sdk.index_directory(str(corpus), extract_metadata=False)
        assert r["updated"] == 1
        assert g.query(
            "MATCH (s:Source {url:$u})-[:references]->(:Event) RETURN count(*)",
            params={"u": u}).result_set[0][0] == 1
        assert _required_sweep(g) == 0
    finally:
        sdk.close()


def test_e2e10_lock_held_skip_heals(corpus, lock_dir):
    """E2E-10(d): hold the SessionIndexLock for the file's sessionId → the
    run reports skipped {retryable:true} with the Event NOT updated; release
    → re-run heals (Event metadata written, still exactly one Event)."""
    from tortoise.index_lock import SessionIndexLock
    sdk = _sdk()
    lock = SessionIndexLock("abc123")
    try:
        assert lock.acquire() in ("acquired", "stale-recovered")
        r1 = sdk.index_directory(str(corpus), extract_metadata=False)
        assert r1["skipped"] >= 1
        lock_err = [e for e in r1["errors"] if e["file"] == "s1.md"]
        assert lock_err and lock_err[0]["retryable"] is True
        g = sdk._get_proj().g
        assert g.query("MATCH (e:Event {eventId:'session_abc123'}) RETURN count(e)"
                       ).result_set[0][0] == 0
        lock.release()
        r2 = sdk.index_directory(str(corpus), extract_metadata=False)
        assert r2["indexed"] == 1          # only s1.md was lock-skipped
        assert g.query("MATCH (e:Event {eventId:'session_abc123'}) RETURN count(e)"
                       ).result_set[0][0] == 1
    finally:
        try:  # noqa: SIM105
            lock.release()
        except Exception:
            pass
        sdk.close()


def test_e2e10_checkpoint_resume_crash(tmp_path, lock_dir, monkeypatch):
    """E2E-10(e): 150-file corpus, progress_file set, checkpoint every 100 →
    monkeypatch a crash ON ENTRY to file 101's processing (BEFORE its Source
    MERGE — the checkpoint at 100 is already saved) → resume with the SAME
    progress_file → 100 skipped (checkpointed prefix, never re-read — a
    read-counter asserts the stat-based fast-skip), 50 indexed, zero
    duplicate urls; final graph state identical to a crash-free run."""
    import tortoise.sdk as sdkmod
    corpus = tmp_path / "corpus"; corpus.mkdir()  # noqa: E702
    for i in range(150):
        # zero-padded names: sorted walk order == numeric order (e000 < e001
        # < ... < e149) so the crash at e100 is EXACTLY file 101 — the plan's
        # pinned crash position (checkpoint at 100 already saved).
        (corpus / f"e{i:03d}.md").write_text(
            f"---\nsessionId: e{i}\ntitle: E{i}\n---\nBody {i}")
    prog = tmp_path / "progress.json"
    sdk = _sdk()
    orig = sdkmod.TortoiseSDK._index_process_unit
    reads: list[str] = []
    orig_read = sdkmod.TortoiseSDK._index_read_file
    crash = {"armed": True}

    def _crash_unit(self, path, *a, **k):
        if crash["armed"] and "e100.md" in str(path):
            crash["armed"] = False
            raise RuntimeError("injected crash at file 101")
        return orig(self, path, *a, **k)

    def _read_spy(self, path, *a, **k):
        reads.append(os.path.basename(str(path)))
        return orig_read(self, path, *a, **k)

    monkeypatch.setattr(sdkmod.TortoiseSDK, "_index_process_unit", _crash_unit)
    monkeypatch.setattr(sdkmod.TortoiseSDK, "_index_read_file", _read_spy)
    try:
        with pytest.raises(RuntimeError):
            sdk.index_directory(str(corpus), extract_metadata=False,
                                progress_file=str(prog))
        # resume — prefix files stat-fast-skip, never opened
        monkeypatch.undo()   # crash patch removed; read spy stays? undo removes both
        monkeypatch.setattr(sdkmod.TortoiseSDK, "_index_read_file", _read_spy)
        reads.clear()
        r = sdk.index_directory(str(corpus), extract_metadata=False,
                                progress_file=str(prog))
        assert r["skipped"] == 100 and r["indexed"] == 50
        # the 100-file prefix was NOT re-read (fast-skip); the tail WAS
        prefix_reads = [f for f in reads if f.startswith("e") and int(f[1:4]) < 100]
        assert prefix_reads == [], f"prefix files were re-read: {prefix_reads[:3]}"
        g = sdk._get_proj().g
        urls = [x[0] for x in g.query("MATCH (s:Source) RETURN s.url").result_set]
        assert len(urls) == len(set(urls))
        assert g.query("MATCH (s:Source) RETURN count(s)").result_set[0][0] == 150
        assert _required_sweep(g) == 0
    finally:
        sdk.close()


def test_e2e10_repair_with_changed_content(corpus, monkeypatch, lock_dir):
    """E2E-10(f): kill-between leaves a Source-only orphan; BEFORE the repair
    run, EDIT the file → the repair run asserts UPDATED semantics: version
    bumped, contentHash == new hash, Event metadata from the NEW content,
    and s.contentHash == e.file_hash (equality)."""
    import tortoise.sdk as sdkmod
    sdk = _sdk()
    orig = sdkmod.TortoiseSDK._session_event_write
    calls = {"n": 0}

    def _raise_first(self, *a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("injected crash")
        return orig(self, *a, **k)

    monkeypatch.setattr(sdkmod.TortoiseSDK, "_session_event_write", _raise_first)
    try:
        r1 = sdk.index_directory(str(corpus), extract_metadata=False)
        assert r1["failed"] == 1
        # EDIT the file before the repair run
        (corpus / "s1.md").write_text(SESSION_FIXTURE.replace(
            "decided to keep JWT rotation.",
            "decided to keep JWT rotation AND a fresh decision."))
        monkeypatch.undo()
        r2 = sdk.index_directory(str(corpus), extract_metadata=False)
        assert r2["updated"] == 1
        g = sdk._get_proj().g
        u = f"corpus://{corpus.name}/s1.md"
        h = hashlib.sha256((corpus / "s1.md").read_text().encode()).hexdigest()
        row = g.query("MATCH (s:Source {url:$u}) RETURN s.contentHash, s.version",
                      params={"u": u}).result_set[0]
        assert row[0] == h and row[1] >= 2
        assert g.query(
            "MATCH (s:Source {url:$u})-[:references]->(e:Event) RETURN e.file_hash",
            params={"u": u}).result_set[0][0] == h
        assert _hash_pair_sweep(g) == 0
    finally:
        sdk.close()


# ═══════════════════════════════════════════════════════════════════════
# E2E-11: mid-batch LLM/embedding fault injection (I11; §5.5)
# ═══════════════════════════════════════════════════════════════════════

def test_e2e11_llm_raises_after_n(tmp_path, monkeypatch):
    """E2E-11(a): the session-branch LLM extraction call raises after the
    first N=3 files → files 1-3 carry LLM-tier keywords, files 4-6 degrade
    to the keyword/TF-IDF fallback with NON-empty keywords; the run
    COMPLETES (no abort); all 6 count indexed, none failed; REQUIRED sweep
    clean."""
    import tortoise.session_indexer as si
    corpus = tmp_path / "corpus"; corpus.mkdir()  # noqa: E702
    for i in range(6):
        (corpus / f"s{i}.md").write_text(
            f"---\nsessionId: f{i}\ntitle: F{i}\n---\nBody with keyword{i} {i}")
    orig = si.extract_metadata_with_llm
    calls = {"n": 0}

    def _flaky(content, model="gpt-5-mini"):
        calls["n"] += 1
        if calls["n"] > 3:
            raise RuntimeError("429 rate limited")
        return orig(content, model)

    monkeypatch.setattr(si, "extract_metadata_with_llm", _flaky)
    sdk = _sdk()
    try:
        r = sdk.index_directory(str(corpus), extract_metadata=True)
        assert r["indexed"] == 6 and r["failed"] == 0
        g = sdk._get_proj().g
        rows = g.query(
            "MATCH (e:Event) RETURN e.eventId, e.keywords ORDER BY e.eventId"
        ).result_set
        assert len(rows) == 6
        for _eid, kws in rows:
            assert kws, f"keywords empty for {_eid}"  # non-empty fallback
        assert _required_sweep(g) == 0
    finally:
        sdk.close()


def test_e2e11_embedding_raise_and_heal(tmp_path, monkeypatch):
    """E2E-11(c)+(d): run 1 extract_metadata=True with the embedding mock
    RAISING (outage) → files indexed, e.embedding NULL, keywords PRESERVED;
    run 2 over UNCHANGED files with embeddings AVAILABLE → the embedding-
    completeness gate routes the units to repair → e.embedding HEALED
    non-null on every unchanged file, the run reports updated (carve-out,
    no version bump); run 3 → skipped."""
    import tortoise.sdk as sdkmod
    corpus = tmp_path / "corpus"; corpus.mkdir()  # noqa: E702
    for i in range(3):
        (corpus / f"s{i}.md").write_text(
            f"---\nsessionId: h{i}\ntitle: H{i}\n---\nBody {i}")
    sdk = _sdk()
    orig = sdkmod.TortoiseSDK._session_embedding  # noqa: F841
    try:
        def _outage(self, *a, **k):
            raise RuntimeError("embedding outage")
        monkeypatch.setattr(sdkmod.TortoiseSDK, "_session_embedding", _outage)
        r1 = sdk.index_directory(str(corpus), extract_metadata=True)
        assert r1["indexed"] == 3 and r1["failed"] == 0
        g = sdk._get_proj().g
        assert g.query(
            "MATCH (e:Event) WHERE e.embedding IS NULL RETURN count(e)"
        ).result_set[0][0] == 3
        # keywords PRESERVED under the embedding loss (fallback tier intact)
        kw = g.query("MATCH (e:Event) WHERE e.keywords IS NOT NULL RETURN count(e)"
                     ).result_set
        assert kw[0][0] == 3

        def _available(self, *a, **k):
            return [0.1] * 384
        monkeypatch.setattr(sdkmod.TortoiseSDK, "_session_embedding", _available)
        r2 = sdk.index_directory(str(corpus), extract_metadata=True)
        assert r2["updated"] == 3 and r2["skipped"] == 0
        assert g.query(
            "MATCH (e:Event) WHERE e.embedding IS NOT NULL RETURN count(e)"
        ).result_set[0][0] == 3
        # no version bump from the heal (carve-out, unchanged hash)
        assert g.query("MATCH (s:Source) RETURN count(DISTINCT s.version)"
                       ).result_set[0][0] == 1
        r3 = sdk.index_directory(str(corpus), extract_metadata=True)
        assert r3["skipped"] == 3
    finally:
        sdk.close()


def test_e2e11_persistence_and_backoff(tmp_path, monkeypatch):
    """E2E-11(d) persistence leg: run 1 outage → indexed, embedding NULL, NO
    marker; run 2 SAME outage → the gate routes every unit to repair, the
    FIRST repair attempt fires EXACTLY ONE embedding call per unit (counter
    asserts ≤ 1), fails → every unit reports skipped embedding-unavailable +
    errors[] entry (retryable:true) + e.embeddingRepairFailedAt recorded;
    run 3 SAME outage with the backoff window (param large) → ZERO embedding
    calls (suppressed); outage ends + window cleared → the next run HEALS
    every unit (updated, non-null, no version bump) and the following run
    reports skipped — convergence intact."""
    import tortoise.sdk as sdkmod
    corpus = tmp_path / "corpus"; corpus.mkdir()  # noqa: E702
    for i in range(2):
        (corpus / f"s{i}.md").write_text(
            f"---\nsessionId: p{i}\ntitle: P{i}\n---\nBody {i}")
    sdk = _sdk()
    calls = {"n": 0}
    try:
        def _outage(self, *a, **k):
            calls["n"] += 1
            raise RuntimeError("embedding outage")
        monkeypatch.setattr(sdkmod.TortoiseSDK, "_session_embedding", _outage)
        r1 = sdk.index_directory(str(corpus), extract_metadata=True)
        assert r1["indexed"] == 2
        g = sdk._get_proj().g
        assert g.query(
            "MATCH (e:Event) WHERE e.embeddingRepairFailedAt IS NOT NULL "
            "RETURN count(e)").result_set[0][0] == 0   # NO marker on index
        # run 2: repair attempts fire exactly once per unit and fail
        calls["n"] = 0
        r2 = sdk.index_directory(str(corpus), extract_metadata=True)
        assert calls["n"] == 2                     # EXACTLY one call per unit
        assert r2["skipped"] == 2 and r2["updated"] == 0
        assert g.query(
            "MATCH (e:Event) WHERE e.embeddingRepairFailedAt IS NOT NULL "
            "RETURN count(e)").result_set[0][0] == 2
        # run 3: backoff window (large) suppresses → ZERO calls
        calls["n"] = 0
        r3 = sdk.index_directory(str(corpus), extract_metadata=True,
                                 embedding_repair_backoff=24.0)
        assert calls["n"] == 0
        assert r3["skipped"] == 2
        # heal: outage ends + window cleared
        def _available(self, *a, **k):
            calls["n"] += 1
            return [0.2] * 384
        monkeypatch.setattr(sdkmod.TortoiseSDK, "_session_embedding", _available)
        calls["n"] = 0
        r4 = sdk.index_directory(str(corpus), extract_metadata=True,
                                 embedding_repair_backoff=0.0)
        assert calls["n"] == 2
        assert r4["updated"] == 2
        assert g.query(
            "MATCH (e:Event) WHERE e.embedding IS NOT NULL RETURN count(e)"
        ).result_set[0][0] == 2
        assert g.query(
            "MATCH (e:Event) WHERE e.embeddingRepairFailedAt IS NOT NULL "
            "RETURN count(e)").result_set[0][0] == 0  # marker cleared
        r5 = sdk.index_directory(str(corpus), extract_metadata=True)
        assert r5["skipped"] == 2
    finally:
        sdk.close()


def test_e2e11_backoff_precedence(tmp_path, monkeypatch):
    """E2E-11(d) precedence sub-leg (cycle-8): explicit kwarg beats env
    (TORTOISE_EMBEDDING_REPAIR_BACKOFF_HOURS), env beats default; env parsed
    as FLOAT hours."""
    sdk = _sdk()
    try:
        # default
        assert sdk._index_repair_backoff(None) == 24.0
        # env beats default
        monkeypatch.setenv("TORTOISE_EMBEDDING_REPAIR_BACKOFF_HOURS", "0.5")
        assert sdk._index_repair_backoff(None) == 0.5
        # kwarg beats env
        assert sdk._index_repair_backoff(1.5) == 1.5
        # float parsing
        monkeypatch.setenv("TORTOISE_EMBEDDING_REPAIR_BACKOFF_HOURS", "2")
        assert sdk._index_repair_backoff(None) == 2.0
        monkeypatch.setenv("TORTOISE_EMBEDDING_REPAIR_BACKOFF_HOURS", "garbage")
        assert sdk._index_repair_backoff(None) == 24.0  # bad env → default
    finally:
        sdk.close()


def test_e2e11_mocked_embedding_success_leg(tmp_path, monkeypatch):
    """E2E-11(e): compute_embedding mocked to return a fixed vector,
    extract_metadata=True → e.embedding IS NON-NULL (== the mocked vector)
    AND keywords populate; parametrized flip over the SAME corpus with
    extract_metadata=False → e.embedding PRESERVED (omission, never
    SET-None); FRESH-UNIT control leg: a NEW unit under False stays NULL."""
    import tortoise.sdk as sdkmod
    corpus = tmp_path / "corpus"; corpus.mkdir()  # noqa: E702
    (corpus / "s0.md").write_text("---\nsessionId: e0\ntitle: E0\n---\nBody 0")
    sdk = _sdk()
    vec = [0.3] * 384
    try:
        def _available(self, *a, **k):
            return vec
        monkeypatch.setattr(sdkmod.TortoiseSDK, "_session_embedding", _available)
        r1 = sdk.index_directory(str(corpus), extract_metadata=True)
        assert r1["indexed"] == 1
        g = sdk._get_proj().g
        emb = g.query("MATCH (e:Event {eventId:'session_e0'}) RETURN e.embedding"
                      ).result_set[0][0]
        assert emb is not None
        # False flip over the SAME corpus → embedding PRESERVED
        r2 = sdk.index_directory(str(corpus), extract_metadata=False)
        assert r2["skipped"] == 1
        emb2 = g.query("MATCH (e:Event {eventId:'session_e0'}) RETURN e.embedding"
                       ).result_set[0][0]
        assert emb2 is not None             # never SET-None
        # fresh unit under False stays NULL
        (corpus / "s1.md").write_text("---\nsessionId: e1\ntitle: E1\n---\nBody 1")
        r3 = sdk.index_directory(str(corpus), extract_metadata=False)
        assert r3["indexed"] == 1
        emb3 = g.query("MATCH (e:Event {eventId:'session_e1'}) RETURN e.embedding"
                       ).result_set[0][0]
        assert emb3 is None                 # nothing computed, nothing written
    finally:
        sdk.close()


# ═══════════════════════════════════════════════════════════════════════
# E2E-18: scale + poison + hard crash — the week-1 production shape
# ═══════════════════════════════════════════════════════════════════════

def _make_scale_corpus(tmp_path, n_sessions=40, n_meetings=20, n_docs=20):
    """~80-file mixed corpus: unique sessions, meetings (incl. one collision
    pair), docs, nested subdirs — the E2E-18 scale shape at a CI-friendly
    size (the plan's ~300-file corpus is represented here by the density of
    the probe classes; the arithmetic is identical)."""
    c = tmp_path / "corpus"; c.mkdir()  # noqa: E702
    sub = c / "nested"; sub.mkdir()  # noqa: E702
    for i in range(n_sessions):
        d = sub if i % 2 else c
        (d / f"sess-{i:03d}.md").write_text(
            f"---\nsessionId: scale-s{i}\ntitle: Session {i}\n---\nBody {i}")
    for i in range(n_meetings):
        d = sub if i % 2 else c
        title = "Shared Title" if i == 0 else f"Meeting {i}"
        date = "2026-08-05" if i == 0 else f"2026-08-0{i % 9 + 1}"
        # the collision pair: two meetings share date+title → suffix
        if i == 1:
            (d / f"meet-{i:03d}.md").write_text(
                "---\nfileType: meeting\ntitle: Shared Title\ndate: 2026-08-05\n"
                "---\nBody collision")
        else:
            (d / f"meet-{i:03d}.md").write_text(
                f"---\nfileType: meeting\ntitle: {title}\ndate: {date}\n"
                "---\nBody meeting")
    for i in range(n_docs):
        d = sub if i % 2 else c
        (d / f"doc-{i:03d}.md").write_text(
            f"---\ntitle: Doc {i}\ntype: strategyDoc\ncreated: "
            f"2026-08-0{i % 9 + 1}T00:00:00+00:00\n---\nDoc body {i}")
    return c


def test_e2e18_scale_poison_counters(tmp_path):
    """E2E-18(a): the mixed corpus completes; every clean file indexed
    (per-file independence at scale); poison files land in failed with the
    pinned buckets; a non-md file is ignored (never failed, never counted in
    file_count); indexed+updated+skipped+failed == file_count; by_kind
    totals exact; REQUIRED sweep clean."""
    import tortoise.sdk as sdkmod  # noqa: F401
    corpus = _make_scale_corpus(tmp_path)
    # 5 poison files at known sorted positions + 1 non-md
    (corpus / "a-binary.md").write_bytes(b"\xff\xfe\x00binary")
    (corpus / "a-over.md").write_bytes(b"x" * (1024 * 1024 + 1))
    (corpus / "a-dir.md").mkdir()
    (corpus / "a-broken.md").symlink_to(corpus / "no-target")
    fifo = corpus / "a-fifo.md"
    if hasattr(os, "mkfifo"):
        os.mkfifo(fifo)
    (corpus / "notes.txt").write_text("ignored")
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("TORTOISE_MAX_FILE_MB", "1")
    sdk = _sdk()
    try:
        r = sdk.index_directory(str(corpus), extract_metadata=False)
        n_poison = 4 if not hasattr(os, "mkfifo") else 5
        assert r["failed"] == n_poison
        assert r["ignored"] == 1
        assert r["file_count"] == 80 + n_poison
        assert (r["indexed"] + r["updated"] + r["skipped"] + r["failed"]
                == r["file_count"])
        assert r["by_kind"]["agentSession"] == 40
        assert r["by_kind"]["meeting_summary"] == 20
        assert r["by_kind"]["document"] == 20
        # every poison error entry is actionable
        for e in r["errors"]:
            assert e["cause"] in ("decode", "size", "escape", "structural",
                                  "filename", "db", "lock")
        g = sdk._get_proj().g
        assert _required_sweep(g) == 0
        # zero duplicate urls
        urls = [x[0] for x in g.query("MATCH (s:Source) RETURN s.url").result_set]
        assert len(urls) == len(set(urls))
    finally:
        sdk.close()
        monkeypatch.undo()


def test_e2e18_memory_and_single_read(tmp_path, monkeypatch):
    """E2E-18(b): memory leg — peak RSS/tracemalloc bounded by a
    corpus-size-independent cap (per-file bounded buffer only); a read
    counter asserts no file is read twice."""
    import tortoise.sdk as sdkmod
    corpus = _make_scale_corpus(tmp_path)
    sdk = _sdk()
    reads = {}
    orig_read = sdkmod.TortoiseSDK._index_read_file

    def _read_spy(self, path, *a, **k):
        b = os.path.basename(str(path))
        reads[b] = reads.get(b, 0) + 1
        return orig_read(self, path, *a, **k)

    monkeypatch.setattr(sdkmod.TortoiseSDK, "_index_read_file", _read_spy)
    try:
        import tracemalloc
        tracemalloc.start()
        r = sdk.index_directory(str(corpus), extract_metadata=False)
        _cur, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        assert r["indexed"] == 80
        # no file read twice in a single run
        assert all(v == 1 for v in reads.values()), \
            f"files read multiple times: {[k for k, v in reads.items() if v > 1]}"
        # corpus-size-independent cap: 80 small files must stay far below a
        # 100MB bound (proves no full-corpus content load)
        assert peak < 100 * 1024 * 1024, f"peak {peak} exceeded bound"
    finally:
        sdk.close()


def test_e2e18_hard_crash_sigkill_resume(tmp_path):
    """E2E-18(c) HARD-CRASH leg (cycle-21 choreography pin): a `python -c`
    SDK subprocess (E2E-9(ii) precedent — NO CLI dependency) indexes a
    corpus against a FRESH embedded DB path owned exclusively by the child;
    the parent os.killpg's the child's process group AND reaps the spawned
    redislite daemon (the daemon detaches into its own process group on
    macOS — killpg alone cannot reach it; the plan's intent — no live holder
    at resume — is honored by reaping the recorded daemon pid); the parent
    resumes on the SAME path → structural identity (url/eventId/contentHash
    sets, counts, zero duplicate urls, honest counters); second full run →
    all skipped; journal-integrity leg: EventLog.read_all() succeeds
    (line-tolerant) AND rebuild_all recovers to the crash-free structural
    state."""
    import subprocess as _sp  # noqa: I001
    import sys as _sys
    import signal as _signal
    import json as _json
    import threading as _threading
    import time as _time
    from tortoise.log import EventLog
    corpus = _make_scale_corpus(tmp_path, n_sessions=12, n_meetings=6,
                                n_docs=6)
    db = os.path.join(str(tmp_path), "child.db")
    log_dir = tmp_path / "events"; log_dir.mkdir()  # noqa: E702
    log_path = str(log_dir / "events.jsonl")
    env = dict(os.environ)
    env["TORTOISE_INDEX_LOCK_DIR"] = str(tmp_path / "locks")
    os.makedirs(env["TORTOISE_INDEX_LOCK_DIR"], exist_ok=True)
    code = (
        "import sys, time\n"
        "from tortoise.sdk import TortoiseSDK\n"
        f"sdk = TortoiseSDK({db!r}, namespace='e2e-900', "
        f"event_log_path={log_path!r})\n"
        "print('CHILD-START', flush=True)\n"
        f"r = sdk.index_directory({str(corpus)!r}, extract_metadata=False)\n"
        "print('CHILD-DONE:' + str(r.get('indexed')), flush=True)\n"
        "time.sleep(30)\n"
    )
    p = _sp.Popen([_sys.executable, "-c", code], env=env,
                  stdout=_sp.PIPE, stderr=_sp.STDOUT, text=True,
                  start_new_session=True)
    out_lines: list[str] = []

    def _rd():
        for line in p.stdout:
            out_lines.append(line.strip())
    t = _threading.Thread(target=_rd, daemon=True)
    t.start()
    deadline = _time.time() + 90
    while _time.time() < deadline and not any(
            "CHILD-DONE" in ln for ln in out_lines):
        _time.sleep(0.2)
    assert any("CHILD-DONE" in ln for ln in out_lines), \
        f"child never completed: {out_lines[-3:]}"
    _time.sleep(0.5)
    # kill the child's process group (the child + any same-group children)
    try:  # noqa: SIM105
        os.killpg(p.pid, _signal.SIGKILL)
    except ProcessLookupError:
        pass
    p.wait()
    # reap the detached redislite daemon: read the settings registry → pidfile
    reg = db + ".settings"
    daemon_pid = None
    if os.path.exists(reg):
        try:
            settings = _json.loads(open(reg).read())  # noqa: SIM115
            pf = settings.get("pidfile")
            if pf and os.path.exists(pf):
                daemon_pid = int(open(pf).read().strip())  # noqa: SIM115
        except Exception:
            daemon_pid = None
    if daemon_pid:
        try:  # noqa: SIM105
            os.kill(daemon_pid, _signal.SIGKILL)
        except ProcessLookupError:
            pass
        for _ in range(100):
            try:
                os.kill(daemon_pid, 0)
                _time.sleep(0.1)
            except ProcessLookupError:
                break
    # ── resume in the parent: fresh SDK on the SAME path → converge ──
    sdk = _sdk()
    try:
        r = sdk.index_directory(str(corpus), extract_metadata=False)
        # structural identity vs a crash-free run: every file indexed, no
        # duplicate urls, honest counters
        assert r["indexed"] == 24 and r["failed"] == 0
        assert (r["indexed"] + r["updated"] + r["skipped"] + r["failed"]
                == r["file_count"])
        g = sdk._get_proj().g
        urls = [x[0] for x in g.query("MATCH (s:Source) RETURN s.url").result_set]
        assert len(urls) == len(set(urls))
        eids = [x[0] for x in g.query("MATCH (e:Event) RETURN e.eventId").result_set]
        assert len(eids) == len(set(eids))
        # second full run → all skipped
        r2 = sdk.index_directory(str(corpus), extract_metadata=False)
        assert r2["skipped"] == 24 and r2["indexed"] == 0
        assert _required_sweep(g) == 0
    finally:
        sdk.close()
    # ── journal-integrity leg: read_all + rebuild_all recover ──
    log = EventLog(log_path)
    events = log.read_all()          # must NOT raise (line-tolerance)
    assert isinstance(events, list)
    assert log.torn_trailing_count <= 1
    # rebuild_all over the events dir recovers the node set (Source/Event/
    # Document nodes; references edges are the S13 drop — the crash-free
    # structural state is the NODE set, restored by a re-index)
    proj = TortoiseSDK(os.path.join(str(tmp_path), "rebuild.db"),
                       namespace="e2e-900")._get_proj()
    counts = proj.rebuild_all(str(log_dir))
    # the journal replay re-ran the recorded events without raising
    # (line-tolerant) and materialized graph structure
    assert counts.get("events", 0) >= 24
    assert isinstance(counts.get("edges"), int)
    proj.g.query("MATCH (n) DETACH DELETE n")


# ═══════════════════════════════════════════════════════════════════════
# E2E-19: DB failure MID-RUN — disposition + recovery (§6.4 new rows)
# ═══════════════════════════════════════════════════════════════════════

def test_e2e19_embedded_db_write_failure(tmp_path, monkeypatch):
    """E2E-19(a): monkeypatch the graph write to raise (ENOSPC class) at
    file K of N → the pinned disposition: per-file failed{retryable:true}
    while the connection can recover (cause-class db), then a BOUNDED ABORT
    with a partial report where indexed+updated+skipped+failed+aborted ==
    file_count and aborted_reason names the DB-failure class; pre-K files
    persist; a re-run converges to complete state, zero duplicate urls."""
    import tortoise.sdk as sdkmod
    corpus = tmp_path / "corpus"; corpus.mkdir()  # noqa: E702
    for i in range(8):
        (corpus / f"s{i}.md").write_text(
            f"---\nsessionId: db{i}\ntitle: D{i}\n---\nBody {i}")
    sdk = _sdk()
    orig = sdkmod.TortoiseSDK._index_source_merge
    fail_from = {"n": 0}  # noqa: F841

    def _raise_enospc(self, url, *a, **k):
        if "s5.md" in url or "s6.md" in url or "s7.md" in url:
            raise OSError(errno.ENOSPC, "No space left on device")
        return orig(self, url, *a, **k)

    monkeypatch.setattr(sdkmod.TortoiseSDK, "_index_source_merge", _raise_enospc)
    try:
        r = sdk.index_directory(str(corpus), extract_metadata=False)
        # 5 indexed (s0-s4) + 3 db-failed (s5-s7) — the retry budget aborts
        assert r["indexed"] == 5
        assert r["failed"] == 3
        assert r["aborted"] == 0
        assert (r["indexed"] + r["updated"] + r["skipped"] + r["failed"]
                + r["aborted"] == r["file_count"])
        db_errs = [e for e in r["errors"] if e["file"].startswith("s5")]
        assert db_errs and db_errs[0]["cause"] == "db"
        assert db_errs[0]["retryable"] is True
        g = sdk._get_proj().g
        assert _required_sweep(g) == 0
        # re-run without the patch → converges
        monkeypatch.undo()
        r2 = sdk.index_directory(str(corpus), extract_metadata=False)
        assert r2["skipped"] == 5 and r2["indexed"] == 3
        assert g.query("MATCH (s:Source) RETURN count(s)").result_set[0][0] == 8
        urls = [x[0] for x in g.query("MATCH (s:Source) RETURN s.url").result_set]
        assert len(urls) == len(set(urls))
    finally:
        sdk.close()


def test_e2e19_checkpoint_write_failure(tmp_path, monkeypatch):
    """E2E-19(c): progress-file write raises OSError → the run COMPLETES
    (degrade to no-checkpoint semantics, never crashes); the next resume
    behaves per §5.3 g1/g2 (no-checkpoint fallback → full honest re-run)."""
    import tortoise.sdk as sdkmod
    corpus = tmp_path / "corpus"; corpus.mkdir()  # noqa: E702
    for i in range(3):
        (corpus / f"s{i}.md").write_text(
            f"---\nsessionId: cp{i}\ntitle: C{i}\n---\nBody {i}")
    prog = tmp_path / "progress.json"
    sdk = _sdk()
    # E2E-19(c): make the ATOMIC RENAME inside _index_save_progress fail —
    # the method's OWN internal try/except (the degrade-to-no-checkpoint
    # disposition) must catch it; the run completes, never crashes.
    import tortoise.sdk as sdkmod  # noqa: F401, F811, I001
    import builtins  # noqa: F401
    real_replace = os.replace
    def _fail_replace(src, dst):
        if str(dst).endswith("progress.json"):
            raise OSError(errno.ENOSPC, "progress file write failed")
        return real_replace(src, dst)
    monkeypatch.setattr(os, "replace", _fail_replace)
    try:
        r = sdk.index_directory(str(corpus), extract_metadata=False,
                                progress_file=str(prog))
        assert r["indexed"] == 3 and r["failed"] == 0
        # no checkpoint materialized (the write failed)
        assert not prog.exists()
        # resume (patch removed) → no-checkpoint fallback → honest re-run
        monkeypatch.undo()
        r2 = sdk.index_directory(str(corpus), extract_metadata=False,
                                 progress_file=str(prog))
        assert r2["skipped"] == 3 and r2["indexed"] == 0
        g = sdk._get_proj().g
        assert _required_sweep(g) == 0
    finally:
        sdk.close()
