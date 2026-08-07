"""Coverage expansion for pre-existing TortoiseSDK methods (#257).

Targets the untested method families listed in issue #257:
supersede_point / invalidate_point, annotate_operator /
mitigate_operator, team_create, set_point_baseline /
calibrate_summary. Each family gets a happy-path + error-path test
where applicable, running against embedded FalkorDBLite (temp dir —
no Docker needed, per AGENTS.md).
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from tortoise.exceptions import ControlPlaneError
from tortoise.sdk import TortoiseSDK


@pytest.fixture
def sdk():
    db_path = os.path.join(tempfile.mkdtemp(prefix="tortoise_sdkcov_"), "test.db")
    sdk = TortoiseSDK(db_path)
    yield sdk
    sdk.close()


def _make_operator(sdk: TortoiseSDK, name: str = "op-1") -> str:
    return sdk.create_point("operator", name, is_operator=True)["id"]


def _make_claim(sdk: TortoiseSDK, content: str = "claim content") -> str:
    return sdk.create_point("statement", content)["id"]


# ── supersede_point ────────────────────────────────────────────────────


class TestSupersedePoint:
    def test_supersede_marks_old_outdated_and_links_corrects(self, sdk):
        old = _make_claim(sdk, "old claim")
        new = _make_claim(sdk, "new claim")

        result = sdk.supersede_point(old, new)

        assert result["invalidated"] is True
        assert result["id"] == old
        assert result["corrected_by"] == new
        assert sdk.get_point(old)["outdated"] is True

        proj = sdk._get_proj()
        rows = proj.g.query(
            "MATCH (a:Point {id:$new})-[:CORRECTS]->(b:Point {id:$old}) RETURN 1",
            params={"new": new, "old": old},
        ).result_set
        assert rows, "CORRECTS edge missing after supersede"

    def test_supersede_transfers_edges(self, sdk):
        old = _make_claim(sdk, "old with edge")
        target = _make_claim(sdk, "target")
        sdk.create_operator("IMPL", old, [target])
        new = _make_claim(sdk, "new claim")

        sdk.supersede_point(old, new)

        # The IMPL edge should now point at the new point, not the old one
        proj = sdk._get_proj()
        rows = proj.g.query(
            "MATCH (op:Point {is_operator:true})-[:IMPL]->(n:Point {id:$new}) RETURN 1",
            params={"new": new},
        ).result_set
        assert rows, "IMPL edge not transferred to new point"
        stale = proj.g.query(
            "MATCH (op:Point {is_operator:true})-[:IMPL]->(n:Point {id:$old}) RETURN 1",
            params={"old": old},
        ).result_set
        assert not stale, "IMPL edge still points at superseded point"


# ── invalidate_point ───────────────────────────────────────────────────


class TestInvalidatePoint:
    def test_invalidate_marks_outdated(self, sdk):
        pid = _make_claim(sdk, "to invalidate")
        replacement = _make_claim(sdk, "replacement")

        result = sdk.invalidate_point(pid, replacement)

        assert result == {"invalidated": True, "id": pid, "corrected_by": replacement}
        assert sdk.get_point(pid)["outdated"] is True


# ── annotate_operator ──────────────────────────────────────────────────


class TestAnnotateOperator:
    def test_annotate_happy_path(self, sdk):
        op = _make_operator(sdk)

        result = sdk.annotate_operator(op, 0.3, 0.8, 0.9, 0.4)

        assert result["annotator_bias"] == 0.3
        assert result["annotator_precision"] == 0.8
        assert result["annotator_consistency"] == 0.9
        assert result["annotator_directness"] == 0.4

    def test_annotate_rejects_missing_operator(self, sdk):
        with pytest.raises(ValueError, match="not found"):
            sdk.annotate_operator("no-such-op", 0.5, 0.5, 0.5, 0.5)

    def test_annotate_rejects_non_operator(self, sdk):
        pid = _make_claim(sdk)
        with pytest.raises(ValueError, match="not an operator"):
            sdk.annotate_operator(pid, 0.5, 0.5, 0.5, 0.5)

    @pytest.mark.parametrize("bad", [(1.5, 0.5, 0.5, 0.5), (-0.1, 0.5, 0.5, 0.5),
                                     (0.5, 1.1, 0.5, 0.5), (0.5, 0.5, 0.5, -0.2)])
    def test_annotate_rejects_out_of_range_dims(self, sdk, bad):
        op = _make_operator(sdk)
        with pytest.raises(ValueError):
            sdk.annotate_operator(op, *bad)


# ── mitigate_operator ──────────────────────────────────────────────────


class TestMitigateOperator:
    def test_mitigate_happy_path(self, sdk):
        op = _make_operator(sdk)

        result = sdk.mitigate_operator(op, "outdated source", strength=0.3)

        assert result["id"]
        assert result["mitigation_strength"] == 0.3

    def test_mitigate_is_idempotent(self, sdk):
        op = _make_operator(sdk)

        first = sdk.mitigate_operator(op, "reason A", strength=0.3)
        second = sdk.mitigate_operator(op, "reason B", strength=0.7)

        # Same mitigation point updated, not duplicated
        assert first["id"] == second["id"]
        assert second["mitigation_strength"] == 0.7

    def test_mitigate_rejects_bad_strength(self, sdk):
        op = _make_operator(sdk)
        with pytest.raises(ValueError, match="strength"):
            sdk.mitigate_operator(op, "reason", strength=1.5)

    def test_mitigate_rejects_missing(self, sdk):
        with pytest.raises(ValueError, match="not found"):
            sdk.mitigate_operator("ghost", "reason")


# ── team_create (multi-tenancy) ────────────────────────────────────────


class TestTeamCreate:
    def test_team_create_returns_team(self, sdk):
        result = sdk.team_create("team-alpha")

        assert result["name"] == "team-alpha"
        assert result["graph_name"] == "team_team-alpha"
        assert result["api_key"].startswith("tt_")

    def test_team_create_namespaces_graph(self, sdk):
        sdk.team_create("team-beta")

        # A team-scoped SDK reads its own namespace graph (same embedded DB)
        team_sdk = TortoiseSDK(db_path=sdk._db_path, namespace="team-beta")
        try:
            proj = team_sdk._get_proj()
            assert proj.graph_name != "tortoise"
        finally:
            team_sdk.close()

    @pytest.mark.parametrize("bad", ["bad name!", "", "x" * 65])
    def test_team_create_rejects_bad_names(self, sdk, bad):
        with pytest.raises(ControlPlaneError):
            sdk.team_create(bad)


# ── set_point_baseline / calibrate_summary ─────────────────────────────


class TestBaselineCalibration:
    def test_set_point_baseline_persists_to_graph(self, sdk):
        pid = _make_claim(sdk, "baselined claim")

        sdk.set_point_baseline(pid, 5.0, 2.0)

        # Persisted to the graph (survives a fresh SDK over the same DB)
        fresh = TortoiseSDK(db_path=sdk._db_path)
        try:
            point = fresh.get_point(pid)
            assert point["baseline_set"] is True
            assert point["baseline_source"] == "explicit"
            assert point["ep_alpha"] == 5.0
            assert point["ep_beta"] == 2.0
        finally:
            fresh.close()

    def test_calibrate_summary_returns_list(self, sdk):
        result = sdk.calibrate_summary()

        assert isinstance(result, list)
