"""Investment Portfolio Manager overlay pack (issue #9663) — the overlay shape,
the value test re-derived from the manifest, the clash rules, and the proof that
core extraction is unchanged with the pack OFF.

What this file pins, and why each half is separate:

1. **Overlay shape** — `depends_on: [venture]`, no rename of what `venture`
   already models. The position is `venture:investment`; this pack declares no
   `investment`/`holding` of its own and every cross-pack KIND reference is
   written namespaced. The one outcome #9663 exists to prevent is an overlay
   that gives one concept two homes, so the shape half is asserted structurally
   (kind buckets + reference spellings), not by prose.
2. **The value test** — re-derived from `manifest.yaml` on every run instead of
   restated. A kind is admitted only if it owns a closed lifecycle with declared
   folds, declares a `values` shape, is a chain step, or is an endpoint of a
   relation carrying a `mechanism`; anything else belongs in a `synonyms` entry
   on an existing kind. Each declared kind's pass is asserted exactly, and each
   REJECTED candidate is pinned against the entry or kind that absorbed it.
3. **The clash rules** — `synonyms`/`examples`/`nearMisses` are how ambiguity is
   resolved here, never a competing kind: every kindDef carries all three, the
   `nearMisses` name the genuinely confusable sibling (cross-pack targets
   namespaced, relation targets bare by contract), and no synonym collides with
   a `venture` synonym (two kinds sharing one synonym is the ambiguity this pack
   is supposed to remove).
4. **Core extraction unchanged with the pack OFF** — the actual "does not
   disturb anything" proof: the brief, the kind-index spec and its cache key
   compiled for a graph that has NOT installed the overlay are identical to the
   same three compiled from a catalog copy with the pack removed entirely. The
   positive control (installed ⇒ the vocabulary IS there) is in the same class,
   so a permanently-absent pack cannot pass this either.
5. **Wiring guards** — the `_PACK_TRIGGERS` row (compact-mode selection), the
   master list, the prompt's `[values: …]` segment, and the absence of a
   pack-declared chain (a chain outside `extractor_v2.CHAINS` reaches no prompt,
   so declaring one would be inert).
6. **The two engine gaps this pack forced** — the #2781 missing-peer gate for
   relation `nearMisses` (a pack naming a sibling pack's PREDICATE as its
   near-miss must not be isolated for it) and the `tortoise pack validate`
   false green for a pack whose directory name is not its namespace.
7. **Write doors + `validate_domain`** — the pack installs on a fresh graph,
   each new kind mints with its own status, the mark attaches to the position,
   and the domain validator is clean. DB-gated (the docker lane).

Note on the lifecycle bullet: the value test reads "owns a closed `statuses`
lifecycle WITH a declared `folds`". No kind in this pack claims that bullet —
the two Objects own a lifecycle but declare no fold (no event would drive
draft→adopted→breached→retired; inventing one to satisfy the bullet is the
mechanism-invention the value test exists to stop), and `valuationMark` is an
Event, which carries no state at all. Every kind therefore passes a DIFFERENT
bullet, asserted below.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: I001
import yaml

from tests._live_utils import LIVE_URI_SKIP_REASON
from tortoise import extractor_v2 as v2
from tortoise.kind_index import cache_key_for
from tortoise.pack_registry import (
    CORE_KINDS,
    PackRegistry,
    default_packs_dir,
)
from tortoise.pack_state import DEFAULT_STARTER_PACKS, ensure_tenant_packs
from tortoise.sdk import TortoiseSDK
from tortoise.value_extractor import (
    compile_kind_index_spec,
    compile_value_brief,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
REPO_PACKS_DIR = REPO_ROOT / "packs"
PACK_DIR = REPO_PACKS_DIR / "investment-portfolio-manager"
MANIFEST_PATH = PACK_DIR / "manifest.yaml"
RAW = yaml.safe_load(MANIFEST_PATH.read_text())
VENTURE_RAW = yaml.safe_load(
    (REPO_PACKS_DIR / "venture" / "manifest.yaml").read_text())

NS = "portfolio"
EXPECTED_OBJECT_KINDS = frozenset({"mandate", "allocationTarget"})
EXPECTED_EVENT_KINDS = frozenset({"valuationMark"})
EXPECTED_NAMESPACES = frozenset({
    "agent-ops", "dev", "marketing", "pm", "portfolio", "product-strategy",
    "venture",
})
POSITION = "venture:investment"

#: The names this pack must NOT declare: each is either the position itself
#: (a rename), a claim (a Point), or a label the value test rejects.
REJECTED_KIND_NAMES = frozenset({
    "investment", "holding", "valuation", "thesis", "view", "hypothesis",
    "benchmark", "rebalance",
})


def _pack_kind_names(raw: dict) -> set[str]:
    ont = raw.get("ontology", {}) or {}
    out: set[str] = set()
    for field in ("objectKinds", "eventKinds", "pointKinds", "documentKinds",
                  "actionKinds"):
        out.update(ont.get(field) or [])
    return out


VENTURE_KIND_NAMES = _pack_kind_names(VENTURE_RAW)


def _value_test_bullets(kind: str) -> set[str]:
    """Re-derive #9663's value test for ``kind`` from the manifest.

    Returns the subset of the four admitted bullets the manifest actually
    satisfies — nothing is restated here, so a manifest edit that adds or drops
    a declaration changes this verdict.
    """
    ont = RAW["ontology"]
    spec = (ont.get("kindDefs") or {}).get(kind) or {}
    bullets: set[str] = set()
    # (1) a closed lifecycle WITH declared folds. `folds` is required by the
    # bullet as written, so a lifecycle alone does not pass it.
    if spec.get("statuses") and spec.get("folds"):
        bullets.add("lifecycle_with_folds")
    # (2) a declared value shape.
    if spec.get("values"):
        bullets.add("values_shape")
    # (3) a step in a declared chain.
    steps = {s for c in (ont.get("chains") or []) for s in (c.get("steps") or [])}
    if kind in steps:
        bullets.add("chain_step")
    # (4) an endpoint of a declared relation carrying a mechanism. Relation
    # endpoints are NAMESPACED, so the bare kind name is compared prefixed —
    # a bare comparison would silently report no endpoint for every kind.
    full = f"{NS}:{kind}"
    for rel in ont.get("relations") or []:
        if full not in (rel.get("fromKind"), rel.get("toKind")):
            continue
        if rel.get("mechanism"):
            bullets.add("relation_endpoint")
            break
    return bullets


def _server_uri_set() -> bool:
    uri = os.environ.get("TORTOISE_DB_URI") or ""
    return uri.split("://", 1)[0] in {"docker", "redis", "rediss"} and bool(
        urlparse(uri).hostname)


requires_db = pytest.mark.skipif(not _server_uri_set(), reason=LIVE_URI_SKIP_REASON)


@pytest.fixture(scope="module")
def registry() -> PackRegistry:
    r = PackRegistry(default_packs_dir())
    n = r.load_all()
    assert n == len(EXPECTED_NAMESPACES), (
        f"expected {len(EXPECTED_NAMESPACES)} packs, got {n}: {r.errors}")
    assert not r.errors, f"whole-registry compile must be clean: {r.errors}"
    return r


@pytest.fixture(scope="module")
def pack(registry: PackRegistry):
    return registry.get_pack(NS)


# ══════════════════════════════════════════════════════════════════════════
# 1. Overlay shape — an overlay ADDS, it never renames
# ══════════════════════════════════════════════════════════════════════════

class TestOverlayShape:

    def test_pack_identity(self, pack):
        assert pack.namespace == NS
        assert pack.version == "0.1.0"
        assert pack.tier == "free"

    def test_declares_the_base_pack_as_a_dependency(self):
        """#2781: the cross-pack references below are only survivable because
        the dependency is DECLARED — that declaration is what turns an absent
        `venture` into a named gap instead of an isolation."""
        assert RAW["depends_on"] == ["venture"]

    def test_registry_compiles_clean_and_keeps_every_pack(self, registry):
        assert set(registry.packs) == set(EXPECTED_NAMESPACES)
        assert not registry.errors

    def test_kind_buckets_are_exactly_the_admitted_set(self, pack):
        assert set(pack.object_kinds) == EXPECTED_OBJECT_KINDS
        assert set(pack.event_kinds) == EXPECTED_EVENT_KINDS
        assert set(pack.point_kinds) == set()
        assert set(pack.document_kinds) == set()
        assert set(pack.action_kinds) == set()

    def test_objects_and_events_are_disjoint(self, pack):
        assert not (set(pack.object_kinds) & set(pack.event_kinds))

    def test_no_kind_renames_the_portfolio_position(self, pack):
        """The failure #9663 exists to prevent: a second home for one concept.

        FAIL-ON: the pack declares `investment`/`holding` (or any other name
        `venture` already owns) as its OWN kind, so every later reader has to
        know two spellings for the position.
        REACHABLE: `venture` really does declare the position (`investment` is
        in the sibling's objectKinds), so the intersection being empty is an
        observation about this pack, not about an empty set.
        """
        assert "investment" in VENTURE_KIND_NAMES
        assert "investment" in VENTURE_RAW["ontology"]["kindDefs"]
        own = _pack_kind_names(RAW)
        assert not (own & VENTURE_KIND_NAMES), own & VENTURE_KIND_NAMES
        assert not (own & REJECTED_KIND_NAMES), own & REJECTED_KIND_NAMES

    def test_every_cross_pack_kind_reference_is_namespaced(self):
        """`venture:investment`, never a bare `investment` — the issue's lever 1.

        FAIL-ON: a cross-pack nearMiss is written bare, which reads as a
        reference to this pack's own vocabulary (or, worse, invites re-declaring
        the kind so the bare name resolves).
        """
        refs = []
        for kind, spec in (RAW["ontology"].get("kindDefs") or {}).items():
            refs.extend(spec.get("nearMisses") or [])
        cross = [r for r in refs if r.split(":", 1)[-1] in VENTURE_KIND_NAMES]
        assert cross, "fixture: at least one cross-pack nearMiss must exist"
        # A bare name that happens to BE a venture kind is the failure; a
        # `venture:`-prefixed one is the contract.
        assert [r for r in cross if ":" not in r] == [], (
            "cross-pack kind references must be namespaced: "
            f"{[r for r in cross if ':' not in r]}")
        assert set(cross) == {
            "venture:condition", "venture:investment", "venture:disbursement",
        }

    def test_relation_endpoints_point_at_this_pack_or_the_declared_base(
            self, registry):
        """Every endpoint RESOLVES, and the cross-pack ones resolve into the
        pack named in `depends_on` (not into a third pack by accident)."""
        local = _pack_kind_names(RAW)
        for rel in RAW["ontology"]["relations"]:
            for side in ("fromKind", "toKind"):
                ref = rel[side]
                assert ref.startswith(f"{NS}:") or ref.startswith("venture:"), ref
                if ref.startswith(f"{NS}:"):
                    assert ref.split(":", 1)[1] in local, ref
        # zero errors == every ref resolved (the registry's own verdict)
        assert not registry.errors

    def test_the_pack_is_not_a_starter(self):
        """An overlay that auto-installs would put its vocabulary into every
        graph's prompt — the choice `venture` also makes."""
        assert NS not in DEFAULT_STARTER_PACKS

    def test_no_chain_is_declared(self):
        """A pack-declared chain outside `extractor_v2.CHAINS` reaches no
        prompt (`master["chains"]` is built from that constant on every path),
        so declaring one would be inert. The absence is the decision, pinned so
        a later "add the flow" edit has to face `CHAINS` first."""
        assert (RAW["ontology"].get("chains") or []) == []
        assert "portfolioReviewFlow" not in v2.CHAINS


