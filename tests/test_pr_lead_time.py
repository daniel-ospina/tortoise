"""Guard suite for tools/pr_lead_time.py (#6139).

The tool shipped with NO tests, which is why the defects these pin went
unnoticed — every one of them was found by a reviewer reproducing it against
live data, not by a test. Each test below names the defect it prevents from
returning; the P1s were all reproduced on real PRs before being fixed:

* queue entry taken from a ZERO-LENGTH `Mergify Merge Queue` run — a queue
  EVALUATION probe, not residence. Measured: PR #6106 read review_wait 0.0 /
  queue_cycle 74.1 min against 63.0 / 11.2 min from the residence run (6.6x),
  and #5742 / #6079 have no residence run at all yet were assigned 47.7 and
  89.3 min of residence.
* a gate EARLIER than created_at, making seconds_a negative and aborting the
  WHOLE run through the partition invariant (PR #5137, a = -10762 s).
* an UNOBSERVED read exiting 0 — a page cap only warned on stderr, and a failed
  `gh` call raised an uncaught RuntimeError and exited 1 (the code reserved for
  "a gate would fail").
* a naive `--now` compared against an aware GitHub timestamp -> TypeError.
* a bot APPROVED review counted as human readiness, while `first_activity()`
  excludes bots.
* `--prune-after` zeroing the queue population so the report printed
  `queue_prs_closed_in_window=0` as though the queue were genuinely empty.

Everything here is pure: no network, no database, no subprocess. The `Gh` client
is not exercised.
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import pr_lead_time as plt  # noqa: E402

T0 = datetime(2026, 9, 28, 12, 0, 0, tzinfo=timezone.utc)  # noqa: UP017 — bare `python3` is 3.9
UTC = timezone.utc  # noqa: UP017 — same reason; one marker instead of four


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def _mergify(start: datetime, end: datetime) -> dict:
    return {"name": "Mergify Merge Queue", "started_at": _iso(start),
            "completed_at": _iso(end)}


# --- queue entry: residence, not evaluation ---------------------------------

def test_zero_length_mergify_probe_is_not_queue_entry():
    """The P1. A zero-length run must not be read as the queue entry instant."""
    probe = _mergify(T0, T0)                       # zero-length evaluation
    residence = _mergify(T0 + timedelta(minutes=66), T0 + timedelta(minutes=77))
    entry = plt.queue_entry([probe, residence])
    assert entry == T0 + timedelta(minutes=66), (
        "the zero-length evaluation probe was taken as queue entry — this is the "
        "defect that overstated PR #6106's queue residence by 6.6x")
    assert entry != T0


def test_head_with_only_zero_length_probes_has_no_queue_marker():
    """A head whose only Mergify runs are probes must read as NO marker, so
    `merged_without_queue_marker` can fire instead of fabricating residence."""
    assert plt.queue_entry([_mergify(T0, T0), _mergify(T0, T0)]) is None


def test_queue_entry_picks_the_earliest_residence_run():
    a = _mergify(T0 + timedelta(hours=2), T0 + timedelta(hours=3))
    b = _mergify(T0 + timedelta(hours=1), T0 + timedelta(hours=4))
    assert plt.queue_entry([a, b]) == T0 + timedelta(hours=1)


def test_queue_entry_ignores_non_mergify_runs():
    other = {"name": "python-ci-gate", "started_at": _iso(T0),
             "completed_at": _iso(T0 + timedelta(hours=5))}
    assert plt.queue_entry([other]) is None


def test_queue_entry_is_none_without_mergify_runs():
    assert plt.queue_entry([]) is None


def test_queue_entry_ignores_an_unparseable_span():
    """A run missing one endpoint cannot establish residence."""
    assert plt.queue_entry([{"name": "Mergify Merge Queue", "started_at": _iso(T0)}]) is None


# --- eligibility: the live-path boundary (readiness vs residence) -------------
#
# `eligible_at` exists because the queue split cannot express the split on a path
# with NO queue: with no queue entry the whole of (b) reads as "pre-entry" and
# residence is invisible. Measured 2026-10-04 on the live path: eligible -> merged
# medians 0.8 min against created -> eligible medians 2.02 h, so (b) is
# overwhelmingly READINESS. These pin the fail-open direction (an unrecognised
# conclusion must never read as an eligible head) and the never-a-zero rule.

def _check(name: str, conclusion: str | None, start: datetime | None = None,
           end: datetime | None = None, cid: int = 1,
           slug: str = "github-actions") -> dict:
    return {"name": name, "conclusion": conclusion,
            "started_at": _iso(start or T0),
            "completed_at": _iso(end) if end else None,
            "id": cid, "app": {"slug": slug}}


def test_eligibility_is_none_when_a_newest_attempt_is_red():
    """The #6807 polarity: a red — or an UNRECOGNISED conclusion — must never be
    read as an eligible head (the 'unknown input read as GREEN' fail-open)."""
    runs = [_check("test (a)", "success", end=T0 + timedelta(minutes=29)),
            _check("test (b)", "failure", end=T0 + timedelta(minutes=31))]
    assert plt.eligible_at(runs) is None
    assert plt.eligible_at([_check("test (a)", "something-new")]) is None
    assert plt.eligible_at([_check("test (a)", None)]) is None


def test_eligibility_is_none_when_no_check_surface_was_observed():
    """Never a zero: no check surface is UNOBSERVABLE, not instantaneously ready."""
    assert plt.eligible_at([]) is None
    assert plt.eligible_at([_mergify(T0, T0 + timedelta(minutes=5))]) is None


def test_eligibility_is_the_latest_completion_of_the_newest_attempts():
    runs = [_check("test (a)", "success", end=T0 + timedelta(minutes=29)),
            _check("test (b)", "success", end=T0 + timedelta(minutes=31)),
            _check("test (c)", "skipped", end=T0 + timedelta(minutes=20))]
    assert plt.eligible_at(runs) == T0 + timedelta(minutes=31)


def test_eligibility_takes_the_newest_attempt_not_the_first():
    """A re-run that fixed a red makes the head eligible, and the older SUCCESS
    must not win the clock either — the newest attempt per key decides both."""
    red = _check("test (a)", "failure", end=T0 + timedelta(minutes=10), cid=1)
    green = _check("test (a)", "success", end=T0 + timedelta(minutes=40), cid=2)
    assert plt.eligible_at([red, green]) == T0 + timedelta(minutes=40)
    assert plt.eligible_at([green, red]) == T0 + timedelta(minutes=40)


def test_eligibility_treats_cancelled_and_stale_as_non_red():
    """The rail's allow-list, and it matters: a cancelled shard is common (an
    environmental kill), so the opposite reading would make most heads
    permanently ineligible and the split would report nothing."""
    runs = [_check("test (d)", "cancelled", end=T0 + timedelta(minutes=5)),
            _check("test (e)", "stale", end=T0 + timedelta(minutes=6))]
    assert plt.eligible_at(runs) == T0 + timedelta(minutes=6)


def test_eligibility_is_none_without_a_completion_clock():
    """A conclusion without a completion time cannot date the boundary."""
    runs = [{"name": "test (a)", "conclusion": "success", "started_at": _iso(T0),
             "completed_at": None, "id": 1, "app": {"slug": "github-actions"}}]
    assert plt.eligible_at(runs) is None


def test_split_b_at_eligibility_sums_to_the_leg():
    gate = T0
    merged = T0 + timedelta(hours=4)
    s = plt.split_b_at_eligibility(gate, merged, T0 + timedelta(hours=3, minutes=59))
    assert s["pre_eligible_seconds"] + s["post_eligible_seconds"] == 4 * 3600
    assert s["post_eligible_seconds"] == 60.0


def test_split_b_charges_nothing_when_eligibility_is_unobservable():
    """Both halves None — never 0.0, which would read as 'no residence'."""
    s = plt.split_b_at_eligibility(T0, T0 + timedelta(hours=2), None)
    assert s == {"eligible_at": None, "pre_eligible_seconds": None,
                 "post_eligible_seconds": None}


def test_split_b_clamps_eligibility_into_the_leg():
    """A check can complete before the cheap entry gate finishes, and a clock can
    land a second after the merge: eligibility is clamped, so neither half can be
    negative (the class that aborted a whole run on PR #5137, a = -10762 s)."""
    gate, merged = T0 + timedelta(hours=1), T0 + timedelta(hours=2)
    early = plt.split_b_at_eligibility(gate, merged, T0)
    assert early["pre_eligible_seconds"] == 0.0
    late = plt.split_b_at_eligibility(gate, merged, T0 + timedelta(hours=5))
    assert late["post_eligible_seconds"] == 0.0


# --- gate clamp into the PR's own life --------------------------------------

def test_gate_before_creation_is_clamped_to_created():
    """The P1 reproduced as PR #5137: gate ~18:49Z, created 21:48:44Z."""
    created = T0
    merged = T0 + timedelta(hours=1)
    gate = T0 - timedelta(hours=3)                 # already green at PR creation
    assert plt.clamp_gate(gate, created, merged) == created


def test_clamped_gate_keeps_segments_non_negative():
    """The consequence the whole-run abort came from."""
    created, merged = T0, T0 + timedelta(hours=2)
    gate = plt.clamp_gate(T0 - timedelta(hours=3), created, merged)
    assert (gate - created).total_seconds() == 0
    assert (merged - gate).total_seconds() >= 0


def test_gate_after_merge_is_clamped_to_merged():
    created, merged = T0, T0 + timedelta(hours=1)
    assert plt.clamp_gate(T0 + timedelta(hours=5), created, merged) == merged


def test_gate_inside_the_life_is_unchanged():
    created, merged, gate = T0, T0 + timedelta(hours=2), T0 + timedelta(hours=1)
    assert plt.clamp_gate(gate, created, merged) == gate


def test_none_gate_passes_through():
    assert plt.clamp_gate(None, T0, T0 + timedelta(hours=1)) is None


# --- bot approvals are not human readiness ----------------------------------

def test_bot_approval_is_not_human_readiness():
    """`first_approval_at` feeds ready = max(gate, approval), so a bot's
    APPROVED review counted as human readiness until it was filtered;
    `first_activity()` already excluded bots, so the two disagreed."""
    bots = [{"state": "APPROVED", "submitted_at": _iso(T0),
             "user": {"login": "mergify[bot]", "type": "Bot"}}]
    assert plt.review_stats(bots)["first_approval_at"] is None


def test_human_approval_is_still_readiness():
    """The discriminating half: if the filter were widened to drop ALL
    approvals, the test above would still pass."""
    human = [{"state": "APPROVED", "submitted_at": _iso(T0),
              "user": {"login": "daniel-ospina", "type": "User"}}]
    assert plt.review_stats(human)["first_approval_at"] is not None


def test_bot_detection_is_what_the_filter_rests_on():
    """Second discrimination: neutering is_bot() must not leave the suite green."""
    assert plt.is_bot({"login": "mergify[bot]", "type": "Bot"}) is True
    assert plt.is_bot({"login": "daniel-ospina", "type": "User"}) is False


# --- timestamps -------------------------------------------------------------

def test_naive_timestamp_is_read_as_utc():
    """A bare `--now` used to raise TypeError against an aware GitHub value."""
    parsed = plt.ts("2026-01-01T00:00:00")
    assert parsed is not None and parsed.tzinfo is not None
    assert parsed == datetime(2026, 1, 1, tzinfo=UTC)
    # the comparison that used to explode
    assert parsed < datetime.now(UTC)


def test_zulu_timestamp_is_aware_and_equal():
    assert plt.ts("2026-01-01T00:00:00Z") == datetime(2026, 1, 1, tzinfo=UTC)


def test_missing_timestamp_is_none():
    assert plt.ts(None) is None
    assert plt.ts("") is None


# --- exit-code discipline ---------------------------------------------------

def _run_main(monkeypatch, argv_extra=None, result=None, raises=None,
              truncates=None):
    def fake_measure(*_a, **_k):
        # The real caps record INTO _TRUNCATIONS while measure() sweeps, and
        # main() clears it on entry — so a seed cannot be set from outside and
        # must be produced from inside, exactly as Gh.paged does.
        if truncates:
            plt._TRUNCATIONS.extend(truncates)
        if raises is not None:
            raise raises
        return result
    monkeypatch.setattr(plt, "measure", fake_measure)
    argv = ["--repo-root", str(ROOT), "--days", "0"] + (argv_extra or [])
    try:
        return plt.main(argv)
    finally:
        plt._TRUNCATIONS.clear()


def test_main_exits_unknown_on_empty_population(monkeypatch, capsys):
    """An empty window used to return 0 with every `verified` flag true."""
    rc = _run_main(monkeypatch, result={"prs": []})
    assert rc == 2, "an EMPTY population must be UNKNOWN, never 0"


def test_main_exits_unknown_when_a_read_was_truncated(monkeypatch, capsys):
    """A page cap only wrote a stderr warning and still exited 0."""
    rc = _run_main(monkeypatch, result={"prs": [{"number": 1}]},
                   truncates=["closed-PR scan"])
    assert rc == 2, "a truncated population must be UNKNOWN, never 0"


def test_main_exits_unknown_on_a_failed_read(monkeypatch, capsys):
    """A failed `gh` call raised an uncaught RuntimeError -> exit 1."""
    rc = _run_main(monkeypatch, raises=RuntimeError("gh api failed: rate limit"))
    assert rc == 2, "an unobserved read must be UNKNOWN (2), not the verdict code 1"


def test_main_exits_unknown_when_gh_is_missing(monkeypatch, capsys):
    """REPRODUCED by review: with `gh` off PATH, `FileNotFoundError` escaped the
    narrower `except (RuntimeError, subprocess.CalledProcessError)` and the tool
    exited 1 — the verdict code — for a read it never made."""
    rc = _run_main(monkeypatch, raises=FileNotFoundError("gh"))
    assert rc == 2, "a missing `gh` binary is an unobserved read, not a verdict"


def test_main_exits_unknown_on_a_corrupt_read(monkeypatch, capsys):
    """A corrupt cache entry or a non-JSON body on exit 0 raises
    json.JSONDecodeError out of json.loads — same escape, same wrong exit 1."""
    import json as _json
    rc = _run_main(monkeypatch, raises=_json.JSONDecodeError("boom", "doc", 0))
    assert rc == 2


def test_unobserved_run_leaves_no_json_artifact(monkeypatch, tmp_path, capsys):
    """--json-out used to be written BEFORE the guards, so an UNKNOWN run left a
    file whose contents read as a measurement to any consumer that reads the file
    rather than the exit code."""
    out = tmp_path / "lead.json"
    rc = _run_main(monkeypatch, argv_extra=["--json-out", str(out)],
                   result={"prs": []})
    assert rc == 2 and not out.exists()


def test_partition_violation_is_not_reported_as_unknown(monkeypatch):
    """`1` stays reserved for a real verdict: an invariant failure must NOT be
    swallowed into the UNKNOWN path. This asserts the MESSAGE, not merely that
    *some* SystemExit was raised — the weaker form was satisfied by any
    unrelated SystemExit, so it stayed green even when the partition assertion
    itself had been deleted (found by review)."""
    with pytest.raises(SystemExit) as ei:
        _run_main(monkeypatch,
                  raises=SystemExit("PARTITION VIOLATED: legs != elapsed"))
    assert "PARTITION VIOLATED" in str(ei.value)


# --- the fabricated zero ----------------------------------------------------

def test_prune_after_does_not_zero_the_queue_population():
    """`--prune-after` cleared `queue_prs`, so the report printed
    `queue_prs_closed_in_window=0` as if the queue were genuinely empty — the
    'single largest accounting error in earlier readings' this tool exists to
    prevent.

    LIMIT, stated rather than implied (found by review): this is a TEXT pin and
    covers only that literal expression. A semantically equivalent reintroduction
    — `queue_prs.clear()`, `queue_prs = []`, `del queue_prs[:]` — passes it. It
    strips `#` comments but not string literals, so quoting the expression in the
    module DOCSTRING still trips it. A behavioural pin would drive `measure()`
    with a fake `Gh`; that is NOT done here, so this guard is weaker than its
    name suggests."""
    src = (ROOT / "tools" / "pr_lead_time.py").read_text()
    # strip comments: the source legitimately MENTIONS the removed expression in
    # the comment that explains why it is gone — the pin is on executable code.
    code = "\n".join(line.split("#", 1)[0] for line in src.splitlines())
    assert "queue_prs[:0]" not in code, (
        "--prune-after must not empty the queue population; the report would "
        "read a fabricated 0 as a measured one")


# --- measure(): the CALL SITES, which no helper test can reach ----------------
# A fresh review round found that this suite asserted only helpers: deleting
# `gate = clamp_gate(gate, created, merged_at)` from measure(), or replacing the
# `first_approval_at` assignment with None, left every test green. A claim about
# what the PROGRAM reports cannot be falsified by testing the function the claim
# was drafted from. These three tests drive measure() itself.

GATE_CTX = "fake-gate"
SHA = "a" * 40


def _pr(number=1, created=T0, merged=None, closed=None):
    end = closed or merged
    return {"number": number, "title": "t", "created_at": _iso(created),
            "updated_at": _iso(end or created),
            "closed_at": _iso(end) if end else None,
            "merged_at": _iso(merged) if merged else None,
            "draft": False, "user": {"login": "daniel-ospina", "type": "User"},
            "head": {"sha": SHA}}


def _gate_run(at):
    return {"id": 1, "name": GATE_CTX, "app": {"slug": "github-actions"},
            "status": "completed", "conclusion": "success",
            "started_at": _iso(at), "completed_at": _iso(at)}


class FakeGh:
    """Answers measure()'s reads from canned payloads. Records every URL it was
    asked for, so a shape mismatch is diagnosable instead of mysterious."""

    def __init__(self, runs, commits, reviews=None):
        self.runs, self.commits = runs, commits
        self.reviews = reviews or []
        self.calls = self.cache_hits = 0
        self.asked: list[str] = []

    def _route(self, url):
        self.calls += 1
        self.asked.append(url)
        if "state=closed" in url:
            return [_pr(merged=T0 + timedelta(hours=1))] if url.endswith("page=1") else []
        if "check-runs" in url:
            return self.runs
        if "/commits?" in url:
            return self.commits
        if "/reviews?" in url:
            return self.reviews
        return []

    def page(self, url, **kw):
        # The real Gh.page returns the raw body, so a check-runs read through it
        # yields the {"check_runs": [...]} OBJECT, not a list. Unreachable today
        # (check_runs() only reaches obj_paged) — but a fake that answers the
        # wrong SHAPE would mask a future switch to .page, so it matches the real
        # contract rather than the current call path. Found by review.
        if "check-runs" in url:
            self.calls += 1
            self.asked.append(url)
            return {"total_count": len(self.runs), "check_runs": self.runs}
        return self._route(url)

    def paged(self, url, **kw):
        return self._route(url)

    def obj_paged(self, url, **kw):
        # a check-runs read returns an OBJECT keyed by `check_runs` over REST
        if "check-runs" in url:
            self.calls += 1
            self.asked.append(url)
            return {"total_count": len(self.runs), "check_runs": self.runs}
        return self._route(url)


def _measure(gh):
    return plt.measure(gh, "o/r", [GATE_CTX], 7, T0 + timedelta(days=1))


def _gate_before_creation_gh(**kw):
    """PR #5137's shape: the gate was already green 3h BEFORE the PR existed."""
    return FakeGh([_gate_run(T0 - timedelta(hours=3))],
                  [{"sha": SHA, "commit": {"author": {"date": _iso(T0)}}}], **kw)


def test_measure_clamps_a_pre_creation_gate_at_the_call_site():
    """REPRODUCED by the review as the suite's worst gap: removing the clamp call
    from measure() left all 19 tests green, because only clamp_gate() was
    asserted. This drives measure() and requires seconds_a == 0.0, not negative."""
    rows = _measure(_gate_before_creation_gh())["prs"]
    assert len(rows) == 1
    assert rows[0]["seconds_a"] == 0.0, (
        "a gate earlier than created_at must be clamped at the CALL SITE in "
        "measure(); otherwise seconds_a is negative and the run aborts")
    # rows carry datetime.isoformat() (+00:00), while _iso() writes the GitHub
    # spelling (Z) — the same instant, different spelling.
    assert rows[0]["gate_success_at"] == T0.isoformat()


def test_measure_itself_raises_on_a_negative_segment(monkeypatch):
    """The other direction: measure()'s OWN guard must exist. `main()` passing on
    a raise it was handed is not evidence that measure() raises — the earlier
    test was satisfied by any SystemExit, including one from nowhere."""
    gh = _gate_before_creation_gh()
    monkeypatch.setattr(plt, "clamp_gate", lambda g, c, m: g)  # reintroduce the defect
    with pytest.raises(SystemExit) as ei:
        _measure(gh)
    assert "NEGATIVE SEGMENT" in str(ei.value)


def test_measure_uses_the_filtered_approval_at_the_call_site():
    """The bot filter is only useful if its RESULT is what the row carries. Two
    approvals, the bot's earlier: the row must report the human's. Fails both if
    the filter is dropped (bot's earlier time wins) and if the assignment is
    replaced by None (found as a second caller gap by the VGATE verifier)."""
    human_at = T0 + timedelta(minutes=10)
    reviews = [
        {"state": "APPROVED", "submitted_at": _iso(T0 - timedelta(minutes=10)),
         "user": {"login": "mergify[bot]", "type": "Bot"}},
        {"state": "APPROVED", "submitted_at": _iso(human_at),
         "user": {"login": "daniel-ospina", "type": "User"}},
    ]
    rows = _measure(_gate_before_creation_gh(reviews=reviews))["prs"]
    assert rows[0]["first_approval_at"] == human_at.isoformat(), (
        "the row must carry review_stats()'s FILTERED approval, not the raw "
        "earliest — which is the bot's")


# --- the _TRUNCATIONS PRODUCERS ---------------------------------------------
# Every other guard covers main()'s CONSUMER of the module list. If the producer
# stopped recording, main() would stay green and a truncated sweep would read as
# a complete one — the failure the list exists to make impossible. Gh.page shells
# out to `gh api` via subprocess.run, so the producer is reachable with one patch.


def _fake_gh_page(monkeypatch, body, argv_seen=None):
    def fake_run(argv, **_kw):
        if argv_seen is not None:
            argv_seen.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(body), stderr="")
    monkeypatch.setattr(plt.subprocess, "run", fake_run)
    plt._TRUNCATIONS.clear()
    return plt.Gh(cache_dir=None, min_interval=0)


