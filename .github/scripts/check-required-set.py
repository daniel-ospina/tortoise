#!/usr/bin/env python3
"""Required-set sync guard (#6144): the required check list, the queue's lists,
and the declarative mirror must be ONE set — and every name in it must be
accounted for.

WHY THIS EXISTS
---------------
The same set of names is declared in three places, and until now **nothing
read two of them**:

  1. LIVE branch protection — `branches/main/protection` `.required_status_checks`.
     The source of truth. (The merge gate's `contexts`.)
  2. `.mergify.yml` `queue_rules[].queue_conditions` / `merge_conditions`.
     The merge queue's conditions. The file states the invariant in a COMMENT
     — *"each required check named in EXACTLY ONE of the two lists"* — and
     carries a human-maintained line: *"Last reconciled with the live required
     set: 2026-09-26"*. A comment cannot fail, and the date is only as good as
     its last editor.
  3. `.github/settings.yml` — a declarative mirror.

Both drift directions are defects, and they are NOT symmetric:

  * a required name in **NEITHER** mergify list is still enforced by Mergify's
    branch-protection injection, so the damage is that the file stops
    DESCRIBING reality (a maintenance trap, not an outage);
  * a name in **`merge_conditions`** that the queue branch never reports on
    **DEADLOCKS the queue for every PR** — the merge waits forever for a check
    that will never arrive;
  * a name in **`queue_conditions`** that no PR-triggered workflow produces is
    the same hazard at the ENTRY gate: the condition can never become true, so
    no PR can even enter the queue.

  Both buckets are evaluated against PR-like refs (the PR head for entry, the
  queue branch for the merge), so `check_deadlock` runs over BOTH.

WHY A GUARD AND NOT A TIDIER COMMENT
------------------------------------
This repo has already recorded the failure mode of changing the CI surface
without accounting for it: splitting the PR-tier surface **silently dropped the
#2656 manifest-drift gate plus eight others** (issue #6144, tortoise #2656).
The requirement is the one the issue itself writes down — the change is correct
only if *every guarantee is accounted for*, "an explicit enumeration with a
test, not a comment". This file is that enumeration; `tests/test_required_set_sync.py`
is that test.

WHAT IT DOES NOT DO
-------------------
It does not EDIT branch protection, and it does not propose a partition of the
test surface. Changing the required set is a branch-protection change and the
partition depends on #6139.

It READS live branch protection only under `--live`, which is a manual owner/rail
check and NOT part of CI: that read needs admin scope, which `GITHUB_TOKEN` does
not have. So the WIRED assertion is the offline one. A name added to or removed
from live protection ALONE — with no edit to `.mergify.yml` or the mirror — is
caught by `--live` only, and therefore by nobody automatically. Stated here so a
reader does not over-trust the CI step; the durable fix would be a scheduled job
authorised to read protection.

EXIT CODES
----------
0  every check passed
1  a violation (drift, mis-filing, unaccounted name, unproducible check)
2  could not measure (file missing/unparsable, nothing to compare, live read
   failed) — fail-closed: "nothing was compared" is never a pass.

   ONE NAMED EXCEPTION: an ABSENT `.github/settings.yml` is exit 0, not 2. The
   declarative mirror is OPTIONAL — a file that does not exist declares nothing
   to compare, and requiring it would red every repo that does not keep one.
   The moment the file EXISTS it must be usable: present-but-unparsable is 2,
   and present-but-declaring-nothing is a violation (1). "Missing" and "empty"
   are deliberately different verdicts; only the former is a skip.
"""

from __future__ import annotations

import argparse
import fnmatch
import functools
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml


@functools.lru_cache(maxsize=64)
def _read_yaml_cached(path_str: str, mtime_ns: int, size: int) -> Any:
    del mtime_ns, size  # cache-key material only
    try:
        text = Path(path_str).read_text()
    except (UnicodeDecodeError, OSError, MemoryError) as exc:
        # An undecodable/unreadable/too-large file is UNPARSABLE, so it must reach
        # the documented exit 2 with an annotation — not escape as a traceback with
        # exit 1 and no `::error::`. `read_text` allocates the WHOLE file before
        # the parser runs, so it needs its own MemoryError guard IN ADDITION to the
        # parser's below: a file that reads fine can still fail to PARSE into an
        # object graph that does not fit.
        raise CannotMeasure(
            f"{path_str}: unreadable ({type(exc).__name__}: {exc}) — cannot measure") from exc
    try:
        doc = yaml.safe_load(text)
    except RecursionError as exc:
        # A deeply-nested document blows the parser's stack. RecursionError is
        # neither YAMLError (caught by the callers) nor OSError (caught above), so
        # it escaped as an unannotated traceback at every seam.
        raise CannotMeasure(
            f"{path_str}: nesting too deep to parse — cannot measure") from exc
    except MemoryError as exc:
        # The READ guard above does not cover this: a document small enough to read
        # can still PARSE into an object graph that does not fit.
        raise CannotMeasure(
            f"{path_str}: too large to parse — cannot measure") from exc
    except ValueError as exc:
        # PyYAML raises ValueError for a value it RESOLVES but cannot BUILD: an
        # out-of-range timestamp (`x: 2001-02-31`) and, on 3.11+, an integer
        # literal past the digit limit. It is not a YAMLError, so the callers'
        # `except yaml.YAMLError` missed it and it escaped as an unannotated
        # traceback at every seam — offline, in CI.
        raise CannotMeasure(
            f"{path_str}: unparsable ({type(exc).__name__}: {exc}) — cannot measure") from exc
    # ONLY None (an empty document) becomes {}. The previous `or {}` coerced EVERY
    # falsy parse — so a top-level `[]`, `false` or `0` became `{}`, which then
    # reads as "the file declares nothing" and SKIPS its check. A fail-open whose
    # presence depended on the shape: `[a, b]` was caught, `[]` was not.
    return {} if doc is None else doc


def read_yaml(path: Path) -> Any:
    """Parse a YAML file, memoized on (path, mtime, size).

    `run()` parses `.github/workflows/python-ci.yml` (~2.2k lines) TWICE — once
    as one workflow among 26, once for the gate's `LEGS` table — and the tests
    call `run()` several times. Keyed on the file's identity, so a rewritten
    fixture is re-read rather than served stale. Callers must NOT mutate the
    result.
    """
    try:
        stat = path.stat()
    except OSError as exc:
        # The stat is itself a filesystem read: a BROKEN SYMLINK matching the
        # workflows glob raises FileNotFoundError HERE, outside
        # `_read_yaml_cached`'s guard, and would escape as a traceback.
        raise CannotMeasure(
            f"{path}: unreadable ({type(exc).__name__}: {exc}) — cannot measure") from exc
    return _read_yaml_cached(str(path), stat.st_mtime_ns, stat.st_size)

REPO_ROOT = Path(__file__).resolve().parents[2]

MERGIFY_PATH = Path(os.environ.get("MERGIFY_CONFIG", REPO_ROOT / ".mergify.yml"))
SETTINGS_PATH = Path(
    os.environ.get("BRANCH_PROTECTION_DECLARATION", REPO_ROOT / ".github" / "settings.yml")
)
WORKFLOWS_DIR = Path(os.environ.get("WORKFLOWS_DIR", REPO_ROOT / ".github" / "workflows"))
PYTHON_CI_PATH = Path(
    os.environ.get("PYTHON_CI_WORKFLOW", REPO_ROOT / ".github" / "workflows" / "python-ci.yml")
)
GATE_JOB = "python-ci-gate"
GATE_LEGS_HEREDOC = "<<'LEGS'"

