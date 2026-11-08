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
import shlex
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

#: The fleet's work-unit rule, as BYTES (#10951). `collect()` drives the SHIPPED
#: `POOL_JQ` from agent-infra's `refresh-candidates.sh`, which is gitignored
#: machine-local state (#9475) and therefore ABSENT on a runner — so a module that
#: called `collect()` without pinning the rule would be green on a box with the
#: `scripts/` symlink and red on CI (measured by a reviewer: 1 failed on the runner
#: shape, with every row withheld). Pinned to the stated mirror, for every test in
#: this module, so the module is hermetic: no DB, no network, no `gh`, no agent-infra.
FIXTURE_RULE = Path(__file__).resolve().parent / "fixtures" / "10951" / "pool-rule.sh"


@pytest.fixture(autouse=True)
def _pin_the_population_rule(monkeypatch):
    monkeypatch.setenv("NEXT_WORK_POOL_JQ_SH", str(FIXTURE_RULE))
    monkeypatch.delenv("NEXT_WORK_POPULATION", raising=False)


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
            # ⛔ A FIXTURE MUST SATISFY THE SCHEMA (#10951). These rows are all
            # measured-and-kept by the fleet's population rule (bare titles), so
            # `{}` is the honest verdict — an EMPTY map is a real measurement that
            # withheld nothing. Leaving the key out is what a snapshot written
            # before #10951 looks like, and that shape is INCOMPLETE on replay by
            # design (tests/test_10951_population.py pins it).
            "population": {},
            "population_source": "fixture",
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
    """Pickable ⇒ a COMMAND or an ACTION — never neither, never both.

    The earlier form asserted every pickable row had a `next_command`, which is
    FALSE since NEEDS-REVIEW/NEEDS-TEST deliberately carry an ACTION instead (a
    commented-out command was a round-5 defect). It passed only because the
    fixture happened to hold no such row — a legitimate fixture addition would
    have reddened correct code and invited re-adding that defect.
    """
    from tools.next_work import next_action

    s = snap(
        issues=[issue(100), issue(200), issue(300), issue(400)],
        pulls=[
            pull(1001, closes=[100], head="a" * 40),
            pull(2002, closes=[200], head="b" * 40),
            pull(3003, closes=[300], head="c" * 40),
        ],
        check_runs={"a" * 40: all_green(), "b" * 40: _runs_for_position("NEEDS-FIX"),
                    "c" * 40: _runs_for_position("NEEDS-TEST")},
        attestation={"1001": {"verdict": "head"}, "2002": {"verdict": "head"},
                     "3003": {"verdict": "head"}},
        merge_tree={"1001": {"clean": True}, "2002": {"clean": True}, "3003": {"clean": True}},
        claims={"refs/claims/pr-1001": {"verdict": "claim-held", "owner": "b:1"}},
    )
    positions = set()
    for u in units_for(s):
        cmd, act = next_command(u, REPO), next_action(u, REPO)
        if not u.pickable:
            assert cmd is None and act is None, u.key
            continue
        assert (cmd is None) != (act is None), f"{u.key} has both or neither: {cmd!r} / {act!r}"
        positions.add(u.position)
    assert {"NEEDS-FIX", "NEEDS-TEST", "CLAIMED"} & positions


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
    # argparse still refuses the flag; `main` now NORMALISES any SystemExit into the
    # documented 2 (an unknown flag is "could not run", never "nothing is pickable").
    assert _run_main(["--from", "/dev/null", "--now", "1"]) == 2, (
        "an undocumented-then-removed flag must not be silently accepted"
    )
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

def test_the_footer_prints_the_whole_command_never_a_truncated_branch():
    """A truncated command is runnable, plausible and WRONG (fold review, P2).

    The footer row used `cmd[:96]`, which cut a live-shaped branch MID-TOKEN with no marker:
    `merge-one.sh . daniel-ospina/tortoise 1001 fix/10955-queue-con` — a branch that does not
    exist. An earlier fix moved only the `NEXT ACTION` `COMMAND` line to the whole string and
    left this site behind, so a review recorded the defect as closed while it was live here.
    """
    from tools.next_work import next_command

    ref = "fix/10955-queue-consumer-and-gate-renamed-into-a-long-branch"
    s = snap(
        issues=[issue(101)],
        pulls=[{**pull(1011, closes=[101], head="2" * 40), "head_ref": ref}],
        check_runs={"2" * 40: all_green()},
        attestation={"1011": {"verdict": "head"}},
        merge_tree={"1011": {"clean": True}},
    )
    units = build_units(s, CFG)
    (row,) = units
    full = next_command(row, REPO)
    assert full and ref in full, full
    # The command must be long enough that the old 96-char window cut inside the branch name;
    # otherwise this arm would pass on the broken code and pin nothing.
    assert full.index(ref) + len(ref) > 96, full
    text, _ = render(units, CFG, s, repo=REPO, top=5)
    assert full in text, text
    rows = [ln for ln in text.splitlines() if ln.lstrip().startswith("1. ")]
    assert rows, text
    assert ref in rows[0], rows[0]


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
        raise AssertionError("a replay must not run git or gh at all")

    # Stronger than patching the config READER: a replay must spawn nothing. The
    # earlier version patched `load_config_text`, which `collect` no longer calls
    # at all (the gate read is pinned to the checkout), so the test had gone
    # vacuous while still passing.
    monkeypatch.setattr(next_work, "_run", boom, raising=True)
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
        ({"draft": True}, "NEEDS-REVIEW"),
        ({"base": "release/1.x"}, "NEEDS-REVIEW"),
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
    assert row.position == "NEEDS-REVIEW"
    assert any("label=ship-it: absent" in d for d in row.detail)
    assert next_command(row, REPO) is None


# ── a PR-chosen string is not a check state (bucket by PROVENANCE) ─────────
@pytest.mark.parametrize(
    ("base", "cfg_text"),
    [
        # A base ref containing `completed/` — legal per `git-check-ref-format` (no
        # slash-separated component may begin with a dot or end `.lock`; nothing forbids
        # `completed/`), so an author can legitimately create it. Pre-fix this landed in
        # `fails` → NEEDS-FIX → `fix-one.sh`.
        ("completed/spike", None),
        # A REQUIRED LABEL whose name contains `completed/` (config-chosen, same hole).
        (
            "main",
            "queue_rules:\n  - name: q\n    queue_conditions:\n      - base=main\n"
            "      - label=completed/pending-scan\n      - check-success=docs\n",
        ),
        # …and a label name that is a CHECK-SHAPED word: the state bucket must be decided by
        # the `check-success=` prefix, never by what the name spells. (The two OTHER
        # substring strings are unreachable through PR-chosen text: `in flight` and
        # `no check-run` both contain a SPACE, which `parse_config`'s `label=(\S+)` refuses
        # in a label name and `git-check-ref-format` refuses in a ref — so the class this arm
        # closes is the `completed/…` one, and the prefix rule keeps the rest shut.)
        (
            "main",
            "queue_rules:\n  - name: q\n    queue_conditions:\n      - base=main\n"
            "      - label=completed/no-check-run\n      - check-success=docs\n",
        ),
    ],
)
def test_a_gate_refusal_chosen_text_cannot_masquerade_as_a_check(base, cfg_text):
    """⛔ `completed/` or `no check-run` in a BASE REF or LABEL NAME is not a check state.

    `unmet_conditions` emits five forms and two of them embed text the PR (or the queue
    config) chooses: `base=<base> (actual <ref>)` and `label=<name>: absent`. The buckets
    were decided by SUBSTRING over the whole string, so `completed/spike` landed in `fails`,
    `structural` came out EMPTY, the `gate-refusal:` detail was never written — and
    `next_command` handed out `fix-one.sh` for a PR the queue refuses at entry, at exit 0,
    with `next_action` saying nothing is dispatched. Measured pre-fix: `unmet ->
    ['base=main (actual completed/spike)', 'label=completed/x: absent']` bucketed BOTH into
    `fails`. Now the PREFIX decides which condition this is and the text is display-only.
    """
    cfg = CFG if cfg_text is None else parse_config(cfg_text)
    runs = (
        all_green()
        if cfg_text is None
        else [
            {
                "id": 1,
                "name": "docs",
                "status": "completed",
                "conclusion": "success",
                "app": {"slug": "github-actions"},
            }
        ]
    )
    s = snap(
        issues=[issue(100)],
        pulls=[pull(1001, closes=[100], base=base)],
        check_runs={"a" * 40: runs},
        attestation={"1001": {"verdict": "head"}},
        merge_tree={"1001": {"clean": True}},
    )
    (row,) = build_units(s, cfg)
    assert row.position == "NEEDS-REVIEW", row.detail
    assert any(d.startswith("gate-refusal:") for d in row.detail), row.detail
    assert next_command(row, REPO) is None, "a refused PR must never carry a rail command"


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


# ── the claim NAMESPACE lives in the snapshot, not the environment (F1) ────
def test_the_claim_namespace_is_carried_by_the_snapshot(monkeypatch):
    """A replay must not depend on `CLAIM_NS` in the replaying process.

    `claims.sh` honours `CLAIM_NS`, so the collector and a replayer can
    legitimately disagree — and a disagreeing lookup MISSES, which is the one
    branch that reads as freedom. Measured: one snapshot, one held claim,
    `CLAIM_NS=refs/claims-other` → CLAIMED became READY-TO-LAND with a merge
    command at exit 0, while the report still said "2 claim key(s)".
    """
    raw = _rich_snapshot().to_json()
    raw["claim_ns"] = "refs/claims"
    loaded = Snapshot.from_json(raw)
    row = next(u for u in build_units(loaded, CFG) if u.issue == 100)
    assert row.reason == "CLAIMED"

    monkeypatch.setenv("CLAIM_NS", "refs/claims-other")
    import importlib

    from tools import next_work

    reloaded = importlib.reload(next_work)
    try:
        again = next(
            u for u in reloaded.build_units(reloaded.Snapshot.from_json(raw), CFG) if u.issue == 100
        )
        assert again.reason == "CLAIMED", "an env change moved the pick off a held unit"
    finally:
        monkeypatch.delenv("CLAIM_NS", raising=False)
        importlib.reload(next_work)


# ── a hung claims.sh is INCOMPLETE, not a crash (F2) ──────────────────────
def test_a_hung_claims_sh_is_an_unreadable_surface(tmp_path: Path):
    """The only subprocess without error handling — and it does a network fetch."""
    from tools import next_work

    stub = tmp_path / "hung-claims.sh"
    stub.write_text("claim_snapshot() { sleep 30; }\nclaim_snapshot_verdict() { :; }\n")
    old = os.environ.get("NEXT_WORK_CLAIMS_SH")
    os.environ["NEXT_WORK_CLAIMS_SH"] = str(stub)
    try:
        snap_ = Snapshot()
        import unittest.mock as mock

        with mock.patch.object(next_work.subprocess, "run", side_effect=TimeoutError("timed out")):
            next_work._claim_verdicts(tmp_path, ["refs/claims/pr-1"], snap_)
    finally:
        if old is None:
            os.environ.pop("NEXT_WORK_CLAIMS_SH", None)
        else:
            os.environ["NEXT_WORK_CLAIMS_SH"] = old
    assert snap_.unreadable, "a timeout is 'could not check', never 'free'"
    assert any("GH-TIMEOUT" in u for u in snap_.unreadable)


# ── one row per issue, and the tie-break is the NUMBER (F4, F5) ───────────
def test_a_dual_labelled_issue_yields_one_row():
    from tools import next_work

    class StubClient:
        """The SAME issue returned by both label searches — the shape `collect` dedups."""

        def _json(self, args):
            if "P0-blocker" in args[0]:
                return {"total_count": 1, "items": [{"number": 100, "title": "t"}]}
            return {"total_count": 1, "items": [{"number": 100, "title": "t"}]}

    s = Snapshot()
    rows = next_work._search_issues(StubClient(), REPO, "P0-blocker", s)
    rows += next_work._search_issues(StubClient(), REPO, "P1-severe", s)
    # the dedup lives in `collect`; assert its contract on the shape it dedups
    by_number: dict[int, dict] = {}
    for row in rows:
        by_number.setdefault(int(row["number"]), row)
    assert len(by_number) == 1
    # and in the decision: two rows with ONE key would overcount the population
    s2 = snap(issues=[issue(100, "P0-blocker")], pulls=[], )
    keys = [u.key for u in units_for(s2)]
    assert keys.count("100") == 1


def test_the_tie_break_is_the_number_not_its_text():
    """`str(100) < str(99)`, so a text tie-break picked 100 ahead of 99."""
    s = snap(
        issues=[issue(99), issue(100)],
        pulls=[],
    )
    rows = units_for(s)
    assert [u.issue for u in rows] == [99, 100], [u.issue for u in rows]


# ── NEEDS-TEST is a position, not an action (F6) ──────────────────────────
def test_needs_test_is_not_an_executable_action():
    """`executable` must exclude NEEDS-TEST — the actuator-that-cannot-deliver rule."""
    # #101 waits on a runner (NEEDS-TEST, rank 2) and #100 needs a fix
    # (NEEDS-FIX, rank 3), so the RANK LEADER has nothing to run and the
    # first actionable unit is a different row — exactly the live shape of
    # 2026-11-04, when the CI queue itself was the blocker.
    s = snap(
        issues=[issue(100), issue(101)],
        pulls=[pull(1001, closes=[100]), pull(1011, closes=[101], head="b" * 40)],
        check_runs={"a" * 40: _runs_for_position("NEEDS-FIX"), "b" * 40: _runs_for_position("NEEDS-TEST")},
        attestation={"1001": {"verdict": "head"}, "1011": {"verdict": "head"}},
        merge_tree={"1001": {"clean": True}, "1011": {"clean": True}},
    )
    rows = units_for(s)
    waiting = next(u for u in rows if u.position == "NEEDS-TEST")
    assert waiting.pickable and not waiting.executable
    runnable = next(u for u in rows if u.executable)
    assert runnable.position != "NEEDS-TEST"
    text, _ = render(rows, CFG, s, repo=REPO, top=5)
    assert "RUNNABLE" in text, "the first unit a lane can act on NOW must be named"
    assert str(runnable.issue) in text


