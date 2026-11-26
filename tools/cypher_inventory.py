#!/usr/bin/env python3
"""cypher-inventory — the Cypher surface of the PRODUCT, enumerated by SHAPE (#13098).

WHY THIS EXISTS
===============
`#13082` sequences a Cypher→Postgres migration by *shape*, not by file, and
`#13098` asks for the census that makes the sequence safe. Four counts of this
surface were already published and they do not agree:

| count | unit | method |
|---|---|---|
| `1,074` sites / `57` files, `697` anchored + `377` whole-graph | read sites | **unpublished** (`#7879`, `#7970`) |
| `1,525` literals / `89` files | string literals | a re-derivation attempt — a DIFFERENT unit |
| `458` / `50` files | mutating `.query(` sites | one regex scan (`#12894` D-01) |
| `3,015` / `123` files | "Cypher statements" | cited in `#13082`/`#13098`, **no published method** |

`docs/epics/2026-10-10-7879-postgres-record/01-test-design.md:71` already warns
that the first two **must not be compared or summed**. So this tool does not
adopt any of them: it re-measures with a method that is in the box, and every
count it prints names the predicate that produced it — including the `rg`-style
totals the doc reconciles against.

THE UNIT, DECLARED
==================
A **statement site** is a `file:line` where a Cypher statement is handed to a
query door (`<anything>.query(...)`, `.ro_query(...)`, `.execute(...)`) **in the
product surface** — `tortoise/**/*.py`. Tests, docs, `graph-scripts/`,
`archive/`, `eval/`, `battery/` are NOT the product surface: they are counted
separately (`--all-surface`) and never folded into the product number.

⛔ WHAT THIS TOOL CANNOT SEE, stated rather than implied (the `#9124` lesson —
a census that hides its blind spots reads as complete):

* a statement built by a helper (`query=_build(x)`) or a loop variable is
  returned as `unresolved` and COUNTED, never dropped — the classifier's
  polarity is "not provably resolvable is unresolved". The doc's reconciliation
  section prints that number.
* a statement passed through a variable reassigned in a branch resolves to the
  FIRST binding seen in the function; the classifier may then call it by the
  wrong shape. It is never silently mis-shaped as `plain` — an unresolvable
  binding resolves to `unresolved`.
* `.query(` calls whose statement is not Cypher at all (a Postgres `execute`,
  a driver call) are counted separately as `non-cypher door calls` and are
  excluded from every shape total.

SHAPE (precedence order, first match wins — the order encodes "what the
statement NEEDS", which is the issue's question)
================================================
`ddl` → `fulltext` → `vector` → `traversal` → `adjacency` → `ep` → `plain`

⛔ `ddl` is FIRST because a statement that CREATES a fulltext or vector index names
one (`CALL db.idx.fulltext.createNodeIndex(…)`), so the DDL arm has to claim it
before the `fulltext`/`vector` arms see the name — while a `queryNodes`/`query`
QUERY matches no DDL arm and falls through to `fulltext`/`vector`. The earlier
telling of this list put `fulltext` and `vector` ahead of `ddl`, which was the
reverse of what `classify()` does and of what `test_shape_precedence` pins
(`#13098` review F4).

* `ddl` — index/constraint creation and index listing.
* `fulltext` — `db.idx.fulltext.*`.
* `vector` — `db.idx.vector.*` (the move §3 already decided).
* `traversal` — a VARIABLE-LENGTH relationship (`-[…*1..2]`, `[*]`, `*..`) or
  `shortestPath`: needs a walk.
* `adjacency` — an edge pattern WITHOUT variable length: a pattern match over
  adjacency. `#13098` calls this "case by case, measured" and §3 keeps the
  multi-hop shapes, so the verdict is `measure`, never a flip.
* `ep` — the epistemic scalars (`ep_*`, `posterior_*`, `baseline_*`,
  `confidence`): recomputed in Python over a closed component (§6.3).
* `plain` — no edge pattern and none of the above: point lookups, listings,
  filters, aggregates, ordered reads. **These are SQL-shaped.**

ROLE separates a read from a write, because the verdict is shape × role: a
`plain` READ is a migration candidate; a `plain` WRITE maintains the derived
copy and stays until `#13083` makes the shape `record_only`.

VERDICT is a function of shape × role and encodes the recorded decisions in
`docs/architecture/SEARCH-ARCHITECTURE.md` §3 — it does not re-decide them:

* traversal / adjacency / fulltext / ep / ddl / graph writes → **stays** (or
  `measure` for adjacency), each naming the section that keeps it.
* plain read → **candidate**.
* `vector` → **moved** (§3's one decided move).

USAGE
=====
    uv run python tools/cypher_inventory.py                # summary + reconciliation
    uv run python tools/cypher_inventory.py --markdown     # the appendix table
    uv run python tools/cypher_inventory.py --json         # machine form
    uv run python tools/cypher_inventory.py --check        # exit 1 on pin drift (reads the SOURCE)
    uv run python tools/cypher_inventory.py --render       # write the document (source + appendix)
    uv run python tools/cypher_inventory.py --all-surface  # every surface, not just the product

⛔ THE DOCUMENT IS GENERATED, NOT COMMITTED. `docs/architecture/CYPHER-INVENTORY.src.md`
is the tracked source (prose + the 25 pins); `docs/architecture/CYPHER-INVENTORY.md` is
`--render`'s output — Appendix A, one `file:line` row per classified statement
site, so its length moves with the tree — restamps on any commit that moves a
statement, so committing it made every such PR carry its own copy and conflict
with every other one (#13098). ⛔ No row or line count is typed here: the
appendix's own length is the number, and a copy of it in prose rots.
"""

from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter BEFORE the imports below — a module-level
# 3.11+-only import raises ImportError before a later guard could run.
#
# #4053/#11342: the refusal must not wear a VERDICT's status. This tool's
# answers are 0 (census produced) and 1 (`--check` reports doc drift), and
# `raise SystemExit(<message>)` exits 1 — so an old interpreter would report
# DRIFT against a doc nobody touched. 2 is this tool's own "could not run"
# (`check()` already returns it when the doc is absent), which is exactly what an
# old interpreter is, so the refusal is reused on purpose rather than invented.
# `SystemExit(<int>)` carries no message, so the refusal is printed explicitly.
# Pinned by `tests/test_entry_point_python_guard.py::REFUSAL_CODES`.
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    print(
        f"tools/cypher_inventory.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        "`uv run python tools/cypher_inventory.py`",
        file=sys.stderr,
    )
    raise SystemExit(2)

import argparse
import ast
import contextlib
import json
import os
import re
import subprocess
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: The hand-authored SOURCE — the prose and the 25 pins. This is the file git
#: tracks, and the file `--check` reads.
DOC_SRC = ROOT / "docs" / "architecture" / "CYPHER-INVENTORY.src.md"

#: The rendered DOCUMENT — `DOC_SRC` plus this run's appendix table. It is a
#: BUILD ARTIFACT and is NOT tracked: the appendix carries one `file:line` row
#: per statement site, so committing it made every product commit that moved a
#: statement restamp every later row, and every open PR then carried its own
#: whole-census copy of it, stamped against a different tree. Measured
#: 2026-11-24 at `939e37f664`: it appeared in 8 of 11 open PRs and in 3 of the
#: last 20 commits on `main` (reproducible: `git rev-list -20 939e37f664` + a
#: `diff-tree` per commit — the SHA, not `main`, because the figure is stamped to
#: that base and `main` has advanced since: the same command reads 2 today), and
#: it was in 6 of 6 then-conflicting PRs.
DOC_OUT = ROOT / "docs" / "architecture" / "CYPHER-INVENTORY.md"

#: Every verb that owns an OUTPUT stream of its own. `main()` refuses a command that
#: selects more than one (2 = could not run), because each verb's branch RETURNS.
#:
#: ⛔ DECLARED HERE, NOT INLINE IN `main()`, SO THE REFUSAL CAN BE PINNED OVER THE
#: CLASS RATHER THAN OVER THE PAIRS SOMEONE THOUGHT OF. The refusal is only as
#: complete as this tuple: a FIFTH output verb added to `build_parser()` and not
#: listed here would run and silently DROP the other verb's output. That is measured
#: history, not a hypothetical — `--check --json`, `--check --markdown` and
#: `--json --markdown` each exited **0** having run one verb and discarded the other
#: (#14625 review, P2), and the guard that was meant to stop it tested one pair
#: (`--check` + `--render`) while its own comment stated the invariant about the set.
#: `tests/test_13098_cypher_inventory.py` therefore asserts that the parser's flags
#: are EXACTLY this tuple plus `MODIFIERS`, and that EVERY pair in it is refused —
#: so a new verb reds a test instead of silently dropping output.
OUTPUT_VERBS = ("--check", "--markdown", "--json", "--render")

#: Flags that MODIFY a run instead of producing output of their own, so they may
#: accompany any single output verb. `--all-surface` is refused alongside `--check`
#: and `--render` for a different reason — the source's 25 pins are PRODUCT-surface
#: counts — and that refusal is its own branch in `main()`.
MODIFIERS = ("--all-surface",)

#: A source that carries a census row: the defect this split exists to make
#: impossible (someone pastes the appendix back into the tracked file, and the
#: conflict engine returns). `--render` and `--check` both refuse it.
#:
#: ⛔ IT MATCHES THE GENERATED ROW, NOT "ANY TABLE THAT STARTS WITH A `file:line`".
#: The broad `^\| `file:line` \|` matched a TWO-cell hand-authored prose row as
#: readily as a six-cell generated one, so a source whose first table column held
#: a site address was refused by `--render` (exit 2, "Delete the rows") and read by
#: `--check` as DRIFT (exit 1) — with correct pins. The remedy the first message
#: prescribes is then to delete CORRECT PROSE, and `cypher-diff.yml` reads that 1 as
#: "the doc's numbers are wrong" (#13098 review, P2). The appended table's own
#: first five cells (`markdown_table`) are the shape nothing but the generator
#: emits, and they still match a pasted appendix — measured against the tracked
#: document on `main` BEFORE this split, at `a29b5f24ce` (`git show
#: a29b5f24ce:docs/architecture/CYPHER-INVENTORY.md` — that path leaves `main` with
#: this change, so the command is stamped to the sha it was measured at): every one
#: of its appendix rows matched, and no prose row did. ⛔ No row count is typed
#: here: the appendix's own length is the number.
#:
#: ⚠️ A CHANGE TO `markdown_table`'s COLUMNS MUST RE-CUT THIS PATTERN: the arm
#: `test_a_hand_authored_prose_row_is_not_read_as_a_census_row` grades the pattern
#: against `markdown_table`'s own output, so a generator change reds there instead
#: of leaving the guard silently blind.
CENSUS_ROW = re.compile(
    r"^\| `[^`]+:\d+` \| `[^`]*` \| [a-z_]+ \| [a-z_]+ \|", re.MULTILINE)

