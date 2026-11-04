"""Interface B — the cache projection's contract, made executable (#7971).

WHAT THIS PINS. `docs/architecture/STORAGE-ARCHITECTURE.md` §2.0 makes
**Postgres the record and FalkorDB a derived hot cache**. `tortoise/projection/`
already is a replay — **of the journal**. This module's subject is the *other*
direction: the record's **state**, materialised into the cache, one way.

Four claims, and each is a test here rather than a sentence in a design doc:

1. **ONE-WAY BY CONSTRUCTION.** Nothing on this path reads the cache to decide
   what to project. The only reads the materializer issues are the endpoint
   `MATCH`es, which resolve a **record** coordinate — never a cache fact.
2. **THE CACHE IS NEVER AUTHORITATIVE.** Every property the projection writes
   has a **PRIMARY claim in the record**, read from the schema and the placement
   table (`#7968`/`#7955`) — and no DERIVED/VOLATILE name is ever written, which
   is what makes the clause non-vacuous: the schema *refuses* those names, and a
   projection that dropped the distinction would carry them.
3. **REBUILDABLE FROM THE RECORD ALONE.** A rebuild's node key set and per-node
   field set are a function of the record — plus the fold's DECLARED `ON CREATE`
   constants, `cache_projection.FOLD_ON_CREATE_DEFAULTS` (#9634), which fill a
   field the record row is SILENT about — and of nothing else, and projecting
   the same view twice yields the same statement multiset (idempotent).
4. **A COLD REGION READS `not-hot`, NEVER `empty`.** The declaration is written
   **after** the writes and is a function of what was materialised, so a record
   key the projection did not write is not claimed hot — and an `empty` read
   over it becomes **`degraded`**, never `empty`.

WHY THE PLACEMENT TABLE IS IMPORTED, NOT RE-DECLARED. `#7968`'s
`tests/test_record_schema_placement.py` already parses §17.5 and resolves its
eight rows; `tortoise/cache_projection.py` reads the same table for the property
namespace. This suite asserts the two readings agree with the schema on disk, so
a third declaration cannot appear.

NO DATABASE IS NEEDED for everything above — the live half of S3/S9 (project →
destroy the cache → re-project from the record alone) is
`tests/test_cache_projection_live.py`, which skips visibly when no engine is
reachable rather than passing vacuously.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.test_record_schema_placement import (
    SECTION_TABLE,
    expand_edge_property,
    parse_placement,
)
from tortoise import cache_projection as cp
from tortoise import record_projection as rp
from tortoise.residency import (
    COVERAGE_COMPLETE,
    COVERAGE_PARTIAL,
    RESIDENCY_HOT,
    RESIDENCY_NOT_HOT,
    ResidencyDeclaration,
    apply_residency,
    mark_residency,
)
from tortoise.status_vocabulary import STATUS_DEGRADED, STATUS_EMPTY

REPO_ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS = Path(REPO_ROOT / "supabase" / "migrations")

#: Every (section, property, class) the placement table declares, §2.7 applied.
PLACEMENT_ROWS = parse_placement()

#: `{table: {column}}` and `{table: {refused props name}}`, from the SQL.
TABLE_COLUMNS = rp.table_columns(MIGRATIONS)
REFUSED = rp.refused_props_names(MIGRATIONS)

#: section → table, plus the Edges section's home.
SECTION_TABLE_ALL = {**SECTION_TABLE, "Edges": "relation"}


def _fold_source() -> str:
    """`projection/entities.py` with whole-line `#` comments REMOVED.

    The fold's clauses are separated by long comment blocks that QUOTE the
    clause shapes (e.g. "this arm used to read ``s.subjectKind=$sk``"), so a
    source-parsed assertion that keeps comments matches PROSE as often as code —
    and a naive `ON MATCH` truncation lands inside such a comment. That is how
    the Subject pin went vacuous when #9572 put a comment between `s.name=$name`
    and the clause it explains: the slice ended 90 characters in, so the guard
    inspected a comment instead of a clause.

    Only whole-line comments are dropped (no pinned clause sits on a commented
    line); the file is still read from disk, so the pins remain source-parsed and
    need no engine.
    """
    path = REPO_ROOT / "tortoise" / "projection" / "entities.py"
    return "\n".join(
        line for line in path.read_text().splitlines()
        if not line.lstrip().startswith("#")
    )


def _rows(cls: str):
    """Placement rows of one class, edge predicates EXPANDED to properties."""
    for section, prop, c in PLACEMENT_ROWS:
        if c != cls:
            continue
        for name in expand_edge_property(prop):
            yield SECTION_TABLE_ALL[section], name


def _view():
    nodes = [
        _node("Point", {"id": "p-1", "content": "c", "is_operator": False,
                        "point_kind": "claim", "props": {"openSetProp": 1}}),
        _node("Object", {"id": "acme", "name": "acme", "props": {}}),
    ]
    rels = [cp.RecordRelation(
        rel_type="aboutObject", src_label="Point", src_key="p-1",
        tgt_label="Object", tgt_key="acme", idx=0, props={})]
    return cp.RecordView(nodes=tuple(nodes), relations=tuple(rels))


def _node(label: str, row: dict):
    table = cp.NODE_TABLES[label]
    key_column = rp.snake(cp.KEY_PROPERTY[label])
    full = {c: None for c in TABLE_COLUMNS[table]}
    full[key_column] = f"k-{label}"
    full.update(row)
    full.setdefault("props", {})
    return cp.node_from_row(label, full)


# ── 1. the read shape is total and lossless ────────────────────────────────


def test_every_primary_property_has_a_canonical_spelling_that_is_projected():
    """The record's PRIMARY field set survives, spelled the GRAPH's way.

    `pointKind` is the graph property; `point_kind` is the column. A projection
    that wrote the column name would rename the whole namespace, and the S1/S3
    field contract would be broken by a spelling.
    """
    unresolved = []
    for table, prop in _rows("P"):
        if table not in cp.placement_columns():
            continue  # a predicate-only row (no property, e.g. `mitigated_by.*`)
        canonical = cp.canonical_property(table, rp.snake(prop))
        if canonical not in cp.projected_properties(table):
            unresolved.append((table, prop, canonical))
    assert not unresolved, (
        "a PRIMARY property the placement table declares is NOT projected — "
        f"the cache would be poorer than the record: {unresolved}"
    )


def test_a_primary_property_round_trips_through_a_record_row():
    """Arrange a real row shape and assert the value lands under its own name."""
    for table, prop in _rows("P"):
        if table == "relation":
            continue  # exercised by the relation test below
        if rp.snake(prop) not in TABLE_COLUMNS.get(table, ()):
            continue
        label = next(k for k, v in cp.NODE_TABLES.items() if v == table)
        canonical = cp.canonical_property(table, rp.snake(prop))
        got = _node(label, {rp.snake(prop): "v"})
        assert got.props.get(canonical) == "v", (
            f"{table}.{rp.snake(prop)} did not project to {canonical!r} "
            f"(the placement table spells it {prop!r})"
        )


def test_no_derived_property_has_a_record_column():
    """DERIVED/VOLATILE ⇒ no column — the schema is the first line, stated.

    This is WHY the behavioural refusal below is about the `props` tail and not
    a column: `#7968`'s pin already fails if a DERIVED name gets a column, so a
    record row cannot carry one. A future schema that adds one reds THIS test
    and `test_record_schema_placement.py` together, rather than silently
    promoting a recomputed value to authority.
    """
    stray = []
    for table, prop in _rows("D"):
        if rp.snake(prop) in TABLE_COLUMNS.get(table, ()):
            stray.append((table, prop))
    assert not stray, f"a DERIVED property has a record column: {stray}"


def test_a_derived_name_in_the_props_tail_is_refused():
    """S3's contract clause at its one reachable door.

    A column cannot carry a DERIVED name (the test above). The `props` tail
    CAN, and the tail is written verbatim — so the projection refuses a name
    the schema's own CHECK refuses, loudly. This is the mutation target: delete
    the guard in `_props_from_row` and the cache acquires `confidence`, an EP
    scalar recomputed from the structure, as stored authority.
    """
    with pytest.raises(cp.CacheProjectionError):
        _node("Point", {"id": "p-1", "props": {"confidence": 0.9}})
    with pytest.raises(cp.CacheProjectionError):
        _node("Object", {"id": "acme", "name": "acme",
                          "props": {"embedding": [0.1]}})


def test_a_derived_column_on_a_record_row_projects_nothing():
    """A FORWARD GUARD — today no DERIVED row has a column, so it cannot fire.

    `test_no_derived_property_has_a_record_column` asserts the precondition:
    `#7968`'s schema pin makes a DERIVED/VOLATILE name with a record column
    impossible, so the column door is shut by construction. The `props`-tail
    door is the reachable one and is exercised (non-vacuously) by
    `test_a_derived_name_in_the_props_tail_is_refused`. This test is kept as the
    guard for the day a future schema gives a DERIVED name a column: it asserts
    that precondition explicitly, so it can never pass by the loop body simply
    never running.
    """
    with_column = [
        (table, prop) for table, prop in _rows("D")
        if rp.snake(prop) in TABLE_COLUMNS.get(table, ())
    ]
    assert with_column == [], (
        "a DERIVED property now HAS a record column — the column door is live "
        f"and this test's body must decide it: {with_column}"
    )
    leaked = []
    for table, prop in _rows("D"):
        column = rp.snake(prop)
        if column not in TABLE_COLUMNS.get(table, ()):
            continue  # no column exists — covered by the test above
        label = next((k for k, v in cp.NODE_TABLES.items() if v == table), None)
        if label is None:
            continue
        got = _node(label, {column: [0.1, 0.2]})
        if prop in got.props or column in got.props:
            leaked.append((table, prop))
    assert not leaked, (
        "a DERIVED/VOLATILE property is projected as if it had a PRIMARY claim: "
        f"{leaked}"
    )


def test_the_unclassified_column_set_is_exactly_the_declared_four():
    """A NEW record column reds this suite rather than being silently dropped.

    Four columns are neither in the placement table nor refused by the schema:
    §2.8's narrowed carve-out exception (`baseline_alpha`/`baseline_beta`) and
    the two Subject lifecycle fields. They are declared in
    `cache_projection.ADDITIONAL_PRIMARY_COLUMNS`. Anything else is a column
    nobody classified, which is a LOUD failure — never a silent drop.
    """
    declared = {
        (table, column)
        for table, cols in cp.ADDITIONAL_PRIMARY_COLUMNS.items()
        for column in cols
    }
    unclassified = set()
    for table, columns in TABLE_COLUMNS.items():
        if table not in cp.placement_columns():
            continue
        mapped = set(cp.placement_columns()[table])
        refused = set(REFUSED.get(table, ()))
        rest = (set(columns) - mapped - refused
                - cp.bookkeeping_columns(table) - declared)
        unclassified |= {(table, c) for c in rest}
    assert unclassified == set(), (
        "the record gained a column the cache projection has not classified — "
        f"{sorted(unclassified)}"
    )
    assert declared, "the declaration itself vanished"


def test_an_unclassified_column_fails_loudly():
    """The fail-closed direction of the test above."""
    row = {**{c: None for c in TABLE_COLUMNS["point"]},
           "id": "p-1", "invented_by_a_future_migration": 1, "props": {}}
    with pytest.raises(cp.UnknownRecordColumn):
        cp.node_from_row("Point", row)


def test_the_placement_tables_ambiguities_are_exactly_the_five_declared():
    """A column the table spells two ways cannot be named by the record alone.

    ⚠️ A FINDING, pinned so it cannot drift: the placement table enumerates
    FIVE columns in BOTH spellings — four on `public.source` (`session_id`,
    `event_id`, `source_path`, `document_kind`) and one on `public.event`
    (`session_id`) — because the graph's document writer emits the snake forms
    while the session-capture writer mints the camel forms
    (`projection/entities.py::_SOURCE_NODE_PROPS` says so itself). `snake()` is
    therefore **not injective** over the graph's namespace, so a record-sourced
    cache cannot be byte-identical to a journal-sourced one for these five
    fields until one spelling wins (#7955/#7968).
    """
    assert sorted(cp.ambiguities()) == [
        "event.session_id",
        "source.document_kind",
        "source.event_id",
        "source.session_id",
        "source.source_path",
    ]
    # ⛔ The CHOICE is pinned, not just the SET: `canonical_property` delegating
    # to `placement_columns` would otherwise make the obvious assertion
    # tautological, and flipping `_prefer` to snake-first would leave the suite
    # green while silently renaming five graph properties.
    expected_canonical = {
        "event.session_id": "sessionId",
        "source.document_kind": "documentKind",
        "source.event_id": "eventId",
        "source.session_id": "sessionId",
        "source.source_path": "sourcePath",
    }
    got = {
        column: cp.canonical_property(*column.split(".", 1))
        for column in cp.ambiguities()
    }
    assert got == expected_canonical, (
        "the canonical (chosen) spelling drifted from camelCase-first — a "
        f"record-sourced cache would rename the field: {got}"
    )


# ── 2. the keys are the contract's, read from the schema ────────────────────


def test_key_properties_are_the_interface_e_contract_read_from_the_ddl():
    """Read from `node_identity`'s generated CASE, never restated here.

    ⚠️ The CASE is declared TWICE on disk: the CREATE TABLE in
    `20261012000001` and the forward re-declaration in `20261027000001` (#9881
    moved `Object`/`Subject` from `name` to `id`). The reader walks every record
    migration and takes the LAST declaration, so the map below is the POST-move
    contract — the one the database enforces. A reader pinned to the original
    file reported `name` while the live schema required `id`.
    """
    expected = {
        "Point": "id",
        "Event": "eventId",
        "Subject": "id",
        "Object": "id",
        "Source": "url",
        "Session": "id",
    }
    assert expected == cp.KEY_PROPERTY
    for name in ("20261012000001_record_event_stream.sql",
                 "20261027000001_record_semantic_id_key.sql"):
        sql = (MIGRATIONS / name).read_text()
        assert "key_property" in sql, "the DDL moved — re-read the parser"
    # The pre-move declaration is still ON DISK (append-only history) and the
    # reader must NOT be reading it: this is the half that would silently revert
    # the key if the parser went back to one file.
    original = (MIGRATIONS / "20261012000001_record_event_stream.sql").read_text()
    assert "WHEN 'Object'  THEN 'name'" in original


def test_a_node_key_is_the_labels_contract_key():
    # #9881: `Object`/`Subject` key on the stable `id`; the name is a mutable
    # natural key, so it can no longer BE the key.
    assert _node("Subject", {"id": "acme", "name": "acme"}).key == "acme"
    assert _node("Point", {"id": "1a-1"}).key == "1a-1"
    assert _node("Source", {"url": "https://x"}).key == "https://x"
    assert _node("Event", {"event_id": "ev-1"}).key == "ev-1"
    with pytest.raises(cp.UnprojectableRecord):
        _node("Point", {"id": None})


def test_the_key_property_is_projected_as_a_property_too():
    """`id`/`name`/`url` are PRIMARY properties, not only MERGE coordinates."""
    assert _node("Subject", {"id": "acme", "name": "acme"}).props["name"] == "acme"
    assert _node("Source", {"url": "https://x"}).props["url"] == "https://x"


def test_the_props_tail_keeps_its_verbatim_property_names():
    """Open-set properties ride the tail unmapped — `SET n += $props`'s half."""
    got = _node("Point", {"id": "p-1",
                          "props": {"someOpenSetKey": 7, "another_one": "x"}})
    assert got.props["someOpenSetKey"] == 7
    assert got.props["another_one"] == "x"


# ── 2b. the fold's ON CREATE defaults (#9634) ──────────────────────────────
#
# `public.subject.subject_kind` and `public.object.object_kind`/`title` are
# NULLABLE, and a record row can be silent about the discriminator: #9572 (open
# branch `fix/9572-discriminator-clobber`, unmerged) removes the fabricated
# default from every write door — correctly, because the record must not state a
# kind nobody named. The FOLD supplies one under `ON CREATE` regardless, and
# `_props_from_row` drops a NULL column, so without the defaults below a
# record-sourced rebuild materialised a `:Subject` with no `subjectKind` — a
# property ONTOLOGY §4.2 (Subject) / §4.3 (Object) lists as REQUIRED — while
# `rebuild_all` (journal replay, which runs the fold) wrote `'other'`. Live ≠
# record, for that property. ⚠️ On this tree the discriminator is still
# FABRICATED at every write door, so the table is the record→cache half that
# must land WITH or before #9572, not a fix for a live-reachable defect.
#
# ⚠️ `Source.sourceKind` is the SAME construction and is deliberately NOT here:
# the fold has two defaults for it (`"document"` from `_upsert_source`,
# `"agentSession"` for a `session:` ref from `_mint_source_stub`), so a NULL
# row cannot be resolved without the prefix rule — defaulting to `"document"`
# would fabricate the wrong kind on every session Source. #9710.


def test_a_record_row_silent_about_the_kind_gets_the_folds_own_default():
    """A NULL discriminator materializes the constant the FOLD would write.

    This is the measured residual of #9572: the record is silent (NULL column,
    no tail key), so the cache must fall back to the fold's own `ON CREATE`
    constant rather than materializing the node with the property MISSING.
    """
    subject = _node("Subject", {"id": "Probe", "name": "Probe",
                                "subject_kind": None})
    assert subject.props["subjectKind"] == "other", (
        "a record-sourced :Subject lost the discriminator the live fold writes"
    )
    obj = _node("Object", {"id": "acme", "name": "acme",
                           "object_kind": None, "title": None})
    assert obj.props["objectKind"] == "other"
    assert obj.props["title"] == ""


def test_the_source_discriminator_is_declared_excluded_not_guessed():
    """The label whose default is AMBIGUOUS stays out of the table — pinned.

    `_upsert_source` defaults `sourceKind` to `"document"`;
    `_mint_source_stub` defaults a `session:` ref to `"agentSession"` (its own
    docstring: minting those as `document` was the wart #5012 removed). A loader
    default of `"document"` would therefore FABRICATE the wrong kind on every
    session Source — so a NULL `source_kind` row must stay silent until the
    prefix rule (or a record column) exists (#9710). This test fails if a future
    editor "completes" the table by adding it.
    """
    assert "Source" not in cp.FOLD_ON_CREATE_DEFAULTS, (
        "Source.sourceKind has two fold defaults ('document' / 'agentSession' "
        "for session: refs) — a single constant here fabricates a kind; see "
        "#9710"
    )
    source = _node("Source", {"url": "session:abc", "source_kind": None})
    assert "sourceKind" not in source.props, (
        "a session Source whose record row is silent about sourceKind was given "
        "a kind the fold never wrote"
    )


def test_the_subject_null_case_is_fold_parity_and_the_param_carries_no_default():
    """Subject's null half is the FOLD's rule, exactly like Object's (#9572).

    The rationale for `FOLD_ON_CREATE_DEFAULTS` must not claim parity it does
    not have — nor a DECISION it no longer makes. Object's clauses coalesce, so
    null-as-silence is what the fold does there; since #9572 Subject's
    `ON CREATE` does the same (`s.subjectKind=coalesce($sk, 'other')`) with the
    parameter built as `ev.get("subject_kind")` — a NULL default. An ABSENT key
    and a PRESENT-and-null key therefore behave identically (both `'other'`), so
    the loader's `'other'` fill reproduces the fold instead of overriding it.

    The fabricated default is what this pin keeps out, and it is not cosmetic:
    with `ev.get("subject_kind", "other")` restored, "the caller named none"
    and "the caller named other" are the SAME value at the `ON MATCH` arm
    (`coalesce($sk, s.subjectKind)`), so a bare re-create clobbers a stored
    `team` — the #9572 defect itself.

    Source-parsed like the sibling pin, so the claim reds here if the fold
    changes shape or the declaration drifts back to the discharged residual.
    """
    fold = _fold_source()
    # Object: null-as-silence IS the fold's rule (both clauses coalesce).
    assert re.search(r"o\.objectKind=coalesce\(\$ok, 'other'\)", fold)
    assert re.search(r"o\.title=coalesce\(\$title, ''\)", fold)
    # Subject: the same rule ON CREATE (#9572).
    # ⚠️ Truncate on `ON MATCH SET`, NOT `ON MATCH`: the #9572 comment block sits
    # between `s.name=$name` and the clause it explains and contains the words
    # "ON MATCH as", which cut this slice 90 characters in and made the guards
    # below inspect a comment. `_fold_source()` removes that class of trap too.
    create = fold[fold.index("ON CREATE SET s.id=$id, s.name=$name"):]
    create = create[:create.index("ON MATCH SET")]
    assert re.search(r"s\.subjectKind=coalesce\(\$sk, 'other'\)", create), (
        "the Subject `ON CREATE` clause no longer supplies the fold's own "
        "'other' fallback — re-derive FOLD_ON_CREATE_DEFAULTS from the fold"
    )
    assert not re.search(r"s\.subjectKind=\$sk", create), (
        "the Subject `ON CREATE` clause went back to writing `$sk` bare, which "
        "REMOVES the property on a present-null key (#7936) and re-opens the "
        "absent-vs-present-null split this pin discharged"
    )
    # The parameter must stay NULL-defaulted. A fabricated default here is what
    # makes a caller value and an absent one the same value (#9572).
    assert re.search(r'"sk":\s*ev\.get\("subject_kind"\)', fold), (
        "the Subject parameter is not NULL-defaulted any more — a bare "
        "re-create must send NULL, never a kind the caller did not name"
    )
    assert not re.search(r'"sk":\s*ev\.get\("subject_kind",', fold), (
        "the Subject parameter regained a fabricated default — at "
        "`coalesce($sk, s.subjectKind)` it is indistinguishable from a caller "
        "naming that kind, so a bare re-create clobbers a stored kind (#9572)"
    )
    # ON MATCH must PRESERVE what is stored: the invariant the fix protects.
    assert re.search(
        r"s\.subjectKind=coalesce\(\$sk, s\.subjectKind\)", fold), (
        "the Subject `ON MATCH` arm no longer preserves a stored kind"
    )
    # And the declaration says PARITY where an editor of the table reads it,
    # with the discharged residual recorded rather than left standing.
    src = (REPO_ROOT / "tortoise" / "cache_projection.py").read_text()
    assert re.search(r"THE SUBJECT HALF IS PARITY TOO \(#9572\)", src), (
        "FOLD_ON_CREATE_DEFAULTS lost the note declaring that Subject's null "
        "half is fold parity (#9572) — the table would read as a loader "
        "decision it no longer is"
    )
    assert not re.search(r"RESIDUAL — the Subject clause does NOT coalesce", src), (
        "FOLD_ON_CREATE_DEFAULTS still claims the pre-#9572 residual: the fold "
        "COALESCES on Subject now, so that note is stale in the direction that "
        "reads as a decision the loader makes"
    )


def test_a_named_kind_and_a_tail_kind_are_never_overwritten_by_the_default():
    """The default is a FALLBACK, not a write — a named kind wins, wherever it rode."""
    named = _node("Subject", {"id": "Probe", "name": "Probe",
                             "subject_kind": "team"})
    assert named.props["subjectKind"] == "team"
    # A tail value outranks the fallback. ⚠️ The FOLD does not read a tail
    # `subjectKind` (it is in `_SUBJECT_HANDLED`, so `_persist_extra_props` skips
    # it), so this ordering is the LOADER's contract, not a parity claim: the
    # loader must never replace something the record said with a fallback.
    from_tail = _node("Subject", {"id": "Probe", "name": "Probe",
                                  "subject_kind": None,
                                  "props": {"subjectKind": "role"}})
    assert from_tail.props["subjectKind"] == "role"
    # A tail NULL takes the fallback too — and since #9572 that IS fold parity:
    # `s.subjectKind=coalesce($sk, 'other')` with `$sk` NULL-defaulted makes a
    # present-null key and an absent column behave identically, so the record's
    # silence means the same thing on both sides of the cache. Declared at
    # `cache_projection.FOLD_ON_CREATE_DEFAULTS`, and pinned by
    # `test_the_subject_null_case_is_fold_parity_and_the_param_carries_no_default`.
    null_tail = _node("Subject", {"id": "Probe", "name": "Probe",
                                  "subject_kind": None,
                                  "props": {"subjectKind": None}})
    assert null_tail.props["subjectKind"] == "other"
    named_obj = _node("Object", {"id": "acme", "name": "acme",
                                 "object_kind": "org", "title": "Acme"})
    assert (named_obj.props["objectKind"], named_obj.props["title"]) == (
        "org", "Acme")


def test_the_declared_fold_defaults_are_the_folds_own_constants():
    """PIN the declaration against the fold's clauses, so drift reds HERE.

    A second declaration is only safe when something fails if it goes stale.
    `FOLD_ON_CREATE_DEFAULTS` restates three constants the fold owns
    (`_upsert_subject`'s `coalesce($sk, 'other')` fallback and the
    `subjectKind` parameter that feeds it, plus `_upsert_object`'s two
    `coalesce` fallbacks); if the fold changes one, this test reds with the
    constant named, rather than silently re-opening the live ≠ record gap.

    Source-parsed on purpose: the alternative is a live graph, and a pin that
    only runs where an engine is reachable cannot guard the ordinary lane. It
    reads `_fold_source()` (comments stripped) because the fold's comment blocks
    quote the clause shapes, so a comment would otherwise satisfy a pattern. The
    live half of the same claim is
    `tests/test_cache_projection_live.py::test_a_record_sourced_node_carries_the_discriminator_the_fold_supplies`.

    ⚠️ SCOPE, stated so the pin is not read as more than it is: it checks the
    clauses it NAMES — it is not a discovery sweep, so a fold constant nobody
    listed here is invisible to it (the exclusion list in
    `FOLD_ON_CREATE_DEFAULTS` is hand-maintained; #9710 carries the known
    members). Each pattern targets the `ON CREATE SET` node property, not a
    bare params entry, because a params default that the clause stops APPLYING
    would leave this table fabricating a default.
    """
    fold = _fold_source()
    antecedents = {
        # both halves: the `ON CREATE SET` write AND the parameter that feeds it
        # (#9572 — the write COALESCEs and the parameter is NULL-defaulted, so a
        # bare re-create sends no kind and `ON MATCH` preserves the stored one)
        ("Subject", "subjectKind"): (
            r"s\.subjectKind=coalesce\(\$sk, 'other'\)",
            r'"sk":\s*ev\.get\("subject_kind"\)',
        ),
        ("Object", "objectKind"): (
            r"o\.objectKind=coalesce\(\$ok, 'other'\)",
        ),
        ("Object", "title"): (
            r"o\.title=coalesce\(\$title, ''\)",
        ),
    }
    declared = {
        (label, prop): value
        for label, props in cp.FOLD_ON_CREATE_DEFAULTS.items()
        for prop, value in props.items()
    }
    assert set(declared) == set(antecedents), (
        "the declared fold defaults and the pinned fold clauses disagree — a "
        f"default was added or removed: declared-only "
        f"{sorted(set(declared) - set(antecedents))}, pinned-only "
        f"{sorted(set(antecedents) - set(declared))}"
    )
    for key, patterns in antecedents.items():
        for pattern in patterns:
            assert re.search(pattern, fold), (
                f"the fold clause that supplies {key[0]}.{key[1]}'s default is "
                f"gone or changed (/{pattern}/ not found in "
                "projection/entities.py) — re-derive FOLD_ON_CREATE_DEFAULTS "
                "from the fold, do not guess. A NEW constant the fold owns is "
                "the same finding: this table is the record→cache side of it."
            )
    # A constant with a SECOND home drifts silently: `_mint_subject_stub`
    # (`projection/edges.py`) writes the same `subjectKind='other'` literal for a
    # stub-minted Subject, so a change there would red nothing above.
    edges = (REPO_ROOT / "tortoise" / "projection" / "edges.py").read_text()
    assert re.search(r"s\.subjectKind='other'", edges), (
        "`_mint_subject_stub` in projection/edges.py no longer writes the same "
        "`subjectKind='other'` literal this table transcribes for Subject — the "
        "stub-minted half of the constant drifted. Re-derive, do not guess."
    )
    # The Object twin's SECOND home is RETIRED, and asserting the literal is
    # GONE is the pin that keeps it retired. `_event_plain_merge` used to write
    # `o.objectKind='other'` for the stub Object an event's mechanism link
    # minted; #3590 D1/D9 removed that mint (an `EventRecorded`'s `object` is a
    # MENTION: it resolves to a live holder or drops its link and mints
    # nothing), so the fold has exactly ONE home for this constant now — the
    # `ON CREATE SET … coalesce($ok, 'other')` clause pinned above. The
    # Subject twin survives (`_mint_subject_stub`, asserted just above, the
    # recorded bounded exception) — which is why this half is asserted in the
    # NEGATIVE: a lane that re-adds an Object stub mint must come here and
    # re-derive the table, instead of silently re-opening the two-home drift
    # this pin exists to catch.
    assert not re.search(r"o\.objectKind='other'", fold), (
        "`o.objectKind='other'` is back in the fold source — that literal "
        "belonged to the fold-side Object name-stub mint #3590 D1 retired. If "
        "a stub mint is genuinely returning, re-derive "
        "FOLD_ON_CREATE_DEFAULTS and this pin together."
    )
    # The values the patterns above force, asserted so the table cannot drift
    # while the patterns still match (they pin the CONSTRUCTION, not the literal).
    assert declared[("Subject", "subjectKind")] == "other"
    assert declared[("Object", "objectKind")] == "other"
    assert declared[("Object", "title")] == ""


# ── 3. one-way by construction ──────────────────────────────────────────────


def test_the_materializer_issues_no_cache_read():
    """No statement on this path may consult the cache to decide what to write.

    The projection builds its whole plan from the RECORD; the only read the
    plan contains is the endpoint `MATCH` inside a relation upsert, which
    resolves a record coordinate. A statement that RETURNed properties, or that
    read `:Meta`, would be the cache deciding — the defect §2.2 names.
    """
    view = _view()
    for stmt in cp.record_plan(view.nodes, view.relations):
        text = stmt.cypher
        assert ":Meta" not in text
        assert "RETURN" not in text, f"a read leaked into the plan: {text}"
        assert "MERGE" in text


def test_relation_endpoints_resolve_by_label_and_key_not_by_a_bare_id():
    """Interface E's rule, at the edge: a rebuilt edge binds by `(label, key)`.

    Since #9881 a `:Subject`'s contract key IS its `id` (the name became a
    mutable natural key), so the rebound edge binds `Subject{id: …}` — and the
    `relation` row's `tgt_key` holds that id, not the name. The rule this pins
    is unchanged: the binding is the label's DECLARED key column, never a bare
    `{id: $key}` guess (`#2489`/`#5086`); what moved is which column that is.
    """
    rel = cp.RecordRelation(
        rel_type="aboutSubject", src_label="Point", src_key="p-1",
        tgt_label="Subject", tgt_key="acme", idx=0, props={})
    compact = cp.relation_statement(rel).cypher.replace(" ", "")
    assert "tgt:Subject{id:$tgt_key}" in compact, compact
    assert "src:Point{id:$src_key}" in compact, compact


def test_relation_props_ride_the_edge_and_the_predicate_is_the_rel_type():
    rel = cp.RecordRelation(
        rel_type="IMPL", src_label="Point", src_key="op-1",
        tgt_label="Point", tgt_key="in-1", idx=2,
        props={"label": "addresses", "anchor": "extractedFrom"})
    stmt = cp.relation_statement(rel)
    assert "MERGE (src)-[r:IMPL {idx:$idx}]->(tgt)" in stmt.cypher
    assert stmt.params["idx"] == 2
    assert stmt.params["props"]["anchor"] == "extractedFrom"


# ── 4. rebuild == record, and idempotent ────────────────────────────────────


def test_rebuild_equals_record_no_extra_node_and_no_extra_field():
    """The rebuild's field set is EXACTLY the record's — never a superset.

    ⚠️ ONE DECLARED EXCEPTION (#9634): the properties in
    `cp.FOLD_ON_CREATE_DEFAULTS` are the FOLD's `ON CREATE` constants, applied
    where the record row is silent, so the view carries them before this test
    runs. That is why the comparison below cannot see this class — the bound on
    the exception is
    `test_the_declared_fold_defaults_are_the_folds_own_constants`, not this
    assertion. Everything NOT in that table is still pinned exactly here.

    This is S3's contract clause at the STATEMENT level: a superset would be the
    cache carrying state the record does not hold, i.e. the cache becoming a
    second authority. ⛔ The assertion runs over `plan.statements` — the thing
    that decides what is written — NOT over `plan.node_props()`, which returns
    `RecordNode.props` and would compare the record to itself (a mutation that
    made `node_statement` drop a field was invisible to the model-level check).
    """
    view = _view()
    plan = cp.record_plan(view.nodes, view.relations)
    assert plan.node_keys() == {(n.label, n.key) for n in view.nodes}
    for node in view.nodes:
        stmt = next(
            s for s in plan.statements
            if s.cypher.startswith(f"MERGE (n:{node.label} ")
            and s.params["key"] == node.key
        )
        written = set(stmt.params["props"])
        if "embedding" in stmt.params:
            # The vector is written as its own clause, not in the props map.
            written.add("embedding")
        assert written == set(node.props), (
            f"{node.label}/{node.key}: the cache would carry "
            f"{sorted(set(node.props) - written)} less than the record and "
            f"{sorted(written - set(node.props))} beyond it"
        )


def test_projection_is_idempotent():
    view = _view()
    first = cp.record_plan(view.nodes, view.relations)
    second = cp.record_plan(view.nodes, view.relations)
    assert [(s.cypher, s.params) for s in first] == \
        [(s.cypher, s.params) for s in second]


def test_a_point_embedding_is_written_with_the_vertex_form():
    got = _node("Point", {"id": "p-1", "embedding": [0.5, 0.25]})
    stmt = cp.node_statement(got)
    assert "vecf32($embedding)" in stmt.cypher
    assert stmt.params["embedding"] == [0.5, 0.25]


def test_a_verbatim_embedding_is_written_verbatim():
    """`embedding_verbatim` is the caller-owned vector's storage FORM."""
    got = _node("Point", {"id": "p-1", "embedding": [0.5],
                          "embedding_verbatim": True})
    stmt = cp.node_statement(got)
    assert "vecf32" not in stmt.cypher
    assert stmt.params["embedding"] == [0.5]
    # ⛔ The marker is PRIMARY and must survive WITH the vector, not only without
    # it: a record-sourced rebuild that dropped it leaves a caller-owned float64
    # vector unmarked, and the next journaled re-emit narrows it through
    # `vecf32` (#5004 — the `live != rebuild` class this flag prevents).
    assert stmt.params["props"]["embedding_verbatim"] is True


def test_a_verbatim_marker_without_a_vector_is_still_written():
    """The no-vector half of the same PRIMARY field."""
    got = _node("Point", {"id": "p-2", "embedding_verbatim": True})
    stmt = cp.node_statement(got)
    assert stmt.params["props"]["embedding_verbatim"] is True


def test_the_projection_narrows_exactly_the_declared_stored_vector_props():
    """#8615 — WHICH properties ride `vecf32` is declared once, in `stored_form`.

    The record↔cache check (`tortoise/reconciliation.py`) narrows the truth side
    by that same declaration, so the two cannot disagree about the form the
    cache holds. A second, hand-copied list is how this drifts.
    """
    got = _node("Point", {"id": "p-1", "embedding": [0.5, 0.25]})
    stmt = cp.node_statement(got)
    narrowed = {prop for prop in cp.stored_vector_props()
                if f"vecf32(${prop})" in stmt.cypher}
    assert narrowed == set(cp.stored_vector_props())
    assert set(cp.stored_vector_props()) == {"embedding"}
    # each declared vector property rides as its OWN clause, never inside props
    for prop in cp.stored_vector_props():
        assert prop not in stmt.params["props"]


def test_a_verbatim_projection_narrows_no_declared_vector_prop():
    got = _node("Point", {"id": "p-1", "embedding": [0.5],
                          "embedding_verbatim": True})
    stmt = cp.node_statement(got)
    for prop in cp.stored_vector_props():
        assert f"vecf32(${prop})" not in stmt.cypher
        assert f"n.{prop} = ${prop}" in stmt.cypher


def test_is_verbatim_is_an_or_across_the_sides_it_is_given():
    """One side's flag is enough — a dropped flag must not hide a narrowing."""
    from tortoise.stored_form import is_verbatim

    assert is_verbatim({}, {}) is False
    assert is_verbatim({"embedding_verbatim": None}) is False
    assert is_verbatim({"embedding_verbatim": False}) is False
    assert is_verbatim({"embedding_verbatim": True}, {}) is True
    assert is_verbatim({}, {"embedding_verbatim": True}) is True


def test_the_stored_width_narrowing_is_total_and_matches_float32():
    """`struct`'s float32 round trip — the number the engine actually holds."""
    from tortoise.stored_form import narrow_to_stored_width

    assert narrow_to_stored_width([0.1, 0.2]) == [
        0.10000000149011612, 0.20000000298023224]
    assert narrow_to_stored_width([]) == []
    # NOT a numeric vector → None ("not comparable this way"), never a raise
    for bad in ([None], ["x"], [object()], [1e300], None, 7, "abc"):
        assert narrow_to_stored_width(bad) is None


def test_a_non_allowlisted_rel_type_is_refused_before_any_statement_is_built():
    """`rel_type` is interpolated into the query STRUCTURE — it must be allowed.

    `public.relation.rel_type` is `text` with no CHECK, so an arbitrary string
    reaches the interpolation. The allowlist is the same one every comparable
    site uses; refusing here means `record_plan` raises BEFORE the wipe, so a
    hostile row cannot build a plan that destroys the cache and then fails.
    """
    bad = cp.RecordRelation(rel_type="x]->(z) DETACH DELETE z //",
                            src_label="Point", src_key="a",
                            tgt_label="Point", tgt_key="b")
    with pytest.raises(cp.UnprojectableRecord):
        cp.relation_statement(bad)
    with pytest.raises(cp.UnprojectableRecord):
        cp.record_plan([], [bad])
    ok = cp.RecordRelation(rel_type="IMPL", src_label="Point", src_key="a",
                           tgt_label="Point", tgt_key="b")
    assert "r:IMPL" in cp.relation_statement(ok).cypher


def test_a_refused_record_row_leaves_the_cache_untouched():
    """A projection that refuses must not half-apply: the plan is built first."""
    bad = list(_view().nodes)
    bad.append({})  # not a RecordNode — the plan cannot be built at all
    with pytest.raises(AttributeError):
        cp.record_plan(bad)


# ── 5. the declaration: not-hot, never empty ────────────────────────────────


def test_an_unmaterialized_record_key_is_not_hot_and_never_empty():
    """The load-bearing consequence, end to end over the REAL marker.

    A record that holds two keys, a projection that materialised one: the
    unmaterialised key is NOT declared hot, so a zero-hit read for it is
    `not-hot`, and an `empty` status over it becomes `degraded` — never the
    `empty` that is indistinguishable from *"no such node"*.
    """
    record_keys = [("Point", "p-1"), ("Point", "p-2")]
    materialized = [("Point", "p-1")]
    decl = cp.declaration_for(record_keys, materialized)
    assert decl.coverage == COVERAGE_PARTIAL
    assert decl.covers(cp.declared_key("Point", "p-1"))
    assert not decl.covers(cp.declared_key("Point", "p-2"))

    marker = mark_residency(decl, hits=0, key=cp.declared_key("Point", "p-2"))
    assert marker == RESIDENCY_NOT_HOT
    assert apply_residency(STATUS_EMPTY, marker) == STATUS_DEGRADED
    # ...and the covered key still reads `hot`, so the marker did not simply
    # fire for everything.
    assert mark_residency(
        decl, hits=0, key=cp.declared_key("Point", "p-1")) == RESIDENCY_HOT


def test_a_homonymous_pair_across_labels_is_not_collapsed_to_one_hot_key():
    """⛔ THE IDENTITY IS `(label, key)`, NEVER THE KEY ALONE.

    `KEY_PROPERTY` gives `Subject` and `Object` the same key (`name`), so
    `Object/acme` and `Subject/acme` are TWO record nodes with ONE key string.
    A declaration that flattens them declares a **hot key that was never
    written**, and a zero-hit read over the OMITTED label then stands as
    `empty` — the §17.2 *"no such node"* false negative this module exists to
    prevent. Reachable through the documented `limit` mode: nodes are sorted by
    `(label, key)` and truncated, so a bound that cuts between two labels
    produces a partial write and (before this fix) a `complete` declaration.
    """
    record_keys = [("Object", "acme"), ("Subject", "acme")]
    materialized = [("Object", "acme")]
    decl = cp.declaration_for(record_keys, materialized)
    assert decl.coverage == COVERAGE_PARTIAL, (
        "the declaration claimed complete coverage for a label it did not write"
    )
    assert decl.hot_keys == frozenset({"Object:acme"})
    assert decl.covers(cp.declared_key("Object", "acme"))
    assert not decl.covers(cp.declared_key("Subject", "acme"))

    marker = mark_residency(decl, hits=0, key=cp.declared_key("Subject", "acme"))
    assert marker == RESIDENCY_NOT_HOT
    assert apply_residency(STATUS_EMPTY, marker) == STATUS_DEGRADED
    # The written label is still hot — the marker did not simply fire always.
    assert mark_residency(
        decl, hits=0, key=cp.declared_key("Object", "acme")) == RESIDENCY_HOT
    # ...and the soundness check sees the collapse too: a `complete` declaration
    # over this partial write is UNSOUND, which is what makes `declaration_for`
    # above non-optional rather than a nicety.
    assert cp.declaration_is_sound(record_keys, materialized, decl) is True
    lied = ResidencyDeclaration(coverage=COVERAGE_COMPLETE, source="liar")
    assert cp.declaration_is_sound(record_keys, materialized, lied) is False


def test_a_complete_projection_declares_complete_coverage():
    keys = [("Point", "p-1"), ("Object", "acme")]
    decl = cp.declaration_for(keys, keys)
    assert decl.coverage == COVERAGE_COMPLETE
    assert mark_residency(
        decl, hits=0, key=cp.declared_key("Point", "p-9")) == RESIDENCY_HOT


def test_declaring_a_key_hot_that_was_not_written_is_the_mutation_this_catches():
    """The mutation: a projection that claims complete coverage unconditionally.

    It is the one-line "fix" that would make the previous test pass vacuously —
    `coverage=complete` makes the marker unable to fire at all (§17.2: *with a
    complete cache the marker CANNOT fire*). Asserted here so the declaration
    cannot silently become a constant.
    """
    record_keys = [("Point", "p-1"), ("Point", "p-2")]
    decl = cp.declaration_for(record_keys, [("Point", "p-1")])
    assert decl.coverage != COVERAGE_COMPLETE, (
        "the declaration claimed complete coverage for a key it did not write"
    )
    lied = ResidencyDeclaration(coverage=COVERAGE_COMPLETE, source="liar")
    assert mark_residency(
        lied, hits=0, key=cp.declared_key("Point", "p-2")) == RESIDENCY_HOT
    assert cp.declaration_is_sound(
        record_keys, [("Point", "p-1")], lied) is False


def test_the_scope_prefixes_the_declared_keys():
    """Two graphs share one cache instance, so the hot set is scoped."""
    decl = cp.declaration_for([("Point", "p-1"), ("Point", "p-2")],
                              [("Point", "p-1")], scope="g_a")
    assert decl.hot_keys == frozenset({"g_a:Point:p-1"})
    assert decl.covers(cp.declared_key("Point", "p-1", scope="g_a"))
    assert not decl.covers(cp.declared_key("Point", "p-1"))


def test_an_empty_record_declares_partial_not_complete():
    """`complete` over an EMPTY scope would claim coverage nothing measured."""
    decl = cp.declaration_for([], [])
    assert decl.coverage == COVERAGE_PARTIAL


# ── 6. the record read shape, over a (fake) connection ──────────────────────


#: The statement's OWN unwrap marker, from `cache_projection._PAYLOAD_EVENT`:
#: the reader declares that it unwraps a nested `payload -> 'event'` envelope,
#: and `_project_journal_rows` honours that declaration (and only that one).
_UNWRAP_MARK = "jsonb_typeof(payload -> 'event') = 'object'"


def _project_journal_rows(sql: str, rows, params):
    """Emulate the JSONB projection `_LIFECYCLE_EVENTS_SQL` asks Postgres for.

    The reader's columns are computed by JSON operators, which a DB-free cursor
    cannot run — so this fake honours the statement's OWN declared semantics:

    * it unwraps `payload -> 'event'` only when the SQL asks for it (the
      ``_UNWRAP_MARK`` substring), which is what Postgres does too; and
    * it applies the single-Object narrowing only when the `to_jsonb(`
      containment clause is present, using the trailing `target` bind.

    A statement that reads the top level only therefore leaves a
    nested-envelope row with no `event_kind` — exactly what Postgres would
    return — so a reader that LOST the unwrap fails the fold instead of passing
    on a fake that pre-flattened the shape. Rows already shaped as columns
    (`payload` absent) pass through unchanged.
    """
    unwrap = _UNWRAP_MARK in sql
    narrow = "to_jsonb(" in sql
    target = params[-1] if (narrow and params) else None
    out = []
    for row in rows:
        payload = row.get("payload")
        if isinstance(payload, dict) or "payload" in row:
            row = dict(row)
            nested = payload.get("event") if isinstance(payload, dict) else None
            src = nested if (unwrap and isinstance(nested, dict)) else (payload or {})
            row["event_kind"] = src.get("eventKind")
            row["object_name"] = src.get("object")
            row["about_entities"] = src.get("about_entities")
            row["about_entities_camel"] = src.get("aboutEntities")
        if narrow:
            about = row.get("about_entities") or row.get("about_entities_camel")
            names = [row.get("object_name")]
            names.extend(about if isinstance(about, list) else [about])
            if target not in names:
                continue
        out.append(row)
    return out


class _FakeCursor:
    def __init__(self, rows_by_table, seen: list[str] | None = None):
        self._rows = rows_by_table
        self._current: list[dict] = []
        self._seen = seen if seen is not None else []
        self._last = ""
        self._tenant = None

    def execute(self, sql, params=None):
        self._seen.append(sql)
        self._last = sql
        if sql == cp._rp.TENANT_GUC_SQL:
            self._tenant = params[0] if params else None
        m = re.search(r"FROM public\.(\w+)", sql)
        table = m.group(1) if m else None
        self._current = self._rows.get(table, []) if table else []
        if table == "journal_event":
            self._current = _project_journal_rows(sql, self._current, params)

    def fetchall(self):
        return list(self._current)

    def fetchone(self):
        # R6 (#9600 review cycle 2): the reader installs the tenant scope, whose
        # single verify statement returns (tenant, bypassing). This fake models
        # a correctly scoped, NON-bypassing connection.
        if self._last == cp._rp.TENANT_SCOPE_SQL:
            return (self._tenant, False)
        return (self._tenant,)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeConn:
    def __init__(self, rows_by_table, seen: list[str] | None = None):
        self._rows = rows_by_table
        self._seen: list[str] = seen if seen is not None else []

    def cursor(self):
        return _FakeCursor(self._rows, self._seen)


def test_read_record_view_keys_by_the_contract_key():
    conn = _FakeConn({
        "point": [{"id": "p-1", "content": "c", "props": {}},
                  {"id": "p-2", "content": "d", "props": {}}],
        "object": [{"id": "acme", "name": "acme", "props": {}}],
        "relation": [{"rel_type": "aboutObject", "src_label": "Point",
                      "src_key": "p-1", "tgt_label": "Object",
                      "tgt_key": "acme", "idx": 0, "props": {}}],
    })
    view = cp.read_record_view(conn, org_id="o", graph_id="g")
    assert {n.key for n in view.nodes} == {"p-1", "p-2", "acme"}
    assert [r.rel_type for r in view.relations] == ["aboutObject"]
    assert view.record_keys == (("Point", "p-1"), ("Point", "p-2"),
                                ("Object", "acme"))


def test_read_record_view_asks_for_the_scope_it_was_given():
    """A reader that forgot the tenancy predicate would project every tenant.

    #9600 review cycle 2 (P1): the reader also has to install the SCOPE those
    predicates are evaluated against — under the app-role DSN a missing GUC is
    zero rows, not an error. So the first two statements are the tenant prologue
    and its verification, and every DATA statement carries both predicates.
    """
    seen: list[str] = []

    class _Cursor(_FakeCursor):
        def execute(self, sql, params=None):
            seen.append(sql)
            super().execute(sql, params)

    class _Conn:
        def cursor(self):
            return _Cursor({})

    cp.read_record_view(_Conn(), org_id="o", graph_id="g")
    assert seen, "the reader issued no query at all"
    assert seen[0] == cp._rp.TENANT_GUC_SQL, (
        "the reader selected before scoping the connection — every row it got "
        "back came from a connection with no tenant GUC")
    assert seen[1] == cp._rp.TENANT_SCOPE_SQL, (
        "the reader did not verify the tenant it set (nor the role it set it "
        "for)")
    for sql in seen[2:]:
        assert "org_id = %s" in sql and "graph_id = %s" in sql


# ── 3c. §2.6's DERIVED half — the cache COMPUTES what the record may not store


def test_the_cache_computes_the_lifecycle_status_the_record_may_not_store():
    """#9974 — `status` is DERIVED (§2.6), so something must DERIVE it.

    The measured defect this pins. §2.6 (`02-identity-and-placement.md`)
    classifies `Object.status` / `Subject.status` as a **cache** — *"the journal
    value is not the record"* — and both schemas refuse the name in `props`, so
    the record correctly stores nothing. Nothing then COMPUTED it: the
    record-sourced projection wrote the record's columns and invoked no fold, so
    after `rebuild_from_record` a superseded Object carried NO `status` at all,
    `live._terminal_excluded` read the absence as LIVE (the legacy-node rule),
    and `entity_identity.resolve_entity_id` resolved the superseded name.

    The carrier is the record's own PRIMARY column: `_fold_object_superseded`
    (Object) and `EntityMutated op=supersede`'s generic fold (Subject, #1423)
    each write `status='superseded'` and `supersededBy` in ONE statement, so
    `supersededBy` present ⇒ the fold set `'superseded'`.
    """
    obj = _node("Object", {"id": "o-1", "name": "alpha",
                           "superseded_by": "alpha-v2"})
    assert obj.props["status"] == "superseded", (
        "a rebuild materialised a superseded Object with no status — the "
        "record refuses the name and the cache did not derive it (#9974)"
    )
    # The record's PRIMARY carrier still rides through unchanged (no over-drop).
    assert obj.props["supersededBy"] == "alpha-v2"

    subj = _node("Subject", {"id": "s-1", "name": "role",
                             "subject_kind": "team",
                             "superseded_by": "role-v2"})
    assert subj.props["status"] == "superseded", (
        "the Subject half of #1423's supersession is the same §2.6 cache and "
        "the same carrier — it must derive identically"
    )
    assert subj.props["supersededBy"] == "role-v2"

    # …and it reaches the STATEMENT, not just the model: an assertion over
    # `RecordNode.props` would pass while `node_statement` dropped the field.
    stmt = cp.node_statement(obj)
    assert stmt.params["props"]["status"] == "superseded"


def test_the_derivation_fabricates_nothing_where_the_record_is_silent():
    """The other direction, which a naive `'live'` default would break.

    `status` is NOT a `FOLD_ON_CREATE_DEFAULTS` constant. Filling `'live'` where
    the record carries no lifecycle signal would replace *"the record did not
    say"* with a positive claim — and for a row the record holds a NON-default
    status for by other means (an event-kind fold, `restatus`) that claim would
    be false. So an Object the record carries NO supersession signal for carries
    NO `status`: the absent value keeps `_terminal_excluded`'s documented legacy
    rule (*"nodes without a stored status are LIVE"*) instead of minting a value
    the record cannot support.

    ⚠️ The silence being read is the PAIR (`supersededBy` OR `supersededAt`),
    not `supersededBy` alone — a blank `supersededBy` is NOT silence when
    `supersededAt` is present, which is exactly the id-only legacy shape
    (:func:`test_an_id_only_supersession_is_derived_not_left_live`) that a
    `supersededBy`-only reading left resurrected.
    """
    obj = _node("Object", {"id": "o-2", "name": "gamma"})
    assert "status" not in obj.props, (
        "the projection invented a lifecycle status the record does not carry "
        "— #9974's residue (restatus / event-kind folds) is a DECISION, not a "
        "constant to fabricate"
    )
    # …and neither is an EMPTY carrier; that edge is measured and pinned
    # separately, in `test_an_empty_carrier_is_not_a_supersession`.


def test_an_empty_carrier_is_not_a_supersession():
    """An EMPTY carrier is not a supersession — measured against a live engine.

    `public.object_current_status` reads the carriers with `IS NOT NULL`, so a
    row whose `superseded_by` is `''` answers `'superseded'` there. The GRAPH
    does not agree, and the graph is what this cache is a cache OF. Measured on
    a live engine at this head:

        update_entity(obj, supersededBy="")   ->  status='live',  supersededBy=''
        update_entity(obj, supersededBy="v2") ->  status='live',  supersededBy='v2'

    So truthiness — not `IS NOT NULL` — is what agrees with the live node, and
    the record VIEW is the side that over-reports on this edge (a defect in the
    view, named in `DERIVED_FROM_RECORD`'s bound). Deriving on an empty carrier
    would resurrect a node as TERMINAL that the graph holds LIVE.
    """
    for label in ("Object", "Subject"):
        node = _node(label, {"id": "e-1", "name": "epsilon",
                             "superseded_by": "", "superseded_at": ""})
        assert "status" not in node.props, (
            f"an EMPTY carrier derived a lifecycle status on :{label} — the "
            "record view's IS NOT NULL is not the graph's reading (measured: "
            "an empty carrier coexists with a LIVE node)"
        )


def test_the_record_can_clear_the_carriers_it_can_set():
    """The record DOES express un-supersede, by clearing the carriers.

    This pins the bound's `NOT MONOTONE` bullet, which an earlier revision got
    wrong by generalising the status-only `restatus` no-op into "the record
    cannot express un-superseded". Measured: an explicit
    `update_entity(status='live', supersededBy=None, supersededAt=None)`
    journals `EntityMutated op=supersede` with null state, and the mutation
    writes BOTH columns — so a later rebuild derives nothing, matching the
    graph.
    """
    from tortoise.record_projection import mutations_for

    stmts = mutations_for(
        {"type": "EntityMutated", "label": "Object", "op": "supersede",
         "state": {"supersededBy": None, "supersededAt": None}, "id": "o-1"},
        org_id="o", graph_id="g",
    )
    sql = " ".join(s.sql for s in stmts)
    assert "superseded_by" in sql and "superseded_at" in sql, (
        f"the un-supersede write no longer clears both carriers ({sql!r}) — "
        "if it stopped, the record genuinely could not express un-superseded "
        "and the bound's NOT MONOTONE bullet must be re-derived"
    )
    assert any(p is None for s in stmts for p in s.params), (
        "the un-supersede write no longer binds NULL to a carrier — re-derive "
        "the NOT MONOTONE bullet rather than trusting it"
    )


def test_a_status_only_restatus_derives_nothing_the_record_cannot_carry():
    """`restatus`: #9974's residue, and `superseded` is IN it.

    The bound names `superseded` FIRST among the values this table cannot
    derive, because it is the on-point status and the one a reader would assume
    is covered. Measured: `update_entity(subject, status='superseded')`
    leaves the node `status='superseded'` with BOTH carriers absent, and the
    record mutation is the `never_store` no-op — so a rebuild derives nothing
    and the entity comes back LIVE. That remains #9974's open window; this test
    exists so the bound cannot silently claim otherwise.
    """
    from tortoise.record_projection import mutations_for

    stmts = mutations_for(
        {"type": "EntityMutated", "label": "Subject", "op": "restatus",
         "state": {"status": "superseded"}, "id": "s-1"},
        org_id="o", graph_id="g",
    )
    sql = " ".join(s.sql for s in stmts)
    assert "superseded_by" not in sql and "superseded_at" not in sql, (
        f"a status-only restatus now carries a supersession column ({sql!r}) "
        "— if it started to, `superseded` would leave the NOT-DERIVED residue "
        "and the bound must be re-derived"
    )
    # …and the row it produces really derives nothing: no carrier, no status.
    assert "status" not in _node("Subject", {"id": "s-1", "name": "role",
                                               "subject_kind": "team"}).props


def test_an_id_only_supersession_is_derived_not_left_live():
    """#9974's own shape, on the carrier the record writes for it.

    The record does NOT always carry `supersededBy`. An id-only (legacy §6b)
    `ObjectSuperseded` — still replayed, and pinned by
    `test_status_projection.py::test_rebuild_all_legacy_6b_id_only_shape_supersedes`
    (#2164/#2193) — folds `status='superseded'` with `supersededBy=''`, and the
    mutation writes `superseded_at` ALONE:

        UPDATE public.object SET superseded_at = %s …          (no supersedes_by)

    So a derivation reading `supersededBy` only derives NOTHING here, and the
    entity is materialized without a `status` — which `_terminal_excluded` reads
    as LIVE. That is #9974 itself, unfixed on a shape the record still emits.
    The sources are therefore BOTH PRIMARY carriers, as alternatives.
    """
    for label in ("Object", "Subject"):
        node = _node(label, {"id": "x-1", "name": "acme",
                             "superseded_at": "2026-01-01T00:00:00Z"})
        assert node.props.get("status") == "superseded", (
            f"an id-only supersession left the rebuilt :{label} with no status "
            "— the record carries `superseded_at`, so this is #9974 again"
        )
    # …and the value reaches the STATEMENT, not only the model.
    stmt = cp.node_statement(_node("Object", {
        "id": "o-4", "name": "acme", "superseded_at": "2026-01-01T00:00:00Z"}))
    assert stmt.params["props"]["status"] == "superseded"


def test_the_derivation_is_pinned_to_the_folds_own_lifecycle_writes():
    """A `status` derivation is only sound while the fold writes the CARRIERS.

    `DERIVED_FROM_RECORD` infers `status='superseded'` from a supersession
    carrier. That inference holds only because the folds write `status`
    together with those carriers — so this pin reads the fold's clauses and the
    mutation builders, and reds if any half moves. Source-parsed for the same
    reason as the `FOLD_ON_CREATE_DEFAULTS` pin: a live graph cannot guard the
    DB-free lane.
    """
    fold = _fold_source()
    # Object: the blind (replay) clause writes status AND supersededBy together.
    assert re.search(r"SET o\.status='superseded', o\.supersededBy=\$sb", fold), (
        "`_fold_object_superseded` no longer writes status and supersededBy in "
        "one clause — DERIVED_FROM_RECORD's inference is no longer sound"
    )
    # The CAS'd live arm keeps the pair together too (both arms are
    # `CASE WHEN live`, so a node is never given one without the other).
    assert re.search(r"SET o\.status = CASE WHEN live THEN 'superseded'", fold), (
        "`_fold_object_superseded`'s CAS arm changed shape — re-derive the "
        "status/supersededBy pairing, do not guess"
    )
    # The fold sets status UNCONDITIONALLY on `supersedes_by` (it is $sb, which
    # may be ''), which is why a blank successor is still a supersession and the
    # id-only shape needs the `supersededAt` carrier rather than a blank check.
    assert re.search(r"supersededBy=\$sb", fold), (
        "the fold no longer binds supersededBy to the raw $sb — a blank-"
        "successor supersession may have become conditional; re-derive"
    )
    # Subject: `commit_ops` builds ONE state map carrying both keys, and the
    # generic `SET s += $s` fold applies it atomically.
    ops = (REPO_ROOT / "tortoise" / "commit_ops.py").read_text()
    assert re.search(
        r'state = \{"status": "superseded", "supersededBy": supersedes_by,', ops
    ), (
        "`commit_ops._apply_subject_supersession` no longer journals status and "
        "supersededBy in ONE state map — the Subject derivation lost its carrier"
    )
    # The MUTATION path is the record's second writer, and it is the one that
    # produces `superseded_at` with NO `superseded_by` (an id-only event). Pin
    # that shape, because it is the carrier the derivation nearly missed.
    from tortoise.record_projection import mutations_for
    id_only = mutations_for(
        {"type": "ObjectSuperseded", "ts": "2026-01-01T00:00:00Z", "id": "o-1"},
        org_id="o", graph_id="g",
    )
    sql = " ".join(s.sql for s in id_only)
    assert "superseded_at" in sql and "superseded_by" not in sql, (
        "an id-only ObjectSuperseded no longer writes `superseded_at` alone — "
        "if it started writing `superseded_by` the two carriers converged and "
        "this pin (and the id-only test) must be re-derived"
    )
    # The scope of the table is asserted, so adding a member without this
    # review is a red rather than a silent widening of what the cache invents.
    assert {label: tuple(member[0] for member in members)
            for label, members in cp.DERIVED_FROM_RECORD.items()} == {
        "Object": ("status",), "Subject": ("status",)
    }, (
        "DERIVED_FROM_RECORD gained or lost a derived property — every member "
        "must be a §2.6 DERIVED name with a PRIMARY carrier, and each needs its "
        "own pin"
    )
    # …and every member must read the record's whole OR-predicate, both PRIMARY
    # carriers (§2.6: `supersededBy` and `supersededAt`). A one-carrier member
    # resurrects the shape that carrier does not cover.
    assert {label: tuple(member[1] for member in members)
            for label, members in cp.DERIVED_FROM_RECORD.items()} == {
        "Object": (("supersededBy", "supersededAt"),),
        "Subject": (("supersededBy", "supersededAt"),),
    }, (
        "a DERIVED_FROM_RECORD member stopped covering BOTH PRIMARY "
        "supersession carriers — the record emits each alone (id-only legacy "
        "shape carries only superseded_at), so a one-carrier reading leaves "
        "#9974 open on the other"
    )


# ── 3d. the EVENT-STREAM half — the connector / pack lifecycle (#10198) ─────


def _lifecycle(name, event_kind, seq, **kw):
    """One record-sourced `EventRecorded`, reduced to the fold's inputs."""
    return cp.LifecycleEvent(seq=seq, event_kind=event_kind,
                             object_name=name, **kw)


def test_the_record_folds_a_connector_lifecycle_onto_the_objects_status():
    """#10198 — the connector work-item state IS reconstructible, from the stream.

    MEASURED before this change, on a live engine: a `github.issue.closed`
    Object held `status='completed'`, and the node `rebuild_from_record`
    materialised carried NO `status` — which `live._terminal_excluded` reads as
    LIVE (its documented legacy-node rule), so the work item came back CURRENT
    after a rebuild. `reconciliation` then reported a PERMANENT
    `field_mismatches` on `status` for every tenant with connector work items: a
    declared partial signal that is always on is worthless (#10017 R2's shape).

    No `public.object` row carries the vocabulary — it is declared by packs at
    RUNTIME (`status_folds`) — which is why this reads the record's own EVENT
    STREAM and why it lives in `cache_projection.EVENT_STREAM_FOLDS` rather than
    in `DERIVED_FROM_RECORD` (a COLUMN table).
    """
    obj = _node("Object", {"id": "o-1", "name": "issue-1",
                           "object_kind": "issue"})
    folded = cp.apply_event_stream_folds(
        [obj], [_lifecycle("issue-1", "github.issue.closed", 1)])
    assert folded[0].props["status"] == "completed", (
        "a connector-tracked Object still rebuilds with no lifecycle status — "
        "#10198 unfixed")
    # …and the value reaches the STATEMENT, not only the model: an assertion
    # over `RecordNode.props` would pass while `node_statement` dropped it.
    assert cp.node_statement(folded[0]).params["props"]["status"] == "completed"
    # An Object the stream does not NAME is untouched — the fold is keyed on the
    # name the live fold MATCHes, never on "every Object in the view".
    other = _node("Object", {"id": "o-2", "name": "issue-2",
                             "object_kind": "issue"})
    assert "status" not in cp.apply_event_stream_folds(
        [other], [_lifecycle("issue-1", "github.issue.closed", 1)])[0].props


def test_read_record_view_folds_the_event_stream_onto_the_object():
    """The fold is applied where the RECORD is read, so every door inherits it.

    Not only in the materializer: `reconciliation` and `record-restore` read the
    view, and a view whose `status` disagreed with the cache it is compared to
    would keep the permanent `field_mismatches` this issue exists to remove.
    """
    conn = _FakeConn({
        "object": [{"id": "o-1", "name": "issue-1", "object_kind": "issue",
                    "props": {}}],
        "journal_event": [
            {"seq": 1, "event_kind": "github.issue.open",
             "object_name": "issue-1", "about_entities": None,
             "about_entities_camel": None},
            {"seq": 2, "event_kind": "github.issue.closed",
             "object_name": "issue-1", "about_entities": None,
             "about_entities_camel": None},
        ],
    })
    view = cp.read_record_view(conn, org_id="o", graph_id="g")
    obj = next(n for n in view.nodes if n.label == "Object")
    assert obj.props["status"] == "completed"
    # The events ride the VIEW, so a caller that re-projects this view folds by
    # the same stream instead of by nothing (that is what `rebuild_from_record`
    # does — the application is idempotent).
    assert [e.seq for e in view.lifecycle_events] == [1, 2]
    assert cp.apply_event_stream_folds(
        view.nodes, view.lifecycle_events) == view.nodes



def _nested(seq, event, **envelope):
    """One record row as `ConversationMiner._emit_event` persists it (#5475).

    `record_projection._journal_row` stores the record minus `ENVELOPE_KEYS`,
    and `version` / `event` / `createdAt` are NOT envelope keys — so the durable
    payload nests the event and `payload ->> 'eventKind'` is NULL for it.
    """
    payload = {"version": "1.0", "type": "EventRecorded", "event": event,
               "createdAt": "2026-10-28T12:00:00Z"}
    payload.update(envelope)
    return {"seq": seq, "payload": payload}


def test_a_nested_envelope_event_still_folds_the_lifecycle():
    """One of the record's three writing doors nests the event (review P2).

    `mining.ConversationMiner._emit_event` builds
    `{"version", "type", "event": {…}, "createdAt"}` and writes it to the
    record; the LIVE fold unwraps it (`inner = event.get("event", event)`),
    so a record-side reader that read only the TOP level folded NOTHING for
    every mined event, silently — the guide "a rebuild folds by exactly the
    declaration a live write folds by" false for one of the production doors.

    The pre-fix reader returns no event at all here, so both the view fold and
    the single-row door (`read_node`) answer without a status.
    """
    from tortoise import record_serve as rs

    conn = _FakeConn({
        "object": [{"id": "o-1", "name": "issue-1", "object_kind": "issue",
                    "props": {}}],
        "journal_event": [
            _nested(1, {"eventKind": "github.issue.open",
                        "object": "issue-1"}),
            _nested(2, {"eventKind": "github.issue.closed",
                        "object": "issue-1"}),
        ],
    })
    view = cp.read_record_view(conn, org_id="o", graph_id="g")
    obj = next(n for n in view.nodes if n.label == "Object")
    assert obj.props.get("status") == "completed", (
        "a nested-envelope EventRecorded was dropped by the record-side reader "
        "— the live fold folds it")
    # …and the SINGLE-ROW door: its narrowing predicate is the second half of
    # the same bug (a nested payload matched neither `object` nor
    # `about_entities`, so the event never came back).
    assert [e.seq for e in cp.read_lifecycle_events(
        conn, org_id="o", graph_id="g", target="issue-1")] == [1, 2]
    assert rs.read_node(conn, org_id="o", graph_id="g", label="Object",
                        key="o-1")["status"] == "completed"
    # …and an unrelated target still narrows to nothing, so the unwrap did not
    # defeat the single-row filter.
    assert cp.read_lifecycle_events(
        conn, org_id="o", graph_id="g", target="issue-9") == ()


def test_the_fold_survives_a_one_shot_label_iterable():
    """`labels` is declared `Iterable`, so a GENERATOR must fold too (#10258).

    The single-Object gate on the event-stream fold used to read `set(labels)`
    AFTER the node loop had already drained the iterable. For the canonical
    generator caller — `(label for label in …)` — that set was EMPTY, the gate
    was `False`, and `lifecycle_events` came back `()`, so every
    connector-tracked Object lost its `status` and `live._terminal_excluded`
    read the ABSENT value as LIVE: exactly the #10198 defect this change
    exists to remove, with no raise, no log and no signal.

    Both production call sites pass a tuple, so this was latent rather than
    live — but the parameter is declared `Iterable` and the failure is silent,
    which is how it stays latent until it is not.
    """
    rows = {
        "object": [{"id": "o-1", "name": "issue-1", "object_kind": "issue",
                    "props": {}}],
        "journal_event": [
            {"seq": 1, "event_kind": "github.issue.open",
             "object_name": "issue-1", "about_entities": None,
             "about_entities_camel": None},
            {"seq": 2, "event_kind": "github.issue.closed",
             "object_name": "issue-1", "about_entities": None,
             "about_entities_camel": None},
        ],
    }
    tuple_view = cp.read_record_view(_FakeConn(rows), org_id="o", graph_id="g")
    gen_view = cp.read_record_view(
        _FakeConn(rows), org_id="o", graph_id="g",
        labels=(label for label in cp.NODE_TABLES))
    # The gate: the generator caller reads the SAME stream, not an empty one.
    assert [e.seq for e in gen_view.lifecycle_events] == [1, 2], (
        "the label iterable was consumed before the `Object` gate read it — "
        "a generator caller folds NO events, silently")
    # …and therefore the same status, on the same nodes.
    assert tuple(gen_view.nodes) == tuple(tuple_view.nodes)
    obj = next(n for n in gen_view.nodes if n.label == "Object")
    assert obj.props["status"] == "completed"


def test_both_about_entities_spellings_are_preferred_not_unioned():
    """The live fold PREFERS one spelling; the reader must not union them.

    `entities.py` reads `inner.get("about_entities") or
    inner.get("aboutEntities") or []`, so a payload carrying BOTH with different
    contents folds only the snake_case list there. The pre-fix reader
    concatenated the two, folding Objects the live graph never touched — the
    same silent-divergence class as the nested envelope, in the same reader.
    """
    from tortoise.status_folds import StatusFoldIndex

    index = StatusFoldIndex()
    index.by_event = {"disbursement": [("venture:tranche", "released")]}
    index.statuses_by_kind = {"venture:tranche": ("pending", "released")}
    index.lifecycle_kinds = frozenset(index.statuses_by_kind)
    index.kind_forms = frozenset({"venture:tranche", "tranche"})

    conn = _FakeConn({
        "journal_event": [{"seq": 1, "payload": {
            "eventKind": "disbursement",
            "about_entities": ["snake"],
            "aboutEntities": ["camel"],
        }}],
    })
    events = cp.read_lifecycle_events(conn, org_id="o", graph_id="g")
    assert [e.about_names for e in events] == [("snake",)], (
        "the reader unioned the two `about_entities` spellings — the live fold "
        "prefers the snake_case one and never folds the camelCase list")
    snake = _node("Object", {"id": "t-1", "name": "snake",
                             "object_kind": "tranche"})
    camel = _node("Object", {"id": "t-2", "name": "camel",
                             "object_kind": "tranche"})
    folded = {n.props["name"]: n.props.get("status")
              for n in cp.apply_event_stream_folds([snake, camel], events,
                                                   index=index)}
    assert folded == {"snake": "released", "camel": None}, (
        f"the rebuild folded an Object the live fold never touched: {folded}")


def test_the_event_stream_fold_is_last_writer_by_journal_seq():
    """`seq` is the order, and the INPUT order must not matter.

    The live fold applies events in journal order; a rebuild that took the
    database's row order (or the caller's list order) would fold `open` after
    `closed` on a different day and call a closed issue `in_progress`.
    """
    obj = _node("Object", {"id": "o-1", "name": "issue-1",
                           "object_kind": "issue"})
    opened = _lifecycle("issue-1", "github.issue.open", 1)
    closed = _lifecycle("issue-1", "github.issue.closed", 2)
    for order in ((opened, closed), (closed, opened)):
        assert cp.apply_event_stream_folds([obj], order)[0].props["status"] \
            == "completed"
    reopened = _lifecycle("issue-1", "github.issue.reopened", 3)
    assert cp.apply_event_stream_folds([obj], [closed, reopened])[0].props[
        "status"] == "in_progress", (
        "a reopen after a close must fold back to in_progress (#1725) — the "
        "record's seq decides it, not the list order")


def test_a_lifecycle_declaring_kind_owns_its_state_not_the_connectors():
    """#2729's ownership contract, from the record side.

    A kind that declares `statuses` OWNS its state: the built-in connector fold
    must not silently re-label it (`github.issue.closed` -> `completed` is
    outside a tranche's declared vocabulary). The index is the PROJECTION's own
    (`_status_fold_index`), so a rebuild folds by the same declaration a live
    write folds by — and the builtin must still apply to a kind that declares no
    lifecycle.
    """
    from tortoise.status_folds import StatusFoldIndex

    index = StatusFoldIndex()
    index.by_event = {"disbursement": [("venture:tranche", "released")]}
    index.statuses_by_kind = {
        "venture:tranche": ("pending", "conditions-met", "released", "denied")}
    index.lifecycle_kinds = frozenset(index.statuses_by_kind)
    index.kind_forms = frozenset({"venture:tranche", "tranche"})

    tranche = _node("Object", {"id": "t-1", "name": "tranche-a",
                               "object_kind": "tranche"})
    folded = cp.apply_event_stream_folds(
        [tranche], [_lifecycle("tranche-a", "disbursement", 1)], index=index)
    assert folded[0].props["status"] == "released"
    assert "status" not in cp.apply_event_stream_folds(
        [tranche], [_lifecycle("tranche-a", "github.issue.closed", 1)],
        index=index)[0].props, (
        "the connector compatibility fold re-labelled a lifecycle-owning kind — "
        "one word, two incompatible lifecycles (#2725)")

    issue = _node("Object", {"id": "o-1", "name": "issue-1",
                             "object_kind": "issue"})
    assert cp.apply_event_stream_folds(
        [issue], [_lifecycle("issue-1", "github.issue.closed", 1)],
        index=index)[0].props["status"] == "completed", (
        "the built-in compatibility fold stopped applying to a kind that "
        "declares NO lifecycle — a pack-less graph would stop folding")


def test_an_about_entities_target_is_folded_but_not_the_builtin_half():
    """The live fold's asymmetry, reproduced exactly.

    A pack-declared fold is applied to any `about_entities` entry that is an
    Object of the declaring kind (the extraction pipeline emits events with
    `about_entities` and no `object`); the built-in connector fold reads the
    event's DECLARED `object` only (`if builtin_status is not None and name`).
    A rebuild that applied the connector fold to `about_entities` would write
    `completed` onto Objects the live graph never touched.
    """
    from tortoise.status_folds import StatusFoldIndex

    index = StatusFoldIndex()
    index.by_event = {"disbursement": [("venture:tranche", "released")]}
    index.statuses_by_kind = {"venture:tranche": ("pending", "released")}
    index.lifecycle_kinds = frozenset(index.statuses_by_kind)
    index.kind_forms = frozenset({"venture:tranche", "tranche"})

    tranche = _node("Object", {"id": "t-1", "name": "tranche-a",
                               "object_kind": "tranche"})
    assert cp.apply_event_stream_folds(
        [tranche],
        [cp.LifecycleEvent(seq=1, event_kind="disbursement",
                           about_names=("tranche-a",))],
        index=index)[0].props["status"] == "released", (
        "a declared fold stopped reaching an `about_entities` target")

    issue = _node("Object", {"id": "o-1", "name": "issue-1",
                             "object_kind": "issue"})
    assert "status" not in cp.apply_event_stream_folds(
        [issue],
        [cp.LifecycleEvent(seq=1, event_kind="github.issue.closed",
                           about_names=("issue-1",))],
        index=index)[0].props, (
        "the built-in connector fold was applied to an `about_entities` target "
        "the live fold never touches")


def test_a_superseded_object_is_never_resurrected_by_a_connector_event():
    """The terminal guard, which is the whole point of a lifecycle cache.

    Running it the other way would make a rebuild resurrect an Object the graph
    holds terminal — a dual-tracked work item superseded conversationally, then
    closed on GitHub, comes back as `completed` and visible in
    `recall_state`'s default view (the #2164/#3309 defect, re-opened on the
    record path).
    """
    obj = _node("Object", {"id": "o-1", "name": "issue-1",
                           "object_kind": "issue", "superseded_by": "v2"})
    assert obj.props["status"] == "superseded"
    folded = cp.apply_event_stream_folds(
        [obj], [_lifecycle("issue-1", "github.issue.closed", 1)])
    assert folded[0].props["status"] == "superseded"


def test_the_event_stream_fold_can_never_write_a_terminal_status():
    """The bound, stated as an invariant instead of a promise.

    The record carries no signal for a TERMINAL `restatus`
    (`deprecated`/`archived`/`retracted` — see `DERIVED_FROM_RECORD`'s
    NOT-DERIVED bullet), so a rebuild cannot honour one the GRAPH set, and the
    fold may label an Object the graph kept terminal. That is survivable because
    an ABSENT status already reads LIVE to `_terminal_excluded`, so the
    pre-#10198 rebuild showed such an Object too.

    ⚠️ This test alone is NOT the guard, and reading it as one was the review's
    finding: it asserts over `BUILTIN_CONNECTOR_FOLDS` (the half with no
    terminal target, untouched by this change), so it passed unchanged on the
    pre-fix code. What actually protects a terminal Object is the PER-EVENT
    replay guard, asserted in
    `test_a_terminal_pack_fold_latches_instead_of_being_resurrected` — the half
    that CAN break it — and here the built-in half is kept only as the cheap
    catalogue check it always was.
    """
    from tortoise.commit_ops import OBJECT_TERMINAL_STATUSES
    from tortoise.status_folds import BUILTIN_CONNECTOR_FOLDS

    assert not (set(BUILTIN_CONNECTOR_FOLDS.values())
                & OBJECT_TERMINAL_STATUSES), (
        "a built-in connector fold now targets a TERMINAL Object status — the "
        "record has no carrier for that decision, and the built-in half folds "
        "on the event's declared `object`, so it would hide an Object on a "
        "rebuild")


def _terminal_index(terminal_word: str):
    """A pack-shaped index whose fold targets a TERMINAL word (`archived`).

    `status_folds.validate_status_decl` admits this (it only requires the target
    to be one of the kind's declared `statuses`), so the fold must handle it —
    and the LIVE fold does, as a state machine: after `archive` writes
    `archived`, the guard `AND (o.status IS NULL OR NOT (o.status IN $excluded))`
    refuses the later `resume`.
    """
    from tortoise.status_folds import StatusFoldIndex

    index = StatusFoldIndex()
    index.by_event = {
        "archive": [("venture:tranche", terminal_word)],
        "resume": [("venture:tranche", "active")],
    }
    index.statuses_by_kind = {
        "venture:tranche": ("active", terminal_word)}
    index.lifecycle_kinds = frozenset(index.statuses_by_kind)
    index.kind_forms = frozenset({"venture:tranche", "tranche"})
    return index


def test_a_terminal_pack_fold_latches_instead_of_being_resurrected():
    """The live fold is a STATE MACHINE, not a last-writer pick (review P2).

    Live: `archive` writes `archived`, then the guarded `SET` refuses `resume`,
    so the Object stays terminal and out of `recall_state`'s default view. The
    pre-fix record-side fold took the LAST writer by `seq` and wrote `active` —
    a terminal Object RESURRECTED on rebuild, the #2164/#3309 class the live
    test `test_a_superseded_object_is_not_resurrected_by_a_connector_event`
    exists to prevent (that one only covers the `superseded` carrier).

    Both temporal orders are asserted, because the reverse one shows the other
    half of the divergence: the pre-fix fold could also HIDE an Object the graph
    presents as active (writing `archived` over a later `resume` is refused
    live, applied on rebuild).
    """
    from tortoise.commit_ops import OBJECT_TERMINAL_STATUSES

    tranche = _node("Object", {"id": "t-1", "name": "tranche-a",
                               "object_kind": "tranche"})
    for word in sorted(OBJECT_TERMINAL_STATUSES):
        index = _terminal_index(word)
        archive = _lifecycle("tranche-a", "archive", 1)
        resume = _lifecycle("tranche-a", "resume", 2)
        assert cp.apply_event_stream_folds(
            [tranche], [archive, resume], index=index)[0].props["status"] \
            == word, (
            f"a later event resurrected a terminal Object — live refuses the "
            f"write (`o.status IN $excluded`), so `{word}` must latch")
        # The reverse order: `resume` then `archive` ends terminal on BOTH paths.
        assert cp.apply_event_stream_folds(
            [tranche], [_lifecycle("tranche-a", "resume", 1),
                        _lifecycle("tranche-a", "archive", 2)],
            index=index)[0].props["status"] == word
    # …and a non-terminal fold still folds: the guard is on TERMINAL statuses,
    # not on "a fold already ran" (live overwrites `active` with `active`).
    index = _terminal_index("archived")
    assert cp.apply_event_stream_folds(
        [tranche], [_lifecycle("tranche-a", "resume", 1)],
        index=index)[0].props["status"] == "active"

    # …and a terminal status the ROW carries (a supersession) still stops the
    # whole replay, including a pack fold that would otherwise resurrect it.
    superseded = _node("Object", {"id": "t-2", "name": "tranche-b",
                                   "object_kind": "tranche",
                                   "superseded_by": "v2"})
    assert cp.apply_event_stream_folds(
        [superseded], [_lifecycle("tranche-b", "resume", 1)],
        index=index)[0].props["status"] == "superseded"


def test_the_fold_vocabulary_is_status_folds_one_home_and_not_a_copy():
    """`status_folds.py` is the ONE home (#2729); this module must READ it.

    Proven by MUTATION of the one home rather than by a text search: a second
    hand-written map here would keep answering `completed` after the real
    vocabulary moved, which is exactly how a value gets silently omitted from a
    reader (#2901).
    """
    from tortoise import status_folds

    obj = _node("Object", {"id": "o-1", "name": "issue-1",
                           "object_kind": "issue"})
    events = [_lifecycle("issue-1", "github.issue.closed", 1)]
    assert cp.apply_event_stream_folds([obj], events)[0].props["status"] \
        == "completed"
    with pytest.MonkeyPatch.context() as mp:
        mp.setitem(status_folds.BUILTIN_CONNECTOR_FOLDS,
                   "github.issue.closed", "shipped")
        assert cp.apply_event_stream_folds([obj], events)[0].props["status"] \
            == "shipped", (
            "the record-side fold did not read `status_folds` at call time — it "
            "holds a copy of the vocabulary, which is the second home #10198 "
            "forbids")
    # …and its declared scope is pinned, so a widening is a red, not a drift.
    assert cp.EVENT_STREAM_FOLDS == {"Object": ("status",)}, (
        "EVENT_STREAM_FOLDS gained or lost a derived property — every member "
        "needs its own evidence that the record's event stream decides it")


def test_the_record_read_door_answers_the_same_status_the_rebuild_writes():
    """`record_serve.read_node`'s "the same map a rebuild materializes" contract.

    Its own docstring promises the map a `rebuild_from_record` materializes; a
    read door that stopped at the Object ROW would answer a connector-tracked
    Object without the status the rebuild writes, i.e. two answers to one
    question — the split #9974 exists to remove.
    """
    from tortoise import record_serve as rs

    conn = _FakeConn({
        "object": [{"id": "o-1", "name": "issue-1", "object_kind": "issue",
                    "props": {}}],
        "journal_event": [
            {"seq": 1, "event_kind": "github.issue.closed",
             "object_name": "issue-1", "about_entities": None,
             "about_entities_camel": None},
        ],
    })
    props = rs.read_node(conn, org_id="o", graph_id="g", label="Object",
                         key="o-1")
    assert props is not None and props["status"] == "completed"
    # …and it agrees with the rebuilt node, field for field — one map, not two.
    events = cp.read_lifecycle_events(conn, org_id="o", graph_id="g",
                                      target="issue-1")
    assert events, "the read door read no event at all"
    rebuilt = cp.apply_event_stream_folds(
        [cp.node_from_row("Object", {"id": "o-1", "name": "issue-1",
                                     "object_kind": "issue", "props": {}})],
        events,
    )[0]
    assert dict(props) == dict(rebuilt.props)
