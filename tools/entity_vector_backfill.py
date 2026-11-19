#!/usr/bin/env python3
"""#13685 — copy the graph's entity vectors into the record. A COPY, never a re-embed.

THE QUESTION THIS ANSWERS
=========================

    For every `:Point` / `:Object` / `:Subject` / `:Event` / `:Source` node that
    carries a stored `embedding` in the graph, does the record's row hold that
    node's vector — and if not, put it there, so the graph copy is no longer the
    only one?

`tools/entity_vector_parity_census.py` MEASURED the answer on the production corpus
BEFORE this tool ran (2026-11-19T01:40Z): **18,510 entity vectors were `GRAPH-ONLY`** —
Object 9,305, Subject 38, Event 9,167 — and the census's own verdict is *"do NOT
strip … the answer is a backfill, never a delete."*

⚠️ THAT IS THE PRE-RUN NUMBER, NOT THE STATE OF THE RECORD NOW. The tool was EXECUTED
against production at 2026-11-19T06:14Z (`#13685`'s run comment, plan digest
`sha256:f5a9100f…`): 17,859 fingerprints copied, and the census then reported Object
**267** / Event **346** / Subject **0** graph-only. `residual_graph_only` is **17,595**,
and it is NOT a copy problem: 613 entity nodes (267 Object + 346 Event) and 16,982
`:Point`s have **no record row at all** — a vector copy cannot repair a missing row
(inventing one would make a record-sourced rebuild and a JSONL rebuild disagree), so
the remaining blocker is a projection/coverage question, not this tool's.

⚠️ AND `:Point`'S GAP WAS OVERSTATED BY READING TWO COUNTS AS A SUBSET GAP. A
2026-11-18 note compared 45,979 graph-side vectors with 36,873 in the record and
inferred ~9,106 Points held a vector in the graph and none in the record. The tool's own
measurement says otherwise — `Point graph=31,610 record=36,942 fillable=0
unmatched=16,982 existing=14,628`: the graph count is LOWER, and the intersection it
would fill is EMPTY. `:Point` is still in this tool's DEFAULT population, because the
change is harmless and additive (this tool only FILLS, `WHERE … embedding IS NULL`) and
`#13685`'s scope-addition comment asks for it — not because it holds a copyable gap. See
:data:`BACKFILLED` for why it lives here and NOT in the census's `RETIRED`.

WHY THIS IS A COPY AND NOT A RE-EMBED
=====================================

⛔ **NO MODEL CALL IS MADE, ANYWHERE IN THIS FILE.** The record's `object`/`subject`
rows were written WITHOUT a vector on purpose: the entity folds wait
`WRITE_EMBED_BUDGET_S = 0.25 s` and then write the row rather than block on a 13.5 s
cold model load (`#13224`/`#13430`), so `embedding` is NULL on those rows while the
SAME entity already carries a vector in the graph — byte-identical in intent. The
repair is therefore: read `n.embedding` from the graph, write it into the record
row's `embedding` (jsonb), and let the EXISTING generated column compute
`embedding_vec vector(384)` through `public.record_embedding_vector(jsonb)` — the
exact converter the armed cutover already reads
(`supabase/migrations/20261109000001_record_embedding_vector.sql`, and for
`object`/`subject` `20261117000001_record_object_subject_embedding.sql`).

⚠️ **A node with no graph vector is COUNTED, never embedded.** That is the boundary
between this tool and `graph-scripts/backfill_embeddings.py` (which encodes). Two
different repairs, two different instruments; merging them would put a model call
behind an operator's data-migration button.

THE SHAPE (copied from `tools/strip_graph_entity_vectors.py`, deliberately)
==========================================================================

**A PREVIEW IS THE DEFAULT. There is no `--dry-run`: the ABSENCE of `--execute` is
the preview.** The preview reads both stores, prints the population it would copy
(per label, with a canonical digest), and writes nothing — so the plan a human
approves and the plan the execution re-verifies are the same artefact.

WHAT THE DIGEST IS OVER, AND WHY IT IS THAT SET
===============================================

The **population** digest is over the **COPYABLE POPULATION** — one `label<TAB>key`
line per graph node that (a) carries a vector and (b) has a record row to receive it.
The token the operator types is *that population ⊕ the plan's content*, so the
approval binds the artefact and not one field of it (`approval_digest`).
⛔ The population half is deliberately NOT over the "rows still NULL" set, and the
difference is what makes
this tool **restartable** where the stripper must be exact-set:

* filling a row does NOT change the copyable population, so a run killed half-way can
  be re-executed from the SAME approved plan and it simply resumes — `filled` picks up
  the remainder, `skipped_existing` reports what was already done;
* a node ADDED to the graph (or a record row that appears where there was none)
  DOES change it, so a population that moved after approval refuses. One node more,
  one node less, or any key that moved, refuses and names the per-label delta.

So the token authorises an exact POPULATION **and the plan's own content** (see
:func:`approval_digest`), and the write itself is
null-only + idempotent (`WHERE … AND embedding IS NULL`), which is the property
the copy needs: re-running can never overwrite, only fill.

⛔ AND `--execute` RE-MEASURES BOTH STORES, not just the population read. The census is
the instrument that MEASURED the pre-run blocking number (`18,510`), so its per-label numbers
are the authority the plan was approved against: :func:`_measure_label` builds them
from the census's OWN graph leg (`entity_vector_parity_census.read_graph_vector_keys`,
`RESULTSET_SIZE` truncation guard included) and the same `graph_only = keyed
graph-only + unkeyed` definition, and a live measurement that disagrees with the
plan's recorded numbers refuses with the delta. A backfill whose justification moved
is not the backfill that was approved. ⚠️ That definition is the census's as of
`#13647`'s PREDECESSOR — `#13647` moved the census to a RENDERED-identity join plus a
`render_collision` term, and this tool still joins on the RAW key; :func:`_measure_label`
carries the exact boundary and what it costs.

⚠️ WHY IT IS THE CENSUS'S LEGS AND NOT `parity.census()` ITSELF. `:Point` is not in
that function's population (see :data:`BACKFILLED`), so calling it with the five
labels this tool copies would raise `CensusError` and refuse EVERY execution; calling
it with four and measuring Point separately would give one run two definitions of the
same number. And a second full census read would double a 46k-Point run's graph cost
for no information. So one function measures every label, from the census's own graph
leg, and `test_the_per_label_numbers_are_the_census_numbers` pins the equality with
`parity.census()` for the labels the census does measure — the census stays the
instrument, this tool only runs its legs.

THE PRECONDITIONS, EACH ASSERTED AT RUNTIME AND EACH ITS OWN TOKEN
==================================================================

`--execute` refuses — always before the first write, never part-way through — unless:

``REFUSE_NO_PLAN``
    `--execute` without `--plan`. The authority is a reviewed artefact, not a
    race with a table.
``REFUSE_NO_DIGEST``
    `--plan` without `--confirm-plan-digest`. The digest is the token; a plan file
    is a proposal, and copying one is trivial.
``REFUSE_PLAN_UNREADABLE`` / ``REFUSE_DIGEST_MISMATCH`` / ``REFUSE_PLAN_TAMPERED``
    The plan cannot be parsed, was written by another tool, is structurally incomplete,
    or its OWN recorded digest disagrees with `--confirm-plan-digest` — i.e. it was
    edited after printing. ``REFUSE_PLAN_TAMPERED`` is the EDIT detector: the plan
    records `content_sha256` over the fields that scope the write, and `digest` is
    sha256(population ⊕ content), so changing anything the token authorises makes the
    file's own two halves disagree — and re-sealing the content hash cannot recreate
    the operator's token, whose population half is derived from keys the plan does not
    carry.
``REFUSE_TARGET_DRIFT``
    The graph this run resolved is not the graph the plan was built against.
``REFUSE_PLAN_STALE``
    The live copyable population does not yield the plan's digest, or the live census
    disagrees with the plan's recorded census. The message names which and the delta.
``REFUSE_NOTHING_TO_FILL``
    The copyable population is empty — a "success" that copied nothing. Pass
    `--expect-empty` when an empty population is the intended state (a graph that
    genuinely holds no entity vectors).

THE KEY MAPPING, AND WHY IT IS SPELLED ONCE
===========================================

Both sides are keyed by the node's IDENTITY, and neither spelling is invented here:

* **graph side** — the property the vector leg itself returns as the node's id
  (`tortoise/search_engine.run_vector_query`'s `id_field`: `url` for
  `source`/`document`, `eventId` for `event`, `id` otherwise). Read from
  :data:`BACKFILLED`, whose first four rows ARE
  `entity_vector_parity_census.RETIRED` (`*parity.RETIRED` — imported, never restated;
  `#9553`: a second declaration of a key column silently filed no row for a whole
  Event arm). `:Point` is the fifth row and its property is `id`.
* **record side** — `node_key`, the record's one identity column across every table
  (`NodeSnapshot`'s key, `node_identity`'s PK component, and the column the
  `chk_*_node_key_is_contract_key` constraints pin). Since `#9881` it IS the `id` for
  `object`/`subject`, it is the `id` for `point` too, and `event.node_key = event_id`
  / `source.node_key = url` by the same constraints. So ONE column serves all five
  labels, and the per-label contract columns (`id` / `event_id` / `url`) are the SAME
  values — the record read reads them back and REFUSES if they ever diverge, because
  that would mean the schema's own contract moved under the mapping this tool writes
  by.

A node whose record row cannot be found is **counted and reported** as
`still_unmatched`, never invented: this tool has no authority to create a record row
(a row is the projection of a journal line, and inventing one would make a
record-sourced rebuild and a JSONL rebuild disagree).

WHAT IS NEVER WRITTEN
=====================

* ⛔ **A non-NULL `embedding` is never overwritten.** The `UPDATE` carries
  `AND embedding IS NULL`, so the guard is in the statement, not in this file's
  memory of the read. A row filled by a racing writer between the read and the write
  is therefore skipped, not clobbered — and its `rowcount` shortfall is REPORTED.
* ⛔ **A row whose payload exists but whose GENERATED `embedding_vec` is NULL is
  `skipped_unreadable`, not overwritten.** That row answers no dense query, so the
  census still counts it `GRAPH-ONLY` — but the repair for it is a re-derivation
  (a different instrument), never a silent replacement of stored content.
* ⛔ **A vector that is not a numeric 384-long array is `skipped_uncopyable`.** The
  record's converter would store it and NULL the generated column, which is a write
  that changes nothing — so it is counted and surfaced instead of performed.

HARD BOUNDARIES
===============

⛔ No stripping, no write-stop, no graph mutation — this tool issues no
`SET`/`REMOVE`/`DELETE`/`CREATE` against the graph at all (its graph leg travels
`ro_query`, the verb the engine REJECTS for a write). ⛔ No model calls. ⛔ No
MCP/SDK surface change; no migration. ⛔ Never point this at the hosted database
from a test: the acceptance lane is a scratch `pgvector/pgvector:pg16` (a plain
`postgres` image cannot apply `20261109000001`).

Usage::

    # 1. preview (default) — read both stores, print the plan, write nothing.
    #    ⛔ `:Point` is in this default population; `--label` (repeatable) narrows it.
    uv run python tools/entity_vector_backfill.py \\
        --uri 'docker://:pw@host:6379/<graph>' --graph-name <graph> \\
        --record-dsn 'postgresql://tortoise_record_app:...@host/db' \\
        --org-id <org> --graph-id <graph> --plan-out plan.json

    # 2. execute the plan a human approved, by its digest
    uv run python tools/entity_vector_backfill.py \\
        --uri 'docker://:pw@host:6379/<graph>' --graph-name <graph> \\
        --record-dsn 'postgresql://tortoise_record_app:...@host/db' \\
        --org-id <org> --graph-id <graph> \\
        --execute --plan plan.json --confirm-plan-digest sha256:<...>

    # 3. verify from the other instrument — the census must now report PARITY for
    #    every label IT measures (Object/Subject/Event/Source; it does not measure
    #    `:Point`, whose verdict is this tool's own `parity_by_label`)
    uv run python tools/entity_vector_parity_census.py … --require-parity

Exit status: ``0`` preview printed, or execution completed with every planned label
at parity · ``1`` usage · ``2`` execution REFUSED (nothing written) · ``3`` a side
could not be read, or a batch write FAILED (the report names what is already durable)
· ``4`` execution completed but graph-only vectors REMAIN (the strip is still
blocked — see `still_unmatched` / `skipped_unreadable` / `uncopyable`).

`--json` is written on every exit this tool takes, the refusal and failed-write ones
included — a caller that asked for a machine-readable report must not get nothing on
exactly the runs it needs it for. ⛔ THE ONE EXCEPTION IS ARGPARSE'S OWN FAILURES (an
unknown flag, a missing required argument, a bad `--label` choice): they raise
`SystemExit(2)` inside `parse_args`, before `main()` has an `args` object to read
`args.json` from, so no report is possible there and `--json` may not even have been
parsed. That boundary is pinned by
`tests/test_13685_entity_vector_backfill.py::test_argparse_failures_are_the_only_unreported_exits`,
so it cannot widen silently into an exit that DOES know about `--json`. Every one of
those payloads carries an `outcome` key, so a caller can branch on it instead of
inferring the run's state from which keys are present: ``preview`` · ``complete`` ·
``empty`` · ``residual`` · ``unverified`` · ``refused`` · ``write-failed`` ·
``failed`` · ``usage``. `--json` is the REPORT;
`--plan-out` is the artefact `--execute` reads, and it never carries an `outcome`.
"""