#: Files whose statements are the PRODUCT's. Everything else is measured, never
#: folded in — the census unit is named above.
PRODUCT_PREFIX = "tortoise/"

#: Other surfaces, measured separately. Order is presentation only.
OTHER_SURFACES = (
    "tests/",
    "graph-scripts/",
    "tools/",
    "battery/",
    "eval/",
    "integrations/",
    "apps/",
    "archive/",
    "benchmarks/",
    "docs/",
)

#: A query door. `execute` is included because the driver exposes it; a call
#: whose text is not Cypher is excluded from shape totals (see module docstring).
DOORS = frozenset({"query", "ro_query", "execute"})

#: Anything that makes a string "look like" a Cypher statement. `WITH` is a
#: Cypher clause AND a Python keyword — it is matched only at line start, which
#: is why the alternation is anchored.
CYPHER_MARK = re.compile(
    r"(?:\b(?:MATCH|MERGE|UNWIND|CALL|OPTIONAL\s+MATCH|DETACH|RETURN)\b)"
    r"|(?:^\s*WITH\s)"
    r"|(?:^\s*(?:CREATE|DROP|SET|DELETE|REMOVE)\b(?!\s+(?:TABLE|SCHEMA|ROLE|EXTENSION|VIEW|"
    r"FUNCTION|TRIGGER|POLICY|SEQUENCE|TYPE|DATABASE|USER|INDEX\s+ON|OR\s+REPLACE)\b))",
    re.IGNORECASE | re.MULTILINE,
)

#: A statement that carries SQL-only syntax and NO node/relationship pattern is
#: SQL handed to a shared `execute` door — not Cypher. Without this veto the
#: `WITH …` arm of CYPHER_MARK admitted a psycopg2 CTE
#: (`record_allocator.py:406`, `WITH observed AS (SELECT …) INSERT INTO public.…`)
#: and counted it as a `plain` migration candidate (`#13098` review).
SQL_VETO = re.compile(r"\b(?:SELECT|INSERT\s+INTO|UPDATE|DELETE\s+FROM|VALUES\s*\(|JOIN)\b",
                      re.IGNORECASE)
#: What makes a statement provably graph-shaped: a node pattern, a relationship
#: pattern, or a Cypher-only clause.
NODE_PATTERN = re.compile(r"\(\s*`?\w*`?\s*:|<-\[|-\[|\[\w*\s*:\s*\w+\]")

VAR_LENGTH = re.compile(r"-\[[^\]]*\*[^\]]*\]-|<-\[[^\]]*\*[^\]]*\]-|shortestPath\s*\(", re.IGNORECASE)
#: A relationship pattern. ⛔ THE BRACKETLESS SPELLINGS ARE THE SECOND ARM.
#: Cypher accepts `-->`, `<--` and a bare `--` between two node patterns, and the
#: bracketed-only arm could not see any of them — so `MATCH (a)-->(b) RETURN b.id`
#: fell through every shape arm to `plain`/`read` and was published as a migration
#: CANDIDATE: the one direction the verdict flips (`#13098` review P3). There IS
#: one live site in this form — `tortoise/domain_validators.py:240`'s
#: `AND NOT (n)--()`, a real adjacency predicate — so this arm MOVED the census:
#: `adjacency`/`read` 189 → 190 and `plain`/`read` 571 → 570, totals unmoved. The
#: pins reflect it; `--check` proves below that nothing else followed.
#:
#: ⛔ IT IS CONTEXT-ANCHORED ON PURPOSE, and it is NOT added to `NODE_PATTERN`.
#:
#:  * `--` is ALSO SQL's comment introducer. A blanket `--` arm calls
#:    `SELECT * FROM public.point -- note` an `adjacency` statement. That is only
#:    harmless because `SQL_VETO` runs first in `enumerate_file` — so a rule that
#:    is correct ONLY because of a caller's ordering is a latent false candidate,
#:    which is the exact class this fix exists to remove. An arrowhead (`-->`,
#:    `<--`) is unambiguous, and a bare `--` counts only BETWEEN two node patterns
#:    (`)--(`, `]--[`), which no SQL statement's comment sits between.
#:  * `NODE_PATTERN`'s ONE caller is that SQL veto, so widening it with `--` would
#:    stop a SQL statement carrying a comment from being vetoed at all: the fix for
#:    a false candidate would create one.
EDGE_PATTERN = re.compile(
    r"(?:<)?-\[[^\]]*\]-?"          # bracketed: -[..]->, <-[..]-
    r"|<--+>?"                       # <-- and <-->
    r"|--+>"                         # -->
    r"|\)\s*-{2,}\s*\("              # bare -- between two node patterns
    r"|\]\s*-{2,}\s*\["              # …and between two list/bracket patterns
)
FULLTEXT = re.compile(r"db\.idx\.fulltext|fulltext\.query", re.IGNORECASE)
VECTOR = re.compile(r"db\.idx\.vector|vector\.query", re.IGNORECASE)
DDL = re.compile(
    # Only index/constraint CREATION or DROPPING is DDL. ⛔ The first version of
    # this pattern also matched `CALL db.idx.<anything>`, which swallowed every
    # fulltext and vector QUERY into `ddl` — a whole class silently re-labelled.
    r"^\s*(?:CREATE|DROP)\s+(?:INDEX|CONSTRAINT|FULLTEXT|VECTOR)"
    r"|^\s*CALL\s+db\.(?:indexes|constraints|awaitIndexes)"
    r"|^\s*CALL\s+db\.idx\.(?:fulltext|vector)\.(?:create|drop)",
    re.IGNORECASE | re.MULTILINE,
)
#: A `CALL` whose procedure name the TEXT CANNOT PROVE — folded by the resolver
#: (`CALL {}('Point')`, `CALL ???('Point')`) or outside the engine's own `db.*`
#: namespace (`CALL apoc.…`), where read-vs-write is the plugin's business and not
#: visible in the statement. ⛔ The live defect this closes (`#13098` review P1):
#: `tortoise/projection/__init__.py` loops over
#: `("db.idx.fulltext.drop", "db.idx.fulltext.dropIndex")` and calls
#: `CALL {drop_proc}('Point')`, so the resolver folded the NAME — and a fulltext-index
#: DROP was classified `plain`/`read` and published as a migration CANDIDATE, the one
#: direction a verdict flips, inside the headline count. Its un-folded spellings both
#: classify `ddl`/`ddl`, which is what proves the loss was in the folding, not in DDL.
#: `db.*` procedures stay classifiable: that is how fulltext/vector READS reach their
#: own shapes, and a blanket "any CALL is unknown" would swallow them.
CALL_PROC_UNPROVABLE = re.compile(
    r"\bCALL\s+(?:\{\}|\?\?\?|(?!db\.)[\w.]+\s*\()",
    re.IGNORECASE,
)

EP_FIELD = re.compile(
    r"\b(?:ep_alpha|ep_beta|ep_[a-z_]+|posterior_[a-z_]+|baseline_(?:alpha|beta|source)|confidence)\b"
)
WRITE_VERB = re.compile(
    r"^\s*(?:CREATE|MERGE|SET|DELETE|DETACH|REMOVE|DROP)\b",
    re.IGNORECASE | re.MULTILINE,
)
#: A write clause that is part of `MATCH … SET` counts; the regex above is
#: anchored to a line start, and statements are multi-line, so an inline write
#: needs this second arm.
#:
#: ⛔ EVERY Cypher write clause is listed, not only the dotted `SET n.x = 1` the
#: first version handled. That version missed four shapes a real statement uses,
#: and each miss turned a graph WRITE into a migration CANDIDATE — the one
#: direction the verdict flips, so the headline number was inflated by exactly
#: these (`#13098` review P2):
#:   `SET n += $props` / `SET n = $props` / `SET n:Label`  → the target is
#:       followed by an operator, not a `.`/`[`;
#:   `SET {}` / `SET ???`                                → the SET target was
#:       folded out of the literal, so nothing follows the keyword;
#:   `MATCH … MERGE (a)-[:R]->(b)` / `MATCH … CREATE (…)` → the verb is inline,
#:       never at a line start, so the `^`-anchored arm above cannot see it;
#:   `MATCH … REMOVE {}`                                 → the same folded form.
WRITE_INLINE = re.compile(
    r"\bSET\s+(?:(?:`[^`]*`|[\w]+)\s*(?:[.\[]|\+=|=|:)|[{\[]|\?\?\?)"
    r"|\b(?:MERGE|CREATE)\s*\("
    r"|\bDETACH\s+DELETE\b"
    r"|\b(?:DELETE|REMOVE|DROP)\s+(?:(?:`[^`]*`|[\w]+)|[{\[]|\?\?\?)",
    re.IGNORECASE,
)

SHAPES = ("fulltext", "vector", "ddl", "traversal", "adjacency", "ep", "plain", "unresolved")

#: verdict by (shape, role). `STAYS`/`MEASURE`/`CANDIDATE`/`MOVED`/`UNCLASSIFIED`.
VERDICTS = {
    ("fulltext", "read"): "stays:text-index (§6.2; #13099 measured, decision stands)",
    ("fulltext", "write"): "stays:graph-maintenance",
    ("vector", "read"): "moved:record-vector (§3, #11642)",
    ("vector", "write"): "stays:graph-maintenance",
    ("ddl", "ddl"): "stays:graph-maintenance",
    ("traversal", "read"): "stays:traversal (§3)",
    ("traversal", "write"): "stays:graph-maintenance",
    ("adjacency", "read"): "measure:across shapes (#13083 owns placement)",
    ("adjacency", "write"): "stays:graph-maintenance",
    ("ep", "read"): "stays:ep-carveout (§6.3)",
    ("ep", "write"): "stays:ep-carveout (§6.3)",
    ("plain", "read"): "candidate:SQL-shaped",
    ("plain", "write"): "stays:graph-maintenance (candidate when #13083 makes the shape record_only)",
    ("unresolved", "unresolved"): "unclassified:statement not resolvable statically",
}


