"""#2714 — vocab gating by graph (layer 1 APPROVAL): the mechanical tests.

**Class B** per the lane's test doctrine: the decision is already made and
stated as testable indicators, so the tests are written FIRST and assert the
CONTRACT the indicators name — not the code that happens to implement it.

The doctrine requires every mechanical test to answer two questions. They are
answered inline per test as ``FAIL-ON`` and ``REACHABLE``. Read them as: *what
value makes this fail, and does the fixture actually contain a row where that
value is reachable?* An assert on a fixture with no row of the kind under test
is true for every wrong implementation.

Indicators under test (from #2714):
  1. with N packs installed, the extractor prompt and the Layer-1 write gate
     expose exactly that graph's namespaced kinds (+ core + the back-compat
     union of kinds already present in the graph);
  2. a namespaced kind from a NON-installed pack is rejected at write — unless
     the graph's own data already uses it;
  3. existing graphs/tests are unaffected — the union fallback applies where
     activation records are ABSENT (this is the one that bites: a graph with
     no ``:PackInstall`` records must behave exactly as it does today).

The two layers (owner, 2026-09-09) are deliberately not conflated here: this
file tests **APPROVAL** (which packs a graph allows). It tests no
CLASSIFICATION behaviour and adds no LLM hop — content-domain auto-detection
is explicitly NOT wanted (research: no agent-memory/KG product does it).
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")
os.environ.setdefault("RATE_LIMIT_DISABLED", "1")

import pytest  # noqa: I001

from tortoise.commit_schema import (
    CORE_POINT_KINDS,
    CORE_SOURCE_KINDS,
    EVENT_KINDS,
    compile_vocab,
    validate_payload_dict,
)
from tortoise.extractor_v2 import (
    CORE_OBJECT_KEYS,
    _build_master_from_brief,
    build_master_list,
    master_kind_forms,
)
from tortoise.pack_state import (
    _KIND_PROP_KEYS,
    graph_installed_namespaces,
    graph_kind_namespaces,
)
from tortoise.sdk import TortoiseSDK
from tortoise.value_extractor import compile_value_brief

# The payload factories are shared, not re-invented (the #3977 lesson: a
# second copy of a fixture drifts from the first). This is a pure import of
# module-level helpers — `tests/test_commit_schema.py` writes no env at import.
from tests.test_commit_schema import _finalize, _point, _raw_payload

DEV = "dev"
MARKETING = "marketing"

#: A real kindDef'd kind per pack, so the asserts are about NAMESPACES and not
#: about a declare-vs-kindDef distinction.
DEV_POINT = "dev:requirement"
MARKETING_POINT = "marketing:contentBrief"
MARKETING_OBJECT = "marketing:campaign"

#: The #1935 tenant fixture shape (declared objectKind, no kindDef) — the
#: tenant overlay leg of the gate.
TENANT_MANIFEST = """namespace: orphan-ops
name: Orphan Operations
version: 0.1.0
tier: free
ontology:
  extends: core
  objectKinds:
  - contract
  memory_granularity: 'Durable: contract terms.'
"""


#: #5165: a SIXTH CATALOG pack whose namespace is deliberately OUTSIDE the
#: legacy hardcoded starter tuple (``PACK_NS`` in the pre-fix engine). The
#: catalog case is the one the tenant path could never reproduce: a catalog
#: pack has no ``:PackManifest`` node, so it got no ``tenant_prefixes`` entry
#: either — its kinds rode the gated brief and were dropped from the master.
VENTURE_MANIFEST = """namespace: venture
name: Venture
version: 0.1.0
tier: free
ontology:
  extends: core
  objectKinds:
  - tranche
  pointKinds:
  - thesis
  kindDefs:
    tranche:
      description: A financing tranche in a round
    thesis:
      description: An investment thesis
  memory_granularity: 'Durable: tranche terms.'
