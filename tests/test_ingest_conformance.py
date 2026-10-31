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
  warnings   = the THIRTEEN-key closed set (§3.2 table)

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
    # #9750: the thirteenth key — a repeated endpoint in the connection's `to`
    # list was collapsed before the operator write (the input set is a SET).
    # Same rule, with one ordering constraint: the SERVED mirror is DERIVED
    # (`config/served-skills-upstream.yml` pins its bytes upstream), so the key
    # lands in the upstream skill repo FIRST and this repo re-derives the mirror
    # with `tools/served_skills_parity.py --sync` in the same change — a
    # hand-edit of the served copy reds the parity gate instead.
    "duplicate_target_deduped",
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
        # is in the THIRTEEN-key enumeration (a key outside it is a divergence)
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
    THIRTEEN keys (the closed-set anchor)."""
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
    THIRTEEN warning keys.

    ⛔ Only an ABSENT mirror skips. The skip used to fire on
    `"promotion_policy" not in text` as well, which made the marker-grep green
    by SKIP in exactly the case it exists to catch: a sync that dropped the
    marker. #1057 (J8) has landed and the served file is tracked in-repo, so
    that branch was dead code whose only reachable outcome was the fail-open
    one — the same defect the sibling gate at
    `test_served_skill_advertises_only_keys_the_product_emits` carried, one
    function below (fix-review finding, P3)."""
    text = _skill_text()
    if text is None:
        pytest.skip("the served mirror is absent from this tree — nothing to "
                    "grade (`website/apps/dashboard/public/skills/"
                    "how-to-use-tortoise/SKILL.md`)")
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
# naming `duplicate_target_deduped` while the fix that would emit it (#10305)
# was still open — an advertised-but-absent key is a behavior claim, not a typo:
# it says “the engine deduplicates a repeated target for you” in the very case
# where the engine double-counts EP.
#
# #10305 has since MERGED, so that key is emissible now and the mirror
# legitimately carries it (upstream #9 restored the THIRTEEN-key table that #8
# had withdrawn). The gate is deliberately NOT written around any one key: all
# three arms are `len(WARNING_KEYS)`-relative, so it keeps biting the NEXT key
# somebody advertises before the engine emits it, and keeps demanding
# completeness when a key IS added — neither side can drift silently, in either
# direction, as the set grows.
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
_COUNT_WORDS = {value: word for word, value in _NUMBER_WORDS.items()}
# A key no engine emits, used as the “planned/servable-one-day” probe. It must
# stay FICTIONAL: the first version of these cases used `duplicate_target_deduped`
# as the reserved key, #10305 then made that key emissible, and the advertised-key
# arm silently lost its teeth (an already-listed key is not a probe).
_PLANNED_KEY = "future_phantom_key"
_WARNINGS_SECTION = re.compile(r"^## Warnings\b.*?(?=^## |\Z)", re.M | re.S)
_DECLARED_COUNT = re.compile(r"\bthe ([A-Z]+)(?:-key table| keys)\b")
# ⛔ No `(?:_…)` REQUIRED component: the first version demanded an underscore,
# so a phantom key written without one (`phantomkey`) was invisible to
# `_listed_keys` and passed all three arms — an advertised-but-unemitted key
# slipping through the one arm written to catch exactly that (#10437 review,
# P2). The block this matches IS the enumeration by construction, so a bare
# lowercase token there is a key claim, not prose.
_KEY_TOKEN = re.compile(r"`([a-z][a-z0-9_]*)`")


def _declared_key_claims(section: str) -> list[tuple[str, int | None]]:
    """Every cardinality the mirror states for the key set, with the word used.

    The value is ``None`` for a word this gate cannot read as a number. That is
    returned rather than RAISED: `_DECLARED_COUNT` matches any all-caps word, so
    a reworded heading ("the ENGINE keys") would otherwise crash the gate with a
    bare `KeyError` in the one arm whose whole job is to say “re-anchor this”
    (#10437 review, P2).
    """
    return [(word, _NUMBER_WORDS.get(word))
            for word in _DECLARED_COUNT.findall(section)]


