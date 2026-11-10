"""#11679 — the kind hierarchy has TWO surfaces, and they point opposite ways.

`PackRegistry.expand_kind` is a **membership** set: it answers "is this kind a
`<parent>`?" and therefore carries a child's ANCESTORS (`dev:issue` →
`WorkItem`, `Object`). A **read filter** asks the other question — "rows whose
stored kind is this kind or NARROWER" — so `n.pointKind IN [...]` fed the
ancestor names and a narrow filter matched broader-kinded rows. Measured at
`d26f3c6d` (PR #11667, review comment 6755108771):

    expand_kind("product-strategy:feature") = ['product-strategy:feature',
                                              'Object', 'WorkItem',
                                              'core:Object', 'core:WorkItem']

so `search(kind="product-strategy:feature", entity_type="object")` returned
every object the classifier had labelled `core:WorkItem`.

The fix splits the table: `expand_kind` keeps the UP direction (unchanged — the
membership predicate still needs it), and `read_filter_kinds` is the DOWN set
the three `sdk` filter sites and `domain_validators._expand` now use.

Both directions are pinned here on purpose: a test that only fails when the
widening is removed is half a guard (it would pass if the filter set became
empty), and a test that only fails on over-widening passes the original bug.
"""
from __future__ import annotations

import contextlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tortoise.pack_registry import (
    CORE_SUBCLASSES,
    PackRegistry,
)

PACKS_DIR = Path(__file__).resolve().parents[1] / "packs"


@pytest.fixture(scope="module")
def shipped() -> PackRegistry:
    """The 7 shipped packs, loaded once."""
    registry = PackRegistry(str(PACKS_DIR))
    assert registry.load_all() == 7, registry.errors
    assert not registry.errors, registry.errors
    return registry


def _declared_pairs(registry: PackRegistry) -> list[tuple[str, str]]:
    """Every (child, parent) the manifests and ONTOLOGY §6 declare.

    Derived from the loaded registry, so a newly declared pair is covered
    without touching this file and the population cannot pass vacuously.
    """
    pairs = [(f"{p.namespace}:{child}", parent)
             for p in registry.packs.values()
             for child, parent in p.kind_subclasses.items()]
    pairs += [(child, parent)
              for parent, children in CORE_SUBCLASSES.items()
              for child in children]
    return pairs


