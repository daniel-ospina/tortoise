"""M8 archive integrity tests for the onboarding script (epic #1976, #1998 W2).

Guarantees the ONE-live-script contract (DE2E-5 M8): after W2, the single live
onboarding artifact is `tortoise/onboarding/SKILL.md` (the tortoise-onboarding
INSTRUCTIONS document — delivered as instructions the agent reads, never an
installed skill since #4365; the skill-shaped filename is the served path);
the old `AGENT_ONBOARDING.md` prompt + its per-harness variant headers
are ARCHIVED under `tortoise/onboarding/archive/` and the deploy-time staging
pipeline (`stage_variants.py`, `website/onboarding-prompt.md`,
`website/onboarding/<h>.md`) is retired. A two-live-scripts regression must
fail this file.

Also carries the decide-contract scan (every `tortoise_*` token in the
SKILL.md decide-protocol section ⊆ registered MCP tool names — DE2E-5's
decide-protocol-availability pin) and the dashboard wizard-copy contract
tests (harnesses.js/main.jsx scans — issue #967 contract, updated #1730;
these outlived the variants they were born with).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
ONBOARDING_DIR = REPO_ROOT / "tortoise" / "onboarding"
LIVE_SKILL = ONBOARDING_DIR / "SKILL.md"
ARCHIVE_DIR = ONBOARDING_DIR / "archive"
ARCHIVED_PROMPT = ARCHIVE_DIR / "AGENT_ONBOARDING.md"
ARCHIVE_VARIANTS_DIR = ARCHIVE_DIR / "variants"

# Deploy mirror (dashboard public tree → app.premiselabs.co/skills/<name>/)
PUBLIC_SKILLS = (REPO_ROOT / "website" / "apps" / "dashboard" / "public"
                 / "skills")
MIRROR_SKILL = PUBLIC_SKILLS / "tortoise-onboarding" / "SKILL.md"

# The archived prompt's per-harness headers (variants from epic #529).
ARCHIVED_HARNESSES = ("claude-code", "codex", "cursor", "pi")


def _live_md_files() -> list[Path]:
    """Top-level *.md directly in tortoise/onboarding/ (archive/ excluded)."""
    return sorted(p for p in ONBOARDING_DIR.glob("*.md") if p.is_file())


def _registered_mcp_tool_names() -> set[str]:
    """`def tortoise_*` MCP tool handlers in tortoise/mcp_server.py."""
    src = (REPO_ROOT / "tortoise" / "mcp_server.py").read_text(encoding="utf-8")
    return set(re.findall(r"^def (tortoise_\w+)\(", src, re.M))


# ── M8: exactly ONE live onboarding script ────────────────────────────────

def test_live_skill_exists_at_the_defined_path():
    """DE2E-5: SKILL.md exists at the defined path and reads onboarding
    state (the state-vocabulary contract: canonical step ids + fork values
    from tortoise/onboarding/state.py appear in it)."""
    assert LIVE_SKILL.exists(), f"live skill missing: {LIVE_SKILL}"
    content = LIVE_SKILL.read_text(encoding="utf-8")
    assert content.startswith("---\n"), "SKILL.md must carry frontmatter"
    for step in ("harness-connected", "capture-disclosed", "catalog-presented"):
        assert step in content, f"SKILL.md must reference the canonical step {step}"
    for fork in ("'self'", "'build'"):
        assert fork in content, f"SKILL.md must reference fork {fork}"
    assert "tortoise_health" in content, "SKILL.md must verify via tortoise_health"
    assert "checkpoint" in content, "SKILL.md must write the harness-connected checkpoint"


def test_m8_one_live_script_top_level_md():
    """Exactly ONE live script: the only top-level *.md in
    tortoise/onboarding/ is SKILL.md (a two-live-scripts regression — any
    new AGENT_ONBOARDING.md, *-header.md, or other onboarding .md at the
    live path — must fail)."""
    assert _live_md_files() == [LIVE_SKILL], (
        f"top-level onboarding markdown must be exactly {{SKILL.md}}, got: "
        f"{_live_md_files()}")


def test_m8_old_prompt_archived_not_deleted():
    """AGENT_ONBOARDING.md + variant headers live ONLY under archive/ (A0
    rollback path — archived, never deleted, never re-promoted)."""
    assert ARCHIVED_PROMPT.exists(), "archived prompt missing"
    archived = ARCHIVED_PROMPT.read_text(encoding="utf-8")
    assert "ARCHIVED" in archived.splitlines()[0] or "ARCHIVED" in archived[:400], (
        "archived prompt must carry an ARCHIVED banner")
    for harness in ARCHIVED_HARNESSES:
        header = ARCHIVE_VARIANTS_DIR / f"{harness}-header.md"
        assert header.exists(), f"archived variant header missing: {header}"
    # no *-header.md outside archive/ (recursive sweep — a recreated
    # variants/ dir at the live path must fail)
    for p in ONBOARDING_DIR.rglob("*-header.md"):
        assert p.is_relative_to(ARCHIVE_DIR), (
            f"variant header outside archive/: {p}")
    assert not (ONBOARDING_DIR / "stage_variants.py").exists(), (
        "the old staging script must be retired (deployed copies archived)")


def assert_copies_parity(canonical: Path, mirror: Path) -> None:
    """Byte-identity between the two tracked copies, FAIL-CLOSED (#3673).

    #3673 requires that a copy which cannot be read is a FAILURE, never a skip:
    a gate that quietly skips when a copy is unreadable reports health it has
    not observed, and a deleted copy is precisely the state a parity gate exists
    to catch. Both sides are asserted to EXIST before either is read, so a
    missing file raises a named failure instead of a bare FileNotFoundError from
    the comparison — the difference between a diagnosis and a traceback.
    """
    assert canonical.exists(), f"canonical copy missing: {canonical}"
    assert mirror.exists(), f"deploy mirror missing: {mirror}"
    assert mirror.read_text(encoding="utf-8") == canonical.read_text(
        encoding="utf-8"), (
        f"parity broken: {mirror} drifted from {canonical}")


def test_m8_deploy_mirror_matches_canonical():
    """The dashboard deploy mirror is byte-identical to the canonical
    SKILL.md (drift-proofing — the old stage_variants concat guarantee
    carried forward).

    #3673: there are exactly TWO tracked copies (the third is gitignored build
    output), so this is a two-source parity gate and not a one-file no-op gate.
    Watched failing in BOTH directions — a canonical-only edit and a served-only
    edit each exit non-zero — then restored to green."""
    assert_copies_parity(LIVE_SKILL, MIRROR_SKILL)


def test_parity_gate_fails_closed_on_a_missing_or_unreadable_copy(tmp_path):
    """#3673 indicator (3): 0 fail-open paths.

    The only state that may pass is genuine byte-identity. Missing and
    unreadable copies must RAISE — if any of these returned cleanly the gate
    would be reporting a comparison it never performed.
    """
    canon = tmp_path / "canonical.md"
    mirror = tmp_path / "mirror.md"
    canon.write_text("same\n", encoding="utf-8")
    mirror.write_text("same\n", encoding="utf-8")
    assert_copies_parity(canon, mirror)          # identical — the only pass

    mirror.write_text("different\n", encoding="utf-8")
    with pytest.raises(AssertionError, match="drifted"):
        assert_copies_parity(canon, mirror)      # divergence

    with pytest.raises(AssertionError, match="canonical copy missing"):
        assert_copies_parity(tmp_path / "absent.md", mirror)
    with pytest.raises(AssertionError, match="deploy mirror missing"):
        assert_copies_parity(canon, tmp_path / "absent.md")

    # UNREADABLE — a directory where a document is expected.
    mirror.unlink()
    mirror.mkdir()
    with pytest.raises(OSError):
        assert_copies_parity(canon, mirror)


def test_4365_served_document_sends_chatgpt_to_a_path_that_exists():
    """#4365/#2698: the served document's §2 note must not send a ChatGPT user to
    the dashboard's "ChatGPT tab" — #2698 removed it from the chooser
    (HARNESS_FAMILIES has no chatgpt entry). Reverting the note to the retired
    tab tripped no test at all (mutation-verified, #4365 review round 4).

    Canonical only: the mirror is pinned byte-identical by
    `test_m8_deploy_mirror_matches_canonical`, so this covers both copies."""
    skill = LIVE_SKILL.read_text(encoding="utf-8")
    assert "chatgpt.com/plugins" in skill, (
        "the served document must name the ChatGPT Developer-mode path")
    assert "ChatGPT tab" not in skill, (
        "the served document must not name the retired dashboard ChatGPT tab")
    assert "no ChatGPT surface in the dashboard chooser" in skill, (
        "the served document must state that there is no ChatGPT chooser surface")


def _installer_skills() -> list[str]:
    """The served installer's `SKILLS=(...)` payload set."""
    installer = (REPO_ROOT / "website" / "apps" / "dashboard" / "public"
                 / "install-tortoise-skills.sh").read_text(encoding="utf-8")
    m = re.search(r"^SKILLS=\(([^)]*)\)", installer, re.M)
    assert m, "installer SKILLS=(...) array not found"
    return m.group(1).split()


def test_m8_installer_ships_the_three_capabilities_not_onboarding():
    """#4365: onboarding is DELIVERED AS INSTRUCTIONS, not installed as a
    skill — the installer ships the three reusable capabilities only. The
    #1998 W2 shape (a 4th basename) must fail here."""
    assert _installer_skills() == [
        "how-to-use-tortoise", "tortoise-decide", "tortoise-file-finding"
    ], ("the installer must ship the 3 capabilities; onboarding is not a skill")


def test_m8_installer_still_delivers_the_onboarding_instructions():
    """The CODEX reach invariant the removal must not break: the emitter that
    writes AGENTS.md into a codex project must still point the agent at the
    served onboarding instructions. (The filesystem-less harnesses never run
    this installer — their reach is asserted via wizardWorkflowsText in
    `test_4365_served_connect_copy_names_three_skills_plus_the_instructions`.)"""
    installer = (REPO_ROOT / "website" / "apps" / "dashboard" / "public"
                 / "install-tortoise-skills.sh").read_text(encoding="utf-8")
    # Assert on the block the installer EMITS, not on the whole file: the same
    # literal also sits in a top-of-file `#` comment that is never written to
    # any AGENTS.md, so a whole-file membership check stayed GREEN with the
    # reach bullet deleted (mutation-verified RED/GREEN, #4365 review).
    fn_start = installer.index("emit_codex_agents_block() {")
    fn_end = installer.index("\n}\n", fn_start)
    emitted = installer[fn_start:fn_end]
    assert "tortoise-onboarding/SKILL.md" in emitted, (
        "the installer's emitted AGENTS.md block must point at the served "
        "instructions")
    assert "Onboarding is delivered as INSTRUCTIONS" in emitted, (
        "the emitted block must state that onboarding is instructions")
    # …and the installer names them in its SUCCESS output for every harness,
    # not only in the codex-only AGENTS.md block. Anchor on the UNIQUE line:
    # slicing from "Tortoise skills installed to" to end-of-file also contains
    # the stale-copy warning, which carries the same path — a membership check
    # over that slice stayed GREEN with the success line deleted
    # (mutation-verified, #4365 review round 4).
    assert re.search(
        r'echo "Onboarding is NOT a skill[^\n]*\n\s*echo "   '
        r'\$SKILLS_BASE/tortoise-onboarding/SKILL\.md"', installer), (
        "the installer's success output must name the onboarding instructions")
    # name-grep contract: the installer validates each downloaded SKILL.md's
    # frontmatter name (not a literal skill name baked into the script).
    assert 'grep -q "^name: $s$"' in installer, (
        "installer must validate the downloaded SKILL.md frontmatter name")
    # The VERSION must be pinned, not just present: reverting v3 → v2 restored
    # the four-skill era and left every test green (mutation-verified, round 3).
    assert 'SKILLS_VERSION="v3"' in installer, (
        "the installer must advertise the v3 (three-capability) skill set")
    # …and a superseded copy left by a v2 install must be NAMED, not silently
    # ignored — otherwise the user keeps two live onboarding artifacts.
    assert "superseded copy is still at" in installer, (
        "the installer must warn about a stale tortoise-onboarding copy")


# The served connect surfaces that tell a user/agent what the installer ships.
DASHBOARD_SRC = REPO_ROOT / "website" / "apps" / "dashboard" / "src"

SNAPSHOT_PATH = DASHBOARD_SRC / "wizardPrompts.snapshot.json"

def _rendered_snapshot() -> dict:
    """The committed RENDERED agent-facing copy (#4880 / #4365).

    Generated from wizardPrompts.js by
    `node scripts/gen-wizard-prompts-snapshot.mjs` and pinned by the dashboard
    suite. Reading it HERE is what makes the cross-language contract checkable
    without parsing JavaScript source text — the mechanism that produced five
    distinct false greens (#4365 review, cycle 11): a guard that decides from the
    SHAPE of the source is defeated by any source of a different shape.
    """
    assert SNAPSHOT_PATH.exists(), (
        f"{SNAPSHOT_PATH} is missing — regenerate it with "
        f"`cd website/apps/dashboard && node scripts/gen-wizard-prompts-snapshot.mjs`")
    return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))


