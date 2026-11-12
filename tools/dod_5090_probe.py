"""#10465 — the read-only instrument that scores `#5090`'s four DoD bullets.

**Why this exists.** `#5090`'s epic body has never been re-verified since it was
written. Three of its four `Definition of done` bullets have had work land
beneath them (`#4997`, `#4999`, `#5407`, `#5008` all CLOSED) while the body still
says *"there is no rule saying what deserves an embedding. That absence **is**
the epic"* — a sentence the tree contradicts (`tortoise/embed_gate.py`).
The stale-flag class this repo has already paid for once is exactly this: an
epic whose state nobody can read, and a reader forced to guess. This tool is the
instrument that ends the guessing — it MEASURES, it never changes a gate.

⛔ **This module is read-only and must stay so.** It imports the gates to score
them; it never writes a graph property, never flips a flag, and never creates an
index. `#10465`'s scope is measurement plus the epic body's own text; a bullet
that scores `NOT MET` is a FINDING to file, never a fix to smuggle in here.

## The four bullets and how each is scored

`#5090` `## Definition of done` states, verbatim:

1. *"A stated, enforced rule for what earns an embedding."*
2. *"No vector is stored without a path to query it."*
3. *"A fallback query reports the fallback."*
4. *"V1 is decided and implemented."*

Each row is `MET` / `PARTIAL` / `NOT MET` **with a command and its observed
output** — a bullet without a command is unscored, not met. The scoring is
deliberately mechanical so a later lane re-runs it instead of re-reading prose:

| bullet | the command | what makes it `MET` |
|---|---|---|
| 1 | `uv run python tools/dod_5090_probe.py --json` (reads the two gate modules + counts the write seams that reach the gate) | a stated predicate exists, ONE rule set (identity, not a copy), AND every embedding write path reaches it with the gate ON by default |
| 2 | same (`written labels ⊆ served labels`) | every label a write path embeds is one `run_vector_query` serves |
| 3 | same (forces all three mechanisms through `run_vector_query`) | `index` / `scan` / `scan_fallback` are distinguishable in the caller's trace |
| 4 | same (searches for a V1 ruling) | **`MET` is unreachable here**: the bullet is *decided **and implemented***, and this row cannot measure implementation — a record at most carries it to `PARTIAL`, and no record is `NOT MET` |

⚠️ **Bullet 1's `PARTIAL` is the honest answer at HEAD and it is not a bug
report.** The rule is stated (`tortoise/embed_gate.py::embedding_verdict`) and
wired at exactly one seam (`tortoise/embeddings.py::encode_for_row`, reached by
all five entity writers), but its enforcement is behind `TORTOISE_EMBED_GATE`,
which is **unset in every shipped configuration** — so at default the epic's
`enforced` half is an opt-in, not the operative rule of the write path. `#5008`
chose that ordering on purpose (*"the rate effect is measurable before it is
trusted"*); the instrument records the consequence rather than re-deciding it.

## §12.2a — the distinct-predicate count

`STORAGE-ARCHITECTURE.md` §12.2a forbids *"a second, independently-derived rule
set"* for `EMBED`. The instrument counts the **predicates that can answer
`NO-EMBED`**, mechanically: the modules that **own identifier patterns**
(`re.compile` at module scope) over the population `predicate_population()`
returns — the modules the gate **imports** (walked, so a new
`embed_rules.py` counts the day it exists) **plus** the seam modules that decide
whether the gate is consulted at all (`OWNERSHIP_ONLY`, scanned for their own
patterns with their imports NOT followed). The target is **1**,
`value_gate.identifier_only`, asked by `embed_gate.embedding_verdict` — which is
the ASK, not a second predicate, and must report the *shared* rule id. A second
pattern list is the drift the criterion exists to prevent, and it makes this
count read `2`.

⚠️ **Two ways the population can be right and the verdict still unearned,**
both closed here: a module that will not **parse** (or will not read) is a hole,
not a zero, so the verdict is `UNDETERMINED` rather than `MET`; and the seam that
**decides whether to consult the gate** cannot be reached by walking the gate's
own imports — an import makes the *importer* depend on the *importee* — so it is
scanned for ownership separately.

## The census — `stored` / `indexed` / `unindexed` per label

`#5090`'s premise figures (`12,657` embeddings, *"exactly ONE vector index"*) are
2026-09-24 numbers and are **not to be re-quoted**. `--census` re-measures them
on a **named graph** at a **timestamp**: per label, the label's embedding-bearing
node count (`stored`), whether the engine's catalogue holds a `VECTOR` index for
it (`indexed`), the unindexed remainder, and bytes — with the engine's own
per-index size counters reported **and labelled as blind** (§12.1a measured
`GRAPH.MEMORY USAGE`'s index share at 2.00 MB for 43.28 MiB, 21.6× under).

⛔ **A `stored`-only figure is the 2026-09-24 error repeated.** Both columns are
stated, or the label is `UNMEASURED`. Never `0`: an unreachable store and an
empty one are different facts, and `#5090` is a measurement issue whose whole
failure mode is a body that reads as measured without being measured.

⛔ **The reads go through `GRAPH.RO_QUERY` and the labels are quoted.** A label
cannot be a query parameter and its value comes from the graph's own schema, so
it is quoted with the repo's one identifier rule
(`graph_key_restore._quote_identifier`) rather than concatenated raw; and the
read-only verb makes "this module stores nothing" the engine's rule instead of
this file's memory.

### …and WHICH `entity_type` values stop answering (`#11088`)

A row's `unindexed` count is not the surface consequence. The consequence is a
**set of `entity_type` values**, and the label→type relation is not one-to-one:
`security.entity_label` sends `document`+`source` to `:Source` and
`point`+`operator` to `:Point`. So every row carries
`affected_entity_types` (derived from `VALID_ENTITY_TYPES` through that one
translation) and `removal_changes_query_results`, and the census carries a
top-level `surface_consequence` naming the MCP tools and REST route that can
hand `entity_type` to the vector leg — **derived from
`tool_registry.TOOL_REGISTRY` AND the handlers' own bodies (an AST scan of
`tortoise/mcp_server.py`), not listed here**, so the consequence tracks the
surface instead of a copy of it. ⛔ Both sources are load-bearing and neither is
redundant: the registry binding alone names one tool where the surface has two
(`tortoise_query` binds `paginated_query` yet forwards a `text=` argument into the
same method).

⛔ `surface_consequence["requested_not_performed"]` is `True` because this
instrument **reports**; the removal is an MCP/SDK surface change and needs the
owner's approval BEFORE it is implemented (`AGENTS.md`). There is no flag on
this tool that removes anything.

⛔ **Row 1 reads the PRODUCT'S DECLARED WRITER REGISTER (`#11444`).**
`tortoise/embed_writer_register.py` (`#11085`) is where an exception to "every
embedding write routes through the gate" is NAMED with an owning issue —
reading (b), recorded on `#11085`. Until `#11444` this instrument only mentioned
that file in comments, so a named `exempt` writer and a real `bypass` were the
same string to it and DoD row 1 could never reach `MET`.

What the register IS for here: it CLASSIFIES the population, it does not replace
it. The over-inclusive AST enumeration is still printed in full. What the
register is NOT: the measurement. A different question — *does every writer
route through the gate?* — is still answered from source, because a declaration
is the product ACCEPTING an exception, never the instrument proving there is
none.

⛔ **EXACTLY WHAT A REGISTER EDIT BUYS — the precise contract, because the loose
version of this paragraph was false for the case that matters.**

* A name with **no writer behind it in the population row 1 scores** is STALE and
  HOLDS the row (the same "unused suppression" contract ESLint enforces with a
  non-zero exit). That is what a name for a writer **the product does not have**
  always is — and it is also what a name becomes once that writer is FIXED to
  route through the gate, so a declaration does not quietly accumulate: delete
  it then, because an unused exemption is a stale one. It is NOT what naming a
  LIVE outside-gate writer is — that is the next bullet, and it greens the row.
* A `bypass` class, any class outside the vocabulary, or a declaration carrying
  no owning issue (`#<n>`) HOLDS the row: the first two are a DEFECT, and an
  acceptance with no owner behind it is not an acceptance. An unreadable or
  malformed register holds it as `UNMEASURED`.
* A `recognised_producer` claim is CORROBORATED against the writer's own body
  (the seams `route_calls()` reads from the register, matched **by callee
  NAME** — the register exports names, not objects), and every `reason` must
  cite the writer ITSELF rather than another function's evidence.
* ⛔ **Naming a LIVE writer that discovery DOES find outside the gate greens the
  row — `exempt` included.** That is reading (b) doing its job: *"a named,
  accepted exception in the product"* IS the acceptance. Nothing corroborates an
  `exempt` entry beyond its citation, because "this writer stores no live
  vector" (or "stores one under a ruling") needs data flow to falsify, which
  this instrument refuses to guess at. **So `MET` on this row means** *every
  writer discovery finds outside the gate is gated or NAMED* — **never** *no
  writer bypasses*. `tortoise/embed_writer_register.py` is PRODUCT CODE that
  needs review; it is not a suppression file anyone may append to.

⛔ And the residual on the other side of the same row, which the register's own
docstring refuses to let `MET` absorb: a writer discovery classes `gated`
(`tortoise.sdk::create_point`, `::update_point`) may still store a caller-supplied
`embedding` VERBATIM by a recorded decision (PR `#3018` review P2). This row
measures the WRITER population; it never claims every stored vector was decided
by the rule. `ROW_SCOPE_NOTE` carries that sentence into the row's evidence, so a
reader quoting the verdict cannot read it as a closure proof.

Usage::

    uv run python tools/dod_5090_probe.py                     # the four rows
    uv run python tools/dod_5090_probe.py --json              # same, machine-readable
    uv run python tools/dod_5090_probe.py --census \\
        --uri docker://:pw@localhost:6379/<graph> --graph <graph>

Exit status: `0` when the instrument ran (whatever the verdicts — a red row is a
successful measurement), `2` when the instrument itself could not measure:
a gate module it scores will not import, a `--census` URI whose engine has no
index API, or a census whose catalogue read came back `UNMEASURED`. Never a
verdict-derived code: a caller must not be able to read `NOT MET` as "the tool
failed".
"""

from __future__ import annotations

import sys

#: `#5128`'s contract, applied to THIS entry point: refuse an interpreter older
#: than the floor BEFORE any other import. The guard must also be PROTECTIVE
#: rather than documentary — nothing below may use 3.12-only SYNTAX, or the file
#: would die in the tokenizer and never reach this statement. That is why the
#: two nested same-quote f-strings in `score_rule_bullet` are built as plain
#: locals: PEP 701 (`f"…{d['at']}…"` inside a `"`-delimited f-string,
#: https://peps.python.org/pep-0701/ — *Python-Version: 3.12*, "quote reuse")
#: cannot be tokenized before 3.12, and `tests/test_dod_5090_probe.py::
#: test_the_tool_parses_under_the_floor` pins that.
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/dod_5090_probe.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/dod_5090_probe.py`"
    )

import argparse
import ast
import contextlib
import functools
import json
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

VERDICTS = ("MET", "PARTIAL", "NOT MET")

#: `#5090`'s four bullets, verbatim from the epic body. Held here so the table
#: cannot silently score a re-worded bullet: the pin
#: `tests/test_dod_5090_probe.py::test_the_four_bullets_are_scored_verbatim`
#: reads them back and the epic body is the source of truth.
BULLETS: tuple[str, ...] = (
    "A stated, enforced rule for what earns an embedding.",
    "No vector is stored without a path to query it.",
    "A fallback query reports the fallback.",
    "V1 is decided and implemented.",
)

#: The one predicate §12.2a permits, and the module that owns its patterns.
PREDICATE_MODULE = "tortoise/value_gate.py"
PREDICATE_NAME = "value_gate.identifier_only"

#: The one enforcement seam. Every embedding write path must reach THIS
#: function, not the raw primitive — the seam `#5008` built.
SEAM = "tortoise/embeddings.py::encode_for_row"

#: The command that produced a census row — printed WITH the row, so a figure
#: quoted into an epic body carries the way it was produced (#10465 target 1:
#: "a bullet without a command is unscored").
CENSUS_COMMAND = (
    "uv run python tools/dod_5090_probe.py --census "
    "--uri docker://:pw@host:6379/<graph> --graph <graph>"
)


@dataclass
class Row:
    """One scored bullet: verdict + the command that produced it + its output."""

    bullet: int
    text: str
    verdict: str
    command: str
    observed: str
    notes: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.verdict not in VERDICTS:
            raise ValueError(f"verdict must be one of {VERDICTS}, got {self.verdict!r}")


# ── Bullet 1 — "a stated, enforced rule" ───────────────────────────────────


#: The GATED seam (`#5008`). A write path that embeds a row must reach it.
SEAM_CALL = "encode_for_row"

#: The ungated primitives that ALSO produce a stored vector. `encode_for_store`
#: is documented as the reads/backfills/repairs primitive (so its callers are
#: `producer`, not `writer`), but `compute_session_embedding` feeds the session
#: `:Event` writers — a write path that never touches the gate. Both are
#: measured, because "a NEW writer that calls the raw primitive instead" is
#: invisible to a map that only ever looks for the gated call (this tool's own
#: review found exactly that: `session_indexer.main` and
#: `sdk._session_embedding` embed while containing neither gate symbol).
UNGATED_CALLS: tuple[str, ...] = ("encode_for_store", "compute_session_embedding")

#: `MERGE (n:Label …)` / `CREATE (p:Label …)` — the label the WRITE names.
#: ⛔ A `MATCH` is a read, so it is deliberately NOT in this pattern. Counting it
#: was the comment/code disagreement review round 17 found (the comment said
#: MERGE/CREATE while the pattern also took MATCH), and the direction it fails
#: is the bad one: on the module-stem fallback a `MATCH (n:Point)`-shaped read is
#: the one path that can RESOLVE a label the writer never writes. Dropping it can
#: only turn a resolved label into `UNKNOWN(two labels: …)` / `UNKNOWN` (a false
#: `PARTIAL`, the safe direction), and it moves no label on this tree.
_NODE_LABEL = re.compile(r"(?:MERGE|CREATE)\s*\(\s*(\w+)\s*:\s*(\w+)")

#: An embedding WRITE — a Cypher property assignment or the clause helper.
_EMBED_WRITE = re.compile(
    r"(?:\w+\.embedding|(?<![\w.])embedding)\s*=|set_embedding_clause\("
)

#: The SECOND write shape: a CONTAINER KEY, `record["embedding"] = …` /
#: `p['embedding'] = …`. It puts a vector into a props/journal dict rather than
#: onto a graph property, so every alternate in `_EMBED_WRITE` misses it.
#: ⛔ It was INVISIBLE until `#11274` (found by `#11085`'s review, P2): a new
#: writer using this shape added NOTHING to the population, so the register's
#: "an undeclared writer cannot bypass silently" guard stayed green — the exact
#: hole `#11085` exists to close. The live instances it was missing are the
#: four named in `tortoise/embed_writer_register.py`'s container-key bullet
#: (`api::add_point`, `consistency::_graph_fingerprint`, `sdk::_index_gate_read`,
#: `sdk::_write_capture_turns`). ⚠️ `sdk._journal_event_raw_write` uses the same
#: container key but was ALREADY in the population for a different reason (the
#: `embedding=None` in its SIGNATURE matches `_EMBED_WRITE`), so it is not
#: evidence that the shape was seen.
#: Decided on the AST (`_writes_an_embedding`), NOT as an `_EMBED_WRITE`
#: alternate: a regex reads raw SOURCE, so `["embedding"] =` as a pattern also
#: matches PROSE quoting the shape, and this shape is exact enough to decide.
#: The MAP-MERGE shape (`SET n += $p`) stays deliberately OUT: whether the merged
#: map carries a vector is DATA FLOW, not syntax — 60 functions merge props and
#: almost none can carry one, so a syntactic rule here would force dozens of
#: declarations that assert nothing. That residual is named instead of guessed
#: (`UNDECIDABLE_WRITE_SHAPES`) and is why `sdk._update_entity` carries the NAMED
#: `#4094` exemption rather than being read as "no write".
_EMBED_CONTAINER_KEY = "embedding"

#: Write shapes discovery CANNOT decide, with the reason — named so the
#: register's claim can be scoped to the mechanism rather than wider than it
#: (`tests/test_embed_writer_register.py::test_the_register_names_the_shapes_its_discovery_cannot_see`).
UNDECIDABLE_WRITE_SHAPES: dict[str, str] = {
    "map-merge (`SET n += $p`)": (
        "whether the merged map carries an `embedding` is DATA FLOW, not syntax: "
        "60 functions in `tortoise/` merge a props map and almost none can carry a "
        "vector, so a syntactic rule would force dozens of declarations that assert "
        "nothing. The live instance is `sdk._update_entity`, which is DECLARED as an "
        "exempt writer under the NAMED `#4094` exemption instead"
    ),
}


def _writes_an_embedding(node, source: str) -> bool:
    """Whether ``node`` writes a vector — by clause, by clause helper, or into a
    CONTAINER KEY (:data:`_EMBED_CONTAINER_KEY`).

    ``source`` is the function's own text (by line range); the AST pass is what
    sees the container-key shape `_EMBED_WRITE` cannot.
    """
    if _EMBED_WRITE.search(source):
        return True
    for sub in ast.walk(node):
        if isinstance(sub, ast.Assign):
            targets = sub.targets
        elif isinstance(sub, (ast.AugAssign, ast.AnnAssign)):
            targets = [sub.target]
        else:
            continue
        for target in targets:
            for part in ast.walk(target):
                if (
                    isinstance(part, ast.Subscript)
                    and isinstance(part.slice, ast.Constant)
                    and part.slice.value == _EMBED_CONTAINER_KEY
                ):
                    return True
    return False

#: ── The RESOLVED census (#10541) ────────────────────────────────────────────
#: A three-name callee vocabulary cannot tell a legitimate exemption from a
#: bypass, so bullet 2 used to be held at `PARTIAL` over every function the
#: instrument could not resolve. Each such function is now carried by ONE of
#: the classes #10541 names, with the `path::symbol` that decides it. The entries
#: below were READ at their seam — not inferred from a name.
#:
#: ⛔ `path:LINE` IS NOT EVIDENCE, and this table learned that the expensive way:
#: the first version cited offsets, `sdk.py` then grew ~43 lines on `main`, and
#: every `tortoise.sdk::*` citation in it pointed at unrelated code — while
#: `#5090`/`#10541` quote this census as the justification for the classes
#: (review round 17, P3). Nothing executed the offsets (`_census_class` reads
#: `entry[0]` only), so they rotted silently. A SYMBOL is stable under an
#: unrelated edit and still fails when the seam leaves the function the entry
#: names; `CENSUS_EVIDENCE` carries one `(path, symbol, needle)` citation per
#: entry and `census_evidence_violations()` re-derives all of them, so the next
#: drift is a RED test rather than a claim nobody can check.
#:
#: ⚠️ `recognised_producer` here means "DIRECTLY reaches a recognised producer"
#: — the canonical clause builder (`set_embedding_clause`,
#: tortoise/vector_write.py) or the session producer (`_session_embedding` →
#: `compute_session_embedding`). It does NOT mean `encode_for_row`: NONE of the
#: six calls it, so under #10541's literal class-1 wording ("route it through
#: `encode_for_row`") none of them is a CONVERSION.
#: Why each of the six is nonetheless legitimate is stated PER ENTRY, not as a
#: blanket: the declaration #5008 owns covers the session chain
#: (`_session_event_write`, `ingest_corpus`, `_embedding_repair_attempt`) and
#: nothing else. The other three (`restore_graph`, `_event_plain_merge`,
#: `_rebuild_all_replay`) carried NO declared exemption in the PRODUCT until
#: `#11085` — they store a vector computed or backed up UPSTREAM, through the
#: canonical builder, and that reading now has a runtime home in
#: `tortoise/embed_writer_register.py` (`#11085` owns those three declarations).
#: ⛔ This census is a READ-ONLY INSTRUMENT and licenses nothing: the product
#: declaration, not this table, is what an exemption is ALLOWED to point at.
#: From `#11444` row 1 READS that declaration (`declared_writers()`), and this
#: census stays what it always was — the over-inclusive MEASUREMENT the register
#: classifies. The two are not one population and neither replaces the other.
#: The name also inverts this file's own `kind == "producer"`, which means
#: "computes a vector another function writes"; that is a wart, kept because
#: #10541 names the class.
#: `exempt` — the function is not a producer of a STORED vector: it renders the
#:   clause, forwards one, journals one, reads one, sets a FLAG or emits a
#:   DIAGNOSTIC, or merely MENTIONS one in a comment/docstring (the instrument's
#:   own false positives), or stores one under a NAMED owner exemption.
#: `bypass` — a write that escapes the rule. Measured: **none**.
CENSUS_RECOGNISED = "recognised_producer"
CENSUS_EXEMPT = "exempt"
CENSUS_BYPASS = "bypass"
CENSUS_CLASSES = (CENSUS_RECOGNISED, CENSUS_EXEMPT, CENSUS_BYPASS)

WRITER_CENSUS: dict[str, tuple[str, str]] = {
    "tortoise.api::add_point": (
        CENSUS_EXEMPT,
        "`tortoise/api.py::add_point` — discovered by the CONTAINER-KEY shape "
        "(`p[\"embedding\"] = fields[\"embedding\"]`, `_EMBED_WRITE` cannot see it; "
        "`#11274`). It writes a CALLER-supplied vector OVER the server one into the "
        "journal payload, and that is the `#5004` contract rather than a producer: "
        "the key stays PRESENT so an explicit `None` means \"this id has no "
        "vector\", and a foreign vector is CARRIED (restored verbatim on replay) "
        "with NO attestation, because its origin is not ours to vouch for. The "
        "server vector is stamped by `_point` (a different function, under its own "
        "seam). ⛔ A caller-supplied `embedding` is NOT refused by the SDK: `create_point`/"
        "`update_point` store one VERBATIM by a recorded decision (PR `#3018` review P2), so a "
        "TENANT can supply one on those paths — but not through THIS function. Here the branch "
        "guards a vector a DIRECT `EventAPI` caller hands to `add_point(fields=…)` "
        "(`tests/test_write_path_unreachable_seam_5148.py` pins that the key is CARRIED — under "
        "its unimportable seam it cannot tell this branch from `p.update(fields)`); the tenant "
        "doors' journal half lives in `tortoise.sdk` "
        "(`_journal_snapshot_from_create_map` → `_emit_event`'s "
        "`clean.setdefault(\"embedding\", None)`, and `update_point`'s `emit_props` on the "
        "`PointRevised` record). Their gate-side half is `gated` and lies outside this "
        "census's population"),
    "tortoise.api::_emit": (
        CENSUS_EXEMPT,
        "`tortoise/api.py::_emit` — a CARRIER: the vector its record carries is stamped by the "
        "seam (`stamp_journal_embedding`) and stored by the FOLD, so there is no store clause in "
        "its own body (`NO_LIVE_VECTOR` in `tortoise/embed_writer_register`). The instrument's "
        "match is COMMENT PROSE — `#12000`'s F1b note quotes the shape its guard REJECTS "
        "(a non-numeric `embedding` on the keyword payload), and `_EMBED_WRITE` reads raw "
        "source, so a comment naming the rejected shape enrols the function. The same "
        "prose-match class as `tortoise.sdk::_update_entity` and "
        "`tortoise.projection::_prewipe_synthetic_write_scope`"),
    "tortoise.api::_verify_record_vector_width": (
        CENSUS_EXEMPT,
        "`tortoise/api.py::_verify_record_vector_width` — a VERIFIER, not a producer: it reads "
        "the record's declared `embedding_width` and the width's own vector and asks the seam "
        "whether the declaration is truthful (`#11835` review round 9, P1). The instrument's "
        "matches are its PROSE — the DOCSTRING reproducing the measured `_emit(...)` calls, and "
        "the TWO BODY COMMENTS that record the later measured cases (a non-numeric "
        "`embedding` and the fold storing the raw text); a lane that reworded only the "
        "docstring is still in the population (measured). `_EMBED_WRITE` reads raw source, so "
        "prose QUOTING the shape matches, as `tortoise.projection::_prewipe_synthetic_write_scope`'s "
        "does. It issues no write at all (`NO_LIVE_VECTOR` in `tortoise/embed_writer_register`)"),
    "tortoise.consistency::_compare_views": (
        CENSUS_EXEMPT,
        "issues no write; the only WRITE-SHAPED text is a COMMENT in "
        "`tortoise/consistency.py::_compare_views` quoting what `replay(journal)` "
        "cannot produce (`n.embedding=vecf32($embedding)`). The function reads "
        "`\"embedding\"` off both sides and APPENDS it to a divergence field "
        "list — reads and appends, never a write clause"),
    "tortoise.consistency::_graph_fingerprint": (
        CENSUS_EXEMPT,
        "`tortoise/consistency.py::_graph_fingerprint` — discovered by the "
        "CONTAINER-KEY shape (`view[\"embedding\"] = props[\"embedding\"]`, "
        "`#11274`). It copies a vector READ OFF THE GRAPH (`props` comes from "
        "`graph_view`) into a comparison view dict, so the write target is an "
        "in-memory view and not a stored vector — a READ, the same direction as "
        "`turn_prior_vectors`"),
    "tortoise.hosted_backup::restore_graph": (
        CENSUS_RECOGNISED,
        "`tortoise/hosted_backup.py::restore_graph` writes through the canonical "
        "clause builder (`set_embedding_clause(\"n\", \"r.v\")`) from the "
        "backed-up row"),
    "tortoise.projection.entities::_event_plain_merge": (
        CENSUS_RECOGNISED,
        "`tortoise/projection/entities.py::_event_plain_merge` — "
        "`set_embedding_clause(\"e\", preserve_on_null=False)` / "
        "`set_embedding_clause(\"e\")`, the canonical clause"),
    "tortoise.projection::_apply_one": (
        CENSUS_EXEMPT,
        "`tortoise/projection/__init__.py::_apply_one` — the instrument's match "
        "is COMMENT PROSE, and the class is not inferred from that: the fold "
        "never writes the vector, which the same comment states in its own "
        "words (`⛔ This fold never SETS a real value`). `#8488`/`#10523` "
        "documented the caller-vector clear as `#   2. a CALLER-VECTOR write "
        "(`embedding=[…]` / `embedding=None`)`, and that prose is what "
        "`_EMBED_WRITE` matches; the vector write belongs to the CARRIER "
        "(`_upsert_point_props`, `n.embeddingText=$et`), not to this replay. "
        "The only property the function touches is the TEXT prop — "
        "`p.pop(\"embeddingText\", None)` — on an in-memory `{id: point}` "
        "dict. Exempt as a fold of state computed ELSEWHERE, the same "
        "direction as `turn_prior_vectors` (a mirror), and NOT the map-merge "
        "the `_update_entity` entry carries: there is no `SET n += $p` here"
    ),
    "tortoise.projection::_prewipe_synthetic_write_scope": (
        CENSUS_EXEMPT,
        "`tortoise/projection/__init__.py::_prewipe_synthetic_write_scope` — the "
        "match is inside the scope's DOCSTRING and the statement NULLs the "
        "vector (`SET n.embedding = NULL`); a wipe is not a producer"),
    "tortoise.projection::_rebuild_all_replay": (
        CENSUS_RECOGNISED,
        "`tortoise/projection/__init__.py::_rebuild_all_replay` calls "
        "`set_embedding_clause(\"n\", \"$emb\")` (canonical clause). Its labels "
        "are LITERAL (`n:Point` in that function's own Cypher) — the label reads "
        "UNKNOWN only because the `unresolved_write` branch hardcodes "
        "`UNKNOWN(unresolved producer)` and never derives one. ⚠️ It also holds "
        "two OTHER embedding statements this class does not rest on: "
        "`SET n.embedding = NULL` (a wipe) and a DIRECT `n.embedding = $emb` "
        "that bypasses the canonical builder"),
    "tortoise.record_read::turn_prior_vectors": (
        CENSUS_EXEMPT,
        "`tortoise/record_read.py::turn_prior_vectors` — `embedding=row[2]` "
        "populates a dataclass from a returned row: a READ, with no write clause"),
    "tortoise.sdk::_embedding_repair_attempt": (
        CENSUS_RECOGNISED,
        "the repair helper DOES compute and store: "
        "`tortoise/sdk.py::_embedding_repair_attempt` carries "
        "`embedding = self._session_embedding(...)` and `set_embedding_clause(\"e\")` "
        "in its own body. It is a producer on the #5008 session chain, and a "
        "repair by ROLE — both, so `exempt` would contradict the classes above"),
    "tortoise.sdk::_event_raw_write_door": (
        CENSUS_EXEMPT,
        "`tortoise/sdk.py::_event_raw_write_door` — a TRANSPORT door: it forwards "
        "an already-computed `embedding` into `query_params` and executes the "
        "statement inside `_live_write_scope` (the §17.1 sanctioned live-write "
        "lane); it never computes one"),
    "tortoise.sdk::_index_gate_read": (
        CENSUS_EXEMPT,
        "`tortoise/sdk.py::_index_gate_read` — discovered by the CONTAINER-KEY "
        "shape (`g[\"embedding\"] = rows[0][0]`, `#11274`). It is the fast-path "
        "ROUTER READ: every statement it issues is a `MATCH … RETURN`, and the "
        "container it fills is its own `g` dict — the vector is read off a "
        "returned row and never written, the `turn_prior_vectors` direction"),
    "tortoise.sdk::_index_process_unit": (
        CENSUS_EXEMPT,
        "`tortoise/sdk.py::_index_process_unit` — the instrument's match is the "
        "DIAGNOSTIC STRING `\"error\": \"indexed with embedding=None \"`, not a "
        "write clause. That line sits on the `merge_outcome == \"indexed\"` "
        "branch of the unit-completion path, whose store is DELEGATED to "
        "`_session_event_write` in the same module. Its other stores are both "
        "DELEGATED to `_embedding_repair_attempt`, at TWO distinct sites: the "
        "repair-only branch (`embedding_only_incomplete`), and the "
        "post-full-write repair site — whose own comment records that the "
        "repair-only branch is NOT re-entered. Every callee is a recognised "
        "producer, so this function produces no vector of its own"),
    "tortoise.sdk::_journal_event_raw_write": (
        CENSUS_EXEMPT,
        "`tortoise/sdk.py::_journal_event_raw_write` — the instrument matches the "
        "SIGNATURE DEFAULT (`embedding=None`), not a write clause: `_EMBED_WRITE` "
        "cannot see the dict-key form `record[\"embedding\"] = embedding` in that "
        "function. It stores no LIVE graph vector, but its journal IS the "
        "sanctioned replay carrier (`_upsert_event` consumes `embedding` off the "
        "record instead of recomputing it) — exempt as a sanctioned carrier, NOT "
        "because no store is reachable through it"),
    "tortoise.sdk::_session_event_write": (
        CENSUS_RECOGNISED,
        "`tortoise/sdk.py::_session_event_write` — `self._session_embedding(...)` "
        "→ `compute_session_embedding` (the documented #5008 exemption) + "
        "`set_embedding_clause`"),
    "tortoise.sdk::_update_entity": (
        CENSUS_EXEMPT,
        "`tortoise/sdk.py::_update_entity` — the instrument's match is a COMMENT, "
        "but that is only what the regex MATCHED — the function DOES store: "
        "`point_props = dict(props)` keeps a caller-supplied `embedding` through "
        "the map-merge (`SET n += $p`). `_EMBED_WRITE` cannot see a map-merge. "
        "Exempt on the NAMED #4094 exemption, not on absence of a write"),
    "tortoise.sdk::_write_capture_turns": (
        CENSUS_EXEMPT,
        "`tortoise/sdk.py::_write_capture_turns` — discovered by the "
        "CONTAINER-KEY shape (`_turn_snapshot[\"embedding\"] = turn_embs[i] …`, "
        "`#11274`). The vector is `turn_embs[i]` (or the already-stored one) and is "
        "never computed here: no `encode_for_row`, no `set_embedding_clause` in the "
        "body. Exempt as the SANCTIONED TURN-RECORD carrier of the `#5008` session "
        "chain — the same direction, and for the same reason, as "
        "`_journal_event_raw_write`: its record IS what the rebuild reads the "
        "vector back from (`#8488`), not a store reached around the gate"),
    "tortoise.sdk::ingest_corpus": (
        CENSUS_RECOGNISED,
        "`tortoise/sdk.py::ingest_corpus` — `self._session_embedding(...)` + "
        "`set_embedding_clause(\"e\")`, the same canonical chain"),
    "tortoise.vector_write::set_embedding_clause": (
        CENSUS_EXEMPT,
        "`tortoise/vector_write.py::set_embedding_clause` — the clause BUILDER "
        "itself; it renders the `<alias>.embedding = …` clause and stores no "
        "vector"),
}


