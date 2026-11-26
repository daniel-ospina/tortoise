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

_RESTART_CUE = "Restart your client"
# ⛔ THE CUE IS FOUND CASE-INSENSITIVELY. Every form is IGNORECASE because "case
# carries no meaning in this prose", but the cue itself was matched with `in`, so
# a reword that keeps the required ORDER and merely lower-cases it mid-sentence
# ("Once the config is written, restart your client, then confirm the tools are
# actually there") failed the "must keep its restart cue" assert (measured) and
# silently stopped the paragraph being graded at all.
_RESTART_CUE_RE = re.compile(re.escape(_RESTART_CUE), re.IGNORECASE)


def _cue_start(text: str) -> int:
    """Index of the cue in `text`, case-insensitively; -1 when absent."""
    match = _RESTART_CUE_RE.search(text)
    return -1 if match is None else match.start()

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
_DOC_PLACE = (r"(?:above|below|earlier|later|here|there|in\s+this\s+guide"
              r"|in\s+the\s+table|on\s+this\s+page)")
# Appended immediately after the object in a form's pattern.
_NOT_A_DOC = (r"(?!\s+" + _DOC_NOUN + r"\b)"
              r"(?!\s+" + _DOC_PARTICIPLE + r"\s+" + _DOC_PLACE + r"\b)")

