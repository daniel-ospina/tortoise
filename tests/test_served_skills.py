"""#1652 — the priority-3 served skills: `research-domain` +
`analyse-for-contradictions`.

Two halves, because the issue's indicators are two different kinds of claim.

SURFACE (no DB). The two skills are served from the dashboard's public skill
tree — the tree the onboarding wizard's installer downloads from
(``https://app.premiselabs.co/skills/<name>/SKILL.md``) — their frontmatter is
lint-clean, and every ``tortoise_*`` name they tell an agent to call is a tool
the server actually advertises. That last assertion is the durable guard for the
defect class #1660's review found by hand ("tortoise-decide — use the real MCP
tool names"): a served skill that names a tool the client was never granted
cannot be executed, and nothing failed until a human read it.

WORKFLOW SMOKE (embedded). The graph reads the skills prescribe answer on a real
graph: UC2 gaps returns the load-bearing-but-unsupported claim the research loop
must research next, the contradictory-pair flag and the superseded-edge audit
fire for the contradiction analysis, and the structure check the skills gate on
is green. The skills are only executable if these reads are.
"""
from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: I001

from tortoise.sdk import TortoiseSDK
from tortoise.tool_registry import RETIRED_TOOL_REGISTRY, TOOL_REGISTRY
from tests._embedded import skip_if_no_falkor

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVED = os.path.join(REPO_ROOT, "website", "apps", "dashboard", "public", "skills")
INSTALLER = os.path.join(
    REPO_ROOT, "website", "apps", "dashboard", "public",
    "install-tortoise-skills.sh")
DECIDE = os.path.join(SERVED, "tortoise-decide", "SKILL.md")
WIZARD_DOC = os.path.join(REPO_ROOT, "docs", "onboarding.md")

# #1652: the two skills this issue ships.
NEW_SKILLS = ("research-domain", "analyse-for-contradictions")

# #4365: the installer's SHIPPED core set is exactly three reusable
# capabilities. #1652's own recommendation was to keep it there and treat the
# two new skills as the optional next set (non-installed), so this is a pin,
# not an accident: a fourth basename here is a shipped-surface change.
SHIPPED_CORE = ("how-to-use-tortoise", "tortoise-decide", "tortoise-file-finding")

# Pre-existing served-skill names that are NOT advertised tools, pinned EXACTLY
# so the set can only shrink. A fix REDs this test (shrink the map — that is the
# ratchet working); a new stale name REDs it too. `tortoise_health` is tracked
# by #5439; the other four are the #1652 follow-up filing (agent-infra is the
# canonical source for these three skills, so the fix lands there first).
KNOWN_STALE_TOOL_NAMES = {
    "how-to-use-tortoise": {
        "tortoise_fts_query",      # an SDK method name, never a tool
        "tortoise_list_pointkinds",  # retired #3883 → tortoise_overview
        "tortoise_list_sources",     # retired #3883 → tortoise_overview
        "tortoise_status",           # retired #3883 → tortoise_overview
    },
    "tortoise-file-finding": {"tortoise_list_sources"},
    "tortoise-onboarding": {"tortoise_health"},
}

# A `tortoise_` token followed by at least one name character. The bare
# `tortoise_*` wildcard used in prose does not match (the `*` is not a name
# character), which is what keeps the sweep from flagging the wildcard itself.
_TOOL_TOKEN = re.compile(r"\btortoise_[a-z_]+")


def _served_skill_dirs() -> list[str]:
    return sorted(
        e for e in os.listdir(SERVED)
        if os.path.isfile(os.path.join(SERVED, e, "SKILL.md")))


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _frontmatter(text: str) -> tuple[str, dict[str, str]]:
    """(raw yaml, top-level scalar keys) — enough for the lint's P0 rules.

    Deliberately not a YAML parse: the agent-infra skill lint's P0 set is
    structural (bounds, required keys, name==directory, duplicate keys), and a
    test that re-implemented a YAML parser would be a second thing to keep true.
    """
    assert text.startswith("---\n"), "frontmatter must open the file"
    end = text.index("\n---", 3)
    raw = text[4:end + 1]
    keys: dict[str, str] = {}
    for line in raw.splitlines():
        m = re.match(r"^([A-Za-z0-9_.-]+):\s*(.*)$", line)
        if m:
            keys[m.group(1)] = m.group(2).strip()
    return raw, keys


