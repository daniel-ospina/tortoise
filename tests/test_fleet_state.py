"""Hermetic tests for tools/fleet_state.py (#7750).

No network, no cmux, no git: every live source is a pure input or a monkeypatched
``_run``. The cases are the ones the work order names as the reason the tool
exists, and each is chosen so it can FAIL on a regression:

  * the #7750 INVERSION — ``6 Durability core`` had no resume binding and was
    attributed to ``4a``'s session. A resolver that falls back to a guessed file
    fails ``test_no_binding_never_adopts_another_lanes_session``.
  * one session, one lane — a shared binding is a REPORTED violation.
  * the FREE trap — work in a GRANDCHILD keeps a lane busy; a direct-child-only
    CPU walk fails ``test_tree_cpu_includes_a_grandchild``.
  * the AUTHORITATIVE binding field — ``restore_record.checkpoint_id`` is stale;
    only ``resume_binding`` counts. A regression fails the monkeypatched test.
"""

from __future__ import annotations

import argparse
import ast
import inspect
import json
import os
import signal
import stat
import sys
import threading
import time
from pathlib import Path

import pytest

# #5049 rule 1: a wall-clock expiry is an INCONCLUSIVE skip that NAMES the deadline — never a
# product-defect FAIL. Imported at module level (stdlib + pytest only, so the #1686 test-mode
# import ordering is untouched) because `test_pi_processes_sees_a_real_long_path_lane` needs it
# on the path where the FIXTURE — not the census — is what went away.
#
# `_HARNESS_PREFIX` is imported privately ON PURPOSE: it is the single source of the
# `HARNESS DEFECT [#5049]` family marker, and every in-tree `HarnessDefect` raise site renders it
# from this same constant — `tests/_verdict.py` and this file's three arms (census-error, crash,
# clean-exit). Re-measured rather than listed, because a list written into prose goes stale the
# moment a fourth arm lands. The census, ANCHORED on purpose (the unanchored pattern matches this
# very comment, giving 6 for 5):
#
#   grep -rn '^[[:space:]]*raise HarnessDefect(' --include=*.py tests/ tools/ tortoise/
#
# Duplicating the literal would let the two drift, and the marker is what makes a harness fault
# attributable in a log rather than only by its exception class.
from tests._verdict import (
    _HARNESS_PREFIX,
    INCONCLUSIVE_PREFIX,
    HarnessDefect,
    inconclusive,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import fleet_state as fs


def _stamp(age_s: float = 0.0) -> str:
    """A ``generated_at`` stamp exactly as ``build_state`` writes one, ``age_s`` old.

    Freshness is judged on this stamp (#7957), so a fixture that omits it is an
    UNREADABLE-AGE cache — never a fresh one.
    """
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(time.time() - age_s))

S_4A = "01a0d5bb-c98a-71b9-9516-47f7a78550a5"
S_6 = "01a0d5bb-d13c-736d-8260-5cb3af6b7b05"
S_OTHER = "01a10471-fb6b-7448-b181-d7559bc654bc"


def file_exists_for(*sids: str):
    present = set(sids)

    def _fe(sid: str) -> Path | None:
        return Path(f"/sessions/{sid}.jsonl") if sid in present else None

    return _fe


# ---------------------------------------------------------------------------
# the #7750 inversion — the KNOWN-TRUE case
# ---------------------------------------------------------------------------

def test_no_binding_never_adopts_another_lanes_session() -> None:
    """`6 Durability core` has NO pane binding. Its disk identity scan is ambiguous
    and its only candidate file is `4a`'s session. The honest answer is UNKNOWN —
    NOT a confident adoption of another lane's session (the measured defect)."""
    rows = [("4a Retrieval and traversal", "WS-4A"), ("6 Durability core", "WS-6")]
    pane = {"WS-4A": S_4A}  # 6 has NO binding
    disk = {"6 Durability core": [S_4A]}  # the identity scan surfaces 4a's file
    res, violations = fs.resolve_sessions(
        rows, pane, {}, disk, file_exists_for(S_4A, S_6)
    )
    assert res["4a Retrieval and traversal"].sid == S_4A
    assert res["4a Retrieval and traversal"].source == fs.SOURCE_PANE
    # THE ASSERTION THAT FAILS IF A GUESS IS REINTRODUCED:
    assert res["6 Durability core"].sid is None
    assert res["6 Durability core"].source == fs.SOURCE_UNKNOWN
    assert res["6 Durability core"].bound is False
    assert violations == []


def test_override_binds_the_known_true_lane_without_touching_4a() -> None:
    """The disk-verified override gives `6 Durability core` its OWN session — the
    known-true lane reading correctly, and 4a is untouched."""
    rows = [("4a Retrieval and traversal", "WS-4A"), ("6 Durability core", "WS-6")]
    res, _ = fs.resolve_sessions(
        rows, {"WS-4A": S_4A}, {"6 Durability core": S_6}, {}, file_exists_for(S_4A, S_6)
    )
    assert res["6 Durability core"].sid == S_6
    assert res["6 Durability core"].source == fs.SOURCE_OVERRIDE
    assert res["6 Durability core"].sid != res["4a Retrieval and traversal"].sid


def test_one_session_many_lanes_is_reported_not_hidden() -> None:
    rows = [("A", "WS-A"), ("B", "WS-B")]
    res, violations = fs.resolve_sessions(
        rows, {"WS-A": S_4A, "WS-B": S_4A}, {}, {}, file_exists_for(S_4A)
    )
    assert len(violations) == 1
    assert violations[0]["kind"] == "one-session-many-lanes"
    assert set(violations[0]["lanes"]) == {"A", "B"}
    assert res["A"].shared_with == ("B",)
    assert res["B"].shared_with == ("A",)


def test_dangling_binding_falls_through_and_is_refused() -> None:
    """A binding whose file is gone must NOT block the live/disk fallback (#7738),
    and must be recorded as a refused rule."""
    rows = [("L", "W")]
    pane = {"W": "deadbeef-0000-0000-0000-000000000000"}
    res, _ = fs.resolve_sessions(
        rows, pane, {}, {"L": [S_4A]}, file_exists_for(S_4A)
    )
    assert res["L"].sid == S_4A
    assert res["L"].source == fs.SOURCE_DISK
    assert res["L"].guess_refused is not None
    assert "dangling" in res["L"].guess_refused


def test_ambiguous_disk_candidates_are_unknown_not_a_guess() -> None:
    res, _ = fs.resolve_sessions(
        [("L", "W")], {}, {}, {"L": [S_4A, S_6]}, file_exists_for(S_4A, S_6)
    )
    assert res["L"].source == fs.SOURCE_UNKNOWN
    assert res["L"].sid is None
    assert "ambiguous" in res["L"].evidence


# ---------------------------------------------------------------------------
# the FREE trap
# ---------------------------------------------------------------------------

def test_tree_cpu_includes_a_grandchild() -> None:
    """pi(100) -> bash(200) -> pi -p(300). The work is in the grandchild, so a
    direct-child-only walk reads idle. This test fails on that implementation."""
    table = {100: 1, 200: 100, 300: 200}
    cpu = {100: 1.0, 200: 0.0, 300: 50.0}
    assert fs.descendants(100, table) == {200, 300}
    assert fs.tree_cpu(cpu, 100, table) == pytest.approx(51.0)
    # the direct-children-only value (the regression) is 1.0
    assert fs.tree_cpu(cpu, 100, table) != 1.0


def test_free_requires_quiet_tree_and_quiescent_pane() -> None:
    busy_tree = fs.Liveness(cpu_delta_seconds=50.0, cpu_sample_seconds=3.0,
                            descendant_count=2, pane_working=False, pane_ch_moving=False)
    assert not fs.is_genuinely_free(busy_tree, 0, 0)
    assert "descendant" in " ".join(fs.free_reasons(busy_tree, 0, 0))

    moving_pane = fs.Liveness(cpu_delta_seconds=0.0, cpu_sample_seconds=3.0,
                              descendant_count=0, pane_working=False, pane_ch_moving=True)
    assert not fs.is_genuinely_free(moving_pane, 0, 0)
    assert "not quiescent" in " ".join(fs.free_reasons(moving_pane, 0, 0))

    unmeasured = fs.Liveness()
    assert not fs.is_genuinely_free(unmeasured, 0, 0)
    assert fs.free_reasons(unmeasured, 0, 0)

    holds_work = fs.Liveness(cpu_delta_seconds=0.0, cpu_sample_seconds=3.0,
                             descendant_count=0, pane_working=False, pane_ch_moving=False)
    assert not fs.is_genuinely_free(holds_work, 1, 0)

    quiet = fs.Liveness(cpu_delta_seconds=0.0, cpu_sample_seconds=3.0,
                        descendant_count=0, pane_working=False, pane_ch_moving=False)
    assert fs.is_genuinely_free(quiet, 0, 0)


# ---------------------------------------------------------------------------
# the #7743 stall — an orphaned in-flight tool must not read as "working"
# ---------------------------------------------------------------------------

#: The measured #7743 signature (2026-10-09): parent pi alive, pane spinner
#: animating, NO descendant process backing the in-flight `task` tool, and no
#: session write for ~36 min. The turn can never end, so the lane is stalled.
WEDGED = dict(
    pid_alive=True,
    pane_working=True,
    cpu_delta_seconds=0.0,
    descendant_count=0,
    transcript_age_seconds=36 * 60.0,
)


def test_stalled_turn_requires_every_clause_of_the_conjunction() -> None:
    """Every signal ALONE is ambiguous; only their conjunction is the wedge (#7743).
    Dropping any one must clear the flag, so this fails if the detector keys on a
    single signal and would then fire on healthy lanes.

    The conjunction has SIX clauses: the three semantic signals (pane Working, no
    backing child, frozen transcript) and three fail-closed guards (the parent is
    alive, and both the descendant tree and the transcript age were MEASURED).
    """
    assert fs.Liveness(**WEDGED).pane_stalled_turn

    # 1. spinner not animating -> not the wedge (a stalled turn spins).
    assert not fs.Liveness(**{**WEDGED, "pane_working": False}).pane_stalled_turn
    # 2. a live child process backs the in-flight tool -> genuinely busy.
    assert not fs.Liveness(**{**WEDGED, "descendant_count": 1}).pane_stalled_turn
    # 3. transcript written recently -> a slow-but-progressing turn.
    assert not fs.Liveness(
        **{**WEDGED, "transcript_age_seconds": 30.0}
    ).pane_stalled_turn
    # 4. dead/unknown parent -> not a live stalled lane.
    assert not fs.Liveness(**{**WEDGED, "pid_alive": False}).pane_stalled_turn
    # 5. unmeasured transcript age is fail-closed, never a stall.
    assert not fs.Liveness(
        **{**WEDGED, "transcript_age_seconds": None}
    ).pane_stalled_turn
    # 6. an UNREADABLE process table is unmeasured, not "the child is gone":
    #    `cpu_delta_seconds is None` means the descendant tree was never read, so
    #    `descendant_count == 0` is an artifact, not evidence.
    assert not fs.Liveness(
        **{**WEDGED, "cpu_delta_seconds": None}
    ).pane_stalled_turn


def test_a_long_test_run_with_a_live_child_is_not_a_stall() -> None:
    """A lane running a long quiet tool has an OLD transcript but a LIVE child
    (descendant). It must not be flagged — that false positive is why the bound is
    a conjunction and not just "old transcript"."""
    busy = fs.Liveness(**{**WEDGED, "descendant_count": 3})
    assert not busy.pane_stalled_turn


def test_the_stall_bound_is_inclusive_at_the_threshold() -> None:
    """The bound is `>=`, so the threshold itself is pinning: a regression that
    flipped it to `>` (or moved the constant) must fail here, not pass silently."""
    at_bound = fs.Liveness(
        **{**WEDGED, "transcript_age_seconds": fs.STALLED_TURN_SECONDS}
    )
    assert at_bound.pane_stalled_turn
    just_under = fs.Liveness(
        **{**WEDGED, "transcript_age_seconds": fs.STALLED_TURN_SECONDS - 0.001}
    )
    assert not just_under.pane_stalled_turn


def test_free_reasons_surfaces_a_stalled_turn_as_recoverable() -> None:
    lv = fs.Liveness(**WEDGED, cpu_sample_seconds=3.0)
    reasons = " ".join(fs.free_reasons(lv, 0, 0))
    assert "STALLED TURN" in reasons
    assert "RECOVERABLE" in reasons
    assert "#7743" in reasons


def test_to_dict_carries_pane_stalled_turn() -> None:
    """The persisted lane record must expose the flag, or the reader commands
    (`lane`, `--json`) cannot report it."""
    assert fs.Liveness(**WEDGED).to_dict()["pane_stalled_turn"] is True
    assert fs.Liveness().to_dict()["pane_stalled_turn"] is False


# ---------------------------------------------------------------------------
# the authoritative binding field
# ---------------------------------------------------------------------------

def test_pane_bindings_reads_resume_binding_not_stale_restore_record(monkeypatch) -> None:
    ws = "01888E6B-C9FA-4AC3-BFB1-8D42C51EFDD9"
    queried: list[list[str]] = []

    def fake_run(cmd, timeout=60):
        queried.append(list(cmd))
        if cmd[:2] == ["cmux", "list-workspaces"]:
            return json.dumps([{"id": ws}])
        if cmd[:3] == ["cmux", "surface", "resume"]:
            # the measured shape: the TEXT says "No resume binding", resume_binding is
            # None, but restore_record still carries a STALE checkpoint_id.
            return json.dumps({
                "resume_binding": None,
                "restore_record": {"checkpoint_id": S_6},
            })
        return ""

    monkeypatch.setattr(fs, "_run", fake_run)
    assert fs.pane_bindings() == {}, "a stale restore_record must NOT become a binding"
    # non-vacuous: the negative result must come from actually querying the pane
    assert any(c[:3] == ["cmux", "surface", "resume"] for c in queried)


def test_pane_bindings_accepts_a_real_resume_binding(monkeypatch) -> None:
    ws = "C5E9C05B-27B3-4EF2-B68A-745C0223EAFB"

    def fake_run(cmd, timeout=60):
        if cmd[:2] == ["cmux", "list-workspaces"]:
            return json.dumps([{"id": ws}])
        if cmd[:3] == ["cmux", "surface", "resume"]:
            return json.dumps({"resume_binding": {"checkpoint_id": S_4A}, "restore_record": {}})
        return ""

    monkeypatch.setattr(fs, "_run", fake_run)
    assert fs.pane_bindings() == {ws.upper(): S_4A}


# ---------------------------------------------------------------------------
# claim attribution is MECHANICAL, never prose
# ---------------------------------------------------------------------------

def test_attribution_uses_branch_not_bare_numbers_in_prose() -> None:
    prs = [{"number": 10, "headRefName": "fix/420-thing"},
           {"number": 11, "headRefName": "unrelated"}]
    c = fs.attribute_claims(
        lane_branches=["fix/420-thing"], lane_cwd="/wt/420",
        session_text="I looked at 10 and 11 today",  # bare numbers: NOT ownership
        open_prs=prs, open_issue_numbers={420},
    )
    assert c.prs == (10,)
    assert 11 not in c.prs
    assert c.issues == (420,)


def test_attribution_accepts_an_explicit_hash_reference_in_session_text() -> None:
    prs = [{"number": 10, "headRefName": "nope"},
           {"number": 11, "headRefName": "also-nope"}]
    c = fs.attribute_claims(
        lane_branches=[], lane_cwd="/wt", session_text="this touches #11",
        open_prs=prs, open_issue_numbers=set(),
    )
    assert c.prs == (11,)
    assert "session" in c.evidence["PR 11"]


# ---------------------------------------------------------------------------
# review binding + orphans
# ---------------------------------------------------------------------------

def test_review_binding(tmp_path: Path) -> None:
    head = "a" * 40
    rec = tmp_path / "1.json"
    rec.write_text(json.dumps({"head_sha": head, "diff_sha256": "b" * 64}))
    assert fs.review_binding(1, head, record_path=rec, live_diff_sha256=None) == "AT-HEAD"
    assert fs.review_binding(1, "c" * 40, record_path=rec, live_diff_sha256="b" * 64) == "diff-match"
    assert fs.review_binding(1, "c" * 40, record_path=rec, live_diff_sha256="z" * 64) == "stale"
    assert fs.review_binding(1, head, record_path=tmp_path / "2.json", live_diff_sha256=None) == "none"


def test_orphan_report_flags_a_dead_owner_and_an_unclaimed_pr() -> None:
    prs = [{"number": 1, "headRefName": "b1", "title": "t1"},
           {"number": 2, "headRefName": "b2", "title": "t2"},
           {"number": 3, "headRefName": "b3", "title": "t3"}]
    orphans = fs.orphan_report(prs, {1: "L-dead", 2: "L-live"}, {"L-dead": False, "L-live": True})
    by_num = {o["number"]: o for o in orphans}
    assert set(by_num) == {1, 3}
    assert "no live pi" in by_num[1]["reason"]
    assert "no lane claims" in by_num[3]["reason"]


def test_orphan_report_never_calls_a_conflicted_pr_unclaimed() -> None:
    """A PR two lanes both assert is a CONFLICT, not an orphan (#7750).

    The build clears a conflicted PR's owner before orphan detection, so
    without the guard the instrument would print the exact opposite of the
    truth: "no lane claims it" for the one PR two lanes claim.
    """
    prs = [{"number": 7, "headRefName": "b", "title": "t"},
           {"number": 8, "headRefName": "b8", "title": "t8"}]
    orphans = fs.orphan_report(prs, {8: "L-live"}, {"L-live": True}, over_claimed={7})
    assert orphans == []


def test_resolve_conflicts_claims_a_contested_pr_for_nobody() -> None:
    """A conflicted PR is claimed for nobody AND is never reported as an orphan.

    This pins ``resolve_conflicts``. The CALLER (``build_state``) is pinned
    separately by ``test_build_state_pins_the_conflict_wiring_at_the_caller`` — a
    helper test alone left the caller free to drop the guard (review cycle 2).

    The guard makes the two passes ORDER-INDEPENDENT: ``orphan_report`` is told
    ``over_claimed`` whether the prune runs before or after it (verified by
    mutation — reordering alone does NOT fail this test), so no ordering
    assertion is made here; what is asserted is the OUTCOME.
    """
    def lane(label: str, prs: list[int]) -> dict:
        return {"identity": {"lane": label}, "claim": {"prs": list(prs)}}

    # The first claimant is DEAD: if the PR were ever handed to orphan_report
    # as an ordinary PR it would surface as "owner lane A has no live pi". The
    # contest must suppress that.
    lanes = [lane("A", [7, 8]), lane("B", [7])]
    owner_of_pr: dict[int, str | None] = {7: "A", 8: "A"}
    conflicts, claimants, orphans = fs.resolve_conflicts(
        lanes, [{"number": 7, "headRefName": "b7", "title": "t7"}],
        owner_of_pr, {"A": False, "B": True})
    assert conflicts == [7]
    assert claimants == {7: ["A", "B"]}
    assert orphans == []                       # 7 is contested, not ownerless
    assert 7 not in owner_of_pr                # claimed for nobody
    assert all(7 not in l_["claim"]["prs"] for l_ in lanes)
    assert 8 in lanes[0]["claim"]["prs"]       # an uncontested PR is untouched


def test_build_state_pins_the_conflict_wiring_at_the_caller(monkeypatch) -> None:
    """Pin the CALLER, not just the extracted helper (the review's P2).

    Re-inlining the conflict pass in ``build_state`` without ``over_claimed``
    reproduces #7750 while every other test stays green. This drives the real
    ``build_state`` with two lanes whose worktree branches both name PR 700.
    """
    monkeypatch.setattr(fs, "load_registry", lambda: [("A", "WS-A", ""), ("B", "WS-B", "")])
    monkeypatch.setattr(fs, "cmux_workspaces", lambda: {
        "WS-A": {"title": "A", "cwd": "/tmp/wA"},
        "WS-B": {"title": "B", "cwd": "/tmp/wB"},
    })
    monkeypatch.setattr(fs, "pane_bindings", lambda: {})
    monkeypatch.setattr(fs, "live_sessions", lambda: {})
    monkeypatch.setattr(fs, "load_overrides", lambda: {})
    monkeypatch.setattr(fs, "session_index", lambda: ({}, {}))
    monkeypatch.setattr(fs, "worktrees", lambda _root: {
        "/tmp/wA": {"branch": "refs/heads/fix/700-a"},
        "/tmp/wB": {"branch": "refs/heads/fix/700-a"},
    })
    monkeypatch.setattr(fs, "open_prs", lambda _repo, limit=400: fs.GitHubRows([
        {"number": 700, "title": "contested", "headRefName": "fix/700-a",
         "headRefOid": "", "url": "u"}], measured=True))
    monkeypatch.setattr(fs, "open_issues", lambda _repo, limit=800: fs.GitHubRows(measured=True))
    st = fs.build_state(repo="o/r", repo_root="/tmp", orch_ws="", sample_s=0.0, do_pane=False)
    assert st["conflicts"] == [700], "two lanes' branches both name PR 700"
    assert st["conflict_claimants"] == {"700": ["A", "B"]}
    assert not [o for o in st["orphans"] if o.get("number") == 700]


def test_who_names_the_claimants_instead_of_reporting_nobody(
    monkeypatch, capsys) -> None:
    """`who <contested PR>` must NOT print "no lane holds it" and exit 1.

    Both lanes asserted the PR; the conflict pass removed it from the index, so
    without the conflict branch the reader returns the opposite of the truth for
    the one PR two lanes claim (the reviewer's P2 gap).
    """
    fake = {"index": {"prs": {}, "issues": {}},
            "conflict_claimants": {"7761": ["L-a", "L-b"]},
            "lanes": []}
    monkeypatch.setattr(fs, "_load_or_build", lambda _args: fake)
    args = argparse.Namespace(number=7761, json=False)
    rc = fs.cmd_who(args)
    out = capsys.readouterr().out
    assert rc == 0, "a contested PR is a definitive answer, not 'not found'"
    assert "L-a" in out and "L-b" in out
    assert "CONTESTED" in out
    assert "no lane holds it" not in out


def test_who_reports_a_conflict_from_an_older_cache_without_lying(
    monkeypatch, capsys) -> None:
    """A pre-``conflict_claimants`` cache must not resurrect #7750 (finding 4).

    The old state has ``conflicts`` but no claimants. Saying "claimants unknown"
    is honest; saying "no lane holds it" is the exact false negative we removed.
    """
    fake = {"index": {"prs": {}, "issues": {}}, "conflicts": [7761], "lanes": []}
    monkeypatch.setattr(fs, "_load_or_build", lambda _args: fake)
    rc = fs.cmd_who(argparse.Namespace(number=7761, json=False))
    out = capsys.readouterr().out
    assert rc == 0
    assert "CONTESTED" in out and "stale" in out
    assert "no lane holds it" not in out


def test_cmd_conflicts_lists_claimants_and_the_empty_case(monkeypatch, capsys) -> None:
    """`conflicts` names the claimants; an uncontested fleet says so (finding 6)."""
    fake = {"conflicts": [7], "conflict_claimants": {"7": ["A", "B"]},
            "lanes": [
                {"identity": {"lane": "A"}, "liveness": {"liveness_measured": True},
                 "session": {"pi_pid": 1, "pid_alive": True}},
                {"identity": {"lane": "B"}, "liveness": {"liveness_measured": True},
                 "session": {"pi_pid": 2, "pid_alive": False}},
            ]}
    monkeypatch.setattr(fs, "_load_or_build", lambda _args: fake)
    assert fs.cmd_conflicts(argparse.Namespace(json=False)) == 0
    out = capsys.readouterr().out
    assert "PR 7" in out and "A (live)" in out and "B (DEAD)" in out

    monkeypatch.setattr(fs, "_load_or_build", lambda _args: {"conflicts": [], "lanes": []})
    assert fs.cmd_conflicts(argparse.Namespace(json=False)) == 0
    assert "no ownership conflicts" in capsys.readouterr().out


def test_cmd_conflicts_stale_branch_is_reachable(monkeypatch, capsys) -> None:
    """`conflicts` must render the stale-cache branch, not just `who` (finding 1).

    The message names the PR and says the detail is stale; it must NOT crash and
    must NOT omit the contested PR.
    """
    fake = {"conflicts": [7], "lanes": []}          # older cache: no claimants
    monkeypatch.setattr(fs, "_load_or_build", lambda _args: fake)
    assert fs.cmd_conflicts(argparse.Namespace(json=False)) == 0
    out = capsys.readouterr().out
    assert "PR 7" in out and "stale" in out


def test_cmd_conflicts_json_carries_liveness(monkeypatch, capsys) -> None:
    """--json must carry the claimants AND their liveness (finding 3)."""
    fake = {"conflicts": [7], "conflict_claimants": {"7": ["A", "B"]},
            "lanes": [
                {"identity": {"lane": "A"}, "liveness": {"liveness_measured": True},
                 "session": {"pi_pid": 1, "pid_alive": True}},
                {"identity": {"lane": "B"}, "liveness": {"liveness_measured": True},
                 "session": {"pi_pid": 2, "pid_alive": False}},
            ]}
    monkeypatch.setattr(fs, "_load_or_build", lambda _args: fake)
    assert fs.cmd_conflicts(argparse.Namespace(json=True)) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["7"]["claimants"] == ["A", "B"]
    assert payload["7"]["live"] == {"A": True, "B": False}


def test_who_json_conflict_carries_liveness(monkeypatch, capsys) -> None:
    fake = {"index": {"prs": {}, "issues": {}},
            "conflict_claimants": {"7": ["A"]}, "conflicts": [7],
            "lanes": [{"identity": {"lane": "A"},
                       "liveness": {"liveness_measured": True},
                       "session": {"pi_pid": 1, "pid_alive": False}}]}
    monkeypatch.setattr(fs, "_load_or_build", lambda _args: fake)
    assert fs.cmd_who(argparse.Namespace(number=7, json=True)) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["conflict"] is True and payload["lanes"] == ["A"]
    assert payload["live"] == {"A": False}


def test_conflict_map_is_shape_robust_and_takes_the_union() -> None:
    """A malformed or partial cache must never crash or drop a conflict (finding 2)."""
    # non-iterable `conflicts` and non-list claimant value: empty, not a crash
    assert fs.conflict_map({"conflicts": 3, "conflict_claimants": {"7": 5}}) == {"7": []}
    # a proper SUBSET: the union keeps PR 8 (conflicted) visible
    assert fs.conflict_map({"conflicts": [7, 8], "conflict_claimants": {"7": ["A"]}}) \
        == {"7": ["A"], "8": []}
    # non-dict `conflict_claimants` falls back to `conflicts`
    assert fs.conflict_map({"conflicts": [9], "conflict_claimants": ["x"]}) == {"9": []}


def test_lane_live_map_measured_but_sessionless_is_unmeasured() -> None:
    """No session record = no pid evidence; report untagged, never DEAD (finding 4)."""
    state = {"lanes": [
        {"identity": {"lane": "no-session"}, "liveness": {"liveness_measured": True}},
        {"identity": {"lane": "empty-session"}, "liveness": {"liveness_measured": True},
         "session": {}},
        {"identity": {"lane": "no-liveness"}},
        {"identity": {"lane": "dead"}, "liveness": {"liveness_measured": True},
         "session": {"pi_pid": 9, "pid_alive": False}},
    ]}
    assert fs.lane_live_map(state) == {
        "no-session": None, "empty-session": None, "no-liveness": None, "dead": False}


def test_lane_live_map_tolerates_a_malformed_lanes_shape() -> None:
    """A malformed cache must not crash the reader (the 'degrade, never crash' contract)."""
    assert fs.lane_live_map({"lanes": 3}) == {}
    assert fs.lane_live_map({}) == {}


def test_cmd_conflicts_survives_a_non_numeric_conflict_key(monkeypatch, capsys) -> None:
    """The union can carry a junk key; the numeric sort must not raise (cycle-4 P3)."""
    fake = {"conflicts": ["foo"], "conflict_claimants": {"7": ["A"]}, "lanes": []}
    monkeypatch.setattr(fs, "_load_or_build", lambda _args: fake)
    assert fs.cmd_conflicts(argparse.Namespace(json=False)) == 0
    out = capsys.readouterr().out
    assert "PR 7" in out and "PR foo" in out          # numeric first, junk last, no crash


def test_cmd_conflicts_json_survives_a_malformed_lanes_shape(monkeypatch, capsys) -> None:
    """`--json` must not crash when `lanes` is malformed (cycle-4 P3)."""
    fake = {"conflicts": [1], "conflict_claimants": {"1": ["A"]}, "lanes": 3}
    monkeypatch.setattr(fs, "_load_or_build", lambda _args: fake)
    assert fs.cmd_conflicts(argparse.Namespace(json=True)) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["1"] == {"claimants": ["A"], "live": {"A": None}}


def test_who_still_reports_truly_unowned_as_not_found(monkeypatch, capsys) -> None:
    """Control for the test above: truly unowned still exits 1 with 'nobody'."""
    fake = {"index": {"prs": {}, "issues": {}}, "conflict_claimants": {}}
    monkeypatch.setattr(fs, "_load_or_build", lambda _args: fake)
    rc = fs.cmd_who(argparse.Namespace(number=999999, json=False))
    assert rc == 1
    assert "no lane holds it" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# small pure helpers
# ---------------------------------------------------------------------------

def test_mangled_and_refs() -> None:
    assert fs.mangled("/a/b.c") == "--a-b-c--"
    assert fs.refs_from_slug("fix/7735-tempdir") == {7735}
    assert fs.refs_from_slug("tmp/land6260-5339") == {6260, 5339}
    assert fs.refs_from_slug("land1") == set()
    assert fs.refs_from_text("mentions 10 and #20") == {20}

def test_cpu_seconds_formats() -> None:
    assert fs.cpu_seconds("0:15.59") == pytest.approx(15.59)
    assert fs.cpu_seconds("01:02:03") == pytest.approx(3723.0)
    assert fs.cpu_seconds("2-01:00:00") == pytest.approx(2 * 86400 + 3600.0)


def test_normalizer_is_deterministic_and_drops_redundant_index() -> None:
    raw = (
        b"diff --git a/x b/x\nindex 1111111..2222222 100644\n"
        b"--- a/x\n+++ b/x\n@@ -1,3 +1,3 @@\n-a\n+b\n"
    )
    out = fs.normalize_review_diff(raw)
    assert out == fs.normalize_review_diff(raw)
    # assert the BEHAVIOUR, not a constant: the redundant index line is dropped and
    # the hunk header is rewritten; the content survives (a function that returned
    # b"" would pass a weaker `== itself` + `not in` pair).
    assert b"index 1111111" not in out
    assert b"@@ -0,3 +0,3 @@" in out
    assert b"-a\n+b" in out


def test_first_user_message_reads_a_real_session_line(tmp_path: Path) -> None:
    """Regression: ``fh.tell()`` inside a text-mode ``for line in fh`` raises on
    3.12, so the early version returned "" for every file and the `disk` source was
    unreachable. This test FAILS on that version."""
    f = tmp_path / "2026-01-01T00-00-00-000Z_abc.jsonl"
    f.write_text(
        '{"type":"assistant","content":"preamble"}\n'
        '{"type":"user","content":"Lane 6 Durability core #5011"}\n'
    )
    assert fs.first_user_message(f) == "Lane 6 Durability core #5011"


# ---------------------------------------------------------------------------
# Deliverable 2 — cmd_bind
# ---------------------------------------------------------------------------

def _state_file(tmp_path: Path, lane: dict) -> str:
    p = tmp_path / "fleet-state.json"
    p.write_text(json.dumps({"lanes": [lane], "generated_at": _stamp(0)}))
    return str(p)


def test_cmd_bind_all_binds_the_live_sid_and_validates_its_file(tmp_path, monkeypatch, capsys) -> None:
    live, resolved = "a" * 36, "b" * 36
    lane = {
        "identity": {"lane": "L", "workspace": "WS", "worktree": "/wt"},
        # binding_missing is exactly the case where live and resolved DIFFER
        "session": {"binding_missing": True, "live_session_id": live, "sid": resolved,
                    "file": "/nonexistent-resolved.jsonl"},
    }
    state = _state_file(tmp_path, lane)
    monkeypatch.setattr(fs, "session_file_for", lambda sid: Path(f"/s/{sid}.jsonl") if sid == live else None)
    rc = fs.main(["--state", state, "bind", "--all", "--dry-run", "--json"])
    out = capsys.readouterr().out
    assert rc == 0
    assert live in out, "--all must bind the LIVE session id"
    assert resolved not in out, "it must not bind the resolved id whose file does not exist"


def test_cmd_bind_refuses_a_sid_with_no_file(tmp_path, monkeypatch, capsys) -> None:
    state = _state_file(tmp_path, {"identity": {}, "session": {}})
    monkeypatch.setattr(fs, "session_file_for", lambda sid: None)
    rc = fs.main(["--state", state, "bind", "-w", "WS", "-s", "c" * 36])
    assert rc == 2, "binding a session with no file on disk must be refused (#7738)"
    assert "no session file" in capsys.readouterr().err


def test_cmd_bind_exits_nonzero_when_a_bind_fails(tmp_path, monkeypatch) -> None:
    live = "a" * 36
    lane = {"identity": {"lane": "L", "workspace": "WS", "worktree": "/wt"},
            "session": {"binding_missing": True, "live_session_id": live, "sid": live,
                        "file": "/s.jsonl"}}
    state = _state_file(tmp_path, lane)
    monkeypatch.setattr(fs, "session_file_for", lambda sid: Path("/s.jsonl"))
    monkeypatch.setattr(fs, "_run", lambda cmd, timeout=60: "Refused - access denied")
    assert fs.main(["--state", state, "bind", "--all"]) == 1


def test_cmd_bind_treats_not_ok_as_failure(tmp_path, monkeypatch) -> None:
    """Confirmation is positive: a response containing "NOT OK" is a FAILURE."""
    live = "a" * 36
    lane = {"identity": {"lane": "L", "workspace": "WS", "worktree": "/wt"},
            "session": {"binding_missing": True, "live_session_id": live, "sid": live,
                        "file": "/s.jsonl"}}
    state = _state_file(tmp_path, lane)
    monkeypatch.setattr(fs, "session_file_for", lambda sid: Path("/s.jsonl"))
    monkeypatch.setattr(fs, "_run", lambda cmd, timeout=60: "NOT OK: refused - access denied")
    assert fs.main(["--state", state, "bind", "--all"]) == 1


def test_live_sources_degrade_to_empty_on_null_json(monkeypatch) -> None:
    """A JSON `null` (a plausible nil-slice emission) must degrade, not crash."""
    monkeypatch.setattr(fs, "_run", lambda cmd, timeout=60: "null")
    assert fs.cmux_workspaces() == {}
    assert fs.live_sessions() == {}
    assert fs.pane_bindings() == {}


# ---------------------------------------------------------------------------
# #7738 — a ghost lane gets a TERMINAL verdict, never an eternal live/unmeasured one
# ---------------------------------------------------------------------------

def _fleet_build(monkeypatch, *, registry, live, measured, error="", overrides=None,
                 by_sid=None, prs=(), prs_measured=True, prs_error="",
                 issues=(), issues_measured=None, issues_error=""):
    monkeypatch.setattr(fs, "load_registry", lambda: list(registry))
    monkeypatch.setattr(
        fs, "cmux_workspaces",
        lambda: fs.WorkspaceMap(live, measured=measured, error=error),
    )
    monkeypatch.setattr(fs, "pane_bindings", lambda: {})
    monkeypatch.setattr(fs, "live_sessions", lambda: {})
    monkeypatch.setattr(fs, "load_overrides", lambda: dict(overrides or {}))
    monkeypatch.setattr(fs, "session_index", lambda: (dict(by_sid or {}), {}))
    monkeypatch.setattr(fs, "worktrees", lambda _root: {})
    monkeypatch.setattr(fs, "open_prs", lambda _repo, limit=400: fs.GitHubRows(
        prs, measured=prs_measured, error=prs_error))
    # #9392 review P2: the open-ISSUE read is its own measurement. It used to be
    # hard-wired to `prs_measured`, which made "PR read failed, issue read
    # succeeded" — the case `open_issues_read` must get right — inexpressible, so
    # no test could go red for the `None`-when-unmeasured guard.
    monkeypatch.setattr(fs, "open_issues", lambda _repo, limit=800: fs.GitHubRows(
        list(issues),
        measured=prs_measured if issues_measured is None else issues_measured,
        error=issues_error))
    return fs.build_state(repo="o/r", repo_root="/tmp", orch_ws="", sample_s=0.0, do_pane=False)


