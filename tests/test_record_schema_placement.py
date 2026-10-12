"""The Postgres record's schema ↔ placement-table pin (#7968, epic #7879).

WHAT THIS PINS. `docs/architecture/STORAGE-ARCHITECTURE.md` §17.5 says the
placement enumeration "has since LANDED" and points at the field-level placement
table (`docs/epics/2026-10-10-7879-postgres-record/02-identity-and-placement.md`,
W2/#7955). That table is the **S1/S3 field contract**, not documentation of the
schema (#7967 §8), so the two must not be able to drift apart. This test reads
the table, derives the expected column set per layer, reads the migrations on
disk, and fails on either direction of disagreement:

  * every **PRIMARY** property has a column (the record cannot be poorer than
    the history — Interface A's failure mode);
  * every **DERIVED / VOLATILE** property has **no** column (capstone S5 — a
    derived value must not be stored as authority).

WHY IT DERIVES RATHER THAN RESTATES. A hand-copied expectation is a second
declaration of the placement, and the second declaration is the one that goes
stale. Editing either side alone turns the test red, which is the point — and
the CORE_ALSO entries in `tools/ci_selection.py` make CI SELECT this pin on a
migration-only or placement-doc-only change, so the claim holds in CI and not
only in a manual run (#8179 review P2).

THE MUTATION THAT MAKES IT FAIL. `test_mutation_adding_a_derived_column_fails`
performs it in-memory: it adds `confidence` (a DERIVED EP scalar) to the
`point` column set and asserts the checker refuses it. The same mutation on
disk — `confidence double precision,` under `public.point` — fails
`test_derived_and_volatile_properties_have_no_column`. Deleting
`point.content` (PRIMARY) fails `test_primary_properties_have_a_column`.

READING THE TABLE. Two of the appendix's classes are pre-resolution, and §2.7
resolves eight rows — those resolutions are applied here (`RESOLUTIONS`), and
the resolved totals are asserted. `baseline_source` is the one CONDITIONAL row
(§2.8): PRIMARY when `set-by-author`, DERIVED otherwise — it is expected as a
column, and the migration encodes the narrow half as a CHECK.

NO DATABASE IS NEEDED. The pgTAP half (RLS, PK/FK, the CHECKs firing) is
`supabase/tests/20261012000001_record_schema.sql`, run by the PGlite harness.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PLACEMENT_DOC = (
    REPO_ROOT
    / "docs"
    / "epics"
    / "2026-10-10-7879-postgres-record"
    / "02-identity-and-placement.md"
)
MIGRATIONS = [
    REPO_ROOT / "supabase" / "migrations" / name
    for name in (
        "20261012000001_record_event_stream.sql",
        "20261012000002_record_semantic.sql",
        "20261012000003_record_epistemic.sql",
        "20261012000004_record_procedural.sql",
    )
]

# ── the placement table's section → the physical table in the record ─────────
SECTION_TABLE = {
    "Event": "event",
    "Source": "source",
    "Session": "session",
    "Object": "object",
    "Subject": "subject",
    "Point": "point",
    "Edges": "relation",
}

# §2.7's eight resolutions of the measured UNRESOLVED rows: (section, property) -> P|D
RESOLUTIONS = {
    ("Point", "updatedAt"): "P",  # P* — journal the write-time
    ("Point", "version"): "D",
    ("Point", "inherited_at"): "D",
    ("Point", "baseline_source"): "P",  # §2.8 — PRIMARY when 'set-by-author'
    ("Point", "baseline_set"): "D",
    ("Point", "temporal_reviewed"): "P",  # P* — journal it
    ("Edges", "IMPL.confidence"): "P",
    ("Edges", "IMPL.weight"): "P",
}
CONDITIONAL = {("Point", "baseline_source")}  # PRIMARY only when author-set

# The resolved totals (02-identity-and-placement.md §2.7). Asserting them is what
# catches a change to the TABLE as well as to the SQL.
EXPECTED_TOTALS = {"P": 187, "D": 29, "U": 0}
EXPECTED_TOTAL_NAMES = 216

_SECTION_RE = re.compile(r"^## (Event|Source|Session|Object|Subject|Point) \(node\)")
_EDGES_RE = re.compile(r"^## Edges / operators")
_HEADING_RE = re.compile(r"^#{2,3} ")
_ROW_RE = re.compile(r"^\|\s*`([^`]+)`\s*\|\s*([PDU])\s*\|")


def normalize(prop: str) -> str:
    """camelCase → snake_case, PRESERVING leading underscores.

    Preserving them matters: `_nid` / `_graph_id` are DERIVED (replay
    bookkeeping) and must have no column, while `graph_id` is the record's
    tenancy column. Collapsing the leading underscore would make a DERIVED
    property look satisfied by an operational column.
    """
    return re.sub(r"(?<!^)(?=[A-Z])", "_", prop).lower()


def parse_placement(doc: Path = PLACEMENT_DOC) -> list[tuple[str, str, str]]:
    """(section, property, class) with §2.7 applied. Predicate rows (`x.*`) are
    dropped here — they name no property — and returned by parse_predicates."""
    rows: list[tuple[str, str, str]] = []
    section = None
    for line in doc.read_text(encoding="utf-8").splitlines():
        m = _SECTION_RE.match(line)
        if m:
            section = m.group(1)
            continue
        if _EDGES_RE.match(line):
            section = "Edges"
            continue
        if _HEADING_RE.match(line):
            section = None
        if section is None:
            continue
        r = _ROW_RE.match(line)
        if not r:
            continue
        prop = r.group(1)
        if prop.endswith(".*"):
            continue  # a predicate row (parse_predicates), not a property
        rows.append((section, prop, RESOLUTIONS.get((section, prop), r.group(2))))
    return rows


_GROUPED_ROW_RE = re.compile(r"^\|\s*`([^`]+\.\*)`\s*\|\s*([PDU])\s*\|")


def parse_grouped_rows(doc: Path = PLACEMENT_DOC) -> list[tuple[str, str]]:
    """The edge table's grouped `a/b/TAGGED.*` rows, as (group, class).

    They name PREDICATES, not properties, but the table's own counts (216 names,
    13 PRIMARY of the 19 edge rows) count each grouped row as ONE unit — so the
    totals test must count them too.
    """
    return [
        (m.group(1), m.group(2))
        for line in doc.read_text(encoding="utf-8").splitlines()
        if (m := _GROUPED_ROW_RE.match(line))
    ]


def parse_predicates(doc: Path = PLACEMENT_DOC) -> list[str]:
    """Predicates named by the grouped rows."""
    preds: list[str] = []
    for group, _ in parse_grouped_rows(doc):
        preds.extend(group[:-2].split("/"))
    return preds


def expand_edge_property(prop: str) -> list[str]:
    """`IMPL.idx` -> ['idx']; `NAND.direction/confidence/...` -> the five names."""
    if "." not in prop:
        return [prop]
    _, _, rest = prop.partition(".")
    return [part.strip() for part in rest.split("/")]


_COL_RE = re.compile(
    r'^\s{4}("?[A-Za-z_][A-Za-z0-9_]*"?)\s+'
    r"(text|boolean|integer|bigint|double precision|jsonb|timestamptz|numeric)\b"
)
_NON_COLUMN_HEADS = {"CONSTRAINT", "FOREIGN", "PRIMARY", "CHECK", "UNIQUE", "LIKE"}


def parse_sql_columns() -> dict[str, set[str]]:
    """Every table's property columns, read from the migrations on disk."""
    tables: dict[str, set[str]] = {}
    text = "\n".join(p.read_text(encoding="utf-8") for p in MIGRATIONS)
    for block in re.finditer(
        r"CREATE TABLE IF NOT EXISTS public\.(\w+)\s*\((.*?)\n\);", text, re.S
    ):
        name, body = block.group(1), block.group(2)
        cols: set[str] = set()
        for line in body.splitlines():
            m = _COL_RE.match(line)
            if not m or m.group(1).upper() in _NON_COLUMN_HEADS:
                continue
            cols.add(m.group(1).strip('"'))
        tables[name] = cols
    return tables


