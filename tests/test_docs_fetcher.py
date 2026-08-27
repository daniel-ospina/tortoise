"""#1726 Slice 1 — GitHub docs fetcher + staging tests (Task 8).

Unit tests for ``tortoise/indexer/github_docs.py`` — Contents-API walk via a
mock transport, team-partitioned staging, text/size input guards (T1-P16),
atomic staging with cleanup on partial failure, tree-by-sha + blob-sha
dedup, reconciliation, and the falsification (f) pipeline check (walk+stage
→ deterministic corpus ingest → 0 new nodes on unchanged re-ingest).
"""
from __future__ import annotations

import json
import os

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")

import httpx
import pytest

from tests._embedded import _wipe_or
from tests._github_docs_mock import MockGitHubDocsTransport, gh_docs_entry
from tortoise.indexer.github_docs import (
    MAX_DOCS_BLOB_BYTES,
    GitHubDocsIndexer,
)
from tortoise.indexer.github_indexer import GitHubFetchError

TEAM_A = "team-a"
TEAM_B = "team-b"


@pytest.fixture(autouse=True)
def _no_embeddings(monkeypatch):
    """Deterministic, fast embeddings (optional everywhere — None is fine)."""
    monkeypatch.setattr("tortoise.embeddings.compute_embedding",
                        lambda *a, **k: None)


@pytest.fixture(scope="session")
def sdk(tmp_path_factory):
    """ONE session-scoped real SDK (the plan's real-SDK + _wipe_or pattern)."""
    from tortoise.sdk import TortoiseSDK
    store = TortoiseSDK(str(tmp_path_factory.mktemp("docs") / "docs.db"))
    yield store
    store.close()


@pytest.fixture(autouse=True)
def _clean_graph(sdk):
    """Per-test hermeticity: wipe the shared graph before each test."""
    _wipe_or(sdk._get_proj())


def _indexer(transport: MockGitHubDocsTransport, monkeypatch) -> GitHubDocsIndexer:
    """Indexer whose fetch layer is routed through the mock transport —
    monkeypatched on the class so a recreated client (after _close) keeps
    the mock (mirrors the lifecycle tests' _fake_get_client pattern)."""
    idx = GitHubDocsIndexer("fake-token")

    async def _fake_get_client(self):
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(transport=transport)
        return self._client

    monkeypatch.setattr(GitHubDocsIndexer, "_get_client", _fake_get_client)
    return idx


def _docs_tree(*, sha: str, entries: list[dict]) -> dict:
    """{repo: {branch: {sha, entries}}}."""
    return {"acme/repo1": {"main": {"sha": sha, "entries": entries}}}


def _mk_files(*paths: str) -> list[dict]:
    """docs/ tree entries + registered blobs for the given rel paths."""
    entries = [gh_docs_entry(p) for p in paths]
    blobs = {}
    for e in entries:
        blobs[e["sha"]] = (f"# {e['path']}\ncontent\n").encode()
    return entries, blobs


# ── walk + team-partitioned staging ──────────────────────────────

def test_walk_stages_docs_under_team_dir(tmp_path, monkeypatch):
    """docs/ blobs are fetched and staged under
    {TORTOISE_INGEST_BASE_DIR}/{team_id}/{repo}/docs/... with the original
    relative layout preserved."""
    base = str(tmp_path / "ingest")
    monkeypatch.setenv("TORTOISE_INGEST_BASE_DIR", base)
    entries, blobs = _mk_files("docs/README.md", "docs/guides/setup.md")
    t = MockGitHubDocsTransport(
        trees=_docs_tree(sha="tree-v1", entries=entries), blobs=blobs)
    idx = _indexer(t, monkeypatch)
    stats = _run(idx.walk_repo(TEAM_A, "acme/repo1"))

    assert stats["blobs_fetched"] == 2
    assert stats["files_staged"] == 2
    corpus = os.path.join(base, TEAM_A, "acme/repo1")
    assert stats["staged_corpus"] == corpus
    readme = os.path.join(corpus, "docs", "README.md")
    setup = os.path.join(corpus, "docs", "guides", "setup.md")
    assert os.path.isfile(readme)
    assert os.path.isfile(setup)
    with open(readme, encoding="utf-8") as f:
        assert f.read() == "# docs/README.md\ncontent\n"
    # manifest records tree sha + per-path blob shas
    with open(os.path.join(
            base, TEAM_A, ".manifest", "acme", "repo1.json"),
            encoding="utf-8") as mf:
        manifest = json.load(mf)
    assert manifest["tree_sha"] == "tree-v1"
    assert manifest["branch"] == "main"
    assert manifest["blobs"]["docs/README.md"] == entries[0]["sha"]