def test_paged_records_a_truncation_when_it_hits_the_page_cap(monkeypatch, capsys):
    """A FULL page every time, so the loop never sees a short page and runs out of
    pages instead. That is precisely a truncated population, and it must land in
    _TRUNCATIONS — a stderr warning alone leaves main()'s exit code at 0."""
    full = [{"id": i} for i in range(100)]
    gh = _fake_gh_page(monkeypatch, full)
    try:
        out = gh.paged("repos/o/r/issues", per_page=100, max_pages=3)
        assert len(out) == 300
        assert plt._TRUNCATIONS, (
            "a page-cap hit MUST be recorded in _TRUNCATIONS, not only warned "
            "about on stderr")
        assert "repos/o/r/issues" in plt._TRUNCATIONS[0]
    finally:
        plt._TRUNCATIONS.clear()


def test_obj_paged_records_a_truncation_when_it_hits_the_page_cap(monkeypatch, capsys):
    """The same producer on the object endpoint — the one that carries check-runs,
    where a truncated read silently shortens a CI span or a gate lookup."""
    body = {"total_count": 999, "check_runs": [{"id": i} for i in range(100)]}
    gh = _fake_gh_page(monkeypatch, body)
    try:
        gh.obj_paged("repos/o/r/commits/x/check-runs", "check_runs",
                     per_page=100, max_pages=2)
        assert plt._TRUNCATIONS, (
            "an obj_paged cap hit MUST be recorded, keyed by the merge key")
        assert "check_runs" in plt._TRUNCATIONS[0]
    finally:
        plt._TRUNCATIONS.clear()