#: `{census key: ((path, symbol, needle), …)}` — every claim above, re-derivable.
#: Each citation names a SYMBOL and a needle that must appear INSIDE that
#: symbol's own source, so:
#:   * an unrelated edit elsewhere in a 47k-line file cannot invalidate it (the
#:     failure the line-number form had), and
#:   * a seam that MOVES out of the function the entry names still reds, which a
#:     bare `path::symbol exists` pin would miss.
#: `census_evidence_violations()` re-derives the lot; the KEY SET must equal the
#: union of `WRITER_CENSUS` and `WRITER_LABEL_OVERRIDES`, so a new entry cannot
#: be added without evidence and evidence cannot outlive its entry.
CENSUS_EVIDENCE: dict[str, tuple[tuple[str, str, str], ...]] = {
    "tortoise.api::add_point": (
        ("tortoise/api.py", "add_point",
         'p["embedding"] = fields["embedding"]'),),
    "tortoise.api::_emit": (
        ("tortoise/api.py", "_emit", 'embedding="abc"'),
        # ⛔ NOT `stamp_journal_embedding =` — that needle matches the import-failure
        # FALLBACK (`stamp_journal_embedding = None`), so it survived deleting the stamp
        # call the row's premise rests on (cycle-3 review, measured). Cite the CALL.
        ("tortoise/api.py", "_emit",
         "event = stamp_journal_embedding(event, creating=False)"),),
    "tortoise.api::_verify_record_vector_width": (
        ("tortoise/api.py", "_verify_record_vector_width",
         "embedding=[0.1234567890123, 1/3]"),
        # ⛔ The DECISION, not the import that feeds it: `JOURNAL_VECTOR_WIDTH_F32 as _F32`
        # survives turning the refusal into an abstention, so it proved less than the
        # row's reason claims (cycle-4 review, measured).
        ("tortoise/api.py", "_verify_record_vector_width",
         "declared != _F32"),
        # ⛔ And the REFUSAL's own sentence, because the decision line above survives turning
        # the refusal into an abstention (`if not truthful: return`) — measured in cycle 5.
        # This needle vanishes under exactly that mutation, which is the drift the row's
        # reason promises against ("a declared f32 over it is refused").
        ("tortoise/api.py", "_verify_record_vector_width",
         "is not what the values say"),),
    "tortoise.consistency::_compare_views": (
        ("tortoise/consistency.py", "_compare_views",
         "n.embedding=vecf32($embedding)"),),
    "tortoise.consistency::_graph_fingerprint": (
        ("tortoise/consistency.py", "_graph_fingerprint",
         'view["embedding"] = props["embedding"]'),),
    "tortoise.hosted_backup::restore_graph": (
        ("tortoise/hosted_backup.py", "restore_graph",
         "set_embedding_clause("),),
    "tortoise.projection.entities::_event_plain_merge": (
        ("tortoise/projection/entities.py", "_event_plain_merge",
         "set_embedding_clause("),),
    "tortoise.projection::_apply_one": (
        ("tortoise/projection/__init__.py", "_apply_one",
         "a CALLER-VECTOR write"),
        ("tortoise/projection/__init__.py", "_apply_one",
         'p.pop("embeddingText", None)'),),
    "tortoise.projection::_prewipe_synthetic_write_scope": (
        ("tortoise/projection/__init__.py", "_prewipe_synthetic_write_scope",
         "SET n.embedding = NULL"),),
    "tortoise.projection::_rebuild_all_replay": (
        ("tortoise/projection/__init__.py", "_rebuild_all_replay",
         "set_embedding_clause("),),
    "tortoise.record_read::turn_prior_vectors": (
        ("tortoise/record_read.py", "turn_prior_vectors",
         "embedding=row[2]"),),
    "tortoise.sdk::_embedding_repair_attempt": (
        ("tortoise/sdk.py", "_embedding_repair_attempt",
         "self._session_embedding("),
        ("tortoise/sdk.py", "_embedding_repair_attempt",
         "set_embedding_clause("),),
    "tortoise.sdk::_event_raw_write_door": (
        ("tortoise/sdk.py", "_event_raw_write_door", "_live_write_scope"),),
    "tortoise.sdk::_index_gate_read": (
        ("tortoise/sdk.py", "_index_gate_read", 'g["embedding"] = rows[0][0]'),),
    "tortoise.sdk::_index_process_unit": (
        ("tortoise/sdk.py", "_index_process_unit",
         "indexed with embedding=None"),),
    "tortoise.sdk::_journal_event_raw_write": (
        ("tortoise/sdk.py", "_journal_event_raw_write",
         'record["embedding"] = embedding'),),
    "tortoise.sdk::_session_event_write": (
        ("tortoise/sdk.py", "_session_event_write",
         "self._session_embedding("),
        ("tortoise/sdk.py", "_session_event_write",
         "set_embedding_clause("),),
    "tortoise.sdk::_update_entity": (
        ("tortoise/sdk.py", "_update_entity", "point_props = dict(props)"),
        ("tortoise/sdk.py", "_update_entity", "SET n +="),),
    "tortoise.sdk::_write_capture_turns": (
        ("tortoise/sdk.py", "_write_capture_turns",
         '_turn_snapshot["embedding"] ='),),
    "tortoise.sdk::ingest_corpus": (
        ("tortoise/sdk.py", "ingest_corpus", "self._session_embedding("),
        ("tortoise/sdk.py", "ingest_corpus", "set_embedding_clause("),),
    "tortoise.vector_write::set_embedding_clause": (
        ("tortoise/vector_write.py", "set_embedding_clause",
         "vecf32({vector_expr})"),),
    # The label OVERRIDE carries evidence too — it is a claim about where the
    # `:Point` write happens, and it drifted the same way (it cited
    # `api.py:318`, which is `vec = encode_for_row(` in a DIFFERENT function).
    "tortoise.api::_point": (
        ("tortoise/api.py", "_point", 'p["embedding"] = vec'),),
}


def census_evidence_violations(census: dict | None = None,
                              evidence: dict | None = None,
                              root: Path | None = None,
                              overrides: dict | None = None) -> list[str]:
    """Re-derive every census citation at THIS head; empty list = falsified.

    Four ways to fail, each reported rather than absorbed: an entry with no
    evidence, evidence whose entry is gone, a citation whose file/symbol does
    not exist, and a needle that is no longer inside the symbol it is cited for.
    """
    import ast

    census = WRITER_CENSUS if census is None else census
    evidence = CENSUS_EVIDENCE if evidence is None else evidence
    overrides = WRITER_LABEL_OVERRIDES if overrides is None else overrides
    root = REPO_ROOT if root is None else root
    entries = set(census) | set(overrides)
    out: list[str] = []
    for key in sorted(entries - set(evidence)):
        out.append(f"{key}: classed with no re-derivable evidence")
    for key in sorted(set(evidence) - entries):
        out.append(f"{key}: evidence for an entry that does not exist")
    bodies: dict[str, dict[str, str] | None] = {}
    for key in sorted(set(evidence) & entries):
        for citation in evidence[key]:
            rel, symbol, needle = citation
            if rel not in bodies:
                path = root / rel
                try:
                    src = path.read_text(encoding="utf-8")
                except OSError:
                    bodies[rel] = None
                else:
                    lines = src.splitlines()
                    index: dict[str, list[str]] = {}
                    for node in ast.walk(ast.parse(src)):
                        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            index.setdefault(node.name, []).append(
                                "\n".join(lines[node.lineno - 1:node.end_lineno]))
                    bodies[rel] = index
            index = bodies[rel]
            if index is None:
                out.append(f"{key}: cites {rel}, which cannot be read")
                continue
            if symbol not in index:
                out.append(f"{key}: cites {rel}::{symbol}, which that file does "
                           "not define")
                continue
            if not any(needle in body for body in index[symbol]):
                out.append(f"{key}: {rel}::{symbol} does not carry {needle!r} at "
                           "this head")
    return out


def _census_class(writer: str) -> str | None:
    """The class #10541 recorded for ``writer``, or None when unclassified."""
    entry = WRITER_CENSUS.get(writer)
    return entry[0] if entry else None


def _strings(node) -> str:
    """Every string literal in ``node`` — where the Cypher actually lives."""
    import ast

    return "\n".join(
        s.value for s in ast.walk(node)
        if isinstance(s, ast.Constant) and isinstance(s.value, str)
    )


def _callees(node) -> set[str]:
    import ast

    return {
        getattr(c.func, "id", None) or getattr(c.func, "attr", None) or ""
        for c in ast.walk(node) if isinstance(c, ast.Call)
    }


#: Writers whose own Cypher names NO label because the write is delegated to
#: the projection (they hand the vector to the same `:Point` clause
#: `_revise_point` uses) — ONE entry, each with the evidence and each REPORTED
#: in the row. Deliberately not a vocabulary: everything else is derived from
#: the Cypher, so a writer that names `:Document` still trips bullet 2.
WRITER_LABEL_OVERRIDES: dict[str, str] = {
    # `tortoise/api.py::_point` computes the vector and stamps the journal; its
    # Cypher carries no `MERGE (:Label`. It feeds the `:Point` write —
    # `p["embedding"] = vec` is the payload the projection's point clause
    # stores (re-derived in `CENSUS_EVIDENCE`, not cited by line number: the
    # previous pointer, `api.py:318`, was `vec = encode_for_row(` in a
    # DIFFERENT function).
    #
    # ⛔ Keyed by `module::name`, the SAME identity the writer map uses — a bare
    # function name is not an identity: `_point` is a plausible name in any
    # module (this tree already carries `_point_key`, `_point_raw_refs`, …), and
    # a bare-name lookup ASSIGNED `Point` to any same-named writer with no
    # Cypher evidence while the comment above claimed the opposite.
    "tortoise.api::_point": "Point",
}


#: `{root: {module::name, …}}` — filled by `_embedding_writers`, so the row can
#: say which written labels were assigned rather than derived.
_OVERRIDDEN_BY_ROOT: dict[Path, frozenset[str]] = {}


def embedding_writers() -> dict[str, dict]:
    """Every function that produces a stored vector, and what it writes.

    ``{writer: {"callee", "at", "kind", "label", "callees"}}`` where ``kind`` is
    ``gated`` (reaches ``encode_for_row``), ``ungated`` (produces a vector the
    gate never sees), ``producer`` (a function that computes a vector a
    DIFFERENT function writes — `encode_for_store`'s read/backfill callers, and
    the journal stampers), ``unresolved_write`` (writes one with no recognised
    producer call), ``duplicate`` (two same-named defs in one module) or
    ``unparsed`` (the module would not parse — keyed by PATH, one per module).
    ``writer`` is ``<dotted module>::<function>``, so two writers in different
    modules can never share a key.

    **The label is derived from the Cypher, not from the writer's name.** That
    matters: a name-derived map draws from the same small vocabulary as the
    query path, so its "stored without a query path" set is empty BY
    CONSTRUCTION and the `NOT MET` branch is dead code — a row that cannot come
    back red is machinery, not measurement (this tool's own review, P1). Three
    sources, most local first:

    1. the labels in the function's OWN Cypher literals;
    2. the writer's name stem against the labels its MODULE's Cypher names
       (`_upsert_event` -> `Event`), which is two sources agreeing, not a guess;
    3. otherwise ``UNKNOWN`` — reported, and it holds bullet 2 at `PARTIAL`.

    ``callees`` is the writer's own callee-name set, recorded because a
    declaration's class can be a claim about a ROUTE and a route claim is
    re-derivable from the seam (``declaration_violations``, `#11444`). `#11444`
    adds only this key: `kind`, `label`, `callee` and `at` are unchanged, so
    every existing consumer reads exactly what it read before.
    """

    return _embedding_writers(REPO_ROOT)