def test_two_team_staging_isolation(tmp_path, monkeypatch):
    """Team A blobs are never picked up by team B — staging is partitioned
    under {base}/{team_id}/ (T2-P2b)."""
    base = str(tmp_path / "ingest")
    monkeypatch.setenv("TORTOISE_INGEST_BASE_DIR", base)
    entries, blobs = _mk_files("docs/README.md")
    t = MockGitHubDocsTransport(
        trees=_docs_tree(sha="tree-v1", entries=entries), blobs=blobs)
    idx = _indexer(t, monkeypatch)
    _run(idx.walk_repo(TEAM_A, "acme/repo1"))
    _run(idx.walk_repo(TEAM_B, "acme/repo1"))

    team_a_corpus = os.path.join(base, TEAM_A, "acme/repo1")
    team_b_corpus = os.path.join(base, TEAM_B, "acme/repo1")
    assert os.path.isfile(os.path.join(team_a_corpus, "docs", "README.md"))
    assert os.path.isfile(os.path.join(team_b_corpus, "docs", "README.md"))
    # no cross-contamination: neither team's partition bleeds into the other
    assert not os.path.exists(os.path.join(base, TEAM_A, TEAM_B))
    assert not os.path.exists(os.path.join(base, TEAM_B, TEAM_A))
    # team B's corpus holds exactly its own staged file — never team A's
    b_readme = os.path.join(team_b_corpus, "docs", "README.md")
    with open(b_readme, encoding="utf-8") as f:
        b_content = f.read()
    a_readme = os.path.join(team_a_corpus, "docs", "README.md")
    with open(a_readme, encoding="utf-8") as f:
        a_content = f.read()
    assert b_content == a_content == "# docs/README.md\ncontent\n"


# ── input guards (T1-P16) ────────────────────────────────────────

def test_skip_binary_and_oversized(tmp_path, monkeypatch):
    """Binary/non-UTF8 blobs and oversized blobs are skipped with an honest
    skipped count — never staged (text-type guard + max-blob-size constant)."""
    base = str(tmp_path / "ingest")
    monkeypatch.setenv("TORTOISE_INGEST_BASE_DIR", base)
    md = gh_docs_entry("docs/ok.md")
    binary = gh_docs_entry("docs/logo.png",
                           data=b"\x89PNG\r\n\x1a\n\x00\x00\x00 binary")
    oversized = gh_docs_entry("docs/huge.md",
                              size=MAX_DOCS_BLOB_BYTES + 1)
    t = MockGitHubDocsTransport(
        trees=_docs_tree(sha="tree-v1",
                         entries=[md, binary, oversized]),
        blobs={md["sha"]: b"# docs/ok.md\n",
               binary["sha"]: b"\x89PNG\r\n\x1a\n\x00\x00\x00 binary"})
    idx = _indexer(t, monkeypatch)
    stats = _run(idx.walk_repo(TEAM_A, "acme/repo1"))

    assert stats["skipped_binary"] == 1
    assert stats["skipped_oversized"] == 1
    assert stats["blobs_fetched"] == 1  # only the text md was fetched
    assert stats["files_staged"] == 1
    corpus = os.path.join(base, TEAM_A, "acme/repo1")
    assert os.path.isfile(os.path.join(corpus, "docs", "ok.md"))
    assert not os.path.exists(os.path.join(corpus, "docs", "logo.png"))
    assert not os.path.exists(os.path.join(corpus, "docs", "huge.md"))


