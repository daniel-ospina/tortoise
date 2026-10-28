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

from tortoise import record_projection

REPO_ROOT = Path(__file__).resolve().parent.parent
PLACEMENT_DOC = (
    REPO_ROOT
    / "docs"
    / "epics"
    / "2026-10-10-7879-postgres-record"
    / "02-identity-and-placement.md"
)
MIGRATIONS = sorted(
    REPO_ROOT / "supabase" / "migrations" / name
    for name in (
        "20261012000001_record_event_stream.sql",
        "20261012000002_record_semantic.sql",
        "20261012000003_record_epistemic.sql",
        "20261012000004_record_procedural.sql",
        # A FORWARD migration adding COLUMNS (`ALTER TABLE public.point ADD
        # COLUMN`, #2782's money value). Read here for the same reason
        # `record_projection.RECORD_MIGRATIONS` reads it: append-only history
        # means a landed `CREATE TABLE` can only ever gain a column this way.
        "20261018000001_record_point_money_value_2782.sql",
        # #9881 / PR #9948 review P2: the FORWARD migration that RE-DECLARES the
        # Object/Subject key contract (the `node_identity.key_property` CASE and
        # both `chk_*_node_key_is_contract_key` pins). Leaving it out was the
        # stale-pin defect the review measured: this file's pin then read only
        # the PRE-move declaration in `20261012000002` and kept certifying a
        # contract the record no longer holds. Sorted below so `last == newest`
        # — append-only history means the LAST declaration is the one in force,
        # the rule `record_projection.key_properties()` applies.
        "20261027000001_record_semantic_id_key.sql",
    )
)

#: `chk_<table>_node_key_is_contract_key CHECK (…)` bodies in DECLARATION ORDER.
#: `CHECK` must follow the constraint name, so a `DROP CONSTRAINT IF EXISTS
#: chk_…` and the prose that names the constraint in a comment both miss — only
#: a real declaration is captured.
_CONTRACT_KEY_PIN_RE = re.compile(
    r"chk_(\w+)_node_key_is_contract_key\s+CHECK\s*\((.*?)\)", re.S
)


def contract_key_pins(text: str) -> dict[str, list[str]]:
    """Every `node_key` pin's CHECK body, per table, in declaration order."""
    pins: dict[str, list[str]] = {}
    for m in _CONTRACT_KEY_PIN_RE.finditer(text):
        pins.setdefault(m.group(1), []).append(m.group(2))
    return pins


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
EXPECTED_TOTALS = {"P": 192, "D": 29, "U": 0}
#: 216 from the `#7955` pass + the 5 `#2782` money rows (all PRIMARY).
EXPECTED_TOTAL_NAMES = 221

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

#: A FORWARD migration ADDS a column: `ALTER TABLE public.<t> ADD COLUMN [IF NOT
#: EXISTS] <col> <type>, …`. Migrations are append-only history (the
#: `check-migration-append-only` gate refuses an edit to a landed file), so the
#: `2026101200000*` tables can only ever GAIN a column this way — #2782's five
#: money columns did. A read that understands only `CREATE TABLE` reports every
#: such PRIMARY property as MISSING COLUMN, which is exactly what
#: `test_primary_properties_have_a_column` refused on the first cut of #2782.
_ALTER_ADD_COLUMN_RE = re.compile(
    r"ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?public\.(\w+)\b([^;]*?);", re.S
)
_ADDED_COLUMN_RE = re.compile(
    r"ADD\s+COLUMN\s+(?:IF\s+NOT\s+EXISTS\s+)?\"?(\w+)\"?\s+"
    r"(text|boolean|integer|bigint|double precision|jsonb|timestamptz|numeric)\b",
    re.I,
)


def columns_in_create_body(body: str) -> set[str]:
    r"""The column names declared in ONE `CREATE TABLE` body, matched PER LINE.

    `_COL_RE` is anchored `^\s{4}` and compiled **without** `re.MULTILINE`, so
    running it over a whole multi-line body can only ever match at index 0 —
    where the body starts with `\n`, so the 4-whitespace run is followed by a
    space rather than a name. The result is the EMPTY set for every input, which
    turns any `assert col not in base_cols` built on it into an unfalsifiable
    assertion (exactly the #8976 review P3 defect). Splitting into lines here
    means no caller can re-introduce that.
    """
    cols: set[str] = set()
    for line in body.splitlines():
        m = _COL_RE.match(line)
        if not m or m.group(1).upper() in _NON_COLUMN_HEADS:
            continue
        cols.add(m.group(1).strip('"'))
    return cols


def parse_sql_columns() -> dict[str, set[str]]:
    """Every table's property columns, read from the migrations on disk.

    `CREATE TABLE` bodies first, then any forward `ALTER TABLE … ADD COLUMN`
    unioned over them (append-only history — see `_ALTER_ADD_COLUMN_RE`).
    """
    tables: dict[str, set[str]] = {}
    text = "\n".join(p.read_text(encoding="utf-8") for p in MIGRATIONS)
    for block in re.finditer(
        r"CREATE TABLE IF NOT EXISTS public\.(\w+)\s*\((.*?)\n\);", text, re.S
    ):
        name, body = block.group(1), block.group(2)
        tables[name] = columns_in_create_body(body)
    for block in _ALTER_ADD_COLUMN_RE.finditer(text):
        name, body = block.group(1), block.group(2)
        if name not in tables:
            continue  # an ALTER on a table this file does not pin (non-record)
        tables[name] |= {m.group(1).strip('"') for m in _ADDED_COLUMN_RE.finditer(body)}
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


