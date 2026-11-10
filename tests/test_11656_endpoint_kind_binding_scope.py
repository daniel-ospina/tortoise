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

It also pins the three DEFECTS the review found in the docs themselves — all are cheap, mechanical, and would
have shipped because nothing reads prose:

  * neither #11656 doc was registered in `docs/00_index.md` (the repo's ledger — how the next lane finds a
    doc);
  * a cited `.md` was written bare where the basename is ambiguous (more than one file under `docs/` is
    named `04-plan.md`), so the reference did not resolve from the citing document. ⛔ No hard count is
    carried here on purpose: the count rotted the moment this PR stopped adding an eighth `04-plan.md`,
    and the assertion's own message derives it.
  * the two docs carried **line-keyed citations**, and the branch's own final `main` merge moved every one of
    the `.py:<line>` ones into unrelated code (`sdk.py` +44…+62, `projection/__init__.py` +128) while both docs
    asserted the numbers were verified at the merge head. They are now **symbol-keyed** (`path.py::symbol`;
    a manifest by YAML key path, `packs/*/manifest.yaml::key.path`), per this repo's recorded rule
    (`S2-DECISION-SEAM.md`: *"assertions by NAME — never by line number"*), and the ban below is what keeps a
    later edit from reintroducing the class. ⛔ The ban's first revision matched **`.py` only**, so it was
    green over two `packs/*/manifest.yaml:<line>` citations the brief still carried while its header said
    none was carried (review F2 at `09f20512`); it now covers `.py`, `.yaml`/`.yml`, `.md` and `.json`, and
    `test_every_cited_yaml_locator_resolves` holds the replacement locator form to a real key. ⛔ Same
    reasoning as above: no count is written into an assertion, but this header says 40 in the scope and 33 in
    the brief at `12040d1554c1` (re-derive with the ban's own pattern; that is the maximum across every commit
    that touches either doc), because that is the size of the defect the ban covers.
  * the ban asserted only that a `path.py::symbol`-shaped TOKEN was present, never that the symbol EXISTS —
    so **three** citation classes shipped green, in **nine** occurrences at `f54f40b0c072` (six in the scope,
    two in the brief, one in this file): a method cited on the wrong class, a module attribute that is only a
    function-local import, and a method cited as a module-level name.
    `test_every_python_symbol_citation_resolves` imports the module and resolves each citation by `getattr`,
    which is the whole reason the symbol form beats the line number: a name that does not resolve is the same
    dead end in a new costume.
