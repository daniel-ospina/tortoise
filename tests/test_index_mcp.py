"""E2E-17 (#1043) — tortoise_index_files MCP tool dispatch through the REAL layer.

Epic #900 plan §7 E2E-17 (owner T7, plan §8.6): tool-call → sdk_method=
"index_directory" routing → argument mapping (corpus_name) → §3.1 summary
serialized back to the agent, proven through the mcp_server handler's
tool-call entry (the function body the FastMCPAdapter wraps for MCP stdio
JSON-RPC tools/call — NOT sdk.index_directory directly). Legs (a)–(f) plus
the direct-handler quota rescope (d) and the tenant-HTTP refusal (e).

Harness conventions (§7): fresh embedded DB per test via
TortoiseSDK(tmpdir/t.db, namespace="e2e-900"); no network — extract_metadata
=False (and TORTOISE_INDEX_NO_NETWORK forces it at the SDK boundary); graph
assertions via raw Cypher on sdk._get_proj().g; REQUIRED-set invariant sweep
+ cross-node hash-pair sweep after every leg.
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from tortoise.sdk import TortoiseSDK

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


def _db(tmp_path) -> str:
    return os.path.join(str(tmp_path), "t.db")


def _sdk(tmp_path, name: str = "t.db") -> TortoiseSDK:
    return TortoiseSDK(os.path.join(str(tmp_path), name), namespace="e2e-900")


@pytest.fixture
def corpus(tmp_path):
    c = tmp_path / "corpus"
    c.mkdir()
    (c / "s1.md").write_text(SESSION_FIXTURE)
    (c / "meeting-2026-08-05.md").write_text(MEETING_FIXTURE)
    (c / "strategy.md").write_text(DOC_FIXTURE)
    return c


@pytest.fixture(autouse=True)
def _transport_context(monkeypatch):
    """MCP tools require an initialized transport mode (#236 auth gate).

    These tests exercise the mcp_server tool-call entry directly (no HTTP
    middleware), so they run in stdio mode: dev-mode auth (TORTOISE_API_KEY
    unset) and no team context (quota skipped). Restore after each test —
    the same pattern as tests/test_mcp_server.py::_transport_context.
    """
    from tortoise.mcp_auth import (  # noqa: I001
        _current_team_id, _current_team_limits, _transport_mode,
    )
    monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
    _transport_mode.set("stdio")
    _current_team_id.set(None)
    _current_team_limits.set(None)
    yield
    _transport_mode.set(None)
    _current_team_id.set(None)
    _current_team_limits.set(None)


def _dispatch_sdk(monkeypatch, sdk):
    """Route the MCP handler's team-SDK resolution to an isolated embedded DB.

    The handler resolves its SDK per call via _get_team_sdk(); swapping it
    routes the REAL tool-call entry at a fresh graph without touching the
    module-level SDK cache (the swap pattern used by tests/test_mcp_server.py).
    """
    import tortoise.mcp_server as ms
    monkeypatch.setattr(ms, "_get_team_sdk", lambda: sdk)
    return ms


# ── Graph helpers (harness conventions §7) ────────────────────────────

def _count(g, cypher, params=None) -> int:
    return g.query(cypher, params=params or {}).result_set[0][0]


def _required_sweep_clean(g) -> int:
    """REQUIRED-set invariant sweep (plan §7, I9): zero Sources with
    null/empty url/sourceKind/contentHash/ingestedAt."""
    return _count(g, "MATCH (s:Source) WHERE s.url IS NULL OR s.url='' OR "
                     "s.sourceKind IS NULL OR s.contentHash IS NULL OR "
                     "s.contentHash='' OR s.ingestedAt IS NULL RETURN count(s)")


def _hash_pair_sweep(g) -> int:
    """Cross-node hash-pair equality sweep (plan §7): raw count of
    Source→Event pairs whose contentHash/file_hash diverge."""
    return _count(g, "MATCH (s:Source)-[:references]->(e:Event) "
                     "WHERE s.contentHash <> e.file_hash RETURN count(*)")


# ── E2E-17 (a)–(c): summary, graph state, idempotency ──────────────────

class TestE2E17Dispatch:
    def test_leg_a_b_honest_summary_and_graph_state(self, corpus, tmp_path, monkeypatch):
        """(a) tool response carries the honest summary (indexed == file_count,
        by_kind registry labels); (b) graph state matches E2E-1 (Source/Event/
        references edge, REQUIRED sweep clean + hash-pair sweep clean)."""
        sdk = _sdk(tmp_path)
        ms = _dispatch_sdk(monkeypatch, sdk)
        try:
            r = ms.tortoise_index_files(str(corpus), extract_metadata=False)
            # (a) honest summary through the tool boundary
            assert r["file_count"] == 3
            assert r["indexed"] == 3 and r["updated"] == 0
            assert r["failed"] == 0 and r["aborted"] == 0
            assert r["skipped"] == 0
            assert r["errors"] == []
            assert r["corpus_name"] == corpus.name  # default = basename
            assert r["by_kind"] == {
                "agentSession": 1, "meeting_summary": 1, "document": 1,
            }, r["by_kind"]

            # (b) graph state matches E2E-1 (session shape asserted in full)
            g = sdk._get_proj().g
            u = f"corpus://{corpus.name}/s1.md"
            rows = g.query(
                "MATCH (s:Source {url:$u}) RETURN s.sourceKind, s.contentHash, "
                "s.ingestedAt",
                params={"u": u},
            ).result_set
            assert len(rows) == 1
            assert rows[0][0] == "agentSession"
            assert rows[0][1]  # contentHash non-empty
            assert rows[0][2]  # ingestedAt non-empty
            ev = g.query(
                "MATCH (e:Event {eventId:'session_abc123'}) RETURN e.file_hash",
            ).result_set
            assert len(ev) == 1
            assert ev[0][0] == rows[0][1]  # hash-pair equality
            assert _count(g, "MATCH (s:Source {url:$u})-[r:references]->"
                             "(e:Event {eventId:'session_abc123'}) RETURN count(r)",
                           {"u": u}) == 1
            # REQUIRED sweep + hash-pair sweep clean
            assert _required_sweep_clean(g) == 0
            assert _hash_pair_sweep(g) == 0
        finally:
            sdk.close()

    def test_leg_c_second_call_skipped(self, corpus, tmp_path, monkeypatch):
        """(c) second call → skipped (idempotency through the tool boundary)."""
        sdk = _sdk(tmp_path)
        ms = _dispatch_sdk(monkeypatch, sdk)
        try:
            r1 = ms.tortoise_index_files(str(corpus), extract_metadata=False)
            assert r1["indexed"] == 3
            r2 = ms.tortoise_index_files(str(corpus), extract_metadata=False)
            assert r2["skipped"] == 3, r2
            assert r2["indexed"] == 0 and r2["updated"] == 0, r2
            g = sdk._get_proj().g
            assert _hash_pair_sweep(g) == 0
            # version UNCHANGED after skip (hash-diff-gated bump)
            assert _count(g, "MATCH (s:Source) RETURN count(DISTINCT s.version)") == 1
        finally:
            sdk.close()

    def test_leg_d_exhausted_quota_direct_handler(self, corpus, tmp_path, monkeypatch):
        """(d) exhausted-quota path — CYCLE-21 RESCOPE: quota enforcement never
        fires on the REAL dispatch path for this tool (stdio early-return +
        http-excluded), so the leg runs a DIRECT-HANDLER call with fabricated
        _current_team_id/_current_team_limits ContextVars (test_mcp_server.py
        _transport_context pattern) asserting the ERR_QUOTA error dict + zero
        graph writes. The real-layer quota posture is STRUCTURAL-ONLY (the S8
        _QUOTA_GATED membership test)."""
        from tortoise.mcp_auth import _current_team_id, _current_team_limits

        sdk = _sdk(tmp_path)
        ms = _dispatch_sdk(monkeypatch, sdk)
        try:
            tok_id = _current_team_id.set("e2e17-quota-team")
            tok_lim = _current_team_limits.set(
                {"team_id": "e2e17-quota-team", "max_points": 0})
            try:
                r = ms.tortoise_index_files(str(corpus), extract_metadata=False)
            finally:
                _current_team_id.reset(tok_id)
                _current_team_limits.reset(tok_lim)
            assert r.get("code") == ms.ERR_QUOTA, f"expected ERR_QUOTA, got: {r}"
            assert "limit reached" in r.get("error", ""), r
            # zero graph writes — the quota gate fires before any SDK write
            g = sdk._get_proj().g
            assert _count(g, "MATCH (s:Source) RETURN count(s)") == 0
            assert _count(g, "MATCH (e:Event) RETURN count(e)") == 0
            assert _count(g, "MATCH (d:Document) RETURN count(d)") == 0
        finally:
            sdk.close()

    def test_leg_f_corpus_name_multi_corpus(self, tmp_path, monkeypatch):
        """(f) corpus_name multi-corpus leg: two corpus roots with the SAME
        basename into one graph — DEFAULT derivation → collision counted
        (1N Sources, url overlap); distinct corpus_name overrides → 2N
        distinct Sources, by_kind/list_sources correct, zero url overlap."""
        sdk = _sdk(tmp_path)
        ms = _dispatch_sdk(monkeypatch, sdk)
        try:
            root_a = tmp_path / "ra" / "corpus"
            root_b = tmp_path / "rb" / "corpus"
            root_a.mkdir(parents=True)
            root_b.mkdir(parents=True)
            assert root_a.name == root_b.name == "corpus"

            # Same sessionId, different content: the DEFAULT arm exercises the
            # shared-identity in-place update (no event fork) so the collision
            # is attributable to the url, not the session identity.
            (root_a / "s1.md").write_text(SESSION_FIXTURE)
            (root_b / "s1.md").write_text(SESSION_FIXTURE.replace(
                "## Summary\nRefactored the auth middleware; decided to keep JWT rotation.",
                "## Summary\nRefactored the auth middleware; decided to keep JWT rotation.\n"
                "Added: rotate refresh tokens too.",
            ))

            # ── DEFAULT derivation arm — same-basename collision ──
            ra = ms.tortoise_index_files(str(root_a), extract_metadata=False)
            assert ra["indexed"] == 1
            assert ra["corpus_name"] == "corpus"  # default = basename
            rb = ms.tortoise_index_files(str(root_b), extract_metadata=False)
            g = sdk._get_proj().g
            urls = [r[0] for r in g.query("MATCH (s:Source) RETURN s.url").result_set]
            assert urls == [f"corpus://corpus/s1.md"], f"collision arm: {urls}"  # noqa: F541
            # 1N Sources — the same-basename fork collapsed onto one url set
            assert _count(g, "MATCH (s:Source) RETURN count(s)") == 1
            assert _count(g, "MATCH (e:Event) RETURN count(e)") == 1
            # the clobber is DOCUMENTED in the run summary: rb reports updated
            # (content changed under the shared url), never a second Source
            assert rb["indexed"] == 0 and rb["updated"] == 1, rb
            assert _required_sweep_clean(g) == 0
            assert _hash_pair_sweep(g) == 0

            # Distinct session identity for the distinct arm: each corpus then
            # owns its own Event (2N units, hash pairs equal — no fork residue).
            (root_b / "s1.md").write_text(SESSION_FIXTURE.replace(
                "sessionId: abc123", "sessionId: def456",
            ).replace(
                "## Summary\nRefactored the auth middleware; decided to keep JWT rotation.",
                "## Summary\nRefactored the auth middleware; decided to keep JWT rotation.\n"
                "Added: rotate refresh tokens too.",
            ))

            # ── distinct corpus_name override arm — fresh graph ──
            sdk2 = _sdk(tmp_path, "t2.db")
            monkeypatch.setattr(ms, "_get_team_sdk", lambda: sdk2)
            try:
                r1 = ms.tortoise_index_files(str(root_a), corpus_name="alpha",
                                             extract_metadata=False)
                r2 = ms.tortoise_index_files(str(root_b), corpus_name="beta",
                                             extract_metadata=False)
                assert r1["indexed"] == 1 and r2["indexed"] == 1, (r1, r2)
                assert r1["by_kind"] == {"agentSession": 1}, r1
                assert r2["by_kind"] == {"agentSession": 1}, r2
                g2 = sdk2._get_proj().g
                urls = sorted(r[0] for r in g2.query(
                    "MATCH (s:Source) RETURN s.url").result_set)
                assert urls == ["corpus://alpha/s1.md", "corpus://beta/s1.md"], urls
                assert len(urls) == len(set(urls))  # zero url overlap
                assert _count(g2, "MATCH (s:Source) RETURN count(s)") == 2  # 2N
                # by_kind / list_sources correct
                srcs = sdk2.list_sources()
                assert {s["url"] for s in srcs} == set(urls)
                assert all(s["sourceKind"] == "agentSession" for s in srcs)
                assert _required_sweep_clean(g2) == 0
                assert _hash_pair_sweep(g2) == 0
            finally:
                sdk2.close()
        finally:
            sdk.close()


# ── E2E-17 (e): tenant-HTTP refusal (http_policy=False, #329 posture) ──

def _mounted_test_client(app):
    """Wrap the MCP app in a Starlette Mount at /mcp (mirrors hosted_api and
    tests/test_mcp_http.py — the sub-app lifespan is composed into the parent
    because Starlette Mount does NOT auto-run sub-app lifespans)."""
    from starlette.applications import Starlette
    from starlette.routing import Mount
    from starlette.testclient import TestClient

    @asynccontextmanager
    async def _lifespan(parent_app):
        async with app.lifespan(app):
            yield

    parent = Starlette(lifespan=_lifespan, routes=[Mount("/mcp", app=app)])
    return TestClient(parent)


def _parse_sse_json(r):
    text = r.text
    if text.startswith("event:") or "\ndata: " in text:
        for line in text.splitlines():
            if line.startswith("data: "):
                import json
                return json.loads(line[len("data: "):])
        return None
    return r.json()


class TestE2E17HttpRefusal:
    @pytest.fixture
    def http_client(self, tmp_path):
        """Mounted MCP HTTP app with registry auth (embedded — no live server)."""
        from tortoise.mcp_server import create_http_app

        reg = TortoiseSDK(os.path.join(str(tmp_path), "reg.db"),
                          namespace="registry")
        team = reg.team_create("e2e17-http")
        key = reg.apikey_create(team["id"], "e2e17-fixture")["api_key"]
        app = create_http_app(allowed_origins=["https://app.premiselabs.co"],
                              _registry_sdk=reg)
        tc = _mounted_test_client(app)
        tc.headers.update({
            "Authorization": f"Bearer {key}",
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        })
        with tc:
            yield tc
        reg.close()

    def _mcp_post(self, tc, payload):
        r = tc.post("/mcp", json=payload)
        return r, _parse_sse_json(r)

    def test_leg_e_absent_from_http_allowed(self):
        """tortoise_index_files absent from the HTTP_ALLOWED surface
        (http_policy=False, #329 posture)."""
        from tortoise.mcp_auth import HTTP_ALLOWED
        assert "tortoise_index_files" not in HTTP_ALLOWED

    def test_leg_e_http_tools_list_and_call_refused(self, http_client):
        """E2E-17(e) at the wire: tools/list hides the filesystem-walk tool
        and tools/call is refused with the excluded error (-32004)."""
        tc = http_client
        r, body = self._mcp_post(tc, {
            "jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {},
        })
        names = {t.get("name") for t in body.get("result", {}).get("tools", [])}
        assert "tortoise_index_files" not in names, (
            "filesystem-walk tool must not be discoverable over tenant HTTP")

        r, body = self._mcp_post(tc, {  # noqa: RUF059
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "tortoise_index_files",
                       "arguments": {"directory": str(Path("/tmp"))}},
        })
        text = "".join(c.get("text", "") for c in body.get("result", {}).get("content", []))
        assert "-32004" in text or "not available over HTTP" in text, (
            f"expected excluded error, got: {body}")
