"""S9 integration suite for epic #900 T6 (issue #1042) — backfill_sources.

Covers the plan §8.6 T6 bullet + E2E-8 contract: additive-only
eventKind-parametrized reconciliation (AgentSession via ``source_file``,
DocumentCreated via ``eventId`` = rel-path), dry-run/real-run report shapes
(§6.1 docstring), health preservation, the hash-pair sweep checkpoint
(== 1 by-design divergence), second-run convergence, the coexistence forward
run, the CONCURRENT backfill+index threads leg (E2E-9 choreography — a
sequential execution is a test failure), the hardlink/escape fail-closed
variant (no walk context, zero reads), the kill-between crash-repair variant,
and the edge-state variants (a–f, pre-#320 shape, undecodable seam).

Harness conventions (§7): fresh embedded DB per test; graph assertions via raw
Cypher on ``sdk._get_proj().g``; no network (``extract_metadata=False`` on the
new index path short-circuits session embeddings — §6.1 I15). ``corpus_name``
in expected values = basename of the fixture corpus dir (``<C>`` below).
"""
from __future__ import annotations  # noqa: I001

import os
import threading
from pathlib import Path

import pytest

from tortoise.sdk import TortoiseSDK
from tortoise.file_indexer import compute_file_hash

from tests import concurrency_harness as harness


def _db() -> str:
    return harness.make_db()


def _sdk(db: str | None = None) -> TortoiseSDK:
    return TortoiseSDK(db or _db(), namespace="e2e-900")


SESSION_FIXTURE = """\
---
sessionId: {sid}
title: "Session {n}"
---
Body {n}.
"""

DOC_FIXTURE = """\
---
title: "{title}"
type: strategyDoc
---
Body {title}.
"""


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


def _event_props(g, event_id: str) -> dict | None:
    rows = g.query(
        "MATCH (e:Event {eventId:$eid}) RETURN properties(e)",
        params={"eid": event_id},
    ).result_set
    return rows[0][0] if rows else None


def _create_legacy_event(g, *, event_id: str, kind: str,
                         rel_path: str | None = None,
                         file_hash: str | None = None,
                         title: str | None = None) -> None:
    """Simulate a legacy Event via raw Cypher — no Sources (E2E-8 setup)."""
    parts = ["CREATE (e:Event) SET e.eventId=$eid, e.eventKind=$kind"]
    params: dict = {"eid": event_id, "kind": kind}
    if rel_path is not None:
        parts.append(", e.source_file=$sf")
        params["sf"] = rel_path
    if file_hash is not None:
        parts.append(", e.file_hash=$hash")
        params["hash"] = file_hash
    if title is not None:
        parts.append(", e.title=$title")
        params["title"] = title
    g.query("".join(parts), params=params)


def _main_corpus(tmp_path, name: str = "corpus"):
    """E2E-8 main fixture: 3 session files (sessionId legacy1..3) + 2 doc
    files carrying ONLY {title, type: strategyDoc}. Returns (corpus, hashes).
    """
    c = tmp_path / name
    c.mkdir()
    hashes: dict[str, str] = {}
    for i in (1, 2, 3):
        p = c / f"s{i}.md"
        p.write_text(SESSION_FIXTURE.format(sid=f"legacy{i}", n=i))
        hashes[p.name] = compute_file_hash(str(p))
    for n, title in (("docA.md", "Doc A"), ("docB.md", "Doc B")):
        p = c / n
        p.write_text(DOC_FIXTURE.format(title=title))
        hashes[n] = compute_file_hash(str(p))
    return c, hashes


def _seed_main_legacy_events(sdk: TortoiseSDK, c: Path,
                             hashes: dict[str, str]) -> None:
    """E2E-8 setup: 3 AgentSession Events (source_file + file_hash) + 2
    DocumentCreated Events (eventId = rel-path). Legacy Event 3's stored
    file_hash is STALE by fixture design (file edited AFTER capture)."""
    g = sdk._get_proj().g
    for i in (1, 2, 3):
        _create_legacy_event(g, event_id=f"session_legacy{i}",
                             kind="AgentSession", rel_path=f"s{i}.md",
                             file_hash=hashes[f"s{i}.md"], title=f"Legacy {i}")
    # file edited since capture → health bucket `stale`, backfill reports the
    # W2 hash-mismatch note (exactly ONE errors[] entry across the fixture)
    (c / "s3.md").write_text(SESSION_FIXTURE.format(sid="legacy3", n=99)
                             + "edited after capture\n")
    for n in ("docA.md", "docB.md"):
        _create_legacy_event(g, event_id=n, kind="DocumentCreated",
                             file_hash=hashes[n], title=n)


# ═══════════════════════════════════════════════════════════════════════
# E2E-8 main: dry-run/real-run shapes, health preservation, sweep == 1,
# second-run convergence, coexistence forward run, list_sources discovery
# ═══════════════════════════════════════════════════════════════════════

