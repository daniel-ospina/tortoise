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
  * "`fromKind`/`toKind` are acted on in exactly one place" — there are **four** production readers (the
    fourth — `pack_hierarchy._matching_row` + `_binding` — is the pair→writer resolver §5.5 says to reuse,
    not rebuild).
  * "a `create_edge`-only pin leaves 31 untested" — 2 declarations also bind the structural path, so 33
    (the corpus is 35 kind-bearing at this head; this line read "31 → 30" before `main` added three).

Each number below is re-measured from `packs/*/manifest.yaml` (offline: no graph, no SDK construction, no
network; the pack registry is loaded from `packs/`), so a manifest edit that makes a scope claim false reds here instead of rotting in prose. This is
the "dogfood the repo's own declared relations" pin the brief's CI shape asks for.

It also pins the two DEFECTS the review found in the docs themselves — both are cheap, mechanical, and would
have shipped because nothing reads prose:

  * neither #11656 doc was registered in `docs/00_index.md` (the repo's ledger — how the next lane finds a
    doc);
  * a cited `.md` was written bare where the basename is ambiguous (more than one file under `docs/` is
    named `04-plan.md`), so the reference did not resolve from the citing document. ⛔ No hard count is
    carried here on purpose: the count rotted the moment this PR stopped adding an eighth `04-plan.md`,
    and the assertion's own message derives it.
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