CHECK_SUCCESS_PREFIX = "check-success="

# LITERAL spellings of a falsy `if:` that GitHub evaluates as false. See
# `_is_literally_off` — these are decidable, unlike a real expression. Compared
# AFTER `_normalise_if`, so the `${{ }}` wrapper and any inner whitespace are
# already gone: `${{false}}`, `${{ 0 }}` and `${{ null }}` all land here.
#
# The EMPTY STRING is the entry that matters most and is easiest to get wrong:
# `if: ''` and `if: ""` parse to `""` (zero characters), NOT to the two-character
# string `'""'`. Listing only the two-character forms left `if: ''` PRODUCIBLE —
# a false GREEN — while looking like the empty case was covered.
#
# The bare names of the constants are listed for the QUOTED spellings only
# (`if: "null"`), because the unquoted `if: null` arrives as `None` and is handled
# by `_is_literally_off` before this set is consulted.
_FALSY_IF_STRINGS = frozenset({
    "false", "0", "-0", "0.0", "null", "", '\"\"', "''",
})

# Sentinel: the `if:` KEY IS ABSENT. Distinct from the key being present with a
# null value, which PyYAML renders as the same `None` — `job.get("if")` cannot tell
# them apart, and an absent `if:` means the job RUNS while a falsy constant means it
# does not. Collapsing the two made the natural `if: null` spelling producible.
_IF_ABSENT = object()


def _normalise_if(condition: str) -> str:
    """A literal `if:` value reduced to its bare expression.

    `${{ false }}`, `${{false}}`, `${{ FALSE }}` and `false` are the SAME constant
    to GitHub, so they must normalise to the same string. Matching the raw text
    meant only the exact spellings in `_FALSY_IF_STRINGS` were recognised and
    `${{false}}` — one space away from a caught form — counted as PRODUCIBLE.
    """
    text = condition.strip()
    if text.startswith("${{") and text.endswith("}}"):
        text = text[3:-2].strip()
    return text.lower()

# ── THE ENUMERATION (design decision 2) ───────────────────────────────────────
# Every LIVE-required status check, HOW the merge queue enforces it, and the
# pre-merge guarantee it actually provides. Adding a name here without a
# matching `.mergify.yml` entry fails; adding one to `.mergify.yml` without an
# entry here fails. That is the whole anti-silent-drop property.
#
# `queue`    = gated at ENTRY, against the PR head.
# `merge`    = re-asserted at MERGE, against the queue branch — so the check MUST
#              be one the queue branch actually reports (see check_deadlock).
# `injected` = required on main but enforced at MERGE by Mergify's OWN
#              branch-protection injection (`branch_protection_injection_mode`),
#              so it is deliberately NOT named in either list. Naming an
#              `injected` name in `queue_conditions` would ALSO make it an ENTRY
#              gate — a queue BEHAVIOUR change, which is why the enumeration
#              records it instead of editing the queue. Two things keep the
#              category honest: the injected name must be PRODUCIBLE on a
#              PR-like ref (else injection deadlocks the merge — see
#              check_injected_producible), and the injection mode must actually
#              be `merge` (see check_injection_mode).
REQUIRED_SET: dict[str, tuple[str, str]] = {
    "pricing-artifact": (
        "queue",
        "the pricing artefact regenerates and matches the committed product/pricing.json",
    ),
    "docs": (
        "queue",
        "the docs index / link surface resolves",
    ),
    "test-isolation": (
        "queue",
        "tests do not leak state across each other (isolation contract)",
    ),
    "license-surface": (
        "queue",
        "the license surface is unmodified / correctly declared",
    ),
    "legal-e2e": (
        "queue",
        "the legal end-to-end surface still passes",
    ),
    "python-ci-gate": (
        "merge",
        "the whole Python CI aggregate: its `needs:` list is its entire claim "
        "(see the python-ci.yml block above the job and tests/test_ci_selection.py)",
    ),
    "ai-review-gate": (
        "injected",
        "the AI review gate. Required on `main` and enforced at the MERGE by "
        "Mergify's injected branch-protection conditions, and deliberately NOT "
        "named in `queue_conditions`: naming it there would make an LLM review an "
        "ENTRY gate for every PR, which is a queue behaviour change and an owner "
        "decision, not a sync fix. Live protection lists SEVEN contexts and this "
        "is the seventh (#6144)",
    ),
}


class CannotMeasure(Exception):
    """Raised when a surface cannot be read — never silently treated as a pass."""


def require_mapping(value: Any, what: str) -> dict[str, Any]:
    """A parsed YAML value that must be a mapping. Unusable -> exit 2.

    Centralised because patching these one call site at a time kept leaving a
    sibling unguarded, and an unguarded `.get`/`.items()` surfaces as an
    AttributeError traceback with a NON-2 exit and no `::error::` annotation —
    which reads like a violation but names nothing.
    """
    if not isinstance(value, dict):
        raise CannotMeasure(
            f"{what} must be a mapping, got {type(value).__name__} — cannot measure")
    return value


def require_list(value: Any, what: str) -> list[Any]:
    """A parsed YAML value that must be a LIST. Unusable -> exit 2.

    A bare string is REFUSED rather than iterated: `contexts: docs` would
    otherwise silently become {'d','o','c','s'} — a wrong answer that looks fine.
    """
    if not isinstance(value, list):
        raise CannotMeasure(
            f"{what} must be a list, got {type(value).__name__} — cannot measure")
    return value


# ── parsing ───────────────────────────────────────────────────────────────────


def load_mergify(path: Path | None = None) -> dict[str, set[str]]:
    """Return {'queue': {...}, 'merge': {...}} from `.mergify.yml` check-success= conditions."""
    # Resolve at CALL time, not def time: a default argument would freeze the
    # env seam at import and ignore both the env and a monkeypatch.
    path = path or MERGIFY_PATH
    try:
        exists = path.exists()
    except OSError as exc:
        # `Path.exists()` re-raises EACCES on 3.12; it only swallows
        # ENOENT/ENOTDIR/EBADF/ELOOP. Unguarded, this FIRST filesystem touch in
        # `run()` escaped as a traceback (exit 1, no annotation).
        raise CannotMeasure(
            f"{path}: unreadable ({type(exc).__name__}: {exc}) — cannot measure") from exc
    if not exists:
        raise CannotMeasure(f"mergify config not found: {path}")
    try:
        cfg = read_yaml(path)
    except yaml.YAMLError as exc:
        raise CannotMeasure(f"mergify config unparsable: {path}: {exc}") from exc
    if not isinstance(cfg, dict):
        # A top-level LIST/scalar is unusable, and an unguarded `.get` here would
        # surface as an AttributeError traceback with a NON-2 exit and no
        # `::error::` annotation — contradicting this module's exit contract.
        raise CannotMeasure(
            f"{path}: top level is not a mapping, got {type(cfg).__name__} — cannot measure")

    rules = cfg.get("queue_rules")
    if not isinstance(rules, list) or not rules:
        raise CannotMeasure(f"{path}: no queue_rules — nothing to compare is not a pass")

    out: dict[str, set[str]] = {"queue": set(), "merge": set()}
    for index, rule in enumerate(rules):
        require_mapping(rule, f"{path}: queue_rules[{index}]")
        for key, bucket in (("queue_conditions", "queue"), ("merge_conditions", "merge")):
            conds = rule.get(key)
            if conds is None:
                continue
            for cond in require_list(conds, f"{path}: queue_rules[{index}].{key}"):
                if not isinstance(cond, str):
                    # A non-string condition used to reach `.startswith` ->
                    # AttributeError (exit 1, no annotation).
                    raise CannotMeasure(
                        f"{path}: queue_rules[{index}].{key} entries must be strings, "
                        f"got {type(cond).__name__} — cannot measure")
                if cond.startswith(CHECK_SUCCESS_PREFIX):
                    out[bucket].add(cond[len(CHECK_SUCCESS_PREFIX) :])

    if not out["queue"] and not out["merge"]:
        raise CannotMeasure(
            f"{path}: no {CHECK_SUCCESS_PREFIX}* conditions in any queue rule — "
            "nothing to compare is not a pass"
        )
    return out