def test_ghost_workspace_is_retired_and_not_live(monkeypatch) -> None:
    st = _fleet_build(monkeypatch, registry=[("GHOST", "WS-GONE", "")],
                      live={"WS-ALIVE": {"cwd": "/tmp/a"}}, measured=True)
    lane = st["lanes"][0]
    assert lane["terminal"]["state"] == "RETIRED"
    assert lane["terminal"]["workspace_present"] is False
    assert lane["free"] is False
    assert any("no longer exists" in r for r in lane["not_free_reasons"])
    assert st["summary"]["retired"] == 1
    assert st["terminal"][0]["lane"] == "GHOST"
    assert st["reconciliation"]["workspaces_measured"] is True


def test_ghost_with_a_surviving_session_is_recoverable(monkeypatch) -> None:
    sid = "a" * 36
    st = _fleet_build(
        monkeypatch,
        registry=[("GHOST", "WS-GONE", "")],
        live={"WS-ALIVE": {"cwd": "/tmp/a"}}, measured=True,
        overrides={"GHOST": sid},
        by_sid={sid: f"/sessions/--tmp-a--/2026-01-01T00-00-00-000Z_{sid}.jsonl"},
    )
    lane = st["lanes"][0]
    assert lane["session"]["sid"] == sid            # resolved via the override rule
    assert lane["terminal"]["state"] == "RECOVERABLE"
    assert lane["terminal"]["resume_command"] == f"pi --session {sid}"
    assert sid in lane["terminal"]["reason"]
    assert st["summary"]["recoverable"] == 1


def test_a_terminal_ghost_owning_a_pr_is_reported_as_an_orphan(monkeypatch, tmp_path) -> None:
    """#7738: a terminal lane that OWNS an open PR must surface that PR as an
    orphan owned by a lane whose cmux workspace is GONE (terminal).

    The build-time ``lane_live`` map (the terminal special-case) is the only thing
    that turns a ghost-owned PR into an orphan: ``orphan_report`` reads
    ``lane_live[owner]``. Without that special-case an unmeasured ghost maps to the
    anti-false-orphan default ``True`` and holds its PR forever — the exact #7738 bug.
    """
    sid = "b" * 36
    sess = tmp_path / f"2026-01-01T00-00-00-000Z_{sid}.jsonl"
    sess.write_text(json.dumps({"type": "user", "content": "dispatch for #4242 owner"}) + "\n")
    pr = {"number": 4242, "title": "ghost-owned PR", "headRefName": "feat/ghost",
          "headRefOid": "deadbeef"}
    st = _fleet_build(
        monkeypatch,
        registry=[("GHOST", "WS-GONE", "")],
        live={"WS-ALIVE": {"cwd": "/tmp/a"}}, measured=True,
        overrides={"GHOST": sid},
        by_sid={sid: str(sess)},
        prs=[pr],
    )
    lane = st["lanes"][0]
    assert lane["terminal"]["state"] == "RECOVERABLE"
    assert 4242 in lane["claim"]["prs"], "the ghost owns the PR"
    owns = [o for o in st["orphans"] if o.get("number") == 4242]
    assert len(owns) == 1, st["orphans"]
    assert owns[0]["kind"] == "PR"
    assert owns[0]["lane"] == "GHOST"
    # The reason must state the fact that was ACTUALLY measured — the workspace is
    # gone. `lane_live` collapses "terminal" and "measured dead" into one False, so
    # "has no live pi process" here would assert a pid fact nobody read: a terminal
    # lane can carry `pid_alive: true` (that is the #7750 class this module exists to
    # stamp out). Pinning the positive AND the negative makes a revert fail.
    assert "terminal" in owns[0]["reason"] and "RECOVERABLE" in owns[0]["reason"]
    assert "no live pi process" not in owns[0]["reason"]


def test_an_unreadable_workspace_list_retires_nothing(monkeypatch) -> None:
    st = _fleet_build(
        monkeypatch, registry=[("GHOST", "WS-GONE", "")],
        live={}, measured=False, error="cmux list-workspaces returned an empty set",
    )
    lane = st["lanes"][0]
    assert lane["terminal"]["state"] == ""
    assert lane["terminal"]["workspace_present"] is None
    assert st["summary"]["terminal"] == 0
    assert st["summary"]["retired"] == 0
    rec = st["reconciliation"]
    assert rec["workspaces_measured"] is False
    assert rec["error"], "the fail-open path must SAY it failed open"


def test_a_live_workspace_is_not_terminal(monkeypatch) -> None:
    st = _fleet_build(monkeypatch, registry=[("ALIVE", "WS-ALIVE", "")],
                      live={"WS-ALIVE": {"cwd": "/tmp/a"}}, measured=True)
    assert st["lanes"][0]["terminal"] == {
        "state": "", "workspace_present": True, "reason": "", "resume_command": ""}
    assert st["summary"]["terminal"] == 0


def test_lane_live_map_calls_a_terminal_lane_dead_not_unmeasured() -> None:
    """#7738: an unmeasured lane maps to None and the caller reads None as live-safe.
    A ghost must read DEAD even with no pid evidence, or it holds a PR forever."""
    state = {"lanes": [
        {"identity": {"lane": "ghost-retired"},
         "liveness": {"liveness_measured": False},
         "terminal": {"state": "RETIRED"}},
        {"identity": {"lane": "ghost-recoverable"},
         "liveness": {"liveness_measured": True}, "session": {},
         "terminal": {"state": "RECOVERABLE"}},
        {"identity": {"lane": "unmeasured-no-terminal"},
         "liveness": {"liveness_measured": False}},
    ]}
    assert fs.lane_live_map(state) == {
        "ghost-retired": False, "ghost-recoverable": False,
        "unmeasured-no-terminal": None}


def test_workspace_map_reports_whether_the_read_was_measured(monkeypatch) -> None:
    monkeypatch.setattr(fs, "_run", lambda cmd, timeout=60: json.dumps(
        {"workspaces": [{"id": "ab", "current_directory": "/x", "custom_title": "A"}]}))
    ws = fs.cmux_workspaces()
    assert ws.measured is True and ws["AB"]["cwd"] == "/x"
    # an empty / unreadable answer is NOT "no workspaces" — it is unmeasured, and it
    # must say so, so a cmux outage can never silently retire every lane.
    for raw in ("null", "[]", ""):
        monkeypatch.setattr(fs, "_run", lambda cmd, timeout=60, _r=raw: _r)
        bad = fs.cmux_workspaces()
        assert bad.measured is False
        assert bad.error, f"unreadable answer {raw!r} must carry an error"


def test_cmd_build_says_when_the_reconciliation_failed_open(monkeypatch, capsys, tmp_path) -> None:
    """A fail-open path must SAY it failed open (#7738): silence would read as
    'no ghosts' when the truth is 'ghosts cannot be detected in this run'."""
    fake = {
        "generated_at": "", "repo": "o/r", "sample_seconds": 0, "lanes": [],
        "violations": [], "orphans": [], "terminal": [],
        "reconciliation": {"workspaces_measured": False, "error": "cmux returned nothing"},
        "conflicts": [], "conflict_claimants": {}, "index": {"prs": {}, "issues": {}},
        "summary": {"lanes": 0, "bound": 0, "unknown_binding": 0,
                    "binding_missing_but_live": 0, "genuinely_free": 0,
                    "violations": 0, "orphans": 0, "conflicts": 0,
                    "terminal": 0, "recoverable": 0, "retired": 0,
                    "workspaces_measured": False},
    }
    monkeypatch.setattr(fs, "_load_or_build", lambda _args: fake)
    out = str(tmp_path / "fleet-state.json")
    assert fs.cmd_build(argparse.Namespace(out=out, state=out, json=False)) == 0
    assert "INERT (fail-open)" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# #7883 review P2s — the read must COMPLETE, and every channel must say WHY
# ---------------------------------------------------------------------------

def test_a_failed_command_degrades_to_empty_not_to_a_confident_answer(monkeypatch) -> None:
    """`_run` discarded the exit code, so a FAILED-but-chatty command read as a
    successful answer — and for the workspace map that means a partial or stale
    list treated as the whole fleet, which retires live lanes.

    The mutation this pins: return `p.stdout` unconditionally.
    """
    class _P:
        returncode = 3
        stdout = json.dumps({"workspaces": [{"id": "ab", "current_directory": "/x"}]})

    monkeypatch.setattr(fs.subprocess, "run", lambda *a, **k: _P())
    assert fs._run(["cmux", "list-workspaces", "--json"]) == ""
    # and the caller must read that as UNMEASURED, never as "no workspaces"
    assert fs.cmux_workspaces().measured is False


def test_the_windows_TABLE_HEADER_is_not_counted_as_a_window(monkeypatch) -> None:
    """`cmux list-windows` prints a TABLE: a header row, then one row per window.

    Counting non-blank LINES reports n+1, so a healthy single-window box reads as
    "saw 2" and the reconciliation is INERT — every lane unmeasured, the collision
    pre-flight's claim gate INCOMPLETE for every candidate, and the cell dispatches
    nothing behind a full pool (measured 2026-10-18 on a live cell: `lanes=0` behind a
    198-candidate pool). The fix counts the `--json` ARRAY and, in the text fallback,
    drops the header row by matching the `ID` column heading.
    """
    payload = json.dumps({"workspaces": [{"id": "ab", "current_directory": "/x"}]})
    header = "ID                                       NAME  INDEX  FOCUSED  LAYOUT  WORKSPACE ID\n"
    one_row = "screen_abc                               -     0      true     {}      ws_abc\n"

    def run(cmd, timeout=60):
        if cmd[:2] == ["cmux", "list-windows"]:
            # text form: header + ONE data row. The old line count said 2.
            return header + one_row
        return payload

    monkeypatch.setattr(fs, "_run", run)
    assert fs.open_window_count() == 1, "the header is not a window"
    assert fs.cmux_workspaces().measured is True, "a single-window box must measure"

    # --json is preferred and counted by its array length
    def run_json(cmd, timeout=60):
        if cmd[:2] == ["cmux", "list-windows"] and "--json" in cmd:
            return json.dumps([{"id": "screen_abc"}, {"id": "screen_def"}])
        if cmd[:2] == ["cmux", "list-windows"]:
            return header + one_row
        return payload

    monkeypatch.setattr(fs, "_run", run_json)
    assert fs.open_window_count() == 2

    # non-vacuous in the other direction: TWO real windows (header + 2 rows) still refuse
    def run_two(cmd, timeout=60):
        if cmd[:2] == ["cmux", "list-windows"]:
            return header + one_row + one_row.replace("screen_abc", "screen_def")
        return payload

    monkeypatch.setattr(fs, "_run", run_two)
    assert fs.open_window_count() == 2
    assert fs.cmux_workspaces().measured is False

    # and an UNREADABLE answer is None -> unmeasured, never "no windows"
    monkeypatch.setattr(fs, "_run", lambda cmd, timeout=60: "")
    assert fs.open_window_count() is None
    assert fs.cmux_workspaces().measured is False


def test_workspace_map_fails_open_when_more_than_one_window_is_open(monkeypatch) -> None:
    """`cmux list-workspaces` lists ONE window. With a second window open every lane
    in it is merely ABSENT — and absence is exactly what makes a lane terminal — so
    a multi-window fleet is unmeasurable here, NOT an empty fleet.
    """
    payload = json.dumps({"workspaces": [{"id": "ab", "current_directory": "/x"}]})

    def two_windows(cmd, timeout=60):
        if cmd[:2] == ["cmux", "list-windows"]:
            return "  0: AAA selected_workspace=x workspaces=2\n  1: BBB selected_workspace=y workspaces=1\n"
        return payload

    monkeypatch.setattr(fs, "_run", two_windows)
    ws = fs.cmux_workspaces()
    assert ws.measured is False and ws.error, "a per-window answer is not the fleet"
    assert len(ws) == 0 and ws == {}, "it must retire nothing, not everything"

    # non-vacuous: the SAME payload measures when exactly one window is open
    monkeypatch.setattr(fs, "_run", lambda cmd, timeout=60: (
        "  0: AAA selected_workspace=x workspaces=2\n"
        if cmd[:2] == ["cmux", "list-windows"] else payload))
    assert fs.cmux_workspaces().measured is True


def test_who_index_branch_carries_liveness(monkeypatch, capsys) -> None:
    """An index entry outlives the lane that wrote it, so the index branch must say
    which holder is TERMINAL. The conflict branch already did; this one did not.
    """
    fake = {"index": {"prs": {"7": {"lanes": ["A", "B"], "evidence": {}}}, "issues": {}},
            "lanes": [
                {"identity": {"lane": "A"}, "terminal": {"state": "RETIRED"},
                 "liveness": {"liveness_measured": False}, "session": {}},
                {"identity": {"lane": "B"}, "liveness": {"liveness_measured": True},
                 "session": {"pi_pid": 1, "pid_alive": True}},
            ]}
    monkeypatch.setattr(fs, "_load_or_build", lambda _args: fake)
    assert fs.cmd_who(argparse.Namespace(number=7, json=True)) == 0
    assert json.loads(capsys.readouterr().out)["live"] == {"A": False, "B": True}

    assert fs.cmd_who(argparse.Namespace(number=7, json=False)) == 0
    text = capsys.readouterr().out
    assert "B (live)" in text, text
    assert "A (live)" not in text, "a RETIRED lane must not be presented as a live holder"


def test_cmd_bind_all_skips_terminal_lanes(tmp_path, monkeypatch, capsys) -> None:
    """A terminal lane cannot be resumed: binding it would resurrect a ghost AND
    spend a cmux round trip doing it."""
    live = "a" * 36
    lane = {"identity": {"lane": "GHOST", "workspace": "WS", "worktree": "/wt"},
            "terminal": {"state": "RETIRED"},
            "session": {"binding_missing": True, "live_session_id": live, "sid": live,
                        "file": "/s.jsonl"}}
    state = _state_file(tmp_path, lane)
    monkeypatch.setattr(fs, "session_file_for", lambda sid: Path(f"/s/{sid}.jsonl"))
    rc = fs.main(["--state", state, "bind", "--all", "--dry-run", "--json"])
    assert rc == 0
    assert live not in capsys.readouterr().out, "a terminal lane must not be a bind target"


# ---------------------------------------------------------------------------
# #7957 H1/H2/H5 — the cache's age is SELF-REPORTED and BOUNDED, and a FAILED
# board read is never an answer. The epic's headline defect, in-repo: a state
# written in the morning answered a question asked at night, with no age on it.
# ---------------------------------------------------------------------------

def test_state_age_seconds_reads_the_measured_stamp_not_the_mtime(tmp_path) -> None:
    """`generated_at` is a real measurement stamp, so it carries the age of the
    READING. The file's mtime carries no such meaning (#7159)."""
    assert fs.state_age_seconds({"generated_at": _stamp(1800)}) == pytest.approx(1800, abs=5)
    # an ABSENT, empty or garbage stamp is UNKNOWN — never "fresh"
    assert fs.state_age_seconds({}) is None
    assert fs.state_age_seconds({"generated_at": ""}) is None
    assert fs.state_age_seconds({"generated_at": "yesterday"}) is None
    assert fs.state_age_seconds({"generated_at": None}) is None


def test_state_freshness_boundary_is_the_window_not_a_hunch() -> None:
    fresh = fs.state_freshness({"generated_at": _stamp(fs.STATE_FRESH_S - 30)})
    assert fresh["stale"] is False
    assert fresh["age_seconds"] > 0

    stale = fs.state_freshness({"generated_at": _stamp(fs.STATE_FRESH_S + 30)})
    assert stale["stale"] is True
    assert "window" in stale["reason"]

    unknown = fs.state_freshness({"generated_at": ""})
    assert unknown["stale"] is True, "an unreadable stamp is never current"
    assert unknown["age_seconds"] is None and "UNKNOWN" in unknown["reason"]

    # the human rendering carries the number AND the marker
    assert fs.render_state_age(fresh).startswith("state_age=")
    assert "STALE" not in fs.render_state_age(fresh)
    assert "STALE(>900s)" in fs.render_state_age(stale)
    assert fs.render_state_age(unknown) == "state_age=? (unreadable stamp)"


def test_a_stamp_in_the_future_is_unreadable_not_the_freshest_possible_state() -> None:
    """`max(0.0, …)` reported a future stamp as age ZERO — the freshest state there
    is — so a future-dated or corrupt cache answered forever and was never rebuilt,
    which is the exact failure the age window exists to remove (#7957 H1)."""
    future = fs.state_freshness({"generated_at": _stamp(-3600)})
    assert future["age_seconds"] is None, "a future stamp is not an age of zero"
    assert future["stale"] is True, "an unknown age is never current"
    assert "UNKNOWN" in future["reason"]
    assert fs.state_age_seconds({"generated_at": _stamp(-86400)}) is None

    # ...but ordinary clock skew between the writer and this reader is NOT a future
    # stamp, and must still read as fresh — the guard is a sanity bound, not a trap.
    skewed = fs.state_freshness({"generated_at": _stamp(-5)})
    assert skewed["stale"] is False and skewed["age_seconds"] == 0.0

    assert fs.STATE_CLOCK_SKEW_S < fs.STATE_FRESH_S, "skew must not exceed the window"


def test_cmd_free_a_malformed_free_record_is_not_an_offerable_lane(tmp_path, capsys) -> None:
    """`free` is an INVITATION TO DISPATCH, so a record that merely SAYS it is free
    but cannot be read must not be offered. The guard used to cover only the flag, so
    a `{"free": true}` stub passed the filter and then raised KeyError on the fields
    below — a traceback where the caller needed "no genuinely free lane" (#7957 H4)."""
    state = _state_file(tmp_path, {"free": True})
    assert fs.main(["--state", state, "free"]) == 0
    assert fs.main(["--state", state, "free", "--json"]) == 0
    assert "no genuinely free lane" in capsys.readouterr().out