def _declared_key_count(section: str) -> list[int]:
    """Every cardinality the mirror states for the key set that IS readable."""
    return [value for _word, value in _declared_key_claims(section)
            if value is not None]


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
        truncated list (one key short, heading still declaring the true size),
        which is a reader believing a key does not exist when the engine emits
        it;
      * every cardinality the mirror declares (“the N keys”, the “N-key
        table”, whatever word N is) must EQUAL `len(WARNING_KEYS)` — so the
        count and the list cannot drift apart, and extending the set still
        costs the contract amendment #8499 requires
        (`docs/INGEST_CONTRACT.md` §3.2) instead of being a docs-only edit
        nobody notices.
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

    claims = _declared_key_claims(section)
    declared = [value for _word, value in claims if value is not None]
    unreadable = sorted({word for word, value in claims if value is None})
    if unreadable:
        problems.append(
            f"the served skill states the size of the warning set as "
            f"{unreadable}, which this gate cannot read as a number. If the "
            f"wording changed, re-anchor `_DECLARED_COUNT` / `_NUMBER_WORDS` in "
            f"this test — do not let the arm go dark: the declaration is what "
            f"lets a reader tell a complete enumeration from a truncated one.")
    if not declared and not unreadable:
        problems.append(
            "the served skill no longer states how many keys the closed set "
            "has — re-anchor this gate: the declaration is what a reader uses "
            "to tell a complete enumeration from a truncated one")
    elif declared and set(declared) != {len(WARNING_KEYS)}:
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
    if text is None:
        # ⛔ ONLY an absent mirror skips. The earlier form also skipped when the
        # unrelated `promotion_policy` marker was gone, which made a brand-new
        # gate fail OPEN: a mirror restructure that dropped that marker
        # silently disabled the warnings gate while this line stayed green
        # (#10437 review, P2). The subject here is the `## Warnings` section —
        # the file is tracked in-repo, so `text is None` is the only legitimate
        # "nothing to grade" case.
        pytest.skip("the served mirror is absent from this tree — nothing to "
                    "grade (`website/apps/dashboard/public/skills/"
                    "how-to-use-tortoise/SKILL.md`)")
    match = _WARNINGS_SECTION.search(text)
    assert match is not None, "the served skill has no “## Warnings” section"
    problems = _warning_table_problems(match.group(0))
    assert not problems, "\n".join(problems)


def test_10437_the_gate_does_not_skip_on_a_missing_unrelated_marker(monkeypatch):
    """The gate must NOT skip when a marker unrelated to its subject is gone.

    The ``pytest.skip`` used to fire on ``"promotion_policy" not in text`` too,
    so a mirror restructure that dropped that marker silently disabled a gate
    whose subject is the `## Warnings` section — a fail-OPEN path in a guard
    written to be fail-closed (#10437 review, P2).

    FAILS IF: the skip condition mentions anything but ``text is None``. The
    pre-fix gate returns a SKIP here, which this case catches as the wrong
    exception class (a skip from a gate that HAS its subject is the defect it
    pins, so it is asserted against rather than allowed to colour the run
    green).
    """
    keys = sorted(WARNING_KEYS)
    # The shape the gate exists to red: one key short, heading still truthful.
    defective = _synthetic_warnings_section(_COUNT_WORDS[len(keys)], keys[:-1])
    assert "promotion_policy" not in defective

    monkeypatch.setattr(sys.modules[__name__], "_skill_text", lambda: defective)
    with pytest.raises(BaseException) as excinfo:
        test_served_skill_advertises_only_keys_the_product_emits()
    assert isinstance(excinfo.value, AssertionError), (
        "the gate must RED on an incomplete `## Warnings` section even when the "
        "unrelated promotion_policy marker is absent — it bailed out instead: "
        f"{excinfo.value!r}")
    assert "INCOMPLETE" in str(excinfo.value), excinfo.value

    # The control: only an ABSENT mirror may skip, so the skip branch keeps the
    # subject it was written for rather than being deleted outright.
    monkeypatch.setattr(sys.modules[__name__], "_skill_text", lambda: None)
    with pytest.raises(BaseException) as excinfo:
        test_served_skill_advertises_only_keys_the_product_emits()
    assert type(excinfo.value).__name__ == "Skipped", (
        "an absent mirror is the one case that still skips (nothing to "
        f"grade), but the gate raised {excinfo.value!r}")


