"""next_work picks the next unit of work — and the two ways a picker goes wrong.

WHY THIS FILE EXISTS
--------------------
The lane brief asked for a queue consumer that picks the next unit itself, with
**red-first** tests for the two defects that make a picker dangerous:

1. **it picks a unit someone else already holds** — two lanes on one PR, which is
   the duplicate-dispatch failure this fleet has paid for repeatedly (#3061,
   #2985 vs #3005);
2. **it reorders priority** — a P1 ranked above a P0 reverses the owner's ruling
   (*"beta ships when the p0 and p1 are merged"*) while looking like an
   improvement ("pick the closest to done first"). That is the silent-reversal
   class the `OVERRIDES` marker in the module docstring exists to prevent, and a
   test is the only thing that actually holds it.

Everything here runs against a SNAPSHOT — no network, no `gh`, no clock. That is
deliberate: the decision is a pure function of the measured input, so the
properties above are testable without a live repository, and the same snapshot
replays byte-identically (the idempotence proof the brief asks for).

WHAT IS PINNED
--------------
(a) a CLAIMED unit is never the pick, and its row still appears with the reason;
(b) P0 outranks P1 regardless of readiness — no aging (see the module OVERRIDES);
(c) the pick is IDEMPOTENT: same snapshot, same row, and the ordering is total so
    an input permutation cannot change it;
(d) an UNREADABLE surface refuses the pick (exit 2) instead of reporting freedom;
(e) a fold constituent is not a unit — the fold is (#10902) — detected from the
    LABEL the queue gate reads, or `batch_open.py`'s machine-readable line, and
    NEVER from the `batch(...): land #1 #2 #3` title;
(f) the position comes from the repo's OWN gate: untested → NEEDS-TEST,
    un-attested → NEEDS-REVIEW (never READY-TO-LAND), conflicted → NEEDS-FIX.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.next_work import (
    CONSTITUENT_LABEL,
    FOLD_LABEL,
    POSITIONS,
    Snapshot,
    build_units,
    next_command,
    render,
)
from tools.queue_resweep import parse_config

REPO = "daniel-ospina/tortoise"

#: A minimal gate carrying the same label vocabulary as the live `.mergify.yml`,
#: including the negated constituent condition #10902 added.
CONFIG_TEXT = """
queue_rules:
  - name: default
    queue_conditions:
      - base=main
      - -draft
      - -label=superseded-by-fold
      - check-success=docs
      - check-success=test-isolation