from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter BEFORE the imports below — a module-level
# 3.12-only import would fail first, and the operator would get an unattributed
# traceback instead of this sentence (the `tools/` entry-point contract;
# `tests/test_entry_point_python_guard.py` pins it for the corpus). This guard is
# PROTECTIVE, not documentary: nothing below it may use 3.12-only SYNTAX, or the file
# would die in the tokenizer and never reach this statement.
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/entity_vector_backfill.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/entity_vector_backfill.py`"
    )

import argparse
import contextlib
import hashlib
import json
import math
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import entity_vector_parity_census as parity
import turn_parity_census as census_mod

CensusError = parity.CensusError

#: The plan artefact's own name and schema version. `--execute` refuses a plan whose
#: `tool` is not this string, so a hand-written or other-tool JSON cannot pose as one.
PLAN_TOOL = "entity_vector_backfill"
#: ⛔ v2 is NOT cosmetic and is NOT backwards-compatible. v1 carried the population
#: digest ALONE, so `--confirm-plan-digest` bound one string field of the file and
#: nothing else: an editor could retarget a label's `record_table` (writing an Object's
#: vector onto a Point's row), or blank the `census` rows that the graph-side drift
#: guard compares against, and the digest still matched (review finding 1, reproduced).
#: v2 makes the approved token cover the plan's CONTENT as well as its population
#: (:func:`approval_digest`), and stores the graph address REDACTED with a hash
#: (:func:`_redact_uri`) instead of the credentialed URI (finding 6).
#:
#: ⛔ v3 is a SEAL-COMPOSITION change, which is the same class of break: `batch` left
#: :data:`_PLAN_CONTENT_FIELDS` (it is provenance — `execute` takes the batch from the
#: command line, so the recorded value is inert), so a v2 plan's `content_sha256` was
#: computed over a DIFFERENT field set. Left at v2, that plan would fail the CONTENT
#: check and be reported as `REFUSE_PLAN_TAMPERED` — "the file was edited after it was
#: printed" — for a file nobody touched, sending an operator to re-derive a tamper that
#: did not happen. The version is what says "re-plan", so it moves whenever the seal's
#: composition does; a v2 plan now refuses as UNREADABLE, which is the true diagnosis.
PLAN_VERSION = 3

#: The labels this tool copies, in the order they are reported: the CENSUS's own
#: declared population (`entity_vector_parity_census.RETIRED`, imported rather than
#: restated — a second spelling of the key map is what `#9553` records silently filing
#: no row for a whole Event arm) PLUS `:Point`, appended last.
#:
#: ⛔ WHY `:Point` IS HERE AND NOT IN `RETIRED`. That tuple is the population a
#: DESTRUCTIVE tool iterates — `strip_graph_entity_vectors` REMOVEs every vector its
#: plan names — so widening it would widen the strip, which is exactly why the census
#: declares `:Point` in `NOT_IN_SCOPE` and a pin asserts its absence BY NAME. THIS
#: tool only FILLS (`UPDATE … WHERE embedding IS NULL`), so the same label is safe
#: here and `#13685`'s scope comment asks for it. ⚠️ Its measured `fillable` on
#: production was **0** (`Point graph=31,610 record=36,942 unmatched=16,982 existing=
#: 14,628`): the graph-only Points have NO RECORD ROW AT ALL, so this label is additive
#: and harmless rather than a fix for a copyable gap — see the module docstring. One
#: population, derived once, and
#: `tests/test_13685_entity_vector_backfill.py` iterates THIS constant.
BACKFILLED: tuple[tuple[str, str, str], ...] = (
    *parity.RETIRED,
    # `chk_point_node_key_is_contract_key`: `node_key = id`, exactly as for the
    # object/subject twins since `#9881`.
    ("point", "Point", "id"),
)

#: Rows per `UPDATE`. Small enough that one `IN $keys` graph read and one statement
#: stay far UNDER FalkorDB's `RESULTSET_SIZE` (the silent truncation
#: `entity_vector_parity_census.GRAPH_PAGE_SIZE` already guards for the census leg) and
#: that one batch's HNSW index maintenance stays inside the read door's own
#: `statement_timeout`.
DEFAULT_BATCH = 200

#: The largest batch this tool will accept. The graph leg of a batch is one
#: `IN $keys` statement, which FalkorDB truncates SILENTLY past `RESULTSET_SIZE`
#: (default 10,000) — so the bound is the census's page size, not a taste.
MAX_BATCH = parity.GRAPH_PAGE_SIZE

#: The vector width the record's generated column accepts
#: (`public.record_embedding_vector(jsonb)` NULLs anything else), and therefore the
#: width a copied vector must have to be worth writing at all.
_RECORD_WIDTH = 384

#: Refusal tokens. A CLOSED set, printed verbatim, so a caller can gate on the token
#: instead of parsing a sentence — and so a test can assert the population of
#: refusals rather than a string a later wording change would silently retire.
REFUSE_NO_PLAN = "REFUSE_NO_PLAN"
REFUSE_NO_DIGEST = "REFUSE_NO_DIGEST"
REFUSE_PLAN_UNREADABLE = "REFUSE_PLAN_UNREADABLE"
REFUSE_DIGEST_MISMATCH = "REFUSE_DIGEST_MISMATCH"
REFUSE_TARGET_DRIFT = "REFUSE_TARGET_DRIFT"
REFUSE_PLAN_STALE = "REFUSE_PLAN_STALE"
REFUSE_PLAN_TAMPERED = "REFUSE_PLAN_TAMPERED"
REFUSE_NOTHING_TO_FILL = "REFUSE_NOTHING_TO_FILL"

REFUSALS = (
    REFUSE_NO_PLAN,
    REFUSE_NO_DIGEST,
    REFUSE_PLAN_UNREADABLE,
    REFUSE_DIGEST_MISMATCH,
    REFUSE_TARGET_DRIFT,
    REFUSE_PLAN_STALE,
    REFUSE_PLAN_TAMPERED,
    REFUSE_NOTHING_TO_FILL,
)


#: The CLOSED set of `--json` ``outcome`` values — one key every report payload
#: carries, so a consumer writes ONE branch instead of inferring the run's state from
#: which keys happen to be present. Closed for the same reason :data:`REFUSALS` is: a
#: caller gates on the value, so a later edit must not invent a spelling silently, and a
#: test can assert the population rather than a string in prose.
#:
#: ``preview`` the plan a preview measured · ``complete`` execution completed · ``empty``
#: nothing to fill, executed as a no-op · ``residual`` completed but graph-only vectors
#: remain (exit 4) · ``unverified`` the copy is durable but the post-run census failed
#: (exit 3) · ``refused`` a precondition refused before any write (exit 2) · ``failed`` a
#: side could not be read, or (``write-failed``) a batch write failed part-way (exit 3) ·
#: ``usage`` the command line itself was unusable (exit 1).
REPORT_OUTCOMES = (
    "preview",
    "complete",
    "empty",
    "residual",
    "unverified",
    "refused",
    "failed",
    "write-failed",
    "usage",
)


class Refusal(Exception):
    """An execution precondition did not hold. ``token`` is a :data:`REFUSALS` member."""

    def __init__(self, token: str, message: str) -> None:
        if token not in REFUSALS:  # a refusal outside the closed set is a bug
            raise ValueError(f"{token!r} is not in REFUSALS")
        super().__init__(message)
        self.token = token


class WriteFailed(Exception):
    """A batch write FAILED part-way. ``partial`` carries what is already durable.

    ⛔ NOT a :class:`Refusal`: a refusal means *nothing* was written and the operator
    can fix the plan. This one means some batches COMMITTED, so the operator must be
    told exactly how much — the seam is restartable (an approved plan re-executes to
    fill only the remainder), but only if the report says where it stopped.
    """

    def __init__(self, message: str, partial: dict) -> None:
        super().__init__(message)
        self.partial = partial


# ── the graph leg: READ ONLY, and the vectors themselves ────────────────────


#: The batch's vector read. Bounded by `--batch` (≤ :data:`MAX_BATCH`), and keyed by
#: the SAME keys the census's guarded page walk produced — so this leg inherits the
#: truncation guard rather than restating a second paging implementation.
_GRAPH_VECTORS_CYPHER = (
    "MATCH (n:{label}) WHERE n.embedding IS NOT NULL AND n.{key_prop} IN $keys "
    "RETURN n.{key_prop} AS node_key, n.embedding AS embedding"
)


def _population(labels: list[str] | None) -> tuple[tuple[str, str, str], ...]:
    """The selected ``(record_table, graph_label, graph_key_property)`` rows.

    ``None`` is EVERY label (:data:`BACKFILLED`) — ``:Point`` included, because
    `#13685`'s scope-addition comment asks for it and the fill is additive only
    (⚠️ its measured production `fillable` was 0 — the graph-only Points have no record
    row, so this label is harmless here, not a closed gap; see the module docstring).
    """
    if labels is None:
        return BACKFILLED
    wanted = set(labels)
    known = {label for _t, label, _k in BACKFILLED}
    unknown = sorted(wanted - known)
    if unknown:
        raise CensusError(
            f"unknown --label value(s): {unknown} — this tool copies {sorted(known)}")
    return tuple(row for row in BACKFILLED if row[1] in wanted)


