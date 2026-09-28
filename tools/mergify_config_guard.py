#!/usr/bin/env python3
"""Protection-invariant guard for the Mergify entry/merge gate (#5215, Task 4).

WHY THIS FILE EXISTS
--------------------
The #5215 merge-throughput plan's whole safety argument is that every lever must
answer *"what does this stop protecting, and why is that safe?"* (§2). §3 lists
the protection invariants; this tool makes them MECHANICAL, so the answer is
asserted on every event instead of trusted to a reviewer who remembers it.

#5384 and #5527 changed queue settings in production (`branch_protection_injection_mode`,
`max_parallel_checks`, `priority_rules`). The invariants those changes must not
violate now fail closed.

USAGE
-----
    python3 tools/mergify_config_guard.py --static
        exit 0 = every static clause passes (SATISFIED)
        exit 1 = a clause is violated (DIVERGED)
        exit 2 = config absent/unreadable, a duplicate/merge YAML key, an
                 unrecognised injection mode, or absent gate record (UNAVAILABLE)

    python3 tools/mergify_config_guard.py --live
        OPERATIONAL, needs an admin credential (`Administration: read`); it is
        NOT run by CI — a fail-closed admin read inside CI would deadlock every
        PR (`GITHUB_TOKEN` cannot hold `Administration: read`).
        exit 0 = I1 SATISFIED; 1 = DIVERGED; 2 = UNAVAILABLE (recorded non-clean)

    python3 tools/mergify_config_guard.py --recut
        Recompute I10's `gate_digest` from HEAD and refresh `verified_at`.
        This is the documented, bypass-free fix path when I10 reds because the
        gate's definition changed (the digest is HEAD-computed, so the fix is a
        re-cut, never a `--admin` merge).

    python3 tools/mergify_config_guard.py --print-digest
        Print the head-computed `gate_digest` (used to author the record).

RESULT TOKENS (S12 asserts each is reachable, and that UNAVAILABLE is
distinguishable from DIVERGED): SATISFIED / DIVERGED / UNAVAILABLE.

FAIL-CLOSED POLARITY
--------------------
An unreadable, absent, or UNRECOGNISED value is never a pass. An unknown
injection mode, a duplicate YAML key, an unresolvable workflow parse, or a
missing record all exit 2 (UNAVAILABLE). Only a positively-satisfied clause
exits 0.

WHAT THE GUARD DELIBERATELY DOES *NOT* CLAIM
--------------------------------------------
Clause (viii)(a) is a SILENCE detector for TH6, not TH6's cover (§7): it cannot
see an emitting job's `if:`/`needs:`/`continue-on-error`/`steps`, so a PR that
guts a required job (which then reports Success while skipped — #2055/#5649) or
re-badges it with a second `name:` is caught by NO clause here. That vector
(TH2-gut/TH7) is out of the plan's bound (#5649). What this clause asserts is
that no change to the gate's DEFINITION is silent: the record must be re-cut.

I5 (a red job inside the required aggregate's `needs` reddens the aggregate) is
NOT re-implemented here — it is already asserted by
`tests/test_ci_selection.py::test_drift_gate_cannot_skip_the_test_matrix` and
`tests/test_ci_selection.py::test_required_gate_covers_the_long_legs`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shlex
import subprocess
import sys
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parent.parent

MERGIFY_REL = ".mergify.yml"
SETTINGS_REL = ".github/settings.yml"
RECORD_REL = "docs/ci/required-contexts.json"
WORKFLOW_REL = ".github/workflows"

DEFAULT_INJECTION_MODE = "queue"
KNOWN_INJECTION_MODES = ("queue", "merge")
HEAVY_CONTEXT = "python-ci-gate"

# I10's own freshness window (the digest is HEAD-computed, but a stale record is
# still a stale attestation). I1's live read carries its own, looser quarterly
# window and is tracked in a separate key so the two are never conflated.
RECORD_FRESH_DAYS = 7
LIVE_FRESH_DAYS = 90

# The numbered clause inventory. A test pins this tuple, so a clause cannot be
# deleted or renamed silently (#5649 is the residual for a PR that edits the
# pinning test or the workflow together with the clause body).
CLAUSE_IDS = ("i", "ii", "iii", "iv", "v", "vi", "vii", "viii")

# I11 clause (1): files that may legitimately name `.github/settings.yml`. The
# guard READS it (I10's projection includes it) and its test proves that reading
# does not make the stale declaration authoritative — a DECLARATION-HOME CHECK
# is not an authoritative reader. Any OTHER reader is the bug I11 exists to
# catch, so exactly these two paths are exempt.
DECLARATION_HOME_CHECKERS = (
    "tools/mergify_config_guard.py",
    "tests/test_mergify_config_guard.py",
)

EXIT_OK, EXIT_DIVERGED, EXIT_UNAVAILABLE = 0, 1, 2
RESULT_TOKEN = {0: "SATISFIED", 1: "DIVERGED", 2: "UNAVAILABLE"}

# Shell operators that make a step a COMPOUND command. Clause (vii) requires the
# validator invocation to be the step's SOLE command: `python3 tool || true`,
# `python3 tool; exit 0`, `python3 tool && echo ok` and a trailing `echo done`
# all satisfy "the validator runs" while never failing.
_FORBIDDEN_SHELL = (";", "&&", "||", "&", "|", "$(", "`")

# `env:` keys that can neutralise a Python validator: a PR-authored
# `PYTHONPATH` + `sitecustomize.py` can shadow `sys.exit`; `PYTHONSTARTUP` runs
# on interactive start; `PYTHONHOME` re-roots the stdlib.
_PYTHON_ENV_SHADOWS = ("PYTHONPATH", "PYTHONSTARTUP", "PYTHONHOME")


class GuardUnreadable(Exception):
    """Evidence absent, unparseable, or carrying an unrecognised value (exit 2)."""


# ---------------------------------------------------------------------------
# Strict YAML loading — duplicate keys and merge keys are REFUSED (I10)
# ---------------------------------------------------------------------------


class _StrictLoader(yaml.SafeLoader):
    """A SafeLoader that refuses duplicate mapping keys and YAML merge keys.

    `yaml.safe_load` silently keeps the LAST duplicate key, which is exactly the
    shape a `merge=union` (or a careless edit) produces. I10's canonical form
    cannot be computed from a document that admits duplicates, so refusal is
    exit 2 (unreadable evidence), never a pass.
    """

    def construct_mapping(self, node: yaml.Node, deep: bool = False) -> Any:
        seen: set[str] = set()
        for key_node, _ in node.value:
            if key_node.tag == "tag:yaml.org,2002:merge":
                raise GuardUnreadable("YAML merge key ('<<') is not permitted")
            if not isinstance(key_node, yaml.ScalarNode):
                raise GuardUnreadable("non-scalar mapping key is not permitted")
            if key_node.value in seen:
                raise GuardUnreadable(f"duplicate mapping key {key_node.value!r}")
            seen.add(key_node.value)
        return super().construct_mapping(node, deep=deep)


def _load_yaml(text: str, what: str) -> Any:
    try:
        return yaml.load(text, Loader=_StrictLoader)
    except GuardUnreadable:
        raise
    except yaml.YAMLError as exc:
        raise GuardUnreadable(f"{what}: invalid YAML: {exc}") from exc


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise GuardUnreadable(f"{path}: unreadable ({exc})") from exc


# ---------------------------------------------------------------------------
# Configuration loading
# ---------------------------------------------------------------------------


def _load_mergify(root: Path) -> dict:
    path = root / MERGIFY_REL
    if not path.is_file():
        raise GuardUnreadable(f"{MERGIFY_REL} is absent")
    doc = _load_yaml(_read(path), MERGIFY_REL)
    if not isinstance(doc, dict):
        raise GuardUnreadable(f"{MERGIFY_REL}: top level is not a mapping")
    rules = doc.get("queue_rules")
    if not isinstance(rules, list) or not rules:
        raise GuardUnreadable(f"{MERGIFY_REL}: queue_rules must be a non-empty list")
    for index, rule in enumerate(rules):
        if not isinstance(rule, dict):
            raise GuardUnreadable(f"{MERGIFY_REL}: queue rule #{index} is not a mapping")
        if not isinstance(rule.get("name"), str) or not rule["name"]:
            raise GuardUnreadable(f"{MERGIFY_REL}: queue rule #{index} needs a string name")
        for key in ("queue_conditions", "merge_conditions"):
            if key in rule and not isinstance(rule[key], list):
                raise GuardUnreadable(f"{MERGIFY_REL}: rule #{index} {key} must be a list")
            for cond in rule.get(key) or []:
                if not isinstance(cond, str):
                    raise GuardUnreadable(
                        f"{MERGIFY_REL}: rule #{index} {key} carries a non-string condition"
                    )
    return doc


def _load_record(root: Path, record_path: Path | None = None) -> dict | None:
    path = record_path if record_path is not None else root / RECORD_REL
    if not path.is_file():
        return None
    doc = _load_yaml(_read(path), str(path))
    if not isinstance(doc, dict):
        raise GuardUnreadable(f"{path}: top level is not a mapping")
    return doc


def _success_names(conditions: Iterable[str]) -> list[str]:
    """The bare check names of `check-success=` conditions, in list order."""
    names: list[str] = []
    for cond in conditions:
        kind, name = _split_check(cond)
        if kind == "check-success":
            names.append(name)
    return names


def _split_check(cond: str) -> tuple[str | None, str | None]:
    """(kind, name) for a check condition, else (None, None).

    Classification is by CONDITION PREFIX (`check-*` vs anything else), never by
    an exception list of example non-check conditions (cycle 4): a future
    legitimate `label=`/`files~=`/`base=` condition must not false-fail.
    """
    if not isinstance(cond, str):
        return None, None
    body = cond[1:] if cond.startswith("-") else cond
    if not body.startswith("check-"):
        return None, None
    kind, _, name = body.partition("=")
    return kind, name


def _all_check_pairs(rules: Iterable[dict]) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for rule in rules:
        for key in ("queue_conditions", "merge_conditions"):
            for cond in rule.get(key) or []:
                kind, name = _split_check(cond)
                if kind is not None:
                    pairs.append((kind, name))
    return pairs


def _effective_mode(rule: dict) -> str:
    """The rule's effective injection mode (`queue` when absent)."""
    return rule.get("branch_protection_injection_mode", DEFAULT_INJECTION_MODE)