_STORAGE_DOC = REPO_ROOT / "docs" / "architecture" / "STORAGE-ARCHITECTURE.md"
#: The projection doc's own statement of what this pin asserts (`#7968`).
_PROJECTION_DOC = (
    REPO_ROOT / "docs" / "epics" / "2026-10-10-7879-postgres-record" / "04-projection.md"
)
#: The `#7950` capstone evidence README's denominators paragraph.
_CAPSTONE_README = REPO_ROOT / "docs" / "evidence" / "7950-capstone" / "README.md"

#: The appendix's own per-layer headings for the two EPISTEMIC groups — the
#: source `§2.5`'s summary must agree with.
_LAYER_HEADING_RE = re.compile(
    r"^## (?P<group>Point \(node\)|Edges / operators).*?— (?P<names>\d+) properties "
    r"\(PRIMARY (?P<p>\d+) · DERIVED/VOLATILE (?P<d>\d+) · UNRESOLVED (?P<u>\d+)\)",
    re.M,
)
#: `**221 names → 192 PRIMARY · 29 DERIVED/VOLATILE · 0 UNRESOLVED**`
_FINAL_COUNTS_RE = re.compile(
    r"\*\*(?P<names>\d+) names → (?P<p>\d+) PRIMARY · "
    r"(?P<d>\d+) DERIVED/VOLATILE · (?P<u>\d+) UNRESOLVED"
)
#: `resolved to 192 PRIMARY · 29 DERIVED/VOLATILE · 0 UNRESOLVED` (bold optional)
_RESOLVED_RE = re.compile(
    r"resolved to \*{0,2}(?P<p>\d+) PRIMARY · (?P<d>\d+) DERIVED/VOLATILE · "
    r"(?P<u>\d+) UNRESOLVED"
)
#: `221 properties → 192 PRIMARY / 29 DERIVED-VOLATILE / 0 U` — the same totals
#: in the slash dialect `04-projection.md` states them in.
_PROJECTION_TOTAL_RE = re.compile(
    r"(?P<names>\d+) properties → (?P<p>\d+) PRIMARY / "
    r"(?P<d>\d+) DERIVED-VOLATILE / (?P<u>\d+) U\b"
)
#: `**221 property names** across the seven labels\n(192 PRIMARY / 29
#: DERIVED-VOLATILE / 0 UNRESOLVED)` — the capstone README's denominators line.
#: The `#7955` attribution may sit inside the parenthetical (it did before the
#: `#2782` annotation), so that tail is allowed rather than required to be bare.
_CAPSTONE_TOTAL_RE = re.compile(
    r"(?P<names>\d+) property names\*\*.*?\((?P<p>\d+) PRIMARY / "
    r"(?P<d>\d+) DERIVED-VOLATILE / (?P<u>\d+) UNRESOLVED[^)]{0,40}\)",
    re.S,
)

#: The routing index's row for this epic (#7879) — the file a lane reads to find
#: out what EXISTS without re-deriving it, so its census is a claim like any
#: other and is pinned against the migrations on disk (#9638 review P3: the PR
#: that created the tenth record table left this row saying "9 record tables"
#: and "no narrative/raw-text table (0)").
_INDEX_DOC = REPO_ROOT / "docs" / "00_index.md"

#: The epic's OWN "what exists" ledger — §A1 of the doc the index row above
#: indexes, and the map every other lane routes through instead of re-deriving
#: the record schema (#6134). Same defect class, one artefact over: the PR that
#: created the tenth record table left this heading saying "EXISTS, 9 tables +
#: 3 views" with a nine-row table underneath it (#9638 review P3).
_PREREQUISITES_DOC = (
    REPO_ROOT
    / "docs"
    / "epics"
    / "2026-10-10-7879-postgres-record"
    / "08-hosted-prerequisites.md"
)
#: `### A1. The record schema — **EXISTS, 10 tables + 3 views**, and it is …`
_PREREQ_CENSUS_RE = re.compile(
    r"^### A1\. The record schema — \*\*EXISTS, (?P<tables>\d+) tables \+ "
    r"(?P<views>\d+) views\*\*",
    re.M,
)
_INDEX_RECORD_ROW_MARKER = "**Hosted prerequisites for the Postgres record (epic #7879)**"
#: `**10 record tables + 3 procedural views**`
_INDEX_CENSUS_RE = re.compile(
    r"\*\*(?P<tables>\d+) record tables \+ (?P<views>\d+) procedural views\*\*"
)