def _module_name(path: Path, root: Path) -> str:
    """The DOTTED module path a file is imported as — `tortoise.projection`.

    ⛔ NOT `path.stem`. A stem is the BASENAME, so every package's
    `__init__.py` collapses into `__init__` (8 of them under `tortoise/` today),
    as do `tortoise/monitoring.py` / `tortoise/shared_state/monitoring.py` and
    the two `selfhost.py`. Python's own module identity is the dotted path, and
    a package's module **is** the package: `__init__.py` is *implicitly
    executed, and the objects it defines are bound to names in the package's
    namespace* (docs, the import system §5.2).

    The collapse was not cosmetic: the SECOND writer under a shared key became
    `duplicate`, which `written_labels()` excluded, so a writer whose label
    could not be resolved silently vanished from the set bullet 2 compares
    while bullet 2 still read `MET` (this tool's own review, P2).
    """
    parts = list(path.relative_to(root).with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


#: The label a COLLIDED writer carries. One spelling, because `written_labels()`
#: maps every `duplicate` onto it — the stored `label` is never read for one.
DUPLICATE_LABEL = "UNKNOWN(duplicate writer name)"

#: The label a writer declared `LABELS_AT_RUNTIME` carries in the SCORED map (`#11492`). It is an
#: `UNKNOWN…` spelling on purpose: the value is not a label, so it must never reach the
#: served-set comparison as one, and the row's hold set is built from exactly this prefix.
AT_RUNTIME_LABEL = "UNKNOWN(labels decided at runtime)"

#: A PARTIAL measurement's prefix — a writer whose own Cypher names MORE than one label
#: (`_embedding_writers`' `UNKNOWN(two labels: …)` shape). It is a MEASUREMENT, not a gap, so a
#: declaration may not narrow it (`#11492`): the row holds and names it.
#: ⛔ SCOPED TO THE KIND THAT REACHES IT (`#11495` review, measured). That shape is produced on the
#: `gated` / `ungated` path only. An `unresolved_write` writer's own multi-label reading is
#: FLATTENED to `UNKNOWN(unresolved producer)` by `written_labels` before the hold set is computed,
#: so it never enters `partial` and its DECLARATION is the only reading row 2 scores — that entry is
#: bounded by `#11530`'s completeness pin, not by this hold. Do not cite `_event_plain_merge` /
#: `_rebuild_all_replay` as examples of the narrowing this constant forbids: both are
#: `unresolved_write`, so both are resolved by declaration, which is the designed answer.
PARTIAL_LABEL_PREFIX = "UNKNOWN(two labels"


@functools.lru_cache(maxsize=8)
def _embedding_writers(root: Path) -> dict[str, dict]:
    writers: dict[str, dict] = {}
    overridden: set[str] = set()

    def _put(key: str, entry: dict) -> None:
        """Record a writer, applying the COLLISION rule at every exit.

        ⛔ ONE door for all four exits (`#10519`'s review, P3). Three of them
        used to assign `writers[key]` DIRECTLY and only the fourth consulted
        the guard, so when the FIRST definition of a key was `gated`/
        `ungated`/`producer` and a LATER one was an `unresolved_write` or a
        two-label writer, the later entry silently OVERWROTE the earlier one
        and `kind` never became `"duplicate"`: bullet 1's duplicate note never
        fired (`score_rule_bullet`'s `duplicates` read `[]`) and
        `score_path_bullet` reported the collision as an ordinary unresolved
        producer. The verdict therefore depended on `ast.walk` order — the
        reverse order DID report it.
        """
        if key in writers:
            writers[key] = {
                "callee": entry["callee"],
                "at": entry["at"],
                "kind": "duplicate",
                "label": DUPLICATE_LABEL,
                "callees": entry.get("callees", frozenset()),
            }
            # The override's label did not survive the collision, so it must
            # not be REPORTED as one used (`score_path_bullet` prints
            # `_overridden_writers() & set(written_labels())`).
            overridden.discard(key)
            return
        writers[key] = entry

    seam_module = root / "tortoise/embeddings.py"
    for path in sorted((root / "tortoise").rglob("*.py")):
        if path == seam_module:
            continue  # the primitives themselves are not write paths
        rel = path.relative_to(root).as_posix()
        module = _module_name(path, root)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            # ⛔ Keyed by the PATH, not by one shared sentinel: N modules that
            # will not parse used to collapse into a single `UNPARSEABLE`
            # entry carrying ONE path, so the row named a hole it could not
            # locate and the rest were unreported (this tool's own review, P3).
            writers.setdefault(f"UNPARSEABLE:{rel}", {
                "callee": "", "at": rel, "kind": "unparsed",
                "label": "UNKNOWN", "callees": frozenset(),
            })
            continue
        module_labels = {lab for _v, lab in _NODE_LABEL.findall(_strings(tree))}
        file_src = path.read_text(encoding="utf-8")
        lines = file_src.splitlines()
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            called = _callees(node)
            # The function's own text by LINE RANGE — `ast.get_source_segment`
            # re-splits the whole file per call, which cost 90 s on this tree.
            source = "\n".join(lines[node.lineno - 1:node.end_lineno])
            kind: str | None = None
            if SEAM_CALL in called:
                callee, kind = SEAM_CALL, "gated"
            elif set(UNGATED_CALLS) & called:
                callee = sorted(set(UNGATED_CALLS) & called)[0]
                kind = "ungated"
                # The write clause is a CALL (`set_embedding_clause("n")`) or a
                # CONTAINER KEY (`record["embedding"] = …`), so this reads the
                # function's SOURCE *and* its AST, not its string literals —
                # measured: reading only literals classed
                # `session_indexer.main` (which does write) as a producer and
                # hid a real ungated write path.
                if not _writes_an_embedding(node, source):
                    kind = "producer"  # computes it; another function writes it
            else:
                # No recognised producer call: if it still WRITES an embedding,
                # the chain it uses is outside this vocabulary and the row would
                # otherwise report `OUTSIDE the gate: 1` for a tree with three
                # (review round 5, P2 — measured: 17 functions carry an
                # embedding-write clause, 9 of them were absent from this map).
                if kind is None and _writes_an_embedding(node, source):
                    _put(f"{module}::{node.name}", {
                        "callee": "unrecognised",
                        "at": f"{rel}:{node.lineno}",
                        "kind": "unresolved_write",
                        "label": "UNKNOWN(unresolved producer)",
                        # The writer's OWN callee names — recorded so a
                        # declaration's ROUTE claim can be re-derived from the
                        # seam instead of taken on the register's word (#11444).
                        "callees": frozenset(called),
                    })
                continue
            own = _strings(node)
            pairs = _NODE_LABEL.findall(own)
            labels = {lab for _v, lab in pairs}
            if len(labels) == 1:
                label = labels.pop()
            elif len(labels) > 1:
                # Two labels in one writer's Cypher: picking one would report a
                # stored label as covered while the other may be unserved.
                # ⛔ BUILT FROM `PARTIAL_LABEL_PREFIX`, never a hand-retyped copy of it
                # (`#11495`'s review, measured). This line used to spell the literal out, and
                # `scored_labels()`'s `out["partial"]` arm —
                # `label.startswith(PARTIAL_LABEL_PREFIX)` — is its ONLY consumer, so
                # drifting the two apart left the WHOLE suite green (retyping just this line to
                # `"UNKNOWN(2 labels: "` kept 194 passed, 2 skipped) while a two-label
                # MEASUREMENT silently stopped being held, and a declaration could then narrow it
                # before the served-set comparison and read `MET`. That is the false PASS this
                # row's comment above exists to forbid. Deriving from the constant makes that
                # drift impossible rather than merely tested.
                label = f"{PARTIAL_LABEL_PREFIX}: " + ",".join(sorted(labels)) + ")"
                _put(f"{module}::{node.name}", {
                    "callee": callee, "at": f"{rel}:{node.lineno}",
                    "kind": kind, "label": label,
                    "callees": frozenset(called),
                })
                continue
            else:
                stem = (node.name[len("_upsert_"):]
                        if node.name.startswith("_upsert_") else node.name)
                hits = {lab for lab in module_labels
                        if stem.lower().endswith(lab.lower())}
                label = hits.pop() if len(hits) == 1 else "UNKNOWN"
            # ⛔ Keyed by the DOTTED module path, not by the file's stem: a bare
            # name merges two writers across modules (last-path-wins, no
            # sentinel) and silently SHRINKS both rows — `main` is exactly such
            # a name, and it is the only ungated entry today. A STEM is not a
            # module: it collapses every package `__init__.py` into `__init__`
            # (review round 5, P2).
            key = f"{module}::{node.name}"
            if label == "UNKNOWN" and key in WRITER_LABEL_OVERRIDES:
                # The label is recorded, and `score_path_bullet` reports it as
                # an OVERRIDE (not a Cypher derivation) by matching the SAME
                # `module::name` key — so a same-named writer in another module
                # cannot silently inherit it (review round 10, P3: the lookup
                # was `node.name`, so it could).
                label = WRITER_LABEL_OVERRIDES[key]
                overridden.add(key)
            _put(key, {
                "callee": callee,
                "at": f"{rel}:{node.lineno}",
                "kind": kind,
                "label": label,
                "callees": frozenset(called),
            })
    _OVERRIDDEN_BY_ROOT[root] = frozenset(overridden)
    return writers


def _overridden_writers() -> set[str]:
    """Writers whose label came from a documented OVERRIDE, not from Cypher."""
    return set(_OVERRIDDEN_BY_ROOT.get(REPO_ROOT, frozenset()))


def _write_seams_that_embed() -> dict[str, str]:
    """The GATED seams only: ``{writer: file:line}`` for `#5008`'s call sites."""
    return {w: d["at"] for w, d in embedding_writers().items() if d["kind"] == "gated"}


#: Where a "shipped default" for a flag would live. Searched, not assumed:
#: `measure()` clears the ambient env so a lane's own shell cannot make a gate
#: read as enforced, which also means the ambient value can NEVER answer "is
#: this shipped on" (review round 3, P2). `#10996`: the clearing now writes the
#: explicit OFF value instead of unsetting, because `TORTOISE_VALUE_GATE`
#: defaults ON — a distinction this surface has to make, since "unset" is no
#: longer an OFF spelling.
CONFIG_SURFACES: tuple[str, ...] = (
    ".env.example",
    "docker-compose.yml",
    "docker-compose.yaml",
    "Dockerfile",
    "config",
    ".github/workflows",
)


#: ⛔ There is deliberately NO truthy vocabulary in this module. The values a
#: shipped config can use to switch a flag ON are `tortoise/env_truthy.py`'s
#: `TRUTHY` — *"The single declared env-truthiness contract (#4097)"*, whose own
#: docstring enumerates the five divergent conventions it replaced. A local
#: tuple would be a sixth mirror and nothing would pin it to the contract, while
#: `embed_gate.embed_gate_enabled()` reads the contract: if the contract narrowed
#: (e.g. dropped `on`, V3 in that docstring), `FLAG=on` would read ON to this
#: instrument and OFF to the gate — the one input that moves bullet 1, failing
#: OPEN (review round 10, P3). `flag_in_shipped_configs` imports it per call.


#: A YAML block-scalar HEADER: `|` or `>` plus the optional chomping/indent
#: indicators, introduced as a mapping value (`note: >-`) or a sequence entry
#: (`- |`) — YAML 1.2.2 §8.1, <https://yaml.org/spec/1.2.2/#81-block-scalar-styles>.
#: The indicator must introduce the node, so `run: echo hi |` does not match. The
#: mapping KEY is captured because the KEY decides what the body IS (below).
_BLOCK_SCALAR_HEADER = re.compile(
    r"^\s*(?:-\s*)?(?:(?P<key>[A-Za-z_][\w.\-]*)\s*:\s*)?"
    r"[|>](?:[+-]?[1-9]?|[1-9][+-]?)?\s*(?:#.*)?$")

#: The block-scalar KEYS the CI EXECUTES, on the workflow surface only.
#: GitHub runs the body of `jobs.<job_id>.steps[*].run` (and `defaults.run`) as
#: shell commands — *"each line runs in the same shell"* — so an `export
#: FLAG=1` inside one IS a shipped setting, in shell syntax. Every other key a
#: block scalar can hang off (`note:`, `description:`, `if:`, …) is text.
#: Scoped to `.github/workflows` on purpose: a `run:` key in `config/*.yml` is
#: that tool's own field, not something this CI executes.
EXECUTED_BLOCK_KEYS = frozenset({"run"})

#: The config surfaces whose block-scalar bodies can be EXECUTED (see above).
WORKFLOW_SURFACE = ".github/workflows"


def _yaml_block_scalar_bodies(text: str) -> dict[int, str]:
    """The 1-based line numbers that are the BODY of a YAML block scalar.

    Mapped to the KEY that introduced the scalar (lowercased; `""` when the
    header is a bare sequence entry, `- |`), because the key decides whether the
    body is documentation or a command — see `EXECUTED_BLOCK_KEYS`.

    ⛔ **A block scalar is a SCALAR NODE, never a YAML mapping**: YAML 1.2.2
    models block scalars as a scalar node style (§8.1) structurally separate
    from block mappings (§8.2.2), *"Block scalar formats … all express
    strings"* — so no line inside a body can be a mapping ENTRY, and
    `_assigned_value` (which scans per LINE, with no notion of YAML structure)
    read a body line shaped like `TORTOISE_EMBED_GATE: 1` as a shipped ON.

    ⛔ **But "not a mapping entry" is not "documentation"** (review round 17,
    P3). On `.github/workflows` a `run: |` body is a SHELL SCRIPT the CI runs
    (<https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#jobsjob_idstepsrun>:
    *"each line runs in the same shell"*), so `export TORTOISE_EMBED_GATE=1`
    there is an executed setting that this walk used to count as PROSE — it
    reported `documented_only: 1` and `set ON in: none` over a workflow that
    sets it, the measured-looking-nowhere shape this instrument refuses
    elsewhere. The caller therefore scans a body introduced by an executed key
    and skips (and REPORTS) the rest.

    **Wrong outcome named:** `score_rule_bullet` computes `on_by_default =
    ambient or shipped["on"]`, which is the clause separating bullet 1's
    `PARTIAL` from `MET` once `#5090`'s remaining writers are resolved. This
    repo's documentation of THIS flag already lives in a `>-` block
    (`config/v4-mechanisms.yml:426-433`), so the natural edit (a usage line
    added inside that block) was the one shape that tripped it; and CI is where
    a flag would actually be TURNED ON, which is the opposite error.

    Only called for `.yml`/`.yaml` surfaces: `.env`, Dockerfile and shell files
    have no block scalars, and applying it there could only drop real
    assignments.
    """
    body: dict[int, str] = {}
    in_block = False
    header_indent = 0
    body_indent: int | None = None
    key = ""
    for number, line in enumerate(text.splitlines(), 1):
        indent = len(line) - len(line.lstrip())
        if in_block:
            if not line.strip():
                # A blank line inside the block is still scalar content.
                body[number] = key
                continue
            if body_indent is None:
                body_indent = indent
            if indent > header_indent and indent >= body_indent:
                body[number] = key
                continue
            # Dedented: the scalar ends, and THIS line may be a new header.
            in_block = False
        header = _BLOCK_SCALAR_HEADER.match(line)
        if header:
            in_block, header_indent, body_indent = True, indent, None
            key = (header.group("key") or "").lower()
    return body


def _assigned_value(line: str, flag: str) -> str | None:
    """The value ``line`` ASSIGNS to ``flag``, or `None` when it is a mention.

    ⛔ **An assignment, not a substring.** The previous shape asked "does this
    line contain the flag, and is the next token truthy?", which reads a line
    that *documents* the flag as a shipped ON — measured on three shapes this
    repo's own surfaces contain: plain prose (`Set FLAG=1 to enable`), a
    workflow step NAME (`- name: verify FLAG=1 takes effect`), and a commented
    list item (`- # FLAG=1`, where the `#` check ran before the dash strip).
    That is the one input that can flip bullet 1 to `MET` without measuring
    (`score_rule_bullet`), so a prose edit must not be able to move it.

    The assignment shapes are the ones a shipped config actually uses —
    compose map `KEY: value`, compose array `- KEY=value`
    (<https://github.com/compose-spec/compose-spec> `container_spec.md`
    §environment), and the shell/Dockerfile `export`/`ENV`/`ARG` prefixes. A
    bare `KEY` with no separator is a **declaration with no value** (compose
    spec: "the variable is unset"), so it is not ON.

    Returns the raw value token (quotes stripped, trailing `#` comment
    dropped), or `None` if the flag is not the KEY of an assignment.
    """
    stripped = line.strip()
    # A whole-line comment, a YAML document marker, or an INI/TOML comment.
    if not stripped or stripped[0] in "#;" or stripped.startswith("---"):
        return None
    # ⛔ `-` starts a YAML LIST ITEM (`- KEY=1`, the compose `environment:`
    # array form), so it is stripped BEFORE the comment test — otherwise
    # `- # FLAG=1` passes the comment check and reads ON.
    stripped = stripped.lstrip("-").lstrip()
    if not stripped or stripped[0] in "#;":
        return None
    for prefix in ("export ", "ENV ", "ARG "):
        if stripped.startswith(prefix):
            stripped = stripped[len(prefix):].lstrip()
            break
    # The flag must be the KEY: at the start of the line, followed by `=` or
    # `:` and nothing but the value. `Set FLAG=1 to enable` and
    # `name: verify FLAG=1 takes effect` fail here, which is the point.
    if not stripped.startswith(flag):
        return None
    # `KEY : value` is valid YAML (the space before the indicator is not part
    # of the key), so the separator is looked for after an `lstrip`.
    rest = stripped[len(flag):].lstrip()
    if rest[:1] not in ("=", ":"):
        return None
    value = rest[1:].strip()
    # A genuine assignment's value is ONE token, optionally followed by a
    # comment (`FLAG=1  # enable`). Words after it mean the line is prose
    # about the flag, not a setting of it.
    value = value.split(" #", 1)[0].split("\t#", 1)[0].strip()
    value = value.strip("'\"")
    if len(value.split()) > 1:
        return None
    return value


def flag_in_shipped_configs(flag: str) -> dict:
    """Is ``flag`` switched ON by any configuration this repo ships?

    Returns ``{"on": bool, "files": [...], "searched": [paths],
    "mentions": [paths], "unreadable": [paths]}``.

    ⛔ **`searched` is every path the walk READ; `mentions` is the subset whose
    text contains the flag.** They are different questions and the pre-fix code
    answered only the second while documenting it as the first — so for the
    ordinary case this function exists for (a flag set nowhere *and* mentioned
    nowhere) the promised "what 'nowhere' covered" was `[]`, and a documentation
    edit that moved the flag's one prose mention red a falsifier claiming the
    walk had missed a path.

    A flag set nowhere is a measured `False` **only** over the paths actually
    read; `unreadable` names any path the walk could not read (oversize, I/O
    error) so "nowhere" cannot silently exclude a file that does set it.
    """
    from tortoise.env_truthy import is_truthy

    hits: list[str] = []
    searched: list[str] = []
    mentions: list[str] = []
    unreadable: list[str] = []
    documented_only = 0
    for rel in CONFIG_SURFACES:
        target = REPO_ROOT / rel
        candidates = ([target] if target.is_file()
                      else sorted(target.rglob("*")) if target.is_dir() else [])
        for path in candidates:
            if not path.is_file():
                continue
            posix = path.relative_to(REPO_ROOT).as_posix()
            try:
                if path.stat().st_size > 2_000_000:
                    unreadable.append(posix)
                    continue
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                unreadable.append(posix)
                continue
            searched.append(posix)
            if flag not in text:
                continue
            mentions.append(posix)
            # ⛔ A block scalar's body is SCALAR CONTENT, so it is never a YAML
            # mapping entry — but on the workflow surface a body the CI
            # EXECUTES (`run: |`) is a shell setting, not prose. Documentation
            # bodies are skipped and COUNTED; executed ones are scanned like any
            # other line (review round 17, P3).
            bodies = (_yaml_block_scalar_bodies(text)
                      if posix.endswith((".yml", ".yaml")) else {})
            workflow = posix.startswith(WORKFLOW_SURFACE + "/")
            for number, line in enumerate(text.splitlines(), 1):
                if number in bodies:
                    executed = (workflow
                                and bodies[number] in EXECUTED_BLOCK_KEYS)
                    if not executed:
                        if _assigned_value(line, flag) is not None:
                            documented_only += 1
                        continue
                    # An executed `run:` body: fall through to the real scan,
                    # so a shipped `export FLAG=1` is a HIT, not a note.
                value = _assigned_value(line, flag)
                if value is not None and is_truthy(value):
                    hits.append(f"{posix}: {line.strip()[:90]}")
    return {"on": bool(hits), "files": hits, "searched": searched,
            "mentions": mentions, "unreadable": unreadable,
            # Reported, never silently dropped: how many assignment-shaped lines
            # were DOCUMENTATION inside a block scalar (`note:`, `description:`, …)
            # rather than a setting. An assignment inside an EXECUTED workflow
            # `run:` body is not counted here — it is a setting, and it is
            # allowed to move `on`.
            "documented_only": documented_only,
            "surfaces": list(CONFIG_SURFACES)}


#: ── The product's declared writer register (#11444) ───────────────────────
#: `#11085` (PR `#11269`) landed `tortoise/embed_writer_register.py` and RECORDED reading (b)
#: on `#11085`: *"Every embedding writer must route through the gate or be a named, accepted
#: exception in the product; the register is what makes (b) true rather than stated."* This
#: instrument could not see it: it named the register in three comments and never read it, so
#: every writer the product had CLASSIFIED was still reported as *"no recognised producer
#: call"* — a named `exempt` writer and a real `bypass` were the same string to the grader, and
#: `#5090` DoD row 1 was structurally unreachable at `MET`.
#:
#: The fix is the field's settled shape, not a bespoke one: **policy as declared data, read by
#: the instrument** (OPA, *Philosophy*: policy must be decoupled from the service "so that the
#: people responsible for policy can read, write, analyze, version, distribute, and in general
#: manage policy separate from the service itself"). The register is that declaration, and the
#: field's second half is respected too: a declaration is a SUPPLEMENT to the engine's own
#: derivation, never a replacement, so the over-inclusive AST enumeration is still printed.
#:
#: ⛔ **WHAT A REGISTER EDIT CAN AND CANNOT BUY — the exact contract, because the loose version
#: of this paragraph was a lie for the case that matters.**
#:
#: * A name with **no writer behind it in the scored population** is STALE and HOLDS the row
#:   (same contract ESLint's *Bulk Suppressions* enforces with a non-zero exit on an unused
#:   suppression). A name for a writer **the product does not have** is stale; so is a name
#:   whose writer has since been FIXED to route through the gate, or that discovery classes
#:   `gated`/`producer`/`duplicate` — all of them outside the population a declaration is OWED
#:   for, so delete the entry then: an unused exemption is a stale one. Naming a LIVE
#:   outside-gate writer is a different case — the LAST bullet — and that one greens the row.
#: * A name with **no admissible class** (``bypass``, or a class outside the vocabulary) HOLDS
#:   the row as a DEFECT, and so does one carrying no owning issue.
#: * A `recognised_producer` claim is CORROBORATED: the writer's own body must reach one of the
#:   seams the register exports (`route_calls()`), matched **by callee NAME** — the register
#:   exports names, not objects, the same qualifier the register's own docstring carries for its
#:   `gated` class — or the claim is refused and the writer stays unrecognised.
#: * A `reason` must cite the writer ITSELF, not another function's evidence.
#: * ⛔ **Naming a LIVE writer that discovery does find outside the gate DOES green the row — by
#:   class choice, `exempt` included.** That is reading (b): *"a named, accepted exception in
#:   the product"* IS the acceptance, so an instrument that refused it would be reopening the
#:   owner's recorded decision. Nothing here corroborates an `exempt` entry beyond its citation,
#:   because "this writer does not store a live vector" (or "stores one under a ruling") is a
#:   product judgement the instrument cannot re-derive. **Consequence a reader must carry:**
#:   `MET` on this row means *"every writer discovery finds outside the gate is gated or
#:   NAMED"* — never *"no writer bypasses"*. `tortoise/embed_writer_register.py` is product code
#:   that needs review; it is not a suppression file anyone may append to.
#:
#: ⛔ What this does NOT close, stated rather than implied: an `exempt` declaration for a writer
#: that stores a live vector through a shape discovery cannot decide (data flow —
#: `UNDECIDABLE_WRITE_SHAPES`) is indistinguishable to this instrument from a correct one. That
#: residual is the register's own declared scope and `#11086`'s seam. ⛔ And a residual on the
#: OTHER side of this row, which the register's own docstring refuses to let `MET` absorb: a
#: writer discovery classes `gated` (`tortoise.sdk::create_point`, `::update_point`) may still
#: store a caller-supplied `embedding` VERBATIM by a recorded decision (PR `#3018` review P2).
#: This row measures the WRITER population; it never claims every stored vector was decided by
#: the rule. `ROW_SCOPE_NOTE` carries that sentence into the row's own evidence.
REGISTER_MODULE = "tortoise.embed_writer_register"

#: An owning issue reference — the register's `load()` enforces `^#\d+$`; `fullmatch` here accepts
#: the same set minus a trailing newline, i.e. it is STRICTER (fail-closed: the entry is simply not
#: credited), and the instrument re-checks it rather than inheriting an invariant it does not test.
_OWNER_ISSUE_RE = re.compile(r"#\d+")

#: A Cypher label's SHAPE — the same shape `register.load()` enforces, re-checked at the READ
#: boundary for the same reason `_OWNER_ISSUE_RE` is: an injected or tampered declaration must be
#: graded by the instrument, not trusted because the product's own validator would have refused it.
_STORE_LABEL = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

#: The residual a `MET` on this row does NOT absorb — emitted with the row, so a reader quoting
#: the verdict cannot read it as "no vector is stored that the rule did not decide".
ROW_SCOPE_NOTE = (
    "scope: this row measures the WRITER population, not every stored vector — a "
    "`gated` writer may still store a caller-supplied vector VERBATIM "
    "(`tortoise.sdk::create_point` / `::update_point`, the recorded `#5004`/PR #3018 "
    "decision), which no register declaration closes"
)


def route_calls() -> tuple[str, ...]:
    """The recognised-producer SEAMS, read from the register that DEFINES the class.

    ⛔ Not a tuple held here. `recognised_producer` is a claim about a ROUTE, and the register
    exports the seams that claim means (`RECOGNISED_PRODUCER_SEAMS`). A copy in this file would
    be a second spelling of the class's own definition, and it drifts in the FALSE-HOLD
    direction: a seam added to the register would leave every correct declaration reading as
    uncorroborated, holding a row on a correct tree — the opposite of the defect `#11444` fixes.
    """
    from tortoise import embed_writer_register as register

    return tuple(register.RECOGNISED_PRODUCER_SEAMS)


def _normalise_seam(module: str) -> str:
    """A citation's module in ONE spelling: `tortoise.projection`.

    The register keys a writer by its DOTTED module while its prose cites the
    file (`tortoise/projection/__init__.py`). Both name the same module —
    `_module_name` in this file already decides that a package's module **is**
    the package — so BOTH sides are normalised: the citation (`_cited_seams`) and
    the declared KEY (`_normalise_declared_keys`). Normalising only the citation made a key
    written in the path spelling report as `stale` AND as `undeclared` at once, i.e.
    blamed the product for declaring a writer it had declared.
    """
    return module.removesuffix(".py").replace("/", ".").removesuffix(".__init__")


def _cited_seams(reason: str) -> set[tuple[str, str]]:
    """Every `module::symbol` citation in ``reason`` — LINEAR in the text's length.

    ⛔ Deliberately not a regex over the whole string: `[\\w./]+?\\.py|…::\\w+` backtracks
    quadratically on text with no `::` at all (measured on the earlier pattern: 0.30 s at
    5 kB, 5.1 s at 20 kB), and `reason` is a hand-written field with no bound. This walks the
    separators once, so a pasted log in a `reason` costs its own length and nothing more.
    """
    seams: set[tuple[str, str]] = set()
    index = reason.find("::")
    while index >= 0:
        start = index
        while start > 0 and (reason[start - 1].isalnum() or reason[start - 1] in "_./"):
            start -= 1
        end = index + 2
        while end < len(reason) and (reason[end].isalnum() or reason[end] == "_"):
            end += 1
        module, symbol = reason[start:index], reason[index + 2:end]
        if module and symbol and (module[0].isalpha() or module[0] == "_"):
            seams.add((_normalise_seam(module), symbol))
        index = reason.find("::", index + 2)
    return seams


def declared_writers() -> dict[str, dict]:
    """The PRODUCT's declaration, read from `tortoise/embed_writer_register.py`.

    ``{module::symbol: {"cls", "owner_issue", "reason", "stores"}}``, the TEXT fields COERCED to
    text so a malformed entry becomes a NAMED violation instead of a `TypeError` that aborts all
    four rows (which `main()`'s exit contract could not tell from "a row was NOT MET"). ⛔ ``stores``
    is passed through UNINTERPRETED and uncoerced (`#11492`): it is the one field whose shape is
    the declaration's answer, so the reader that grades it must see the raw value — a reader that
    coerced it would turn a malformed declaration into a plausible one. Keys are normalised by
    :func:`_normalise_declared_keys`, one layer up.

    Imported LAZILY and read-only: the register is a leaf module (standard library only, pinned
    by its own `test_the_register_imports_without_entering_the_projection_cycle`), so this read
    cannot enter the `projection → pack_state → sdk → projection` import cycle.

    ⛔ This function READS a declaration; it never derives one. A writer's class is a product
    decision with an owning issue — an instrument that guessed it would be the second rule set
    §12.2a forbids. It must never answer `{}` for a register it could not read: an empty mapping
    renders every outside-gate writer `undeclared` and blames the product for the instrument's
    own failure.
    """
    from tortoise import embed_writer_register as register

    return {
        str(key): {
            "cls": str(getattr(record, "cls", "") or ""),
            "owner_issue": str(getattr(record, "owner_issue", "") or ""),
            "reason": str(getattr(record, "reason", "") or ""),
            "stores": getattr(record, "stores", None),
        }
        for key, record in register.EMBED_WRITER_CLASSES.items()
    }


def _normalise_declared_keys(raw: dict[str, dict]) -> dict[str, dict]:
    """Every declaration keyed by the NORMALISED `module::symbol` — one key per writer.

    ⛔ The register admits two spellings for a seam (`tortoise.projection::_apply_one` and
    `tortoise/projection/__init__.py::_apply_one`) because `_cited_seams` does. Normalising
    only the CITATION side — the first version — made a key written in the path spelling report
    as `stale` AND as `undeclared` at once, for the SAME live function: the row blamed the
    product for not declaring a writer it had declared, and named two symptoms of one spelling.

    Two keys that normalise to one writer RAISE rather than let the later one silently win — the
    register's `load()` refuses a duplicate string key, and this is the same refusal one
    normalisation later. `register_audit` turns it into a named UNMEASURED hold.
    """
    out: dict[str, dict] = {}
    spelled: dict[str, str] = {}
    for key, entry in raw.items():
        module, _, symbol = str(key).partition("::")
        if not module or not symbol:
            raise ValueError(f"{key!r} is not a `module::symbol` key")
        normalised = f"{_normalise_seam(module)}::{symbol}"
        if normalised in out:
            raise ValueError(
                f"{key!r} and {spelled[normalised]!r} both name {normalised!r} — two "
                "spellings of ONE writer, so a lookup keyed by the normalised form would "
                "silently keep one of them"
            )
        spelled[normalised] = str(key)
        out[normalised] = entry
    return out


def register_audit(outside_gate: dict[str, dict]) -> dict:
    """Everything row 1 reports about the register, behind ONE failure boundary.

    ``outside_gate`` is the population a declaration is owed for (`unresolved_write` ∪
    `ungated`). Three sets partition it and cannot overlap; a fourth is disjoint from it:

    * ``undeclared`` — outside the gate, not named (HOLDS);
    * ``violations`` — named, but a claim it does not support (HOLDS): an inadmissible class, no
      owning issue, a `reason` that does not cite the writer, or a route the body does not make;
    * ``credited`` — named and corroborated, or named as `exempt` (reading (b));
    * ``stale`` — named but NOT in the outside-gate population at all (HOLDS: the writer is
      gone or renamed, its module is one the walk skips, its module would not parse, or it now
      ROUTES THROUGH THE GATE — the fix landing, or `gated`/`producer`/`duplicate` to discovery —
      any of which means the declaration is no longer owed and must be deleted, because an
      unused exemption is a stale one).

    A malformed register raises rather than answering, so the caller can hold the row as
    UNMEASURED; the alternative — a partial answer — reads as the product declaring nothing.
    """
    declared = _normalise_declared_keys(declared_writers())
    undeclared = sorted(set(outside_gate) - set(declared))
    stale = sorted(set(declared) - set(outside_gate))
    violations: dict[str, list[str]] = {}
    for writer in sorted(set(declared) & set(outside_gate)):
        why = declaration_violations(writer, declared[writer], outside_gate[writer])
        if why:
            violations[writer] = why
    return {
        "declared": declared,
        "undeclared": undeclared,
        "stale": stale,
        "violations": violations,
        "credited": sorted((set(declared) & set(outside_gate)) - set(violations)),
        "bypass_declarations": sorted(
            w for w, entry in declared.items() if entry["cls"] == CENSUS_BYPASS
        ),
    }


def declaration_violations(
    writer: str, entry: dict, writer_info: dict | None = None
) -> list[str]:
    """Why ``writer``'s declaration is NOT credited — empty list = credited.

    ⛔ The register's entries are HAND-WRITTEN names (`_DECLARATIONS`). Four things are
    checked, and it is worth being exact about what each one buys, because the loose version of
    this docstring claimed corroboration for an `exempt` entry that nothing corroborates:

    1. **self-citation** — the `reason` must cite the writer itself (`module::symbol`). The
       register's own test requires only *a* resolvable citation, so an entry parked on another
       function's evidence passes there and is refused here. This checks that the author
       LOOKED at this writer, not that the claim is true.
    2. **route corroboration, `recognised_producer` ONLY** — that class claims the writer reaches
       the canonical clause builder or the `#5008` session producer (`route_calls()`). If its own
       body makes no such call **by callee NAME** (the register exports names, not objects — the
       same qualifier its own docstring carries for the `gated` class), the claim is contradicted
       and the writer stays unrecognised.
       ⛔ There is NO equivalent for `exempt`, by construction: "stores no live vector" or
       "stores one under a ruling" is a product judgement that needs data flow to falsify, which
       this instrument refuses to guess at (`UNDECIDABLE_WRITE_SHAPES`). An `exempt` declaration
       is accepted BY NAME — that is reading (b), and the reason the register is product code.
    3. **an admissible class** — `bypass` (or anything outside the vocabulary) is a defect the
       instrument can NAME, never an exemption. `#11085`'s `load()` refuses to build one.
    4. **an owning issue** — a declaration with no `#<n>` is an acceptance with nothing behind it
       (reading (b) says *"a named, accepted exception"*; the `owner_issue` is the recorded
       acceptance). `load()` enforces the shape too.
       ⛔ Checks 3 and 4 fire on an injected or tampered register as well: the row must not assume
       the register is well-formed.
    """
    out: list[str] = []
    cls = entry.get("cls")
    if cls == CENSUS_BYPASS:
        out.append("declares `bypass` — a DEFECT, never an exemption")
    elif cls not in CENSUS_CLASSES:
        out.append(f"class {cls!r} is outside {CENSUS_CLASSES}")
    if not _OWNER_ISSUE_RE.fullmatch(entry.get("owner_issue") or ""):
        out.append(
            "carries no owning issue (`#<n>`) — an acceptance with no owner behind it is "
            "not an acceptance"
        )
    module, _, symbol = writer.partition("::")
    if (_normalise_seam(module), symbol) not in _cited_seams(entry.get("reason") or ""):
        out.append("does not cite its own `module::symbol` seam in `reason`")
    if cls == CENSUS_RECOGNISED:
        seams = route_calls()
        callees = set((writer_info or {}).get("callees") or ())
        if not callees.intersection(seams):
            out.append(
                "claims a route to a recognised producer "
                f"({', '.join(seams)}) that its own body does not call, by callee NAME"
            )
    return out


def score_rule_bullet() -> Row:
    """Bullet 1 — the rule is stated, is ONE rule set, and is enforced."""
    import tortoise.embed_gate as embed_gate
    import tortoise.value_gate as value_gate

    gate_src = (REPO_ROOT / "tortoise/embed_gate.py").read_text(encoding="utf-8")
    # Stated = the predicate EXISTS, and WIRED = the seam actually calls it.
    # Both are read; a decorative `SEAM` constant is not evidence (review
    # round 3, P3: "the parenthetical is never measured").
    stated_in_gate = "def embedding_verdict" in gate_src
    seam_wired = False
    try:
        import ast as _ast

        seam_src = (REPO_ROOT / "tortoise/embeddings.py").read_text(encoding="utf-8")
        for node in _ast.walk(_ast.parse(seam_src)):
            if (isinstance(node, _ast.FunctionDef) and node.name == "encode_for_row"):
                seg = _ast.get_source_segment(seam_src, node) or ""
                seam_wired = "embed_gate.embedding_verdict" in seg
                break
    except (OSError, SyntaxError):
        seam_wired = False
    stated = stated_in_gate and seam_wired
    # Identity, not equality: a COPY of the rule set is the drift §12.2a names.
    shared = embed_gate.RULE_SET is value_gate.RULES
    writers = embedding_writers()
    gated = {w: d for w, d in writers.items() if d["kind"] == "gated"}
    ungated = {w: d for w, d in writers.items() if d["kind"] == "ungated"}
    unparsed = [w for w, d in writers.items() if d["kind"] == "unparsed"]
    unresolved_writes = {w: d for w, d in writers.items()
                         if d["kind"] == "unresolved_write"}
    duplicates = [w for w, d in writers.items() if d["kind"] == "duplicate"]
    # ⛔ Two different questions, both asked: the ambient env is what
    # `measure()` deliberately clears (so a lane's shell cannot fake it), and
    # the SHIPPED config is where a default actually lives.
    ambient = embed_gate.embed_gate_enabled()
    shipped = flag_in_shipped_configs(embed_gate.ENV_FLAG)
    on_by_default = ambient or shipped["on"]
    # ⛔ The population a DECLARATION is owed for: every writer the rule never sees,
    # `unresolved_write` AND `ungated` — the same tuple
    # `tests/test_embed_writer_register.py::NEEDS_A_DECLARATION` spells, and the
    # population the register's own two-direction test measures.
    outside_gate = {**unresolved_writes, **ungated}
    # ⛔ UNMEASURED is not "nothing is declared": an unreadable or malformed register must
    # HOLD the row. Reading it as an empty mapping would render every writer `undeclared` and
    # blame the product for the instrument's own failure — the misattribution shape this file
    # refuses elsewhere. ONE boundary: `register_audit` raises for a register that cannot be
    # read OR shaped, so a corrupted entry becomes a named hold instead of a traceback that
    # aborts all four rows (which `main()`'s exit contract could not tell from "a row NOT MET").
    register_error: str | None = None
    try:
        audit = register_audit(outside_gate)
    except Exception as exc:  # noqa: BLE001, RUF100 — fail-closed, and NAMED
        audit = {"declared": {}, "undeclared": [], "stale": [],
                 "violations": {}, "credited": [], "bypass_declarations": []}
        register_error = f"{type(exc).__name__}: {exc}"
    declared = audit["declared"]
    undeclared = audit["undeclared"]
    stale = audit["stale"]
    violations = audit["violations"]
    credited = audit["credited"]
    bypass_declarations = audit["bypass_declarations"]

    def _note_classes(credited_keys: list[str]) -> str:
        """The class histogram of the CREDITED population — the one the note is about.

        ⛔ Over ``credited``, not over ``declared ∩ outside_gate``: counting an uncredited
        declaration made the line read *"0 of 1 … (recognised_producer=1)"* — asserting a
        class was credited in the same sentence that said none was. Declared-but-not-credited
        is counted SEPARATELY, so the line reconciles either way.
        """
        buckets: dict[str, int] = {}
        for writer in credited_keys:
            # `str(...)`: an entry's class is coerced at the read boundary, and a caller that
            # injects one directly must not turn a histogram into an unhashable-key TypeError.
            cls = str(declared[writer].get("cls") or "?")
            buckets[cls] = buckets.get(cls, 0) + 1
        text = " · ".join(f"{cls}={n}" for cls, n in sorted(buckets.items())) or "none"
        uncredited = len(set(declared) & set(outside_gate)) - len(credited_keys)
        return text + (f" (+{uncredited} declared but NOT credited)" if uncredited else "")

    if register_error:
        register_note = (
            f"#11444 — writers credited from the product register "
            f"({REGISTER_MODULE}): UNMEASURED ({register_error}) — the register "
            "could not be read, so NOTHING is credited and nothing is blamed on "
            "the product"
        )
    else:
        register_note = (
            f"#11444 — writers credited from the product register "
            f"({REGISTER_MODULE}): {len(credited)} of {len(outside_gate)} "
            f"outside-gate writers ({_note_classes(credited)})"
        )
        if credited:
            owners = ", ".join(
                f"{w}={declared[w].get('owner_issue')}" for w in credited
            )
            register_note += f"; owners: {owners}"
        # ⛔ The row's OWN scope, carried WITH the verdict: `MET` here means every writer
        # discovery finds outside the gate is gated or NAMED — never "no vector is stored
        # that the rule did not decide".
        register_note += f"; {ROW_SCOPE_NOTE}"
    # ⛔ Built as locals, never nested inside another f-string: nesting a
    # `'`-delimited f-string inside a `"`-delimited one is PEP 701 **syntax**
    # (`Python-Version: 3.12`), so it cannot be tokenized by the older
    # interpreters this file's `#5128` guard exists to refuse — the guard would
    # be documentary and the file would die before reaching it.
    ungated_at = ", ".join(f"{w} @ {d['at']}" for w, d in sorted(ungated.items()))
    unresolved_at = ", ".join(
        f"{w} @ {d['at']}" for w, d in sorted(unresolved_writes.items())
    )
    violations_at = ", ".join(
        f"{w}: {'; '.join(why)}" for w, why in sorted(violations.items())
    )
    notes = [
        f"stated: {stated} (predicate in embed_gate: {stated_in_gate}; "
        f"{SEAM} calls it: {seam_wired})",
        f"one rule set: {shared} (embed_gate.RULE_SET is value_gate.RULES; "
        f"ids={len(value_gate.RULES)})",
        f"write paths reaching the gated seam: {len(gated)} "
        f"({', '.join(sorted(gated)) or 'none'})",
        f"write paths that embed OUTSIDE the gate: {len(ungated)} "
        f"({ungated_at or 'none'})"
        # ⛔ Not a verdict and not a claim about the register: this note reports the
        # PATH. Whether the writer holds the row is decided below, from the register,
        # and says so in its own note (`undeclared` / `credited`).
        + (" — path(s) the gate never sees; each holds this row unless the "
           "product register CREDITS it (a name alone is not enough: the class must "
           "be admissible, the entry must carry an owning issue `#<n>`, the `reason` must "
           "cite the writer, and a `recognised_producer` claim must be corroborated)"
           if ungated else ""),
        f"gate enabled by default: {on_by_default} (ambient "
        f"{embed_gate.ENV_FLAG}={os.environ.get(embed_gate.ENV_FLAG, '')!r}; "
        f"shipped config surface walked: {len(shipped['surfaces'])} "
        f"({', '.join(shipped['surfaces'])}); files read: "
        f"{len(shipped['searched'])}; unreadable: "
        f"{shipped['unreadable'] or 'none'}; mentioning it: "
        f"{shipped['mentions'] or 'none'}"
        f"; set ON in: {shipped['files'] or 'none'})",
        # ⛔ A module that will not parse is a HOLE in the measurement, not a
        # zero: its writers — gated or bypassing — are invisible to every row
        # (this tool's own review, P2).
        f"unparseable modules (hold this row): {sorted(unparsed) or 'none'}",
        # ⛔ A function that writes an embedding with NO recognised producer
        # call is STILL reported: the register is a SUPPLEMENT to this
        # enumeration, never a replacement (`#11444`: *What the fix must
        # preserve*, third bullet — counted/classified, not replaced — and its
        # third Verification checkbox). The register answers *which class* each
        # one is in; it does not remove the population from the evidence.
        f"functions writing an embedding with no recognised producer call: "
        f"{len(unresolved_writes)} — deliberately OVER-INCLUSIVE (it also lists "
        f"repair/backfill helpers and the clause builder itself, because a "
        f"name-level enumeration cannot tell an exemption from a bypass; a "
        f"bypass that reads as exempt is the failure this row exists to "
        f"prevent): "
        f"{unresolved_at or 'none'}",
        register_note,
        f"duplicate writer names (hold this row): {sorted(duplicates) or 'none'}",
    ]
    if undeclared:
        notes.append(
            "undeclared writers (hold this row at PARTIAL — a writer the gate "
            f"never sees that the product does not name): {undeclared}"
        )
    if stale:
        notes.append(
            "stale declarations (hold this row at PARTIAL — a name the outside-gate "
            "population no longer contains: the writer is gone or renamed, its module "
            "is one the walk skips (`tortoise/embeddings.py`), its module would not "
            "parse, or it now ROUTES THROUGH THE GATE (the fix landing) / discovery "
            "classes it `gated`/`producer`/`duplicate` — an unused exemption is a "
            f"stale one, so delete the entry): {stale}"
        )
    if bypass_declarations:
        notes.append(
            "REAL BYPASSES (hold this row at PARTIAL — a bypass is a DEFECT, "
            f"never an exemption): {bypass_declarations}"
        )
    if violations:
        notes.append(
            "uncorroborated declarations (hold this row at PARTIAL — a claim the "
            "entry does not support: an inadmissible class, no owning issue, a "
            "`reason` that does not cite the writer, or a route the body does not "
            "make): "
            f"{violations_at}"
        )
    if register_error:
        notes.append(
            "writer register UNREADABLE or MALFORMED (hold this row at PARTIAL — "
            f"UNMEASURED, never 'nothing declared'): {register_error}"
        )
    if not stated or not shared or not gated:
        verdict = "NOT MET"
    elif (not on_by_default or undeclared or unparsed or duplicates
          or stale or bypass_declarations or violations or register_error):
        # EITHER condition alone means the rule is not the write path's
        # operative rule: the flag is the operator's, and a writer the gate
        # never sees is a path the rule never reaches.
        # ⛔ `#11444`: an OUTSIDE-GATE writer no longer holds the row BY ITSELF.
        # Reading (b) — recorded on `#11085` — accepts a writer that is *"a named,
        # accepted exception in the product"*, and `tortoise.session_indexer::main`
        # is exactly that (declared there, owner `#5008`). It holds whenever the
        # product does NOT name it (`undeclared`), which is the same falsifier with
        # the decision applied.
        # ⛔ And this is the honest limit, not a gap to be closed here: naming a LIVE
        # undeclared writer (as `exempt`, or as `recognised_producer` when its body
        # really does route) DOES green the row — that naming IS the acceptance
        # reading (b) asks for. What a register edit CANNOT do is green it with a
        # name that has no writer behind it (`stale`). Everything else that can be
        # wrong with a name lands in `violations` and holds too: an inadmissible class
        # (`bypass`), no owning issue, a `reason` that does not cite the writer, or a
        # route the body does not make.
        verdict = "PARTIAL"
    else:
        verdict = "MET"
    return Row(
        bullet=1,
        text=BULLETS[0],
        verdict=verdict,
        command="uv run python tools/dod_5090_probe.py --json",
        observed="; ".join(notes),
        notes=notes,
    )


# ── Bullet 2 — "no vector is stored without a path to query it" ─────────────
#: ── Row 2 reads the SAME product register row 1 reads (`#11492`) ─────────────
#: `#11444` taught row 1 to read `tortoise/embed_writer_register.py` and `#11492` left row 2
#: untouched: the same nineteen writers were judged two ways in one file — row 1 said *"every
#: writer routes or is a named exception"*, row 2 said *"unresolved writers: [19 names]"* — because
#: row 2 built its hold from the `unknown` set and never read the declaration. One population, two
#: readings, and the one that holds the epic shut was the one that knew least.
#:
#: ⛔ **Row 2 asks a DIFFERENT question, so the fix is not "reuse row 1's class".** Row 2 asks
#: *"is every stored vector on a label the query leg can serve?"*, and the fact it needs is WHICH
#: LABEL each writer stores to — which no class expresses. `#11492` required the reading to be
#: decided rather than assumed, and it is **Case B**: the register classified but carried no label
#: (`WriterClass` was `{cls, reason, owner_issue}`). Measured at `810eb838c5`, the label is also not
#: DERIVABLE per writer: the `MERGE|CREATE (:Label` literal in a writer's OWN body yields one label
#: for THREE of the twenty, two conflicting labels for two (`_event_plain_merge` `{Event, Source}`,
#: `_rebuild_all_replay` `{Batch, Session}` — and `Batch`/`Session` are NOT served, so a
#: derivation that "picks one" either misses an unserved label or invents a false `NOT MET`; an
#: argument against the DERIVATION, not a claim that either writer is held — both are
#: `unresolved_write`, so `written_labels` flattens their label to the unresolved sentinel before
#: the hold set is computed and their DECLARATION is the reading this row scores), and no usable
#: label for the other fifteen. ⚠️ Those fifteen fail for SEVERAL reasons — do not name one as
#: "the" reason: a label in a `MATCH` the derivation does not credit because a `MATCH` is a read
#: (`_embedding_repair_attempt`'s own `MATCH (e:Event {eventId:$eid})`), a store clause held in a
#: module constant (`_write_capture_turns` executing `_TURN_WRITE_CYPHER`), a property-map merge over
#: a constant tuple (`_update_entity`'s `_CANONICAL_ENTITY_ID_PROPS`), a store delegated to another
#: writer (`_index_process_unit` → `_session_event_write`), a writer that only BUILDS a clause
#: (`set_embedding_clause`), and one whose statement is its caller's
#: (`_event_raw_write_door`). ⛔ Those six are a SAMPLE, NOT the set — the other NINE fail for none
#: of them: a carrier whose value a later pass stores (`add_point`, `_journal_event_raw_write`), a
#: writer whose body issues no store at all — a read, a read into a dict, comment PROSE
#: (`_compare_views`, `_graph_fingerprint`, `turn_prior_vectors`, `_index_gate_read`, `_apply_one`),
#: or a docstring QUOTING one (`_prewipe_synthetic_write_scope`, whose whole body is a `return`; the
#: `SET n.embedding = NULL` it quotes is `_rebuild_all_replay`'s, and that writer is NOT among these
#: fifteen), and a label interpolated at runtime
#: (`restore_graph`'s `(n:{safe_labels})`) — which is why `NO_LIVE_VECTOR` / `LABELS_AT_RUNTIME`
#: exist. The two-label pair is NOT among them: `_event_plain_merge` and
#: `_rebuild_all_replay` keep their `MERGE`s in their own bodies, which is why their own Cypher
#: NAMES two.
#:
#: So the register now DECLARES it (`WriterClass.stores`: a label tuple, or `NO_LIVE_VECTOR` /
#: `LABELS_AT_RUNTIME`) and this row reads that declaration. Same shape as `#11444`, not a second
#: one: **policy as declared data, read by the instrument** — the field's settled pattern (OPA,
#: *Philosophy*: policy is decoupled from the service *"so that the people responsible for policy
#: can read, write, analyze, version, distribute, and in general manage policy separate from the
#: service itself"*), and the same one-owner rule that keeps `route_calls()` from holding a second
#: spelling of the register's own seam list. The vocabulary itself is read from the register
#: (:func:`stores_dispositions`), never re-spelled here.
#:
#: ⛔ What is NOT weakened (the issue's falsifiers, each pinned in
#: `tests/test_dod_5090_probe.py`):
#:   * a writer whose vector lands on a label `run_vector_query` does not serve still holds the
#:     row — a declared label outside `served_labels()` lands in `stored-without-a-query-path` and
#:     the row reads `NOT MET`, so that set can become non-empty again;
#:   * a writer NOTHING declares still holds the row as `unresolved writers` — an undeclared writer
#:     is a hole, never a silent pass (`#11492`'s own falsifier);
#:   * a `bypass` in the declaration holds the row. ⛔ `#11492` found that a bypass was only NOTED
#:     before — `real_bypasses` never reached the verdict — so the note and the hold are now the
#:     same condition;
#:   * a `duplicate` identity and an unparseable module are DIFFERENT defects and are not resolved
#:     by any declaration (a collided writer's label was overwritten, so no label is knowable).
#: ⚠️ The residual, stated rather than implied: a `NO_LIVE_VECTOR` or label-tuple declaration is
#: accepted BY NAME once it is shaped as one (`_store_reading`). The instrument cannot re-derive
#: "this writer stores no live vector" — that is `UNDECIDABLE_WRITE_SHAPES`, data flow — so a false
#: `NO_LIVE_VECTOR` for a writer that DOES store is indistinguishable here from a correct one.
#: That is row 1's `exempt` residual one row over, and its mitigation is the same one: the register
#: is product code under review, and the register's own suite re-derives BOTH halves at HEAD —
#: `::test_every_declared_label_re_derives_at_this_head` for every declared label, and
#: `::test_a_no_live_vector_declaration_executes_no_store_clause` for every `NO_LIVE_VECTOR` entry.
#: ⛔ State the residual at its true size (round-1 review): the `NO_LIVE_VECTOR` scan proves the
#: writer's own body executes no store clause, which is NOT the same as proving it stores nothing —
#: a door that EXECUTES A CALLER-SUPPLIED statement (`sdk::_event_raw_write_door`) cannot be pinned
#: from its own body at all. `#11641` gave that door a BOUND instead of a body-shaped pin — it owns
#: `sdk::_RAW_WRITE_DOOR_LABELS`, REFUSES a statement outside its DECLARED GRAMMAR (`MATCH` /
#: `MERGE` / `CREATE` / `SET` / `REMOVE` / `FOREACH` — every construct on its refusal list, `WITH` /
#: `UNION` / `CALL` / `CALL { … }` / `OPTIONAL MATCH` / `LOAD CSV` / `IN TRANSACTIONS`, is refused
#: in CLAUSE position by `tortoise.vector_write::statement_uses_unmodelled_scope`), REFUSES a
#: statement naming any label outside that set (or one it renders by INTERPOLATION, which no bound can
#: read off the text), and — since the write-target reader landed — REFUSES a statement whose WRITE
#: TARGET alias is unbound, unlabelled, outside the set, or whose labels it mutates. And
#: `tests/test_embed_writer_register.py::LABEL_EVIDENCE` now carries a needle for it. ⚠️ The bound
#: narrows that half; it does not close it: review still catches WHICH label the door's own
#: declaration names, and the bound only catches what the writer's statement TEXT proves. ⛔ THE
#: RESIDUAL, VERBATIM, because an earlier revision of this paragraph claimed totality and was
#: measured FALSE: the bound refuses the DECLARED construct list (`WITH`, `UNION`, `CALL` including
#: `CALL { … }`, `OPTIONAL MATCH`, `LOAD CSV`, `IN TRANSACTIONS`) and a `FOREACH` whose loop variable
#: re-binds an alias a node pattern already binds; the list is a DECLARATION, not a proof of
#: exhaustiveness — a scope construct it does not name is not refused. Clause text a helper builds is
#: read as text like any other, so a construct inside it is refused too. Every half of this is named
#: in the door's own docstring rather than left implied. (The mutating-procedure shape that earlier
#: revisions named here is no longer outside the bound: a procedure is a `CALL`, and `CALL` is
#: refused.)
ROW2_REGISTER_MODULE = REGISTER_MODULE


def stores_dispositions() -> dict[str, str]:
    """The two NON-label dispositions, read from the register that DEFINES them.

    ⛔ Not literals held here. `NO_LIVE_VECTOR` / `LABELS_AT_RUNTIME` are the register's own
    vocabulary, and a copy in this file would drift in the FALSE-HOLD direction: a renamed
    disposition would leave every correct `no-live-vector` declaration reading as an unusable one,
    holding a row on a correct tree. Same reasoning as :func:`route_calls`, one vocabulary over.
    """
    from tortoise import embed_writer_register as register

    return {"no_vector": register.NO_LIVE_VECTOR,
            "at_runtime": register.LABELS_AT_RUNTIME}


def _store_reading(
    entry: object, dispositions: dict[str, str]
) -> tuple[tuple[str, ...] | str | None, str | None]:
    """How to READ one declaration's ``stores`` — ``(reading, violation)``.

    ⛔ No `writer` parameter, on purpose: every violation string below is written WITHOUT a key,
    and both call sites prefix the writer when they record it. Passing the writer in made it read
    as though this function attributed the message, while it never did (a dead parameter — measured
    by AST, `#11492`'s review), and the next editor interpolating it would silently change the
    malformed-`stores` note text.

    ``reading`` is a label tuple, a disposition string, or ``None`` for "nothing usable", and
    ``violation`` is the NAMED reason (empty when the declaration is shaped as a declaration).

    ⛔ Every unusable shape is a VIOLATION, never an unresolved label: an empty tuple, a LIST (a
    frozen declaration must not be editable in place by a consumer — `register.load()` refuses one
    for the same reason), a bare string outside the vocabulary, a non-string member — each is a
    declaration that tried to answer row 2's question and did not, and reading any of them as
    "stores nothing" is exactly the silent pass the falsifier forbids. `#11492` grades a TAMPERED
    or INJECTED register too, so this checks the shape itself rather than inheriting
    `register.load()`'s invariant.
    """
    if isinstance(entry, str):
        if entry in (dispositions["no_vector"], dispositions["at_runtime"]):
            return entry, None
        return None, (
            f"declares stores={entry!r}, which is neither a label tuple nor one of "
            f"{sorted(dispositions.values())}"
        )
    if not isinstance(entry, tuple):
        return None, (
            f"carries no usable `stores` declaration ({type(entry).__name__}) — row 2 cannot say "
            "which label its vector lands on, and that is a hole in the DECLARATION"
        )
    if not entry:
        return None, (
            "declares an EMPTY stores tuple — a shrug is not an answer; say "
            f"{dispositions['no_vector']!r} if the writer stores no live vector"
        )
    bad = [label for label in entry if not isinstance(label, str) or not label]
    if bad:
        return None, f"declares stores={entry!r} with non-label members {bad!r}"
    # A Cypher label's SHAPE, re-checked here rather than inherited from `register.load()` (the
    # same discipline `_OWNER_ISSUE_RE` follows for `owner_issue`). Without it a tampered
    # `("Ev ent",)` would be compared as a label and land outside the served set — a `NOT MET` on
    # a declaration no reader could call valid, i.e. a false hold.
    shapeless = [label for label in entry if not _STORE_LABEL.fullmatch(label)]
    if shapeless:
        return None, f"declares stores={entry!r} with {shapeless!r}, which are not Cypher labels"
    return tuple(entry), None


def resolved_stored_labels(written: dict[str, str] | None = None,
                           writers: dict[str, dict] | None = None) -> dict:
    """Row 2's register read: resolve the writers whose own Cypher named NO label (#11492).

    ``written`` is :func:`written_labels`' derivation — the MEASUREMENT, which always wins where it
    succeeded. This only fills the gaps it left, and only for the population a declaration is owed
    for (`unresolved_write` ∪ `ungated`, the same tuple row 1 scores). ⛔ `duplicate` is NOT in that
    population: a collided identity's label was overwritten by the later definition, so no
    declaration can know it, and resolving one would erase a defect the row reports.

    Returns a dict of buckets, so the caller reports rather than summarises:

    * ``labels`` — ``{writer: (label, …)}`` resolved from the declaration (all compared to served);
    * ``no_vector`` — declared `NO_LIVE_VECTOR` (named; contributes no label);
    * ``at_runtime`` — declared `LABELS_AT_RUNTIME` (HOLDS: no served-set comparison is possible);
    * ``undeclared`` — nothing declares it (HOLDS: the issue's own falsifier);
    * ``violations`` — ``{writer: why}`` a declaration that does not answer the question (HOLDS);
    * ``classes`` — ``{writer: cls}`` for every DECLARED writer, so a `bypass` is read from the
      declaration as well as from the census (row 2 holds on either);
    * ``error`` — the register could not be read (HOLDS as UNMEASURED; every bucket the
      DECLARATION feeds is empty, because an unreadable declaration resolves nothing and blames
      nobody) — but ``partial`` is deliberately NOT one of them: it is measured from the CENSUS,
      before the register is touched, so it survives the failure boundary. ⛔ Keep it there: moving
      that computation inside the `try` (to make this sentence literally true) would delete the
      `MORE THAN ONE label` evidence on the error path, which is the "a `PARTIAL` that names
      nobody" defect the row's own comments and its pin exist to prevent.

    Plus ``error``: a register that cannot be read yields NO resolution at all and a NAMED error,
    never an empty declaration (which would render every writer `undeclared` and blame the product
    for the instrument's failure).
    """
    writers = embedding_writers() if writers is None else writers
    written = written_labels(writers) if written is None else written
    out: dict = {"labels": {}, "no_vector": [], "at_runtime": [], "undeclared": [],
                 "violations": {}, "classes": {}, "partial": [], "contradictions": [],
                 "error": None}
    # ⛔ A PARTIAL measurement is NOT an unresolved one. `UNKNOWN(two labels: Event, Batch)` is the
    # derivation REPORTING TWO LABELS it did measure — one of which may be unserved — so a
    # declaration must never replace it: narrowing it to `("Event",)` (or erasing it with
    # `NO_LIVE_VECTOR`) drops the measured label before the served-set comparison and reads `MET`
    # (`#11492`'s review, reproduced by injection on an `ungated` writer). These HOLD the row and
    # are named; no declaration resolves them.
    out["partial"] = [
        writer for writer, label in sorted(written.items())
        if label.startswith(PARTIAL_LABEL_PREFIX)
    ]
    candidates = [
        writer for writer, label in sorted(written.items())
        if label.startswith("UNKNOWN") and writer not in out["partial"]
        and writers.get(writer, {}).get("kind") in ("unresolved_write", "ungated")
    ]
    try:
        declared = _normalise_declared_keys(declared_writers())
        dispositions = stores_dispositions()
    except Exception as exc:  # noqa: BLE001, RUF100 — fail-closed, and NAMED
        out["error"] = f"{type(exc).__name__}: {exc}"
        return out
    # Every declared CLASS, unfiltered by population: a `bypass` anywhere in the declaration is a
    # defect this row HOLDS on (`#11492` — before it, a bypass only produced a note), and reading it
    # here rather than from the census alone means a bypass declared for a writer whose label
    # derives fine is still named.
    out["classes"] = {w: str(entry.get("cls") or "") for w, entry in declared.items()}
    for writer in candidates:
        if writer not in declared:
            out["undeclared"].append(writer)
            continue
        reading, violation = _store_reading(declared[writer].get("stores"), dispositions)
        if violation:
            out["violations"][writer] = violation
        elif reading == dispositions["no_vector"]:
            out["no_vector"].append(writer)
        elif reading == dispositions["at_runtime"]:
            out["at_runtime"].append(writer)
        else:
            out["labels"][writer] = reading
    # ⛔ A declaration is read for EVERY declared writer, not only for the ones whose derivation
    # failed. Where the derivation SUCCEEDED, a declaration that disagrees with the measured label
    # is a CONTRADICTION — never overwrite the measurement, and never drop the claim either: the
    # contradiction itself is the finding. `#11492`'s last review measured the false pass this
    # closes: a live-register writer declared `stores=("Event", "Batch")` (a shape
    # `register.load()` accepts) was skipped as "derivation already won", so `Batch` never reached
    # the served-set comparison and the row could read `MET` over a declared unserved label.
    # Neither side is assumed right — part of the derivation is a heuristic (module-label stem, a
    # documented override) — so the row reports both sides instead of picking a winner.
    for writer, entry in sorted(declared.items()):
        if writer in candidates or writer in out["violations"]:
            continue
        measured = {label.strip() for label in written.get(writer, "").split(",") if label.strip()}
        if not measured or written.get(writer, "").startswith("UNKNOWN"):
            continue  # no concrete label on this row's side to disagree with
        reading, violation = _store_reading(entry.get("stores"), dispositions)
        if violation:
            # ⛔ The FOURTH shape, and the same drop as the `at_runtime` arm above: an unusable
            # declaration returns `(None, violation)`, and discarding the violation left `None`
            # neither a disposition nor a tuple, so it fell straight through with no contradiction
            # recorded. `('Point', 'Batch', 3)` then read `MET` — a declaration naming a second,
            # unserved label, dropped before the served-set comparison because one member was
            # malformed. This row grades a TAMPERED or INJECTED register too (`_store_reading`'s own
            # docstring says so), so `register.load()`'s refusal is not available as a defence here.
            out["violations"][writer] = violation
        elif reading == dispositions["no_vector"]:
            out["contradictions"].append(
                f"{writer}: declares {dispositions['no_vector']!r} but its own Cypher measures "
                f"{sorted(measured)}")
        elif reading == dispositions["at_runtime"]:
            # ⛔ The THIRD arm, and the one this check first shipped without: `LABELS_AT_RUNTIME` is a
            # `str`, so it satisfied neither arm above and the declaration was silently DROPPED — a
            # contradiction accepted as agreement, and the row free to read `MET`. It contradicts a
            # MEASURED label exactly as the other two do: the derivation resolved `Point`, while the
            # declaration says the label is not statically knowable. Latent only while both live
            # `at_runtime` writers are candidates whose derivation failed; one writer edit away from
            # a false `MET` in the epic's published evidence (review finding, P1).
            out["contradictions"].append(
                f"{writer}: declares {dispositions['at_runtime']!r} but its own Cypher measures "
                f"{sorted(measured)}")
        elif isinstance(reading, tuple) and not set(reading) <= measured:
            out["contradictions"].append(
                f"{writer}: declares {sorted(reading)} but its own Cypher measures "
                f"{sorted(measured)}")
    return out


def written_labels(writers: dict[str, dict] | None = None) -> dict[str, str]:
    """``{writer: label}`` for every write path that STORES a vector.

    Includes the ungated session writers (they store an `:Event` vector) and
    excludes `producer`s: a function that computes a vector another function
    writes is not a label of its own. A label is `UNKNOWN` only when neither
    the writer's own Cypher nor its module's Cypher resolves it — and that is
    reported, never guessed.
    """
    writers = embedding_writers() if writers is None else writers
    # `unresolved_write` is INCLUDED and mapped to UNKNOWN: a function that
    # writes an embedding without a recognised producer call is a write path
    # whose label this instrument could not resolve, so it must hold the row —
    # excluding it made the row claim `written ⊆ served` over a set that left
    # out the very paths its sibling row flags (this tool's own review, P1).
    # `duplicate` is INCLUDED for the same reason: the later definition
    # overwrote the earlier one's label, so that label is UNRESOLVED — leaving
    # it out made bullet 2 read `MET` while bullet 1 held `PARTIAL` on the same
    # hole (this tool's own review, P2).
    return {
        w: (d["label"] if d["kind"] in ("gated", "ungated")
            else ("UNKNOWN(unresolved producer)" if d["kind"] == "unresolved_write"
                  else DUPLICATE_LABEL))
        for w, d in sorted(writers.items())
        if d["kind"] in ("gated", "ungated", "unresolved_write", "duplicate")
    }


def unparsed_modules(writers: dict[str, dict] | None = None) -> list[str]:
    """Modules whose writers could not be enumerated — a hole, never a zero.

    Returns the repo-relative PATHS: one sentinel key for every unparseable
    module named a hole with no location (this tool's own review, P3).
    """
    writers = embedding_writers() if writers is None else writers
    return sorted(d["at"] for d in writers.values() if d["kind"] == "unparsed")


#: `#11747` — a width is irrelevant here: the statement is BUILT, never executed,
#: so the probe plants a two-element vector rather than importing the encoder.
_CACHE_PROJECTION_PROBE_VECTOR: tuple[float, ...] = (0.5, 0.25)


def _statement_stores_vector(stmt, vector_props) -> bool:
    """Would this statement STORE one of ``vector_props``? — the one predicate.

    ⛔ The two doors put the vector in DIFFERENT places, and a probe that read only
    one would be blind to the other (`#12015` review, finding 1):

    * a NODE statement LIFTS each vector into its own top-level param, because it
      needs a per-property store clause (`n.embedding = vecf32($embedding)`) for
      the width narrowing — so the name appears in ``stmt.params``;
    * a RELATION statement passes the whole bag as ONE param (``SET r += $props``),
      so the name appears in ``stmt.params["props"]`` and NOWHERE else.

    Reading only ``stmt.params`` against a relation statement answers "no vector"
    on a tree where the vector IS stored — a FALSE PASS in the instrument, the
    same class of blindness this derivation exists to remove. So both shapes are
    read, and neither is assumed: the predicate asks the statement what it would
    write, wherever that statement puts it.
    """
    params = getattr(stmt, "params", None) or {}
    bags = [params]
    inner = params.get("props")
    if isinstance(inner, dict):
        bags.append(inner)
    return any(prop in bag for bag in bags for prop in vector_props)


def _relationship_target(rel_type: str) -> str:
    """`<relationship:TYPE>` — the ONE owner's spelling, read lazily (`#12015`).

    Imported inside the call so this module keeps importing without a graph or a
    projection available; `cache_projection.relationship_target` is the single place
    the format is decided (the plan's report keys, the operator line and this row's
    orphan set all read it).
    """
    from tortoise.cache_projection import relationship_target

    return relationship_target(rel_type)


def cache_projection_store_labels() -> dict:
    """The labels `cache_projection` can still put a vector on — MEASURED, not read.

    ⛔ `#11747`: `cache_projection.node_statement` was storing a vector on
    `:Session` while this row read `MET` with `stored-without-a-query-path: none`.
    The reason is the DISCOVERY's blind spot, not a gap in the served set: the
    writer emits `f", n.{prop} = {form}"` (a property name INTERPOLATED into the
    clause), which is one of the shapes `_EMBED_WRITE` and the AST pass cannot
    decide — so the writer was absent from `embedding_writers()` entirely and its
    label never reached the served-set comparison. (`tortoise/embed_writer_register`
    says the same in its own words; `INVISIBLE_SHAPES["interpolated_property"]`
    pins it.)

    So this derivation does not read the writer's SOURCE — it RUNS the writer's
    own pipeline (`node_from_row` → `node_statement`) for every label in
    :data:`tortoise.cache_projection.NODE_TABLES` and asks the resulting
    statement what it would actually store. That is immune to the spelling of
    the clause by construction: the answer is read off ``stmt.params``, where a
    vector appears only when a store clause was emitted for it.

    ⛔ BOTH RECORD DOORS ARE PROBED, and neither is assumed: a vector reaches this
    writer through the record's ``embedding`` COLUMN (point/event/source/… carry
    one) or through the row's open-set ``props`` TAIL — which is the ONLY door on
    `public.session`, whose `chk_session_props_no_derived` refuses nothing at all
    (`NOT (props ?| ARRAY[]::text[])`). A shape that REFUSES the name is a
    measurement of that door, not a hole; a label where NO door could be
    built is reported as unmeasured, which holds the row rather than answering
    for it.

    ⛔ THE PLANTED PROPERTY NAME IS DERIVED, never the literal `"embedding"`
    (`#11747` review, finding 3). Both doors are planted with
    :func:`tortoise.cache_projection.stored_vector_props` — the seam designed to be
    widened — so the day a second vector property joins it, this probe plants
    that one too. A literal here would leave an unserved store of the new
    property in NO ``stmt.params`` the probe inspects, and row 2 would keep
    printing `stored-without-a-query-path: none` — the same "second copy of the
    set" this change removes on the served side.

    ⛔ THE WHOLE PER-LABEL DERIVATION IS INSIDE THE FAIL-CLOSED ENVELOPE
    (`#11747` review, finding 4). It used to wrap only the imports and the two
    door-builds, so a label in `cp.NODE_TABLES` that
    `cp.KEY_PROPERTY` does not know made this function RAISE `KeyError` — taking
    the whole DoD report down instead of holding row 2 at `PARTIAL`, which is what
    this function's own contract says an unbuildable shape does. A `KeyError` here
    is a MEASUREMENT FAILURE, and it is recorded as one.

    ⛔ THREE DOORS, not two (`#11747` review, finding 5). The two RECORD doors
    above both enter through :func:`~tortoise.cache_projection.node_from_row`, and
    `record_plan` is public: a caller may hand it a `RecordNode` it built itself,
    which never passes the row→property map. That path is guarded ONLY by
    :func:`~tortoise.cache_projection.node_statement`'s own check, so it is
    probed as its own door — otherwise this row's claim (*"every label a write
    path embeds is one the query path serves"*) would be measurably narrower than
    stated, and deleting that one check would leave the row `MET`.

    ⛔ A RELATIONSHIP IS THE SAME QUESTION WITH NO LABEL AT ALL (`#12015`). The
    record's `public.relation` rows carry a property bag too, and the writer emits
    `SET r += $props` — so an `embedding` key lands a vector on an EDGE, where
    `run_vector_query` (node-only) can never reach it. Node labels alone therefore
    left this half of row 2 unmeasured: the row kept printing
    `stored-without-a-query-path: none` over an edge vector, exactly the blindness
    `#11747` closed for `:Session`. So every RELATIONSHIP TYPE the writer can be
    handed (`tortoise.security.KNOWN_REL_TYPES`) is probed through the two doors
    that exist for edges — the record row (`relation_from_row`) and a
    directly-built `RecordRelation`, since `record_plan` is public — and the types
    that would store one are returned as ``relationships``.

    ⚠️ A relationship type is NOT a label and has no place in the served-label
    comparison: it is reported in its OWN key, and row 2 unions
    ``<relationship:TYPE>`` into the orphan set, where nothing in the served
    vocabulary can ever match it — which is the correct answer (there is no edge
    query path) rather than a comparison that is true by construction.

    Returns ``{"labels": frozenset[str], "relationships": frozenset[str],
    "relationships_probed": frozenset[str], "unmeasured": {target: why},
    "error": str|None}`` — ``relationships_probed`` is the edge population the arm
    PLANTED ON and saw a door refuse, so an empty ``relationships`` cannot be confused
    with an unrun arm — ``error`` means the
    projection could not be imported at all, so nothing was measured and the row
    must not read as if something had been.
    """
    try:
        from tortoise import cache_projection as cp
        from tortoise import record_projection as rp
    except Exception as e:  # an unimportable projection is UNMEASURED
        return {"labels": frozenset(), "relationships": frozenset(),
                "relationships_probed": frozenset(),
                "unmeasured": {}, "error": f"{type(e).__name__}: {e}"}
    stored: set[str] = set()
    edge_stored: set[str] = set()
    #: The predicates this arm PLANTED ON and saw a door REFUSE — appended where the door
    #: RECORDED A DROP of the planted vector, never derived from the input list NOR from
    #: "a statement was built" (both were measured wrong: a door over an empty bag builds
    #: a statement too — `#12015` review, finding C).
    edge_probed: set[str] = set()
    unmeasured: dict[str, str] = {}
    for label in sorted(cp.NODE_TABLES):
        try:
            # The record's own key column for this label (the schema's contract key).
            key_column = rp.snake(cp.KEY_PROPERTY[label])
            key = f"dod-5090-probe-{rp.snake(label)}"
            # DERIVED, not the literal: whatever this projection stores, planted.
            vector_props = sorted(cp.stored_vector_props())
        except Exception as e:
            unmeasured[label] = f"{type(e).__name__}: {e}"
            continue
        vector = list(_CACHE_PROJECTION_PROBE_VECTOR)
        planted = {prop: vector for prop in vector_props}
        # (1) the `props` TAIL — the open set every table carries. (2) the record's
        # `embedding` COLUMN, where the table has one. Both through `node_from_row`.
        record_rows = (
            ("props tail", {key_column: key, "props": dict(planted)}),
            ("embedding column", {key_column: key, **planted}),
        )
        statements: list[tuple[str, object]] = []
        errors: list[str] = []
        for door, row in record_rows:
            try:
                statements.append((door, cp.node_statement(cp.node_from_row(label, row))))
            except Exception as e:  # a refusal is one door's answer
                errors.append(f"{door}: {type(e).__name__}: {e}")
        # (3) the DIRECT writer door — `node_from_row` is out of the path entirely.
        try:
            statements.append((
                "direct writer",
                cp.node_statement(cp.RecordNode(
                    label=label, key=key,
                    props={key_column: key, **planted}))))
        except Exception as e:
            errors.append(f"direct writer: {type(e).__name__}: {e}")
        if not statements:
            # NOTHING could be built: hold the row rather than answering for it.
            unmeasured[label] = "; ".join(errors)
            continue
        for _door, stmt in statements:
            if _statement_stores_vector(stmt, vector_props):
                stored.add(label)
                break
    # ── the EDGE doors (`#12015`) ─────────────────────────────────────────────
    # Every allowlisted predicate: the answer is per-TYPE, so a rule that exempted
    # one type would be invisible to a probe that planted on a single favourite.
    # Both edge doors are built, and a type whose doors could not be built at all is
    # UNMEASURED rather than assumed clean.
    try:
        from tortoise.security import KNOWN_REL_TYPES
        edge_types = sorted(KNOWN_REL_TYPES)
        vector_props = sorted(cp.stored_vector_props())
    except Exception as e:
        unmeasured[_relationship_target("ANY")] = f"{type(e).__name__}: {e}"
        edge_types = []
    for rel_type in edge_types:
        vector = list(_CACHE_PROJECTION_PROBE_VECTOR)
        planted = {prop: vector for prop in vector_props}
        key = f"dod-5090-probe-{rel_type}"
        row = {"rel_type": rel_type, "src_label": "Point", "src_key": key,
               "tgt_label": "Point", "tgt_key": key, "idx": 0,
               "props": dict(planted)}
        edge_statements: list[tuple[str, object]] = []
        edge_errors: list[str] = []
        try:
            edge_statements.append(
                ("record row", cp.relation_statement(cp.relation_from_row(row))))
        except Exception as e:  # a refusal is one door's answer
            edge_errors.append(f"record row: {type(e).__name__}: {e}")
        try:
            edge_statements.append((
                "direct edge",
                cp.relation_statement(cp.RecordRelation(
                    rel_type=rel_type, src_label="Point", src_key=key,
                    tgt_label="Point", tgt_key=key, idx=0,
                    props=dict(planted)))))
        except Exception as e:
            edge_errors.append(f"direct edge: {type(e).__name__}: {e}")
        if not edge_statements:
            unmeasured[cp.relationship_target(rel_type)] = "; ".join(edge_errors)
            continue
        for _door, stmt in edge_statements:
            if _statement_stores_vector(stmt, vector_props):
                edge_stored.add(rel_type)
            # ⛔ A type counts as PROBED only when the planted vector REACHED a door and
            # the door RECORDED refusing it (`#12015` review, finding C — MEASURED: a
            # population derived from `edge_types` stayed full when the plant was
            # disabled, and so did one derived from "a statement was built", because a
            # door over an empty bag builds a statement too. The arm's own observation is
            # the only signal that it planted something and saw the rule work).
            if any(prop in getattr(stmt, "dropped_vectors", {})
                   for prop in vector_props):
                edge_probed.add(rel_type)
    return {"labels": frozenset(stored),
            "relationships": frozenset(edge_stored),
            # ⛔ The POPULATION the edge arm actually PLANTED ON and saw refused
            # (`#12015` review). Without it, "no relationship stores a vector" and "no
            # relationship was ever probed" are the same answer — the empty set — and
            # deleting the whole edge block leaves the door's own test green. It is
            # recorded only where a door RECORDED A DROP of the planted vector, so an arm
            # that stops planting cannot still report full coverage.
            "relationships_probed": frozenset(edge_probed),
            "unmeasured": unmeasured, "error": None}


def served_labels() -> set[str]:
    """The labels `run_vector_query` can query — its declared vocabulary.

    Deliberately NOT the same source as `written_labels`: the written side comes
    from the Cypher literals, the served side from the query vocabulary, so the
    two can disagree — which is the bullet.
    """
    from tortoise.security import VALID_ENTITY_TYPES, entity_label

    # ⛔ The labels the RUNNER actually serves — `run_vector_query` validates
    # against `VALID_ENTITY_TYPES` and then labels the query via `entity_label`.
    # `ENTITY_TYPE_LABELS.values()` is a SECOND, independently maintained
    # declaration: a type added to one and not the other would make this row
    # claim a query path that does not exist (or miss one that does).
    return {entity_label(t) for t in VALID_ENTITY_TYPES}


def scored_labels(
    written: dict[str, str] | None = None, writers: dict[str, dict] | None = None
) -> dict:
    """The map **row 2 SCORES**: the derivation, with the register's declarations filled in.

    Returns ``{"labels", "by_writer", "unresolved", "resolution"}``. ⛔ This is the ONE place the two inputs are combined,
    and it exists because combining them in two places is how a row and its own pin disagree:
    `written_labels()` is the pure MEASUREMENT (it never reads the register) and stays that way, so a
    caller that wants what the row scored — the row itself, or a test pinning the row's implication —
    must come through here. `#11492`'s review caught the first draft claiming in a comment that
    resolving in place made `written_labels()`' own callers see the resolved map; it could not, and a
    pin built on that belief would have reddened the next lane on a CORRECT tree.

    Rules, in one place:

    * a label the DERIVATION resolved wins, always — the register is a supplement, never a
      replacement (`#11444`);
    * a writer whose derivation failed and whose declaration names labels contributes those labels;
    * a writer declared `NO_LIVE_VECTOR` contributes NO label (it has no store of its own);
    * a writer declared `LABELS_AT_RUNTIME` stays unresolved, named by its own disposition;
    * a writer nothing declares, or one whose declaration does not answer, stays unresolved;
    * a `duplicate` identity stays unresolved regardless of any declaration — its label was
      overwritten, which no declaration can repair;
    * ⛔ **a declaration that CONTRADICTS a measured label HOLDS the row** (not "is ignored"). The
      derivation is still never overwritten, but a declared label the writer's own Cypher does not
      name is a disagreement between the product and the measurement — and silently discarding it
      was a FALSE PASS `#11492`'s last review measured: a live-register writer declared
      `stores=("Event", "Batch")` (a shape `register.load()` accepts) was dropped because its
      derivation had succeeded, so `Batch` never reached the served-set comparison and the row could
      read `MET` over a declared unserved label. Neither side is assumed right — the derivation is a
      heuristic in part (module-label stem, a documented override), the declaration is product
      judgement — so the row reports the contradiction instead of picking a winner.
    """
    writers = embedding_writers() if writers is None else writers
    written = written_labels(writers) if written is None else dict(written)
    resolved = resolved_stored_labels(written, writers)
    # The SERVED-SET comparison's subject: every label a write path stores a vector on. A
    # multi-label declaration contributes EACH label, never a joined string — `#11492`'s first
    # draft joined them into one value, which then failed the comparison as a "label" named
    # `Point, Subject, Object, Source, Event` and read `NOT MET` on a correct tree.
    labels: set[str] = {label for label in written.values() if not label.startswith("UNKNOWN")}
    for declared_labels in resolved["labels"].values():
        labels.update(declared_labels)
    for writer in resolved["no_vector"]:
        # A `no-live-vector` declaration contributes NO label — that is what makes
        # `stored-without-a-query-path` a statement about STORES rather than about names.
        written.pop(writer, None)
    # `unresolved` is row 2's HOLD set: every writer whose label is still not a label after the
    # declaration was read. A writer the register resolved is NOT in it (that is the whole fix).
    unresolved = {fn: label for fn, label in written.items() if label.startswith("UNKNOWN")}
    for writer, declared_labels in resolved["labels"].items():
        unresolved.pop(writer, None)
        written[writer] = ", ".join(declared_labels)
    for writer in resolved["at_runtime"]:
        # Kept OUT of `written`'s resolved set and named by its own disposition: the value is not a
        # label, so it must never reach the served-set comparison as one.
        written.pop(writer, None)
        unresolved[writer] = AT_RUNTIME_LABEL
    return {"labels": labels, "by_writer": written, "unresolved": unresolved,
            "resolution": resolved}


def score_path_bullet() -> Row:
    """Bullet 2 — every label a write path embeds is one the query path serves."""
    writers = embedding_writers()
    # ── #11492: the map this row SCORES — the derivation with the product register's declared
    # labels filled in, through the ONE combinator (:func:`scored_labels`). ⛔ The DERIVED label
    # still wins wherever it exists: the register is a SUPPLEMENT to the measurement, never a
    # replacement (`#11444`'s third verification checkbox, one row over).
    scored = scored_labels(writers=writers)
    written, labels, unknown, resolved = (
        scored["by_writer"], scored["labels"], scored["unresolved"], scored["resolution"])
    # ⛔ `#11747`: the cache projection is a WRITE PATH this row could not see. Its store
    # clause interpolates the property name (`f", n.{prop} = {form}"`), which is one of the
    # shapes `_EMBED_WRITE`/`_writes_an_embedding` cannot decide — so it was ABSENT from
    # `embedding_writers()` and a `:Session` vector reached the graph with this row reading
    # `MET`. `cache_projection_store_labels()` measures it by running the writer instead of
    # reading it, and its labels join the SAME served-set comparison as the derived ones:
    # `Session` is not in `served`, so its presence in `projection["labels"]` makes `orphans`
    # non-empty and this row `NOT MET` — the falsifier for a re-introduced write.
    # ⚠️ `projection["labels"]`, NOT the `labels` map above (`#11747` review, finding 5):
    # `labels` is the writer DERIVATION's map and never contains `Session`, so a reader
    # tracing where `Session` enters this comparison would be sent to a map it is not in.
    projection = cache_projection_store_labels()
    served = served_labels()
    # ⛔ `unknown` is row 2's OWN hold set, and it is computed AFTER resolution: a writer the
    # register resolved is no longer unknown (that is the whole fix), while a writer declared
    # `labels-at-runtime` still is — the declaration answered WHERE the vector lands with "it
    # depends", which cannot be compared to the served set. The at-runtime writers are ALSO
    # named by their own note below, so they are held once and reported twice, never counted as
    # `unresolved writers` (a different, sharper defect: nothing declares them at all).
    # ⛔ An UNREADABLE register means NOTHING was resolved, so every outside-gate writer is
    # "unresolved" only in the sense that the instrument could not ask — and naming them as
    # `unresolved writers` ("nothing in the product says which label they store on") would blame
    # the PRODUCT for the instrument's own failure in the same cell that says it does not.
    # `#11492`'s round-2 review measured exactly that contradiction on the live population; the
    # UNMEASURED note is the whole of what an unreadable register can say.
    unresolved_undeclared = [] if resolved["error"] else sorted(
        fn for fn in unknown if writers[fn]["kind"] in ("unresolved_write", "ungated")
        and fn not in resolved["at_runtime"] and fn not in resolved["violations"]
        and fn not in resolved["partial"]
    )
    duplicates = sorted(w for w, d in writers.items() if d["kind"] == "duplicate")
    unparsed = unparsed_modules(writers)
    # A writer whose label is unresolved and which NO note above accounts for: a `gated` writer
    # whose own Cypher named NO single label (label `UNKNOWN`, one label — NOT the
    # `UNKNOWN(two labels: …)` shape, which is a MEASUREMENT: it is subtracted via
    # `resolved["partial"]` and named by its own "MORE THAN ONE label" note), or a `duplicate`
    # (reported by its own note below). `#11492`'s review caught the first draft narrowing the
    # `unresolved writers` note to the declaration population and thereby dropping the `gated` case
    # from the evidence while still holding the row — a `PARTIAL` naming nobody.
    #
    # ⛔ Subtracted BY KIND, never emptied on `resolved["error"]` (round-2 review, P2): the two
    # note populations have DIFFERENT subjects. `unresolved_undeclared` is about writers only the
    # register could have declared, so an unreadable register makes it unknowable and it is emptied
    # above. A `gated` writer's label came from its OWN Cypher and the register was never consulted
    # for it — its declaration is unaffected by whether the register could be read, so emptying this
    # set would re-create the very defect the comment above records: a `PARTIAL` that names nobody.
    # The expression is equivalent to the previous one whenever the register IS readable.
    unexplained = sorted(
        set(unknown) - set(resolved["at_runtime"])
        - set(resolved["violations"]) - set(duplicates) - set(resolved["partial"])
        - {fn for fn in unknown
           if writers[fn]["kind"] in ("unresolved_write", "ungated")}
    )
    # ⛔ `#11747`: the PROJECTION's labels are compared against the served set in the same
    # comparison, but they are deliberately NOT folded into `labels` — that map is this row's
    # writer DERIVATION, and its note is pinned as such (a fixture-built writer tree asserts its
    # own exact content). Keeping the two populations separate in the notes and united only in
    # the comparison is what lets the row catch an unserved projection label WITHOUT the
    # instrument's writer tree appearing to gain a writer it does not have.
    # ⛔ `#12015`: a RELATIONSHIP type that would store a vector is an orphan BY
    # CONSTRUCTION — no edge query path exists for it to be served by — so it joins
    # the ONE orphan set as `<relationship:TYPE>` and can never match `served`. It is
    # unioned here rather than compared on its own so the row keeps a single answer:
    # a second comparison would be a second place for the verdict to disagree with
    # the line that prints it.
    edge_orphans = {_relationship_target(rel_type)
                    for rel_type in (projection.get("relationships") or ())}
    orphans = (labels | set(projection["labels"]) | edge_orphans) - served
    notes = [
        f"written labels: {sorted(labels)}",
        # The evidence for the comparison below: HOW the cache projection's labels were obtained.
        # Named on every run — `#11492` learned that a `MET` resting on a declaration is
        # invisible unless the row prints where each label came from, and this one rests on a
        # MEASUREMENT the discovery cannot make at all (`#11747`).
        "cache projection vector targets (MEASURED by running "
        "cache_projection.node_from_row → node_statement over every record label — the "
        "`embedding` column, the `props` tail, and a directly-built `RecordNode` — "
        f"#11747): {sorted(projection['labels'])}",
        # `#12015`: the same measurement over EDGES, under its OWN key — a relationship
        # type is not a label and must not be read as one. Named on every run for the
        # same reason the line above is: a `MET` must not rest on an unprinted set.
        "cache projection RELATIONSHIP vector targets (MEASURED by running "
        "cache_projection.relation_from_row → relation_statement over every allowlisted "
        "predicate — the `props` bag and a directly-built `RecordRelation` — "
        f"#12015): {sorted(projection['relationships'])}",
        f"served labels (run_vector_query vocabulary): {sorted(served)}",
        f"stored-without-a-query-path: {sorted(orphans) or 'none'}",
    ]
    if projection["error"]:
        notes.append(
            "cache projection vector targets: UNMEASURED "
            f"({projection['error']}) — the projection could not be imported, so NOTHING is "
            "claimed about where it stores (hold this row at PARTIAL)"
        )
    if projection["unmeasured"]:
        notes.append(
            "cache projection targets whose store could not be MEASURED (hold this row at "
            "PARTIAL — no door could be built for it). A record label, or a "
            "`<relationship:TYPE>` for an EDGE, which is NOT a label: "
            + "; ".join(f"{target}: {why}" for target, why in
                        sorted(projection["unmeasured"].items()))
        )
    if resolved["error"]:
        notes.append(
            "#11492 — labels resolved from the product register "
            f"({ROW2_REGISTER_MODULE}): UNMEASURED ({resolved['error']}) — the register could "
            "not be read, so NO writer is resolved and none is blamed on the product"
        )
    elif (resolved["labels"] or resolved["no_vector"] or resolved["at_runtime"]
          or resolved["undeclared"] or resolved["violations"]):
        # The histogram row 1 prints over its own population, one row over: a reader must be able
        # to see that a `MET` here rests on DECLARED labels rather than derived ones.
        notes.append(
            "#11492 — labels resolved from the product register "
            f"({ROW2_REGISTER_MODULE}): {len(resolved['labels'])} label-resolved · "
            f"{len(resolved['no_vector'])} declared no-live-vector · "
            f"{len(resolved['at_runtime'])} labels-at-runtime · "
            f"{len(resolved['undeclared'])} undeclared · "
            f"{len(resolved['violations'])} malformed"
            + ("; " + ", ".join(
                f"{w}={', '.join(v)}" for w, v in sorted(resolved['labels'].items()))
               if resolved["labels"] else "")
            + ("; " + ", ".join(resolved["no_vector"])
               if resolved["no_vector"] else "")
        )
    if resolved["at_runtime"]:
        notes.append(
            "writers whose stored label is decided at RUNTIME (hold this row at PARTIAL — the "
            "declaration cannot name a label, so no served-set comparison is possible): "
            f"{resolved['at_runtime']}"
        )
    if resolved["violations"]:
        notes.append(
            "malformed stores declarations (hold this row at PARTIAL — a declaration that did "
            "not answer WHERE the vector lands): "
            + ", ".join(f"{w}: {why}" for w, why in sorted(resolved["violations"].items()))
        )
    # ⛔ `#11492` round-2/round-3 review. The two note populations have DIFFERENT subjects and are
    # gated DIFFERENTLY, one layer up — do not "restore" them to a shared rule:
    #   * `unresolved_undeclared` is about writers only the REGISTER could have declared, which is
    #     unknowable when the register cannot be read → it is EMPTIED on `resolved["error"]`, so an
    #     unreadable register cannot print "nothing in the product says which label they store on"
    #     in the same cell that says it blames nobody. The blame belongs to the instrument.
    #   * `unexplained` is about a writer whose label came from its OWN Cypher and named NO single
    #     label (`gated`, label `UNKNOWN`) — the register was never consulted for it, so an
    #     unreadable register does not excuse dropping it → it is subtracted BY KIND, never emptied,
    #     or the row holds `PARTIAL` while naming nobody (the defect round 1 fixed). ⛔ The
    #     `UNKNOWN(two labels: …)` shape is NOT here: it is a MEASUREMENT, removed by
    #     `- set(resolved["partial"])` and named by its own "MORE THAN ONE label" note — deleting
    #     that note as "redundant" would re-create a `PARTIAL` naming nobody, one branch over.
    if unresolved_undeclared:
        notes.append(
            "unresolved writers (hold this row at PARTIAL — nothing in the product says which "
            f"label they store on): {unresolved_undeclared}"
        )
    if resolved["contradictions"]:
        notes.append(
            "declarations that CONTRADICT the writer's own Cypher (hold this row at PARTIAL — the "
            "declaration is not ignored and the measurement is not overwritten; the disagreement "
            f"is the finding): {'; '.join(resolved['contradictions'])}"
        )
    if resolved["partial"]:
        notes.append(
            "writers whose own Cypher names MORE THAN ONE label (hold this row at PARTIAL — a "
            "PARTIAL measurement is not a gap, so no declaration may narrow it): "
            f"{resolved['partial']}"
        )
    if unexplained:
        notes.append(
            "writers whose label is unresolved and which this row does not resolve from a "
            f"declaration (hold this row at PARTIAL): {unexplained}"
        )
    if duplicates:
        notes.append(
            "duplicate writer identities (hold this row at PARTIAL — the later "
            f"definition overwrote the earlier one's label): {duplicates}"
        )
    if unparsed:
        notes.append(f"unparseable modules (hold this row at PARTIAL): {unparsed}")
    # ── #10541: the census turns "cannot resolve" into a SCORED answer ───────
    # A function the vocabulary could not resolve is only a hole in the
    # measurement if NOTHING says which of the three honest classes it is in.
    # Once it carries one, the row has a RECORDED ANSWER for every unresolved
    # writer — which is the whole of #10541. It is NOT a licence to move the
    # verdict: the writer set is unchanged and `unknown` still holds the row at
    # PARTIAL below, because re-classing a writer does not make the vocabulary
    # resolve it. (An earlier draft read this as "a complete measurement" and
    # flipped the row to MET, breaking bullet 2's own falsifier; reverted.)
    # ⛔ The census population is the `unresolved_write` set — the SAME set
    # `test_every_unresolved_writer_carries_a_census_class` walks — and NOT
    # `unknown`, which also carries the `duplicate` identities. A duplicate is
    # a DIFFERENT defect (two definitions collided, so the later one's label
    # overwrote the earlier one) and has no key it could EVER have in
    # `WRITER_CENSUS`, so iterating `unknown` printed a census over a
    # population of ZERO whenever a duplicate was the only unresolved entry,
    # and reported a collision as "#10541's own defect". This row is quoted
    # into #5090's body as evidence, so the misattribution is what a reader
    # sees (review at `adde2763f`, blocking).
    # ⛔ `#11492` narrowed this population: it is the writers row 2 could STILL not resolve —
    # `unresolved_write` AND still in `unknown` — because once the product declares a label the
    # writer's label IS resolved, and the census's job (give a scored answer for a writer whose
    # label the vocabulary could not resolve) is discharged by the declaration instead. The
    # `kind` gate is kept for the reason below: a `duplicate` identity is a DIFFERENT defect with
    # no key it could ever have in `WRITER_CENSUS`, so it is never a census subject even when its
    # label is unresolved.
    unresolved_writers = {fn: d for fn, d in writers.items()
                          if d["kind"] == "unresolved_write" and fn in unknown}
    # ⛔ `real_bypasses` is computed over EVERY writer, never over `unresolved_writers`: a
    # register declaration resolves a writer OUT of `unknown`, and deriving the bypass hold from
    # that population would mean the better the declaration, the fewer defects the row can see —
    # a census `bypass` on a declared writer would read `MET` and appear in no note (`#11492`'s
    # round-2 review, both reviewers, verified by injection). The census class is a property of the
    # WRITER; whether this row managed to resolve its label is not.
    # `unknown` is STILL wider than this population by three label-keyed groups, and none is a
    # census SUBJECT — but one of them is a census MEMBER, which is why the header says `N of M`
    # and the segments reconcile instead of pretending they are the same set:
    #   * `duplicate` identities — a collided name has no census key, ever;
    #   * `gated`/`ungated` writers whose label derivation failed — their ROUTE is resolved, which
    #     is what `kind` records, so the three #10541 route classes cannot apply to them;
    #   * a writer declared `labels-at-runtime` — unresolved by DECLARATION rather than by a gap.
    #     ⛔ It IS counted here when its `kind` is `unresolved_write` (both of `#11492`'s runtime
    #     writers are), and its census class is the one that applies. An earlier wording of this
    #     comment claimed the opposite; the note is the artifact, so it names what it counts.
    # An out-of-vocabulary class (a typo'd or retired entry) is NOT a class: it
    # would otherwise fall through BOTH `by_class` (gated on CENSUS_CLASSES) and a
    # `unclassified` gated only on `is None` — the shape this code had before the
    # `not in CENSUS_CLASSES` gate below — leaving the writer named nowhere and
    # every class total reading 0 over a non-empty population. The sibling
    # falsifier catches the typo in CI; the instrument must not lie meanwhile.
    unclassified = sorted(fn for fn in unresolved_writers
                          if _census_class(fn) not in CENSUS_CLASSES)
    by_class: dict[str, list[str]] = {}
    for fn in unresolved_writers:
        cls = _census_class(fn)
        if cls in CENSUS_CLASSES:
            by_class.setdefault(cls, []).append(fn)
    if unresolved_writers:
        notes.append(
            # ⛔ THE HEADER NAMES THE PROPERTY, NEVER ONE LABEL (`#11495`'s review, measured).
            # `unresolved_writers` is `kind == "unresolved_write" and fn in unknown`, and
            # `scored_labels()` puts a writer declared `labels-at-runtime` into `unknown` with
            # `AT_RUNTIME_LABEL` — so a census SUBJECT can carry `UNKNOWN(unresolved producer)`
            # OR `UNKNOWN(labels decided at runtime)`. The header used to say the former, and on
            # the LIVE tree BOTH of its two subjects carried the latter, i.e. the note
            # contradicted the runtime note four cells to its left inside one published row.
            # "whose label is STILL UNKNOWN" is exactly the predicate the population is built
            # from, so no segment on the line can falsify it.
            "#10541 census over the writers whose label is STILL UNKNOWN (neither "
            f"the derivation nor a declaration resolved it) — {len(unresolved_writers)} of "
            f"{len(unknown)} UNKNOWN-labelled writers: "
            # A middle dot, not `" | "`: `Row.observed` joins these notes into
            # ONE markdown table cell, and a pipe there used to split a
            # 4-column row into seven. `_table` now escapes the character at
            # the EMITTER (`_cell`), so this is legibility, not the guard —
            # which is the point: a per-note rule is only as wide as the last
            # author to remember it.
            + " · ".join(
                # ⛔ The `unclassified=` segment is what makes the totals RECONCILE
                # to the `N` in the header. An out-of-vocabulary class is in no
                # `by_class` bucket, so without this the line could read
                # "1 of 1 … recognised_producer=0 · exempt=0 · bypass=0" — a
                # header claiming one writer and three totals summing to zero,
                # which is the same "silently omits a writer" defect this note
                # was re-worded to stop. `by_class` covers in-vocabulary classes
                # and `unclassified` covers the rest, so the segments now sum to
                # `len(unresolved_writers)` by construction.
                [
                    f"{cls}={len(by_class.get(cls, []))}"
                    + (f" ({', '.join(sorted(by_class[cls]))})"
                       if by_class.get(cls) else "")
                    for cls in CENSUS_CLASSES
                ]
                + ([
                    f"unclassified={len(unclassified)}"
                    f" ({', '.join(unclassified)})"
                ] if unclassified else [])
            )
        )
    if unclassified:
        notes.append(
            f"UNCLASSIFIED writers (hold this row at PARTIAL — #10541's own "
            f"defect): {unclassified}"
        )
    real_bypasses = sorted(
        fn for fn in writers if _census_class(fn) == CENSUS_BYPASS
    )
    if real_bypasses:
        notes.append(
            f"REAL BYPASSES (#10541 class 3 — a defect, not an exemption): "
            f"{real_bypasses}"
        )
    overridden = sorted(_overridden_writers() & set(written))
    if overridden:
        notes.append(
            f"labels assigned by the DOCUMENTED override, not derived from "
            f"Cypher (reported, as that comment promises): {overridden}"
        )
    # ⛔ #10541: the census CLASSIFIES the unresolved set; it does not license
    # moving this verdict. `test_an_unresolved_writer_is_reported_and_holds_
    # bullet_two_at_partial` is bullet 2's own falsifier and it encodes the
    # honesty rule: while a writer is unresolved the row cannot claim a
    # COMPLETE measurement. Re-classing the writer does not make the vocabulary
    # resolve it, so the row keeps holding. The open fork is recorded on
    # #10541 — flipping this is a decision, not a cleanup.
    # ⛔ #11492: what DOES move it, and what still cannot.
    #   * a writer RESOLVED from the product register (`resolved['labels']` / `no_vector`)
    #     is no longer in `unknown`, so the hold it used to create is gone — that is
    #     reading (b) applied to THIS row's question, not row 1's class reused: the
    #     declaration states a LABEL, and the label is compared to the served set here.
    #   * a declared label OUTSIDE the served set is `orphans`, i.e. `NOT MET` — the
    #     falsifier's whole point (`stored-without-a-query-path` can go non-empty again).
    #   * `at_runtime`, a malformed declaration, and an UNREADABLE register all HOLD: the
    #     first cannot be compared to the served set at all, and the last two are holes in
    #     the declaration rather than answers. A `MET` over a register nobody could read is
    #     the UNMEASURED-is-not-nothing misattribution row 1 refuses, one row over.
    #   * a `bypass` in the declaration HOLDS — `#11492` found it was only NOTED before
    #     (`real_bypasses` never reached the verdict), so the note and the hold are now one
    #     condition. It is read from the DECLARATION as well as the census: the register is
    #     where a class is declared, and `CENSUS_BYPASS` is the instrument's own vocabulary.
    register_bypasses = sorted(
        writer for writer in writers
        if str((resolved.get("classes") or {}).get(writer, "")) == CENSUS_BYPASS
    )
    if register_bypasses:
        notes.append(
            "REAL BYPASSES (hold this row at PARTIAL — a bypass is a DEFECT, never an "
            f"exemption): {register_bypasses}"
        )
    verdict = "NOT MET" if orphans else (
        "PARTIAL" if (unknown or unparsed or duplicates or real_bypasses or register_bypasses
                      or resolved["partial"] or resolved["contradictions"]
                      or resolved["at_runtime"] or resolved["violations"]
                      or resolved["error"]
                      or projection["error"] or projection["unmeasured"])
        else "MET")
    return Row(
        bullet=2,
        text=BULLETS[1],
        verdict=verdict,
        command="uv run python tools/dod_5090_probe.py --json",
        observed="; ".join(notes),
        notes=notes,
    )


# ── Bullet 3 — "a fallback query reports the fallback" ─────────────────────


class _Catalogue:
    """A scripted graph: returns a different (rows, exc) per call, in order.

    Deterministic, embedded, no LLM, no corpus — the smallest case that can
    distinguish the three mechanisms `run_vector_query` can take.
    """

    def __init__(self, responses: list[tuple[list, BaseException | None]]) -> None:
        self._responses = list(responses)
        self._i = 0

    def query(self, cypher, params=None, timeout=None):
        if self._i >= len(self._responses):
            rows, exc = [], None
        else:
            rows, exc = self._responses[self._i]
            self._i += 1
        if exc is not None:
            raise exc
        return type("_R", (), {"result_set": rows or []})()


VEC = [0.1] * 384


def fallback_probe(degraded: dict | None = None) -> dict[str, str]:
    """Force each of the three vector mechanisms and read the caller's trace.

    Returns ``{mechanism: verdict-from-the-trace}`` where each value is the
    trace entry's own ``mechanism`` (or a readable failure). Bullet 3 is MET
    only when a caller can tell a full scan from an indexed read WITHOUT
    reading `search_engine.py` — which is `#4999`'s contract.

    ``degraded``, when passed, is FILLED with the same three probes' `#2952`
    reading (``{mechanism: {"degraded", "reason", "declared"}}``). It is an
    out-parameter rather than a second return value so every existing caller
    keeps the mechanisms dict it already reads.
    """
    # The breakers are module state: snapshot and restore them, because a
    # "read-only" instrument must not un-trip the vector breaker in whatever
    # process imports it (this tool's own review, P2 — `measure()` already does
    # this for the gate flags). ⛔ NOT `deepcopy`: `_CircuitBreaker` holds a
    # `threading.Lock` (`search_engine.py:167`), so a deepcopy raises
    # `TypeError: cannot pickle '_thread.lock' object` — measured, and it fired
    # the moment a real search had registered a breaker, which is exactly the
    # state this restore exists to protect. The SLOT values are primitives.
    import tortoise.search_engine as _se
    from tortoise.search_engine import (
        VECTOR_MECHANISM_KEY,
        declared_degraded_read,
        reset_circuit_breakers,
        run_vector_query,
    )

    _SLOTS = ("fail_threshold", "cooldown_seconds", "_fails", "_open_until", "_probing")

    def _snapshot() -> dict[str, dict]:
        return {
            name: {slot: getattr(b, slot) for slot in _SLOTS if hasattr(b, slot)}
            for name, b in (getattr(_se, "_BREAKERS", {}) or {}).items()
        }

    snapshot = _snapshot()

    def _restore() -> None:
        breakers = getattr(_se, "_BREAKERS", None)
        if not isinstance(breakers, dict):
            return
        for name, state in snapshot.items():
            b = breakers.get(name)
            if b is None:
                continue
            for slot, value in state.items():
                # A slot we cannot restore is reported by the state it leaves,
                # not fatal to a measurement.
                with contextlib.suppress(Exception):
                    setattr(b, slot, value)
        # Every breaker this probe CREATED is absent from `snapshot`, so the loop
        # above cannot reach it — and in a FRESH process the snapshot is `{}`,
        # which is the CLI's state, `measure()`'s state, and the state this
        # tool's first test starts from. `_probe_mechanisms` lazily registers the
        # vector breaker (`_breaker_record` → `search_engine._breaker`), so that
        # entry stayed behind in the CALLER's process: measured at `37fa883bd`,
        # `before {}` → `after {'vector': (0, 0.0, False)}` (review P3). The
        # previous restore could not see it because its falsifier PRE-CREATED the
        # breaker — the one state in which the snapshot is not empty. Reset first,
        # so a thread already holding the object keeps a CLOSED, clean breaker,
        # then un-register it: a read-only instrument must leave the module's map
        # the way it found it. `_BREAKERS_LOCK` is the map's own lock and is never
        # held across a query (that is `_CircuitBreaker._lock`), so this cannot
        # stall the caller; a lock we cannot take still gets the reset above.
        created = [name for name in list(breakers) if name not in snapshot]
        if not created:
            return
        for name in created:
            b = breakers.get(name)
            if b is not None:
                with contextlib.suppress(Exception):
                    b.reset()
        lock = getattr(_se, "_BREAKERS_LOCK", None)
        acquired = False
        if lock is not None:
            # A lock we cannot take (or does not exist) still gets the reset
            # above; the un-registering below is then done unlocked, which is no
            # worse than the module's own unlocked `reset_circuit_breakers()`.
            with contextlib.suppress(Exception):
                acquired = lock.acquire(timeout=5.0)
        try:
            for name in created:
                breakers.pop(name, None)
        finally:
            if acquired:
                with contextlib.suppress(Exception):
                    lock.release()

    out: dict[str, str] = {}

    def _entry(trace):
        return next((e for e in trace if e.get("leg") == "vector"), {})

    try:
        return _probe_mechanisms(out, _entry, run_vector_query,
                                 VECTOR_MECHANISM_KEY, reset_circuit_breakers,
                                 declared_degraded_read, degraded)
    finally:
        _restore()


def _probe_mechanisms(out, _entry, run_vector_query, VECTOR_MECHANISM_KEY,
                      reset_circuit_breakers,
                      declared_degraded_read=None,
                      degraded_out: dict | None = None) -> dict[str, str]:
    def _note_degraded(name: str, trace) -> None:
        """`#2952`'s meaning, MEASURED — not asserted in prose in an epic body.

        `#4999` reports the mechanism ADDITIVELY and deliberately leaves
        `degraded` alone (`search_engine.py`: per `#2952` it means "the vector
        leg contributed no semantic results", and a brute-force scan DOES
        return semantic rows — flipping it "would make
        `require_hybrid_read` refuse a valid semantic read"). `#5090` row 3
        cited that as HALF of its evidence cell while the row's own command
        read only the mechanism key, so that half was unscored — the exact
        defect `#10465` exists to remove (review round 13, P3).
        """
        if degraded_out is None:
            return
        entry = _entry(trace)
        degraded_out[name] = {
            "degraded": entry.get("degraded"),
            "reason": entry.get("reason"),
            # The marker `#2952`'s consumer derives from the trace: `None`
            # means "this read is not declared degraded".
            "declared": (declared_degraded_read(trace)
                         if declared_degraded_read else "UNMEASURED"),
        }

    reset_circuit_breakers()
    # ⛔ The fake ROWS are `(id, score)` PAIRS, and that is load-bearing: the
    # brute-force scan reads `row[1]` as its score, so a one-element row raises
    # `IndexError: tuple index out of range` — whose message contains "index",
    # so the leg records `degraded=True, reason="index_missing"` and returns
    # `[]` while the MECHANISM key still reads `scan`. The probe then reported a
    # healthy `scan` from a read that FAILED (measured at `268512824`: `scan:
    # degraded=True reason='index_missing' count=0`). That is why `#5090` row
    # 3's `degraded` clause could never have been scored from its own command —
    # the probe never took the healthy scan branch at all (review round 13, P3).
    # (a) the index path: signature A answers with rows.
    trace: list = []
    run_vector_query(_Catalogue([([("a", 0.9), ("b", 0.8)], None)]), VEC,
                     entity_type="point", is_embedded=False, leg_trace=trace)
    out["index"] = str(_entry(trace).get(VECTOR_MECHANISM_KEY, "<key absent>"))
    _note_degraded("index", trace)

    # (b) an engine with no index API: the deliberate full scan.
    reset_circuit_breakers()
    trace = []
    run_vector_query(_Catalogue([([("a", 0.9)], None)]), VEC,
                     entity_type="point", is_embedded=True, leg_trace=trace)
    out["scan"] = str(_entry(trace).get(VECTOR_MECHANISM_KEY, "<key absent>"))
    _note_degraded("scan", trace)

    # (c) an index was EXPECTED and the query failed: the fallback that must
    #     announce itself — #4999's whole subject.
    reset_circuit_breakers()
    trace = []
    run_vector_query(
        _Catalogue([
            ([], RuntimeError("Invalid arguments for procedure")),
            ([], RuntimeError("Invalid arguments for procedure")),
            ([("a", 0.9)], None),
        ]),
        VEC, entity_type="point", is_embedded=False, leg_trace=trace,
    )
    out["scan_fallback"] = str(_entry(trace).get(VECTOR_MECHANISM_KEY, "<key absent>"))
    _note_degraded("scan_fallback", trace)
    return out


def score_fallback_bullet() -> Row:
    """Bullet 3 — the three mechanisms are distinguishable in the caller's trace."""
    degraded: dict = {}
    observed = fallback_probe(degraded=degraded)
    distinguishable = len(set(observed.values())) == len(observed)
    named = all(v.startswith("<key absent>") is False for v in observed.values())
    # ⛔ `#2952`'s meaning is HALF of what `#5090` row 3 claims ("`degraded` keeps
    # `#2952`'s meaning"), so this row's own command reads it: the mechanism is
    # reported ADDITIVELY, `degraded` stays ``False`` on all three, and no
    # `declared_degraded_read` marker is derived from the trace. A read that
    # did not happen is `UNMEASURED` (`untouched` False), never a silent pass.
    untouched = bool(degraded) and all(
        d["degraded"] is False and d["declared"] is None
        for d in degraded.values())
    notes = [f"{k}: {v}" for k, v in observed.items()]
    for k, d in degraded.items():
        marker = d["declared"]
        notes.append(
            f"{k}: degraded={d['degraded']!r} reason={d['reason']!r} "
            f"declared_degraded_read={marker if marker is not None else 'None'}")
    notes.append(f"#2952 untouched on every mechanism: {untouched} "
                 f"({len(degraded)}/{len(observed)} mechanisms read)")
    notes.append(f"distinguishable: {distinguishable}; every path reports: {named}")
    return Row(
        bullet=3,
        text=BULLETS[2],
        verdict=("MET" if distinguishable and named and untouched
                 else "NOT MET"),
        command="uv run python tools/dod_5090_probe.py --json",
        observed="; ".join(notes),
        notes=notes,
    )


# ── Bullet 4 — "V1 is decided and implemented" ─────────────────────────────

#: The phrases a DECIDED V1 would carry. Absence of all of them is the finding,
#: not a guess: V1 is `#5090`'s owner gate and only a ruling can satisfy it.
#: Case-insensitive, because a real ruling is prose ("V1: decided — Point-only,
#: 2026-11-…") and a spelling-sensitive search is a false negative with no way
#: to correct it but editing this tool (this tool's own review, P2).
V1_DECIDED_MARKERS = ("v1: decided", "v1 is decided", "v1 decided")
#: Where a ruling would land, so an undecided read says what it looked at.
V1_SURFACES = (
    "docs/architecture/STORAGE-ARCHITECTURE.md",
    "tortoise/projection/__init__.py",
)


def _plain_words(text: str) -> str:
    """The words of ``text``, with markdown and punctuation removed.

    Only for comparing a documentation line against the DoD bullet it may be
    quoting: emphasis (`**V1 is decided**`), a checklist box (`- [x] …`),
    backticks and sentence punctuation all differ between a quotation and the
    bullet it quotes, while the WORDS do not.
    """
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))


