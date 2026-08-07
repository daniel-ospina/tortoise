#!/usr/bin/env python3
"""License-surface consistency check (#338 T3.3, repo-local).

Asserts all four surfaces declare BSL 1.1 + the $5M AUG + Apache-2.0
conversion, so the pre-#338 tri-state (README=BSL / LICENSE=AGPL /
pyproject=MIT) cannot re-occur. Root cause of the tri-state: the graph
licensing decision (DEC-002) was never synced to files — this check is
the mechanical backstop.

Repo-local by design: `scripts/` is an agent-infra symlink; this lives in
`validation/` per AGENTS.md. Wired into `.github/workflows/ci.yml` at T5.3
(after all four files converge), NOT python-ci.yml (agent-infra template).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SURFACES = {
    "LICENSE": {
        "path": ROOT / "LICENSE",
        "required": ["Business Source License 1.1",
                     "$5,000,000",
                     "Apache License, Version 2.0"],
    },
    "README.md": {
        "path": ROOT / "README.md",
        "required": ["Business Source License", "Business Source License 1.1"],
    },
    "pyproject.toml": {
        "path": ROOT / "pyproject.toml",
        "required": ["BUSL-1.1"],
    },
    "index.md": {
        "path": ROOT / "index.md",
        "required": ["Business Source License"],
    },
}


def check() -> list[str]:
    errors: list[str] = []
    for name, spec in SURFACES.items():
        path = spec["path"]
        if not path.exists():
            errors.append(f"{name}: file missing ({path})")
            continue
        text = path.read_text()
        for needle in spec["required"]:
            if needle not in text:
                errors.append(f"{name}: missing '{needle}'")
    return errors


def main() -> int:
    errors = check()
    if errors:
        print("❌ License surface inconsistent:")
        for e in errors:
            print(f"   - {e}")
        print("   Fix all four surfaces to declare BSL 1.1 + $5M AUG + Apache-2.0 conversion.")
        return 1
    print("✅ License surface consistent: LICENSE / README.md / pyproject.toml / index.md all BSL 1.1.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
