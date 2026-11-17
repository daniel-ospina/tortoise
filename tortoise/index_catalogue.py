"""The ONE definition of "does the index catalogue index this field?" (#3154/#522/#3441).

``CALL db.indexes()`` reports one row per index: ``row[1]`` is the list of
INDEXED FIELD NAMES and ``row[2]`` maps each of those names to the index TYPES
carrying it (measured on embedded FalkorDBLite 4.18.3 —
``OrderedDict({'content': ['FULLTEXT'], 'name': ['RANGE', 'FULLTEXT']})``).
Two places in the product ask this catalogue "is ``is_operator`` indexed?": the
hosted restore/import audit (``tortoise/hosted_backup.py``, #3154) and the
embedded repair sweep plus the schema probe (``tortoise/projection/__init__.py``,
#522/#4465).

Why ONE function rather than a copy at each site
------------------------------------------------
A SUBSTRING test for ``"is_operator"`` also matches an unrelated indexed
property such as ``is_operator_flag``, and the two callers fail in different,
both-bad ways:

* ``hosted_backup._index_has_is_operator`` — the false positive is never
  cleared by the drop (which only targets the two known boolean-index forms),
  so the post-drop re-check reported "still present" and raised AFTER the live
  graph had already been swapped: a successful restore reported as a
  destructive failure (#3154 review P2).
* the embedded repair sweep — the sweep only emits drops built from
  permutations of a fixed 5-field set, none of which can name
  ``is_operator_flag``, so the observable effect is a **bounded no-op**: 260
  wasted ``DROP INDEX`` attempts on every startup that takes the full path
  (#3441, measured).

Fixing "the same defect class one path at a time" is exactly what let the
second site survive the first fix. One definition, imported by every caller, is
the fix that does not leave a third occurrence for the next audit to find.

The match is EXACT on the field name. A stringified ``properties`` cell (an
engine has been observed to report one) is TOKENISED, never substring-tested.

It also owns the ONE rendering of a VECTOR-index DROP (#13320) — see
:func:`vector_index_drop_statement`. That function exists for the same reason
this module does: the restore path had already measured which statement drops a
VECTOR index and written it down (``graph_key_restore.drop_cypher``), and the
retirement sweep then re-derived the same statement from an unmeasured guess and
shipped the guess. One rendering, imported by both.
"""
from __future__ import annotations

import re

__all__ = [
    "catalogue_cell",
    "index_catalogue_has_field",
    "index_row_has_field",
    "indexed_field_names",
    "quote_identifier",
    "vector_index_drop_statement",
    "vector_index_drop_statements",
]


def catalogue_cell(row, index: int):
    """``row[index]`` for a ``CALL db.indexes()`` row, or ``None`` when absent.

    A row can be shorter than expected (or not a sequence at all) on an engine
    whose catalogue shape differs, and a presence question must answer "not
    index-bearing" for such a row rather than raise inside a caller that (in
    the repair sweep's case) is wrapped in a bare ``except Exception``.

    PUBLIC because this is the repo's single catalogue-row CELL ACCESSOR, and a caller that
    needs the row anyway must import it rather than copy it: one accessor per row, so
    ``graph_key_restore.py`` cannot drift on how a cell is read.

    The ACCESSOR is the shared invariant — not the whole reader. ``graph_key_restore.py``
    carries its own ``_properties_column_names`` for the ``properties`` column on purpose: a
    binary-safe client hands a cell over as ``bytes``, and ``indexed_field_names`` renders
    that as the repr (``b'[embedding]'``) rather than the name. Do not "align" the two — they
    decode different shapes, for a stated reason.
    """
    try:
        return row[index]
    except (TypeError, IndexError, KeyError):
        return None


#: The in-module name, kept so this module's own call sites read as before.
_cell = catalogue_cell


def indexed_field_names(row) -> tuple[str, ...]:
    """The field NAMES a single ``CALL db.indexes()`` row reports, in order.

    Reads BOTH catalogue columns — the ``properties`` list (``row[1]``) and the
    KEYS of the per-property kinds map (``row[2]``). The two agree on every
    engine observed, and reading both means a spot where a property appears in
    only one of them still answers the presence question, instead of the answer
    depending on which column a caller happened to read.

    ``row[1]`` is normally a list, but a STRINGIFIED list has been observed.
    That form is tokenised (``"[a, b]"`` → ``("a", "b")``), never
    substring-tested, so ``is_operator_flag`` cannot answer a query for
    ``is_operator``. Names are stripped and de-duplicated with order preserved,
    so a caller that only asks membership gets a stable answer.
    """
    props, kinds = _cell(row, 1), _cell(row, 2)

    raw: list[str] = []
    if isinstance(props, str):
        raw.extend(
            part.strip().strip("[]'\"").strip()
            for part in props.strip().strip("[]").split(",")
        )
    elif isinstance(props, (list, tuple, set, frozenset)):
        raw.extend(str(part).strip() for part in props)
    elif props is not None:
        raw.append(str(props).strip())

    if isinstance(kinds, dict):
        raw.extend(str(name).strip() for name in kinds)

    names: list[str] = []
    for name in raw:
        if name and name not in names:
            names.append(name)
    return tuple(names)


def index_row_has_field(row, field_name: str, *, label: str | None = None) -> bool:
    """Does THIS ``CALL db.indexes()`` row index ``field_name``?

    ``label`` restricts the row to that node label; ``None`` (the default)
    accepts any label. Membership is EXACT — ``is_operator_flag`` never answers
    a query for ``is_operator``.
    """
    if label is not None and str(_cell(row, 0)) != label:
        return False
    return field_name in indexed_field_names(row)


