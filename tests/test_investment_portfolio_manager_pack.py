"""Investment portfolio manager overlay pack (issue #9663) — clash rules and
the value test for every kind.

What this file pins, in the issue's own terms:

1. **Overlay, not a second home.** The pack `depends_on: [venture]`, declares no
   `equivalentTo`, and never re-declares a venture kind. Where the
   portfolio-manager concept IS a venture concept the manifest references
   `venture:investment` NAMESPACED — an overlay that renames the position is the
   one outcome #9663 exists to prevent, because it gives one concept two homes.
2. **The value test, computed rather than claimed.** Every admitted kind must
   pass at least one of the four bullets (`statuses`+`folds`, `values`, a chain
   step, a relation endpoint carrying a mechanism). The test below RE-DERIVES
   each kind's passing bullets from the compiled manifest — a kind added later
   with no shape behind it fails here instead of validating clean.
3. **Every rejected candidate names its absorbing home** — a claim is the core
   Point kind (`statement`), `benchmark` rides `allocationTarget`'s `synonyms`,
   and `rebalance` is absorbed by the KIND `venture:actionItem` (this pack
   declares no `rebalance` kind or synonym, and the test enforces that).
4. **The clash rules are declared where the classifier reads them** — every
   kind carries `synonyms` / `examples` (classifier) and `nearMisses` (rerank),
   so ambiguity is resolved in the manifest rather than by minting a kind.
5. **Core extraction is unchanged with the pack OFF** — and the UNGATED path
   is measured too, because it is a different path: a graph with no
   `:PackInstall` records gets the catalog union (#2714/#5163), so the pack IS
   there. The gated half is compared against a packs dir with this pack
   REMOVED (value brief, kind-index spec, cache key); the ungated half asserts
   that stripping this pack's own contributions leaves exactly the gated
   compile — the pack adds entries and modifies nobody's.
6. **Each new kind is mintable on a fresh graph**, and the declared
   `values` declaration reaches the extraction prompt.

Docker lane (default): TORTOISE_DB_URI must be set (epic #1647 P4).
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import ClassVar
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import yaml

from tests._live_utils import LIVE_URI_SKIP_REASON
from tortoise import extractor_v2 as v2
from tortoise.kind_index import cache_key_for
from tortoise.pack_registry import (
    CANONICAL_POINT_KINDS,
    PackRegistry,
    default_packs_dir,
)
from tortoise.pack_values import declared_value_fields
from tortoise.sdk import TortoiseSDK
from tortoise.value_extractor import compile_kind_index_spec, compile_value_brief

REPO_PACKS_DIR = Path(__file__).resolve().parents[1] / "packs"
PACK_DIR = REPO_PACKS_DIR / "investment-portfolio-manager"
MANIFEST = PACK_DIR / "manifest.yaml"
RAW = yaml.safe_load(MANIFEST.read_text())

NS = "portfolio"

#: The approved kind set. Objects hold state; the mark is a dated occurrence.
EXPECTED_OBJECT_KINDS = frozenset({"mandate", "allocationTarget"})
EXPECTED_EVENT_KINDS = frozenset({"valuationMark"})

#: The value test (#9663), one bullet per admitted kind:
#:   1 = closed `statuses` lifecycle WITH a declared `folds`
#:   2 = declares a `values` shape
#:   3 = a step in a declared `chains` path
#:   4 = an endpoint of a declared relation carrying a `mechanism`
#: Every admitted kind names the bullet its admission RESTS on. It may pass
#: others (allocationTarget also ends a relation) — the named one is why it is a
#: kind and not a synonym.
VALUE_TEST = {
    "mandate": 4,
    "allocationTarget": 2,
    "valuationMark": 4,
}

#: Candidates #9663 examined and the test PRUNED, with the home that absorbs the
#: mention. A rejected candidate that names no home is a concept with nowhere to
#: land, which is how a "drop it" becomes an extraction hole. The home is either
#: a `synonyms` entry on an existing kind (`benchmark`), a KIND the mention
#: resolves to (`rebalance` → venture's action item; `thesis` → the core Point
#: kind) — and the third field says which shape, so the test can enforce the
#: right one instead of accepting a synonym list that never names the candidate.
REJECTED = {
    "thesis": ("core point", "statement"),
    "benchmark": ("synonyms", "allocationTarget"),
    "rebalance": ("kind", "venture:actionItem"),
}


def _server_uri_set() -> bool:
    uri = os.environ.get("TORTOISE_DB_URI") or ""
    return uri.split("://", 1)[0] in {"docker", "redis", "rediss"} and bool(
        urlparse(uri).hostname)


requires_db = pytest.mark.skipif(not _server_uri_set(), reason=LIVE_URI_SKIP_REASON)


@pytest.fixture(scope="module")
def registry() -> PackRegistry:
    r = PackRegistry(default_packs_dir())
    n = r.load_all()
    assert n == len(list(REPO_PACKS_DIR.glob("*/manifest.yaml"))) - 1, (
        f"every catalog dir except _template must load, got {n}: {r.errors}")
    assert not r.errors, f"whole-registry compile must be clean: {r.errors}"
    return r


@pytest.fixture(scope="module")
def pack(registry: PackRegistry):
    return registry.get_pack(NS)


@pytest.fixture(scope="module")
def venture(registry: PackRegistry):
    return registry.get_pack("venture")


@pytest.fixture
def sdk(tmp_path, force_sparse_tfidf):
    """A fresh graph per test."""
    s = TortoiseSDK(db_path=str(tmp_path / "portfolio.db"),
                    namespace=f"test_portfolio_{os.urandom(4).hex()}")
    try:
        yield s
    finally:
        s.close()


def _portfolio_synonyms(pack) -> set[str]:
    """Every `synonyms` entry THIS pack declares, across all its kinds."""
    return {
        word
        for spec in pack.kind_defs.values()
        for word in spec.get("synonyms", [])
    }


def _passes_bullets(kind: str, spec: dict, manifest) -> set[int]:
    """The value-test bullets ONE kind actually passes, derived from the manifest.

    Deliberately re-derived rather than read from a comment: the manifest's
    justification prose is not evidence, the declarations are.
    """
    passed: set[int] = set()
    if spec.get("statuses") and spec.get("folds"):
        passed.add(1)
    if spec.get("values"):
        passed.add(2)
    chain_steps = {
        str(step).rsplit(":", 1)[-1]
        for chain in (manifest.chains or [])
        for step in (chain.get("steps") or [])
    }
    if kind in chain_steps:
        passed.add(3)
    for rel in manifest.relations:
        if rel.get("mechanism") not in ("IMPL", "NAND"):
            continue
        ends = {
            str(rel.get("fromKind", "")).rsplit(":", 1)[-1],
            str(rel.get("toKind", "")).rsplit(":", 1)[-1],
        }
        if kind in ends:
            passed.add(4)
    return passed


# ── 1. The pack loads, and it is an OVERLAY ───────────────────────────────


class TestManifestAndOverlayShape:
    def test_manifest_validates_clean_in_the_whole_catalog(self, registry, pack):
        assert pack is not None, "portfolio pack must load"
        assert not registry.errors
        assert pack.namespace == NS
        assert pack.name == "Investment Portfolio Manager"
        assert pack.version == "0.1.0"
        assert pack.tier == "free"

    def test_it_declares_its_venture_dependency(self, pack):
        """#2781: the cross-pack references are DECLARED, so an absent `venture`
        is reported against this pack instead of isolating it."""
        assert list(pack.depends_on) == ["venture"]

    def test_it_survives_an_absent_venture_and_names_it(self, tmp_path):
        """The #2781 promise the declaration makes, ASSERTED (a declaration that
        is never exercised is a comment).

        This pack names two of venture's own predicates as relation
        `nearMisses` (`gatedBy`, `producedBy`), so it is the first shipped pack
        to depend on relation `nearMisses` resolution going through the same
        missing-peer gate the relation ENDPOINTS use. Before that routing it was
        DROPPED with no diagnostic naming the pack to install. The unit-level
        pin for the registry rule is in tests/test_pack_registry_gaps.py.
        """
        dst = tmp_path / "packs"
        shutil.copytree(REPO_PACKS_DIR, dst)
        shutil.rmtree(dst / "venture")

        reg = PackRegistry(dst)
        reg.load_all()
        assert NS in reg.packs, f"a missing peer must not isolate the pack: {reg.errors}"
        assert NS not in reg.errors, reg.errors
        msgs = " ".join(reg.unresolved_refs.get(NS, []))
        assert "venture" in msgs and "not loaded" in msgs, msgs

    def test_kind_buckets_are_exactly_the_approved_set(self, pack):
        assert set(pack.object_kinds) == EXPECTED_OBJECT_KINDS
        assert set(pack.event_kinds) == EXPECTED_EVENT_KINDS
        assert not pack.point_kinds, (
            "a claim is a core Point (statement), never a pack pointKind")

    def test_state_lives_on_objects_and_events_carry_none(self, pack):
        for kind in pack.object_kinds:
            assert pack.store_as(kind) == "entity", kind
        for kind in pack.event_kinds:
            assert pack.store_as(kind) == "event", kind
        # The mark is an occurrence, so it declares no lifecycle: state would
        # have to move onto an Object, and the position's belongs to venture.
        assert "statuses" not in pack.kind_defs["valuationMark"]

    def test_the_pack_renames_nothing_venture_already_models(
            self, pack, venture):
        """⛔ The outcome #9663 exists to avoid: one concept, two homes."""
        assert not pack.kind_equivalences, (
            "no equivalentTo: this pack is not the same bare name under a "
            "second namespace")
        # No declared kind collides with a venture kind — the overlay adds
        # vocabulary, it does not restate the position/programme/asset.
        assert not (set(pack.object_kinds) | set(pack.event_kinds)) & (
            set(venture.object_kinds) | set(venture.event_kinds))

    def test_every_reference_to_the_position_is_namespaced(self, pack):
        """`venture:investment` — never bare (the issue's lever 1)."""
        endpoints = set()
        for rel in pack.relations:
            endpoints.add(rel.get("fromKind"))
            endpoints.add(rel.get("toKind"))
        assert "venture:investment" in endpoints
        # No bare `investment` endpoint: a bare ref resolves by accident of
        # load order, which is exactly the ambiguity this manifest removes.
        assert "investment" not in endpoints
        for kind, spec in pack.kind_defs.items():
            assert "investment" not in (spec.get("nearMisses") or []), kind
            assert "venture:investment" in (spec.get("nearMisses") or [
            ]) or kind == "valuationMark", kind


