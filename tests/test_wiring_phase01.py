"""#334 Phase 0/1 — wiring remediation baseline tooling tests.

Smoke tests for the no-write baseline tooling shipped in the Phase 0/1 PR:

  - graph-scripts/connectivity_gate.py — reachability classification +
    read-only graph stats; non-zero exit on unreachable endpoints.
  - graph-scripts/baseline_scan.py — read-only JSON report of the wiring
    anomalies the epic targets (stub Points, dead stub edges, degenerate
    operators, NULL-status Points, missing sourceKind, neutral-tier Sources,
    missing extractedFrom backing, Subject stubs) + cap-skip UNVERIFIABLE
    note. Asserted READ-ONLY: node/edge counts unchanged by the scan.
  - graph-scripts/rdb_snapshot_restore.py — docker-aware RDB snapshot/restore
    guardrails (no docker required for this unit surface): URI parsing,
    target classification, restore refusal without --yes, embedded-target
    refusal (no docker RDB for FalkorDBLite).

Anomalies are seeded via direct projection queries (the SDK write paths
validate inputs and cannot produce stubs/degenerate operators), mirroring
the legacy cross-file wiring scripts that created them (#329/#6713).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "graph-scripts"))

import pytest  # noqa: E402, I001

import baseline_scan  # noqa: E402
import connectivity_gate  # noqa: E402
import rdb_snapshot_restore  # noqa: E402

from tortoise.sdk import TortoiseSDK  # noqa: E402


@pytest.fixture
def sdk():
    """Fresh embedded FalkorDBLite graph (per-test path isolation)."""
    db_path = f"{tempfile.mkdtemp(prefix='tt_334_')}/wiring.db"
    s = TortoiseSDK(db_path)
    s.test_guard = lambda: None  # destructive teardown safety for tests
    yield s
    s.close()


def _proj(sdk):
    return sdk._get_proj()


def _seed_clean_graph(sdk) -> tuple[str, str]:
    """Small healthy graph: two points, one operator, one tiered Source."""
    a = sdk.create_point("statement", "alice deployed the service")["id"]
    b = sdk.create_point("statement", "deploy passed all checks")["id"]
    sdk.create_operator("IMPL", a, [b])
    sdk.create_source(f"https://example.com/src-{uuid.uuid4().hex[:8]}",
                      "github_issue", tier="T1")
    return a, b


def _seed_anomalies(sdk, a: str, b: str) -> dict:
    """Seed every anomaly class the baseline scan targets.

    Returns the seeded IDs for sample assertions.
    """
    proj = _proj(sdk)
    g = proj.g
    hex8 = uuid.uuid4().hex[:8]

    # ── Canonical stub Point (_create_edges signature: short ID, '[missing]')
    stub_id = f"stub-{hex8}"
    g.query("CREATE (s:Point {id:$id, content:'[missing]', is_operator:false})",
            params={"id": stub_id})

    # ── Dead edge from a real operator into the stub (the EP garbage class)
    op_id = sdk.create_operator("IMPL", a, [b])["id"]
    g.query("MATCH (o:Point {id:$oid}), (s:Point {id:$sid}) "
            "MERGE (o)-[:IMPL {idx:2}]->(s) "
            "MERGE (s)-[:INPUT {idx:2}]->(o)",
            params={"oid": op_id, "sid": stub_id})

    # ── Degenerate operator: 1 typed input (engine exclusion threshold <2)
    deg_id = f"deg-{hex8}"
    g.query("CREATE (o:Point {id:$id, content:'degenerate operator', "
            "is_operator:true, op_type:'IMPL'})", params={"id": deg_id})
    g.query("MATCH (o:Point {id:$oid}), (p:Point {id:$pid}) "
            "CREATE (o)-[:IMPL {idx:0}]->(p) "
            "CREATE (p)-[:INPUT {idx:0}]->(o)",
            params={"oid": deg_id, "pid": a})

    # ── NULL-status Point (legacy write; treated LIVE by _live_only)
    ns_id = f"ns-{hex8}"
    g.query("CREATE (p:Point {id:$id, content:'legacy no status', "
            "is_operator:false})", params={"id": ns_id})

    # ── Non-operator Point with missing content (degenerate non-stub)
    nc_id = f"nc-{hex8}"
    g.query("CREATE (p:Point {id:$id, is_operator:false})",
            params={"id": nc_id})

    # ── Sources: one without sourceKind (neutral), one tiered (non-neutral)
    neutral_url = f"https://example.com/nokind-{hex8}"
    g.query("CREATE (s:Source {url:$url, title:'no kind', "
            "ingestedAt:'2026-01-01T00:00:00Z'})", params={"url": neutral_url})
    sdk.create_source(f"https://example.com/tiered-{hex8}", "github_issue",
                      tier="T1")

    # ── Subject stub (auto-created signature: id = name, subjectKind='other')
    subj_name = f"missing-entity-{hex8}"
    g.query("CREATE (s:Subject {name:$name, id:$name, subjectKind:'other'})",
            params={"name": subj_name})
    g.query("MATCH (p:Point {id:$pid}), (s:Subject {name:$name}) "
            "MERGE (p)-[:aboutSubject]->(s)",
            params={"pid": a, "name": subj_name})

    return {"stub_id": stub_id, "op_id": op_id, "deg_id": deg_id,
            "ns_id": ns_id, "nc_id": nc_id, "neutral_url": neutral_url,
            "subj_name": subj_name}


# ── baseline_scan: anomaly detection ─────────────────────────────────────

class TestBaselineScan:
    def test_scan_reports_stub_points(self, sdk):
        a, b = _seed_clean_graph(sdk)
        seeded = _seed_anomalies(sdk, a, b)
        report = baseline_scan.scan_graph(_proj(sdk))
        stub = report["stub_points"]
        assert stub["count"] == 1
        assert seeded["stub_id"] in {s["id"] for s in stub["samples"]}
        # non-operator missing-content class is distinct
        assert report["stub_points"]["also_missing_content_non_operator"]["count"] == 1
        assert seeded["nc_id"] in {
            s["id"] for s in report["stub_points"]
            ["also_missing_content_non_operator"]["samples"]}

    def test_scan_reports_stub_edges(self, sdk):
        a, b = _seed_clean_graph(sdk)
        seeded = _seed_anomalies(sdk, a, b)
        report = baseline_scan.scan_graph(_proj(sdk))
        edges = report["stub_edges"]
        assert edges["count"] >= 1
        assert edges["by_edge_type"].get("IMPL", 0) >= 1
        assert edges["by_edge_type"].get("INPUT", 0) >= 1
        pairs = {(s["operator_id"], s["stub_id"]) for s in edges["samples"]}
        assert (seeded["op_id"], seeded["stub_id"]) in pairs

    def test_scan_reports_degenerate_operators(self, sdk):
        a, b = _seed_clean_graph(sdk)
        seeded = _seed_anomalies(sdk, a, b)
        report = baseline_scan.scan_graph(_proj(sdk))
        deg = report["degenerate_operators"]
        assert deg["count"] == 1
        assert deg["threshold"]  # documents the <2-input engine threshold
        assert seeded["deg_id"] in {s["id"] for s in deg["samples"]}

    def test_scan_reports_null_status_points(self, sdk):
        a, b = _seed_clean_graph(sdk)
        seeded = _seed_anomalies(sdk, a, b)
        report = baseline_scan.scan_graph(_proj(sdk))
        ns = report["null_status_points"]
        assert ns["count"] >= 1
        assert seeded["ns_id"] in {s["id"] for s in ns["samples"]}
        assert "LIVE" in ns["note"]  # NULL status is treated as live

    def test_scan_reports_missing_source_kind(self, sdk):
        a, b = _seed_clean_graph(sdk)
        seeded = _seed_anomalies(sdk, a, b)
        report = baseline_scan.scan_graph(_proj(sdk))
        msk = report["missing_source_kind"]
        assert msk["sources"]["count"] == 1
        assert seeded["neutral_url"] in {s["url"] for s in msk["sources"]["samples"]}
        assert msk["points_legacy"]["count"] >= 1

    def test_scan_reports_tier_neutral_sources(self, sdk):
        a, b = _seed_clean_graph(sdk)   # clean graph: 1 tiered (T1) source
        seeded = _seed_anomalies(sdk, a, b)  # + 1 neutral + 1 tiered (T1)
        report = baseline_scan.scan_graph(_proj(sdk))
        tier = report["source_tier_neutral"]
        assert tier["total_sources"] == 3
        assert tier["count"] == 1          # only the sourceKind-less one
        assert tier["pct_neutral"] == round(1 / 3, 4)
        assert seeded["neutral_url"] in {s["url"] for s in tier["samples"]}

    def test_scan_reports_extracted_from(self, sdk):
        a, b = _seed_clean_graph(sdk)
        _seed_anomalies(sdk, a, b)
        report = baseline_scan.scan_graph(_proj(sdk))
        ef = report["extracted_from"]
        assert ef["points_edge_backed"] == 0  # no extractedFrom edges seeded
        assert ef["evidence_without_source_backing"]["count"] >= 1

    def test_scan_extracted_from_excludes_property_backed(self, sdk):
        """Property-backed points (entities.py ~L171-174) are NOT 'without
        backing' — the metrics must stay disjoint (conf-60 double-count)."""
        a, b = _seed_clean_graph(sdk)
        _seed_anomalies(sdk, a, b)
        proj = _proj(sdk)
        report1 = baseline_scan.scan_graph(proj)
        base = report1["extracted_from"][
            "evidence_without_source_backing"]["count"]

        hex8 = uuid.uuid4().hex[:8]
        pid = f"prop-{hex8}"
        proj.g.query(
            "CREATE (p:Point {id:$id, content:'property-backed evidence', "
            "is_operator:false, extractedFrom:'https://example.com/src-prop'})",
            params={"id": pid})

        report2 = baseline_scan.scan_graph(proj)
        ef = report2["extracted_from"]
        assert ef["points_property_backed"] >= 1
        # property-backed point must NOT be double-counted as without backing
        assert ef["evidence_without_source_backing"]["count"] == base
        assert pid not in {s["id"] for s in
                           ef["evidence_without_source_backing"]["samples"]}

    def test_connect_projection_missing_path_fails(self):
        """Missing embedded path must fail loudly, not spin a fresh empty
        FalkorDBLite DB (conf-65 — all-zero 'clean' scan would mislead)."""
        with pytest.raises(FileNotFoundError):
            baseline_scan.connect_projection(
                {"mode": "embedded", "path": "/nonexistent/334-xyz.db"})

    def test_connect_projection_memory_allowed(self):
        proj = baseline_scan.connect_projection(
            {"mode": "embedded", "path": ":memory:"})
        assert proj is not None

    def test_scan_reports_subject_stubs(self, sdk):
        a, b = _seed_clean_graph(sdk)
        seeded = _seed_anomalies(sdk, a, b)
        report = baseline_scan.scan_graph(_proj(sdk))
        subj = report["subject_stubs"]
        assert subj["count"] == 1
        assert seeded["subj_name"] in {s["name"] for s in subj["samples"]}
        assert subj["about_subject_edges"] >= 1

    def test_scan_cap_skip_unverifiable(self, sdk):
        a, b = _seed_clean_graph(sdk)
        _seed_anomalies(sdk, a, b)
        report = baseline_scan.scan_graph(_proj(sdk))
        cap = report["cap_skip"]
        assert cap["status"] == "UNVERIFIABLE_FROM_GRAPH_STATE"
        assert cap["decision"]["blocks_close"] is True
        assert cap["decision"]["owner"]  # event-log normalization owner recorded

    def test_scan_is_read_only(self, sdk):
        a, b = _seed_clean_graph(sdk)
        _seed_anomalies(sdk, a, b)
        proj = _proj(sdk)
        g = proj.g
        before_nodes = g.query("MATCH (n) RETURN count(n)").result_set[0][0]
        before_edges = g.query("MATCH ()-[r]->() RETURN count(r)").result_set[0][0]
        report = baseline_scan.scan_graph(proj)
        after_nodes = g.query("MATCH (n) RETURN count(n)").result_set[0][0]
        after_edges = g.query("MATCH ()-[r]->() RETURN count(r)").result_set[0][0]
        assert report["read_only"] is True
        assert before_nodes == after_nodes
        assert before_edges == after_edges


