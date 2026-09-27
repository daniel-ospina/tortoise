"""Hermetic tests for tools/queue_resweep.py.

No network, no `gh`, no Mergify. Every GitHub surface is a `FakeGithub` whose
check-run responses are a SCRIPT, so the tests can reproduce the outcomes the tool
exists to tell apart (all measured 2026-09-26):

  * a `Mergify Merge Queue` check-run that goes `in_progress`   -> ENTERED
  * the same check-run `completed/neutral`                      -> WAITING, not queued
  * a run that never leaves `queued`                            -> UNKNOWN
  * no new check-run at all                                     -> UNKNOWN (exit 1)
  * the PR ready and the BASE gate red                          -> SKIP-BASE-RED, no post

The tests are the contract: dry run posts nothing, a PR already in the queue is
never touched, a live command for the current head is not re-posted, and a
`completed/neutral` answer is never reported as an entry.

Run standalone:    python3 tests/test_queue_resweep.py
Run under pytest:  TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix' \
                       uv run pytest tests/test_queue_resweep.py -q
                   (or TORTOISE_TEST_CARVE_OUT=1 for the URI-less embedded carve-out)
"""

from __future__ import annotations

import io
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
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
        base_error=None,
        query_error=None,
    ):
        self.pulls = pulls
        self.checks_by_sha = {k: list(v) for k, v in checks_by_sha.items()}
        # The base branch defaults to GREEN, so every test that is not about the
        # base gate exercises the ordinary path.
        self.base_sha = base_sha
        self.checks_by_sha.setdefault(
            base_sha, [base_checks if base_checks is not None else all_green(cid_base=500)]
        )
        self.comments = comments or {}
        self.head_dates = head_dates or {}
        self.post_error = post_error or {}
        self.base_error = base_error
        self.query_error = query_error or {}
        self.calls: list[str] = []
        self.posted: list[tuple[int, str]] = []

    def list_pulls(self, repo, base):
        self.calls.append(f"pulls:{repo}:{base}")
        return list(self.pulls)

    def branch_head_sha(self, repo, branch):
        self.calls.append(f"branch:{repo}:{branch}")
        if self.base_error:
            raise qr.GhError(self.base_error)
        return self.base_sha

    def list_check_runs(self, repo, sha):
        self.calls.append(f"checks:{sha}")
        if sha in self.query_error:
            raise qr.GhError(self.query_error[sha])
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
    kw.setdefault("config_source", "test")
    return qr.run_sweep(client, "o/r", cfg(), **kw)


def entered_script(n=1, cid=900):
    return [all_green(), [*all_green(), mergify_check("in_progress", cid=cid + n)]]


# ── config ───────────────────────────────────────────────────────────────────


class TestConfig(unittest.TestCase):
    def test_reads_the_gate(self):
        c = cfg()
        self.assertEqual(c.queue_name, "main")
        self.assertEqual(c.base, "main")
        self.assertEqual(set(c.required_checks), set(CHECKS))

    def test_repo_config_parses_to_structural_invariants_only(self):
        """Deliberately does NOT pin a literal check set.

        `queue_conditions` legitimately changes: #5384 moves the heavy
        `python-ci-gate` out of entry to merge-side. Pinning the set here would
        red that PR (a `.mergify.yml`-only change selects the FULL matrix) and
        would red this guard for a change the tool should tolerate.
        """
        text = (ROOT / ".mergify.yml").read_text(encoding="utf-8")
        c = qr.parse_config(text)
        self.assertEqual(c.base, "main")
        self.assertEqual(c.queue_name, "main")
        self.assertTrue(c.required_checks)

    def test_parses_a_merge_side_only_config(self):
        """The #5384 shape: the heavy check gates the merge, not entry."""
        text = FIXTURE_CONFIG.replace("      - check-success=python-ci-gate\n", "", 1)
        self.assertNotIn("python-ci-gate", qr.parse_config(text).required_checks)
        self.assertIn("merge_conditions", text)

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