# ── non-vacuity: each arm reds on the mirror shape it was written for ──
# Built from a template rather than from the served file so these run (and
# RED) even where the mirror is absent or has already been fixed — the point
# is the GATE, not the artifact of the day.
def _synthetic_warnings_section(heading_word: str | None, keys: list[str],
                               planned: str = _PLANNED_KEY) -> str:
    """A warnings section in the mirror's own shape.

    ``heading_word=None`` renders a heading that states NO cardinality, which is
    its own gate arm (the declaration is what separates a complete enumeration
    from a truncated one). Otherwise the heading says “the <WORD> keys”.

    The planned-key blockquote sits AFTER a blank line, exactly as a mirror that
    *mentions* a key without enumerating it would: `_listed_keys` stops at the
    first blank line, so a key merely named in prose is not counted as
    enumerated. `planned` defaults to the FICTIONAL `_PLANNED_KEY` for the
    reason recorded there.
    """
    listed = " ·\n".join(f"`{key}`" for key in keys)
    heading = ("## Warnings (closed set)" if heading_word is None else
               f"## Warnings — the {heading_word} keys (closed set)")
    return (
        f"{heading}\n\n"
        "`warnings` entries are key-prefixed strings. A key outside this "
        "table is a\ndivergence — report it:\n\n"
        f"{listed}\n\n"
        f"> A further key (`{planned}`) is **reserved**: it joins this closed "
        "set when that fix lands.\n"
    )


def test_10437_warning_table_gate_reds_on_a_truncated_enumeration():
    """The arm this gate was missing: the whole set minus its last key, under a
    heading that still declares the TRUE size. The subset arm is silent (nothing
    advertised) and the count arm is silent (the heading is still truthful), so
    ONLY the completeness arm catches it.
    """
    keys = sorted(WARNING_KEYS)
    section = _synthetic_warnings_section(_COUNT_WORDS[len(keys)], keys[:-1])

    # why the older two arms cannot carry this alone:
    assert not [key for key in _listed_keys(section) if key not in WARNING_KEYS]
    assert _declared_key_count(section) == [len(keys)]

    problems = _warning_table_problems(section)
    assert problems, "a truncated enumeration passed every arm"
    assert keys[-1] in "\n".join(problems), (
        f"the gate reds but does not name the omitted key {keys[-1]!r}, which "
        f"is what tells the fixer which mirror line to add")


def test_10437_warning_table_gate_reds_on_an_advertised_key():
    """The 2026-10-30 regression itself (#10438): a key the engine does not
    emit, INSIDE the enumeration, at a count that truthfully includes it."""
    keys = sorted(WARNING_KEYS)
    section = _synthetic_warnings_section(
        _COUNT_WORDS[len(keys) + 1], [*keys, _PLANNED_KEY])
    problems = _warning_table_problems(section)
    assert problems, "an advertised-but-unemitted key passed every arm"
    assert _PLANNED_KEY in "\n".join(problems)