"""

CFG = parse_config(CONFIG_TEXT)
REQUIRED = list(CFG.required_checks)


def runs(*conclusions: str, sha: str = "a" * 40) -> list[dict]:
    """`n` check-runs, one per required check, in the given conclusions."""
    out = []
    for i, conclusion in enumerate(conclusions):
        name = REQUIRED[i]
        if conclusion in ("queued", "in_progress"):
            out.append({"id": i + 1, "name": name, "status": conclusion, "conclusion": None,
                        "app": {"slug": "github-actions"}})
        else:
            out.append({"id": i + 1, "name": name, "status": "completed",
                        "conclusion": conclusion, "app": {"slug": "github-actions"}})
    return out


def all_green(sha: str = "a" * 40) -> list[dict]:
    return runs(*(["success"] * len(REQUIRED)), sha=sha)


def snap(
    *,
    issues: list[dict] | None = None,
    pulls: list[dict] | None = None,
    check_runs: dict | None = None,
    attestation: dict | None = None,
    claims: dict | None = None,
    merge_tree: dict | None = None,
    local_holders: dict | None = None,
    unreadable: list[str] | None = None,
) -> Snapshot:
    return Snapshot.from_json(
        {
            "repo": REPO,
            "base": "main",
            "config_source": "origin/main:.mergify.yml",
            "gate": {
                "queue_name": CFG.queue_name,
                "base": CFG.base,
                "required_checks": list(CFG.required_checks),
                "required_labels": list(CFG.required_labels),
                "forbidden_labels": list(CFG.forbidden_labels),
            },
            "collected_at": 1793800000,
            "issues": issues or [],
            "pulls": pulls or [],
            "check_runs": check_runs or {},
            "attestation": attestation or {},
            "claims": claims or {},
            "merge_tree": merge_tree or {},
            "local_holders": local_holders or {},
            "unreadable": unreadable or [],
        }
    )


def issue(n: int, priority: str = "P0-blocker", title: str = "t") -> dict:
    return {"number": n, "title": title, "priority": priority}


def pull(
    n: int,
    *,
    head: str = "a" * 40,
    closes: list[int] | None = None,
    refs: list[int] | None = None,
    labels: list[str] | None = None,
    body: str = "",
    draft: bool = False,
    base: str = "main",
) -> dict:
    #: `base` is present because the COLLECTOR always sets it (`pr["base"]` from the
    #: PR list) and `unmet_conditions` reads `pr["base"]["ref"]`. Omitting it made
    #: every fixture emit `base=main (actual unknown)` — a structural refusal the
    #: gate really would name — which is why the position tests below must isolate
    #: the condition they are about.
    return {
        "number": n,
        "head": head,
        "draft": draft,
        "base": {"ref": base} if base else {},
        "labels": [{"name": lb} for lb in (labels or [])],
        "closing_issues": closes or [],
        "refs_issues": refs or [],
        "title": f"pr {n}",
        "fold_line": body,
    }


def units_for(s: Snapshot):
    return build_units(s, CFG)


def pick_of(s: Snapshot):
    return next((u for u in units_for(s) if u.pickable), None)


# ── (a) the headline: a claimed unit is never the pick ──────────────────────
def test_claimed_pr_is_never_picked():
    """Two ready units; the better-ranked one is held → the OTHER is picked.

    This is the duplicate-dispatch defect restated as a test: a picker that
    ignores `refs/claims` hands two lanes the same PR.
    """
    s = snap(
        issues=[issue(100), issue(200)],
        pulls=[pull(1001, closes=[100]), pull(1002, closes=[200])],
        check_runs={"a" * 40: all_green()},
        attestation={"1001": {"verdict": "head"}, "1002": {"verdict": "head"}},
        merge_tree={"1001": {"clean": True}, "1002": {"clean": True}},
        claims={"refs/claims/100-aaaaaaaaaaaa": {"verdict": "claim-held", "owner": "box-a:1234"},
                "refs/claims/pr-1001": {"verdict": "claim-held", "owner": "box-a:1234"}},
    )
    rows = units_for(s)
    held = next(u for u in rows if u.issue == 100)
    assert held.reason == "CLAIMED", held
    assert held.detail, "a claimed row must say why, not just be absent"
    assert not held.pickable
    assert next_command(held, REPO) is None, "a held unit must never carry a command"
    picked = pick_of(s)
    assert picked is not None and picked.issue == 200


def test_local_holder_branch_blocks_without_a_claim_ref():
    """Work that exists only on this checkout is a holder too (#4256)."""
    s = snap(
        issues=[issue(100)],
        pulls=[],
        local_holders={"issue:100": [".worktrees/fix-100-something"]},
    )
    (row,) = [u for u in units_for(s) if u.issue == 100]
    assert row.reason == "CLAIMED"
    assert "local:" in (row.holder or "")


def test_expired_lease_entry_still_blocks_the_pick():
    """A verdict this tool does not recognise is a HOLDER, never freedom.

    `claims.sh` owns the lease polarity (an unparseable lease is LIVE). This
    consumer must not re-derive it, so any verdict that is not explicitly
    `claim-absent` blocks — the fail-closed direction.
    """
    s = snap(
        issues=[issue(100)],
        pulls=[pull(1001, closes=[100])],
        check_runs={"a" * 40: all_green()},
        attestation={"1001": {"verdict": "head"}},
        merge_tree={"1001": {"clean": True}},
        claims={"refs/claims/pr-1001": {"verdict": "claim-owner-unparseable", "owner": "?"}},
    )
    (row,) = units_for(s)
    assert row.reason == "CLAIMED"


# ── (b) the second headline: priority is never reordered by readiness ───────
def test_p1_never_outranks_p0():
    """A P0 that is merely NO-PR still outranks a P1 that is READY-TO-LAND.

    This is the deliberate departure from the queue literature's *aging*: the
    owner's ruling is the P0/P1 set ships, so readiness must not promote a P1.
    """
    s = snap(
        issues=[issue(100, "P0-blocker"), issue(200, "P1-severe")],
        pulls=[pull(2002, closes=[200])],
        check_runs={"a" * 40: all_green()},
        attestation={"2002": {"verdict": "head"}},
        merge_tree={"2002": {"clean": True}},
    )
    picked = pick_of(s)
    assert picked is not None
    assert picked.issue == 100, f"P1 {picked.issue} was picked over the P0 — priority was reordered"
    assert picked.position == "NO-PR"


def test_priority_dominates_across_every_position_pair():
    """No (P1 position, P0 position) pair may invert — the property, not one case."""
    for p1_pos in POSITIONS:
        for p0_pos in POSITIONS:
            issues = [issue(100, "P0-blocker"), issue(200, "P1-severe")]
            pulls = [pull(1001, closes=[100]), pull(2002, closes=[200])]
            check_runs = {}
            attestation = {}
            merge_tree = {}
            if p0_pos == "NO-PR":
                pulls = [p for p in pulls if p["number"] != 1001]
            else:
                check_runs = {"a" * 40: _runs_for_position(p0_pos)}
            if p1_pos == "NO-PR":
                pulls = [p for p in pulls if p["number"] != 2002]
            else:
                # a second head for the P1's PR
                p1_head = "b" * 40
                for p in pulls:
                    if p["number"] == 2002:
                        p["head"] = p1_head
                check_runs[p1_head] = _runs_for_position(p1_pos)
            for pr, pos in ((1001, p0_pos), (2002, p1_pos)):
                if pos != "NO-PR":
                    merge_tree[str(pr)] = {"clean": pos != "NEEDS-FIX"}
                    attestation[str(pr)] = {"verdict": "head" if pos != "NEEDS-REVIEW" else "norecord"}
            s = snap(issues=issues, pulls=pulls, check_runs=check_runs,
                     attestation=attestation, merge_tree=merge_tree)
            picked = pick_of(s)
            assert picked is not None and picked.issue == 100, (
                f"P0 at {p0_pos} lost to P1 at {p1_pos}"
            )


def _runs_for_position(position: str) -> list[dict]:
    if position == "NEEDS-FIX":
        return runs("failure", *(["success"] * (len(REQUIRED) - 1)))
    if position == "NEEDS-TEST":
        return runs("in_progress", *(["success"] * (len(REQUIRED) - 1)))
    return all_green()


# ── (c) idempotence ────────────────────────────────────────────────────────
def test_same_snapshot_same_pick():
    s = _rich_snapshot()
    first = pick_of(s)
    second = pick_of(snap(**{k: v for k, v in json.loads(json.dumps(s.to_json())).items()
                            if k in {"issues", "pulls", "check_runs", "attestation",
                                     "claims", "merge_tree", "local_holders", "unreadable"}}))
    assert first is not None and second is not None
    assert first.key == second.key


def test_input_permutation_does_not_change_the_pick():
    """The ordering key is TOTAL, so construction order cannot decide the pick."""
    base = _rich_snapshot().to_json()
    forward = build_units(Snapshot.from_json(json.loads(json.dumps(base))), CFG)
    raw = json.loads(json.dumps(base))
    raw["issues"] = list(reversed(raw["issues"]))
    raw["pulls"] = list(reversed(raw["pulls"]))
    backward = build_units(Snapshot.from_json(raw), CFG)
    assert [u.key for u in forward] == [u.key for u in backward]
    # Stronger than the keys: the RENDERED output must be byte-identical too.
    # Measured 2026-11-04 — the pick never moved, but five `shares PR with` rows
    # named a different sharer depending on input order, so two consumers reading
    # the same queue printed different lines.
    a, _ = render(forward, CFG, Snapshot.from_json(json.loads(json.dumps(base))), repo=REPO, top=5)
    b, _ = render(backward, CFG, Snapshot.from_json(raw), repo=REPO, top=5)
    assert a == b


def _rich_snapshot() -> Snapshot:
    """A queue with a mix of every reason, for the ordering/idempotence properties."""
    return snap(
        issues=[
            issue(100, "P0-blocker", "held p0"),
            issue(101, "P0-blocker", "ready p0"),
            issue(200, "P1-severe", "ready p1"),
            issue(201, "P1-severe", "no-pr p1"),
        ],
        pulls=[
            pull(1001, closes=[100], head="1" * 40),
            pull(1011, closes=[101], head="2" * 40),
            pull(2001, closes=[200], head="3" * 40),
        ],
        check_runs={"1" * 40: all_green(), "2" * 40: all_green(), "3" * 40: all_green()},
        attestation={"1001": {"verdict": "head"}, "1011": {"verdict": "head"},
                     "2001": {"verdict": "head"}},
        merge_tree={"1001": {"clean": True}, "1011": {"clean": True}, "2001": {"clean": True}},
        claims={"refs/claims/issue-100": {"verdict": "claim-held", "owner": "box-b:9"}},
    )


# ── (d) an unreadable surface refuses the pick ─────────────────────────────
def test_unreadable_surface_refuses_the_pick(tmp_path: Path):
    """`could not check` is never `free` — exit 2, and the report says REFUSED."""
    s = _rich_snapshot()
    s.unreadable = ["claims.sh not found — the claim registry cannot be read"]
    path = tmp_path / "snap.json"
    path.write_text(json.dumps(s.to_json()), encoding="utf-8")
    rc = _run_main(["--from", str(path), "--repo", REPO])
    assert rc == 2, "an unreadable claim registry must not exit 0"
    text, _ = render(build_units(s, CFG), CFG, s, repo=REPO, top=5)
    assert "REFUSED" in text


def test_ahead_by_zero_branch_is_not_a_holder(tmp_path: Path):
    """#8175 restated: a branch with 0 commits ahead produced nothing and must not block.

    Measured on a hub with ~1470 local refs, reading every branch name as a claim
    marks almost the whole queue CLAIMED — the abandoned-claim defect that made
    a P1 undispatchable forever.
    """
    from tools.next_work import _local_holders

    repo = tmp_path / "r"
    repo.mkdir()
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}

    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True,
                       env={**__import__("os").environ, **env})

    git("init", "-q", "-b", "main")
    (repo / "f").write_text("x")
    git("add", "f")
    git("commit", "-qm", "base")
    git("update-ref", "refs/remotes/origin/main", "HEAD")
    # an ABANDONED branch naming issue 500: zero commits ahead
    git("branch", "fix/500-abandoned")
    # a LIVE branch naming issue 501: one commit ahead
    git("checkout", "-qb", "fix/501-live")
    (repo / "g").write_text("y")
    git("add", "g")
    git("commit", "-qm", "work")

    holders = _local_holders(repo)
    assert "issue:500" not in holders, f"an ahead-by-0 branch blocked the queue: {holders.get('issue:500')}"
    assert "issue:501" in holders, "a branch carrying real commits was not seen as a holder"