def check_placement(rows, columns_by_table) -> list[str]:
    """The checker, as a pure function, so the mutation test can drive it."""
    problems: list[str] = []
    for section, prop, cls in rows:
        table = SECTION_TABLE[section]
        cols = columns_by_table.get(table)
        if cols is None:
            problems.append(f"no CREATE TABLE for {table!r} (section {section})")
            continue
        # Expand an Edges property's leaf name for BOTH classes: `IMPL.msg_beta`
        # is DERIVED, and the whole dotted name normalises to `i_m_p_l.msg_beta`,
        # which can never equal a column — so a `cls == "P"`-only expansion left
        # the DERIVED branch unable to fire for the four BP message props (#8179
        # review P2). The leaf name is what the relation table could carry.
        names = expand_edge_property(prop) if section == "Edges" else [prop]
        for name in names:
            col = normalize(name)
            if cls == "P":
                if col not in cols:
                    note = " (CONDITIONAL, §2.8)" if (section, prop) in CONDITIONAL else ""
                    problems.append(
                        f"MISSING COLUMN: {table}.{col} — {section}.{prop} is PRIMARY{note}"
                    )
            elif cls == "D" and col in cols:
                problems.append(
                    f"DERIVED/VOLATILE AS AUTHORITY: {table}.{col} — "
                    f"{section}.{prop} is {cls} (§17.5 carve-out)"
                )
    return problems


