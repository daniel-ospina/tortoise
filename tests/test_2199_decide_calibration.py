"""Issue #2199 — decide-part auto-calibration + baseline provenance regressions.

A user following the DOCUMENTED decide sequence (create options → link
criteria/findings → mitigate → rank) gets a ranking on the FIRST attempt:
decision parts (option/criterion/evidence/decision) filed through the decide
tooling are born LIVE with an explicit, provenance-recorded starting belief
('medium' = Beta(3,1) stamped ``system-default``) unless the author passes a
custom ``credibility`` (stamped ``set-by-author``). The old hidden
promote/calibrate chores are gone and the CalibrationError fail-closed gate
(#344/#1212) still fires ONLY where a live evidence point genuinely has no
baseline (e.g. document-extracted claims whose source is untiered).

Also pins the baseline_source token family rename:
'explicit'→'set-by-author', 'inherited'→'inherited-from-source', new
'system-default' — set_point_baseline rejects old spellings loudly and the
one-time migration (graph-scripts/2199_baseline_source_rename.py) renames
deployed legacy rows so old and new spellings never coexist.

Runs on the embedded fixture (no Docker needed — same pattern as
tests/test_calibration.py) so the suite executes on every lane.
"""
from __future__ import annotations

import importlib.util as _ilu
import os
import tempfile
from pathlib import Path

import pytest

from tortoise.exceptions import CalibrationError
from tortoise.sdk import (
    BASELINE_SOURCE_INHERITED,
    BASELINE_SOURCE_SET_BY_AUTHOR,
    BASELINE_SOURCE_SYSTEM_DEFAULT,
    DECIDE_DEFAULT_CREDIBILITY,
    TortoiseSDK,
)

# The graph-scripts dir has a dash — import the migration under test via
# importlib (same pattern as tests/test_remove_context_migration.py).
_REPO_ROOT = Path(__file__).resolve().parent.parent
_MIGRATION_PATH = _REPO_ROOT / "graph-scripts" / "2199_baseline_source_rename.py"
_spec = _ilu.spec_from_file_location("baseline_source_rename_2199", str(_MIGRATION_PATH))
_migration = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_migration)


@pytest.fixture
def sdk():
    """Fresh embedded TortoiseSDK on an isolated file DB (no Docker needed)."""
    db_path = os.path.join(tempfile.mkdtemp(prefix="tt_2199_"), "test.db")
    s = TortoiseSDK(db_path)
    yield s
    s.close()


def _set_raw_baseline(sdk, pid: str, alpha: float, beta: float,
                      source: str | None) -> None:
    """Stamp a baseline directly on the graph, bypassing set_point_baseline —
    simulates pre-#2199 rows the migration must rename."""
    if source is None:
        sdk._get_proj().g.query(
            "MATCH (n:Point {id:$id}) SET n.ep_alpha=$a, n.ep_beta=$b, "
            "n.baseline_set=true",
            params={"id": pid, "a": alpha, "b": beta},
        )
    else:
        sdk._get_proj().g.query(
            "MATCH (n:Point {id:$id}) SET n.ep_alpha=$a, n.ep_beta=$b, "
            "n.baseline_set=true, n.baseline_source=$src",
            params={"id": pid, "a": alpha, "b": beta, "src": source},
        )


# ── Decision parts born live + baselined (indicator 1) ────────────