def test_4365_served_connect_copy_names_three_skills_plus_the_instructions():
    """#4365: what the dashboard hands the agent must be what the shell
    installer ships — the live defect was harnesses.js claiming FOUR skills
    while the installer installed three — and every live surface must name the
    onboarding instruction document, so the flow is reachable with NO skill
    installed.

    Everything asserted here reads a RENDERED value (the snapshot) or the shell
    installer itself. The former source-text guards were DELETED, not fixed:
    review rounds 4-10 defeated six of them (a name on its own source line, an
    escaped backtick, a blank line before the template, a same-line
    concatenation, a nested template, an ASCII-hyphen clause break) and the last
    three also FALSE-REDded formatting-only edits. The rendered invariants live
    in website/apps/dashboard/src/wizardPrompts.test.js, mutation-tested.
    """
    harnesses = (DASHBOARD_SRC / "harnesses.js").read_text(encoding="utf-8")

    m = re.search(
        r"^export const ONBOARDING_INSTRUCTIONS_URL =\s*\n?\s*'([^']+)'",
        harnesses, re.M)
    assert m, "ONBOARDING_INSTRUCTIONS_URL must be an exported constant"
    url = m.group(1)
    assert url == (
        "https://app.premiselabs.co/skills/tortoise-onboarding/SKILL.md"
    ), "the instruction URL must be the served instruction document"

    # Job 1 — the CROSS-FILE contract Python can check and node cannot: the set
    # the dashboard CLAIMS is the set the SHELL installer ships.
    m = re.search(r"^export const SKILLS_LIST =\s*\n?\s*'([^']+)'", harnesses, re.M)
    assert m, "SKILLS_LIST must be an exported constant"
    assert m.group(1).split(", ") == _installer_skills(), (
        "SKILLS_LIST must list exactly what the installer ships")
    assert "onboarding" not in m.group(1), (
        "SKILLS_LIST must not include the onboarding skill")

    # Job 2 — the RENDERED copy, from the snapshot both suites share.
    snap = _rendered_snapshot()
    assert snap["skillSet"] == m.group(1), (
        "the committed rendered snapshot is stale — regenerate it with "
        "`cd website/apps/dashboard && node scripts/gen-wizard-prompts-snapshot.mjs`")
    sentence = snap["onboardingInstructions"]
    assert sentence.startswith("Onboarding is instructions, not a skill"), (
        "the shared onboarding sentence must say onboarding is INSTRUCTIONS, "
        "not a skill")
    assert url in sentence, (
        "the shared onboarding sentence must name the served instructions "
        "document — that is what makes onboarding reachable with no skill "
        "installed")

    surfaces = dict(snap["prompts"])
    surfaces["workflows"] = snap["workflows"]
    surfaces["onboardingInstructions"] = sentence
    surfaces.update({f"UNIVERSAL_COMMAND.{k}": v for k, v in snap["commands"].items()})
    # #4880: the live JSX captions — extracted into wizardPrompts.js as DATA so
    # they are rendered values like everything else.
    surfaces.update({f"caption.{k}": v for k, v in snap["captions"].items()})

    # Every rendered set statement enumerates EXACTLY what the installer ships.
    # This is the live #4365 defect — a served copy claiming a 4th skill.
    checked = 0
    skill_names = _installer_skills()
    for label, text in surfaces.items():
        for inner in re.findall(r"install[^\n]{0,60}?skills?\s*\(([^)]*)\)", text, re.I):
            # The phrase is GENERALIZED, not the literal "install the Tortoise
            # skills": a reworded claim ("Also install the Tortoise helper skills
            # (agent-memory) from …") reintroduced the defect class while never
            # matching the literal. A parenthetical that ENUMERATES capabilities
            # — a comma list, a shipped name, or a skill-id-shaped token — must
            # be exactly the shipped set; prose hints are allowed by shape, so
            # rewording one is not a false red.
            enumerates = (
                "," in inner
                or any(n in inner for n in skill_names)
                or re.search(r"(?:^|[\s,])([a-z][a-z0-9]*(?:-[a-z0-9]+)+)(?=$|[\s,)])", inner)
            )
            if not enumerates:
                continue
            assert inner.split(", ") == skill_names, (
                f"{label}: the rendered copy enumerates {inner!r}, which is not "
                f"the set the installer ships ({skill_names})")
            checked += 1
    assert checked >= 4, (
        f"expected the four config-writing prompts to state the set (found "
        f"{checked} statements) — a prompt that lost its enumeration must fail")
    # …and only the sanctioned text may follow a rendered set statement. The tail
    # is ANCHORED, not probed one character at a time: a bare `\s` probe cannot
    # tell the legitimate ` from <installer URL>.` from a hostile
    # ` plus agent-memory:` — the space-separated form of the #4365 defect class
    # slipped through an earlier revision of this check.
    m = re.search(
        r"^export const SKILLS_INSTALL_URL =\s*\n?\s*'([^']+)'", harnesses, re.M)
    assert m, "SKILLS_INSTALL_URL must be an exported constant"
    tail_forms = (":\n", f" from {m.group(1)}.")
    for label, text in surfaces.items():
        for tail in re.findall(
                r"install the Tortoise skills\s*\([^)]*\)([\s\S]{0,60})", text, re.I):
            assert tail.startswith(tail_forms), (
                f"{label}: only {tail_forms} may follow the shipped set statement — "
                f"got {tail[:40]!r}")

    # The reach invariant: the four config-writing prompts carry the
    # instructions inline; the two connector leaves get them in the workflows
    # prompt, their ONLY delivery surface.
    for label in ("pi/1", "cursor/1", "claude/1", "codex/1"):
        assert sentence in snap["prompts"][label], (
            f"prompt {label} must carry the onboarding instructions")
    assert sentence in snap["workflows"], (
        "the teach-human workflows prompt is the connector leaves' ONLY "
        "delivery surface and must carry the onboarding instructions")
    # The step-2 skills primer inside the LEGACY_WIZARD_ARCHIVED block
    # (main.jsx:7845-8122, flag=false) is deliberately NOT pinned: it is dead
    # code kept byte-identical for the A0 rollback path, and editing it breaks
    # the archived-block line-count pin in overview.test.js.


