"""#5407 — the range and full-text index label sets must not drift from routing.

``_ensure_indexes`` creates range and full-text indexes for a fixed set of
labels, and a query leg resolves a caller's ``entity_type`` to a label. Both
index sets were inline literals inside ``_ensure_indexes``, so a served label
could be added with no index created for it: the FTS leg then degrades to
``index_missing`` / an empty run and the structural leg falls back to a
correct-but-slow label scan, with no error and no red test.

This is a seam, not a bug fix. Measured when written, the sets agree exactly
(5 served labels, 5 full-text labels, 5 range labels), so no live query is
broken today. Coverage is asserted against **both** pairs of declarations the
index path uses: the sets the DDL sweep iterates, and the required sets that
gate whether the sweep runs at all — a label absent from the latter is never
indexed, because an already-indexed graph reports current and skips the sweep.

**Scope — what these assertions do NOT cover.** Read this before trusting them.

* The **vector/HNSW** index is out of scope entirely. ``run_vector_query``
  needs neither a range nor a full-text index; its labels are still literals in
  ``_ensure_vector_index_api``, and ``_record_vector_index_inventory`` compares
  against them only to emit a latched warning. A served label with no vector
  index passes every assertion here.
* ``ENTITY_TYPE_LABELS`` is read by the **vector** leg only. ``run_fts_query``,
  ``run_structural_query`` and the SDK's post-retrieval Cypher keep their own
  equivalent derivation, so a drift in *those* legs would leave these tests
  green. Migrating them is part of #5407's remaining scope.
* The provenance check pins that the sweep loops **iterate** the declarations.
  It does not pin that the loop body uses the loop variable, and it does not
  reach the label literals at the other creation sites — ``_fix_point_search_keys``
  and the Event FTS migration both drop and recreate by literal. Renaming a
  label there desynchronizes the served set with every test here green.
"""

import ast
import inspect
import textwrap

import pytest

from tortoise.projection import (
    _FULLTEXT_INDEX_LABEL_FIELDS,
    _POINT_RANGE_INDEX_LABEL,
    _POINT_RANGE_INDEX_PROPS,
    _RANGE_INDEX_LABEL_PROPS,
    FalkorProjection,
)
from tortoise.security import (
    ENTITY_TYPE_LABELS,
    VALID_ENTITY_TYPES,
    entity_label,
)

#: Labels a query leg can be routed to, read off the declared mapping rather
#: than re-typed. ``operator``/``point`` both resolve to ``Point``;
#: ``document``/``source`` both to ``Source``.
SERVED_LABELS = frozenset(ENTITY_TYPE_LABELS.values())


def _fulltext_labels() -> frozenset:
    return frozenset(label for label, _ in _FULLTEXT_INDEX_LABEL_FIELDS)


def _range_labels() -> frozenset:
    return (frozenset(label for label, _ in _RANGE_INDEX_LABEL_PROPS)
            | {_POINT_RANGE_INDEX_LABEL})


def _assert_covered(served, indexed, kind: str) -> None:
    missing = set(served) - set(indexed)
    assert not missing, (
        f"{kind}: every declared label must have an index; missing "
        f"{sorted(missing)}. Add the label to the index declaration in "
        f"tortoise/projection/__init__.py, or correct the entity-type "
        f"mapping in tortoise/security.py."
    )


def test_the_served_label_set_is_not_empty() -> None:
    """Guard against a vacuous pass: coverage below is meaningless if empty."""
    assert SERVED_LABELS, "no entity type resolves to a label — mapping broken"


def test_every_served_label_has_a_fulltext_index() -> None:
    _assert_covered(SERVED_LABELS, _fulltext_labels(), "fulltext")


def test_every_served_label_has_a_range_index() -> None:
    _assert_covered(SERVED_LABELS, _range_labels(), "range")


def test_every_entity_type_resolves_to_an_indexed_label() -> None:
    """Per-type form: the derivation the legs apply is itself covered.

    Enumerates ``VALID_ENTITY_TYPES`` rather than ``ENTITY_TYPE_LABELS`` so a
    *new* entity type that nobody added to the mapping is caught too.
    """
    for entity_type in sorted(VALID_ENTITY_TYPES):
        label = entity_label(entity_type)
        assert label in _fulltext_labels(), (
            f"entity_type={entity_type!r} routes to label {label!r}, which "
            f"has no full-text index"
        )
        assert label in _range_labels(), (
            f"entity_type={entity_type!r} routes to label {label!r}, which "
            f"has no range index"
        )


