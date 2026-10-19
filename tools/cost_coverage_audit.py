#!/usr/bin/env python3
"""#5045 — the cost-coverage audit: is dimension X measured? (a query, not a grep)

Two verbs against ONE declaration (``tortoise/cost_dimensions.py``):

``query <dimension>``
    Answer the substrate's own question. Prints the declared state, unit,
    attribution, read path and every declared limit. Exit 0 when the dimension
    is measured or an allocation, 3 when it is declared OUT OF SCOPE (a real
    answer, with its reason — not "nobody"), 2 when no such dimension is
    declared (so a caller can never read a typo as "not measured").

``matrix``
    Print the coverage matrix and RE-VERIFY every declaration against the live
    code and the tracked tree. This is the part that makes the registry an
    instrument rather than a comment:

    1. **bindings** — every declared writer/reader resolves and is callable;
       every declared ``metric`` resolves to a live prometheus child whose family
       name and label set are EXACTLY the declared ones (a metric that gained or
       lost a label, or was renamed, is a series change, so it is drift, not
       detail).
    2. **ledger** — every declared column and SQL RPC exists in the EFFECTIVE schema
       the migrations leave behind: the corpus is replayed in order, so
       ``CREATE TABLE`` bodies, ``ALTER TABLE … ADD COLUMN``, ``RENAME COLUMN``,
       ``DROP COLUMN``, ``RENAME TO``, ``DROP TABLE``, ``CREATE FUNCTION``,
       ``ALTER FUNCTION … RENAME TO`` and ``DROP FUNCTION`` all move the end state
       (every command in a multi-command ``ALTER TABLE`` included, not only the
       first). The ``COLUMN`` keyword is OPTIONAL in all three column verbs —
       ``DROP [COLUMN] c``, ``ADD [COLUMN] c <type>``, ``RENAME [COLUMN] c TO d`` —
       and the short spelling moves the end state too; a command that touches no
       column (``ADD [CONSTRAINT|PRIMARY KEY|FOREIGN KEY|UNIQUE|CHECK|EXCLUDE|LIKE]``,
       ``DROP CONSTRAINT``, ``ALTER [COLUMN] c …``, RLS / trigger / rule toggles,
       ``OWNER TO``, ``SET``/``RESET``, ``CLUSTER ON``, ``REPLICA IDENTITY``,
       inheritance, partitions) is consumed DELIBERATELY, so a constraint can never
       be read as a column. A column renamed in an appended migration and not in the
       declaration
       fails here, and so does an RPC renamed by ``ALTER FUNCTION … RENAME TO`` or
       dropped by ``DROP FUNCTION``, a multi-target ``DROP TABLE a, b`` takes
       BOTH tables' columns with it and a multi-target ``DROP FUNCTION f(a), g(b)``
       takes both functions. Nothing is matched as a substring or inside a
       SQL comment — the strip is quote-aware, so a ``--`` inside a string literal
       cannot swallow the DDL after it — and an input the replay cannot lex
       (unterminated literal / body / block comment, unreadable ``DROP TABLE`` or
       ``DROP FUNCTION`` target, an ``ALTER TABLE`` command that is neither a column
       verb nor a known non-column one) is reported as a FINDING, never skipped.
    3. **no dead hook** — a dimension's writer (or reader, when the read path is
       the primitive) must have a call site in a tracked NON-TEST, NON-TOOL file,
       or the declaration must carry ``unscheduled_reason``. #4493's defect was a
       metric that registered, exported and tested while never being incremented in
       production: "wired and tested" is not "measured", and a developer
       instrument that mentions the writer is not a production call site either.
    4. **no undeclared dimension** — every org-attributed prometheus child in
       ``tortoise/monitoring.py`` must be declared, and no OTHER tracked module
       may construct a prometheus metric without declaring it (detected from the
       callee's IMPORT provenance — package- and submodule-level, aliased or
       bare — so an aliased constructor, a ``name=`` keyword construction and
       ``from prometheus_client.metrics import Counter`` are all visible).
       ``tests/``, ``tools/``, ``battery/``, ``graph-scripts/`` and ``docs/`` are
       excluded as developer/eval surfaces, so the claim is about PRODUCTION
       modules. A seventh counter added in a hurry is the failure mode #5045
       exists to end.
    5. **cardinality** — each declared cap/overflow attribute resolves to a live
       int/str, on BOTH axes (the per-org cap/overflow and the route-class
       ``path_caps``/``path_overflow`` that carries its own ``__unrouted__``
       sentinel).
    6. **states** — every row's ``state`` is in the declared vocabulary, so a typo
       cannot fall into none of measured / allocation / out-of-scope and report a
       clean matrix.
    7. **corpus** — every tracked module the scans read can actually be PARSED. An
       unparseable file contributes no names and no metric facts, so reporting it
       is the difference between a gate that ran and a gate that looked like it
       did.

Exit 0 clean, 1 on ANY drift (naming each finding), 2 on a usage error.

⛔ The audit never fabricates: an unresolvable declaration is reported as
drift, never skipped with a warning.

Usage:
    uv run python tools/cost_coverage_audit.py matrix [--root .] [--json]
    uv run python tools/cost_coverage_audit.py query egress
"""

