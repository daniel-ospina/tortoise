"""A12 (epic #902, issue #1050) — docs/behavior conformance test.

Closes the cycle-22 doc/behavior drift disease class: INGEST_CONTRACT.md's
response examples must stay key-set-equal to the REAL sdk.ingest /
tortoise_ingest responses, the tool description must name the gated
default, and the skill (J8) must carry the operational markers.

Canonical key enumeration (plan §5.5/E2E-6.2, INGEST_CONTRACT §2.2/§3.2):
  top-level  = {granularity, batch_id, created, deduped, ids, nudges,
                warnings}  (+ results for granularity="granular")
  created/deduped = {points, entities, sources, connections}
  ids        = {points, entities, sources, connections, refs}
  failure    = {error, code, violations}  (never a results key)
  warnings   = the TWELVE-key closed set (§3.2 table)

Skill markers (J8 — the how-to-use-tortoise skill section is #1057's
deliverable; the skill file lives in the agent-infra repo and is mirrored
into this repo's skills/ only on dev machines — the marker-grep SKIPS when
the file/markers are absent so CI stays green until #1057 lands).
"""
from __future__ import annotations

import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from tortoise.sdk import TortoiseSDK

REPO_ROOT = Path(__file__).resolve().parents[1]

# ── canonical enumeration anchors (INGEST_CONTRACT §2.2/§2.3/§3.2) ────
TOP_LEVEL = {"granularity", "batch_id", "created", "deduped", "ids",
             "nudges", "warnings"}
SECTION_COUNTS = {"points", "entities", "sources", "connections"}
IDS_KEYS = {"points", "entities", "sources", "connections", "refs"}
WARNING_KEYS = {
    "append_only_items", "modified_item_residue", "mitigation_orphan_residue",
    "mitigation_drift_duplicate", "nfc_straddle_duplicate",
    "mitigation_strength_change", "partial_operator_residue",
    "operator_absorb_completed", "label_dropped_resubmit",
    "direction_dropped_resubmit", "direction_changed_resubmit",
    # #8499: the twelfth key — a plain IMPL/NAND connection between :Point
    # endpoints is reified by the endpoint-kind gate (owner ruling
    # 2026-10-13). Added to the CLOSED set deliberately, with the §3.2 table
    # row and its A12 note amended in the same change — extending this set is
    # a contract amendment, never a drive-by emission.
    "point_mediated_route",
}


@pytest.fixture
def sdk():
    db_path = os.path.join(tempfile.mkdtemp(prefix="tortoise_conf_"), "test.db")
    sdk = TortoiseSDK(db_path)
    yield sdk
    sdk.close()
    shutil.rmtree(os.path.dirname(db_path), ignore_errors=True)


def _bundle():
    return {
        "points": [
            {"ref": "p1", "kind": "claim", "content": "A implies B."},
            {"ref": "p2", "kind": "claim", "content": "B."},
        ],
        "entities": [
            {"ref": "s1", "type": "subject", "name": "Ferra Labs",
             "subjectKind": "organization"},
        ],
        "sources": [
            {"ref": "src1", "url": "https://example.com/rust-report",
             "sourceKind": "report", "tier": "T1"},
        ],
        "connections": [
            {"ref": "c1", "from": "p1", "to": "p2", "operator": "IMPL"},
            {"ref": "c2", "from": "s1", "to": "p1", "relation": "authoredBy"},
            {"ref": "c3", "from": "p1", "to": "src1",
             "relation": "extractedFrom"},
        ],
    }


# ── indicator 1: doc example key-set-equal to real responses ─────────

def test_sdk_success_response_key_set_equal(sdk):
    """A12 indicator 1: the SDK success response's key set == the canonical
    enumeration (top-level + created/deduped + ids); granular adds results."""
    for granularity in ("bulk", "granular"):
        res = sdk.ingest(_bundle(), granularity=granularity)
        assert set(res.keys()) == TOP_LEVEL | (
            {"results"} if granularity == "granular" else set()), \
            f"{granularity}: {sorted(res.keys())}"
        assert set(res["created"].keys()) == SECTION_COUNTS
        assert set(res["deduped"].keys()) == SECTION_COUNTS
        assert set(res["ids"].keys()) == IDS_KEYS
        assert res["granularity"] == granularity
        assert len(res["batch_id"]) == 26
        assert isinstance(res["warnings"], list)
        # E2E-6.2 closed-set enforcement: every emitted warning's key-prefix
        # is in the TWELVE-key enumeration (a key outside it is a divergence)
        for w in res["warnings"]:
            assert isinstance(w, str), f"warning entry must be a key-prefixed string: {w!r}"
            assert w.split(":", 1)[0] in WARNING_KEYS, \
                f"warning key outside the closed enumeration: {w!r}"