# ── installer ergonomics (#3): Pi verification + version stamp ─────────────

INSTALLER_PUBLIC = (REPO_ROOT / "website" / "apps" / "dashboard" / "public"
                    / "install-tortoise-skills.sh")
INSTALLER_DIST = (REPO_ROOT / "website" / "apps" / "dashboard" / "dist"
                  / "install-tortoise-skills.sh")


def _installer_text() -> str:
    return INSTALLER_PUBLIC.read_text(encoding="utf-8")


def _declared_version() -> str:
    m = re.search(r'^SKILLS_VERSION="([^"]+)"', _installer_text(), re.M)
    assert m, "installer must declare SKILLS_VERSION"
    return m.group(1)


def _stub_curl_dir(tmp_path: Path) -> Path:
    """A `curl` stub that writes a valid SKILL.md for any requested skill,
    so the installer can be exercised with no network. Set
    STUB_CURL_FAIL_SKILL=<name> to make that one download fail."""
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    stub = bindir / "curl"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        "out=''; url=''\n"
        "while [ $# -gt 0 ]; do\n"
        "  case \"$1\" in\n"
        "    -o) out=\"$2\"; shift 2 ;;\n"
        "    -*) shift ;;\n"
        "    *) url=\"$1\"; shift ;;\n"
        "  esac\n"
        "done\n"
        "skill=\"${url##*/skills/}\"; skill=\"${skill%%/*}\"\n"
        "[ \"${STUB_CURL_FAIL_SKILL:-}\" = \"$skill\" ] && exit 22\n"
        "printf -- '---\\nname: %s\\ndescription: stub\\n---\\nbody\\n' \"$skill\" > \"$out\"\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    return bindir