"""
from __future__ import annotations

import ast
import importlib
import inspect
import os
import re
import sys
from collections import Counter
from pathlib import Path

import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: I001
from tortoise.pack_registry import (
    CORE_KINDS,
    CORE_SUBCLASSES,
    LEGACY_KIND_ALIASES,
    PackRegistry,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKS_DIR = REPO_ROOT / "packs"
SCOPE_DOC = REPO_ROOT / "docs" / "plans" / "2026-11-09-11656-endpoint-kind-binding.md"
BRIEF_DOC = REPO_ROOT / "docs" / "research" / "2026-11-09-11656-kindpair-enforcement.md"
INDEX_DOC = REPO_ROOT / "docs" / "00_index.md"

#: Roots whose `.py` files are cited but must NOT be imported here — importing a test module into a test
#: module (or a tool into the suite) is a side effect this pin does not need. A citation into one of these is
#: checked by NAME against the file's own AST instead, so an invented symbol still reds.
_NON_IMPORTABLE_ROOTS = ("tests", "tools")


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
        only on `:Point` (`sdk.py::TortoiseSDK._point_mediated_route`), so the direct-shape population is "not Point-involved", which
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
        # ⛔ No `len(cross) + len(layer) == 8` row here. The two exact sorted rows above FIX both lists, so
        # such a row is determined by them: any edit that makes it false has already reddened line 241 or
        # 242 first, and it can never be the failing row under any mutation (review F3 — a DEAD assertion in
        # a file whose header says every assertion below must be able to FAIL). The 5 + 3 = 8 arithmetic is
        # the message those two rows carry, not a third claim.

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

    def test_every_carries_example_resolves_to_a_declared_kind(self):
        """The brief's transitivity examples must name KINDS the expansion really carries (§4.3).

        Review P3-1 (PR #11671) found §4.3's downward example — *"`WorkItem` carries `dev:requirement`"* —
        false, and it had survived ten review rounds because nothing resolves prose. `dev:requirement` is
        **not a kind**: it is a READ-ONLY legacy spelling of `product-strategy:requirement`
        (`pack_registry.LEGACY_KIND_ALIASES`), so it shows up inside `expand_kind("WorkItem")` without ever
        being declared — which is exactly how the sentence looked true. The row that does exist,
        `requirement: WorkItem`, is a **direct child**, so "grandchild" was wrong too.

        This pin resolves EVERY `` `X` carries `Y` `` example in the brief against the registry, the same way
        `test_every_cited_markdown_path_resolves` resolves its citations, so a wrong example reds here
        instead of rotting in prose. The depth half of the finding is
        `test_the_downward_example_is_a_pack_kind_two_levels_below_a_core_kind`.
        """
        registry = PackRegistry(PACKS_DIR)
        registry.load_all()
        declared = {kind for kinds in registry.list_all_kinds().values() for kind in kinds} | set(CORE_KINDS)
        legacy_spellings = {s for spellings in LEGACY_KIND_ALIASES.values() for s in spellings}

        examples = re.findall(r"`([A-Za-z][\w:-]*)` carries `([A-Za-z][\w:-]*)`", BRIEF_DOC.read_text())
        assert examples, (
            "the brief makes no `X` carries `Y` transitivity example at all — §4.3's job is to state the "
            "closure WITH an example, so a scanner that finds none is asserting nothing"
        )
        for parent, child in examples:
            assert child not in legacy_spellings, (
                f"the brief says `{parent}` carries `{child}`, but `{child}` is a READ-ONLY legacy spelling "
                f"({sorted(legacy_spellings)}), not a declared kind: it appears in an expansion without being "
                "a kind, which is how review P3-1's `dev:requirement` example read as true"
            )
            assert child in declared, (
                f"the brief says `{parent}` carries `{child}`, but no pack or CORE row declares `{child}`"
            )
            assert child in registry.expand_kind(parent), (
                f"the brief says `{parent}` carries `{child}`, but the expansion does not carry it — "
                f"measured {sorted(registry.expand_kind(parent))}"
            )

    def test_the_downward_example_is_a_pack_kind_two_levels_below_a_core_kind(self):
        """§4.3's corrected downward example — `Object` → `Project` → `dev:epic` — is really two levels.

        The finding's second half: the old example was one level (`requirement: WorkItem`), presented as
        "grandchildren". This pin holds the replacement to the depth the sentence claims: the grandchild is
        a PACK kind, its declared parent is a CORE kind, and that parent is itself a child of the kind the
        sentence starts from — so it cannot be restated as a direct-child example later either.
        """
        registry = PackRegistry(PACKS_DIR)
        registry.load_all()

        # The chain the sentence names, one link at a time.
        assert "Project" in CORE_SUBCLASSES["Object"], (
            "`Project` is no longer a CORE child of `Object` — §4.3's corrected example lost its middle link"
        )
        assert "dev:epic" in registry.expand_kind("Project"), (
            "`dev:epic` is no longer carried by `Project` (`packs/dev/manifest.yaml` `subclassOf`)"
        )
        assert "dev:epic" in registry.expand_kind("Object"), (
            "downward closure is no longer transitive: `Object` should reach the PACK kind `dev:epic`"
        )
        # …and NOT a direct child of `Object`, which is what makes it a grandchild rather than a restatement.
        assert "dev:epic" not in CORE_SUBCLASSES["Object"], (
            "`dev:epic` is now a DIRECT child of `Object`, so §4.3's example no longer demonstrates two levels"
        )


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
        # ⛔ LEADING NEWLINE, not a bare substring. `sdk.py` also carries
        # `if _request_route == "direct" and _route == "operator":` — `_request_route` ENDS IN `_route`,
        # so the needle matches INSIDE it (not at the end of the line) — so a bare `in` check stayed green
        # with the real branch deleted, which is
        # the file's own F3 rule ("a weaker assertion beside a behavioural one, whose message claims
        # precedence it cannot earn"). The anchor pins the branch at a line start.
        assert '\n                if _route == "direct":\n' in ingest, (
            "the bundle writer's direct route — the second public path — no longer branches on "
            "`_route == \"direct\"` at a statement boundary. A substring test here is satisfied by the "
            "`_request_route` decoy above it, which is why this anchor is newline-delimited"
        )
        assert "label=label" in _call_window(ingest, "self.create_direct_edge("), (
            "`ingest`'s direct route no longer passes `label` into `create_direct_edge` — the second public "
            "path to the door is gone and the delta §1.1 counts would have to be re-derived"
        )

    def test_point_mediated_route_docstring_states_the_point_only_rule(self):
        """⚠️ Pins the DOCSTRING, not the behaviour — named so the title cannot promise more.

        A mutant that made `_point_mediated_route` reify an `Object↔Object` pair would keep this row green; the
        behavioural half of §1.1's claim is `test_the_direct_shape_partition_28_object_and_33_non_point` above
        (28 / 33 / 2). This row exists because §1.1's *reading* of the code is what the scope quotes, and a
        silent docstring edit would change what a lane believes without reddening anything else (review P3).
        """
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
        (the journal call in `sdk.py::TortoiseSDK._record_structural_edge`), a `label` property for a
        `create_direct_edge` record (`sdk.py::TortoiseSDK.create_direct_edge`,
        restored through `DIRECT_EDGE_ATTR_KEYS`). That distinction is the scope's §1.2 item 6.
        """
        lines = (REPO_ROOT / "tortoise" / "projection" / "__init__.py").read_text().splitlines()
        starts = [i for i, ln in enumerate(lines) if "fold ``DirectEdgeCreated``" in ln]
        assert len(starts) == 1, f"expected exactly one DirectEdgeCreated fold marker, found {len(starts)}"
        # The window ENDS at the next leg: `DirectEdgeRepoint` reads `etype = ev.get("edge_type")`
        # too, in the leg this bound below identifies by the same string it reads
        # (`for ev in direct_repoint_events`). Cited by SIGN, never by line number: a `:<line>` here
        # was 127 lines stale on the commit that introduced it and pointed INSIDE the window it claims
        # to exclude (round-17 P2-2). Without this bound the assertion below could not fail — the copy
        # it means to exclude satisfies it.
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
        `relation_write.apply_relation_edges` (`relation_write.py::apply_relation_edges`; imported and called
        inside `sdk.py::TortoiseSDK._extract_session_v2`) → `plan_relation_edges` → `declared_hierarchy` →
        `_resolve` (`pack_hierarchy.py::_resolve`) → `_binding` (`pack_hierarchy.py::_binding`) + `_matching_row`
        (`pack_hierarchy.py::_matching_row`). The **join is in
        `_resolve`**, not in `plan_hierarchy_edges` (`pack_hierarchy.py::plan_hierarchy_edges`), which matches on the
        already-resolved `edge.from_kind`/`edge.to_kind`; and the symbol is **not** `sdk.apply_relation_edges` —
        `TortoiseSDK` has no such method, so a reader following the old sentence found an import.

        ⛔ **Every assertion below must be able to FAIL.** `callable(module_level_function)` is a tautology: it
        stayed green if the `_binding` call at `pack_hierarchy.py::_binding` or the `_matching_row` call at `pack_hierarchy.py::_matching_row`
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

    def test_the_readers_exist_and_the_catalog_carries_the_pair(self, monkeypatch):
        """§5.5 readers 1 and 3, asserted as CALLS — not as `callable()` plus a source substring.

        ⛔ **Review F2: §5.5 says FOUR readers and this file pinned two of them, part of a third.**
        `callable(module_level_fn)` is a tautology, and `'"fromKind"' in source` cannot tell the
        `ontology.relations` leg from the `extraction.relationTemplates` leg (each alone satisfies it), so
        deleting EITHER `_pair(...)` call stayed green. And `domain_validators._portfolio_mark_contract` —
        the reader plan §5.2 calls the load-bearing one, the sentence §6 pin 10 is justified by — was not
        mentioned in this file at all while the `ci-surfaces.yml` entry claimed the four were pinned. Both
        are now pinned by calling them, and `test_resolve_traversal_path_…` below pins reader 2.
        """
        from tortoise import domain_validators
        from tortoise.value_extractor import _relation_endpoint_refs

        # ── Reader 3: `value_extractor._relation_endpoint_refs` ───────────────────────────────────────
        # BEHAVIOURAL: one relation + one relationTemplate in, the UNDIRECTED kind→kind adjacency out for
        # BOTH declaration sites. Reds if either `_pair(...)` line is deleted (the other leg's `fromKind`
        # literal is still in the source, which is exactly why the substring check could not fail), and reds
        # if the closure stops being undirected or stops including same-namespace partners.
        raw = {
            "ontology": {"relations": [{"fromKind": "a:x", "toKind": "b:y", "predicate": "p"}]},
            "extraction": {"relationTemplates": [{"fromKind": "a:m", "toKind": "c:n"}]},
        }
        assert _relation_endpoint_refs(raw) == {
            "a:x": ["b:y"], "b:y": ["a:x"], "a:m": ["c:n"], "c:n": ["a:m"],
        }, (
            "`_relation_endpoint_refs` no longer collects from BOTH declaration sites — §5.5 reader 3, whose "
            "output decides which kinds compact mode offers together"
        )

        # ── Reader 1: `domain_validators._portfolio_mark_contract` ────────────────────────────────────
        # §5.2 makes `portfolio:marks` the load-bearing case BECAUSE this reader reads it. The
        # manifest-edit-FOLLOWS behaviour is
        # `tests/test_investment_portfolio_manager_pack.py::test_the_mark_contract_follows_a_manifest_edit`;
        # what this file needs is the claim its own comment makes — that the reader is still there and still
        # answers from the declaration rather than from the constants it falls back to.
        # ⚠️ No `callable(...)` guard here: it is the weaker twin of the assertion below, and this file's own
        # rule (stated in `test_the_fourth_reader_is_the_pair_to_writer_resolver`) is that a weaker assertion
        # beside a behavioural one is the F3 tautology class. If the symbol vanished, the call below raises.
        mark_row = next(
            r for r in _declared_relations()
            if r["_ns"] == "portfolio" and r.get("predicate") == "marks"
        )
        assert domain_validators._portfolio_mark_contract() == (
            mark_row["fromKind"], mark_row["toKind"], mark_row["predicate"],
        ), (
            "`_portfolio_mark_contract` no longer answers with the portfolio pack's own `marks` declaration "
            "(`portfolio:valuationMark → venture:investment`) — §5.2's load-bearing case and §6 pin 10 rest "
            "on this reader reading the manifest"
        )
        # ⛔ **The derivation, not the fallback — and deliberately no `"_pack_relations()" in source`
        # check beside it.** A source-text version of this was tried first and mutation 4a (an early
        # `return MARK_KIND, POSITION_KIND, MARK_RELATION` placed BEFORE the loop) stayed GREEN under it,
        # because the loop's text is still present and unreachable. A weaker assertion beside a
        # behavioural one — whose message claims precedence it cannot earn — is the F3 class this round
        # removed elsewhere in this file, so it is not kept "for completeness". Swapping the pack's `marks`
        # row must MOVE the answer (same mechanism
        # `test_investment_portfolio_manager_pack.py::test_the_mark_contract_follows_a_manifest_edit` pins in
        # its own suite; here it is what makes §5.5's "reuse it" claim — and §6 pin 10 — non-vacuous).
        monkeypatch.setattr(
            domain_validators,
            "_pack_relations",
            lambda: [{"predicate": "marksPosition", "fromKind": "portfolio:valuationMark",
                      "toKind": "venture:position"}],
        )
        assert domain_validators._portfolio_mark_contract() == (
            "portfolio:valuationMark", "venture:position", "marksPosition",
        ), (
            "`_portfolio_mark_contract` no longer FOLLOWS the declaration — it is answering from the "
            "module constants, so a manifest edit to `marks` desynchronises the rule silently and §5.5's "
            "'reuse the reader that already exists' becomes a re-derivation with nothing red"
        )

        registry = PackRegistry(PACKS_DIR)
        registry.load_all()
        rows = registry.list_relations()
        assert any(r.get("fromKind") and r.get("toKind") for r in rows), (
            "`list_relations` must expose the pair — `_resolve_traversal_path` and `_relation_endpoint_refs` "
            "read it from here, so a scope that calls the pair map net-new is re-implementing this"
        )

    def test_resolve_traversal_path_is_a_kind_pair_to_predicate_resolver(self):
        """It IS `allowed_relations(from_kind, to_kind)` in production (scope §5.5 reader 2) — CALLED.

        ⛔ **Review F2: the existence checks this replaces stayed green over `return None` inserted as the
        first statement of the body**, while §5.5's "it IS `allowed_relations(...)` in production" became
        false. So the resolver is exercised on a REAL declared pair and its answer is held to the catalog:
        the comment must be a declared `(fromKind, toKind, predicate)` row, and its bare kind suffixes must
        be the ones the path names.
        """
        from tortoise.sdk import TortoiseSDK

        method = TortoiseSDK._resolve_traversal_path
        assert method.__doc__ and "predicate" in method.__doc__
        source = inspect.getsource(method)
        assert "list_relations" in source
        assert "fromKind" in source

        # `__new__` rather than the constructor: the resolver touches no `self` attribute and needs no DB,
        # so this exercises the real bound method without building a graph.
        resolved = TortoiseSDK.__new__(TortoiseSDK)._resolve_traversal_path("Product→Feature")
        assert resolved, (
            "`_resolve_traversal_path('Product→Feature')` resolved nothing — §5.5's reader 2 is then not a "
            "kind-pair→predicate resolver at all, and D5 would have to be implemented twice"
        )
        assert set(resolved) >= {"predicate", "fromKind", "toKind"}, resolved

        registry = PackRegistry(PACKS_DIR)
        registry.load_all()
        declared = {
            (r.get("fromKind"), r.get("toKind"), r.get("predicate"))
            for r in registry.list_relations()
        }
        assert (resolved["fromKind"], resolved["toKind"], resolved["predicate"]) in declared, (
            f"the resolver returned {resolved!r}, which is NOT a declared relation — it must answer from the "
            "catalog (§5.5 reader 2), not from a re-derived pair"
        )
        assert resolved["fromKind"].split(":")[-1].lower() == "product", resolved
        assert resolved["toKind"].split(":")[-1].lower() == "feature", resolved


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

    def test_the_stated_pin_population_matches_this_file(self):
        """The scope's §6 note states THIS file's population; that figure has already gone stale once.

        It said "20 test functions / 21 collected" while the file held 22/23 — the review's F2, one of two
        shipped sites carrying it (the other is this file's `config/ci-surfaces.yml` registration comment).
        A hand-maintained count rots the moment a test is added, which is precisely what happened: the
        commit that made it stale added the twelfth errata row in a *different* file. So the count is
        DERIVED here and both stated copies are held to it — a new test that forgets to update them reds
        this row instead of shipping a false coverage claim. The parenthetical that EXPLAINS the delta is
        held to the same derivation: it said "(one is parametrised over the two docs)" against 25→27, a
        reason that contradicts the two integers beside it while no integer-only assertion could see it
        (round-17 P2-1).
        """
        source = Path(__file__).read_text()
        # pytest collects MODULE-LEVEL functions and CLASS methods — never a nested `def`. Counting with
        # `^    def test_` silently assumed every test is a 4-space-indented method, so a module-level
        # `def test_...` left this pin GREEN while the file's real population grew (review round 19, P3):
        # the pin would then be certifying a coverage figure it had under-counted. Derive the population the
        # way the collector does, not the way the file happens to be indented today.
        tree = ast.parse(source)
        functions = 0
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
                functions += 1
            elif isinstance(node, ast.ClassDef):
                functions += sum(
                    1
                    for member in node.body
                    if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and member.name.startswith("test_")
                )
        # `collected` = functions + the extra cases of every `@pytest.mark.parametrize` arm.
        extra = sum(
            len(cases.split(",")) - 1
            for cases in re.findall(r"@pytest\.mark\.parametrize\(\"[a-z_]+\", \[([^\]]+)\]", source)
        )
        collected = functions + extra

        scope = SCOPE_DOC.read_text()
        stated = set(re.findall(r"\*\*(\d+) test functions / (\d+) collected\*\*", scope))
        assert len(stated) == 1, (
            f"the scope must state this file's population exactly once as **N test functions / M collected**; "
            f"found {sorted(stated)} — update it in the same commit that changes the count"
        )
        stated_f, stated_c = (int(x) for x in stated.pop())
        assert (stated_f, stated_c) == (functions, collected), (
            f"{SCOPE_DOC.name} says **{stated_f} test functions / {stated_c} collected**; this file holds "
            f"{functions} / {collected}. The doc's number is what a reader uses to judge coverage."
        )

        # The parenthetical must also be TRUE, not merely present: it exists to explain the delta, so the
        # count it states is derived from this file's own decorators and held to the delta it explains.
        doc_arms = len(re.findall(r'@pytest\.mark\.parametrize\("doc", \[SCOPE_DOC, BRIEF_DOC\]', source))
        assert collected - functions == doc_arms, (
            f"this file collects {collected} from {functions} functions but holds {doc_arms} doc-parametrised "
            "tests — the delta is no longer explained by the arms the doc's parenthetical names"
        )
        numwords = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5}
        stated_arms = re.findall(
            r"\(([a-z]+|\d+) (?:is|are) parametrised over the two docs[^)]*\)", scope
        )
        assert len(stated_arms) == 1, (
            "the scope must explain the delta exactly once as `(N is/are parametrised over the two docs)`; "
            f"found {stated_arms} — update it in the same commit that changes the arms"
        )
        word = stated_arms[0]
        stated_n = int(word) if word.isdigit() else numwords.get(word)
        assert stated_n == doc_arms, (
            f"{SCOPE_DOC.name} says **{word}** test(s) are parametrised over the two docs; this file holds "
            f"{doc_arms} ({collected} collected from {functions} functions). A wrong count here contradicts "
            "the two integers in the same sentence and sends a merger to the wrong coverage figure."
        )

        surfaces = (REPO_ROOT / "config" / "ci-surfaces.yml").read_text()
        assert f"{functions} test functions / {collected} collected" in surfaces, (
            "this file's `config/ci-surfaces.yml` registration comment carries a different population than the "
            f"file itself ({functions} / {collected}) — F2 named both sites, so both are held here"
        )

    def test_every_cited_yaml_locator_resolves(self):
        """A manifest citation is a YAML **key path**, and it is resolved against the manifest itself.

        The brief carried two manifest citations as line ranges (`packs/dev/manifest.yaml:33-35`,
        `packs/product-strategy/manifest.yaml:21`) while its own header asserted no line number was carried,
        and the ban below could not see either one because it matched `.py` only (review F2 at `09f20512`).
        A pack edit inserted above the cited line re-stales the claim silently and green — the exact rot this
        PR removed for python citations — so both citations now name a key path, and this row walks the
        loaded manifest to that key: a moved or renamed key reds here instead of leaving a reader with a
        pointer into a different declaration.
        """
        citations = []
        for doc in (SCOPE_DOC, BRIEF_DOC):
            for rel, keypath in re.findall(
                r"`(packs/[A-Za-z0-9_./-]+\.ya?ml)::([A-Za-z0-9_.\-\[\]]+)`", doc.read_text()
            ):
                citations.append((doc, rel, keypath))
        assert citations, (
            "neither doc cites a manifest by key path (`packs/…/manifest.yaml::key.path`) — either the "
            "scanner is broken or the manifest citations were deleted rather than re-keyed"
        )
        for doc, rel, keypath in citations:
            manifest = REPO_ROOT / rel
            assert manifest.exists(), f"{doc.name} cites `{rel}`, which does not resolve"
            node = yaml.safe_load(manifest.read_text())
            walked = ""
            for part in keypath.split("."):
                walked = f"{walked}.{part}" if walked else part
                assert isinstance(node, dict) and part in node, (
                    f"{doc.name} cites `{rel}::{keypath}` but `{walked}` is not a key in that manifest — a "
                    "key path that names nothing sends the reader to a different declaration"
                )
                node = node[part]

    @pytest.mark.parametrize("doc", [SCOPE_DOC, BRIEF_DOC], ids=["scope", "brief"])
    def test_neither_doc_cites_a_line_keyed_reference(self, doc):
        """A `file:<line>` citation is not a citation — it rots on the next `main` merge.

        Measured, not assumed: this branch's own final `main` merge (`59e27baa`) shifted `sdk.py` by +44 in
        the bulk and by +56/+62 around `_resolve_traversal_path`, and `projection/__init__.py` by +128 — so a
        reader cannot even shift by a constant, and the offsets differ per file. Both docs nonetheless stated
        the numbers were *"verified at the same merged head"*. 24 of them pointed at unrelated code: a lane
        implementing #11656 opened `create_direct_edge`'s citation and landed in `list_batch`'s docstring.

        The repo's recorded convention is the opposite of line-keyed (`docs/architecture/S2-DECISION-SEAM.md`:
        *"assertions by NAME — never by line number"*; `tortoise/hosted_api.py`: *"Cited by SYMBOL, not line
        number"*), and nothing read these two docs' numbers, so the rot was silent. Fixing one number would
        re-rot on the next merge; this ban removes the class. The count is DERIVED for the message, never
        hard-coded in the assertion — a hard number here is the same rot in a new costume.

        ⛔ The ban's first revision matched `.py` only, and the brief was green under it while carrying two
        `packs/*/manifest.yaml:<line>` citations *and* a header claiming none was carried (review F2 at
        `09f20512`). A manifest or `.md` line number rots identically, so the class is closed for every file
        type these docs cite; the replacement locator form is held by
        `test_every_cited_yaml_locator_resolves` above.
        """
        text = doc.read_text()
        offenders = re.findall(r"[A-Za-z0-9_./-]+\.(?:py|ya?ml|md|json):[0-9]+(?:-[0-9]+)?", text)
        assert not offenders, (
            f"{doc.name} carries {len(offenders)} line-keyed citation(s) "
            f"({sorted(set(offenders))}). Cite the SYMBOL instead (`path.py::symbol`; a manifest by YAML key "
            "path, `packs/*/manifest.yaml::key.path`): a line number rots on the next `main` merge and sends "
            "the reader to unrelated code."
        )
        # The ban must not be vacuous in the other direction either: the doc DOES cite python files, by symbol.
        symbols = re.findall(r"`[A-Za-z_/]+\.py::[A-Za-z_.]+", text)
        assert symbols, (
            f"{doc.name} cites no python symbol at all — the doc's mechanism is code citations, so either "
            "the scanner is broken or the citations were deleted rather than re-keyed"
        )

    def test_every_python_symbol_citation_resolves(self):
        """`path.py::symbol` beats a line number ONLY if the symbol exists — so resolve it, do not pattern-match it.

        The ban above closes `file:<line>` and names `path.py::symbol` as the replacement, but for three
        revisions it asserted only that SOME symbol-shaped token was present (`assert symbols`). A wrong or
        invented symbol therefore shipped GREEN — and did. Measured at `f54f40b0c072`, the head this row was
        added to: **nine** occurrences across this PR's three artifacts (six in the scope, two in the brief,
        one in this file), spanning **three** distinct citation classes:

          * `projection/entities.py::_EdgeHandlers._upsert_point_edges` — `_upsert_point_edges` is a method of
            `_EntityHandlers`; `edges.py`'s `_EdgeHandlers` has no such attribute.
          * `relation_write.py::plan_hierarchy_edges` — defined in `pack_hierarchy.py` and imported INSIDE
            `plan_relation_edges`, so `relation_write.plan_hierarchy_edges` does not exist.
          * `projection/__init__.py::_rebuild_all_replay` — a method of `FalkorProjection`, not a module-level
            name.

        Two were found by a reviewer opening the citation; the third was found by the first run of this row —
        which is the asymmetry this row removes. It is the same defect the errata table records for `sdk.apply_relation_edges`: a
        locator that looks like a citation, reads like a citation, and sends the reader nowhere.

        ⛔ A file that does not exist is a FAILURE, not a skip. An earlier revision folded "not a `tortoise/`
        module" and "no such file" into one `None`, so an invented module (`sdkk.py::…`) passed GREEN — the
        dead end this row exists to close, one level up. A `tests/` or `tools/` citation is checked by NAME
        against that file's own AST instead of being imported (importing a test module into a test module is a
        side effect this pin does not need), so an invented test symbol reds too.
        """

        def _citation_path(rel: str) -> Path:
            """The file a `path.py::symbol` citation names.

            A bare `sdk.py` is relative to `tortoise/` (that is how both docs write a `tortoise/` module); an
            explicit `tortoise/…` or a non-production root (`tests/`, `tools/`) is repo-relative.
            """
            if rel.startswith("tortoise/") or rel.split("/", 1)[0] in _NON_IMPORTABLE_ROOTS:
                return REPO_ROOT / rel
            return REPO_ROOT / "tortoise" / rel

        def _module_for(path: Path) -> str | None:
            """The import path for a citation's file, or `None` when the file is not importable as `tortoise.*`."""
            rel = path.relative_to(REPO_ROOT).as_posix()
            if not rel.startswith("tortoise/"):
                return None
            p = rel[len("tortoise/") :]
            if p == "__init__.py":
                return "tortoise"
            if p.endswith("/__init__.py"):
                return "tortoise." + p[: -len("/__init__.py")].replace("/", ".")
            return "tortoise." + p[: -len(".py")].replace("/", ".")

        def _ast_declares(path: Path, symbol: str) -> bool:
            """True when `symbol` resolves down `path`'s AST the way `getattr` resolves against a module.

            The first part must be declared at MODULE level; each later part must be declared in the body of
            the `ClassDef` the previous part resolved to. Flattening every name in the file into one set —
            the first revision of this helper — accepts `SomeOtherClass.method` and even a function-local
            variable, which is the same wrong-qualifier defect (`_EdgeHandlers._upsert_point_edges`) this
            row exists to catch, so class qualification is resolved rather than erased.
            """

            def _scope(body: list[ast.stmt]) -> dict[str, dict]:
                declared: dict[str, dict] = {}
                for node in body:
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        declared[node.name] = {}
                    elif isinstance(node, ast.ClassDef):
                        declared[node.name] = _scope(node.body)
                    elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                        for target in targets:
                            if isinstance(target, ast.Name):
                                declared[target.id] = {}
                return declared

            scope = _scope(ast.parse(path.read_text()).body)
            for part in symbol.split("."):
                if part not in scope:
                    return False
                scope = scope[part]
            return True

        unresolved = []
        for doc in (SCOPE_DOC, BRIEF_DOC):
            found = sorted(
                set(
                    re.findall(
                        r"`([A-Za-z0-9_./-]+\.py)::([A-Za-z_][A-Za-z0-9_.]*)`", doc.read_text()
                    )
                )
            )
            for rel, symbol in found:
                # `path.py::symbol` is the citation FORM, quoted as prose in both docs — not a citation.
                if (rel, symbol) == ("path.py", "symbol"):
                    continue
                # ⛔ The FILE is resolved FIRST, and a file that does not exist is a failure.
                # An earlier revision folded "not a `tortoise/` module" and "no such file" into one
                # `None` and skipped both, so an invented module (`sdkk.py::…`) passed green — the very
                # dead end this row exists to close, one level up. A missing file is not a scope decision.
                path = _citation_path(rel)
                if not path.exists():
                    unresolved.append(f"{doc.name}: `{rel}::{symbol}` — no such file `{rel}`")
                    continue
                module_name = _module_for(path)
                if module_name is None:
                    if not _ast_declares(path, symbol):
                        unresolved.append(
                            f"{doc.name}: `{rel}::{symbol}` — `{path.name}` declares no `{symbol}`"
                        )
                    continue
                node = importlib.import_module(module_name)
                try:
                    for part in symbol.split("."):
                        node = getattr(node, part)
                except Exception as exc:  # AttributeError in practice; an import hole surfaces too
                    unresolved.append(
                        f"{doc.name}: `{rel}::{symbol}` does not resolve in `{module_name}` "
                        f"({type(exc).__name__})"
                    )
        assert not unresolved, (
            "these citations name a symbol that does not exist, so a lane implementing the issue opens the "
            "module and lands nowhere — cite the symbol that actually holds the behaviour (or the class that "
            "holds the method):\n  " + "\n  ".join(unresolved)
        )

    def test_this_pin_carries_no_line_keyed_citation(self):
        """The file that SHIPS the ban must obey the SAME ban the docs do — not a weaker one.

        Round-17 P2-2, measured: this file explained its own window bound with a bare line number that was
        **127 lines stale on the commit that introduced it**, and wrong in the most misleading direction
        available — it named a location *inside* the window the bound claims to exclude, so a reader trying
        to verify the bound landed on unrelated operator-cleanup code. The class is exactly the one the ban
        above closes for the two docs; it shipped here because nothing scanned this file.

        ⛔ Round-18 P3: the first revision of THIS row matched only a backticked bare `` `:NNN` ``, while the
        docs' ban matches `path.ext:NNN` — so this file was held to a strictly weaker ban and carried two
        `packs/*/manifest.yaml:<line>` citations (quoted below as the defect record) that its own row could
        not see. Both patterns are now scanned, and the two live historical citations are listed explicitly
        rather than hiding in a blind spot: this is a DECLARED allowlist, so a NEW one reds.
        """
        #: Citations quoted as the RECORD of a defect already fixed, not live locators. A count here is a
        #: claim about prose, so it is named rather than pattern-excluded.
        historical = {
            "packs/dev/manifest.yaml:33-35",
            "packs/product-strategy/manifest.yaml:21",
        }
        source = Path(__file__).read_text()
        offenders = re.findall(r"`:\d{3,5}`", source)
        offenders += [
            c for c in re.findall(r"[A-Za-z0-9_./-]+\.(?:py|ya?ml|md|json):[0-9]+(?:-[0-9]+)?", source)
            if c not in historical
        ]
        assert not offenders, (
            f"this pin carries {len(offenders)} line-keyed citation(s) {sorted(set(offenders))}. Cite the "
            "SYMBOL, or the literal string an assertion already reads (`for ev in direct_repoint_events`): "
            "a line number rots on the next `main` merge and lands the reader in unrelated code. If it is the "
            "record of a defect already fixed, add it to the `historical` allowlist above so the exception is "
            "declared rather than invisible."
        )