class TestDecisionPartsBornLive:
    def test_option_without_status_is_live_with_system_default(self, sdk):
        p = sdk.create_point("option", "Option A")
        pt = sdk.get_point(p["id"])
        assert pt["status"] == "live", "decide part must be born live (#2199)"
        assert pt["baseline_set"] is True
        # default prior 'medium' = Beta(3,1), mean 0.75 (knock-on decision 2)
        assert pt["ep_alpha"] == 3
        assert pt["ep_beta"] == 1
        assert pt["baseline_source"] == BASELINE_SOURCE_SYSTEM_DEFAULT

    def test_all_decide_kinds_born_live_with_explicit_baseline(self, sdk):
        for kind in ("option", "criterion", "evidence", "decision"):
            p = sdk.create_point(kind, f"{kind} content")
            pt = sdk.get_point(p["id"])
            assert pt["status"] == "live", kind
            assert pt["baseline_set"] is True, kind
            assert pt["baseline_source"] == BASELINE_SOURCE_SYSTEM_DEFAULT, kind

    def test_explicit_draft_status_keeps_manual_control(self, sdk):
        """Explicit status= keeps the pre-#2199 posture — no auto-default."""
        p = sdk.create_point("option", "Staged option", status="draft")
        pt = sdk.get_point(p["id"])
        assert pt["status"] == "draft"
        assert pt.get("baseline_set") in (None, False)

    def test_explicit_live_status_keeps_manual_control_no_default(self, sdk):
        """An EXPLICIT status='live' decide part must NOT get a system-default:
        the caller asked for manual control (e.g. a capture/commit receiver
        filing a sourced decide part live — it must stay inherit-eligible so a
        tiered source's belief is applied at EP time, never frozen at the flat
        medium default). Born-live-without-status is the ONLY auto path."""
        p = sdk.create_point("option", "Sourced live option", status="live",
                             extractedFrom="https://docs.example/tiered")
        pt = sdk.get_point(p["id"])
        assert pt["status"] == "live"
        assert pt.get("baseline_set") in (None, False)
        assert pt.get("baseline_source") is None
        # And with a tiered source, inheritance applies at EP time (no
        # system-default token blocking it).
        sdk._get_proj().g.query(
            "MATCH (s:Source {url:$url}) SET s.credibilityTier='T0'",
            params={"url": "https://docs.example/tiered"},
        )
        sdk._apply_source_inheritance(recency_decay=1.0)
        assert sdk.get_point(p["id"])["baseline_source"] == \
            BASELINE_SOURCE_INHERITED

    def test_statement_without_status_unchanged_draft_no_default(self, sdk):
        """Non-decide kinds are untouched — no silent uniform anywhere."""
        p = sdk.create_point("statement", "Plain claim")
        pt = sdk.get_point(p["id"])
        assert pt["status"] == "draft"
        assert pt.get("baseline_set") in (None, False)


# ── Custom override → set-by-author (indicator 2) ─────────────────

class TestCustomCredibility:
    def test_ladder_word_stamps_set_by_author(self, sdk):
        p = sdk.create_point("criterion", "Security", credibility="gold")
        pt = sdk.get_point(p["id"])
        assert pt["status"] == "live"
        assert pt["ep_alpha"] == 10 and pt["ep_beta"] == 1  # gold = (10,1)
        assert pt["baseline_source"] == BASELINE_SOURCE_SET_BY_AUTHOR

    def test_ladder_word_equals_default_betas_still_set_by_author(self, sdk):
        """credibility='medium' == the default Beta(3,1) — but the provenance
        differs: an explicit statement is set-by-author, not system-default."""
        p = sdk.create_point("option", "Opt", credibility="medium")
        pt = sdk.get_point(p["id"])
        assert (pt["ep_alpha"], pt["ep_beta"]) == (3, 1)
        assert pt["baseline_source"] == BASELINE_SOURCE_SET_BY_AUTHOR

    def test_tier_and_numeric_aliases_accepted(self, sdk):
        p_t = sdk.create_point("evidence", "via T2", credibility="T2")
        p_n = sdk.create_point("evidence", "via 3", credibility=3)
        assert sdk.get_point(p_t["id"])["ep_alpha"] == 3
        assert sdk.get_point(p_n["id"])["ep_alpha"] == 2  # low=(2,1)

    def test_unknown_word_fails_loud_before_any_write(self, sdk):
        """A typo'd ladder word raises — never the pre-#2199 silent Beta(1,1)."""
        with pytest.raises(ValueError, match="Unknown credibility"):
            sdk.create_point("option", "Broken", credibility="very-sure")
        # Nothing was created (validation happens before the node write).
        summary = sdk.calibrate_summary()
        assert all("Broken" not in (i.get("content") or "") for i in summary)

    def test_set_point_baseline_rejects_old_spellings(self, sdk):
        p = sdk.create_point("statement", "claim")
        with pytest.raises(ValueError, match="2199_baseline_source_rename"):
            sdk.set_point_baseline(p["id"], 3, 1, source="explicit")
        with pytest.raises(ValueError, match="2199_baseline_source_rename"):
            sdk.set_point_baseline(p["id"], 3, 1, source="inherited")
        with pytest.raises(ValueError, match="2199_baseline_source_rename"):
            sdk.set_point_baseline(p["id"], 3, 1, source="garbage-token")