# ── the head-bound claim ref is really consulted (F7) ─────────────────────
def test_a_head_bound_claim_blocks_even_when_the_pr_key_is_free():
    """`<pr>-<sha12>` is the claim a REVIEW takes; `pr-<N>` is not the same key."""
    s = snap(
        issues=[issue(100)],
        pulls=[pull(1001, closes=[100], head="c" * 40)],
        check_runs={"c" * 40: all_green()},
        attestation={"1001": {"verdict": "head"}},
        merge_tree={"1001": {"clean": True}},
        claims={"refs/claims/1001-cccccccccccc": {"verdict": "claim-held", "owner": "box-r:7"}},
    )
    (row,) = units_for(s)
    assert row.reason == "CLAIMED", "a head-bound claim was not consulted"
    assert row.holder == "box-r:7"


# ── a fold can be held locally (no issue to key on) ───────────────────────
def test_a_locally_held_fold_is_not_free():
    fold = pull(900, head="9" * 40, labels=[FOLD_LABEL], body="fold-constituents: 1001")
    s = snap(
        issues=[],
        pulls=[fold],
        check_runs={"9" * 40: all_green()},
        attestation={"900": {"verdict": "head"}},
        merge_tree={"900": {"clean": True}},
        local_holders={"pr:900": ["lane/900-fold-work"]},
    )
    row = next(u for u in units_for(s) if u.kind == "fold")
    assert row.reason == "CLAIMED", "a fold with no issue was invisible to the local surface"


# ── a line-only fold must be MEASURED, not just detected (review 1) ────────
def test_a_fold_declared_only_by_its_line_is_measured(monkeypatch):
    """`build_units` accepts the declared line as fold evidence — so `collect` must measure it.

    The measurement filter looked only at issue links and the `batch-fold` LABEL,
    so a line-only fold was never attested and never merge-tested: the abstention
    forced NEEDS-TEST forever and the row claimed "awaiting checks" over green
    checks and "head not available locally" when no fetch was attempted. That is
    the COMMON live shape — the label is not applied automatically yet.
    """
    from tools import next_work


    # REST/`gh` shape, which is what `collect` maps: head.sha, head.ref, body…
    rest_fold = {
        "number": 900,
        "head": {"sha": "9" * 40, "ref": "batch/fold-900"},
        "base": {"ref": "main"},
        "draft": False,
        "labels": [],
        "title": "batch: a fold",
        "body": "fold-constituents: 1001",
    }

    class FakeClient:
        def list_pulls(self, repo, base):
            return [rest_fold]

        def _get_list(self, path, params="", select=".[]"):
            if path.endswith("/pulls"):
                return [rest_fold]
            return []

        def list_check_runs(self, repo, sha):
            # `collect` reads the REPO's gate (6 required checks here), not the
            # 2-check test config, so the fake must satisfy the real gate.
            return [
                {
                    "id": i,
                    "name": name,
                    "status": "completed",
                    "conclusion": "success",
                    "app": {"slug": "github-actions"},
                }
                for i, name in enumerate(
                    [
                        "pricing-artifact",
                        "docs",
                        "test-isolation",
                        "license-surface",
                        "legal-e2e",
                        "ai-review-gate",
                    ]
                )
            ]

    measured: dict[str, object] = {}

    monkeypatch.setattr(next_work, "GhCli", lambda: FakeClient())
    monkeypatch.setattr(next_work, "_search_issues", lambda *a, **k: [])
    monkeypatch.setattr(next_work, "_closing_refs", lambda *a, **k: {})
    monkeypatch.setattr(next_work, "_local_holders", lambda *a, **k: {})
    monkeypatch.setattr(next_work, "_claim_verdicts", lambda *a, **k: None)
    monkeypatch.setattr(
        next_work,
        "_attestation",
        lambda rr, repo, pr, snap: measured.setdefault("attested", []).append(pr)
        or snap.attestation.__setitem__(str(pr), {"verdict": "head"}),
    )
    monkeypatch.setattr(
        next_work,
        "_merge_tree",
        lambda rr, base, pr, snap: measured.setdefault("merged", []).append(int(pr["number"]))
        or snap.merge_tree.__setitem__(str(pr["number"]), {"clean": True}),
    )
    snap_, cfg, _ = next_work.collect(REPO, Path(__file__).resolve().parent.parent)
    assert measured.get("attested") == [900], "a line-only fold was never attested"
    assert measured.get("merged") == [900], "a line-only fold was never merge-tested"
    row = next(u for u in build_units(snap_, cfg) if u.kind == "fold")
    assert row.position == "READY-TO-LAND", row.detail
    assert (next_command(row, REPO) or "").startswith("bash "), (
        "a measured fold must name the rail that lands it, not a wait note"
    )


# ── an unreadable SNAPSHOT is exit 2, never "nothing to do" (review 2) ────
def test_an_unreadable_snapshot_exits_2(tmp_path: Path):
    assert _run_main(["--from", str(tmp_path / "nope.json"), "--repo", REPO]) == 2
    bad = tmp_path / "truncated.json"
    bad.write_text('{"repo": "x", "issues": [', encoding="utf-8")
    assert _run_main(["--from", str(bad), "--repo", REPO]) == 2
    notobj = tmp_path / "list.json"
    notobj.write_text("[1, 2, 3]", encoding="utf-8")
    assert _run_main(["--from", str(notobj), "--repo", REPO]) == 2


# ── the documented 0 / 1 / 2 contract, each pinned (review 4c) ────────────
def test_exit_codes_are_pinned(tmp_path: Path):
    ready = tmp_path / "ready.json"
    ready.write_text(json.dumps(_rich_snapshot().to_json()), encoding="utf-8")
    assert _run_main(["--from", str(ready), "--repo", REPO]) == 0

    held = _rich_snapshot().to_json()
    held["claims"] = {
        "refs/claims/issue-101": {"verdict": "claim-held", "owner": "a:1"},
        "refs/claims/issue-200": {"verdict": "claim-held", "owner": "a:1"},
        "refs/claims/issue-201": {"verdict": "claim-held", "owner": "a:1"},
        "refs/claims/issue-100": {"verdict": "claim-held", "owner": "a:1"},
    }
    allheld = tmp_path / "held.json"
    allheld.write_text(json.dumps(held), encoding="utf-8")
    assert _run_main(["--from", str(allheld), "--repo", REPO]) == 1

    incomplete = _rich_snapshot().to_json()
    incomplete["unreadable"] = ["GH-TIMEOUT(search:P0-blocker)"]
    inc = tmp_path / "inc.json"
    inc.write_text(json.dumps(incomplete), encoding="utf-8")
    assert _run_main(["--from", str(inc), "--repo", REPO]) == 2


# ── the render layer never prints a held unit's command (review 4a/4b) ────
def test_render_never_prints_a_held_units_command():
    s = snap(
        issues=[issue(100), issue(200)],
        pulls=[pull(1001, closes=[100]), pull(2002, closes=[200], head="b" * 40)],
        check_runs={"a" * 40: all_green(), "b" * 40: all_green()},
        attestation={"1001": {"verdict": "head"}, "2002": {"verdict": "head"}},
        merge_tree={"1001": {"clean": True}, "2002": {"clean": True}},
        claims={"refs/claims/pr-1001": {"verdict": "claim-held", "owner": "box-a:1"}},
    )
    text, pick = render(build_units(s, CFG), CFG, s, repo=REPO, top=5)
    assert pick is not None and pick.issue == 200
    action = text.split("NEXT ACTION", 1)[1]
    assert "1001" not in action, "the held unit's PR appeared in the NEXT ACTION block"
    assert "2002" in action


def test_runnable_is_never_a_held_or_superseded_unit():
    s = snap(
        issues=[issue(100), issue(200)],
        pulls=[
            pull(1001, closes=[100], labels=[CONSTITUENT_LABEL]),
            pull(2002, closes=[200], head="b" * 40),
        ],
        check_runs={"a" * 40: all_green(), "b" * 40: _runs_for_position("NEEDS-FIX")},
        attestation={"1001": {"verdict": "head"}, "2002": {"verdict": "head"}},
        merge_tree={"1001": {"clean": True}, "2002": {"clean": True}},
    )
    rows = units_for(s)
    runnable = next(u for u in rows if u.executable)
    assert runnable.reason != "SUPERSEDED-BY-FOLD"
    assert runnable.pickable


# ── the search surface refuses truncation and failure (review 4d) ─────────
def test_search_failures_are_unreadable_never_an_empty_population():
    from tools import next_work

    class Raising:
        def _json(self, args):
            raise next_work.GhError("gh api search/issues failed: HTTP 403")

    s = Snapshot()
    assert next_work._search_issues(Raising(), REPO, "P0-blocker", s) == []
    assert any("GH-RATE-LIMITED" in u for u in s.unreadable), s.unreadable

    class NoTotal:
        def _json(self, args):
            return {"items": [{"number": 1, "title": "t"}]}

    s2 = Snapshot()
    next_work._search_issues(NoTotal(), REPO, "P0-blocker", s2)
    assert any("total_count" in u for u in s2.unreadable), s2.unreadable

    class Shortfall:
        """Page 1 returns 1 of 5 promised rows and nothing follows: a capped window."""

        def _json(self, args):
            # NB: `"page=1" in url` is ALWAYS true — `per_page=100` contains it.
            # Split on the last `page=` to get the number actually requested.
            if args[0].rsplit("page=", 1)[1] == "1":
                return {"total_count": 5, "items": [{"number": 1, "title": "t"}]}
            return {"total_count": 5, "items": []}

    s3 = Snapshot()
    next_work._search_issues(Shortfall(), REPO, "P0-blocker", s3)
    assert any("page-capped" in u for u in s3.unreadable), s3.unreadable


# ── the dedup is the REAL function, not re-implemented in the test (4e) ───
def test_dedupe_issues_keeps_one_row_and_the_higher_priority():
    from tools.next_work import dedupe_issues

    rows = dedupe_issues(
        [
            {"number": 100, "title": "t", "priority": "P1-severe"},
            {"number": 100, "title": "t", "priority": "P0-blocker"},
            {"number": 99, "title": "t", "priority": "P1-severe"},
        ]
    )
    assert [r["number"] for r in rows] == [99, 100]
    assert rows[1]["priority"] == "P0-blocker"


def test_the_newest_pr_is_the_one_offered():
    """An older abandoned PR that names the issue is not the thing to hand a lane."""
    from tools.next_work import _open_pulls_for_issue

    s = snap(
        issues=[issue(100)],
        pulls=[pull(1001, closes=[100]), pull(2002, closes=[100], head="b" * 40)],
    )
    assert [p["number"] for p in _open_pulls_for_issue(s, 100)] == [2002, 1001]


# ── the emitted command is pasteable: it names the branch (review, minor) ─
def test_the_emit_command_names_the_head_branch():
    s = snap(
        issues=[issue(100)],
        pulls=[{**pull(1001, closes=[100]), "head_ref": "fix/100-something"}],
        check_runs={"a" * 40: all_green()},
        attestation={"1001": {"verdict": "head"}},
        merge_tree={"1001": {"clean": True}},
    )
    (row,) = units_for(s)
    cmd = next_command(row, REPO) or ""
    assert "fix/100-something" in cmd
    assert "<branch>" not in cmd, "`<branch>` is an input redirection in a shell, not a placeholder"


def test_collect_maps_the_head_branch_into_the_snapshot(monkeypatch):
    """The branch must be recorded by `collect`, not planted by a test.

    `Unit.head_ref` existed and `next_command` read it, but nothing WROTE it — so
    on every live run (and every `--snapshot-out` replay) the emitted rail command
    said "branch unknown". The only test planted the field on a synthetic
    snapshot, which is why it could not catch this.
    """
    from tools import next_work

    rest_pr = {
        "number": 42,
        "head": {"sha": "b" * 40, "ref": "fix/42-thing"},
        "base": {"ref": "main"},
        "draft": False,
        "labels": [],
        "title": "t",
        "body": "Closes #42",
    }

    class FakeClient:
        def list_pulls(self, repo, base):
            return [rest_pr]

        def _get_list(self, path, params="", select=".[]"):
            if path.endswith("/pulls"):
                return [rest_pr]
            return []  # no cross-referenced events for this PR

        def list_check_runs(self, repo, sha):
            return [
                {"id": i, "name": n, "status": "completed", "conclusion": "success",
                 "app": {"slug": "github-actions"}}
                for i, n in enumerate(
                    ["pricing-artifact", "docs", "test-isolation", "license-surface",
                     "legal-e2e", "ai-review-gate"]
                )
            ]

    monkeypatch.setattr(next_work, "GhCli", lambda: FakeClient())
    monkeypatch.setattr(next_work, "_search_issues", lambda c, r, label, s: (
        [{"number": 42, "title": "t", "priority": "P0-blocker"}] if label == "P0-blocker" else []
    ))
    monkeypatch.setattr(next_work, "_closing_refs", lambda *a, **k: {42: [42]})
    monkeypatch.setattr(next_work, "_local_holders", lambda *a, **k: {})
    monkeypatch.setattr(next_work, "_claim_verdicts", lambda *a, **k: None)
    monkeypatch.setattr(next_work, "_attestation", lambda rr, repo, pr, snap: snap.attestation.__setitem__(str(pr), {"verdict": "head"}))
    monkeypatch.setattr(next_work, "_merge_tree", lambda rr, base, pr, snap: snap.merge_tree.__setitem__(str(pr["number"]), {"clean": True}))

    snap_, cfg, _ = next_work.collect(REPO, Path(__file__).resolve().parent.parent)
    (snap_pr,) = snap_.pulls
    assert snap_pr["head_ref"] == "fix/42-thing", snap_pr
    (row,) = build_units(snap_, cfg)
    cmd = next_command(row, REPO) or ""
    assert "fix/42-thing" in cmd, "the emitted command does not name the real branch"