def _run_installer(tmp_path: Path, home: Path, harness: str = "pi",
                   fail_skill: str | None = None):
    bindir = _stub_curl_dir(tmp_path)
    env = dict(os.environ, HOME=str(home),
               PATH=f"{bindir}{os.pathsep}{os.environ['PATH']}")
    if fail_skill:
        env["STUB_CURL_FAIL_SKILL"] = fail_skill
    return subprocess.run(
        ["bash", str(INSTALLER_PUBLIC), "--harness", harness],
        cwd=tmp_path, env=env, text=True, capture_output=True,
    )


def test_installer_dist_is_a_build_artifact_not_a_committed_mirror():
    """#3775 untracked `website/apps/dashboard/dist/` — it is a vite build output, not a
    committed mirror, so asserting a COMMITTED dist copy exists cannot hold on a fresh
    checkout, and re-committing one is the artifact #3775 deliberately removed.

    What still matters is the source-of-truth relation: the served installer is the
    `public/` one, so if a `dist/` tree happens to be built in this checkout, its copy
    must not disagree with the source (a stale built mirror muddies which bytes are live).
    """
    tracked = subprocess.run(
        ["git", "ls-files", "--error-unmatch",
         "website/apps/dashboard/dist/install-tortoise-skills.sh"],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True,
    ).returncode == 0
    assert not tracked, (
        "dist/install-tortoise-skills.sh is tracked again — #3775 untracked dist/ as a "
        "build artifact; a committed stale copy muddies which bytes are served")
    if INSTALLER_DIST.exists():
        assert INSTALLER_DIST.read_text(encoding="utf-8") == _installer_text(), (
            "a BUILT dist copy disagrees with public/ — rerun the dashboard build")