def test_absent_claim_entry_is_free_not_abstention():
    """The complement: a registry that was READ and does not hold the key is free."""
    s = _rich_snapshot()
    picked = pick_of(s)
    assert picked is not None and picked.reason != "CLAIMED"


# ── (e) folds (#10902 / #10909) ────────────────────────────────────────────
def test_constituent_label_makes_the_fold_the_unit():
    """A PR carrying `superseded-by-fold` is not a work unit; the fold is."""
    # `fold-constituents:` names PRs, not issues — that is `batch_open.py`'s
    # vocabulary ("Given a fold PR and its constituents", "closing a PR whose
    # work is NOT in the fold"), and using it the other way round would make the
    # declared list mean something a second tool does not.
    fold = pull(900, head="9" * 40, labels=[FOLD_LABEL],
                body="fold-constituents: 1001\n")
    constituent = pull(1001, closes=[100], labels=[CONSTITUENT_LABEL])
    s = snap(
        issues=[issue(100)],
        pulls=[fold, constituent],
        check_runs={"9" * 40: all_green(), "a" * 40: all_green()},
        attestation={"900": {"verdict": "head"}, "1001": {"verdict": "head"}},
        merge_tree={"900": {"clean": True}, "1001": {"clean": True}},
    )
    rows = units_for(s)
    const_row = next(u for u in rows if u.issue == 100)
    assert const_row.reason == "SUPERSEDED-BY-FOLD"
    assert const_row.fold_pr == 900
    assert next_command(const_row, REPO) is None
    fold_row = next(u for u in rows if u.kind == "fold")
    assert fold_row.fold_pr == 900
    # the fold inherits its constituent's priority rather than inventing one
    assert fold_row.priority == "P0-blocker"
    picked = pick_of(s)
    assert picked is not None and picked.kind == "fold"


def test_fold_title_alone_is_not_evidence():
    """The `batch(...): land #1 #2 #3` title is prose, and prose is refused.

    `tools/batch_open.py` states why: "Deriving the close list from that prose
    would close a PR the fold never carried."
    """
    prose_fold = pull(901, head="9" * 40, body="batch(combine-a): land #100 #101 as one tree")
    prose_fold["title"] = "batch(combine-a): land #100 #101 as one validated tree"
    s = snap(issues=[issue(100), issue(101)], pulls=[prose_fold])
    rows = units_for(s)
    assert not [u for u in rows if u.kind == "fold"], "a title was read as fold membership"
    assert {u.reason for u in rows if u.kind == "issue"} == {"NO-PR"}