# ══════════════════════════════════════════════════════════════════════════
# 2. The value test — re-derived from the manifest, per kind
# ══════════════════════════════════════════════════════════════════════════

class TestValueTest:

    def test_every_declared_kind_passes_at_least_one_bullet(self):
        """No kind rides in on prose. A kind that fails all four belongs in a
        `synonyms` entry instead."""
        for kind in _pack_kind_names(RAW):
            bullets = _value_test_bullets(kind)
            assert bullets, f"kind '{kind}' passes none of the four bullets"

    def test_each_kind_passes_exactly_the_bullets_it_earns(self):
        assert _value_test_bullets("mandate") == {"relation_endpoint"}
        assert _value_test_bullets("allocationTarget") == {
            "values_shape", "relation_endpoint"}
        assert _value_test_bullets("valuationMark") == {"relation_endpoint"}

    def test_the_lifecycle_bullet_is_claimed_by_nobody(self):
        """Both Objects own a lifecycle but declare no `folds`, and the mark is
        an Event (stateless). Pinned so a future `folds` addition is a decision
        recorded here, not a silent one — and so the test does not pretend the
        lifecycle bullet carried a kind it did not."""
        for kind in _pack_kind_names(RAW):
            assert _value_test_bullets(kind) != {"lifecycle_with_folds"}, kind
            assert "lifecycle_with_folds" not in _value_test_bullets(kind), kind
        for kind in EXPECTED_OBJECT_KINDS:
            assert RAW["ontology"]["kindDefs"][kind]["statuses"]
            assert not RAW["ontology"]["kindDefs"][kind].get("folds")

    def test_the_value_shape_is_the_percent_the_issue_asked_for(self):
        values = RAW["ontology"]["kindDefs"]["allocationTarget"]["values"]
        assert values == [{"name": "targetPct", "valueKind": "percent"}]

    def test_rejected_candidates_are_not_declared(self):
        own = _pack_kind_names(RAW)
        assert not (own & REJECTED_KIND_NAMES)

    def test_each_rejection_names_what_absorbed_it(self):
        """The acceptance half: every rejected candidate says which `synonyms`
        entry (or existing kind) absorbed it — and the absorption is REAL, not
        a promise in a comment."""
        header = MANIFEST_PATH.read_text()
        # `benchmark` → a synonym on allocationTarget (a benchmark weight IS a
        # target the allocation is measured against).
        assert "benchmark" in header
        assert "benchmark weight" in RAW["ontology"]["kindDefs"][
            "allocationTarget"]["synonyms"]
        # `rebalance` → `venture:actionItem` (a unit of work with an owner and
        # a date). No new kind, and no synonym grafted onto a sibling's kind.
        assert "rebalance" in header
        assert "actionItem" in VENTURE_RAW["ontology"]["objectKinds"]
        # `thesis` / `view` / `hypothesis` → the core `statement` Point.
        assert "thesis" in header
        assert "statement" in CORE_KINDS
        # `valuation` (as an Object) → the `valuationMark` EVENT + the position.
        assert "valuationMark" in own_of(RAW)
        assert "valuation" not in own_of(RAW)

    def test_the_header_records_all_four_bullets(self):
        header = MANIFEST_PATH.read_text()
        for phrase in ("owns a closed `statuses` lifecycle",
                       "declares a `values` shape",
                       "is a step in a declared `chains` path",
                       "is an endpoint of a declared `relations` entry"):
            assert phrase in header, phrase