# ── the two RE-DERIVED NUMBERS that are not table/view counts (#9638 P2) ─────
# The census above counts tables and views. Two further figures are quoted in
# the same ledgers as *the number of migrations on disk* and *how many lines a
# grep matches* — and both went stale the moment a migration was added, because
# nothing read them. They are derived here from the same directory the doc's own
# command reads, so the sentence and the tree cannot disagree.
_MIGRATIONS_DIR = REPO_ROOT / "supabase" / "migrations"
#: §A1/R7's "at this head, N migrations" and the index row's "`pgvector` = 0
#: occurrences in N migrations" — the SAME denominator (`supabase/migrations`).
_PREREQ_MIGRATION_COUNT_RE = re.compile(r"at this head, (?P<count>\d+) migrations")
_INDEX_MIGRATION_COUNT_RE = re.compile(
    r"`pgvector` = 0 occurrences in (?P<count>\d+) migrations"
)
#: The SAME row states *which* migration is the newest — the half no pattern
#: above read, so the count could be right while the attribution was false
#: (`#3524` review, P2). `(?P<detail>[^)]*)` stops at the clause's own closing
#: parenthesis; the sentence has no nested parens.
_INDEX_MIGRATION_ATTRIBUTION_RE = re.compile(
    r"re-measured at this head; (?P<detail>[^)]*)"
)
#: A migration filename as the docs write it: the 14-digit stamp plus `_name.sql`.
_MIGRATION_FILENAME_RE = re.compile(r"\d{14}_[a-z0-9_]+\.sql")
#: §A5's *third* restatement of the same denominator — `grep -riE
#: 'pgvector|vector\(' over all N` / `migrations` — which wraps across a line
#: break, so the separator is `\s+` and not a literal space. Without this
#: pattern the figure had a reader in two of its three homes and could go stale
#: in the third with the census test still green (the exact failure the docstring
#: below names). It deliberately cannot reach §A1/R7's historical `over all` EOL
#: → `65 migrations`, because there is no space after `all` there.
_PREREQ_PGVECTOR_TOTAL_RE = re.compile(r"over all (?P<count>\d+)\s+migrations")
#: The `narrative|raw_turn|raw_text|transcript` census command's own terms —
#: the alternation the two "N lines" claims were taken with.
_NARRATIVE_CENSUS_TERMS = ("narrative", "raw_turn", "raw_text", "transcript")
#: §G's re-measurement: "returns **N lines, all in `<migration>`**".
_PREREQ_GREP_LINE_COUNT_RE = re.compile(r"returns \*\*(?P<count>\d+) lines, all in")
#: R7's: "the same command matches `<migration>` on N lines".
_PREREQ_R7_GREP_LINE_COUNT_RE = re.compile(r"on (?P<count>\d+) lines")
#: The record's own migrations: a 14-digit stamp and the `record_` noun. This
#: deliberately does NOT use a bare `*record*` glob — `0014_metering_records.sql`
#: is metering, not the record, and counting it would make the census wrong in
#: the other direction.
_RECORD_MIGRATION_RE = re.compile(r"^\d{14}_record_.*\.sql$")
_CREATE_TABLE_RE = re.compile(r"CREATE TABLE IF NOT EXISTS public\.([a-z_]+)")
_CREATE_VIEW_RE = re.compile(r"CREATE (?:OR REPLACE )?VIEW public\.([a-z_]+)")


def record_tables_and_views() -> tuple[dict[str, str], dict[str, str]]:
    """`({table: migration}, {view: migration})` read off the migrations on disk.

    DERIVED, not restated: a hand-copied list is a second declaration of the
    schema, and the second declaration is the one that goes stale — which is
    exactly how `docs/00_index.md` came to say "9 record tables" while ten
    existed.
    """
    tables: dict[str, str] = {}
    views: dict[str, str] = {}
    for path in sorted((REPO_ROOT / "supabase" / "migrations").glob("*.sql")):
        if not _RECORD_MIGRATION_RE.match(path.name):
            continue
        text = path.read_text(encoding="utf-8")
        for match in _CREATE_TABLE_RE.finditer(text):
            tables.setdefault(match.group(1), path.name)
        for match in _CREATE_VIEW_RE.finditer(text):
            views.setdefault(match.group(1), path.name)
    return tables, views