def test_constituent_label_without_a_fold_still_blocks_and_says_so():
    """The label alone blocks the unit; it does not pretend to know which fold."""
    s = snap(issues=[issue(100)], pulls=[pull(1001, closes=[100], labels=[CONSTITUENT_LABEL])],
             check_runs={"a" * 40: all_green()},
             attestation={"1001": {"verdict": "head"}}, merge_tree={"1001": {"clean": True}})
    (row,) = units_for(s)
    assert row.reason == "SUPERSEDED-BY-FOLD"
    assert row.fold_pr is None
    assert any("no open fold" in d for d in row.detail)


def test_unlabelled_fold_is_reported_not_hidden():
    """`fold-constituents:` without the label still governs — and says the label is missing.

    Measured 2026-11-04 on the live repo: #10894 and #10702 are open folds with
    NO label and NO `fold-constituents:` line, so #10902's gate cannot see them.
    The consumer must not silently accept that; it names it on the row.
    """
    fold = pull(900, head="9" * 40, labels=[], body="fold-constituents: 1001")
    s = snap(issues=[issue(100)], pulls=[fold, pull(1001, closes=[100])],
             check_runs={"9" * 40: all_green()},
             attestation={"900": {"verdict": "head"}}, merge_tree={"900": {"clean": True}})
    fold_row = next(u for u in units_for(s) if u.kind == "fold")
    assert any("UNLABELLED" in d for d in fold_row.detail)


# ── (f) positions come from the repo's own gate ───────────────────────────
def test_untested_head_is_needs_test():
    s = snap(issues=[issue(100)], pulls=[pull(1001, closes=[100])],
             check_runs={"a" * 40: _runs_for_position("NEEDS-TEST")},
             attestation={"1001": {"verdict": "head"}}, merge_tree={"1001": {"clean": True}})
    (row,) = units_for(s)
    assert row.position == "NEEDS-TEST"
    assert row.reason == "NEEDS-TEST"


def test_failed_check_is_needs_fix():
    s = snap(issues=[issue(100)], pulls=[pull(1001, closes=[100])],
             check_runs={"a" * 40: _runs_for_position("NEEDS-FIX")},
             attestation={"1001": {"verdict": "head"}}, merge_tree={"1001": {"clean": True}})
    (row,) = units_for(s)
    assert row.position == "NEEDS-FIX"


def test_green_checks_without_attestation_is_never_ready_to_land():
    """Green CI is not a review. A merge needs a head-bound attestation."""
    s = snap(issues=[issue(100)], pulls=[pull(1001, closes=[100])],
             check_runs={"a" * 40: all_green()},
             attestation={"1001": {"verdict": "norecord"}},
             merge_tree={"1001": {"clean": True}})
    (row,) = units_for(s)
    assert row.position == "NEEDS-REVIEW", row
    assert row.reason == "NEEDS-REVIEW"


def test_unreadable_attestation_is_not_a_clean_record():
    s = snap(issues=[issue(100)], pulls=[pull(1001, closes=[100])],
             check_runs={"a" * 40: all_green()},
             attestation={"1001": {"verdict": "unreadable", "why": "record-review.sh not found"}},
             merge_tree={"1001": {"clean": True}})
    (row,) = units_for(s)
    assert row.position == "NEEDS-REVIEW"
    assert any("UNREADABLE" in d for d in row.detail)


def test_merge_conflict_is_needs_fix_and_unmeasured_is_not_clean():
    conflicted = snap(issues=[issue(100)], pulls=[pull(1001, closes=[100])],
                      check_runs={"a" * 40: all_green()},
                      attestation={"1001": {"verdict": "head"}},
                      merge_tree={"1001": {"clean": False, "why": "conflict: a.py"}})
    (row,) = units_for(conflicted)
    assert row.position == "NEEDS-FIX"

    unmeasured = snap(issues=[issue(100)], pulls=[pull(1001, closes=[100])],
                      check_runs={"a" * 40: all_green()},
                      attestation={"1001": {"verdict": "head"}},
                      merge_tree={"1001": {"clean": None, "why": "head not available locally"}})
    (row2,) = units_for(unmeasured)
    assert any("UNMEASURED" in d for d in row2.detail)


def test_a_refs_only_link_is_named_on_the_row():
    """#10534: 18 of 18 PRs naming a P0/P1 used `Refs`, which closes nothing."""
    s = snap(issues=[issue(100)], pulls=[pull(1001, refs=[100])],
             check_runs={"a" * 40: all_green()},
             attestation={"1001": {"verdict": "head"}}, merge_tree={"1001": {"clean": True}})
    (row,) = units_for(s)
    assert any("REFS" in d for d in row.detail)


# ── the label vocabulary must be the CONFIG's, not a memory of it ──────────
def test_labels_match_the_live_mergify_config():
    """A rename in `.mergify.yml` must not silently blind this reader."""
    root = Path(__file__).resolve().parent.parent
    text = (root / ".mergify.yml").read_text(encoding="utf-8")
    assert f"-label={CONSTITUENT_LABEL}" in text, (
        f"{CONSTITUENT_LABEL!r} is no longer the queue gate's constituent exclusion"
    )
    assert FOLD_LABEL in text, f"{FOLD_LABEL!r} is no longer the fold's label"


def test_every_unit_gets_a_command_or_a_reason_why_not():
    s = _rich_snapshot()
    for u in units_for(s):
        if u.pickable:
            assert next_command(u, REPO), f"{u.key} is pickable but names no command"
        else:
            assert next_command(u, REPO) is None


# ── helpers ────────────────────────────────────────────────────────────────
def _run_main(argv: list[str]) -> int:
    from tools import next_work

    return next_work.main(argv)