def declared_lists() -> tuple[set[str], set[str]]:
    """The two NAMED-IN-.mergify.yml buckets, from the enumeration above."""
    queue = {n for n, (where, _) in REQUIRED_SET.items() if where == "queue"}
    merge = {n for n, (where, _) in REQUIRED_SET.items() if where == "merge"}
    return queue, merge


def injected_names() -> set[str]:
    """The enumeration's `injected` bucket: live-required, enforced BY INJECTION.

    Kept out of `declared_lists()` on purpose — that function is compared against
    `.mergify.yml`'s actual `*_conditions` lists, and an `injected` name is by
    definition in NEITHER, so folding it in would make every comparison report a
    spurious "dropped condition".
    """
    return {n for n, (where, _) in REQUIRED_SET.items() if where == "injected"}


def read_injection_modes(path: Path | None = None) -> list[Any]:
    """`branch_protection_injection_mode` for EVERY queue rule, in order.

    ALL rules, not just the first: `queue_rules` is a LIST and each rule injects
    independently, so a second rule left at the default `queue` would still gate
    ENTRY with the injected contexts while the first said `merge`. Reading only the
    first rule would report a clean verdict for a claim that is false of the second
    — the failure this catches is a two-rule config where only one is checked.

    An ABSENT key comes back as None (Mergify's default is `queue`); a missing key
    inside a non-dict rule does too, since such a rule cannot be read either.

    A `queue_rules` that is PRESENT but not a list raises `CannotMeasure` rather
    than returning an empty list. Returning `[]` reported "no mode was declared"
    for a value that is not a declaration at all: `check_injection_mode([])` then
    reported a violation (exit 1) for a config whose real defect is that it could
    not be READ, and a direct caller comparing modes could read the empty list as
    success. "Present but unusable" is exit 2, which is the distinction this module
    draws everywhere else.
    """
    path = path or MERGIFY_PATH
    try:
        cfg = read_yaml(path)
    except yaml.YAMLError as exc:
        raise CannotMeasure(f"mergify config unparsable: {path}: {exc}") from exc
    if not isinstance(cfg, dict):
        raise CannotMeasure(
            f"{path}: top level is not a mapping, got {type(cfg).__name__} — cannot measure")
    rules = cfg.get("queue_rules")
    if not isinstance(rules, list):
        # Not "present but not a list": an ABSENT key reaches here too and arrives
        # as None (`cfg.get` cannot tell absent from explicit-null), so wording it
        # "present" made the message itself collapse the distinction this branch
        # exists to draw. The type name is the part that is always true.
        raise CannotMeasure(
            f"{path}: `queue_rules` is absent or not a list, got "
            f"{type(rules).__name__} — cannot measure")
    return [
        rule.get("branch_protection_injection_mode") if isinstance(rule, dict) else None
        for rule in rules
    ]


def _triggers(workflow: dict[str, Any]) -> set[str]:
    """Workflow trigger names. PyYAML resolves a bare `on:` key to the BOOLEAN True.

    There is no `if raw is None` early exit: the `isinstance` chain falls through
    to the same empty set, so the branch was REDUNDANT. (Redundant, not
    untestable — `("", False)` in the trigger test exercises exactly that
    fallthrough.) It was removed to keep one expression of the behaviour.
    """
    raw = workflow.get("on", workflow.get(True))
    if isinstance(raw, str):
        return {raw}
    if isinstance(raw, list):
        return {str(x) for x in raw}
    if isinstance(raw, dict):
        return {str(k) for k in raw}
    return set()


# A POSITIVE comparison against any NON-PR event is exactly as decisive as
# `== 'push'`: a job gated `github.event_name == 'schedule'` can no more report on
# a PR ref than a push-gated one. Matching only `push` left `schedule`,
# `workflow_dispatch`, `issues`, ... classified PRODUCIBLE — a false GREEN in the
# deadlock check, the one direction that guard must never fail in. The lookahead
# excludes the PR events, which the check below already excludes anyway.
#
# There is deliberately no separate `_PR_IS_PUSH`: this pattern already covers
# `== 'push'`, so a dedicated push regex was a branch whose deletion could not
# change any verdict — the same reason `_triggers`' raw-None branch and
# `check_partition`'s `dq & dm` test were removed.
_PR_IS_OTHER_EVENT = re.compile(
    r"github\.event_name\s*==\s*['\"](?!pull_request(?:_target)?['\"])[A-Za-z_]+['\"]")
_PR_IS_NOT_PULL_REQUEST = re.compile(
    r"github\.event_name\s*!=\s*['\"]pull_request(?:_target)?['\"]")
_PR_IS_PULL_REQUEST = re.compile(
    r"github\.event_name\s*==\s*['\"]pull_request(?:_target)?['\"]")


def _is_a_zero_number(text: str) -> bool:
    """A numeric literal that is exactly zero — including exponent forms PyYAML
    leaves as STRINGS.

    GitHub's number literals are "any number format supported by JSON", which
    accepts `0e0`, `0.0e0` and `0e+0`. YAML 1.1 resolves none of them, and the
    reason is worth stating precisely because it is not "a dot or a signed
    exponent": PyYAML's float pattern REQUIRES a dot, and its optional exponent
    group is itself sign-REQUIRING (`[eE][-+][0-9]+`). So `0e+0` has the sign and
    no dot, `0.0e0` has the dot and no sign, and neither resolves. PyYAML hands
    all of them over as `str`, the `isinstance(..., (int, float))` arm never sees
    them — which left
    `if: 0e0` classified PRODUCIBLE, a false GREEN in the one direction the
    deadlock checks must never fail in. `float()` closes it: it accepts every JSON
    number form.

    Applied ONLY after the string membership test, and a parse failure stays
    PRODUCIBLE — `0o0`, `nan` and any word are deliberately not decoded, the same
    reading this module documents for expressions it cannot decide. (`nan` and
    `inf` DO parse, and compare unequal to zero, so they stay producible too.)
    """
    try:
        return float(text) == 0.0
    except ValueError:
        return False