def _restates_the_dod_bullet(line: str, bullet: str) -> bool:
    """True when ``line`` QUOTES the bullet instead of recording a ruling.

    ⛔ `V1_DECIDED_MARKERS` are matched as SUBSTRINGS, and one marker is a
    substring of the requirement it is checking: `BULLETS[3]` is `"V1 is
    decided and implemented."` and `"v1 is decided" in "v1 is decided and
    implemented."` is `True`. So any doc that **restates the bullet** — the
    single most likely edit to `V1_SURFACES`, and the editing style this PR
    uses everywhere — registered as a record that the ruling exists and flipped
    row 4 from `NOT MET` to `PARTIAL`: publishing "a V1 ruling exists" when the
    owner has not ruled. Row 4 IS the epic's owner gate, so that direction
    fabricates a decision (this tool's own review, P3).

    Containment is checked BOTH ways against the bullet's own words, because a
    wrapped quotation defeats a same-line substring test: `V1 is decided and` /
    `implemented.` on two lines raises the marker on the first, and that line is
    a PREFIX of the bullet. A line whose words are inside the bullet, or that
    contains the bullet's words, is a quotation.

    The safe direction here is a false `NOT MET`: the row is capped at `PARTIAL`
    by construction (`MET` is unreachable — a record cannot prove what shipped),
    so excluding a quotation costs nothing, while admitting one invents the
    ruling the epic stays open for.
    """
    norm_line, norm_bullet = _plain_words(line), _plain_words(bullet)
    if not norm_bullet or not norm_line:
        return False
    return norm_bullet in norm_line or norm_line in norm_bullet


