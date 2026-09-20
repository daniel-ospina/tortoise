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
                         ``[ -n "${{ secrets.X }}" ] &&`` guard is reported as
                         CONDITIONAL — while that GitHub secret is absent, the
                         Fly value is unmanaged, which is exactly the #4126 case.
``workflow``             the workflow sets it from non-secret context
                         (``${GITHUB_SHA}``, a composed flag, …).
``fly-toml-env``         an assigned key in ``fly.toml``'s ``[env]`` table.
``unmanaged``            present on Fly with NO managing source — recorded debt,
                         reported on every run, escalated under #4126.

Exit codes (mirrors check-migration-drift / check-fly-machines-guard):
  0 — every Fly secret is declared and every declaration is honoured
  1 — drift found (undeclared Fly secret, or a declaration the deploy does not
      honour)
  2 — could not determine state (missing/unparsable manifest, unreadable or
      EMPTY secret list, malformed entry). Fail-closed: an unreadable state —
      including a payload that reads as "no secrets" — is never clean.

Env seams (all optional; used by the hermetic test suite):
  FLY_SECRETS_FILE          fixture path holding the ``--json`` secret list
  FLY_MANAGED_SECRETS_FILE  manifest path override
  FLY_APP                   app name (default: fly.toml ``app``)
  FLY_TOML                  fly.toml path override
  DEPLOY_WORKFLOW           deploy workflow path override
  FLY_SECRETS_CMD           secret-list command override
                            (default ``flyctl secrets list --app <app> --json``)
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

# `NAME=…` tokens in a captured `flyctl secrets set` payload.
_ASSIGN_RE = re.compile(r"\b([A-Z][A-Z0-9_]{2,})=([^\s]*)")
# The propagation step's shell is located by its `flyctl secrets set` call.
_PAYLOAD_CMD_RE = re.compile(r"\bflyctl\s+secrets\s+set\b")
_SECRET_REF_RE = re.compile(r"secrets\.([A-Za-z0-9_]+)")
# The Actions template reference substituted before the shell runs.
_SECRET_TMPL_RE = re.compile(r"\$\{\{\s*secrets\.([A-Za-z0-9_]+)\s*\}\}")


def extract_propagation_script(text: str) -> str:
    """The `run:` shell block that builds the Fly secrets payload.

    Raises ValueError when it cannot be located — fail-closed, because a guard
    that cannot read the payload would silently certify any fleet.
    """
    lines = text.splitlines()
    cmd_index = next((i for i, ln in enumerate(lines) if _PAYLOAD_CMD_RE.search(ln)), None)
    if cmd_index is None:
        raise ValueError("no `flyctl secrets set` invocation found in the deploy workflow")
    run_index = None
    for i in range(cmd_index, -1, -1):
        if re.match(r"\s*(-\s*)?run:\s*\|?\s*$", lines[i]):
            run_index = i
            break
    if run_index is None:
        raise ValueError("could not find the `run:` block holding the secrets payload")
    indent = len(lines[run_index]) - len(lines[run_index].lstrip())
    body: list[str] = []
    for line in lines[run_index + 1 :]:
        if not line.strip():
            body.append("")
            continue
        if len(line) - len(line.lstrip()) <= indent:
            break
        body.append(line)
    if not body:
        raise ValueError("the secrets-payload `run:` block is empty")
    block_indent = min((len(ln) - len(ln.lstrip()) for ln in body if ln.strip()), default=0)
    return "\n".join(ln[block_indent:] for ln in body)


def payload_step_is_conditional(text: str) -> bool:
    """True when the payload step carries a step-level ``if:``.

    A YAML ``if:`` on the step is not part of the `run:` shell, so executing the
    block cannot see it. It is read here instead: a gated step means every name it
    assigns is only propagated when the condition holds — the fail-safe
    direction, since an over-conditional name stays visible in the debt list.
    """
    lines = text.splitlines()
    cmd_index = next((i for i, ln in enumerate(lines) if _PAYLOAD_CMD_RE.search(ln)), None)
    if cmd_index is None:
        return False
    for line in reversed(lines[:cmd_index]):
        # `- if: …` is both the step marker and the condition, so test the key
        # BEFORE treating the line as a step boundary.
        if re.match(r"\s*(-\s*)?if:\s*\S", line):
            return True
        if line.strip().startswith("- "):
            break
    return False


def _capture_payload(script: str, present: dict[str, str]) -> dict[str, str]:
    """Run the propagation shell with a stubbed flyctl; return ``NAME -> value``.

    EXECUTION, not parsing. The block is the ground truth, and every shell
    spelling (``&&``, ``test``, ``[[``, ``case``, a `while`, a YAML step-level
    ``if:``, an accumulator rename, ``+=``, a backslash continuation) is handled
    for free. Four review rounds each found a new spelling the parser missed; the
    parser is gone.

    Hermetic and value-blind: ``flyctl``/``fly`` are shell stubs on a PATH that
    contains nothing else, so no real Fly call can happen, and the GitHub secret
    placeholders are replaced by dummy markers — the guard never touches a
    credential. ``secrets.X`` is the marker ``__SEC_X__`` when the caller says X
    is present, else empty.
    """

    def substitute(match: re.Match[str]) -> str:
        name = match.group(1)
        return f"__SEC_{name}__" if name in present else ""

    rendered = _SECRET_TMPL_RE.sub(substitute, script)
    # Resolve the interpreter BEFORE the child's PATH is narrowed to the stubs.
    bash = shutil.which("bash") or "/bin/bash"
    with tempfile.TemporaryDirectory(prefix="fly-secret-stub-") as stub_dir:
        for binary in ("flyctl", "fly"):
            path = Path(stub_dir) / binary
            path.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
            path.chmod(0o755)
        env = {
            "PATH": stub_dir,
            "HOME": stub_dir,
            "GITHUB_SHA": "fixture-sha",
        }
        # `bash -e` mirrors GitHub's default shell for a `run:` block.
        proc = subprocess.run(
            [bash, "-e"], input=rendered, capture_output=True, text=True, env=env, timeout=30
        )
    if proc.returncode != 0:
        raise ValueError(
            f"the propagation shell failed under the stub (rc={proc.returncode}): "
            f"{proc.stderr.strip()[:300]}"
        )
    captured: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        match = _ASSIGN_RE.fullmatch(line.strip())
        if match:
            captured[match.group(1)] = match.group(2)
    return captured


