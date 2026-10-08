"""#5407 — the INDEX path and the QUERY path must not disagree about labels.

``_ensure_indexes`` creates range and full-text indexes for a fixed set of
labels, and the query legs resolve a caller's ``entity_type`` to a label
through ``tortoise.security.entity_label``. Nothing tied those two together:
both index sets were inline literals inside ``_ensure_indexes``, so an entity
type could be added and its index silently never created — the query leg then
answers against a label with no index and degrades to an empty run, with no
error and no red test. #4997 is the measured instance of that shape for the
vector pair.

This is a seam, not a bug fix. Measured when written, the two sets agree
exactly (5 served labels, 5 full-text labels, 5 range labels), so no live
query is broken today. The test exists so the next entity type cannot
silently break one, and so re-inlining the literals cannot go unnoticed.
"""

import inspect

import pytest

from tortoise.projection import (
    _FULLTEXT_INDEX_LABEL_FIELDS,
    _POINT_RANGE_INDEX_PROPS,
    _RANGE_INDEX_LABEL_PROPS,
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
    # ``Point``'s range indexes are declared by ``_POINT_RANGE_INDEX_PROPS``,
    # which is a prop list rather than a ``(label, props)`` pair, so the label
    # is added here; ``test_point_range_indexes_are_declared`` pins that.
    return frozenset(label for label, _ in _RANGE_INDEX_LABEL_PROPS) | {"Point"}


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
    """Per-leg form: the derivation each query leg applies is itself covered.

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
    """``Point``'s range props are a separate declaration from the pairs."""
    assert _POINT_RANGE_INDEX_PROPS == ("id", "pointKind", "content_hash")


def test_the_coverage_assertion_fails_when_a_label_is_missing() -> None:
    """Mutation check: proves the assertion shape in 2/3 is not vacuous."""
    with pytest.raises(AssertionError):
        _assert_covered(frozenset({"Brand"}), _fulltext_labels(), "fulltext")


def test_ensure_indexes_no_longer_declares_labels_inline() -> None:
    """Structural: the seam holds only while the literals live in one place.

    If a label list is re-inlined into ``_ensure_indexes``, that copy is again
    invisible to the coverage assertions above.
    """
    from tortoise.projection import FalkorProjection

    source = inspect.getsource(FalkorProjection._ensure_indexes)
    assert "_FULLTEXT_INDEX_LABEL_FIELDS" in source, (
        "the full-text label set is not read from the module-level "
        "declaration — a re-inlined copy would drift unnoticed"
    )
    assert "_RANGE_INDEX_LABEL_PROPS" in source, (
        "the range label set is not read from the module-level "
        "declaration — a re-inlined copy would drift unnoticed"
    )
    assert "_POINT_RANGE_INDEX_PROPS" in source, (
        "the Point range props are not read from the module-level declaration"
    )