def test_e2e8_main_backfill_reconciliation(tmp_path, monkeypatch):
    """E2E-8 main leg: DRY-RUN then REAL-RUN report shapes (would_create==5 /
    would_link==5 → created==5 / linked==5 / skipped==0), errors[] EXACTLY
    ONE entry in both modes (legacy Event 3's hash-mismatch note),
    degraded_no_file == 0, zero graph changes on dry-run, health buckets
    IDENTICAL before/after (additive-only), hash-pair sweep == exactly 1
    (by-design additive-only divergence), second run convergence
    (created==0/linked==0/skipped==5), then the coexistence forward run
    (count(Source) stays 5, doc Sources gain a SECOND references edge,
    DocumentCreated legacy Events byte-identical)."""
    c, hashes = _main_corpus(tmp_path)
    monkeypatch.setenv("TORTOISE_INGEST_BASE_DIR", str(c))
    sdk = _sdk()
    try:
        g = sdk._get_proj().g
        _seed_main_legacy_events(sdk, c, hashes)

        # before-snapshot (non-trivial health mix: matched + stale + unindexed)
        health_before = sdk.session_index_health(str(c))
        assert health_before["matched"] == 2
        assert [Path(x).name for x in health_before["stale"]] == ["s3.md"]
        assert health_before["duplicates"] == []
        assert health_before["unindexed"] == [str(c / "docA.md"),
                                              str(c / "docB.md")]

        # DocumentCreated legacy Events: byte-identical props snapshot (SC4)
        doc_props_before = (_event_props(g, "docA.md"),
                            _event_props(g, "docB.md"))
        assert doc_props_before[0] is not None and doc_props_before[1] is not None

        # ── DRY RUN ──
        dry = sdk.backfill_sources(str(c), dry_run=True)
        assert set(dry.keys()) == {"dry_run", "corpus_name", "would_create",
                                   "would_link", "degraded_no_file", "errors"}
        assert dry["dry_run"] is True
        assert dry["corpus_name"] == c.name
        assert dry["would_create"] == 5
        assert dry["would_link"] == 5
        assert dry["degraded_no_file"] == 0
        assert len(dry["errors"]) == 1
        assert dry["errors"][0]["cause"] == "hash-mismatch"
        assert "edited since capture" in dry["errors"][0]["error"]
        # zero graph changes
        assert g.query("MATCH (s:Source) RETURN count(s)").result_set[0][0] == 0
        assert g.query("MATCH ()-[r:references]->() RETURN count(r)"
                       ).result_set[0][0] == 0

        # ── REAL RUN ──
        real = sdk.backfill_sources(str(c))
        assert set(real.keys()) == {"dry_run", "corpus_name", "created",
                                    "linked", "skipped", "degraded_no_file",
                                    "errors"}
        assert real["dry_run"] is False
        assert real["created"] == 5
        assert real["linked"] == 5
        assert real["skipped"] == 0
        assert real["degraded_no_file"] == 0
        assert len(real["errors"]) == 1
        assert real["errors"][0]["cause"] == "hash-mismatch"

        # REQUIRED-set invariant sweep clean; raw hash-pair sweep == exactly 1
        # (legacy Event 3's stale stored hash vs its Source's current hash —
        # the plan's own pinned additive-only divergence)
        assert _required_sweep(g) == 0
        assert _hash_pair_sweep(g) == 1

        # every legacy Event gained exactly one Source at corpus://<C>/<rel>
        for rel, eid in (("s1.md", "session_legacy1"),
                         ("s2.md", "session_legacy2"),
                         ("s3.md", "session_legacy3"),
                         ("docA.md", "docA.md"),
                         ("docB.md", "docB.md")):
            rows = g.query(
                "MATCH (e:Event {eventId:$eid})<-[:references]-(s:Source) "
                "RETURN s.url, s.contentHash",
                params={"eid": eid}).result_set
            assert len(rows) == 1, f"{eid} should have exactly one Source"
            assert rows[0][0] == f"corpus://{c.name}/{rel}"
            assert rows[0][1] == compute_file_hash(str(c / rel))

        # health preservation — buckets IDENTICAL before/after (backfill is
        # purely additive: Sources + edges only; Events untouched)
        health_after = sdk.session_index_health(str(c))
        assert health_after["matched"] == health_before["matched"] == 2
        assert health_after["stale"] == health_before["stale"]
        assert health_after["duplicates"] == health_before["duplicates"] == []
        assert health_after["unindexed"] == health_before["unindexed"]

        # ── SECOND RUN — convergence ──
        r2 = sdk.backfill_sources(str(c))
        assert r2["created"] == 0
        assert r2["linked"] == 0
        assert r2["skipped"] == 5
        # node counts unchanged (convergence)
        assert g.query("MATCH (s:Source) RETURN count(s)").result_set[0][0] == 5
        assert g.query("MATCH (e:Event) RETURN count(e)").result_set[0][0] == 5

        # list_sources discovery (CYCLE-25 v3.6 #6 spelling: agentSession)
        sources = {s["url"]: s for s in sdk.list_sources()}
        assert len(sources) == 5
        assert sources[f"corpus://{c.name}/s1.md"]["sourceKind"] == "agentSession"
        assert sources[f"corpus://{c.name}/s3.md"]["sourceKind"] == "agentSession"
        assert sources[f"corpus://{c.name}/docA.md"]["sourceKind"] == "document"
        assert sources[f"corpus://{c.name}/docB.md"]["sourceKind"] == "document"
        for s in sources.values():
            assert s["points"] == 0

        # ── COEXISTENCE forward run (runs LAST — cycle-3 ordering pin) ──
        fwd = sdk.index_directory(str(c), extract_metadata=False)
        assert fwd["failed"] == 0
        assert g.query("MATCH (s:Source) RETURN count(s)").result_set[0][0] == 5
        # the 2 doc Sources now hold TWO references edges each (→ legacy
        # DocumentCreated Event AND → new Document(doc_<rel>))
        for name in ("docA.md", "docB.md"):
            rows = g.query(
                "MATCH (s:Source {url:$url})-[:references]->(n) RETURN count(n)",
                params={"url": f"corpus://{c.name}/{name}"}).result_set
            assert rows[0][0] == 2, name
        # zero new Events created by the forward path for the docs
        assert g.query("MATCH (e:Event {eventKind:'DocumentCreated'}) "
                       "RETURN count(e)").result_set[0][0] == 2
        # DocumentCreated legacy Events byte-identical (frozen, SC4)
        assert _event_props(g, "docA.md") == doc_props_before[0]
        assert _event_props(g, "docB.md") == doc_props_before[1]
        # CYCLE-10: buckets + sweep never move in this fixture (the forward
        # run's hash-equal gate skips the stale Event-3 unit)
        health_fwd = sdk.session_index_health(str(c))
        assert health_fwd["matched"] == 2
        assert [Path(x).name for x in health_fwd["stale"]] == ["s3.md"]
        assert health_fwd["unindexed"] == [str(c / "docA.md"),
                                           str(c / "docB.md")]
        assert _hash_pair_sweep(g) == 1
    finally:
        sdk.close()


def test_e2e8_coexistence_embedding_gate_heal(tmp_path, monkeypatch):
    """E2E-8 coexistence × embedding-gate leg (cycle-7): legacy session Events
    carrying NULL embeddings HEAL under an extract_metadata=True re-run with
    the embedding mock available — additive write (embedding + marker only);
    health buckets IDENTICAL immediately before/after, DocumentCreated Events
    STILL byte-identical, sweep count UNCHANGED by the heal itself."""
    import tortoise.sdk as sdkmod
    c, hashes = _main_corpus(tmp_path)
    monkeypatch.setenv("TORTOISE_INGEST_BASE_DIR", str(c))
    sdk = _sdk()
    try:
        g = sdk._get_proj().g
        _seed_main_legacy_events(sdk, c, hashes)
        sdk.backfill_sources(str(c))
        # forward run (no-network) — the pre-heal state
        sdk.index_directory(str(c), extract_metadata=False)
        assert g.query("MATCH (e:Event {eventKind:'AgentSession'}) "
                       "WHERE e.embedding IS NULL RETURN count(e)"
                       ).result_set[0][0] == 3
        doc_props_before = (_event_props(g, "docA.md"),
                            _event_props(g, "docB.md"))
        health_before = sdk.session_index_health(str(c))
        sweep_before = _hash_pair_sweep(g)

        def _available(self, *a, **k):  # noqa: ANN001, RUF100
            return [0.1] * 384
        monkeypatch.setattr(sdkmod.TortoiseSDK, "_session_embedding", _available)
        r = sdk.index_directory(str(c), extract_metadata=True)
        assert r["updated"] == 3 and r["failed"] == 0
        # healed — the gate applies to backfill-linked units (§5.1 pin (a))
        assert g.query("MATCH (e:Event {eventKind:'AgentSession'}) "
                       "WHERE e.embedding IS NULL RETURN count(e)"
                       ).result_set[0][0] == 0
        # DELTA scoping: health identical, doc Events byte-identical, sweep
        # unchanged by the heal (the heal writes no hash)
        health_after = sdk.session_index_health(str(c))
        assert health_after["matched"] == health_before["matched"]
        assert health_after["stale"] == health_before["stale"]
        assert health_after["unindexed"] == health_before["unindexed"]
        assert _event_props(g, "docA.md") == doc_props_before[0]
        assert _event_props(g, "docB.md") == doc_props_before[1]
        assert _hash_pair_sweep(g) == sweep_before
    finally:
        sdk.close()


# ═══════════════════════════════════════════════════════════════════════
# Edge-state variants (I20) + degraded variant
# ═══════════════════════════════════════════════════════════════════════