# ── connectivity_gate ────────────────────────────────────────────────────

class TestConnectivityGate:
    def test_classify_target_uri(self):
        cfg = connectivity_gate.classify_target("docker://:pw@localhost:6379/tt", None)
        assert cfg["mode"] == "uri"
        assert cfg["snapshot_path"]  # RDB path advertised for URI targets

    def test_classify_target_embedded(self):
        cfg = connectivity_gate.classify_target(None, "/tmp/fresh.db")
        assert cfg["mode"] == "embedded"
        assert "NONE" in cfg["snapshot_path"]  # no docker RDB for embedded

    def test_classify_target_rejects_bad_scheme(self):
        with pytest.raises(ValueError):
            connectivity_gate.classify_target("bolt://localhost:7687/tt", None)

    def test_graph_stats_read_only(self, sdk):
        a, b = _seed_clean_graph(sdk)  # noqa: RUF059
        stats = connectivity_gate.graph_stats(_proj(sdk))
        assert stats["nodes"] >= 4  # 3 points + 1 source
        assert stats["edges"] >= 2  # IMPL + INPUT
        assert stats["by_label"].get("Point", 0) >= 3
        assert stats["by_label"].get("Source", 0) == 1

    def test_connect_projection_missing_path_fails(self):
        """Missing embedded path must fail loudly (conf-65)."""
        with pytest.raises(FileNotFoundError):
            connectivity_gate.connect_projection(
                {"mode": "embedded", "path": "/nonexistent/334-xyz.db"})

    def test_redact_uri(self):
        """URI password must never leak into meta.json/stdout (conf-75)."""
        redacted = connectivity_gate.redact_uri(
            "docker://:s3cr3t@localhost:6379/tortoise")
        assert "s3cr3t" not in redacted
        assert redacted == "docker://:****@localhost:6379/tortoise"
        user_redacted = connectivity_gate.redact_uri(
            "redis://user:s3cr3t@host:6380/db")
        assert "s3cr3t" not in user_redacted
        assert user_redacted == "redis://user:****@host:6380/db"
        # no credentials → unchanged
        assert connectivity_gate.redact_uri(
            "redis://localhost:6379/tt") == "redis://localhost:6379/tt"

    def test_cli_unreachable_exit_nonzero(self):
        # Closed port on loopback → ConnectionRefusedError → gate exits 1
        rc = connectivity_gate.main(
            ["--uri", "docker://:x@127.0.0.1:1/tt-graph", "--json"])
        assert rc == 1


