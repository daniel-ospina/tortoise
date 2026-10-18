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
"""
from __future__ import annotations

__all__ = [
    "catalogue_cell",
    "index_catalogue_has_field",
    "index_row_has_field",
    "indexed_field_names",
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