def test_e2e8_degraded_deleted_file(tmp_path):
    """E2E-8 degraded variant: delete one source file before backfill →
    Source still created, contentHash == Event.file_hash, counted in
    degraded_no_file; second run zero-create."""
    c = tmp_path / "corpus"; c.mkdir()  # noqa: E702
    gone = c / "gone.md"
    gone.write_text(SESSION_FIXTURE.format(sid="g1", n=1))
    stored = compute_file_hash(str(gone))
    gone.unlink()
    sdk = _sdk()
    try:
        g = sdk._get_proj().g
        _create_legacy_event(g, event_id="session_g1", kind="AgentSession",
                             rel_path="gone.md", file_hash=stored, title="G1")
        r = sdk.backfill_sources(str(c))
        assert r["created"] == 1 and r["linked"] == 1
        assert r["degraded_no_file"] == 1
        assert len(r["errors"]) == 1
        assert r["errors"][0]["cause"] == "missing"
        rows = g.query(
            "MATCH (e:Event {eventId:'session_g1'})<-[:references]-(s:Source) "
            "RETURN s.contentHash, s.url").result_set
        assert rows[0][0] == stored          # degraded to the stored hash
        assert rows[0][1] == f"corpus://{c.name}/gone.md"
        assert _required_sweep(g) == 0
        r2 = sdk.backfill_sources(str(c))
        assert r2["created"] == 0 and r2["skipped"] == 1
    finally:
        sdk.close()


def test_e2e8_edge_null_hash_no_file(tmp_path):
    """E2E-8(a): legacy Event with source_file set, file_hash null, file
    deleted → errors[] entry, NO Source created (REQUIRED contentHash cannot
    be honored), run continues over remaining files; identical in dry-run and
    real run."""
    c = tmp_path / "corpus"; c.mkdir()  # noqa: E702
    (c / "ok.md").write_text(SESSION_FIXTURE.format(sid="ok1", n=1))
    sdk = _sdk()
    try:
        g = sdk._get_proj().g
        _create_legacy_event(g, event_id="session_gone", kind="AgentSession",
                             rel_path="gone.md")            # null file_hash
        _create_legacy_event(g, event_id="session_ok1", kind="AgentSession",
                             rel_path="ok.md",
                             file_hash=compute_file_hash(str(c / "ok.md")))
        dry = sdk.backfill_sources(str(c), dry_run=True)
        assert dry["would_create"] == 1          # ok.md only
        assert len(dry["errors"]) == 1
        assert dry["errors"][0]["cause"] == "structural"
        real = sdk.backfill_sources(str(c))
        assert real["created"] == 1 and real["linked"] == 1
        assert len(real["errors"]) == 1
        assert real["errors"][0]["cause"] == "structural"
        assert g.query("MATCH (s:Source) RETURN count(s)").result_set[0][0] == 1
        # the REQUIRED sweep asserts no null-contentHash Source was written
        assert _required_sweep(g) == 0
        assert g.query(
            "MATCH (s:Source {url:$u}) RETURN count(s)",
            params={"u": f"corpus://{c.name}/gone.md"}).result_set[0][0] == 0
    finally:
        sdk.close()


def test_e2e8_edge_outside_root(tmp_path):
    """E2E-8(b): legacy Event whose source_file is not relativizable under the
    corpus root → errors[] entry counted IDENTICALLY in dry-run and real run
    (would_create excludes it)."""
    c = tmp_path / "corpus"; c.mkdir()  # noqa: E702
    (c / "ok.md").write_text(SESSION_FIXTURE.format(sid="ok1", n=1))
    outside = tmp_path / "outside"; outside.mkdir()  # noqa: E702
    (outside / "x.md").write_text("outside body")
    sdk = _sdk()
    try:
        g = sdk._get_proj().g
        _create_legacy_event(g, event_id="session_abs", kind="AgentSession",
                             rel_path=str(outside / "x.md"), file_hash="h")
        _create_legacy_event(g, event_id="session_ok1", kind="AgentSession",
                             rel_path="ok.md",
                             file_hash=compute_file_hash(str(c / "ok.md")))
        dry = sdk.backfill_sources(str(c), dry_run=True)
        assert dry["would_create"] == 1
        assert len(dry["errors"]) == 1
        assert dry["errors"][0]["cause"] == "escape"
        real = sdk.backfill_sources(str(c))
        assert real["created"] == 1 and real["skipped"] == 0
        assert len(real["errors"]) == 1
        assert real["errors"][0]["cause"] == "escape"
        assert g.query("MATCH (s:Source) RETURN count(s)").result_set[0][0] == 1
    finally:
        sdk.close()


def test_e2e8_edge_edited_since_capture(tmp_path):
    """E2E-8(c): fixture file body changed after the legacy Event's file_hash
    was stored → backfill creates the Source with the CURRENT file hash, the
    Event KEEPS its stored file_hash (additive-only), the mismatch is recorded
    in errors[], sweep == +1, second backfill run zero-create."""
    c = tmp_path / "corpus"; c.mkdir()  # noqa: E702
    p = c / "s1.md"
    p.write_text(SESSION_FIXTURE.format(sid="legacy1", n=1))
    stored = compute_file_hash(str(p))
    sdk = _sdk()
    try:
        g = sdk._get_proj().g
        _create_legacy_event(g, event_id="session_legacy1",
                             kind="AgentSession", rel_path="s1.md",
                             file_hash=stored, title="S1")
        p.write_text(SESSION_FIXTURE.format(sid="legacy1", n=1) + "EDITED\n")
        r = sdk.backfill_sources(str(c))
        assert r["created"] == 1 and r["linked"] == 1
        assert len(r["errors"]) == 1
        assert r["errors"][0]["cause"] == "hash-mismatch"
        rows = g.query(
            "MATCH (e:Event {eventId:'session_legacy1'}) RETURN e.file_hash"
        ).result_set
        assert rows[0][0] == stored          # Event frozen (additive-only)
        src = g.query(
            "MATCH (s:Source {url:$u}) RETURN s.contentHash",
            params={"u": f"corpus://{c.name}/s1.md"}).result_set
        assert src[0][0] == compute_file_hash(str(p))   # CURRENT hash
        assert _hash_pair_sweep(g) == 1
        r2 = sdk.backfill_sources(str(c))
        assert r2["created"] == 0 and r2["skipped"] == 1
    finally:
        sdk.close()


def test_e2e8_edge_over_limit(tmp_path, monkeypatch):
    """E2E-8(d): a legacy session file grown beyond max_file_mb → backfill
    NEVER reads it (two-layer size-guard inheritance — ZERO compute_file_hash
    calls): Source created from Event.file_hash, degraded_no_file + errors[]
    note; a subsequent forward index_directory reports the same file failed
    WITHOUT forking a second Source (the backfill Source already owns the
    url)."""
    import tortoise.file_indexer as fimod
    c = tmp_path / "corpus"; c.mkdir()  # noqa: E702
    big = c / "big.md"
    big.write_text(SESSION_FIXTURE.format(sid="big1", n=1))
    stored = compute_file_hash(str(big))
    with open(big, "ab") as f:
        f.truncate(2 * 1024 * 1024)          # sparse 2 MiB > 1 MiB guard
    monkeypatch.setenv("TORTOISE_MAX_FILE_MB", "1")
    monkeypatch.setenv("TORTOISE_INGEST_BASE_DIR", str(c))
    sdk = _sdk()
    try:
        g = sdk._get_proj().g
        _create_legacy_event(g, event_id="session_big1", kind="AgentSession",
                             rel_path="big.md", file_hash=stored, title="B1")
        reads = {"hash_calls": 0}
        orig = fimod.compute_file_hash

        def _counted(path):
            reads["hash_calls"] += 1
            return orig(path)
        monkeypatch.setattr(fimod, "compute_file_hash", _counted)
        r = sdk.backfill_sources(str(c))
        assert reads["hash_calls"] == 0      # the guard refused the read
        assert r["created"] == 1 and r["linked"] == 1
        assert r["degraded_no_file"] == 1
        assert len(r["errors"]) == 1
        assert r["errors"][0]["cause"] == "size"
        assert "max_file_mb" in r["errors"][0]["error"]
        src = g.query(
            "MATCH (s:Source {url:$u}) RETURN s.contentHash",
            params={"u": f"corpus://{c.name}/big.md"}).result_set
        assert src[0][0] == stored           # degraded to the stored hash
        assert _required_sweep(g) == 0
        monkeypatch.setattr(fimod, "compute_file_hash", orig)
        fwd = sdk.index_directory(str(c), extract_metadata=False)
        assert fwd["failed"] == 1            # guard: file failed, never read
        assert g.query("MATCH (s:Source) RETURN count(s)").result_set[0][0] == 1
    finally:
        sdk.close()