def _key_prop(label: str) -> str:
    """The graph identity property for ``label``, from the ONE population declaration."""
    for _table, name, key_prop in BACKFILLED:
        if name == label:
            return key_prop
    raise CensusError(f"{label!r} is not this tool's entity population")


def _narrow(vector: Any) -> list[float] | None:
    """``vector`` at the store's own ``float32`` width, or ``None`` if it is not one.

    ⛔ THE NARROWING IS NOT COSMETIC. The graph holds an entity vector at the engine's
    ``float32`` width, so what this leg reads back is already f32-exact; the record's
    `embedding` payload is written as JSON numbers and the GENERATED column casts it
    back to `vector(384)`. Narrowing through `tortoise.stored_form` — the repo's ONE
    declaration of the stored form — makes the round trip exact instead of nearly
    exact, and gives the tool one total, non-raising answer for a value that is not a
    numeric vector at all (`None`), which is what `skipped_uncopyable` reports.

    Returns `None` for a non-numeric or over-range element, never a fabricated vector:
    a copy that invents a value is worse than a copy that reports one it could not do.
    """
    from tortoise.stored_form import narrow_to_stored_width

    narrowed = narrow_to_stored_width(vector)
    if narrowed is None:
        return None
    # ⛔ NON-FINITE IS NOT A VECTOR, and `struct.pack("<f", float('nan'))` ACCEPTS it
    # (`narrow_to_stored_width` is right to be total: it answers "what width", not
    # "is this copyable"). A NaN/inf element survives the length check below and is
    # then serialised by `json.dumps` as the bare token `NaN`, which is not JSON and
    # which Postgres refuses — so the batch raised, `WriteFailed` exited 3, and because
    # `keys = sorted(fillable)` is deterministic, re-executing reproduced the SAME
    # failure at the SAME batch: the label was WEDGED until someone hand-edited the
    # graph (review finding 4, reproduced). Counted as `skipped_uncopyable` instead,
    # which is what the docstring already promised for this class.
    if not all(math.isfinite(value) for value in narrowed):
        return None
    return narrowed


def read_graph_vectors(g: Any, label: str, keys: list[str]) -> dict[str, Any]:
    """The vectors for ``keys``, as read from the graph — read-only, no writes.

    Returns ``{key: narrowed_vector_or_None}``. A key absent from the result lost its
    vector between the census and this read; the caller counts it, it does not fail.
    Raises :class:`CensusError` when the statement itself cannot be issued: an
    unreadable graph is a REFUSAL, never "this label had no vectors".
    """
    if not keys:
        return {}
    quoted = parity._quote_identifier(label)
    quoted_key = parity._quote_identifier(_key_prop(label))
    statement = _GRAPH_VECTORS_CYPHER.format(label=quoted, key_prop=quoted_key)
    try:
        rows = parity._read(g, statement, {"keys": list(keys)}).result_set
    except Exception as exc:
        raise CensusError(
            f"the graph's `{label}` vectors could not be read "
            f"({type(exc).__name__}: {exc}) — nothing was written for this batch") from exc
    out: dict[str, Any] = {}
    for row in rows:
        try:
            key, vector = row[0], row[1]
        except (IndexError, TypeError) as exc:
            raise CensusError(
                f"the graph's `{label}` read returned a row that is not "
                f"`(key, embedding)` ({row!r}) — the shape this tool copies has "
                "moved, so nothing was written") from exc
        if not isinstance(key, str) or not key:
            # Unreachable through `IN $keys` — and a REFUSAL rather than a skip if it
            # ever becomes reachable: a vector-bearing node with no identity is the
            # very population `entity_vector_parity_census` counts as `unkeyed`, and
            # dropping it silently here is how a copy reports success over a hole.
            raise CensusError(
                f"the graph's `{label}` keyed read returned a row with no identity "
                f"key for keys {sorted(out)[:3]}… — the node shape this copy "
                "addresses has moved, so nothing was written")
        out[key] = _narrow(vector)
    return out


# ── the record leg: the one door, so a test can replace it ──────────────────

#: The per-label record read. `node_key` is the identity column the write addresses;
#: the CONTRACT column (`id` / `event_id` / `url`) is read alongside it so a schema
#: that ever decoupled them is a REFUSAL rather than a silent mismatch.
_RECORD_TARGET_SQL = (
    "SELECT node_key, {contract}, "
    "       (embedding IS NULL) AS missing, "
    "       (embedding_vec IS NOT NULL) AS readable "
    "FROM public.{table} WHERE org_id = %s AND graph_id = %s"
)

#: The columns the record read depends on, asserted from the cursor's own description
#: so a renamed/dropped column is a REFUSAL rather than an empty comparison that
#: certifies "nothing to fill" vacuously.
_RECORD_TARGET_COLUMNS = ("node_key", "missing", "readable")

#: The one write this tool issues — ONE statement per BATCH, not one per row.
#: ⛔ `AND t.embedding IS NULL` is the NEVER-OVERWRITE guard, and it lives in the
#: STATEMENT so it holds against a row that changed between the read and the write (the
#: `#13629` review lesson: a gate that trusts a predicate it read earlier is trusting a
#: memory). The `VALUES` list is built from the batch's own ``(node_key, vector)``
#: pairs, so one statement's `rowcount` is the batch's real answer — a per-row loop
#: would report the same total through N round trips and N index updates.
_WRITE_SQL = (
    "UPDATE public.{table} AS t SET embedding = v.payload "
    "FROM (VALUES {values}) AS v(node_key, payload) "
    "WHERE t.org_id = %s AND t.graph_id = %s AND t.node_key = v.node_key "
    "AND t.embedding IS NULL"
)


def _record_contract_column(table: str) -> str:
    """The label's contract key column, from the schema's own CHECK vocabulary.

    ⛔ THIS IS NOT A SECOND DECLARATION OF THE KEY MAP. The `chk_*_node_key_is_contract_key`
    constraints pin `node_key` to exactly these columns, so what this function names is
    the column the schema ALREADY equates with `node_key` — and :func:`RecordDoor.targets`
    verifies the equality at runtime rather than assuming it.
    """
    contract = {
        "point": "id",        # `chk_point_node_key_is_contract_key`
        "object": "id",       # `#9881`: node_key moved from `name` to `id`
        "subject": "id",      # …and its twin
        "event": "event_id",  # `chk_event_node_key_is_contract_key`
        "source": "url",      # `chk_source_node_key_is_contract_key`
    }
    if table not in contract:
        raise CensusError(f"{table!r} is not one of this tool's record tables")
    return contract[table]


class RecordDoor:
    """The record: ONE connection, one tenant-scoped transaction per batch.

    ⛔ THE SCOPE IS RE-APPLIED PER BATCH, and that is load-bearing rather than
    defensive. `record_projection.scope_connection` installs the tenant GUC
    **transaction-locally** on purpose (a session-level `SET` would outlive the
    transaction and hand the next tenant the previous one's scope, `#9600` cycle 3) —
    and this tool commits per batch precisely so a killed run leaves finished batches
    durable. Those two facts together mean the scope dies at each commit, so a batch
    that skipped re-scoping would write with the GUC unset, where `WITH CHECK` treats
    a NULL tenant as a policy violation (a loud failure, never a wrong-tenant write —
    and the loud failure is what we want to avoid by scoping correctly, not to rely on).
    """

    def __init__(self, record_dsn: str, org_id: str, graph_id: str) -> None:
        self.record_dsn = record_dsn
        self.org_id = org_id
        self.graph_id = graph_id
        self._conn: Any = None

    # ── lifecycle ──────────────────────────────────────────────────────────
    def _connection(self) -> Any:
        if self._conn is None:
            from tortoise import record_serve

            # ⛔ THE OPEN IS PART OF THE READ, so a failure to open is a CensusError
            # like any other unreadable side — not an escaping `RecordUnavailable`.
            # It used to sit OUTSIDE every guard (review finding 3, reproduced): a
            # wrong password or a down database produced a traceback and exit 1 —
            # which this module's own exit table reserves for USAGE errors — and NO
            # `--json` report, on the likeliest operator mistake there is.
            try:
                self._conn = record_serve.open_record(self.record_dsn)
            except Exception as exc:
                raise CensusError(
                    "the record could not be opened "
                    f"({type(exc).__name__}: {exc}) — an absence is NOT established, "
                    "so nothing was written") from exc
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            with contextlib.suppress(Exception):  # best-effort close
                self._conn.close()
            self._conn = None

    def __enter__(self) -> RecordDoor:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # ── the read ───────────────────────────────────────────────────────────
    def targets(self, table: str) -> dict:
        """Every record row for this tenant, bucketed by what a copy can do with it.

        Returns ``{"rows", "missing", "readable"}`` — sets of `node_key`. ``rows`` is
        every row that exists (the set `still_unmatched` is computed against),
        ``missing`` those whose `embedding` is NULL (the only writable ones), and
        ``readable`` those whose GENERATED `embedding_vec` is present.
        """
        from tortoise import record_projection

        conn = self._connection()
        contract = _record_contract_column(table)
        sql = _RECORD_TARGET_SQL.format(table=table, contract=contract)
        try:
            cursor = record_projection.scope_connection(conn, self.org_id)
            try:
                cursor.execute(sql, (self.org_id, self.graph_id))
                columns = [d[0] for d in cursor.description]
                fetched = cursor.fetchall()
            finally:
                with contextlib.suppress(Exception):
                    cursor.close()
        except CensusError:
            raise
        except Exception as exc:
            raise CensusError(
                f"`public.{table}` could not be read ({type(exc).__name__}: {exc}) — "
                "an absence is NOT established, so nothing was written") from exc

        missing_cols = [c for c in _RECORD_TARGET_COLUMNS if c not in columns]
        if missing_cols:
            raise CensusError(
                f"the `public.{table}` read did not return {', '.join(missing_cols)} — "
                "the record schema this tool writes through has moved "
                f"(got: {', '.join(str(c) for c in columns)})")

        rows: set[str] = set()
        missing: set[str] = set()
        readable: set[str] = set()
        for raw in fetched:
            rec = dict(zip(columns, raw, strict=False))
            key = rec.get("node_key")
            if not isinstance(key, str) or not key:
                # A row with no identity cannot be joined to the graph, so it can
                # never be the row a graph node is copied into. Counted by absence
                # from `rows`, never guessed at.
                continue
            contract_value = rec.get(contract)
            if contract_value != key:
                raise CensusError(
                    f"`public.{table}` row {key!r} has {contract}={contract_value!r} — "
                    "the schema's `chk_*_node_key_is_contract_key` equality has "
                    "moved, so the key this tool maps graph nodes BY is no longer "
                    "the key the table holds. Nothing was written.")
            rows.add(key)
            if rec.get("missing"):
                missing.add(key)
            elif rec.get("readable"):
                readable.add(key)
        return {"rows": rows, "missing": missing, "readable": readable}

    # ── the write ──────────────────────────────────────────────────────────
    def write(self, table: str, pairs: list[tuple[str, list[float]]]) -> int:
        """One committed batch, as ONE statement. Returns the rows it changed.

        ``pairs`` is ``(node_key, vector)``; the statement re-asserts
        ``embedding IS NULL``, so a key whose row was filled by someone else since the
        read contributes 0 — the caller reports the shortfall instead of pretending.
        """
        from tortoise import record_projection

        if not pairs:
            return 0
        payload: dict[str, list[float]] = {}
        for key, vector in pairs:
            payload.setdefault(key, vector)
        values_sql = ", ".join("(%s::text, %s::jsonb)" for _ in payload)
        try:
            # ⛔ `allow_nan=False` and INSIDE the guard. `json.dumps` emits `NaN` /
            # `Infinity` by default, which Postgres refuses as jsonb — and a
            # `ValueError` raised before this `try` escaped as an uncaught traceback
            # with no report. `_narrow` already rejects non-finite values, so this is
            # the belt to that brace: a future caller of `write()` cannot emit a
            # payload the store will reject.
            params: list[Any] = []
            for key, vector in payload.items():
                params.extend((key, json.dumps(vector, allow_nan=False)))
            params.extend((self.org_id, self.graph_id))
            conn = self._connection()
            cursor = record_projection.scope_connection(conn, self.org_id)
            try:
                cursor.execute(
                    _WRITE_SQL.format(table=table, values=values_sql), params)
                written = max(int(cursor.rowcount), 0)
                conn.commit()
            finally:
                with contextlib.suppress(Exception):
                    cursor.close()
        except Exception as exc:
            with contextlib.suppress(Exception):
                conn.rollback()
            raise CensusError(
                f"the `public.{table}` write FAILED ({type(exc).__name__}: {exc}) — "
                "the transaction was rolled back, so this batch is not durable; "
                "earlier batches are") from exc
        return written