class TestReadFilterDirection:
    """#11679 — a read filter is DOWN only; the membership set keeps UP."""

    def test_a_read_filter_never_carries_an_ancestor(self, shipped):
        """THE defect. Every declared parent is absent from its child's filter.

        Both spellings are checked, because rows are STORED under the
        classifier's `core:` spelling (`extractor_v2.CORE_OBJECT_KEYS`) — a
        filter that drops only the bare parent name still matches those rows.
        """
        pairs = _declared_pairs(shipped)
        assert len(pairs) >= 21, f"declared pairs went missing: {pairs}"

        leaked: list[tuple[str, str, list[str]]] = []
        for child, parent in pairs:
            bucket = shipped.read_filter_kinds(child)
            if parent in bucket:
                leaked.append((child, parent, bucket))
            core_parent = parent if parent.startswith("core:") else f"core:{parent}"
            if core_parent in bucket:
                leaked.append((child, core_parent, bucket))
        assert not leaked, (
            "a read filter carries an ANCESTOR, so `n.pointKind IN [...]` "
            "matches rows BROADER than the kind asked for (#11679): "
            f"{leaked}")

    def test_the_membership_set_still_answers_up(self, shipped):
        """The other half of the guard: the fix must not narrow `expand_kind`.

        Same population, opposite direction. If someone "fixes" #11679 by
        removing the UP walk from the shared table, this fails — the
        `"operator" in expand_kind(kind)` membership idiom needs it.
        """
        pairs = _declared_pairs(shipped)
        missing = [(child, parent, shipped.expand_kind(child))
                   for child, parent in pairs
                   if parent not in shipped.expand_kind(child)]
        assert not missing, (
            "the membership set lost a declared parent — the UP direction was "
            f"removed instead of split off (#11679): {missing}")

    def test_a_filter_keeps_every_declared_descendant(self, shipped):
        """Nothing may be dropped that is not an ancestor: DOWN survives.

        For every declared pair the PARENT's filter must contain the CHILD (and
        the child's stored `core:` spelling), otherwise `query(kind="Object")`
        would stop returning pack Objects — the wrong-empty #6146 forbids.
        """
        pairs = _declared_pairs(shipped)
        missing = []
        for child, parent in pairs:
            bucket = shipped.read_filter_kinds(parent)
            if child not in bucket:
                missing.append((parent, child))
        assert not missing, (
            "a read filter dropped a declared DESCENDANT — a parent-kind read "
            f"now returns a wrong-empty (#6146): {missing}")

    def test_a_root_read_filter_is_unchanged(self, shipped):
        """A kind with no ancestors keeps its whole bucket.

        Exhaustive over the CORE roots (no child in any row): there is nothing
        to subtract, so the two surfaces must be identical. This is the
        over-narrowing guard — a "drop the last N members" or "drop everything
        a parent has" implementation fails here while passing the ancestor test.
        """
        roots = [kind for kind in CORE_SUBCLASSES
                 if kind not in {child for children in CORE_SUBCLASSES.values()
                                 for child in children}]
        assert roots, "no ancestor-free core root found — the check is vacuous"
        mismatched = {
            root: (shipped.expand_kind(root), shipped.read_filter_kinds(root))
            for root in roots
            if shipped.read_filter_kinds(root) != shipped.expand_kind(root)
        }
        assert not mismatched, (
            "a root kind's read filter differs from its membership set although "
            f"it has no ancestor to drop: {mismatched}")

    def test_every_kind_filter_is_nonempty_and_within_the_membership_set(self, shipped):
        """Table-wide invariant over all 311 buckets.

        `read_filter_kinds(k)` must contain `k` (a filter that dropped the
        asked-for kind would be a wrong-empty) and must be a subset of the
        membership bucket (it only ever subtracts).
        """
        kinds = list(shipped._kind_expansions)  # the built table, by design
        assert len(kinds) >= 300, f"expansion table shrank: {len(kinds)}"
        missing_self = [k for k in kinds if k not in shipped.read_filter_kinds(k)]
        not_subset = [k for k in kinds
                      if not set(shipped.read_filter_kinds(k))
                      <= set(shipped.expand_kind(k))]
        assert not missing_self, f"read filter dropped its own kind: {missing_self}"
        assert not not_subset, f"read filter is not a subset: {not_subset}"

    def test_a_pack_child_filter_is_the_pre_11667_read_set(self, shipped):
        """The measured regression, exactly: `dev:issue` reads its own rows only.

        Before #11667 the bucket was `["dev:issue", "pm:issue"]` (the
        `equivalentTo` peer) and `query(kind="dev:issue")` returned only those
        rows. The UP walk added `WorkItem`/`Object` to the shared list; the
        filter set must be the pre-#11667 set again.
        """
        assert shipped.read_filter_kinds("dev:issue") == ["dev:issue", "pm:issue"]
        assert shipped.expand_kind("dev:issue") != shipped.read_filter_kinds("dev:issue")
        assert "WorkItem" in shipped.expand_kind("dev:issue")

    def test_a_bare_kind_filter_keeps_its_namespaced_rows(self, shipped):
        """#6146 spelling rescue survives the split, one-directionally.

        A bare kind's filter still reaches its namespaced forms (rows are stored
        under either spelling), but the namespaced bucket still does not gain
        the bare name — pre-existing asymmetry, kept as the negative control.
        """
        assert "dev:issue" in shipped.read_filter_kinds("issue")
        assert "issue" not in shipped.read_filter_kinds("dev:issue")

    def test_the_cross_axis_pair_is_down_only(self, shipped):
        """The one declared cross-axis pair, on the filter surface.

        `portfolio:allocationTarget` is a declared subclass of the POINT kind
        `target` (the §5/#7813 model pinned by
        `test_a_cross_axis_parent_is_a_recorded_decision`). A filter for the
        child must not match `target` rows.
        """
        assert "target" in shipped.expand_kind("portfolio:allocationTarget")
        assert "target" not in shipped.read_filter_kinds(
            "portfolio:allocationTarget")
        assert "portfolio:allocationTarget" in shipped.read_filter_kinds("target")

    def test_domain_validators_expand_is_the_read_filter(self, shipped):
        """The validator's vocabulary helper is the DOWN surface too.

        `domain_validators._expand` builds the `IN` vocabularies the graph
        validators pass to Cypher, so it is a read filter by definition.
        """
        from tortoise.domain_validators import _expand

        assert "Subject" not in _expand("team")
        assert "team" in _expand("team")
        assert "WorkItem" not in _expand("dev:issue")
        assert "dev:issue" in _expand("dev:issue")

    def test_the_up_helper_has_exactly_one_caller(self):
        """Structurally: `_expand_kind` is the MEMBERSHIP helper, and only the
        membership idiom may use it.

        The `hybrid_search` kind filter (`n.{kind_field} IN [...]`) cannot be
        pinned behaviourally without a query vector and an embedder, so its
        wiring is pinned as a rule instead: the UP helper has exactly ONE
        caller (`_operator_scope_clauses`). A new read-filter site that reaches
        for `_expand_kind` fails here, and a deliberate membership caller has to
        say so by updating this allow-list.
        """
        import ast

        tree = ast.parse((Path(__file__).resolve().parents[1]
                          / "tortoise" / "sdk.py").read_text())
        callers = sorted({
            node.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and any(
                isinstance(inner, ast.Call)
                and isinstance(inner.func, ast.Attribute)
                and inner.func.attr == "_expand_kind"
                for inner in ast.walk(node)
                if inner is not node
            )
        })
        assert callers == ["_operator_scope_clauses"], (
            "a membership-set caller other than the `\"operator\" in ...` idiom "
            f"appeared — read filters take `_read_filter_kinds` (#11679): {callers}")


