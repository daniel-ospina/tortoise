#!/usr/bin/env python3
"""#7044 Role registry — parse operations/subjects/ → Subject nodes.

    python tortoise/scripts/sync_roles.py --root . --db tortoise.db

Walks operations/subjects/ directories, extracts role definitions from
markdown files, and creates Subject nodes (subjectKind: role).

ponytail: walk + title extraction; roles are just named subjects.
"""
from __future__ import annotations

import sys
from pathlib import Path

_repo_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_repo_root))

from tortoise.sdk import TortoiseSDK


def _extract_role(fpath: Path) -> str | None:
    """Extract role name from frontmatter subjectKind or filename stem."""
    text = fpath.read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.startswith("subjectKind:") or line.startswith("role:"):
            return line.split(":", 1)[1].strip().strip("\"'")
    return fpath.stem.replace("-", " ").title()


def sync_roles(root: str = ".", db_path: str = "tortoise.db") -> dict:
    """Walk operations/subjects/ and create Subject(role) nodes."""
    sdk = TortoiseSDK(db_path)
    subjects_dir = Path(root) / "operations" / "subjects"
    count = 0

    if subjects_dir.is_dir():
        for fpath in subjects_dir.rglob("*.md"):
            name = _extract_role(fpath)
            if name:
                sdk.create_subject(name, "role")
                count += 1

    sdk.close()
    return {"roles": count}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Sync role registry → graph")
    ap.add_argument("--root", default=".", help="Repo root (default: .)")
    ap.add_argument("--db", default="tortoise.db", help="Path to tortoise.db")
    args = ap.parse_args()
    result = sync_roles(args.root, args.db)
    print(f"Synced: {result['roles']} roles → {args.db}")