def test_rows_sharing_one_pr_are_named_as_one_unit():
    """One PR lands several issues (#10726 closes 7). Two rows, ONE action."""
    s = snap(
        issues=[issue(100), issue(101)],
        pulls=[pull(2001, closes=[100, 101])],
        check_runs={"a" * 40: all_green()},
        attestation={"2001": {"verdict": "head"}}, merge_tree={"2001": {"clean": True}},
    )
    rows = units_for(s)
    higher = next(u for u in rows if u.issue == 101)
    assert any("shares PR #2001 with #100" in d for d in higher.detail), higher.detail


def test_a_title_only_fold_is_flagged_but_never_becomes_a_unit():
    """The queue note is a REPORT; `build_units` must not read a title as membership."""
    prose = pull(901, head="9" * 40, body="batch: fold 32 open PRs into one tree")
    prose["title"] = "batch: fold 32 open tortoise PRs into one validated tree"
    s = snap(issues=[issue(100)], pulls=[prose])
    assert not [u for u in units_for(s) if u.kind == "fold"]
    from tools.next_work import _title_looks_like_fold_without_evidence

    assert _title_looks_like_fold_without_evidence(s) == [901]
    text, _ = render(units_for(s), CFG, s, repo=REPO, top=5)
    assert "QUEUE NOTE" in text and "INERT" in text
    assert "batch_open.py --fold" in text, "the note must name the remedy, not just the defect"

def test_no_clock_seam_is_documented_or_accepted():
    """The decision has NO clock input — and the CLI must not pretend otherwise.

    A reviewer on this change caught the first version documenting `--now` ("pin
    the lease clock") in four places while `build_units` never read it: `--from`
    with `--now 1000` and `--now 9999999999` produced byte-identical output, so
    the flag demonstrated nothing and the docstring claimed a determinism it did
    not implement. A claim of determinism that rests on an unused knob is worse
    than no claim, because it is the sentence a reader trusts. The honest shape is
    the stronger one: claim leases are resolved by `claims.sh` at COLLECTION time,
    the snapshot records the verdict, and no clock enters the decision at all.
    """
    with pytest.raises(SystemExit) as exc:
        _run_main(["--from", "/dev/null", "--now", "1"])
    assert exc.value.code != 0, "an undocumented-then-removed flag must not be silently accepted"
    from tools.next_work import main  # noqa: F401  (imported for its parser)

    help_text = subprocess.run(
        [sys.executable, "tools/next_work.py", "--help"],
        capture_output=True, text=True, cwd=Path(__file__).resolve().parent.parent,
    ).stdout
    options_block = help_text.split("options:", 1)[1].split("\n\n", 1)[0]
    assert "--now" not in options_block, "the parser must not accept a clock seam"
    assert "no clock" in help_text, (
        "the help must state the property that REPLACED the seam: no clock enters the decision"
    )
    # …and the MODULE DOCSTRING, which no test used to read, must not retain the
    # fossil: the first version said "given the same snapshot and the same clock",
    # which still advertises the clock as an input four lines above the sentence
    # that says it is not one.
    from tools.next_work import __doc__ as module_doc

    assert "same clock" not in module_doc, "the docstring still conditions the pick on a clock"
    assert "no clock cannot" not in module_doc
    assert "no clock enters the decision" in module_doc.lower()


def test_top_flag_actually_summarises_rows():
    """`--top N` must print N rows — not merely restate N in a footer numeral.

    Caught by a verifier on this change: the flag said "how many rows the footer
    summarises" while only changing the number printed in a sentence.
    """
    s = _rich_snapshot()
    text3, _ = render(build_units(s, CFG), CFG, s, repo=REPO, top=1)
    text5, _ = render(build_units(s, CFG), CFG, s, repo=REPO, top=99)
    assert "TOP 1 PICKABLE" in text3
    assert "TOP 3 PICKABLE" in text5  # 3 pickable of 4; `top` is capped by the population
    assert text3.count("\n  1. ") == 1
    assert text5.count("\n  1. ") == 1 and text5.count("\n  3. ") == 1
    assert text5.count("\n  ") > text3.count("\n  ")

# ── the gate is part of the SNAPSHOT, not of the process cwd ───────────────
def test_replay_never_reads_the_queue_config(tmp_path: Path, monkeypatch):
    """A replay must not touch `.mergify.yml` — not even to "confirm" it.

    Measured by a verifier on this change: the first replay ran
    `load_config_text(None)`, which shells `git show origin/main:.mergify.yml` in
    the PROCESS CWD. The same snapshot therefore picked #10602 from the repo root
    and #10017 from `/tmp` — two consumers, two rows, on identical input. That is
    the duplicate pick this tool exists to prevent, so the gate travels INSIDE the
    snapshot and any config read during a replay is now a hard error.
    """
    from tools import next_work

    def boom(*a, **k):
        raise AssertionError("a replay must not read .mergify.yml")

    monkeypatch.setattr(next_work, "load_config_text", boom, raising=True)
    s = _rich_snapshot()
    path = tmp_path / "snap.json"
    path.write_text(json.dumps(s.to_json()), encoding="utf-8")
    assert next_work.main(["--from", str(path), "--repo", REPO]) in (0, 1)


def test_a_snapshot_without_a_gate_is_incomplete_not_free(tmp_path: Path):
    """No gate ⇒ no position is provable ⇒ exit 2, never a confident pick."""
    raw = _rich_snapshot().to_json()
    raw["gate"] = None
    path = tmp_path / "snap.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    assert _run_main(["--from", str(path), "--repo", REPO]) == 2


def test_replay_position_matches_the_in_process_decision(tmp_path: Path):
    """The gate in the snapshot is the gate the decision used at collection."""
    s = _rich_snapshot()
    path = tmp_path / "snap.json"
    path.write_text(json.dumps(s.to_json()), encoding="utf-8")
    in_process = [(u.key, u.position, u.reason) for u in build_units(s, CFG)]
    replayed = build_units(Snapshot.from_json(json.loads(path.read_text())), CFG)
    assert in_process == [(u.key, u.position, u.reason) for u in replayed]


