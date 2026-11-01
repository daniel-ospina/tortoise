#!/usr/bin/env python3
"""Build the ChatGPT-installable `.skill` archives for the served skills.

Why an archive at all (#9660): the ChatGPT skill uploader accepts ONLY a file
named `SKILL.md`, a `.zip`, or a `.skill` (verified in the live UI — a file
named `tortoise-file-finding.md` is refused: "Choose a .zip, .skill, or
SKILL.md file"). Several skills therefore cannot ship as several `SKILL.md`
downloads: they collide in the user's Downloads folder, and the renamed copies
are rejected. A `.skill` archive carries the skill folder inside it, so each
file downloads with its own name and uploads as-is.

One archive = one skill: a `.zip` holding several skill folders is rejected by
the uploader ("Couldn't upload this skill"). Do not merge them.

Layout inside the archive mirrors the uploader's expectation — the skill's root
directory is named after the skill and holds `SKILL.md`:

    <name>.skill
      └── <name>/SKILL.md

The archives are derived artifacts (checked in because the docs site has no
build step); `tests/test_onboarding_variants.py` fails if one drifts from its
source `SKILL.md`.

Usage:
    uv run python tools/build_skill_archives.py            # write changed archives
    uv run python tools/build_skill_archives.py --check    # exit 1 if any is stale
"""
from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter before the imports below — a module-level
# 3.11+-only import (`from datetime import UTC`) would fail first (D9 shape).
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/build_skill_archives.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        "`uv run python tools/build_skill_archives.py`"
    )

import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

#: The SERVED copies are the archive source (one source, one mirror): the
#: dashboard's `public/skills/<name>/SKILL.md` is the byte the parity gate
#: (`tools/served_skills_parity.py`) compares against the upstream MIT repo, and
#: it is also the file `dist/` publishes at
#: app.premiselabs.co/skills/<name>/SKILL.md. Building from it means the archive
#: cannot disagree with either. A third copy under `website/skills/` was tried
#: and removed: it added a hand-kept duplicate of every skill's markdown, which
#: the `docs` lint gate then flagged per new file, for no consumer.
SERVED_SKILLS_REL = (Path("website") / "apps" / "dashboard" / "public"
                     / "skills")
SERVED_SKILLS = REPO_ROOT / SERVED_SKILLS_REL

#: The docs origin serves the built archives at
#: https://tortoise.premiselabs.co/skills/<name>.skill.
SKILLS_DIR_REL = Path("website") / "skills"
SKILLS_DIR = REPO_ROOT / SKILLS_DIR_REL

#: Every skill the ChatGPT card hands over. Keep in step with
#: website/apps/dashboard/src/harnesses.js (SKILL_ARCHIVE_NAMES) and
#: tests/test_onboarding_variants.py::SERVED_SKILL_NAMES — the test reads all
#: three lists and fails on any divergence.
#:
#: ⛔ `tortoise-onboarding` is deliberately ABSENT (#4365): it is a one-time setup
#: flow that is READ at its served URL, never installed into a skills namespace.
#: Building an archive for it here would hand the card a file to upload that its
#: own first screen forbids uploading.
SKILL_NAMES = (
    "how-to-use-tortoise",
    "tortoise-decide",
    "tortoise-file-finding",
)

#: The only arguments this tool accepts. Anything else is REJECTED: a tool whose
#: job is to refuse drift must not silently fall through to WRITE mode when a
#: flag is mistyped (a `--chek` used to rewrite the tree and exit 0).
_ALLOWED_ARGV = frozenset({(), ("--check",)})

#: A fixed timestamp keeps the archive byte-stable, so regenerating without a
#: source change produces no diff.
_FIXED_DATE = (1980, 1, 1, 0, 0, 0)


def archive_path(name: str, root: Path = REPO_ROOT) -> Path:
    return root / SKILLS_DIR_REL / f"{name}.skill"


def source_path(name: str, root: Path = REPO_ROOT) -> Path:
    return root / SERVED_SKILLS_REL / name / "SKILL.md"


def build(name: str, root: Path = REPO_ROOT) -> bytes:
    """Bytes of the `.skill` archive for `name` (a zip with ONE entry)."""
    src = source_path(name, root)
    if not src.is_file():
        raise SystemExit(f"missing source skill: {src}")
    import io

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        info = zipfile.ZipInfo(f"{name}/SKILL.md", date_time=_FIXED_DATE)
        info.external_attr = 0o644 << 16
        info.compress_type = zipfile.ZIP_DEFLATED
        zf.writestr(info, src.read_bytes())
    return buf.getvalue()


def write_stale_archives(root: Path = REPO_ROOT, names=None) -> list[str]:
    """Write every STALE archive under `root`; return the names written.

    ⛔ NOT called `rebuild`. `tests/test_destructive_wipe_guard.py` pins every
    `*.rebuild(...)`/`*.rebuild_all(...)` call site in `tests/` to a literal
    `confirm_destructive` boolean, because `tortoise`'s `rebuild`/`rebuild_all`
    DETACH-DELETE a projection. That pin is a name-matched AST sweep, so a
    non-destructive function sharing the name reds it (`#10423`: 3 sites in
    `tests/test_served_skills_parity.py`), and allowlisting archive-building in
    a destructive-wipe pin trades a real safety list for a naming accident. A
    distinct name keeps both honest: this function writes a zip, it never wipes.

    The callable half of the `--check`/write pair, so the step that INVALIDATES
    an archive can re-cut it without shelling out. That is the whole point
    (#10423): `tools/served_skills_parity.py --sync` re-derives the SERVED copy
    — the archive's only source — and used to leave the archive stale, so the
    documented remedy for mirror drift produced a tree that reds
    `test_9660_skill_archive_holds_exactly_its_own_skill`. Twice in two days:
    cd7410b80 (#9750 round 3) and f045ac953 (#10423), same test, same rebuild.

    Byte-stable: `_FIXED_DATE` plus the source bytes alone decide the archive,
    so writing an already-current archive writes nothing and reports nothing.
    A name with no served source under `root` is SKIPPED rather than raising —
    a caller may be driving a partial tree, and the archive of a skill that
    does not exist there is not this function's verdict to give (the parity
    gate reports a missing served copy).
    """
    wanted_names = SKILL_NAMES if names is None else tuple(names)
    written: list[str] = []
    for name in wanted_names:
        if not source_path(name, root).is_file():
            continue
        target = archive_path(name, root)
        payload = build(name, root)
        if target.is_file() and target.read_bytes() == payload:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        written.append(name)
    return written


def main(argv: list[str]) -> int:
    if tuple(argv) not in _ALLOWED_ARGV:
        # Fail LOUD rather than defaulting to write mode: an unrecognised
        # argument is a caller mistake, and the destructive path must never be
        # the fallback for one (#9660 review).
        print(
            f"unrecognised argument(s): {' '.join(argv)}\n"
            "usage: build_skill_archives.py [--check]",
            file=sys.stderr,
        )
        return 2
    check = argv == ["--check"]
    stale: list[str] = []
    for name in SKILL_NAMES:
        target = archive_path(name)
        wanted = build(name)
        current = target.read_bytes() if target.is_file() else None
        if current == wanted:
            continue
        stale.append(name)
        if not check:
            target.write_bytes(wanted)
    for name in stale:
        print(f"{'stale' if check else 'wrote'}: {archive_path(name).name}")
    if check and stale:
        print("run: uv run python tools/build_skill_archives.py", file=sys.stderr)
        return 1
    if not stale:
        print(f"all {len(SKILL_NAMES)} archives are current")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