def own_of(raw: dict) -> set[str]:
    return _pack_kind_names(raw)


# ══════════════════════════════════════════════════════════════════════════
# 3. The clash rules — ambiguity is resolved in the manifest, not by a new kind
# ══════════════════════════════════════════════════════════════════════════

class TestClashRules:

    def test_every_kind_carries_synonyms_examples_and_near_misses(self):
        """`synonyms`/`examples` are what the classifier reads; `nearMisses` is
        what the rerank reads. A kind missing any of the three leans on a
        competing kind being declared instead — which is the thing this pack
        must not do."""
        for kind, spec in RAW["ontology"]["kindDefs"].items():
            for field in ("synonyms", "examples", "nearMisses"):
                assert spec.get(field), f"kindDefs '{kind}' has no {field}"

    def test_the_near_misses_name_the_confusable_sibling(self):
        kd = RAW["ontology"]["kindDefs"]
        assert "allocationTarget" in kd["mandate"]["nearMisses"]
        assert "venture:condition" in kd["mandate"]["nearMisses"]
        assert POSITION in kd["allocationTarget"]["nearMisses"]
        assert "mandate" in kd["allocationTarget"]["nearMisses"]
        assert POSITION in kd["valuationMark"]["nearMisses"]
        assert "venture:disbursement" in kd["valuationMark"]["nearMisses"]

    def test_relation_near_misses_are_bare_and_resolved(self):
        """Predicates are declared and matched BARE by contract (#2030), so a
        relation near-miss target is bare too — a namespaced one would be a
        typo, not a cross-pack gap. Both halves are asserted on the real
        catalog: bare here, and resolved through `depends_on` when the sibling
        is loaded (`registry.errors` is empty)."""
        targets = []
        for rel in RAW["ontology"]["relations"]:
            for t in rel.get("nearMisses") or []:
                assert ":" not in t, t
                targets.append(t)
        assert "gatedBy" in targets          # venture's predicate, bare
        # Every relation declares its near-misses (the clash surface exists).
        for rel in RAW["ontology"]["relations"]:
            assert rel.get("nearMisses"), rel.get("predicate")
            assert rel.get("mechanism") == "IMPL", rel.get("predicate")
            assert rel.get("semantics"), rel.get("predicate")

    def test_no_synonym_collides_with_a_venture_synonym(self):
        """One synonym on two kinds is exactly the ambiguity an overlay must
        remove: the classifier would face two candidates for one phrase. The
        pack is disjoint from the base pack's synonym surface — checked in both
        directions, case-insensitively."""
        mine = {s.lower() for spec in RAW["ontology"]["kindDefs"].values()
                for s in (spec.get("synonyms") or [])}
        theirs = {s.lower() for spec in VENTURE_RAW["ontology"]["kindDefs"].values()
                  for s in (spec.get("synonyms") or [])}
        assert mine and theirs, "fixture: both packs must declare synonyms"
        assert mine & theirs == set(), sorted(mine & theirs)

    def test_no_synonym_is_another_kind_s_bare_name(self):
        """A synonym that is literally a declared kind name ('investment') is a
        rename wearing a synonym's clothes."""
        mine = {s.lower() for spec in RAW["ontology"]["kindDefs"].values()
                for s in (spec.get("synonyms") or [])}
        assert not (mine & {k.lower() for k in VENTURE_KIND_NAMES})
        assert not (mine & {k.lower() for k in _pack_kind_names(RAW)})

    def test_the_brazilian_vocabulary_is_carried(self):
        """`venture` carries pt-BR synonyms (`investida`, `edital`, …) and the
        overlay follows the same convention — the domain is Brazilian."""
        for kind in EXPECTED_OBJECT_KINDS:
            syn = " ".join(RAW["ontology"]["kindDefs"][kind]["synonyms"]).lower()
            assert any(word in syn for word in
                       ("mandato", "política", "alocação", "peso-alvo",
                        "meta de alocação")), kind
        mark = " ".join(
            RAW["ontology"]["kindDefs"]["valuationMark"]["synonyms"]).lower()
        assert "marcação" in mark or "avaliação" in mark


