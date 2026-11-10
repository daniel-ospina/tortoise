"""#11656 — the measured facts the endpoint-kind-binding scope rests on.

WHY THIS FILE EXISTS

`docs/plans/2026-11-09-11656-endpoint-kind-binding.md` and
`docs/research/2026-11-09-11656-kindpair-enforcement.md` carry load-bearing NUMBERS — how many declarations
exist, which predicates are declared more than once, which surfaces can write a declared label. A lane
implements against those numbers. A fresh review of the first revision found **three of them wrong**, all
produced the same way: asserted from a partial reading instead of re-measured.

  * "`hasPart` is declared 4 different ways" — it is declared **once** (`dev:theme → dev:epic`). The "four"
    counted `semantics: hasPart`, which rides four *different predicates*. A `semantics:` value is not a
    declaration.
  * "`fromKind`/`toKind` are acted on in exactly one place" — there are **three** production readers.
  * "a `create_edge`-only pin leaves 31 untested" — 2 declarations also bind the structural path, so 30.

Each number below is re-measured from `packs/*/manifest.yaml` (offline: no graph, no registry boot, no
network), so a manifest edit that makes a scope claim false reds here instead of rotting in prose. This is
the "dogfood the repo's own declared relations" pin the brief's CI shape asks for.

It also pins the two DEFECTS the review found in the docs themselves — both are cheap, mechanical, and would
have shipped because nothing reads prose:

  * neither #11656 doc was registered in `docs/00_index.md` (the repo's ledger — how the next lane finds a
    doc);
  * a cited `.md` was written bare where the basename is ambiguous (7 files in this repo are named
    `04-plan.md`), so the reference did not resolve from the citing document.
"""
from __future__ import annotations

import inspect
import os
import re
import sys
from collections import Counter
from pathlib import Path

import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: I001
from tortoise.pack_registry import PackRegistry

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKS_DIR = REPO_ROOT / "packs"
SCOPE_DOC = REPO_ROOT / "docs" / "plans" / "2026-11-09-11656-endpoint-kind-binding.md"
BRIEF_DOC = REPO_ROOT / "docs" / "research" / "2026-11-09-11656-kindpair-enforcement.md"
INDEX_DOC = REPO_ROOT / "docs" / "00_index.md"


def _declared_relations() -> list[dict]:
    """Every `ontology.relations[]` row across the shipped packs, keyed by its namespace."""
    rows: list[dict] = []
    for manifest in sorted(PACKS_DIR.glob("*/manifest.yaml")):
        data = yaml.safe_load(manifest.read_text()) or {}
        namespace = data.get("namespace")
        if not namespace:
            continue  # `_template` ships nothing
        for rel in (data.get("ontology") or {}).get("relations") or []:
            if isinstance(rel, dict):
                rows.append({**rel, "_ns": namespace})
    return rows


def _kind_bearing(rows: list[dict]) -> list[dict]:
    return [r for r in rows if r.get("fromKind") and r.get("toKind")]