# ── rdb_snapshot_restore guardrails ──────────────────────────────────────

class TestRdbSnapshotRestore:
    def test_parse_uri(self):
        cfg = rdb_snapshot_restore.parse_uri(
            "docker://:pw@localhost:6379/tortoise")
        assert cfg["host"] == "localhost"
        assert cfg["port"] == 6379
        assert cfg["graph"] == "tortoise"

    def test_restore_refuses_without_yes(self):
        result = rdb_snapshot_restore.restore(
            "docker://:x@localhost:6379/tt", "/nonexistent.rdb", None, yes=False)
        assert result["ok"] is False
        assert "refusing to restore without --yes" in result["error"]

    def test_embedded_target_refused(self):
        # Embedded FalkorDBLite has no docker RDB — clear error, exit 1
        rc = rdb_snapshot_restore.main(
            ["--path", "/tmp/some.db", "restore", "--rdb", "/x.rdb", "--yes"])
        assert rc == 1

    def test_snapshot_dry_run(self):
        result = rdb_snapshot_restore.snapshot(
            "docker://:x@localhost:6379/tt", "backups/334", None, dry_run=True)
        assert result["ok"] is True
        assert result["dry_run"] is True
        assert any("BGSAVE" in w for w in result["would"])
        # stats sidecar is recorded BEFORE the BGSAVE (conf-70 ordering)
        assert (result["would"].index(next(w for w in result["would"]
                                             if "meta.json" in w))
                < result["would"].index(next(w for w in result["would"]
                                              if "redis-cli BGSAVE" in w)))

    def test_snapshot_captures_stats_before_bgsave(self, monkeypatch, tmp_path):
        """conf-70: the stats sidecar must be captured BEFORE BGSAVE so it
        describes the exact data state the RDB persists (no TOCTOU), and the
        stored uri must be password-redacted (conf-75)."""
        calls: list[str] = []
        monkeypatch.setattr(rdb_snapshot_restore, "resolve_container",
                            lambda uri, c: (calls.append("resolve"),
                                            "falkordb")[1])
        monkeypatch.setattr(rdb_snapshot_restore, "_container_rdb_info",
                            lambda c: (calls.append("rdb_info"),
                                       {"dir": "/data",
                                        "dbfilename": "dump.rdb"})[1])
        monkeypatch.setattr(rdb_snapshot_restore, "graph_stats_for",
                            lambda uri: (calls.append("stats"),
                                         {"nodes": 3, "edges": 1,
                                          "by_label": {"Point": 3}})[1])
        monkeypatch.setattr(rdb_snapshot_restore, "_bgsave_and_wait",
                            lambda c, start_ts, timeout_s=120:
                            (calls.append("bgsave"),
                             {"ok": True, "lastsave": 1})[1])

        def fake_docker(args, timeout=30):
            calls.append("docker:" + args[0])
            if args[0] == "cp":  # emulate docker cp writing the RDB out
                with open(args[-1], "w", encoding="utf-8") as fh:
                    fh.write("REDIS0009-fake-rdb")
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

        monkeypatch.setattr(rdb_snapshot_restore, "_docker", fake_docker)

        out_dir = str(tmp_path / "snap")
        result = rdb_snapshot_restore.snapshot(
            "docker://:s3cr3t@localhost:6379/tortoise", out_dir, None)
        assert result["ok"] is True
        assert calls.index("stats") < calls.index("bgsave")
        # uri stored redacted — never the raw password (conf-75)
        assert "s3cr3t" not in result["uri"]
        assert "****" in result["uri"]
        with open(result["meta"], encoding="utf-8") as fh:
            meta = json.load(fh)
        assert "s3cr3t" not in meta["uri"]
        assert meta["uri"] == "docker://:****@localhost:6379/tortoise"

    def test_restore_refuses_when_appendonly(self, monkeypatch, tmp_path):
        """conf-60: appendonly=yes makes Redis load the AOF over the placed
        RDB — restore must refuse instead of silently no-oping."""
        rdb_file = str(tmp_path / "snap.rdb")
        with open(rdb_file, "w", encoding="utf-8") as fh:
            fh.write("REDIS0009-fake")
        monkeypatch.setattr(rdb_snapshot_restore, "resolve_container",
                            lambda uri, c: "falkordb")

        def fake_docker(args, timeout=30):
            if (args[:3] == ["exec", "falkordb", "redis-cli"] and
                    args[3:5] == ["CONFIG", "GET"]):
                return subprocess.CompletedProcess(
                    args, 0, stdout="appendonly\nyes\n", stderr="")
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

        monkeypatch.setattr(rdb_snapshot_restore, "_docker", fake_docker)
        result = rdb_snapshot_restore.restore(
            "docker://:x@localhost:6379/tt", rdb_file, None, yes=True)
        assert result["ok"] is False
        assert "appendonly=yes" in result["error"]

    def test_resolve_container_ambiguous_aborts(self, monkeypatch):
        """conf-55: multiple containers publishing the port — refuse rather
        than stop/restart the wrong one."""
        ps_out = ("alpha\t0.0.0.0:6379->6379/tcp\n"
                  "beta\t0.0.0.0:6379->6379/tcp\n")
        monkeypatch.setattr(
            rdb_snapshot_restore, "_docker",
            lambda args, timeout=30: subprocess.CompletedProcess(
                args, 0, stdout=ps_out, stderr=""))
        with pytest.raises(RuntimeError, match="MULTIPLE"):
            rdb_snapshot_restore.resolve_container(
                "docker://:x@localhost:6379/tt", None)

    def test_resolve_container_single_match(self, monkeypatch):
        ps_out = "alpha\t0.0.0.0:6379->6379/tcp\n"
        monkeypatch.setattr(
            rdb_snapshot_restore, "_docker",
            lambda args, timeout=30: subprocess.CompletedProcess(
                args, 0, stdout=ps_out, stderr=""))
        assert rdb_snapshot_restore.resolve_container(
            "docker://:x@localhost:6379/tt", None) == "alpha"

    def test_resolve_container_explicit_arg_wins(self, monkeypatch):
        def boom(args, timeout=30):
            raise AssertionError("docker must not be called")
        monkeypatch.setattr(rdb_snapshot_restore, "_docker", boom)
        assert rdb_snapshot_restore.resolve_container(
            "docker://:x@localhost:6379/tt", "my-falkordb") == "my-falkordb"

    def test_redact_uri_reexport(self):
        assert "s3cr3t" not in rdb_snapshot_restore.redact_uri(
            "docker://:s3cr3t@localhost:6379/tortoise")