# ══════════════════════════════════════════════════════════════════════════
# 4. Core extraction unchanged with the pack OFF (the "doesn't disturb" proof)
# ══════════════════════════════════════════════════════════════════════════

@pytest.fixture
def catalog_without_the_pack(tmp_path) -> Path:
    """A full copy of the real catalog with ONLY this pack removed — the
    control arm: everything else identical, so a difference cannot come from a
    different catalogue."""
    dest = tmp_path / "packs"
    shutil.copytree(REPO_PACKS_DIR, dest)
    shutil.rmtree(dest / PACK_DIR.name)
    assert not (dest / PACK_DIR.name).exists()
    return dest


class TestExtractionWithThePackOff:

    def test_the_brief_is_byte_identical_when_the_pack_is_not_installed(
            self, catalog_without_the_pack):
        """A graph that has not installed the overlay gets the SAME brief as a
        catalog that does not contain it at all.

        FAIL-ON: the overlay's kinds leak into the gated brief of a graph that
        never installed it (a gate that only filters on the caller's side, or a
        compile that reads the catalog regardless of `installed_namespaces`).
        REACHABLE: the same compile WITH the namespace installed differs, and
        differs exactly by this pack's kinds — so the equality is not a
        comparison of two empty things.
        """
        gated = compile_value_brief(
            REPO_PACKS_DIR,
            installed_namespaces=frozenset(EXPECTED_NAMESPACES) - {NS})
        control = compile_value_brief(catalog_without_the_pack)
        assert gated == control
        assert {k for k in control if k.startswith(f"{NS}:")} == set()

        installed = compile_value_brief(
            REPO_PACKS_DIR,
            installed_namespaces=frozenset(EXPECTED_NAMESPACES))
        assert {k for k in installed if k.startswith(f"{NS}:")} == {
            f"{NS}:mandate", f"{NS}:allocationTarget", f"{NS}:valuationMark"}
        assert set(installed) - set(control) == {
            f"{NS}:mandate", f"{NS}:allocationTarget", f"{NS}:valuationMark"}

    def test_the_kind_index_spec_and_its_cache_key_are_unchanged_when_off(
            self, catalog_without_the_pack):
        """The classifier's candidate set is what the gate protects, and
        `cache_key_for` is its content hash — so an equal key is the strongest
        statement that the pack changed nothing for a graph that did not install
        it (no silent re-embed of the whole index).

        FAIL-ON: a declared value field / kind reaches the index text (the
        #2818 hazard) so the cache key rotates on install for EVERY graph.
        REACHABLE: the installed spec's key differs, so an unchanged key is a
        real observation.
        """
        gated = compile_kind_index_spec(
            REPO_PACKS_DIR,
            installed_namespaces=frozenset(EXPECTED_NAMESPACES) - {NS})
        control = compile_kind_index_spec(catalog_without_the_pack)
        assert gated == control
        assert cache_key_for(gated) == cache_key_for(control)
        assert f"{NS}:mandate" not in gated

        installed = compile_kind_index_spec(
            REPO_PACKS_DIR,
            installed_namespaces=frozenset(EXPECTED_NAMESPACES))
        assert f"{NS}:mandate" in installed
        assert cache_key_for(installed) != cache_key_for(control)

    def test_the_declared_value_is_invisible_to_the_kind_index(self):
        """#2818: `values` is deliberately invisible to the classification
        index (the spec is built from description/synonyms/examples/nearMisses
        only), so a declaration cannot rotate the npz cache key by itself.

        FAIL-ON: a `values` key reaches the spec entry, which `cache_key_for`
        hashes — every install of this pack would re-embed the whole index.
        REACHABLE: the entry IS present and carries the prose/synonyms that the
        classifier uses, so the absent key is a real absence, not an empty
        entry.
        """
        spec = compile_kind_index_spec(
            REPO_PACKS_DIR, installed_namespaces=frozenset({NS, "venture"}))
        entry = spec[f"{NS}:allocationTarget"]
        assert set(entry) == {
            "text", "section", "description", "synonyms", "examples",
            "nearMisses"}, set(entry)
        assert "allocation target" in entry["text"].lower()