def test_e2e8_hardlink_out_fail_closed_and_escape(tmp_path, monkeypatch):
    """E2E-8(e): hardlink/escape fail-closed — NO walk context. A legacy
    AgentSession Event whose source_file is an in-root hardlink entry of an
    OUTSIDE-root inode (st_nlink > 1) → NEVER READ (dry-run FIRST: ZERO
    compute_file_hash calls), degrade to Event.file_hash; the escape sibling
    (symlink alias resolving outside the root) → errors[] entry, NO Source,
    run completes — never an aborting raise. A subsequent forward
    index_directory reports the hardlink file failed WITHOUT forking a second
    Source (the backfill Source already owns the url)."""
    import tortoise.file_indexer as fimod
    c = tmp_path / "corpus"; c.mkdir()  # noqa: E702
    outside = tmp_path / "outside"; outside.mkdir()  # noqa: E702
    (outside / "out.md").write_text(
        "---\nsessionId: ho\ntitle: HO\n---\nBody")
    os.link(outside / "out.md", c / "hardlink-out.md")
    (c / "leak.md").symlink_to(outside / "out.md")
    monkeypatch.setenv("TORTOISE_INGEST_BASE_DIR", str(c))
    sdk = _sdk()
    try:
        g = sdk._get_proj().g
        stored = compute_file_hash(str(c / "hardlink-out.md"))
        _create_legacy_event(g, event_id="session_hl", kind="AgentSession",
                             rel_path="hardlink-out.md", file_hash=stored,
                             title="HL")
        _create_legacy_event(g, event_id="session_leak", kind="AgentSession",
                             rel_path="leak.md", file_hash=stored,
                             title="Leak")
        reads = {"hash_calls": 0}
        orig = fimod.compute_file_hash

        def _counted(path):
            reads["hash_calls"] += 1
            return orig(path)
        monkeypatch.setattr(fimod, "compute_file_hash", _counted)

        # ── dry-run leg FIRST (cycle-6): ZERO reads, degraded Source counted
        # in would_create, notes reported ──
        dry = sdk.backfill_sources(str(c), dry_run=True)
        assert reads["hash_calls"] == 0
        assert dry["would_create"] == 1       # hardlink-out (degraded) only
        assert dry["would_link"] == 1
        assert dry["degraded_no_file"] == 1
        assert len(dry["errors"]) == 2        # hardlink note + escape entry
        causes = sorted(e["cause"] for e in dry["errors"])
        assert causes == ["escape", "inode"]
        assert g.query("MATCH (s:Source) RETURN count(s)").result_set[0][0] == 0

        monkeypatch.setattr(fimod, "compute_file_hash", orig)
        real = sdk.backfill_sources(str(c))
        assert reads["hash_calls"] == 0       # real run also refuses both reads
        assert real["created"] == 1 and real["linked"] == 1
        assert real["skipped"] == 0
        assert real["degraded_no_file"] == 1
        assert len(real["errors"]) == 2
        # NO Source for the escape sibling; the hardlink Source owns the url
        assert g.query("MATCH (s:Source) RETURN count(s)").result_set[0][0] == 1
        src = g.query(
            "MATCH (s:Source {url:$u}) RETURN s.contentHash, s.sourceKind",
            params={"u": f"corpus://{c.name}/hardlink-out.md"}).result_set
        assert src[0][0] == stored
        assert src[0][1] == "agentSession"
        assert _required_sweep(g) == 0

        # forward index_directory over the SAME corpus: the hardlink file
        # reports failed (inode reconciliation) WITHOUT forking a second Source
        fwd = sdk.index_directory(str(c), extract_metadata=False)
        assert fwd["failed"] == 2             # hardlink-out + leak (escape)
        assert g.query("MATCH (s:Source) RETURN count(s)").result_set[0][0] == 1
        assert _hash_pair_sweep(g) == 0       # degraded hash == stored hash
    finally:
        sdk.close()


def test_e2e8_hardlink_pair_sibling(tmp_path):
    """E2E-8(f) hardlink-pair sibling (always-run — os.link only, NO mount
    privilege): TWO legacy AgentSession Events whose source_files are an
    os.link pair (BOTH in-root, ONE physical file, st_nlink == 2) → the
    same-inode scan does NOT apply (cycle-7 scan-scope pin); NEITHER file is
    read; each Event degrades to its own stored file_hash → TWO degraded
    Sources at BOTH urls, degraded_no_file == 2 + errors[] entries; second
    run zero-create (both Events skipped)."""
    import tortoise.file_indexer as fimod
    c = tmp_path / "corpus"; c.mkdir()  # noqa: E702
    (c / "a.md").write_text(SESSION_FIXTURE.format(sid="hpa", n=1))
    os.link(c / "a.md", c / "b.md")
    stored = compute_file_hash(str(c / "a.md"))
    sdk = _sdk()
    try:
        g = sdk._get_proj().g
        _create_legacy_event(g, event_id="session_hpa", kind="AgentSession",
                             rel_path="a.md", file_hash=stored, title="A")
        _create_legacy_event(g, event_id="session_hpb", kind="AgentSession",
                             rel_path="b.md", file_hash=stored, title="B")
        reads = {"hash_calls": 0}
        orig = fimod.compute_file_hash

        def _counted(path):
            reads["hash_calls"] += 1
            return orig(path)
        fimod.compute_file_hash = _counted
        try:
            r = sdk.backfill_sources(str(c))
        finally:
            fimod.compute_file_hash = orig
        assert reads["hash_calls"] == 0       # NEITHER file read
        assert r["created"] == 2 and r["linked"] == 2
        assert r["skipped"] == 0
        assert r["degraded_no_file"] == 2
        assert len(r["errors"]) == 2
        assert all(e["cause"] == "inode" for e in r["errors"])
        # url-keyed shape: TWO distinct Sources at BOTH urls
        for rel in ("a.md", "b.md"):
            rows = g.query(
                "MATCH (s:Source {url:$u}) RETURN s.contentHash",
                params={"u": f"corpus://{c.name}/{rel}"}).result_set
            assert rows[0][0] == stored
        assert g.query("MATCH (s:Source) RETURN count(s)").result_set[0][0] == 2
        assert _required_sweep(g) == 0
        r2 = sdk.backfill_sources(str(c))
        assert r2["created"] == 0 and r2["linked"] == 0 and r2["skipped"] == 2
    finally:
        sdk.close()


