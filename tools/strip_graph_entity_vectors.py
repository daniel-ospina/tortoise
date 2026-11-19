#!/usr/bin/env python3
"""#13629 step 3 — strip `embedding` from `:Object`/`:Subject`/`:Event`/`:Source` nodes.

**A PREVIEW IS THE DEFAULT. There is no `--dry-run`: the ABSENCE of `--execute` is
the preview.** The preview reads both stores, prints the exact population it would
touch (per label: how many nodes, and a canonical digest of the whole set), and names
every precondition that would refuse — so the plan a human approves and the plan the
execution re-verifies are the same artefact.

WHY THIS IS NOT A SECOND CENSUS
===============================
`tools/entity_vector_parity_census.py` already owns the two store legs, the keyset
paging guard against FalkorDB's silent `RESULTSET_SIZE` truncation, the tenant-scoped
record read, and the graph ⊆ record parity definition. This module IMPORTS all of it —
`read_graph_vector_keys`, `read_record_vector_keys`, the `RETIRED` population. What it
adds is the layer the census deliberately does not have:

    census   → "does the record hold a readable copy of every graph vector?"
    this tool → "which exact nodes may be stripped, and what token authorises
                 stripping exactly those?"

so the parity rule has ONE implementation, and the stripper inherits every refusal the
census already makes.

THE PRECONDITIONS, EACH ASSERTED AT RUNTIME AND EACH ITS OWN TOKEN
=================================================================
`--execute` refuses before the first write unless every one of these holds at the moment
it runs. ⛔ ONE exception, and it is named rather than hidden: a removal statement that
raises MID-RUN cannot be refused — earlier batches are already stripped. That state is
``REFUSE_WRITE_FAILED`` / exit 6, never the generic refusal, because exit 2 promises
"nothing was written".

``REFUSE_NO_PLAN``
    `--execute` without `--plan`. There is no "use the live census instead": the
    authority to delete is a reviewed artefact, not a race with a table.
``REFUSE_NO_DIGEST``
    `--plan` without `--confirm-plan-digest`. The digest is the token; a plan file is
    not an approval, and copying one is trivial.
``REFUSE_PLAN_UNREADABLE`` / ``REFUSE_DIGEST_MISMATCH``
    The plan cannot be parsed, was written by another tool, or its OWN recorded digest
    disagrees with `--confirm-plan-digest` — i.e. it was edited after it was printed.
``REFUSE_PLAN_STALE``
    The live census does not yield the plan's digest. One node more, one node less, or
    any key that moved refuses and names the per-label delta. This is the exact-set
    precondition, and it is what makes the plan a TOKEN rather than a suggestion.
``REFUSE_GRAPH_ONLY``
    Any label is short of parity (a graph vector the record cannot answer with). That
    vector is the only readable copy of the fingerprint; the answer is `#13430`'s
    backfill repair, never a delete.
``REFUSE_WRITE_STILL_EMITS``
    The graph fold still derives and writes this label's vector on every write. This is
    NOT ceremony: `_upsert_object`/`_upsert_subject` write `embedding` on `ON MATCH`
    too, so stripping a node before the write half stops means the next mention of that
    node RE-ADDS the vector — the strip would be undone, and the operator would have no
    way to tell a completed retirement from a racing writer.

    ⛔ AND ONE PR IS NOT ENOUGH TO SATISFY IT, which this file previously got wrong. `#13610`
    option 2 (PR #13617) gates the `object`/`subject` FOLDS; the same properties are also
    stored by `tortoise.sdk::_update_entity` (a caller-supplied `embedding` rides
    `SET n += $props` on Subject/Object/Source/Event), by
    `tortoise.hosted_backup::restore_graph` (all five labels) and, for `:Event`, by five
    more `sdk`/`session_indexer` writers. Every one of those re-adds the vector on the next
    call, so the requirement this gate enforces is **every declared writer of the label
    asks the gate** — not "the fold does". The gate therefore refuses all four labels
    today, INCLUDING after #13617 lands, and it names each un-cleared writer. That is the
    true state of a two-pronged retirement, and it is a satisfiable one: as each
    writer's `stores` entry loses the label, the gate opens for it. Testing that the gate
    CAN open (rather than only that it refuses) is
    `test_the_gate_OPENS_when_every_declared_writer_asks` — an enforcer whose firing
    condition can never hold would be the defect, not the refusal.
``REFUSE_TARGET_DRIFT``
    The target this run resolved is not the one the plan was built against — compared on
    all four axes that name a store: the graph NAME, `org_id`, `graph_id` and the graph
    URI's credential-stripped fingerprint. The tenant flags default from the environment
    and the graph name is usually `tortoise`, so a narrower check lets a plan previewed on
    one deployment execute against another's record.
``REFUSE_NOTHING_TO_STRIP``
    The plan is empty. An empty plan executed to a vacuous "success" is the shape that
    makes a re-run report a finished retirement it never performed; pass
    `--expect-empty` when an empty population is the intended state. Derived from the
    plan's ENTRY list, never from its `total_nodes` count — a count is a field a hand can
    edit into a plan whose set is empty.
``REFUSE_WRITE_ERROR``
    A removal statement raised on the FIRST batch, so **no node was stripped**. Exit is
    ``2`` like every other pre-write refusal: the state is exactly "nothing was written,
    safe to retry once the store error is understood", which is what a wedge, a timeout or
    an oversized ``--batch`` actually produces.
``REFUSE_WRITE_FAILED``
    A removal statement raised AFTER at least one batch had already been stripped. Exit is
    ``6``, not ``2``: the graph is PART-STRIPPED, and the only correct next action is to
    re-plan. Reusing the generic code would tell a caller "nothing happened, safe to
    retry" about the one state where retrying the same plan is wrong. ⛔ The COUNT decides
    which token is raised — a zero-stripped failure that reported "part-stripped" would
    teach an operator to distrust exit 6 on the one occasion it matters.

THE REMOVAL ITSELF
==================
Per label, batched (`--batch`, default 200), and IDEMPOTENT BY CONSTRUCTION:

    MATCH (n:Label) WHERE n.embedding IS NOT NULL AND n.<key> IN $keys
    REMOVE n.embedding RETURN count(n)

`REMOVE` of an absent property is a no-op, so a node already stripped costs nothing and
raises nothing; the per-batch removed count is compared against the batch size and a
shortfall is REPORTED, never silently accepted.

⛔ **The removal is NOT journaled, and that is correct rather than an omission.** The
value being removed is a DERIVED one — `derived_embedding.encode(name)` for the four
entity folds (`tortoise/projection/entities.py`), re-derivable from the row's own
`name` at a cost of cents (the packet's §2 on #11395: *"an embedding is derivable from
its text — a re-embed cost, not a data loss"*). A journal record would be a barrier to
that repair, not a protection of it. Compare `tools/purge_legacy_turns.py`, where the
journal IS mandatory: those rows are the transcript, and a rebuild that resurrects them
is the failure. Here a rebuild that re-derives a vector is the SEQUENCED re-write, and
the write gate above is what keeps it from happening by accident.

⚠️ **The graph-side `:Point` vector is NOT in scope** — the armed cutover and `#13330`
own it, and `#13629`'s brief forbids touching it. The population is
`entity_vector_parity_census.RETIRED` and nothing else.

Usage::

    # 1. preview (default) — read both stores, print the plan, name the refusals
    uv run python tools/strip_graph_entity_vectors.py \\
        --uri 'docker://:pw@host:6379/<graph>' --graph-name <graph> \\
        --record-dsn 'postgresql://tortoise_record_app:...@host/db' \\
        --org-id <org> --graph-id <graph> --plan-out plan.json

    # 2. execute the plan a human approved, by its digest
    uv run python tools/strip_graph_entity_vectors.py \\
        --uri 'docker://:pw@host:6379/<graph>' --graph-name <graph> \\
        --record-dsn 'postgresql://tortoise_record_app:...@host/db' \\
        --org-id <org> --graph-id <graph> \\
        --execute --plan plan.json --confirm-plan-digest sha256:<...>

Exit status: ``0`` preview printed, or execution completed AND verified · ``1`` usage ·
``2`` execution REFUSED (nothing written) · ``3`` a side could not be read · ``5``
execution ran but its RESULT is not trustworthy (residual vectors remain, or the
post-removal count could not be taken) · ``6`` execution FAILED PART-WAY — part of the
graph is already stripped, re-plan before anything else.
"""