def test_collect_dedupes_an_issue_found_by_both_label_searches(monkeypatch):
    """The WIRING, not the helper: `snap.issues = dedupe_issues(found)` inside `collect`.

    An issue carrying BOTH labels is returned by BOTH searches — the overcount the module's
    own comment at `collect` says it fixed. The existing arm calls `dedupe_issues` directly
    and another dedupes in TEST-local code, so reverting the wiring to
    `snap.issues = found` left the whole file green while `--json` emitted two units a
    consumer keys as one and the priority ladder double-counted the same issue.
    """
    from tools import next_work

    class FakeClient:
        def _get_list(self, path, params="", select=".[]"):
            return []

        def list_check_runs(self, repo, sha):
            return []

    monkeypatch.setattr(next_work, "GhCli", lambda: FakeClient())
    # The SAME issue number under both priority searches, as GitHub returns it.
    monkeypatch.setattr(
        next_work,
        "_search_issues",
        lambda c, r, label, s: [
            {"number": 77, "title": "t", "priority": label}
            if label in ("P0-blocker", "P1-severe")
            else []
        ],
    )
    monkeypatch.setattr(next_work, "_local_holders", lambda *a, **k: {})
    monkeypatch.setattr(next_work, "_claim_verdicts", lambda *a, **k: None)

    snap_, _, _ = next_work.collect(REPO, Path(__file__).resolve().parent.parent)
    assert len(snap_.issues) == 1, f"the population double-counted one issue: {snap_.issues}"
    assert snap_.issues[0]["priority"] == "P0-blocker", snap_.issues[0]


def test_fold_constituents_may_carry_a_hash_prefix():
    """`batch_open.py` accepts `#<n>` tokens; requiring a bare digit made such a
    fold invisible here, leaving its constituents pickable ahead of it."""
    fold = pull(900, head="9" * 40, labels=[], body="fold-constituents: #1501, #1502")
    s = snap(
        issues=[issue(501), issue(502)],
        pulls=[fold, pull(1501, closes=[501]), pull(1502, closes=[502])],
        check_runs={"9" * 40: all_green()},
        attestation={"900": {"verdict": "head"}},
        merge_tree={"900": {"clean": True}},
    )
    rows = units_for(s)
    fold_row = [u for u in rows if u.kind == "fold"]
    assert fold_row, "a `#`-prefixed constituent list was not read as a fold"
    assert all(u.reason == "SUPERSEDED-BY-FOLD" for u in rows if u.kind == "issue")


# ── the replayed gate goes through the SAME parser (review round 5, #1) ────
def test_a_replayed_gate_with_no_required_check_is_refused(tmp_path: Path):
    """A gate that requires nothing makes every PR landable — on either path.

    The live path exits 2 because `parse_config` refuses a queue rule naming no
    `check-success`. The replay path rebuilt the config by hand and validated
    nothing, so a snapshot with `required_checks: []` produced "no unmet
    conditions" and a merge command for a PR whose CI did not exist at all.
    """
    raw = _rich_snapshot().to_json()
    raw["gate"] = {**raw["gate"], "required_checks": []}
    raw["gate_text"] = ""
    raw["check_runs"] = {}
    path = tmp_path / "empty-gate.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    assert _run_main(["--from", str(path), "--repo", REPO]) == 2


def test_a_replayed_gate_text_is_parsed_by_the_one_parser(tmp_path: Path):
    raw = _rich_snapshot().to_json()
    raw["gate_text"] = "queue_rules:\n  - name: q\n    queue_conditions:\n      - nonsense-condition\n"
    path = tmp_path / "bad-gate.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    assert _run_main(["--from", str(path), "--repo", REPO]) == 2


# ── one ACTION TARGET, one command-bearing row (review round 5, #2) ───────
def test_two_issues_sharing_a_pr_offer_only_one_command():
    """One PR lands several issues (#10726 closes 7) — that is ONE action.

    Both rows stayed pickable with the SAME command, so a consumer taking two rows
    (the documented parallelism pattern) worked one PR twice — the duplicate pick
    this tool exists to prevent, one level below `pick`.
    """
    s = snap(
        issues=[issue(100), issue(101)],
        pulls=[{**pull(2001, closes=[100, 101]), "head_ref": "fix/2001-thing"}],
        check_runs={"a" * 40: all_green()},
        attestation={"2001": {"verdict": "head"}},
        merge_tree={"2001": {"clean": True}},
    )
    rows = units_for(s)
    with_cmd = [u for u in rows if next_command(u, REPO)]
    assert len(with_cmd) == 1, [(u.key, next_command(u, REPO)) for u in with_cmd]
    other = next(u for u in rows if u is not with_cmd[0])
    assert not other.pickable
    assert other.duplicate_of == with_cmd[0].key


def test_a_fold_and_its_issue_do_not_both_offer_a_command():
    fold = {**pull(900, head="9" * 40, labels=[FOLD_LABEL], body="fold-constituents: 1001"),
            "head_ref": "batch/fold-900"}
    s = snap(
        issues=[issue(100)],
        pulls=[fold, pull(1001, closes=[100])],
        check_runs={"9" * 40: all_green(), "a" * 40: all_green()},
        attestation={"900": {"verdict": "head"}, "1001": {"verdict": "head"}},
        merge_tree={"900": {"clean": True}, "1001": {"clean": True}},
    )
    cmds = [next_command(u, REPO) for u in units_for(s) if next_command(u, REPO)]
    assert len(cmds) == len(set(cmds)), f"two rows emit one command: {cmds}"


# ── a comment is not a command (review round 5, #3) ───────────────────────
def test_needs_review_is_an_action_not_a_command():
    """A commented-out line exits 0 having done nothing — indistinguishable from done."""
    s = snap(
        issues=[issue(100)],
        pulls=[{**pull(1001, closes=[100]), "head_ref": "fix/100-thing"}],
        check_runs={"a" * 40: all_green()},
        attestation={"1001": {"verdict": "norecord"}},
        merge_tree={"1001": {"clean": True}},
    )
    from tools.next_work import next_action

    (row,) = units_for(s)
    assert row.position == "NEEDS-REVIEW"
    assert next_command(row, REPO) is None, "a shell comment was offered as a command"
    action = next_action(row, REPO) or ""
    assert "reviewer" in action and row.head in action + str(row.head)
    assert not row.executable
    text, _ = render(units_for(s), CFG, s, repo=REPO, top=5)
    assert "ACTION" in text and "COMMAND" not in text.split("CONSUME")[0]


# ── an unknown branch must still produce a pasteable command (round 5, #4) ─
def test_an_unknown_branch_resolves_inside_the_command():
    s = snap(
        issues=[issue(100)],
        pulls=[pull(1001, closes=[100])],  # no head_ref
        check_runs={"a" * 40: all_green()},
        attestation={"1001": {"verdict": "head"}},
        merge_tree={"1001": {"clean": True}},
    )
    (row,) = units_for(s)
    cmd = next_command(row, REPO) or ""
    assert "<" not in cmd and ">" not in cmd, f"a <…> token is a shell redirection: {cmd}"
    assert "headRefName" in cmd


# ── the fold line follows batch_open.py's grammar exactly (round 5, #5) ───
def test_an_unparseable_fold_line_is_refused_not_prefix_harvested():
    from tools.next_work import _fold_constituents, _fold_line_refused

    ok = {"fold_line": "fold-constituents: #1501, #1502"}
    assert _fold_constituents(ok) == {1501, 1502}
    assert not _fold_line_refused(ok)

    # A well-formed parenthetical TAIL is prose and is STRIPPED (batch_open's rule)
    assert _fold_constituents({"fold_line": "fold-constituents: 10543 (10641 dropped)"}) == {10543}
    # …but text AFTER the tail is refused whole
    refused = {"fold_line": "fold-constituents: 10543 (10641 dropped) #10820"}
    assert _fold_constituents(refused) == set(), (
        "batch_open.py refuses this line; prefix-harvesting it suppresses 10543 forever"
    )
    assert _fold_line_refused(refused)

    none = {"fold_line": "Closes #1 #2"}
    assert _fold_constituents(none) == set()
    assert not _fold_line_refused(none)


def test_an_unparseable_fold_line_does_not_supersede_a_constituent():
    fold = pull(900, head="9" * 40, labels=[], body="fold-constituents: 1001 (dropped) #99999")
    s = snap(
        issues=[issue(100)],
        pulls=[fold, pull(1001, closes=[100])],
        check_runs={"9" * 40: all_green(), "a" * 40: all_green()},
        attestation={"1001": {"verdict": "head"}},
        merge_tree={"1001": {"clean": True}},
    )
    rows = units_for(s)
    assert not [u for u in rows if u.kind == "fold"], "a refused fold line created a fold"
    assert next(u for u in rows if u.issue == 100).reason != "SUPERSEDED-BY-FOLD"


# ── a mis-shaped snapshot exits 2, not a traceback (round 5, #6) ──────────
def test_a_mis_shaped_snapshot_exits_2(tmp_path: Path):
    raw = _rich_snapshot().to_json()
    for pr in raw["pulls"]:
        pr["labels"] = ["ship-it"]  # hand-written form: plain strings
    path = tmp_path / "string-labels.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    assert _run_main(["--from", str(path), "--repo", REPO]) in (0, 1), (
        "plain-string labels are a legitimate hand-written shape and must be read"
    )

    raw2 = _rich_snapshot().to_json()
    raw2["local_holders"] = ["not", "a", "mapping"]
    path2 = tmp_path / "bad-holders.json"
    path2.write_text(json.dumps(raw2), encoding="utf-8")
    assert _run_main(["--from", str(path2), "--repo", REPO]) == 2


# ── two folds claiming one constituent: the report must not depend on order ─
def test_two_folds_for_one_constituent_report_the_lowest():
    a = pull(901, head="9" * 40, labels=[FOLD_LABEL], body="fold-constituents: 1001")
    b = pull(900, head="8" * 40, labels=[FOLD_LABEL], body="fold-constituents: 1001")
    for pulls in ([a, b], [b, a]):
        s = snap(issues=[issue(100)], pulls=[*pulls, pull(1001, closes=[100])])
        row = next(u for u in units_for(s) if u.issue == 100)
        assert row.fold_pr == 900


# ── a mis-shaped snapshot exits 2 EVERYWHERE, including before the decision ──
@pytest.mark.parametrize(
    "mangle",
    [
        {"claims": ["x"]},
        {"claims": "x"},
        {"attestation": [1, 2]},
        {"gate": "queue_rules:"},
        {"gate": []},
        {"gate_text": 7},
        {"local_holders": ["not", "a", "mapping"]},
        {"issues": "x"},
        {"pulls": ["200"]},
    ],
)
def test_any_mis_shaped_snapshot_exits_2(tmp_path: Path, mangle):
    """Exit 1 means "the queue was read and answered". A shape the tool cannot
    decide is "could not check" — and the guard must cover the LOAD, not just the
    decision: `claims: ["x"]` used to raise out of `from_json` as a traceback with
    the interpreter's own exit 1."""
    raw = _rich_snapshot().to_json()
    raw.update(mangle)
    path = tmp_path / "mangled.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    assert _run_main(["--from", str(path), "--repo", REPO]) == 2, mangle


def test_the_runnable_line_names_the_ACTUAL_reason():
    """The parenthetical said "waiting on a runner" for every top row without a command.

    A NEEDS-REVIEW top row printed "dispatch a reviewer at #N" and, one line later,
    told the operator to wait for a runner that would never move it.
    """
    review_top = snap(
        issues=[issue(100), issue(200)],
        pulls=[pull(1001, closes=[100]), {**pull(2002, closes=[200], head="b" * 40)}],
        check_runs={"a" * 40: all_green(), "b" * 40: _runs_for_position("NEEDS-FIX")},
        attestation={"1001": {"verdict": "norecord"}, "2002": {"verdict": "head"}},
        merge_tree={"1001": {"clean": True}, "2002": {"clean": True}},
    )
    text, pick = render(build_units(review_top, CFG), CFG, review_top, repo=REPO, top=5)
    assert pick is not None and pick.position == "NEEDS-REVIEW"
    assert "RUNNABLE" in text
    assert "REVIEW" in text.split("RUNNABLE", 1)[1].split("\n", 1)[0] or "REVIEW" in text
    assert "waiting on a runner" not in text, "the wrong cause was printed for a NEEDS-REVIEW top row"

    test_top = snap(
        issues=[issue(100), issue(200)],
        pulls=[pull(1001, closes=[100]), pull(2002, closes=[200], head="b" * 40)],
        check_runs={"a" * 40: _runs_for_position("NEEDS-TEST"), "b" * 40: _runs_for_position("NEEDS-FIX")},
        attestation={"1001": {"verdict": "head"}, "2002": {"verdict": "head"}},
        merge_tree={"1001": {"clean": True}, "2002": {"clean": True}},
    )
    text2, _ = render(build_units(test_top, CFG), CFG, test_top, repo=REPO, top=5)
    assert "waiting on a runner" in text2


def test_the_queue_note_distinguishes_a_missing_fold_line_from_a_refused_one():
    """A line that does not parse is not "no line" — and `_fold_line_refused` was dead.

    The note told a reader these PRs carry no `fold-constituents:` line and to add
    one — when the line is already there in a form `batch_open.py` refuses whole.
    """
    prose = pull(901, head="9" * 40, labels=[], body="fold-constituents: 1001 (dropped) #99999")
    prose["title"] = "batch: fold 32 open tortoise PRs into one validated tree"
    s = snap(issues=[issue(100)], pulls=[prose, pull(1001, closes=[100])])
    text, _ = render(build_units(s, CFG), CFG, s, repo=REPO, top=5)
    assert "QUEUE NOTE" in text
    assert "does not parse" in text
    assert "#901" in text.split("does not parse", 1)[1] or "901" in text.split("QUEUE NOTE", 1)[1]