class TestTheInventoryTheScopeCites:
    """§1 of the scope: the counts that decide which doors must be gated.

    These moved when `origin/main` was merged into this branch (+3 kind-bearing declarations, 32 -> 35).
    They are re-measured here rather than quoted, because the review of the previous revision found three
    figures that had been carried forward.
    """

    def test_totals(self):
        rows = _declared_relations()
        assert len(rows) == 36, "the scope says 36 declared relations across 7 packs"
        assert len(_kind_bearing(rows)) == 35, "35 of them carry fromKind+toKind (32 before origin/main moved)"

    def test_every_kind_bearing_declaration_carries_a_core_mechanism(self):
        """34 IMPL + 1 NAND.

        This is the fact that makes the OPERATOR path (35/35) the seam — and the reason a `create_edge`-only
        gate covers 2 of 35, a partial one. If a pack ever adds a kind-bearing relation on a non-core
        mechanism, the seam choice has to be re-derived — so it must red, not drift.
        """
        mechanisms = Counter(r.get("mechanism") for r in _kind_bearing(_declared_relations()))
        assert mechanisms == {"IMPL": 34, "NAND": 1}, mechanisms

    def test_the_routing_the_gate_choice_rests_on(self):
        """The number §1.2's table and §5.3a both rest on: which write paths each declaration binds.

        `relation_write_paths()` is the real routing predicate — the `mechanism` counts above are only a
        proxy for it. Measured at this head: 33 declarations bind the OPERATOR path alone, and exactly 2
        (`hasPart`, `dependsOn`) ALSO bind the structural path — which is why a `create_edge`-only gate
        covers 2 of 35 and an operator-path gate covers 35 of 35. A change to `CORE_PREDICATES` or to
        `relation_write_paths` itself must red here rather than leave the scope silently wrong.
        """
        from tortoise.pack_registry import relation_write_paths

        bearing = _kind_bearing(_declared_relations())
        operator_only = [r["predicate"] for r in bearing if relation_write_paths(r) == ("operator",)]
        structural = [r["predicate"] for r in bearing if "structural" in relation_write_paths(r)]
        assert len(operator_only) == 33, Counter(operator_only)
        assert sorted(structural) == ["dependsOn", "hasPart"], sorted(structural)
        assert all("operator" in relation_write_paths(r) for r in bearing), "every one rides the operator path"

    def test_haspart_is_declared_exactly_once(self):
        """⛔ The review's P1: revision 1 claimed "4 packs, 4 different pairs".

        `hasPart` appears as a *predicate* once. The four rows revision 1 counted carry `semantics: hasPart`
        on four different predicates — a meaning, not a declaration.
        """
        haspart = [r for r in _declared_relations() if r.get("predicate") == "hasPart"]
        assert len(haspart) == 1, [(r["_ns"], r.get("fromKind"), r.get("toKind")) for r in haspart]
        assert (haspart[0]["fromKind"], haspart[0]["toKind"]) == ("dev:theme", "dev:epic")

        # The miscount's source: `semantics: hasPart` rides SEVERAL DIFFERENT predicates. The exact
        # count moves with main (+1 at the merge head), so pin the shape — a population of distinct
        # predicates, none of them `hasPart` — rather than a number that would red on an unrelated edit.
        semantics_only = [r for r in _declared_relations()
                          if r.get("semantics") == "hasPart" and r.get("predicate") != "hasPart"]
        assert len(semantics_only) >= 4, "the miscount's source: several distinct predicates CARRY hasPart meaning"
        # A SHAPE pin, not the count: the miscount counted four DIFFERENT predicates carrying this meaning,
        # so if they ever collapse into one predicate the evidence for "`semantics:` is not a declaration" is
        # gone. (`predicate != "hasPart"` above already guarantees the row is not the declaration itself.)
        assert len({r["predicate"] for r in semantics_only}) >= 4, sorted({r["predicate"] for r in semantics_only})

    def test_the_repeated_predicates_are_addresses_contains_targets(self):
        """D3's evidence, on the correct rows (the corrected §1.3 / brief §4.4)."""
        bearing = _kind_bearing(_declared_relations())
        counts = Counter(r.get("predicate") for r in bearing)
        repeated = {p: n for p, n in counts.items() if n > 1}
        assert repeated == {"addresses": 6, "contains": 2, "targets": 2, "assignedTo": 2}, repeated

        addresses = [r for r in bearing if r.get("predicate") == "addresses"]
        pairs = {(r["fromKind"], r["toKind"]) for r in addresses}
        assert len(pairs) == 6, "6 declarations over 6 DISTINCT pairs — not 6 uses of one pair"
        assert len([r for r in addresses if r["_ns"] == "product-strategy"]) == 4

    def test_the_declarations_a_naive_binding_test_would_disable_are_eight(self):
        """D3 must bind the vocabulary a declaration NAMES, not only the declaring pack's.

        5 name a foreign namespace explicitly; 3 rows over 2 predicates are layer-valued (`toKind: Subject` —
        `assignedTo` twice, `holds`). Under the naive "both kinds in the declaring pack" test all 8 become
        permanently `unbound` — never enforced, never warned about — and `portfolio:marks` is load-bearing
        (`domain_validators._portfolio_mark_contract`).
        """
        bearing = _kind_bearing(_declared_relations())
        cross, layer = [], []
        for r in bearing:
            declared = {(r["fromKind"] or "").split(":", 1)[0],
                        (r["toKind"] or "").split(":", 1)[0]}
            if r["toKind"] == "Subject":
                layer.append(r["predicate"])
            elif any(ns and ns != r["_ns"] for ns in declared):
                cross.append(r["predicate"])
        assert sorted(cross) == ["constrainedBy", "deploysTo", "implements", "manifests", "marks"], cross
        assert sorted(layer) == ["assignedTo", "assignedTo", "holds"], layer
        assert len(cross) + len(layer) == 8


class TestTheSeveritySeamThePrecedenceMustAnswerFor:
    """§2.1 of the scope: the enforcement map cannot carry D1's refuse half on the shipped tree."""

    def test_no_shipped_pack_declares_a_per_relation_enforcement_level(self):
        for manifest in sorted(PACKS_DIR.glob("*/manifest.yaml")):
            data = yaml.safe_load(manifest.read_text()) or {}
            levels = ((data.get("extraction") or {}).get("enforcement") or {}).get("relations")
            assert not levels, (
                f"{manifest.parent.name} now declares per-relation enforcement {levels!r} — the scope's "
                "§2.1 rests on the shipped tree declaring NONE, which is why the kind-pair check carries its "
                "own `block` default instead of inheriting the enforcement map's `warn`"
            )

    def test_every_kind_bearing_declaration_resolves_warn_through_the_real_seam(self):
        registry = PackRegistry(PACKS_DIR)
        registry.load_all()
        resolved: Counter = Counter()
        for rel in _kind_bearing(_declared_relations()):
            pack = registry.get_pack(rel["_ns"])
            assert pack is not None, rel["_ns"]
            resolved[pack.enforcement_for_relation(rel["predicate"])] += 1
        assert resolved == {"warn": 35}, resolved


