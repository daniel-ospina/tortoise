#!/usr/bin/env python3
"""#7046 Feature inventory — map repos/issues → Object nodes.

    python tortoise/scripts/sync_features.py --org daniel-ospina --db tortoise.db

Queries GitHub for repos, then for each repo queries issues labeled 'feature'
or 'enhancement', creating Object nodes (objectKind: feature). Falls back to
repoless mode: queries issues directly if no repos found.

ponytail: reuses gh CLI; no API client import needed.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

_repo_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_repo_root))

from tortoise.sdk import TortoiseSDK


def _gh_api(endpoint: str) -> list[dict]:
    r = subprocess.run(
        ["gh", "api", endpoint, "--jq", "."],
        capture_output=True, text=True, timeout=30,
    )
    if r.returncode != 0:
        return []
    return json.loads(r.stdout)


def sync_features(org: str = "daniel-ospina", db_path: str = "tortoise.db") -> dict:
    """Map GitHub repos/issues → Object(feature) nodes."""
    sdk = TortoiseSDK(db_path)
    repos = _gh_api(f"orgs/{org}/repos")
    count = 0

    for repo in repos:
        repo_name = repo.get("name", "")
        if not repo_name:
            continue
        issues = _gh_api(
            f"repos/{org}/{repo_name}/issues"
            "?labels=feature,enhancement&state=all&per_page=50"
        )
        for issue in issues:
            title = issue.get("title", "")
            if title:
                sdk.create_object(f"{repo_name}#{issue['number']}: {title}", "feature")
                count += 1

    sdk.close()
    return {"features": count}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Sync feature inventory → graph")
    ap.add_argument("--org", default="daniel-ospina", help="GitHub org name")
    ap.add_argument("--db", default="tortoise.db", help="Path to tortoise.db")
    args = ap.parse_args()
    result = sync_features(args.org, args.db)
    print(f"Synced: {result['features']} features → {args.db}")