def test_a_complete_sweep_records_no_truncation(monkeypatch, capsys):
    """The discriminating half: a SHORT page ends the loop cleanly. If the producer
    appended unconditionally, this fails — without it the two tests above could not
    tell 'recorded a real cap hit' from 'always records'."""
    gh = _fake_gh_page(monkeypatch, [{"id": 1}])
    try:
        out = gh.paged("repos/o/r/issues", per_page=100, max_pages=3)
        assert out == [{"id": 1}]
        assert not plt._TRUNCATIONS, "a complete sweep must record NOTHING"
    finally:
        plt._TRUNCATIONS.clear()


# --- leg (a)'s internal clock (#6238) ---------------------------------------
# (a) was the drain's DOMINANT segment and carried no internal boundary at all,
# so "authors take a long time to push" and "CI takes a long time to go green"
# were the same reading. These pin the two boundaries that separate them.

def test_split_a_names_the_three_segments():
    created = T0
    gate = T0 + timedelta(hours=5)
    s = plt.split_a_at_commits(created, gate, T0 + timedelta(hours=1),
                               T0 + timedelta(hours=2))
    assert s["authoring_seconds"] == 3600.0
    assert s["dispatch_seconds"] == 3600.0
    assert s["gate_ci_seconds"] == 3 * 3600.0
    assert s["authoring_seconds"] + s["dispatch_seconds"] + s["gate_ci_seconds"] == 5 * 3600