def test_the_queue_note_counts_two_populations_separately():
    """`N of them` must not name a PR that is not one of "them".

    Measured: one title-only fold (#912) plus a LABELLED fold with a refused line
    (#911) produced "1 open PR(s) batch by TITLE ALONE (#912)" followed by
    "⚠️ 2 of them … (#911, #912)" — a count larger than the population it referred
    to, naming a PR that was not in it.
    """
    labelled_refused = {
        **pull(911, head="9" * 40, labels=[FOLD_LABEL], body="fold-constituents: 1001 (dropped) #99999"),
    }
    labelled_refused["title"] = "batch: fold things"
    title_only = {**pull(912, head="8" * 40, labels=[], body="fold-constituents: 1001 (dropped) #99999")}
    title_only["title"] = "batch: fold 32 open tortoise PRs into one validated tree"
    s = snap(issues=[issue(100)], pulls=[labelled_refused, title_only, pull(1001, closes=[100])])
    text, _ = render(build_units(s, CFG), CFG, s, repo=REPO, top=5)
    note = text.split("QUEUE NOTE", 1)[1].split("TOP ", 1)[0]
    assert "1 of them DO carry" in note, note
    assert "1 other open PR(s) carry a non-parsing" in note, note
    # the labelled one is reported in ITS own line, not inside the "of them" count
    them = next(ln for ln in note.splitlines() if "of them DO carry" in ln)
    assert "#912" in them and "#911" not in them, them


def test_a_labelled_fold_with_a_refused_line_says_so_on_its_row():
    """A label says "fold"; a refused line means it declares NOTHING. Say it."""
    fold = {**pull(911, head="9" * 40, labels=[FOLD_LABEL], body="fold-constituents: 1001 (dropped) #99999")}
    s = snap(
        issues=[],
        pulls=[fold, pull(1001, closes=[100])],
        check_runs={"9" * 40: all_green()},
        attestation={"911": {"verdict": "head"}},
        merge_tree={"911": {"clean": True}},
    )
    fold_row = next(u for u in units_for(s) if u.kind == "fold")
    assert any("refused WHOLE" in d for d in fold_row.detail), fold_row.detail
    text, _ = render(units_for(s), CFG, s, repo=REPO, top=5)
    assert "non-parsing" in text


# ── this guard must actually RUN on an edit to the file it guards ──────────
def test_a_next_work_only_change_selects_core_so_this_guard_actually_runs() -> None:
    """The `CORE_ALSO` entry is LOAD-BEARING, so it is pinned here.

    `tools/` is in `NON_PYTHON_PREFIXES` and no `SOURCE_PATTERNS` row matches
    `tools/next_work.py`, so WITHOUT the entry a next_work-only diff is filtered
    out BEFORE the "not matched -> core" fallback, `select()` returns no surface,
    and this file never runs on the edit it exists to judge. For THIS tool that is
    the fail-open direction specifically: it decides which unit a lane may act on,
    so the diff most worth guarding is one that weakens the deciding code.

    Measured with the entry removed: `surfaces == []` and `test_next_work.py` is
    absent from `test_files`. `tests/test_ci_selection.py`'s generic ratchet
    derives candidates from `tortoise/**/*.py` stems only, so it cannot see a
    `tools/` guard — hence the pin lives here, where the entry does.

    Registration in `config/ci-surfaces.yml` is the OTHER half and is pinned by
    `ci_selection.py --integrity` (a `tests/`-unregistered file reds it); this
    test pins the selection half, which integrity does not read.
    """
    from tools import ci_selection as cs

    selection = cs.select(["tools/next_work.py"], "pull_request", cs.load_manifest())
    assert selection["surfaces"] == ["core"], selection
    assert "test_next_work.py" in selection["test_files"], (
        "the CORE_ALSO entry is missing: a next_work-only diff would run no guard "
        f"at all — got {len(selection['test_files'])} files"
    )


# ── the branch is UNTRUSTED input, not a shell-safe token (round 6, #1) ───
def test_a_hostile_branch_name_cannot_escape_the_emitted_command():
    """`git check-ref-format` allows `$(`, backticks, `"` and `;` in a ref name.

    The command this tool prints is the one thing it exists to hand a runner, and
    it interpolated the head ref RAW inside double quotes — and double quotes
    EXECUTE `$( )`. Verified: a head ref of `fix/100-$(tar${IFS}czf${IFS}/tmp/x)`
    came out verbatim. The repo's own rail documents this exact threat; the emitter
    must not put the value back into the shell.
    """
    hostile = 'fix/100-$(tar${IFS}czf${IFS}/tmp/x${IFS}$HOME);`id`"quoted'
    s = snap(
        issues=[issue(100)],
        pulls=[{**pull(1001, closes=[100]), "head_ref": hostile}],
        check_runs={"a" * 40: all_green()},
        attestation={"1001": {"verdict": "head"}},
        merge_tree={"1001": {"clean": True}},
    )
    (row,) = units_for(s)
    cmd = next_command(row, REPO) or ""
    import shlex

    # The ref must survive as ONE shell word (so the rail receives it intact) and
    # must be QUOTED in the text — an unquoted `$(` would run.
    argv = shlex.split(cmd)
    assert argv[-1] == hostile, f"the ref is not recoverable as ONE argument: {argv}"
    assert shlex.quote(hostile) in cmd, f"the ref was not quoted: {cmd}"


def test_the_emitted_command_is_shell_parseable_with_a_plain_branch():
    import shlex

    s = snap(
        issues=[issue(100)],
        pulls=[{**pull(1001, closes=[100]), "head_ref": "fix/100-plain"}],
        check_runs={"a" * 40: all_green()},
        attestation={"1001": {"verdict": "head"}},
        merge_tree={"1001": {"clean": True}},
    )
    (row,) = units_for(s)
    argv = shlex.split(next_command(row, REPO) or "")
    assert argv[-1] == "fix/100-plain"
    assert argv[0] == "bash"


# ── carry-patch is NOT a review cycle (round 6, #4) ───────────────────────
def test_carry_patch_asks_for_a_re_record_not_a_review():
    """`record-review.sh` exit 6: re-record at the current head, do NOT review.

    The action offered was "dispatch a reviewer … --evidence comment:<id>" — a
    wasted review, and a nudge toward the fabricated-evidence shape the recorder
    warns about.
    """
    from tools.next_work import next_action

    s = snap(
        issues=[issue(100)],
        pulls=[{**pull(1001, closes=[100]), "head_ref": "fix/100-x"}],
        check_runs={"a" * 40: all_green()},
        attestation={"1001": {"verdict": "carry-patch"}},
        merge_tree={"1001": {"clean": True}},
    )
    (row,) = units_for(s)
    assert row.position == "NEEDS-REVIEW"
    action = next_action(row, REPO) or ""
    assert "re-record" in action
    assert "reviewer" not in action and "--evidence" not in action


# ── a failed gate read must be recorded, not silently substituted ─────────
def test_a_failed_gate_read_is_recorded_as_incomplete(tmp_path: Path, monkeypatch):
    """The checkout's own `.mergify.yml` may be an uncommitted edit.

    Reading it after `origin/main` failed produced "no unmet conditions" →
    READY-TO-LAND with a command, while the module's own exit-2 list says an
    unreadable gate is INCOMPLETE.

    ⛔ BEHAVIOUR, NOT A SOURCE WINDOW. This was pinned by splitting the module's source on
    `FALLBACK — origin/` and asserting `snap.unreadable.append` somewhere in the next 1600
    characters — and the `except (ConfigError, OSError)` handler's OWN append sat inside that
    window (~700 characters later), so the assertion was satisfied by an unrelated line: the
    fallback could stop recording its failure (keep the message, `print` it to stderr
    instead) and the pin stayed green. The arm below drives `collect` against a real
    directory with a `.mergify.yml` on disk and no `origin/main` to read, so the only way it
    passes is if the fallback actually records the refusal.
    """
    from tools import next_work

    root = tmp_path / "repo"
    root.mkdir()
    (root / ".mergify.yml").write_text(
        "queue_rules:\n  - name: q\n    queue_conditions:\n      - base=main\n"
        "      - check-success=docs\n",
        encoding="utf-8",
    )

    class FakeClient:
        def _get_list(self, path, params="", select=".[]"):
            return []

        def list_check_runs(self, repo, sha):
            return []

    monkeypatch.setattr(next_work, "GhCli", lambda: FakeClient())
    monkeypatch.setattr(next_work, "_search_issues", lambda *a, **k: [])
    monkeypatch.setattr(next_work, "_local_holders", lambda *a, **k: {})
    monkeypatch.setattr(next_work, "_claim_verdicts", lambda *a, **k: None)

    snap_, cfg, _ = next_work.collect(REPO, root)
    assert cfg is not None, "the fallback copy on disk must still be parsed"
    assert any("fell back to" in u for u in snap_.unreadable), snap_.unreadable
    assert any(".mergify.yml" in u for u in snap_.unreadable), snap_.unreadable


def next_work_module_file() -> str:
    import tools.next_work as mod

    return mod.__file__ or ""


def test_a_broken_pipe_exits_2_not_120(tmp_path: Path):
    """The documented contract is 0/1/2; a piped run was exiting 120.

    `print` only fills the buffer, so the closed-pipe error surfaced at the
    interpreter's SHUTDOWN flush and CPython turned it into 120 — an undocumented
    code on the same command family (<8 KB → 0, 8,489 B → 120, 58,653 B → 120,
    `--json` on the same data → 2). The flush is now inside the guard and stdout
    is pointed at devnull so the shutdown flush has somewhere to go.

    ⛔ WHAT THIS PINS, EXACTLY: the documented exit for a broken pipe. It does NOT
    isolate the `dup2` epilogue — `BrokenPipeError` subclasses `OSError`, so the
    general catch-all also answers 2 (measured: removing the dedicated handler
    alone leaves this green). Removing the WHOLE guard chain reds it. The docstring
    said otherwise once; it says this now.
    """
    raw = _rich_snapshot().to_json()
    # big enough to exceed a pipe buffer
    # big enough to GUARANTEE an EPIPE: a 64 KB pipe buffer swallows a small report
    # whole, and then the exit is honestly 0 because nothing broke.
    raw["issues"] = raw["issues"] + [issue(1000 + i) for i in range(8000)]
    root = Path(__file__).resolve().parent.parent
    # ⛔ `bash -o pipefail`, and run against a TMP fixture: a bare `cmd | head -1`
    # returns HEAD's status, so the earlier form asserted nothing about the tool
    # (deleting the whole BrokenPipeError handler left the suite green). Round 9.
    path = tmp_path / "pipe.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    proc = subprocess.run(
        ["bash", "-o", "pipefail", "-c",
         f"{sys.executable} {root}/tools/next_work.py --from {path} --repo {REPO} | head -1"],
        capture_output=True,
        text=True,
        cwd=root,
        timeout=120,
    )
    assert proc.returncode == 2, f"expected the documented INCOMPLETE, got {proc.returncode}"


# ── every interpolated value is shell-quoted, not just the branch ─────────
HOSTILE_VALUES = [
    "fix/100-$(tar${IFS}czf${IFS}/tmp/x${IFS}$HOME)",
    "fix/100-`id`",
    'fix/100-"quoted"',
    "fix/100-;rm -rf /",
    "fix/100-a b",
    "fix/100-'single",
    "fix/100-$HOME",
    "fix/100-*",
]


@pytest.mark.parametrize("hostile", HOSTILE_VALUES)
def test_a_hostile_branch_is_one_inert_argument(hostile):
    """`git check-ref-format` permits `$(`, backticks, `"`, `;`, spaces and `*`.

    A branch name is UNTRUSTED INPUT, and the emitted command is the one string
    this tool exists to hand a runner. Two checks: the shell SPLITS it back to the
    original value (so the rail receives it intact) and a real `sh` never
    expands it (so nothing runs).
    """
    import shlex

    s = snap(
        issues=[issue(100)],
        pulls=[{**pull(1001, closes=[100]), "head_ref": hostile}],
        check_runs={"a" * 40: all_green()},
        attestation={"1001": {"verdict": "head"}},
        merge_tree={"1001": {"clean": True}},
    )
    (row,) = units_for(s)
    cmd = next_command(row, REPO) or ""
    argv = shlex.split(cmd)
    assert argv[-1] == hostile, f"the ref is not recoverable as ONE argument: {argv}"

    # ⛔ EXECUTE THE EMITTED STRING, with the rail replaced by `printf`. An earlier
    # version wrapped the WHOLE command in `shlex.quote`, so the shell saw one word
    # and the assertion held for every input. This one expands for real: an
    # unquoted `$( … )` or `` `id` `` changes the output, so the guard discriminates.
    fragment = cmd.replace(
        "bash scripts/orchestrator-harness/merge-one.sh . ", "printf '%s\\n' ", 1
    )
    proc = subprocess.run(["sh", "-c", fragment], capture_output=True, text=True, timeout=30)
    lines = proc.stdout.split("\n")
    assert hostile in lines, f"a shell did not receive the literal ref: {proc.stdout!r}"