def _call_window(source: str, marker: str) -> str:
    """The text of the FIRST call that opens at `marker`, up to its matching `)`.

    Exists so a source-shape pin can assert a property of ONE call instead of a whole function: `link_entities`
    is 270 lines carrying `label=label` at three sites, and `ingest` is 955 lines carrying it at four, so a
    function-wide `in` check stays green when the argument passed at the door is deleted.
    """
    start = source.index(marker)
    open_paren = start + len(marker) - 1
    assert source[open_paren] == "(", f"`{marker}` does not end at an open paren in this source"
    depth = 0
    for idx in range(open_paren, len(source)):
        char = source[idx]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return source[start: idx + 1]
    raise AssertionError(f"the call opened at `{marker}` never closes — the source this pin reads is malformed")


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
        """The number the brief's §2.1 write-path table and the epic's `04-plan.md` §5.3a point 3 both state:
        which write paths each declaration binds.

        ⚠️ **Not the same 33 as §1.1's.** §1.1's 33 is the **direct-shape** population (complement: the 2
        Point-involved rows); this pin's 33 is the **operator-path-only** population (complement:
        `hasPart`/`dependsOn`, which ALSO bind the structural path). The two sets differ on four rows, so
        citing §1.1 here would send a lane building pin 14's fixture to the wrong set — and omit exactly the
        two Point-bearing rows §1.1's own paragraph requires that arm to cover.

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

        # The miscount's source: `semantics: hasPart` rides SEVERAL DIFFERENT predicates — **five** rows over
        # **four** distinct predicates at this head (`decomposesInto`, `contains` ×2, `partOf`,
        # `partOfSprint`), which is the figure the scope §1.3 and the brief §3.2 both state. Pinned as a count
        # because both docs now assert it: a pack edit that changes it must red here rather than leave the
        # prose wrong.
        semantics_only = [r for r in _declared_relations()
                          if r.get("semantics") == "hasPart" and r.get("predicate") != "hasPart"]
        assert len(semantics_only) == 5, [r["predicate"] for r in semantics_only]
        assert {r["predicate"] for r in semantics_only} == {
            "decomposesInto", "contains", "partOf", "partOfSprint"}, sorted({r["predicate"] for r in semantics_only})

    def test_the_direct_shape_partition_28_object_and_33_non_point(self):
        """§1.1 / the brief's §2.4: **28** Object↔Object, **33** direct-shape, **2** Point-involved.

        ⚠️ This pins a figure that has already been got wrong once: the first revision said "32 of the 35 are
        Object→Object", which is the **non-`Subject`** count (35 − 3) relabelled — it absorbed the four
        non-`Subject` non-Object rows, and two of those are Point-involved. `_point_mediated_route` reifies
        only on `:Point` (`sdk.py:25058-25060`), so the direct-shape population is "not Point-involved", which
        is 33, not 32. An implementer building pin 14's fixture from the richest documented predicate
        (`addresses`, 6 declarations) would pick the `useCase→feature` row, watch it reify, and conclude the
        direct door is not the dominant shape.
        """
        registry = PackRegistry(PACKS_DIR)
        registry.load_all()

        axis: dict[str, str] = {}
        for pack in registry.packs.values():
            for attr, name in (("object_kinds", "object"), ("document_kinds", "object"),
                               ("event_kinds", "event"), ("point_kinds", "point")):
                for k in getattr(pack, attr, None) or []:
                    axis[f"{pack.namespace}:{k}"] = name
        from tortoise import pack_registry as pr

        for kind in pr.CORE_ENTITY_TYPES:
            axis[kind] = "subject"
        for const, name in ((pr.CANONICAL_OBJECT_KINDS, "object"),
                            (pr.CANONICAL_EVENT_KINDS, "event"),
                            (pr.CANONICAL_POINT_KINDS, "point")):
            for k in const:
                axis.setdefault(k if ":" in k else f"core:{k}", name)

        partition: Counter = Counter()
        for rel in _kind_bearing(_declared_relations()):
            a, b = axis.get(rel["fromKind"]), axis.get(rel["toKind"])
            assert a and b, (
                f"{rel['_ns']}:{rel['predicate']} names a kind whose axis is unknown "
                f"({rel['fromKind']} -> {rel['toKind']}) — the kind catalog moved; re-derive §1.1's partition"
            )
            partition[(a, b)] += 1

        assert partition[("object", "object")] == 28, partition
        assert partition[("object", "subject")] == 3, partition
        point_involved = partition[("object", "point")] + partition[("point", "object")]
        assert point_involved == 2, partition
        direct_shape = sum(n for (_a, b), n in partition.items() if b != "point" and _a != "point")
        assert direct_shape == 33, partition

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

    def test_a_layer_valued_declaration_binds_after_11000s_six_leg(self):
        """Pin 12 (§5.3): `toKind: Subject` must **bind**, not sit `unbound` forever.

        `Subject` is a layer (`pack_registry.CORE_ENTITY_TYPES`), not a kind, so a declaration naming it is
        only enforceable if the expansion table carries ONTOLOGY §6's core hierarchy. Before `#11000`'s §6
        leg landed this measured `['Subject']` — a single element — and every layer-valued declaration
        (§5.2: `assignedTo` twice, `holds`) was permanently `unbound`: never enforced, never warned about.

        This pin is deliberately **behavioural, not textual**: it asserts the expansion, so a regression of
        the §6 leg reds here instead of silently re-`unbound`ing the layer case while the prose still says
        it binds. Measured at this head: 12 kinds — six bare plus their six `core:` spellings.
        """
        registry = PackRegistry(PACKS_DIR)
        registry.load_all()

        expanded = registry.expand_kind("Subject")
        # The §6 subtypes a layer-valued declaration has to admit, spelled both ways.
        for kind in ("organization", "team", "role", "naturalPerson"):
            assert kind in expanded, (
                f"`{kind}` missing from expand_kind('Subject') — #11000's §6 leg regressed and every "
                f"`toKind: Subject` declaration (assignedTo ×2, holds) is `unbound` again"
            )
            assert f"core:{kind}" in expanded, f"`core:{kind}` spelling missing from expand_kind('Subject')"
        assert "Subject" in expanded, "INVARIANT (#6146): the argument is always in its own expansion"
        assert len(expanded) == 12, expanded

        # The direction D2 leans on, which #11000 Leg A added: a child carries its parent, transitively.
        issue = registry.expand_kind("dev:issue")
        assert "WorkItem" in issue, "#11000 Leg A regressed: `dev:issue` no longer carries its `subclassOf` parent"
        assert "Object" in issue, "upward closure is no longer transitive: `dev:issue` should reach `Object`"


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

        ⛔ **Every `label=label` assertion below is scoped to the `create_direct_edge(...)` CALL, not to the
        whole function.** `link_entities` is 270 lines with `label=label` at three sites and `ingest` is 955
        lines with it at four; a function-wide `in` check stays green when the pass-through at the door is
        deleted, which is the exact removal this pin exists to catch (review P2-4).
        """
        from tortoise.sdk import TortoiseSDK

        linking = inspect.getsource(TortoiseSDK.link_entities)
        assert 'upper in ("IMPL", "NAND") and operator is not True' in linking
        assert "label=label" in _call_window(linking, "self.create_direct_edge("), (
            "`link_entities` no longer passes the caller's `label` into `create_direct_edge` — the door would "
            "then be reachable with no declared predicate, and §1.1's 33-of-35 direct-shape count is void"
        )

        ingest = inspect.getsource(TortoiseSDK.ingest)
        assert '_route == "direct"' in ingest, "the bundle writer's direct route — the second public path"
        assert "label=label" in _call_window(ingest, "self.create_direct_edge("), (
            "`ingest`'s direct route no longer passes `label` into `create_direct_edge` — the second public "
            "path to the door is gone and the delta §1.1 counts would have to be re-derived"
        )

    def test_point_mediated_route_only_reifies_a_point_pair(self):
        """Object↔Object keeps the direct shape; the partition is pinned above (28 / 33 / 2)."""
        from tortoise.sdk import TortoiseSDK

        doc = inspect.getdoc(TortoiseSDK._point_mediated_route) or ""
        assert "Object↔Object" in doc and "operator-mediated" in doc
        assert "not Point-bearing" in doc, (
            "the docstring no longer says Subject/Event endpoints are not Point-bearing — §1.1's 33-of-35 rests "
            "on them taking the Object path"
        )

    def test_the_direct_door_has_its_OWN_replay_fold(self):
        """⛔ The P1 a fresh review found: there are **two** replay folds, not one.

        §1.2 of the scope listed only the operator fold (`_EdgeHandlers._create_edges`). `create_direct_edge`'s
        writes are replayed by the `DirectEdgeCreated` fold in `rebuild_all`'s pass-2b — a SEPARATE site that
        MERGEs the journaled rel type, so gating the door live and not here reproduces the
        `derived == replay(journal)` divergence on the door §1.1 calls dominant.

        ⛔ **Every assertion below is scoped to a WINDOW starting at the fold marker**, because the same
        strings occur elsewhere in this 17k-line module and a whole-file `in` check cannot fail: `MERGE
        (a)-[:{etype}]->(b)` also appears in the `DirectEdgeRepoint` leg, and `#9553 class 4` in two
        unrelated places. A file-wide assertion would stay green if the creation fold were deleted.

        The predicate is in a DIFFERENT key per producer: `edge_type` for a structural-lane record
        (`sdk.py:12142`), a `label` property for a `create_direct_edge` record (`sdk.py:29214`/`:29238`,
        restored through `DIRECT_EDGE_ATTR_KEYS`). That distinction is the scope's §1.2 item 6.
        """
        lines = (REPO_ROOT / "tortoise" / "projection" / "__init__.py").read_text().splitlines()
        starts = [i for i, ln in enumerate(lines) if "fold ``DirectEdgeCreated``" in ln]
        assert len(starts) == 1, f"expected exactly one DirectEdgeCreated fold marker, found {len(starts)}"
        # The window ENDS at the next leg: `DirectEdgeRepoint` reads `etype = ev.get("edge_type")`
        # too, at `:17306` (inside 400 lines of the marker). Without this bound the assertion
        # below could not fail — the copy it means to exclude satisfies it.
        ends = [i for i in range(starts[0], len(lines)) if "for ev in direct_repoint_events" in lines[i]]
        assert ends, "the DirectEdgeRepoint leg is gone — the window's end bound has to be re-derived"
        window = "\n".join(lines[starts[0]: ends[0]])

        assert "`ownedBy`/`hasPart`/" in window, (
            "the note naming the rels a structural-lane record carries is gone from the fold — that note is "
            "the evidence that they are DECLARED predicates (scope §1.2 item 6)"
        )
        assert 'f"MERGE (a)-[:{etype}]->(b)"' in window, (
            "the fold no longer writes a PARAMETERISED rel type — if it is a literal now, a declared predicate "
            "cannot reach it and scope §1.2 item 6 has to be re-derived"
        )
        assert 'etype = ev.get("edge_type")' in window, (
            "the fold no longer reads `edge_type` — re-derive where a replay record's predicate comes from"
        )
        assert "DIRECT_EDGE_ATTR_KEYS" in window, (
            "the fold no longer restores DIRECT_EDGE_ATTR_KEYS, so a create_direct_edge record's `label` "
            "property is not replayed — the second producer's predicate key would be lost"
        )


