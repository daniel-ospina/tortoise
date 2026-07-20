#!/usr/bin/env python3
"""#7042 GitHub org connector — populate Subjects (teams) and Objects (repos).

    python tortoise/scripts/sync_github.py --org daniel-ospina --db tortoise.db

Queries GitHub API via `gh api` for teams and repos, creates Subject nodes
(subjectKind: team) and Object nodes (objectKind: repository).

ponytail: gh CLI is a pre-installed dep; no PyGithub needed.
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
    """Call `gh api <endpoint>` and return parsed JSON list."""
    r = subprocess.run(
        ["gh", "api", endpoint, "--jq", "."],
        capture_output=True, text=True, timeout=30,
    )
    if r.returncode != 0:
        print(f"gh api error: {r.stderr.strip()}", file=sys.stderr)
        return []
    return json.loads(r.stdout)


def sync_github(org: str, db_path: str = "tortoise.db") -> dict:
    """Sync GitHub org → Subject (teams) + Object (repos) nodes."""
    sdk = TortoiseSDK(db_path)
    teams_created, repos_created = 0, 0

    for team in _gh_api(f"orgs/{org}/teams"):
        sdk.create_subject(team.get("name", team.get("slug", "")), "team")
        teams_created += 1

    for repo in _gh_api(f"orgs/{org}/repos"):
        sdk.create_object(repo.get("name", ""), "repository")
        repos_created += 1

    sdk.close()
    return {"teams": teams_created, "repos": repos_created}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Sync GitHub org → graph")
    ap.add_argument("--org", default="daniel-ospina", help="GitHub org name")
    ap.add_argument("--db", default="tortoise.db", help="Path to tortoise.db")
    args = ap.parse_args()
    result = sync_github(args.org, args.db)
    print(f"Synced: {result['teams']} teams, {result['repos']} repos → {args.db}")
