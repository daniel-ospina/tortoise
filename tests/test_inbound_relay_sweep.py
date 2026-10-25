"""The intake relay needs a path that does not depend on one event-time run.

MEASURED DEFECT (2026-10-24, this repo)
---------------------------------------
`.github/workflows/inbound-relay.yml` is the source stop for external
contributions (#2409 / #2441): label `queued-for-triage`, comment, close, relay
to intake. Its only external trigger is `opened`, so it gets ONE chance per
item — and that chance does not survive this repo's own settings:

  * `GET /repos/…/actions/permissions/fork-pr-contributor-approval` →
    `{"approval_policy": "all_external_contributors"}`: a workflow run started
    by an external actor is held until a maintainer approves it.
  * Of the 800 most recent runs of that workflow (2026-10-19T05:08Z →
    2026-10-24T23:53Z): **792 `skipped`** (owner-authored traffic, correctly
    excluded before a runner exists) and **8 `startup_failure`** — every one of
    them an external actor's event — and **0 executions**. 8 of 8 external
    triggers died before a single step ran.
  * Nothing re-fires it. `tools/ci_check_repair.py` repairs REQUIRED checks
    only; a close/reopen re-fires `reopened`, which this workflow does not
    listen to.
  * Measured cost on the item that named this lane: PR #9883 — a phantom whose
    entire diff was one README comment line (`README.md +3 −0`) — stayed open
    13 h 41 m, ran the full CI matrix (6 reds, all inherited from its base) and
    consumed a lane cycle (`pr-doctor-brief-prd-9883.txt`).

So the relief is not a smarter classifier of "is this diff a real fix" (we do
not own the producer, and an external producer can defeat any client-side
patch). It is the thing the design already promises and never delivered: the
relay's EFFECT must converge even when its event-time run dies.

WHAT THIS SUITE PINS
--------------------
`tools/inbound_relay_sweep.py` runs on a schedule under the repository's own
credential — not an external actor's — so it is not subject to the approval
policy and does not depend on any event run. It applies the SAME policy as the
workflow, and these tests pin both halves:

  * the decision, cross-checked against the workflow's own job-level `if:`
    (imported from `test_inbound_relay_guard.py`), so the two paths cannot
    drift apart into "the sweep closes what the relay would spare";
  * the rails, because a sweeper that closes pull requests is dangerous in a
    way a per-event relay is not: an incomplete listing must take NO action, a
    failed intake POST must leave the item OPEN and UNLABELLED for the next
    run, a run is capped, and the window is bounded.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
MODULE_PATH = REPO_ROOT / "tools" / "inbound_relay_sweep.py"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "inbound-relay.yml"
SWEEP_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "inbound-relay-sweep.yml"

OWNER = "daniel-ospina"
LABEL = "queued-for-triage"
NOW = datetime(2026, 10, 25, 0, 30, tzinfo=UTC)


def _load() -> Any:
    """The tool, loaded by path — `tools/` is not an installed package."""
    assert MODULE_PATH.is_file(), f"missing tool: {MODULE_PATH}"
    spec = importlib.util.spec_from_file_location("inbound_relay_sweep", MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # Registered before execution: a dataclass resolves its own module from
    # `sys.modules` while the class body runs.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def sweep() -> Any:
    return _load()


# ── a fake GitHub, recording every mutation in order ─────────────────────────


class FakeClient:
    """Records mutations; `fail_relay` makes the intake POST fail."""

    def __init__(
        self,
        *,
        issues: list[dict[str, Any]] | None = None,
        pulls: list[dict[str, Any]] | None = None,
        fail_relay: bool = False,
        fail_listing: bool = False,
        fail_detail: bool = False,
        fail_comment: bool = False,
        fail_close: bool = False,
        fail_label: bool = False,
        relayed: bool = False,
        fail_resume: bool = False,
    ) -> None:
        self._issues = issues or []
        self._pulls = pulls or []
        self.fail_relay = fail_relay
        self.fail_listing = fail_listing
        self.fail_detail = fail_detail
        self.fail_comment = fail_comment
        self.fail_close = fail_close
        self.fail_label = fail_label
        #: Model an item whose relay already commented (a retry, not a new item).
        self.relayed = relayed
        self.fail_resume = fail_resume
        self.labelled_reads: list[str] = []
        #: Set True to model a window denser than the page budget.
        self.window_truncated = False
        self.calls: list[tuple[str, Any]] = []
        self.seen_stop_before: Any = None

    # reads — one bounded listing, which carries pull requests as issue objects
    def list_open(self, *, stop_before: Any = None) -> list[dict[str, Any]]:
        self.seen_stop_before = stop_before
        if self.fail_listing:
            raise RuntimeError("rate limit exceeded")
        out = [dict(item) for item in self._issues]
        for pr in self._pulls:
            marked = {k: v for k, v in pr.items() if k not in ("head", "base")}
            marked["pull_request"] = {"url": "…"}
            out.append(marked)
        return out

    def pull_detail(self, number: int) -> dict[str, Any]:
        self.calls.append(("detail", number))
        if self.fail_detail:
            raise RuntimeError(f"pulls/{number} failed")
        for pr in self._pulls:
            if int(pr["number"]) == number:
                return dict(pr)
        raise RuntimeError(f"no such PR {number}")

    def list_labelled(self, label: str) -> list[dict[str, Any]]:
        self.labelled_reads.append(label)
        return [
            dict(item)
            for item in [*self._issues, *self._pulls]
            if label.casefold()
            in {
                str(entry.get("name") if isinstance(entry, dict) else entry).casefold()
                for entry in (item.get("labels") or [])
            }
        ]

    def already_relayed(self, number: int) -> bool:
        self.calls.append(("check", number))
        if self.fail_resume:
            raise RuntimeError("gh api comments failed (rc=1): 502")
        return self.relayed

    # writes
    def post_intake(self, payload: dict[str, Any]) -> None:
        self.calls.append(("intake", payload["source_item_id"]))
        if self.fail_relay:
            raise RuntimeError("intake refused")

    def add_label(self, number: int, label: str) -> None:
        self.calls.append(("label", number))
        if self.fail_label:
            raise RuntimeError("gh api labels failed (rc=1): rate limited")

    def comment(self, number: int, body: str) -> None:
        self.calls.append(("comment", number))
        if self.fail_comment:
            raise RuntimeError("gh api comments failed (rc=1): rate limited")

    def close(self, kind: str, number: int) -> None:
        self.calls.append(("close", number))
        if self.fail_close:
            raise RuntimeError("gh api PATCH failed (rc=1): 502")

    def mutations(self) -> list[tuple[str, Any]]:
        """Mutations only — a PR detail fetch or a marker check is a read."""
        return [call for call in self.calls if call[0] not in ("detail", "check")]


def issue(
    number: int,
    *,
    author: str = "someone",
    author_type: str = "User",
    labels: list[str] | None = None,
    created: timedelta = timedelta(hours=1),
) -> dict[str, Any]:
    return {
        "number": number,
        "title": f"t{number}",
        "body": "b",
        "html_url": f"https://github.com/{OWNER}/tortoise/issues/{number}",
        "user": {"login": author, "type": author_type},
        "labels": [{"name": name} for name in (labels or [])],
        "created_at": (NOW - created).isoformat().replace("+00:00", "Z"),
    }


def pull(
    number: int,
    *,
    author: str = "someone",
    author_type: str = "User",
    labels: list[str] | None = None,
    head_ref: str = "feature/thing",
    head_repo: str = "someone/tortoise",
    base_ref: str = "main",
    created: timedelta = timedelta(hours=1),
) -> dict[str, Any]:
    obj = issue(
        number,
        author=author,
        author_type=author_type,
        labels=labels,
        created=created,
    )
    obj["html_url"] = f"https://github.com/{OWNER}/tortoise/pull/{number}"
    obj.update(
        {
            "head": {"ref": head_ref, "repo": {"full_name": head_repo}},
            "base": {"ref": base_ref},
            "draft": False,
        }
    )
    return obj


def config(sweep: Any) -> Any:
    return sweep.Config(
        repo=f"{OWNER}/tortoise",
        owner=OWNER,
        label=LABEL,
        queue_prefixes=("mergify/merge-queue/",),
        intake_url="https://intake.example/functions/v1/inbound-ingest",
        intake_secret="s3cret",
    )


def run(sweep: Any, client: FakeClient, **kwargs: Any) -> Any:
    params: dict[str, Any] = {"since_hours": 24, "max_actions": 20, "dry_run": False}
    params.update(kwargs)
    return sweep.sweep(client, config(sweep), now=NOW, **params)


# ── (i) the phantom shape is relayed, in one order, exactly once ─────────────


def test_unrelayed_external_pr_is_relayed_then_closed(sweep: Any) -> None:
    """The #9883 shape: an external PR nobody has relayed yet.

    The sweep does not classify the DIFF — an external PR is closed and queued
    whatever its diff says, which is the whole point: a phantom and a real
    contribution take the same route, so no diff heuristic can be fooled.
    """
    client = FakeClient(pulls=[pull(9883, author="Maiiiry")])
    report = run(sweep, client)

    assert report.relayed == [("pr", 9883)]
    assert client.mutations() == [
        ("intake", "pr-9883"),
        ("comment", 9883),
        ("close", 9883),
        ("label", 9883),
    ], (
        "relay must land BEFORE the close — the record is why the close is safe — "
        "and the label must land LAST: it is the idempotence marker, so applying "
        "it before the close makes a failed close invisible to every later run "
        "(open + labelled = skipped forever, and nothing retries it)"
    )
    assert report.failed == []


def test_external_issue_is_relayed_then_closed(sweep: Any) -> None:
    client = FakeClient(issues=[issue(9913, author="fr3iheit")])
    report = run(sweep, client)

    assert report.relayed == [("issue", 9913)]
    assert client.mutations() == [
        ("intake", "issue-9913"),
        ("comment", 9913),
        ("close", 9913),
        ("label", 9913),
    ]


# ── (ii) the four classes the relay must never touch ─────────────────────────


def test_owner_items_are_never_touched(sweep: Any) -> None:
    client = FakeClient(
        issues=[issue(1, author=OWNER)],
        pulls=[pull(2, author=OWNER, head_repo=f"{OWNER}/tortoise")],
    )
    report = run(sweep, client)

    assert report.relayed == []
    assert client.mutations() == []


def test_already_relayed_items_are_skipped(sweep: Any) -> None:
    """The label is the idempotence marker: a second run is a no-op."""
    client = FakeClient(
        issues=[issue(1, labels=[LABEL])],
        pulls=[pull(2, labels=[LABEL])],
    )
    report = run(sweep, client)

    assert report.relayed == []
    assert client.mutations() == []


def test_queue_batch_pull_request_is_never_touched(sweep: Any) -> None:
    """#5185/#3558: closing a batch PR dequeues every merge in the fleet."""
    client = FakeClient(
        pulls=[
            pull(
                6648,
                author="mergify[bot]",
                author_type="Bot",
                head_ref="mergify/merge-queue/6e89cfe947",
                head_repo=f"{OWNER}/tortoise",
            )
        ]
    )
    report = run(sweep, client)

    assert report.relayed == []
    assert client.mutations() == []


