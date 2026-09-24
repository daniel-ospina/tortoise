#!/usr/bin/env python3
"""Verify a Cloudflare Pages project carries the bindings the code requires.

WHY THIS EXISTS (issue #3616)
-----------------------------
#3501 merged a BFF auth layer that needs a `SESSIONS` D1 binding. The binding did
not exist, the deploy went green, and production `/auth/start` returned
`503 session_store_unavailable` for every visitor. The requirement lived in a PR
body, which cannot fail.

This runs BEFORE the deploy, so a missing binding blocks the change instead of
reaching users. The failure mode being defended against is specifically "deploy
succeeds, runtime 503s".

WHY THE MANIFEST LIVES IN config/, NOT website/
-----------------------------------------------
Because `wrangler pages deploy .` uploads EVERYTHING under `website/`, and
`.wranglerignore` is NOT honoured by `wrangler pages deploy`. A cycle-4 review
verified that four ways: wrangler's `pages/validate.ts` uses a hardcoded
IGNORE_LIST with no ignore-file read; the string `wranglerignore` appears in 0
files across all locally installed wrangler versions; `wrangler pages deploy
--help` exposes no include/exclude; and live,
`https://tortoise.premiselabs.co/.wranglerignore` returns 200 while `apps/` was
served despite being listed. #3620 replaced that wholesale upload with a staged
DENYLIST copy, so excluded paths are no longer published — but a NEW top-level
entry under `website/` is still staged unless it is excluded (the deploy job's
classification preflight and `tests/test_pages_bindings.py` pin that). Keeping
the manifest in `config/` is what makes it immune to that residual risk.

Usage:
    check_pages_bindings.py --manifest config/required-bindings.yml \\
        [--project <name>] [--account-id <id>] [--api-token <token>] [--json]

`--project` narrows the check to ONE project in the manifest. Omit it to check
EVERY project (the default): a project the manifest declares is never silently
skipped, because a skipped project is a gate that asserts nothing.

Exit codes:
    0  every `required` binding present for every checked project (warnings allowed)
    1  a `required` binding is missing
    2  could not determine the state (API error, bad manifest, unknown --project)
       — fail closed

The pure comparison logic is `evaluate()`, unit-tested in
`tests/test_pages_bindings.py` with no network access.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - the workflow installs pyyaml
    print("pyyaml is required: pip install pyyaml", file=sys.stderr)
    sys.exit(2)

#: The only legal `kind` values. `required` fails the deploy, `recommended` warns.
KIND_ENUM = frozenset({"required", "recommended"})


def _validate_bindings(path: Path, where: str, bindings) -> list[dict]:
    """Validate ONE project's `bindings` list and return it unchanged.

    Validation is deliberately strict: a manifest that declares nothing, or
    declares an entry with an unvalidated field, makes the gate vacuous — and a
    vacuous gate is how #3616 shipped. Every field `evaluate()` reads is checked
    here, because a field the loader ignores is a field an attacker (or a typo)
    can use to turn `required` into a warning.
    """
    prefix = f"{path}: {where}" if where else f"{path}:"
    if not isinstance(bindings, list) or not bindings:
        raise ValueError(
            f"{prefix} `bindings` must be a non-empty list — an empty manifest "
            "would assert nothing"
        )

    for i, spec in enumerate(bindings):
        if not isinstance(spec, dict):
            raise ValueError(f"{prefix} bindings[{i}] must be a mapping")
        for key in ("name", "type"):
            if not spec.get(key):
                raise ValueError(f"{prefix} bindings[{i}] is missing `{key}`")

        # `kind` MUST be validated against an enum. Without this, a one-character
        # typo (`kind: Required`, `kind: rquired`) silently downgrades a binding
        # to a warning and the deploy goes green — which is precisely the #3616
        # failure mode this gate exists to prevent. A misspelling must be a hard
        # error, not a fail-open.
        #
        # `isinstance(kind, str)` first: a non-string kind (a list, a mapping, a
        # bool) is unhashable or surprising, and would raise TypeError from the
        # set-membership test instead of the contractual ValueError.
        kind = spec.get("kind", "required")
        if not isinstance(kind, str) or kind not in KIND_ENUM:
            raise ValueError(
                f"{prefix} bindings[{i}] ({spec.get('name')}) has invalid kind "
                f"{kind!r}; expected one of {sorted(KIND_ENUM)}"
            )

        # `envs: []` must be rejected. `.get("envs", default)` does NOT apply the
        # default when the key is present-but-empty, so an empty list made the
        # binding invisible to `evaluate()` — neither required nor recommended,
        # silently skipped. Validate the shape here rather than relying on the
        # default in two places.
        envs = spec.get("envs", ["production"])
        if not isinstance(envs, list) or not envs:
            raise ValueError(
                f"{prefix} bindings[{i}] ({spec.get('name')}) `envs` must be a "
                "non-empty list of environment names"
            )

    if not any(s.get("kind", "required") == "required" for s in bindings):
        raise ValueError(
            f"{prefix} no binding is marked `required` — the gate would only warn"
        )

    return bindings


def load_manifest(path: Path) -> dict:
    """Load and validate a binding manifest, normalized to `{"projects": [...]}`.

    TWO SHAPES ARE ACCEPTED:

      * multi-project (current)::

            projects:
              - project: premise-labs
                bindings: [...]
              - project: tortoise-dashboard
                bindings: [...]

      * single-project (legacy, still loads)::

            project: premise-labs
            bindings: [...]

    Both normalize to a list of ``{"project": <name>, "bindings": [...]}`` so
    `main()` and `evaluate()` never branch on the on-disk shape. The legacy shape
    is kept so a manifest outside this repo does not break on upgrade.
    """
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a mapping, got {type(data).__name__}")

    if "projects" in data:
        raw = data["projects"]
        if not isinstance(raw, list) or not raw:
            raise ValueError(
                f"{path}: `projects` must be a non-empty list of projects"
            )
        projects: list[dict] = []
        seen: set[str] = set()
        for i, entry in enumerate(raw):
            if not isinstance(entry, dict):
                raise ValueError(f"{path}: projects[{i}] must be a mapping")
            name = entry.get("project")
            if not isinstance(name, str) or not name:
                raise ValueError(f"{path}: projects[{i}] is missing a `project` name")
            if name in seen:
                raise ValueError(
                    f"{path}: duplicate project {name!r} — two entries would "
                    "check the same Cloudflare project twice"
                )
            seen.add(name)
            projects.append(
                {
                    "project": name,
                    "bindings": _validate_bindings(
                        path, f"projects[{i}] ({name})", entry.get("bindings")
                    ),
                }
            )
        return {"projects": projects}

    # Legacy single-project shape. Kept working so an existing manifest loads.
    bindings = _validate_bindings(path, "", data.get("bindings"))
    project = data.get("project")
    if not isinstance(project, str) or not project:
        raise ValueError(f"{path}: missing a `project` name")
    return {"projects": [{"project": project, "bindings": bindings}]}


def iter_projects(manifest: dict) -> list[dict]:
    """Every project in a loaded manifest, in declaration order."""
    return manifest["projects"]


def evaluate(project: dict, configs: dict[str, dict]) -> tuple[list[str], list[str]]:
    """Return (missing_required, missing_recommended) for ONE project.

    ``project`` is a manifest entry (``{"project": name, "bindings": [...]}``).
    ``configs`` maps env name -> that env's deployment config dict (the shape the
    Pages API returns under ``deployment_configs``).

    Labels are project-qualified — ``<project>:<env>:<type>:<name>``. The two
    projects share binding names, so a bare ``production:env_vars:SUPABASE_URL``
    would not say WHICH project is missing it.

    Pure function: no network, no I/O. This is what the tests exercise.
    """
    missing_required: list[str] = []
    missing_recommended: list[str] = []
    pname = project["project"]

    for spec in project["bindings"]:
        bname = spec["name"]
        kind = spec.get("kind", "required")
        btype = spec["type"]
        for envname in spec.get("envs", ["production"]):
            env = configs.get(envname) or {}
            bucket = env.get(btype) or {}
            # Truthiness, not key membership: a null-valued binding
            # (`{"SESSIONS": None}`) is not a usable binding.
            if bucket.get(bname):
                continue
            label = f"{pname}:{envname}:{btype}:{bname}"
            if kind == "required":
                missing_required.append(label)
            else:
                missing_recommended.append(label)

    return missing_required, missing_recommended


def fetch_configs(account_id: str, project: str, api_token: str) -> dict:
    url = (
        f"https://api.cloudflare.com/client/v4/accounts/{account_id}"
        f"/pages/projects/{project}"
    )
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {api_token}")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            payload = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise RuntimeError(
            f"Cloudflare API {e.code} reading project {project!r}: "
            f"{e.read().decode()[:300]}"
        ) from e
    except Exception as e:  # network, DNS, TLS
        raise RuntimeError(f"could not reach the Cloudflare API: {e}") from e

    if not payload.get("success"):
        raise RuntimeError(f"Cloudflare API reported failure: {payload.get('errors')}")

    # A `success: true` response with no `result` is a malformed payload, not a
    # "binding is missing" verdict. Raise RuntimeError so main() exits 2
    # (could-not-determine, fail closed) rather than letting a KeyError escape
    # and land on the exit-1 path that means "a required binding is absent".
    result = payload.get("result")
    if not isinstance(result, dict):
        raise RuntimeError(
            f"Cloudflare API returned success with a malformed payload: {str(payload)[:300]}"
        )
    # `deployment_configs` must be PRESENT. `result.get(...) or {}` would turn a
    # missing key into "every binding is absent" (exit 1, "a required binding is
    # missing") — the wrong reason, sending an operator to hunt for a binding
    # that is not the problem. Both fail closed; the diagnosis must be right.
    configs = result.get("deployment_configs")
    if not isinstance(configs, dict):
        raise RuntimeError(
            "Cloudflare API returned success but no `deployment_configs` — the "
            "project payload shape is not what this gate expects"
        )
    return configs


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--manifest", default="config/required-bindings.yml")
    ap.add_argument(
        "--project",
        default=None,
        help="check only this project (default: every project in the manifest)",
    )
    ap.add_argument("--account-id", default=os.environ.get("CLOUDFLARE_ACCOUNT_ID"))
    ap.add_argument("--api-token", default=os.environ.get("CLOUDFLARE_API_TOKEN"))
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args(argv)

    def _fail(reason: str) -> int:
        """Exit 2 (could-not-determine), emitting the SAME document shape as the

        other paths when --json is set. Without this, `--json` printed nothing on

        this path, so an unconditional `json.loads(stdout)` crashed with

        JSONDecodeError instead of reading an error document.

        """
        print(f"::error::{reason}", file=sys.stderr)
        if args.json:
            print(
                json.dumps(
                    {
                        "project": None,
                        "projects": [],
                        "missing_required": [],
                        "missing_recommended": [],
                        "error": reason,
                    },
                    indent=2,
                )
            )
        return 2

    try:
        manifest = load_manifest(Path(args.manifest))
    except Exception as e:
        return _fail(f"cannot read {args.manifest}: {e}")

    projects = iter_projects(manifest)

    if args.project is not None:
        projects = [p for p in projects if p["project"] == args.project]
        if not projects:
            # A typo'd --project must NOT report success for a project that was
            # never checked — that is the #3616 vacuous-gate failure with one
            # flag removed. Exit 2 (could-not-determine), never 0.
            return _fail(
                f"project {args.project!r} is not declared in {args.manifest} — "
                "refusing to report success for a project that was never checked"
            )

    if not args.account_id or not args.api_token:
        return _fail(
            "--account-id and --api-token (or CLOUDFLARE_ACCOUNT_ID / "
            "CLOUDFLARE_API_TOKEN) are required"
        )

    results: list[dict] = []
    missing_required: list[str] = []
    missing_recommended: list[str] = []
    for project in projects:
        try:
            configs = fetch_configs(args.account_id, project["project"], args.api_token)
        except RuntimeError as e:
            # Fail CLOSED: not knowing is not the same as knowing it is fine.
            return _fail(str(e))
        req, rec = evaluate(project, configs)
        missing_required += req
        missing_recommended += rec
        results.append(
            {
                "project": project["project"],
                "missing_required": req,
                "missing_recommended": rec,
            }
        )

    if args.json:
        # Diagnostics go to STDERR. Emitting them after the document made
        # `--json` unparseable: `json.loads(stdout)` failed with "Extra data:
        # line 12 column 1" because the `::warning::` lines and the success line
        # followed the JSON on stdout. stdout must be exactly one document.
        print(
            json.dumps(
                {
                    "projects": results,
                    "missing_required": missing_required,
                    "missing_recommended": missing_recommended,
                },
                indent=2,
            )
        )
        for item in missing_recommended:
            print(
                f"::warning::binding absent (recommended, code has a default): {item}",
                file=sys.stderr,
            )
        if missing_required:
            for item in missing_required:
                print(f"::error::REQUIRED binding missing: {item}", file=sys.stderr)
            return 1
        print(
            f"\u2705 {', '.join(p['project'] for p in projects)}: "
            "all required bindings present",
            file=sys.stderr,
        )
        return 0

    for item in missing_recommended:
        print(f"::warning::binding absent (recommended, code has a default): {item}")

    if missing_required:
        for item in missing_required:
            print(f"::error::REQUIRED binding missing: {item}", file=sys.stderr)
        print(
            "\nThe code cannot serve its purpose without these. Each line above is "
            "`<project>:<env>:<type>:<name>` — bind the missing entry on the named "
            "Pages project before deploying. See config/required-bindings.yml and "
            "issue #3616.",
            file=sys.stderr,
        )
        return 1

    print(
        f"✅ {', '.join(p['project'] for p in projects)}: all required bindings "
        f"present ({len(missing_recommended)} recommended absent)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