from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter before the imports below — a module-level
# 3.12-only import would fail first, and the operator would get an unattributed
# traceback instead of this sentence (the `tools/` entry-point contract;
# `tests/test_entry_point_python_guard.py` pins it for the corpus). This guard is
# PROTECTIVE, not documentary: nothing below it may use 3.12-only SYNTAX, or the file
# would die in the tokenizer and never reach this statement.
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/strip_graph_entity_vectors.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/strip_graph_entity_vectors.py`"
    )

import argparse
import hashlib
import json
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
PLAN_TOOL = "strip_graph_entity_vectors"
PLAN_VERSION = 1

#: Batched removal bound. Small enough that one `IN $keys` list never approaches
#: FalkorDB's `RESULTSET_SIZE`, large enough that a 10k-node label is 50 statements.
DEFAULT_BATCH = 200

#: Refusal tokens. A CLOSED set, printed verbatim, so a caller can gate on the token
#: instead of parsing a sentence — and so a test can assert the population of
#: refusals rather than a string that a later wording change would silently retire.
REFUSE_NO_PLAN = "REFUSE_NO_PLAN"
REFUSE_NO_DIGEST = "REFUSE_NO_DIGEST"
REFUSE_PLAN_UNREADABLE = "REFUSE_PLAN_UNREADABLE"
REFUSE_DIGEST_MISMATCH = "REFUSE_DIGEST_MISMATCH"
REFUSE_PLAN_STALE = "REFUSE_PLAN_STALE"
REFUSE_GRAPH_ONLY = "REFUSE_GRAPH_ONLY"
REFUSE_WRITE_STILL_EMITS = "REFUSE_WRITE_STILL_EMITS"
REFUSE_TARGET_DRIFT = "REFUSE_TARGET_DRIFT"
REFUSE_NOTHING_TO_STRIP = "REFUSE_NOTHING_TO_STRIP"
REFUSE_WRITE_ERROR = "REFUSE_WRITE_ERROR"
REFUSE_WRITE_FAILED = "REFUSE_WRITE_FAILED"

REFUSALS = (
    REFUSE_NO_PLAN,
    REFUSE_NO_DIGEST,
    REFUSE_PLAN_UNREADABLE,
    REFUSE_DIGEST_MISMATCH,
    REFUSE_PLAN_STALE,
    REFUSE_GRAPH_ONLY,
    REFUSE_WRITE_STILL_EMITS,
    REFUSE_TARGET_DRIFT,
    REFUSE_NOTHING_TO_STRIP,
    REFUSE_WRITE_ERROR,
    REFUSE_WRITE_FAILED,
)


class Refusal(Exception):
    """An execution precondition did not hold. ``token`` is a :data:`REFUSALS` member."""

    def __init__(self, token: str, message: str) -> None:
        if token not in REFUSALS:  # a refusal outside the closed set is a bug
            raise ValueError(f"{token!r} is not in REFUSALS")
        super().__init__(message)
        self.token = token


# ── the write gate: does the graph fold still derive this label's vector? ─────


#: The module owning the write gate `#13610` option 2 introduces. Named as a constant so
#: the refusal message and the lookup cannot disagree about where the answer lives.
WRITE_GATE_MODULE = "tortoise.record_vector"
WRITE_GATE_SYMBOL = "mirror_required"

#: The fold that derives and writes each label's vector, in `tortoise/projection/entities.py`.
#:
#: ⛔ THIS MAP EXISTS BECAUSE THE READ PREDICATE IS NOT THE WRITE FACT, and the measurement
#: that proves it is the reason this map is not optional. On the tree that introduces
#: `mirror_required` (`#13610`'s write half, PR #13617), `COVERAGE_GATED_LABELS` is
#: `frozenset(LABEL_SERVED)` — ALL SEVEN LABELS — so `mirror_required("event")` answers
#: ``False`` as soon as the record's coverage verifies. But the gate is consulted at exactly
#: TWO folds (`entities.py:_upsert_subject`, `_upsert_object`); `_upsert_event`,
#: `_upsert_source` and `_upsert_document` write `embedding` regardless. A tool that trusted
#: the predicate alone would therefore declare Event/Source *write-stopped* and strip them
#: into a live writer — the exact race `REFUSE_WRITE_STILL_EMITS` exists to prevent, and it
#: would have shipped green because no test can see a boolean that means two things.
#:
#: So a label's write is treated as stopped only when its OWN writers consult the gate, read
#: from the source with `ast` (the repo's established idiom for "is this writer gated?" —
#: `tools/dod_5090_probe.py`). Fail-closed throughout: an unparseable module, a missing
#: function, or a function that never ASKS the gate all answer "still emits".
FOLD_MODULE = "tortoise.projection.entities"
FOLD_WRITERS: dict[str, tuple[str, ...]] = {
    "Object": ("_upsert_object",),
    "Subject": ("_upsert_subject",),
    "Event": ("_upsert_event", "_event_plain_merge"),
    # `_event_plain_merge` MERGEs `(s:Source {url:$id})` and sets its embedding, so the
    # source label has THREE writers, not two — verified by walking `MERGE (x:Label`
    # targets in each fold body, not by reading the function names.
    "Source": ("_upsert_source", "_upsert_document", "_event_plain_merge"),
}

#: The gate predicates. A writer must CALL one of these; merely mentioning it is not enough.
_GATE_REFERENCES = frozenset({"mirror_required", "_derived_mirror_required"})