def test_user_authored_same_repo_queue_branch_is_spared(sweep: Any) -> None:
    """The queue-branch arm, which bot identity CANNOT cover.

    Mergify's documented `draft_bot_account` opens the batch under a USER
    account, so belt 2 (`user.type == 'Bot'`) does not fire and the head branch
    is the only marker left. Every other same-repo `mergify/merge-queue/…` case
    in this suite is bot-authored, and the bot arm returns FIRST — so all of
    them stay green with this arm deleted. This is the case that goes red. The
    event path spares the same shape (its belt 1 is author-agnostic), and the
    parity test below pins that too.
    """
    batch = pull(
        17,
        author="some-user",
        author_type="User",
        head_ref="mergify/merge-queue/6e89cfe947",
        head_repo=f"{OWNER}/tortoise",
    )
    client = FakeClient(pulls=[batch])
    report = run(sweep, client)

    assert sweep.should_relay(batch, config(sweep)) is False
    assert report.relayed == []
    assert client.mutations() == []


def test_same_repo_bot_pr_to_the_default_branch_is_spared(sweep: Any) -> None:
    """Belt 2 of the guard: an installed App cannot be impersonated."""
    client = FakeClient(
        pulls=[
            pull(
                7,
                author="dependabot[bot]",
                author_type="Bot",
                head_ref="dependabot/npm/thing",
                head_repo=f"{OWNER}/tortoise",
                base_ref="main",
            )
        ]
    )
    assert run(sweep, client).relayed == []
    assert client.mutations() == []


