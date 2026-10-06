"""#4327 — the installer must not silently strip foreign content it did not write.

The destination (``~/.pi/agent/skills`` for the Pi harness) is a flat, SHARED
namespace. Measured 2026-09-20: a different owner's tool (agent-infra) had
already written three of the basenames, byte-identical to its
``origin/main``, and the install replaced them verbatim — no merge, no backup,
no message — silently dropping two machine-wide conventions carried by 95-96
of 122 installed skills:

  * the ``subjects.team: organisation-design-team`` frontmatter key, and
  * the ``⛔ … MUST be read in full — not skimmed.`` banner + its trailing
    ``> Continue following the workflow …`` line.

Owner ruling (on #4327, 2026-09-20): those conventions are **required**, so
preserving foreign keys and foreign seam blocks is an acceptance criterion.

Every test here drives the REAL installer end-to-end — a temporary copy with
ONLY the ``SKILLS_BASE`` URL substituted for a local ``file://`` fixture — and
asserts the resolved bytes on disk, never source text.

Mutations that turn these RED (each verified RED before merge):
  * restore the plain ``mv "$tmp" "$DEST/$s/SKILL.md"`` write  -> the foreign
    key/banner/trailer assertions in ``test_merge_preserves_foreign_content``
    fail (that is the pre-fix behaviour: whole-file replacement);
  * drop the frontmatter union  -> ``subjects.team`` assertion fails;
  * drop the leading-banner seam -> the banner assertion fails;
  * drop the trailing-block seam -> the trailer assertion fails;
  * drop the ``.bak`` write      -> the backup assertion fails;
  * make the fresh path rewrite the payload -> the fresh-install byte-identity
    assertion fails.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALLER = (REPO_ROOT / "website" / "apps" / "dashboard" / "public"
             / "install-tortoise-skills.sh")

# #4365: the installer ships the 3 reusable capabilities. Onboarding is
# DELIVERED AS INSTRUCTIONS (a served document), never installed as a skill —
# so it is deliberately absent here; re-adding it must red the suite.
SKILLS = ("how-to-use-tortoise", "tortoise-decide", "tortoise-file-finding")

# The machine-wide conventions agent-infra adds to the skills it installs.
FOREIGN_KEY = "subjects.team: organisation-design-team"
BANNER = (
    "> ⛔ **This skill MUST be read in full — not skimmed.** "
    "Formal review gates depend on its workflow.\n"
    "> Skipping steps silently bypasses quality checks. "
    "Missing gates = undetected breakages."
)
TRAILER = (
    "> Continue following the workflow as mandated by this skill. "
    "Do not skip steps."
)

# Distinctive body markers so a merge (new payload wins) can be told from a
# replace (old body survives) or a union (both survive).
OLD_MARKER = "OLD-ONLY-BODY-MARKER-THAT-MUST-BE-GONE"
NEW_MARKER = "NEW-BODY-MARKER-THAT-MUST-BE-PRESENT"


def _payload(name: str, body: str) -> str:
    return (
        "---\n"
        f"name: {name}\n"
        f"description: fixture payload for {name}\n"
        "domain: capability\n"
        "type: Workflow\n"
        "status: live\n"
        "updated: 2026-09-20\n"
        "allowed-tools: read write bash grep\n"
        "---\n"
        "\n"
        "> ⛔ **Read this skill in full before using it.** "
        "Fixture own-banner line.\n"
        "\n"
        f"# {name}\n"
        "\n"
        f"{body}\n"
    )


def _foreign_skill(name: str, body: str) -> str:
    """An older Tortoise payload as a FOREIGN tool would have left it: the
    machine convention inserted (extra frontmatter key + banner + trailer)."""
    return (
        "---\n"
        f"name: {name}\n"
        f"description: stale fixture payload for {name}\n"
        "domain: capability\n"
        f"{FOREIGN_KEY}\n"
        "type: Workflow\n"
        "status: live\n"
        "updated: 2026-01-01\n"
        "allowed-tools: read write bash grep\n"
        "---\n"
        f"{BANNER}\n"
        "\n"
        "> ⛔ **Read this skill in full before using it.** "
        "Fixture own-banner line.\n"
        "\n"
        f"# {name}\n"
        "\n"
        f"{body}\n"
        "---\n"
        f"{TRAILER}\n"
    )


def _write_fixture_tree(root: Path, bodies: dict[str, str]) -> Path:
    """Materials a local `file://` skills tree: <root>/<name>/SKILL.md."""
    base = root / "served-skills"
    for name in SKILLS:
        d = base / name
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(
            _payload(name, bodies.get(name, f"fixture body for {name}")),
            encoding="utf-8")
    return base


def _patched_installer(tmp_path: Path, base_uri: str) -> Path:
    """The REAL installer, with only its download base swapped for `base_uri`."""
    src = INSTALLER.read_text(encoding="utf-8")
    patched, n = re.subn(r'^SKILLS_BASE=.*$', f'SKILLS_BASE="{base_uri}"', src,
                         count=1, flags=re.M)
    assert n == 1, "installer SKILLS_BASE anchor not found — update this test"
    out = tmp_path / "installer.sh"
    out.write_text(patched, encoding="utf-8")
    return out


def _run(installer: Path, home: Path, cwd: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["HOME"] = str(home)
    # No stdin is provided: a non-interactive install must never block.
    return subprocess.run(
        ["bash", str(installer), "--harness", "pi"],
        env=env, cwd=str(cwd), capture_output=True, text=True, timeout=60,
        stdin=subprocess.DEVNULL,
    )


def _fixture(tmp_path: Path, bodies: dict[str, str]):
    """Returns (installer, home, skills_dir, served_base)."""
    served = _write_fixture_tree(tmp_path, bodies)
    installer = _patched_installer(tmp_path, f"file://{served}")
    home = tmp_path / "home"
    skills = home / ".pi" / "agent" / "skills"
    return installer, home, skills, served


def test_merge_preserves_foreign_content_and_backs_up(tmp_path):
    """A same-named foreign file is merged, not replaced: foreign frontmatter
    key + banner + trailer survive, the new payload wins, the old body is gone,
    and the pre-existing bytes are recoverable from a `.bak`."""
    installer, home, skills, _ = _fixture(
        tmp_path, {"tortoise-decide": NEW_MARKER})

    dest_dir = skills / "tortoise-decide"
    dest_dir.mkdir(parents=True)
    dest = dest_dir / "SKILL.md"
    foreign_original = _foreign_skill("tortoise-decide", OLD_MARKER)
    dest.write_text(foreign_original, encoding="utf-8")

    res = _run(installer, home, tmp_path)
    assert res.returncode == 0, f"installer failed:\n{res.stdout}\n{res.stderr}"

    merged = dest.read_text(encoding="utf-8")

    # Foreign content preserved (the owner's acceptance criteria).
    assert FOREIGN_KEY in merged, "foreign frontmatter key was stripped"
    assert BANNER in merged, "foreign banner block was stripped"
    assert TRAILER in merged, "foreign trailer block was stripped"
    # New payload wins; the stale body must not survive.
    assert NEW_MARKER in merged, "new payload body missing"
    assert OLD_MARKER not in merged, "stale body survived a merge"
    # Exactly one of each convention — no duplication across a merge.
    assert merged.count("MUST be read in full — not skimmed") == 1
    assert merged.count("Continue following the workflow as mandated") == 1
    assert merged.count(FOREIGN_KEY) == 1
    # Frontmatter is still valid YAML and keeps the payload's own fields.
    assert merged.startswith("---\nname: tortoise-decide\n")
    assert "updated: 2026-09-20" in merged

    # The pre-existing bytes are recoverable, verbatim.
    bak = dest.with_name("SKILL.md.bak")
    assert bak.exists(), "no backup of the pre-existing foreign copy was made"
    assert bak.read_text(encoding="utf-8") == foreign_original, (
        "the backup is not the pre-existing copy")

    # It said what it did.
    assert "preserved-frontmatter: subjects.team" in res.stdout
    assert "preserved-banner" in res.stdout
    assert "preserved-trailer" in res.stdout
    assert str(bak) in res.stdout


def test_merge_is_idempotent_and_first_backup_wins(tmp_path):
    """A re-run changes nothing on disk and does not overwrite the single
    pre-Tortoise backup with its own merged output."""
    installer, home, skills, _ = _fixture(
        tmp_path, {"tortoise-decide": NEW_MARKER})
    dest_dir = skills / "tortoise-decide"
    dest_dir.mkdir(parents=True)
    dest = dest_dir / "SKILL.md"
    foreign_original = _foreign_skill("tortoise-decide", OLD_MARKER)
    dest.write_text(foreign_original, encoding="utf-8")

    assert _run(installer, home, tmp_path).returncode == 0
    first = dest.read_bytes()

    res2 = _run(installer, home, tmp_path)
    assert res2.returncode == 0, res2.stderr
    assert dest.read_bytes() == first, "re-run was not idempotent"
    assert dest.with_name("SKILL.md.bak").read_text(
        encoding="utf-8") == foreign_original, "second run clobbered the backup"


def test_fresh_install_is_byte_identical_to_the_payload(tmp_path):
    """No pre-existing file (the customer path) — the payload is written
    verbatim, with no backup and no merge.**"""
    installer, home, skills, served = _fixture(tmp_path, {})
    res = _run(installer, home, tmp_path)
    assert res.returncode == 0, res.stderr

    for name in SKILLS:
        installed = (skills / name / "SKILL.md").read_bytes()
        served_bytes = (served / name / "SKILL.md").read_bytes()
        assert installed == served_bytes, (
            f"fresh install of {name} is not the payload verbatim")
        assert not (skills / name / "SKILL.md.bak").exists()
    assert "merged" not in res.stdout


def test_uncollided_skill_is_written_verbatim_even_when_a_sibling_collides(
        tmp_path):
    """Preservation is per-skill: only the colliding destination is merged;
    an untouched destination is still the payload verbatim."""
    installer, home, skills, served = _fixture(
        tmp_path, {"tortoise-decide": NEW_MARKER})
    dest_dir = skills / "tortoise-decide"
    dest_dir.mkdir(parents=True)
    (dest_dir / "SKILL.md").write_text(
        _foreign_skill("tortoise-decide", OLD_MARKER), encoding="utf-8")

    assert _run(installer, home, tmp_path).returncode == 0

    for name in ("how-to-use-tortoise", "tortoise-file-finding"):
        assert (skills / name / "SKILL.md").read_bytes() == (
            served / name / "SKILL.md").read_bytes()


def test_onboarding_is_not_installed_even_when_a_copy_is_present(tmp_path):
    """#4365: onboarding is not in the installed set. A pre-existing
    ``tortoise-onboarding/`` in the destination is neither overwritten nor
    merged — the installer never touches a skill it does not ship."""
    installer, home, skills, _ = _fixture(tmp_path, {})
    dest = skills / "tortoise-onboarding" / "SKILL.md"
    dest.parent.mkdir(parents=True)
    original = "PRE-EXISTING ONBOARDING COPY\n"
    dest.write_text(original, encoding="utf-8")

    res = _run(installer, home, tmp_path)
    assert res.returncode == 0, f"installer failed:\n{res.stdout}\n{res.stderr}"
    assert dest.read_text(encoding="utf-8") == original, (
        "the installer must not touch tortoise-onboarding — it is not shipped")
    assert not dest.with_name("SKILL.md.bak").exists(), (
        "no backup is made for a skill the installer does not ship")


def test_payload_name_sanity_check_still_refuses_a_wrong_payload(tmp_path):
    """Constraint honoured: the existing `^name: <skill>$` payload check is not
    weakened — a payload whose frontmatter name does not match is refused and
    the destination is left untouched."""
    installer, home, skills, served = _fixture(tmp_path, {})
    # Corrupt one served payload's name, leaving its body intact.
    bad = served / "tortoise-decide" / "SKILL.md"
    bad.write_text(
        bad.read_text(encoding="utf-8").replace(
            "name: tortoise-decide", "name: some-other-skill"),
        encoding="utf-8")

    dest_dir = skills / "tortoise-decide"
    dest_dir.mkdir(parents=True)
    dest = dest_dir / "SKILL.md"
    dest.write_text("PRE-EXISTING\n", encoding="utf-8")

    res = _run(installer, home, tmp_path)
    assert res.returncode != 0, "wrong-name payload was accepted"
    assert "payload was not the skill file" in res.stdout + res.stderr
    assert dest.read_text(encoding="utf-8") == "PRE-EXISTING\n", (
        "destination was touched despite the failed sanity check")


def test_installer_has_no_leftover_temp_files(tmp_path):
    """The merge leaves no `.merged.`/`.preserved.` scratch files behind."""
    installer, home, skills, _ = _fixture(
        tmp_path, {"tortoise-decide": NEW_MARKER})
    dest_dir = skills / "tortoise-decide"
    dest_dir.mkdir(parents=True)
    (dest_dir / "SKILL.md").write_text(
        _foreign_skill("tortoise-decide", OLD_MARKER), encoding="utf-8")
    assert _run(installer, home, tmp_path).returncode == 0

    leftovers = [p.name for p in skills.rglob("*")
                 if ".merged." in p.name or ".preserved." in p.name
                 or p.name.endswith(".tmp")]
    assert leftovers == [], f"leftover temp files: {leftovers}"


def _payload_plain_body(name: str, body: str) -> str:
    """A payload whose body starts with a heading, NOT a blockquote — the
    real `tortoise-file-finding` shape (no leading banner in the payload)."""
    return (
        "---\n"
        f"name: {name}\n"
        f"description: fixture payload for {name}\n"
        "domain: capability\n"
        "updated: 2026-09-20\n"
        "---\n"
        "\n"
        f"# {name}\n"
        "\n"
        f"{body}\n"
    )


def test_merge_is_idempotent_when_the_payload_has_no_leading_banner(tmp_path):
    """The seam logic must not depend on the payload opening with a blockquote
    (the real `tortoise-file-finding` body opens with a heading)."""
    installer, home, skills, served = _fixture(
        tmp_path, {"tortoise-decide": NEW_MARKER})
    (served / "tortoise-decide" / "SKILL.md").write_text(
        _payload_plain_body("tortoise-decide", NEW_MARKER), encoding="utf-8")
    dest_dir = skills / "tortoise-decide"
    dest_dir.mkdir(parents=True)
    dest = dest_dir / "SKILL.md"
    dest.write_text(_foreign_skill("tortoise-decide", OLD_MARKER),
                    encoding="utf-8")

    assert _run(installer, home, tmp_path).returncode == 0
    first = dest.read_bytes()
    merged = dest.read_text(encoding="utf-8")
    assert FOREIGN_KEY in merged and BANNER in merged and TRAILER in merged
    assert NEW_MARKER in merged and OLD_MARKER not in merged
    # The foreign banner still precedes the payload's heading.
    assert merged.index("MUST be read in full — not skimmed") < merged.index(
        "# tortoise-decide")

    assert _run(installer, home, tmp_path).returncode == 0
    assert dest.read_bytes() == first, "re-run was not idempotent"


def test_pre_existing_backup_is_never_clobbered(tmp_path):
    """First-backup-wins: an existing `.bak` (e.g. from an earlier install)
    must survive untouched, so the oldest recoverable revision is not lost."""
    installer, home, skills, _ = _fixture(
        tmp_path, {"tortoise-decide": NEW_MARKER})
    dest_dir = skills / "tortoise-decide"
    dest_dir.mkdir(parents=True)
    dest = dest_dir / "SKILL.md"
    dest.write_text(_foreign_skill("tortoise-decide", OLD_MARKER),
                    encoding="utf-8")
    bak = dest.with_name("SKILL.md.bak")
    sentinel = "PRIOR-BACKUP-SENTINEL — must not be overwritten\n"
    bak.write_text(sentinel, encoding="utf-8")

    res = _run(installer, home, tmp_path)
    assert res.returncode == 0, res.stderr
    assert bak.read_text(encoding="utf-8") == sentinel
    assert "existing backup kept" in res.stdout
    assert FOREIGN_KEY in dest.read_text(encoding="utf-8")


def test_multi_paragraph_foreign_banner_keeps_its_blank_line(tmp_path):
    """A foreign banner made of two blockquote paragraphs is preserved with
    the blank line between them (not flattened into one paragraph)."""
    installer, home, skills, _ = _fixture(
        tmp_path, {"tortoise-decide": NEW_MARKER})
    dest_dir = skills / "tortoise-decide"
    dest_dir.mkdir(parents=True)
    dest = dest_dir / "SKILL.md"
    two_para = (
        "---\n"
        "name: tortoise-decide\n"
        f"{FOREIGN_KEY}\n"
        "domain: capability\n"
        "updated: 2026-01-01\n"
        "---\n"
        "> ⛔ **This skill MUST be read in full — not skimmed.** para one, line one\n"
        "> para one, line two\n"
        "\n"
        "> ⛔ **Second foreign banner paragraph.** para two\n"
        "\n"
        "# tortoise-decide\n"
        "\n"
        f"{OLD_MARKER}\n"
        "---\n"
        f"{TRAILER}\n"
    )
    dest.write_text(two_para, encoding="utf-8")

    res = _run(installer, home, tmp_path)
    assert res.returncode == 0, res.stderr
    merged = dest.read_text(encoding="utf-8")
    assert (
        "> para one, line two\n"
        "\n"
        "> ⛔ **Second foreign banner paragraph.** para two" in merged
    ), "the blank line between the two banner paragraphs was lost"
    # 2 lines + the separating blank + 1 line are all preserved.
    assert "preserved-banner: 4 line(s)" in res.stdout


def test_real_agent_infra_convention_shape_is_preserved(tmp_path):
    """Regression shape: the EXACT two-line banner + trailing line measured on
    the machine survive, and the merged document keeps the convention's own
    layout (banner immediately after `---`; trailer after a `---` rule)."""
    installer, home, skills, _ = _fixture(
        tmp_path, {"tortoise-decide": NEW_MARKER})
    dest_dir = skills / "tortoise-decide"
    dest_dir.mkdir(parents=True)
    (dest_dir / "SKILL.md").write_text(
        _foreign_skill("tortoise-decide", OLD_MARKER), encoding="utf-8")

    assert _run(installer, home, tmp_path).returncode == 0
    lines = (dest_dir / "SKILL.md").read_text(encoding="utf-8").splitlines()

    # Frontmatter closes, then the foreign banner begins immediately.
    assert lines[0] == "---", "frontmatter must open the file"
    close = lines.index("---", 1)  # the closing marker, after the opener
    assert lines[close + 1].startswith("> ⛔ **This skill MUST be read in full")
    assert lines[close + 2].startswith("> Skipping steps silently bypasses")
    # Trailer is the last line, preceded by a `---` rule.
    assert lines[-1] == TRAILER
    assert lines[-2] == "---"