# ── calibrate_summary shows provenance (indicator 3) ──────────────

class TestCalibrateSummaryProvenance:
    def test_auto_default_visible_in_summary(self, sdk):
        auto = sdk.create_point("option", "Auto default option")
        manual = sdk.create_point("statement", "Author claim", credibility="high")
        rows = {i["id"]: i for i in sdk.calibrate_summary()}
        auto_row = rows[auto["id"]]
        assert auto_row["calibrated"] is True
        assert auto_row["baseline_source"] == BASELINE_SOURCE_SYSTEM_DEFAULT
        assert "system applied its standard starting belief" in auto_row["provenance"]
        manual_row = rows[manual["id"]]
        assert manual_row["baseline_source"] == BASELINE_SOURCE_SET_BY_AUTHOR
        assert "stated by the author" in manual_row["provenance"]

    def test_inherited_baseline_provenance_visible(self, sdk):
        url = "https://doi.org/10.2199/inherit"
        p = sdk.create_point("statement", "Sourced claim", extractedFrom=url)
        sdk._get_proj().g.query(
            "MATCH (s:Source {url:$url}) SET s.credibilityTier='T1'",
            params={"url": url},
        )
        sdk._apply_source_inheritance(recency_decay=1.0)
        rows = {i["id"]: i for i in sdk.calibrate_summary()}
        row = rows[p["id"]]
        assert row["baseline_source"] == BASELINE_SOURCE_INHERITED
        assert "inherited from its source" in row["provenance"]

    def test_legacy_tokenless_baseline_renders_as_set_by_author(self, sdk):
        """Pre-token baseline_set=true rows (2x2 mapping) → author-set copy."""
        p = sdk.create_point("statement", "Legacy")
        _set_raw_baseline(sdk, p["id"], 9.0, 1.0, source=None)
        rows = {i["id"]: i for i in sdk.calibrate_summary()}
        row = rows[p["id"]]
        assert row["calibrated"] is True
        assert "stated by the author" in row["provenance"]

    def test_legacy_token_row_with_old_spelling_surfaced(self, sdk):
        """Un-migrated 'explicit'/'inherited' rows surface the fix path."""
        p = sdk.create_point("statement", "Unmigrated")
        _set_raw_baseline(sdk, p["id"], 5.0, 1.0, source="explicit")
        rows = {i["id"]: i for i in sdk.calibrate_summary()}
        row = rows[p["id"]]
        assert "2199_baseline_source_rename.py" in row["provenance"]


# ── Migration path (issue requirement 4) ─────────────────────────

class TestBaselineSourceRenameMigration:
    def test_migration_renames_legacy_tokens(self, sdk):
        ex = sdk.create_point("statement", "was explicit")
        inh = sdk.create_point("statement", "was inherited")
        sd = sdk.create_point("option", "was system-default")  # new token
        _set_raw_baseline(sdk, ex["id"], 5.0, 1.0, source="explicit")
        _set_raw_baseline(sdk, inh["id"], 6.0, 1.0, source="inherited")

        proj = sdk._get_proj()
        # Sanity: new token untouched by count
        assert _migration.count_legacy(proj) == 2
        moved = _migration.rename_legacy(proj)
        assert moved == {"explicit": 1, "inherited": 1}
        assert _migration.count_legacy(proj) == 0
        assert sdk.get_point(ex["id"])["baseline_source"] == BASELINE_SOURCE_SET_BY_AUTHOR
        assert sdk.get_point(inh["id"])["baseline_source"] == BASELINE_SOURCE_INHERITED
        # system-default (new value, no deployed data) is untouched
        assert sdk.get_point(sd["id"])["baseline_source"] == BASELINE_SOURCE_SYSTEM_DEFAULT
        # Baselines themselves survive the rename
        assert sdk.get_point(ex["id"])["ep_alpha"] == 5.0

    def test_migration_rerun_is_noop(self, sdk):
        ex = sdk.create_point("statement", "was explicit")
        _set_raw_baseline(sdk, ex["id"], 5.0, 1.0, source="explicit")
        proj = sdk._get_proj()
        _migration.rename_legacy(proj)
        assert _migration.count_legacy(proj) == 0
        # Second run: nothing left to move (re-running is safe).
        assert _migration.rename_legacy(proj) == {"explicit": 0, "inherited": 0}