def score_v1_bullet() -> Row:
    """Bullet 4 — a V1 ruling exists and the code implements it."""
    decided: list[str] = []
    quoted: list[str] = []
    open_lines: list[str] = []
    for rel in V1_SURFACES:
        path = REPO_ROOT / rel
        if not path.exists():
            continue
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not re.search(r"\bV1\b", line):
                continue
            if any(m in line.lower() for m in V1_DECIDED_MARKERS):
                # ⛔ A quotation of the bullet is NOT a record: see
                # `_restates_the_dod_bullet`. Reported, never silently dropped,
                # so the read says what it dismissed.
                if _restates_the_dod_bullet(line, BULLETS[3]):
                    quoted.append(f"{rel}:{i}: {line.strip()}")
                    continue
                decided.append(f"{rel}:{i}: {line.strip()}")
            elif re.search(r"\bOPEN\b|\bopen decision\b|human gate|owner decision",
                           line) or "never spends that decision" in line:
                open_lines.append(f"{rel}:{i}: {line.strip()}")
    notes = [
        f"decided markers found: {len(decided)}",
        "implemented: UNMEASURED(not what a decision record proves — a ruling "
        "creates the follow-up work; this row can only see the record)",
    ]
    notes += [f"restates the DoD bullet, not a ruling (excluded): {ln}"
              for ln in quoted[:3]]
    notes += [f"open: {ln}" for ln in open_lines[:3]]
    # The bullet is "decided AND implemented", and the implementation half is
    # unmeasurable here (nothing a decision RECORD contains proves what shipped),
    # so a record caps this row at PARTIAL however many decided markers it
    # carries — MET is unreachable by construction. The opposite direction is
    # equally flat: a surface that still says OPEN does NOT lower the row, so a
    # doc QUOTING the bullet cannot flip it to `NOT MET`. `open_lines` therefore
    # decides nothing about the verdict — it is carried in `notes` (review, P3:
    # the branch used to claim a distinction it did not make).
    verdict = "NOT MET" if not decided else "PARTIAL"
    return Row(
        bullet=4,
        text=BULLETS[3],
        verdict=verdict,
        command="uv run python tools/dod_5090_probe.py --json",
        observed="; ".join(notes),
        notes=notes,
    )


