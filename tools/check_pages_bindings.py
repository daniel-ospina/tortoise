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

Usage:
    check_pages_bindings.py --manifest website/required-bindings.yml \\
        [--account-id <id>] [--api-token <token>] [--json]

Exit codes:
    0  every `required` binding present (warnings allowed)
    1  a `required` binding is missing
    2  could not determine the state (API error, bad manifest) — fail closed

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


def load_manifest(path: Path) -> dict:
    """Load and validate a binding manifest.

    Validation is deliberately strict: a manifest that declares nothing, or
    declares an entry with an unvalidated field, makes the gate vacuous — and a
    vacuous gate is how #3616 shipped. Every field `evaluate()` reads is checked
    here, because a field the loader ignores is a field an attacker (or a typo)
    can use to turn `required` into a warning.
    """
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a mapping, got {type(data).__name__}")

    bindings = data.get("bindings")
    if not isinstance(bindings, list) or not bindings:
        raise ValueError(
            f"{path}: `bindings` must be a non-empty list — an empty manifest "
            "would assert nothing"
        )

    for i, spec in enumerate(bindings):
        if not isinstance(spec, dict):
            raise ValueError(f"{path}: bindings[{i}] must be a mapping")
        for key in ("name", "type"):
            if not spec.get(key):
                raise ValueError(f"{path}: bindings[{i}] is missing `{key}`")

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
                f"{path}: bindings[{i}] ({spec.get('name')}) has invalid kind "
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
                f"{path}: bindings[{i}] ({spec.get('name')}) `envs` must be a "
                "non-empty list of environment names"
            )

    if not any(s.get("kind", "required") == "required" for s in bindings):
        raise ValueError(
            f"{path}: no binding is marked `required` — the gate would only warn"
        )

    return data


def evaluate(manifest: dict, configs: dict[str, dict]) -> tuple[list[str], list[str]]:
    """Return (missing_required, missing_recommended) as human-readable strings.

    ``configs`` maps env name -> that env's deployment config dict (the shape the
    Pages API returns under ``deployment_configs``).

    Pure function: no network, no I/O. This is what the tests exercise.
    """
    missing_required: list[str] = []
    missing_recommended: list[str] = []

    for spec in manifest["bindings"]:
        name = spec["name"]
        kind = spec.get("kind", "required")
        btype = spec["type"]
        for envname in spec.get("envs", ["production"]):
            env = configs.get(envname) or {}
            bucket = env.get(btype) or {}
            # Truthiness, not key membership: a null-valued binding
            # (`{"SESSIONS": None}`) is not a usable binding.
            if bucket.get(name):
                continue
            label = f"{envname}:{btype}:{name}"
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
    return result.get("deployment_configs") or {}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--manifest", default="website/required-bindings.yml")
    ap.add_argument("--account-id", default=os.environ.get("CLOUDFLARE_ACCOUNT_ID"))
    ap.add_argument("--api-token", default=os.environ.get("CLOUDFLARE_API_TOKEN"))
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args(argv)

    try:
        manifest = load_manifest(Path(args.manifest))
    except Exception as e:
        print(f"::error::cannot read {args.manifest}: {e}", file=sys.stderr)
        return 2

    project = manifest.get("project")
    if not project or not args.account_id or not args.api_token:
        print(
            "::error::project, --account-id and --api-token (or "
            "CLOUDFLARE_ACCOUNT_ID / CLOUDFLARE_API_TOKEN) are required",
            file=sys.stderr,
        )
        return 2

    try:
        configs = fetch_configs(args.account_id, project, args.api_token)
    except RuntimeError as e:
        # Fail CLOSED: not knowing is not the same as knowing it is fine.
        print(f"::error::{e}", file=sys.stderr)
        return 2

    missing_required, missing_recommended = evaluate(manifest, configs)

    if args.json:
        print(
            json.dumps(
                {
                    "project": project,
                    "missing_required": missing_required,
                    "missing_recommended": missing_recommended,
                },
                indent=2,
            )
        )

    for item in missing_recommended:
        print(f"::warning::binding absent (recommended, code has a default): {item}")

    if missing_required:
        for item in missing_required:
            print(f"::error::REQUIRED binding missing: {item}", file=sys.stderr)
        print(
            "\nThe code cannot serve its purpose without these. Bind them on the "
            f"`{project}` Pages project before deploying — see "
            "website/required-bindings.yml and issue #3616.",
            file=sys.stderr,
        )
        return 1

    print(
        f"✅ {project}: all required bindings present "
        f"({len(missing_recommended)} recommended absent)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