def _required_labels(indexes) -> frozenset:
    """Labels named by a ``(label, prop, kind)`` required-index declaration."""
    return frozenset(label for label, _prop, _kind in indexes)


def test_the_required_index_sets_also_cover_every_served_label() -> None:
    """The gate that decides whether the DDL sweep runs at all.

    ``_ensure_indexes`` returns early when ``_schema_is_current()`` is True,
    and that gate is built from ``_REQUIRED_RANGE_INDEXES`` /
    ``_REQUIRED_FULLTEXT_INDEXES`` — a third and fourth declaration of the same
    label sets, independent of the two the sweep itself iterates. A served
    label added to the sweep declarations alone leaves an already-indexed graph
    reporting current, so the new index is never created.

    Label-level only: an entry removed for one *property* while another row for
    the same label remains is not caught here.
    """
    for kind, indexes in (
            ("range", FalkorProjection._REQUIRED_RANGE_INDEXES),
            ("fulltext", FalkorProjection._REQUIRED_FULLTEXT_INDEXES)):
        _assert_covered(SERVED_LABELS, _required_labels(indexes), kind)


def test_point_range_indexes_are_declared() -> None:
    """``Point``'s ranged set is declared as a label plus props."""
    assert _POINT_RANGE_INDEX_LABEL == "Point"
    assert _POINT_RANGE_INDEX_PROPS == ("id", "pointKind", "content_hash")


def test_coverage_reddens_when_a_served_label_loses_its_index() -> None:
    """Mutation check against the REAL sets, not a synthetic literal.

    Drops ``Source`` — which genuinely is served — from the real index set the
    production declarations produce, and requires the coverage assertion to
    fail. Without this, a green coverage test could mean only that the
    assertion never fires.
    """
    for kind, indexed in (("fulltext", _fulltext_labels()),
                          ("range", _range_labels())):
        assert "Source" in SERVED_LABELS and "Source" in indexed, (
            "fixture assumption: Source is served and indexed in both kinds"
        )
        with pytest.raises(AssertionError):
            _assert_covered(SERVED_LABELS, indexed - {"Source"}, kind)


def _ensure_indexes_tree() -> ast.AST:
    source = inspect.getsource(FalkorProjection._ensure_indexes)
    return ast.parse(textwrap.dedent(source))


def _loop_over(name: str):
    """The ``for`` statement in ``_ensure_indexes`` iterating ``name``."""
    for node in ast.walk(_ensure_indexes_tree()):
        if (isinstance(node, ast.For) and isinstance(node.iter, ast.Name)
                and node.iter.id == name):
            return node
    return None


def _interpolated_names(nodes) -> set:
    """Names interpolated into a ``CREATE INDEX FOR (n:...)`` f-string."""
    found = set()
    for node in nodes:
        if not isinstance(node, ast.JoinedStr):
            continue
        if not any(isinstance(v, ast.Constant) and isinstance(v.value, str)
                   and "CREATE INDEX FOR (n:" in v.value
                   for v in node.values):
            continue
        for value in node.values:
            if (isinstance(value, ast.FormattedValue)
                    and isinstance(value.value, ast.Name)):
                found.add(value.value.id)
    return found


def test_ensure_indexes_iterates_the_declarations_not_literals() -> None:
    """The sweep loops must ITERATE the module-level declarations.

    Header-level only: this does not pin that the loop body uses the loop
    variable (see the module docstring's scope note).
    """
    for name in ("_FULLTEXT_INDEX_LABEL_FIELDS", "_RANGE_INDEX_LABEL_PROPS",
                 "_POINT_RANGE_INDEX_PROPS"):
        assert _loop_over(name) is not None, (
            f"no loop in _ensure_indexes iterates {name} — a re-inlined copy "
            f"would drift unnoticed"
        )


def test_the_point_ddl_interpolates_the_declared_label() -> None:
    """The Point label must come from the declaration at ITS OWN DDL site.

    Scoped to the statements inside the ``_POINT_RANGE_INDEX_PROPS`` loop, not
    to every ``CREATE INDEX`` f-string in the method: a decoy f-string naming
    the constant elsewhere would otherwise keep this green with the Point DDL
    reverted to a literal.
    """
    loop = _loop_over("_POINT_RANGE_INDEX_PROPS")
    assert loop is not None, "the Point range loop is gone"
    interpolated = _interpolated_names(ast.walk(loop))
    assert interpolated, "no CREATE INDEX FOR (n:...) statement in the Point loop"
    assert "_POINT_RANGE_INDEX_LABEL" in interpolated, (
        f"the Point DDL does not interpolate the declared label; it "
        f"interpolates {sorted(interpolated)}"
    )