from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter BEFORE the imports below — a module-level
# 3.11+-only import (`from datetime import UTC`) would fail first (D9 shape), and
# the failure would be unattributed. This tool's documented invocation is
# `uv run python tools/cost_coverage_audit.py …`; the ambient `python3` may be
# older, and a guard here turns an ImportError into a named refusal.
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/cost_coverage_audit.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/cost_coverage_audit.py`"
    )

import argparse
import ast
import json
import re
import subprocess
from dataclasses import dataclass
from functools import cache
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tortoise import cost_dimensions as cd  # noqa: E402

#: Where the prometheus metrics of this repo are declared. The drift gate is
#: scoped to it because it IS the declared metric surface: ``monitoring.py``
#: owns the substrate's children, and a metric built anywhere else is a second,
#: undeclared surface.
METRIC_MODULE = "tortoise.monitoring"

#: Tracked path prefixes that are not production call sites. ``tests/`` is the
#: point of the check (a writer only tests reach is the #4493 dead hook); the
#: worktree path is a full second copy of the tree and must never be scanned;
#: ``docs/`` holds no code; ``tools/`` is a set of developer instruments
#: (#4493/#5045 review P2: a script that mentions a writer is not a production
#: call site — accepting one is how "registered, exported and tested while never
#: incremented in production" reads as measured), and ``battery/`` and
#: ``graph-scripts/`` are an eval harness and historical one-off scripts that are
#: not the product's cost path (a metric they build would not be a production
#: measurement — if one ever does become one, it belongs in
#: ``tortoise/monitoring.py`` and this list is wrong, which the reviewer of that
#: change should catch here).
_NOT_PRODUCTION = (
    "tests/", ".worktrees/", "docs/", "tools/", "battery/", "graph-scripts/"
)

_MIGRATIONS = "supabase/migrations"


def _repo_root(root: Path | None) -> Path:
    return Path(root).resolve() if root else _ROOT


def tracked_python(root: Path) -> list[str]:
    """The tracked ``.py`` corpus (``git ls-files``), or a walk as a fallback."""
    return list(_tracked_python(Path(root).resolve()))


@cache
def _tracked_python(root: Path) -> tuple[str, ...]:
    """Cached corpus: the audit is called once per check and the tree is static."""
    try:
        out = subprocess.run(
            ["git", "ls-files", "*.py"], cwd=root, capture_output=True,
            text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return tuple(str(p.relative_to(root)) for p in root.rglob("*.py"))
    return tuple(line for line in out.splitlines() if line.endswith(".py"))


def migration_text(root: Path) -> str:
    """Every tracked migration's SQL, concatenated (the DDL is the authority)."""
    return _migration_text(Path(root).resolve())


@cache
def _migration_text(root: Path) -> str:
    migdir = root / _MIGRATIONS
    if not migdir.is_dir():
        return ""
    return "\n".join(p.read_text(encoding="utf-8", errors="replace")
                     for p in sorted(migdir.glob("*.sql")))


# ── 1. bindings ──────────────────────────────────────────────────────────


def check_bindings() -> list[str]:
    """Every declared binding resolves; metric family AND labels match.

    A series is identified by its emitted family name plus its label set, so both
    halves are compared against the LIVE child: ``_name`` (what a scrape and every
    dashboard/alert selects on) and ``_labelnames``. Comparing only the labels
    left a family rename — the same series change in the other axis — invisible,
    and gating the family half on ``if binding.family`` made an UNDECLARED family
    invisible too: a ``METRIC`` binding that names no family says nothing about
    the series it claims, so it is drift, not a skip. An incomplete declaration
    must not buy a clean matrix.

    ``Binding.kind`` is checked against the declared vocabulary FIRST, and it is
    the one field that silently escaped every other check: ``writers()`` /
    ``readers()`` / ``metrics()`` are ``b.kind == <const>`` filters, so a typo'd
    kind matched none of them — this check skipped it (not ``METRIC``, and a real
    function is callable), ``check_dead_hooks`` fell through to the dimension's
    reader, and ``unbacked_dimensions()`` saw a non-empty tuple so the summary's
    figure stayed 0. The vocabulary is declared in ``cd.binding_kinds()``.
    """
    findings: list[str] = []
    for dim in cd.dimensions():
        for binding in dim.bindings:
            if binding.kind not in cd.binding_kinds():
                findings.append(
                    f"{dim.name}: binding {binding.dotted} has undeclared kind "
                    f"{binding.kind!r} (declared: "
                    f"{', '.join(cd.binding_kinds())})")
            target = _safe_resolve(binding, findings)
            if target is None:
                continue
            if binding.kind == cd.METRIC:
                family = getattr(target, "_name", None)
                if not binding.family:
                    findings.append(
                        f"{dim.name}: metric {binding.dotted} declares no family — "
                        "a series is identified by its emitted family name plus its "
                        f"labels, and this one emits {family!r}; declare family=")
                elif family != binding.family:
                    findings.append(
                        f"{dim.name}: metric {binding.dotted} emits family "
                        f"{family!r}, declared {binding.family!r}")
                labels = tuple(getattr(target, "_labelnames", ()) or ())
                if labels != binding.labels:
                    findings.append(
                        f"{dim.name}: metric {binding.dotted} carries labels "
                        f"{labels!r}, declared {binding.labels!r}")
            elif not callable(target):
                findings.append(
                    f"{dim.name}: {binding.kind} {binding.dotted} is not callable")
    return findings


def _safe_resolve(binding: cd.Binding, findings: list[str]):
    try:
        return cd.resolve(binding)
    except Exception as exc:  # noqa: BLE001, RUF100 — an unresolvable declaration IS drift
        findings.append(f"declared binding does not resolve: {binding.dotted} ({exc!r})")
        return None


# ── 2. ledger columns + RPCs ─────────────────────────────────────────────


def check_ledger(root: Path) -> list[str]:
    """Every declared column and SQL RPC exists in the migrations' END STATE.

    Both halves are read off the EFFECTIVE schema (``_effective_schema``): the
    corpus is replayed in order and every definition is applied as DDL —
    ``CREATE TABLE`` bodies, ``ALTER TABLE … ADD COLUMN``, ``RENAME COLUMN``,
    ``DROP COLUMN``, ``CREATE FUNCTION``, ``ALTER FUNCTION … RENAME TO``,
    ``DROP FUNCTION`` — so the answer is *"does this symbol exist when the
    migrations finish"*, not *"was this word ever written in DDL position"*.

    The distinction is the whole check: this repo expresses a rename as a NEW,
    APPENDED migration (``20260915000001_tenancy_team_to_org.sql``: "Earlier
    migrations keep their historical `team` spelling … this migration renames the
    end state"), so a history-only predicate stays green forever on the stale
    spelling. Nothing is matched as a bare substring, nothing inside a SQL comment
    (the migrations quote column and function names in their own header
    comments), and a ``GRANT`` / ``REVOKE`` / ``COMMENT ON`` line names a symbol
    without defining it — prose that creates nothing is never a definition.
    """
    sql = migration_text(root)
    if not sql:
        return [f"no migrations found under {_MIGRATIONS}/ — cannot verify the ledger"]
    schema = _effective_schema(sql)
    # An input the replay could not read is DRIFT, not a pass: a corpus the lexer
    # gave up on is a corpus whose statements were never checked.
    findings: list[str] = [f"migrations cannot be replayed: {problem}"
                           for problem in schema.problems]
    for dim in cd.dimensions():
        for column in dim.columns:
            if not schema.has_column(column):
                findings.append(
                    f"{dim.name}: declared column {column!r} is not created by any "
                    "migration (DDL position)")
        for rpc in dim.rpcs:
            if not schema.has_function(rpc):
                findings.append(
                    f"{dim.name}: declared SQL function {rpc!r} is not defined by "
                    "any migration (no CREATE FUNCTION)")
    return findings


@dataclass(frozen=True)
class _Lexed:
    """*sql* reduced to CODE (comments gone, literals kept) plus its PROBLEMS."""

    code: str
    problems: tuple[str, ...] = ()


@cache
def _lex_sql(sql: str) -> _Lexed:
    """Split *sql* into code plus literals, removing SQL comments only in CODE.

    The predicates below match DDL, and the migrations carry long header comments
    that quote the very statements they describe (measured:
    ``20260925000002_oauth_redemption_state.sql:100`` says "each `ADD COLUMN
    code_id` takes ACCESS EXCLUSIVE"). Matching the raw text therefore accepts a
    comment as a definition; stripping first is what makes the "DDL position"
    claim true.

    The strip is QUOTE-AWARE, because a regex is not: ``--`` inside a
    single-quoted literal (``COMMENT ON TABLE public.x IS 'a -- b'``) is DATA. A
    ``re.sub(r"--[^\n]*")`` deleted the literal's closing quote along with the
    rest of the line, leaving an unterminated quote that made ``_statements`` run
    to the NEXT quote and emit one statement matching no DDL regex — measured: a
    ``DROP FUNCTION`` after such a literal read as still defined, and a ``RENAME
    COLUMN`` after one read as never applied (fail-OPEN in both directions), and
    a column that IS created read as drift when the literal came first (false
    RED). Dollar-quoted bodies and double-quoted identifiers are opaque for the
    same reason, and Postgres NESTS ``/* */``, so the block sweep counts depth —
    the old DOTALL sweep ran to the first ``*/`` far away and deleted real DDL
    (``graph_storage_mb``'s ``ALTER TABLE … ADD COLUMN`` vanished).

    Input that cannot be lexed (unterminated literal, identifier, dollar body or
    block comment) comes back as a PROBLEM, never as a silent truncation: this
    module's rule is that an unresolvable input is drift, never skipped.
    """
    out: list[str] = []
    problems: list[str] = []
    i, n = 0, len(sql)

    def line_of(index: int) -> int:
        return sql.count("\n", 0, index) + 1

    while i < n:
        ch = sql[i]
        if ch == "'":  # single-quoted literal — data, never comment
            j, closed = i + 1, False
            while j < n:
                if sql[j] == "'":
                    if sql[j + 1:j + 2] == "'":  # doubled quote inside the literal
                        j += 2
                        continue
                    closed = True
                    break
                j += 1
            if not closed:
                problems.append(
                    f"unterminated single-quoted literal at line {line_of(i)}")
                out.append(sql[i:])
                i = n
                break
            out.append(sql[i:j + 1])
            i = j + 1
            continue
        if ch == '"':  # quoted identifier — a name, not a comment
            j = sql.find('"', i + 1)
            if j == -1:
                problems.append(f"unterminated quoted identifier at line {line_of(i)}")
                out.append(sql[i:])
                i = n
                break
            out.append(sql[i:j + 1])
            i = j + 1
            continue
        if ch == "$":  # dollar-quoted body — procedural code, not schema
            tag = re.match(r"\$([A-Za-z_][A-Za-z0-9_]*)?\$", sql[i:])
            if tag:
                opener = tag.group(0)
                end = sql.find(opener, i + len(opener))
                if end == -1:
                    problems.append(
                        f"unterminated dollar-quoted body at line {line_of(i)}")
                    out.append(sql[i:])
                    i = n
                    break
                out.append(sql[i:end + len(opener)])
                i = end + len(opener)
                continue
        if sql.startswith("--", i):  # line comment
            end = sql.find("\n", i)
            i = n if end == -1 else end
            continue
        if sql.startswith("/*", i):  # block comment (Postgres nests them)
            depth, j = 0, i
            while j < n:
                if sql.startswith("/*", j):
                    depth, j = depth + 1, j + 2
                    continue
                if sql.startswith("*/", j):
                    depth, j = depth - 1, j + 2
                    if depth == 0:
                        break
                    continue
                j += 1
            if depth != 0:
                problems.append(f"unterminated block comment at line {line_of(i)}")
                i = n
                break
            out.append(" ")  # keep adjacent tokens apart
            i = j
            continue
        out.append(ch)
        i += 1
    return _Lexed("".join(out), tuple(problems))


def _strip_sql_comments(sql: str) -> str:
    """*sql* with comments removed and literals preserved (see ``_lex_sql``)."""
    return _lex_sql(sql).code


#: A column DEFINITION, not a mention: a name/type pair at the head of a
#: ``CREATE TABLE`` body line, or ``ADD COLUMN [IF NOT EXISTS] <name>``.
_COLUMN_TYPES = (
    "text", "bigint", "integer", "int", "smallint", "real", "numeric",
    "double precision", "boolean", "timestamptz", "timestamp", "date", "uuid",
    "jsonb", "json",
)

_IDENT = r"[A-Za-z_][A-Za-z0-9_$]*"

_CREATE_TABLE = re.compile(
    rf"^\s*CREATE\s+(?:UNLOGGED\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?"
    rf"(?:public\.)?({_IDENT})\s*\(", re.IGNORECASE)
_ALTER_TABLE = re.compile(
    rf"^\s*ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?(?:ONLY\s+)?(?:public\.)?"
    rf"({_IDENT})\s+(.*)$", re.IGNORECASE | re.DOTALL)
#: EVERY command a single ``ALTER TABLE`` statement can carry that MOVES the column
#: set, matched against ONE command (``_effective_schema`` splits the statement at
#: top-level commas first). One statement may list several (``DROP COLUMN a, ADD
#: COLUMN b, RENAME COLUMN c TO d``); applying only the FIRST one left the rest of
#: the statement invisible, which is the same stale-declaration fail-open the
#: replay exists to close, one command further in.
#:
#: Postgres makes the ``COLUMN`` keyword OPTIONAL in all three column verbs —
#: ``DROP [COLUMN] [IF EXISTS] c``, ``ADD [COLUMN] [IF NOT EXISTS] c <type>``,
#: ``RENAME [COLUMN] c TO d`` — so requiring it matched NO alternative for a
#: valid spelling and ``_effective_schema`` silently applied nothing: no state
#: change AND no ``problems`` entry, the one thing this module's contract forbids
#: ("an input the replay cannot read … is reported as a FINDING, never skipped").
#: The short form is therefore accepted, and ``_ALTER_TABLE_NOT_COLUMN`` is tried
#: FIRST so a table constraint can never be read as a column: ``ADD PRIMARY KEY
#: (org_id)`` creates no column named ``primary``, and ``DROP CONSTRAINT c`` drops
#: no column named ``c``. Alternatives are ordered so ``RENAME [COLUMN] c TO d``
#: wins over the shorter ``RENAME TO`` table rename.
_ALTER_TABLE_VERB = re.compile(
    rf"RENAME\s+(?:COLUMN\s+)?({_IDENT})\s+TO\s+({_IDENT})"
    rf"|RENAME\s+TO\s+({_IDENT})"
    rf"|DROP\s+(?:COLUMN\s+)?(?:IF\s+EXISTS\s+)?({_IDENT})"
    rf"|ADD\s+(?:COLUMN\s+)?(?:IF\s+NOT\s+EXISTS\s+)?({_IDENT})",
    re.IGNORECASE)
#: An ``ALTER TABLE`` command the replay reads as leaving the COLUMN SET untouched,
#: so consuming it is a decision and not a skip: table constraints (named or not),
#: attribute changes on an EXISTING column, constraint validation, RLS / trigger /
#: rule toggles, ownership, storage parameters, clustering, replica identity,
#: inheritance and partitions. Every command that matches NEITHER this nor
#: ``_ALTER_TABLE_VERB`` is reported in ``_Schema.problems`` — the reason this arm
#: exists is that 68 of the corpus's 133 ``ALTER TABLE`` statements are this kind,
#: and a blanket "no column verb ⇒ problem" would have reddened the live gate.
#: Each constraint keyword ends at a ``\b``, because the short ``ADD <name> <type>``
#: form makes them prefixes of real column names: ``ADD likes integer`` must stay a
#: column, not be eaten as ``ADD LIKE``.
_ALTER_TABLE_NOT_COLUMN = re.compile(
    r"(?:ADD\s+CONSTRAINT\b"
    rf"|ADD\s+(?:PRIMARY\s+KEY|FOREIGN\s+KEY|UNIQUE|CHECK|EXCLUDE|LIKE)\b"
    r"|DROP\s+CONSTRAINT\b"
    rf"|ALTER\s+(?:COLUMN\s+)?{_IDENT}"
    r"|VALIDATE\s+CONSTRAINT\b"
    r"|(?:ENABLE|DISABLE)\s+(?:(?:REPLICA|ALWAYS)\s+)?"
    r"(?:ROW\s+LEVEL\s+SECURITY|TRIGGER|RULE)"
    r"|(?:NO\s+)?FORCE\s+ROW\s+LEVEL\s+SECURITY\b"
    r"|OWNER\s+TO\b"
    r"|(?:SET|RESET)\b"
    r"|CLUSTER\s+ON\b"
    r"|REPLICA\s+IDENTITY\b"
    r"|(?:NO\s+)?INHERIT\b"
    r"|(?:ATTACH|DETACH)\s+PARTITION\b)", re.IGNORECASE)
#: ``DROP TABLE`` takes a TARGET LIST (``DROP TABLE IF EXISTS a, b`` drops both), so
#: the whole list is captured and split; the single-identifier form matched only
#: the first table and left every later table's columns reading as present.
_DROP_TABLE = re.compile(
    r"^\s*DROP\s+TABLE\s+(?:IF\s+EXISTS\s+)?(.*)$", re.IGNORECASE | re.DOTALL)
_CREATE_FUNCTION = re.compile(
    rf"^\s*CREATE\s+(?:OR\s+REPLACE\s+)?FUNCTION\s+(?:public\.)?({_IDENT})\s*\(",
    re.IGNORECASE)
_ALTER_FUNCTION_RENAME = re.compile(
    rf"^\s*ALTER\s+FUNCTION\s+(?:public\.)?({_IDENT})\s*(?:\([^)]*\))?\s*"
    rf"RENAME\s+TO\s+({_IDENT})", re.IGNORECASE | re.DOTALL)
#: ``DROP FUNCTION`` takes a TARGET LIST too (``DROP FUNCTION IF EXISTS f(a),
#: g(b)`` drops both, Postgres' grammar is ``name [, ...] [CASCADE|RESTRICT]``), so
#: the whole list is captured and split; the single-identifier form dropped only
#: the first function and left every later one reading as still defined.
_DROP_FUNCTION = re.compile(
    r"^\s*DROP\s+FUNCTION\s+(?:IF\s+EXISTS\s+)?(.*)$", re.IGNORECASE | re.DOTALL)


@dataclass
class _Schema:
    """The schema the migration corpus LEAVES BEHIND (the effective end state).

    ``columns`` maps a table to the column names it ends with; ``functions`` is
    the end-state set of ``public`` function names. Both are built by replaying
    the corpus in order, so a symbol renamed or dropped by a LATER, APPENDED
    migration is gone — which is exactly how this repo expresses a rename.

    Declarations name a column without its table and a function without its
    signature, so the lookups are name-shaped too: a column exists when ANY table
    ends with it, a function when its name survives to the end. A ``DROP
    FUNCTION`` is signature-specific in Postgres while this replay is name-shaped,
    so a name dropped and later re-created is present (the order in the corpus is
    drop-then-create); a name dropped LAST is drift, which is the answer the
    ledger needs.

    Every verb moves the end state, not just the first one in a statement: a
    single ``ALTER TABLE`` may carry several commands, and a ``DROP TABLE`` takes
    the columns it held with it — EVERY table of a multi-target ``DROP TABLE a,
    b``, not only the first. ``DROP FUNCTION`` takes a target list too, so every
    function of ``DROP FUNCTION f(a), g(b)`` is removed, not only the first.
    Missing any of these left a column or function that the schema no longer has
    reading as present — the stale-declaration fail-open.

    ``problems`` carries what the replay could NOT read (an unlexable corpus, an
    unreadable ``DROP TABLE`` or ``DROP FUNCTION`` target, an ``ALTER TABLE``
    command that is neither a column verb nor a known non-column verb).
    ``check_ledger`` reports each as drift, so a statement the replay skipped is
    never a statement that passed. "Known non-column" is a DECISION
    (``_ALTER_TABLE_NOT_COLUMN``), not a fallback: an unrecognised command is a
    problem, so a future verb cannot go silent.
    """

    columns: dict[str, set[str]]
    functions: set[str]
    problems: tuple[str, ...] = ()

    def has_column(self, column: str) -> bool:
        wanted = column.strip('"').lower()
        return any(wanted in cols for cols in self.columns.values())

    def has_function(self, rpc: str) -> bool:
        return rpc.strip('"').lower() in self.functions


@cache
def _effective_schema(sql: str) -> _Schema:
    """Replay *sql* statement by statement into its end state (see ``_Schema``)."""
    columns: dict[str, set[str]] = {}
    functions: set[str] = set()
    lexed = _lex_sql(sql)
    problems: list[str] = list(lexed.problems)
    for statement in _statements(lexed.code):
        create_table = _CREATE_TABLE.match(statement)
        if create_table:
            table = _table_name(create_table.group(1))
            cols = columns.setdefault(table, set())
            cols.update(_create_table_columns(statement, create_table.end() - 1))
            continue
        create_function = _CREATE_FUNCTION.match(statement)
        if create_function:
            functions.add(_table_name(create_function.group(1)))
            continue
        drop_function = _DROP_FUNCTION.match(statement)
        if drop_function:
            targets, unreadable = _drop_function_targets(drop_function.group(1))
            for target in targets:
                functions.discard(target)
            problems.extend(unreadable)
            continue
        alter_function = _ALTER_FUNCTION_RENAME.match(statement)
        if alter_function:
            functions.discard(_table_name(alter_function.group(1)))
            functions.add(_table_name(alter_function.group(2)))
            continue
        drop_table = _DROP_TABLE.match(statement)
        if drop_table:
            targets, unreadable = _drop_table_targets(drop_table.group(1))
            for target in targets:
                columns.pop(target, None)
            problems.extend(unreadable)
            continue
        alter_table = _ALTER_TABLE.match(statement)
        if not alter_table:
            continue
        table = _table_name(alter_table.group(1))
        cols = columns.setdefault(table, set())
        for raw_command in _split_top_level(alter_table.group(2)):
            command = raw_command.strip()
            if not command:
                continue
            # A command that touches no column is CONSUMED deliberately, before the
            # optional-``COLUMN`` verb scan can read a table constraint as a column
            # (``ADD PRIMARY KEY (org_id)`` names no column ``primary``).
            if _ALTER_TABLE_NOT_COLUMN.match(command):
                continue
            verb = _ALTER_TABLE_VERB.match(command)
            if not verb:
                # Neither a column verb nor a known non-column one: the replay did
                # NOT read it, so it is a problem — an ALTER TABLE that matches no
                # alternative used to apply nothing and report nothing, leaving a
                # stale declaration green about a column the schema no longer has.
                problems.append(
                    f"ALTER TABLE {table}: command {command!r} cannot be read — "
                    "the columns it changes are not tracked")
                continue
            renamed_from, renamed_to, new_table, dropped, added = verb.groups()
            if renamed_from:
                cols.discard(_table_name(renamed_from))
                cols.add(_table_name(renamed_to))
            elif new_table:
                moved = columns.pop(table, set())
                table = _table_name(new_table)
                cols = columns.setdefault(table, set())
                cols.update(moved)
            elif dropped:
                cols.discard(_table_name(dropped))
            else:
                cols.add(_table_name(added))
    return _Schema(columns, functions, tuple(dict.fromkeys(problems)))


def _drop_table_targets(raw: str) -> tuple[list[str], list[str]]:
    """Every table a ``DROP TABLE`` names, plus the targets the replay cannot read.

    ``DROP TABLE`` takes a comma-separated target list and Postgres drops all of
    them in one statement, so popping only the FIRST left every later table's
    columns reading as present for the rest of the replay — the same
    stale-declaration fail-open the replay closes for append-only migrations, one
    target further in. A target this replay cannot read is a PROBLEM, never a
    silent half-drop.
    """
    targets: list[str] = []
    problems: list[str] = []
    for part in _split_top_level(raw):
        token = re.sub(r"\b(?:CASCADE|RESTRICT)\s*$", "", part.strip(), flags=re.IGNORECASE)
        token = re.sub(r"^public\.", "", token.strip(), flags=re.IGNORECASE)
        if re.fullmatch(_IDENT, token):
            targets.append(_table_name(token))
        elif token:
            problems.append(
                f"DROP TABLE target {part.strip()!r} cannot be read — the tables it "
                "names are not tracked")
    return targets, problems


def _drop_function_targets(raw: str) -> tuple[list[str], list[str]]:
    """Every function a ``DROP FUNCTION`` names, plus the targets it cannot read.

    ``DROP FUNCTION`` takes a comma-separated target list and Postgres drops all of
    them in one statement, so discarding only the FIRST left every later function
    reading as still defined for the rest of the replay — the same
    stale-declaration fail-open the sibling ``DROP TABLE`` replay closes one verb
    over (``_drop_table_targets``), where the drop-then-recreate pattern is the
    repo's live idiom. A target this replay cannot read is a PROBLEM, never a
    silent half-drop.

    Each target carries its SIGNATURE (``public.f_one(text, integer)``), so the
    argument list is stripped after the split — ``_split_top_level`` keeps a
    multi-argument signature in one part, and the replay is name-shaped (see
    ``_Schema``).
    """
    targets: list[str] = []
    problems: list[str] = []
    for part in _split_top_level(raw):
        token = re.sub(r"\b(?:CASCADE|RESTRICT)\s*$", "", part.strip(), flags=re.IGNORECASE)
        token = re.sub(r"^public\.", "", token.strip(), flags=re.IGNORECASE)
        named = re.fullmatch(rf"({_IDENT})\s*(?:\(.*\))?", token, flags=re.DOTALL)
        if named:
            targets.append(_table_name(named.group(1)))
        elif token:
            problems.append(
                f"DROP FUNCTION target {part.strip()!r} cannot be read — the function "
                "it names is not tracked")
    return targets, problems


def _table_name(identifier: str) -> str:
    return identifier.strip('"').lower()


def _statements(sql: str) -> list[str]:
    """Split *sql* on statement terminators, respecting quotes and dollar bodies.

    A naive ``split(';')`` cuts a plpgsql body at its own internal semicolons and
    loses the ``CREATE FUNCTION`` header from the rest of its statement, and a
    single-quoted literal (``COMMENT ON … IS 'a;b'``) splits a statement in half.
    Dollar-quoted bodies are replaced by their tag pair — the definition header has
    already been read by the time the body starts, and the body is procedural code,
    not schema.
    """
    statements: list[str] = []
    buf: list[str] = []
    i, n = 0, len(sql)
    while i < n:
        ch = sql[i]
        if ch == "'":
            j = i + 1
            while j < n:
                if sql[j] == "'":
                    if sql[j + 1:j + 2] == "'":  # doubled quote inside the literal
                        j += 2
                        continue
                    break
                j += 1
            buf.append(sql[i:j + 1])
            i = j + 1
            continue
        if ch == "$":
            tag = re.match(r"\$([A-Za-z_][A-Za-z0-9_]*)?\$", sql[i:])
            if tag:
                opener = tag.group(0)
                end = sql.find(opener, i + len(opener))
                buf.append(opener)
                i = n if end == -1 else end + len(opener)
                continue
        if ch == ";":
            statements.append("".join(buf))
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    statements.append("".join(buf))
    return [s for s in (statement.strip() for statement in statements) if s]


def _create_table_columns(statement: str, open_paren: int) -> set[str]:
    """The column names DEFINED in the ``CREATE TABLE`` body at *open_paren*.

    Only a real ``CREATE TABLE … ( … )`` body is parsed, so a bare ``<name>
    <type>`` line that is really a ``RETURNS TABLE (`` output column or a plpgsql
    ``DECLARE`` variable is NOT a column definition — the anchoring the previous
    ``^\\s*<name>\\s+<type>`` line-scan lacked (it matched either shape anywhere in
    the corpus). Table constraints (``PRIMARY KEY``, ``UNIQUE``, ``CONSTRAINT``,
    ``FOREIGN KEY``, ``CHECK``, ``EXCLUDE``, ``LIKE``) are rejected because the type
    token must be one of ``_COLUMN_TYPES``.
    """
    body = _parenthesised(statement, open_paren)
    found: set[str] = set()
    definition = re.compile(rf"\s*({_IDENT})\s+(?:{'|'.join(_COLUMN_TYPES)})\b",
                            re.IGNORECASE)
    for part in _split_top_level(body):
        match = definition.match(part)
        if match:
            found.add(_table_name(match.group(1)))
    return found


def _parenthesised(sql: str, open_paren: int) -> str:
    """The text inside the parenthesis opening at *open_paren* (``""`` if unbalanced)."""
    depth = 0
    for i in range(open_paren, len(sql)):
        if sql[i] == "(":
            depth += 1
        elif sql[i] == ")":
            depth -= 1
            if depth == 0:
                return sql[open_paren + 1:i]
    return ""


def _split_top_level(body: str) -> list[str]:
    """Split *body* on commas that are not inside a nested parenthesis."""
    parts: list[str] = []
    buf: list[str] = []
    depth = 0
    for ch in body:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(buf))
            buf = []
            continue
        buf.append(ch)
    parts.append("".join(buf))
    return parts


def _creates_column(sql: str, column: str) -> bool:
    """True when *column* exists in the schema *sql* leaves behind.

    Not "was it created at some point": the corpus is replayed, so a column that a
    later migration RENAMEs or DROPs is absent (see ``_effective_schema``). Two
    accepted definitions, both DDL-anchored: an ``ALTER TABLE … ADD COLUMN`` (so
    the ``ADD COLUMN`` must belong to an ALTER, not to prose) or a name/type pair
    inside a parsed ``CREATE TABLE`` body.
    """
    return _effective_schema(sql).has_column(column)


def _defines_function(sql: str, rpc: str) -> bool:
    """True when *rpc* exists in the function set *sql* leaves behind.

    A ``GRANT``/``REVOKE``/``COMMENT ON`` line names a function without creating
    it, and an ``ALTER FUNCTION … RENAME TO`` renames it AWAY — the replay applies
    both, so a stale declaration does not stay green on the historical ``CREATE``.
    """
    return _effective_schema(sql).has_function(rpc)


# ── 3. no dead hook ──────────────────────────────────────────────────────


def referenced_names(path: Path) -> set[str]:
    """Every function NAME referenced in *path* (``f`` and ``m.f``, called or not).

    A reference, not only a call, because a legitimate production call site can
    hand the writer to a runner instead of calling it inline — measured:
    ``hosted_api`` reaches ``metering.record_capture_usage`` as an ARGUMENT to
    ``run_on_daemon_worker``, so a Call-only predicate reported the live capture
    dimension as a dead hook. Comments never appear in the AST and a docstring is
    a ``Constant``, so prose cannot satisfy this check.
    """
    return set(_file_facts(Path(path)).names)


@dataclass(frozen=True)
class _FileFacts:
    """One cached AST pass per file: both corpus gates read the same parse.

    The dead-hook scan and the foreign-metric scan both need every tracked
    module, so parsing twice doubled the audit's cost (measured: ~7 s per pass
    over 1,700 files). One pass, two facts.

    ``error`` is the third fact: a file that cannot be PARSED is reported, never
    read as "no names, no metric". Returning clean facts for an unparseable
    module let a file that references every declared writer satisfy the dead-hook
    check while contributing nothing to it — a silent fail-OPEN in the check whose
    stated job is "never skipped with a warning".
    """

    names: frozenset[str]
    builds_prometheus_metric: bool
    error: str | None = None


@cache
def _file_facts(path: Path) -> _FileFacts:
    """Cached per-file AST facts — the scan walks 1,700 files."""
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source)
    except (SyntaxError, OSError, ValueError) as exc:
        return _FileFacts(frozenset(), False, f"{type(exc).__name__}: {exc}")
    names: set[str] = set()
    constructs, modules = _prometheus_handles(tree)
    builds = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        if builds or not isinstance(node, ast.Call):
            continue
        if _looks_like_prometheus(node, constructs, modules):
            builds = True
    return _FileFacts(frozenset(names), builds)


def production_call_sites(root: Path) -> dict[str, list[str]]:
    """Map each declared writer/reader name to the non-test files that use it."""
    wanted = {
        binding.attr
        for dim in cd.dimensions()
        for binding in (dim.writers() or dim.readers())
    }
    sites: dict[str, list[str]] = {name: [] for name in wanted}
    for rel in tracked_python(root):
        if any(rel.startswith(prefix) for prefix in _NOT_PRODUCTION):
            continue
        path = root / rel
        if not path.is_file():
            continue
        for name in wanted & referenced_names(path):
            sites[name].append(rel)
    return sites


def check_dead_hooks(root: Path) -> list[str]:
    """A dimension must have a production call site, or say why it has none."""
    sites = production_call_sites(root)
    findings: list[str] = []
    for dim in cd.dimensions():
        if dim.state == cd.OUT_OF_SCOPE:
            continue
        bindings = dim.writers() or dim.readers()
        if not bindings:
            findings.append(f"{dim.name}: declares no writer and no reader")
            continue
        reached = [b.attr for b in bindings if sites.get(b.attr)]
        if not reached and not dim.unscheduled_reason:
            findings.append(
                f"{dim.name}: no production call site for "
                f"{', '.join(b.attr for b in bindings)} — a writer only tests "
                "reach is a dead hook (#4493); declare unscheduled_reason if "
                "that is deliberate")
    return findings


def unparseable_corpus_findings(root: Path) -> list[str]:
    """Tracked modules the corpus gates could not inspect.

    A file that does not parse contributes no names and no metric facts, so it
    silently satisfies BOTH the dead-hook scan (it may reference every declared
    writer) and the foreign-metric scan (it may build a tenth counter). The audit's
    contract is that an unresolvable input is reported as drift, never skipped —
    this is that report.
    """
    findings: list[str] = []
    for rel in tracked_python(root):
        if any(rel.startswith(prefix) for prefix in _NOT_PRODUCTION):
            continue
        path = root / rel
        if not path.is_file():
            continue
        error = _file_facts(path).error
        if error is not None:
            findings.append(
                f"{rel}: cannot be parsed ({error}) — the dead-hook and "
                "foreign-metric scans cannot see it")
    return findings


# ── 4. no undeclared dimension ───────────────────────────────────────────


def _prometheus_children(module) -> dict[str, object]:
    """Every prometheus Counter/Gauge/Histogram/Summary held by *module*.

    Raises ``ImportError`` when prometheus_client is absent — deliberately: the
    caller turns that into a FINDING. Swallowing it returned ``{}``, which made
    the undeclared-metric gate pass silently on a tree where it could not run at
    all (the audit never fabricates, and never reports a vacuous clean either).
    """
    from prometheus_client import Counter, Gauge, Histogram, Summary

    kinds = (Counter, Gauge, Histogram, Summary)
    return {name: obj for name, obj in vars(module).items()
            if isinstance(obj, kinds)}


def undeclared_metric_findings(children: dict[str, object] | None = None) -> list[str]:
    """Org-attributed prometheus children that no declared dimension names."""
    if children is None:
        try:
            children = _prometheus_children(cd.resolve(METRIC_MODULE))
        except ImportError as exc:  # pragma: no cover — prometheus is a hard dependency
            return [f"prometheus_client is not importable ({exc}) — the "
                    "declared-metric gate cannot run; install the runtime deps "
                    "and re-run (a gate that cannot run is drift, not a pass)"]
    declared = cd.attributed_bindings()
    findings: list[str] = []
    for name, obj in children.items():
        labels = tuple(getattr(obj, "_labelnames", ()) or ())
        if not cd.ATTRIBUTION_KEYS.intersection(labels):
            continue
        if name not in declared:
            findings.append(
                f"undeclared per-org metric {METRIC_MODULE}.{name} (labels "
                f"{labels!r}) — declare it as a dimension, or declare it out of "
                "scope with its reason")
    return findings


def foreign_metric_findings(root: Path) -> list[str]:
    """A prometheus metric constructed outside the declared metric module.

    A second metric surface is how a substrate grows a seventh counter that no
    declaration knows about. The check is source-level (AST), so it also catches
    a metric built lazily or in a test-visible helper.
    """
    try:
        import prometheus_client  # noqa: F401 — hard dependency; probes availability
    except ImportError as exc:  # pragma: no cover — prometheus is a hard dependency
        return [f"prometheus_client is not importable ({exc}) — the "
                "foreign-metric gate cannot run; install the runtime deps and "
                "re-run (a gate that cannot run is drift, not a pass)"]
    findings: list[str] = []
    for rel in tracked_python(root):
        if any(rel.startswith(p) for p in _NOT_PRODUCTION):
            continue
        if rel == METRIC_MODULE.replace(".", "/") + ".py":
            continue
        path = root / rel
        if not path.is_file():
            continue
        facts = _file_facts(path)
        if facts.error is not None:
            findings.append(
                f"{rel}: cannot be parsed ({facts.error}) — the foreign-metric "
                "gate cannot inspect it, so it is reported rather than skipped")
            continue
        if facts.builds_prometheus_metric:
            findings.append(
                f"{rel}: constructs a prometheus metric outside "
                f"{METRIC_MODULE} — the substrate's metric surface is declared "
                "in one module")
    return findings


#: The prometheus metric constructors this repo's surface is built from.
_PROMETHEUS_TYPES = frozenset({"Counter", "Gauge", "Histogram", "Summary"})

#: The package the constructors come from. Matched as a PREFIX, never by equality:
#: ``prometheus_client`` re-exports these names from ``prometheus_client.metrics``,
#: so the submodule spelling is the one a reader copies out of the package's own
#: source — and an exact match bound nothing for it, making
#: :func:`foreign_metric_findings` report clean over a metric built outside
#: ``monitoring.py`` (a false PASS in the check #5045 exists to make).
_PROMETHEUS_MODULE = "prometheus_client"


def _is_prometheus_module(name: str | None) -> bool:
    """Is *name* the prometheus package or one of its submodules?"""
    return bool(name) and (
        name == _PROMETHEUS_MODULE
        or name.startswith(_PROMETHEUS_MODULE + "."))


def _prometheus_handles(tree: ast.Module) -> tuple[frozenset[str], frozenset[str]]:
    """The names *tree* binds to prometheus constructors / the module itself.

    ``(constructors, modules)``: bare names bound by ``from prometheus_client
    import Counter [as _C]`` and aliases bound by ``import prometheus_client [as
    pc]``. Resolving the callee to its IMPORT is what makes the gate trustworthy in
    both directions: an aliased constructor is still caught, while
    ``collections.Counter(...)`` — ordinary tally code used all over this repo — is
    not, whatever its arguments look like.

    Both the package and its SUBMODULE spellings bind a handle: ``from
    prometheus_client.metrics import Counter``, ``import prometheus_client.metrics
    as pm`` and ``from prometheus_client import metrics as m`` are the same
    construction as the package-level form, and prometheus_client's own
    ``__init__`` re-exports through ``.metrics``, so they are realistic copies
    rather than contrived ones. Every form is keyed on the name it BINDS (the
    leading component for a bare ``import a.b``, which is what Python binds).
    """
    constructors: set[str] = set()
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and _is_prometheus_module(node.module):
            for alias in node.names:
                if alias.name in _PROMETHEUS_TYPES:
                    constructors.add(alias.asname or alias.name)
                else:
                    # A non-constructor name from the package is a handle to a
                    # submodule (`from prometheus_client import metrics as m`),
                    # so `m.Counter(...)` resolves through the module arm.
                    modules.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if _is_prometheus_module(alias.name):
                    # `import prometheus_client.metrics as pm` binds `pm`; the
                    # bare `import a.b` form binds only `a`, so the root is what
                    # a later `prometheus_client.metrics.Counter` walks from.
                    modules.add(alias.asname or alias.name.split(".")[0])
    return frozenset(constructors), frozenset(modules)


def _prometheus_root_name(node: ast.expr) -> str | None:
    """The leading ``Name`` of an attribute chain (``a.b.c`` -> ``a``)."""
    while isinstance(node, ast.Attribute):
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


def _looks_like_prometheus(node: ast.Call, constructors: frozenset[str] = frozenset(
                               {"Counter", "Gauge", "Histogram", "Summary"}),
                           modules: frozenset[str] = frozenset({"prometheus_client"}),
                           ) -> bool:
    """Does *node* construct a prometheus metric?

    Keyed on the CALLEE's provenance (bound from ``prometheus_client``, aliased or
    module-qualified), not on a name-shaped string argument. Both weaker designs
    failed in opposite directions, and each is pinned by a test: a name-shape-only
    check cannot see an aliased import (``_C("tortoise_x_total", …)``) and cannot
    see a ``name=`` keyword construction (``Counter(name="tortoise_x_total",
    documentation=…)``), while an any-identifier check reds on honest tallies.

    The module-qualified arm walks the attribute chain to its ROOT, so the nested
    submodule spelling (``pc.metrics.Counter(...)``) resolves to the same handle as
    ``pc.Counter(...)``; ``collections.Counter`` still does not, because only
    prometheus-derived roots are in *modules*.
    """
    func = node.func
    if isinstance(func, ast.Name) and func.id in constructors:
        return True
    return (
        isinstance(func, ast.Attribute)
        and func.attr in _PROMETHEUS_TYPES
        and _prometheus_root_name(func.value) in modules
    )


# ── 5. cardinality ───────────────────────────────────────────────────────


def check_states() -> list[str]:
    """Every ``Dimension.state`` is in the declared vocabulary.

    A typo'd state falls into NONE of measured / allocations / out-of-scope, so the
    matrix's arithmetic silently stops adding up and the coverage query answers
    "not measured" for a row that claims to be. Declared vocabulary or drift.
    """
    return [
        f"{dim.name}: undeclared state {dim.state!r} (declared: "
        f"{', '.join(cd.states())})"
        for dim in cd.dimensions()
        if dim.state not in cd.states()
    ]


def check_cardinality() -> list[str]:
    """Each declared cap/overflow attribute resolves to a live int/str.

    Covers BOTH axes: the per-org cap/overflow and the route-class
    ``path_caps``/``path_overflow``. The path axis is a live label-cap convention
    in this substrate (its own caps, its own sentinel), so leaving it out of the
    declaration would be exactly the undeclared-convention drift this gate exists
    to end.
    """
    findings: list[str] = []
    for dim in cd.dimensions():
        card = dim.cardinality
        if card is None:
            continue
        if card.lifetime not in (cd.PERSISTENT, cd.PER_PUBLISH):
            findings.append(
                f"{dim.name}: undeclared cardinality lifetime {card.lifetime!r}")
        for label, dotted, kind in (("cap", card.cap, int),
                                    ("overflow", card.overflow, str)):
            if dotted is None:
                findings.append(f"{dim.name}: declares {label} lifetime but no {label}")
                continue
            findings.extend(_resolve_typed(dim.name, label, dotted, kind))
        for dotted in card.path_caps:
            findings.extend(_resolve_typed(dim.name, "path cap", dotted, int))
        if card.path_caps and card.path_overflow is None:
            findings.append(f"{dim.name}: declares path caps but no path overflow")
        if card.path_overflow is not None:
            findings.extend(
                _resolve_typed(dim.name, "path overflow", card.path_overflow, str))
    return findings


def _resolve_typed(dim_name: str, label: str, dotted: str, kind: type) -> list[str]:
    """Resolve *dotted* and check its type — a single finding, never a raise."""
    try:
        value = cd.resolve(dotted)
    except Exception as exc:  # noqa: BLE001, RUF100 — drift, not a warning
        return [f"{dim_name}: {label} {dotted} does not resolve ({exc!r})"]
    if not isinstance(value, kind):
        return [f"{dim_name}: {label} {dotted} is {type(value).__name__}, "
                f"declared {kind.__name__}"]
    return []


# ── the verbs ────────────────────────────────────────────────────────────


def audit(root: Path) -> dict[str, list[str]]:
    """Run every check. Keys are the check names; values are its findings."""
    return {
        "bindings": check_bindings(),
        "ledger": check_ledger(root),
        "corpus": unparseable_corpus_findings(root),
        "dead_hooks": check_dead_hooks(root),
        "states": check_states(),
        "undeclared_metrics": (
            undeclared_metric_findings() + foreign_metric_findings(root)),
        "cardinality": check_cardinality(),
    }


def _cmd_matrix(args) -> int:
    root = _repo_root(args.root)
    findings = audit(root)
    rows = cd.coverage()
    if args.json:
        print(json.dumps({"coverage": rows, "findings": findings}, indent=2))
    else:
        # The summary's last figure is DERIVED: the registry's own unbacked rows
        # plus the undeclared-metric findings this audit owns (it needs the live
        # prometheus children, which the dependency-free registry does not import).
        print(cd.render_matrix(
            undeclared_metrics=len(findings["undeclared_metrics"])))
        print()
        flat = [f for group in findings.values() for f in group]
        if flat:
            print(f"DRIFT — {len(flat)} finding(s):")
            for name, group in findings.items():
                for finding in group:
                    print(f"  [{name}] {finding}")
        else:
            print("verified: bindings, ledger columns/RPCs, production call "
                  "sites, declared metrics, cardinality, states, corpus — "
                  "no drift")
    return 1 if any(findings.values()) else 0


def _cmd_query(args) -> int:
    try:
        dim = cd.by_name(args.dimension)
    except KeyError as exc:
        print(f"no such dimension: {exc}", file=sys.stderr)
        return 2
    writer = ", ".join(b.attr for b in dim.writers()) or "—"
    reader = ", ".join(b.attr for b in dim.readers()) or "—"
    print(f"{dim.name}: {dim.state}")
    print(f"  kind:        {dim.kind}")
    print(f"  unit:        {dim.unit}")
    print(f"  attribution: {dim.attribution}")
    print(f"  writer:      {writer}")
    print(f"  reader:      {reader}")
    if dim.cardinality is not None:
        print(f"  cardinality: {dim.cardinality.lifetime} "
              f"(cap {dim.cardinality.cap}, overflow {dim.cardinality.overflow})")
    if dim.columns:
        print(f"  columns:     {', '.join(dim.columns)}")
    if dim.rpcs:
        print(f"  sql:         {', '.join(dim.rpcs)}")
    for limit in dim.limits:
        print(f"  ⚠ limit:     {limit}")
    if dim.unscheduled_reason:
        print(f"  schedule:    none — {dim.unscheduled_reason}")
    # A declared out-of-scope dimension is an ANSWER (with its reason), not a
    # missing primitive: exit 3 so a caller can tell the two apart.
    return 3 if dim.state == cd.OUT_OF_SCOPE else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="verb", required=True)

    matrix = sub.add_parser("matrix", help="print + verify the coverage matrix")
    matrix.add_argument("--root", default=None)
    matrix.add_argument("--json", action="store_true")
    matrix.set_defaults(func=_cmd_matrix)

    query = sub.add_parser("query", help="is this dimension measured?")
    query.add_argument("dimension")
    query.set_defaults(func=_cmd_query)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
