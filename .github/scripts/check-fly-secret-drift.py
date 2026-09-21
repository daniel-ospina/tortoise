#!/usr/bin/env python3
"""Fly secret *provenance* guard for the hosted app (#4126).

Fail-closed pre-deploy check: every secret on the Fly app must have a declared
managing source, so no production env var can exist only on Fly.

The incident this exists for (#4126, 2026-09-19): ``TORTOISE_SESSION_LLM_MODEL``
and ``OPENROUTER_API_KEY`` were hand-set on ``tortoise-y4mjjq`` with **no
managing source in version control**. The deployed key 403'd on every
extraction call, ``POST /v1/sessions`` answered ``200`` with
``status: "partial"``/``extracted: 0``, and 50/50 recent sessions built no
memory. Nothing in CI could see the drift, because the drift is by construction
invisible: a name that no workflow and no file mentions cannot be compared
against anything.

Contract
--------
``.github/scripts/fly-managed-secrets.txt`` is the declaration of intent — one
``<NAME> <source>`` line per Fly secret. The check is bidirectional: a
declaration the deploy no longer honours is a violation too, so the manifest
cannot rot into a comfortable lie.

Sources (see the manifest header for the full rationale):

``gh-secret:<GH_NAME>``  ``deploy-hosted.yml`` propagates it from the GitHub
                         Actions secret ``<GH_NAME>`` (not always the same name).
                         Propagated *unconditionally* is the managed state; a
                         name the workflow only assigns behind a
                         ``[ -n "${{ secrets.X }}" ] &&`` guard is CONDITIONAL —
                         and if that name is on Fly while ``GH_SECRETS_PRESENT``
                         does not carry ``<GH_NAME>``, the deploy skips the
                         assignment every run and Fly keeps a hand-managed value:
                         that is the #4126 defect and it FAILS.
``workflow``             the workflow sets it from non-secret context
                         (``${GITHUB_SHA}``, a composed flag, a deliberately
                         versioned default that is always assigned).
``fly-toml-env``         an assigned key in ``fly.toml``'s ``[env]`` table — and
                         NOT assigned by the deploy (a Fly secret shadows
                         ``[env]``, so a workflow assignment would make the
                         versioned value dead).
``unmanaged``            present on Fly with NO managing source. **This is the
                         #4126 defect, so it FAILS the gate.** The token is kept
                         because it names the state precisely: an operator must
                         declare the real source (``gh-secret:…`` / ``workflow`` /
                         ``fly-toml-env``) or remove the Fly secret. A gate that
                         passes on a Fly-only variable is the fail-open #4126 was
                         filed to close.
``fly-only:<issue-ref>``  a DELIBERATELY out-of-band secret: managed outside
                         BOTH GitHub and ``fly.toml`` on purpose, with the named
                         issue carrying the recorded decision (an ``OVERRIDES:``
                         marker states what the ruling overrides). The ref must
                         be a WELL-FORMED, non-zero issue number — ``#<N>`` (this
                         repo) or ``<owner>/<repo>#<N>``. ``#0``, a non-ASCII
                         digit, an empty ref, or trailing junk is a manifest this
                         checker cannot read: exit 2, never a pass — the
                         exception is only meaningful because of the issue it
                         names. This is what distinguishes it from ``unmanaged``:
                         `unmanaged` means NO source was declared; `fly-only`
                         means a source was deliberately REFUSED, and here is the
                         ruling. A bare ``unmanaged`` entry still FAILS, and a
                         ``fly-only`` name that is absent from Fly, that the
                         deploy assigns, or that is a ``fly.toml [env]`` key also
                         FAILS (the exception is for a live, real, unmanaged
                         name).
                         ⚠️ SHAPE ONLY — the checker is hermetic and offline, so
                         it verifies the ref is a well-formed issue number, NOT
                         that the issue exists or carries the ruling: a
                         well-formed ref to a nonexistent issue passes. The
                         existence half is a reviewer's job, and the `OVERRIDES:`
                         marker on the named issue is what makes it checkable.

Exit codes (mirrors check-migration-drift / check-fly-machines-guard):
  0 — every Fly secret is declared and every declaration is honoured
  1 — drift found (undeclared Fly secret, a declaration the deploy does not
      honour, a name whose declaration names NO managing source, or a guarded
      declaration whose GitHub secret does not exist)
  2 — could not determine state (missing/unparsable manifest, unreadable or
      EMPTY secret list, malformed entry — including a `fly-only:` declaration
      whose issue ref is empty or malformed — absent GH_SECRETS_PRESENT, a
      payload that cannot be read). Fail-closed: an unreadable state is never
      clean.

Env seams (all optional; used by the hermetic test suite):
  FLY_SECRETS_FILE          fixture path holding the ``--json`` secret list
  FLY_MANAGED_SECRETS_FILE  manifest path override
  FLY_APP                   app name (default: fly.toml ``app``)
  FLY_TOML                  fly.toml path override
  DEPLOY_WORKFLOW           deploy workflow path override
  FLY_SECRETS_CMD           secret-list command override
                            (default ``flyctl secrets list --app <app> --json``)
  GH_SECRETS_PRESENT        space-separated names of the GitHub Actions secrets
                            the CURRENT run has. REQUIRED — the deploy step
                            builds it from the ``${{ secrets.X }}`` values it
                            propagates, because no CI token can list repo
                            secrets. Unset is exit 2, never "all present".
  FLY_STUB_TIMEOUT          seconds before the stubbed payload run is treated as
                            unclassifiable (default 30) — a test seam.

Requires PyYAML (a project dependency) to read the workflow's STRUCTURE — see
``_yaml_module``: an `if:` or an `env:` on the payload step or its job is a YAML
key, not shell text, and guessing at it from line positions certified a
may-never-run step at exit 0. The deploy workflow provisions it with
``uv run --no-project --with pyyaml``.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_MANIFEST = REPO_ROOT / ".github" / "scripts" / "fly-managed-secrets.txt"
DEFAULT_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "deploy-hosted.yml"


def read_gh_secret_presence() -> set[str]:
    """The GitHub Actions secrets the runner actually HAS, as a name set.

    The guard cannot read GitHub secret existence itself (a workflow run's token
    cannot list repo secrets), so the deploy step states it in
    ``GH_SECRETS_PRESENT`` — built from the same ``${{ secrets.X }}`` values it
    propagates. Without it a ``gh-secret:<X>`` declaration whose GitHub secret
    does not exist is indistinguishable from one whose secret does, and the gate
    exits 0 while the deploy silently skips the assignment and Fly keeps a
    hand-managed value: exactly the #4126 incident (#4259 review P1).

    FAIL-CLOSED: an unset variable is an unreadable state (exit 2), never "all
    present" — a missing probe must not silently reopen the hole. A name the
    probe omits reads as ABSENT, so forgetting a probe line fails the deploy for
    that name rather than passing it.
    """
    raw = os.environ.get("GH_SECRETS_PRESENT")
    if raw is None:
        raise ValueError(
            "GH_SECRETS_PRESENT is not set — the deploy step must state which GitHub "
            "Actions secrets this run can see (a gh-secret declaration cannot be "
            "verified against GitHub otherwise)"
        )
    return set(raw.split())


# `NAME=…` tokens in a captured `flyctl secrets set` payload.
_ASSIGN_RE = re.compile(r"\b([A-Z][A-Z0-9_]{2,})=([^\s]*)")
# A comment leader: `#` at the start of a line or after whitespace. NOT any `#` —
# a `fly-only:#661` issue reference is data, and a plain `split("#", 1)` turned
# the reference into the empty string, failing the whole manifest as unreadable.
# A trailing `  # note` still strips, because its `#` follows whitespace.
_COMMENT_RE = re.compile(r"(?:^|\s)#")
# A `fly-only:<issue-ref>` argument — the issue that carries the recorded
# decision allowing a deliberately out-of-band secret. `<#N>` (this repo) or
# `<owner>/<repo>#<N>`. The number is REQUIRED and must be a well-formed,
# NON-ZERO ASCII issue number:
#   `fly-only:`, `fly-only:#`, `fly-only:#abc`, `fly-only:#661abc`, `fly-only:661`
#   and a bare `owner/repo` are all unreadable state (exit 2);
#   `#[1-9][0-9]*` rather than `#\d+` because `\d` matches UNICODE decimal
#   digits — `#\u0666\u0666\u0661` (Arabic-Indic 661) is not an issue number and
#   must not be accepted — and because issue numbers start at 1, so `#0`
#   names no issue. Both were accepted before this was tightened (found in the
#   #4523 review as a T2 fail-open).
#   Each `owner`/`repo` part must START with an alphanumeric, so `..`, `-x` and
#   `/repo` are not accepted as an owner/repo prefix (a `..` prefix that parsed
#   as an owner would be a malformed ref that still exited 0).
# The category exists only to point at a ruling — without a well-formed ref it
# is indistinguishable from `unmanaged`, which FAILS.
_FLY_ONLY_OWNER_REPO = r"[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*"
_FLY_ONLY_REF_RE = re.compile(rf"^(?:{_FLY_ONLY_OWNER_REPO})?#[1-9][0-9]*$")
# Substituted for `${{ secrets.X }}` when X is treated as present. The delimiters
# are control characters that cannot occur in a GitHub/secret NAME, so a marker is
# never a substring of another marker: `\x01SEC:FOO\x02` does NOT match inside
# `\x01SEC:FOO___BAR\x02`, which the `__SEC_FOO__` spelling did (#4126 review —
# the collision attributed FOO's declaration to a different secret's value).
_MARK_OPEN = "\x01SEC:"
_MARK_CLOSE = "\x02"
# The stub records one argv per invocation on fd 3: args separated by ARG_SEP,
# the invocation terminated by RECORD_SEP.
_STUB_ARG_SEP = "\x1e"
_STUB_RECORD_SEP = "\x1d"
# The propagation step's shell is located by its `fly secrets set` call. Both the
# `flyctl` spelling and the `fly` alias are matched, and global flags may precede
# the subcommand (`flyctl --app X secrets set …`) — Fly accepts them, so a missing
# match would make the block invisible to the reverse-completeness half.
_PAYLOAD_CMD_RE = re.compile(
    r"\bfly(?:ctl)?(?:\s+-{1,2}[\w-]+(?:=\S+|\s+\S+)?)*\s+secrets\s+set\b"
)
_SECRET_REF_RE = re.compile(r"secrets\.([A-Za-z0-9_]+)")
# The Actions template reference substituted before the shell runs.
_SECRET_TMPL_RE = re.compile(r"\$\{\{\s*secrets\.([A-Za-z0-9_]+)\s*\}\}")


def _yaml_module():
    """PyYAML, or a fail-closed error naming how the gate is run."""
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover — exercised by the message only
        raise ValueError(
            "PyYAML is required to read the deploy workflow's structure — a YAML `if:` "
            "on a step or job is not part of the `run:` shell, so execution cannot see "
            "it and line positions cannot be trusted. The deploy workflow runs this "
            f"gate with `uv run --no-project --with pyyaml python3`: {exc}"
        ) from exc
    return yaml


# The step whose `run:` invokes this gate — used to tell the gate's OWN job from
# another job (see `_gate_job`).
_GATE_SELF_RE = re.compile(r"check-fly-secret-drift")


def _gate_job(doc: dict) -> str | None:
    """The job THIS gate runs in — the one whose step invokes this script.

    The distinction matters for a job-level `if:`. A payload in this gate's OWN
    job is certified only when the gate actually ran (they run together), so a
    job-level `if:` here is irrelevant: if the job is skipped, NOTHING is
    certified. A payload in ANOTHER job is certified by a gate that did run, so
    that job's `if:` can skip the propagation while the gate stays green —
    which is why only ANOTHER job's job-level `if:` is refused (#4259 review).
    "None" (this gate is not wired into the workflow being checked) resolves
    strict: every payload job counts as another job.
    """
    for job_name, job in (doc.get("jobs") or {}).items():
        if not isinstance(job, dict):
            continue
        for step in job.get("steps") or []:
            if (
                isinstance(step, dict)
                and isinstance(step.get("run"), str)
                and _GATE_SELF_RE.search(step["run"])
            ):
                return str(job_name)
    return None


def extract_propagation_blocks(text: str) -> list[tuple[str, dict[str, str]]]:
    """Every step whose `run:` builds a Fly secrets payload, with its step env.

    The workflow is PARSED, not scanned by line position. Everything that can make
    a payload conditional on something the shell cannot see is a YAML KEY — an
    `if:` on the step, an `if:` on the JOB, an `env:` value a guard reads — and a
    positional scan is one formatting choice away from missing it: `if:` with its
    value on the next line, `if :`, `"if":`, and `if:` written AFTER `run:` all
    parse to the same step dict (four spellings certified a may-never-run step at
    exit 0, #4259 review).

    ALL payload steps are returned, not just the first: the contract is
    bidirectional, and a second propagation step is as much a managing source as
    the first.

    Raises ValueError when the workflow is not parseable YAML, when no payload step
    exists (fail-closed: a guard that cannot read the payload would silently certify
    any fleet), and when a payload step or its job carries a YAML `if:` — an
    UNVERIFIABLE state the checker cannot evaluate, so it must not certify names
    from a step that may never run. That is the #4126 state (a propagation that
    silently does nothing, with the gate green), and the remedy is to move the
    condition INSIDE the `run:` shell, where execution sees the guard.
    """
    yaml = _yaml_module()
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ValueError(f"the deploy workflow is not parseable YAML: {exc}") from exc
    if not isinstance(doc, dict):
        raise ValueError("the deploy workflow is not a YAML mapping")
    blocks: list[tuple[str, dict[str, str]]] = []
    gate_job = _gate_job(doc)
    for job_name, job in (doc.get("jobs") or {}).items():
        if not isinstance(job, dict):
            continue
        for index, step in enumerate(job.get("steps") or []):
            if not isinstance(step, dict):
                continue
            run = step.get("run")
            if not isinstance(run, str) or not _PAYLOAD_CMD_RE.search(run):
                continue
            label = step.get("name") or f"#{index}"
            if "if" in step:
                raise ValueError(
                    f"the step that sets the Fly secrets (job {job_name!r}, {label!r}) "
                    "carries a step-level `if:` — the checker cannot evaluate YAML "
                    "conditions, so it cannot certify that the payload runs. Move the "
                    "condition inside the `run:` shell (where the execution-based "
                    "classifier can see the guard) or drop it"
                )
            if "if" in job and str(job_name) != gate_job:
                raise ValueError(
                    f"the job {job_name!r} that sets the Fly secrets carries a "
                    "job-level `if:`, and it is NOT the job this gate runs in — so the "
                    "gate can be green on a run where that job never propagates. Move "
                    "the condition into the step's `run:` shell (where the "
                    "execution-based classifier can see the guard), run the gate in "
                    "that job, or drop it"
                )
            # A `needs:` dependency can skip the propagation exactly as an `if:`
            # can, and the gate stays green while it does — the same fail-open
            # shape, so the same rule: only a payload in the gate's OWN job is
            # safe (there the gate is skipped too, so nothing is certified).
            if job.get("needs") and str(job_name) != gate_job:
                raise ValueError(
                    f"the job {job_name!r} that sets the Fly secrets has a `needs:` "
                    "dependency, and it is NOT the job this gate runs in — a skipped "
                    "or failed dependency skips the propagation while the gate stays "
                    "green. Run the gate in that job, or drop the dependency"
                )
            # The workflow's, the job's and the step's `env:` are all inherited by
            # the payload shell, so a guard may read them (`[ -z "$VAR" ]`). They
            # are modelled — with secrets substituted exactly like the script —
            # because a value the stub env lacks would make the run sample disagree
            # with the deploy. Narrowest wins: step over job over workflow root.
            step_env: dict[str, str] = {}
            for source in (doc.get("env"), job.get("env"), step.get("env")):
                if isinstance(source, dict):
                    step_env.update({str(k): str(v) for k, v in source.items()})
            blocks.append((run, step_env))
    if not blocks:
        raise ValueError("no `fly secrets set` invocation found in the deploy workflow")
    return blocks


def _stub_records(raw: str) -> list[list[str]]:
    """Parse the stub's argv records (args SEP-joined, record RECORD_SEP-terminated)."""
    groups: list[list[str]] = []
    for record in raw.split(_STUB_RECORD_SEP):
        record = record.strip("\n")
        if not record:
            continue
        groups.append(record.split(_STUB_ARG_SEP))
    return groups