# ── the plan ────────────────────────────────────────────────────────────────


def digest(entries: list[tuple[str, str]]) -> str:
    """The canonical digest of an exact population: one ``label<TAB>key`` line per node.

    Sorted, tab-separated, newline-joined, `sha256`-prefixed. Both properties are
    load-bearing and neither is style: the digest is over the WHOLE set (so a single
    key moving changes it), and it is over ROWS rather than a JSON blob (so a
    re-serialisation of the plan file cannot change what was approved).
    """
    body = "\n".join(f"{label}\t{key}" for label, key in sorted(set(entries)))
    return "sha256:" + hashlib.sha256(body.encode()).hexdigest()


#: The plan keys whose VALUES scope the write. An ALLOW-LIST, not `everything but the
#: hashes`: a field added later is out of the approved promise until it is named here,
#: which is the fail-closed direction (an unlisted field cannot silently widen what the
#: operator approved). Every entry either selects the target (`graph`, `graph_uri_sha256`,
#: `org_id`, `graph_id`) or scopes the population (`population`, `census`, `labels`,
#: `population_digest`).
#:
#: ⛔ `batch` IS DELIBERATELY NOT HERE (review cycle 2, finding 1). It is the size of one
#: `UPDATE` — a pace/statement knob — and the copy it produces is identical at any size
#: (`keys = sorted(fillable)` is partitioned, not sampled; the write is null-only and
#: idempotent), and `execute` takes the batch from the COMMAND LINE, so the value the
#: plan records cannot change what is written by any path. A seal may only cover values
#: that scope the write: sealing an inert field means an edit to it — a harmless act —
#: reports `REFUSE_PLAN_TAMPERED`, i.e. a tamper diagnosis for a migration that is not
#: changed. It stays in the plan as provenance and is shape-checked
#: (:data:`_PLAN_SHAPE`), and `PLAN_VERSION` moved to 3 with this change because the
#: seal's composition did.
_PLAN_CONTENT_FIELDS = (
    "tool", "version", "graph", "graph_uri_sha256", "org_id", "graph_id",
    "population", "census", "labels",
    "total_copyable", "total_fillable", "still_unmatched", "population_digest",
)


def content_sha256(plan: dict) -> str:
    """The digest of the plan's AUTHORITATIVE content — what it authorises, verbatim.

    Canonical JSON (`sort_keys`, no whitespace) over :data:`_PLAN_CONTENT_FIELDS`, so a
    re-serialisation of the file cannot change it while an EDITED value always does.
    """
    payload = {field: plan.get(field) for field in _PLAN_CONTENT_FIELDS}
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return "sha256:" + hashlib.sha256(body.encode()).hexdigest()


def approval_digest(population: str, content: str) -> str:
    """The token `--confirm-plan-digest` carries: population ⊕ content.

    ⛔ WHY BOTH HALVES. The population digest alone (v1) bound ONE STRING FIELD of the
    file: `_load_plan` compared `plan["digest"]` to the token and never recomputed
    anything, so any edit that left that field alone was invisible — a retargeted
    `record_table` wrote an Object's vector onto a Point's row, and a blanked `census`
    silenced the graph-side drift guard (finding 1). Binding the CONTENT too means the
    operator approves the artefact, not a field of it: an editor who re-seals
    `content_sha256` still cannot reproduce the token the operator typed, because the
    population half is derived from keys the plan does not carry.
    """
    body = f"{population}\n{content}"
    return "sha256:" + hashlib.sha256(body.encode()).hexdigest()


def _redact_uri(uri: str) -> str:
    """`uri` with its credential removed — the secret-hygiene rule this repo applies.

    The plan artefact is written to disk, handed to a human for approval, and kept
    until execution, so the raw `--uri` cannot go into it: a FalkorDB URI's userinfo
    routinely carries the password (`docker://:falkordb@host:6379`). Mirrors
    `tools/cypher_diff_harness.py::_redact`, including its `rsplit("@", 1)` rule — the
    authority ends at the LAST `@`, because both libpq and redis-py split userinfo
    that way and a generated password routinely contains a raw `@`. Splitting on the
    first one prints the tail of the password (#13098).
    """
    if "://" not in uri:
        return uri
    scheme, rest = uri.split("://", 1)
    # ⛔ The split is on the AUTHORITY SEGMENT, which ends at the first `/` (review cycle
    # 3, finding 4). `rest.rsplit("@", 1)` is right for a password containing a raw `@`
    # but wrong for an `@` in the PATH: `docker://h:6379/a@b` became `docker://h:***@b`,
    # inventing a userinfo and losing host, port and graph.
    authority, sep, tail = rest.partition("/")
    if "@" in authority:
        creds, host = authority.rsplit("@", 1)
        user = creds.split(":", 1)[0]
        return f"{scheme}://{user}:***@{host}{sep}{tail}"
    return uri


def _uri_authority(uri: str) -> str:
    """``uri`` without its userinfo — scheme, host, port and graph, never a credential.

    ⛔ WHAT THE DRIFT GUARD IS ABOUT (review cycle 2, finding 3). Hashing the WHOLE URI
    made `graph_uri_sha256` an offline dictionary oracle over the password — and this
    repo's documented FalkorDB URIs carry a KNOWN default (`docker://:falkordb@host:6379`,
    used verbatim in `AGENTS.md`), so the digest of the whole string is recoverable in
    microseconds. Worse, a ROTATED password produced `REFUSE_TARGET_DRIFT` with the
    message "a replica or a stale address is not the graph the plan measured" — a wrong
    diagnosis that sends an operator to re-plan a migration for a rotation. The guard
    exists to prove the plan and the run mean the SAME GRAPH, which the address answers
    and a credential does not; the tenant ids and the graph name are compared
    separately, so nothing about the destination is loosened by dropping the secret.

    ⛔ THE SPLIT IS ON THE AUTHORITY SEGMENT (review cycle 3, finding 4). Applied to the
    whole remainder, the same `rsplit` also split on an `@` in the PATH, so
    `docker://h:6379/a@b` and `docker://other:1/z@b` BOTH reduced to `docker://b` and
    hashed EQUAL — two different addresses passing the drift guard, a fail-open in the
    binding this function exists to keep. The authority ends at the first `/`.
    """
    if "://" not in uri:
        return uri
    scheme, rest = uri.split("://", 1)
    authority, sep, tail = rest.partition("/")
    if "@" in authority:
        authority = authority.rsplit("@", 1)[1]
    return f"{scheme}://{authority}{sep}{tail}"


def _table_for(label: str) -> str:
    """The record table a label copies INTO, from the ONE population declaration.

    ⛔ DERIVED, never read from the plan (finding 1). The plan records the table for a
    human to read; `execute` took it from the file and wrote where the file said, so a
    one-word edit retargeted the copy. The label is the authority; the plan's copy is
    checked against it and refuses when they disagree.
    """
    for table, name, _key_prop_name in BACKFILLED:
        if name == label:
            return table
    raise CensusError(f"{label!r} is not this tool's entity population")


def _read_targets(door: RecordDoor, table: str) -> dict:
    """``door.targets`` behind a seam a test can replace without a database."""
    return door.targets(table)


#: The census-shaped fields every measurement publishes, so a reader can put this
#: tool's table beside `tools/entity_vector_parity_census.py`'s and see the same
#: vocabulary (`graph_vectors` / `record_vectors` / `graph_only` / ...).
_CENSUS_FIELDS = (
    "label",
    "record_table",
    "graph_vectors",
    "record_vectors",
    "graph_only",
    "unkeyed_graph_nodes",
    "record_unreadable_vector",
    "record_only",
)

#: The `_CENSUS_FIELDS` members that are COUNTS — validated as integers at plan load, so a
#: hand-written plan cannot hand a `None`/string to a reader that does arithmetic on it
#: (review cycle 2, finding 2). `label`/`record_table` are strings and validated as such.
_CENSUS_COUNT_FIELDS = _CENSUS_FIELDS[2:]