def _is_literally_off(condition: Any) -> bool:
    """A LITERAL falsy `if:` — decidable as "this job can never run".

    `if: false` is not an expression we "cannot classify"; it is a constant GitHub
    evaluates as falsy and skips. Counting it PRODUCIBLE would be a false GREEN in
    the one direction the deadlock checks must never fail in: a required check whose
    only producer is such a job would read as arriving forever while the merge waits
    for it.

    An explicit YAML `null` lands here too, and that is deliberate. It used to fall
    through to "producible" while `_FALSY_IF_STRINGS` simultaneously contained
    `"null"` — so the guard called the null constant falsy when written `${{ null }}`
    and truthy when written `if: null`, an internal contradiction where the natural
    spelling took the unsafe branch. The null CONSTANT is falsy in GitHub's
    expression language, so all spellings of it now agree. A bare `if:` (key present,
    no value) parses to the same `None` and is therefore classified the same way;
    that is the fail-closed reading of an ambiguous input, and the caller
    distinguishes a truly ABSENT key, which still means the job runs.

    LIMIT, so this is not over-trusted: this classifies CONSTANTS — including
    numeric literals in any JSON form, which is why `0e0` and `0.0e0` are decoded
    rather than read as opaque text. `if: ${{ 3-3 }}` is an expression that
    evaluates falsy and is deliberately NOT recognised — classifying arbitrary
    expressions is the trade `_gated_off_pr_refs` documents, and guessing there
    would manufacture false deadlocks.
    """
    if condition is None or condition is False:
        return True
    if isinstance(condition, str):
        text = _normalise_if(condition)
        return text in _FALSY_IF_STRINGS or _is_a_zero_number(text)
    if isinstance(condition, (int, float)):
        return not condition
    return False


def _gated_off_pr_refs(job: dict[str, Any]) -> bool:
    """Can this job never report on a PR-like ref, because of its own `if:`?

    A workflow can be `on: pull_request` while a JOB inside it is gated to push
    only. Such a job is not a producible check on the PR head or the queue
    branch, so counting it as producible is a false negative in the deadlock
    check — exactly the hazard this guard exists to catch.

    The event-name COMPARISON is classified, not substring-matched: a brute
    `"pull_request" in condition` test also swallowed the NEGATED form
    (`!= 'pull_request'`), which is just as push-only as `== 'push'`.

    LIMIT, stated so it is not over-trusted: the POSITIVE comparisons classified
    here are `== 'push'` and `== '<any other non-PR event>'`. An `if:` we cannot
    classify at all — an arbitrary expression, a `contains()`, a comparison against
    something that is not a quoted event name — still counts as producible,
    because the opposite default would turn an unrecognised expression into a
    false DEADLOCK on a healthy check, which is a worse failure than an unclosed
    false negative.

    A LITERAL falsy `if:` is NOT in that unclassifiable category: it is decidable
    and is handled first (`_is_literally_off`), because "this job never runs" is a
    fact, not a guess.
    """
    condition = job.get("if", _IF_ABSENT)
    if condition is _IF_ABSENT:
        return False  # no `if:` AT ALL -> the job runs, so it is producible
    if _is_literally_off(condition):
        return True  # decidable: the job never runs, so it produces no check
    if not isinstance(condition, str):
        return False
    if _PR_IS_PULL_REQUEST.search(condition):
        return False  # names a PR event positively; it CAN run on a PR ref
    if _PR_IS_OTHER_EVENT.search(condition):
        return True
    return bool(_PR_IS_NOT_PULL_REQUEST.search(condition))


_MATRIX_REF = re.compile(r"\$\{\{\s*matrix\.([A-Za-z0-9_]+)\s*\}\}")


def _render_matrix(template: str, matrix: dict[str, Any]) -> set[str]:
    """Render `test (${{ matrix.half }})` against the matrix values → {'test (a)', ...}.

    A template we cannot fully render is returned as-is (the caller then holds a
    name that will not match — fail-closed, not fail-open).
    """
    refs = set(_MATRIX_REF.findall(template))

    def expand(tmpl: str, keys: list[str], acc: set[str]) -> None:
        if not keys:
            acc.add(tmpl)
            return
        key = keys[0]
        values = matrix.get(key)
        if not isinstance(values, list):
            acc.add(tmpl)
            return
        # Substitute THIS key's placeholder, not the leftmost match: a template
        # with two different refs would otherwise pair an `a` value with a `b`
        # slot and yield names that do not exist.
        pattern = re.compile(r"\$\{\{\s*matrix\." + re.escape(key) + r"\s*\}\}")
        for value in values:
            # A CALLABLE replacement: passing `str(value)` directly makes the value
            # a replacement TEMPLATE, so a matrix value containing `\1` or
            # `\g<name>` raises re.error (exit 1, no annotation) and `\n`/`\t`
            # silently becomes a control character in the check name.
            expand(pattern.sub(lambda _m, v=value: str(v), tmpl), keys[1:], acc)

    acc: set[str] = set()
    expand(template, sorted(refs), acc)
    return acc


def producible_on_pull_request(workflows_dir: Path | None = None) -> set[str]:
    """Check names producible by any workflow that runs on a pull-request ref.

    The queue branch (`mergify/merge-queue/<sha>`) is a PR-like ref. A check
    produced only by a `push:` workflow whose BRANCH FILTER excludes the queue
    branch — the common case, e.g. `on: push: branches: [main]` — can never report
    there, and naming one in `merge_conditions` is the deadlock `.mergify.yml`
    warns about.

    KNOWN OVER-APPROXIMATION, stated so the verdict is not over-trusted: an
    UNFILTERED `on: push:` workflow DOES report on the queue branch (the queue
    branch produces ordinary `push` check runs — `.mergify.yml` says so itself),
    but this function treats EVERY workflow without a `pull_request`*
    trigger as unproducible. So a check produced ONLY by an unfiltered `push:`
    workflow is reported unproducible when it is not, and a `merge_conditions`
    entry naming it would be a FALSE RED. That direction is chosen deliberately: a
    false RED is visible and costs a review; a false GREEN is the outage. Names of
    `pull_request`-triggered workflows are the ground truth here, and no name in a
    list today depends on the over-approximation.
    """
    workflows_dir = workflows_dir or WORKFLOWS_DIR
    try:
        is_dir = workflows_dir.is_dir()
    except OSError as exc:
        # `is_dir()` re-raises EACCES; it only ignores ENOENT/ENOTDIR/EBADF/ELOOP.
        raise CannotMeasure(
            f"{workflows_dir}: unreadable ({type(exc).__name__}: {exc}) — cannot measure") \
            from exc
    if not is_dir:
        raise CannotMeasure(f"workflows dir not found: {workflows_dir}")
    names: set[str] = set()
    # `os.listdir`, NOT `glob`: `Path.glob` SILENTLY SWALLOWS EACCES and yields no
    # names, so an unreadable workflows directory looked like an EMPTY one and the
    # guard FABRICATED a measurement — it reported "NO pull_request-triggered
    # workflow produces it" for every required check (six false deadlock
    # violations, exit 1) instead of the honest exit 2.
    try:
        entries = sorted(os.listdir(workflows_dir))
    except OSError as exc:
        raise CannotMeasure(
            f"{workflows_dir}: unreadable ({type(exc).__name__}: {exc}) — cannot measure") \
            from exc
    for path in [workflows_dir / n for n in entries if fnmatch.fnmatch(n, "*.y*ml")]:
        try:
            workflow = read_yaml(path)
        except (yaml.YAMLError, CannotMeasure):
            # Deliberate: a broken workflow is other gates' business. Skipping it
            # can only make a NAME look unproducible, which reds the deadlock
            # check — a false RED. It can never manufacture a false GREEN, so this
            # stays a `continue` even for an unreadable file.
            continue
        if not isinstance(workflow, dict):
            continue
        if not (_triggers(workflow) & {"pull_request", "pull_request_target"}):
            continue
        jobs = workflow.get("jobs")
        require_mapping(jobs, f"{path}: `jobs:`")
        for job_id, job in jobs.items():
            require_mapping(job, f"{path}: jobs.{job_id}")
            if _gated_off_pr_refs(job):
                continue
            template = job.get("name")
            if template is not None and not isinstance(template, str):
                # `name: 5` used to reach `.strip()` -> AttributeError.
                raise CannotMeasure(
                    f"{path}: jobs.{job_id}.name must be a string, got "
                    f"{type(template).__name__} — cannot measure")
            if isinstance(template, str) and template.strip():
                strategy = job.get("strategy")
                if strategy is None:
                    strategy = {}
                require_mapping(strategy, f"{path}: jobs.{job_id}.strategy")
                matrix = strategy.get("matrix")
                if matrix is None:
                    names.add(template.strip())
                else:
                    require_mapping(matrix, f"{path}: jobs.{job_id}.strategy.matrix")
                    names |= _render_matrix(template.strip(), matrix)
            else:
                names.add(str(job_id))
    return names


