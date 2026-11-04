"""ci_runner_labels — the label join, checked against a COMMITTED runner snapshot.

WHY THIS EXISTS
    On 2026-11-04 ~98 workflow runs sat queued for HOURS while 15 x86_64 self-hosted
    runners idled. No job was misconfigured and no test was red: every queued job asked
    for `ubuntu-latest`, and the only three runners carrying that label were ARM64 —
    the fallback gate's arm is capped (`FALLBACK_ARM_MAX`, default 3) and ordered by
    ASCENDING runner id, and the ARM pool happened to register first. The fifteen x64
    runners could not accept a label they did not carry, so they were unreachable
    capacity. Every required check is behind that queue, so no head could be tested and
    nothing could merge for hours.

    Nothing in the tree asked "can ANY runner answer the labels my jobs request?".
    `scripts/ci_runner_gate.sh --status` answers a neighbouring question — but it runs
    on the Mac, and on 2026-11-04 its log had been silent for three days.

TWO RULES, because they catch different failures
    R1  NO PROVIDER FOR A JOB'S LABEL SET. `runs-on` is an AND over ONE runner, so the
        unit that can be checked is the SET, not the individual label: a job asking
        `[self-hosted, self-hosted-x64, self-hosted-arm64]` is unsatisfiable on a pool
        where the x64 and ARM runners each carry half of it, while every label in the set
        individually has a provider. Checking per label would call that green.
        A label set nobody answers does not FAIL — it does not RUN: the job sits
        `pending` with zero materialised jobs, forever, and a required aggregate behind
        it never reports. Silent, total block.
    R2  ARCHITECTURE. `ubuntu-latest` is an x86_64 image on GitHub-hosted. A pool that
        claims it while carrying NO `X64` runner is serving x64-assumed jobs from another
        architecture — measured on 2026-11-04 as `aarch64` hosts running `test (a)`,
        `test (g)`, `test-record-postgres`, `test-d14-hosted-api`. R2 is the rule that
        reds on the 2026-11-04 state and greens once an x64 runner answers.

        R2 is deliberately NOT "no ARM runner may claim it": removing the label is the
        step that can strand a job, and #6897/#9878 own that decision. R2 only says that
        IF the pool claims an x64 image, at least one provider must actually be x64.

ALTERNATIVES, NOT ONE BIG SET
    A matrix job emits SEVERAL label sets — one per row — and any ONE of them makes the
    job schedulable. `tools/ci_selection.fast_shard_runner()` returns `["ubuntu-latest"]`
    for one row and `["self-hosted", "self-hosted-x64"]` for the next. Those are
    ALTERNATIVES. Unioning them would demand a single runner carrying `ubuntu-latest` AND
    `self-hosted` — impossible — so the gate would red a correct repo precisely when the
    pool is in its documented HEALTHY state (no pool runner claiming `ubuntu-latest`).
    So: each alternative is a conjunction (AND over one runner); the alternatives are a
    disjunction (any one suffices). Collapsing either direction makes this gate a false
    pass or a false block.

THE SNAPSHOT IS COMMITTED, NOT LIVE — AND IT IS A POINT-IN-TIME READ
    CI cannot call the Actions runners API (needs admin on the repo; the fleet token
    does not carry it on every box). So the provider set is a committed snapshot,
    `config/ci-runner-labels.json`, refreshed by:

        uv run python tools/ci_runner_labels.py --capture

    ⛔ A snapshot certifies the moment it was taken, not the next 90 days. Two
    consequences, stated rather than papered over, because `ubuntu-latest` is attached
    and detached by `scripts/ci_runner_gate.sh` on the order of minutes:
      * a RED may be a STALE CAPTURE (an x64 runner restarting during `--capture`), not a
        real gap — so RE-CAPTURE FIRST and only then treat it as a coverage failure.
      * the reverse is the real danger: a pool that answered at capture and was later
        relabelled keeps this gate GREEN until the bound expires. `MAX_SNAPSHOT_AGE_DAYS`
        is the only revalidation there is, so it REDS rather than warning.

⛔ THIS TOOL NEVER WRITES A LABEL. Attaching `ubuntu-latest` to a runner is the gate's
   job (`scripts/ci_runner_gate.sh`), or a deliberate, logged operator action. A checker
   that silently relabelled the pool would be able to make itself green.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
# `tools/` is a package, but `python tools/<script>.py` puts tools/ (not the repo root) on
# sys.path — so a sibling `from tools.x import y` needs the root inserted explicitly.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"
SNAPSHOT_PATH = REPO_ROOT / "config" / "ci-runner-labels.json"

#: A snapshot older than this REDS. The aim is "a past state cannot be certified
#: indefinitely", not a weekly chore; raise it with a reason, never delete it — and
#: `test_the_age_bound_stays_meaningful` will refuse a bound so large it stops being one.
MAX_SNAPSHOT_AGE_DAYS = 90

#: How far in the FUTURE a `captured_at` may sit before it is treated as stale. A skew
#: allowance is not optional: an unguarded negative age lets a clock-skewed capture (or a
#: one-digit year typo) disable the only revalidation this gate has, silently, for a year.
MAX_FUTURE_SKEW = timedelta(hours=1)

#: The images GitHub-hosted provides that this tree may legitimately rely on. Listed
#: EXPLICITLY rather than pattern-matched: a pattern (`macos-*`) would silently cover a
#: label nobody offers, which is the fail-open R1 exists to catch.
HOSTED_IMAGES = frozenset(
    {
        "ubuntu-latest",
        "ubuntu-22.04",
        "ubuntu-24.04",
        "ubuntu-26.04",
        "ubuntu-22.04-arm",
        "ubuntu-24.04-arm",
        "ubuntu-26.04-arm",
        "macos-13",
        "macos-14",
        "macos-15",
        "macos-latest",
        "windows-2019",
        "windows-2022",
        "windows-2025",
        "windows-latest",
    }
)

#: `ubuntu-latest` is x86_64 on GitHub-hosted. R2 keys off this, not off a label suffix.
X86_IMAGE_LABELS = frozenset({"ubuntu-latest", "ubuntu-22.04", "ubuntu-24.04", "ubuntu-26.04"})

#: The host-derived label a runner carries for x86_64. GitHub owns it; it cannot be set
#: by hand, which is what makes it the one trustworthy statement of a runner's arch.
X64_HOST_LABEL = "X64"

#: `${{ fromJSON(matrix.<key>) }}`, tolerant of the spacing a human may add.
_MATRIX_EXPR = re.compile(r"fromJSON\(\s*matrix\.([A-Za-z0-9_]+)\s*\)")


class UnreadableRunsOn(RuntimeError):
    """A `runs-on:` with no resolvable static label set.

    REFUSED, never cleared: treating an unparsed job as covered is how a coverage gate
    reports green over work it never read. `scripts/ci_runner_gate.sh --status` makes the
    same call (exit 4) for the same reason.
    """


# ── the requested side ────────────────────────────────────────────────────────────────
def _alternatives_from_scalar(value: str, where: str, job: dict | None = None) -> list[list[str]]:
    """One label as a single alternative, or the resolved alternatives of an expression."""
    text = value.strip()
    if "${{" not in text:
        return [[text]] if text else []
    match = _MATRIX_EXPR.search(text)
    if match is not None and job is not None:
        static = _matrix_alternatives(job, match.group(1), where)
        if static is not None:
            return static
        from tools.ci_selection import SHARD_RUNNER_HOSTED, SHARD_RUNNER_X64

        # TWO alternatives, NOT their union: a row asks for one routing or the other.
        return [list(SHARD_RUNNER_HOSTED), list(SHARD_RUNNER_X64)]
    raise UnreadableRunsOn(f"{where}: runs-on expression {text!r} has no resolvable label set")


def _alternatives_from_runs_on(
    value: object, where: str, job: dict | None = None
) -> list[list[str]]:
    """The job's ALTERNATIVE label sets — any ONE of which makes it schedulable.

    Handles the documented forms: a scalar, a flow array, a block sequence, and a
    `group:`/`labels:` mapping. A runner GROUP is routing, not a label — a group-only
    mapping therefore contributes no alternatives (it is READABLE and says "any runner in
    this group"), which is different from an unreadable value and must not red the tree.
    """
    if isinstance(value, list):
        alternatives: list[list[str]] = [[]]
        for item in value:
            if not isinstance(item, str):
                raise UnreadableRunsOn(f"{where}: non-string member in runs-on array: {item!r}")
            member = _alternatives_from_scalar(item, where, job)
            alternatives = [a + b for a in alternatives for b in member]
        return [a for a in alternatives if a]
    if isinstance(value, dict):
        if "labels" in value:
            return _alternatives_from_runs_on(value["labels"], where, job)
        if "group" in value:
            return []  # a runner GROUP is not a label; the group decides the routing
        raise UnreadableRunsOn(f"{where}: runs-on mapping carries neither `labels:` nor `group:`")
    if isinstance(value, str):
        return _alternatives_from_scalar(value, where, job)
    raise UnreadableRunsOn(f"{where}: unrecognised runs-on value {value!r}")


def _matrix_alternatives(job: dict, key: str, where: str) -> list[list[str]] | None:
    """The alternative label sets a `matrix.<key>` can hold, or None when the matrix is
    not visible in the workflow at all.

    ⛔ RESOLVED FROM THE JOB'S OWN MATRIX WHERE THAT IS POSSIBLE — one ALTERNATIVE per
    row. A static `include` is READ. A matrix that is itself an expression (this tree's
    `test` job: the whole matrix is emitted by `tools/ci_selection.py`) is invisible here,
    and falls back to the selector's two routings as two ALTERNATIVES.

    A matrix that IS visible but does not expose `key` is REFUSED. Substituting the
    selector's union there would fabricate a label set for a job whose real rows ask for
    something else, and the labels it really requests would go unchecked — the fail-open
    this module exists to close.
    """
    matrix = (job.get("strategy") or {}).get("matrix")
    if isinstance(matrix, str) and "${{" in matrix:
        return None  # computed outside the workflow
    if not isinstance(matrix, dict):
        return None
    rows = matrix.get("include")
    if not isinstance(rows, list):
        # Visible, but written as AXES rather than `include`. The rows' values are not
        # in the file, and guessing them is the fail-open.
        raise UnreadableRunsOn(
            f"{where}: matrix is visible but carries no `include` rows, so `matrix.{key}` "
            f"cannot be read — refusing rather than substituting an unrelated label set"
        )
    alternatives: list[list[str]] = []
    for row in rows:
        if not isinstance(row, dict) or key not in row:
            continue
        raw = row[key]
        if not isinstance(raw, str):
            raise UnreadableRunsOn(f"{where}: matrix.{key} is not a string: {raw!r}")
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise UnreadableRunsOn(
                f"{where}: matrix.{key} is not a JSON label array: {raw!r} ({exc})"
            ) from exc
        if not isinstance(parsed, list) or not all(isinstance(x, str) for x in parsed):
            raise UnreadableRunsOn(f"{where}: matrix.{key} is not a label array: {raw!r}")
        alternatives.append([x for x in parsed if x])
    if not alternatives:
        raise UnreadableRunsOn(
            f"{where}: the matrix's `include` rows carry no `{key}`, so `matrix.{key}` "
            f"cannot be read — refusing rather than substituting an unrelated label set"
        )
    return alternatives


def requested_labels(workflows_dir: Path = WORKFLOWS_DIR) -> dict[str, list[frozenset[str]]]:
    """`<workflow>:<job>` -> its ALTERNATIVE label sets (any one makes it schedulable).

    ⛔ THE ALTERNATIVES ARE KEPT APART ON PURPOSE — see the module docstring. A
    conjunction must be checked as a set (AND over one runner); a matrix must be checked
    as a disjunction of sets (a runner per row).
    """
    if not workflows_dir.is_dir():
        raise UnreadableRunsOn(f"no workflows directory at {workflows_dir}")

    paths = sorted(set(workflows_dir.glob("*.yml")) | set(workflows_dir.glob("*.yaml")))
    found: dict[str, list[frozenset[str]]] = {}
    for path in paths:
        try:
            doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:  # a workflow we cannot read is not a workflow we cover
            raise UnreadableRunsOn(f"{path.name}: unparseable YAML ({exc})") from exc
        if not isinstance(doc, dict):
            continue
        jobs = doc.get("jobs")
        if not isinstance(jobs, dict):
            continue
        for job_name, job in jobs.items():
            if not isinstance(job, dict) or "runs-on" not in job:
                continue
            where = f"{path.name}:{job_name}"
            alternatives = _alternatives_from_runs_on(job["runs-on"], where, job)
            if not alternatives:  # group-only routing: nothing to check, and READABLE
                continue
            found[where] = [frozenset(a) for a in alternatives]
    return found


# ── the provider side ────────────────────────────────────────────────────────────────
def pool_providers(snapshot: dict, label: str) -> list[dict]:
    """ONLINE self-hosted runners carrying `label`.

    ⛔ ONLINE ONLY. GitHub defines an offline runner as "not connected to GitHub" — it
    cannot be handed a job, so counting it as an answer is how a pool read `7 of 7` with
    every carrier offline. (`scripts/ci_runner_gate.sh --status` excludes them for the
    same reason, and prints them as context instead.)
    """
    return [
        r
        for r in snapshot.get("runners", [])
        if r.get("status") == "online" and label in r.get("labels", [])
    ]


def hosted_covers(label: str) -> bool:
    return label in HOSTED_IMAGES


def _pool_answers(snapshot: dict, labels: frozenset[str]) -> list[dict]:
    """ONLINE runners carrying EVERY label in the set (GitHub's AND semantics)."""
    return [
        r
        for r in snapshot.get("runners", [])
        if r.get("status") == "online" and labels <= set(r.get("labels", []))
    ]


def _alternative_is_answerable(snapshot: dict, labels: frozenset[str]) -> bool:
    """Is this ONE alternative satisfiable — by the pool, or by GitHub-hosted?"""
    if _pool_answers(snapshot, labels):
        return True
    # A single-image job is served by GitHub-hosted. A CONJUNCTION is never: hosted
    # carries one image and no `self-hosted`, so a multi-label set needs the pool.
    return len(labels) == 1 and hosted_covers(next(iter(labels)))


def violations(snapshot: dict, requested: dict[str, list[frozenset[str]]]) -> list[str]:
    """Every way the snapshot fails to answer the tree's own `runs-on:` label sets."""
    problems: list[str] = []

    for job in sorted(requested):
        alternatives = requested[job]
        if any(_alternative_is_answerable(snapshot, alt) for alt in alternatives):
            continue
        described = " | ".join(str(sorted(a)) for a in alternatives)
        # Name the most useful cause: a label with no provider at all is a different
        # repair from a conjunction no single runner carries.
        detail = None
        for alt in alternatives:
            missing = [x for x in sorted(alt) if not pool_providers(snapshot, x)]
            if missing:
                detail = f"no provider for {missing}"
                break
        if detail is None:
            detail = (
                "each label has a provider individually, but NO single runner carries the "
                "whole set — `runs-on` is an AND over one runner"
            )
        problems.append(
            f"R1 {job} cannot be scheduled by any runner: runs-on {described} — {detail}. "
            f"A label set nobody answers does not fail — the job sits `pending` forever, "
            f"withholding any required check behind it."
        )

    # R2 is judged per LABEL: it is about which ARCHITECTURE serves an x86_64 image, so it
    # applies whether the label is required or merely one alternative.
    every_label = {label for alts in requested.values() for alt in alts for label in alt}
    for label in sorted(X86_IMAGE_LABELS & every_label):
        providers = pool_providers(snapshot, label)
        if not providers:
            continue  # the pool does not claim it; hosted serves it. Not R2's business.
        if any(X64_HOST_LABEL in p.get("labels", []) for p in providers):
            continue
        names = ", ".join(sorted(p["name"] for p in providers))
        problems.append(
            f"R2 {label!r} is an x86_64 image, and the self-hosted pool claims it — but NO "
            f"provider is {X64_HOST_LABEL}: {names}. x64-assumed jobs would run on another "
            f"architecture (measured 2026-11-04 on ARM64 hosts: flaky/failed test legs), and "
            f"every idle x64 runner is unreachable capacity."
        )
    return problems


def staleness(snapshot: dict, now: datetime | None = None) -> str | None:
    """A message when the snapshot cannot certify the present, else None."""
    raw = snapshot.get("captured_at")
    if not raw:
        return (
            "the runner snapshot carries no `captured_at`, so it certifies nothing. "
            "Refresh it: uv run python tools/ci_runner_labels.py --capture"
        )
    try:
        captured = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return f"the runner snapshot's `captured_at` {raw!r} is not an ISO-8601 timestamp"
    if captured.tzinfo is None:
        captured = captured.replace(tzinfo=UTC)
    moment = now or datetime.now(UTC)
    if captured - moment > MAX_FUTURE_SKEW:
        return (
            f"the runner snapshot is dated {raw} — in the FUTURE by more than the "
            f"{int(MAX_FUTURE_SKEW.total_seconds() // 60)}-minute skew allowance, so its age "
            f"never expires and the staleness control is disabled. Fix `captured_at`."
        )
    age = moment - captured
    if age > timedelta(days=MAX_SNAPSHOT_AGE_DAYS):
        return (
            f"the runner snapshot is {age.days} days old (limit {MAX_SNAPSHOT_AGE_DAYS}) — a past "
            f"pool state cannot be certified as today's. Refresh: "
            f"uv run python tools/ci_runner_labels.py --capture"
        )
    return None


# ── capture ──────────────────────────────────────────────────────────────────────────
def capture(repo: str = "daniel-ospina/tortoise") -> dict:
    """Read the live pool and return the snapshot body. A READ — it writes no label.

    Uses `tools.runner_autoscale.read_pool()` so the snapshot and the autoscaler cannot
    disagree about who is in the pool or what they carry (that drift has already bitten
    once: `resolve_labels` vs the installer, pinned by a test).
    """
    from tools.runner_autoscale import read_pool

    pool = read_pool(repo)
    return {
        "captured_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "repo": repo,
        "refresh": "uv run python tools/ci_runner_labels.py --capture",
        "note": (
            "COMMITTED SNAPSHOT of the self-hosted runner pool's labels, valid as of "
            "captured_at only. CI cannot read the Actions runners API, so this file is the "
            "provider set the label-coverage test checks against. Refresh it; never "
            "hand-edit a runner's labels here."
        ),
        "hosted_images": sorted(HOSTED_IMAGES),
        "runners": [
            {
                "name": entry.name,
                "status": entry.status,
                "busy": entry.busy,
                "labels": sorted(entry.labels),
            }
            for entry in sorted(pool.values(), key=lambda e: e.name)
        ],
    }


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args == ["--capture"]:
        body = capture()
        SNAPSHOT_PATH.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {SNAPSHOT_PATH.relative_to(REPO_ROOT)} ({len(body['runners'])} runners)")
        return 0
    if args == ["--check"]:
        snapshot = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
        stale = staleness(snapshot)
        try:
            problems = ([stale] if stale else []) + violations(snapshot, requested_labels())
        except UnreadableRunsOn as exc:
            print(f"REFUSED (no verdict — a label set could not be read): {exc}")
            return 1
        for line in problems:
            print(line)
        if not problems:
            print("ok: every requested runs-on label set has a provider")
        return 1 if problems else 0
    print(__doc__)
    print("usage: ci_runner_labels.py (--capture | --check)")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