# ── 2. The value test — computed, not claimed ─────────────────────────────


class TestValueTest:
    def test_every_admitted_kind_passes_the_bullet_it_claims(self, pack):
        for kind, bullet in VALUE_TEST.items():
            spec = pack.kind_defs[kind]
            passed = _passes_bullets(kind, spec, pack)
            assert bullet in passed, (
                f"{NS}:{kind} claims value-test bullet ({bullet}) but the "
                f"manifest passes {sorted(passed) or 'none'} — an admission "
                f"with no shape behind it belongs in a synonym, not a kind")

    def test_no_kind_is_admitted_without_passing_a_bullet(self, pack):
        declared = set(pack.object_kinds) | set(pack.event_kinds)
        assert declared == set(VALUE_TEST), (
            "every declared kind must be named in VALUE_TEST with the bullet "
            "its admission rests on")
        for kind in declared:
            assert _passes_bullets(kind, pack.kind_defs[kind], pack), (
                f"{NS}:{kind} passes no value-test bullet at all")

    def test_allocationTarget_declares_the_percent_shape(self, pack):
        """Bullet (2), concretely: `is this within mandate?` gets a SHAPE."""
        values = declared_value_fields(pack.kind_defs["allocationTarget"])
        assert values == [{
            "name": "targetPct",
            "valueKind": "percent",
            "required": True,
            "cardinality": "one",
        }]

    def test_relation_mechanisms_bind_a_write_path(self, pack):
        """Bullet (4) means a DECLARED mechanism, not a declaration-only edge
        (#2766): a relation that binds no write path is not an admission."""
        from tortoise.pack_registry import RELATION_WRITABLE, relation_write_verdict

        for rel in pack.relations:
            verdict, paths = relation_write_verdict(rel)
            assert verdict == RELATION_WRITABLE, (rel["predicate"], verdict)
            assert paths

    def test_no_chain_is_declared_because_none_would_reach_a_prompt(self, pack):
        """`master["chains"]` is built from `extractor_v2.CHAINS`, so a chain
        declared here would reach no prompt. The pack says so instead of
        shipping a declaration that does nothing."""
        assert not pack.chains

    def test_kind_descriptions_are_extractor_prose_not_review_bookkeeping(
            self, pack):
        """`description` is PROMPT TEXT, not a comment (issue #9663).

        Three consumers read it verbatim: `value_extractor` carries it into the
        value brief, `_build_master_from_brief` puts it in `pack_kinds`, and
        `_render_master_verbose`/the compact render emit
        ``- {kind} — {description}`` into the S2/S4 classification prompt
        (`compile_kind_index_spec` embeds it too). So review bookkeeping in a
        description ships into EVERY extraction on the catalog-union path
        (#2714/#5163 — every graph with no `:PackInstall` records), and it is
        the one place this pack's design rationale must NOT live: the rationale
        belongs in YAML comments, which do not ship.

        Both halves are pinned — the junk marker is absent, and the prose that
        the extractor actually needs is still there (a fix that deleted the
        kind's description outright would pass the first assertion alone).
        """
        for kind, spec in pack.kind_defs.items():
            description = spec.get("description") or ""
            assert "[" not in description and "value test" not in description, (
                f"{NS}:{kind}'s description carries review bookkeeping; it is "
                f"rendered into every extraction prompt — move it to the "
                f"manifest's comment header: {description!r}")
            assert description.strip(), (
                f"{NS}:{kind} ships no description — the extractor prompt "
                f"renders it as the kind's only disambiguation prose")
        # The extractor-facing disambiguation this pack NEEDS is extractor prose
        # and must survive the move (`test_benchmark_is_absorbed_only_with_a_
        # weight` pins the same sentence from the declaration side).
        assert "bare benchmark LABEL" in (
            pack.kind_defs["allocationTarget"]["description"])

    # ── rejected candidates ───────────────────────────────────────────────

    def test_thesis_is_a_core_point_not_a_kind(self, pack):
        home, ref = REJECTED["thesis"]
        assert home == "core point" and ref in CANONICAL_POINT_KINDS
        assert "thesis" not in pack.kind_defs
        assert not pack.point_kinds
        # …and no synonym list here quietly claims it either.
        assert "thesis" not in _portfolio_synonyms(pack)

    def test_benchmark_is_absorbed_only_with_a_weight(self, pack):
        """`benchmark` is absorbed by a WEIGHT-flavoured synonym, and the bare
        label is deliberately NOT a synonym.

        The rule is stated in one place and enforced here: this kind declares
        `targetPct` as REQUIRED, so routing a bare "our benchmark is the MSCI
        World" mention to it would push the extractor to invent a percentage the
        utterance never stated. A benchmark with a weight is this kind; a bare
        benchmark label stays prose.
        """
        home, ref = REJECTED["benchmark"]
        assert home == "synonyms"
        synonyms = pack.kind_defs[ref]["synonyms"]
        assert "benchmark weight" in synonyms and "reference weight" in synonyms
        assert "benchmark" not in synonyms
        # …and the description states the same boundary, so the two cannot drift.
        assert "bare benchmark LABEL" in pack.kind_defs[ref]["description"]
        # The declaration that makes the boundary load-bearing.
        assert declared_value_fields(pack.kind_defs[ref])[0]["required"] is True

    def test_rebalance_rides_ventures_action_item_predicate(self, pack, venture):
        """`rebalance` is an OWNED DATED ACTION, not a state carrier, so its home
        is the KIND `venture:actionItem` — the absorbing `synonyms` are that
        kind's existing action aliases. ⛔ This pack must NOT claim it: a second
        home here is the rename #9663 exists to avoid."""
        home, ref = REJECTED["rebalance"]
        assert home == "kind"
        ns, kind = ref.split(":", 1)
        assert ns == "venture" and kind in venture.kind_defs
        assert {"action item", "task", "next step"} <= set(
            venture.kind_defs[kind]["synonyms"])
        assert "rebalance" not in pack.kind_defs
        assert "rebalance" not in _portfolio_synonyms(pack), (
            "this pack must not absorb `rebalance` — the mention belongs to "
            "venture's action item, and claiming it here gives the same "
            "concept two homes")