_KEY_ABSENT = object()


def _raw_settings_entries(path: Path | None) -> Any:
    """The `repository.branch-protection` VALUE, `_KEY_ABSENT` when there is NO FILE.

    `_KEY_ABSENT` means NO FILE — the mirror does not exist, so `check_settings`
    skips it. A file that exists but declares nothing returns None, which
    `_settings_entries` maps to `[]` (an empty declaration) and `check_settings`
    then flags: an emptied declaration must reach it as an EMPTY set, not as None,
    or it passes vacuously in the one place this guard exists to fail closed.

    A present-but-null `branch-protection:` is deliberately NOT special-cased here;
    the `None -> []` mapping lives in `_settings_entries`. A duplicate branch here
    would be untestable — whichever copy you delete, the verdict is identical.
    """
    path = path or SETTINGS_PATH
    try:
        os.lstat(path)
    except (FileNotFoundError, NotADirectoryError):
        # NO directory entry at all: the mirror does not exist. This is the ONE
        # legitimate skip, and `os.lstat` (not `os.path.lexists`) is what makes it
        # safe. `lexists` is `lstat` under the hood and swallows EVERY OSError, so
        # a mirror behind an unsearchable PARENT directory (EACCES) was classified
        # "absent", `check_settings` was skipped, and the guard exited 0 with a
        # surface never compared. (A mode-000 FILE did not trigger this — `lstat`
        # needs no read permission on the target, only search on the parents.)
        return _KEY_ABSENT
    except OSError as exc:
        raise CannotMeasure(
            f"{path}: unreadable ({type(exc).__name__}: {exc}) — cannot measure") from exc
    try:
        doc = read_yaml(path)
    except yaml.YAMLError as exc:
        raise CannotMeasure(f"{path} unparsable: {exc}") from exc
    if not isinstance(doc, dict):
        # `[]`, `false`, `0`, a scalar — unusable input. `read_yaml` must NOT
        # coerce these to `{}`: doing so turned each into "declares nothing"
        # (exit 0) while the truthy version of the same shape exited 2.
        raise CannotMeasure(
            f"{path}: top level is not a mapping, got {type(doc).__name__} — cannot measure")
    repository = doc.get("repository")
    if repository is None:
        # Absent OR explicitly null: a present file declaring no branch protection.
        return []
    # PRESENT but not a mapping is UNUSABLE, not absent: returning _KEY_ABSENT
    # here would make `declared_settings_contexts` answer None, which
    # `check_settings` skips — silently disabling one of the three surfaces with
    # an exit 0.
    repository = require_mapping(repository, f"{path}: `repository`")
    return repository.get("branch-protection")


def _settings_entries(path: Path | None) -> list[dict[str, Any]]:
    """The `repository.branch-protection` entries; [] when the key is absent.

    A present key that is not a list of mappings is UNUSABLE, not empty: it exits
    2 rather than quietly yielding no entries (a dict here used to raise
    AttributeError, i.e. a non-zero exit — that must not silently become a pass).
    """
    raw = _raw_settings_entries(path)
    if raw is _KEY_ABSENT or raw is None:
        return []
    if not isinstance(raw, list):
        raise CannotMeasure(
            f"{path or SETTINGS_PATH}: repository.branch-protection must be a list, "
            f"got {type(raw).__name__} — cannot measure")
    for entry in raw:
        if not isinstance(entry, dict):
            raise CannotMeasure(
                f"{path or SETTINGS_PATH}: branch-protection entries must be mappings, "
                f"got {type(entry).__name__} — cannot measure")
    return raw


def _required_status_checks(entry: dict[str, Any]) -> dict[str, Any]:
    """`entry['required_status_checks']` as a mapping, or {} when absent.

    A present-but-non-mapping value is unusable and raises, rather than being
    silently treated as empty.
    """
    rsc = entry.get("required_status_checks")
    if rsc is None:
        return {}
    if not isinstance(rsc, dict):
        raise CannotMeasure(
            f"required_status_checks must be a mapping, got {type(rsc).__name__} — "
            "cannot measure")
    # Validate presence AND shape: a bare `required_status_checks:` reaches the
    # `or []` below, and a non-list `contexts` reaches iteration, either as a
    # non-2 exit with no `::error::`.
    contexts = rsc.get("contexts")
    if contexts is not None:
        require_list(contexts, "required_status_checks.contexts")
    return rsc


def _is_main(entry: dict[str, Any]) -> bool:
    """Does this entry speak for `main`? A MISSING branch is not `main`."""
    return str(entry.get("branch", "")).strip() == "main"


def declared_settings_contexts(path: Path | None = None) -> set[str] | None:
    """Contexts declared for `main` by `.github/settings.yml`, or None if the mirror is absent.

    Note the file is INERT: probot-settings reads top-level `branches:`, while
    this file nests under `repository: -> branch-protection:`. That is precisely
    why it is dangerous — it reads as the branch-protection source of truth and
    is not. (The stale `strict: true` in it is the origin of #4764's premise.)

    Only `branch: main` entries count. Unioning every entry would let the mirror
    declare the RIGHT six contexts for the WRONG branch — the exact mis-filing
    this guard exists to catch — and still pass.

    Returns an EMPTY SET (not None) when the key is present but declares no main
    contexts, so `check_settings` flags it instead of skipping it.
    """
    if _raw_settings_entries(path) is _KEY_ABSENT:
        return None
    contexts: set[str] = set()
    for entry in _settings_entries(path):
        if not _is_main(entry):
            continue
        rsc = _required_status_checks(entry)
        contexts |= _settings_contexts(rsc, path)
    return contexts