"""

VENTURE_TRANCHE = "venture:tranche"
VENTURE_THESIS = "venture:thesis"


def _brief_namespaces(brief: dict) -> set[str]:
    """The pack namespaces a compiled brief exposes (core excluded)."""
    return {k.split(":", 1)[0] for k in brief
            if ":" in k and not k.startswith("core:")}


def _master_namespaces(master: dict) -> set[str]:
    """The pack namespaces a master list exposes as pack_kinds."""
    return {k.split(":", 1)[0] for k in master.get("pack_kinds", {})}


def _dev_manifest_ontology() -> dict:
    """The dev pack's ``ontology:`` block — the ORACLE for kind-level checks.

    Read from the manifest (the compile INPUT), never from either compile
    output: an oracle that agrees with the implementation by construction
    cannot catch the implementation drifting from the decision.
    """
    import yaml

    from tortoise.pack_registry import default_packs_dir
    data = yaml.safe_load(
        (Path(default_packs_dir()) / DEV / "manifest.yaml").read_text())
    return data["ontology"]


@pytest.fixture
def venture_catalog(tmp_path, monkeypatch):
    """A hermetic default packs dir holding ONLY the venture pack (#5165).

    The default-packs-dir resolution is monkeypatched at its ONE primitive
    (``pack_registry.default_packs_dir`` — resolved by ``compile_value_brief``
    at call time), so the catalog's namespace set is exactly ``{venture}``:
    a namespace the pre-fix engine's hardcoded tuple does not contain. The
    #1350 process-global master memo is reset for the test and restored by
    monkeypatch.
    """
    from tortoise import extractor_v2

    packs_dir = tmp_path / "packs"
    (packs_dir / "venture").mkdir(parents=True)
    (packs_dir / "venture" / "manifest.yaml").write_text(VENTURE_MANIFEST)
    monkeypatch.setattr("tortoise.pack_registry.default_packs_dir",
                        lambda *a, **k: packs_dir)
    monkeypatch.setattr(extractor_v2, "_MASTER_LIST_CACHE", None)
    return packs_dir


def _seed_install(sdk, namespace: str, *, status: str = "active",
                  source: str = "starter") -> None:
    """Write ONE ``:PackInstall`` activation record directly (pack_state's own
    write path is `ensure_tenant_packs`, which activates the whole starter
    set — this test needs exactly one namespace, and the removal status)."""
    sdk._get_proj().g.query(
        "MERGE (p:PackInstall {namespace: $ns}) "
        "SET p.version = '0.0.0', p.status = $st, p.source = $src",
        params={"ns": namespace, "st": status, "src": source},
    )


def _read_install_namespaces(sdk) -> list[str]:
    rows = sdk._get_proj().g.query(
        "MATCH (p:PackInstall) RETURN p.namespace ORDER BY p.namespace",
    ).result_set
    return [r[0] for r in rows]


@pytest.fixture
def sdk(tmp_path):
    """A dedicated embedded graph per test (its own namespace ⇒ its own graph).

    Never a shared graph name: this file is about per-graph state, so a shared
    graph would make the isolation assertions unfalsifiable. (No literal
    guard is in play either way: the value is ``test_``-prefixed, which the
    namespace guard skips, and this fixture makes no ``select_graph`` call,
    which is all the select_graph guard looks at.)"""
    return TortoiseSDK(db_path=str(tmp_path / "l7-vocab-gating.db"),
                       namespace=f"test_l7_vocab_{uuid.uuid4().hex[:8]}")


# ══════════════════════════════════════════════════════════════════════════
# A. compile_value_brief — the PROMPT-side seam (pure; no graph)
# ══════════════════════════════════════════════════════════════════════════

class TestCompileValueBriefGating:

    def test_ungated_brief_is_still_the_catalog_union(self):
        """The default path is untouched (indicator 3, at the seam).

        FAIL-ON: the gate leaks into the default path — e.g. `None` starts
        meaning "no packs" — which would narrow the brief to core.
        REACHABLE: the real ``packs/`` catalog holds 5 namespaced packs, so
        ``_pack_namespaces`` is non-trivial and a narrowing is visible.
        """
        brief = compile_value_brief()
        assert _brief_namespaces(brief) == {
            DEV, MARKETING, "product-strategy", "pm", "agent-ops",
        }, "the default brief must still compile the whole catalog"

    def test_explicit_none_is_byte_identical_to_the_default(self):
        """``installed_namespaces=None`` means NO GATE, not an empty gate.

        FAIL-ON: ``None`` starts behaving like the empty set (narrowing every
        existing caller to core), or a default-argument change (e.g. ``[]``
        instead of ``None``) silently gates them.
        REACHABLE: three real compiles — the ungated brief carries the real
        catalog's 5 namespaces, and the empty-gate brief is genuinely
        narrower, so the equality is distinguished from a trivial one. The
        KEY ORDER is asserted too: the brief's order is prompt-visible
        downstream (extractor_v2's pack_kinds keeps the brief's insertion
        order).
        """
        default = compile_value_brief()
        explicit = compile_value_brief(installed_namespaces=None)
        assert list(explicit) == list(default)
        assert explicit == default
        # The contrast that gives the equality its value: an EMPTY gate is a
        # different thing from None. Without this, the assertion above is a
        # tautology (None IS the default argument).
        empty_gate = compile_value_brief(installed_namespaces=frozenset())
        assert _brief_namespaces(default) == {
            DEV, MARKETING, "product-strategy", "pm", "agent-ops",
        }, "fixture: the ungated brief must be non-trivially populated"
        assert _brief_namespaces(empty_gate) == set(), \
            "an EMPTY gate must narrow to core — otherwise None gates nothing"
        assert set(empty_gate) < set(default)

    def test_gate_narrows_the_brief_to_the_installed_namespace(self):
        """Indicator 1 at the seam: N installed ⇒ exactly those namespaces.

        FAIL-ON: the filter is a no-op, so the marketing namespace survives a
        dev-only gate.
        REACHABLE: ``marketing:campaign`` exists in the real marketing pack's
        kindDefs, so its absence under the gate is a real observation (not an
        assert over an empty fixture).
        """
        gated = compile_value_brief(installed_namespaces={DEV})
        assert _brief_namespaces(gated) == {DEV}
        assert DEV_POINT in gated
        assert MARKETING_OBJECT not in gated

    def test_gate_never_touches_the_core_vocabulary(self):
        """Core is never gated — a graph always has its core kinds.

        FAIL-ON: the gate is applied to the core dict too, so a dev-only graph
        loses ``core:Project`` etc. (and every write of a core kind 422s).
        REACHABLE: the core dict is a real, non-empty fixture (17 keys).
        """
        ungated = compile_value_brief()
        gated = compile_value_brief(installed_namespaces={DEV})
        core_keys = {k for k in ungated if k.startswith("core:")}
        assert core_keys and core_keys <= set(gated)

    def test_gate_also_applies_to_the_tenant_manifest_overlay(self):
        """The #2031 overlay is gated by the SAME namespace filter.

        FAIL-ON: the tenant overlay bypasses the gate, so a stored-but-not-
        installed tenant pack leaks kinds into the prompt. (Before the fix the
        overlay loop had no gate at all.)
        REACHABLE: the overlay fixture declares ``orphan-ops:contract`` AND a
        ``memory_granularity``, so with ``orphan-ops`` NOT in the gate both
        legs are genuinely absent, and with it IN the gate both are
        genuinely present — the positive control proves the assert can
        distinguish the two implementations. (The granularity leg was
        previously untested under a gate; the fixture is what made it so.)
        """
        manifests = {"orphan-ops": TENANT_MANIFEST}
        gated_out = compile_value_brief(
            tenant_manifests=manifests, installed_namespaces={DEV})
        assert not any(k.startswith("orphan-ops:") for k in gated_out)
        assert "orphan-ops" not in gated_out["memory_granularity"], \
            "the overlay's memory_granularity must be gated with its kinds"
        installed = compile_value_brief(
            tenant_manifests=manifests,
            installed_namespaces={DEV, "orphan-ops"})
        assert "orphan-ops:contract" in installed
        assert installed["memory_granularity"]["orphan-ops"]

    def test_granularity_follows_the_gate(self):
        """memory_granularity is compiled per namespace and must be gated too.

        FAIL-ON: granularity compiled from `ns_files` unchecked, so a
        dev-only graph still renders the marketing granularity into S1.
        REACHABLE: every real pack in the catalog declares
        memory_granularity, so a gated-out namespace's absence is observable.
        """
        gated = compile_value_brief(installed_namespaces={DEV})
        assert set(gated["memory_granularity"]) == {DEV}
        assert set(compile_value_brief()["memory_granularity"]) >= {
            DEV, MARKETING}


# ══════════════════════════════════════════════════════════════════════════
# B. compile_vocab — the WRITE-gate seam (pure; no graph)
# ══════════════════════════════════════════════════════════════════════════

class TestCompileVocabGating:

    def test_ungated_vocab_is_still_the_catalog_union(self):
        """Indicator 3 at the write gate.

        FAIL-ON: the default vocab stops carrying pack kinds.
        REACHABLE: `marketing:contentBrief` is a real declared pointKind.
        """
        assert MARKETING_POINT in compile_vocab().point_kinds

    def test_gate_rejects_a_non_installed_namespaced_kind(self):
        """Indicator 2 at the seam.

        FAIL-ON: the point-kinds leg is not filtered.
        REACHABLE: the marketing pack really declares contentBrief; the dev
        vocabulary really does not.
        """
        vocab = compile_vocab(installed_namespaces={DEV})
        assert DEV_POINT in vocab.point_kinds
        assert MARKETING_POINT not in vocab.point_kinds

    def test_gate_keeps_every_core_leg_intact(self):
        """Core point/source/event legs are never gated.

        FAIL-ON: gating a core leg (e.g. the event set) 422s core writes.
        REACHABLE: all three core sets are non-empty in the real build.

        ⚠️ The core set is the CANONICAL one, not "every kind without a
        colon": a pack kind also has a bare (un-namespaced) form — the
        registry's canonical `ns:kind` plus the bare `kind` — so the
        `':' not in k` heuristic misclassifies `rationale` / `useCase` as
        core and reports a phantom loss. (This test caught exactly that on
        its first run; the fix is the canonical set, not a looser assert.)
        """
        core_by_leg = {
            "point_kinds": CORE_POINT_KINDS,
            "source_kinds": CORE_SOURCE_KINDS,
            "event_kinds": EVENT_KINDS,
        }
        gated = compile_vocab(installed_namespaces={DEV})
        for leg, core in core_by_leg.items():
            assert core, f"fixture: {leg} has no canonical core member"
            assert core <= getattr(gated, leg), f"{leg} lost core vocabulary"


# ══════════════════════════════════════════════════════════════════════════
# C. The Layer-1 gate itself — a real payload, a graph-scoped vocab
# ══════════════════════════════════════════════════════════════════════════

class TestLayer1WriteGate:

    @staticmethod
    def _check(point_kind: str, vocab):
        raw = _raw_payload(points=[_point(0, pointKind=point_kind)])
        return validate_payload_dict(_finalize(raw), vocab=vocab)

    def test_non_installed_kind_is_rejected_at_write(self):
        """Indicator 2, end-to-end through Layer-1 (the actual 422).

        FAIL-ON: the write gate accepts a kind whose namespace the graph does
        not install — the exact "a marketing-only graph mints dev kinds"
        defect the issue exists to kill (here inverted: a dev-only graph
        minting marketing kinds).
        REACHABLE: the payload's point really carries
        ``marketing:contentBrief``, and that string is really absent from the
        dev-scoped vocab — so the rejection is caused by the gate, not by an
        unrelated shape error (the positive control below proves it).
        """
        vocab = compile_vocab(installed_namespaces={DEV})
        result, _payload = self._check(MARKETING_POINT, vocab)
        assert not result.ok, "a non-installed kind must not pass Layer-1"
        assert "points[0].pointKind" in result.errors
        assert result.code == "calibration_mismatch"

    def test_installed_kind_is_accepted_at_write(self):
        """The positive control for the test above.

        Without this, a rejection could come from anything. FAIL-ON: the gate
        is too wide (rejects an installed kind), or the payload is invalid for
        an unrelated reason. REACHABLE: `dev:requirement` is a real declared
        dev pointKind and the payload is the shared valid fixture.
        """
        vocab = compile_vocab(installed_namespaces={DEV})
        result, _payload = self._check(DEV_POINT, vocab)
        assert result.ok, f"the installed kind must pass: {result.errors}"

    def test_ungated_vocab_accepts_the_same_kind(self):
        """Indicator 3, mirrored at the write gate: no records ⇒ no narrowing.

        FAIL-ON: the baseline union stops accepting a catalog kind.
        REACHABLE: identical payload to the rejection test — only the vocab
        differs, so the two tests differ by exactly the gate.
        """
        result, _payload = self._check(MARKETING_POINT, compile_vocab())
        assert result.ok, f"the union vocab must accept it: {result.errors}"


# ══════════════════════════════════════════════════════════════════════════
# D. The graph-side resolver + the PROMPT it feeds (needs a graph)
# ══════════════════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════════════════
# D2. pack_kinds is DERIVED from the brief — there is no second allowlist
#     (#5165; the pre-fix engine prefix-matched a hardcoded PACK_NS tuple)
# ══════════════════════════════════════════════════════════════════════════

class TestPackKindDerivationFromBrief:
    """#5165 — after #2714 the brief is the authority; a namespace filter in
    the master builder can only ever DROP a pack the graph installed.

    The defect: ``_build_master_from_brief`` kept a brief key only if it
    started with ``PACK_NS + tenant_prefixes``. A CATALOG pack outside
    ``PACK_NS`` has no ``:PackManifest`` node, hence no tenant prefix either,
    so its kinds were present in the gated brief and silently absent from
    ``pack_kinds`` — never offered by the prompt, never writable by the
    minted-kind gate (``master_kind_forms``).
    """

    def test_installed_catalog_pack_outside_the_legacy_tuple_reaches_pack_kinds(
            self, sdk, venture_catalog):
        """The issue's exact scenario: a catalog pack INSTALLED on a graph
        whose namespace is not in the legacy tuple.

        FAIL-ON: the master re-applies a namespace allowlist — the graph's
        approval set is ``{venture}``, so the gated brief carries the venture
        kinds and a second filter drops them (pre-fix this assertion is
        reached with an EMPTY ``pack_kinds``).
        REACHABLE: the hermetic catalog genuinely declares
        ``venture:tranche``/``venture:thesis`` (asserted on the gated brief
        itself, the compile INPUT), and the seeded ``:PackInstall`` record is
        the graph's whole approval set — so both the brief and the installed
        set really name venture.
        """
        _seed_install(sdk, "venture")
        gated_brief = compile_value_brief(
            installed_namespaces=frozenset({"venture"}))
        assert VENTURE_TRANCHE in gated_brief and VENTURE_THESIS in gated_brief, \
            "fixture: the gated brief must carry the venture kinds"
        master = build_master_list(sdk=sdk)
        assert VENTURE_TRANCHE in master["pack_kinds"]
        assert VENTURE_THESIS in master["pack_kinds"]
        assert not any(k.startswith("core:") for k in master["pack_kinds"]), \
            "core kinds belong to the objects section, never pack_kinds"
        # The consequence named in the issue: a dropped pack_kind is also
        # unwritable — master_kind_forms is S5's minted-kind gate, and it
        # reads this exact section.
        assert VENTURE_TRANCHE in master_kind_forms(master), \
            "the minted-kind gate must accept an installed catalog pack's kind"

    def test_default_path_pack_kinds_are_every_non_core_brief_key(
            self, venture_catalog):
        """The default (ungated) path too: the catalog union IS the brief's
        own key set, so a new catalog pack needs no engine edit.

        FAIL-ON: the default path still filters by a static namespace tuple
        (pre-fix: ``pack_kinds`` is empty under this fixture).
        REACHABLE: the hermetic catalog holds a namespace outside the legacy
        tuple, so the brief and a filtered pack_kinds genuinely differ —
        order included (the brief's insertion order is prompt-visible).
        """
        master = build_master_list()
        brief = compile_value_brief()
        expected = [k for k in brief
                    if k != "memory_granularity" and not k.startswith("core:")]
        assert expected, "fixture: the hermetic brief must have pack kinds"
        assert list(master["pack_kinds"]) == expected, \
            "pack_kinds must be exactly the brief's non-core keys, in order"
        assert VENTURE_TRANCHE in master["pack_kinds"]

    def test_every_catalog_pack_reaches_the_master_list(self, venture_catalog):
        """The general guard: NO shipped catalog pack's namespace may be
        absent from `pack_kinds` — the exact property the hardcoded tuple
        violated.

        FAIL-ON: the master re-applies a namespace allowlist. The oracle is
        the registry's own `packs` (the compile INPUT), never the master —
        under this hermetic catalog the pre-fix code reds with
        `missing == ['venture']`.
        REACHABLE: the registry genuinely holds the venture namespace and its
        manifest genuinely declares kinds, so `missing` is a real comparison
        and not an empty-vs-empty tautology.
        """
        from tortoise.pack_registry import PackRegistry, default_packs_dir

        reg = PackRegistry(default_packs_dir())
        reg.load_all()
        assert "venture" in reg.packs, "fixture: the catalog must load venture"
        kinds = build_master_list()["pack_kinds"]
        missing = [ns for ns in reg.packs
                   if not any(k.startswith(f"{ns}:") for k in kinds)]
        assert not missing, f"shipped catalog packs absent from pack_kinds: {missing}"

    def test_core_namespaced_brief_key_lands_in_objects_never_pack_kinds(self):
        """A non-canonical `core:*` key must not enter `pack_kinds`.

        FAIL-ON: the derivation classifies by `CORE_OBJECT_KEYS` membership
        alone, so the stray key rides `pack_kinds` — and `render_s2_prompt`
        derives the core-only prompt's pack-namespace list from exactly that
        section, telling the model `core:` is a PACK namespace whose content
        must be `unclassified`.
        REACHABLE: `compile_value_brief` tolerates a non-canonical `core:*`
        key by design (it filters only collisions with the canonical 16 — the
        legacy/bypass `core` `:PackManifest` it defends against), so a brief
        can genuinely carry one.
        """
        brief = {
            "core:Project": {"description": "A project"},
            "core:FinancialReport": {"description": "a bypass core kind"},
            VENTURE_TRANCHE: {"description": "A financing tranche"},
            "memory_granularity": {},
        }
        master = _build_master_from_brief(brief)
        assert "core:FinancialReport" not in master["pack_kinds"]
        assert master["objects"].get("core:FinancialReport") == \
            "a bypass core kind", "the stray core kind must stay offered"
        assert VENTURE_TRANCHE in master["pack_kinds"]

    def test_brief_core_keys_are_exactly_the_canonical_object_kinds(self):
        """The derivation rests on `CORE_OBJECT_KEYS` being the brief's whole
        core key set; nothing else pins that equality.

        FAIL-ON: `compile_value_brief`'s core dict gains or renames a key
        while `CORE_OBJECT_KEYS` stays put. The consequence is NOT
        mis-sectioning — a `core:`-prefixed key is routed into `objects`
        either way — it is that the `objects` seed and the brief disagree:
        a canonical kind the brief no longer carries renders with an empty
        description, and a kind the brief adds loses its seeded position.
        No other test pins this equality.
        REACHABLE: the real brief carries 16 core keys, and the comparison is
        a SET equality, so an addition and a removal each red it.
        """
        core_keys = {k for k in compile_value_brief() if k.startswith("core:")}
        assert core_keys == set(CORE_OBJECT_KEYS), \
            "the brief's core keys and the canonical object kinds drifted"


class TestGraphInstalledNamespaces:

    def test_absent_records_returns_none_and_leaves_the_prompt_untouched(self, sdk):
        """⭐ Indicator 3 — the one that bites.

        A graph with NO activation records must keep working EXACTLY as today.
        Every pre-#318 graph, every self-hosted graph, and every graph restored
        without pack state is in this class; gating them to core-only would
        break them while the new tests still passed.

        FAIL-ON: the resolver returns ``frozenset()`` (or an empty set) for an
        empty graph instead of ``None`` — the master then loses every pack kind.
        REACHABLE: this fixture has genuinely zero ``:PackInstall`` rows
        (asserted, not assumed), and the ungated brief really has 5 namespaces
        to lose — so "untouched" is measurable, not vacuous.
        """
        assert _read_install_namespaces(sdk) == [], \
            "fixture precondition: this graph must have NO :PackInstall rows"
        assert graph_installed_namespaces(sdk) is None
        master = build_master_list(sdk=sdk)
        assert _master_namespaces(master) == _brief_namespaces(
            compile_value_brief())
        assert MARKETING_OBJECT in master["pack_kinds"]

    def test_installed_records_gate_the_extractor_prompt(self, sdk):
        """Indicator 1: N packs installed ⇒ exactly those pack kinds offered.

        FAIL-ON: the resolver reads the records but the prompt ignores them
        (`build_master_list` still renders the catalog union) — the marketing
        namespace survives a dev-only graph.
        REACHABLE: the seeded record really is the only activation, and the
        marketing keys really are in the catalog brief being filtered.
        """
        _seed_install(sdk, DEV)
        assert graph_installed_namespaces(sdk) == frozenset({DEV})
        master = build_master_list(sdk=sdk)
        assert _master_namespaces(master) == {DEV}
        assert DEV_POINT in master["pack_kinds"]
        assert MARKETING_OBJECT not in master["pack_kinds"]

    def test_prompt_set_and_write_gate_set_agree(self, sdk):
        """The two seams read ONE resolver — a KIND-level mismatch is a bug.

        The 422 hazard is directional: a kind the PROMPT offers and the write
        gate then REJECTS is an extraction the graph cannot store. A
        namespace-prefix comparison cannot see it (mutation-verified: dropping
        ``dev:requirement`` from the gate's point leg left a prefix-only
        version of this test green), so the POINT leg — the one whose value
        the payload's ``pointKind`` is checked against — is pinned BY KIND.

        FAIL-ON: any ``pointKinds`` entry the pack manifest declares is
        missing from the prompt, or from the write gate's point leg.
        REACHABLE: the dev manifest declares three pointKinds
        (requirement/risk/technicalDebt), all of which the ungated compile
        really carries — so both sides have rows, and dropping any one of
        them from the gate reds this test.

        Residual (stated, not implied): the object/document legs are NOT
        compared. They are validated by the deterministic enforcer, which is
        still ungated (#5163), so there is no write gate to agree WITH yet.
        """
        _seed_install(sdk, DEV)
        declared_points = {f"{DEV}:{k}"
                           for k in _dev_manifest_ontology()["pointKinds"]}
        assert declared_points, "fixture: the dev manifest declares pointKinds"
        installed = graph_installed_namespaces(sdk)
        master = build_master_list(sdk=sdk)
        vocab = compile_vocab(installed_namespaces=installed)
        offered = set(master["pack_kinds"])
        assert declared_points <= offered, (
            "the prompt omits a kind its own installed pack declares")
        rejected = declared_points - set(vocab.point_kinds)
        assert not rejected, (
            "the prompt offers point kinds the write gate rejects (the "
            f"extractor would mint a 422): {sorted(rejected)}"
        )

    def test_historical_kind_namespaces_are_unioned_back(self, sdk):
        """Indicator 2's back-compat clause: data already using a namespace
        keeps it writable even when the pack is not installed.

        FAIL-ON: the union is record-only, so a graph carrying marketing data
        under a dev-only install set loses the ability to update that data.
        REACHABLE: the fixture writes a real ``objectKind='marketing:campaign'``
        node, so the scan has a row to find — this is the row the doctrine
        warns is usually missing.
        """
        _seed_install(sdk, DEV)
        sdk._get_proj().g.query(
            "MERGE (n:Object {id: 'l7-historical', "
            "objectKind: $kind})",
            params={"kind": MARKETING_OBJECT},
        )
        assert graph_kind_namespaces(sdk._get_proj().g) == frozenset({MARKETING})
        assert graph_installed_namespaces(sdk) == frozenset({DEV, MARKETING})
        # …and the union actually reaches the write gate.
        vocab = compile_vocab(installed_namespaces=graph_installed_namespaces(sdk))
        assert MARKETING_POINT in vocab.point_kinds

    def test_removed_install_record_stays_in_the_approval_set(self, sdk):
        """The recorded removal semantics (#2714 open decision (a)).

        The non-destructive arm is taken: ``delete_tenant_manifest`` flips the
        record to ``status='removed'`` and the namespace stays writable
        because its kinds are still in the data.

        FAIL-ON: the resolver filters to ``status == 'active'``, silently
        choosing the tombstone arm that the issue left open.
        REACHABLE: a real ``status='removed'`` record is written here, so the
        assertion is over a row of exactly the kind under test.
        """
        _seed_install(sdk, DEV)
        _seed_install(sdk, MARKETING, status="removed", source="custom")
        assert graph_installed_namespaces(sdk) == frozenset({DEV, MARKETING})

    def test_data_union_change_recompiles_the_view(self, sdk):
        """A namespace appearing in the graph's DATA after a compile must
        recompile the tenant view — the memo key includes the data union.

        This is a VGATE reviewer's counterexample turned into a test: with a
        record-only memo key, ``tenant_view`` returned the SAME memoized
        object after a marketing node had been written, so the graph's own
        history stayed invisible to the gate for as long as the key stood
        still.

        FAIL-ON: the memo key covers only ``:PackInstall`` records (plus the
        write dirty-set), so the second ``tenant_view`` is an identity hit
        and ``marketing`` never enters the approval set.
        REACHABLE: the node written between the two calls is a real
        ``objectKind='marketing:campaign'`` row, so the data union genuinely
        changes and the two views genuinely differ.
        """
        from tortoise.pack_manifest_store import tenant_view
        _seed_install(sdk, DEV)
        v1 = tenant_view(sdk)
        assert v1["installed_namespaces"] == frozenset({DEV})
        sdk._get_proj().g.query(
            "MERGE (n:Object {id: 'l7-late-ns', objectKind: $k})",
            params={"k": MARKETING_OBJECT},
        )
        v2 = tenant_view(sdk)
        assert v2 is not v1, "the memo served a stale approval set"
        assert MARKETING in v2["installed_namespaces"]

    def test_stored_but_uninstalled_tenant_manifest_is_gated_out(self, sdk):
        """A ``:PackManifest`` node with no activation record contributes
        nothing — the tenant leg of the gate.

        FAIL-ON: the tenant overlay is merged unconditionally (the pre-#2714
        behaviour), so an orphaned manifest's kinds reach the prompt.
        REACHABLE + POSITIVE CONTROL: the identical manifest IS exposed once
        its ``:PackInstall`` record exists, so the absence is caused by the
        gate and not by a malformed fixture or a stale memo.
        """
        _seed_install(sdk, DEV)
        g = sdk._get_proj().g
        g.query(
            "MERGE (m:PackManifest {namespace: 'orphan-ops'}) "
            "SET m.name = 'Orphan Operations', m.version = '0.1.0', "
            "    m.sha256 = 'deadbeef', m.status = 'active', m.yaml = $y",
            params={"y": TENANT_MANIFEST},
        )
        assert "orphan-ops:contract" not in build_master_list(sdk=sdk)["pack_kinds"]
        _seed_install(sdk, "orphan-ops", source="custom")
        assert "orphan-ops:contract" in build_master_list(sdk=sdk)["pack_kinds"]


# ══════════════════════════════════════════════════════════════════════════
# E. The kind-scan failure direction (hermetic graph stub — no DB)
# ══════════════════════════════════════════════════════════════════════════

class _FakeResult:
    def __init__(self, rows):
        self.result_set = rows


class _FakeGraph:
    """A graph stub whose per-property kind scans are scripted, so the
    FAILURE direction of ``graph_kind_namespaces`` is testable without a DB."""

    def __init__(self, rows_by_key: dict, *, fail_key: str | None = None,
                 bad_row_key: str | None = None,
                 none_result_key: str | None = None):
        self._rows = rows_by_key
        self._fail_key = fail_key
        self._bad_row_key = bad_row_key
        self._none_result_key = none_result_key

    def query(self, cypher, params=None):
        if self._fail_key and f"n.{self._fail_key} IS NOT NULL" in cypher:
            raise RuntimeError("scripted scan failure")
        for key, values in self._rows.items():
            if f"n.{key} IS NOT NULL" in cypher:
                if self._none_result_key == key:
                    return _FakeResult(None)
                rows = [[v] for v in values]
                if self._bad_row_key == key:
                    # An un-subscriptable "row" between two real ones.
                    rows.insert(1, 1)
                return _FakeResult(rows)
        return _FakeResult([])


class TestKindScanFailureDirection:

    def test_kind_scan_failure_narrows_never_widens(self):
        """A failed property scan may only NARROW the data union.

        This pins the behaviour the docstring states, so the comment cannot
        drift from the code (the doctrine's "a process claim can only
        re-stale" failure mode). It is a deliberate trade-off, not an
        accident: widening on error would silently disable the gate and
        re-create the defect #2714 fixes.

        FAIL-ON: the implementation swallows the error and returns an EMPTY
        union *as if it were complete* in a way that widens (it cannot here),
        or — the real risk — the handler grows a bare `except: pass` that
        returns a partially-built set indistinguishable from a complete one
        AND some leg unions the catalog back in. Also fails if the exception
        escapes at all.
        REACHABLE: the stub returns real rows for `objectKind`
        (`marketing:campaign`, `dev:code`) and then fails that exact key — so
        both the complete and the narrowed result are non-empty/observable,
        and the subset relation has a value on both sides.
        """
        rows = {"objectKind": ["marketing:campaign", "dev:code"]}
        complete = graph_kind_namespaces(_FakeGraph(rows))
        assert complete == frozenset({MARKETING, DEV}), \
            "fixture: the complete scan must find both namespaces"
        narrowed = graph_kind_namespaces(
            _FakeGraph(rows, fail_key="objectKind"))
        assert narrowed <= complete, \
            "a scan failure must never WIDEN the back-compat union"
        assert narrowed == frozenset(), \
            "the failed key's namespaces must drop out of the union"

    def test_kind_property_set_is_pinned_against_an_independent_oracle(self):
        """The scanned property set is a CONTRACT, not the loop's own variable.

        ``test_every_key_is_scanned_and_only_ns_kinds_count`` iterates
        ``_KIND_PROP_KEYS`` — the same tuple the implementation iterates — so
        dropping a member removes it from the code AND from the test's
        expectation and the test stays green (mutation-verified: removing
        ``subjectKind`` left the whole file green). A namespace carried only
        by the dropped property then becomes undiscoverable to the
        back-compat union — data the graph already holds stops being writable
        (indicator 2's "accepted if historical" clause) with no red test.

        FAIL-ON: any member is dropped from or added to ``_KIND_PROP_KEYS``
        without this canonical list and the writer-side twin moving with it.
        REACHABLE: the tuple is non-empty and the canonical set is compared as
        a SET, so an omission and an addition are both observable.
        """
        # Hard-coded HERE, deliberately independent of the implementation:
        # two copies of the same knowledge, one of which (this one) the
        # implementation cannot mutate along with its own loop.
        canonical = {
            "objectKind", "pointKind", "eventKind", "sourceKind",
            "documentKind", "subjectKind", "actionKind", "kind",
        }
        assert set(_KIND_PROP_KEYS) == canonical, (
            "the scanned kind-property set drifted; a namespace carried only "
            "by a dropped property is invisible to the back-compat union"
        )
        # The writer-side twin must agree (#3977: a second copy drifts from
        # the first). Imported lazily — hosted_api is a heavy import and this
        # is a static-constant comparison.
        from tortoise.hosted_api import _KIND_PROP_KEYS as _hosted_keys
        assert set(_hosted_keys) == canonical, (
            "hosted_api._KIND_PROP_KEYS drifted from the union's scan set"
        )

    def test_undecodable_row_narrows_instead_of_escaping(self):
        """A row the decode loop cannot read must NARROW, not escape.

        FAIL-ON: the decode loop sits outside the narrowing guard, so one
        un-subscriptable row (or a ``None`` result_set) raises out of
        ``graph_kind_namespaces`` — and because ``graph_installed_namespaces``
        does not catch it, the whole tenant view goes down instead of the
        union narrowing.
        REACHABLE: the stub returns a real decodable row BEFORE the bad one
        and a second one AFTER it, so "the bad row is skipped" and "the read
        continues" are both observable.
        """
        rows = {"objectKind": ["marketing:campaign", "dev:code"]}
        complete = graph_kind_namespaces(_FakeGraph(rows))
        assert complete == frozenset({MARKETING, DEV}), \
            "fixture: the complete scan must find both namespaces"
        truncated = graph_kind_namespaces(
            _FakeGraph(rows, bad_row_key="objectKind"))
        assert truncated == frozenset({MARKETING}), \
            "the rows before the bad one must survive; it must not raise"
        assert truncated <= complete, "decode failure must not widen"
        assert graph_kind_namespaces(
            _FakeGraph(rows, none_result_key="objectKind")) == frozenset(), \
            "a None result_set must narrow to nothing, not raise"

    def test_every_key_is_scanned_and_only_ns_kinds_count(self):
        """All eight kind properties are scanned; non-namespaced values and
        the null row are ignored (no crash, no phantom namespace).

        FAIL-ON: a key is dropped from `_KIND_PROP_KEYS` (a pack namespace
        carried only by that property becomes undiscoverable), or a bare
        `dev` value / a `None` row is treated as a namespace.
        REACHABLE: the stub returns one namespaced value, one BARE value
        (`dev`) and one `None` — so both the accept and the two reject paths
        have a row.
        """
        for key in _KIND_PROP_KEYS:
            found = graph_kind_namespaces(_FakeGraph({key: ["dev:code"]}))
            assert found == frozenset({DEV}), f"key {key} was not scanned"
        noisy = graph_kind_namespaces(
            _FakeGraph({"objectKind": ["dev", None, "marketing:campaign"]}))
        assert noisy == frozenset({MARKETING}), \
            "bare and null kind values must not become namespaces"