def test_staging_cleanup_partial_failure(tmp_path, monkeypatch):
    """A mid-walk fetch failure cleans up THIS run's staged files and leaves
    the manifest untouched — no half-fetched content for the next corpus
    pass (atomic-or-reconciled staging)."""
    base = str(tmp_path / "ingest")
    monkeypatch.setenv("TORTOISE_INGEST_BASE_DIR", base)
    a = gh_docs_entry("docs/a.md")
    b = gh_docs_entry("docs/b.md")
    t = MockGitHubDocsTransport(
        trees=_docs_tree(sha="tree-v1", entries=[a, b]),
        blobs={a["sha"]: b"# docs/a.md\n", b["sha"]: b"# docs/b.md\n"},
        fail_blobs={b["sha"]}, fail_blobs_status=401)  # terminal — no backoff
    idx = _indexer(t, monkeypatch)
    with pytest.raises(GitHubFetchError):
        _run(idx.walk_repo(TEAM_A, "acme/repo1"))

    # a.md (staged before the failure) must be removed — no stale files
    corpus = os.path.join(base, TEAM_A, "acme/repo1")
    assert not os.path.exists(os.path.join(corpus, "docs", "a.md"))
    assert not os.path.exists(os.path.join(corpus, "docs", "b.md"))
    # manifest not updated on partial failure
    manifest_path = os.path.join(base, TEAM_A, ".manifest", "acme",
                                 "repo1.json")
    assert not os.path.exists(manifest_path)


def test_staging_cleanup_after_prior_success_keeps_old_files(
        tmp_path, monkeypatch):
    """Deferred-commit invariant (Fix 1): after a SUCCESSFUL walk, a later
    walk that fails mid-fetch must leave the OLD manifest + previously-staged
    files untouched — the new (never-fetched) content never lands, the
    failing run's staged temp files are discarded, and the manifest never
    advances past a failed run."""
    base = str(tmp_path / "ingest")
    monkeypatch.setenv("TORTOISE_INGEST_BASE_DIR", base)
    a1 = gh_docs_entry("docs/a.md")
    t1 = MockGitHubDocsTransport(
        trees=_docs_tree(sha="tree-v1", entries=[a1]),
        blobs={a1["sha"]: b"# docs/a.md v1\n"})
    idx = _indexer(t1, monkeypatch)
    first = _run(idx.walk_repo(TEAM_A, "acme/repo1"))
    assert first["tree_changed"] is True
    corpus = os.path.join(base, TEAM_A, "acme/repo1")
    a_file = os.path.join(corpus, "docs", "a.md")
    assert os.path.isfile(a_file)

    # tree v2: docs/c.md NEW (would stage fine) + docs/z.md CHANGED whose
    # blob fetch fails terminally (401). Path order ⇒ c stages first, then
    # z fails — exercising temp cleanup AND old-file preservation.
    c = gh_docs_entry("docs/c.md", text="# docs/c.md new\n")
    z = gh_docs_entry("docs/z.md", text="# docs/z.md v2\n")
    t2 = MockGitHubDocsTransport(
        trees=_docs_tree(sha="tree-v2", entries=[c, z]),
        blobs={a1["sha"]: b"# docs/a.md v1\n",
               c["sha"]: b"# docs/c.md new\n",
               z["sha"]: b"# docs/z.md v2\n"},
        fail_blobs={z["sha"]}, fail_blobs_status=401)
    idx2 = _indexer(t2, monkeypatch)
    with pytest.raises(GitHubFetchError):
        _run(idx2.walk_repo(TEAM_A, "acme/repo1"))

    # ── deferred-commit invariants ──
    manifest_path = os.path.join(base, TEAM_A, ".manifest", "acme",
                                 "repo1.json")
    with open(manifest_path, encoding="utf-8") as mf:
        manifest = json.load(mf)
    assert manifest["tree_sha"] == "tree-v1", \
        "manifest must NOT advance past a failed run"
    assert manifest["branch"] == "main"
    with open(a_file, encoding="utf-8") as f:
        assert f.read() == "# docs/a.md v1\n", \
            "previously-staged content must survive a failed re-walk"
    # the failing run's newly-staged file (c) is discarded + the failed
    # blob (z) never lands
    assert not os.path.exists(os.path.join(corpus, "docs", "c.md"))
    assert not os.path.exists(os.path.join(corpus, "docs", "z.md"))
    # stats2["tree_changed"]: the second walk could NOT have short-circuited
    # — it reached the blob-fetch stage (a short-circuit returns
    # tree_changed=False after the tree fetch with ZERO blob requests).
    assert any("git/blobs/" in str(r.url) for r in t2.requests), \
        "a changed tree must not short-circuit"


