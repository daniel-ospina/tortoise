"""Integration tests for S9 skill wiring contracts (tortoise_client.py).

Validates the §6.3 contracts against the current client API:
- queryPriorResearch(domain) → returns claims whose pointKind matches the domain
- writeStrategyPoints(points) → persists Points, queryable via queryExistingStrategies()
- queryExistingVisions(point_kind) → returns vision Points (kind-filtered)
- writeClaim(content, kind, authored_by, confidence) → generic single-claim writer

Note: the `context` kwarg was REMOVED from the API in #49 — pointKind is
the filtering dimension (see sdk.create_point's explicit TypeError).
Runs with FalkorDBLite (embedded) — no Docker needed.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sqlite3
import sys
import tempfile
from pathlib import Path
from unittest.mock import call, patch

from redis import exceptions as redis_exc

# Ensure Tortoise + client are importable (repo root, mirrors tests/test_sdk.py)
_TORTOISE_ROOT = Path(__file__).resolve().parents[1]
if str(_TORTOISE_ROOT) not in sys.path:
    sys.path.insert(0, str(_TORTOISE_ROOT))

import pytest

from tortoise import tortoise_client
from tortoise.sdk import TortoiseSDK


def _fresh_sdk() -> TortoiseSDK:
    """Create a fresh temp db, wire tortoise_client to it, return SDK.

    Isolation: points TORTOISE_DB_PATH at a unique embedded DB so the
    client's no-arg TortoiseSDK() (env-driven) resolves to the same file
    as the returned SDK — each test gets its own DB, no cross-test leaks.
    The temp dir is registered for cleanup by _restore_db_env.
    """
    db_dir = tempfile.mkdtemp(prefix="tortoise_s9_test_")
    _FRESH_SDK_DIRS.append(db_dir)
    db_path = os.path.join(db_dir, "tortoise.db")
    os.environ["TORTOISE_DB_PATH"] = db_path
    os.environ.pop("TORTOISE_DB_URI", None)
    sdk = TortoiseSDK(db_path)
    _FRESH_SDKS.append(sdk)  # closed by _restore_db_env teardown
    return sdk


_FRESH_SDK_DIRS: list[str] = []
_FRESH_SDKS: list = []


@pytest.fixture(autouse=True)
def _restore_db_env():
    """Save/restore TORTOISE_DB_URI + TORTOISE_DB_PATH around each test so
    the _fresh_sdk() env writes never leak into sibling test files. Also
    removes temp DB dirs created by _fresh_sdk (test hygiene)."""
    saved_uri = os.environ.get("TORTOISE_DB_URI")
    saved_path = os.environ.get("TORTOISE_DB_PATH")
    yield
    if saved_uri is None:
        os.environ.pop("TORTOISE_DB_URI", None)
    else:
        os.environ["TORTOISE_DB_URI"] = saved_uri
    if saved_path is None:
        os.environ.pop("TORTOISE_DB_PATH", None)
    else:
        os.environ["TORTOISE_DB_PATH"] = saved_path
    # Close SDKs before removing their DB dirs (open SQLite handles are a
    # Windows hazard and a latent lock-contention source on macOS).
    while _FRESH_SDKS:
        sdk = _FRESH_SDKS.pop()
        try:
            sdk.close()
        except Exception:
            pass
    while _FRESH_SDK_DIRS:
        shutil.rmtree(_FRESH_SDK_DIRS.pop(), ignore_errors=True)


# ══════════════════════════════════════════════════════════════════════
# Test: queryPriorResearch
# ══════════════════════════════════════════════════════════════════════

class TestQueryPriorResearch:
    """§6.3: queryPriorResearch(domain) returns claims matching that kind."""

    def test_returns_existing_claims_by_kind(self):
        sdk = _fresh_sdk()
        sdk.create_point("competitor-analysis",
                         "El Dato competes with OpenTable",
                         authoredBy="research-skill")
        sdk.create_point("competitor-analysis",
                         "Competitor X has 20% market share",
                         authoredBy="research-skill")
        sdk.create_point("statement", "Unrelated claim", authoredBy="other")
        sdk.close()

        results = tortoise_client.query_prior_research("competitor-analysis")
        assert len(results) == 2, f"Expected 2 results, got {len(results)}"
        contents = {r["content"] for r in results}
        assert "El Dato competes with OpenTable" in contents
        assert "Competitor X has 20% market share" in contents
        assert "Unrelated claim" not in contents

    def test_returns_claims_by_kind_match(self):
        sdk = _fresh_sdk()
        sdk.create_point("decision", "Deploy FalkorDB in production",
                         authoredBy="research-skill")
        sdk.close()

        results = tortoise_client.query_prior_research("decision")
        assert len(results) == 1
        assert results[0]["pointKind"] == "decision"

    def test_empty_when_no_match(self):
        sdk = _fresh_sdk()
        sdk.create_point("statement", "Something else", authoredBy="test")
        sdk.close()

        results = tortoise_client.query_prior_research("nonexistent-domain")
        assert results == []
        # Positive control: the same client sees the written point — the
        # empty result is a true no-match, not a silently-degraded client.
        found = tortoise_client.query_prior_research("statement")
        assert len(found) == 1

    def test_empty_domain_returns_empty_healthy(self):
        """Boundary: an empty-string domain must NOT dump the whole graph
        — the SDK's falsy-kind fallback returns ALL points when kind is
        empty, so the client guards it to a no-match, with a positive
        control proving the seeded point is visible via its real kind."""
        _fresh_sdk()
        tortoise_client.write_strategy_points(
            [{"content": "Seed"}], kind="decision")
        assert tortoise_client.query_prior_research("") == []
        assert len(tortoise_client.query_prior_research("decision")) == 1



# ══════════════════════════════════════════════════════════════════════
# Test: writeStrategyPoints + queryExistingStrategies
# ══════════════════════════════════════════════════════════════════════

class TestStrategyPoints:
    """§6.3: writeStrategyPoints → persisted → queryExistingStrategies."""

    def test_write_and_query(self):
        _fresh_sdk()  # wire tortoise_client to fresh db

        points = [
            {"content": "Focus on B2B carousel pipeline in Q3",
             "authoredBy": "define-strategy-skill",
             "confidence": 0.8},
            {"content": "Defer mobile app until Q4",
             "authoredBy": "define-strategy-skill",
             "confidence": 0.9},
        ]
        created = tortoise_client.write_strategy_points(points)
        assert len(created) == 2
        for c in created:
            assert c["pointKind"] == "strategy"
            assert "id" in c

        results = tortoise_client.query_existing_strategies()
        assert len(results) == 2
        contents = {r["content"] for r in results}
        assert "Focus on B2B carousel pipeline in Q3" in contents
        assert "Defer mobile app until Q4" in contents

    def test_empty_strategies_on_fresh_db(self):
        _fresh_sdk()  # fresh db with no data

        results = tortoise_client.query_existing_strategies()
        assert results == []
        # Boundary: empty write list is a no-op in both healthy and
        # degraded states — pin it against the healthy path (#343).
        assert tortoise_client.write_strategy_points([]) == []
        # Positive control: the healthy path is engaged — a write round-trips
        # (a client that always degraded would return [] for writes too).
        created = tortoise_client.write_strategy_points([{"content": "positive control"}])
        assert len(created) == 1
        assert len(tortoise_client.query_existing_strategies()) == 1


# ══════════════════════════════════════════════════════════════════════
# Test: Vision queries
# ══════════════════════════════════════════════════════════════════════

class TestVisionPoints:
    """Vision Point query/write from the skill wiring contracts."""

    def test_write_vision_via_client(self):
        """P0 regression: write_strategy_points(kind='vision') creates vision Points."""
        _fresh_sdk()
        created = tortoise_client.write_strategy_points(
            [{"content": "Vision written via client", "authoredBy": "define-vision-skill"}],
            kind="vision",
        )
        assert len(created) == 1
        assert created[0]["pointKind"] == "vision"

        # Should appear in vision query, NOT in strategy query
        visions = tortoise_client.query_existing_visions()
        assert any(p["content"] == "Vision written via client" for p in visions)

        strategies = tortoise_client.query_existing_strategies()
        assert not any(p["content"] == "Vision written via client" for p in strategies)

    def test_query_visions_filters_by_kind(self):
        sdk = _fresh_sdk()
        sdk.create_point("vision", "El Dato will be the OS for restaurant discovery",
                         authoredBy="define-vision-skill",
                         confidence=0.6)
        sdk.create_point("statement", "Not a vision", authoredBy="test")
        sdk.close()

        results = tortoise_client.query_existing_visions()
        assert len(results) == 1
        assert results[0]["pointKind"] == "vision"
        assert "OS for restaurant discovery" in results[0]["content"]

    def test_query_visions_all(self):
        sdk = _fresh_sdk()
        sdk.create_point("vision", "Vision A", authoredBy="test")
        sdk.create_point("vision", "Vision B", authoredBy="test")
        sdk.close()

        results = tortoise_client.query_existing_visions()
        assert len(results) == 2
        assert all(p["pointKind"] == "vision" for p in results)


# ══════════════════════════════════════════════════════════════════════
# Test: write_claim (generic single-claim writer)
# ══════════════════════════════════════════════════════════════════════

class TestWriteClaim:
    """Generic write_claim function used by research skill."""

    def test_write_and_retrieve(self):
        _fresh_sdk()

        result = tortoise_client.write_claim(
            "El Dato has 5 active competitors", kind="statement",
            authored_by="research-skill",
            confidence=0.8,
        )
        assert result["id"]
        assert result["pointKind"] == "statement"
        assert result["content"] == "El Dato has 5 active competitors"

        # Retrievable via queryPriorResearch on its kind
        results = tortoise_client.query_prior_research("statement")
        assert len(results) == 1
        assert results[0]["content"] == "El Dato has 5 active competitors"

    def test_write_hypothesis_with_low_confidence(self):
        _fresh_sdk()

        result = tortoise_client.write_claim(
            "Competitors may launch similar feature in Q3", kind="hypothesis",
            authored_by="research-skill",
            confidence=0.2,
        )
        assert result["pointKind"] == "hypothesis"
        assert result["confidence"] == 0.2

    def test_confidence_zero_and_full_boundaries(self):
        """Boundary: confidence 0.0 and 1.0 must NOT be dropped. The
        `is not None` guards in write_claim/_prepare_point are exactly the
        pattern that breaks if regressed to a falsy check (0.0 is falsy) —
        a silent confidence drop affects EP propagation semantics."""
        _fresh_sdk()

        zero = tortoise_client.write_claim(
            "Zero-confidence claim", kind="hypothesis", confidence=0.0)
        assert zero["confidence"] == 0.0
        full = tortoise_client.write_claim(
            "Full-confidence claim", kind="hypothesis", confidence=1.0)
        assert full["confidence"] == 1.0

        created = tortoise_client.write_strategy_points(
            [{"content": "S0", "confidence": 0.0}])
        assert created[0]["confidence"] == 0.0


# ══════════════════════════════════════════════════════════════════════
# Test: graceful degradation (issue #343)
# ══════════════════════════════════════════════════════════════════════

class TestGracefulDegradation:
    """Issue #343: unset/invalid TORTOISE_DB_URI must degrade, never crash.

    `_get_sdk()` previously caught ImportError only — a ValueError from an
    unset/bad TORTOISE_DB_URI (raised either at construction or lazily on
    first query via FalkorProjection init) propagated as a traceback.
    Every contract now degrades to its "unavailable" return instead.
    """

    def _clear_db_env(self):
        os.environ.pop("TORTOISE_DB_URI", None)
        os.environ.pop("TORTOISE_DB_PATH", None)

    def test_sdk_constructor_value_error_degrades_not_crashes(self):
        """Historical contract pin: a ValueError from TortoiseSDK()
        construction — the failure class in the reported eldato-era
        traceback (ValueError: Either path or host must be provided) —
        yields graceful degradation. The mock injects the historical
        failure; the real constructor trigger (relative path) is covered
        by test_relative_db_path_is_config_error_not_crash."""
        self._clear_db_env()
        with patch("tortoise.sdk.TortoiseSDK") as mock_sdk:
            mock_sdk.side_effect = ValueError("Either path or host must be provided")
            assert tortoise_client._get_sdk() is None
            assert tortoise_client.query_prior_research("competitor-analysis") == []
            assert tortoise_client.status()["available"] is False

    @pytest.mark.parametrize("exc", list(tortoise_client._UNAVAILABLE_ERRORS))
    def test_lazy_db_failure_degrades_all_contracts(self, exc, capsys):
        """Projection init is lazy — an SDK that constructs OK but fails on
        first use (unreachable DB) must degrade on EVERY contract, never
        traceback. Parametrized over the source tuple itself so coverage
        can never drift from the caught classes."""
        self._clear_db_env()
        with patch("tortoise.sdk.TortoiseSDK") as mock_sdk:
            mock_sdk.return_value.query.side_effect = exc("DB unreachable")
            mock_sdk.return_value.create_point.side_effect = exc("DB unreachable")
            mock_sdk.return_value.summarize_structure.side_effect = exc("DB unreachable")
            assert tortoise_client.query_prior_research("competitor-analysis") == []
            assert tortoise_client.query_existing_strategies() == []
            assert tortoise_client.query_existing_visions() == []
            assert tortoise_client.query_existing_visions(point_kind="strategy") == []
            assert tortoise_client.query_existing_visions(point_kind="") == []
            assert tortoise_client.write_strategy_points([{"content": "x"}]) == []
            claim = tortoise_client.write_claim("x", kind="statement")
            assert claim == {"error": "tortoise_unavailable", "id": "", "written": False}
            status = tortoise_client.status()
            assert status["available"] is False
            assert "TORTOISE_DB_URI" in status["message"]
        # The lazy degradation path must also surface the actionable
        # warning on stderr (not just the construction path).
        assert "tortoise unavailable" in capsys.readouterr().err

    def test_tuple_contains_required_classes(self):
        """The degradation tuple must contain every unavailable class the
        contract promises. Hardcoded here because parametrizing over the
        tuple itself is self-referential: removing a class from the tuple
        would silently drop that parametrized case. This test fails if a
        required class is removed."""
        required = [ImportError, ValueError, RuntimeError, ConnectionError,
                    OSError, sqlite3.OperationalError,
                    redis_exc.ConnectionError, redis_exc.TimeoutError]
        missing = [c for c in required
                   if c not in tortoise_client._UNAVAILABLE_ERRORS]
        assert missing == [], f"Unavailable classes missing from tuple: {missing}"

    def test_unreachable_docker_uri_real_trigger_degrades(self, capsys):
        """REAL lazy-failure trigger (#343's production shape): a docker://
        URI pointing at a closed port constructs fine (projection init is
        lazy) and the FIRST query raises the redis driver's OWN
        ConnectionError — not a subclass of the builtin. The mocked
        parametrized test only proves the client catches classes it is
        handed; this proves the real driver path degrades too."""
        self._clear_db_env()
        os.environ["TORTOISE_DB_URI"] = "docker://localhost:1"
        # Pin the path: construction succeeds → the degradation below must
        # come from lazy first-use, not the constructor catch.
        assert tortoise_client._get_sdk() is not None
        assert tortoise_client.query_prior_research("competitor-analysis") == []
        assert tortoise_client.query_existing_strategies() == []
        assert tortoise_client.write_strategy_points([{"content": "x"}]) == []
        claim = tortoise_client.write_claim("x", kind="statement")
        assert claim == {"error": "tortoise_unavailable", "id": "", "written": False}
        assert tortoise_client.status()["available"] is False
        assert "tortoise unavailable" in capsys.readouterr().err

    @pytest.mark.parametrize("uri", ["not-a-uri", "bolt://:0"])
    def test_malformed_uri_real_trigger_degrades(self, uri):
        """REAL constructor-accepted / lazy-parse-failure triggers: a URI
        with an unsupported scheme constructs fine and raises ValueError
        ("Unsupported scheme") on first use. Pins that the REAL SDK
        failure classes for malformed input are inside the tuple."""
        self._clear_db_env()
        os.environ["TORTOISE_DB_URI"] = uri
        assert tortoise_client._get_sdk() is not None
        assert tortoise_client.query_prior_research("competitor-analysis") == []
        assert tortoise_client.status()["available"] is False

    def test_query_visions_point_kind_filter_healthy(self):
        """Healthy-path branch pin: point_kind filters to that kind; the
        empty-string fallback behaves identically to the default vision
        query (falsy guard) (#343)."""
        _fresh_sdk()
        tortoise_client.write_strategy_points(
            [{"content": "Vision A", "authoredBy": "t"},
             {"content": "Vision B", "authoredBy": "t"}],
            kind="vision",
        )
        tortoise_client.write_strategy_points(
            [{"content": "Strategy S", "authoredBy": "t"}], kind="strategy")

        by_kind = tortoise_client.query_existing_visions(point_kind="strategy")
        assert len(by_kind) == 1
        assert by_kind[0]["content"] == "Strategy S"

        default = tortoise_client.query_existing_visions()
        empty = tortoise_client.query_existing_visions(point_kind="")
        assert all(p["pointKind"] == "vision" for p in default)
        assert len(default) == 2
        assert empty == default

    def test_missing_sdk_module_degrades(self):
        """Original degradation path (ImportError on the SDK import)
        preserved: None SDK + empty query result, never a traceback."""
        self._clear_db_env()
        with patch.dict(sys.modules, {"tortoise.sdk": None}):
            assert tortoise_client._get_sdk() is None
            assert tortoise_client.query_prior_research("competitor-analysis") == []

    def test_production_unset_uri_degrades(self, monkeypatch):
        """REAL SDK trigger: FLY_APP_NAME + no URI raises RuntimeError in
        the constructor (the SDK's P0 data-loss guard) BEFORE any DB
        access — the client must still degrade, not traceback (#343)."""
        self._clear_db_env()
        monkeypatch.setenv("FLY_APP_NAME", "tortoise")
        assert tortoise_client._get_sdk() is None
        assert tortoise_client.query_prior_research("competitor-analysis") == []
        assert tortoise_client.status()["available"] is False

    def test_relative_db_path_is_config_error_not_crash(self):
        """Relative TORTOISE_DB_PATH raises ValueError at SDK construction
        (RELATIVE_PATH_ERROR) — the REAL trigger for the constructor-value
        error class. Every contract degrades, never crashes."""
        self._clear_db_env()
        os.environ["TORTOISE_DB_PATH"] = "relative/tortoise.db"
        assert tortoise_client._get_sdk() is None
        assert tortoise_client.query_prior_research("competitor-analysis") == []
        assert tortoise_client.query_existing_strategies() == []
        assert tortoise_client.query_existing_visions() == []
        assert tortoise_client.write_strategy_points([{"content": "x"}]) == []
        claim = tortoise_client.write_claim("x", kind="statement")
        assert claim == {"error": "tortoise_unavailable", "id": "", "written": False}
        assert tortoise_client.status()["available"] is False

    def test_unset_uri_logs_actionable_warning(self, capsys):
        """The degradation path must surface the actionable message
        (TORTOISE_DB_URI + `tortoise init` hint) on stderr. Real trigger:
        relative TORTOISE_DB_PATH raises the constructor ValueError."""
        self._clear_db_env()
        os.environ["TORTOISE_DB_PATH"] = "relative/tortoise.db"
        tortoise_client.query_prior_research("competitor-analysis")
        err = capsys.readouterr().err
        assert "tortoise unavailable" in err
        assert "TORTOISE_DB_URI" in err
        assert "tortoise init" in err
        # The stderr payload is the documented JSON shape (agent-parseable).
        payload = json.loads(err)
        assert payload["status"] == "noop"
        assert "warning" in payload

    @pytest.mark.parametrize("argv,out_key,out_value", [
        (["tortoise_client.py", "query-prior-research", "--domain", "x"], "count", 0),
        (["tortoise_client.py", "query-strategies"], "count", 0),
        (["tortoise_client.py", "query-visions"], "count", 0),
        (["tortoise_client.py", "write-points", "--kind", "strategy",
          "--points-json", '[{"content": "x"}]'], "written", 0),
        (["tortoise_client.py", "write-claim", "--content", "x"],
         "error", "tortoise_unavailable"),
        (["tortoise_client.py", "status"], "available", False),
    ])
    def test_cli_subcommands_degrade(self, argv, out_key, out_value, capsys, monkeypatch):
        """Every CLI subcommand degrades to JSON output with NO SystemExit
        (exit-0 contract) when the SDK is unavailable (#343). Real trigger:
        relative TORTOISE_DB_PATH raises the constructor ValueError."""
        self._clear_db_env()
        os.environ["TORTOISE_DB_PATH"] = "relative/tortoise.db"
        monkeypatch.setattr(sys, "argv", argv)
        tortoise_client.main()  # must not raise SystemExit
        payload = json.loads(capsys.readouterr().out)
        assert payload[out_key] == out_value

    def test_cli_process_exits_zero(self):
        """Real-process CLI contract: degradation → returncode 0, JSON
        stdout, actionable stderr — no traceback. A relative
        TORTOISE_DB_PATH forces the config error deterministically (no
        default embedded-DB side effects); PYTHONPATH is pinned so the
        `tortoise` package resolves regardless of ambient environment."""
        env = {k: v for k, v in os.environ.items()
               if k not in ("TORTOISE_DB_URI", "TORTOISE_DB_PATH", "FLY_APP_NAME")}
        env["TORTOISE_DB_PATH"] = "relative/tortoise.db"
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
        proc = subprocess.run(
            [sys.executable, str(tortoise_client.__file__),
             "query-prior-research", "--domain", "x"],
            capture_output=True, text=True, timeout=120, env=env,
            cwd=str(Path(__file__).resolve().parents[1]),
        )
        assert proc.returncode == 0, proc.stderr
        assert json.loads(proc.stdout)["count"] == 0
        assert "tortoise init" in proc.stderr
        # Trigger determinism (relative path raises the config ValueError,
        # not an import failure) is pinned in-process by
        # test_relative_db_path_is_config_error_not_crash — asserting on
        # the exception class name inside the warning string would couple
        # this test to an internal message format.

    def test_cli_write_points_healthy_roundtrip(self, capsys, monkeypatch):
        """Healthy-path CLI contract: against a real embedded DB the
        success JSON schema is printed (written/results) and points
        round-trip — the degraded CLI tests alone can't pin this. Also
        covers the CLI string→float confidence path at the 0.0 boundary,
        the empty-batch no-op, and --kind forwarding to vision."""
        _fresh_sdk()
        monkeypatch.setattr(sys, "argv", [
            "tortoise_client.py", "write-points", "--kind", "strategy",
            "--points-json", '[{"content": "cli healthy path"}]',
        ])
        tortoise_client.main()
        payload = json.loads(capsys.readouterr().out)
        assert payload["written"] == 1
        assert payload["results"][0]["pointKind"] == "strategy"

        monkeypatch.setattr(sys, "argv", [
            "tortoise_client.py", "write-points", "--kind", "strategy",
            "--points-json", "[]",
        ])
        tortoise_client.main()
        empty = json.loads(capsys.readouterr().out)
        assert empty["written"] == 0
        assert empty["results"] == []

        monkeypatch.setattr(sys, "argv", [
            "tortoise_client.py", "write-points", "--kind", "vision",
            "--points-json", '[{"content": "cli vision"}]',
        ])
        tortoise_client.main()
        vision = json.loads(capsys.readouterr().out)
        assert vision["written"] == 1
        assert vision["results"][0]["pointKind"] == "vision"

        monkeypatch.setattr(sys, "argv", [
            "tortoise_client.py", "write-claim", "--content", "cli claim",
            "--kind", "hypothesis", "--confidence", "0.0",
        ])
        tortoise_client.main()
        claim = json.loads(capsys.readouterr().out)
        assert claim["confidence"] == 0.0

    def test_cli_queries_healthy_roundtrip(self, capsys, monkeypatch):
        """Healthy-path CLI wiring for the read subcommands: --domain and
        --point-kind must actually reach the query (the degraded tests
        can't prove argument forwarding — their outputs are constants).
        Contrast pairs pin the forwarding: a hardcoded kind would fail the
        differing outcomes."""
        _fresh_sdk()
        tortoise_client.write_strategy_points(
            [{"content": "Vision A", "authoredBy": "t"},
             {"content": "Vision B", "authoredBy": "t"}],
            kind="vision",
        )
        tortoise_client.write_strategy_points(
            [{"content": "Strategy S", "authoredBy": "t"}], kind="strategy")
        tortoise_client.write_strategy_points(
            [{"content": "Statement T", "authoredBy": "t"}], kind="statement")

        monkeypatch.setattr(sys, "argv", [
            "tortoise_client.py", "query-visions", "--point-kind", "strategy"])
        tortoise_client.main()
        filtered = json.loads(capsys.readouterr().out)
        assert filtered["count"] == 1
        assert filtered["results"][0]["content"] == "Strategy S"

        monkeypatch.setattr(sys, "argv", ["tortoise_client.py", "query-visions"])
        tortoise_client.main()
        default = json.loads(capsys.readouterr().out)
        assert default["count"] == 2
        assert all(p["pointKind"] == "vision" for p in default["results"])

        # Contrast: a hardcoded "vision" kind would return 2 here.
        monkeypatch.setattr(sys, "argv", [
            "tortoise_client.py", "query-prior-research", "--domain", "statement"])
        tortoise_client.main()
        prior = json.loads(capsys.readouterr().out)
        assert prior["count"] == 1
        assert prior["results"][0]["content"] == "Statement T"

        monkeypatch.setattr(sys, "argv", ["tortoise_client.py", "query-strategies"])
        tortoise_client.main()
        strategies = json.loads(capsys.readouterr().out)
        assert strategies["count"] == 1
        assert strategies["results"][0]["content"] == "Strategy S"

        monkeypatch.setattr(sys, "argv", ["tortoise_client.py", "status"])
        tortoise_client.main()
        status = json.loads(capsys.readouterr().out)
        assert status["available"] is True
        assert status["db_uri"] == os.environ["TORTOISE_DB_PATH"]

    @pytest.mark.parametrize("argv,code", [
        (["tortoise_client.py"], 1),  # no command → help + exit 1
        (["tortoise_client.py", "bogus-cmd"], 2),  # argparse unknown
        (["tortoise_client.py", "query-prior-research"], 2),  # missing --domain
    ])
    def test_cli_terminal_paths(self, argv, code, capsys, monkeypatch):
        """main()'s argparse-native terminal paths: no command → help +
        exit 1; unknown subcommand / missing required --domain →
        SystemExit(2). No tracebacks."""
        self._clear_db_env()
        monkeypatch.setattr(sys, "argv", argv)
        with pytest.raises(SystemExit) as excinfo:
            tortoise_client.main()
        assert excinfo.value.code == code

    def test_to_json_falls_back_to_str(self):
        """_to_json's default=str fallback keeps non-serializable values
        in results parseable."""
        payload = json.loads(tortoise_client._to_json({"x": object()}))
        assert isinstance(payload["x"], str)

    def test_cli_bad_json_exits_one(self, capsys, monkeypatch):
        """Unrelated loud-error path preserved: invalid --points-json still
        exits 1 with a JSON error (not swallowed by degradation)."""
        self._clear_db_env()
        monkeypatch.setattr(sys, "argv", [
            "tortoise_client.py", "write-points", "--kind", "strategy",
            "--points-json", "{not json",
        ])
        with pytest.raises(SystemExit) as excinfo:
            tortoise_client.main()
        assert excinfo.value.code == 1
        err = json.loads(capsys.readouterr().err)
        assert err["error"] == "invalid-json"

    @pytest.mark.parametrize("argv", [
        ["tortoise_client.py", "write-points", "--kind", "strategy",
         "--points-json", '[{"content": "x", "confidence": "high"}]'],
        ["tortoise_client.py", "write-claim", "--content", "x",
         "--confidence", "high"],
    ])
    def test_cli_bad_input_emits_json_error(self, argv, capsys, monkeypatch):
        """CLI input errors (bad confidence) emit JSON + exit 1 — no raw
        traceback, consistent across both write subcommands (#343). Uses a
        REAL SDK: input validation fires before any SDK call, so the mock
        would add patch-target coupling without signal."""
        _fresh_sdk()
        monkeypatch.setattr(sys, "argv", argv)
        with pytest.raises(SystemExit) as excinfo:
            tortoise_client.main()
        assert excinfo.value.code == 1
        err = json.loads(capsys.readouterr().err)
        assert err["error"] == "invalid-input"

    def test_non_unavailable_errors_propagate(self):
        """The degradation tuple must NOT swallow genuine bugs — a TypeError
        from the SDK propagates to the caller."""
        self._clear_db_env()
        with patch("tortoise.sdk.TortoiseSDK") as mock_sdk:
            mock_sdk.return_value.query.side_effect = TypeError("bug")
            with pytest.raises(TypeError):
                tortoise_client.query_prior_research("competitor-analysis")
            mock_sdk.return_value.create_point.side_effect = TypeError("bug")
            with pytest.raises(TypeError):
                tortoise_client.write_strategy_points([{"content": "x"}])

    def test_bad_confidence_is_input_error_not_degradation(self):
        """Client-side input errors surface loudly — they must NOT be
        masked as 'tortoise unavailable' by the degradation catch. Uses a
        REAL SDK (input validation precedes any SDK call, so the mock
        adds coupling without signal)."""
        _fresh_sdk()
        with pytest.raises(ValueError):
            tortoise_client.write_strategy_points(
                [{"content": "x", "confidence": "high"}])
        with pytest.raises(ValueError):
            tortoise_client.write_claim("x", kind="statement", confidence="high")
        with pytest.raises(KeyError):
            tortoise_client.write_strategy_points([{"authoredBy": "x"}])

    def test_bad_confidence_while_degraded_returns_empty(self):
        """When the SDK is unavailable, degradation wins over input
        validation: writes return their degraded result (no write attempt)
        rather than raising — pinned for both write contracts. Real
        trigger: relative TORTOISE_DB_PATH makes the SDK unavailable."""
        self._clear_db_env()
        os.environ["TORTOISE_DB_PATH"] = "relative/tortoise.db"
        assert tortoise_client.write_strategy_points(
            [{"content": "x", "confidence": "high"}]) == []
        assert tortoise_client.write_claim(
            "x", kind="statement", confidence="high") == {
            "error": "tortoise_unavailable", "id": "", "written": False}

    def test_non_locked_error_not_retried(self):
        """Only lock contention is retried: a non-locked error on the
        first attempt propagates immediately with NO backoff sleep, and —
        being outside the unavailable tuple — surfaces to the caller
        rather than degrading. A locked-then-non-locked sequence sleeps
        once, then propagates."""
        self._clear_db_env()
        with patch("tortoise.sdk.TortoiseSDK") as mock_sdk, patch("time.sleep") as sleep_mock:
            mock_sdk.return_value.create_point.side_effect = TypeError("boom")
            with pytest.raises(TypeError):
                tortoise_client.write_strategy_points([{"content": "a"}])
        assert sleep_mock.call_args_list == []

        with patch("tortoise.sdk.TortoiseSDK") as mock_sdk, patch("time.sleep") as sleep_mock:
            mock_sdk.return_value.create_point.side_effect = [
                sqlite3.OperationalError("database is locked"),
                TypeError("boom"),
            ]
            with pytest.raises(TypeError):
                tortoise_client.write_strategy_points([{"content": "a"}])
        assert sleep_mock.call_args_list == [call(0.1)]

    def test_mid_loop_failure_degrades_all_or_nothing(self):
        """Pin batch semantics: if the DB fails mid-batch, the write
        contract degrades to [] — already-persisted points are NOT
        reported, so callers must treat the batch as not-written (known
        limitation; callers should retry with idempotency in mind)."""
        self._clear_db_env()
        with patch("tortoise.sdk.TortoiseSDK") as mock_sdk:
            mock_sdk.return_value.create_point.side_effect = [
                {"id": "1", "pointKind": "strategy"},
                redis_exc.ConnectionError("DB unreachable"),
            ]
            assert tortoise_client.write_strategy_points(
                [{"content": "a"}, {"content": "b"}]) == []

    def test_locked_error_retried_then_succeeds(self):
        """Lock contention is transient: create_point raising a locked
        error on attempts 1-2 is retried (backoff) and the batch completes
        on attempt 3. Two transient failures → exactly two backoff sleeps
        (100ms/200ms schedule documented in _create_with_retry)."""
        self._clear_db_env()
        with patch("tortoise.sdk.TortoiseSDK") as mock_sdk, patch("time.sleep") as sleep_mock:
            mock_sdk.return_value.create_point.side_effect = [
                sqlite3.OperationalError("database is locked"),
                sqlite3.OperationalError("database is locked"),
                {"id": "1", "pointKind": "strategy"},
            ]
            created = tortoise_client.write_strategy_points([{"content": "a"}])
        assert len(created) == 1
        assert created[0]["id"] == "1"
        assert sleep_mock.call_args_list == [call(0.1), call(0.2)]

    def test_locked_error_exhausted_degrades(self):
        """Lock contention that survives all retries degrades to [] (the
        embedded lock error class IS in the tuple) — no raw traceback."""
        self._clear_db_env()
        with patch("tortoise.sdk.TortoiseSDK") as mock_sdk, patch("time.sleep"):
            mock_sdk.return_value.create_point.side_effect = sqlite3.OperationalError(
                "database is locked")
            assert tortoise_client.write_strategy_points([{"content": "a"}]) == []

    def test_status_healthy_reports_available(self):
        """Status happy path: SDK reachable → available True with a real
        chain dict, and db_uri reports the TORTOISE_DB_PATH branch when
        the URI is unset (echo of the env the SDK resolves from)."""
        sdk = _fresh_sdk()
        sdk.close()
        result = tortoise_client.status()
        assert result["available"] is True
        assert isinstance(result["chain_status"], dict)
        assert result["db_uri"] == os.environ["TORTOISE_DB_PATH"]

    def test_check_available_three_states(self):
        """_check_available() is a first-use probe: False when
        construction fails (config error), False when the DB is
        unreachable (the #343 shape — a construction-only probe would
        lie, since the constructor is lazy), True against a healthy
        embedded DB."""
        self._clear_db_env()
        os.environ["TORTOISE_DB_PATH"] = "relative/tortoise.db"
        assert tortoise_client._check_available() is False
        self._clear_db_env()
        os.environ["TORTOISE_DB_URI"] = "docker://localhost:1"
        assert tortoise_client._check_available() is False
        _fresh_sdk()
        assert tortoise_client._check_available() is True

    def test_visions_empty_fresh_db_positive_control(self):
        """Healthy empty-result boundary for visions: fresh DB → [] is a
        true empty (positive control round-trips), mirroring the
        strategies empty-fresh-db pin."""
        _fresh_sdk()
        assert tortoise_client.query_existing_visions() == []
        created = tortoise_client.write_strategy_points(
            [{"content": "Vision control"}], kind="vision")
        assert len(created) == 1
        assert len(tortoise_client.query_existing_visions()) == 1

    def test_status_uri_preferred_branch(self):
        """status() db_uri prefers TORTOISE_DB_URI over PATH. summarize is
        stubbed so the probe succeeds without a live DB — the URI is only
        echoed, never connected to."""
        self._clear_db_env()
        os.environ["TORTOISE_DB_URI"] = "docker://localhost:1"
        os.environ["TORTOISE_DB_PATH"] = "/tmp/ignored.db"
        with patch.object(TortoiseSDK, "summarize_structure", return_value={"nodes": 0}):
            result = tortoise_client.status()
        assert result["available"] is True
        assert result["db_uri"] == "docker://localhost:1"

    def test_status_not_set_fallback(self, monkeypatch):
        """status() db_uri fallback branch: with neither env var set, the
        SDK opens the canonical default path (healthy) and db_uri reports
        the literal 'not set' fallback. HOME is pointed at a SHORT temp
        path (with ~/.tortoise pre-created — falkordblite does not create
        the parent dir) so the default-path DB has no repo side effects.

        NOTE: not pytest's tmp_path — the embedded FalkorDBLite redis
        socket lives under HOME/.tortoise/... and macOS unix socket paths
        cap at 104 chars; tmp_path's deep dir makes connect() fail ENOENT
        (same flake family as #819)."""
        self._clear_db_env()
        monkeypatch.delenv("FLY_APP_NAME", raising=False)
        short_home = Path("/tmp/t343_status_home")
        shutil.rmtree(short_home, ignore_errors=True)
        (short_home / ".tortoise").mkdir(parents=True)
        monkeypatch.setenv("HOME", str(short_home))
        try:
            result = tortoise_client.status()
        finally:
            shutil.rmtree(short_home, ignore_errors=True)
        assert result["available"] is True
        assert result["db_uri"] == "not set"

    def test_status_non_unavailability_error_keeps_available(self):
        """A non-tuple failure inside summarize_structure is a query error,
        not unavailability — available stays True with an error chain."""
        self._clear_db_env()
        with patch("tortoise.sdk.TortoiseSDK") as mock_sdk:
            mock_sdk.return_value.summarize_structure.side_effect = TypeError("bug")
            result = tortoise_client.status()
        assert result["available"] is True
        assert result["chain_status"]["error"] == "query failed"