#: A test module living INSIDE the product tree (`tortoise/test_cross_ontology.py`)
#: is not a statement the product executes. Excluded by name, counted, and the
#: count is printed — an exclusion a reader cannot see is an omission.
TEST_MODULE = re.compile(r"(?:^|/)test_[^/]*\.py$")


@dataclass(frozen=True)
class Site:
    """One statement site — the census unit."""

    path: str
    line: int
    function: str
    door: str
    shape: str
    role: str
    digest: str
    partial: bool = False

    @property
    def verdict(self) -> str:
        return VERDICTS.get((self.shape, self.role), "unclassified")

    def as_row(self) -> dict:
        return {
            "file:line": f"{self.path}:{self.line}",
            "function": self.function,
            "shape": self.shape,
            "role": self.role,
            "verdict": self.verdict,
            "statement": self.digest,
            "partial": self.partial,
        }


# --------------------------------------------------------------------------- #
# enumeration
# --------------------------------------------------------------------------- #
#: ``GIT_*`` variables that RETARGET every `git` subprocess at another checkout.
#:
#: ⛔ EVERY PROBE IN THIS MODULE DECIDES PERMISSION, SO A PROBE THAT DESCRIBES THE
#: WRONG REPOSITORY DECIDES THE WRONG PERMISSION (#14625 review, P1). Both
#: `--render` destination guards (the tracked-path probe and the git-metadata one)
#: and the census enumeration itself run `git` in the INHERITED environment, so an
#: exported `GIT_DIR` makes all three answer about a DIFFERENT repository. Measured
#: at `4019fa5` from this file's own bytes in scratch repos: with `GIT_DIR=<other>`,
#: `--render <intact --separate-git-dir>/config` exited **0** and wrote 145,745 bytes
#: over a 92-byte config that `git` could then no longer read ("bad config line 1"),
#: and a TRACKED destination answered 0 too (14 -> 145,745 bytes) instead of the
#: "refusing to render over a TRACKED path" this module prints — both guards defeated
#: by ONE exported variable, with git's non-positive answer read as permission. Git
#: exports `GIT_DIR` into hooks, and wrappers/CI export it routinely.
#:
#: This tool describes ROOT's repository by construction (`ROOT` is its own parent),
#: so the caller's retargeting is never the intent — the same list
#: `tools/lane_stash.py` scrubs for the same reason (`_RETARGETING_ENV`: inherited
#: from a hook they "would silently make this tool write lane refs ... against a peer
#: worktree"); `tests/test_13098_cypher_inventory.py` pins the two lists equal so
#: neither can drift. NOT included: `GIT_CEILING_DIRECTORIES`, measured not to bypass
#: the metadata guard (refused, 2, bytes unchanged).
_GIT_RETARGETING_ENV = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_COMMON_DIR",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_NAMESPACE",
)


def _git_env() -> dict[str, str]:
    """The environment every `git` subprocess in this module runs under.

    The caller's ``GIT_*`` retargeting is dropped, so each probe answers about the
    repository it was pointed at rather than the one an inherited variable names.
    """
    return {k: v for k, v in os.environ.items() if k not in _GIT_RETARGETING_ENV}


#: Seconds the git-metadata probe may take before it is read as `could not determine`.
#: ⛔ WITHOUT IT THE COMMENT BELOW IS A CLAIM WITH NO CODE BEHIND IT (#14625 review,
#: P3): the refusal block already counted "a timeout" among the failures that REFUSE,
#: but no bound was passed, so a hung git hung `--render` — measured at `9e477cdb`, an
#: external `timeout 40` returned **124**, having printed no refusal and written
#: nothing. A false permit costs a repository git cannot restore; an unbounded probe
#: costs the same by never finishing, so the bound is enforced rather than described.
_GIT_PROBE_TIMEOUT_S = 30


def _looks_like_git_metadata(directory: Path) -> bool:
    """True when ``directory`` itself holds git metadata, resolvable or not.

    A bare repository and a `--separate-git-dir`/worktree git dir all answer True; a
    work tree's ROOT does not (its metadata is in ``.git``, a separate path), and an
    ordinary directory does not.

    ⛔ ONE DISTINCTIVE MARKER PLUS ANY SECOND ONE, because the damage that stops git
    from answering is not always the same file. A removed ``HEAD`` was the first case
    measured (the bare repo answered like a plain directory and `--render` wrote its
    config over at exit 0); a removed ``objects/`` is the same class one file over
    (measured on the version that REQUIRED ``objects/``: 66 -> 145,745 bytes at rc 0,
    with ``HEAD``, ``refs/`` and ``config`` all intact). A named paired set keeps moving
    the hole, so the test is: a distinctive marker (``objects/``, ``refs/`` or ``HEAD`` —
    none of which an unrelated directory carries) PLUS at least one more, which is what
    a repository always has two of. Two ordinary files called ``config``/``index`` in a
    project directory are NOT metadata, on purpose.
    """
    markers = ("objects", "refs", "HEAD", "config", "packed-refs", "index")
    present = [marker for marker in markers if (directory / marker).exists()]
    return len(present) >= 2 and any(
        (directory / distinctive).exists()
        for distinctive in ("objects", "refs", "HEAD")
    )


def surface_files(prefixes: tuple[str, ...]) -> list[str]:
    """Tracked Python files under the given prefixes — bounded by the git index.

    `git ls-files` reads the index, so this never walks `.worktrees/`,
    `node_modules/` or a build tree: the search-guard rule and the load policy.
    """
    # ⛔ `-z` EMITS RAW FILENAME BYTES, so `text=True` (strict UTF-8) RAISES on a
    # tracked name that is not valid UTF-8 — a `UnicodeDecodeError`, which is a
    # `ValueError`, NOT an `OSError`, so it slips through both of `main()`'s guards and
    # exits 1 (the DRIFT code) on the DEFAULT verb and on `--check`. Measured in a
    # scratch repo with a tracked `caf\xe9.py`: bare verb and `--check` both rc 1.
    # `surrogateescape` round-trips those bytes, so `ROOT / relpath` still opens the
    # file and the census reads it like any other.
    #
    # ⛔ AND IT IS BOUNDED (#14625 review, P3): this enumeration runs on EVERY verb, so
    # an unbounded `git` here hangs the census, `--check` and `--render` alike — which
    # is the case that made the metadata guard's "a timeout REFUSES" sentence false
    # even after that probe was given a bound.
    out = subprocess.run(
        ["git", "ls-files", "-z", "--", *prefixes],
        cwd=ROOT, capture_output=True, text=True, check=True,
        encoding="utf-8", errors="surrogateescape",
        timeout=_GIT_PROBE_TIMEOUT_S,
        # An inherited `GIT_DIR` would enumerate a PEER repository and hand its files
        # to the parser — a wrong document at exit 0, not a refusal. See
        # `_GIT_RETARGETING_ENV`.
        env=_git_env(),
    ).stdout
    files = [p for p in out.split("\0") if p.endswith(".py")]
    return sorted(files)
