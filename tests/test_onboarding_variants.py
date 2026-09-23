"""M8 archive integrity tests for the onboarding script (epic #1976, #1998 W2).

Guarantees the ONE-live-script contract (DE2E-5 M8): after W2, the single live
onboarding script is `tortoise/onboarding/SKILL.md` (the tortoise-onboarding
skill); the old `AGENT_ONBOARDING.md` prompt + its per-harness variant headers
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
import re
from pathlib import Path

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


def test_m8_deploy_mirror_matches_canonical():
    """The dashboard deploy mirror is byte-identical to the canonical
    SKILL.md (drift-proofing — the old stage_variants concat guarantee
    carried forward)."""
    assert MIRROR_SKILL.exists(), f"deploy mirror missing: {MIRROR_SKILL}"
    canonical = LIVE_SKILL.read_text(encoding="utf-8")
    assert MIRROR_SKILL.read_text(encoding="utf-8") == canonical, (
        "deploy mirror drifted from the canonical SKILL.md")


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