def test_absent_attestation_tool_is_an_unreadable_surface(tmp_path: Path, monkeypatch):
    """Exit 2 means the SURFACE is missing — not that a PR has no record.

    The docstring promised the attestation tool in the exit-2 list while
    `_attestation` only ever set a per-row verdict, so a box without
    `record-review.sh` exited 0. A `norecord` verdict stays exit 0 (a real
    answer); a MISSING TOOL does not.
    """
    from tools import next_work

    monkeypatch.setenv("RECORD_REVIEW_SH", str(tmp_path / "nope.sh"))
    monkeypatch.setenv("AGENT_INFRA_PATH", str(tmp_path / "no-infra"))
    s = Snapshot()
    next_work._attestation(tmp_path, REPO, 42, s)
    assert s.attestation["42"]["verdict"] == "unreadable"
    assert any("record-review.sh not found" in u for u in s.unreadable), s.unreadable


# ── the claim verdict, ACROSS the shell boundary (D3) ──────────────────────
def test_claim_verdicts_survives_the_claims_sh_exit_codes(tmp_path: Path):
    """The duplicate-dispatch defect, at the seam where it actually lived.

    `claims.sh` returns rc 1 for `claim-held` and rc 2 for `claim-unreadable`, so
    `v=$(claim_snapshot_verdict …) || v=unreadable` overwrote EVERY correct held
    verdict with `unreadable` (and lost the owner, because the globals die with
    the subshell). Measured by a verifier: a PR held by `box-a:4242` came out
    `unreadable`, was then read as free, and the tool printed a merge command for
    it at exit 0. This test crosses the boundary with a stub that implements the
    documented rc contract exactly, which no earlier test did.
    """
    from tools import next_work

    stub = tmp_path / "claims-stub.sh"
    stub.write_text(
        "\n".join(
            [
                "CLAIM_VERDICT=''; CLAIM_SNAPSHOT_OWNER=''",
                "claim_snapshot() { :; }",
                "claim_snapshot_verdict() {",
                # A faithful stub indexes refs/claims/<key> and matches field 1
                # EXACTLY, because that is what claims.sh does (claim_snapshot
                # writes ${CLAIM_NS}/${key}; the reader compares $1 == ref). An
                # earlier stub keyed on bare words and therefore validated the
                # shell boundary while baking in the WRONG addressing — the
                # tool asked for `pr-10852` and got `claim-absent` for a held PR.
                '  case "$2" in refs/claims/pr-held)', 
                '    CLAIM_VERDICT=claim-held; CLAIM_SNAPSHOT_OWNER="box-a:4242"; return 1 ;;',
                '  refs/claims/pr-absent)',
                '    CLAIM_VERDICT=claim-absent; CLAIM_SNAPSHOT_OWNER=""; return 0 ;;',
                '  *)  CLAIM_VERDICT=claim-unreadable; CLAIM_SNAPSHOT_OWNER=""; return 2 ;;',
                "  esac",
                "}",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch_env = {"NEXT_WORK_CLAIMS_SH": str(stub)}
    old = dict(os.environ)
    os.environ.update(monkeypatch_env)
    try:
        s = Snapshot()
        next_work._claim_verdicts(
            tmp_path,
            ["refs/claims/pr-held", "refs/claims/pr-absent", "refs/claims/pr-mystery"],
            s,
        )
    finally:
        os.environ.clear()
        os.environ.update(old)

    assert s.claims["refs/claims/pr-held"]["verdict"] == "claim-held", s.claims
    assert s.claims["refs/claims/pr-held"]["owner"] == "box-a:4242", "the owner survives the shell call"
    assert s.claims["refs/claims/pr-absent"]["verdict"] == "claim-absent"
    assert s.claims["refs/claims/pr-mystery"]["verdict"] == "claim-unreadable"
    assert any("claim-unreadable" in u for u in s.unreadable), (
        "an abstention must make the RUN incomplete, not just decorate a row"
    )
    assert all(k.startswith("refs/claims/") for k in s.claims), (
        "every key recorded must be a registry ref — a bare key addresses nothing"
    )


def test_unreadable_claim_verdict_blocks_the_unit():
    """An abstention is a HOLDER, not freedom — the doctrine, enforced."""
    s = snap(
        issues=[issue(100)],
        pulls=[pull(1001, closes=[100])],
        check_runs={"a" * 40: all_green()},
        attestation={"1001": {"verdict": "head"}},
        merge_tree={"1001": {"clean": True}},
        claims={"refs/claims/pr-1001": {"verdict": "claim-unreadable", "owner": ""}},
    )
    (row,) = units_for(s)
    assert row.reason == "CLAIMED"
    assert next_command(row, REPO) is None


def test_expired_and_orphaned_claims_are_free():
    """`claims.sh` says rc 0 for expired/orphaned: those windows are over."""
    for verdict in ("claim-expired", "claim-orphaned"):
        s = snap(
            issues=[issue(100)],
            pulls=[pull(1001, closes=[100])],
            check_runs={"a" * 40: all_green()},
            attestation={"1001": {"verdict": "head"}},
            merge_tree={"1001": {"clean": True}},
            claims={"refs/claims/pr-1001": {"verdict": verdict, "owner": "box-z:1"}},
        )
        (row,) = units_for(s)
        assert row.reason != "CLAIMED", f"{verdict} must not block a unit whose window closed"


# ── closing references are GraphQL-only (D4) ───────────────────────────────
def test_closing_refs_are_read_from_the_graphql_surface(monkeypatch):
    """REST's PR object has no `closingIssuesReferences`; the collector must ask gh.

    Verified live by a verifier: `gh api repos/…/pulls/10790` has no such key, so
    the first version's `closing_issues` was empty for all 27 open PRs and every
    row claimed "merging this PR closes nothing".
    """
    from tools import next_work

    payload = json.dumps(
        [
            {"number": 10790, "closingIssuesReferences": [{"number": 8906}, {"number": 10046}]},
            {"number": 10726, "closingIssuesReferences": []},
        ]
    )

    def fake_run(args, **kwargs):
        assert "closingIssuesReferences" in " ".join(args), "the GraphQL field must be requested"
        return subprocess.CompletedProcess(list(args), 0, payload, "")

    monkeypatch.setattr(next_work, "_run", fake_run, raising=True)
    s = Snapshot()
    refs = next_work._closing_refs(REPO, s)
    assert refs[10790] == [8906, 10046]
    assert refs[10726] == []
    assert s.unreadable == []


def test_a_closes_link_is_not_reported_as_refs_only():
    s = snap(
        issues=[issue(100)],
        pulls=[pull(1001, closes=[100])],
        check_runs={"a" * 40: all_green()},
        attestation={"1001": {"verdict": "head"}}, merge_tree={"1001": {"clean": True}},
    )
    (row,) = units_for(s)
    assert not any("REFS only" in d for d in row.detail), row.detail


# ── the gate's OTHER refusals are positions too (review finding 2) ─────────
@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"draft": True}, "NEEDS-FIX"),
        ({"base": "release/1.x"}, "NEEDS-FIX"),
    ],
)
def test_structural_gate_refusals_are_not_ready_to_land(kwargs, expected):
    """A DRAFT PR, or one based on another branch, is not the closest to done.

    The first version sorted only `completed/…` and `in flight` and let
    everything else — `draft`, `base=main (actual release/1.x)`,
    `label=X: absent` — fall through to `READY-TO-LAND`, so a PR the queue will
    REFUSE was the tool's best position with an executable command. Measured by a
    reviewer against the live gate.
    """
    s = snap(
        issues=[issue(100)],
        pulls=[pull(1001, closes=[100], **kwargs)],
        check_runs={"a" * 40: all_green()},
        attestation={"1001": {"verdict": "head"}},
        merge_tree={"1001": {"clean": True}},
    )
    (row,) = units_for(s)
    assert row.position == expected, row.detail
    assert row.reason != "READY-TO-LAND"