def _installer_skills() -> list[str]:
    m = re.search(r"^SKILLS=\(([^)]*)\)", _read(INSTALLER), re.M)
    assert m, "installer SKILLS=(...) array not found"
    return m.group(1).split()


# ── Surface ────────────────────────────────────────────────────────────────

def test_1652_the_two_skills_are_served():
    """Indicator 1 — both skills exist as first-party SKILL.md documents on the
    served surface (the tree the installer downloads from)."""
    served = _served_skill_dirs()
    for name in NEW_SKILLS:
        assert name in served, (
            f"{name} is not a served skill — the installer fetches "
            f"{SERVED}/<name>/SKILL.md")


def test_1652_served_frontmatter_is_lint_clean():
    """Indicator 1 — 0 skill-lint failures. Mirrors the agent-infra skill
    lint's P0 structural rules for the two files this issue adds: frontmatter
    bounds, `name` == directory, non-empty `description`, no duplicate key."""
    for name in NEW_SKILLS:
        text = _read(os.path.join(SERVED, name, "SKILL.md"))
        raw, keys = _frontmatter(text)
        assert "name" in keys and keys["name"] == name, (
            f"{name}: frontmatter name must equal the directory name")
        assert keys.get("description", "").strip(" \"'"), (
            f"{name}: description must be non-empty")
        seen = set()
        for line in raw.splitlines():
            m = re.match(r"^([A-Za-z0-9_.-]+):", line)
            if m:
                assert m.group(1) not in seen, (
                    f"{name}: duplicate frontmatter key {m.group(1)!r}")
                seen.add(m.group(1))


def test_1652_served_skills_name_only_advertised_tools():
    """The durable half of indicator 1: every `tortoise_*` tool a served skill
    tells an agent to call must be in the ADVERTISED surface. A client invokes
    only what its granted tool list contains, so a retired (or invented) name is
    an instruction that cannot be executed — the class found by hand in #1660.

    The known-stale map is an exact ratchet (see KNOWN_STALE_TOOL_NAMES), not an
    allowlist that can quietly grow."""
    live = {d.name for d in TOOL_REGISTRY}
    retired = {d.name for d in RETIRED_TOOL_REGISTRY}
    stale: dict[str, set[str]] = {}
    for name in _served_skill_dirs():
        text = _read(os.path.join(SERVED, name, "SKILL.md"))
        found = {t for t in _TOOL_TOKEN.findall(text) if t not in live}
        if found:
            stale[name] = found
    assert stale == KNOWN_STALE_TOOL_NAMES, (
        "the served skills' non-advertised tool names changed.\n"
        f"  found: {stale}\n"
        f"  pinned: {KNOWN_STALE_TOOL_NAMES}\n"
        f"  advertised: {len(live)} tools, retired: {sorted(retired)}\n"
        "A fix (or an added stale name) must update KNOWN_STALE_TOOL_NAMES — "
        "the map is a shrinking ratchet, never a growing allowlist.")


def test_1652_tortoise_decide_names_the_research_domain_loop():
    """Indicator 2 — tortoise-decide explicitly names the graph UC2 weak-spot
    loop that `research-domain` implements, and names the skill."""
    decide = _read(DECIDE)
    assert "research-domain" in decide, (
        "tortoise-decide must reference the research-domain skill")
    assert 'mode="gaps"' in decide, (
        "tortoise-decide must name the UC2 gaps query that drives the loop")
    assert "load / (1 + support)" in decide, (
        "tortoise-decide must state the UC2 load/support score it steers on")
    assert "under-supported" in decide, (
        "tortoise-decide must describe what UC2 returns")

    # …and the skill it names exists, so the reference is not a dangling one.
    research = _read(os.path.join(SERVED, "research-domain", "SKILL.md"))
    assert 'mode="gaps"' in research and "tortoise_recall" in research, (
        "research-domain must itself document the UC2 gaps recall")