# ══════════════════════════════════════════════════════════════════════════
# 5. Wiring guards
# ══════════════════════════════════════════════════════════════════════════

class TestWiring:

    def test_compact_mode_can_select_the_namespace(self):
        """#5165: a shipped catalog namespace with no `_PACK_TRIGGERS` entry is
        injected WHOLE in compact mode (fail-open). The row is what lets story
        selection gate it — and it must fire on the pack's own words."""
        assert f"{NS}:" in v2._PACK_TRIGGERS
        triggers = v2._PACK_TRIGGERS[f"{NS}:"]
        assert triggers and all(isinstance(t, str) and t for t in triggers)

        pack_kinds = {"portfolio:mandate": "The constraints holdings respect",
                      "venture:investment": "The fund's stake"}
        # A portfolio-MANAGER story selects the overlay and NOT the base: the
        # two namespaces have their own trigger rows, which is the point.
        assert set(v2._select_pack_kinds(
            "the mandate is breached: our allocation is off-target",
            pack_kinds)) == {"portfolio:mandate"}
        # A base-pack story selects the base on its own words.
        assert set(v2._select_pack_kinds(
            "the fund's stake in the grant tranche", pack_kinds)) == {
                "venture:investment"}
        # …and a story carrying both selects both.
        assert set(v2._select_pack_kinds(
            "the mandate caps the tranche", pack_kinds)) == {
                "portfolio:mandate", "venture:investment"}
        assert set(v2._select_pack_kinds(
            "we shipped the ticket fix", pack_kinds)) == set(pack_kinds), (
            "an unrelated story must fall OPEN (all kinds), never to a subset")

    def test_the_kinds_reach_the_master_list_and_its_forms(self):
        master = v2.build_master_list()
        expected = {f"{NS}:{k}" for k in
                    EXPECTED_OBJECT_KINDS | EXPECTED_EVENT_KINDS}
        assert expected <= set(master["pack_kinds"])
        assert expected <= v2.master_kind_forms(master)

    def test_the_declared_value_reaches_the_prompt_when_it_IS_installed(self):
        """The declaration is inert until installed — and then it is rendered
        exactly once, as `[values: targetPct(percent)]`, with the policy block
        that tells the model where to put the value."""
        brief = compile_value_brief(
            REPO_PACKS_DIR, installed_namespaces=frozenset({NS, "venture"}))
        master = v2._build_master_from_brief(brief)
        assert set(master["pack_values"]) == {f"{NS}:allocationTarget"}
        rendered = v2._render_master_verbose(master)
        assert f"{NS}:allocationTarget" in rendered
        assert "[values: targetPct(percent)]" in rendered
        rules = v2._s2s4_rules(master)
        assert "KIND VALUE FIELDS" in rules
        # POLICY, never vocabulary (the rule must not name the kind or field).
        assert "allocationTarget" not in rules and "targetPct" not in rules

    def test_the_overlay_contributes_no_chain_completeness_noise(self):
        """Portfolio review is normal without a mark, and a mandate without a
        target: the completeness contract ('first emitted step requires the
        next') would fire on ordinary captures, so this pack declares no chain
        and must not appear in the notes."""
        notes = v2.validate_chain_completeness({
            "entities": [{"kind": "portfolio:mandate"},
                         {"kind": "portfolio:allocationTarget"},
                         {"kind": "portfolio:valuationMark"}]})
        assert [n for n in notes if n["chain"].startswith(NS)] == []

    def test_dropping_venture_does_not_drop_the_overlay(self):
        """#2781 at the catalog level for THIS pack: with the base pack absent
        the overlay's manifest is still valid, so it stays loaded and every
        reference into `venture` is REPORTED by name."""
        r = PackRegistry(REPO_PACKS_DIR)
        # Simulate the absent base pack by loading a copy that lacks it.
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "packs"
            shutil.copytree(REPO_PACKS_DIR, dest)
            shutil.rmtree(dest / "venture")
            r = PackRegistry(dest)
            n = r.load_all()
        assert n == len(EXPECTED_NAMESPACES) - 1
        assert NS in r.packs, "the overlay must stay loaded without its base"
        assert not r.errors, r.errors
        gaps = r.unresolved_refs.get(NS) or []
        assert gaps, "the missing base pack must be reported against this pack"
        assert any("depends_on" in g and "'venture'" in g for g in gaps), gaps