def test_write_state_json_survives_concurrent_repairs_of_one_cache(tmp_path) -> None:
    """Every reader that finds a stale cache tries to repair it, so a FIXED temp name
    is shared by concurrent writers: all but one lose `os.replace` to
    FileNotFoundError and their repair is dropped, re-paying the full rebuild on the
    next read (#7957 H2). The write must be atomic AND collision-free."""
    path = tmp_path / "state.json"
    payload = {"generated_at": "2020-01-01T00:00:00+0000", "pad": "x"}
    results: list[bool] = []

    def write() -> None:
        results.append(fs._write_state_json(path, payload))

    threads = [threading.Thread(target=write) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert all(results), f"a concurrent repair was silently dropped: {results}"
    assert json.loads(path.read_text()) == payload, "the cache was left torn"
    assert not list(tmp_path.glob("*.tmp")), "a temp file was leaked"


def test_write_state_json_does_not_narrow_the_cache_mode(tmp_path) -> None:
    """`tempfile.mkstemp` creates 0600 and `os.replace` made THAT the cache's final mode,
    silently narrowing a file that was 0644 and locking out a non-owner reader (a
    container, a group process) with no error at write time, only a failure to read later.
    The mode must be what `Path.write_text` produces — compared against a real file rather
    than a constant, so the assertion holds under any umask (#7957 H2)."""
    reference = tmp_path / "reference"
    reference.write_text("x")

    path = tmp_path / "state.json"
    assert fs._write_state_json(path, {"generated_at": _stamp(0)}) is True
    assert stat.S_IMODE(path.stat().st_mode) == stat.S_IMODE(reference.stat().st_mode)


def test_write_state_json_does_not_leave_a_cache_unreadable(tmp_path) -> None:
    """Copying an existing cache's mode verbatim perpetuates the very failure the mode
    handling exists to prevent: a 0o000 cache would be rewritten 0o000, unreadable by
    everyone including its writer, so every read rebuilds and rewrites the same unusable
    file (#7957 H2)."""
    path = tmp_path / "state.json"
    path.write_text("{}")
    os.chmod(path, 0o000)

    assert fs._write_state_json(path, {"generated_at": _stamp(0)}) is True
    assert stat.S_IMODE(path.stat().st_mode) != 0o000, "the cache must not stay unreadable"
    assert json.loads(path.read_text())["generated_at"]


def test_write_state_json_does_not_leak_the_descriptor_it_opened(tmp_path, monkeypatch) -> None:
    """`with os.fdopen(...)` must own the descriptor on EVERY path, including a
    `json.dumps` failure inside the block. A spy on `os.close` cannot see this — the
    `with` closes at the C level — so prove it with `fstat`, which fails with EBADF only
    if the descriptor was really closed."""
    opened: list[int] = []
    real_open = os.open

    def spy_open(path_: object, flags: int, mode: int = 0o777) -> int:
        fd = real_open(path_, flags, mode)
        opened.append(fd)
        return fd

    monkeypatch.setattr(fs.os, "open", spy_open)
    circular: dict[str, object] = {"generated_at": _stamp(0)}
    circular["self"] = circular

    with pytest.raises(ValueError):
        fs._write_state_json(tmp_path / "state.json", circular)

    assert opened, "the temp was never created"
    with pytest.raises(OSError):
        os.fstat(opened[0])  # EBADF: the descriptor was really closed


def test_write_state_json_closes_no_descriptor_it_does_not_own(tmp_path, monkeypatch) -> None:
    """This function must not close a descriptor by hand at all: `os.fdopen` finalises and
    closes the FileIO when a later stage of the call fails, so a second close hits
    whatever the freed number was reused for (#7957 H2: readers repair concurrently)."""
    closed: list[int] = []
    real_close = os.close

    def spy_close(fd: int) -> None:
        closed.append(fd)
        real_close(fd)

    monkeypatch.setattr(fs.os, "close", spy_close)
    circular: dict[str, object] = {"generated_at": _stamp(0)}
    circular["self"] = circular

    with pytest.raises(ValueError):
        fs._write_state_json(tmp_path / "state.json", circular)

    assert closed == [], f"the `with` owns the descriptor; it must not be closed here: {closed}"


def test_write_state_json_cleans_up_when_the_payload_is_unserialisable(tmp_path) -> None:
    """A payload `json.dumps` cannot render (a circular reference) is a CALLER bug, not an
    I/O failure, so it propagates rather than being reported as a failed cache write — but
    it must still leave neither a temp file nor a half-written cache behind, because every
    reader that finds a stale cache takes this path."""
    path = tmp_path / "state.json"
    circular: dict[str, object] = {"generated_at": _stamp(0)}
    circular["self"] = circular

    with pytest.raises(ValueError):
        fs._write_state_json(path, circular)

    assert not path.exists(), "a failed write must not fabricate a cache"
    assert not list(tmp_path.glob("*.tmp")), "a temp file was leaked"


def test_lane_json_carries_the_age_of_the_answer(tmp_path, capsys) -> None:
    """The human channel reads the stderr line; a machine consumer reading `lane
    --json` must be able to learn the age of what it was handed (#7957 H1)."""
    state = _state_file(tmp_path, {
        "identity": {"lane": "L"}, "session": {}, "liveness": {}, "claim": {},
    })
    assert fs.main(["--state", state, "lane", "L", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert "freshness" in payload
    assert payload["freshness"]["stale"] is False


def _load(args_list: list[str]):
    return fs._load_or_build(fs.build_parser().parse_args(args_list))


def _hermetic_rebuild(monkeypatch, *, lane: str = "FRESH-LANE"):
    """A `build_state` that answers without a single live source, plus the two
    resolvers `_load_or_build` calls first (which would otherwise shell out)."""
    built = {
        "generated_at": _stamp(0),
        "lanes": [{"identity": {"lane": lane}}],
        "reconciliation": {"github_measured": True, "github_error": ""},
    }
    monkeypatch.setattr(fs, "build_state", lambda **_kw: built)
    monkeypatch.setattr(fs, "resolve_repo", lambda _repo: "o/r")
    monkeypatch.setattr(fs, "resolve_repo_root", lambda _repo: "/tmp")
    return built


def test_a_stale_cache_is_rebuilt_and_repaired_not_served_as_current(
    tmp_path, monkeypatch, capsys
) -> None:
    """THE headline defect (#7957 H1/H2). A cache 4h old must not answer a question
    asked now — the served age is BOUNDED, not merely labelled — and the expired cache
    is repaired so the next reader does not pay for the same rebuild."""
    path = tmp_path / "fleet-state.json"
    path.write_text(json.dumps({
        "generated_at": _stamp(4 * 3600),
        "lanes": [{"identity": {"lane": "STALE-LANE"}}],
    }))
    _hermetic_rebuild(monkeypatch)

    out = _load(["--state", str(path), "free"])

    lanes = [lane["identity"]["lane"] for lane in out["lanes"]]
    assert lanes == ["FRESH-LANE"], "a 4h-old cache must not be served as current"
    assert out["freshness"]["stale"] is False
    on_disk = json.loads(path.read_text())
    assert on_disk["lanes"][0]["identity"]["lane"] == "FRESH-LANE", \
        "the expired cache must be repaired, or every later reader rebuilds again"
    err = capsys.readouterr().err
    assert "STALE(>900s)" in err and "rebuilding before answering" in err


def test_a_cache_with_no_readable_stamp_is_never_current(tmp_path, monkeypatch) -> None:
    """An unknown age is NOT freshness. Both shapes (absent, garbage) must rebuild."""
    _hermetic_rebuild(monkeypatch)
    for raw in ({}, {"generated_at": ""}, {"generated_at": "not-a-time"}):
        path = tmp_path / f"state-{len(json.dumps(raw))}.json"
        path.write_text(json.dumps({"lanes": [{"identity": {"lane": "UNSTAMPED"}}], **raw}))
        out = _load(["--state", str(path), "free"])
        assert [lane["identity"]["lane"] for lane in out["lanes"]] == ["FRESH-LANE"], raw


def test_allow_stale_reads_the_aged_cache_on_purpose_and_marks_it_stale(
    tmp_path, monkeypatch, capsys
) -> None:
    """The value is still PUBLISHED past the window — marked stale, never presented as
    current (the #7871 resolution: render the age, do not suppress the reading)."""
    path = tmp_path / "fleet-state.json"
    path.write_text(json.dumps({
        "generated_at": _stamp(3 * 3600),
        "lanes": [{"identity": {"lane": "AGED-LANE"}}],
    }))
    monkeypatch.setattr(fs, "build_state", lambda **_kw: pytest.fail(
        "--allow-stale must read the cache, not rebuild"))

    out = _load(["--state", str(path), "--allow-stale", "free"])

    assert [lane["identity"]["lane"] for lane in out["lanes"]] == ["AGED-LANE"]
    assert out["freshness"]["stale"] is True
    assert out["freshness"]["age_seconds"] > fs.STATE_FRESH_S
    err = capsys.readouterr().err
    assert "STALE(>900s)" in err and "NOT current" in err


def test_a_fresh_cache_is_served_without_rebuilding(tmp_path, monkeypatch, capsys) -> None:
    """Control: the rebuild is for EXPIRY, not for every read — and the age it
    answered from is still printed (H1: never inferred by hand)."""
    path = tmp_path / "fleet-state.json"
    path.write_text(json.dumps({
        "generated_at": _stamp(60),
        "lanes": [{"identity": {"lane": "CACHED-LANE"}}],
    }))
    monkeypatch.setattr(fs, "build_state", lambda **_kw: pytest.fail(
        "a fresh cache must be served, not rebuilt"))

    out = _load(["--state", str(path), "free"])

    assert [lane["identity"]["lane"] for lane in out["lanes"]] == ["CACHED-LANE"]
    assert out["freshness"]["stale"] is False
    err = capsys.readouterr().err
    assert "state_age=1m" in err and "source=cache" in err


def test_every_reader_prints_the_age_it_answered_from(tmp_path, monkeypatch, capsys) -> None:
    """`free` is the dispatch invitation; the run must say what it answered from."""
    lane = {"identity": {"lane": "L"}, "free": False, "session": {"sid": None}}
    state = _state_file(tmp_path, lane)
    assert fs.main(["--state", state, "free"]) == 0
    assert "state_age=" in capsys.readouterr().err


def test_gh_json_declares_whether_the_read_was_measured(monkeypatch) -> None:
    """"Could not read it" and "the answer is empty" are different facts (#7957 H5)."""
    monkeypatch.setattr(fs, "_capture", lambda cmd, timeout=60: ('[{"number": 1}]', 0, ""))
    rows = fs.gh_json(["pr", "list"])
    assert rows == [{"number": 1}]
    assert rows.measured is True and rows.error == ""

    monkeypatch.setattr(fs, "_capture", lambda cmd, timeout=60: ("", 0, ""))
    rows = fs.gh_json(["pr", "list"], what="gh pr list")
    assert rows == [] and rows.measured is False
    assert "no parsable JSON" in rows.error

    # a chatty failure, a null, and a non-list payload are all UNMEASURED
    for payload, expect in (("rate limit exceeded", "no parsable JSON"),
                            ("null", "NoneType")):
        monkeypatch.setattr(
            fs, "_capture", lambda cmd, timeout=60, p=payload: (p, 0, ""))
        r = fs.gh_json(["pr", "list"], what="gh pr list")
        assert r.measured is False and expect in r.error

    # a PLAIN sequence carries no provenance and is UNMEASURED — never assumed read
    assert fs.GitHubRows([{"number": 2}]).measured is False


# ── #11202: the read's FAILURE IS CLASSIFIED, not collapsed ─────────────────
#
# Every test below fails on the pre-#11202 code: `_run` degraded a timeout, a
# refused command and a corrupt body to the SAME empty string, so `gh_json`
# answered "returned no parsable JSON" for all three — the string the claim gate
# printed about a read that was merely slow, with a remedy that sent the operator
# to check auth for a 37 s read against a 60 s bound.


def test_a_timed_out_read_names_the_timeout_not_a_corrupt_payload(monkeypatch) -> None:
    """A KILL is its own class: the one failure whose answer is retry-or-widen."""
    monkeypatch.setattr(
        fs, "_capture", lambda cmd, timeout=60: ("", fs.GH_READ_TIMEOUT_RC, ""))
    rows = fs.gh_json(["issue", "list"], timeout=100, what="gh issue list")

    assert rows.measured is False          # fail-closed: never an answer
    assert "TIMED OUT after 100s" in rows.error
    assert "killed for being slow" in rows.error
    # The defect: this string used to be the WHOLE cause, for every failure class.
    assert "no parsable JSON" not in rows.error


def test_a_real_kill_reaches_the_reader_as_a_timeout(monkeypatch) -> None:
    """⛔ THE `TimeoutExpired` ARM ITSELF IS PINNED (#11202 review round 14).

    Every other case in this block stubs `_capture`, so the CLASSIFICATION could be
    deleted — `except subprocess.TimeoutExpired` folded back into the generic
    "could not be run" arm below it — and the whole suite stayed green. That fold IS
    the defect #11202 was filed for (a slow read reported as a missing binary, then
    rendered as "returned no parsable JSON"), so a revert would reinstate the bug
    with no red. Measured before this case existed: reverting only that arm gave
    `600 passed, 239 subtests passed`.

    The stub therefore sits BELOW `_capture` — `fs.subprocess` itself — so the real
    `except` clause is what the assertion observes and no `_capture` stand-in can
    satisfy it. Hermetic: no sleep, no real process.
    """
    monkeypatch.delenv(fs.GH_READ_BOUND_ENV, raising=False)

    class _KilledSubprocess:
        """A `subprocess` stand-in whose every call is a kill."""

        class TimeoutExpired(Exception):
            def __init__(self, cmd, timeout):
                super().__init__(cmd, timeout)

        @staticmethod
        def run(cmd, **kwargs):
            raise _KilledSubprocess.TimeoutExpired(cmd, kwargs.get("timeout", 1))

    monkeypatch.setattr(fs, "subprocess", _KilledSubprocess)

    out, rc, err = fs._capture(["gh", "issue", "list"], timeout=5)
    assert (out, rc) == ("", fs.GH_READ_TIMEOUT_RC)
    assert "timed out after 5s" in err
    # The other arm must NOT be what caught it.
    assert "could not be run" not in err

    rows = fs.gh_json(["issue", "list", "--limit", "2000"], timeout=100,
                      retries=1, what="gh issue list")

    assert rows.measured is False          # fail-closed: a kill is never an answer
    assert "TIMED OUT after 100s" in rows.error
    assert "no parsable JSON" not in rows.error
    assert "could not be run" not in rows.error


def test_a_refused_read_carries_the_commands_own_cause(monkeypatch) -> None:
    """A 403 says WHY. Discarding stderr is how three causes became one."""
    monkeypatch.setattr(
        fs, "_capture",
        lambda cmd, timeout=60: ("", 1, "API rate limit exceeded for user ID 1.\n"))
    rows = fs.gh_json(["issue", "list"], what="gh issue list")

    assert rows.measured is False
    assert "FAILED (1 exit" in rows.error
    assert "API rate limit exceeded" in rows.error
    assert "no parsable JSON" not in rows.error


def test_a_refusal_with_empty_stderr_never_renders_stdout_chatter(monkeypatch) -> None:
    """⛔ THE DISCRIMINATING SHAPE FOR THE STDERR-ONLY RULE (#11202 review round 8).

    The refusal arm passes stderr ONLY, because a progress line on stdout is not a
    cause. Every existing fixture gives that arm a NON-EMPTY stderr, so `_gh_said`'s
    stream ORDER alone produced the right answer and passing `stdout` in as well was
    undetectable: the mutation `_gh_said(err)` -> `_gh_said(err, out)` left the whole
    suite green. This case is the one that only the rule itself can answer — empty
    stderr WITH chatter on stdout.
    """
    monkeypatch.setattr(
        fs, "_capture", lambda cmd, timeout=60: ("Fetching page 2 of 19...\n", 1, ""))
    rows = fs.gh_json(["issue", "list"], what="gh issue list")

    assert rows.measured is False
    assert "FAILED (1 exit" in rows.error
    # The chatter must never be rendered as the CAUSE…
    assert "Fetching" not in rows.error, rows.error
    # …and the empty stderr must be reported as such, not as "either stream" (the
    # refusal arm never read stdout at all).
    assert "it printed nothing on stderr" in rows.error
    assert "either stream" not in rows.error


def test_gh_said_empty_is_stream_aware() -> None:
    """A claim about a stream nobody read is the same defect class as a wrong cause."""
    assert fs._gh_said("") == "it printed nothing on stderr"
    # A whitespace-only OTHER stream is what makes "either stream" TRUE. (A stream
    # carrying text is not empty: `_gh_said("", "body")` returns that text.)
    assert fs._gh_said("", "body") == "'body'"
    assert fs._gh_said("", "\n  \n") == "it printed nothing on either stream"
    # …and a non-empty stream still wins, stderr first.
    assert "boom" in fs._gh_said("boom", "chatter")


def test_the_timeout_retry_answers_the_measured_intermittency(monkeypatch) -> None:
    """Contention passes, so ONE retry turns a killed read into a real answer.

    The seam is deleted from the environment first: with `FLEET_STATE_GH_READ_BOUND=fixed`
    armed the retry is OFF BY DESIGN (that is the pre-#11202 path), so an ambient export
    would turn this into a false red on a correct change.
    """
    monkeypatch.delenv(fs.GH_READ_BOUND_ENV, raising=False)
    replies = [("", fs.GH_READ_TIMEOUT_RC, ""), ('[{"number": 7}]', 0, "")]
    monkeypatch.setattr(fs, "_capture", lambda cmd, timeout=60: replies.pop(0))

    rows = fs.gh_json(["issue", "list"], timeout=100, retries=1, what="gh issue list")

    assert rows.measured is True and rows == [{"number": 7}]


def test_two_timeouts_still_fail_closed(monkeypatch) -> None:
    """⛔ A RETRY MUST NOT FABRICATE AN ANSWER. Retried, still killed, still UNMEASURED."""
    monkeypatch.delenv(fs.GH_READ_BOUND_ENV, raising=False)
    calls: list[int] = []

    def capture(cmd, timeout=60):
        calls.append(timeout)
        return ("", fs.GH_READ_TIMEOUT_RC, "")

    monkeypatch.setattr(fs, "_capture", capture)
    rows = fs.gh_json(["issue", "list"], timeout=100, retries=1, what="gh issue list")

    assert rows.measured is False and len(calls) == 2
    assert "(2 attempt(s)" in rows.error and "(retried)" in rows.error


def test_a_refusal_is_not_retried_because_it_is_already_an_answer(monkeypatch) -> None:
    calls: list[list[str]] = []

    def capture(cmd, timeout=60):
        calls.append(list(cmd))
        return ("", 1, "HTTP 403: Resource not accessible")

    monkeypatch.setattr(fs, "_capture", capture)
    rows = fs.gh_json(["issue", "list"], retries=1)

    assert rows.measured is False and len(calls) == 1
    assert "Resource not accessible" in rows.error


def test_the_list_bound_scales_with_the_declared_page_count(monkeypatch) -> None:
    """A list read's cost is linear in what it asks for; a flat bound is not (#11202).

    Measured 2026-11-07: ~7 ms/row — 1.0 s at limit=100, 13.0-13.7 s at 2000 (1854
    open issues), 37.4 s on the owner's Mac, and 2.2x under four concurrent
    pre-flights. Against a flat 60 s those are simultaneously fine and fatal.

    The seam is DELETED from the environment first (review, LOW): `gh_list_timeout`
    reads ambient env, so an operator who armed `FLEET_STATE_GH_READ_BOUND=fixed` —
    exactly the slow-Mac case this change advertises — would get a RED suite on a
    correct change, and the next lane would read that as a regression.
    """
    monkeypatch.delenv(fs.GH_READ_BOUND_ENV, raising=False)
    assert fs.gh_list_timeout(2000) == 100          # 2000 rows x 50 ms
    assert fs.gh_list_timeout(400) == fs.GH_LIST_MIN_S   # small reads keep the floor
    assert fs.gh_list_timeout(0) == fs.GH_LIST_MIN_S     # no declared size -> floor
    assert fs.gh_list_timeout(2000) > fs.gh_list_timeout(400)


def test_the_off_switch_restores_the_pre_11202_path(monkeypatch) -> None:
    """`FLEET_STATE_GH_READ_BOUND=fixed` -> flat 60 s and NO retry, as before.

    What the old path does when off, stated: a read that contention slowed past 60 s
    is killed again and the board answers INCOMPLETE — fail-closed, and the cause now
    names the timeout. It selects no path and no target; the bound is arithmetic.

    ⛔ ONE ATTEMPT IS THE POINT, SO IT IS ASSERTED (review, HIGH). The first draft
    collected the attempt count and asserted only `measured is False`, while its own
    comment promised "the single attempt is what the old path did" — and the code then
    did NOT honour that, because `retries=1` at the call sites overrode the seam. A
    switch a caller can silently outvote is not a switch: `gh_json` clamps the retry
    on the seam, and this assertion is what keeps it clamped.
    """
    monkeypatch.setenv(fs.GH_READ_BOUND_ENV, "fixed")
    assert fs.gh_read_bound_enabled() is False
    assert fs.gh_list_timeout(2000) == fs.GH_LIST_MIN_S

    calls: list[int] = []

    def capture(cmd, timeout=60):
        calls.append(timeout)
        return ("", fs.GH_READ_TIMEOUT_RC, "")

    monkeypatch.setattr(fs, "_capture", capture)
    rows = fs.gh_json(["issue", "list"], timeout=fs.gh_list_timeout(2000), retries=1)
    assert rows.measured is False
    # ONE attempt at the FLAT bound — the caller's `retries=1` does not outvote the seam.
    assert calls == [fs.GH_LIST_MIN_S]
    assert "(1 attempt(s)" in rows.error and "FIXED seam" in rows.error
    assert "retried" not in rows.error


def test_the_off_switch_off_state_still_fails_closed_end_to_end(monkeypatch) -> None:
    """The switch must not become a way to make the gate answer without a read."""
    monkeypatch.setenv(fs.GH_READ_BOUND_ENV, "fixed")
    monkeypatch.setattr(
        fs, "_capture", lambda cmd, timeout=60: ("", fs.GH_READ_TIMEOUT_RC, ""))
    rows = fs.open_issues("o/r")

    assert rows.measured is False and rows == []
    assert "TIMED OUT" in rows.error



def test_the_open_issue_read_uses_the_scaled_bound(monkeypatch) -> None:
    """The expensive surface must actually RECEIVE the bound it derives (#11202)."""
    monkeypatch.delenv(fs.GH_READ_BOUND_ENV, raising=False)
    seen: dict[str, object] = {}

    def capture(cmd, timeout=60):
        seen["cmd"] = list(cmd)
        seen["timeout"] = timeout
        return ("[]", 0, "")

    monkeypatch.setattr(fs, "_capture", capture)
    assert fs.open_issues("o/r") == []
    assert seen["timeout"] == fs.gh_list_timeout(fs.OPEN_ISSUES_LIMIT)
    # The window is compared against the CONSTANT, not a literal: the bound gets
    # resized when a repo outgrows it (2000 -> 4000, 2026-11-11), and a test that
    # pinned the old number would fail on the resize while proving nothing about
    # whether the read RECEIVED it. This still fails if the read drops `--limit`.
    assert "--limit" in seen["cmd"] and str(fs.OPEN_ISSUES_LIMIT) in seen["cmd"]

    fs.open_prs("o/r")
    assert seen["timeout"] == fs.gh_list_timeout(400)



def test_build_state_records_the_board_provenance(monkeypatch) -> None:
    unread = _fleet_build(monkeypatch, registry=[], live={}, measured=True, prs=(),
                          prs_measured=False, prs_error="gh pr list timed out")
    assert unread["reconciliation"]["github_measured"] is False
    assert unread["reconciliation"]["github_error"] == "gh pr list timed out"
    assert unread["summary"]["github_measured"] is False
    assert fs.github_provenance(unread) == {"measured": False, "error": "gh pr list timed out"}

    read = _fleet_build(monkeypatch, registry=[], live={}, measured=True, prs=(),
                        prs_measured=True)
    assert read["reconciliation"]["github_measured"] is True
    assert fs.github_provenance(read) == {"measured": True, "error": ""}


def test_github_provenance_tolerates_a_pre_provenance_cache() -> None:
    """A cache written before this field existed must still be readable — only an
    EXPLICIT failure makes a reader refuse (the `conflict_map` tolerance)."""
    assert fs.github_provenance({}) == {"measured": True, "error": ""}
    assert fs.github_provenance({"reconciliation": {}}) == {"measured": True, "error": ""}
    assert fs.github_provenance({"reconciliation": {"github_measured": False,
                                                   "github_error": "x"}}) == {
        "measured": False, "error": "x"}


# ---------------------------------------------------------------------------
# the LANE ROSTER's provenance — a FAILED registry read is not an empty fleet
# ---------------------------------------------------------------------------

def test_load_registry_reports_an_unreadable_file_instead_of_an_empty_roster(tmp_path) -> None:
    """The regression: `load_registry` swallowed the OSError into `[]`, so an
    unreadable registry enumerated ZERO lanes and every consumer answered "no free
    lane" / "no lane holds it" from a read that never happened."""
    rows = fs.load_registry(tmp_path / "missing-dir" / "lane-registry.tsv")
    assert list(rows) == []
    assert rows.measured is False
    assert rows.error, "a failed read must NAME its cause"
    assert "lane-registry.tsv" in rows.error


def test_load_registry_is_measured_for_a_readable_but_empty_registry(tmp_path) -> None:
    """Control: an EMPTY registry is an ANSWER. Only a FAILED read is unmeasured —
    over-refusing here would wedge the fleet on a legitimately empty file."""
    p = tmp_path / "lane-registry.tsv"
    p.write_text("# lane-registry.tsv — label<TAB>workspace-uuid\n\n")
    rows = fs.load_registry(p)
    assert list(rows) == []
    assert rows.measured is True
    assert rows.error == ""


def test_load_registry_reads_real_rows(tmp_path) -> None:
    p = tmp_path / "lane-registry.tsv"
    p.write_text("# comment\nA\tWS-A\tlabel-a\nB\tWS-B\n")
    rows = fs.load_registry(p)
    assert list(rows) == [("A", "WS-A", "label-a"), ("B", "WS-B", "")]
    assert rows.measured is True


def test_registry_provenance_tolerates_a_pre_provenance_cache() -> None:
    """Same tolerant reading as `github_provenance`: only an EXPLICIT failure makes
    a reader refuse, so a legacy cache cannot wedge the fleet."""
    assert fs.registry_provenance({}) == {"measured": True, "error": ""}
    assert fs.registry_provenance({"reconciliation": {}}) == {"measured": True, "error": ""}
    assert fs.registry_provenance(
        {"reconciliation": {"registry_measured": False, "registry_error": "denied"}}
    ) == {"measured": False, "error": "denied"}


def test_build_state_records_the_registry_provenance(monkeypatch) -> None:
    measured_now = _fleet_build(monkeypatch, registry=[], live={}, measured=True)
    assert measured_now["reconciliation"]["registry_measured"] is True
    assert measured_now["summary"]["registry_measured"] is True

    _fleet_build(monkeypatch, registry=[], live={}, measured=True)  # re-arm the other sources
    monkeypatch.setattr(
        fs, "load_registry",
        lambda: fs.RegistryRows(measured=False, error="PermissionError: denied"),
    )
    unread = fs.build_state(repo="o/r", repo_root="/tmp", orch_ws="", sample_s=0.0,
                            do_pane=False)
    assert unread["reconciliation"]["registry_measured"] is False
    assert unread["reconciliation"]["registry_error"] == "PermissionError: denied"
    assert fs.registry_provenance(unread) == {"measured": False, "error": "PermissionError: denied"}


def _unread_registry_state(**extra):
    """The board READ, the ROSTER not: a state whose `github_measured` is True and
    whose `registry_measured` is False."""
    st = _unread_state(**extra)
    st["reconciliation"] = {
        "workspaces_measured": True, "github_measured": True, "github_error": "",
        "registry_measured": False,
        "registry_error": "FileNotFoundError: lane-registry.tsv could not be read",
    }
    return st


def test_free_refuses_when_the_registry_is_unreadable(monkeypatch, capsys) -> None:
    """`free` is an INVITATION TO DISPATCH. An unreadable roster enumerates zero
    lanes, so answering would report a fully-busy fleet from a read that failed."""
    monkeypatch.setattr(fs, "_load_or_build", lambda _args: _unread_registry_state())
    assert fs.cmd_free(argparse.Namespace(json=False)) == 2
    cap = capsys.readouterr()
    assert "UNREADABLE" in cap.err and "lane-registry" in cap.err
    assert "no genuinely free lane" not in cap.out, "an empty roster is not an answer"


@pytest.mark.parametrize(
    ("cmd_name", "args", "false_answer"),
    [
        ("who", argparse.Namespace(number=1234, json=False), "no lane holds it"),
        ("orphans", argparse.Namespace(json=False), "none"),
        ("conflicts", argparse.Namespace(json=False), "no ownership conflicts"),
        ("violations", argparse.Namespace(json=False), "none"),
    ],
)
def test_roster_derived_answers_refuse_an_unreadable_registry(
    monkeypatch, capsys, cmd_name, args, false_answer
) -> None:
    """PR #8594 review P1: EVERY roster-derived answer refuses, not only `free`.

    An unreadable registry enumerates ZERO lanes, so `index` is empty ("no lane
    holds it"), `conflict_claimants` is empty ("no ownership conflicts") and no PR
    has an owner (orphans report from the roster too). Each is the same false
    negative `_registry_readable`'s own docstring names — a lane shown work another
    lane already holds — and each was answered from a read that never happened.
    """
    monkeypatch.setattr(fs, "_load_or_build", lambda _args: _unread_registry_state())
    rc = getattr(fs, f"cmd_{cmd_name}")(args)
    cap = capsys.readouterr()
    assert rc == 2, f"{cmd_name} answered from an unread roster: {cap.out!r}"
    assert "UNREADABLE" in cap.err and "lane-registry" in cap.err, cmd_name
    assert false_answer not in cap.out, (
        f"{cmd_name} manufactured {false_answer!r} from an unread roster"
    )


def test_lane_and_bind_never_assert_absence_from_an_unreadable_registry(
    monkeypatch, capsys
) -> None:
    """PR #8594 review P2: the LAST two roster-derived readers must refuse too.

    `cmd_lane`'s `no lane matching ...` is an EXISTENCE verdict and `cmd_bind --all`'s
    `nothing to bind` is an all-clear. Both search `state["lanes"]`, which an unread
    registry leaves empty -- so both answered a negative from a read that never happened.
    This is the repro shape of the review: an unreadable registry enumerates ZERO lanes.
    """
    monkeypatch.setattr(fs, "_load_or_build", lambda _args: _unread_registry_state(lanes=[]))
    rc = fs.cmd_lane(argparse.Namespace(lane="HANG", json=False))
    cap = capsys.readouterr()
    assert rc == 2, f"`lane` answered from an unread roster: {cap.out!r}"
    assert "UNREADABLE" in cap.err and "lane-registry" in cap.err
    assert "no lane matching" not in (cap.out + cap.err), (
        "`lane` manufactured an existence verdict from an unread roster"
    )

    monkeypatch.setattr(fs, "_load_or_build", lambda _args: _unread_registry_state(lanes=[]))
    rc = fs.cmd_bind(argparse.Namespace(
        all=True, json=False, dry_run=True, workspace=None, session=None,
        label=None, cwd=None, pi=None,
    ))
    cap = capsys.readouterr()
    assert rc == 2, f"`bind --all` answered from an unread roster: {cap.out!r}"
    assert "UNREADABLE" in cap.err and "lane-registry" in cap.err
    assert "nothing to bind" not in (cap.out + cap.err), (
        "`bind --all` reported an all-clear from an unread roster"
    )


def test_lane_and_bind_still_answer_from_a_measured_empty_roster(monkeypatch, capsys) -> None:
    """OVER-FIX GUARD: a roster that was READ and has zero lanes is a real answer."""
    empty = _unread_registry_state(lanes=[])
    empty["reconciliation"]["registry_measured"] = True
    empty["reconciliation"]["registry_error"] = ""
    monkeypatch.setattr(fs, "_load_or_build", lambda _args: empty)
    rc = fs.cmd_lane(argparse.Namespace(lane="HANG", json=False))
    assert rc == 1, "a measured empty roster still means 'no such lane'"
    assert "no lane matching" in capsys.readouterr().err

    monkeypatch.setattr(fs, "_load_or_build", lambda _args: empty)
    rc = fs.cmd_bind(argparse.Namespace(
        all=True, json=False, dry_run=True, workspace=None, session=None,
        label=None, cwd=None, pi=None,
    ))
    cap = capsys.readouterr()
    assert rc == 0, f"a measured empty roster has nothing to bind: {cap.out!r}"
    assert "nothing to bind" in cap.out


def test_build_refuses_to_cache_a_state_with_an_unreadable_registry(tmp_path, monkeypatch,
                                                                   capsys) -> None:
    """A cached zero-lane state would be served as a FRESH fleet for the whole TTL."""
    fake = _unread_registry_state(
        generated_at=_stamp(0), lanes=[], terminal=[], orphans=[], violations=[],
        conflict_claimants={}, summary={},
    )
    monkeypatch.setattr(fs, "_load_or_build", lambda _args: fake)
    out = tmp_path / "fleet-state.json"
    rc = fs.cmd_build(argparse.Namespace(out=str(out), state=str(out), json=False))
    assert rc == 2, "an unread roster must not be reported as a successful build"
    assert not out.exists(), "a failed read must never be written as fact"
    err = capsys.readouterr().err
    assert "NOT CACHED" in err and "lane registry" in err


# ---------------------------------------------------------------------------
# #9392 — an ABSENT lane roster is a PROVISIONING gap, not a failed read
# ---------------------------------------------------------------------------

_ABSENT_ROSTER = (
    "FileNotFoundError: [Errno 2] No such file or directory: "
    "'/tmp/cell-home/.pi/agent/state/lane-registry.tsv'"
)
_UNREADABLE_ROSTER = (
    "PermissionError: [Errno 13] Permission denied: "
    "'/tmp/cell-home/.pi/agent/state/lane-registry.tsv'"
)


def test_an_absent_roster_names_provisioning_and_not_a_rebuild(
    tmp_path, monkeypatch, capsys
) -> None:
    """#9392: the refusal must name the cause's real OWNER.

    Measured before the fix, in a cell whose roster was never provisioned:
    `fleet_state.py build` printed only "NOT CACHED: the lane registry could not
    be read" — while `collision_preflight.py` spent the same run telling the
    reader to rebuild the board, which fails on this very read. A reader was
    sent in a loop, so the sentence has to distinguish NEVER PROVISIONED from a
    file that exists and could not be read, and say that a rebuild cannot help.
    """
    fake = _unread_registry_state()
    fake["reconciliation"]["registry_error"] = _ABSENT_ROSTER
    monkeypatch.setattr(fs, "_load_or_build", lambda _args: fake)
    out = tmp_path / "fleet-state.json"
    assert fs.cmd_build(
        argparse.Namespace(out=str(out), state=str(out), json=False)) == 2
    assert not out.exists(), "a failed read must never be written as fact"
    err = capsys.readouterr().err
    assert "NOT CACHED" in err and "lane registry" in err
    # …the actionable cause and its owner.
    assert "NEVER PROVISIONED" in err
    assert "lane-registry.tsv" in err
    assert "CANNOT create it" in err, "a rebuild must be named as no remedy"
    assert "PROVISIONING" in err and "CELL step" in err
    assert fs.REGISTRY_ROW_FORMAT in err, "name the format a row has to be in"
    # …and never the self-row shortcut the false-CLEAN class comes from.
    assert "Do NOT write a row for this lane alone" in err


def test_a_present_but_unreadable_roster_is_not_called_unprovisioned(
    tmp_path, monkeypatch, capsys
) -> None:
    """Two causes, two sentences. Sending a reader to "provision" a file whose
    real problem is its MODE would be the same defect one cause over (#9392)."""
    fake = _unread_registry_state()
    fake["reconciliation"]["registry_error"] = _UNREADABLE_ROSTER
    monkeypatch.setattr(fs, "_load_or_build", lambda _args: fake)
    out = tmp_path / "fleet-state.json"
    assert fs.cmd_build(
        argparse.Namespace(out=str(out), state=str(out), json=False)) == 2
    err = capsys.readouterr().err
    assert "EXISTS but could not be read" in err
    assert "permissions/encoding" in err
    assert "NEVER PROVISIONED" not in err


def test_registry_remedy_has_three_arms_and_never_lists_the_rebuild() -> None:
    """No recorded cause must not be guessed at — and NO arm may suggest the
    rebuild that fails on the same read (#9392)."""
    absent = fs.registry_remedy(_ABSENT_ROSTER)
    assert "NEVER PROVISIONED" in absent
    assert fs.registry_never_provisioned(_ABSENT_ROSTER) is True
    for text in ("FileNotFoundError", "No such file or directory", "FileNotFound"):
        assert fs.registry_never_provisioned(text) is True, text
    for other in (_UNREADABLE_ROSTER, "", "cause not recorded"):
        assert fs.registry_never_provisioned(other) is False, other
        assert "NEVER PROVISIONED" not in fs.registry_remedy(other)
    assert "not recorded" in fs.registry_remedy("")
    for error in (_ABSENT_ROSTER, _UNREADABLE_ROSTER, ""):
        assert "rebuild the fleet board" not in fs.registry_remedy(error).lower()


def test_other_errnos_are_not_read_as_a_missing_roster() -> None:
    """⛔ `"errno 2" in text` IS A PREFIX of errno 20/21/26/27/28 (review P2).

    So `IsADirectoryError` — the predicate's OWN docstring example of a roster
    that is PRESENT and unreadable — ENOTDIR, ENOSPC, EROFS and EMFILE were all
    classified as NEVER PROVISIONED, and the reader was sent to provision a file
    that is already there while the arm it needed ("EXISTS but could not be
    read") was withheld. The errno is matched as a TOKEN.
    """
    absent_errnos = {
        20: "NotADirectoryError: [Errno 20] Not a directory: /x/lane-registry.tsv",
        21: "IsADirectoryError: [Errno 21] Is a directory: /x/lane-registry.tsv",
        26: "OSError: [Errno 26] Text file busy: /x/lane-registry.tsv",
        27: "OSError: [Errno 27] File too large: /x/lane-registry.tsv",
        28: "OSError: [Errno 28] No space left on device: /x/lane-registry.tsv",
    }
    for errno, text in absent_errnos.items():
        with_name_and_without = (text, text.split(": ", 1)[1].lower())
        for shape in with_name_and_without:
            assert fs.registry_never_provisioned(shape) is False, shape
        assert "EXISTS but could not be read" in fs.registry_remedy(text), errno
        assert "NEVER PROVISIONED" not in fs.registry_remedy(text), errno
    # …and the cause it MUST still catch, in both shapes, is unchanged.
    for absent in (_ABSENT_ROSTER, "[errno 2] no such file or directory",
                   "errno 2"):
        assert fs.registry_never_provisioned(absent) is True, absent
        assert "NEVER PROVISIONED" in fs.registry_remedy(absent), absent


def test_the_board_publishes_the_roster_size_beside_its_provenance(monkeypatch) -> None:
    """#9392's silent half: a ONE-ROW roster is indistinguishable from a full one.

    The measured incident: a hand-written self-row turned 11 live lanes
    invisible and flipped an INCOMPLETE into a CLEAN with no code change, because
    `board source: rebuilt` describes the READ, never its coverage. The board now
    carries the counts, so a reader can print "roster rows: 1".
    """
    monkeypatch.setattr(fs, "load_registry",
                        lambda: [("cell-t3", "WS-A", "")])
    monkeypatch.setattr(fs, "cmux_workspaces", lambda: fs.WorkspaceMap(
        {f"WS-{n}": {"cwd": f"/tmp/w{n}"} for n in range(3)}, measured=True))
    monkeypatch.setattr(fs, "pane_bindings", lambda: {})
    monkeypatch.setattr(fs, "live_sessions", lambda: {})
    monkeypatch.setattr(fs, "load_overrides", lambda: {})
    monkeypatch.setattr(fs, "session_index", lambda: ({}, {}))
    monkeypatch.setattr(fs, "worktrees", lambda _root: {})
    monkeypatch.setattr(fs, "open_prs", lambda _repo, limit=400: fs.GitHubRows(
        [], measured=True))
    monkeypatch.setattr(fs, "open_issues",
                        lambda _repo, limit=fs.OPEN_ISSUES_LIMIT: fs.GitHubRows(
                            [], measured=True))
    rec = fs.build_state(repo="o/r", repo_root="/tmp", orch_ws="",
                         sample_s=0.0, do_pane=False)["reconciliation"]
    assert rec["registry_rows"] == 1, "the roster of one is visible as one"
    assert rec["workspaces_count"] == 3


def test_the_workspace_count_excludes_synthetic_process_only_lanes(monkeypatch) -> None:
    """the count is CMUX workspaces, not the `PROC:` entries the board mints.

    `register_process_lanes` MUTATES the same live map to add a `PROC:<pid>` entry
    for a lane found in the process table and not in cmux (#8999: the sanctioned
    `nohup` launch path), so `len(workspaces)` published "live cmux workspaces: 2"
    for a cmux list of ONE. That is the value the exit-2 remedy prints beside the
    roster count, and it is wrong worst exactly here: the synthetic entry is minted
    when the process's cwd is claimed by !=1 registry row — i.e. when the roster is
    missing rows, the cell case #9392 exists for — while the remedy tells the
    operator to take one uuid per lane from `cmux list-workspaces --json`, a list a
    `PROC:` entry is not in.
    """
    monkeypatch.setattr(fs, "load_registry", lambda: [("cell-t3", "WS-A", "")])
    monkeypatch.setattr(fs, "cmux_workspaces", lambda: fs.WorkspaceMap(
        {"WS-A": {"cwd": "/tmp/a"}}, measured=True))
    monkeypatch.setattr(fs, "pane_bindings", lambda: {})
    # One PROCESS-only lane: no cmux workspace, cwd not claimed by exactly one row.
    monkeypatch.setattr(fs, "live_sessions", lambda: fs.LiveSessions(
        {f"{fs.PROC_WS_PREFIX}4242": [
            {"store": fs.LIVE_STORE_PROCESS, "cwd": "/tmp/elsewhere"}]},
        measured=True))
    monkeypatch.setattr(fs, "load_overrides", lambda: {})
    monkeypatch.setattr(fs, "session_index", lambda: ({}, {}))
    monkeypatch.setattr(fs, "worktrees",
                        lambda _root: {"/tmp/elsewhere": {"branch": "fix/x"}})
    monkeypatch.setattr(fs, "open_prs", lambda _repo, limit=400: fs.GitHubRows(
        [], measured=True))
    monkeypatch.setattr(fs, "open_issues",
                        lambda _repo, limit=fs.OPEN_ISSUES_LIMIT: fs.GitHubRows(
                            [], measured=True))
    state = fs.build_state(repo="o/r", repo_root="/tmp", orch_ws="",
                           sample_s=0.0, do_pane=False)
    # …the synthetic workspace IS registered (the lane must not look terminal)…
    assert any(fs.is_proc_ws(lane["identity"]["workspace"])
               for lane in state["lanes"]), (
        "the process-only lane must still be a lane with a namespaced workspace")
    # …and the PUBLISHED COUNT is the cmux one, not the mutated map's length.
    assert state["reconciliation"]["workspaces_count"] == 1, (
        "a PROC: pseudo-workspace is not a live cmux workspace")


def test_an_unmeasured_workspace_read_publishes_no_count(monkeypatch) -> None:
    """⛔ ABSENT IS NOT ZERO, for the LIVE count too (review P1).

    EVERY failure path of `cmux_workspaces` returns an EMPTY `WorkspaceMap`, so
    `len()` beside a failed read is an artefact of the failure. Publishing it
    made the claim gate print "vs 0 live workspace(s)" for a fleet nobody asked
    successfully — the documented fail-open of #7738, where the beat cannot
    reach cmux — i.e. it asserted an empty live fleet and read every roster row
    as a ghost lane. The board must say "this read did not happen" (`None`), not
    `0`.
    """
    monkeypatch.setattr(fs, "load_registry",
                        lambda: fs.RegistryRows([("cell-t3", "WS-A", "")],
                                                measured=True))
    # The REAL fail-open shape: no workspaces AND `measured=False`.
    monkeypatch.setattr(fs, "cmux_workspaces", lambda: fs.WorkspaceMap(
        {}, measured=False, error="could not confirm a single cmux window"))
    monkeypatch.setattr(fs, "pane_bindings", lambda: {})
    monkeypatch.setattr(fs, "live_sessions", lambda: {})
    monkeypatch.setattr(fs, "load_overrides", lambda: {})
    monkeypatch.setattr(fs, "session_index", lambda: ({}, {}))
    monkeypatch.setattr(fs, "worktrees", lambda _root: {})
    monkeypatch.setattr(fs, "open_prs", lambda _repo, limit=400: fs.GitHubRows(
        [], measured=True))
    monkeypatch.setattr(fs, "open_issues",
                        lambda _repo, limit=fs.OPEN_ISSUES_LIMIT: fs.GitHubRows(
                            [], measured=True))
    rec = fs.build_state(repo="o/r", repo_root="/tmp", orch_ws="",
                         sample_s=0.0, do_pane=False)["reconciliation"]
    assert rec["workspaces_measured"] is False
    assert rec["workspaces_count"] is None, (
        "an unmeasured workspace read must not publish a count")
    # …while the MEASURED roster beside it is still published: the fix is one
    # field, not a refusal to say anything.
    assert rec["registry_rows"] == 1



def test_an_unmeasured_registry_read_publishes_no_count(monkeypatch) -> None:
    """⛔ ABSENT IS NOT ZERO, for the ROSTER count too (review P2, #9392).

    `load_registry` returns `RegistryRows(error=…)` with NO rows on any `OSError`,
    so `len()` beside `registry_measured: false` is an artefact of the failure —
    and `fleet_state.py build --json` prints that object BEFORE its refusal. The
    sibling field below already answers with `None`; publishing `0` here is the
    "roster rows: 0" false-CLEAN direction this PR exists to close, one key over
    from where it was closed.
    """
    monkeypatch.setattr(fs, "load_registry",
                        lambda: fs.RegistryRows(measured=False,
                                                error=_ABSENT_ROSTER))
    monkeypatch.setattr(fs, "cmux_workspaces", lambda: fs.WorkspaceMap(
        {"WS-A": {"cwd": "/tmp/wa"}}, measured=True))
    monkeypatch.setattr(fs, "pane_bindings", lambda: {})
    monkeypatch.setattr(fs, "live_sessions", lambda: {})
    monkeypatch.setattr(fs, "load_overrides", lambda: {})
    monkeypatch.setattr(fs, "session_index", lambda: ({}, {}))
    monkeypatch.setattr(fs, "worktrees", lambda _root: {})
    monkeypatch.setattr(fs, "open_prs", lambda _repo, limit=400: fs.GitHubRows(
        [], measured=True))
    monkeypatch.setattr(fs, "open_issues",
                        lambda _repo, limit=fs.OPEN_ISSUES_LIMIT: fs.GitHubRows(
                            [], measured=True))
    rec = fs.build_state(repo="o/r", repo_root="/tmp", orch_ws="",
                         sample_s=0.0, do_pane=False)["reconciliation"]
    assert rec["registry_measured"] is False
    assert rec["registry_rows"] is None, (
        "an unmeasured roster read must not publish a row count")
    # …while the MEASURED workspace count beside it IS still published, so the fix
    # is one field and not a blanket refusal to say anything.
    assert rec["workspaces_count"] == 1



def test_cmd_build_refuses_to_cache_an_unread_board(tmp_path, monkeypatch, capsys) -> None:
    """#7957 H5: a failed read is never CACHED. Writing it would replace the last good
    state with an empty fleet and free every lane into capacity on the next read."""
    fake = {"generated_at": _stamp(0), "lanes": [], "terminal": [], "orphans": [],
            "violations": [], "conflicts": [], "conflict_claimants": {},
            "index": {"prs": {}, "issues": {}},
            "reconciliation": {"workspaces_measured": True,
                               "github_measured": False,
                               "github_error": "gh pr list timed out"},
            "summary": {"lanes": 0, "bound": 0, "unknown_binding": 0,
                        "binding_missing_but_live": 0, "genuinely_free": 0,
                        "violations": 0, "orphans": 0, "conflicts": 0,
                        "terminal": 0, "recoverable": 0, "retired": 0,
                        "workspaces_measured": True, "github_measured": False}}
    monkeypatch.setattr(fs, "_load_or_build", lambda _args: fake)
    out = tmp_path / "fleet-state.json"
    rc = fs.cmd_build(argparse.Namespace(out=str(out), state=str(out), json=False))
    assert rc == 2, "an unread board must not be reported as a successful build"
    assert not out.exists(), "a failed read must never be written as fact"
    err = capsys.readouterr().err
    assert "NOT CACHED" in err and "gh pr list timed out" in err


def _unread_state(**extra):
    return {
        "index": {"prs": {}, "issues": {}},
        "conflicts": [],
        "lanes": [{"identity": {"lane": "BUSY-REALLY"}, "free": True,
                   "session": {"sid": "a" * 36}}],
        "orphans": [],
        "violations": [],
        "reconciliation": {"workspaces_measured": True, "github_measured": False,
                           "github_error": "gh pr list timed out"},
        **extra,
    }


def test_who_never_asserts_nobody_from_an_unread_board(monkeypatch, capsys) -> None:
    """A failed read is exactly when a claim LOOKS absent (#7957 H5)."""
    monkeypatch.setattr(fs, "_load_or_build", lambda _args: _unread_state())
    rc = fs.cmd_who(argparse.Namespace(number=999999, json=False))
    cap = capsys.readouterr()
    assert rc == 2
    assert "no lane holds it" not in cap.out
    assert "UNREADABLE" in cap.err and "gh pr list timed out" in cap.err


def test_free_refuses_to_offer_capacity_from_an_unread_board(monkeypatch, capsys) -> None:
    """`free` is an INVITATION TO DISPATCH: with the board unread, every "holds N
    PR(s)" reason disappears and a working lane reads as capacity (#7158's class)."""
    monkeypatch.setattr(fs, "_load_or_build", lambda _args: _unread_state())
    assert fs.cmd_free(argparse.Namespace(json=False)) == 2
    cap = capsys.readouterr()
    assert "BUSY-REALLY" not in cap.out, "no lane may be offered from an unread board"
    assert "UNREADABLE" in cap.err


def test_conflicts_and_orphans_refuse_an_unread_board(monkeypatch, capsys) -> None:
    """An empty list is an ALL-CLEAR; it must not be manufactured by a failed read."""
    monkeypatch.setattr(fs, "_load_or_build", lambda _args: _unread_state())
    assert fs.cmd_conflicts(argparse.Namespace(json=False)) == 2
    assert fs.cmd_orphans(argparse.Namespace(json=False)) == 2
    err = capsys.readouterr().err
    assert err.count("UNREADABLE") == 2


def test_a_measured_board_is_still_answered(monkeypatch, capsys) -> None:
    """Control against over-refusing: a MEASURED board answers exactly as before
    (the refusal is for a failure, not for an empty fleet)."""
    monkeypatch.setattr(fs, "_load_or_build", lambda _args: {
        "index": {"prs": {}, "issues": {}}, "conflicts": [], "lanes": [],
        "reconciliation": {"github_measured": True, "github_error": ""},
    })
    rc = fs.cmd_who(argparse.Namespace(number=999999, json=False))
    out = capsys.readouterr().out
    assert rc == 1 and "no lane holds it" in out
    assert fs.cmd_free(argparse.Namespace(json=False)) == 0


def test_a_lane_read_is_warned_not_suppressed_on_an_unread_board(
    monkeypatch, capsys
) -> None:
    """`lane`'s session/liveness sections are valid whatever the board says, so it
    answers — and SAYS which half is incomplete."""
    lane = {"identity": {"lane": "L", "workspace": "W", "worktree": "/wt",
                         "branch": "b", "head_sha": "d" * 40},
            "session": {"sid": "a" * 36, "source": "disk", "file": "/s.jsonl",
                        "pi_pid": 1, "pid_alive": True, "binding_missing": False},
            "liveness": {"cpu_delta_seconds": 0.0, "cpu_sample_seconds": 3.0,
                         "descendant_count": 0, "pane_working": False,
                         "pane_quiescent": True, "pane_stalled_turn": False},
            "claim": {"prs": (), "issues": (), "evidence": {}},
            "free": True, "not_free_reasons": [], "terminal": {}, "work": {"prs": []}}
    monkeypatch.setattr(fs, "_load_or_build",
                        lambda _args: _unread_state(lanes=[lane]))
    assert fs.cmd_lane(argparse.Namespace(lane="L", json=False)) == 0
    cap = capsys.readouterr()
    assert "lane      L" in cap.out
    assert "UNREADABLE" in cap.err and "incomplete" in cap.err


def test_free_never_offers_a_lane_whose_record_cannot_be_read(monkeypatch, capsys) -> None:
    """A malformed lane record is NOT free (fail-closed, the module's polarity) and the
    command whose answer is "send work here" must degrade rather than crash."""
    monkeypatch.setattr(fs, "_load_or_build", lambda _args: {
        "lanes": [{"identity": {"lane": "NO-FREE-KEY"}}, 3, None],
        "reconciliation": {"github_measured": True, "github_error": ""},
    })
    assert fs.cmd_free(argparse.Namespace(json=False)) == 0
    out = capsys.readouterr().out
    assert "NO-FREE-KEY" not in out
    assert "no genuinely free lane" in out


def test_a_json_reader_carries_the_age_in_the_payload(tmp_path, monkeypatch, capsys) -> None:
    """H1 for the MACHINE channel: the orchestrator reads JSON, so the age must be in
    the payload — not only in a stderr line it may never capture."""
    path = tmp_path / "fleet-state.json"
    path.write_text(json.dumps({
        "generated_at": _stamp(0),
        "index": {"prs": {}, "issues": {}},
        "conflict_claimants": {}, "conflicts": [], "lanes": [],
    }))
    assert fs.main(["--state", str(path), "who", "999999", "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["freshness"]["stale"] is False
    assert 0 <= payload["freshness"]["age_seconds"] < fs.STATE_FRESH_S
    assert payload["freshness"]["window_seconds"] == fs.STATE_FRESH_S


def test_payload_freshness_is_computed_from_the_state_not_trusted_from_prose() -> None:
    """A fixture (or a cache written by an older build) carries no `freshness` block;
    the payload must still state the age it can compute, and never claim a `rebuilt`
    it cannot prove."""
    stale = fs.payload_freshness({"generated_at": _stamp(3600)})
    assert stale["stale"] is True and stale["rebuilt"] is False
    assert stale["age_seconds"] == pytest.approx(3600, abs=5)

    annotated = fs.payload_freshness({
        "generated_at": _stamp(1), "freshness": {"rebuilt": True}})
    assert annotated["stale"] is False and annotated["rebuilt"] is True

    assert fs.payload_freshness(3)["stale"] is True, "a non-state is never current"


# ---------------------------------------------------------------------------
# #8175 — was the OPEN-ISSUE read WIDE ENOUGH to prove a negative?
# ---------------------------------------------------------------------------

def _issues_build(monkeypatch, rows: int):
    """Build a state whose open-issue read returned exactly `rows` rows."""
    monkeypatch.setattr(fs, "load_registry", lambda: [])
    monkeypatch.setattr(fs, "cmux_workspaces", lambda: fs.WorkspaceMap(
        {"WS": {"cwd": "/tmp/a"}}, measured=True))
    monkeypatch.setattr(fs, "pane_bindings", lambda: {})
    monkeypatch.setattr(fs, "live_sessions", lambda: {})
    monkeypatch.setattr(fs, "load_overrides", lambda: {})
    monkeypatch.setattr(fs, "session_index", lambda: ({}, {}))
    monkeypatch.setattr(fs, "worktrees", lambda _root: {})
    monkeypatch.setattr(fs, "open_prs", lambda _repo, limit=400: fs.GitHubRows(
        [], measured=True))
    monkeypatch.setattr(
        fs, "open_issues",
        lambda _repo, limit=fs.OPEN_ISSUES_LIMIT: fs.GitHubRows(
            [{"number": n + 1} for n in range(rows)], measured=True))
    return fs.build_state(repo="o/r", repo_root="/tmp", orch_ws="",
                          sample_s=0.0, do_pane=False)


def test_an_open_issue_read_at_the_cap_is_stamped_as_truncated(monkeypatch) -> None:
    """`attribute_claims` gates every attribution on membership in the open-issue
    read, so a read that hit the cap cannot support "no lane is attributed to issue
    N" for an N it never saw. Exactly-at-limit is indistinguishable from truncated,
    so it is stamped as truncated — and the board carries the numbers a refusing
    reader prints (#8175)."""
    st = _issues_build(monkeypatch, fs.OPEN_ISSUES_LIMIT)
    rec = st["reconciliation"]
    assert rec["open_issues_truncated"] is True
    assert rec["open_issues_read"] == fs.OPEN_ISSUES_LIMIT
    assert rec["open_issues_limit"] == fs.OPEN_ISSUES_LIMIT


def test_an_open_issue_read_that_failed_publishes_no_count(monkeypatch) -> None:
    """ABSENT IS NOT ZERO for the open-issue read too (#9392 review P2).

    `gh_json` returns NO rows on failure, so `len(issues_read)` beside a failed
    read publishes "0 open issues" — and `cmd_build --json` prints the object
    BEFORE its own refusal, so a reader can act on the invented zero. The guard
    exists in `build_state`; this pins it, because a guard nothing can make red
    is not a gate.
    """
    st = _fleet_build(monkeypatch, registry=[], live={}, measured=True,
                      issues=[{"number": 1}, {"number": 2}, {"number": 3}],
                      issues_measured=False,
                      issues_error="gh: Could not resolve host: api.github.com")
    rec = st["reconciliation"]
    assert rec["open_issues_read"] is None, (
        "a failed issue read must publish NO count, not the empty list's length"
    )


def test_a_measured_issue_read_publishes_its_count_even_when_the_pr_read_failed(
        monkeypatch) -> None:
    """The distinguishing case: the ISSUE read succeeded while the PR read did
    not. Gating the count on `github_measured` (the AND) would discard a real
    measurement — the wrong fix this pins against (#9392 review P2)."""
    st = _fleet_build(monkeypatch, registry=[], live={}, measured=True,
                      prs_measured=False, prs_error="gh: HTTP 502",
                      issues=[{"number": 1}, {"number": 2}, {"number": 3}],
                      issues_measured=True)
    rec = st["reconciliation"]
    assert rec["github_measured"] is False, "the PR read failed"
    assert rec["open_issues_read"] == 3, (
        "the issue read DID happen, so its count is a measurement and must be published"
    )


def test_an_open_issue_read_below_the_cap_is_not_truncated(monkeypatch) -> None:
    """The normal case (3 open issues against the window):
    absence from `index.issues` IS a measured "nobody", so the flag must be exactly
    False — not merely absent — because `collision_preflight._board_can_answer`
    refuses a board that does not stamp its coverage."""
    st = _issues_build(monkeypatch, 3)
    rec = st["reconciliation"]
    assert rec["open_issues_truncated"] is False
    assert rec["open_issues_read"] == 3
    assert rec["open_issues_limit"] == fs.OPEN_ISSUES_LIMIT


def test_the_open_issue_window_clears_the_largest_verified_crossing(monkeypatch) -> None:
    """A window at or below the coverage it was last verified to have re-creates the
    #11872 outage, and the suite must RED on it (#11917).

    The only floor before this was a literal from 2026-10-14 (989) while the repo
    passed 2005 on 2026-11-11, so any window in `(989, 2005]` — 2000 included —
    satisfied the suite while making `open_issues_truncated` True for every real
    board, which turns the pre-flight's *absent issue* surface into `INCOMPLETE
    (exit 2)` for every issue nobody has taken yet. That is not hypothetical: the
    revert reached main's shape once through a merge resolution on a PR that never
    touched this file (#11839), and every check stayed green.

    ⛔ WHAT THIS GUARD CAN AND CANNOT PROVE — say it plainly, because the weaker
    claim is the true one. `issues_truncated = len(rows) >= OPEN_ISSUES_LIMIT`, so a
    window EQUAL to a count SATURATES at it: the relation must be STRICT, and the
    second assertion exercises the boundary from the saturated side. What that buys
    is "the window was not silently shrunk below the coverage it was last verified
    to have". It does NOT buy headroom: the recorded crossing is a SNAPSHOT the repo
    has since grown past, so a window sized to today's count still passes. That is a
    limit of any committed constant, not something this test can close — which is
    why the window itself carries ~2x (`tools/fleet_state.py`: "a window sized just
    above today's count would re-cliff within days") rather than being sized to the
    crossing.
    """
    assert fs.OPEN_ISSUES_LIMIT > fs.OPEN_ISSUES_WINDOW_RECORDED_CROSSING, (
        "the window must exceed the largest open-issue count it was verified "
        f"against ({fs.OPEN_ISSUES_LIMIT} <= "
        f"{fs.OPEN_ISSUES_WINDOW_RECORDED_CROSSING}) — a window equal to a count "
        "saturates at it, truncating every board and refusing every absent issue"
    )
    # The boundary, from the saturated side: pin the window TO the crossing and the
    # same-size read saturates — that is the off-by-one a `>=` comparison would bless.
    monkeypatch.setattr(fs, "OPEN_ISSUES_LIMIT", fs.OPEN_ISSUES_WINDOW_RECORDED_CROSSING)
    saturated = _issues_build(monkeypatch, fs.OPEN_ISSUES_WINDOW_RECORDED_CROSSING)
    assert saturated["reconciliation"]["open_issues_truncated"] is True, (
        "a window EQUAL to the read's size reported not-truncated: `>=` is the "
        "saturation test, so a window pinned to a count saturates at that count"
    )
    # Drop the pinned window before the positive case below, or it measures the pin.
    monkeypatch.undo()
    # …and the board answers at the size it was last verified to cover.
    st = _issues_build(monkeypatch, fs.OPEN_ISSUES_WINDOW_RECORDED_CROSSING)
    rec = st["reconciliation"]
    assert rec["open_issues_truncated"] is False, (
        "a read the size of the last verified crossing saturated the window: "
        f"{rec['open_issues_read']} issue(s) at limit {rec['open_issues_limit']} — "
        "the pre-flight would refuse every unattributed issue"
    )
    assert rec["open_issues_read"] == fs.OPEN_ISSUES_WINDOW_RECORDED_CROSSING



# ---------------------------------------------------------------------------
# #8999 — the live-session reader: two cmux grammars, and the lane no cmux
#          store can see at all (the cell launch path)
# ---------------------------------------------------------------------------

CELL_CWD = "/tmp/cell-t3/wt-8999"


def _proc_live(pid: int, cwd: str = CELL_CWD, **extra) -> dict:
    """A process-store record exactly as ``live_sessions`` emits one."""
    rec = {
        "agent": "pi",
        "workspace_id": f"{fs.PROC_WS_PREFIX}{pid}",
        "pid": pid,
        "cwd": cwd,
        "session_id": None,
        "agent_lifecycle": "running",
        "store": fs.LIVE_STORE_PROCESS,
    }
    rec.update(extra)
    return rec


def _cell_fleet(monkeypatch, *, live, registry=(), wts=None, issues=(8999,),
                prs=(), workspaces=None, session_index=({}, {})):
    """A cell-shaped board: no cmux workspace list, no registry, one worktree."""
    monkeypatch.setattr(fs, "load_registry", lambda: list(registry))
    monkeypatch.setattr(fs, "cmux_workspaces", lambda: (
        fs.WorkspaceMap(error="cmux: command not found")
        if workspaces is None else workspaces
    ))
    monkeypatch.setattr(fs, "pane_bindings", lambda: {})
    monkeypatch.setattr(fs, "live_sessions", lambda: live)
    monkeypatch.setattr(fs, "load_overrides", lambda: {})
    monkeypatch.setattr(fs, "session_index", lambda: session_index)
    # The fixture's pids are not real processes on the machine running the suite; the
    # pid READ is what `ps` measured, so it is an input here, never an `os.kill` probe.
    alive = {r.get("pid") for recs in live.values() for r in recs if r.get("pid")}
    monkeypatch.setattr(fs, "pid_alive", lambda pid: pid in alive)
    monkeypatch.setattr(fs, "worktrees", lambda _root: (
        {CELL_CWD: {"branch": "refs/heads/feat/8999-tm", "head": "a" * 40}}
        if wts is None else wts
    ))
    monkeypatch.setattr(fs, "open_prs", lambda _repo, limit=400: fs.GitHubRows(
        list(prs), measured=True))
    monkeypatch.setattr(
        fs, "open_issues",
        lambda _repo, limit=fs.OPEN_ISSUES_LIMIT: fs.GitHubRows(
            [{"number": n} for n in issues], measured=True))
    return fs.build_state(repo="o/r", repo_root="/tmp", orch_ws="",
                          sample_s=0.0, do_pane=False)


def test_the_board_publishes_which_liveness_stores_were_unreadable(
        monkeypatch) -> None:
    """#11202: the board's RECONCILIATION block must carry the store groups that did
    not answer, beside the one that did.

    Measured on a cell: the board answered `process-table` while both cmux grammars
    refused, and every surface printed `board source: rebuilt` — a CLEAN resting on
    one store that read exactly like a CLEAN that read them all. The field is the
    datum the pre-flight's claim-gate note publishes (#11202).
    """
    live = fs.LiveSessions(
        {f"{fs.PROC_WS_PREFIX}4242": [_proc_live(4242)]},
        measured=True, source=fs.LIVE_STORE_PROCESS,
        error="cmux session store: `cmux sessions list --json --all` exited 2: "
              "'usage.invalid: --all needs a value'",
        unreadable=(fs.LIVE_STORE_CMUX_GROUP,),
    )
    st = _cell_fleet(monkeypatch, live=live)
    rec = st["reconciliation"]
    assert rec["liveness_measured"] is True
    assert rec["liveness_source"] == fs.LIVE_STORE_PROCESS
    assert rec["liveness_unreadable"] == [fs.LIVE_STORE_CMUX_GROUP], (
        "a JSON list on the wire, naming the store that did not answer")
    # ABSENT IS NOT EMPTY: a plain dict (a fixture, an older caller) declares no
    # store, so the board says nothing rather than "everything was read".
    st = _cell_fleet(monkeypatch, live={})
    assert st["reconciliation"]["liveness_unreadable"] == []


def test_a_cell_lane_started_by_nohup_is_a_lane_the_board_can_answer_for(
        monkeypatch) -> None:
    """THE #8999 DEFECT, end to end. The cell launch path starts a lane as a
    ``nohup`` child of a shell, so no cmux store lists it and no registry row names
    it. Before this change the board enumerated zero lanes, published a
    `liveness-unmeasured` orphan, and `collision_preflight` answered INCOMPLETE
    (exit 2) for EVERY issue on the box — a gate that can never authorise anything.

    The process record must become a LANE (not just a liveness reading), or the gate
    moves from "unmeasured" to a false "nobody holds it": the fail-open the
    `_board_can_answer` docstring exists to prevent.
    """
    live = fs.LiveSessions(
        {f"{fs.PROC_WS_PREFIX}4242": [_proc_live(4242)]},
        measured=True, source=fs.LIVE_STORE_PROCESS,
    )
    st = _cell_fleet(monkeypatch, live=live)
    proc = [lane_ for lane_ in st["lanes"]
            if lane_["identity"]["workspace"].startswith(fs.PROC_WS_PREFIX)]
    assert len(proc) == 1, "the process must be enumerated as a lane, not only as a pid"
    lane = proc[0]
    assert lane["session"]["pid_alive"] is True
    assert lane["liveness"]["liveness_measured"] is True
    assert lane["identity"]["branch"] == "feat/8999-tm"
    # … and the issue the worktree names is attributed to it, so the claim gate has
    # an answer to read instead of an absence.
    assert st["index"]["issues"]["8999"]["lanes"] == [lane["identity"]["lane"]]
    assert not [o for o in st["orphans"] if o.get("kind") == "liveness-unmeasured"]
    rec = st["reconciliation"]
    assert rec["liveness_measured"] is True
    assert rec["liveness_source"] == fs.LIVE_STORE_PROCESS
    assert rec["process_lanes"] == [f"{fs.PROC_WS_PREFIX}4242"]


def test_a_process_lane_never_reads_terminal_and_never_asks_cmux_for_a_pane(
        monkeypatch) -> None:
    """A synthetic workspace is not a CMUX workspace, and two things must not treat
    it as one: the workspace-existence reconciliation (which would RETIRE a lane
    that is running right now) and the pane reader (which would ask cmux about a
    workspace it never created, and report the resulting empty read as a quiet
    pane)."""
    asked: list[str] = []
    monkeypatch.setattr(fs, "pane_screen", lambda ws: asked.append(ws) or "")
    assert fs.pane_state(f"{fs.PROC_WS_PREFIX}7") == {}
    assert asked == [], "cmux must not be asked about a synthetic workspace"
    live = fs.LiveSessions(
        {f"{fs.PROC_WS_PREFIX}4242": [_proc_live(4242)]},
        measured=True, source=fs.LIVE_STORE_PROCESS,
    )
    # A MEASURED workspace list that cannot contain the synthetic id must still not
    # retire the lane: the synthetic workspace is registered as present.
    st = _cell_fleet(monkeypatch, live=live, workspaces=fs.WorkspaceMap(
        {"WS-A": {"cwd": "/tmp/other"}}, measured=True))
    lane = next(lane_ for lane_ in st["lanes"]
                if lane_["identity"]["workspace"].startswith(fs.PROC_WS_PREFIX))
    assert lane["terminal"]["state"] == "", "a live process lane is not a ghost"
    assert lane["terminal"]["workspace_present"] is True


def test_one_worktree_is_one_lane_even_with_several_pi_processes(monkeypatch) -> None:
    """A ``task`` child is itself a ``pi`` in its parent's cwd, and a respawned lane
    overlaps its predecessor. Emitting one lane per PID would attribute one lane's
    issue TWICE, and `_holders_from_board` reads two recorded lanes for one label as
    unreadable — turning a readable lane into exit 2."""
    live = fs.LiveSessions(
        {f"{fs.PROC_WS_PREFIX}4243": [_proc_live(4242), _proc_live(4243)]},
        measured=True, source=fs.LIVE_STORE_PROCESS)
    kept, proc_ws = fs.register_process_lanes(
        live, {}, [], {CELL_CWD: {"branch": "refs/heads/feat/8999-tm"}})
    assert len(kept) == 1
    assert len(proc_ws) == 1


def test_live_sessions_collapses_processes_by_cwd(monkeypatch) -> None:
    """The collapse rule, at the reader: two pids in one cwd are ONE lane record set
    (one workspace key), so the lane join sees every pid but counts one lane."""
    monkeypatch.setattr(fs, "cmux_session_records", lambda: ([], "", "no cmux"))
    monkeypatch.setattr(fs, "pi_processes", lambda: (
        [{"pid": 10, "ppid": 1}, {"pid": 11, "ppid": 10}], ""))
    monkeypatch.setattr(fs, "process_cwds", lambda pids: (
        {10: "/tmp/wt-a", 11: "/tmp/wt-a"}, ""))
    live = fs.live_sessions()
    assert live.measured is True
    assert list(live) == [f"{fs.PROC_WS_PREFIX}11"], "the newest pid names the lane"
    assert [r["pid"] for r in live[f"{fs.PROC_WS_PREFIX}11"]] == [10, 11]


def test_an_unreadable_everything_stays_unmeasured(monkeypatch) -> None:
    """THE FAIL-CLOSED POLARITY (#8999 item 2). When no store can be read the board
    must still publish `liveness-unmeasured` and the gate must still refuse — an
    unmeasured pid is never a dead owner, and "nobody holds it" may never come from
    a read that did not happen."""
    monkeypatch.setattr(fs, "cmux_session_records", lambda: (
        [], "", "`cmux sessions list --json --all` produced nothing"))
    monkeypatch.setattr(fs, "pi_processes", lambda: (
        [], "`ps -axo pid=,ppid=,uid=,command=` produced nothing"))
    live = fs.live_sessions()
    assert live.measured is False
    assert live.source == ""
    assert "produced nothing" in live.error
    st = _cell_fleet(monkeypatch, live=live)
    assert [o["kind"] for o in st["orphans"] if o["kind"] == "liveness-unmeasured"]
    assert st["reconciliation"]["liveness_measured"] is False


def test_a_live_pi_whose_cwd_cannot_be_read_is_unmeasured_not_dropped(
        monkeypatch) -> None:
    """The cwd IS the attribution (worktree -> branch -> issue), so a live process
    with no readable cwd cannot rule OUT a holder. Dropping it silently would let the
    gate answer "nobody holds it" while that very process may hold the issue."""
    monkeypatch.setattr(fs, "cmux_session_records", lambda: ([], "", "no cmux"))
    monkeypatch.setattr(fs, "pi_processes", lambda: ([{"pid": 5, "ppid": 1}], ""))
    monkeypatch.setattr(fs, "process_cwds", lambda pids: ({}, "`lsof` could not be run"))
    live = fs.live_sessions()
    assert live.measured is False
    assert "lsof" in live.error


def test_a_process_that_exited_before_its_cwd_was_read_is_not_a_gap(monkeypatch) -> None:
    """A pid can vanish between ``ps`` and the cwd read. That is not a measurement
    gap — it is not running — so it must not hold the whole store unmeasured
    (which on a busy fleet would be red forever)."""
    monkeypatch.setattr(fs, "cmux_session_records", lambda: ([], "", "no cmux"))
    monkeypatch.setattr(fs, "pi_processes", lambda: (
        [{"pid": 6, "ppid": 1}, {"pid": 7, "ppid": 1}], ""))
    monkeypatch.setattr(fs, "process_cwds", lambda pids: ({7: "/tmp/wt-a"}, ""))
    monkeypatch.setattr(fs, "pid_alive", lambda pid: pid == 7)
    live = fs.live_sessions()
    assert live.measured is True, "the exited pid is not a gap"
    assert list(live) == [f"{fs.PROC_WS_PREFIX}7"]


def test_the_same_gap_is_closed_on_the_batched_lsof_reader(monkeypatch) -> None:
    """THE SAME INVARIANT ON THE MACOS PATH, which is where it was BROKEN.

    ``lsof`` prints NOTHING when every pid it was handed has gone. The reader used
    to turn that empty answer into a store-level error, so ONE vanished process
    promoted the whole process store to unreadable — and on a box whose cmux store
    also fails that is `liveness-unmeasured` for EVERY issue, forever: the permanent
    red gate #8999 exists to remove. The `/proc` path already judged this per pid
    (`test_a_process_that_exited_before_its_cwd_was_read_is_not_a_gap`), so the two
    readers must agree.

    ⛔ AND THE FAIL-CLOSED DIRECTION SURVIVES: the second half of this test is the
    same empty ``lsof`` answer with the pid still ALIVE, which must stay unmeasured.
    """
    import subprocess as sp
    import unittest.mock as mock

    class _P:
        returncode = 1          # lsof's "some pids not found" status
        stdout = ""             # nothing to report: every pid is gone
        stderr = "lsof: no pwd entry for the given pid\n"

    monkeypatch.setattr(fs, "_proc_cwd_is_readable", lambda: False)
    monkeypatch.setattr(fs, "cmux_session_records", lambda: ([], "", "no cmux"))
    monkeypatch.setattr(fs, "pi_processes", lambda: ([{"pid": 8, "ppid": 1}], ""))
    # ⛔ THE MOCK MUST COVER *BOTH* HALVES. It used to close before the second
    # `live_sessions()`, which therefore reached the HOST's real ``lsof`` and
    # asserted against the host's process table — it failed on any box where pid 8
    # exists and lsof answers for it (measured, 2026-10-19: a Cloud cell printed
    # `n/proc/8/cwd (readlink: Permission denied)`). Hermetic means every read in
    # the test is a pure input.
    with mock.patch.object(sp, "run", lambda *a, **k: _P()):
        cwds, err = fs.process_cwds([8])
        assert cwds == {} and err == "", (
            "a `lsof` that RAN and reported no cwd is an answer, not a failure")

        monkeypatch.setattr(fs, "pid_alive", lambda pid: False)
        live = fs.live_sessions()
        assert live.measured is True, "the exited pid is not a gap here either"
        assert dict(live) == {}

        # ⛔ THE CONTRAST: the pid is ALIVE, so the empty answer cannot rule it out.
        monkeypatch.setattr(fs, "pid_alive", lambda pid: pid == 8)
        live = fs.live_sessions()
    assert live.measured is False, "a live pid with no cwd is still a gap"
    assert "8" in live.error


def test_a_failed_lsof_invocation_is_still_a_gap(monkeypatch) -> None:
    """The other side of the same distinction: a command that could not RUN (or
    exited with a status that means it never did its job) is a real failure, and it
    must NOT be laundered into a measured "nobody" by the empty-stdout rule."""
    import subprocess as sp
    import unittest.mock as mock

    class _Broken:
        returncode = 2          # did not do its job
        stdout = ""
        stderr = "lsof: illegal option\n"

    monkeypatch.setattr(fs, "_proc_cwd_is_readable", lambda: False)
    with mock.patch.object(sp, "run", lambda *a, **k: _Broken()):
        cwds, err = fs.process_cwds([9])
    assert cwds == {} and "illegal option" in err

    # … and a `lsof` that is not installed at all.
    def _boom(*a, **k):
        raise FileNotFoundError("lsof")

    with mock.patch.object(sp, "run", _boom):
        cwds, err = fs.process_cwds([9])
    assert cwds == {} and "could not be run" in err


def test_a_placeholder_cwd_is_unresolved_not_a_lane(monkeypatch) -> None:
    """⛔ `lsof` DOES NOT FAIL WHEN IT CANNOT FOLLOW A PID'S CWD — it prints a
    PLACEHOLDER, and the kernel appends `` (deleted)`` to a removed directory.

    Both strings are not paths. Accepted as a cwd they match no worktree, so
    `register_process_lanes` drops the pid as another repo's lane while the store
    still reads MEASURED — the gate may then answer "nobody holds it" about a live
    `pi` the process table DID see. Measured on a Cloud cell (2026-10-19):

        $ lsof -a -d cwd -Fn -p 8     ->  n/proc/8/cwd (readlink: Permission denied)

    So an unusable string is UNRESOLVED, and the per-pid judgement decides: live
    keeps the store unmeasured, exited is not a gap.
    """
    import subprocess as sp
    import unittest.mock as mock

    placeholder = "/proc/8/cwd (readlink: Permission denied)"

    class _Placeholder:
        returncode = 0
        stdout = f"p8\nn{placeholder}\n"
        stderr = ""

    monkeypatch.setattr(fs, "_proc_cwd_is_readable", lambda: False)
    with mock.patch.object(sp, "run", lambda *a, **k: _Placeholder()):
        cwds, err = fs.process_cwds([8])
    assert cwds == {} and err == "", (
        "lsof's placeholder is not a path — it must read as UNRESOLVED")
    # ⛔ AND THE RAW DATUM TRAVELS OUT OF THE `lsof` BRANCH TOO (review of #2401). The
    # macOS reader is the one the Mac fleet runs, and deleting its `refused[cur] = raw`
    # left this suite green while that fleet reverted to "no cwd could be read".
    assert cwds.refused == {8: placeholder}, (
        "the lsof reader must carry the rejected string, not only drop it")

    # The same rule on the `/proc` reader: a removed directory.
    monkeypatch.setattr(fs, "_proc_cwd_is_readable", lambda: True)
    with mock.patch.object(fs.os, "readlink", lambda _p: "/wt/8999 (deleted)"):
        cwds, err = fs.process_cwds([8])
    assert cwds == {} and err == "", "` (deleted)` is an unresolved cwd too"

    # … and it reaches the store's polarity, which is the point: the pid is never
    # published as a lane under a path that attributes to nothing.
    monkeypatch.setattr(fs, "cmux_session_records", lambda: ([], "", "no cmux"))
    monkeypatch.setattr(fs, "pi_processes", lambda: ([{"pid": 8, "ppid": 1}], ""))
    with mock.patch.object(fs.os, "readlink", lambda _p: placeholder):
        monkeypatch.setattr(fs, "pid_alive", lambda pid: True)
        live = fs.live_sessions()
        assert live.measured is False, "a live pid with no usable cwd is still a gap"
        assert dict(live) == {}, "no lane is minted from a placeholder"
        assert "8" in live.error

        monkeypatch.setattr(fs, "pid_alive", lambda pid: False)
        live = fs.live_sessions()
    assert live.measured is True and dict(live) == {}, "an exited pid is not a gap"


def test_a_rejected_cwd_carries_its_raw_datum_out_of_the_reader(
        monkeypatch) -> None:
    """A REJECTED CWD IS A DATUM, NOT AN ABSENCE (measured 2026-10-24, cell-4).

    Two live lanes had their worktrees reaped under them, so the kernel answered
    ``/proc/<pid>/cwd`` → ``/tmp/cell-wt-adv-runnerprov-land (deleted)``. The
    reader rejects that string — correct, it attributes to no worktree — and then
    reported the refusal as "no cwd could be read", which is FALSE and threw away
    the only thing that identifies the orphan: the path. A reader that holds the
    datum and reports an absence is the class this pins.
    """
    monkeypatch.setattr(fs, "_proc_cwd_is_readable", lambda: True)
    raw = "/tmp/cell-wt-adv-runnerprov-land (deleted)"
    monkeypatch.setattr(fs.os, "readlink", lambda _p: raw)
    cwds, err = fs.process_cwds([2510056])
    assert dict(cwds) == {} and err == "", "still unresolved — fail-closed"
    assert cwds.refused == {2510056: raw}, (
        "the raw string travels with the answer instead of being dropped")

    # … and an EXITED pid leaves no refusal behind: nothing was measured, so
    # there is nothing to name (the per-pid judgement decides, as before).
    def _gone(_p):
        raise FileNotFoundError("no such process")

    monkeypatch.setattr(fs.os, "readlink", _gone)
    cwds, _err = fs.process_cwds([2510056])
    assert cwds.refused == {}


def test_a_reaped_worktree_is_named_in_the_gap_not_hidden(monkeypatch) -> None:
    """THE CELL-WIDE ZERO, in one assertion.

    A live pid whose cwd names a REAPED worktree still leaves the store UNMEASURED
    (the wording never becomes "measured" — #8999's fail-closed polarity is
    untouched). What changes is that the gap now says WHICH pid ran WHERE, so the
    refusal is one line an operator can act on instead of "no cwd could be read"
    — which named neither the pid nor the datum, and zeroed a cell's whole queue
    for 12+ hours.
    """
    raw = "/tmp/cell-wt-adv-reaping (deleted)"

    class _Cwds(dict):
        pass

    cwds = _Cwds()
    # AN INSTANCE ATTRIBUTE, NOT A CLASS ONE: `refused` is a per-test DATUM (one pid → the path
    # that pid refused), not a constant every instance of `_Cwds` shares, and an instance
    # attribute says so at the point the fixture is built. This is intent, not a gate: the
    # `main` version was `ClassVar[dict[int, str]]`, which is lint-clean too — `ClassVar` is
    # exactly what RUF012 asks for — so nothing here reds `ruff check .` either way.
    cwds.refused = {2559943: raw}

    monkeypatch.setattr(fs, "cmux_session_records", lambda: ([], "", "no cmux"))
    monkeypatch.setattr(fs, "pi_processes", lambda: ([{"pid": 2559943, "ppid": 1}], ""))
    monkeypatch.setattr(fs, "process_cwds", lambda pids: (cwds, ""))
    monkeypatch.setattr(fs, "pid_alive", lambda pid: True)
    live = fs.live_sessions()
    assert live.measured is False, "a live pid with no attributable cwd is still a gap"
    assert dict(live) == {}, "and no lane is minted from a deleted path"
    assert "2559943" in live.error
    assert raw in live.error, (
        "the deleted path is the datum — without it the gap is undiagnosable")
    assert "no cwd could be read" not in live.error, (
        "a cwd WAS read; saying otherwise is the falsehood that hid the cause")
    assert "orphan" in live.error, "and the remedy names the orphan process, not the gate"


def test_the_arm_that_decided_the_refusal_leads_the_cause(monkeypatch) -> None:
    """ONLY the process arm poisons ``measured`` — an unreadable cmux store is
    covered by the process table, which sees every live lane this user runs — so the
    process arm must LEAD the sentence.

    ⛔ DOWNSTREAM READERS BOUND THIS STRING. The harness records the first cause cut
    to 320 characters and the dispatch line cuts it again at 400 (measured
    2026-10-24: `[dispatch] BLOCKED … first cause: … (tried: `cmux sessi`), so
    listing the inert cmux failure first truncated the deciding datum mid-word.
    """
    monkeypatch.setattr(fs, "cmux_session_records", lambda: (
        [], "", "`cmux sessions list --json --all` exited 2"))
    monkeypatch.setattr(fs, "pi_processes", lambda: ([{"pid": 5, "ppid": 1}], ""))
    monkeypatch.setattr(fs, "process_cwds", lambda pids: ({}, ""))
    monkeypatch.setattr(fs, "pid_alive", lambda pid: True)
    live = fs.live_sessions()
    assert live.error.index("live-process table") < live.error.index(
        "cmux session store"), "the deciding arm leads"
    assert live.measured is False


def test_a_path_with_a_parenthesised_suffix_is_a_cwd_not_a_placeholder(
        monkeypatch) -> None:
    """⛔ A DIRECTORY NAMED ``wt-3051 (2)`` IS A PATH, NOT lsof's PLACEHOLDER.

    ``_CWD_PLACEHOLDER_RE`` matched ANY path whose last segment ends in a
    parenthesised suffix, so a duplicate-checkout spelling (``wt-3051 (2)``) read
    as UNRESOLVED. That is fail-CLOSED but permanent-red: the pid is alive, so
    `proc_error` is set, `measured` goes False, `build_state` publishes the
    `liveness-unmeasured` orphan and EVERY pre-flight on the box answers INCOMPLETE
    for as long as that lane runs — the gate #8999 exists to remove (review of
    #9016). The two forms the reader must still reject are named exactly.
    """
    # The legitimate spelling survives …
    assert fs._usable_cwd("/tmp/wt-3051 (2)") == "/tmp/wt-3051 (2)"
    # … and the two documented placeholders do not.
    assert fs._usable_cwd("/proc/8/cwd (readlink: Permission denied)") == ""
    assert fs._usable_cwd("/wt/8999 (deleted)") == ""

    # The polarity that matters, end to end: such a cwd is a LANE, and the store
    # stays MEASURED (no orphan, no permanently-red gate).
    real = "/tmp/wt-3051 (2)"
    monkeypatch.setattr(fs, "_proc_cwd_is_readable", lambda: True)
    monkeypatch.setattr(fs, "cmux_session_records", lambda: ([], "", "no cmux"))
    monkeypatch.setattr(fs, "pi_processes", lambda: ([{"pid": 8, "ppid": 1}], ""))
    monkeypatch.setattr(fs.os, "readlink", lambda _p: real)
    monkeypatch.setattr(fs, "pid_alive", lambda pid: True)
    live = fs.live_sessions()
    assert live.measured is True, (
        "a real path with a parenthesised suffix is a measurement")
    assert list(live) == [f"{fs.PROC_WS_PREFIX}8"], "and it attributes a lane"


def test_a_guest_cli_error_object_is_not_a_session_store(monkeypatch) -> None:
    """THE ROOT CAUSE. cmux 0.1.0 (the Cloud guest CLI) answers a grammar it does
    not have with VALID JSON:

        {"code":"usage.invalid","message":"--all needs a value"}

    Reading that as "no sessions" is a measured "nothing is running" while the box
    runs lanes. The payload's SHAPE is what separates an answer from a refusal.

    ⛔ AND IT IS WRITTEN TO STDERR WITH A NON-ZERO EXIT (measured on cell-4,
    2026-10-24: `cmux sessions list --json --all` → rc 2, payload on STDERR, STDOUT
    empty). This test used to stub ``_run`` — which degrades a non-zero exit to
    "" — so it fed the classifier a payload the production path could NEVER hand
    it: the suite was green while the box reported "produced nothing" for a
    command that had named its own problem. The seam is ``subprocess.run`` now, so
    the rc/stream path under test is the one the reader actually runs.
    """
    payload = json.dumps({"code": "usage.invalid", "message": "--all needs a value"})
    seen: list[list[str]] = []

    class _Refusal:
        returncode = 2
        stdout = ""
        stderr = payload

    def fake_run(cmd, **kwargs):
        seen.append(list(cmd))
        return _Refusal()

    monkeypatch.setattr(fs.subprocess, "run", fake_run)
    recs, source, err = fs.cmux_session_records()
    assert recs == [] and source == ""
    assert "usage.invalid" in err and "--all needs a value" in err, (
        "the grammar's OWN answer is the cause, and it arrived on stderr")
    assert "exited 2" in err, "the rc is part of the datum"
    assert "produced nothing" not in err, (
        "the command produced a payload that named the problem — saying it "
        "produced nothing is the falsehood this pins")
    assert seen[0][1] == "sessions", "the Mac grammar is still tried first"

    # ⛔ THE BOUND IS ON THE RENDERED STRING, NOT ON ITS INPUT (review of #2401):
    # `repr` expands each non-printable character four-fold, so slicing the input to
    # 200 and THEN calling repr produced up to 802 characters — 2.5x the 320-char
    # window the harness reads a cause through. A control-laden refusal is the case
    # that catches it.
    assert len(fs._cmux_said("\x01" * 200)) <= fs._CMUX_SAID_BOUND
    assert len(fs._cmux_said("\\" * 200)) <= fs._CMUX_SAID_BOUND
    # ⛔ AND THE CUT NEVER SPLITS AN ESCAPE (review of #9887, fourth round). A head slice
    # of the RENDERED form ended `…\x01\x0` — a sequence that never existed, written into
    # the persisted cause by the very function whose job is to make that cause readable.
    # `literal_eval` is the assertion: a rendered form that cannot be parsed back is a
    # truncated one. Red on `repr(first)[:_CMUX_SAID_BOUND]` for n >= 50.
    for n in (50, 59, 200):
        said = fs._cmux_said("\x01" * n)
        # raises ValueError if the cut landed inside `\xNN` — which is the whole point
        rendered = ast.literal_eval(said)
        assert rendered and set(rendered) == {"\x01"}, (
            f"a {n}-byte control payload must render as a WHOLE literal: {said[-12:]!r}")
        assert len(said) <= fs._CMUX_SAID_BOUND
    assert fs._cmux_said("usage.invalid: --all needs a value") == (
        "'usage.invalid: --all needs a value'"), "a plain cause is untouched"


def test_the_cloud_cli_answers_liveness_from_the_process_store_and_SAYS_SO(
        monkeypatch) -> None:
    """THE CELL'S END-TO-END POLARITY (#11202), with the CLI's OWN payloads.

    Measured on the cell 2026-11-09 (`origin/main` 86aaef8f02, cmux 0.1.0 guest CLI):

        cmux sessions list --json --all  → rc 2 'usage.invalid: --all needs a value'
        cmux session list --json         → rc 1 'validation.invalid: request
                                            contains unknown parameters'

    Both grammars are Mac-only and NO grammar on that CLI can supply a per-lane
    session store (every `session list` / `machine list` request comes back
    `request contains unknown parameters` — the guest CLI and the daemon are
    version-skewed). The board therefore answers from the PROCESS TABLE alone, and
    that is measured and legitimate (#8999). What must ALSO be true is that the
    read says WHICH store answered and WHICH could not be read, because a CLEAN
    resting on one store must not read as a CLEAN that read them all.

    ⛔ AND `live.measured is True` HERE IS A DECISION, NOT AN OVERSIGHT (#11678).
    The #11669 review raised this assertion as a P1 the merge left open: it reads
    like a pinned fail-open unless the ruling beside it is visible, and the review
    named the cost of leaving it unexplained — *"the next lane will read the red as
    a regression and revert the fix"*. The ruling is on #11202 (decision comment
    `6754475756`, recorded before the merge): the process table is TOTAL coverage
    for the population this gate protects, so an absent cmux session store costs no
    coverage, and a CLEAN resting on the process table is a MEASUREMENT — not an
    abstention — and stays exit 0.

    OVERRIDES: the blanket "any store that did not answer must make the gate
    non-clean" rule — the process table sees every live lane this user runs, so
    refusing on the cmux group's absence would restore the permanent INCOMPLETE
    (every dispatch blocked, forever) that #11202 was filed to remove.

    ⛔ So if you arrived here to make this `measured is False`: that is arm (a) of
    the #11669 review, and it was REFUSED. Read the decision comment above before
    changing this test — and see the sibling pin in `tests/test_collision_preflight.py`
    (`test_the_unreadable_cmux_store_never_makes_a_measured_board_unanswerable`),
    which is where that refusal is enforced on the GATE's own read.
    """
    class _Refusal:
        returncode = 2
        stdout = ""
        stderr = json.dumps({"code": "usage.invalid",
                             "message": "--all needs a value"})

    monkeypatch.setattr(fs.subprocess, "run", lambda cmd, **k: _Refusal())
    monkeypatch.setattr(fs, "pi_processes", lambda: ([{"pid": 42, "ppid": 1}], ""))
    monkeypatch.setattr(fs, "process_cwds", lambda pids: ({42: CELL_CWD}, ""))
    monkeypatch.setattr(fs, "pid_alive", lambda pid: True)
    live = fs.live_sessions()
    assert live.measured is True, "the process table answered, so the fleet is measured"
    assert live.source == fs.LIVE_STORE_PROCESS
    assert live.unreadable == (fs.LIVE_STORE_CMUX_GROUP,), (
        "the store that did NOT answer travels beside the one that did")
    assert list(live) == [f"{fs.PROC_WS_PREFIX}42"]
    assert "--all needs a value" in live.error, (
        "and the refusal is still recorded — never silently dropped")
    # ⛔ AND THE BOARD CARRIES BOTH HALVES, driven from THIS read (#11202 review,
    # P2: an earlier assertion here compared the store name against `source`,
    # which passes with or without the change and checks no board at all).
    board = _cell_fleet(monkeypatch, live=live)
    rec = board["reconciliation"]
    assert rec["liveness_measured"] is True
    assert rec["liveness_source"] == fs.LIVE_STORE_PROCESS
    assert rec["liveness_unreadable"] == [fs.LIVE_STORE_CMUX_GROUP], (
        "the failing group reaches the board's reconciliation block")
    assert [lane for lane in board["lanes"]
            if str(lane["identity"]["workspace"]).startswith(fs.PROC_WS_PREFIX)], (
        "and the lane the process store attributed is ON the board — not just a "
        "liveness reading")


def test_a_healthy_cmux_store_leaves_nothing_unreadable(monkeypatch) -> None:
    """The complement: when a grammar ANSWERS, the store is not reported as
    unreadable — the new field must not become a permanent complaint (#11202)."""
    monkeypatch.setattr(fs, "cmux_session_records", lambda: (
        [], fs.LIVE_STORE_CMUX_PLURAL, ""))
    monkeypatch.setattr(fs, "pi_processes", lambda: ([{"pid": 42, "ppid": 1}], ""))
    monkeypatch.setattr(fs, "process_cwds", lambda pids: ({42: "/tmp/wt-a"}, ""))
    live = fs.live_sessions()
    assert live.measured is True
    assert live.unreadable == (), "no store refused, so nothing is reported unreadable"
    assert live.source == f"{fs.LIVE_STORE_CMUX_PLURAL}+{fs.LIVE_STORE_PROCESS}"


def test_a_session_store_from_a_FAILED_command_is_named_and_refused(
        monkeypatch) -> None:
    """THE POLARITY THE CAPTURE FIX MUST NOT MOVE (review of #2401).

    Reading a failing command's stdout is what lets the REFUSAL be named. It must
    not become a way to ACCEPT an answer: a non-zero exit can mean a PARTIAL list,
    and a lane missing from a partial store is the fail-open direction — the gate
    answering "nobody holds it" about a running lane. So the payload names the
    refusal and is then NOT the answer.
    """

    class _Partial:
        returncode = 1
        stdout = json.dumps({"sessions": [
            {"agent": "pi", "workspace_id": "ws-1", "pid": 1}]})
        stderr = ""

    monkeypatch.setattr(fs.subprocess, "run", lambda cmd, **k: _Partial())
    recs, source, err = fs.cmux_session_records()
    assert recs == [] and source == "", (
        "a store that arrived with a failed command is not the answer")
    assert "exited 1" in err and "is not read as a session store" in err


def test_a_store_on_STDERR_is_a_cause_never_an_answer(monkeypatch) -> None:
    """THE STREAMS ARE NOT INTERCHANGEABLE (review of #2401).

    Reading the failure's words is what lets the refusal be named — but stderr must
    never become an EVIDENCE source the pre-#2401 reader did not consult: a
    `{"sessions": []}` written there (a warning, a debug echo) would otherwise be a
    MEASURED "nothing is running" from a channel that carries diagnostics.
    """

    class _StderrStore:
        returncode = 0
        stdout = ""
        stderr = json.dumps({"sessions": [
            {"agent": "pi", "workspace_id": "ws-1", "pid": 1}]})

    monkeypatch.setattr(fs.subprocess, "run", lambda cmd, **k: _StderrStore())
    recs, source, err = fs.cmux_session_records()
    assert recs == [] and source == "", (
        "a store on stderr is not an answer, however well-formed")
    assert "printed nothing on stdout" in err
    assert "ws-1" in err, (
        "the datum belongs to the CAUSE: the store WAS read, and it was named — on the "
        "stream that cannot answer, so the refusal carries it and the answer does not")


def test_whitespace_stdout_does_not_shadow_a_stderr_cause(monkeypatch) -> None:
    """A CAUSE COMES FROM THE FIRST STREAM THAT SAID SOMETHING (final review round).

    `raw`-truthiness let a WHITESPACE-only stdout win over a non-empty stderr, so the
    refusal rendered `(stderr: no output)` about a stream that had named the problem —
    the same "produced nothing about a command that said something" falsehood this
    change exists to remove, narrowed to a whitespace case.
    """

    class _WhitespaceStdout:
        returncode = 0
        stdout = "\n"
        stderr = "warning: session socket unavailable, falling back"

    monkeypatch.setattr(fs.subprocess, "run", lambda cmd, **k: _WhitespaceStdout())
    recs, source, err = fs.cmux_session_records()
    assert recs == [] and source == ""
    assert "session socket unavailable" in err, (
        "the stderr cause must not be shadowed by a whitespace-only stdout")
    assert "no output" not in err, "and the label must not claim stderr was empty"


def _collision_preflight():
    """`tools/collision_preflight.py`, loaded by path — the CLAIM GATE's own composition.

    The harness records the gate's sentence, not this module's, so the budget the gap
    message has to fit is decided in THAT file. Calling the producers keeps the arithmetic
    here honest when their wording changes — a literal copy of the prose cannot (review of
    #9887, fourth round).
    """
    import importlib.util

    path = Path(__file__).resolve().parents[1] / "tools" / "collision_preflight.py"
    name = "_collision_preflight_for_gap_budget"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader, f"cannot load {path}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_the_gap_fits_the_bound_its_readers_apply(monkeypatch) -> None:
    """THE MESSAGE IS READ THROUGH A BOUND (review of #2401).

    `refresh-candidates.sh` cuts the first cause it records at 320 characters and
    `orch-loop.sh` prints `why=` at 400. A per-pid clause made five refused pids a
    977-character sentence — so the pids and the remedy fell outside the window, and
    a message built for an unbounded reader is a message that is invisible where it
    matters. The pids, one raw datum and the remedy must all survive 320.
    """
    refused = {
        2510056 + i: f"/tmp/cell-wt-adv-runnerprov-land{i} (deleted)" for i in range(5)
    }

    class _Cwds(dict):
        pass

    cwds = _Cwds()
    cwds.refused = refused
    monkeypatch.setattr(fs, "cmux_session_records", lambda: ([], "", "no cmux"))
    monkeypatch.setattr(fs, "pi_processes", lambda: (
        [{"pid": p, "ppid": 1} for p in refused], ""))
    monkeypatch.setattr(fs, "process_cwds", lambda pids: (cwds, ""))
    monkeypatch.setattr(fs, "pid_alive", lambda pid: True)
    live = fs.live_sessions()
    assert live.measured is False
    head = live.error[:320]
    for pid in refused:
        assert str(pid) in head, f"pid {pid} fell outside the 320-char window"
    assert "/tmp/cell-wt-adv-runnerprov-land0 (deleted)" in head, (
        "the datum fell outside the window")
    assert "orphan" in head, "and so did the remedy"
    # ⛔ THE BOUND IS ON THE DECIDING ARM, NOT ON THE WHOLE SENTENCE (review of #2401, third round).
    # `live.error` joins the process arm AND the cmux arm, and a real box's cmux arm is long: with
    # both grammars refusing (the measured cell-4 state) the whole sentence is 622 characters. The
    # old `len(live.error) < 400` went RED for that legitimate state and was green here only because
    # this fixture stubs the cmux arm to `"no cmux"`. What the readers bound is the FIRST CAUSE —
    # `unusable_cause` cuts at 320 — so that is what has to fit, and it is the arm that decides
    # `measured` that must lead it.
    proc_arm = live.error.split("; cmux session store: ")[0]
    assert len(proc_arm) <= 320, (
        f"the deciding arm must fit the first-cause cut: {len(proc_arm)} chars")

    # ⛔ AND A REAL FLEET PATH IS LONG (review of #2401). The case above uses a
    # 40-character path, which cannot see the failure that matters: Mac worktrees are
    # `/Users/…/tortoise/.worktrees/cell-wt-…`, routinely 90+ characters, and an
    # unbounded sample put the remedy straight back outside the window this message
    # exists to fit (`orphan` fell out of `[:320]` at pathlen 90 — measured).
    long_path = (
        "/Users/danielospina/Documents/GitHub/tortoise/.worktrees/"
        "cell-wt-adv-runnerprov-land-0123456789 (deleted)"
    )
    long_refused = {2510100 + i: long_path for i in range(5)}
    cwds.refused = long_refused
    monkeypatch.setattr(fs, "pi_processes", lambda: (
        [{"pid": p, "ppid": 1} for p in long_refused], ""))
    live = fs.live_sessions()
    head = live.error[:320]
    for pid in long_refused:
        assert str(pid) in head, f"pid {pid} fell outside the window on a long path"
    assert "reap the orphan" in head, "the remedy must survive a long pathlen too"
    assert "runnerprov-land-0123456789 (deleted)" in head, (
        "and the datum must keep the part that IDENTIFIES the directory — its tail")

    # ⛔ AND WITH A REAL cmux ARM (review of #2401, third round). The ordering change exists so the
    # process arm — the only one that can poison `measured` — LEADS the cause, and nothing tested it
    # against a realistic second arm: cell-4's two grammar refusals are ~350 characters on their own,
    # so the deciding datum is only visible because it comes first.
    cmux_arm = (
        "`cmux sessions list --json --all` exited 2: 'usage.invalid: --all needs a "
        "value' (a failed command's output is not read as a session store); "
        "`cmux session list --json` exited 3: 'cannot connect to session socket "
        "/tmp/cmux-tui-1000/cloud.sock: Connection refused (os error 111)' "
        "(a failed command's output is not read as a session store)"
    )
    monkeypatch.setattr(fs, "cmux_session_records", lambda: ([], "", cmux_arm))
    live = fs.live_sessions()
    head = live.error[:320]
    assert live.error.startswith("live-process table: "), (
        "the deciding arm must lead the sentence")
    assert "orphan" in head, (
        "the remedy must still be inside the first-cause cut when a real cmux arm follows it")
    for pid in long_refused:
        assert str(pid) in head, f"pid {pid} was pushed out by the cmux arm"

    # ⛔ AND A CONTROL-LADEN PATH (review of #2401, fourth round). The cap is on the RENDERED form
    # because `repr` expands each non-printable byte four-fold; the plain-path case above cannot see
    # that, so BOTH regressions (dropping the `[:64]`, or re-capping the raw string) shipped green.
    # This case is the only one that can fail on them.
    ctl = {2510200 + i: "/tmp/wt-" + "\x01" * 40 + " (deleted)" for i in range(5)}
    cwds.refused = ctl
    monkeypatch.setattr(fs, "pi_processes", lambda: (
        [{"pid": p, "ppid": 1} for p in ctl], ""))
    live = fs.live_sessions()
    ctl_arm = live.error.split("; cmux session store: ")[0]
    assert len(ctl_arm) <= 320, (
        f"a control-laden sample must not overrun the bound: {len(ctl_arm)} chars")
    assert "reap the orphan" in ctl_arm[:320], (
        "and the remedy must still be inside the window")
    # ⛔ AND THE CAP KEEPS THE TAIL IT PROMISES (review of #2401, fifth round). The cap used to be
    # `repr("…" + sample[-40:])[:64]` — a HEAD cut of the RENDERED form — so this fixture kept 61
    # escape characters and dropped ` (deleted)`, the suffix that says the directory is gone and
    # the reason this message exists. Only the length was graded, so the loss shipped green.
    assert " (deleted)" in ctl_arm, (
        "the rendered sample must keep its TAIL — the suffix is what identifies the "
        f"directory: {ctl_arm!r}")
    rendered_sample = ctl_arm.split(" — e.g. ", 1)[1].split(" (a removed directory", 1)[0]
    assert len(rendered_sample) <= 64, (
        "and it is still bounded to the rendered 64-char cap, not left unbounded: "
        f"{len(rendered_sample)} chars")

    # ⛔ AND THE COUNTING CASE: more than five unresolved pids adds `(+N more)`, which has to be
    # budgeted too (measured 324-326 chars for 8-digit pids before the remedy clause was shortened).
    many = {2520000 + i: f"/tmp/cell-wt-many{i} (deleted)" for i in range(12)}
    cwds.refused = many
    monkeypatch.setattr(fs, "pi_processes", lambda: (
        [{"pid": p, "ppid": 1} for p in many], ""))
    live = fs.live_sessions()
    many_arm = live.error.split("; cmux session store: ")[0]
    # ⛔ AND THE SAMPLE MUST COME FROM THE NAMED SLICE (review of #2401, fourth round). With the
    # refusal held only by pids OUTSIDE the five named, an all-pids scan printed `e.g. <path>` for a
    # pid the message never names — an operator reaping the five would change nothing.
    sparse = {2530000: "", 2530001: "", 2530002: "", 2530003: "", 2530004: ""}
    sparse.update({2530005: "/tmp/cell-wt-the-one-that-matters (deleted)",
                   2530006: "/tmp/cell-wt-also-matters (deleted)"})
    cwds.refused = {k: v for k, v in sparse.items() if v}
    monkeypatch.setattr(fs, "pi_processes", lambda: (
        [{"pid": p, "ppid": 1} for p in sparse], ""))
    live = fs.live_sessions()
    sparse_arm = live.error.split("; cmux session store: ")[0]
    assert "(+2 more)" in sparse_arm, "the omitted count is stated"
    assert "the-one-that-matters" not in sparse_arm, (
        "the datum must never belong to a pid the message does not name")

    assert "(+7 more)" in many_arm, "the omitted count is still stated"
    assert len(many_arm) <= 320, (
        f"the counting case must fit the bound too: {len(many_arm)} chars")
    assert "reap the orphan" in many_arm[:320]

    # ⛔ AND THE BOUND THAT ACTUALLY BITES IS THE COMPOSED ONE (review of #9887, third round).
    # Everything above grades `live.error` against 320, but the harness never records this arm on
    # its own: `refresh-candidates.sh:2342` writes the CLAIM GATE's reason through `cut -c1-320`,
    # and that reason wraps this arm in the gate's own prose — so the cause's budget is what is in
    # front of it, not 320. Asserting `len(live.error) <= 320` therefore proved nothing about the
    # reader the message was written for.
    # ⛔ AND IT IS COMPOSED FROM THE PRODUCERS, NOT FROM A COPY OF THEIR PROSE (review of #9887,
    # fourth round). A literal copy stays green when the gate's wording changes — which is the
    # one event this arithmetic has to survive — so the note is built by the gate itself:
    # `_board_can_answer` (a `liveness-unmeasured` orphan carrying this cause) into
    # `_holder_gate_note`. Both live in `tools/collision_preflight.py` and are called here, not
    # quoted.
    claim_gate = _collision_preflight()
    note = claim_gate._holder_gate_note(claim_gate.HolderRead(
        measured=False,
        source="unreadable board",
        unanswerable=claim_gate._board_can_answer({
            "reconciliation": {
                "registry_measured": True,
                "github_measured": True,
                "open_issues_truncated": False,
                "liveness_error": many_arm,
            },
            "index": {"issues": {}},
            "lanes": [],
            "orphans": [{"kind": "liveness-unmeasured"}],
        }),
    ))
    # Only what is IN FRONT of the cause costs it anything: the gate's trailing
    # ` (comment prose is not read, #8175)` sits after the 320-char cut and costs zero.
    offset = note.index(many_arm)
    recorded = note[:320]
    assert 320 - offset == 139, (
        f"the gate puts {offset} chars in front of this cause, so the harness's 320-char "
        f"cut leaves it {320 - offset} — re-measure the numbers in `live_sessions`' comment "
        f"(and #9900) against THIS, not against a tally of the gate's whole wrapper")
    # What IS guaranteed, in the reader that matters: every pid an operator has to reap is
    # inside the recorded 320 (the last of the five 8-digit pids ends at 259).
    for pid in sorted(many)[:5]:
        assert str(pid) in recorded, (
            f"pid {pid} fell out of the COMPOSED first cause the harness records")
    # What is NOT — tracked as #9900, and pinned so it cannot pass silently for a guarantee: the
    # ` (deleted)` suffix (the part that says the directory is GONE) and the remedy are outside
    # the cut. When #9900 shrinks the gate's prose, THESE flip and must become positive
    # assertions rather than be deleted.
    assert " (deleted)" not in recorded, (
        "the datum's identifying suffix is outside the composed cut today (#9900) — if this "
        "starts failing, the gate's prose shrank and this assertion should be inverted")
    assert "orphan" not in recorded, (
        "the remedy is outside the composed cut today (#9900) — invert, do not delete, once "
        "the composed budget grows")


def test_the_plural_grammar_is_preferred_and_a_later_one_can_answer(monkeypatch) -> None:
    """Version-awareness, both directions: the Mac's plural grammar answers, and if a
    future cmux only has the singular one the reader still reads a SESSION STORE."""
    calls: list[list[str]] = []

    class _Answer:
        returncode = 0
        stdout = json.dumps({"sessions": [
            {"agent": "pi", "workspace_id": "ws-1", "pid": 1}]})
        stderr = ""

    class _NoGrammar:
        # The box's REAL shape: the plural resource is refused on stderr, rc 2.
        returncode = 2
        stdout = ""
        stderr = json.dumps(
            {"code": "usage.invalid", "message": "--all needs a value"})

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        return _NoGrammar() if cmd[1] == "sessions" else _Answer()

    monkeypatch.setattr(fs.subprocess, "run", fake_run)
    recs, source, err = fs.cmux_session_records()
    assert err == "" and source == fs.LIVE_STORE_CMUX_SINGULAR
    assert recs and recs[0]["workspace_id"] == "ws-1"
    assert calls[0][1] == "sessions", "the Mac grammar is tried first"


def test_a_pid_a_cmux_record_already_carries_is_not_emitted_twice(monkeypatch) -> None:
    """On a Mac every lane's pi is in the cmux store. Emitting it a second time as a
    process lane would enumerate one lane twice — the duplicate-label shape that
    makes the claim gate exit 2 on a perfectly readable board.

    ⛔ AND THE MASK REQUIRES A *READING*, NOT A ROW. `--all` is a durable history
    that keeps ENDED sessions, and pids are recycled, so a record whose pid is dead
    proves nothing about the live process that now carries that pid. Masking on mere
    existence dropped the only evidence a cell lane has (it has no cmux workspace at
    all) while the store still read MEASURED — the gate could then answer "nobody
    holds it" about a running lane.
    """
    cmux = [{"agent": "pi", "workspace_id": "WS-1", "pid": 900}]
    monkeypatch.setattr(fs, "cmux_session_records", lambda: (
        cmux, fs.LIVE_STORE_CMUX_PLURAL, ""))
    monkeypatch.setattr(fs, "pi_processes", lambda: ([{"pid": 900, "ppid": 1}], ""))
    monkeypatch.setattr(fs, "process_cwds", lambda pids: ({900: "/tmp/wt-a"}, ""))
    monkeypatch.setattr(fs, "pid_alive", lambda pid: pid == 900)
    live = fs.live_sessions()
    assert list(live) == ["WS-1"]
    assert live.measured is True
    assert live.source == f"{fs.LIVE_STORE_CMUX_PLURAL}+{fs.LIVE_STORE_PROCESS}"

    # THE SAME ROW WITH A DEAD PID IS NOT A READING: the live process is published.
    monkeypatch.setattr(fs, "pid_alive", lambda pid: False)
    live = fs.live_sessions()
    assert list(live) == ["WS-1", f"{fs.PROC_WS_PREFIX}900"], (
        "an ENDED cmux record must not mask the live process that recycled its pid")


def test_a_record_with_a_different_cwd_does_not_mask_a_recycled_pid(monkeypatch) -> None:
    """⛔ A LIVE PID IS NOT THE SAME PROCESS — only a matching CWD proves it is.

    `cmux sessions list --all` is a durable HISTORY that keeps ENDED sessions for
    ever, and pids are recycled (macOS wraps at ~99 998), so "the pid is alive" and
    "the pid was recycled onto an ended record" are the SAME observation: `pid_alive`
    proves only that SOME process holds the pid, never that the record describes it.

    Masking on the pid alone therefore hid a live `pi` that carries a recycled pid.
    That process has no cmux workspace of its own — the process table is its only
    evidence — so its lane vanished while the store still read MEASURED, and the
    gate answered "nobody holds it" about a running lane: the fail-open #8999
    exists to close (review of #9016). The record names a cwd and the process runs
    in one, so the two can be told apart; only the SAME cwd is the same lane.
    """
    cmux = [{"agent": "pi", "workspace_id": "WS-1", "pid": 900,
             "cwd": "/tmp/wt-a"}]
    monkeypatch.setattr(fs, "cmux_session_records", lambda: (
        cmux, fs.LIVE_STORE_CMUX_PLURAL, ""))
    monkeypatch.setattr(fs, "pi_processes", lambda: ([{"pid": 900, "ppid": 1}], ""))
    monkeypatch.setattr(fs, "pid_alive", lambda pid: pid == 900)

    # A DIFFERENT cwd: provably a different lane, so the process is published.
    monkeypatch.setattr(fs, "process_cwds", lambda pids: ({900: "/tmp/wt-b"}, ""))
    live = fs.live_sessions()
    assert sorted(live) == [f"{fs.PROC_WS_PREFIX}900", "WS-1"], (
        "a record whose cwd is another worktree must not mask the live process")
    assert live[f"{fs.PROC_WS_PREFIX}900"][0]["cwd"] == "/tmp/wt-b", (
        "the process's OWN cwd is what attributes its lane")
    assert live.measured is True

    # THE SAME cwd (spelled differently): one lane measured twice, emitted once.
    monkeypatch.setattr(fs, "process_cwds", lambda pids: ({900: "/tmp/wt-a/"}, ""))
    live = fs.live_sessions()
    assert list(live) == ["WS-1"], "the same lane must not be enumerated twice"
    assert live.measured is True

    # AND WHEN THE PROCESS CWD CANNOT BE READ the record is NOT a match: the pid is
    # not shown to be that record's process, so the store is UNMEASURED (fail-closed)
    # rather than answering a measured "nobody holds it" from a comparison that did
    # not happen.
    monkeypatch.setattr(fs, "process_cwds", lambda pids: ({}, ""))
    live = fs.live_sessions()
    assert live.measured is False
    assert "900" in live.error

    # SEVERAL RECORDS ON ONE PID: a cwd match on ANY of them is the same lane, so the
    # verdict cannot depend on the payload's order (a reused workspace accumulates
    # records). WS-1 names another worktree, WS-2 names the process's own — last-wins
    # bookkeeping would publish a spurious `PROC:` lane for a lane already measured.
    both = [cmux[0], {"agent": "pi", "workspace_id": "WS-2", "pid": 900,
                      "cwd": "/tmp/wt-b"}]
    monkeypatch.setattr(fs, "cmux_session_records", lambda: (
        both, fs.LIVE_STORE_CMUX_PLURAL, ""))
    monkeypatch.setattr(fs, "process_cwds", lambda pids: ({900: "/tmp/wt-b"}, ""))
    live = fs.live_sessions()
    assert sorted(live) == ["WS-1", "WS-2"], (
        "a cwd match on ANY record for the pid is the same lane")
    assert live.measured is True


def test_a_record_with_no_workspace_does_not_mask_a_live_process(monkeypatch) -> None:
    """⛔ A RECORD THAT MASKS A PID MUST ALSO PUBLISH ONE.

    `cmux sessions list` can answer a `pi` record carrying a LIVE pid and NO
    `workspace_id` (a degraded/malformed payload). The mask used to be applied
    before the workspace test, so the pid went into `cmux_pids` while the record
    itself had no key to be published under: the `PROC:` leg skipped the process as
    already counted and the record stayed unpublished, leaving a running `pi` in
    NEITHER store while `measured` stayed True — no lane, no orphan, and the gate
    answering "nobody holds it" about a live lane (review of #9016). An
    unpublished record masks nothing, so the process table still speaks for it.
    """
    cmux = [{"agent": "pi", "pid": 900}]           # live pid, NO workspace_id
    monkeypatch.setattr(fs, "cmux_session_records", lambda: (
        cmux, fs.LIVE_STORE_CMUX_PLURAL, ""))
    monkeypatch.setattr(fs, "pi_processes", lambda: ([{"pid": 900, "ppid": 1}], ""))
    monkeypatch.setattr(fs, "process_cwds", lambda pids: ({900: "/tmp/wt-a"}, ""))
    monkeypatch.setattr(fs, "pid_alive", lambda pid: pid == 900)
    live = fs.live_sessions()
    assert list(live) == [f"{fs.PROC_WS_PREFIX}900"], (
        "a record that cannot be published must not suppress the process lane")
    assert live.measured is True


def test_a_healthy_cmux_store_does_not_cover_for_an_unreadable_process_store(
        monkeypatch) -> None:
    """⛔ THE MIXED CASE — the fail-open this reader must not have (#8999).

    `measured` used to mean "at least one store answered", so a cmux store that
    answered EMPTY outranked a process store that had just failed: every process
    lane was dropped, no `liveness-unmeasured` orphan was published, and the gate
    answered "nobody holds it" from a measurement the reader knew was incomplete —
    about a box whose only lanes are the ones cmux cannot see at all.

    The gap here is the narrow one: `ps` saw two pids, one cwd was read, and the
    other pid is STILL ALIVE with no readable cwd. That lane may hold the issue, so
    the fleet is unmeasured — while the lane that WAS attributed is still published
    as evidence.
    """
    monkeypatch.setattr(fs, "cmux_session_records", lambda: (
        [], fs.LIVE_STORE_CMUX_PLURAL, ""))          # answered, and empty
    monkeypatch.setattr(fs, "pi_processes", lambda: (
        [{"pid": 5, "ppid": 1}, {"pid": 6, "ppid": 1}], ""))
    monkeypatch.setattr(fs, "process_cwds", lambda pids: ({5: CELL_CWD}, ""))
    monkeypatch.setattr(fs, "pid_alive", lambda pid: pid == 6)
    live = fs.live_sessions()
    assert live.measured is False, (
        "a store that answered does not cover for one that could not be read")
    assert live.source == fs.LIVE_STORE_CMUX_PLURAL, "the cmux store DID answer"
    assert "6" in live.error, "and the gap names the pid it could not attribute"
    assert list(live) == [f"{fs.PROC_WS_PREFIX}5"], (
        "the lane that WAS attributed is still published as evidence")

    # The same shape when the whole process leg is a failed COMMAND (not a pid):
    # `ps`, or the cwd read, could not run at all. (Before the board, which stubs
    # `live_sessions` itself.)
    monkeypatch.setattr(fs, "pi_processes", lambda: ([], "`ps` produced nothing"))
    failed = fs.live_sessions()
    assert failed.measured is False
    assert failed.source == fs.LIVE_STORE_CMUX_PLURAL
    assert "produced nothing" in failed.error

    monkeypatch.setattr(fs, "pi_processes", lambda: ([{"pid": 5, "ppid": 1}], ""))
    monkeypatch.setattr(fs, "process_cwds", lambda pids: ({}, "`lsof` could not be run"))
    failed = fs.live_sessions()
    assert failed.measured is False
    assert "lsof" in failed.error

    # … and it reaches the BOARD and the GATE: no lane is a measured holder.
    st = _cell_fleet(monkeypatch, live=live)
    assert st["reconciliation"]["liveness_measured"] is False
    assert [o["kind"] for o in st["orphans"]
            if o["kind"] == "liveness-unmeasured"], (
        "the board must publish the orphan, or the gate reads a measured never")


def test_a_measured_empty_process_table_is_an_answer_not_an_unmeasured_one(
        monkeypatch) -> None:
    """A box with no lane running must read MEASURED-EMPTY: that is what lets the
    gate say "nobody holds it" honestly. The opposite mistake — treating an empty
    answer as an unread store — is why every cell pre-flight was INCOMPLETE."""
    monkeypatch.setattr(fs, "cmux_session_records", lambda: ([], "", "no cmux"))
    monkeypatch.setattr(fs, "pi_processes", lambda: ([], ""))
    live = fs.live_sessions()
    assert live.measured is True
    assert dict(live) == {}
    assert live.error


def test_pi_is_matched_on_the_executable_not_on_a_command_line_that_mentions_it() -> None:
    """The cell launcher is ``/bin/bash -c 'cd … && nohup pi …'`` — a process whose
    COMMAND LINE contains "pi" and which is not a lane. Matching on the command line
    would make every launcher a lane (measured 2026-10-18: 9 such shells on one
    box). Another user's ``pi`` is not this fleet's lane either, and its cwd is
    unreadable, so counting it would hold the whole store unmeasured forever."""
    me = os.getuid()
    other = me + 1
    ps = "".join(f"{pid:>5} {ppid:>5} {uid:>5} {cmd}\n" for pid, ppid, uid, cmd in (
        (100, 1, me, "/bin/bash -c cd /tmp/wt && nohup pi --model m -p brief"),
        (101, 1, me, "pi"),
        (102, 101, me, "pi --model m -p brief"),
        (103, 1, me, "/usr/sbin/pi-hole"),
        (104, 1, me, "node /opt/pi-coding-agent/dist/index.js"),
        (105, 1, other, "pi --model m -p someone-elses"),
    ))
    assert fs._pi_pids_from_ps(ps) == [{"pid": 101, "ppid": 1}, {"pid": 102, "ppid": 101}]


def test_lsof_stdout_survives_a_non_zero_exit() -> None:
    """``lsof`` exits 1 when it cannot stat SOME of the pids it was handed while
    still printing the cwd of every one it could. Degrading that to "" would throw
    away a complete answer because one process exited mid-read — and on a busy
    fleet that is the normal case."""
    import subprocess as sp
    import unittest.mock as mock

    class _P:
        returncode = 1
        stdout = "p7\nfcwd\nn/tmp/wt-a\n"
        stderr = "lsof: no pwd entry for UID 0\n"

    with mock.patch.object(sp, "run", lambda *a, **k: _P()):
        out, err = fs._run_tolerant(["lsof", "-a", "-d", "cwd", "-Fn", "-p", "7"])
    assert err == "" and "/tmp/wt-a" in out


def test_a_process_lane_is_scoped_to_this_repos_worktrees(monkeypatch) -> None:
    """A branch slug names a number in EVERY repo, so another repo's ``feat/8999-x``
    would falsely attribute THIS repo's #8999. A process lane is this board's lane
    only when its cwd is one of this repo's worktrees — and when the worktree list
    cannot be read the population is NOT narrowed, because a false COLLISION blocks
    an honest dispatch while a false "nobody" authorises one."""
    outside = _proc_live(9001, cwd="/tmp/other-repo/.worktrees/8999-lane")
    inside = _proc_live(9002)
    live = fs.LiveSessions(
        {f"{fs.PROC_WS_PREFIX}9001": [outside], f"{fs.PROC_WS_PREFIX}9002": [inside]},
        measured=True, source=fs.LIVE_STORE_PROCESS)

    scoped = dict(live)
    kept, _ = fs.register_process_lanes(
        scoped, {}, [], {CELL_CWD: {"branch": "refs/heads/feat/8999-tm"}})
    assert [lbl for lbl, _ws, _i in kept] == ["proc-9002"]

    unscoped = dict(live)
    kept, _ = fs.register_process_lanes(unscoped, {}, [], {})
    assert sorted(lbl for lbl, _ws, _i in kept) == ["proc-9001", "proc-9002"]


def test_a_process_lane_in_a_subdirectory_belongs_to_that_worktree() -> None:
    """A lane that cd'ed into a subdirectory is still its worktree's lane — and it
    must attribute from the worktree ROOT, or its branch (and therefore its issue)
    is lost."""
    deep = _proc_live(9100, cwd=CELL_CWD + "/tools")
    live = {f"{fs.PROC_WS_PREFIX}9100": [deep]}
    roots = {CELL_CWD: {"branch": "refs/heads/feat/8999-tm"}}
    workspaces: dict = {}
    kept, _ = fs.register_process_lanes(live, workspaces, [], roots)
    assert [lbl for lbl, _ws, _i in kept] == ["proc-9100"]
    assert workspaces[f"{fs.PROC_WS_PREFIX}9100"]["cwd"] == CELL_CWD


def test_two_processes_at_two_depths_of_one_worktree_are_ONE_lane() -> None:
    """⛔ ONE WORKTREE IS ONE LANE — and the mint is where that has to hold.

    `live_sessions` collapses processes by CWD, and a lane's processes do not all
    share one cwd: a `pi` that has cd'ed into `<wt>/tools` is a SECOND cwd, so two
    `PROC:` keys reach `register_process_lanes` for ONE lane. The registry-row join
    cannot catch it — those rows are the ones handed IN, and a lane this call
    already minted is not among them — and the same-label guard in
    `_holders_from_board` cannot either, because the two labels DIFFER
    (`proc-A`/`proc-B`). Two records for one lane make `attribute_claims` attribute
    the issue to both and the gate answer TWO live holders: a false COLLISION, on
    exactly the box this fix exists to unblock.
    """
    shallow = _proc_live(9400, cwd=CELL_CWD)
    deep = _proc_live(9401, cwd=CELL_CWD + "/tools")
    live = {f"{fs.PROC_WS_PREFIX}9400": [shallow],
            f"{fs.PROC_WS_PREFIX}9401": [deep]}
    workspaces: dict = {}
    kept, proc_ws = fs.register_process_lanes(
        live, workspaces, [], {CELL_CWD: {"branch": "refs/heads/feat/8999-tm"}})
    assert [lbl for lbl, _ws, _i in kept] == ["proc-9400"], (
        "one worktree, one lane — the second depth folds into the lane already minted")
    assert len(proc_ws) == 1
    # The pids are not lost: both are on the surviving lane's record, so its
    # `pi_pids`/`pid_alive` still cover every process measured in the worktree.
    assert [r["pid"] for r in live[f"{fs.PROC_WS_PREFIX}9400"]] == [9400, 9401]
    assert f"{fs.PROC_WS_PREFIX}9401" not in workspaces, (
        "the folded-away workspace key must not linger as a second workspace")


def test_a_worktree_spelled_two_ways_is_still_one_lane(tmp_path) -> None:
    """The same invariant under the other way one worktree doubles: a REAL PATH
    spelling. cmux records a workspace's cwd as it was given, and a lane's cwd can
    resolve through a symlink, so a plain string comparison mints a second lane for a
    worktree that already has one. `worktree_of` already compares real paths for
    exactly this reason; the owner join has to agree with it."""
    real = tmp_path / "wt-8999"
    real.mkdir()
    link = tmp_path / "link-8999"
    link.symlink_to(real)
    row_cwd = str(link)          # what cmux recorded
    proc_cwd = str(real / "tools")  # where the pi process sits
    live = {f"{fs.PROC_WS_PREFIX}9500": [_proc_live(9500, cwd=proc_cwd)]}
    workspaces: dict = {"WS-A": {"cwd": row_cwd}}
    kept, proc_ws = fs.register_process_lanes(
        live, workspaces, [("lane-7", "WS-A", "")], {str(real): {"branch": "refs/heads/feat/7-x"}})
    assert [lbl for lbl, _ws, _i in kept] == ["lane-7"], (
        "the existing lane is this process — a symlinked spelling does not mint a second")
    assert proc_ws == set()
    assert [r["pid"] for r in live["WS-A"]] == [9500]


def test_two_depths_of_one_worktree_publish_one_lane_on_the_board(monkeypatch) -> None:
    """The same defect at the BOARD level, where it becomes a blocking false
    COLLISION: one attributed label, one lane, one holder."""
    live = fs.LiveSessions({
        f"{fs.PROC_WS_PREFIX}9600": [_proc_live(9600, cwd=CELL_CWD)],
        f"{fs.PROC_WS_PREFIX}9601": [_proc_live(9601, cwd=CELL_CWD + "/tools")],
    }, measured=True, source=fs.LIVE_STORE_PROCESS)
    st = _cell_fleet(monkeypatch, live=live)
    proc = [lane for lane in st["lanes"]
            if lane["identity"]["workspace"].startswith(fs.PROC_WS_PREFIX)]
    assert len(proc) == 1, "one worktree is one lane, whatever depth its pids sit at"
    assert st["index"]["issues"]["8999"]["lanes"] == [proc[0]["identity"]["lane"]]
    assert st["reconciliation"]["process_lanes"] == [
        f"{fs.PROC_WS_PREFIX}9600"]


def test_a_process_lane_joins_a_registry_row_that_already_claims_its_cwd() -> None:
    """One lane, one record: when exactly one registry row's workspace already
    claims the process's cwd, the pid is that lane's measured liveness — minting a
    second lane would make one lane read as a collision between two."""
    reg_cwd = "/tmp/cell-t3/wt-lane"
    deep = _proc_live(9200, cwd=reg_cwd)
    live = {f"{fs.PROC_WS_PREFIX}9200": [deep]}
    workspaces: dict = {"WS-A": {"cwd": reg_cwd}}
    kept, proc_ws = fs.register_process_lanes(
        live, workspaces, [("lane-7", "WS-A", "")],
        {reg_cwd: {"branch": "refs/heads/feat/7-x"}})
    assert [lbl for lbl, _ws, _i in kept] == ["lane-7"], "no second lane is minted"
    assert proc_ws == set()
    assert len(live["WS-A"]) == 1 and live["WS-A"][0]["pid"] == 9200


def test_one_worktree_stays_one_lane_when_the_cmux_workspace_map_is_unreadable() -> None:
    """⛔ THE OWNER JOIN MAY NOT DEPEND ON THE WORKSPACE MAP ALONE (review of #9016).

    ``cmux_workspaces`` degrades to an EMPTY map on every failure path (no single
    window, unparsable JSON, a timeout) and a cell has no cmux at all — but
    ``cmux sessions list`` can still answer, so the registry row binds evidence and
    is KEPT, and its own session record still carries the cwd it ran in. Indexing
    owners from ``workspaces`` only therefore found NO owner for a worktree a
    registry row already owned, minted ``proc-<pid>`` beside ``lane-x``, and the
    board published TWO live holders for one lane — a false COLLISION.
    """
    reg_cwd = "/tmp/cell-t3/wt-lane"
    # Unreadable workspace list — the row's cwd is only in its own live record.
    workspaces: dict = {}
    live = {
        "WS-A": [{"agent": "pi", "workspace_id": "WS-A", "pid": 8100,
                   "cwd": reg_cwd, "session_id": None,
                   "agent_lifecycle": "running",
                   "store": fs.LIVE_STORE_CMUX_SINGULAR}],
        f"{fs.PROC_WS_PREFIX}9200": [_proc_live(9200, cwd=reg_cwd)],
    }
    kept, proc_ws = fs.register_process_lanes(
        live, workspaces, [("lane-7", "WS-A", "")],
        {reg_cwd: {"branch": "refs/heads/feat/7-x"}})
    assert [lbl for lbl, _ws, _i in kept] == ["lane-7"], (
        "the registry row's own record names the cwd, so the process is that lane")
    assert proc_ws == set()
    assert f"{fs.PROC_WS_PREFIX}9200" not in live, (
        "the synthetic key must not linger as a second lane")
    assert [r["pid"] for r in live["WS-A"]] == [8100, 9200]


def test_worktree_of_prefers_the_deepest_root_by_resolved_length(tmp_path) -> None:
    """⛔ THE RETURNED ROOT IS RAW, BUT THE DEPTH IS RESOLVED (review of #9016).

    ``worktree_of`` returns the RAW root (the caller keys the worktree map by it),
    so comparing ``len(rroot) > len(best)`` compared a RESOLVED length against a RAW
    one. A parent whose raw spelling is longer than its resolved path (a symlinked
    root) then beat its own CHILD whenever the child's extra segments were shorter
    than the symlink's excess — collapsing a ``.worktrees/<lane>`` lane onto the hub,
    whose branch is ``main``, so the issue that lane holds reads "nobody holds it".
    """
    short = tmp_path / "p"
    short.mkdir()
    child = short / "w"
    child.mkdir()
    long_link = tmp_path / "this-is-a-really-long-parent-directory-name"
    long_link.symlink_to(short)
    cwd = str(child / "tools")
    roots = [str(long_link), str(child)]
    assert len(str(long_link)) > len(str(child)), (
        "the fixture must keep the raw parent LONGER than the resolved child, "
        "or it cannot distinguish the two comparisons")
    assert fs.worktree_of(cwd, roots) == str(child), (
        "the deepest root wins by RESOLVED length, and it is returned RAW")


def test_process_lanes_are_skipped_by_the_session_disk_scan(monkeypatch) -> None:
    """The disk rule reads the first user message of every file in a lane's session
    directory; on the shared checkout that is 1.6 GB of files. A process lane needs
    no guess — its pid IS the measurement — so the scan must not run for it, or a
    cell board re-reads the whole store once per lane.

    ⛔ WITH A CONTROL ARM: the same board carries a NON-process lane whose session
    directory also has files, so `seen` proves the scan RUNS for a lane it should
    scan and does not run for the process lane. Without that arm the assertion is
    vacuous — `session_index` was stubbed empty, so `first_user_message` was never
    called for ANY lane, and deleting the skip left the test green (review of
    #9016)."""
    seen: list[str] = []
    monkeypatch.setattr(fs, "first_user_message",
                        lambda p: seen.append(str(p)) or "x")
    other_wt = "/tmp/cell-t3/wt-other-lane"
    scanned = f"/sessions/{fs.mangled(other_wt)}/2026-01-01T00-00-00-000Z_abc.jsonl"
    # The PROCESS lane's own directory is populated too, so "not in `seen`" is a
    # real skip and not an empty directory.
    proc_file = f"/sessions/{fs.mangled(CELL_CWD)}/2026-01-01T00-00-00-000Z_def.jsonl"
    live = fs.LiveSessions(
        {f"{fs.PROC_WS_PREFIX}9300": [_proc_live(9300)]},
        measured=True, source=fs.LIVE_STORE_PROCESS)
    _cell_fleet(
        monkeypatch, live=live,
        registry=[("lane-other", "WS-B", "")],
        workspaces=fs.WorkspaceMap(
            {"WS-B": {"cwd": other_wt}}, measured=True),
        session_index=({}, {
            fs.mangled(other_wt): [scanned],
            fs.mangled(CELL_CWD): [proc_file],
        }),
    )
    assert scanned in seen, "the control lane's directory IS scanned"
    assert proc_file not in seen, "a process lane's directory is NOT scanned"


def test_the_board_reports_which_store_answered(monkeypatch) -> None:
    """`reconciliation` carries the liveness store's provenance, so an INCOMPLETE
    verdict can name the cause instead of asserting an unreadable one."""
    live = fs.LiveSessions(
        {f"{fs.PROC_WS_PREFIX}4242": [_proc_live(4242)]},
        measured=True, source=fs.LIVE_STORE_PROCESS, error="no cmux on this box")
    rec = _cell_fleet(monkeypatch, live=live)["reconciliation"]
    assert rec["liveness_source"] == fs.LIVE_STORE_PROCESS
    assert rec["liveness_error"] == "no cmux on this box"
    assert rec["liveness_measured"] is True


# ── the process census must be able to SEE a lane whose argv[0] is a path ────────
def test_pi_processes_reads_the_untruncated_command_column(monkeypatch) -> None:
    """⛔ `ps` TRUNCATES `command=` TO THE OUTPUT WIDTH, AND THE MATCHER READS ITS BASENAME.

    Measured 2026-10-24 while building the EMPTY-CLAIM leg: a fixture lane launched as
    `/tmp/…/fakebin-empty/pi 120` arrived as `/tmp/…/fakebin-empt` (80 columns), so
    `_pi_pids_from_ps` matched no basename `pi` and the census answered "no pi process"
    about a process that was running. That is the fail-open direction for everything built
    on the answer — `StaleHolder._owner` reads exactly it to decide whether a worktree has a
    live owner. Without `-ww` this test fails; the assertion is on the ARGV the reader
    sends, so it cannot pass by accident on a box whose `ps` happens not to truncate.
    """
    seen: list[list[str]] = []

    def _fake_run(cmd, timeout=60):
        seen.append(list(cmd))
        # A row shaped exactly as the truncated `ps` produces it: the basename gone.
        return f"  4242   1   {os.getuid()} /tmp/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/fakebin-empt\n"

    monkeypatch.setattr(fs, "_run", _fake_run)
    procs, err = fs.pi_processes()
    assert err == ""
    assert procs == [], "a truncated command is not a pi row — it must not be guessed at"
    argv = seen[0]
    assert "-ww" in argv, f"the census must not let `ps` truncate the command column: {argv}"
    assert argv[:2] == ["ps", "-ww"]


def _row_for_pid(pid: int) -> str:
    """The `command=` text a census would read for ONE pid — see the two callers for its role.

    ⚠️ IT IS NOT ONE THING. In caller 1 it is a DIAGNOSTIC interpolated into a failure message;
    in caller 2 it IS the assertion's subject (that test's verdict is `assert row`). Calling it
    "never an oracle" was true of caller 1 only, and contradicts caller 2 below.

    FOUR call sites (#12039), and they do NOT play one role. `poll()` collects the status and a
    collected child has no row left to quote, so every call site reads the row FIRST.

      1. the fixture test's FAILURE path — when the poll ends without the census seeing the
         fixture's pid (the deadline expiring, or the child dying early), what settles the cause
         in one run is whether the table holds the fixture at all, holds it under a truncated
         argv[0], or does not hold it at all. DIAGNOSTIC: interpolated into a message;
      2. `test_child_exited_reads_the_status_without_reaping_the_row`, on a PASSING path, where
         the row IS the assertion's subject — it is how `_child_exited`'s `WNOWAIT` is proven to
         have left the status uncollected;
      3. the CAPABILITY GATE in `test_ps_answers_is_the_control_it_claims_to_be`;
      4. the CAPABILITY GATE in `test_child_exited_reads_the_status_without_reaping_the_row`.

    ⛔ CALLERS 3 AND 4 ARE GATES, NOT DIAGNOSTICS (#12039 cycle 14 P1/P2): they decide SKIP vs
    FAIL, and at site 3 this call is the *witness* that stops a broken `_ps_answers` from skipping
    the very test that must catch it. Its argv is pinned textually by its own guard, because
    dropping `-p` would make it answer from the whole table for ANY pid and silently satisfy
    `assert row` at site 2.

    ⛔ AND IT MUST CARRY A TIMEOUT (#12039 cycle 14 P1). It had none, which made it the worst kind
    of authority: a `ps` slower than `_ps_answers`'s 60 s budget answered HERE while the probe said
    "cannot answer", and `assert answers is True` turned that into a required-shard FAIL produced by
    LOAD — the #5049 rule-1 inversion this PR exists to remove — while a `ps` that HUNG held the
    shard open indefinitely (measured: `timeout -s KILL 45` → exit 137, zero output). A timeout, or
    any `OSError`/`SubprocessError`, returns `""`, so an unreadable-in-time table lands on the
    existing SKIP arm and can never be what reds the shard.

    `""` means the table has no row for the pid — or could not be read within the budget.
    """
    import subprocess  # no module-level import in this file; the caller imports it too

    try:
        return subprocess.run(
            ["ps", "-ww", "-p", str(pid), "-o", "command="],
            capture_output=True, text=True, timeout=_CENSUS_SAMPLE_TIMEOUT_S,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):  # a `ps` that cannot answer in time
        return ""


#: The signals a signal-death most often comes from OUTSIDE the fixture: SIGKILL/SIGTERM from
#: an OOM killer or an orphan sweep, SIGINT from the shard's own `timeout -s INT`, SIGHUP from
#: a dropped controlling terminal. These are evidence about the BOX — a killed child has no
#: argv[0] for any census to match — so a deadline expiry on one of them is INCONCLUSIVE
#: (#5049 rule 1). Every OTHER signal is TREATED AS a substrate fault and must stay a FAIL
#: (#5049 rule 4): SIGSEGV/SIGABRT/SIGBUS/SIGILL/SIGFPE are the interpreter aborting, and the arm
#: names the signal rather than assuming one.
#:
#: ⚠️ KNOWN RESIDUAL, stated rather than implied: `_OUTSIDE_SIGNALS` is a four-signal allow-list,
#: so a signal the KERNEL sends on a resource limit — `SIGXCPU` (24) on `RLIMIT_CPU`/`ulimit -t`,
#: `SIGXFSZ` (25) on `RLIMIT_FSIZE` — lands in the crash arm and is reported as the substrate's
#: fault. That attribution direction is the one this PR exists to fix, on the signals it was
#: measured against; widening the allow-list changes verdicts and needs its own measurement, so
#: it is left as a named residual rather than a quiet guess.
_OUTSIDE_SIGNALS = frozenset({signal.SIGKILL, signal.SIGTERM, signal.SIGINT, signal.SIGHUP})

#: How many CONSECUTIVE census errors make the error the census's fault rather than the box's.
#: ONE is not enough: `pi_processes()` folds a timeout, a refusal and an argument rejection into
#: the same empty string, and a loaded self-hosted runner can lose a single `ps` — that is
#: #5049 rule 1 (a deadline/load expiry must not produce a FAIL), and it is the box that
#: produced the 2026-11-23 incident. Persisting across samples, on a box whose own `ps -p <pid>`
#: answers, is not load.
_CENSUS_ERROR_STREAK = 2

#: …and how LONG that streak must last before it stops being load. A streak alone measures
#: nothing: the samples are 0.1 s apart, so `_CENSUS_ERROR_STREAK` fires in 0.2 s, while
#: `pi_processes()` folds a 30 s `_run` timeout, a refusal and an argument rejection into the
#: same empty string — and a memory-pressure burst (the box that produced the 2026-11-23
#: incident OOM-killed a child) outlasts 0.2 s by construction. Two seconds of CONTINUOUS
#: failure on a box whose own `ps -p <pid>` answers is the census; anything shorter is #5049
#: rule 1 (load must not produce a FAIL).
_CENSUS_ERROR_MIN_SPAN_S = 2.0

#: How long a census SAMPLE may spend failing before it is a wall-clock expiry rather than a
#: broken invocation (#12039 cycle 8, P1). `pi_processes()` reads the table through
#: `_run(..., timeout=30)` (`tools/fleet_state.py`), whose `except Exception: return ""` folds
#: a `TimeoutExpired` into the SAME empty string as a rejected argument — so the error TEXT
#: cannot tell load from a census fault, and the only signal that separates them is how long
#: the sample took. A census whose invocation is wrong fails in milliseconds; one that burned
#: `_run`'s whole subprocess budget on a loaded box is a `ps` timeout, which is #5049 rule 1
#: (load must not produce a FAIL). ⚠️ THIS IS `_run`'s TIMEOUT SPELLED HERE because it is passed
#: EXPLICITLY at `pi_processes`'s own call (`_run(["ps", "-ww", …], timeout=30)`), not taken from
#: `_run`'s default (`timeout: int = 60`), so it cannot be imported — keep the two in step.
_CENSUS_SAMPLE_TIMEOUT_S = 30.0

#: The budget the long-path poll runs on — ONE home for the number, because the body's
#: `deadline` and the arms' PREMISES must read the same value (#12039 review cycle 11, F1).
#: A premise that carries its own copy of the budget (`assert 6 * step >= 10.0` beside a body
#: that says `deadline = time.time() + 10.0`) is decoupled from the thing it names: raise the
#: body's budget and the premise keeps passing while the arm silently grades a different poll.
_LONG_PATH_POLL_BUDGET_S = 10.0

#: How many `time()` reads carry the poll's clock from its start to the FIRST erroring sample's
#: own deadline check — the multiplier in `once_slow`'s and `death_after_streak`'s premises.
#: MEASURED by line trace at `b353771b43`: TWO reads before the loop (`deadline`,
#: `poll_started`) plus FIVE inside the sample (`sample_started`, the duration read,
#: `streak_started`, the span read, the error branch's deadline check) is SEVEN; this is
#: deliberately BELOW the true count, so a step derived from it still carries the clock with one
#: read of slack. ⛔ NOT A GUESS, BUT NOT A STATIC PIN EITHER (#12039 cycle 13, F1) — for a TYPO
#: in either direction. MEASURED on copies at this head, implementation unmutated, only this
#: constant moved:
#: `6 -> 7` leaves `184 passed`, `19` leaves `184 passed`, and `1` and `2` also leave `184 passed`
#: (they DO move the `max()` below — `10/1 + 0.1 = 10.1` — and still red nothing, because the step
#: has to pass `_CENSUS_SAMPLE_TIMEOUT_S = 30.0` to change a verdict, which this integer domain
#: cannot reach). Only a raise to `>= 20` reds (`20 * 0.5 = 10.0` is no longer `< 10`) — MEASURED,
#: `11 failed`, the premise-(b) assertion firing across all 11 params. The behavioural pins are the
#: `once_slow` and `death_after_streak` ROWS, not a bare assertion on this constant.
_LONG_PATH_ERROR_SAMPLE_TIME_CALLS = 6

#: How many `time()` reads span the STREAK at the sample where the count+span conjunction fires
#: (`death_after_streak`'s premise (a)): `sample_started` -> the span read is FIVE reads, which
#: is what `_CENSUS_ERROR_MIN_SPAN_S / 5 + 0.1` is derived from.
#: ⛔ AND IT HAS NO STATIC PIN, SO A TYPO HERE RETIRES THE ORDERING GUARD SILENTLY (#12039 cycle
#: 13, F1). The guard that looks like a pin —
#: `_LONG_PATH_STREAK_TIME_CALLS * death_step_s >= _CENSUS_ERROR_MIN_SPAN_S` — expands to
#: `0.1 * K >= 0` and is true for every positive K, because the divisor cancels against its own
#: product. MEASURED at this head, on copies, restored after each run:
#:   K = 8, implementation unmutated      -> `184 passed`  (the deleted assertion saw nothing)
#:   K = 8, `_child_exited` moved BELOW the conjunction -> `184 passed`  (the row goes VACUOUS)
#:   K = 5, same mutant                    -> `1 failed`   (the row reds — it IS the real pin)
#: ⛔ MEASURE THE MUTANT YOU NAME (#12039 cycle 13). An earlier version of this paragraph reported
#: "still `2 failed` at `K = 8`" from a mutant that made the check UNREACHABLE rather than moving it
#: below the gate — that is DELETE-entirely, not a reordering. It reds at every K at which the file
#: is green unmutated, for a different reason (nothing observes the death), and the two are NOT
#: interchangeable: the difference is the finding.
#: WHY the typo is NOT survivable: with the gate not yet qualified at the death sample the poll
#: keeps going; a moved-below check then meets the already-dead child on that same sample and
#: breaks to the same outside-signal SKIP the shipped order produces — both orderings agree, and
#: the arm can no longer distinguish them. (Deleting the check instead reds the row on every K at
#: which the file is green unmutated — for a DIFFERENT reason, since nothing observes the death at
#: all — which is exactly why the wrong mutant looked reassuring. ⛔ SCOPE A CLAIM TO THE K IT WAS
#: MEASURED AT: the deletion raises on the death sample itself at the shipped `K = 5`
#: (`on 2 consecutive samples over 2.50s`) and only one sample later from `K = 7` (`K = 8`: count 3,
#: span 3.15 s), and at `K = 1` nothing at all is attributable — premise (b) below reds the FILE
#: with no mutant applied, the same `11 failed, 173 passed` either way.)
#: SO: no static assertion can pin this constant. The `death_after_streak` ROW pins the ORDERING
#: wherever the gate is satisfied at the death sample — `5 * (2/K + 0.1) >= 2.0`, i.e. `K <= 6` —
#: MEASURED: `1 failed` at `K` = 2, 3, 4, 5, 6, and `184 passed` (vacuous) at `K` = 7 and 8. A typo
#: past that boundary therefore retires the guard silently, with nothing static to red. ⛔ SCOPE IT
#: THAT WAY rather than to "the shipped value": the row covers a RANGE of K, and writing the range
#: as one point is the same over-claim in the safe direction.
_LONG_PATH_STREAK_TIME_CALLS = 5


def _signal_name(num: int) -> str:
    """`SIGNAME (n)` for a signal number — TOTAL over the numbers, unlike `signal.Signals`.

    ⛔ `signal.Signals` is NOT total, so a message may not format a name unguarded. On Linux the
    realtime band is enumerated at its two ENDS only (`SIGRTMIN` = 34, `SIGRTMAX` = 64), so every
    number in between — 35..63 — raises `ValueError`, measured on the shipped enum:

        signal.Signals(9).name  -> 'SIGKILL'          signal.Signals(35) -> ValueError
        signal.Signals(11).name -> 'SIGSEGV'          signal.Signals(40) -> ValueError

    The CRASH arm below interpolates a name into the `HarnessDefect` it raises, so an unguarded
    `signal.Signals(-rc).name` replaces that verdict with a `ValueError` traceback about the
    FIXTURE's death — which reads as a bug in this file, not as "the substrate aborted". The
    number is the one fact the diagnosis always has, so the fallback keeps it.
    """
    try:
        return f"{signal.Signals(num).name} ({num})"
    except ValueError:  # unnamed on this platform: e.g. SIGRTMIN < n < SIGRTMAX
        return f"signal {num}"


def _child_exited(proc) -> bool:
    """Has the child EXITED? Never collects its status, so `ps` still renders the zombie row.

    `proc.poll()` answers the same question but COLLECTS the status (`waitpid`), and a collected
    child disappears from the process table — the diagnosis below interpolates exactly that row
    (`'[python] <defunct>'`) as its evidence. `waitid(WNOWAIT)` reports an exited child without
    reaping it, so the evidence survives. Without `waitid` the fallback is `poll()`: the answer
    is still right and the row is then empty.
    """
    try:
        return os.waitid(os.P_PID, proc.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None
    except AttributeError:  # pragma: no cover — POSIX waitid absent
        return proc.poll() is not None
    except (ChildProcessError, OSError):  # ECHILD — every hermetic arm (their pid is no child)
        return proc.poll() is not None


def test_signal_name_is_total_over_every_signal_number() -> None:
    """The CRASH arm's message must never *be* the exception it reports (#5049 rule 4).

    `signal.Signals(n)` is not total — the realtime band is enumerated at `SIGRTMIN`/`SIGRTMAX`
    only — so `signal.Signals(35).name` raises. Formatting a name unguarded inside the
    `HarnessDefect(...)` message would lose the verdict the message exists to deliver and leave
    a `ValueError` traceback pointing at this file instead of at the aborted fixture. The gap is
    MEASURED here rather than assumed: if `Signals` ever becomes total the fallback is dead code
    and this test says so, instead of quietly passing over it.
    """
    assert _signal_name(signal.SIGKILL).startswith("SIGKILL")
    assert _signal_name(signal.SIGSEGV).startswith("SIGSEGV")
    unnamed: list[int] = []
    for num in range(1, 128):
        try:
            signal.Signals(num)
        except ValueError:
            unnamed.append(num)
        # NO number may cost the verdict, whether or not `Signals` can name it.
        assert _signal_name(num)
    assert unnamed, "signal.Signals names 1..127 here, so this fallback is dead code"
    for num in unnamed:
        assert _signal_name(num) == f"signal {num}"


def _ps_answers() -> bool:
    """Can THIS box's `ps` answer at all? The control for the census's own error path.

    `tools.fleet_state.pi_processes()` reports an error whenever its `ps` call yields nothing,
    and `_run` folds a refusal, an argument rejection and a timeout into that same empty string.
    A box with no usable `ps` may legitimately skip this test; a box whose `ps` answers and whose
    census still fails is the defect the test exists to find. This is the question that tells the
    two apart, asked about our OWN pid so it cannot depend on the fixture.
    """
    import subprocess

    try:
        out = subprocess.run(
            ["ps", "-p", str(os.getpid()), "-o", "command="],
            capture_output=True, text=True, timeout=60,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):  # `ps` absent/unusable — driven by its own test
        return False
    return bool(out)


def test_pi_processes_sees_a_real_long_path_lane(monkeypatch) -> None:
    """End-to-end MATCHER coverage: a real process named `pi` under a long path is found.

    ⛔ AND IT PINS THE TRUNCATION FIX, which the first version of this test did not (review,
    2026-10-24). The truncation is driven by the OUTPUT WIDTH — `COLUMNS` when the
    environment carries one, the terminal width otherwise — so a box with a wide terminal
    shows nothing and a green here proved nothing about `-ww`. This test SETS
    `COLUMNS=80` (measured: every `ps` row cut to exactly 80 characters, 1966 uncapped),
    which reproduces the hazard deterministically: the fixture lane's argv[0] is ~59
    characters, so without `-ww` the `command=` column is cut before its basename and the
    census cannot see a live lane. Verified red with the flag removed.

    A real process carrying an arbitrary argv[0] (never a `#!` script — the kernel
    rewrites argv[0] to the interpreter, which `_pi_pids_from_ps` documents). SKIPS on FOUR
    paths and no more: where the process table cannot be read at all, which means the reader
    reports an error AND ``ps`` itself cannot answer (`_ps_answers()` asks about our own pid, so a
    box without a usable `ps` does not red a census that is fine); where an
    OUTSIDE signal killed the fixture's child while the poll ran (`_OUTSIDE_SIGNALS` — the OOM
    killer, an orphan sweep, the shard's own timeout; a killed child has no `argv[0]` for any
    census to match); where ONE census sample burned `_run`'s whole subprocess budget
    (`_CENSUS_SAMPLE_TIMEOUT_S`) — a `ps` timeout is load, and `pi_processes` folds it into the
    same string as a broken invocation; **or** where the census NEVER answered the poll
    (`healthy_samples == 0`) — with no healthy sample there is nothing to judge the `-ww` flag by.
    Everything
    else stays a FAIL, and there are FOUR of them — the taxonomy is
    enumerated here so a reader re-deriving from this header cannot reinstate a skip that the
    arms below deliberately removed (#12039, review cycles 4, 6 and 9):

      * the child stays **ALIVE** and unreported for the whole deadline (``rc is None``) — the
        `-ww` pin itself, and this test's primary assertion. ⛔ This is NOT one of the substrate
        arms below, and it is the ONE arm that deliberately DEPARTS from #5049 rule 1 (a
        wall-clock expiry FAILS here): the census has already answered NEGATIVELY about a subject
        the test can see is alive, so the expiry is the MEASUREMENT rather than a budget standing
        in for an unmeasurable one, and rule 1's premise does not hold. Recorded as an
        `OVERRIDES:` line on #12039 — never turn THIS arm into a skip;
      * the census **keeps** reporting an error while ``ps`` itself answers — `_CENSUS_ERROR_STREAK`
        CONSECUTIVE samples **spanning `_CENSUS_ERROR_MIN_SPAN_S`**. It is a CONJUNCTION: one
        lost ``ps`` under load is not the census (#5049 rule 1), and neither are TWO that are
        0.2 s apart — the count-only reading is the gate mutation that redded `late_pair`;
      * a clean exit means the fixture never ran its command (``rc >= 0``);
      * a CRASH signal (SIGSEGV/SIGABRT/…) means the interpreter under it died.

    The last two are the test substrate's fault, not the box's; the second is this test's own
    subject; the first is the property under test. See the exits far below.

    ⚠️ THE COUNT IS PART OF THE CONTRACT. Four `raise` sites and **FOUR** skip paths — the table
    unreadable at all (`_ps_answers`), one sample over `_CENSUS_SAMPLE_TIMEOUT_S`, a census that
    never answered (`healthy_samples == 0`), and `_OUTSIDE_SIGNALS`; a header that
    states a different count is the drift `test_the_long_path_test_header_names_the_census_error_fail_arm`
    guards BY NAME (`_CENSUS_ERROR_STREAK`, `_ps_answers`, `_CENSUS_ERROR_MIN_SPAN_S`,
    `_CENSUS_SAMPLE_TIMEOUT_S`, `healthy_samples`, `OVERRIDES`) **and BY NUMBER** — it parses the
    word after `SKIPS on ` and compares it to the `pytest.skip` + `inconclusive` calls the body
    actually has, by AST, so re-wording the number is a RED rather than an assumption. (Measured
    drift: this block said "THREE" while
    omitting the ALIVE arm, in the same docstring that says "FOUR classifications" — two
    contradictory counts, in the block a guard exists to keep honest. Cycle 9 caught the same class
    again from the other side: the count said "two skip paths" after a third one landed.)

    ⛔ IT IS NOT A COPY OF `sleep` ANY MORE (measured on the `ubuntu-26.04` hosted
    runner `GitHub Actions 1000848497`, `test (c)`, head `70116a870dbb`). On that image
    `/usr/bin/sleep` is a symlink into the uutils MULTI-CALL binary
    (`/usr/lib/cargo/bin/coreutils/sleep`, 11.8 MB), and a multi-call binary dispatches on
    `basename(argv[0])`. Copied to this fixture's long name it dies before it can sleep:
    `cp /usr/bin/sleep <dir>/pi && <dir>/pi 60` prints `coreutils: unknown program 'pi'`
    and exits 1. The census then answered `pids seen: []` about a child that never
    existed — CORRECTLY — and no bounded poll can repair that, so the fixture, not the
    census, was the defect.

    The pin itself was re-measured on that same image before this change: at `COLUMNS=80`
    `ps -ww -axo pid=,ppid=,uid=,command=` shows the whole long path (basename `pi`
    matches), and the identical call WITHOUT `-ww` is cut at exactly 80 characters —
    measured on this fixture's shape, the row ends inside the PATH, so what a matcher
    reads as `basename(argv[0])` is the truncated directory name
    (`pi-census-xxxx…sx8`), never `pi`. So `-ww` is still load-bearing; only the
    fixture's way of putting an arbitrary argv[0] in front of a live process changed.
    `executable=` runs this interpreter under the long path — exactly the argv[0] the
    matcher reads — with no dependence on a copied binary accepting its new name.

    ⛔ A BOUNDED POLL, AND THE REASON IS NOT THE FORK WINDOW THIS ONCE CLAIMED (#12039,
    measured). `Popen` returns after a successful `execve` on CPython/Linux (the parent
    blocks on the CLOEXEC errpipe), and a `/proc/<pid>/cmdline` read taken the instant it
    returned never carried the parent's argv — though it CAN be readable-and-EMPTY while
    the child is state `R` (measured: 13-30 OURS / 10-27 EMPTY over 40-spawn samples,
    load- and spawn-mode dependent). So a `/proc` read is not a witness that the row is
    populated; a `ps` invocation costs ~43 ms here, which is why a poll on the TABLE (what
    the census itself reads) is the shape that settles.

    What this test has actually measured is worse for the one-shot form: at `70116a870dbb`
    the census redded with `pids seen: []` AFTER the full 10 s poll, which a window cannot
    produce — a window resolves inside the deadline, so that red was the FIXTURE (a copied
    multi-call `sleep` that refuses its new name on `ubuntu-26.04`), which `executable=`
    fixes.

    The poll is kept because it costs nothing on the green path and it makes a red
    legible (the deadline plus the table's own row, below), NOT because a race here was
    measured. It does not widen the match, so the truncation pin keeps its power: a
    `-ww`-less census truncates on every sample, burns the deadline and fails.

    ⛔ THE EXPIRY SPLITS ON WHAT ACTUALLY HAPPENED TO THE FIXTURE — FOUR classifications,
    not the three this list carried before the crash arm landed (#5049 rules 1 and 4). The
    pin's claim is "a `-ww`-less census misses a LIVE child", so:

      * child **ALIVE** (`poll() is None`) and unreported → **FAIL**. The real regression.
      * child killed by an **OUTSIDE** signal (`-rc in _OUTSIDE_SIGNALS`: SIGKILL, SIGTERM,
        SIGINT, SIGHUP) → **INCONCLUSIVE**, naming the 10 s deadline. The box killed it (OOM,
        an orphan sweep, the shard's own `timeout`) and a killed child has no `argv[0]` for
        any census to match, so the expiry says nothing about the flag. Before this split
        that case redded a REQUIRED shard on a diff (`tests/test_12184_shifted_cites.py`)
        that cannot reach this file.

        ⛔ HOW THE 2026-11-23 INCIDENT IS ATTRIBUTED — THE INFERENCE, NOT THE ROW TEXT. The
        failing log carried `pids seen: [1104635]` (a healthy census matching a *different*
        `pi` the runner box itself runs) and `the table's row for the fixture pid read
        '[python] <defunct>'`, but it printed NO returncode — and a cleanly-exited child is an
        unreaped zombie too, leaving the identical `'[python] <defunct>'` row. So the row text
        identifies NEITHER arm. The attribution rests on the command itself: the payload is
        `import time; time.sleep(60)`, which cannot return inside a 10 s deadline, so a child
        that vanished did so by being KILLED, not by exiting on its own. That is the
        inference — stated here because the row text cannot carry it.

        ⛔ AND IF THAT INFERENCE IS WRONG, THIS REDS RATHER THAN SKIPS — deliberately. A child
        that exits `>= 0` under a `sleep(60)` payload means the interpreter never ran it, and a
        CRASH signal (SIGSEGV/SIGABRT/…) means the interpreter died under it; both are
        substrate faults that rule 4 requires to FAIL (see the last two exits). An honest red
        that names the substrate beats a permanent green that hides it; the cost is that such
        an image fails this required shard until the fixture is updated, and the
        `HarnessDefect` message says so.

      * child killed by a **CRASH** signal (any other signal → `HarnessDefect`), deliberately
        a FAIL per rule 4: the interpreter aborted, which is the substrate, never the box.
      * child **EXITED CLEANLY** (`poll() >= 0`) → **`HarnessDefect`**, deliberately a FAIL per
        rule 4. `time.sleep(60)` cannot return inside a 10 s deadline, so a clean exit means the
        fixture never ran its command — the class the docstring above records as a CAUGHT bug
        (the `ubuntu-26.04` uutils multi-call `sleep` that exited 1). Skipping *this* one would
        make a permanently-refusing fixture green-by-skip on every run: a false PASS that no
        instrument reds, since neither static nodeid manifest carries this file and the
        INCONCLUSIVE family deliberately trips no skip-guard.
    """
    import shutil
    import subprocess
    import tempfile

    long_dir = Path(tempfile.mkdtemp(prefix="pi-census-" + "x" * 40))
    # ⛔ The narrow width is the FIXTURE, not the environment: without this the test
    # measures whatever terminal the suite happens to run under.
    monkeypatch.setenv("COLUMNS", "80")
    try:
        pi = long_dir / "pi"
        # argv[0] is the long path — what the census matcher reads. `executable=`
        # decides what actually RUNS, so no copied binary has to accept being named
        # `pi`, and the child is a live process on every image (see the docstring).
        proc = subprocess.Popen([str(pi), "-c", "import time; time.sleep(60)"],
                                executable=sys.executable,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            # Bounded poll on the CENSUS ITSELF — never a widened or loosened match
            # (see the docstring: the reason is a legible red, not a measured race).
            # A census missing `-ww` still never matches (its rows are cut before the
            # basename), so the truncation pin keeps its discriminating power: it takes
            # the FULL deadline and fails, it does not go green.
            deadline = time.time() + _LONG_PATH_POLL_BUDGET_S
            poll_started = time.time()
            err_streak = 0
            streak_started: float | None = None
            pids: list[int] = []
            # ⛔ HOW MANY SAMPLES THE CENSUS ACTUALLY ANSWERED, and the first error it reported
            # (#12039 cycles 9 and 10). Whether the ALIVE arm's "invisible for 10 s of polling" is
            # true depends on this count, NOT on how long the errors took: a cumulative-time test
            # skipped the `-ww` pin after ONE 5 s error even when fifteen healthy samples had come
            # back without the fixture — a FAIL-OPEN, which is worse than the false red it fixed.
            healthy_samples = 0
            err_samples = 0
            first_census_err = ""
            while True:
                # ⛔ HOW LONG THE SAMPLE ITSELF TOOK, measured around the call (#12039 cycle 8, P1).
                # `pi_processes` reads the table through `_run(..., timeout=30)`
                # (`tools/fleet_state.py`), and `_run`'s `except Exception: return ""` folds a
                # `TimeoutExpired` into the SAME empty string as a rejected argument. So the error
                # TEXT cannot tell load from a broken invocation — but the sample's own DURATION
                # can: a census whose invocation is wrong fails in milliseconds, while a sample
                # that burned a timeout-scale budget failed on the wall clock. This is the only
                # signal in the loop that distinguishes them.
                sample_started = time.time()
                procs, err = fs.pi_processes()
                sample_s = time.time() - sample_started
                if err:
                    err_samples += 1
                    if not first_census_err:
                        first_census_err = err
                    # ⛔ A CENSUS ERROR IS NOT A PERMANENT SKIP — BUT ONE SAMPLE IS NOT A VERDICT
                    # (#12039, review rounds 3 and 4). This branch used to skip on ANY census
                    # error, which fused two different facts: a box whose `ps` cannot answer at
                    # all (legitimately not this test's business), and the CENSUS's own
                    # invocation failing on a box whose `ps` works — precisely the regression
                    # this test exists to catch, and it would stay green forever (neither static
                    # nodeid manifest carries this file; no skip-guard trips).
                    # The first fix for that redded on ONE `ps` failure out of ~100 samples,
                    # which is #5049 rule 1 inverted: `pi_processes()` folds a timeout, a refusal
                    # and an argument rejection into the same empty string, and a loaded runner
                    # can lose a single `ps`. So:
                    #   box cannot answer at all  → skip (unchanged);
                    #   one isolated error        → keep polling (the load class);
                    #   _CENSUS_ERROR_STREAK consecutive errors on a box whose `ps` answers → FAIL.
                    # ⛔ THE FIXTURE'S OWN DEATH IS CLASSIFIED FIRST (#12039 review round 5,
                    # P1). Asking the census before this made the outside-signal skip arm
                    # unreachable while the census was erroring: a child the BOX killed
                    # (SIGKILL from an OOM sweep) plus a simultaneous `ps` failure reported
                    # `HarnessDefect … the census's own invocation is what failed` — the exact
                    # inversion this PR exists to stop, and the header promises that kill as
                    # one of the FOUR skip paths. A dead child ends the poll; the four arms
                    # below classify it.
                    if _child_exited(proc):
                        break
                    # ⛔ THE SPAN IS MEASURED FROM THE STREAK, NOT FROM THE POLL (#12039 review
                    # cycle 6, P1). `poll_started` was set ONCE before the loop, so two
                    # consecutive errors arriving AFTER 2 s of HEALTHY polling satisfied
                    # `_CENSUS_ERROR_MIN_SPAN_S` — a 0.2 s `ps` blip, the load class rule 1
                    # protects, redded a REQUIRED shard, and the message below asserted a span
                    # the streak never had. MEASURED on the shipped form (hermetic probe, fake
                    # clock, ALIVE fixture): 50 healthy samples (~5 s), then exactly TWO
                    # consecutive errors, then healthy -> `HarnessDefect … spanning at least 2s`.
                    # The documented invariant is CONTINUOUS failure, so the clock starts at the
                    # streak's own first error and any successful sample resets it.
                    if err_streak == 0:
                        streak_started = time.time()
                    err_streak += 1
                    streak_span_s = time.time() - streak_started
                    # ⛔ A WALL-CLOCK EXPIRY INSIDE THE CENSUS IS NOT THE CENSUS'S FAULT
                    # (#12039 cycle 8, P1). Every census error carries the same string, so a `ps`
                    # that ran into `_run`'s 30 s timeout and a `ps` that REJECTED an argument are
                    # indistinguishable by TEXT. Calling the timeout a census fault reds a
                    # REQUIRED shard for load — the #5049 rule-1 inversion this test exists to
                    # remove, one layer down. The sample's own DURATION separates them: a census
                    # whose invocation is wrong fails in milliseconds, a timed-out one burned
                    # `_run`'s whole budget. ⛔ CHECKED BEFORE THE CONJUNCTION, not inside it: a
                    # 30 s timeout spends the poll's entire budget on its FIRST sample, so the
                    # healthy-branch deadline `break` below would otherwise turn it into the
                    # ALIVE arm's FAIL before the count gate was ever reached (measured).
                    if sample_s >= _CENSUS_SAMPLE_TIMEOUT_S:
                        inconclusive(
                            f"the census itself spent {sample_s:.1f}s failing, so the error "
                            f"may be a `ps` timeout rather than the census's own invocation",
                            deadline_s=sample_s,
                            diagnosis=(
                                f"The error text is {err!r}, and `pi_processes` returns exactly "
                                f"that string for a timeout AND for a rejected argument, so the "
                                f"KIND is only visible in how long the sample took."),
                        )
                    if (
                        err_streak >= _CENSUS_ERROR_STREAK
                        and streak_span_s >= _CENSUS_ERROR_MIN_SPAN_S
                    ):
                        # The verdict is due NOW, so the `ps` control runs at most once per
                        # poll instead of once per erroring sample (review round 5, P2: the
                        # branch paid one subprocess per sample — ~100 in one budget).
                        if not _ps_answers():
                            pytest.skip(f"the process table is unreadable here: {err}")
                        raise HarnessDefect(
                            f"{_HARNESS_PREFIX}: the census reported an error on "
                            f"{err_streak} consecutive samples over {streak_span_s:.2f}s "
                            f"(the continuous-failure bound is {_CENSUS_ERROR_MIN_SPAN_S:g}s) "
                            f"on a box whose `ps` answers: "
                            f"{err!r} — `ps -p {os.getpid()} -o command=` answered, so the "
                            f"census's own invocation is what failed, which is this test's "
                            f"subject")
                    # ⛔ THE ERROR BRANCH ALSO ANSWERS TO THE POLL DEADLINE (review cycle 5, P2).
                    # It used to `continue` unconditionally, so on a continuously-erroring census
                    # the loop's ONLY exit was the count+span gate — and the SPAN half is a tunable
                    # constant. Raise that bound above the poll budget and the gate can never fire,
                    # the healthy branch that checks `deadline` is never reached, and the REQUIRED
                    # shard HANGS instead of reding: measured on a copy with
                    # `_CENSUS_ERROR_MIN_SPAN_S = 1e9`, the `census_error` selection was killed at
                    # 45 s having printed ZERO output. The poll has ONE budget and this branch now
                    # spends it the same way the healthy branch does — `break`, so a spent budget
                    # lands in the exits below and reaches a verdict.
                    # ⛔ NOT `inconclusive()` HERE, DELIBERATELY (cycle-8, after the `once_slow`
                    # arm landed): whether the expiry is the machine's or the census's is the
                    # CONJUNCTION's decision, and this branch is upstream of it. A skip raised here
                    # pre-empted `once_slow` — one erroring sample whose own `time()` pair spans the
                    # bound, which must stay the ALIVE arm because the COUNT half was never reached
                    # (measured: `1 failed` with the skip, `once_slow` receiving INCONCLUSIVE where
                    # AssertionError is required). Breaking instead lets the arms classify.
                    if time.time() >= deadline:
                        break
                    time.sleep(0.1)
                    continue
                err_streak = 0
                streak_started = None
                healthy_samples += 1
                pids = [p["pid"] for p in procs]
                # ⛔ A DEAD CHILD ENDS THE POLL (#12039 review finding 4a). Liveness was
                # consulted only AFTER the 10 s deadline burned, so on a box where the child
                # dies the conclusion available at the first sample cost the deadline plus ~100
                # `ps` invocations. The exits below are untouched — this only reaches them
                # sooner. `_child_exited` does not collect the status on purpose: reaping here
                # would empty the table row the diagnosis quotes.
                if proc.pid in pids or _child_exited(proc) or time.time() >= deadline:
                    break
                time.sleep(0.1)
            if proc.pid not in pids:
                # ⛔ FOUR EXITS, SPLIT ON WHAT ACTUALLY HAPPENED TO THE FIXTURE (#5049 rules 1
                # and 4; measured on `pi-runner-cell3-x64-1` 2026-11-23).
                # ⛔ ONE OF THEM IS A DELIBERATE DEPARTURE FROM RULE 1, AND IT IS STATED AS ONE.
                # Rule 1 forbids a fixed wall-clock budget from producing FAIL (`tests/_verdict.py`
                # :4-8: *"never a product-defect FAIL"*), and the `rc is None` exit below DOES fail
                # on a wall-clock expiry. Say that instead of pretending otherwise: the expiry there
                # is not a budget standing in for an outcome nobody can observe — the census has
                # already ANSWERED, negatively, about a subject the test can see is alive, so "not
                # reported while alive" is the measurement and `-ww` is its subject. A skip would
                # make that regression green forever. (Recorded as an `OVERRIDES:` line on #12039;
                # a reader holding `tests/_verdict.py` should read it as RAISED, not overlooked.)
                # A fixture that died on its own MUST fail as well, because the property was then
                # never testable. Those are different facts, and before this split the test could
                # not tell them apart:
                #
                #   FAILED … test_pi_processes_sees_a_real_long_path_lane — … invisible to the
                #   census for 10s of polling (pids seen: [1104635]; the table's row for the
                #   fixture pid read '[python] <defunct>')
                #
                # `[python] <defunct>` is the row a DEAD child leaves, and `pids seen` matching
                # some OTHER `pi` (the runner box itself runs one) is a healthy census. Ten
                # seconds of polling cannot make a dead process visible, so that red measured
                # what happened to the FIXTURE, not the `-ww` flag — and it redded a REQUIRED
                # shard on a diff (`tests/test_12184_shifted_cites.py`) that cannot reach this
                # file at all. WHICH exit a dead child gets depends on WHY it died, and the
                # returncode only says that much — its SIGN decides between "the box killed it"
                # and "it died on its own": `_OUTSIDE_SIGNALS` (SIGKILL/SIGTERM/SIGINT/SIGHUP)
                # is the box's doing; a CRASH signal and a clean exit are both the substrate's.
                # The four arms, in order below:
                #
                #   rc is None                    → ALIVE and unreported → AssertionError. THE
                #                                   `-ww` PIN, and the one arm that deliberately
                #                                   OVERRIDES #5049 rule 1 (see the block above and
                #                                   the `OVERRIDES:` line on #12039). If a reader
                #                                   is tempted to turn it into a skip "to satisfy
                #                                   rule 1", that is the override being reverted: a
                #                                   skip here makes the regression this pin exists
                #                                   for green forever.
                #                                   (Round 1 of this review caught exactly that
                #                                   version — a `poll() is not None` skip that
                #                                   sent a cleanly-exiting fixture green. Do not
                #                                   reinstate it.)
                #   rc < 0, -rc outside-signal    → inconclusive. Rule 1: the box, not the flag.
                #   rc < 0, any other signal      → HarnessDefect. Rule 4: the interpreter
                #                                   crashed, so the substrate never ran the
                #                                   command.
                #   rc >= 0                       → HarnessDefect. Rule 4: it exited on its own
                #                                   under a `sleep(60)` payload, so it never ran.
                row = _row_for_pid(proc.pid)
                rc = proc.poll()
                if rc is None and healthy_samples == 0:
                    # ⛔ THE CENSUS NEVER ANSWERED, SO NOTHING HERE CAN JUDGE THE `-ww` FLAG (#12039
                    # cycles 9 and 10). Measured band: one erroring sample costing 9.5 s..29 s falls
                    # through the count+span conjunction (`err_streak == 1`), spends the deadline,
                    # and used to land in the ALIVE arm — which then asserted `invisible for 10s of
                    # polling` after ONE sample, `pids seen: []` when the census never answered, and
                    # the `-ww` cause while dropping `err`. Rule 1: the budget ran out and the census
                    # gave no answer, so the outcome is unknown; this is INCONCLUSIVE and it quotes
                    # what the census actually said.
                    # ⛔ KEYED ON `healthy_samples == 0`, NOT on cumulative error TIME. A time test
                    # skipped a GENUINE `-ww` regression after one 5 s error even when fifteen
                    # healthy samples had come back without the fixture — a fail-open, and it
                    # falsified the `OVERRIDES:` scope on #12039, which justifies the ALIVE FAIL
                    # with "the census has already answered NEGATIVELY". Measured verdict flip on
                    # the time test at 4.9 s with 15 healthy samples; the count test keeps the FAIL.
                    inconclusive(
                        "a long-path `pi` lane observed by a census that never answered",
                        deadline_s=_LONG_PATH_POLL_BUDGET_S,
                        diagnosis=(
                            f"Every one of the poll's {err_samples} census sample(s) failed — the "
                            f"first reported {first_census_err!r} — so there is no healthy sample "
                            f"to judge the `-ww` flag by and the error itself is the evidence; the "
                            f"table's row for the fixture pid read {row!r}."),
                    )
                if rc is None:
                    # ALIVE and unreported after the full deadline: the census, or the `-ww`
                    # flag, is broken. This is the assertion the pin exists for.
                    err_note = (
                        f" ({err_samples} of {healthy_samples + err_samples} samples failed and "
                        f"the first reported {first_census_err!r})" if err_samples else "")
                    raise AssertionError(
                        f"a lane whose argv[0] is {str(pi)!r} was ALIVE and invisible to the "
                        f"census for {_LONG_PATH_POLL_BUDGET_S:.0f}s of polling (pids seen: "
                        f"{pids}; the table's row "
                        f"for the fixture pid read {row!r}){err_note} — a narrow/`-ww`-less census "
                        f"truncates "
                        f"the argv[0] on every sample, so this is the flag removed or the matcher "
                        f"broken")
                if rc < 0 and -rc in _OUTSIDE_SIGNALS:
                    # A signal from OUTSIDE the fixture — the OOM killer, an orphan sweep, the
                    # shard's `timeout -s INT`. That is evidence about the machine, never about
                    # the census — #5049 rule 1.
                    inconclusive(
                        "a long-path `pi` lane visible to the census",
                        deadline_s=_LONG_PATH_POLL_BUDGET_S,
                        diagnosis=(
                            f"The fixture process {proc.pid} was killed by "
                            f"{_signal_name(-rc)} after {(time.time() - poll_started):.2f}s of "
                            f"the poll (its budget was {_LONG_PATH_POLL_BUDGET_S:g}s), before "
                            f"the census could observe it; the table's row read {row!r} and the "
                            f"census matched {pids!r}. A killed child has no argv[0] left to "
                            f"match, so the property was never observable and this expiry is "
                            f"evidence about the box, not the census."),
                    )
                if rc < 0:
                    # ⛔ A CRASH IS NOT THE BOX'S DOING (#5049 rule 4). `SIGSEGV`/`SIGABRT`/
                    # `SIGBUS`/`SIGILL`/`SIGFPE` is the CHILD aborting — the same defect class
                    # as the clean-exit arm below, and skipping it would make a crashing
                    # fixture green on EVERY run: a false PASS that no instrument reds (neither
                    # static nodeid manifest carries this file, and the INCONCLUSIVE family
                    # deliberately trips no skip-guard). Only signals the box sends are the
                    # machine's; an abort belongs to the substrate.
                    raise HarnessDefect(
                        f"{_HARNESS_PREFIX}: the fixture child CRASHED on "
                        f"{_signal_name(-rc)} before the {_LONG_PATH_POLL_BUDGET_S:.0f}s "
                        f"deadline "
                        f"(table row read {row!r}) — an abort is the TEST SUBSTRATE at fault, "
                        f"neither the box nor the census.")
                # ⛔ rc >= 0 is NOT the box and NOT the census: the child exited ON ITS OWN.
                # `time.sleep(60)` cannot return inside the poll's budget, so a clean exit means
                # the fixture never slept at all — a HARNESS defect, which
                # `tests/_verdict.py` rule 4 keeps a FAIL on purpose. The docstring above cites
                # exactly this class as a CAUGHT bug (the `ubuntu-26.04` uutils multi-call
                # `sleep` that printed `coreutils: unknown program 'pi'` and exited 1).
                # Skipping it here would make a permanently-refusing fixture green-by-skip on
                # every run: a false PASS, and nothing reds it — neither static manifest carries
                # this file, and the INCONCLUSIVE family deliberately trips no skip-guard.
                raise HarnessDefect(
                    f"{_HARNESS_PREFIX}: the fixture child exited rc={rc} on its own before "
                    f"the {_LONG_PATH_POLL_BUDGET_S:.0f}s deadline — `time.sleep(60)` cannot do "
                    f"that, so the "
                    f"fixture never ran its command (table row read {row!r}). This is the TEST "
                    f"SUBSTRATE at fault, not the census: the child must stay alive for the "
                    f"poll to observe it.")
        finally:
            proc.terminate()
            proc.wait(timeout=10)
    finally:
        shutil.rmtree(long_dir, ignore_errors=True)


class _FakeClock:
    """A clock that advances a fixed step only when the code under test asks the time.

    Monkeypatched over this module's `time` binding, so `time.sleep` costs nothing and the
    loop's own `time.time()` calls are the only thing that moves it — which is what makes
    "did the poll end EARLY?" measurable without waiting ten real seconds.
    """

    START = 1_000.0

    def __init__(self, step_s: float) -> None:
        self.now = self.START
        self.step_s = step_s

    def time(self) -> float:
        self.now += self.step_s
        return self.now

    def sleep(self, _s: float) -> None:
        return None


class _FakeChild:
    """A stand-in for the fixture's `Popen`: a scripted `poll()`, no process behind it."""

    def __init__(self, rc: int | None) -> None:
        # A pid that is NOT THIS PROCESS's CHILD, so `os.waitid` raises ECHILD
        # (`ChildProcessError`, `[Errno 10] No child processes`) and `_child_exited` takes its
        # documented `poll()` fallback.
        self.pid = 4_000_001
        self._rc = rc

    def poll(self) -> int | None:
        return self._rc

    def die_with(self, rc: int) -> None:
        """Become a DEAD child from now on — ALIVE until this is called.

        The `death_after_streak` arm needs a child that is live while the census streak
        qualifies and dies on the sample the count+span gate would fire: the only state in which
        the ORDER of `_child_exited` against that gate is observable (#12039 cycle 10, P2).
        """
        self._rc = rc

    def terminate(self) -> None:
        return None

    def wait(self, timeout: float | None = None) -> int:
        return 0 if self._rc is None else self._rc


def _verdict_or_fail(expect, why: str, call):
    """Call the fixture and RETURN the exception it raised, requiring the EXPECTED kind.

    ⛔ A SKIP IS NOT A VERDICT, AND `pytest.raises` CANNOT SEE ONE. `pytest.skip.Exception`
    derives from `OutcomeException`/`BaseException`, not from `AssertionError` — measured:
    `issubclass(pytest.skip.Exception, AssertionError) is False`
    (`Skipped.__mro__` -> `Skipped, OutcomeException, BaseException, object`). So an arm that
    expects `AssertionError` or `HarnessDefect` and instead gets a skip does NOT fail: the
    `Skipped` escapes `pytest.raises(expect)` and THIS guard test is reported as **SKIPPED**,
    i.e. green. That is precisely the false PASS these arms exist to pin — the file's own
    comments note that neither static nodeid manifest carries it and the INCONCLUSIVE family
    trips no skip-guard — and it was MEASURED on the shipped form of this test: mutating the
    ALIVE arm back to `pytest.skip(...)` gave `3 passed, 1 skipped` with the pin-green, and
    the pre-fix census skip left the `persistent/ps-answers` and `once` params skipped.

    So in every verdict-bearing arm an escaping skip is converted into a FAILURE. The
    skip-expecting arm is unchanged (that is its verdict, and `why` still discriminates it).
    """
    if expect == "skip":
        with pytest.raises(pytest.skip.Exception) as excinfo:
            call()
        return excinfo.value
    try:
        with pytest.raises(expect) as excinfo:
            call()
    except pytest.skip.Exception as exc:
        pytest.fail(f"the code SKIPPED where {expect.__name__} is required ({why}): {exc}")
    return excinfo.value


@pytest.mark.parametrize(
    ("rc", "expect", "marker", "why"),
    [
        (None, AssertionError, "was ALIVE and invisible to the census", "the -ww pin still fails"),
        (
            -9, "skip", "was killed by SIGKILL (9)",
            "SIGKILL from outside is the box (rule 1) — ⛔ the ARM-SPECIFIC phrase, not just the "
            "`INCONCLUSIVE [#5049]` family prefix (cycle-11 F2): the never-answered skip carries "
            "that prefix too, so a family-only marker cannot tell this arm from the one that "
            "fires when the span bound is raised past the poll budget",
        ),
        (
            -15, "skip", "was killed by SIGTERM (15)",
            "SIGTERM is the box too — an orphan sweep or the shard's own `timeout -s TERM` "
            "(cycle-8 P2: only `-9` was driven, so the set could shrink silently). The marker is "
            "arm-specific for the same reason as `-9`: the two signals must not be "
            "interchangeable",
        ),
        (-11, HarnessDefect, "the fixture child CRASHED on", "SIGSEGV is the substrate (rule 4)"),
        (1, HarnessDefect, "exited rc=", "a clean exit is the substrate (rule 4)"),
    ],
)
def test_long_path_census_expiry_splits_on_the_fixture_exit_kind(monkeypatch, rc, expect, marker, why) -> None:
    """The expiry's FOUR exits, driven without spawning anything (#12039; #5049 rules 1 and 4).

    Both directions of the split this PR exists to get right are pinned here, and neither needs
    the real fixture: a DEAD fixture must not red a required shard (the 2026-11-23 incident),
    and a fixture that dies ON ITS OWN — a clean exit under a `time.sleep(60)` payload, or a
    crash signal — must not go green by skip (the false PASS the first fix nearly shipped, which
    the `rc >= 0` arm guards and the crash arm guards from the other side).

    ⛔ EACH ARM CARRIES TWO MARKERS WITH TWO DIFFERENT JOBS (#12039 review cycles 2 and 3).
    The ARM-SPECIFIC phrase is the DISCRIMINATOR; the family marker is a separate CONTRACT. Both
    substrate arms used to assert only `HARNESS DEFECT [#5049]`, which discriminates nothing —
    measured: DELETING the crash arm (so a SIGSEGV is reported as "exited rc=-11 on its own …
    the fixture never ran its command") left all four params GREEN, because the shared marker
    pinned "a fixture death FAILs" and never "a crash is diagnosed AS a crash". So the crash
    arm now asserts the phrase only it can produce (`CRASHED on`) and the clean exit asserts
    `exited rc=`. ⛔ THE TWO OUTSIDE-SIGNAL SKIP ARMS HAD THE SAME DEFECT ONE CYCLE LATER
    (cycle-11 F2): they asserted `INCONCLUSIVE [#5049]`, which `inconclusive()` prepends to ALL
    FOUR skip exits in the function — so with `_CENSUS_ERROR_MIN_SPAN_S` raised past ~7.84 the
    poll breaks before the child dies and the NEVER-ANSWERED skip satisfies this row's marker,
    silently retiring the arm. They now assert `was killed by SIGKILL (9)` / `… SIGTERM (15)`,
    the text only their own arm interpolates. The family marker is asserted SEPARATELY below,
    for every arm, so an arm cannot drop the prefix merely to look unique.

    The elapsed-time assertion pins the poll's EARLY BREAK (`_child_exited`, `be6161b7`): a dead
    child must end the loop on the first sample, not after the poll budget. The ALIVE arm is the
    control — it still burns the whole deadline AND still fails, which is why the early break
    cannot convert the `-ww` pin into a skip.
    """
    module = sys.modules[__name__]
    clock = _FakeClock(step_s=0.1)
    child = _FakeChild(rc)

    class _Completed:
        stdout = ""

    monkeypatch.setattr(module, "time", clock)
    monkeypatch.setattr(module.fs, "pi_processes", lambda: ([], None))
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: child)
    # `_row_for_pid` shells out to `ps`; stub it so the arm stays hermetic. It is the DIAGNOSTIC
    # here and the CAPABILITY WITNESS at the two gate sites, so one stub covers both roles.
    monkeypatch.setattr("subprocess.run", lambda *a, **k: _Completed())

    raised = _verdict_or_fail(
        expect, why, lambda: test_pi_processes_sees_a_real_long_path_lane(monkeypatch))
    assert marker in str(raised), why
    if expect is HarnessDefect:
        # The arm-specific `marker` above DISCRIMINATES the two substrate arms; the family marker
        # is a separate contract every `HarnessDefect` carries (`tests/_verdict.py`), so assert
        # both — distinct arms must not be allowed to drop the family prefix to look unique.
        assert _HARNESS_PREFIX in str(raised), (
            "every HarnessDefect carries the family marker, whatever its own arm-specific text")
    elif marker.startswith("was killed by"):
        # …and the same two-jobs rule for the outside-signal SKIP arm (cycle-11 F2): the phrase
        # above is the DISCRIMINATOR, this prefix is the #5049 rule-1 family contract.
        assert INCONCLUSIVE_PREFIX in str(raised), (
            "every outside-signal skip carries the INCONCLUSIVE family marker, whatever its own "
            "arm-specific text (#5049 rule 1)")
    elapsed = clock.now - _FakeClock.START
    if rc is None:
        assert elapsed >= _LONG_PATH_POLL_BUDGET_S, (
            "the ALIVE arm must still burn the full deadline and still fail")
    else:
        assert elapsed < _LONG_PATH_POLL_BUDGET_S, (
            "a dead child must end the poll on the first sample, not at the poll budget")


@pytest.mark.parametrize(
    ("census_kind", "ps_answers", "child_rc", "expect", "marker", "why"),
    [
        (
            "persistent", True, None, HarnessDefect, "HARNESS DEFECT [#5049]",
            "a census that keeps failing on a box whose `ps` answers is the census",
        ),
        (
            "persistent", False, None, "skip", "the process table is unreadable here",
            "no usable `ps` on this box is that box's limitation, not the census's",
        ),
        (
            "death_after_streak", True, -9, "skip", "was killed by SIGKILL (9)",
            "a child that dies on the very sample where the census streak has ALREADY qualified "
            "is classified by the FIXTURE's death, not by the census — `_child_exited` must stay "
            "ABOVE the count+span conjunction. ⛔ THE ORDERING WAS PINNED BY NOTHING (cycle-10 "
            "P2): the plain `persistent`/`-9` row has the child dead from sample 1, so both "
            "orders break before the gate is reached and moving the check BELOW the conjunction "
            "left the whole file green (measured here: the ordering mutant applied with this arm "
            "removed -> 183 passed). Here it reds: the child is live "
            "while the streak starts and dies exactly where the gate would fire, so the shipped "
            "order yields the outside-signal skip and the swapped order a HarnessDefect — the "
            "2026-11-23 misattribution this PR exists to remove. ⛔ THE MARKER IS ARM-SPECIFIC "
            "(cycle-11 F2): it used to be the `INCONCLUSIVE [#5049]` family prefix, which the "
            "NEVER-ANSWERED skip also carries — so a span bound large enough to break the poll "
            "before the death sample (`_CENSUS_ERROR_MIN_SPAN_S >= 7.84`, premise (b) below) "
            "satisfied this row through the WRONG exit and the ordering pin went quietly vacuous",
        ),
        (
            "once", True, None, AssertionError, "was ALIVE and invisible to the census",
            "ONE isolated `ps` death is the load class (#5049 rule 1) and must not red the shard",
        ),
        (
            "once_slow", True, None, "skip", "observed by a census that never answered",
            "ONE counting sample whose streak span the CLOCK steps past the bound — without the "
            "COUNT half, a >= _CENSUS_ERROR_MIN_SPAN_S gap between the streak's own two `time()` "
            "reads reds a required shard (cycle-7: dropping the count left 175 passed; cycle-8: "
            "the mechanism is the clock, NOT `ps` cost — `streak_started` is taken after "
            "`pi_processes()` returns, so a slow `ps` is spent before the streak clock starts). "
            "The verdict is INCONCLUSIVE, not the ALIVE FAIL, because a clock that slow exhausts "
            "the poll before a healthy sample is ever taken — so this arm is the COUNT pin and "
            "`never_answered` is the SKIP pin; deleting the count flips THIS one to HarnessDefect",
        ),
        (
            "costly_once", True, None, AssertionError, "was ALIVE and invisible to the census",
            "ONE expensive erroring sample followed by HEALTHY samples that never see the fixture "
            "must stay the ALIVE FAIL: the census DID answer, so the `-ww` regression is live. ⛔ "
            "This is the cycle-10 P1 pin — a skip keyed on cumulative error TIME flipped exactly "
            "this shape to INCONCLUSIVE with 15 healthy samples (measured), i.e. a fail-open that "
            "retires the pin the whole test exists for",
        ),
        (
            "never_answered", True, None, "skip", "observed by a census that never answered",
            "ONE erroring sample costing almost the whole poll budget breaks before a healthy "
            "sample is taken, so the census never ANSWERED — it used to land in the ALIVE arm, "
            "which then asserted 'invisible for 10s of polling' after ONE sample, 'pids seen: []' "
            "when the census never answered, and dropped `err` (cycle-9 P1, the 9.5s..29s band). "
            "⛔ Scope is `healthy_samples == 0`: with healthy samples back and no fixture the "
            "`-ww` pin still FAILS (cycle-10 P1 — a cumulative-time test skipped that, a "
            "fail-open)",
        ),
        (
            "persistent_slow", True, None, "skip", "may be a `ps` timeout",
            "a census sample that consumes the SPAN BOUND to fail is a wall-clock expiry inside "
            "`pi_processes` (its `_run` timeout), i.e. load — rule 1 says INCONCLUSIVE, and "
            "calling it the census's own invocation was the cycle-8 P1",
        ),
        (
            "persistent_beyond_budget", True, None, "skip", "observed by a census that never answered",
            "a span bound the poll budget cannot contain must still reach a VERDICT: the error "
            "branch spends the budget and breaks, so the loop cannot spin (cycle-6 P2: deleting "
            "that break left the file fully green, and a constant bump then HANGS the shard)",
        ),
        (
            "late_pair", True, None, AssertionError, "was ALIVE and invisible to the census",
            "a burst that satisfies the ERROR COUNT but not the SPAN, arriving AFTER "
            "_CENSUS_ERROR_MIN_SPAN_S of healthy polling, is still the load class — the span "
            "belongs to the STREAK, not to the poll (cycle-6 P1)",
        ),
        (
            "persistent", True, -9, "skip", "was killed by SIGKILL (9)",
            "a fixture the BOX killed is classified by its own arm, whatever the census did — "
            "arm-specific text for the same reason as `death_after_streak` (cycle-11 F2)",
        ),
    ],
)
def test_long_path_census_error_is_the_census_only_when_it_persists(
    monkeypatch, census_kind, ps_answers, child_rc, expect, marker, why
) -> None:
    """The census ERROR path: neither a permanent skip nor a one-sample failure (#12039).

    The skip this replaces was a false PASS (the regression the test exists to catch would have
    stayed green forever), and the first fix for it was the opposite error — one `ps` lost under
    load redded a required shard, because `pi_processes()` folds a timeout, a refusal and an
    argument rejection into the same empty string. This pins BOTH directions and the load case:

      * persistent error, box's `ps` answers → `HarnessDefect` (a FAIL, with the family marker);
      * persistent error, no usable `ps`      → skip (the box cannot answer at all);
      * ONE error, then recovery              → the ordinary ALIVE arm, i.e. no verdict from the
                                                error at all (#5049 rule 1: load is not a defect);
      * persistent error AND the box killed the fixture → the outside-signal SKIP. The fixture's
                                                own death is classified FIRST; asking the census
                                                first turns a box-caused kill into a census FAIL,
                                                which is the 2026-11-23 class this PR removes.

    The harness also pins the streak's SPAN: the fake clock advances one `step_s` per `time()`
    call (`0.1 s`, except the two arms whose own premise sets it — `once_slow`'s and
    `death_after_streak`'s DERIVED steps, which are what make ONE sample span the bound and are
    how the COUNT half stays pinned), so the verdict may only fire after
    `_CENSUS_ERROR_MIN_SPAN_S` of simulated failure, and the assertion below fails a
    sample-count-only rule. Hermetic throughout: no child is spawned, `ps` never runs, and the
    clock only advances when the code asks it.
    """
    module = sys.modules[__name__]
    # ⛔ NOT A GLOBAL STEP: `once_slow` reads a DESCHEDULING gap — the two adjacent `time()` reads
    # inside the streak straddle the bound while the sample itself was instant — so its clock is
    # what is stepped. `persistent_slow` reads a `ps` TIMEOUT instead, where the cost is inside the
    # census call, and spends the clock there rather than here: a per-call step cannot express the
    # difference, and conflating the two is what let a `ps` timeout stay a FAIL (cycle 8, P1).
    # ⛔ DERIVED FROM THE CONSTANTS, never the literal 2.5 (#12039 cycle 10): a hard step retires
    # the COUNT-half pin the moment `_CENSUS_ERROR_MIN_SPAN_S` is raised past it — measured, at
    # `_CENSUS_ERROR_MIN_SPAN_S = 2.6` with the count half deleted the whole file stayed green.
    # ⛔ AND DERIVED FROM THE PREMISE, NOT FROM A RATIO (#12039 cycle 10, P1). `* 1.25` alone holds
    # only premise (a) — one sample's own `time()` pair clears the bound — and it went RED on the
    # CORRECT implementation for every `_CENSUS_ERROR_MIN_SPAN_S` in 0.2 .. 1.33 (measured on
    # copies, implementation UNMUTATED, only the constant changed). Premise (b) is that the FIRST
    # erroring sample spends the poll's WHOLE budget, so `healthy_samples` stays 0 and the
    # verdict is this row's skip: that needs `_LONG_PATH_ERROR_SAMPLE_TIME_CALLS x step >=
    # _LONG_PATH_POLL_BUDGET_S`, because the reads taken before the loop plus the first sample's
    # own reads are what carry the clock to `deadline`. Without (b) the poll took a HEALTHY sample
    # and landed in the ALIVE arm where this row requires the skip — a false red produced by
    # LOWERING a documented load-sensitivity constant, the same #5049 rule-1 inversion cycle 9
    # fixed on the raising axis.
    # ⛔ AND BOTH SIDES READ THE SAME TWO CONSTANTS (#12039 cycle 11, F1). The assertion here used
    # to be `assert 6 * once_slow_step_s >= 10.0` beside a step whose `max()` second term was
    # `10.0 / 6 + 0.1` — `assert max(x, y) >= y`, true for EVERY positive `_CENSUS_ERROR_MIN_SPAN_S`
    # and decoupled from the body's own budget literal, so changing the body's `deadline` left the
    # premise green while the arm graded a poll it no longer described. There is no STATIC form of
    # (b) that can fail, because the `max()` below is itself the enforcement of (b) (measured:
    # typoing its divisor 6 -> 7 left all 184 green, premise (a)'s term dominating) — so the claim
    # is not written as an assertion that cannot fail, as agent-infra#3442 rule 3 requires.
    # WHAT IS FALSIFIABLE IS THE BEHAVIOUR: drop the second term (or move the body's budget off
    # `_LONG_PATH_POLL_BUDGET_S`) and `once_slow` takes a HEALTHY sample and lands in the ALIVE
    # arm where this row requires the never-answered skip — the row REDS. Measured on a copy:
    # second term dropped at `_CENSUS_ERROR_MIN_SPAN_S = 0.6` -> 1 failed (`[once_slow-…]`). So the
    # premise (b) pin is the row itself, and it is stated here instead of shadowed by a tautology.
    once_slow_premise_a_step_s = _CENSUS_ERROR_MIN_SPAN_S * 1.25
    assert once_slow_premise_a_step_s > _CENSUS_ERROR_MIN_SPAN_S, (
        "premise (a): the `once_slow` step must place ONE erroring sample's own `time()` pair "
        "past _CENSUS_ERROR_MIN_SPAN_S, or the COUNT half is unpinnable")
    once_slow_step_s = max(
        once_slow_premise_a_step_s,
        _LONG_PATH_POLL_BUDGET_S / _LONG_PATH_ERROR_SAMPLE_TIME_CALLS + 0.1,
    )
    # ⛔ `death_after_streak` NEEDS ITS OWN STEP, from its own premise (#12039 cycle 10, P2): the
    # child dies on the sample where the streak QUALIFIES, so the span the conjunction would read
    # there is five `time()` calls long and must already clear the bound — otherwise the ordering
    # this arm exists to pin is never exercised and the arm is vacuous. The `+ 0.1` is margin for
    # the reads taken before the loop.
    death_step_s = _CENSUS_ERROR_MIN_SPAN_S / _LONG_PATH_STREAK_TIME_CALLS + 0.1
    # ⛔ PREMISE (a) IS **STATED, NOT ASSERTED** — AND THE CONSTANT IT LEANS ON IS UNPINNED
    # (#12039 cycle 13, F1). The obvious guard here is
    # `_LONG_PATH_STREAK_TIME_CALLS * death_step_s >= _CENSUS_ERROR_MIN_SPAN_S`, and it is a
    # TAUTOLOGY: expand the left side and it reduces to
    # `_CENSUS_ERROR_MIN_SPAN_S + 0.1 * _LONG_PATH_STREAK_TIME_CALLS >= _CENSUS_ERROR_MIN_SPAN_S`,
    # i.e. `0.1 * K >= 0` — true for EVERY span and every positive read count, because the divisor
    # cancels against its own product. It was shipped that way under the claim "Derived, so raising
    # the constant cannot silently make it so", which is exactly inverted: deriving the step FROM
    # the constant is what makes the assertion unfalsifiable on that axis (agent-infra#3442 rule 3
    # — an assertion that cannot vary under ANY mutation of the diff is dead weight that actively
    # misleads the next reader into thinking the guard is covered).
    # ⛔ SO `_LONG_PATH_STREAK_TIME_CALLS` HAS NO STATIC PIN — and, unlike the `once_slow` premise
    # (b) above, its typo is NOT survivable. This premise is therefore STATED, not asserted, and
    # the absence of a pin is named rather than papered over with a cleverer assertion that also
    # cannot fail. MEASURED on copies at this head, restored after each run:
    #   K = 8, implementation unmutated                     -> `184 passed` (the assertion: blind)
    #   K = 8, `_child_exited` moved BELOW the conjunction   -> `184 passed`
    #        ^ the row goes VACUOUS: `death_step_s` is 0.35 s, the streak spans `5 * 0.35 = 1.75 s
    #          < 2.0` at the death sample, the gate is not satisfied, and the moved-below check then
    #          meets the already-dead child ON THAT SAME SAMPLE and breaks to the same outside-signal
    #          SKIP the shipped order produces — so both orderings agree and nothing discriminates.
    #   K = 5, same mutant                                   -> `1 failed`  (the row IS the pin)
    # ⛔ MEASURE THE MUTANT YOU NAME (#12039 cycle 13): a "reorder" mutant that DELETES the check
    # instead reports `2 failed` at BOTH values, because nothing observes the death at all — and that
    # is what made an earlier version of this comment claim the typo was survivable. It is not.
    # WHAT IS FALSIFIABLE IS THE BEHAVIOUR, at the shipped value: move `_child_exited` below the
    # conjunction and the `death_after_streak` row REDS (`1 failed` — the PR body's own number).
    # That is this premise's pin — stated here instead of shadowed by a tautology, exactly as
    # `once_slow`'s premise (b) was corrected one cycle ago (block above). Premise (b) below DOES
    # stay an assertion: there both sides are independent constants, so it can fail.
    # (#12039 cycle 11, F2, still honoured for (b): at `_CENSUS_ERROR_MIN_SPAN_S >= 7.84` the poll
    # broke on the FIRST sample (clock 11.68 vs deadline 11.67), `child.die_with()` never ran,
    # `healthy_samples == 0` and the NEVER-ANSWERED skip satisfied the row's family-only marker —
    # the ordering pin retired itself on the one axis a maintainer actually moves, UP. The row's
    # marker is arm-specific now (see the parametrization), and this line reds the FILE with the
    # derivation to repair rather than letting the arm grade a different exit.)
    assert _LONG_PATH_ERROR_SAMPLE_TIME_CALLS * death_step_s < _LONG_PATH_POLL_BUDGET_S, (
        "premise (b): the death sample must arrive BEFORE the poll's deadline, or the fixture is "
        "never killed where the gate would fire and this row grades the never-answered skip "
        "instead (measured: the break happens on sample 1 from _CENSUS_ERROR_MIN_SPAN_S = 7.84). "
        "Re-derive death_step_s for the new bound — do not delete the arm")
    if census_kind == "once_slow":
        arm_step_s = once_slow_step_s
    elif census_kind == "death_after_streak":
        arm_step_s = death_step_s
    else:
        arm_step_s = 0.1
    clock = _FakeClock(step_s=arm_step_s)
    # `death_after_streak` must start ALIVE and die MID-POLL: its subject is the ORDER of the
    # child check against the census conjunction, and that order is only observable while the
    # child is still live as the streak qualifies.
    child = _FakeChild(None if census_kind == "death_after_streak" else child_rc)
    err = "`ps -ww -axo pid=,ppid=,uid=,command=` produced nothing"
    calls = {"n": 0}
    # DERIVED from the constants, never literal (see the `late_pair` arm below): a literal burst
    # pins today's numbers, so raising `_CENSUS_ERROR_STREAK` — the natural direction for a
    # load-sensitivity constant — silently retires the pin instead of reding.
    step_s = clock.step_s
    # `burst` = how many consecutive errors: at least the count `_CENSUS_ERROR_STREAK` so the COUNT
    # condition is always reachable.
    burst = max(2, _CENSUS_ERROR_STREAK)
    # ⛔ THE BURST IS PLACED BY SIMULATED TIME, NOT BY A SAMPLE INDEX (#12039 cycle 9, P1). The old
    # placement was `late = 2 + int(MIN_SPAN / step_s)` — a SAMPLE index derived as if one sample
    # cost one `time()` call — while the poll's deadline is SIMULATED TIME. The implementation's
    # per-sample call count has changed twice (one, then three, then five for an erroring sample),
    # and each change silently moved the burst: at `_CENSUS_ERROR_MIN_SPAN_S = 3.1` the placement
    # put it past the poll's break, so `assert calls["n"] > started_at + burst` fired on the
    # CORRECT implementation — a FALSE RED from raising a documented load-sensitivity constant, the
    # #5049 rule-1 inversion in the one arm that pins rule 1. MEASURED on copies at the head that
    # carried it, implementation UNMUTATED: span 2.0/2.5/3.0 green, 3.1 RED, 3.2/3.3 RED. Waiting
    # for the CLOCK instead makes the placement independent of that count, which is the whole point.
    burst_state: dict[str, int | None] = {"left": 0, "started_at": None}

    def _census():
        calls["n"] += 1
        if census_kind == "persistent":
            return [], err
        if census_kind == "persistent_beyond_budget":
            # ⛔ THE CAP IS THE INSTRUMENT, NOT THE SUBJECT (cycle-6 P2). With a span bound the
            # budget cannot contain, the count+span gate can NEVER fire, so the only thing that
            # ends this poll is the error branch's own deadline check. Without it this loop never
            # ends, and a hang is indistinguishable from a slow box — so the cap converts that into
            # a RED. It cannot masquerade as a pass: the post-condition requires the ALIVE arm's
            # marker, and this AssertionError does not carry it.
            assert calls["n"] < 500, (
                "the error branch never spent the poll budget: 500 consecutive erroring samples "
                "with a span bound the budget cannot contain means the poll cannot reach a "
                "verdict — it HANGS, which is the failure this arm exists to catch")
            return [], err
        if census_kind == "once_slow":
            # ⛔ THE MECHANISM IS THE CLOCK, NOT `ps` (#12039 cycle 8, P2). `streak_started` is
            # taken AFTER `pi_processes()` returns, so a slow or hung `ps` is spent BEFORE the
            # streak clock starts and contributes nothing to the span. What can reach the bound
            # on ONE counting sample is a >= `_CENSUS_ERROR_MIN_SPAN_S` gap between the streak's
            # two ADJACENT `time()` reads — a descheduled process or a wall-clock step — and the
            # fake clock is the only affordable way to place it. `err_streak` is 1, so the COUNT
            # half is the only thing between this and a `HarnessDefect` on a required shard.
            return ([], err) if calls["n"] == 1 else ([], None)
        if census_kind == "costly_once":
            # ONE erroring sample that costs most of the poll budget, then a HEALTHY census that
            # simply never sees the fixture. `healthy_samples` is therefore >= 1: the census
            # answered, so the `-ww` pin must still FAIL (#12039 cycle 10, P1).
            if calls["n"] == 1:
                clock.now += 5.0
                return [], err
            return [], None
        if census_kind == "never_answered":
            # ONE erroring sample that costs almost the whole poll budget, so the poll's own budget
            # check breaks before a healthy sample is ever taken — `healthy_samples` stays 0. That is
            # the 9.5s..29s band on a loaded runner: too cheap to reach `_CENSUS_SAMPLE_TIMEOUT_S`,
            # far too expensive to leave the "10 s of polling" claim true. ⛔ Deliberately NOT the
            # "one costly error then 15 healthy samples" shape: there the census DID answer, the
            # `-ww` pin must still FAIL, and a test that skipped it would pin a fail-open.
            if calls["n"] == 1:
                clock.now += 9.8
                return [], err
            return [], None
        if census_kind == "persistent_slow":
            # One sample that COSTS `_run`'s whole subprocess budget and then fails — the shape of
            # a `ps` that ran into `_run(..., timeout=30)` (`tools/fleet_state.py`), whose
            # `except Exception: return ""` folds a `TimeoutExpired` into the SAME empty string as
            # a broken invocation. Modelled by SPENDING the clock INSIDE the sample, not by raising
            # the per-call step: `sample_s` is `time.time() - sample_started` around this call, so
            # the spend is what a real 30 s timeout looks like, while the per-sample step stays
            # 0.1 s and `once_slow` (a descheduling gap, not a slow `ps`) stays distinguishable.
            clock.now += _CENSUS_SAMPLE_TIMEOUT_S
            return [], err
        if census_kind == "late_pair":
            # Healthy samples first, then a burst of consecutive errors, then healthy again. The
            # burst STARTS once the POLL's own simulated clock is past `_CENSUS_ERROR_MIN_SPAN_S`
            # (see `burst_state` above), which is what makes the old gate's from-`poll_started`
            # span already over the bound when the burst begins — it raises `HarnessDefect` on a
            # required shard, while the fixed gate measures the span from the STREAK and keeps
            # polling.
            # ⛔ BOTH the placement AND the length are DERIVED (measured, #12039). They were the
            # literals 40..42 — a burst of exactly two — so raising `_CENSUS_ERROR_STREAK` to 3
            # made the COUNT condition unreachable: the case degenerated to the deadline arm and
            # the from-`poll_started` mutant passed IDENTICALLY to the correct implementation
            # (measured on throwaway copies: streak 3 → `6 passed` for BOTH; streak 2 →
            # `1 failed, 5 passed` for the mutant). A pin that goes QUIET on a constant bump is
            # the same false-PASS class this PR removes.
            # ⛔ AND THE CLOCK IS FROZEN ACROSS THE BURST so its SPAN is structurally ZERO, not
            # merely "one sample". MEASURED per-sample cost of the implementation's own `time()`
            # calls on this very poll, by line trace: the FIRST erroring sample costs FIVE (the
            # `sample_started` read, the `streak_started` read, the span read, the error branch's
            # own deadline check, and the next `sample_started`) and every later one FOUR, so the
            # streak's span reaches `step_s + 4(k - 1) * step_s` at its k-th erroring sample. At
            # `_CENSUS_ERROR_STREAK = 20` that is `0.1 + 19 x 0.4 = 7.7 s`, past the 2.0 s bound —
            # MEASURED with the freeze removed: the CORRECT implementation trips (`1 failed`,
            # reporting `over 7.70s`), so the freeze is required, not a flourish. A burst long
            # enough to reach a large `_CENSUS_ERROR_STREAK` would otherwise
            # trip the span bound in the CORRECT implementation too — the same silent retirement,
            # moved from the count axis to the span axis. Freezing makes the arm independent of
            # BOTH constants: the mutant's from-`poll_started` span is frozen at >= the bound
            # (fires), the streak's own span is frozen at 0 (does not).
            # (This block has now been re-measured TWICE — it cited `Counter({1: 99, 2: 1})` before
            # the borrow-into-the-error-branch deadline check existed, then a 3/2 split before the
            # sample-duration reads landed. Both times the numbers were true at the commit that
            # wrote them and false one commit later, which is why the method is stated here.)
            # ⛔ AND ONLY WHILE THE SPAN BOUND STAYS BELOW THE POLL'S OWN 10 s DEADLINE (#12039
            # cycle 8, P2): the premise is ASSERTED above, not assumed — at
            # `_CENSUS_ERROR_MIN_SPAN_S = 9.8` the placement runs past the break and this arm
            # reds the CORRECT implementation.
            if burst_state["started_at"] is None:
                # Still in the healthy prefix. Wait until the POLL has genuinely run past the span
                # bound in SIMULATED TIME — measured on the clock, so no derivation has to assume
                # what a sample costs. The margin is THREE steps: TWO cover the reads taken before
                # the loop (`deadline`, `poll_started`, both visible a few lines above), so a
                # from-`poll_started` span is strictly GREATER than the bound at the burst's first
                # sample, and the third is slack. MEASURED at `+ step_s`: span 1.90 < 2.0, i.e. the
                # mutant did NOT fire and this arm went SILENT — the margin is load-bearing, not
                # decoration.
                if clock.now - _FakeClock.START < _CENSUS_ERROR_MIN_SPAN_S + 3 * step_s:
                    return [], None
                burst_state["started_at"] = calls["n"]
                burst_state["left"] = burst
            if burst_state["left"]:
                burst_state["left"] -= 1
                clock.step_s = 0.0
                return [], err
            if clock.step_s == 0.0:
                clock.step_s = step_s
            return [], None
        if census_kind == "death_after_streak":
            # ⛔ THE FIXTURE DIES WHERE THE GATE WOULD FIRE (#12039 cycle 10, P2). Sample 1 errors
            # while the child is ALIVE, so the streak STARTS and no arm can classify the death
            # yet; on sample 2 the child dies (SIGKILL — an OOM sweep's first signal) on the same
            # sample the count+span conjunction has just been satisfied. Shipped order:
            # `_child_exited` breaks first, so the four exits below classify a BOX-caused kill and
            # the verdict is the outside-signal INCONCLUSIVE. Move that check below the
            # conjunction and this sample raises `HarnessDefect` instead — a required shard red
            # for what the box did, at the exact moment the census was also broken.
            if calls["n"] == 1:
                return [], err
            assert child_rc is not None
            child.die_with(child_rc)
            return [], err
        return ([], err) if calls["n"] == 1 else ([], None)

    class _Completed:
        stdout = ""

    monkeypatch.setattr(module, "time", clock)
    monkeypatch.setattr(module.fs, "pi_processes", _census)
    monkeypatch.setattr(module, "_ps_answers", lambda: ps_answers)
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: child)
    # `_row_for_pid` shells out to `ps`; stub it so the arm stays hermetic. It is the DIAGNOSTIC
    # here and the CAPABILITY WITNESS at the two gate sites, so one stub covers both roles.
    monkeypatch.setattr("subprocess.run", lambda *a, **k: _Completed())
    if census_kind == "persistent_beyond_budget":
        # Override the bound AFTER the placement state is built (it describes `late_pair`) and
        # BEFORE the call, because the gate being unreachable inside the budget is precisely the
        # case.
        monkeypatch.setattr(module, "_CENSUS_ERROR_MIN_SPAN_S", 1e9)

    raised = _verdict_or_fail(
        expect, why, lambda: test_pi_processes_sees_a_real_long_path_lane(monkeypatch))
    assert marker in str(raised), why
    if expect is HarnessDefect:
        assert clock.now - _FakeClock.START >= _CENSUS_ERROR_MIN_SPAN_S, (
            "the census verdict must wait for the streak to SPAN a real interval — two 0.1 s "
            "samples are the load class (#5049 rule 1), not a broken census"
        )
    elif marker.startswith("was killed by"):
        # The outside-signal skip's phrase DISCRIMINATES it from the never-answered skip, which
        # carries the same family prefix; the prefix is a separate rule-1 contract (cycle-11 F2).
        assert INCONCLUSIVE_PREFIX in str(raised), (
            "every outside-signal skip carries the INCONCLUSIVE family marker, whatever its own "
            "arm-specific text (#5049 rule 1)"
        )
    if census_kind == "once":
        assert calls["n"] > _CENSUS_ERROR_STREAK, "a single error must not decide the verdict"
    if census_kind == "late_pair":
        assert burst_state["started_at"] is not None, (
            "the burst never started: `_CENSUS_ERROR_MIN_SPAN_S` was not reachable inside the "
            "poll's own budget, so this arm never placed the blip and graded the deadline arm "
            "instead — raise the poll's budget or lower the bound (#12039)")
        assert calls["n"] > burst_state["started_at"] + burst, (
            "the poll must keep running after a burst that satisfies the COUNT but not the SPAN "
            f"(started_at={burst_state['started_at']}, burst={burst}) — a census verdict here is "
            "the #5049 rule-1 false red this arm pins against")


def test_the_long_path_test_header_names_the_census_error_fail_arm() -> None:
    """The docstring's skip/FAIL taxonomy must name the arm the code actually has (#12039 cycle 4).

    Review cycle 4 at `70caad8392` measured this drift in the VERY block the previous commit
    repaired: the header said the test skips where the process table cannot be read at all and
    listed only two FAIL arms, while the code FAILs the census error on a box whose ``ps``
    answers — a third arm. A reader who re-derives the skip rule from that header writes
    ``pytest.skip(...)`` on any census error and re-creates the permanent green the fix removes:
    a false PASS that no instrument reds, since neither static nodeid manifest carries this file
    and the INCONCLUSIVE family trips no skip-guard.

    Text, not behaviour, on purpose. The ARMS' behaviour is pinned at their own raise sites by
    `test_long_path_census_expiry_splits_on_the_fixture_exit_kind` and
    `test_long_path_census_error_is_the_census_only_when_it_persists`; what nothing else pins is
    the prose a reader derives from. Both tokens are absent from the header this finding was
    raised against — measured at `70caad8392`: `'_CENSUS_ERROR_STREAK' not in doc` and
    `'_ps_answers' not in doc` — and the streak token is still absent at `16b24c83e9`, which
    narrowed the skip but never named the arm. So each guard reds on the version of the drift it
    exists for rather than passing vacuously.
    """
    doc = test_pi_processes_sees_a_real_long_path_lane.__doc__ or ""
    # ⛔ GRADE THE TAXONOMY, NOT THE WHOLE DOCSTRING (#12039 cycle 9, P1). The count paragraph below
    # ENUMERATES every token this guard asserts — it lists them to say the guard pins them by NAME —
    # so asserting against the whole docstring made the guard VACUOUS: measured at this head,
    # deleting the ENTIRE four-arm taxonomy (all four FAIL classifications and the skip paths, 1171
    # chars) left it PASSING, because that enumeration alone satisfies all six tokens. A guard that
    # cannot fail is the false-PASS class this PR exists to remove, so the split is part of the pin:
    # the boundary must exist, or this guard has nothing to grade and must say so.
    taxonomy, marker, _count_paragraph = doc.partition("THE COUNT IS PART OF THE CONTRACT")
    assert marker, (
        "the header no longer separates its taxonomy from the count paragraph, so this guard "
        "cannot tell the prose that PUBLISHES the rule from the prose that DESCRIBES the guard — "
        "re-derive the boundary rather than deleting it (#12039)")
    # The third FAIL arm, named by the constant that decides it — and the STREAK is the point:
    # one lost `ps` under load is the box (#5049 rule 1), not the census.
    assert "_CENSUS_ERROR_STREAK" in taxonomy, (
        "the header stopped naming the census-error FAIL arm, so a reader re-deriving the skip "
        "rule from it can reinstate `pytest.skip(...)` on any census error (#12039)")
    # And the census-error SKIP is narrowed, never "the table cannot be read" on its own.
    assert "_ps_answers" in taxonomy, (
        "the header's census-error skip must also require `_ps_answers()` to come back empty; "
        "`where the process table cannot be read at all` alone is the permanent-green drift")
    # And the FAIL boundary is a CONJUNCTION, not a sample count (#12039 cycle 4). Without this
    # token the header can state "N consecutive samples" alone, and a reader re-deriving the gate
    # from it writes the count-only form — the mutation that redded `late_pair` with `over 0.20s`.
    assert "_CENSUS_ERROR_MIN_SPAN_S" in taxonomy, (
        "the header stopped naming the SPAN half of the census-error FAIL boundary, so the rule "
        "it publishes is the count-only gate the code does not implement (#12039)")
    # ⛔ AND THE TWO SKIP-PATH CONSTANTS (#12039 cycle 9, P2). A third skip path landed and the
    # header went on saying "SKIPS on TWO paths", with the count explicitly declared part of the
    # contract one paragraph below it. The count is guarded here the only way it can be: by NAME,
    # because a guard cannot count what a future reader is free to re-word.
    for const in ("_CENSUS_SAMPLE_TIMEOUT_S", "healthy_samples"):
        assert const in taxonomy, (
            f"the header stopped naming {const}, so a reader re-deriving the verdict rules from "
            f"it cannot see that a slow / budget-consuming census SKIPS instead of reding — the "
            f"drift the paragraph calling the count 'part of the contract' exists to forbid "
            f"(#12039)")
    # And the ONE arm that deliberately fails against #5049 rule 1 must stay declared as an
    # override, not left as three contradictory glosses (#12039 cycle 7). A reader holding
    # `tests/_verdict.py` — which says a wall-clock expiry is *never* a product-defect FAIL — has
    # nothing but this word to tell a raised departure from an oversight, and the "fix" for an
    # oversight is exactly the skip that makes the `-ww` regression green forever.
    assert "OVERRIDES" in taxonomy, (
        "the header stopped declaring that the ALIVE arm OVERRIDES #5049 rule 1, so a reader "
        "holding `tests/_verdict.py` reads a FAILING wall-clock expiry as an oversight and "
        "turns it into a skip — the permanent green this file's arms exist to prevent (#12039)")
    # ⛔ AND THE COUNT ITSELF, NOT ONLY THE NAMES (#12039 cycle 10, P2). The paragraph below declares
    # the count "part of the contract" and NOTHING compared it to the code: the header said "SKIPS
    # on THREE paths" while enumerating four, twice, one sentence after the declaration. Names
    # cannot catch that — so the declared number is parsed out of the header and checked against the
    # skip exits actually present in the function, by AST.
    words = {"ZERO": 0, "ONE": 1, "TWO": 2, "THREE": 3, "FOUR": 4, "FIVE": 5}
    tail = taxonomy.split("SKIPS on ", 1)
    assert len(tail) == 2, "the header stopped declaring how many skip paths the test has"
    word = tail[1].split()[0].strip("*_ `")
    assert word in words, f"unreadable skip-path count {word!r} in the header"
    fn = next(
        n for n in ast.walk(ast.parse(Path(__file__).read_text()))
        if isinstance(n, ast.FunctionDef)
        and n.name == "test_pi_processes_sees_a_real_long_path_lane")
    exits = 0
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if name in ("skip", "inconclusive"):
            exits += 1
    assert words[word] == exits, (
        f"the header declares {words[word]} skip paths but the test body has {exits} skip exits "
        f"(pytest.skip + inconclusive) — the count is contractual and this is the drift the "
        f"paragraph above it forbids (#12039)")


def test_ps_answers_is_the_control_it_claims_to_be(monkeypatch) -> None:
    """`_ps_answers` must ANSWER in both directions — nothing else ran its body (#12039 cycle 8).

    This one function decides the two verdicts the census-error arm can reach: `False` skips
    ("the box cannot read its process table") and `True` FAILs ("the census's own invocation is
    what failed") — which is the regression this PR exists to catch. No test exercised it: the
    arm stubs it (`monkeypatch.setattr(module, "_ps_answers", lambda: ps_answers)`), so measured
    on copies, forcing the body to `return False` (EVERY persistent census error becomes a skip —
    the permanent green this PR removes) or to `return True` (the rule-1 inversion on a box with
    no `ps`) left the whole file passing. The realistic breakage is the silent one: spelling the
    field `-o "commandx="` makes `ps` exit non-zero with EMPTY stdout and raise NOTHING, so the
    `except (OSError, SubprocessError)` arm never fires and `bool("")` decides.

    So the BODY is pinned here rather than stubbed, in all three shapes: empty stdout (the silent
    non-zero exit), a real row, and a `ps` that cannot be executed at all. Hermetic on purpose —
    stubbing `subprocess.run` pins the parsing and every arm without depending on the box's `ps`,
    which is exactly what `test_child_exited_reads_the_status_without_reaping_the_row` does for
    `_child_exited`'s real branch.
    """
    import subprocess

    class _Completed:
        def __init__(self, stdout: str) -> None:
            self.stdout = stdout

    def _raise(*_a, **_k):
        raise OSError(2, "No such file or directory")

    with monkeypatch.context() as patch:
        patch.setattr(subprocess, "run", lambda *a, **k: _Completed(""))
        assert _ps_answers() is False, (
            "a `ps` that exits non-zero with empty stdout must read as `no usable ps`: it raises "
            "nothing, so `bool('')` is the only thing that decides, and `True` here FAILs a "
            "required shard on a box whose `ps` cannot read the table")

    with monkeypatch.context() as patch:
        patch.setattr(subprocess, "run", lambda *a, **k: _Completed("python -c pass\n"))
        assert _ps_answers() is True, (
            "a `ps` that DOES answer our own pid must read as `ps works`, or every census error "
            "is excused as the box's and the regression stays green forever")

    with monkeypatch.context() as patch:
        patch.setattr(subprocess, "run", _raise)
        assert _ps_answers() is False, (
            "a `ps` that cannot be executed at all is `no usable ps`, not an exception from the "
            "control that is supposed to classify the census's error")

    # ⛔ AND THE REAL INVOCATION MUST STILL ANSWER (the half a stub cannot see). The breakage this
    # arm most plausibly suffers is a source-level typo in the `ps` ARGUMENTS — `-o command=`
    # spelled one character wrong exits non-zero with empty stdout and raises NOTHING — and every
    # stub above replaces that invocation, so a stubbed test cannot red it. Measured on a copy:
    # with `commandx=` the stubbed directions all pass. This direction is why it reds.
    import shutil

    # ⛔ THE TEXTUAL PIN RUNS ON EVERY BOX, SO IT COMES FIRST — ABOVE `shutil.which` (#12039 cycle
    # 12 P2, cycle 13 F2). It needs no `ps` at all, so a `ps`-less box must still be able to red a
    # mistyped invocation; behind any availability gate it silently stops being a pin there.
    # It pins the WHOLE argv, not two substrings: `-p <pid>` is the selector the function's own
    # docstring rests on ("asked about our OWN pid so it cannot depend on the fixture"), and
    # dropping just that token left this file `184 passed` (measured), so substrings are not the
    # property. `_row_for_pid`'s own argv carries its own pin, below.
    invocation = inspect.getsource(_ps_answers)
    assert (
        '["ps", "-p", str(os.getpid()), "-o", "command="]' in invocation
    ), (
        "`_ps_answers` stopped asking `ps -p <our pid> -o command=`. Dropping ONLY the `-p` "
        "selector leaves the tokens this test used to look for intact while the control starts "
        "answering from the whole table, and every census error would then be excused as the "
        f"box's. Its invocation is: {invocation!r}")

    # ⛔ AND THE WITNESS'S ARGV, BY THE SAME RULE (#12039 cycle 14 P2). Without `-p` it answers
    # from the WHOLE table for any pid, which satisfies `assert row` in the `WNOWAIT` test even when
    # the child's status was collected — disarming the only pin on `_child_exited`'s real branch
    # (measured: `_child_exited → proc.poll()` plus a `-p`-less witness = `184 passed`).
    witness_invocation = inspect.getsource(_row_for_pid)
    assert (
        '["ps", "-ww", "-p", str(pid), "-o", "command="]' in witness_invocation
    ), (
        "`_row_for_pid` stopped selecting ONE pid: it answers from the whole table, so the "
        "`WNOWAIT` pin's `assert row` cannot fail and the capability witness answers for a table "
        f"it never read. Its invocation is: {witness_invocation!r}")

    if shutil.which("ps") is None:  # pragma: no cover — no `ps` to ask
        pytest.skip("no `ps` on this box: the positive direction cannot be measured")
    # ⛔ THE SKIP NEEDS A WITNESS THAT IS NOT THE PREDICATE UNDER TEST (#12039 cycle 13 P1). This
    # was `if not _ps_answers(): skip` plus `assert _ps_answers() is True`, i.e. the assert could
    # only fire if two adjacent calls flipped — so a BROKEN `_ps_answers` (the exact thing this
    # test exists to catch) read as "this box cannot answer" and SKIPPED a required-shard FAIL arm.
    # Measured: `_ps_answers`'s real call given `timeout=0.000001` → `182 passed, 2 skipped, 0
    # failed` here, against `1 failed` on the parent commit. The witness is now a DIFFERENT
    # invocation — `_row_for_pid(os.getpid())`, i.e. `ps -ww -p <pid> -o command=`: the SAME
    # question with `-ww`, on its own budget, through a probe that shares no code path with
    # `_ps_answers`. (It is NOT the census's `ps -ww -axo …` call; neither test makes that one.) A
    # table one probe can read while the other says it cannot is the CONTROL's breakage, and it
    # FAILS.
    answers = _ps_answers()
    if not answers and not _row_for_pid(os.getpid()):
        # pragma: no cover — a `ps` that exists and cannot answer
        pytest.skip(
            "`ps` exists but neither `-p <pid> -o command=` nor a full table read can answer, so "
            "this box cannot measure the positive direction; the invocation was pinned TEXTUALLY "
            "above")
    assert answers is True, (
        "`ps -p <our pid> -o command=` answered nothing while `_row_for_pid` — the same question "
        "with `-ww`, on its own budget — DID return our row, so the control is what broke: every "
        "census error would be excused as the box's and the regression this file pins would stay "
        "green")


def test_the_outside_signal_set_is_exactly_the_signals_the_box_sends() -> None:
    """`_OUTSIDE_SIGNALS` is the rule-1/rule-4 boundary, so its MEMBERSHIP must be pinned.

    A signal in this set SKIPS (the box killed the fixture — orphan sweep, OOM sweep, the shard's
    `timeout`); a signal outside it FAILS as a substrate crash. Shrinking the set therefore routes
    a box-caused kill into the crash arm — a FAIL on a required shard for something the box did,
    which is the inversion this PR exists to remove. Nothing pinned the membership: measured on a
    copy at the cycle-8 head, `_OUTSIDE_SIGNALS = frozenset({signal.SIGKILL})` left the whole file
    green (`175 passed`), because the parametrized expiry arm only drove `-9`. The `-15` case above
    now covers the second most likely signal behaviourally; this assertion covers the other two
    (`SIGINT` from an interrupt, `SIGHUP` from a dropped session), which no arm drives.
    """
    assert frozenset(
        {signal.SIGKILL, signal.SIGTERM, signal.SIGINT, signal.SIGHUP}) == _OUTSIDE_SIGNALS, (
        "`_OUTSIDE_SIGNALS` is the boundary between 'the box killed it' (skip, #5049 rule 1) and "
        "'the substrate crashed' (FAIL, rule 4). A member added here excuses a real crash; one "
        "removed FAILs a required shard for the box's own doing — state the change deliberately "
        "in the arms above rather than editing this set (#12039)")


def test_child_exited_reads_the_status_without_reaping_the_row() -> None:
    """`_child_exited` must NOT collect the status: the `ps` row is the diagnosis's evidence.

    `waitid(WNOWAIT)` over `Popen.poll()` is the whole reason the helper exists — `poll()` calls
    `waitpid()`, and a COLLECTED child leaves no row for the `HarnessDefect` message to quote
    (the 2026-11-23 incident's evidence is exactly that row: `'[python] <defunct>'`).

    ⛔ NOTHING ELSE IN THIS FILE EXERCISES THE REAL BRANCH (review cycle 6, P2). Every hermetic arm
    drives `_FakeChild`, whose pid `4_000_001` makes `os.waitid` raise `ChildProcessError`, so all
    of them take the documented `poll()` fallback — measured: mutating this helper to
    `return proc.poll() is not None` (i.e. reaping the evidence away) left the suite fully GREEN.
    (No totals quoted: every one this block has carried has gone stale within one commit, which is
    the same drift class the rest of this file keeps repairing.) So the property is pinned here
    with a REAL, short-lived child: exited, row still readable, and only THEN `poll()` returns the
    code.
    """
    import shutil
    import subprocess

    if shutil.which("ps") is None:  # pragma: no cover — no `ps` to read a row with
        pytest.skip("no `ps` on this box: the row this test grades cannot be read")
    # ⛔ AND THE SAME CAPABILITY GATE AS THE CONTROL, ON THE SAME INDEPENDENT WITNESS (#12039 cycle
    # 12 P2, cycle 13 P1). Without a gate, a `ps` that exists but cannot answer turns the row
    # assertion below into a required-shard FAIL for a box reason — the inversion this PR removes
    # everywhere else. Keyed on `_ps_answers()` ALONE the gate is a fail-open: a broken control
    # would skip this test too. `_row_for_pid` is the witness (same question with `-ww`, own
    # budget), so only a box where BOTH invocations come back empty skips.
    if not _ps_answers() and not _row_for_pid(os.getpid()):
        # pragma: no cover — a `ps` that exists and cannot answer
        pytest.skip(
            "`ps` exists but neither `-p <pid> -o command=` nor a full table read can answer, so "
            "the row this test grades cannot be read on this box")

    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    try:
        deadline_s = 10.0
        deadline = time.time() + deadline_s
        while not _child_exited(proc) and time.time() < deadline:
            time.sleep(0.05)
        # ⛔ A WALL-CLOCK EXPIRY IS NOT A FAIL (#5049 rule 1, review cycle 5 P2). This used to be
        # `assert _child_exited(proc), "the child exited but ..."`, so on a loaded box a
        # `python -c pass` child that had not exited within 10 s redded a REQUIRED shard with a
        # message asserting the helper was broken — the same rule-1 inversion this PR removes
        # everywhere else (the outside-signal arm above converts the identical situation). The
        # real pins below are untouched and still discriminate: mutating `_child_exited` back to
        # `return proc.poll() is not None` reds this test on the missing `ps` row (measured).
        if not _child_exited(proc):
            inconclusive(
                "a short-lived child's exit observed by `_child_exited` without reaping it",
                deadline_s=deadline_s,
                diagnosis=(
                    "The child was still running when the budget expired, so the `WNOWAIT` "
                    "probe was never exercised — a load observation, not evidence about the "
                    "helper."),
            )
        row = _row_for_pid(proc.pid)
        assert row, (
            "the exited child has no `ps` row, so its status was COLLECTED rather than "
            "observed — the `HarnessDefect` message would lose the evidence it quotes "
            "(`_child_exited` must keep `WNOWAIT`)")
        assert proc.poll() is not None, (
            "the status was not collectable after `_child_exited` read it, so the returncode "
            "the exit arms classify would be lost")
    finally:
        if proc.poll() is None:  # pragma: no cover — child still alive on a slow box
            proc.kill()
        proc.wait(timeout=10)
