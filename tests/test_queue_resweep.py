"""Hermetic tests for tools/queue_resweep.py.

No network, no `gh`, no Mergify. Every GitHub surface is a `FakeGithub` whose
check-run responses are a SCRIPT, so the tests can reproduce the two real
outcomes the tool exists to tell apart (measured 2026-09-26):

  * a `Mergify Merge Queue` check-run that goes `in_progress`  → ENTERED
  * the same check-run `completed/neutral`                     → WAITING, not queued
  * no new check-run at all                                    → UNKNOWN (exit 1)

The tests are the contract: dry run posts nothing, a PR already in the queue is
never touched, a live command for the current head is not re-posted, and a
`completed/neutral` answer is never reported as an entry.

Run standalone:    python3 tests/test_queue_resweep.py
Run under pytest:  python3 -m pytest tests/test_queue_resweep.py -q
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import queue_resweep as qr  # noqa: E402

FIXTURE_CONFIG = """
queue_rules:
  - name: main
    queue_conditions:
      - base=main
      - -draft
      - check-success=python-ci-gate
      - check-success=pricing-artifact
      - check-success=docs
      - check-success=test-isolation
      - check-success=license-surface
      - check-success=legal-e2e
    merge_conditions:
      - check-success=python-ci-gate
    merge_method: squash
    batch_size: 2
merge_protections_settings:
  auto_merge_conditions: true