def test_no_stated_total_disagrees_with_the_pin():
    """Every prose restatement of the totals moves WITH the table (#8976 P2).

    The same defect landed three times on this PR: the appendix header was
    updated for the five `#2782` rows while **§2.7's "Final counts"**,
    **§2.5's per-layer summary** and **`STORAGE-ARCHITECTURE.md` §17.5's
    resolved totals** kept the pre-#2782 numbers — so the prose told a reader
    **187 P** while this pin (and the table it pins) said **192**. A review
    caught it each time; nothing mechanical did. The fix then missed two more
    restatements (`04-projection.md`'s own sentence about what this pin asserts,
    and the `#7950` capstone README's denominators) for exactly that reason. This
    reads all five back, so the next row set cannot be added to one place only.

    Derived, never restated: the totals come from `EXPECTED_TOTALS` /
    `EXPECTED_TOTAL_NAMES`, and the per-layer summary is checked against the
    appendix's OWN section headings (which the section headings themselves
    already have to sum to `EXPECTED_TOTAL_NAMES`).
    """
    doc = PLACEMENT_DOC.read_text(encoding="utf-8")

    # §2.7 — the resolved totals, stated as "N names → P PRIMARY · D D/V · U U"
    final = [
        line for line in doc.splitlines() if line.startswith("**Final counts:**")
    ]
    assert len(final) == 1, f"§2.7's 'Final counts' line is gone or duplicated: {final}"
    stated = _FINAL_COUNTS_RE.search(final[0])
    assert stated, f"§2.7's 'Final counts' no longer states the totals: {final[0]!r}"
    assert (
        int(stated.group("names")),
        int(stated.group("p")),
        int(stated.group("d")),
        int(stated.group("u")),
    ) == (EXPECTED_TOTAL_NAMES, EXPECTED_TOTALS["P"], EXPECTED_TOTALS["D"], EXPECTED_TOTALS["U"]), (
        "§2.7's 'Final counts' disagrees with the pin — 192 P / 221 names are "
        f"what the table and EXPECTED_TOTALS say, the sentence says {stated.group(0)!r}"
    )

    # §2.5 — the epistemic layer summary must equal the two appendix headings it
    # summarises (`Point` + edges/operators), so a row added to either moves it.
    headings = {m.group("group").split(" (")[0]: m for m in _LAYER_HEADING_RE.finditer(doc)}
    assert set(headings) == {"Point", "Edges / operators"}, sorted(headings)
    layer = {
        key: sum(int(headings[g].group(key)) for g in headings)
        for key in ("names", "p", "d", "u")
    }
    section = re.search(
        r"^#### 2\.5 Epistemic.*?\((?P<names>\d+) names · (?P<p>\d+) P · "
        r"(?P<d>\d+) D · (?P<u>\d+) U\)",
        doc,
        re.M,
    )
    assert section, "§2.5's per-layer heading no longer states its totals"
    assert all(int(section.group(k)) == layer[k] for k in layer), (
        "§2.5 disagrees with the appendix headings it summarises — the heading "
        f"says {section.group(0)!r}, the appendix sums to {layer}"
    )
    point_line = re.search(r"`Point` \((?P<names>\d+): (?P<p>\d+) P / (?P<d>\d+) D / (?P<u>\d+) U\)", doc)
    assert point_line, "§2.5's `Point` per-layer line is gone"
    assert (
        int(point_line.group("names")),
        int(point_line.group("p")),
        int(point_line.group("d")),
        int(point_line.group("u")),
    ) == tuple(int(headings["Point"].group(k)) for k in ("names", "p", "d", "u")), (
        f"§2.5's `Point` line disagrees with the appendix heading: {point_line.group(0)!r}"
    )

    # STORAGE-ARCHITECTURE §17.5 — the resolved totals, one paragraph above the
    # record-schema sentence this PR amends.
    storage = _STORAGE_DOC.read_text(encoding="utf-8")
    resolved = _RESOLVED_RE.search(storage)
    assert resolved, "STORAGE-ARCHITECTURE §17.5 no longer states the resolved totals"
    assert (
        int(resolved.group("p")),
        int(resolved.group("d")),
        int(resolved.group("u")),
    ) == (EXPECTED_TOTALS["P"], EXPECTED_TOTALS["D"], EXPECTED_TOTALS["U"]), (
        "STORAGE-ARCHITECTURE §17.5's resolved totals disagree with the pin: "
        f"{resolved.group(0)!r} vs {EXPECTED_TOTALS}"
    )

    # Two further restatements carried the pre-#2782 numbers and were missed by
    # the first cut of this pin (#8976 review P2): `04-projection.md`'s own
    # statement of what this pin asserts, and the `#7950` capstone evidence
    # README's denominators paragraph. Both are read here, in the dialect each
    # states them in, so neither can drift again.
    expected = (
        EXPECTED_TOTAL_NAMES,
        EXPECTED_TOTALS["P"],
        EXPECTED_TOTALS["D"],
        EXPECTED_TOTALS["U"],
    )
    for label, path, pattern in (
        ("04-projection.md's resolved-totals sentence", _PROJECTION_DOC, _PROJECTION_TOTAL_RE),
        ("7950-capstone README's denominators", _CAPSTONE_README, _CAPSTONE_TOTAL_RE),
    ):
        text = path.read_text(encoding="utf-8")
        statements = list(pattern.finditer(text))
        assert statements, f"{label} no longer states the resolved totals"
        for statement in statements:
            stated_totals = tuple(
                int(statement.group(k)) for k in ("names", "p", "d", "u")
            )
            assert stated_totals == expected, (
                f"{label} disagrees with the pin — {expected} is what the table "
                f"and EXPECTED_TOTALS say, the sentence says {statement.group(0)!r}"
            )


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


def test_the_routing_index_census_matches_the_record_migrations():
    """`docs/00_index.md`'s table census must be what the migrations HAVE (#9638 P3).

    The index's #7879 row is the routing surface a lane reads to learn what
    exists in the record *without re-deriving it*; it states a census ("N record
    tables + M procedural views") and, until this pin, a NEGATIVE ("no
    narrative/raw-text table (0)"). Both went false in the one direction the
    index exists to prevent: `20261023000001_record_session_narrative.sql`
    created the tenth table and exactly the narrative home the row said did not
    exist, so the file that indexes `08-hosted-prerequisites.md` contradicted the
    doc it indexes.

    RED on the pre-fix text — measured: 9 stated vs 10 derived (`{event,
    journal_event, node_identity, object, point, relation, session,
    session_narrative, source, subject}`), and the negative sentence still
    present. The fix is the row's own one-liner, not a change here.

    Non-vacuity of the derivation: the counterfactual is not a re-typed number.
    `record_tables_and_views()` reads the `CREATE TABLE`/`CREATE VIEW` statements
    off the record migrations on disk, so deleting the narrative migration would
    drop the derivation to 9 — and the row would then be WRONG in the other
    direction, which is why the assertion is equality and not `>=`.
    """
    tables, views = record_tables_and_views()
    rows = [
        line
        for line in _INDEX_DOC.read_text(encoding="utf-8").splitlines()
        if _INDEX_RECORD_ROW_MARKER in line
    ]
    assert len(rows) == 1, (
        f"docs/00_index.md has {len(rows)} rows for the record's hosted "
        "prerequisites; the census pin needs exactly one to read "
        f"(marker {_INDEX_RECORD_ROW_MARKER!r})"
    )
    row = rows[0]

    stated = _INDEX_CENSUS_RE.search(row)
    assert stated, (
        "docs/00_index.md's #7879 row no longer states its census in the pinned "
        "dialect (`**N record tables + M procedural views**`) — restate it rather "
        "than deleting the phrase: this pin reads it, and without it the row's "
        "claims about what exists become uncheckable"
    )
    assert int(stated.group("tables")) == len(tables), (
        f"docs/00_index.md says {stated.group('tables')} record tables, the "
        f"migrations define {len(tables)}: {sorted(tables)}. Update the row's "
        "count (and name a NEW table's migration beside it if it falls outside "
        "the `20261012*_record_*.sql` glob it cites)"
    )
    assert int(stated.group("views")) == len(views), (
        f"docs/00_index.md says {stated.group('views')} procedural views, the "
        f"migrations define {len(views)}: {sorted(views)}"
    )

    # The NEGATIVE, which is the half that actually misleads: a reader asking
    # "is there a home for raw text / the narrative?" was told no.
    negatives = [
        phrase
        for phrase in ("no narrative/raw-text table", "no narrative table")
        if phrase in row
    ]
    if "session_narrative" in tables:
        assert not negatives, (
            f"docs/00_index.md still says {negatives} while "
            f"{tables['session_narrative']} defines public.session_narrative — "
            "the index tells a lane the home does not exist, in the file that "
            "exists to stop it re-deriving what the map already holds"
        )


