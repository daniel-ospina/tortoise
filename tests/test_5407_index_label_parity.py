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
Coverage is asserted against **both** pairs of declarations the index path
uses: the sets the DDL sweep iterates, and the required sets that gate whether
the sweep runs at all — a label absent from the latter is never indexed, and
an already-indexed graph reports current and skips the sweep entirely.

**Scope — read this before trusting the assertions.** ``ENTITY_TYPE_LABELS``
is read by the **vector** leg only. ``run_fts_query``, ``run_structural_query``
and the SDK's post-retrieval Cypher each still keep their own equivalent
derivation, so a drift in *those* legs would leave these tests green while the
leg answered against an unindexed label — the same class of defect, not yet
covered. Migrating the legs onto the declaration is part of #5407's remaining
scope; the note above ``ENTITY_TYPE_LABELS`` in ``tortoise/security.py`` is
its fullest statement, though it is itself incomplete.
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

#: Labels the **vector** leg can be routed to. Read off the declaration that
#: leg uses rather than re-typed, so this cannot drift from its routing.
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
        f"{kind}: every label the vector leg can be routed to must have an "
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
    """Per-leg form for the vector leg: the derivation it applies is covered.

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


def _ensure_indexes_tree() -> ast.AST:
    source = inspect.getsource(FalkorProjection._ensure_indexes)
    return ast.parse(textwrap.dedent(source))


def _index_ddl_statements() -> list:
    """Every ``CREATE INDEX`` f-string in ``_ensure_indexes``, as AST nodes."""
    return [node for node in ast.walk(_ensure_indexes_tree())
            if isinstance(node, ast.JoinedStr)
            and any(isinstance(v, ast.Constant) and isinstance(v.value, str)
                    and "CREATE INDEX FOR (n:" in v.value
                    for v in node.values)]


def _iterated_names() -> set:
    """Names that ``_ensure_indexes`` iterates over, via the AST.

    A substring scan is not a provenance test: appending a comment naming the
    constant defeats it while the literal is fully re-inlined.
    """
    return {node.iter.id for node in ast.walk(_ensure_indexes_tree())
            if isinstance(node, ast.For) and isinstance(node.iter, ast.Name)}


def _loaded_names() -> set:
    return {node.id for node in ast.walk(_ensure_indexes_tree())
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)}


def _required_labels(indexes) -> frozenset:
    """Labels named by a ``(label, prop, kind)`` required-index declaration."""
    return frozenset(label for label, _prop, _kind in indexes)


def test_the_required_index_sets_also_cover_every_served_label() -> None:
    """The gate that decides whether the DDL sweep runs at all.

    ``_ensure_indexes`` returns early when ``_schema_is_current()`` is True,
    and that gate is built from ``_REQUIRED_RANGE_INDEXES`` /
    ``_REQUIRED_FULLTEXT_INDEXES`` — a third and fourth declaration of the same
    label sets, independent of the two the sweep itself iterates. Adding a
    served label to the sweep declarations alone leaves an already-indexed
    graph reporting current, so the new index is never created: the #4997
    shape, reachable while every other assertion here is green.
    """
    for kind, indexes in (
            ("range", FalkorProjection._REQUIRED_RANGE_INDEXES),
            ("fulltext", FalkorProjection._REQUIRED_FULLTEXT_INDEXES)):
        _assert_covered(SERVED_LABELS, _required_labels(indexes), kind)


def test_ensure_indexes_iterates_the_declarations_not_literals() -> None:
    """The label sets must be *read from* the declarations, not re-inlined."""
    iterated = _iterated_names()
    assert "_FULLTEXT_INDEX_LABEL_FIELDS" in iterated, (
        "the full-text loop no longer iterates the module-level declaration — "
        "a re-inlined copy would drift unnoticed"
    )
    assert "_RANGE_INDEX_LABEL_PROPS" in iterated, (
        "the range loop no longer iterates the module-level declaration — "
        "a re-inlined copy would drift unnoticed"
    )
    assert "_POINT_RANGE_INDEX_PROPS" in iterated, (
        "the Point range props are not iterated from the module-level "
        "declaration — a dead reference elsewhere in the body would keep this "
        "green while the literal was re-inlined"
    )


def test_the_point_ddl_interpolates_the_declared_label() -> None:
    """The Point label must come from the declaration *at the DDL site*.

    Scoped to the ``CREATE INDEX FOR (n:...`` f-string itself rather than any
    ``Load`` of the name in the method: a dead reference elsewhere in a
    ~230-line body would otherwise keep this green with the DDL reverted to a
    literal — the round-1 gap, one step removed.
    """
    ddl = _index_ddl_statements()
    assert ddl, "no CREATE INDEX FOR (n:...) statement found in _ensure_indexes"
    interpolated = set()
    for node in ddl:
        for value in node.values:
            if (isinstance(value, ast.FormattedValue)
                    and isinstance(value.value, ast.Name)):
                interpolated.add(value.value.id)
    assert "_POINT_RANGE_INDEX_LABEL" in interpolated, (
        f"the Point DDL does not interpolate the declared label; it "
        f"interpolates {sorted(interpolated)}"
    )
