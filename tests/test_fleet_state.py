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
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import fleet_state as fs

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
    monkeypatch.setattr(fs, "open_prs", lambda _repo, limit=400: [
        {"number": 700, "title": "contested", "headRefName": "fix/700-a",
         "headRefOid": "", "url": "u"}])
    monkeypatch.setattr(fs, "open_issues", lambda _repo, limit=800: [])
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
    p.write_text(json.dumps({"lanes": [lane]}))
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

def _fleet_build(monkeypatch, *, registry, live, measured, error="", overrides=None, by_sid=None):
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
    monkeypatch.setattr(fs, "open_prs", lambda _repo, limit=400: [])
    monkeypatch.setattr(fs, "open_issues", lambda _repo, limit=800: [])
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
