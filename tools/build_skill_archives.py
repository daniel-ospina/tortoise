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
SERVED_SKILLS = REPO_ROOT / "website" / "apps" / "dashboard" / "public" / "skills"

#: The docs origin serves the built archives at
#: https://tortoise.premiselabs.co/skills/<name>.skill.
SKILLS_DIR = REPO_ROOT / "website" / "skills"

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


def archive_path(name: str) -> Path:
    return SKILLS_DIR / f"{name}.skill"


def source_path(name: str) -> Path:
    return SERVED_SKILLS / name / "SKILL.md"


def build(name: str, source: Path | None = None) -> bytes:
    """Bytes of the `.skill` archive for `name` (a zip with ONE entry).

    ``source`` overrides the file the payload is read from; the default is the
    served copy in this checkout. It exists for a caller that has already
    resolved a DIFFERENT root (a test zipping an arbitrary file through this
    entry point).

    ⛔ ``--sync`` does NOT use it. The sync derives the served copy itself and
    must archive the bytes it FETCHED, so it calls ``build_from_bytes`` with the
    payload in memory — building from this root's file is exactly the disk read
    that made `--sync --dry-run` zip the pre-sync mirror and report "no archive
    changes" over a stale one (#10088). Keep the two entry points agreeing about
    what an archive is; do not route a sync through ``source``.
    """
    src = source_path(name) if source is None else source
    if not src.is_file():
        raise SystemExit(f"missing source skill: {src}")
    return build_from_bytes(name, src.read_bytes())


def build_from_bytes(name: str, payload: bytes) -> bytes:
    """Bytes of the `.skill` archive for `name` from a skill body in MEMORY.

    The archive is a pure function of the skill body, so the body does not have
    to exist on disk to be archived. That is what lets a caller report the
    archive it WOULD derive without writing anything — `--sync --dry-run` reads
    no post-write file (there is none) and must still name the artifacts a real
    run would rewrite; building from the fetched payload it already holds is the
    only way to say that truthfully. A dry run that built from disk would zip
    the PRE-sync mirror, find it equal to the stale archive, and report "no
    archive changes" on exactly the #10088 shape.

    ``build`` delegates here, so the two entry points cannot drift apart about
    what a `.skill` byte stream is.
    """
    import io

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        info = zipfile.ZipInfo(f"{name}/SKILL.md", date_time=_FIXED_DATE)
        info.external_attr = 0o644 << 16
        info.compress_type = zipfile.ZIP_DEFLATED
        zf.writestr(info, payload)
    return buf.getvalue()


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