@pytest.mark.parametrize("hostile", HOSTILE_VALUES)
def test_a_hostile_repo_slug_is_one_inert_argument(hostile):
    """The first fix quoted the BRANCH and left five rail lines interpolating the repo.

    `_repo_slug`'s regex accepts `;`, `$(` and spaces from a remote URL, and
    `--repo`/`--from` carry it too. Measured: `evil/repo;touch /tmp/PWNED` split
    into two argv words and the second command RAN.
    """
    import shlex

    bad = f"evil/{hostile}"
    s = snap(
        issues=[issue(100)],
        pulls=[{**pull(1001, closes=[100]), "head_ref": "fix/100-x"}],
        check_runs={"a" * 40: all_green()},
        attestation={"1001": {"verdict": "head"}},
        merge_tree={"1001": {"clean": True}},
    )
    (row,) = units_for(s)
    cmd = next_command(row, bad) or ""
    argv = shlex.split(cmd)
    assert bad in argv, f"the repo slug was not passed as ONE argument: {argv}"
    assert argv[0] == "bash"
    # nothing beyond the rail's own argv: the injected shell metacharacters must not
    # have produced extra words that a shell would treat as commands
    assert not any(a.startswith("touch") for a in argv), argv
    # execute it: an unquoted slug would split into extra words and the literal
    # slug would not survive as one line
    fragment = cmd.replace(
        "bash scripts/orchestrator-harness/merge-one.sh . ", "printf '%s\\n' ", 1
    )
    proc = subprocess.run(["sh", "-c", fragment], capture_output=True, text=True, timeout=30)
    assert bad in proc.stdout.split("\n"), f"a shell did not receive the literal slug: {proc.stdout!r}"


def test_an_unresolvable_repo_is_incomplete_not_nothing_to_do(tmp_path: Path, monkeypatch):
    """exit 2 is "could not check"; exit 1 means the queue was read and answered."""
    from tools import next_work

    monkeypatch.setattr(next_work, "_run", lambda *a, **k: subprocess.CompletedProcess(
        list(a[0]) if a else [], 128, "", "fatal: not a git repository"
    ))
    assert next_work.main([]) == 2


# ── a held row still OWNS its action target (round 8, #2) ─────────────────
def test_a_claim_on_one_issue_blocks_the_pr_it_shares_with_another():
    """A CLAIMED row was skipped before registering its PR, so a co-closing row
    re-offered `merge-one.sh` for an artifact whose work is held — refused only at
    merge time by the pr-<N> claim instead of here."""
    s = snap(
        issues=[issue(100), issue(101), issue(102)],
        pulls=[{**pull(2001, closes=[100, 101, 102]), "head_ref": "fix/2001-big"}],
        check_runs={"a" * 40: all_green()},
        attestation={"2001": {"verdict": "head"}},
        merge_tree={"2001": {"clean": True}},
        claims={"refs/claims/issue-100": {"verdict": "claim-held", "owner": "lane:1"}},
    )
    rows = units_for(s)
    cmds = [next_command(u, REPO) for u in rows if next_command(u, REPO)]
    assert cmds == [], f"a held PR was re-offered: {cmds}"
    held = next(u for u in rows if u.issue == 100)
    for other in (u for u in rows if u.issue in (101, 102)):
        assert other.duplicate_of == held.key, other


# ── nodigest / unreadable are not review debts (round 8, #3) ──────────────
@pytest.mark.parametrize(
    ("verdict", "want", "forbidden"),
    [
        ("nodigest", "re-record", "reviewer"),
        ("carry-patch", "re-record", "reviewer"),
        ("unreadable", "retry", "reviewer"),
        ("norecord", "reviewer", "re-record"),
        ("fresh", "reviewer", "re-record"),
    ],
)
def test_each_attestation_verdict_gets_the_action_its_recorder_prescribes(
    verdict, want, forbidden
):
    """`record-review.sh` is the authority on what each verdict owes.

    exit 5 nodigest = "a CARRY, not a review"; exit 3 unreadable = "RETRY, do NOT
    re-review"; exit 6 carry-patch = "RE-RECORD ... do NOT run a review cycle".
    Only `norecord`/`fresh` owe a review. Offering a review for the others burned
    20-40 minutes and nudged toward evidence that does not exist.
    """
    from tools.next_work import next_action

    s = snap(
        issues=[issue(100)],
        pulls=[{**pull(1001, closes=[100]), "head_ref": "fix/100-x"}],
        check_runs={"a" * 40: all_green()},
        attestation={"1001": {"verdict": verdict}},
        merge_tree={"1001": {"clean": True}},
    )
    (row,) = units_for(s)
    action = (next_action(row, REPO) or "").lower()
    assert want in action, action
    assert forbidden not in action, action


# ── a fold can itself be a CONSTITUENT of a bigger fold (round 9, #1) ─────
def test_a_fold_that_is_a_constituent_is_not_pickable():
    """The #10902 failure, one level up.

    The issue path applied `superseded`; the fold path did not, so a `batch-fold`
    PR carrying `superseded-by-fold` computed READY-TO-LAND and was handed the
    command that lands it SEPARATELY and ahead of the fold carrying it — for a PR
    the repo's own entry gate refuses.
    """
    inner = pull(900, head="9" * 40, labels=[FOLD_LABEL, CONSTITUENT_LABEL],
                 body="fold-constituents: 1001")
    s = snap(
        issues=[issue(100)],
        pulls=[inner, pull(1001, closes=[100])],
        check_runs={"9" * 40: all_green(), "a" * 40: all_green()},
        attestation={"900": {"verdict": "head"}, "1001": {"verdict": "head"}},
        merge_tree={"900": {"clean": True}, "1001": {"clean": True}},
    )
    rows = units_for(s)
    inner_row = next(u for u in rows if u.kind == "fold")
    assert inner_row.reason == "SUPERSEDED-BY-FOLD", inner_row
    assert next_command(inner_row, REPO) is None
    assert not any(next_command(u, REPO) for u in rows)


def test_an_outer_fold_supersedes_the_fold_it_declares():
    inner = pull(900, head="9" * 40, labels=[FOLD_LABEL], body="fold-constituents: 1001")
    outer = pull(901, head="8" * 40, labels=[FOLD_LABEL], body="fold-constituents: 900")
    s = snap(
        issues=[], pulls=[inner, outer],
        check_runs={"9" * 40: all_green(), "8" * 40: all_green()},
        attestation={"900": {"verdict": "head"}, "901": {"verdict": "head"}},
        merge_tree={"900": {"clean": True}, "901": {"clean": True}},
    )
    rows = units_for(s)
    inner_row = next(u for u in rows if u.fold_pr == 900)
    assert inner_row.reason == "SUPERSEDED-BY-FOLD"
    pick = next(u for u in rows if u.pickable)
    assert pick.fold_pr == 901, "the OUTER fold is the unit"


# ── a DRAFT is not something a fixer is dispatched at (round 9, #2) ───────
def test_a_draft_gets_an_action_not_a_command():
    """`-draft` is a live entry condition and a draft is normally a human mid-work."""
    from tools.next_work import next_action

    s = snap(
        issues=[issue(100)],
        pulls=[{**pull(1001, closes=[100], draft=True), "head_ref": "fix/100-wip"}],
        check_runs={"a" * 40: all_green()},
        attestation={"1001": {"verdict": "head"}},
        merge_tree={"1001": {"clean": True}},
    )
    (row,) = units_for(s)
    assert row.draft and row.pickable
    assert next_command(row, REPO) is None, "a fixer was dispatched at a draft"
    assert not row.executable
    assert "DRAFT" in (next_action(row, REPO) or "")


# ── the documented order is gate → attestation → merge (round 10) ─────────
def test_a_missing_review_is_surfaced_even_when_the_merge_is_unmeasured():
    """The merge abstention ran FIRST, so an owed review was reported as "waiting".

    Measured before the fix: unmeasured merge_tree + attestation `norecord` →
    NEEDS-TEST / "wait — in a runner queue", and the review that was actually owed
    never appeared. The documented order (gate → attestation → merge) now holds.
    """
    s = snap(
        issues=[issue(100)],
        pulls=[{**pull(1001, closes=[100]), "head_ref": "fix/100-x"}],
        check_runs={"a" * 40: all_green()},
        attestation={"1001": {"verdict": "norecord"}},
        merge_tree={"1001": {"clean": None, "why": "head not available locally"}},
    )
    (row,) = units_for(s)
    assert row.position == "NEEDS-REVIEW", row.detail
    assert any("attestation: norecord" in d for d in row.detail)
    assert any("UNMEASURED" in d for d in row.detail), "the merge note must still be recorded"


def test_json_refused_run_ships_no_pickable_row(tmp_path: Path, capsys):
    """A machine consumer keying on `pickable` must not act on a REFUSED run."""
    from tools import next_work

    raw = _rich_snapshot().to_json()
    raw["unreadable"] = ["GH-TIMEOUT(search:P0-blocker)"]
    path = tmp_path / "refused.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    assert next_work.main(["--from", str(path), "--repo", REPO, "--json"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["refused"] is True
    assert payload["units"], "the rows are still reported"
    assert all(u["pickable"] is False for u in payload["units"])


# ── a wrong-typed merge verdict must not read as MEASURED CLEAN (round 12) ─
@pytest.mark.parametrize("bad", [0, "false", "yes", 1, {}, [], "true"])
def test_a_non_boolean_merge_verdict_is_an_abstention(bad):
    """The polarity was `is False` / `is None`, so every OTHER value fell through.

    `0` and `"false"` are the values a hand-written snapshot most plausibly carries
    for "conflicted"; either one read as MEASURED CLEAN and got the merge rail at
    exit 0, for a tree that was never shown to merge. Every other field in this
    module fails closed on an unrecognised value — attestation, claims, the gate.
    """
    s = snap(
        issues=[issue(100)],
        pulls=[{**pull(1001, closes=[100]), "head_ref": "fix/100-x"}],
        check_runs={"a" * 40: all_green()},
        attestation={"1001": {"verdict": "head"}},
        merge_tree={"1001": {"clean": bad}},
    )
    (row,) = units_for(s)
    assert row.position == "NEEDS-TEST", row.detail
    assert next_command(row, REPO) is None
    assert any("UNMEASURED" in d for d in row.detail)


def test_a_true_merge_verdict_still_lands():
    """The complement: a real boolean True must not be swept into the abstention."""
    s = snap(
        issues=[issue(100)],
        pulls=[{**pull(1001, closes=[100]), "head_ref": "fix/100-x"}],
        check_runs={"a" * 40: all_green()},
        attestation={"1001": {"verdict": "head"}},
        merge_tree={"1001": {"clean": True}},
    )
    (row,) = units_for(s)
    assert row.position == "READY-TO-LAND"
    assert (next_command(row, REPO) or "").startswith("bash ")


# ── a red review gate means A REVIEW IS OWED, not "fix the code" (r14 #1) ──
def _live_gate_runs(*, review_red: bool) -> list[dict]:
    """Runs for the REAL gate: every check green except `ai-review-gate`."""
    names = ["pricing-artifact", "docs", "test-isolation", "license-surface",
             "legal-e2e", "ai-review-gate"]
    return [
        {
            "id": i,
            "name": n,
            "status": "completed",
            "conclusion": "failure" if (review_red and n == "ai-review-gate") else "success",
            "app": {"slug": "github-actions"},
        }
        for i, n in enumerate(names)
    ]


def _live_cfg():
    from tools.next_work import parse_config

    return parse_config((Path(__file__).resolve().parent.parent / ".mergify.yml").read_text())


def test_a_red_review_gate_asks_for_a_review_not_a_fixer():
    """`.mergify.yml`: `ai-review-gate` is "red by design until a review is recorded".

    Bucketing it with the failing checks made the tool dispatch a FIXER at a PR
    whose reviewer left no findings, and made NEEDS-REVIEW unreachable on the live
    gate (gate-green ⇒ record bound ⇒ READY-TO-LAND; gate-red ⇒ NEEDS-FIX).
    Measured live: #10858 (`norecord`), #10833 and #10575 (`fresh`) each got
    `fix-one.sh`.
    """
    from tools.next_work import next_action

    s = snap(
        issues=[issue(100)],
        pulls=[{**pull(1001, closes=[100]), "head_ref": "fix/100-x"}],
        check_runs={"a" * 40: _live_gate_runs(review_red=True)},
        attestation={"1001": {"verdict": "norecord"}},
        merge_tree={"1001": {"clean": True}},
    )
    (row,) = build_units(s, _live_cfg())
    assert row.position == "NEEDS-REVIEW", row.detail
    assert next_command(row, REPO) is None, "a fixer was dispatched at a review debt"
    assert "reviewer" in (next_action(row, REPO) or "")


def test_a_red_review_gate_with_a_bound_record_asks_a_HUMAN_not_a_fixer():
    """The complement: a bound record + red gate is not a code problem either.

    `record-review.sh`'s own header (#10677): a record whose SIGNED body marker never
    reached the PR is invisible to the required check, so the gate correctly reports
    "No AI review evidence found" and a properly reviewed PR is blocked by a
    BOOKKEEPING failure (remedy `--flush-pending`). The tool cannot tell that from a
    genuinely failing check, so neither gets a fixer at approved code.
    """
    from tools.next_work import next_action

    s = snap(
        issues=[issue(100)],
        pulls=[{**pull(1001, closes=[100]), "head_ref": "fix/100-x"}],
        check_runs={"a" * 40: _live_gate_runs(review_red=True)},
        attestation={"1001": {"verdict": "head"}},
        merge_tree={"1001": {"clean": True}},
    )
    (row,) = build_units(s, _live_cfg())
    assert row.position == "NEEDS-REVIEW", row.detail
    assert next_command(row, REPO) is None, "a fixer was dispatched at approved code"
    assert "flush-pending" in (next_action(row, REPO) or "")


def test_a_real_check_failure_is_still_needs_fix():
    runs = _live_gate_runs(review_red=False)
    runs[1]["conclusion"] = "failure"  # docs
    s = snap(
        issues=[issue(100)],
        pulls=[{**pull(1001, closes=[100]), "head_ref": "fix/100-x"}],
        check_runs={"a" * 40: runs},
        attestation={"1001": {"verdict": "norecord"}},
        merge_tree={"1001": {"clean": True}},
    )
    (row,) = build_units(s, _live_cfg())
    assert row.position == "NEEDS-FIX"


