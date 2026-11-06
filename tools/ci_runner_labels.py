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

EVERY ROW IS A SEPARATE JOB — NONE OF THEM IS OPTIONAL
    A matrix expands into one job instance PER ROW, and GitHub runs all of them. So a
    single unanswered row is a job that sits `pending` forever and withholds the required
    aggregate behind it — the 2026-11-04 mechanism exactly. Every set this module returns
    is therefore REQUIRED, and `violations()` demands a provider for each one.

    ⛔ BUT THE ROWS MUST BE CHECKED AS SEPARATE SETS, NOT AS ONE UNION. The rows are
    `["ubuntu-latest"]` and `["self-hosted", "self-hosted-x64"]`
    (`tools/ci_selection.fast_shard_runner()`). Unioned into one set they demand a single
    runner carrying `ubuntu-latest` AND `self-hosted`, which no runner can be — so the
    gate would red the required suite on a correct repo, precisely when the pool is in its
    documented HEALTHY state. Conjunction WITHIN a row, disjunction ACROSS rows that are
    each independently required: both halves have to be right.

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

import sys

# #5128: refuse a <3.12 interpreter before the imports below — `from datetime import UTC`
# is module-level and 3.11+, so an older interpreter raises ImportError there before any
# later guard could run (the guard must be the first statement after the docstring).
# ⛔ The invocation named is the SCRIPT form: `tests/test_entry_point_python_guard.py`
# treats `tools/<x>.py` as a loose script (a package module needs a directory chain
# under `tools/`), so `-m tools.ci_runner_labels` is NOT this file's invocation.
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/ci_runner_labels.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/ci_runner_labels.py`"
    )

import json
import re
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
_MATRIX_JSON_EXPR = re.compile(r"fromJSON\(\s*matrix\.([A-Za-z0-9_]+)\s*\)")
#: The bare idiom `${{ matrix.<key> }}` — a label per row, not a JSON array.
_MATRIX_BARE_EXPR = re.compile(r"matrix\.([A-Za-z0-9_]+)")
#: The WHOLE value must be one expression. `pool-${{ matrix.os }}` is a PARTIAL expression
#: and must be refused: resolving only the expression discards the literal text around it,
#: so the gate would certify `ubuntu-latest` for a job that really asks
#: `pool-ubuntu-latest` — green over a job no runner can take.
_WHOLE_EXPR = re.compile(r"^\$\{\{.*\}\}$", re.DOTALL)

#: The one matrix key whose rows are emitted by `tools/ci_selection.py` rather than
#: written in the workflow. A computed matrix under any OTHER key cannot be read here,
#: and substituting the shard routings for it would certify a job with labels it never
#: asks for (the fabrication the visible-matrix branch refuses).
_SHARD_MATRIX_KEY = "runner"
#: The one job consuming it. Matched by `<workflow>:<job>` so a same-named key in another
#: workflow is refused rather than certified against the wrong row set.
_SHARD_CONSUMER = "python-ci.yml:test"


class UnreadableRunsOn(RuntimeError):
    """A `runs-on:` with no resolvable static label set.

    REFUSED, never cleared: treating an unparsed job as covered is how a coverage gate
    reports green over work it never read. `scripts/ci_runner_gate.sh --status` makes the
    same call (exit 4) for the same reason.
    """


# ── the requested side ────────────────────────────────────────────────────────────────
def _row_sets_from_scalar(value: str, where: str, job: dict | None = None) -> list[list[str]]:
    """One label as one row-set, or the resolved row-sets of an expression.

    ⛔ EVERY RETURNED SET IS REQUIRED — see the module docstring. A matrix's rows are all
    run, so none of them is optional.
    """
    text = value.strip()
    if "${{" not in text:
        return [[text]] if text else []
    if job is None or not _WHOLE_EXPR.match(text):
        # A PARTIAL expression cannot be resolved without discarding the literal text
        # around it, which would certify a label nobody asked for. Refused, never guessed
        # at — `docs/ops/ci-runner-x64.md` lists this form as one the parser must refuse.
        raise UnreadableRunsOn(
            f"{where}: runs-on {text!r} is not ONE whole resolvable expression — a partial or "
            f"unknown expression cannot be read as a label set"
        )

    as_json = _MATRIX_JSON_EXPR.search(text)
    if as_json is not None:
        return _matrix_rows(job, as_json.group(1), where, json_array=True)
    bare = _MATRIX_BARE_EXPR.search(text)
    if bare is not None:
        return _matrix_rows(job, bare.group(1), where, json_array=False)
    raise UnreadableRunsOn(f"{where}: runs-on expression {text!r} has no resolvable label set")


def _row_sets_from_runs_on(value: object, where: str, job: dict | None = None) -> list[list[str]]:
    """The job's REQUIRED label sets — one per matrix row; each an AND over one runner.

    Handles the documented forms: a scalar, a flow array, a block sequence, and a
    `group:`/`labels:` mapping. A runner GROUP is routing, not a label — a group-only
    mapping therefore contributes no sets (it is READABLE and says "any runner in this
    group"), which is different from an unreadable value and must not red the tree.
    """
    if isinstance(value, list):
        # ⛔ MORE THAN ONE MATRIX EXPRESSION IN ONE ARRAY IS REFUSED, NEVER
        # CROSS-PRODUCTED. A matrix runs ONE combination per row, so
        # `runs-on: ['${{ matrix.os }}', '${{ matrix.arch }}']` has as many rows as the
        # matrix does — not the product of the two expressions'. Multiplying invents
        # label PAIRS no row asks for, and those phantom rows can red a correct repo with
        # a message blaming "the label set". Refusing is the contract: never guess.
        expressions = [i for i in value if isinstance(i, str) and "${{" in i]
        if len(expressions) > 1:
            raise UnreadableRunsOn(
                f"{where}: runs-on names {len(expressions)} matrix expressions "
                f"({expressions}); their rows cannot be cross-producted without inventing "
                f"combinations the matrix never runs"
            )
        sets: list[list[str]] = [[]]
        for item in value:
            if not isinstance(item, str):
                raise UnreadableRunsOn(f"{where}: non-string member in runs-on array: {item!r}")
            row_sets = _row_sets_from_scalar(item, where, job)
            sets = [a + b for a in sets for b in row_sets]
        return [a for a in sets if a]
    if isinstance(value, dict):
        if "labels" in value:
            return _row_sets_from_runs_on(value["labels"], where, job)
        if "group" in value:
            return []  # a runner GROUP is not a label; the group decides the routing
        raise UnreadableRunsOn(f"{where}: runs-on mapping carries neither `labels:` nor `group:`")
    if isinstance(value, str):
        return _row_sets_from_scalar(value, where, job)
    raise UnreadableRunsOn(f"{where}: unrecognised runs-on value {value!r}")


def _matrix_rows(job: dict, key: str, where: str, json_array: bool) -> list[list[str]]:
    """Every row's label set for `matrix.<key>`.

    Read from the job's own `include` rows where the matrix is visible. A matrix that is
    itself an expression is invisible here; the ONLY such matrix this gate can resolve is
    the shard selector's, whose rows `tools/ci_selection.fast_shard_runner()` emits under
    the key `runner` — so those two row-sets are returned. Any other computed matrix is
    REFUSED rather than certified against labels its rows never ask for.
    """
    matrix = (job.get("strategy") or {}).get("matrix")
    if isinstance(matrix, str) and "${{ " in matrix:
        # ⛔ NOT BY KEY NAME ALONE. `matrix.runner` only has known rows in the ONE job that
        # consumes the shard selector (`python-ci.yml:test`). Another workflow could name
        # its own computed matrix `runner`, and certifying it against the shard routings
        # would be green over labels its rows never ask for. Identity, not convention.
        if key != _SHARD_MATRIX_KEY or where != _SHARD_CONSUMER:
            raise UnreadableRunsOn(
                f"{where}: `matrix.{key}` comes from a computed matrix this tool cannot read "
                f"(only {_SHARD_CONSUMER}'s `matrix.{_SHARD_MATRIX_KEY}` has known rows) — "
                f"refusing rather than certifying labels the rows never ask for"
            )
        from tools.ci_selection import SHARD_RUNNER_HOSTED, SHARD_RUNNER_X64

        # BOTH routings' rows, as SEPARATE required sets: a row is a job, so each must be
        # answerable. Unioning them would demand one runner carrying both.
        return [list(SHARD_RUNNER_HOSTED), list(SHARD_RUNNER_X64)]
    if not isinstance(matrix, dict):
        raise UnreadableRunsOn(f"{where}: no readable `strategy.matrix` for `matrix.{key}`")

    rows = matrix.get("include")
    axes = [k for k in matrix if k != "include"]
    if axes and rows is not None:
        # An `include` entry that cannot merge into an axis combination ADDS a further
        # combination (GitHub documents this), so the real row set cannot be recovered.
        raise UnreadableRunsOn(
            f"{where}: matrix mixes axis key(s) {axes} with `include`, so the generated row "
            f"set cannot be read statically"
        )
    if rows is None:
        # AXES ONLY: the axis named `key` is a literal list IN THE FILE, so its values are
        # read directly. (The cross product matters only when one label is assembled from
        # several axes at once, which the partial-expression refusal already rejects.)
        values = matrix.get(key)
        if not isinstance(values, list) or not values:
            raise UnreadableRunsOn(
                f"{where}: matrix carries neither an `include` list nor an axis named `{key}`"
            )
        out: list[list[str]] = []
        for item in values:
            if isinstance(item, list) and all(isinstance(x, str) for x in item):
                out.append([x for x in item if x])
            elif isinstance(item, str) and item.strip():
                out.append([item])
            else:
                raise UnreadableRunsOn(
                    f"{where}: matrix axis `{key}` has an unreadable member {item!r}"
                )
        return out
    if not isinstance(rows, list):
        raise UnreadableRunsOn(
            f"{where}: matrix carries no `include` rows, so `matrix.{key}` cannot be read"
        )
    row_sets: list[list[str]] = []
    for row in rows:
        if not isinstance(row, dict):
            raise UnreadableRunsOn(f"{where}: matrix `include` carries a non-mapping row {row!r}")
        if key not in row:
            # ⛔ NOT `continue`. A row that omits the key cannot be evaluated, and dropping
            # it would report "every row answers" for ROWS NEVER READ — the same
            # cleared-instead-of-refused defect the all-rows-missing case already fixes.
            raise UnreadableRunsOn(
                f"{where}: a matrix `include` row carries no `{key}`, so that row's "
                f"`runs-on` cannot be evaluated — refusing rather than skipping it"
            )
        raw = row[key]
        if isinstance(raw, list) and all(isinstance(x, str) for x in raw):
            # An `include` value may itself be a label ARRAY — a documented `runs-on` form.
            row_sets.append([x for x in raw if x])
            continue
        if not isinstance(raw, str):
            raise UnreadableRunsOn(f"{where}: matrix.{key} is not a string or label array: {raw!r}")
        if json_array:
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise UnreadableRunsOn(
                    f"{where}: matrix.{key} is not a JSON label array: {raw!r} ({exc})"
                ) from exc
            if not isinstance(parsed, list) or not all(isinstance(x, str) for x in parsed):
                raise UnreadableRunsOn(f"{where}: matrix.{key} is not a label array: {raw!r}")
            if not [x for x in parsed if x]:
                raise UnreadableRunsOn(f"{where}: matrix.{key} is an EMPTY label array")
            row_sets.append([x for x in parsed if x])
        elif raw.strip():
            row_sets.append([raw])
        else:
            raise UnreadableRunsOn(f"{where}: matrix.{key} is EMPTY, so it names no label")
    if not row_sets:
        raise UnreadableRunsOn(
            f"{where}: the matrix's `include` rows carry no `{key}`, so `matrix.{key}` "
            f"cannot be read — refusing rather than substituting an unrelated label set"
        )
    return row_sets


def reusable_workflow_jobs(workflows_dir: Path = WORKFLOWS_DIR) -> list[str]:
    """`<workflow>:<job>` for every job that delegates to a REUSABLE WORKFLOW.

    ⛔ A job-level `uses:` takes its `runs-on:` from the CALLED workflow, which lives in
    another repo — so this gate can never read it. Silently skipping those jobs is how
    "every label set is covered" becomes a claim about a population nobody enumerated:
    `agent-infra` moving one of its reusable workflows onto a label this pool cannot answer
    would leave those jobs `pending` forever while this suite stayed green.

    They are therefore COUNTED and PINNED rather than skipped in silence: the suite asserts
    the exact set, so a NEW reusable-workflow job reds until someone either adds its labels
    here or records why it is out of scope.
    """
    found: list[str] = []
    if not workflows_dir.is_dir():
        return found
    for path in sorted(set(workflows_dir.glob("*.yml")) | set(workflows_dir.glob("*.yaml"))):
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        jobs = doc.get("jobs") if isinstance(doc, dict) else None
        if not isinstance(jobs, dict):
            continue
        for job_name, job in jobs.items():
            if isinstance(job, dict) and "uses" in job and "runs-on" not in job:
                found.append(f"{path.name}:{job_name}")
    return found


def requested_labels(workflows_dir: Path = WORKFLOWS_DIR) -> dict[str, list[frozenset[str]]]:
    """`<workflow>:<job>` -> the REQUIRED label sets (one per matrix row; all must answer).

    ⛔ EVERY SET IS REQUIRED. A matrix runs every row, so an unanswered row is a job that
    never starts. Within a set it is an AND over one runner; across sets, each is
    independently required — never unioned, and never "any one suffices".
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
            row_sets = _row_sets_from_runs_on(job["runs-on"], where, job)
            if not row_sets:  # group-only routing: nothing to check, and READABLE
                continue
            # de-duplicate while keeping a stable order (a matrix can repeat a row)
            unique: list[frozenset[str]] = []
            for row in row_sets:
                frozen = frozenset(row)
                if frozen not in unique:
                    unique.append(frozen)
            found[where] = unique
    return found


# ── the provider side ────────────────────────────────────────────────────────────────
def _norm(label: str) -> str:
    """GitHub matches labels CASE-INSENSITIVELY.

    docs.github.com "apply labels": "Labels are case-insensitive", and this repo pins the
    same rule for its own label algebra (`tools/runner_autoscale.py::_preserve_labels`,
    `tests/test_runner_autoscale.py` — a mixed-case `Self-Hosted-Arm64` IS the arm64
    marker). Comparing exactly would red a pool that can schedule the job for a spelling
    difference, and the message would blame the label set.
    """
    return label.strip().lower()


def pool_providers(snapshot: dict, label: str) -> list[dict]:
    """ONLINE self-hosted runners carrying `label`.

    ⛔ ONLINE ONLY. GitHub defines an offline runner as "not connected to GitHub" — it
    cannot be handed a job, so counting it as an answer is how a pool read `7 of 7` with
    every carrier offline. (`scripts/ci_runner_gate.sh --status` excludes them for the
    same reason, and prints them as context instead.)
    """
    wanted = _norm(label)
    return [
        r
        for r in snapshot.get("runners", [])
        if r.get("status") == "online" and wanted in {_norm(x) for x in r.get("labels", [])}
    ]


def hosted_covers(label: str) -> bool:
    return _norm(label) in {_norm(x) for x in HOSTED_IMAGES}


def _pool_answers(snapshot: dict, labels: frozenset[str]) -> list[dict]:
    """ONLINE runners carrying EVERY label in the set (GitHub's AND semantics)."""
    wanted = {_norm(x) for x in labels}
    return [
        r
        for r in snapshot.get("runners", [])
        if r.get("status") == "online" and wanted <= {_norm(x) for x in r.get("labels", [])}
    ]


def _row_is_answerable(snapshot: dict, labels: frozenset[str]) -> bool:
    """Is this ONE row satisfiable — by the pool, or by GitHub-hosted?"""
    if _pool_answers(snapshot, labels):
        return True
    # A single-image row is served by GitHub-hosted. A CONJUNCTION is never: hosted
    # carries one image and no `self-hosted`, so a multi-label row needs the pool.
    return len(labels) == 1 and hosted_covers(next(iter(labels)))


def violations(snapshot: dict, requested: dict[str, list[frozenset[str]]]) -> list[str]:
    """Every way the snapshot fails to answer the tree's own `runs-on:` label sets."""
    problems: list[str] = []

    for job in sorted(requested):
        for row in requested[job]:
            if _row_is_answerable(snapshot, row):
                continue
            members = sorted(row)
            missing = [m for m in members if not pool_providers(snapshot, m)]
            detail = (
                f"no provider for {missing}"
                if missing
                else "each label has a provider individually, but NO single runner carries the "
                "whole set — `runs-on` is an AND over one runner"
            )
            problems.append(
                f"R1 {job} cannot be scheduled on labels {members} — {detail}. A row that no "
                f"runner answers does not fail: that job sits `pending` forever, and any "
                f"required check waiting on it never reports."
            )

    # R2 is judged per LABEL: it is about which ARCHITECTURE serves an x86_64 image.
    #
    # ⛔ The intersection goes through `_norm`, and it is not cosmetic. `every_label` holds
    # the SPELLING the workflow used, while `X86_IMAGE_LABELS` is lowercase, so a raw `&`
    # silently drops `runs-on: Ubuntu-Latest`: R2 is then never evaluated for it, while R1
    # answers the very same row case-insensitively (`_pool_answers`) — so the row counts as
    # served and the 2026-11-04 state (an x86_64 image claimed only by ARM hosts) reports
    # GREEN. GitHub matches labels case-insensitively everywhere else, so the spelling is
    # not something a workflow author can be expected to get exactly right.
    every_label = {label for rows in requested.values() for row in rows for label in row}
    x86_wanted = {_norm(x) for x in X86_IMAGE_LABELS}
    # One entry per NORMALISED image, holding the tree's own spelling for the message; a
    # tree that spells one image two ways must still yield one verdict, deterministically.
    x86_requested: dict[str, str] = {}
    for label in sorted(every_label):
        if _norm(label) in x86_wanted:
            x86_requested.setdefault(_norm(label), label)
    for image in sorted(x86_requested):
        label = x86_requested[image]
        providers = pool_providers(snapshot, label)
        if not providers:
            continue  # the pool does not claim it; hosted serves it. Not R2's business.
        if any(_norm(X64_HOST_LABEL) in {_norm(x) for x in p.get("labels", [])} for p in providers):
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