def test_installer_pi_row_verifies_the_mcp_connection():
    """#3 gap 1: the Pi row must not stop at 'scanned on startup' — it must
    tell the user how to verify the MCP CONNECTION (tortoise_health) and give
    the cold-host retry path (mcp_load)."""
    m = re.search(r"\n    pi\)\n(.*?)\n\s*;;", _installer_text(), re.S)
    assert m, "installer pi case arm not found"
    pi_arm = m.group(1)
    assert "tortoise_health" in pi_arm, "Pi row must name the verify tool"
    assert "mcp_load tortoise" in pi_arm, "Pi row must give the cold-host retry"


def test_installer_records_a_sidecar_version_stamp(tmp_path):
    """#3 gap 2: the installed version is recorded in a SIDECAR, so the
    installed SKILL.md bodies stay byte-identical to the served originals
    (the `^name:` payload check and the canonical<->mirror parity test both
    read those bodies)."""
    home = tmp_path / "home"
    home.mkdir()
    proc = _run_installer(tmp_path, home)
    assert proc.returncode == 0, proc.stderr
    skills_dir = home / ".pi" / "agent" / "skills"
    stamp = skills_dir / ".tortoise-skills-version"
    assert stamp.exists(), "installer must write a version stamp"
    text = stamp.read_text(encoding="utf-8")
    assert f"skills_version={_declared_version()}" in text
    assert "harness=pi" in text
    assert "source=" in text
    # content identity per skill, so a local edit is catchable across versions
    # (the set is READ from the installer, so this cannot re-freeze a stale era;
    # #4365 dropped tortoise-onboarding from the installed set)
    for s in _installer_skills():
        assert f"sha256.{s}=" in text, f"stamp must record a digest for {s}"
    # no timestamp: the stamp also lands in version-controlled project dirs
    assert "installed_at" not in text
    # sidecar choice: the installed bodies are the served bytes, unmutated
    for s in _installer_skills():
        body = (skills_dir / s / "SKILL.md").read_text(encoding="utf-8")
        assert body == f"---\nname: {s}\ndescription: stub\n---\nbody\n"
        assert "tortoise-skills-version" not in body