def _settings_contexts(rsc: dict[str, Any], path: Path) -> set[str]:
    """The mirror's `contexts` for one entry, with ELEMENT types validated.

    `str(c)` used to coerce every element, so `contexts: [5, null]` silently became
    `{'5', 'None'}` — an unvalidated input in the one file whose whole purpose is
    to be compared exactly. A non-string can never equal a real check name, so this
    was never a fail-open; it is refused for the same reason the live path refuses
    it: a value we cannot read is a value we cannot measure.

    The LIST shape is deliberately NOT re-checked here — `_required_status_checks`
    already refuses a non-list `contexts` (and is the only caller-visible guard on
    that), so a `require_list` here would be unreachable for every caller in this
    module and no test could detect its removal. Element types are this function's
    own contribution.
    """
    entries = rsc.get("contexts")
    if entries is None:
        return set()
    for ctx in entries:
        if not isinstance(ctx, str):
            raise CannotMeasure(
                f"{path}: contexts must be strings, got {type(ctx).__name__}"
                " — cannot measure")
    return set(entries)


def declared_settings_off_main(path: Path | None = None) -> list[str]:
    """Branches other than `main` the mirror declares contexts or `strict` for."""
    off: list[str] = []
    for entry in _settings_entries(path):
        if _is_main(entry):
            continue
        rsc = _required_status_checks(entry)
        if rsc.get("contexts") or "strict" in rsc:
            off.append(str(entry.get("branch", "<missing branch>")))
    return off


def declared_settings_strict(path: Path | None = None) -> bool | None:
    """The mirror's `strict` flag for `main`, or None when it declares none."""
    for entry in _settings_entries(path):
        if not _is_main(entry):
            continue
        rsc = _required_status_checks(entry)
        if "strict" in rsc:
            value = rsc["strict"]
            # `strict: "false"` is a STRING and `bool("false")` is True — it
            # would manufacture a spurious #4764 mismatch against a live
            # `strict=false`. A non-boolean is unusable input, not a truthy one.
            if not isinstance(value, bool):
                raise CannotMeasure(
                    f"required_status_checks.strict must be a boolean, got "
                    f"{type(value).__name__} — cannot measure")
            return value
    return None


# ── checks ────────────────────────────────────────────────────────────────────


def check_partition(parsed: dict[str, set[str]]) -> list[str]:
    """INV-1/INV-2: declared == queue ∪ merge, each name in exactly one list."""
    problems: list[str] = []
    dq, dm = declared_lists()

    for bucket in ("queue", "merge"):
        declared, actual = (dq, parsed["queue"]) if bucket == "queue" else (dm, parsed["merge"])
        for name in sorted(actual - declared):
            problems.append(
                f"{name!r} is in .mergify.yml {bucket}_conditions but NOT in the enumeration "
                f"in {Path(__file__).name} — an unaccounted required check"
            )
        for name in sorted(declared - actual):
            problems.append(
                f"{name!r} is declared '{bucket}' but is missing from .mergify.yml "
                f"{bucket}_conditions — a dropped {bucket} condition"
            )

    # NOTE: there is deliberately no `if dq & dm:` check here. `declared_lists()`
    # partitions each name by its single `where`, so the two declared buckets are
    # DISJOINT BY CONSTRUCTION — the branch could never fire, and a guard that
    # cannot fire is documentation pretending to be enforcement. The real overlap
    # risk (one name in BOTH .mergify.yml lists) is checked here:
    if parsed["queue"] & parsed["merge"]:
        problems.append(
            f"{sorted(parsed['queue'] & parsed['merge'])} appear in BOTH "
            "queue_conditions and merge_conditions in .mergify.yml"
        )

    for name, (where, why) in REQUIRED_SET.items():
        if where not in ("queue", "merge", "injected"):
            problems.append(f"{name!r}: unknown bucket {where!r}")
        if not (why or "").strip():
            problems.append(f"{name!r}: no recorded guarantee — coverage accounting is empty")

    # An `injected` name must stay OUT of both fused lists. If it is named there,
    # the enumeration's justification for it ("deliberately not an entry gate")
    # is false — naming it IS an entry gate. The generic `actual - declared`
    # comparison above also fires, but with a message that reads as a filing
    # error rather than the BEHAVIOUR change it actually is.
    for name in sorted(injected_names()):
        if name in parsed["queue"] or name in parsed["merge"]:
            problems.append(
                f"{name!r} is declared 'injected' but IS named in .mergify.yml — "
                "naming it also makes it an ENTRY gate, which contradicts the "
                "category and changes queue behaviour"
            )

    if not dq:
        problems.append("the enumeration declares an EMPTY queue bucket — fail-closed")
    if not dm:
        problems.append("the enumeration declares an EMPTY merge bucket — fail-closed")
    if not injected_names():
        # The two buckets above are cross-checked against `.mergify.yml`, so an
        # empty one is caught there too. The `injected` bucket has NO second file:
        # nothing outside this guard records that a live-required name is enforced
        # by injection. So an empty bucket is UNVERIFIABLE offline — drop a name
        # from it while the mirror is absent and `run()` printed exit 0 over a live
        # set that still required it. Fail closed, like the other two.
        problems.append("the enumeration declares an EMPTY injected bucket — fail-closed")
    return problems


def check_injection_mode(modes: list[Any]) -> list[str]:
    """EVERY queue rule must set `branch_protection_injection_mode: merge`.

    Not decoration. Under `merge` (set in `.mergify.yml`, contract point 4) the
    injected branch-protection contexts gate the MERGE only, which is what makes an
    `injected` name's absence from `queue_conditions` a deliberate choice WITHOUT
    weakening enforcement. Under `queue` — Mergify's DEFAULT, so an ABSENT key means
    `queue` — those same contexts are injected for QUEUING too, and the enumeration's
    claim about an `injected` name stops being true. The failure this catches, named
    concretely: the key is deleted, flipped to `queue`, or left unset on a SECOND
    rule while the first says `merge`.
    """
    if not modes:
        return [
            ".mergify.yml has no queue rule, so `branch_protection_injection_mode` "
            "cannot be read — the mode justifies every 'injected' name, so it cannot "
            "be assumed (#6144)"
        ]
    return [
        f".mergify.yml queue_rules[{index}].branch_protection_injection_mode is "
        f"{mode!r}, not 'merge' (an absent key means Mergify's default, 'queue') — "
        f"under 'queue' the injected branch-protection contexts gate ENTRY as well, "
        f"so an 'injected' name is no longer 'deliberately not an entry gate' and "
        f"the enumeration's reason for it is false (#6144)"
        for index, mode in enumerate(modes)
        if mode != "merge"
    ]


def check_injected_producible(injected: set[str], producible: set[str]) -> list[str]:
    """Every `injected` name must be PRODUCIBLE on a PR-like ref.

    An `injected` name is never named in `.mergify.yml`, so `check_deadlock` never
    sees it — but Mergify injects it as a MERGE condition from branch protection,
    evaluated against the queue branch. A required check that no PR-like workflow
    produces can never go green there and the merge waits forever: the SAME
    deadlock, reached through the injection door instead of a list. The failure
    this catches, named concretely: a live-required check whose only producer is
    `push:`-filtered to `main`.
    """
    return [
        f"{name!r} is required on main and enforced by branch-protection injection, "
        f"but NO pull_request-like workflow produces it — the merge would wait "
        f"forever (a deadlock through the injection door)"
        for name in sorted(injected - producible)
    ]


