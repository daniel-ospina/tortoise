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

    ``{writer: {"callee", "at", "kind", "label"}}`` where ``kind`` is
    ``gated`` (reaches ``encode_for_row``), ``ungated`` (produces a vector the
    gate never sees) or ``producer`` (a function that computes a vector a
    DIFFERENT function writes — `encode_for_store`'s read/backfill callers, and
    the journal stampers). ``writer`` is ``<dotted module>::<function>``, so two
    writers in different modules can never share a key.

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
                "label": "UNKNOWN",
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
                label = "UNKNOWN(two labels: " + ",".join(sorted(labels)) + ")"
                _put(f"{module}::{node.name}", {
                    "callee": callee, "at": f"{rel}:{node.lineno}",
                    "kind": kind, "label": label,
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
    # ⛔ Built as locals, never nested inside another f-string: nesting a
    # `'`-delimited f-string inside a `"`-delimited one is PEP 701 **syntax**
    # (`Python-Version: 3.12`), so it cannot be tokenized by the older
    # interpreters this file's `#5128` guard exists to refuse — the guard would
    # be documentary and the file would die before reaching it.
    ungated_at = ", ".join(f"{w} @ {d['at']}" for w, d in sorted(ungated.items()))
    unresolved_at = ", ".join(
        f"{w} @ {d['at']}" for w, d in sorted(unresolved_writes.items())
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
        + (" — the session/Turn writers #5008 declares as exempt"
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
        # call is reported, not assumed clean: the session/Event chain calls
        # the primitive through `self._session_embedding`, so a three-name
        # callee vocabulary cannot see it (review round 5, P2).
        f"functions writing an embedding with no recognised producer call: "
        f"{len(unresolved_writes)} — deliberately OVER-INCLUSIVE (it also lists "
        f"repair/backfill helpers and the clause builder itself, because a "
        f"name-level enumeration cannot tell an exemption from a bypass; a "
        f"bypass that reads as exempt is the failure this row exists to "
        f"prevent): "
        f"{unresolved_at or 'none'}",
        f"duplicate writer names (hold this row): {sorted(duplicates) or 'none'}",
    ]
    if not stated or not shared or not gated:
        verdict = "NOT MET"
    elif not on_by_default or ungated or unparsed or unresolved_writes or duplicates:
        # EITHER condition alone means the rule is not the write path's
        # operative rule: the flag is the operator's, and an ungated writer is
        # a path the rule never sees. Turning the flag ON does not fix the
        # second — which is why it must hold the row, not be masked by it.
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


def score_path_bullet() -> Row:
    """Bullet 2 — every label a write path embeds is one the query path serves."""
    writers = embedding_writers()
    written = written_labels(writers)
    served = served_labels()
    labels = {label for label in written.values() if not label.startswith("UNKNOWN")}
    unknown = {fn: label for fn, label in written.items() if label.startswith("UNKNOWN")}
    duplicates = sorted(w for w, d in writers.items() if d["kind"] == "duplicate")
    unparsed = unparsed_modules(writers)
    orphans = labels - served
    notes = [
        f"written labels: {sorted(labels)}",
        f"served labels (run_vector_query vocabulary): {sorted(served)}",
        f"stored-without-a-query-path: {sorted(orphans) or 'none'}",
    ]
    if unknown:
        notes.append(f"unresolved writers (hold this row at PARTIAL): {sorted(unknown)}")
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
    unresolved_writers = {fn: d for fn, d in writers.items()
                          if d["kind"] == "unresolved_write"}
    # `unknown` is wider than this population by TWO label-keyed groups, and
    # neither is a census subject: the `duplicate` identities above, and
    # `gated`/`ungated` writers whose label derivation still failed (`UNKNOWN`,
    # or the `UNKNOWN(two labels: …)` shape) — those have their ROUTE resolved,
    # which is what `kind` records, so the three #10541 route classes cannot
    # apply to them. Because the two populations CAN differ, the note below
    # names the one it counted (`N of M UNKNOWN-labelled writers`) rather than
    # claiming "the unresolved set": a total that silently omits a writer this
    # row listed four notes up is the same defect shape the population fix
    # above removes.
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
            "#10541 census over the writers labelled UNKNOWN(unresolved "
            f"producer) — {len(unresolved_writers)} of {len(unknown)} "
            "UNKNOWN-labelled writers: "
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
    real_bypasses = sorted(by_class.get(CENSUS_BYPASS, []))
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
    verdict = "NOT MET" if orphans else (
        "PARTIAL" if (unknown or unparsed or duplicates) else "MET")
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
    for error in (handler_error, fixed_error):
        if error:
            out.append({"tools": error})
    # ⛔ The DECLARED BOUND is published on its own line, never merged into the
    # scan-failure channel: it says the scan ran and this is its known edge, which a
    # reader must be able to tell apart from "the scan could not run" (#11337).
    if fixed_declared:
        out.append({"tools": fixed_declared})
    # ⛔ An UNRESOLVABLE `VECTOR_LEG_SDK_METHOD` empties BOTH sets, so `out` was
    # `[]` — and `takes`, which is computed to be `UNMEASURED(...)` in exactly
    # that case, had no entry to ride on. The surface then read as a MEASURED
    # "no tool can pass entity_type" beside `entity_types_whose_query_results_
    # change` naming real types: the under-reporting direction, produced by the
    # very drift the resolution pin exists to catch — a pin protects the repo,
    # not the instrument as shipped (review round 2, P2).
    if not out or isinstance(takes, str):
        out.append({"tools": f"UNMEASURED(the surface could not be derived: "
                             f"{takes if isinstance(takes, str) else 'no tool resolves'})"})
    return out


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
        stack: list[ast.AST] = list(fn.body)
        while stack:
            node = stack.pop()
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                 ast.ClassDef)):
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
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                 ast.ClassDef)):
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
    # extraction below reads `func.attr` / `func.id`, so a reach that takes a
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
    # IS walked and IS reported — only the target binding is skipped.) Also NOT
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
        # to prevent, produced by the guard against false alarms. Same skip rule
        # as the mention walk, or the two disagree about what a scope is.
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
                if isinstance(n, (ast.ListComp, ast.SetComp, ast.DictComp,
                                  ast.GeneratorExp)):
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
        stack = list(fn.body)
        while stack:
            node = stack.pop()
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                 ast.ClassDef)):
                continue
            if id(node) not in call_funcs:
                reached = _reachable_by_name(node, mentionable, shadowed)
                if reached:
                    by_reference.setdefault(fn.name, set()).add(reached)
            stack.extend(ast.iter_child_nodes(node))
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
        stack: list[ast.AST] = list(fn.body)
        while stack:
            node = stack.pop()
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                 ast.ClassDef)):
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
                # a true 22,016 B). An indexed label cannot hold mixed widths,
                # so the disagreement is the no-index case's own hazard.
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
            unindexed = "UNMEASURED(stored count unreadable)"
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
            payload = {"derived_payload_B": "UNMEASURED(the catalogue row was "
                                            "unreadable, so coverage is not "
                                            "provable)",
                       "how": f"catalogue row unreadable: {unreadable_here[0]}"}
        elif width is None:
            # ⛔ The refusal, BEFORE the index is consulted: without a measured
            # width there is no byte figure, indexed or not — and when the probe
            # saw more than one width, the reason is that, not "unprobeable".
            payload = {"derived_payload_B": "UNMEASURED("
                                            + (width_reason or
                                               "stored width not probeable") + ")",
                       "how": ("a single width cannot stand for this label's "
                               "stored vectors" if width_reason
                               else "needs a width probe")}
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
            "unindexed": (f"UNMEASURED(catalogue row unreadable: {unreadable_here[0]})"
                          if unreadable_here else unindexed),
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