def test_the_review_gate_names_are_still_in_the_live_config():
    """A rename in `.mergify.yml` must not silently reclassify the review debt."""
    from tools.next_work import REVIEW_GATE_CHECKS

    cfg = _live_cfg()
    for name in REVIEW_GATE_CHECKS:
        assert name in cfg.required_checks, (
            f"{name!r} is no longer a required check — a red review gate would read as a code failure"
        )


def test_an_empty_repo_slug_emits_no_command():
    """`'' 1001` is not the exact command a runner would execute."""
    s = snap(
        issues=[issue(100)],
        pulls=[{**pull(1001, closes=[100]), "head_ref": "fix/100-x"}],
        check_runs={"a" * 40: all_green()},
        attestation={"1001": {"verdict": "head"}},
        merge_tree={"1001": {"clean": True}},
    )
    (row,) = units_for(s)
    assert next_command(row, "") is None


def test_an_unmeasured_merge_does_not_pretend_a_runner_queue():
    from tools.next_work import next_action

    s = snap(
        issues=[issue(100)],
        pulls=[{**pull(1001, closes=[100]), "head_ref": "fix/100-x"}],
        check_runs={"a" * 40: all_green()},
        attestation={"1001": {"verdict": "head"}},
        merge_tree={"1001": {"clean": None, "why": "head not available locally"}},
    )
    (row,) = units_for(s)
    assert row.position == "NEEDS-TEST"
    action = next_action(row, REPO) or ""
    assert "runner queue" not in action
    assert "merge tree could not be measured" in action


def test_executable_documents_both_no_action_causes():
    """The `executable` docstring must not claim every NEEDS-TEST is a runner wait.

    It said "the head is waiting on a runner" while `next_action` correctly says
    "nothing is in a runner queue" for an UNMEASURED merge tree. A gloss that names
    one of two causes is the class of sentence this file keeps having to correct, so
    it is pinned rather than trusted.
    """
    from tools.next_work import Unit

    doc = (Unit.executable.__doc__ or "").lower()
    assert "runner" in doc
    assert "measured" in doc, "the docstring omits the unmeasured-merge-tree cause"


# ── the NO-PR rail must PROVISION, and a missing claims map must refuse (r16) ─
def test_the_no_pr_command_uses_the_rail_that_creates_the_worktree():
    """`lane.sh` refuses unless the caller is ALREADY in a worktree on the branch.

    Measured on a throwaway clone: `lane.sh --repo … 10955 lane/10955` exits 72
    (`reason=branch-missing`), and no fleet tool creates a `lane/<N>` branch — the
    real dispatch provisions `feat/<N>-tm` via `wt_provision`. The NO-PR row is the
    most numerous position, so the emitted command must be the rail that does the
    provisioning (`dispatch-merge.sh`), not one that assumes it.
    """
    s = snap(issues=[issue(10955)], pulls=[])
    (row,) = units_for(s)
    assert row.position == "NO-PR"
    cmd = next_command(row, REPO) or ""
    assert "dispatch-merge.sh" in cmd, cmd
    assert "lane.sh" not in cmd, "lane.sh assumes a worktree this command never creates"
    assert "10955" in cmd


@pytest.mark.parametrize("drop", ["claims", "gate"])
def test_a_snapshot_missing_a_decision_input_is_incomplete(tmp_path: Path, drop):
    """No claim map is not "nothing is held" — the registry was never consulted."""
    raw = _rich_snapshot().to_json()
    raw.pop(drop)
    path = tmp_path / f"no-{drop}.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    assert _run_main(["--from", str(path), "--repo", REPO]) == 2


def test_an_unreadable_fetched_tip_refuses_the_measurement(monkeypatch):
    """`if got and got != head` treated an EMPTY rev-parse as a match.

    The comment above it says the whole point is that the measurement must be bound
    to the head it is filed under; an unreadable tip is a mismatch.
    """
    from tools import next_work

    calls: list[list[str]] = []

    def fake_run(args, **kwargs):
        calls.append(list(args))
        if args[:2] == ["git", "cat-file"]:
            return subprocess.CompletedProcess(list(args), 1, "", "missing")
        if args[:2] == ["git", "fetch"] and any("refs/pull" in a for a in args):
            return subprocess.CompletedProcess(list(args), 0, "", "")
        if args[:2] == ["git", "fetch"]:
            return subprocess.CompletedProcess(list(args), 1, "", "no sha fetch")
        if args[:2] == ["git", "rev-parse"]:
            return subprocess.CompletedProcess(list(args), 0, "", "")  # EMPTY
        if args[:2] == ["git", "merge-tree"]:
            raise AssertionError("merge-tree must not run on an unverified ref")
        return subprocess.CompletedProcess(list(args), 0, "", "")

    monkeypatch.setattr(next_work, "_run", fake_run, raising=True)
    s = Snapshot()
    pr = {"number": 7, "head": "a" * 40}
    next_work._merge_tree(Path("."), "origin/main", pr, s)
    assert s.merge_tree["7"]["clean"] is None
    assert "moved" in s.merge_tree["7"]["why"]


@pytest.mark.parametrize("bad", [[], "", None, "x", 0, [1, 2]])
def test_a_non_map_claim_surface_is_incomplete(tmp_path: Path, bad):
    """Key presence is not enough: `[]` and `""` are falsy and read as "nothing held".

    `(self.claims or {})` swallowed them, so `ref not in snap.claims` answered
    "absent" for every key and the run emitted a merge command at exit 0 — the same
    fail-open as a missing map, one shape over.
    """
    raw = _rich_snapshot().to_json()
    raw["claims"] = bad
    path = tmp_path / "bad-claims.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    assert _run_main(["--from", str(path), "--repo", REPO]) == 2, bad


# ── ONE table for the shape of every decision input (r18) ─────────────────
@pytest.mark.parametrize(
    ("field", "bad"),
    [
        ("unreadable", ""), ("unreadable", {}), ("unreadable", 0),
        ("issues", {}), ("issues", ""),
        ("pulls", {}), ("pulls", ""),
        ("claims", []), ("claims", ""), ("claims", None), ("claims", 0),
        ("attestation", []), ("attestation", ""),
        ("merge_tree", []), ("merge_tree", ""),
        ("check_runs", []), ("local_holders", ""),
        ("gate", ""), ("gate", []),
    ],
)
def test_a_wrong_typed_decision_input_is_incomplete(tmp_path: Path, field, bad):
    """Every one of these read as EMPTY before, i.e. "nothing is held / nothing unreadable".

    Five were found one review round at a time, each measured as exit 0 WITH a
    runnable command from an input on which that surface was never consulted — so
    the shape check is a TABLE at the single door, not a guard per field.
    """
    raw = _rich_snapshot().to_json()
    raw[field] = bad
    path = tmp_path / f"bad-{field}.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    assert _run_main(["--from", str(path), "--repo", REPO]) == 2, (field, bad)


def test_a_wrong_typed_issues_or_pulls_is_named_not_a_TypeError(tmp_path: Path):
    """The CONTAINER is judged before its ELEMENTS are iterated, and the refusal NAMES it.

    ⛔ THIS TEST ASSERTS THE MESSAGE, NOT THE EXIT CODE, and that is the whole point.
    `test_a_wrong_typed_decision_input_is_incomplete` above parametrises `{}` and `""`
    for `issues`/`pulls` — both iterate harmlessly, so both produced the field-named
    refusal even BEFORE the guard, and the inputs that actually crashed (`5`, `None`,
    `True`, `1.5`) reached the outer frame's `except (..., TypeError, ...)` handler,
    which also returns 2. An exit-code assertion therefore passes on the buggy code
    and cannot tell the fix from its absence — measured: removing the guard left the
    whole suite green. So pin the two things only the fix produces: no `TypeError`,
    and a message naming the field as not a list.

    Measured before the fix (each raised out of `from_json`):
        issues: 5 / null / true / 1.5 -> TypeError: '<type>' object is not iterable
        pulls: 7                      -> TypeError: 'int' object is not iterable
    After it, each is `unreadable` with e.g.
        "issues: the snapshot's issues is int, not a list — that surface was never read"
    and the CLI refuses (exit 2) carrying that message, not a traceback summary.
    """
    for field, bad, named in (
        ("issues", 5, "issues is int, not a list"),
        ("issues", None, "issues is NoneType, not a list"),
        ("issues", True, "issues is bool, not a list"),
        ("issues", 1.5, "issues is float, not a list"),
        ("pulls", 7, "pulls is int, not a list"),
        ("pulls", None, "pulls is NoneType, not a list"),
    ):
        raw = _rich_snapshot().to_json()
        raw[field] = bad
        # ⛔ The `TypeError` this replaced escaped HERE, before any refusal existed.
        snap = Snapshot.from_json(raw)
        assert any(named in u for u in snap.unreadable), (field, bad, snap.unreadable)
        path = tmp_path / f"bad-{field}-{type(bad).__name__}.json"
        path.write_text(json.dumps(raw), encoding="utf-8")
        rc = _run_main(["--from", str(path), "--repo", REPO])
        assert rc == 2, (field, bad)


def test_a_claim_map_under_another_namespace_is_incomplete(tmp_path: Path):
    """`claim_ns` travels with the data; keys under a different prefix are not it."""
    raw = _rich_snapshot().to_json()
    raw["claim_ns"] = "refs/claims-other"
    path = tmp_path / "wrong-ns.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    assert _run_main(["--from", str(path), "--repo", REPO]) == 2


def test_an_empty_but_right_typed_input_is_still_valid(tmp_path: Path):
    """The complement: `{}` / `[]` of the RIGHT type is a legitimate measurement."""
    raw = _rich_snapshot().to_json()
    raw["claims"] = {}
    raw["unreadable"] = []
    path = tmp_path / "clean.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    assert _run_main(["--from", str(path), "--repo", REPO]) in (0, 1)


@pytest.mark.parametrize("bad", [[], {}, 0, False, 1])
def test_a_non_string_claim_namespace_is_incomplete(tmp_path: Path, bad):
    """`claim_ns` was coerced before the shape door, so a falsy wrong type vanished.

    Measured: `claim_ns: []` exited 0 with a runnable merge command.
    """
    raw = _rich_snapshot().to_json()
    raw["claim_ns"] = bad
    path = tmp_path / "bad-ns.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    assert _run_main(["--from", str(path), "--repo", REPO]) == 2, bad


@pytest.mark.parametrize("bad_value", ["", {}, 0, False, "x"])
def test_a_local_holder_value_that_is_not_a_list_is_incomplete(tmp_path: Path, bad_value):
    """A judged CONTAINER is not a judged ELEMENT.

    `{"issue:101": ""}` is falsy and iterates empty, so the unit read FREE and was
    handed a runnable command at exit 0 for work the snapshot's own key says is held.
    """
    raw = _rich_snapshot().to_json()
    raw["local_holders"] = {"issue:101": bad_value}
    path = tmp_path / "bad-holders.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    assert _run_main(["--from", str(path), "--repo", REPO]) == 2, bad_value


@pytest.mark.parametrize(
    ("field", "bad"),
    [
        ("labels", 0), ("labels", ""), ("labels", {}), ("labels", None),
        ("closing_issues", 0), ("closing_issues", ""), ("closing_issues", "101"),
        ("draft", 0), ("draft", ""), ("draft", []),
        ("base", 0), ("base", ""), ("base", []), ("base", None),
        ("head", 0), ("head_ref", 1), ("number", "1001"),
    ],
)
def test_a_wrong_typed_pull_field_is_incomplete(tmp_path: Path, field, bad):
    """One level down, the same erasure as the top-level fields.

    Measured before the fix: `labels: 0` on a PR holding `superseded-by-fold` read as
    no labels and handed out a merge command (#10902); `closing_issues: 0` produced a
    NO-PR dispatch for work an existing PR carries; `draft: 0` read as not-a-draft;
    `base: ""` produced a fixer at an unread base.
    """
    raw = _rich_snapshot().to_json()
    raw["pulls"][0][field] = bad
    path = tmp_path / f"pull-{field}.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    assert _run_main(["--from", str(path), "--repo", REPO]) == 2, (field, bad)


def test_an_absent_pull_field_is_a_legitimate_omission(tmp_path: Path):
    """The complement: a hand-written snapshot need not carry every field."""
    raw = _rich_snapshot().to_json()
    for key in ("labels", "closing_issues", "refs_issues", "draft", "base", "head_ref"):
        raw["pulls"][0].pop(key, None)
    path = tmp_path / "lean.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    assert _run_main(["--from", str(path), "--repo", REPO]) in (0, 1)


@pytest.mark.parametrize("bad", [True, False, 0, -1, "1001", None, [], {}])
def test_a_pull_number_that_is_not_a_usable_int_is_incomplete(tmp_path: Path, bad):
    """`isinstance(True, int)` is True, so `number: true` passed the door.

    The row then addressed PR #1001 while the emitted command merged **#1** — a
    runnable command aimed at the wrong artifact. A boolean, a zero, a negative and
    an absent number are all unaddressable.
    """
    raw = _rich_snapshot().to_json()
    raw["pulls"][0]["number"] = bad
    path = tmp_path / "bad-number.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    assert _run_main(["--from", str(path), "--repo", REPO]) == 2, bad
    # …and the door's OWN message reaches the operator, rather than a KeyError/
    # TypeError from the outer frame burying it.
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve().parent.parent / "tools" / "next_work.py"),
         "--from", str(path), "--repo", REPO],
        capture_output=True, text=True, timeout=120,
    )
    assert "pull number" in proc.stdout, proc.stdout[-300:] + proc.stderr[-300:]