def check_deadlock(names: set[str], producible: set[str],
                   bucket: str) -> list[str]:
    """Every condition must be producible on a PR-like ref — in BOTH lists.

    `merge_conditions` gate the MERGE, evaluated against the queue branch; the
    queue branch is PR-like, so a check only a `push:` workflow FILTERED AWAY from
    the queue branch produces can never report there and the merge waits forever.
    (See `producible_on_pull_request` for why an unfiltered `push:` workflow is
    over-approximated as unproducible, and why that direction is the safe one.)

    `queue_conditions` gate ENTRY, evaluated against the PR HEAD — also PR-like,
    so the same property is required. An unproducible entry condition means the
    condition can never become true and entry stalls for every PR. Checking only
    the merge list leaves that half unguarded.
    """
    # `bucket` has no default and no fallback entry: it is a REQUIRED argument with
    # exactly two call sites, both of which pass one of these keys. A default arm
    # could never fire, so it was documentation pretending to be a guard — same
    # reason `_PR_IS_PUSH` and `_triggers`' raw-None branch were deleted.
    consequence = {
        "merge_conditions": "the queue branch never reports it, so the queue DEADLOCKS for every PR",
        "queue_conditions": "so the condition can never become true and queue ENTRY stalls for every PR",
    }[bucket]
    return [
        f"{name!r} is in {bucket} but NO pull_request-triggered workflow "
        f"produces it — {consequence}"
        for name in sorted(names - producible)
    ]


def gate_legs(path: Path | None = None) -> tuple[list[str], set[str]]:
    """Return (needs, LEGS rows) for the required aggregate job.

    Two machine-readable halves of ONE claim. `needs:` is the set of legs the
    gate observes; the `LEGS` heredoc is the fail-closed table that decides what
    a non-`success` result MEANS for each. They must describe the same set.
    """
    path = path or PYTHON_CI_PATH
    try:
        exists = path.exists()
    except OSError as exc:
        raise CannotMeasure(
            f"{path}: unreadable ({type(exc).__name__}: {exc}) — cannot measure") from exc
    if not exists:
        raise CannotMeasure(f"workflow not found: {path}")
    try:
        workflow = read_yaml(path)
    except yaml.YAMLError as exc:
        raise CannotMeasure(f"{path} unparsable: {exc}") from exc
    if not isinstance(workflow, dict):
        raise CannotMeasure(
            f"{path}: top level is not a mapping, got {type(workflow).__name__} — cannot measure")
    jobs = workflow.get("jobs") or {}
    require_mapping(jobs, f"{path}: `jobs:`")
    if GATE_JOB not in jobs:
        raise CannotMeasure(f"{path}: no {GATE_JOB!r} job")
    gate = jobs[GATE_JOB] or {}
    require_mapping(gate, f"{path}: jobs.{GATE_JOB}")
    needs = gate.get("needs")
    if needs is None:
        needs = []
    elif isinstance(needs, str):
        needs = [needs]
    elif not isinstance(needs, list):
        # `needs: 5` AND `needs: 0` / `false` / `''`: a truthiness-based `or []`
        # silently accepted the falsy non-lists as "no needs", so the same shape
        # exited 2 when truthy and 1 when falsy.
        raise CannotMeasure(
            f"{path}: jobs.{GATE_JOB}.needs must be a string or list, got "
            f"{type(needs).__name__} — cannot measure")

    for n in needs:
        # Elements, not just the container: `needs: [{a: b}]` reached `set(needs)`
        # and raised `TypeError: unhashable type: 'dict'` — exit 1, no annotation.
        if not isinstance(n, str) or not n.strip():
            raise CannotMeasure(
                f"{path}: jobs.{GATE_JOB}.needs entries must be non-blank strings, "
                f"got {type(n).__name__} — cannot measure")

    steps = gate.get("steps")
    if steps is None:
        steps = []
    if not isinstance(steps, list):
        raise CannotMeasure(
            f"{path}: jobs.{GATE_JOB}.steps must be a list, got "
            f"{type(steps).__name__} — cannot measure")
    runs: list[str] = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        run = step.get("run")
        if run is None:
            continue
        if not isinstance(run, str):
            # `run: 5` / `true` / `[a, b]` — a `run or ""` only replaced FALSY
            # values, so a truthy non-string reached `.splitlines()` and raised
            # an AttributeError traceback (exit 1, no `::error::`).
            raise CannotMeasure(
                f"{path}: {GATE_JOB} step `run:` must be a string, got "
                f"{type(run).__name__} — cannot measure")
        runs.append(run)
    legs: set[str] = set()
    found_heredoc = False
    for run in runs:
        lines = run.splitlines()
        for idx, line in enumerate(lines):
            # The opener is the tail of a command: `done <<'LEGS'`, not a bare marker.
            if not line.strip().endswith(GATE_LEGS_HEREDOC):
                continue
            found_heredoc = True
            for row in lines[idx + 1 :]:
                if row.strip() == "LEGS":
                    break
                row = row.strip()
                if not row:
                    continue
                legs.add(row.split("|", 1)[0].strip())
    if not found_heredoc:
        raise CannotMeasure(
            f"{path}: {GATE_JOB} has no {GATE_LEGS_HEREDOC} table — the required "
            "check's per-leg verdict table could not be read; nothing is not a pass"
        )
    return needs, legs


def check_gate_legs(needs: list[str], legs: set[str]) -> list[str]:
    """`needs:` and the gate's LEGS table must describe the SAME set.

    A leg in `needs:` with no LEGS row still trips rule 1 (which greps the
    joined results for `failure|cancelled`), but a leg that reports `skipped`
    has NO row to fail closed on — so the required check would CERTIFY a shard
    the selector selected and GitHub never ran. That is the #5219 shape (a
    green required check over a tree whose shard did not run) reached through
    the other door. `tests/test_ci_selection.py` asserts the same set equality at
    test time; this re-asserts it at RUN time inside the aggregate job itself —
    defence in depth on the job that owns the required context, not the discovery
    of a gap.
    """
    problems: list[str] = []
    as_set = set(needs)
    for leg in sorted(as_set - legs):
        problems.append(
            f"{leg!r} is in {GATE_JOB}.needs but has NO row in its LEGS table — a "
            "`skipped` result would not fail closed, so the required check could "
            "certify a shard that never ran"
        )
    for leg in sorted(legs - as_set):
        problems.append(
            f"{leg!r} has a LEGS row but is NOT in {GATE_JOB}.needs — the row is "
            "dead (its result can never be read), and the leg it names is "
            "unobservable by the required check"
        )
    if not as_set:
        problems.append(f"{GATE_JOB}.needs is EMPTY — the required check observes nothing")
    return problems


def check_settings_branches(off_main: list[str]) -> list[str]:
    """The mirror is a mirror of `main`; contexts for another branch are mis-filed."""
    if not off_main:
        return []
    return [
        f".github/settings.yml declares required_status_checks for branch(es) {off_main} "
        f"rather than `main` — a mirror of the wrong branch, which would silently "
        f"check nothing that matters"
    ]