def index_catalogue_has_field(rows, field_name: str, *,
                              label: str | None = None) -> bool:
    """Does an index catalogue contain ``field_name`` on any (matching) row?

    ``rows`` is the ``result_set`` of ``CALL db.indexes()``. ``label`` restricts
    the search to that node label; ``None`` (the default) accepts any label —
    the schema probe asks the question without one, because a boolean index on
    ``is_operator`` is hazardous wherever it hangs.
    """
    return any(
        index_row_has_field(row, field_name, label=label)
        for row in (rows or ())
    )


# ── The ONE VECTOR-index DROP rendering (#13320) ────────────────────────────
#
# A VECTOR index is a different index TYPE to every other kind the catalogue
# reports, and the generic statement does not reach it. MEASURED (#13320, and
# again for #7846 before it — ``graph_key_restore.VectorIndexDefinition``):
# on ``falkordb-server:v4.20.6`` (graph module 42006) a graph whose catalogue
# reports ``Point  [embedding]  {'embedding': ['VECTOR']}`` answers
#
#   DROP INDEX ON :Point(embedding)                → Unable to drop index on
#   DROP INDEX FOR (p:Point) ON (p.embedding)        :Point(embedding): no such index
#
# for BOTH generic forms — while ``DROP VECTOR INDEX FOR (p:Point) ON
# (p.embedding)`` returns an empty result set and the index is gone from the
# next ``CALL db.indexes()``. Both generic forms were nonetheless shipped as the
# retirement's statement list (#11642), so an armed deployment reported
# ``vector_index_retired() is True`` and kept the index it paid for, with the
# failures visible only at DEBUG — the shape #13320 was filed on.
#
# This is the ``#3441`` lesson applied to a second row: the restore path had
# already measured and recorded the working form; the retirement path derived
# its own from prose and got it wrong. So the rendering lives HERE, once, and
# both callers import it.

#: An identifier that needs no quoting. Anything else is backtick-quoted (with
#: interior backticks doubled) because a label/property name reaches Cypher by
#: interpolation and comes from the graph's own schema — i.e. from whatever the
#: customer wrote there.
_BARE_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

#: The catalogue's entity TYPE tokens, as ``CALL db.indexes()`` cell 6 spells them.
#: They live HERE because this module is the leaf every catalogue reader imports:
#: ``graph_key_restore`` aliases these as ``_NODE``/``_RELATIONSHIP`` instead of
#: restating them, so "what the catalogue calls a relationship" has one definition
#: (#13320). Only the two a DROP form is known for — a VECTOR index on anything
#: else gets no rendering guessed for it.
NODE_ENTITY_TYPE = "NODE"
RELATIONSHIP_ENTITY_TYPE = "RELATIONSHIP"


def quote_identifier(name: str) -> str:
    """``name`` as a Cypher identifier, backtick-quoted only when it must be.

    ONE quoter (#13320). ``graph_key_restore._quote_identifier`` is the same
    function under its historical private name, so a label that needs quoting
    (or, worse, one that CONTAINS a backtick) cannot be escaped two ways.
    """
    if _BARE_IDENTIFIER.match(name or ""):
        return name
    return "`" + str(name).replace("`", "``") + "`"


def vector_index_drop_statement(
    label: str,
    property: str,
    *,
    entity_type: str = NODE_ENTITY_TYPE,
    variable: str = "p",
) -> str:
    """The statement that ACTUALLY drops a VECTOR index (#13320).

    ⛔ The vector-index form, NOT ``DROP INDEX ON :Label(prop)``: measured on
    graph module 42006, the plain form answers ``Unable to drop index on
    :Point(embedding): no such index`` for a vector index that ``CALL
    db.indexes()`` is reporting a row for. Following that error leaves the index
    in place — which is exactly what the restore path exists to avoid, and what
    the retirement path did on every armed deployment until #13320.

    ``entity_type`` is the catalogue's own token (cell 6): a relationship index
    is written ``()-[p:REL]-()``, a node index ``(p:Label)``.
    """
    if entity_type == RELATIONSHIP_ENTITY_TYPE:
        pattern = f"()-[{variable}:{quote_identifier(label)}]-()"
    else:
        pattern = f"({variable}:{quote_identifier(label)})"
    return (
        f"DROP VECTOR INDEX FOR {pattern} "
        f"ON ({variable}.{quote_identifier(property)})"
    )


def vector_index_drop_statements(label: str, property: str) -> tuple[str, ...]:
    """Ordered DROPs a RETIREMENT sweep may try, the measured form FIRST (#13320).

    The first entry is :func:`vector_index_drop_statement` — the only form
    measured to drop a VECTOR index. The generic forms follow for an engine that
    reaches vector indexes through its plain drop; they are CANDIDATES, tried
    only on a sweep whose catalogue says the vector index is present, so a form
    the engine rejects costs one failed DDL on the ONE migration sweep and
    nothing afterwards.

    ⛔ ``graph_key_restore`` deliberately does NOT use this list — it must issue
    the one measured form and verify it, never "try something and see". This
    ordering is for a best-effort retirement that must also work on an engine
    this repo has not measured.
    """
    node = quote_identifier(label)
    prop = quote_identifier(property)
    return (
        vector_index_drop_statement(label, property),
        f"DROP INDEX ON :{node}({prop})",
        f"DROP INDEX FOR (p:{node}) ON (p.{prop})",
    )