#: The repo's own completeness witness for "who stores a vector on a label". Its ``stores``
#: field is re-derived at HEAD by `tests/test_embed_writer_register.py`, and it is the ONLY
#: population that is not a hand-maintained list — which is why the write gate reads it.
#: ⛔ `FOLD_WRITERS` alone was not enough: it covered `entities.py` and missed
#: `tortoise.sdk::_update_entity` (a caller-supplied `embedding` rides `SET n += $props`
#: onto Subject/Object/Source/Event), `tortoise.hosted_backup::restore_graph` (all five
#: labels) and five `:Event` writers in `sdk`/`session_indexer`. Those re-add a vector on
#: the next call exactly as a fold does, so a gate that cleared the folds alone would
#: license a strip its own refusal claims to prevent — one layer outside its own map.
WRITER_REGISTER_MODULE = "tortoise.embed_writer_register"


class _UnreadableFold(Exception):
    """Raised internally when a writer's source cannot be inspected — always fail-closed."""


def _writer_sources() -> dict[str, str]:
    """``module -> source`` for every module a writer check needs, imported once."""
    cache = getattr(_writer_sources, "_cache", None)
    if cache is None:
        cache = {}
        _writer_sources._cache = cache  # type: ignore[attr-defined]
    return cache


def _module_source(module_name: str) -> str:
    import importlib

    cache = _writer_sources()
    if module_name not in cache:
        try:
            module = importlib.import_module(module_name)
            cache[module_name] = Path(module.__file__).read_text()
        except Exception as exc:
            raise _UnreadableFold(
                f"{module_name} could not be read for AST inspection "
                f"({type(exc).__name__}: {exc})") from exc
    return cache[module_name]


def _function_bodies(source: str) -> dict[str, list]:
    """``name -> [FunctionDef, ...]``. A LIST, so a shadowed redefinition is not lost."""
    import ast

    bodies: dict[str, list] = {}
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:      # an unparseable module proves nothing about a write
        raise _UnreadableFold(f"the source could not be parsed ({exc})") from exc
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            bodies.setdefault(node.name, []).append(node)
    return bodies


def _gate_call(node) -> bool:
    """Does this subtree contain a CALL to the gate? A call — not a name, not a string."""
    import ast

    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            callee = sub.func
            name = getattr(callee, "id", None) or getattr(callee, "attr", None)
            if name in _GATE_REFERENCES:
                return True
    return False


def _asks_the_gate(node) -> bool:
    """Does this function ASK the gate — i.e. let its answer steer control flow?

    ⛔ THREE WAYS TO BE WRONG, and each one was a real hole in the first version of this
    check, which was a substring search over the function's raw source lines:

    * **prose** — a comment saying ``# TODO: consult mirror_required`` cleared it. That is
      not a hypothetical: it is exactly what a WIP write-stop leaves behind, in the very
      fold that never asks.
    * **a dying declaration** — a bare ``mirror_required("event")`` expression statement
      is a CALL whose answer nothing reads. It appears to gate and gates nothing.
    * **dead code** — ``if False: mirror_required(...)``.

    So a mention is not evidence and a call is not sufficient: the call must sit in a
    position whose value DECIDES something: an ``if``/``while``/conditional-expression
    TEST that is not a constant. That is the shape the real write-stop uses (`if
    _derived_mirror_required("object"):` around the encode in `_upsert_object`), and it is
    the ONLY position where the gate demonstrably prevents a write *inside the function*.

    ⛔ A RETURNED VALUE IS NOT ACCEPTED, even when it is the gate's own answer.
    ``self._write(ev); return mirror_required("object")``, ``return (self._write(ev),
    mirror_required(…))`` and ``return self._write(ev) or mirror_required(…)`` all call the
    gate and all write unconditionally first — the gate's answer is merely handed to a
    caller who may ignore it. Reachability of a NON-constant branch is not decidable here
    and is not claimed: this answers *"does the code ask before writing"*, never *"does the
    answer hold"*.

    ⛔ AN ASSIGNMENT DOES **NOT** COUNT, and the earlier versions of this function accepted
    ``x = mirror_required(...)`` — a call whose answer is then discarded while the write
    runs unconditionally. That is the same "dying declaration" the paragraph above refuses,
    one line longer. A value that reaches an ``if`` through a local name is therefore not
    recognised (the docstring's alias caveat applies here too): the accepted shapes are
    spelled with the call IN the deciding position, which costs a refactor and never a
    strip.
    A DYNAMIC form (``getattr(record_vector, "mirror_required")(…)``) is deliberately not
    accepted — it cannot be told apart from an accident, and refusing it costs a refactor
    rather than a strip.
    """
    import ast

    for sub in ast.walk(node):
        if isinstance(sub, ast.If | ast.While | ast.IfExp):
            if isinstance(sub.test, ast.Constant):
                continue                      # `if False:` — dead, decides nothing
            # ⛔ THE TEST SUBTREE covers the boolean/negation forms (`not g(...)`,
            # `g(...) and _write()`), because the call sits INSIDE the test. Reading
            # `BoolOp.value`/`UnaryOp.value` — as an earlier version did — read a field
            # those nodes do not have, so both promised arms were dead code.
            if _gate_call(sub.test):
                return True
    return False


def _writer_consults_gate(module_name: str, symbol: str) -> tuple[bool, str]:
    """``(gated, reason)`` for ONE ``module::symbol`` writer of a retired label."""
    try:
        bodies = _function_bodies(_module_source(module_name))
    except _UnreadableFold as exc:
        return False, f"{exc} — treated as STILL EMITTING"
    definitions = bodies.get(symbol)
    if not definitions:
        return False, (
            f"`{symbol}` is ABSENT from {module_name} — a renamed writer cannot be proven "
            "stopped")
    # EVERY definition must ask, not just one: a shadowed first definition is a writer too.
    ungated = [d.lineno for d in definitions if not _asks_the_gate(d)]
    if ungated:
        return False, (
            f"`{module_name}::{symbol}` (definition at line(s) "
            f"{', '.join(str(n) for n in ungated)}) writes `embedding` without CALLING "
            f"`{WRITE_GATE_SYMBOL}`")
    return True, f"{module_name}::{symbol} calls the gate"


def _register_writers(label: str) -> tuple[str, ...]:
    """Every declared writer whose own body stores a vector on ``label``, sorted.

    Read from `tortoise.embed_writer_register` — the repo's completeness witness, whose
    ``stores`` field is re-derived at HEAD by `tests/test_embed_writer_register.py`. An
    unreadable register is NOT an empty population: it raises, and the caller refuses.
    """
    from tortoise import embed_writer_register as register

    return tuple(sorted(
        key for key, record in register.EMBED_WRITER_CLASSES.items()
        if isinstance(record.stores, tuple) and label in record.stores))


def _writers_for(label: str) -> tuple[tuple[str, ...], str | None]:
    """``(writers, refusal)`` — the union of the hand map and the register's population."""
    writers = {f"{FOLD_MODULE}::{name}" for name in FOLD_WRITERS.get(label, ())}
    try:
        writers.update(_register_writers(label))
    except Exception as exc:
        return (), (
            f"the writer register ({WRITER_REGISTER_MODULE}) could not be read "
            f"({type(exc).__name__}: {exc}) — the population that writes this label's "
            "vector cannot be enumerated, so it cannot be proven stopped")
    if not writers:
        return (), (
            f"NO declared writer of `:{label}` exists — an empty population is not "
            "evidence that a write has stopped")
    return tuple(sorted(writers)), None