def payload_partition(
    script: str, step_conditional: bool = False
) -> tuple[set[str], set[str], dict[str, set[str]]]:
    """``(assigned, unconditional, sources)`` — computed by executing the block.

    ``assigned``      names in the payload when every GitHub secret is present
    ``unconditional`` names in the payload when NO GitHub secret is present
    ``sources[name]`` the GitHub secrets whose marker appears in the value the
                      workflow assigns to ``name`` — so a `gh-secret:<GH_NAME>`
                      declaration must name the secret that actually feeds THAT
                      variable, not merely one referenced anywhere.

    ``step_conditional`` forces ``unconditional`` empty: a step-level ``if:``
    gates the whole payload, which the shell cannot show.
    """
    secret_names = sorted(set(_SECRET_REF_RE.findall(script)))
    all_present = _capture_payload(script, {name: name for name in secret_names})
    assigned = set(all_present)
    unconditional = set() if step_conditional else set(_capture_payload(script, {}))
    sources: dict[str, set[str]] = {name: set() for name in assigned}
    for name, value in all_present.items():
        sources[name] = {s for s in secret_names if f"__SEC_{s}__" in value}
    return assigned, unconditional, sources


def _err(msg: str) -> None:
    print(f"::error::{msg}", file=sys.stderr)


def read_manifest(path: Path) -> dict[str, str]:
    """Parse ``<NAME> <source>`` lines. Raises ValueError on a malformed entry."""
    entries: dict[str, str] = {}
    for lineno, raw in enumerate(path.read_text().splitlines(), start=1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) != 2:
            raise ValueError(f"{path.name}:{lineno}: expected '<NAME> <source>', got {raw!r}")
        name, source = parts
        kind = source.split(":", 1)[0]
        if kind not in ("gh-secret", "workflow", "fly-toml-env", "unmanaged"):
            raise ValueError(
                f"{path.name}:{lineno}: unknown source {source!r} "
                "(expected gh-secret:<GH_NAME> | workflow | fly-toml-env | unmanaged)"
            )
        if kind == "gh-secret" and not source.split(":", 1)[1]:
            raise ValueError(f"{path.name}:{lineno}: gh-secret needs the GitHub secret name")
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
        if not name:
            raise ValueError(f"secret entry has no name: {item!r}")
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
        script = extract_propagation_script(workflow_text)
        assigned, unconditional, assignment_sources = payload_partition(
            script, step_conditional=payload_step_is_conditional(workflow_text)
        )
    except ValueError as exc:
        _err(f"cannot determine secret provenance: {exc}")
        return 2
    # Everything the payload carries that is NOT there with every GitHub secret
    # absent is conditionally propagated. Executing the block computes that
    # exactly, so no shell spelling can hide it.
    assigned_conditionally = assigned - unconditional
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
    for name in sorted(set(fly_names) - set(declared)):
        violations.append(
            f"UNDECLARED — {name!r} is on Fly but declared nowhere in {manifest_path.name}"
        )
    for name in sorted(assigned - set(declared)):
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
                # Assigned behind a guard: while that GitHub secret is absent the
                # Fly value is untouched, i.e. hand-managed — the #4126 case.
                # CONSERVATIVE: any guarded assignment wins over an unguarded one.
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
        elif kind == "unmanaged" and name in assigned:
            violations.append(
                f"STALE DECLARATION — {name!r} is declared unmanaged but "
                "deploy-hosted.yml propagates it (declare it gh-secret:…)"
            )

    unmanaged = sorted(n for n, s in declared.items() if s == "unmanaged")
    # The lists describe the FLY app's state, so a declared-but-not-yet-present
    # name is not debt — the reverse-completeness rule above already covers it.
    fly_conditional = [n for n in fly_names if n in set(conditional)]
    fly_unmanaged = [n for n in fly_names if n in set(unmanaged)]
    if fly_conditional:
        print(
            f"CONDITIONAL PROPAGATION — {len(fly_conditional)} secret(s) are only assigned when "
            "the GitHub secret exists. The guard cannot read GitHub secret existence, so "
            "while it is absent the Fly value is untouched — hand-managed, the #4126 case:"
        )
        for name in sorted(fly_conditional):
            print(f"  - {name}")
    if fly_unmanaged:
        print(
            f"RECORDED DEBT — {len(fly_unmanaged)} secret(s) on Fly have no managing source "
            "(#4126):"
        )
        for name in fly_unmanaged:
            print(f"  - {name}")
    if fly_conditional or fly_unmanaged:
        print(
            "  Neither list fails the gate yet: retiring them (create the GitHub secret / "
            "declare a managing source) is an owner decision escalating under #4126. They "
            "are printed on every run so the debt cannot be forgotten."
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

    managed = [n for n in fly_names if n not in set(conditional) and n not in set(unmanaged)]
    print(
        f"OK: all {len(fly_names)} Fly secret(s) are declared "
        f"({len(managed)} managed, {len(fly_conditional)} conditionally propagated, "
        f"{len(fly_unmanaged)} recorded unmanaged)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
