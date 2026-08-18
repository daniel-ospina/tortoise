"""Issue #348 — audit tool (SDK audit() + CLI `tortoise audit` + MCP tortoise_audit).

Covers: all 8 audit checks (point-level legacy missing_sourceKind, missing_
sourceDate, superseded_no_edge via CORRECTS, superseded_active_edges, naive-IMPL
word-boundary heuristic, mitigation_recommended via mitigated_by, legacy
mitigates edges, Source-level missing_sourceKind_source), uncapped counts vs
capped samples, pointKind scoping, exit-code semantics, both surfaces (CLI
wraps the SDK audit() method; MCP handler + registry entry), and the negative
case (correctly-wired graphs via the real SDK write paths are NOT flagged).

Run with: .venv/bin/python -m pytest tests/test_audit.py -v
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from tortoise.sdk import TortoiseSDK
from tests._embedded import skip_if_no_falkor


@pytest.fixture
def sdk(tmp_path):
    """Fresh embedded SDK per test (unique DB path)."""
    if skip_if_no_falkor():
        pytest.skip("embedded FalkorDBLite unavailable")
    return TortoiseSDK(db_path=str(tmp_path / "t.db"))


def _q(sdk, query: str, params: dict | None = None) -> list:
    r = sdk._get_proj().g.query(query, params=params or {})
    return r.result_set if r else []


def _create(sdk, label: str, props: str) -> None:
    """Create one node: CREATE (v:Label {props, id:'label'})."""
    _q(sdk, f"CREATE ({label}:Point {{{props}, id:'{label}'}})")


def _edge(sdk, a: str, b: str, rel: str) -> None:
    _q(sdk, f"MATCH (a:Point {{id:'{a}'}}), (b:Point {{id:'{b}'}}) "
            f"CREATE (a)-[:{rel}]->(b)")


def _check(report: dict, cid: str) -> dict | None:
    return next((c for c in report["checks"] if c["id"] == cid), None)


# ── 1. Structured JSON + exit-code semantics ──────────────────────

def test_audit_returns_structured_json_and_exit_codes(sdk):
    """audit() returns {node_count, edge_count, checks, summary, exit_code};
    clean graph → exit_code 0."""
    report = sdk.audit()
    assert set(report) >= {"node_count", "edge_count", "checks", "summary", "exit_code"}
    assert report["summary"]["clean"] is True
    assert report["exit_code"] == 0
    assert report["checks"] == []

    _create(sdk, "p1", "content:'x', pointKind:'statement', is_operator:false, status:'live'")
    _create(sdk, "op1", "is_operator:true, op_type:'IMPL', pointKind:'statement', "
                        "status:'live', confidence:0.2")
    _edge(sdk, "op1", "p1", "IMPL")

    report = sdk.audit()
    assert report["exit_code"] == 1
    assert report["summary"]["clean"] is False
    assert report["summary"]["total_issues"] > 0
    # per-check shape: id/severity/count/samples, count is a total >= samples
    for ch in report["checks"]:
        assert {"id", "severity", "count", "samples"} <= set(ch)
        assert ch["count"] >= len(ch["samples"])


# ── 2. Checks 1–7 ────────────────────────────────────────────────

def test_check1_point_level_sourcekind_legacy(sdk):
    """Evidence via an operator without point-level sourceKind → legacy LOW
    missing_sourceKind (point-level sourceKind is a legacy annotation #398)."""
    _create(sdk, "ev", "content:'untiered ev', pointKind:'statement', "
                       "is_operator:false, status:'live'")
    _create(sdk, "op1", "is_operator:true, op_type:'IMPL', pointKind:'statement', status:'live'")
    _edge(sdk, "op1", "ev", "IMPL")

    report = sdk.audit()
    ch = _check(report, "missing_sourceKind")
    assert ch is not None
    assert ch["count"] == 1
    assert ch["severity"] == "low"
    assert ch["legacy"] is True
    assert ch["samples"][0]["node_id"] == "ev"


def test_check2_missing_sourcedate(sdk):
    """Point with sourceKind but no sourceDate → low missing_sourceDate."""
    _create(sdk, "ev", "content:'dated', pointKind:'statement', is_operator:false, "
                       "status:'live', sourceKind:'T3'")
    report = sdk.audit()
    ch = _check(report, "missing_sourceDate")
    assert ch is not None and ch["count"] == 1
    assert ch["samples"][0]["node_id"] == "ev"


def test_check3_superseded_no_edge_uses_corrects(sdk):
    """Superseded/outdated points must have an INCOMING CORRECTS edge from
    their replacement (supersede_point writes (new)-[:CORRECTS]->(old)). The
    graph-scripts-era SUPERSEDES edge is NOT a current write path and does not
    satisfy the check."""
    _create(sdk, "sup", "content:'superseded orphan', pointKind:'statement', "
                        "is_operator:false, status:'superseded'")
    _create(sdk, "repl", "content:'replacement', pointKind:'statement', "
                         "is_operator:false, status:'live'")
    # legacy-era edge — must NOT satisfy the CORRECTS requirement
    _edge(sdk, "sup", "repl", "SUPERSEDES")

    report = sdk.audit()
    ch = _check(report, "superseded_no_edge")
    assert ch is not None and ch["severity"] == "high"
    assert ch["count"] == 1
    assert ch["samples"][0]["node_id"] == "sup"

    # now write the canonical CORRECTS edge → no longer flagged
    _edge(sdk, "repl", "sup", "CORRECTS")
    report = sdk.audit()
    ch = _check(report, "superseded_no_edge")
    assert ch is None or ch["count"] == 0


def test_check3_legacy_outdated_flag(sdk):
    """outdated=true (invalidate_point keeps the original status) without a
    CORRECTS edge is flagged too."""
    _create(sdk, "old", "content:'invalidated', pointKind:'statement', "
                        "is_operator:false, status:'live', outdated:true")
    report = sdk.audit()
    ch = _check(report, "superseded_no_edge")
    assert ch is not None and ch["count"] == 1


def test_check4_superseded_active_edges(sdk):
    """Live point with IMPL/NAND INTO a superseded point → medium."""
    _create(sdk, "sup", "content:'superseded', pointKind:'statement', "
                        "is_operator:false, status:'superseded'")
    _create(sdk, "act", "content:'active', pointKind:'statement', "
                        "is_operator:false, status:'live'")
    _edge(sdk, "act", "sup", "IMPL")

    report = sdk.audit()
    ch = _check(report, "superseded_active_edges")
    assert ch is not None and ch["severity"] == "medium"
    assert ch["count"] == 1
    assert ch["samples"][0]["node_id"] == "sup"


def test_check5_naive_impl_word_boundary(sdk):
    """IMPL edges to contradiction-laden targets → advisory LOW heuristic;
    word-boundary matching must NOT flag innocent substrings ('tornado')."""
    _create(sdk, "t1", "content:'This cannot work in production', pointKind:'statement', "
                       "is_operator:false, status:'live'")
    _create(sdk, "t2", "content:'tornado warning issued', pointKind:'statement', "
                       "is_operator:false, status:'live'")
    _create(sdk, "op1", "is_operator:true, op_type:'IMPL', pointKind:'statement', status:'live'")
    _create(sdk, "op2", "is_operator:true, op_type:'IMPL', pointKind:'statement', status:'live'")
    _edge(sdk, "op1", "t1", "IMPL")
    _edge(sdk, "op2", "t2", "IMPL")

    report = sdk.audit()
    ch = _check(report, "impl_instead_of_nand")
    assert ch is not None and ch["severity"] == "low"
    assert ch["count"] == 1  # 'tornado' must NOT match any word boundary
    assert ch["samples"][0]["node_id"] == "op1"
    assert "verify semantic contradiction" in ch["samples"][0]["fix"].lower()


def test_check6_mitigation_recommended_uses_mitigated_by(sdk):
    """Low-confidence operators must carry (op)-[:mitigated_by]->(m) — the SDK
    write path. The old `(tgt)<-[mit:mitigates]-()` shape could never see an
    SDK-created mitigation (wrong edge type AND direction)."""
    _create(sdk, "p", "content:'claim', pointKind:'statement', is_operator:false, status:'live'")
    _create(sdk, "low", "is_operator:true, op_type:'IMPL', pointKind:'statement', "
                        "status:'live', confidence:0.2")
    _create(sdk, "mit", "is_operator:true, op_type:'IMPL', pointKind:'statement', "
                        "status:'live', confidence:0.9")
    _create(sdk, "m", "content:'[MITIGATION] x', pointKind:'statement', is_operator:false")
    _edge(sdk, "low", "p", "IMPL")
    _edge(sdk, "mit", "p", "IMPL")
    # canonical mitigation: (op)-[:mitigated_by]->(m) — outbound from operator
    _edge(sdk, "low", "m", "mitigated_by")

    report = sdk.audit()
    ch = _check(report, "mitigation_recommended")
    # 'low' is mitigated → NOT flagged; only 'mit' is above the threshold → 0
    assert ch is None or ch["count"] == 0

    # remove the mitigation → low is flagged, high-confidence 'mit' still not
    _q(sdk, "MATCH (o:Point {id:'low'})-[r:mitigated_by]->() DELETE r")
    report = sdk.audit()
    ch = _check(report, "mitigation_recommended")
    assert ch is not None and ch["count"] == 1
    assert ch["severity"] == "medium"
    assert ch["samples"][0]["node_id"] == "low"
    assert "strength=0.3" in ch["samples"][0]["fix"]


def test_check6b_legacy_mitigates_edge(sdk):
    """graph-scripts-era (op)-[:mitigates]->(m) edges are surfaced as a
    distinct LOW legacy check (migration signal, never coverage)."""
    _create(sdk, "p", "content:'claim', pointKind:'statement', is_operator:false, status:'live'")
    _create(sdk, "op", "is_operator:true, op_type:'IMPL', pointKind:'statement', "
                       "status:'live', confidence:0.2")
    _create(sdk, "m", "content:'[MITIGATION] x', pointKind:'statement', is_operator:false")
    _edge(sdk, "op", "p", "IMPL")
    _edge(sdk, "op", "m", "mitigates")  # legacy edge type

    report = sdk.audit()
    ch = _check(report, "legacy_mitigates_edge")
    assert ch is not None
    assert ch["count"] == 1
    assert ch["severity"] == "low"
    assert ch["legacy"] is True
    assert "mitigated_by" in ch["samples"][0]["fix"]
    # the legacy edge must NOT satisfy mitigation_recommended
    ch6 = _check(report, "mitigation_recommended")
    assert ch6 is not None and ch6["count"] == 1


def test_check7_source_missing_tier(sdk):
    """Sources backing in-scope evidence with NEITHER sourceKind NOR
    credibilityTier → medium missing_sourceKind_source (#1158). A Source with
    credibilityTier only (create_source(url, kind, tier=...)) is valid."""
    _create(sdk, "ev1", "content:'from untiered source', pointKind:'statement', "
                        "is_operator:false, status:'live'")
    _create(sdk, "ev2", "content:'from tiered source', pointKind:'statement', "
                        "is_operator:false, status:'live'")
    _create(sdk, "op1", "is_operator:true, op_type:'IMPL', pointKind:'statement', status:'live'")
    _q(sdk, "CREATE (s1:Source {url:'https://example.com/untiered'})")
    _q(sdk, "CREATE (s2:Source {url:'https://example.com/tiered', "
             "sourceKind:'news', credibilityTier:'T2'})")
    _edge(sdk, "op1", "ev1", "IMPL")
    _edge(sdk, "op1", "ev2", "IMPL")
    _q(sdk, "MATCH (e:Point {id:'ev1'}), (s:Source {url:'https://example.com/untiered'}) "
            "CREATE (e)-[:extractedFrom]->(s)")
    _q(sdk, "MATCH (e:Point {id:'ev2'}), (s:Source {url:'https://example.com/tiered'}) "
            "CREATE (e)-[:extractedFrom]->(s)")

    report = sdk.audit()
    ch = _check(report, "missing_sourceKind_source")
    assert ch is not None and ch["severity"] == "medium"
    assert ch["count"] == 1
    assert ch["samples"][0]["node_id"] == "https://example.com/untiered"


def test_check1_point_backed_by_tiered_source_not_flagged(sdk):
    """#1158: a point backed by a tiered Source is NOT a point-level legacy gap.
    The point-level check must not false-positive on current-ontology graphs
    whose tiering lives on the Source (a Source-level annotation can never move
    the point-level metric)."""
    _create(sdk, "ev", "content:'from tiered source', pointKind:'statement', "
                       "is_operator:false, status:'live'")
    _create(sdk, "op1", "is_operator:true, op_type:'IMPL', pointKind:'statement', status:'live'")
    _edge(sdk, "op1", "ev", "IMPL")
    _q(sdk, "CREATE (s:Source {url:'https://example.com/tiered', "
            "sourceKind:'news', credibilityTier:'T2'})")
    _q(sdk, "MATCH (e:Point {id:'ev'}), (s:Source {url:'https://example.com/tiered'}) "
            "CREATE (e)-[:extractedFrom]->(s)")

    report = sdk.audit()
    ch1 = _check(report, "missing_sourceKind")
    assert ch1 is None or ch1["count"] == 0
    ch7 = _check(report, "missing_sourceKind_source")
    assert ch7 is None or ch7["count"] == 0


def test_check1_legacy_point_with_untiered_source_owned_by_check7(sdk):
    """A point WITH an extractedFrom edge to an UNTIERED Source is not a
    point-level legacy gap (it has provenance) — check 7 flags the Source
    instead. Single-report division: no double-flagging of one root cause."""
    _create(sdk, "ev", "content:'from untiered source', pointKind:'statement', "
                       "is_operator:false, status:'live'")
    _create(sdk, "op1", "is_operator:true, op_type:'IMPL', pointKind:'statement', status:'live'")
    _edge(sdk, "op1", "ev", "IMPL")
    _q(sdk, "CREATE (s:Source {url:'https://example.com/untiered'})")
    _q(sdk, "MATCH (e:Point {id:'ev'}), (s:Source {url:'https://example.com/untiered'}) "
            "CREATE (e)-[:extractedFrom]->(s)")

    report = sdk.audit()
    ch1 = _check(report, "missing_sourceKind")
    assert ch1 is None or ch1["count"] == 0
    ch7 = _check(report, "missing_sourceKind_source")
    assert ch7 is not None and ch7["count"] == 1
    assert ch7["samples"][0]["node_id"] == "https://example.com/untiered"


def test_check7_kind_resolving_neutral_flagged(sdk):
    """#1158: key the Source check on resolve_tier OUTCOME, not the raw field.
    A Source whose sourceKind is an unregistered kind ('news' → registry default
    None → neutral) is effectively untiered and must be flagged."""
    _create(sdk, "ev", "content:'from news source', pointKind:'statement', "
                       "is_operator:false, status:'live'")
    _create(sdk, "op1", "is_operator:true, op_type:'IMPL', pointKind:'statement', status:'live'")
    _edge(sdk, "op1", "ev", "IMPL")
    _q(sdk, "CREATE (s:Source {url:'https://example.com/news', sourceKind:'news'})")
    _q(sdk, "MATCH (e:Point {id:'ev'}), (s:Source {url:'https://example.com/news'}) "
            "CREATE (e)-[:extractedFrom]->(s)")

    report = sdk.audit()
    ch = _check(report, "missing_sourceKind_source")
    assert ch is not None and ch["count"] == 1
    assert ch["samples"][0]["node_id"] == "https://example.com/news"


def test_check7_explicit_tier_beats_neutral_kind(sdk):
    """Explicit credibilityTier wins over a neutral-resolving sourceKind
    (resolve_tier precedence) → not flagged."""
    _create(sdk, "ev", "content:'from explicitly tiered source', pointKind:'statement', "
                       "is_operator:false, status:'live'")
    _create(sdk, "op1", "is_operator:true, op_type:'IMPL', pointKind:'statement', status:'live'")
    _edge(sdk, "op1", "ev", "IMPL")
    _q(sdk, "CREATE (s:Source {url:'https://example.com/news-tiered', "
            "sourceKind:'news', credibilityTier:'T2'})")
    _q(sdk, "MATCH (e:Point {id:'ev'}), (s:Source {url:'https://example.com/news-tiered'}) "
            "CREATE (e)-[:extractedFrom]->(s)")

    report = sdk.audit()
    ch = _check(report, "missing_sourceKind_source")
    assert ch is None or ch["count"] == 0


# ── 3. Uncounted totals vs capped samples ─────────────────────────

def test_counts_uncapped_samples_capped(sdk):
    """60 violations → count=60 (uncapped), samples capped at 50 (#348: the
    old LIMIT-capped issues were samples, not totals)."""
    _create(sdk, "op1", "is_operator:true, op_type:'IMPL', pointKind:'statement', status:'live'")
    _create(sdk, "op2", "is_operator:true, op_type:'IMPL', pointKind:'statement', "
                        "status:'live', confidence:0.2")
    for i in range(60):
        _create(sdk, f"e{i}", f"content:'ev {i}', pointKind:'statement', "
                              "is_operator:false, status:'live'")
        _edge(sdk, "op1", f"e{i}", "IMPL")
        _edge(sdk, "op2", f"e{i}", "IMPL")

    report = sdk.audit()
    ch1 = _check(report, "missing_sourceKind")
    assert ch1 is not None and ch1["count"] == 120  # 60 evs × 2 operators
    assert len(ch1["samples"]) == 50
    ch6 = _check(report, "mitigation_recommended")
    assert ch6 is not None and ch6["count"] == 60
    assert len(ch6["samples"]) == 50
    assert report["summary"]["total_issues"] == 180


# ── 4. Scoping ───────────────────────────────────────────────────

def test_audit_point_kinds_scope(sdk):
    """point_kinds scopes the audit; unknown kind → clean."""
    _create(sdk, "ev", "content:'ev', pointKind:'decision', is_operator:false, status:'live'")
    _create(sdk, "op1", "is_operator:true, op_type:'IMPL', pointKind:'decision', status:'live'")
    _edge(sdk, "op1", "ev", "IMPL")

    scoped = sdk.audit(point_kinds=["statement"])
    assert scoped["summary"]["clean"] is True
    all_ = sdk.audit()
    assert all_["summary"]["clean"] is False
    scoped2 = sdk.audit(point_kinds=["decision"])
    assert scoped2["summary"]["clean"] is False


# ── 5. Real write paths: correctly-wired graph is NOT flagged ─────

def test_real_write_paths_not_flagged(sdk):
    """A graph wired through the SDK (supersede_point → CORRECTS,
    mitigate_operator → mitigated_by, create_source with tier) must not trip
    the canonical checks."""
    old = sdk.create_point("statement", "old claim", status="live")
    new = sdk.create_point("statement", "new claim", status="live")
    other = sdk.create_point("statement", "other claim", status="live")
    sdk.supersede_point(old["id"], new["id"])  # (new)-[:CORRECTS]->(old), status superseded

    op = sdk.create_operator("IMPL", new["id"], [other["id"]])
    _q(sdk, "MATCH (o:Point {id:$id}) SET o.confidence = 0.2",
       params={"id": op["id"]})
    sdk.mitigate_operator(op["id"], "Relevant because...", strength=0.3)

    sdk.create_source("https://example.com/tiered", "news", tier="T2")
    ev = sdk.create_point("statement", "evidence", status="live")
    _q(sdk, "MATCH (e:Point {id:$eid}), (s:Source {url:$url}) "
            "CREATE (e)-[:extractedFrom]->(s)",
       params={"eid": ev["id"], "url": "https://example.com/tiered"})

    report = sdk.audit()
    for cid in ("superseded_no_edge", "mitigation_recommended",
                "missing_sourceKind_source", "superseded_active_edges"):
        ch = _check(report, cid)
        assert ch is None or ch["count"] == 0, cid


# ── 6. CLI surface (wraps the SDK audit()) ───────────────────────

def test_cli_json_mode_and_exit_code(sdk, capsys, tmp_path):
    """tortoise audit --json prints the SDK report and exits 1 on issues."""
    db = str(tmp_path / "t.db")
    _create(sdk, "ev", "content:'x', pointKind:'statement', is_operator:false, status:'live'")
    _create(sdk, "op1", "is_operator:true, op_type:'IMPL', pointKind:'statement', "
                        "status:'live', confidence:0.2")
    _edge(sdk, "op1", "ev", "IMPL")
    sdk.close()

    from tortoise.__main__ import main
    rc = main(["audit", "--db", db, "--json"])
    out = capsys.readouterr().out
    report = json.loads(out)
    assert rc == 1
    assert report["exit_code"] == 1
    assert report["summary"]["total_issues"] > 0
    assert any(c["id"] == "mitigation_recommended" for c in report["checks"])


def test_cli_human_mode_and_clean_exit(sdk, capsys, tmp_path):
    """Human mode prints the summary; a clean graph exits 0."""
    db = str(tmp_path / "t.db")
    sdk.close()

    from tortoise.__main__ import main
    rc = main(["audit", "--db", db])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Tortoise Audit" in out
    assert "No issues found" in out


def test_cli_human_mode_reports_issues(sdk, capsys, tmp_path):
    db = str(tmp_path / "t.db")
    _create(sdk, "sup", "content:'orphan', pointKind:'statement', "
                        "is_operator:false, status:'superseded'")
    sdk.close()

    from tortoise.__main__ import main
    rc = main(["audit", "--db", db])
    out = capsys.readouterr().out
    assert rc == 1
    assert "[superseded_no_edge]" in out
    assert "HIGH" in out


def _clear_db_env(monkeypatch):
    """Reset every DB-target env source so env-based scenarios start clean.

    Mirrors tests/test_cli_context.py's _delenv_falkordb + URI/PATH clears.
    """
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    monkeypatch.delenv("TORTOISE_DB_PATH", raising=False)
    for k in ("FALKORDB_HOST", "FALKORDB_PORT", "FALKORDB_PASSWORD"):
        monkeypatch.delenv(k, raising=False)


@pytest.mark.parametrize("case", [
    "invalid_port",
    "relative_path",
    "unreachable_uri",
    "embedded_busy",
])
def test_cli_error_paths_clean_one_line(case, capsys, monkeypatch, tmp_path):
    """#1258 review-fix (9daec32e): every _cmd_audit failure path surfaces as
    a SINGLE clean stderr line with exit 1 — never a raw traceback, and never
    a credential leak from a URI-bearing target.

    Covers the four paths the fix commit added error handling for:
      (a) invalid FALKORDB_PORT       → ValueError in _resolve_db_target
      (b) relative --db path          → ValueError in _resolve_db_target
      (c) unreachable URI host        → RuntimeError/ConnectionError in the
                                        SDK/audit branch (password must not
                                        appear in output)
      (d) embedded store held busy    → EmbeddedStoreBusyError from the SDK's
                                        §5.3 pid-registry probe
    """
    from tortoise.__main__ import main

    secret = None
    if case == "invalid_port":
        _clear_db_env(monkeypatch)
        monkeypatch.setenv("FALKORDB_HOST", "localhost")
        monkeypatch.setenv("FALKORDB_PORT", "not-an-int")
        argv = ["audit"]
        expect = "Invalid FALKORDB_PORT"
    elif case == "relative_path":
        _clear_db_env(monkeypatch)
        argv = ["audit", "--db", "relative/path.db"]
        expect = "Relative DB path"
    elif case == "unreachable_uri":
        _clear_db_env(monkeypatch)
        # port 1 on loopback → instant ECONNREFUSED (no network RTT, no hang).
        # The password-bearing userinfo must never reach stdout/stderr.
        secret = "sup3rsekrit"
        monkeypatch.setenv(
            "TORTOISE_DB_URI",
            f"docker://:{secret}@127.0.0.1:1/tortoise")
        argv = ["audit"]
        expect = "audit failed"
    else:  # embedded_busy
        _clear_db_env(monkeypatch)
        db_path = str(tmp_path / "busy.db")
        # Simulate a LIVE holder of the embedded store: write the redislite
        # pid-registry (<path>.settings → pidfile) pointing at a live pid.
        # Our own pid works — the probe only passes paths this process has
        # ALREADY opened (_embedded_busy_known), and busy.db is fresh.
        (tmp_path / "busy.db.settings").write_text(
            json.dumps({"pidfile": str(tmp_path / "busy.pid")}),
            encoding="utf-8")
        (tmp_path / "busy.pid").write_text(str(os.getpid()), encoding="utf-8")
        argv = ["audit", "--db", db_path]
        expect = "Embedded store busy"

    rc = main(argv)
    captured = capsys.readouterr()
    assert rc == 1
    assert expect in captured.err
    assert len(captured.err.strip().splitlines()) == 1, \
        f"expected a SINGLE stderr line, got:\n{captured.err!r}"
    assert "Traceback" not in captured.out
    assert "Traceback" not in captured.err
    if secret is not None:
        assert secret not in captured.out
        assert secret not in captured.err


# ── 7. MCP surface (registry + handler) ──────────────────────────

def test_mcp_registry_entry_and_http_allowlist():
    """tortoise_audit is a read-only registry entry wired to the SDK audit()
    method; the HTTP allow-list is derived (both surfaces pick it up)."""
    from tortoise.tool_registry import TOOL_REGISTRY, get_http_allowed

    entry = next((t for t in TOOL_REGISTRY if t.name == "tortoise_audit"), None)
    assert entry is not None
    assert entry.sdk_method == "audit"
    assert entry.http_policy is True
    assert entry.annotations.readOnlyHint is True
    assert "tortoise_audit" in get_http_allowed()


def test_mcp_handler_registered_on_server():
    """mcp_server exposes a tortoise_audit handler that the registry adapter
    registers (handler name present in module globals, like check_structure)."""
    import tortoise.mcp_server as ms
    assert callable(getattr(ms, "tortoise_audit", None))


def test_mcp_handler_returns_same_report(sdk, monkeypatch):
    """The MCP handler wraps the same SDK audit() — same JSON, same exit code."""
    import tortoise.mcp_server as ms
    from tortoise.mcp_auth import _transport_mode

    _create(sdk, "ev", "content:'x', pointKind:'statement', is_operator:false, status:'live'")
    _create(sdk, "op1", "is_operator:true, op_type:'IMPL', pointKind:'statement', "
                        "status:'live', confidence:0.2")
    _edge(sdk, "op1", "ev", "IMPL")

    token = _transport_mode.set("http")
    monkeypatch.setattr(ms, "_get_team_sdk", lambda: sdk)
    try:
        report = ms.tortoise_audit()
        assert isinstance(report, dict)
        assert report["exit_code"] == 1
        assert any(c["id"] == "mitigation_recommended" for c in report["checks"])
        # matches the SDK surface exactly
        assert report == sdk.audit()
        # scoped call surfaces the point_kinds param
        scoped = ms.tortoise_audit(point_kinds=["nonexistent"])
        assert scoped["summary"]["clean"] is True
    finally:
        _transport_mode.reset(token)