def test_1652_installer_core_set_unchanged_and_new_skills_are_documented():
    """Indicator 3 — the two skills are NOT promoted into the installer's core
    set (#4365 pins it at three), and the optional set is documented instead."""
    assert _installer_skills() == list(SHIPPED_CORE), (
        "#1652 keeps the installer's shipped set at three; a promotion is a "
        "#4365 surface decision, not a side effect of this issue")
    for name in NEW_SKILLS:
        assert name not in _installer_skills()
        assert name in _read(WIZARD_DOC), (
            f"{name} is optional (not installed) — it must be documented as "
            "the next available set in docs/onboarding.md")


# ── #8066: the served-skill relay ratchet lives in its own static file ─────
#
# The ratchet's SUBJECT is every `tests/**/*.py`, so it cannot live here: this
# file is registered under `onboarding` alone, and a `core`-owned test file's
# diff (the sharp case being `tests/test_ingest_conformance.py`, the very file
# #8499 retargeted off the relay) does not select `onboarding`. It moved to
# `tests/test_8066_served_skill_relay_ratchet.py`, registered on `core` AND
# `onboarding`, and that file pins the registration property by enumerating
# every in-scope file (the #1349/#2938 silent-drop class).


# ── #3513: the renumbered SKILL sections must stay findable BY NUMBER ──────
# The onboarding SKILL.md's sections are cited by NUMBER from outside the file
# (mcp_server.py, hosted_api.py, tests, and a live research routing pointer).
# #3513 inserted a new §6 and renumbered the rest, sweeping only the
# in-document self-references — every external pointer then aimed at the wrong
# section and nothing failed, because no test pinned a section NUMBER. These
# derive the number from the heading and assert the referrers cite it, so the
# next insertion cannot re-stale them silently.
_ONBOARDING_SKILL = os.path.join(SERVED, "tortoise-onboarding", "SKILL.md")


def _skill_section_number(title: str) -> str:
    text = _read(_ONBOARDING_SKILL)
    m = re.search(rf"^## (\d+)\.\s+{re.escape(title)}", text, re.M)
    assert m, f"the served SKILL.md has no `## <n>. {title}` section"
    return m.group(1)


def test_3513_the_renumbered_skill_sections_are_cited_by_their_real_numbers():
    decide_n = _skill_section_number("Next steps")
    contract_n = _skill_section_number("Capture-announcement COPY CONTRACT")
    assert decide_n != contract_n, "the two cited sections must be distinct"
    mcp = _read(os.path.join(REPO_ROOT, "tortoise", "mcp_server.py"))
    api = _read(os.path.join(REPO_ROOT, "tortoise", "hosted_api.py"))
    assert f"`tortoise/onboarding/SKILL.md` §{decide_n}" in mcp, (
        "mcp_server.py cites the decide protocol's home by a stale section "
        "number — renumber it with the section (#3513)")
    assert f"tortoise/onboarding/SKILL.md §{contract_n}" in api, (
        "hosted_api.py cites the capture-announcement copy contract by a stale "
        "section number — renumber it with the section (#3513)")
    for rel, token in (
        ("tests/test_onboarding_auto_complete.py", f"SKILL.md` §{decide_n}"),
        ("tests/test_capture_disclosure_template.py", f"SKILL.md §{contract_n}"),
        ("docs/research/1714-solution-converge.md", f"§{contract_n} (the announcement)"),
    ):
        assert token in _read(os.path.join(REPO_ROOT, rel)), (
            f"{rel} cites a SKILL.md section by a stale number (#3513)")


# ── Workflow smoke (embedded) ──────────────────────────────────────────────