class TestTheThirdDoorExists:
    """⛔ The review's P0: `create_direct_edge` is in scope, and this is the evidence."""

    def test_create_direct_edge_takes_a_declared_label(self):
        from tortoise.sdk import TortoiseSDK

        params = inspect.signature(TortoiseSDK.create_direct_edge).parameters
        assert "label" in params, "the direct door takes the declared predicate, so it IS a declared-relation writer"
        assert "from_kind" not in params and "to_kind" not in params

    def test_create_direct_edge_carries_no_kind_or_declaredness_check(self):
        """Nothing at this door inspects kinds today — the gate is what #11656 adds there."""
        from tortoise.sdk import TortoiseSDK

        source = inspect.getsource(TortoiseSDK.create_direct_edge)
        for absent in ("kind_pair_verdict", "undeclared_relation", "expand_kind"):
            assert absent not in source, (
                f"`{absent}` now appears in create_direct_edge — re-derive §1.2: either the gate landed "
                "(move this pin to its positive form) or a check was added at a door the scope does not name"
            )

    def test_two_public_surfaces_route_a_plain_operator_to_the_direct_door(self):
        """`link_entities`' operator-less arm, and `ingest`'s own `direct` route.

        Both pass the caller's `label` straight through and neither is kind-checked, so the door is reachable
        with a DECLARED predicate from two surfaces — not from one internal primitive.
        """
        from tortoise.sdk import TortoiseSDK

        linking = inspect.getsource(TortoiseSDK.link_entities)
        assert 'upper in ("IMPL", "NAND") and operator is not True' in linking
        assert "label=label" in linking

        ingest = inspect.getsource(TortoiseSDK.ingest)
        assert '_route == "direct"' in ingest, "the bundle writer's direct route — the second public path"
        assert "self.create_direct_edge(" in ingest and "label=label" in ingest

    def test_point_mediated_route_only_reifies_a_point_pair(self):
        """Object↔Object keeps the direct shape — 30 of the 32 declarations."""
        from tortoise.sdk import TortoiseSDK

        doc = inspect.getdoc(TortoiseSDK._point_mediated_route) or ""
        assert "Object↔Object" in doc and "operator-mediated" in doc


class TestThePairHasThreeProductionReaders:
    """⛔ The review's P1: "exactly one place" is false, and the prevention half must not build a fourth."""

    def test_the_readers_exist_and_the_catalog_carries_the_pair(self):
        from tortoise.sdk import TortoiseSDK
        from tortoise.value_extractor import _relation_endpoint_refs

        assert callable(TortoiseSDK._resolve_traversal_path)
        assert callable(_relation_endpoint_refs)
        assert '"fromKind"' in inspect.getsource(_relation_endpoint_refs)

        registry = PackRegistry(PACKS_DIR)
        registry.load_all()
        rows = registry.list_relations()
        assert any(r.get("fromKind") and r.get("toKind") for r in rows), (
            "`list_relations` must expose the pair — `_resolve_traversal_path` and `_relation_endpoint_refs` "
            "read it from here, so a scope that calls the pair map net-new is re-implementing this"
        )

    def test_resolve_traversal_path_is_a_kind_pair_to_predicate_resolver(self):
        """It IS `allowed_relations(from_kind, to_kind)` in production (scope §5.5)."""
        from tortoise.sdk import TortoiseSDK

        method = TortoiseSDK._resolve_traversal_path
        assert method.__doc__ and "predicate" in method.__doc__
        source = inspect.getsource(method)
        assert "list_relations" in source
        assert "fromKind" in source


class TestTheDocsAreDiscoverableAndResolve:
    """⛔ The review's P2s: both docs registered, and every cited path resolves."""

    def test_both_docs_are_registered_in_the_index(self):
        index = INDEX_DOC.read_text()
        for doc in (SCOPE_DOC, BRIEF_DOC):
            rel = doc.relative_to(REPO_ROOT).as_posix()
            assert f"`{rel}`" in index, (
                f"{rel} is not registered in docs/00_index.md — that index is the repo's ledger and how the "
                "next lane finds the doc"
            )

    @pytest.mark.parametrize("doc", [SCOPE_DOC, BRIEF_DOC], ids=["scope", "brief"])
    def test_every_cited_markdown_path_resolves(self, doc):
        """A cited `.md` must resolve from the citing doc, or be an UNAMBIGUOUS repo-wide basename.

        7 files in this repo are named `04-plan.md`, so citing it bare from `docs/research/` is a reference
        that does not resolve — the exact defect the review found.
        """
        basenames = Counter(p.name for p in (REPO_ROOT / "docs").rglob("*.md"))
        cited = set(re.findall(r"`([A-Za-z0-9_./-]+\.md)`", doc.read_text()))
        assert cited, "the doc cites no markdown path at all — the scanner is broken, not the doc"
        for token in sorted(cited):
            if "/" in token:
                target = (REPO_ROOT / token) if token.startswith("docs/") else (doc.parent / token)
                assert target.exists(), f"{doc.name} cites `{token}`, which does not resolve"
            else:
                assert basenames[token] <= 1, (
                    f"{doc.name} cites `{token}` bare, but {basenames[token]} files under docs/ share that "
                    "name — cite it with a path"
                )
