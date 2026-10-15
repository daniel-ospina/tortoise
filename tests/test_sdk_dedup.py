"""Tests for create_point dedup — #80 (content_hash always persisted) + #49 (context-free dedup by content_hash + pointKind).

Runnable with:
  .venv/bin/python -m pytest tests/test_sdk_dedup.py -v
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from tortoise.sdk import TortoiseSDK
from tortoise.source_credibility import AUTHOR_ASSERTION_WEAK_PC


@pytest.fixture
def sdk():
    """SDK with temp embedded database. Closed after test."""
    db_path = os.path.join(tempfile.mkdtemp(prefix="tortoise_dedup_test_"), "test.db")
    sdk = TortoiseSDK(db_path)
    yield sdk
    sdk.close()
    shutil.rmtree(os.path.dirname(db_path), ignore_errors=True)


class TestDedupAlwaysPersistsHash:
    """Issue #80: content_hash is persisted on every creation, so dedup works
    even when the first call omitted dedup=True."""

    def test_create_point_dedup_without_first_dedup(self, sdk):
        """Create without dedup, then with dedup=True → same id returned."""
        content = "Claim created without dedup first"
        p1 = sdk.create_point("statement", content, dedup=False)
        p2 = sdk.create_point("statement", content, dedup=True)
        assert p1["id"] == p2["id"]

    def test_dedup_with_extra_props(self, sdk):
        """A later dedup call with different props must not overwrite the original.

        RE-PINNED (#398, owner ruling 2026-10-12): this used to pin the
        superseded ladder prior as the claim's strength (``gold ⇒ ep_alpha 10``).
        An authored, unsourced assertion is now ONE weak evidence body at the
        weakest registered weight, the same whoever wrote it, and the word is
        kept as the ``credibility`` LABEL. The test's actual subject — a dedup
        hit never overwrites the baseline the point already carries — is
        unchanged, and the ``credibility`` label (not the number) is now what
        makes the two words distinguishable.
        """
        content = "Gold baseline claim"
        p1 = sdk.create_point("hypothesis", content, dedup=True,
                              credibility="gold")
        # The authored weak body landed (gold ⇒ one weak body, NOT a 10:1 prior)
        # so the dedup hit below has something to clobber.
        assert p1.get("ep_alpha") == pytest.approx(1.0 + AUTHOR_ASSERTION_WEAK_PC)
        assert p1.get("baseline_source") == "set-by-author"
        assert p1.get("credibility") == "T0"  # the word, kept as a label
        # Later dedup attempt with a DIFFERENT credibility — must not overwrite
        # the authored assertion already carried.
        p2 = sdk.create_point("hypothesis", content, dedup=True,
                              credibility="T1")
        assert p1["id"] == p2["id"]
        assert p2.get("ep_alpha") == p1.get("ep_alpha")
        assert p2.get("ep_beta") == p1.get("ep_beta") == 1.0
        assert p2.get("baseline_source") == p1.get("baseline_source")
        assert p2.get("credibility") == "T0"  # first word wins


class TestDedupHitAppliesAuthoredBaseline:
    """Issue #7980 — an authored ``credibility`` is a WRITE, never a silent drop.

    The dedup-hit branch used to log-and-drop the resolved prior, so a caller
    that supplied one got a 200 back on a point with NO baseline: not EP-able,
    and the fail-closed #344/#1157 calibration gate then refused the whole
    graph (``/v1/dream`` errors). That is the #4032 silent-false-green shape,
    on the field #7909 added to BOTH REST surfaces.

    FIRST-WRITER-WINS: the prior is applied only while the existing point has
    none — see ``TestDedupAlwaysPersistsHash::test_dedup_with_extra_props``,
    which pins the other half (a dedup hit never overwrites a set baseline,
    which is IMMUTABLE evidence per #844).
    """

    def test_dedup_hit_calibrates_an_uncalibrated_point(self, sdk):
        content = "#7980 dedup hit applies the authored baseline"
        first = sdk.create_point("statement", content, dedup=True)
        # No credibility on the first write, so the point carries no baseline.
        assert not first.get("baseline_set"), first
        second = sdk.create_point("statement", content, dedup=True,
                                  credibility="gold")
        assert first["id"] == second["id"], "still a dedup hit — no duplicate"
        assert second.get("baseline_set") is True, second
        assert second.get("baseline_source") == "set-by-author", second
        # RE-PINNED (#398, owner ruling 2026-10-12): this passed only while the
        # dedup-hit path wrote the LADDER prior the word named (gold ⇒ 10.0) and
        # never the `credibility` label — the defect. The ruled value is ONE
        # weak body, and the label is what lets a real source displace it.
        assert second.get("ep_alpha") == pytest.approx(
            1.0 + AUTHOR_ASSERTION_WEAK_PC), second
        assert second.get("ep_beta") == 1.0, second
        assert second.get("credibility") == "T0", second
        # The in-memory prior EP actually runs with must agree with the graph.
        assert sdk._evidence.get(first["id"]) == (
            pytest.approx(1.0 + AUTHOR_ASSERTION_WEAK_PC), 1.0)

    def test_dedup_hit_leaves_the_graph_ep_able(self, sdk):
        """The observable #7980 symptom, not just the prop: the write reported
        success and the fail-closed gate then refused the graph."""
        content = "#7980 the graph stays EP-able after the hit"
        sdk.create_point("statement", content, dedup=True)
        sdk.create_point("statement", content, dedup=True, credibility="high")
        # Raised CalibrationError before the fix: a live point with no baseline.
        sdk.compute_confidence()

    def test_dedup_hit_weak_body_is_displaceable_by_a_real_source(self, sdk):
        """#398/#6151 Defect 1 — the sequence that was BROKEN.

        Before the fix the dedup-hit path stamped the word's ladder prior and
        (crucially) never wrote the ``credibility`` label, so the recompute
        guard skipped the point FOREVER: a word on a dedup hit was a
        self-declaration no refutation could move. Measured before: 0.9091 and
        frozen. The ruled path writes the weak body + the label, so attaching a
        real T1 source raises the claim (0.5238 -> 0.8361).
        """
        content = "#6151 dedup-hit weak body is displaceable"
        pid = sdk.create_point("statement", content, dedup=True)["id"]
        sdk.create_point("statement", content, dedup=True, credibility="gold")
        before = sdk.get_point(pid)
        assert before.get("ep_alpha") == pytest.approx(
            1.0 + AUTHOR_ASSERTION_WEAK_PC)
        assert before.get("credibility") == "T0"
        # attach a REAL T1 source and run the inheritance/recompute
        sdk.create_source("https://t1-dedup.example", "document", tier="T1",
                          sourceDate="2024-01-01T00:00:00+00:00")
        sdk._get_proj()._link_source(pid, "https://t1-dedup.example")
        sdk._apply_source_inheritance(recency_decay=1.0, recompute_interval=0)
        after = sdk.get_point(pid)
        # 1 + word(0.1) + T1(4.0) = 5.1 -> 0.8361
        assert after.get("ep_alpha") == pytest.approx(5.1)
        assert after.get("baseline_source") == "inherited-from-source"
        assert after["ep_alpha"] > before["ep_alpha"], (
            "a real source must displace the weak authored body")

    def test_dedup_hit_onto_a_born_live_decide_part_keeps_the_2199_default(
            self, sdk):
        """#398/#6151 Defect 1 — the decide-part dedup hit.

        A decide part born live carries the #2199 default Beta(3,1). The word
        ADDS its weak body ON TOP of that provision (3.1 -> 0.7561) and writes
        the label + the recorded marker, so the labelled claim is never weaker
        than the same part unlabelled (frozen at 0.75).
        """
        content = "#6151 born-live decide part dedup word"
        pid = sdk.create_point("option", content)["id"]
        born = sdk.get_point(pid)
        assert born.get("baseline_source") == "system-default"
        assert born.get("baseline_system_default") is True
        sdk.create_point("option", content, dedup=True, credibility="gold")
        hit = sdk.get_point(pid)
        assert hit.get("ep_alpha") == pytest.approx(3.0 + AUTHOR_ASSERTION_WEAK_PC)
        assert hit.get("baseline_source") == "set-by-author"
        assert hit.get("credibility") == "T0"
        assert hit["ep_alpha"] / (hit["ep_alpha"] + hit["ep_beta"]) \
            == pytest.approx(0.7561, abs=0.0001)
        # And the #2199 provision survives a recompute (never weaker than the
        # unlabelled part's frozen 0.75).
        sdk._apply_source_inheritance(recency_decay=1.0, recompute_interval=0)
        rec = sdk.get_point(pid)
        assert rec.get("ep_alpha") == pytest.approx(3.1)

    def test_dedup_hit_plain_point_rules_out_the_ladder_prior(self, sdk):
        """#398/#6151 Defect 1 — the measured value, not the superseded 0.9091.

        The dedup hit onto a point with NO baseline must land at ONE weak body
        (1.1 -> 0.5238), never the 10:1 T0 prior (10.0 -> 0.9091).
        """
        content = "#6151 plain point dedup word"
        pid = sdk.create_point("statement", content, dedup=True)["id"]
        sdk.create_point("statement", content, dedup=True, credibility="gold")
        pt = sdk.get_point(pid)
        mean = pt["ep_alpha"] / (pt["ep_alpha"] + pt["ep_beta"])
        assert mean == pytest.approx(0.5238, abs=0.0001)
        assert mean != pytest.approx(0.9091, abs=0.0001)
        assert pt.get("credibility") == "T0"

    def test_dedup_hit_does_not_grant_2199_to_a_born_draft_option(self, sdk):
        """#6151 review P2 (N2) — the dedup over-grant.

        A decide part BORN live always carries `baseline_set=true` (it got the
        #2199 default at create, `_baseline_create_fields`), so `baseline_set`
        false on the EXISTING point is PROOF the default was never granted.
        The old branch nevertheless decided from the CURRENT call's kind/status
        and stamped the +2.0 default onto a born-DRAFT option on a dedup hit.

        Measured before: ep_alpha 3.1, mean 0.7561, marker=True (an unearned
        +2.0 alpha). After: the word is the option's whole baseline — one weak
        body on neutral — 1.1 / 0.5238, and NO marker.
        """
        content = "#6151 born draft option dedup default"
        pid = sdk.create_point("option", content, status="draft")["id"]
        assert sdk.get_point(pid).get("baseline_set") is None, (
            "a born-draft decide part never received the #2199 default")
        hit = sdk.create_point("option", content, dedup=True, credibility="gold")
        assert hit.get("ep_alpha") == pytest.approx(
            1.0 + AUTHOR_ASSERTION_WEAK_PC), hit
        mean = hit["ep_alpha"] / (hit["ep_alpha"] + hit["ep_beta"])
        assert mean == pytest.approx(0.5238, abs=0.0001), hit
        assert hit.get("baseline_system_default") is None, hit
        assert hit.get("credibility") == "T0", hit

    def test_dedup_hit_preserves_an_existing_baseline_and_says_so(
            self, sdk, caplog):
        """Never an overwrite — and never a SILENT keep either: the caller is
        told which belief won."""
        import logging

        content = "#7980 dedup hit preserves the authored baseline"
        first = sdk.create_point("statement", content, dedup=True,
                                 credibility="gold")
        # RE-PINNED (#398, owner ruling 2026-10-12): the authored word is ONE
        # weak body, not the ladder prior it names (gold ⇒ 10.0 was superseded).
        assert first.get("ep_alpha") == pytest.approx(
            1.0 + AUTHOR_ASSERTION_WEAK_PC)
        # A retry stating the SAME belief loses nothing — the point already
        # carries exactly that author-stated prior — so it must stay quiet.
        # The self-host surface dedups by DEFAULT, which makes this retry the
        # normal path; a WARNING on it is how a warning stops being read.
        with caplog.at_level(logging.WARNING, logger="tortoise.sdk"):
            retry = sdk.create_point("statement", content, dedup=True,
                                     credibility="gold")
        assert retry.get("ep_alpha") == pytest.approx(
            1.0 + AUTHOR_ASSERTION_WEAK_PC), retry
        assert not [r for r in caplog.records if "not applied" in r.getMessage()], [
            r.getMessage() for r in caplog.records]
        # A DIFFERENT belief is the case that must be audible: it is dropped.
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger="tortoise.sdk"):
            second = sdk.create_point("statement", content, dedup=True,
                                      credibility="T1")
        assert second.get("ep_alpha") == pytest.approx(
            1.0 + AUTHOR_ASSERTION_WEAK_PC), second
        assert second.get("baseline_source") == "set-by-author", second
        assert second.get("credibility") == "T0", second
        assert any("not applied" in r.getMessage() for r in caplog.records), [
            r.getMessage() for r in caplog.records]


class TestCrossContextDedup:
    """Issue #93 → #49: create_point dedup contract after context deprecation.

    Context-scoped dedup was removed in #49. Dedup now matches by
    content_hash + pointKind only — context-like props (e.g. wing) do not
    affect matching, and passing `context=` raises TypeError.
    These tests assert the CURRENT contract so a regression in dedup
    keying is caught.
    """

    def test_dedup_same_content_and_kind_returns_existing(self, sdk):
        """Same content dedup returns existing point (hash + kind match)."""
        content = "The sky is blue"

        p1 = sdk.create_point("statement", content, dedup=True)
        p2 = sdk.create_point("statement", content, dedup=True)

        assert p1["id"] == p2["id"], (
            "Same content should return existing point"
        )
        assert p2["content"] == content

    def test_context_param_rejected(self, sdk):
        """context= raises TypeError — the #49 deprecation guard is enforced."""
        content = "Context guard claim"
        with pytest.raises(TypeError):
            sdk.create_point("statement", content, dedup=True, context="legacy-ctx")
        # Even an explicit None context is rejected — no silent fallback.
        with pytest.raises(TypeError):
            sdk.create_point("statement", content, dedup=True, context=None)

    def test_dedup_plain_points_match_each_other(self, sdk):
        """Points without context dedup against each other (hash + kind match)."""
        content = "Untethered claim"

        p1 = sdk.create_point("statement", content, dedup=True)  # no context
        p2 = sdk.create_point("statement", content, dedup=True)  # no context

        assert p1["id"] == p2["id"], (
            "Same content without context should dedup"
        )

    def test_dedup_ignores_context_like_props(self, sdk):
        """Context-like props (wing) no longer isolate dedup — #49 removal.

        Before #49, different contexts created distinct points for the same
        content. Now dedup keys on content_hash + pointKind only, so the
        same content with different wing props dedups to ONE point.
        """
        content = "Contextual claim"

        p1 = sdk.create_point("statement", content, dedup=True, wing="alpha")
        p2 = sdk.create_point("statement", content, dedup=True, wing="beta")

        assert p1["id"] == p2["id"], (
            "Same content must dedup to one point — context-like props do not isolate"
        )

    def test_wings_do_not_isolate_dedup(self, sdk):
        """Same content across three 'contexts' yields ONE point (no isolation)."""
        content = "Cross-context contamination test"

        p_a = sdk.create_point("statement", content, dedup=True, wing="alpha")
        p_b = sdk.create_point("statement", content, dedup=True, wing="beta")
        p_c = sdk.create_point("statement", content, dedup=True, wing="gamma")

        ids = {p_a["id"], p_b["id"], p_c["id"]}
        assert len(ids) == 1, (
            "Context isolation was removed (#49) — all wings must share one point"
        )


class TestDedupHitDropsCallerStatus:
    """Issue #1905 — the dedup-hit branch must drop caller-passed status
    before update_point. update_point rejects any non-'live' status, so a
    dedup hit carrying status='draft' (gated promotion's only explicit
    status) used to raise a raw ValueError mid-batch."""

    def test_dedup_hit_with_draft_status_no_crash(self, sdk):
        """Second create_point with status='draft' dedups to the same point."""
        content = "#1905 draft status dedup hit"
        p1 = sdk.create_point("statement", content, dedup=True, status="draft")
        p2 = sdk.create_point("statement", content, dedup=True, status="draft")
        assert p1["id"] == p2["id"]
        assert p2["status"] == "draft"

    def test_dedup_hit_does_not_mutate_existing_lifecycle(self, sdk):
        """The existing point's status survives a dedup hit unchanged — the
        hit only applies non-lifecycle props (updatedAt), never status."""
        content = "#1905 lifecycle preservation"
        p1 = sdk.create_point("statement", content, dedup=True, status="draft")
        sdk.update_point(p1["id"], status="live")
        # dedup hit with status='draft' must NOT demote the live point
        p2 = sdk.create_point("statement", content, dedup=True, status="draft")
        assert p1["id"] == p2["id"]
        assert p2["status"] == "live"

    def test_dedup_hit_live_status_does_not_promote_draft(self, sdk):
        """A dedup hit with status='live' must NOT promote an existing draft
        point — lifecycle transitions stay explicit (update_point / ingest
        auto-policy), never implicit on a dedup hit (#1905 contract)."""
        content = "#1905 no implicit promotion"
        p1 = sdk.create_point("statement", content, dedup=True, status="draft")
        assert p1["status"] == "draft"
        # pre-#1905 this raised ValueError (update_point rejects non-'live')
        # for the draft→live case it used to promote; both are now a no-op
        p2 = sdk.create_point("statement", content, dedup=True, status="live")
        assert p1["id"] == p2["id"]
        assert p2["status"] == "draft"

    def test_dedup_hit_invalid_status_still_raises(self, sdk):
        """Vocabulary validation is uniform on BOTH paths: an invalid status
        raises even when the point already exists (no silent-ignore on dedup
        hits — status is validated before the branch, #1905)."""
        content = "#1905 uniform validation"
        sdk.create_point("statement", content, dedup=True)
        with pytest.raises(ValueError) as exc:
            sdk.create_point("statement", content, dedup=True, status="bogus")
        assert "Invalid status" in str(exc.value)