def test_placement_table_resolves_to_the_recorded_totals():
    """The table itself is stable — a change here is a placement change."""
    counts = {"P": 0, "D": 0, "U": 0}
    for _, _, cls in parse_placement():
        counts[cls] += 1
    for _, cls in parse_grouped_rows():
        counts[cls] += 1
    assert sum(counts.values()) == EXPECTED_TOTAL_NAMES, counts
    assert counts == EXPECTED_TOTALS, counts


def test_the_four_layers_have_tables():
    """The four layers each have a physical home; procedural deliberately has none."""
    tables = parse_sql_columns()
    # event stream (the journal's own shape + the Episodic entities)
    assert {"node_identity", "journal_event", "source", "event", "session"} <= set(tables)
    # semantic
    assert {"object", "subject"} <= set(tables)
    # epistemic (Point + the relations table, ONTOLOGY §8)
    assert {"point", "relation"} <= set(tables)
    # procedural — a view/read, NEVER a table (§2.6)
    procedural = "\n".join(p.read_text(encoding="utf-8") for p in MIGRATIONS)
    assert "CREATE OR REPLACE VIEW public.object_current_status" in procedural
    assert "CREATE OR REPLACE VIEW public.subject_current_status" in procedural
    assert "CREATE TABLE IF NOT EXISTS public.procedural" not in (procedural), (
        "the procedural layer must not become a table"
    )


def test_primary_properties_have_a_column():
    problems = check_placement(parse_placement(), parse_sql_columns())
    missing = [p for p in problems if p.startswith("MISSING COLUMN")]
    assert not missing, "\n".join(missing)


def test_derived_and_volatile_properties_have_no_column():
    problems = check_placement(parse_placement(), parse_sql_columns())
    derived = [p for p in problems if p.startswith("DERIVED")]
    assert not derived, "\n".join(derived)


def test_no_unresolved_rows():
    assert not [r for r in parse_placement() if r[2] == "U"]


def test_every_predicate_the_table_names_is_declared():
    """The edge table's grouped rows name predicates; the relations table must
    declare the vocabulary they belong to (the record must not silently lose one)."""
    text = "\n".join(p.read_text(encoding="utf-8") for p in MIGRATIONS)
    missing = [p for p in parse_predicates() if p not in text]
    assert not missing, f"predicates absent from the record: {missing}"
    assert len(parse_predicates()) >= 15


@pytest.mark.parametrize(
    "table,col",
    [
        ("point", "confidence"),  # EP scalar (§2.0 carve-out)
        ("point", "ep_alpha"),
        ("point", "baseline_set"),
        ("point", "content_hash"),
        ("point", "version"),
        ("object", "status"),  # the projection-owned status CACHE
        ("object", "embedding"),
        ("subject", "embedding"),
        ("source", "reliability"),
        ("relation", "msg_alpha"),
        ("relation", "msg_beta"),
        ("relation", "back_msg_alpha"),
        ("relation", "back_msg_beta"),
        ("event", "confidence"),
    ],
)
def test_the_carve_out_names_are_absent_from_the_sql(table, col):
    """Belt-and-braces: the carve-out names never appear as column definitions on
    their own table (the same name may legitimately be a column elsewhere —
    `confidence` is PRIMARY on `relation`, DERIVED on `point`/`event`)."""
    columns = parse_sql_columns()
    assert col not in columns.get(table, set()), f"{table}.{col} must not be a record column"