def _resolve_text(node: ast.AST | None, consts: dict[str, str], depth: int = 0,
                  partial: list[bool] | None = None) -> str | None:
    """Best-effort static text of a statement expression.

    Returns None when NOTHING of the expression resolves. When only PART of it
    does (a `+`-concatenation with a local, a `.format` on a computed string),
    the resolvable parts are kept and the rest becomes `???`, and `partial[0]`
    is set — because the SHAPE lives in the literal half
    (`"MATCH (op:Point)-[:" + safe_rel + "]->(t)"` is an adjacency read, and
    dropping it would hide a whole class in the `unresolved` bucket).
    """
    if partial is None:
        partial = [False]
    if node is None or depth > 6:
        partial[0] = True
        return None
    if isinstance(node, ast.Constant):
        if isinstance(node.value, str):
            return node.value
        partial[0] = True
        return None
    if isinstance(node, ast.JoinedStr):  # f-string
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            elif isinstance(value, ast.FormattedValue):
                inner = _resolve_text(value.value, consts, depth + 1)
                partial[0] = True  # an interpolation is never fully static text
                parts.append(inner if inner is not None else "{}")
            else:
                parts.append("{}")
        return "".join(parts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _resolve_text(node.left, consts, depth + 1, partial)
        right = _resolve_text(node.right, consts, depth + 1, partial)
        if left is None and right is None:
            return None
        return (left if left is not None else "???") + (right if right is not None else "???")
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
        # `"...%s..." % (x,)` — keep the literal, drop the substitution.
        left = _resolve_text(node.left, consts, depth + 1, partial)
        if left is None:
            return None
        partial[0] = partial[0] or bool(node.right)
        return re.sub(r"%[sdr]", "{}", left)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "format":
        recv = _resolve_text(node.func.value, consts, depth + 1, partial)
        if recv is None:
            return None
        partial[0] = partial[0] or bool(node.args or node.keywords)
        return re.sub(r"\{[^}]*\}", "{}", recv)
    if isinstance(node, ast.Name):
        text = consts.get(node.id)
        if text is None:
            partial[0] = True
        return text
    partial[0] = True
    return None


def _module_consts(tree: ast.Module) -> dict[str, str]:
    """Module-level `NAME = <resolvable string>` bindings, folded in order."""
    consts: dict[str, str] = {}
    for stmt in tree.body:
        if isinstance(stmt, ast.Assign):
            for target in stmt.targets:
                if isinstance(target, ast.Name):
                    text = _resolve_text(stmt.value, consts)
                    if text is not None:
                        consts[target.id] = text
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            text = _resolve_text(stmt.value, consts)
            if text is not None:
                consts[stmt.target.id] = text
    return consts


def _own_nodes(scope: ast.AST):
    """Yield nodes belonging to `scope` itself — never descending into a nested
    def/class/lambda.

    ⛔ This is what keeps the census a CENSUS: iterating `ast.walk` once per
    `FunctionDef` counts a nested function's door calls once per enclosing
    scope, which inflated this tool's own first run by 60 % (2,678 sites against
    1,609 door-call lines). One traversal, one count.
    """
    stack = list(ast.iter_child_nodes(scope))
    while stack:
        node = stack.pop()
        yield node
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        # ⛔ `ast.Lambda` is NOT a scope boundary here: `lambda: _sdk.query("MATCH …")`
        # is 52 real product statements (23 `asyncio.to_thread(lambda: …)` in
        # hosted_api, 29 in sdk.py), and stopping at the lambda dropped every one
        # of them while the reconciliation's residual line absorbed the loss (#13098 review).
        stack.extend(ast.iter_child_nodes(node))


def _local_consts(func: ast.AST, consts: dict[str, str]) -> dict[str, str]:
    """Function-local `NAME = <string>` bindings (first binding wins)."""
    local = dict(consts)
    for node in _own_nodes(func):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id not in local:
                    text = _resolve_text(node.value, consts)
                    if text is not None:
                        local[target.id] = text
    return local


def _is_door(call: ast.Call) -> str | None:
    func = call.func
    if isinstance(func, ast.Attribute) and func.attr in DOORS:
        return func.attr
    return None


def _statement_node(call: ast.Call) -> ast.AST | None:
    for kw in call.keywords:
        if kw.arg in {"q", "query", "statement", "cypher"}:
            return kw.value
    return call.args[0] if call.args else None


def _digest(text: str, limit: int = 68) -> str:
    """One-line, pipe-safe digest: the statement's leading clause, whitespace folded."""
    flat = re.sub(r"\s+", " ", text).strip()
    flat = flat.replace("|", "\\|").replace("`", "'")
    return flat[:limit] + ("…" if len(flat) > limit else "")


def classify(text: str) -> tuple[str, str]:
    """(shape, role) for a resolved statement."""
    if CALL_PROC_UNPROVABLE.search(text):
        # The polarity the census already applies to an unresolvable STATEMENT
        # applies one level down to an unresolvable PROCEDURE: not provably a read
        # is not a candidate. Counted and named, never dropped (`#9124`).
        return "unresolved", "unresolved"
    if DDL.search(text):
        return "ddl", "ddl"
    if FULLTEXT.search(text):
        shape = "fulltext"
    elif VECTOR.search(text):
        shape = "vector"
    elif VAR_LENGTH.search(text):
        shape = "traversal"
    elif EDGE_PATTERN.search(text):
        shape = "adjacency"
    elif EP_FIELD.search(text):
        shape = "ep"
    else:
        shape = "plain"
    writes = bool(WRITE_VERB.search(text) or WRITE_INLINE.search(text))
    if shape == "ep" and not writes:
        return shape, "read"
    return shape, ("write" if writes else "read")


def enumerate_file(relpath: str) -> tuple[list[Site], int, int]:
    """(sites, non_cypher_door_calls, unparsed) for one file — ONE traversal."""
    try:
        source = (ROOT / relpath).read_text(encoding="utf-8")
        tree = ast.parse(source)
    except (SyntaxError, UnicodeDecodeError):
        return [], 0, 1
    consts = _module_consts(tree)
    sites: list[Site] = []
    non_cypher = 0

    def walk(scope: ast.AST, prefix: str, scope_consts: dict[str, str]) -> None:
        nonlocal non_cypher
        for node in _own_nodes(scope):
            if not isinstance(node, ast.Call):
                continue
            door = _is_door(node)
            if door is None:
                continue
            partial = [False]
            text = _resolve_text(_statement_node(node), scope_consts, partial=partial)
            if text is None:
                # A door call we cannot resolve: counted as unresolved, never
                # dropped — the over-report polarity (#9124).
                sites.append(Site(relpath, node.lineno, prefix.rstrip("."), door,
                                  "unresolved", "unresolved", "<unresolved>"))
                continue
            if not CYPHER_MARK.search(text):
                non_cypher += 1
                continue
            if SQL_VETO.search(text) and not NODE_PATTERN.search(text):
                # SQL-only syntax with no node/relationship pattern: a Postgres
                # statement sharing the `execute` door, not a Cypher statement.
                non_cypher += 1
                continue
            shape, role = classify(text)
            sites.append(Site(relpath, node.lineno, prefix.rstrip("."), door,
                              shape, role, _digest(text), partial=partial[0]))

    def descend(scope: ast.AST, prefix: str, scope_consts: dict[str, str]) -> None:
        walk(scope, prefix, scope_consts)
        # Then each nested scope gets ITS OWN traversal, with its own bindings.
        for node in _own_nodes(scope):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = f"{prefix}{node.name}."
                descend(node, name, _local_consts(node, scope_consts))

    descend(tree, "", consts)
    return sites, non_cypher, 0


def census(prefixes: tuple[str, ...], *, drop_test_modules: bool = True) -> dict:
    files = surface_files(prefixes)
    # ⛔ The test-module exclusion is a PRODUCT-surface rule (`tortoise/test_*.py`).
    # Applying it in `--all-surface` mode excluded 1,468 files under `tests/` —
    # i.e. it would have measured every surface except the largest one.
    excluded = [f for f in files if drop_test_modules and TEST_MODULE.search(f)]
    if drop_test_modules:
        files = [f for f in files if not TEST_MODULE.search(f)]
    sites: list[Site] = []
    non_cypher = 0
    unparsed = 0
    for rel in files:
        file_sites, file_non_cypher, file_unparsed = enumerate_file(rel)
        sites.extend(file_sites)
        non_cypher += file_non_cypher
        unparsed += file_unparsed
    return {
        "prefixes": list(prefixes),
        "files_scanned": len(files),
        "files_with_sites": len({s.path for s in sites}),
        "excluded_test_modules": len(excluded),
        "sites": sites,
        "non_cypher_door_calls": non_cypher,
        "unparsed_files": unparsed,
        "ast_door_calls": len(sites) + non_cypher,
        "partial": sum(1 for s in sites if s.partial),
    }


# --------------------------------------------------------------------------- #
# the rg-style predicates, computed in-process so the doc reconciles against
# numbers this tool can reproduce without a shell
# --------------------------------------------------------------------------- #
def rg_totals(prefixes: tuple[str, ...], *, drop_test_modules: bool = True) -> dict:
    """Line/file totals for the predicates the doc shows as `rg` equivalents.

    The reconciliation is MEASURED, not inferred: the occurrences of the door
    pattern that sit on a line carrying no door CALL are counted here (comments
    and docstrings), so the arithmetic  regex − non-call = AST calls  closes on
    numbers this tool computed rather than on a plausible remainder.
    """
    files = surface_files(prefixes)
    if drop_test_modules:
        files = [f for f in files if not TEST_MODULE.search(f)]
    verb_line = re.compile(r"\b(?:MATCH|MERGE)\s*\(", re.IGNORECASE)
    door_line = re.compile(r"\.(?:ro_)?(?:query|execute)\s*\(")
    totals = {
        "py_files": len(files),
        "lines_match_or_merge": 0,
        "files_match_or_merge": 0,
        "lines_door_calls": 0,
        "files_door_calls": 0,
        "door_occurrences_without_a_call": 0,
        "door_occurrences_on_call_lines": 0,
        "lines_where_regex_and_ast_disagree": 0,
    }
    for rel in files:
        try:
            text = (ROOT / rel).read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError):
            continue
        m = verb_line.findall(text)
        occurrences = door_line.findall(text)
        if m:
            totals["lines_match_or_merge"] += len(m)
            totals["files_match_or_merge"] += 1
        if occurrences:
            totals["lines_door_calls"] += len(occurrences)
            totals["files_door_calls"] += 1
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        ast_by_line = Counter(
            node.lineno for node in ast.walk(tree)
            if isinstance(node, ast.Call) and _is_door(node)
        )
        call_lines = set(ast_by_line)
        for lineno, line in enumerate(text.splitlines(), start=1):
            occurrences_here = len(door_line.findall(line))
            if lineno in call_lines:
                totals["door_occurrences_on_call_lines"] += occurrences_here
                if occurrences_here != ast_by_line[lineno]:
                    totals["lines_where_regex_and_ast_disagree"] += 1
            else:
                totals["door_occurrences_without_a_call"] += occurrences_here
    return totals


def shape_matrix(sites: list[Site]) -> dict:
    matrix = defaultdict(Counter)
    for site in sites:
        matrix[site.shape][site.role] += 1
    out = {}
    for shape in SHAPES:
        counts = matrix.get(shape)
        if counts:
            out[shape] = dict(sorted(counts.items()))
    return out


def report(result: dict) -> str:
    sites: list[Site] = result["sites"]
    matrix = shape_matrix(sites)
    lines = []
    add = lines.append
    add("Cypher inventory — " + ("EVERY tracked surface" if result["prefixes"] == ["."]
                                 else "the PRODUCT surface (tortoise/**/*.py)"))
    add("=" * 68)
    add(f"files scanned            : {result['files_scanned']}")
    add(f"files with statement sites: {result['files_with_sites']}")
    add(f"statement sites (census)  : {len(sites)}")
    add(f"non-Cypher door calls     : {result['non_cypher_door_calls']} (excluded from shape totals)")
    add(f"partially resolved sites  : {result['partial']} (literal half kept; `???` marks the rest)")
    add(f"unparsed files            : {result['unparsed_files']}")
    add(f"excluded test modules     : {result['excluded_test_modules']} (tortoise/test_*.py)")
    add("")
    add("by shape × role")
    add("-" * 68)
    for shape, roles in matrix.items():
        total = sum(roles.values())
        detail = ", ".join(f"{r}={c}" for r, c in roles.items())
        add(f"  {shape:<12} {total:>5}   ({detail})")
    add("")
    add("by verdict")
    add("-" * 68)
    verdicts = Counter(s.verdict for s in sites)
    for verdict, count in verdicts.most_common():
        add(f"  {count:>5}  {verdict}")
    add("")
    add("top files by site count")
    add("-" * 68)
    for path, count in Counter(s.path for s in sites).most_common(15):
        add(f"  {count:>5}  {path}")
    add("")
    add("reconciliation predicates (the `rg` equivalents)")
    add("-" * 68)
    totals = rg_totals(tuple(result["prefixes"]),
                       drop_test_modules=result["excluded_test_modules"] > 0)
    add(f"  py files under surface                         : {totals['py_files']}")
    add(f"  occurrences of \\.(ro_)?(query|execute)\\(      : {totals['lines_door_calls']}"
        f"  across {totals['files_door_calls']} files")
    add(f"  lines matching \\b(MATCH|MERGE)\\s*\\(            : {totals['lines_match_or_merge']}"
        f"  across {totals['files_match_or_merge']} files")
    add(f"  statement sites (this census, resolved or not) : {len(sites)}")
    add("")
    add("  how the two reconcile")
    add(f"    regex door-pattern occurrences          {totals['lines_door_calls']}")
    add(f"    − occurrences on a line with no door CALL (comments/docstrings)"
        f"  {totals['door_occurrences_without_a_call']}")
    add(f"    = occurrences on a line WITH a door CALL"
        f"                 {totals['door_occurrences_on_call_lines']}")
    add(f"    − of those, the AST did not record (residual)"
        f"         {totals['door_occurrences_on_call_lines'] - result['ast_door_calls']}")
    add(f"    = AST door calls                        {result['ast_door_calls']}")
    add(f"    = classified Cypher {len(sites)} + non-Cypher {result['non_cypher_door_calls']}"
        f"  {len(sites) + result['non_cypher_door_calls']}")
    return "\n".join(lines)


