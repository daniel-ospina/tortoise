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
        # #11827: the FORWARD migration that DROPS a column (`ALTER TABLE
        # public.point DROP COLUMN as_of`). Read here for the same reason as the
        # ADD file above: append-only history means a landed column can only ever
        # be removed this way, and a read that understood only the ADD would keep
        # certifying a column the database no longer has.
        "20261110000001_record_point_retire_as_of_11827.sql",
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
EXPECTED_TOTALS = {"P": 191, "D": 29, "U": 0}
#: 216 from the `#7955` pass + the 5 `#2782` money rows (all PRIMARY)
#: − the 1 row `#11827` RETIRES (`asOf`, a second name for the carrier's
#: valid-time start; its column is dropped by a forward migration).
EXPECTED_TOTAL_NAMES = 220

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
#: The mirror: `ALTER TABLE … DROP COLUMN [IF EXISTS] <col>` (#11827 retires
#: `point.as_of`). Same reason as the ADD form — the read must see the shrink.
_DROPPED_COLUMN_RE = re.compile(
    r"DROP\s+COLUMN\s+(?:IF\s+EXISTS\s+)?\"?(\w+)\"?", re.I
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
    # Forward ALTERs, applied IN THE ORDER THE SQL STATES THEM: a drop is the
    # shrinking direction, and one migration both drops and re-adds the same
    # name (`20261027000001` re-declares `node_identity.key_property`, which is
    # `GENERATED ALWAYS` and cannot be altered in place). Grouping "all adds,
    # then all drops" deletes a live column — the same ordering rule
    # `record_projection._ordered_column_ops` applies.
    ops: list[tuple[int, str, str, str]] = []
    for block in _ALTER_ADD_COLUMN_RE.finditer(text):
        name, body = block.group(1), block.group(2)
        if name not in tables:
            continue  # an ALTER on a table this file does not pin (non-record)
        for m in _ADDED_COLUMN_RE.finditer(body):
            ops.append((block.start() + m.start(), name,
                        m.group(1).strip('"'), "add"))
        for m in _DROPPED_COLUMN_RE.finditer(body):
            ops.append((block.start() + m.start(), name,
                        m.group(1).strip('"'), "drop"))
    for _position, name, column, kind in sorted(ops):
        if kind == "add":
            tables[name] |= {column}
        else:
            tables[name] -= {column}
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

# ── the same row's ARMING claim, which is a live-state claim like any other ───
# `test_the_routing_index_census_matches_the_record_migrations` reads this row's
# CENSUS. The row ALSO declares the record's **live operational state**, and that
# half carried the pre-arming census ("the record is NOT ARMED … because R1 has
# still not armed the DSN") for eleven days after the arming was measured, while
# `08-hosted-prerequisites.md` §A8 — the file this row's own source column points
# at — and `09-record-tenancy-decision.md` were corrected to say the opposite
# (#11064 review, P2: the correction stopped one hop short, at the doc that
# ROUTES every reader, so a lane arrives at the corrected page with the stale
# premise already loaded). The arming is measured, not asserted: `#9548` comment
# `6407834085` (2026-10-26, `armed: true`, `tortoise_record_app` granted `LOGIN`)
# and the owner's 2026-11-03 decision (comment `6612968196`) that superseded the
# ruling which had forbidden the provisioning.
_INDEX_ARMED_CLAIM = "the record is ✅ ARMED on the hosted deployment"
#: What makes that claim EVIDENCE rather than a second assertion — the counter
#: that was actually read (the epic's own rounded bound: an empty
#: `record_write_failures` falsifies "writes are failing" and does NOT establish
#: that a row was written).
_INDEX_ARMED_EVIDENCE = ("record_write_failures",)
#: …and the build the reading was taken on, pinned by **shape, not by value**
#: (#11064 review, non-blocking note 1). The value in the row is a *measurement*
#: (`fc6a8a64b` was serving when it was taken); it changes on the next deploy, so
#: pinning the literal would steer the next fixer to restore a build that no
#: longer serves — a stale evidence claim, which is the defect class this pin
#: exists to close. What must survive is that the row names a build at all, so a
#: rewrite cannot degrade the reading into an anonymous assertion.
_INDEX_ARMED_BUILD_RE = re.compile(r"build `[0-9a-f]{7,40}`")
#: The retired live-state sentences. They may STAY as history — this repo keeps a
#: superseded statement in place, labelled and dated — but a row that carries one
#: with no `SUPERSEDED` marker BEFORE it is telling a lane, in the present tense,
#: something measured false on 2026-10-26.
#:
#: The three sites are named by DESCRIPTION, not by character offset (#11064
#: review round 9, P3): they used to be cited as `08:205-238` and `09:196-212`,
#: and the second was wrong the moment it was written — the offset it gave as the
#: START sat on the tail of the PREVIOUS bullet, and the one it gave as the END
#: on a blank line plus `## Reopening`, while the block it meant to name sat
#: strictly BETWEEN them (it is the third bullet under *Not verified by this
#: lane*, named in the list below). That block is therefore ALSO not restated
#: here as a `09:<n>` span: it was correct when this paragraph was written and is
#: a decay waiting to happen, which is the whole point. ⛔ **The rule below was
#: quoted in round 10 as sitting "eight lines above this comment" — it did not
#: exist anywhere in the repo, so the pointer is now stated here in place rather
#: than quoted from a home it never had** (#11064 rebase review, P3): *no
#: character offsets on purpose — they were pinned once and were stale by the
#: next round.* A description survives the next edit; an offset does not.
#:   * `08` §R1 — the `SUPERSEDED`-labelled block in its read-back bullet;
#:   * `09` §6 — the third bullet under *Not verified by this lane* (the one that
#:     opens "This decision does not arm anything itself — but the arming has
#:     since happened", carrying `SUPERSEDED in part`);
#:   * the runbook — its `AUTHORISED AND PARTLY DONE` box at
#:     §"Arming the Postgres record".
_INDEX_RETIRED_ARMING = (
    "the record is NOT ARMED on the hosted deployment",
    "because R1 has still not armed the DSN",
)
_INDEX_SUPERSEDED_MARKER = "SUPERSEDED"
#: The two epic docs this row indexes, and the measured-arming sentence each now
#: carries. Pinned PRESENT so a later edit cannot flip the row back into
#: disagreement with the file it routes to.
_ARMED_SOURCE_OF_TRUTH = {
    "docs/epics/2026-10-10-7879-postgres-record/08-hosted-prerequisites.md": (
        "ARMED — AND THE ACT IS AUTHORISED"
    ),
    "docs/epics/2026-10-10-7879-postgres-record/09-record-tenancy-decision.md": (
        "is in force on production writes"
    ),
}

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
# ⛔ #11642 — THE OCCURRENCE HALF IS NOW DERIVED, not assumed to be 0. Before
# the vector move the whole point of this row was that `pgvector` appeared in NO
# migration, so `= 0 occurrences` could be a literal. `#11642` puts it in ONE
# (`20261109000001_record_embedding_vector.sql`), and a pattern that continues to
# demand the literal `0` would either fail forever or — worse — be "fixed" by
# editing the number, which is a hand-copied figure that goes stale the moment a
# second migration mentions the extension. The count is captured and checked
# against the tree below.
#: §A5's census line: "`grep -riE 'pgvector|vector\('` over all 81
#: migrations returns **28**" — the FILE-with-a-count claim is the index row's;
#: this one is the LINE count of the same grep, and the two are separate facts.
_PREREQ_A5_LINES_RE = re.compile(
    r"over all \d+\s+migrations returns \*\*(?P<occurrences>\d+)\*\*")

_INDEX_MIGRATION_COUNT_RE = re.compile(
    r"`pgvector` = (?P<occurrences>\d+) of (?P<count>\d+) migrations"
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
#: The FOURTH and FIFTH homes of the same denominator, and the two that went
#: stale while the three above were corrected (#1981 review, P2):
#: `docs/00_index.md`'s #4051 row (*"pgvector appears in **0 of 79
#: migrations**"*) and the doc THAT row routes to,
#: `docs/architecture/SEARCH-ARCHITECTURE.md` (*"appears in 0 of 79\nmigrations"* — wrapped, hence `\s+`). Neither was read: the patterns above
#: are anchored to §R7/§A5 of the prerequisites doc and to the #7879 index row,
#: so `-#4051`'s copy had no reader at all and went stale in the identical
#: direction. A count is only pinned where a pattern reaches it.
#: Same derivation for the two documents that use the `<occurrences> of <N>
#: migrations` form (see `_INDEX_MIGRATION_COUNT_RE` for why it is no longer a
#: literal `0`).
_MIGRATION_COUNT_OF_RE = re.compile(
    r"(?P<occurrences>\d+) of (?P<count>\d+)\s+migrations")
#: The two documents carrying the `0 of N migrations` form — scanned WHOLE,
#: because the phrase is not confined to one row.
_COUNT_OF_DOCS = (
    REPO_ROOT / "docs" / "00_index.md",
    REPO_ROOT / "docs" / "architecture" / "SEARCH-ARCHITECTURE.md",
)
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


# ── the label check, PER OCCURRENCE (it used to be first-occurrence only) ─────
# #11064's round-7 review measured that the two retired-sentence guards below
# were **fail-open for the exact regression they document**. Both were written
# as a first-occurrence pair — `at = text.find(retired)`, `labelled =
# text.find(marker)`, `assert labelled < at` — and `find` returns the EARLIEST
# match of each. The #7879 row is a single multi-thousand-character line that
# already carries an unrelated `SUPERSEDED` in the A8 cell, AHEAD of the retained
# R1 clause and its own label, so the pair degraded to "the row contains the word
# SUPERSEDED somewhere before this sentence". (No character offsets here on
# purpose: they were pinned once and were stale by the next round — the same decay
# this file's own doctrine removes. The span relation is the fact; the number is
# a copy of it.) Replayed against
# the head, that guard stayed GREEN for both mutations it exists to reject:
#
#   * append the retired sentence as a LIVE claim ("…and the record is NOT ARMED
#     on the hosted deployment, so R1 is not work to pick up.") — the edit shape
#     this row has actually taken every round; and
#   * delete the label that covers the retained R1 clause, leaving only the
#     unrelated earlier A8-cell label.
#
# A green run over either is a FALSE PASS on the premise that cost lane
# `p0-10000` a whole write-path verification (#10000), which is the defect class
# this PR exists to close — a gate that reports OK over the artifact being wrong.
#
# So the label requirement is now per occurrence AND adjacent: every occurrence
# needs a `SUPERSEDED` **label** before it with no sentence boundary in between,
# i.e. the marker must label the same sentence the retired text sits in. "Label"
# is not "mention": the marker counts only where it is a standalone token CLOSED
# by the label's own terminator — `SUPERSEDED,` / `SUPERSEDED:` / `SUPERSEDED.` /
# `**SUPERSEDED** —` — and never where it merely modifies a following noun
# (`the SUPERSEDED note`) or sits inside a parenthetical pointer (`(see
# SUPERSEDED)`, `[SUPERSEDED]`). Writing that split down was not enough: three
# further mutants beat the first cut of it (#11064 round-9 review, P3), and the
# constructor is now shaped by them rather than by the spellings that happened to
# be in front of it:
#
#   * a label whose own FULL STOP was consumed by the match read as "same
#     sentence" across a sentence break — `SUPERSEDED. In short, <retired>` passed,
#     and that is the strongest boundary a writer uses. The terminator is therefore
#     a LOOKAHEAD: it stays in `gap`, so `_SENTENCE_BOUNDARY_RE` sees it.
#   * `)` and `]` were accepted as closers, so `(see SUPERSEDED) and, as before,
#     <retired>` — the exact anaphoric "see the label above" mention the round-8
#     mutant exists to reject, with the noun dropped — passed. A bracket means a
#     parenthetical, i.e. a mention; it is no longer a closer.
#   * the docstrings advertised `**SUPERSEDED** —`, which the regex refused (an
#     over-block: a fixer told to use the documented bold form would get a red and
#     might weaken the guard instead of relabelling). A trailing emphasis run is
#     now a closer, so the documented form works.
#
# Non-vacuity is per CLAIM, not per fault class: each clause of the property below
# has a mutant that kills it, including one that must stay GREEN so the documented
# label forms remain usable. See
# `test_the_retired_sentence_guard_rejects_its_own_regressions`.
#
# Three more shapes survived that sentence rule, all measured GREEN on the real
# row before this round and all of them the same false PASS (#11064 round-9):
#
#   * **the label closes its OWN sentence.** `SUPERSEDED.` consumes the period as
#     part of the label token, so a boundary scan over the *gap* can never see it:
#     *"The earlier note was SUPERSEDED. Because R1 has still not armed the DSN,
#     nothing lands."* passed on a label that labelled the PREVIOUS sentence. A
#     label that closed its own sentence or clause (`;`) cannot cover what follows.
#   * **the label is in a different table cell.** These rows are markdown tables,
#     so `| … SUPERSEDED, | … R1 has still not armed the DSN |` puts the label and
#     the claim in two independent cells sharing a line; a cell boundary is as
#     hard a boundary as a sentence one.
#   * **the em-dash label was not accepted at all** — the shape this file's own
#     docs use most (`**SUPERSEDED** — kept as …`). That is the opposite error (a
#     false RED on a correctly labelled correction), and the comment above claimed
#     it was accepted while `_SUPERSEDED_LABEL_RE` rejected it. A comment that
#     states a property the code does not have is the same defect class as a doc
#     that states a measurement the deployment does not have.
#
# The property this enforces, stated exactly (do not restate it as "labelled"):
# **some `SUPERSEDED` LABEL token occurs before the retired text, and between
# them there is neither a sentence/clause boundary nor a markdown table-cell
# separator.** It is a textual proxy for "this clause is labelled history", not a
# proof of intent — a label token followed by a comma chain that then re-asserts
# the premise still passes. That residual is accepted and named, because the
# alternative (parsing the prose) is a bigger fail-open than the one it closes.
_SENTENCE_BOUNDARY_RE = re.compile(r"[.!?](?:\s|$)|;\s")
#: A markdown table-cell separator. Cells are independent claims sharing a line,
#: so a label in one does not label the text in the next. (`#7879`'s row IS a
#: markdown table row, so this is not hypothetical.)
_CELL_SEPARATOR = "|"
#: Punctuation that CLOSES the sentence or clause the label token sits in. A label
#: followed by one of these labels what PRECEDES it, never what follows.
#: ⛔ This is NOT redundant with `_SENTENCE_BOUNDARY_RE`, and the merge that
#: deleted the first lane's equivalent table on that assumption re-opened a hole:
#: that regex only recognises a terminator followed by whitespace or end-of-text,
#: so `SUPERSEDED."` — the ordinary quoted-closer shape — and a typo like
#: `SUPERSEDED.Because` both escaped it. Whether a rule is redundant is a question
#: about BEHAVIOUR on the mutants, not about the text of the two rules
#: (#11064 round-10 review, P3: measured by loading both parents and this merge as
#: three modules against the same artifact — peer RED, merge GREEN, on 11 mutant
#: classes the textual diff showed as a tidy-up).
_LABEL_CLOSES_ITS_OWN_CLAUSE = ".!?;"
#: A `SUPERSEDED` that is a LABEL — a standalone token closed by `, ; : . ! ?`, by
#: an em/en dash (with or without a preceding emphasis run, so both `SUPERSEDED —`
#: and `**SUPERSEDED** —` are labels), by a trailing emphasis run
#: (`**SUPERSEDED**`), by end of text, or by the qualifier `in part` before any of
#: those closers (`**SUPERSEDED\n  in part:**`). The closer is a LOOKAHEAD so it stays in
#: the text between the label and the retired sentence, where the
#: sentence-boundary test can see it — with the closer CONSUMED, `SUPERSEDED.` read
#: as "same sentence" across the very boundary the label had just closed. ⛔ `)`
#: and `]` are deliberately NOT closers: a bracket is a parenthetical pointer,
#: i.e. a mention. This list is the whole accepted set — nothing else is
#: documented in prose, so a fixer is never told to use a form the regex refuses.
_SUPERSEDED_LABEL_RE = re.compile(
    re.escape(_INDEX_SUPERSEDED_MARKER)
    # ⛔ `SUPERSEDED in part:` is a LABEL TOO (#11064 review r17, P3). The closer had
    # to follow the token immediately (whitespace/emphasis only), so the form this
    # repo itself ships — `**SUPERSEDED\n  in part:**`, `09-record-tenancy-decision.md`
    # §5 — was not a label: a fixer copying the repo's own spelling got a false RED
    # whose remedy ("add a SUPERSEDED label") did not fit the text in front of it.
    # Latent when found (the guard is applied to the index row and 08 §R8, not 09),
    # i.e. exactly the kind of hole that fires on the next file to use it.
    + r"(?:\s+in\s+part)?"
    + r"(?=[\s*_]*(?:[,;:.!?]|[\u2014\u2013]|[*_]+|$))"
)


def _assert_retired_claim_is_labelled(
    text: str,
    needle: str,
    *,
    where: str,
    remedy: str,
) -> None:
    """Raise unless EVERY `needle` in `text` is covered by its own `SUPERSEDED` label.

    Per occurrence, not first-occurrence (see the block comment above): a
    retired sentence may be kept as labelled history — this repo's convention for
    an overridden ruling — but each retained copy must be inside the sentence
    whose LABEL covers it. A copy with no label before it, with only a *mention*
    of the word (`the SUPERSEDED note`) in a preceding clause, with the covering
    label a sentence away (so it was never covering this clause), with a label
    that closed its own sentence (`SUPERSEDED.`), or with the label in another
    table cell, is telling a reader something was measured false as if it were
    live.
    """
    for match in re.finditer(re.escape(needle), text):
        labels = list(_SUPERSEDED_LABEL_RE.finditer(text, 0, match.start()))
        assert labels, (
            f"{where} asserts {needle!r} with no "
            f"{_INDEX_SUPERSEDED_MARKER} LABEL before it (a bare mention such as "
            f"'the {_INDEX_SUPERSEDED_MARKER} note' does not label a clause) — "
            f"that is a live instruction, not labelled history. {remedy}"
        )
        label = labels[-1]
        gap = text[label.end() : match.start()]
        # A label whose OWN terminator closes the sentence labels what precedes it,
        # never what follows (`.!/;` — see `_LABEL_CLOSES_ITS_OWN_CLAUSE` for why
        # this is not what the sentence-boundary scan below already covers).
        after = gap.lstrip(" \t*_")
        assert not after or after[0] not in _LABEL_CLOSES_ITS_OWN_CLAUSE, (
            f"{where} asserts {needle!r} after a {after[0]!r}-closed "
            f"{_INDEX_SUPERSEDED_MARKER} label ({label.group(0)!r}); that label "
            "CLOSED its own sentence, so it labels the clause BEFORE it and not "
            f"this one — the retired text still reads as live. {remedy}"
        )
        assert _CELL_SEPARATOR not in gap, (
            f"{where} asserts {needle!r} with its nearest "
            f"{_INDEX_SUPERSEDED_MARKER} label in a DIFFERENT table cell; cells "
            f"are independent claims sharing a line, so that label does not "
            f"cover this one. {remedy}"
        )
        assert not _SENTENCE_BOUNDARY_RE.search(gap), (
            f"{where} asserts {needle!r} with the nearest "
            f"{_INDEX_SUPERSEDED_MARKER} label a SENTENCE away ({gap[:160]!r}); "
            "a label in an earlier sentence does not cover this one, so the "
            f"retired text still reads as live. {remedy}"
        )


def _index_record_row() -> str:
    """The routing index's #7879 row — exactly one, or a loud failure."""
    rows = [
        line
        for line in _INDEX_DOC.read_text(encoding="utf-8").splitlines()
        if _INDEX_RECORD_ROW_MARKER in line
    ]
    assert len(rows) == 1, (
        f"docs/00_index.md has {len(rows)} rows for the record's hosted "
        f"prerequisites (marker {_INDEX_RECORD_ROW_MARKER!r})"
    )
    return re.sub(r"\s+", " ", rows[0])


def test_the_routing_index_declares_the_measured_arming_not_the_census():
    """The index's #7879 row says ARMED, and any retained "not armed" is labelled.

    The row is the entry point a lane reads FIRST (AGENTS.md → Documentation
    Filing: *"For topic-to-file routing, see `docs/00_index.md`"*), and its own
    source column points at `08-hosted-prerequisites.md`, whose §A8 was corrected
    to say the record is armed and authorised. Until this pin, the row still said
    the opposite in the present tense — the same defect class the epic artifacts
    were fixed for, one hop further out, where the review's own sweep over "the
    files this PR edits" could not see it.

    RED on the pre-fix text — measured at #11064's head before the correction: the
    row carried **neither** `the record is ✅ ARMED on the hosted deployment`
    **nor** the `record_write_failures` counter and a measured build, and both
    retired sentences appeared with **no** `SUPERSEDED` marker anywhere in the row.

    Non-vacuity is the inverted half: the retired sentences are NOT banned — they
    are this repo's convention for an overridden ruling (keep it, label it, date
    it) — so the assertion is that any retained one is preceded by the label. A
    row that re-asserts "R1 has still not armed the DSN" as live reds here, and so
    does a rewrite that deletes the measured state instead of correcting it.

    The build is pinned by SHAPE (a `build` reference plus a sha), never by value:
    the value in the row is a measurement of a build that changes on the next
    deploy, and pinning the literal would send the next fixer to restore a build
    that is no longer serving (#11064 review, non-blocking note 1).
    """
    row = _index_record_row()
    assert _INDEX_ARMED_CLAIM in row, (
        "docs/00_index.md's #7879 row no longer states the MEASURED arming state "
        f"({_INDEX_ARMED_CLAIM!r}). It is the routing surface a lane reads first, "
        "and the epic doc it indexes (§A8) says the record is armed"
    )
    for needle in _INDEX_ARMED_EVIDENCE:
        assert needle in row, (
            f"docs/00_index.md's #7879 row states arming without {needle!r} — the "
            "claim is then an assertion, not a measurement (cite the build and the "
            "counter, and keep the bound: an empty `record_write_failures` is not "
            "evidence that a row was written)"
        )
    assert _INDEX_ARMED_BUILD_RE.search(row), (
        "docs/00_index.md's #7879 row states arming without naming the BUILD the "
        "reading was taken on (expected a `build` reference plus a sha). Leave the "
        "sha's value alone — it is a measurement, and it changes on the next "
        "deploy; what must survive is the reference, so the claim cannot decay "
        "into an anonymous assertion"
    )
    for retired in _INDEX_RETIRED_ARMING:
        _assert_retired_claim_is_labelled(
            row,
            retired,
            where="docs/00_index.md's #7879 row",
            remedy=(
                "That sentence is false since 2026-10-26 (#9548 comment "
                "6407834085). Keep it only as labelled history, as 08 §A8 and 09 "
                "do — one `SUPERSEDED` in the SAME sentence, not an earlier cell"
            ),
        )
    # The row and the docs it routes to must AGREE — that disagreement is the
    # finding, not the stale row on its own.
    for path, anchor in _ARMED_SOURCE_OF_TRUTH.items():
        text = re.sub(r"\s+", " ", (REPO_ROOT / path).read_text(encoding="utf-8"))
        assert anchor in text, (
            f"{path} no longer states the measured arming ({anchor!r}) — the "
            "index row's correction was derived from it, so re-derive both together"
        )


# ── the SAME stale-premise sweep, applied to the file the index routes to ─────
# #11064's round-1 fix corrected the runbook and left the two live epic docs
# carrying the superseded ruling; the round-3 fix corrected those and left the
# index; this round's finding is that the §A8 disclaimer then claimed a COMPLETE
# enumeration of the remaining census-point statements — two — while the file
# carried four. A closed list is the mechanism: it tells a reader that everything
# unlisted is current, so each correction that enumerates its sites hands the
# next stale sentence a place to hide. The two pins below hold the fix in the
# shape that cannot rot that way (state the invariant, label the list
# non-exhaustive) and hold the one cross-artifact disagreement this round found.

#: §A8's disclaimer must say what makes a statement here trustworthy — its own
#: measurement date — rather than claiming its list of exceptions is complete.
_PREREQ_DISCLAIMER_INVARIANT = "does not carry its own measurement date"
_PREREQ_DISCLAIMER_NOT_EXHAUSTIVE = "NOT exhaustive"
#: The retired closed-list claim: `the two remaining census-point statements`.
#: It was wrong when written (the file carried four — R6's read-back note, R7
#: item 2, R8's drill attribution and the A8 RLS row — and the §G paragraph made
#: five), and because it enumerated it made every unlisted sentence read as
#: vetted-current.
_PREREQ_DISCLAIMER_RETIRED_CLOSED_LIST = "the two remaining census-point statements"

#: R8's drill attribution after the correction, and the runbook's §9 drill row it
#: must agree with. Two artefacts an operator reads for this epic disagreed on
#: what blocks the drill, and the direction was the costly one: 08 said "blocked
#: on arming", which is the premise that made lane `p0-10000` conclude that the
#: only remaining move was the prohibited one.
_DRILL_BLOCKER_ANCHORS = (
    # a captured session that actually wrote the two row kinds
    "captured",
    "journal_event",
    # the record leg needs a DIRECT (not pooler) backup-capable DSN
    "direct",
)
_DRILL_RETIRED_ATTRIBUTION = "#9548 hosted arming / #9551 CI"
_RUNBOOK_DOC = REPO_ROOT / "docs" / "infra-runbook.md"
_RUNBOOK_DRILL_ROW_MARKER = "a restore executed against a **real armed record**"


def test_the_epic_a8_disclaimer_states_an_invariant_not_a_closed_list():
    """§A8 tells a reader how to judge any statement here, not just listed ones.

    The disclaimer that the previous fix round added enumerated its exceptions
    ("the two remaining census-point statements elsewhere in it") and was wrong
    on its own count — the file carried four, and the §G paragraph made five. The
    defect is not the number: an enumeration that claims completeness makes every
    unlisted sentence read as vetted-current, which is exactly how a stale
    "R1 is unarmed" survived two sweeps scoped to "the files this PR touched".

    So the pin is on the shape: state the invariant (any statement without its own
    measurement date describes the tree that was), label the list NOT exhaustive,
    and never re-assert the closed-list claim. RED on the pre-fix text — measured
    at #11064's head before this correction: `the two remaining census-point
    statements` was present and neither the invariant nor the non-exhaustive
    label was.
    """
    text = re.sub(r"\s+", " ", _PREREQUISITES_DOC.read_text(encoding="utf-8"))
    assert _PREREQ_DISCLAIMER_RETIRED_CLOSED_LIST not in text, (
        "08-hosted-prerequisites.md §A8 claims its list of census-point "
        f"statements is complete ({_PREREQ_DISCLAIMER_RETIRED_CLOSED_LIST!r}) — it "
        "was not (the file carried four, R6's read-back note and R8's drill "
        "attribution among them). State the invariant and label the list NOT "
        "exhaustive instead of counting sites"
    )
    assert _PREREQ_DISCLAIMER_INVARIANT in text, (
        "08-hosted-prerequisites.md §A8 no longer states the invariant that makes "
        f"this file readable ({_PREREQ_DISCLAIMER_INVARIANT!r}): a statement "
        "without its own measurement date describes the tree that was. Without "
        "it, only the listed sites are safe and the next stale sentence hides"
    )
    assert _PREREQ_DISCLAIMER_NOT_EXHAUSTIVE in text, (
        "08-hosted-prerequisites.md §A8's list of dated sites must be labelled "
        f"{_PREREQ_DISCLAIMER_NOT_EXHAUSTIVE!r} — an unlabelled list of "
        "exceptions reads as a complete one"
    )


def test_the_epic_r8_drill_blocker_agrees_with_the_runbook():
    """R8's unmeasured-drill attribution must name what is actually missing.

    Both artefacts state why the hosted drill has not run, and they disagreed:
    `docs/infra-runbook.md` §9's drill row was corrected to *"the blocker is not
    the arming any more"* (missing: a captured session that wrote a
    `journal_event` + `point` row, and a direct backup-capable DSN), while R8
    still said the row was unmeasured `(#9548 hosted arming / #9551 CI
    TEST_RECORD_DB_URI)` — two things the same runbook states are both satisfied.
    The costly direction: 08 sent a lane back to the arming premise.

    RED on the pre-fix text: the retired attribution was present in R8 and neither
    `captured` nor `direct` appeared in the R8 section. Non-vacuity is the
    cross-artifact half — the runbook's own drill row must still carry the same
    two elements, so this cannot be satisfied by weakening 08 alone.

    The retired wording may STAY as labelled history (this repo keeps a superseded
    statement in place, dated and relabelled), so it is only a failure when it
    appears with no `SUPERSEDED` marker before it.
    """
    text = _PREREQUISITES_DOC.read_text(encoding="utf-8")
    flat = re.sub(r"\s+", " ", text)
    start = flat.index("### R8 —")
    # R9 is the next section — scope to it so "the epic says captured somewhere"
    # cannot stand in for "R8 names what blocks the drill".
    section = flat[start : flat.index("### R9 —")]
    for anchor in _DRILL_BLOCKER_ANCHORS:
        assert anchor in section, (
            f"08-hosted-prerequisites.md §R8 does not name {anchor!r} as part of "
            "what blocks the hosted drill. The runbook's §9 drill row (the "
            "corrected one) names a captured session that wrote a "
            "`journal_event` + `point` row and a DIRECT backup-capable DSN; an "
            "operator reading R8 must not be sent back to the arming premise"
        )
    _assert_retired_claim_is_labelled(
        section,
        _DRILL_RETIRED_ATTRIBUTION,
        where="08-hosted-prerequisites.md §R8",
        remedy=(
            "Both of those blockers are satisfied (2026-10-26 arming; #9551 "
            "closed 2026-10-23), so as a live attribution it sends a lane to "
            "redo work that is done. Keep it only as labelled history — one "
            "`SUPERSEDED` in the SAME sentence, not an earlier section"
        ),
    )
    # …and the two artifacts must still agree, which is the finding itself.
    runbook_rows = [
        line
        for line in _RUNBOOK_DOC.read_text(encoding="utf-8").splitlines()
        if _RUNBOOK_DRILL_ROW_MARKER in line
    ]
    assert len(runbook_rows) == 1, (
        "docs/infra-runbook.md §9 has no single "
        f"{_RUNBOOK_DRILL_ROW_MARKER!r} drill row ({len(runbook_rows)} found) — "
        "the correction in 08 §R8 is derived from it"
    )
    runbook_row = re.sub(r"\s+", " ", runbook_rows[0])
    for anchor in ("captured", "direct"):
        assert anchor in runbook_row, (
            f"docs/infra-runbook.md §9's armed-record drill row no longer names "
            f"{anchor!r} as part of the blocker — re-derive 08 §R8's attribution "
            "with it rather than letting the two drift apart again"
        )


def test_the_retired_sentence_guard_rejects_its_own_regressions():
    """The label guard is executed against the mutations it exists to reject.

    The #11064 round-7 review measured that the two pins above were **fail-open**:
    both located the retired sentence and the `SUPERSEDED` marker with
    first-occurrence `find`, so on the real #7879 row — one very long line
    carrying an unrelated `SUPERSEDED` well before this sentence — the check
    reduced to "the word appears somewhere before this sentence", and it stayed
    GREEN for both regressions below. A green run over a stale premise is a false
    PASS, and a false PASS is the one verdict this whole file exists to prevent.

    Restating the guard as "per occurrence + same sentence" is only half the
    fix: the half that can rot is a docstring claiming a property nothing
    executes. So this test takes the REAL head text — not a fixture, so it cannot
    drift from the artifact — applies each mutation, and requires a red:

    The mutants are grouped by the CLAIM each one kills, because a textual
    invariant has no universal sufficient mutant set — coverage means every
    declared claim has a mutant, not that the pile is large:

    * **per occurrence** — (1) **append** the retired sentence as a live claim at
      the end of the row, the edit shape this row has taken every round; (2)
      **strip the covering label** from the retained clause, leaving the unrelated
      earlier A8-cell marker as the only `SUPERSEDED` in the row.
    * **a label, not a mention** — (3) `the SUPERSEDED note, because …`; (4) the
      same with the noun dropped (`Per the SUPERSEDED note, …`); (5) a
      **parenthetical pointer**, `(see SUPERSEDED) and, as before, …`; (6) a
      bracketed one, `[SUPERSEDED] and …`. (5) and (6) are the anaphoric "see the
      label above" move in its other spellings.
    * **adjacent, not a sentence away** — (7) the label closes its **own**
      sentence (`… was SUPERSEDED. Because …`, and the quoted-closer spelling
      `… was SUPERSEDED." Because …`); (8) the label sits in a **different
      markdown table cell** from the claim it is supposed to cover.
    * **the inverse control** — (9) an **em-dash label** (`**SUPERSEDED** — …`)
      must stay GREEN. It is this repo's most common labelled-history shape, and
      refusing it was a false RED that would push a fixer into weakening the guard
      rather than relabelling.

    Each RED mutant is measured GREEN under the rule it was written to defeat —
    not merely supposed to be — and if the guard is over-tightened so that a real
    label stops counting, mutant (9) reds instead: the two failure directions are
    pinned separately.
    """
    row = _index_record_row()
    marker = _INDEX_SUPERSEDED_MARKER

    # The head row is green under the guard — otherwise the mutations below
    # would prove nothing about the guard (they would only restate a failure).
    for retired in _INDEX_RETIRED_ARMING:
        _assert_retired_claim_is_labelled(
            row, retired, where="the head row", remedy="fix the row"
        )

    # The needle to mutate has to be one the head actually RETAINS: a needle that
    # occurs zero times iterates zero times, so mutating it would prove nothing.
    # Which retired sentence survives is the row's business, so it is read off the
    # row rather than hard-coded.
    retained = [n for n in _INDEX_RETIRED_ARMING if n in row]
    assert retained, (
        "the #7879 row retains none of "
        f"{_INDEX_RETIRED_ARMING} — this pin mutates a retained sentence, so "
        "either the row was rewritten (drop the mutation that no longer applies) "
        "or labelled history was deleted rather than kept"
    )
    needle = retained[0]

    def _expect(verdict: str, mutated: str, why: str) -> None:
        """The guard must reach `verdict` on this mutation — that IS the deliverable."""
        try:
            _assert_retired_claim_is_labelled(
                mutated, needle, where="the mutation", remedy="none"
            )
        except AssertionError as exc:
            if verdict == "RED":
                return
            # Chained, not swallowed: the raised message carries WHAT the pin
            # demanded, and the guard's own refusal stays on the traceback as
            # the cause — a fixer needs both to tell an over-tight guard from a
            # mutation that really is labelled.
            raise AssertionError(
                f"the guard REFUSED a form it must accept. {why}"
            ) from exc
        if verdict == "RED":
            raise AssertionError(why)

    def _expect_red(mutated: str, why: str) -> None:
        _expect("RED", mutated, why)

    # MUTATION 1 — the stale premise re-appended as a live claim. `find`-based
    # first-occurrence logic sees the earlier, unrelated marker and passes;
    # per-occurrence logic sees no marker inside this sentence and reds.
    mutated_append = f"{row} In short: {needle}."
    assert mutated_append.rfind(needle) >= len(row), (
        "the append mutation did not land a second occurrence after the head "
        "row, so it does not exercise the appended-live-claim shape"
    )
    assert mutated_append.find(marker) < mutated_append.find(needle), (
        "the append mutation must leave a `SUPERSEDED` BEFORE the first "
        f"{needle!r}, or a first-occurrence check would red anyway and the "
        "mutation tests nothing"
    )
    _expect_red(
        mutated_append,
        "the retired-sentence guard accepted a row that re-appends "
        f"{needle!r} as a LIVE claim — it is fail-open for the regression it "
        "documents (the earlier `SUPERSEDED` it matched is an unrelated "
        "clause). Require the label per occurrence AND in the same sentence",
    )

    # MUTATION 2 — the covering label deleted from the retained clause. Its own
    # marker sits in the same sentence; removing it must red even though an
    # unrelated `SUPERSEDED` still exists earlier in the row.
    covering_at = row.rfind(marker, 0, row.find(needle))
    assert covering_at != -1, (
        "the head row no longer carries a `SUPERSEDED` marker before "
        f"{needle!r} — the retained clause lost its own label, which is the "
        "defect this pin exists to catch"
    )
    mutated_strip = row[:covering_at] + row[covering_at + len(marker) :]
    assert marker in mutated_strip[: mutated_strip.find(needle)], (
        "the strip mutation left no `SUPERSEDED` before the retained "
        f"{needle!r}, so it no longer isolates the first-occurrence defect (a "
        "first-occurrence check would red too). The row is expected to carry an "
        "unrelated earlier marker"
    )
    _expect_red(
        mutated_strip,
        "the retired-sentence guard accepted a row whose retained "
        f"{needle!r} lost its OWN `SUPERSEDED` label, because an unrelated "
        "earlier marker still existed — that is the first-occurrence "
        "defect. Require the label in the same sentence as the sentence",
    )

    # MUTATIONS 3–7 — one per CLAIM the docstring makes, not one per fault class.
    # A textual invariant has no universal sufficient mutant set, so each clause of
    # "label, not a mention; adjacent, not a sentence away" gets a mutant that
    # kills it (#11064 round-9 review, P3 — the first cut of this drift was caught
    # only because a reviewer wrote the mutants the test had not).
    #
    # Rounds 8 and 9 each contributed shapes the previous constructor accepted:
    # the bare mention, the mention whose own full stop was swallowed by the match,
    # and the parenthetical pointer. The last is the SAME anaphoric move as the
    # bare mention with the noun dropped — `(see SUPERSEDED) and, as before,
    # <retired>` — which is why a bracket is no longer a label closer.
    for why, mention in (
        (
            "a bare mention of the word is not a label",
            f"In short, the earlier {marker} note stands, and {needle}.",
        ),
        (
            "the same mention, noun dropped — a label is not a pointer",
            f"Per the {marker} note, {needle}, nothing lands.",
        ),
        (
            "a label's own full stop must NOT read as the same sentence",
            f"{marker}. In short, {needle}, nothing lands.",
        ),
        (
            "a parenthetical pointer is a mention, not a label",
            f"(see {marker}) and, as before, {needle}, nothing lands.",
        ),
        (
            "a bracketed pointer is a mention, not a label",
            f"[{marker}] and {needle}.",
        ),
        (
            "a label adjacent through an emphasis run IS a label — this one must "
            "stay GREEN, or the documented forms become unusable and a fixer "
            "reds on correct prose",
            f"**{marker}** — {needle}.",
        ),
    ):
        mutated_mention = f"{row} {mention}"
        assert mutated_mention.rfind(needle) >= len(row), (
            "the mention mutation did not land a second occurrence after the "
            f"head row: {mention!r}"
        )
        _expect(
            "GREEN" if mention.startswith(f"**{marker}**") else "RED",
            mutated_mention,
            f"the retired-sentence guard got the wrong verdict on a sentence it "
            f"must judge: {why}. Mutation was {mention!r}; the stale premise is "
            f"{needle!r} and a LABEL is a token closed by `, ; : . ! ?`, a "
            "trailing emphasis run, or end of text — never a bracket, and never "
            "a word modifying a following noun",
        )
        # …and for the RED ones, the RETAINED occurrence must still be accepted:
        # otherwise the mutant would red for the head row's own label being wrong,
        # and it would prove nothing about the appended claim.
        if not mention.startswith(f"**{marker}**"):
            _assert_retired_claim_is_labelled(
                f"{row} {marker}, and this stands.",
                needle,
                where="the control",
                remedy="none",
            )

    # MUTATION 7 (own-sentence closer) — the label closes its OWN sentence and the
    # premise is re-asserted as live in the next one. This was GREEN before round 9,
    # on the most natural way to write a superseded marker: a sentence of its own.
    mutated_closed = f"{row} The earlier note was {marker}. Because {needle}, nothing lands."
    assert mutated_closed.rfind(needle) >= len(row), (
        "the clause-closing mutation did not land a second occurrence after the "
        "head row"
    )
    _expect_red(
        mutated_closed,
        "the retired-sentence guard accepted a label that closed its own "
        f"sentence ('{marker}.') and then re-asserted {needle!r} as live in the "
        "next one — the boundary was consumed by the label match, so the gap "
        "scan never saw it. A label that closes a sentence or clause (`.!?;`) "
        "labels what precedes it, never what follows",
    )

    # MUTATION 7b — the SAME own-sentence hole, in the spelling that survives a
    # whitespace-anchored boundary scan: a quoted closer (`SUPERSEDED."`) or a typo
    # with no space after the terminator. `_SENTENCE_BOUNDARY_RE` only recognises
    # `[.!?]` followed by whitespace or end-of-text, so a rule built solely on the
    # gap was GREEN for every one of these while the peer lane's terminator check
    # was RED — measured differentially across both parents and this merge, which is
    # how the regression this pins got in (#11064 round-10 review, P3).
    for closer in ('."', ".B", ".**", ";B", "!B", "?B"):
        mutated_closed_odd = f"{row} The earlier note was {marker}{closer} Because {needle}."
        assert mutated_closed_odd.rfind(needle) >= len(row), (
            f"the {closer!r}-closer mutation did not land a second occurrence "
            "after the head row"
        )
        _expect_red(
            mutated_closed_odd,
            f"the retired-sentence guard accepted a label closed by {closer!r} and "
            f"then re-asserted {needle!r} as live — the terminator closes the "
            "label's own sentence whatever follows it, so a whitespace-anchored "
            "boundary scan is not enough on its own",
        )

    # MUTATION 8 (other table cell) — the label and the claim sit in DIFFERENT cells
    # of the same row. These docs are markdown tables, so this is the edit shape a lane gets
    # when it labels one cell and re-asserts the premise in the next one; the cell
    # separator is as hard a boundary as a sentence end.
    mutated_cell = f"{row} | the earlier note was {marker}, | {needle} |"
    assert mutated_cell.rfind(needle) >= len(row), (
        "the cell mutation did not land a second occurrence after the head row"
    )
    _expect_red(
        mutated_cell,
        "the retired-sentence guard accepted a label in a DIFFERENT table cell "
        f"from the {needle!r} it is supposed to cover — cells are independent "
        "claims sharing a line, so the label does not reach across a `|`",
    )

    # MUTATION 9 (inverse control): an em-dash label must stay GREEN. This is the
    # shape the docs in this repo use most (`**SUPERSEDED** — kept as …`), and
    # `_SUPERSEDED_LABEL_RE` did not accept it before round 9 — so a correctly
    # labelled correction was a false RED, and the block comment claimed otherwise.
    mutated_dash = f"{row} Kept as the {marker} — {needle}, nothing lands."
    assert mutated_dash.rfind(needle) >= len(row), (
        "the em-dash mutation did not land a second occurrence after the head row"
    )
    _assert_retired_claim_is_labelled(
        mutated_dash, needle, where="the em-dash label", remedy="none"
    )

    # MUTATION 10 (inverse control): the `in part` label must stay GREEN. This is the
    # spelling `09-record-tenancy-decision.md` §5 ships
    # (`**SUPERSEDED\n  in part:** the sentence below said …`), and
    # `_SUPERSEDED_LABEL_RE` refused it before r17 — the closer had to follow the
    # token immediately, so the repo's own form was not a label. A fixer who copies
    # the shipped spelling must not be told to add a label that is already there.
    mutated_in_part = f"{row} **{marker}\n  in part:** {needle}, nothing lands."
    assert mutated_in_part.rfind(needle) >= len(row), (
        "the `in part` mutation did not land a second occurrence after the head row"
    )
    _assert_retired_claim_is_labelled(
        mutated_in_part, needle, where="the `in part` label", remedy="none"
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

    # ⛔ #11642: the OCCURRENCE half, derived. Every `pgvector` census sentence
    # states how many migrations mention the extension; that number is measured
    # here with the same grep the prose names, so it cannot drift into a
    # hand-copied figure the way the denominator once did.
    on_disk_pgvector = sum(
        1 for path in _MIGRATIONS_DIR.glob("*.sql")
        if re.search(r"pgvector|vector\(", path.read_text(encoding="utf-8"),
                     re.I))
    # ⛔ §A5 STATES A LINE COUNT, NOT A FILE COUNT, and it is DERIVED here rather
    # than skipped (#11642 review round 2, P3). The earlier draft `continue`d on
    # it, which made the arm dead code — and that dead arm is exactly why a
    # stale `26` shipped in both §A5 and the index row when the file actually
    # matched 28 lines. The two units are different questions about the same
    # grep, so each is measured against its own command.
    # ⛔ LINES, not matches: the command §A5 names is `grep -riE … | wc -l`, which
    # counts LINES carrying at least one match. A `findall` count is a different
    # number (measured: 31 matches on 28 lines), and taking it would have made
    # this pin demand a figure the stated command cannot produce.
    on_disk_pgvector_lines = sum(
        sum(1 for line in path.read_text(encoding="utf-8").splitlines()
            if re.search(r"pgvector|vector\(", line, re.I))
        for path in _MIGRATIONS_DIR.glob("*.sql"))
    stated = [int(m.group("occurrences"))
              for m in _INDEX_MIGRATION_COUNT_RE.finditer(index_row)]
    assert stated, ("docs/00_index.md's #7879 row no longer states its pgvector "
                    "occurrence count")
    assert set(stated) == {on_disk_pgvector}, (
        f"docs/00_index.md says pgvector appears in {sorted(set(stated))} "
        f"migration(s); the tree has {on_disk_pgvector} — re-state it at this head")
    # §A5's figure is the LINE count of `grep -riE 'pgvector|vector\('`; the
    # pattern it states is `over all N migrations returns **M**`.
    a5 = _PREREQ_A5_LINES_RE.search(prerequisites)
    assert a5, ("08-hosted-prerequisites.md §A5 no longer states the grep's LINE "
                "count — restate it rather than deleting the phrase")
    assert int(a5.group("occurrences")) == on_disk_pgvector_lines, (
        f"§A5 says the pgvector grep returns {a5.group('occurrences')} lines; the "
        f"tree has {on_disk_pgvector_lines} — re-state it at this head")
    for path in _COUNT_OF_DOCS:
        stated = [int(m.group("occurrences"))
                  for m in _MIGRATION_COUNT_OF_RE.finditer(
                      path.read_text(encoding="utf-8"))]
        if stated:
            assert set(stated) == {on_disk_pgvector}, (
                f"{path.relative_to(REPO_ROOT)} says pgvector appears in "
                f"{sorted(set(stated))} migration(s); the tree has "
                f"{on_disk_pgvector} — restate it rather than deleting the phrase")

    # The same denominator in its OTHER two homes (#1981 review, P2). These are
    # scanned as whole documents rather than rows: the phrase is not confined to
    # one row, and the one in `SEARCH-ARCHITECTURE.md` wraps across a line.
    for path in _COUNT_OF_DOCS:
        text = path.read_text(encoding="utf-8")
        stated = [int(m.group("count")) for m in _MIGRATION_COUNT_OF_RE.finditer(text)]
        assert stated, (
            f"{path.relative_to(REPO_ROOT)} no longer states `0 of N migrations` "
            "— restate it rather than deleting the phrase: this pin reads it"
        )
        assert set(stated) == {on_disk}, (
            f"{path.relative_to(REPO_ROOT)} says `0 of {sorted(set(stated))} "
            f"migrations`, {_MIGRATIONS_DIR.relative_to(REPO_ROOT)} holds "
            f"{on_disk} ({migrations[-1].name} is the newest). Two sites of this "
            "denominator already went stale while the other three were "
            "corrected — a count is only pinned where a pattern reaches it"
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
    # own guard in `test_9231`, this one does not). Measured: over all 79 files
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


MONEY_COLUMNS = ("amount_minor", "currency", "minor_unit_exponent", "verbatim")
#: `as_of` was the FIFTH money column (#2782) and is RETIRED (#11827). It is
#: named here, not deleted from the record, for one reason: the migration that
#: ADDED it is landed history (append-only), so the migration-text pins below
#: must still account for five columns while the LIVE record has four.
MONEY_RETIRED_COLUMNS = ("as_of",)
MONEY_MIGRATION = "20261018000001_record_point_money_value_2782.sql"
RETIRE_MIGRATION = "20261110000001_record_point_retire_as_of_11827.sql"


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
    # The ADD file is landed history and still declares five columns; `as_of` is
    # retired by a LATER file (#11827), so it is expected here and must NOT be
    # in `MONEY_COLUMNS` (the live set).
    assert set(types) == set(MONEY_COLUMNS) | set(MONEY_RETIRED_COLUMNS), types


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
        f"the note declares {sorted(declared)}, the LIVE money columns are "
        f"{MONEY_COLUMNS} (the note must not re-list a retired column)"
    )
    # The note describes the LIVE record, so it is compared against the ADD file
    # minus the retired column — the ADD file itself is landed history and keeps
    # declaring all five (#11827).
    live = {k: v for k, v in actual.items() if k not in MONEY_RETIRED_COLUMNS}
    assert declared == live, (
        f"the note's column TYPES disagree with the migration: doc {declared} vs "
        f"SQL {live} — a reader following the note would look for a column the "
        "record does not have"
    )


def test_the_retired_as_of_column_is_dropped_by_a_forward_migration():
    """#11827: `public.point.as_of` is retired by a NEW, append-only migration.

    Two halves, both machine-checked: the ADD file is untouched (editing it is
    what the `check-migration-append-only` gate refuses), and the DROP reaches
    the record's schema read — otherwise `_split_properties` would keep placing
    a replayed legacy `asOf` into a column the database no longer has, and the
    record's insert would fail on the legacy rows it exists to carry.
    """
    forward = REPO_ROOT / "supabase" / "migrations" / RETIRE_MIGRATION
    assert forward.exists(), f"the retirement migration is gone: {forward}"
    assert forward in MIGRATIONS, (
        "the retirement migration must be READ, not merely on disk — an unread "
        "DROP COLUMN is invisible to this pin"
    )
    sql = forward.read_text(encoding="utf-8")
    assert re.search(
        r"ALTER\s+TABLE\s+public\.point[^;]*DROP\s+COLUMN\s+IF\s+EXISTS\s+as_of",
        sql, re.I | re.S), (
        "the retirement migration no longer drops point.as_of — read "
        f"{forward.name}"
    )
    columns = parse_sql_columns()["point"]
    for column in MONEY_COLUMNS:
        assert column in columns, column
    for column in MONEY_RETIRED_COLUMNS:
        assert column not in columns, (
            f"point.{column} is retired (#11827) but the schema read still "
            "reports it — the DROP COLUMN form is not read"
        )


def test_the_two_schema_readers_agree_on_every_record_table():
    """#11827 review P1: ONE column set, TWO readers, and they must agree.

    `parse_sql_columns()` here is the placement checker's independent parse, and
    `record_projection.table_columns()` is what the record's writer reads. They
    diverged the moment a DROP COLUMN entered the tree: this file applied "all
    adds, then all drops" while the projection read drops last over the WHOLE
    set, so BOTH readers dropped `node_identity.key_property` — a column
    `20261027000001_record_semantic_id_key.sql` re-adds a few characters after
    the `DROP COLUMN` in the SAME `ALTER TABLE` (it is `GENERATED ALWAYS`, so it
    cannot be altered in place). Measured then: `table_columns()` lacked the
    column while `column_types()` had it, and `_split_properties` moved
    `keyProperty` into the free-form `props` tail.

    Both readers are now position-ordered, and this pin is what keeps a future
    drop-and-re-add (or any other ordering subtlety) from making them disagree
    silently: an independent parse that answers differently from the writer's is
    a pin that cannot hold.
    """
    from tortoise.record_projection import column_types, table_columns

    independent = parse_sql_columns()
    real = table_columns()
    # ⚠️ `session_narrative` is deliberately outside this file's `MIGRATIONS`
    # (the `creates`-conditional tenth table), so this parse cannot speak about
    # it; and `embedding_vec` is a `public.vector(384)` column this file's
    # deliberately narrow type vocabulary does not model. Neither is the axis
    # this pin exists for.
    assert set(real) - set(independent) <= {"session_narrative"}, (
        sorted(set(real) - set(independent)))
    for table in sorted(independent):
        missing = independent[table] - real[table]
        assert not missing, (
            f"{table}: the placement checker reads columns the record writer "
            f"does not have — {sorted(missing)}. A column set that shrinks in "
            "one reader and not the other is the #11827 ordering defect "
            "(a DROP and a re-ADD in one `ALTER TABLE`)"
        )
    # …and the reader-internal invariant: a column with a DECLARED TYPE is a
    # column. This is exactly what broke — `column_types()` still carried
    # `node_identity.key_property` after `table_columns()` had dropped it.
    for table, declared in column_types().items():
        if table in real:
            assert set(declared) <= real[table], (
                f"{table}: declared types for columns the column set does not "
                f"have — {sorted(set(declared) - real[table])}")


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