# ---------------------------------------------------------------------------
# Workflow emitters
# ---------------------------------------------------------------------------


def _workflow_files(root: Path) -> list[Path]:
    directory = root / WORKFLOW_REL
    if not directory.is_dir():
        return []
    return sorted(
        p
        for p in directory.iterdir()
        if p.is_file() and p.suffix in (".yml", ".yaml")
    )


def _triggers(doc: dict) -> Any:
    """The `on:` value. PyYAML parses an unquoted `on:` as the boolean key True."""
    for key in ("on", True):
        if key in doc:
            return doc[key]
    return None


def _has_pull_request(triggers: Any) -> bool:
    if isinstance(triggers, str):
        return triggers == "pull_request"
    if isinstance(triggers, list):
        return "pull_request" in triggers
    if isinstance(triggers, dict):
        return "pull_request" in triggers
    return False


def _workflow_docs(root: Path) -> dict[str, dict]:
    """basename -> parsed doc, for every workflow file.

    An unparseable workflow is UNREADABLE evidence -> exit 2, never skipped: a
    skipped file would let clause (vi) pass vacuously.
    """
    docs: dict[str, dict] = {}
    for path in _workflow_files(root):
        doc = _load_yaml(_read(path), path.name)
        if not isinstance(doc, dict):
            raise GuardUnreadable(f"{path.name}: top level is not a mapping")
        docs[path.name] = doc
    return docs