@pytest.mark.parametrize(
    ("field", "bad_elements"),
    [
        ("closing_issues", [True]),
        ("closing_issues", ["100"]),
        ("refs_issues", [True]),
        ("labels", [1]),
        ("labels", [None]),
        ("labels", [True]),
    ],
)
def test_a_wrong_typed_list_ELEMENT_is_incomplete(tmp_path: Path, field, bad_elements):
    """A judged container is not a judged element.

    `closing_issues: [true]` coerces to `int(True) == 1` and silently re-points the
    link, reproducing the NO-PR dispatch for work an existing PR carries — the same
    harm the container check was added for, reached through an element.
    """
    raw = _rich_snapshot().to_json()
    raw["pulls"][0][field] = bad_elements
    path = tmp_path / f"bad-{field}-el.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    assert _run_main(["--from", str(path), "--repo", REPO]) == 2, (field, bad_elements)


@pytest.mark.parametrize("field", ["repo", "base", "config_source"])
@pytest.mark.parametrize("bad", [None, [], {}, 0, True, 1.5])
def test_a_non_string_identity_field_is_incomplete(tmp_path: Path, field, bad):
    """`if value is not None` exempted a PRESENT `null` — the case the door exists for."""
    raw = _rich_snapshot().to_json()
    raw[field] = bad
    path = tmp_path / f"bad-{field}.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    assert _run_main(["--from", str(path), "--repo", REPO]) == 2, (field, bad)


@pytest.mark.parametrize("bad", ["x", None, True, [], 1.5])
def test_a_non_int_collected_at_is_incomplete(tmp_path: Path, bad):
    """Provenance only — but the docstring says EVERY field is judged."""
    raw = _rich_snapshot().to_json()
    raw["collected_at"] = bad
    path = tmp_path / "bad-collected.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    assert _run_main(["--from", str(path), "--repo", REPO]) == 2, bad


# ── issues[].number is judged like a pull number (r21 #1) ─────────────────
@pytest.mark.parametrize("bad", [True, False, 0, -1, 100.9, "100", None, [], {}])
def test_an_issue_number_that_is_not_a_usable_int_is_incomplete(tmp_path: Path, bad):
    """`isinstance(True, int)` is True and `int(100.9) == 100` succeeds, so the outer
    frame raised nothing: `issues=[{"number": true}]` produced a dispatch command for
    issue #1 at exit 0, for an issue the snapshot never named."""
    raw = _rich_snapshot().to_json()
    raw["issues"][0]["number"] = bad
    path = tmp_path / "bad-issue-number.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    assert _run_main(["--from", str(path), "--repo", REPO]) == 2, bad


def test_a_wrong_typed_issue_priority_or_title_is_incomplete(tmp_path: Path):
    for field in ("priority", "title"):
        raw = _rich_snapshot().to_json()
        raw["issues"][0][field] = 7
        path = tmp_path / f"bad-issue-{field}.json"
        path.write_text(json.dumps(raw), encoding="utf-8")
        assert _run_main(["--from", str(path), "--repo", REPO]) == 2, field


# ── a gate refusal is a human edit, not a code fix (r21 #2) ──────────────
@pytest.mark.parametrize("kwargs", [{"base": "release/1.x"}, {"draft": True}])
def test_a_gate_refusal_dispatches_no_fixer(kwargs):
    """`base=`/`label=` were routed to NEEDS-FIX whose rail is `fix-one.sh`.

    A fixer at a PR whose only defect is its base or a missing label finds no
    findings, pushes nothing, and re-fails the chain.
    """
    from tools.next_work import next_action

    s = snap(
        issues=[issue(100)],
        pulls=[{**pull(1001, closes=[100]), "head_ref": "fix/100-x", **kwargs}],
        check_runs={"a" * 40: all_green()},
        attestation={"1001": {"verdict": "head"}},
        merge_tree={"1001": {"clean": True}},
    )
    (row,) = units_for(s)
    assert next_command(row, REPO) is None
    assert "HUMAN" in (next_action(row, REPO) or "") or "DRAFT" in (next_action(row, REPO) or "")


@pytest.mark.parametrize(
    ("condition", "want"),
    [
        (["draft"], "undraft"),
        (["base=main (actual release/1.x)"], "retarget"),
        (["base=main (actual unknown)"], "could not be READ"),
        (["label=ship-it: absent"], "add the required"),
    ],
)
def test_each_gate_refusal_prescribes_its_own_edit(condition, want):
    """One message for all four conditions prescribed the wrong edit for two of them."""
    from tools.next_work import _gate_refusal_edit

    text = _gate_refusal_edit(condition)
    assert want in text, text


@pytest.mark.parametrize("bad", ["x", 1, None, [1]])
def test_a_non_object_pull_element_is_refused_and_named(tmp_path: Path, bad):
    """An element that is not a pull was left to the outer frame, which raised in
    `build_units` before `render` — so the refusal never printed.

    ⛔ THIS TEST ASSERTS THE NAMED REFUSAL, NOT THE EXIT CODE (fold review, P0). It used to
    assert only `_run_main(...) == 2`, and `main`'s outer frame converts the crash into that
    same exit code — so the assertion was satisfied by BOTH the fixed and the broken code.
    Measured: deleting the guard at `tools/next_work.py:565-573` left this test green
    (`4 passed`) while the tool's output degraded from
    `pull element 1` to `AttributeError: 'int' object has no attribute 'get'`. That is the
    same trap `test_a_wrong_typed_issues_or_pulls_is_named_not_a_TypeError` documents in its
    own docstring, one sibling over. The refusal is now read from the PRIMARY SOURCE
    (`Snapshot.unreadable`, where the guard appends it), which the broken code cannot produce
    at all — it raises out of `from_json` first.
    """
    from tools.next_work import Snapshot

    raw = _rich_snapshot().to_json()
    raw["pulls"].append(bad)
    snap = Snapshot.from_json(raw)
    assert any("pull element" in u for u in snap.unreadable), (bad, snap.unreadable)
    # …and the refusal reaches the operator, rather than a traceback summary
    path = tmp_path / "bad-pull-element.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    assert _run_main(["--from", str(path), "--repo", REPO]) == 2, bad


def test_a_draft_action_names_the_edit():
    """The remedy ('undraft') appeared only in the row detail; the ACTION is what a
    runner reads."""
    from tools.next_work import next_action

    s = snap(
        issues=[issue(100)],
        pulls=[{**pull(1001, closes=[100], draft=True), "head_ref": "fix/100-wip"}],
        check_runs={"a" * 40: all_green()},
        attestation={"1001": {"verdict": "head"}},
        merge_tree={"1001": {"clean": True}},
    )
    (row,) = units_for(s)
    assert "UNDRAFT" in (next_action(row, REPO) or "").upper()


def test_every_human_case_is_named_in_help():
    """`--help` must enumerate the human cases the code emits, not just a review."""
    root = Path(__file__).resolve().parent.parent
    proc = subprocess.run(
        [sys.executable, str(root / "tools" / "next_work.py"), "--help"],
        capture_output=True, text=True, timeout=60,
    )
    text = proc.stdout.lower()
    for phrase in ("review", "gate refusal", "runner", "unmeasured merge tree", "draft"):
        assert phrase in text, phrase


# ── the target repo travels with the data (r23 #1) ───────────────────────
def test_a_replay_cannot_retarget_the_command_to_another_repo(tmp_path: Path):
    """`--repo <other>` on a snapshot collected for A emitted a command naming B.

    Every row, head, attestation and claim was read from A; the merge command named
    B, and `merge-one.sh` does not re-assert the slug against the checkout's origin.
    """
    raw = _rich_snapshot().to_json()
    assert raw["repo"] == REPO
    path = tmp_path / "snap.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    assert _run_main(["--from", str(path), "--repo", "someone-else/other-repo"]) == 2
    # agreement is still fine
    assert _run_main(["--from", str(path), "--repo", REPO]) in (0, 1)


def test_a_snapshot_with_no_repo_is_incomplete(tmp_path: Path):
    """No slug = no addressable command, and every emit site depends on one."""
    raw = _rich_snapshot().to_json()
    raw["repo"] = ""
    path = tmp_path / "no-repo.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    assert _run_main(["--from", str(path)]) == 2


# ── a gate refusal outranks an attestation verdict in the ACTION (r23 #3) ─
def test_a_gate_refusal_outranks_nodigest_in_the_action():
    """A wrong base AND `nodigest` got "re-record … needs NO new review" as its only
    ACTION, and the base mismatch appeared nowhere but the row detail."""
    from tools.next_work import next_action

    s = snap(
        issues=[issue(100)],
        pulls=[{**pull(1001, closes=[100]), "head_ref": "fix/100-x", "base": {"ref": "release/1.x"}}],
        check_runs={"a" * 40: all_green()},
        attestation={"1001": {"verdict": "nodigest"}},
        merge_tree={"1001": {"clean": True}},
    )
    (row,) = units_for(s)
    action = (next_action(row, REPO) or "")
    assert "gate refusal" in action, action
    assert "base=main (actual release/1.x)" in action


# ── a DRAFT with a failing check must not get a fixer (r23 #4) ────────────
def test_a_draft_with_a_failing_check_dispatches_no_fixer():
    """The one reachable combination the suite did not exercise.

    `draft` → structural → NEEDS-REVIEW normally, but a red required check wins
    first (NEEDS-FIX), so only the `unit.draft` guard in `next_command` prevents a
    fixer at the author's draft branch.
    """
    s = snap(
        issues=[issue(100)],
        pulls=[{**pull(1001, closes=[100], draft=True), "head_ref": "fix/100-wip"}],
        check_runs={"a" * 40: _runs_for_position("NEEDS-FIX")},
        attestation={"1001": {"verdict": "head"}},
        merge_tree={"1001": {"clean": True}},
    )
    (row,) = units_for(s)
    assert row.position == "NEEDS-FIX"
    assert row.draft
    assert next_command(row, REPO) is None, "a fixer was dispatched at a draft"


HOSTILE_REPO = "evil/repo;touch /tmp/PWNED"


@pytest.mark.parametrize(
    "verdict",
    ["nodigest", "carry-patch", "unreadable", "norecord", "fresh"],
)
def test_every_action_quotes_the_repo_slug(verdict):
    """The ACTION strings interpolated `repo` RAW while every COMMAND quoted it.

    `evil/repo;touch /tmp/PWNED` appeared verbatim in the NEXT ACTION block at
    exit 0 — the block's own instruction, on the code path whose docstring says
    "EVERY VALUE IN THIS STRING IS UNTRUSTED". An operator who copy-pastes an ACTION
    would run it; the regression tests only covered `next_command`.
    """
    from tools.next_work import next_action

    s = snap(
        issues=[issue(100)],
        pulls=[{**pull(1001, closes=[100]), "head_ref": "fix/100-x"}],
        check_runs={"a" * 40: all_green()},
        attestation={"1001": {"verdict": verdict}},
        merge_tree={"1001": {"clean": True}},
    )
    (row,) = units_for(s)
    action = next_action(row, HOSTILE_REPO) or ""
    # the QUOTED form is present, so a copy-paste is one inert argument
    assert shlex.quote(HOSTILE_REPO) in action, action


def test_the_header_does_not_call_a_parsed_gate_unreadable():
    """`gate {config_source or 'UNREADABLE'}` labelled a successfully parsed gate."""
    s = _rich_snapshot()
    s.config_source = ""
    text, _ = render(build_units(s, CFG), CFG, s, repo=REPO, top=5)
    head = text.splitlines()[0]
    assert "UNREADABLE" not in head, head


@pytest.mark.parametrize(
    ("kwargs", "want"),
    [
        ({"draft": True}, "DRAFT"),
        ({"base": {"ref": "release/1.x"}}, "GATE-REFUSED"),
    ],
)
def test_the_runnable_note_names_a_draft_or_a_gate_refusal(kwargs, want):
    """The epilog names five reasons a top row has no command; the ladder implemented three.

    A DRAFT top row printed "dispatch a reviewer …"? no — it printed the UNDRAFT
    ACTION and then, one line below, "the rank leader needs a REVIEW", the same
    "name the actual reason" defect the code above claims to have fixed.
    """
    s = snap(
        issues=[issue(100), issue(200)],
        pulls=[
            {**pull(1001, closes=[100]), "head_ref": "fix/100-x", **kwargs},
            {**pull(2002, closes=[200], head="b" * 40), "head_ref": "fix/200-x"},
        ],
        check_runs={"a" * 40: all_green(), "b" * 40: _runs_for_position("NEEDS-FIX")},
        attestation={"1001": {"verdict": "head"}, "2002": {"verdict": "head"}},
        merge_tree={"1001": {"clean": True}, "2002": {"clean": True}},
    )
    text, pick = render(build_units(s, CFG), CFG, s, repo=REPO, top=5)
    assert pick is not None
    assert "RUNNABLE" in text
    tail = text.split("RUNNABLE", 1)[1]
    assert want in tail, tail[:300]


def test_the_footer_does_not_call_rows_pickable_on_a_refused_run():
    """`TOP 1 PICKABLE` contradicted `--json`'s `pickable: false` on the same snapshot."""
    s = _rich_snapshot()
    s.unreadable = ["GH-TIMEOUT(search:P0-blocker)"]
    text, _ = render(build_units(s, CFG), CFG, s, repo=REPO, top=5)
    assert "TOP 0 PICKABLE" in text
    assert "REFUSED" in text