"""

CHECKS = (
    "python-ci-gate",
    "pricing-artifact",
    "docs",
    "test-isolation",
    "license-surface",
    "legal-e2e",
)


# ── fixtures ─────────────────────────────────────────────────────────────────


def check(name, status="completed", conclusion="success", cid=1, app="github-actions", **extra):
    run = {
        "id": cid,
        "name": name,
        "status": status,
        "conclusion": conclusion,
        "app": {"slug": app},
    }
    run.update(extra)
    return run


def mergify_check(status, conclusion=None, cid=900):
    return check(qr.QUEUE_CHECK_NAME, status, conclusion, cid=cid, app=qr.MERGIFY_APP_SLUG)


def all_green(cid_base=1):
    return [check(n, cid=cid_base + i) for i, n in enumerate(CHECKS)]


def pr(number=1, title="t", sha="sha1", draft=False, base="main", labels=()):
    return {
        "number": number,
        "title": title,
        "draft": draft,
        "head": {"sha": sha},
        "base": {"ref": base},
        "labels": [{"name": n} for n in labels],
    }


class Clock:
    """A fake monotonic clock that advances on every read, so poll loops terminate."""

    def __init__(self, step=10.0):
        self.t = 0.0
        self.step = step

    def __call__(self):
        self.t += self.step
        return self.t


class FakeGithub:
    """Scripted GitHub. `checks_by_sha[sha]` is a LIST OF RESPONSES; the last repeats.

    That models "read the check-runs, post, read them again" — the poll that
    distinguishes an entry from a send.
    """

    def __init__(
        self,
        pulls,
        checks_by_sha,
        comments=None,
        head_dates=None,
        post_error=None,
        base_sha="mainsha",
        base_checks=None,
    ):
        self.pulls = pulls
        self.checks_by_sha = {k: list(v) for k, v in checks_by_sha.items()}
        # The base branch defaults to GREEN, so every test that is not about the
        # base gate exercises the ordinary path.
        self.base_sha = base_sha
        self.checks_by_sha.setdefault(base_sha, [base_checks or all_green(cid_base=500)])
        self.comments = comments or {}
        self.head_dates = head_dates or {}
        self.post_error = post_error or {}
        self.calls: list[str] = []
        self.posted: list[tuple[int, str]] = []

    def list_pulls(self, repo, base):
        self.calls.append(f"pulls:{repo}:{base}")
        return list(self.pulls)

    def branch_head_sha(self, repo, branch):
        self.calls.append(f"branch:{repo}:{branch}")
        return self.base_sha

    def list_check_runs(self, repo, sha):
        self.calls.append(f"checks:{sha}")
        seq = self.checks_by_sha[sha]
        return seq[0] if len(seq) == 1 else seq.pop(0)

    def list_comments(self, repo, number):
        self.calls.append(f"comments:{number}")
        return list(self.comments.get(number, []))

    def head_committed_at(self, repo, sha):
        self.calls.append(f"headdate:{sha}")
        return self.head_dates.get(sha, "2026-09-26T00:00:00Z")

    def post_comment(self, repo, number, body):
        if number in self.post_error:
            raise qr.GhError(self.post_error[number])
        self.posted.append((number, body))
        return {"id": 1, "url": "u"}


def cfg():
    return qr.parse_config(FIXTURE_CONFIG)


def run(client, **kw):
    kw.setdefault("dry_run", True)
    kw.setdefault("sleep", lambda _s: None)
    kw.setdefault("now", Clock())
    return qr.run_sweep(client, "o/r", cfg(), **kw)


# ── config ───────────────────────────────────────────────────────────────────


class TestConfig(unittest.TestCase):
    def test_reads_the_gate(self):
        c = cfg()
        self.assertEqual(c.queue_name, "main")
        self.assertEqual(c.base, "main")
        self.assertEqual(set(c.required_checks), set(CHECKS))

    def test_repo_config_carries_the_known_required_contexts(self):
        """The live config must still name the required checks the sweep evaluates."""
        text = (ROOT / ".mergify.yml").read_text(encoding="utf-8")
        c = qr.parse_config(text)
        self.assertEqual(c.base, "main")
        self.assertTrue(set(CHECKS).issubset(set(c.required_checks)), c.required_checks)

    def test_unknown_condition_fails_closed(self):
        bad = FIXTURE_CONFIG.replace("- check-success=docs", "- check-pending=docs")
        with self.assertRaises(qr.ConfigError) as ctx:
            qr.parse_config(bad)
        self.assertIn("check-pending=docs", str(ctx.exception))

    def test_missing_checks_is_an_error(self):
        bad = "queue_rules:\n  - name: main\n    queue_conditions:\n      - base=main\n      - -draft\n"
        with self.assertRaises(qr.ConfigError):
            qr.parse_config(bad)

    def test_no_rules_is_an_error(self):
        with self.assertRaises(qr.ConfigError):
            qr.parse_config("merge_protections_settings:\n  auto_merge_conditions: true\n")

    def test_bad_yaml_is_an_error(self):
        with self.assertRaises(qr.ConfigError):
            qr.parse_config("queue_rules: [\n")


# ── check-run interpretation ─────────────────────────────────────────────────


class TestCheckRuns(unittest.TestCase):
    def test_newest_by_name_uses_id_not_started_at(self):
        runs = [
            check("docs", conclusion="failure", cid=10, started_at=None),
            check("docs", conclusion="success", cid=11, started_at="2026-01-01T00:00:00Z"),
        ]
        newest = qr.newest_by_name(runs)
        self.assertEqual(newest["docs"]["conclusion"], "success")

    def test_completed_neutral_is_waiting_not_queued(self):
        self.assertEqual(qr.queue_state([mergify_check("completed", "neutral")]), "waiting")

    def test_in_progress_is_in_queue(self):
        self.assertEqual(qr.queue_state([mergify_check("in_progress", None)]), "in_queue")

    def test_completed_success_is_not_a_candidate(self):
        self.assertEqual(qr.queue_state([mergify_check("completed", "success")]), "in_queue")

    def test_absent(self):
        self.assertEqual(qr.queue_state(all_green()), "absent")

    def test_other_app_is_not_mergify(self):
        runs = [check(qr.QUEUE_CHECK_NAME, "in_progress", None, app="some-other-app")]
        self.assertEqual(qr.queue_state(runs), "absent")


# ── condition evaluation ─────────────────────────────────────────────────────


class TestUnmetConditions(unittest.TestCase):
    def test_all_green_has_no_unmet_conditions(self):
        self.assertEqual(qr.unmet_conditions(pr(), all_green(), cfg()), [])

    def test_names_each_failing_and_in_flight_check(self):
        runs = all_green()
        for run in runs:
            if run["name"] == "docs":
                run["conclusion"] = "failure"
            if run["name"] == "test-isolation":
                run["status"] = "in_progress"
                run["conclusion"] = None
        unmet = qr.unmet_conditions(pr(), runs, cfg())
        self.assertIn("check-success=docs: completed/failure", unmet)
        self.assertIn("check-success=test-isolation: in_progress (in flight)", unmet)
        self.assertEqual(len(unmet), 2)

    def test_skipped_and_neutral_do_not_pass(self):
        for conclusion in ("skipped", "neutral", "cancelled", None):
            runs = all_green()
            for run in runs:
                if run["name"] == "docs":
                    run["conclusion"] = conclusion
            unmet = qr.unmet_conditions(pr(), runs, cfg())
            self.assertTrue(any(u.startswith("check-success=docs:") for u in unmet), conclusion)

    def test_missing_check_run_is_named(self):
        runs = [r for r in all_green() if r["name"] != "legal-e2e"]
        unmet = qr.unmet_conditions(pr(), runs, cfg())
        self.assertIn("check-success=legal-e2e: no check-run", unmet)

    def test_draft_and_base_are_named(self):
        unmet = qr.unmet_conditions(pr(draft=True, base="release"), all_green(), cfg())
        self.assertIn("draft", unmet)
        self.assertIn("base=main (actual release)", unmet)


# ── idempotency ──────────────────────────────────────────────────────────────


class TestLiveCommand(unittest.TestCase):
    def test_command_after_head_is_live(self):
        comments = [{"body": "@mergifyio queue", "created_at": "2026-09-26T01:00:00Z"}]
        self.assertTrue(qr.has_live_command(comments, "2026-09-26T00:00:00Z"))

    def test_command_before_head_is_stale(self):
        comments = [{"body": "@mergifyio queue", "created_at": "2026-09-25T00:00:00Z"}]
        self.assertFalse(qr.has_live_command(comments, "2026-09-26T00:00:00Z"))

    def test_unrelated_comment_is_not_a_command(self):
        comments = [{"body": "looks good", "created_at": "2026-09-26T01:00:00Z"}]
        self.assertFalse(qr.has_live_command(comments, "2026-09-26T00:00:00Z"))


# ── the sweep ────────────────────────────────────────────────────────────────


class TestSweep(unittest.TestCase):
    def test_dry_run_posts_nothing(self):
        client = FakeGithub([pr(1)], {"sha1": [all_green()]})
        report = run(client)
        self.assertEqual(client.posted, [])
        self.assertEqual(report.tally(), {"WOULD-POST": 1})
        self.assertIn("DRY RUN", report.render())

    def test_never_touches_a_queued_label(self):
        client = FakeGithub([pr(1, labels=("queued",))], {"sha1": [all_green()]})
        report = run(client, dry_run=False)
        self.assertEqual(client.posted, [])
        self.assertEqual(report.tally(), {"SKIP-IN-QUEUE": 1})

    def test_never_touches_a_pr_in_flight_in_the_queue(self):
        client = FakeGithub([pr(1)], {"sha1": [[*all_green(), mergify_check("in_progress")]]})
        report = run(client, dry_run=False)
        self.assertEqual(client.posted, [])
        self.assertEqual(report.tally(), {"SKIP-IN-QUEUE": 1})

    def test_unmet_condition_is_named_and_nothing_is_posted(self):
        runs = all_green()
        for r in runs:
            if r["name"] == "python-ci-gate":
                r["conclusion"] = "failure"
        client = FakeGithub([pr(1)], {"sha1": [runs]})
        report = run(client, dry_run=False)
        self.assertEqual(client.posted, [])
        self.assertEqual(report.tally(), {"SKIP-UNMET": 1})
        self.assertIn("check-success=python-ci-gate: completed/failure", report.outcomes[0].detail)

    def test_live_command_is_not_reposted(self):
        client = FakeGithub(
            [pr(1)],
            {"sha1": [all_green()]},
            comments={1: [{"body": "@mergifyio queue", "created_at": "2026-09-26T01:00:00Z"}]},
            head_dates={"sha1": "2026-09-26T00:00:00Z"},
        )
        report = run(client, dry_run=False)
        self.assertEqual(client.posted, [])
        self.assertEqual(report.tally(), {"SKIP-LIVE-COMMAND": 1})

    def test_command_is_reposted_after_a_push(self):
        client = FakeGithub(
            [pr(1, sha="sha2")],
            {"sha2": [all_green(), [*all_green(), mergify_check("in_progress")]]},
            comments={1: [{"body": "@mergifyio queue", "created_at": "2026-09-25T00:00:00Z"}]},
            head_dates={"sha2": "2026-09-26T00:00:00Z"},
        )
        report = run(client, dry_run=False)
        self.assertEqual(len(client.posted), 1)
        self.assertEqual(client.posted[0][1], qr.DEFAULT_COMMAND)
        self.assertEqual(report.tally(), {"ENTERED": 1})

    def test_entered_requires_a_new_check_run(self):
        client = FakeGithub(
            [pr(1)],
            {"sha1": [all_green(), [*all_green(), mergify_check("in_progress", cid=901)]]},
        )
        report = run(client, dry_run=False)
        self.assertEqual(report.tally(), {"ENTERED": 1})
        self.assertIn("in_progress", report.outcomes[0].detail)

    def test_a_new_completed_success_check_run_is_also_an_entry(self):
        """`completed/success` on a NEW run means the queue took it and its own check
        passed — the other half of the artifact contract. An OLD success is not
        evidence about a new command, which is what `id > before_id` enforces.
        """
        client = FakeGithub(
            [pr(1)],
            {"sha1": [all_green(), [*all_green(), mergify_check("completed", "success", cid=905)]]},
        )
        report = run(client, dry_run=False)
        self.assertEqual(report.tally(), {"ENTERED": 1})
        self.assertIn("completed/success", report.outcomes[0].detail)

    def test_a_new_success_is_read_as_entry_over_a_stale_waiting_run(self):
        """A pre-existing `completed/neutral` makes the PR a candidate (waiting). If a
        NEW `completed/success` then appears, it is an entry — the stale waiting run
        must not mask it (newest by `id`, not by started_at).
        """
        stale = mergify_check("completed", "neutral", cid=50)
        client = FakeGithub(
            [pr(1)],
            {
                "sha1": [
                    [*all_green(), stale],
                    [*all_green(), stale, mergify_check("completed", "success", cid=906)],
                ]
            },
        )
        report = run(client, dry_run=False)
        self.assertEqual(len(client.posted), 1)
        self.assertEqual(report.tally(), {"ENTERED": 1})

    def test_completed_neutral_is_waiting_not_entered(self):
        client = FakeGithub(
            [pr(1)],
            {"sha1": [all_green(), [*all_green(), mergify_check("completed", "neutral", cid=902)]]},
        )
        report = run(client, dry_run=False)
        self.assertEqual(report.tally(), {"WAITING": 1})
        self.assertIn("NOT queued", report.outcomes[0].detail)

    def test_no_new_check_run_is_unknown_not_entered(self):
        client = FakeGithub([pr(1)], {"sha1": [all_green()]})
        report = run(client, dry_run=False)
        self.assertEqual(report.tally(), {"UNKNOWN": 1})
        self.assertIn("no new Mergify check-run", report.outcomes[0].detail)

    def test_post_failure_is_reported(self):
        client = FakeGithub(
            [pr(1)],
            {"sha1": [all_green()]},
            post_error={1: "410 Gone"},
        )
        report = run(client, dry_run=False)
        self.assertEqual(report.tally(), {"POST-FAILED": 1})
        self.assertEqual(client.posted, [])

    def test_post_cap_stops_posting(self):
        pulls = [pr(n, sha=f"sha{n}") for n in (1, 2, 3)]
        checks = {
            f"sha{n}": [all_green(), [*all_green(), mergify_check("in_progress", cid=1000 + n)]]
            for n in (1, 2, 3)
        }
        client = FakeGithub(pulls, checks)
        report = run(client, dry_run=False, post_cap=2)
        self.assertEqual(len(client.posted), 2)
        self.assertEqual(report.tally().get("SKIP-CAP"), 1)

    def test_spacing_is_applied_between_posts(self):
        pulls = [pr(n, sha=f"sha{n}") for n in (1, 2)]
        checks = {
            f"sha{n}": [all_green(), [*all_green(), mergify_check("in_progress", cid=2000 + n)]]
            for n in (1, 2)
        }
        client = FakeGithub(pulls, checks)
        slept: list[float] = []
        run(client, dry_run=False, spacing=3.0, sleep=slept.append)
        self.assertEqual(len(client.posted), 2)
        self.assertEqual(slept, [3.0])  # one gap between two posts, not after the last


class TestBaseGate(unittest.TestCase):
    """§7's state: the PR is ready and the BASE gate is red — `0 queued` is not
    "nothing is ready". Measured on #5600: six `completed/success` checks on the
    head while `python-ci-gate` on `main` was `completed/failure`."""

    def _red_base(self):
        runs = all_green(cid_base=500)
        for r in runs:
            if r["name"] == "python-ci-gate":
                r["conclusion"] = "failure"
        return runs

    def test_checks_that_do_not_report_on_the_base_cannot_block_it(self):
        """Only `python-ci-gate` reports on a push to main. Treating the five
        pull_request-only contexts as unmet base conditions would make the gate
        permanently red and the tool permanently refuse to post."""
        base_runs = [check("python-ci-gate", cid=1)]  # the only base-side reporter
        client = FakeGithub([pr(1)], {"sha1": [all_green()]}, base_checks=base_runs)
        report = run(client)
        self.assertEqual(report.base_unmet, [])
        self.assertTrue(report.base_green)
        self.assertEqual(report.tally(), {"WOULD-POST": 1})

    def test_base_gate_is_read_and_named(self):
        client = FakeGithub([pr(1)], {"sha1": [all_green()]}, base_checks=self._red_base())
        report = run(client)
        self.assertIn("branch:o/r:main", client.calls)
        self.assertFalse(report.base_green)
        self.assertEqual(report.base_unmet, ["check-success=python-ci-gate: completed/failure"])

    def test_in_flight_base_check_is_named(self):
        base_runs = [check("python-ci-gate", status="in_progress", conclusion=None, cid=1)]
        client = FakeGithub([pr(1)], {"sha1": [all_green()]}, base_checks=base_runs)
        report = run(client)
        self.assertEqual(
            report.base_unmet, ["check-success=python-ci-gate: in_progress (in flight on main)"]
        )

    def test_red_base_makes_a_ready_pr_skip_and_post_nothing(self):
        client = FakeGithub([pr(1)], {"sha1": [all_green()]}, base_checks=self._red_base())
        report = run(client, dry_run=False)
        self.assertEqual(client.posted, [])
        self.assertEqual(report.tally(), {"SKIP-BASE-RED": 1})
        self.assertIn("base gate unmet", report.outcomes[0].detail)
        self.assertIn("check-success=python-ci-gate", report.outcomes[0].detail)

    def test_report_says_ready_but_stuck(self):
        client = FakeGithub([pr(1)], {"sha1": [all_green()]}, base_checks=self._red_base())
        text = run(client).render()
        self.assertIn("base gate: RED", text)
        self.assertIn("does NOT mean nothing is ready", text)

    def test_pr_side_unmet_still_wins_over_base_red(self):
        runs = all_green()
        for r in runs:
            if r["name"] == "docs":
                r["conclusion"] = "failure"
        client = FakeGithub([pr(1)], {"sha1": [runs]}, base_checks=self._red_base())
        report = run(client, dry_run=False)
        self.assertEqual(report.tally(), {"SKIP-UNMET": 1})

    def test_allow_red_base_posts_and_names_the_base_condition(self):
        client = FakeGithub(
            [pr(1)],
            {"sha1": [all_green(), [*all_green(), mergify_check("completed", "neutral", cid=903)]]},
            base_checks=self._red_base(),
        )
        report = run(client, dry_run=False, allow_red_base=True)
        self.assertEqual(len(client.posted), 1)
        self.assertEqual(report.tally(), {"WAITING": 1})
        self.assertIn("base gate unmet", report.outcomes[0].detail)
        self.assertIn("check-success=python-ci-gate", report.outcomes[0].detail)

    def test_waiting_names_the_pr_side_condition_when_the_base_is_green(self):
        runs = all_green()
        for r in runs:
            if r["name"] == "legal-e2e":
                r["status"] = "in_progress"
                r["conclusion"] = None
        client = FakeGithub(
            [pr(1)],
            {"sha1": [all_green(), [*runs, mergify_check("completed", "neutral", cid=904)]]},
        )
        report = run(client, dry_run=False)
        self.assertEqual(report.tally(), {"WAITING": 1})
        self.assertIn("check-success=legal-e2e", report.outcomes[0].detail)


class TestGhCliSelectors(unittest.TestCase):
    """The `--jq` selector must iterate the array the endpoint actually returns.

    Regression: the check-runs endpoint returns an OBJECT
    (`{total_count, check_runs: [...]}`), so `.[]` yields its VALUES — a scalar and
    a nested list — and the sweep crashed on the first real dry run with
    `'list' object has no attribute 'get'`. The hermetic fakes could not catch it
    (they bypass `GhCli`), so the selector itself is pinned here.
    """

    def _jq_for(self, call):
        proc = mock.Mock(returncode=0, stdout="", stderr="")
        with mock.patch.object(qr, "_run", return_value=proc) as fake:
            call()
        args = fake.call_args.args[0]
        self.assertIn("--jq", args)
        return args[args.index("--jq") + 1]

    def test_check_runs_selects_the_check_runs_array(self):
        self.assertEqual(
            self._jq_for(lambda: qr.GhCli().list_check_runs("o/r", "sha")),
            ".check_runs[] | @json",
        )

    def test_pulls_and_comments_select_the_top_level_array(self):
        self.assertEqual(self._jq_for(lambda: qr.GhCli().list_pulls("o/r", "main")), ".[] | @json")
        self.assertEqual(self._jq_for(lambda: qr.GhCli().list_comments("o/r", 1)), ".[] | @json")

    def test_a_non_object_element_fails_loudly_instead_of_being_dropped(self):
        proc = mock.Mock(returncode=0, stdout="3\n", stderr="")
        with mock.patch.object(qr, "_run", return_value=proc), self.assertRaises(qr.GhError) as ctx:
            qr.GhCli().list_check_runs("o/r", "sha")
        self.assertIn("does not iterate the array", str(ctx.exception))

    def test_head_commit_date_is_read_as_a_raw_string(self):
        """`gh --jq` prints strings unquoted — parsing the date as JSON failed on the
        first real dry run and turned 28 eligible PRs into UNKNOWN."""
        proc = mock.Mock(returncode=0, stdout="2026-09-26T12:34:56Z\n", stderr="")
        with mock.patch.object(qr, "_run", return_value=proc):
            self.assertEqual(qr.GhCli().head_committed_at("o/r", "sha"), "2026-09-26T12:34:56Z")


class TestMain(unittest.TestCase):
    def _main(self, client, extra=None):
        # `--poll-timeout 0 --spacing 0`: the real `time.sleep`/`time.monotonic` are
        # used on the `main` path (that is the point — it is the production wiring),
        # so the tests pin the clock to zero rather than paying 9 poll intervals.
        argv = ["--repo", "o/r", "--config", "-", "--poll-timeout", "0", "--spacing", "0"]
        with (
            mock.patch.object(qr, "GhCli", return_value=client),
            mock.patch.object(sys, "stdin", mock.Mock(read=lambda: FIXTURE_CONFIG)),
        ):
            return qr.main(argv + (extra or []))

    def test_dry_run_exits_zero(self):
        client = FakeGithub([pr(1)], {"sha1": [all_green()]})
        self.assertEqual(self._main(client), 0)

    def test_unknown_verdict_exits_nonzero(self):
        client = FakeGithub([pr(1)], {"sha1": [all_green()]})
        self.assertEqual(self._main(client, ["--live"]), 1)

    def test_post_failure_exits_nonzero(self):
        client = FakeGithub([pr(1)], {"sha1": [all_green()]}, post_error={1: "boom"})
        self.assertEqual(self._main(client, ["--live"]), 1)

    def test_clean_live_run_exits_zero(self):
        client = FakeGithub(
            [pr(1)],
            {"sha1": [all_green(), [*all_green(), mergify_check("in_progress", cid=777)]]},
        )
        self.assertEqual(self._main(client, ["--live"]), 0)

    def test_config_error_exits_two(self):
        with mock.patch.object(sys, "stdin", mock.Mock(read=lambda: "queue_rules: []\n")):
            self.assertEqual(qr.main(["--repo", "o/r", "--config", "-"]), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