def markdown_table(sites: list[Site]) -> str:
    lines = [
        "| file:line | fn | shape | role | verdict | statement |",
        "|---|---|---|---|---|---|",
    ]
    for site in sorted(sites, key=lambda s: (s.path, s.line)):
        lines.append(
            f"| `{site.path}:{site.line}` | `{site.function or '-'}` | {site.shape} | {site.role} "
            f"| {site.verdict} | `{site.digest}` |"
        )
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# drift check — the doc's pinned counts are the gate
# --------------------------------------------------------------------------- #
PINNED = re.compile(r"<!-- cypher-inventory:(\w+) = (\d+) -->")


def render(src: Path, result: dict) -> str:
    """`src` (prose + pins) with this run's appendix appended — the document.

    ⛔ THE APPENDIX IS APPENDED, NEVER MERGED: a source that already carries a
    census row is REFUSED with exit 2 (this module's "could not run") rather than
    rendered, because rendering it would silently duplicate rows — and
    re-committing those rows is the whole defect this split removes. (2, never 1:
    1 is the DRIFT verdict. `--check` on a row-carrying file is a DIFFERENT
    verdict — 1, naming the rows — because a committed appendix is a census
    defect, not a tool that could not run.)

    ⛔ THE SEPARATOR IS INSERTED, not assumed: the source ends on the VISIBLE note
    that sits under the Appendix heading (the tracked file is what a reader sees on
    a forge, so a heading with nothing under it promises rows the source does not
    carry — `#14625` review, P2), with a SINGLE trailing newline; two blank lines
    there would be a markdownlint MD012 finding on the required `docs` check. This
    function supplies the one blank line that separates that note from the table.
    """
    if not src.exists():
        print(f"render: no source at {src}", file=sys.stderr)
        raise SystemExit(2)
    # ⛔ A SOURCE THAT CANNOT BE READ IS "COULD NOT RUN" (2), NOT A CRASH (1) — the
    # twin of `check()`'s guard. `--render` has NO drift verdict, so an uncaught
    # PermissionError / IsADirectoryError / UnicodeDecodeError exits 1, the code
    # reserved for "the pins drifted", and §7 tells every lane to run this verb: a
    # lane would re-cut 25 correct pins over a file it never read.
    try:
        text = src.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as err:
        print(f"render: could not read {src}: {err}", file=sys.stderr)
        raise SystemExit(2) from err
    rows = CENSUS_ROW.findall(text)
    if rows:
        print(
            f"render: refusing {src} — it carries {len(rows)} census row(s) "
            f"(first: {rows[0]!r}). The appendix is GENERATED: nobody commits it, "
            "and pasting it back into the source is the conflict this split exists "
            "to remove. Delete the rows; `--render` appends them from the tree.",
            file=sys.stderr,
        )
        raise SystemExit(2)
    return text.rstrip("\n") + "\n\n" + markdown_table(result["sites"]) + "\n"


def check(doc: Path, result: dict) -> int:
    if not doc.exists():
        print(f"check: no doc at {doc}", file=sys.stderr)
        return 2
    # ⛔ A DOC THAT IS PRESENT BUT UNREADABLE IS "COULD NOT RUN" (2), NOT DRIFT (1) —
    # same class as the census wrap in `main()`, in the same verb: an uncaught
    # PermissionError/EIO would exit 1, the code reserved for "the pins drifted", and a
    # lane would re-cut 25 pins that were correct.
    try:
        text = doc.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as err:
        # UnicodeDecodeError is a ValueError, NOT an OSError, so it needed naming:
        # a present-but-non-UTF-8 source exited 1 through this very guard.
        print(f"check: could not read {doc}: {err}", file=sys.stderr)
        return 2
    rows = CENSUS_ROW.findall(text)
    if rows:
        print(
            f"check: {doc} carries {len(rows)} census row(s) (first: {rows[0]!r}) "
            "— it is a RENDERED document, or a source someone pasted the rows "
            "into. The pins live in docs/architecture/CYPHER-INVENTORY.src.md, "
            "and the appendix is generated by `--render`; never committed",
            file=sys.stderr,
        )
        # 1, deliberately: a committed appendix is a RED, not "could not run".
        return 1
    # ⛔ A DUPLICATED PIN IS "COULD NOT RUN" (2), NOT A LAST-WINS (#14625 review, round
    # 3, P3). `{k: int(v) for k, v in PINNED.findall(text)}` keeps the LAST occurrence
    # of a repeated key silently, so a source that declares `sites` twice — the first
    # one edited by an author who believes it is the live pin — still printed
    # "check: 25 pinned counts match" and exited 0 (measured: `sites` pinned 1339 then
    # 1332, 26 pin comments, rc 0). That is a FALSE PASS in the one verb whose whole
    # job is to refuse a wrong number, and it is invisible: 25 `PINNED` matches still
    # count as 25 pins. A source is hand-authored, so a duplicate is a mistake to
    # report rather than a value to pick — 2, because the doc cannot be graded at all.
    pin_pairs = PINNED.findall(text)
    repeated = sorted(
        key for key, count in Counter(k for k, _ in pin_pairs).items() if count > 1
    )
    if repeated:
        print(
            f"check: {doc} declares {', '.join(repeated)} more than once —\n"
            "`--check` would keep only the LAST occurrence silently, so a duplicate "
            "pin is\na pin nobody grades. Delete the extra declaration(s) and re-run.",
            file=sys.stderr,
        )
        return 2
    pinned = {k: int(v) for k, v in pin_pairs}
    sites: list[Site] = result["sites"]
    actual = {
        "sites": len(sites),
        "files": result["files_with_sites"],
        # ⛔ `files_scanned` is pinned too: §0 publishes the scanned-file count as
        # its own row, and `files` is a DIFFERENT number (only the files that CARRY
        # a site — a module can be scanned and carry none). Before this key existed
        # the doc's scanned count could drift by any amount while `--check` stayed
        # green (`#13098` review P3). ⛔ Neither figure is quoted here: a number
        # beside a claim rots at the next file that lands, which is why this
        # comment said "295 files scanned" long after §0 had stopped saying it.
        "files_scanned": result["files_scanned"],
        "unresolved": sum(1 for s in sites if s.shape == "unresolved"),
        "candidates": sum(1 for s in sites if s.verdict.startswith("candidate")),
        # ⛔ §0 also PUBLISHES these four, and they were outside `actual`, so the doc
        # could have printed any value for them while `--check` stayed green — the
        # same vacuity the `files_scanned` key above closed, left open for its
        # neighbours (`#13098` review P3). `ast_door_calls` is the reconciliation's
        # headline (§1's `= N AST door calls` line, which is the classified statements
        # PLUS these non-Cypher calls), so it is pinned as the SUM rather than as a
        # second independent count that could disagree. ⛔ The §1 line is named, not
        # quoted: this comment used to type `§0's "= 1,557"`, a figure that appears in
        # no section of the document at any base — a quoted number beside a pinned
        # relation rots and misdirects exactly as fast as a stale one.
        "non_cypher_door_calls": result["non_cypher_door_calls"],
        "ast_door_calls": len(sites) + result["non_cypher_door_calls"],
        "partial": result["partial"],
    }
    matrix = shape_matrix(sites)
    for shape, roles in matrix.items():
        actual[f"shape_{shape}"] = sum(roles.values())
        # ⛔ The ROLE SPLIT is pinned for EVERY shape, not only `plain`. A role swap
        # leaves the shape TOTAL untouched, and §3 publishes `adjacency` 189/100 and
        # `ep` 21/11 as numbers no pin carried — so the exact regression the census
        # exists to catch (a graph write published as `read`, i.e. as a migration
        # candidate) was invisible for every shape except `plain` (`#13098` review
        # P2). `plain` was pinned first because it is the one whose reads ARE the
        # candidate list; the rule is not specific to it.
        for role, count in roles.items():
            actual[f"shape_{shape}_{role}"] = count
    drift = {
        key: (pinned.get(key), value)
        for key, value in actual.items()
        if pinned.get(key) != value
    }
    missing = [key for key in actual if key not in pinned]
    stale = [key for key in pinned if key not in actual]
    if drift or missing or stale:
        for key, (was, now) in sorted(drift.items()):
            print(f"DRIFT {key}: doc={was} tool={now}", file=sys.stderr)
        for key in sorted(missing):
            print(f"MISSING pin {key} (tool={actual[key]})", file=sys.stderr)
        for key in sorted(stale):
            print(f"STALE pin {key}={pinned[key]} — the tool no longer computes it",
                  file=sys.stderr)
        return 1
    return emit(f"check: {len(actual)} pinned counts match")