def _fold_consults_gate(label: str) -> tuple[bool, str]:
    """``(every_writer_gated, reason)`` for ``label`` over EVERY writer that stores it."""
    writers, refusal = _writers_for(label)
    if refusal:
        return False, refusal
    ungated: list[str] = []
    for key in writers:
        module_name, _, symbol = key.partition("::")
        gated, why = _writer_consults_gate(module_name, symbol)
        if not gated:
            ungated.append(why)
    if ungated:
        return False, (
            f":{label} has {len(ungated)} writer(s) that do not ask the gate, so a strip "
            "would be undone by the next one: " + "; ".join(ungated))
    return True, f"every :{label} writer ({len(writers)}) calls the gate"


def write_gate(entity_type: str) -> tuple[bool, str]:
    """``(still_emits, reason)`` for one caller-facing ``entity_type``.

    ⛔ **FAIL-CLOSED IN EVERY UNKNOWN**, which is the opposite of the polarity the gate
    itself uses. `record_vector.mirror_required` answers ``True`` (write it) on an
    unknown because writing costs bytes while skipping costs the row — correct for a
    WRITE. This function answers ``True`` (still emits) on an unknown because *"I could
    not read the gate"* must never be able to licence a delete.

    The symbol is looked up, not imported by name, for one deliberate reason: it is
    `#13610`'s write half and lands in PR #13617. A hard `from … import mirror_required`
    would make THIS module fail to import on the tree `#13617` has not reached — hiding,
    behind an `ImportError`, the very thing an operator needs to be told.
    """
    try:
        from tortoise import record_vector
    except Exception as exc:  # unreadable gate is "still emits"
        return True, (
            f"{WRITE_GATE_MODULE} could not be imported "
            f"({type(exc).__name__}: {exc}), so the write gate cannot be read — "
            "treated as STILL EMITTING")
    gate = getattr(record_vector, WRITE_GATE_SYMBOL, None)
    if gate is None:
        return True, (
            f"{WRITE_GATE_MODULE}.{WRITE_GATE_SYMBOL} is ABSENT on this tree, so the "
            "graph fold still derives and writes this label's vector on every write "
            "(including ON MATCH). #13610's write half (PR #13617) gates the "
            "object/subject folds only; it leaves the event, source and document folds "
            "writing unconditionally — which this function ALSO checks below, so the "
            "predicate appearing is necessary and not sufficient. Stripping now would "
            "be undone by the next write of the same node")
    try:
        needed = bool(gate(entity_type))
    except Exception as exc:  # a raising gate is not a permission
        return True, (
            f"{WRITE_GATE_MODULE}.{WRITE_GATE_SYMBOL}({entity_type!r}) raised "
            f"{type(exc).__name__}: {exc} — an unreadable answer is not a permission")
    if needed:
        return True, (
            f"{WRITE_GATE_MODULE}.{WRITE_GATE_SYMBOL}({entity_type!r}) answers True: "
            "the record has not proved it holds this label's vectors, so the graph "
            "copy is still the answering leg and the fold keeps writing it")
    # ⛔ THE COVERAGE ANSWER IS NECESSARY AND NOT SUFFICIENT. It is a READ predicate: it
    # says what the record holds, never what the fold will do. `COVERAGE_GATED_LABELS` is
    # every served label while only two folds consult the gate, so trusting it alone
    # declares Event/Source stopped and strips them into a live writer. The fold's own
    # source is the only witness to the write.
    from tortoise.security import ENTITY_TYPE_LABELS

    label = ENTITY_TYPE_LABELS.get(entity_type)
    if label is None:
        return True, (
            f"{entity_type!r} has no declared graph label "
            f"({WRITE_GATE_MODULE} coverage alone is not a write fact)")
    gated, why = _fold_consults_gate(label)
    if not gated:
        return True, (
            f"{WRITE_GATE_MODULE}.{WRITE_GATE_SYMBOL}({entity_type!r}) answers False "
            f"(the record reports coverage), but {why}")
    return False, (
        f"{WRITE_GATE_MODULE}.{WRITE_GATE_SYMBOL}({entity_type!r}) answers False and "
        f"{why} — the graph copy is no longer written for this label")


def label_write_gate(label: str) -> tuple[bool, str]:
    """``(still_emits, reason)`` for a LABEL, over every `entity_type` that addresses it.

    `:Source` serves both `source` and `document` (`tortoise.security.ENTITY_TYPE_LABELS`),
    so a label is write-stopped only when EVERY type addressing it is. Checking one and
    assuming the other is how a strip half-lands on the document half of `:Source`.
    """
    types = parity.affected_entity_types(label)
    if not types:
        # ⛔ `all([])` IS TRUE. A label no entity_type addresses would otherwise be
        # declared write-stopped on zero evidence — the vacuous gate, in the one function
        # that decides whether a destructive tool may run.
        return True, f"no entity_type addresses :{label} — refusing on zero evidence"
    reasons: list[str] = []
    still = False
    for entity_type in types:
        emits, reason = write_gate(entity_type)
        if emits:
            still = True
            reasons.append(f"{entity_type}: {reason}")
    if still:
        return True, "; ".join(reasons)
    return False, f"write-stopped for {', '.join(types)}"


def strip_readiness(label: str) -> tuple[bool, str]:
    """The single answer both the census report and the plan carry."""
    emits, reason = label_write_gate(label)
    return (not emits), reason


# ── the plan ────────────────────────────────────────────────────────────────


def digest(entries: list[tuple[str, str]]) -> str:
    """The canonical digest of an exact node set: one LENGTH-FRAMED row per node.

    Sorted, `sha256`-prefixed, and **length-framed** — ``<len(label)>:<label>`` then
    ``<len(key)>:<key>`` per row. Three properties, all load-bearing:

    * over the WHOLE set, so a single key moving changes it;
    * over ROWS rather than a JSON blob, so a re-serialisation of the plan file cannot
      change what was approved;
    * ⛔ FRAMED, because a node key is **caller-supplied** (`:Object.id`, a `:Source`
      `url`) and may contain the separator. With a bare ``label<TAB>key`` join, the
      single node ``(Object, "a\nSubject\tb")`` and the pair ``(Object, "a") +
      (Subject, "b")`` produce ONE digest — and this digest is the authority to delete,
      so a collision is a set the human did not approve being executed as if it were.
      Length framing makes an embedded separator unrepresentable rather than unlikely.
    """
    body = "\n".join(
        f"{len(label)}:{label}{len(key)}:{key}"
        for label, key in sorted(set(entries)))
    return "sha256:" + hashlib.sha256(body.encode()).hexdigest()