class TestReadPathFilterClause:
    """The `sdk` filter sites match on the DOWN set — measured on a real graph."""

    def test_a_narrow_kind_query_does_not_return_a_broader_kinded_row(self):
        """`query`/`paginated_query(kind=child)` must not match a parent's rows.

        Points stored with `pointKind='WorkItem'` (the broader kind) and
        `pointKind='dev:issue'` (its declared child) — the exact stored shape
        the review measured at `d26f3c6d`. The child filter returns only the
        child; the parent filter still returns both (#6146 DOWN reach).
        """
        from tests._live_utils import _skip_unless_live_uri

        _skip_unless_live_uri()

        from tortoise.sdk import TortoiseSDK

        sdk = TortoiseSDK()
        created: list[str] = []
        try:
            broad = sdk.create_point("WorkItem", "a work item, not an issue")
            narrow = sdk.create_point("dev:issue", "an issue")
            created = [broad["id"], narrow["id"]]

            narrow_rows = sdk.query(kind="dev:issue")
            narrow_ids = {row["id"] for row in narrow_rows}
            assert narrow["id"] in narrow_ids, (
                "query(kind='dev:issue') lost its OWN row — wrong-empty")
            assert broad["id"] not in narrow_ids, (
                "query(kind='dev:issue') returned a WorkItem row: the read "
                "filter carries the ancestor (#11679)")

            broad_ids = {row["id"] for row in sdk.query(kind="WorkItem")}
            assert {broad["id"], narrow["id"]} <= broad_ids, (
                "query(kind='WorkItem') lost a narrower kind's row — the DOWN "
                f"direction was over-narrowed (#6146): {broad_ids}")

            page = sdk.paginated_query(kind="dev:issue", limit=50)
            page_ids = {row["id"] for row in page["results"]}
            assert narrow["id"] in page_ids
            assert broad["id"] not in page_ids, (
                "paginated_query(kind='dev:issue') returned a WorkItem row")

            assert "WorkItem" not in sdk._read_filter_kinds("dev:issue")
            assert "WorkItem" in sdk._expand_kind("dev:issue")
        finally:
            for pid in created:
                with contextlib.suppress(Exception):
                    sdk.delete_point(pid)
            sdk.close()