def test_bot_authored_pr_is_spared_even_from_a_fork(sweep: Any) -> None:
    """A DELIBERATE asymmetry with the event path, and the reason for it.

    The event path closes a bot-authored PR from a fork (it is not the queue's
    batch). The sweep spares every bot-authored item, because the class of thing
    our automation opens includes live alarm threads — see
    `test_bot_authored_issue_is_never_closed`. An external bot's PR left open
    costs one step; our own monitor issue closed leaves a gap in the alarm
    history. The asymmetry decides the same way it does for the queue's batch.
    """
    client = FakeClient(
        pulls=[
            pull(
                8,
                author="mergify[bot]",
                author_type="Bot",
                head_ref="mergify/merge-queue/deadbeef00",
                head_repo="someone/tortoise",
            )
        ]
    )
    assert run(sweep, client).relayed == []
    assert client.mutations() == []


def test_bot_authored_issue_is_never_closed(sweep: Any) -> None:
    """#9613's shape: a live alarm thread opened by our own monitor.

    `deploy-hosted: deploy-api failed at 'drift'` — open, `bug`, authored by
    `github-actions[bot]`. Closing it would silently destroy a record a human
    reads, which is a worse error than a triage miss.
    """
    client = FakeClient(
        issues=[issue(9613, author="github-actions[bot]", author_type="Bot", labels=["bug"])]
    )
    report = run(sweep, client)

    assert report.relayed == []
    assert client.mutations() == []


def test_unreadable_author_type_fails_closed(sweep: Any) -> None:
    """An empty / unknown `user.type` is not evidence of the queue's batch."""
    client = FakeClient(
        pulls=[
            pull(
                9,
                author="mergify[bot]",
                author_type="",
                head_repo=f"{OWNER}/tortoise",
            )
        ]
    )
    assert run(sweep, client).relayed == [("pr", 9)]


# ── (iii) the rails — a sweeper must not act on what it could not read ───────


def test_incomplete_listing_takes_no_action(sweep: Any) -> None:
    """An unreadable page is not an empty page. No closes on a partial read."""
    client = FakeClient(pulls=[pull(1), pull(2)], fail_listing=True)
    report = run(sweep, client)

    assert report.relayed == []
    assert client.mutations() == []
    assert report.errors and report.exit_code == 2


def test_failed_intake_post_leaves_the_item_open_and_unlabelled(sweep: Any) -> None:
    """Relay-then-close: bookkeeping failure must not silently drop the record.

    The item stays OPEN and UNLABELLED, so the next run retries it — and the
    run exits non-zero, so a dead intake is an alarm rather than a silent
    "handled".
    """
    client = FakeClient(pulls=[pull(9883, author="Maiiiry")], fail_relay=True)
    report = run(sweep, client)

    assert client.mutations() == [("intake", "pr-9883")]
    assert report.relayed == []
    assert report.failed and report.failed[0][1] == 9883
    assert report.exit_code == 1


@pytest.mark.parametrize("failing_step", ["comment", "close"])
def test_a_bookkeeping_failure_leaves_the_item_for_the_next_run(
    sweep: Any, failing_step: str
) -> None:
    """A `gh` failure AFTER the intake POST must not escape `sweep()`.

    The pre-fix code guarded only the intake POST: a `RuntimeError` from
    `add_label` / `comment` / `close` propagated out of `sweep()`, `main()` had
    no `try`, so the run died with a traceback before printing the report and
    every remaining item was dropped. It also applied the LABEL before them,
    which left the item OPEN + LABELLED — skipped by every later run and
    retried by nothing (the event path only listens for `opened`).

    This pins both halves at once: no exception escapes, the item is reported
    as FAILED (so the run exits 1), and it was never labelled — so the next
    15-minute run selects it again.
    """
    client = FakeClient(pulls=[pull(9883, author="Maiiiry")], **{f"fail_{failing_step}": True})
    report = run(sweep, client)

    assert report.relayed == []
    assert [n for _, n in report.failed] == [9883]
    assert report.exit_code == 1
    assert ("label", 9883) not in client.mutations(), (
        "the label is the idempotence marker: applying it before a successful "
        "close leaves an open+labelled item that nothing retries"
    )
    assert any(failing_step in note for note in report.notes), report.notes
    # …and the evidence that a later run would retry it: the decision still says
    # "relay", and the label the sweep would have applied is absent.
    assert sweep.should_relay(pull(9883, author="Maiiiry"), config(sweep)) is True