# ── §12.2a — the distinct-predicate count ──────────────────────────────────


#: The modules a predicate can live in without being a second RULE SET: the
#: embed seam's own module graph, walked rather than listed.
EMBED_PATH_ROOTS: tuple[str, ...] = (
    # NOT `tortoise/embeddings.py`, and for a reason about WALKING only: it is
    # the CALLER and imports half the tree, so WALKING it counted 47 unrelated
    # pattern owners as predicates. ⛔ That is NOT an argument that the seam
    # owns no predicate — see `OWNERSHIP_ONLY` immediately below, which scans
    # it for its own patterns without following its imports.
    "tortoise/embed_gate.py",
    "tortoise/value_gate.py",
)

#: Modules that can answer `NO-EMBED` but must not be WALKED.
#:
#: ⛔ `tortoise/embeddings.py` is the seam that DECIDES whether the gate is
#: consulted at all (`if embed_gate.embed_gate_enabled():`), and it is the
#: conventional home of an inline skip — so its OWN module-scope patterns are
#: predicates in exactly §12.2a's sense. It is excluded from
#: `EMBED_PATH_ROOTS` for a different reason that reads as if it covered this
#: one: its IMPORTS fan out to half the tree, which is an argument about
#: reachability, not about its own assignments. (An import makes the IMPORTER
#: depend on the IMPORTEE — Python Language Reference §5 — so no forward walk
#: from the gate can ever enter its caller; the edge runs the wrong way.)
#:
#: ⚠️ Scanned for OWNERSHIP ONLY: its imports are NOT followed.
OWNERSHIP_ONLY: tuple[str, ...] = (
    "tortoise/embeddings.py",
)


def _resolve(root: Path, parts: list[str]) -> set[str]:
    """The in-tree module file(s) a dotted import path names."""
    out: set[str] = set()
    if not parts:
        return out
    base = root / "tortoise"
    for i in range(1, len(parts) + 1):
        pkg = base.joinpath(*parts[:i])
        for cand in (pkg.with_suffix(".py"), pkg / "__init__.py"):
            if cand.exists():
                out.add(cand.relative_to(root).as_posix())
    return out


def _import_edges(root: Path, tree, path: Path) -> set[str]:
    """In-tree modules one file imports: the AST, because a regex is not.

    ⛔ Both shapes below are IN THIS TREE and both were missed by a one-line
    `from … import …` regex, each producing `count: 1 / MET` on a tree with two
    predicates (review round 3, P2):

    * `import tortoise.kind_index as ki` (a plain `import` — `kind_classifier.py:204`);
    * `from .pkg import (\n    a,\n    b,\n)` (a parenthesised, multi-line list —
      `api.py:175`, `entities.py:28`, `session_indexer.py:32`, …).
    """
    import ast

    targets: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split(".")
                if parts[0] == "tortoise":
                    targets |= _resolve(root, parts[1:])
        elif isinstance(node, ast.ImportFrom):
            spec = node.module or ""
            if node.level:  # relative: `from .` / `from ..pkg`
                # ⛔ Resolved against the IMPORTING FILE'S package, not always
                # `tortoise/`: `from .rules import _P` inside
                # `tortoise/gates/__init__.py` names `gates/rules.py`, and
                # resolving it as `tortoise/rules.py` dropped a whole subtree
                # (review round 5, P2).
                pkg_dir = path.relative_to(root).parent
                for _ in range(node.level - 1):
                    pkg_dir = pkg_dir.parent
                base = [x for x in pkg_dir.parts if x != "tortoise"]
                parts = [x for x in spec.split(".") if x]
                targets |= _resolve(root, [*base, *parts]) if parts else set()
                for alias in node.names:
                    targets |= _resolve(root, [*base, *parts, alias.name])
            elif spec == "tortoise" or spec.startswith("tortoise."):
                parts = [x for x in spec.split(".")[1:] if x]
                targets |= _resolve(root, parts)
                for alias in node.names:
                    targets |= _resolve(root, [*parts, alias.name])
    return targets


def predicate_population() -> list[str]:
    """Every module the §12.2a count SCANS: reachable-by-import PLUS owned.

    ⛔ The two halves answer two different questions and are not
    interchangeable. `embed_path_modules()` walks the gate's own import graph,
    so it can only enter a module the gate DEPENDS ON. A module that depends on
    the gate — most importantly the seam that decides whether to consult it
    (`tortoise/embeddings.py`) — owns predicates by §12.2a's definition while
    being unreachable from that walk, because an import makes the importer
    depend on the importee and never the reverse (Python Language Reference §5).
    Scanning it WITHOUT walking it is what closes that hole without dragging in
    the forty-odd unrelated pattern owners its imports reach (this tool's own
    review, P3: the count read `1 / MET` over a seam module carrying a second
    rule set).
    """
    return sorted(set(embed_path_modules()) | {
        m for m in OWNERSHIP_ONLY if (REPO_ROOT / m).exists()
    })


def embed_path_modules() -> set[str]:
    """Every `tortoise/**.py` reachable from the embed seam by LOCAL imports.

    ⛔ A two-file whitelist (this tool's own review, P1) is a search that can
    only find what it already names: a THIRD module owning its own patterns —
    the drift §12.2a forbids — measured as drift-free. This walks the import
    graph instead, so a new `embed_rules.py` imported anywhere on the embed path
    is counted the moment it exists.
    """
    return _embed_path_modules(REPO_ROOT)


@functools.lru_cache(maxsize=8)
def _embed_path_modules(root: Path) -> frozenset[str]:
    edges: dict[str, set[str]] = {}
    for path in sorted((root / "tortoise").rglob("*.py")):
        try:
            src = path.read_text(encoding="utf-8")
            tree = ast.parse(src)
        except (OSError, SyntaxError):
            continue
        mod = path.relative_to(root).as_posix()
        targets: set[str] = _import_edges(root, tree, path)
        edges[mod] = targets
    seen: set[str] = set()
    queue = [r for r in EMBED_PATH_ROOTS if (root / r).exists()]
    while queue:
        r = queue.pop()
        if r in seen:
            continue
        seen.add(r)
        queue.extend(edges.get(r, ()))
    return frozenset(seen)


def _walk_module_scope(node: ast.AST):
    """Yield every descendant of ``node`` that is NOT inside a function body.

    The population this is used for is "patterns OWNED AT IMPORT TIME": module
    statements, and class bodies (executed at import). A `re.compile` inside a
    `def`/method/lambda **body** is not owned then — it runs when it is called,
    in that call — so descending into it counts a helper's local pattern as a
    second rule set.
    """
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        yield child
        yield from _walk_module_scope(child)


def count_embed_predicates() -> dict:
    """Count the distinct predicates that can answer `NO-EMBED` (target: 1).

    A predicate is a module in `predicate_population()` that **owns identifier
    patterns** — an assignment executed at MODULE SCOPE (a `re.compile` at
    module level, or in a class body, since both run at import) — that is, the
    modules the gate imports PLUS the seam modules that decide whether the gate
    is consulted, scanned for ownership without their imports being followed.

    ⛔ A pattern compiled **inside a function body** is NOT module scope and is
    NOT counted. The docstring above said "`re.compile` at module scope" while
    the code ran `ast.walk()` over each top-level statement, which descends into
    `FunctionDef.body` — so one ordinary helper-local `pat = re.compile(...)` in
    any module of the population recorded `count: 2`, `verdict: "DRIFT"`, and
    reddened `test_the_embed_decision_has_exactly_one_predicate` (a required
    `core` leg) on a change that is not drift: asserting today's tree reds a
    required check on a CORRECT change, whose cheapest-looking repair is to
    delete the guard (this tool's own review, P3).

    Class bodies are deliberately still scanned (`_walk_module_scope` prunes
    only function/lambda bodies): a class-level pattern LIST is owned at import
    and would be a real second rule set, and a false `MET` is the failure this
    instrument exists to remove — and nothing in the population is a class
    attribute today, so this widens no live count.

    `embed_gate.embedding_verdict` is the ASK, not a predicate: it must call the
    shared one and report the shared rule id, which is checked separately
    (`RULE_SET is value_gate.RULES`).

    ⛔ A module that will not parse, or will not read, is a HOLE in the
    measurement and NOT a zero — the same doctrine the writer map states at
    `_embedding_writers`/`score_rule_bullet` ("a module that will not parse is a
    HOLE in the measurement, not a zero: its writers … are invisible to every
    row"). It used to `continue`, so an unparseable module carrying a second
    pattern list left `count: 1` and `verdict: MET` (this tool's own review,
    P3) — the verdict this whole instrument exists to withhold.
    """
    import tortoise.embed_gate as embed_gate
    import tortoise.value_gate as value_gate

    owners: list[str] = []
    unparseable: list[str] = []
    for rel in predicate_population():
        # AST, not a text pattern: an ALIASED import (`import re as _re`) is
        # still a pattern, and a substring scan missed exactly that in this
        # tool's own mutation check — the falsifier PASSED on mutated code,
        # which is the one failure a falsifier must not have.
        try:
            tree = ast.parse((REPO_ROOT / rel).read_text(encoding="utf-8"))
        except (OSError, SyntaxError, ValueError):
            # ⛔ Reported, never `continue`d: keyed by the PATH so the verdict
            # can name the hole it could not read.
            unparseable.append(rel)
            continue
        # ANY import-time expression that calls `.compile(` owns a pattern —
        # a single assignment AND a collection (`_PATS = [re.compile(…), …]`),
        # which is the most natural shape for "a second pattern LIST" and read
        # `count: 1 / MET` when only the single-assignment form was matched.
        # ⛔ Module scope, as the docstring says: `_walk_module_scope` prunes
        # function/lambda bodies, so a helper's LOCAL `re.compile` is not a
        # second predicate owner (review, P3).
        # ⚠️ Deliberate asymmetry, documented: an unrelated import-time
        # pattern-owning module imported by the gate also counts as an owner (a
        # false DRIFT). Over-reporting drift is the safe direction here; a false
        # `MET` is the failure this whole instrument exists to remove.
        for node in _walk_module_scope(tree):
            if isinstance(node, (ast.Assign, ast.AnnAssign)) and any(
                isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
                and c.func.attr == "compile"
                for c in ast.walk(node)
            ):
                owners.append(rel)
                break
    return {
        "count": len(owners),
        "target": 1,
        "owners": owners,
        "predicate": PREDICATE_NAME,
        "rule_ids": list(value_gate.RULES),
        "rule_set_is_shared_identity": embed_gate.RULE_SET is value_gate.RULES,
        # ⛔ `UNDETERMINED` BEFORE the two real verdicts: a population this
        # instrument could not read cannot certify `MET`, and calling it `DRIFT`
        # would claim a second rule set it never SAW. Neither answer is available
        # from a hole, so the third value is the honest one.
        "unparseable": unparseable,
        "verdict": ("UNDETERMINED" if unparseable else
                    "MET" if len(owners) == 1 and embed_gate.RULE_SET is value_gate.RULES
                    else "DRIFT"),
    }


# ── The census ─────────────────────────────────────────────────────────────


def _read(graph, cypher: str):
    """Issue a census READ through the engine's read-only verb.

    "Stores nothing" was this module's doctrine and only a convention in the
    code: `graph.query` is the write-CAPABLE verb, so the doctrine rested on
    every statement written here happening to be read-shaped (review round 15,
    P3). `GRAPH.RO_QUERY` is rejected server-side for a write, which makes the
    doctrine the engine's to enforce instead of this file's to remember.
    Measured on falkordb-server 4.20.6: `CALL db.indexes()`, `CALL db.labels()`,
    the per-label `count(n)` and the width probe answer identically through
    `ro_query`.
    """
    return graph.ro_query(cypher)


def _cypher_label(label: str) -> str:
    """``label`` as a Cypher node label, quoted by the product's OWN rule.

    ⛔ A label cannot be a query parameter, so it reaches Cypher by
    interpolation — and its value comes from the graph's own schema
    (`CALL db.labels()`, the index catalogue), i.e. from whatever a customer
    wrote there. Interpolating it raw is the shape `tortoise/graph_key_restore.py`
    already names (*"a label/property name reaches Cypher by interpolation and
    comes from the graph's own schema"*) and `tortoise/security.py` answers with
    an allowlist for the query runners. The census cannot allowlist (it reports
    labels it has never seen — that is its job), so it uses the repo's one
    identifier-quoting rule, `graph_key_restore._quote_identifier` (#7846):
    bare when bare, else backtick-quoted with interior backticks DOUBLED.
    IMPORTED, never copied — a second quoting rule would drift from the first.
    Measured on falkordb-server 4.20.6: a doubled interior backtick is REJECTED
    at parse time, so a backtick-bearing label fails closed into
    `stored=UNMEASURED(...)` rather than forming a re-purposed query.
    """
    from tortoise.graph_key_restore import _quote_identifier

    return _quote_identifier(str(label))


# ── `entity_type` ← label, and the callers that reach it (#11088) ───────────

#: The ONE SDK method the vector leg is reachable through. Named, not derived:
#: it is the function `degradation_chain` is called FROM (sdk.py's
#: `tortoise_fts_query`), and a rename there must red
#: `tests/test_dod_5090_probe.py`'s caller pin rather than silently make this
#: census name a method that no longer exists.
VECTOR_LEG_SDK_METHOD = "tortoise_fts_query"

#: The module-level runner the leg itself lives in — the second half of the
#: path, so a reader can walk it without searching.
VECTOR_LEG_RUNNER = "tortoise.search_engine.run_vector_query"

#: How many stored vectors the width probe samples. A label can hold vectors of
#: MORE THAN ONE width (an `EMBEDDING_DIM` rotation), and `stored × width` is a
#: figure only while every sampled row agrees — so the probe samples a handful
#: and REFUSES on disagreement instead of extrapolating one row across the label.
WIDTH_PROBE_SAMPLES = 5


def affected_entity_types(label: str) -> list[str]:
    """The ``entity_type`` values a vector-leg query for ``label`` answers.

    ⛔ The mapping is NOT one-to-one and that is exactly why this is not
    bookkeeping (#11088): ``security.entity_label`` sends **two** entity types
    to `:Source` (``document``, ``source``) and **two** to `:Point`
    (``point``, ``operator``). A census that names only labels therefore names
    the wrong population for the surface question it exists to answer — a
    reader would remove `:Object`'s vectors believing one query changes when it
    is one, or believe `:Source`'s vectors serve one caller when they serve two.

    Derived from :data:`tortoise.security.VALID_ENTITY_TYPES` through the
    product's ONE translation (:func:`tortoise.security.entity_label`) — never
    a second table here, so a type added to the vocabulary appears without an
    edit to this file.
    """
    from tortoise.security import VALID_ENTITY_TYPES, entity_label

    return sorted(
        et for et in VALID_ENTITY_TYPES
        if entity_label(et) == label
    )


def vector_leg_surface() -> list[dict]:
    """Every MCP tool / REST route that can hand `entity_type` to the vector leg.

    ⛔ DERIVED from :data:`tortoise.tool_registry.TOOL_REGISTRY`, the ONE
    declaration of the MCP/REST surface — never a list written here. A
    hardcoded list would be a second declaration of the surface, and the whole
    point of this half of #11088 is that the consequence is stated for *the
    tools that exist now*: a tool added tomorrow (or removed today) is named by
    this function without an edit, and one retired here cannot linger as a
    claim about a surface that no longer exists.

    The binding is `sdk_method`, not the tool NAME: `tortoise_search` and
    `tortoise_query` are two MCP tools, and neither a name match nor a
    `sdk_method`-only filter names both (see the ⛔ paragraph below).

    `takes_entity_type` is read from the SDK method's SIGNATURE — a fact, not a
    prose sniff on a description. ⛔ It is a fact about the METHOD, not about a
    given handler: the SDK signature has `entity_type`, so this field reads
    `True` for every entry bound to it whether or not that handler forwards the
    argument. What pins a handler's FORWARDING is `named_by` — `handler forwards
    entity_type into the hybrid engine` appears only for a handler whose body
    does (review round 2, P3: the earlier wording claimed this field catches a
    stopped-forwarding handler, which it does not and cannot).

    ⛔ A `sdk_method` filter ALONE IS INCOMPLETE, and incompleteness here is a
    false claim rather than an omission: `tortoise_query`'s registry binding is
    `paginated_query` (its structural arm), yet its MCP handler forwards a
    `text=` argument to `tortoise_fts_query(entity_type=…)`. A census built on
    the binding would therefore name ONE tool where the surface has two. So the
    handler body is AST-scanned for a `tortoise_fts_query` call that passes
    `entity_type` — the same technique this file already uses for embedding
    writers. The TWO handler-shaped sources are unioned with a THIRD,
    :func:`_sdk_methods_on_vector_leg` (SDK methods that ask the leg for a fixed
    `entity_type`, including through a private helper), and each entry carries
    `named_by` saying which sources named it.
    """
    try:
        from tortoise.tool_registry import TOOL_REGISTRY
    except Exception as e:  # pragma: no cover - import failure is the caller's
        return [{"tools": f"UNMEASURED({type(e).__name__}: {e})"}]

    takes: object = "UNMEASURED(the SDK method could not be imported)"
    try:
        import inspect

        from tortoise.sdk import TortoiseSDK

        params = inspect.signature(
            getattr(TortoiseSDK, VECTOR_LEG_SDK_METHOD)).parameters
        takes = "entity_type" in params
    except Exception as e:
        takes = f"UNMEASURED({type(e).__name__}: {e})"

    by_binding = {t.name for t in TOOL_REGISTRY
                  if getattr(t, "sdk_method", None) == VECTOR_LEG_SDK_METHOD}
    by_handler, handler_error = _handlers_forwarding_entity_type()
    fixed_by_method, fixed_error, fixed_declared = \
        _sdk_methods_on_vector_leg()
    #: Tool name → the `entity_type` values its SDK method asks the leg for
    #: WITHOUT the caller choosing one (third source — see
    #: `_sdk_methods_on_vector_leg`).
    by_fixed = {t.name: sorted(fixed_by_method[t.sdk_method])
                for t in TOOL_REGISTRY
                if getattr(t, "sdk_method", None) in fixed_by_method}
    registered = {t.name for t in TOOL_REGISTRY}
    spec = {t.name: getattr(t, "rest_spec", None) for t in TOOL_REGISTRY}

    out: list[dict] = []
    for name in sorted(by_binding | by_handler | set(by_fixed)):
        sources: list[str] = []
        if name in by_binding:
            sources.append("registry sdk_method")
        if name in by_handler:
            sources.append("handler forwards entity_type into the hybrid engine")
        if name in by_fixed:
            sources.append("its SDK method calls the leg with a fixed entity_type")
        how = " AND ".join(sources)
        route = spec.get(name)
        if route is not None:
            rest = f"{route.method} {route.path}"
        elif name in registered:
            # ⛔ A registry tool with NO `rest_spec` is MCP-only — that is a
            # fact about the surface, not a failed measurement, and conflating
            # the two would report `tortoise_query` as unregistered.
            rest = "MCP only (no REST route)"
        else:
            rest = "UNMEASURED(handler not in TOOL_REGISTRY — not on the MCP surface)"
        fixed = by_fixed.get(name)
        entry = {
            "tool": name,
            "rest": rest,
            "named_by": how,
            "takes_entity_type": takes,
            # ⛔ Two different facts about the same tool, and collapsing them
            # would overstate one class: a PASS-THROUGH tool can ask the leg
            # for ANY type the caller supplies (`"caller-supplied"`), while a
            # fixed-type tool asks for one the caller never names. A tool that
            # is BOTH keeps both — `entity_types` reads `caller-supplied` (the
            # wider fact, so a consequence is never understated) and the fixed
            # list rides beside it as `also_fixed_entity_types` (review round 9,
            # P3: the single value silently dropped the pass-through reach of a
            # tool that also fixes a type).
            "entity_types": ("caller-supplied" if (name in by_handler or fixed is None)
                             else fixed),
        }
        if fixed is not None and name in by_handler:
            entry["also_fixed_entity_types"] = fixed
        out.append(entry)
    # ⛔ THE DERIVED-FAILURE LINE IS DECIDED FROM THE SCAN, NOT FROM `out`
    # (#11389 review, P2). The DECLARED BOUND below is appended to `out`, which
    # made `not out` UNREACHABLE whenever a declaration existed: a surface that
    # could not be derived AT ALL then published only the by-reference caveat,
    # which reads as "the scan ran and this is its known edge" — the two
    # channels separated in one direction (#11337) and silently collapsed in the
    # other. `derived_tool_rows` is the scan's own verdict, captured before
    # either caveat line is added.
    derived_tool_rows = bool(out)
    for error in (handler_error, fixed_error):
        if error:
            out.append({"tools": error})
    # ⛔ An UNRESOLVABLE `VECTOR_LEG_SDK_METHOD` empties BOTH sets, so `out` was
    # `[]` — and `takes`, which is computed to be `UNMEASURED(...)` in exactly
    # that case, had no entry to ride on. The surface then read as a MEASURED
    # "no tool can pass entity_type" beside `entity_types_whose_query_results_
    # change` naming real types: the under-reporting direction, produced by the
    # very drift the resolution pin exists to catch — a pin protects the repo,
    # not the instrument as shipped (review round 2, P2).
    if not derived_tool_rows or isinstance(takes, str):
        out.append({"tools": f"UNMEASURED(the surface could not be derived: "
                             f"{takes if isinstance(takes, str) else 'no tool resolves'})"})
    # ⛔ The DECLARED BOUND is published on its own line, never merged into the
    # scan-failure channel: it says the scan ran and this is its known edge, which a
    # reader must be able to tell apart from "the scan could not run" (#11337).
    # Emitted AFTER the derived-failure line for the same reason (#11389 review,
    # P2): the failure is the more important fact, and appending it last would
    # leave it present but unreachable when both apply.
    if fixed_declared:
        out.append({"tools": fixed_declared})
    return out


#: The comprehension forms, each of which introduces its OWN scope (#11389
#: review, P2). Named once so the two walks that must agree about them cannot
#: drift apart.
_COMPREHENSION_NODES = (ast.ListComp, ast.SetComp, ast.DictComp,
                        ast.GeneratorExp)


def _comprehension_targets(node: ast.AST) -> frozenset[str]:
    """The names a comprehension binds in its OWN scope.

    ⛔ A COMPREHENSION IS A SCOPE (#11389 review, P2). Its `for` target shadows
    an enclosing binding for the element, the `if` clauses and the body, so a
    `Load` of that name INSIDE the comprehension refers to the comprehension —
    not to a leg-reaching function of the enclosing method. Reading it as a
    mention published a spurious `declared bound` entry for
    `[_review_pool for _review_pool in rows]` and
    `[r for _review_pool in rows if _review_pool]`: the cry-wolf direction this
    channel exists to narrow, and invisible to the pinned fixture because its
    element is `x`. Collects TARGETS only (which may destructure, `for a, b in
    …`), never the element expression.
    """
    bound: set[str] = set()
    for gen in getattr(node, "generators", ()):
        for sub in ast.walk(gen.target):
            if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Store):
                bound.add(sub.id)
    return frozenset(bound)