# ── 3. Clash rules live where the classifier reads them ───────────────────


class TestClashRules:
    def test_every_kind_carries_synonyms_examples_and_near_misses(self, pack):
        for kind in set(pack.object_kinds) | set(pack.event_kinds):
            spec = pack.kind_defs[kind]
            assert spec.get("synonyms"), f"{kind} needs synonyms"
            assert spec.get("examples"), f"{kind} needs examples"
            assert spec.get("nearMisses"), f"{kind} needs nearMisses"

    def test_near_misses_name_the_confusable_sibling(self, pack):
        """The rerank reads `nearMisses`, so the confusable sibling is named
        there rather than left to the classifier's judgement."""
        assert "venture:investment" in pack.kind_defs["mandate"]["nearMisses"]
        assert "venture:fundingAgreement" in pack.kind_defs["mandate"]["nearMisses"]
        assert "mandate" in pack.kind_defs["allocationTarget"]["nearMisses"]
        assert "venture:disbursement" in pack.kind_defs["valuationMark"]["nearMisses"]

    def test_pt_br_synonyms_follow_the_venture_convention(self, pack):
        """`venture` carries pt-BR synonyms (investida, desembolso). The
        overlay does the same, so the same corpus classifies consistently."""
        pt = {"mandato", "alocação alvo", "marcação"}
        found = {
            word
            for spec in pack.kind_defs.values()
            for word in spec.get("synonyms", [])
        }
        assert pt <= found

    def test_relation_near_misses_disambiguate_the_two_depends_on_edges(self, pack):
        """`constrainedBy` (position bound to a mandate) and `boundedBy` (target
        bound to a mandate) are two dependsOn edges; each names the other."""
        by_pred = {r["predicate"]: r for r in pack.relations}
        assert "boundedBy" in by_pred["constrainedBy"]["nearMisses"]
        assert "constrainedBy" in by_pred["boundedBy"]["nearMisses"]

    #: Declared synonyms deliberately NOT in the trigger row, each with the
    #: reason. The asymmetry that decides every exclusion: a SPURIOUS trigger is
    #: EXPENSIVE — one match suppresses the all-packs fallback and strips every
    #: other pack's kinds from the prompt — while a MISSING trigger costs the
    #: overlay, and sometimes does not even cost that.
    #:
    #: ⚠️ State the cost ACCURATELY: the all-packs fallback fires only when
    #: NOTHING matched in ANY namespace, and the sibling rows are full of ordinary
    #: English (`dev:` matches "deploy"/"sprint"/"test", `venture:` matches
    #: "fund"/"asset"). So a genuinely portfolio-shaped story whose ONLY
    #: portfolio vocabulary is an excluded synonym does lose the overlay when a
    #: sibling also matches — "the allocation target for the fund was missed;
    #: deploy the fix" selects dev+venture and no `portfolio:*`. That loss is
    #: ACCEPTED and pinned below (`test_the_accepted_loss_is_a_recorded_decision`),
    #: not denied: the alternative costs another pack's whole vocabulary.
    #:
    #: The row's own invariant (asserted in `test_compact_mode_can_gate_the_
    #: namespace_by_story`) is: an entry is the namespace name, or it STARTS
    #: WITH `investment ` (the SPACE prefix, not a bare containment and not the
    #: hyphen form — a bare `investment` collides in "the investment of effort",
    #: and `investment-grade` collides in "sub-investment-grade bonds"). Every
    #: ENGLISH exclusion below fails that invariant for a
    #: measured reason — each was run through the REAL `_select_pack_kinds`
    #: against the real catalog and selected `portfolio:*` on ordinary prose,
    #: dropping the other packs. `peso alvo` is excluded for a different reason
    #: (the rows are English-only), which is why it is listed last and probed
    #: with a pt-BR sentence.
    NON_TRIGGER_SYNONYMS: ClassVar[dict[str, str]] = {
        "allocation target": (
            "budgeting prose — 'the allocation target for the training budget "
            "was missed' selected portfolio:* ALONE"),
        "target allocation": (
            "the same budgeting collision, mirrored — 'the council approved "
            "its target allocation of the road budget'"),
        "target weight": (
            "engineering prose — 'target weight for the casing'"),
        "policy weight": (
            "engineering prose — 'the policy weight of the review was high'"),
        "benchmark weight": (
            "engineering prose — 'the benchmark weight for the new composite "
            "was 4.2 kg' selected portfolio:* ALONE. This is the entry that "
            "falsified the earlier '+/- 1 sibling carries the meaning' "
            "rationale: it collides in exactly the sentence that makes "
            "'reference weight' unsafe"),
        "reference weight": (
            "engineering prose — 'the reference weight of the assembly is "
            "4.2 kg' selected portfolio:* ALONE"),
        "policy statement": (
            "compliance prose — 'our privacy policy statement explains how "
            "the data is handled' selected portfolio:* and dropped dev, "
            "marketing, pm and venture kinds"),
        "mandate limits": (
            "ordinary English — 'the mandate limits how much we can spend'"),
        # A COMPOUND synonym is not automatically safe: the two below rode
        # inside an ordinary word one character to the left, which is exactly
        # the trap bare `valuation` was removed for.
        "valuation mark": (
            "a SUBSTRING of 'evaluation mark' and 'revaluation mark' — "
            "'valuation mark' in 'the evaluation mark scheme' is True, so the "
            "trap the bare 'valuation' entry was removed for reappears one "
            "word longer"),
        "fair value mark": (
            "'fair value' is an accounting term for ANY asset, so it does not "
            "name a portfolio position ('a fair value mark to each essay' "
            "still matches); a genuine portfolio valuation carries "
            "'portfolio' or 'investment'"),
        "write-down": (
            "ordinary English/accounting prose ('a write-down of the "
            "inventory at year end') — not a portfolio-specific mention"),
        "write-up": (
            "ordinary English prose — measured: 'the write-up covered the "
            "migration' selected portfolio:* ALONE"),
        # The trigger rows are English throughout (`_PACK_TRIGGERS` has no
        # pt-BR entry in any pack); a pt-BR mention reaches the pack through its
        # own synonym layer at classification, not through story selection.
        "peso alvo": "pt-BR: story selection in `_PACK_TRIGGERS` is English-only",
    }

    def test_compact_mode_can_gate_the_namespace_by_story(self, pack):
        """The namespace has a `_PACK_TRIGGERS` row, so compact-mode story
        selection gates it instead of injecting it WHOLE into every prompt (the
        fail-open fallback `_select_pack_kinds` documents).

        Three directions, because a row alone is not a gate, and one wrong
        trigger word is worse than no row: `_select_pack_kinds` matches
        SUBSTRINGS, and a single spurious match suppresses the all-packs
        fallback — so a false-positive trigger does not add a section, it DROPS
        every other pack's kinds.

        The assertion that matters is NARROWING (`set(hit) != set(pack_kinds)`):
        a bare "is the namespace present?" check is satisfied by the all-packs
        fallback for ANY synonym the row omits, which is how a missing trigger
        passed review twice.
        """
        from tortoise.extractor_v2 import _PACK_TRIGGERS, _select_pack_kinds

        assert f"{NS}:" in _PACK_TRIGGERS
        assert all(isinstance(t, str) and t for t in _PACK_TRIGGERS[f"{NS}:"])

        # The row is derived from the manifest, not from memory: every entry is
        # the namespace's own name or a declared synonym carrying a space or a
        # hyphen (a single generic word is exactly the substring trap).
        declared = {syn.lower()
                    for spec in RAW["ontology"]["kindDefs"].values()
                    for syn in (spec.get("synonyms") or [])}
        compound = {w for w in declared
                    if (" " in w or "-" in w) and w.isascii()}
        assert compound, "the manifest must declare compound synonyms"
        expected = (compound - set(self.NON_TRIGGER_SYNONYMS)) | {NS}
        assert set(_PACK_TRIGGERS[f"{NS}:"]) == expected, (
            set(_PACK_TRIGGERS[f"{NS}:"]) ^ expected)

        # THE INVARIANT, mechanically: a substring match over a phrase this
        # generic cannot be trusted to a judgement call, so an entry is the
        # namespace's own name or an `investment `-prefixed phrase (SPACE form
        # only). The prefix matters: a bare "investment" would satisfy a naked
        # containment test while colliding in "the investment of effort" and
        # "disinvestment of the plant". The HYPHEN form is NOT admitted —
        # measured, `investment-grade` selects `portfolio:*` alone on
        # "sub-investment-grade bonds widened the spread".
        row = _PACK_TRIGGERS[f"{NS}:"]
        assert all(t == NS or t.startswith("investment ") for t in row), [
            t for t in row if not (t == NS or t.startswith("investment "))]

        # The REAL catalogue, so a sibling namespace collision (agent-ops's
        # "policy") is visible: a four-entry fixture hides exactly the failure
        # this row exists to avoid.
        pack_kinds = v2._build_master_from_brief(
            compile_value_brief())["pack_kinds"]

        hit = _select_pack_kinds(
            "the board reviewed the investment mandate last week", pack_kinds)
        assert f"{NS}:mandate" in hit, list(hit)
        assert set(hit) != set(pack_kinds), "that is the all-packs fallback"
        assert "dev:issue" not in hit, list(hit)

        # An unrelated story that DOES match another pack — so the result is a
        # real selection, not the all-packs fallback (which would make the
        # negative assertion vacuous).
        miss = _select_pack_kinds(
            "deploy the hotfix and fix the CI test failure", pack_kinds)
        assert "dev:issue" in miss, list(miss)
        assert not [k for k in miss if k.startswith(f"{NS}:")], list(miss)

        # The substring trap: "evaluation" is not "valuation mark". A trigger
        # that matched it would drop dev's kinds from an unrelated prompt — the
        # failure mode is vocabulary LOSS, not an extra section.
        evaluation = _select_pack_kinds(
            "the quarterly evaluation of the design system is in this deploy",
            pack_kinds)
        assert "dev:issue" in evaluation, list(evaluation)
        assert not [k for k in evaluation if k.startswith(f"{NS}:")], list(evaluation)

        # …and a COMPOUND entry is not automatically safe: every exclusion in
        # NON_TRIGGER_SYNONYMS has an ordinary-prose probe here, so the row
        # cannot regain one by declaring it in the manifest. Each story also
        # matches ANOTHER pack, so the negative assertion is not satisfied by
        # the all-packs fallback. The last probe is pt-BR: `peso alvo` is
        # excluded because these rows are English-only, and the probe pins that
        # an untranslated mention stays out of the row rather than matching it.
        for story in (
            "the evaluation mark scheme for the coursework, then deploy it",
            "the revaluation mark was booked, so deploy the fix",
            "a fair value mark was added to each essay; deploy the fix",
            "the reference weight of the assembly is 4.2 kg; deploy the fix",
            "the benchmark weight for the new composite was 4.2 kg; deploy it",
            "the policy weight of the review was high; deploy the fix",
            "the target weight for the casing was 4.2 kg; deploy the fix",
            "the allocation target for the training budget was missed; deploy",
            "the council approved its target allocation of the road budget; "
            "deploy the fix",
            "our privacy policy statement explains the data; deploy the fix",
            "the mandate limits how much we can spend, so deploy the fix",
            "a write-down of the inventory at year end, then deploy",
            "the write-up of the release covered the deploy",
            "o conselho revisou o peso alvo no semestre passado; deploy the fix",
        ):
            got = _select_pack_kinds(story, pack_kinds)
            assert not [k for k in got if k.startswith(f"{NS}:")], (story, got)
            assert "dev:issue" in got, (story, got)

        # Two of those exclusions could never have matched this namespace
        # ALONE, and the row's comment states that as the reason they are not
        # counter-examples to the measurement: `policy statement` and `policy
        # weight` also carry `agent-ops:`'s bare "policy". Pinned, not a
        # fail-on — the point is that the RECORD stays checkable rather than
        # being prose that re-stales the day `agent-ops:`'s row changes.
        for entry in ("policy statement", "policy weight"):
            assert entry in self.NON_TRIGGER_SYNONYMS, entry
            assert any(t in entry for t in _PACK_TRIGGERS["agent-ops:"]), entry

        # Bare generic words are the OTHER half of the same trap: an overlay
        # trigger must match the mentions the pack DECLARES, and only those.
        # Each of these is a real-world sentence with no portfolio subject, and
        # each has to leave the selection to the packs that story is about.
        for story in ("the team's mandate is to ship the CLI during this deploy",
                      "resource allocation across the fleet during this deploy",
                      "the accessibility audit found a visual impairment in the "
                      "keyboard navigation flow, so fix the UI test"):
            got = _select_pack_kinds(story, pack_kinds)
            assert not [k for k in got if k.startswith(f"{NS}:")], (story, got)
            assert set(got) != set(pack_kinds), (
                "the story must match some OTHER pack, or the all-packs "
                "fallback makes this assertion vacuous")

        # …and every compound synonym the pack declares (minus the recorded
        # exceptions) must NARROW to the namespace, not merely appear in the
        # fallback — including the ones a sibling would otherwise win (a bare
        # "policy statement" contains agent-ops's "policy").
        for mention in sorted(compound - set(self.NON_TRIGGER_SYNONYMS)):
            story = f"the board reviewed the {mention} last week during the deploy"
            got = _select_pack_kinds(story, pack_kinds)
            assert [k for k in got if k.startswith(f"{NS}:")], (mention, got)
            assert set(got) != set(pack_kinds), (mention, "all-packs fallback")

    def test_every_trigger_mention_keeps_the_endpoints_own_kind(self, pack):
        """#9733 — the closed list a trigger mention produces CONTAINS the
        position kind this pack's own relations point at.

        The row alone proved NARROWING and never checked that the narrowed
        vocabulary can still spell the pack's declarations: `marks` and
        `constrainedBy` both take a `venture:investment` endpoint, `depends_on`
        declares `venture`, and three of the four `portfolio:` trigger words are
        not also `venture:` triggers (only the bare `portfolio` one is, and it is
        also a `pm:` trigger) — so a portfolio story used to lose the very kind a
        valuation mark exists to value, while the list is rendered under
        "EVERY kind you emit MUST come from this list".

        ⛔ The "three of the four" is not a hedge, it is the measurement, and it
        is what makes the DISCRIMINATION section below necessary: the `portfolio`
        mention selects the `venture:` row on its own, so that one mention never
        demonstrated the defect. The pre-#9733 sentence said "no `portfolio:`
        trigger word is also a `venture:` trigger" — false, and contradicted by
        this test's own assertion at the bottom.

        Every trigger word is exercised, not one: a row is data and the defect
        is a missing CO-SELECTION, so probing a single mention would pass while
        another entry still lost the overlay's dependency.
        """
        from tortoise.extractor_v2 import _PACK_TRIGGERS, _select_pack_kinds

        master = v2._build_master_from_brief(compile_value_brief())
        pack_kinds, refs = master["pack_kinds"], master["pack_refs"]
        # The declaration the fix honours, read from the manifest rather than
        # from memory: `venture` is the pack's declared dependency, and the
        # referenced endpoint lives in that namespace. `pack_refs` is keyed by
        # KIND (#9733), so the row is read off the declaring kind itself.
        assert list(pack.depends_on) == ["venture"]
        assert refs["portfolio:valuationMark"] == ["venture:investment"], (
            refs.get("portfolio:valuationMark"))

        for mention in _PACK_TRIGGERS[f"{NS}:"]:
            story = f"the board reviewed the {mention} last week"
            hit = _select_pack_kinds(story, pack_kinds, refs)
            assert "venture:investment" in hit, (mention, story, sorted(hit))
            # NARROWING still holds — the co-selection is not the all-packs
            # fallback restored under another name.
            assert set(hit) != set(pack_kinds), (mention, "all-packs fallback")
            assert "dev:issue" not in hit, (mention, sorted(hit))

        # …and the assertion above is not vacuous: the TRIGGER WORDS alone drop
        # the endpoint (the pre-#9733 head, i.e. the failing side) for every
        # mention except the bare `portfolio` one — which `venture:` and `pm:`
        # ALSO carry, so that mention never demonstrated the defect. Pin BOTH
        # facts, so the discriminating set cannot shrink to nothing unnoticed
        # (the whole row relying on a sibling's trigger would be the same strip
        # one word over).
        discriminating = [
            mention for mention in _PACK_TRIGGERS[f"{NS}:"]
            if "venture:investment" not in _select_pack_kinds(
                f"the board reviewed the {mention} last week", pack_kinds)]
        assert discriminating, (
            "no mention discriminates — every portfolio trigger also matches "
            "venture:, so this test would pass on the unfixed code")
        assert set(discriminating) == set(_PACK_TRIGGERS[f"{NS}:"]) - {"portfolio"}, (
            discriminating)

    def test_the_accepted_loss_is_a_recorded_decision(self, pack):
        """The COST of the row's invariant, pinned so it is a decision and not an
        accident.

        `_select_pack_kinds` falls back to ALL packs only when NOTHING matched in
        ANY namespace, and the sibling rows carry ordinary English (dev:'s
        "deploy"/"sprint"/"test", venture:'s "fund"/"asset"). A genuinely
        portfolio-shaped story whose only portfolio vocabulary is an excluded
        synonym therefore LOSES the overlay as soon as a sibling matches. That is
        the accepted side of the trade — a spurious trigger would instead strip
        another pack's whole vocabulary — and it is pinned here so a future row
        change that alters it is visible rather than silent.

        If one of these starts selecting `portfolio:*`, the trade-off moved:
        update the recorded decision above rather than deleting this test.
        """
        from tortoise.extractor_v2 import _select_pack_kinds

        pack_kinds = v2._build_master_from_brief(
            compile_value_brief())["pack_kinds"]
        for story in (
            "the allocation target for the fund was missed; deploy the fix",
            "the board reviewed the valuation mark before the sprint",
            "the benchmark weight for the mandate was off; deploy the fix",
        ):
            got = _select_pack_kinds(story, pack_kinds)
            assert not [k for k in got if k.startswith(f"{NS}:")], (
                "the accepted cost changed — update the recorded decision",
                story, got)