def build_plan(*, g: Any, record_dsn: str, org_id: str, graph_id: str,
               graph_name: str, graph_uri: str,
               labels: list[str] | None, batch: int) -> dict:
    """Read both stores, and produce the approvable artefact. Writes NOTHING."""
    report = parity.census(
        g=g, record_dsn=record_dsn, org_id=org_id, graph_id=graph_id,
        labels=labels, strip_readiness=strip_readiness)
    entries: list[tuple[str, str]] = []
    per_label: list[dict] = []
    for row in report["labels"]:
        keys = list(report["keys_by_label"].get(row["label"]) or ())
        # ⛔ THE KEYS ARE CARRIED IN THE PLAN, and that is the authority contract. The
        # execution compares the live set against this one (its digest) and then strips
        # EXACTLY these keys — never a set re-read after the check. A re-read would let a
        # vector that appeared between the digest check and the `REMOVE` be deleted
        # without ever having been in the approved plan, and without its record copy
        # having been parity-checked: the authority gap the token exists to close.
        if row["unkeyed_graph_nodes"]:
            # An unkeyed vector-bearing node cannot be named in a plan, so it cannot be
            # stripped — and it must not be silently left out of the population a human
            # is approving either. It blocks (the census already counts it into
            # graph_only), and the count is stated here in the plan.
            per_label.append({
                "label": row["label"],
                "keys": [],
                "nodes": 0,
                "unkeyed_nodes_blocking": row["unkeyed_graph_nodes"],
                "strip_ready": False,
                "blocked_reason": row["blocked_reason"],
            })
            continue
        entries.extend((row["label"], key) for key in keys)
        per_label.append({
            "label": row["label"],
            "keys": keys,
            "nodes": len(keys),
            "unkeyed_nodes_blocking": 0,
            "strip_ready": row["blocked_reason"] is None,
            "blocked_reason": row["blocked_reason"],
        })
    return {
        "tool": PLAN_TOOL,
        "version": PLAN_VERSION,
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
        "graph": graph_name,
        #: Credential-STRIPPED: the plan is what a human approves and pastes, so it must
        #: not circulate the graph's password (see `uri_fingerprint`).
        "graph_uri": uri_fingerprint(graph_uri),
        "org_id": org_id,
        "graph_id": graph_id,
        "batch": batch,
        "population": list(report["population"]),
        "declared_population": list(report["declared_population"]),
        "census": report["labels"],
        "labels": per_label,
        # DERIVED from the entry list, never trusted from a field a hand can edit: the
        # emptiness refusal and the "expected" count both read this.
        "total_nodes": len(entries),
        "digest": digest(entries),
        "digest_scope": "one LENGTH-FRAMED `len(label):label len(key):key` row per "
                        "node, sorted, sha256",
        "ready": all(row["strip_ready"] for row in per_label) and bool(entries),
    }


def plan_entries(plan: dict) -> list[tuple[str, str]]:
    """The approved node set, reconstructed from the plan's own per-label key lists.

    ⛔ NEVER from ``total_nodes``: a count cannot be digested, so a count-based
    emptiness test (or a count-based "expected") is a number a hand can edit into a
    plan while the set it describes stays empty — which is exactly the vacuous
    "finished retirement" `REFUSE_NOTHING_TO_STRIP` exists to refuse.
    """
    return [(row["label"], key) for row in plan.get("labels") or ()
            for key in (row.get("keys") or ())]


def _key_prop(label: str) -> str:
    """The graph identity property for ``label``, from the census's one declaration."""
    for _table, name, key_prop in parity.RETIRED:
        if name == label:
            return key_prop
    raise CensusError(f"{label!r} is not in the retired population")  # pragma: no cover


def render_plan(plan: dict) -> str:
    lines = [
        f"strip-graph-entity-vectors PLAN — graph {plan['graph']!r} at {plan['at']}",
        f"record tenant: org {plan['org_id']!r} / graph {plan['graph_id']!r}",
        "",
        f"{'label':10} {'nodes':>8} {'strip_ready':>12}  note",
    ]
    for row in plan["labels"]:
        note = "ready" if row["strip_ready"] else (row["blocked_reason"] or "blocked")
        lines.append(
            f"{row['label']:10} {row['nodes']:>8} "
            f"{row['strip_ready']!s:>12}  {note}")
    lines += [
        "",
        f"TOTAL nodes to strip: {plan['total_nodes']}",
        f"DIGEST: {plan['digest']}",
        f"        ({plan['digest_scope']})",
        "",
        "This is a PREVIEW — nothing was removed. To execute this exact set:",
        "  --execute --plan <this file> --confirm-plan-digest "
        f"{plan['digest']}",
    ]
    measured = plan.get("population") or [r["label"] for r in plan["labels"]]
    declared = plan.get("declared_population") or measured
    dropped = sorted(set(declared) - set(measured))
    if dropped:
        lines += [
            "",
            f"⚠️ NARROWED: this plan covers {', '.join(measured)} only and says NOTHING "
            f"about {', '.join(dropped)}. Execution is limited to the labels above.",
        ]
    if not plan["ready"]:
        lines += [
            "",
            "⛔ NOT READY: at least one label above is blocked, so execution of THIS plan "
            "would REFUSE (nothing is written). Resolve the block (backfill the record, "
            "or land that label's write-stop) and re-plan. ⚠️ A NARROWED plan is a "
            "different thing: `--label` at PLAN time drops the blocked labels instead of "
            "refusing, and the narrowed plan names what it left out above.",
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
            "Nothing was removed.") from exc
    if not isinstance(plan, dict) or plan.get("tool") != PLAN_TOOL \
            or plan.get("version") != PLAN_VERSION:
        raise Refusal(
            REFUSE_PLAN_UNREADABLE,
            f"{path!r} is not a {PLAN_TOOL} v{PLAN_VERSION} plan (tool="
            f"{plan.get('tool') if isinstance(plan, dict) else type(plan).__name__!r})"
            ". Nothing was removed.")
    if plan.get("digest") != confirmed:
        raise Refusal(
            REFUSE_DIGEST_MISMATCH,
            f"the plan's own digest is {plan.get('digest')!r} but "
            f"--confirm-plan-digest says {confirmed!r}. A plan edited after it was "
            "printed is not the plan that was approved. Nothing was removed.")
    # ⛔ THE RECOMPUTATION, and it is not redundant with the field check above. The
    # ``digest`` FIELD is a string in a JSON file: editing the per-label ``keys`` lists
    # leaves it untouched, so a field-only check approves a set the human never saw —
    # `plan_entries` calls itself "the approved node set" and `execute` strips exactly
    # those lists. Re-deriving the digest FROM the lists is what makes that claim true.
    try:
        recomputed = digest(plan_entries(plan))
    except Exception as exc:
        raise Refusal(
            REFUSE_PLAN_UNREADABLE,
            f"the plan's per-label key lists could not be read as an entry set "
            f"({type(exc).__name__}: {exc}). Nothing was removed.") from exc
    # ⛔ A DUPLICATE LABEL ROW is invisible to the digest (`sorted(set(entries))`) and makes
    # the removal strip the same label twice with a reset counter. Refused here because a
    # plan this tool did not print is not the approved artefact.
    rows = plan.get("labels") or []
    labels = [row.get("label") for row in rows if isinstance(row, dict)]
    duplicated = sorted({label for label in labels if labels.count(label) > 1})
    if duplicated:
        raise Refusal(
            REFUSE_PLAN_UNREADABLE,
            f"the plan lists {', '.join(duplicated)} more than once. A digest is a set, so "
            "duplication cannot be seen in it — and executing it would strip the same "
            f"label twice. Re-plan. Nothing was removed.")
    if recomputed != confirmed:
        raise Refusal(
            REFUSE_DIGEST_MISMATCH,
            f"the plan's key lists re-derive to {recomputed!r} but "
            f"--confirm-plan-digest says {confirmed!r} (its recorded `digest` field is "
            f"{plan.get('digest')!r}). The recorded field was not edited with the lists, "
            "or not from these lists — either way this is not the approved set. Nothing "
            "was removed.")
    return plan