def test_mcp_success_response_key_set_equal(sdk, monkeypatch):
    """A12 indicator 1 (MCP surface): tortoise_ingest returns the SAME key
    set through the handler layer."""
    import tortoise.mcp_server as mcp_mod  # noqa: I001
    from tortoise.mcp_auth import (_current_org_id, _current_org_limits,
                                   _transport_mode)
    _transport_mode.set("stdio")
    _current_org_id.set(None)
    _current_org_limits.set(None)
    orig = mcp_mod._get_org_sdk
    mcp_mod._get_org_sdk = lambda: sdk
    try:
        res = mcp_mod.tortoise_ingest(bundle=_bundle())
        assert set(res.keys()) == TOP_LEVEL, sorted(res.keys())
        assert set(res["created"].keys()) == SECTION_COUNTS
        assert set(res["deduped"].keys()) == SECTION_COUNTS
        assert set(res["ids"].keys()) == IDS_KEYS
    finally:
        _transport_mode.set(None)
        _current_org_id.set(None)
        _current_org_limits.set(None)
        mcp_mod._get_org_sdk = orig


def test_failure_response_shape_no_results_key(sdk, monkeypatch):
    """A12 indicator 1 failure half: an invalid bundle returns the
    {error, code, violations} shape — NEVER a results key (the bulk failure
    shape, §2.5/§5.2)."""
    from tortoise.exceptions import BundleValidationError
    bad = {
        "points": [
            {"ref": "p1", "kind": "claim", "content": "A."},
            {"ref": "p2", "kind": "claim", "content": "B."},
        ],
        "connections": [
            {"from": "p1", "to": "01GHOST00000000000000000000",
             "operator": "IMPL"},
        ],
    }
    with pytest.raises(BundleValidationError):
        sdk.ingest(bad, granularity="granular")
    # MCP surface: structured {error, code: ERR_BUNDLE_INVALID, violations}
    import tortoise.mcp_server as mcp_mod  # noqa: I001
    from tortoise.mcp_auth import (_current_org_id, _current_org_limits,
                                   _transport_mode)
    _transport_mode.set("stdio")
    _current_org_id.set(None)
    _current_org_limits.set(None)
    orig = mcp_mod._get_org_sdk
    mcp_mod._get_org_sdk = lambda: sdk
    try:
        res = mcp_mod.tortoise_ingest(bundle=bad, granularity="granular")
        assert "error" in res, "failure response must carry the error key"
        assert res["code"] == mcp_mod.ERR_BUNDLE_INVALID
        assert "violations" in res and res["violations"]  # noqa: RUF019
        assert "results" not in res, "failure shape must have no results key"
    finally:
        _transport_mode.set(None)
        _current_org_id.set(None)
        _current_org_limits.set(None)
        mcp_mod._get_org_sdk = orig


# ── indicator 3: tool description markers ────────────────────────────

def test_tool_description_names_promotion_policy_and_gated_default():
    """A12 indicator 3: the tortoise_ingest tool description names
    promotion_policy + the gated default (the #131-behavior-change
    announcement surface)."""
    from tortoise.tool_registry import TOOL_REGISTRY
    entry = next((t for t in TOOL_REGISTRY
                  if t.name == "tortoise_ingest"), None)
    assert entry is not None, "tortoise_ingest missing from TOOL_REGISTRY"
    desc = entry.description
    assert "promotion_policy" in desc, "tool must name promotion_policy"
    assert "gated" in desc, "tool must name the gated default"


# ── doc conformance: the contract doc carries the canonical anchors ───