def test_tree_truncated_surfaced(tmp_path, monkeypatch):
    """A truncated recursive tree (GitHub 100k-entry cap) must NOT
    short-circuit the walk — a truncated tree is a PARTIAL view, so the
    tree-by-sha shortcut is disabled (Fix 5) and the flag is surfaced in
    stats."""
    base = str(tmp_path / "ingest")
    monkeypatch.setenv("TORTOISE_INGEST_BASE_DIR", base)
    entries, blobs = _mk_files("docs/README.md")
    t = MockGitHubDocsTransport(
        trees=_docs_tree(sha="tree-v1", entries=entries), blobs=blobs,
        tree_truncated=True)
    idx = _indexer(t, monkeypatch)
    first = _run(idx.walk_repo(TEAM_A, "acme/repo1"))
    assert first["tree_truncated"] is True
    assert first["tree_changed"] is True

    # same tree sha on a second walk: WITHOUT the truncated guard this would
    # short-circuit (tree_changed=False) — it must re-walk instead.
    idx2 = _indexer(t, monkeypatch)
    stats2 = _run(idx2.walk_repo(TEAM_A, "acme/repo1"))
    assert stats2["tree_truncated"] is True
    assert stats2["tree_changed"] is True, \
        "a truncated tree must never short-circuit (partial view)"
    assert stats2["blobs_unchanged"] == 1  # unchanged blobs still dedup


def test_truncated_tree_preserves_hidden_files(tmp_path, monkeypatch):
    """A truncated tree is a PARTIAL view: a previously-staged path absent
    from the truncated listing is NOT reconcile-deleted (it may sit beyond
    the 100k-entry truncation point) — it is carried forward in the manifest
    (Fix 5 × 2b interaction)."""
    base = str(tmp_path / "ingest")
    monkeypatch.setenv("TORTOISE_INGEST_BASE_DIR", base)
    a1 = gh_docs_entry("docs/a.md")
    t1 = MockGitHubDocsTransport(
        trees=_docs_tree(sha="tree-v1", entries=[a1]),
        blobs={a1["sha"]: b"# docs/a.md v1\n"})
    idx = _indexer(t1, monkeypatch)
    _run(idx.walk_repo(TEAM_A, "acme/repo1"))
    corpus = os.path.join(base, TEAM_A, "acme/repo1")
    a_file = os.path.join(corpus, "docs", "a.md")
    assert os.path.isfile(a_file)

    # tree-v2 truncated: a.md NOT in the (partial) listing; b.md is new.
    b = gh_docs_entry("docs/b.md")
    t2 = MockGitHubDocsTransport(
        trees=_docs_tree(sha="tree-v2", entries=[b]),
        blobs={b["sha"]: b"# docs/b.md\n"},
        tree_truncated=True)
    idx2 = _indexer(t2, monkeypatch)
    stats = _run(idx2.walk_repo(TEAM_A, "acme/repo1"))
    assert stats["tree_truncated"] is True
    # a.md is carried forward — NOT reconcile-deleted
    assert os.path.isfile(a_file)
    assert stats["files_reconciled_removed"] == 0
    manifest_path = os.path.join(base, TEAM_A, ".manifest", "acme",
                                 "repo1.json")
    with open(manifest_path, encoding="utf-8") as mf:
        manifest = json.load(mf)
    assert manifest["blobs"].get("docs/a.md") == a1["sha"], \
        "a truncated view must not drop hidden paths from the manifest"
    assert manifest["blobs"].get("docs/b.md") == b["sha"]


# ── dedup (tree-by-sha + per-path blob sha) ──────────────────────