def test_a_failed_label_is_reported_without_losing_the_rest_of_the_batch(
    sweep: Any,
) -> None:
    """The last step failing must still report, and must not abort the batch."""
    client = FakeClient(
        issues=[issue(1), issue(2)], pulls=[pull(3, author="Maiiiry")], fail_label=True
    )
    report = run(sweep, client)

    assert report.relayed == []
    assert [n for _, n in report.failed] == [1, 2, 3]
    assert report.exit_code == 1
    # Every item still got its record, comment and close — the label failed for
    # all three, and the run reported it instead of raising.
    assert ("close", 3) in client.mutations()
    # …and the operator is told the truth: this state is NOT retried.
    assert all("by hand" in note for note in report.notes if "failed" in note)


def test_a_dense_window_acts_on_the_prefix_and_defers_the_rest(sweep: Any) -> None:
    """A flood must not be an off-switch for the control.

    More items in the window than the page budget ⇒ the prefix is acted on and
    the unread remainder is reported as deferred. Aborting the whole run (the
    pre-fix behaviour: `ListingError` → `errors` → exit 2, no action) would let
    any GitHub user disable the sweep by opening 1,000 issues in 24 h.
    """
    client = FakeClient(pulls=[pull(9883, author="Maiiiry")])
    client.window_truncated = True
    report = run(sweep, client)

    assert report.relayed == [("pr", 9883)]
    assert report.truncated is True
    assert report.errors == []
    assert report.exit_code == 0
    assert any("deferred" in note for note in report.notes), report.notes


def test_the_detail_budget_bounds_the_reads_that_decide_the_actions(sweep: Any) -> None:
    """`--max` bounds the mutations; the detail budget bounds the reads.

    A detail fetch is one `gh api` call, and the page budget alone admits up to
    1,000 candidates. Unbounded, a dense window spends the whole REST budget on
    reads and then 403s on the mutations — and a mutation that fails after its
    intake POST re-relays the item every 15 minutes without ever closing it.
    """
    client = FakeClient(pulls=[pull(n, author="someone") for n in range(1, 21)])
    report = run(sweep, client, max_actions=20, detail_budget=3)

    fetches = [call for call in client.calls if call[0] == "detail"]
    assert len(fetches) == 3, fetches
    assert report.unexamined == 17
    assert report.deferred == 17
    assert report.exit_code == 0


def test_the_detail_fetch_stops_once_the_action_cap_is_met(sweep: Any) -> None:
    """Once `--max` actable items are known, more reads buy nothing."""
    client = FakeClient(pulls=[pull(n, author="someone") for n in range(1, 41)])
    report = run(sweep, client, max_actions=2)

    fetches = [call for call in client.calls if call[0] == "detail"]
    assert len(fetches) == 2, fetches
    assert report.unexamined == 38
    assert [n for _, n in report.relayed] == [1, 2]


def test_a_spared_queue_batch_does_not_exhaust_the_action_cap(sweep: Any) -> None:
    """The cap counts ACTABLE items, not examined ones.

    A pile of queue batches at the head of the window must not stop the sweep
    from reaching the external items behind them.
    """
    batches = [
        pull(
            n,
            author="mergify[bot]",
            author_type="Bot",
            head_ref="mergify/merge-queue/6e89cfe947",
            head_repo=f"{OWNER}/tortoise",
        )
        for n in range(1, 6)
    ]
    client = FakeClient(pulls=[*batches, pull(900, author="outsider")])
    report = run(sweep, client, max_actions=1)

    assert [n for _, n in report.relayed] == [900]
    # Bot-authored items cost NO detail fetch at all — `should_relay` decides
    # them from the listing, so the reads are spent only where they can change
    # the answer.
    assert [n for name, n in client.calls if name == "detail"] == [900]


def test_a_retry_resumes_instead_of_re_posting(sweep: Any) -> None:
    """A failed CLOSE must not make the next run comment a second time.

    The sequence is relay → comment → close → label, so an item whose close
    failed is selected again 15 minutes later. Without a resume it would re-post
    the same boilerplate comment on an external contributor's PR every 15
    minutes for as long as the close keeps failing. The relay's own footer is
    the marker that says "we already spoke".

    ⛔ The RECORD is still posted on a resume. The marker proves we commented,
    never that intake accepted the record — the event path posts intake LAST
    (label → comment → close → POST), so its footer can exist with no record at
    all — and `source_item_id` keys the POST, so repeating it is a no-op.
    """
    client = FakeClient(pulls=[pull(9883, author="Maiiiry")], relayed=True, fail_close=True)
    report = run(sweep, client)

    assert [n for _, n in report.failed] == [9883]
    assert client.mutations() == [("intake", "pr-9883"), ("close", 9883)], (
        "a retry must skip only the COMMENT — never the record"
    )

    # …and once the close stops failing, the retry completes with the marker
    # applied and still only one comment on the item.
    client = FakeClient(pulls=[pull(9883, author="Maiiiry")], relayed=True)
    report = run(sweep, client)
    assert report.relayed == [("pr", 9883)]
    assert client.mutations() == [("intake", "pr-9883"), ("close", 9883), ("label", 9883)]


def test_a_failed_resume_read_is_not_blamed_on_the_intake(sweep: Any) -> None:
    """The note names the step that actually failed — an operator debugs that."""
    client = FakeClient(pulls=[pull(9883, author="Maiiiry")], fail_resume=True)
    report = run(sweep, client)

    assert client.mutations() == []
    note = next(n for n in report.notes if "9883" in n)
    assert "resume check" in note and "intake POST" not in note, note


def test_the_resume_marker_is_the_comment_the_relay_actually_posts(sweep: Any) -> None:
    """A literal that drifts would silently turn every retry back into a re-post."""
    assert sweep.RELAY_MARKER in "\n".join(sweep._COMMENT_PULL)
    assert sweep.RELAY_MARKER in "\n".join(sweep._COMMENT_ISSUE)