def _measure_label(g: Any, door: RecordDoor, table: str, label: str) -> dict:
    """One label, both stores, in the CENSUS's own vocabulary and definitions.

    ⛔ THE GRAPH LEG IS THE CENSUS'S OWN (`entity_vector_parity_census.
    read_graph_vector_keys` — its aggregate-vs-paged cross-check included, which is
    what makes FalkorDB's SILENT `RESULTSET_SIZE` shortfall loud), and the numbers are
    built with the census's own definitions: `graph_only = |keyed graph keys − record
    keys with a readable vector| + unkeyed`, and `record_vectors` is the count of rows
    whose GENERATED `embedding_vec` is present — `embedding IS NOT NULL` is NOT the
    parity test, because a payload the converter refuses answers no dense query.

    ⚠️ THAT DEFINITION IS NOT CURRENTLY THE CENSUS'S, AND THE DIFFERENCE IS MEASURED
    (`#13647` moved it under this tool). The census now joins on the RENDERED identity
    (`graph["text"]`) plus a `render_collision` term, because the record's identity
    column is TEXT; this tool joins on the RAW key, because the raw value is what the
    `UPDATE … WHERE node_key = %s` it issues must match. On a NON-STRING graph identity
    the two therefore disagree, and the tool is the side that over-reports: a node keyed
    `id=123` against a record row holding `"123"` measures `graph_only=1` here and `0`
    in the census, so the node is never `fillable` and its residual never clears. This is
    STATED rather than silently half-fixed: adopting the census's rendered join alone
    would not reconcile them either (it would double-count the collision excess against
    the raw-key mismatch), and choosing which identity the WRITE path matches on is a
    design decision, not a merge repair. Tracked as `#13775`; the pin that
    compares the two instruments is bounded to populations where the rendered and raw
    forms coincide, and says so.

    The RECORD leg is :meth:`RecordDoor.targets`, the same read the write addresses
    (it is the only read that can tell this tool which rows are NULL). One pass per
    label serves the plan, the pre-write re-measurement and the post-run verdict, so
    plan and execution cannot measure two different things.

    ⚠️ `parity.census()` ITSELF IS DELIBERATELY NOT CALLED — see the module docstring:
    it cannot be given `:Point` (that label is outside its `RETIRED` population, which
    gates a destructive tool), and calling it for the other four would both double the
    graph read on a 46k-Point corpus and give one run two definitions of one number.
    `tests/test_13685_entity_vector_backfill.py::test_the_per_label_numbers_are_the_census_numbers`
    pins this function's output equal to `parity.census()`'s for the labels the census
    does measure, and the scratch-Postgres end-to-end leg runs the census tool itself.

    ⛔ BOTH RECORD NUMBERS ARE NARROWED TO THIS LABEL'S GRAPH KEYS, which is the
    census's own definition of them (`entity_vector_parity_census`, `#13647`):
    `record_vectors` is *"of THIS label's graph key set, the rows the record can answer
    with a readable `embedding_vec`"*, because *"a tenant-wide total answers 'does the
    record hold vectors', not 'does it hold THIS node's'"*. The door reads the WHOLE
    table — it must, since it is the same read that reports which rows are NULL and
    therefore fillable — so the intersection is this function's job. Without it the
    plan's record column and its graph column describe two different populations while
    the census reports one — which is what the equality pin caught when that census
    change (#13647) landed.
    """
    graph = parity.read_graph_vector_keys(g, label, _key_prop(label))
    record = _read_targets(door, table)
    graph_keys = set(graph["keys"])
    readable = set(record["readable"])
    #: The rows of the MEASURED population the record can answer — see this function's
    #: docstring for why the intersection is not optional.
    readable_in_population = readable & graph_keys
    #: Row-bearing keys whose vector is unusable (`embedding` set, generated column
    #: NULL — the converter refused the payload), likewise narrowed.
    unreadable = set(record["rows"]) - set(record["missing"]) - readable
    return {
        "label": label,
        "record_table": table,
        "graph_vectors": int(graph["count"]),
        "record_vectors": len(readable_in_population),
        # The census's definition, verbatim: an UNKEYED vector-bearing node can never be
        # matched to a row, so it is graph-only BY CONSTRUCTION and counted into the
        # total as well as named separately.
        "graph_only": len(graph_keys - readable) + int(graph["unkeyed"]),
        "unkeyed_graph_nodes": int(graph["unkeyed"]),
        "record_unreadable_vector": len(unreadable & graph_keys),
        #: The record being AHEAD: readable rows this graph holds no vector for. The
        #: census derives the same number from one aggregate row
        #: (`record_readable_total - |readable ∩ graph keys|`); this door already holds
        #: the whole table's readable set, so this set difference IS that figure.
        "record_only": len(readable - graph_keys),
        "keys": graph_keys,
        "record": record,
    }


#: The measurement fields a plan RECORDS. Only these reach the plan file: `keys` and
#: `record` are the raw sets the buckets are computed from, and a 46k-key list in the
#: artefact would make the thing a human is asked to approve unreadable.
def _census_row(measured: dict) -> dict:
    return {field: measured[field] for field in _CENSUS_FIELDS}


def _buckets(measured: dict) -> dict:
    """The per-label arithmetic, in ONE place, so plan and execute cannot disagree.

    ``measured`` is :func:`_measure_label`'s answer — the graph keys from the census's
    own leg, the record buckets from the door this tool writes through.
    """
    graph_keys = set(measured["keys"])
    record = measured["record"]
    record_rows = set(record["rows"])
    matched = graph_keys & record_rows
    return {
        "graph_keys": graph_keys,
        "matched": matched,
        # ⛔ COUNTED, NEVER DROPPED. A graph vector whose record row does not exist is
        # the one population this tool cannot repair (inventing a row would make a
        # record-sourced rebuild and a JSONL rebuild disagree), so it must be REPORTED
        # — a silent skip is how a copy reports success over a hole.
        "still_unmatched": graph_keys - record_rows,
        "fillable": matched & set(record["missing"]),
        "skipped_existing": matched - set(record["missing"]),
        "skipped_unreadable": (matched - set(record["missing"])) - set(record["readable"]),
        "unkeyed_graph_nodes": int(measured["unkeyed_graph_nodes"]),
    }


def build_plan(*, g: Any, door: RecordDoor, graph_name: str, graph_uri: str,
               labels: list[str] | None, batch: int) -> dict:
    """Read both stores, and produce the approvable artefact. Writes NOTHING."""
    selected = _population(labels)
    entries: list[tuple[str, str]] = []
    per_label: list[dict] = []
    census_rows: list[dict] = []
    for table, label, _key_prop_name in selected:
        measured = _measure_label(g, door, table, label)
        buckets = _buckets(measured)
        entries.extend((label, key) for key in buckets["matched"])
        census_rows.append(_census_row(measured))
        per_label.append({
            "label": label,
            "record_table": table,
            "graph_with_vector": measured["graph_vectors"],
            "record_with_vector": measured["record_vectors"],
            "copyable": len(buckets["matched"]),
            "fillable": len(buckets["fillable"]),
            "still_unmatched": len(buckets["still_unmatched"]),
            "skipped_existing": len(buckets["skipped_existing"]),
            "skipped_unreadable": len(buckets["skipped_unreadable"]),
            "unkeyed_nodes": buckets["unkeyed_graph_nodes"],
            "sample": sorted(buckets["fillable"])[:5],
        })
    total = len(entries)
    plan: dict = {
        "tool": PLAN_TOOL,
        "version": PLAN_VERSION,
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
        "graph": graph_name,
        # ⛔ THE ADDRESS IS STORED REDACTED, WITH ITS HASH (review finding 6). The raw
        # `--uri` carries the FalkorDB password, and this artefact is written to
        # `--plan-out`, handed to a human to approve, and kept until execution. The
        # hash keeps `execute`'s drift guard exactly as strong (it compares the live
        # URI's own hash), without the secret reaching the file or `--json`.
        "graph_uri_redacted": _redact_uri(graph_uri),
        "graph_uri_sha256": "sha256:" + hashlib.sha256(
            _uri_authority(graph_uri).encode()).hexdigest(),
        "org_id": door.org_id,
        "graph_id": door.graph_id,
        "batch": batch,
        "population": [label for _t, label, _k in selected],
        "census": census_rows,
        "labels": per_label,
        "total_copyable": total,
        "total_fillable": sum(row["fillable"] for row in per_label),
        "still_unmatched": sum(row["still_unmatched"] for row in per_label),
        "population_digest": digest(entries),
        "digest_scope": (
            "the approved token is sha256(POPULATION ⊕ CONTENT). POPULATION is one "
            "`label<TAB>key` line per COPYABLE node (graph vector + record row), "
            "sorted, sha256. CONTENT is a canonical JSON digest of this plan's "
            "target, census, per-label rows and population — so an edited "
            "plan is a DIFFERENT token, not the same one. `batch` is provenance "
            "only and is deliberately NOT sealed: `execute` takes the batch from "
            "the command line, so the value recorded here cannot change what is "
            "written."),
        "ready": bool(total),
    }
    # ⛔ DERIVED LAST, from the plan itself: one function computes the hash and one
    # sets the token, so the two can never disagree about what was hashed.
    plan["content_sha256"] = content_sha256(plan)
    plan["digest"] = approval_digest(plan["population_digest"],
                                     plan["content_sha256"])
    return plan


def render_plan(plan: dict) -> str:
    lines = [
        f"entity-vector-backfill PLAN — graph {plan['graph']!r} at {plan['at']}",
        f"source URI: {plan.get('graph_uri_redacted', '—')} "
        f"({plan.get('graph_uri_sha256', '')[:19]}…)",
        f"record tenant: org {plan['org_id']!r} / graph {plan['graph_id']!r}",
        "",
        f"{'label':10} {'graph':>8} {'record':>8} {'fillable':>9} "
        f"{'unmatched':>10} {'existing':>9} {'unread':>7}  note",
    ]
    for row in plan["labels"]:
        lines.append(
            f"{row['label']:10} {row['graph_with_vector']:>8} "
            f"{row['record_with_vector']:>8} {row['fillable']:>9} "
            f"{row['still_unmatched']:>10} {row['skipped_existing']:>9} "
            f"{row['skipped_unreadable']:>7}  "
            f"{'copyable' if row['copyable'] else 'nothing to copy'}")
    lines += [
        "",
        f"COPYABLE population: {plan['total_copyable']} node(s) — the set this plan "
        "authorises",
        f"of which still NULL in the record: {plan['total_fillable']} "
        f"(a re-run fills only the remainder)",
        f"graph-only and NOT copyable: {plan['still_unmatched']} unmatched "
        "(no record row exists — never invented by this tool)",
        f"DIGEST: {plan['digest']}",
        f"        population alone: {plan['population_digest']}",
        f"        content alone:    {plan['content_sha256']}",
        f"        ({plan['digest_scope']})",
        "",
        "This is a PREVIEW — nothing was written. To execute this exact population:",
        "  --execute --plan <this file> --confirm-plan-digest "
        f"{plan['digest']}",
    ]
    for row in plan["labels"]:
        if row["sample"]:
            lines.append(
                f"   {row['label']} fillable sample: {', '.join(row['sample'])}")
    if not plan["ready"]:
        lines += [
            "",
            "⛔ EMPTY POPULATION: no graph node of this population carries a vector "
            "that a record row could receive. Execution would REFUSE — pass "
            "--expect-empty only when that is the intended state (an empty graph, or "
            "a graph whose entity vectors were already stripped).",
        ]
    return "\n".join(lines)


# ── execution ───────────────────────────────────────────────────────────────