# ══════════════════════════════════════════════════════════════════════════
# 6. The two engine gaps this pack forced
# ══════════════════════════════════════════════════════════════════════════

def _manifest(**over) -> dict:
    raw = {
        "namespace": "dev",
        "name": "Development",
        "ontology": {"extends": "core", "objectKinds": ["issue"]},
    }
    raw.update(over)
    return raw


def _write_pack(root: Path, raw: dict) -> None:
    d = root / raw["namespace"]
    d.mkdir(parents=True, exist_ok=True)
    (d / "manifest.yaml").write_text(yaml.dump(raw))


class TestRelationNearMissesGoThroughTheMissingPeerGate:
    """#9663: relation `nearMisses` was the ONE cross-pack site that never got
    the #2781 missing-peer gate. A pack naming a SIBLING pack's predicate as its
    near-miss (the documented, cheap way to disambiguate vocabulary) was
    isolated when that sibling was not installed — measured on the real pack:
    dropping `venture` produced 2 fatal errors and dropped `portfolio`."""

    def test_a_sibling_pack_s_predicate_is_reported_not_fatal(self, tmp_path):
        root = tmp_path / "packs"
        _write_pack(root, _manifest(
            depends_on=["venture"],
            ontology={
                "extends": "core",
                "objectKinds": ["issue"],
                "relations": [{
                    "predicate": "addresses",
                    "fromKind": "dev:issue",
                    "toKind": "dev:issue",
                    "mechanism": "IMPL",
                    # `gatedBy` is declared by the (absent) sibling — bare, as
                    # predicates are matched by contract.
                    "nearMisses": ["gatedBy"],
                }],
            },
        ))
        r = PackRegistry(root)
        assert r.load_all() == 1, "a missing PEER must not isolate the pack"
        assert not r.errors, r.errors
        gaps = r.unresolved_refs.get("dev") or []
        assert any("nearMisses target 'gatedBy'" in g for g in gaps), gaps

    def test_a_typo_stays_fatal(self, tmp_path):
        """The gate must not weaken the typo guard: with a COMPLETE dependency
        set (none declared), an unresolvable target is still an isolation."""
        root = tmp_path / "packs"
        _write_pack(root, _manifest(
            ontology={
                "extends": "core",
                "objectKinds": ["issue"],
                "relations": [{
                    "predicate": "addresses",
                    "fromKind": "dev:issue",
                    "toKind": "dev:issue",
                    "mechanism": "IMPL",
                    "nearMisses": ["noSuchPredicate"],
                }],
            },
        ))
        r = PackRegistry(root)
        assert r.load_all() == 0
        assert any("nearMisses target 'noSuchPredicate'" in e
                   for e in r.errors.get("dev", [])), r.errors

    def test_the_message_names_the_predicate_universe(self, tmp_path):
        """A predicate near-miss must not report itself as a missing KIND —
        the diagnostic says what it looked for."""
        root = tmp_path / "packs"
        _write_pack(root, _manifest(
            ontology={
                "extends": "core",
                "objectKinds": ["issue"],
                "relations": [{
                    "predicate": "addresses",
                    "fromKind": "dev:issue",
                    "toKind": "dev:issue",
                    "mechanism": "IMPL",
                    "nearMisses": ["noSuchPredicate"],
                }],
            },
        ))
        r = PackRegistry(root)
        r.load_all()
        msg = " ".join(r.errors.get("dev", []))
        assert "any declared predicate or semantics value" in msg, msg
        assert "any known kind" not in msg, msg