# ── 4. Core extraction unchanged with the pack OFF ────────────────────────


@pytest.fixture(scope="module")
def packs_without_portfolio(tmp_path_factory) -> Path:
    """A copy of the shipped catalog with this pack REMOVED — the comparison
    target for "core extraction unchanged with the pack off"."""
    dst = tmp_path_factory.mktemp("packs-no-portfolio") / "packs"
    shutil.copytree(REPO_PACKS_DIR, dst)
    shutil.rmtree(dst / "investment-portfolio-manager")
    return dst


def _strip_portfolio(table: dict) -> dict:
    """`table` minus THIS pack's own contributions.

    Both compiles key their kind entries by `ns:kind`, but
    `memory_granularity` is keyed by bare namespace — filtering only on the
    former would leave one of this pack's entries in place and make the
    comparison below fail for the wrong reason. `pack_refs` (#9733) is the same
    shape and needs the same treatment on BOTH sides: the pack's own row (its
    cross-pack relation endpoints), and any `portfolio:*` endpoint another
    pack's row names — a brief section is a contribution surface, so a new one
    must not make "nothing else moved" read as false.
    """
    out = {k: v for k, v in table.items() if not k.startswith(f"{NS}:")}
    gran = out.get("memory_granularity")
    if isinstance(gran, dict) and NS in gran:
        out["memory_granularity"] = {
            n: g for n, g in gran.items() if n != NS}
    refs = out.get("pack_refs")
    if isinstance(refs, dict):
        # #9733: `pack_refs` is keyed by KIND (`portfolio:valuationMark`), not by
        # namespace, so this pack's contribution is both its own kinds as KEYS
        # (each of which names partners) and its kinds as PARTNERS inside
        # another pack's row. Treating either half as untouched would leave one
        # of this pack's entries in place and make "nothing else moved" read as
        # false for the wrong reason.
        out["pack_refs"] = {
            k: [r for r in row if not str(r).startswith(f"{NS}:")]
            for k, row in refs.items() if not k.startswith(f"{NS}:")}
        out["pack_refs"] = {n: row for n, row in out["pack_refs"].items() if row}
    return out