def _load_plan(path: str, confirmed: str) -> dict:
    try:
        with open(path) as handle:
            plan = json.load(handle)
    except Exception as exc:  # unreadable plan is a refusal
        raise Refusal(
            REFUSE_PLAN_UNREADABLE,
            f"{path!r} could not be read as a plan ({type(exc).__name__}: {exc}). "
            "Nothing was written.") from exc
    if not isinstance(plan, dict) or plan.get("tool") != PLAN_TOOL \
            or plan.get("version") != PLAN_VERSION:
        raise Refusal(
            REFUSE_PLAN_UNREADABLE,
            f"{path!r} is not a {PLAN_TOOL} v{PLAN_VERSION} plan (tool="
            f"{plan.get('tool') if isinstance(plan, dict) else type(plan).__name__!r}, "
            f"version={plan.get('version') if isinstance(plan, dict) else None!r}). "
            "Nothing was written.")
    missing = _missing_plan_fields(plan)
    if missing:
        # ⛔ A STRUCTURALLY INCOMPLETE PLAN IS A REFUSAL, not a KeyError from inside
        # `execute` (review finding 2, reproduced). The shape was never checked, so a
        # file carrying only `tool`/`version`/`digest` was ACCEPTED by this function
        # and then raised `KeyError: 'graph'` — which `main()` does not catch, so it
        # escaped as a traceback with exit 1 (the code this module reserves for USAGE)
        # and NO `--json` report, contradicting both the docstring's exit table and its
        # "`--json` on every exit path" promise.
        raise Refusal(
            REFUSE_PLAN_UNREADABLE,
            f"{path!r} is not a readable {PLAN_TOOL} plan: {missing}. A plan is the "
            "artefact an operator approves, so an incomplete one has no "
            "interpretation this tool may invent. Nothing was written.")
    recomputed = content_sha256(plan)
    if plan.get("content_sha256") != recomputed:
        raise Refusal(
            REFUSE_PLAN_TAMPERED,
            f"the plan's content does not hash to what it records: recorded "
            f"{plan.get('content_sha256')!r} vs recomputed {recomputed!r}. The file "
            "was edited after it was printed — a retargeted `record_table`, a blanked "
            "`census`, or any other change — so it is not the plan that was "
            "approved. Nothing was written.")
    recomputed_token = approval_digest(plan.get("population_digest") or "",
                                       plan["content_sha256"])
    if plan.get("digest") != recomputed_token:
        # ⛔ THE SECOND HALF, and it is not redundant: an editor can recompute
        # `content_sha256` after changing the file. What they cannot recompute is the
        # token inside `digest`, because its population half is derived from keys the
        # plan does not carry — so re-sealing the content hash leaves `digest` naming
        # something else, and the operator's `--confirm-plan-digest` no longer matches.
        raise Refusal(
            REFUSE_PLAN_TAMPERED,
            f"the plan's own digest is {plan.get('digest')!r} but its population ⊕ "
            f"content hashes to {recomputed_token!r}. Those are two halves of the "
            "approved token and they disagree, so the file is not the plan that was "
            "printed. Nothing was written.")
    if plan.get("digest") != confirmed:
        raise Refusal(
            REFUSE_DIGEST_MISMATCH,
            f"the plan's own digest is {plan.get('digest')!r} but "
            f"--confirm-plan-digest says {confirmed!r}. A plan edited after it was "
            "printed is not the plan that was approved. Nothing was written.")
    unexpected = _plan_label_drift(plan)
    if unexpected:
        raise Refusal(
            REFUSE_PLAN_UNREADABLE,
            f"{path!r} names a (label, record table) pair this tool does not copy: "
            f"{unexpected}. The `record_table` is DERIVED from the label, never "
            "taken from the file. Nothing was written.")
    return plan


#: The plan fields `--execute` reads, with the type it requires. One table, so the
#: validator and the readers cannot drift; a field added to `build_plan` without being
#: added here is simply not validated, and one added here without being built is a
#: refusal at load rather than a `KeyError` at write time.
_PLAN_SHAPE: tuple[tuple[str, type | tuple[type, ...]], ...] = (
    ("at", str),
    ("graph", str),
    ("graph_uri_redacted", str),
    ("graph_uri_sha256", str),
    ("org_id", str),
    ("graph_id", str),
    ("batch", int),
    ("population", list),
    ("census", list),
    ("labels", list),
    ("total_copyable", int),
    ("total_fillable", int),
    ("still_unmatched", int),
    ("population_digest", str),
    ("content_sha256", str),
    ("digest", str),
)


def _rows_of(plan: dict, field: str) -> list:
    """``plan[field]`` when it really is a list, else ``[]``.

    ⛔ GATING, NOT `or []` (review cycle 3, finding 1). `plan.get(field) or []` reads as a
    guard and is not one: `5 or []` evaluates to **5**, so a plan whose only defect is
    `"labels": 5` raised `TypeError: 'int' object is not iterable` INSIDE the validator —
    escaping `main()` (whose arms are `Refusal`/`CensusError`/`WriteFailed`) as a
    traceback, exit 1 and no `--json`, which is the exact class the validator exists to
    close. The type problem is already RECORDED by the `_PLAN_SHAPE` loop; this only has
    to avoid crashing on the value it just complained about.
    """
    value = plan.get(field)
    return value if isinstance(value, list) else []


def _missing_plan_fields(plan: dict) -> str:
    """A sentence naming every field the plan is missing or mistypes, or ``""``.

    Total by construction: it never raises on the value it is inspecting, because the
    value it is inspecting is the thing that might be wrong.
    """
    problems: list[str] = []
    for field, expected in _PLAN_SHAPE:
        if field not in plan:
            problems.append(f"{field} is missing")
        elif not isinstance(plan[field], expected):
            problems.append(
                f"{field} is {type(plan[field]).__name__}")
    for row in _rows_of(plan, "labels"):
        if not isinstance(row, dict):
            problems.append("labels carries a non-object row")
            continue
        for field in ("label", "record_table"):
            if not isinstance(row.get(field), str) or not row.get(field):
                problems.append(f"a labels row has no {field}")
    for row in _rows_of(plan, "census"):
        if not isinstance(row, dict):
            problems.append("census carries a non-object row")
            continue
        for field in _CENSUS_FIELDS:
            if field not in row:
                problems.append(f"a census row has no {field}")
        # ⛔ PRESENCE IS NOT SHAPE (review cycle 2, finding 2). Validating only that the
        # key exists let `{"graph_vectors": None}` through and then raise `TypeError`
        # from `int(None)` inside `_assert_census_matches` — which escapes `main()` (its
        # arms are `Refusal`/`CensusError`/`WriteFailed`) as a traceback, exit 1 and no
        # `--json`, the exact class this validator was added to close.
        if not isinstance(row.get("label"), str) or not row.get("label"):
            problems.append("a census row has no string label")
        if not isinstance(row.get("record_table"), str) or not row.get("record_table"):
            problems.append("a census row has no string record_table")
        for field in _CENSUS_COUNT_FIELDS:
            if field in row and not isinstance(row[field], int):
                problems.append(f"a census row's {field} is not an integer")
    return "; ".join(dict.fromkeys(problems))


def _plan_label_drift(plan: dict) -> str:
    """The (label, table) pairs in the plan that are not a `BACKFILLED` pair, or ``""``.

    ⛔ THE DERIVATION IS THE GUARD. `execute` resolves the table from the LABEL
    (:func:`_table_for`) and refuses when the plan's recorded table disagrees, so the
    retarget attack of review finding 1 has no path left even on a plan whose hashes
    were re-sealed by hand.
    """
    bad: list[str] = []
    for row in _rows_of(plan, "labels"):
        if not isinstance(row, dict):
            bad.append("labels carries a non-object row")
            continue
        label, table = row.get("label"), row.get("record_table")
        if label not in {name for _t, name, _k in BACKFILLED}:
            bad.append(f"{label!r} is not a copied label")
            continue
        if table != _table_for(label):
            bad.append(f"{label} -> {table!r} (the table is {_table_for(label)!r})")
    return "; ".join(bad)


def _require_tokens(args: Any) -> dict:
    """The two token preconditions, checked BEFORE any store is opened.

    ⛔ ORDER IS LOAD-BEARING. The authority to write is (a) a reviewed plan and (b) its
    digest, and neither needs a connection to check. Reading the stores first would
    make a token-less run pay for two store binds and could fail on an UNRELATED
    refusal (an unresolvable graph, an unreadable record), so an operator who forgot
    the digest would be told about a store instead of about the missing token.
    """
    if not args.plan:
        raise Refusal(
            REFUSE_NO_PLAN,
            "--execute without --plan. There is no 'use the live census instead': the "
            "authority to write is a reviewed artefact. Nothing was written.")
    if not args.confirm_plan_digest:
        raise Refusal(
            REFUSE_NO_DIGEST,
            "--plan without --confirm-plan-digest. The digest IS the token; a plan "
            "file alone is a proposal, and copying one is trivial. Nothing was "
            "written.")
    return _load_plan(args.plan, args.confirm_plan_digest)


#: The census fields the re-census re-asserts — GRAPH-SIDE ONLY, and the exclusion is
#: the point rather than an oversight.
#:
#: `record_vectors` and `graph_only` move BY DESIGN as this tool fills rows, so
#: re-asserting them would make the tool refuse its own progress — i.e. it could never
#: resume a run it had half-completed, which is the one property a batched backfill
#: needs. What must NOT move is the population the vectors are read FROM: a graph that
#: gained or lost a vector-bearing node, or a node whose identity key disappeared,
#: changes what the approved plan is a plan over. The record side is covered from the
#: other direction by the digest (a row appearing or vanishing moves the copyable set).
_RE_CENSUS_FIELDS = ("graph_vectors", "unkeyed_graph_nodes")


def _assert_census_matches(plan: dict, live: list[dict]) -> None:
    """The plan's recorded GRAPH-SIDE census must still be the live one.

    ``live`` is the re-measured per-label rows (:func:`_measure_label`), NOT raw graph
    keys: this is the "live re-census" half of the precondition, and it is not
    redundant with the digest. The digest covers the COPYABLE population — nodes that
    have BOTH a graph vector and a record row. A vector-bearing node with NO identity
    key (`unkeyed`) is in neither side of that intersection, so a plan could
    digest-match while the population it was justified by grew a node this tool can
    never help. Re-measuring is what notices, and what makes the plan's recorded
    graph-side numbers an assertion instead of a memory.
    """
    recorded = {}
    for row in _rows_of(plan, "census"):
        # A non-string `label` is UNHASHABLE (`{"Object": …}["Object"]` is fine, a list is
        # not), and a non-numeric count makes `int()` raise — neither may escape this
        # function as a `TypeError` (review cycle 2, finding 2). A row this tool cannot
        # interpret is a DELTA, which is a `REFUSE_PLAN_STALE` refusal, not a traceback.
        label = row.get("label") if isinstance(row, dict) else None
        if isinstance(label, str):
            recorded[label] = row
    deltas: list[str] = []
    for row in live:
        old = recorded.get(row["label"])
        if old is None:
            deltas.append(f"{row['label']}: absent from the plan")
            continue
        for field in _RE_CENSUS_FIELDS:
            try:
                same = int(old.get(field, -1)) == int(row[field])
            except (TypeError, ValueError):
                # A recorded value this tool cannot read as a count is NOT a match.
                same = False
            if not same:
                deltas.append(
                    f"{row['label']}.{field}: plan {old.get(field)!r} → live {row[field]!r}")
    if deltas:
        raise Refusal(
            REFUSE_PLAN_STALE,
            "the live census does not match the census this plan was approved "
            f"against, on the graph side the plan copies FROM ({'; '.join(deltas)}). "
            "Re-plan and re-approve. Nothing was written.")