# ── Documented decide flow ranks on the first try (indicator 1) ──

class TestDocumentedDecideFlowFirstTry:
    def _build_documented_decision(self, sdk):
        """Exactly the skill's 7-step protocol — NO promote, NO set_point_baseline."""
        # 1-2. Create the nodes (options/criteria/findings; no status passed —
        # the documented decide flow does not stage drafts).
        opt_a = sdk.create_point("option", "Option A")
        opt_b = sdk.create_point("option", "Option B")
        crit = sdk.create_point("criterion", "Security")
        finding = sdk.create_point("evidence", "A is secure")
        # 4-5. Wire edges.
        op1 = sdk.create_operator("IMPL", crit["id"], [opt_a["id"]])
        sdk.create_operator("IMPL", finding["id"], [opt_a["id"]])
        sdk.create_operator("NAND", finding["id"], [opt_b["id"]])
        # 5. Mitigate an operator (relevance: criterion TRUE but matters LESS).
        sdk.mitigate_operator(op1["id"], "Security weighs less here", 0.30)
        return {"opt_a": opt_a, "opt_b": opt_b}

    def test_flow_ranks_first_try_with_calibration_gate_on(self, sdk):
        pts = self._build_documented_decision(sdk)
        # Every part is live + explicitly baselined — the graph is calibrated
        # after the DOCUMENTED steps alone.
        assert sdk.get_point(pts["opt_a"]["id"])["status"] == "live"
        summary = sdk.calibrate_summary()
        uncalibrated = [i for i in summary if not i["calibrated"]
                        and i.get("status") != "draft"]
        assert uncalibrated == [], f"documented flow left uncalibrated: {uncalibrated}"
        # EP with the fail-closed gate ON — no CalibrationError, no chores.
        result = sdk.compute_confidence(
            anchors=[pts["opt_a"]["id"], pts["opt_b"]["id"]],
            require_calibration=True)
        assert result["converged"] is True
        confs = result["confidences"]
        a = confs.get(pts["opt_a"]["id"], {}).get("mean", 0)
        b = confs.get(pts["opt_b"]["id"], {}).get("mean", 0)
        assert a > b, f"expected A ({a:.3f}) ranked above B ({b:.3f})"

    def test_mitigation_baselined_no_calibration_error(self, sdk):
        """A mitigated operator produces a calibrated live mitigation point."""
        opt = sdk.create_point("option", "Option A")
        finding = sdk.create_point("evidence", "Supports A")
        op = sdk.create_operator("IMPL", finding["id"], [opt["id"]])
        mit = sdk.mitigate_operator(op["id"], "Overstated", 0.2)
        pt = sdk.get_point(mit["id"])
        assert pt["baseline_set"] is True
        assert pt["baseline_source"] == BASELINE_SOURCE_SYSTEM_DEFAULT
        assert (pt["ep_alpha"], pt["ep_beta"]) == (3, 1)
        # idempotent re-mitigation with an author belief re-baselines
        mit2 = sdk.mitigate_operator(op["id"], "Actually very weak", 0.15,
                                     credibility="low")
        assert mit2["id"] == mit["id"]
        pt2 = sdk.get_point(mit["id"])
        assert pt2["baseline_source"] == BASELINE_SOURCE_SET_BY_AUTHOR
        assert pt2["ep_alpha"] == 2 and pt2["ep_beta"] == 1

    def test_remitigating_legacy_uncalibrated_mitigation_heals_it(self, sdk):
        """Re-mitigating a PRE-#2199 mitigation (live statement, no baseline)
        stamps the system default so the documented first-run EP never trips
        the fail-closed CalibrationError on legacy graphs (P2-2 review)."""
        opt = sdk.create_point("option", "Option A")
        finding = sdk.create_point("evidence", "Supports A")
        op = sdk.create_operator("IMPL", finding["id"], [opt["id"]])
        # Simulate a legacy mitigation point with NO baseline.
        proj = sdk._get_proj()
        from tortoise.ids import ulid as _ulid
        mid = _ulid()
        proj.g.query(
            "CREATE (m:Point {id:$id, content:$c, pointKind:'statement', "
            "mitigation_strength:0.3, is_operator:false})",
            params={"id": mid, "c": "[MITIGATION] legacy reason"},
        )
        proj.g.query(
            "MATCH (m:Point {id:$mid}), (op:Point {id:$oid}) "
            "CREATE (m)-[:IMPL]->(op), (op)-[:mitigated_by]->(m)",
            params={"mid": mid, "oid": op["id"]},
        )
        assert sdk.get_point(mid).get("baseline_set") in (None, False)
        healed = sdk.mitigate_operator(op["id"], "Updated reason", 0.2)
        assert healed["id"] == mid
        pt = sdk.get_point(mid)
        assert pt["baseline_set"] is True
        assert pt["baseline_source"] == BASELINE_SOURCE_SYSTEM_DEFAULT
        assert (pt["ep_alpha"], pt["ep_beta"]) == (3, 1)

    def test_mitigate_unknown_credibility_fails_loud(self, sdk):
        opt = sdk.create_point("option", "Option A")
        finding = sdk.create_point("evidence", "Supports A")
        op = sdk.create_operator("IMPL", finding["id"], [opt["id"]])
        with pytest.raises(ValueError, match="Unknown credibility"):
            sdk.mitigate_operator(op["id"], "x", 0.2, credibility="nope")


