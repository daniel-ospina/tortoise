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

import ast
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
    # #5439: the served document must not INSTRUCT a retired tool. The COUNT is the
    # assertion, not a presence check — and that distinction is the whole point:
    # the document legitimately keeps exactly ONE mention, the ChatGPT carve-out's
    # retirement disclosure ("the legacy `tortoise_health` name is retired, so it is
    # absent from `tools/list`"). A presence check for the advertised call is
    # satisfied BY that disclosure, which says the opposite of what it appears to
    # pin: measured on the pre-fix blob, `tortoise_graph_overview(section="health")`
    # already occurs once, at the disclosure, so reverting all ten instruction sites
    # left such an assertion GREEN (review finding F1 on PR #11719 — the same
    # NAME-is-weaker-than-the-CALL class this file documents at #10914 below).
    #
    # ⛔ THE DISCLOSURE'S OWN SENTENCE IS THE PIN, not the word "retired" (#11719
    # review, third head, finding 2 — measured there before this was written).
    # `"retired" in line` is a spelling an INSTRUCTION can also carry: rewriting
    # the disclosure to "`tortoise_health` is retired, but call it anyway —
    # tools/list hides it and" left one mention, containing "retired", and this
    # guard PASSED while the document instructed a name absent from `tools/list`.
    # The count alone cannot tell the disclosure from an instruction either (that
    # is finding 1, one head earlier). So the property is: the document names the
    # retired tool EXACTLY ONCE, and that occurrence is the disclosure — the one
    # that names the NAME as retired, says it is absent from `tools/list`, and
    # says it will not appear in the connector's grant.
    #
    # ⛔ THE COUNT IS OVER OCCURRENCES OF THE NAME, NOT LINES (#11719 review,
    # fourth head, finding 1 — measured there before this was written). A count of
    # LINES says nothing about what else shares the line: appending an instruction
    # ON THE LINE THAT CARRIES THE NAME — "`tortoise_health` name is retired, so it
    # is absent from `tools/list` and — but call `tortoise_health` anyway if the
    # container is missing." — leaves ONE line naming it, and the line-count form
    # PASSED (measured: `pytest tests/test_onboarding_variants.py -q` → 42 passed)
    # while the document instructed a
    # name absent from `tools/list`. An append AFTER `(#3883)` lands on the NEXT
    # line instead, which the line-count form DOES catch — so the line count is
    # defeated by SHAPE (re-wrap the disclosure and the same instruction slips
    # through), and only the occurrence count is not. Counting the NAME closes it:
    # with `_flat.count(name) == 1` AND the disclosure present, the single
    # occurrence
    # of the name is necessarily INSIDE the disclosure sentence, so no second site
    # can exist — on that line or any other. (Pre-fix blob: 11 occurrences. This
    # head: 1, the disclosure alone.)
    #
    # Matched against the whitespace-normalised document so re-wrapping the
    # paragraph cannot red a correct document the way a line-shaped pin would; a
    # legitimate REWORD of the disclosure moves this pin with it, deliberately,
    # because the sentence is what the served document promises the ChatGPT
    # reader.
    _flat = " ".join(content.split())
    _disclosure = ("`tortoise_health` name is retired, so it is absent from "
                   "`tools/list` and will not appear in this connector's tool "
                   "grant (#3883)")
    _retired_hits = [ln for ln in content.splitlines() if "tortoise_health" in ln]
    _occurrences = _flat.count("tortoise_health")
    assert _occurrences == 1 and _disclosure in _flat, (
        "the served document must name the RETIRED `tortoise_health` ONLY inside "
        f"its retirement disclosure; found {_occurrences} occurrence(s) of the "
        f"name across {len(_retired_hits)} line(s): {_retired_hits[:3]}. The "
        f"disclosure sentence is {_disclosure!r}. "
        'The verify call is tortoise_graph_overview(section="health") (#5439)')
    # ⛔ THE VERIFY PIN IS WRAP-INSENSITIVE TOO, for the same reason the two
    # assertions above are (review finding on `f36e0a5abb`, P3 — measured there
    # before this was written). It was `in content`, a raw-text, line-shaped pin
    # sitting three lines under a comment stating that pins are matched against
    # the whitespace-NORMALISED document "so re-wrapping the paragraph cannot red
    # a correct document". Measured: re-wrapping §4's step the way it renders —
    # "1. Call" / "   `tortoise_graph_overview(section=\"health\")` (MCP tool, …)"
    # — leaves the document CORRECT and reds this pin. A false block on true prose
    # trains the next author to delete the pin instead of keeping the order (the
    # #3429 failure this file already records below). `_flat` is in scope from
    # above, so the fix is the haystack: the rendered `1. Call …` prefix is kept,
    # which is what makes it non-vacuous — the ChatGPT disclosure carries the bare
    # call string, but it does not carry the imperative.
    assert '1. Call `tortoise_graph_overview(section="health")`' in _flat, (
        "the §4 verify step must name the ADVERTISED health section — a retired "
        "name is absent from `tools/list`, so a client that honours its granted "
        "tool list has no function to call (#5439)")
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
    `tortoise_graph_overview(section="health")`; `_RetiredToolTransform` serves retired
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
    assert 'tortoise_graph_overview(section="health")' in body, (
        "the ChatGPT first job must verify through the advertised health section")
    # ⛔ BOTH HALVES, for the reason this test exists (#11609 lane P1): the file
    # half used to be satisfied by naming `tortoise_create_point`, which was itself
    # retired forward into `create_entity`'s `type="point"` arm — so a body that
    # kept it would satisfy this assertion while failing the retirement rule above.
    assert 'tortoise_create_entity(type="point"' in body, (
        "…and must still file the first memory through an ADVERTISED door, which is "
        "what marks it connected")
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
    # #11767: re-aimed off the retiring host, ONCE, so the copy does not have to
    # be pasted again. ⚠️ MEASURED TWICE, because #12031's deploy landed between:
    # before it (2026-11-12 05:49Z) the replaced value
    # `tortoise.premiselabs.co/docs#chatgpt` answered 200 — 0 hops — and the
    # installed one answered 301 → 200. AFTER it (05:52Z, three cache-busted
    # reads) BOTH spellings are ONE hop, 301 onto `premiselabs.co/tortoise/docs`
    # (200, 0 hops): so the move is 1-hop → 1-hop and cost nothing, and the
    # retired host is NOT the 0-hop shortcut a future lane might re-adopt on the
    # old reading. The 0-hop value is the one #12049 installs.
    # ⛔ SCHEME-QUALIFIED on purpose: the bare host needle
    # `"premiselabs.co/docs#chatgpt"` is ALSO a substring of the retired
    # `"tortoise.premiselabs.co/docs#chatgpt"` (measured:
    # `'premiselabs.co/docs#chatgpt' in 'tortoise.premiselabs.co/docs#chatgpt'`
    # is True), so a bare-host pin stays green on a revert to the exact value
    # #11767 removes — the regression it exists to catch. The `https://` prefix
    # is what makes only the canonical origin match.
    assert ("https://premiselabs.co/docs#chatgpt" in skill
            or "https://premiselabs.co/tortoise/docs#chatgpt" in skill), (
        "the served document must still point at the reference copy of those "
        "steps, on the canonical origin — BOTH spellings terminate on the same "
        "page, so pinning one would red the 0-hop re-point #12049 owns while "
        "the sibling guard in test_runtime_urls_are_canonical.py accepts either")


def _is_environ(node: ast.AST) -> bool:
    """True for `os.environ` / `environ` — the object, not a call on it."""
    if isinstance(node, ast.Attribute):
        return node.attr == "environ"
    return isinstance(node, ast.Name) and node.id == "environ"


def _loop_reads_environ(loop: ast.For) -> bool:
    """True when a loop's body consults the environment (`get` or subscript)."""
    for inner in ast.walk(loop):
        if (isinstance(inner, ast.Call)
                and isinstance(inner.func, ast.Attribute)
                and inner.func.attr == "get"
                and _is_environ(inner.func.value)):
            return True
        if isinstance(inner, ast.Subscript) and _is_environ(inner.value):
            return True
    return False


def _env_names_the_client_reads() -> set[str] | None:
    """The env-var names `tortoise/mcp_client.py` actually READS, or None.

    A DECLARED name is not a read (#10727 review, F2): `_ENV_KEY_NAMES` is a
    tuple, so a pin that asks whether a name is IN it stays green when the loop
    consuming that tuple is deleted — the client would then read no environment
    at all, which is exactly the empty-Bearer class #5153 exists to catch (the
    tuple survives as a dead constant AND as prose in the module's docstrings).

    Parsed, not pattern-matched, so a later refactor that grows the tuple or moves
    the loop keeps working, while the property under test stays the real one: a
    declared name is looked up in `os.environ`.
    """
    source = (REPO_ROOT / "tortoise" / "mcp_client.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    declared: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if not any(isinstance(t, ast.Name) and t.id == "_ENV_KEY_NAMES"
                   for t in targets):
            continue
        value = node.value
        if isinstance(value, ast.Tuple):
            declared = {
                element.value for element in value.elts
                if isinstance(element, ast.Constant) and isinstance(element.value, str)
            }
    if not declared:
        return None
    reads = any(
        isinstance(node, ast.For)
        and isinstance(node.iter, ast.Name)
        and node.iter.id == "_ENV_KEY_NAMES"
        and _loop_reads_environ(node)
        for node in ast.walk(tree)
    )
    return declared if reads else None


def test_5153_served_document_pins_the_canonical_mcp_key_variable():
    """#5153: the served document's Pi step must name the CANONICAL key variable
    and say that a differently-named MCP credential — a #3615 policy difference,
    not a defect — must therefore be aliased. Without that, a profile following
    the doc exports only its own name, the committed `.mcp.json` expands
    `${TORTOISE_API_KEY}` to an empty Bearer, and the server answers 401 while
    the CLI/SDK/static-auth paths go unauthenticated: the config-name mismatch
    this issue reports.

    ⚠️ What this pin does NOT claim, because it is no longer true: the document
    must not say `TORTOISE_API_KEY` is "the only name ANY code path reads". The
    MCP HTTP client accepts the `TORTOISE_MCP_API_KEY` alias (proposed by #5200,
    landed by the #10605 fix — see the assertions below), so an absolute "only
    one name" would be false, and the alias disclosure is what keeps a reader
    from renaming the key and getting an empty Bearer.

    The variable name is pinned against the CODE, not against itself — every
    `Bearer ${VAR}` snippet the document carries must name a variable that
    `tortoise/mcp_client.py` (the MCP HTTP client) actually READS, so a snippet
    repointed at an unread name — exactly the issue's proposed "fix option 1"
    — fails here. "Reads" is proved by parsing the client's resolution loop, not
    by finding the name somewhere in the file (see `_env_names_the_client_reads`).

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
    # The claim is "canonical, and the name every recipe uses" — not "the only
    # name any code path reads". The MCP HTTP client ALSO accepts
    # `TORTOISE_MCP_API_KEY` as an alias (the fleet host exports it, #10605), so
    # an absolute "only one name" would be false, and the disclosure is what keeps
    # a reader from renaming the key and getting an empty Bearer (#5153).
    #
    # PROVENANCE (#10727 review, F3): #5200 is still OPEN and only PROPOSED the
    # alias — no code path on main read the name before this PR — so "since
    # #5200 the client accepts it" is false: the alias is landed by the #10605
    # fix. Pinning it here is what stops a later reader from restoring the tidy
    # but wrong sentence, because that is exactly the shape a doc review would
    # "correct" back (#5153's own rule: the document must not credit a change
    # that has not landed).
    assert "is the canonical name, and the one every recipe in this" in skill, (
        "the Pi step must state which name is canonical for every recipe")
    assert ("additionally accepts `TORTOISE_MCP_API_KEY` as an alias") in skill, (
        "the alias the MCP HTTP client accepts must be disclosed, or the "
        "'only one name' reading returns (#5200, #10605)")
    assert "#5200 *proposed*" in skill, (
        "the alias's provenance must credit #5200 with PROPOSING it and the "
        "#10605 fix with landing it — #5200 is still open (#10727 review F3)")
    assert "Since #5200" not in skill, (
        "the false provenance returned: #5200 did not land the alias, it "
        "proposed it while open (#10727 review F3)")
    assert 'export TORTOISE_API_KEY="$TORTOISE_MCP_API_KEY"' in skill, (
        "the Pi step must show the alias a separately-named MCP credential needs")

    # BOTH indirection forms, because the document's ONE Bearer snippet is the
    # Cursor one and `.cursor/mcp.json` expands `${env:NAME}` — the PLAIN
    # `${NAME}` form is Claude Code's `--header` and is NOT expanded by Cursor
    # (cursor.com/docs/mcp; the same split is already recorded below, at the
    # `CURSOR_MCP_CONFIG_ENV` / `PI_MCP_CONFIG_ENV` wizard pins). The `env:`
    # prefix is optional in the EXTRACTION only — the pin immediately below
    # still grades which form Cursor's snippet must carry.
    # What EMPTIED `referenced` here was #14607 REMOVING the separate plain-form
    # `.mcp.json` snippet (`4b2fba3e^:tortoise/onboarding/SKILL.md:415`, `"Bearer
    # ${TORTOISE_API_KEY}"`); the Cursor `env:` row predates that change — it
    # arrived with `24e8388c5f316`, and `01b78913cde4` only dropped
    # its `"type": "http",`. So the red is LOCATOR DRIFT on a legitimate
    # rewrite, NOT a guard that was born broken: this file PASSES 42/42 at
    # `4b2fba3e^`, where that plain-form snippet still satisfied the un-widened
    # regex, and reds only from `d20162d21830` onward. Do not "restore" the
    # plain snippet to satisfy a tighter locator: in a JSON header that form
    # resolves once at process start to an EMPTY `Authorization: Bearer`
    # (tortoise/onboarding/SKILL.md:411-414) — the empty-bearer class this guard
    # exists to prevent.
    referenced = set(
        re.findall(
            r'"Authorization":\s*"Bearer \$\{(?:env:)?([A-Z0-9_]+)\}',
            skill))
    assert referenced, (
        "the served document must carry an env-indirect Bearer snippet")
    assert '"Authorization": "Bearer ${env:TORTOISE_API_KEY}"' in skill, (
        "the Cursor `.cursor/mcp.json` snippet must interpolate `${env:NAME}` — "
        "Cursor does not expand the plain `${NAME}` form in `headers`, so a "
        "snippet repointed at it ships the literal `Bearer ${...}` text and "
        "every request is a 401")
    # ASK THE CLIENT WHAT IT READS, and prove it READS. The precedence loop (a
    # names tuple consulted with `os.environ.get(name)`) has no per-variable
    # literal for a shape-pin to find — pinning one redded a correct fix
    # (#10727) — while "the name merely appears somewhere in mcp_client.py" would
    # pass on a docstring or a comment, which is exactly the #5153 false PASS
    # this guard exists to prevent. A `_ENV_KEY_NAMES` MEMBERSHIP check is the
    # same hole one level up: the tuple is a DECLARATION, so deleting the read
    # and leaving a dead constant stays green (#10727 review, F2). What this
    # asserts instead is a READ: a loop over the declared names whose body
    # consults `os.environ`.
    read_names = _env_names_the_client_reads()
    assert read_names is not None, (
        "`tortoise/mcp_client.py` no longer READS `_ENV_KEY_NAMES` from the "
        "environment — the tuple is a dead constant, so every name the served "
        "document tells an operator to export is one nothing looks up (the #5153 "
        "empty-Bearer defect). This pin checks for a READ, not a mention")
    unread = sorted(v for v in referenced if v not in read_names)
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
    # ⚠️ RE-KEYED at #12915 (#4282 Phase 2, lane B): the document now teaches the
    # CONTAINER spelling because `tortoise_mitigate_operator` was retired-forward
    # in that batch — it still answers through the #3883 shim, but a served
    # onboarding document must teach the ADVERTISED door. The invariant is
    # unchanged (#8169: "the dampener branch must survive the change that ADDS a
    # direction"); only the name it is stated with follows the surface, and
    # asserting the retired name here would pin the document to stop teaching the
    # door the surface advertises.
    assert "tortoise_adjust_relationship(action='mitigate')" in step, (
        "step 5 must teach the advertised dampener door "
        "(`tortoise_adjust_relationship(action='mitigate')`, which absorbed "
        "`tortoise_mitigate_operator`) — a document that teaches only the "
        "retired name sends the reader through the #3883 shim")
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
    # #4282: the structure check is reached through the `graph_overview` container's
    # `structure_check` SECTION. The door `tortoise_check_structure` deliberately stays
    # LIVE (it carries the #8482 wrap-envelope repair a `-> Any` container cannot), but
    # the protocol names the container because a tool-grant client must be able to follow
    # it, and the container is the name this family consolidates onto.
    #
    # #10914 review P3: requiring only the container NAME was weaker than the assertion it
    # replaced — any mention of `tortoise_graph_overview` satisfies it, including the
    # section="health" verification sentence, so nothing failed if the protocol stopped
    # performing the structure check at all. The SECTION is what performs it, so pin the
    # section: `section="structure_check"` is the call the protocol actually makes.
    # The loop names only tokens whose presence can VARY. `tortoise_graph_overview`
    # is deliberately NOT a member: the decide section already carries it via the
    # `section="structure_check"` line below, so a membership assertion on the
    # container name is a no-op that no revert can redden (PR #11719 review F1).
    # The health verify is guarded by the explicit SECTION assertion below, which
    # DOES redden — measured: the pre-fix decide section contains 0 occurrences of
    # `tortoise_graph_overview(section="health")`.
    # #11609 P2: the ranking arm moved. `tortoise_compute_confidence` is retired into the
    # confidence container's `recompute=True` arm, so the protocol names the container —
    # and the ARM is pinned separately below, because naming the container alone gets a
    # token list no different from one that asks for the stored calibration view.
    # #11609 P7: the SAME move for the wiring door. `tortoise_create_operator` was retired
    # forward onto `tortoise_link_entities` in the batch that registered it, so the pin
    # follows the door — and the ARM is pinned below for P2's reason: the container is one
    # linking verb whose RELATION decides the writer, and a list of targets is the
    # part/whole fan-out `create_operator` had (one operator over N targets). Naming the
    # container without the list would admit a protocol that cannot perform that fan-out.
    # Measured at this head: the door is still SERVED (forwarded), so the `missing` sweep
    # above cannot catch a stale name; this pin is that sweep's only guard against one.
    # Canonical-only staleness is caught separately by
    # `test_m8_deploy_mirror_matches_canonical`, and a revert of BOTH copies by
    # `test_1652_served_skills_name_only_advertised_tools` — neither of those keeps the
    # protocol teaching the part/whole fan-out, which is what the arm pin below does.
    for required in ("tortoise_check_confidence", "tortoise_link_entities"):
        assert required in tokens, f"decide protocol must use {required}"
    assert "`target_ids=[…]` selects the operator writer" in decide_section, (
        "the decide protocol must wire through `tortoise_link_entities`'s "
        "`target_ids=[…]` arm — that arm is the part/whole fan-out the retired "
        "`tortoise_create_operator` had (one operator over N targets), and the "
        "plain two-endpoint `link_entities` call does not reproduce it")
    assert "check_confidence(recompute=True)" in decide_section, (
        "the decide protocol must RANK through the confidence container's recompute "
        "arm — the container's default answers the stored calibration view, so a "
        "protocol naming it without the arm presents no ranking at all")
    assert 'tortoise_graph_overview(section="health")' in decide_section, (
        "the decide protocol must verify the live connection through the "
        "container's health section — the retired `tortoise_health` name is not "
        "in `tools/list`, so the verify could never succeed")
    assert 'tortoise_graph_overview(section="structure_check")' in decide_section, (
        "the decide protocol must run the structure check through the container's "
        "structure_check section — naming the container alone does not perform it")


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

# ⛔ THE CUE HAS TWO SPELLINGS IN THIS REPO'S OWN PINNED DOC, and only one was
# graded. `docs/quickstart-cloud.md:135` says "Restart YOUR client" while `:80`
# says "restart THE client", so the second restart instruction in the very file
# this pin protects was never a cue at all — inverting it ("look for the
# `tortoise_*` tools and restart the client") shipped GREEN while
# `_restart_cue_order_violation`'s own docstring claims it grades "EVERY CUE
# PARAGRAPH" (#14679 review at `b5b1ee005941`, measured). `your|the` is the whole
# definite-article variation; a doc that drops the possessive is not a different
# instruction, it is the same one spelled the way the docs already spell it.
_RESTART_CUE_TEXT = r"restart\s+(?:your|the)\s+client"
# ⛔ THE CUE IS FOUND CASE-INSENSITIVELY. Every form is IGNORECASE because "case
# carries no meaning in this prose", but the cue itself was matched with `in`, so
# a reword that keeps the required ORDER and merely lower-cases it mid-sentence
# ("Once the config is written, restart your client, then confirm the tools are
# actually there") failed the "must keep its restart cue" assert (measured) and
# silently stopped the paragraph being graded at all.
_RESTART_CUE_RE = re.compile(_RESTART_CUE_TEXT, re.IGNORECASE)


def _cue_start(text: str) -> int:
    """Index of the cue in `text`, case-insensitively; -1 when absent."""
    match = _RESTART_CUE_RE.search(text)
    return -1 if match is None else match.start()


def _cue_end(text: str) -> int:
    """Index just past the cue in `text`; -1 when absent.

    ⛔ NOT `_cue_start(text) + len(_RESTART_CUE)`. The cue is a PATTERN
    (`restart your|the client`), so a constant length slices the post-cue window
    at the wrong offset for one of the two spellings — it would leave ` client`
    at the head of the window, or eat into it. Measured at `b5b1ee005941`: the
    `the` spelling is the one that exposed it.
    """
    match = _RESTART_CUE_RE.search(text)
    return -1 if match is None else match.end()

# ── The restart-order pin's GRADED inversion forms — a CLOSED, MEASURED set ───
# The pin exists for one defect (#3232, #14676): the client reads its MCP config
# at START, so copy that tells the user to confirm the tools BEFORE the restart
# sends them looking for tools the running session cannot have loaded.
#
# A prose denylist is open-ended by nature, so this set is deliberately finite
# and every member was MEASURED against this file's own history: each form was
# observed GREEN on the pin it replaces before the form was added (a claim wider
# than its measurement is what #14676's review found), and the config-scope
# exclusion in `_confirmation_scopes_the_config` was observed as a FALSE RED.
# Phrasings outside this set are OUT OF SCOPE BY DESIGN — an unbounded pattern is
# what produced both defects this pin was re-cut to fix, and a rule that reds
# legitimate copy is how a pin gets deleted (#3429).
#
# ⛔ EVERY FORM IS CASE-INSENSITIVE. A clause that starts after a separator
# begins capitalised, and the `^`-anchored adversative form could otherwise
# NEVER fire on ordinary prose: "Delete the old entry. But first confirm the
# tools are actually there. Restart your client, …" was GREEN while its
# lower-case twin was RED — measured, before the flag went on. Form (f) cannot
# work without it at all, being a pre-cue imperative that always begins
# capitalised ("Confirm the tools are actually there. Restart your client, …").
# The predicate stems carry their OWN morphology: `verify\w*` (an earlier cut)
# cannot match "verified" (y→i), and `ensure\w*` cannot match "ensuring" (the
# silent e) — both measured as false PASSES of the very form this set grades.
# ⛔ ONE DOC-REFERENCE RULE, USED BY EVERY FORM. A documentation noun after
# `tools`, or a participial doc phrase after it ("the tools listed above", "the
# tools available in this guide"), is a CROSS-REFERENCE, not a presence claim —
# and correct copy that points at the docs went RED in every form that lacked
# this. Two forms carried different, shorter lists, so `… checked the tools
# overview` was GREEN in one and RED in the other, and forms (b)/(c)/(d) had no
# exclusion at all (each measured). The rule is defined once here and appended to
# every form's object — with ONE deliberate exception, form (e), whose own
# comment gives the reason: `listed` is in its presence predicate, which is what
# grades "the tools listed above appear", so exempting doc phrases there would
# delete the row that grades them.
# ⛔ THE WHOLE DOCUMENTED FAMILY, not just the two words the comments happened to
# use. `documentation` — the very word these comments name the category with —
# was MISSING, so "after checking the tools documentation" was RED while its
# `tools reference` twin is a declared-GREEN row (measured), and the same held
# for every other cross-reference noun. A doc is readable without a restart, so
# grading one as an inversion asserts something the copy does not say.
_DOC_NOUN = (r"(?:section|reference|table|guide|docs?|documentation|page"
             r"|overview|summary|readme|appendix|catalog(?:ue)?|manifest"
             r"|inventory|walkthrough|tour|cheat-?sheet|matrix|screenshot"
             r"|diagram|index|chapter|listing)")
_DOC_PARTICIPLE = r"(?:listed|shown|available|described|documented)"
# ⛔ THE LOCATIVE ARM IS `(?:in|on) <determiner> <DOC NOUN>`, NOT THREE PINNED
# PHRASINGS. It used to be `in this guide|in the table|on this page`, so the
# verdict turned on the determiner (`this`→`the`) and on which synonym for "doc"
# the writer picked — the same ONE-WORD class this file has closed repeatedly
# (`first`→`firstly`, `though`→`although`, one apostrophe, one glyph). Measured
# at this head: `in this guide` GREEN, its `the` twin RED, and `on the page` /
# `in the section` RED. ⛔ The determiner is NOT a gate on the class: `the F`
# picks the unique F and `this F` adds only a salience restriction on top of
# uniqueness (CEUR-WS Vol-1845 paper 5; BUCLD 50) — both are definite references
# to the doc. The NOUN is what decides the class, so the arm is composed from
# `_DOC_NOUN` and covers the whole 25-noun family the arm above was widened for.
# ⛔ A POSSESSIVE IS THE SAME DETERMINER CLASS, and the line below used to admit
# only `this|the` while its own block claimed "the determiner is NOT a gate on the
# class". Measured at #14679's review of `019ac6ff4b1e`: `Confirm the tools are
# described in YOUR guide, then restart your client.` was RED (`in our
# documentation` likewise) while `in the guide` is GREEN — a FALSE RED on real
# copy, which is the direction that gets a pin deleted (#3429).
# ⛔ PINNED BY THE TWO GREEN ROWS BELOW (`in your guide`, `in our documentation`):
# removing the possessives reds BOTH — 2 mismatched of the whole table at
# `5f1c0cbec6cb` — and no other arm
# covers the shape, so this one is load-bearing rather than redundant. (An
# earlier revision of this comment claimed the opposite; it was measured by a
# probe whose replacement string never matched the source, so it proved nothing.)
_DOC_LOCATIVE = r"(?:in|on)\s+(?:this|the|your|our|its|their)\s+" + _DOC_NOUN
# `here|there` stay the ONLY difference between this set and the STRICT one (see
# the copula note below): every other member is unambiguously documentary.
_DOC_PLACE = (r"(?:above|below|earlier|later|here|there|" + _DOC_LOCATIVE + r")")
# ⛔ `here|there` ARE NOT DOC-ONLY PLACES, so the COPULA arm gets its own set.
# `here`/`there` also point at the RUNNING SESSION (`the tools are actually
# there` is the pin's canonical RED row), and once a copula is admitted between
# the object and the participle, `Confirm the tools are available there` — a
# real pre-restart presence claim — was suppressed GREEN (#14679 review cycle 36,
# measured: three shapes went RED→GREEN, and `_DOC_PLACE`'s other members are
# unambiguously documentary). The participle-directly-after-object arm keeps the
# full set: `the tools listed there` is still a pointer at a list on the page.
_DOC_PLACE_STRICT = (r"(?:above|below|earlier|later|" + _DOC_LOCATIVE + r")")
# Appended immediately after the object in a form's pattern.
# ⛔ A COPULA BETWEEN THE OBJECT AND THE DOC PARTICIPLE IS STILL A
# CROSS-REFERENCE: `Check the tools are listed above. Then restart your client.`
# is a pointer at the docs, not a claim about the running session, and it was
# RED — the second lookahead only suppressed the doc phrase DIRECTLY after the
# object (`the tools listed above`), so the `are` spelling slipped through
# (#14679 review cycle 35, measured; pre-existing gap, newly reachable once the
# pre-cue walk-back graded the clause a `. then` cue introduces).
_NOT_A_DOC = (r"(?!\s+" + _DOC_NOUN + r"\b)"
              r"(?!\s+(?:(?:are|is|were|was|be|been)\s+)?"
              + _DOC_PARTICIPLE + r"\s+" + _DOC_PLACE_STRICT + r"\b)"
              r"(?!\s+" + _DOC_PARTICIPLE + r"\s+" + _DOC_PLACE + r"\b)")

# ⛔ THE ADVERB RUN IS DEFINED ONCE, BECAUSE THE ARM USES IT TWICE AND THE TWO
# COPIES DIVERGED. It skips the adverbs that can sit between a predicate and the
# state word without changing the claim: `-ly` covers the open class
# (`temporarily`, `completely`, `seemingly` …), and the closed members cover the
# ones with no `-ly` form.
# ⛔ A NEGATING ADVERB IS NOT ONE OF THEM. `hardly|barely|scarcely|rarely|seldom`
# INVERT the word behind them — "the tools are hardly missing" is a PRESENCE
# claim — so the `-ly` slot excludes them by lookahead rather than admitting them
# and reading the absence word as a bare absence (#14679 review cycle 33,
# measured: adding a bare `\w+ly` slot here turned those four GREEN).
# ⛔ `just|also|first` ARE IN THE RUN. They are focus words, and leaving them out
# flips a declared-GREEN row: "when the tools are just missing" is an absence and
# must stay GREEN (measured). The residual that buys is the other side of the
# same word — "the tools are NOT JUST missing" is also an absence, and it is
# graded RED, because the run then reads `not … missing` as a negated absence
# (a presence claim). Rare copy, and the alternative re-REDs the common one.
_ABSENCE_ADVERB_RUN = (
    r"(?:(?:(?!(?:hardly|barely|scarcely|rarely|seldom)\b)\w+ly"
    r"|longer|yet|still|now|very|so|too|quite|rather|already|just|also|first)"
    r"\s+)*"
)

_CONFIRM_PREDICATE = (
    r"\b(?:confirm|check|verif(?:y|ies|ied|ying)|ensur(?:e|es|ed|ing)"
    # ⛔ `make sure` / `be sure` / `validate` ARE THE SAME PREDICATE, and the
    # closure below was missing the two highest-frequency members of the very
    # vocabulary it claims is closable. `make sure` is not an exotic synonym:
    # this file STATES the equivalence 60 lines down (`make sure to` / `be sure
    # to` are the same instruction) and already admits `make\s+sure\s+to` in
    # form (f)'s modal slot — it simply never admitted it in the OBJECT
    # position, so the declared-RED `Before you restart your client, confirm the
    # tools are actually there.` went GREEN one word over (#14679 review at
    # `1dedd33da07e`).
    # ⛔ THE COUNTS, AND THE RULE THAT MAKES THEM CHECKABLE — substituting the
    # spelling on EACH of the 238 declared-RED rows in `1dedd33da07e`'s table and
    # counting the rows that then grade GREEN: `confirm`→`make sure` **77**,
    # `confirming`→`making sure` **26**, `confirmed`→`made sure` **17**,
    # `confirm|verify`→`validate` **78**. (The figures first written here were
    # inherited from the review's prose and could not be reproduced — the rule was
    # unstated — so they are re-measured here rather than repeated;
    # #14679 review at `fdaaa23d016c` flagged exactly that.) The same rule at THIS
    # head flips 0 rows for all four — the substitution no longer buys anything —
    # and that 0 is EXACT for this closure (`confirmed`→`made sure` measured 4
    # until the FOURTH copy of this list, the observation gate below, was widened
    # too; #14679 review at `5ee54138490e`). The list stays CLOSED — these are
    # members of it, not a new arm.
    # ⛔ `make certain` / `be certain` ARE THE SAME PREDICATE ONE SYNONYM OVER, and
    # leaving them out re-created the very defect this closure was widened for —
    # the comment above claims `make sure`/`be sure` are "the same predicate",
    # and `certain` is that predicate's other spelling. Measured at the review of
    # `fdaaa23d016c`: `Restart your client after making certain the tools are
    # actually there.` was GREEN end-to-end on the real quickstart while its
    # `making sure` twin is a declared-RED row, and because all seven forms route
    # through this constant the gap defeated every one of them at once, the
    # pre-cue (f) and the negated-cue arm included.
    # ⛔ DECLARED RESIDUAL: the COPULA spelling (`once you are sure|certain the
    # tools …`) is deliberately NOT here. It is a different construction (a
    # predicate adjective, not a verb phrase), and admitting a bare `sure|certain`
    # to a list whose members must take the object directly would need its own
    # measurement rather than a synonym slot.
    r"|mak(?:e|es|ing)\s+(?:sure|certain)|made\s+(?:sure|certain)"
    r"|be\s+(?:sure|certain)|validat(?:e|es|ed|ing)"
    r"|see|saw|seen|look|list)(?:ing|ed|s|es|d)?\b"
)

# ⛔ THE OBJECT HAS TWO SPELLINGS IN THIS REPO'S OWN COPY, and only one was
# graded. Every form keyed on the plural `\btools\b`, while
# `docs/quickstart-cloud.md:135` names the thing the user must look at as "the
# client's MCP/tool list" — so the SINGULAR `tool list` escaped all seven forms
# (#14717, measured at `8c3bb0d692`): both
# "Confirm the tool list is complete. Restart your client, …" and
# "Restart your client after confirming the tool list is complete." were GREEN,
# and the second one INVERTS the order this pin exists to enforce — the pin's
# own docs ship the spelling its patterns cannot see. Defined ONCE and appended
# wherever the object is graded, so a third spelling has one home rather than
# nine (nine copies of `\btools\b` is how this gap opened).
# ⛔ THREE BRANCHES, because the plural HEAD alone is not enough where a
# PREDICATE must follow the object: `\btools\b` matches inside `tools list`, but
# form (e) then demands whitespace and finds `list` — so `tools list` (the
# spelling this file's own prose uses for the session's list) needs its own
# branch. `list` is deliberately not a `_DOC_NOUN`: "the tools list" is the
# SESSION's own list, not a page (#14679 review cycle 30, measured).
_TOOLS_OBJECT = (
    r"(?:\btools\b|\btool\s+lists?\b|\btools\s+lists?\b"
    r"|`?tortoise_\*`?|\bthey\b|\bthem\b)"
)
# …and the compiled form, for the ONE site that must LOCATE the object rather
# than match it in sequence: `_confirmation_scopes_the_config` needs its offset
# to decide which side of the object a config noun sits on. Built from the same
# fragment so a third spelling cannot reach one site and miss the other (that is
# how the singular `tool list` was graded by the forms while a config-scoped
# clause naming it was still refused — measured).
_TOOLS_OBJECT_RE = re.compile(_TOOLS_OBJECT, re.IGNORECASE)


def _inversion_form(pattern: str) -> re.Pattern[str]:
    """A graded form. Case carries no meaning in this prose, so every form is
    case-insensitive — see the note above on the `^`-anchored one."""
    return re.compile(pattern, re.IGNORECASE)

# ⛔ `_ADVERBS` — ONE DEFINITION, USED BY FORM (f) *AND* BY FORM (a). It must be
# bound before the first form is built, which is why it lives here rather than
# beside the doctrine comment below; that comment describes THIS constant, and
# its removal measurements are re-taken over the whole table at this head.
# ⛔ THE OPEN `\w+ly` CLASS IS NOT ENOUGH ON ITS OWN, and neither is a closed
# remainder read as `now|still`. `always` was not a member, so ONE inserted
# non-`-ly` adverb re-opened the pre-cue half of this pin — the exact defect the
# `\w+ly` arm exists to refuse — while its `First confirm …` twin is a declared
# RED row (#14679 review, two fresh contexts, measured at `8c96f5de`):
#
#     RED    First confirm the tools are actually there. Restart your client, …
#     GREEN  Always confirm the tools are actually there. Restart your client, …
#     RED    You must confirm the tools are actually there. Restart your client, …
#     GREEN  You must always confirm the tools are actually there. Restart …
#     GREEN  Please always confirm the tools are actually there. Restart …
#
# `_SEQUENCER_FRAGMENT_RE` already lists `always` as a word that "carries no
# imperation", so the two arms of this file disagreed about the same closed
# class. `often` is added with it as the same frequency family.
# ⛔ `never`/`ever` ARE DELIBERATELY NOT MEMBERS, and the first cut of this
# comment wrongly said they were. A NEGATED imperative is not a confirmation —
# `Never confirm the tools are actually there. Restart your client.` is copy
# telling the user NOT to do it, so grading it as a deferred confirmation would
# be a FALSE RED, which is the direction this file treats as the more expensive
# one to add. Only `always` carries rows; `often` is declared in the row-less
# list (measured: removing it alone flips 0 rows), so nothing here is an
# undeclared arm.
# ⛔ `never`/`ever` WERE CLAIMED AND WERE NOT MEMBERS. The note above says the
# frequency family ("often/never/ever") is added; the literal carried `often`
# and `always` only, so `Don't ever restart your client until …` stayed GREEN
# while its bare twin is a declared-RED row (#14679 review cycle 51, measured).
# The claim is now the literal.
_ADVERBS = (r"(?:\w+ly|very|so|too|quite|rather|first|already|just|also"
            r"|now|still|always|often)")
# ⛔ `never`/`ever` MUST NOT BE ADDED TO `_ADVERBS`, and cycle 51 added them —
# which broke the decision recorded two blocks above: `Never confirm the tools
# are actually there. Restart your client.` is copy telling the user NOT to do
# it, and form (f) consumes this class as a LEADING ADVERB RUN BEFORE THE
# PREDICATE, so membership made it a FALSE RED (#14679 review cycle 52,
# measured). The caller that genuinely needs them is the NEGATED-CUE lead
# (`Don't ever restart your client until …`), which is a different position, so
# it gets a caller-local run instead of widening the shared class.
_CUE_LEAD_ADVERBS = (r"(?:" + _ADVERBS + r"|never|ever)")
# ⛔ THE PRE-CUE VERB LIST, ONE DEFINITION — form (f) spelled it twice and a
# THIRD copy sits inside the shared `_CONFIRM_PREDICATE`, which is how the same
# hole opened four times (#14679 review rounds 3-6). Every arm of form (f) reads
# this constant; if a member is added, every arm gains it.
_PRE_CUE_VERB = (
    # ⛔ MORPHOLOGY, NOT A TRAILING `\w*`. This copy spelled `verify\w*` and
    # `ensure\w*`, which — as this very file documents 180 lines up — "cannot
    # match 'verified' (y→i)" and "cannot match 'ensuring'". Measured side by
    # side, it drifted on SIX spellings the other three copies all carry
    # (`verifies|verified|ensuring|validating|made sure|making certain`), so the
    # comment's claim of ONE DEFINITION was false of the code (#14679 review
    # cycle 48). The morphology is now written out.
    # \u26d4 AN EMPHATIC `Do` IS THE SAME ONE-INSERTED-WORD DEFECT, and it defeated
    # form (f) ENTIRELY: `Do make sure the tools are actually there. Restart your
    # client, then run a query.` measured GREEN while the bare `Make sure \u2026`
    # twin is a declared-RED row, and a `Do ` prefix over the declared-RED set
    # flipped 119 rows to GREEN (#14679 review at `f16e32451480`, measured). The
    # file's own `_SEQUENCER_FRAGMENT_RE` already lists `do|does` among the words
    # that "carry no imperation", so the two arms disagreed about one closed class
    # exactly as they did for `always`.
    r"(?:(?:do|does)\s+)?(?:\w+-)?(?:confirm|check|verif(?:y|ies|ied|ying)|ensur(?:e|es|ed|ing)"
    r"|see|look|validat(?:e|es|ed|ing)"
    r"|mak(?:e|es|ed|ing)\s+(?:sure|certain)|made\s+(?:sure|certain)"
    r"|be\s+(?:sure|certain))\w*\b"
)
# (f) The same inversion stated one sentence EARLIER — a PRE-CUE imperative the
#     reader must carry out before restarting:
#     "Confirm the tools are actually there. Restart your client, …".
#     Form (a) cannot see it (it needs a connective) and the post-cue side never
#     reads that sentence, so it was GREEN (measured) until this form and the
#     pre-cue clause in `_restart_cue_order_violation` existed.
#     It is the ONLY pre-cue-only form, and it is held by IDENTITY rather than
#     by an index into `_INVERSION_FORMS`: an index silently re-arms the false
#     RED below as soon as a form is added or removed (measured — inserting a
#     form at that position made "Restart your client. Confirm the tools are
#     actually there." RED again and no table row noticed).
_PRE_CUE_ONLY = _inversion_form(
    # ⛔ `first`/`also`/`just` may sit between the courtesy and the verb, and one
    # of them is the MOST natural English phrasing of the instruction this form
    # exists to catch: "First confirm the tools are actually there. Restart …"
    # was GREEN (measured) while its unadorned twin was RED, and so was
    # "Please first confirm …" — one inserted word defeated the whole form.
    # ⛔ AND A MODAL SUBJECT IS THE SAME ONE-INSERTED-WORD DEFECT, one class over.
    # The predicate (`confirm`) and object are already in the graded vocabulary;
    # only the mood differs, so "You must confirm the tools are actually there.
    # Restart …" stayed GREEN while its `Please`/`First`/`Confirm` twins are
    # declared RED rows (measured, #14679 review at `2ce284961b`, which also
    # measured `You must confirm … , then restart your client` end-to-end on the
    # real doc as a false PASS). `make sure to` / `be sure to` are the same
    # instruction; "after making sure the tools …" used to be a declared residual
    # of form (a) and is now GRADED by it — through the shared
    # `_CONFIRM_PREDICATE`, which `make sure`/`be certain`/`validate` are members
    # of (row `Restart your client after making sure the tools are actually
    # there.`), so the residual wording had to go rather than be restored. What
    # remains genuinely ungraded is the COPULA spelling (`after making certain
    # the tools …`'s `once you are certain the tools …` twin), and that is a
    # predicate-vocabulary gap in `_CONFIRM_PREDICATE`, declared there — not a
    # residual of this slot.
    # ⛔ A LEADING SEQUENCER/ADVERB IS THE SAME ONE-INSERTED-WORD DEFECT as the
    # courtesy and modal slots above, and it was the LAST one left: `Now`/`Then`
    # /`Next`/`Finally`/`Immediately`/`Simply`/`Again`/`Still` in front of the
    # imperative defeated form (f) ENTIRELY — measured at `509d235dcce1`, one
    # leading word flipped **39 of the 40** declared-RED pre-cue rows GREEN, and
    # `Then confirm the tools are actually there. Restart your client, …` passed
    # both doc-level assertions end-to-end on the real `docs/quickstart-cloud.md`
    # — i.e. the whole pre-cue half of this pin, defeated by one word (#14679
    # review cycle 37). The run is capped-free and repeats, and it was measured
    # at **0 flips over all 405 rows** plus the shipped doc before landing.
    # ⛔ A LEADING CONJUNCTION IS THE SAME ONE-INSERTED-WORD DEFECT, and it was
    # still open after the adverb slot closed: `And confirm the tools are actually
    # there. Restart your client, …` and `So confirm …` measured GREEN (and
    # end-to-end on the shipped doc) while `First confirm …` and `But first
    # confirm …` are declared-RED rows. `and`/`so`/`or`/`plus` introduce a
    # sentence exactly as `Now`/`Then` do, so they belong in the same run — and,
    # like the adverb run, they may be followed by the `first` slot
    # (`And first confirm …`). Measured at the review of `5ee54138490e`.
    r"^(?:(?:(?:now|then|next|finally|lastly|immediately|again|simply|still"
    r"|afterwards?|subsequently|additionally|please|kindly"
    r"|and|so|or|plus)\s*,?\s+)+)?"
    # ⛔ THE ADVERB RUN **REPEATS** AND SITS ON BOTH SIDES OF THE SUBJECT, because
    # form (a) does both and a single optional slot defeated one word twice over:
    # `You first must confirm …` (adverb before the subject) and
    # `You must first carefully confirm …` (two adverbs after it) both measured
    # GREEN while the form-(a) twin of the second is RED (#14679 review round 6).
    r"(?:(?:" + _ADVERBS + r")\s*,?\s+)*"
    # ⛔ A MODAL MAY STAND AT THE HEAD OF A SEMI-MODAL CHAIN. The slot admitted the
    # two spellings separately, so `You need to confirm …` is a declared-RED row
    # while `You WILL need to confirm …` measured GREEN — one inserted modal over a
    # row this file pins (#14679 review round 6). English stacks them (Wikipedia
    # `English modal verbs` §11 "Modals at the head of chains", §8.7 "Need"), so
    # the chain is the unit, not the single member.
    r"(?:(?:(?:you|one)(?:\s+|['\u2019](?:ll|d|re|ve)\s+))?"
    # …and the adverb run REPEATS AFTER the subject too: `You first must confirm …`
    # places one between the pronoun and the modal, which the single pre-subject
    # run could not reach (#14679 review round 6).
    r"(?:(?:" + _ADVERBS + r")\s*,?\s+)*"
    r"(?:(?:will|would|should|may|might|could|can|must|shall)\s+"
    # ⛔ AND THE RUN REPEATS AT THE CHAIN BOUNDARY. The adverb slots sit around the
    # chain but not INSIDE it, so `You will FIRST need to confirm …` — the most
    # natural placement of `first` in this instruction — was GREEN while its
    # adverb-free twin is a declared-RED row: the same one-inserted-word defect,
    # one position further out (#14679 review round 7). `then`/`next` are in the
    # slot because they escape it identically (measured).
    r"(?:(?:(?:" + _ADVERBS + r")|then|next)\s*,?\s+)*)?"
    # ⛔ THE SEMI-MODAL'S OWN `to` IS A POSITION TOO. The adverb runs sit around and
    # at the boundary of the chain, so `You will NEED FIRST TO confirm …` was still
    # GREEN over a declared-RED twin — the same one-inserted-word defect, one
    # position further in (#14679 review round 8). A semi-modal therefore carries
    # its own run before `to`.
    r"(?:(?:need|have|want)\s+(?:(?:" + _ADVERBS + r")\s*,?\s+)*to"
    r"|be\s+able\s+to|be\s+going\s+to|going\s+to|must|should|can"
    # \u26d4 THE PERIPHRASTIC MODALS ARE THE SAME INSTRUCTION, and the first cut of
    # this slot carried only the auxiliaries. `You are required to confirm the
    # tools are actually there` measured GREEN while `You must confirm \u2026` is a
    # declared-RED row, and so did 14 more spellings a doc might plausibly use
    # (`you've got to`, `you're supposed/expected/advised/meant/obligated to`,
    # `you've to`, `one has to` \u2026) \u2014 #14679 review at `eeb42a4f`, measured:
    # 15 of 18 constructed periphrastic spellings graded GREEN. `be <participle>
    # to` is one construction, so it is graded as one arm rather than by
    # enumerating the participles one review at a time; `have to` and
    # `have got to` differ only by a filler `got`, which is the same
    # ONE-INSERTED-WORD defect this file closes everywhere else.
    r"|(?:(?:are|is|am|be|will|would|should|may|might|can|could|['\u2019]re)\s+)*"
    r"(?:required|supposed|expected|advised|meant|obligated|allowed|asked)\s+to"
    r"|(?:have|has|['\u2019]ve)\s+(?:got\s+)?to|got\s+to"
    # ⛔ `had better` IS THE SAME MODAL, AND THE FILE SAYS SO TWICE. This form
    # omitted it while `_NEGATED_CUE_LEAD_RE` admits `\b(?:had|'d|\u2019d)\s+better\s+not`
    # and its comment declares "`ought` AND `had better` ARE THE SAME LEAD" —
    # two copies of one class disagreeing, which is this file's most repeated
    # defect: `You had better confirm the tools are actually there. Restart …`
    # was GREEN while its own negation is a pinned RED row (#14679 review round 8).
    r"|had\s+better|better|could|will|shall|may|might|ought\s+to|would)\s+"
    r"|be\s+sure\s+to\s+|make\s+sure\s+to\s+)?"
    # ⛔ THE ADVERB SLOT SITS ON **EITHER SIDE** OF THE MODAL — form (a) already
    # solved this (`(?:_ADVERBS)\s+)*` both sides) and (f) was the outlier, so
    # moving one word defeated the whole pre-cue half again:
    # `And you should first confirm the tools are actually there. Restart …` and
    # its conjunction-free twin measured GREEN while `You must confirm …` is RED
    # (#14679 review at `019ac6ff4b1e`).
    # ⛔ AND THE SLOT IS `_ADVERBS`, NOT A COPY OF ITS FIRST THREE MEMBERS. The
    # first fix spelled `first|also|just` inline while its own comment said form
    # (a) had solved this — so the natural placement of ANY `-ly` adverb was still
    # a false PASS: `You must carefully confirm the tools are actually there.` and
    # `First, carefully confirm …` both measured GREEN over declared-RED twins
    # (`You must confirm …`). A copy that cites the original is exactly how the
    # confirmation list drifted four times; this slot reuses the constant (#14679
    # review round 5).
    r"(?:(?:" + _ADVERBS + r")\s*,?\s+)*"
    r"(?:"
    # (f.1) `list` takes the tools THEMSELVES as its object, so there is no
    # document it could be pointing at and no predicate to require:
    # "Please list the tools you see" is an instruction about the session.
    # ⛔ THE OBJECT SLOT MUST SURVIVE THE REPO'S OWN SPELLING. The determiner was
    # pinned to `the|all|your` immediately before `tools`, so the quickstart's
    # `\`tortoise_*\` tools` defeated it: "Please list the `tortoise_*` tools you
    # see" was GREEN while its plain twin is a declared RED row (measured) — the
    # same one-inserted-word defect the adverb slot above records as fixed.
    # …and the OBJECT ITSELF is the docs' two spellings, not the plural alone —
    # `_TOOLS_OBJECT` is what grades `tool list` here (#14717). The two arms are
    # independent: the modifier slot admits `\`tortoise_*\`` before EITHER
    # spelling, and the object fragment admits the singular head.
    r"list\w*\s+(?:the\s+|all\s+|your\s+)?"
    r"(?:[\w`*_.\u2019-]+\s+){0,2}?" + _TOOLS_OBJECT
    + _NOT_A_DOC
    + r"|"
    # (f.2) every other verb: the object must be a PRESENCE PREDICATE on
    # `tools` — "the tools ARE actually there", "the tools appear", "the tools
    # list IS complete". ⛔ THE PREDICATE IS THE MECHANISM, not a decoration: a
    # DOCUMENTATION NOUN IS NOT ONE, so "See the tools overview for the full
    # list" carries no claim about the running session and must stay GREEN.
    # Without this arm the form graded a bare `tools`, and enumerating the
    # documentation nouns that must be let through was measured to fail —
    # `overview`, `summary`, `readme`, `appendix`, `index`, `chapter`,
    # `diagram`, `screenshot`, `catalog(ue)`, `manifest`, `documentation`,
    # `tour`, `walkthrough`, `cheatsheet`, `cheat-sheet`, `matrix` and
    # `inventory` were ALL RED on "Check the tools <noun> for the full tool
    # list" (18 of 24 probed), each one a refusal of correct copy — the failure
    # mode that gets a pin deleted (#3429). A noun list cannot be completed; a
    # predicate list can, because a presence claim has a closed vocabulary.
    # ⛔ A HYPHENATED PREDICATE IS THE SAME PREDICATE. `double-check` / `re-verify`
    # are the docs' own register and this verb list admitted only the bare form,
    # so "Please double-check the tools are actually there. Restart …" was GREEN
    # while its `check` twin is a declared RED row — the same lesson form (a)
    # already carries as `(?:\w+-)?` (#14679 review cycle 29, measured).
    + _PRE_CUE_VERB
    # …it is the SAME constant as (f.1) and `_CONFIRM_PREDICATE`, not a second
    # spelling: this copy kept the pre-cue sentence GREEN after the shared
    # constant was widened (`Make sure the tools are actually there. Restart your
    # client, then run a query.` — measured, #14679 review at `1dedd33da07e`).
    + r"[^\n]*?"
    + _TOOLS_OBJECT
    # …and a documentation noun immediately after `tools` is not a presence
    # claim even when a copula follows it ("Ensure the tools table is up to
    # date" — the one shape the predicate arm alone would still refuse). The
    # predicate arm above is what carries the general case; this list only
    # guards the copula-after-a-doc-noun shape, so it stays small on purpose.
    + _NOT_A_DOC + r"(?:\s+[\w'’-]+){0,3}\s+"
    r"(?:are|is|was|were|be|been|appear|appears|show|shows|listed|visible"
    r"|available|loaded|enabled|present|running|live|up|there|exist|exists"
    r"|work|works|load|loads|connect|connects|respond|responds|import"
    r"|see|saw|seen|have|has|had)\b"
    + r"|"
    # (f.3) AN ELLIPTICAL PRESENCE CLAIM ON THE CLIENT'S OWN LIST. Every arm above
    # requires a predicate WORD after the object, so the shipped sentence
    # reordered — `Confirm the tools in the client\u2019s MCP/tool list, then
    # restart your client.` — was GREEN: `in the client's MCP/tool list` is a
    # locative with the predicate elided, which is how a real doc writes it
    # (`docs/quickstart-cloud.md:135` names the object of the check as
    # "`tortoise_*` in the client's MCP/tool list"). The locative is SESSION-side,
    # not documentary: `list` is deliberately not a `_DOC_NOUN`, so this arm adds
    # no doc-reference suppression (#14679 review round 6).
    + _PRE_CUE_VERB
    + r"[^\n]*?"
    + _TOOLS_OBJECT
    + r"\s+(?:in|on)\s+(?:the\s+|your\s+|our\s+)?client['\u2019]s\b"
    + r")"
)

# Prefixes that decorate a CLAUSE without being part of it: separators, brackets
# and MARKDOWN emphasis / quote / list markers. ⛔ The pre-cue side stripped only
# whitespace, so a bolded, italicised, code-spanned or blockquoted imperative
# never reached `_PRE_CUE_ONLY`'s `^` anchor and shipped GREEN —
# `**Confirm the tools are actually there.** Restart your client, …` is the exact
# defect #14676 exists to stop, and it was a FALSE PASS until this set existed
# (measured, with its `*` / backtick / `>` / `-` twins).
#
# ⛔ APPLIED TO EACH CLAUSE, NOT TO THE STATEMENT — and that is a MEASURED choice,
# not a preference. The same strip on `pre_statement` was tried first and is DEAD
# CODE: removing it left every row green (measured: 120 passed, 0 failed), because
# only the LAST clause is graded and the clause strip already reaches its leading
# decoration. An unpinned second mechanism for one behaviour is how a reader ends
# up believing two arms are load-bearing when only one is, so it is not here.
# Deliberately a SEPARATE set from the post-cue `lstrip`, which is pinned by rows
# of its own — widening that one would move behaviour this fix has no reason to
# touch.
# ⛔ A MODAL BETWEEN THE SUBJECT AND THE PREDICATE IS THE SAME ONE-INSERTED-WORD
# DEFECT this file already closed twice — in form (f) ("a MODAL SUBJECT is the
# same one-inserted-word defect, one class over") and in the sequencer-fragment
# test ("one inserted word re-opened the false PASS"). Form (a) never got it: its
# slot went `you … adverbs*` straight into `_CONFIRM_PREDICATE`, so `can` /
# `should` / `are able to` were in neither slot and the match never started
# (#14679 review cycle 40, measured on the shipped pin: `Restart your client
# once you can confirm the tools are actually there` was GREEN while its
# modal-free twin `after you confirm …` is a declared RED row — the canonical
# inversion, admitted one word over).
# ⛔ THE SET IS MEASURED, AND TWO OF ITS CLASSES ARE DELIBERATELY SMALL.
#   * `could|would|shall|may|might` are NOT here: each flips 0 rows and none is
#     natural copy in this construction ("after you could confirm the tools"
#     reads as a hypothetical, not an instruction), so they are the same dead
#     weight the file deletes elsewhere.
#   * The ability subject is `are` only, plus the BARE `able to` that the
#     contracted subject needs. `is|am|be|was|were` are unreachable: the subject
#     slot admits only implied / `you` / `you have` / `having`, so nothing can
#     agree with them (measured — each flips 0 rows).
# ⛔ AND THE CONTRACTION MUST BE ADMITTED ON BOTH SIDES OF IT. `you are able to
# confirm …` was a declared RED row while its `you're` / `you’re` twins were
# GREEN — ONE APOSTROPHE bypassed a row this arm exists to hold, which is the
# same defect the file already states for `you've` ("The auxiliary is spelled
# out AND contracted"). The `'re` arm below is that fix; the bare `able to` here
# is the other half of it (`you're` + `able to`).
_MODAL_TO_VERB = (
    # ⛔ THE BARE COPULA BELONGS HERE, and it was the ONE slot that made the
    # completive above REACHABLE ONLY THROUGH ITS CONTRACTION. `you're done
    # confirming` matched (the subject slot admits `you(?:'(?:ve|re)|…)`) while
    # `you are done confirming` had no path — `_MODAL_TO_VERB` admitted
    # `are\s+able\s+to` but not `are` alone — so the verdict turned on the
    # apostrophe, which is the exact asymmetry the `are|were` doctrine in this
    # form's own comment forbids (`… after you've verified …` GREEN vs the
    # uncontracted twin RED, measured). Measured at the review of `fdaaa23d016c`:
    # `Restart your client after you are done confirming the tools are actually
    # there.` was GREEN while the `you're` twin is a declared-RED row, and forward
    # copy that uses the same copula (`after you are back, then confirm …`,
    # `after you are done with setup, then confirm …`) stays GREEN because a
    # confirmation predicate must still follow.
    r"(?:can|should|will|must|are|were"
    r"|are\s+able\s+to|able\s+to)"
)

# An adverb run that may sit EITHER side of the modal. The slot used to be glued
# to the predicate (`_MODAL_TO_VERB\s+` then straight into `_CONFIRM_PREDICATE`),
# so one adverb inserted between them re-opened the exact false PASS the modal
# slot closes: `once you can actually confirm …` was GREEN while both
# `once you can confirm …` and `once you actually confirm …` are RED (measured).
# ⛔ `now` AND `still` ARE ADVERBS IN THIS FILE ALREADY — `_ABSENCE_ADVERB_RUN`
# lists both — and leaving them out of THIS run was the same one-word gap
# `following|upon` and `file` were: `after you have now confirmed the tools are
# actually there` and its `you've` contraction measured GREEN while the bare twin
# is a declared-RED row (measured at this head). Neither word changes the sense
# of `after`/`once`, so they order nothing and must not defeat the form. They are
# BARE (non-`-ly`) time adverbs, which is why the `\w+ly` arm never reached them
# (#14679 review at `fdaaa23d016c`).
# ⛔ `_ADVERBS` IS DEFINED ABOVE THE FIRST FORM THAT USES IT (see the `rather`
# block just above `_MODAL_TO_VERB`, which describes this constant) so
# form (f) can REUSE it instead of copying its members — one definition, never a
# copy. A copy that cites the original is how the confirmation list drifted four
# times; the adverb slot drifted the same way (#14679 review round 5).
# ⛔ `rather` IS AN ALLOW-LIST MEMBER, and it is DECLARED here rather than
# dropped as dead weight: mutating it out flips 0 rows (measured at `5f1c0cbec6cb`), but
# removing an allow-list adverb does not close a hole — it risks a future FALSE
# RED on copy this pin has simply never seen (`… after you have rather confirmed`
# is not a sentence a doc ships). ⛔ IT IS NOT `page`'s STANDING ANY MORE: this
# comment used to cite `page`'s 0-row measurement as the precedent, and `page` now
# flips 1 row (`Confirm the tools are shown on the page, then restart your
# client.`), so the precedent is stale — `page` is a PINNED allow-list term, and
# `rather` is the declared-0 one. A citation to another arm's measurement rots the
# moment that arm gains a row; cite the rule, not the neighbour (#14679 review at
# `019ac6ff4b1e`).

_PRE_CUE_PREFIX = " \t,.;:—–-…('\"`*>#_[]{}"

# ⛔ A MARKDOWN LIST MARKER IS NOT PART OF THE CLAUSE, and an ORDINAL one cannot
# be reached by `_PRE_CUE_PREFIX`, which is a character set: `lstrip` on `(1)`
# leaves `1)`, and on `1)` leaves `1)`. The leftover reached form (f)'s `^` anchor
# and defeated it, so the pre-cue half was blind to the whole ordinal family while
# the dash bullet (which `_PRE_CUE_PREFIX` *does* carry) was graded:
#
#     RED    -  Confirm …\n   - Restart …      (control)
#     GREEN  1) Confirm …\n   2) Restart …     (measured)
#     GREEN  (1) Confirm …\n  (2) Restart …    (measured)
#
# Stripped as a MARKER (`marker` + whitespace), so a bare number in prose is
# untouched.
_PRE_CUE_LIST_MARKER_RE = re.compile(
    r"^(?:[-*+>]|\(\d{1,2}\)|\d{1,2}[.)])\s+")


_INVERSION_FORMS = (
    # (a) a connective whose own clause carries a CONFIRMATION PREDICATE acting
    #     on the session's tool list, with the subject IMPLIED, "you", "you
    #     [have]" or "having":
    #     "Restart your client after confirming the tools are actually there",
    #     "… after you have verified the tools …", "… after you've checked the
    #     tools …". A DIFFERENT subject ("after we confirm the tools …") and the
    #     PASSIVE ("after the tools have been confirmed present") are out of
    #     scope by design, as is any other rephrasing — the comment claims the
    #     subject group the regex actually admits. (The "once the tools are
    #     listed" shape is form (e)'s, not this one's.)
    #     The predicate must sit in the connective's OWN clause: a verb merely
    #     reached later is not the condition the restart is deferred to, so
    #     "… after adding the server so you can see the tools", "… if you cannot
    #     see the tools", "… once the tools stop appearing" and "… after
    #     upgrading then confirm the tools …" are NOT graded (each measured a
    #     FALSE RED on the wider first cut of this form).
    #     The auxiliary is spelled out AND contracted: "… after you've verified
    #     the tools …" was GREEN (measured) while the uncontracted twin was RED.
    #     One inserted ADVERB between the auxiliary and the predicate defeated
    #     the whole form — "… after you have first verified the tools …" was
    #     GREEN (measured) while its bare twin is a declared RED row, the same
    #     one-inserted-word class `c30589f4df458` closed one form over. The slot
    #     admits ANY `-ly` adverb plus a CLOSED list of bare ones (`_ADVERBS`), and
    #     repeats, because a MANNER adverb does not change the connective's
    #     verdict — a closed `-ly` list only re-opened the hole for `previously` /
    #     `recently` / `thoroughly` / `also already` (each measured GREEN with the
    #     closed list). The bare list is the part that has to be by enumeration,
    #     and every gap in it is a one-word false PASS: `first` was the first such
    #     gap, and `now` / `still` the last (measured — see `_ADVERBS`).
    #     ⛔ THE CONDITIONAL ARMS ARE GATED ON THE VERB'S CLASS, NOT ON AN ADVERB
    #     LIST. "if you ONLY see the tools after a second restart" describes the
    #     SYMPTOM that motivates a restart, and this table already declares
    #     "Restart your client if you cannot see the tools in the list" GREEN — a
    #     tool-observation CONDITION is expressly not an inversion here. An
    #     unfiltered `\w+ly` reddened `only` / `barely` / `rarely` / `mostly` /
    #     `lately` copy (each measured).
    #     ⛔ BUT AN EIGHT-WORD ADVERB BLACKLIST WAS THE WRONG SHAPE, twice over.
    #     It was too wide (the lookahead sat on the whole adverb GROUP, so any
    #     blacklisted word made the form give up entirely and
    #     "when you have only recently CONFIRMED the tools are actually there" —
    #     a declared-RED inversion — went GREEN), and too narrow (a frequency
    #     adverb outside the eight, or an observation verb with a complement that
    #     is not a doc noun, was still graded). What actually distinguishes the
    #     two readings is the VERB: `see` / `look` / `list` name an observation of
    #     the running session, and everything `_CONFIRM_PREDICATE` admits beyond
    #     them names a confirmation the user performs. So the gate is a lookahead
    #     on the observation verbs, and the adverb list is gone.
    #     ⛔ AND IT IS SCOPED TO THE CONDITIONAL CONNECTIVES (`if|when`), because
    #     there the adverb changes the sense of the CONDITION; under `after|once`
    #     the connective itself already fixes the order and no adverb can undo it
    #     (a blanket exclusion lost "after you have only recently confirmed the
    #     tools are actually there", measured).
    #     ⛔ A HYPHENATED PREDICATE IS THE SAME PREDICATE: "double-checked" was
    #     GREEN while "checked" is a declared RED row (measured).
    #     ⛔ AND A DOC CROSS-REFERENCE IS NOT A PRESENCE CHECK, the rule form (f)
    #     already carries ("the noun must name the RUNNING SESSION's tools").
    #     SCOPE, stated because it is PARTIAL and measured: the lookahead
    #     disqualifies a `tools` occurrence a documentation noun follows
    #     IMMEDIATELY, and lazy matching lets a LATER `tools` in the same clause
    #     re-anchor — so "after checking the tools reference for the full list of
    #     tools" is still RED. That residual is shared with form (f)'s own
    #     lookahead and is not claimed away here.
    #     ⛔ A COMMA-WRAPPED adverb is OUT OF SCOPE BY DESIGN, and that is stated
    #     rather than hidden: `… after you have, first, verified the tools …` is
    #     GREEN, because post-cue clauses are split on `,` BEFORE the forms are
    #     applied, so the auxiliary and its predicate never share a clause. The
    #     `,?` this slot used to carry was unreachable for that reason and has
    #     been dropped rather than left looking like an arm (measured: removing
    #     it changed no row). Grading it would mean re-joining clause fragments
    #     around a comma, which is a different (and much wider) rewrite than
    #     this pin claims.
    #     ⛔ A COMPLETIVE BETWEEN THE SUBJECT AND THE PREDICATE IS THE SAME SLOT,
    #     and it was in NEITHER the adverb run nor the modal slot. `done` /
    #     `finished` / `through` name the completion of the very confirmation the
    #     connective defers to, so they order nothing — but they defeated the
    #     whole form, which is the same one-inserted-word class every other slot
    #     on this form was hardened against (#14679 review cycle 42, measured):
    #
    #       RED    Restart your client once you're confirming the tools are actually there.
    #       RED    Restart your client after you have confirmed the tools are actually there.
    #       GREEN  Restart your client once you're done confirming the tools are actually there.
    #       GREEN  Restart your client after you have finished confirming the tools are actually there.
    #       GREEN  Restart your client after you have already finished confirming the tools are actually there.
    #
    #     It sits AFTER the modal slot and not inside the adverb run, because the
    #     completive follows the auxiliary (`… after you have finished confirming`,
    #     never `… after you finished have confirming`). It is applied in BOTH the
    #     `after|once` and the `if|when` arm — the two are separate alternatives
    #     here, so an insertion in one only would leave the other arm open. It
    #     binds only to a PREDICATE that follows immediately, so a completive over
    #     an unrelated object stays out: `… after you have finished the config and
    #     can confirm the tools …` and `… once you're through with setup and
    #     confirm the tools …` are declared-GREEN rows below.
    #     ⛔ `completed` IS THE SAME SLOT, and it was the one synonym the closure
    #     left out — `Restart your client after you have completed confirming the
    #     tools are actually there.` measured GREEN while its `finished` twin is a
    #     declared-RED row (measured at this head). The class is a closed list of
    #     completives, so it has to be enumerated; a missing member is a one-word
    #     false PASS, exactly as `make sure` / `be sure` / `validate` were in
    #     `_CONFIRM_PREDICATE` (#14679 review at `fdaaa23d016c`).
    _inversion_form(
        # ⛔ `following` AND `upon` ARE `after`, SPELLED DIFFERENTLY, and the
        # denial of that here is what made them false PASSes: this file already
        # treats them as the same relation in `_DEFERRED_CHECK_RE`
        # (`(?:after|once|when|following|upon)\s+…restart…`), and both put the
        # confirmation strictly BEFORE the restart — the inversion this pin
        # exists to red. Measured at `1dedd33da07e` by the substitution rule stated
        # at `_CONFIRM_PREDICATE`: `after`→`upon` flips **54** of the 238
        # declared-RED rows GREEN, `after`→`following` **54**; at this head the rule
        # flips 6 each, and ALL SIX ARE THE RULE'S OWN ARTIFACT, not holes — the
        # substitution renders ungrammatical copy (`upon the tools are listed`,
        # `only upon the tools are actually there`), which no doc ships. Stated
        # rather than rounded to 0, and
        # passes end-to-end on `Restart your client upon confirming the tools are
        # actually there`.
        r"\b(?:(?:after|once|following|upon)\s+"
        r"(?:you(?:'(?:ve|re)|\u2019(?:ve|re)|\s+have)\s+|you\s+|having\s+)?"
        r"(?:" + _ADVERBS + r"\s+)*"
        r"(?:" + _MODAL_TO_VERB + r"\s+(?:" + _ADVERBS + r"\s+)*)?"
        # the completive slot — see the block above `_inversion_form(`
        # ⛔ THE PRESENT-TENSE SIBLING IS THE SAME COMPLETIVE: `after you finish
        # confirming` / `after you complete confirming` measured GREEN while
        # `after you have finished confirming` is a declared-RED row — the same
        # one-inflection class as `firstly` (#14679 review at `5ee54138490e`).
        # ⛔ THE COMPLETIVE'S OWN PARTICLE IS NOT AN OBJECT: `done WITH confirming`
        # is `done confirming`. The slot's rationale for refusing words between the
        # completive and the predicate is "a completive over an UNRELATED object",
        # and `with` is the completive's particle — one inserted word was defeating
        # the slot this very comment extends (#14679 review at `019ac6ff4b1e`,
        # measured: `after you are done with confirming the tools …` GREEN while
        # its `done confirming` twin is a declared-RED row). Forward copy that
        # names an object (`… done with the config and confirm the tools …`)
        # stays GREEN, because the predicate must still follow.
        r"(?:(?:done|finished|finish(?:es)?|through|completed|complete(?:s)?)"
        r"(?:\s+(?:with|up))?\s+)?"
        # ⛔ `whenever` IS `when` AND `as soon as` IS `once`, and both were GREEN
        # twins of declared-RED rows (`Restart your client whenever the tools are
        # actually there.` vs the declared-RED `when` row — by the same rule:
        # `when`→`whenever` **14**, `once`→`whenever` **40**; at this head 0 and 1 —
        # the single `once`→`whenever` flip is the rule's artifact too, since
        # `whenever` changes the sense to frequency and the observation gate
        # correctly declines it). This is the SAME defect class the file
        # already closed once for `first`→`firstly` (cycle 29), and `whenever` is
        # not exotic — `_AND_SUBORDINATE_HALF_RE` lists it 900 lines up.
        r"|(?:if|when|whenever|as\s+soon\s+as)\s+(?:you(?:'(?:ve|re)|\u2019(?:ve|re)|\s+have)\s+|you\s+|having\s+)?"
        r"(?:" + _ADVERBS + r"\s+)*"
        r"(?:" + _MODAL_TO_VERB + r"\s+(?:" + _ADVERBS + r"\s+)*)?"
        # the completive slot — see the block above `_inversion_form(`
        # ⛔ THE PRESENT-TENSE SIBLING IS THE SAME COMPLETIVE: `after you finish
        # confirming` / `after you complete confirming` measured GREEN while
        # `after you have finished confirming` is a declared-RED row — the same
        # one-inflection class as `firstly` (#14679 review at `5ee54138490e`).
        # ⛔ THE COMPLETIVE'S OWN PARTICLE IS NOT AN OBJECT: `done WITH confirming`
        # is `done confirming`. The slot's rationale for refusing words between the
        # completive and the predicate is "a completive over an UNRELATED object",
        # and `with` is the completive's particle — one inserted word was defeating
        # the slot this very comment extends (#14679 review at `019ac6ff4b1e`,
        # measured: `after you are done with confirming the tools …` GREEN while
        # its `done confirming` twin is a declared-RED row). Forward copy that
        # names an object (`… done with the config and confirm the tools …`)
        # stays GREEN, because the predicate must still follow.
        r"(?:(?:done|finished|finish(?:es)?|through|completed|complete(?:s)?)"
        r"(?:\s+(?:with|up))?\s+)?"
        # ⛔ THE OBSERVATION GATE CARRIES THE PROGRESSIVE, and it did not until the
        # `'re` subject arm made it REACHABLE: the spelling was `see(?:s)?|saw|seen`
        # — no `seeing` — while its siblings on this line do carry it
        # (`look(?:ing|s|ed)?`, `list(?:ing|s|ed)?`) and `_CONFIRM_PREDICATE` admits
        # `see(?:ing|…)`. Until `you're` entered the subject slot nothing admitted
        # could be followed by a grammatical progressive (`you seeing` / `you have
        # seeing` / `you've seeing` are all ungrammatical), so the hole was
        # UNREACHABLE and no row could see it. One contraction made it reachable and
        # flipped a declared-GREEN row's contracted twin to RED — the very defect
        # class the `'re` arm exists to close, re-created in the opposite direction
        # (#14679 review cycle 41, measured: `if you see the tools from a previous
        # session` GREEN, `if you're seeing …` RED, `if you are only seeing …` GREEN,
        # so the verdict turned on the contraction alone).
        r"(?!(?:see(?:ing|s)?|saw|seen|look(?:ing|s|ed)?|list(?:ing|s|ed)?)\b"
        # ⛔ THE FOURTH COPY OF THE CONFIRMATION CLOSURE, and it was the one the
        # widening missed: this gate fires on an OBSERVATION verb and then asks
        # "is a CONFIRMATION also in this clause?" — so with the old vocabulary the
        # answer for `made sure` / `be certain` / `validate` was "no", the clause
        # was classified observation-only, and the whole form was skipped. Measured
        # at the review of `5ee54138490e`, end-to-end on the shipped doc:
        # `Restart your client if you have looked in the panel and made sure the
        # tools are actually there.` was GREEN while its `confirmed` twin is a
        # declared-RED row. Same list as `_CONFIRM_PREDICATE` — a copy that drifts
        # is how the first three of these opened, and this copy STILL drifted one
        # morphology short after the first widening: it read `ensur(?:e|es|ed)`
        # while the constant reads `ensur(?:e|es|ed|ing)`, so
        # `Restart your client if you are looking in the panel and ensuring the
        # tools are actually there.` stayed GREEN while its `checking` control was
        # RED (#14679 review at `019ac6ff4b1e`). The two lists are now the same
        # CLASS, read them side by side — but they are NOT letter-identical, and
        # saying they were was itself the drift this note exists to prevent
        # (#14679 review cycle 47, measured): this copy reads `verif(?:y|ies|ied)`
        # and appends `\w*`, while the constant enumerates morphology and carries
        # `verifying`. The two coincide on every spelling (`verif(?:y|ies|ied)\w*`
        # matches `verifying`). ⛔ NEITHER "SAME CLASS" NOR "WIDER" IS TRUE, and
        # both were stated here (#14679 review cycle 51, measured): `_CONFIRM_PREDICATE`
        # ALSO carries the observation stems `see|saw|seen|look|list`, which this
        # copy MUST omit — the first lookahead below is exactly that observation
        # class, and adding them here would make every `if you see the tools …`
        # row red, i.e. the opposite of the maintenance direction a parity note
        # invites. So this copy is the NARROWER one by design, and interpolating
        # the constant is not a tidy-up: it would add six observation verbs to the
        # predicate lookahead and flip declared-GREEN symptom rows to RED. The
        # divergence is load-bearing; read the MEASUREMENT, not a claim of parity.
        r"(?!.*?\b(?:confirm|check|verif(?:y|ies|ied)|ensur(?:e|es|ed|ing)"
        r"|mak(?:e|es|ing)\s+(?:sure|certain)|made\s+(?:sure|certain)"
        r"|be\s+(?:sure|certain)|validat(?:e|es|ed|ing))\w*\b"
        # ⛔ THE GUARD READS THE GRADED OBJECT, not its own copy of it. It spelled
        # `tools|tool\s+list` inline, so `tool LISTS` was not found, the clause was
        # classified as a pure observation, and form (a) was disarmed — the exact
        # defect the singular arm closed, one character over (#14679 review cycle
        # 30, measured: "… if you looked in the panel and confirmed the tool
        # lists are complete" GREEN, its `tool list` twin a declared RED row).
        r"[^\n]*?\b(?:" + _TOOLS_OBJECT + r"|they|it)\b)))"
        r"(?:\w+-)?"
        + _CONFIRM_PREDICATE
        + r"[^\n]*?" + _TOOLS_OBJECT + _NOT_A_DOC
    ),
    # (b) a participle that places the confirmation before the restart:
    #     "Restart your client, having first confirmed the tools are there".
    _inversion_form(r"\bhaving\b[^\n]*?" + _CONFIRM_PREDICATE
                    + r"[^\n]*?" + _TOOLS_OBJECT + _NOT_A_DOC),
    # (c) an ADVERSATIVE clause leading with the pre-restart marker:
    #     "Restart your client, but first confirm the tools are actually there".
    #     A BARE leading "first" is deliberately not graded — "Restart your
    #     client first and confirm the tools …" is the forward reading of the
    #     same words.
    _inversion_form(
        r"^(?:but|however|though|although|yet)\b"
        # ⛔ `firstly` IS `first`. The closed marker list admitted only the bare
        # adverb, so a one-word inflection defeated FOUR of the seven forms at
        # once — a `first`→`firstly` substitution on every declared-RED row that
        # contains `first` flipped 26 of 32 GREEN (#14679 review cycle 29,
        # measured). The same lesson form (a)'s `\w+ly` slot already carries
        # ("a closed four-word list only re-opened the hole for `previously` /"
        # "`recently` / `thoroughly`"), applied to the marker itself.
        # `although` is `though` for the same reason, one synonym over.
        r"[^\n]*?\b(?:first(?:ly)?|beforehand|in\s+advance"
        # ⛔ FORM (d) ALREADY CARRIES THESE, FORM (c) DID NOT, and the two lists
        # describe the same order claim. `before restarting` / `prior to
        # restarting` were graded in the TRAILING position and invisible in the
        # LEADING one, so a synonym gap made the adversarial lead blind
        # (#14679 review cycle 50, measured):
        #
        #     RED    Restart your client, but first confirm the tools …
        #     RED    Restart your client, confirm the tools … before restarting. (trailing)
        #     GREEN  Restart your client, but before that, confirm …            (measured)
        #     GREEN  Restart your client, but prior to that, confirm …          (measured)
        r"|before\s+(?:that|doing\s+so|restarting)|before\s+you\s+restart"
        r"|prior\s+to\s+(?:that|restarting)|ahead\s+of\s+(?:that|restarting))\b"
        # …and an intervening IMPERATIVE that is not the confirmation must not
        # carry the match through to a later `confirm the tools`: a `then`
        # between the marker and the predicate means the reader is told to do
        # something ELSE first ("but first save your work, then confirm the
        # tools"), which is the forward order (#14679 review cycle 29, measured
        # — the comma-less twin was a false RED).
        r"(?:(?!\bthen\b)[^\n])*?"
        + _CONFIRM_PREDICATE
        + r"[^\n]*?" + _TOOLS_OBJECT + _NOT_A_DOC
    ),
    # (d) a marker trailing the object, unambiguous about the order:
    #     "… confirm the tools are actually there beforehand". `first` is the
    #     same order claim in the trailing position and was GREEN (measured) —
    #     "… confirm the tools are actually there first" is a one-word
    #     substitution of a declared RED row.
    #     ⛔ `first` MUST BE CLAUSE-FINAL, because it is also an idiom: "verify
    #     the tools are present FIRST THING" is forward copy (verify as your
    #     first action after the restart) and an unanchored `\bfirst\b` reddened
    #     it (measured). `already` is NOT a marker here at all for the same
    #     reason in stronger form: it asserts a STATE, not an order — "confirm
    #     the tools are already there" says nothing about when they were
    #     confirmed, and reding it turned a declared-GREEN row RED under a
    #     one-word change (measured; its adversative twin below still reds).
    _inversion_form(
        _CONFIRM_PREDICATE + r"[^\n]*?" + _TOOLS_OBJECT + _NOT_A_DOC + r"[^\n]*?"
        r"(?:\b(?:beforehand|before restarting|before you restart"
        # `in advance` is `beforehand` one synonym over, and form (c) already
        # admits it; leaving it out here kept "… though you should confirm the
        # tools are actually there in advance" GREEN while its `beforehand`
        # twin is a declared RED row (#14679 review cycle 29, measured).
        r"|in\s+advance"
        # ⛔ `prior to restarting` / `ahead of restarting` ARE `before
        # restarting`, and `:2500` SAYS SO ("`until`/`prior to` are the same
        # inversion") — the deletion note below is about the CUE-side pattern,
        # where the bare `Restart your client` makes `prior to restart your
        # client` ungrammatical. That argument never applied HERE: in form (d)
        # the object is `restarting`, so it is reachable ordinary English, and
        # the declared-RED row's GREEN twin was one synonym over (#14679 review
        # at `1dedd33da07e`, measured by the `_CONFIRM_PREDICATE` rule:
        # `before restarting`→`prior to restarting` flips 1 row; 0 at this head).
        # (No artifact here: the substitution is grammatical and the arm closes it.)
        r"|prior\s+to\s+restarting|ahead\s+of\s+restarting"
        r")\b"
        r"|\bfirst(?:ly)?\b(?=[\s.,;:!?)\]}\"'`*_—–…]*$))"
    ),
    # (d′) `already` DOES carry an order claim — but only where an adversative,
    #     not the adverb, is what asserts it ("… but confirm the tools are
    #     already there" = confirm first). Measured GREEN before this form.
    _inversion_form(
        r"^(?:but|however|though|although|yet)\b[^\n]*?" + _CONFIRM_PREDICATE
        + r"[^\n]*?" + _TOOLS_OBJECT + _NOT_A_DOC + r"[^\n]*?\balready\b"
    ),
    # (e) the restart conditioned on the tools ALREADY being present:
    #     "Restart your client only once the tools are actually there",
    #     "… once the tools appear". The predicate is REQUIRED — a bare `once the
    #     tools` reddened "… once the tools stop appearing", a remedy with the
    #     restart first (measured). What is required is a COPULA or one of
    #     appear|show|exist|load|visible; the present-tense copula `are` admits any
    #     complement
    #     on purpose ("… once the tools are ready" is still a tool-state the
    #     reader must reach BEFORE restarting), so the claim here is that
    #     predicate SET, not "a presence sense". (`is` is gone: after `tools\s+` a
    #     singular `is` is unreachable — measured as an arm no row could depend on.
    #     `listed` STAYS: it is shadowed by `are` on "the tools are listed", but it
    #     is the only member that grades "the tools listed above appear", so
    #     dropping it lost a shape rather than shedding a dead arm.)
    #     A determiner/modifier other than a bare `the` is the same shape:
    #     "once your tools appear" / "once all the tools appear" were GREEN
    #     (measured) while "once the tools appear" was RED — the modifier is
    #     bounded to two words so the pattern cannot wander.
    #     The PERFECT auxiliaries are deliberately NOT in the set: `have`/`has`
    #     admitted every complement, and redded "… once the tools have stopped
    #     appearing" — the simple-tense twin this form must keep GREEN
    #     (measured).
    #     The leading `only` needs no arm of its own — `re.search` finds `once …`
    #     after it — so none is written (an optional prefix no row can depend on
    #     is dead weight, and it was).
    #     ⛔ THE CONNECTIVE IS NOT ONLY `once`. `after` and `when` state the SAME
    #     condition, so one word bypassed this form and 20+ declared rows:
    #     "Restart your client after the tools are listed" was GREEN while
    #     "… once the tools are listed" is a declared RED row, and the `when` /
    #     `after` twins of the singular row this PR adds were GREEN too (#14679
    #     review cycle 29, measured). The false-RED risk is NOT "the same as
    #     `once`'s" — that claim stood here until cycle 35 falsified it, see the
    #     relative-pronoun exclusion below. Two of the false REDs are carried by
    #     the two skips applied before every form: an INSTALL-side state (`… after
    #     the tools are installed`) is file-side and exempt via
    #     `_confirmation_scopes_the_config` — which is why the object lookup there
    #     had to admit the singular too — and a negated observation (`… if you see
    #     no tools`) is exempt via `_negated_observation_only`. The THIRD was
    #     carried by nothing at all, and is why the connective now excludes a
    #     relative pronoun.
    #     `if` is deliberately NOT here: it is form (a)'s connective, and adding
    #     it would grade "… if you cannot see the tools", a remedy with the
    #     restart first (measured false RED on form (a)'s wider first cut).
    _inversion_form(
        # ⛔ A RELATIVE PRONOUN IS NOT A CONDITION — measured false RED (#14679
        # review cycle 35). "Restart your client, AFTER WHICH the tools are
        # available" puts the restart FIRST and then says what follows it:
        # `which`/`that` is a RELATIVE pronoun standing for the restart event
        # ("after which …" = "and after that, …"), not a modifier of `tools`, so
        # the slot below must not be allowed to absorb it. Without this arm FIVE
        # forward renderings were RED with a message asserting an inversion the
        # copy does not contain, which is the exact false-red class #3429 says
        # gets a pin deleted:
        #   "…, after which the tools are available." /
        #   "…, after which the tools are listed under MCP." /
        #   "…, after that the tools are available." /
        #   "…, after which YOUR tools are available." /
        #   "…, after which the tool list is complete."
        # It is excluded at the CONNECTIVE and not inside the slot, because the
        # slot is lazy and would simply absorb one more word; allowing only a
        # determiner there instead would drop the `once \`tortoise_*\` tools`-class
        # modifiers the slot exists for. And it cannot hide an inversion: every
        # graded form requires a tools PREDICATE inside the connective's own
        # clause, and "after which restart your client" carries none — a
        # relative clause can only attach to what the restart already did.
        # ⛔ `whenever` AND `as soon as` — see form (a)'s note. They are `when`
        # and `once` for this purpose and were GREEN twins of declared-RED rows
        # (14 rows for `when`→`whenever`, 40 for `once`→`whenever`).
        # ⛔ `following`/`upon` are deliberately NOT here. Two different facts, and
        # an earlier revision ran them together: removing them from THIS set flips
        # 0 rows because they are not members of it, while removing them from FORM
        # (a)'s connectives reds 3 rows (`upon confirming the tools …`, `following
        # confirming …`, `upon making sure …`) — so they are load-bearing where
        # they actually sit (#14679 review round 6). The presence shapes they would
        # cover HERE are forward copy anyway ("Restart your client upon the tools
        # being listed" = after they are listed); the arm they load is the
        # CONFIRMATION shape, which is the inversion.
        r"\b(?:once|when|after|whenever|as\s+soon\s+as)"
        r"(?!\s+(?:which|that)\b)\s+"
        r"(?:[A-Za-z0-9_`*.-]+\s+){0,2}?"
        + _TOOLS_OBJECT + r"\s+"
        # ⛔ `is` is REACHABLE again, and required: it was dropped as "an arm no
        # row could depend on" because a plural `tools\s+` can never be followed
        # by a singular copula. With the singular `tool list` object graded that
        # reasoning no longer holds — "… once the tool list is complete" is the
        # twin of the declared-RED "… once the tools are listed", and leaving
        # it out is a false PASS of form (e) on the docs' own spelling (#14717).
        r"(?:all\s+|both\s+)?"
        # ⛔ Third-person SINGULAR forms are here for the singular head noun, and
        # only for it: `the tools show` is plural agreement and was already
        # graded, while "only once the tool list SHOWS `tortoise_*`" was GREEN
        # with the object widened and no singular verb (#14717, measured).
        # A verb set that assumes plural agreement silently re-opens the gap the
        # object widening just closed.
        # ⛔ `visible` is NOT here: after the object, the copula arm `are|is`
        # already grades "the tools ARE visible", and the bare "the tools visible"
        # is ungrammatical — mutating it out flips 0 rows (measured at `5f1c0cbec6cb`) and no
        # well-formed row can depend on it (#14679 review at `1dedd33da07e`,
        # measured). Declared rather than silently carried as an arm.
        # ⛔ `become` IS A CHANGE-OF-STATE COPULA, not a synonym left out on
        # purpose: `RESTART your client once the tools are available.` is a
        # declared-RED row while its `become` twin measured GREEN — the same
        # one-word gap the `visible`/`listed` arms exist to close (#14679 review
        # at `8c96f5de`, measured):
        #
        #     RED    Restart your client once the tools are available.
        #     GREEN  Restart your client once the tools become available.
        #     GREEN  Restart your client when the tools become available.
        #     GREEN  Restart your client after the tools become available.
        r"(?:are|is|appear|appears|show|shows|exist|exists|load|loads"
        r"|become|becomes|became|listed)\b"
        # ⛔ AN ABSENCE IS NOT A PRESENCE CLAIM, and `when` is a CONDITIONAL —
        # the module's own doctrine. Admitting `when` without this arm redded the
        # symptom copy `when`-led clauses are made of: "Restart your client when
        # the tools are MISSING" / "… are not visible in the panel" / "… are gone
        # from the list" went GREEN→RED at `b2f9a12f`, and neither skip applied
        # before every form could save them (`_INSTALL_SIDE_RE` wants an install
        # participle; `_OBSERVATION_NEGATION_RE` wants `see|saw|notice`).
        #
        # ⛔ AND THE ARM GRADES THE POLARITY OF THE STATE, NOT A TOKEN LIST. The
        # first cut of it listed the tokens right after the predicate, which is
        # wrong in BOTH directions (measured, #14679 review cycle 31):
        #   - it swallowed a PRESENCE claim stated as a negated absence —
        #     "once the tools are NOT MISSING" / "… are NO LONGER EMPTY" went RED
        #     to GREEN, failing open on the property this form exists to grade;
        #   - and one adverb re-opened the false RED it was written to close —
        #     "when the tools are STILL MISSING" / "… are DEFINITELY NOT there"
        #     stayed RED.
        # So: an adverbial run is skipped, then the arm suppresses the claim ONLY
        # when it resolves to an absence — a bare absence word, or a negation
        # whose complement is not one (`not visible`, `no longer visible`),
        # NEVER `not <absence>` / `no longer <absence>`, which is a presence
        # claim and must be graded.
        # ⛔ BOTH RUNS ARE THE SAME CLASS, `_ABSENCE_ADVERB_RUN`, defined once.
        # The first cut spelled eight words on ONE side and `longer?` on the
        # other, so a single inserted adverb leaked in BOTH directions
        # (#14679 review cycle 32, measured): `not ACTUALLY missing` (presence)
        # was GREEN, while `TEMPORARILY missing` / `JUST missing` / `COMPLETELY
        # gone` (absences) were RED. Cycle 33 then measured the two runs STILL
        # diverged (13 members outer, 4 inner), so `not QUITE missing` and its
        # nine twins failed open while their `-ly` twins were graded — two copies
        # of one class is how they diverged, so there is now one.
        # ⛔ ONE NEGATION BRANCH, NOT TWO — and the reason is MEASURED, not
        # assumed. The previous comment claimed `(?:not|never|no)\b` "already
        # matches" a deleted `no longer <non-absence>` alternative; re-adding it
        # flips exactly one row (`no longer ACTUALLY missing`, RED→GREEN), so that
        # reason was FALSE. The true one: the old alternative's gap was `\s*`
        # with no adverb run, i.e. it was WIDER than the negation branch and
        # FAIL-OPENED `no longer <adverb> <absence>`; it was removed for that,
        # not for being dead (#14679 review cycle 33, measured).
        r"(?!\s+"
        + _ABSENCE_ADVERB_RUN +
        r"(?:"
        r"(?:missing|gone|absent|broken|empty|stale)\b"
        r"|(?:not|never|no)\b"
        r"(?!\s+" + _ABSENCE_ADVERB_RUN +
        r"(?:missing|gone|absent|broken|empty|stale)\b)"
        r")"
        r")"
    ),
    _PRE_CUE_ONLY,
)

# A config-side noun: the config is a FILE, readable without a restart.
# ⛔ `\b` cannot precede `\.` — a word boundary needs a WORD character on one
# side, so `\b\.mcp\.json` matched only `x.mcp.json` and was DEAD for every real
# spelling (`.mcp.json`, `the .mcp.json file`, `~/.mcp.json`). Measured: the
# `config` spelling of a sentence was exempted while the `.mcp.json` spelling of
# the same sentence was graded — the false RED the arm exists to prevent.
# ⛔ AND `file` IS THE SAME NOUN. This block's own first line calls the config “a
# FILE”, and this repo's register uses that spelling — `tortoise/onboarding/
# SKILL.md` and `docs/quickstart-cloud.md` both say “it names the file it read” —
# but the set below did not admit it, so the plainest spelling of the SAME
# file-side fact was graded as a session-presence inversion, with an assert
# message claiming an inversion the copy does not contain. Minimal pair, one
# word (#14679 review cycle 42, measured):
#
#   RED    Restart your client after confirming the tools appear in the file.
#   GREEN  Restart your client after confirming the tools appear in the config file.
#
# The bare `file` spelling is admitted on the SAME terms as the other three
# nouns (`config`, `settings`, `.mcp.json`) and through the same two arms, so it
# inherits exactly the exclusion's existing width rather than widening it: a
# clause that scopes itself to a FILE is file-side whatever the file is called
# — `in the log file` matches `_CONFIG_PREP_RE` too, and is exempt for the same
# reason reading `config` is (measured). ⛔ The honest boundary, measured and
# not claimed away: `Restart your client after confirming the tools are actually
# there in the config file.` is GREEN — the `_CONFIG_PREP_RE` path does not
# consult `_SESSION_PRESENCE_RE`, so a session-presence phrase qualified by a
# file scope is exempted. That is PRE-EXISTING behaviour shared by all four
# nouns (it is identical for `config`, `settings` and `.mcp.json` at this head),
# not a hole this arm opens; it is recorded here so the next reader measures it
# rather than assuming the exclusion is narrower than it is.
_CONFIG_NOUN_RE = re.compile(
    r"(?:\b(?:configs?|settings|installer|file)\b|\.mcp\.json)", re.IGNORECASE)
# …including one naming the scope AFTER the object ("… the tools are enabled in
# your client settings"), and one pointing back at the file by PRONOUN ("… the
# config file exists and the tools are enabled in it") — both are file-side, and
# both were measured as FALSE REDS before their arm existed.
_CONFIG_PREP_RE = re.compile(
    r"\b(?:in|from|inside|per|under)\s+(?:it|them|that|those)\b"
    # ⛔ NO FREE 40-CHAR WINDOW. `[^\n]{0,40}?` let ANY mention of the config
    # within 40 characters excuse the clause, which contradicted this module's
    # own rule that "a bare mention of 'config' elsewhere in the clause is NOT
    # enough" — `… after confirming the tools are actually there in the current
    # session (the config is fine).` was GREEN (measured), a confirmation of the
    # RUNNING session's tools excused by a parenthetical aside. The noun must
    # follow its preposition directly, through at most a determiner/possessor and
    # an optional owner ("in your client settings"), which is the shape the arm
    # exists for. ⛔ BOTH halves of that were too narrow and were measured RED on
    # this repo's OWN register: the owner admitted only `client|harness|mcp`, so a
    # per-client noun ("in your Cursor settings", "in your Claude Code settings")
    # was refused even though `docs/quickstart-cloud.md` is organised per named
    # client; and the noun admitted only `config|settings`, so `.mcp.json` — the
    # spelling `_CONFIG_NOUN_RE`'s own comment records as the false RED this arm
    # exists to prevent — was refused here while `config` was exempted.
    r"|\b(?:in|from|inside|per|under)\s+(?:your\s+|the\s+|its\s+)?"
    r"(?:(?:client|harness|mcp)\s+|[`*_]*[A-Z][\w.-]*(?:\s+Code)?[`*_]*(?:'s|\u2019s)?[`*_]*\s+)?"
    # ⛔ THIS NOUN LIST MUST BE `_CONFIG_NOUN_RE`'S, and it was not: it read
    # `config|settings|file|.mcp.json` while the noun arm carries `configs?` and
    # `installer`. The SAME noun therefore scoped the clause file-side in noun
    # position and session-side in prep position, which is a one-word
    # substitution flipping a declared-GREEN row — this file's own standard for a
    # defect (#14679 review cycle 49):
    #
    #     GREEN  … after confirming the installer lists the tools.      (noun arm)
    #     RED    … after confirming the tools appear in your installer. (prep arm)
    #     GREEN  … after confirming the tools appear in your configs.    (fixed here)
    r"(?:[`'\"]?)(?:configs?|settings|installer|file|\.mcp\.json)\b",
    re.IGNORECASE)
# ⛔ AN INSTALLATION-SIDE STATE IS A FILE-SIDE FACT, NOT A SESSION ONE. The
# config-scope escape keys on NOUNS (`config|settings|installer`), so it could
# not exempt an installation VERB, and correct copy in this repo's own vocabulary
# — "Restart your client once the tools are installed / are configured / are set
# up / are on the allowlist" — went RED with a message claiming an inversion
# (measured). An install/config participle is a claim about what the FILE says;
# only `are actually there` / `are listed` / `appear` are claims about the
# running session. Deliberately bound to the state copula (`are|is|get`), never
# to a confirmation predicate — "after confirming the tools are installed" is a
# check of the file and is exempt for the same reason.
# The RUNNING-SESSION half of the same question: does the clause claim the tools
# the CURRENT session can see? Used to stop an install-side anchor from excusing
# a clause that also makes a session-presence claim.
# …and the same claim introduced by a coordinating `and` AFTER the object. The
# conjunction rule below scans only BETWEEN the config noun and the first `tools`,
# so a conjunct that follows the object was invisible and the clause was exempted
# — "… confirming the config lists the tools AND the tools are actually there" was
# GREEN (measured) while the same content with the conjunct BEFORE the object is a
# declared-RED row.
# ⛔ ONE DEFINITION OF THE PRESENCE ADVERB RUN — this list was spelled TWICE,
# once per presence pattern, and both copies were blind to every adverb but
# `actually`. So a conjunct that DOES claim the running session's tools was
# classified as config/install-side and exempted, and the pin PASSED:
#
#     RED    … and the tools are actually there     (control)
#     RED    … and the tools are all present        (control — `present` is bare)
#     GREEN  … and the tools are all there          (measured)
#     GREEN  … and the tools are really there       (measured)
#
# `_ABSENCE_ADVERB_RUN` exists one form over for exactly this reason ("an
# adverbial run is skipped, then the arm grades the polarity of the state");
# `_INSTALL_SIDE_RE` already carries `(?:all|both)\s+`. Interpolated into BOTH
# patterns so the two cannot drift again (#14679 review cycle 48).
# ⛔ THE OPEN `-ly` ARM. Its own comment names `_ABSENCE_ADVERB_RUN` as the
# precedent ("exists one form over for exactly this reason") and that constant IS
# open, while this one stayed a closed list — so ONE unlisted adverb defeated the
# presence pattern, the conjunct read as file-side, and the clause was exempted
# (#14679 review cycle 50, measured):
#
#     RED    … and the tools are actually there       (control)
#     GREEN  … and the tools are temporarily there    (measured)
_PRESENCE_ADVERB = (r"(?:\w+ly|actually|all|both|really|definitely|plainly|truly"
                    # ⛔ THE CLOSED REMAINDER IS `_ABSENCE_ADVERB_RUN`'S, and it was
                    # not: that constant carries `just|also|first|very|so|too|quite|
                    # rather|yet|longer` and this one carried none of them, so an
                    # unlisted adverb made the conjunct read file-side and exempted the
                    # clause — `… and the tools are also there` GREEN while `all there`
                    # is RED (#14679 review cycle 51, measured). One run, one literal.
                    r"|clearly|visibly|indeed|still|already|now|just|also|first|very"
                    r"|so|too|quite|rather|yet|longer)")

_SESSION_AND_CONJUNCT_RE = re.compile(
    r"\band\b[^\n]*?\b(?:" + _PRESENCE_ADVERB + r"\s+there"
    r"|(?:are|is|were|was)\s+(?:" + _PRESENCE_ADVERB + r"\s+)?there"
    r"|listed|present|visible|appearing|appears?|show(?:s)?\s+up"
    r"|load(?:ed|s)|running|live|in\s+the\s+list)\b", re.IGNORECASE)
_SESSION_PRESENCE_RE = re.compile(
    # ⛔ `there` MUST BE THE COPULA FORM. A bare `\bthere\b` matched existential
    # "there are no duplicate entries", so an entirely FILE-SIDE conjunct was
    # graded as an inversion (measured) — the config exclusion exists to keep
    # exactly that copy GREEN.
    r"\b(?:" + _PRESENCE_ADVERB + r"\s+there"
    r"|(?:are|is|were|was)\s+(?:" + _PRESENCE_ADVERB + r"\s+)?there"
    r"|listed|present|visible|appearing|appears?"
    r"|show(?:s)?\s+up|load(?:ed|s)|running|live|in\s+the\s+list)\b",
    re.IGNORECASE)
_INSTALL_SIDE_RE = re.compile(
    _TOOLS_OBJECT + r"[^\n]*?\b(?:are|is|get|gets|getting)\s+(?:(?:all|both)\s+)?"
    r"(?:installed|configured|enabled|set\s+up|added|wired|registered|mounted|provisioned|on\s+the\s+allowlist)\b",
    re.IGNORECASE)
# ⛔ A TERMINATOR ENDS A STATEMENT ONLY WHEN IT REALLY ENDS ONE. Splitting on a
# bare `[.;—]` cut INSIDE dotted tokens — `v1.2`, `e.g.`, `.mcp.json` — which let
# a claimed inversion through ("… after confirming release v1.2 works and the
# tools are actually there": the window ended inside the version number, so form
# (a) never saw the tools) and redded forward copy ("…, e.g. quit and reopen the
# app, then confirm the tools are there"). Both measured. A `;` or an em dash
# breaks when followed by whitespace; a `.` breaks only when what follows looks
# like the start of a sentence.
# The lookahead is what keeps `e.g. quit` inside one statement; the cost is that a
# sentence beginning lower case ("… . otherwise, once the tools …") is graded as
# though it continued, which is pinned as RED below rather than left implicit.
# ⛔ GLYPH VARIANTS ARE THE SAME PUNCTUATION. The break class carried only the em
# dash while the punctuation strip (`_PRE_CUE_PREFIX`) and both sequencer
# `lstrip` calls in this function treat `–` and `-` as the same mark, so an
# en-dash or spaced-hyphen twin of a declared-GREEN row
# went RED with a message claiming an inversion (measured):
# "Restart your client – once the tools are listed, run …".
# The dash/hyphen arms do NOT require a following space: "client—once",
# "client —once", "client–once" and "client--once" are one-character edits of a
# DECLARED-GREEN row and were all RED (measured), and the unspaced pre-cue twin
# ("Delete the old entry—but first confirm the tools …") was a false PASS. A
# single hyphen inside a word is still not a break ("well-known").
# The hyphen arms are three cases, and only one of them is redundant: a LEADING
# `--` is covered by `^-\s*`; an unspaced mid-word `client--once` is only
# reachable at the window start, so it needs no arm of its own (both measured);
# and a SPACED mid-string ` -- ` needs the `(?<=\s)--\s+` arm — deleting it reds
# the `re-run the bootstrap -- once …` row (measured), so it is load-bearing.
# ⛔ A SPACED `--flag` IS NOT A BREAK. `-{1,2}\s*` did break at " --cask", so
# "Restart your client, run `brew install --cask x`, once the tools appear." was
# GREEN while its `--` -free twin was RED — a false PASS that truncated the window
# in front of the very clause that inverts the order (measured). The hyphen arm is
# therefore explicit about its three shapes: a single hyphen that STARTS a break
# (" - once"), a double hyphen that stands alone ("-- once"), and a leading one
# ("-once"); a single hyphen followed by a non-space ("well-known", "-cask") and a
# spaced long option ("--cask") are neither.
# ⛔ …and a mid-string single hyphen needs WHITESPACE AFTER IT: `\s*` matches
# empty, so `pytest -q` / `tail -f log` / `pip install -e .` each split the window
# and put the inverting clause out of scope (measured: the `-q` twin of the
# `--cask` row stayed GREEN at the pin level while its `--cask` twin was caught).
_STATEMENT_BREAK_RE = re.compile(
    # ⛔ A COLON BREAKS A STATEMENT TOO — it introduces the follow-up step, which
    # is unambiguously forward ("Restart your client: once the tools are listed,
    # run …"), and the file's own doctrine is that glyph variants are the same
    # punctuation. Without it the colon twin of a DECLARED-GREEN row was RED and
    # the colon twin of a declared-RED row was GREEN, both one-character edits
    # (measured). Whitespace-bounded, so `https://` is safe — but note that a
    # DOTTED TOKEN FOLLOWED BY A SPACE still splits (`e.g.: quit and reopen`
    # splits at `e.g.`), which is the same window truncation the colon doctrine
    # already accepts, not extra protection.
    # ⛔ `*` AND `+` ARE DELIBERATELY ABSENT FROM THE WHITESPACE-BOUNDED ARM, and
    # that is a CORRECTION of a claim the neighbouring comment still makes.
    # `_PRE_CUE_PREFIX` strips the same markers, so the two mechanisms do one
    # job: narrowing `[-*+]` to `[-]` here flips **0 of the 616 declared rows**
    # (measured at `8c96f5de`, whole file: `668 passed` both ways) and every probe
    # the block names — `* Confirm …\n* Restart …`, the `+` and `>` twins, the
    # ordinal pair — returns the byte-identical verdict either way. An arm no row
    # and no probe can reach is not protection, it is rot (the same call cycle 17
    # made when it deleted the unexercised prefix strip on `pre_statement`).
    # `>` and the ordinals were already moved out by the `^-`-anchor correction
    # above; this is the last of them.
    r"(?:[;—–…]\s*|:\s+|(?:(?<=\s)[-](?![-])\s+|(?<=\s)--\s+|^-+\s*)"
    # ⛔ A MARKDOWN LIST MARKER IS A STATEMENT BOUNDARY — the same doctrine the
    # leading `^-` arm has always applied, which is why the DASH bullet split and
    # its four twins did not. The dash-only arm left `*`, `>`, `+` and both
    # ordinal spellings inside one statement, where the NEXT step's marker stayed
    # attached to the pre-cue clause as a trailing decoration (`… there. *`) and
    # defeated form (f)'s anchor:
    #
    #     RED    - Confirm the tools are actually there.\n- Restart …   (control)
    #     GREEN  * Confirm …\n* Restart …                              (measured)
    #     GREEN  > Confirm …\n> Restart … / + Confirm …\n+ Restart …  (measured)
    #     GREEN  1) Confirm …\n2) Restart … / (1) Confirm …           (measured)
    #
    # ⛔ THE `^`-ANCHORED ARMS CARRY `-` ONLY, AND THAT IS A CORRECTION. They are
    # applied to the POST-cue SUBSTRING too (`after.lstrip(" \t,…")`), where `^`
    # is mid-sentence, so `^[-*+]\s*` and `^>\s*` emptied the whole post-cue
    # window and no post-cue form was applied at all:
    #
    #     RED    Restart your client, after confirming the tools …   (undecorated)
    #     GREEN  Restart your client, **after confirming the tools …**
    #     GREEN  > Restart your client,\n> after confirming the tools …
    #
    # Measured: narrowing to `^-+\s*` and dropping `^>\s*` keeps the WHOLE table
    # and every `test_3232_*` / `test_5153_*` green while fixing both probes. (The
    # integer that used to sit here rotted the moment a row was added — the same
    # defect the split-loss paragraph was rewritten for; a count names its sha or
    # does not appear.) The
    # `>`-and-ordinal arms below do the pre-cue work here, and `_PRE_CUE_PREFIX`
    # does the `*`/`+` half — ⛔ NOT the whitespace-bounded arm, which carried
    # `*`/`+` until cycle 50 and never matched a row or a probe (measured: 0 of 616
    # rows and every named probe identical once they are removed; see the note on
    # that arm). `\s+` (not `\s*`) after the emphasis-capable markers keeps
    # `**bold**` and `*emphasis*` inside their clause.
    r"|(?<=\s)>\s+"
    r"|(?<=\s)(?:\(\d{1,2}\)|\d{1,2}[.)])\s+"
    r"|\.\s+(?=[A-Z\"'\u201c\u201d\u2018\u2019`(\[*_>])"
    r")")

# ⛔ A NEGATED *OBSERVATION* IS A SYMPTOM CONDITION, A NEGATED *INSTRUCTION* IS NOT.
# "if you see NO tools in the list" describes the failure the restart fixes (the
# same class as the declared-GREEN "if you cannot see the tools in the list"), so
# it must stay GREEN. But the same words under an instruction verb are still an
# instruction: "… but first confirm no tools are missing", "… after confirming no
# tools are missing", "… confirm no tools are missing beforehand" are all
# pre-restart demands and must stay RED. A predicate-wide negation lookahead could
# not tell those apart and DISABLED forms (b)/(c)/(d) on every one of them
# (measured — a false PASS introduced by the fix for the false red). So the rule
# is scoped to the OBSERVATION verb itself and applied as an explicit skip.
# ⛔ `see THAT …` NEEDS NO LOOKAHEAD — it is excluded BY CONSTRUCTION. An arm
# `(?!\s+that\b)` did sit here, with a comment claiming it was measured; it was
# STRUCTURALLY UNREACHABLE (a complementizer can never satisfy the negation word
# below, so the pattern does not match that shape with or without it — measured
# at review cycle 34: removing it flips 0 of 344 rows, and the pattern does not
# match "see that no tools are missing" either way). "… but first see that no
# tools are missing" is still RED, but through form (c)'s `_CONFIRM_PREDICATE`,
# which is what actually grades it. An arm no row can depend on is dead weight,
# and a comment asserting a measurement it cannot have is worse than the dead
# arm — it is how a reader comes to believe this rule covers something it does
# not.
# ⛔ THE NEGATION MUST BE THE VERB'S OWN OBJECT, NOT ONE FOUND FURTHER ALONG.
# `[^\n]*?` let the match START at the graded claim's own observation-verb
# predicate (`see|saw|seen` are in `_CONFIRM_PREDICATE`) and run ACROSS the claim
# to the appended phrase's `no errors`, so truncating at the match start deleted
# the claim itself: "after you saw the tools are actually there, and see no
# errors in the log" was GREEN while its tail-less twin is a declared RED row
# (measured, forms (a)/(b)/(c)). The object is now adjacent to the verb.
_OBSERVATION_NEGATION_RE = re.compile(
    r"\b(?:see|saw|seen|seeing|notice[sd]?|noticing)\b\s+"
    r"(?:any\s+|a\s+)?\b(?:no|none|zero|nothing)\b", re.IGNORECASE)


def _negated_observation_only(clause: str) -> bool:
    """True when the clause's ONLY claim is a negated observation.

    ⛔ THE SKIP IS CLAUSE-WIDE, SO IT MUST NOT FIRE ON A CLAUSE THAT ALSO DEMANDS
    A PRE-RESTART CONFIRMATION. Appending one unrelated negated observation
    disarmed EVERY form — "… but first confirm the tools are actually there, and
    see no errors in the log" was GREEN, and so was the same appended to (b),
    (d), (d′), (e) and (f) (measured; each twin without the phrase is RED). The
    rule is scoped to the observation *verb*, so a confirmation predicate stated
    LEFT BEHIND must not match a form, while "if you see no tools" leaves
    nothing to grade and stays GREEN.
    """
    neg = _OBSERVATION_NEGATION_RE.search(clause)
    if neg is None:
        return False
    # ⛔ THE TEST IS FORM-AGNOSTIC: strip the negated observation and ask whether
    # any graded form still matches what is left. Keying it on a confirmation
    # PREDICATE was too narrow — form (e) ("only once the tools are actually
    # there and see no errors") carries no confirmation verb at all, so it
    # stayed GREEN (measured).
    # ⛔ A CONFIRMATION PREDICATE AFTER THE NEGATED OBSERVATION IS STILL A
    # CONFIRMATION. The prefix test asked only about what is LEFT of the match, so
    # a clause that states the negated observation FIRST and the confirmation
    # second was classified as observation-only and skipped: "Restart your client
    # after seeing no errors and confirming the tools are actually there" was
    # GREEN while its `after seeing the build output and confirming …` twin is RED
    # (#14679 review cycle 36, measured). Deleting the span instead does not help
    # — form (a) needs the predicate in the connective's own clause, so the
    # sentence stays GREEN either way; the suffix must be asked directly. The
    # observation VERB is what the skip is scoped to, so this is a predicate test
    # rather than a form match — what stops form (a) on the deletion shape is the
    # requirement that the predicate sit in the connective's OWN clause, which is
    # the reason stated two lines above.
    if re.search(r"\b(?:confirm|check|verif(?:y|ies|ied|ying)"
                 r"|ensur(?:e|es|ed|ing)"
                 # …and the THIRD copy (see `_CONFIRM_PREDICATE`): a negated
                 # observation followed by `make sure` is still a confirmation
                 # the skip must not swallow.
                 r"|mak(?:e|es|ing)\s+(?:sure|certain)|made\s+(?:sure|certain)"
                 r"|be\s+(?:sure|certain)"
                 r"|validat(?:e|es|ed|ing))\w*\b", clause[neg.end():]):
        return False
    # ⛔ THE REMAINDER IS THE CLAUSE WITH THE NEGATED OBSERVATION *DELETED*, NOT
    # THE PREFIX IN FRONT OF IT. A prefix-only remainder asks only what is LEFT of
    # the observation, so a graded claim stated AFTER it was invisible to this
    # skip: "Restart your client seeing no errors only once the tools are actually
    # there" is form (e) with no confirmation verb for the arm above to catch, so
    # it was classified observation-only and shipped GREEN while its
    # negation-free twin is a declared RED row (#14679's review at `4aa1fc78`,
    # Finding 1 — the one reproduction cycle 36's suffix arm did not close;
    # measured GREEN at `31554d14` and RED with this splice). Splicing the suffix
    # onto the prefix keeps every form gradable ACROSS the seam instead of
    # discarding one side of it. This complements, and does not replace, the arm
    # above: the forms that need their predicate in the connective's OWN clause
    # (a)/(c)/(d) are closed by that predicate test, because deleting the span
    # leaves `after  errors and confirming …`, which no form matches. Widening the
    # remainder flips **0 of 417** declared rows (measured at `31554d14` by
    # re-running the whole table under this splice) — no forward row depends on
    # the discarded suffix.
    remainder = clause[: neg.start()] + " " + clause[neg.end():]
    return not any(pattern.search(remainder) for pattern in _INVERSION_FORMS)

# A clause that opens with one of these continues the cue FORWARD ("restart,
# then …"), so a subordinator later in it is not a deferral of the restart.
_FORWARD_SEQUENCER_RE = re.compile(r"^(?:then|and|next|afterwards?)\b", re.IGNORECASE)

# ⛔ A PRE-CUE FRAGMENT IS A CLAUSE WITH NO CONTENT WORD IN IT — not a clause
# that matches a sequencer LIST. The first cut of this was
# `^(?:and\s+)?(?:then|next|afterwards?|also|and)\b[\s,]*$`, i.e. a closed token
# list, and #14679's review at `2ce284961b` measured what that costs: ONE INSERTED
# WORD defeated it end-to-end on the real doc —
#
#     Confirm the tools are actually there, then PLEASE restart your client.   GREEN
#     Confirm the tools are actually there, ONLY THEN restart your client.    GREEN
#     Confirm the tools are actually there, then YOU MUST restart …           GREEN
#     Confirm the tools are actually there, AFTER WHICH restart …             GREEN
#
# none of those fragments contains a verb, which is the property that makes it a
# fragment — so the test is now that every word belongs to the closed class of
# words that carry no imperation on their own (sequencers, restrictives,
# courtesy, modal subjects, pronouns, subordinators). `Then`, `Then please`,
# `only then`, `then you must`, `After which`, `Before you` are all fragments by
# this test; `Delete the old entry`, `Then confirm the tools …` and
# `Be sure to confirm the tools …` are not, because `delete` / `confirm` are not
# in the class.
# ⛔ THE FRAGMENT CLASS IS NOT ONLY MODALS. Cycle 47 measured that
# `then you should probably` / `then you will likely` / `You may want to` /
# `You had better` are fragments in exactly the sense this test means — every
# word carries no imperation — but one word outside the list defeated the
# walk-back exactly as `must` used to, leaving the fragment as the governing
# clause and discarding the confirmation behind it:
#
#     RED    Confirm the tools … , then you must restart your client.
#     GREEN  Confirm the tools … , then you should probably restart your client.
#
# `_NEGATED_CUE_LEAD_RE` already states that "`ought` AND `had better` ARE THE
# SAME LEAD", so the fragment test could not see a member the file names two
# functions up. `probably|likely|maybe|perhaps|want|better|best|had|ought` and the
# intensifiers are added on that authority, not as a new class.
# ⛔ A CLAUSE THAT *OPENS WITH A SEQUENCER* HANDS THE READER TO THE NEXT ACTION, and
# `_SEQUENCER_FRAGMENT_RE` above cannot express that: it is a CLOSED allow-list of
# the filler words seen so far, so an unlisted one makes the clause look
# substantive and STOPS the walk-back instead of continuing it — which leaves the
# confirmation before it ungraded. One inserted word re-opened the whole pre-cue
# half of the pin with the suite still green (measured at `f16e324514`):
#
#     RED    Confirm the tools are actually there, then restart your client.
#     GREEN  Confirm the tools are actually there, then go restart your client.
#     GREEN  … then go ahead and restart / proceed to restart / remember to
#            restart / feel free to restart / all that's left is to restart
#
# The discriminator is NOT "matches no form" — an ordinary imperative that adds
# an ACTION (`then save the file`) is `_cue_governing_clause`'s DECLARED residual
# and must stay gradable. What separates the two is whether the sequencer's
# clause names an object OF ITS OWN: `save the FILE`, `run A query` do; `go`,
# `proceed to`, `remember to`, `all that's left is to` interpose no action at all
# and are the same instruction with a filler lead. So the class is closed at the
# FRONT ("begins with a sequencer") and the object test is the bound — not
# another filler word in the list below.
#
# ⛔ THAT OBJECT TEST IS A **PROXY**, AND A REVIEW SHOWED THE PROXY LEAKS. "Names a
# determiner" is not "adds an action": the dominant English spelling of this
# filler is a COPULAR FRAME that carries `the`/`your` and interposes nothing —
# `then the only thing left to do is restart`, `then your only remaining step is
# to restart`, `then on your machine restart` — all GREEN while genuinely
# inverted (measured, #14679 review cycle 55). `_FILLER_FRAME_RE` is the second
# bound: a remainder that is a copular frame, or that opens with a locative and
# names no confirmation, is still the cue's own lead-in. And the LOCATIVE half is
# why the object test no longer admits a bare `that` pronoun — `then all that is
# left is to` and `then all that's left is to` are the same sentence, and the
# hyphen-free twin was GREEN while its contraction is a pinned RED row.
#
# ⛔ AND THE FRONT IS A LIST TOO, SO IT CANNOT BE COMPLETE — stated plainly rather
# than claimed closed: `later`, `thereafter`, `once more`, `at that point` and
# `only at the end` were all GREEN before this cut (measured, same review) and are
# members now, but the next unlisted sequencer re-opens exactly this. What the
# pin DOES guarantee is that the plain forms below are graded, and every member
# it gains takes a row with it; a member it lacks is a measured residual, not a
# silent one.
_SEQUENCER_LEAD_RE = re.compile(
    r"^(?:(?:and|but|so|only)\s+)?"
    r"(?:then|next|afterwards?|after\s+that|after\s+which|which"
    r"|subsequently|finally|lastly|later|thereafter|once\s+more"
    r"|at\s+that\s+point|only\s+at\s+the\s+end)\b", re.IGNORECASE)

#: A determiner/possessive: the clause names something, so it is an instruction.
#: ⛔ A BARE `that` **IS** ADMITTED HERE, and this comment says so because the
#: opposite reading is what a first cut of it wrote: the regex admits `that` unless
#: it is the `that's` contraction, so `then all that is left is to restart` passes
#: this test. What closes that shape is the `_FILLER_FRAME_RE` arm below — a frame
#: noun reads as a frame regardless — and that is MEASURED: removing bare `that`
#: from this alternation changes **0** rows. A narrower `that <word>`-minus-frame-
#: verbs form was written, measured at 0 rows as well, and dropped rather than
#: shipped as unpinned dead weight. (Corrected after review cycle 56 found this
#: comment asserting the reverse of the shipped pattern.)
_OWN_OBJECT_RE = re.compile(
    r"\b(?:the|a|an|this|these|those|your|our|its|their|my|his|her"
    r"|that(?!['\u2019]s))\b",
    re.IGNORECASE)

#: A COPULAR FILLER FRAME, or a locative lead-in — the clause is still the cue's
#: own preamble. `the only thing left to do is restart` / `your only remaining
#: step is to restart` / `on your machine restart`. Deliberately narrow: an
#: ordinary imperative (`then save the file`, `then run a query`) matches none of
#: these and stays gradable, which is the DECLARED residual.
_FILLER_FRAME_RE = re.compile(
    # a FRAME NOUN (`thing`, `step`, `left`, `remains`) introduces the frame —
    # a bare `the … are` is not one: `confirm the tools ARE actually there`
    # carries a determiner and a copula and is a CONFIRMATION, not filler. My
    # first cut of this arm matched `the` … `are` and flipped FOUR declared-RED
    # `Then confirm the tools …` rows to GREEN, which the table caught.
    r"\b(?:the|your|only|last|remaining|all)\b[\w\s'\u2019-]{0,40}?"
    r"\b(?:thing|step|left|remains?)\b"
    # …or a `be to` infinitival lead-in (`the next move is to restart`)
    r"|\b(?:is|are|was|were)\s+to\b"
    # …or a clause that OPENS with a locative and names no confirmation
    # ⛔ THE LOCATIVE SET IS A LIST TOO, and this is its declared residual: `with`,
    # `via`, `during` and `after` are members (measured RED without them), and the
    # next preposition re-opens exactly this — the same bound the sequencer front
    # above states about itself.
    # ⛔ AND `after` MUST NOT REACH A PARTICIPLE. A popped clause is DISCARDED,
    # never graded — `_cue_governing_clause` returns `clauses[-1:]` and
    # `_INVERSION_FORMS` is not consulted until after it returns — so letting
    # `after` match `after confirming the tools are actually there` re-opened the
    # file's canonical inversion:
    #
    #     RED    After confirming the tools are actually there, restart your client.
    #     GREEN  Then after confirming the tools are actually there, restart your client.
    #     GREEN  Then after checking the tools … / Finally after confirming the tools …
    #
    # (measured at cycle 56, a regression that commit introduced — its comment
    # claimed form (a) graded the clause "before this walk-back ever runs", which
    # is the reverse of the execution order). The negative lookahead keeps
    # `after the config change`, which is 56e's pinned row.
    r"|^(?:on|in|at|inside|within|from|under|with|via|during"
    r"|after(?!\s+(?:\w+ing|confirmed|checked|verified|validated|ensured)\b))\b",
    re.IGNORECASE)


def _is_continuation_fragment(clause: str) -> bool:
    """Is `clause` a sequencer continuation rather than an instruction of its own?

    Three ways in: the whole clause is drawn from the filler/sequencer class
    (`_SEQUENCER_FRAGMENT_RE`), or it OPENS with a sequencer and its remainder is
    a filler frame (`_FILLER_FRAME_RE`), or it opens with a sequencer and names
    no object of its own (`then go`, `then all that is left is to`). The object
    test is only a PROXY for "adds an action" and leaks on copular frames, which
    is why the frame test sits in front of it; `then save the file` matches
    neither and stays gradable, which is `_cue_governing_clause`'s DECLARED
    residual.
    """
    if _SEQUENCER_FRAGMENT_RE.match(clause):
        return True
    lead = _SEQUENCER_LEAD_RE.match(clause)
    if lead is None:
        return False
    rest = clause[lead.end():].strip()
    if not rest:
        return True
    if _FILLER_FRAME_RE.search(rest):
        # ⛔ A FRAME MATCH IS NOT ENOUGH WHEN THE CLAUSE ITSELF GRADES. The `after`
        # arm originally carried a ONE-TOKEN negative lookahead, so a pronoun, an
        # `-ly` adverb or a hyphen between `after` and the participle restored the
        # canonical false PASS it had just closed (measured, review cycle 58):
        #
        #     RED    After you have confirmed the tools are actually there, restart …
        #     GREEN  Then after you have confirmed the tools are actually there, restart …
        #     GREEN  Then after carefully confirming / after double-checking / after
        #            you've finished checking …
        #
        # `after you have confirmed the tools` is form (a)'s OWN listed spelling,
        # so the pop decision is taken from the clause: a remainder that names the
        # tools AND carries a confirmation predicate is a GRADED CONFIRMATION, not
        # a locative lead-in, whatever frame word it happens to contain.
        # ⛔ BUT KEEPING A CLAUSE IS ONLY WORTH DOING IF SOMETHING CAN GRADE IT. A
        # frame-led remainder that names the tools and carries a predicate is kept
        # by the test above — and if no form matches it, the kept clause becomes
        # the GOVERNING one, nothing is graded, and the confirmation before it is
        # discarded with `clauses[:-1]`. One inserted clause re-opened a declared-RED
        # row that way (measured, review cycle 59):
        #
        #     RED at cd1e01ad74 → GREEN at e337fcd3d5
        #     Confirm the tools are actually there, then after the config change
        #     verify the tools are actually there, restart your client.
        #
        # …and twelve variants of it (frame-noun, `on your machine`, `with the new
        # config`, `via the tray`, split-sentence). So the test is not "does it look
        # like a confirmation" but "would a form grade it" — the branch pops
        # otherwise, which restores the earlier confirmation as the governor.
        if (_TOOLS_OBJECT_RE.search(rest)
                and re.search(_CONFIRM_PREDICATE, rest, re.IGNORECASE)):
            return not any(p.search(clause) for p in _INVERSION_FORMS)
        return True
    return _OWN_OBJECT_RE.search(rest) is None


_SEQUENCER_FRAGMENT_RE = re.compile(
    r"^(?:\s*(?:then|next|afterwards?|which|only|just|also|and|but|so|please"
    r"|kindly|you|we|must|should|can|could|will|would|may|might|shall"
    r"|need|needs|have|has|to|be|sure|make|makes"
    r"|first|firstly|now|once|finally|lastly|subsequently|still|too"
    r"|probably|likely|maybe|perhaps|want|wants|better|best|had|ought"
    r"|always|often|never|ever|simply|prefer|very|really|actually|definitely|surely"
    # ⛔ THE OPEN `-ly` ADVERB CLASS, AND THE FILLER IDIOMS. `_ADVERBS` is the
    # OPEN class (`\w+ly` + a closed remainder) precisely because "a noun list
    # cannot be completed"; form (f) and form (a) both use it, while THIS list
    # carried no `\w+ly` arm at all — so one adverb re-opened the whole pre-cue
    # half, and the two arms of the same pin disagreed about the same word class
    # (#14679 review cycle 49):
    #
    #     RED    … , then restart your client.      (declared table row)
    #     GREEN  … , then quickly restart your client.      (measured)
    #     GREEN  … , then immediately restart your client.  (measured)
    #     GREEN  … , then all you need to do is restart …   (measured)
    #
    # `immediately|again|additionally` are already members of `_PRE_CUE_ONLY`'s
    # leading run, and the EIGHT contractions below (counted off the literal, not
    # from memory — this said "six" while the alternation carried eight) are the
    # same one-apostrophe family cycle 48 closed ten of (`it'd` vs the
    # declared-RED `it is` twin).
    r"|\w+ly|immediately|again|additionally|soon|already"
    r"|all|do|does|next|step|going|the|a|an|of"
    r"|it'd|it'll|that'd|they'll|we're|we've|there'll|i'll"
    # ⛔ CONTRACTIONS, PRONOUNS, COPULAS AND THE `able|ready|done` ADJECTIVES.
    # The class claims to be "sequencers, restrictives, courtesy, modal subjects,
    # PRONOUNS and subordinators" — and `it`, `that`, `there`, `is`, `are` are
    # exactly those, while `be` was present and `is`/`are` were not. One
    # contraction defeated the walk-back and the confirmation behind it was
    # discarded, so the pin PASSED on plainly inverted copy:
    #
    #     RED    … then you must restart your client …
    #     GREEN  … then you'll need to restart your client …          (measured)
    #     GREEN  … then you are ready to restart …                    (measured)
    #     GREEN  … and once that's done, restart …                    (measured)
    #     GREEN  … then it is best to restart …                       (measured)
    #     GREEN  … then you will be able to restart …                 (measured)
    #
    # `_PRE_CUE_ONLY` already knows `you(?:'ll|'d|'re|'ve)`, so the two lists of
    # the same class disagreed. This is register, not exotica: `docs/*.md` uses
    # `it's` 12×, `you're` 8×, `that's` 6×, and `docs/quickstart-cloud.md:433`
    # itself uses `you'll`.
    r"|you'll|you'd|you're|you've|we'll|they're|it's|that's|that'll|there's"
    r"|it|that|this|there|they|is|are|was|were|am"
    r"|able|ready|done|possible|necessary|important|wise|safe"
    r"|quite|rather|already"
    r"|before|after|until|unless|prior|when|whenever|as\s+soon\s+as)\b\s*,?\s*)+$", re.IGNORECASE)

# ⛔ A CUE GOVERNED BY A TEMPORAL SUBORDINATE INVERTS WHAT FOLLOWS IT. In
# "Before you restart your client, confirm the tools are actually there" the
# confirmation is the condition OF the restart, so it happens FIRST — the same
# order the pin reds in form (a)'s "restart after confirming …". The post-cue
# side skips form (f) on purpose (a confirmation that follows the cue IS the
# order the pin requires), so without this the `before` rendering was GREEN
# (measured at `5c6dda9dc3`, together with its comma/period/semicolon `then`
# twins). `until`/`prior to` are the same inversion; "after" is NOT (it is the
# cue's own forward form (a)). `by the time` is the fourth spelling of the same
# relation and was measured GREEN after the first three were closed (review
# cycle 34): "By the time you restart your client, confirm the tools are
# actually there" is still a pre-restart demand.
_CUE_UNDER_INVERTING_SUBORDINATE_RE = re.compile(
    # ⛔ `prior to` IS NOT HERE. It was carried unpinned from the first cut and a
    # reviewer could not reach it in any grammatical shape (the cue is the bare
    # `Restart your client`, so "prior to restart your client" is not English,
    # and with a comma the pattern cannot match) — `prior\s+to` flipped 0 of 359
    # rows on removal. An arm no row can depend on is dead weight.
    r"\b(?:before|by\s+the\s+time|until)\b[^,;.]*$", re.IGNORECASE)

# ⛔ THE SAME INVERSION, WITH THE CUE NEGATED — and NOTHING on the post-cue side
# saw it, because `_CUE_UNDER_INVERTING_SUBORDINATE_RE` is PRE-cue only. The cue
# `Restart your client` matches INSIDE `Don't restart your client`, so the
# negation lead falls into `before` (where no pre-cue form grades it, having no
# confirmation predicate of its own) and the post-cue half is graded against
# forms whose connective sets contain no `until`:
#     RED    Until you restart your client, confirm the tools are actually there.
#     GREEN  Don't restart your client until you have confirmed the tools are actually there.
# The second sentence asks for the confirmation BEFORE the restart, which is the
# inversion, and this file already states the rule at `_CUE_UNDER_INVERTING_`
# `SUBORDINATE_RE`'s rationale ("`until`/`prior to` are the same inversion")
# (#14679 review at `1dedd33da07e`, measured).
# The arm is deliberately MONOTONE — it fires ONLY when the cue itself is
# negated, so no forward copy can reach it — and it requires a CONFIRMATION
# predicate, so the presence-only shape (`Don't restart your client until the
# tools appear.`) stays GREEN: nothing there names who confirms what, and
# grading it would widen the pin into copy the docs do not ship (declared
# residual, measured on the three spellings above).
# ⛔ AND THE LEAD IS EVERY NEGATED MODAL, NOT JUST THE IMPERATIVE — the closure
# below used to be `do not|don't|never`, so the MODAL spellings of the same
# instruction went GREEN while `Don't …` is a declared-RED row (measured at this
# head):
#
#   GREEN  You shouldn't restart your client until you have confirmed the tools are actually there.
#   GREEN  You shouldn't restart your client until you've confirmed the tools are actually there.
#   GREEN  You should not restart your client until you have confirmed the tools are actually there.
#   GREEN  You must not restart your client until you have confirmed the tools are actually there.
#
# `shouldn't` is ordinary doc prose ("you shouldn't need to restart …"), and the
# deferral is identical whichever negation spells it: the confirmation must land
# BEFORE the restart, which is the inversion. Monotonicity is preserved — the
# lead still has to END at the cue, and the connective + confirmation predicate
# are still required — so a positive-modal forward twin stays GREEN (`You can't
# confirm the tools are actually there until you restart your client.` measures
# GREEN: the negation is not on the cue's lead, and the order is forward).
#: ⛔ ONE CONSTANT FOR ONE CLASS. `be <participle> to` is a single construction
#: (`You are required/supposed/expected/allowed/meant to …`), and it lived INLINE
#: in `_PRE_CUE_ONLY` only — so the negated-cue arm, which needs the same class,
#: carried a strict subset and shipped `You are not supposed to restart … until
#: you have confirmed …` GREEN while the positive spelling is a declared-RED row.
#: Two copies of one class disagreeing is this file's most repeated defect, so the
#: class is defined once and both arms consume it.
_PERIPHRASTIC_MODAL = (
    r"(?:required|supposed|expected|advised|meant|obligated|allowed|asked)")

_NEGATED_CUE_LEAD_RE = re.compile(
    # ⛔ `ought` AND `had better` ARE THE SAME LEAD, and the comment above claims
    # "EVERY NEGATED MODAL" — so the two members the enumeration left out are the
    # same defect one modal over (measured at the review of `5ee54138490e`:
    # `You oughtn't restart your client until you have confirmed the tools are
    # actually there.` and `You'd better not …` were GREEN while their `shouldn't`
    # twin is a declared-RED row).
    # ⛔ `ought` goes in the MODAL list, not in its own arm — but its NEGATION is
    # NOT the same shape as every other member's, and assuming it was cost a hole:
    # `ought` is the one member whose positive form carries a `to`-infinitive
    # (`ought to`), so the spelled-out negative is `ought not TO restart`, and the
    # `not`-anchored arm below could not reach it — `You ought not to restart your
    # client until you have confirmed the tools are actually there.` measured GREEN
    # while its `oughtn't` twin is a declared-RED row (#14679 review round 6).
    # `\s+to` is therefore optional inside the negation, for every member: the
    # others never carry it, so the group costs nothing and closes the class.
    r"(?:\bnever"
    # ⛔ `have`/`has` JOIN THE LIST: `You have not got to restart …` is the same
    # lead as `don't have to`, and the arm could not reach it because the spelled-
    # out negation group carried the modals but not the auxiliaries (measured,
    # #14679 review cycle 55).
    r"|\b(?:do|does|did|should|could|would|must|need|have|has|can|may|might|ought)"
    r"(?:\s+not(?:\s+to)?|n['\u2019]t)"
    # ⛔ `can` + `n't` IS `can't`, NOT `cann't` — the double n made the `can` arm
    # structurally DEAD, so the most common negated modal in English was GREEN
    # while its `shouldn't` twin is a declared-RED row (#14679 review cycle 50,
    # measured). `cannot` was covered only by its own literal:
    #
    #     RED    You shouldn't restart your client until you have confirmed the tools …
    #     RED    You cannot   restart your client until you have confirmed the tools …
    #     GREEN  You can't    restart your client until you have confirmed the tools …
    #
    # `won't` (will + not) and `shan't` are the same contraction class.
    r"|\b(?:ca|wo|sha)n['\u2019]t"
    # …and the spelled-out forms of the same modals, which involve no contraction:
    # ⛔ `you're not` IS `aren't` ONE APOSTROPHE OVER, and BOTH were unhandled on
    # this side of the arm: the spelled-out form admitted `are not`, the contraction
    # form admitted `aren't`, and the far commoner `you're not` / `they're not` /
    # `it's not` matched NEITHER — so `You're not supposed to restart … until you
    # have confirmed …` was GREEN while the `aren't` spelling of the same sentence
    # is a declared-RED row (measured, #14679 review cycle 57).
    r"|\b(?:will|shall|are|you['\u2019]re|we['\u2019]re|they['\u2019]re"
    r"|it['\u2019]s|he['\u2019]s|she['\u2019]s)\s+not(?:\s+to)?"
    # ⛔ THE CONTRACTIONS OF `be`, which the `ca|wo|sha` arm one line below does
    # NOT cover — `aren't`/`isn't`/`wasn't`/`weren't` never matched this arm at all
    # (`You aren't going to restart … until you have confirmed …` measured GREEN,
    # #14679 review cycle 55). `(?:\s+to)?` is the `ought not to` lesson: `aren't to
    # restart` is the same lead.
    r"|\b(?:are|is|was|were)n['\u2019]t(?:\s+to)?"
    r"|\b(?:had|'d|\u2019d)\s+better\s+not"
    r"|\bcannot)"
    # ⛔ AN ADVERB BEFORE THE CUE. `_ADVERBS` exists for exactly this defect and
    # every other arm takes it (form (f), form (a), `_ABSENCE_ADVERB_RUN`,
    # `_SEQUENCER_FRAGMENT_RE`) — this one did not, so ONE inserted adverb
    # re-opened a declared-RED row (#14679 review cycle 51, measured):
    #
    #     RED    Don't restart your client until you have confirmed the tools …
    #     GREEN  Don't ever restart your client until you have confirmed …
    #
    # …and `You shouldn't ever/immediately/simply …`, `Don't just/now …` likewise.
    # ⛔ AND THE SAME HOLE ONE CLASS OVER: A SEMI-MODAL BETWEEN THE NEGATION AND
    # THE CUE. `_CUE_LEAD_ADVERBS` is an adverb run, so it admits `shouldn't EVER
    # restart`, and it is silent on `shouldn't NEED TO restart` — the negation is
    # then not adjacent to the cue and the whole negated-cue arm is unreachable.
    # `need` is ALREADY a member of the alternation above; only its POSITION as a
    # `to`-semi-modal was unhandled, and form (f) has carried that chain for the
    # positive sibling all along (`need|have|want … to`, `be able to`, `be going
    # to`). The `_SEQUENCER_FRAGMENT_RE` block's "`shouldn't` is ordinary doc
    # prose" line is the one that cites it as prose — and that line was the
    # ungraded phrasing:
    #
    #     RED    You shouldn't restart your client until you have confirmed …   (row)
    #     GREEN  You shouldn't need to restart your client until you have confirmed …
    #     GREEN  You don't need to restart your client unless/before you have confirmed …
    #
    # Measured end-to-end at `f16e324514`: replacing the quickstart paragraph with
    # the `don't need to … until` spelling left the suite at 724 passed.
    # ⛔ AND MY FIRST CUT OF THAT SLOT WAS A **SUBSET** OF FORM (f)'s CLASS, which
    # is this file's most repeated defect: two copies of one class disagreeing.
    # `_PRE_CUE_ONLY` has carried `be <participle> to` (`supposed|allowed|meant|
    # required|expected|advised|obligated|asked`) and `have got to` all along and
    # calls them "the same instruction"; the slot below listed neither, so
    #
    #     GREEN  You are not supposed  to restart your client until you have confirmed …
    #     GREEN  You are not allowed / not meant to restart …
    #     GREEN  You have not got to restart …
    #
    # all shipped GREEN while `You must confirm …` is a declared-RED row (measured,
    # #14679 review cycle 55). `_PERIPHRASTIC_MODAL` is now ONE constant consumed by
    # both arms, and `test_3232_the_two_copies_of_the_periphrastic_modals_agree`
    # pins the agreement so a third divergence is a red row, not a comment.
    # ⛔ AND `aren't` / `isn't` NEVER MATCHED AT ALL: the spelled-out arm required
    # `are not`, so the contraction — the commonest negation in English prose —
    # was GREEN, including `aren't going to`, which the `be|going` member was
    # one `n't` away from covering (measured, same review).
    r"(?:\s+(?:" + _CUE_LEAD_ADVERBS + r"|"
    r"(?:need|needs|have|has|want|wants|be|able|going|got"
    r"|" + _PERIPHRASTIC_MODAL + r")(?:\s+to)?))*\s*$",
    re.IGNORECASE)
# ⛔ `unless` IS `until`, SPELLED DIFFERENTLY — and it was in NEITHER this set
# nor form (a)'s or (e)'s, so the whole negated-cue arm was unreachable for the
# most idiomatic English spelling of "not … until":
#
#     RED    Don't restart your client until you have confirmed the tools …   (control)
#     GREEN  Don't restart your client unless you have confirmed the tools …  (measured)
#
# One word, doc-level: replacing only the cue paragraph with the `unless` spelling
# left `_restart_cue_order_violation` returning None, so the pin PASSED on an
# inversion it exists to red (measured, #14679 review cycle 46). The file already
# treats `if|unless|until` as one class one arm over (`_AND_SUBORDINATE_HALF_RE`),
# and its own doctrine is that a connective spelled differently is the same
# connective — "`following` AND `upon` ARE `after`", "`whenever` IS `when`".
# Non-vacuity: adding it flips the finding to RED and moves no other row.
_DEFERRING_CONNECTIVE_RE = re.compile(
    r"^(?:until|unless|before|by\s+the\s+time|prior\s+to)\b", re.IGNORECASE)

# …and a clause that ENDS on one introduces the NEXT clause, whichever side of
# its own comma the sequencer sits on. `_FORWARD_SEQUENCER_RE` can only see an
# introducer that starts a clause, so an introducer attached to the cue's own
# text ("Restart your client AND THEN, once the tools are listed, …") leaves its
# introduced clause bare — and that clause is then graded standalone, which is a
# FALSE RED on copy that plainly puts the restart first (measured).
_SEQUENCER_TAIL_RE = re.compile(
    r"(?i)(?:^|[\s,;:(—–-])(?P<seq>(?:(?:and|then|next|afterwards?)\b[\s,]*)+)$"
)


# A clause that opens with one of these overrides a forward introducer: its
# OWN connective decides the order, so "… and then, BUT FIRST confirm the tools
# are actually there" must not be swallowed by the forward skip (measured — the
# glued-introducer merge above turned exactly that RED into a GREEN before this
# guard existed).
_ADVERSATIVE_LEAD_RE = re.compile(r"^(?:but|however|though|although|yet)\b", re.IGNORECASE)

# Markdown/quoting decoration, for comparing a clause against a structural regex
# that anchors on its own punctuation. Replaced by a SPACE, never deleted: `_`
# and `*` can sit between the words of a sequencer run, and deleting them would
# fuse tokens a `\b` boundary depends on.
# ⛔ WORD-INTERNAL `_`/`*` ARE NOT DECORATION. An unconditional substitution
# turns `check_then` into "check then", which MANUFACTURES the boundary the
# tail anchor needs — so `run `check_then`, once the tools are listed` went
# GREEN (measured) with the inverting clause skipped. Only a decoration
# that is NOT flanked by word characters is replaced, and the set carries
# the bracket/brace glyphs the clause strip set (`_PRE_CUE_PREFIX`) already
# has, or `[then]` / `{then}` stayed RED while `**then**` was GREEN.
_DECORATION_RE = re.compile(
    # ⛔ TYPOGRAPHIC QUOTES ARE DECORATION TOO, and they were not. GFM does no
    # smart-punctuation substitution (its extensions are tables / task lists /
    # strikethrough / autolinks), so a `“` in a `.md` source reaches the reader
    # unchanged — and this repo's own docs use them. A pre-cue imperative wrapped
    # in them was never graded (#14679 review cycle 51, measured):
    #
    #     RED    "Confirm the tools are actually there." Restart your client, …
    #     GREEN  “Confirm the tools are actually there.” Restart your client, …
    #
    # The file's doctrine is that GLYPH VARIANTS ARE THE SAME PUNCTUATION (which
    # is why `–`, `-`, `…` and `:` were each added); this is that doctrine one
    # glyph over. `«»` and `‘’` are the same class.
    # ⛔ …BUT NOT THE GLYPHS OF THE DOCS' OWN `tortoise_*` TOKEN. The right-hand
    # alternation fires whenever the character is not FOLLOWED by a word
    # character, and in `tortoise_*` the `_` is followed by `*` — so the strip
    # rewrote the doc's own object to `tortoise`, which `_TOOLS_OBJECT` cannot
    # match, and form (f) went blind (#14679 review cycle 51, measured):
    #
    #     RED    Confirm the tools show up in the list, then restart your client.
    #     GREEN  Confirm `tortoise_*` shows up in the client's MCP/tool list, …
    #
    # The token is graded by every OTHER form (their post-cue twins are RED);
    # only the arm that strips decoration lost it. Both the `_` AND the `*`
    # must be spared, so the guard is on BOTH sides.
    r"(?<![A-Za-z0-9])(?<!tortoise)(?<!tortoise_)[*_`>\[\]{}|“”‘’«»]"
    r"|(?<!tortoise)(?<!tortoise_)[*_`>\[\]{}|“”‘’«»](?![A-Za-z0-9])")


def _confirmation_scopes_the_config(clause: str) -> bool:
    """Is this clause's confirmation about the CONFIG (a file) rather than the
    running session's tool list?

    A pre-restart check of the CONFIG is LEGITIMATE — the file is readable
    without a restart — so "Restart your client after verifying the config lists
    the tools" keeps the restart FIRST and must stay GREEN (measured as a FALSE
    RED on the previous cut of this pin). Two placements are graded: the config
    noun before the object ("… verifying the config lists the tools"), and a
    prepositional phrase after it ("… checked the tools are enabled in your
    client settings"). A bare mention of "config" elsewhere in the clause is
    NOT enough — "… confirming the tools are there — the config is read at
    start" stays an inversion.

    ⛔ UNLESS THE CLAUSE ALSO CONFIRMS THE TOOLS. The exclusion is positional,
    so a config noun would otherwise swallow a CONJUNCTION: "… after confirming
    the config is right AND the tools are actually there" confirms the running
    session's tools too and is an inversion — it was GREEN (measured) before a
    coordinating `and` between the two was treated as breaking the exclusion.
    """
    # ⛔ THE OBJECT IS LOCATED WITH THE FILE'S OWN TOKEN, not with a bare
    # `find("tools")`: the literal lookup cannot see the SINGULAR `tool list`
    # that `_TOOLS_OBJECT` grades, so a config-scoped clause naming it fell
    # through this exclusion and was graded as an inversion (measured on the head
    # that widened the object):
    #
    #   GREEN  Restart your client after verifying the config lists the tools.
    #   RED    Restart your client after verifying the config lists the tool list.
    #
    # The config is a FILE and readable without a restart, so both are forward
    # copy and the RED one is the false red #3429 records as the shape that gets a
    # pin deleted. The search is case-insensitive and indexes the ORIGINAL
    # clause: a capitalised object ("… the config lists the Tools") must route
    # through the same exclusion, and was a FALSE RED while this lookup was
    # case-sensitive (measured).
    object_at = _TOOLS_OBJECT_RE.search(clause)
    if object_at is None:
        return False
    tools_at = object_at.start()
    # An installation/config STATE is file-side — but it is an ANCHOR, not a
    # short-circuit. Returning True here BYPASSED the conjunction rule below, so
    # `… confirming the tools are installed AND the tools are actually there` was
    # GREEN while its `are actually there`-only twin is a declared-RED row
    # (measured, with its `configured and they appear in the list` twin). A
    # clause that also claims the RUNNING session's tools is still an inversion.
    if (_INSTALL_SIDE_RE.search(clause)
            and not _SESSION_PRESENCE_RE.search(clause)):
        return True
    before = clause[:tools_at]
    after_object = clause[tools_at:]
    config = _CONFIG_NOUN_RE.search(before)
    if not config:
        return bool(_CONFIG_PREP_RE.search(after_object))
    tail = before[config.end():]
    and_match = re.search(r"\band\b", tail, re.IGNORECASE)
    if not and_match:
        # …but the object may be followed by a session-presence conjunct, which
        # the `tail` scan above cannot see because it stops at `tools`.
        return not _SESSION_AND_CONJUNCT_RE.search(after_object)
    # The `and` only breaks the exclusion when the second conjunct is NOT
    # itself file-side: "… the config file exists and the tools are enabled in
    # it" stays file-side (the trailing pronoun points back at the file), while
    # "… the config is right and the tools are actually there" confirms the
    # session's tool list and is graded.
    return bool(_CONFIG_PREP_RE.search(after_object)
                or _CONFIG_NOUN_RE.search(tail[and_match.end():]))


def _tools_appear_after_cue(quick: str) -> bool:
    """The POSITIVE half of the pin: does the cue's sentence, or the one
    immediately after it, name the tools?

    ⛔ NOT A PARAGRAPH-WIDE SEARCH. A paragraph-wide search is satisfiable by an
    UNRELATED later mention — this paragraph ends "if the tools are missing, run
    `tortoise mcp-status`" — so any paragraph that says "restart" and mentions
    the tools anywhere would pass. The window is the cue's sentence plus the
    next one — `parts[:2]` of a split at REAL statement breaks — and no further: measured, "Restart your client. The config is
    reloaded. The tools appear later." must NOT satisfy it.
    """
    para = next(p for p in quick.split("\n\n") if _RESTART_CUE_RE.search(p))
    flat = " ".join(para.split())
    after = flat[_cue_end(flat):]
    # ⛔ DO NOT `lstrip` THE TERMINATOR. Stripping it turns "the cue's sentence or
    # the next one" into "the next TWO", because the empty first part is then
    # filled from further along — measured: "Restart your client. The config is
    # reloaded. The tools appear later." wrongly satisfied the bound. Only the
    # separating whitespace/comma that continues the cue is stripped.
    parts = _STATEMENT_BREAK_RE.split(after.lstrip(" \t,…"), maxsplit=2)
    # ⛔ CASE CARRIES NO MEANING IN THIS PROSE, and this was the ONE object search
    # without the flag: "Restart your client, then confirm the Tools are there"
    # satisfied the bound but "Restart your client. Tools then appear in the
    # list." did not — a red with the "must keep a tools-appear clause" message
    # over a paragraph that carries one (#14679 review cycle 30, measured).
    return re.search(_TOOLS_OBJECT, " ".join(parts[:2]),
                     re.IGNORECASE) is not None


def _restart_cue_order_violation(quick: str) -> str | None:
    """The first clause in `quick`'s restart-cue paragraph that INVERTS the
    cue's order, or None when the paragraph is clean.

    BOTH sides of the cue are graded, but NOT the same way, because they are
    not the same statement.

    * BEFORE the cue, only the clause that GOVERNS the cue is graded (the last
      one). Grading every earlier clause read an ordinary concessive as a
      deferral — "Even if you confirmed the tools in an earlier session, a
      changed config is not picked up until the process reloads. Restart your
      client, then confirm the tools …" was RED (measured) while nothing in it
      defers the restart. Form (f) is what grades the sentence-before shape
      ("Confirm the tools are actually there. Restart your client …"), and it is
      PRE-CUE ONLY.
      ⛔ THAT EXAMPLE IS TWO SENTENCES, and the HALF-SENTENCE spelling of the same
      concessive is a separate hole: comma-joined, it DOES govern the cue, and
      form (a) graded it as a deferral — "If you already checked the tools once,
      restart your client, then confirm the tools are actually there." was a
      FALSE RED (#14679 review at `49ef237f4c5b`, P1-2). Its own exemption is
      `_is_completed_concession`, which keys on the clause being a CONDITION
      whose check is marked PRIOR-COMPLETE — the temporal deferrals this side
      must grade carry no such marker.
    * AFTER the cue, only the CUE'S OWN SENTENCE is graded. A later sentence is
      a separate statement the reader carries out AFTER the restart, so grading
      it redded correct copy — "Restart your client. Once the tools are listed,
      run `tortoise mcp-status --live`." (measured RED, with a message
      asserting an inversion the copy does not contain). For the same reason
      form (f) is not applied post-cue at all: a confirmation that follows the
      cue is the very order this pin REQUIRES ("Restart your client. Confirm
      the tools are actually there.").

    Every split part is stripped before grading, because the `^`-anchored forms
    could otherwise never fire past the first part: `re.split` leaves a leading
    space on every part after a separator, so "Delete the old entry, but first
    confirm the tools are actually there. Restart your client …" was GREEN
    (measured) — a real pre-cue inversion, invisible to the anchor.

    Graded forms are exactly the SEVEN patterns in `_INVERSION_FORMS` — (a), (b),
    (c), (d), (d′), (e) and the pre-cue-only (f) — minus (i) any whose confirmation
    scopes the config, (ii) every clause that opens with a forward sequencer (see
    above; post-cue, that skip covers all seven), and (iii) a negated OBSERVATION
    (`_OBSERVATION_NEGATION_RE`), which is a symptom condition rather than a
    confirmation. ⛔ The count was stated as "six" here and in two other comments
    while the tuple held seven: `(d′)` (an adversative leading `already`) is
    load-bearing and was missing from every enumeration, so a reader auditing the
    graded surface under-counted it (measured — removing (d′) flips
    "… but confirm the tools are already there" from RED to GREEN).
    Rephrasings outside that set are out of scope BY DESIGN; this function grades
    what it claims and claims only what it grades.
    """
    # ⛔ EVERY CUE PARAGRAPH, not just the first. `next(...)` graded only the
    # first paragraph containing the cue, so a troubleshooting/FAQ paragraph
    # later in the doc could invert the order with no signal (measured).
    for para in [p for p in quick.split("\n\n") if _RESTART_CUE_RE.search(p)]:
        violation = _paragraph_cue_order_violation(para)
        if violation is not None:
            return violation
    return None


def _cue_governing_clause(statements: list[str]) -> tuple[list[str], str]:
    """The clause that GOVERNS the cue, and the STATEMENT it sits in.

    A clause with no content word in it — only sequencers, restrictives,
    courtesy, modal subjects, pronouns and subordinators (`then`, `only then`,
    `then please`, `then you must`, `after which`, `before you`) — is a
    FRAGMENT, not an instruction. Grading it reads the fragment and misses the
    imperative it sequences, which is how one inserted word re-opened the false
    PASS: `_SEQUENCER_FRAGMENT_RE` is the test, and it is a class rather than a
    list on purpose (#14679's review at `2ce284961b`, measured). Walking back is
    what makes the pre-cue side symmetric with the post-cue side, which has
    carried `_SEQUENCER_TAIL_RE` for the same reason.

    ⛔ THE WALK CROSSES A STATEMENT BOUNDARY, and it must: `.`/`;` are statement
    breaks, so in "Confirm the tools are actually there. Then restart your
    client" the surviving statement is the literal `Then` and the imperative
    lives in the PREVIOUS statement. Popping only within the last statement
    fixes the comma twin and leaves both other twins GREEN (measured).

    ⛔ AND IT RETURNS THE STATEMENT THE CLAUSE CAME FROM, not the last one. The
    two differ exactly when the walk-back crossed a boundary, and the deferral
    exemption is a property of the clause's OWN statement: handing it
    `statements[-1]` (the literal `Then`) made `After restarting, check the
    tools are present. Then restart your client, …` a FALSE RED — one inserted
    `Then` over a declared-GREEN row (measured, #14679 review at `31554d1424af`).

    ⛔ SCOPE, stated because it is PARTIAL and the consequence is not obvious: it
    returns `clauses[-1:]` — ONLY the clause that governs the cue — so a pre-cue
    confirmation followed by one ORDINARY imperative is never graded at all.
    Measured (#14679 review cycle 42):

        GREEN  Confirm the tools are actually there, then restart your client.   (the RED row)
        GREEN  Confirm the tools are actually there, save the file, then restart your client.
        GREEN  Confirm the tools are actually there, quit the editor, then restart your client.
        GREEN  Confirm the tools are actually there. Save the file. Then restart your client.

    The governing clause resolves to `save the file`, which no form matches, and
    the confirmation clause is DISCARDED — so any wording that slips an ordinary
    imperative between the check and the cue is out of this pin's reach. This is
    declared rather than fixed because the `last clause only` scope is itself a
    DECLARED choice with a measured false red behind it (`_is_completed_concession`
    exists precisely because grading every earlier clause reddened ordinary
    concessive copy), and because the obvious tightening — grade EVERY pre-cue
    clause — does not rescue that example either: “Even if you confirmed the
    tools in an earlier session” carries no `already|previously`, so it would be
    re-reddened. Closing this needs the pre-cue side to distinguish a
    confirmation clause from a concessive one structurally rather than by
    position, which is a different (and much wider) change than this pin claims.
    The three rows above are pinned as declared-GREEN so the residual is
    MEASURED and visible rather than hidden, and so that a future tightening
    shows up as a deliberate row flip instead of a surprise.
    """
    for statement in reversed(statements):
        # ⛔ STRIP THE DECORATION *BEFORE* FILTERING EMPTIES, or a bare markdown
        # marker becomes the governing clause and DISARMS the whole pre-cue
        # half: `"*"` survives `if c.strip()` and then `lstrip(_PRE_CUE_PREFIX)`
        # (which carries `*` and `>`) empties it, and `_SEQUENCER_FRAGMENT_RE`
        # cannot match the empty string, so the walk-back loop cannot pop it.
        # The function then returned `['']` — TRUTHY — as the governing clause,
        # which is the exact violation of its own docstring ("a clause with no
        # content word in it … is a FRAGMENT, not an instruction"):
        #
        #     RED    - Confirm the tools are actually there.\n- Restart …   (control)
        #     GREEN  * Confirm the tools are actually there.\n* Restart …   (measured)
        #     GREEN  > Confirm …\n> Restart …                              (measured)
        #
        # `>` blockquotes are used 19 times in `docs/quickstart-cloud.md` itself.
        clauses = [c for c in (_PRE_CUE_LIST_MARKER_RE.sub(
            "", raw.strip().lstrip(_PRE_CUE_PREFIX).strip()).strip()
                               for raw in re.split(r"[,;]", statement)) if c]
        # ⛔ SEARCH A DECORATION-FREE COPY, exactly as the post-cue side does: a
        # decorated sequencer (`**then**`, `*then*`, `` `then` ``) is the same
        # word to a reader, and matching the raw clause left
        # "Confirm the tools are actually there, **then** restart your client"
        # GREEN while its bare twin is RED (measured — the verdict turns on a
        # markdown marker, which is the failure mode `_PRE_CUE_PREFIX` exists to
        # close).
        while clauses and _is_continuation_fragment(
                _DECORATION_RE.sub(" ", clauses[-1])):
            clauses.pop()
        if clauses:
            return clauses[-1:], statement
    return [], ""


# ⛔ A PRE-CUE IMPERATIVE THAT DEFERS ITS OWN CHECK PAST THE RESTART IS FORWARD.
# "Check the tools after restarting. Restart your client, then confirm the tools
# appear." is self-consistent copy and was RED (measured) — form (f) cannot tell
# it from "Confirm the tools are actually there. Restart your client …".
# ⛔ AND THE DEFERRAL IS POSITIONAL-AGNOSTIC, not a trailing literal. The first
# cut matched only `after <the> restart(ing)` at the END of the clause, so every
# FRONTED spelling of the same relation was a false red (#3429) while its comment
# stated the rule generally: "Check the tools once your client has restarted.",
# "After restarting, check the tools are present." and "Once the client restarts,
# check the tools are present." all came back RED with the "inverts its own
# restart cue" message over copy that plainly puts the restart FIRST (measured,
# #14679 review at `18f99499d7`). The phrase is found ANYWHERE IN THE GRADED
# CLAUSE'S OWN STATEMENT now — not in the clause alone, and not in the paragraph
# (nor in a LATER statement: the walk-back can cross a `.`/`;` boundary, and the
# statement it lands in is the one whose deferral counts — `statements[-1]` was
# the literal `Then` in that case and was a false red)
# because where the deferral sits in the sentence does not change what it defers.
_DEFERRED_CHECK_RE = re.compile(
    r"\b(?:after|once|when|following|upon)\s+(?:\w+\s+){0,3}restart(?:ing|s|ed)?\b"
    r"|\bpost[\s-]restart\b", re.IGNORECASE)


def _defers_its_check_past_the_restart(clause: str) -> bool:
    return _DEFERRED_CHECK_RE.search(clause) is not None


# ⛔ A PRE-CUE CONDITION ON A CHECK MARKED AS ALREADY DONE IS A CONCESSION, NOT A
# DEFERRAL — and grading it is a FALSE RED on ordinary copy (#14679 review at
# `49ef237f4c5b`, P1-2). The pre-cue side grades the clause that GOVERNS the cue
# for the DEFERRAL reading — "AFTER confirming the tools are actually there.
# Restart your client …" — and that reading is TEMPORAL: the confirmation is
# ordered BEFORE the restart. A conditional `if`/`when` clause whose check carries
# a PRIOR-COMPLETION marker orders nothing; it states a state the reader may
# already be in, and the sentence then tells them to restart and confirm in the
# order the pin requires. Form (a) reaches it only by consuming `already` into
# its adverb slot, so the fix is here and not in the form (which is load-bearing
# post-cue, where `already` really does invert):
#
#     RED (false)  If you already checked the tools once, restart your client,
#                  then confirm the tools are actually there.
#                  -> graded clause: 'If you already checked the tools once'
#     RED (false)  If you already confirmed the tools in an earlier session,
#                  restart your client after a config change, then run a query.
#     GREEN        the same sentence joined with `;` — the `;` makes the
#                  concessive its OWN statement, so it never governed the cue.
#
# The prior-completion marker is what separates these from the DEFERRALS the
# pre-cue side must still grade, and both sides of that line are pinned in the
# table: `AFTER confirming the tools are actually there. Restart …` carries no
# marker and stays RED (the ONE pre-cue row form (a) is load-bearing for), and
# `If you confirm the tools are actually there, restart your client` — a check the
# reader is still being told to perform — stays RED as well.
# ⛔ THE OPENER IS RESTRICTED TO THE ARMS FORM (a) CAN REACH. `although`/`though`
# are the same concession to a reader, but form (a)'s connective set is
# `after|once|if|when`, so no row can make them load-bearing here — the rows for
# them were GREEN with the exemption neutralised, i.e. vacuous pins, and this file
# has deleted an arm for exactly that (see `prior to` at
# `_CUE_UNDER_INVERTING_SUBORDINATE_RE`). Their verdict is still correct; they are
# simply not graded by form (a) at all, so there is nothing to exempt.
_CONCESSION_OPENER_RE = re.compile(r"^(?:even\s+)?(?:if|when)\b", re.IGNORECASE)
# ⛔ THE MARKER IS NOT TWO ADVERBS: a PAST-TENSE PREDICATE marks prior completion
# just as plainly, and the earlier claim that widening was "ineffective" was FALSE
# — it was measured by a probe whose replacement never matched the source, so it
# changed nothing and "proved" the widening dead. Re-measured properly (#14679
# review round 10): admitting `checked|confirmed|verified` flips exactly the three
# past-tense concessive shapes RED→GREEN, flips 0 declared rows, and leaves the
# present-tense deferral `If you check the tools before restarting …` RED — so it
# is a one-line fix with no counter-cost, not a wider change. Pinned by three rows
# in the table's declared-residual block.
# ⛔ AND THE SESSION PHRASE IS CARRIED, IN `_PAST_TIME_ADVERBIAL` — it is NOT
# dropped. Cycle 55 wrote here that it had been: that measurement was taken under
# the UNGATED predicate arm, where `confirmed` alone matched `… in an earlier
# session`, and it was never re-measured after this commit gated that arm. Gated,
# the phrase does real work (`in a previous session` and `on the previous run` are
# now rows), so the claim was false about the code it described — corrected after
# review cycle 57 found the comment contradicting the regex.
#: The past-time adverbial a bare past-tense predicate NEEDS before it marks prior
#: completion. ⛔ WITHOUT IT THE PREDICATE EXEMPTS THE PRESENT-PERFECT CONDITIONAL,
#: which is form (a)'s CORE SHAPE — `When you have confirmed the tools are actually
#: there, restart your client.` went GREEN (measured at `b8c41f9f0c`, RED at
#: `bb518ab9a3`), i.e. an inversion shipping GREEN, which is the one direction this
#: file refuses (#14679 review cycle 56). Round 10's `checked|confirmed|verified`
#: was right about the class and wrong about the gate: the three shapes it fixed
#: all carry `before`/`earlier`/`last time`.
_PAST_TIME_ADVERBIAL = (
    # ⛔ `before` AND `prior` ARE NOT PAST-TIME ADVERBIALS HERE — they are THE
    # INVERSION. `When you have confirmed the tools PRIOR TO restarting, restart
    # your client.` says the confirmation is ordered BEFORE the restart, which is
    # the exact ordering #14676 forbids, and admitting `prior` let it pass as a
    # "completed concession" (measured, #14679 review cycle 57). The file says the
    # same thing one arm over: `until`/`prior to` ARE the same inversion.
    # ⛔ AND THE DETERMINER IS `an?|the`: `an?` alone excluded `in THE previous
    # session`, ordinary forward copy one article from a declared-GREEN row (false
    # RED, same review). `on the previous run` is the same phrase with another
    # preposition.
    # ⛔ …BUT A CLAUSE-FINAL `before`/`prior` IS the bare adverb ("if you checked
    # the tools before,"), which is prior completion and must stay GREEN — its
    # row is the peer lane's and it caught this. The bound is the clause END, not
    # the word: `prior TO restarting` keeps the inversion reading and stays graded.
    r"(?:earlier|last\s+time|previously"
    # ⛔ ONLY THE RESTART-ADJACENT READING IS THE INVERSION — cycle 57 dropped
    # mid-clause `before|prior` wholesale and re-redded ordinary troubleshooting
    # copy, turning the verdict on a SYNONYM (`before the upgrade` vs `earlier`),
    # which this file's doctrine forbids (measured, review cycle 58):
    #
    #     GREEN at d79d041845 → RED at cd1e01ad74
    #     If you verified the tools before the upgrade, restart your client, then confirm …
    #     If you confirmed the tools prior to this session, restart …
    #     If you checked the tools before the restart, …
    #
    # …while `prior TO restarting` / `before you restart` stay graded. `(?=\s*$)`
    # was also positional, so a parenthetical defeated it.
    # ⛔ RESTART-ADJACENCY IS A SET OF VERB SPELLINGS, not two tokens. `(to )?(you )?
    # restart` exempted `before PROCEEDING TO restart`, `before GOING TO restart`,
    # `before the client RESTARTS` and `before the client IS RESTARTED` — all
    # restart-adjacent readings that shipped GREEN (measured, review cycle 59) —
    # while the NOUN reading `before the restart` is a past-time adverbial and stays
    # GREEN, which is the line this bound draws: noun vs verb.
    r"|(?:before|prior)(?!\s+(?:(?:to|you)\s+)?(?:(?:proceeding|going|about)\s+to\s+)?(?:the\s+(?:client|session|editor|app|connector|server)\s+(?:is\s+)?)?restart)"
    # ⛔ AND THE PREPOSITION+ARTICLE ARE OPTIONAL: `last session` / `last run` are
    # ordinary past-time prose, and requiring `in the` made the verdict turn on an
    # article — the one-synonym flip this file forbids (measured, review cycle 59).
    r"|(?:(?:in|on)\s+(?:an?|the)\s+)?(?:earlier|previous|prior|last)\s+"
    r"(?:session|run|conversation))")

#: ⛔ AND THE PREDICATE ARM MUST COVER THE WHOLE `_PRE_CUE_VERB` CLASS, not three
#: of its spellings: `If you validated the tools earlier, …` and `If you made sure
#: the tools were there earlier, …` are the same concession as `checked`/`confirmed`
#: and were RED — forward copy, the false-RED direction (#14679 review cycle 56).
#: `double-checked` is covered by the `\w+-` prefix, which is the same hyphen
#: handling `_PRE_CUE_VERB` carries.
_PRIOR_COMPLETION_RE = re.compile(
    r"\b(?:already|previously)\b"
    # ⛔ PAST PARTICIPLES ONLY. A bare stem would admit the PRESENT-tense deferral
    # `If you check the tools before restarting, restart your client.` — a real
    # inversion, pinned RED, which the stem spelling turned GREEN (measured).
    r"|\b(?:\w+-)?(?:checked|confirmed|verified|validated|ensured)\b[^,;]*?"
    + _PAST_TIME_ADVERBIAL
    + r"|\bmade\s+(?:sure|certain)\b[^,;]*?" + _PAST_TIME_ADVERBIAL,
    re.IGNORECASE)


def _is_completed_concession(clause: str) -> bool:
    """A pre-cue CONDITION whose check is marked PRIOR-COMPLETE — a concession
    about a past session, not an instruction that defers the confirmation."""
    bare = _DECORATION_RE.sub(" ", clause).lstrip(_PRE_CUE_PREFIX)
    return bool(_CONCESSION_OPENER_RE.match(bare)
                and _PRIOR_COMPLETION_RE.search(bare))


# ⛔ AN `and <OPENER>` HALF IS A NEW CLAUSE WHEN ITS SUBORDINATE CONDITION IS
# FOLLOWED BY A MAIN CLAUSE OF ITS OWN — and that is the ONLY shape this file
# declines to fold into the cue's own conditional.
#
# The general question ("is this half a fresh statement, or the second conjunct of
# the condition the cue sits in?") is CLAUSE-LEVEL vs PHRASE-LEVEL COORDINATION,
# which is settled OUTSIDE regexes: the published conjunct-identification and
# comma-splice work decides it from parse trees and POS features. THREE cuts of a
# general `and <opener>` disambiguation were tried in this branch on that
# impossible premise and each shipped a fresh seam — a false PASS on the
# config-conjunction, a markdown hole, then a FALSE RED on `Restart your client
# after any upgrade, and once the tools appear run …` (#14679 reviews at
# `ed87479fbb05`, `6239dd051795`, `4f33aad5ff2f`; all measured).
#
# SO THE TEST IS DELIBERATELY NEITHER GENERAL NOR SYMMETRIC — it is SCOPED, and
# CLOSED over the two shapes it decides. ⛔ IT IS NOT MONOTONE IN DIRECTION: it
# closes a false PASS (GREEN→RED) on the `first`-anchor inversion AND a false RED
# (RED→GREEN) on ordinary forward copy, and BOTH are correct, each with its own
# row below. What it DOES guarantee is the property that matters — every shape it
# does not recognise keeps the graded verdict it has today, so the cut cannot ship
# a fresh seam: neutralising `_and_half_opens_its_own_clause` flips exactly the 6
# declared rows that pin these two forms, PLUS the one dedicated test that asserts
# the graded clause behind the `first`-anchor row — 7 failures and nothing else
# (measured at this head). ⛔ The previous wording said "6 … and nothing else",
# which the dedicated test added in the SAME commit had already made false: a
# count is a measurement, so it is stated as the number a run actually reports.
#
# Every verdict in this block is MEASURED at this head, not asserted — the
# previous cut of it claimed GREEN for row 2 while the row three thousand lines
# below pinned it `True` (RED), i.e. the same "this function asserts both
# verdicts" defect the review at `4f33aad5ff2f` closed as P2, reintroduced one
# round later in the prose. The rows are the guard; this block must agree with
# them.
#
#   GREEN  Restart your client (you should see no errors in the log), and once
#          the tools are listed run `tortoise mcp-status`.   (its OWN main clause)
#   RED    Restart your client, confirm the tools are actually there first, and
#          then run a query.   (a forward sequencer, own clause — and RED is the
#          WANTED verdict: with the fold declined, `first` stays clause-final, so
#          the P1-A false PASS is closed here rather than widened)
#   RED    Restart your client after confirming the config is right, and once the
#          tools are actually there.  (a BARE condition — no main clause, so the
#          half is the cue's second condition: folded in and graded)
#   RED    Restart your client after confirming the config is right, and the tools
#          are actually there.                       (same; form (a) across the seam)
#
# The residual is stated, not hidden: an imperative OUTSIDE the closed list below
# keeps the pre-fix verdict, which for this shape is FOLDED IN AND GRADED — a
# false RED of #3429's class (`… and once the tools are listed grep the log.`
# measures RED here). It already existed and is not made worse. ⛔ THE DIRECTION
# IS PART OF THE CLAIM: this block called it "a false PASS", which is the
# opposite of what the declared-residual row in the `line, inverted` table pins
# (`True` = RED) — the same prose-vs-row contradiction the review at
# `4f33aad5ff2f` closed as P2, one round after it was closed.
#
# ⛔ AND THE SHAPES THIS CUT DOES NOT REACH ARE DECLARED TOO, so a later reviewer
# does not have to rediscover them: a PRE-CUE `first` anchor followed by more
# clause is still GREEN where the sentence is an inversion — measured by direct
# call at this head, both GREEN and both unchanged by the fold (so their root is
# the pre-cue governing-clause walk, NOT this cut, and re-opening them is not a
# fifth arm here):
#
#   GREEN (want RED)  Confirm the tools are actually there first, and once they
#                     are listed restart your client.
#   GREEN (want RED)  Confirm the tools are actually there first, and if so
#                     restart your client.
#
# ⛔ THE SAME IS TRUE POST-CUE, and the block above would otherwise claim a
# closure it does not have, so it is declared here: the fold is declined for an
# `and`-half that opens `then|next|afterwards?`, or `once|when|whenever|after|
# if|unless|until|while|before` followed by a verb from the CLOSED list. A
# coordinated imperative with neither (`and run a query`, `and check the log`)
# and a forward adverb outside that sequencer set (`and finally run a query`)
# therefore still FOLD, so the clause-final `first` form (d) anchors on is gone
# and the sentence measures GREEN even though it INVERTS the cue:
#
#   GREEN (want RED)  Restart your client, confirm the tools are actually there
#                     first, and run a query.
#   GREEN (want RED)  Restart your client, confirm the tools are actually there
#                     first, and finally run a query.
#
# Both are pinned GREEN in the table below, so a future fix flips exactly those
# rows. Measured, not asserted — and identical with the fold NEUTRALISED, so the
# residual predates this cut and the cut does not make it worse.
#
# Closing it is not a one-word extension, and that is why it is declared rather
# than fixed here: declining the fold for a bare imperative trades this false
# PASS for one on the config-conjunction the fold exists for — `Restart your
# client after confirming the config is right, and check the tools are actually
# there.` measures RED today and GREEN under that extension (measured) — and
# gating the decline on the PREDECESSOR's `first` is the shape three earlier cuts
# of this mechanism shipped a seam for (`6239dd051795`). #14717 owns the
# scope-the-pin-down-vs-parse-it decision that would settle it.
#
# Six such spellings were measured and are recorded with their verdicts on
# #14717, which owns the "scope the pin down vs parse it" decision — and the two
# post-cue spellings above are recorded there with them.
_AND_FORWARD_SEQUENCER_RE = re.compile(
    r"(?i)^and\s+(?:then|next|afterwards?)\b")
_AND_SUBORDINATE_HALF_RE = re.compile(
    r"(?i)^and\s+(?:once|when|whenever|after|if|unless|until|while|before)\b")
# …and a CLOSED list of verbs a main clause opens with. A general verb test
# would be POS tagging, which this file does not do.
_AND_HALF_MAIN_CLAUSE_VERB_RE = re.compile(
    r"\b(?:run|runs|start|starts|restart|restarts|relaunch|relaunches|reload|reloads"
    r"|reopen|reopens|open|opens|close|closes|quit|quits|invoke|invokes"
    r"|execute|executes|call|calls|use|uses|try|tries|test|tests|query|queries"
    r"|inspect|inspects|navigate|navigates|click|clicks|type|types|save|saves"
    r"|write|writes|check|checks|confirm|confirms|verify|verifies|ensure|ensures"
    r"|see|sees|look|looks|add|adds|delete|deletes|remove|removes|install|installs"
    r"|configure|configures|enable|enables|disable|disables|copy|copies|paste|pastes"
    r"|send|sends|connect|connects|retry|retries|wait|waits|pick|picks|choose|chooses"
    r"|browse|browses|review|reviews|update|updates|upgrade|upgrades|reinstall"
    r"|reinstalls|refresh|refreshes|switch|switches)\b",
    re.IGNORECASE)


def _and_half_opens_its_own_clause(bare: str) -> bool:
    """True when a decoration-free `and …` half carries a main clause of its own.

    A BARE tools-presence condition (`and once the tools are actually there`) is
    the cue's second conjunct and is folded in — that is what keeps the
    config-conjunction rows graded. A condition whose predicate is followed by an
    imperative (`and once the tools are listed RUN …`) is a separate clause, which
    the cue's own sentence does not contain, so folding it in is what redded
    ordinary forward copy (#14679 review at `4f33aad5ff2f`, measured).
    """
    if _AND_FORWARD_SEQUENCER_RE.match(bare):
        return True
    return bool(_AND_SUBORDINATE_HALF_RE.match(bare)
                and _AND_HALF_MAIN_CLAUSE_VERB_RE.search(bare))


def _confirmation_predicate_setting_a_tool_list(clause: str) -> bool:
    """A confirmation predicate that is NOT the `list` inside the tool list.

    `_CONFIRM_PREDICATE` carries the VERB `list`, and `_TOOLS_OBJECT` carries the
    NOUN `tool list`, so the negated-cue arm matched its own object as the
    confirmation and redded plain presence copy:

        RED    You shouldn't restart your client unless the tool list is empty.
        GREEN  You shouldn't restart your client unless the tools are missing.

    Same defect class the file closes one arm over ("THE GUARD READS THE GRADED
    OBJECT, not its own copy of it"), and it was reachable only once `unless`
    landed: the `until` twin never had a `tool list` in it. The predicate must
    therefore sit OUTSIDE the tools-object span — a confirmation is a claim ABOUT
    the tool list, never the noun itself (measured, #14679 review cycle 47).
    """
    spans = [m.span() for m in _TOOLS_OBJECT_RE.finditer(clause)]
    for match in re.finditer(_CONFIRM_PREDICATE, clause, re.IGNORECASE):
        if any(start <= match.start() and match.end() <= end
               for start, end in spans):
            continue
        # ⛔ AND THE SAME WORD IS A NOUN AFTER A DETERMINER. `list` is BOTH the
        # verb in this closure and the docs' own name for the graded object, so
        # `… until the tools appear in THE LIST` was graded as an inversion — the
        # verdict turned on whether the sentence mentions a list, not on who
        # confirms anything. A pure ABSENCE claim (`unless the tools are missing
        # from the list`) was RED while its list-less twin is a declared-GREEN
        # row: the over-block direction this file grades P1 (#14679 review round
        # 7). The determiner test is the discriminative one, and it is monotone:
        # `until you can SEE the tools in the list` keeps `see` as its predicate
        # and stays RED.
        preceding = clause[:match.start()].rstrip("`'\u2019\u201d ")
        if re.search(r"(?i)\b(?:the|a|an|this|that|your|our|its|their|each|per)$",
                     preceding):
            continue
        return True
    return False


# ⛔ A COMMA-DELIMITED PREPOSITIONAL ASIDE, stripped from BOTH sides of the cue
# before either is split into clauses. One arm, one job: the split on `,` is what
# grades the inversion inside the cue's own statement, and an aside wedged between
# the predicate and its object must not supply that break. Both ends are required
# (an opening comma AND a closing comma), so a TRAILING aside and every ordinary
# `, then|and|but|so` clause are left to the split exactly as before.
#
# ⛔ THE PRE-CUE SIDE IS LOAD-BEARING AND WAS MISSING IN THE FIRST CUT. Applying
# the strip only post-cue left the IDENTICAL aside work on the other side of the
# cue: the aside became the clause that GOVERNS the cue, matched no inversion
# form, and form (f) — the pin's largest arm — was silently disarmed (#14679
# review at `1960427e`, measured). Inserting `, in the client's MCP/tool list,`
# turned the canonical declared-RED inversion GREEN, and 47 of the declared-RED
# rows had a GREEN twin at a comma boundary:
#
#     RED    Confirm the tools are actually there, then restart your client.
#     GREEN  Confirm the tools are actually there, in the client's MCP/tool
#            list, then restart your client.      (before this fix)
#     RED    … the same, after this fix
#
# Measured at `8c96f5de` with the POST-cue strip removed vs present: 616 of 616
# declared rows and the 41 non-parametrized `test_3232_*` / `test_5153_*` tests
# returned the same verdict, so the arm is additive and cannot flip a pinned row
# on its own; the rows that GAIN a verdict from it are the declared-RED rows
# pinned below it. The pre-cue half is pinned the same way (`…, in the client's
# MCP/tool list, then restart …`), so neither side can be dropped silently.
_ASIDE_RE = re.compile(
    r",\s*(?:in|on|under|from|per|within|inside|via|at)\b[^,]{1,80}?,\s*",
    re.IGNORECASE,
)
# ⛔ WHAT THE ASIDE IS REPLACED WITH DEPENDS ON WHAT FOLLOWS IT, and one
# substitution cannot serve both shapes (#14679 review at `eeb42a4f`, measured):
#
#   WEDGE      predicate , aside , its OWN OBJECT      → remove WHOLE, no comma
#              `after verifying, in the client's list, that the tools …`
#              must become `after verifying that the tools …`; keeping a comma
#              splits the predicate from its object again and the row goes GREEN.
#   BOUNDARY   clause , aside , the NEXT CLAUSE         → keep ONE comma
#              `… the config is right, in the client settings, but first confirm …`
#              must become `… is right, but first confirm …`; removing the comma
#              GLUES the two clauses (`right but first confirm`), so the `^`-
#              anchored forms (c) and (f) can never reach an inversion that is
#              still there. That was ONE substitution away from re-opening nine
#              declared-RED rows at `eeb42a4f` (measured: 9 GREEN twins over the
#              190 sweepable rows, 47 with both strips removed).
#
# The discriminator is the token AFTER the aside: an OBJECT continuation (a
# determiner, a pronoun, `that`, a backticked token) means the aside sat inside
# one clause; anything else begins a new clause. `*` is deliberately NOT an
# object continuation: `,**confirm the tools**` is a DECORATED IMPERATIVE, which
# is a new clause and must keep its comma.
_ASIDE_OBJECT_TAIL_RE = re.compile(
    r"(?:that|which|who|the|a|an|your|our|its|their|this|these|those"
    r"|they|them|it|`)\b",
    re.IGNORECASE,
)


def _strip_asides(text: str) -> str:
    """Remove comma-delimited prepositional asides, keeping the seam right.

    See the block above: a WEDGE is removed whole (the predicate must reconnect
    to its own object), a BOUNDARY keeps exactly one comma (the next clause must
    stay clause-initial).
    """
    def _repl(match: re.Match[str]) -> str:
        return " " if _ASIDE_OBJECT_TAIL_RE.match(text, match.end()) else ", "

    return _ASIDE_RE.sub(_repl, text)


def _paragraph_cue_order_violation(para: str) -> str | None:
    flat = " ".join(para.split())
    cue_at = _cue_start(flat)
    before, after = flat[:cue_at], flat[_cue_end(flat):]
    # ⛔ STATEMENT BREAKS FIRST, then clauses — the same reason the post-cue side
    # does it. A bare `[,.;]` cut inside `v1.2`/`e.g.` and ignored the em dash, so
    # a pre-cue form (f) inversion went GREEN ("Confirm the tools appear after the
    # v1.2 upgrade. Restart your client …", "Delete the old entry — but first
    # confirm the tools are actually there") while the comma twin was RED.
    # ⛔ THE ASIDE IS STRIPPED ON THIS SIDE TOO — see `_ASIDE_RE`'s block. Without
    # it the aside becomes the clause that governs the cue and form (f) never
    # fires, which is a false PASS of the canonical inversion (measured).
    before = _strip_asides(before)
    pre_statements = [s for s in _STATEMENT_BREAK_RE.split(
        before.lstrip(" \t,.;:—–-")) if s.strip()]
    # ⛔ AND EACH CLAUSE, not just the statement: the LAST clause is what form (f)
    # anchors on, so a decoration sitting after a comma (`Delete the old entry,
    # **confirm the tools are actually there**. Restart …`) hides the imperative
    # exactly as a leading one does (measured).
    pre_clauses, governing_statement = _cue_governing_clause(pre_statements)
    # The cue's OWN sentence, on the same reasoning: a `.` OR a `;` ends the
    # window (both begin a new statement — grading past them read a subordinate
    # clause that attaches to the NEXT verb as a deferral of the restart:
    # "Restart your client; once the tools are listed, run `tortoise mcp-status
    # --live`." was RED, measured, and its comma twin likewise). `,` stays a
    # clause break INSIDE the sentence, which is what keeps the inversion behind
    # a parenthetical comma ("… fully (on macOS, quit the menu-bar app) after
    # confirming the tools are there") visible.
    # ⛔ `lstrip` must NOT strip the terminator, or the window silently becomes
    # "the cue's sentence or the NEXT one" and a following sentence is graded —
    # measured, that is how "Restart your client. When you check the tools, …"
    # came back RED. Only the separating punctuation that continues the cue
    # (spaces, a comma) is stripped here.
    # An em dash ends it too: "Restart your client — once the tools are listed,
    # run `tortoise mcp-status --live`." is a separate statement (measured RED
    # while the dash was inside the window), exactly as the `;` twin is.
    post_sentence = _STATEMENT_BREAK_RE.split(after.lstrip(" \t,…"), maxsplit=1)[0]
    # ⛔ A COMMA-DELIMITED PREPOSITIONAL ASIDE IS NOT A CLAUSE BREAK. Splitting on
    # the comma cut the predicate away from its own object, so the canonical
    # inversion walked straight through the split that the comment above says is
    # what "keeps the inversion behind a parenthetical comma visible" (#14679
    # review at `8c96f5de`, measured — its bare twin is RED):
    #
    #     RED    Restart your client after verifying the tools are actually there.
    #     GREEN  Restart your client after verifying, in the client's MCP/tool
    #            list, that the tools are actually there.
    #     GREEN  … once you have confirmed, in the client's tool list, that …
    #     GREEN  … after checking, under MCP in the client settings, that …
    #
    # The aside is stripped BEFORE the split, so `after verifying that the tools
    # are actually there` is graded as the one clause it is. Only an aside that
    # BOTH opens with a preposition AND is closed by a following comma is removed,
    # so a trailing aside (no closing comma) and every `, then|and|but …` clause
    # are untouched — the declared rows are the guard on that (626 of them when
    # this arm landed at `8c96f5de`; the count names its sha because it rots).
    post_sentence = _strip_asides(post_sentence)
    post_clauses = [c.strip() for c in post_sentence.split(",")]
    # ⛔ AN `and`-LED CLAUSE CONTINUES THE PREVIOUS ONE — UNLESS IT OPENS ITS OWN
    # FORWARD CLAUSE. Splitting at the comma made the second half its own clause,
    # which the forward-sequencer skip then dropped whole — so "after confirming
    # the config is right, AND the tools are actually there" passed, while the
    # no-comma twin is RED (measured). Merging first lets the config-conjunction
    # rule see both halves.
    # ⛔ BUT `and once|and when|and then …` IS A SEPARATE FORWARD STATEMENT, and
    # folding it into its predecessor DESTROYS the sequencer the skip anchors on:
    # the merged clause no longer starts with `and`, so `_FORWARD_SEQUENCER_RE`
    # cannot skip it, `_negated_observation_only` saw a spliced remainder and
    # refused the skip, and forward copy went RED (#14679 review at
    # `49ef237f4c5b`, measured):
    #     GREEN  Restart your client (you should see no errors in the log), and
    #            once the tools are listed run `tortoise mcp-status`.
    #     RED    the same sentence, after the splice landed in `remainder`
    # ⛔ THE DISAMBIGUATION IS NOT REVERSED — IT IS SCOPED TO THE ONE DECIDABLE
    # SHAPE. `_and_half_opens_its_own_clause` declines the fold only when the half
    # carries a main clause of its own, and its own comment states why that is the
    # only safe cut and why it is monotone (it can only REMOVE a false red). Both
    # findings from the review at `4f33aad5ff2f` are closed by it and by nothing
    # else; the previous `GREEN (want RED)` residual it leaves is GONE, not
    # re-declared — that sentence measures RED again (`… and once the tools are
    # actually there.` is a bare condition, so it is folded in and graded).
    merged: list[str] = []
    for clause in post_clauses:
        # An `and`-led clause continues the previous ONE (below) UNLESS it carries
        # its own main clause, in which case it is appended as its own clause so
        # `_FORWARD_SEQUENCER_RE` can skip it and the predecessor keeps its
        # clause-final anchor (`… actually there first, and then run …`).
        bare = _DECORATION_RE.sub(" ", clause).lstrip(_PRE_CUE_PREFIX)
        if (merged
                and re.match(r"(?i)^and\b", bare)
                and _and_half_opens_its_own_clause(bare)):
            merged.append(clause)
        elif (merged and re.match(r"(?i)^and\b", bare)):
            merged[-1] = f"{merged[-1]}, {clause}"
        elif (merged
                # ⛔ SEARCH A DECORATION-FREE COPY. The tail anchor is `$`, so a
                # decorated sequencer (`**then**`, `*then*`, `` `then` ``,
                # `_then_`) never matched, the clause it introduces was graded
                # standalone, and correct copy went RED — 8 forms measured, each
                # a one-decoration edit of a DECLARED-GREEN row. Stripping for
                # the SEARCH only keeps the introducer text intact for the
                # rebuild below.
                and (introducer := _SEQUENCER_TAIL_RE.search(
                    _DECORATION_RE.sub(" ", merged[-1])))
                and not _ADVERSATIVE_LEAD_RE.match(
                    clause.lstrip(_PRE_CUE_PREFIX))):
            # ⛔ A SEQUENCER THAT INTRODUCES THE NEXT CLAUSE MAY SIT ON EITHER
            # SIDE OF ITS OWN COMMA, and both sides detach the clause it
            # introduces — after which the skip below cannot see it. A comma
            # after the sequencer ("…, then, once the tools are listed run …",
            # "…, and, …", "…, next, …", "…, afterwards, …") was measured
            # RED one comma at a time. So was a sequencer GLUED to the cue's own
            # clause — "Restart your client AND THEN, once the tools are listed,
            # …", "… and afterwards, …", "…, then afterwards, …" — because the
            # match here only ever admitted a clause that was NOTHING BUT the
            # sequencer, so an introducer sharing its clause with the cue text
            # was invisible. The introducer is APPENDED as its own clause, not
            # substituted for `merged[-1]`: substituting ERASES everything left
            # of the sequencer, and with it any inversion that clause carried
            # (measured — "…, but first confirm the tools are actually there AND
            # THEN, run …" went GREEN, a false PASS of form (c), and the same
            # append-free shape defeated forms (a) and (d)). `_PRE_CUE_PREFIX`
            # is stripped before the adversative test because the docs are
            # markdown — "**but first confirm …**" and "(but first …)" are the
            # same word to a reader and were GREEN (measured).
            merged.append(f"{introducer.group('seq').strip(' \t,')}, {clause}")
        else:
            merged.append(clause)
    # Post-cue clauses get the SAME decoration strip the pre-cue side gets
    # (`_PRE_CUE_PREFIX`). Without it form (c)'s `^` anchor never sees a bolded, quoted or
    # parenthesised adversative, so "and then, **but first confirm the tools are
    # actually there**, run …" stayed GREEN (measured).
    post_clauses = [c.strip().lstrip(_PRE_CUE_PREFIX).strip() for c in merged]
    for clause in pre_clauses:
        if (_confirmation_scopes_the_config(clause)
                or _negated_observation_only(clause)
                # ⛔ A COMPLETED CONCESSION IS NOT A DEFERRAL — see
                # `_is_completed_concession`. `If you already checked the tools
                # once, restart your client, then confirm the tools …` was a
                # false RED (measured, #14679 review at `49ef237f4c5b`).
                or _is_completed_concession(clause)
                # ⛔ THE DEFERRAL IS A PROPERTY OF THE CLAUSE'S STATEMENT, NOT OF
                # ITS LAST FRAGMENT. "After restarting, check the tools are
                # present." puts the deferral in an EARLIER clause of the same
                # statement, so a per-clause test read the bare imperative and
                # graded it — a false red (#3429). Checking the FINAL STATEMENT
                # is the narrow fix: it does not reach back past a statement
                # break, so "Check the logs after restarting. Confirm the tools
                # are actually there. Restart your client" still reds the
                # confirmation it must red (measured).
                or _defers_its_check_past_the_restart(governing_statement)):
            continue
        for pattern in _INVERSION_FORMS:
            # ⛔ STRIP THE DECORATION FOR FORM (f) ONLY, AND FROM THE WHOLE
            # CLAUSE. `_PRE_CUE_PREFIX` is applied at the clause's START only, so
            # once the leading run consumes `Now ` the markdown marker is
            # INTERIOR and the `^` anchor never reaches the verb: `Now **Confirm
            # the tools are actually there.** Restart your client, …` was GREEN
            # end-to-end on the real quickstart, and the four declared-RED
            # decorated rows flipped GREEN under every recognised lead (measured,
            # #14679 review at `31554d1424af`). The other forms match raw text by
            # design, so the substitution is scoped here rather than to `clause`.
            subject = clause
            if pattern is _PRE_CUE_ONLY:
                subject = _DECORATION_RE.sub(" ", clause).strip()
            if pattern.search(subject):
                return clause
    # ⛔ SEE `_CUE_UNDER_INVERTING_SUBORDINATE_RE`: when "before"/"until" governs
    # the cue, the confirmation that FOLLOWS it happens first, so form (f) — the
    # only form that grades a bare confirmation imperative, and the one the
    # post-cue side otherwise skips by design — must grade it here.
    # ⛔ AN EMPTY PRE-CUE STATEMENT MEANS THE BREAK SITS IMMEDIATELY BEFORE THE
    # CUE, so nothing can GOVERN it: "Before you continue: restart your client,
    # confirm the tools are actually there." leaves `["Before you continue", ""]`
    # and the empty fragment was dropped by the split's own filter, so the
    # `Before …` clause read as the cue's governor and form (f) graded a
    # comma-spliced confirmation that follows the restart — a forward shape RED
    # (#14679 review cycle 35, measured, with its `-` and `;` twins).
    cue_opens_a_statement = not _STATEMENT_BREAK_RE.split(
        before.lstrip(" \t,.;:—–-"))[-1].strip()
    cue_under_inverting_subordinate = bool(
        pre_statements
        and not cue_opens_a_statement
        and _CUE_UNDER_INVERTING_SUBORDINATE_RE.search(pre_statements[-1]))
    # ⛔ WHEN THE CUE IS GOVERNED AND A BREAK GLYPH SITS IMMEDIATELY AFTER IT, the
    # post-cue clause list is EMPTY, so form (f) — which the pre-cue side only
    # reaches through a post-cue clause — graded nothing and the confirmation the
    # governor inverts was never read: every non-comma twin of the declared-RED
    # "Before you restart your client, confirm the tools are actually there" was
    # GREEN (`;`, `:`, spaced `-`, `—`, `Until …:`), and the colon's own
    # rationale ("unambiguously forward") is falsified by exactly that shape
    # (#14679 review cycle 36, measured). The governance is known here, so the
    # statement after the break is graded in its place.
    # ⛔ ONE STATEMENT, NOT THE REST OF THE PARAGRAPH. The first cut of this
    # fallback took `_rest[1]` whole, so it graded PAST the statement break and
    # ahead of the merge above — and a statement that comes AFTER the governor's
    # step (`Before you restart your client: write the config. Afterwards,
    # confirm the tools …`) went RED, as did a sequencer-led one whose comma twin
    # is declared GREEN (#14679 review cycle 37, measured). Splitting to the
    # first statement and letting the loop's own `_FORWARD_SEQUENCER_RE` skip run
    # keeps both forward shapes GREEN.
    if cue_under_inverting_subordinate and not ",".join(post_clauses).strip():
        _rest = _STATEMENT_BREAK_RE.split(after.lstrip(" \t,…"), maxsplit=1)
        if len(_rest) > 1 and _rest[1].strip():
            _stmt = _STATEMENT_BREAK_RE.split(_rest[1], maxsplit=1)[0]
            _stmt = _stmt.lstrip(_PRE_CUE_PREFIX).strip()
            # ⛔ A SEQUENCER-LED STATEMENT STAYS ONE CLAUSE. Splitting it on its
            # comma detaches the introducer (`then`) into its own clause, after
            # which the loop's `_FORWARD_SEQUENCER_RE` skip has nothing to skip
            # and the confirmation behind it is graded standalone — RED, while the
            # comma twin of the same sentence is GREEN (#14679 review cycle 37,
            # measured).
            post_clauses = ([_stmt] if _FORWARD_SEQUENCER_RE.match(_stmt)
                            else [c.strip().lstrip(_PRE_CUE_PREFIX).strip()
                                  for c in _stmt.split(",")])
    # ⛔ SEARCH A DECORATION-FREE COPY, exactly as every other arm does
    # (`_PRE_CUE_PREFIX`, `_DECORATION_RE`, form (f)'s subject substitution). This
    # arm read the RAW pre-cue text, and its `\s*$` must land ON the cue, so any
    # markdown between the negation and the cue broke it (#14679 review cycle 50,
    # measured):
    #
    #     RED    Don't restart your client until you have confirmed the tools …
    #     GREEN  **Don't** restart your client until …          (measured)
    #     GREEN  Don't **restart your client** until …          (measured)
    #     GREEN  `Don't` restart your client until …            (measured)
    cue_is_negated = bool(_NEGATED_CUE_LEAD_RE.search(
        _DECORATION_RE.sub(" ", before)))
    for clause in post_clauses:
        # ⛔ A NEGATED CUE DEFERS THE RESTART TO ITS OWN `until`/`before` CLAUSE,
        # so a confirmation inside that clause happens FIRST — see
        # `_NEGATED_CUE_LEAD_RE`. Checked BEFORE the sequencer skip: the skip's
        # rationale (a sequencer continues the cue's predication forward) does
        # not hold for a restart that was told NOT to happen yet.
        if (cue_is_negated
                and _DEFERRING_CONNECTIVE_RE.match(clause)
                # ⛔ THE OBSERVATION SKIP APPLIES HERE TOO. Without it the `unless`
                # arm redded the presence-only shape it exists to leave GREEN:
                # `unless you see no tools in the list` — `see` is a member of
                # `_CONFIRM_PREDICATE`, so the arm fired before the skip the
                # post-cue loop applies three lines down, and
                # `_negated_observation_only` returns True on that very clause.
                and not _negated_observation_only(clause)
                # ⛔ AND THE PREDICATE MUST BE A CLAIM ABOUT THE OBJECT, not the
                # object's own noun (`tool list` vs the verb `list`).
                and _confirmation_predicate_setting_a_tool_list(clause)
                and _TOOLS_OBJECT_RE.search(clause)):
            return clause
        # A clause led by a FORWARD sequencer continues the cue's own
        # predication in the forward direction: "then once the tools are listed
        # run …", "and once the tools are listed, run …", "then after confirming
        # the tools are there, run …" all put the confirmation AFTER the restart
        # and were RED (measured). The sequencer is what says so; the
        # subordinator that follows it does not make it a deferral.
        # Leading punctuation must not hide the sequencer: "… (then once the
        # tools are listed, run …)" was RED (measured) while its bare twin was
        # GREEN. (The em-dash twin is protected by the STATEMENT BREAK instead —
        # the dash ends the window, so it never reaches this clause.)
        if (_FORWARD_SEQUENCER_RE.match(clause)
                or _confirmation_scopes_the_config(clause)
                or _negated_observation_only(clause)):
            continue
        for pattern in _INVERSION_FORMS:
            if pattern is _PRE_CUE_ONLY and not cue_under_inverting_subordinate:
                continue
            if pattern.search(clause):
                return clause
    return None


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
    non-per-harness restart statements. The INSTALLER cue is pinned to its
    exact string — that is where a harness's own mechanism lives (a new shell
    for Pi/Codex CLI, a registry + relaunch for Codex Desktop), so an edit that
    contradicts one is visible. The QUICKSTART half is pinned as an ORDER (the
    cue, then a tools-appear clause, with the inversion forms enumerated in
    `_INVERSION_FORMS`); its equality pin was retired in #14607's aftermath
    because it redded a legitimate reword — #3429's lesson, applied here. The
    `_are_exact` name is historical and now covers only the installer half."""
    src = _installer_text()
    assert ('echo "Next: restart your agent, then confirm the skills are '
            'listed:"') in src, (
        "the installer's closing cue must restart, THEN confirm/list the "
        "skills — the skills directory is read at agent start")
    quick = QUICKSTART.read_text(encoding="utf-8")
    # #3429's lesson, applied to THIS line: pin the ORDER, not one wording.
    # #14607 reworded "Restart your client. You should now have Tortoise's
    # tools …" into "Restart your client, then confirm the tools are actually
    # there — `tortoise_*` in the client's MCP/tool list …", which KEEPS the
    # requirement and strengthens it (it names the confirmation), yet the
    # equality pin called it a regression — the shape that trains the next
    # author to delete the pin instead of keeping the order (measured red at
    # `7107272801e5` on `main` and on this head alike).
    assert _RESTART_CUE_RE.search(quick), (
        "docs/quickstart-cloud.md must keep its restart cue — the config is "
        "read at client start, so a written config is not a loaded one")
    # ⛔ THE CLAUSE MUST BE IN THE CUE'S SENTENCE, NOT MERELY SOMEWHERE LATER IN
    # THE PARAGRAPH — that is what `_tools_appear_after_cue` bounds, and the
    # bound is pinned by `test_3232_the_tools_appear_bound_is_sentence_bounded`
    # (measured: reverting it to a paragraph-wide search reds a row there). A
    # COMMA does not bound it — "Restart your client after changing the config,
    # then confirm the tools …" is forward and must pass — so only a sentence
    # terminator does. The two-sentence reach comes from `parts[:2]`; the
    # leading `lstrip` only removes the separating COMMA/space that continues
    # the cue, never a terminator (stripping one would buy a second sentence). The paragraph's sentence-before
    # shape ("Confirm the tools are actually there. Restart your client …") is
    # graded by form (f) only when the cue's own or next sentence ALSO names the
    # tools; when it does not, THIS bound fails first, with the tools-appear
    # message rather than the inversion one (measured both ways).
    assert _tools_appear_after_cue(quick), (
        "docs/quickstart-cloud.md must keep a tools-appear clause AFTER its "
        "restart cue — 'restart' alone never tells the user what to look for, "
        "and a clause placed before the cue points them at tools the running "
        "session cannot have yet (#3232)")
    # ⛔ POSITION IS NOT ORDER — the index check above has a false PASS.
    # Measured on the landed pin: "Restart your client after confirming the
    # tools are actually there" places `tools` after the cue and is GREEN, while
    # INVERTING the property this pin exists to enforce — the client reads its
    # MCP config at START, so a user told to confirm the tools FIRST looks for
    # tools the running session cannot have loaded yet. No wording is pinned
    # here: a forward phrasing ("…, then confirm the tools …") still passes, so
    # #3429's lesson (pin the ORDER, not one reword) survives. "Restart your
    # client after changing the config, then confirm the tools …" stays GREEN
    # because form (a) requires the confirmation predicate in the connective's
    # OWN clause ("changing" is not one) — NOT because of the COMMA split.
    # ⛔ THAT SECOND HALF WAS TRUE WHEN WRITTEN AND IS NOW FALSE, measured: on the
    # then-55-row table (cycle 8) removing the comma split changed no row, but on
    # the current table it moved FIFTEEN rows (7 RED→GREEN and 8 GREEN→RED),
    # measured at cycle 54 by replacing
    # `[c.strip() for c in post_sentence.split(",")]` with
    # `[post_sentence.strip()]` → 16 failed, 719 passed on a 735-row table; the
    # sixteenth failure is
    # `test_3232_the_and_then_half_reds_for_the_first_anchor_not_by_accident`, a
    # test over those rows, not a row. ⛔ The earlier revision of this comment
    # said TWELVE rows / `13 failed, 583 passed` and attributed it to "this head"
    # while naming a different sha — `583 + 13 = 596` is not a count any head of
    # this file has collected, and the two GREEN→RED rows it was missing are the
    # cycle-50 `but before that` / `but before doing so` residuals, which landed
    # after that number was taken. A count is a measurement, so it is stated as
    # the number a run actually reports (#14679 review cycle 54, P2). ⛔ It is
    # load-bearing in BOTH directions, and the seven RED rows are NOT all one shape:
    # SEVEN declared-RED rows are LOST (a false PASS):
    #   1. `… and then, but first confirm the tools are actually there, run …`
    #   2. `… after confirming the config is right, [but first confirm the tools
    #      are actually there].` — the config-conjunction row
    #   3. the same merged introducer BOLDED: `… and then, **but first confirm …**`
    #   4. the same PARENTHESISED: `… and then, (but first confirm …)`
    #   5. `… and then, although first confirm the tools are actually there, run …`
    #      — the `although` twin cycle 29 added (so "all added by cycles 18-21"
    #      was stale by one).
    #   6. `Restart your client, confirm the tools are actually there first, and
    #      then run a query.` ⛔ NOT a merged introducer, and the reason it needs
    #      the split is different: it is form (d)'s CLAUSE-FINAL `$` anchor, which
    #      stops seeing `first` once the comma no longer separates the clause
    #      (the row's own comment; `test_3232_the_and_then_half_reds_for_the_
    #      first_anchor_not_by_accident` pins that mechanism). Rows 1-5 are
    #      merged-introducer shapes; row 6 is not. The previous wording said
    #      "FIVE / all five are merged-introducer shapes", which was true at
    #      `7d9427b2db21` when it was written — row 6 arrived later in
    #      `fae45f844fb89` — and is stale at this head.
    #   7. `Restart your client after confirming the config is right, IN THE CLIENT
    #      SETTINGS, but first confirm the tools are actually there.` — row 2's
    #      shape with an interposed locative, which row 2's own `[but first …]`
    #      enumeration does not cover. Measured at cycle 54 and added to the count
    #      above; it is the seventh, which is why "SIX / all one shape" was stale.
    # ⛔ …AND EIGHT declared-GREEN rows are FALSELY REDDENED without it (the inverse
    # error, #3429's class — a rule that reds legitimate copy is how a pin gets
    # deleted). The six whose SHAPE this comment enumerates:
    # `… after seeing no errors, then confirming …`, the
    # `(you should see no errors in the log), and once the tools are listed …` row,
    # `… seeing no errors in the log, and when the tools appear …`,
    # `… after confirming the config is right, and once the tools are listed …`,
    # `… after any upgrade, and once the tools appear …`, and its `**and**` twin…
    # …plus the two cycle-50 declared residuals (searched by their wording below,
    # not by a line number — cycle 54 wrote `:6442` here and its own 50-line
    # insertion had already invalidated the number it wrote, which is the
    # class this very block is about):
    # `Restart your client, but before that, confirm the tools are actually
    # there.` and its `but before doing so` twin, which landed after the six were
    # listed and are the other two the earlier count was missing.
    # So the split is what puts a later fragment in front of the forms AND what
    # keeps a cue from being read past its own clause. LOAD-BEARING; do not
    # delete it. A count is a measurement, so it is stated as the number a run
    # actually reports (`:3098`'s rule, applied to itself).
    #
    # What the graded set IS and IS NOT (#14679's reviews measured both):
    # - It is the SEVEN forms in `_INVERSION_FORMS`, each observed GREEN before
    #   its cut: (a) the "after/once/when/if + confirmation-predicate-the-tools"
    #   clause, (b) "having [first] confirmed the tools", (c) "but first …
    #   confirm the tools", (d) "confirm the tools … beforehand", (d′) "but
    #   confirm the tools are already there", (e) "[only] once the tools are
    #   there", and (f) a PRE-CUE "Confirm the tools …" sentence, which the
    #   post-cue scan cannot see. ⛔ (d′) is the one every enumeration used to
    #   omit while the tuple held seven — it is load-bearing, so a reader
    #   auditing the graded surface under-counted it.
    # - It is NOT an open-ended inversion detector. "Restart your client; having
    #   first confirmed the tools are actually there" (a BREAK GLYPH between the
    #   cue and the inversion marker) and "Restart your client, confirm the tools
    #   are actually there first, and run a query" (a coordinate imperative with
    #   no forward sequencer, folded into its predecessor) are deliberately NOT
    #   graded, and both are pinned as declared GREEN rows in the table — as is
    #   any other rephrasing outside those
    #   SEVEN (a, b, c, d, d′, e, and the pre-cue-only f). ⛔ The two examples that
    #   used to stand here (`… when the tools appear` and `after making sure the
    #   tools …`) are both GRADED TODAY (rows `Restart your client when the tools
    #   appear.` and `Restart your client after making sure the tools are
    #   actually there.`), so the block asserted an exemption its own table
    #   contradicts — a doc-vs-row disagreement of the kind that has already
    #   cost this file two review cycles (#14679 review at `1dedd33da07e`,
    #   measured). Widening a prose denylist is how both defects this pin fixes got
    #   in. The comment and the assert message claim only the seven, so neither
    #   outruns the measurement.
    # - A confirmation scoped to the CONFIG stays GREEN: the file is readable
    #   without a restart, so "Restart your client after verifying the config
    #   lists the tools" is forward copy, not an inversion — UNLESS the clause
    #   conjoins the tool list to it ("… the config is right and the tools are
    #   actually there"), which confirms both.
    violation = _restart_cue_order_violation(quick)
    assert violation is None, (
        f"docs/quickstart-cloud.md inverts its own restart cue: {violation!r} "
        "asks the user to confirm the tools this session cannot have loaded "
        "yet — the restart comes FIRST (#3232, #14676). Graded forms are the "
        "seven (a, b, c, d, d′, e, and the pre-cue-only f) enumerated in "
        "_INVERSION_FORMS; a pre-restart check of the CONFIG "
        "is not an inversion unless it also conjoins the tool list, a clause "
        "that opens with a forward sequencer is not a deferral, and rephrasings "
        "outside that closed set are out of scope by design.")


# Every row below is a MEASURED case: the GREEN rows are forward copy (a false
# red here is how a pin gets deleted), and each RED row was GREEN on the pre-fix
# pattern — so this table is the executable form of the review's mutation matrix,
# not a restatement of it.
def test_3232_the_restart_cue_is_found_case_insensitively():
    """The cue is located case-insensitively, as every form is.

    Every `_inversion_form` is IGNORECASE because "case carries no meaning in
    this prose", but the cue itself was matched with `in`, so a reword that keeps
    the required ORDER and merely lower-cases it mid-sentence stopped the
    paragraph being graded at all (measured — and it failed the "must keep its
    restart cue" assert on the real doc, which trains the next author to delete
    the cue rather than keep the order).
    """
    forward = ("Once the config is written, restart your client, then confirm "
               "the tools are actually there.")
    inverted = ("Once the config is written, RESTART YOUR CLIENT after "
                "confirming the tools are actually there.")
    assert _restart_cue_order_violation(forward) is None, forward
    assert _restart_cue_order_violation(inverted) is not None, inverted
    # …and the same doc with the cue lower-cased is graded, not skipped.
    # ⛔ THE TRANSFORMATION MUST LOWER THE CUE, not the first character of the
    # paragraph. `inverted[0].lower() + inverted[1:]` only rewrote "O" → "o", so
    # the cue in `doc_lower` was still `RESTART YOUR CLIENT` and this assertion
    # reddened under exactly the same mutation as the one above it (dropping
    # `re.IGNORECASE` from `_RESTART_CUE_RE`) — it pinned nothing the table did
    # not already pin (measured, #14679 review cycle 35). This is the dead-arm
    # class cycle 34 called out: "a comment asserting a measurement it cannot
    # have is worse than the dead arm".
    doc_lower = ("Lead-in.\n\n"
                 + inverted.replace("RESTART YOUR CLIENT", "restart your client")
                 + "\n")
    assert "RESTART YOUR CLIENT" not in doc_lower
    assert _restart_cue_order_violation(doc_lower) is not None, doc_lower


@pytest.mark.parametrize("line, inverted", [
    # ── forward: restart first, the confirmation follows ────────────────────
    ("Restart your client, then confirm the tools are actually there — "
     "`tortoise_*` in the client's tool list.", False),
    ("Restart your client after changing the config, then confirm the tools "
     "are actually there.", False),
    ("Restart your client. The tools then appear in the list.", False),
    ("Restart your client after the config change so it loads the tools.", False),
    ("Restart your client first, then confirm the tools are actually there.", False),
    ("Restart your client first and confirm the tools are actually there.", False),
    # ── forward, config-side: the file is readable without a restart ────────
    ("Restart your client after verifying the config lists the tools.", False),
    ("Restart your client after confirming the config file lists the tools.", False),
    ("Restart your client once you have checked the tools are enabled in your "
     "client settings.", False),
    # ── forward, RELATIVE PRONOUN: the restart is not conditioned on the tools —
    #    the tools follow it ──────────────────────────────────────────────────
    # ⛔ Every row here was RED before the connective's `(?!\s+(?:which|that)\b)`
    # (measured, #14679 review cycle 35): `which`/`that` stands for the RESTART
    # event, so "after which the tools are available" states the order the pin
    # requires and was rejected as an inversion. Removing that exclusion reds all
    # five; the already-GREEN `after which point …` row stays GREEN either way,
    # which is why the arm is written at the connective rather than by widening a
    # slot that no row constrains.
    ("Restart your client, after which the tools are available.", False),
    ("Restart your client, after which the tools are listed under MCP.", False),
    ("Restart your client, after that the tools are available.", False),
    ("Restart your client, after which your tools are available.", False),
    ("Restart your client, after which the tool list is complete.", False),
    ("Restart your client, after which point the tools are available.", False),
    # ── inversions: confirm FIRST, restart second ───────────────────────────
    ("Restart your client after confirming the tools are actually there.", True),
    ("Restart your client, but first confirm the tools are actually there.", True),
    ("Restart your client, having first confirmed the tools are actually "
     "there.", True),
    ("Restart your client, though you should confirm the tools are actually "
     "there beforehand.", True),
    # a MODAL between the subject and the predicate is one inserted word, and it
    # used to defeat form (a) end-to-end (measured GREEN before the slot added
    # at `_MODAL_TO_VERB`). ⛔ THESE ARE **NOT** 「twin pairs」 WITH THE ROWS ABOVE:
    # an earlier revision of this comment claimed each modal row sits beside a
    # declared modal-free twin of the same sentence, and no such row existed
    # (`grep` found only this comment) — the paired rows above differ by the
    # CONNECTIVE as well as the modal, and the modal-free twins that exist are
    # the `after you confirm …` / `after you see …` rows further down this table,
    # which are RED for that reason alone. Every alternative in `_MODAL_TO_VERB`
    # is pinned by ONE row here and by nothing else, which is what makes removing
    # it observable (measured: each dropped alternative flips its own row).
    ("Restart your client once you can confirm the tools are actually there.",
     True),
    ("Restart your client once you are able to confirm the tools are there.",
     True),
    ("Restart your client after you should confirm the tools are there.", True),
    ("Restart your client once you will confirm the tools are there.", True),
    ("Restart your client after you must confirm the tools are there.", True),
    # ⛔ ONE APOSTROPHE IS NOT A DIFFERENT SENTENCE. The `are able to` row above
    # was RED while these two contracted twins were GREEN (measured) — the file's
    # own doctrine spells the auxiliary out AND contracted, so both spellings are
    # rows here rather than one row and a false PASS.
    ("Restart your client once you're able to confirm the tools are there.",
     True),
    ("Restart your client once you\u2019re able to confirm the tools are there.",
     True),
    # …and an ADVERB between the modal and its predicate must not re-open the
    # same hole: `once you can actually confirm …` was GREEN while both
    # `can confirm` (above) and `actually confirm` are RED (measured).
    ("Restart your client once you can actually confirm the tools are there.",
     True),
    # …and the same slot closes the modal+OBSERVATION inconsistency: the
    # modal-free `after you see the tools in the list` row (further down this
    # table) is RED, so its `can see` spelling must be RED too (measured GREEN
    # before).
    ("Restart your client once you can see the tools in the list.", True),
    ("Restart your client only once the tools are actually there.", True),
    ("Restart your client, after confirming the tools are actually there.", True),
    # a pre-cue imperative that defers its OWN check past the restart is forward
    # copy, not a deferral of the restart (measured RED before the exemption):
    ("Check the tools after restarting. Restart your client, then confirm the "
     "tools appear.", False),
    # ── inversions that were GREEN on the first closed set ──────────────────
    # ⛔ OUT OF SCOPE BY DESIGN, recorded so it is not silently re-added: an
    # inversion stated in the sentence AFTER the cue ("Restart your client.
    # After confirming the tools are actually there.") is NOT graded. The window
    # closes at the cue's own statement because grading the next one redded
    # forward copy that must pass — "Restart your client. Confirm the tools are
    # actually there." is the shipped sentence re-punctuated, and "… Once the
    # tools are listed, run `tortoise mcp-status --live`." confirms AFTER the
    # restart (both measured RED). That is a real coverage cost and not an
    # equivalence worth claiming: an ADVERSATIVE inversion there ("… . But first
    # confirm the tools are actually there.") is measurably distinguishable from
    # forward copy and is still left ungraded, because keeping the window closed
    # is what protects the forward shapes.
    # a case-insensitive connective BEFORE the cue, and a pre-cue inversion that
    # follows a comma (the `^`-anchored forms died on the leading space):
    ("AFTER confirming the tools are actually there. Restart your client, then "
     "confirm the tools are actually there.", True),
    ("Delete the old entry, but first confirm the tools are actually there. "
     "Restart your client, then confirm the tools are actually there.", True),
    ("Note the following, confirm the tools are actually there. Restart your "
     "client, then confirm the tools are actually there.", True),
    # the perfect construction, which `verify\w*` could not match:
    ("Restart your client after you have verified the tools are actually "
     "there.", True),
    # a PRE-CUE confirmation: the same inversion, one sentence earlier.
    ("Confirm the tools are actually there. Restart your client, then confirm "
     "the tools are actually there.", True),
    # the config exclusion must not swallow a CONJUNCTION that confirms both:
    ("Restart your client after confirming the config is right and the tools "
     "are actually there.", True),
    # ── forward copy the wider first cut of form (a) reddened ───────────────
    ("Restart your client after adding the server so you can see the tools.",
     False),
    ("Restart your client if you cannot see the tools in the list.", False),
    ("Restart your client once the tools stop appearing.", False),
    ("Restart your client after upgrading then confirm the tools are actually "
     "there.", False),
    # ── the COMPLETIVE slot (#14679 review cycle 42) ─────────────────────────
    # `done` / `finished` / `through` name the completion of the confirmation the
    # connective defers to, so they order nothing. Each of these was GREEN before
    # the slot existed, and each drops to GREEN again if it is removed — the
    # class is one inserted word, the same one every other slot here closes.
    ("Restart your client once you're done confirming the tools are actually "
     "there.", True),
    ("Restart your client after you have finished confirming the tools are "
     "actually there.", True),
    ("Restart your client after you have already finished confirming the tools "
     "are actually there.", True),
    ("Restart your client once you're through confirming the tools are actually "
     "there.", True),
    # `completed` is the synonym the closure left out — the same one-word class as
    # `make sure` / `be sure` / `validate` in `_CONFIRM_PREDICATE` (#14679 review
    # at `fdaaa23d016c`, measured GREEN before, GREEN again if it is removed):
    ("Restart your client after you have completed confirming the tools are "
     "actually there.", True),
    # …and the slot still binds only to a predicate that FOLLOWS: a completive
    # over an unrelated object stays out (`completed the config and confirmed`).
    ("Restart your client after you have completed the config and confirmed the "
     "tools are actually there.", False),
    # …and the slot must NOT reach a completive over an UNRELATED object: the
    # predicate does not follow, so there is no deferred confirmation to grade.
    # Both are GREEN, and both would be false REDs if the slot were a bare
    # `\w+\s+` run.
    ("Restart your client after you have finished the config and can confirm "
     "the tools are actually there.", False),
    ("Restart your client once you're through with setup and confirm the tools "
     "are actually there.", False),
    # ── the FILE spelling of the config noun (#14679 review cycle 42) ────────
    # This block's own `_CONFIG_NOUN_RE` comment calls the config "a FILE" and
    # this repo's register uses that spelling, so the plainest form of a
    # file-side fact is forward copy — it was RED with a message claiming an
    # inversion the sentence does not contain. Both go RED if `file` is removed
    # from the noun arms.
    ("Restart your client after confirming the tools appear in the file.",
     False),
    ("Restart your client after verifying the file lists the tools.", False),
    # ── the DECLARED pre-cue residual (#14679 review cycle 42) ───────────────
    # `_cue_governing_clause` returns ONLY the clause that governs the cue, so an
    # ordinary imperative slipped between the confirmation and the cue discards
    # the confirmation and the paragraph is never graded. These three are GREEN
    # by that declared scope — pinned so the residual is measured rather than
    # hidden, and so a future tightening shows up as a deliberate row flip.
    ("Confirm the tools are actually there, save the file, then restart your "
     "client.", False),
    ("Confirm the tools are actually there, quit the editor, then restart your "
     "client.", False),
    ("Confirm the tools are actually there. Save the file. Then restart your "
     "client.", False),
    # the shipped sentence with its comma turned into a full stop — form (f) must
    # NOT grade a confirmation that follows the cue, or the pin reds the very
    # order it requires (measured RED before form (f) was made pre-cue-only):
    ("Restart your client. Confirm the tools are actually there.", False),
    ("Restart your client. Check the tool list for the `tortoise_*` tools.", False),
    # a concessive BEFORE the cue is not a deferral of it (measured RED when the
    # pre-cue side graded every earlier clause):
    ("The config is read once, at client start. Even if you confirmed the tools "
     "in an earlier session, a changed config is not picked up until the process "
     "reloads. Restart your client, then confirm the tools are actually there.",
     False),
    # ⛔ AND THE CONCESSIVE'S HALF-SENTENCE SPELLING IS THE SAME CLASS — the row
    # above is TWO sentences, so the concessive was its own statement and never
    # became the cue's governing clause. Comma-joined it DOES govern the cue and
    # was graded a DEFERRAL, which is a false RED (#14679 review at
    # `49ef237f4c5b`, P1-2; see `_is_completed_concession`). Every alternative in
    # `_CONCESSION_OPENER_RE` and `_PRIOR_COMPLETION_RE` carries a row, because an
    # alternative nothing pins is an alternative nobody can remove safely.
    ("If you already checked the tools once, restart your client, then confirm "
     "the tools are actually there.", False),
    ("Even if you already confirmed the tools in an earlier session, restart "
     "your client, then confirm the tools are actually there.", False),
    ("When you already checked the tools once, restart your client, then confirm "
     "the tools are actually there.", False),
    ("If you previously confirmed the tools are actually there, restart your "
     "client, then confirm them in the new session.", False),
    # ── the OTHER side of that line, which the exemption must not swallow ────
    # A pre-cue condition with NO prior-completion marker is a check the reader is
    # still being told to perform, so it is still ordered BEFORE the restart and
    # stays RED (both measured RED with `_is_completed_concession` in place):
    ("If you confirm the tools are actually there, restart your client, then "
     "confirm them again.", True),
    ("When you check the tools are actually there, restart your client, then "
     "confirm them again.", True),
    # …and the temporal deferral the exemption is scoped NOT to touch is already
    # pinned above ("a case-insensitive connective BEFORE the cue"): `AFTER
    # confirming the tools are actually there. Restart …` stays RED.
    # ── forward copy the SENTENCE-scoped post-cue window protects ──────────
    # a confirmation carried out in the sentence AFTER the cue is forward:
    ("Restart your client. Once the tools are listed, run `tortoise mcp-status "
     "--live`.", False),
    ("Restart your client. When you check the tools, `tortoise_*` will be "
     "there.", False),
    ("Restart your client now. The tools then appear in the tool list.", False),
    # a `;` or a forward sequencer continues the cue FORWARD — the subordinator
    # that follows is not a deferral of the restart (all measured RED before):
    ("Restart your client; once the tools are listed, run `tortoise mcp-status "
     "--live`.", False),
    ("Restart your client, then once the tools are listed run `tortoise "
     "mcp-status`.", False),
    ("Restart your client, then after confirming the tools are actually there, "
     "run `tortoise mcp-status`.", False),
    # ⛔ AND THE INTRODUCER MAY BE GLUED TO THE CUE'S OWN CLAUSE, not only stand
    # alone between two commas: "… and then, once …", "… and afterwards, …",
    # "…, then afterwards, …" were each measured RED, because the introducer
    # shared its clause with the cue text and so `_FORWARD_SEQUENCER_RE` — which
    # only ever looks at a clause's START — never saw it. Same meaning as the
    # three rows above; same verdict required. The adversative row below pins the
    # other half: the merged clause must NOT swallow an inversion's own marker.
    ("Restart your client and then, once the tools are listed, run `tortoise "
     "mcp-status`.", False),
    ("Restart your client and afterwards, once the tools are listed, run "
     "`tortoise mcp-status`.", False),
    ("Restart your client, then afterwards, once the tools are listed, run "
     "`tortoise mcp-status`.", False),
    ("Restart your client and then, but first confirm the tools are actually "
     "there, run `tortoise mcp-status`.", True),
    # ── a DECORATED or BRACKETED connective is the same word to a reader.
    # `_PRE_CUE_PREFIX` now carries `[`/`]` and the `and`-merge uses it, because
    # `**and** the tools …` failed `^and\b`, was NOT merged into the config
    # clause, and was then skipped by the forward sequencer — so a declared-RED
    # inversion shipped GREEN while its plain-comma twin was RED (measured):
    ("Restart your client after confirming the config is right, **and** the "
     "tools are actually there.", True),
    ("Restart your client after confirming the config is right, `and` the "
     "tools are actually there.", True),
    ("Restart your client after confirming the config is right, [but first "
     "confirm the tools are actually there].", True),
    # ── a DECORATED forward sequencer introduces the next clause just the same.
    # The tail anchor is `$`, so `**then**` never matched it, the clause it
    # introduces was graded standalone and correct copy went RED — 8 forms
    # measured, each a one-decoration edit of a DECLARED-GREEN row. The search
    # now runs on a `_DECORATION_RE`-cleared copy:
    ("Restart your client, **then**, once the tools are listed, run `tortoise "
     "mcp-status`.", False),
    ("Restart your client and **then**, once the tools are listed, run `tortoise "
     "mcp-status`.", False),
    # ── an INSTALLATION-side state is a file-side fact, not a session one.
    # `_INSTALL_SIDE_RE` — every one of these was RED with a message claiming an
    # inversion, while the noun form (`once the config lists the tools`) was
    # GREEN, which is the inconsistency the arm closes:
    ("Restart your client once the tools are installed.", False),
    ("Restart your client once the tools are configured.", False),
    # a quantifier between the object and its verb is the same install-side fact
    # (both were RED while the un-quantified twin above is GREEN):
    ("Restart your client once the tools are all installed.", False),
    ("Restart your client once the tools are both configured.", False),
    ("Restart your client once the tools are registered.", False),
    # ⛔ THE NEGATED-OBJECT ARM NEEDS AN `after|once` ROW TO BE OBSERVABLE. The
    # `if|when` rows above are GREEN for the different reason the observation
    # gate gives them, so deleting the arm left this suite fully green (measured
    # by the reviewer) — this is the only shape on which the arm itself bites:
    ("Restart your client after you have seen no tools in the list.", False),
    ("Restart your client once the tools are set up.", False),
    ("Restart your client once the tools are on the allowlist.", False),
    # ── a BARE config mention no longer excuses the clause: the free 40-char
    # window is gone, so a parenthetical aside cannot exempt a confirmation of
    # the RUNNING session's tools (measured GREEN before, which contradicted this
    # module's own rule that a bare mention is not enough):
    ("Restart your client after confirming the tools are actually there in the "
     "current session (the config is fine).", True),
    # ── the SPACED MID-STRING SINGLE HYPHEN arm is load-bearing and had no row.
    # Deleting `(?<=\s)-(?!-)\s+` from `_STATEMENT_BREAK_RE` flips exactly this
    # row GREEN — a silent false PASS of a pre-cue adversative (measured):
    ("Delete the old entry - but first confirm the tools are actually there. "
     "Restart your client, then confirm the tools are actually there.", True),
    # ── form (f)'s `check` and `verify` verb arms, which no row exercised (the
    # disclosure named only `see|list`), so removing either from the predicate
    # changed nothing (measured):
    ("Check the tools are actually there. Restart your client, then confirm "
     "the tools are actually there.", True),
    ("Verify the tools are actually there. Restart your client, then confirm "
     "the tools are actually there.", True),
    # ── an INSTALLATION-side state is an ANCHOR, not a short-circuit. Each of
    # these ALSO claims the running session's tools, so the conjunction rule
    # still grades it — they were GREEN while the install-side return
    # short-circuited the rule (measured), against a declared-RED row one
    # phrase away:
    ("Restart your client after confirming the tools are installed and the "
     "tools are actually there.", True),
    ("Restart your client after confirming the tools are installed and "
     "actually listed in the client.", True),
    ("Restart your client, but first confirm the tools are configured and they "
     "appear in the list.", True),
    ("Confirm the tools are installed and the tools are actually there. "
     "Restart your client, then confirm the tools are actually there.", True),
    # ── ONE doc-reference rule for EVERY form (`_NOT_A_DOC`). Before it was
    # shared, form (a) carried a shorter noun list than form (f) — so
    # `overview`/`summary`/`readme`/`appendix` were GREEN before the cue and RED
    # after it — and forms (b)/(c)/(d) had no exclusion at all, so a PARTICIPLE
    # doc reference ("the tools listed above") redded in every form (measured):
    ("Restart your client after you have checked the tools overview.", False),
    ("Restart your client, having checked the tools reference.", False),
    ("Restart your client, but first check the tools reference.", False),
    ("Restart your client, check the tools reference beforehand.", False),
    ("Check the tools listed above for the exact names. Restart your client, "
     "then confirm the tools are actually there.", False),
    ("See the tools available in this guide. Restart your client, then confirm "
     "the tools are actually there.", False),
    # ── THE LOCATIVE ARM IS THE NOUN FAMILY, NOT THREE PINNED PHRASINGS. It was
    # `in this guide|in the table|on this page`, so the verdict turned on the
    # determiner (`this`→`the`) and on which synonym for "doc" the writer used —
    # the same ONE-WORD class this file closes everywhere else. Every row below
    # was measured RED at the previous head and is GREEN here (6 for 6), and the
    # two RED controls under them did NOT move (§`_DOC_LOCATIVE`):
    ("Confirm the tools are described in the guide, then restart your client.",
     False),
    ("Confirm the tools are shown on the page, then restart your client.", False),
    ("Confirm the tools listed in the section, then restart your client.", False),
    ("Confirm the tools are listed in the documentation, then restart your "
     "client.", False),
    ("Check the tools listed in the reference. Then restart your client.", False),
    ("Restart your client after you have checked the tools listed in the "
     "walkthrough.", False),
    # ⛔ ……and the exclusion still does not reach a REAL inversion, nor the
    # `there` spelling that cycle 36 had to pull out of the copula arm:
    ("Restart your client, but first confirm the tools in the documentation.",
     True),
    ("Confirm the tools are available there, then restart your client.", True),
    # ── a NEGATED object is a SYMPTOM condition, the same class as the declared
    # GREEN `if you cannot see the tools in the list` — `no`/`none`/`zero` were
    # RED because negation by anything but `cannot` fell through the predicate:
    ("Restart your client if you see no `tortoise_*` tools.", False),
    ("Restart your client if you see zero tools in the list.", False),
    # ── …but a NEGATED INSTRUCTION is still an instruction. These were GREEN
    # while the negation rule was a PREDICATE-WIDE lookahead, which therefore
    # disabled forms (b)/(c)/(d) on every one of them (measured) — the false PASS
    # the scoped `_OBSERVATION_NEGATION_RE` skip exists to close:
    ("Restart your client, but first confirm no tools are missing.", True),
    ("Restart your client after confirming no tools are missing.", True),
    ("Restart your client, confirm no tools are missing beforehand.", True),
    # ── a session-presence conjunct AFTER the object. The conjunction rule scans
    # only between the config noun and the first `tools`, so this shape was
    # exempted while the same content with the conjunct BEFORE the object reds
    # (measured) — `_SESSION_AND_CONJUNCT_RE` closes it:
    ("Restart your client after confirming the config lists the tools and the "
     "tools are actually there.", True),
    # ── form (d′) is the seventh form and appeared in no enumeration; this row is
    # what proves it is load-bearing (removing (d′) flips it RED → GREEN):
    ("Restart your client, but confirm the tools are already there.", True),
    # ── form (f)'s `see` verb, which carried no discriminating row (every other
    # `See the tools …` row is doc-excluded and stayed GREEN either way):
    ("See the tools are actually there. Restart your client, then confirm the "
     "tools are actually there.", True),
    # ── `_DECORATION_RE` is word-internal-safe. An unconditional substitution
    # turned `check_then` into "check then" and MANUFACTURED the sequencer the
    # tail anchor needs, so this inverting clause was skipped (measured):
    ("Restart your client, run `check_then`, once the tools are listed, run "
     "`tortoise mcp-status`.", True),
    # ── the clause-strip set and the decoration-cleared copy carry the SAME
    # glyphs, or `[then]`/`{then}` were RED while `**then**` was GREEN:
    ("Restart your client, [then], once the tools are listed, run `tortoise "
     "mcp-status`.", False),
    ("Restart your client, {then}, once the tools are listed, run `tortoise "
     "mcp-status`.", False),
    # ── the PRE-CUE `after restarting` exemption, which changed no row before
    # this one existed — a pre-restart check that defers its own assertion past
    # the restart is forward copy:
    ("Confirm the tools are working after restarting. Restart your client, "
     "then confirm the tools are actually there.", False),
    # ── `_CONFIG_PREP_RE`'s POSITIVE half. The four install-side rows above are
    # satisfied by `_INSTALL_SIDE_RE` alone, so this arm had no row of its own;
    # `are listed` is not an install participle, so only this regex can exempt it:
    ("Restart your client after checking the tools are listed in your client "
     "settings.", False),
    ("Restart your client after checking the tools are listed in them.", False),
    # two file-side facts CONJOINED are still file-side (measured RED before the
    # pronoun arm existed), and so is a config fact named after the object:
    ("Restart your client after confirming the config file exists and the tools "
     "are enabled in it.", False),
    ("Restart your client after checking the installer ran and the config lists "
     "the tools.", False),
    # the row that makes `_PRE_CUE_ONLY`-is-pre-cue-only LOAD-BEARING: without
    # the identity skip this forward confirmation is graded by form (f)
    # (measured — removing the skip changed no row before this one existed).
    ("Restart your client, confirm the tools are actually there.", False),
    # ── inversions the previous cut passed ─────────────────────────────────
    # contracted auxiliary: the uncontracted twin was graded, this was not:
    ("Restart your client after you've verified the tools are actually there.",
     True),
    # ── cycle 18: an ORTHOGRAPHIC or POSITIONAL one-word change defeated a form,
    #    each measured GREEN where its twin is the declared RED row above ─────
    # the introducer must be APPENDED, not substituted for the clause it follows:
    # substituting erased that clause and with it the inversion it carried.
    ("Restart your client, but first confirm the tools are actually there and "
     "then, run `tortoise mcp-status`.", True),
    ("Restart your client after confirming the tools are actually there and "
     "then, run `tortoise mcp-status`.", True),
    ("Restart your client, confirm the tools are actually there beforehand and "
     "then, run `tortoise mcp-status`.", True),
    # markdown decoration is not a hedge — `_PRE_CUE_PREFIX` before the
    # adversative test AND before form (c)'s `^` anchor:
    ("Restart your client and then, **but first confirm the tools are actually "
     "there**, run `tortoise mcp-status`.", True),
    ("Restart your client and then, (but first confirm the tools are actually "
     "there), run `tortoise mcp-status`.", True),
    # one inserted ADVERB between the auxiliary and the predicate (form (a)):
    ("Restart your client after you have first verified the tools are actually "
     "there.", True),
    ("Restart your client after you have already verified the tools are actually "
     "there.", True),
    ("Restart your client after you first confirm the tools are there.", True),
    # the pre-restart marker one word over (form (d)): `first` trailing the
    # object is the same order claim as `beforehand` — but only CLAUSE-FINAL,
    # and `already` only under an adversative, because `already` asserts a STATE
    # ("confirm the tools are already there" = after the restart) and reding it
    # turned a declared-GREEN row RED under a one-word change. Each shape below
    # was measured; the GREEN ones are the false blocks that fix exists to avoid.
    ("Restart your client, confirm the tools are actually there first.", True),
    ("Restart your client, but confirm the tools are actually there first.",
     True),
    ("Restart your client, confirm the tools are already there.", False),
    ("Restart your client, check the tools are already listed.", False),
    ("Restart your client to confirm the tools are already in the list.",
     False),
    ("Restart your client, give it a moment, and check that the tools are "
     "already enabled.", False),
    ("Restart your client, and once it is back, confirm the tools are already "
     "in the list.", False),
    ("Restart your client, verify the tools are present first thing.", False),
    # …and the adverb slot in form (a) is an OPEN class, because the connective
    # is what carries the order — a closed four-word list only re-opened the
    # hole one adverb over (each of these measured GREEN with the closed list):
    ("Restart your client after you have previously verified the tools are "
     "actually there.", True),
    ("Restart your client after you have recently confirmed the tools are "
     "actually there.", True),
    ("Restart your client after you have thoroughly verified the tools are "
     "actually there.", True),
    ("Restart your client after you have also already verified the tools are "
     "actually there.", True),
    # …and a BARE (non-`-ly`) time adverb is in the same slot: `now` / `still`
    # are adverbs in this file's own `_ABSENCE_ADVERB_RUN`, so omitting them here
    # was the `file` / `following|upon` class — a one-word false PASS. Each of
    # these measured GREEN before the bare list carried them, and each is GREEN
    # again if they are removed from it (measured at `fdaaa23d016c`).
    ("Restart your client after you have now confirmed the tools are actually "
     "there.", True),
    ("Restart your client once you've now confirmed the tools are actually "
     "there.", True),
    ("Restart your client after you have still confirmed the tools are actually "
     "there.", True),
    # …while the adverb does NOT widen the CONFIG exclusion it sits in front of:
    # a file-side fact is forward copy whatever adverb precedes the predicate.
    ("Restart your client after you have now confirmed the config lists the "
     "tools.", False),
    # ── cycle 20: an OPEN class must be bounded on BOTH sides ──────────────
    # a RESTRICTIVE / FREQUENCY adverb is not a manner adverb, and it changes the
    # sense: this copy describes the SYMPTOM that motivates a restart, which the
    # table above already treats as forward ("if you cannot see the tools …").
    # All four were RED under the unfiltered `\w+ly` (measured):
    ("Restart your client if you only see the tools after a second restart.",
     False),
    ("Restart your client if you have lately seen the tools vanish from the "
     "list.", False),
    ("Restart your client if you have rarely seen the tools appear at all.",
     False),
    ("Restart your client if you have barely seen the tools appear.", False),
    # …and the exclusion belongs on the CONDITIONAL connectives, not on `if`
    # alone: `when` introduces a condition exactly as `if` does, so the `when`
    # twin of the row above was refused when the arm named only `if` (measured):
    ("Restart your client when you only see the tools after a second restart.",
     False),
    ("Restart your client when you have rarely seen the tools appear at all.",
     False),
    # …and the gate is the VERB, not an adverb list: any adverb in a CONDITIONAL
    # clause whose verb is an observation is the symptom, not an inversion. The
    # forms below are the inflections `_CONFIRM_PREDICATE` itself admits — a gate
    # listing only the base forms graded `rarely LOOKED for the tools` and
    # `simply LISTED the tools` as confirmations (measured), which is the same
    # one-word-substitution defect one inflection over:
    ("Restart your client if you see the tools from a previous session.", False),
    # ⛔ THE CONTRACTED/PROGRESSIVE TWINS OF THE ROW ABOVE ARE THE SAME SENTENCE, and
    # they were RED (measured) because the observation gate spelled `see(?:s)?` with
    # no `seeing` while it admitted `you're` in the subject slot — so one apostrophe
    # flipped a declared-GREEN symptom clause to RED, and `you are … seeing` stayed
    # GREEN because the uncontracted subject could not reach the progressive. The
    # gate now carries the form and these three rows hold it.
    ("Restart your client if you're seeing the tools from a previous session.",
     False),
    ("Restart your client if you\u2019re seeing the tools from a previous session.",
     False),
    ("Restart your client if you're only seeing the tools after a second "
     "restart.", False),
    ("Restart your client when you're seeing the tools listed twice.", False),
    ("Restart your client if you have rarely looked for the tools at all.",
     False),
    ("Restart your client if you simply listed the tools in the panel.", False),
    # …but an observation followed by a CONFIRMATION is still an inversion — the
    # gate must not blind the whole clause (both were GREEN before this):
    ("Restart your client if you have looked in the panel and confirmed the "
     "tools are actually there.", True),
    ("Restart your client if you have listed the tools in the panel and "
     "confirmed they appear.", True),
    ("Restart your client when you frequently see the tools disappear.", False),
    ("Restart your client if you occasionally see the tools disappear from the "
     "list.", False),
    # …while the same adverb in front of a CONFIRMATION predicate stays an
    # inversion, `when` included (all three were GREEN under the adverb list):
    ("Restart your client when you have only recently confirmed the tools are "
     "actually there.", True),
    ("Restart your client when you have only just verified the tools are "
     "actually there.", True),
    ("Restart your client when you merely confirmed the tools are actually "
     "there.", True),
    # …and a degree intensifier before the adverb is the same adverb:
    ("Restart your client after you have very recently confirmed the tools "
     "are actually there.", True),
    ("Restart your client when you have only very recently confirmed the "
     "tools are actually there.", True),
    # …and the exclusion is scoped to the CONDITIONAL connectives (`if|when`),
    # because there the adverb changes the sense of the CONDITION; under
    # `after|once` the connective itself already fixes the order. A blanket
    # exclusion lost these (both were RED):
    ("Restart your client after you have only recently confirmed the tools are "
     "actually there.", True),
    ("Restart your client after you have merely confirmed the tools are "
     "actually there.", True),
    # markdown decoration on the trailing marker is not a hedge (the same
    # doctrine as the bolded adversative row above), and all three were GREEN:
    ("Restart your client, confirm the tools are actually there **first**.",
     True),
    ("Restart your client, confirm the tools are actually there `first`.", True),
    ("Restart your client, confirm the tools are actually there [first].", True),
    # a DOC cross-reference is not a presence check — the rule form (f) already
    # carries, now shared by form (a) (both were RED):
    ("Restart your client after you have quickly checked the tools reference.",
     False),
    ("Restart your client after you have carefully checked the tools section.",
     False),
    ("Restart your client after you have checked the tools reference.", False),
    # …and the doc-noun set must be the SAME on both sides of the cue: form (f)
    # carries nine nouns, form (a) carried six, so the identical clause flipped
    # verdict across the cue for these four (each RED post-cue, GREEN pre-cue):
    ("Restart your client after checking the tools overview for the full list.",
     False),
    ("Restart your client after checking the tools summary.", False),
    ("Restart your client after checking the tools readme.", False),
    ("Restart your client after checking the tools appendix.", False),
    # a file-side noun is the same noun in either spelling, and the OWNER may be a
    # named client — this repo's quickstart is organised per named client, so
    # `config` GREEN / `.mcp.json` RED and `client settings` GREEN / `Cursor
    # settings` RED were both false blocks (all four measured GREEN now):
    ("Restart your client after checking the tools are listed in `.mcp.json`.",
     False),
    ("Restart your client after checking the tools are listed in your Cursor "
     "settings.", False),
    ("Restart your client after checking the tools are listed in your Claude "
     "Code settings.", False),
    # …and the owner may be DECORATED or POSSESSIVE, which is how this repo's own
    # quickstart writes client names (`**Cursor**`, `**Claude Code**`); all three
    # were RED while the bare twin above is GREEN:
    ("Restart your client after checking the tools are listed in your "
     "**Cursor** settings.", False),
    ("Restart your client after checking the tools are listed in **Cursor** "
     "settings.", False),
    ("Restart your client after checking the tools are listed in Cursor's "
     "settings.", False),
    # a TWO-TOKEN client name takes the possessive after `Code`, not before it
    # (measured RED while its one-token twin above is GREEN):
    ("Restart your client after checking the tools are listed in Claude "
     "Code's settings.", False),
    # …and decoration and the possessive COMPOSE (both were RED while each alone
    # was GREEN):
    ("Restart your client after checking the tools are listed in "
     "**Cursor**'s settings.", False),
    ("Restart your client after checking the tools are listed in "
     "**Claude Code**'s settings.", False),
    # a quantifier between the object and its verb is the same claim, and form
    # (e)'s copula arm already admitted it (measured GREEN before):
    ("Restart your client once the tools all appear.", True),
    # a leading ellipsis hides the adversative unless it is stripped like every
    # other glyph (both were GREEN):
    ("Restart your client … but first confirm the tools are actually there.",
     True),
    ("Restart your client, … but first confirm the tools are actually there.",
     True),
    # a hyphenated predicate is the same predicate (both were GREEN):
    ("Restart your client after you have double-checked the tools are "
     "actually there.", True),
    ("Restart your client after you have re-checked the tools are actually "
     "there.", True),
    # a determiner/modifier other than a bare `the` in form (e):
    ("Restart your client once all the tools appear.", True),
    ("Restart your client once your tools appear.", True),
    # a capitalised OBJECT must still route through the config exclusion (its
    # own lookup was case-sensitive — measured FALSE RED):
    ("Restart your client after verifying the config lists the Tools.", False),
    ("Restart your client once you have checked the Tools are enabled in your "
     "client settings.", False),
    # ⛔ DOTTED TOKENS ARE NOT STATEMENT BREAKS — `v1.2`, `e.g.`, `.mcp.json`.
    # The window split on a bare `[.;—]`, so a version number ended it and this
    # inversion slipped through while the `e.g.` shapes below went RED (measured;
    # the form wildcards also stopped at the dot, so both halves needed fixing):
    ("Restart your client after confirming release v1.2 works and the tools are "
     "actually there.", True),
    ("Restart your client, e.g. quit and reopen the app, then confirm the tools "
     "are there.", False),
    ("Restart your client (e.g. quit and reopen), then confirm the tools are in "
     "the list.", False),
    # the perfect tense of the REMEDY shape must stay GREEN: `have`/`has` were in
    # form (e)'s predicate set and redded it (measured), which is why they are not.
    ("Restart your client once the tools have stopped appearing.", False),
    # the typographic apostrophe, which no other row exercised:
    ("Restart your client after you\u2019ve verified the tools are actually there.",
     True),
    # the other two forward sequencers, and the `.mcp.json` config noun — each
    # was an unexercised arm before these rows existed:
    ("Restart your client, next check that the tools appear, then run "
     "`tortoise mcp-status`.", False),
    ("Restart your client; afterwards the tools appear in the list — nothing "
     "to do.", False),
    ("Restart your client after editing `.mcp.json` so the tools are listed.",
     False),
    # …and the `.mcp.json` arm is load-bearing only through a CONFIRMATION whose
    # predicate reaches the tool list — "editing" is not a predicate, so the row
    # above passes without the exclusion. Measured RED before the arm could match:
    ("Restart your client after checking the `.mcp.json` lists the tools.",
     False),
    ("Restart your client after checking the config lists the tools.", False),
    # glyph and spelling variants of shapes already declared GREEN (each was RED
    # on a one-character edit of a row above — measured). A dash or hyphen does
    # NOT need a following space, and a comma may sit right after a forward
    # sequencer ("then, once the tools are listed, run …"):
    ("Restart your client—once the tools are listed, run `tortoise mcp-status`.",
     False),
    ("Restart your client —once the tools are listed, run `tortoise "
     "mcp-status`.", False),
    ("Restart your client–once the tools are listed, run `tortoise mcp-status`.",
     False),
    ("Restart your client--once the tools are listed, run `tortoise "
     "mcp-status`.", False),
    ("Restart your client -- once the tools are listed, run `tortoise "
     "mcp-status`.", False),
    # a spaced double hyphen MID-string needs its own arm (the leading one above is
    # matched by `^-\s*`, so only this row exercises the standalone `--` arm):
    ("Restart your client, re-run the bootstrap -- once the tools are "
     "listed, run `tortoise mcp-status`.", False),
    ("Restart your client, then, once the tools are listed, run `tortoise "
     "mcp-status`.", False),
    ("Restart your client, and, once the tools are listed run `tortoise "
     "mcp-status`.", False),
    ("Restart your client, next, once the tools are listed run `tortoise "
     "mcp-status`.", False),
    ("Restart your client, afterwards, once the tools are listed run `tortoise "
     "mcp-status`.", False),
    # a spaced `--flag` is NOT a break: the pre-fix arm split the window in front
    # of the clause that inverts the order, so this pair was GREEN/RED the wrong
    # way round (measured). Both twins are RED now, and both forward twins GREEN:
    ("Restart your client, run `brew install --cask x`, once the tools are "
     "actually there.", True),
    ("Restart your client, run `brew install --cask x`, then confirm the tools "
     "are there.", False),
    # a single-dash SHORT FLAG must not split the window either (`\s*` after the
    # hyphen used to match empty, so `-q` behaved like a dash — measured):
    ("Restart your client, run `pytest -q`, once the tools are actually there.",
     True),
    ("Restart your client, run `pytest -q`, then confirm the tools are there.",
     False),
    # the unspaced pre-cue dash twin, which was a false PASS before the break fix:
    ("Delete the old entry—but first confirm the tools are actually there. "
     "Restart your client, then confirm the tools are actually there.", True),
    ("Restart your client – once the tools are listed, run `tortoise "
     "mcp-status`.", False),
    ("Restart your client - once the tools are listed, run `tortoise "
     "mcp-status`.", False),
    ("Restart your client, afterward once the tools are listed run `tortoise "
     "mcp-status`.", False),
    # ── rows for the remaining named arms ────────────────────────────────────
    # form (c)'s `however`/`yet` synonyms, and the config-prep pronouns other than
    # `it` (both were live but had no row — measured by narrowing each arm):
    ("Restart your client, however first confirm the tools are actually there.",
     True),
    ("Restart your client after checking the tools are enabled in them.",
     False),
    # form (f)'s `please` and its `see|list` verbs, which begin a pre-cue
    # imperative — nothing else in the table fires those alternatives:
    ("Please confirm the tools are actually there. Restart your client, then "
     "confirm the tools are actually there.", True),
    ("Please list the tools you see. Restart your client, then confirm the "
     "tools are actually there.", True),
    # …and the SAME instruction in the repo's own spelling of the list — one
    # inserted token defeated the object slot (measured):
    ("Please list the `tortoise_*` tools you see. Restart your client, then "
     "confirm the tools are actually there.", True),
    ("Please list the MCP tools you see. Restart your client, then confirm "
     "the tools are actually there.", True),
    ("Please list all the `tortoise_*` tools you see. Restart your client, "
     "then confirm the tools are actually there.", True),
    # ── form (f)'s LEADING-ADVERB arm, and the decorations that hide a pre-cue
    # imperative from the `^` anchor. Every row above was GREEN before the cut
    # that made it RED (measured), and each mechanism below flips its own rows
    # when removed:
    #   – the adverb slot: `First …` / `Please first …` (one inserted word);
    #   – `_PRE_CUE_PREFIX`: bold / italic / code / blockquote / list-marker
    #     forms, which is how the quickstart actually writes an instruction the
    #     reader must act on — including the same decoration after a COMMA, which
    #     is why the strip is per-CLAUSE;
    #   – the statement-break lookahead (below, RED side): a full stop that ends
    #     one sentence INSIDE emphasis was not a break at all.
    ("First confirm the tools are actually there. Restart your client, then "
     "confirm the tools are actually there.", True),
    ("Please first confirm the tools are actually there. Restart your client, "
     "then confirm the tools are actually there.", True),
    ("**Confirm the tools are actually there.** Restart your client, then "
     "confirm the tools are actually there.", True),
    ("*Confirm the tools are actually there.* Restart your client, then "
     "confirm the tools are actually there.", True),
    ("`Confirm the tools are actually there.` Restart your client, then "
     "confirm the tools are actually there.", True),
    ("> Confirm the tools are actually there. Restart your client, then "
     "confirm the tools are actually there.", True),
    ("- **Confirm the tools are actually there.** Restart your client, then "
     "confirm the tools are actually there.", True),
    ("Delete the old entry, **confirm the tools are actually there**. Restart "
     "your client, then confirm the tools are actually there.", True),
    # ── the statement-break lookahead's EMPHASIS arm, RED side: without `*` in
    # the lookahead the two sentences never split, so the last pre-cue statement
    # began at `Delete` and the inversion shipped GREEN (measured):
    ("Delete the old entry. **Confirm the tools are actually there.** Restart "
     "your client, then confirm the tools are actually there.", True),
    # ── form (f)'s DOC-CROSS-REFERENCE exclusion: forward copy that points at a
    # documentation artifact rather than at the running session's tool list.
    # Each was RED (a false red asserting an inversion the copy does not
    # contain) before the lookahead existed — the failure mode that gets a pin
    # deleted (#3429):
    ("See the tools section below for the full list. Restart your client, then "
     "confirm the tools are actually there.", False),
    ("Check the tools reference for the full tool list. Restart your client, "
     "then confirm the tools are actually there.", False),
    # …and the `list` verb must exclude a doc object the same way `check` does,
    # or the two forms disagree on the same sentence (measured):
    ("List the tools reference for the full tool list. Restart your client, "
     "then confirm the tools are actually there.", False),
    ("List the tools documentation. Restart your client, then confirm the "
     "tools are actually there.", False),
    ("Ensure the tools table is up to date. Restart your client, then confirm "
     "the tools are actually there.", False),
    ("See the tools guide on MCP. Restart your client, then confirm the tools "
     "are actually there.", False),
    ("See the tools docs for the full list. Restart your client, then confirm "
     "the tools are actually there.", False),
    ("See the tools page on the wiki. Restart your client, then confirm the "
     "tools are actually there.", False),
    # ⛔ `guide` IS A SEPARATE ALTERNATIVE of `_DOC_NOUN` too, and the four rows
    # above are spared by OTHER nouns (`documentation`, `table`, `docs`, `page`)
    # — so removing `guide` alone flipped 0 declared rows while still sparing
    # real copy (#14679 review at `49ef237f4c5b`, P2). This row is that copy, and
    # it goes RED the moment `guide` leaves `_DOC_NOUN` (measured). It is the
    # post-cue spelling on purpose: the pre-cue `See the tools guide on MCP.` row
    # above is spared by a different arm, which is exactly how `guide` went
    # unpinned in the first place.
    ("Restart your client after checking the tools guide.", False),
    # ⛔ …but `list` is NOT a reference noun: "the tools list" is the SESSION's
    # list, so this stays an inversion. Without this row, adding `list` to the
    # exclusion set above would be an unmeasured regression — it is also what
    # keeps form (f)'s `list`-verb arm reachable.
    ("Confirm the tools list is complete. Restart your client, then confirm "
     "the tools are actually there.", True),
    # ── the statement-break lookahead's EMPHASIS arm, GREEN side: a full stop
    # inside `**…**` now ends the cue's sentence, so this FORWARD copy is no
    # longer refused (it was RED — measured — while the lookahead admitted only
    # `[A-Z"'`(\[]`):
    ("Restart your client. **Once the tools are listed**, run `tortoise "
     "mcp-status`.", False),
    # form (a)'s `having` subject, and the predicate's `saw`/`seen`/`look`:
    ("Restart your client after having verified the tools are actually there.",
     True),
    ("Restart your client after you saw the tools are actually there.", True),
    ("Restart your client, having seen the tools are actually there.", True),
    ("Restart your client, look at the tools beforehand.", True),
    ("Restart your client, list the tools beforehand.", True),
    # `settings` as the config noun, and the (e) predicates beyond `are`:
    ("Restart your client after checking the settings list the tools.", False),
    ("Restart your client once the tools exist.", True),
    ("Restart your client once the tools show up.", True),
    ("Restart your client once the tools are listed.", True),
    ("Restart your client once the tools listed above appear.", True),
    # the merge loop's `lstrip`, without which a bracketed `and`-clause is not
    # recognised as continuing the previous one:
    ("Restart your client after confirming the config is right, (and the tools "
     "are actually there).", True),
    # ── pre-cue shapes that were GREEN before the statement-break fix ────────
    ("Confirm the tools appear after the v1.2 upgrade. Restart your client, then "
     "confirm the tools are actually there.", True),
    ("Confirm the tools are listed in v1.2. Restart your client, then confirm "
     "the tools are actually there.", True),
    ("Delete the old entry — but first confirm the tools are actually there. "
     "Restart your client, then confirm the tools are actually there.", True),
    # ── an `and`-led clause continues the previous one ───────────────────────
    ("Restart your client after confirming the config is right, and the tools "
     "are actually there.", True),
    ("Restart your client after confirming the config is right, and that the "
     "tools are actually there.", True),
    # ── rows for the arms behind the shapes above; each one flips when the arm
    # it names is removed (measured per arm). A few alternatives in the regexes
    # are live but carry no row of their own: spelling variants, a few untested
    # pronoun/synonym/preposition alternatives (form (e)'s `visible|load`, form
    # (c)'s `yet`, `_CONFIG_PREP_RE`'s `that|those` and `from|inside|per|under`,
    # `_CONFIG_NOUN_RE`'s plural `configs?`, form (f)'s `look|ensure` verbs),
    # and two that are REDUNDANT rather than untested (form (a)'s `having`, by
    # form (b), and `_SESSION_AND_CONJUNCT_RE`'s `load(?:ed|s)`, by
    # `_SESSION_PRESENCE_RE` — both measured: removing each alone changes no row,
    # though removing the presence copy flips `installed and they are loaded`).
    # ⛔ AND `_DOC_NOUN`'s UNEXERCISED ALTERNATIVES (cycle 54, measured): of its 26
    # alternatives, `catalog`, `catalogue`, `inventory`, `tour`, `cheat-sheet`,
    # `cheatsheet`, `matrix`, `screenshot`, `diagram`, `index`, `chapter` and
    # `listing` appear in no row, so removing any one of them is undetectable by
    # the suite. Declared rather than enumerated into rows: the arm's DIRECTION is
    # a wider exclusion, i.e. a false GREEN — the false-RED direction this list
    # exists to keep out — and the block above already states that "a noun list
    # cannot be completed".
    # ⛔ AND `always` FROM THE ADVERB FAMILY (cycle 50): it carries its own rows,
    # while `often` is LIVE but row-less (measured: removing it alone flips 0
    # rows) and declared here rather than left silent. `never`/`ever` are NOT
    # members on purpose — a negated imperative is not a confirmation, so grading
    # it would be a false RED (`_ADVERBS`' own block states this). The family was
    # absent at `8c96f5de`, which is what made one inserted `always` re-open the
    # pre-cue half of the pin.
    # ⛔ `when` used to be listed here and is NOT row-less: replacing form (a)'s
    # `(?:if|when|whenever|as soon as)` with `(?:if|whenever|as soon as)` flips
    # **5** declared rows (measured at `8c96f5de` — the fourth was the
    # degree-intensifier row, the fifth arrived with the completive rows; the
    # number here said FOUR, i.e. a count restated from a parent head that a later
    # commit in the same branch had already invalidated). It is pinned, so the
    # claim that it carries no row was wrong in the safe direction but still wrong.
    # ⛔ `list` is NOT redundant and is not in that list: it is load-bearing for
    # form (d) and row-pinned (`list the tools beforehand`), which is how a claim
    # of redundancy about a live arm was caught here — measured by deleting it
    # from `_CONFIRM_PREDICATE`, which flips that row from RED to GREEN. The
    # comments claim the behaviour, not a row for every alternative
    # ───────────────────────────────────
    ("Restart your client, and once the tools are listed run `tortoise "
     "mcp-status`.", False),
    ("Restart your client, next once the tools are listed run `tortoise "
     "mcp-status`.", False),
    ("Restart your client, afterwards once the tools are listed run `tortoise "
     "mcp-status`.", False),
    ("Restart your client after checking the tools are actually there.", True),
    ("Restart your client after ensuring the tools are actually there.", True),
    ("Restart your client if you check the tools first.", True),
    ("Restart your client after checking the installer lists the tools.", False),
    # form (c)'s `beforehand` before the predicate (the other row's sits after it,
    # so it is form (d) that catches that one):
    ("Restart your client, though beforehand you confirmed the tools are "
     "actually there.", True),
    # form (d)'s two trailing-marker arms (post-cue, so form (f) is skipped):
    ("Restart your client, confirm the tools are actually there before "
     "restarting.", True),
    ("Restart your client, confirm the tools are actually there before you "
     "restart.", True),
    # a sentence that begins lower case is graded as though it continued — the
    # cost of the lookahead that keeps `e.g. quit` inside one statement:
    ("Restart your client. otherwise, after confirming the tools are actually "
     "there, run `tortoise mcp-status`.", True),
    # the PRE-cue config exclusion: a file-side check before the cue is forward,
    # and nothing else in this table depends on that arm (measured):
    ("After verifying the config lists the tools, Restart your client and "
     "confirm the tools are there.", False),
    # leading punctuation must not hide a forward sequencer:
    ("Restart your client — once the tools are listed, run `tortoise "
     "mcp-status --live`.", False),
    ("Restart your client (then once the tools are listed, run `tortoise "
     "mcp-status`).", False),
    # ── `:` IS A STATEMENT BREAK, like `;` and the dashes. A colon introducing
    # the follow-up step is ordinary quickstart copy and unambiguously forward;
    # without the arm its GREEN twin was RED and its pre-cue RED twin was GREEN,
    # both one-character edits (measured):
    ("Restart your client: once the tools are listed, run `tortoise "
     "mcp-status --live`.", False),
    # ⛔ …and with a COLON the `after confirming` clause FOLLOWS the restart
    # ("restart: then, after confirming, run …"), so it is forward copy and stays
    # GREEN. Measured before it was dropped as a wrong expectation.
    ("Restart your client: after confirming the tools are actually there, run "
     "`tortoise mcp-status`.", False),
    # ── CYCLE 37 (#14679's review at `e8adfb7b`): the governor fallback above
    # graded PAST the statement break and ahead of the merge, so copy that
    # legitimately follows the governor's own step went RED. These are its
    # regression guards (both are one-glyph edits of declared-GREEN twins):
    ("Before you restart your client: write the config. Afterwards, confirm the "
     "tools are actually there.", False),
    ("Before you restart your client: then, confirm the tools are actually "
     "there.", False),
    # ── CYCLE 37 RESIDUALS, pinned RED so they are visible rather than claimed
    # away. Each is measured and each needs a decision, not another widening:
    # (a) an inserted `, then` splits the clause and re-opens the negated-
    #     observation false PASS closed two rows above:
    ("Restart your client after seeing no errors, then confirming the tools "
     "are actually there.", False),
    # (b) CLOSED (was: `be|make sure` is a confirmation with no confirmation
    #     VERB, so it was missed by both the suffix arm and form (f)'s verb
    #     slot — 77 declared-RED rows had a `make sure` GREEN twin, per the
    #     substitution rule at `_CONFIRM_PREDICATE`). Closing it
    #     was a DECISION, not a widening: this sentence's `and` may conjoin
    #     INSIDE the `after` clause ("after [seeing no errors and making sure the
    #     tools are there]" = confirm first), which is exactly why its `confirm`
    #     twin two rows up is declared RED. Grading the two spellings
    #     differently was the defect, so the `make sure` twin now reds too — at
    #     the cost of reding one ambiguous shape no doc ships, which is the
    #     conservative side of a false PASS (#14679 review at `1dedd33da07e`).
    ("Restart your client after seeing no errors and make sure the tools are "
     "actually there.", True),
    # (c) a comma-free run-on where the config check follows the object: form
    #     (d)'s clause-final `first` fires and `_confirmation_scopes_the_config`
    #     cannot see the config noun, so a file-side check goes RED (its comma
    #     twin is GREEN):
    ("Restart your client if you see no tools in the list and check the config "
     "path first.", True),
    # (d) A BREAK GLYPH AFTER THE CUE HIDES AN EXPLICIT INVERSION MARKER. This is
    #     the largest measured residual and it is PRE-EXISTING: **70** declared-RED
    #     rows have a GREEN twin for each of `;` `:` ` - ` `—` `–` (**350**
    #     shapes). Measured at `8c96f5de` by replacing the FIRST `, ` after the cue
    #     of every declared-RED row with the glyph and re-grading; the number here
    #     said 55/275 and was a count restated from a parent head (a fresh review
    #     measured 71/355 with its own replacement rule — quoting either without
    #     the method is what makes a denominator drift, so both are named).
    #     The truncated post-cue window is what buys the forward shapes above, so
    #     closing it is a design decision (it flips 3 forward rows RED for 70
    #     recovered), not a patch. One representative is pinned here:
    ("Restart your client; having first confirmed the tools are actually there.",
     False),
    ("Delete the old entry: but first confirm the tools are actually there. "
     "Restart your client, then confirm the tools are actually there.", True),
    # ── the session-presence vetoes share the file's OWN presence vocabulary.
    # A narrower copy missed `loaded` / `show up`, so the most natural rendering
    # of the very claim the arm exists to catch shipped GREEN (measured):
    ("Restart your client after confirming the tools are installed and they "
     "are loaded.", True),
    ("Restart your client after confirming the config lists the tools and they "
     "show up.", True),
    # ── …but a bare existential `there are` is NOT a presence claim, so an
    # entirely file-side conjunct must stay GREEN. `\\bthere\\b` matched it and
    # graded the clause as an inversion (measured):
    ("Restart your client after confirming the config lists the tools and "
     "there are no duplicate entries.", False),
    # ── `see THAT …` is an INSTRUCTION, not an observation, so the observation
    # skip must not swallow it (its `confirm` twin is a declared-RED row):
    ("Restart your client, but first see that no tools are missing.", True),
    # ── …and the observation skip is SCOPED: one appended, unrelated negated
    # observation must not disarm the form. Every one of these was GREEN before
    # the scope was fixed (measured, all seven forms):
    ("Restart your client, but first confirm the tools are actually there, and "
     "see no errors in the log.", True),
    ("Restart your client, having first confirmed the tools are actually there "
     "and noticed no errors.", True),
    ("Restart your client after confirming the tools are actually there and "
     "seeing no errors.", True),
    ("Restart your client, but confirm the tools are already there and see no "
     "errors.", True),
    ("Restart your client only once the tools are actually there and see no "
     "errors.", True),
    # ── …and the negation must be the VERB'S OWN OBJECT: an observation verb
    # that is part of the graded claim (`saw`/`seen`/`see the tools are actually
    # there`) must not let the match run ACROSS the claim to a later `no …`,
    # which deleted the claim and disarmed (a)/(b)/(c) (measured):
    ("Restart your client after you saw the tools are actually there, and see "
     "no errors in the log.", True),
    ("Restart your client, having seen the tools are actually there and "
     "noticed no errors.", True),
    ("Restart your client, but first see the tools are actually there and see "
     "no errors.", True),
    # ── …while a clause whose only claim IS a negated observation stays GREEN:
    ("Restart your client if you see no `tortoise_*` tools in the list.", False),
    ("Restart your client after you have noticed no errors in the log.", False),
    # ── a documentation CROSS-REFERENCE is readable without a restart, so it is
    # not an inversion. `documentation` was missing from the doc-noun list, so
    # these were RED while their `tools reference` twins are GREEN (measured):
    ("Restart your client after checking the tools documentation.", False),
    ("Restart your client, having checked the tools documentation.", False),
    ("Ensure the tools documentation is up to date. Restart your client, then "
     "confirm the tools are actually there.", False),
    ("Restart your client after checking the tools manifest.", False),
    # ── THE DOCS' OWN SPELLING IS SINGULAR (`docs/quickstart-cloud.md:135` names
    # "the client's MCP/tool list"), and every form keyed on the plural `tools`,
    # so `tool list` escaped all seven — including the one that INVERTS the
    # order (#14717, measured GREEN at `8c3bb0d692`). One row per form, so a
    # regression can name which arm lost the spelling rather than just "the
    # singular stopped working"; the GREEN rows are the false-red guards that
    # make the widening safe (a doc cross-reference and the pin's OWN required
    # order are not inversions).
    ("Confirm the tool list is complete. Restart your client, then confirm the "
     "tools are actually there.", True),                      # (f) pre-cue
    ("Restart your client after confirming the tool list is complete.", True),
    ("Restart your client after you have verified the tool list is complete.",
     True),                                                    # (a), auxiliary
    ("Restart your client, having first confirmed the tool list is complete.",
     True),                                                    # (b)
    ("Restart your client, but first confirm the tool list shows "
     "`tortoise_*`.", True),                                   # (c)
    ("Restart your client, but confirm the tool list is already complete.",
     True),                                                    # (d′)
    ("Restart your client only once the tool list is complete.", True),
    # …form (e)'s copula was `are`-only (a singular `is` was unreachable behind a
    # plural object) and its verbs carried plural agreement only, so both these
    # stayed GREEN with the object widened and no change here (measured):
    ("Restart your client only once the tool list shows `tortoise_*`.", True),
    # …and the widening must not red anything it merely has to READ:
    ("Restart your client, then confirm the tool list is complete.", False),
    ("Restart your client. Confirm the tool list is complete.", False),
    ("See the tool list section for the full list. Restart your client, then "
     "confirm the tools are actually there.", False),
    ("Restart your client after checking the tool list documentation.", False),
    ("Restart your client after checking the tool list reference.", False),
    # a completive `has stopped` is not a presence claim, exactly as its plural
    # twin (`… once the tools have stopped appearing`) is declared GREEN above:
    ("Restart your client only once the tool list has stopped changing.", False),
    # …nor is a POST-cue confirmation of the tool list (the pin's required order):
    ("Restart your client after changing the config, then confirm the tool "
     "list is complete.", False),
    # ── THE CONFIG EXCLUSION NAMES THE SAME OBJECT, and did not: its lookup was a
    # literal `find("tools")`, so a config-scoped clause that names the SINGULAR
    # spelling fell through the exclusion and was graded as an inversion. Both
    # rows below were RED on the head that widened the object while their `tools`
    # twins are declared-GREEN rows — a false red on copy the file itself calls
    # forward (the config is readable without a restart), which is the failure
    # mode #3429 records as the one that gets a pin deleted:
    ("Restart your client after verifying the config lists the tool list.",
     False),
    ("Restart your client after confirming the config file lists the tool "
     "list.", False),
    # ── …and form (a)'s OBSERVATION GUARD carries the singular arm too, which no
    # row exercised (measured: reverting it to a bare `tools` left all 322 rows
    # GREEN — an arm nothing could depend on). The guard exists so an observation
    # verb does not blind a clause that ALSO confirms the tools; on the singular
    # spelling it reopened exactly that hole:
    #
    #   GREEN  Restart your client if you looked in the panel and confirmed the
    #          tool list is complete.
    #
    # The `if`-led clause is the one that pins it: the `after you …` twin is
    # caught by form (a)'s first alternative, which carries no guard at all
    # (measured, and that is why this row is the `if` one).
    ("Restart your client if you looked in the panel and confirmed the tool "
     "list is complete.", True),
    # ── CYCLE 29 (#14679's review at `7bf9f944`): eight measured defects, each a
    # row below. Six are the "one word defeats a closed list" class this pin is
    # made of; the first two are corrections to the singular widening itself.
    #
    # (P1) `_confirmation_scopes_the_config` looked up the LITERAL `tools`, so
    # the whole file-side exemption was unreachable for the singular object one
    # commit after the object was widened — five declared-GREEN classes went RED.
    # The object lookup now uses `_TOOLS_OBJECT`; these are its twins (the first
    # is pinned above, from the peer commit that fixed the same defect):
    ("Restart your client after checking the tool list is listed in your "
     "client settings.", False),
    ("Restart your client once the tool list is installed.", False),
    ("Restart your client after the tools are installed.", False),
    ("Restart your client when the tools are configured.", False),
    # …and the conjunction rule still fires through the singular object:
    ("Restart your client after confirming the config is right and the tool "
     "list is actually there.", True),
    # (P2) `firstly` is `first`: a one-word inflection defeated four forms and
    # flipped 26 of 32 declared-RED rows GREEN (measured).
    ("Restart your client, but firstly confirm the tools are actually there.",
     True),
    ("Restart your client, confirm the tools are actually there firstly.", True),
    ("Please firstly confirm the tools are actually there. Restart your client, "
     "then confirm the tools are actually there.", True),
    # (P2) form (e) was `once`-anchored, so one word bypassed 20+ declared rows —
    # including the singular ones the previous commit added:
    ("Restart your client only after the tools are actually there.", True),
    ("Restart your client only when the tool list is complete.", True),
    ("Restart your client after the tools are listed.", True),
    # (P2) form (c)'s span ran through an intervening IMPERATIVE to a later
    # confirmation, so removing a comma turned forward copy RED:
    ("Restart your client, but first save your work, then confirm the tools "
     "are actually there.", False),
    ("Restart your client but first save your work then confirm the tools are "
     "actually there.", False),
    ("Restart your client (but first save your work) and then confirm the tools "
     "are actually there.", False),
    # (P3) a hyphenated predicate is the same predicate — the lesson form (a)
    # already carries, applied to the form where an imperative takes it most:
    ("Please double-check the tools are actually there. Restart your client, "
     "then confirm the tools are actually there.", True),
    # (P3) the adversative / pre-restart vocabularies were closed one synonym
    # short on both sides (`though`→`although`, `beforehand`→`in advance`):
    ("Restart your client, although beforehand you confirmed the tools are "
     "actually there.", True),
    ("Restart your client, though you should confirm the tools are actually "
     "there in advance.", True),
    # (P3) `tool lists` — the plural of the docs' own spelling — defeated it:
    ("Restart your client after confirming the tool lists are complete.", True),
    # (P3) two arms added by the previous commit that NO row pinned (mutating
    # each flipped 0 of 274 rows): form (f.1)'s own object arm (its added row is
    # satisfied by (f.2), one arm over), and form (e)'s singular verbs.
    ("Please list the tool list from the panel. Restart your client, then "
     "confirm the tools are actually there.", True),
    ("Restart your client only once the tool list appears.", True),
    ("Restart your client only once the tool list exists.", True),
    ("Restart your client only once the tool list loads.", True),
    # ── CYCLE 30 (#14679's review at `b2f9a12f`): six findings, each a row here.
    # The first is the regression that review round introduced: admitting `when`
    # into form (e) without an absence arm turned `when`-led SYMPTOM copy RED.
    # A restart cannot be once-conditioned on an absence, so those REDs asserted
    # an inversion the copy does not contain (#3429's pin-deletion shape):
    ("Restart your client when the tools are missing.", False),
    ("Restart your client when the tools are not visible in the panel.", False),
    ("Restart your client when the tools are gone from the list.", False),
    # ⛔ This row is a CONJUNCTION guard, and it is labelled as one because it
    # pins nothing on its own (measured): it stays GREEN with the absence arm
    # removed, with `if` admitted to form (e), and with the arm narrowed to
    # `not|never`. It flips only when TWO of those hold at once, which is the
    # only state in which it carries information.
    ("Restart your client if the tools are missing.", False),
    # ── CYCLE 31 (#14679's review at `006c2038`): the absence arm's FIRST cut
    # graded the token, not the POLARITY, and was wrong in both directions.
    # An adverb re-opened the presence false-RED it was written to close:
    ("Restart your client when the tools are still missing.", False),
    ("Restart your client when the tools are definitely not there.", False),
    ("Restart your client when the tools are still not visible.", False),
    # …and the adverb run is the FILE'S OWN CLASS, not a hand-picked eight: the
    # first cut spelled it out, so one inserted adverb leaked in both directions
    # (#14679 review cycle 32, measured — these seven were RED, and the four
    # `not <adverb> missing` shapes below were GREEN):
    ("Restart your client when the tools are temporarily missing.", False),
    ("Restart your client when the tools are just missing.", False),
    ("Restart your client when the tools are completely gone.", False),
    ("Restart your client when the tools are seemingly gone.", False),
    ("Restart your client when the tools are intermittently missing.", False),
    ("Restart your client when the tools are consistently broken.", False),
    ("Restart your client when the tools are plainly absent.", False),
    # A declared RESIDUAL, not a goal: a non-`-ly` adverb before an absence word
    # (`altogether absent`) is still graded — the same residual form (a) carries,
    # and it is pinned here so a reader sees the boundary instead of inferring
    # the arm is complete.
    ("Restart your client when the tools are altogether absent.", True),
    # …and it is a REGRESSION GUARD for behaviour that predates this commit (`no
    # longer visible` was already GREEN at `006c2038`), not evidence for the
    # polarity fix — the third alternative that was supposed to carry it flipped
    # 0 of 319 rows and was deleted as dead weight.
    ("Restart your client when the tools are no longer visible.", False),
    # …and a PRESENCE claim stated as a NEGATED ABSENCE failed OPEN — the
    # negation of an absence is presence, which is exactly the condition form
    # (e) grades. These were GREEN on the first cut of the arm:
    ("Restart your client once the tools are not missing.", True),
    ("Restart your client only once the tools are no longer missing.", True),
    ("Restart your client once the tools are no longer empty.", True),
    ("Restart your client once the tools are no longer absent.", True),
    ("Restart your client once the tools are never gone.", True),
    # …and one inserted adverb moved the polarity the other way (cycle 32):
    ("Restart your client once the tools are not actually missing.", True),
    ("Restart your client once the tools are not currently missing.", True),
    ("Restart your client once the tools are not really missing.", True),
    ("Restart your client once the tools are not yet missing.", True),
    ("Restart your client once the tools are not entirely absent.", True),
    ("Restart your client once the tools are no longer actually missing.", True),
    ("Restart your client once the tools are never actually gone.", True),
    # ── CYCLE 33 (#14679's review at `9e7e1f60`): the two adverb runs had
    # DIVERGED (13 members outer, 4 inner), so a non-`-ly` focus/degree adverb
    # after a negation suppressed a PRESENCE claim — one word from the declared
    # RED row above (`not ENTIRELY absent`). They are one constant now:
    ("Restart your client when the tools are not quite missing.", True),
    ("Restart your client once the tools are not quite gone.", True),
    ("Restart your client after the tools are not very missing.", True),
    ("Restart your client when the tools are not still missing.", True),
    ("Restart your client when the tools are not now missing.", True),
    # …and a NEGATING adverb inverts the absence word behind it, so `hardly
    # missing` is presence. A bare `\w+ly` slot in the run turned all four of
    # these GREEN; the slot now excludes them by lookahead:
    ("Restart your client when the tools are hardly missing.", True),
    ("Restart your client once the tools are barely missing.", True),
    ("Restart your client after the tools are scarcely missing.", True),
    ("Restart your client when the tools are rarely missing.", True),
    # A declared RESIDUAL, pinned so a reader sees the boundary: `not just
    # missing` is an ABSENCE (a focus negation), and it is graded RED because the
    # run reads `not … missing` as a negated absence. Keeping `just` out of the
    # run instead flips the declared-GREEN `when the tools are just missing`, so
    # the common copy wins and the rare shape is recorded here.
    ("Restart your client when the tools are not just missing.", True),
    # …while a real PRESENCE condition stays graded:
    ("Restart your client when the tools are actually there.", True),
    ("Restart your client when the tools appear.", True),
    # form (a)'s observation guard spelled its own object, so `tool lists` was
    # classified as a pure observation and form (a) was disarmed — the singular
    # defect one character over:
    ("Restart your client if you looked in the panel and confirmed the tool "
     "lists are complete.", True),
    # `tools list` (plural head) is the file's own prose spelling and form (e)
    # missed it: `\btools\b` matched, then the copula demanded whitespace and
    # found `list`:
    ("Restart your client only once the tools list is complete.", True),
    ("Restart your client after the tools list is complete.", True),
    # `although` is pinned at BOTH of its newer sites, not only form (c)'s copy —
    # removing either one alone flipped 0 rows while changing real verdicts:
    ("Restart your client, although confirm the tools are already there.", True),
    ("Restart your client and then, although first confirm the tools are "
     "actually there, run `tortoise mcp-status`.", True),
    # ── CYCLE 34 (#14679's review at `5c6dda9dc3`, the cycle-32 P1 carried
    # unfixed): the PRE-cue side graded `[-1:]` unconditionally, so whenever the
    # cue was introduced by a SEQUENCER the fragment that survived was the
    # literal `then` and no form could match it. All four renderings below are
    # the SAME inversion the pin exists to catch, and all four were GREEN at
    # `5c6dda9dc3` — measured end-to-end on the real `docs/quickstart-cloud.md`,
    # rewording only line 135: each one left the doc-level test PASSING. The
    # post-cue side had this machinery (`_SEQUENCER_TAIL_RE`); the pre-cue side
    # has `_SEQUENCER_FRAGMENT_RE` + `_cue_governing_clause` now.
    ("Confirm the tools are actually there, then restart your client.", True),
    # ⛔ THE PRE-CUE ASIDE — the same arm as the post-cue one, on the other side
    # of the cue, and it was MISSING until #14679's review at `1960427e` measured
    # that inserting `, in the client's MCP/tool list,` turned every one of these
    # declared-RED rows GREEN (47 of them had an aside twin): the aside became the
    # clause that governs the cue, so form (f) never fired. `_ASIDE_RE` is now
    # applied to `before` as well.
    ("Confirm the tools are actually there, in the client's MCP/tool list, then "
     "restart your client.", True),
    ("Confirm the tools are actually there, under MCP, then restart your client.",
     True),
    ("Confirm the tools are actually there, at the MCP panel, then restart your "
     "client.", True),
    ("Confirm the tools are actually there, from the menu, then restart your "
     "client.", True),
    ("Restart your client, in the client's MCP/tool list, then confirm the tools "
     "are actually there.", False),
    ("Confirm the tools are actually there, **then** restart your client.", True),
    ("Confirm the tools are actually there, and then restart your client.", True),
    ("Confirm the tools are actually there. Then restart your client.", True),
    ("Confirm the tools are actually there; then restart your client.", True),
    # …and the fourth rendering is the SUBORDINATE one: `before` governs the
    # cue, so the confirmation after it is the condition OF the restart and
    # happens FIRST — the exact order form (a) reds when it trails the cue.
    ("Before you restart your client, confirm the tools are actually there.",
     True),
    ("Before you restart your client, please verify the tools are actually "
     "there.", True),
    ("Until you restart your client, confirm the tools are actually there.",
     True),
    ("By the time you restart your client, confirm the tools are actually "
     "there.", True),
    # ── CYCLE 35 (#14679's review at `2ce28496`): four findings, three rows here.
    # (1) a DOC CROSS-REFERENCE with a copula is still a cross-reference — the
    # doc-participle lookahead only suppressed the phrase DIRECTLY after the
    # object, so the `are listed above` spelling went RED once the walk-back
    # graded the clause a `. then` cue introduces:
    ("Check the tools are listed above. Then restart your client.", False),
    ("Check the tools are documented below, then restart your client.", False),
    # (2) a STATEMENT BREAK IMMEDIATELY BEFORE THE CUE means nothing governs it,
    # and the empty pre-cue fragment the split produces was dropped by its own
    # filter — so `Before you continue:` read as the cue's governor and form (f)
    # graded a comma-spliced confirmation that follows the restart:
    ("Before you continue: restart your client, confirm the tools are actually "
     "there.", False),
    ("Before you start - restart your client, confirm the tools are actually "
     "there.", False),
    ("Before you start; restart your client, confirm the tools are actually "
     "there.", False),
    # (3) the ONE-WORD twins of the declared-RED "…, then restart …" row, which
    # no row pinned: the sequencer test this file shipped at that point flipped 0
    # declared rows while making each of these GREEN, so a future dead-arm sweep
    # on this file's own stated ground would have silently re-opened the false
    # PASS. (That test was `_BARE_SEQUENCER_RE`, a closed token list; it is
    # `_SEQUENCER_FRAGMENT_RE` now — a word-class test, because the list form was
    # itself defeated by one inserted word — and these rows pin the member words
    # either way.)
    ("Confirm the tools are actually there, and restart your client.", True),
    ("Confirm the tools are actually there. Also restart your client.", True),
    ("Confirm the tools are actually there, next restart your client.", True),
    ("Confirm the tools are actually there, afterwards restart your client.",
     True),
    # ── CYCLE 36 (#14679's review at `24f1ac50`): the copula arm admitted
    # `here|there`, which are NOT doc-only places — they are the pin's canonical
    # presence deixis ("the tools are actually there" is a declared RED row), so
    # a real pre-restart presence claim went GREEN. The copula arm now uses a
    # strict place set; the participle-directly-after-object arm keeps both.
    ("Confirm the tools are available there. Restart your client.", True),
    ("Restart your client, but first confirm the tools are available there.",
     True),
    ("Check the tools are listed there. Then restart your client.", True),
    # …while the documentary places stay GREEN, with and without a copula:
    ("Check the tools are described in this guide. Then restart your client.",
     False),
    ("Check the tools listed there. Then restart your client.", False),
    # …and `…` is the one break glyph the statement-break class was missing, so
    # its twin of the three cycle-35 rows was a false RED (glyph variants are the
    # same punctuation — the file's own doctrine, three times over in this
    # section):
    ("Before you start … restart your client, confirm the tools are actually "
     "there.", False),
    ("Before you continue… restart your client, confirm the tools are actually "
     "there.", False),
    # ── CYCLE 36, second half (#14679's review at `4aa1fc78`): two more measured
    # holes, both false PASSes.
    # (1) the observation skip asked only what is LEFT of a negated observation,
    # so a clause that states the observation FIRST and the confirmation SECOND
    # was classified as observation-only and skipped:
    ("Restart your client after seeing no errors and confirming the tools are "
     "actually there.", True),
    # ── …CYCLE 39, the same hole stated as Finding 1 in the review at
    # `4aa1fc78`: the skip is CLAUSE-WIDE, so a claim to the RIGHT of the negated
    # observation must still be graded. Four of these pin the cycle-36 suffix arm
    # (they were GREEN before it and no row covered them); the fifth is form (e),
    # which has no confirmation verb for that arm to see — the PREFIX-ONLY
    # remainder was what skipped it, and the splice above is what grades it
    # (measured GREEN at `31554d14`, RED after). One row per form, so a regression
    # names the arm that lost the discriminator:
    ("Restart your client after you saw no errors and verified the tools are "
     "actually there.", True),                                 # (a)
    ("Restart your client having noticed no errors and first confirmed the "
     "tools are actually there.", True),                       # (b)
    ("Restart your client, but having seen no errors first confirm the tools "
     "are actually there.", True),                             # (c)
    ("Restart your client but having noticed no errors confirm the tools are "
     "actually there beforehand.", True),                      # (d)
    ("Restart your client seeing no errors only once the tools are actually "
     "there.", True),                                          # (e) — splice
    # …and the discriminator must not become "any negation reds the clause":
    # when the negation's own complement IS the tools object, or when nothing
    # follows it to grade, the observation is the clause's ONLY claim and the
    # skip must still fire (these three are GREEN at the head and under the
    # splice alike):
    ("Restart your client seeing no tools in the list only once the list has "
     "loaded.", False),
    # (2) when a `before|until|by the time` governor is known AND a break glyph
    # sits immediately after the cue, the post-cue list is empty, so form (f) —
    # which only the post-cue side reaches — graded nothing. Every non-comma
    # twin of the declared-RED governor row was GREEN, and the colon's own
    # rationale ("unambiguously forward") is falsified by exactly that shape:
    ("Before you restart your client; confirm the tools are actually there.",
     True),
    ("Before you restart your client: confirm the tools are actually there.",
     True),
    ("Before you restart your client -  confirm the tools are actually there.",
     True),
    ("Before you restart your client —  confirm the tools are actually there.",
     True),
    ("Until you restart your client: confirm the tools are actually there.",
     True),
    # …and the forward colon shapes stay GREEN (no governor, so the guard does
    # not fire):
    # ⛔ THE WALK-BACK MUST NOT RED FORWARD COPY. A bare sequencer sequences
    # whatever precedes it, so the clause it defers to is graded — and when that
    # clause carries no tools-appear claim, it stays GREEN. These rows are what
    # pins that: without them the walk-back is unpinned in the safe direction and
    # a future "always grade the previous clause" edit could red ordinary copy.
    ("Delete the old entry, then restart your client.", False),
    ("Check the log. Then restart your client, then confirm the tools are "
     "actually there.", False),
    ("Delete the old entry, then restart your client, then confirm the tools "
     "are actually there.", False),
    ("Before you restart your client, save the MCP config file.", False),
    ("Before you restart your client, confirm the config is written.", False),
    ("Restart your client, before confirming the tools are actually there.",
     False),
    # ── CYCLE 35 (#14679's review at `2ce284961b`): the fragment test was a
    # sequencer TOKEN LIST, so ONE INSERTED WORD re-opened the false PASS the
    # cycle-34 fix had just closed — measured end-to-end on the real doc, all
    # four PASSING both doc-level asserts. `_SEQUENCER_FRAGMENT_RE` is now the
    # class test its comment always claimed (a clause with no content word is a
    # fragment), and these are its rows:
    ("Confirm the tools are actually there, then please restart your client.",
     True),
    ("Confirm the tools are actually there, only then restart your client.",
     True),
    ("Confirm the tools are actually there, then you must restart your "
     "client.", True),
    ("Confirm the tools are actually there, after which restart your "
     "client.", True),
    # …and the OTHER half is the modal subject on the imperative itself: form
    # (f)'s leading slot admitted only courtesy + adverbs, so the canonical
    # two-sentence inversion stayed GREEN behind `You must`:
    ("You must confirm the tools are actually there. Restart your client, "
     "then run a query.", True),
    ("You should confirm the tools are actually there. Restart your client, "
     "then run a query.", True),
    ("You need to confirm the tools are actually there. Restart your client, "
     "then run a query.", True),
    ("Be sure to confirm the tools are actually there. Restart your client, "
     "then run a query.", True),
    ("You must confirm the tools are actually there, then restart your "
     "client.", True),
    # ⛔ …and the same slot must NOT red forward copy: a modal subject that
    # carries no tools-presence claim, or a fragment whose preceding clause
    # carries none, is not an inversion.
    ("You must save the config file. Restart your client, then confirm the "
     "tools are actually there.", False),
    ("You must restart your client, then confirm the tools are actually "
     "there.", False),
    ("Delete the old entry, after which restart your client, then confirm the "
     "tools are actually there.", False),
    # ── CYCLE 37 (#14679's review at `18f99499d7`): the "a pre-cue imperative that
    # defers its own check past the restart is FORWARD" exemption matched only a
    # TRAILING `after <the> restart(ing)`, so every FRONTED spelling of the same
    # relation was a false red (#3429) with the "inverts its own restart cue"
    # message over copy that puts the restart first. `_DEFERRED_CHECK_RE` sees the
    # phrase anywhere in the clause's STATEMENT now — anywhere in the statement,
    # not anywhere in the paragraph, which the row after these three pins.
    ("Check the tools once your client has restarted. Restart your client, "
     "then confirm the tools are actually there.", False),
    ("After restarting, check the tools are present. Restart your client, "
     "then confirm the tools are actually there.", False),
    ("Once the client restarts, check the tools are present. Restart your "
     "client, then confirm the tools are actually there.", False),
    # ⛔ AND `when` is a SEPARATE ALTERNATIVE of that same regex, which had no row
    # of its own: it flipped 0 of the declared rows on removal while still being
    # load-bearing on real copy (#14679 review at `49ef237f4c5b`, P2 — "deleting
    # `when` … flips a real verdict"). This row is that verdict, and it goes RED
    # the moment `when` leaves `_DEFERRED_CHECK_RE` (measured).
    ("Check the tools when your client has restarted. Restart your client, "
     "then confirm the tools are actually there.", False),
    # ⛔ …and the exemption must NOT reach back past a STATEMENT BREAK: an
    # unrelated earlier deferral does not make a later confirmation forward.
    ("Check the logs after restarting. Confirm the tools are actually there. "
     "Restart your client, then confirm the tools are actually there.", True),
    # ── CYCLE 38 (#14679's review at `509d235dcce1`): form (f)'s leading slot had
    # no SEQUENCER/ADVERB arm, so ONE leading word defeated the whole pre-cue
    # half — measured: 39 of the 40 declared-RED pre-cue rows flipped GREEN and
    # `Then confirm the tools … Restart your client, …` passed both doc-level
    # assertions end-to-end on the real quickstart.
    ("Now confirm the tools are actually there, then restart your client.", True),
    ("Then confirm the tools are actually there. Restart your client.", True),
    ("Next confirm the tools are actually there. Restart your client.", True),
    ("Finally confirm the tools are actually there. Restart your client.", True),
    # …while a leading sequencer in front of the RESTART is still forward copy:
    ("Now restart your client, then confirm the tools are actually there.",
     False),
    ("Simply restart your client, then confirm the tools are actually there.",
     False),
    # ── CYCLE 39 (#14679's review at `31554d1424af`): the leading run consumed
    # the adverb, which turned the markdown marker INTERIOR — where
    # `_PRE_CUE_PREFIX` does not reach — so the four declared-RED DECORATED rows
    # flipped GREEN under every recognised lead, and `Now **Confirm the tools are
    # actually there.** Restart your client, …` passed both doc-level assertions
    # end-to-end on the real quickstart. Form (f) is now matched against a
    # decoration-free copy, like the rest of the file's decoration handling.
    ("Now **Confirm the tools are actually there.** Restart your client, then "
     "confirm the tools are actually there.", True),
    ("Then *confirm the tools are actually there*. Restart your client, then "
     "confirm the tools are actually there.", True),
    ("Now > confirm the tools are actually there. Restart your client, then "
     "confirm the tools are actually there.", True),
    ("Now `confirm the tools are actually there`. Restart your client, then "
     "confirm the tools are actually there.", True),
    # ── …and the deferral exemption is looked up on the GRADED CLAUSE'S OWN
    # STATEMENT, not the last one: `_cue_governing_clause` walks back across a
    # `.`/`;` boundary, so one inserted `Then` made `After restarting, check the
    # tools are present. …` a FALSE RED over a declared-GREEN row (measured).
    ("After restarting, check the tools are present. Then restart your client, "
     "then confirm the tools are actually there.", False),
    ("Once the client restarts, check the tools are present. Then restart your "
     "client, then confirm the tools are actually there.", False),
    # ── CYCLE 40/41: an `and once|and when|and then` continuation used to fold
    # into its predecessor, which DESTROYED the sequencer `_FORWARD_SEQUENCER_RE`
    # anchors on — so forward copy was graded RED with the "inverts its own restart
    # cue" message (#3429's class). `_and_half_opens_its_own_clause` now declines
    # the fold for the one decidable shape (the half carries a main clause of its
    # own), so these three are GREEN again and are pinned as the FORWARD copy they
    # are — not as a residual (#14679 review at `4f33aad5ff2f`, P1).
    ("Restart your client (you should see no errors in the log), and once the "
     "tools are listed run `tortoise mcp-status`.", False),
    ("Restart your client seeing no errors in the log, and when the tools "
     "appear run `tortoise mcp-status --live`.", False),
    # …and the SAME half reds again when the sentence is about the cue and not
    # about a later command, because the half is then a BARE CONDITION with no
    # main clause of its own: it is folded in and graded (§`_AND_HALF…`).
    ("Restart your client, confirm the tools are actually there first, and then "
     "run a query.", True),
    # ⛔ DECLARED RESIDUAL, in the OTHER direction, and it is the one this cut's
    # boundary leaves OPEN: a coordinated imperative that opens with no
    # forward sequencer (`and run a query`) and a forward adverb outside the
    # sequencer set (`and finally run a query`) still FOLD into the predecessor,
    # so the clause-final `first` form (d) anchors on is spliced away and the
    # sentence measures GREEN although it INVERTS the cue. Measured at this head,
    # and identical with `_and_half_opens_its_own_clause` neutralised — so it
    # PREDATES this cut and is not made worse by it. Not fixed here because the
    # extension that closes it opens a false PASS on the config-conjunction
    # (`… after confirming the config is right, and check the tools are actually
    # there.` RED→GREEN, measured) — #14717 owns that call. Pinned GREEN so the
    # boundary is visible and a future fix flips exactly these rows.
    ("Restart your client, confirm the tools are actually there first, and run "
     "a query.", False),
    ("Restart your client, confirm the tools are actually there first, and "
     "finally run a query.", False),
    # ⛔ …while the CONFIG-CONJUNCTION the merge exists for still reds: its
    # `and`-half is not a forward clause of its own, so it is still folded in and
    # the config-scope rule still sees both halves.
    # …and a half that DOES carry its own main clause stays GREEN even behind a
    # condition predecessor — the exact shape whose third disambiguation cut shipped
    # a FALSE RED (`Restart your client after any upgrade, and once the tools appear
    # run …`, now covered below).
    ("Restart your client after confirming the config is right, and once the "
     "tools are listed run `tortoise mcp-status`.", False),
    ("Restart your client after any upgrade, and once the tools appear run a "
     "query.", False),
    # ── CYCLE 41 (#14679's review at `ed87479fbb05`): declining the merge from the
    # `and`-half's OWN opener shipped a FALSE PASS one inserted word from the row
    # above — whether that half is a fresh statement or the SECOND CONJUNCT of a
    # condition is a property of the PREDECESSOR. It also came straight back on a
    # one-markdown edit (`**and** once`), because the test read the raw clause.
    ("Restart your client after confirming the config is right, and once the "
     "tools are actually there.", True),
    ("Restart your client after confirming the config is right, and when the "
     "tools are actually there.", True),
    ("Restart your client seeing no errors in the log, **and** once the tools "
     "appear run `tortoise mcp-status --live`.", False),
    # ⛔ DECLARED RESIDUAL, and it is the one this mechanism actually produces
    # (measured at this head, not asserted): `_AND_HALF_MAIN_CLAUSE_VERB_RE` is a
    # CLOSED verb list, so an imperative outside it keeps the pre-fix verdict —
    # folded in and graded, i.e. a false RED of #3429's class that ALREADY
    # existed and is not made worse. Pinned as RED so the boundary is visible and
    # a future fix flips exactly this row (measured — and the round-8 revision
    # that claimed "2 rows, the second being the declared-GREEN `Before you start -
    # restart …`" was WRONG: that row contains no `and` at all, so no closure of
    # this constant can touch it. A claim introduced while correcting a stale
    # count is the same defect as the count.); closing it means adding the verb,
    # not
    # a new arm.
    ("Restart your client after confirming the config is right, and once the "
     "tools are listed grep the log.", True),
    # ── CYCLE 42 (#14679's review at `1dedd33da07e`): FIVE closed-vocabulary
    # gaps, each measured as a GREEN twin of a DECLARED-RED row and each one
    # word or one synonym away. Every row below is that twin; the constant or
    # form it pins is named in its comment, and each arm's load is proven by
    # removal (see the mutation table in this file's history).
    #
    # (1) `_CONFIRM_PREDICATE`'s closure omitted the highest-frequency members
    #     of the vocabulary it claims is closable. All seven forms route through
    #     it, so one synonym defeated every form at once — 77 / 26 / 17 / 78 rows
    #     per the substitution rule stated at `_CONFIRM_PREDICATE` (this sentence
    #     used to carry a superseded figure set; one rule, one set of numbers).
    ("Before you restart your client, confirm the tools are actually there — "
     "`tortoise_*` in the client's MCP/tool list.", True),
    ("Before you restart your client, make sure the tools are actually there — "
     "`tortoise_*` in the client's MCP/tool list.", True),
    ("Restart your client after making sure the tools are actually there.", True),
    ("Restart your client after you have made sure the tools are actually "
     "there.", True),
    ("Restart your client after you have made sure the tool list is complete.",
     True),
    ("Restart your client, having first made sure the tools are actually "
     "there.", True),
    ("Restart your client, but first make sure the tools are actually there.",
     True),
    ("Restart your client, though you should make sure the tools are actually "
     "there beforehand.", True),
    ("Restart your client, but make sure the tools are already there.", True),
    ("Make sure the tools are actually there. Restart your client, then run a "
     "query.", True),
    ("Restart your client only once you have made sure the tools are actually "
     "there.", True),
    ("Restart your client after validating the tools are actually there.", True),
    ("Restart your client, confirm the tools are actually there prior to "
     "restarting.", True),
    # …and the CLOSED set stays closed in the other direction: a `make sure`
    # that scopes the CONFIG is the file-side check the pin calls forward copy,
    # exactly as its `confirm` twin is.
    ("Restart your client after making sure the config lists the tools.", False),
    # (2) `following` / `upon` are `after` in this file's own `_DEFERRED_CHECK_RE`.
    ("Restart your client upon confirming the tools are actually there.", True),
    ("Restart your client following confirming the tools are actually there.",
     True),
    ("Restart your client upon making sure the tools are actually there.", True),
    # (3) `whenever` is `when`, and `as soon as` is `once` — the same one-word
    #     inflection class the file already closed once for `first`→`firstly`.
    ("Restart your client whenever the tools are actually there.", True),
    ("Restart your client as soon as the tools are actually there.", True),
    ("Restart your client whenever the tool list is complete.", True),
    ("Restart your client once the tools are visible in the list.", True),
    # …and the CONFIRMATION shapes, which only form (a) can catch (form (e)'s
    # object slot admits at most two words before `tools`, so these are the rows
    # that make form (a)'s `whenever|as soon as` arm load-bearing).
    ("Restart your client whenever you have confirmed the tools are actually "
     "there.", True),
    ("Restart your client as soon as you have made sure the tools are actually "
     "there.", True),
    # (5) `make certain` / `be certain` are `make sure` / `be sure` — the same
    #     predicate the closure's own comment says it closes over, one synonym over.
    #     The two IMPERATIVE spellings are pinned too: `be sure` had no row either,
    #     so both arms were unpinned until these two (`be_certain` measured 0
    #     flips without them, #14679 review at `fdaaa23d016c`).
    ("Be sure the tools are actually there. Restart your client, then run a "
     "query.", True),
    ("Be certain the tools are actually there. Restart your client, then run "
     "a query.", True),
    # …and a POST-cue spelling, so the shared constant's `be certain` arm is
    # pinned by a form that actually reads it (the two rows above are pre-cue, and
    # form (f) carries its own copy of the alternation).
    ("Restart your client, but first be certain the tools are actually there.",
     True),
    ("Restart your client after making certain the tools are actually there.",
     True),
    ("Restart your client after you've made certain the tools are actually "
     "there.", True),
    ("Restart your client, having first made certain the tools are actually "
     "there.", True),
    ("Make certain the tools are actually there. Restart your client, then run "
     "a query.", True),
    # …and the DECLARED residual of that closure: the COPULA spelling is a
    # different construction (predicate adjective, not a verb phrase), so
    # `once you are certain the tools …` stays GREEN while its bare twin
    # `once certain the tools …` is RED through form (e)'s 0-2-word slot. Pinned
    # GREEN so the boundary is visible and a future fix flips exactly this row.
    ("Restart your client once you are certain the tools are actually there.",
     False),
    # (7) the FOURTH copy of the confirmation closure — the observation gate —
    #     was the one the widening missed, so the "looked … and made sure" shape
    #     still shipped GREEN. It then drifted ONE MORPHOLOGY further (no `ing`),
    #     which is why the row below uses the progressive the gate fires on.
    ("Restart your client if you are looking in the panel and ensuring the "
     "tools are actually there.", True),
    # (11) the completive's own particle is not an object, the adverb slot sits on
    #      EITHER side of the modal, and a possessive is the same determiner class.
    ("Restart your client after you are done with confirming the tools are "
     "actually there.", True),
    ("You should first confirm the tools are actually there. Restart your "
     "client, then run a query.", True),
    ("And you should first confirm the tools are actually there. Restart your "
     "client, then run a query.", True),
    ("Confirm the tools are described in your guide, then restart your client.",
     False),
    ("Confirm the tools are described in our documentation, then restart your "
     "client.", False),
    ("Restart your client if you have looked in the panel and made sure the "
     "tools are actually there.", True),
    ("Restart your client if you have looked in the panel and validated the "
     "tools are complete.", True),
    # (12) round-5 findings: the adverb slot is `_ADVERBS` (not a copy of its
    #      first three members), `ought to` is a modal, and `up` is the
    #      completive's particle just as `with` is.
    ("You must carefully confirm the tools are actually there. Restart your "
     "client, then run a query.", True),
    ("Carefully confirm the tools are actually there. Restart your client, then "
     "run a query.", True),
    ("First, carefully confirm the tools are actually there. Restart your "
     "client.", True),
    ("You ought to confirm the tools are actually there. Restart your client, "
     "then run a query.", True),
    ("Restart your client after you have finished up confirming the tools are "
     "actually there.", True),
    ("Restart your client after you are done with the config and confirm the "
     "tools are actually there.", False),
    # (13) round-6 findings: a modal at the head of a semi-modal chain, the adverb
    #      between subject and modal, `ought not to`, and the elliptical presence
    #      claim on the client's own list. Declared residual: a DOUBLE chain
    #      (`You're going to need to confirm …`) is still GREEN — the slot admits
    #      one pre-modal, and nesting it would be an unbounded grammar.
    ("You will need to confirm the tools are actually there. Restart your "
     "client, then run a query.", True),
    ("You'll need to confirm the tools are actually there. Restart your client.",
     True),
    ("You must be able to confirm the tools are actually there. Restart your "
     "client.", True),
    ("You first must confirm the tools are actually there. Restart your "
     "client.", True),
    ("You also must confirm the tools are actually there. Restart your "
     "client.", True),
    ("You must first carefully confirm the tools are actually there. Restart "
     "your client.", True),
    ("You ought not to restart your client until you have confirmed the tools "
     "are actually there.", True),
    ("Confirm the tools in the client's MCP/tool list, then restart your "
     "client.", True),
    ("Confirm the tools appear in the client's MCP/tool list, then restart your "
     "client.", True),
    ("Restart your client, then confirm the tools in the client's MCP/tool "
     "list.", False),
    ("You're going to need to confirm the tools are actually there. Restart "
     "your client.", False),
    # (14) round-7: the adverb run must also repeat at the CHAIN BOUNDARY
    #      (`will first need to`), and `list` is a NOUN after a determiner, not
    #      the closure's verb — a pure absence claim naming the list is GREEN,
    #      while the same clause with a real observation verb stays RED.
    ("You will first need to confirm the tools are actually there. Restart "
     "your client, then run a query.", True),
    ("You will also need to confirm the tools are actually there. Restart your "
     "client.", True),
    ("You will still need to confirm the tools are actually there. Restart your "
     "client.", True),
    ("You will then need to confirm the tools are actually there. Restart your "
     "client.", True),
    ("Don't restart your client until the tools appear in the list.", False),
    ("You shouldn't restart your client unless the tools are missing from the "
     "list.", False),
    ("Don't restart your client until you can see the tools in the list.", True),
    # (15) round-8: `had better` is the modal the file already names twice, and the
    #      semi-modal's own `to` is a position the adverb runs must reach.
    #      DECLARED, not fixed: a MULTI-WORD adverb phrase (`first of all`,
    #      `once again`) and `be to` (`You are to confirm …`) stay GREEN — the run
    #      is single-token by construction and `be to` is a member enumeration,
    #      not a class.
    ("You had better confirm the tools are actually there. Restart your client, "
     "then run a query.", True),
    ("You'd better confirm the tools are actually there. Restart your client, "
     "then run a query.", True),
    ("You will need first to confirm the tools are actually there. Restart your "
     "client, then run a query.", True),
    ("You need first to confirm the tools are actually there. Restart your "
     "client, then run a query.", True),
    ("Restart your client, then you had better confirm the tools are actually "
     "there.", False),
    ("You will need to first of all confirm the tools are actually there. "
     "Restart your client.", False),
    ("You are to confirm the tools are actually there. Restart your client, "
     "then run a query.", False),
    # (16) round-10: the prior-completion marker accepts a PAST-TENSE predicate, so
    #      a concessive about an earlier session is GREEN instead of being graded
    #      as a deferral (the over-block direction, #3429).
    ("If you checked the tools earlier, restart your client, then confirm the "
     "tools are actually there.", False),
    ("If you confirmed the tools last time, restart your client, then confirm "
     "the tools are actually there.", False),
    # …while a PRESENT-tense condition with the same adverbial is a real deferral
    # and stays graded.
    ("If you check the tools before restarting, restart your client.", True),
    # ⛔ DECLARED RESIDUAL of this pin — #14738. A confirmation POSTPOSED behind a
    # dangling subordinator ("… verify the tools once you restart") is graded RED
    # even though the restart comes first in it. It is a known FALSE RED tracked
    # in #14738, not a hole: the forms demand the confirmation sit in the
    # connective's own clause. Pinned at its CURRENT measured verdict (RED = True)
    # with the DESIRED verdict named in words, so fixing #14738 shows up as a
    # deliberate row flip rather than as silence. EXPECTED AFTER #14738: False.
    # (#14679 review round 10 — this boundary was declared only in the tracker,
    # never in the file a reader inspects.)
    ("You can verify the tools are actually there once you restart your "
     "client.", True),
    ("You can confirm the tools are actually there after you restart your "
     "client.", True),
    # (8) a LEADING CONJUNCTION introduces a sentence exactly as `Now`/`Then` do.
    ("And confirm the tools are actually there. Restart your client, then run "
     "a query.", True),
    ("And first confirm the tools are actually there. Restart your client, "
     "then run a query.", True),
    ("So confirm the tools are actually there. Restart your client, then run "
     "a query.", True),
    # (9) `ought` / `had better` are negated modals like the rest of the closed
    #     list the lead's own comment claims is complete.
    ("You oughtn't restart your client until you have confirmed the tools are "
     "actually there.", True),
    ("You'd better not restart your client until you have confirmed the tools "
     "are actually there.", True),
    # (10) the PRESENT-TENSE completive is the same closed class one inflection over.
    ("Restart your client after you finish confirming the tools are actually "
     "there.", True),
    ("Restart your client once you complete checking the tools are actually "
     "there.", True),
    # (6) the bare copula makes the completive reachable uncontracted, so the
    #     verdict no longer turns on the apostrophe.
    ("Restart your client after you are done confirming the tools are actually "
     "there.", True),
    ("Restart your client once you are finished checking the tools are actually "
     "there.", True),
    ("Restart your client once you are through verifying the tools are actually "
     "there.", True),
    ("Restart your client after you are done with setup, then confirm the tools "
     "are actually there.", False),
    # (4) a NEGATED cue defers the restart into its own `until`/`before` clause,
    #     so the confirmation inside it happens first. `until` was admitted on
    #     the PRE-cue side only.
    ("Don't restart your client until you have confirmed the tools are "
     "actually there.", True),
    ("Do not restart your client until you have confirmed the tools are "
     "actually there.", True),
    ("Never restart your client before you have made sure the tools are "
     "actually there.", True),
    # …and the lead is EVERY NEGATED MODAL, not just the imperative (#14679
    # review at `fdaaa23d016c`): `shouldn't` is ordinary doc prose and its
    # deferral is the same inversion. Each measured GREEN before the lead was
    # widened, and each is GREEN again if it is narrowed back.
    ("You shouldn't restart your client until you have confirmed the tools are "
     "actually there.", True),
    ("You shouldn't restart your client until you've confirmed the tools are "
     "actually there.", True),
    ("You should not restart your client until you have confirmed the tools are "
     "actually there.", True),
    ("You must not restart your client until you have confirmed the tools are "
     "actually there.", True),
    # …and the arm still does not reach a negation that is NOT on the cue's lead,
    # nor a forward order that merely contains `can't`:
    ("You can't confirm the tools are actually there until you restart your "
     "client.", False),
    ("You shouldn't restart your client unless the tools are missing.", False),
    # …while a negated cue followed by a FORWARD step stays GREEN (the arm is
    # monotone: it only fires on a deferring connective + a confirmation).
    ("Don't restart your client, then confirm the tools are actually there.",
     False),
    # …and the presence-only shape stays GREEN, declared above `_NEGATED_CUE_LEAD_RE`.
    ("Don't restart your client until the tools appear.", False),
    # ⛔ CYCLE 46 — FOUR CLOSED-VOCABULARY GAPS, each a GREEN twin of a row that
    # was already RED, each measured GREEN before its fix and closed at the root.
    #
    # 1. `unless` sat in NO connexion list — not this one, not form (a)'s, not
    #    form (e)'s — so the whole negated-cue arm was unreachable for the most
    #    idiomatic English spelling of "not … until". The `until` twin above was
    #    RED; this one was GREEN, doc-level (the pin PASSED an inverted
    #    paragraph). It is `_AND_SUBORDINATE_HALF_RE`'s own `if|unless|until`
    #    class, one arm over.
    ("Don't restart your client unless you have confirmed the tools are "
     "actually there.", True),
    ("Never restart your client unless you've confirmed the tools are actually "
     "there.", True),
    #    …while the presence-only `unless` row above stays GREEN: the arm is
    #    monotone and fires only on a deferring connective AND a confirmation.
    #
    # 2. A BARE markdown marker survived `if c.strip()` and was then emptied by
    #    `lstrip(_PRE_CUE_PREFIX)` (which carries `*` and `>`). `_SEQUENCER_
    #    FRAGMENT_RE` cannot match the empty string, so the walk-back could not
    #    pop it and the function returned `['']` — TRUTHY — as the governing
    #    clause, disarming the entire pre-cue half. The dash twin was RED.
    ("* Confirm the tools are actually there.\n"
     "* Restart your client, and the tools appear in the client's MCP/tool list.",
     True),
    ("> Confirm the tools are actually there.\n"
     "> Restart your client, and the tools appear in the client's MCP/tool list.",
     True),
    # 3. `_SEQUENCER_FRAGMENT_RE`'s own declared "modal subjects" class was seven
    #    modals short, so `then you can restart` was not recognised as a fragment
    #    and BECAME the governing clause, hiding the imperative it sequences. The
    #    pinned `then you must` twin was already RED — same class, same commit's
    #    doctrine, two lists disagreeing.
    ("Confirm the tools are actually there, then you can restart your client.",
     True),
    ("Confirm the tools are actually there, then you could restart your client.",
     True),
    # 4. `+` and BOTH ordinal spellings were not statement breaks, so the NEXT
    #    step's marker stayed attached to the pre-cue clause as a trailing
    #    decoration (`… there. +`) and defeated form (f)'s `^` anchor. `-` and
    #    `1.` were RED, so this was a marker family asserted, never measured.
    ("+ Confirm the tools are actually there.\n"
     "+ Restart your client, and the tools appear in the client's MCP/tool list.",
     True),
    ("1) Confirm the tools are actually there.\n"
     "2) Restart your client, and the tools appear in the client's MCP/tool list.",
     True),
    ("(1) Confirm the tools are actually there.\n"
     "(2) Restart your client, and the tools appear in the client's MCP/tool list.",
     True),
    # ⛔ CYCLE 47 — THE REGRESSIONS THE CYCLE-46 FIXES THEMSELVES INTRODUCED, each
    # measured by a fresh-context reviewer at `5a676093c4` and closed here.
    #
    # 5a. `unless` reached presence and observation copy the arm exists to leave
    #     GREEN: `_CONFIRM_PREDICATE` carries `see` and `list`, so a negated
    #     OBSERVATION (`see no tools`) passed as a confirmation, and the new
    #     `tool list` OBJECT matched the verb `list` as its own confirmation. The
    #     `until` twin never exposed either, because neither had a `tool list` in
    #     it. The arm now applies the same `_negated_observation_only` skip the
    #     post-cue loop applies, and requires the predicate to sit OUTSIDE the
    #     tools-object span.
    ("You shouldn't restart your client unless the tool list is empty.", False),
    ("Don't restart your client unless you see no tools in the list.", False),
    # 5b. The `^`-anchored statement-break arms are applied to the POST-cue
    #     SUBSTRING too, where `^` is mid-sentence — so `^[-*+]\s*` / `^>\s*`
    #     emptied the whole post-cue window and NO post-cue form was applied. The
    #     `^` arms now carry `-` only (the only member the table actually needs);
    #     the whitespace-bounded arms do the rest, because the paragraph is
    #     flattened before it is split.
    ("Restart your client, **after confirming the tools are actually there**.",
     True),
    ("Restart your client, *after confirming the tools are actually there*.",
     True),
    ("> Restart your client,\n"
     "> after confirming the tools are actually there.", True),
    # 5c. One word outside the fragment class defeated the walk-back exactly as
    #     `must` used to; `probably|likely|want|better|had|ought` add no
    #     imperation, and `_NEGATED_CUE_LEAD_RE` already calls `ought`/`had
    #     better` the same lead.
    ("Confirm the tools are actually there, then you should probably restart "
     "your client.", True),
    ("Confirm the tools are actually there, then you will likely restart your "
     "client.", True),
    ("Confirm the tools are actually there. You may want to restart your "
     "client.", True),
    ("Confirm the tools are actually there. You had better restart your "
     "client.", True),
    # …and the intensifier arm is pinned too, because an unpinned list is the
    # drift this block exists to stop: measured, removing ONLY these words from
    # the class flips no row, so they would otherwise ship unmeasured.
    ("Confirm the tools are actually there, then you should really restart "
     "your client.", True),
    ("Confirm the tools are actually there, then you simply restart your "
     "client.", True),
    # 5d. ⛔ DECLARED RESIDUAL — a COMMA-LESS pre-cue coordinated imperative.
    #     `_cue_governing_clause` splits the pre-cue statement on `[,;]` only, so
    #     the confirmation reached through a bare `and` is never split out and
    #     form (f)'s `^` anchor never sees it:
    #
    #         RED    Delete the old entry, and confirm the tools … Restart …
    #         GREEN  Delete the old entry and confirm the tools … Restart …
    #
    #     Declared rather than widened: splitting on a bare ` and ` would cut
    #     `tools and config` and `save and close` in two, and the file's doctrine
    #     is that a decoration or conjunction that CAN be ordinary prose is not
    #     made a splitter to catch one contrived sentence. Pinned so the residual
    #     is measured and visible instead of silent, and so a future tightening
    #     shows up as a deliberate row flip.
    ("Delete the old entry and confirm the tools are actually there. Restart "
     "your client, then run a query.", False),
    # ⛔ CYCLE 48 — the fragment class was still a whitelist that missed the
    # classes it CLAIMS (pronouns, copulas) and every contraction. One word
    # defeated the walk-back and the pin PASSED on plainly inverted copy. The
    # `then you must` twin was already RED.
    ("Confirm the tools are actually there, then you'll need to restart your "
     "client, then confirm the tools are actually there.", True),
    ("Confirm the tools are actually there, then you are ready to restart your "
     "client, then confirm the tools are actually there.", True),
    ("Confirm the tools are actually there, and once that's done, restart your "
     "client, then confirm the tools are actually there.", True),
    ("Confirm the tools are actually there, then it is best to restart your "
     "client, then confirm the tools are actually there.", True),
    ("Confirm the tools are actually there, then you will be able to restart "
     "your client, then confirm the tools are actually there.", True),
    #⛔ CYCLE 48 — the presence-adverb run was spelled TWICE and both copies knew
    # only `actually`, so a conjunct that DOES claim the running session's tools
    # was treated as config/install-side and exempted. `all present` was already
    # RED because `present` is a bare token; `all there` was GREEN.
    ("Restart your client after confirming the config lists the tools and the "
     "tools are all there, then confirm the tools are actually there.", True),
    ("Restart your client after confirming the tools are installed and the "
     "tools are really there, then confirm the tools are actually there.",
     True),
    # ⛔ CYCLE 49 — the fragment class had NO `\w+ly` arm while `_ADVERBS` is the
    # OPEN class, so one adverb re-opened the pre-cue half; the filler idioms and
    # six more contractions were the same one-word class.
    ("Confirm the tools are actually there, then quickly restart your client.",
     True),
    ("Confirm the tools are actually there, then immediately restart your "
     "client.", True),
    ("Confirm the tools are actually there, then again restart your client.",
     True),
    ("Confirm the tools are actually there, and additionally restart your "
     "client.", True),
    ("Confirm the tools are actually there, then all you need to do is restart "
     "your client.", True),
    ("Confirm the tools are actually there, then the next step is to restart "
     "your client.", True),
    ("Confirm the tools are actually there, then it'd be best to restart your "
     "client.", True),
    ("Confirm the tools are actually there, then you are going to restart your "
     "client.", True),
    # …and `_CONFIG_PREP_RE`'s noun list now carries `_CONFIG_NOUN_RE`'s: the same
    # noun scoped the clause file-side in noun position and session-side in prep
    # position, a one-word substitution flipping the verdict.
    ("Restart your client after confirming the tools appear in your installer.",
     False),
    ("Restart your client after confirming the tools appear in your configs.",
     False),
    # ⛔ CYCLE 49 — DECLARED RESIDUAL: MARKDOWN BLOCK STRUCTURE. A BLANK LINE
    # between two list items puts the confirmation and the cue in DIFFERENT
    # paragraphs, and `_restart_cue_order_violation` grades per paragraph, so the
    # confirmation is never read. A TABLE never splits at all (`|` is stripped by
    # `_DECORATION_RE` but is not a statement break). Both are declared rather
    # than fixed: grading ACROSS paragraphs re-opens every decision this pin made
    # about "only the cue's own statement" (a concession in a previous paragraph
    # is ordinary copy), and adding `|` as a statement break would cut cell text
    # in two for no gain elsewhere. Pinned so the residual is MEASURED and
    # visible, and a future tightening shows up as a deliberate row flip.
    ("## Activate\n\n1. Confirm the tools are actually there.\n\n"
     "2. Restart your client, then run a query.", False),
    # ── CYCLE 50 — the PREPOSITIONAL ASIDE, the adverb family, and `become` ──
    # ⛔ A COMMA-DELIMITED ASIDE. Each of these measured GREEN before
    # `_ASIDE_RE`, so the canonical inversion walked through a shape the docs
    # themselves use; the forward control below it is the guard on the other
    # direction (an aside that is NOT between a predicate and its object).
    ("Restart your client after verifying, in the client's MCP/tool list, that "
     "the tools are actually there.", True),
    ("Restart your client once you have confirmed, in the client's tool list, "
     "that the tools are actually there.", True),
    ("Restart your client after checking, under MCP in the client settings, "
     "that the tools are actually there.", True),
    # ⛔ THE ADVERB FAMILY, not just the `-ly` arm: `always`/`often`/`never`/`ever`
    # were absent from `_ADVERBS`, so one inserted word re-opened the pre-cue half
    # while the `First` twin was a declared-RED row.
    ("Always confirm the tools are actually there. Restart your client, then run "
     "a query.", True),
    ("You must always confirm the tools are actually there. Restart your client, "
     "then run a query.", True),
    ("Please always confirm the tools are actually there. Restart your client, "
     "then run a query.", True),
    # ⛔ THE CHANGE-OF-STATE COPULA — `are available` is RED, so `become
    # available` must be too.
    ("Restart your client once the tools become available.", True),
    ("Restart your client when the tools become available.", True),
    # ⛔ DECLARED RESIDUAL: A PRE-CUE APPOSITIVE. The clause that governs the cue
    # is the LAST one, and an em-dash or comma appositive makes that last clause a
    # NOUN PHRASE (`the ones listed under MCP`) rather than the confirmation, so
    # nothing is graded (#14679 review at `8c96f5de`, measured — the bare twin
    # two rows above is RED). Declared rather than fixed: folding a noun-phrase
    # appositive back into its predecessor is clause-level coordination, and this
    # file's own record is that THREE speculative cuts of that shape each shipped
    # a fresh seam (see `_and_half_opens_its_own_clause`'s block). Pinned so the
    # residual is MEASURED and a future tightening shows up as a deliberate flip.
    ("Confirm the tools are actually there \u2014 the ones listed under MCP, then "
     "restart your client.", False),
    ("Confirm the tools are actually there, the ones listed under MCP, then "
     "restart your client.", False),
    # ── cycle 52 — the aside at a CLAUSE BOUNDARY, and the periphrastic modals ──
    # ⛔ `_ASIDE_RE`'s match includes the aside's LEADING comma, so replacing it
    # with a bare space GLUED two clauses together and the `^`-anchored forms (c)
    # and (f) could never reach an inversion that was still there — the fix from
    # cycle 50/50c was one substitution away from re-opening these (#14679 review
    # at `eeb42a4f`, measured):
    #
    #     RED    … the config is right, but first confirm the tools are actually there.
    #     GREEN  … the config is right, in the client settings, but first confirm …
    #
    # `_strip_asides` now keeps ONE comma when the token after the aside begins a
    # new clause, and removes the aside whole when it continues the same clause
    # (an object tail: a determiner, a pronoun, `that`, a backticked token).
    ("Restart your client after confirming the config is right, in the client "
     "settings, but first confirm the tools are actually there.", True),
    ("Delete the old entry, in the client's MCP/tool list, but first confirm the "
     "tools are actually there. Restart your client.", True),
    ("Delete the old entry, in the client's MCP/tool list, **confirm the tools "
     "are actually there**. Restart your client.", True),
    ("Restart your client after saving the config, in the settings page, and "
     "confirm the tools are actually there.", False),
    # ⛔ THE PERIPHRASTIC MODALS ARE THE SAME INSTRUCTION. The modal slot carried
    # only the auxiliaries, so 15 of 18 periphrastic spellings a doc might
    # plausibly use graded GREEN while their `You must …` twin is declared RED
    # (#14679 review at `eeb42a4f`, measured).
    ("You are required to confirm the tools are actually there. Restart your "
     "client, then run a query.", True),
    ("You will be required to confirm the tools are actually there. Restart "
     "your client, then run a query.", True),
    ("You're supposed to confirm the tools are actually there. Restart your "
     "client, then run a query.", True),
    ("You've got to confirm the tools are actually there. Restart your client, "
     "then run a query.", True),
    ("One must confirm the tools are actually there. Restart your client, then "
     "run a query.", True),
    # ⛔ DECLARED RESIDUAL: the SUBJECTLESS-CONTRACTION spelling `You've to` \u2014
    # non-standard English (the standard forms are `you have to` / `you've got
    # to`, both graded RED), so it is declared rather than given its own arm.
    ("You've to confirm the tools are actually there. Restart your client, then "
     "run a query.", False),
    # ── cycle 53 — the cue's OTHER spelling, and the elliptical confirmation ──
    # ⛔ THE CUE IS SPELLED TWO WAYS IN THE PINNED DOC. `docs/quickstart-cloud.md:135`
    # says "Restart YOUR client"; `:80` says "restart THE client". The cue was a
    # literal, so the doc's SECOND restart instruction was never a cue at all and
    # inverting it shipped GREEN while the function's own docstring claimed it
    # grades "EVERY CUE PARAGRAPH" (#14679 review at `b5b1ee005941`, measured).
    ("Restart the client after confirming the tools are actually there.", True),
    # …and the elliptical wording in the SAME register stays GREEN: declared
    # below, next to its twin, rather than asserted RED here.
    ("Look for the `tortoise_*` tools, then restart the client.", False),
    ("Restart the client, then confirm the tools are actually there.", False),
    ("Verify the load the only way it can be verified \u2014 restart the client and "
     "look for the `tortoise_*` tools.", False),
    # ⛔ DECLARED RESIDUAL: THE ELLIPTICAL `look for <object>` CONFIRMATION — an
    # imperative whose object IS the thing being confirmed but which carries NO
    # predicate word (`look for the tools, then restart`). Every form (f) arm above
    # requires a predicate after the object; a `look|search|check + for + object`
    # arm was written and MEASURED to fire on no shape at all (it is reachable only
    # with a predicate the elliptical copy does not have), so it was deleted rather
    # than carried as an unpinned arm. Pinned here so the gap is visible and a
    # future tightening shows up as a deliberate flip.
    ("Look for the tortoise_* tools, then restart your client.", False),
    ("Look for the tools section below for the full list. Restart your client, "
     "then confirm the tools are actually there.", False),
    # ── cycle 54 — the EMPHATIC `Do`, and the `going to` arm pinned ──
    # ⛔ AN EMPHATIC `Do` INSERTED BEFORE THE PRE-CUE IMPERATIVE defeated form (f)
    # completely — the same one-inserted-word defect this file closed for `First`,
    # `Please first`, `Always` and `You must` (#14679 review at `f16e32451480`,
    # measured; a `Do ` prefix flipped 119 of 392 declared-RED rows GREEN, 52
    # after this fix, and those 52 are prefixings that make the line ungrammatical).
    ("Do make sure the tools are actually there. Restart your client, then run "
     "a query.", True),
    ("Do confirm the tools are actually there. Restart your client, then run a "
     "query.", True),
    ("Please do confirm the tools are actually there. Restart your client, then "
     "run a query.", True),
    ("Restart your client, then do confirm the tools are actually there.", False),
    # ⛔ `be going to|going to` in the modal slot was exercised by NO row, so a
    # regression in it was invisible; its contracted form is what carries it.
    ("You're going to confirm the tools are actually there. Restart your client, "
     "then run a query.", True),
    # ── cycle 50b — the ANAPHOR and the doc's own token are the object too ──
    # ⛔ `_TOOLS_OBJECT` graded only `tools` / `tool list` / `tools list`, so a
    # PRONOUN standing for the tools — and the token `docs/quickstart-cloud.md:135`
    # itself uses — shipped GREEN while the `the tools` twin one word away was a
    # declared-RED row. Same class as #14717 (`tool list`): the pin's own docs
    # ship a spelling its patterns could not see (#14679 review, third fresh
    # context, measured at `6fc1b35c`):
    #
    #     RED    Restart your client after confirming the tools are actually there.
    #     GREEN  Restart your client after confirming they are actually there.
    #     GREEN  Restart your client once you have verified they're actually there.
    #     GREEN  Restart your client once `tortoise_*` shows up in the client's
    #            MCP/tool list.
    #
    # The anaphor is only a legitimate object where a CONFIRMATION predicate has
    # already scoped what it refers to, which is exactly the position these forms
    # grade — the forward controls below are the guard on the other direction.
    ("Restart your client after confirming they are actually there.", True),
    ("Restart your client once you have verified they're actually there.", True),
    ("Restart your client once `tortoise_*` shows up in the client's MCP/tool "
     "list.", True),
    ("Restart your client, then confirm they are actually there.", False),
    ("Restart your client, then confirm `tortoise_*` appears in the list.",
     False),
    ("Restart your client after saving the file, and they will appear in the "
     "list.", False),
    # ⛔ DECLARED RESIDUAL: THE PREDICATE'S NOMINALIZATION. `_CONFIRM_PREDICATE`
    # is the VERB closure (plus the copula `be sure|certain`), so the noun form
    # carries no row and is ungraded — a predicate-vocabulary gap of the same
    # family the block above declares for `after making certain the tools …`.
    # Pinned rather than fixed: a `confirmation that` arm is a second grammar
    # (noun + complementiser), not a member of a verb list.
    ("Restart your client after confirmation that the tools are actually "
     "there.", False),
    # ⛔ CYCLE 50 — four one-token gaps, each a GREEN twin of a declared-RED row.
    # `whenever`/`as soon as` are the spellings the file itself names as `when`/
    # `once`; `can`+`n't` was structurally DEAD (the double n), so the most
    # common negated modal in English was GREEN; the negated-cue arm read RAW
    # pre-cue text, so any markdown between the negation and the cue broke it;
    # and `_PRESENCE_ADVERB` was a closed list beside an OPEN sibling.
    ("Confirm the tools are actually there, and whenever that's done, restart "
     "your client, then confirm the tools are actually there.", True),
    ("Confirm the tools are actually there, and as soon as that's done, "
     "restart your client, then confirm the tools are actually there.", True),
    ("You can't restart your client until you have confirmed the tools are "
     "actually there.", True),
    ("You won't restart your client until you have confirmed the tools are "
     "actually there.", True),
    ("You will not restart your client until you have confirmed the tools are "
     "actually there.", True),
    ("**Don't** restart your client until you have confirmed the tools are "
     "actually there.", True),
    ("Don't **restart your client** until you have confirmed the tools are "
     "actually there.", True),
    ("Restart your client after confirming the config lists the tools and the "
     "tools are temporarily there, then confirm the tools are actually there.",
     True),
    # ⛔ CYCLE 50 — DECLARED RESIDUAL (1): a LEAD-IN clause that fronts the
    # confirmation. Form (f) is `^`-anchored on the imperative, so a lead phrase
    # hides it, and the lead phrases are an UNBOUNDED set of English idioms:
    # `It is best to confirm …`, `It's a good idea to confirm …`, `The first step
    # is to confirm …`, `Your first task is to confirm …`, `Start by confirming
    # …`, `Begin by confirming …`, `Remember to confirm …`, `Don't forget to
    # confirm …`, `Be careful to confirm …` (all measured GREEN). Declared, not
    # fixed: widening form (f)'s anchor to swallow arbitrary lead phrases is the
    # direction this file records as the one that gets a pin deleted (#3429 —
    # enumerating the lead phrasings that must stay GREEN produced 18 of 24
    # false REDs on correct copy). Pinned so the residual is MEASURED.
    ("It is best to confirm the tools are actually there. Restart your client, "
     "then run a query.", False),
    ("The first step is to confirm the tools are actually there. Restart your "
     "client, then run a query.", False),
    # ⛔ CYCLE 50 — DECLARED RESIDUAL (2): a pre-restart MARKER PHRASE THAT
    # CARRIES ITS OWN COMMA. Post-cue clauses are split on `,` before grading, so
    # `but before that, confirm …` becomes TWO clauses (`but before that` /
    # `confirm the tools …`) — this said "three", which the code does not do, and
    # a count is a measurement (#14679 review cycle 51) — and the marker never
    # meets the predicate it orders.
    # The comma-less twin `but first confirm …` IS RED. Declared rather than
    # fixed because the remedy is merging clauses across commas — the
    # `_and_half_opens_its_own_clause` problem one conjunction over, whose own
    # measured history is that a naive merge reds ordinary copy. Pinned.
    ("Restart your client, but before that, confirm the tools are actually "
     "there.", False),
    ("Restart your client, but before doing so, confirm the tools are actually "
     "there.", False),
    # ⛔ CYCLE 51 — three P1s and one P2, each a one-glyph/one-word edit of a
    # declared-RED row.
    #
    # 51a. TYPOGRAPHIC QUOTES. GFM does no smart-punctuation substitution, so a
    #      `“` in a source file reaches the reader unchanged and this repo's docs
    #      use them. The decoration strip and the full-stop lookahead carried
    #      ASCII quotes only, so a quoted imperative was never graded.
    ("\u201cConfirm the tools are actually there.\u201d Restart your client, then "
     "run a query.", True),
    # 51b. THE DOCS' OWN `tortoise_*` TOKEN. The decoration strip removed the `_`
    #      (followed by `*`, a non-word char) and then the `*`, rewriting the
    #      object to `tortoise` — which `_TOOLS_OBJECT` cannot match, so form (f)
    #      went blind on the very token `docs/quickstart-cloud.md:135` uses. Every
    #      other form graded it (their post-cue twins are RED).
    ("Confirm `tortoise_*` shows up in the client's MCP/tool list, then restart "
     "your client.", True),
    # 51c. ONE ADVERB BEFORE THE CUE. `_ADVERBS` exists for this defect and every
    #      other arm takes it; the negated-cue lead had no slot for it, and
    #      `never`/`ever` were not members of `_ADVERBS` at all despite its
    #      comment claiming the frequency family had been added.
    ("Don't ever restart your client until you have confirmed the tools are "
     "actually there.", True),
    # 51d. `_PRESENCE_ADVERB`'s closed remainder is now `_ABSENCE_ADVERB_RUN`'s,
    #      so an unlisted adverb no longer reads the conjunct as file-side.
    ("Restart your client after confirming the config lists the tools and the "
     "tools are also there, then confirm the tools are actually there.", True),
    # ⛔ CYCLE 51 — DECLARED RESIDUAL: DISPLACED NEGATION (`but not until`). The
    # deferring connective is consulted only inside the `cue_is_negated` branch,
    # and that branch requires the negation to ABUT the cue in the PRE-cue text.
    # Here the negation sits AFTER the cue, and no form owns a bare `until`/
    # `before` + confirmation post-cue:
    #
    #     RED    Don't restart your client until you have confirmed the tools …
    #     GREEN  Restart your client, but not until you have confirmed …   (measured)
    #
    # Declared rather than fixed because the remedy is a new POST-cue negated-
    # deferral form, and every post-cue addition in this file's history has cost
    # a false RED on forward copy (`Restart your client; once the tools are
    # listed, run …` and its colon/dash twins are the recorded example). Pinned.
    ("Restart your client, but not until you have confirmed the tools are "
     "actually there.", False),
    # ⛔ CYCLE 52 — CYCLE 51'S OWN REGRESSION, and the class disagreement behind it.
    #
    # 52a. `never`/`ever` were added to the SHARED `_ADVERBS` to fix the
    #      negated-cue lead, and form (f) consumes that class as a LEADING ADVERB
    #      RUN BEFORE THE PREDICATE — so a negated imperative became a false RED.
    #      The decision to keep them out was already recorded in the file; the
    #      caller that needs them is a DIFFERENT POSITION, so it now gets a
    #      caller-local run (`_CUE_LEAD_ADVERBS`) and the shared class is restored.
    ("Never confirm the tools are actually there. Restart your client, then run "
     "a query.", False),
    ("You never need to confirm the tools are actually there. Restart your "
     "client, then run a query.", False),
    # …while the caller that DOES need them still reds.
    # 52b. `_SEQUENCER_FRAGMENT_RE` carried `always` but not `often|never|ever` —
    #      the same closed frequency family, split across two arms. A fragment it
    #      does not pop becomes the governing clause and the confirmation behind
    #      it is discarded.
    ("Confirm the tools are actually there, then never restart your client.",
     True),
    ("Confirm the tools are actually there, then often restart your client.",
     True),
    # ⛔ CYCLE 54 — ONE FILLER WORD BEFORE THE CUE RE-OPENED THE PRE-CUE HALF.
    # `_SEQUENCER_FRAGMENT_RE` is a CLOSED list of filler words, so an unlisted
    # one made `then go` look SUBSTANTIVE, the walk-back stopped there, and the
    # confirmation before it was never graded — the suite stayed GREEN while
    # `_restart_cue_order_violation` returned None on the declared-RED control's
    # twin (measured at `f16e324514`, and end-to-end: the quickstart paragraph
    # replaced with the `then go restart` spelling left 724 passed).
    # `_is_continuation_fragment` now decides by the OBJECT the sequencer clause
    # does or does not name, which is why the three rows the residual comment
    # above declares GREEN stay GREEN (they name one: `the file`, `the editor`).
    ("Confirm the tools are actually there, then go restart your client.", True),
    ("Confirm the tools are actually there, then go ahead and restart your "
     "client.", True),
    ("Confirm the tools are actually there, then proceed to restart your "
     "client.", True),
    ("Confirm the tools are actually there, then remember to restart your "
     "client.", True),
    ("Confirm the tools are actually there, then feel free to restart your "
     "client.", True),
    ("Confirm the tools are actually there, then all that's left is to restart "
     "your client.", True),
    # …and the OBJECT bound that keeps the declared residual GREEN is pinned from
    # the other side, so widening the fragment test to "matches no form" flips
    # these two instead of shipping an over-block.
    ("Confirm the tools are actually there, then save the file, then restart "
     "your client.", False),
    ("Confirm the tools are actually there, then run a query, then restart "
     "your client.", False),
    # ⛔ CYCLE 54 — A SEMI-MODAL BETWEEN THE NEGATION AND THE CUE. `_CUE_LEAD_ADVERBS`
    # gave the negated-cue lead an adverb slot but no `to`-semi-modal slot, so the
    # negation stopped abutting the cue and the whole negated arm was unreachable —
    # `need` was already a member of the alternation; only its POSITION as
    # `need to` was unhandled (form (f) has carried that chain for the positive
    # sibling all along). The file's own comment cites "you shouldn't need to
    # restart …" as ordinary prose; that phrasing was the ungraded one (measured).
    ("You shouldn't need to restart your client until you have confirmed the "
     "tools are actually there.", True),
    ("You don't need to restart your client unless you have confirmed the "
     "tools are actually there.", True),
    ("You don't need to restart your client before you have confirmed the "
     "tools are actually there.", True),
    # ── cycle 55 — the review of cycle 54 found the FRAME, the CLASS COPY and the
    #    CONTRACTION one arm further out each time. Every row below measured GREEN
    #    before the arm it names existed, and is the guard against removing it.
    # 55a. THE COPULAR FRAME. "names a determiner" was a PROXY for "adds an
    #      action", and the dominant spelling of this filler carries one.
    ("Confirm the tools are actually there, then the only thing left to do is "
     "restart your client.", True),
    ("Confirm the tools are actually there, then the last thing you need to do "
     "is restart your client.", True),
    ("Confirm the tools are actually there, then your only remaining step is to "
     "restart your client.", True),
    ("Confirm the tools are actually there, then on your machine restart your "
     "client.", True),
    # 55b. THE UNCONTRACTED TWIN — one apostrophe defeated the row above it.
    ("Confirm the tools are actually there, then all that is left is to restart "
     "your client.", True),
    ("Confirm the tools are actually there, then all that remains is to restart "
     "your client.", True),
    # 55c. UNLISTED SEQUENCERS — the "closed at the FRONT" claim is now stated as a
    #      measured residual instead.
    ("Confirm the tools are actually there, later, restart your client.", True),
    ("Confirm the tools are actually there, thereafter, restart your client.", True),
    ("Confirm the tools are actually there, once more, restart your client.", True),
    # 55d. THE TWO COPIES OF THE PERIPHRASTIC CLASS AGREE — one row per spelling, so
    #      a member no row depends on cannot sit in the alternation as dead weight.
    ("You are not supposed to restart your client until you have confirmed the "
     "tools are actually there.", True),
    ("You are not allowed to restart your client until you have confirmed the "
     "tools are actually there.", True),
    ("You are not meant to restart your client until you have confirmed the "
     "tools are actually there.", True),
    ("You have not got to restart your client until you have confirmed the "
     "tools are actually there.", True),
    ("You shouldn't want to restart your client until you have confirmed the "
     "tools are actually there.", True),
    ("You don't have to restart your client until you have confirmed the tools "
     "are actually there.", True),
    # 55e. THE CONTRACTIONS OF `be`, which the `ca|wo|sha` arm never covered.
    ("You aren't going to restart your client until you have confirmed the "
     "tools are actually there.", True),
    ("You aren't to restart your client until you have confirmed the tools are "
     "actually there.", True),
    # 55f. ⛔ THE FALSE RED CYCLE 54 INTRODUCED, PINNED SO IT CANNOT COME BACK. The
    #      new sequencer arm correctly stops popping at `then go ahead and`, which
    #      exposed this completed concession; `_PRIOR_COMPLETION_RE` did not know
    #      the phrase, so forward copy went RED (GREEN before cycle 54, RED at its
    #      head).
    ("Even if you confirmed the tools in an earlier session, then go ahead and "
     "restart your client, then confirm the tools are actually there.", False),
    ("If you confirmed the tools in an earlier session, then go ahead and "
     "restart your client, then confirm the tools are actually there.", False),
    # ── cycle 56 — the third review found the gate the peer lane's predicate arm
    #    was missing, the semi-modal members cycle 55 dropped, and four unpinned
    #    arms. Every row below measured GREEN (or, for the forward ones, RED)
    #    before the fix it names.
    # 56a. ⛔ A BARE PAST-TENSE PREDICATE EXEMPTS THE PRESENT-PERFECT CONDITIONAL,
    #      which is form (a)'s CORE SHAPE — an inversion shipping GREEN:
    ("When you have confirmed the tools are actually there, restart your client.",
     True),
    ("If you have confirmed the tools are actually there, restart your client.",
     True),
    ("When you have checked the tools are actually there, restart your client.",
     True),
    ("When you've verified the tools are actually there, restart your client.",
     True),
    # …while the past-tense concessions the adverbial gate exists for stay GREEN,
    # and the predicate arm covers the whole `_PRE_CUE_VERB` class, not three of it.
    ("If you validated the tools earlier, restart your client, then confirm the "
     "tools are actually there.", False),
    ("If you made sure the tools were there earlier, restart your client, then "
     "confirm the tools are actually there.", False),
    # 56b. `be able to` WAS NOT DEAD — cycle 55 dropped it from the negated slot and
    #      took THREE real inversions GREEN with it (a subset of the class it had just
    #      been fixed to stop copying).
    ("You won't be able to restart your client until you have confirmed the "
     "tools are actually there.", True),
    ("You will not be able to restart your client until you have confirmed the "
     "tools are actually there.", True),
    ("You aren't able to restart your client until you have confirmed the tools "
     "are actually there.", True),
    # 56c. `only at the end` reverses its shape and had no row (0 rows depended on
    #      it); `at that point` was redundant and was dropped instead of declared.
    ("Confirm the tools are actually there, only at the end, restart your "
     "client.", True),
    # 56d. the `be to` lead-in arm was dead — this is the row only it can reach.
    ("Confirm the tools are actually there, then the next move is to restart "
     "your client.", True),
    # 56e. the locative half is a LIST; these four are its measured members.
    ("Confirm the tools are actually there, then with the new config restart "
     "your client.", True),
    ("Confirm the tools are actually there, then via the tray restart your "
     "client.", True),
    ("Confirm the tools are actually there, then during the install restart your "
     "client.", True),
    # ── cycle 57 — the fourth review found three regressions CYCLE 56 INTRODUCED
    #    (`after` reaching a participle, `at that point` dropped, `an?` excluding
    #    `the`) plus two pre-existing holes in the same arms.
    # 57a. ⛔ `after` MUST NOT REACH A PARTICIPLE: a popped clause is DISCARDED,
    #      never graded, so this is the file's canonical inversion shipping GREEN.
    ("Then after confirming the tools are actually there, restart your client.",
     True),
    ("Finally after confirming the tools are actually there, restart your client.",
     True),
    ("Then after checking the tools are actually there, restart your client.",
     True),
    # …while the bare-noun locative that motivated the member stays RED, so the
    # lookahead is not paid for by losing 56e.
    ("Confirm the tools are actually there, then after the config change restart "
     "your client.", True),
    # 57b. `and at that point` — one word from the member cycle 56 called redundant.
    ("Confirm the tools are actually there, and at that point, restart your "
     "client.", True),
    # 57c. `prior to restarting` IS the inversion, not a past-time adverbial.
    ("When you have confirmed the tools prior to restarting, restart your client.",
     True),
    ("When you have confirmed the tools before you restart, restart your client.",
     True),
    # 57d. `you're not` / `they're not` — the negation of `be` with the auxiliary
    #      contracted and `not` separate, which NEITHER existing spelling reached.
    ("You're not supposed to restart your client until you have confirmed the "
     "tools are actually there.", True),
    ("You're not able to restart your client until you have confirmed the tools "
     "are actually there.", True),
    ("You're not going to restart your client until you have confirmed the tools "
     "are actually there.", True),
    ("They're not able to restart the client until they have confirmed the tools "
     "are actually there.", True),
    # 57e. …and the `an?`→`an?|the` widening, from both sides: forward copy that
    #      was a false RED, and the bare clause-final adverb that must stay GREEN.
    ("If you confirmed the tools in the previous session, restart your client, "
     "then confirm the tools are actually there.", False),
    ("If you confirmed the tools on the previous run, restart your client, then "
     "confirm the tools are actually there.", False),
    # …and `|last` was added to the qualifier with no row depending on it (measured:
    # deleting it flipped 0 rows), so it gets one.
    # ── cycle 58 — the `after` pop is decided from the CLAUSE now, so a pronoun or
    #    an adverb between `after` and the participle no longer restores the false
    #    PASS; and `before|prior` is a past-time adverbial EXCEPT when a restart
    #    follows it, which is the inversion itself.
    ("Then after you have confirmed the tools are actually there, restart your "
     "client.", True),
    ("Then after carefully confirming the tools are actually there, restart your "
     "client.", True),
    ("If you verified the tools before the upgrade, restart your client, then "
     "confirm the tools are actually there.", False),
    ("If you checked the tools before the restart, restart your client, then "
     "confirm the tools are actually there.", False),
    # ⛔ DECLARED RESIDUAL, pinned at its CURRENT verdict. The NOMINAL spelling is
    # not closed: `_CONFIRM_PREDICATE` is a verb list, so `your confirmation` does
    # not satisfy it — and popping the clause instead of keeping it does not help
    # either, because the clause is ungraded STANDALONE too, measured with the
    # `then after` lead removed and nothing popped:
    #
    #   _restart_cue_order_violation("After your confirmation that the tools are "
    #                                "actually there, restart your client.") -> None
    #
    # ⛔ Cycle 58 first wrote a different mechanism here — "the retained clause keeps
    # its leading `then after`, which no form's `^` anchor accepts" — and that is
    # FALSE in both directions: form (f) IS `^`-anchored and DOES accept a `then`
    # lead, and form (a), which grades the row this pin adds, has no `^` at all
    # (measured, review cycle 59). The gap is vocabulary, not anchoring. EXPECTED
    # AFTER a wider fix (a noun arm on the predicate): True.
    ("Then after your confirmation that the tools are actually there, restart "
     "your client.", False),

    # ── cycle 59 — the same three arms, from the reviewer's own measured corpus.
    #    (a) A KEPT clause nothing grades: the confirmation before it is discarded
    #        and the inversion ships GREEN. Locative AND frame-noun spellings.
    ("Confirm the tools are actually there, then after the config change verify "
     "the tools are actually there, restart your client.", True),
    ("Confirm the tools are actually there, then the last step is to verify the "
     "tools are actually there, restart your client.", True),
    ("Confirm the tools are actually there, then via the tray check the tools "
     "are actually there, restart your client.", True),
    #    (b) RESTART-ADJACENCY is a set of verb spellings, not two tokens.
    ("If you checked the tools before proceeding to restart, restart your "
     "client, then confirm the tools are actually there.", True),
    ("If you checked the tools before going to restart, restart your client, "
     "then confirm the tools are actually there.", True),
    ("If you checked the tools before the client restarts, restart your client, "
     "then confirm the tools are actually there.", True),
    ("If you checked the tools before the client is restarted, restart your "
     "client, then confirm the tools are actually there.", True),
    #    (c) the past-time adverbial does NOT require a preposition + article.
    ("If you confirmed the tools last session, restart your client, then "
     "confirm the tools are actually there.", False),
    ("If you confirmed the tools last run, restart your client, then confirm "
     "the tools are actually there.", False),
    ("If you confirmed the tools in the last session, restart your client, then "
     "confirm the tools are actually there.", False),
    ("If you checked the tools before, restart your client, then confirm the "
     "tools are actually there.", False),
])
def test_3232_restart_order_pin_grades_the_forms_it_claims(line, inverted):
    doc = f"Some lead-in copy.\n\n{line}\n\nRate limit: **100 requests/min per key**.\n"
    violation = _restart_cue_order_violation(doc)
    if inverted:
        assert violation is not None, (
            f"this inversion must be graded and was not: {line!r}")
    else:
        assert violation is None, (
            f"pinned GREEN and was graded ({violation!r}) — forward copy, or a "
            f"DECLARED residual, per this row's own comment: {line!r}")


def test_3232_pre_cue_verb_carries_every_morphology_the_other_copies_do():
    """⛔ THE `_PRE_CUE_VERB` COPY DRIFTED ON SIX SPELLINGS, and the comment above
    it claimed "ONE DEFINITION". This is a direct pin because the drift has no
    grammatical path through the grader (form (f) is `^`-anchored, so the verb
    must be clause-initial, and `Verified`/`Ensuring` are not imperatives) — the
    honest evidence for a copy that cannot be reached is that the copy itself
    carries the spellings its three siblings do (#14679 review cycle 48).
    """
    import re as _re
    spellings = ("verify", "verifies", "verified", "verifying",
                 "ensure", "ensures", "ensured", "ensuring",
                 "validate", "validates", "validated", "validating",
                 "make sure", "made sure", "making certain")
    for spelling in spellings:
        assert _re.search(_PRE_CUE_VERB, spelling, _re.IGNORECASE), (
            f"_PRE_CUE_VERB cannot match {spelling!r}; the other copies can")


def test_3232_the_and_then_half_reds_for_the_first_anchor_not_by_accident():
    """⛔ RED FOR THE RIGHT REASON, and the reason is the mechanism's OWN.

    The row that pins `… confirm the tools are actually there first, and then run
    a query.` as inverted would also pass if some unrelated arm happened to grade
    the sentence — and then the one shape this cut exists for (`first` stays
    clause-final because the fold was declined) could regress silently while the
    row stayed green. So pin the GRADED CLAUSE: the inversion form (d) reports is
    the `first`-ordered confirmation, which is only clause-final AT ALL because
    `_and_half_opens_its_own_clause` declined to splice `and then run …` onto it.

    Removing that decline reds the parametrized row through its OWN assertion (the
    verdict goes GREEN), so this test is not a substitute for non-vacuity — it is
    what keeps that row meaningful if the verdict ever stays RED for a different
    cause.
    """
    line = ("Restart your client, confirm the tools are actually there first, "
            "and then run a query.")
    assert _restart_cue_order_violation(line) == (
        "confirm the tools are actually there first")


def test_3232_the_two_copies_of_the_periphrastic_modals_agree():
    """⛔ TWO COPIES OF ONE CLASS IS THIS FILE'S MOST REPEATED DEFECT, so the class
    is now ONE constant (`_PERIPHRASTIC_MODAL`) consumed by `_PRE_CUE_ONLY` and by
    `_NEGATED_CUE_LEAD_RE` — and this test is the executable detector that the two
    arms still agree on every spelling a doc might plausibly use.

    Why it is a test and not a comment: cycle 54 widened the NEGATED arm with a
    hand-typed subset (`need|needs|have|has|want|wants|be|going|able`) while the
    positive sibling had carried `be <participle> to` and `have got to` all along,
    and calls them "the same instruction". The result was measured, not theorised:

        RED    You must confirm the tools are actually there. Restart …      (row)
        GREEN  You are not supposed to restart your client until you have confirmed …
        GREEN  You have not got to restart your client until you have confirmed …

    Both arms are asserted arm-by-arm, because the failure mode is one arm
    silently missing a member the other has.
    """
    spellings = ("required", "supposed", "expected", "advised", "meant",
                 "obligated", "allowed", "asked")
    # ⛔ THE NEGATED LEAD IS `$`-ANCHORED against the PRE-CUE TEXT — the span that
    # ends where the cue begins — so it is fed that span, not a whole sentence.
    # (Feeding it a sentence fails for a reason that has nothing to do with the
    # class, which would make this test assert the wrong thing.)
    for spelling in spellings:
        positive = f"You are {spelling} to confirm the tools are actually there."
        pre_cue = f"You are not {spelling} to "
        assert _PRE_CUE_ONLY.search(positive), (
            f"form (f) lost the periphrastic spelling {spelling!r}")
        assert _NEGATED_CUE_LEAD_RE.search(pre_cue), (
            f"the negated-cue lead cannot match {spelling!r}, which form (f) "
            f"carries — the two copies have diverged again")
        # …and end to end, on a real sentence: the pin must GRADE it.
        sentence = (f"You are not {spelling} to restart your client until you "
                    f"have confirmed the tools are actually there.")
        assert _restart_cue_order_violation(sentence) is not None, (
            f"{spelling!r} is in both arms but ships GREEN end to end: {sentence!r}")
    # …and the `have got to` member, which is the same construction with a filler
    # `got` (the one-inserted-word defect this file closes everywhere else).
    assert _PRE_CUE_ONLY.search("You have got to confirm the tools are actually there.")
    assert _NEGATED_CUE_LEAD_RE.search("You have not got to ")
    # ⛔ AND `be able to` — cycle 55 dropped `be|able` from this slot as "dead" and
    # took THREE real inversions GREEN with it (`You won't be able to restart … until
    # you have confirmed …`), which this test could not see because it only listed
    # the periphrastic spellings. A member the two arms disagree about is a red row
    # now, not a footnote.
    assert _NEGATED_CUE_LEAD_RE.search("You won't be able to ")
    assert _NEGATED_CUE_LEAD_RE.search("You aren't able to ")
    # ⛔ and END TO END, because the arm being `$`-anchored on the pre-cue span means
    # a `search` over a whole sentence does not exercise the operator order at all.
    assert _restart_cue_order_violation(
        "You aren't able to restart your client until you have confirmed the "
        "tools are actually there.") is not None


def test_3232_every_cue_paragraph_is_graded():
    """⛔ NOT JUST THE FIRST CUE PARAGRAPH. A troubleshooting or FAQ paragraph
    that repeats the cue can invert the order, and `next(...)` graded only the
    first paragraph containing the cue — so the second one shipped with no signal
    (measured). The doc-level test above cannot cover this: the real quickstart
    has a single cue paragraph."""
    good = "Restart your client, then confirm the tools are actually there."
    bad = "Restart your client after confirming the tools are actually there."
    assert _restart_cue_order_violation(f"{good}\n\n{good}") is None
    assert _restart_cue_order_violation(f"{bad}\n\n{good}") is not None
    assert _restart_cue_order_violation(f"{good}\n\n{bad}") is not None, (
        "a SECOND cue paragraph must be graded — this is the shape that was "
        "silent while only the first paragraph was read")


# The POSITIVE bound's window, pinned. Without these rows the window is unpinned:
# reverting `_tools_appear_after_cue` to a paragraph-wide search left the whole
# suite GREEN (measured), which is exactly the false PASS its docstring forbids.
@pytest.mark.parametrize("line, appears", [
    # the cue's own sentence, and the one immediately after it, both count:
    ("Restart your client, then confirm the tools are actually there.", True),
    ("Restart your client. The tools then appear in the list.", True),
    # …and the bound is CASE-INSENSITIVE like every other object search (it was
    # the one without the flag: a capitalised `Tools` failed a paragraph that
    # carries the clause, with the "must keep a tools-appear clause" message):
    ("Restart your client. Tools then appear in the list.", True),
    # …and the SINGULAR spelling satisfies it, as it does in the pin itself:
    ("Restart your client. Confirm the tool list is complete.", True),
    # a mention TWO sentences later does not, and neither does silence:
    ("Restart your client. The config is reloaded. The tools appear later.",
     False),
    ("Restart your client. Nothing to see here.", False),
])
def test_3232_the_tools_appear_bound_is_sentence_bounded(line, appears):
    doc = f"Some lead-in copy.\n\n{line}\n\nRate limit: **100 requests/min per key**.\n"
    assert _tools_appear_after_cue(doc) is appears, (
        f"the tools-appear window is wrong for {line!r} (expected "
        f"{'a tools clause' if appears else 'no tools clause'})")