def test_installer_pi_success_output_prints_verification_and_stamp(tmp_path):
    """The success output names the verify call and points at the stamp, so
    the installed version is visible without diffing the product site."""
    home = tmp_path / "home"
    home.mkdir()
    proc = _run_installer(tmp_path, home)
    assert proc.returncode == 0, proc.stderr
    assert "tortoise_health" in proc.stdout
    assert "mcp_load tortoise" in proc.stdout
    assert ".tortoise-skills-version" in proc.stdout


def test_installer_warns_when_replacing_a_locally_edited_copy(tmp_path):
    """Drift detection: a locally edited copy is reported before it is
    overwritten, even at an unchanged version (content, not version, is the
    signal)."""
    home = tmp_path / "home"
    home.mkdir()
    assert _run_installer(tmp_path, home).returncode == 0
    victim = (home / ".pi" / "agent" / "skills" / "tortoise-decide"
              / "SKILL.md")
    victim.write_text("---\nname: tortoise-decide\n---\nlocally edited\n",
                      encoding="utf-8")
    proc = _run_installer(tmp_path, home)
    assert proc.returncode == 0, proc.stderr
    assert "tortoise-decide" in proc.stderr
    assert "edited locally" in proc.stderr


def test_installer_warns_when_there_is_no_stamp(tmp_path):
    """The issue's actual scenario: a stale copy installed by an OLDER
    installer (no stamp at all) must be flagged before being replaced."""
    home = tmp_path / "home"
    home.mkdir()
    assert _run_installer(tmp_path, home).returncode == 0
    skills_dir = home / ".pi" / "agent" / "skills"
    (skills_dir / ".tortoise-skills-version").unlink()
    victim = skills_dir / "tortoise-decide" / "SKILL.md"
    victim.write_text("---\nname: tortoise-decide\n---\nold copy\n",
                      encoding="utf-8")
    proc = _run_installer(tmp_path, home)
    assert proc.returncode == 0, proc.stderr
    assert "older/manual install" in proc.stderr


def test_failed_install_does_not_write_a_version_stamp(tmp_path):
    """A failed download must not leave a stamp claiming a version that was
    not fully installed."""
    home = tmp_path / "home"
    home.mkdir()
    # The failing skill must be one the installer actually SHIPS: since #4365
    # onboarding is delivered as instructions, so failing a skill it no longer
    # downloads is a no-op and the installer correctly exits 0.
    proc = _run_installer(tmp_path, home, fail_skill=_installer_skills()[0])
    assert proc.returncode != 0
    assert not (home / ".pi" / "agent" / "skills"
                / ".tortoise-skills-version").exists()


def test_stamp_write_replaces_a_symlink_instead_of_writing_through_it(tmp_path):
    """The installer runs inside an untrusted project clone for the project
    harnesses; a planted `.tortoise-skills-version -> ../../README.md`
    symlink must not turn the install into an arbitrary-file clobber."""
    project = tmp_path
    skills_dir = project / ".claude" / "skills"
    skills_dir.mkdir(parents=True)
    victim = project / "README.md"
    victim.write_text("important project readme\n", encoding="utf-8")
    stamp = skills_dir / ".tortoise-skills-version"
    stamp.symlink_to(victim)
    proc = _run_installer(tmp_path, tmp_path / "home", harness="claude")
    assert proc.returncode == 0, proc.stderr
    assert victim.read_text(encoding="utf-8") == "important project readme\n"
    assert not stamp.is_symlink(), "the stamp write followed the symlink"
    assert "skills_version=" in stamp.read_text(encoding="utf-8")