_CONFIRM_PREDICATE = (
    r"\b(?:confirm|check|verif(?:y|ies|ied|ying)|ensur(?:e|es|ed|ing)"
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
_TOOLS_OBJECT = r"(?:\btools\b|\btool\s+lists?\b|\btools\s+lists?\b)"
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
    r"^(?:please\s+)?(?:(?:first(?:ly)?|also|just)\s*,?\s+)?"
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
    r"(?:\w+-)?(?:confirm|check|verify|see|look|ensure)\w*\b[^\n]*?"
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
    r")"
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
_PRE_CUE_PREFIX = " \t,.;:—–-…('\"`*>#_[]{}"


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
    #     admits ANY `-ly` adverb (plus the bare four), and repeats, because a
    #     MANNER adverb does not change the connective's verdict — a closed
    #     four-word list only re-opened the hole for `previously` / `recently` /
    #     `thoroughly` / `also already` (each measured GREEN with the closed
    #     list).
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
    _inversion_form(
        r"\b(?:(?:after|once)\s+"
        r"(?:you(?:'ve|\u2019ve|\s+have)\s+|you\s+|having\s+)?"
        r"(?:(?:\w+ly|very|so|too|quite|rather|first|already|just|also)\s+)*"
        r"|(?:if|when)\s+(?:you(?:'ve|\u2019ve|\s+have)\s+|you\s+|having\s+)?"
        r"(?:(?:\w+ly|very|so|too|quite|rather|first|already|just|also)\s+)*"
        r"(?!(?:see(?:s)?|saw|seen|look(?:ing|s|ed)?|list(?:ing|s|ed)?)\b"
        r"(?!.*?\b(?:confirm|check|verif(?:y|ies|ied)|ensur(?:e|es|ed))\w*\b"
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
        r"[^\n]*?\b(?:first(?:ly)?|beforehand|in\s+advance)\b"
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
        r"|in\s+advance)\b"
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
    #     review cycle 29, measured). The false-RED risk is the same as `once`'s
    #     and is already carried by the two skips applied before every form: an
    #     INSTALL-side state (`… after the tools are installed`) is file-side and
    #     exempt via `_confirmation_scopes_the_config` — which is why the object
    #     lookup there had to admit the singular too. A negated observation
    #     (`… if you see no tools`) is exempt via `_negated_observation_only`.
    #     `if` is deliberately NOT here: it is form (a)'s connective, and adding
    #     it would grade "… if you cannot see the tools", a remedy with the
    #     restart first (measured false RED on form (a)'s wider first cut).
    _inversion_form(
        r"\b(?:once|when|after)\s+(?:[A-Za-z0-9_`*.-]+\s+){0,2}?"
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
        r"(?:are|is|appear|appears|show|shows|exist|exists|load|loads"
        r"|listed|visible)\b"
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
        # ⛔ BOTH RUNS ARE THE FILE'S OWN ADVERB CLASS, not a hand-picked list.
        # The first cut spelled eight words on ONE side and `longer?` on the
        # other, so a single inserted adverb leaked in BOTH directions
        # (#14679 review cycle 32, measured): `not ACTUALLY missing` (presence)
        # was GREEN, while `TEMPORARILY missing` / `JUST missing` / `COMPLETELY
        # gone` (absences) were RED. This is the lesson form (a) already records
        # 300 lines above — "an EIGHT-WORD ADVERB BLACKLIST WAS THE WRONG SHAPE
        # … so the adverb list is gone" — and (a)'s own `(?:\w+ly|very|so|too|
        # quite|rather|first|already|just|also)` is the class reused here. A
        # closed list cannot be completed; the `-ly` slot can.
        # ⛔ ONE NEGATION BRANCH, NOT TWO. A separate `no longer <non-absence>`
        # alternative was measured to flip 0 of 319 rows, because `(?:not|never|
        # no)\b` + its own lookahead already matches it — an arm no row can
        # depend on is dead weight and it was removed (review cycle 32).
        r"(?!\s+"
        r"(?:(?:\w+ly|still|yet|now|very|so|too|quite|rather|first|already"
        r"|just|also)\s+)*"
        r"(?:"
        r"(?:missing|gone|absent|broken|empty|stale)\b"
        r"|(?:not|never|no)\b"
        r"(?!\s+(?:(?:\w+ly|longer|yet|still)\s+)*"
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
_CONFIG_NOUN_RE = re.compile(
    r"(?:\b(?:configs?|settings|installer)\b|\.mcp\.json)", re.IGNORECASE)
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
    r"(?:[`'\"]?)(?:config|settings|\.mcp\.json)\b",
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
_SESSION_AND_CONJUNCT_RE = re.compile(
    r"\band\b[^\n]*?\b(?:actually\s+there"
    r"|(?:are|is|were|was)\s+(?:actually\s+)?there"
    r"|listed|present|visible|appearing|appears?|show(?:s)?\s+up"
    r"|load(?:ed|s)|running|live|in\s+the\s+list)\b", re.IGNORECASE)
_SESSION_PRESENCE_RE = re.compile(
    # ⛔ `there` MUST BE THE COPULA FORM. A bare `\bthere\b` matched existential
    # "there are no duplicate entries", so an entirely FILE-SIDE conjunct was
    # graded as an inversion (measured) — the config exclusion exists to keep
    # exactly that copy GREEN.
    r"\b(?:actually\s+there|(?:are|is|were|was)\s+(?:actually\s+)?there"
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
    r"(?:[;—–]\s*|:\s+|(?:(?<=\s)-(?!-)\s+|(?<=\s)--\s+|^-\s*)"
    r"|\.\s+(?=[A-Z\"'`(\[*_>])"
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
# ⛔ `see THAT …` IS AN INSTRUCTION, NOT AN OBSERVATION. The distinction this
# rule draws is verb-class, so the complementizer reading escaped it: "… but
# first see that no tools are missing" is a pre-restart demand and was GREEN
# (measured) while its `confirm` twin is a declared-RED row.
# ⛔ THE NEGATION MUST BE THE VERB'S OWN OBJECT, NOT ONE FOUND FURTHER ALONG.
# `[^\n]*?` let the match START at the graded claim's own observation-verb
# predicate (`see|saw|seen` are in `_CONFIRM_PREDICATE`) and run ACROSS the claim
# to the appended phrase's `no errors`, so truncating at the match start deleted
# the claim itself: "after you saw the tools are actually there, and see no
# errors in the log" was GREEN while its tail-less twin is a declared RED row
# (measured, forms (a)/(b)/(c)). The object is now adjacent to the verb.
_OBSERVATION_NEGATION_RE = re.compile(
    r"\b(?:see|saw|seen|seeing|notice[sd]?|noticing)\b(?!\s+that\b)\s+"
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
    remainder = clause[: neg.start()]
    return not any(pattern.search(remainder) for pattern in _INVERSION_FORMS)

# A clause that opens with one of these continues the cue FORWARD ("restart,
# then …"), so a subordinator later in it is not a deferral of the restart.
_FORWARD_SEQUENCER_RE = re.compile(r"^(?:then|and|next|afterwards?)\b", re.IGNORECASE)

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
    r"(?<![A-Za-z0-9])[*_`>\[\]{}|]|[*_`>\[\]{}|](?![A-Za-z0-9])")


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
    after = flat[_cue_start(flat) + len(_RESTART_CUE):]
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


def _paragraph_cue_order_violation(para: str) -> str | None:
    flat = " ".join(para.split())
    cue_at = _cue_start(flat)
    before, after = flat[:cue_at], flat[cue_at + len(_RESTART_CUE):]
    # ⛔ STATEMENT BREAKS FIRST, then clauses — the same reason the post-cue side
    # does it. A bare `[,.;]` cut inside `v1.2`/`e.g.` and ignored the em dash, so
    # a pre-cue form (f) inversion went GREEN ("Confirm the tools appear after the
    # v1.2 upgrade. Restart your client …", "Delete the old entry — but first
    # confirm the tools are actually there") while the comma twin was RED.
    pre_statement = _STATEMENT_BREAK_RE.split(
        before.lstrip(" \t,.;:—–-"))[-1]
    # ⛔ AND EACH CLAUSE, not just the statement: the LAST clause is what form (f)
    # anchors on, so a decoration sitting after a comma (`Delete the old entry,
    # **confirm the tools are actually there**. Restart …`) hides the imperative
    # exactly as a leading one does (measured).
    pre_clauses = [c.strip().lstrip(_PRE_CUE_PREFIX).strip()
                   for c in re.split(r"[,;]", pre_statement)
                   if c.strip()][-1:]
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
    post_clauses = [c.strip() for c in post_sentence.split(",")]
    # ⛔ AN `and`-LED CLAUSE CONTINUES THE PREVIOUS ONE. Splitting at the comma
    # made the second half its own clause, which the forward-sequencer skip then
    # dropped whole — so "after confirming the config is right, AND the tools are
    # actually there" passed, while the no-comma twin is RED (measured). Merging
    # first lets the config-conjunction rule see both halves.
    merged: list[str] = []
    for clause in post_clauses:
        if merged and re.match(r"(?i)^and\b",
                               clause.lstrip(_PRE_CUE_PREFIX)):
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
                or _negated_observation_only(clause)):
            continue
        # ⛔ A PRE-CUE IMPERATIVE THAT DEFERS ITS OWN CHECK PAST THE RESTART IS
        # FORWARD: "Check the tools after restarting. Restart your client, then
        # confirm the tools appear." is self-consistent copy and was RED
        # (measured) — form (f) cannot tell it from "Confirm the tools are
        # actually there. Restart your client …".
        if re.search(r"\bafter\s+(?:the\s+)?restart(?:ing|s)?\b", clause,
                     re.IGNORECASE):
            continue
        for pattern in _INVERSION_FORMS:
            if pattern.search(clause):
                return clause
    for clause in post_clauses:
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
            if pattern is _PRE_CUE_ONLY:
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
    # the current table it loses FOUR declared-RED rows — all of them
    # merged-introducer shapes added by cycles 18-21 (`… and then, but first
    # confirm …` and its bold/paren twins, plus the config-conjunction row). They
    # live in a LATER comma-delimited fragment, so the split is what puts them in
    # front of the forms. The split is LOAD-BEARING; do not delete it.
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
    # - It is NOT an open-ended inversion detector. "… when the tools appear";
    #   "after making sure the tools …"; and any other rephrasing outside those
    #   SEVEN (a, b, c, d, d′, e, and the pre-cue-only f) are deliberately NOT
    #   graded — widening a prose denylist is how both defects this pin fixes got
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
    # …and the same doc with the cue lower-cased is graded, not skipped:
    doc_lower = f"Lead-in.\n\n{inverted[0].lower() + inverted[1:]}\n"
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
    # ── inversions: confirm FIRST, restart second ───────────────────────────
    ("Restart your client after confirming the tools are actually there.", True),
    ("Restart your client, but first confirm the tools are actually there.", True),
    ("Restart your client, having first confirmed the tools are actually "
     "there.", True),
    ("Restart your client, though you should confirm the tools are actually "
     "there beforehand.", True),
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
    # ── a NEGATED object is a SYMPTOM condition, the same class as the declared
    # GREEN `if you cannot see the tools in the list` — `no`/`none`/`zero` were
    # RED because negation by anything but `cannot` fell through the predicate:
    ("Restart your client if you see no `tortoise_*` tools.", False),
    ("Restart your client if you see zero tools in the list.", False),
    ("Restart your client after you have seen no tools in the list.", False),
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
    ("Restart your client, but confirm the tools are already there.", True),
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
    # ⛔ `when` used to be listed here and is NOT row-less: replacing form (a)'s
    # `(?:if|when)` with `(?:if)` flips FOUR declared rows (measured; the fourth
    # is the degree-intensifier row added later), so it is pinned and the claim
    # that it carries no row was wrong in the safe direction but still wrong.
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
])
def test_3232_restart_order_pin_grades_the_forms_it_claims(line, inverted):
    doc = f"Some lead-in copy.\n\n{line}\n\nRate limit: **100 requests/min per key**.\n"
    violation = _restart_cue_order_violation(doc)
    if inverted:
        assert violation is not None, (
            f"this inversion must be graded and was not: {line!r}")
    else:
        assert violation is None, (
            f"false red on forward copy ({violation!r}): {line!r}")


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