def test_split_a_is_all_none_when_either_boundary_is_missing():
    """Both-or-neither, as in the (b) eligibility split: an unobservable clock
    must never read as 0.0 authoring or 0.0 CI, because either zero is a claim
    ("instant") about a duration nobody measured."""
    gate = T0 + timedelta(hours=2)
    for s in (plt.split_a_at_commits(T0, gate, None, T0 + timedelta(minutes=10)),
              plt.split_a_at_commits(T0, gate, T0 + timedelta(minutes=5), None)):
        assert s == {"first_commit_at": None, "first_gate_ci_start_at": None,
                     "authoring_seconds": None, "dispatch_seconds": None,
                     "gate_ci_seconds": None}


def test_split_a_clamps_a_commit_that_precedes_the_pr():
    """The COMMON shape: a branch is pushed and then the PR is opened, so the
    first commit is older than created_at. Unclamped this is the negative-segment
    class that aborted a whole run on PR #5137 (a = -10762 s)."""
    s = plt.split_a_at_commits(T0, T0 + timedelta(hours=2), T0 - timedelta(hours=3),
                               T0 + timedelta(minutes=30))
    assert s["authoring_seconds"] == 0.0
    assert s["dispatch_seconds"] == 1800.0
    assert s["gate_ci_seconds"] == 5400.0
    # the TIMESTAMP must be clamped too, not just the duration: a boundary printed
    # outside [created, gate] contradicts the emitted rule and disagrees with the
    # segment beside it (found by review on the first cut of this function)
    assert s["first_commit_at"] == T0.isoformat()