def emit(text: str, stream=None) -> int:
    """Write one stdout payload; a write that cannot happen is 2, never 1.

    ⛔ `--markdown` IS the appendix table — the one verb a lane pipes (`| head`) or
    redirects, and the one whose payload is far larger than the stdout buffer. A
    closed pipe (`BrokenPipeError`) or a full disk (`OSError ENOSPC`) used to escape
    uncaught and exit **1**, the code this tool reserves for the DRIFT verdict, so a
    lane read "the pins drifted" and re-cut 25 correct pins. `--render`'s write and
    `check()`'s read already answer 2 for their own half of this class; this closes
    the stdout half for every emitting verb.

    The explicit `flush()` is load-bearing: a payload small enough to sit in the
    buffer raises at interpreter shutdown (exit **120**) instead of here, so without
    it the smaller verbs would keep answering something that is not "could not run".
    """
    out = sys.stdout if stream is None else stream
    # ⛔ `sys.stdout` IS `None` WHEN fd 1 IS CLOSED AT START (`>&-`), and `None.write`
    # raises AttributeError — which exits **1**, the DRIFT code. Measured: `--markdown >&-`
    # exited 0 before this writer (bare `print()` with a `None` stream is a documented
    # no-op) and 1 after it, so the guard would have been the one path that INTRODUCED
    # the misclassification it exists to remove. "Could not run" is 2, named.
    if out is None:
        print("census: could not write to stdout (no stream: fd 1 is closed) — this is "
              "'could not run', not drift", file=sys.stderr)
        return 2
    try:
        out.write(text + "\n")
        out.flush()
    except BrokenPipeError:
        # Python's own advice: re-point fd 1 at devnull so the shutdown flush cannot
        # re-raise and turn the 2 below into 120. Guarded — a test double has no fd.
        _detach_stdout(out)
        print("census: could not write to stdout (closed pipe) — this is 'could not "
              "run', not drift", file=sys.stderr)
        return 2
    except OSError as err:
        # ⛔ THE SAME DETACH IS OWED HERE, and it is measured, not defensive: the failed
        # `flush()` leaves its bytes in the buffer, so the interpreter's shutdown flush
        # retries them and REPLACES the 2 with **120** (a full disk: `>/dev/full`). The
        # exit code is the whole point of this guard, so a code that says "could not
        # run" must survive its own teardown.
        _detach_stdout(out)
        print(f"census: could not write to stdout: {err} — this is 'could not run', "
              "not drift", file=sys.stderr)
        return 2
    return 0


def _detach_stdout(out) -> None:
    """Re-point a failed stdout at devnull so Python's shutdown flush cannot re-raise."""
    with contextlib.suppress(OSError, ValueError, AttributeError):
        os.dup2(os.open(os.devnull, os.O_WRONLY), out.fileno())