def test_stamp_write_is_not_clobbered_via_a_planted_temp_symlink(tmp_path):
    """The temp file used for the atomic stamp write must not be predictable:
    a pre-planted `$STAMP.tmp -> ../../README.md` symlink must not become an
    arbitrary-file clobber (mktemp gives an unpredictable name)."""
    skills_dir = tmp_path / ".claude" / "skills"
    skills_dir.mkdir(parents=True)
    victim = tmp_path / "README.md"
    victim.write_text("important project readme\n", encoding="utf-8")
    (skills_dir / ".tortoise-skills-version.tmp").symlink_to(victim)
    proc = _run_installer(tmp_path, tmp_path / "home", harness="claude")
    assert proc.returncode == 0, proc.stderr
    assert victim.read_text(encoding="utf-8") == "important project readme\n"
    assert "skills_version=" in (skills_dir
                                 / ".tortoise-skills-version").read_text()


def test_download_temp_is_not_clobbered_via_a_planted_symlink(tmp_path):
    """The per-skill download temp is mktemp-named too: a planted
    `<skill>/SKILL.md.tmp -> ../../../README.md` must not make curl clobber an
    arbitrary file in an untrusted project clone."""
    skill_dir = tmp_path / ".claude" / "skills" / "tortoise-decide"
    skill_dir.mkdir(parents=True)
    victim = tmp_path / "README.md"
    victim.write_text("important project readme\n", encoding="utf-8")
    (skill_dir / "SKILL.md.tmp").symlink_to(victim)
    proc = _run_installer(tmp_path, tmp_path / "home", harness="claude")
    assert proc.returncode == 0, proc.stderr
    assert victim.read_text(encoding="utf-8") == "important project readme\n"
    installed = skill_dir / "SKILL.md"
    assert not installed.is_symlink()
    assert "name: tortoise-decide" in installed.read_text(encoding="utf-8")
    # mktemp's 0600 must not leak onto the installed payload
    assert (installed.stat().st_mode & 0o777) == 0o644
    assert not list(skill_dir.glob("SKILL.md.??????")), "temp file left behind"


def test_stamp_write_is_reported_when_it_cannot_be_written(tmp_path):
    """A stamp path that cannot hold a file (a directory) must be reported as
    a failure, not silently announced as written."""
    home = tmp_path / "home"
    home.mkdir()
    skills_dir = home / ".pi" / "agent" / "skills"
    skills_dir.mkdir(parents=True)
    (skills_dir / ".tortoise-skills-version").mkdir()
    proc = _run_installer(tmp_path, home)
    assert proc.returncode == 0, proc.stderr
    assert "could not write the version stamp" in proc.stderr
    assert "version stamp:" not in proc.stdout


def test_m8_no_live_reference_to_old_paths_outside_archive():
    """Sweep: live code paths never point at the retired prompt/staging
    pipeline (docs/epics + historical research docs are exempt; prose
    comments in tests that merely describe the archive are exempt)."""
    scanned = [
        REPO_ROOT / "tortoise" / "__main__.py",
        REPO_ROOT / "tools" / "ci_selection.py",
        REPO_ROOT / ".github" / "workflows" / "deploy-pages.yml",
        REPO_ROOT / "website" / "self-hosted.html",
        REPO_ROOT / "website" / "functions" / "_middleware.ts",
    ]
    old_literals = ("onboarding-prompt.md", "AGENT_ONBOARDING.md",
                    "stage_variants")
    for path in scanned:
        text = path.read_text(encoding="utf-8")
        for lit in old_literals:
            assert lit not in text, (
                f"live reference to retired onboarding artifact "
                f"{lit!r} in {path}")


# ── decide-protocol contract (DE2E-5, I-4) ────────────────────────────────

def test_skill_decide_protocol_tools_are_registered_mcp_tools():
    """Every tortoise_* token in the SKILL.md's decide-protocol section is a
    registered MCP tool — the generic protocol must run on ALL 6 harnesses
    with no local skill file (a typo'd tool name would strand the protocol)."""
    skill = LIVE_SKILL.read_text(encoding="utf-8")
    decide_section = skill.split("### The generic MCP-tool decide protocol")[1]
    tokens = set(re.findall(r"tortoise_\w+", decide_section))
    assert tokens, "no tortoise_* tokens found in the decide-protocol section"
    registered = _registered_mcp_tool_names()
    missing = tokens - registered
    assert not missing, (
        f"decide-protocol tools not registered in mcp_server.py: {missing}")
    # the ranking read-out requires the EP computation + structure check
    for required in ("tortoise_compute_confidence", "tortoise_check_structure",
                     "tortoise_create_operator", "tortoise_health"):
        assert required in tokens, f"decide protocol must use {required}"


def test_skill_capture_announcement_copy_contract_present():
    """W2 owns the capture-announcement COPY CONTRACT — the literal one-line
    copy must be in the SKILL.md (W6 consumes it; a wording drift must fail)."""
    skill = LIVE_SKILL.read_text(encoding="utf-8")
    assert "I'll remember this session so you can recall it later" in skill
    assert "View/delete in Settings" in skill