def test_unchanged_tree_short_circuits_zero_fetch(tmp_path, monkeypatch):
    """An unchanged tree (same tree sha) short-circuits the whole walk — 0
    blobs fetched, 0 files staged (incremental tree-by-sha)."""
    base = str(tmp_path / "ingest")
    monkeypatch.setenv("TORTOISE_INGEST_BASE_DIR", base)
    entries, blobs = _mk_files("docs/README.md")
    t = MockGitHubDocsTransport(
        trees=_docs_tree(sha="tree-v1", entries=entries), blobs=blobs)
    idx = _indexer(t, monkeypatch)
    first = _run(idx.walk_repo(TEAM_A, "acme/repo1"))
    assert first["blobs_fetched"] == 1
    second = _run(idx.walk_repo(TEAM_A, "acme/repo1"))
    assert second["tree_changed"] is False
    assert second["blobs_fetched"] == 0
    assert second["files_staged"] == 0
    assert second["docs_entries"] == 1  # still walks + counts the tree


def test_incremental_blob_dedup_fetches_only_changed(tmp_path, monkeypatch):
    """Within a changed tree, only blobs whose sha differs from the manifest
    are fetched (per-path blob-sha dedup)."""
    base = str(tmp_path / "ingest")
    monkeypatch.setenv("TORTOISE_INGEST_BASE_DIR", base)
    a1 = gh_docs_entry("docs/a.md")
    b1 = gh_docs_entry("docs/b.md")
    blobs1 = {a1["sha"]: b"# docs/a.md v1\n", b1["sha"]: b"# docs/b.md v1\n"}
    t1 = MockGitHubDocsTransport(
        trees=_docs_tree(sha="tree-v1", entries=[a1, b1]), blobs=blobs1)
    idx = _indexer(t1, monkeypatch)
    _run(idx.walk_repo(TEAM_A, "acme/repo1"))

    # tree v2: a unchanged, b edited (new blob sha)
    b2 = gh_docs_entry("docs/b.md", text="# docs/b.md v2\n")
    blobs2 = dict(blobs1)
    blobs2[b2["sha"]] = b"# docs/b.md v2\n"
    t2 = MockGitHubDocsTransport(
        trees=_docs_tree(sha="tree-v2", entries=[a1, b2]), blobs=blobs2)
    idx2 = _indexer(t2, monkeypatch)
    stats = _run(idx2.walk_repo(TEAM_A, "acme/repo1"))

    assert stats["tree_changed"] is True
    assert stats["blobs_fetched"] == 1
    assert stats["blobs_unchanged"] == 1
    assert stats["files_staged"] == 1
    with open(os.path.join(base, TEAM_A, "acme/repo1", "docs", "b.md"),
              encoding="utf-8") as f:
        assert f.read() == "# docs/b.md v2\n"


def test_reconcile_removes_deleted_docs(tmp_path, monkeypatch):
    """A staged file that disappears from the tree is removed on the next
    walk (reconciled staging — no stale files for the next corpus pass)."""
    base = str(tmp_path / "ingest")
    monkeypatch.setenv("TORTOISE_INGEST_BASE_DIR", base)
    a = gh_docs_entry("docs/a.md")
    b = gh_docs_entry("docs/b.md")
    t1 = MockGitHubDocsTransport(
        trees=_docs_tree(sha="tree-v1", entries=[a, b]),
        blobs={a["sha"]: b"# docs/a.md\n", b["sha"]: b"# docs/b.md\n"})
    idx = _indexer(t1, monkeypatch)
    _run(idx.walk_repo(TEAM_A, "acme/repo1"))
    corpus = os.path.join(base, TEAM_A, "acme/repo1")
    assert os.path.isfile(os.path.join(corpus, "docs", "b.md"))

    # tree v2: b deleted
    t2 = MockGitHubDocsTransport(
        trees=_docs_tree(sha="tree-v2", entries=[a]),
        blobs={a["sha"]: b"# docs/a.md\n"})
    idx2 = _indexer(t2, monkeypatch)
    stats = _run(idx2.walk_repo(TEAM_A, "acme/repo1"))
    assert stats["files_reconciled_removed"] == 1
    assert not os.path.exists(os.path.join(corpus, "docs", "b.md"))
    assert os.path.isfile(os.path.join(corpus, "docs", "a.md"))


# ── master-branch fallback ───────────────────────────────────────