def check_settings(contexts: set[str] | None, expected: set[str]) -> list[str]:
    """The declarative mirror must agree, or not exist."""
    if contexts is None:
        return []
    if contexts != expected:
        return [
            f".github/settings.yml declares contexts {sorted(contexts)} but the required set is "
            f"{sorted(expected)} — a stale declarative mirror. Update it or delete it; do not "
            f"leave a third list that reads as authoritative and is not."
        ]
    return []


def read_live_protection() -> tuple[set[str], bool | None]:
    """Live branch protection: (required contexts, strict). Needs admin-scoped credentials.

    `strict` is read because the declarative mirror also declares it, and a stale
    `strict: true` there is not cosmetic: it was the recorded premise of #4764.
    A null/absent value is returned as None (unknown), never coerced to False.
    """
    cmd = [
        "gh",
        "api",
        "repos/daniel-ospina/tortoise/branches/main/protection",
        "--jq",
        "{contexts: .required_status_checks.contexts, strict: .required_status_checks.strict}",
    ]
    try:
        out = subprocess.run(cmd, capture_output=True, encoding="utf-8",
                             errors="replace", timeout=60)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        # ValueError is in here for UnicodeDecodeError: decoding gh's output used
        # the locale encoding and a malformed byte raised it inside `subprocess`,
        # where neither OSError nor SubprocessError catches it. `errors="replace"`
        # above is the belt to this brace.
        raise CannotMeasure(f"could not run gh: {exc}") from exc
    if out.returncode != 0:
        raise CannotMeasure(
            "could not read branch protection (needs admin-scoped credentials): "
            + (out.stderr or "").strip()[:300]
        )
    import json

    try:
        payload = json.loads(out.stdout)
    except json.JSONDecodeError as exc:
        raise CannotMeasure(f"branch protection returned non-JSON: {out.stdout[:200]!r}") from exc
    if not isinstance(payload, dict):
        raise CannotMeasure(
            f"branch protection payload must be an object, got {type(payload).__name__}")
    # Validate the payload's shape. A malformed `gh` response used to reach
    # `set(...)` and raise TypeError (exit 1, no annotation); this is the last
    # unguarded parsed-payload access in the module.
    contexts = require_list(
        [] if payload.get("contexts") is None else payload["contexts"],
        "branch protection contexts")
    for ctx in contexts:
        if not isinstance(ctx, str):
            raise CannotMeasure(
                f"branch protection contexts must be strings, got {type(ctx).__name__}"
                " — cannot measure")
    strict = payload.get("strict")
    if strict is not None and not isinstance(strict, bool):
        raise CannotMeasure(
            f"branch protection strict must be a boolean, got {type(strict).__name__}"
            " — cannot measure")
    return set(contexts), strict


def check_declared_strict(contexts_strict: bool | None, declared_strict: bool | None) -> list[str]:
    """The mirror's `strict` must match live — but only when both are known."""
    if contexts_strict is None or declared_strict is None:
        return []
    if contexts_strict != declared_strict:
        return [
            f".github/settings.yml declares strict={declared_strict} but live protection is "
            f"strict={contexts_strict} — a stale premise that has already produced a wrong "
            f"conclusion once (#4764)"
        ]
    return []


# ── entry point ───────────────────────────────────────────────────────────────


def run(live: bool = False) -> tuple[int, list[str], list[str]]:
    """Return (exit_code, violations, notes)."""
    notes: list[str] = []
    try:
        parsed = load_mergify()
        producible = producible_on_pull_request()
        settings = declared_settings_contexts()
        declared_strict = declared_settings_strict()
        off_main = declared_settings_off_main()
        needs, legs = gate_legs()
        if live:
            live_contexts, live_strict = read_live_protection()
        else:
            live_contexts, live_strict = None, None
        injection_modes = read_injection_modes()
    except CannotMeasure as exc:
        return 2, [], [f"CANNOT MEASURE: {exc}"]

    dq, dm = declared_lists()
    injected = injected_names()
    # The enumeration is the LIVE-required set, so it — not `dq | dm` — is the
    # expected total. `dq | dm` omitted every `injected` name, which is how a
    # SEVEN-context live protection could read as "six" and still pass.
    expected = set(REQUIRED_SET)

    violations = (
        check_partition(parsed)
        + check_deadlock(parsed["queue"], producible, "queue_conditions")
        + check_deadlock(parsed["merge"], producible, "merge_conditions")
        + check_settings(settings, expected)
        + check_settings_branches(off_main)
        + check_declared_strict(live_strict, declared_strict)
        + check_injection_mode(injection_modes)
        + check_injected_producible(injected, producible)
        + check_gate_legs(needs, legs)
    )
    if live_contexts is not None:
        missing = sorted(expected - live_contexts)
        extra = sorted(live_contexts - expected)
        if missing:
            violations.append(
                f"required in the enumeration but NOT required on main: {missing} "
                f"— the enumeration has drifted ahead of branch protection"
            )
        if extra:
            violations.append(
                f"required on main but NOT in the enumeration: {extra} "
                f"— an unaccounted required check"
            )

    notes.append(f"queue_conditions : {sorted(parsed['queue'])}")
    notes.append(f"merge_conditions : {sorted(parsed['merge'])}")
    notes.append(f"declared total   : {len(expected)} name(s) "
                 f"(queue={len(dq)}, merge={len(dm)}, injected={len(injected)})")
    # Record the mirror's state explicitly. Without this, an ABSENT mirror left no
    # note at all and `main()` still printed a success line claiming the mirror
    # AGREED — asserting a comparison that never happened.
    notes.append(
        "declarative mirror : ABSENT — not compared (the file is optional)"
        if settings is None
        else f"declarative mirror : {len(settings)} context(s) declared for main")
    notes.append(f"producible on PR refs: {len(producible)} check name(s) across "
                 f"{len(list(WORKFLOWS_DIR.glob('*.y*ml')))} workflow file(s)")
    notes.append(f"{GATE_JOB}: {len(needs)} need(s), {len(legs)} LEGS row(s) — "
                 f"{'identical' if set(needs) == legs else 'MISMATCH'}")
    if live_contexts is not None:
        notes.append(f"LIVE required    : {sorted(live_contexts)}")
        notes.append(f"LIVE strict      : {live_strict}  | settings.yml: {declared_strict}")
    else:
        notes.append("LIVE required    : not read (offline mode; pass --live)")
    return (1 if violations else 0), violations, notes


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument(
        "--live",
        action="store_true",
        help="also compare against live branch protection (needs admin credentials)",
    )
    args = ap.parse_args(argv)

    code, violations, notes = run(live=args.live)
    for note in notes:
        print(f"  {note}")
    if code == 0:
        print("✅ required-set sync: no drift between the queue lists, the "
              "enumeration and the surfaces actually read (see the notes above); "
              "every merge condition is producible on a PR ref")
        return 0
    if code == 2:
        for note in notes:
            if note.startswith("CANNOT MEASURE"):
                print(f"::error::{note}")
        return 2
    for violation in violations:
        print(f"::error::required-set drift: {violation}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