def _payload_assignments(groups: list[list[str]]) -> dict[str, str]:
    """The ``NAME=VALUE`` arguments of every ``flyctl secrets set`` invocation.

    Read from the STUB's own argv records (fd 3) — never from the block's stdout.
    The step's `run:` body is comment-dense and carries ``echo`` notices, and an
    ``echo "NAME=value"`` on the same stream used to be certified as a real Fly
    assignment (#4126 review: a payload could forge an assignment with an echo).
    """
    captured: dict[str, str] = {}
    for argv in groups:
        # Global flags may precede the subcommand (`flyctl --app X secrets set …`),
        # so the `secrets set` pair is located by content, never by position.
        try:
            index = argv.index("secrets")
        except ValueError:
            continue
        if index + 1 >= len(argv) or argv[index + 1] != "set":
            continue
        for token in argv[index + 2:]:
            match = _ASSIGN_RE.fullmatch(token)
            if match:
                captured[match.group(1)] = match.group(2)
    return captured


def _capture_payload(
    script: str, present: dict[str, str], step_env: dict[str, str] | None = None
) -> dict[str, str]:
    """Run the propagation shell with a stubbed flyctl; return ``NAME -> value``.

    EXECUTION, not parsing. The block is the ground truth, and every shell
    spelling (``&&``, ``test``, ``[[``, ``case``, a `while`, a YAML step-level
    ``if:``, an accumulator rename, ``+=``, a backslash continuation) is handled
    for free. Four review rounds each found a new spelling the parser missed; the
    parser is gone.

    Hermetic and value-blind: ``flyctl``/``fly`` are shell stubs on a PATH that
    contains nothing else, so no real Fly call can happen, and the GitHub secret
    placeholders are replaced by dummy markers — the guard never touches a
    credential. ``secrets.X`` is the marker ``\x01SEC:X\x02`` when the caller says
    X is present, else empty.

    The stub reports its argv on **fd 3**, a dedicated file descriptor opened by
    the wrapper shell and never mentioned to the block. The block's stdout is not
    read at all, so a payload cannot certify itself with an ``echo``.
    """

    def substitute(match: re.Match[str]) -> str:
        name = match.group(1)
        return f"{_MARK_OPEN}{name}{_MARK_CLOSE}" if name in present else ""

    rendered = _SECRET_TMPL_RE.sub(substitute, script)
    # Resolve the interpreter BEFORE the child's PATH is narrowed to the stubs.
    bash = shutil.which("bash") or "/bin/bash"
    with tempfile.TemporaryDirectory(prefix="fly-secret-stub-") as stub_dir:
        argv_log = Path(stub_dir) / "argv.records"
        argv_log.touch()
        for binary in ("flyctl", "fly"):
            path = Path(stub_dir) / binary
            path.write_text(
                "#!/bin/sh\n"
                'for _arg in "$@"; do printf \'%s\\036\' "$_arg" >&3; done\n'
                "printf '\\035' >&3\n"
            )
            path.chmod(0o755)
        env = {
            "PATH": stub_dir,
            "HOME": stub_dir,
            "GITHUB_SHA": "fixture-sha",
        }
        # The step's and the job's `env:` are inherited by the payload shell, and a
        # guard may read one (`[ -z "$VAR" ]`). They are modelled with the same
        # secret substitution as the script, so the run sample cannot disagree with
        # the deploy over a value the stub env happened to lack. `setdefault` keeps
        # PATH/HOME/GITHUB_SHA authoritative for the harness itself.
        for key, value in (step_env or {}).items():
            env.setdefault(key, _SECRET_TMPL_RE.sub(substitute, value))
        # fd 3 is opened by the wrapper and inherited across `exec`, so the stub
        # has somewhere private to write. `bash -e -u` mirrors GitHub's default
        # shell for a `run:` block, plus NOUNSET: a payload that reads a variable
        # the harness does not model (a `$GITHUB_ENV` value written by an earlier
        # step, a runner-provided variable) would otherwise be classified against
        # an empty value the real run may not have — a guard could flip and the
        # gate certify an assignment the deploy skips, or the reverse. `-u` turns
        # that unmodellable read into a non-zero exit, i.e. the fail-closed
        # could-not-determine state, never a green certificate. It is exec'd after
        # the harness is set up so the narrowed PATH cannot hide it.
        command = f"exec 3>{shlex.quote(str(argv_log))}; exec {shlex.quote(bash)} -e -u"
        # A payload that hangs is an unreadable state: it must fail closed, never
        # escape as an uncaught TimeoutExpired (Python exits 1 — the BYPASSABLE
        # drift code). The seam keeps the hermetic test fast.
        timeout = float(os.environ.get("FLY_STUB_TIMEOUT") or 30)
        try:
            proc = subprocess.run(
                [bash, "-c", command],
                input=rendered,
                capture_output=True,
                text=True,
                env=env,
                timeout=timeout,
            )
            # Read the stub's records BEFORE the temporary directory is torn down.
            records = _stub_records(argv_log.read_text())
        except subprocess.TimeoutExpired as exc:
            raise ValueError(
                f"the propagation shell did not finish within {timeout:g}s under the "
                "stub — a payload that hangs cannot be classified"
            ) from exc
        except OSError as exc:
            raise ValueError(f"the propagation shell could not be run: {exc}") from exc
    if "command not found" in proc.stderr:
        raise ValueError(
            "the propagation shell invoked a command this harness does not model "
            f"({proc.stderr.strip()[:200]}) — the stub PATH carries only fly/flyctl, "
            "so whether the payload assigns the Fly secret depends on an external "
            "command's output or exit status and cannot be determined. Build the "
            "payload from shell builtins and `${{ secrets.* }}` expansions, or "
            "assign the secret unconditionally"
        )
    if proc.returncode != 0:
        raise ValueError(
            f"the propagation shell failed under the stub (rc={proc.returncode}): the payload "
            "reads a variable this harness does not model (workflow/job/step "
            "`env:`, GITHUB_SHA) — a value written by an earlier step to "
            "$GITHUB_ENV, or a runner-provided variable — so whether it assigns "
            "the Fly secret cannot be determined. Put the value in an `env:` key, "
            "or assign the secret unconditionally. stderr: "
            f"{proc.stderr.strip()[:300]}"
        )
    return _payload_assignments(records)


