#!/usr/bin/env python3
"""License-surface consistency check (#338 T3.3, repo-local).

Asserts all four surfaces declare BSL 1.1 + the $5M AUG + MPL 2.0
conversion, so the pre-#338 tri-state (README=BSL / LICENSE=AGPL /
pyproject=MIT) cannot re-occur. Root cause of the tri-state: the graph
licensing decision (DEC-002) was never synced to files — this check is
the mechanical backstop.

Repo-local by design: `scripts/` is an agent-infra symlink; this lives in
`validation/` per AGENTS.md. Wired into `.github/workflows/ci.yml` at T5.3
(after all four files converge), NOT python-ci.yml (agent-infra template).
"""
from __future__ import annotations

import re  # noqa: F401
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SURFACES = {
    "LICENSE": {
        "path": ROOT / "LICENSE",
        "required": ["Business Source License 1.1",
                     "$5,000,000",
                     "Mozilla Public License, Version 2.0"],
    },
    "README.md": {
        "path": ROOT / "README.md",
        "required": ["Business Source License", "Business Source License 1.1",
                     "$5,000,000", "Mozilla Public License"],
    },
    "pyproject.toml": {
        "path": ROOT / "pyproject.toml",
        "required": ["BUSL-1.1"],
    },
    "index.md": {
        "path": ROOT / "index.md",
        "required": ["Business Source License", "Mozilla Public License"],
    },
}

# #526 client/server split — the thin driver distribution (client/) is
# Apache-2.0 by design (MongoDB/Redis driver precedent; see
# docs/client-server-split.md §License). These surfaces must declare
# Apache-2.0 so the boundary cannot silently regress (same backstop pattern
# as the engine's four-surface check above).
CLIENT_SURFACES = {
    "client/LICENSE": {
        "path": ROOT / "client" / "LICENSE",
        "required": ["Apache License", "Version 2.0"],
    },
    "client/pyproject.toml": {
        "path": ROOT / "client" / "pyproject.toml",
        "required": ["Apache-2.0"],
    },
    "client/README.md": {
        "path": ROOT / "client" / "README.md",
        "required": ["Apache-2.0"],
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
    for name, spec in CLIENT_SURFACES.items():
        path = spec["path"]
        if not path.exists():
            errors.append(f"{name}: file missing ({path})")
            continue
        text = path.read_text()
        for needle in spec["required"]:
            if needle not in text:
                errors.append(f"{name} (#526 client dist): missing '{needle}'")
    return errors


def main() -> int:
    errors = check()
    if errors:
        print("❌ License surface inconsistent:")
        for e in errors:
            print(f"   - {e}")
        print("   Engine: all four surfaces must declare BSL 1.1 + $5M AUG + MPL 2.0 conversion.")
        print("   Client (#526): client/LICENSE, client/pyproject.toml, client/README.md must declare Apache-2.0.")
        return 1
    print("✅ License surfaces consistent: engine BSL 1.1 ($5M AUG + MPL 2.0); tortoise-client Apache-2.0.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
