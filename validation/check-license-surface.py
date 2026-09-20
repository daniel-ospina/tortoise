#!/usr/bin/env python3
"""License-surface consistency check (#338 T3.3, repo-local).

Asserts all four surfaces declare BSL 1.1 + the $5M AUG + MPL 2.0
conversion, so the pre-#338 tri-state (README=BSL / LICENSE=AGPL /
pyproject=MIT) cannot re-occur. Root cause of the tri-state: the graph
licensing decision (DEC-002) was never synced to files — this check is
the mechanical backstop.

Also asserts the NON-BSL surfaces that must stay permissive, because a
per-directory licence is otherwise invisible to CI and an artifact can
silently inherit the repo default — the #526 client dist (Apache-2.0) and,
since #4366, the served consumer skills surface (MIT). See
`docs/license-notes.md` §6 and §7.

Repo-local by design: `scripts/` is an agent-infra symlink; this lives in
`validation/` per AGENTS.md. Wired into `.github/workflows/ci.yml` at T5.3
(after all four files converge), NOT python-ci.yml (agent-infra template).
The `license-surface` job is a REQUIRED branch-protection check with no
path gate, so a consumer-surface regression fails every PR.
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

# #4366 — consumer-facing skills surface (MIT by owner decision 2026-09-20;
# docs/license-notes.md §7). The skills the dashboard SERVES at
# app.premiselabs.co/skills/** (vite copies website/apps/dashboard/public/ into
# the Pages upload undisturbed) are the artifact a customer or a third-party
# product vendors/adapts — the same class of artifact as the #526 client dist.
# They carried NO licence of their own, so they inherited the repo's BSL 1.1 by
# default, which puts the BSL boundary at the FILE instead of at the network —
# the exact outcome the service model exists to avoid.
#
# Two fail-closed assertions, because either half alone is vacuously green:
#   1. the per-directory MIT licence exists and declares MIT (so deleting it,
#      gutting it, or swapping it for the BSL text REDs); and
#   2. no file under the surface declares a BSL/BUSL licence (so a NEW file
#      dropped into public/skills/ carrying a copy-pasted BSL header cannot
#      silently inherit the engine licence).
# Markers are the unambiguous LICENCE DECLARATIONS, not the bare string "BSL":
# the served skills legitimately discuss BSL in prose (how-to-use-tortoise's
# decision-comparison examples), and a check that reds on a skill explaining
# licensing would be one people learn to ignore.
CONSUMER_SURFACES = {
    "website/apps/dashboard/public/skills": {
        "path": ROOT / "website" / "apps" / "dashboard" / "public" / "skills",
        "licence": ROOT / "website" / "apps" / "dashboard" / "public" / "skills" / "LICENSE",
        "required": [
            "MIT License",
            "Copyright (c) 2026 Premise Labs",
            "Permission is hereby granted, free of charge",
        ],
    },
}

# Two marker classes, because a licence DECLARATION and a PROSE MENTION look
# different and only one of them is a regression:
#   * TOKENS are unambiguous wherever they appear — the SPDX id with or without
#     its version (`BUSL`, `BUSL-1.1`), scanned across the WHOLE file; the bare
#     acronym `BSL` is deliberately NOT a token because the served
#     how-to-use-tortoise skill discusses BSL in prose.
#   * The human-readable canonical NAME ("Business Source License[ 1.1]") is how
#     that same skill DISCUSSES licensing, so it counts only in the DECLARATION
#     WINDOW — the leading lines of the file (frontmatter + a header comment),
#     which is where a licence header lives and where prose does not.
# Both are matched case-insensitively with runs of whitespace collapsed, so a
# re-imported header cannot slip past on casing or column alignment. LIMIT (by
# design, documented in docs/license-notes.md §7): a file that is not valid
# UTF-8 is skipped — the surface is text; a binary asset dropped into it would
# carry no assertion.
DECLARATION_WINDOW = 20
BSL_TOKENS = (re.compile(r"\bbusl\b", re.I),)
BSL_NAMES = (re.compile(r"business\s+source\s+license(\s+1\.1)?", re.I),)
# A file whose whole body IS (or carries) a licence/notice — `LICENSE*`,
# `COPYING*`, `NOTICE*`, `*.license`/`*.licence`/`*.copying`/`*.notice` (REUSE
# sidecars), or anything under a `LICENSES/` dir — is scanned whole-file for the
# canonical NAME, not just in its first lines: the real BSL text carries the
# name at line 2 AND again at line 28 and never carries the `BUSL` token, so a
# composite file that keeps the MIT markers and appends the BSL terms would slip
# past a window scan (verified bypasses: a composite `LICENSE`, and — after the
# first fix — a composite `*.license` sidecar, both caught in review).
LICENCE_FILE_RE = re.compile(
    r"^(licen[cs]e|copying|notice)(\..+)?$|\.(licen[cs]e|copying|notice)$", re.I
)


def is_licence_file(path: Path) -> bool:
    # search(), not match(): the `*.license` sidecar form anchors on the
    # SUFFIX, so a name like `MIT.license` can only be found by searching.
    return bool(LICENCE_FILE_RE.search(path.name)) or any(
        part.upper() == "LICENSES" for part in path.parts
    )


def bsl_declaration(text: str, *, whole_file_names: bool = False) -> str | None:
    """The first BSL DECLARATION in `text`, or None. Never matches body prose."""
    for pattern in BSL_TOKENS:
        if pattern.search(text):
            return pattern.pattern
    scope = text if whole_file_names else "\n".join(text.splitlines()[:DECLARATION_WINDOW])
    for pattern in BSL_NAMES:
        if pattern.search(scope):
            return pattern.pattern
    return None


def _display(path: Path) -> Path:
    """Repo-relative where possible, absolute otherwise — never raise.

    A surface is normally under ROOT, but the functions below must stay usable
    (and testable) for any path, so they do not assume it.
    """
    try:
        return path.relative_to(ROOT)
    except ValueError:
        return path


def check_consumer_surface(name: str, spec: dict) -> list[str]:
    """MIT licence present + no BSL declaration anywhere under the surface."""
    errors: list[str] = []
    licence = spec["licence"]
    if not licence.exists():
        errors.append(
            f"{name}: no per-directory licence at {_display(licence)} — the "
            "surface inherits the repo's BSL 1.1 by default (#4366)"
        )
    else:
        text = licence.read_text()
        for needle in spec["required"]:
            if needle not in text:
                errors.append(f"{name}: {licence.name} is not a valid MIT licence — missing '{needle}'")

    # Recursive: a file added anywhere in the surface (a new skill dir, a new
    # asset) is covered by the directory licence above, so the only way it can
    # re-import BSL is by declaring it in the file itself. The licence file is
    # scanned too — NO exemption: a composite LICENSE that keeps the MIT
    # markers and appends BSL terms is the same copy-paste accident class, and
    # a presence-only MIT check would pass it (verified bypass, cycle-2 review).
    for path in sorted(p for p in spec["path"].rglob("*") if p.is_file()):
        rel = _display(path)
        try:
            body = path.read_text()
        except UnicodeDecodeError:
            continue  # documented limit: only UTF-8 text is asserted
        declared = bsl_declaration(body, whole_file_names=is_licence_file(path))
        if declared is not None:
            errors.append(
                f"{name}: {rel} declares BSL 1.1 ('{declared}') — a consumer-consumed "
                "artifact must not carry the engine's licence (#4366)"
            )
    return errors


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
    for name, spec in CONSUMER_SURFACES.items():
        errors.extend(check_consumer_surface(name, spec))
    return errors


def main() -> int:
    errors = check()
    if errors:
        print("❌ License surface inconsistent:")
        for e in errors:
            print(f"   - {e}")
        print("   Engine: all four surfaces must declare BSL 1.1 + $5M AUG + MPL 2.0 conversion.")
        print("   Client (#526): client/LICENSE, client/pyproject.toml, client/README.md must declare Apache-2.0.")
        print("   Consumer skills (#4366): website/apps/dashboard/public/skills/LICENSE must declare MIT, and")
        print("   no file under that surface may declare BSL 1.1/BUSL-1.1.")
        return 1
    print("✅ License surfaces consistent: engine BSL 1.1 ($5M AUG + MPL 2.0); tortoise-client Apache-2.0; "
          "served skills surface MIT.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