def test_a_missing_required_label_is_not_ready_to_land():
    cfg = parse_config(
        "queue_rules:\n  - name: q\n    queue_conditions:\n      - base=main\n      - label=ship-it\n      - check-success=docs\n"
    )
    s = snap(
        issues=[issue(100)],
        pulls=[pull(1001, closes=[100])],
        check_runs={"a" * 40: [{"id": 1, "name": "docs", "status": "completed",
                                "conclusion": "success", "app": {"slug": "github-actions"}}]},
        attestation={"1001": {"verdict": "head"}},
        merge_tree={"1001": {"clean": True}},
    )
    (row,) = build_units(s, cfg)
    assert row.position == "NEEDS-FIX"
    assert any("label=ship-it: absent" in d for d in row.detail)


# ── an unmeasured merge is an abstention, like every other one (finding 4) ──
def test_unmeasured_merge_tree_is_not_landable():
    s = snap(
        issues=[issue(100)],
        pulls=[pull(1001, closes=[100])],
        check_runs={"a" * 40: all_green()},
        attestation={"1001": {"verdict": "head", "why": "head"}},
        merge_tree={"1001": {"clean": None, "why": "fetch failed"}},
    )
    (row,) = units_for(s)
    assert row.position == "NEEDS-TEST", row
    assert any("UNMEASURED" in d for d in row.detail)


# ── an incomplete run delivers no pick in EITHER format (finding 5) ─────────
def test_json_refuses_the_pick_when_the_run_is_incomplete(tmp_path: Path, capsys):
    from tools import next_work

    raw = _rich_snapshot().to_json()
    raw["unreadable"] = ["claims.sh not found — the claim registry cannot be read"]
    path = tmp_path / "snap.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    rc = next_work.main(["--from", str(path), "--repo", REPO, "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 2
    assert payload["refused"] is True
    assert payload["pick"] is None, "an INCOMPLETE run must not hand a machine consumer a pick"
    assert payload["next_command"] is None
    assert all(u["next_command"] is None for u in payload["units"])


# ── a hash or a date is not an issue number (finding 6) ────────────────────
@pytest.mark.parametrize(
    ("slug", "expected"),
    [
        ("backup/fix-10704-bc80c635d", {10704}),
        ("batch-2026-11-03", set()),
        ("fix/10704-live-work", {10704}),
        (".worktrees/prd-10675", {10675}),
        ("next-work-2", set()),
        ("fix/10704-deadbeef1234", {10704}),
    ],
)
def test_issue_number_mentions_ignore_hashes_and_dates(slug, expected):
    """`backup/fix-10704-bc80c635d` claimed issue 635; `batch-2026-11-03` claimed 2026.

    Both then read CLAIMED forever — #8175's "an abandoned claim blocks its issue
    forever" class, re-entered through name matching.
    """
    from tools.next_work import _issue_numbers_in

    assert _issue_numbers_in(slug) == expected


# ── a local-holder read that FAILS is not an empty holder list (finding 3) ─
def test_local_holder_read_failure_is_unreadable_not_free(tmp_path: Path):
    from tools.next_work import _local_holders

    not_a_repo = tmp_path / "not-a-repo"
    not_a_repo.mkdir()
    s = Snapshot()
    _local_holders(not_a_repo, s)
    assert s.unreadable, "a git that cannot answer must not report 'nobody holds it'"
    assert any("local holders" in u for u in s.unreadable)