class TestLoadConfig(unittest.TestCase):
    def test_stdin_source_is_labelled(self):
        with mock.patch.object(sys, "stdin", mock.Mock(read=lambda: FIXTURE_CONFIG)):
            text, source = qr.load_config_text("-")
        self.assertEqual(text, FIXTURE_CONFIG)
        self.assertEqual(source, "<stdin>")

    def test_explicit_path_source_is_labelled(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cfg.yml"
            path.write_text(FIXTURE_CONFIG, encoding="utf-8")
            text, source = qr.load_config_text(str(path))
            self.assertEqual(text, FIXTURE_CONFIG)
            self.assertEqual(source, str(path))

    def test_unreadable_main_ref_falls_back_LOUDLY(self):
        """The fallback may be an unrelated branch's copy of the gate, so which
        copy was read is part of the verdict and must not be silent."""
        err = io.StringIO()
        with redirect_stderr(err):
            text, source = qr.load_config_text(None, main_ref="origin/does-not-exist")
        self.assertIn("FALLBACK", source)
        self.assertIn(FIXTURE_CONFIG.splitlines()[0], text)
        self.assertIn("falling back", err.getvalue())


# ── check-run interpretation ─────────────────────────────────────────────────


class TestCheckRuns(unittest.TestCase):
    def test_newest_by_name_uses_id_not_started_at(self):
        """The two keys must DISAGREE, or this test cannot fail on the bug it names:
        a fixture where the higher `id` also carries the only non-null `started_at`
        passes under both orderings (proven by mutation)."""
        runs = [
            check("docs", conclusion="failure", cid=10, started_at="2026-09-26T00:00:00Z"),
            check("docs", conclusion="success", cid=11, started_at=None),
        ]
        self.assertEqual(qr.newest_by_name(runs)["docs"]["conclusion"], "success")

    def test_a_preferred_app_wins_over_a_newer_foreign_run(self):
        """A required context is (app, name); a PR-authored job named after one must
        not be able to mask it."""
        runs = [
            check("docs", conclusion="success", cid=99, app="some-fork-app"),
            check("docs", conclusion="failure", cid=10, app=qr.REQUIRED_CHECK_APP),
        ]
        self.assertEqual(qr.newest_by_name(runs)["docs"]["conclusion"], "failure")

    def test_a_foreign_app_is_used_when_no_preferred_run_exists(self):
        runs = [check("docs", conclusion="success", cid=5, app="other-app")]
        self.assertEqual(qr.newest_by_name(runs)["docs"]["conclusion"], "success")

    def test_check_apps_reports_the_ambiguity(self):
        runs = [check("docs", cid=1), check("docs", cid=2, app="fork")]
        self.assertEqual(qr.check_apps(runs)["docs"], {"github-actions", "fork"})

    def test_completed_neutral_is_waiting_not_queued(self):
        self.assertEqual(qr.queue_state([mergify_check("completed", "neutral")]), "waiting")

    def test_in_progress_is_in_queue(self):
        self.assertEqual(qr.queue_state([mergify_check("in_progress", None)]), "in_queue")

    def test_completed_success_is_not_a_candidate(self):
        self.assertEqual(qr.queue_state([mergify_check("completed", "success")]), "in_queue")

    def test_non_success_completions_do_not_hold_the_pr(self):
        """A failed/abandoned attempt means the queue does NOT hold the PR, so the PR
        is a candidate — the recovery this tool performs."""
        for conclusion in ("failure", "cancelled", "stale", "timed_out", "action_required", None):
            self.assertEqual(
                qr.queue_state([mergify_check("completed", conclusion)]), "waiting", conclusion
            )

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

    def test_command_at_the_head_second_is_live(self):
        """Comment and commit timestamps are second-granularity, so equality is the
        reachable boundary — `>` instead of `>=` would re-post in the same second."""
        same = "2026-09-26T00:00:00Z"
        self.assertTrue(
            qr.has_live_command([{"body": "@mergifyio queue", "created_at": same}], same)
        )

    def test_command_before_head_is_stale(self):
        comments = [{"body": "@mergifyio queue", "created_at": "2026-09-25T00:00:00Z"}]
        self.assertFalse(qr.has_live_command(comments, "2026-09-26T00:00:00Z"))

    def test_unrelated_comment_is_not_a_command(self):
        comments = [{"body": "looks good", "created_at": "2026-09-26T01:00:00Z"}]
        self.assertFalse(qr.has_live_command(comments, "2026-09-26T00:00:00Z"))

    def test_a_custom_command_matches_only_itself(self):
        comments = [{"body": "@mergifyio queue main", "created_at": "2026-09-26T01:00:00Z"}]
        self.assertTrue(
            qr.has_live_command(comments, "2026-09-26T00:00:00Z", "@mergifyio queue main")
        )
        self.assertFalse(qr.has_live_command(comments, "2026-09-26T00:00:00Z", qr.DEFAULT_COMMAND))

    def test_a_prose_mention_is_not_a_live_command(self):
        """Mergify acts on a comment whose BODY IS the command. A note that merely
        mentions it must not suppress the re-request — prune/audit comments quote the
        command inline, so a substring match would silently go quiet."""
        prose = [
            {
                "body": (
                    "Recommend closing. Each run buys a cycle; I would have posted "
                    "`@mergifyio queue` for it."
                ),
                "created_at": "2026-09-26T01:00:00Z",
            }
        ]
        self.assertFalse(qr.has_live_command(prose, "2026-09-26T00:00:00Z"))

    def test_the_command_on_its_own_line_inside_a_longer_body_is_live(self):
        comments = [
            {
                "body": "@mergifyio queue\n\nRe-requesting after the base gate went green.",
                "created_at": "2026-09-26T01:00:00Z",
            }
        ]
        self.assertTrue(qr.has_live_command(comments, "2026-09-26T00:00:00Z"))


# ── GitHub access ────────────────────────────────────────────────────────────


class TestGhCli(unittest.TestCase):
    """The selectors are pinned here because the hermetic fakes bypass `GhCli`."""

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

    def test_every_page_is_assembled(self):
        """`--paginate` emits one object per PAGE — more than one line is the norm."""
        proc = mock.Mock(returncode=0, stdout='{"id":1}\n{"id":2}\n', stderr="")
        with mock.patch.object(qr, "_run", return_value=proc):
            self.assertEqual([r["id"] for r in qr.GhCli().list_comments("o/r", 1)], [1, 2])

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

    def test_a_missing_gh_is_a_GhError_not_a_traceback(self):
        with (
            mock.patch.object(qr.subprocess, "run", side_effect=FileNotFoundError("gh")),
            self.assertRaises(qr.GhError),
        ):
            qr._run(["gh", "api", "x"])

    def test_a_hung_gh_is_a_GhError(self):
        with (
            mock.patch.object(
                qr.subprocess, "run", side_effect=qr.subprocess.TimeoutExpired("gh", 1)
            ),
            self.assertRaises(qr.GhError),
        ):
            qr._run(["gh", "api", "x"])


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

    def test_a_failed_queue_attempt_is_a_candidate(self):
        """The queue does not hold a PR whose last attempt completed `failure` — the
        re-sweep must re-request it rather than read it as in-queue."""
        client = FakeGithub(
            [pr(1)],
            {
                "sha1": [
                    [*all_green(), mergify_check("completed", "failure", cid=50)],
                    *entered_script(),
                ]
            },
        )
        report = run(client, dry_run=False)
        self.assertEqual(len(client.posted), 1)
        self.assertEqual(report.tally(), {"ENTERED": 1})

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
            {"sha2": entered_script()},
            comments={1: [{"body": "@mergifyio queue", "created_at": "2026-09-25T00:00:00Z"}]},
            head_dates={"sha2": "2026-09-26T00:00:00Z"},
        )
        report = run(client, dry_run=False)
        self.assertEqual(len(client.posted), 1)
        self.assertEqual(client.posted[0][1], qr.DEFAULT_COMMAND)
        self.assertEqual(report.tally(), {"ENTERED": 1})

    def test_repost_waiting_re_requests_a_neutral_attempt(self):
        """The one case the default rule cannot distinguish from 'Mergify is still
        tracking it': a live command whose attempt already answered neutral."""
        client = FakeGithub(
            [pr(1)],
            {
                "sha1": [
                    [*all_green(), mergify_check("completed", "neutral", cid=50)],
                    *entered_script(),
                ]
            },
            comments={1: [{"body": "@mergifyio queue", "created_at": "2026-09-26T01:00:00Z"}]},
            head_dates={"sha1": "2026-09-26T00:00:00Z"},
        )
        report = run(client, dry_run=False)
        self.assertEqual(client.posted, [])
        self.assertEqual(report.tally(), {"SKIP-LIVE-COMMAND": 1})

        client.posted.clear()
        client.checks_by_sha["sha1"] = [
            [*all_green(), mergify_check("completed", "neutral", cid=50)],
            *entered_script(),
        ]
        report = run(client, dry_run=False, repost_waiting=True)
        self.assertEqual(len(client.posted), 1)
        self.assertEqual(report.tally(), {"ENTERED": 1})

    def test_entered_requires_a_new_check_run(self):
        client = FakeGithub([pr(1)], {"sha1": entered_script(cid=900)})
        report = run(client, dry_run=False)
        self.assertEqual(report.tally(), {"ENTERED": 1})
        self.assertIn("in_progress", report.outcomes[0].detail)

    def test_a_new_completed_success_check_run_is_also_an_entry(self):
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
        must not mask it (newest by `id`, not by started_at)."""
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

    def test_a_run_that_never_advances_is_unknown(self):
        """A new run that stays `queued` must not be read as an entry — the reviewer
        proved this branch was unreachable from the suite as written."""
        client = FakeGithub(
            [pr(1)],
            {"sha1": [all_green(), [*all_green(), mergify_check("queued", None, cid=907)]]},
        )
        report = run(client, dry_run=False)
        self.assertEqual(report.tally(), {"UNKNOWN": 1})
        self.assertIn("stuck at queued", report.outcomes[0].detail)

    def test_no_new_check_run_is_unknown_not_entered(self):
        client = FakeGithub([pr(1)], {"sha1": [all_green()]})
        report = run(client, dry_run=False)
        self.assertEqual(report.tally(), {"UNKNOWN": 1})
        self.assertIn("no new Mergify check-run", report.outcomes[0].detail)

    def test_a_stale_run_is_not_evidence_about_a_new_command(self):
        """Strictly newer by `id`: a run that existed BEFORE the post is not evidence.
        With `>=` instead of `>` this reads the stale run as an entry."""
        stale = [*all_green(), mergify_check("completed", "neutral", cid=50)]
        client = FakeGithub([pr(1)], {"sha1": [stale, list(stale)]})
        report = run(client, dry_run=False)
        self.assertEqual(report.tally(), {"UNKNOWN": 1})

    def test_post_failure_is_reported(self):
        client = FakeGithub([pr(1)], {"sha1": [all_green()]}, post_error={1: "410 Gone"})
        report = run(client, dry_run=False)
        self.assertEqual(report.tally(), {"POST-FAILED": 1})
        self.assertEqual(client.posted, [])

    def test_a_query_failure_on_one_pr_does_not_abort_the_sweep(self):
        """Posting is a side effect: aborting mid-sweep would lose the report of what
        was already posted."""
        client = FakeGithub(
            [pr(1, sha="bad"), pr(2, sha="sha2")],
            {"sha2": entered_script(cid=100)},
            query_error={"bad": "404 head ref deleted"},
        )
        report = run(client, dry_run=False)
        self.assertEqual(report.tally(), {"QUERY-FAILED": 1, "ENTERED": 1})
        self.assertIn("head ref deleted", report.outcomes[0].detail)

    def test_only_restricts_which_prs_are_considered(self):
        pulls = [pr(n, sha=f"sha{n}") for n in (1, 2, 3)]
        checks = {f"sha{n}": entered_script(cid=100 * n) for n in (1, 2, 3)}
        client = FakeGithub(pulls, checks)
        report = run(client, dry_run=False, only=[2])
        self.assertEqual([n for n, _ in client.posted], [2])
        self.assertEqual(len(report.outcomes), 1)
        self.assertEqual(report.outcomes[0].number, 2)

    def test_post_cap_stops_posting(self):
        pulls = [pr(n, sha=f"sha{n}") for n in (1, 2, 3)]
        checks = {f"sha{n}": entered_script(cid=100 * n) for n in (1, 2, 3)}
        client = FakeGithub(pulls, checks)
        report = run(client, dry_run=False, post_cap=2)
        self.assertEqual(len(client.posted), 2)
        self.assertEqual(report.tally().get("SKIP-CAP"), 1)

    def test_spacing_is_applied_between_posts(self):
        pulls = [pr(n, sha=f"sha{n}") for n in (1, 2)]
        checks = {f"sha{n}": entered_script(cid=100 * n) for n in (1, 2)}
        client = FakeGithub(pulls, checks)
        slept: list[float] = []
        run(client, dry_run=False, spacing=3.0, sleep=slept.append)
        self.assertEqual(len(client.posted), 2)
        self.assertEqual(slept, [3.0])  # one gap between two posts, not after the last

    def test_a_verification_poll_waits_the_configured_interval(self):
        """`--poll-interval` is not observable when the first poll already succeeds.

        Response 1 is consumed by the pre-post classification read; polls 2 and 3 find
        no new run, so both must sleep before poll 4 finds the entry.
        """
        client = FakeGithub(
            [pr(1)],
            {
                "sha1": [
                    all_green(),
                    all_green(),
                    all_green(),
                    [*all_green(), mergify_check("in_progress", cid=908)],
                ]
            },
        )
        slept: list[float] = []
        report = run(client, dry_run=False, spacing=3.0, poll_interval=6.0, sleep=slept.append)
        self.assertEqual(report.tally(), {"ENTERED": 1})
        self.assertEqual(slept, [6.0, 6.0])  # no spacing: only one post

    def test_titles_are_sanitized(self):
        """PR titles are remote input: control characters must not reach the terminal."""
        client = FakeGithub([pr(1, title="ok\x1b[31mRED\x07\nnext\tline")], {"sha1": [all_green()]})
        rendered = run(client).render()
        self.assertNotIn("\x1b", rendered)
        self.assertNotIn("\x07", rendered)
        self.assertIn("ok [31mRED next line", rendered)

    def test_report_names_its_scope_and_command(self):
        client = FakeGithub([pr(1)], {"sha1": [all_green()]})
        text = run(client, command="@mergifyio queue hotfix", only=[1]).render()
        self.assertIn("@mergifyio queue hotfix", text)
        self.assertIn("scope --only 1", text)
        self.assertIn("config: test", text)


class TestBaseGate(unittest.TestCase):
    """The state where the PR is ready and the BASE gate is red — `0 queued` is not
    "nothing is ready". Measured on #5600: six `completed/success` checks on the head
    while `python-ci-gate` on `main` was `completed/failure`."""

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
        self.assertTrue(report.base_observable)
        self.assertEqual(report.tally(), {"WOULD-POST": 1})

    def test_an_unobservable_base_is_reported_as_such_not_as_green(self):
        """With no required check reporting on the base, the honest answer is "cannot
        tell". This is the regime #5384 would introduce, and the report names it."""
        client = FakeGithub([pr(1)], {"sha1": [all_green()]}, base_checks=[])
        report = run(client)
        self.assertFalse(report.base_observable)
        self.assertIn("NOT OBSERVABLE", report.render())
        self.assertNotIn("base gate: GREEN", report.render())
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

    def test_a_base_query_failure_is_reported_not_fatal(self):
        client = FakeGithub(
            [pr(1)], {"sha1": [all_green()]}, base_error="could not read branches/main"
        )
        report = run(client, dry_run=False)
        self.assertFalse(report.base_observable)
        self.assertEqual(report.tally().get("QUERY-FAILED"), 1)
        self.assertEqual(len(client.posted), 1)  # the sweep continues on the PR side

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


