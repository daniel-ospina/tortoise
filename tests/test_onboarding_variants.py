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
import sys
import zipfile
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

# The served copies (app.premiselabs.co/skills/<name>/SKILL.md) are ALSO the
# source each `.skill` archive is BUILT from, so there is no third copy to keep
# in step: the archive is derived from the byte the parity gate pins and the
# dashboard serves.
#
# The DOCS origin (tortoise.premiselabs.co) carries the built archives only. The
# docs page itself links them, and a same-origin <a download> is what names the
# saved file. The DASHBOARD card links the same URLs cross-origin, where
# `download` is IGNORED — that path relies on `website/_headers` pinning
# `/skills/*.skill` to Content-Type octet-stream + Content-Disposition
# attachment, so it downloads instead of rendering.

# The three sha256-pinned served skills (config/served-skills-upstream.yml) get a
# docs-origin `.skill` archive: the ChatGPT card hands them over as installable
# skills and a one-click download must be SAME-ORIGIN.
#
# ⛔ `tortoise-onboarding` is NOT among them (#4365): onboarding is delivered as
# instructions the agent READS at its served URL, never installed as a skill, so
# there is no archive for it. Its served document is MIRROR_SKILL.
SERVED_SKILL_NAMES = ("how-to-use-tortoise", "tortoise-decide",
                      "tortoise-file-finding")

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

    The comparison is over BYTES, not decoded text (#3673 Target: "byte-identical"
    and "0 fail-open paths"). `Path.read_text()` opens with universal-newline
    translation, so `b"a\n"` and `b"a\r\n"` read EQUAL — a CRLF mirror passed
    this gate while the bytes actually served differed from the canonical.
    Measured: those two payloads are unequal as bytes and equal via `read_text`.
    `read_bytes()` is the same one-line read with no normalisation step left to
    fail open. A binary read also keeps the DIR / absent cases failing (OSError /
    a named AssertionError), which is the fail-closed behaviour asserted below.
    """
    assert canonical.exists(), f"canonical copy missing: {canonical}"
    assert mirror.exists(), f"deploy mirror missing: {mirror}"
    assert mirror.read_bytes() == canonical.read_bytes(), (
        f"parity broken: {mirror} drifted from {canonical}")


def test_m8_deploy_mirror_matches_canonical():
    """The dashboard deploy mirror is byte-identical to the canonical SKILL.md
    (drift-proofing — the old stage_variants concat guarantee carried forward).

    #3673: a copy that cannot be read is a FAILURE, not a skip (see
    assert_copies_parity), so this gate can never report a comparison it did not
    perform. It compares the TWO copies of the onboarding document that exist —
    the canonical live skill and the dashboard deploy mirror; #9660 briefly made
    it three (a docs-origin copy, to build an archive that made onboarding an
    installed skill) and removed it with that archive, because #4365 says
    onboarding is READ, not installed. The `.skill` archives of the three served
    skills are a different hop and are covered by
    test_9660_skill_archive_holds_exactly_its_own_skill (archive payload vs the
    served `SKILL.md`, byte for byte) plus
    test_9660_skill_archive_is_built_from_the_SERVED_source (no third copy).
    Watched failing in BOTH directions — a canonical-only edit and a mirror-only
    edit each exit non-zero — then restored to green."""
    assert_copies_parity(LIVE_SKILL, MIRROR_SKILL)


@pytest.mark.parametrize("name", SERVED_SKILL_NAMES)
def test_9660_skill_archive_is_built_from_the_SERVED_source(name):
    """#9660: one source per skill — the served copy, not a third duplicate.

    The archive must be derived from the byte the parity gate pins
    (`config/served-skills-upstream.yml` → `public/skills/<name>/SKILL.md`), so a
    docs-origin `SKILL.md` under `website/skills/` is an unpinned duplicate: it
    must not exist, and nothing may link it. (It was tried and removed — besides
    duplicating every skill's markdown, each new file is a whole-file subject of
    the `docs` lint gate.)
    """
    served = PUBLIC_SKILLS / name / "SKILL.md"
    assert served.is_file(), f"the served skill is the archive source: {served}"
    duplicate = REPO_ROOT / "website" / "skills" / name / "SKILL.md"
    assert not duplicate.exists(), (
        f"{duplicate.relative_to(REPO_ROOT)} duplicates the served skill and is "
        "pinned by nothing — the archive is built from the served copy")
    docs = (REPO_ROOT / "website" / "docs.html").read_text(encoding="utf-8")
    assert f"/skills/{name}/SKILL.md" not in docs, (
        "docs.html must not link a docs-origin SKILL.md that does not exist")


#: Every skill the ChatGPT card hands over, archive included.
SKILL_ARCHIVE_NAMES = SERVED_SKILL_NAMES


@pytest.mark.parametrize("name", SKILL_ARCHIVE_NAMES)
def test_9660_skill_archive_holds_exactly_its_own_skill(name):
    """#9660: the card hands over a `.skill` archive, not a `SKILL.md`.

    The ChatGPT uploader enforces the filename — a file named
    `tortoise-file-finding.md` is refused with "Choose a .zip, .skill, or
    SKILL.md file" — so several skills cannot ship as several `SKILL.md`
    downloads: they collide in the user's Downloads folder and the renamed copies
    are rejected. The archive (verified accepted by the live uploader) carries the
    skill folder, so each file keeps its own name.

    Two properties this pins, both observed in the live UI:
    * exactly ONE skill per archive — a `.zip` holding several skill folders is
      rejected ("Couldn't upload this skill"), so merging them must fail here;
    * the entry is `<name>/SKILL.md` at the archive root, with the skill's own
      frontmatter `name`, which is how the uploader derives the skill name.

    Built by tools/build_skill_archives.py from the SERVED copy; a source edit
    without a rebuild fails here rather than shipping a stale skill to users.
    """
    archive = REPO_ROOT / "website" / "skills" / f"{name}.skill"
    source = PUBLIC_SKILLS / name / "SKILL.md"
    assert archive.is_file(), (
        f"missing skill archive: {archive} — run "
        "uv run python tools/build_skill_archives.py")
    with zipfile.ZipFile(archive) as zf:
        entries = [i.filename for i in zf.infolist() if not i.is_dir()]
        assert entries == [f"{name}/SKILL.md"], (
            f"{archive.name} must hold exactly one skill (got {entries}); "
            "one archive = one skill — the uploader rejects a multi-skill zip")
        payload = zf.read(f"{name}/SKILL.md")
    assert payload == source.read_bytes(), (
        f"stale archive: {archive.name} drifted from {source} — run "
        "uv run python tools/build_skill_archives.py")
    declared = re.search(r"^name:\s*(\S+)\s*$",
                         payload.decode("utf-8").split("---")[1], re.M)
    assert declared and declared.group(1) == name, (
        f"{name}.skill frontmatter name must be {name!r} (got "
        f"{declared.group(1) if declared else None!r}); the uploader names the "
        "skill from it")


def test_9660_the_docs_origin_actually_publishes_the_archives():
    """The card links these archives CROSS-ORIGIN, so two deploy-time facts
    decide whether the hand-off works at all — and neither was asserted:

    * `website/skills/` is a NEW top-level entry, and the Pages deploy refuses
      to upload an UNCLASSIFIED one (`check_pages_upload_root.py`, run as a
      preflight). The gap is the COMBINED edit: classifying it `excluded`
      *together with* a matching `--exclude='/skills/'` in the deploy step keeps
      `test_pages_bindings.py`'s
      `test_every_top_level_entry_under_website_is_classified` green — that test
      only requires the table and the rsync excludes to AGREE, not that `skills`
      stays public — while every archive link 404s. (A bare flip to `excluded`
      is caught there: `excluded != staged` for an entry with no matching
      exclude.)
    * an unknown extension has no MIME mapping, and the dashboard's links are
      cross-origin, where browsers ignore `download` — so `/skills/*.skill`
      must be served as an attachment. Without the rule the file's behaviour
      depends on how the host guesses it.
    """
    sys.path.insert(0, str(REPO_ROOT))
    from tools.check_pages_upload_root import load_classification

    staged = load_classification()
    assert staged.get("skills") is True, (
        "config/pages-upload-classification.txt must classify `skills` as "
        "`public` — the deploy stages only public entries, and a 404 here is "
        "the exact dead-end the ChatGPT journey exists to remove")

    headers = (REPO_ROOT / "website" / "_headers").read_text(encoding="utf-8")
    rules = re.findall(r"^(\S[^\n]*)\n((?:[ \t]+[^\n]*\n)+)", headers, re.M)
    matched = [(pat, block) for pat, block in rules if pat.strip() == "/skills/*.skill"]
    assert matched, (
        "website/_headers must carry a `/skills/*.skill` rule — the card links "
        "the archives cross-origin, where `download` is ignored, so only the "
        "response headers make it a download")
    block = matched[0][1]
    assert re.search(r"^\s+Content-Type:\s*application/octet-stream\s*$", block, re.M), (
        "/skills/*.skill must be served as application/octet-stream")
    assert re.search(r"^\s+Content-Disposition:\s*attachment", block, re.M), (
        "/skills/*.skill must be served with Content-Disposition: attachment — "
        "otherwise the browser may render the zip in-tab instead of saving it")


def _js_skill_archive_names() -> tuple[str, ...]:
    """`SKILL_ARCHIVE_NAMES` from harnesses.js — the set the CARD hands over."""
    src = (REPO_ROOT / "website" / "apps" / "dashboard" / "src"
           / "harnesses.js").read_text(encoding="utf-8")
    start = src.index("export const SKILL_ARCHIVE_NAMES")
    block = src[start:src.index("])", start)]
    return tuple(re.findall(r"'([a-z0-9-]+)'", block))


def _build_skill_names() -> tuple[str, ...]:
    """`SKILL_NAMES` from tools/build_skill_archives.py — the BUILT set."""
    src = (REPO_ROOT / "tools" / "build_skill_archives.py").read_text(
        encoding="utf-8")
    start = src.index("\nSKILL_NAMES = (")
    block = src[start:src.index(")", start)]
    return tuple(re.findall(r'"([a-z0-9-]+)"', block))


def test_9660_onboarding_is_never_installed_as_a_skill():
    """#4365: `tortoise-onboarding` is READ, never INSTALLED — pin every list.

    The served document states this twice in its own first screen: the ⛔ banner
    ("delivered as INSTRUCTIONS, never installed as a skill … never copied into a
    harness's skills namespace") and the ChatGPT note, which originally told a
    ChatGPT user to install this very document. #4365 records the rule as an owner
    decision, with an exception allowed only if it is named and justified there.

    The #9660 card shipped a `tortoise-onboarding.skill`, so the journey handed a
    user a file whose own first screen forbids its upload, and the agent reading it
    was told both "never installed as a skill" and "install this as a skill". No
    guard observed any list: the archive test was parametrized over a literal that
    contained `tortoise-onboarding`, i.e. it pinned the MECHANISM of that upload.
    This asserts the RULE, against all three lists that decide what is installed —
    the card's JS list, the archives the tool BUILDS, and the served set — and
    against the artifacts on disk. A fourth entry in any one of them fails here.
    """
    assert _js_skill_archive_names() == _build_skill_names() == SERVED_SKILL_NAMES, (
        "the card's install set, the set build_skill_archives.py BUILDS and the "
        "served set must be ONE set — divergence ships a 404 link on the card or "
        "an archive nothing hands over "
        f"(js={_js_skill_archive_names()}, "
        f"build={_build_skill_names()}, served={SERVED_SKILL_NAMES})")
    # …and against the INSTALLER, which is the authoritative ancestor: an
    # upstream `--sync` that adds a skill updates the installer and the pin, and
    # a hand-kept literal here would not notice, so the card could hand over
    # three while the installer ships four.
    assert tuple(_installer_skills()) == _js_skill_archive_names(), (
        "the installer's SKILLS=(…) set and the card's install set must be ONE "
        f"set (installer={_installer_skills()}, "
        f"card={_js_skill_archive_names()})")
    for label, names in (("harnesses.js SKILL_ARCHIVE_NAMES", _js_skill_archive_names()),
                         ("build_skill_archives.py SKILL_NAMES", _build_skill_names()),
                         ("the served set", SERVED_SKILL_NAMES)):
        assert "tortoise-onboarding" not in names, (
            f"{label} includes tortoise-onboarding — #4365 says onboarding is "
            "delivered as INSTRUCTIONS and never installed as a skill; if a "
            "harness genuinely needs it installed, that is a RECORDED exception "
            "on #4365 (naming the harness and the reason) plus an OVERRIDES: "
            "line, not a list edit")
    # …and the artifacts: no archive to upload, no docs-origin copy to build one
    # from. A leftover archive is exactly the file a user would still download.
    stray = (REPO_ROOT / "website" / "skills" / "tortoise-onboarding.skill")
    assert not stray.is_file(), (
        f"{stray.relative_to(REPO_ROOT)} exists — remove it: the card installs "
        "three skills, and #4365 forbids this fourth")
    assert not (REPO_ROOT / "website" / "skills" / "tortoise-onboarding").exists(), (
        "the docs-origin copy of the onboarding document has no consumer once "
        "onboarding is not archived; it would be an unpinned duplicate")
    # The served document must not contradict itself either: the banner that
    # forbids the install is still there, so the two halves of the journey agree.
    served = LIVE_SKILL.read_text(encoding="utf-8")
    assert "never installed as a skill" in served, (
        "the served onboarding document must keep the #4365 banner — it is the "
        "artifact a ChatGPT user reads, and it must not be reworded into "
        "agreeing with an install it forbids")
    assert "never copied into a harness's skills namespace" in served
    # The docs card is the reference copy of the same journey: a link to a
    # removed archive is a 404 that nothing else observes.
    docs = (REPO_ROOT / "website" / "docs.html").read_text(encoding="utf-8")
    assert "/skills/tortoise-onboarding.skill" not in docs, (
        "website/docs.html still links the removed onboarding archive")
    for name in SERVED_SKILL_NAMES:
        assert f'href="/skills/{name}.skill"' in docs, (
            f"website/docs.html must still hand over {name}.skill")


def test_9660_build_skill_archives_rejects_an_unrecognised_argument():
    """A drift gate must not fall through to WRITE mode on a typo.

    `main` read `check = "--check" in argv`, so every other argument was silently
    ignored: `--chek` wrote the tree and exited 0 — the tool whose job is to refuse
    drift mutating the checkout and reporting success. A caller now gets exit 2 and
    a usage line, and nothing is written (asserted on the artifact, not the exit
    code alone).
    """
    tool = REPO_ROOT / "tools" / "build_skill_archives.py"
    before = {p.name: p.read_bytes()
              for p in (REPO_ROOT / "website" / "skills").glob("*.skill")}
    result = subprocess.run(
        [sys.executable, str(tool), "--chek"],
        capture_output=True, text=True, cwd=REPO_ROOT)
    assert result.returncode == 2, (
        f"an unrecognised argument must be refused (got {result.returncode}: "
        f"{result.stdout}{result.stderr})")
    assert "unrecognised argument" in result.stderr, result.stderr
    assert "all " not in result.stdout, (
        "the refusal must not also report the tree as current — that is the "
        "success line of a run that did not happen")
    after = {p.name: p.read_bytes()
             for p in (REPO_ROOT / "website" / "skills").glob("*.skill")}
    assert after == before, "a refused invocation must not rewrite any archive"


def test_9660_chatgpt_first_job_names_only_ADVERTISED_tools():
    """ChatGPT builds its tool grant from `tools/list`, so a RETIRED name in its
    first job is a call it can never make.

    The step used to end with the SHARED verify body — "Call tortoise_health" —
    while three artifacts in the same tree said that call is impossible from
    ChatGPT: `tool_registry.py` retires `tortoise_health` in favour of
    `tortoise_overview(section="health")`; `_RetiredToolTransform` serves retired
    names on `get_tool` **without advertising them in `list_tools`**; and the
    onboarding document this journey hands over tells the agent to use the
    ADVERTISED form. The verify half of the journey's first action therefore
    asked for a tool ChatGPT has no grant for.

    Asserted on the RENDERED snapshot (the value both suites share) against the
    REGISTRY itself, so a future retirement cannot pass here: any `tortoise_*`
    token the copy names must be advertised, never merely retired.
    """
    from tortoise.tool_registry import RETIRED_USE_INSTEAD, TOOL_REGISTRY

    snap = _rendered_snapshot()
    body = snap["prompts"]["chatgpt/1"]
    advertised = {t.name for t in TOOL_REGISTRY}
    mentioned = set(re.findall(r"tortoise_[a-z_]+", body))
    assert mentioned, (
        "the ChatGPT first job must name the tools it calls — otherwise the step "
        "that proves the connection proves nothing")
    retired_mentioned = mentioned & set(RETIRED_USE_INSTEAD)
    assert not retired_mentioned, (
        f"the ChatGPT first job names RETIRED tool(s) {sorted(retired_mentioned)} — "
        "they are absent from tools/list, so a tool-grant client cannot call them; "
        f"use the advertised form {RETIRED_USE_INSTEAD[sorted(retired_mentioned)[0]]!r}")
    assert mentioned <= advertised, (
        f"the ChatGPT first job names unknown tool(s) "
        f"{sorted(mentioned - advertised)}")
    # The substance, so a rewrite cannot satisfy the rule with a tool that is
    # advertised but does nothing for onboarding.
    assert 'tortoise_overview(section="health")' in body, (
        "the ChatGPT first job must verify through the advertised health section")
    assert "tortoise_create_point" in body, (
        "…and must still file the first memory, which is what marks it connected")
    # The shared body is correct where the legacy name still answers (get_tool):
    # the other six harnesses keep it, so this is a per-harness fix, not a
    # rewrite of everyone's copy.
    for harness in ("pi", "cursor", "claude", "codex", "claude-desktop",
                    "claude-web"):
        assert "tortoise_health" in snap["prompts"][f"{harness}/2"], (
            f"{harness}: the shared verify body is unchanged for config-file "
            "harnesses — a per-harness fix must not reword theirs")


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


def test_parity_gate_compares_bytes_not_newline_normalised_text(tmp_path):
    """#3673 Target "0 fail-open paths": the contract is BYTE-identity.

    A gate that reads TEXT cannot see a line-ending difference: `read_text()`
    applies universal-newline translation, so a mirror committed with CRLF read
    EQUAL to an LF canonical while the bytes served differed. That is not the
    comparison the gate claims to make, so it is a fail-open path and not a
    nicety. The payloads below differ ONLY by line ending — provably unequal as
    bytes and equal as text — so this test is what stops `assert_copies_parity`
    reverting to `read_text()`.
    """
    canon = tmp_path / "canonical.md"
    mirror = tmp_path / "mirror.md"
    canon.write_bytes(b"line one\nline two\n")
    mirror.write_bytes(b"line one\r\nline two\r\n")
    assert canon.read_text(encoding="utf-8") == mirror.read_text(
        encoding="utf-8"), "precondition: the payloads differ only by newline"
    with pytest.raises(AssertionError, match="drifted"):
        assert_copies_parity(canon, mirror)


def test_4365_served_document_sends_chatgpt_to_a_path_that_exists():
    """#4365/#2698: the served document's §2 note must not send a ChatGPT user to
    the dashboard's "ChatGPT tab" — #2698 deleted that tab, and #2912's
    four-family HARNESS_FAMILIES has no chatgpt entry. Reverting the note to the
    retired tab tripped no test at all (mutation-verified, #4365 review round 4).

    Canonical only: the mirror is pinned byte-identical by
    `test_m8_deploy_mirror_matches_canonical`, so this covers both copies."""
    skill = LIVE_SKILL.read_text(encoding="utf-8")
    assert "chatgpt.com/plugins" in skill, (
        "the served document must name the ChatGPT connector surface")
    # #9660: the note carried OpenAI's OLD path (Settings → Security and login →
    # Developer mode, then a Developer-mode app). Read off a signed-in Plus
    # account on 2026-10-23 that page has no Developer-mode switch at all and the
    # connector is created from `Plugins → Add → Add custom MCP server`. An agent
    # that receives this document is told which surface to open, so the stale
    # path sends it hunting a toggle that does not exist.
    for stale in ("Security and login", "Scan Tools", "Developer-mode app"):
        assert stale not in skill, (
            f"the served document must not carry the retired ChatGPT path {stale!r} "
            "(#9660)")
    assert "Add custom MCP server" in skill, (
        "the served document must name the real connector entry point (#9660)")
    # #9660: the grant is three clicks, and the middle one opens a dialog the user
    # has to answer. The note said "connect the plugin", which left the consent
    # dialog between Connect and Authorize unmentioned — the live run stopped
    # there until it was answered (2026-10-23).
    assert "Connect Tortoise" in skill, (
        "the served document must name the consent dialog's button, not just "
        "'connect the plugin' — the grant does not complete without it")
    # …and the ORGANIZATION act survives on BOTH consent shapes. The consent page
    # NAMES the organization only when the account has one selectable org; with
    # several it renders a chooser, hides the named-org line and leaves Authorize
    # disabled until one is picked (tortoise/oauth.py `consent_preview`, the
    # `len(orgs) > 1` memberships path). "Check the name" alone therefore points
    # at text a multi-org account never sees — the same class of defect as the
    # unnamed consent dialog — so the pick is pinned here (#9660 review).
    assert "pick the Organization you are onboarding" in skill, (
        "the served document must keep the multi-org act: the consent page shows "
        "a CHOOSER (and no named organization) when the account has more than "
        "one selectable org, and Authorize stays disabled until one is picked")
    # …and the pick must be placed BEFORE Authorize: on the chooser branch the
    # button is disabled until a value is chosen (tortoise/oauth.py sets
    # `authBtn().disabled = true`; only the select's onchange re-arms it), so
    # "click Authorize, then pick" names an inert control. Bounded to the ChatGPT
    # NOTE: measured over the whole document, any bolded `**Authorize**` elsewhere
    # would red this with a message blaming the ChatGPT recipe (#9660 review).
    note_at = skill.index("#1701 — ChatGPT is a 7th harness")
    note = skill[note_at:skill.index("installing the three reusable skills", note_at)]
    assert note.index("pick the Organization") < note.index("**Authorize**"), (
        "the served document's ChatGPT recipe must pick the Organization before "
        "Authorize — an agent reads this list aloud to a user who cannot see "
        "which control is disabled")
    assert "ChatGPT tab" not in skill, (
        "the served document must not name the retired dashboard ChatGPT tab")
    # #9660 OWNER RULING (2026-10-23): the chooser DOES carry a ChatGPT
    # surface now (the exclusion above was reversed), so the document must say
    # so — while keeping the docs page as the reference copy. This replaces the
    # old "there is no ChatGPT chooser surface" pin, which is now false.
    assert "no ChatGPT surface in the dashboard chooser" not in skill, (
        "the served document still claims ChatGPT is absent from the dashboard "
        "chooser — the #9660 owner ruling added it")
    assert "dashboard's connect step (the ChatGPT" in skill, (
        "the served document must tell a ChatGPT user it has a dashboard surface")
    assert "tortoise.premiselabs.co/docs#chatgpt" in skill, (
        "the served document must still point at the reference copy of those steps")


def test_5153_served_document_pins_the_canonical_mcp_key_variable():
    """#5153: the served document's Pi step must state that `TORTOISE_API_KEY`
    is the only name any code path reads, and that a differently-named MCP
    credential — a #3615 policy difference, not a defect — must therefore be
    aliased. Without that, a profile following the doc exports only its own
    name, the committed `.mcp.json` expands `${TORTOISE_API_KEY}` to an empty
    Bearer, and the server answers 401 while the CLI/SDK/static-auth paths go
    unauthenticated: the config-name mismatch this issue reports.

    The variable name is pinned against the CODE, not against itself — every
    `Bearer ${VAR}` snippet the document carries must name a variable that
    `tortoise/mcp_client.py` (the MCP HTTP client) actually reads, so a snippet
    repointed at an unread name — exactly the issue's proposed "fix option 1"
    — fails here.

    Canonical only: the mirror is pinned byte-identical by
    `test_m8_deploy_mirror_matches_canonical`, so this covers both copies.

    #5153 P1 review: the paragraph must NOT re-assert the credential-
    independent capture reassurance. That reassurance is FALSE on the Pi host
    this very step instructs: `agent-infra/extensions/reflect-hook.ts`
    resolves the credential itself and starts hosted capture on its mere
    PRESENCE (`env.TORTOISE_API_KEY || file.apiKey`), reading no
    `TORTOISE_CAPTURE`. The claim is true only of the in-repo consent-gated
    paths (`tortoise/capture_consent.py`), so the document must name the
    per-surface split and the open dependency (`agent-infra#1117`) instead of
    generalising."""
    skill = LIVE_SKILL.read_text(encoding="utf-8")
    assert "is the canonical name, and the **only** one any code path" in skill, (
        "the Pi step must state that TORTOISE_API_KEY is the only name read")
    assert 'export TORTOISE_API_KEY="$TORTOISE_MCP_API_KEY"' in skill, (
        "the Pi step must show the alias a separately-named MCP credential needs")

    referenced = set(
        re.findall(r'"Authorization":\s*"Bearer \$\{([A-Z0-9_]+)\}', skill))
    assert referenced, (
        "the served document must carry an env-indirect Bearer snippet")
    client = (REPO_ROOT / "tortoise" / "mcp_client.py").read_text(encoding="utf-8")
    unread = sorted(v for v in referenced
                    if f'os.environ.get("{v}"' not in client)
    assert not unread, (
        "the served Bearer snippet references a variable the MCP HTTP client "
        f"never reads ({', '.join('$' + v for v in unread)}) — the 401 in #5153")

    # ── #5153 P1: no credential-independent capture reassurance ────────────
    # The false claim, in the two forms the doc carried it. "The alias is safe
    # since #3615" is the added sentence; "never on the credential" is the
    # generalisation that made it read as authoritative. Both are false on a
    # Pi host and must not come back.
    for stale in ("The alias is safe since #3615", "never on the credential",
                  "no longer required for that purpose"):
        assert stale not in skill, (
            "the false credential-independent capture reassurance returned "
            f"({stale!r}): the Pi reflect-hook captures on credential presence, "
            "not on TORTOISE_CAPTURE — agent-infra#1117")
    # The replacement must be per-surface and name the truth on each side:
    # the in-repo consent gate, the ungated Pi reflect-hook, and the open
    # dependency that makes the Pi side a known gap rather than a settled fact.
    for required in ("tortoise/capture_consent.py", "reflect-hook",
                     "agent-infra#1117"):
        assert required in skill, (
            "the capture-gating claim must be per-surface and name "
            f"{required!r} (#5153 P1)")

    # The same reassurance was mirrored in .env.example; pin it there too.
    env_example = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    for stale in ("The alias is safe since #3615", "never on the credential"):
        assert stale not in env_example, (
            f".env.example repeats the false reassurance ({stale!r})")
    assert "agent-infra#1117" in env_example, (
        ".env.example must name the open Pi reflect-hook dependency (#1117)")


def test_8169_onboarding_documents_the_support_direction_of_strength():
    """#8169: the advice taught only how to WEAKEN an operator.

    Every operator-strength move this document carried was a dampener
    (`tortoise_mitigate_operator`, strength 0.10–0.50), so an agent holding
    evidence that SUPPORTED a connection had no documented move — the gap
    that left #6151's supporter half with no surface to write to. The
    mechanism itself was already there (#6151 owner ruling → PR #8181:
    `tortoise/weights.py::annotation_factor`, a symmetric 0.5–2.0 multiplier
    where an unset rating is exactly 1.0); what was missing HERE was the
    instruction to use it, because `tortoise_annotate_operator` read like a
    cosmetic note rather than the positive counterpart to dampening.

    Two things are pinned together, because either alone is the defect:
    the branch must exist, AND it must not be confused with #2315 — the
    `never > 0.50` rule governs `mitigation_strength`, not the annotation
    multiplier, so a document that merged the two would re-open a settled
    decision instead of documenting one.

    Canonical only: the mirror is pinned byte-identical by
    `test_m8_deploy_mirror_matches_canonical`, so this covers both copies.
    """
    skill = LIVE_SKILL.read_text(encoding="utf-8")

    # The weakening branch must survive: this change ADDS a direction.
    start = skill.index("5. **Mitigate, don't NAND, for fit.**")
    step = skill[start:skill.index("\n6. **Options can IMPL/NAND each other**")]
    assert "tortoise_mitigate_operator" in step
    assert "never >0.50" in step, (
        "the mitigation band is #2315's ruling and must stay stated")

    # The supporting branch, sliced so a claim made elsewhere in a 900-line
    # document cannot satisfy it.
    heading = "Rating semantics — the SUPPORT direction"
    assert heading in step, (
        "step 5 must carry the positive counterpart to mitigating — rating "
        "the operator to make a connection count MORE (#8169)")
    rating = step[step.index(heading):]
    # Phrases below are matched against a WHITESPACE-FOLDED view: the document
    # is hard-wrapped at ~78 columns, so any multi-word phrase straddles a
    # newline and a raw substring test silently never matches.
    flat = " ".join(rating.split())
    assert "annotation_factor" in rating, (
        "the rating band must name its single source in tortoise/weights.py")
    assert "#2315" in rating, (
        "the band must separate itself from #2315's never >0.50 dampener "
        "rule — the two multipliers are different mechanisms")
    assert "neutral (factor 1.0)" in flat, (
        "an unset rating is exactly neutral and the paragraph must SAY so — "
        "the bare `1.0` is satisfied by `(base 1.0)` in the bounds sentence, "
        "so a deleted neutrality clause stayed green")
    # Anchored on the PHRASE (and on a `\b`, so #2315's `0.50` cannot stand in
    # for it), not on the bare number. `"0.5"` is a substring of the
    # `never > 0.50` text this same slice is REQUIRED to carry four lines up,
    # so a bare `"0.5" in rating` stayed green with the worst-edge sentence
    # deleted — the assertion whose message is "the 0.5 edge must be stated"
    # never tested that, and a one-sided band would have passed.
    assert re.search(r"worst rating is 0\.5\b", flat), (
        "the band's WORST edge (0.5) must be stated where the band is defined, "
        "or the document implies a gain-only multiplier")
    assert re.search(r"best is 2\.0\b", flat), (
        "the band's BEST edge (2.0) must be stated where the band is defined")

    # The bounds must be DIRECTIONAL, and the compression must be stated for
    # BOTH directions. `annotation_factor` is applied before the [0.1, 10.0]
    # clamp, so on an operator-targeting NAND (base 16.0) the ceiling absorbs
    # the GAIN entirely (16.0 * 2.0 clamps to 10.0 — nothing to gain) AND
    # compresses the dampening: only a factor below 10.0/16.0 = 0.625 clears
    # the ceiling, so a mild down-rating moves nothing and the worst rating
    # lands at 8.0, a ~20% cut rather than the nominal 50%. Two earlier
    # revisions of the paragraph were false here and stayed green because the
    # test pinned the wrong claim: "not at all" (direction-blind) and then
    # "only the GAIN is squeezed" (magnitude-blind). `weights.py:121-131`
    # states the true shape — "exact near the bottom of the range and
    # compresses as the base approaches the ceiling".
    assert "RAISING" in flat, (
        "the NAND bound must be scoped to RAISING the weight — the clamp "
        "absorbs gain only on a plain NAND, so a direction-blind bound is false")
    assert "gains nothing" in flat, (
        "the ceiling's effect on the GAIN must be stated: a better rating on an "
        "operator-targeting NAND cannot raise it")
    assert "16.0 × 0.625 = 10.0" in flat, (
        "the absorption threshold must be stated: 16.0 × 0.625 = 10.0, so any "
        "factor at or above 0.625 is clamped and a mild down-rating is inert "
        "— without it the paragraph implies every down-rating lands")
    assert "16.0 × 0.5 = 8.0" in flat, (
        "the worked worst case must be stated: 16.0 × 0.5 = 8.0, i.e. a ~20% "
        "cut from the clamped 10.0, not the nominal 50%")
    assert "compresses" in flat, (
        "the ceiling compresses the band's EFFECT as the base approaches it "
        "(weights.py:121-131); a symmetric-band claim without the compression "
        "is false on a NAND")
    assert "only the gain" not in flat.lower(), (
        "the clamp squeezes the DAMPENING too — 'only the GAIN is squeezed' is "
        "false on a 16.0 base, where a mild down-rating is absorbed and the "
        "worst lands at 8.0 instead of 4.0")

    # `annotate_operator` takes FOUR required dimensions, not two — a doc that
    # names only bias/precision sends the caller at a TypeError. Asserted in
    # `rating` (the band's own paragraph), so the dimensions are stated where
    # the instruction to rate lives, not merely somewhere in step 5.
    for dim in ("bias", "precision", "consistency", "directness"):
        assert dim in rating, (
            f"annotate_operator requires {dim!r}; the rating paragraph must "
            "name all four")


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

    # Exactly ONE definition: a second export would make a first-match search
    # read whichever came first, so this file and test_harness_mcp_config.py
    # would disagree about the URL while both looked green (#4836).
    m = re.findall(
        r"^export const ONBOARDING_INSTRUCTIONS_URL =\s*\n?\s*'([^']+)'",
        harnesses, re.M)
    assert len(m) == 1, (
        f"ONBOARDING_INSTRUCTIONS_URL must be defined exactly once, found {m}")
    url = m[0]
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
    # #3430: the per-harness `UNIVERSAL_COMMAND` blocks were deleted (only the Codex
    # Desktop block was ever rendered). The surviving entry contributes NOTHING to
    # the loop below — that block names the installer only as a commented curl
    # invocation and enumerates no skill set — so the coverage that matters is the
    # four rendered wizard prompts — the `checked >= 4` floor at the end of this
    # test is what fails if one of them loses its enumeration.
    surfaces.update({f"CODEX_DESKTOP_COMMAND.{k}": v for k, v in snap["commands"].items()})
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
    dashboard wizard, #1566/#1730).

    #2936: the request has ONE home (`wizardBeacon`) since the shipped connect
    step was wired to it — the two call paths below (the live cards' `onCopied`
    and the ARCHIVED render's `wizardCopy`) must both resolve the leaf through
    `analyticsHarnessOf` before it reaches the body.
    """
    dashboard = REPO_ROOT / "website" / "apps" / "dashboard" / "src" / "main.jsx"
    src = dashboard.read_text(encoding="utf-8")
    assert "onboarding/state" in src
    # The emitter is the only site that builds the body, and the body carries
    # the RESOLVED harness — never the raw wizard leaf (a UI-only leaf like
    # 'codexDesktop' is not in the server vocabulary).
    assert "harness: attributed" in src
    assert "const attributed = analyticsHarnessOf(harness)" in src
    # Both call paths go through it: the archived render's wizardCopy and the
    # shipped cards' onCopied.
    assert "wizardBeacon(wizardHarness, 'config')" in src
    assert "onCopied={() => wizardBeacon(" in src


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


# ── #3232: the MCP session boundary in the installer + generic copy ─────────
#
# The wizard prompts now cross the session boundary before they call the
# server (#3232). The installer-written surfaces carry the SAME ordering
# requirement, but with a different failure mode: the AGENTS.md standing block
# is written into every provisioned repo and re-read by the agent in EVERY
# later session, so a block that says "verify with tortoise_health before
# reporting done" with no restart step re-creates the defect in every future
# session of that repo. The two generic restart lines are pinned so a future
# edit cannot make them contradict the per-harness credential mechanisms.
QUICKSTART = REPO_ROOT / "docs" / "quickstart-cloud.md"


def test_3232_agents_standing_block_orders_the_restart_before_the_verify():
    """The AGENTS.md heredoc must carry the corrected ORDER, not merely lack
    the old demand: deleting the verify sentence would satisfy "no bad order"
    while removing the only checkpoint cue the block has."""
    src = _installer_text()
    # The heredoc writes its delimiters via ${ms}/${me}, so the LITERAL markers
    # only appear in the variable assignments — slice the block by its own first
    # and last content lines instead.
    block = src[src.index("# Tortoise — agent memory"):
                src.index('${me}" > "$block"')]
    flat = " ".join(block.split())
    # #3429: pinned as an ORDER (positions), not as one sentence. The sentence
    # was reworded to name WHO restarts and that the server is loaded at session
    # start; an equality pin on the old wording made that rewording a red, which
    # trains the next author to delete the pin instead of keeping the order.
    write_at = flat.index("Write the MCP config")
    restart_at = flat.index("restart the client so the new session reads it")
    verify_at = flat.index("run the verify with tortoise_health")
    assert write_at < restart_at < verify_at, (
        "the AGENTS.md block must write the config, RESTART the client, and "
        "only then verify — the standing block is re-read every session, so a "
        "verify-before-restart order re-creates #3232 for the whole repo")
    assert "THIS session cannot see it" in flat and "that NEW session" in flat, (
        "the block must say the MCP server is loaded at session start and that "
        "the verify belongs to the NEW session — 'restart the client' alone "
        "does not tell an agent it cannot verify in the session that wrote the "
        "config (#3232's defect, restated for a surface re-read every session")


def test_3232_generic_restart_lines_are_exact():
    """The installer's stdout cue and the quickstart line are the two generic,
    non-per-harness restart statements. Each is pinned against its exact
    string so an edit that contradicts a harness's own mechanism (a new shell
    for Pi/Codex CLI, a registry + relaunch for Codex Desktop) is visible."""
    src = _installer_text()
    assert ('echo "Next: restart your agent, then confirm the skills are '
            'listed:"') in src, (
        "the installer's closing cue must restart, THEN confirm/list the "
        "skills — the skills directory is read at agent start")
    quick = QUICKSTART.read_text(encoding="utf-8")
    assert "Restart your client." in quick, (
        "docs/quickstart-cloud.md must keep its restart cue before the "
        "tools-appear sentence")