# ── a failed read is CLASSIFIED, not lumped (brief: GH-RATE-LIMITED) ───────
@pytest.mark.parametrize(
    ("detail", "expected_prefix"),
    [
        ("gh api ... failed: HTTP 403: API rate limit exceeded", "GH-RATE-LIMITED("),
        ("gh api https://api.github.com/x failed: secondary rate limit", "GH-RATE-LIMITED("),
        ("gh api --paginate timed out after 60s", "GH-TIMEOUT("),
        ("killed after 90s", "GH-TIMEOUT("),
        ("something else entirely", "search:P0-blocker"),
    ],
)
def test_a_failed_read_names_what_kind_of_failure_it_was(detail, expected_prefix):
    """A rate limit clears in a minute; a bad token does not. Say which.

    The brief asks for `GH-RATE-LIMITED(<endpoint>)` on a 403. Recording every
    failure as "unreadable" makes a throttle indistinguishable from a broken
    credential at the only moment anyone reads it — and a measured live run lost
    every surface to timeouts, which is a different remedy again.
    """
    from tools.next_work import _unreadable

    s = Snapshot()
    _unreadable(s, "search:P0-blocker", detail)
    assert s.unreadable and s.unreadable[0].startswith(expected_prefix), s.unreadable
    assert "(" in s.unreadable[0]


def test_a_refused_run_prints_no_command_in_the_text_footer_either():
    """`--json` nulled every command on a refused run; the text footer did not.

    The same fail-open, one format over: a text consumer grepping for a command
    could find one on a run the tool itself calls REFUSED.
    """
    s = _rich_snapshot()
    s.unreadable = ["GH-RATE-LIMITED(search:P0-blocker)"]
    text, _ = render(build_units(s, CFG), CFG, s, repo=REPO, top=5)
    assert "REFUSED" in text
    for line in text.splitlines():
        if line.strip().startswith(("1.", "2.", "3.", "4.", "5.")):
            assert "bash " not in line and "record-review.sh" not in line, line


def test_an_abstention_in_the_claim_map_is_incomplete_even_without_the_sentence(tmp_path: Path):
    """One field decides refusal. The verdict map IS the fact; the prose follows it.

    A replayed snapshot whose `claims` map held `claim-unreadable` but whose
    `unreadable` list was empty rendered a row saying "claim registry UNREADABLE"
    while `NEXT ACTION` said "every measured unit is held" and the exit code was
    1 — the row and the verdict contradicting each other, decided by which field
    happened to carry the fact.
    """
    from tools import next_work

    raw = _rich_snapshot().to_json()
    raw["claims"] = {"refs/claims/pr-1011": {"verdict": "claim-unreadable", "owner": ""}}
    raw["unreadable"] = []
    path = tmp_path / "snap.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    rc = next_work.main(["--from", str(path), "--repo", REPO])
    assert rc == 2, "a per-key abstention is INCOMPLETE, whatever field carries it"
    loaded = Snapshot.from_json(json.loads(path.read_text()))
    assert any("claim-unreadable" in u for u in loaded.unreadable)


def test_a_refused_run_carries_no_advisory_command_either():
    """The QUEUE NOTE's remedy is spelled as a command only when the run is usable."""
    prose = pull(901, head="9" * 40, body="batch: fold 32 open PRs into one tree")
    prose["title"] = "batch: fold 32 open tortoise PRs into one validated tree"
    s = snap(issues=[issue(100)], pulls=[prose])
    ok_text, _ = render(build_units(s, CFG), CFG, s, repo=REPO, top=5)
    assert "run `uv run python tools/batch_open.py --fold <N> --label-only`" in ok_text

    s.unreadable = ["GH-RATE-LIMITED(search:P0-blocker)"]
    refused_text, _ = render(build_units(s, CFG), CFG, s, repo=REPO, top=5)
    assert "REFUSED" in refused_text
    # the remedy is still STATED — it is just not spelled as something to run
    assert "makes the relationship mechanical" in refused_text
    assert "uv run python tools/batch_open.py" not in refused_text, (
        "a refused run must not carry a command, not even an advisory one"
    )


# ── ONE rule for "cannot be read", on the row AND on the run (review F1/F2) ─
@pytest.mark.parametrize("entry", [{"verdict": ""}, {"verdict": None}, {}, None, "claim-held"])
def test_a_claim_entry_that_cannot_be_read_blocks_and_is_incomplete(entry):
    """Silence is not `claim-absent`. Both readers use ONE definition.

    `normalize()` folded only the literal `claim-unreadable` while `_claim_of`
    also treated an empty verdict as an abstention, so the same snapshot produced
    a row saying "claim registry UNREADABLE" beside a run saying "every measured
    unit is held or superseded" — at exit 1, or exit 0 with a command when a
    second pickable unit existed. A present key with a null value was read as
    FREE and handed a command.
    """
    s = snap(
        issues=[issue(100), issue(200)],
        pulls=[pull(1001, closes=[100]), pull(2002, closes=[200])],
        check_runs={"a" * 40: all_green()},
        attestation={"1001": {"verdict": "head"}, "2002": {"verdict": "head"}},
        merge_tree={"1001": {"clean": True}, "2002": {"clean": True}},
        claims={"refs/claims/pr-1001": entry},
    ).normalize()
    rows = units_for(s)
    held = next(u for u in rows if u.issue == 100)
    assert held.reason == "CLAIMED", held
    assert next_command(held, REPO) is None
    assert any("claim-unreadable" in u for u in s.unreadable), (
        "an unreadable entry must make the RUN incomplete, not just decorate a row"
    )


def test_absent_key_is_still_free():
    """The complement: a key the registry does not hold is genuinely free."""
    s = snap(
        issues=[issue(100)],
        pulls=[pull(1001, closes=[100])],
        check_runs={"a" * 40: all_green()},
        attestation={"1001": {"verdict": "head"}},
        merge_tree={"1001": {"clean": True}},
        claims={"refs/claims/pr-9999": {"verdict": "claim-held", "owner": "x:1"}},
    ).normalize()
    (row,) = units_for(s)
    assert row.reason == "READY-TO-LAND"
    assert s.unreadable == []