def test_a_malformed_pace_knob_cannot_decide_an_exit_code(
    monkeypatch, capsys
):
    """`NEXT_WORK_PACE` malformed → the default, a one-shot named warning, never an exit code.

    ⛔ This arm asserts `pace_seconds()`, and on its own it is UNFALSIFIABLE for the defect the
    fix exists for. A PARTIAL regression that keeps `pace_seconds()` and re-adds the
    module-level `PACE_SECONDS = float(os.environ.get(...))` leaves this arm GREEN, because
    `monkeypatch.setenv` runs after `tools.next_work` is already imported and the knob is not
    ambient. Measured: `1 passed`. `…_is_caught_in_a_FRESH_PROCESS` below is the arm that reds.
    """
    from tools import next_work

    # ⛔ TEST ISOLATION, not decoration. `_PACE_WARNED` is a PROCESS-WIDE one-shot
    # (`tools/next_work.py`): `monkeypatch` cannot restore it, so any earlier test in this
    # file that runs a paced call with a malformed `NEXT_WORK_PACE` leaves it True and the
    # `"NEXT_WORK_PACE" in err` assertion below then fails for a reason that has nothing to
    # do with the knob this test is about — a property of test ORDER, not of the code.
    # Latent today (no earlier test triggers it), so it will never show up as red on its own.
    monkeypatch.setattr(next_work, "_PACE_WARNED", False)

    monkeypatch.setenv("NEXT_WORK_PACE", "0.2s")
    assert next_work.pace_seconds() == next_work.PACE_SECONDS_DEFAULT
    err = capsys.readouterr().err
    assert "NEXT_WORK_PACE" in err and "0.2s" in err, err
    # the warning is emitted ONCE, not on every paced call
    next_work.pace_seconds()
    assert capsys.readouterr().err == ""

    monkeypatch.setenv("NEXT_WORK_PACE", "0")
    assert next_work.pace_seconds() == 0.0

    # ⛔ THE BRANCH EVERY REAL RUN TAKES. `NEXT_WORK_PACE` is unset in production, so
    # `raw is None or raw == "" -> PACE_SECONDS_DEFAULT` is the only arm that runs outside a
    # test. Without these lines nothing pins it: mutating that branch to `return 0.0` (pacing
    # silently disabled, i.e. the rate limits this pacing exists for) left the whole file GREEN
    # — nothing in it notices. ⛔ Deliberately no pass COUNT here: it was written as one, and
    # the peer commit that predates this note's own commit added a test, so the figure was
    # already stale inside the same fold. That is the rule this PR applied twice elsewhere
    # (`25 arms`, the `durations` row): a restated count re-stales on the next test added.
    # Both spellings matter — an exported-but-empty knob is the shape a shell wrapper produces
    # — and both must be SILENT (no spurious warning).
    monkeypatch.delenv("NEXT_WORK_PACE", raising=False)
    assert next_work.pace_seconds() == next_work.PACE_SECONDS_DEFAULT
    assert capsys.readouterr().err == ""
    monkeypatch.setenv("NEXT_WORK_PACE", "")
    assert next_work.pace_seconds() == next_work.PACE_SECONDS_DEFAULT
    assert capsys.readouterr().err == ""


def test_a_malformed_pace_knob_is_caught_in_a_FRESH_PROCESS() -> None:
    """THE property the fix exists for: the MODULE still imports — so a run still reaches the
    snapshot read and can answer "the queue was read and NO unit is pickable" — with a
    malformed knob in the ambient environment.

    Module-level `float(os.environ.get(...))` raised `ValueError` before anything was read and
    the process exited **1**, the code this tool defines as that answer; a consumer keying on
    the exit code stops the lane. Measured pre-fix:

        NEXT_WORK_PACE=0.2s uv run python tools/next_work.py
          -> ValueError: could not convert string to float: '0.2s'
          -> exit 1 with no snapshot read

    Mutation check (this is the arm that reds): re-introducing the module-level parse makes
    `import tools.next_work` raise in the child, so `returncode != 0`. A `monkeypatch.setenv`
    on the already-imported module cannot detect it.
    """
    env = {**os.environ, "NEXT_WORK_PACE": "0.2s"}
    proc = subprocess.run(
        [sys.executable, "-c", "import tools.next_work as m; print(m.pace_seconds())"],
        cwd=str(Path(__file__).resolve().parent.parent),
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert proc.returncode == 0, f"{proc.returncode}: {proc.stdout}{proc.stderr}"
    assert "Traceback" not in proc.stderr, proc.stderr
    assert proc.stdout.strip() == "0.2", proc.stdout
    assert "NEXT_WORK_PACE" in proc.stderr and "0.2s" in proc.stderr, proc.stderr


# ── a gate-refused row never reaches a rail, whatever the position (r25 #1) ─
@pytest.mark.parametrize(
    ("runs", "merge", "label"),
    [
        ("needs_fix", {"clean": True}, "structural × red check"),
        ("green", {"clean": False, "why": "conflict: a.py"}, "structural × conflict"),
        ("green", {"clean": True}, "structural alone"),
    ],
)
def test_a_gate_refused_row_is_never_handed_a_rail(runs, merge, label):
    """A structural refusal can be out-voted on POSITION by a red check or a conflict.

    `other_fails` wins at the bucket, and the merge block downgrades unconditionally,
    so the row was NEEDS-FIX and got `fix-one.sh` — while `next_action` printed
    "nothing is dispatched" beside it, and `--json` carried BOTH a command and an
    action for one unit (this file's own `command XOR action` invariant).
    """
    from tools.next_work import next_action

    s = snap(
        issues=[issue(100)],
        pulls=[{**pull(1001, closes=[100]), "head_ref": "fix/100-x",
                "base": {"ref": "release/1.x"}}],
        check_runs={"a" * 40: _runs_for_position("NEEDS-FIX") if runs == "needs_fix" else all_green()},
        attestation={"1001": {"verdict": "head"}},
        merge_tree={"1001": merge},
    )
    (row,) = units_for(s)
    assert next_command(row, REPO) is None, f"a rail was emitted for a gate refusal ({label})"
    action = next_action(row, REPO) or ""
    assert "gate refusal" in action, action


def test_command_and_action_are_mutually_exclusive_for_every_shape():
    """The invariant, over a population that includes the gate-refusal combinations."""
    from tools.next_work import next_action

    shapes = []
    for base in ("main", "release/1.x"):
        for draft in (False, True):
            for runs in ("green", "needs_fix", "needs_test"):
                for merge in ({"clean": True}, {"clean": False}, {"clean": None}):
                    for verdict in ("head", "norecord", "unavailable"):
                        shapes.append((base, draft, runs, merge, verdict))
    for base, draft, runs, merge, verdict in shapes:
        s = snap(
            issues=[issue(100)],
            pulls=[{**pull(1001, closes=[100]), "head_ref": "fix/100-x",
                    "base": {"ref": base}, "draft": draft}],
            check_runs={"a" * 40: {
                "green": all_green,
                "needs_fix": lambda: _runs_for_position("NEEDS-FIX"),
                "needs_test": lambda: _runs_for_position("NEEDS-TEST"),
            }[runs]()},
            attestation={"1001": {"verdict": verdict}},
            merge_tree={"1001": merge},
        )
        (row,) = units_for(s)
        cmd, act = next_command(row, REPO), next_action(row, REPO)
        assert (cmd is None) != (act is None), (
            f"{base=} {draft=} {runs=} {merge=} {verdict=} → cmd={cmd!r} act={act!r}"
        )


def test_a_missing_check_run_is_not_a_runner_wait():
    """A required check that never REPORTED is a harness defect, not a wait."""
    from tools.next_work import next_action

    s = snap(
        issues=[issue(100)],
        pulls=[{**pull(1001, closes=[100]), "head_ref": "fix/100-x"}],
        check_runs={"a" * 40: []},  # nothing ever reported
        attestation={"1001": {"verdict": "head"}},
        merge_tree={"1001": {"clean": True}},
    )
    (row,) = units_for(s)
    assert row.position == "NEEDS-TEST"
    action = next_action(row, REPO) or ""
    assert "NEVER REPORTED" in action, action
    assert "runner queue" not in action


def test_executable_agrees_with_next_command_across_the_shape_space():
    """`executable` and `next_command` must classify identically for a real repo.

    (With an EMPTY slug `next_command` also returns None — no addressable command —
    while `executable` takes no repo; that case is unreachable from the CLI because
    a missing repo is INCOMPLETE, so the predicate is compared over real slugs.)

    The gate-refusal guard went into the emitter and not into this predicate, so
    `executable=True` sat beside `next_command is None` and `render` chose the
    RUNNABLE slot by `executable` — printing a literal `None` where the command
    belongs, under "the first unit a lane can act on NOW".
    """
    for base in ("main", "release/1.x"):
        for draft in (False, True):
            for runs in ("green", "needs_fix", "needs_test"):
                for merge in ({"clean": True}, {"clean": False}, {"clean": None}, {}):
                    for verdict in ("head", "norecord", "unavailable"):
                        s = snap(
                            issues=[issue(100)],
                            pulls=[{**pull(1001, closes=[100]), "head_ref": "fix/100-x",
                                    "base": {"ref": base}, "draft": draft}],
                            check_runs={"a" * 40: {
                                "green": all_green,
                                "needs_fix": lambda: _runs_for_position("NEEDS-FIX"),
                                "needs_test": lambda: _runs_for_position("NEEDS-TEST"),
                            }[runs]()},
                            attestation={"1001": {"verdict": verdict}},
                            merge_tree={"1001": merge},
                        )
                        (row,) = units_for(s)
                        assert row.executable == (next_command(row, REPO) is not None), (
                            f"{base=} {draft=} {runs=} {merge=} {verdict=}"
                        )


def test_a_gate_refused_second_row_does_not_take_the_runnable_slot():
    """Two rows, both gate-refused: the RUNNABLE slot must not print a `None`."""
    s = snap(
        issues=[issue(100), issue(200)],
        pulls=[
            {**pull(1001, closes=[100]), "head_ref": "a", "base": {"ref": "release/1.x"}},
            {**pull(2002, closes=[200], head="b" * 40), "head_ref": "b",
             "base": {"ref": "release/1.x"}},
        ],
        check_runs={"a" * 40: all_green(), "b" * 40: _runs_for_position("NEEDS-FIX")},
        attestation={"1001": {"verdict": "head"}, "2002": {"verdict": "head"}},
        merge_tree={"1001": {"clean": True}, "2002": {"clean": True}},
    )
    text, _ = render(build_units(s, CFG), CFG, s, repo=REPO, top=5)
    assert "RUNNABLE  #200" not in text, text
    assert "RUNNABLE  none" in text or "RUNNABLE" not in text


def test_every_place_that_lists_the_needs_test_causes_lists_all_three():
    """Every place that enumerates the NEEDS-TEST causes must name all three.

    Each cause was added to one place at a time and three of the four ended up one
    short. What this pins: the SOURCE as a whole, the `--help` output, and the
    `executable` docstring each name all three. It is a floor, not a per-region
    check — a cause removed from ONE docstring still passes if a literal survives
    elsewhere, so the prose is also reviewed by reading it.
    """
    root = Path(__file__).resolve().parent.parent
    src = (root / "tools" / "next_work.py").read_text(encoding="utf-8")
    help_text = subprocess.run(
        [sys.executable, str(root / "tools" / "next_work.py"), "--help"],
        capture_output=True, text=True, timeout=60,
    ).stdout
    for label, text in (("source", src), ("--help", help_text)):
        low = text.lower()
        assert "runner" in low, label
        assert "unmeasured merge tree" in low, label
        assert "never reported" in low, label
    from tools.next_work import Unit

    doc = (Unit.executable.__doc__ or "").lower()
    for phrase in ("runner", "measured", "reported"):
        assert phrase in doc, (phrase, doc)


def test_an_absent_local_holder_map_is_incomplete(tmp_path: Path):
    """Absent is not empty — for `local_holders` too.

    `claims` already refused an absent map; `local_holders` fell back to `{}`, which
    `_claim_of` reads as "nothing local holds it" — measured: a snapshot that simply
    OMITS the field, with a local branch holding the unit, exited 0 WITH a merge
    command. The local half has no registry entry, so no rail catches it later.
    """
    raw = _rich_snapshot().to_json()
    raw.pop("local_holders")
    path = tmp_path / "no-holders.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    assert _run_main(["--from", str(path), "--repo", REPO]) == 2


def test_a_bad_pace_env_var_does_not_decide_an_exit_code():
    """`float(os.environ[...])` at import raised before `main`'s frame: exit 1.

    Exit 1 is documented as "the queue was read and NO unit is pickable" — a typo'd
    env var must not read as an exhausted queue.
    """
    root = Path(__file__).resolve().parent.parent
    env = dict(os.environ, NEXT_WORK_PACE="abc")
    proc = subprocess.run(
        [sys.executable, str(root / "tools" / "next_work.py"), "--help"],
        capture_output=True, text=True, timeout=60, env=env,
    )
    assert proc.returncode == 0, (proc.returncode, proc.stderr[-300:])
    assert "ignoring NEXT_WORK_PACE" in proc.stderr


@pytest.mark.parametrize("bad", ["abc", "inf", "-inf", "1e400", "-3", "nan"])
def test_an_unusable_pace_value_never_decides_an_exit_code(bad):
    """`inf` parsed fine and `time.sleep(inf)` raised OverflowError (an ArithmeticError,
    which `main`'s frame does not catch) → exit 1, the "queue exhausted" code."""
    root = Path(__file__).resolve().parent.parent
    env = dict(os.environ, NEXT_WORK_PACE=bad)
    proc = subprocess.run(
        [sys.executable, str(root / "tools" / "next_work.py"), "--help"],
        capture_output=True, text=True, timeout=60, env=env,
    )
    assert proc.returncode == 0, (bad, proc.returncode, proc.stderr[-300:])


def test_a_huge_finite_pace_value_is_capped():
    root = Path(__file__).resolve().parent.parent
    env = dict(os.environ, NEXT_WORK_PACE="1e9")
    proc = subprocess.run(
        [sys.executable, "-c",
         f"import sys; sys.path.insert(0, {str(root)!r}); "
         "from tools.next_work import PACE_SECONDS; print(PACE_SECONDS)"],
        capture_output=True, text=True, timeout=60, env=env,
    )
    assert float(proc.stdout.strip()) <= 5.0, proc.stdout
