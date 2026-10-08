"""#5407 — the INDEX path and the QUERY path must not disagree about labels.

``_ensure_indexes`` creates range and full-text indexes for a fixed set of
labels, and a query leg resolves a caller's ``entity_type`` to a label. Both
index sets were inline literals inside ``_ensure_indexes``, so an entity type
could be added and its index silently never created — the leg then answers
against a label with no index and degrades to an empty run, with no error and
no red test. #4997 is the measured instance of that shape for the vector pair.

This is a seam, not a bug fix. Measured when written, the two sets agree
exactly (5 served labels, 5 full-text labels, 5 range labels), so no live
query is broken today. The test exists so the next entity type cannot
silently break one, and so re-inlining the literals cannot go unnoticed.

**Scope note.** ``ENTITY_TYPE_LABELS`` is the declared routing for the vector
leg. ``run_fts_query``, ``run_structural_query`` and the SDK's post-retrieval
Cypher each still keep their own equivalent derivation (``security.py`` says so
at its ``entity_label`` docstring); migrating those onto the declaration is
#5407's remainder, not this change's.
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

#: Labels a query leg can be routed to. Read off the declaration the legs use
#: rather than re-typed, so this cannot drift from the routing itself.
#: ``operator``/``point`` both resolve to ``Point``; ``document``/``source``
#: both to ``Source``.
SERVED_LABELS = frozenset(ENTITY_TYPE_LABELS.values())


def _fulltext_labels() -> frozenset:
    return frozenset(label for label, _ in _FULLTEXT_INDEX_LABEL_FIELDS)


def _range_labels() -> frozenset:
    return (frozenset(label for label, _ in _RANGE_INDEX_LABEL_PROPS)
            | {_POINT_RANGE_INDEX_LABEL})


def _assert_covered(served, indexed, kind: str) -> None:
    missing = set(served) - set(indexed)
    assert not missing, (
        f"{kind}: every label a query leg can be routed to must have an "
        f"index; missing {sorted(missing)}. Add the label to the index "
        f"declaration in tortoise/projection/__init__.py, or correct the "
        f"entity-type mapping in tortoise/security.py."
    )


def test_the_served_label_set_is_not_empty() -> None:
    """Guard against a vacuous pass: coverage below is meaningless if empty."""
    assert SERVED_LABELS, "no entity type resolves to a label — mapping broken"


def test_every_served_label_has_a_fulltext_index() -> None:
    _assert_covered(SERVED_LABELS, _fulltext_labels(), "fulltext")


def test_every_served_label_has_a_range_index() -> None:
    _assert_covered(SERVED_LABELS, _range_labels(), "range")


def test_every_entity_type_resolves_to_an_indexed_label() -> None:
    """Per-leg form: the derivation the query legs apply is itself covered.

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


def test_point_range_indexes_are_declared() -> None:
    """``Point``'s ranged set is declared as a label plus props."""
    assert _POINT_RANGE_INDEX_LABEL == "Point"
    assert _POINT_RANGE_INDEX_PROPS == ("id", "pointKind", "content_hash")


@pytest.mark.parametrize("kind", ["fulltext", "range"])
def test_coverage_reddens_when_a_served_label_loses_its_index(kind) -> None:
    """Mutation check against the REAL sets, not a synthetic literal.

    Drops a label that genuinely is served from the index set the production
    declarations produce, and requires the same assertion the coverage tests
    use to fail. Without this, a green coverage test could mean only that the
    assertion never fires.
    """
    indexed = _fulltext_labels() if kind == "fulltext" else _range_labels()
    victim = "Source"
    assert victim in SERVED_LABELS and victim in indexed, (
        "fixture assumption: Source is served and indexed in both kinds"
    )
    with pytest.raises(AssertionError):
        _assert_covered(SERVED_LABELS, indexed - {victim}, kind)


def _iterable_names_in_ensure_indexes() -> set:
    """Names that ``_ensure_indexes`` iterates over, via the AST.

    A substring scan is not a provenance test: appending a comment naming the
    constant defeats it while the literal is fully re-inlined.
    """
    source = inspect.getsource(FalkorProjection._ensure_indexes)
    tree = ast.parse(textwrap.dedent(source))
    return {node.iter.id for node in ast.walk(tree)
            if isinstance(node, ast.For) and isinstance(node.iter, ast.Name)}


def _loaded_names_in_ensure_indexes() -> set:
    source = inspect.getsource(FalkorProjection._ensure_indexes)
    tree = ast.parse(textwrap.dedent(source))
    return {node.id for node in ast.walk(tree)
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)}


def test_ensure_indexes_iterates_the_declarations_not_literals() -> None:
    """The label sets must be *read from* the declarations, not re-inlined."""
    iterated = _iterable_names_in_ensure_indexes()
    assert "_FULLTEXT_INDEX_LABEL_FIELDS" in iterated, (
        "the full-text loop no longer iterates the module-level declaration — "
        "a re-inlined copy would drift unnoticed"
    )
    assert "_RANGE_INDEX_LABEL_PROPS" in iterated, (
        "the range loop no longer iterates the module-level declaration — "
        "a re-inlined copy would drift unnoticed"
    )
    assert "_POINT_RANGE_INDEX_PROPS" in _loaded_names_in_ensure_indexes(), (
        "the Point range props are not read from the module-level declaration"
    )
    assert "_POINT_RANGE_INDEX_LABEL" in _loaded_names_in_ensure_indexes(), (
        "the Point label is not read from its declaration at the DDL site"
    )
