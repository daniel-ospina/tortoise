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
from dataclasses import dataclass
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
        # `#13220` — the FORWARD migration that gives `public.object` and
        # `public.subject` an `embedding` column + a GENERATED `embedding_vec` +
        # an HNSW index each (owner ruling 2026-11-17). ⛔ Leaving it out was a
        # FALSE PASS this pin's own review measured: `parse_sql_columns()` reads
        # only this tuple, so the two `("object"|"subject", "embedding")` rows in
        # the carve-out parametrisation below kept asserting the columns were
        # ABSENT while the migration created them — green, about a schema the
        # record no longer has. Append-only history means a column ARRIVES this
        # way, which is exactly the reason the ADD file above is listed.
        "20261117000001_record_object_subject_embedding.sql",
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


def declared_derived_column_exceptions() -> set[tuple[str, str]]:
    """`{(table, column)}` — DERIVED names a DECISION gave a column (`#13220`).

    Read from `record_projection.DERIVED_COLUMNS`, never re-listed here: that
    mapping is the ONE home of "a DERIVED value that has a column and still may
    not be carried by a payload", and a second copy in a test is how a declaration
    and the code it describes drift apart.

    ⛔ A DECLARATION, NOT A BLANKET EXEMPTION: the guard beside
    `test_derived_and_volatile_properties_have_no_column` asserts every name in
    here is genuinely DERIVED in the placement table AND genuinely a column, so a
    name added to the code side cannot quietly widen the carve-out, and one that
    stopped being a column reds rather than lingering.
    """
    from tortoise.record_projection import DERIVED_COLUMNS

    return {(table, col) for table, cols in DERIVED_COLUMNS.items() for col in cols}


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
            elif cls == "D" and col in cols and (
                    (table, col) not in declared_derived_column_exceptions()):
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
#: Same derivation for the three documents registered in `_COUNT_OF_DOCS`, which
#: state the denominator in the `<occurrences> of <N> migrations` form (see
#: `_INDEX_MIGRATION_COUNT_RE` for why it is no longer a literal `0`).
_MIGRATION_COUNT_OF_RE = re.compile(
    r"(?P<occurrences>\d+) of (?P<count>\d+)\s+migrations")