def test_an_open_labelled_item_is_reported_and_rules_nothing(sweep: Any) -> None:
    """The one state nothing repairs must be NAMED, not silently skipped.

    `_prefilter` drops an open+labelled item (the label is the idempotence
    marker), so before this it appeared in no list, no count and no note — while
    the event path applies the label FIRST and can leave exactly this state (a
    502 on the comment/close). It is counted and reported; never auto-closed,
    because a human may have labelled it deliberately to keep it open.
    """
    client = FakeClient(
        issues=[issue(9913, author="fr3iheit", labels=[LABEL])],
        pulls=[pull(9884, author="Maiiiry", labels=[LABEL])],
    )
    report = run(sweep, client)

    assert report.stranded == 2
    assert report.relayed == []
    assert client.mutations() == [], "a stranded item is never auto-closed"
    assert any("HALF-APPLIED" in note for note in report.notes), report.notes
    assert report.exit_code == 0, "a note, not a red run every 15 minutes"


def test_a_labelled_owner_item_is_not_called_stranded(sweep: Any) -> None:
    """Owner traffic is outside the relay entirely — its labels are not ours."""
    client = FakeClient(issues=[issue(1, author=OWNER, labels=[LABEL])])
    report = run(sweep, client)

    assert report.stranded == 0
    assert not any("HALF-APPLIED" in note for note in report.notes)


def test_a_failed_label_says_it_will_not_be_retried(sweep: Any) -> None:
    """The label step is last: its failure is the one that is NOT self-healing.

    The close already succeeded, so the item is no longer in the open listing and
    no later run re-selects it. The note must say that instead of claiming a
    retry that cannot happen.
    """
    client = FakeClient(pulls=[pull(9883, author="Maiiiry")], fail_label=True)
    report = run(sweep, client)

    assert [n for _, n in report.failed] == [9883]
    note = next(n for n in report.notes if "9883" in n)
    assert "by hand" in note and "no later run" in note, note
    assert "retries it" not in note, note


def test_run_is_capped_and_reports_the_remainder(sweep: Any) -> None:
    client = FakeClient(issues=[issue(n) for n in range(1, 6)])
    report = run(sweep, client, max_actions=2)

    assert [n for _, n in report.relayed] == [1, 2]
    assert report.deferred == 3
    assert ("close", 3) not in client.mutations()


def test_window_excludes_older_items(sweep: Any) -> None:
    client = FakeClient(
        issues=[issue(1, created=timedelta(hours=1))],
        pulls=[pull(2, created=timedelta(hours=72))],
    )
    report = run(sweep, client, since_hours=24)

    assert report.relayed == [("issue", 1)]


def test_dry_run_performs_nothing(sweep: Any) -> None:
    client = FakeClient(pulls=[pull(9883, author="Maiiiry")])
    report = run(sweep, client, dry_run=True)

    assert report.relayed == [("pr", 9883)]
    assert client.mutations() == []
    assert report.exit_code == 0


def test_dry_run_needs_no_intake_credential(sweep: Any) -> None:
    """A dry run never POSTs, so it must not demand the secret it never uses.

    Refusing here turns "show me the plan" into a red run on exactly the machine
    where the credential is not configured.
    """
    client = FakeClient(pulls=[pull(9883, author="Maiiiry")])
    cfg = sweep.Config(
        repo=f"{OWNER}/tortoise",
        owner=OWNER,
        label=LABEL,
        queue_prefixes=("mergify/merge-queue/",),
        intake_url="",
        intake_secret="",
    )
    report = sweep.sweep(client, cfg, now=NOW, since_hours=24, max_actions=20, dry_run=True)

    assert report.relayed == [("pr", 9883)]
    assert client.mutations() == []
    assert report.errors == []
    assert report.exit_code == 0


def test_main_reports_a_crashing_sweep_instead_of_dying(sweep: Any, monkeypatch: Any) -> None:
    """`main()` must render the failure — a traceback loses the whole report."""

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("unexpected")

    monkeypatch.setattr(sweep, "sweep", boom)
    assert sweep.main(["--repo", f"{OWNER}/tortoise"]) == 2


def test_missing_intake_configuration_takes_no_action(sweep: Any) -> None:
    """No URL or no secret is not a reason to close without a record."""
    client = FakeClient(pulls=[pull(9883)])
    cfg = sweep.Config(
        repo=f"{OWNER}/tortoise",
        owner=OWNER,
        label=LABEL,
        queue_prefixes=("mergify/merge-queue/",),
        intake_url="",
        intake_secret="",
    )
    report = sweep.sweep(client, cfg, now=NOW, since_hours=24, max_actions=20, dry_run=False)

    assert client.mutations() == []
    assert report.exit_code == 2


# ── (iv) the two implementations of the policy cannot drift apart ────────────