def test_doc_carries_canonical_enumeration_anchors():
    """A12 doc-side: INGEST_CONTRACT.md's §2.2 example JSON block carries
    every canonical top-level key and the §3.2 warnings table carries all
    TWELVE keys (the closed-set anchor)."""
    doc = (REPO_ROOT / "docs" / "INGEST_CONTRACT.md").read_text(
        encoding="utf-8")
    for key in TOP_LEVEL:
        assert f'"{key}"' in doc, f"§2.2 example missing key {key!r}"
    for key in SECTION_COUNTS:
        assert f'"{key}"' in doc, f"created/deduped example missing {key!r}"
    for key in IDS_KEYS:
        assert f'"{key}"' in doc, f"ids example missing {key!r}"
    for key in sorted(WARNING_KEYS):
        assert f"`{key}`" in doc or key in doc, \
            f"§3.2 warnings table missing key {key!r}"
    # failure-half conformance: the doc carries the {error, code, violations}
    # failure example (plan A12(a) requires success + failure examples)
    assert "{error, code" in doc, "§5 failure example missing {error, code..."
    assert "violations" in doc, "§5 failure example missing violations"
    assert "ERR_BUNDLE_INVALID" in doc, "§5 failure example missing the code"


# ── indicator 2: skill markers (J8 — skip until the section lands) ───

# Read the SERVED mirror — the bytes users actually download
# (`/skills/<name>/SKILL.md`). agent-infra stays canonical for AUTHORING; this
# is about which copy the marker CONTRACT is enforced on. The tracked `skills/`
# symlink hard-codes one machine's path, so it dangles everywhere else: the
# marker contract was SKIPPED in CI — the guard was green exactly where it
# could not see the file — while on a dev machine it silently graded whatever
# an unrelated checkout happened to contain. The dead tracked symlink is
# #8066/#8027; the missing three-copy parity gate is #3673/#4643/#4914.
# Reading the in-repo served copy is deterministic and is the pattern
# `test_served_skills.py` already uses.
SERVED_SKILL = (
    REPO_ROOT / "website" / "apps" / "dashboard" / "public"
    / "skills" / "how-to-use-tortoise" / "SKILL.md"
)


def _skill_text() -> str | None:
    """The how-to-use-tortoise skill as THIS repo serves it (absent only if
    the served tree is missing — the marker-grep skips then)."""
    return (SERVED_SKILL.read_text(encoding="utf-8")
            if SERVED_SKILL.exists() else None)


def test_skill_markers_contract():
    """A12 indicator 2 (J8): when the how-to-use-tortoise skill section
    exists (the served mirror), it must carry the operational markers —
    bundle shape, granularity, the gated default, the interim promotion
    entry, idempotent resubmission, the retry/decision tables, and all
    TWELVE warning keys. SKIPPED until #1057 (J8) lands the section — the
    marker-grep is the enforcement the moment it does."""
    text = _skill_text()
    if text is None or "promotion_policy" not in text:
        pytest.skip("J8 skill section (#1057) not yet landed — markers "
                    "absent/untracked")
    assert "bundle" in text, "bundle-shape marker"
    assert "granularity" in text
    assert "gated" in text
    assert "promotion_policy" in text
    assert "tortoise_update_point" in text, "interim promotion entry"
    assert "resubmit" in text.lower() or "idempotent" in text.lower()
    assert "retry" in text.lower()
    assert "decision" in text.lower(), "decision-table marker"
    assert "migration" in text.lower(), "§6.6 migration-line marker"
    for key in sorted(WARNING_KEYS):
        assert key in text, f"skill warnings section missing {key!r}"


# ── the OTHER direction: mirror → product (#10438) ────────────────────
# `test_skill_markers_contract` above asks “does the mirror carry every key the
# product owns”, which is containment only — a mirror that advertises a key the
# product does NOT emit passes it in silence. That gap is what let the
# 2026-10-30 upstream sync (#7, upstream 5c8d99c) serve a THIRTEEN-key table
# naming `duplicate_target_deduped`, a key emitted by nothing at that head (the
# emitting fix, #10305, was still open and #9750 leaves the mechanism undecided).
# The served file is what integrators read, so an advertised-but-absent key is a
# behavior claim, not a typo: it says “the engine deduplicates a repeated target
# for you” in the very case where the engine double-counts EP.
#
# Both arms are needed because the mirror is DERIVED (upstream authors it, this
# repo pins the bytes — `config/served-skills-upstream.yml`), so neither side can
# be trusted to describe the other.