def test_props_checks_refuse_the_derived_names():
    """The open-set passthrough tail must not become a back door for a cache."""
    text = "\n".join(p.read_text(encoding="utf-8") for p in MIGRATIONS)
    assert "chk_point_props_no_derived" in text
    assert "'confidence'" in text and "'ep_alpha'" in text and "'baseline_set'" in text
    assert "chk_source_props_no_derived" in text
    assert "chk_object_props_no_derived" in text
    assert "chk_relation_props_no_derived" in text


def test_relation_is_never_a_bare_id():
    """Interface E: a foreign key is (label, key). The relation table must use it."""
    text = "\n".join(p.read_text(encoding="utf-8") for p in MIGRATIONS)
    assert "src_label" in text and "src_key" in text
    assert "FOREIGN KEY (org_id, graph_id, src_label, src_key)" in text
    assert "FOREIGN KEY (org_id, graph_id, tgt_label, tgt_key)" in text
    # the key property is generated from the label, so a store cannot disagree
    assert "key_property" in text and "GENERATED ALWAYS AS" in text
    # and each layer pins node_key to that label's key column
    for table, keycol in (
        ("source", "url"),
        ("event", "event_id"),
        ("session", "id"),
        ("object", "name"),
        ("subject", "name"),
        ("point", "id"),
    ):
        assert re.search(
            rf"chk_{table}_node_key_is_contract_key CHECK \({keycol} IS NOT NULL AND node_key = {keycol}\)", text
        ), f"{table} does not pin node_key to {keycol} (with the NULL guard)"


def test_every_table_is_tenant_scoped_and_rls_enabled():
    """S7: team_id/org_id on every row, RLS as the boundary."""
    for p in MIGRATIONS:
        text = p.read_text(encoding="utf-8")
        for t in re.findall(r"CREATE TABLE IF NOT EXISTS public\.(\w+)", text):
            assert f"ALTER TABLE public.{t} ENABLE ROW LEVEL SECURITY;" in text, t
            assert f"CREATE POLICY {t}_read_org ON public.{t}" in text, t
            assert f"CREATE POLICY {t}_service_role_all ON public.{t}" in text, t


def test_mutation_adding_a_derived_column_fails():
    """THE MUTATION. Add `confidence` (DERIVED) to the point column set in memory:
    the checker must refuse it. The on-disk equivalent fails
    test_derived_and_volatile_properties_have_no_column."""
    rows = parse_placement()
    columns = {t: set(c) for t, c in parse_sql_columns().items()}
    columns["point"].add("confidence")
    problems = check_placement(rows, columns)
    assert any(p.startswith("DERIVED") and "point.confidence" in p for p in problems), problems


def test_mutation_removing_a_primary_column_fails():
    """THE OTHER DIRECTION. Drop `point.content` (PRIMARY): the checker must
    refuse. The on-disk equivalent fails test_primary_properties_have_a_column."""
    rows = parse_placement()
    columns = {t: set(c) for t, c in parse_sql_columns().items()}
    columns["point"].discard("content")
    problems = check_placement(rows, columns)
    assert any(p.startswith("MISSING COLUMN") and "point.content" in p for p in problems), problems


def test_mutation_adding_a_derived_edge_property_fails():
    """The dotted DERIVED edge props must be checked by their LEAF name.

    `IMPL.back_msg_beta` normalises as a whole to `i_m_p_l.back_msg_beta`, which
    can never equal a relation column — so before the `Edges` expansion covered
    DERIVED as well as PRIMARY (#8179 review P2), adding `back_msg_beta` to
    `public.relation` left `check_placement` empty and the derived edge value
    could be stored as authority undetected."""
    rows = parse_placement()
    columns = {t: set(c) for t, c in parse_sql_columns().items()}
    columns["relation"].add("back_msg_beta")
    problems = check_placement(rows, columns)
    assert any(
        p.startswith("DERIVED") and "relation.back_msg_beta" in p for p in problems
    ), problems