def _emitters(docs: dict[str, dict]) -> list[dict]:
    """Every job's effective check-run name, workflow, id, and PR availability.

    The effective name is the job's `name:` field when set, else its job id
    (cycle 10). Keying on the job id alone let a second job carrying
    `name: docs` shadow a required context.
    """
    emitters: list[dict] = []
    for workflow, doc in docs.items():
        has_pr = _has_pull_request(_triggers(doc))
        jobs = doc.get("jobs")
        if not isinstance(jobs, dict):
            continue
        for job_id, job in jobs.items():
            if not isinstance(job, dict):
                continue
            name = job.get("name")
            effective = name if isinstance(name, str) and name else str(job_id)
            emitters.append(
                {
                    "workflow": workflow,
                    "job": str(job_id),
                    "name": effective,
                    "has_pr": has_pr,
                }
            )
    return emitters


def _emitter_map(emitters: Iterable[dict]) -> dict[str, list[list[str]]]:
    """`effective check-run name -> sorted [(workflow, job)]` (I10's projection)."""
    mapping: dict[str, list[list[str]]] = {}
    for emitter in emitters:
        mapping.setdefault(emitter["name"], []).append(
            [emitter["workflow"], emitter["job"]]
        )
    return {name: sorted(entries) for name, entries in sorted(mapping.items())}


# ---------------------------------------------------------------------------
# I10 — the gate digest
# ---------------------------------------------------------------------------


def _gate_projection(root: Path) -> dict:
    """The entry-gate definition, canonicalized for the digest (I10).

    The projection is: every `queue_rules[*]` IN FILE ORDER (name + full
    condition lists + effective injection mode + `autoqueue`), a top-level
    `merge_conditions` if present, `merge_protections_settings.auto_merge_conditions`,
    `.github/settings.yml`, and the effective emitter map. Order is semantic
    (first-match-wins), so `queue_rules` stays a list. Throughput knobs
    (`batch_size`, `batch_max_wait_time`, `merge_method`) are deliberately NOT in
    the projection: the digest keys on WHAT THE GATE REQUIRES, not on how the
    queue batches.
    """
    doc = _load_mergify(root)
    rules = []
    for rule in doc["queue_rules"]:
        rules.append(
            {
                "name": rule["name"],
                "queue_conditions": list(rule.get("queue_conditions") or []),
                "merge_conditions": list(rule.get("merge_conditions") or []),
                "branch_protection_injection_mode": _effective_mode(rule),
                "autoqueue": rule.get("autoqueue"),
            }
        )
    settings_path = root / SETTINGS_REL
    settings_text = _read(settings_path) if settings_path.is_file() else None
    mps = doc.get("merge_protections_settings")
    return {
        "queue_rules": rules,
        "merge_conditions": doc.get("merge_conditions"),
        "merge_protections_settings": {
            "auto_merge_conditions": (
                mps.get("auto_merge_conditions") if isinstance(mps, dict) else None
            )
        },
        "emitters": _emitter_map(_emitters(_workflow_docs(root))),
        "settings_yml": settings_text,
    }


