#!/usr/bin/env python3
"""#7043 Product inventory — parse docs/teams/*/product/ → Object nodes.

    python tortoise/scripts/sync_products.py --root . --db tortoise.db

Walks docs/teams/*/product/ directories, extracts product names from
markdown/YAML frontmatter, and creates Object nodes (objectKind: product).

ponytail: walk + yaml.safe_load; no schema library needed.
"""
from __future__ import annotations

import sys
from pathlib import Path

_repo_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_repo_root))

from tortoise.sdk import TortoiseSDK


def _extract_name(fpath: Path) -> str | None:
    """Extract product name from frontmatter title or filename stem."""
    text = fpath.read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.startswith("title:"):
            return line.split(":", 1)[1].strip().strip("\"'")
    return fpath.stem.replace("-", " ").title()


def sync_products(root: str = ".", db_path: str = "tortoise.db") -> dict:
    """Walk docs/teams/*/product/ and create Object(product) nodes."""
    sdk = TortoiseSDK(db_path)
    products_dir = Path(root) / "docs" / "teams"
    count = 0

    for product_dir in products_dir.glob("*/product"):
        for fpath in product_dir.glob("*.md"):
            name = _extract_name(fpath)
            if name:
                sdk.create_object(name, "product")
                count += 1

    sdk.close()
    return {"products": count}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Sync product inventory → graph")
    ap.add_argument("--root", default=".", help="Repo root (default: .)")
    ap.add_argument("--db", default="tortoise.db", help="Path to tortoise.db")
    args = ap.parse_args()
    result = sync_products(args.root, args.db)
    print(f"Synced: {result['products']} products → {args.db}")