def _sdk_methods_on_vector_leg(
        ) -> tuple[dict[str, set[str]], str | None, str | None]:
    """SDK methods that call the vector leg, and the `entity_type` they ask for.

    ⛔ THE THIRD SOURCE, and the one neither of the other two can see (#11088
    review round 7, P1). A handler that never forwards `entity_type` can still
    reach the leg, because the SDK method it calls names the type itself:
    `recall_state` calls `tortoise_fts_query(…, entity_type="object")` while its
    `object_centric` default is on, and `search_sessions` calls it with
    `entity_type="event"` — so `tortoise_recall`'s DEFAULT `mode="state"` and
    `tortoise_search_sessions` both change when those labels' vectors are
    removed, and a derivation built only on forwarded arguments named neither.
    Under-reporting the surface of a surface change is the dangerous direction:
    an owner approves a change that is larger than the request describes.

    A call with **no** `entity_type=` runs the SDK's own default (`"point"`),
    which is as much of an entrant as an explicit one — the label it lands on is
    affected the same way. A non-literal argument is reported as
    `UNKNOWN(<expression>)`, never guessed into a type.

    Returns `(method name → entity types, error, declared_bound)`.

    ⛔ THE TWO REFUSAL CHANNELS ARE SEPARATE (#11337 review, P1). `error` is the
    reason the scan could not be TAKEN — a missing or unparseable source — and it
    is what makes a caller read "no SDK method reaches the leg" correctly. The
    declared bound is a DIFFERENT fact: the scan ran and its claim has a known
    edge. Overloading one slot for both made a legitimate by-reference shape red
    the real-tree tests whose precondition is `error is None`, under a message
    that reads like a scan failure — a false block on a correct tree, whose
    cheapest repair is to delete the declaration.
    """
    path = REPO_ROOT / "tortoise" / "sdk.py"
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except Exception as e:
        return ({}, f"UNMEASURED(SDK scan failed: {type(e).__name__}: {e})",
                None)

    #: The SDK's declared default for the argument, read from the source rather
    #: than restated here: `entity_type: str = "point"` in
    #: `TortoiseSDK.tortoise_fts_query`. A call that omits the kwarg runs it.
    default_entity_type = "UNKNOWN(no declared default found)"

    def _is_target(node: ast.AST) -> bool:
        if isinstance(node, ast.Attribute):
            return node.attr == VECTOR_LEG_SDK_METHOD
        return isinstance(node, ast.Name) and node.id == VECTOR_LEG_SDK_METHOD

    def _types_for(call: ast.Call) -> list[str]:
        found: list[str] = []
        for kw in call.keywords:
            if kw.arg is None:  # `**{…}` — the keywords are not in the tree
                found.append("UNKNOWN(**kwargs unpacking)")
            elif kw.arg == "entity_type":
                found.append(kw.value.value if isinstance(kw.value, ast.Constant)
                             and isinstance(kw.value.value, str)
                             else f"UNKNOWN({ast.unparse(kw.value)[:40]})")
        return found or [default_entity_type]

    def _reachable_by_name(node: ast.AST, entrant: dict[str, set[str]],
                           shadowed: set[str] | None = None) -> str | None:
        """The leg-reaching function this expression names, if any.

        Used ONLY to declare a reach this scan cannot resolve — never to add an
        entrant. `self.<name>` and a bare `<name>` are in-module method
        references; `getattr(self, "<name>")` is the same reach with the name
        in a string literal, and `getattr(self, <expr>)` is a reach whose target
        cannot be resolved at all — declared, because declaring adds no entrant
        and a possibly-short caller list is the safe direction to report.
        """
        # ⛔ `shadowed` is the local bindings of the enclosing function: a bare
        # `recall_state = 5` binds the NAME without referring to the method, and
        # declaring it would caveat a complete table — the same cry-wolf failure
        # the resolved-call exclusion removes (#11337 review, P2).
        # ⛔ A STORE/DEL name is a BINDING, not a mention (#11389 review, P2 —
        # a regression introduced by skipping comprehension scopes: the mention
        # walk then reached `[_pool for _pool in rows]` and read the target as a
        # reference, a spurious caveat on a complete table).
        if (isinstance(node, ast.Name) and node.id in entrant
                and isinstance(node.ctx, ast.Load)
                and node.id not in (shadowed or ())):
            return node.id
        # ⛔ `ctx` applies to an Attribute too (#11389 review, P3): `self._pool = v`,
        # `self._pool += v`, `self._pool: T = v` and `del self._pool` are BINDINGS,
        # and publishing one is the same spurious caveat the bare-Name guard
        # prevents.
        if (isinstance(node, ast.Attribute) and node.attr in entrant
                and isinstance(node.ctx, ast.Load)):
            receiver = node.value
            # `self`, and the in-module receivers that stand for it (#11389
            # review, P3): `super()` and `type(self)`. A genuinely FOREIGN
            # receiver (`self.conn.helper(1)`) is excluded HERE — but note the
            # scope of that statement: this channel only. The transitive CLOSURE
            # above matches `func.attr` with no receiver check, so it still names
            # such a caller (pre-existing behaviour, unchanged by #11337). This
            # channel's job is to add the caveat, not to correct the closure.
            if isinstance(receiver, ast.Name) and receiver.id == "self":
                return node.attr
            if (isinstance(receiver, ast.Call)
                    and isinstance(receiver.func, ast.Name)
                    and receiver.func.id in ("super", "type")):
                return node.attr
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "getattr" and len(node.args) in (2, 3)
                and isinstance(node.args[0], ast.Name)
                and node.args[0].id == "self"):
            name = node.args[1]
            if (isinstance(name, ast.Constant) and isinstance(name.value, str)
                    and name.value in entrant):
                return name.value
            if not isinstance(name, ast.Constant):
                # ⛔ The name is not in the tree, so whether this reaches the leg
                # is UNKNOWABLE here — not "no" (#11337 review). Reporting it
                # costs a declaration and prevents the silent-short-list class.
                return f"getattr(self, {ast.unparse(name)[:40]})"
        return None

    def _scan(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
        found: set[str] = set()
        # Only THIS function's own body: a nested `def` is its own method, and
        # attributing its call to the enclosing scope would name the wrong one.
        # ⛔ But a nested `class` BODY IS this function's body (#11389 review,
        # P3): it executes when the method does, so a leg call sitting in it is
        # a real fixed-type use — the same doctrine the mention walk follows.
        # Skipping it here left such a call in NEITHER channel: not measured by
        # this walk, and excluded from the mention walk as a call it "owns".
        stack: list[ast.AST] = list(fn.body)
        while stack:
            node = stack.pop()
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if isinstance(node, ast.Call) and _is_target(node.func):
                found.update(_types_for(node))
            stack.extend(ast.iter_child_nodes(node))
        return found

    # ⛔ TWO PASSES, because the default must be resolved BEFORE any call site
    # is read: a single `ast.walk` reaches `suggest_entry_points` (which omits
    # the kwarg, i.e. runs the default) before it reaches the leg's own
    # signature, so a one-pass version reported
    # `UNKNOWN(no declared default found)` for a call whose type is `point`.
    # An order-dependent derivation is not a measurement.
    functions = [n for n in ast.walk(tree)
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    leg = next((fn for fn in functions if fn.name == VECTOR_LEG_SDK_METHOD), None)
    if leg is not None:
        # The leg's own signature declares the default. The leg itself is never
        # counted as an ENTRANT — that would name it as its own caller.
        for default, a in zip(
                [None] * (len(leg.args.args) - len(leg.args.defaults))
                + list(leg.args.defaults), leg.args.args, strict=True):
            if a.arg == "entity_type" and isinstance(default, ast.Constant):
                default_entity_type = default.value
        for a, default in zip(leg.args.kwonlyargs, leg.args.kw_defaults,
                              strict=True):
            if a.arg == "entity_type" and isinstance(default, ast.Constant):
                default_entity_type = default.value

    direct: dict[str, set[str]] = {}
    callees: dict[str, set[str]] = {}
    known = {fn.name for fn in functions}
    for fn in functions:
        if fn is leg:
            continue
        types = _scan(fn)
        if types:
            direct[fn.name] = types
        calls: set[str] = set()
        stack = list(fn.body)
        while stack:
            node = stack.pop()
            # Same doctrine as `_scan`: a nested class body runs with this
            # function, so its calls are this function's edges.
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if isinstance(node, ast.Call):
                func = node.func
                name = (func.attr if isinstance(func, ast.Attribute)
                        else func.id if isinstance(func, ast.Name) else None)
                if name in known:
                    calls.add(name)
            stack.extend(ast.iter_child_nodes(node))
        if calls:
            callees[fn.name] = calls

    # ⛔ ONE HOP IS NOT THE POPULATION (review round 9, P2). A registry tool can
    # reach the leg through a PRIVATE helper: `review_connections` →
    # `_review_add`/`_review_prune` → `_review_pool` → the leg. Keying the scan
    # on the ENCLOSING function named `_review_pool` (not a `TOOL_REGISTRY
    # .sdk_method`) and then dropped the tool with no entry anywhere — a silent
    # hole in the caller list of an approval request. The transitive closure
    # over in-module calls closes that class: a method is an entrant iff any
    # path from it reaches the leg THROUGH A NAME-RESOLVED in-module call, and
    # it carries the entity types found along the paths it can take.
    #
    # ⚠️ DECLARED BOUND (#11337): "name-resolved" is the whole claim. The callee
    # extraction ABOVE reads `func.attr` / `func.id`; the walk below collects
    # mentions instead, so a reach that takes a
    # leg-reaching function BY REFERENCE is not an edge in this graph and the
    # closure cannot traverse it. The forms are not a list to keep current —
    # `_safe(self._review_pool, …)`, a `key=`/callback, `functools.partial`,
    # `getattr(self, <name>)`, an alias, a container, a bare `return` — the
    # rule is that the function is MENTIONED without being called — anywhere in
    # a function BODY, and not shadowed by a function-scope binding.
    # ⚠️ OUTSIDE THE WALK, and named rather than implied: a nested `def`'s
    # decorators and parameter DEFAULTS (evaluated when the outer method runs —
    # `def inner(cb=self._review_pool)` IS a real reach), and a comprehension's
    # own binding targets. Those are not reported, so the census doc says so too;
    # the class-definition-time argument that would justify excluding a METHOD
    # signature's default does not apply to a nested def, and the previous
    # comment wrongly used it for both. (A mention INSIDE a comprehension body
    # IS walked and IS reported — EXCEPT a `Load` that reuses THAT comprehension's
    # own target name: the target binds in the comprehension's own scope, so such
    # a `Load` is the comprehension's binding, not the enclosing method's
    # (#11389 review, P3).) Also NOT
    # modelled in `shadowed`: `*args`/`**kwargs` names, lambda parameters, `match`
    # patterns and `global`/`nonlocal` — those produce a spurious entry rather
    # than a missed one, and are owned by `#11397`. Under-
    # reporting is the dangerous direction for a surface an owner approves, so
    # the shape is REPORTED through the same `UNMEASURED` channel the handler
    # scan uses for its own bound (see `by_reference` below) instead of being
    # dropped. It is deliberately not GUESSED into the entrant set: bare
    # attribute-name matching would also over-attribute a foreign receiver
    # (`self.conn.helper(1)`), and a guess dressed as a measurement is the class
    # this instrument exists to refuse.
    changed = True
    while changed:
        changed = False
        for name, calls in callees.items():
            reach = set(direct.get(name, set()))
            for callee in calls:
                reach |= direct.get(callee, set())
            if not reach <= direct.get(name, set()):
                merged = direct.get(name, set()) | reach
                if merged != direct.get(name):
                    direct[name] = merged
                    changed = True

    # ⛔ The declared bound, MADE OBSERVABLE (#11337). A method that hands a
    # leg-reaching function to another call instead of calling it cannot be
    # resolved by a `Call.func` scan — so if that shape is present the caller
    # list is SHORT, and the only honest report is to say so. Emitted through
    # the `UNMEASURED` channel the caller already prints, so it rides beside the
    # tools rather than in a docstring nobody reads at report time.
    # ⛔ MEASURED at this head, against the SOURCE: `tortoise/sdk.py` holds
    # exactly ONE by-reference shape — `volunteer_context`'s
    # `_search_fn=self.tortoise_fts_query` — so this does NOT stay empty and a
    # clean run DOES publish a declared-bound line. Grepping for `_safe(`
    # answers a SPELLING, not the shape; that inference is what made an earlier
    # version of this comment certify the opposite of the truth, and the
    # cheapest repair for a reader who trusted it would be to delete the
    # declaration as a false positive. A previous cycle read five entries off this
    # scan's output and published them as "LIVE" — they were all the
    # resolved-call false positive above, which is why the negative pin exists
    # beside the positive one: the instrument's own output is not evidence about
    # itself.
    # ⛔ EVERY NODE, not a set of positions (#11337 review, P3). Naming the
    # shapes that carried the reach — a call argument, a `key=`/callback, a
    # `functools.partial`, a `getattr`, a plain `ast.Assign` — pinned only the
    # spellings the fixture happened to write, while `f: T = self._pool`, a
    # walrus, a container (`[self._pool]`), an `if`-expression and a bare
    # `return self._pool` were all still dropped with no entry. A mention of an
    # entrant by name IS the fact this channel reports, whatever syntax carries
    # it, so the rule is the mention — which is also the only rule that cannot
    # go stale when the SDK's shape changes.
    # ⛔ NOT SKIPPING ENTRANTS (#11337 review, P2). This used to skip any
    # function already in `direct`, on the reasoning that it is named anyway.
    # It is — but its ENTITY-TYPE SET is not: `direct[name]` is what
    # `vector_leg_surface` publishes as `entity_types`, so a method that calls
    # one leg-reaching helper and PASSES another lost the second helper's types
    # with nothing saying so. The list being complete is not the claim; the set
    # is. Only the leg itself is skipped (it is not its own caller).
    #
    # ⛔ AND A RESOLVED CALL IS NOT A BY-REFERENCE REACH (#11337 review, P0).
    # `_reachable_by_name` matches a `self.<entrant>` Attribute, and a plain
    # call's own `func` IS such an attribute — so `self._review_pool(scope)`,
    # which the closure above has already traversed, was being declared as an
    # unresolved reference. Measured: every one of the five entries this scan
    # produced on the real `tortoise/sdk.py` was that false positive, and the
    # published caveat said the caller table might be short when it was not —
    # the cry-wolf failure that makes a real declaration ignorable, produced by
    # the very channel meant to prevent it. The `func` of a `Call` is therefore
    # excluded: the closure owns it.
    #: ⛔ THE LEG IS IN THE MENTION SET (#11389 review, P1). `direct` excludes it
    #: (it is not its own caller), but a mention OF the leg is the by-reference
    #: case in its purest form — `_search_fn=self.tortoise_fts_query` in
    #: `volunteer_context` passes the leg itself into the volunteer pipeline, which
    #: calls it with `entity_type="point"`. Without the leg in this set that reach
    #: was neither traversed (not a `Call.func`) NOR declared, and three artifacts
    #: asserted no such shape existed. A name-resolved CALL of the leg is still
    #: excluded, by `call_funcs` above — that is the closure's.
    mentionable: dict[str, set[str]] = {**direct, VECTOR_LEG_SDK_METHOD: set()}
    by_reference: dict[str, set[str]] = {}
    for fn in functions:
        if fn is leg:
            continue
        # ⛔ ONLY A NAME-RESOLVED CALL'S `func` IS THE CLOSURE'S (#11389 review,
        # P2). Excluding every `Call.func` also excluded `getattr(self, n)(…)`,
        # whose func is itself a `Call`: the closure cannot resolve that call,
        # so the reach was neither traversed NOR declared — the silent-short-list
        # class in the dangerous direction, hidden by the guard against false
        # alarms. Only `Attribute`/`Name` funcs (the forms `calls` extracts) are
        # excluded; anything else stays visible to the mention walk.
        call_funcs = {id(c.func) for c in ast.walk(fn)
                      if isinstance(c, ast.Call)
                      and isinstance(c.func, (ast.Attribute, ast.Name))}
        # ⛔ SCOPED TO THE BODY WALK BELOW, not `ast.walk(fn)` (#11337 review,
        # P2). The whole-subtree walk collected a NESTED def's locals, so a
        # nested `_review_pool = 5` suppressed a genuine mention in the
        # ENCLOSING body — the silent-short-list direction this channel exists
        # to prevent, produced by the guard against false alarms.
        # ⚠️ NOT the same rule as the mention walk, and the difference is
        # deliberate (#11389 review, P3): this walk skips `ClassDef` — a class
        # namespace is not FUNCTION scope, so a class-body binding must not
        # suppress an enclosing mention — while the mention walk descends into a
        # class body, because a mention there IS a real reach. The two disagree
        # about `class` on purpose; the residual that follows (a class-body
        # binding reusing an entrant's name is an OVER-report) is on `#11397`.
        shadowed: set[str] = set()
        for sub in fn.args.args + fn.args.kwonlyargs + fn.args.posonlyargs:
            shadowed.add(sub.arg)
        for sub in list(fn.body):
            stack_s = [sub]
            while stack_s:
                n = stack_s.pop()
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef,
                                  ast.ClassDef)):
                    continue
                # ⛔ NOT a comprehension target: `[x for _pool in …]` binds in the
                # comprehension's OWN scope, so treating it as a function local
                # suppresses a genuine mention elsewhere in the body — the
                # under-report direction this guard exists to narrow, not create
                # (#11389 review). Comprehension scopes are skipped entirely.
                if isinstance(n, _COMPREHENSION_NODES):
                    continue
                if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
                    shadowed.add(n.id)
                # ⛔ These two bind a name on the NODE, not as `Name(Store)`
                # (#11389 review, P3): `except X as _pool` keeps the name in
                # `ExceptHandler.name`, and `import _pool` in the alias. A local
                # that reuses an entrant's name is not a reach either way.
                elif isinstance(n, ast.ExceptHandler) and n.name:
                    shadowed.add(n.name)
                elif isinstance(n, (ast.Import, ast.ImportFrom)):
                    for alias in n.names:
                        bound = alias.asname or alias.name.split(".")[0]
                        shadowed.add(bound)
                stack_s.extend(ast.iter_child_nodes(n))
        # ⛔ A COMPREHENSION'S OWN BINDINGS SHADOW WITHIN IT (#11389 review,
        # P2). `shadowed` above skips comprehension subtrees ON PURPOSE — a
        # comprehension target binds in its own scope, so it must not suppress a
        # mention elsewhere in the enclosing body. But this walk DESCENDS into
        # the comprehension, where that target is exactly the binding a `Load`
        # refers to; without the scope, `[_review_pool for _review_pool in
        # rows]` and `[r for _review_pool in rows if _review_pool]` each read as
        # a mention of the enclosing method's name.
        stack: list[tuple[ast.AST, frozenset[str]]] = [
            (n, frozenset()) for n in fn.body]
        while stack:
            node, comp_bound = stack.pop()
            # ⛔ A nested `class` BODY IS WALKED (#11389 review, P3): it executes
            # when the enclosing method does, and `self` inside it resolves to that
            # method's — so `class K: cb = _safe(self._review_pool)` is a real
            # by-reference reach. Only nested FUNCTIONS are their own scope here.
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if id(node) not in call_funcs:
                reached = _reachable_by_name(node, mentionable,
                                             shadowed | comp_bound)
                if reached:
                    by_reference.setdefault(fn.name, set()).add(reached)
            if isinstance(node, _COMPREHENSION_NODES):
                # ⛔ The OUTERMOST `for`'s ITERABLE is evaluated in the
                # ENCLOSING scope, not the comprehension's (#11389 review, P3):
                # `[x for _pool in _pool]` iterates the METHOD's `_pool`, so a
                # mention there is a real reach and suppressing it was an
                # under-report. Only the later `for` clauses and the element
                # live inside the comprehension's own scope.
                here = comp_bound | _comprehension_targets(node)
                for f in ("elt", "key", "value"):
                    child = getattr(node, f, None)
                    if child is not None:
                        stack.append((child, here))
                for i, gen in enumerate(getattr(node, "generators", ())):
                    stack.append((gen.iter, comp_bound if i == 0 else here))
                    stack.append((gen.target, here))
                    for cond in getattr(gen, "ifs", ()):
                        stack.append((cond, here))
                continue
            for child in ast.iter_child_nodes(node):
                stack.append((child, comp_bound))
    declaration = None
    if by_reference:
        # ⛔ Every TARGET, not just the first: a method can take two different
        # leg-reaching helpers by reference, and a per-method single string
        # silently dropped the second (#11337 review, P2).
        named = ", ".join(f"{fn} ({', '.join(sorted(targets))})"
                          for fn, targets in sorted(by_reference.items()))
        # ⛔ `declared bound:` is a MACHINE-READABLE marker, not decoration: the
        # published entry carries the same dict shape and `UNMEASURED(` prefix
        # as a scan failure, so without it a consumer cannot tell "the scan ran
        # and has a known edge" from "the scan could not run" (#11337 review).
        # The prose is count-neutral so one entry does not read ungrammatically.
        declaration = (
            f"UNMEASURED(declared bound: {named} — a leg-reaching function is "
            "taken by reference here, which this scan follows as a name-resolved "
            "call only; the caller list and its entity-type set may be short by "
            "that reach)")
    return {name: direct[name] for name in sorted(direct)}, None, declaration