# A tuple literal, not `" … ".split()`: SIM905 is in the lint select (`SIM`), and
# `main-lint.yml` runs `ruff check .` on every merged tree with no `paths:`
# filter, so the split form reddens the first post-merge run — the same "the
# remedy returns a red tree" defect this PR exists to close.
_NUMBER_WORDS = {word: value for value, word in enumerate((
    "ZERO", "ONE", "TWO", "THREE", "FOUR", "FIVE", "SIX", "SEVEN", "EIGHT",
    "NINE", "TEN", "ELEVEN", "TWELVE", "THIRTEEN", "FOURTEEN", "FIFTEEN",
    "SIXTEEN", "SEVENTEEN", "EIGHTEEN", "NINETEEN", "TWENTY"))}
_WARNINGS_SECTION = re.compile(r"^## Warnings\b.*?(?=^## |\Z)", re.M | re.S)
_DECLARED_COUNT = re.compile(r"\bthe ([A-Z]+)(?:-key table| keys)\b")
_KEY_TOKEN = re.compile(r"`([a-z][a-z0-9]*(?:_[a-z0-9]+)+)`")


def _declared_key_count(section: str) -> list[int]:
    """Every cardinality the mirror states for the key set, as integers."""
    return [_NUMBER_WORDS[word] for word in _DECLARED_COUNT.findall(section)]


def _listed_keys(section: str) -> list[str]:
    """The keys the mirror enumerates, in the list that follows “report it”.

    Anchored on the prose line that introduces the list and then on the first
    blank line after it, so it reads the ENUMERATION and not the whole section
    (a section may legitimately *mention* a reserved key in prose without
    claiming it is in the set)."""
    lines = section.splitlines()
    start = next((i + 1 for i, line in enumerate(lines)
                  if "divergence" in line), None)
    assert start is not None, (
        "could not find the mirror's key-list anchor “divergence” in the "
        "warnings section — re-anchor this gate rather than dropping it")
    block: list[str] = []
    for line in lines[start:]:
        if not line.strip():
            if block:
                break
            continue
        block.append(line)
    return _KEY_TOKEN.findall("\n".join(block))


def _warning_table_problems(section: str) -> list[str]:
    """Every way ``section`` fails to describe the product's closed warning set.

    PURE on purpose: the gate below runs it against the ONE real mirror, which
    cannot show that it would have RED for a mirror that is wrong — the three
    `test_10437_warning_table_gate_…` tests run the same function against
    synthetic sections, so each arm has a case that proves it fires.

    Three arms, because each fails where the others cannot:
      * no key may be advertised that the product does not emit (the 2026-10-30
        regression, #10438);
      * the enumeration must be COMPLETE — the subset arm alone passes a
        truncated list (eleven of twelve, heading still `TWELVE`), which is a
        reader believing a key does not exist when the engine emits it;
      * every cardinality the mirror declares (“the TWELVE keys”, the
        “TWELVE-key table”) must EQUAL `len(WARNING_KEYS)` — so the count and
        the list cannot drift apart, and extending the set still costs the
        contract amendment #8499 requires (`docs/INGEST_CONTRACT.md` §3.2)
        instead of being a docs-only edit nobody notices.
    """
    problems: list[str] = []
    listed = _listed_keys(section)

    advertised = sorted({key for key in listed if key not in WARNING_KEYS})
    if advertised:
        problems.append(
            f"the served skill advertises warning keys the product does not "
            f"emit: {advertised}. The closed set is §3.2's "
            f"{len(WARNING_KEYS)} keys (#8499); a key becomes servable only in "
            f"the same change that emits it. If it is merely PLANNED, keep it "
            f"out of the enumeration and say in prose that it is reserved/not "
            f"emitted yet — do not count it (see specs upstream, not this "
            f"mirror, which is derived: "
            f"`tools/served_skills_parity.py --sync`).")

    missing = sorted(WARNING_KEYS - set(listed))
    if missing:
        problems.append(
            f"the served skill's enumeration is INCOMPLETE: it omits "
            f"{missing}, which the product emits (§3.2's closed set, #8499). "
            f"A subset check cannot see this — an integrator reading the served "
            f"copy would treat an emitted key as a divergence "
            f"(`docs/INGEST_CONTRACT.md` §3.2 is the authoritative list; this "
            f"mirror is derived by `tools/served_skills_parity.py --sync` from "
            f"specs authored upstream).")

    declared = _declared_key_count(section)
    if not declared:
        problems.append(
            "the served skill no longer states how many keys the closed set "
            "has — re-anchor this gate: the declaration is what a reader uses "
            "to tell a complete enumeration from a truncated one")
    elif set(declared) != {len(WARNING_KEYS)}:
        problems.append(
            f"the served skill states the warnings set has "
            f"{sorted(set(declared))} keys but the product's closed set has "
            f"{len(WARNING_KEYS)} (docs/INGEST_CONTRACT.md §3.2, #8499). "
            f"Either the enumeration above is missing a key the product emits, "
            f"or the mirror is ahead of it — and an integrator reading the "
            f"served copy has no way to tell which.")

    return problems