def test_split_a_forces_monotone_boundaries():
    """A check start EARLIER than the first commit (an earlier attempt whose
    commit was rebased away, or a re-run credited to an older sha) must not make
    `dispatch_seconds` negative: the second boundary is raised to the first."""
    s = plt.split_a_at_commits(T0, T0 + timedelta(hours=1),
                               T0 + timedelta(minutes=40), T0 + timedelta(minutes=10))
    assert s["dispatch_seconds"] == 0.0
    assert s["authoring_seconds"] + s["dispatch_seconds"] + s["gate_ci_seconds"] == 3600


def test_split_a_clamps_boundaries_past_the_gate():
    """A start recorded after the gate it produced is clamped, so the CI campaign
    cannot outlive (a) — the same clamp the queue split makes at queue entry."""
    s = plt.split_a_at_commits(T0, T0 + timedelta(hours=1),
                               T0 + timedelta(hours=5), T0 + timedelta(hours=5))
    assert s["gate_ci_seconds"] == 0.0
    assert s["authoring_seconds"] + s["dispatch_seconds"] + s["gate_ci_seconds"] == 3600
    assert s["first_gate_ci_start_at"] == (T0 + timedelta(hours=1)).isoformat()


def _clock_gh(start_offset, commit_offset, started=True):
    """A merged PR whose single commit landed at `commit_offset` and whose gate
    context began at `start_offset` and completed 30 min later."""
    run = {"id": 1, "name": GATE_CTX, "app": {"slug": "github-actions"},
           "status": "completed", "conclusion": "success",
           "started_at": _iso(T0 + start_offset) if started else None,
           "completed_at": _iso(T0 + timedelta(minutes=40))}
    return FakeGh([run], [{"sha": SHA,
                           "commit": {"author": {"date": _iso(T0 + commit_offset)}}}])