@pytest.fixture
def sdk(tmp_path, force_sparse_tfidf):
    """Embedded SDK with the embedder pinned OFF.

    These tests assert graph STRUCTURE (load/support counts, edge flags,
    audit checks) and never a vector ranking, and `recall_gaps(kind=...)` is a
    population scan, so the vector leg contributes nothing. Pinning it off is
    what makes the file both deterministic — CI runs with the bge cache present
    or absent — and quick: an unpinned cold `EmbeddingModel.get()` blocks on the
    HF egress the hermetic guard blocks, retrying for ~2 minutes per run.
    """
    if skip_if_no_falkor():
        pytest.skip("embedded FalkorDBLite unavailable")
    return TortoiseSDK(db_path=str(tmp_path / "t.db"))


def _link_source(sdk: TortoiseSDK, pid: str, url: str) -> None:
    sdk.create_source(url, "T1")
    sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}), (s:Source {url:$u}) "
        "CREATE (n)-[:extractedFrom]->(s)",
        params={"id": pid, "u": url})


def test_1652_research_domain_uc2_step_answers(sdk):
    """`research-domain` step 4 tells the agent to ask the graph where the
    reasoning is load-bearing but under-supported. That question must answer:
    the unsupported claim the next research round should target is returned,
    the well-supported claim is not — otherwise the loop has no control signal
    and silently degrades into ordinary research."""
    ev = [sdk.create_point("evidence", f"battery survey {i}") for i in range(3)]
    gap = sdk.create_point("statement", "solid-state cycle life holds at 2000 cycles")
    target = sdk.create_point("statement", "solid-state cells ship in 2027 vehicles")
    well = sdk.create_point("statement", "solid-state density exceeds 400 Wh/kg")
    for e in ev:
        sdk.create_operator("IMPL", e["id"], [well["id"]])
    sdk.create_operator("IMPL", gap["id"], [target["id"]])
    _link_source(sdk, well["id"], "https://example.com/solid-state")

    ids = {r["id"] for r in sdk.recall_gaps(kind="statement")}
    assert gap["id"] in ids, "the load-bearing unsupported claim is the gap"
    assert well["id"] not in ids, "a well-supported claim is not a gap"
    # The breakdown the skill tells the agent to read.
    rr = next(r for r in sdk.recall_gaps(kind="statement")
              if r["id"] == gap["id"])["gaps_ranking"]
    assert rr["load"] == 1 and rr["support"] == 0
    # G4 — the skills gate on a structurally sound graph.
    assert sdk.check_structure() == []


def test_1652_contradiction_reads_answer(sdk):
    """`analyse-for-contradictions` step 3 claims three detections. Each must
    answer on a real graph: a contradictory pair (both IMPL and NAND), a live
    edge into a superseded point, and the structure gate the skill requires."""
    left = sdk.create_point("statement", "we keep the shared connection pool")
    right = sdk.create_point("statement", "each tenant gets a dedicated pool")
    # Edge-level: the same pair is BOTH implied and mutually excluded.
    sdk.create_operator("IMPL", left["id"], [right["id"]])
    sdk.create_operator("NAND", left["id"], [right["id"]])
    flags = sdk.review_connections(mode="prune")
    contradictory = [f for f in flags["prune"] if f["issue"] == "contradictory"]
    assert contradictory, (
        "review_connections(mode='prune') must flag the contradictory pair — "
        "it is the edge-level detection the skill prescribes")
    assert {contradictory[0]["from"], contradictory[0]["to"]} == {
        left["id"], right["id"]}

    # Lifecycle-level: a live edge into a superseded point (audit's own check).
    old = sdk.create_point("statement", "single-writer ingest queue")
    new = sdk.create_point("statement", "single-flight ingest queue")
    sdk.supersede_point(old["id"], new["id"])
    live = sdk.create_point("statement", "the ingest path is single-writer")
    sdk.create_operator("IMPL", live["id"], [old["id"]])
    report = sdk.audit()
    check = next((c for c in report["checks"]
                  if c["id"] == "superseded_active_edges"), None)
    assert check is not None and check["count"] >= 1, (
        "audit() must flag the live edge into the superseded point")

    # Both skills gate on this; the skills must not be built on a broken graph.
    assert sdk.check_structure() == []