def payload_partition(
    scripts: list[tuple[str, dict[str, str]]], gh_present: set[str]
) -> tuple[set[str], set[str], set[str], dict[str, set[str]]]:
    """``(assigned, unconditional, live, sources)`` over EVERY payload block.

    ``assigned``      names in the payload when every GitHub secret is present
    ``unconditional`` names the payload carries even with NO GitHub secret present
    ``live``          names the payload carries with the secrets the RUN actually
                      has. A name in ``assigned`` but not in ``live`` is skipped by
                      this deploy, so whatever is on Fly survives — the #4126
                      incident, whatever gates the assignment.
    ``sources[name]`` the GitHub secrets whose marker appears in the value the
                      workflow assigns to ``name`` — so a `gh-secret:<GH_NAME>`
                      declaration must name the secret that actually feeds THAT
                      variable, not merely one referenced anywhere.

    The synthetic samples are the two the guard can take cheaply, and the payload
    need not be monotone in the secret set — a ``[ -z "${{ secrets.X }}" ]`` guard
    assigns when X is ABSENT, so a name can sit in ``unconditional`` and not in
    ``assigned``. A MANAGED name is therefore the INTERSECTION (present with every
    secret AND with none); anything in the symmetric difference is guarded, i.e.
    propagation that depends on a secret. Sampling ``assigned`` alone classified a
    `-z`-guarded name as managed (#4126 review).

    CONSERVATIVE over blocks: a name is unconditional only if EVERY block that
    assigns it assigns it unconditionally. Blocks run in order and the last write
    wins, so one guarded writer is enough to leave a hand-managed value in place.
    """
    assigned: set[str] = set()
    unconditional: set[str] = set()
    live: set[str] = set()
    guarded: set[str] = set()
    sources: dict[str, set[str]] = {}
    for script, step_env in scripts:
        secret_names = sorted(set(_SECRET_REF_RE.findall(script)))
        # An `env:` value may itself carry `${{ secrets.X }}` (Actions substitutes
        # it into the step env). X need not appear in the `run:` body, so the
        # "every secret present" sample must treat it as present too — otherwise
        # a guard reading that env value flips relative to the `live` sample and a
        # propagated name is misreported STALE (#4259 review).
        sample_names = set(secret_names)
        for value in (step_env or {}).values():
            sample_names.update(_SECRET_REF_RE.findall(str(value)))
        all_present = _capture_payload(
            script, {name: name for name in sorted(sample_names)}, step_env
        )
        # The payload THIS run builds — markers only for the secrets the runner
        # actually carries. One more execution of the same shell.
        in_run = _capture_payload(script, {name: name for name in gh_present}, step_env)
        block_assigned = set(all_present)
        block_unconditional = set(_capture_payload(script, {}, step_env))
        guarded |= block_assigned - block_unconditional
        unconditional |= block_unconditional
        assigned |= block_assigned
        live |= set(in_run)
        for name, value in all_present.items():
            sources.setdefault(name, set()).update(
                s for s in sample_names if f"{_MARK_OPEN}{s}{_MARK_CLOSE}" in value
            )
    return assigned, unconditional - guarded, live, sources