def _installed_minus_portfolio() -> list[str]:
    """The catalog's installed set with THIS pack excluded — the gate a graph
    that never installed it resolves (the pack is not a starter)."""
    registry = PackRegistry(default_packs_dir())
    registry.load_all()
    return [ns for ns in registry.packs if ns != NS]


class TestExtractionWithThePackOff:
    """The acceptance's "doesn't disturb anything" half, measured on BOTH paths
    that actually run — they are not the same path, and treating them as one is
    how this check passed on evidence that covered neither of them.

    * **GATED** — a graph whose resolved installed set excludes this pack
      (`installed_namespaces=`): the pack must contribute NOTHING. Compared
      against a catalog copy with the pack physically removed, for the value
      brief, the kind-index spec and its cache key.
    * **UNGATED** — a graph with NO `:PackInstall` records gets the catalog
      union by contract (#2714 indicator 3 / #5163), so this pack IS present.
      "Unchanged" therefore cannot mean "absent": the invariant is that it ADDS
      its own entries and MODIFIES nobody else's — asserted below, per entry.
    """

    def test_gated_off_contributes_nothing(self):
        gated = compile_value_brief(
            default_packs_dir(), installed_namespaces=_installed_minus_portfolio())
        assert not [k for k in gated if k.startswith(f"{NS}:")], list(gated)

    def test_gated_off_equals_a_catalog_without_the_pack(
            self, packs_without_portfolio):
        gated = compile_value_brief(
            default_packs_dir(), installed_namespaces=_installed_minus_portfolio())
        without = compile_value_brief(packs_without_portfolio)
        assert gated == without
        # The positive control that makes the equality mean something.
        assert any(k.startswith(f"{NS}:") for k in compile_value_brief())

    def test_gated_off_kind_index_and_its_cache_key_are_unchanged(
            self, packs_without_portfolio):
        gated = compile_kind_index_spec(
            default_packs_dir(), installed_namespaces=_installed_minus_portfolio())
        without = compile_kind_index_spec(packs_without_portfolio)
        assert gated == without
        assert cache_key_for(gated) == cache_key_for(without)

    def test_the_ungated_union_adds_the_pack_and_modifies_no_other_entry(self):
        """The path a graph with no `:PackInstall` records actually takes.

        This pack IS in the union there, so the meaningful assertion is
        per-entry: stripping the pack's OWN contributions (its `ns:kind` brief
        keys and its `memory_granularity[ns]` text) leaves EXACTLY the gated
        brief / kind index — i.e. no other pack's entry, and no core entry,
        moved.
        """
        installed = _installed_minus_portfolio()
        gated_brief = compile_value_brief(
            default_packs_dir(), installed_namespaces=installed)
        ungated_brief = compile_value_brief()
        assert _strip_portfolio(ungated_brief) == gated_brief
        assert set(ungated_brief) - set(gated_brief) == {
            f"{NS}:mandate", f"{NS}:allocationTarget", f"{NS}:valuationMark",
        }
        assert NS in ungated_brief["memory_granularity"]
        assert NS not in gated_brief["memory_granularity"]

        gated_index = compile_kind_index_spec(
            default_packs_dir(), installed_namespaces=installed)
        ungated_index = compile_kind_index_spec()
        assert _strip_portfolio(ungated_index) == gated_index
        added = set(ungated_index) - set(gated_index)
        assert added == {
            f"{NS}:mandate", f"{NS}:allocationTarget", f"{NS}:valuationMark",
        }, added

    def test_the_declared_value_reaches_the_prompt(self):
        """The consumer half of #2818: a declaration no prompt reads is
        decoration. This is the UNGATED (union) render — the one a graph with no
        pack records gets."""
        master = v2._build_master_from_brief(compile_value_brief())
        assert master["pack_values"] == {
            f"{NS}:allocationTarget": [{
                "name": "targetPct",
                "valueKind": "percent",
                "required": True,
                "cardinality": "one",
            }],
        }
        assert "[values: targetPct(percent" in v2._render_master_verbose(master)
        assert "KIND VALUE FIELDS" in v2._s2s4_rules(master)
        # …and it is the ONLY kind in the catalog that renders the segment.
        assert v2._render_master_verbose(master).count("[values:") == 1


