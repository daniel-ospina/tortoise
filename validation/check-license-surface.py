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
`docs/license-notes.md` — the "Client/Server Split" section (#526) and §7
(#4366).

Repo-local by design: `scripts/` is an agent-infra symlink; this lives in
`validation/` per AGENTS.md. Wired into `.github/workflows/ci.yml` at T5.3
(after all four files converge), NOT python-ci.yml (agent-infra template).
The `license-surface` job is a REQUIRED branch-protection check with no
path gate, so a consumer-surface regression fails every PR.
"""
from __future__ import annotations

import contextlib
import os
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
#     its version (`BUSL`, `BUSL-1.1`), scanned across the WHOLE file, prose
#     included (an artifact that names BUSL anywhere is claiming it). The bare
#     acronym `BSL` is deliberately NOT a token: the served how-to-use-tortoise
#     skill discusses BSL in prose and must not red a required check.
#   * The human-readable canonical NAME ("Business Source License[ 1.1]") is
#     matched only in the DECLARATION WINDOW — the leading lines of the file
#     (frontmatter + a header comment), which is where a licence header lives —
#     except in a licence/notice file, which is scanned whole.
# Both are matched case-insensitively with runs of whitespace collapsed, so a
# re-imported header cannot slip past on casing or column alignment. LIMIT (by
# design, documented in docs/license-notes.md §7): a file that is not valid
# UTF-8 carries no assertion, and a canonical NAME buried past the window of an
# ordinary content file is out of reach — the `BUSL` token is not, wherever it
# appears.
DECLARATION_WINDOW = 20
# `busl` / `BUSL-1.1` (the SPDX id, with or without its version) and the
# versioned short form `BSL[ ._-][v]1.1` (how vendored notices and
# mariadb.com/bsl11 abbreviate it — `BSL 1.1`, `BSL-1.1`, `BSL v1.1`). The
# UNVERSIONED acronym `BSL` is deliberately not a token — the served
# how-to-use-tortoise skill writes "BSL is OSI-approved" and "BSL+AGPL" in
# prose, and a version is what separates a claim from a mention.
BSL_TOKENS = (
    re.compile(r"\bbusl(-1\.1)?\b", re.I),
    re.compile(r"\bbsl[\s._-]*(?:v(?:ersion)?[\s._-]*)?1\.1\b", re.I),
)
BSL_NAMES = (re.compile(r"business\s+source\s+license(\s+1\.1)?", re.I),)
# A file whose NAME looks like a licence/notice is scanned whole-file for the
# canonical NAME, not just in its first lines: the real BSL text carries the name
# at line 2 AND again at line 28 and never carries the `BUSL` token, so a
# composite file that keeps the MIT markers and appends the BSL terms slips past
# a window scan. Recognised: any name carrying a licence/notice token followed
# by a non-alphanumeric or the end — `LICENSE`, `LICENSE.md`, `LICENSE-BSL`,
# `LICENSE 2.txt`, `LICENSE (copy).txt` (the duplicate names Finder/Windows
# produce), `license copy.txt`, `third_party_licenses.txt`,
# `THIRD-PARTY-NOTICES.txt`, `COPYING.LESSER`, `COPYRIGHT`. Deliberately NOT
# `licensee-notes.md` (an alphanumeric continuation means the word is not the
# token — it is prose about licensing), which is why a bare `LICENSE*` glob
# would be wrong; likewise a name with no separator at all (`LICENSEBSL`) is out
# of reach and is stated as such in docs/license-notes.md §7.
# Every form below was a review finding at some point: the original pattern
# missed `*.license` sidecars, then the dashed/suffixed forms, then the
# space/bracket forms.
LICENCE_FILE_RE = re.compile(
    r"(licen[cs]es?|copying|notices?|copyright)([^A-Za-z0-9]|$)", re.I
)
LICENCE_DIRS = ("LICENSES", "LICENCES", "NOTICES")


def is_licence_file(path: Path) -> bool:
    # search(), not match(): the sidecar form (`MIT.license`) and the suffixed
    # form (`third_party_licenses.txt`) anchor mid-string, so only a search finds
    # them — `match()` here is what let `MIT.license` bypass the check.
    return bool(LICENCE_FILE_RE.search(path.name)) or any(
        part.upper() in LICENCE_DIRS for part in path.parts
    )


def bsl_declaration(text: str, *, whole_file_names: bool = False) -> str | None:
    """The first BSL DECLARATION in `text`, or None.

    Returns the MATCHED TEXT — the evidence a maintainer needs — not the regex.
    A `BUSL` token counts anywhere, prose included; the canonical NAME counts
    only in the declaration window unless `whole_file_names` (a licence/notice
    file), which is what keeps the served skills' licensing prose from reding a
    required check.
    """
    for pattern in BSL_TOKENS:
        hit = pattern.search(text)
        if hit is not None:
            return hit.group(0)
    scope = text if whole_file_names else "\n".join(text.splitlines()[:DECLARATION_WINDOW])
    for pattern in BSL_NAMES:
        hit = pattern.search(scope)
        if hit is not None:
            return hit.group(0)
    return None


def _surface_files(root: Path) -> list[Path]:
    """Every regular file under `root`, following DIRECTORY symlinks.

    `Path.rglob` does not descend into a symlinked directory, so a skill dir
    symlinked into the surface would be served (the vite `public/` copy follows
    links) while carrying no assertion — a latent fail-open in the guard (found
    by the code-review gate). Directory cycles are pruned by (device, inode).
    """
    seen: set[tuple[int, int]] = set()
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=True):
        try:
            stat = os.stat(dirpath)
        except OSError:
            dirnames[:] = []
            continue
        key = (stat.st_dev, stat.st_ino)
        if key in seen:
            dirnames[:] = []
            continue
        seen.add(key)
        found.extend(Path(dirpath) / name for name in filenames)
    return sorted(found)


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
    licence_text: str | None = None
    if not licence.exists():
        errors.append(
            f"{name}: no per-directory licence at {_display(licence)} — the "
            "surface inherits the repo's BSL 1.1 by default (#4366)"
        )
    elif not licence.is_file():
        errors.append(f"{name}: {_display(licence)} is not a file")
    else:
        # The licence is the one file the assertion is centred on: an
        # undecodable one must be a NAMED error, not a traceback (the same
        # "not UTF-8 → no assertion" limit applies to it as to the rest of the
        # surface, so it cannot fail open silently either).
        try:
            licence_text = licence.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            errors.append(f"{name}: {_display(licence)} is not UTF-8 text — cannot assert its licence")
    if licence_text is not None:
        for needle in spec["required"]:
            if needle not in licence_text:
                errors.append(f"{name}: {licence.name} is not a valid MIT licence — missing '{needle}'")

    # Every file under the surface (symlinked dirs included) is covered by the
    # directory licence above, so the only way it can re-import BSL is by
    # declaring it in the file itself. The licence file is scanned too — NO
    # exemption: a composite LICENSE that keeps the MIT markers and appends BSL
    # terms is the same copy-paste accident class, and a presence-only MIT check
    # would pass it (verified bypass, review cycle 2).
    # The reads pin UTF-8 explicitly: `Path.read_text()` without an encoding
    # decodes in the PROCESS LOCALE, so under a C/POSIX locale every non-ASCII
    # file would raise UnicodeDecodeError and be silently skipped — silently
    # vacating the assertion for exactly the served skills (all four are
    # heavily non-ASCII). `encoding="utf-8"` makes the except branch mean what
    # it says (found by the code-review gate).
    for path in _surface_files(spec["path"]):
        if not path.is_file():
            continue
        rel = _display(path)
        try:
            body = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue  # documented limit: only UTF-8 text is asserted
        declared = bsl_declaration(body, whole_file_names=is_licence_file(path))
        if declared is not None:
            errors.append(
                f"{name}: {rel} declares BSL 1.1 (matched {declared!r}) — a consumer-consumed "
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
        text = path.read_text(encoding="utf-8")
        for needle in spec["required"]:
            if needle not in text:
                errors.append(f"{name}: missing '{needle}'")
    for name, spec in CLIENT_SURFACES.items():
        path = spec["path"]
        if not path.exists():
            errors.append(f"{name}: file missing ({path})")
            continue
        text = path.read_text(encoding="utf-8")
        for needle in spec["required"]:
            if needle not in text:
                errors.append(f"{name} (#526 client dist): missing '{needle}'")
    for name, spec in CONSUMER_SURFACES.items():
        errors.extend(check_consumer_surface(name, spec))
    return errors


def main() -> int:
    # The summary glyphs below cannot be encoded by a C/POSIX stdout, which turns
    # a diagnosis into a traceback; pin the stream the same way the file reads
    # are pinned (found while closing the review's locale finding).
    with contextlib.suppress(AttributeError, ValueError):
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
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