def test_branch_fallback_to_master(tmp_path, monkeypatch):
    """A 404 on the requested branch falls back to master (honest flag)."""
    base = str(tmp_path / "ingest")
    monkeypatch.setenv("TORTOISE_INGEST_BASE_DIR", base)
    entries, blobs = _mk_files("docs/README.md")
    t = MockGitHubDocsTransport(
        trees={"acme/repo1": {"master": {"sha": "tree-m", "entries": entries}}},
        blobs=blobs,
        tree_404_branches={("acme/repo1", "main")})
    idx = _indexer(t, monkeypatch)
    stats = _run(idx.walk_repo(TEAM_A, "acme/repo1"))
    assert stats["branch_fell_back"] is True
    assert stats["branch"] == "master"
    assert stats["files_staged"] == 1


# ── unset-base fail-closed (fetcher-level defense in depth) ──────

def test_unset_base_fails_closed(tmp_path, monkeypatch):
    """The fetcher itself refuses to walk when TORTOISE_INGEST_BASE_DIR is
    unset — no staging writes happen (the hosted job is the primary gate;
    this is the same check at the fetch layer)."""
    monkeypatch.delenv("TORTOISE_INGEST_BASE_DIR", raising=False)
    entries, blobs = _mk_files("docs/README.md")
    t = MockGitHubDocsTransport(
        trees=_docs_tree(sha="tree-v1", entries=entries), blobs=blobs)
    idx = _indexer(t, monkeypatch)
    with pytest.raises(GitHubFetchError):
        _run(idx.walk_repo(TEAM_A, "acme/repo1"))


# ── falsification (f): walk+stage → corpus ingest → 0 new nodes ──

def test_ingest_reingest_zero_new_nodes(sdk, tmp_path, monkeypatch):
    """End-to-end falsification (f): walk+stage, then the deterministic
    corpus pipeline (index_directory — compute_file_hash dedup,
    derive_document_id, classification) ingests the staged docs; an
    unchanged re-ingest produces 0 NEW nodes. The ingest corpus root is the
    TEAM partition (as the hosted job does) so rel-paths embed
    {owner}/{repo} and doc ids are repo-unique."""
    base = str(tmp_path / "ingest")
    monkeypatch.setenv("TORTOISE_INGEST_BASE_DIR", base)
    entries, blobs = _mk_files("docs/README.md", "docs/guides/setup.md")
    t = MockGitHubDocsTransport(
        trees=_docs_tree(sha="tree-v1", entries=entries), blobs=blobs)
    idx = _indexer(t, monkeypatch)
    stats = _run(idx.walk_repo(TEAM_A, "acme/repo1"))
    assert stats["files_staged"] == 2

    def _docs_count():
        rows = sdk._get_proj().g.query(
            "MATCH (d:Document) RETURN count(d)").result_set
        return int(rows[0][0])

    team_root = GitHubDocsIndexer.team_root(TEAM_A)
    assert str(team_root) == os.path.join(base, TEAM_A)
    first = sdk.index_directory(
        str(team_root), file_type="doc", extract_metadata=False,
        corpus_name="acme-docs")
    assert first["indexed"] == 2
    assert _docs_count() == 2
    # doc ids are repo-unique: {owner}/{repo} is embedded in the rel path
    rows = sdk._get_proj().g.query(
        "MATCH (d:Document) RETURN d.id ORDER BY d.id").result_set
    assert rows == [["doc_acme/repo1/docs/README.md"],
                    ["doc_acme/repo1/docs/guides/setup.md"]]

    # unchanged re-walk + re-ingest ⇒ 0 new nodes (falsification (f))
    t2 = MockGitHubDocsTransport(
        trees=_docs_tree(sha="tree-v1", entries=entries), blobs=blobs)
    idx2 = _indexer(t2, monkeypatch)
    stats2 = _run(idx2.walk_repo(TEAM_A, "acme/repo1"))
    assert stats2["blobs_fetched"] == 0
    second = sdk.index_directory(
        str(team_root), file_type="doc", extract_metadata=False,
        corpus_name="acme-docs")
    assert second["indexed"] == 0
    assert second["skipped"] == 2
    assert _docs_count() == 2, "unchanged re-ingest must add 0 new Document nodes"


def _run(coro):
    import asyncio
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()