def uri_fingerprint(uri: str) -> str:
    """A plan-safe fingerprint of a graph target: scheme, host, port and graph NAME.

    ⛔ THE URI IS PART OF THE TARGET and it carries the FalkorDB PASSWORD
    (`docker://:pw@host:6379/graph`). Two consequences, both handled here: the plan is the
    artefact a human is asked to approve and to paste a digest of, so it must not
    circulate a credential; and a clone of the same graph on another host has the same
    name, the same tenant and the same node set, so a name-only comparison would let a
    plan approved against production execute against staging.
    """
    scheme, sep, rest = uri.partition("://")
    if not sep:
        return uri
    authority, _, path = rest.partition("/")
    host = authority.rpartition("@")[2]      # drop `user:password@`
    return f"{scheme}://{host}/{path.lstrip('/')}" if path else f"{scheme}://{host}"


def _live_entries(g: Any, labels: list[str]) -> list[tuple[str, str]]:
    """The graph's CURRENT set, per label — the digest comparison's other side.

    Used only to decide whether the approved plan still describes the store. The
    vectors actually removed come from the PLAN (`plan_entries`), never from this read:
    a set re-read between the check and the write is a set the human never approved.
    """
    entries: list[tuple[str, str]] = []
    for label in labels:
        keys = parity.read_graph_vector_keys(g, label, _key_prop(label))["keys"]
        entries.extend((label, key) for key in keys)
    return entries


def execute(*, g: Any, plan: dict, record_dsn: str, org_id: str, graph_id: str,
            graph_name: str, graph_uri: str, confirmed: str, batch: int,
            expect_empty: bool) -> dict:
    """Every precondition, then the batched removal. Refuses BEFORE the first write."""
    # ⛔ THE WHOLE TARGET TRIPLE. The graph NAME alone is not the target: the tenant
    # flags default from `TORTOISE_RECORD_ORG_ID`/`TORTOISE_RECORD_GRAPH_ID`, and the
    # graph name is very often the `tortoise` default — so a preview on one tenant and an
    # execute on another would certify parity against the WRONG record and strip the
    # vectors whose own tenant's record never held them. `org_id`/`graph_id` are in the
    # plan; all three are compared.
    drift = [name for name, planned, live_value in (
        ("graph", plan.get("graph"), graph_name),
        ("org_id", plan.get("org_id"), org_id),
        ("graph_id", plan.get("graph_id"), graph_id),
        ("graph_uri", plan.get("graph_uri"), graph_uri),
    ) if planned != live_value]
    if drift:
        raise Refusal(
            REFUSE_TARGET_DRIFT,
            f"the plan was built against a different target on {', '.join(drift)} "
            f"(plan graph={plan.get('graph')!r} org={plan.get('org_id')!r} "
            f"graph_id={plan.get('graph_id')!r} uri={plan.get('graph_uri')!r}; this run "
            f"graph={graph_name!r} org={org_id!r} graph_id={graph_id!r} "
            f"uri={graph_uri!r}). A plan is an authority over ONE target. Nothing was "
            "removed.")
    labels = [row["label"] for row in plan["labels"]]

    # ── the parity re-check, LIVE, immediately before the first write ────────
    live = parity.census(
        g=g, record_dsn=record_dsn, org_id=org_id, graph_id=graph_id, labels=labels,
        strip_readiness=strip_readiness)
    blocked = [row for row in live["labels"] if row["graph_only"]]
    if blocked:
        detail = "; ".join(
            f"{row['label']} graph_only={row['graph_only']}"
            + (f" (unkeyed={row['unkeyed_graph_nodes']})"
               if row["unkeyed_graph_nodes"] else "")
            for row in blocked)
        raise Refusal(
            REFUSE_GRAPH_ONLY,
            f"parity does NOT hold at the moment of the delete: {detail}. A graph-only "
            "vector is the ONLY readable copy of that fingerprint — the answer is a "
            "backfill (#13430's repair path), never a delete. Nothing was removed.")

    # ── the exact-set precondition: the token authorises ONE set ─────────────
    live_digest = digest(_live_entries(g, labels))
    if live_digest != confirmed:
        per_label = ", ".join(
            f"{row['label']}={row['graph_vectors']}" for row in live["labels"])
        raise Refusal(
            REFUSE_PLAN_STALE,
            f"the live population does not match the approved plan. plan "
            f"{confirmed!r} vs live {live_digest!r} (live per label: {per_label}). One "
            "node more, one node less, or any key that moved, refuses — re-plan and "
            "re-approve. Nothing was removed.")

    # ── the write gate: stripping into a live writer is a no-op retirement ───
    still = [row for row in live["labels"] if row["blocked_reason"]]
    if still:
        detail = "; ".join(f"{row['label']}: {row['blocked_reason']}" for row in still)
        raise Refusal(
            REFUSE_WRITE_STILL_EMITS,
            f"the graph write path still emits these labels' vectors, so a strip would "
            f"be undone by the next write of the same node: {detail}. Nothing was "
            "removed.")

    # ── nothing to strip is a REFUSAL, not a success ─────────────────────────
    # ⛔ DERIVED from the approved ENTRY list, never read from `total_nodes`: a count is
    # a field a hand can edit into a plan whose set is empty, and the refusal would then
    # not fire on the one shape it exists for.
    total = len(plan_entries(plan))
    if total == 0 or not live["labels"]:
        if not expect_empty:
            raise Refusal(
                REFUSE_NOTHING_TO_STRIP,
                "the approved plan contains no node. Executing it would report a "
                "completed retirement it never performed — pass --expect-empty only "
                "when an empty population is the intended state. Nothing was removed.")
        return {"at": datetime.now(UTC).isoformat(timespec="seconds"),
                "graph": graph_name, "plan_digest": confirmed, "labels": [],
                "removed": 0, "expected": 0, "residual": 0, "empty": True}

    removed: dict[str, int] = {}
    shortfalls: dict[str, int] = {}
    for row in plan["labels"]:
        label = row["label"]
        # ⛔ THE APPROVED KEYS, not a fresh read of the table. The digest above already
        # proved the live set equals this one; stripping the live set instead would let
        # a vector that appeared in the check-to-write window be deleted without ever
        # being in the plan a human approved, and without its record copy having been
        # parity-checked.
        keys = sorted(row.get("keys") or ())
        quoted_label = parity._quote_identifier(label)
        quoted_key = parity._quote_identifier(_key_prop(label))
        # ⛔ `setdefault`, NOT assignment. A plan whose `labels` list repeats a label would
        # otherwise RESET that label's running count on the second row, so a later failure
        # would report "nothing was stripped" (exit 2) over a half-stripped table — the
        # exact lie this split exists to remove.
        removed.setdefault(label, 0)
        shortfalls.setdefault(label, 0)
        for start in range(0, len(keys), batch):
            chunk = keys[start:start + batch]
            if not chunk:
                continue
            statement = (
                f"MATCH (n:{quoted_label}) "
                f"WHERE n.embedding IS NOT NULL AND n.{quoted_key} IN $keys "
                "REMOVE n.embedding RETURN count(n)")
            try:
                result = g.query(statement, params={"keys": chunk})
                seen = int(result.result_set[0][0]) if result.result_set else 0
            except Exception as exc:  # report, never swallow
                # ⛔ THE COUNT DECIDES THE TOKEN, and getting this wrong is worse than not
                # having the distinction at all. A first-chunk failure — the state a store
                # wedge, a timeout or a `--batch` too large for the driver actually
                # produces — has removed NOTHING, so exit 2 is exactly true and a retry is
                # safe. Only a failure AFTER at least one batch landed is part-stripped.
                # Claiming "part-stripped" on the zero case teaches an operator to distrust
                # exit 6 on the one occasion it matters.
                stripped = sum(removed.values())
                if stripped == 0:
                    raise Refusal(
                        REFUSE_WRITE_ERROR,
                        f"the removal statement FAILED on {label} at offset {start} "
                        f"({type(exc).__name__}: {exc}) and NO node was stripped — no "
                        "batch had stripped anything before this failure, so nothing was "
                        "removed and re-running once the store error is understood is "
                        "safe.") from exc
                raise Refusal(
                    REFUSE_WRITE_FAILED,
                    f"the removal statement FAILED on {label} at offset {start} "
                    f"({type(exc).__name__}: {exc}). {stripped} node(s) were ALREADY "
                    "STRIPPED before the failure — the graph is part-stripped, which is "
                    "why this is not a plain refusal. Nothing further was removed; "
                    "re-plan to see the current set.") from exc
            removed[label] += seen
            # `REMOVE` of an absent property is a no-op, so a shortfall is benign — but
            # it is also the signal that the population moved under us, so it is
            # counted and reported rather than absorbed.
            if seen < len(chunk):
                shortfalls[label] += len(chunk) - seen

    residual = {}
    for row in plan["labels"]:
        label = row["label"]
        try:
            residual[label] = parity.read_graph_vector_keys(
                g, label, _key_prop(label))["count"]
        except CensusError as exc:  # pragma: no cover - post-write read failure
            residual[label] = f"UNMEASURED({exc})"
    # ⛔ THE LABELS THIS RUN DID NOT TOUCH. `residual_by_label` is over the plan's labels,
    # so a narrowed strip whose residuals are all zero would otherwise report a clean
    # retirement of a population it never looked at — the declared-but-unmeasured labels
    # still hold their vectors. Named in the result so no reader has to infer the scope.
    unmeasured = sorted(set(plan.get("declared_population") or ())
                        - {row["label"] for row in plan["labels"]})
    if unmeasured:
        sys.stderr.write(
            f"strip_graph_entity_vectors: NARROWED — this plan covers "
            f"{', '.join(row['label'] for row in plan['labels'])} only. "
            f"{', '.join(unmeasured)} were NOT stripped and still hold their vectors; "
            "this run's zero residual is about the plan's labels, not the retirement.\n")
    return {
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
        "graph": graph_name,
        "plan_digest": confirmed,
        "labels": [row["label"] for row in plan["labels"]],
        "unmeasured_by_label": unmeasured,
        "children": len(plan["labels"]),
        "removed": sum(removed.values()),
        "expected": total,
        "removed_by_label": removed,
        "shortfall_by_label": shortfalls,
        "residual_by_label": residual,
        "empty": False,
    }