def conditional_names(assigned: set[str], unconditional: set[str]) -> set[str]:
    """Names whose assignment depends on a secret — the symmetric difference.

    Anything present in only ONE of the two samples is guarded in some direction;
    only the intersection is unconditionally propagated. A `-z`-guarded name is in
    ``unconditional`` but not ``assigned``, and used to fall out of both lists
    (counted as managed, #4126 review).
    """
    return (assigned | unconditional) - (assigned & unconditional)


def _err(msg: str) -> None:
    print(f"::error::{msg}", file=sys.stderr)


def read_manifest(path: Path) -> dict[str, str]:
    """Parse ``<NAME> <source>`` lines. Raises ValueError on a malformed entry.

    The source SHAPE is validated before anything is indexed out of it. A bare
    ``gh-secret`` (no ``:``) used to reach ``source.split(":", 1)[1]`` and raise
    IndexError — an uncaught crash, so the process exited **1**, the *bypassable*
    drift code, instead of the fail-closed **2** (#4126 review).
    """
    entries: dict[str, str] = {}
    for lineno, raw in enumerate(path.read_text().splitlines(), start=1):
        marker = _COMMENT_RE.search(raw)
        line = (raw[: marker.start()] if marker else raw).strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) != 2:
            raise ValueError(f"{path.name}:{lineno}: expected '<NAME> <source>', got {raw!r}")
        name, source = parts
        kind, sep, argument = source.partition(":")
        if kind not in ("gh-secret", "workflow", "fly-toml-env", "unmanaged", "fly-only"):
            raise ValueError(
                f"{path.name}:{lineno}: unknown source {source!r} "
                "(expected gh-secret:<GH_NAME> | workflow | fly-toml-env | unmanaged | "
                "fly-only:<issue-ref>)"
            )
        if kind == "gh-secret" and not argument:
            raise ValueError(f"{path.name}:{lineno}: gh-secret needs the GitHub secret name")
        if kind == "fly-only" and not _FLY_ONLY_REF_RE.match(argument or ""):
            # The SAFEGUARD: the category cannot be used as a second spelling of
            # `unmanaged`. A ref that does not name an issue number carries no
            # ruling, so the declaration is worth no more than `unmanaged` —
            # which FAILS. Malformed is unreadable state (exit 2), never a pass.
            # The regex excludes `#0` and non-ASCII digits; see `_FLY_ONLY_REF_RE`.
            raise ValueError(
                f"{path.name}:{lineno}: fly-only needs a well-formed, non-zero issue "
                "number ('#<N>' or '<owner>/<repo>#<N>') naming the issue that carries "
                f"the recorded decision — got {source!r}. Without it the entry is "
                "`unmanaged` in disguise, which fails"
            )
        if kind not in ("gh-secret", "fly-only") and sep:
            raise ValueError(f"{path.name}:{lineno}: {kind} takes no ':' argument (got {source!r})")
        if name in entries:
            raise ValueError(f"{path.name}:{lineno}: duplicate declaration for {name}")
        entries[name] = source
    return entries