# ── CalibrationError stays loud where a live point has no baseline ─

class TestGenuinelyUncalibratedStillFailsLoud:
    def test_untiered_sourced_live_statement_fails_loud(self, sdk):
        """Document-extracted claims with an untiered source keep the #344
        fail-closed gate + the source-tier suggestion (unchanged behavior)."""
        url = "https://docs.example/untiered"
        p = sdk.create_point("statement", "Claim from a doc", status="live",
                             extractedFrom=url)
        summary = sdk.calibrate_summary()
        row = next(i for i in summary if i["id"] == p["id"])
        assert row["calibrated"] is False
        assert "untiered" in row["suggestion"]
        with pytest.raises(CalibrationError, match="uncalibrated"):
            sdk.compute_confidence(require_calibration=True)

    def test_live_evidence_kind_without_baseline_not_auto_saved(self, sdk):
        """The system-default applies ONLY to decide-part kinds created through
        the decide tooling. A statement created live without credibility or a
        tiered source is still the engine's genuine no-baseline case."""
        sdk.create_point("statement", "uncalibrated live", status="live")
        with pytest.raises(CalibrationError):
            sdk.compute_confidence(require_calibration=True)
        # And the escape hatch still exists (author belief) — no doc drift.
        sdk2_db = os.path.join(tempfile.mkdtemp(prefix="tt_2199b_"), "t.db")
        s2 = TortoiseSDK(sdk2_db)
        try:
            s2.create_point("statement", "calibrated live", status="live",
                            credibility="medium")
            res = s2.compute_confidence(require_calibration=True)
            assert res["converged"] is True
        finally:
            s2.close()


def test_default_credibility_constant_is_medium():
    """Knock-on decision 2: reuse the ladder — do NOT invent a new number."""
    assert DECIDE_DEFAULT_CREDIBILITY == "medium"