# ── main(): arguments -> sweep -> exit code ──────────────────────────────────


class TestMain(unittest.TestCase):
    def _main(self, client, extra=None, config=FIXTURE_CONFIG):
        # `--poll-timeout 0 --spacing 0`: the real `time.sleep`/`time.monotonic` are
        # used on the `main` path (that is the point — it is the production wiring),
        # so the tests pin the clock to zero rather than paying poll intervals.
        argv = ["--repo", "o/r", "--config", "-", "--poll-timeout", "0", "--spacing", "0"]
        with (
            mock.patch.object(qr, "GhCli", return_value=client),
            mock.patch.object(sys, "stdin", mock.Mock(read=lambda: config)),
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

    def test_query_failure_exits_nonzero(self):
        client = FakeGithub([pr(1, sha="bad")], {"bad": [all_green()]}, query_error={"bad": "404"})
        self.assertEqual(self._main(client, ["--live"]), 1)

    def test_clean_live_run_exits_zero(self):
        client = FakeGithub([pr(1)], {"sha1": entered_script(cid=777)})
        self.assertEqual(self._main(client, ["--live"]), 0)

    def test_config_error_exits_two(self):
        client = FakeGithub([], {})
        self.assertEqual(self._main(client, config="queue_rules: []\n"), 2)

    def test_a_multi_pr_journey_renders_every_verdict(self):
        """The wiring from argparse to run_sweep to the rendered SUMMARY, over a mixed
        outcome set — a mis-wired flag between them is invisible to unit tests."""
        pulls = [pr(1, sha="a"), pr(2, sha="b"), pr(3, sha="c")]
        unmet = all_green()
        for r in unmet:
            if r["name"] == "docs":
                r["conclusion"] = "failure"
        checks = {
            "a": entered_script(cid=10),  # 1 -> ENTERED
            "b": [all_green(), [*all_green(), mergify_check("completed", "neutral", cid=11)]],  # 2
            "c": [unmet],  # 3 -> SKIP-UNMET
        }
        client = FakeGithub(pulls, checks)
        out = io.StringIO()
        with mock.patch("sys.stdout", out):
            code = self._main(client, ["--live", "--post-cap", "10"])
        rendered = out.getvalue()
        # WAITING is a NAMED state with a named unmet condition — not an operational
        # failure — so it does not raise the exit code; UNKNOWN/POST-FAILED do.
        self.assertEqual(code, 0)
        self.assertIn("ENTERED=1", rendered)
        self.assertIn("WAITING=1", rendered)
        self.assertIn("SKIP-UNMET=1", rendered)
        self.assertIn("check-success=docs", rendered)
        self.assertEqual(len(client.posted), 2)

    def test_post_cap_reached_exits_nonzero(self):
        pulls = [pr(n, sha=f"sha{n}") for n in (1, 2)]
        checks = {f"sha{n}": entered_script(cid=100 * n) for n in (1, 2)}
        client = FakeGithub(pulls, checks)
        self.assertEqual(self._main(client, ["--live", "--post-cap", "1"]), 1)

    def test_only_flag_reaches_the_sweep(self):
        pulls = [pr(n, sha=f"sha{n}") for n in (1, 2)]
        checks = {f"sha{n}": entered_script(cid=100 * n) for n in (1, 2)}
        client = FakeGithub(pulls, checks)
        out = io.StringIO()
        with mock.patch("sys.stdout", out):
            self._main(client, ["--live", "--only", "2"])
        self.assertEqual([n for n, _ in client.posted], [2])
        self.assertIn("scope --only 2", out.getvalue())

    def test_command_flag_reaches_the_post(self):
        client = FakeGithub([pr(1)], {"sha1": entered_script()})
        self._main(client, ["--live", "--command", "@mergifyio queue hotfix"])
        self.assertEqual(client.posted, [(1, "@mergifyio queue hotfix")])


if __name__ == "__main__":
    unittest.main(verbosity=2)