def build_parser() -> argparse.ArgumentParser:
    """The CLI.

    ⛔ FACTORED OUT OF `main()` SO THE FLAG SET ITSELF CAN BE PINNED. The one-verb-per-
    command refusal is driven by `OUTPUT_VERBS`, and a pin that reads the parser's own
    `option_strings` is the only way to make a NEW flag a red instead of a silent
    output drop (`#14625` review, P2). Keeping the parser inline would leave that pin
    re-typing the flag list, i.e. asserting a copy against a copy.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--markdown", action="store_true", help="emit the appendix table")
    parser.add_argument("--json", action="store_true", help="emit machine form")
    parser.add_argument("--check", nargs="?", const=str(DOC_SRC.relative_to(ROOT)),
                        help="compare the SOURCE's pinned counts with this run (exit 1 on drift)")
    parser.add_argument("--render", nargs="?", const=str(DOC_OUT.relative_to(ROOT)),
                        help="write the document (source + this run's appendix) here")
    parser.add_argument("--all-surface", action="store_true",
                        help="census every tracked surface, not only the product")
    return parser


def _tracked_destination_refusal(target: Path, rel: str | None) -> int | None:
    """Refuse a destination that git TRACKS; `None` means the path is free.

    Answers the invariant `--render`'s output is never committed — the same one for a
    destination inside `ROOT` and for one in another checkout, so the probe follows the
    DESTINATION (`rel` when it is inside `ROOT`, otherwise the destination's own
    directory and basename). A destination in no repository at all answers
    "untracked" (git exits 128), which is the documented permit for a plain path.

    Returns 2 (could not run / refused) when git cannot be asked at all — an absent
    `git` binary raises `FileNotFoundError`, and an uncaught one would exit 1, the
    DRIFT code this module reserves for `--check`.
    """
    # `target.parent` can be a path that does not exist yet; `git -C` needs a
    # directory, so walk up to the nearest one that does (the write itself creates the
    # rest, and a directory that does not exist cannot hold a tracked file).
    probe_dir = target.parent
    while not probe_dir.is_dir() and probe_dir != probe_dir.parent:
        probe_dir = probe_dir.parent
    if rel is not None:
        probe_cwd, probe_path, label = ROOT, rel, rel
    else:
        probe_cwd, label = probe_dir, str(target)
        # ⛔ THE FULL PATH FROM THE NEAREST EXISTING ANCESTOR, not `target.name`
        # (#14625 review, round 5, P3): a destination whose parent directories do not
        # exist yet asked about its BASENAME in a directory that is not its parent, so a
        # peer repo tracking `sub/deep/x.md` answered "untracked" for `peer/sub/deep/x.md`
        # after `sub/` was removed, and `--render` landed a generated document on a
        # TRACKED path at rc 0. The invariant is about the path, so the probe names the
        # whole remainder — `git ls-files` matches an index entry whose work-tree file is
        # absent, which is exactly this case.
        probe_path = target.relative_to(probe_dir).as_posix()
    # ⛔ A PROBE THAT CANNOT RUN IS "COULD NOT RUN" (2), never 1. An absent `git`
    # binary raises FileNotFoundError here, and nothing catches it: the interpreter
    # then exits 1, which this tool reserves for the DRIFT verdict — so a lane reads
    # "the census drifted" from a box that could not answer at all (#13098 review, P2).
    # Same class, same status as every other refusal in this module.
    try:
        # ⛔ AND THIS PROBE MUST NOT BE ABLE TO FAIL DECODING EITHER. With
        # `core.quotePath=false` in the user's config, `git` emits RAW filename bytes on
        # stdout AND stderr, so a strict `text=True` decode raises
        # `UnicodeDecodeError` — a `ValueError`, not the `OSError` caught below — and a
        # deliberate "refusing to render over a TRACKED path" (2) became a traceback and
        # exit 1 (measured with `quotePath false` and a `caf\xe9.md`). The payload is
        # unused, so decoding is pure overhead that must never fail.
        tracked = subprocess.run(
            ["git", "ls-files", "--error-unmatch", "--", probe_path],
            cwd=probe_cwd, capture_output=True, text=True,
            encoding="utf-8", errors="surrogateescape",
            # ⛔ SCRUBBED, OR THE ANSWER DESCRIBES ANOTHER REPOSITORY (#14625 review,
            # P1): with `GIT_INDEX_FILE` pointing at a scratch index, this probe
            # answered "untracked" for a path this repository tracks and `--render`
            # overwrote it at rc 0 (measured: 14 -> 145,745 bytes) — the self-locking
            # corruption this refusal describes.
            env=_git_env(),
            # ⛔ AND BOUNDED (#14625 review, P3): this probe runs FIRST on every
            # `--render`, so an unbounded `git` here hangs the write path before the
            # metadata probe's own bound is ever reached — measured with a `git` that
            # sleeps: the tool outlived an external `timeout 90` (124).
            timeout=_GIT_PROBE_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        print(
            f"render: could not probe {label} for tracking: git did not answer within "
            f"{_GIT_PROBE_TIMEOUT_S}s",
            file=sys.stderr,
        )
        return 2
    except OSError as err:
        print(f"render: could not probe {label} for tracking: {err}", file=sys.stderr)
        return 2
    if tracked.returncode == 0:
        print(
            f"refusing to render over a TRACKED path ({label}): the document is\n"
            "`--render`'s output, which is never committed. Pick an untracked\n"
            "path (the default is gitignored).",
            file=sys.stderr,
        )
        return 2
    return None


def main(argv: list[str] | None = None) -> int:
    # ⛔ STDOUT MUST NOT BE ABLE TO FAIL ENCODING. A non-UTF-8 tracked filename reaches
    # the report/table as a surrogate escape, and under a real UTF-8 locale
    # (`en_US.UTF-8`, macOS's default) `sys.stdout.errors` is `strict`, so `print(...)`
    # raised `UnicodeEncodeError` and the bare verb exited 1 — the drift code — while
    # the same run under `C.UTF-8` exited 0. A locale-dependent exit status is worse
    # than a mis-rendered byte. (No effect where stdout has no `reconfigure`.)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="backslashreplace")
    args = build_parser().parse_args(argv)

    # ⛔ NEITHER VERB MAY RUN THE WIDE CENSUS UNDER THE SOURCE'S PRODUCT PINS.
    # The source's 25 pins are PRODUCT-surface counts (`census(("tortoise",))`),
    # and `--render` appends the run's rows under those pins. Rendering the
    # all-surface census would write a document whose §0 `sites` pin — a
    # PRODUCT-surface count — sits above an appendix several times longer,
    # print success, and be self-contradictory — a false pass in
    # prose, which is the class this file exists to refuse. The refusal is loud
    # rather than a silent scope-narrowing: a lane that asked for the wide census
    # and got the product one would believe it had rendered the wide table.
    if args.all_surface and (args.render is not None or args.check is not None):
        verb = "--render" if args.render is not None else "--check"
        print(
            f"`{verb} --all-surface` contradicts the source's pins: the 25 pins are\n"
            "PRODUCT-surface counts, so the wide census can never satisfy --check and\n"
            "would sit under an appendix that contradicts it under --render. Take the\n"
            "wide census from `--markdown --all-surface` (or `--json`) instead.",
            file=sys.stderr,
        )
        return 2

    # ⛔ THE TWO VERBS ARE TWO RUNS, AND A COMBINATION MUST NOT SILENTLY DROP ONE.
    # `--check`'s branch returns before `--render`'s, so `--check --render /tmp/x.md`
    # printed `check: 25 pinned counts match`, exited **0**, and NEVER WROTE the file
    # (measured at #14625's round-4 review). That is the no-op-gate shape this module
    # exists to refuse: a lane running §7's two halves as one command reads a green
    # check and believes it has regenerated the document. No §7 sentence declares a
    # precedence, so none is invented here — the pair is refused (2 = could not run)
    # and the message names the two-step form, which is §7's own order.
    if args.check is not None and args.render is not None:
        print(
            "`--check` and `--render` are two runs: --check's branch returns before\n"
            "--render's, so combining them would silently DROP the write. Run\n"
            "`--render` first and `--check` second (that is §7's order), one per\n"
            "command.",
            file=sys.stderr,
        )
        return 2

    # ⛔ THE REFUSAL ABOVE COVERS ONE PAIR; THE INVARIANT IT STATES IS ABOUT THE SET
    # (#14625 review, P2). The comment above says "a combination must not silently
    # drop one", but the `if` tested `--check`+`--render` ALONE, so three more pairs
    # were accepted and each ran ONE verb and discarded the other with **rc 0** —
    # measured on the shipped tree: `--check --json` printed `check: 25 pinned counts
    # match` and emitted no JSON, `--check --markdown` printed the same line and no
    # table, and `--json --markdown` returned the JSON payload for a caller who asked
    # for the appendix table (a lane redirecting that saves a `.json` body as `.md`).
    # That is the "gate that silently does nothing" class this module exists to
    # refuse, and it is a CLASS rather than a pair: every one of these verbs owns its
    # own output stream, so no precedence exists to apply and none is invented here —
    # a second one is refused (2 = could not run). `--all-surface` is a MODIFIER, not
    # an output verb, so `--markdown --all-surface` / `--json --all-surface` stay
    # legal; `--check --all-surface` is refused separately above.
    # ⛔ MEMBERSHIP IS RESOLVED FROM THE PARSED NAMESPACE, NOT FROM A SECOND HAND LIST.
    # A verb counts as SELECTED when argparse gave it something other than its absent
    # value — `None` for the `nargs="?"` verbs, `False` for the `store_true` ones — so
    # adding a verb to `OUTPUT_VERBS` (which a test forces for any new parser flag)
    # needs no edit here: this line covers it by construction. A hand-written mapping
    # would be the same pair-vs-class defect one level down, which is the defect this
    # refusal was rewritten to remove.
    _output_verbs = [
        verb for verb in OUTPUT_VERBS
        if getattr(args, verb.removeprefix("--").replace("-", "_")) not in (None, False)
    ]
    if len(_output_verbs) > 1:
        print(
            "these verbs each own their own OUTPUT, so a combination would silently\n"
            f"drop all but one: got {' + '.join(_output_verbs)}. Run one per command —\n"
            f"`{'`, `'.join(OUTPUT_VERBS)}` are {len(OUTPUT_VERBS)} runs, and\n"
            "`--all-surface` is the modifier that may accompany `--markdown`/`--json`.",
            file=sys.stderr,
        )
        return 2

    # ⛔ `--render` MUST NOT OVERWRITE A TRACKED PATH. The refusal inside `render()`
    # guards the SOURCE'S CONTENT (a source that already carries rows), never the
    # DESTINATION — so `--render docs/architecture/CYPHER-INVENTORY.src.md` read the
    # clean source, passed that refusal, and wrote source + the appendix rows over the tracked
    # file. That is self-locking: afterwards `--check` exits 1 ("carries N census
    # row(s)") and `--render` exits 2 (refuses the rowed source), so the tool can
    # neither grade nor repair the tree it just corrupted. A mistyped path is the
    # plausible route here — §7 tells every lane to run this verb — and the same
    # clobber applies to any other tracked doc. Refused before the census runs, so the
    # refusal costs nothing. `git ls-files` answers "is this path in the index", which
    # is exactly the invariant (`--render`'s output is never committed); outside a git
    # work tree (exit 128) it simply does not fire.
    if args.render is not None:
        # ⛔ AND A DESTINATION THAT CANNOT BE RESOLVED IS "COULD NOT RUN" (2) TOO.
        # `Path.resolve()` raises `RuntimeError("Symlink loop …")` on ELOOP — NOT an
        # OSError — and it runs before every guard, so the traceback escaped and the
        # interpreter exited 1, the DRIFT code, from a verb that has no drift verdict
        # (measured with a two-symlink loop: --render /tmp/loop/a/deep.md → rc 1).
        try:
            target = (ROOT / args.render).resolve()
            src_resolved = DOC_SRC.resolve()
        except (OSError, RuntimeError) as err:
            print(f"render: could not resolve {args.render}: {err}", file=sys.stderr)
            return 2
        # ⛔ A DIRECTORY IS REFUSED AS A DIRECTORY, not as a "TRACKED path".
        # `git ls-files --error-unmatch -- <dir>` exits 0 for any directory that
        # merely CONTAINS tracked files, so the probe below claimed
        # `--render docs/architecture` and prescribed "Pick an untracked path", while
        # the real error is EISDIR — which the write guard names only for a directory
        # holding no tracked file (measured at #14625's round-4 review: exit 2 in both
        # cases, so the exit STATUS was right and the diagnosis was not).
        if target.is_dir():
            print(
                f"render: could not write {args.render}: [Errno 21] Is a directory.\n"
                f"{target} is a DIRECTORY — --render needs a FILE destination; the\n"
                "default writes the gitignored `CYPHER-INVENTORY.md`.",
                file=sys.stderr,
            )
            return 2
        if target == src_resolved:
            print(
                f"refusing to render over the SOURCE ({args.render}): the document is\n"
                "`--render`'s output and the source is hand-authored. Pick another\n"
                "path (the default writes the gitignored `CYPHER-INVENTORY.md`).",
                file=sys.stderr,
            )
            return 2
        # ⛔ A HARDLINK IS GIT METADATA WEARING A FOREIGN PATH, and every guard above
        # decides metadata-ness from the PATH (#14625 review, P3 — the last residual of
        # the destination guards). A hardlink has TWO names for ONE inode, so
        # `ln -f <repo>/.git/config /tmp/x/doc.md` then `--render /tmp/x/doc.md` carries
        # no `.git` component, is untracked, and sits in no repository: all three guards
        # permit it and the write lands THROUGH the link on the config. Measured at
        # `059a0382` (this head, after every other guard was in place): rc **0**,
        # `render: wrote 2451 lines`, and the repository's 92-byte config became
        # 391,249 bytes — `git -C <repo> rev-parse --git-dir` then answered 128, "bad
        # config line 1", on a repository no git command can restore.
        #
        # The property is the LINK COUNT, not the metadata: a destination with a second
        # name is written through to that name wherever it lives (a tracked doc, a peer
        # checkout, a repository's metadata), which is the one thing a GENERATED document
        # must never do. `Path.resolve()` cannot answer this — it follows symlinks, and a
        # hardlink is not one — so it is `stat`d.
        #
        # A destination that does not exist yet has no inode to share and needs no check;
        # a `stat` that fails for any OTHER reason is refused, for the same reason every
        # unanswerable probe in this module is: a false refusal costs a re-run with
        # another path, a false permit costs a file nothing restores.
        try:
            links = target.stat().st_nlink
        except (FileNotFoundError, NotADirectoryError):
            # No inode yet: nothing to write through. `NotADirectoryError` is routed to
            # the WRITE rather than refused here on purpose — the destination's parent
            # is a regular file, and the write guard below names that failure
            # (`could not write … [Errno 20] Not a directory`) instead of this one.
            links = 1
        except OSError as err:
            print(f"render: could not inspect {args.render}: {err}", file=sys.stderr)
            return 2
        if links > 1:
            print(
                f"refusing to render over {args.render}: {target} has {links} hard\n"
                "links, so the document would be written THROUGH every other name of\n"
                "that file. A hardlink to a repository's metadata destroys it and git\n"
                "cannot restore it. Pick a path with exactly one link (the default\n"
                "writes the gitignored `CYPHER-INVENTORY.md`).",
                file=sys.stderr,
            )
            return 2
        try:
            # `ROOT.resolve()` is inside the guard too: a symlink loop on the tree root
            # raises `RuntimeError` here, and only `ValueError` (path outside ROOT) was
            # ever caught.
            rel = target.relative_to(ROOT.resolve()).as_posix()
        except (ValueError, OSError, RuntimeError):
            rel = None
        # ⛔ THE TRACKED-PATH GUARD FOLLOWS THE DESTINATION, NOT `ROOT` (#14625 review,
        # P2). It used to answer only when the target sat INSIDE `ROOT` (`rel` is not
        # None), so `--render` into ANOTHER checkout's tracked file never asked anybody
        # and overwrote it at rc 0 (measured: 27 -> 145,745 bytes). The index that
        # matters is the one the DESTINATION belongs to, so the probe runs from the
        # destination's own directory with its basename.
        refusal = _tracked_destination_refusal(target, rel)
        if refusal is not None:
            return refusal

        # ⛔ GIT METADATA IS NOT A DESTINATION EITHER, and the probe above cannot see
        # it: `git ls-files` answers "is this path in the INDEX", so `.git/config`,
        # `.git/index`, `.git/HEAD` and every ref are UNTRACKED by construction and
        # pass that probe. Measured: `--render .git/config` in a scratch repo read the
        # source, passed both earlier guards, and exited **0** having written the
        # document over a 92-byte repository config (144,193 bytes after) — silent
        # destruction that `git` cannot restore, from a verb §7 tells every lane to
        # run. So this guard asks git where the metadata LIVES rather than asking the
        # index: `--git-dir` and `--git-common-dir` one call, because a linked
        # worktree's refs are shared with the main checkout, so the common dir is a
        # second answer. ⛔ A probe that CANNOT be answered is refused below rather than
        # read as harmless: the tracked-path probe's "not a repo" pass is not the same
        # case, because a tracked path cannot exist outside a repository while git
        # metadata can (measured at `bf0dbcf9`: forcing the probe to exit 128 made the
        # guard a no-op and `--render` wrote over a `--separate-git-dir` repo's config).
        #
        # ⛔ AND THE PROBE ALONE IS NOT THE INVARIANT — it is the DIRECTORY case of it.
        # A LINKED WORKTREE'S `.git` IS A REGULAR FILE holding `gitdir: …`, and it is
        # NOT inside either dir the probe reports (measured: `git rev-parse --git-dir
        # --git-common-dir` from a worktree answers `<common>/worktrees/<name>` and
        # `<common>`, while the link is `<worktree>/.git`). Measured at `037d0c68`: in a
        # real `git worktree add`, `--render .git` printed `render: wrote 4 lines`,
        # exited **0**, replaced that file, and every later `git` call in the worktree
        # answered **128** — the worktree detached from its repository, with no `git`
        # command to restore it. So a destination carrying a `.git` COMPONENT is refused
        # as metadata too, and that half needs no subprocess (which is also why it is
        # decided first: it cannot be made to depend on git answering).
        metadata_dirs = []
        # ⛔ AND THE PROBE IS RUN FROM BESIDE THE TARGET, NOT FROM `ROOT` — `--render`
        # takes any absolute path, and a destination inside ANOTHER checkout's git
        # directory carries no `.git` component when that repo was made with
        # `--separate-git-dir`. Measured at `bf0dbcf9`: probing ROOT answered "not
        # metadata" for a second repo's separate git dir and `--render` wrote the
        # document over that repo's `config`. Measured too that a probe from the
        # target's own parent DOES see it: `git -C <separate-git-dir> rev-parse
        # --git-dir` answers `.`, and from a subdirectory of it, that directory.
        #
        # ⛔ CASE-INSENSITIVE ON PURPOSE: on a case-insensitive filesystem `.GIT/config`
        # resolves to the real `.git/config`, so a case-sensitive component test lets it
        # through.
        dot_git_at = [i for i, part in enumerate(target.parts) if part.lower() == ".git"]
        if dot_git_at:
            metadata_dirs.append(Path(*target.parts[: dot_git_at[0] + 1]))
        else:
            probe_cwd = target.parent
            while not probe_cwd.is_dir() and probe_cwd != probe_cwd.parent:
                probe_cwd = probe_cwd.parent
            try:
                meta_probe = subprocess.run(
                    ["git", "rev-parse", "--git-dir", "--git-common-dir"],
                    cwd=probe_cwd, capture_output=True, text=True,
                    encoding="utf-8", errors="surrogateescape",
                    # ⛔ SCRUBBED, OR THE GUARD PROTECTS THE WRONG REPOSITORY (#14625
                    # review, P1): with `GIT_DIR=<other>`, this probe answered 0 with
                    # the OTHER checkout's metadata, so `metadata_dirs` named a
                    # directory the destination is not inside and the write landed on
                    # an INTACT `<separate-git-dir>/config` at rc 0 (92 -> 145,745
                    # bytes; `git -C <repo> rev-parse` then answered 128).
                    env=_git_env(),
                    # ⛔ AND A PROBE THAT NEVER ANSWERS IS BOUNDED, NOT AWAITED (#14625
                    # review, P3). `timeout=` is what makes the "a timeout — REFUSES"
                    # sentence below true: measured at `9e477cdb` with a `git` that
                    # sleeps, `--render` had to be killed externally (124).
                    timeout=_GIT_PROBE_TIMEOUT_S,
                )
            except subprocess.TimeoutExpired:
                # ⛔ `TimeoutExpired` is NOT an `OSError`, so it does not reach the
                # handler below: without this arm a hung git raises out of `main()` and
                # the refuse path is never taken.
                print(
                    f"refusing to render over {args.render}: git did not answer within "
                    f"{_GIT_PROBE_TIMEOUT_S}s whether it is GIT METADATA\n"
                    "Git cannot restore what --render writes over, so a probe that "
                    "never answers is refused rather than awaited.\n"
                    "Pick another path (the default writes the gitignored "
                    "`CYPHER-INVENTORY.md`).",
                    file=sys.stderr,
                )
                return 2
            except OSError as err:
                print(
                    f"render: could not probe {args.render} for git metadata: {err}",
                    file=sys.stderr,
                )
                return 2
            # ⛔ AN EMPTY LINE IS NOT `probe_cwd`: `Path("")` resolves to the current
            # directory, so an unanswered probe would refuse EVERY destination —
            # fail-closed in the wrong direction, and silently.
            for line in meta_probe.stdout.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    metadata_dirs.append((probe_cwd / line).resolve())
                except (OSError, RuntimeError):
                    continue
            # ⛔ A NON-ZERO PROBE IS "COULD NOT DETERMINE", NOT "NOT METADATA". The one
            # honest permit is git's own `not a git repository` with no `.git` entry on
            # any ancestor (a plain directory is a legitimate destination). Every other
            # failure — a broken git, an unreadable layout, a timeout — REFUSES: a false
            # refusal costs a re-run with another path, a false permit costs a repository
            # git cannot restore.
            if meta_probe.returncode != 0:
                stderr_text = meta_probe.stderr or ""
                # ⛔ AND "NOT A REPOSITORY" IS NOT THE SAME AS "NOT METADATA". A
                # git directory whose own HEAD/refs were damaged answers exactly like
                # a plain directory, so a `.git`-entry test let `--render <bare>/config`
                # through at rc 0 and git could not restore it (#14625 review, P1,
                # reproduced with HEAD removed: a 66-byte bare config -> 145,745 bytes).
                # The structural term needs no subprocess — which is the point, since the
                # whole reason this branch runs is that git could not answer.
                #
                # ⛔ AND THE CLASS IS A REPOSITORY'S METADATA, NOT EVERY GIT FILE: a
                # user-level `$HOME/.gitconfig` carries no `objects/`/`refs/`/`HEAD`
                # marker, so it is permitted (measured, round 5: rc 0, 23 -> 145,745
                # bytes — git cannot restore that one either). Stated rather than
                # implied, because the refusal message above claims the class.
                ancestors = (target.parent, *target.parent.parents)
                plainly_outside = (
                    "not a git repository" in stderr_text
                    and not any((parent / ".git").exists() for parent in ancestors)
                    and not any(_looks_like_git_metadata(parent) for parent in ancestors)
                )
                if not plainly_outside:
                    first = stderr_text.strip().splitlines()
                    print(
                        f"refusing to render over {args.render}: git could not say "
                        "whether it is GIT METADATA\n"
                        f"(`git rev-parse` from {probe_cwd} exited "
                        f"{meta_probe.returncode}"
                        + (f": {first[0]}" if first else "")
                        + ").\nGit cannot restore what --render writes over, so an "
                        "unanswerable probe is refused rather than read as harmless.\n"
                        "Pick another path (the default writes the gitignored "
                        "`CYPHER-INVENTORY.md`).",
                        file=sys.stderr,
                    )
                    return 2
        for meta in metadata_dirs:
            if target == meta or meta in target.parents:
                print(
                    f"refusing to render over GIT METADATA ({args.render}):\n"
                    f"{target} is inside this repository's git directory "
                    f"({meta}).\nThat is not a document destination, and git "
                    "cannot restore what --render writes over. Pick another\n"
                    "path (the default writes the gitignored `CYPHER-INVENTORY.md`).",
                    file=sys.stderr,
                )
                return 2

    prefixes: tuple[str, ...] = ("tortoise",)
    if args.all_surface:
        prefixes = (".",)

    # ⛔ EVERY MODE THAT CANNOT ENUMERATE ANSWERS 2, NEVER 1. `surface_files()` runs
    # `git ls-files … check=True`, so on a box without a `git` binary or without an
    # index it raises (`FileNotFoundError`, `CalledProcessError` 128) — and an
    # uncaught exception exits the interpreter with 1, the code this tool reserves
    # for the DRIFT verdict. `--render` has no drift verdict at all, and `--check`
    # would then say "the census drifted" about a box that could not answer, sending
    # a lane to re-cut 25 pins that were correct. The probe wrapper below closes this
    # class for `git ls-files --error-unmatch`; this one closes it for the census's
    # own enumeration, which runs on EVERY path.
    try:
        result = census(prefixes, drop_test_modules=not args.all_surface)
        # ⛔ `--json` NEEDS A SECOND, UNGUARDED-UNTIL-NOW ENUMERATION: `rg_totals()`
        # re-reads the index through `surface_files()`, so a PARTIAL failure (the first
        # walk succeeds, the second is refused — a transient EAGAIN, an index that
        # becomes unreadable mid-run) escaped this guard and exited 1, the DRIFT code,
        # from a verb that has no drift verdict. Measured with a `git` shim that
        # delegates the first call and exits 128 on the second. It is inside the guard
        # for the same reason as the first: this tool's "could not run" must be total.
        rg = (
            rg_totals(prefixes, drop_test_modules=not args.all_surface)
            if args.json
            else None
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as err:
        print(
            f"census: could not enumerate {prefixes}: {err} — this is 'could not "
            "run', not drift",
            file=sys.stderr,
        )
        return 2

    if args.check is not None:
        return check(ROOT / args.check, result)
    if args.render is not None:
        out = ROOT / args.render
        text = render(DOC_SRC, result)
        # ⛔ AND NEITHER DOES A WRITE THAT CANNOT HAPPEN. `--render` has NO drift
        # verdict, so an uncaught IsADirectoryError/PermissionError/OSError (read-only
        # mount, ENOSPC) exits 1 — the status this tool reserves for "the pins drifted"
        # — and §7 tells every lane to run this verb, so a lane that rendered into a
        # path it cannot write re-cuts 25 correct pins. "Could not run" is 2, and the
        # message names the path and the error (#13098 review, P2).
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
            # ⛔ THE TABLE CAN CARRY A SURROGATE-ESCAPED PATH (a non-UTF-8 tracked
            # filename), which strict UTF-8 cannot ENCODE: `write_text` then raised
            # `UnicodeEncodeError` — a `ValueError`, not an `OSError` — and left an
            # EMPTY file behind while exiting 1, the drift code, from a verb with no
            # drift verdict. `backslashreplace` renders such a byte as text, and the
            # guard names `UnicodeError` so neither half can escape.
            out.write_text(text, encoding="utf-8", errors="backslashreplace")
        except (OSError, UnicodeError) as err:
            print(f"render: could not write {out}: {err}", file=sys.stderr)
            return 2
        return emit(f"render: wrote {len(text.splitlines())} lines to {out}")
    if args.json:
        payload = {
            "prefixes": result["prefixes"],
            "files_scanned": result["files_scanned"],
            "files_with_sites": result["files_with_sites"],
            "sites": len(result["sites"]),
            "non_cypher_door_calls": result["non_cypher_door_calls"],
            "unparsed_files": result["unparsed_files"],
            "shape_matrix": shape_matrix(result["sites"]),
            "verdicts": dict(Counter(s.verdict for s in result["sites"])),
            "partial": result["partial"],
            "rg_totals": rg,
            "rows": [s.as_row() for s in sorted(result["sites"], key=lambda s: (s.path, s.line))],
        }
        return emit(json.dumps(payload, indent=2, sort_keys=True))
    if args.markdown:
        return emit(markdown_table(result["sites"]))
    # ⛔ AND THE BARE VERB ENUMERATES TWICE TOO: `report()` calls `rg_totals()`, which
    # re-reads the index — outside the guard above, so a PARTIAL failure exited 1, the
    # DRIFT code, from the DEFAULT verb (measured with a `git` shim that delegates the
    # first `ls-files` and refuses the second).
    try:
        text = report(result)
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as err:
        print(f"census: could not enumerate: {err} — this is 'could not run', not drift",
              file=sys.stderr)
        return 2
    return emit(text)


if __name__ == "__main__":
    raise SystemExit(main())
