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
from __future__ import annotations

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
    monkeypatch.setenv("TORTOISE_MAX_FILE_MB", "0.0001")
    p = corpus / "s1.md"
    sdk = _sdk()
    try:
        real_lstat = os.lstat
        def _lying_lstat(path, *a, **kw):
            if str(path) == str(p):
                # stat-only lie: under-limit size, regular-file mode
                import types
                return types.SimpleNamespace(
                    st_mode=stat.S_IFREG | 0o644, st_size=10, st_mtime=0.0,
                    st_dev=1, st_ino=1, st_nlink=1)
            return real_lstat(path, *a, **kw)
        # patch the pre-read stat call (as imported by the read path)
        monkeypatch.setattr(os, "lstat", _lying_lstat)
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
        r = sdk.index_directory(str(corpus), extract_metadata=False)
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