def execute(*, g: Any, plan: dict, door: RecordDoor, graph_name: str,
            graph_uri: str, confirmed: str, batch: int, expect_empty: bool) -> dict:
    """Every precondition, then the batched, null-only copy. Refuses BEFORE any write.

    ⛔ THE TOKEN IS RE-VERIFIED HERE, not only in `_load_plan` (review cycle 2, finding 1).
    `main()` loads the plan through `_require_tokens`, so the checks were redundant FOR
    THAT CALLER — but this function is public to the test suite and to any future caller,
    and the docstring above promises preconditions, not "preconditions if you came through
    `main`". Before v2 this function compared the live population to `confirmed` and could
    not write on a bad token; after v2 (whose token covers the content, so it cannot be
    compared to a population) that comparison had to move, and moving it must not leave
    this entry point able to write a plan nobody sealed — and to mint a report naming the
    digest as the authority.
    """
    if plan.get("content_sha256") != content_sha256(plan):
        raise Refusal(
            REFUSE_PLAN_TAMPERED,
            "the plan's content does not hash to what it records, so it is not the plan "
            "that was approved. Nothing was written.")
    if plan.get("digest") != approval_digest(plan.get("population_digest") or "",
                                             plan["content_sha256"]):
        raise Refusal(
            REFUSE_PLAN_TAMPERED,
            "the plan's own digest is not its population ⊕ content, so the file is not "
            "the artefact that was printed. Nothing was written.")
    if plan.get("digest") != confirmed:
        raise Refusal(
            REFUSE_DIGEST_MISMATCH,
            f"the plan's own digest is {plan.get('digest')!r} but the token this run was "
            f"given is {confirmed!r}. Nothing was written.")
    if plan["graph"] != graph_name:
        raise Refusal(
            REFUSE_TARGET_DRIFT,
            f"the plan was built against graph {plan['graph']!r} but this run resolves "
            f"{graph_name!r}. A plan is an authority over ONE target. Nothing was "
            "written.")

    # ⛔ THE RECORD TENANT AND THE GRAPH URI ARE PART OF THE TARGET (review F1).
    # `plan["org_id"]`, `plan["graph_id"]` and `plan["graph_uri"]` are recorded by
    # `build_plan` and PRINTED as the approved artefact's own `record tenant:` line —
    # and before this check nothing read them back. The digest cannot cover them: it is
    # over `label<TAB>key` lines only, so it encodes the POPULATION and never the
    # destination, and `_assert_census_matches` re-measures the GRAPH side. Two tenants
    # holding the same population (a clone, a restore, a second org's graph built from
    # the same journal) therefore produce the SAME digest, and the null-only write would
    # land in the wrong tenant's rows. RLS bounds the damage to the tenant named on the
    # command line — which is exactly why nothing else noticed.
    plan_tenant = (plan.get("org_id"), plan.get("graph_id"))
    live_tenant = (door.org_id, door.graph_id)
    if plan_tenant != live_tenant:
        raise Refusal(
            REFUSE_TARGET_DRIFT,
            f"the plan was built for record tenant org {plan_tenant[0]!r} / graph "
            f"{plan_tenant[1]!r} but this run resolves org {live_tenant[0]!r} / graph "
            f"{live_tenant[1]!r}. A plan is an authority over ONE target, and the "
            "population digest cannot tell two tenants apart — it covers which rows, "
            "never whose. Nothing was written.")
    if plan.get("graph_uri_sha256") != (
            "sha256:" + hashlib.sha256(_uri_authority(graph_uri).encode()).hexdigest()):
        # ⛔ THE HASH IS COMPARED, not the address (review finding 6): the raw URI
        # carries a credential, so the plan stores its digest plus a redacted copy for
        # a human to read. Same strength as comparing the strings, no secret on disk.
        raise Refusal(
            REFUSE_TARGET_DRIFT,
            "the plan was built against a different graph address (its "
            f"`graph_uri_sha256` is {plan.get('graph_uri_sha256')!r}, recorded for "
            f"{plan.get('graph_uri_redacted')!r}) than the one this run resolves "
            f"({_redact_uri(graph_uri)!r}, "
            f"sha256:{hashlib.sha256(_uri_authority(graph_uri).encode()).hexdigest()[:16]}…). A plan "
            "is an authority over ONE target; a replica or a stale address is not "
            "the graph the plan measured. Nothing was written.")

    # ── the live re-census and the exact-population precondition ─────────────
    # ⛔ THE TABLE IS DERIVED FROM THE LABEL (review finding 1, reproduced). It used to
    # be read out of the plan file, so editing `labels[i]["record_table"]` on an
    # otherwise untouched plan made this tool write an Object's vector onto a Point's
    # record row — and the null-only guard does not help, because the WRONG row's
    # `embedding` is NULL too, which is exactly what the guard looks for. A plan whose
    # recorded table disagrees with the derivation is not a plan this tool executes.
    selected: list[tuple[str, str]] = []
    for row in plan["labels"]:
        label = row["label"]
        derived = _table_for(label)
        if row.get("record_table") != derived:
            raise Refusal(
                REFUSE_PLAN_TAMPERED,
                f"the plan sends {label!r} to `public.{row.get('record_table')}` but "
                f"this tool copies {label!r} into `public.{derived}` only. The table "
                "is DERIVED from the label, never taken from the file, so a plan "
                "naming another destination is not the plan that was approved. "
                "Nothing was written.")
        selected.append((derived, label))
    measured = {label: _measure_label(g, door, table, label)
                for table, label in selected}
    _assert_census_matches(plan, list(measured.values()))

    entries: list[tuple[str, str]] = []
    buckets: dict[str, dict] = {}
    for row in plan["labels"]:
        label = row["label"]
        buckets[label] = _buckets(measured[label])
        entries.extend((label, key) for key in buckets[label]["matched"])
    live_digest = digest(entries)
    # ⛔ THE LIVE POPULATION IS COMPARED TO THE PLAN'S OWN POPULATION DIGEST, not to
    # `confirmed`: since v2 the operator's token is sha256(POPULATION ⊕ CONTENT), and
    # `_load_plan` has already proved the plan's `digest` IS that token. Comparing the
    # live set to the TOKEN would refuse every correct plan.
    if live_digest != plan["population_digest"]:
        per_label = ", ".join(
            f"{row['label']}={len(buckets[row['label']]['matched'])}"
            for row in plan["labels"])
        raise Refusal(
            REFUSE_PLAN_STALE,
            "the live copyable population does not match the approved plan. plan "
            f"{plan['population_digest']!r} vs live {live_digest!r} (live per label: "
            f"{per_label}). One node more, one node less, or any key that moved, "
            "refuses — re-plan and re-approve. Nothing was written.")

    if not entries:
        if not expect_empty:
            raise Refusal(
                REFUSE_NOTHING_TO_FILL,
                "the approved plan contains no copyable node. Executing it would "
                "report a backfill it never performed — pass --expect-empty only when "
                "an empty population is the intended state. Nothing was written.")
        return {"at": datetime.now(UTC).isoformat(timespec="seconds"),
                "graph": graph_name, "plan_digest": confirmed, "labels": [],
                "filled": 0, "copyable": 0, "empty": True}

    # ── the copy: batched, null-only, committed per batch ───────────────────
    filled: dict[str, int] = {}
    shortfall: dict[str, int] = {}
    uncopyable: dict[str, int] = {}
    vanished: dict[str, int] = {}
    try:
        for row in plan["labels"]:
            label = row["label"]
            # DERIVED, not read from the plan (see the derivation above).
            table = _table_for(label)
            filled[label] = 0
            shortfall[label] = 0
            uncopyable[label] = 0
            vanished[label] = 0
            keys = sorted(buckets[label]["fillable"])
            for start in range(0, len(keys), batch):
                chunk = keys[start:start + batch]
                if not chunk:
                    continue
                vectors = read_graph_vectors(g, label, chunk)
                pairs: list[tuple[str, list[float]]] = []
                for key in chunk:
                    if key not in vectors:
                        # The vector went away between the census and this read. It is
                        # NOT an error (nothing was lost that this tool owns) and it is
                        # NOT a silent skip either.
                        vanished[label] += 1
                        continue
                    vector = vectors[key]
                    if vector is None or len(vector) != _RECORD_WIDTH:
                        # The record's converter would NULL the generated column for
                        # this payload, i.e. the write would change nothing a dense
                        # query can read. Counted, never performed.
                        uncopyable[label] += 1
                        continue
                    pairs.append((key, vector))
                written = door.write(table, pairs)
                filled[label] += written
                if written < len(pairs):
                    # A row was filled by a concurrent writer between our read and our
                    # statement, so the null-only guard refused it. Reported, never
                    # absorbed: a shortfall that vanished is how a partial run reads as
                    # a complete one.
                    shortfall[label] += len(pairs) - written
    except CensusError as exc:
        partial = {
            "at": datetime.now(UTC).isoformat(timespec="seconds"),
            "graph": graph_name, "plan_digest": confirmed,
            "filled": sum(filled.values()),
            "filled_by_label": dict(filled),
            "shortfall_by_label": dict(shortfall),
            "uncopyable_by_label": dict(uncopyable),
            "vanished_by_label": dict(vanished),
            "complete": False,
        }
        raise WriteFailed(
            f"{exc} — {sum(filled.values())} row(s) are already durable (committed per "
            "batch). The plan is still the right authority: re-execute it to fill only "
            "the remainder.", partial) from exc

    # ── the post-run verdict: did the copy actually close the gap? ──────────
    labels = [row["label"] for row in plan["labels"]]
    try:
        after = [_measure_label(g, door, table, label) for table, label in selected]
        census_after = [_census_row(row) for row in after]
        census_error = None
    except CensusError as exc:
        after, census_after, census_error = [], [], str(exc)

    residual = sum(int(row["graph_only"]) for row in census_after)
    by_label = {
        row["label"]: {
            "graph_only_before": next(
                (r["graph_only"] for r in plan["census"] if r["label"] == row["label"]),
                None),
            "graph_only_after": row["graph_only"],
            "record_vectors_after": row["record_vectors"],
            "at_parity": row["graph_only"] == 0,
        }
        for row in census_after
    }
    return {
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
        "graph": graph_name,
        "plan_digest": confirmed,
        "labels": labels,
        "copyable": len(entries),
        "planned_fillable": sum(len(buckets[r["label"]]["fillable"])
                                for r in plan["labels"]),
        "filled": sum(filled.values()),
        "filled_by_label": filled,
        "shortfall_by_label": shortfall,
        "uncopyable_by_label": uncopyable,
        "vanished_by_label": vanished,
        "still_unmatched_by_label": {
            r["label"]: len(buckets[r["label"]]["still_unmatched"])
            for r in plan["labels"]},
        "skipped_existing_by_label": {
            r["label"]: len(buckets[r["label"]]["skipped_existing"])
            for r in plan["labels"]},
        "skipped_unreadable_by_label": {
            r["label"]: len(buckets[r["label"]]["skipped_unreadable"])
            for r in plan["labels"]},
        "census_after": census_after,
        "parity_by_label": by_label,
        "residual_graph_only": residual,
        "at_parity_after": (None if census_error
                            else all(row["graph_only"] == 0 for row in census_after)),
        "census_error": census_error,
        "empty": False,
    }