def test_the_prerequisites_a1_census_matches_the_record_migrations():
    """The epic's §A1 ledger must state what the migrations HAVE (#9638 P3).

    `08-hosted-prerequisites.md` §A1 is the record's "what EXISTS, without
    re-deriving it" ledger — §A8 exists precisely to tell a reader how far to
    trust A1–A7 — and `docs/00_index.md`'s #7879 row indexes it. The PR that
    added `20261023000001_record_session_narrative.sql` corrected the index row
    (pinned by the test above) but left this document, in the same section,
    asserting the pre-change world: a heading of "EXISTS, 9 tables + 3 views"
    over an exhaustive nine-row table with no `session_narrative` row, plus R7's
    `**there is no dedicated table for it**` and a `→ 0` census command a few
    lines above its own DECIDED block. Index and indexed disagreed, in the one
    direction §A1 exists to prevent.

    Both halves are read, because they can drift independently: the **heading**
    count (`**EXISTS, N tables + M views**`) and the **row set** — the table
    body's first cells, which name every table and view. Bumping the count while
    leaving a row out is the exact shape this document had.

    RED on the pre-fix text — measured: heading says 9 vs 10 derived, and
    `session_narrative` is absent from the row set. The fix is the document's own
    heading and table, not a change here.

    Non-vacuity of the derivation: both expected values come from
    `record_tables_and_views()`, which reads `CREATE TABLE`/`CREATE VIEW` off the
    record migrations on disk — so dropping the narrative migration makes the
    document WRONG in the other direction, which is why the row-set assertion is
    equality and not containment.
    """
    text = _PREREQUISITES_DOC.read_text(encoding="utf-8")
    heading = _PREREQ_CENSUS_RE.search(text)
    assert heading, (
        "§A1 of 08-hosted-prerequisites.md no longer states its census in the "
        "pinned dialect (`### A1. The record schema — **EXISTS, N tables + M "
        "views**`) — restate it rather than deleting the phrase: this pin reads "
        "it, and without it the section's `EXISTS` claim becomes uncheckable"
    )

    tables, views = record_tables_and_views()
    assert int(heading.group("tables")) == len(tables), (
        f"§A1 says {heading.group('tables')} record tables, the migrations "
        f"define {len(tables)}: {sorted(tables)}. Update the heading (and add "
        "the missing row to the table under it — the row set is pinned too)"
    )
    assert int(heading.group("views")) == len(views), (
        f"§A1 says {heading.group('views')} procedural views, the migrations "
        f"define {len(views)}: {sorted(views)}"
    )

    # The table body. `### ` and `## ` end the section, so the row set read here
    # is §A1's and nothing else's; each row's FIRST cell names its table(s)/
    # view(s) in backticks (the second cell is a `file:line` path, whose dotted
    # `20261012000001_record_event_stream.sql:52` shape the `[a-z_]+` class
    # cannot match, so only the name cells contribute).
    section = text.split("### A1. The record schema", 1)[1]
    for end in ("\n### ", "\n## "):
        section = section.split(end, 1)[0]
    named: set[str] = set()
    for line in section.splitlines():
        if line.startswith("|"):
            named.update(re.findall(r"`([a-z_]+)`", line.split("|")[1]))

    missing = (set(tables) | set(views)) - named
    extra = named - (set(tables) | set(views))
    assert not missing, (
        "§A1's table does not name "
        f"{sorted(missing)} — the migrations define them and a reader routing "
        "through this ledger would conclude they do not exist"
    )
    assert not extra, (
        "§A1's table names "
        f"{sorted(extra)}, which no record migration defines — the ledger has "
        "gone stale in the other direction (a dropped table, or a typo)"
    )