def _handlers_forwarding_entity_type() -> tuple[set[str], str | None]:
    """MCP handlers that pass `entity_type` into `tortoise_fts_query`.

    Both call shapes are accepted: `graph.tortoise_fts_query(…, entity_type=…)`
    (the attribute IS the callee) and `_safe(graph.tortoise_fts_query, …,
    entity_type=…)` (the attribute is the first ARGUMENT — the shape every
    `_safe`-wrapped handler in this repo uses). A scan that only matched the
    first would miss `tortoise_search`, the tool the claim is primarily about.

    ⚠️ **DECLARED BOUND: this names handlers that call it DIRECTLY.** A handler
    that delegates to a helper which forwards the argument is NOT named — the
    HELPER's name is, and since it is not a registry tool it lands in the
    `UNMEASURED(handler not in TOOL_REGISTRY …)` entry beside the real tools
    rather than vanishing. `**{…}` keyword unpacking is likewise unmatched. At
    this head neither shape exists — the two module-level handlers in
    `mcp_server.py` that forward `entity_type` are the whole population of THIS
    SCAN, and a new one is REPORTED, not silently dropped, because the unmatched
    name lands in that `UNMEASURED` entry. Stated rather than claimed complete:
    this scan's promise is over what a walk of the tree can see, and it is one of
    THREE sources — a tool whose SDK method reaches the leg for a FIXED
    `entity_type` is named by :func:`_sdk_methods_on_vector_leg`, because it
    appears in no handler that forwards the argument (review round 7, P1).

    Returns `(names, error)`; `error` is the reason when the scan could not be
    taken, so the caller reports it instead of reading an empty set as "no tool
    is affected".
    """
    path = REPO_ROOT / "tortoise" / "mcp_server.py"
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except Exception as e:
        return set(), f"UNMEASURED(handler scan failed: {type(e).__name__}: {e})"

    names: set[str] = set()

    def _is_target(node: ast.AST) -> bool:
        if isinstance(node, ast.Attribute):
            return node.attr == VECTOR_LEG_SDK_METHOD
        return isinstance(node, ast.Name) and node.id == VECTOR_LEG_SDK_METHOD

    def _calls_with_entity_type(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
        # Only THIS function's own body — a nested `def` is its own handler and
        # attributing its call to the outer scope would name the wrong tool.
        # A nested CLASS body is this handler's body (same doctrine as `_scan`).
        stack: list[ast.AST] = list(fn.body)
        while stack:
            node = stack.pop()
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if isinstance(node, ast.Call):
                passes_entity_type = any(k.arg == "entity_type"
                                         for k in node.keywords)
                targets = _is_target(node.func) or any(
                    _is_target(a) for a in node.args)
                if passes_entity_type and targets:
                    return True
            stack.extend(ast.iter_child_nodes(node))
        return False

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and _calls_with_entity_type(node):
            names.add(node.name)
    return names, None


def vector_census(graph, graph_name: str, at: str | None = None,
                  graph_names: list[str] | None = None,
                  uri_graph_name: str | None = None) -> dict:
    """`stored` / `indexed` / `unindexed` + bytes, per label, on a NAMED graph.

    Reads the engine's own catalogue (`CALL db.indexes()`); stores nothing.
    Every field is either measured or the string ``UNMEASURED(<why>)`` — never
    `0`, because "no rows" and "could not read" are different facts and this
    issue exists because a figure was quoted without either.
    """
    at = at or datetime.now(UTC).isoformat(timespec="seconds")
    # ⛔ A catalogue read on a graph NAME that does not exist MATERIALISES an
    # empty graph server-side (measured by this file's own verification:
    # `GRAPH.LIST` gained `tortoise_test_matrix` after `--census --graph
    # tortoise_test_matrix`). That is a write, in a tool whose whole doctrine is
    # "stores nothing" — and it reads as a measured zero for a graph that is not
    # there, the exact conflation this instrument exists to prevent. So the NAME
    # is proved to exist BEFORE any query is issued.
    # ⛔ The handle is selected by `graph_name` (the name this row will carry),
    # never by the URI path alone: measuring one graph and labelling another is
    # how a census reads as measured without being reproducible (this tool's own
    # review, P1 — it printed `Point stored=0` for a graph holding 50 vectors).
    # `uri_graph_name` is recorded so a reader can see both.
    if graph_names is None:
        return {"graph": graph_name, "at": at, "labels": {},
                "catalogue": "UNMEASURED(graph existence not provable — pass "
                             "graph_names; a catalogue read on a missing name "
                             "CREATES the graph)",
                "command": CENSUS_COMMAND}
    if graph_name not in graph_names:
        return {"graph": graph_name, "at": at, "labels": {},
                "catalogue": f"UNMEASURED(graph {graph_name!r} does not exist on "
                             f"this engine: {sorted(graph_names)})",
                "command": CENSUS_COMMAND}
    try:
        rows = _read(graph, "CALL db.indexes()").result_set
    except Exception as e:
        return {"graph": graph_name, "at": at, "labels": {},
                "catalogue": f"UNMEASURED({type(e).__name__}: {e})",
                "command": CENSUS_COMMAND}

    # ⛔ `{indexed} | {served}` is NOT the population: a label that stores
    # vectors, has no index AND no query path is the worst class this census is
    # for, and drawing the population from the two things it compares omits it
    # by construction (this tool's own review, P1 — a `:Chunk` with 2 stored
    # embeddings never appeared). The graph's own labels are the population.
    indexed: dict[str, list[dict]] = {}
    #: label → {KIND: [fields]} — EVERY index kind the catalogue reports for the
    #: label, not only VECTOR. `#11088` §3.1 asks whether a label carries a
    #: FULLTEXT index (it decides whether a lexically-matching query survives a
    #: vector removal), and the census said "a run settles it" while parsing
    #: VECTOR rows only — so the deciding datum was not in the answer (review
    #: round 7, P2). Recorded from the same rows, at no extra query cost.
    index_kinds: dict[str, dict[str, list[str]]] = {}
    #: (label-or-None, reason) — matched by LABEL, so a short row (`['Point']`)
    #: is still attached to its label instead of being lost in a repr.
    unreadable: list[tuple[str | None, str]] = []
    for row in rows or []:
        try:
            label, types = row[0], row[2]
        except (IndexError, TypeError):
            row_label = row[0] if isinstance(row, (list, tuple)) and row else None
            unreadable.append((row_label, f"short row {repr(row)[:60]}"))
            continue
        if not isinstance(types, dict):
            unreadable.append((label, f"types cell is {type(types).__name__}"))
            continue
        for fld, kinds in types.items():
            if isinstance(kinds, (list, tuple, set)):
                for kind in sorted({str(k).upper() for k in kinds}):
                    index_kinds.setdefault(label, {}).setdefault(
                        kind, []).append(fld)
            if isinstance(kinds, (list, tuple, set)) and any(
                str(k).upper() == "VECTOR" for k in kinds
            ):
                opts = row[3] if len(row) > 3 and isinstance(row[3], dict) else {}
                dim = None
                try:
                    dim = (opts.get(fld) or {}).get("dimension")
                except AttributeError:
                    dim = None
                stats = row[8] if len(row) > 8 and isinstance(row[8], dict) else {}
                # ⛔ These counters describe the row's FULLTEXT bookkeeping
                # (measured: docTableSize / docTrieSize / invertedSize / …),
                # NOT the vector index — a live HNSW holding 76,800 B of
                # payload reported 5,150 B, all of it fulltext. Reported under
                # their real name so the figure cannot be read as a vector size.
                engine_bytes = {k: v for k, v in stats.items()
                                if isinstance(v, int) and k.endswith("Size")} or None
                # A LIST per label: a label with vector indexes on two fields
                # must not have one silently overwrite the other.
                indexed.setdefault(label, []).append({
                    "field": fld,
                    "dimension": dim if dim is not None else "UNMEASURED(no dimension in options)",
                    "engine_catalogue_counters_FULLTEXT_NOT_VECTOR": engine_bytes
                    if engine_bytes is not None
                    else "UNMEASURED(no size counters in the catalogue row)",
                })

    try:
        graph_labels = [r[0] for r in (_read(graph, "CALL db.labels()").result_set or [])]
        population_source = "CALL db.labels()"
    except Exception as e:
        # ⛔ NOT a narrowed fallback to `indexed | served`: that is the very
        # population bug this census exists to remove, and it silently drops
        # the stored-no-index-no-query-path class (review round 3, P2). A
        # population that could not be read is a measurement that did not
        # happen.
        return {"graph": graph_name, "at": at, "labels": {},
                "catalogue": f"UNMEASURED(population read failed: "
                             f"{type(e).__name__}: {e})",
                "population": "UNMEASURED(CALL db.labels() failed)",
                "command": CENSUS_COMMAND, "uri_path_graph": uri_graph_name,
                "unreadable_catalogue_rows": unreadable}

    labels: dict[str, dict] = {}
    for label in sorted(set(graph_labels) | set(indexed) | served_labels()):
        try:
            res = _read(
                graph,
                f"MATCH (n:{_cypher_label(label)}) "
                f"WHERE n.embedding IS NOT NULL RETURN count(n)",
            ).result_set
            stored = int(res[0][0])
        except Exception as e:
            stored = f"UNMEASURED({type(e).__name__}: {e})"
        # ⛔ A row we could not read and could not ATTRIBUTE (no label cell at
        # all — `[]`, or a label cell that is not the label string, e.g. `None`)
        # is not "this label's row" and it is not NO label's row: it is EVERY
        # label's. Keying it on `None` and matching `lab == label` dropped it
        # for every label — `label` is always a str (the population is
        # `graph_labels | indexed | served_labels()`), so the filter was empty
        # everywhere and BOTH columns took the MEASURED branch: a label printed
        # `indexed=False, unindexed=stored` ("this label has no index, and all N
        # of its vectors are unindexed") while the catalogue that would have
        # said otherwise was a row this code could not read. That is the exact
        # inversion of the contract stated three lines below and in the
        # function docstring — `stored`/`indexed` are "either measured or
        # `UNMEASURED(<why>)` — never `0`, because 'no rows' and 'could not
        # read' are different facts" (round-23 review, P3, measured with
        # `catalogue=[[]]`). An unattributable row therefore holds EVERY label's
        # two columns at `UNMEASURED`, the same safe direction the attributable
        # case already takes.
        unreadable_here: list[str] = []
        for lab, why in unreadable:
            if lab is None:
                # The reason alone. Prefixing it with `None: ` would read as a
                # label literally named `None` — the printer already formats
                # this tuple the same way (`lab is None` → `why`).
                unreadable_here.append(why)
            elif lab == label:
                unreadable_here.append(f"{lab}: {why}")
        found = indexed.get(label) or []
        # ⛔ An index on the LABEL is not an index on the COLUMN counted here.
        # `field` was already read and used to be ignored: a `(Source:vec)`
        # index made a `Source.embedding` vector report `indexed=True`
        # (this tool's own review, P1 — measured on a live engine).
        covering = [ix for ix in found if ix["field"] == "embedding"]
        idx = covering[0] if covering else (found[0] if found else None)
        dim = idx["dimension"] if covering and isinstance(idx["dimension"], int) else None
        # The stored WIDTH is probed once, before either column is written:
        # `unindexed` must not claim "covered" when the index and the stored
        # vectors disagree on dimension (the `#4280` class), nor when either
        # number is unreadable (review round 7, P2), and the payload is derived
        # from the same number.
        width = None
        width_reason: str | None = None
        width_samples = 0
        if isinstance(stored, int) and stored > 0:
            try:
                # `size(n.embedding)` is REJECTED on a vecf32 column, so the
                # vector is fetched and measured here (measured: every payload
                # read UNMEASURED before this).
                #
                # ⛔ MORE THAN ONE ROW, because one row's width is not the
                # label's: a graph that went through an `EMBEDDING_DIM`
                # rotation holds vectors of two widths, and `stored x width`
                # then reports one row's size multiplied across every row —
                # wrong in an unbounded direction, in the figure #11088 exists
                # to produce, with nothing marking it as estimated (review
                # round 7, P2; measured 5x1024 + 1x384 → 24,576 B reported for
                # a true 22,016 B). ⚠️ A covering index does NOT make the
                # disagreement impossible — measured: an index of dimension
                # 384 over a label holding one 384-wide and five 1024-wide
                # rows publishes `stored x dimension` on a 384-majority
                # sample and refuses (`stored widths disagree`) on a mixed
                # one, so a disagreement is BOTH arms' hazard. What the
                # covering arm adds is a cross-check, not an invariant: it
                # publishes `stored x dimension` only where the PROBED width
                # agrees with the index dimension (`probed width W agrees`)
                # and refuses otherwise. The census never establishes that an
                # indexed label holds a single width, so that invariant is
                # not assumed here — census doc §2 states the same.
                probe = _read(
                    graph,
                    f"MATCH (n:{_cypher_label(label)}) "
                    f"WHERE n.embedding IS NOT NULL RETURN n.embedding "
                    f"LIMIT {WIDTH_PROBE_SAMPLES}",
                ).result_set
                seen = [len(r[0]) for r in (probe or [])
                        if r and r[0] is not None]
                widths = set(seen)
                width_samples = len(seen)
                if len(widths) == 1:
                    width = widths.pop()
                elif len(widths) > 1:
                    width_reason = ("stored widths disagree ("
                                    + ", ".join(str(w) for w in sorted(widths))
                                    + "), so stored x width is not a figure")
            except Exception:
                width = None
        if isinstance(stored, str):
            # ⛔ THE SAME MASKING, ONE BRANCH UP (#11389 review, P2). The
            # two-cause rule below was wired only to the `dim`/`width` pair, so
            # a row whose stored-count read FAILED **and** whose covering index
            # `dimension` was unreadable still reported the count ALONE, with
            # `indexed: true` beside it — the identical round trip this change
            # exists to remove: repair the count, re-run, be refused again on
            # the dimension, never told both causes existed. Measured with a
            # fake that raises on `count(n)`.
            # ⚠️ The WIDTH is deliberately NOT named here, unlike the
            # dim/width arm: a width probe can only measure rows, and the count
            # is exactly the fact that could not be read, so "no rows, nothing
            # to probe" and "the probe failed" are indistinguishable and naming
            # it would be a guess. The `dimension` is a CATALOGUE cell and
            # independent of the count, so it is a cause we can name.
            # The single-cause text is byte-identical to before.
            unindexed = (
                "UNMEASURED(stored count unreadable and the index dimension "
                "unreadable)" if covering and dim is None
                else "UNMEASURED(stored count unreadable)")
        elif covering and width is not None and dim is not None and width != dim:
            unindexed = f"UNMEASURED(index dim {dim} != stored width {width})"
        elif covering and stored != 0 and (width is None or dim is None):
            # ⛔ A covering index is not a PROVED coverage. `unindexed = 0`
            # claims every stored vector is inside the index, and that claim
            # needs BOTH numbers: the index's dimension and the stored width.
            # This branch is the `#4280` disagreement one step weaker — the
            # inputs are not known, so they cannot be compared — and it used to
            # fall through to `0`, printing a measured-looking figure beside a
            # `payload` column that said `UNMEASURED` (review round 7, P2).
            # The dimension is REQUIRED in a vector index's OPTIONS
            # (docs.falkordb.com › Indexing › Vector Index), so an absent one is
            # an unreadable catalogue cell, not a label with no dimension;
            # `width` is None when the vecf32 probe failed or returned nothing.
            # ⛔ NAME BOTH CAUSES WHEN THERE ARE TWO (#11337). This used to
            # compute the cause from every unmeasured input and then REASSIGN
            # it to the width cause alone, so a row whose index `dimension` was
            # unreadable AND whose sampled widths disagreed reported only the
            # width: a reader repairs the width probe, re-runs, is refused again
            # with different text, and is never told the second cause exists.
            # The single-cause texts are unchanged.
            causes: list[str] = []
            if dim is None:
                causes.append("the index dimension unreadable")
            if width is None:
                causes.append(f"the stored width ({width_reason})"
                              if width_reason else "the stored width unreadable")
            unindexed = ("UNMEASURED(coverage not provable: "
                         + " and ".join(causes) + ")")
        elif covering:
            unindexed = 0
        else:
            unindexed = stored
        if isinstance(stored, int) and stored == 0:
            payload = {"derived_payload_B": 0, "how": "no stored vectors on this label"}
        elif unreadable_here:
            # ⛔ The catalogue row could not be read, so COVERAGE is unknown —
            # and `not covering` cannot tell "no index" from "an index we could
            # not see" (`covering` is empty for both). `how` may not say either
            # way, and the byte figure refuses for the same reason `indexed`
            # and `unindexed` already do (review round 1, P2: the row said
            # `indexed: UNMEASURED(...)` and, one field over, "NO covering
            # index" — a coverage fact it had not measured).
            # ⛔ BUT A FAILED CATALOGUE READ DOES NOT CANCEL WHAT THE WIDTH
            # PROBE MEASURED (#11389 review, P2). The probe is an independent
            # read and its disagreement is a MEASURED fact; discarding it made
            # a reader repair the catalogue, re-run, and be refused again on
            # the width — the same round trip, in the one arm the previous
            # enumeration did not count. Measured before the fix: `disagree`
            # appeared nowhere in the row while the probe had returned both
            # widths. The catalogue text is otherwise unchanged.
            payload = {"derived_payload_B": (
                           "UNMEASURED(the catalogue row was unreadable, so "
                           "coverage is not provable"
                           + (f"; {width_reason}" if width_reason else "")
                           + ")"),
                       "how": (f"catalogue row unreadable: {unreadable_here[0]}"
                               + ("; the width probe did run" if width_reason
                                  else ""))}
        elif width is None:
            # ⛔ The refusal, BEFORE the index is consulted: without a measured
            # width there is no byte figure, indexed or not — and when the probe
            # saw more than one width, the reason is that, not "unprobeable".
            # ⛔ NAME BOTH CAUSES HERE TOO (#11389 review, P2). The two-cause
            # rule reached `unindexed` but stopped one branch short of the BYTE
            # FIGURE: with a covering index whose `dimension` is unreadable over
            # a mixed-width label, `unindexed` named both causes while
            # `derived_payload_B` named the width alone — so a reader repairs
            # the width, re-runs, and is refused again on the dimension. Same
            # round trip, one field over. Measured before the fix.
            # The single-cause texts are byte-identical: when `dim` is readable
            # — or the label has no covering index, where an index `dimension`
            # is not a cause at all — the old string is unchanged.
            # ⛔ AND `how` NAMES IT TOO (#11389 review, P2). `how` is the
            # REMEDY channel in this function (the dim-only arm says "needs the
            # index dimension to cross-check the measured width"), so a `how`
            # that names only the width sends the reader to re-probe something
            # that will not lift the refusal. The refusal and the remedy must
            # name the same causes or the field is worse than absent.
            payload_causes: list[str] = []
            dim_cause = "the index dimension unreadable"
            if covering and dim is None:
                payload_causes.append(dim_cause)
            payload_causes.append(width_reason or "stored width not probeable")
            how = ("a single width cannot stand for this label's stored vectors"
                   if width_reason else "needs a width probe")
            if covering and dim is None:
                how += f", and {dim_cause}"
            payload = {"derived_payload_B": "UNMEASURED("
                                            + " and ".join(payload_causes)
                                            + ")",
                       "how": how}
        elif not covering:
            # ⛔ #11088: THE UNINDEXED LABELS ARE THE POPULATION THIS CENSUS IS
            # FOR, and they had no byte figure at all. The payload used to be
            # derived from the covering index's `dimension`, so every label the
            # issue exists to quantify reported
            # `UNMEASURED(no VECTOR index on embedding — nothing to derive
            # from)` — while the stored WIDTH had already been probed one branch
            # above (the `#4280` cross-check needs it). The number was in hand
            # and not printed, which is exactly the `stored`-only / missing-figure
            # class this instrument exists to refuse. The width is the
            # measurement; with no index there is nothing to cross-check it
            # against, and `how` says so rather than implying it was verified.
            #
            # ⛔ The predicate is `not covering`, NOT `dim is None`: a label can
            # carry a VECTOR index on `embedding` whose `dimension` cell is
            # unreadable, and keying on `dim` put "NO covering index" in `how`
            # beside `indexed=True` and a populated `vector_indexes_on_label` —
            # the row contradicting its own field (review, P2).
            # ⛔ The SAMPLE SIZE belongs in `how`: a probe of N rows cannot
            # prove every row agrees, so a narrower minority width outside the
            # sample is not seen. Stating the sample is the difference between
            # a bounded observation and an implied exhaustive check (review
            # round 9, P2 — the figure stays, the overclaim goes).
            payload = {"derived_payload_B": stored * width * 4,
                       "how": f"stored {stored} x probed width {width} dims x 4 B "
                              f"(vecf32); NO covering index, so the width comes "
                              f"from {width_samples} sampled row(s) "
                              "chosen by the engine (the probe carries no "
                              "ORDER BY), and is not cross-checked — a narrower "
                              "minority width outside that sample would not be "
                              "seen"}
        elif dim is None:
            # A covering index EXISTS and its `dimension` is unreadable. The
            # stored width is still measured, but coverage is not provable —
            # the same posture `unindexed` takes for this row — so the byte
            # figure refuses too rather than reading as verified.
            payload = {"derived_payload_B": "UNMEASURED(a covering index exists "
                                            "but its dimension is unreadable)",
                       "how": "needs the index dimension to cross-check the "
                              "measured width"}
        elif width != dim:
            payload = {"derived_payload_B": "UNMEASURED(stored width "
                                            f"{width} != index dimension {dim})",
                       "how": "the index and the stored vectors disagree (#4280 class)"}
        else:
            payload = {"derived_payload_B": stored * dim * 4,
                       "how": f"stored {stored} x {dim} dims x 4 B (vecf32), "
                              f"probed width {width} agrees"}
        # ── #11088: WHICH entity_type values stop answering ──────────────
        # `unindexed` above is a COUNT. The surface question is a SET: removing
        # the unindexed remainder does not change "a query", it changes the
        # query for every `entity_type` the product translates onto this label.
        # ⛔ What changes is SEMANTIC RECALL, not retrieval: with the vectors
        # gone the dense leg has nothing to answer
        # (`CALL db.idx.vector.queryNodes` on an unindexed label raises today,
        # so `run_vector_query` degrades to a brute-force scan), but
        # `classify_query` turns the FTS leg on for every text query, and
        # `_FULLTEXT_INDEX_LABEL_FIELDS` declares a fulltext index on all five
        # served labels — so a lexically-matching query keeps answering, and
        # only a query that matched ONLY by meaning goes to zero rows. Measured
        # for `:Object`; §3.1 of `docs/epics/2026-11-04-5090-vector-layer/`
        # `S3-VECTOR-CENSUS.md` also records where `STORAGE-ARCHITECTURE.md`
        # §12.1b's 2026-09-24 inventory DISAGREES (`:Source` FULLTEXT `—`, a
        # reading taken before that DDL), so the answer for `document`/`source`
        # is a census on the target graph and not an assumption. (Review round
        # 1: this comment said "returns zero rows", which the measurement
        # refuted.) A label with no `entity_type`
        # at all is the worst class: stored, unindexed AND unqueryable — so it
        # is reported as affected-for-nobody, not omitted.
        ets = affected_entity_types(label)
        # ⛔ Derived from the FINAL cell values, not from the locals above: an
        # unreadable catalogue row leaves `covering` empty, so the `else:
        # unindexed = stored` branch fabricates a full "unindexed" coverage —
        # and computing `changes` from it published `True`, plus a
        # `surface_consequence` entry, for a label whose own `unindexed` cell
        # read `UNMEASURED` three lines away (review round 1, P1).
        if unreadable_here:
            changes = ("UNMEASURED(the catalogue row was unreadable, so whether "
                       "any stored vector is unindexed is not provable)")
        elif isinstance(unindexed, int) and isinstance(stored, int):
            changes = bool(unindexed > 0 and ets)
        else:
            # Neither the count nor the coverage is measured, so neither is
            # the consequence: `False` here would be a claim built on a hole.
            changes = "UNMEASURED(removal consequence needs a measured count)"
        labels[label] = {
            "stored": stored,
            # An unreadable catalogue row is NOT "this label has no index": it
            # is a row we could not read, and the column says so.
            "indexed": ("UNMEASURED(catalogue row unreadable: "
                        f"{unreadable_here[0]})" if unreadable_here
                        else bool(covering)),
            "unindexed": (
                f"UNMEASURED(catalogue row unreadable: {unreadable_here[0]}"
                # ⛔ …and the width the probe DID measure is not cancelled by
                # it (#11389 review, P2): `width_reason` is a measured fact
                # from an independent read. Same rule as the payload above.
                + (f"; {width_reason}" if width_reason else "")
                + ")" if unreadable_here else unindexed),
            "vector_indexes_on_label": found or "none",
            "index": idx or "none",
            # ⛔ EVERY index kind, not only VECTOR: §3.1's deciding datum for
            # `:Source`/`document` is whether a FULLTEXT index exists, and the
            # doc promised the run settles it (review round 7, P2).
            "indexes_on_label": (
                "UNMEASURED(catalogue row unreadable)" if unreadable_here
                else (index_kinds.get(label) or "none")),
            "payload": payload,
            "affected_entity_types": ets,
            "removal_changes_query_results": changes,
        }
    return {"graph": graph_name, "at": at, "labels": labels,
            "catalogue": "CALL db.indexes()", "population": population_source,
            "command": CENSUS_COMMAND,
            "uri_path_graph": uri_graph_name,
            "unreadable_catalogue_rows": unreadable,
            "surface_consequence": surface_consequence(labels)}


def surface_consequence(labels: dict) -> dict:
    """The MCP/SDK surface a removal would change, from the census rows.

    ⛔ This is REQUESTED, not performed (#11088 / `AGENTS.md`'s MCP-SDK surface
    rule). The dict therefore carries `requested_not_performed: True` as a
    FIELD rather than as a sentence in a document, so a consumer reading the
    machine form cannot mistake a census for an authorisation.

    Only labels whose removal would change a query are listed. A label that is
    stored, unindexed and served by NO `entity_type` is reported in its row
    (empty `affected_entity_types`) and is deliberately absent here: nothing
    queries it, so nothing changes.

    ⛔ A label whose consequence is `UNMEASURED(...)` is listed under
    `unmeasured_labels`, NEVER dropped. Dropping it would shrink
    `entity_types_whose_query_results_change` silently — the UNDER-reporting
    direction this module's own §3 calls the dangerous one, since a surface
    request whose consequence is understated is a request the owner can approve
    by mistake (review round 1, P1). It is reachable on a live engine: an
    unreadable catalogue row, and a per-label count that times out.
    """
    affected, unmeasured = [], []
    for label, row in sorted(labels.items()):
        changes = row.get("removal_changes_query_results")
        if changes is True:
            affected.append({
                "label": label,
                "entity_types": row["affected_entity_types"],
                "unindexed": row["unindexed"],
                "stored": row["stored"],
                "derived_payload_B": row["payload"]["derived_payload_B"],
            })
        elif changes is not False:
            # `True` / `False` are the only measured answers; anything else is a
            # hole, and a hole is carried, not discarded.
            unmeasured.append({
                "label": label,
                "entity_types": row["affected_entity_types"],
                "why": changes,
            })
    return {
        "requested_not_performed": True,
        "sdk_method": VECTOR_LEG_SDK_METHOD,
        "runner": VECTOR_LEG_RUNNER,
        "surface": _annotated_surface(affected, unmeasured),
        "entity_types_whose_query_results_change": sorted(
            {et for a in affected for et in a["entity_types"]}),
        "labels": affected,
        # ⛔ The under-reporting channel. `entity_types_whose_query_results_change`
        # is a MEASURED set; a reader must not take it as the whole population
        # while this is non-empty.
        "unmeasured_labels": unmeasured,
    }


def _annotated_surface(affected: list[dict], unmeasured: list[dict]) -> list[dict]:
    """`vector_leg_surface()` with each tool's ACTUAL consequence attached.

    ⛔ The entrant list and the affected list are different questions, and the
    approval request needs the second: `tortoise_issue_insight` and
    `tortoise_suggest_entry_points` reach the leg for `point` only, and `:Point`
    is the one INDEXED label — naming them as entrants without saying so would
    read as six affected tools. Each entry therefore carries
    `reaches_affected_labels`: the intersection of what it asks the leg for with
    the `entity_type` values whose results this census measured as changing, or
    `"any (the caller chooses)"` for a pass-through tool (review round 7, P1:
    the surface was under-reported; this keeps the over-report from replacing
    it).

    An UNMEASURED consequence makes the affected set a LOWER BOUND, so the field
    says that instead of understating a tool's reach: a partial set may not be
    used to clear a tool.
    """
    surface = vector_leg_surface()
    possible = {et for a in affected for et in a["entity_types"]}
    for entry in surface:
        if "tool" not in entry:
            continue
        asked = entry.get("entity_types")
        if unmeasured:
            entry["reaches_affected_labels"] = (
                "UNMEASURED(the affected set is partial — see unmeasured_labels)")
        elif not possible:
            # ⛔ NOTHING is removable on this graph, so NOTHING changes — and a
            # pass-through tool must not print "any (the caller chooses)" here,
            # which would name two tools as changing on a graph where the
            # affected set is empty (review round 9, P2: an over-report in the
            # approval artifact, contradicting this function's own promise).
            entry["reaches_affected_labels"] = []
        elif not isinstance(asked, list):
            entry["reaches_affected_labels"] = "any (the caller chooses)"
        elif any(str(t).startswith("UNKNOWN") for t in asked):
            # ⛔ An `UNKNOWN(...)` member is the reporting channel for a
            # non-literal `entity_type`; intersecting that STRING with the
            # affected types would yield `[]` and read as a measured "no
            # change" (review round 9, P2).
            entry["reaches_affected_labels"] = (
                "UNMEASURED(its SDK method's entity_type is not a literal)")
        else:
            entry["reaches_affected_labels"] = sorted(set(asked) & possible)
    return surface


def _non_vector_indexes_line(kinds: object) -> str:
    """The text-mode line for a label's indexes, or `""` when VECTOR is all it has.

    ⛔ Extracted so the FORMAT is testable: the first version built the string
    inline and put the closing paren on the whole join instead of on each
    entry, so a label carrying two non-VECTOR kinds printed
    `FULLTEXT(a, RANGE(b)` — a malformed line in the one output a reader quotes
    into an approval request, and invisible because the single-kind case (all
    the §4 transcript had) is correct (verification gate, P2).
    """
    if isinstance(kinds, str) and kinds.startswith("UNMEASURED"):
        return f"⛔ indexes: {kinds}"
    if not isinstance(kinds, dict):
        return ""
    extra = {kind: fields for kind, fields in kinds.items() if kind != "VECTOR"}
    if not extra:
        return ""
    return ("other indexes on this label: "
            + ", ".join(f"{kind}({','.join(fields)})"
                        for kind, fields in sorted(extra.items())))


def _connect(uri: str):
    """`(graph, names)` from a `docker://`/`bolt://` URI — never embedded.

    Embedded engines have no index API at all (`_record_vector_index_inventory`
    returns early on them), so a census there would measure `indexed: False`
    for every label and read as "nothing is indexed" — a false finding. This
    refuses instead. The graph NAMES come back with the handle because a
    catalogue read on a name that does not exist CREATES the graph.
    """
    from urllib.parse import urlparse

    u = urlparse(uri)
    if u.scheme in ("", "file") or not u.hostname:
        # Exit 2 = "the instrument could not measure" (the docstring's
        # contract). ⛔ A NON-INT SystemExit argument exits **1**, so the message
        # is printed and the code passed explicitly — passing the string made
        # the documented `2` a contract the code never implemented, and a
        # caller could not tell "could not measure" from "a row is NOT MET".
        print(
            "census: an embedded/absent engine has no index API — pass a "
            "docker:// or bolt:// URI (embedded would report indexed=False for "
            "every label, which is NOT the same fact)",
            file=sys.stderr,
        )
        raise SystemExit(2)
    from falkordb import FalkorDB

    from tortoise.projection import _bounded_server_socket_kwargs

    # #8118: the read bound comes from the product's ONE resolver, never from
    # this call site. ``falkordb.FalkorDB`` defaults ``socket_timeout=None``
    # and hands it to redis-py explicitly, so an unbounded client parks a
    # "read, change nothing" census in ``recv()`` forever against a stalled
    # engine (#2969's defrag loop, #2838's MERGE lock) — alive, 0% CPU, no
    # error, which is the one outcome this instrument cannot tolerate.
    db = FalkorDB(
        host=u.hostname,
        port=u.port or 6379,
        password=u.password or "",
        **_bounded_server_socket_kwargs(),
    )
    try:
        names = list(db.list_graphs() or [])
    except Exception:
        names = None
    return db, names


# ── The report ─────────────────────────────────────────────────────────────


def measure() -> dict:
    """Run the whole instrument. Clears the gate flags first — a lane's own env
    must not be able to make a gate read as enforced or as idle.

    ⛔ Both switches are set EXPLICITLY to their OFF value (`#10996`). Popping
    them cleared the ambient value but produced an "off" reading only while the
    flags were default-OFF; `TORTOISE_VALUE_GATE` now defaults ON (the S2.2
    mechanical half, `EXTRACTOR-V4-ARCHITECTURE.md` §S2.2), so an unset value
    would have made every row of this instrument read a DIFFERENT configuration
    than the one it declares.
    """
    # Snapshot and RESTORE: an import of a "read-only" instrument must not
    # leave the rest of the caller's process with both gates disabled.
    _flags = {v: os.environ.get(v) for v in ("TORTOISE_EMBED_GATE", "TORTOISE_VALUE_GATE")}
    for var in _flags:
        os.environ[var] = "0"
    try:
        rows = _measure_rows()
    finally:
        for var, value in _flags.items():
            if value is None:
                os.environ.pop(var, None)
            else:
                os.environ[var] = value
    return {
        "issue": 10465,
        "epic": 5090,
        "head": _head(),
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
        "rows": [asdict(r) for r in rows],
        "predicates": count_embed_predicates(),
        "seams": _write_seams_that_embed(),
    }


def _measure_rows() -> list[Row]:
    rows = [score_rule_bullet(), score_path_bullet(),
            score_fallback_bullet(), score_v1_bullet()]
    return rows


def _head() -> str:
    try:
        import subprocess

        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True,
            text=True, check=False,
        ).stdout.strip() or "UNMEASURED(git rev-parse returned nothing)"
    except Exception as e:
        return f"UNMEASURED({type(e).__name__}: {e})"


def _cell(value: str) -> str:
    """One GFM table cell: a literal `|` is written `\\|` (GFM §4.10, Ex. 200).

    The escaping belongs to the EMITTER, not to each note. Three of the four
    rows carry text this tool does not author — `shipped["files"]` config lines,
    and `V1_SURFACES` doc lines, one of which is a markdown document whose V1
    discussion lives in *table rows*. A per-note rule has to be remembered by
    every `notes` producer, so it is only ever as wide as the last person to
    remember it; escaping here covers a row added tomorrow.
    """
    return str(value).replace("|", "\\|")


def _table(rows: list[Row]) -> str:
    out = ["| # | bullet | verdict | evidence |", "|---|---|---|---|"]
    for r in rows:
        out.append(
            f"| {r.bullet} | {_cell(r.text)} | **{r.verdict}** | {_cell(r.observed)} |")
    # The command is NOT decoration: `#10465` scores a bullet WITH its command,
    # so a row printed without one would be unscored evidence in the one place
    # a reader actually looks.
    out.append("")
    for r in rows:
        out.append(f"- row {r.bullet} command: `{r.command}`")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true", help="machine-readable report")
    ap.add_argument("--census", action="store_true",
                    help="also take the per-label vector census")
    ap.add_argument("--uri", default=os.environ.get("TORTOISE_DB_URI", ""),
                    help="a docker:// or bolt:// URI (default $TORTOISE_DB_URI)")
    ap.add_argument("--graph", default=os.environ.get("TORTOISE_GRAPH_NAME", ""),
                    help="the graph NAME to census (default $TORTOISE_GRAPH_NAME)")
    args = ap.parse_args(argv)

    try:
        report = measure()
    except ImportError as e:
        # The other route to the documented `2`: a gate module the instrument
        # SCORES that will not import. Without this the traceback exits 1, which
        # is indistinguishable from a verdict failure.
        print(f"could not measure: {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    unmeasured = False
    if args.census:
        if not args.uri:
            report["census"] = {"error": "UNMEASURED(no --uri and no TORTOISE_DB_URI)"}
            unmeasured = True
        else:
            # ⛔ The graph NAME comes from the URI's PATH, parsed the way
            # `_connect` parses it — never from the last `/`-segment of the raw
            # URI. For `docker://:SECRET@host:6379` the path is EMPTY and that
            # segment is `:SECRET@host:6379`, i.e. the URI's USERINFO: the old
            # derivation carried the password into `report["census"]["graph"]`
            # (and, on the text path, into a line printed twice), reachable with
            # NO FLAG AT ALL because `--uri` defaults to `$TORTOISE_DB_URI`.
            # Same credential class as `tests/_embedded.py`'s warning and #3039
            # (review round 13, P2). A path-less URI names NO graph, so the
            # census cannot be taken — which is stated, never substituted with
            # the authority.
            from urllib.parse import urlsplit

            uri_path_name = urlsplit(args.uri).path.strip("/").rsplit("/", 1)[-1]
            graph_name = args.graph or uri_path_name
            if not graph_name:
                report["census"] = {
                    "error": "UNMEASURED(no graph name: the URI path names "
                             "none and --graph is absent — a URI's authority "
                             "is not a graph name)"}
                unmeasured = True
            else:
                db, names = _connect(args.uri)
                # ⛔ The handle is selected by the NAME the report will carry.
                # Using the URI path while LABELLING the row `--graph`
                # attributes one graph's numbers to another (this tool's own
                # review, P1: it printed `Point stored=0` for a graph holding
                # 50 vectors). `uri_path_name` is recorded so a reader can see
                # both — and `None`, not `''`, when the URI carries no path.
                graph = db.select_graph(graph_name)
                report["census"] = vector_census(
                    graph, graph_name, graph_names=names,
                    uri_graph_name=uri_path_name or None)
                census = report["census"]
                unmeasured = unmeasured or str(
                    census.get("catalogue", "")).startswith(
                        "UNMEASURED") or "error" in census

    if args.json:
        print(json.dumps(report, indent=2, default=str))
        return 2 if unmeasured else 0

    print(f"#10465 — #5090 DoD, measured at {report['head']} on {report['at']}\n")
    print(_table([Row(**{k: v for k, v in r.items()}) for r in report["rows"]]))
    p = report["predicates"]
    print(f"\n§12.2a predicates: {p['count']} (target {p['target']}) — "
          f"{p['verdict']}; owners={p['owners']}; shared identity="
          f"{p['rule_set_is_shared_identity']}")
    if p["unparseable"]:
        # ⛔ Printed BESIDE the count, so `<N> — MET` can never be read off a
        # line whose population had a hole in it (`UNDETERMINED` is above).
        print(f"  ⛔ unreadable modules (this count could not scan them): "
              f"{p['unparseable']}")
    if "census" in report:
        c = report["census"]
        if "error" not in c:
            print(f"\nCensus — graph `{c.get('graph')}` at {c.get('at')} "
                  f"({c.get('catalogue', '')}):")
            # A figure quoted into an epic body must carry the way it was
            # produced (`#10465` target 1) — the docstring says so, and the text
            # path was the one place that did not.
            print(f"  command: {c.get('command', CENSUS_COMMAND)}")
        for label, d in (c.get("labels") or {}).items():
            print(f"  {label:10s} stored={d['stored']!s:12s} "
                  f"indexed={d['indexed']!s:6s} unindexed={d['unindexed']!s:12s} "
                  f"payload={d['payload']['derived_payload_B']}")
            # #11088: the count is not the consequence — the SET of
            # `entity_type` values is. Printed on the row it belongs to, so a
            # reader cannot lift the number without reading what removing it
            # would change.
            # ⛔ `removal of the UNINDEXED remainder`, not `removal`: the field
            # is about the unindexed share, and on an INDEXED label (`:Point`,
            # the only one) the bare sentence read "removal changes query
            # results: False" — one paraphrase from "deleting every Point's
            # embedding is harmless" (review round 1, P2).
            print(f"  {'':10s} entity_type(s) served by this label: "
                  f"{d.get('affected_entity_types') or 'none (no query path)'}"
                  f" — removing the UNINDEXED remainder changes query results: "
                  f"{d.get('removal_changes_query_results')}")
            # ⛔ §3.1 leans on a FULLTEXT index to say a lexically-matching
            # query survives a vector removal, and said "the same run settles
            # it" while the run reported VECTOR rows only. The kinds are now
            # read, so they are PRINTED — a datum a reader must open the JSON
            # for is a datum the text mode's claim cannot stand on (review
            # round 7, P2).
            indexes = _non_vector_indexes_line(d.get("indexes_on_label"))
            if indexes:
                print(f"  {'':10s} {indexes}")
        sc = c.get("surface_consequence")
        if sc:
            print("  surface consequence — removal is REQUESTED, not "
                  "performed; it needs the owner's approval (MCP/SDK surface):")
            print(f"    affected entity_type values: "
                  f"{sc.get('entity_types_whose_query_results_change') or 'none'}")
            # ⛔ An UNMEASURED surface is PRINTED, not skipped — dropping it left
            # the `path:` line starting with a bare `/` and understated the
            # surface (review round 1, P3).
            for entry in sc.get("surface") or []:
                if "tool" not in entry:
                    print(f"    ⛔ surface: {entry.get('tools')}")

            def _surface_entry(entry: dict) -> str:
                # ⛔ A tool whose SDK method names the type itself carries the
                # TYPE, not a pass-through arrow: printing both classes the same
                # way would hide the one the caller never chooses (review round
                # 7, P1).
                fixed = entry.get("entity_types")
                chosen = ("" if not isinstance(fixed, list)
                          else ", entity_type=" + "|".join(fixed))
                return f"{entry['tool']} ({entry['rest']}{chosen})"

            path = " / ".join(_surface_entry(t) for t in sc.get("surface") or []
                              if "tool" in t)
            print(f"    path: {path}"
                  f" → {sc.get('sdk_method')}(entity_type=…) → {sc.get('runner')}")
            # The question the approval request actually asks: WHICH tools'
            # results change. Derived, per tool, from the measured set.
            changed = [t for t in sc.get("surface") or []
                       if "tool" in t and t.get("reaches_affected_labels")]
            if changed:
                print("    tools whose results change: " + " / ".join(
                    f"{t['tool']} ({t['reaches_affected_labels']})"
                    for t in changed))
            for entry in sc.get("unmeasured_labels") or []:
                print(f"    ⛔ UNMEASURED — {entry['label']} "
                      f"({entry['entity_types'] or 'no entity_type'}): {entry['why']}")
                print("       the affected set above is MEASURED ONLY; it is not "
                      "the whole population")
        if "error" in c:
            print(f"  {c['error']}")
        if c.get("population"):
            print(f"  population: {c['population']}")
        if c.get("unreadable_catalogue_rows"):
            # ⛔ The rows are `(label-or-None, why)` TUPLES (the per-label filter
            # unpacks them), so joining them as strings raised `TypeError` and
            # sent the whole run to `UNMEASURED(...)` with exit 2 — for a
            # census that measured fine. The `--json` path hid it (`default=str`).
            print("  unreadable catalogue rows: " + "; ".join(
                f"{lab}: {why}" if lab is not None else why
                for lab, why in c["unreadable_catalogue_rows"][:3]))
    return 2 if unmeasured else 0


def cli(argv: list[str] | None = None) -> int:
    """`main`, with the documented exit contract enforced for every route.

    The docstring promises `2` when the instrument could not measure. A raised
    `ImportError`, a row scorer that throws, or an unreachable engine must all
    land there — not on `1` with a traceback, which a caller cannot tell from a
    red row.
    """
    try:
        return main(argv)
    except SystemExit as e:  # `_connect` refuses an embedded engine this way
        if e.code in (0, None):
            return 0
        # `SystemExit("msg")` carries a STRING code — `int()` on it raised
        # out of this very handler and exited 1 (review round 5, P3).
        return e.code if isinstance(e.code, int) else 2
    except Exception as e:
        print(f"dod-5090-probe: UNMEASURED({type(e).__name__}: {e})",
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(cli())