def test_measure_wires_the_leg_a_clock_split_at_the_call_site():
    """The lesson from this file's worst review gap: asserting the pure function
    is not evidence that measure() CALLS it. This drives measure() and requires
    the three fields on the row, summing to seconds_a exactly."""
    r = _measure(_clock_gh(timedelta(minutes=10), timedelta(0)))["prs"][0]
    assert r["authoring_seconds"] == 0.0            # commit at created_at
    assert r["dispatch_seconds"] == 600.0           # created -> first CI start
    assert r["gate_ci_seconds"] == 1800.0           # start -> gate success
    assert (r["authoring_seconds"] + r["dispatch_seconds"]
            + r["gate_ci_seconds"]) == r["seconds_a"]


def test_measure_does_not_claim_the_split_when_the_ci_start_clock_is_missing():
    """A check-run with no `started_at` still gates (the gate rule reads
    `completed_at`), so (a) is measurable while its first boundary is not: the
    row must report None for all three rather than charge the whole of (a) to CI."""
    res = _measure(_clock_gh(timedelta(minutes=10), timedelta(0), started=False))
    r = res["prs"][0]
    assert r["seconds_a"] is not None and r["gate_ci_seconds"] is None
    assert r["authoring_seconds"] is None and r["dispatch_seconds"] is None
    assert res["leg_a_clock_split_pct"]["prs"] == 0
    assert res["totals"]["leg_a_clock_split_missing_prs"] == 1