def test_the_migration_and_grep_census_reproduces_at_this_head():
    """The two RE-DERIVED numbers in the ledgers must equal what the tree holds.

    #9638's own thesis is that a census stated in `docs/00_index.md` /
    `08-hosted-prerequisites.md` must be what the migrations ARE — the two pins
    above enforce exactly that for the table and view counts. Two further figures
    sit in the same sentences, were re-taken by this PR, and were wrong by one:

    * `08-hosted-prerequisites.md` R7: *"at this head, 68 migrations"* — measured
      69, i.e. the number forgot to count the migration this PR adds. This is the
      failure mode the pins above were written for, one artefact over.
    * `docs/00_index.md`'s #7879 row: *"`pgvector` = 0 occurrences in 68
      migrations"* — the same denominator, stale by the same migration.
    * both files: the `narrative|raw_turn|raw_text|transcript` grep *"on 37
      lines"* / *"returns **37 lines**"* — measured 36, because the \u26a0\ufe0f CARRIER
      GAP block was rewritten after the number was taken.
    * `08-hosted-prerequisites.md` §A5: *"over all 70"* / *"migrations"* — the
      SAME denominator again, wrapped across a line break, and the one site of
      the three that no pattern read: it was corrected to 71 only by hand, so
      the next migration would have left it stale with this test green.

    Neither number had a reader, which is why both could be wrong at the head of
    the PR that re-derived them. This test IS that reader: the denominator comes
    from `supabase/migrations/*.sql` (the directory the doc's own command reads)
    and the line count from scanning those files with the command's own terms.

    RED on the pre-fix text — measured: 68 stated vs 69 on disk in both files, and
    37 stated vs 36 scanned in two places.

    Non-vacuity: the expectations are not re-typed numbers. Adding or removing a
    migration changes the first, and any migration that mentions one of the four
    terms changes the second — the document then goes WRONG in the other
    direction, which is why both assertions are equality.
    """
    migrations = sorted(_MIGRATIONS_DIR.glob("*.sql"))
    assert migrations, f"no migrations under {_MIGRATIONS_DIR}"
    on_disk = len(migrations)

    prerequisites = _PREREQUISITES_DOC.read_text(encoding="utf-8")
    index_row = next(
        line
        for line in _INDEX_DOC.read_text(encoding="utf-8").splitlines()
        if _INDEX_RECORD_ROW_MARKER in line
    )

    for label, pattern, text in (
        ("08-hosted-prerequisites.md R7's `at this head, N migrations`",
         _PREREQ_MIGRATION_COUNT_RE, prerequisites),
        ("docs/00_index.md's #7879 row `... in N migrations`",
         _INDEX_MIGRATION_COUNT_RE, index_row),
        ("08-hosted-prerequisites.md §A5's `over all N migrations`",
         _PREREQ_PGVECTOR_TOTAL_RE, prerequisites),
    ):
        stated = [int(m.group("count")) for m in pattern.finditer(text)]
        assert stated, (
            f"{label} no longer states its migration count — restate it rather "
            "than deleting the phrase: this pin reads it, and without it the "
            "re-derivation stops reproducing"
        )
        assert set(stated) == {on_disk}, (
            f"{label} says {sorted(set(stated))} migrations, "
            f"{_MIGRATIONS_DIR.relative_to(REPO_ROOT)} holds {on_disk} "
            f"({migrations[-1].name} is the newest). Update the stated number "
            "— a stale denominator is what makes the census unreproducible"
        )

    # The same command the docs quote: `grep -riE '<terms>' supabase/migrations`
    # — `-i` case-folded, one row per matching LINE.
    matched_lines = 0
    for path in migrations:
        for line in path.read_text(encoding="utf-8").splitlines():
            folded = line.lower()
            if any(term in folded for term in _NARRATIVE_CENSUS_TERMS):
                matched_lines += 1
    assert matched_lines, (
        "the narrative/raw-text census grep matches nothing — the `N lines` "
        "claim in §A1/R7 and §G describes a match that no longer exists"
    )

    for label, pattern, text in (
        ("08-hosted-prerequisites.md §G's `returns **N lines**`",
         _PREREQ_GREP_LINE_COUNT_RE, prerequisites),
        ("08-hosted-prerequisites.md R7's `on N lines`",
         _PREREQ_R7_GREP_LINE_COUNT_RE, prerequisites),
    ):
        stated = [int(m.group("count")) for m in pattern.finditer(text)]
        assert stated, (
            f"{label} no longer states its line count — restate it rather than "
            "deleting the phrase: this pin reads it"
        )
        assert set(stated) == {matched_lines}, (
            f"{label} says {sorted(set(stated))} lines, the "
            "`narrative|raw_turn|raw_text|transcript` grep over "
            f"supabase/migrations matches {matched_lines}. Re-run the command "
            "and state its own number"
        )


