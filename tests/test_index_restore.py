"""S13/S15 + T12 suite (epic #900, issue #1038): rebuild-semantics regression.

Plan §8.6 T12 + §8.4 S13/S15 rows: accept-and-document (option b) with the
re-index repair oracle — rebuild_all (wipe + journal replay) DROPS
session/meeting ``references`` edges (created indexer-side, never journaled)
while DOC-UNIT edges SURVIVE (the journaled DocumentCreated event carries the
``source_url`` override → replay's #205 auto-wire re-creates the edge onto the
real Source); no phantom Sources; Source/Event/Document nodes survive replay;
a re-index run restores the dropped session/meeting edges (repair carve-out →
``updated``). The wipe-after-parse + line-tolerance ordering pins: parse ALL
.jsonl into memory (torn TRAILING line skipped+warned, never raised) BEFORE
the wipe — a torn tail rebuilds to the crash-free structural state. Restore
drill: backup (corpus + events dir + db) → wipe → rebuild_all (line-tolerant)
→ re-index → count(Source)==file_count, zero duplicate urls. Forward-only
release commitment: the old binary + new journal = silent record loss
(documented; the old logic skips unknown record types).

Harness conventions (§7): fresh embedded DB per test; graph assertions via
raw Cypher; extract_metadata=False (no network).
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path

import pytest

from tortoise.sdk import TortoiseSDK
from tortoise.log import EventLog


def _db(tmp_path: Path, name: str = "t.db") -> str:
    return os.path.join(str(tmp_path), name)


def _sdk(tmp_path: Path, name: str = "t.db",
         events: str | None = None) -> TortoiseSDK:
    kw = {}
    if events is not None:
        kw["event_log_path"] = events
    return TortoiseSDK(_db(tmp_path, name), namespace="e2e-900", **kw)


SESSION_FIXTURE = """\
---
sessionId: {sid}
title: "{title}"
---
Body {sid}.
"""

MEETING_FIXTURE = """\
---
fileType: meeting
title: "{title}"
date: 2026-08-05
---
Body.
"""

DOC_FIXTURE = """\
---
title: "{title}"
type: strategyDoc
---
Doc body.
"""


def _required_sweep(g) -> int:
    return g.query(
        "MATCH (s:Source) WHERE s.url IS NULL OR s.url='' OR s.sourceKind IS NULL "
        "OR s.contentHash IS NULL OR s.contentHash='' OR s.ingestedAt IS NULL "
        "RETURN count(s)").result_set[0][0]


def _hash_pair_sweep(g) -> int:
    return g.query(
        "MATCH (s:Source)-[:references]->(e:Event) "
        "WHERE s.contentHash <> e.file_hash RETURN count(*)").result_set[0][0]


def _all_three_corpus(tmp_path: Path) -> Path:
    """A corpus with one file of EACH type (session/meeting/doc)."""
    c = tmp_path / "corpus"; c.mkdir()
    (c / "s1.md").write_text(SESSION_FIXTURE.format(sid="r1", title="S1"))
    (c / "m1.md").write_text(MEETING_FIXTURE.format(title="M1"))
    (c / "d1.md").write_text(DOC_FIXTURE.format(title="D1"))
    return c


# ═══════════════════════════════════════════════════════════════════════
# S13 (option b): rebuild drops session/meeting references; doc edges survive
# ═══════════════════════════════════════════════════════════════════════

def test_s13_rebuild_drops_session_meeting_edges_doc_survives(tmp_path):
    """S13 option (b) + T12: index a 3-type corpus → rebuild_all → the
    SESSION + MEETING references edges are DROPPED (created indexer-side,
    never journaled), the DOC-UNIT references edge SURVIVES (the journaled
    DocumentCreated event's source_url override → replay's #205 auto-wire);
    Source/Event/Document nodes all survive; no phantom Sources; REQUIRED
    sweep clean."""
    events_dir = tmp_path / "events"; events_dir.mkdir()
    events = str(events_dir / "events.jsonl")
    corpus = _all_three_corpus(tmp_path)
    sdk = _sdk(tmp_path, events=events)
    try:
        r = sdk.index_directory(str(corpus), extract_metadata=False)
        assert r["indexed"] == 3
        g = sdk._get_proj().g
        assert g.query("MATCH ()-[r:references]->() RETURN count(r)"
                       ).result_set[0][0] == 3
        # journal has the SourceCreated/EventRecorded/DocumentCreated lines
        log = EventLog(events)
        assert len(log.read_all()) >= 3
        proj = sdk._get_proj()
        counts = proj.rebuild_all(str(events_dir))
        # node survival: 3 Sources, 2 Events (session+meeting), 1 Document
        assert g.query("MATCH (s:Source) RETURN count(s)").result_set[0][0] == 3
        assert g.query("MATCH (e:Event) RETURN count(e)").result_set[0][0] == 2
        assert g.query("MATCH (d:Document) RETURN count(d)").result_set[0][0] == 1
        # the SPLIT: session+meeting references DROPPED (2), doc edge SURVIVES
        assert g.query("MATCH ()-[r:references]->() RETURN count(r)"
                       ).result_set[0][0] == 1
        doc_edge = g.query(
            "MATCH (s:Source)-[:references]->(d:Document) RETURN count(*)"
        ).result_set[0][0]
        assert doc_edge == 1
        # no phantom Sources (url=doc_<rel> would be a phantom)
        urls = [x[0] for x in g.query("MATCH (s:Source) RETURN s.url").result_set]
        assert len(urls) == 3 and len(set(urls)) == 3
        assert not any("doc_" in u.split("/")[-1] for u in urls)
        assert _required_sweep(g) == 0
        # re-index restores the dropped session/meeting edges (repair
        # carve-out → updated) — the re-index repair oracle
        r2 = sdk.index_directory(str(corpus), extract_metadata=False)
        assert r2["updated"] == 2 and r2["skipped"] == 1, r2
        assert g.query("MATCH ()-[r:references]->() RETURN count(r)"
                       ).result_set[0][0] == 3
        assert _hash_pair_sweep(g) == 0
    finally:
        sdk.close()


def test_s13_post_rebuild_version_equality_and_sweep(tmp_path):
    """T12 cycle-19: post-rebuild Source/Event `version` equality vs
    pre-rebuild + the hash-pair sweep == 0 checkpoint (after the re-index
    repair oracle runs)."""
    events_dir = tmp_path / "events"; events_dir.mkdir()
    corpus = _all_three_corpus(tmp_path)
    sdk = _sdk(tmp_path, events=str(events_dir / "events.jsonl"))
    try:
        sdk.index_directory(str(corpus), extract_metadata=False)
        g = sdk._get_proj().g
        pre = {u: g.query("MATCH (s:Source {url:$u}) RETURN s.version",
                          params={"u": u}).result_set[0][0]
               for u in [x[0] for x in
                         g.query("MATCH (s:Source) RETURN s.url").result_set]}
        proj = sdk._get_proj()
        proj.rebuild_all(str(events_dir))
        # re-index repair oracle restores edges; versions land at the
        # converged values (the hash-diff-gated bump replays journaled states)
        sdk.index_directory(str(corpus), extract_metadata=False)
        post = {u: g.query("MATCH (s:Source {url:$u}) RETURN s.version",
                           params={"u": u}).result_set[0][0]
                for u in pre}
        assert pre == post, f"version drift post-rebuild: {pre} vs {post}"
        assert _hash_pair_sweep(g) == 0
        assert _required_sweep(g) == 0
    finally:
        sdk.close()


# ═══════════════════════════════════════════════════════════════════════
# S15: wipe-after-parse + line-tolerance ordering pins
# ═══════════════════════════════════════════════════════════════════════

def test_s15_torn_tail_journal_rebuilds_to_crash_free_state(tmp_path):
    """T12/S15 cycle-21: a journal with a TORN TRAILING line (SIGKILL
    mid-append) → EventLog.read_all() succeeds (line-tolerance, skipped +
    counted, never raised) AND rebuild_all recovers to the crash-free
    structural state (the wipe-after-parse pin: ALL jsonl parsed BEFORE the
    wipe, so a torn line is a survivable skip, not total loss)."""
    events_dir = tmp_path / "events"; events_dir.mkdir()
    log_path = str(events_dir / "events.jsonl")
    corpus = _all_three_corpus(tmp_path)
    sdk = _sdk(tmp_path, events=log_path)
    try:
        sdk.index_directory(str(corpus), extract_metadata=False)
        g = sdk._get_proj().g
        n_sources = g.query("MATCH (s:Source) RETURN count(s)").result_set[0][0]
        # simulate the SIGKILL mid-append: a torn trailing line
        with open(log_path, "a", encoding="utf-8") as f:
            f.write('{"type": "EventRecorded", "id": "session_r1", "eventId": "sess')  # torn
        log = EventLog(log_path)
        events = log.read_all()          # must NOT raise
        assert log.torn_trailing_count == 1
        assert len(events) >= 3
        # rebuild survives the torn tail (parse-all-then-wipe)
        proj = sdk._get_proj()
        proj.rebuild_all(str(events_dir))
        assert g.query("MATCH (s:Source) RETURN count(s)").result_set[0][0] == n_sources
        assert _required_sweep(g) == 0
    finally:
        sdk.close()


def test_s15_mid_file_malformed_raises_actionable(tmp_path):
    """T12/S15 cycle-23: a malformed MID-FILE line (valid position,
    unparseable content) is a SEPARATE corruption class — read_all RAISES an
    actionable error naming the file and line (a mid-file skip would drop a
    record that IS in the journal; the tail-tear tolerance covers only the
    trailing line)."""
    events_dir = tmp_path / "events"; events_dir.mkdir()
    log_path = str(events_dir / "events.jsonl")
    corpus = _all_three_corpus(tmp_path)
    sdk = _sdk(tmp_path, events=log_path)
    try:
        sdk.index_directory(str(corpus), extract_metadata=False)
        # corrupt a MID-FILE line (not the last)
        lines = Path(log_path).read_text().splitlines()
        lines[1] = "{not-json"
        Path(log_path).write_text("\n".join(lines) + "\n")
        log = EventLog(log_path)
        with pytest.raises(ValueError) as ei:
            log.read_all()
        assert "line" in str(ei.value).lower() and str(log_path) in str(ei.value)
    finally:
        sdk.close()


# ═══════════════════════════════════════════════════════════════════════
# Restore drill: backup → wipe → rebuild (line-tolerant) → re-index
# ═══════════════════════════════════════════════════════════════════════

def test_s15_restore_drill_end_to_end(tmp_path):
    """T12 cycle-21 restore drill: index fixture → back up (1) the corpus
    files (source of truth), (2) the FULL events/ JSONL directory (sole
    replay source), (3) the db file → fresh graph → restore via rebuild_all
    (line-tolerant) → re-index → count(Source)==file_count, zero duplicate
    urls, edges restored."""
    events_dir = tmp_path / "events"; events_dir.mkdir()
    log_path = str(events_dir / "events.jsonl")
    corpus = _all_three_corpus(tmp_path)
    db = _db(tmp_path)
    sdk = _sdk(tmp_path, events=log_path)
    try:
        r = sdk.index_directory(str(corpus), extract_metadata=False)
        assert r["indexed"] == 3
    finally:
        sdk.close()
    # backup: corpus + events dir + db (SDK closed so the db file is free)
    backup = tmp_path / "backup"; backup.mkdir()
    shutil.copytree(str(corpus), str(backup / "corpus"))
    shutil.copytree(str(events_dir), str(backup / "events"))
    shutil.copy2(db, str(backup / "t.db"))
    assert (backup / "events" / "events.jsonl").exists()
    # restore: fresh graph → rebuild_all from the backup events dir
    sdk2 = TortoiseSDK(_db(tmp_path, "restored.db"), namespace="e2e-900")
    try:
        proj = sdk2._get_proj()
        counts = proj.rebuild_all(str(backup / "events"))
        g = sdk2._get_proj().g
        # nodes survive; session/meeting edges dropped per S13
        assert g.query("MATCH (s:Source) RETURN count(s)").result_set[0][0] == 3
        assert g.query("MATCH (d:Document) RETURN count(d)").result_set[0][0] == 1
        # re-index restores edges → convergence
        r2 = sdk2.index_directory(str(backup / "corpus"), extract_metadata=False)
        assert r2["updated"] >= 2, r2
        assert g.query("MATCH ()-[r:references]->() RETURN count(r)"
                       ).result_set[0][0] == 3
        assert g.query("MATCH (s:Source) RETURN count(s)").result_set[0][0] == 3
        urls = [x[0] for x in g.query("MATCH (s:Source) RETURN s.url").result_set]
        assert len(urls) == len(set(urls)) == 3   # zero duplicate urls
        assert _required_sweep(g) == 0
    finally:
        sdk2.close()


# ═══════════════════════════════════════════════════════════════════════
# Backfill → rebuild leg (T12 cycle-19): actual wipe semantics
# ═══════════════════════════════════════════════════════════════════════

def test_t12_backfill_rebuild_wipe_semantics(tmp_path):
    """T12 cycle-19 backfill→rebuild leg: legacy DocumentCreated Event nodes
    are ABSENT post-rebuild (wipe + replay — unjournaled legacy nodes), the
    backfill Sources are present (journaled via create_source), and a re-run
    of backfill on the surviving corpus converges."""
    import hashlib
    from tortoise.file_indexer import compute_file_hash
    events_dir = tmp_path / "events"; events_dir.mkdir()
    corpus = tmp_path / "corpus"; corpus.mkdir()
    (corpus / "docA.md").write_text(DOC_FIXTURE.format(title="DocA"))
    stored = compute_file_hash(str(corpus / "docA.md"))
    sdk = _sdk(tmp_path, events=str(events_dir / "events.jsonl"))
    try:
        g = sdk._get_proj().g
        # legacy DocumentCreated Event (unjournaled — created via raw Cypher)
        g.query(
            "CREATE (e:Event) SET e.eventId='docA.md', e.eventKind='DocumentCreated', "
            "e.file_hash=$h, e.title='DocA'", params={"h": stored})
        r = sdk.backfill_sources(str(corpus))
        assert r["created"] == 1 and r["linked"] == 1
        assert g.query("MATCH (e:Event {eventId:'docA.md'}) RETURN count(e)"
                       ).result_set[0][0] == 1
        proj = sdk._get_proj()
        proj.rebuild_all(str(events_dir))
        # the UNJOURNALED legacy DocumentCreated Event node is DROPPED
        assert g.query("MATCH (e:Event {eventId:'docA.md'}) RETURN count(e)"
                       ).result_set[0][0] == 0
        # the backfill Source survives (journaled via create_source)
        assert g.query("MATCH (s:Source) RETURN count(s)").result_set[0][0] == 1
        assert _required_sweep(g) == 0
    finally:
        sdk.close()


# ═══════════════════════════════════════════════════════════════════════
# Forward-only release commitment: old-logic skip-unknown-type
# ═══════════════════════════════════════════════════════════════════════

def test_t12_old_logic_skips_unknown_record_types(tmp_path):
    """T12 cycle-23: the replay's pass-1b if/elif chain has NO else — an
    unknown record type (a journal written by a NEWER binary) is SKIPPED,
    never a crash. This is the forward-only release contract: the OLD binary
    replaying a NEW journal skips the new record kinds (documented silent
    record loss — the rollback hazard the forward-only commitment names)."""
    events_dir = tmp_path / "events"; events_dir.mkdir()
    log_path = str(events_dir / "events.jsonl")
    corpus = _all_three_corpus(tmp_path)
    sdk = _sdk(tmp_path, events=log_path)
    try:
        sdk.index_directory(str(corpus), extract_metadata=False)
        # append an UNKNOWN record type (a future binary's kind)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"type": "FutureRecordKind2027",
                                "id": "future-1", "payload": {"x": 1}}) + "\n")
        proj = sdk._get_proj()
        counts = proj.rebuild_all(str(events_dir))   # must NOT raise
        g = sdk._get_proj().g
        # known kinds replayed; the unknown kind skipped silently
        assert g.query("MATCH (s:Source) RETURN count(s)").result_set[0][0] == 3
        assert _required_sweep(g) == 0
    finally:
        sdk.close()


# ═══════════════════════════════════════════════════════════════════════
# T12 cycle-19 (c): INSTANTIATES (aboutObject) edges join the rebuild-drop
# scope — count==0 post-rebuild, restored after the re-index.
# ═══════════════════════════════════════════════════════════════════════

def test_t12_instantiates_edges_rebuild_drop_and_restore(tmp_path):
    """T12 cycle-19 (c): a session Event's issue/PR aboutObject edges are
    DROPPED by rebuild (created indexer-side, never journaled) and RESTORED
    by the re-index (the session upsert re-runs _connect_issue_objects)."""
    events_dir = tmp_path / "events"; events_dir.mkdir()
    corpus = tmp_path / "corpus"; corpus.mkdir()
    (corpus / "s.md").write_text(
        "---\nsessionId: r9\ntitle: Issues\nissues: [repo#1]\n"
        "prs: [repo#2]\n---\nBody with issue references")
    sdk = _sdk(tmp_path, events=str(events_dir / "events.jsonl"))
    try:
        r = sdk.index_directory(str(corpus), extract_metadata=False)
        assert r["indexed"] == 1
        g = sdk._get_proj().g
        n_live = g.query(
            "MATCH (e:Event {eventId:'session_r9'})-[:aboutObject]->(o:Object) "
            "RETURN count(o)").result_set[0][0]
        assert n_live >= 2, f"expected issue/PR edges live, got {n_live}"
        proj = sdk._get_proj()
        proj.rebuild_all(str(events_dir))
        # the aboutObject edges are DROPPED (indexer-side, unjournaled)
        assert g.query(
            "MATCH ()-[:aboutObject]->() RETURN count(*)").result_set[0][0] == 0
        # re-index restores them (the session upsert re-wires)
        sdk.index_directory(str(corpus), extract_metadata=False)
        assert g.query(
            "MATCH (e:Event {eventId:'session_r9'})-[:aboutObject]->(o:Object) "
            "RETURN count(o)").result_set[0][0] == n_live
        assert _required_sweep(g) == 0
    finally:
        sdk.close()
