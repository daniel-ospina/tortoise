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


def load_manifest(path: Path) -> dict:
    """Load and validate a binding manifest.

    Validation is deliberately strict: a manifest that declares nothing (or
    declares an entry without a name/type) would make the gate vacuous, and a
    vacuous gate is how #3616 shipped. Reject it rather than pass it.
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
            present = name in bucket
            if present:
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
    return payload["result"].get("deployment_configs") or {}


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