def result_exit_code(result: dict) -> int:
    """The exit status for a COMPLETED execution — the post-write verdict, in one place.

    ⛔ Extracted so its polarity can be asserted without a store, because the polarity is
    the whole content of exit 5. `0` means "stripped and verified"; anything the run
    could not verify is NOT zero — an unmeasurable residual summing to nothing is how a
    strip whose result nobody could read reports a clean retirement.
    """
    if result.get("empty"):
        return 0
    residuals = list((result.get("residual_by_label") or {}).values())
    unmeasured = [v for v in residuals if not isinstance(v, int)]
    if unmeasured or sum(v for v in residuals if isinstance(v, int)):
        return 5
    return 0


#: The exit status for a refusal. ⛔ ONE table, because the two codes promise DIFFERENT
#: things and a caller routes on the difference: 2 says "nothing was written, safe to
#: retry with a fixed plan"; 6 says "part of the graph is already stripped, re-plan before
#: anything else". Folding them would tell a caller that a half-finished retirement is a
#: no-op.
_REFUSAL_EXIT = {REFUSE_WRITE_FAILED: 6}


def refusal_exit_code(token: str) -> int:
    """``2`` for a refusal before any write, ``6`` once a batch has landed."""
    return _REFUSAL_EXIT.get(token, 2)