class TestThePairHasProductionReaders:
    """⛔ The review's P1: "exactly one place" is false — there are FOUR, and one already IS the pair→writer
    resolver the prevention half must reuse rather than rebuild."""

    def test_the_fourth_reader_is_the_pair_to_writer_resolver(self):
        """`pack_hierarchy._matching_row` matches on the DECLARED pair; `_binding` resolves the writer.

        §5.5's rule is "do not add a fourth resolver". The fourth *reader* is already that resolver: it joins a
        hierarchy entry to its `relations[]` row on `(fromKind, toKind)` (plus `predicate`/`semantics`).

        ⛔ **The chain, named correctly (review finding 6).** Production reach is
        `relation_write.apply_relation_edges` (`relation_write.py:791`; imported inside `_extract_session_v2` at
        `sdk.py:17200`, called at `:17201`) → `plan_relation_edges` (`:823`) → `declared_hierarchy` (`:594`) →
        `_resolve` (`pack_hierarchy.py:263`) → `_binding` (`:306`) + `_matching_row` (`:333`). The **join is in
        `_resolve`**, not in `plan_hierarchy_edges` (`relation_write.py:631`), which matches on the
        already-resolved `edge.from_kind`/`edge.to_kind`; and the symbol is **not** `sdk.apply_relation_edges` —
        `TortoiseSDK` has no such method, so a reader following the old sentence found an import.

        ⛔ **Every assertion below must be able to FAIL.** `callable(module_level_function)` is a tautology: it
        stayed green if the `_binding` call at `pack_hierarchy.py:306` or the `_matching_row` call at `:333`
        were deleted, while §5.5's "already IS the pair→writer resolver" became false (review finding 6, the
        same defect class as P2-4). So the two calls are pinned INSIDE `_resolve`'s own source, and the live
        path is pinned by the symbol that actually exists.
        """
        from tortoise import pack_hierarchy, relation_write
        from tortoise.sdk import TortoiseSDK

        source = inspect.getsource(pack_hierarchy._matching_row)
        assert 'rel.get("fromKind") != from_kind' in source, (
            "`_matching_row` no longer matches on the declared fromKind — re-derive §5.5's reader list"
        )
        assert 'rel.get("toKind") != to_kind' in source, "`_matching_row` no longer matches on the declared toKind"

        # ⛔ The resolver's own call sites, not `callable()` on the functions they call.
        resolver = inspect.getsource(pack_hierarchy._resolve)
        assert "_binding(pack, predicate, from_kind, to_kind)" in resolver, (
            "`_resolve` no longer calls `_binding` — the hierarchy entry is then resolved without a "
            "write-path verdict, and §5.5's 'the pair→writer resolver already exists' is false"
        )
        assert "_matching_row(" in resolver, (
            "`_resolve` no longer calls `_matching_row` — `restates_relation` would be a second, "
            "re-derived answer to the join this module exists to own"
        )

        # ⛔ The symbol the docs must name. `sdk.apply_relation_edges` does not exist; this does.
        assert callable(relation_write.apply_relation_edges), (
            "`tortoise.relation_write.apply_relation_edges` is gone — re-derive §5.5's live-path claim"
        )
        assert not hasattr(TortoiseSDK, "apply_relation_edges"), (
            "`TortoiseSDK.apply_relation_edges` now exists — the docs' old symbol has become real, so the "
            "§5.5 correction (and this pin) must be re-derived rather than left claiming it is absent"
        )
        capture = inspect.getsource(TortoiseSDK._extract_session_v2)
        assert "from tortoise.relation_write import apply_relation_edges" in capture, (
            "`_extract_session_v2` no longer imports `apply_relation_edges` — the hierarchy reader is then "
            "not on the capture write path at all, and §5.5's reachability claim has to be re-derived"
        )

        registry = PackRegistry(PACKS_DIR)
        registry.load_all()
        declared = pack_hierarchy.declared_hierarchy(registry)
        rows = sorted((d.pack, d.path, d.predicate) for d in declared)
        assert len(rows) == 6, (
            "the shipped packs declare a different number of hierarchies than §5.5 measured. The rows are "
            f"{rows} — re-derive the count in the scope and in `tools/pack_relation_census.py`'s census, "
            "do not just move the number here"
        )

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

        More than one file under `docs/` is named `04-plan.md`, so citing it bare from `docs/research/` is a
        reference that does not resolve — the exact defect the review found. The count is derived in the
        assertion message rather than written here, because a hard number in this docstring is the same
        rot this file's own header records (`31` → `33`, `three readers` → `four`).
        """
        basenames = Counter(p.name for p in (REPO_ROOT / "docs").rglob("*.md"))
        cited = set(re.findall(r"`([A-Za-z0-9_./-]+\.md)`", doc.read_text()))
        assert cited, "the doc cites no markdown path at all — the scanner is broken, not the doc"
        for token in sorted(cited):
            if "/" in token:
                target = (REPO_ROOT / token) if token.startswith("docs/") else (doc.parent / token)
                assert target.exists(), f"{doc.name} cites `{token}`, which does not resolve"
            else:
                assert basenames[token] == 1, (
                    f"{doc.name} cites `{token}` bare, but {basenames[token]} files under docs/ carry that "
                    "name — cite it with a path (0 means it resolves to nothing at all)"
                )
