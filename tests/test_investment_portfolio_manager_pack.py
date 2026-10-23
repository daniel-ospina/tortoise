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

    def test_compact_mode_can_gate_the_namespace_by_story(self, pack):
        """The namespace has a `_PACK_TRIGGERS` row, so compact-mode story
        selection gates it instead of injecting it WHOLE into every prompt (the
        fail-open fallback `_select_pack_kinds` documents).

        Two directions, because a row alone is not a gate, and one wrong
        trigger word is worse than no row: `_select_pack_kinds` matches
        SUBSTRINGS, and a single spurious match suppresses the all-packs
        fallback — so a false-positive trigger does not add a section, it DROPS
        every other pack's kinds.
        """
        from tortoise.extractor_v2 import _PACK_TRIGGERS, _select_pack_kinds

        assert f"{NS}:" in _PACK_TRIGGERS
        assert all(isinstance(t, str) and t for t in _PACK_TRIGGERS[f"{NS}:"])
        pack_kinds = {
            f"{NS}:mandate": "m", f"{NS}:allocationTarget": "a",
            "dev:issue": "d", "venture:tranche": "t",
        }

        hit = _select_pack_kinds(
            "the board reviewed the mandate and the target weight", pack_kinds)
        assert f"{NS}:mandate" in hit
        assert "dev:issue" not in hit

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

        # Bare generic words are the OTHER half of the same trap: an overlay
        # trigger must match the mentions the pack DECLARES, and only those.
        # `mandate`/`allocation` alone are generic English ("the team's mandate
        # is to ship the CLI", "resource allocation across the fleet"), and a
        # match there strips every other pack's kinds from the prompt.
        for generic in ("the team's mandate is to ship the CLI",
                        "resource allocation across the fleet"):
            got = _select_pack_kinds(f"{generic} during this deploy", pack_kinds)
            assert not [k for k in got if k.startswith(f"{NS}:")], (generic, got)

        # …and every multi-word mention the manifest declares as a `synonym`
        # must SELECT the namespace, or the row cannot fire on the vocabulary
        # the pack ships. Single words are skipped: `mark`/`valuation` are
        # deliberately not triggers (they ride inside "marketing"/"evaluation").
        declared = {syn.lower()
                    for spec in RAW["ontology"]["kindDefs"].values()
                    for syn in (spec.get("synonyms") or [])}
        multiword = sorted(w for w in declared if " " in w and w.isascii())
        assert multiword, "the manifest must declare multi-word synonyms"
        for mention in multiword:
            got = _select_pack_kinds(
                f"the board reviewed the {mention} last week", pack_kinds)
            assert [k for k in got if k.startswith(f"{NS}:")], (mention, got)


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
    comparison below fail for the wrong reason.
    """
    out = {k: v for k, v in table.items() if not k.startswith(f"{NS}:")}
    gran = out.get("memory_granularity")
    if isinstance(gran, dict) and NS in gran:
        out["memory_granularity"] = {
            n: g for n, g in gran.items() if n != NS}
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
