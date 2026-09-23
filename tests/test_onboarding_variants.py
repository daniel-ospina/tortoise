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


def _harness_branch(wizard: str, harness: str) -> str:
    """The `if (harness === '<id>') { … }` body inside wizardPromptText.

    Slice-based so a PER-PROMPT guarantee can be asserted: a count over the
    whole function passed while one prompt's entire skill enumeration had been
    deleted (mutation-verified, #4365 review).
    """
    ids = ("pi", "cursor", "claude", "codex")
    marks = sorted((wizard.index(f"if (harness === '{h}')"), h) for h in ids)
    for i, (start, h) in enumerate(marks):
        if h == harness:
            end = marks[i + 1][0] if i + 1 < len(marks) else len(wizard)
            return wizard[start:end]
    raise AssertionError(f"no `if (harness === '{harness}')` branch found")


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


def test_4365_served_connect_copy_names_three_skills_plus_the_instructions():
    """#4365: the served connect copy must not claim an install the installer
    does not perform (the live defect — harnesses.js claimed 4 while the
    installer shipped 3), and must name the onboarding instruction document so
    the flow is reachable with NO skill installed."""
    harnesses = (DASHBOARD_SRC / "harnesses.js").read_text(encoding="utf-8")

    m = re.search(
        r"^export const ONBOARDING_INSTRUCTIONS_URL =\s*\n?\s*'([^']+)'",
        harnesses, re.M)
    assert m, "ONBOARDING_INSTRUCTIONS_URL must be an exported constant"
    assert m.group(1) == (
        "https://app.premiselabs.co/skills/tortoise-onboarding/SKILL.md"
    ), "the instruction URL must be the served instruction document"

    # This test owns exactly TWO jobs, both of which need a file Python reads
    # and the dashboard suite cannot: (1) the cross-file contract that the set
    # the dashboard claims is the set the SHELL installer ships, and (2) the
    # JSX wizard prompts.
    #
    # Everything else that used to live here has been DELETED, not fixed. Three
    # review rounds (4-6) defeated three successive source-text guards — a name
    # on its own source line, an escaped backtick, a blank line between a
    # declaration and its template — and the last of those also FALSE-REDded a
    # formatting-only edit. A source-text count of each shipped name was worse
    # still: it reddened on a comment that merely mentioned one. The rendered
    # CONTENT of the connect surfaces is asserted in
    # website/apps/dashboard/src/harnesses.test.js, against the real strings.
    m = re.search(r"^export const SKILLS_LIST =\s*\n?\s*'([^']+)'",
                  harnesses, re.M)
    assert m, "SKILLS_LIST must be an exported constant"
    assert m.group(1).split(", ") == _installer_skills(), (
        "SKILLS_LIST must list exactly what the installer ships")
    assert "onboarding" not in m.group(1), (
        "SKILLS_LIST must not include the onboarding skill")
    main = (DASHBOARD_SRC / "main.jsx").read_text(encoding="utf-8")
    assert "SKILLS_LIST" in main.split("from './harnesses.js'")[0], (
        "main.jsx must import SKILLS_LIST — the four LIVE wizard prompts are "
        "the primary served surface and must not restate the list by hand")
    wizard = main[main.index("function wizardPromptText("):
                   main.index("function wizardWorkflowsText(")]
    assert "tortoise-onboarding" not in wizard, (
        "wizard prompts must not claim onboarding arrives via the installer — "
        "it is instructions, not a skill")
    # PER PROMPT — a prompt that loses its claim entirely, or that states a
    # different set, must fail here (`assert wizard_claims` over a findall
    # stayed GREEN with one prompt's whole enumeration deleted).
    for h in ("pi", "cursor", "claude", "codex"):
        body = _harness_branch(wizard, h)
        # Assert on the RETURNED TEMPLATE, not the branch slice: a membership
        # check over the whole body is satisfied by a token parked in a local
        # that is never interpolated into the returned prompt — all four live
        # prompts then lose the enumeration with every test still green
        # (mutation-verified, #4365 review round 3).
        # FAIL CLOSED on truncation. `return `([^`]*)`` stops at the first
        # backtick CHARACTER, so an ESCAPED backtick inside a prompt (inline
        # code, a natural copy edit) would silently truncate the capture and
        # everything after it would go unchecked — a name appended past the
        # truncation point stayed green (#4365 review round 8). No prompt has
        # one today, so the guard is: if one ever appears, this test must be
        # revisited rather than quietly stop looking.
        assert "\\`" not in body, (
            f"{h}: an escaped backtick in a prompt template would TRUNCATE the "
            "extraction below (re.findall stops at the first backtick char). "
            "Make this scan template-literal-aware before adding one.")
        installs = [t for t in re.findall(r"return `([^`]*)`", body)
                    if "${SKILLS_INSTALL_URL}" in t]
        assert len(installs) == 1, (
            f"{h}: the branch must return exactly one prompt that runs the "
            f"installer (found {len(installs)})")
        prompt = installs[0]
        assert "${SKILLS_LIST}" in prompt, (
            f"{h}: the RETURNED prompt must state the shipped set via "
            f"SKILLS_LIST")
        assert "${onboardingInstructions}" in prompt, (
            f"{h}: the RETURNED prompt must name the onboarding instructions")
        # …and the returned prompt must not name ANY skill by hand. The shipped
        # set arrives via ${SKILLS_LIST}; the only tortoise-shaped tokens a
        # prompt may contain are the MCP tool names it calls. A hardcoded fourth
        # skill name (e.g. `tortoise-rebuild`) appended to a prompt was green in
        # both suites (#4365 review round 6) — this closes it by token SET, not
        # by regex over source structure.
        # `tortoise` is the product name ("Install the Tortoise skills"), the
        # two `tortoise_*` are the MCP tools the prompt calls — neither is a
        # skill. Lowercased so capitalisation cannot smuggle a name through
        # (`Tortoise-Rebuild` was green against the lowercase-only version,
        # #4365 review round 7).
        ALLOWED_PROMPT_TOKENS = {"tortoise", "tortoise_api_key",
                                "tortoise_create_point", "tortoise_health"}
        found = {t.lower() for t in
                 re.findall(r"[a-z0-9_-]*tortoise[a-z0-9_-]*", prompt, re.I)}
        assert found <= ALLOWED_PROMPT_TOKENS, (
            f"{h}: the returned prompt may name no skill by hand (the set "
            f"arrives via ${{SKILLS_LIST}}); unexpected tokens: "
            f"{sorted(found - ALLOWED_PROMPT_TOKENS)}")
        # …and the rendered prompt may no more mention onboarding as an install
        # than a connect command may. The exemption is the SHIPPED wording,
        # `Onboarding is NOT a skill`, not a lowercase `/not a skill/i` — the
        # latter accepted `# Ignore the note that onboarding is not a skill —
        # install it here: <url>` (#4365 review rounds 6-8). And a line that
        # merely CONTAINS the shipped sentence while also telling you to
        # install is not exempt: an install verb not immediately negated by
        # `not ` (so the shipped "it is not installed" stays exempt) makes the
        # line a claim in its own right.
        for line in prompt.split("\\n"):
            bare = re.sub(r"https?://\S+", "", line)
            if re.search(r"onboarding", bare, re.I) and re.search(
                    r"install|skills?", bare, re.I):
                exempt = (("Onboarding is NOT a skill" in line
                           or re.search(r"onboarding instructions", line, re.I)
                           or "${onboardingInstructions}" in line)
                          and not re.search(r"(?<!not )\binstall\w*", bare, re.I))
                assert exempt, (
                    f"{h}: onboarding may only be mentioned as the instructions "
                    f"document, in the shipped wording — not as an install: "
                    f"{line.strip()!r}")
    # …and the shared line must SAY something: interpolating an empty constant
    # satisfied the per-prompt assertions above while the four live prompts lost
    # the whole point of #4365. (An empty body is caught incidentally, because
    # the URL then disappears from the slice; a bodyless sentence was GREEN.)
    m = re.search(r"const onboardingInstructions = `([^`]*)`", wizard)
    assert m, "the shared onboarding-instructions line must be declared"
    assert "${ONBOARDING_INSTRUCTIONS_URL}" in m.group(1), (
        "the shared line must interpolate the served instructions URL")
    assert "not a skill" in m.group(1), (
        "the shared line must say onboarding is not a skill")
    # NOTE: the step-2 skills primer at main.jsx:~7950 is NOT pinned here and is
    # deliberately NOT made to interpolate SKILLS_LIST — it lives INSIDE the
    # `LEGACY_WIZARD_ARCHIVED` block (main.jsx:7845-8122, flag=false), i.e. it is
    # dead code kept byte-identical for the A0 rollback path. Editing it breaks
    # the archived-block line-count pin in overview.test.js. A review round
    # flagged it as a live surface that had drifted from the shared constant;
    # it is neither live nor reachable.
    # The prompts are pinned through the template they RETURN (see the
    # `${onboardingInstructions}` loop above) plus the constant's own body — a
    # bare whole-file membership check here would be satisfied by the
    # declaration alone and would overstate what it measures (#4365 round 5).

    # …and the filesystem-less harnesses (Claude Desktop/Web, ChatGPT) never ran
    # the installer and have no skills directory — the workflows prompt body is
    # their ONLY delivery surface, so it must name the instructions too.
    wf_start = main.index("function wizardWorkflowsText(")
    wf_end = main.index("\nfunction ", wf_start + 1)
    workflows = main[wf_start:wf_end]
    assert "${ONBOARDING_INSTRUCTIONS_URL}" in workflows, (
        "the teach-human workflows prompt must name the onboarding instructions")


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
