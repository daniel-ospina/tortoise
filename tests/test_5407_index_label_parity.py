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
import re
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
    assert _POINT_RANGE_INDEX_PROPS == ("id", "pointKind", "content_hash",
                                       "key")


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


def test_every_declared_range_index_is_also_REQUIRED() -> None:
    """PROPERTY-level coverage of the required set — the (label, prop) pairs.

    ``test_the_required_index_sets_also_cover_every_served_label`` above is
    LABEL-granular, so it cannot see a newly declared property whose index is
    merely created and never REQUIRED — and that is the #4465/#8400 trap
    exactly: ``_ensure_indexes`` returns early when ``_schema_is_current()`` is
    True, so on an EXISTING graph the new index is never created and the
    declaration is a no-op. Found in review (#7927): ``("Event", "id")`` was
    added to ``_RANGE_INDEX_LABEL_PROPS`` and not to
    ``_REQUIRED_RANGE_INDEXES``, which every test in this suite still passed
    because they all build a FRESH database.
    """
    declared = {(label, prop)
                for label, props in _RANGE_INDEX_LABEL_PROPS for prop in props}
    # The Point set is declared by its own constant (the #3154 composite/
    # boolean-index repair path owns it), and the two Session keys are created
    # by dedicated statements — all three are REQUIRED already; assert only
    # that nothing DECLARED is missing from the required set.
    required = {(label, prop)
                for label, prop, kind in FalkorProjection._REQUIRED_RANGE_INDEXES
                if kind == "RANGE"}
    missing = declared - required
    assert not missing, (
        f"declared but NOT required, so an existing graph never gets them: "
        f"{sorted(missing)}. Add the (label, prop, 'RANGE') entry to "
        f"FalkorProjection._REQUIRED_RANGE_INDEXES (#4465/#8400).")


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


# ── The docstring's "in full" RANGE enumeration vs the DDL sites ─────────
#
# #8527 review: ``_ensure_indexes`` opened with a bullet claiming to list its
# range indexes "in full" while naming only ``Point``. The same body also
# issues range indexes for ``Subject``/``Object``/``Event``/``Source`` (via
# ``_RANGE_INDEX_LABEL_PROPS``) and two ``Session`` keys, and that bullet is the
# line lane L2 (#5094) reads as evidence the gap was intended rather than
# drift — so an under-description there is a claim mismatch, not a taste issue.
# This is the anti-drift falsifier: the enumeration is checked against the DDL
# sites that produce it, so neither half can move alone.


def _range_ddl_labels() -> frozenset:
    """Labels the RANGE ``CREATE INDEX`` sites in ``_ensure_indexes`` cover.

    Derived from the code, not re-typed: the two declaration-driven sets plus
    the literal labels of every standalone ``CREATE INDEX FOR (...:Label)``
    statement in the body (the two ``:Session`` keys today).
    """
    source = inspect.getsource(FalkorProjection._ensure_indexes)
    declared = ({_POINT_RANGE_INDEX_LABEL}
                | {label for label, _ in _RANGE_INDEX_LABEL_PROPS})
    literal = set(re.findall(
        r"CREATE INDEX FOR \(\w+:([A-Za-z_]\w*)\)", source))
    return frozenset(declared | literal)


def _docstring_range_bullet() -> str:
    """The RANGE bullet of ``_ensure_indexes``'s summary, and only it.

    Scoped to the bullet so a label named in ANOTHER family's bullet — the
    FULL-TEXT bullet names ``Subject``/``Object``/``Event``/``Source`` too —
    cannot satisfy a RANGE enumeration claim.
    """
    doc = inspect.getdoc(FalkorProjection._ensure_indexes) or ""
    for chunk in re.split(r"\n\s*\*\s+", doc):
        if chunk.lstrip().startswith("RANGE"):
            return chunk
    raise AssertionError(
        "the _ensure_indexes docstring has no RANGE bullet to check"
    )


def test_docstring_range_bullet_names_every_range_ddl_label() -> None:
    labels = _range_ddl_labels()
    # Vacuity guard: the oracle must actually see the literal ``:Session``
    # sites, or the pre-fix bullet (which names only ``Point``) would satisfy
    # the assertion below. Without this the test could pass while measuring
    # nothing.
    assert "Session" in labels, (
        "the RANGE-DDL oracle found no literal label — the extractor is broken, "
        "so the check below cannot redden on the pre-fix bullet"
    )
    bullet = _docstring_range_bullet()
    missing = sorted(label for label in labels if label not in bullet)
    assert not missing, (
        f"the _ensure_indexes RANGE bullet claims to list the range indexes "
        f"'in full' but does not name {missing}. Name every RANGE label the DDL "
        f"sites cover, or drop the 'in full' claim."
    )