class TestPackValidateCLI:
    """The acceptance command, and the FALSE GREEN #9663 exposed in it."""

    def _run(self, *argv: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "tortoise", "pack", "validate", *argv],
            capture_output=True, text=True, cwd=str(REPO_ROOT), timeout=180)

    def test_the_pack_validates_clean(self):
        r = self._run(str(PACK_DIR))
        out = r.stdout + r.stderr
        assert r.returncode == 0, out
        assert f"✓ {NS}" in out
        assert "Clean" in out
        # #2766: every relation's write path is reported, not assumed.
        assert f"{NS}.constrainedBy" in out
        assert f"{NS}.boundedBy" in out
        assert f"{NS}.marks" in out

    def test_json_carries_the_relation_verdicts(self):
        import json
        r = self._run(str(PACK_DIR), "--json")
        assert r.returncode == 0, r.stderr
        payload = json.loads(r.stdout)
        assert payload["ok"] is True
        assert NS in payload["loaded"]
        mine = [x for x in payload["relation_verdicts"]["relations"]
                if x["pack"] == NS]
        assert {x["predicate"] for x in mine} == {
            "constrainedBy", "boundedBy", "marks"}
        assert all(x["write_verdict"] == "writable" for x in mine), mine

    def test_a_broken_pack_whose_dir_is_not_its_namespace_is_not_a_false_green(
            self, tmp_path):
        """⭐ The fail-open gate: `tortoise pack validate` filtered the
        registry's errors by `ns == <directory name>`, but the registry keys
        them by the MANIFEST namespace — so for every pack whose directory name
        differs (`packs/project-management` → `pm`,
        `packs/investment-portfolio-manager` → `portfolio`) the command printed
        `✓ Clean` and exited 0 while the pack had been ISOLATED.

        FAIL-ON: the filter compares the directory name only — the measured
        pre-fix output for this tree was `1 pack(s) failed validation and were
        isolated: portfolio` followed by `✓ Clean — pack(s) validate`, exit 0.
        REACHABLE: the same tree prints `✗ portfolio:` and exits 1 once the
        declared namespace is matched, and the CONTROL run on the clean pack in
        the same tree exits 0.
        """
        dest = tmp_path / "packs"
        shutil.copytree(REPO_PACKS_DIR, dest)
        target = dest / PACK_DIR.name
        broken = (target / "manifest.yaml").read_text().replace(
            "      - venture:condition", "      - venture:conditon")
        (target / "manifest.yaml").write_text(broken)

        r = self._run(str(target))
        out = r.stdout + r.stderr
        assert r.returncode == 1, out
        assert f"✗ {NS}" in out, out
        assert "Clean" not in out
        assert "venture:conditon" in out

        # CONTROL: the untouched pack in the same tree still validates clean,
        # so the failure above is the injected typo and not a broken command.
        shutil.copyfile(MANIFEST_PATH, target / "manifest.yaml")
        ok = self._run(str(target))
        assert ok.returncode == 0, ok.stdout + ok.stderr