# ── 5. Each new kind is mintable on a fresh graph ─────────────────────────


class TestKindsAreMintable:
    @requires_db
    def test_the_pack_installs_on_a_fresh_graph_and_validates_clean(self, sdk):
        """The acceptance's install line, literally: activated on a fresh graph,
        then `tortoise_validate_domain` is clean for it."""
        from tortoise.pack_state import ensure_tenant_packs, get_tenant_packs

        ensure_tenant_packs(sdk, starter=["venture", NS])
        assert {p["namespace"] for p in get_tenant_packs(sdk)} == {
            "venture", NS}
        verdict = sdk.validate_domain(NS)
        assert verdict["ok"] is True, verdict
        assert verdict["violations"] == []

    @requires_db
    def test_validate_domain_has_teeth(self, sdk):
        """`tortoise_validate_domain` clean is only evidence if the domain HAS
        rules: `run_domain_graph_validators` returns `([], [])` for a known
        namespace with none registered, so a clean verdict alone would be
        vacuous. Both of this pack's rules are exercised — each fires on the
        shape it names, and the clean case is the same graph corrected.
        """
        from tortoise.domain_validators import (
            SURFACE_GRAPH,
            domain_validators,
        )

        assert domain_validators(NS, surface=SURFACE_GRAPH), (
            "the portfolio domain must register at least one graph-surface "
            "validator, or `validate_domain` is a green light over nothing")

        position = sdk.create_object("Alpha Holdings (seed position)",
                                     objectKind="venture:investment")
        # 1. an out-of-vocabulary status on a state-owning kind
        sdk.create_object("Mandate M-9", objectKind=f"{NS}:mandate",
                          status="active")
        # 2. a mark with no position behind it
        sdk.create_entity("event", "VM orphan",
                          eventKind=f"{NS}:valuationMark")

        verdict = sdk.validate_domain(NS)
        assert verdict["ok"] is False
        rules = {v["rule"] for v in verdict["violations"]}
        assert rules == {"undeclared_object_status", "orphaned_valuation_mark"}
        orphan = next(v for v in verdict["violations"]
                      if v["rule"] == "orphaned_valuation_mark")
        assert orphan["kind"] == f"{NS}:valuationMark"
        assert orphan["fix"], "a violation must name its fix"

        # The same graph, corrected, is clean — the rules are about the shape,
        # not about the kinds existing.
        sdk._get_proj().g.query(
            "MATCH (o:Object {name:'Mandate M-9'}) SET o.status = 'adopted'")
        sdk._get_proj().g.query(
            "MATCH (e:Event {name:'VM orphan'}) DETACH DELETE e")
        # TWO attached marks, one per declared write shape: `aboutObject` (the
        # structural form) and the `marks` relation as DECLARED — `mechanism:
        # IMPL` resolves to the operator path, which writes INPUT→Point→IMPL
        # and never `aboutObject`. A rule that honours only one of them forbids
        # the relation that admits the kind.
        sdk.create_entity("event", "VM-1",
                          eventKind=f"{NS}:valuationMark",
                          aboutObject=position["id"])
        vm2 = sdk.create_entity("event", "VM-2",
                                eventKind=f"{NS}:valuationMark")
        sdk.create_operator("IMPL", vm2["node"]["id"], [position["id"]],
                            label="marks", semantics="related")
        assert sdk.validate_domain(NS)["ok"] is True

    def test_an_unreadable_pack_is_named_in_the_callers_payload(
            self, monkeypatch):
        """A CLEAN verdict over ZERO graded rules must not reach the caller
        unqualified.

        The UNDECLARED-STATUS rule reads its whole vocabulary off the pack, so
        with no readable `portfolio` pack it grades ZERO rules and `ok` stays
        `True` — honestly, since there is no violation to report. (The orphan
        rule is NOT blind in that state: `_portfolio_mark_contract` falls back
        to the module constants, so it still grades.) But the
        server-side `logger.warning` is not a channel an MCP/CLI caller of
        `tortoise_validate_domain` can read, so without this the caller gets an
        unqualified green light over an EMPTY rule set — the exact failure
        `test_validate_domain_has_teeth` guards from the other side. The
        degradation is therefore carried in the advisory `drift` list, which
        `validate_domain` returns in its payload.
        """
        from tortoise import domain_loader
        from tortoise.domain_validators import run_domain_graph_validators

        class _EmptyGraph:
            """Empty result sets: with no pack nothing can be found, and a rule
            that reached the graph would still find no rows."""

            class g:  # stand-in for the FalkorDB client
                @staticmethod
                def query(*_a, **_k):
                    return type("R", (), {"result_set": []})()

        monkeypatch.setattr(domain_loader, "_get_registry", lambda: None)
        violations, drift = run_domain_graph_validators(NS, _EmptyGraph())
        assert violations == [], violations
        rules = {d["rule"] for d in drift}
        assert "portfolio_rules_unavailable" in rules, drift
        entry = next(d for d in drift
                     if d["rule"] == "portfolio_rules_unavailable")
        assert {"rule", "kind", "ref", "message", "fix"} <= set(entry), entry
        assert entry["ref"] == NS
        # It is ADVISORY: it names the degradation without inventing a
        # violation, so the verdict stays `ok` and the payload says why.
        assert run_domain_graph_validators(
            NS, _EmptyGraph(), include_drift=False)[1] == []

    @requires_db
    def test_a_mis_wired_mark_is_an_orphan(self, sdk):
        """A mark attached to the WRONG thing is as unusable as an unattached
        one: the manifest declares ONE relation (`marks` → `venture:investment`),
        so a `producedBy` operator pointing at a mandate answers a different
        question — and a rule that accepted "some IMPL operator to some Object"
        would report it clean while claiming the mark reaches a position.
        """
        sdk.create_entity("object", "Mandate M-7",
                          objectKind=f"{NS}:mandate", is_episodic=False)

        # (a) an IMPL operator with the WRONG label
        wrong_rel = sdk.create_entity("event", "VM wrong-relation",
                                      eventKind=f"{NS}:valuationMark")
        sdk.create_operator("IMPL", wrong_rel["node"]["id"],
                            [sdk._get_proj().g.query(
                                "MATCH (o:Object {name:'Mandate M-7'}) "
                                "RETURN o.id").result_set[0][0]],
                            label="producedBy", semantics="related")

        # (b) the RIGHT label on the WRONG kind of target
        wrong_target = sdk.create_entity("event", "VM wrong-target",
                                          eventKind=f"{NS}:valuationMark")
        sdk.create_operator("IMPL", wrong_target["node"]["id"],
                            [sdk._get_proj().g.query(
                                "MATCH (o:Object {name:'Mandate M-7'}) "
                                "RETURN o.id").result_set[0][0]],
                            label="marks", semantics="related")

        verdict = sdk.validate_domain(NS)
        flagged = {v["ref"] for v in verdict["violations"]
                   if v["rule"] == "orphaned_valuation_mark"}
        assert flagged == {wrong_rel["node"]["id"], wrong_target["node"]["id"]}, (
            flagged)

    @requires_db
    def test_a_superseded_mandate_is_not_an_undeclared_status(self, sdk):
        """Supersession is the ENGINE's own state-change fold
        (``commit_ops.apply_supersessions`` writes ``status='superseded'``), and
        it is how a mandate changes: a rule that only accepted the manifest's
        four words would redden `validate_domain portfolio` on every graph that
        superseded one — the pack would forbid its own write path.
        """
        from tortoise.commit_ops import OBJECT_TERMINAL_STATUSES, apply_supersessions

        sdk.create_entity("object", "Mandate M-1 — draft",
                          objectKind=f"{NS}:mandate", is_episodic=False)
        sdk.create_entity("object", "Mandate M-2",
                          objectKind=f"{NS}:mandate", is_episodic=False)
        applied = apply_supersessions(
            sdk._get_proj(), sdk,
            [{"superseded": "Mandate M-1 — draft",
              "supersedes_by": "Mandate M-2", "evidence": "board vote"}],
            session_id="s-9663")
        assert applied == 1, "the live fold must apply, or this test is vacuous"

        stored = sdk._get_proj().g.query(
            "MATCH (o:Object {name:'Mandate M-1 — draft'}) RETURN o.status"
        ).result_set
        assert stored and stored[0][0] in OBJECT_TERMINAL_STATUSES, stored

        declared = set(RAW["ontology"]["kindDefs"]["mandate"]["statuses"])
        assert "superseded" not in declared, (
            "the framework terminal vocabulary is written by the engine, not "
            "declared by the kind — declaring it would be a second definition")
        assert sdk.validate_domain(NS)["ok"] is True

    @requires_db
    def test_a_bare_spelled_kind_is_not_blind_to_the_rule(self, sdk):
        """The write gate admits BOTH spellings of a pack kind —
        ``extractor_v2._object_kind_forms`` lowercases ``portfolio:mandate`` and
        ``mandate`` — and the projection stores ``objectKind`` verbatim. A rule
        that queried only the namespaced spelling therefore reported CLEAN over
        an Object the gate had just produced: the fail-open half of the same
        problem the supersession exemption fixes on the other side.

        Both directions are pinned: the BARE spelling is now seen, and a
        NAMESPACED kind whose local part carries a capital
        (``portfolio:allocationTarget``) is still seen after the vocabulary was
        case-folded — a case-sensitive query over lowercased forms would miss
        it, which is the same blindness with a smaller blast radius.
        """
        # (a) bare spelling, out-of-vocabulary status → REPORTED
        sdk.create_object("Mandate M-11 (bare kind)", objectKind="mandate",
                          status="active")
        # (b) namespaced spelling stored verbatim with a capital, out-of-
        #     vocabulary status → REPORTED
        sdk.create_object("Allocation Target AT-2",
                          objectKind=f"{NS}:allocationTarget",
                          status="active")
        stored = dict(sdk._get_proj().g.query(
            "MATCH (o:Object) RETURN o.name, o.objectKind").result_set)
        assert stored["Mandate M-11 (bare kind)"] == "mandate", stored
        assert stored["Allocation Target AT-2"] == f"{NS}:allocationTarget", (
            stored)

        verdict = sdk.validate_domain(NS)
        flagged = {(v["rule"], v["kind"]) for v in verdict["violations"]}
        assert flagged == {
            ("undeclared_object_status", "mandate"),
            ("undeclared_object_status", f"{NS}:allocationTarget"),
        }, verdict

        # (c) the bare spelling carrying a DECLARED status is not a violation —
        #     the rule reads the kind's own vocabulary, not the prefix.
        sdk._get_proj().g.query(
            "MATCH (o:Object {name:'Mandate M-11 (bare kind)'}) "
            "SET o.status = 'adopted'")
        sdk._get_proj().g.query(
            "MATCH (o:Object {name:'Allocation Target AT-2'}) "
            "SET o.status = 'proposed'")
        assert sdk.validate_domain(NS)["ok"] is True

    @requires_db
    def test_a_pipeline_spelled_mark_is_seen_by_the_orphan_rule(self, sdk):
        """Rule 2 must read the spellings the ENGINE writes, not only the ones a
        caller types — the rule-2 analogue of
        ``test_a_bare_spelled_kind_is_not_blind_to_the_rule``.

        Two independent widenings, and both directions are pinned:

          * the extraction pipeline's own event commit STRIPS the namespace
            (``sdk._extract_session_v2`` writes ``ekind.rsplit(":", 1)[-1]``),
            so every mark minted by extraction is stored ``valuationMark``. An
            exact ``e.eventKind = 'portfolio:valuationMark'`` matched nothing the
            pipeline wrote — the unattached mark was never reported, a false
            CLEAN on the rule's own purpose;
          * the write gate admits the BARE object-kind spelling too
            (``objectKind='investment'``), and the projection stores it
            verbatim — so an exact ``(o:Object {objectKind: 'venture:investment'})``
            flagged a CORRECTLY wired mark as an orphan (a false violation).
        """
        # (a) an UNATTACHED mark under the pipeline's bare event-kind spelling
        #     → REPORTED (the false-CLEAN half).
        sdk.create_entity("event", "VM pipeline-spelled orphan",
                          eventKind="valuationMark")
        # (b) a mark wired to a BARE-spelled position → CLEAN (the false-
        #     violation half).
        bare_position = sdk.create_object("Beta Holdings (bare position kind)",
                                          objectKind="investment")
        sdk.create_entity("event", "VM-4 (bare position)",
                          eventKind="valuationMark",
                          aboutObject=bare_position["id"])

        # The graph really holds the spellings this test claims (a test that
        # silently got canonicalised kinds would prove nothing).
        marks = dict(sdk._get_proj().g.query(
            "MATCH (e:Event) RETURN e.name, e.eventKind").result_set)
        assert marks["VM pipeline-spelled orphan"] == "valuationMark", marks
        positions = dict(sdk._get_proj().g.query(
            "MATCH (o:Object) RETURN o.name, o.objectKind").result_set)
        assert positions["Beta Holdings (bare position kind)"] == "investment", (
            positions)

        orphan_id = sdk._get_proj().g.query(
            "MATCH (e:Event {name:'VM pipeline-spelled orphan'}) RETURN e.id"
        ).result_set[0][0]
        verdict = sdk.validate_domain(NS)
        flagged = {v["ref"] for v in verdict["violations"]
                   if v["rule"] == "orphaned_valuation_mark"}
        assert flagged == {orphan_id}, verdict

        # (c) attaching the pipeline-spelled mark to the bare-spelled position
        #     clears it: the rule is about the attachment, never the spelling.
        sdk._get_proj().g.query(
            "MATCH (e:Event {name:'VM pipeline-spelled orphan'}) "
            "MATCH (o:Object {name:'Beta Holdings (bare position kind)'}) "
            "CREATE (e)-[:aboutObject]->(o)")
        assert sdk.validate_domain(NS)["ok"] is True

    def test_the_mark_contract_is_pinned_to_the_manifest(self):
        """``POSITION_KIND``/``MARK_RELATION`` used to be an UNPINNED second copy
        of the manifest's ``marks`` entry while the comment claimed they were
        read off it. Both halves are now pinned: the contract the rule uses is
        read from the loaded pack, and the registry-less FALLBACK constants are
        asserted against the manifest so they cannot drift either.
        """
        from tortoise import domain_validators as dv

        marks = next(r for r in RAW["ontology"]["relations"]
                     if r["predicate"] == "marks")
        expected = (f"{NS}:valuationMark", marks["toKind"], marks["predicate"])
        assert dv._portfolio_mark_contract() == expected, (
            "the rule must read the pack's own `marks` declaration")
        assert expected == (dv.MARK_KIND, dv.POSITION_KIND, dv.MARK_RELATION), (
            "the registry-less fallback must not drift from the manifest it "
            "stands in for")

    def test_the_mark_contract_follows_a_manifest_edit(self, monkeypatch):
        """Why derivation rather than a constant: a manifest edit to ``toKind``
        or a predicate rename must move the rule WITH it, or the orphan rule
        silently flags every correctly-wired mark (or goes blind) with nothing
        red in CI.
        """
        from tortoise import domain_loader
        from tortoise import domain_validators as dv

        class _Pack:
            relations: ClassVar[list[dict]] = [{
                "predicate": "marksPosition",
                "fromKind": f"{NS}:valuationMark",
                "toKind": "venture:deal",
                "mechanism": "IMPL",
            }]

        class _Reg:
            status_fold_index = None

            def get_pack(self, namespace):
                return _Pack() if namespace == NS else None

        monkeypatch.setattr(domain_loader, "_get_registry", lambda: _Reg())
        assert dv._portfolio_mark_contract() == (
            f"{NS}:valuationMark", "venture:deal", "marksPosition")

    def test_the_admitted_kind_forms_are_widened_and_case_folded(self,
                                                                monkeypatch):
        """The vocabulary the rule queries is every spelling the write gate
        admits, lowercased — ``portfolio:allocationTarget`` keeps its capital in
        storage, so folding only ONE side would trade the bare-spelling
        blindness for a namespaced one. Read from ``StatusFoldIndex``'s own
        seam, never re-derived a third time.
        """
        from tortoise import domain_loader
        from tortoise import domain_validators as dv
        from tortoise.status_folds import StatusFoldIndex

        index = StatusFoldIndex()

        class _Reg:
            status_fold_index = index

            def get_pack(self, namespace):  # pragma: no cover — unused here
                return None

        monkeypatch.setattr(domain_loader, "_get_registry", lambda: _Reg())
        assert dv._admitted_kind_forms(f"{NS}:valuationMark") == [
            f"{NS}:valuationmark", "valuationmark"]
        # The same set the index itself returns — the seam, not a copy of it.
        assert dv._admitted_kind_forms(f"{NS}:allocationTarget") == [
            str(f).lower()
            for f in index.kind_forms_for(f"{NS}:allocationTarget")]

    @requires_db
    def test_each_object_kind_mints_with_its_own_status(self, sdk):
        sdk.create_object("Mandate M-1: no sector above 25%",
                          objectKind=f"{NS}:mandate", status="adopted")
        sdk.create_object("Allocation Target AT-1: 30% venture",
                          objectKind=f"{NS}:allocationTarget", status="proposed")

        rows = sdk._get_proj().g.query(
            "MATCH (o:Object) RETURN o.name, o.objectKind, o.status"
        ).result_set
        minted = {r[0]: (r[1], r[2]) for r in rows}
        assert minted["Mandate M-1: no sector above 25%"] == (
            f"{NS}:mandate", "adopted")
        assert minted["Allocation Target AT-1: 30% venture"] == (
            f"{NS}:allocationTarget", "proposed")

    @requires_db
    def test_the_valuation_mark_mints_as_an_event_about_the_position(self, sdk):
        position = sdk.create_object("Alpha Holdings (seed position)",
                                     objectKind="venture:investment",
                                     status="invested")
        mark = sdk.create_entity(
            "event", "Valuation Mark VM-3: 2026-Q3",
            eventKind=f"{NS}:valuationMark",
            aboutObject=position["id"])

        rows = sdk._get_proj().g.query(
            "MATCH (e:Event) RETURN e.name, e.eventKind, e.status"
        ).result_set
        assert [tuple(r) for r in rows] == [
            ("Valuation Mark VM-3: 2026-Q3", f"{NS}:valuationMark", None)
        ], rows
        assert mark["node"]  # the write returned a node, not a refusal
        # The mark is wired to the position it marks — the `marks` relation's
        # shape, written through the event door.
        linked = sdk._get_proj().g.query(
            "MATCH (e:Event {name:'Valuation Mark VM-3: 2026-Q3'})"
            "-[:aboutObject]->(o:Object) RETURN o.name").result_set
        assert [r[0] for r in linked] == ["Alpha Holdings (seed position)"]

    @requires_db
    def test_every_new_kind_is_mintable_after_the_install(self, sdk):
        """All three kinds, on the installed graph — the write gate has
        `:PackInstall` records here, so an unapproved namespace would be
        refused (#5475) rather than silently admitted."""
        from tortoise.pack_state import ensure_tenant_packs

        ensure_tenant_packs(sdk, starter=["venture", NS])
        for kind in sorted(EXPECTED_OBJECT_KINDS):
            sdk.create_object(f"{kind} 1", objectKind=f"{NS}:{kind}")
        for kind in sorted(EXPECTED_EVENT_KINDS):
            sdk.create_entity("event", f"{kind} 1", eventKind=f"{NS}:{kind}")

        rows = sdk._get_proj().g.query(
            "MATCH (o:Object) RETURN DISTINCT o.objectKind").result_set
        assert {r[0] for r in rows} == {f"{NS}:{k}" for k in EXPECTED_OBJECT_KINDS}
        rows = sdk._get_proj().g.query(
            "MATCH (e:Event) RETURN DISTINCT e.eventKind").result_set
        assert {r[0] for r in rows} == {f"{NS}:{k}" for k in EXPECTED_EVENT_KINDS}

    @requires_db
    def test_a_mandate_state_change_supersedes_rather_than_appends(self, sdk):
        """State on Objects (#2725 rule 1): a later state is a supersession, so
        "is this within mandate?" has exactly one current answer."""
        from tortoise.commit_ops import apply_supersessions

        sdk.create_object("Mandate M-1 — draft", objectKind=f"{NS}:mandate",
                          status="draft")
        sdk.create_object("Mandate M-1 — adopted", objectKind=f"{NS}:mandate",
                          status="adopted")
        applied = apply_supersessions(
            sdk._get_proj(), sdk,
            [{"superseded": "Mandate M-1 — draft",
              "supersedes_by": "Mandate M-1 — adopted",
              "evidence": "the board adopted it"}],
            session_id="sess_mandate", warn=lambda _m: None)
        assert applied == 1
        rows = sdk._get_proj().g.query(
            "MATCH (o:Object {name:'Mandate M-1 — draft'}) "
            "RETURN o.status, o.supersededBy").result_set
        assert next(iter(rows[0])) == "superseded"