def _require_tokens(args: Any) -> dict:
    """The two token preconditions, checked BEFORE any store is opened.

    ⛔ ORDER IS LOAD-BEARING. The authority to delete is (a) a reviewed plan and (b) its
    digest, and neither needs a connection to check. Reading the stores first would make
    a token-less run pay for two store binds and could fail on an UNRELATED refusal
    (an unresolvable graph, an unreadable record) — so an operator who forgot the
    digest would be told about a store instead of about the missing token.
    """
    if not args.plan:
        raise Refusal(
            REFUSE_NO_PLAN,
            "--execute without --plan. There is no 'use the live census instead': "
            "the authority to delete is a reviewed artefact, not a race with a "
            "table. Nothing was removed.")
    if not args.confirm_plan_digest:
        raise Refusal(
            REFUSE_NO_DIGEST,
            "--plan without --confirm-plan-digest. The digest IS the token; a plan "
            "file alone is a proposal, and copying one is trivial. Nothing was "
            "removed.")
    return _load_plan(args.plan, args.confirm_plan_digest)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--uri", help="FalkorDB URI of the target graph")
    src.add_argument("--db-path", help="embedded graph path instead of a URI")
    parser.add_argument("--graph-name", default=None,
                        help="the graph NAME to strip (a target selector; it never "
                             "scopes the record read — that is --org-id/--graph-id)")
    parser.add_argument("--record-dsn", default=None,
                        help="hosted record DSN (default: TORTOISE_RECORD_DSN)")
    parser.add_argument("--org-id", default=os.environ.get("TORTOISE_RECORD_ORG_ID", ""),
                        help="record tenant org")
    parser.add_argument("--graph-id", default=os.environ.get("TORTOISE_RECORD_GRAPH_ID", ""),
                        help="record tenant graph")
    parser.add_argument("--label", action="append", default=None,
                        choices=[label for _, label, _ in parity.RETIRED],
                        help="plan only this label (repeatable); default: all four")
    parser.add_argument("--batch", type=int, default=None,
                        help="nodes per removal statement (default: the plan's recorded "
                             f"batch on --execute, else {DEFAULT_BATCH})")
    parser.add_argument("--plan-out", default=None,
                        help="write the preview plan to this path "
                             "(required to execute it later)")
    parser.add_argument("--json", default=None, help="write the run's report here")
    parser.add_argument("--execute", action="store_true",
                        help="REMOVE the property from the plan's exact node set; the "
                             "default is a preview")
    parser.add_argument("--plan", default=None,
                        help="the approved plan file (required with --execute)")
    parser.add_argument("--confirm-plan-digest", default=None,
                        help="the plan's `sha256:` digest, i.e. the token authorising "
                             "this exact set (required with --execute)")
    parser.add_argument("--expect-empty", action="store_true",
                        help="allow an EMPTY plan to execute as a no-op")
    args = parser.parse_args(argv)

    empty_flag = census_mod.empty_graph_target_flag(args)
    if empty_flag:
        sys.stderr.write(f"strip_graph_entity_vectors: {empty_flag}\n")
        return 1
    if args.batch is not None and args.batch <= 0:
        parser.error("--batch must be > 0")
    if args.execute and args.label:
        # ⛔ THE PLAN'S DIGEST IS THE AUTHORITY AT EXECUTE TIME, so a narrowing flag here is
        # a contradiction, not a filter: argparse would validate `--label` and then the run
        # would strip the WHOLE approved plan while the operator believes it stripped one
        # label. Refusing is the only honest reading.
        parser.error(
            "--label is a PLAN-time narrowing and cannot narrow --execute — the plan's "
            "digest authorises an exact set. Build a narrowed plan with --label, then "
            "execute that plan.")
    if args.execute and args.plan_out:
        parser.error(
            "--plan-out is a PLAN-time flag; --execute writes no plan. Remove it.")

    record_dsn = args.record_dsn or os.environ.get("TORTOISE_RECORD_DSN", "")
    if not record_dsn:
        sys.stderr.write(
            "strip_graph_entity_vectors: no record leg — pass --record-dsn or set "
            "TORTOISE_RECORD_DSN. Parity cannot be established without the copy the "
            "removal depends on, so nothing was read and nothing was removed.\n")
        return 3
    if not args.org_id or not args.graph_id:
        sys.stderr.write(
            "strip_graph_entity_vectors: the record tenant is unnamed — pass "
            "--org-id and --graph-id (or TORTOISE_RECORD_ORG_ID / "
            "TORTOISE_RECORD_GRAPH_ID). An untenanted record read returns ZERO rows "
            "under RLS and would certify parity vacuously (#10024). Nothing was "
            "removed.\n")
        return 3

    # ── the two token refusals come FIRST: neither needs a store ─────────────
    plan: dict | None = None
    if args.execute:
        try:
            plan = _require_tokens(args)
        except Refusal as exc:
            sys.stderr.write(f"strip_graph_entity_vectors: {exc.token} — {exc}\n")
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
        sys.stderr.write(f"strip_graph_entity_vectors: REFUSED — {exc}\n")
        return 3
    except Exception as exc:
        sys.stderr.write(
            f"strip_graph_entity_vectors: REFUSED — the target could not be resolved "
            f"({type(exc).__name__}: {exc}). Nothing was removed.\n")
        return 3

    if not args.execute:
        try:
            plan = build_plan(
                g=proj.g, record_dsn=record_dsn, org_id=args.org_id,
                graph_id=args.graph_id, graph_name=graph_name,
                graph_uri=args.uri or args.db_path or "", labels=args.label,
                batch=args.batch or DEFAULT_BATCH)
        except CensusError as exc:
            sys.stderr.write(
                f"strip_graph_entity_vectors: REFUSED — {exc}. A census that cannot be "
                "run is a refusal, not a pass, and nothing was removed.\n")
            return 3
        print(render_plan(plan))
        if args.plan_out:
            Path(args.plan_out).write_text(json.dumps(plan, indent=2) + "\n")
            print(f"\nplan written to {args.plan_out}")
        if args.json:
            Path(args.json).write_text(json.dumps(plan, indent=2) + "\n")
        return 0

    try:
        plan = _load_plan(args.plan, args.confirm_plan_digest)
        # ⛔ THE PLAN'S BATCH IS HONOURED when the operator did not restate it: the plan is
        # the approved artefact, and a value it records must not be silently ignored at the
        # moment it is used (the same defect class as `--label` on `--execute`).
        try:
            plan_batch = int(plan.get("batch") or DEFAULT_BATCH)
        except (TypeError, ValueError):
            plan_batch = DEFAULT_BATCH
        if plan_batch <= 0:
            plan_batch = DEFAULT_BATCH
        result = execute(
            g=proj.g, plan=plan, record_dsn=record_dsn, org_id=args.org_id,
            graph_id=args.graph_id, graph_name=graph_name,
            # ⛔ THE SAME EXPRESSION `build_plan` FINGERPRINTS, and the same function:
            # `execute` compares the plan's stored fingerprint against this value, so a
            # raw URI here would drift on every credentialed deployment, and omitting
            # the argument would raise `TypeError` out of the destructive path instead
            # of refusing it. Both are the same bug seen from two sides.
            graph_uri=uri_fingerprint(args.uri or args.db_path or ""),
            confirmed=args.confirm_plan_digest, batch=args.batch or plan_batch,
            expect_empty=args.expect_empty)
    except Refusal as exc:
        sys.stderr.write(
            f"strip_graph_entity_vectors: {exc.token} — {exc}\n")
        # ⛔ EXIT 6 IS "PART-STRIPPED", and it is a DIFFERENT code from exit 2 on
        # purpose. See `refusal_exit_code` — the mapping lives in one table so the two
        # promises cannot drift apart.
        return refusal_exit_code(exc.token)
    except CensusError as exc:
        sys.stderr.write(f"strip_graph_entity_vectors: REFUSED — {exc}\n")
        return 3

    print(json.dumps(result, indent=2))
    if args.json:
        Path(args.json).write_text(json.dumps(result, indent=2) + "\n")
    code = result_exit_code(result)
    if code == 5:
        residuals = result.get("residual_by_label") or {}
        unmeasured = sorted(label for label, v in residuals.items()
                            if not isinstance(v, int))
        if unmeasured:
            sys.stderr.write(
                "strip_graph_entity_vectors: the post-removal count could NOT be taken "
                f"for {', '.join(unmeasured)} — a strip whose result could not be "
                "verified is not a completed strip. Re-run the census against this graph "
                "before reporting the retirement.\n")
        else:
            sys.stderr.write(
                "strip_graph_entity_vectors: "
                f"{sum(v for v in residuals.values() if isinstance(v, int))} vector(s) "
                "remain after the removal — a CONCURRENT WRITER re-added them, or the "
                "write gate is not actually closed on the running deployment. Re-plan; "
                "do not re-execute this plan (its preconditions no longer hold).\n")
    return code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