def gate_digest(root: Path) -> str:
    """`sha256(canonical(entry-gate projection))` — UTF-8 JSON, sorted keys.

    `queue_rules` retains FILE ORDER (it is a list, so `sort_keys` cannot reorder
    it). Duplicate/merge keys were already refused at load, so the projection is
    a well-defined function of the tree.
    """
    canonical = json.dumps(
        _gate_projection(root), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _parse_iso(value: Any, what: str) -> datetime:
    if not isinstance(value, str):
        raise GuardUnreadable(f"{what}: expected an ISO-8601 timestamp, got {value!r}")
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise GuardUnreadable(f"{what}: invalid timestamp {value!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Clause (vii) — the TH4 union validator
# ---------------------------------------------------------------------------


def _attributes_files(root: Path) -> list[Path]:
    """Every managed attributes file (cycle 8(a)): `.gitattributes` at any depth,
    plus `$GIT_DIR/info/attributes`. A nested `docs/product/.gitattributes`
    carrying `merge=union` must not escape the precondition.
    """
    files: list[Path] = []
    for path in sorted(root.rglob(".gitattributes")):
        if ".git" in path.parts:
            continue
        if path.is_file():
            files.append(path)
    git_dir = root / ".git"
    if git_dir.is_dir():
        info = git_dir / "info" / "attributes"
    elif git_dir.is_file():
        # A linked worktree: `.git` is a file pointing at the real gitdir.
        info = None
        try:
            for line in _read(git_dir).splitlines():
                if line.startswith("gitdir:"):
                    target = Path(line.split(":", 1)[1].strip())
                    if not target.is_absolute():
                        target = (root / target).resolve()
                    info = target / "info" / "attributes"
                    break
        except GuardUnreadable:
            info = None
    else:
        info = None
    if info is not None and info.is_file():
        files.append(info)
    return files


def _unioned_files(root: Path) -> set[str]:
    unioned: set[str] = set()
    for path in _attributes_files(root):
        for line in _read(path).splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            fields = stripped.split()
            if len(fields) < 2:
                continue
            pattern, attrs = fields[0], fields[1:]
            if any(attr == "merge=union" for attr in attrs):
                unioned.add(pattern)
    return unioned


def _if_excludes_pull_request(expr: Any) -> bool:
    """True only for an `if:` that is literally false or schedule-only.

    The plan's rule is "an `if:` that is NOT false/schedule-only" — an
    unrecognised expression is allowed, so this is deliberately narrow.
    """
    if expr is None:
        return False
    text = str(expr).strip().lower()
    if text in ("false", "${{ false }}"):
        return True
    return "schedule" in text and "pull_request" not in text


def _job_reaches_pull_request(job: dict) -> bool:
    return not _if_excludes_pull_request(job.get("if"))


def _single_command_run(run: str) -> list[str] | None:
    """The argv of a run block iff it is ONE command with no shell operator.

    Returns None when the run is compound (multiple statements, a trailing
    operator, a line continuation, or command substitution). Comment-only lines
    are ignored.
    """
    lines = [
        line for line in run.splitlines() if line.strip() and not line.strip().startswith("#")
    ]
    # A newline separates shell statements: more than one non-comment line is a
    # COMPOUND command (`python3 tool\necho done` runs the echo regardless of
    # the tool's exit code), never a single statement.
    if len(lines) != 1:
        return None
    if lines[0].rstrip().endswith("\\"):
        return None
    for line in lines:
        if any(op in line for op in _FORBIDDEN_SHELL):
            return None
    joined = lines[0].strip()
    if not joined:
        return None
    try:
        return shlex.split(joined)
    except ValueError:
        return None


def _validator_candidates(docs: dict[str, dict]) -> list[dict]:
    """Fail-closed validator steps: a direct `python3 <tool>.py` in a PR-true job.

    Excludes the guard itself and `ci_selection.py` (the drift gate is a
    different assertion). A candidate is still subject to the shape rules below.
    """
    candidates: list[dict] = []
    for workflow, doc in docs.items():
        jobs = doc.get("jobs")
        if not isinstance(jobs, dict):
            continue
        for job_id, job in jobs.items():
            if not isinstance(job, dict) or not _job_reaches_pull_request(job):
                continue
            for step in job.get("steps") or []:
                if not isinstance(step, dict):
                    continue
                run = step.get("run")
                if not isinstance(run, str):
                    continue
                argv = _single_command_run(run)
                if not argv or len(argv) < 2:
                    continue
                tool = argv[1]
                if argv[0] not in ("python", "python3"):
                    continue
                if not tool.startswith("tools/") or not tool.endswith(".py"):
                    continue
                if tool in ("tools/ci_selection.py", "tools/mergify_config_guard.py"):
                    continue
                candidates.append(
                    {
                        "workflow": workflow,
                        "job": str(job_id),
                        "step": step,
                        "argv": argv,
                        "tool": tool,
                        "run": run,
                        "job_spec": job,
                    }
                )
    return candidates


def _validate_union_protection(root: Path, docs: dict[str, dict]) -> tuple[int, str]:
    """Clause (vii): whenever `merge=union` is active, a fail-closed validator
    must be invoked as a step's SOLE, DIRECT command and must name the real
    unioned set.

    The validator's own correctness is #5570's
    `tests/test_registry_integrity.py::test_validated_set_equals_unioned_set`;
    this clause covers the PRESENCE vector only and consumes that pairing by
    requiring the invoked tool to reference every unioned path.
    """
    unioned = _unioned_files(root)
    if not unioned:
        return EXIT_OK, "no managed attributes file sets merge=union — no validator required"
    problems: list[str] = []
    accepted = 0
    for cand in _validator_candidates(docs):
        step = cand["step"]
        argv = cand["argv"]
        label = f"{cand['workflow']}:{cand['job']}"
        if any(arg in ("-h", "--help") for arg in argv):
            problems.append(f"{label}: validator invocation is `--help`-only")
            continue
        if "-c" in argv[:2]:
            problems.append(f"{label}: `python -c` payload is not a direct invocation")
            continue
        if step.get("shell"):
            problems.append(f"{label}: validator step overrides `shell:` ({step['shell']!r})")
            continue
        if step.get("continue-on-error"):
            problems.append(f"{label}: validator step is `continue-on-error`")
            continue
        env = step.get("env") or {}
        if isinstance(env, dict):
            shadow = sorted(k for k in env if k in _PYTHON_ENV_SHADOWS)
            if shadow:
                problems.append(f"{label}: validator step shadows env {shadow}")
                continue
        # (c) the invocation must name the REAL path/config set, never /dev/null.
        named: set[str] = set()
        if "--paths" in argv:
            values = [a for a in argv[argv.index("--paths") + 1 :] if not a.startswith("-")]
            if not values or any(v == "/dev/null" for v in values):
                problems.append(f"{label}: `--paths` names no real file (got {values})")
                continue
            named.update(values)
        tool_path = root / cand["tool"]
        if not tool_path.is_file():
            problems.append(f"{label}: validator {cand['tool']} does not exist")
            continue
        try:
            source = _read(tool_path)
        except GuardUnreadable as exc:
            problems.append(f"{label}: validator unreadable ({exc})")
            continue
        named.update(p for p in unioned if p in source)
        missing = sorted(unioned - named)
        if missing:
            problems.append(
                f"{label}: validator {cand['tool']} does not name the unioned set {missing}"
            )
            continue
        accepted += 1
    if accepted:
        return EXIT_OK, f"{accepted} fail-closed validator(s) cover {sorted(unioned)}"
    detail = "merge=union is active but no fail-closed validator invocation was found"
    if problems:
        detail += " — " + "; ".join(problems)
    return EXIT_DIVERGED, detail


# ---------------------------------------------------------------------------
# Clause (viii)(b) — I11, the declaration home
# ---------------------------------------------------------------------------


def _settings_readers(root: Path) -> list[str]:
    """In-repo readers of `.github/settings.yml`, excluding the declaration-home
    checkers (the guard and its test). A new reader makes the stale one-context
    declaration authoritative, which is the failure I11 exists to catch.
    """
    readers: list[str] = []
    for directory in (".github", "tests", "tools"):
        base = root / directory
        if not base.exists():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file() or ".git" in path.parts:
                continue
            rel = path.relative_to(root).as_posix()
            if rel == SETTINGS_REL or rel in DECLARATION_HOME_CHECKERS:
                continue
            if path.name in {Path(p).name for p in DECLARATION_HOME_CHECKERS}:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if "settings.yml" in text:
                readers.append(rel)
    return readers


def _settings_contexts(root: Path) -> list[str]:
    """The contexts declared in `.github/settings.yml` (the #3467 home)."""
    path = root / SETTINGS_REL
    if not path.is_file():
        raise GuardUnreadable(f"{SETTINGS_REL} is absent")
    doc = _load_yaml(_read(path), SETTINGS_REL)
    if not isinstance(doc, dict):
        raise GuardUnreadable(f"{SETTINGS_REL}: top level is not a mapping")
    repository = doc.get("repository")
    if not isinstance(repository, dict):
        raise GuardUnreadable(f"{SETTINGS_REL}: `repository` is not a mapping")
    protection = repository.get("branch-protection")
    if not isinstance(protection, list) or not protection:
        raise GuardUnreadable(f"{SETTINGS_REL}: `branch-protection` is not a non-empty list")
    contexts: list[str] = []
    for entry in protection:
        if not isinstance(entry, dict):
            continue
        checks = entry.get("required_status_checks")
        if isinstance(checks, dict):
            for context in checks.get("contexts") or []:
                if isinstance(context, str):
                    contexts.append(context)
    return contexts


# ---------------------------------------------------------------------------
# Clause implementations
# ---------------------------------------------------------------------------


def _clause_i(doc: dict) -> tuple[int, str]:
    """I2 structural well-formedness: no check twice within one list, and
    `python-ci-gate` in every queue rule's `merge_conditions` (the conjunct I1's
    empty-set escape depends on)."""
    problems: list[str] = []
    for rule in doc["queue_rules"]:
        for key in ("queue_conditions", "merge_conditions"):
            conditions = rule.get(key) or []
            seen_conditions: set[str] = set()
            seen_names: set[str] = set()
            for cond in conditions:
                if cond in seen_conditions:
                    problems.append(f"{rule['name']}.{key}: condition {cond!r} appears twice")
                seen_conditions.add(cond)
                _, name = _split_check(cond)
                if name is not None:
                    if name in seen_names:
                        problems.append(f"{rule['name']}.{key}: check {name!r} named twice")
                    seen_names.add(name)
        merge_names = _success_names(rule.get("merge_conditions") or [])
        if HEAVY_CONTEXT not in merge_names:
            problems.append(
                f"{rule['name']}: {HEAVY_CONTEXT} is not a check-success in merge_conditions"
            )
    if problems:
        return EXIT_DIVERGED, "; ".join(problems)
    return EXIT_OK, "no duplicate check within a list; python-ci-gate gates every merge"


def _clause_ii(doc: dict) -> tuple[int, str]:
    """I2 mode-aware list structure. An UNRECOGNISED mode is exit 2 (cycle 5)."""
    problems: list[str] = []
    for rule in doc["queue_rules"]:
        mode = _effective_mode(rule)
        if mode not in KNOWN_INJECTION_MODES:
            raise GuardUnreadable(
                f"rule {rule['name']!r}: unrecognised branch_protection_injection_mode {mode!r}"
            )
        queue = set(rule.get("queue_conditions") or [])
        merge = set(rule.get("merge_conditions") or [])
        if mode == "queue":
            extra = sorted(merge - queue)
            if extra:
                problems.append(
                    f"{rule['name']}: mode=queue requires merge_conditions ⊆ queue_conditions; "
                    f"not in queue_conditions: {extra}"
                )
        else:  # merge
            overlap = sorted(queue & merge)
            if overlap:
                problems.append(
                    f"{rule['name']}: mode=merge requires queue ∩ merge == ∅; overlap: {overlap}"
                )
    if problems:
        return EXIT_DIVERGED, "; ".join(problems)
    return EXIT_OK, "every rule's list structure matches its effective injection mode"


def _clause_iii(doc: dict) -> tuple[int, str]:
    """I3: every `check-*` condition is `check-success` — scoped to check
    conditions only (`base=main` / `-draft` are legitimate non-check entries)."""
    problems: list[str] = []
    for rule in doc["queue_rules"]:
        for key in ("queue_conditions", "merge_conditions"):
            for cond in rule.get(key) or []:
                kind, _ = _split_check(cond)
                if kind is not None and kind != "check-success":
                    problems.append(f"{rule['name']}.{key}: {cond!r} is not `check-success`")
    if problems:
        return EXIT_DIVERGED, "; ".join(problems)
    return EXIT_OK, "every check-* condition is check-success"


def _clause_iv(doc: dict, record: dict | None) -> tuple[int, str]:
    """I2b: under `merge` injection the five cheap contexts are explicit
    `check-success` ENTRY conditions (the merge-time injected path accepts
    neutral/skipped — the named residual of E1)."""
    active = [rule for rule in doc["queue_rules"] if _effective_mode(rule) == "merge"]
    if not active:
        return EXIT_OK, "no rule uses merge injection — clause not active"
    if record is None:
        raise GuardUnreadable("merge injection is active but the gate record is absent")
    required = record.get("required_contexts")
    if not isinstance(required, list) or not required:
        raise GuardUnreadable("the gate record carries no required_contexts")
    cheap = sorted(set(required) - {HEAVY_CONTEXT})
    problems: list[str] = []
    for rule in active:
        entry = set(_success_names(rule.get("queue_conditions") or []))
        missing = sorted(c for c in cheap if c not in entry)
        if missing:
            problems.append(
                f"{rule['name']}: merge injection requires {missing} as explicit "
                "check-success entry conditions"
            )
    if problems:
        return EXIT_DIVERGED, "; ".join(problems)
    return EXIT_OK, f"merge injection: {cheap} are explicit entry conditions"


def _clause_v(doc: dict) -> tuple[int, str]:
    """The `autoqueue` ↔ `auto_merge_conditions` schema exclusion: setting both is
    rejected by Mergify's schema (`.mergify.yml` documents this decision)."""
    has_autoqueue = [rule["name"] for rule in doc["queue_rules"] if rule.get("autoqueue") is not None]
    mps = doc.get("merge_protections_settings")
    auto_merge = mps.get("auto_merge_conditions") if isinstance(mps, dict) else None
    if has_autoqueue and auto_merge is not None:
        return (
            EXIT_DIVERGED,
            f"rules {has_autoqueue} set `autoqueue` while auto_merge_conditions is set — "
            "the two are mutually exclusive by schema",
        )
    return EXIT_OK, "autoqueue and auto_merge_conditions are not both set"


def _clause_vi(doc: dict, emitters: list[dict]) -> tuple[int, str]:
    """Emission (structural): every check named in either list maps to a job's
    effective name in a workflow with a `pull_request` trigger."""
    required = sorted({name for _, name in _all_check_pairs(doc["queue_rules"])})
    pr_names = {e["name"] for e in emitters if e["has_pr"]}
    missing = [name for name in required if name not in pr_names]
    if missing:
        return (
            EXIT_DIVERGED,
            f"named checks with no pull_request-emitting workflow job: {missing}",
        )
    return EXIT_OK, f"{required} are emitted on a pull_request workflow"


def _clause_viii_a(root: Path, record: dict | None) -> tuple[int, str]:
    """I10 — the HEAD-COMPUTED gate digest (TH6's silence detector)."""
    if record is None:
        raise GuardUnreadable(f"{RECORD_REL} is absent — the gate definition is unattested")
    head_digest = gate_digest(root)
    recorded = record.get("gate_digest")
    if recorded != head_digest:
        return (
            EXIT_DIVERGED,
            "gate_digest(head) != record.gate_digest — the gate definition changed; "
            "re-cut with `--recut` (this is the documented fix path, never `--admin`)",
        )
    verified_at = _parse_iso(record.get("verified_at"), f"{RECORD_REL}.verified_at")
    age = _now() - verified_at
    if age > timedelta(days=RECORD_FRESH_DAYS):
        return (
            EXIT_DIVERGED,
            f"record verified_at is {age.days}d old (> {RECORD_FRESH_DAYS}d); re-cut with `--recut`",
        )
    return EXIT_OK, f"gate_digest matches head and was verified {_iso(verified_at)}"


def _clause_viii_b(root: Path, record: dict | None) -> tuple[int, str]:
    """I11 — the declaration home cannot become silently live."""
    readers = _settings_readers(root)
    if readers:
        return (
            EXIT_DIVERGED,
            f"a workflow/test/tool reads {SETTINGS_REL} as an authoritative source: {readers}",
        )
    consistent = bool(record.get("settings_home_consistent")) if record else False
    if not consistent:
        return EXIT_OK, "no reader of the declaration home; equality check inert (D9 pending)"
    try:
        declared = sorted(_settings_contexts(root))
    except GuardUnreadable as exc:
        return EXIT_DIVERGED, f"settings_home_consistent=true but {exc}"
    required = sorted(record.get("required_contexts") or [])
    if declared != required:
        return (
            EXIT_DIVERGED,
            f"{SETTINGS_REL} declares {declared} but the record's required set is {required}",
        )
    return EXIT_OK, f"{SETTINGS_REL} agrees with the record ({required})"


# ---------------------------------------------------------------------------
# Static runner
# ---------------------------------------------------------------------------


def run_static(root: Path, record_path: Path | None = None) -> tuple[int, list[str]]:
    """Run every numbered clause. Returns (exit_code, output lines).

    Exit code is the aggregate: a clause that cannot read its evidence (2) is
    never downgraded by other clauses passing.
    """
    lines: list[str] = []
    root = Path(root)
    try:
        doc = _load_mergify(root)
        record = _load_record(root, record_path)
        docs = _workflow_docs(root)
        emitters = _emitters(docs)
    except GuardUnreadable as exc:
        lines.append(f"(config) FAIL [2] {exc}")
        return EXIT_UNAVAILABLE, lines

    clauses: list[tuple[str, Callable[[], tuple[int, str]]]] = [
        ("i", lambda: _clause_i(doc)),
        ("ii", lambda: _clause_ii(doc)),
        ("iii", lambda: _clause_iii(doc)),
        ("iv", lambda: _clause_iv(doc, record)),
        ("v", lambda: _clause_v(doc)),
        ("vi", lambda: _clause_vi(doc, emitters)),
        ("vii", lambda: _validate_union_protection(root, docs)),
        (
            "viii",
            lambda: _combine(
                _clause_viii_a(root, record),
                _clause_viii_b(root, record),
            ),
        ),
    ]
    worst = EXIT_OK
    for clause_id, fn in clauses:
        try:
            code, detail = fn()
        except GuardUnreadable as exc:
            code, detail = EXIT_UNAVAILABLE, str(exc)
        worst = max(worst, code)
        lines.append(f"{clause_id} {'PASS' if code == 0 else 'FAIL'} [{code}] {detail}")
    return worst, lines


def _combine(*results: tuple[int, str]) -> tuple[int, str]:
    worst = max(code for code, _ in results)
    detail = "; ".join(text for _, text in results)
    return worst, detail


def _print_result(mode: str, code: int, lines: list[str]) -> None:
    for line in lines:
        print(line)
    print(f"{mode} RESULT: {RESULT_TOKEN[code]}")


# ---------------------------------------------------------------------------
# Live runner (I1) — operational, admin credential, NOT run by CI
# ---------------------------------------------------------------------------


def _gh_api(path: str) -> Any:
    proc = subprocess.run(
        ["gh", "api", path], capture_output=True, text=True, check=False
    )
    if proc.returncode != 0:
        raise GuardUnreadable(f"gh api {path} failed: {proc.stderr.strip()[:200]}")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise GuardUnreadable(f"gh api {path} returned non-JSON") from exc


def _repo_slug(explicit: str | None) -> str:
    if explicit:
        return explicit
    proc = subprocess.run(
        ["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode == 0 and proc.stdout.strip():
        return proc.stdout.strip()
    raise GuardUnreadable("cannot resolve the repository slug; pass --repo")


def _main_sha(slug: str) -> str | None:
    try:
        data = _gh_api(f"repos/{slug}/commits/main")
        sha = data.get("sha") if isinstance(data, dict) else None
        return sha if isinstance(sha, str) else None
    except GuardUnreadable:
        return None


def _write_record(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _live_required_contexts(fetch: Callable[[str], Any], slug: str) -> list[str]:
    protection = fetch(f"repos/{slug}/branches/main/protection")
    if not isinstance(protection, dict):
        raise GuardUnreadable("branch protection read is not a mapping")
    checks = protection.get("required_status_checks")
    if not isinstance(checks, dict):
        raise GuardUnreadable("branch protection carries no required_status_checks")
    contexts = checks.get("contexts")
    if not isinstance(contexts, list) or not all(isinstance(c, str) for c in contexts):
        raise GuardUnreadable("required_status_checks.contexts is not a list of strings")
    return contexts


def run_live(
    root: Path,
    record_path: Path | None = None,
    slug: str | None = None,
    fetch: Callable[[str], Any] | None = None,
    write: bool = True,
) -> tuple[int, list[str]]:
    """I1: the live required set vs the check names the queue config enforces.

    UNAVAILABLE (no admin credential / read failure) is NON-CLEAN and is recorded
    as such, never as satisfied.
    """
    lines: list[str] = []
    root = Path(root)
    fetch = fetch or _gh_api
    try:
        doc = _load_mergify(root)
    except GuardUnreadable as exc:
        lines.append(f"(config) FAIL [2] {exc}")
        return EXIT_UNAVAILABLE, lines
    lhs = sorted({name for _, name in _all_check_pairs(doc["queue_rules"])})
    try:
        resolved = _repo_slug(slug)
        live = sorted(set(_live_required_contexts(fetch, resolved)))
    except GuardUnreadable as exc:
        lines.append(f"live read UNAVAILABLE: {exc}")
        if write and record_path is not None and record_path.is_file():
            _record_live_attempt(record_path, root, "UNAVAILABLE", None, None)
        return EXIT_UNAVAILABLE, lines
    if lhs == live:
        lines.append(f"live required contexts == the config's check set ({live})")
        if write and record_path is not None:
            _record_live_attempt(record_path, root, "SATISFIED", live, resolved)
        return EXIT_OK, lines
    lines.append(f"DIVERGED: config enforces {lhs} but the live required set is {live}")
    if write and record_path is not None:
        _record_live_attempt(record_path, root, "DIVERGED", live, resolved)
    return EXIT_DIVERGED, lines


def _record_live_attempt(
    path: Path,
    root: Path,
    result: str,
    observed: list[str] | None,
    slug: str | None,
) -> None:
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        record = {}
    record["live_result"] = result
    record["live_checked_at"] = _iso(_now())
    if observed is not None:
        record["live_observed_contexts"] = observed
    if result == "SATISFIED" and observed is not None:
        # A satisfied live read IS a re-cut: it re-attests the required set.
        record["required_contexts"] = observed
        record["live_verified_at"] = _iso(_now())
        record["verified_at"] = _iso(_now())
        record["gate_digest"] = gate_digest(root)
        record["read_sha"] = _main_sha(slug) if slug else None
    _write_record(path, record)


def run_recut(root: Path, record_path: Path | None = None) -> tuple[int, list[str]]:
    """Recompute I10's digest from HEAD and refresh `verified_at` (fix path)."""
    root = Path(root)
    path = record_path if record_path is not None else root / RECORD_REL
    if not path.is_file():
        return EXIT_UNAVAILABLE, [f"{path} is absent — nothing to re-cut"]
    try:
        record = _load_record(root, path)
    except GuardUnreadable as exc:
        return EXIT_UNAVAILABLE, [str(exc)]
    assert record is not None
    record["gate_digest"] = gate_digest(root)
    record["verified_at"] = _iso(_now())
    record.setdefault("settings_home_consistent", False)
    _write_record(path, record)
    return EXIT_OK, [f"re-cut: gate_digest={record['gate_digest']} verified_at={record['verified_at']}"]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--static", action="store_true", help="run the static clauses (CI)")
    mode.add_argument("--live", action="store_true", help="I1 — needs an admin credential")
    mode.add_argument("--recut", action="store_true", help="re-cut I10's digest from HEAD")
    mode.add_argument("--print-digest", action="store_true", help="print the head gate_digest")
    mode.add_argument("--clause-inventory", action="store_true", help="print the clause ids")
    parser.add_argument("--root", default=str(REPO), help="repository root to inspect")
    parser.add_argument("--record", default=None, help="path to the gate record JSON")
    parser.add_argument("--repo", default=None, help="owner/name for --live")
    parser.add_argument("--no-write", action="store_true", help="do not persist a live read")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    record_path = Path(args.record).resolve() if args.record else root / RECORD_REL

    if args.clause_inventory:
        print(" ".join(CLAUSE_IDS))
        return EXIT_OK
    if args.print_digest:
        try:
            print(gate_digest(root))
        except GuardUnreadable as exc:
            print(f"UNREADABLE: {exc}", file=sys.stderr)
            return EXIT_UNAVAILABLE
        return EXIT_OK
    try:
        if args.live:
            code, lines = run_live(
                root, record_path, slug=args.repo, write=not args.no_write
            )
            _print_result("LIVE", code, lines)
            return code
        if args.recut:
            code, lines = run_recut(root, record_path)
            _print_result("RECUT", code, lines)
            return code
        code, lines = run_static(root, record_path)
        _print_result("STATIC", code, lines)
        return code
    except GuardUnreadable as exc:
        # Fail closed: an exception on any path is UNREADABLE evidence, never a
        # pass and never a traceback that a caller could mistake for a crash.
        _print_result("LIVE" if args.live else "STATIC", EXIT_UNAVAILABLE, [str(exc)])
        return EXIT_UNAVAILABLE


if __name__ == "__main__":
    sys.exit(main())