# ── wizard copy ref tests (issue #967 contract, updated #1730) ─────────────
# The canonical harness-copy surface is the dashboard wizard (harnesses.js) +
# the CLI (`_harness_mcp_config`). These tests pin the same contracts
# (optimal shapes, env-indirection, no literal key) against the copy surface.
# They scan raw source text — they survive the universal-command payload
# swap (#1998) because the legacy HARNESS_* exports are preserved (A0
# rollback path).

WELCOME = REPO_ROOT / "website" / "apps" / "dashboard" / "src" / "harnesses.js"


def _welcome() -> str:
    return WELCOME.read_text(encoding="utf-8")


def _extract_js_block(html: str, const_name: str) -> str:
    """Brace-balanced JS object literal for a harnesses.js const (raw text)."""
    idx = html.index(f"{const_name} =")
    open_brace = html.index("{", idx)
    depth = 0
    for j in range(open_brace, len(html)):
        if html[j] == "{":
            depth += 1
        elif html[j] == "}":
            depth -= 1
            if depth == 0:
                break
    return html[open_brace : j + 1]


def test_wizard_harness_configs_are_optimal():
    """T3: semantic fragments per harness in the wizard copy (harnesses.js)."""
    html = _welcome()
    # Claude: CLI one-liner shape in the copy
    assert "claude mcp add" in html
    assert "--transport http" in html
    assert "https://api.premiselabs.co/mcp/" in html
    # Codex: CLI + env export
    for frag in ("codex mcp add tortoise --url",
                 "--bearer-token-env-var TORTOISE_API_KEY",
                 "export TORTOISE_API_KEY="):
        assert frag in html, f"codex copy missing fragment: {frag}"
    # File paths named in the copy/steps
    for frag in (".cursor/mcp.json", ".mcp.json"):
        assert frag in html, f"missing file path: {frag}"
    # Cursor canonical JSON: shape + env expansion (trailing slash load-bearing)
    cursor_cfg = _extract_js_block(html, "CURSOR_MCP_CONFIG_ENV")
    assert "MCP_URL" in cursor_cfg  # url references the shared const
    assert "${env:TORTOISE_API_KEY}" in cursor_cfg
    # Pi canonical JSON: env-indirection (no literal key); the EXACT pi token
    # (plain ${TORTOISE_API_KEY} — pi's mcp-client has no env: prefix support)
    # is pinned by the #1729 harness-copy PR.
    pi_cfg = _extract_js_block(html, "PI_MCP_CONFIG_ENV")
    assert "MCP_URL" in pi_cfg
    assert "TORTOISE_API_KEY" in pi_cfg
    assert "tt_" not in pi_cfg
    # The shared const pins the trailing-slash endpoint (load-bearing, #529)
    assert 'const MCP_URL = \'https://api.premiselabs.co/mcp/\'' in html


def test_wizard_env_form_blocks_carry_no_literal_key():
    """T3/T10 negative: the canonical env blocks contain no tt_ key."""
    html = _welcome()
    for const in ("CURSOR_MCP_CONFIG_ENV", "PI_MCP_CONFIG_ENV"):
        block = _extract_js_block(html, const)
        assert "tt_" not in block, f"{const} canonical env block must not contain a literal key"


def test_wizard_copy_beacon_persists_harness_section():
    """T4: the wizard copy action PATCHes /v1/onboarding/state with
    {harness, section} (the copy beacon moved from welcome.html to the
    dashboard wizard, #1566/#1730)."""
    dashboard = REPO_ROOT / "website" / "apps" / "dashboard" / "src" / "main.jsx"
    src = dashboard.read_text(encoding="utf-8")
    assert "onboarding/state" in src
    assert "harness: wizardHarness" in src
    assert "section: 'config'" in src


def test_key_never_interpolated_into_env_blocks():
    """T7b/J5: the canonical env blocks use env expansion only — the API key
    never appears as a literal in the wizard copy (HARNESS_INSTALL receives
    the key as a function argument; the env configs never carry it)."""
    html = _welcome()
    assert "TORTOISE_API_KEY" in html
    for const in ("CURSOR_MCP_CONFIG_ENV", "PI_MCP_CONFIG_ENV"):
        block = _extract_js_block(html, const)
        assert "tt_" not in block, f"{const} must stay key-free"
        assert "TORTOISE_API_KEY" in block, f"env token missing from {const}"
        # cursor pins env: expansion; the EXACT pi token is owned by #1729
        # (plain form — pi's mcp-client has no env: prefix support).
        assert "${env:TORTOISE_API_KEY}" in _extract_js_block(html, "CURSOR_MCP_CONFIG_ENV")