def read_fly_secret_names(app: str) -> list[str]:
    """The live secret names. A ``FLY_SECRETS_FILE`` fixture short-circuits it."""
    fixture = os.environ.get("FLY_SECRETS_FILE")
    if fixture:
        payload = json.loads(Path(fixture).read_text())
    else:
        cmd = os.environ.get("FLY_SECRETS_CMD") or f"flyctl secrets list --app {app} --json"
        proc = subprocess.run(shlex.split(cmd), capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(
                f"secret list failed (rc={proc.returncode}): {proc.stderr.strip()[:300]}"
            )
        payload = json.loads(proc.stdout)
    # Never trust the shape: a wrong shape must fail closed, not read as "no secrets".
    if not isinstance(payload, list):
        raise ValueError(f"secret list is not a JSON array: {type(payload).__name__}")
    names = []
    for item in payload:
        name = item.get("name") if isinstance(item, dict) else None
        # A non-string name is a payload the guard cannot classify. Accepting it by
        # truthiness let it crash later (`TypeError: unhashable type` — or `'<' not
        # supported between str and int` in a sort), an uncaught exception, so the
        # process exited 1, the BYPASSABLE code, instead of fail-closed 2.
        if not isinstance(name, str) or not name:
            raise ValueError(f"secret entry has no usable string name: {item!r}")
        names.append(name)
    # An EMPTY list is the same fail-open hazard as a wrong shape: every
    # `set(fly_names) - set(declared)` is empty, so a truncated payload reports
    # a perfectly clean fleet. The app has 40 secrets; zero is never a state
    # this guard may certify.
    if not names:
        raise ValueError("secret list is empty — refusing to read that as a clean fleet")
    return names


def workflow_secret_refs(text: str) -> set[str]:
    """Every ``secrets.<NAME>`` the deploy workflow references."""
    return set(_SECRET_REF_RE.findall(text))


def fly_toml_env_keys(text: str) -> set[str]:
    """Assigned keys inside ``fly.toml``'s ``[env]`` table.

    Parsed, not substring-matched: a name that appears only in a *comment* (the
    real fly.toml documents `TORTOISE_TRUST_X_FORWARDED_PROTO` that way) is not
    an assignment and must not satisfy a ``fly-toml-env`` declaration.
    """
    keys: set[str] = set()
    in_env = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_env = stripped == "[env]"
            continue
        if not in_env:
            continue
        code = line.split("#", 1)[0]
        match = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*=", code)
        if match:
            keys.add(match.group(1))
    return keys


def resolve_app(toml_path: Path) -> str:
    app = os.environ.get("FLY_APP")
    if app:
        return app
    for line in toml_path.read_text().splitlines():
        if line.strip().startswith("app") and "=" in line:
            return line.split("=", 1)[1].strip().strip('"')
    raise ValueError("cannot determine the Fly app: set FLY_APP or declare `app` in fly.toml")


def main() -> int:
    manifest_path = Path(os.environ.get("FLY_MANAGED_SECRETS_FILE") or DEFAULT_MANIFEST)
    workflow_path = Path(os.environ.get("DEPLOY_WORKFLOW") or DEFAULT_WORKFLOW)
    toml_path = Path(os.environ.get("FLY_TOML") or (REPO_ROOT / "fly.toml"))

    try:
        declared = read_manifest(manifest_path)
    except (OSError, ValueError) as exc:
        _err(f"cannot determine secret provenance: manifest unusable ({exc})")
        return 2

    try:
        app = resolve_app(toml_path)
        fly_names = read_fly_secret_names(app)
        workflow_text = workflow_path.read_text()
        env_keys = fly_toml_env_keys(toml_path.read_text())
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        _err(f"cannot determine secret provenance: {exc}")
        return 2

    secret_refs = workflow_secret_refs(workflow_text)
    try:
        # Read FIRST: the partition needs it — the payload the RUN actually builds
        # is one of its samples.
        gh_present = read_gh_secret_presence()
    except ValueError as exc:
        _err(f"cannot determine secret provenance: {exc}")
        return 2
    try:
        blocks = extract_propagation_blocks(workflow_text)
        assigned, unconditional, live, assignment_sources = payload_partition(blocks, gh_present)
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        # subprocess.SubprocessError/OSError must land HERE: escaping as an
        # uncaught exception would exit 1 — the code the deploy step translates
        # into a bypass — instead of fail-closed 2 (#4126 review).
        _err(f"cannot determine secret provenance: {exc}")
        return 2
    # Everything the payload carries that is NOT there with every GitHub secret
    # absent is conditionally propagated. Executing the block computes that
    # exactly, so no shell spelling can hide it — including the `-z` direction,
    # where the name appears ONLY in the no-secret sample.
    assigned_conditionally = conditional_names(assigned, unconditional)
    if not assigned:
        _err(
            "cannot determine secret provenance: the flyctl secrets set payload is "
            "empty when every GitHub secret is present"
        )
        return 2

    print(
        f"check-fly-secret-drift: Fly app {app} vs {manifest_path.name} "
        f"({len(fly_names)} secret(s) live, {len(declared)} declared)"
    )

    violations: list[str] = []
    conditional: list[str] = []

    # (1) An undeclared name is NEW drift — from either side of the contract.
    # Towards Fly: a var that exists only on Fly. Towards the workflow: a Fly
    # var the deploy can set that no declaration covers (it would otherwise pass
    # the run that sets it and hard-fail every run after).
    #
    # BOTH samples: a `[ -z "${{ secrets.X }}" ]` guard assigns only in the
    # no-secret run, and that assignment happens on EVERY deploy while X is
    # absent — the name is deploy-managed, so it must be declared. Sampling
    # `assigned` alone left it undetectable in either direction (#4126 review).
    # BOTH samples, plus the one the RUN builds: a name the deploy assigns only
    # under a guard NEITHER synthetic sample reproduces (e.g. `A present AND B
    # absent`) is visible only in the run sample, and it is deployed just as much
    # as any other (#4259 review).
    for name in sorted(set(fly_names) - set(declared)):
        violations.append(
            f"UNDECLARED — {name!r} is on Fly but declared nowhere in {manifest_path.name}"
        )
    for name in sorted((assigned | unconditional | live) - set(declared)):
        violations.append(
            f"UNDECLARED — {name!r} is assigned by {workflow_path.name} but declared "
            f"nowhere in {manifest_path.name}"
        )

    # (2) A declaration the deploy does not honour is stale intent. A source
    # that is only *referenced* is not propagation: the Fly variable has to be
    # assigned, or the declaration describes a name nothing actually sets.
    for name, source in sorted(declared.items()):
        kind, _, gh_name = source.partition(":")
        if kind == "gh-secret":
            if gh_name not in secret_refs:
                violations.append(
                    f"STALE DECLARATION — {name!r} is declared gh-secret:{gh_name} but "
                    "deploy-hosted.yml never references it"
                )
            elif name not in assigned:
                violations.append(
                    f"STALE DECLARATION — {name!r} is declared gh-secret:{gh_name} but "
                    f"deploy-hosted.yml never assigns the Fly variable {name}"
                )
            elif gh_name not in assignment_sources.get(name, set()):
                # Referenced somewhere is not enough: the declared secret must be
                # the one feeding THIS variable.
                violations.append(
                    f"STALE DECLARATION — {name!r} is declared gh-secret:{gh_name} but "
                    f"that secret does not feed the assignment of {name} "
                    f"(found: {sorted(assignment_sources.get(name) or [])})"
                )
            elif name in assigned_conditionally:
                # Guarded, and the guard's inputs are present in THIS run (the
                # per-run skip check below is the authority on that): propagated
                # on every deploy, so not managed-unconditionally but not drift
                # either. CONSERVATIVE: any guarded assignment wins over an
                # unguarded one.
                conditional.append(name)
        elif kind == "workflow":
            if name not in assigned:
                violations.append(
                    f"STALE DECLARATION — {name!r} is declared workflow-set but "
                    "deploy-hosted.yml never assigns it"
                )
            elif name in assigned_conditionally:
                conditional.append(name)
        elif kind == "fly-toml-env":
            if name not in env_keys:
                violations.append(
                    f"STALE DECLARATION — {name!r} is declared fly-toml-env but is not an "
                    "assigned key in fly.toml's [env] table"
                )
            elif name in fly_names:
                # A Fly SECRET shadows [env], so the version-controlled value is
                # not what the app reads: the declaration describes a source that
                # loses to an unmanaged one.
                violations.append(
                    f"STALE DECLARATION — {name!r} is declared fly-toml-env but is also a "
                    "Fly secret, which shadows [env] (declare the real source "
                    "instead)"
                )
            elif name in assigned or name in unconditional or name in live:
                # The reverse of the check above: [env] is only the source while
                # no Fly secret shadows it, and the deploy assigns this name — so
                # a deploy CREATES the secret that kills the versioned value. Check
                # BOTH samples: a `-z`-guarded assignment appears only in the
                # no-secret run, and sampling `assigned` alone missed it (#4126
                # review).
                violations.append(
                    f"STALE DECLARATION — {name!r} is declared fly-toml-env but "
                    f"deploy-hosted.yml assigns the Fly variable {name}, which would "
                    "create the secret that shadows [env] (remove the assignment, or "
                    "declare the real source)"
                )
        elif kind == "unmanaged":
            # #4126: `unmanaged` names NO managing source, so it is the defect the
            # issue was filed for — not a benign recorded debt. Pass it and the
            # gate exits 0 on exactly the state that took extraction down.
            if name in assigned:
                violations.append(
                    f"STALE DECLARATION — {name!r} is declared unmanaged, which declares "
                    "no managing source, but deploy-hosted.yml propagates it "
                    "(declare it gh-secret:…)"
                )
            elif name in fly_names:
                violations.append(
                    f"UNSOURCED — {name!r} exists on Fly and its declaration names NO "
                    "managing source. Declare the real source (gh-secret:<GH_NAME> / "
                    "workflow / fly-toml-env), or drop the Fly secret after recording "
                    "the value in version control"
                )
            else:
                violations.append(
                    f"UNSOURCED — {name!r} is declared with no managing source and "
                    "exists on neither Fly nor the deploy payload (remove the entry, "
                    "or declare its real source)"
                )
        elif kind == "fly-only":
            # A decision-backed exception, NOT a way to spell `unmanaged` — see
            # read_manifest: reaching here proves the ref named an issue number.
            # The exception is only true while the name really is a live,
            # deliberately unversioned Fly secret, so every half is checked:
            # a name that is gone (nothing to except), a name the deploy assigns
            # (version-controlled after all), and a name that is an assigned key
            # in `fly.toml [env]` (version-controlled THERE, and a Fly secret
            # shadows that value — the same state the `fly-toml-env` route
            # rejects) are all stale declarations.
            if name not in fly_names:
                violations.append(
                    f"STALE DECLARATION — {name!r} is declared fly-only:{gh_name} but "
                    "exists neither on Fly nor in the deploy payload; a decision-backed "
                    "exception is for a REAL out-of-band Fly secret (remove the entry, "
                    "or declare its real source)"
                )
            elif name in env_keys:
                violations.append(
                    f"STALE DECLARATION — {name!r} is declared fly-only:{gh_name} but is "
                    "an assigned key in fly.toml's [env] table, so its value IS recorded "
                    "in version control (declare it fly-toml-env, and drop the Fly "
                    "secret that shadows it)"
                )
            elif name in assigned or name in unconditional or name in live:
                violations.append(
                    f"STALE DECLARATION — {name!r} is declared fly-only:{gh_name} but "
                    f"deploy-hosted.yml assigns the Fly variable {name}, so it IS "
                    "managed from version control (declare the real source)"
                )

    # The list describes the FLY app's state, so a declared-but-not-yet-present
    # name is not debt — the reverse-completeness rule above already covers it.
    #
    # (3) Declared AND honoured with every secret present, but skipped by THIS run:
    # the deploy leaves whatever is on Fly, so the value is hand-managed — the
    # #4126 incident, whatever gates the assignment (a guard on the declared
    # secret, or on any OTHER secret, or a composite flag). The two synthetic
    # samples cannot see it; it is a property of the REAL secret set, which is why
    # GH_SECRETS_PRESENT is read at all (#4259 review).
    for name, source in sorted(declared.items()):
        kind, _, gh_name = source.partition(":")
        if kind not in ("gh-secret", "workflow") or name not in fly_names:
            continue
        if name in assigned and name not in live:
            if kind == "gh-secret" and gh_name not in gh_present:
                reason = f"its declared GitHub secret {gh_name} is not present in this run"
            else:
                reason = "the GitHub secrets this run carries make its assignment a no-op"
            violations.append(
                f"UNSOURCED — {name!r} is on Fly but this deploy does not assign it "
                f"({reason}), so the Fly value is hand-managed. Assign it unconditionally "
                "from version control, or drop the Fly secret"
            )

    fly_conditional = [n for n in fly_names if n in set(conditional)]
    if fly_conditional:
        print(
            f"CONDITIONAL PROPAGATION — {len(fly_conditional)} secret(s) are only assigned when "
            "the GitHub secret exists. The guard cannot read GitHub secret existence, so "
            "while it is absent the Fly value is untouched — hand-managed, the #4126 case:"
        )
        for name in sorted(fly_conditional):
            print(f"  - {name}")
        print(
            "  Every declared source above is honoured by the deploy, but a GitHub secret "
            "that never exists keeps the Fly value hand-set. A declaration whose GitHub "
            "secret is absent is the #4126 defect — make the assignment unconditional, or "
            "record the value in version control."
        )

    if violations:
        print("::error::Fly secret provenance FAILED:")
        for v in violations:
            print(f"  - {v}")
        print(
            f"{len(violations)} violation(s) — declare the managing source in "
            f"{manifest_path.name}, or manage it from deploy-hosted.yml / fly.toml. "
            "A production env var that exists only on Fly cannot be rotated from version "
            "control and drifts silently (#4126)."
        )
        return 1

    # Anything still standing is unconditionally propagated (or gated only by a
    # declaration the rules above accepted). No `unmanaged` subtraction is needed
    # here: a declared `unmanaged` name is a violation above, so reaching this
    # line proves none survives on Fly.
    fly_only = [n for n in fly_names if declared.get(n, "").startswith("fly-only:")]
    managed = [n for n in fly_names if n not in set(conditional) and n not in set(fly_only)]
    print(
        f"OK: all {len(fly_names)} Fly secret(s) are declared "
        f"({len(managed)} managed, {len(fly_conditional)} conditionally propagated, "
        f"{len(fly_only)} decision-backed fly-only)"
    )
    return 0


if __name__ == "__main__":
    # Fail-closed belt and braces: an UNEXPECTED exception would make Python exit
    # 1 — the code the deploy step translates into a bypass when
    # `skip-fly-secret-provenance` is set. No unclassifiable state may take that
    # path.
    try:
        sys.exit(main())
    except Exception as exc:
        _err(f"cannot determine secret provenance: unexpected {type(exc).__name__}: {exc}")
        sys.exit(2)