def _load_event_guard() -> Any:
    """The event path's own suite, loaded BY PATH.

    ⛔ Never `importorskip`. This cross-check is the ONLY thing keeping the sweep
    and the event path from drifting apart, so a guard module that cannot be
    loaded must FAIL the suite, never skip past the gate green (a rename, a
    moved file or an `--import-mode=importlib` collection change would silently
    delete the control). Loading by path also removes the dependency on pytest's
    rootdir insertion.
    """
    path = REPO_ROOT / "tests" / "test_inbound_relay_guard.py"
    spec = importlib.util.spec_from_file_location("test_inbound_relay_guard", path)
    if spec is None or spec.loader is None:
        pytest.fail(f"the sweep/event parity gate cannot load the guard from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_policy_agrees_with_the_event_guard(sweep: Any) -> None:
    """The sweep's decision must equal the workflow's own job-level `if:`.

    `closes()` evaluates the REAL guard expression from
    `inbound-relay.yml`. If one path learns a new exclusion (a renamed queue
    prefix, a second spared class) and the other does not, this fails — which
    is the only durable defence against two implementations of one policy.
    """
    guard = _load_event_guard()

    cases: list[tuple[str, dict[str, Any], dict[str, Any]]] = [
        (
            "external fork PR",
            guard._event(actor="outsider", login="outsider"),
            pull(10, author="outsider"),
        ),
        (
            "owner PR",
            guard._event(actor=OWNER, login=OWNER, head_repo=f"{OWNER}/tortoise"),
            pull(11, author=OWNER, head_repo=f"{OWNER}/tortoise"),
        ),
        (
            "queue batch",
            guard.batch_pr(),
            pull(
                12,
                author="mergify[bot]",
                author_type="Bot",
                head_ref="mergify/merge-queue/6e89cfe947",
                head_repo=f"{OWNER}/tortoise",
            ),
        ),
        (
            # Mergify's `draft_bot_account`: the batch opened under a USER
            # account, where only the queue-OWNED HEAD BRANCH is evidence.
            "user-authored same-repo queue branch",
            guard._event(
                login="some-user",
                head_ref="mergify/merge-queue/6e89cfe947",
                head_repo=f"{OWNER}/tortoise",
            ),
            pull(
                17,
                author="some-user",
                head_ref="mergify/merge-queue/6e89cfe947",
                head_repo=f"{OWNER}/tortoise",
            ),
        ),
        (
            "bot same-repo default base",
            guard._event(
                login="dependabot[bot]",
                user_type="Bot",
                head_ref="dependabot/npm/x",
                head_repo=f"{OWNER}/tortoise",
            ),
            pull(
                13,
                author="dependabot[bot]",
                author_type="Bot",
                head_ref="dependabot/npm/x",
                head_repo=f"{OWNER}/tortoise",
            ),
        ),
        (
            "bot-authored issue (#9613)",
            guard._event(
                event_name="issues",
                actor="github-actions[bot]",
                login="github-actions[bot]",
                user_type="Bot",
            ),
            issue(16, author="github-actions[bot]", author_type="Bot"),
        ),
        (
            "fork PR named like a queue branch",
            guard._event(
                head_ref="mergify/merge-queue/0123456789",
                head_repo="someone-else/tortoise",
            ),
            pull(
                15,
                head_ref="mergify/merge-queue/0123456789",
                head_repo="someone-else/tortoise",
            ),
        ),
    ]

    disagreements = [
        (name, guard.closes(event), sweep.should_relay(item, config(sweep)))
        for name, event, item in cases
        if guard.closes(event) != sweep.should_relay(item, config(sweep))
    ]
    assert not disagreements, (
        "the sweep and the workflow disagree about who is external: "
        f"{disagreements} (guard_decision, sweep_decision)"
    )

    # The ONE deliberate divergence, pinned so it cannot widen by accident: a
    # bot-authored PR from a fork is closed by the event path and spared by the
    # sweep (see `test_bot_authored_pr_is_spared_even_from_a_fork`).
    bot_fork = pull(
        14,
        author="mergify[bot]",
        author_type="Bot",
        head_ref="mergify/merge-queue/deadbeef00",
        head_repo="someone-else/tortoise",
    )
    assert guard.closes(
        guard._event(
            login="mergify[bot]",
            user_type="Bot",
            head_ref="mergify/merge-queue/deadbeef00",
            head_repo="someone-else/tortoise",
        )
    ), "the event path no longer closes a bot's fork PR — re-derive this exception"
    assert sweep.should_relay(bot_fork, config(sweep)) is False


# ── (v) the literals are bound to the event path, not copied by hand ─────────


def test_sweep_workflow_is_scheduled_and_uses_the_same_label(sweep: Any) -> None:
    import yaml

    assert SWEEP_WORKFLOW.is_file(), "the sweep must actually be scheduled"
    doc = yaml.safe_load(SWEEP_WORKFLOW.read_text())

    # It must fire without an event, or it inherits the exact defect it repairs.
    assert "schedule" in doc[True], "a cron trigger is the point of this workflow"
    assert "workflow_dispatch" in doc[True]

    # The label is the event path's idempotence marker; a second literal here
    # that drifts would re-close everything on every run.
    event_doc = yaml.safe_load(WORKFLOW.read_text())
    event_text = json.dumps(event_doc["jobs"])
    assert LABEL in event_text, "the event path no longer uses this label"
    assert sweep.LABEL == LABEL, "the sweep must use the event path's label"
    assert sweep.OWNER == OWNER


def test_the_workflow_never_checks_out_a_dispatched_ref(sweep: Any) -> None:
    """A dispatch from an unmerged branch must not run that branch's tool.

    `workflow_dispatch` honours the dispatched ref, so an unpinned
    `actions/checkout` runs the branch's `tools/inbound_relay_sweep.py` AND
    reads its `.mergify.yml` against the live repo — while holding
    `issues: write`, `pull-requests: write` and the intake secret.
    `persist-credentials: false` does not help: the step token is what makes the
    mutations. A checkout step without `ref:` must fail here.
    """
    import yaml

    doc = yaml.safe_load(SWEEP_WORKFLOW.read_text())
    checkouts = [
        step
        for step in doc["jobs"]["sweep"]["steps"]
        if str(step.get("uses") or "").startswith("actions/checkout")
    ]
    assert len(checkouts) == 1, checkouts
    with_ = checkouts[0].get("with") or {}
    assert with_.get("ref") == "${{ github.event.repository.default_branch }}", (
        "the checkout must be PINNED to the default branch: an unpinned "
        "checkout executes the DISPATCHED ref's tool against the live repo"
    )
    assert with_.get("persist-credentials") is False


def test_a_manual_dispatch_defaults_to_inert(sweep: Any) -> None:
    """A dispatch to INSPECT the live repo must not relay, comment and close."""
    import yaml

    doc = yaml.safe_load(SWEEP_WORKFLOW.read_text())
    inputs = (doc[True]["workflow_dispatch"] or {}).get("inputs") or {}
    assert inputs["dry_run"]["default"] == "true", (
        "a silent live default turns 'show me the plan' into real closes"
    )

    # The schedule path passes no input, so `inputs.dry_run` is empty there —
    # empty is not 'true', so the scheduled run still acts. That property is the
    # WHOLE control's liveness, so assert the exact expression over the step's
    # own `run:` script rather than a substring a polarity flip would satisfy
    # (`!(inputs.dry_run == 'true') && '--dry-run'` makes every scheduled run
    # dry — the worst regression this workflow can have).
    script = next(
        step["run"]
        for step in doc["jobs"]["sweep"]["steps"]
        if "inbound_relay_sweep.py" in str(step.get("run") or "")
    )
    assert script.count("--dry-run") == 1, script
    assert re.search(r"\$\{\{\s*inputs\.dry_run == 'true' && '--dry-run' \|\| ''\s*\}\}", script), (
        "the flag must be guarded by `inputs.dry_run == 'true'` with an empty "
        "fallback: any other shape makes the SCHEDULED run dry (or unconditional)"
    )


def test_the_workflow_pins_its_only_dependency_to_the_lockfile(sweep: Any) -> None:
    """This job holds write tokens and the intake secret — no free-floating fetch."""
    text = SWEEP_WORKFLOW.read_text()
    installed = re.search(r"pip install[^\n]*?pyyaml([^\s\"']*)", text)
    assert installed, "the workflow must install the queue-config reader"
    spec = installed.group(1)
    assert spec.startswith("=="), (
        f"pyyaml is installed unpinned ({spec or 'no specifier'!r}) in the one job "
        "that holds `issues: write` + `pull-requests: write` and the intake secret"
    )
    locked = re.search(
        r'^name = "pyyaml"\nversion = "([^"]+)"', (REPO_ROOT / "uv.lock").read_text(), re.M
    )
    assert locked, "uv.lock no longer carries pyyaml"
    assert spec == f"=={locked.group(1)}", (
        f"the workflow pins pyyaml{spec} but the lockfile resolves "
        f"{locked.group(1)} — bind the pin to the lockfile"
    )


# ── (vi) the real client's paging rails, on the live backlog's shape ─────────
#
# This repo has 1,202 open issues+pulls, so "walk every page or raise" would
# raise on EVERY run — a permanent no-op wearing the costume of a safety rail.
# Paging is therefore window-bounded: newest-first, stop once a page reaches
# past the cutoff. These pin that with the real `GhClient` and a stubbed `gh`.


def _stub_gh(sweep: Any, monkeypatch: Any, pages: dict[int, Any]) -> list[str]:
    seen: list[str] = []

    def fake(argv: list[str], *, timeout: int = 60) -> str:
        seen.append(argv[1])
        page = int(argv[1].rsplit("page=", 1)[1])
        value = pages[page]
        if isinstance(value, Exception):
            raise value
        return json.dumps(value)

    monkeypatch.setattr(sweep, "_gh", fake)
    return seen


def test_every_entry_point_guards_the_interpreter_first(sweep: Any) -> None:
    """#5128: a tracked `tools/*.py` entry point must refuse <3.12 up front.

    This file imports `from datetime import UTC` at module level, so on an
    ambient 3.9 interpreter it dies with an unattributed ImportError before any
    guard could run. `tests/test_entry_point_python_guard.py` enumerates tracked
    entry points, so the guard has to live in the file itself.
    """
    text = MODULE_PATH.read_text()
    guard = text.index("sys.version_info < (3, 12)")
    assert guard < text.index("import argparse"), (
        "the interpreter guard must precede the imports it protects"
    )
    for required in (">= 3.12", "tools/inbound_relay_sweep.py", "uv run python"):
        assert required in text[: text.index("import argparse")], required


def test_client_stops_paging_at_the_window(sweep: Any, monkeypatch: Any) -> None:
    """One page is enough when the newest page already reaches past the window."""
    page = [issue(1000 - n, created=timedelta(minutes=10 * n + 1)) for n in range(100)]
    page[-1] = issue(1, created=timedelta(hours=48))  # oldest on page 1
    seen = _stub_gh(sweep, monkeypatch, {1: page})

    client = sweep.GhClient(f"{OWNER}/tortoise")
    got = client.list_open(stop_before=NOW - timedelta(hours=24))

    assert len(got) == 100
    assert seen == [
        "repos/daniel-ospina/tortoise/issues?state=open&sort=created&direction=desc"
        "&per_page=100&page=1"
    ], seen
    assert seen[0].count("?") == 1, "a second '?' is a 422, read as an incomplete listing"


def test_client_raises_on_a_failed_page(sweep: Any, monkeypatch: Any) -> None:
    """A transient gh failure is not an empty page."""
    page = [issue(n, created=timedelta(minutes=n + 1)) for n in range(100)]
    _stub_gh(sweep, monkeypatch, {1: page, 2: RuntimeError("rate limit exceeded")})

    client = sweep.GhClient(f"{OWNER}/tortoise")
    with pytest.raises(sweep.ListingError):
        client.list_open(stop_before=NOW - timedelta(hours=24))


def test_client_returns_the_prefix_instead_of_aborting_a_huge_window(
    sweep: Any, monkeypatch: Any
) -> None:
    """A window denser than the page budget yields a PREFIX, not an abort.

    The pre-fix client raised `ListingError` here, which `sweep()` turned into
    `errors` → exit 2 with no action at all. Since any GitHub user can open
    1,000 issues on a public repo, that made a flood an off-switch for the whole
    control. A FAILED page still raises (the rail that is correct) — see
    `test_client_raises_on_a_failed_page`.
    """
    page = [issue(1000 - n, created=timedelta(minutes=n + 1)) for n in range(100)]
    _stub_gh(sweep, monkeypatch, {n: page for n in range(1, 30)})

    client = sweep.GhClient(f"{OWNER}/tortoise", max_pages=3)
    got = client.list_open(stop_before=NOW - timedelta(hours=24))

    assert len(got) == 300, "the pages already read must be returned, not discarded"
    assert client.window_truncated is True
    # The flag is per-listing, never sticky: a later run that fits the budget
    # must not keep reporting a deferred remainder.
    _stub_gh(
        sweep,
        monkeypatch,
        {1: [issue(5, created=timedelta(minutes=5))]},
    )
    assert client.list_open(stop_before=NOW - timedelta(hours=24)) != []
    assert client.window_truncated is False


def test_queue_prefixes_are_read_from_the_live_config(sweep: Any) -> None:
    """The exclusion is config-bound — the same source the event guard reads."""
    import yaml

    live = yaml.safe_load((REPO_ROOT / ".mergify.yml").read_text())
    expected = {
        str(rule.get("queue_branch_prefix") or "mergify/merge-queue/")
        for rule in (live.get("queue_rules") or [])
    }
    assert set(sweep.queue_prefixes()) == expected


def test_unreadable_queue_config_refuses_to_act(sweep: Any, tmp_path: Path) -> None:
    """No config is not "no queue batch": closing one dequeues every merge."""
    with pytest.raises(sweep.ConfigError):
        sweep.queue_prefixes(tmp_path / "does-not-exist.yml")

    custom = tmp_path / "mergify.yml"
    custom.write_text("queue_rules:\n  - name: main\n    queue_branch_prefix: mergify-\n")
    assert sweep.queue_prefixes(custom) == ("mergify-",)


# ── (vii) the two reads that decide a RESUME and the STRANDED state ────────
#
# Both are real `GhClient` calls, pinned at the client level: the window-bounded
# listing cannot answer either question, and a `FakeClient` that ignores
# `stop_before` cannot tell that apart from a correct implementation.


def _stub_gh_json(sweep: Any, monkeypatch: Any, answers: dict[str, Any]) -> list[str]:
    """Stub `_gh` keyed on a substring of the requested path."""
    seen: list[str] = []

    def fake(argv: list[str], *, timeout: int = 60) -> str:
        seen.append(argv[1])
        for needle, value in answers.items():
            if needle in argv[1]:
                if isinstance(value, Exception):
                    raise value
                return json.dumps(value)
        raise AssertionError(f"unexpected gh call: {argv[1]}")

    monkeypatch.setattr(sweep, "_gh", fake)
    return seen


def test_the_stranded_read_is_filtered_and_window_independent(sweep: Any, monkeypatch: Any) -> None:
    """An open+labelled item that aged out of the 24 h window must still be found.

    The window read stops paging at the cutoff, so scanning it reported the
    unrepairable state only while the item was recent — and then it vanished from
    every report while staying open and retried by nothing.
    """
    client = sweep.GhClient(f"{OWNER}/tortoise")
    seen = _stub_gh_json(
        sweep,
        monkeypatch,
        {
            # the window read: newest items only, none of them labelled
            "sort=created&direction=desc": [
                issue(n, created=timedelta(minutes=n + 1)) for n in range(1, 4)
            ],
            # the filtered read: the old stranded item, well past the window
            "labels=queued-for-triage": [issue(500, author="old-timer", labels=[LABEL])],
        },
    )

    labelled = client.list_labelled(LABEL)

    assert [int(item["number"]) for item in labelled] == [500]
    assert any("state=open" in call and "labels=queued-for-triage" in call for call in seen), seen
    assert not any("sort=created&direction=desc" in call for call in seen), (
        "the stranded read must not ride on the window listing"
    )


def test_sweep_reports_a_stranded_item_outside_the_window(sweep: Any) -> None:
    client = FakeClient(
        issues=[issue(1, created=timedelta(hours=1))],
        pulls=[pull(500, author="old-timer", labels=[LABEL], created=timedelta(days=30))],
    )
    report = run(sweep, client)

    assert client.labelled_reads == [LABEL], "one dedicated filtered read"
    assert report.stranded == 1
    assert any("HALF-APPLIED" in note for note in report.notes), report.notes
    # …and the stale labelled item is NOT dragged into the action set.
    assert report.relayed == [("issue", 1)]


def test_the_resume_marker_must_be_our_own_comment(sweep: Any, monkeypatch: Any) -> None:
    """The footer is a fixed public string — anyone can paste it.

    Only a BOT-typed comment (an installed App / GITHUB_TOKEN, not impersonable)
    or the owner's own login counts, so a contributor cannot silence the relay's
    comment (or, worse, get an item closed on the strength of a quoted footer).
    """
    footer = {"body": f"👋 Thanks for the contribution!\n\n{sweep.RELAY_MARKER}"}
    client = sweep.GhClient(f"{OWNER}/tortoise")

    _stub_gh_json(
        sweep,
        monkeypatch,
        {"comments": [{**footer, "user": {"login": "Maiiiry", "type": "User"}}]},
    )
    assert client.already_relayed(9883) is False

    _stub_gh_json(
        sweep,
        monkeypatch,
        {"comments": [{**footer, "user": {"login": "github-actions[bot]", "type": "Bot"}}]},
    )
    assert client.already_relayed(9883) is True

    _stub_gh_json(
        sweep,
        monkeypatch,
        {"comments": [{**footer, "user": {"login": OWNER, "type": "User"}}]},
    )
    assert client.already_relayed(9883) is True, "a local run posts as the owner"

    _stub_gh_json(
        sweep,
        monkeypatch,
        {"comments": [{"body": "unrelated", "user": {"login": "x", "type": "Bot"}}]},
    )
    assert client.already_relayed(9883) is False


def test_the_resume_read_asks_for_the_newest_comments(sweep: Any, monkeypatch: Any) -> None:
    """Our comment is posted at CLOSE time — page 1 of the default order is oldest."""
    client = sweep.GhClient(f"{OWNER}/tortoise")
    seen = _stub_gh_json(sweep, monkeypatch, {"comments": []})

    assert client.already_relayed(9883) is False
    assert "sort=created&direction=desc" in seen[0], seen