def test_the_index_row_names_this_heads_newest_migration():
    """The count and its ATTRIBUTION must both be true at this head.

    The pin above reads the *number* and nothing else, so the sentence could say
    the right count while naming the WRONG newest migration — and it did. At
    `97703631c` this lane's row read:

        "…in 77 migrations (re-measured at this head; the newest is #10017's
         cache-tier migration, `20261029000001_record_point_cache_tier.sql`, so
         the count moved ahead of it rather than with it)"

    77 was right, and the attribution was false: the 77th is this lane's own
    `20261030000001_oauth_consent_tickets.sql`, so the count moved WITH that
    migration, not ahead of it (`#3524` review, P2). A lane that trusts the
    sentence learns the wrong newest migration — from the docs index, which is
    read first. The count was pinned; the name was not, which is why it could
    rot while the test stayed green.

    RED on that pre-fix text: the newest file on disk is not named in the
    clause. Non-vacuity: the expectation is not a re-typed filename — adding a
    migration makes `migrations[-1]` a different file, so the sentence has to
    be re-derived at the same head, in the same direction as the count it sits
    beside.
    """
    migrations = sorted(_MIGRATIONS_DIR.glob("*.sql"))
    assert migrations, f"no migrations under {_MIGRATIONS_DIR}"
    newest = migrations[-1].name

    index_row = next(
        line
        for line in _INDEX_DOC.read_text(encoding="utf-8").splitlines()
        if _INDEX_RECORD_ROW_MARKER in line
    )

    clause = _INDEX_MIGRATION_ATTRIBUTION_RE.search(index_row)
    assert clause, (
        "docs/00_index.md's #7879 row no longer states WHICH migration is the "
        "newest (`re-measured at this head; …`). Restate it rather than "
        "deleting the phrase: this pin reads it, and an attribution no reader "
        "can reach is a stale flag waiting to happen"
    )

    attributed = [m.group(0) for m in _MIGRATION_FILENAME_RE.finditer(clause.group("detail"))]
    assert attributed, (
        "the index row's re-measurement clause names no migration file — name "
        f"the newest ({newest}) so a lane reading the index is told the truth"
    )
    assert newest in attributed, (
        f"docs/00_index.md's #7879 row names {attributed} as the newest "
        f"migration(s), but `{_MIGRATIONS_DIR.relative_to(REPO_ROOT)}` newest is "
        f"{newest}. A count that is right while the name is wrong is still the "
        "wrong answer — re-state the attribution at this head"
    )
    unknown = [name for name in attributed if not (_MIGRATIONS_DIR / name).is_file()]
    assert not unknown, (
        f"the index row names migration(s) that do not exist on disk: {unknown}"
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
    # And each layer pins node_key to that label's key column. ⚠️ The
    # expectation is DERIVED from `record_projection.NODE_TABLES` — never a
    # hand-copied tuple. The tuple this replaces said `("object", "name")` /
    # `("subject", "name")` and stayed GREEN across #9881, because it restated
    # the retired contract (#9948 review P2). And the DECLARATIONS are read from
    # the whole `supabase/migrations` directory, not from this file's
    # `MIGRATIONS` list: that list is hand-maintained, so a future FORWARD
    # migration that re-declares a pin would re-create exactly the same defect
    # (the list is pinned by nothing — `RECORD_MIGRATIONS`' completeness has its
    # own guard in `test_9231`, this one does not). Measured: over all 73 files
    # the pin set is identical to the 6-file list's (`source 1 · event 1 ·
    # session 1 · object 2 · subject 2 · point 1`), so reading the directory is
    # free and closes the recurrence instead of patching this round of it.
    # The LAST declaration wins (append-only history; the same rule
    # `key_properties()` reads), and the files are read in filename order — which
    # IS chronological, because the name is the migration timestamp.
    all_text = "\n".join(
        p.read_text(encoding="utf-8")
        for p in sorted((REPO_ROOT / "supabase" / "migrations").glob("*.sql"))
    )
    assert "src_label" in all_text and "src_key" in all_text
    assert "FOREIGN KEY (org_id, graph_id, src_label, src_key)" in all_text
    assert "FOREIGN KEY (org_id, graph_id, tgt_label, tgt_key)" in all_text
    # the key property is generated from the label, so a store cannot disagree
    assert "key_property" in all_text and "GENERATED ALWAYS AS" in all_text
    pins = contract_key_pins(all_text)
    assert set(pins) == set(record_projection.NODE_TABLES), (
        "the tables carrying a node_key pin and the projection's NODE_TABLES "
        f"disagree — pinned {sorted(pins)}, NODE_TABLES "
        f"{sorted(record_projection.NODE_TABLES)}"
    )
    for table, (_label, keycol) in record_projection.NODE_TABLES.items():
        bodies = pins[table]
        body = bodies[-1]
        assert re.search(rf"\bnode_key\s*=\s*{keycol}\b", body), (
            f"the LAST chk_{table}_node_key_is_contract_key does not pin "
            f"node_key to {keycol!r} — read {body.strip()!r} of {len(bodies)} "
            "declaration(s). Under append-only history the last one is the one "
            "in force, so a stale pin here certifies a contract the record "
            "does not hold"
        )
        assert re.search(rf"\b{keycol}\s+IS\s+NOT\s+NULL\b", body), (
            f"chk_{table}_node_key_is_contract_key lost the NULL guard on "
            f"{keycol!r}: a bare `node_key = {keycol}` is NULL (not FALSE) for "
            "a NULL key and is silently satisfied (#8179 review P1) — read "
            f"{body.strip()!r}"
        )


MONEY_COLUMNS = ("amount_minor", "currency", "minor_unit_exponent", "verbatim", "as_of")
MONEY_MIGRATION = "20261018000001_record_point_money_value_2782.sql"


def test_a_forward_migration_adds_the_money_columns_and_the_read_sees_them():
    """#2782's five money columns are PRIMARY, and they arrive by an ALTER.

    `20261012000003_record_epistemic.sql` is append-only history (the
    `check-migration-append-only` gate refuses an edit to a landed file), so the
    money value reaches `public.point` as a FORWARD `ALTER TABLE … ADD COLUMN`.
    The schema read must therefore understand that form: with the ALTER parse
    removed, `parse_sql_columns()` reports none of the five and
    `test_primary_properties_have_a_column` fails for all of them — which is how
    this landed: the first cut of the PR added the five placement rows and left
    the record without the columns.
    """
    forward = REPO_ROOT / "supabase" / "migrations" / MONEY_MIGRATION
    assert forward.exists(), f"the forward migration is gone: {forward}"
    assert forward in MIGRATIONS, (
        "the forward migration must be READ, not merely on disk — an unread "
        "ALTER is invisible to this pin"
    )
    # the columns are NOT in the base CREATE TABLE, so only the ALTER can supply them
    base = (REPO_ROOT / "supabase" / "migrations" / "20261012000003_record_epistemic.sql").read_text(
        encoding="utf-8"
    )
    body = re.search(r"CREATE TABLE IF NOT EXISTS public\.point\s*\((.*?)\n\);", base, re.S)
    assert body, "public.point is no longer created by 20261012000003"
    base_cols = columns_in_create_body(body.group(1))
    assert base_cols, (
        "the base CREATE TABLE read produced NO columns — an empty `base_cols` "
        "makes `col not in base_cols` unfalsifiable, so the anti-cheat half of "
        "this pin would be dead (#8976 review P3)"
    )
    columns = parse_sql_columns()["point"]
    for col in MONEY_COLUMNS:
        assert col not in base_cols, (
            f"point.{col} is declared in the base CREATE TABLE — if the money "
            "columns moved into 20261012000003 the ALTER parse proves nothing"
        )
        assert col in columns, (
            f"point.{col} is missing from the schema read — the ALTER TABLE … ADD "
            "COLUMN form is not parsed, so every money row reads MISSING COLUMN"
        )


def test_the_base_table_guard_can_see_a_column_moved_into_the_base_table():
    r"""The anti-cheat clause above is falsifiable (#8976 review P3).

    `test_a_forward_migration_adds_the_money_columns_and_the_read_sees_them`
    asserts `col not in base_cols`, which is only meaningful if `base_cols` can
    ever CONTAIN one of the five columns. The pre-fix read ran `_COL_RE`
    (anchored `^\s{4}`, no `re.MULTILINE`) over the WHOLE multi-line body, where
    `^` matches at index 0 only — so it always returned the empty set, and the
    exact mutation its docstring names (moving the money columns into
    `20261012000003`'s `CREATE TABLE`) still passed.

    This performs that mutation in memory, per column, and requires the read to
    see it — so the guard really can fire.
    """
    base = (
        REPO_ROOT / "supabase" / "migrations" / "20261012000003_record_epistemic.sql"
    ).read_text(encoding="utf-8")
    body = re.search(r"CREATE TABLE IF NOT EXISTS public\.point\s*\((.*?)\n\);", base, re.S)
    assert body, "public.point is no longer created by 20261012000003"
    point_body = body.group(1)
    assert columns_in_create_body(point_body), "the base read sees no columns at all"
    for col in MONEY_COLUMNS:
        doctored = f"{point_body}\n    {col} bigint,"
        assert col in columns_in_create_body(doctored), (
            f"`{col}` injected into the base CREATE TABLE is invisible to the "
            "guard, so `assert col not in base_cols` cannot fail and the ALTER "
            "parse is not really proven"
        )


def test_the_money_columns_are_the_integer_shape_not_floats():
    """The column TYPE carries the money rule (`tortoise/money.py`, WS-C §1/D1).

    A float cannot represent 0.10, so an `amount_minor`/`minor_unit_exponent`
    declared `double precision`/`numeric` would be a silently lossy home for a
    value the shape promises is exact — the record must not be weaker than the
    graph.
    """
    sql = (REPO_ROOT / "supabase" / "migrations" / MONEY_MIGRATION).read_text(encoding="utf-8")
    types = {
        m.group(1): m.group(2).lower()
        for m in re.finditer(
            r"ADD\s+COLUMN\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)\s+(\w+)", sql, re.I
        )
    }
    assert types, "no ADD COLUMN parsed from the forward money migration"
    assert types.get("amount_minor") == "bigint", types
    assert types.get("minor_unit_exponent") == "integer", types
    assert set(types) == set(MONEY_COLUMNS), types


def test_the_money_note_declares_the_column_types_the_migration_has():
    """The doc's column types ARE the migration's, not a restatement (#8976 P3-1).

    The note under the `Point` table lists the physical shape a reader will go
    looking for in `public.point`. It said `as_of date` while the migration — and
    `tortoise/money.py`, which types `asOf` as `str` — say `text`: the doc
    described a column that does not exist. Both sides are read here, so the note
    cannot drift from the SQL again.
    """
    doc = PLACEMENT_DOC.read_text(encoding="utf-8")
    note = [
        line for line in doc.splitlines() if line.startswith("> **Where the money value lives")
    ]
    assert len(note) == 1, f"the money-storage note is gone or duplicated: {len(note)}"
    declared = dict(re.findall(r"`(\w+) (bigint|text|integer|date|numeric)`", note[0]))
    sql = (REPO_ROOT / "supabase" / "migrations" / MONEY_MIGRATION).read_text(encoding="utf-8")
    actual = {
        m.group(1): m.group(2).lower()
        for m in re.finditer(
            r"ADD\s+COLUMN\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)\s+(\w+)", sql, re.I
        )
    }
    assert set(declared) == set(MONEY_COLUMNS), (
        f"the note declares {sorted(declared)}, the money columns are {MONEY_COLUMNS}"
    )
    assert declared == actual, (
        f"the note's column TYPES disagree with the migration: doc {declared} vs "
        f"SQL {actual} — a reader following the note would look for a column the "
        "record does not have"
    )


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