def test_e2e8_hardlink_pair_mixed_deleted(tmp_path):
    """E2E-8(f) mixed leg: the same os.link pair with ONE member's file
    deleted before backfill → the missing path degrades to its file_hash
    (degraded_no_file), while the REMAINING path's nlink drops to 1 →
    scanned/read normally (Source from the CURRENT file hash); two distinct
    urls, one Source each, the run completes honestly."""
    c = tmp_path / "corpus"; c.mkdir()  # noqa: E702
    (c / "a.md").write_text(SESSION_FIXTURE.format(sid="hpa", n=1))
    os.link(c / "a.md", c / "b.md")
    stored = compute_file_hash(str(c / "a.md"))
    sdk = _sdk()
    try:
        g = sdk._get_proj().g
        _create_legacy_event(g, event_id="session_hpa", kind="AgentSession",
                             rel_path="a.md", file_hash=stored, title="A")
        _create_legacy_event(g, event_id="session_hpb", kind="AgentSession",
                             rel_path="b.md", file_hash=stored, title="B")
        (c / "b.md").unlink()
        r = sdk.backfill_sources(str(c))
        assert r["created"] == 2 and r["linked"] == 2
        assert r["degraded_no_file"] == 1     # only the missing b.md
        assert len(r["errors"]) == 1
        assert r["errors"][0]["cause"] == "missing"
        rows_a = g.query(
            "MATCH (s:Source {url:$u}) RETURN s.contentHash",
            params={"u": f"corpus://{c.name}/a.md"}).result_set
        assert rows_a[0][0] == compute_file_hash(str(c / "a.md"))
        rows_b = g.query(
            "MATCH (s:Source {url:$u}) RETURN s.contentHash",
            params={"u": f"corpus://{c.name}/b.md"}).result_set
        assert rows_b[0][0] == stored         # degraded
        assert g.query("MATCH (s:Source) RETURN count(s)").result_set[0][0] == 2
    finally:
        sdk.close()


def test_e2e8_mixed_state_dry_run(tmp_path):
    """E2E-8 mixed-state dry-run variant: SOME legacy Events already have
    Sources (pre-linked by an earlier partial run) and some do not →
    dry_run would_create EXCLUDES already-existing Sources, would_link counts
    ONLY missing edges (never double-counts the linked ones); the subsequent
    real run confirms exactly those numbers."""
    c = tmp_path / "corpus"; c.mkdir()  # noqa: E702
    (c / "a.md").write_text(SESSION_FIXTURE.format(sid="m1", n=1))
    (c / "b.md").write_text(SESSION_FIXTURE.format(sid="m2", n=2))
    sdk = _sdk()
    try:
        g = sdk._get_proj().g
        _create_legacy_event(g, event_id="session_m1", kind="AgentSession",
                             rel_path="a.md",
                             file_hash=compute_file_hash(str(c / "a.md")))
        _create_legacy_event(g, event_id="session_m2", kind="AgentSession",
                             rel_path="b.md",
                             file_hash=compute_file_hash(str(c / "b.md")))
        # pre-link session_m1 fully (partial-run simulation)
        sdk.create_source(f"corpus://{c.name}/a.md", "agentSession",
                          contentHash=compute_file_hash(str(c / "a.md")),
                          title="A", _searchText="A")
        g.query(
            "MATCH (s:Source {url:$u}), (e:Event {eventId:'session_m1'}) "
            "MERGE (s)-[:references]->(e)",
            params={"u": f"corpus://{c.name}/a.md"})
        dry = sdk.backfill_sources(str(c), dry_run=True)
        assert dry["would_create"] == 1       # b.md only
        assert dry["would_link"] == 1         # b.md only (a.md already linked)
        real = sdk.backfill_sources(str(c))
        assert real["created"] == 1 and real["linked"] == 1
        assert real["skipped"] == 1           # session_m1 pre-linked
        assert g.query("MATCH (s:Source) RETURN count(s)").result_set[0][0] == 2
        assert g.query("MATCH ()-[r:references]->() RETURN count(r)"
                       ).result_set[0][0] == 2
    finally:
        sdk.close()


def test_e2e8_pre_320_shape_structural(tmp_path):
    """E2E-8 pre-#320-shape leg (cycle-21): an AgentSession Event with NO
    source_file AND no file_hash → errors[] structural entry, NO Source, NO
    raise, run completes, dry-run counts identically."""
    c = tmp_path / "corpus"; c.mkdir()  # noqa: E702
    (c / "ok.md").write_text(SESSION_FIXTURE.format(sid="ok1", n=1))
    sdk = _sdk()
    try:
        g = sdk._get_proj().g
        _create_legacy_event(g, event_id="session_legacy",
                             kind="AgentSession")           # neither prop
        _create_legacy_event(g, event_id="session_ok1", kind="AgentSession",
                             rel_path="ok.md",
                             file_hash=compute_file_hash(str(c / "ok.md")))
        dry = sdk.backfill_sources(str(c), dry_run=True)
        assert dry["would_create"] == 1
        assert len(dry["errors"]) == 1
        assert dry["errors"][0]["cause"] == "structural"
        assert "source_file" in dry["errors"][0]["error"]
        real = sdk.backfill_sources(str(c))
        assert real["created"] == 1 and real["linked"] == 1
        assert len(real["errors"]) == 1
        assert real["errors"][0]["cause"] == "structural"
        assert g.query("MATCH (s:Source) RETURN count(s)").result_set[0][0] == 1
        assert _required_sweep(g) == 0
    finally:
        sdk.close()


def test_e2e8_undecodable_source_file(tmp_path, monkeypatch):
    """E2E-8 undecodable-source_file leg (cycle-12; the (x) class via the
    mockable seam — invalid-UTF-8 filename bytes are UNCREATABLE on
    macOS/APFS, so the seam injects the surrogate string a Linux-side decode
    produces): a legacy Event whose source_file round-trips to an undecodable
    name → errors[] entry (cause filename), run continues, never an aborting
    raise (the shared per-file guard at the entry points)."""
    import tortoise.file_indexer as fimod
    c = tmp_path / "corpus"; c.mkdir()  # noqa: E702
    (c / "ok.md").write_text(SESSION_FIXTURE.format(sid="ok1", n=1))
    sdk = _sdk()
    try:
        g = sdk._get_proj().g
        # the legacy Event carries a normal stored path; the #909-shared
        # identity seam (derive_source_url) raises the undecodable class for
        # that path exactly as urllib.parse.quote does for a surrogateescape
        # string (verified: quote('\udcff', safe='') raises UnicodeEncodeError)
        _create_legacy_event(g, event_id="session_bad", kind="AgentSession",
                             rel_path="bad.md", file_hash="h")
        _create_legacy_event(g, event_id="session_ok1", kind="AgentSession",
                             rel_path="ok.md",
                             file_hash=compute_file_hash(str(c / "ok.md")))
        orig = fimod.derive_source_url

        def _undecodable(path, corpus_root, corpus_name=None):
            if str(path).endswith("bad.md"):
                raise UnicodeEncodeError(
                    "utf-8", "\udcff", 0, 1, "surrogates not allowed")
            return orig(path, corpus_root, corpus_name)
        monkeypatch.setattr(fimod, "derive_source_url", _undecodable)
        dry = sdk.backfill_sources(str(c), dry_run=True)
        assert dry["would_create"] == 1
        assert len(dry["errors"]) == 1
        assert dry["errors"][0]["cause"] == "filename"
        real = sdk.backfill_sources(str(c))
        assert real["created"] == 1 and real["linked"] == 1
        assert len(real["errors"]) == 1
        assert real["errors"][0]["cause"] == "filename"
        assert g.query("MATCH (s:Source) RETURN count(s)").result_set[0][0] == 1
    finally:
        sdk.close()


# ═══════════════════════════════════════════════════════════════════════
# Kill-between crash-repair variant
# ═══════════════════════════════════════════════════════════════════════