#: The three documents carrying the `N of M migrations` form — scanned WHOLE,
#: because the phrase is not confined to one row. ⛔ A FOURTH file matches the same
#: phrase and is deliberately NOT registered: `docs/epics/2026-11-04-7949-
#: extractor-v4-capstone/05-verify.md` quotes the form inside a historical
#: narrative about a past head, where it reads `1 of 82` — a denominator this tree
#: has moved past — so deriving it would RED for a record that is correct as
#: history. (Only THAT one is read there: the `1 of 83` beside it is not followed by
#: the word `migrations`, so this pattern never sees it — measured, not assumed.)
_COUNT_OF_DOCS = (
    REPO_ROOT / "docs" / "00_index.md",
    REPO_ROOT / "docs" / "architecture" / "SEARCH-ARCHITECTURE.md",
    #: The SIXTH home (#13315 review, P3): the storage picture restated the same
    #: denominator in a form no pattern reached — *"86 migrations, `pgvector` in
    #: 1"* — so the next migration would have left it stale while this suite
    #: stayed green. That is the recurrence of the failure this file's own
    #: docstring names ("a count is only pinned where a pattern reaches it"), so
    #: the row was restated in the pinned `<occurrences> of <N> migrations` form
    #: AND added here: a denominator with no reader is a false PASS waiting.
    REPO_ROOT / "docs" / "architecture" / "STORAGE-PICTURE-2026-11-17.md",
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
            f"{path.relative_to(REPO_ROOT)} no longer states `N of M migrations` "
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


def test_every_declared_derived_column_exception_is_REAL_and_still_DERIVED():
    """The guard that keeps the declared exception from becoming a loophole.

    Two directions, both measured against this tree: every exception names a
    column `parse_sql_columns()` can see (a stale entry would otherwise silently
    excuse a name that is not even there), and every exception names a property
    the placement table really classifies DERIVED (an entry for a PRIMARY or
    absent row would be excusing the wrong thing). Without this, adding a name to
    `record_projection.DERIVED_COLUMNS` would widen §17.5's carve-out with no
    test anywhere saying so.
    """
    exceptions = declared_derived_column_exceptions()
    assert exceptions == {("object", "embedding"), ("subject", "embedding")}, (
        "the declared exception is `#13220`'s two fingerprint columns and nothing "
        f"else — a third entry is a §17.5 reopen, not a test edit ({exceptions})")
    columns = parse_sql_columns()
    for table, col in sorted(exceptions):
        assert col in columns.get(table, set()), (
            f"{table}.{col} is declared as a columned DERIVED value but is not a "
            "column at this head — the declaration has outlived the schema")
    derived_pairs = {
        (SECTION_TABLE[section], normalize(name))
        for section, prop, cls in parse_placement()
        if cls == "D"
        for name in (expand_edge_property(prop) if section == "Edges" else [prop])
    }
    for table, col in sorted(exceptions):
        assert (table, col) in derived_pairs, (
            f"{table}.{col} is declared an exception to the §17.5 carve-out but "
            "the placement table does not classify it DERIVED")


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


#: `chk_<table>_props_no_derived CHECK (…)` bodies in DECLARATION ORDER. Same
#: shape as `contract_key_pins` above: `CHECK` must follow the constraint name,
#: so a `DROP CONSTRAINT IF EXISTS chk_…` and the prose that names the
#: constraint (migration headers do) both miss — only a real declaration is
#: captured.
#: The constraint HEADER, deliberately with NO body in the pattern.
#: ⛔ WHY THE BODY IS NOT CAPTURED IN THE PATTERN (measured false pass, review of
#: #13265 P2). The first form captured it inline — `CHECK\b(.{0,400})` with
#: `re.S` — and `finditer` resumes only after a WHOLE match, i.e. past those 400
#: characters, so a re-declaration starting inside the previous one's window was
#: never matched at all and the pin read the body it had already captured.
#:
#: ⛔ AND WHY THE BODY IS NOT A WINDOW EITHER (round 3 of the same review). Taking
#: "the text up to the next header, capped at 400" looked right and was not: the
#: closest pair of declarations in this tree — END of one declaration to the START
#: of the next, which is the distance a window has to clear — is MEASURED by
#: `test_the_snippet_cap_clears_every_declaration_gap`, and that test is the ONLY
#: place a number may live. ⛔ NOT HERE: this comment carried a hand-copied census
#: and it was WRONG WHEN IT WAS WRITTEN — not merely stale later, which is the
#: explanation it used to give. Measured at this PR's own base: the tree already
#: held 88 migrations and 9 declarations while the comment said 87, and the gap
#: figure it named matched the PRE-FIX scanner, not the shipped one (a line
#: comment is replaced by a space now, which moves every gap containing one). A
#: hand-copied census is a claim nothing
#: checks; that is the same defect as the one this block documents. The CONVENTION
#: is stated so the next reader does not "correct" one ruler with the other
#: (round 4 did): end→start, not start→start — they differ by the declaration's own
#: width — and the numbers live only in the test's own failure message.
#: A cap of
#: 400 characters bounded the "body" — a slice of whatever
#: followed the constraint. Substring-testing
#: that slice for `'embedding'` let MIGRATION PROSE satisfy the pin: a new
#: migration shrinking the ban to `ARRAY['status']` and then saying, in a comment
#: within the window, "the 'embedding' column is written by the projection" left
#: the pin GREEN — the same false pass, one layer in.
#:
#: ⇒ THE PIN READS THE REFUSED NAMES, NOT A TEXT WINDOW. The question these
#: assertions actually ask is "which property names does the in-force ban
#: refuse?", so the `ARRAY[…]` literal is parsed and its members are compared as
#: names. Prose cannot answer that question, and neither can truncation: a body
#: that refuses by some expression this pattern does not understand yields NO
#: names and fails the pin loudly instead of passing on a stray quote.
#:
#: ⛔ AND THE NAMES SEARCH IS ANCHORED TO THE CHECK'S OWN EXPRESSION (round 3 P1 of
#: the same review, still open at round 4 as N4). A bare `ARRAY[…]` search over
#: the span took the FIRST such literal ANYWHERE in it — and the LAST
#: declaration's span ran to EOF (tens of thousands of characters — its exact length
#: moves with every migration, and the test above measures it rather than this note
#: asserting it) — so a comment
#: citing the literals to explain the history (this repo's own header style, and a
#: comment is a WORSE answerer than prose because its `ARRAY['embedding']` is
#: well-formed) was read as the ban. Measured counter-example: an in-force ban
#: rewritten to `'{status}'::text[]` followed by `-- Was ARRAY['embedding'], now
#: ARRAY['status']` reported `{embedding}` over a ban that refuses only `status` —
#: a FALSE PASS, which is the direction this whole block exists to close. So the
#: search starts at the declaration's own `NOT (props ?|` and stops at that
#: statement's `;`: text outside the CHECK can no longer answer, and a CHECK
#: spelled in a form this pattern does not understand still yields NO names.
#: (Canonical read is `pg_get_constraintdef()` over `pg_constraint` — the live
#: `chk_*_props_no_derived` pgTAP arm, which this DB-free parse cannot replace;
#: PostgreSQL explicitly does not guarantee a CHECK reduces to a value list, which
#: is why an unreadable CHECK must fail LOUD here rather than guess.)
#: ⛔ QUOTED AND SCHEMA-QUALIFIED NAMES ARE LEGAL DDL and were each a false pass
#: (round 5 P1, measured): `DROP CONSTRAINT "chk_…"` was invisible (a removed ban
#: read as in force) and `ADD CONSTRAINT "chk_…" CHECK (…ARRAY['status'])` was
#: invisible too, so the read fell back to the SUPERSEDED body — exactly F8's
#: failure through a different vehicle. ⚠️ THE `(?:[\w"]+\s*\.\s*)?` BEFORE THE
#: NAME IS DEAD TOLERANCE, NOT DDL: PostgreSQL takes a bare `ColId` for a
#: CONSTRAINT name, so `CONSTRAINT public.chk_…` is a syntax error (round 9 P3 —
#: the comment used to claim PG "accepts" it). It is kept only so a hand-written
#: fixture cannot fall over, and no migration uses it.
_PROPS_BAN_PIN_HEADER_RE = re.compile(
    r"(?P<add>ADD\s+)?CONSTRAINT\s+(?:[\w\"]+\s*\.\s*)?(?P<quote>\"?)"
    r"(?P<name>chk_(?P<table>\w+)_props_no_derived)(?P=quote)\s*CHECK\b",
    re.I,
)
#: The STATEMENT a props-ban event must sit in. ⛔ NOT EVERY MATCH IS DDL: the
#: scanner KEEPS string and dollar-quoted literals (it must — `ARRAY['embedding']`
#: is a literal), so DDL-shaped text inside DATA matched these patterns and was
#: read as executed. Measured (round 6 P2, reproduced at 697e939386): a
#: `COMMENT ON TABLE public.subject IS $c$ was: ADD CONSTRAINT chk_subject_props_no_derived
#: CHECK (NOT (props ?| ARRAY['embedding','status'])) $c$` appended after a real
#: shrink to `{status}` left BOTH pins GREEN — a false pass, in the green direction.
#: So an event counts only when the text since the previous `;` opens a table
#: statement, and its target table must AGREE with the table in its name.
#: ⚠️ STATED RESIDUAL: DDL executed dynamically (`DO $$ … EXECUTE 'ALTER TABLE …' $$`)
#: is inside a literal and is therefore NOT read. That is today's corpus (measured:
#: the only `EXECUTE 'ALTER TABLE` statements touch triggers), and
#: `test_no_migration_executes_dynamic_DDL_for_a_props_ban` reds if one appears,
#: so the blind spot cannot be entered silently.
_PROPS_BAN_STMT_RE = re.compile(
    r"\s*(?:CREATE|ALTER)\s+TABLE\s+(?:ONLY\s+)?(?:IF\s+(?:NOT\s+)?EXISTS\s+)?"
    r"(?:([\w\"]+)\s*\.\s*)?([\w\"]+)",
    re.I,
)


def fold_identifier(token: str) -> str:
    """A PostgreSQL identifier, folded the way the server would.

    ⛔ AN UNQUOTED identifier folds to lower case; a QUOTED one keeps its exact
    case, so `public."Subject"` is a DIFFERENT table from `public.subject` and
    `"chk_Subject_props_no_derived"` a different constraint. Folding both alike
    made a ban created on the quoted sibling answer for ours — a GREEN false pass
    over a ban that had been dropped (round 10 P2, measured; the round-9 residual
    note claimed this direction failed closed, and that claim was false).
    """
    if token.startswith('"') and token.endswith('"') and len(token) > 1:
        return token[1:-1]
    return token.lower()

#: The CHECK's OWN expression. ⛔ ANCHORED AT THE START of the declaration's own
#: body (`CHECK` is followed immediately by it), not SEARCHED for later in the
#: text: an unanchored search could take a LATER, unrelated statement's
#: `NOT (props ?| ARRAY[…])` and report its names as this constraint's (round 5 of
#: review of #13265, measured: a `CHECK (NOT (props ? 'confidence'))` followed by a
#: sibling constraint's `?| ARRAY['embedding','status']` reported the sibling's
#: names). `(?:\(\s*)*` tolerates redundant parentheses; a body spelled any other
#: way yields NO names and fails the pins LOUDLY rather than guessing.
_PROPS_BAN_EXPR_RE = re.compile(r"\s*(?:\(\s*)*NOT\s*\(\s*props\s*\?\|", re.I)
#: The refused-name list: `NOT (props ?| ARRAY['embedding', 'status'])`. ⛔ MATCHED
#: at the operand position (`?|` takes its right-hand operand immediately), NEVER
#: searched for later in the statement: a sibling action in the same ALTER could
#: otherwise answer a CHECK whose own operand this pattern cannot read — the same
#: class as round 5's D, one level in. `props ?| some_ident` therefore yields NO
#: names and fails the pins loudly, which is the documented contract.
_PROPS_BAN_NAMES_RE = re.compile(r"\s*ARRAY\s*\[([^\]]*)\]", re.I)
#: ⛔ THE NAMES ARE THE **QUOTED** ENTRIES, so a cast cannot smuggle SQL type
#: tokens into a property-name set: `ARRAY['embedding'::text, 'status'::text]`
#: yields `{embedding, status}`, not `{embedding, status, text}` (round 5 P3).
_QUOTED_PROPERTY_RE = re.compile(r"'([^']*)'")
#: A `DROP CONSTRAINT` is an EVENT: a drop with no later re-declaration leaves NO
#: ban in force, and a declaration-only reader cannot see that (round 4 N3).
_PROPS_BAN_DROP_RE = re.compile(
    r"DROP\s+CONSTRAINT\s+(?:IF\s+EXISTS\s+)?(?:[\w\"]+\s*\.\s*)?(?P<quote>\"?)"
    r"(?P<name>chk_(?P<table>\w+)_props_no_derived)(?P=quote)",
    re.I,
)
#: A `RENAME CONSTRAINT` is an event too, and it does NOT re-declare: after it the
#: name the pins read no longer exists, so the in-force read must say so instead of
#: certifying the renamed body (round 5 P3).
_PROPS_BAN_RENAME_RE = re.compile(
    r"RENAME\s+CONSTRAINT\s+(?P<quote>\"?)(?P<name>chk_(?P<table>\w+)_props_no_derived)"
    r"(?P=quote)\s+TO\b",
    re.I,
)
#: Only for the diagnostic snippet attached to a finding; NOT read by any pin.
_PROPS_BAN_SNIPPET_CHARS = 400


@dataclass(frozen=True)
class PropsBanEvent:
    """One event in a table's props-ban history, at `position` in the source."""

    position: int
    kind: str                 # "declare" | "drop" | "rename"
    table: str
    is_redeclaration: bool
    names: frozenset[str]
    snippet: str
    declared_on: tuple[str, str] | None = None  # (schema, table) of its statement


def event_table(match: re.Match) -> str:
    """The table in a constraint NAME, folded per its quoting (`chk_<t>_…`).

    ⛔ THE WHOLE NAME DECIDES, not just its `<table>` segment: the pattern is
    case-insensitive, so a QUOTED `"CHK_subject_props_no_derived"` — a different
    constraint in PostgreSQL — used to fold onto ours and certify a ban that had
    been dropped (round 11 P3, measured: `('declare', {'status'})` after a real
    DROP). A quoted name yields its `<table>` only when it EQUALS the canonical
    spelling; otherwise it yields the raw name, which matches no table and is
    therefore invisible.
    """
    name, table, quote = match.group("name"), match.group("table"), match.group("quote")
    if not quote:
        return table.lower()
    return table if name == f"chk_{table}_props_no_derived" else name


def props_ban_events(text: str) -> list[PropsBanEvent]:
    """Every props-ban event (declaration, DROP or RENAME), in source order.

    ⛔ THE COMMENT SCANNER IS `record_projection._strip_sql_comments`, REUSED — not
    a second one here. A regex stripper is quote-unaware, so a `--` or `/*` inside
    a string literal deletes real DDL and a dropped ban reads as in force (round 5
    P1, measured); that module's scanner is quote-, dollar-quote- and escape-aware
    and its behaviour is pinned by `tests/test_11075_record_declared_type_contract.py`
    (a DIFFERENT `_strip_sql_comments` lives in `tools/cost_coverage_audit`, pinned by
    `test_cost_dimensions.py` — not this one). This pin imports the product's rather
    than growing a variant that can drift from it, and the NESTING case I added to
    that scanner is pinned there too.
    """
    clean = record_projection._strip_sql_comments(text)
    # ⛔ STATEMENT BOUNDARIES COME FROM THE SCANNER, NOT FROM `rfind(";")`. The
    # scanner KEEPS literals, so a `;` inside one is DATA: a `DEFAULT 'a;b'` before
    # a `CHECK (…)` made the constraint's own prefix start at `b',` and the whole
    # declaration vanished from the read (measured — it fell back to an older,
    # superseded body, which is F8 through a fourth vehicle). `_scan_sql` says
    # which characters are CODE, so a terminator inside a literal is not one and
    # an event inside a literal is not an event.
    code_spans_ = code_spans(clean)

    def is_code(position: int) -> bool:
        return any(start <= position < end for start, end in code_spans_)

    def declared_on(position: int) -> str | None:
        """The table this event's own STATEMENT targets, or None if it is not one."""
        prefix = statement_opening(clean, code_spans_, position)
        target = _PROPS_BAN_STMT_RE.match(prefix)
        if target is None:
            return None
        # ⛔ THE SCHEMA IS PART OF THE TARGET: `ALTER TABLE archive.subject …` is
        # not `public.subject`, and a constraint there constrains nothing here
        # (round 8 P3, measured — same class as round 6's H, one level out). An
        # unqualified name means `public`, which is where every record table lives.
        schema = fold_identifier(target.group(1)) if target.group(1) else "public"
        return (schema, fold_identifier(target.group(2)))

    headers = [
        m for m in _PROPS_BAN_PIN_HEADER_RE.finditer(clean)
        if is_code(m.start()) and declared_on(m.start()) is not None
    ]
    # ⛔ ONE FILTERED LIST, and the EVENTS are built from it. These used to be
    # built from a fresh `finditer` over `clean`, so the filter applied to the
    # span-bounding copy and NOT to the events: a `DROP CONSTRAINT` mentioned in a
    # dollar-quoted history note registered as a real drop (measured: a migration
    # that only documents "a DROP was tried and reverted" red the pin). Fail-closed
    # then, but the prose above said otherwise — and once the boundary read is
    # literal-aware this would be a false pass.
    others = [
        (m, "drop") for m in _PROPS_BAN_DROP_RE.finditer(clean)
        if is_code(m.start()) and declared_on(m.start()) is not None
    ] + [
        (m, "rename") for m in _PROPS_BAN_RENAME_RE.finditer(clean)
        if is_code(m.start()) and declared_on(m.start()) is not None
    ]
    events: list[PropsBanEvent] = []
    for index, match in enumerate(headers):
        # The declaration's span ends at the NEXT event of any kind, and its
        # expression at the first `;` — so neither a later constraint nor a later
        # DROP can supply this one's names.
        later = [m.start() for m in headers[index + 1:] if m.start() > match.start()]
        later += [m.start() for m, _kind in others if m.start() > match.start()]
        stop = min(later) if later else len(clean)
        span = clean[match.end():stop]
        # …and the cut is the first `;` that is CODE, so a `;` inside a literal
        # cannot truncate the CHECK's own body (`AND note <> 'x;y'` measured).
        statement = span
        for start, end in code_spans_:
            if end <= match.end():
                continue
            found = clean.find(";", max(start, match.end()), min(end, stop))
            if found != -1:
                statement = clean[match.end():found]
                break
        names: frozenset[str] = frozenset()
        anchor = _PROPS_BAN_EXPR_RE.match(statement)
        if anchor is not None:
            array = _PROPS_BAN_NAMES_RE.match(statement, anchor.end())
            if array is not None:
                names = frozenset(_QUOTED_PROPERTY_RE.findall(array.group(1)))
        events.append(PropsBanEvent(
            match.start(), "declare", event_table(match), bool(match.group("add")),
            names, span[:_PROPS_BAN_SNIPPET_CHARS], declared_on(match.start())))
    for match, kind in others:
        events.append(PropsBanEvent(
            match.start(), kind, event_table(match), False, frozenset(),
            match.group(0), declared_on(match.start())))
    events.sort(key=lambda event: event.position)
    return events


def props_ban_pins(text: str) -> dict[str, list[tuple[bool, frozenset[str], str]]]:
    """Every `props_no_derived` DECLARATION, per table, in declaration order.

    Each entry is `(is_redeclaration, refused_names, snippet)`:

    * `is_redeclaration` — True when the DDL form was `ADD CONSTRAINT …` (a
      FORWARD re-declaration, which under append-only history SUPERSEDES the
      in-`CREATE TABLE` one), False for a bare `CONSTRAINT … CHECK` inside a
      `CREATE TABLE` body;
    * `refused_names` — the property names this constraint's own `ARRAY[…]`
      literal names: the names this view REPORTS. ⚠️ The primary pin asserts on
      `props_ban_in_force`, not on this view, because this one is
      DECLARATION-ONLY and cannot see a DROP or RENAME (see its docstring). A text
      window is not used anywhere, because prose and truncation pollute one;
    * `snippet` — the declaration's following text, capped, for failure messages.

    ⛔ THIS VIEW OMITS DROPS AND RENAMES, which is why `props_ban_in_force`
    exists: a reader that asks "what is in force?" must see them.
    """
    pins: dict[str, list[tuple[bool, frozenset[str], str]]] = {}
    for event in props_ban_events(text):
        if event.kind == "declare":
            pins.setdefault(event.table, []).append(
                (event.is_redeclaration, event.names, event.snippet))
    return pins


def props_ban_in_force(text: str, table: str) -> tuple[str, frozenset[str]]:
    """What the database enforces on `table` today: `(kind, refused_names)`.

    `kind` is `"declare"` (a ban exists — `refused_names` is what it refuses),
    `"drop"` (the last event removed it: NOTHING is refused), `"rename"` (it
    exists under another name, so the name these pins read is gone), `"mismatch"`
    (the last event's statement targets a different table or schema, so nothing
    HERE is certified) or `"none"`.
    """
    events = [event for event in props_ban_events(text) if event.table == table]
    if not events:
        return ("none", frozenset())
    last = events[-1]
    if last.declared_on is not None and last.declared_on != ("public", table):
        # ⛔ A NAME IS NOT A TARGET: constraint names are per-table, so
        # `ALTER TABLE public.other ADD CONSTRAINT chk_subject_props_no_derived`
        # is legal and constrains NOTHING on `subject` while answering for it
        # (round 6 P3, measured). Loud, never silent.
        return ("mismatch", frozenset())
    return (last.kind, last.names)


def all_migration_text() -> str:
    """Every `supabase/migrations/*.sql` on disk, in filename (= chronological) order."""
    return "\n".join(
        p.read_text(encoding="utf-8") for p in sorted(_MIGRATIONS_DIR.glob("*.sql"))
    )


def code_spans(text: str) -> list[tuple[int, int]]:
    """The `(start, end)` spans of `text` that are CODE (not literal, not comment).

    From `record_projection._scan_sql` — the ONE scanner — because a `;` inside a
    literal is DATA, not a statement terminator (round 7 P1, measured).
    """
    return [
        (start, end)
        for start, end, kind in record_projection._scan_sql(text)
        if kind == "code"
    ]


def declaration_positions(text: str) -> list[tuple[int, int]]:
    """Every props-ban DECLARATION's `(start, end)`, notes and prose excluded.

    A `COMMENT ON …` statement that quotes a declaration is DATA: unfiltered, a
    history note made the snippet-cap census red with "the cap reaches into the
    NEXT ban" when there was no next ban, only a note (round 9 P3, measured).
    """
    clean = record_projection._strip_sql_comments(text)
    spans = code_spans(clean)
    found: list[tuple[int, int]] = []
    for match in _PROPS_BAN_PIN_HEADER_RE.finditer(clean):
        if not any(start <= match.start() < end for start, end in spans):
            continue
        # ⚠️ No `COMMENT ON` exclusion here, unlike the guard: a note's text is a
        # LITERAL, so the span test above already excludes it. An exclusion that
        # can never fire is dead code in a file whose subject is accuracy (round 9
        # measured it unreachable: removing it changes no result).
        found.append((match.start(), match.end()))
    return found


def statement_opening(text: str, spans: list[tuple[int, int]], position: int) -> str:
    """The text from the last CODE `;` before `position` up to it."""
    start = 0
    for span_start, span_end in spans:
        if span_start >= position:
            break
        found = text.rfind(";", span_start, min(span_end, position))
        if found != -1:
            start = max(start, found + 1)
    return text[start:position]


@pytest.mark.parametrize("table,col", [("object", "embedding"), ("subject", "embedding")])
def test_the_13220_embedding_columns_EXIST_and_their_props_ban_STANDS(table, col):
    """The ONE declared exception to the carve-out — asserted, not exempted.

    `#13220` / owner ruling 2026-11-17: these two labels gain the fingerprint
    column (it must be INDEXED in Postgres, impossible without storing it), which
    is why they were REMOVED from the "absent from the SQL" parametrisation above
    rather than left there going quietly green. Both halves are pinned here, in
    one place: the column EXISTS (**this is the assertion that would have caught
    the propagation defect** — the migration arrived while the pin still said the
    column could not), and the open property tail still REFUSES the name, so the
    column is the projection's to write and never a payload's.
    """
    columns = parse_sql_columns()
    assert col in columns.get(table, set()), (
        f"{table}.{col} is the #13220 fingerprint column and must be a record column")
    # ⛔ The assertion below is scoped to the LAST DDL re-declaration of the
    # constraint, not to the whole migration union. A union-wide `'embedding' in
    # text` was satisfied by HISTORY — `20261012000002_record_semantic.sql` and
    # `20261014000001_record_subject_props_no_derived.sql` both carry the string —
    # so it stayed green even if a later migration dropped and re-added
    # `chk_<table>_props_no_derived` WITHOUT the name. And a bare `rfind` on the
    # constraint NAME is equally wrong: migration headers cite it in PROSE
    # (this PR's own header does), so the name's last textual occurrence is not a
    # definition. Match the DDL form only — `ADD CONSTRAINT <name> CHECK` or a
    # bare `CONSTRAINT <name> CHECK` — and read the LAST one.
    # ⛔ AND COMPARE ITS REFUSED NAMES, NEVER A TEXT WINDOW. A window is satisfied
    # by migration PROSE: this pin passed over a ban shrunk to `ARRAY['status']`
    # once a comment mentioning 'embedding' sat within 400 characters of it
    # (review of #13265, round 3 — measured: with the text window in force the
    # shrunk ban read as GREEN; the name-based read reds it). `props_ban_pins`
    # parses the `ARRAY[…]` literal
    # for exactly this reason, and a body it cannot read yields NO names, so the
    # assertion fails loudly instead of passing on a stray quote.
    # ⛔ AND READ IT FROM THE WHOLE MIGRATIONS DIRECTORY, not this file's
    # `MIGRATIONS` tuple. That tuple is the read set `parse_sql_columns()` uses for
    # its COLUMN census, and it is hand-maintained — it does NOT list
    # `20261014000001_record_subject_props_no_derived.sql`, which holds the
    # IN-FORCE `chk_subject_props_no_derived` (`ARRAY['embedding','status']`). A
    # first cut of this assertion read the tuple and therefore read the STALE
    # Oct-12 declaration (`ARRAY['embedding']`) while reporting that it had read
    # "the LAST" one — a pin structurally unable to see the re-declaration it
    # exists to catch, since append-only history means every future re-declaration
    # is a NEW FILE too. The directory read is the same rule the sibling
    # `test_relation_is_never_a_bare_id` documents for the same reason.
    #
    # The read is `props_ban_in_force(all_migration_text(), table)` — ONE helper
    # family, shared with
    # `test_the_props_ban_pin_reads_the_IN_FORCE_redeclaration_not_the_create_table_one`
    # below, so the regex that defines "a declaration" has a single home rather
    # than a second copy that can drift from this one.
    kind, refused = props_ban_in_force(all_migration_text(), table)
    assert kind != "none", (
        f"chk_{table}_props_no_derived has no DDL definition anywhere under "
        f"{_MIGRATIONS_DIR.relative_to(REPO_ROOT)} — the props ban has no home")
    assert kind == "declare", (
        f"the IN-FORCE state of chk_{table}_props_no_derived is {kind!r}: the last "
        f"event is a DROP, so NOTHING is refused on {table} and {col!r} is open to "
        "a payload. A reader that only collects declarations cannot see this")
    assert col in refused, (
        f"the LAST re-declaration of chk_{table}_props_no_derived no longer names "
        f"{col!r} — a later migration re-opened the name even though the column "
        f"now exists. Names read: {sorted(refused)}")


def test_a_redeclaration_that_FOLLOWS_another_closely_is_STILL_READ():
    """A body capture that swallows the NEXT declaration is a false pass.

    ⛔ MEASURED (review of #13265 P2). With the body captured inside the pattern
    (`CHECK\b(.{0,400})`, `re.S`, greedy) `finditer` resumes past the whole
    match, so a declaration starting inside the previous one's 400-character
    window was never matched: on the text below the old pattern saw **0**
    `subject` declarations (both sat inside the `object` one's window) and the
    pin read whatever body it had already captured. With the third declaration
    SHRINKING the ban to `ARRAY['status']`, that made the pin certify a
    constraint the database no longer had — F8's failure one level down, where
    the READ SET is right and the PARSER drops it.

    TWO discriminators, because either alone is defeatable: the COUNT reds a
    regression that re-narrows the read to some fixed width (a declaration then
    hides inside its predecessor's slice, as it did under `CHECK\b(.{0,400})`),
    and the LAST NAME SET reds a read of the wrong declaration.
    """
    text = (
        "ALTER TABLE public.object ADD CONSTRAINT chk_object_props_no_derived\n"
        "  CHECK (NOT (props ?| ARRAY['embedding']));\n"
        "ALTER TABLE public.subject ADD CONSTRAINT chk_subject_props_no_derived\n"
        "  CHECK (NOT (props ?| ARRAY['embedding']));\n"
        "ALTER TABLE public.subject DROP CONSTRAINT IF EXISTS chk_subject_props_no_derived;\n"
        "ALTER TABLE public.subject ADD CONSTRAINT chk_subject_props_no_derived\n"
        "  CHECK (NOT (props ?| ARRAY['status']));\n")
    pins = props_ban_pins(text)
    assert set(pins) == {"object", "subject"}, (
        f"a declaration was swallowed by the one before it: {sorted(pins)}")
    assert len(pins["subject"]) == 2, (
        "only one chk_subject_props_no_derived was read although two are "
        f"declared — the parser is consuming the next header: {pins['subject']!r}")
    assert "embedding" in pins["subject"][0][1]
    assert "embedding" not in pins["subject"][-1][1], (
        "the LAST (in-force) declaration no longer refuses 'embedding' and this "
        f"read did not notice: {sorted(pins['subject'][-1][1])}")

    # ⛔ AND PROSE MUST NOT RESCUE IT (round 3 of the same review). A text-window
    # read passed this case: the shrunk CHECK followed by a comment that mentions
    # the name — which is what a real migration would plausibly say.
    prose = (
        text
        + "-- Only 'status' is refused from here on; the 'embedding' column is\n"
        + "-- written by the projection and never by a payload.\n")
    rescued = props_ban_pins(prose)["subject"][-1][1]
    assert "embedding" not in rescued, (
        "a comment mentioning 'embedding' after the shrunk CHECK satisfied the "
        f"pin — the read is still a text window: {sorted(rescued)}")


def test_the_props_ban_pin_reads_the_CHECKS_own_expression_not_the_tail():
    """An UNANCHORED `ARRAY[…]` search is answerable by text outside the CHECK.

    ⛔ MEASURED (review of #13265, round 3 P1, still open at round 4 as N4).
    `_PROPS_BAN_NAMES_RE.search(span)` took the FIRST `ARRAY[…]` anywhere in the
    span, and the LAST declaration's span ran to EOF (see
    `test_the_snippet_cap_clears_every_declaration_gap` for the measured length),
    so the documented promise — "a body that refuses by some expression
    this pattern does not understand yields NO names and fails the pin loudly
    instead of passing on a stray quote" — held only while nothing downstream of
    it contained an `ARRAY[…]`. That is exactly what a migration header does, and
    in a COMMENT it is worse than prose: `ARRAY['embedding']` is well-formed, so
    the names regex reads it as the ban.

    The text below is an in-force ban narrowed to a NON-`ARRAY` spelling and then
    explained in a comment. The ban refuses `status` ONLY; the comment names
    `embedding`. An unanchored read reports `{embedding}` — certifying a ban the
    database does not have, which is the false PASS this whole block exists to
    close. The anchored read sees no `ARRAY[…]` inside the CHECK's own expression
    and reports NO names, so the pin fails loudly.

    The second half is the over-narrowing guard: a literal that IS inside the
    expression must still be read, so "no names" cannot become the answer to
    every question.
    """
    text = (
        "ALTER TABLE public.subject ADD CONSTRAINT chk_subject_props_no_derived\n"
        "  CHECK (NOT (props ?| '{status}'::text[]));\n"
        "-- Was ARRAY['embedding'], now ARRAY['status']: the column is written by\n"
        "-- the projection and never by a payload.\n")
    names = props_ban_pins(text)["subject"][-1][1]
    assert "embedding" not in names, (
        "the ARRAY[…] search ran PAST the CHECK's own expression and read a "
        f"trailing comment as the ban: {sorted(names)}")

    shrunk = (
        "ALTER TABLE public.subject ADD CONSTRAINT chk_subject_props_no_derived\n"
        "  CHECK (NOT (props ?| ARRAY['status']::text[]));\n"
        "-- was ARRAY['embedding'] before this migration\n")
    assert props_ban_pins(shrunk)["subject"][-1][1] == frozenset({"status"}), (
        "the anchor over-narrowed: the literal INSIDE the CHECK's own expression "
        "must still be read")


    # ROUND 3 vehicle A — a comment carrying the OLD `ARRAY[…]`, INSIDE the CHECK.
    comment_array = (
        "ALTER TABLE public.subject ADD CONSTRAINT chk_subject_props_no_derived CHECK (\n"
        "  -- was: NOT (props ?| ARRAY['embedding', 'status']::text[])\n"
        "  NOT (props ?| ARRAY['status']::text[]));\n")
    kind, names = props_ban_in_force(comment_array, "subject")
    assert (kind, names) == ("declare", frozenset({"status"})), (
        f"a comment's ARRAY[...] was read as the refused names: {sorted(names)}")

    # ROUND 3 vehicle B — a comment between the constraint NAME and `CHECK`. Legal
    # SQL, and how a migration narrating a narrowing reads; it used to hide the
    # declaration entirely, so the in-force read fell back to an OLDER one.
    name_comment = (
        "ALTER TABLE public.subject ADD CONSTRAINT chk_subject_props_no_derived\n"
        "  -- narrowed: only status from here on\n"
        "  CHECK (NOT (props ?| ARRAY['status']::text[]));\n")
    kind, names = props_ban_in_force(name_comment, "subject")
    assert (kind, names) == ("declare", frozenset({"status"})), (
        "a comment between the constraint name and CHECK hid the declaration, so "
        f"the read fell back to nothing or to an older one: {(kind, sorted(names))}")

    # ROUND 3 vehicle C — a DROP with no later re-declaration leaves NO ban.
    dropped = (
        "ALTER TABLE public.subject ADD CONSTRAINT chk_subject_props_no_derived\n"
        "  CHECK (NOT (props ?| ARRAY['embedding', 'status']::text[]));\n"
        "ALTER TABLE public.subject DROP CONSTRAINT chk_subject_props_no_derived;\n")
    kind, names = props_ban_in_force(dropped, "subject")
    assert (kind, names) == ("drop", frozenset()), (
        "a DROP after the last declaration was not seen, so the read keeps "
        f"certifying a ban the database no longer has: {(kind, sorted(names))}")


def test_no_migration_executes_dynamic_DDL_for_a_props_ban():
    """The blind spot of the target check, made LOUD — in the shapes it can see.

    An event must sit in a `CREATE|ALTER TABLE` statement, so DDL executed
    DYNAMICALLY is not read. This test is the compensating control, and ⛔ IT WAS
    FALSE AS FIRST WRITTEN: it matched `EXECUTE '…'` only and measured SILENT on
    `EXECUTE format('… %I …')` and on the synthetic `EXECUTE $q$…$q$` and
    `EXECUTE 'a' || 'b'` shapes, while its own prose claimed the blind spot
    "cannot be entered silently". ⚠️ Measured (round 13): only the `format(…)`
    form is actually WRITTEN in `supabase/migrations` — 4 occurrences, incl.
    `20260915000001_tenancy_team_to_org.sql:184`
    (`EXECUTE format('ALTER TABLE %I RENAME CONSTRAINT %I TO %I', …)`); the other
    two appear only in `supabase/tests/` (a dollar-quoted `EXECUTE`) or not at all
    (`||`), so they are vehicles, not corpus precedent.

    Two controls, because a widened keyword match alone is either silent or
    over-broad (that tenancy statement names OTHER constraints, which is legal):

    1. no dynamic statement may mention a props-ban constraint at all — the
       spelling-independent rule, since the name is what makes it ours;
    2. every DDL-SHAPED MATCH (`…CONSTRAINT chk_<t>_props_no_derived`, on ADD,
       DROP or RENAME) in the comment-stripped text must be a parsed event, so a
       declaration the statement filter drops — for ANY reason, including a
       statement it never sees — cannot disappear silently. Counting it over
       LITERALS TOO is what catches the `DO $$ … $$` spelling; excluding
       `COMMENT ON` statements is what keeps a legal `COMMENT ON CONSTRAINT`
       (and a history note) from reading as a dropped event.

    ⚠️ STATED RESIDUAL: a name ASSEMBLED from parameters (`format('… ADD
    CONSTRAINT %I …', 'chk_' || t || '_props_no_derived')`) appears literally
    nowhere, and no text control can see it. Measured: no migration does this.
    """
    for path in sorted((REPO_ROOT / "supabase" / "migrations").glob("*.sql")):
        clean = record_projection._strip_sql_comments(path.read_text(encoding="utf-8"))
        for match in re.finditer(r"\bEXECUTE\b", clean, re.I):
            statement = clean[match.start():]
            end = statement.find(";")
            statement = statement if end == -1 else statement[:end]
            assert "props_no_derived" not in statement, (
                f"{path.name} executes a dynamic statement that names a props-ban "
                f"constraint ({statement[:120]!r}) — the props-ban parse cannot "
                "see it; teach the parse or record the gap")
    # Control 2: EVERY DDL-SHAPED MATCH must be a parsed event. ⛔ COUNTED OVER
    # THE COMMENT-STRIPPED TEXT WITH LITERALS KEPT — not over code spans. Code
    # spans would make DDL inside a `DO $$ … $$` body (a LITERAL, but EXECUTED)
    # invisible to the count, which is measured: the corpus already ships plain
    # `ALTER TABLE` inside DO bodies (`0005_waitlist_subscribers.sql:55`,
    # `20261022000001_record_session_capture_redactions_integer.sql:86`,
    # `20261102000002_record_point_search_keys_text.sql:166` — line numbers
    # re-measured after round 9 found the first set cited files shorter than the
    # line it named), and a props-ban
    # re-declaration placed there read the STALE event with both controls silent —
    # a green false pass in the guard that exists to prevent exactly that.
    #
    # A `COMMENT ON …` statement is excluded so a HISTORY NOTE in
    # `COMMENT ON TABLE … $c$ … $c$` does not read as a dropped event. The
    # exclusion is LOAD-BEARING for `COMMENT ON CONSTRAINT chk_… IS '…'` too
    # whenever the comment's own text quotes DDL — measured: a comment whose text
    # contains `ADD CONSTRAINT chk_… CHECK (…)` adds a second match, so without
    # the exclusion the count is 2 against 1 parsed event and legal SQL reds. (An
    # earlier version of this note claimed such a statement "matches none of the
    # three patterns", which is true only when its text quotes no DDL.)
    stripped = record_projection._strip_sql_comments(all_migration_text())
    spans = code_spans(stripped)
    hits = 0
    for match in list(_PROPS_BAN_PIN_HEADER_RE.finditer(stripped)) + [
        m for m, _kind in [
            (m, "drop") for m in _PROPS_BAN_DROP_RE.finditer(stripped)
        ] + [(m, "rename") for m in _PROPS_BAN_RENAME_RE.finditer(stripped)]
    ]:
        opening = statement_opening(stripped, spans, match.start())
        if re.match(r"\s*COMMENT\s+ON\b", opening, re.I):
            continue
        hits += 1
    parsed = len(props_ban_events(all_migration_text()))
    assert hits == parsed, (
        f"{hits} DDL-shaped match(es) of `props_no_derived` but {parsed} parsed "
        f"event(s) — a declaration, DROP or RENAME was dropped by the statement "
        f"filter (parsed: {sorted(f'{e.table}:{e.kind}' for e in props_ban_events(all_migration_text()))})")


def test_the_snippet_cap_clears_every_declaration_gap():
    """The 400-char snippet cap is only diagnostic if it cannot reach the next ban.

    ⛔ THIS TEST OWNS THE CENSUS. The block above used to state it as prose
    (a file count and a tightest gap), and both were WRONG WHEN WRITTEN (the tree
    already held 88 migrations at this PR's base, and the gap it named came from a
    DIFFERENT ruler — the pre-fix scanner) — a stale figure BESIDE a pinned one,
    which is the failure class this file
    documents at length. So the figures are derived here, at run time, and the
    failure message prints them; nothing in the file asserts a hand-copied number.
    """
    raw = all_migration_text()
    clean = record_projection._strip_sql_comments(raw)
    files = sorted((REPO_ROOT / "supabase" / "migrations").glob("*.sql"))
    # ⛔ FILTERED LIKE EVERY OTHER READ: a `COMMENT ON …` note quoting the
    # declaration is DATA, not a ban (round 9 P3, measured).
    declarations = declaration_positions(raw)
    assert len(declarations) >= 2, (
        f"the tree holds {len(declarations)} props_no_derived declaration(s) — the "
        "parse is not reading it")
    gaps = [
        declarations[i + 1][0] - declarations[i][1]
        for i in range(len(declarations) - 1)
    ]
    tail = len(clean) - declarations[-1][1]
    assert min(gaps) > _PROPS_BAN_SNIPPET_CHARS, (
        f"the snippet cap ({_PROPS_BAN_SNIPPET_CHARS}) reaches into the NEXT ban: "
        f"tightest END→START gap is {min(gaps)} over {len(files)} migrations / "
        f"{len(declarations)} declarations (gaps {gaps}, last tail {tail})")
    assert tail > _PROPS_BAN_SNIPPET_CHARS, (
        f"the last declaration's tail ({tail}) is shorter than the snippet cap "
        f"({_PROPS_BAN_SNIPPET_CHARS}) — the snippet would be a whole-file read")


def test_legal_sql_spellings_cannot_false_pass_the_props_ban():
    """Every spelling that has been a false pass, one vehicle each — all measured.

    The connective tissue is the same in every one: the pin answered from a place
    that was not "what does the database enforce on this table right now".

    * **A** a `--`/`/* */` INSIDE a string literal (a `COMMENT ON … IS 'TODO /* … */'`)
      deleted the DDL after it under a regex stripper, so a DROPped ban read in force.
      Fixed by reusing `record_projection._strip_sql_comments` (quote-, dollar-quote-
      and escape-aware) instead of a second, weaker stripper.
    * **B** PostgreSQL NESTS block comments; that scanner's `find("*/")` closed the
      outer comment at the inner `*/`, leaving a commented-out declaration as code.
      Fixed in the scanner itself — its first consumer is the product's column
      parser, so the defect was not this pin's alone.
    * **C** a QUOTED name (`DROP CONSTRAINT "chk_…"`) was invisible, so a removed
      ban read in force, and a quoted ADD fell back to a superseded body — F8
      through another vehicle. ⚠️ The SCHEMA-QUALIFIED spelling the regex also
      tolerates (`DROP CONSTRAINT public.chk_…`) is NOT legal DDL — PostgreSQL
      takes a bare `ColId` for a constraint name — so its vehicle pins a
      TOLERANCE, not a spelling a migration may use.
    * **D** the `?|` anchor was SEARCHED for in the span, so a sibling or later
      statement's `ARRAY[…]` supplied the names for a CHECK this pattern could not
      read. Fixed by anchoring at the CHECK's own body (`re.match`): an unreadable
      body now yields NO names and fails loudly.
    * **E** `RENAME CONSTRAINT` re-declares nothing; after it the name the pins read
      no longer exists. Fixed by making RENAME an event.
    * **F** a `::text` cast inside the literal leaked type tokens (`text`) into the
      property-name set. Fixed by taking the QUOTED entries only.

    Round 6 added two more, both in the GREEN direction (a false pass):

    * **G** DDL-shaped text inside DATA — `COMMENT ON … IS $c$ was: ADD CONSTRAINT … $c$`
      — was read as executed DDL, so a history note re-declared the ban the
      migration had just shrunk. An event now counts only when its own statement
      (text since the previous `;`) opens `CREATE|ALTER TABLE`, and its target
      table must agree with the table in its name.
    * **H** a constraint named `chk_subject_…` created ON ANOTHER TABLE
      (`ALTER TABLE public.other …`) answered for `subject`, which it does not
      constrain. Now `mismatch`, which is loud.

    Rounds 7–9 added three more (the same class, each measured):

    * **I** a `;` inside a LITERAL is not a statement terminator, so a
      `DEFAULT 'a;b'` before the constraint hid its declaration entirely (it fell
      back to a superseded body). Boundaries now come from `_scan_sql`'s `code`
      spans.
    * **J** a CHECK declaration inside a `DEFAULT $c$…$c$` literal was counted as
      its own declaration — asserted on the EVENT COUNT, because a phantom placed
      before the real one is masked by it.
    * **K** another SCHEMA (`ALTER TABLE archive.subject …`) answered for
      `public.subject`; targets are compared as `(schema, table)`.

    And round 9–10's two, which this list previously omitted while the body held
    them:

    * **L** an UNQUOTED UPPERCASE name (`DROP CONSTRAINT CHK_…`) is the same
      identifier in PostgreSQL and must not be invisible — and a QUOTED
      `public."Subject"` is a DIFFERENT one and must not be folded onto ours;
    * **M** a `COMMENT ON` note quoting a declaration is not a declaration, so the
      snippet-cap census must not count it (it made that test red with "reaches
      into the NEXT ban" where there was only a note).
    """
    # ROUND 5 vehicle A — a `--` inside a STRING LITERAL must not swallow the DDL
    # after it (that is why the read reuses the product's quote-aware scanner).
    # The `--` sits INSIDE a literal and the DROP shares its line, so a stripper
    # that is not quote-aware deletes the DROP: that is the only spelling that
    # changes the ANSWER rather than merely corrupting a neighbour.
    string_literal = (
        "ALTER TABLE public.subject ADD CONSTRAINT chk_subject_props_no_derived\n"
        "  CHECK (NOT (props ?| ARRAY['embedding', 'status']::text[]));\n"
        "INSERT INTO public.notes VALUES ('--'); "
        "ALTER TABLE public.subject DROP CONSTRAINT chk_subject_props_no_derived;\n")
    assert props_ban_in_force(string_literal, "subject")[0] == "drop", (
        "a `--` or `/*` inside a string literal swallowed real DDL, so a DROPPED "
        "ban still reads as in force")

    # ROUND 5 vehicle B — nested block comments: PostgreSQL nests them, so a
    # declaration inside an outer comment is NOT executed.
    # ⛔ ORDER MATTERS FOR THE PIN: the commented-out (SHRINKING) declaration is
    # placed LAST, so a scanner that leaves it in the text makes it the "in force"
    # answer. Put it first and the real declaration below it would mask the bug.
    nested = (
        "ALTER TABLE public.subject ADD CONSTRAINT chk_subject_props_no_derived\n"
        "  CHECK (NOT (props ?| ARRAY['embedding']));\n"
        "/* history: /* moved */ ALTER TABLE public.subject ADD CONSTRAINT\n"
        "     chk_subject_props_no_derived CHECK (NOT (props ?| ARRAY['status'])); */\n")
    kind, names = props_ban_in_force(nested, "subject")
    assert (kind, names) == ("declare", frozenset({"embedding"})), (
        "a commented-out declaration was read as executed (the shrink-to-status "
        f"inside the comment won): {(kind, sorted(names))}")

    # ROUND 5 vehicle C — a quoted / schema-qualified name on the DROP.
    for spelling in ('DROP CONSTRAINT "chk_subject_props_no_derived";',
                     "DROP CONSTRAINT public.chk_subject_props_no_derived;"):
        quoted = (
            "ALTER TABLE public.subject ADD CONSTRAINT chk_subject_props_no_derived\n"
            "  CHECK (NOT (props ?| ARRAY['embedding', 'status']::text[]));\n"
            f"ALTER TABLE public.subject {spelling}\n")
        assert props_ban_in_force(quoted, "subject")[0] == "drop", (
            f"a DROP spelled `{spelling}` was invisible, so a removed ban reads in force")

    # …and the same spelling on a SHRINKING re-declaration.
    quoted_add = (
        'ALTER TABLE public.subject ADD CONSTRAINT "chk_subject_props_no_derived"\n'
        "  CHECK (NOT (props ?| ARRAY['status']::text[]));\n")
    kind, names = props_ban_in_force(quoted_add, "subject")
    assert (kind, names) == ("declare", frozenset({"status"})), (
        f"a quoted ADD was invisible, so an older body answered instead: {(kind, sorted(names))}")

    # ROUND 5 vehicle D — the anchor must be THIS declaration's own expression, not
    # a later sibling's `?|` list in the same or a following statement.
    # ⛔ ONE STATEMENT (a multi-action ALTER, which PostgreSQL accepts): with the
    # sibling in a second statement the `;` bound alone would hide it, and the
    # vehicle would not discriminate the anchor at all.
    sibling = (
        "ALTER TABLE public.subject\n"
        "  ADD CONSTRAINT chk_subject_props_no_derived CHECK (NOT (props ? 'confidence')),\n"
        "  ADD CONSTRAINT chk_subject_props_guard CHECK (NOT (props ?| ARRAY['embedding', 'status']::text[]));\n")
    kind, names = props_ban_in_force(sibling, "subject")
    assert (kind, names) == ("declare", frozenset()), (
        "an unreadable CHECK borrowed a LATER statement's ARRAY[…] list instead of "
        f"failing closed: {(kind, sorted(names))}")

    # …AND the same statement's sibling must not answer when OUR operand is
    # unreadable (`?| some_ident`, not an ARRAY). Fail-closed is the contract.
    unreadable_operand = (
        "ALTER TABLE public.subject\n"
        "  ADD CONSTRAINT chk_subject_props_no_derived CHECK (NOT (props ?| excluded)),\n"
        "  ADD CONSTRAINT chk_subject_props_guard CHECK (NOT (props ?| ARRAY['embedding', 'status']));\n")
    kind, names = props_ban_in_force(unreadable_operand, "subject")
    assert names == frozenset(), (
        "an unreadable operand borrowed the sibling's ARRAY[…] in the SAME "
        f"statement: {sorted(names)}")

    # ROUND 5 vehicle E — `RENAME CONSTRAINT` does not re-declare: after it the name
    # these pins read no longer exists.
    renamed = (
        "ALTER TABLE public.subject ADD CONSTRAINT chk_subject_props_no_derived\n"
        "  CHECK (NOT (props ?| ARRAY['embedding', 'status']::text[]));\n"
        "ALTER TABLE public.subject RENAME CONSTRAINT chk_subject_props_no_derived\n"
        "  TO chk_subject_props_no_derived_v2;\n")
    assert props_ban_in_force(renamed, "subject")[0] == "rename", (
        "a RENAME CONSTRAINT was not seen, so the pin certifies a name that no "
        "longer exists")

    # ROUND 5 vehicle F — a cast inside the literal must not become a property name.
    cast = (
        "ALTER TABLE public.subject ADD CONSTRAINT chk_subject_props_no_derived\n"
        "  CHECK (NOT (props ?| ARRAY['embedding'::text, 'status'::text]));\n")
    kind, names = props_ban_in_force(cast, "subject")
    assert names == frozenset({"embedding", "status"}), (
        f"a ::text cast leaked SQL type tokens into the property-name set: {sorted(names)}")




    # ROUND 10 vehicle — a QUOTED identifier is NOT folded: `public."Subject"` is a
    # different table, so a ban there must not answer for `public.subject`.
    quoted_sibling = (
        "ALTER TABLE public.subject ADD CONSTRAINT chk_subject_props_no_derived\n"
        "  CHECK (NOT (props ?| ARRAY['embedding', 'status']::text[]));\n"
        "ALTER TABLE public.subject DROP CONSTRAINT chk_subject_props_no_derived;\n"
        "ALTER TABLE public.\"Subject\" ADD CONSTRAINT chk_subject_props_no_derived\n"
        "  CHECK (NOT (props ?| ARRAY['embedding', 'status']::text[]));\n")
    assert props_ban_in_force(quoted_sibling, "subject")[0] == "mismatch", (
        "a ban on the QUOTED sibling table `public.\"Subject\"` certified the "
        "unquoted `public.subject`, whose ban had been dropped")
    # …while a QUOTED LOWERCASE identifier IS the same one.
    quoted_same = (
        "ALTER TABLE public.\"subject\" ADD CONSTRAINT chk_subject_props_no_derived\n"
        "  CHECK (NOT (props ?| ARRAY['status']::text[]));\n")
    assert props_ban_in_force(quoted_same, "subject") == ("declare", frozenset({"status"})), (
        "a quoted LOWERCASE identifier was treated as a different table")

    # …and the CONSTRAINT NAME folds by the same rule: a quoted capitalised name
    # is a different constraint, so its event belongs to table `Subject`.
    named_quoted = (
        'ALTER TABLE public.subject ADD CONSTRAINT "chk_Subject_props_no_derived"\n'
        "  CHECK (NOT (props ?| ARRAY['status']::text[]));\n")
    assert {event.table for event in props_ban_events(named_quoted)} == {"Subject"}, (
        "a QUOTED constraint name was folded to lower case, so a differently-named "
        "constraint was read as the `subject` one")

    # ROUND 13 vehicle — a DOUBLE-QUOTED IDENTIFIER is data: a `--` inside one must
    # not delete the rest of its line (that hid a real DROP from BOTH the parse and
    # the guard's count, i.e. silent green — measured before the scanner skipped
    # them).
    for hostile_ident in ("a--b", "a;b", "a'b", "a/*b"):
        # ⛔ THE DROP IS ON THE SAME LINE as the identifier, deliberately: a `--`
        # comment stops at the newline, so a next-line DROP is invisible to the
        # bug and the vehicle would stay green for `--` (round 14 P3, measured —
        # a regression limited to `--` alone would have shipped).
        quoted_ident = (
            "ALTER TABLE public.subject ADD CONSTRAINT chk_subject_props_no_derived\n"
            "  CHECK (NOT (props ?| ARRAY['embedding', 'status']::text[]));\n"
            "ALTER TABLE public.subject RENAME COLUMN \"" + hostile_ident + "\" TO c; "
            "ALTER TABLE public.subject DROP CONSTRAINT chk_subject_props_no_derived;\n")
        assert props_ban_in_force(quoted_ident, "subject")[0] == "drop", (
            f"a double-quoted identifier containing {hostile_ident!r} swallowed the "
            "real DROP after it")

    # ROUND 11 vehicle — the `E` of a preceding WORD is not an escape prefix, so
    # `LIKE'C:\'` must not swallow the rest of the file (it hid a real DROP).
    word_then_literal = (
        "ALTER TABLE public.subject ADD CONSTRAINT chk_subject_props_no_derived\n"
        "  CHECK (NOT (props ?| ARRAY['embedding', 'status']::text[]));\n"
        "SELECT 1 FROM n WHERE x LIKE'C:\\';\n"
        "ALTER TABLE public.subject DROP CONSTRAINT chk_subject_props_no_derived;\n")
    assert props_ban_in_force(word_then_literal, "subject")[0] == "drop", (
        "the `E` of `LIKE` armed backslash-escapes, the literal swallowed the file "
        "and a real DROP became invisible")

    # …and the WHOLE quoted name decides: `"CHK_subject_props_no_derived"` is a
    # different constraint, so its ADD must not certify the ban we dropped.
    quoted_other_name = (
        "ALTER TABLE public.subject ADD CONSTRAINT chk_subject_props_no_derived\n"
        "  CHECK (NOT (props ?| ARRAY['embedding', 'status']::text[]));\n"
        "ALTER TABLE public.subject DROP CONSTRAINT chk_subject_props_no_derived;\n"
        'ALTER TABLE public.subject ADD CONSTRAINT "CHK_subject_props_no_derived"\n'
        "  CHECK (NOT (props ?| ARRAY['status']::text[]));\n")
    assert props_ban_in_force(quoted_other_name, "subject")[0] == "drop", (
        'a quoted "CHK_…" (a DIFFERENT constraint) certified the ban we dropped')

    # ROUND 10 vehicle — a BLOCK COMMENT is WHITESPACE in PostgreSQL, so deleting
    # it merged `DROP` with `CONSTRAINT` and a real drop became invisible. This is
    # the pin for the scanner's single-space substitution (round 10 measured that
    # the fix had NO committed pin: reverting it left all six suites green).
    block_separated = (
        "ALTER TABLE public.subject ADD CONSTRAINT chk_subject_props_no_derived\n"
        "  CHECK (NOT (props ?| ARRAY['embedding', 'status']::text[]));\n"
        "ALTER TABLE public.subject DROP/* retired */CONSTRAINT chk_subject_props_no_derived;\n")
    assert props_ban_in_force(block_separated, "subject")[0] == "drop", (
        "a block comment BETWEEN `DROP` and `CONSTRAINT` hid the drop — the "
        "comment is whitespace, so removing it must leave a separator")

    # ROUND 9 vehicle — an UNQUOTED UPPERCASE name is the same identifier in
    # PostgreSQL, so it must not be invisible: `DROP CONSTRAINT CHK_…` read as a
    # stale `declare` (green over a dropped ban) before the fold.
    upper = (
        "ALTER TABLE public.subject ADD CONSTRAINT chk_subject_props_no_derived\n"
        "  CHECK (NOT (props ?| ARRAY['embedding', 'status']::text[]));\n"
        "ALTER TABLE public.subject DROP CONSTRAINT CHK_SUBJECT_PROPS_NO_DERIVED;\n")
    assert props_ban_in_force(upper, "subject")[0] == "drop", (
        "an unquoted UPPERCASE constraint name was invisible — PostgreSQL folds it "
        "to the same identifier")
    upper_rename = (
        "ALTER TABLE public.subject ADD CONSTRAINT chk_subject_props_no_derived\n"
        "  CHECK (NOT (props ?| ARRAY['embedding', 'status']::text[]));\n"
        "ALTER TABLE public.subject RENAME CONSTRAINT CHK_SUBJECT_PROPS_NO_DERIVED\n"
        "  TO chk_subject_props_no_derived_v2;\n")
    assert props_ban_in_force(upper_rename, "subject")[0] == "rename", (
        "an unquoted UPPERCASE name on RENAME was invisible")

    upper_add = (
        "ALTER TABLE public.subject ADD CONSTRAINT chk_subject_props_no_derived\n"
        "  CHECK (NOT (props ?| ARRAY['embedding', 'status']::text[]));\n"
        "ALTER TABLE public.subject ADD CONSTRAINT CHK_SUBJECT_PROPS_NO_DERIVED\n"
        "  CHECK (NOT (props ?| ARRAY['status']::text[]));\n")
    assert props_ban_in_force(upper_add, "subject")[1] == frozenset({"status"}), (
        "an unquoted UPPERCASE re-declaration was invisible, so the SUPERSEDED "
        "body answered for it")

    # …and a COMMENT ON note that quotes a declaration is not a declaration, so
    # the census must not count it (round 9 P3: it made the cap test red with
    # "reaches into the NEXT ban" where there was no next ban).
    noted = (
        "ALTER TABLE public.subject ADD CONSTRAINT chk_subject_props_no_derived\n"
        "  CHECK (NOT (props ?| ARRAY['embedding', 'status']::text[]));\n"
        "COMMENT ON TABLE public.subject IS $c$ was: ADD CONSTRAINT\n"
        "  chk_subject_props_no_derived CHECK (NOT (props ?| ARRAY['embedding'])) $c$;\n")
    assert len(declaration_positions(noted)) == 1, (
        "a note quoting a declaration was counted as a second declaration: "
        f"{len(declaration_positions(noted))}")

    # ROUND 8 vehicle — a declaration inside a LITERAL of a real ALTER TABLE
    # statement is DATA (a default value), not a second declaration: only the
    # statement's own constraint counts.
    literal_declaration = (
        "ALTER TABLE public.subject ADD COLUMN note text DEFAULT $c$ADD CONSTRAINT\n"
        "  chk_subject_props_no_derived CHECK (NOT (props ?| ARRAY['embedding','status']))$c$;\n"
        "ALTER TABLE public.subject ADD CONSTRAINT chk_subject_props_no_derived\n"
        "  CHECK (NOT (props ?| ARRAY['status']::text[]));\n")
    # ⛔ ASSERTED ON THE EVENT LIST, NOT ON THE WINNER: a phantom that comes
    # BEFORE the real declaration is masked by it, so an "in force" assertion
    # passes while the parse is wrong (the same ordering trap as vehicle B).
    events_here = [e for e in props_ban_events(literal_declaration) if e.table == "subject"]
    assert len(events_here) == 1, (
        "a CHECK declaration inside a `DEFAULT $c$…$c$` literal was counted as a "
        f"declaration of its own: {[(e.kind, sorted(e.names)) for e in events_here]}")

    # …and a `DROP` inside a dollar-quoted note is a NOTE, not a drop.
    note_drop = (
        "ALTER TABLE public.subject ADD CONSTRAINT chk_subject_props_no_derived\n"
        "  CHECK (NOT (props ?| ARRAY['embedding', 'status']::text[]));\n"
        "COMMENT ON TABLE public.subject IS $c$ a DROP CONSTRAINT\n"
        "  chk_subject_props_no_derived was tried and reverted $c$;\n")
    assert props_ban_in_force(note_drop, "subject")[0] == "declare", (
        "a DROP mentioned in a comment note registered as a real drop")

    # ROUND 8 vehicle — ANOTHER SCHEMA is not `public`: a same-named constraint
    # on `archive.subject` says nothing about the record table.
    other_schema = (
        "ALTER TABLE public.subject ADD CONSTRAINT chk_subject_props_no_derived\n"
        "  CHECK (NOT (props ?| ARRAY['embedding', 'status']::text[]));\n"
        "ALTER TABLE public.subject DROP CONSTRAINT chk_subject_props_no_derived;\n"
        "ALTER TABLE archive.subject ADD CONSTRAINT chk_subject_props_no_derived\n"
        "  CHECK (NOT (props ?| ARRAY['embedding', 'status']::text[]));\n")
    kind, names = props_ban_in_force(other_schema, "subject")
    assert kind == "mismatch", (
        "a constraint on ANOTHER SCHEMA answered for `public.subject`: "
        f"{(kind, sorted(names))}")

    # ROUND 7 vehicle — a `;` inside a LITERAL is not a statement terminator, so a
    # `DEFAULT 'a;b'` earlier in the same CREATE TABLE must not hide the constraint.
    semicolon_in_literal = (
        "CREATE TABLE public.subject (\n"
        "  id bigint,\n"
        "  note text DEFAULT 'a;b',\n"
        "  CONSTRAINT chk_subject_props_no_derived CHECK (NOT (props ?| ARRAY['embedding','status']))\n"
        ");\n")
    kind, names = props_ban_in_force(semicolon_in_literal, "subject")
    assert (kind, names) == ("declare", frozenset({"embedding", "status"})), (
        "a `;` inside a string literal was read as a statement terminator and hid "
        f"the declaration: {(kind, sorted(names))}")

    # ROUND 6 vehicle G — DDL-shaped text inside DATA is not executed DDL.
    history_note = (
        "ALTER TABLE public.subject DROP CONSTRAINT chk_subject_props_no_derived;\n"
        "ALTER TABLE public.subject ADD CONSTRAINT chk_subject_props_no_derived\n"
        "  CHECK (NOT (props ?| ARRAY['status']::text[]));\n"
        "COMMENT ON TABLE public.subject IS $c$ was: ADD CONSTRAINT\n"
        "  chk_subject_props_no_derived CHECK (NOT (props ?| ARRAY['embedding','status'])) $c$;\n")
    kind, names = props_ban_in_force(history_note, "subject")
    assert (kind, names) == ("declare", frozenset({"status"})), (
        "a COMMENT's dollar-quoted history note was read as executed DDL and "
        f"re-declared the ban it merely describes: {(kind, sorted(names))}")

    # ROUND 6 vehicle H — the NAME is not the TARGET: a same-named constraint on
    # another table constrains nothing here, so answering for it is a lie.
    wrong_table = (
        "ALTER TABLE public.subject ADD CONSTRAINT chk_subject_props_no_derived\n"
        "  CHECK (NOT (props ?| ARRAY['embedding', 'status']::text[]));\n"
        "ALTER TABLE public.other ADD CONSTRAINT chk_subject_props_no_derived\n"
        "  CHECK (NOT (props ?| ARRAY['embedding', 'status']::text[]));\n")
    kind, names = props_ban_in_force(wrong_table, "subject")
    assert kind == "mismatch", (
        "a constraint on ANOTHER table answered for `subject`: "
        f"{(kind, sorted(names))}")


def test_the_props_ban_pin_reads_the_IN_FORCE_redeclaration_not_the_create_table_one():
    """F8's own non-vacuity pin: the LAST declaration read is the one in force.

    `chk_subject_props_no_derived` is declared TWICE — inside
    `20261012000002_record_semantic.sql`'s `CREATE TABLE` (`ARRAY['embedding']`)
    and again, RE-DECLARED to add `status`, by
    `20261014000001_record_subject_props_no_derived.sql`'s
    `DROP CONSTRAINT IF EXISTS … ADD CONSTRAINT …` (`ARRAY['embedding',
    'status']`). Under append-only history the Oct-14 one is in force, so a pin
    that reads the Oct-12 one is certifying a constraint the database no longer
    has — which is precisely F8. TWO independent discriminators, because either
    alone is defeatable: the DDL FORM (`ADD CONSTRAINT` = a forward re-declaration;
    the Oct-12 one is a bare `CONSTRAINT … CHECK` inside `CREATE TABLE`) reddens on
    the pre-fix read set, and the NAME SET (the in-force body refuses BOTH
    `embedding` and `status`) reddens on the review's mutant, which shrinks the ban
    to `ARRAY['status']` — a body that still contains the string `'status'` and so
    would pass a status-only check.
    """
    # ⛔ `props_ban_in_force`, NOT `props_ban_pins(...)[-1]`: the latter omits DROPS
    # and RENAMES, so this pin — whose whole subject is "which declaration is in
    # force" — was still drop-blind (round 5 P3, measured: a trailing
    # `DROP CONSTRAINT` left it GREEN while `props_ban_in_force` said `drop`).
    kind, names = props_ban_in_force(all_migration_text(), "subject")
    assert kind == "declare", (
        f"the in-force state of chk_subject_props_no_derived is {kind!r}, not a "
        "declaration — the ban this pin certifies does not exist under that name")
    pins = props_ban_pins(all_migration_text())
    is_redecl, _, snippet = pins["subject"][-1]
    assert is_redecl, (
        "the LAST chk_subject_props_no_derived is an in-`CREATE TABLE` declaration "
        "— the Oct-14 FORWARD re-declaration is the one in force, so the pin is "
        f"reading the wrong definition again: {snippet!r}")
    assert {"embedding", "status"} <= names, (
        "the LAST chk_subject_props_no_derived does not refuse BOTH names — the "
        "in-force Oct-14 re-declaration refuses `embedding` and `status`, and the "
        "Oct-12 one it superseded refused only `embedding`. Either the pin is "
        "reading the superseded definition again or the ban was shrunk: "
        f"names read {sorted(names)}, declaration starts {snippet!r}")
    # …and the subject ban has TWO declarations to choose between, which is why
    # `[-1]` had to stop meaning "the Oct-12 one". Object stays single-declared.
    assert len(pins["subject"]) >= 2 and len(pins["object"]) >= 1


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
    # own guard in `test_9231`, this one does not). ⛔ THE CLAIM THAT THE DIRECTORY
    # READ IS FREE IS ASSERTED BELOW, NOT NARRATED HERE. This comment used to state
    # a hand-copied census ("over all 79 files … `source 1 · event 1 · session 1 ·
    # object 2 · subject 2 · point 1`") — the file count rotted with the next
    # migration, and round 6's re-measure compared the figures against the
    # **props-ban** pin family rather than this one, which is the same
    # hand-copied-census failure it was reporting. An equality assertion cannot
    # rot and cannot cite the wrong family.
    # The LAST declaration wins (append-only history; the same rule
    # `key_properties()` reads), and the files are read in filename order — which
    # IS chronological, because the name is the migration timestamp.
    all_text = "\n".join(
        p.read_text(encoding="utf-8")
        for p in sorted((REPO_ROOT / "supabase" / "migrations").glob("*.sql"))
    )
    assert contract_key_pins(all_text) == contract_key_pins(
        "\n".join(p.read_text(encoding="utf-8") for p in MIGRATIONS)), (
        "reading the whole migrations DIRECTORY and reading the hand-maintained "
        "`MIGRATIONS` list now give different node_key pin sets — the directory "
        "read is no longer free, and which list is authoritative is a decision")
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
    # it; `record_cursor` (#12894) is outside it for a stronger reason — it is NOT
    # a node table at all: it carries no graph property mapped to a column (it is
    # the ORDINAL COUNTER, keyed `(org_id, graph_id)`), so there is no placement
    # for this checker to decide and an entry for it would be an empty map that
    # asserts nothing; and `embedding_vec` is a `public.vector(384)` column this
    # file's deliberately narrow type vocabulary does not model. None of the three
    # is the axis this pin exists for — the readers must agree on every table the
    # parse CAN speak about, and a table it cannot speak about must be NAMED here
    # rather than silently widening the tolerated difference.
    assert set(real) - set(independent) <= {"session_narrative", "record_cursor"}, (
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