def test_measure_itself_raises_when_the_leg_a_split_does_not_sum(monkeypatch):
    """A fresh review of the sibling (b) split found `if False and …` left every
    test green: the assertion guarding a published number has to be asserted
    itself. This makes the split off by one second and requires measure() to
    abort the run."""
    real = plt.split_a_at_commits

    def off_by_one(created, gate, first_commit, first_ci_start):
        s = real(created, gate, first_commit, first_ci_start)
        if s["authoring_seconds"] is not None:
            s["authoring_seconds"] += 1.0
        return s

    monkeypatch.setattr(plt, "split_a_at_commits", off_by_one)
    with pytest.raises(SystemExit) as ei:
        _measure(_clock_gh(timedelta(minutes=10), timedelta(0)))
    assert "(a) CLOCK SPLIT VIOLATED" in str(ei.value)


class _TwoHeadGh(FakeGh):
    """Two merged PRs on different heads with independent check-runs: one head
    whose CI start clock exists and one whose does not."""

    def __init__(self, runs_by_sha):
        super().__init__([], [])
        self.by_sha = runs_by_sha
        self._prs = [_pr(1, merged=T0 + timedelta(hours=1)),
                     _pr(2, merged=T0 + timedelta(hours=1))]
        self._prs[1]["head"] = {"sha": "b" * 40}

    def _route(self, url):
        self.calls += 1
        self.asked.append(url)
        if "state=closed" in url:
            return self._prs if url.endswith("page=1") else []
        if "/commits?" in url:
            sha = "b" * 40 if "/pulls/2/" in url else SHA
            return [{"sha": sha, "commit": {"author": {"date": _iso(T0)}}}]
        return []

    def obj_paged(self, url, **kw):
        if "check-runs" in url:
            self.calls += 1
            self.asked.append(url)
            runs = self.by_sha[url.split("/commits/")[1].split("/")[0]]
            return {"total_count": len(runs), "check_runs": runs}
        return self._route(url)


def test_leg_a_split_pct_divides_by_the_observable_rows_only():
    """Two rows with the SAME 2400 s of (a); only one has a CI start clock. The
    shares must be of that row's (a) (25%/75%), not of the 2-row total (which
    would print 12.5%/37.5% and hide the unobservable half in the denominator)."""
    started = {"id": 1, "name": GATE_CTX, "app": {"slug": "github-actions"},
               "status": "completed", "conclusion": "success",
               "started_at": _iso(T0 + timedelta(minutes=10)),
               "completed_at": _iso(T0 + timedelta(minutes=40))}
    unstarted = dict(started, id=2, started_at=None)
    res = plt.measure(_TwoHeadGh({SHA: [started], "b" * 40: [unstarted]}),
                      "o/r", [GATE_CTX], 7, T0 + timedelta(days=1))
    assert len(res["prs"]) == 2
    assert res["leg_a_clock_split_pct"]["prs"] == 1
    assert res["totals"]["leg_a_clock_split_missing_prs"] == 1
    assert res["leg_a_clock_split_pct"]["dispatch"] == 25.0
    assert res["leg_a_clock_split_pct"]["gate_ci"] == 75.0