def test_e2e8_kill_between_crash_repair(tmp_path, monkeypatch):
    """E2E-8 kill-between crash-repair variant: monkeypatch the
    references-link step to raise AFTER the first Source is created → run 1
    reports that leg failed (errors[] entry); the graph holds a Source with NO
    incoming edge. Re-run backfill → linked == 1 (REPAIR of the missing edge),
    created == 0 (a re-run MUST NOT skip the Event just because the Source
    exists — W2 crash-repair pin), edge exists; third run created == 0,
    linked == 0 (convergence)."""
    import tortoise.sdk as sdkmod
    c = tmp_path / "corpus"; c.mkdir()  # noqa: E702
    (c / "s1.md").write_text(SESSION_FIXTURE.format(sid="legacy1", n=1))
    sdk = _sdk()
    orig_link = sdkmod.TortoiseSDK._backfill_link
    try:
        g = sdk._get_proj().g
        _create_legacy_event(g, event_id="session_legacy1",
                             kind="AgentSession", rel_path="s1.md",
                             file_hash=compute_file_hash(str(c / "s1.md")),
                             title="S1")

        def _boom(self, url, event_id):
            raise RuntimeError("simulated crash between Source-create and edge-link")
        monkeypatch.setattr(sdkmod.TortoiseSDK, "_backfill_link", _boom)
        r1 = sdk.backfill_sources(str(c))
        assert r1["created"] == 1
        assert r1["linked"] == 0
        assert r1["skipped"] == 0
        assert len(r1["errors"]) == 1
        assert r1["errors"][0]["cause"] == "db"
        assert "references-link" in r1["errors"][0]["error"]
        # graph holds a Source with NO incoming edge
        rows = g.query(
            "MATCH (s:Source {url:$u}) RETURN s.url",
            params={"u": f"corpus://{c.name}/s1.md"}).result_set
        assert len(rows) == 1
        assert g.query(
            "MATCH (s:Source)-[:references]->(e:Event {eventId:'session_legacy1'}) "
            "RETURN count(*)").result_set[0][0] == 0

        # ── repair run ──
        monkeypatch.setattr(sdkmod.TortoiseSDK, "_backfill_link", orig_link)
        r2 = sdk.backfill_sources(str(c))
        assert r2["created"] == 0
        assert r2["linked"] == 1             # REPAIR of the missing edge
        assert r2["skipped"] == 0
        assert r2["errors"] == []
        assert g.query(
            "MATCH (s:Source)-[:references]->(e:Event {eventId:'session_legacy1'}) "
            "RETURN count(*)").result_set[0][0] == 1

        # ── third run: convergence ──
        r3 = sdk.backfill_sources(str(c))
        assert r3["created"] == 0 and r3["linked"] == 0 and r3["skipped"] == 1
        assert _required_sweep(g) == 0
    finally:
        sdk.close()


# ═══════════════════════════════════════════════════════════════════════
# Concurrent backfill+index leg (cycle-12; E2E-9 choreography — a SEQUENTIAL
# execution is a TEST FAILURE)
# ═══════════════════════════════════════════════════════════════════════

def test_e2e8_source_write_failure_honest_counters(tmp_path, monkeypatch):
    """Review-gate regression (P1 — honest counters in the `db` failure
    class): a Source-write failure must NEVER increment `linked` (an edge
    that was not written is never counted) — run 1 reports created==0 /
    linked==0 / skipped==0 with ONE errors[] cause "db" entry and a clean
    graph; a re-run REPAIRS both the Source and the edge (created==1 /
    linked==1 / errors==[]); the third run converges (skipped==1)."""
    import tortoise.sdk as sdkmod
    c = tmp_path / "corpus"; c.mkdir()  # noqa: E702
    (c / "s1.md").write_text(SESSION_FIXTURE.format(sid="legacy1", n=1))
    sdk = _sdk()
    orig = sdkmod.TortoiseSDK._index_source_merge
    try:
        g = sdk._get_proj().g
        _create_legacy_event(g, event_id="session_legacy1",
                             kind="AgentSession", rel_path="s1.md",
                             file_hash=compute_file_hash(str(c / "s1.md")),
                             title="S1")

        def _boom(self, *a, **k):  # noqa: ANN001, ANN002, ANN003, RUF100
            raise RuntimeError("simulated Source-write failure (db class)")
        monkeypatch.setattr(sdkmod.TortoiseSDK, "_index_source_merge", _boom)
        r1 = sdk.backfill_sources(str(c))
        assert r1["created"] == 0 and r1["linked"] == 0 and r1["skipped"] == 0
        assert len(r1["errors"]) == 1
        assert r1["errors"][0]["cause"] == "db"
        assert "Source write failed" in r1["errors"][0]["error"]
        # clean graph — no phantom Source, no edge
        assert g.query("MATCH (s:Source) RETURN count(s)").result_set[0][0] == 0
        assert g.query("MATCH ()-[r:references]->() RETURN count(r)"
                       ).result_set[0][0] == 0
        assert _required_sweep(g) == 0

        # re-run repairs both the Source and the edge
        monkeypatch.setattr(sdkmod.TortoiseSDK, "_index_source_merge", orig)
        r2 = sdk.backfill_sources(str(c))
        assert r2["created"] == 1 and r2["linked"] == 1 and r2["skipped"] == 0
        assert r2["errors"] == []
        assert g.query("MATCH ()-[r:references]->() RETURN count(r)"
                       ).result_set[0][0] == 1

        # third run: convergence
        r3 = sdk.backfill_sources(str(c))
        assert r3["created"] == 0 and r3["linked"] == 0 and r3["skipped"] == 1
        assert _required_sweep(g) == 0
    finally:
        sdk.close()