# ══════════════════════════════════════════════════════════════════════════
# 7. Write doors: install on a fresh graph, mint every kind, validate_domain
# ══════════════════════════════════════════════════════════════════════════

@pytest.fixture
def sdk(tmp_path, force_sparse_tfidf):
    s = TortoiseSDK(db_path=str(tmp_path / "portfolio.db"),
                    namespace=f"test_portfolio_{os.urandom(4).hex()}")
    try:
        yield s
    finally:
        s.close()


@requires_db
class TestInstallAndMint:

    def test_the_pack_installs_on_a_fresh_graph_and_validates_clean(self, sdk):
        records = ensure_tenant_packs(sdk, starter=["venture", NS])
        assert {r["namespace"] for r in records} == {"venture", NS}

        pos = sdk.create_object("Alpha Holdings", objectKind=POSITION)
        mandate = sdk.create_object(
            "Mandate M-1: no single position above 20% of committed capital",
            objectKind=f"{NS}:mandate", status="adopted")
        target = sdk.create_object(
            "Allocation Target AT-1: 15% in agritech",
            objectKind=f"{NS}:allocationTarget", status="adopted",
            targetPct=1500)
        mark = sdk.create_event(
            "Valuation Mark VM-3: Alpha Holdings at 2.1x cost, 2026-06-30",
            f"{NS}:valuationMark", aboutObject=pos["id"])

        kinds = {r[0] for r in sdk._get_proj().g.query(
            "MATCH (n) WHERE n.objectKind IS NOT NULL "
            "RETURN n.objectKind").result_set}
        assert {f"{NS}:mandate", f"{NS}:allocationTarget", POSITION} <= kinds
        event_kinds = {r[0] for r in sdk._get_proj().g.query(
            "MATCH (e:Event) RETURN e.eventKind").result_set}
        assert f"{NS}:valuationMark" in event_kinds

        # The lifecycle the manifest declares is the one that lands.
        status = sdk._get_proj().g.query(
            "MATCH (o:Object {id:$id}) RETURN o.status",
            params={"id": mandate["id"]}).result_set[0][0]
        assert status == "adopted"
        assert target and mark

        report = sdk.validate_domain(NS)
        assert report["domain"] == NS
        assert report["ok"] is True, report
        assert report["violations"] == []