def test_served_skill_advertises_only_keys_the_product_emits():
    """mirror → product (#10438): the served skill's warnings section must
    enumerate EXACTLY the product's closed set, and must state that set's size
    correctly. The three arms live in `_warning_table_problems` (see its
    docstring), which runs here against the real mirror and in the
    `test_10437_warning_table_gate_…` cases against synthetic sections.
    """
    text = _skill_text()
    if text is None or "promotion_policy" not in text:
        pytest.skip("J8 skill section (#1057) not yet landed — markers "
                    "absent/untracked")
    match = _WARNINGS_SECTION.search(text)
    assert match is not None, "the served skill has no “## Warnings” section"
    problems = _warning_table_problems(match.group(0))
    assert not problems, "\n".join(problems)


# ── non-vacuity: each arm reds on the mirror shape it was written for ──
# Built from a template rather than from the served file so these run (and
# RED) even where the mirror is absent or has already been fixed — the point
# is the GATE, not the artifact of the day.
def _synthetic_warnings_section(heading_word: str, keys: list[str]) -> str:
    """A warnings section in the mirror's own shape.

    The reserved-key blockquote sits AFTER a blank line, exactly as the real
    mirror has it: `_listed_keys` stops at the first blank line, so a key
    merely *named in prose* is not counted as enumerated.
    """
    listed = " ·\n".join(f"`{key}`" for key in keys)
    return (
        f"## Warnings — the {heading_word} keys (closed set)\n\n"
        "`warnings` entries are key-prefixed strings. A key outside this "
        "table is a\ndivergence — report it:\n\n"
        f"{listed}\n\n"
        "> A thirteenth key (`duplicate_target_deduped`) is **reserved**: it "
        "joins this closed set when that fix lands.\n"
    )


def test_10437_warning_table_gate_reds_on_a_truncated_enumeration():
    """The arm this gate was missing: eleven of twelve keys, heading still
    `TWELVE`. The subset arm is silent (nothing advertised) and the count arm is
    silent (it still says 12), so ONLY the completeness arm catches it.
    """
    keys = sorted(WARNING_KEYS)
    section = _synthetic_warnings_section("TWELVE", keys[:-1])

    # why the older two arms cannot carry this alone:
    assert not [key for key in _listed_keys(section) if key not in WARNING_KEYS]
    assert _declared_key_count(section) == [12]

    problems = _warning_table_problems(section)
    assert problems, "a truncated enumeration passed every arm"
    assert keys[-1] in "\n".join(problems), (
        f"the gate reds but does not name the omitted key {keys[-1]!r}, which "
        f"is what tells the fixer which mirror line to add")


def test_10437_warning_table_gate_reds_on_an_advertised_key():
    """The 2026-10-30 regression itself (#10438): the reserved
    `duplicate_target_deduped` INSIDE the enumeration, at a truthful count."""
    section = _synthetic_warnings_section(
        "THIRTEEN", [*sorted(WARNING_KEYS), "duplicate_target_deduped"])
    problems = _warning_table_problems(section)
    assert problems, "an advertised-but-unemitted key passed every arm"
    assert "duplicate_target_deduped" in "\n".join(problems)


def test_10437_warning_table_gate_reds_on_a_declared_count_mismatch():
    """Twelve keys, heading says THIRTEEN: the count and the list must not be
    able to drift apart in either direction."""
    section = _synthetic_warnings_section("THIRTEEN", sorted(WARNING_KEYS))
    problems = _warning_table_problems(section)
    assert problems, "a miscounted closed set passed every arm"
    assert "13" in "\n".join(problems)


def test_10437_warning_table_gate_is_clean_on_the_mirror_shape():
    """The control: the real mirror's shape (twelve keys, `TWELVE`, reserved
    key only in prose) is CLEAN — so the three cases above are proving the arms
    fire, not that the gate reds unconditionally."""
    section = _synthetic_warnings_section("TWELVE", sorted(WARNING_KEYS))
    assert _warning_table_problems(section) == []
