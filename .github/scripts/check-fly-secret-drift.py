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
declaration that the workflow no longer honours is a violation too, so the
manifest cannot rot into a comfortable lie.

Sources (see the manifest header for the full rationale):

``gh-secret:<GH_NAME>``  ``deploy-hosted.yml`` propagates it from the GitHub
                         Actions secret ``<GH_NAME>`` (not always the same name).
``workflow``             the workflow sets it from non-secret context
                         (``${GITHUB_SHA}``, a composed flag, …).
``fly-toml-env``         applied from ``fly.toml`` ``[env]``.
``unmanaged``            present on Fly with NO managing source — recorded debt,
                         reported on every run, escalated under #4126.

Exit codes (mirrors check-migration-drift / check-fly-machines-guard):
  0 — every Fly secret is declared and every declaration is honoured
  1 — drift found (undeclared Fly secret, or a declaration the deploy does not
      honour)
  2 — could not determine state (missing/unparsable manifest, unreadable secret
      list, malformed entry). Fail-closed: an unreadable state is never clean.

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
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_MANIFEST = REPO_ROOT / ".github" / "scripts" / "fly-managed-secrets.txt"
DEFAULT_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "deploy-hosted.yml"

# `NAME=…` tokens on a line that builds the secrets payload.
_ASSIGN_RE = re.compile(r"\b([A-Z][A-Z0-9_]{2,})=")
_SECRET_REF_RE = re.compile(r"secrets\.([A-Za-z0-9_]+)")


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
    return names


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
        toml_text = toml_path.read_text()
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        _err(f"cannot determine secret provenance: {exc}")
        return 2

    workflow_refs = set(_SECRET_REF_RE.findall(workflow_text))
    # Assignment tokens only on lines that build the Fly secrets payload — a name
    # mentioned in a comment or a gate must not count as propagation.
    workflow_assigned = set()
    for line in workflow_text.splitlines():
        if "ARGS" in line:
            workflow_assigned |= set(_ASSIGN_RE.findall(line))

    print(
        f"check-fly-secret-drift: Fly app {app} vs {manifest_path.name} "
        f"({len(fly_names)} secret(s) live, {len(declared)} declared)"
    )

    violations: list[str] = []

    # (1) An undeclared name is NEW drift: a var that exists only on Fly.
    for name in sorted(set(fly_names) - set(declared)):
        violations.append(
            f"UNDECLARED — {name!r} is on Fly but declared nowhere in {manifest_path.name}"
        )

    # (2) A declaration the deploy does not honour is stale intent.
    for name, source in sorted(declared.items()):
        kind, _, gh_name = source.partition(":")
        if kind == "gh-secret" and gh_name not in workflow_refs:
            violations.append(
                f"STALE DECLARATION — {name!r} is declared gh-secret:{gh_name} but "
                "deploy-hosted.yml never references it"
            )
        elif kind == "workflow" and name not in workflow_assigned:
            violations.append(
                f"STALE DECLARATION — {name!r} is declared workflow-set but "
                "deploy-hosted.yml never assigns it"
            )
        elif kind == "fly-toml-env" and name not in toml_text:
            violations.append(
                f"STALE DECLARATION — {name!r} is declared fly-toml-env but absent from fly.toml"
            )
        elif kind == "unmanaged" and name in workflow_assigned:
            violations.append(
                f"STALE DECLARATION — {name!r} is declared unmanaged but "
                "deploy-hosted.yml propagates it (declare it gh-secret:…)"
            )

    unmanaged = sorted(n for n, s in declared.items() if s == "unmanaged")
    if unmanaged:
        print(f"RECORDED DEBT — {len(unmanaged)} secret(s) on Fly have no managing source (#4126):")
        for name in unmanaged:
            print(f"  - {name}")
        print(
            "  These do not fail the gate yet: which of them become managed is an owner "
            "decision escalating under #4126. They are printed on every run so the debt "
            "cannot be forgotten."
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

    print(
        f"OK: all {len(fly_names)} Fly secret(s) are declared "
        f"({len(declared) - len(unmanaged)} managed, {len(unmanaged)} recorded unmanaged)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
