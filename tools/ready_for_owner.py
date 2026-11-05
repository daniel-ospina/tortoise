"""Condition 2 of the READY-FOR-OWNER gate — "are all REQUIRED checks green?".

WHY THIS FILE EXISTS
    A lane decides "is this PR ready?" by asking whether every **required** check
    is green. The canonical source of that set is the branch-protection API:

        gh api repos/<owner>/<repo>/branches/main/protection \\
          --jq '.required_status_checks.contexts[]'

    That read needs ADMIN permission on the repository. A **fine-grained PAT**
    without the *Administration* repository permission gets

        HTTP 403 {"message":"Resource not accessible by personal access token"}

    (measured 2026-11-04 on this box, `github_pat_11ATOIH...`), so the lane
    credential cannot answer the first question a readiness gate asks. Lanes then
    improvise differently — counting ALL checks, counting only the ones that
    reported, or treating `cancelled` as a pass — and their verdicts stop
    agreeing with each other and with the merge rail.

    ⚠️ The failure is **CREDENTIAL-dependent, not repo-wide**: a user OAuth token
    with `repo` scope reads the same endpoint fine on the same box. This repo has
    no rulesets (`/rulesets` and `/rules/branches/main` both return `[]`), so the
    config below is the COMPLETE source, not merely a proxy.

THE FIX — derive the set from the REPO, never the API
    `.mergify.yml` already states the contract:

        live required contexts == queue_conditions union merge_conditions (checks),
        each required check named in EXACTLY ONE of the two lists

    so the required set is a pure function of a tracked file. Resolution order:

      1. `.mergify.yml` derivation            (repo-owned, always available)
      2. `docs/ci/required-contexts.json`     (committed snapshot — see below)
      3. FAIL(unreadable)

    Both live in the tree, so condition 2 needs **no credential at all**. The
    protection API is never called here — calling it would reintroduce the exact
    failure this file exists to remove.

    ⚠️ The snapshot is used to REPORT the set, never to decide condition 2: it is
    a flat `required_contexts` list with no entry/merge split, so it cannot say
    which names are merge-time (and a guessed split would resurrect the false
    `FAIL(missing)` on `python-ci-gate`). An unreadable config is therefore an
    honest `FAIL(unreadable)`, never a verdict built on a guessed split.

REUSED, NOT REIMPLEMENTED
    The parser is `tools/mergify_config_guard.py` (`_load_mergify`,
    `_success_names`, `GuardUnreadable`) — the same code the CI guard uses for its
    I1/I3 clauses. A second parser here would be a second answer to the same
    question, and the two would drift. A test cross-checks this module's answer
    against the pre-existing reader `.github/scripts/check-required-set.py`.

HARD RULES ENCODED
    * only a POSITIVE `check-success=<name>` is a requirement; a negated one
      (`-check-success=X`) is the OPPOSITE and never satisfies it.
    * a name in BOTH lists is a CONFIGURATION ERROR (contract clause 1), reported
      as `FAIL(config)` — never silently de-duplicated, and never "repaired" by
      falling back to the snapshot.
    * `check-success` is strict: a skipped or neutral check must NOT read as a
      pass (contract clause 3). Only `success` is green.
    * in-flight is **not yet ready**, never a pass; `cancelled`, `null`, an
      undocumented conclusion and a missing check-run are all NOT green.
    * non-check conditions (`base=main`, `-draft`, `-label=superseded-by-fold`)
      are excluded by the `check-` PREFIX, never by deleting lines from the file
      — deleting them would re-open #3016 (see `.mergify.yml`'s own warning).

Exit codes report **condition 2** — the only implemented condition:
`0` PASS · `1` FAIL · `2` FAIL(unreadable).
The report's `verdict` field reports the WHOLE gate and stays `NOT-READY` while
conditions 1/3/4 are stubs, so the exit status must not be derived from it (a
merged, fully-green PR would otherwise exit 1, i.e. "a required check is red").
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.mergify_config_guard import (  # noqa: E402
    KNOWN_INJECTION_MODES,
    MERGIFY_REL,
    RECORD_REL,
    GuardUnreadable,
    _effective_mode,
    _load_mergify,
    _success_names,
)

EXIT_PASS, EXIT_FAIL, EXIT_UNREADABLE = 0, 1, 2

# ── Polarity ────────────────────────────────────────────────────────────────
#
# `check-success=` has ONE satisfying conclusion. Everything else is not green —
# including the conclusions a naive reader treats as harmless (`neutral`,
# `skipped`) and the ones GitHub has not documented yet, which must never fall
# through a gap into a pass.
GREEN_CONCLUSIONS = frozenset({"success"})

# Verdict vocabulary. `PASS` is the ONLY non-failure; `DEFERRED` is the one
# non-failure that is also not a claim of greenness (see `evaluator_for`).
PASS = "PASS"
FAIL_RED = "FAIL(red)"
FAIL_PENDING = "FAIL(in-flight)"
FAIL_MISSING = "FAIL(missing)"
FAIL_CONFIG = "FAIL(config)"
FAIL_UNREADABLE = "FAIL(unreadable)"
FAIL_NOT_IMPLEMENTED = "FAIL(not-implemented)"

# A `merge_conditions` check absent at the PR head is NOT a failure: it is
# evaluated by Mergify on `mergify/merge-queue/<sha>`, not on the author head.
# That is the entire point of the two-step queue — the ~37 min heavy suite runs
# ONCE on the batch instead of on every PR head. Measured 2026-11-04: all six
# sampled open PR heads carried ZERO `python-ci-gate` runs. Treating its absence
# as FAIL would report NOT-READY for every PR forever — a false FAIL, and exactly
# the kind of divergence between verdicts this instrument exists to end.
DEFERRED = "DEFERRED(merge-time)"


class RequiredSetUnreadable(Exception):
    """Neither the config nor the committed snapshot yields a usable required set."""


class RequiredSetConfigError(Exception):
    """The config names a check in both lists — contract clause 1 violated."""


# ── Required-set resolution (no credential) ─────────────────────────────────


@dataclass(frozen=True)
class RequiredSet:
    names: tuple[str, ...]
    source: str
    queue_names: tuple[str, ...] = ()
    merge_names: tuple[str, ...] = ()
    snapshot_agrees: bool | None = None
    notes: tuple[str, ...] = ()
    # False only for a snapshot-only fallback, which cannot be evaluated: the
    # flat `required_contexts` list carries no entry/merge split.
    entry_merge_split: bool = True
    # `branch_protection_injection_mode` — LOAD-BEARING, never assumed. Under
    # `merge` (set here) the required contexts are injected for the MERGE only, so
    # `queue_conditions` IS the entry gate and a name in both lists is a config
    # error. Under `queue` (Mergify's default) they are injected for QUEUING AND
    # MERGING, so `merge_conditions` must be a SUBSET of `queue_conditions` — the
    # overlap is LEGAL and every context is an entry requirement, with nothing
    # deferred. A gate that hard-codes one mode disagrees with the guard about
    # what a legal config is; `.mergify.yml` itself says "Read the mode before
    # reasoning about the list".
    # None on the snapshot-only path: a datum must not assert a config read that
    # never happened, in a module whose own contract says the mode is never assumed.
    mode: str | None = None

    @property
    def overlap(self) -> tuple[str, ...]:
        return tuple(n for n in self.queue_names if n in set(self.merge_names))


def _snapshot_names(root: Path) -> tuple[str, ...] | None:
    """`required_contexts` from the committed record, or None if unreadable.

    Read with `json`, not the guard's YAML loader: this file is JSON and is the
    FALLBACK, so a non-JSON surprise here must degrade to "no snapshot", never
    to an exception that masks the primary path.
    """
    path = root / RECORD_REL
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    names = doc.get("required_contexts") if isinstance(doc, dict) else None
    if not isinstance(names, list) or not all(isinstance(n, str) and n for n in names):
        return None
    return tuple(names)


def derive_from_config(root: Path) -> RequiredSet:
    """The required set as `.mergify.yml` declares it — the PRIMARY source.

    Derives the union across EVERY queue rule (not just the first): a repository
    with two rules would otherwise silently drop the second rule's checks.
    """
    doc = _load_mergify(root)  # raises GuardUnreadable
    rules = doc["queue_rules"]
    queue_names: list[str] = []
    merge_names: list[str] = []
    modes: set[str] = set()
    for rule in rules:
        queue_names.extend(_success_names(rule.get("queue_conditions") or []))
        merge_names.extend(_success_names(rule.get("merge_conditions") or []))
        rule_mode = _effective_mode(rule)
        # Type-check BEFORE hashing: a container-typed mode is unhashable, so
        # `modes.add(...)` would raise TypeError and escape every handler as a
        # traceback — and the process then exits 1, which reads to a caller as
        # "a required check is red" instead of "I could not read the config".
        if not isinstance(rule_mode, str):
            raise RequiredSetUnreadable(
                f"{MERGIFY_REL}: queue rule {rule.get('name')!r} "
                f"branch_protection_injection_mode must be a string, "
                f"got {type(rule_mode).__name__}"
            )
        modes.add(rule_mode)

    # The injection mode decides what the entry gate IS, so it is READ, never
    # assumed. Two rules in one file with different modes cannot be reasoned
    # about as a single required set — refuse rather than pick one.
    if len(modes) != 1:
        raise RequiredSetUnreadable(
            f"{MERGIFY_REL}: queue rules disagree on branch_protection_injection_mode "
            f"({sorted(modes)}); the entry gate is mode-dependent and cannot be mixed"
        )
    mode = modes.pop()
    if mode not in KNOWN_INJECTION_MODES:
        raise RequiredSetUnreadable(
            f"{MERGIFY_REL}: unrecognised branch_protection_injection_mode {mode!r} "
            f"(known: {list(KNOWN_INJECTION_MODES)})"
        )

    # De-duplicate a name repeated in the SAME list (harmless) but NEVER a name
    # present in both lists (a config error, raised below).
    def _uniq(seq: list[str]) -> tuple[str, ...]:
        seen: set[str] = set()
        out: list[str] = []
        for n in seq:
            if n not in seen:
                seen.add(n)
                out.append(n)
        return tuple(out)

    q, m = _uniq(queue_names), _uniq(merge_names)
    overlap = tuple(n for n in q if n in set(m))
    # Only under `merge` is an overlap a CONFIGURATION ERROR (the contract's "named
    # in EXACTLY ONE list"). Under `queue` the guard REQUIRES
    # merge_conditions subset-of queue_conditions, so an overlap is the legal shape.
    if mode == "merge" and overlap:
        raise RequiredSetConfigError(
            "check(s) named in BOTH queue_conditions and merge_conditions: "
            + ", ".join(overlap)
            + " — under branch_protection_injection_mode=merge the configuration "
            "contract (clause 1) requires EXACTLY ONE list; a name in both is a "
            "configuration error, not a de-duplication problem"
        )

    union = tuple(dict.fromkeys(q + m))
    if not union:
        raise RequiredSetUnreadable(
            f"{MERGIFY_REL}: no positive `check-success=` condition in any queue rule"
        )

    snapshot = _snapshot_names(root)
    notes: list[str] = []
    agrees: bool | None = None
    if snapshot is None:
        notes.append(f"{RECORD_REL}: no usable committed snapshot")
    else:
        agrees = set(snapshot) == set(union)
        if not agrees:
            notes.append(
                f"{RECORD_REL} disagrees with {MERGIFY_REL}: snapshot-only="
                f"{sorted(set(snapshot) - set(union))} config-only="
                f"{sorted(set(union) - set(snapshot))}"
            )
    return RequiredSet(
        names=union,
        source=f"{MERGIFY_REL} (queue_conditions union merge_conditions)",
        queue_names=q,
        merge_names=m,
        snapshot_agrees=agrees,
        notes=tuple(notes),
        mode=mode,
    )


def resolve_required_set(root: Path) -> RequiredSet:
    """Config first, committed snapshot second, unreadable third — never an empty pass."""
    try:
        return derive_from_config(root)
    except RequiredSetConfigError:
        raise  # a config error is a verdict, not a reason to fall back
    except (GuardUnreadable, RequiredSetUnreadable) as primary:
        snapshot = _snapshot_names(root)
        if snapshot:
            return RequiredSet(
                names=snapshot,
                source=f"{RECORD_REL} (committed snapshot fallback)",
                notes=(f"{MERGIFY_REL} unreadable ({primary}); using the snapshot",),
                entry_merge_split=False,
            )
        raise RequiredSetUnreadable(
            f"required set unreadable from BOTH sources — {MERGIFY_REL}: {primary}; "
            f"{RECORD_REL}: absent or carrying no `required_contexts`"
        ) from None


# ── Check-run reading ───────────────────────────────────────────────────────


class RateLimited(RequiredSetUnreadable):
    """A genuine rate limit — distinct from a permissions denial.

    Only a message that SAYS rate limit qualifies. A bare `403` does not: the
    lane credential's protection denial is also a 403, and reporting that as
    "rate limited" sends the operator to chase a limit that does not exist — the
    exact misdiagnosis this file exists to remove, migrated one endpoint over.
    """

    def __init__(self, endpoint: str, reset_in: str):
        self.endpoint, self.reset_in = endpoint, reset_in
        super().__init__(f"GH-RATE-LIMITED({endpoint},{reset_in})")


def _gh_api_json(endpoint: str) -> dict:
    """`gh api --paginate`, merged across pages. Every failure is a verdict, never a traceback."""
    try:
        proc = subprocess.run(
            ["gh", "api", "--paginate", endpoint],
            capture_output=True, text=True, timeout=120,
        )
    except FileNotFoundError as exc:
        raise RequiredSetUnreadable(f"`gh` is not on PATH: {exc}") from None
    except subprocess.TimeoutExpired as exc:
        # The captured stderr is thrown away by default, which loses the cause —
        # observed live: a 120 s kill reported only "timed out" while gh's stderr
        # carried "API rate limit exceeded … shared rate-limit window". That is
        # the same misdiagnosis the RateLimited docstring exists to prevent.
        raw = exc.stderr or ""
        tail = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
        if "rate limit" in tail.lower():
            raise RateLimited(endpoint, "see gh api rate_limit") from None
        raise RequiredSetUnreadable(
            f"gh api {endpoint} timed out after 120s" + (f"; stderr: {tail.strip()[:300]}" if tail.strip() else "")
        ) from None
    except OSError as exc:
        raise RequiredSetUnreadable(f"gh api {endpoint} could not run: {exc}") from None

    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip()
        low = stderr.lower()
        if "rate limit" in low:
            raise RateLimited(endpoint, "see gh api rate_limit")
        # Anything else (permissions denial, 404, auth) is a read failure.
        raise RequiredSetUnreadable(f"gh api {endpoint} failed: {stderr[:400]}")

    merged: dict = {}
    for page in _split_pages(proc.stdout, endpoint):
        try:
            obj = json.loads(page)
        except ValueError as exc:
            raise RequiredSetUnreadable(f"gh api {endpoint}: unparseable page ({exc})") from None
        if not isinstance(obj, dict):
            raise RequiredSetUnreadable(
                f"gh api {endpoint}: page is not a JSON object (got {type(obj).__name__})"
            )
        for key, value in obj.items():
            if isinstance(value, list):
                merged.setdefault(key, []).extend(value)
            else:
                merged.setdefault(key, value)
    return merged


def _split_pages(stdout: str, endpoint: str = "") -> list[str]:
    """Split concatenated JSON objects (the `--paginate` output) safely.

    FAIL-CLOSED: an unterminated trailing object is a DROPPED PAGE, and a dropped
    page is exactly how a superseding red attempt disappears and a check reads
    green. So a truncated tail raises instead of being discarded.
    """
    chunks, depth, start, in_str, esc = [], 0, None, False, False
    for i, ch in enumerate(stdout):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start is not None:
                chunks.append(stdout[start : i + 1])
                start = None
    where = f" from {endpoint}" if endpoint else ""
    if depth != 0 or in_str:
        raise RequiredSetUnreadable(
            f"gh output{where} ended mid-object — a page was truncated (refusing to read a partial set)"
        )
    if not chunks:
        raise RequiredSetUnreadable(f"gh output{where} contained no JSON object")
    return chunks


def _run_id_of(run: dict) -> int | None:
    """The Actions run id embedded in `details_url`, if this run has one."""
    details = run.get("details_url")
    if not isinstance(details, str) or "/actions/runs/" not in details:
        return None
    try:
        return int(details.split("/actions/runs/")[1].split("/")[0])
    except (IndexError, ValueError):
        return None


def fetch_check_runs(repo: str, sha: str) -> list[dict]:
    """All check-runs for a commit, every attempt, every page.

    `filter=all` is pinned: the default is NOT all (measured on real commits it
    returned 26 of 40), and a missing attempt here reads as a missing check.
    """
    doc = _gh_api_json(f"repos/{repo}/commits/{sha}/check-runs?per_page=100&filter=all")
    runs = doc.get("check_runs")
    if not isinstance(runs, list):
        raise RequiredSetUnreadable(f"check-runs response for {sha} carried no `check_runs` list")
    # Validate the ELEMENTS, not just the container: a malformed payload must
    # become a verdict, never an AttributeError from `run.get(...)` downstream.
    if not all(isinstance(run, dict) for run in runs):
        raise RequiredSetUnreadable(f"check-runs response for {sha} carried a non-object entry")
    # A well-formed response can still be SHORT of its own `total_count` (a
    # pagination cap, a short read, a proxy). Defending only against unterminated
    # JSON is not enough: a dropped page is exactly how a superseding red attempt
    # disappears and a check reads green, and a short-but-valid body would sail
    # straight through as a PASS.
    total = doc.get("total_count")
    if not isinstance(total, int):
        # A guard that silently disables itself when its input is missing is
        # fail-open: an absent/rewritten `total_count` must not turn the ONLY
        # truncation defence off.
        raise RequiredSetUnreadable(
            f"check-runs response for {sha} carried no integer `total_count` — "
            "cannot prove the set is complete"
        )
    if len(runs) < total:
        raise RequiredSetUnreadable(
            f"check-runs truncated for {sha}: received {len(runs)} of {total} — refusing to read a partial set"
        )
    return runs


def fetch_workflow_ids(repo: str, sha: str) -> tuple[dict[int, int], str | None]:
    """`(run_id -> workflow_id, unavailable_reason)` for the runs on this commit.

    The workflow id is the STABLE identity of a workflow FILE: a re-run allocates
    a new run id but keeps the same workflow id (verified: #10895's
    `ai-review-gate` runs 41895779449 and 41898449197 both carry
    `workflow_id=346071631`). Grouping therefore needs it, because
      * keying on the RUN id splits a retry history and lets the OLDEST red win
        (a measured false red on the merged, fully-green #10895), while
      * keying on the NAME alone lets a newer green in one workflow mask a red in
        a sibling workflow that publishes the same JOB name.

    One call, not one per run. **The reason is returned, never swallowed**: this
    endpoint needs `Actions: read`, which the lane's fine-grained PAT has not been
    verified to hold, so a failure here is EXPECTED to be possible. A silent `{}`
    would revert the retry fix and turn #10895 into a confident false red with
    nothing in the report saying why — so the caller must be able to degrade to an
    honest `FAIL(unreadable)` instead.
    """
    try:
        doc = _gh_api_json(f"repos/{repo}/actions/runs?head_sha={sha}&per_page=100")
    except RequiredSetUnreadable as exc:
        return {}, f"workflow map unavailable ({exc})"
    runs = doc.get("workflow_runs")
    if not isinstance(runs, list):
        return {}, "workflow map unavailable (response carried no `workflow_runs` list)"
    out: dict[int, int] = {}
    for run in runs:
        if not isinstance(run, dict):
            continue
        rid, wid = run.get("id"), run.get("workflow_id")
        if isinstance(rid, int) and isinstance(wid, int):
            out[rid] = wid
    return out, None


# ── Evaluation ──────────────────────────────────────────────────────────────


@dataclass
class ContextResult:
    name: str
    verdict: str
    status: str | None
    conclusion: str | None
    attempts: int
    detail: str = ""


@dataclass
class ConditionResult:
    verdict: str
    rows: list[ContextResult] = field(default_factory=list)
    required: RequiredSet | None = None
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.verdict == PASS


def _newest_per_group(
    runs: list[dict], workflow_of: dict[int, int] | None = None
) -> dict[str, list[dict]]:
    """`name -> [newest current attempt, one per (publishing app, workflow)]`.

    The identity is `(app.slug, workflow_id, name)`:
      * `workflow_id` collapses RE-RUNS (same workflow, new run id) so the newest
        attempt decides — a re-run is a retry, not a second check;
      * `app.slug` + a distinct `workflow_id` keeps two DIFFERENT workflows that
        publish the same job name separate, so neither can mask the other.
    When the workflow map is unavailable the run id stands in, which is stricter
    (it never merges two attempts) and so can only over-report red.

    The newest attempt within a group is chosen by `id`: `started_at` is nullable
    for a queued attempt, and `latest` is a per-suite field that does not
    distinguish two attempts of one check.
    """
    workflow_of = workflow_of or {}
    groups: dict[tuple, dict] = {}
    for run in runs:
        name = run.get("name")
        if not isinstance(name, str) or not name:
            continue
        app = (run.get("app") or {}).get("slug") if isinstance(run.get("app"), dict) else None
        run_id = _run_id_of(run)
        workflow = workflow_of.get(run_id) if run_id is not None else None
        identity = workflow if workflow is not None else run_id
        key = (app, identity if identity is not None else id(run), name)
        current = groups.get(key)
        if current is None:
            groups[key] = run
            continue
        cur_id, new_id = current.get("id"), run.get("id")
        # A non-int id cannot be ORDERED. Silently keeping the first attempt would
        # be a guess — and the guess decides whether a retry's newest (possibly
        # green) attempt or its older (possibly red) one is read.
        if not isinstance(cur_id, int) or not isinstance(new_id, int):
            raise RequiredSetUnreadable(
                f"check-run {name!r} carries a non-integer id ({cur_id!r}/{new_id!r}) — "
                "attempts cannot be ordered"
            )
        if new_id > cur_id:
            groups[key] = run

    by_name: dict[str, list[dict]] = {}
    for (_app, _wf, name), run in groups.items():
        by_name.setdefault(name, []).append(run)
    return by_name


def evaluator_for(
    required: RequiredSet,
    runs: list[dict],
    workflow_of: dict[int, int] | None = None,
    workflow_error: str | None = None,
) -> ConditionResult:
    """Green-ness of the required set, split by WHERE each check is evaluated.

    `queue_conditions` names gate ENTRY and are evaluated against the PR head, so
    each one must be green HERE. `merge_conditions` names gate the MERGE and are
    re-evaluated on the queue branch, so at the head they are DEFERRED: absent is
    expected and must not fail the gate, while a present-and-red one still fails
    (a red heavy suite at the head is a real signal worth stopping on).

    AMBIGUITY IS NOT A VERDICT. When the workflow map is unavailable, a required
    name carrying more than one attempt is undecidable — it might be a retry (the
    newest wins) or two sibling workflows (red wins). Guessing either way is a
    wrong answer, so it is reported as `FAIL(unreadable)` naming the cause. A name
    with a SINGLE attempt is always exact under either keying, so it proceeds.
    """
    if not required.entry_merge_split:
        return ConditionResult(
            verdict=FAIL_UNREADABLE,
            required=required,
            detail=(
                "cannot evaluate: the required set came from the committed snapshot, "
                "which is a flat list with no entry/merge split — refusing to guess "
                "which checks are merge-time (a wrong guess re-creates the false "
                "FAIL(missing) on python-ci-gate)"
            ),
        )

    workflow_of = workflow_of or {}
    ambiguous = _ambiguous_names(runs, workflow_of, required)
    if ambiguous:
        reason = workflow_error or "attempts not present in the workflow map"
        return ConditionResult(
            verdict=FAIL_UNREADABLE,
            required=required,
            detail=(
                "cannot decide " + ", ".join(sorted(ambiguous))
                + " — each has more than one attempt whose workflow identity could "
                "not be resolved, so a retry (newest wins) is indistinguishable from "
                "two sibling workflows (red wins): " + reason
            ),
        )

    try:
        by_name = _newest_per_group(runs, workflow_of)
    except RequiredSetUnreadable as exc:
        # `evaluator_for` returns a verdict on every path; a malformed payload is
        # a verdict, never an exception that escapes to the caller.
        return ConditionResult(verdict=FAIL_UNREADABLE, required=required, detail=str(exc))
    rows: list[ContextResult] = []

    if required.mode == "queue":
        # Injected for QUEUING AND MERGING: every required context gates ENTRY, so
        # every one is evaluated at the head and NOTHING is deferred (a `queue`
        # config lists the heavy check in queue_conditions too, by the guard's own
        # clause ii, which is why the two lists overlap there).
        for name in required.names:
            rows.append(_evaluate_at_head(name, by_name.get(name) or [], runs))
    else:
        for name in required.queue_names:
            rows.append(_evaluate_at_head(name, by_name.get(name) or [], runs))

        for name in required.merge_names:
            attempts = by_name.get(name) or []
            if not attempts:
                rows.append(ContextResult(
                    name, DEFERRED, None, None, 0,
                    "merge_conditions: evaluated on the queue branch, not at the PR head",
                ))
            else:
                rows.append(_evaluate_at_head(name, attempts, runs))

    detail = ""
    reds = [r for r in rows if r.verdict == FAIL_RED]
    pending = [r for r in rows if r.verdict == FAIL_PENDING]
    missing = [r for r in rows if r.verdict == FAIL_MISSING]
    if reds:
        verdict = FAIL_RED
    elif pending:
        verdict = FAIL_PENDING
    elif missing:
        verdict = FAIL_MISSING
    elif rows and all(r.verdict in (PASS, DEFERRED) for r in rows):
        # ⛔ `DEFERRED` is the one non-failure that is NOT a claim of greenness, so a
        # row set in which NOTHING was decided at the head is not a pass — it means
        # condition 2 was never evaluated. Reachable from configuration under
        # `mode=merge`: a `queue_conditions` list carrying no positive
        # `check-success=` is an EMPTY ENTRY GATE (every PR enters unconditionally),
        # and `derive_from_config` refuses only an EMPTY UNION — so with one merge-time
        # check named, the union is non-empty, every row is DEFERRED, and this arm
        # returned PASS for a head whose check-runs had never been read. Under
        # `mode=queue` it cannot arise: every required name is evaluated at the head.
        if any(r.verdict != DEFERRED for r in rows):
            verdict = PASS
        else:
            verdict = FAIL_UNREADABLE
            detail = (
                "cannot evaluate: not one required check was decided at the head "
                "(every row is DEFERRED) — an empty entry gate means a PASS would "
                "be a claim about nothing"
            )
    else:
        verdict = FAIL_UNREADABLE
    return ConditionResult(verdict=verdict, rows=rows, required=required, detail=detail)


def _ambiguous_names(
    runs: list[dict], workflow_of: dict[int, int], required: RequiredSet
) -> set[str]:
    """Required names whose attempts cannot be grouped by workflow identity.

    Only names with MORE THAN ONE attempt can be ambiguous: one attempt is the
    whole history, so it resolves exactly under either keying.
    """
    by_name: dict[str, list[dict]] = {}
    for run in runs:
        if not isinstance(run, dict):
            continue
        name = run.get("name")
        if isinstance(name, str) and name:
            by_name.setdefault(name, []).append(run)

    out: set[str] = set()
    for name in required.names:
        attempts = by_name.get(name) or []
        if len(attempts) <= 1:
            continue
        for run in attempts:
            rid = _run_id_of(run)
            if rid is None or rid not in workflow_of:
                out.add(name)
                break
    return out


def _evaluate_at_head(name: str, attempts: list[dict], all_runs: list[dict]) -> ContextResult:
    """One required name, evaluated strictly against the head."""
    total = sum(1 for r in all_runs if r.get("name") == name)
    if not attempts:
        return ContextResult(
            name, FAIL_MISSING, None, None, total,
            "no check-run at this head",
        )

    statuses = [(s.lower() if isinstance(s := r.get("status"), str) else "") for r in attempts]
    # A non-string `conclusion` is NOT green: normalise it to None so it reds
    # rather than raising `TypeError: unhashable type` on the set membership test.
    conclusions = [(c if isinstance(c := r.get("conclusion"), str) else None) for r in attempts]

    # RED wins over in-flight: a completed non-success is final and nothing
    # still running can un-fail it. This also covers `cancelled`, `neutral`,
    # `skipped`, `timed_out`, `action_required`, `stale`, an undocumented
    # conclusion and a completed run whose conclusion is null.
    reds = [
        (s, c) for s, c in zip(statuses, conclusions)
        if s == "completed" and (c not in GREEN_CONCLUSIONS)
    ]
    if reds:
        detail = ", ".join(f"{s}/{c if c is not None else 'null'}" for s, c in reds)
        return ContextResult(
            name, FAIL_RED, reds[0][0], reds[0][1], total,
            f"not `success`: {detail}",
        )

    if any(s != "completed" for s in statuses):
        pending = [s for s in statuses if s != "completed"]
        return ContextResult(
            name, FAIL_PENDING, pending[0], None, total,
            f"in flight ({pending[0]}) — not yet ready",
        )

    return ContextResult(name, PASS, "completed", "success", total, "success")


def condition_2(root: Path, repo: str, sha: str) -> ConditionResult:
    """Condition 2 in one call: resolve the set, read the head, decide."""
    try:
        required = resolve_required_set(root)
    except RequiredSetConfigError as exc:
        return ConditionResult(verdict=FAIL_CONFIG, detail=str(exc))
    except RequiredSetUnreadable as exc:
        return ConditionResult(verdict=FAIL_UNREADABLE, detail=str(exc))
    try:
        runs = fetch_check_runs(repo, sha)
        workflow_of, workflow_error = fetch_workflow_ids(repo, sha)
        # Kept INSIDE the try: a malformed payload reaching the evaluator must
        # still become a verdict, never a traceback.
        result = evaluator_for(required, runs, workflow_of, workflow_error)
    except RequiredSetUnreadable as exc:  # includes RateLimited
        return ConditionResult(verdict=FAIL_UNREADABLE, required=required, detail=str(exc))
    if workflow_error:
        # Attached UNCONDITIONALLY: a PASS computed without a workflow identity is
        # a PASS that assumed one attempt per name, and a reader must be able to
        # tell it apart from a fully-informed PASS.
        result.required = _with_note(required, workflow_error)
    return result


def _with_note(required: RequiredSet, note: str) -> RequiredSet:
    """A copy carrying an extra note, so a degraded read is visible in the report."""
    return RequiredSet(
        names=required.names,
        source=required.source,
        queue_names=required.queue_names,
        merge_names=required.merge_names,
        snapshot_agrees=required.snapshot_agrees,
        notes=required.notes + (note,),
        entry_merge_split=required.entry_merge_split,
        mode=required.mode,
    )


# ── The READY-FOR-OWNER gate (condition 2 real, the rest honest stubs) ──────

CONDITION_NAMES = {
    1: "the PR's diff is non-empty and touches no protected path",
    2: "every REQUIRED check is green at the head",
    3: "a review record exists and is bound to the live head",
    4: "no merge conflict with the base",
}


def gate(pr: int, repo: str, head_sha: str, root: Path) -> dict:
    """All conditions; only condition 2 is implemented.

    An honest `FAIL(not-implemented)` beats a wrong `PASS` — a stub that guesses
    is how a readiness verdict stops meaning anything.
    """
    c2 = condition_2(root, repo, head_sha)
    conditions = []
    for number in sorted(CONDITION_NAMES):
        if number == 2:
            conditions.append({
                "condition": number,
                "description": CONDITION_NAMES[number],
                "verdict": c2.verdict,
                "detail": c2.detail,
                "rows": [r.__dict__ for r in c2.rows],
            })
        else:
            conditions.append({
                "condition": number,
                "description": CONDITION_NAMES[number],
                "verdict": FAIL_NOT_IMPLEMENTED,
                "detail": "not implemented by this tool",
                "rows": [],
            })
    decided = "READY" if all(c["verdict"] == PASS for c in conditions) else "NOT-READY"
    return {
        "pr": pr,
        "repo": repo,
        "head_sha": head_sha,
        "condition_2_verdict": c2.verdict,
        "required_set": None if c2.required is None else {
            "names": list(c2.required.names),
            "source": c2.required.source,
            "queue_names": list(c2.required.queue_names),
            "merge_names": list(c2.required.merge_names),
            "overlap": list(c2.required.overlap),
            "snapshot_agrees": c2.required.snapshot_agrees,
            "notes": list(c2.required.notes),
            "entry_merge_split": c2.required.entry_merge_split,
            "injection_mode": c2.required.mode,
        },
        "conditions": conditions,
        "verdict": decided,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="READY-FOR-OWNER gate (condition 2 real)")
    ap.add_argument("--pr", type=int, action="append", default=[], help="PR number (repeatable)")
    ap.add_argument("--sha", action="append", default=[],
                    help="head sha (repeatable; give one for EVERY --pr, or none)")
    ap.add_argument("--repo", default="daniel-ospina/tortoise")
    ap.add_argument("--root", default=".")
    ap.add_argument("--required-set", action="store_true", help="print the derived set and exit")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    root = Path(args.root).resolve()

    if args.required_set:
        def _json_set(rs: "RequiredSet | None", *, verdict: str, error: str | None) -> None:
            """`--json` must be JSON on EVERY path, including the failures."""
            print(json.dumps({
                "names": list(rs.names) if rs else [],
                "source": rs.source if rs else None,
                "queue_names": list(rs.queue_names) if rs else [],
                "merge_names": list(rs.merge_names) if rs else [],
                "overlap": list(rs.overlap) if rs else [],
                "injection_mode": rs.mode if rs else None,
                "snapshot_agrees": rs.snapshot_agrees if rs else None,
                "notes": list(rs.notes) if rs else [],
                "entry_merge_split": rs.entry_merge_split if rs else False,
                "evaluable": bool(rs.entry_merge_split) if rs else False,
                "verdict": verdict,
                "error": error,
            }, indent=2))

        try:
            rs = resolve_required_set(root)
        except RequiredSetConfigError as exc:
            if args.json:
                _json_set(None, verdict=FAIL_CONFIG, error=str(exc))
            else:
                print(f"{FAIL_CONFIG}: {exc}")
            return EXIT_FAIL
        except RequiredSetUnreadable as exc:
            if args.json:
                _json_set(None, verdict=FAIL_UNREADABLE, error=str(exc))
            else:
                print(f"{FAIL_UNREADABLE}: {exc}")
            return EXIT_UNREADABLE
        if args.json:
            # `--json` promises machine-readable stdout; a human report here
            # breaks that for any consumer that adds the flag uniformly.
            print(json.dumps({
                "names": list(rs.names),
                "source": rs.source,
                "queue_names": list(rs.queue_names),
                "merge_names": list(rs.merge_names),
                "overlap": list(rs.overlap),
                "injection_mode": rs.mode,
                "snapshot_agrees": rs.snapshot_agrees,
                "notes": list(rs.notes),
                "entry_merge_split": rs.entry_merge_split,
                "evaluable": rs.entry_merge_split,
                "verdict": PASS if rs.entry_merge_split else FAIL_UNREADABLE,
                "error": None,
            }, indent=2))
            return EXIT_PASS if rs.entry_merge_split else EXIT_UNREADABLE
        print(f"source: {rs.source}")
        print(f"queue_conditions: {', '.join(rs.queue_names) or '(none)'}")
        print(f"merge_conditions: {', '.join(rs.merge_names) or '(none)'}")
        print(f"OVERLAP: {', '.join(rs.overlap) or '(none — contract satisfied)'}")
        print(f"union ({len(rs.names)}): {', '.join(rs.names)}")
        print(
            "injection_mode: " + (rs.mode if rs.mode is not None
                                 else "(not read — config unreadable)")
        )
        print(f"snapshot_agrees: {rs.snapshot_agrees}")
        for note in rs.notes:
            print(f"note: {note}")
        if not rs.entry_merge_split:
            # Reporting the set is useful; claiming it resolved is not. The set
            # came from the flat snapshot, so condition 2 cannot be evaluated and
            # a scripted consumer must not read exit 0 as "the set resolved".
            print("cannot evaluate condition 2: this set has no entry/merge split")
            return EXIT_UNREADABLE
        return EXIT_PASS

    if not args.pr:
        ap.error("--pr is required unless --required-set is given")
    # A partial --sha list would pair by index and evaluate PR A at PR B's head —
    # a confidently wrong verdict about a tree nobody asked about.
    if args.sha and len(args.sha) != len(args.pr):
        ap.error("--sha must be given for every --pr, or for none")

    codes = [EXIT_PASS]
    reports = []
    failures = []
    for index, pr in enumerate(args.pr):
        try:
            sha = args.sha[index] if args.sha else _resolve_head(repo=args.repo, pr=pr)
            report = gate(pr, args.repo, sha, root)
        except RequiredSetUnreadable as exc:  # includes RateLimited
            message = str(exc) if isinstance(exc, RateLimited) else f"{FAIL_UNREADABLE}: #{pr}: {exc}"
            # A machine consumer of `--json` must be able to PARSE stdout, and an
            # unreadable PR must not silently vanish from `reports` (an absence
            # read as a verdict). So the message goes to stderr and the PR lands
            # in the JSON as an explicit unreadable record.
            if args.json:
                print(message, file=sys.stderr)
            else:
                print(message)
            failures.append({
                "pr": pr,
                "repo": args.repo,
                "head_sha": None,
                "condition_2_verdict": FAIL_UNREADABLE,
                "detail": str(exc),
                "conditions": [],
                "verdict": "NOT-READY",
            })
            codes.append(EXIT_UNREADABLE)
            continue
        reports.append(report)
        if not args.json:
            _print_report(report)
        # EXIT STATUS REPORTS CONDITION 2 — the only implemented condition. The
        # report's own `verdict` field reports the WHOLE gate and stays NOT-READY
        # while conditions 1/3/4 are stubs, so deriving the exit code from it
        # would make a merged, fully-green PR exit 1 ("a required check is red")
        # — the very readiness/rail divergence this instrument exists to remove.
        c2 = report.get("condition_2_verdict")
        if c2 == FAIL_UNREADABLE:
            codes.append(EXIT_UNREADABLE)
        elif c2 != PASS:
            codes.append(EXIT_FAIL)

    if args.json:
        print(json.dumps(reports + failures, indent=2))

    return EXIT_UNREADABLE if EXIT_UNREADABLE in codes else max(codes)


def _resolve_head(repo: str, pr: int) -> str:
    try:
        proc = subprocess.run(
            ["gh", "pr", "view", str(pr), "--repo", repo, "--json", "headRefOid", "--jq", ".headRefOid"],
            capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RequiredSetUnreadable(f"cannot resolve head for #{pr}: {exc}") from None
    if proc.returncode != 0:
        raise RequiredSetUnreadable(f"cannot resolve head for #{pr}: {(proc.stderr or '').strip()[:300]}")
    return proc.stdout.strip()


def _print_report(report: dict) -> None:
    rs = report.get("required_set")
    print(f"\n=== PR #{report['pr']} @ {report['head_sha'][:12]} — {report['verdict']} ===")
    if rs:
        print(f"  required set from {rs['source']}")
        print(f"  union ({len(rs['names'])}): {', '.join(rs['names'])}")
        if rs["overlap"]:
            print(f"  ⛔ OVERLAP (config error): {', '.join(rs['overlap'])}")
    c2 = next((c for c in report["conditions"] if c["condition"] == 2), None)
    if c2:
        print(f"  condition 2: {c2['verdict']}")
        if c2["detail"]:
            print(f"    {c2['detail']}")
        for row in c2["rows"]:
            print(f"    {row['name']:<22} {row['verdict']:<20} "
                  f"status={row['status']} conclusion={row['conclusion']} "
                  f"runs={row['attempts']}" + (f" — {row['detail']}" if row["detail"] else ""))


if __name__ == "__main__":
    raise SystemExit(main())
