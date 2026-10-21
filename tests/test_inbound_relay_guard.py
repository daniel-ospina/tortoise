"""Behavioural tests for the inbound-relay guard (#5185).

`.github/workflows/inbound-relay.yml` CLOSES pull requests: that is its whole
job (`pull_request_target` → label, comment, close, relay to intake). So its
one hard problem is telling apart

  * an untrusted EXTERNAL author — close it, and
  * the merge queue's own BATCH pull request — never touch it,

and the failure is asymmetric. Closing an external PR costs one triage entry.
Closing a batch PR dequeues the queued PR it was validating, i.e. it stops
EVERY merge in the fleet (#5185: no PR merged for ~7 h while this happened,
and the symptom reads as "the queue is flaky").

These tests evaluate the workflow's REAL job-level `if:` against synthetic
event payloads, so they pin behaviour rather than spelling: the job runs ⇔
`closes(...)` is True. A guard whose *text* changes but whose decisions are
unchanged still passes, and a guard that starts closing (or sparing) the wrong
class fails. That is the mutation the old text-only assertions could not
catch — `#5185`'s own fix keyed on the literal `mergify[bot]`, so a rename, a
queue-engine change, or a configured `queue_branch_prefix` silently re-broke
it.

Two tests at the end are deliberately SOURCE-level, because the rot they guard
is a literal that a reviewer must see: the queue branch prefix (config-bound,
read from `.mergify.yml`) and the absence of any hardcoded bot login. They
compare whitespace-normalised expression text, so reformatting the guard is
not a failure.

WHY THESE CLAUSES (research: Mergify's own docs, "Recognize a Queue Pull
Request" / "How leftover branches are matched"). A queue pull request carries
four markers, and only ONE of them holds for every queue PR:

  | marker | holds for every queue PR? |
  |---|---|
  | head branch = `queue_branch_prefix` + 10 hex | YES — "whoever opens it and whatever state it is in" |
  | author = `mergify[bot]` | NO — a queue rule with `draft_bot_account` opens drafts under THAT account |
  | draft | NO — the `merge-batch` method marks the draft ready for review before merging, and developers open drafts too |
  | title `merge queue: …` | NO — same exceptions, and a title is author-chosen |

So the guard's PRIMARY evidence is the queue-owned head branch (author
agnostic), and its BELT — the redundancy that survives the branch prefix being
reconfigured — is author identity from GitHub's own `type` field (an installed
GitHub App cannot be impersonated by a contributor), never a list of bot
logins. `queue_branch_prefix` is configuration, so it is read from
`.mergify.yml` here and bound to the workflow: change the config without
changing the guard and these tests go red instead of the queue going dark.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "inbound-relay.yml"
MERGIFY = REPO_ROOT / ".mergify.yml"

REPO = "daniel-ospina/tortoise"
OTHER_REPO = "someone-else/tortoise"
DEFAULT_BRANCH = "main"
# Mergify docs, "Recognize a Queue Pull Request": the prefix defaults to this
# and is set per queue rule; `.mergify.yml` in this repo sets no override.
DOCUMENTED_DEFAULT_PREFIX = "mergify/merge-queue/"


# ── A strict evaluator for the GitHub-Actions expression subset in the guard ──
#
# Deliberately narrow: it understands exactly the operators the guard uses and
# RAISES on anything else. A guard rewritten with an operator this harness does
# not model must fail the suite loudly, never fall through as `True` — a
# permissive evaluator here would turn "the guard changed shape" into a silent
# green, which is the same fail-open it exists to prevent.

_TOKEN = re.compile(
    r"\s*(?:"
    r"(?P<lparen>\()|(?P<rparen>\))|(?P<comma>,)|"
    r"(?P<cmp>!=|==)|(?P<and>&&)|(?P<or>\|\|)|(?P<not>!)|"
    r"(?P<fn>[A-Za-z_][A-Za-z0-9_]*)(?=\s*\()|"
    r"'(?P<str>[^']*)'|"
    r"(?P<path>[A-Za-z_][A-Za-z0-9_\-]*(?:\.[A-Za-z_][A-Za-z0-9_\-]*)+)"
    r")"
)


class UnsupportedExpression(AssertionError):
    """The guard uses syntax this harness does not model — do not guess."""


def _tokenize(expr: str) -> list[tuple[str, str]]:
    tokens: list[tuple[str, str]] = []
    pos = 0
    while pos < len(expr):
        if expr[pos].isspace():
            pos += 1
            continue
        m = _TOKEN.match(expr, pos)
        if not m:
            raise UnsupportedExpression(
                f"unsupported token at {expr[pos:pos + 30]!r}"
            )
        kind = m.lastgroup or ""
        tokens.append((kind, m.group(kind) or ""))
        pos = m.end()
    return tokens


def _truthy(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return value != ""
    return bool(value)


def _lookup(path: str, context: dict[str, Any]) -> Any:
    cur: Any = context
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _equals(left: Any, right: Any) -> bool:
    # GitHub coerces null to '' and compares strings case-insensitively.
    left = "" if left is None else left
    right = "" if right is None else right
    if isinstance(left, str) and isinstance(right, str):
        return left.casefold() == right.casefold()
    return left == right


def _starts_with(value: Any, prefix: Any) -> bool:
    if value is None or prefix is None:
        return False
    return str(value).casefold().startswith(str(prefix).casefold())


class _Parser:
    def __init__(self, tokens: list[tuple[str, str]]) -> None:
        self.tokens = tokens
        self.i = 0

    def peek(self) -> tuple[str, str] | None:
        return self.tokens[self.i] if self.i < len(self.tokens) else None

    def take(self) -> tuple[str, str]:
        tok = self.peek()
        if tok is None:
            raise UnsupportedExpression("unexpected end of expression")
        self.i += 1
        return tok

    def expect(self, kind: str) -> None:
        got = self.take()
        if got[0] != kind:
            raise UnsupportedExpression(f"expected {kind}, got {got[0]}")

    def parse(self, context: dict[str, Any]) -> Any:
        value = self.or_expr(context)
        if self.peek() is not None:
            raise UnsupportedExpression(f"trailing tokens: {self.tokens[self.i:]}")
        return value

    def or_expr(self, context: dict[str, Any]) -> Any:
        value = self.and_expr(context)
        while self.peek() and self.peek()[0] == "or":
            self.take()
            right = self.and_expr(context)
            # Actions' `||` returns the OPERAND, not a boolean (`x || 'd'` is the
            # idiomatic null-safe default). Modelling it as a bool would grade a
            # correct null-safe rewrite as wrong — a false block, never a false
            # green (review B, cycle 2).
            value = value if _truthy(value) else right
        return value

    def and_expr(self, context: dict[str, Any]) -> Any:
        value = self.unary(context)
        while self.peek() and self.peek()[0] == "and":
            self.take()
            right = self.unary(context)
            # Likewise `&&` yields the right operand when the left is truthy.
            value = right if _truthy(value) else value
        return value

    def unary(self, context: dict[str, Any]) -> Any:
        tok = self.peek()
        if tok and tok[0] == "not":
            self.take()
            return not _truthy(self.unary(context))
        return self.comparison(context)

    def comparison(self, context: dict[str, Any]) -> Any:
        left = self.primary(context)
        tok = self.peek()
        if tok and tok[0] == "cmp":
            op = self.take()[1]
            right = self.primary(context)
            return _equals(left, right) if op == "==" else not _equals(left, right)
        return left

    def primary(self, context: dict[str, Any]) -> Any:
        kind, text = self.take()
        if kind == "lparen":
            value = self.or_expr(context)
            self.expect("rparen")
            return value
        if kind == "str":
            return text
        if kind == "path":
            return _lookup(text, context)
        if kind == "fn":
            if text != "startsWith":
                raise UnsupportedExpression(f"unsupported function {text}()")
            self.expect("lparen")
            arg = self.or_expr(context)
            self.expect("comma")
            prefix = self.or_expr(context)
            self.expect("rparen")
            return _starts_with(arg, prefix)
        raise UnsupportedExpression(f"unsupported token {kind}={text!r}")


def _guard_expression() -> str:
    doc = yaml.safe_load(WORKFLOW.read_text())
    expr = doc["jobs"]["relay"].get("if")
    assert isinstance(expr, str) and expr.strip(), (
        "inbound-relay's guard must stay a job-level `if:` — it is what keeps "
        "the workflow at zero runner cost for internal traffic"
    )
    return expr


def closes(github: dict[str, Any]) -> bool:
    """True ⇔ the relay job runs on this event ⇔ the PR/issue is closed."""
    return bool(_Parser(_tokenize(_guard_expression())).parse({"github": github}))


# ── Event payloads ────────────────────────────────────────────────────────────

def _event(
    *,
    event_name: str = "pull_request_target",
    actor: str = "someone",
    login: str = "someone",
    user_type: str = "User",
    head_ref: str = "feature/thing",
    head_repo: str | None = REPO,
    head_repo_known: bool = True,
    fork: bool = False,
    base_ref: str = DEFAULT_BRANCH,
    draft: bool = False,
    default_branch: str | None = DEFAULT_BRANCH,
) -> dict[str, Any]:
    repository: dict[str, Any] = {"full_name": REPO}
    if default_branch is not None:
        repository["default_branch"] = default_branch

    pull: dict[str, Any] = {
        "number": 1234,
        "draft": draft,
        "user": {"login": login, "type": user_type},
        "head": {
            "ref": head_ref,
            "repo": (
                {"full_name": head_repo, "fork": fork} if head_repo_known else None
            ),
        },
        "base": {"ref": base_ref, "repo": {"full_name": REPO}},
    }
    if head_repo is None and head_repo_known:
        pull["head"]["repo"] = None

    # The shape the workflow's expressions actually see: `github.…` with the
    # webhook body under `github.event`, not a flattened payload.
    github: dict[str, Any] = {
        "event_name": event_name,
        "actor": actor,
        "repository": REPO,
        "event": {
            "action": "opened",
            "repository": repository,
            "pull_request": pull,
        },
    }
    if event_name == "issues":
        github["event"]["issue"] = {"number": 99, "title": "t", "body": "b"}
        github["event"].pop("pull_request")
    return github


def batch_pr(**over: Any) -> dict[str, Any]:
    """The live shape of a queue batch PR (measured: #6648, #6647, #6640)."""
    kwargs: dict[str, Any] = dict(
        actor="mergify[bot]",
        login="mergify[bot]",
        user_type="Bot",
        head_ref="mergify/merge-queue/6e89cfe947",
        head_repo=REPO,
        base_ref=DEFAULT_BRANCH,
        draft=True,
    )
    kwargs.update(over)
    return _event(**kwargs)


# ── (i) the queue's own batch PRs are NEVER closed ────────────────────────────

def test_live_batch_pr_is_not_closed() -> None:
    """The regression pin for #3558/#5185, on the shape measured in the repo."""
    assert closes(batch_pr()) is False


def test_batch_pr_is_not_closed_when_the_queue_bot_login_rotates() -> None:
    """The #5185 root cause: the old guard keyed on the LITERAL `mergify[bot]`.

    A renamed app or a different queue engine is the same class of PR — a
    bot-authored, same-repo, queue-branch batch — and must not be closed.
    """
    assert closes(
        batch_pr(actor="queue-app[bot]", login="queue-app[bot]")
    ) is False


def test_batch_pr_is_not_closed_when_the_prefix_is_reconfigured() -> None:
    """`queue_branch_prefix` is configuration (docs show e.g. `mergify-`)."""
    assert closes(
        batch_pr(
            head_ref="mergify-a76b1f3d25",
            actor="queue-app[bot]",
            login="queue-app[bot]",
        )
    ) is False


def test_batch_pr_marked_ready_for_review_is_not_closed() -> None:
    """`merge-batch` marks the draft ready before merging (vendor-documented).

    A guard that required `draft` would close exactly that batch.
    """
    assert closes(batch_pr(draft=False)) is False


def test_batch_pr_opened_under_a_bot_account_is_not_closed() -> None:
    """DELIBERATE, vendor-documented exception: `draft_bot_account` opens the
    queue's own draft under a USER account, so the branch belt is author-agnostic.

    Narrowing this case would dequeue every merge for a queue rule that sets
    `draft_bot_account` — i.e. it would reintroduce #5185 for that rule.
    """
    assert closes(
        batch_pr(actor="some-user", login="some-user", user_type="User")
    ) is False


# ── (ii) genuinely external PRs are still closed ──────────────────────────────

def test_external_fork_pr_is_closed() -> None:
    event = _event(
        actor="outsider",
        login="outsider",
        head_ref="patch-1",
        head_repo=OTHER_REPO,
        fork=True,
    )
    assert closes(event) is True


def test_external_issue_is_still_relayed() -> None:
    assert closes(_event(event_name="issues", actor="outsider", login="outsider")) is True


def test_fork_pr_named_like_a_queue_branch_is_closed() -> None:
    """A branch NAME is not evidence: anyone can name their fork's branch this.

    What a contributor cannot do is push that branch into THIS repository — so
    the head-repo clause, not the name, carries the decision.
    """
    event = _event(
        actor="outsider",
        login="outsider",
        head_ref="mergify/merge-queue/6e89cfe947",
        head_repo=OTHER_REPO,
        fork=True,
    )
    assert closes(event) is True


def test_internal_human_pr_on_an_ordinary_branch_is_closed() -> None:
    """Non-owner, non-queue traffic is what the relay is FOR."""
    event = _event(
        actor="collaborator",
        login="collaborator",
        user_type="User",
        head_ref="fix/thing",
        head_repo=REPO,
    )
    assert closes(event) is True


def test_mergify_bot_on_a_fork_pr_is_closed() -> None:
    """The old actor belt exempted `mergify[bot]` on ANY PR, fork included."""
    event = _event(
        actor="mergify[bot]",
        login="mergify[bot]",
        user_type="Bot",
        head_ref="not-a-queue-branch",
        head_repo=OTHER_REPO,
        fork=True,
    )
    assert closes(event) is True


def test_owner_prs_are_never_relayed() -> None:
    """The zero-cost bound: internal traffic is skipped before a runner exists."""
    assert closes(_event(actor="daniel-ospina", login="daniel-ospina")) is False


# ── (iii) ambiguous / unreadable FAILS CLOSED (treated as external) ───────────

AMBIGUOUS: dict[str, dict[str, Any]] = {
    "no pull_request payload": {
        "event_name": "pull_request_target",
        "actor": "outsider",
        "repository": REPO,
        "event": {"action": "opened", "repository": {"full_name": REPO}},
    },
    "both base fields unreadable at once": _event(
        login="mergify[bot]",
        user_type="Bot",
        head_ref="not-a-queue-branch",
        base_ref="",
        default_branch=None,
    ),
    "pr author is null": _event(login="x", user_type="User") | {
        "event": _event()["event"] | {
            "pull_request": _event()["event"]["pull_request"] | {"user": None}
        }
    },
    "author type is empty": _event(login="mergify[bot]", user_type=""),
    "author type is unknown": _event(login="mergify[bot]", user_type="Organization"),
    "head repo is null (deleted/unknown fork)": _event(head_repo_known=False),
    "head ref is underivable": _event(head_ref=""),
    "bot author, base branch unreadable": _event(
        login="mergify[bot]", user_type="Bot", base_ref=""
    ),
    "bot author, default branch unreadable": _event(
        login="mergify[bot]", user_type="Bot", default_branch=None
    ),
}


def test_ambiguous_or_unreadable_payloads_are_treated_as_external() -> None:
    assert AMBIGUOUS, "an empty fixture list would make this test vacuous"
    spared = [name for name, event in AMBIGUOUS.items() if not closes(event)]
    assert not spared, (
        "the relay FAILS CLOSED: a PR it cannot classify as the queue's own "
        f"batch is closed, but these were spared: {spared}"
    )


# ── the guard's literals are config-bound, never a list of names ──────────────

def _queue_rules() -> list[dict[str, Any]]:
    return yaml.safe_load(MERGIFY.read_text()).get("queue_rules") or []


def _effective_prefixes() -> set[str]:
    """Every prefix the configured queue rules actually use (documented default
    when a rule sets none) — the guard holds one literal per covered prefix."""
    prefixes = {
        str(rule.get("queue_branch_prefix") or DOCUMENTED_DEFAULT_PREFIX)
        for rule in _queue_rules()
    }
    return prefixes or {DOCUMENTED_DEFAULT_PREFIX}


def _parsed_bases() -> list[str]:
    """One entry per queue rule that yields a parseable `base=` value.

    A LIST, not a set: the caller needs to tell "a rule's base condition could
    not be parsed" (length mismatch) apart from "two rules share one base"
    (a single distinct value) — conflating them produced a false red with a
    wrong cause (review B, cycle 3).
    """
    bases: list[str] = []
    for rule in _queue_rules():
        for condition in rule.get("queue_conditions") or []:
            m = re.fullmatch(r"\s*base\s*=\s*(\S+)\s*", str(condition))
            if m:
                bases.append(m.group(1))
                break
    return bases


def _normalised_guard() -> str:
    """The guard's expression with ALL whitespace removed.

    Source-level assertions must compare MEANING at the text level. Collapsing
    whitespace runs is not enough: the house style may wrap inside the call, so
    `startsWith(` + a newline + the argument has to join back to `startsWith(`.
    Both sides are stripped identically here (review B, cycle 2), so a pure
    reformat cannot red this file — and `tests/test_merge_throughput.py` /
    `tests/test_reporting_workflow_concurrency.py` build the SAME stripped form,
    so one reformat has one answer across the suite.
    """
    return "".join(_guard_expression().split())


def test_every_configured_queue_branch_prefix_is_covered() -> None:
    """Rot is the defect. If a prefix is reconfigured or ADDED, this goes red.

    `queue_branch_prefix` is per queue rule, so a second rule can carry a second
    prefix. Enumerating the CONFIGURED set is what turns "a rule was added"
    into a red test instead of an outage (review A, finding 1).
    """
    guard = _normalised_guard()
    missing = sorted(
        prefix
        for prefix in _effective_prefixes()
        if f"startsWith(github.event.pull_request.head.ref,'{prefix}')" not in guard
    )
    assert not missing, (
        f"inbound-relay's branch evidence does not cover the configured "
        f"queue_branch_prefix value(s) {missing} in .mergify.yml; a queue rule "
        "on an uncovered prefix has its batch PR closed, which dequeues every "
        "PR in it (#5185)"
    )


def test_every_queue_rule_shares_one_base_that_belt_2_can_cover() -> None:
    """Belt 2 keys on ONE base (`github.event.repository.default_branch`).

    A queue rule on a second base is not covered by it — so adding one must red
    here rather than silently cost that rule's queued merges (review A,
    finding 1). The population is asserted, not assumed: a rule whose `base=`
    condition this cannot parse reds too, instead of being dropped from the
    count and leaving the test vacuous (review B, cycle 2).
    """
    rules = _queue_rules()
    assert rules, ".mergify.yml declares no queue_rules — this test would be vacuous"
    bases = _parsed_bases()
    assert len(bases) == len(rules), (
        f"{len(rules)} queue rule(s) but {len(bases)} parsed `base=` condition(s); "
        "a rule whose base condition is missing or spelled differently cannot be "
        "checked against belt 2's single-base assumption, so this test would pass "
        "vacuously"
    )
    assert len(set(bases)) <= 1, (
        f"queue rules target more than one base {sorted(set(bases))}; belt 2 only "
        "covers the repository default branch, so the guard needs a belt for "
        "each extra base or that rule's batch PRs will be closed (#5185)"
    )


def test_the_guard_enumerates_no_bot_logins() -> None:
    """Identity is GitHub's `type: Bot`, not a login list that rots (#5185)."""
    guard = _normalised_guard()
    offenders = re.findall(r"[A-Za-z0-9_.\-]+\[bot\]", guard)
    assert not offenders, (
        f"the guard keys on hardcoded bot logins {offenders}; use "
        "`github.event.pull_request.user.type == 'Bot'` so a rename cannot "
        "re-break the queue"
    )
    assert "user.type=='Bot'" in guard, (
        "the author-identity belt (GitHub's own `type` field) is missing"
    )


def test_the_relay_only_reacts_to_opened() -> None:
    """`action` is the one input the behavioural model does not vary.

    The guard never reads `github.event.action`: what bounds it to "once, at
    open" is the `types:` filter. Widening it (e.g. `+ synchronize`) would make
    the relay close and re-relay on every push with every test here still
    green, so the trigger is pinned alongside the guard it feeds (review B,
    cycle 2).
    """
    raw = yaml.safe_load(WORKFLOW.read_text())
    # YAML 1.1 loaders (PyYAML included) read the bare key `on` as boolean True.
    on = raw.get("on", raw.get(True))
    triggers = on if isinstance(on, dict) else {}
    assert triggers, "the workflow must declare an explicit `on:` mapping"
    assert triggers["pull_request_target"] == {"types": ["opened"]}
    assert triggers["issues"] == {"types": ["opened"]}


def test_the_evaluator_refuses_syntax_it_does_not_model() -> None:
    """The harness must never silently pass a guard it cannot read."""
    for expr in ("hashFiles('**') != ''", "github.actor =~ 'x'", "true &&& false"):
        with __import__("pytest").raises(UnsupportedExpression):
            _Parser(_tokenize(expr)).parse({"github": {"actor": "x"}})


def test_the_evaluator_models_actions_operand_semantics() -> None:
    """`a || b` yields the OPERAND in Actions, so a null-safe default works.

    A boolean-returning model would grade `(ref || 'none') == 'none'` wrong —
    a false block on a guard hardened for nulls, which is exactly what the
    fail-closed paragraph invites (review B, cycle 2).
    """
    def value(expr: str, ref: str = "feat/x") -> Any:
        ctx = {"github": {"actor": "bot", "event": {"pull_request": {"head": {"ref": ref}}}}}
        return _Parser(_tokenize(expr)).parse(ctx)

    # `'feat/x' || 'none'` is the string 'feat/x' — a boolean model returns True,
    # which then fails the comparison (the false block this test exists for).
    assert value("(github.event.pull_request.head.ref || 'none')") == "feat/x"
    assert value("(github.event.pull_request.head.ref || 'none') == 'feat/x'") is True
    # An empty ref falls through to the default.
    assert value("(github.event.pull_request.head.ref || 'none')", ref="") == "none"
    # `&&` yields the right operand when the left is truthy, else the left.
    assert value("(github.actor && 'yes')") == "yes"
    assert value("(github.event.pull_request.head.ref && 'yes')", ref="") == ""