def test_e2e8_concurrent_backfill_and_index(tmp_path):
    """E2E-8 concurrent backfill+index leg: legacy AgentSession Event
    (session_legacy1, source_file=conv.md) + fixture conv.md whose frontmatter
    sessionId is legacy1 → barrier-released CONCURRENT backfill_sources +
    index_directory on PER-THREAD SDK instances against ONE shared embedded
    daemon (marker-node warm-up proves daemon reuse; a sequential execution is
    a test failure) → count(Source) == 1 (url MERGE convergence across two
    different code paths), count(Event) == 1, count(references) == 1, honest
    counters, second-run zero-create."""
    c = tmp_path / "corpus"; c.mkdir()  # noqa: E702
    (c / "conv.md").write_text(SESSION_FIXTURE.format(sid="legacy1", n=1))
    db = _db()
    seed = TortoiseSDK(db, namespace="e2e-900")
    try:
        g = seed._get_proj().g
        _create_legacy_event(g, event_id="session_legacy1",
                             kind="AgentSession", rel_path="conv.md",
                             file_hash=compute_file_hash(str(c / "conv.md")),
                             title="Conv")
    finally:
        seed.close()

    n = 2
    # SDK instances constructed SEQUENTIALLY (never from racing threads —
    # deterministic daemon reuse; a racing constructor could start a second
    # daemon = split-brain)
    sdks = [TortoiseSDK(db, namespace="e2e-900") for _ in range(n)]
    barrier = threading.Barrier(n)
    results: list[dict | None] = [None] * n
    errors: list[BaseException] = []
    reuse_holds = True

    def _worker(i: int) -> None:
        nonlocal reuse_holds
        sdk = sdks[i]
        try:
            barrier.wait(timeout=60)          # all SDKs constructed
            if i == 0:
                harness.marker_warmup(sdk)    # warm the marker on the daemon
            barrier.wait(timeout=60)          # marker written
            if not harness.assert_marker_visible(sdk):
                reuse_holds = False           # daemon reuse does NOT hold
            barrier.wait(timeout=60)          # reuse verified (or not)
            if not reuse_holds:
                return
            if i == 0:
                results[i] = sdk.backfill_sources(str(c))
            else:
                results[i] = sdk.index_directory(str(c), extract_metadata=False)
        except BaseException as e:  # noqa: BLE001, RUF100
            errors.append(e)
            try:  # noqa: SIM105
                barrier.wait(timeout=60)
            except Exception:  # noqa: BLE001, RUF100
                pass
        finally:
            sdk.close()

    threads = [threading.Thread(target=_worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=600)
    if errors:
        raise errors[0]
    if not reuse_holds:
        pytest.skip("embedded daemon reuse does not hold on this platform — "
                    "skipping the threads leg (§7 harness pin)")

    check = TortoiseSDK(db, namespace="e2e-900")
    try:
        g = check._get_proj().g
        # url MERGE convergence across two different code paths
        assert g.query("MATCH (s:Source) RETURN count(s)").result_set[0][0] == 1
        assert g.query("MATCH (e:Event) RETURN count(e)").result_set[0][0] == 1
        assert g.query("MATCH ()-[r:references]->() RETURN count(r)"
                       ).result_set[0][0] == 1
        assert _required_sweep(g) == 0
        assert _hash_pair_sweep(g) == 0
        # honest counters: created counts Sources created, linked counts
        # Events whose edge was written (a fresh Source always counts in BOTH,
        # as in the main fixture) — so the valid outcomes are (created=1,
        # linked=1, skipped=0) if backfill won the race, or (0, 0, 1) if the
        # index thread completed the unit first
        bf = results[0]
        assert bf["created"] in (0, 1) and bf["linked"] in (0, 1)
        assert bf["skipped"] in (0, 1)
        assert bf["created"] + bf["skipped"] == 1
        # P2 (review gate): the index thread's Source MERGE can land between
        # backfill's probe and its own merge → edge-linked-while-not-created
        # (linked=1, created=0) is legitimate repair-work accounting under the
        # race; the graph-level count(references)==1 assertion pins the real
        # contract.
        assert bf["linked"] in (bf["created"], 1)
        assert bf["degraded_no_file"] == 0
        idx = results[1]
        assert (idx["indexed"] + idx["updated"] + idx["skipped"] + idx["failed"]
                == idx["file_count"] == 1)
        # second-run zero-create on BOTH paths
        sdk2 = TortoiseSDK(db, namespace="e2e-900")
        try:
            r2 = sdk2.backfill_sources(str(c))
            assert r2["created"] == 0 and r2["linked"] == 0 and r2["skipped"] == 1
            rr = sdk2.index_directory(str(c), extract_metadata=False)
            assert rr["skipped"] == 1 and rr["indexed"] == 0
        finally:
            sdk2.close()
    finally:
        check.close()


# ═══════════════════════════════════════════════════════════════════════
# E2E-8 MOVED-file leg (cycle-14/15) + DELETE-after-fork sub-leg
# ═══════════════════════════════════════════════════════════════════════

def test_e2e8_moved_file_pure_move(tmp_path):
    """E2E-8 MOVED-file leg — PURE-MOVE parametrization (cycle-14/15):
    legacy Event with source_file = old rel-path; fixture file at a NEW
    rel-path with UNCHANGED content → backfill creates the degraded Source
    at the old url + a 'moved' note (the hash cross-check matches); the
    forward run creates the fresh Source at the new url; the two-Source
    fork is EXPLICITLY counted; the post-forward sweep == 0 (pure-move:
    content-unchanged, both pairs hash-equal)."""
    c = tmp_path / "corpus"; c.mkdir()  # noqa: E702
    sub = c / "archive"; sub.mkdir()  # noqa: E702
    old = c / "moved.md"
    old.write_text(SESSION_FIXTURE.format(sid="mv1", n=1))
    stored = compute_file_hash(str(old))
    new_path = sub / "moved.md"
    os.rename(str(old), str(new_path))       # content UNCHANGED
    sdk = _sdk()
    try:
        g = sdk._get_proj().g
        _create_legacy_event(g, event_id="session_mv1", kind="AgentSession",
                             rel_path="moved.md", file_hash=stored,
                             title="Moved")
        r = sdk.backfill_sources(str(c))
        assert r["created"] == 1 and r["linked"] == 1
        assert r["degraded_no_file"] == 1
        assert len(r["errors"]) == 1
        assert r["errors"][0]["cause"] == "moved"      # distinguishing wording
        assert "moved" in r["errors"][0]["error"]
        # degraded Source at the OLD url with the stored hash
        rows = g.query(
            "MATCH (e:Event {eventId:'session_mv1'})<-[:references]-(s:Source) "
            "RETURN s.contentHash, s.url").result_set
        assert rows[0][0] == stored
        assert rows[0][1] == f"corpus://{c.name}/moved.md"
        assert _required_sweep(g) == 0
        # dry-run parity: the same fork is reported identically
        r_dry = sdk.backfill_sources(str(c), dry_run=True)
        assert r_dry["would_create"] == 0 and r_dry["would_link"] == 0
        # forward run: creates the FRESH Source at the new url (two-Source
        # fork explicitly counted) — the new-path file is session-classified
        # and the OLD Event already owns session_mv1 (primary election keeps
        # the Event; the new Source is registered + edge-less by election)
        rf = sdk.index_directory(str(c), extract_metadata=False)  # noqa: F841
        urls = [x[0] for x in g.query("MATCH (s:Source) RETURN s.url").result_set]
        assert len(urls) == 2                 # the two-Source fork
        assert f"corpus://{c.name}/moved.md" in urls
        assert f"corpus://{c.name}/archive/moved.md" in urls
        # pure-move: sweep == 0 (content-unchanged → both pairs hash-equal)
        assert _hash_pair_sweep(g) == 0
    finally:
        sdk.close()


def test_e2e8_moved_file_edited_move(tmp_path):
    """E2E-8 MOVED-file leg — EDITED-MOVE parametrization: the fixture's
    frontmatter sessionId PINNED to match the legacy Event (cycle-15 pin),
    content EDITED at the new path → post-forward sweep == 1 (the old
    degraded Source vs the refreshed legacy Event — exclusion class 3)."""
    c = tmp_path / "corpus"; c.mkdir()  # noqa: E702
    sub = c / "archive"; sub.mkdir()  # noqa: E702
    old = c / "moved.md"
    old.write_text(SESSION_FIXTURE.format(sid="mv2", n=1))
    stored = compute_file_hash(str(old))
    new_path = sub / "moved.md"
    os.rename(str(old), str(new_path))
    # EDITED after the move — hash differs from the stored file_hash
    new_path.write_text(SESSION_FIXTURE.format(sid="mv2", n=1)
                        + "edited after the move\n")
    sdk = _sdk()
    try:
        g = sdk._get_proj().g
        _create_legacy_event(g, event_id="session_mv2", kind="AgentSession",
                             rel_path="moved.md", file_hash=stored,
                             title="Moved2")
        r = sdk.backfill_sources(str(c))
        assert r["created"] == 1 and r["degraded_no_file"] == 1
        # EDITED-move: the content hash differs from the stored file_hash →
        # the moved-vs-deleted cross-check finds NO hash match → the note
        # classifies as `missing` (indistinguishable from deletion — the
        # distinguishing `moved` wording fires ONLY on a hash match, plan
        # cycle-15); the forward run still produces the two-Source fork.
        assert r["errors"][0]["cause"] == "missing"
        rf = sdk.index_directory(str(c), extract_metadata=False)  # noqa: F841
        urls = [x[0] for x in g.query("MATCH (s:Source) RETURN s.url").result_set]
        assert len(urls) == 2                 # two-Source fork
        # EDITED-move: the old pair is a raw-sweep MISMATCH (old Source
        # contentHash = stored ≠ refreshed Event file_hash) → sweep == 1
        assert _hash_pair_sweep(g) == 1
        assert _required_sweep(g) == 0
    finally:
        sdk.close()


def test_e2e8_delete_after_fork(tmp_path):
    """E2E-8 DELETE-after-fork sub-leg (cycle-15): after the moved-file
    fork, delete the NEW-path file → re-run backfill → created == 0, no
    THIRD Source resurrected from the stored file_hash at either url, run
    completes, health bucket documented."""
    c = tmp_path / "corpus"; c.mkdir()  # noqa: E702
    sub = c / "archive"; sub.mkdir()  # noqa: E702
    old = c / "moved.md"
    old.write_text(SESSION_FIXTURE.format(sid="mv3", n=1))
    stored = compute_file_hash(str(old))
    new_path = sub / "moved.md"
    os.rename(str(old), str(new_path))
    sdk = _sdk()
    try:
        g = sdk._get_proj().g
        _create_legacy_event(g, event_id="session_mv3", kind="AgentSession",
                             rel_path="moved.md", file_hash=stored,
                             title="Moved3")
        sdk.backfill_sources(str(c))         # degraded Source at old url
        sdk.index_directory(str(c), extract_metadata=False)  # fresh at new url
        assert g.query("MATCH (s:Source) RETURN count(s)").result_set[0][0] == 2
        # DELETE the new-path file → re-run backfill
        new_path.unlink()
        r = sdk.backfill_sources(str(c))
        assert r["created"] == 0             # no third Source resurrected
        assert g.query("MATCH (s:Source) RETURN count(s)").result_set[0][0] == 2
        assert _required_sweep(g) == 0
        # health bucket documented (cycle-15 pin): the legacy Event's file
        # is now missing everywhere → `unindexed`/`stale` per the health
        # surface (the doc bucket names the fork state)
        h = sdk.session_index_health(str(c))
        assert h["file_count"] == 0          # no files left in the corpus
        assert h["matched"] == 0
    finally:
        sdk.close()


# ═══════════════════════════════════════════════════════════════════════
# E2E-8(f) same-inode (mount-alias) convergence legs
# ═══════════════════════════════════════════════════════════════════════

def test_e2e8_same_inode_convergence_seam(tmp_path, monkeypatch):
    """E2E-8(f) mount-alias convergence — ALWAYS-RUN SEAM leg (the plan's
    pinned seam philosophy: the mount-check DECISION LOGIC runs on EVERY
    platform via the injectable provider; only the real `mount --bind`
    fixture is Linux-gated). Two legacy AgentSession Events whose source
    files report the SAME (st_dev, st_ino) with st_nlink == 1 (the
    per-file undetectable mount-alias class) → the sibling-Event same-inode
    scan converges: dry-run reports would_create == 1 + would_link == 2 +
    errors[] note naming the alias pair; the real run creates exactly ONE
    Source (first sorted path) and links BOTH Events onto it; the second
    run reports both Events skipped."""
    import types as _types
    c = tmp_path / "corpus"; c.mkdir()  # noqa: E702
    a = c / "a.md"; b = c / "b.md"  # noqa: E702
    a.write_text(SESSION_FIXTURE.format(sid="alias1", n=1))
    b.write_text(SESSION_FIXTURE.format(sid="alias1", n=1))  # SAME content
    stored = compute_file_hash(str(a))
    sdk = _sdk()
    real_stat = os.stat

    def _fake_stat(path, *args, **kwargs):
        st = real_stat(path, *args, **kwargs)
        if os.path.basename(str(path)) in ("a.md", "b.md"):
            # fabricate the mount-alias signature: same inode, nlink == 1
            return _types.SimpleNamespace(
                st_mode=st.st_mode, st_size=st.st_size, st_mtime=st.st_mtime,
                st_dev=0x9001, st_ino=0xBEEF, st_nlink=1)
        return st

    monkeypatch.setattr(os, "stat", _fake_stat)
    try:
        g = sdk._get_proj().g
        _create_legacy_event(g, event_id="session_alias_a", kind="AgentSession",
                             rel_path="a.md", file_hash=stored, title="Alias A")
        _create_legacy_event(g, event_id="session_alias_b", kind="AgentSession",
                             rel_path="b.md", file_hash=stored, title="Alias B")
        # dry-run: ONE would_create (the winner), TWO would_link, alias note
        rd = sdk.backfill_sources(str(c), dry_run=True)
        assert rd["would_create"] == 1
        assert rd["would_link"] == 2
        alias_notes = [e for e in rd["errors"] if e.get("cause") == "inode-alias"]
        assert len(alias_notes) == 1
        assert "a.md" in alias_notes[0]["error"] and "b.md" in alias_notes[0]["error"]
        assert rd["would_create"] == 1        # the winner counted once
        # real run: ONE Source, BOTH Events linked onto it
        r = sdk.backfill_sources(str(c))
        assert r["created"] == 1 and r["linked"] == 2 and r["degraded_no_file"] == 0
        assert g.query("MATCH (s:Source) RETURN count(s)").result_set[0][0] == 1
        # both Events have a references edge to the ONE Source
        assert g.query("MATCH ()-[r:references]->() RETURN count(r)"
                       ).result_set[0][0] == 2
        url = g.query("MATCH (s:Source) RETURN s.url").result_set[0][0]
        assert url == f"corpus://{c.name}/a.md"   # first sorted path wins
        assert _required_sweep(g) == 0
        # second run: both Events skipped (already Source + edge)
        r2 = sdk.backfill_sources(str(c))
        assert r2["created"] == 0 and r2["linked"] == 0 and r2["skipped"] == 2
    finally:
        sdk.close()


@pytest.mark.skipif(not (os.path.exists("/proc/self/mountinfo")
                         and os.geteuid() == 0),
                    reason="mount --bind requires Linux + root (E2E-8(f) gate)")
def test_e2e8_same_inode_convergence_mount_bind(tmp_path):
    """E2E-8(f) mount-alias convergence — LINUX-ONLY real-leg (skip-if-
    unavailable, same `mount --bind` gate as E2E-7(s)): a dir bind-mounted
    INSIDE the corpus → two legacy Events whose source files are mount-alias
    paths of ONE physical file converge onto a single Source (first sorted
    path), both Events linked, second run both skipped."""
    import subprocess as _sp
    c = tmp_path / "corpus"; c.mkdir()  # noqa: E702
    realdir = tmp_path / "real"; realdir.mkdir()  # noqa: E702
    (realdir / "f.md").write_text(SESSION_FIXTURE.format(sid="mnt1", n=1))
    stored = compute_file_hash(str(realdir / "f.md"))
    alias = c / "alias"
    alias.mkdir()
    _sp.run(["mount", "--bind", str(realdir), str(alias)], check=True)
    try:
        sdk = _sdk()
        try:
            g = sdk._get_proj().g
            _create_legacy_event(g, event_id="session_mnt_real",
                                 kind="AgentSession", rel_path="real/f.md",
                                 file_hash=stored, title="Mnt Real")
            _create_legacy_event(g, event_id="session_mnt_alias",
                                 kind="AgentSession", rel_path="alias/f.md",
                                 file_hash=stored, title="Mnt Alias")
            rd = sdk.backfill_sources(str(c), dry_run=True)
            assert rd["would_create"] == 1 and rd["would_link"] == 2
            r = sdk.backfill_sources(str(c))
            assert r["created"] == 1 and r["linked"] == 2
            assert g.query("MATCH (s:Source) RETURN count(s)").result_set[0][0] == 1
            assert g.query("MATCH ()-[r:references]->() RETURN count(r)"
                           ).result_set[0][0] == 2
            r2 = sdk.backfill_sources(str(c))
            assert r2["created"] == 0 and r2["linked"] == 0 and r2["skipped"] == 2
            assert _required_sweep(g) == 0
        finally:
            sdk.close()
    finally:
        _sp.run(["umount", str(alias)], check=False)