def _write_report(path: str | None, payload: dict) -> None:
    """Write the machine-readable report a caller asked for with ``--json``.

    ⛔ ON EVERY EXIT `main()` TAKES, not only the happy one. A caller that passed
    `--json` used to get NO file on exactly the runs it needs a report for — a refusal, a
    failed batch — because the write sat after the success return. Best-effort by design:
    the exit code and stderr are the verdict, so a write failure here must never mask
    them.

    ⛔ THE SCOPE IS `main()`'s EXITS, and the qualifier is load-bearing: argparse's own
    failures (`parse_args` → `SystemExit(2)`) happen before an `args` object exists, so
    they are NOT reachable from here and no report is owed for them (#review F2).
    """
    if not path:
        return
    with contextlib.suppress(OSError):
        Path(path).write_text(json.dumps(payload, indent=2) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--uri", help="FalkorDB URI of the source graph")
    src.add_argument("--db-path", help="embedded graph path instead of a URI")
    parser.add_argument("--graph-name", default=None,
                        help="the graph NAME to read (a target selector; it never "
                             "scopes the record read — that is --org-id/--graph-id)")
    parser.add_argument("--record-dsn", default=None,
                        help="hosted record DSN (default: TORTOISE_RECORD_DSN)")
    parser.add_argument("--org-id", default=os.environ.get("TORTOISE_RECORD_ORG_ID", ""),
                        help="record tenant org")
    parser.add_argument("--graph-id", default=os.environ.get("TORTOISE_RECORD_GRAPH_ID", ""),
                        help="record tenant graph")
    parser.add_argument("--label", action="append", default=None,
                        choices=[label for _, label, _ in BACKFILLED],
                        help="PREVIEW-ONLY: plan only this label (repeatable); default: "
                             "all five (:Point included — see the module docstring). "
                             "`--execute` copies the labels the APPROVED PLAN carries, "
                             "so this flag is ignored there by design")
    parser.add_argument("--batch", type=int, default=DEFAULT_BATCH,
                        help=f"rows per write batch (default {DEFAULT_BATCH}, "
                             f"max {MAX_BATCH})")
    parser.add_argument("--plan-out", default=None,
                        help="write the preview plan to this path "
                             "(required to execute it later)")
    parser.add_argument("--json", default=None, help="write the run's report here")
    parser.add_argument("--execute", action="store_true",
                        help="copy the plan's exact population into the record; the "
                             "default is a preview")
    parser.add_argument("--plan", default=None,
                        help="the approved plan file (required with --execute)")
    parser.add_argument("--confirm-plan-digest", default=None,
                        help="the plan's `sha256:` digest, i.e. the token authorising "
                             "this exact population (required with --execute)")
    parser.add_argument("--expect-empty", action="store_true",
                        help="allow an EMPTY population to execute as a no-op")
    args = parser.parse_args(argv)

    empty_flag = census_mod.empty_graph_target_flag(args)
    if empty_flag:
        sys.stderr.write(f"entity_vector_backfill: {empty_flag}\n")
        _write_report(args.json, {"outcome": "usage", "error": "EMPTY_GRAPH_TARGET",
                                  "message": empty_flag, "wrote": False})
        return 1
    if args.batch <= 0:
        # ⛔ Exit 1 through the REPORT, not `parser.error`: argparse exits BEFORE a
        # `--json` path has been consumed, so a caller that asked for a machine-readable
        # report got nothing on this run too. `1` is the docstring's USAGE code; the only
        # exits that now write no report are argparse's OWN pre-`main` failures (an
        # unknown flag, a missing required one), which happen before `args.json` exists.
        _message = (
            "entity_vector_backfill: --batch must be > 0 — a batch of zero writes "
            "nothing while reporting a finished run. Nothing was written.")
        sys.stderr.write(_message + "\n")
        _write_report(args.json, {"outcome": "usage", "error": "BATCH_INVALID",
                                  "message": _message, "wrote": False})
        return 1
    if args.batch > MAX_BATCH:
        # A batch's graph read is one `IN $keys` statement, and FalkorDB truncates
        # SILENTLY past RESULTSET_SIZE — a truncated batch would look like "these
        # nodes have no vector" and be counted as absent rather than copied.
        _message = (
            f"entity_vector_backfill: --batch {args.batch} exceeds the {MAX_BATCH}-row "
            "bound (one batch is one `IN $keys` graph read, which the engine truncates "
            "silently past RESULTSET_SIZE). Nothing was written.")
        sys.stderr.write(_message + "\n")
        _write_report(args.json, {"outcome": "usage", "error": "BATCH_TOO_LARGE",
                                  "message": _message, "wrote": False})
        return 1
    record_dsn = args.record_dsn or os.environ.get("TORTOISE_RECORD_DSN", "")
    if not record_dsn:
        _message = (
            "entity_vector_backfill: no record leg — pass --record-dsn or set "
            "TORTOISE_RECORD_DSN. The copy has no destination without it, so nothing "
            "was read and nothing was written.")
        sys.stderr.write(_message + "\n")
        _write_report(args.json, {"outcome": "failed", "error": "NO_RECORD_LEG",
                                  "message": _message, "wrote": False})
        return 3
    if not args.org_id or not args.graph_id:
        _message = (
            "entity_vector_backfill: the record tenant is unnamed — pass --org-id and "
            "--graph-id (or TORTOISE_RECORD_ORG_ID / TORTOISE_RECORD_GRAPH_ID). An "
            "untenanted record read returns ZERO rows under RLS, which would report "
            "every graph node as unmatched (#10024). Nothing was written.")
        sys.stderr.write(_message + "\n")
        _write_report(args.json, {"outcome": "failed", "error": "NO_RECORD_TENANT",
                                  "message": _message, "wrote": False})
        return 3

    # ── the two token refusals come FIRST: neither needs a store ─────────────
    plan: dict | None = None
    if args.execute:
        try:
            plan = _require_tokens(args)
        except Refusal as exc:
            sys.stderr.write(f"entity_vector_backfill: {exc.token} — {exc}\n")
            _write_report(args.json, {"outcome": "refused", "refusal": exc.token,
                                      "message": str(exc), "wrote": False})
            return 2

    from tortoise.projection import FalkorProjection

    try:
        graph_name = census_mod.resolve_graph_name(
            args.uri, args.db_path, args.graph_name)
        if args.uri:
            proj = FalkorProjection.from_uri(
                args.uri, graph_name=graph_name,
                skip_health_check=True, ensure_indexes=False)
        else:
            proj = FalkorProjection(
                args.db_path, graph_name=graph_name,
                skip_health_check=True, ensure_indexes=False)
        census_mod._refuse_unless_graph_present(proj, graph_name)
    except CensusError as exc:
        sys.stderr.write(f"entity_vector_backfill: REFUSED — {exc}\n")
        _write_report(args.json, {"outcome": "failed", "error": "CENSUS_ERROR",
                                  "message": str(exc), "wrote": False})
        return 3
    except Exception as exc:
        _message = (
            f"entity_vector_backfill: REFUSED — the target could not be resolved "
            f"({type(exc).__name__}: {exc}). Nothing was written.")
        sys.stderr.write(_message + "\n")
        _write_report(args.json, {"outcome": "failed", "error": "TARGET_UNRESOLVED",
                                  "message": _message, "wrote": False})
        return 3

    door = RecordDoor(record_dsn, args.org_id, args.graph_id)
    try:
        if not args.execute:
            try:
                plan = build_plan(
                    g=proj.g, door=door, graph_name=graph_name,
                    graph_uri=args.uri or args.db_path or "", labels=args.label,
                    batch=args.batch)
            except CensusError as exc:
                _message = (
                    f"entity_vector_backfill: REFUSED — {exc}. A census that cannot "
                    "be run is a refusal, not a pass, and nothing was written.")
                sys.stderr.write(_message + "\n")
                _write_report(args.json, {"outcome": "failed", "error": "CENSUS_ERROR",
                                          "message": _message, "wrote": False})
                return 3
            print(render_plan(plan))
            if args.plan_out:
                Path(args.plan_out).write_text(json.dumps(plan, indent=2) + "\n")
                print(f"\nplan written to {args.plan_out}")
            _write_report(args.json, {**plan, "outcome": "preview"})
            return 0

        # ⛔ NO SECOND LOAD. `_require_tokens` already read and validated the plan
        # above (before any store was opened); re-reading it here would be a TOCTOU
        # window in which the file could be swapped between the token check and the
        # execution it authorises.
        try:
            result = execute(
                g=proj.g, plan=plan, door=door, graph_name=graph_name,
                graph_uri=args.uri or args.db_path or "",
                confirmed=args.confirm_plan_digest, batch=args.batch,
                expect_empty=args.expect_empty)
        except Refusal as exc:
            sys.stderr.write(f"entity_vector_backfill: {exc.token} — {exc}\n")
            _write_report(args.json, {"outcome": "refused", "refusal": exc.token,
                                      "message": str(exc), "wrote": False})
            return 2
        except CensusError as exc:
            sys.stderr.write(f"entity_vector_backfill: REFUSED — {exc}\n")
            _write_report(args.json, {"outcome": "failed", "error": "CENSUS_ERROR",
                                      "message": str(exc), "wrote": False})
            return 3
        except WriteFailed as exc:
            # ⛔ NOT a Refusal (some batches are DURABLE) and NOT a CensusError subclass,
            # so without this arm it escaped `main()` as a traceback and exit 1 — which
            # the docstring defines as USAGE error, on a half-finished data migration.
            # `partial` is the one datum this class exists to carry (per-label counts,
            # `complete: False`), so it goes to stderr AND to `--json`.
            sys.stderr.write(
                f"entity_vector_backfill: {exc}\n"
                "entity_vector_backfill: durable before the failure (per label): "
                f"{json.dumps(exc.partial, sort_keys=True)}\n")
            _write_report(args.json, {**exc.partial, "outcome": "write-failed"})
            return 3
    finally:
        door.close()

    print(json.dumps(result, indent=2))
    # ⛔ ONE `outcome` VALUE PER EXIT, so a caller can branch on it uniformly instead of
    # inferring the run's state from WHICH keys happen to be present (review F2: the
    # success payload used to carry no `outcome` at all while every failure payload did).
    if result.get("empty"):
        _outcome = "empty"
    elif result.get("census_error"):
        _outcome = "unverified"
    elif result["residual_graph_only"]:
        _outcome = "residual"
    else:
        _outcome = "complete"
    _write_report(args.json, {**result, "outcome": _outcome})
    if result.get("empty"):
        return 0
    if result.get("census_error"):
        sys.stderr.write(
            "entity_vector_backfill: the post-run census could not be taken "
            f"({result['census_error']}) — the copy is durable but UNVERIFIED. Re-run "
            "tools/entity_vector_parity_census.py before treating any label as at "
            "parity.\n")
        return 3
    if result["residual_graph_only"]:
        sys.stderr.write(
            f"entity_vector_backfill: {result['residual_graph_only']} graph-only "
            "vector(s) REMAIN — a node with no record row (never invented by this "
            "tool), a record payload the converter cannot read (repair is a "
            "re-derivation, not a copy), or a graph vector that is not a numeric "
            f"{_RECORD_WIDTH}-long array. The strip is still BLOCKED for those "
            "labels; see the per-label `still_unmatched_by_label` / "
            "`skipped_unreadable_by_label` / `uncopyable_by_label`, and confirm with "
            "`uv run python tools/entity_vector_parity_census.py` (it measures "
            "Object/Subject/Event/Source — `:Point` is reported here, not there, "
            "because the census's population is the population the STRIP removes).\n")
        return 4
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