def test_10437_warning_table_gate_reads_a_key_with_no_underscore():
    """A phantom key spelled WITHOUT an underscore must still be read as one.

    `_KEY_TOKEN` required a `_word` part, so a key such as ``phantomkey`` never
    entered `_listed_keys`: the advertised arm stayed silent, the completeness
    arm stayed silent (every product key was present) and the count arm stayed
    silent (the heading was truthful about 13) — an advertised-but-unemitted
    key passing all three arms, which is the one thing this gate exists to stop
    (#10437 review, P2).

    The three silent arms are asserted here so the case demonstrably isolates
    the token pattern rather than redding for a neighbouring reason.
    """
    keys = sorted(WARNING_KEYS)
    phantom = "phantomkey"  # no underscore: the old token regex could not see it
    section = _synthetic_warnings_section(_COUNT_WORDS[len(keys)],
                                          [*keys, phantom])

    assert _declared_key_count(section) == [len(keys)]  # count arm: silent
    assert phantom in _listed_keys(section), (
        "the gate cannot see a phantom key without an underscore, so its "
        "advertised arm is blind to it")
    problems = _warning_table_problems(section)
    assert any(phantom in problem for problem in problems), problems
    assert not any("INCOMPLETE" in problem for problem in problems), problems


def test_10437_warning_table_gate_reds_on_a_declared_count_mismatch():
    """Every key listed, the heading understates the set: the count and the list
    must not be able to drift apart in either direction."""
    keys = sorted(WARNING_KEYS)
    section = _synthetic_warnings_section(_COUNT_WORDS[len(keys) - 1], keys)
    problems = _warning_table_problems(section)
    assert problems, "a miscounted closed set passed every arm"
    assert str(len(keys)) in "\n".join(problems)


def test_10437_warning_table_gate_reds_on_an_undeclared_count():
    """The fourth arm: the FULL key set under a heading that states NO
    cardinality. Nothing else can catch it — the subset arm is silent (nothing
    advertised) and the count arm has no declaration to compare — so the “no
    longer states how many keys” branch is the only thing that reds it, which
    the slice sets below demonstrate rather than assert. Without this case that
    branch could be deleted with every other test still green (#10437 review,
    P2; the cycle-1 version of this test used `keys[:-1]`, so the completeness
    arm fired too and the isolation the docstring claimed was not measured).
    """
    keys = sorted(WARNING_KEYS)
    section = _synthetic_warnings_section(None, keys)

    assert _declared_key_count(section) == []
    assert not [key for key in _listed_keys(section) if key not in WARNING_KEYS]

    problems = _warning_table_problems(section)
    assert any("no longer states how many keys" in problem
               for problem in problems), problems
    # The COMPLETE key set, so the completeness arm is silent and the claim in
    # the docstring is DEMONSTRATED here (#10437 cycle-2 review, P2).
    assert not any("INCOMPLETE" in problem for problem in problems), problems


def test_10437_warning_table_gate_reds_on_an_unreadable_count():
    """A cardinality word the gate cannot read is a PROBLEM, not a traceback:
    a reworded heading must red with an instruction to re-anchor (#10437
    review, P2).
    """
    keys = sorted(WARNING_KEYS)
    section = _synthetic_warnings_section("ENGINE", keys)

    problems = _warning_table_problems(section)
    assert any("cannot read as a number" in problem for problem in problems), \
        problems
    # The section DOES state a size, it just states it unreadably, so the
    # “no longer states how many keys” arm must stay SILENT. Without this the
    # `and not unreadable` guard on that branch can be dropped and every test
    # stays green while the gate emits a false second message (#10437 cycle-3
    # review, P2).
    assert not any("no longer states how many keys" in problem
                   for problem in problems), problems


def test_10437_warning_table_gate_is_clean_on_the_mirror_shape():
    """The control: the real mirror's shape (every key, the correct count, a
    further key named only in prose) is CLEAN — so the three cases above are
    proving the arms fire, not that the gate reds unconditionally."""
    keys = sorted(WARNING_KEYS)
    section = _synthetic_warnings_section(_COUNT_WORDS[len(keys)], keys)
    assert _warning_table_problems(section) == []
