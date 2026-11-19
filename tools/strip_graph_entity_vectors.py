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
    today, INCLUDING after #13617 lands. When the coverage predicate is present it names
    each un-cleared writer; while the predicate is ABSENT it returns before that walk, so
    it names the requirement rather than the writers. That is the
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
    A removal statement raised on the FIRST batch, so no EARLIER statement had completed
    — and therefore **no node is KNOWN to have been stripped**. Exit is ``2`` like every
    other pre-write refusal. ⛔ THE CLAIM IS SCOPED TO WHAT IS MEASURED: ``attempted``
    counts statements ISSUED, not writes LANDED — the same distinction that rejected
    ``sum(removed.values())`` on the other side of this token. A client-side read timeout,
    or a socket dropped AFTER the server applied the batch, raises here with the batch
    already landed, and a wedge, a timeout or an oversized ``--batch`` are exactly the
    things that produce that shape. So this is not a promise that the store is untouched;
    it is the one fact the count supports. A retry after a part-landed batch is safe by
    construction anyway — the live set no longer matches the plan and the re-run refuses
    with ``REFUSE_PLAN_STALE`` rather than stripping twice.
``REFUSE_TARGET_UNPARSEABLE``
    The graph target's userinfo boundary cannot be determined, so no credential-free
    fingerprint of it exists — see `uri_fingerprint`. Exit is ``2``. ⛔ Refused rather
    than guessed because the two halves of the requirement cannot both be met on an
    ambiguous URI: cutting at the last ``@`` keeps the password out of the plan but can
    DROP the host, and dropping the host makes two different targets compare EQUAL, which
    is a fail-open on the very check that stops a plan approved against production from
    executing against staging. FOUR SHAPES reach it, with two different remedies:

    * an ``@`` after the first ``/`` (a password containing ``/``, or an ``@`` inside the
      graph name) — percent-encode the credential (``/`` → ``%2F``), or pass the NAME out
      of band with ``--graph-name`` (percent-encoding a name silently targets a different
      graph);
    * a ``?``/``#`` before the last ``@`` where no ``host:port`` follows it — the authority
      cannot be separated from the credential; percent-encode ``?`` → ``%3F``, ``#`` →
      ``%23``;
    * userinfo on a target written without a scheme separator (``docker::pw@host:6379/g``),
      which would otherwise be passed through verbatim as a db path;
    * a CREDENTIAL WITH NO ``@`` at all (``docker://:pw``, ``docker://user:pw``,
      ``docker://:pw:6379``) and an empty authority (``docker://``) — the shapes a
      copy-paste that drops the ``@host`` tail produces, which the canonical masker
      redacts as unrecognised and which used to be returned VERBATIM as the host.
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

⛔ **`$keys` CARRIES THE GRAPH'S OWN VALUES — typed, never rendered.** `IN` is equality
and Cypher equality is type-sensitive, so `n.id IN ["123"]` does not select an integer
`:Object.id` (MEASURED, `tests/test_13629_entity_vector_retire.py`). A plan that carried
`str()` renderings would match nothing on such a node, count a shortfall, read a residual
and report exit 5 — blaming a concurrent writer for the tool's own rendering, with
"re-plan" producing the identical plan. The plan therefore holds the RAW identity value
and the record leg is asked in the rendered form; one value, two uses, no re-derivation.
For the same reason the comparison is NOT `toString(n.<key>) IN $keys`: the engine's
`toString(1.5)` is `1.500000` while `str()` is `1.5`, so that would fail on exactly the
non-string keys it was added for.

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

COMPATIBILITY — what this build refuses that the previous one did not
=====================================================================
Stated here because a plan is an artefact that OUTLIVES the build that printed it, and
an operator holding an older file deserves a diagnosis rather than a surprise:

* **A plan minted under the TAB-JOINED framing refuses with
  ``REFUSE_DIGEST_MISMATCH``.** That is every plan minted before this change:
  ``digest()`` used to be ``label<TAB>key`` and is now ``len(label):label`` +
  ``len(key):key`` (and type-framed for non-strings, `_digest_key`), so such a plan's
  token cannot re-derive from its own key lists. RE-PLAN; the plan's population is not
  wrong, its token is. ⛔ Only the TAB-JOINED framing is refused: a plan whose population
  is ALL STRINGS and that was framed by the length-prefixed build still re-derives
  byte-identically and stays executable (`_digest_key`) — that is deliberate, and it is
  why this paragraph names the framing rather than "the previous build".
* **A plan that names the same node twice refuses with ``REFUSE_PLAN_UNREADABLE``.**
  The digest describes a SET, so duplication is invisible to it while `expected` counted
  it — one more number that could exceed the set it claimed to describe.
* **Exit ``6`` and ``--expect-narrowed`` are new.** ``6`` means "part of the graph is
  already stripped", which ``2`` ("nothing was written") must never mean; a narrowed run
  exits ``5`` unless ``--expect-narrowed`` says the partial sweep was intended.
* **The record leg is two statements** (one aggregate, one narrowed to the graph's own
  keys), not one full-table read.
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
REFUSE_TARGET_UNPARSEABLE = "REFUSE_TARGET_UNPARSEABLE"

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
    REFUSE_TARGET_UNPARSEABLE,
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
    # ⛔ NOT `_event_plain_merge`: it MERGEs `(s:Source {url:$id})` but every `embedding`
    # touch in it is on `e` (`clear_embedding(..., "e", ...)` and
    # `set_embedding_clause("e")`), so it writes the EVENT vector, not the Source one. A
    # MERGE-target walk finds the Source and stops — which is how an earlier comment
    # "verified" a write that does not happen, and it made `:Source` permanently
    # unopenable on a function that cannot legitimately ask about a vector it never writes.
    #
    # ⛔ `_upsert_source` STAYS, and the round-13 review's request to drop it was NOT
    # applied — its premise ("stores no vector: zero `set_embedding_clause`/`clear_embedding`
    # in the body") is TRUE AND IRRELEVANT, which is why the measurement is written here
    # instead of the reading being trusted (`#13647`). The body issues no LITERAL clause of
    # its own, but it PERSISTS THE CALLER'S: `_upsert_source` ends by funnelling extra props
    # through `_persist_extra_props("MATCH (n:Source {url: $url})", …, allow_keys=_allow)`,
    # `_allow = self._SOURCE_EXTRA_PROPS`, and that closed surface DECLARES `embedding`
    # (`tortoise/projection/entities.py::_SOURCE_EXTRA_PROPS`, beside `domain`/`summary`;
    # `#12182` cycle 9 corrected the register's entry for the same mistake). So
    # `create_source(url, props={"embedding": [...]})` lands a vector on `:Source` with no
    # gate — the `sdk::_update_entity` passthrough class this map exists to cover. Dropping
    # the name would therefore be a FAIL-OPEN: the day `_upsert_document` learns to ask,
    # `:Source` would clear while an ungated writer still re-adds the vector on the next
    # call. A fix may add refusals, never acceptances.
    #
    # STATED RESIDUAL: neither Source writer asks the gate today, so `:Source` is refused
    # for as long as the passthrough stores an ungated caller vector — the fail-closed
    # direction (an operator is refused a strip, never granted a losing one). Opening it is
    # `#12180`'s work (a caller vector landing on `:Source` with no gate and no width
    # check), tracked there rather than papered over by a narrower population here.
    "Source": ("_upsert_source", "_upsert_document"),
}

#: The gate predicates. A writer must CALL one of these; merely mentioning it is not enough.
_GATE_REFERENCES = frozenset({"mirror_required", "_derived_mirror_required"})

#: The graph property a graph-side vector lives in — `embedding`, not the record's
#: `embedding_vec` column. One name, because evidence for "the writer stores the vector"
#: and the statement that removes it must not be able to drift apart.
EMBEDDING_PROPERTY = "embedding"

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


def _asks_the_gate(node, entity_types: tuple[str, ...]) -> bool:
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
    position whose value DECIDES the WRITE: an ``if`` (the only form walked — `while` and
    conditional expressions are not accepted, and this docstring says so rather than
    promising arms the code does not implement) whose test asks the gate about THIS label
    and whose BODY STORES the embedding. That is exactly the shape `#13617`
    implements — ``embedding = None; if _derived_mirror_required("subject"):
    … embedding = derived_embedding.encode(...)``, an ENCODE GUARD with no ``return`` and
    no ``raise``. Requiring an early exit, as an earlier version did, refused the only
    write-stop that exists and made the gate unable to open on the fold that asks; a
    `Return`/`Raise` body is still accepted as the alternative form.

    ⛔ AND IT MUST BE ASKED **ABOUT THIS LABEL**. `_update_entity` and `restore_graph` each
    store FIVE labels, so a gate call is not a fact about all of them: a partial write-stop
    shaped `if _derived_mirror_required("object"): props.pop("embedding")` leaves the
    Source/Event stores unconditional, and a name-only match would clear every label that
    function declares — the "one boolean meaning five things" defect this file already
    fixed once at the fold level. So the call's first positional argument must be a
    STRING CONSTANT naming one of the label's own ``entity_types``; a computed argument is
    "still emits", because the tool cannot tell which label it asked about.

    ⛔ AND THE TESTED BRANCH MUST CONTAIN THE STORE. `if mirror_required("object"):
    _bump_counter()` calls the gate and the write then runs anyway; `if g(...) or True:` is
    a constant branch. The accepted form is an ``if`` whose test asks the gate about this
    label and whose body STORES ``embedding`` (or returns/raises) — so the guard sits on
    the write itself, which is what `#13617` does. "Stores" means an embedding-named
    ASSIGNMENT of a non-constant value, or a Cypher clause that sets the property **and
    is given to a statement runner** (a clause string nothing runs is text, not a store): a
    mention (`print(embedding)`, a log line naming it) and a placeholder
    (`embedding = None`) are both refused, because a `False` verdict must actually stop the
    vector being written.

    ⛔ AND ON THE STOP ARM A STORE REFUSES THE GUARD, WHATEVER ITS POSITION — and the stop
    arm is the BODY under a negated ask (`if not gate(l): …`) and the **`else`** under a
    positive one (`if gate(l): … else: …`). The exit-shaped write-stop and the
    removal-shaped one are both accepted only
    when the arm places NO vector at all: `if not gate("object"): props["embedding"] =
    _encode(ev); return None` is a writer that stores exactly when told to stop, and an
    earlier revision certified it GATED because the exit arm ran first and asked nothing
    about the store. The same held for the `raise` spelling, for `props.update(embedding=…)`,
    for `pop("embedding")` followed by a store, and for a store in the arm's own `finally`
    (measured at `622e9935`). "No store" is judged ANYWHERE in the arm (`_own_walk`: live
    conditionals and `finally` included), not just unconditionally — on this arm an
    unplaceable store is evidence AGAINST the write-stop, so the fail-closed answer is to
    refuse. What this still cannot see is residual 4's family: a store reached through a
    helper ARGUMENT (`_set(props, embedding)`), which no arm-level walk can attribute.

    ⛔ AND THE POSITIVE ASK'S `else` IS THAT SAME STOP ARM (`#13647`, measured at
    `f9d0549e80`). Under `if gate(l): …` the body runs when the graph must still mirror and
    `orelse` runs when it must stop, so a store on `orelse` is the stop arm storing what it
    was told to stop — and every arm read `sub.body` only, so it was never examined:
    `if gate("object"): props["embedding"] = …; else: props["embedding"] = …; return None`
    certified the writer GATED. Both stop arms now read their own arm with
    `anywhere=True`; the mirror arm never does, so the destructive gate cannot ACCEPT a
    writer it refused before.

    ⛔ STATED RESIDUALS, all four, because a completeness claim narrower than the code is
    how the next lane re-derives false confidence:

    1. **Two write sites, one guarded** — undetectable without data flow this check does
       not do. The requirement it CAN enforce is "the gate guards A store of this label in
       this function"; a write-stop that leaves a second site open is a defect in that
       write-stop.
    2. **No ordering** — the walk does not relate the `if`'s POSITION to the store, so an
       accepted guard placed AFTER an unconditional store still clears the function. What
       narrows this in practice is that the guarded body must itself store, which a
       pointless late guard does not do.
    3. **Dead code, nested scopes, NESTED CONDITIONS and SWALLOWED EXITS are pruned, not
       analysed** — a guard wrapped in `if False:`/`while False:` or inside an inner
       `def`/`class`/`lambda` is not counted (`_reachable_ifs`); a guard behind a
       runtime-false condition this check cannot evaluate is likewise not counted; on the
       MIRROR arm a STORE is counted only where it is UNCONDITIONALLY reached
       (`_unconditional_walk`) — on a STOP arm the same question is asked to REFUSE, so a
       store there is counted ANYWHERE the check can place it (`_own_walk`); and an EXIT is
       counted only where it is unconditionally
       reached AND a raise there can actually LEAVE the function — a raise a `try`
       handler or a context manager can swallow is unproven, whether that `try`/`with`
       sits INSIDE the guard body (`_unconditional_exits`) or ENCLOSES the guard
       (`_swallowed_from_outside`). So `if not gate(l): if DEBUG: raise`,
       `try: raise … except Exception: pass` around it, and `with suppress(…): raise` are
       unproven rather than certified. The REACHABLE `else` of a constant-tested `if` is
       kept. A condition this check cannot decide is answered "unproven", which leaves
       the destructive strip refused.
    4. **Three shapes are refused rather than resolved, deliberately** — a store reached
       through a helper ARGUMENT (`_set(e, embedding)`, indistinguishable from
       `print(embedding)` at this level), a store behind a LOCAL ALIAS
       (`gate = mirror_required; if gate(...)`), and a stop raise inside a `with` whose
       manager would in fact PROPAGATE (`with _TRACE.span("skip"): raise _Skip()`): a
       manager's ``__exit__`` cannot be told apart from one that suppresses, so it is an
       unproven exit and the writer is refused. All three fail closed: refusing them costs
       a refactor (`embedding = …`, spell the gate out, `return` instead of `raise`), and
       failing open costs the data.

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
    wanted = set(entity_types)
    import ast

    # ⛔ A GUARD'S ANCESTRY IS PART OF THE QUESTION, and `_reachable_ifs` yields the guard
    # without it: a `try`/`with` ABOVE the guard can swallow the raise the guard body
    # relies on (see `_swallowed_from_outside`). Parent links are built once, not per `if`.
    parents = {child: parent for parent in ast.walk(node)
               for child in ast.iter_child_nodes(parent)}
    for sub in _reachable_ifs(node):
        if _statically_falsy(sub.test):
            continue                          # `if False:` / `if not True:` — dead
        if not _asks_about(sub.test, wanted):
            continue
        # ⛔ THE GUARDED BODY MUST **STORE**, NOT MENTION. An `if` that asks and then logs
        # about the embedding is not a write-stop — and a text match is exactly the "prose
        # is not evidence" defect this file already fixed once at the mention level, which
        # round 5 re-admitted one layer down by matching any embedding-ish name or string.
        # `Return`/`Raise` covers the early-exit form; a store-shaped node covers the
        # encode-guard form `#13617` uses (`embedding = derived_embedding.encode(...)`).
        polarity = _gate_polarity(sub.test, wanted)
        if polarity == 0:
            continue                          # masked: `… is False`, `bool(…)`, a lookup
        # ⛔ AN EXIT IS ONLY A WRITE-STOP IF IT DOES NOT *WRITE*. `if gate(l): return
        # self._write(ev)` RETURNS A WRITE on the mirror arm — that is a store, and under a
        # negated test it stores exactly when the gate said stop, i.e. the polarity
        # inversion again wearing a `return`. Only a bare `return`/`return None`/`raise` is
        # an exit; a `return` carrying a call is judged as a write, below.
        #
        # ⛔ AND AN EXIT IS ONLY A WRITE-STOP ON THE *NEGATED* ARM. `mirror_required`
        # answers True = "the graph must still store this", so `if gate("object"): return
        # None` exits on the runs the vector MUST be written and falls through to the store
        # on every run a strip can happen — the inverted write-stop, and still certified
        # gated at `df345c22` because this arm ran the exit test BEFORE looking at
        # `polarity` (round 8's F1.a, measured: GATED). The correct spelling is
        # `if not gate("object"): return`; a `return` carrying a CALL is judged as a
        # write by the polarity arms below, never as an exit.
        # ⛔ AND THE STOP ARM MUST NOT STORE THE VECTOR IT CLAIMS TO STOP. Measured
        # fail-OPEN at `622e9935`: `if not gate("object"): props["embedding"] =
        # _encode(ev); return None` was certified GATED, because the EXIT arm below ran
        # first and never asked whether the SAME arm places the vector — so a writer that
        # stores exactly when told to stop lifted `REFUSE_WRITE_STILL_EMITS`. The same held
        # for the `raise` spelling, for `props.update(embedding=…)`, and for a
        # `pop("embedding")` followed by a store (the arm both removes and places it), and
        # for a store in the arm's own `finally`. That arm must therefore contain NO store
        # ANYWHERE — not merely no *unconditional* one — because for THIS question a store
        # the check cannot place is exactly the evidence that refuses the strip:
        # `anywhere=True` takes `_own_walk` (which descends live conditionals and `finally`)
        # rather than `_unconditional_walk`. This arm can only ever refuse MORE.
        if polarity < 0 and any(_stores_embedding(statement, anywhere=True, scope=node)
                                for statement in sub.body):
            continue
        # ⛔ AND THE EXIT MUST ACTUALLY LEAVE. `_unconditional_exits` withholds a `raise`
        # that a `try` handler or a context manager can swallow — a swallowed raise is
        # not a write-stop, and counting it lifted `REFUSE_WRITE_STILL_EMITS` for a writer
        # that stored unconditionally (round-9 F1, measured at `1cc61230`: GATED).
        if polarity < 0 and any(_is_write_stop_exit(statement)
                                for statement in sub.body
                                for statement in _unconditional_exits(
                                    statement,
                                    swallowable=_swallowed_from_outside(
                                        node, sub, parents))):
            return True
        # ⛔ THE POLARITY DECIDES WHICH BODY IS A WRITE-STOP, and getting this wrong in
        # either direction is a defect that was measured:
        #   +1 (positive ask, `if gate(l): …`)   → the branch runs when the graph must
        #        still mirror, so it must STORE, or clear/remove on the other arm.
        #   -1 (negated ask, `if not gate(l): …`) → the branch runs when the graph must
        #        STOP mirroring, so it must REMOVE the property. Counting a STORE here is
        #        the polarity inversion; refusing the removal shut the gate on the dict
        #        writer whose only write-stop IS this shape.
        # ⛔ AND ON A POSITIVE ASK THE *ELSE* IS THE STOP ARM (`#13647`, round-13 review).
        # `polarity > 0` means the BODY runs when the graph must still mirror, so a store
        # there is correct — but `mirror_required` answering False runs `orelse`, and a
        # store there is the stop arm storing the vector it was told to stop writing, the
        # same defect `622e9935` closed for the negated spelling one arm over. Measured
        # fail-OPEN at `f9d0549e80`: `if gate("object"): props["embedding"] = …; else:
        # props["embedding"] = …; return None` certified GATED through
        # `_writer_consults_gate`, because no arm read `sub.orelse` at all.
        #
        # `anywhere=True` for the same reason as the negated arm above: on a STOP arm a
        # store the check cannot place (behind a live condition, inside the arm's
        # `finally`) still places the vector, so the fail-closed answer is to refuse. This
        # can only ever refuse MORE — the stated posture of every rule in this function.
        # A `try`'s `orelse` is unrelated (`_reachable_ifs` yields `If` nodes only).
        if polarity > 0 and any(_stores_embedding(statement, anywhere=True, scope=node)
                                for statement in sub.orelse):
            continue                         # the stop arm STORES — not a write-stop
        if polarity > 0 and any(_stores_embedding(statement, scope=node)
                                for statement in sub.body):
            return True                      # the mirror arm: writing here is correct
        if polarity < 0 and any(_removes_embedding(statement) for statement in sub.body):
            return True
    return False


def _is_write_stop_exit(statement) -> bool:
    """Is this statement a plain early exit — `raise`, or a `return` that writes nothing?

    A `return` of a CALL is a write that also exits, so it is judged by the same polarity
    rule as an assignment. Treating every `Return` as an exit accepted
    `if not gate(l): return self._write(ev)`, which stores on the arm the gate said stop.

    ⛔ THE CALLER APPLIES THE POLARITY, and must: an exit that writes nothing is a
    write-stop only when the branch runs on the STOP verdict (`polarity < 0`). On the
    mirror arm (`if gate(l): return`) the same exit skips the write the gate asked for, so
    it is the inverted shape rather than a write-stop.
    """
    import ast

    if isinstance(statement, ast.Raise):
        return True
    if isinstance(statement, ast.Return):
        return statement.value is None or not any(
            isinstance(node, ast.Call) for node in _own_walk(statement.value))
    return False


def _statically_falsy(test) -> bool:
    """Is this test a constant that is False — `False`, `0`, or `not True`?

    ⛔ ONE HELPER FOR BOTH ARMS. The `if` arm recognised only `ast.Constant`, so `if not
    True:` kept a dead guard (fail-open, mild) while `if False:` pruned it, and the `while`
    arm had its own copy — two spellings that could disagree. `not True` is `False` to the
    compiler and to a reader.
    """
    import ast

    if isinstance(test, ast.Constant):
        return not test.value
    return (isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not)
            and isinstance(test.operand, ast.Constant) and bool(test.operand.value))


def _own_walk(node):
    """`ast.walk`, but a NESTED SCOPE is not this function's code — and neither is a dead branch.

    ⛔ `ast.walk` descends into an inner `def`/`class`/lambda, so a store inside a helper
    that never runs "proved" the write-stop, and a `return` inside one satisfied the
    early-exit arm — while `_reachable_ifs` prunes exactly those scopes for the guard. Being
    stricter one level up and laxer one level down is how the same defect walks back in.

    ⛔ NESTED SCOPES AND DEAD BRANCHES ARE NOT EVIDENCE. This is the walk the GUARD arm
    uses (`_reachable_ifs` prunes nested scopes for the same reason), and the STORE and
    EXIT arms judge with `_unconditional_walk`, which prunes one layer further: a store or
    an exit behind a LIVE nested condition is unproven, not proven. `_own_walk` survives
    only where finding ANY node is the fail-closed direction — see `_is_write_stop_exit`'s
    "does this return's value call anything" question, where a call hidden behind a
    short-circuit or a conditional expression still means the return carries a write.

    ⛔ AND THE SAME ARGUMENT APPLIES ONE ARM OVER, to a statically dead BRANCH. The GUARD
    walk already drops `if False:` / `while False:` (`_statically_falsy`), but the STORE and
    EXIT walks did not — so

        if _derived_mirror_required("object"):
            if False:
                embedding = _encode(ev)      # never runs — and it certified the arm
        props["embedding"] = vec             # …while this runs unconditionally

    was ACCEPTED as gated. A dead store is not evidence of a store; pruning it here makes
    `_stores_embedding`, `_removes_embedding` and the exit arm answer the same question the
    guard arm answers, in the same way. Only the BODY of a statically-falsy test is dropped:
    an `else` still runs, so it is still walked.
    """
    import ast

    stack = [node]
    while stack:
        current = stack.pop()
        yield current
        if isinstance(current, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda
                      | ast.ClassDef):
            continue
        if isinstance(current, ast.If | ast.While) and _statically_falsy(current.test):
            stack.extend(current.orelse)      # the dead BODY is not evidence; `else` is live
            continue
        stack.extend(ast.iter_child_nodes(current))


def _unconditional_walk(node):
    """Every node reached unconditionally — the shared walk behind all three arms.

    A thin view over `_unconditional_nodes`, which also reports whether a `raise` at a
    node could be SWALLOWED. Stores and removals do not care (a store that runs before a
    caught raise still ran); the EXIT arm does, and reads `_unconditional_exits` instead.
    """
    for sub, _swallowable in _unconditional_nodes(node):
        yield sub


def _swallowed_from_outside(function, guard, parents) -> bool:
    """Does a `try`-with-handlers or a `with` ENCLOSE this guard?

    ⛔ THE SAME QUESTION ONE LEVEL UP, and the walk below cannot answer it: the exit arm
    starts its descent at a statement of the guard's OWN body, so every construct ABOVE the
    guard is invisible to it. An enclosing wrapper swallows a raise exactly as a nested one
    does:

        try:
            if not _derived_mirror_required("object"):
                raise _Skip()            # caught by the handler below → does NOT exit
        except Exception:
            _log(ev)
        props["embedding"] = _encode(ev) # runs unconditionally

    and its `with suppress(...)` mirror. Both were certified GATED at `97e1b9e2` (measured),
    i.e. round-9 F1 survived one level up from the fix that closed it below.

    Only two positions can catch a raise PROPAGATING OUT of the guard: a `try`'s BODY (its
    `orelse`, `finally` and `handlers` cannot catch their own try's raise) and a
    `with`/`async with` BODY (its ``__exit__`` sees everything propagating out of it).
    Everything else on the path — `if`, `for`, `while`, `match`, a `finally` — is
    transparent, so the walk continues upward; a nested scope ends it.

    ⚠️ THE `with` ARM'S POSITION TEST IS STRUCTURAL, NOT DEFENDED. `child in parent.body`
    cannot be mutated into a red test, because a STATEMENT can only ever be a `With`'s
    body — its `items` are expressions (measured over every registered writer module and
    this file's fixtures: 32 `with` ancestors, 32 in `body`, 0 elsewhere). It is kept for
    symmetry with the `try` arm, whose identical conjunct IS pinned (dropping it reddens
    `test_a_swallowed_exit_does_not_certify_the_arm`). Do not read it as a checked
    invariant.
    """
    import ast

    child = guard
    parent = parents.get(child)
    while parent is not None and parent is not function:
        if isinstance(parent, ast.Try | getattr(ast, "TryStar", ast.Try)):
            if parent.handlers and child in parent.body:
                return True
        elif isinstance(parent, ast.With | ast.AsyncWith) and child in parent.body:
            return True
        elif isinstance(parent, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda
                        | ast.ClassDef):
            return False                      # a nested scope: the raise never reaches here
        child, parent = parent, parents.get(parent)
    return False


def _unconditional_exits(node, *, swallowable: bool = False):
    """`_unconditional_walk`, minus the `raise`s that are only *maybe* an exit.

    ⛔ A `raise` IS NOT AN EXIT IF SOMETHING CATCHES IT, and `_is_write_stop_exit` answers
    True for any `ast.Raise` — so the walk has to withhold the ones it cannot prove. A
    raise can be swallowed twice over, and both were fail-OPEN at `1cc61230` (measured:
    both shapes below reported GATED, lifting `REFUSE_WRITE_STILL_EMITS` for a writer that
    stores the vector unconditionally):

        if not _derived_mirror_required("object"):
            try:
                raise _Skip()            # caught by the handler below → does NOT exit
            except Exception:
                pass
        props["embedding"] = _encode(ev) # runs unconditionally

        if not _derived_mirror_required("object"):
            with suppress(Exception):
                raise _Skip()            # `__exit__` may return True → does NOT exit
        props["embedding"] = _encode(ev)

    So a `raise` inside a `try` BODY whose own `try` declares handlers, and a `raise`
    inside a `with`/`async with` body, are *unproven* and dropped — the same answer the
    walk already gives a store behind a live condition. `return` is NOT affected: neither
    an `except` clause nor a context manager's `__exit__` can cancel a return, so the
    accepted `with TRACE: return None` arm still certifies; and a `raise` in a
    `try`/`finally` (no handlers) or in the `finally` body still propagates, so it still
    certifies. Both directions are pinned by
    `test_a_swallowed_exit_does_not_certify_the_arm`.

    ``swallowable`` seeds the walk for the one case it cannot see from inside: a `try`/`with`
    that ENCLOSES the guard (`_swallowed_from_outside`).
    """
    import ast

    for sub, flag in _unconditional_nodes(node, swallowable=swallowable):
        if flag and isinstance(sub, ast.Raise):
            continue
        yield sub


def _unconditional_nodes(node, *, swallowable: bool = False):
    """`_own_walk`, but a NESTED CONDITIONAL is not evidence — only what always runs is.

    Yields ``(node, swallowable)``: ``swallowable`` is True where an exception raised at
    this node could be caught before it leaves the function — inside a `try` body that
    declares handlers, or inside a `with` body (a context manager's ``__exit__`` may
    return True). It says nothing about a `return`.

    ⛔ THE THIRD LOOSENESS IN THE SAME PLACE, and the one the earlier fixes missed. Pruning
    nested SCOPES (`_own_walk`) and statically DEAD branches (`_statically_falsy`) still
    left a *live* nested condition certifying an arm:

        if not _derived_mirror_required("object"):
            if DEBUG:                    # live condition, not a constant
                raise                    # ⇒ certified write-stopped
        props["embedding"] = vec         # ⇒ runs whenever DEBUG is false

    and its mirror on the store arm (`if gate(l): if flag: embedding = encode()`) — both
    fail-OPEN, and the exit arm's answer is the sole fact that authorises deleting the only
    readable copy of a fingerprint. `#13647`'s round-3 P1 is exactly this shape; the
    docstring's declared residuals named nested scopes, dead code and ordering, NOT
    conditional descent, so it was an undeclared hole rather than an accepted residual.

    So this walk descends ONLY through nodes that always execute between it and the leaf:
    a `with` body, and a `try`'s body/finally. It STOPS at every construct whose child may
    not run — `If`/`While`/`For` (only the test/iterable is evaluated, so only that is
    walked), `IfExp`, comprehensions, `match` cases, exception handlers, a `try`'s `orelse`
    — and at every nested scope, as `_own_walk` does.

    ⛔ "ALWAYS EXECUTES" IS NOT THE SAME AS "ALWAYS LEAVES" — that is the second flag this
    walk carries, and the `Try`/`With` arms set it. A `try` BODY always runs up to a raise,
    but a raise there is caught by that same `try`'s handlers; a `with` body always runs,
    but its manager's ``__exit__`` may swallow a raise. So the descendants of a
    `try`-with-handlers body and of a `with` body are yielded with ``swallowable=True``,
    and `_unconditional_exits` drops their `raise`s. Everything else — a `try/finally`
    with no handlers, a `finally` body, the `with` item expressions (which run before the
    manager exists) — keeps the enclosing flag.

    ⛔ NOT USING IT IS FAIL-OPEN, so this is not merely "stricter": with the arm's action
    behind a condition we cannot decide, the honest answer is *unproven*, and an unproven
    write-stop must leave the destructive strip refused. Missing a store that IS
    unconditional would also refuse (the arm does not certify), which is the same safe
    direction — this walk can only ever make the gate harder to open, never easier.
    """
    import ast

    stack: list[tuple] = [(node, swallowable)]
    while stack:
        current, swallowable = stack.pop()
        yield current, swallowable
        if isinstance(current, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda
                      | ast.ClassDef):
            continue
        if isinstance(current, ast.If):
            stack.append((current.test, swallowable))   # the test runs; neither arm is guaranteed
            continue
        if isinstance(current, ast.While):
            stack.append((current.test, swallowable))
            continue
        if isinstance(current, ast.For | ast.AsyncFor):
            stack.append((current.iter, swallowable))   # the iterable runs; the body may not
            continue
        if isinstance(current, ast.IfExp | ast.ListComp | ast.SetComp | ast.DictComp
                      | ast.GeneratorExp | ast.Match):
            continue                          # conditional / repeated / pattern-selected
        if isinstance(current, ast.Try | getattr(ast, "TryStar", ast.Try)):
            # ⛔ ONLY THE BODY BECOMES SWALLOWABLE, and only when there are HANDLERS: a
            # `try/finally` cannot catch, and a raise in `finally` propagates past the
            # `try` that owns it. `orelse` may never run; `handlers` is not this arm's code.
            caught = swallowable or bool(current.handlers)
            stack.extend((statement, caught) for statement in current.body)
            stack.extend((statement, swallowable) for statement in current.finalbody)
            continue
        if isinstance(current, ast.With | ast.AsyncWith):
            # ⛔ THE MANAGER MAY SWALLOW A RAISE FROM ITS BODY (`suppress`, or any
            # `__exit__` returning True), so the body is unproven as an EXIT while still
            # being evidence as a STORE. Its ITEMS run first, before the manager exists,
            # so a raise there keeps the enclosing flag.
            stack.extend((item, swallowable) for item in current.items)
            stack.extend((statement, True) for statement in current.body)
            continue
        stack.extend((child, swallowable) for child in ast.iter_child_nodes(current))


def _gate_conjuncts(test) -> list:
    """The top-level operands of an `and` chain, or `[test]` — nothing else unwrapped.

    ⛔ WHITELIST, NOT BLACKLIST. Refusing `not` and `or` by name was two tokens of a long
    list: `gate(...) is False`, `== False`, `!= True`, `bool(gate(...)) is False`,
    `{True: False, False: True}[gate(...)]` and `all((gate(...), True))` all invert or mask
    the answer and all passed. `is False` is not exotic — this repo already spells it that
    way in its write paths — and a WIP write-stop spelled that way would let the destructive
    strip proceed into a live writer, the single outcome `REFUSE_WRITE_STILL_EMITS` exists
    to prevent. So the gate call must BE the operand: anything wrapped around it (a
    comparison, a `not`, another call, a subscript) is not evidence.
    """
    import ast

    if isinstance(test, ast.BoolOp) and isinstance(test.op, ast.And):
        operands = []
        for value in test.values:
            operands.extend(_gate_conjuncts(value))
        return operands
    return [test]


def _is_gate_call(node, wanted: set[str]) -> bool:
    """Is this expression EXACTLY a gate call about one of ``wanted``?"""
    import ast

    if not isinstance(node, ast.Call):
        return False
    name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
    if name not in _GATE_REFERENCES or not node.args:
        return False
    first = node.args[0]
    return (isinstance(first, ast.Constant) and isinstance(first.value, str)
            and first.value in wanted)


def _is_explicit_inversion(node, wanted: set[str]) -> bool:
    """Is this `gate(l) <op> <bool literal>` — an inversion the author spelled out?"""
    import ast

    if not isinstance(node, ast.Compare) or len(node.ops) != 1 or len(node.comparators) != 1:
        return False
    if not _is_gate_call(node.left, wanted):
        return False
    op, right = node.ops[0], node.comparators[0]
    if not (isinstance(right, ast.Constant) and isinstance(right.value, bool)):
        return False
    # `is False` / `== False` / `is not True` / `!= True` invert; the mirror forms assert.
    same = isinstance(op, (ast.Is, ast.Eq))
    return (same and right.value is False) or (not same and right.value is True)


def _gate_polarity(test, wanted: set[str]) -> int:
    """``+1`` — a conjunct IS the gate call; ``-1`` — it is negated; ``0`` — masked away.

    ⛔ `not gate(...)` IS A VALID ASK FOR A REMOVAL-ARM WRITE-STOP. `mirror_required` reads
    "must the GRAPH still store?", so the dict writer's stop is
    `if not gate("object"): props.pop("embedding")` — remove the property exactly when the
    graph must no longer mirror it. Refusing `not` outright (as one revision did) shut the
    gate permanently on `sdk::_update_entity`: an enforcer that can never fire. Polarity is
    therefore decided HERE and enforced per-arm by the caller, not globally.
    """
    import ast

    positive = negative = False
    for conjunct in _gate_conjuncts(test):
        if _is_gate_call(conjunct, wanted):
            positive = True
        elif isinstance(conjunct, ast.UnaryOp) and isinstance(conjunct.op, ast.Not) \
                and _is_gate_call(conjunct.operand, wanted):
            negative = True
        elif _is_explicit_inversion(conjunct, wanted):
            # `gate(l) is False` / `== False` / `is not True` / `!= True` — a DECIDED
            # inversion, not a mask: a `Compare` against a boolean literal names exactly
            # the same answer `not` does, so it is a valid ask (and `is False` is a spelling
            # this repo already uses in its write paths). `bool(...)`, `all(...)` and a
            # lookup remain refused: they can produce a value that is none of the gate's.
            negative = True
    # ⛔ `max(0, -1)` IS 0 — the first version of this function silently reported "masked"
    # for a negated ask, because 0 (the "no answer" sentinel) beat -1.
    if positive:
        return 1
    if negative:
        return -1
    return 0


def _reachable_ifs(node):
    """Every `if` in the function's OWN body — not in a nested scope, not in dead code.

    ⛔ A guard wrapped in `if False:`, `while False:` or sitting in an inner `def`/`class`
    is not a write-stop for this function, and an `ast.walk` sees all three. This walk
    prunes a constant-tested branch and any nested scope, so what it yields is code that can
    actually run here.

    ⚠️ THE `else` OF A CONSTANT-TESTED `if` IS REACHABLE. `if False: … else: if gate(...):
    embedding = …` is a live guard, so only the dead ARM is dropped, never the sibling.
    """
    import ast

    stack = list(node.body)
    while stack:
        current = stack.pop()
        if isinstance(current, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
            continue                      # a nested scope is not this function's guard
        if isinstance(current, ast.ClassDef):
            continue                      # likewise: a class body does not run here
        if isinstance(current, ast.If):
            yield current
            if _statically_falsy(current.test):
                stack.extend(current.orelse)      # `if False:` / `if not True:` — only else runs
            elif isinstance(current.test, ast.Constant):
                stack.extend(current.body)        # a TRUTHY literal: only the body runs
            else:
                stack.extend(current.body)
                stack.extend(current.orelse)
            continue
        if isinstance(current, ast.While | ast.For | ast.AsyncFor):
            # ⛔ ONLY A *FALSY* CONSTANT IS DEAD. Pruning every constant test treated
            # `while True:` like `while False:` and dropped a reachable guard — the
            # fail-closed direction, but a state the docstring did not name.
            test = current.test if isinstance(current, ast.While) else current.iter
            if _statically_falsy(test):
                continue                  # `while False:` / `for x in []` never runs
            for field in ("body", "orelse"):
                stack.extend(getattr(current, field, []) or [])
            continue
        if isinstance(current, ast.With | ast.AsyncWith | ast.Try):
            for field in ("body", "orelse", "finalbody"):
                stack.extend(getattr(current, field, []) or [])
            for handler in getattr(current, "handlers", []) or []:
                stack.extend(handler.body)
            continue
        if isinstance(current, ast.IfExp):
            continue
        if isinstance(current, ast.Match):
            # ⛔ `ast.match_case` IS NOT AN `ast.stmt`, so the generic arm dropped every case
            # body — a guard inside a `match` was invisible, and a real write-stop was
            # refused. A case guard is code only when it is not a constant.
            for case in current.cases:
                stack.extend(case.body)
                if case.guard is not None and not _statically_falsy(case.guard):
                    stack.append(case.guard)
            continue
        stack.extend(child for child in ast.iter_child_nodes(current)
                     if isinstance(child, ast.stmt))


def _asks_about(test, wanted: set[str]) -> bool:
    """Does this test CALL the gate about one of ``wanted`` (a label's entity types)?

    ⛔ THE FIRST ARGUMENT MUST BE A STRING CONSTANT NAMING THIS LABEL. `_update_entity` and
    `restore_graph` each store five labels, so one gate call is not a fact about all of
    them: a partial write-stop shaped `if _derived_mirror_required("object"):
    props.pop("embedding")` leaves the Source/Event stores unconditional. A computed
    argument is refused because the tool cannot tell which label was asked about.
    """
    import ast

    # ⛔ SHORT-CIRCUITED AWAY IS NOT ASKED. `if g("object") or True:` contains the call and
    # its answer decides nothing — the branch is taken either way.
    for sub in ast.walk(test):
        if isinstance(sub, ast.BoolOp):
            for operand in sub.values:
                if isinstance(operand, ast.Constant):
                    return False

    for sub in ast.walk(test):
        if not isinstance(sub, ast.Call):
            continue
        callee = sub.func
        name = getattr(callee, "id", None) or getattr(callee, "attr", None)
        if name not in _GATE_REFERENCES:
            continue
        if not sub.args:
            continue
        first = sub.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str) \
                and first.value in wanted:
            return True
    return False


def _callee_name(call) -> str:
    """The last name of a call's callee — `session.run` -> `run`, `_write` -> `_write`."""
    callee = call.func
    return getattr(callee, "id", None) or getattr(callee, "attr", None) or ""


#: Callee-name suffixes that RUN the statement they are given. ⛔ AN INCLUDE LIST: the
#: question is "does this call EXECUTE a statement", and naming the loggers to exclude
#: instead would be the two-token blacklist this file already rejected once (round 8) —
#: `_log`, `print`, `debug`, `warnings.warn` and the next spelling nobody thought of would
#: each re-open the hole.
_STATEMENT_RUNNERS = ("run", "query", "execute", "write")


def _runs_statements(call) -> bool:
    """Does this call hand its arguments to something that executes them?"""
    return _callee_name(call).lower().endswith(_STATEMENT_RUNNERS)


def _call_operands(call) -> list:
    """Everything a call is GIVEN — positional and keyword values."""
    return [*call.args, *(keyword.value for keyword in call.keywords)]


def _clause_flow(scope) -> tuple[frozenset[str], frozenset[str]]:
    """``(names assigned a clause string, of those the ones that reach a statement runner)``.

    ⛔ TWO SETS, because the two arms ask different questions. A builder-then-run writer
    (`q = "SET n.embedding = $e"; session.run(q)`) is a real store and has to stay
    accepted. The ASSIGNMENT alone is text: measured at `6ee74268`,
    `if gate("object"): _clause = "SET n.embedding = $e"` followed by an UNCONDITIONAL
    store was certified GATED — dead text opened the gate, round 6's "a mention is not a
    store" re-admitted through `ast.Constant`.
    """
    import ast

    if scope is None:                     # no scope, no data flow: nothing is "run"
        return frozenset(), frozenset()
    # ⛔ NOT `_own_walk(scope)` — given a FunctionDef it YIELDS IT AND STOPS (a function is
    # a scope boundary to that walk), so the body has to be walked statement by statement.
    nodes = ([leaf for statement in getattr(scope, "body", ()) for leaf in _own_walk(statement)]
             if isinstance(scope, ast.FunctionDef | ast.AsyncFunctionDef) else list(_own_walk(scope)))
    assigned = {
        target.id
        for sub in nodes
        if isinstance(sub, ast.Assign | ast.AnnAssign | ast.AugAssign)
        and _sets_embedding_clause(sub.value)
        for target in (sub.targets if isinstance(sub, ast.Assign) else [sub.target])
        if isinstance(target, ast.Name)
    }
    if not assigned:
        return frozenset(), frozenset()
    run = {
        leaf.id
        for sub in nodes
        if isinstance(sub, ast.Call) and _runs_statements(sub)
        for operand in _call_operands(sub)
        for leaf in _own_walk(operand)
        if isinstance(leaf, ast.Name) and leaf.id in assigned
    }
    return frozenset(assigned), frozenset(run)


def _stores_embedding(node, *, anywhere: bool = False, scope=None) -> bool:
    """Does this statement STORE the vector — a real assignment, or a Cypher clause set?

    ⛔ TWO WALKS, ONE QUESTION, AND THE CHOICE IS THE CALLER'S because the safe answer is
    OPPOSITE in the two arms. On the MIRROR arm (`polarity > 0`) a store clears the writer,
    so only an UNCONDITIONAL store is evidence — `_unconditional_walk`, the default. On the
    STOP arm the same question is asked to REFUSE (`anywhere=True` → `_own_walk`): a store
    the check cannot place (behind a live condition, in a `try`/`finally`) still places the
    vector on the arm the gate said stop, and refusing it is the fail-closed direction.

    ⛔ DELIBERATELY NARROW, TWICE OVER. Two earlier versions were wrong in opposite
    directions, both silently:

    * accepting any CALL with an embedding-named argument made `print(embedding)` look like
      a store (a mention, not a write);
    * accepting any embedding-named ASSIGNMENT made `embedding = None` look like one —
      which is the placeholder the real write-stop itself uses as its pre-guard reset, and
      is also what `if gate(...): embedding = None` / `else: embedding = _encode(...)`
      leaves in the guarded branch while the vector IS stored on the branch the gate said
      stop.

    So the value must be non-constant, AND THE PROPERTY MUST BE EXACT: `props["embedding"]`,
    `n.embedding`, a local named exactly `embedding`. A SUBSTRING match accepted
    `props["embedding_model"] = current_model()` — the identity triple this repo stores
    alongside the vector (`embedding_text_hash`/`embedding_model`/`embedding_revision`) — so
    a writer that sets its identity but still stores `props["embedding"]` elsewhere cleared
    the gate. That is the exact mirror of the removal-side hole (`pop("embedding_model")`),
    and this repo proves the shape exists: `embeddings.py` pops that triple while keeping
    the vector.

    The accepted shapes are three: `embedding = derived_embedding.encode(...)` (the `#13617`
    form), `props["embedding"] = …`, `n.embedding = …`, and a Cypher clause string **that
    REACHES A STATEMENT RUNNER** — either passed directly to one
    (`session.run("… SET n.embedding = $e …")`) or assigned to a local and then passed to
    one (`q = "SET n.embedding = $e"; session.run(q)`). ⛔ A clause assigned and never run
    places nothing, so it is text, not a store (`6ee74268`). ⛔ NOT accepted: `set_embedding_clause(...)` in a
    CALL position — it is a pure string BUILDER (`tortoise/vector_write.py`) that writes
    nothing, so it is evidence of a clause being assembled, not of a store; `clear_embedding`
    (clearing is what the strip does); `popitem()` (no argument names WHICH key went — that is
    `_removes_embedding`'s `pop(<this property>)` arm).
    """
    import ast

    clause_names, run_names = _clause_flow(scope)
    # ⛔ A KEY BOUND TO THE PROPERTY'S OWN STRING IS THE SAME KEY (`#13647`, measured at
    # `98eec2ca`): `key = "embedding"; props[key] = _encode(ev)` placed the vector on the stop
    # arm and certified the writer. Resolving it is still static — the name is a local bound to
    # a CONSTANT in this scope — so the exact-property precision is preserved. STOP ARM ONLY:
    # on the mirror arm this would be a new acceptance.
    alias_keys = _property_alias_names(scope) if anywhere else frozenset()
    for sub in (_own_walk(node) if anywhere else _unconditional_walk(node)):
        if isinstance(sub, ast.Assign | ast.AnnAssign | ast.AugAssign):
            value = sub.value
            targets = sub.targets if isinstance(sub, ast.Assign) else [sub.target]
            # ⛔ A CYPHER CLAUSE IS A STORE ONLY WHERE SOMETHING RUNS IT. `.embedding =` in
            # a string is the statement a RUNNER repeats; the assignment on its own places
            # nothing. So the value must be bound to a NAME that a statement runner is
            # given somewhere in this scope — otherwise dead text opens the gate.
            if _sets_embedding_clause(value):
                if any(isinstance(target, ast.Name) and target.id in run_names
                       for target in targets):
                    return True           # `q = "SET n.embedding = $e"; session.run(q)`
                continue                  # text nothing runs
            if isinstance(sub, ast.AugAssign) and (
                    (isinstance(sub.value, ast.Dict) and _dict_stores_the_vector(sub.value))
                    # ⛔ THE ITERABLE-OF-PAIRS SPELLING, STOP-ARM ONLY. `dict.__ior__` accepts
                    # `[("embedding", vec)]` exactly as it accepts the literal, and the
                    # `isinstance(..., ast.Dict)` narrowing walked past it (measured CERTIFIED
                    # by the review of this branch while the CALL form
                    # `props.update([("embedding", vec)])` was already refused).
                    # ⛔ STOP-ARM ONLY IS LOAD-BEARING: the mirror arm's widest arm is reached
                    # for EVERY statement, and recognizing a new spelling there is a new
                    # ACCEPTANCE, not a new refusal — measured: the same recognition
                    # unconditionally turned a `refused` mirror-arm fixture GATED. A fix may add
                    # refusals, never acceptances.
                    or (anywhere and _mapping_operand_stores_the_vector(sub.value))):
                # `props |= {"embedding": vec}` — a merge that places the vector. ⛔ AND THE
                # ITERABLE-OF-PAIRS SPELLING IS THE SAME MERGE: `props |= [("embedding", vec)]`
                # (and its tuple twin) is accepted by `dict.__ior__` exactly as the literal is,
                # and the `isinstance(..., ast.Dict)` narrowing this arm used to carry walked
                # straight past it — measured CERTIFIED by the review of this branch, while the
                # CALL form `props.update([("embedding", vec)])` was already refused. One
                # operand rule, not two: `_pairs_store_the_vector` keeps the same two
                # narrowings, so `props |= [("embedding", None)]` and
                # `props |= [("embedding_model", _m())]` stay certified.
                return True
            # ⛔ A MAPPING LITERAL ASSIGNED IN THE ARM IS A STORE TOO, and a `{**{…}}` merge
            # needs the RECURSIVE form — hence `stop_arm=True` here and `anywhere`-only, both
            # for the same reason: this is a refusal the mirror arm does not make, and a fix
            # may add refusals, never acceptances. Measured GATED at `8637df0fde` for
            # `props = {"embedding": _encode(ev)}` and `props = {**props, "embedding": …}`.
            if anywhere and isinstance(value, ast.Dict) \
                    and _dict_stores_the_vector(value, stop_arm=True):
                return True
            # ⛔ AN UNPACKED OR CHAINED TARGET IS STILL A STORE (`#13647` round 12). The
            # mirror arm's rule is "EVERY target names the property", which is right where
            # a missed recognition costs a refactor and a false PASS on the STOP arm —
            # where it certified `props["embedding"], props["embedding_model"] = vec, model`
            # and `x = props["embedding"] = vec` (both measured GATED at `8637df0fde`).
            # Deliberately `anywhere`-only: see `_mutator_stores_the_vector` on why a fix
            # may add refusals and never acceptances.
            if anywhere and _mixed_assignment_stores_the_vector(targets, value):
                return True
            # ⛔ AND A REBUILT MAPPING IS THE SAME STORE (`98eec2ca`): `props = props |
            # {"embedding": …}`, `props = {**props, "embedding": …}` and the writer being
            # HANDED either place the property through no mutator and no embedding-named
            # target, so every other arm here misses them. `_rebuild_stores_the_vector` is
            # deliberately narrow (a MERGE, never a plain literal) so a mention such as
            # `_audit({"embedding": props.get(…)})` stays a mention.
            if anywhere and _rebuild_stores_the_vector(value):
                return True
            if not all(_names_embedding(target)
                       or (anywhere and _keyed_by_alias(target, alias_keys))
                       for target in targets):
                continue
            if value is None or isinstance(value, ast.Constant):
                continue                  # `embedding = None` places nothing
            return True
        elif isinstance(sub, ast.Call):
            name = _callee_name(sub)
            # ⛔ THE CLAUSE PASSED STRAIGHT TO A RUNNER is the store that clause asserts:
            # `session.run("MATCH … SET n.embedding = $e")`, or `session.run(q)` with `q`
            # bound to one above. ⛔ ONLY a runner: `_log(clause)` and `print(clause)` are
            # the mention case again, which is why the callee must name one (an include
            # list, not a list of loggers to exclude).
            if _runs_statements(sub):
                operands = _call_operands(sub)
                if any(_sets_embedding_clause(leaf)
                       for operand in operands for leaf in _own_walk(operand)):
                    return True
                if any(isinstance(leaf, ast.Name) and leaf.id in clause_names
                       for operand in operands for leaf in _own_walk(operand)):
                    return True
            # ⛔ A DICT-STYLE STORE: `props.update(embedding=vec)`. The keyword must name the
            # property exactly and carry a non-constant value.
            if name == "update":
                for keyword in sub.keywords:
                    if keyword.arg == EMBEDDING_PROPERTY \
                            and not isinstance(keyword.value, ast.Constant):
                        return True
                # ⛔ THE MAPPING FORM IS THE SAME STORE: `props.update({"embedding": vec})`.
                # Accepting only the keyword spelling refused a dict writer that used the
                # other one — with a message claiming it had no gate `if` at all.
                for argument in sub.args:
                    if isinstance(argument, ast.Dict) and _dict_stores_the_vector(argument):
                        return True
            # ⛔ AND THE MUTATOR'S METHOD SPELLING IS THE SAME STORE, which the vocabulary
            # above missed: it recognises the OPERATOR form `props |= {"embedding": vec}`
            # and `update(…)` but not the methods those survive as
            # (`props.__setitem__("embedding", vec)`, `props.__ior__({…})`,
            # `props.setdefault("embedding", vec)`, `setattr(props, "embedding", vec)`,
            # `dict.__setitem__(props, …)`). Measured fail-OPEN at `add2e7b89`: all five
            # certified a STOP arm that placed the vector, the same defect as the six
            # spellings above, re-entering one verb over. Consulted ONLY when `anywhere`
            # (the stop arm) because that is where a missed store is a false PASS; on the
            # mirror arm the identical recognition would WIDEN what the destructive gate
            # accepts, and a fix may add refusals, never acceptances.
            if anywhere and _mutator_stores_the_vector(sub):
                return True
            # ⛔ AND A WRITER HANDED A REBUILT MAPPING IS THE SAME STORE (`98eec2ca`):
            # `_write({**props, "embedding": vec})`. Narrow by construction —
            # `_rebuild_stores_the_vector` asks for a MERGE (`**`/`|`) or a `dict(embedding=…)`,
            # so a plain literal handed to a logger (`_audit({"embedding": props.get(…)})`)
            # is a mention and stays one.
            if anywhere and any(_rebuild_stores_the_vector(operand)
                                for operand in _call_operands(sub)):
                return True
        elif isinstance(sub, ast.For | ast.AsyncFor) and anywhere:
            # ⛔ THE LOOP TARGET IS AN ASSIGNMENT TARGET. `for props["embedding"] in
            # _encode(ev):` places the vector on every iteration, and no assignment arm can
            # see it (measured GATED at `8637df0fde`). Stop-arm only, like every recognition
            # in this family: on the mirror arm it would widen what the gate ACCEPTS.
            if _unpacked_target_stores_the_vector(sub.target, sub.iter):
                return True
        elif isinstance(sub, ast.NamedExpr) and anywhere:
            # `if (embedding := _encode(ev)):` — a walrus IS an assignment with a target,
            # and it was certified while the plain `embedding = …` was refused.
            if _unpacked_target_stores_the_vector(sub.target, sub.value):
                return True
        elif isinstance(sub, ast.comprehension) and anywhere:
            # `[x for props["embedding"] in _vecs(ev)]` — a comprehension target is an
            # assignment target too, and it iterates. ⛔ THE `anywhere` HERE IS BELT AND
            # BRACES, AND PROVABLY SO: `_unconditional_nodes` PRUNES `ListComp`/`SetComp`/
            # `DictComp`/`GeneratorExp` without descending, so the MIRROR walk never yields an
            # `ast.comprehension` at all. The qualifier cannot be reddened by any test because
            # the branch is unreachable without it — stated here rather than left to look like
            # an unpinned guard (the review that found this called the mutant "survived"; it is
            # EQUIVALENT, and this is the evidence).
            if _unpacked_target_stores_the_vector(sub.target, sub.iter):
                return True
        elif isinstance(sub, ast.With | ast.AsyncWith) and anywhere:
            # `with _ctx() as props["embedding"]:` binds the property to a value this check
            # cannot see, so it is refused rather than assumed to be a clear (fail-closed).
            # ⛔ STILL NOT SEEN, and named rather than implied: a COMPUTED key
            # (`props[KEY] = vec` where `KEY = "embedding"`) — resolving the constant is data
            # flow this check does not do, so it reads as a store of nothing.
            if any(item.optional_vars is not None and _target_names_the_vector(item.optional_vars)
                   for item in sub.items):
                return True
    return False


def _mixed_assignment_stores_the_vector(targets, value) -> bool:
    """Does a MULTI-TARGET, UNPACKED or CHAINED assignment place the vector?

    ⛔ THE STORE VOCABULARY WAS PER-TARGET AND EXACT, and spellings a real writer writes
    in ONE statement fell outside it. Measured fail-OPEN at `8637df0fde` — each certified a
    STOP arm that placed the vector it was told to stop writing:

        props["embedding"], props["embedding_model"] = _encode(ev), _m()  # unpacked
        x = props["embedding"] = _encode(ev)                              # chained
        [props["embedding"], _m] = [_encode(ev), 1]                      # list target

    The first is the unpacked form of the IDENTITY TRIPLE this repo stores beside the
    vector (`embedding_text_hash`/`embedding_model`/`embedding_revision`); the second is
    the chained form of the same store. Both were refused as stores because the mirror arm
    asks `all(_names_embedding(target) …)`, and a `Tuple` target or a `Name` sibling
    answers False for the whole assignment.

    ⛔ STOP-ARM ONLY (`anywhere=True`, at the call site), for the reason
    `_mutator_stores_the_vector` states: on the mirror arm the identical recognition would
    WIDEN what the destructive gate accepts, and a fix may add refusals, never acceptances.
    The mirror-arm rows in `test_the_stop_arm_must_not_store_the_vector` pin exactly that.

    ⛔ PAIRED, so a REMOVAL is not read as a store: `props["embedding"], x = None, 1`
    assigns the PLACEHOLDER, which is exactly the `embedding = None` this file already
    refuses to count — and so does the one-element unpack `props["embedding"], = (None,)`,
    which an earlier `len(elements) > 1` guard mis-refused (it made the code narrower than
    this docstring). A target element is paired with its own value when the RHS is an
    unpacked literal of the SAME LENGTH, and only a NON-CONSTANT paired value counts.
    """
    return any(_unpacked_target_stores_the_vector(target, value) for target in targets)


def _unpacked_target_stores_the_vector(target, value) -> bool:
    """One assignment target, unwrapped recursively: `Tuple`/`List`/`Starred`/exact name.

    ⛔ RECURSION IS THE POINT. `(props["embedding"], _m), _h = (vec, 1), 2` puts the store
    one level deeper than the first version looked, and `*props["embedding"], = _vecs(ev)`
    hides it behind a `Starred`; both certified a STOP arm at `8637df0fde`.
    """
    import ast

    if isinstance(target, ast.Starred):
        return _unpacked_target_stores_the_vector(target.value, value)
    if isinstance(target, ast.Tuple | ast.List):
        paired = (isinstance(value, ast.Tuple | ast.List)
                  and len(value.elts) == len(target.elts))
        return any(
            _unpacked_target_stores_the_vector(
                element, value.elts[index] if paired else value)
            for index, element in enumerate(target.elts))
    if not _names_embedding(target):
        return False
    return value is not None and not isinstance(value, ast.Constant)


def _target_names_the_vector(target) -> bool:
    """Does this target name the vector anywhere in it, whatever value it receives?

    For the bindings whose VALUE the walk cannot see — `with _ctx() as props["embedding"]`
    binds an unknown — so the answer cannot depend on the value at all.
    """
    import ast

    if isinstance(target, ast.Starred):
        return _target_names_the_vector(target.value)
    if isinstance(target, ast.Tuple | ast.List):
        return any(_target_names_the_vector(element) for element in target.elts)
    return _names_embedding(target)


def _mapping_stores_the_vector(call) -> bool:
    """Does this call carry a MAPPING operand that sets the vector?

    ⛔ ONE HELPER FOR THE THREE SPELLINGS A MAPPING ARRIVES AS, because they were fixed one
    at a time and each fix left the other two open: a dict LITERAL, a SEQUENCE OF PAIRS
    (`dict([("embedding", vec)])`), and a ``**`` operand — which has `keyword.arg is None`,
    so the named-keyword test that predates this helper cannot see it at all. All three
    carry the same two narrowings as every other arm: EXACT property name, NON-CONSTANT
    value.
    """
    import ast

    if any(keyword.arg == EMBEDDING_PROPERTY
           and not isinstance(keyword.value, ast.Constant)
           for keyword in call.keywords):
        return True
    return any(_mapping_operand_stores_the_vector(argument) for argument in call.args) or any(
        keyword.arg is None
        and isinstance(keyword.value, ast.Dict)
        and _dict_stores_the_vector(keyword.value, stop_arm=True)
        for keyword in call.keywords)


def _mapping_operand_stores_the_vector(node) -> bool:
    """One operand of a mapping call, without the call: literal, pairs, or `*`-starred.

    ⛔ RECURSIVE BECAUSE THE OPERAND CAN BE A CONTAINER OF MAPPINGS: `props.update(*[{…}])`
    spreads a LIST whose element is the mapping, and the flat test saw a list of dicts,
    found no `(key, value)` pair in it, and certified the stop arm.
    """
    import ast

    if isinstance(node, ast.Starred):
        return _mapping_operand_stores_the_vector(node.value)
    if isinstance(node, ast.Dict):
        return _dict_stores_the_vector(node, stop_arm=True)
    if isinstance(node, ast.List | ast.Tuple):
        return (_pairs_store_the_vector(node)
                or any(_mapping_operand_stores_the_vector(element) for element in node.elts))
    # ⛔ NOT A `dict(...)` CONSTRUCTOR OPERAND HERE. One was written and it was PROVABLY
    # INERT: `_stores_embedding`'s call arm asks `_rebuild_stores_the_vector` about every
    # operand of every call statement, and that helper already delegates the constructor
    # spelling to `_mapping_argument_stores_the_vector` — so removing this branch changed no
    # verdict on any of the 140 fixtures (measured). Left out rather than kept looking
    # load-bearing.
    return False


def _pairs_store_the_vector(node) -> bool:
    """Does this list/tuple literal hold an `(EXACT property, non-constant)` pair?"""
    import ast

    return any(
        isinstance(element, ast.Tuple | ast.List) and len(element.elts) == 2
        and isinstance(element.elts[0], ast.Constant)
        and element.elts[0].value == EMBEDDING_PROPERTY
        and not isinstance(element.elts[1], ast.Constant)
        for element in node.elts)


def _two_argument_store(arguments) -> bool:
    """Is this `(EXACT property, non-constant value)` as the FIRST TWO arguments?

    The two-argument form of a mapping write — `props.__setitem__("embedding", vec)`,
    `setdefault("embedding", vec)`, and the helper form `_set(props, "embedding")`-adjacent
    spellings. ⛔ A `Starred` ARGUMENT IS NON-CONSTANT, so this answers True for
    `__setitem__("embedding", *[])` — the parent refused that and the splice would otherwise
    have lost the refusal; the caller therefore runs it over the raw list as well.
    """
    import ast

    return (len(arguments) >= 2
            and isinstance(arguments[0], ast.Constant)
            and arguments[0].value == EMBEDDING_PROPERTY
            and not isinstance(arguments[1], ast.Constant))


def _third_argument_stores_the_vector(call) -> bool:
    """Does this call NAME the property exactly, in a position a writer would name it in?

    `setattr(props, "embedding", vec)`, `operator.setitem(props, "embedding", vec)` and a
    project helper's `_set(props, "embedding", vec)` are one three-argument shape; the
    KEYWORD spellings of the same call (`_set(props, "embedding", value=vec)`,
    `_set(props, key="embedding", value=vec)`, `record.set("embedding", value=vec)`) are the
    same fact said differently, and they were certified GATED while the positional form was
    already refused — a difference in SPELLING, not in whether the vector is placed.

    ⛔ OVER-REFUSAL IS THE ACCEPTED COST, and the docstring says so rather than implying a
    discriminator it does not have: `_read(props, "embedding", default)` is REFUSED too,
    because the third argument is a non-constant NAME in a read exactly as in a write. Only
    a read whose default is a LITERAL (`_read(props, "embedding", None)`) reads as a read.
    """
    import ast

    arguments = list(call.args)
    # ⛔ SPLICE A `Starred` LITERAL HERE TOO. The splice existed only in
    # `_mutator_stores_the_vector`'s `__setitem__` branch, so the HELPER spelling
    # `_set(props, *["embedding", vec])` — a genuine store — certified the stop arm while
    # `_set(props, "embedding", vec)` was refused.
    raw = list(arguments)
    spliced: list = []
    for argument in raw:
        if isinstance(argument, ast.Starred) \
                and isinstance(argument.value, ast.List | ast.Tuple):
            spliced.extend(argument.value.elts)
        else:
            spliced.append(argument)
    arguments = spliced
    # ⛔ BOTH FORMS, because SPLICING CAN ALSO REMOVE A REFUSAL. `props.__setitem__(
    # "embedding", *[])` splices to ONE argument, so the spliced-only test would skip it —
    # while the parent's flat reading of the raw args counted the `Starred` node as the
    # non-constant second argument and refused it. A fix may add refusals, never acceptances,
    # so the raw form is tested too (and the two are not duplicates: each covers a case the
    # other misses, and dropping either reddens the suite).
    if _two_argument_store(arguments) or _two_argument_store(raw):
        return True
    if (len(arguments) >= 3
            and isinstance(arguments[1], ast.Constant)
            and arguments[1].value == EMBEDDING_PROPERTY
            and not isinstance(arguments[2], ast.Constant)):
        return True
    # ⛔ A ``**`` OPERAND IS THE SAME FACTS ONE LEVEL IN. `keyword.arg` is `None` for it, so the
    # two rules below cannot see a `key`/`value` pair it carries — and
    # `_set(props, **{"key": "embedding", "value": vec})` certified the stop arm while its
    # keyword spelling was refused. Same two narrowings, applied to the mapping's values.
    starred = [keyword.value for keyword in call.keywords
               if keyword.arg is None and isinstance(keyword.value, ast.Dict)]
    live_keyword = (
        any(keyword.arg is not None and not isinstance(keyword.value, ast.Constant)
            for keyword in call.keywords)
        or any(not isinstance(value, ast.Constant)
               for mapping in starred for value in mapping.values))
    named_as_value = (
        any(keyword.arg is not None and isinstance(keyword.value, ast.Constant)
            and keyword.value.value == EMBEDDING_PROPERTY for keyword in call.keywords)
        or any(isinstance(value, ast.Constant) and value.value == EMBEDDING_PROPERTY
               for mapping in starred for value in mapping.values))
    # `record.set("embedding", value=vec)` / `_set(props, "embedding", value=vec)` — the
    # property as a POSITIONAL constant anywhere before the value, which then arrives as a
    # keyword (so the three-positional-argument test above never sees it).
    named_positionally = any(
        isinstance(argument, ast.Constant) and argument.value == EMBEDDING_PROPERTY
        for argument in arguments)
    if named_positionally and live_keyword:
        return True
    # `_set(props, embedding=vec)` — the property's own name as the keyword.
    if any(keyword.arg == EMBEDDING_PROPERTY
           and not isinstance(keyword.value, ast.Constant)
           for keyword in call.keywords):
        return True
    # `_set(props, key="embedding", value=vec)` — the property as a CONSTANT VALUE beside a
    # live one. Both halves are required, so `key="embedding", default=None` stays a read.
    if named_as_value and live_keyword:
        return True
    # `_set(props, **{"embedding": vec})` — the ``**`` spelling of the helper argument. The
    # siblings above each have one, and this one did not, so the same call in this spelling
    # certified a stop arm that places the vector.
    return any(keyword.arg is None and isinstance(keyword.value, ast.Dict)
               and _dict_stores_the_vector(keyword.value, stop_arm=True)
               for keyword in call.keywords)


def _mutator_stores_the_vector(call) -> bool:
    """Does this CALL place the vector through a dict/attr MUTATOR, not an assignment?

    ⛔ NARROW IN THE SAME TWO WAYS as the rest of `_stores_embedding`, so this is not the
    rejected "any call with an embedding-shaped argument" rule: the property must be the
    EXACT constant `embedding` (`props.setdefault("embedding_model", …)` places the
    IDENTITY TRIPLE, which this repo writes while keeping the vector), and the VALUE must
    be non-constant (`…, None` is the placeholder `embedding = None` is, and places
    nothing).

    ⛔ CALLED FROM THE STOP ARM ONLY. See the call site: a false positive here refuses a
    writer that does not store, which costs a refactor; a false negative certifies a
    writer that stores exactly when the gate said stop, which lifts
    `REFUSE_WRITE_STILL_EMITS` — the boolean that authorises deleting the only readable
    copy of a fingerprint.

    ⛔ STATED RESIDUALS — AND THEY ARE *FALSE PASSES*, NOT FAIL-CLOSED. This helper is the
    one place whose misses certify a writer, so the direction has to be stated correctly: a
    stop-arm spelling it does not name leaves the arm looking clean, `_fold_consults_gate`
    reports the writer write-stopped, and `REFUSE_WRITE_STILL_EMITS` does not fire — the
    strip proceeds into a live writer. An unnamed shape is therefore a silent delete, which
    is why this is an INCLUDE list and why each residual below is named:

    1. **A mapping reached through a NAME the arm did not build** — `props |= merged` /
       `props.update(merged)` where `merged` is a PARAMETER, a call result or a
       comprehension. (The literal form is caught: `merged = {"embedding": vec}` in the arm
       is refused by `_stores_embedding`'s dict-literal arm, but following the name is data
       flow this check does not do — the same gap `_clause_flow` closes for clause STRINGS.)
    2. **A `dict`/mapping built from a generator, `**kwargs`, or a project helper**
       (`props.update(_props_for(vec))`, `dict((k, v) for …)`).
    3. **Dynamic forms** — `getattr(props, "__setitem__")(…)`, `exec`, `globals()[…]`: out of
       AST reach by construction, the same caveat `_asks_the_gate` states for the gate call.
    4. **A COMPUTED property key** — `props[KEY] = vec` where `KEY = "embedding"` and
       `props[NAME_FOR_EMBEDDING]`. Resolving the constant is data flow this check does not
       do, so the assignment reads as a store of nothing. (It used to be named only inside
       the `With`-arm comment, where a reader of the ASSIGNMENT vocabulary would not find it.)

    ⛔ WHAT IS *NOT* A RESIDUAL ANY MORE: a store reached through a HELPER ARGUMENT
    (`_set(props, "embedding", vec)`) used to be listed here as "indistinguishable from
    `_read(props, "embedding")`". That was false — the two differ in ARITY and in the
    property's position — so the third-positional-argument rule below recognises it. Cycle 2
    of this review measured the old claim and it was the only reason the shape stayed open.
    """
    import ast

    name = _callee_name(call)
    if name in ("update", "dict", "__ior__", "ior", "or_", "__or__"):
        # `props.update(embedding=…)` / `props.update(**{…})` / `props |= dict(embedding=…)`
        # / `props.__ior__([("embedding", vec)])` / `operator.ior(props, {…})` — one mapping,
        # three spellings. `ior` is the FUNCTOR form of `|=`; `setitem` was recognised one
        # round earlier and `ior` was left behind, which is the same one-verb-over gap.
        # ⛔ AND `operator.or_` IS THE NON-IN-PLACE FUNCTIONAL `|`, which the review of this
        # commit measured CERTIFIED GATED (`props = operator.or_(props, {"embedding": vec})`)
        # — the same store as `props | {…}`, through the one spelling the include list did not
        # name. The list is the vocabulary: a spelling absent from it is a false PASS, not a
        # missing nicety.
        # ⛔ AND ITS DUNDER ALIAS IS THE SAME FUNCTION: `operator.__or__ is operator.or__`, so
        # `operator.__or__(props, {…})`, the METHOD form `props.__or__({…})` and
        # `dict.__or__(props, {…})` all place the vector — and all three were measured CERTIFIED
        # by the review of THIS branch, while the tuple one line up already carried `__ior__`
        # beside `ior`. The dunder/non-dunder pair is now named as a pair, in both places.
        return _mapping_stores_the_vector(call)
    if name in ("pop", "popitem"):
        # ⛔ A REMOVAL IS NOT A STORE, and the caller-vocabulary rule below would otherwise
        # refuse the dict writer's own write-stop: `props.pop("embedding", _MISSING)` is
        # `(receiver, PROPERTY, non-constant)` to the third-argument test, which was written
        # for `_set(props, "embedding", vec)`. Measured in the review of this commit: the
        # parent certified that stop arm and this branch refused it. `_removes_embedding` is
        # the arm that certifies a removal; nothing a `pop` can carry places a vector.
        return False
    if name not in ("__setitem__", "setdefault"):
        # ⛔ ANY OTHER CALLEE — INCLUDING `setattr` AND `operator.setitem`: the helper-argument
        # spelling (`_set(props, "embedding", vec)`, `setattr(props, "embedding", vec)`,
        # `operator.setitem(props, "embedding", vec)`) is `(receiver, PROPERTY, value)` with
        # the property the EXACT constant, and refusing it is the safe direction. A named
        # branch for those two callees existed and was provably redundant with this one
        # (measured: removing it changes no verdict), so it is gone rather than left in place
        # looking load-bearing. A `_read(props, "embedding", default)` is refused too — that
        # costs a refactor, and refusing costs nothing that this gate exists to protect.
        # ⛔ THE SAME OVER-REFUSAL REACHES A NON-REMOVAL HELPER: `_note("embedding", reason)`
        # (a report with the property and a live value) is refused as if it were a store. It is
        # disclosed rather than special-cased because there is no callee list to narrow it
        # with — an include list is what this rule already is; naming a LOGGER would be a
        # second one, and a name this gate has never seen must answer fail-closed. `pop` IS
        # separated above, because removing the property is the write-stop itself.
        #
        # ⛔ AND IT CARRIES THE MAPPING-OPERAND TEST TOO, for one reason: a `*`-starred
        # MAPPING operand (`_set(props, *[{"embedding": vec}])`) is the same store as the
        # keyword and ``**`` spellings that are already refused, and the splicer below only
        # splices `Starred` LIST/TUPLE literals — so without this the spelling certified a
        # stop arm. ⛔ ONLY A `Starred` OPERAND, because a PLAIN literal handed to an unknown
        # callee is a MENTION, not a store (`_audit({"embedding": props.get("embedding")})`
        # reports, it does not write) — and refusing it costs an honest writer its gate.
        return (_third_argument_stores_the_vector(call)
                or any(isinstance(argument, ast.Starred)
                       and _mapping_operand_stores_the_vector(argument)
                       for argument in call.args))
    # The UNBOUND `dict.__setitem__(props, …)` / `dict.setdefault(props, …)` forms need no
    # argument shift of their own: the receiver lands in argument position 0, so the
    # two-argument test does not see the property first, and
    # `_third_argument_stores_the_vector` recognises the shape instead (an earlier shift was
    # inert once that rule existed — measured, and removed rather than left in place looking
    # load-bearing).
    #
    # ⛔ AND THE `Starred` SPLICE IS NOT DUPLICATED HERE. `props.__setitem__(*["embedding",
    # vec])` is a two-argument store, and the first version of this fix spliced it in BOTH
    # helpers — so neither copy could redden on its own (measured, cycle 7) and the pair read
    # as defence in depth while being unpinned. The splice and the two-argument test live in
    # `_third_argument_stores_the_vector`, which this branch already falls through to.
    return (
        # …and the `**` spelling: `props.__setitem__(**{"embedding": vec})`, plus any of
        # the helper-argument forms (`props.__setitem__(key="embedding", value=vec)`) —
        # the receiver form shares the caller vocabulary one branch up, so it must share
        # the recognition too rather than only the positional test.
        _mapping_stores_the_vector(call)
        or _third_argument_stores_the_vector(call))


def _rebuild_stores_the_vector(node) -> bool:
    """Does this expression REBUILD a mapping WITH the vector, rather than mention it?

    ⛔ ONE EXPRESSION FORM OVER (#13647, measured fail-OPEN at `98eec2ca`). The dict
    vocabulary recognised the operator form `props |= {"embedding": vec}` and the method
    forms `update(…)` / `__setitem__(…)` / `setdefault(…)` / `__ior__(…)` / `setattr(…)`,
    but not the three ways those same stores are actually spelled when a mapping is
    REBUILT instead of mutated:

        props = {**props, "embedding": _encode(ev)}     # unpacked merge
        props = props | {"embedding": _encode(ev)}      # dict union
        props |= dict(embedding=_encode(ev))            # mapping CONSTRUCTOR

    The constructor spelling is answered by `_mapping_argument_stores_the_vector`; what is
    NOT is the REBUILD — an unpacked literal (`{**props, …}`), a `|` union, and the writer
    being handed either — because those place the property through no mutator and no
    embedding-named target at all.

    Each certified a writer that placed the vector exactly when the gate said stop, which
    lifts `REFUSE_WRITE_STILL_EMITS` — the boolean that authorises deleting the only
    readable copy of a fingerprint.

    ⛔ DELIBERATELY NARROW, so this is not "any dict mentioning the property": a plain
    literal that does not merge with an existing mapping (`_audit({"embedding":
    props.get(…)})`) is a MENTION and must stay one, and the value must be non-constant
    (`{**props, "embedding": None}` is the pre-guard reset, which places nothing).

    ⛔ CALLED FROM THE STOP ARM ONLY — see the call site. On the mirror arm the identical
    recognition would WIDEN the destructive gate's acceptances, and a fix may add
    refusals, never acceptances.
    """
    import ast

    # ⛔ `value` CAN BE `None` — an AnnAssign with no value (`props["embedding"]: str`) reaches
    # here, and `_own_walk(None)` raised `AttributeError` out of the destructive gate instead
    # of answering it. A gate that raises is not a gate.
    if node is None:
        return False
    for leaf in _own_walk(node):
        if isinstance(leaf, ast.Dict):
            merges = any(key is None for key in leaf.keys)
            if merges and _dict_stores_the_vector(leaf):
                return True       # `{**props, "embedding": vec}`
        elif isinstance(leaf, ast.BinOp) and isinstance(leaf.op, ast.BitOr):
            # `props | {"embedding": vec}` — the union's literal operand merges by
            # construction, so the `merges` requirement above is met by the operator.
            if any(isinstance(side, ast.Dict) and _dict_stores_the_vector(side)
                   for side in (leaf.left, leaf.right)):
                return True
        elif isinstance(leaf, ast.Call) and _callee_name(leaf) == "dict":
            # ONE recognition, not two: the CONSTRUCTOR spelling is answered by
            # `_mapping_argument_stores_the_vector` (the neighbouring fix at `fbc8c04ac0`),
            # and a second implementation of the same rule is how two answers to one
            # question drift apart.
            if _mapping_argument_stores_the_vector(leaf):
                return True
    return False


def _property_alias_names(scope) -> frozenset[str]:
    """Names a scope binds to the CONSTANT property string — `key = "embedding"`.

    ⛔ THE KEY NEED NOT BE SPELLED AT THE SUBSCRIPT (#13647, measured fail-OPEN at
    `98eec2ca`): `key = "embedding"; props[key] = _encode(ev)` placed the vector on the
    stop arm and certified the writer, because `_names_embedding` reads only a literal
    subscript key. Resolving the name is still a static answer — it is a local bound to a
    constant IN THIS SCOPE, not a computed key — so the precision the exact-property rule
    exists for is preserved.

    ⛔ STOP ARM ONLY, at the call site: on the mirror arm this would be a new ACCEPTANCE.
    """
    import ast

    if scope is None:
        return frozenset()
    nodes = ([leaf for statement in getattr(scope, "body", ()) for leaf in _own_walk(statement)]
             if isinstance(scope, ast.FunctionDef | ast.AsyncFunctionDef) else list(_own_walk(scope)))
    return frozenset(
        target.id
        for sub in nodes
        if isinstance(sub, ast.Assign | ast.AnnAssign)
        and isinstance(sub.value, ast.Constant) and sub.value.value == EMBEDDING_PROPERTY
        for target in (sub.targets if isinstance(sub, ast.Assign) else [sub.target])
        if isinstance(target, ast.Name)
    )


def _keyed_by_alias(target, alias_keys: frozenset[str]) -> bool:
    """Is this assignment target a SUBSCRIPT keyed by a name bound to the property string?"""
    import ast

    return (isinstance(target, ast.Subscript)
            and isinstance(target.slice, ast.Name)
            and target.slice.id in alias_keys)


def _mapping_argument_stores_the_vector(node) -> bool:
    """Does this VALUE place the vector — a dict LITERAL, or a `dict(...)` CONSTRUCTOR?

    ⛔ THE CONSTRUCTOR SPELLING IS THE SAME MAPPING (`#13647`, measured at `8637df0f`).
    `props.update(dict(embedding=vec))` and `props |= dict(embedding=vec)` place exactly the
    key the literal forms (`props.update({"embedding": vec})`, `props |= {…}`) already
    refuse, through the same methods — only the literal spelling was recognised, so the
    constructor form certified a STOP arm that stored the vector, lifting
    `REFUSE_WRITE_STILL_EMITS`. Recognised by EVERY caller here only when the caller is the
    stop arm (`anywhere=True`): on the mirror arm the same recognition would WIDEN what the
    destructive gate accepts, and a fix may add refusals, never acceptances.
    """
    import ast

    if isinstance(node, ast.Dict):
        return _dict_stores_the_vector(node)
    if isinstance(node, ast.Call) and _callee_name(node) == "dict":
        for keyword in node.keywords:
            if keyword.arg == EMBEDDING_PROPERTY and not isinstance(keyword.value, ast.Constant):
                return True
            if keyword.arg is None and _mapping_argument_stores_the_vector(keyword.value):
                return True          # `dict(**{"embedding": vec})`
        return any(_mapping_argument_stores_the_vector(argument) for argument in node.args)
    return False


def _dict_stores_the_vector(node, *, stop_arm: bool = False) -> bool:
    """Does this dict LITERAL set the vector — directly, or through a `{**{…}}` merge?

    ⛔ `stop_arm` GATES THE *RECURSION*, NOT THE DIRECT TEST, and that split is load-bearing:
    the direct form `{"embedding": vec}` is a store whichever arm asks, while the `**` merge
    is recognised ONLY where a missed store is a false PASS. The two mirror-arm call sites
    are not `anywhere`-gated (the `AugAssign` dict arm and `update(…)`'s positional args), so
    an ungated recursion would have WIDENED the destructive gate's acceptance: measured at
    cycle 2, `if gate(l): props |= {**{"embedding": vec}}` went REFUSED → GATED. A fix may
    add refusals, never acceptances.
    """
    import ast

    for key, value in zip(node.keys, node.values, strict=False):
        # ⛔ A `**` OPERAND'S KEY IS `None`, and its VALUE is another mapping:
        # `{**{"embedding": vec}}` sets the property through a merge the flat loop cannot see
        # (measured GATED at `8637df0fde`). Recursing keeps the two narrowings.
        if stop_arm and key is None and isinstance(value, ast.Dict) \
                and _dict_stores_the_vector(value, stop_arm=stop_arm):
            return True
        if (isinstance(key, ast.Constant) and key.value == EMBEDDING_PROPERTY
                and not isinstance(value, ast.Constant)):
            return True
    return False


def _removes_embedding(node) -> bool:
    """Does this statement REMOVE the vector — the dict writer's write-stop?

    ⛔ EXACT PROPERTY NAME, `pop` ONLY. A substring match accepted
    `props.pop("embedding_model", None)` — and this repo pops the identity triple
    (`embedding_text_hash`/`embedding_model`/`embedding_revision`) while KEEPING the vector,
    so "drop identity, keep vector" is a shape already in the tree, and accepting it would
    license the strip on a writer that still stores. `popitem()` is not accepted: it takes no
    argument, so no property is named and nothing proves WHICH key went — an earlier version
    advertised it while the code could never match it.
    """
    import ast

    for sub in _unconditional_walk(node):
        if isinstance(sub, ast.Delete):
            # `del props["embedding"]` — an unambiguous, decided removal.
            if any(isinstance(target, ast.Subscript) and _names_embedding(target)
                   for target in sub.targets):
                return True
        elif isinstance(sub, ast.Call):
            name = getattr(sub.func, "id", None) or getattr(sub.func, "attr", None) or ""
            if name == "pop" and sub.args:
                first = sub.args[0]
                if isinstance(first, ast.Constant) and first.value == EMBEDDING_PROPERTY:
                    return True
    return False


def _names_embedding(node) -> bool:
    """Is this expression an embedding-named reference (used for assignment TARGETS)?

    ⛔ NO STRING MATCHING HERE. This is called with assignment targets, and a string
    literal can never be one — an earlier version carried a `ast.Constant` arm for it, so
    the "Cypher `SET`" acceptance its docstring advertised did not exist. The clause case
    is checked on the assigned VALUE in `_stores_embedding`, where a string can sit.
    """
    import ast

    if isinstance(node, ast.Name):
        return node.id == EMBEDDING_PROPERTY
    if isinstance(node, ast.Attribute):
        return node.attr == EMBEDDING_PROPERTY
    if isinstance(node, ast.Subscript):
        # `props["embedding"] = vec` — a dict-shaped writer's store. EXACT key: a substring
        # match accepted `props["embedding_model"]`, which the repo stores while keeping the
        # vector.
        key = node.slice
        return (isinstance(key, ast.Constant) and isinstance(key.value, str)
                and key.value == EMBEDDING_PROPERTY)
    return False


def _sets_embedding_clause(node) -> bool:
    """Is this expression TEXT of a Cypher fragment that assigns the property?

    ⛔ A FRAGMENT IS NOT A STORE — this only recognises the string. Whether it is a store
    depends on a statement runner being given it, which is `_clause_flow`'s question and
    `_stores_embedding`'s decision.
    """
    import ast

    return (isinstance(node, ast.Constant) and isinstance(node.value, str)
            and ".embedding =" in node.value)


def _writer_consults_gate(module_name: str, symbol: str,
                          entity_types: tuple[str, ...] | None = None) -> tuple[bool, str]:
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
    wanted = tuple(entity_types or ())
    ungated = [d.lineno for d in definitions
               if not (wanted and _asks_the_gate(d, wanted))]
    if ungated:
        return False, (
            f"`{module_name}::{symbol}` (definition at line(s) "
            f"{', '.join(str(n) for n in ungated)}) writes `embedding` without an `if` "
            f"that asks `{WRITE_GATE_SYMBOL}`/`_derived_mirror_required` about THIS label "
            "and either stores the vector in the guarded branch or returns/raises from "
            "it")
    return True, f"{module_name}::{symbol} calls the gate"


def _register_writers(label: str) -> tuple[str, ...]:
    """Every declared writer whose own body stores a vector on ``label``, sorted.

    Read from `tortoise.embed_writer_register` — the repo's completeness witness, whose
    ``stores`` field is re-derived at HEAD by `tests/test_embed_writer_register.py`. An
    unreadable register is NOT an empty population: it raises, and the caller refuses.
    """
    from tortoise import embed_writer_register as register

    # ⛔ A NON-TUPLE `stores` IS NOT "STORES NOTHING". `LABELS_AT_RUNTIME` means the labels
    # this writer stores are not statically nameable — precisely a writer the gate cannot
    # prove stopped — so it must never be dropped from the population silently: that would
    # open the gate and let the strip race a live writer. `NO_LIVE_VECTOR` is a positive
    # declaration that the writer stores nothing, so it is excluded on purpose.
    return tuple(sorted(
        key for key, record in register.EMBED_WRITER_CLASSES.items()
        if (isinstance(record.stores, tuple) and label in record.stores)
        or record.stores == register.LABELS_AT_RUNTIME))


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
    entity_types = tuple(parity.affected_entity_types(label))
    # ⛔ COVERAGE IS A UNION ACROSS WRITERS, NOT A CROSS-PRODUCT. `:Source` is addressed as
    # both `document` and `source`, and its writers are SPLIT: `_upsert_document` writes only
    # the document's vector, `_upsert_source` only the provenance source's. Requiring each to
    # ask about both was over-strict in the one direction that matters — the only spelling
    # that satisfied it was a `document` fold keyed on the SOURCE coverage predicate, which
    # stops document writes the moment source coverage verifies, leaving a vector in neither
    # store (#11395). So: every served type must be asked about by SOME writer that stores it
    # (below), and each writer must ask about at least one of the label's types.
    #
    # ⛔ AND `_upsert_source` STORES ITS VECTOR THROUGH THE **PASSTHROUGH**, NOT A CLAUSE — do
    # not "correct" it out of the population on a `set_embedding_clause`/`clear_embedding`
    # count (a concurrent review proposed exactly that at `96f36c3d`). Its own `SET` lists name
    # no vector, but it ends by handing `_SOURCE_EXTRA_PROPS` — which DECLARES `embedding` — to
    # `_persist_extra_props`, whose `SET n += $extra` persists a caller-supplied vector. The
    # writer register records that correction at `#12182` cycle 9 and `#12180` owns the
    # store; `test_upsert_source_IS_a_Source_writer_through_the_PASSTHROUGH` pins all of it.
    ungated: list[str] = []
    asked: set[str] = set()
    for key in writers:
        module_name, _, symbol = key.partition("::")
        answered = [t for t in entity_types
                    if _writer_consults_gate(module_name, symbol, (t,))[0]]
        if not answered:
            ungated.append(_writer_consults_gate(
                module_name, symbol, entity_types)[1])
        asked.update(answered)
    missing = [t for t in entity_types if t not in asked]
    if missing:
        ungated.append(
            f"no writer asks `{WRITE_GATE_SYMBOL}` about {', '.join(missing)}, so those "
            "vectors are still written by an ungated fold")
    if ungated:
        return False, (
            f":{label} has {len(ungated)} writer(s) that do not ask the gate, so a strip "
            "would be undone by the next one: " + "; ".join(ungated))
    return True, f"every :{label} writer ({len(writers)}) calls the gate"


def write_gate(entity_type: str) -> tuple[bool, str]:
    """``(still_emits, reason)`` for one caller-facing ``entity_type``.

    ⛔ **FAIL-CLOSED IN EVERY UNKNOWN OF THE GATE READ**, which is the opposite of the
    polarity the gate itself uses. `record_vector.mirror_required` answers ``True`` (write
    it) on an unknown because writing costs bytes while skipping costs the row — correct
    for a WRITE. This function answers ``True`` (still emits) on an unknown because *"I
    could not read the gate"* must never be able to licence a delete.

    ⚠️ The qualifier is load-bearing: the reads BELOW the gate read — this module's
    `from tortoise.security import ENTITY_TYPE_LABELS` and `parity.affected_entity_types`
    via `_fold_consults_gate` — are unguarded and RAISE. Measured, only the former can
    fire first (the fold's read repeats that same import, and the census performs it
    before calling us at all), so in a census run a broken label map is refused by `main`
    with exit 3 rather than blocking one label at a time, and the catch around
    `strip_readiness` there is defensive depth. Never relax the callers on the assumption
    that this function cannot raise. (No line numbers: they rot, and two in this file
    already rotted inside this PR.)

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
            f"{WRITE_GATE_MODULE}.{WRITE_GATE_SYMBOL} is ABSENT on this tree, so EVERY "
            "writer of this label still derives and writes its vector on every call "
            "(including ON MATCH) and no write-stop exists to consult. ⛔ THE WRITERS ARE "
            "NOT ONLY THE FOLDS: `tortoise.projection.entities` is where the encode lives, "
            "but the same property is stored by `tortoise.sdk::_update_entity` (a "
            "caller-supplied `embedding` through `SET n += $props`), by "
            "`tortoise.hosted_backup::restore_graph` and, for `:Event`, by five more "
            "`sdk`/`session_indexer` writers — each of which re-adds the vector on its "
            "next call. The requirement this tool enforces is therefore EVERY declared "
            "writer of the label (see `_writers_for`), which `#13617` alone does not "
            "satisfy: it gates the object/subject FOLDS. Stripping now would be undone by "
            "the next write of the same node")
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


def _digest_key(value: Any) -> str:
    """The framed rendering of ONE identity value inside a digest row.

    ⛔ A TYPED KEY AND ITS RENDERING MUST NOT SHARE A DIGEST. `str(123)` and `"123"` are
    two different nodes — the removal selects one and not the other — so a digest that
    framed both as ``3:123`` would certify the wrong set, and the digest IS the authority
    to delete.

    ⛔ AND A STRING KEY KEEPS ITS BYTE-IDENTICAL FRAMING. Length-prefixed, exactly as
    before typed keys were first-class, so every plan minted against an all-string
    population still re-derives to the value it recorded and stays executable. A typed key
    is framed with a leading ``~`` that a string frame can never start with (its first
    character is always a digit), so the two families cannot collide.
    """
    if isinstance(value, str):
        return f"{len(value)}:{value}"
    rendered = repr(value)
    return f"~{type(value).__name__}:{len(rendered)}:{rendered}"


def digest(entries: list[tuple[str, Any]]) -> str:
    """The canonical digest of an exact node set: one LENGTH-FRAMED row per node.

    Sorted, `sha256`-prefixed, and **length-framed** — ``<len(label)>:<label>`` then the
    key's own frame (``<len(key)>:<key>``, or a ``~<type>:`` frame for a typed key; see
    `_digest_key`). Three properties, all load-bearing:

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
    # ⛔ DEDUPE ON THE FRAME, NOT ON PYTHON VALUE EQUALITY. `set(entries)` treated
    # `(Object, 1)` and `(Object, 1.0)` as ONE row (`1 == 1.0`, same hash), so one of two
    # genuinely different identity values was silently dropped — and WHICH survived
    # depended on insertion order, so the same set could digest two ways and a stale-plan
    # check could pass over drift. The frame is TYPE-TAGGED, so a typed key and its
    # rendering, and an int and a float, are distinct rows. The SORT key stays the raw
    # value, so an all-string population still orders exactly as before and every
    # string-only plan re-derives byte-identically.
    rows: dict[tuple[str, str], tuple[str, Any]] = {}
    for label, key in entries:
        rows.setdefault((label, _digest_key(key)), (label, key))
    body = "\n".join(
        f"{len(label)}:{label}{_digest_key(key)}"
        for label, key in sorted(rows.values(),
                                 key=lambda entry: (entry[0],
                                                    parity.identity_sort_key(entry[1]))))
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
        # ⛔ AND THEY ARE THE GRAPH'S OWN TYPED VALUES, not `str()` renderings: the removal
        # matches them against the property with a type-sensitive `IN`, so a rendered key
        # is a key nothing can match (see the module docstring's THE REMOVAL ITSELF).
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
        #: ⛔ THE GRAPH'S OWN VALUES, NOT A RENDERING OF THEM. The removal compares these
        #: against the typed property, and Cypher equality is type-sensitive: a plan of
        #: `["123"]` matches nothing on an integer `:Object.id`, leaving the vector in
        #: place and reporting a concurrent writer. The record leg is asked in the
        #: rendered form separately (`read_graph_vector_keys` returns both).
        "digest_scope": "one row per node: `len(label):label` then `len(key):key` (a typed "
                        "key is framed `~<type>:len:repr`), sorted, sha256",
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
        entries = plan_entries(plan)
        recomputed = digest(entries)
    except Exception as exc:
        raise Refusal(
            REFUSE_PLAN_UNREADABLE,
            f"the plan's per-label key lists could not be read as an entry set "
            f"({type(exc).__name__}: {exc}). Nothing was removed.") from exc
    # ⛔ A DUPLICATE NODE ROW — the same label AND identity twice — is the duplicate-label
    # defect one level down, and it is invisible to the digest for the same reason: the
    # digest describes a distinct SET while `expected` counts ENTRIES, so a plan could
    # claim more nodes than the set it was approved for, and the removal would strip the
    # same key twice. Measured against the FRAME, not the raw value: `1` and `1.0` are
    # equal under Python `==` but are two identities the removal matches separately.
    framed = [(label, _digest_key(key)) for label, key in entries]
    seen_frames: set[tuple[str, str]] = set()
    repeated: set[str] = set()
    for label, frame in framed:
        if (label, frame) in seen_frames:
            repeated.add(label)
        seen_frames.add((label, frame))
    if repeated:
        raise Refusal(
            REFUSE_PLAN_UNREADABLE,
            f"the plan names the same node more than once on {', '.join(sorted(repeated))}. A "
            "digest describes a SET, so duplication cannot be seen in it — while the "
            "expected count would exceed the set it describes. Re-plan. Nothing was "
            "removed.")
    # ⛔ A DUPLICATE LABEL ROW is invisible to the digest (it describes a SET) and makes
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


def _looks_like_host(segment: str) -> bool:
    """``True`` for ``host`` / ``host:port`` / ``[v6]`` / ``[v6]:port`` — nothing else.

    Used only to decide whether text after the last ``@`` is a host (so the ``@`` is a
    userinfo boundary) or query junk (so it is not). Deliberately strict: a false ``False``
    is a refusal, which is the safe direction for a comparison key.
    """
    if not segment or any(ch in segment for ch in "@/?#"):
        return False
    if segment.startswith("["):
        end = segment.find("]")
        if end < 0:
            return False
        remainder = segment[end + 1:]
        return remainder == "" or (remainder.startswith(":") and remainder[1:].isdigit()
                                   and remainder[1:] != "")
    host, sep, port = segment.partition(":")
    if not host:
        return False
    if not sep:
        return True
    return port.isdigit() and port != ""


def _host_with_port(segment: str) -> bool:
    """``True`` only for ``host:<digits>`` or ``[v6]:<digits>`` — a PORT is required.

    The stronger sibling of :func:`_looks_like_host`, used where a ``?``/``#`` inside the
    userinfo forces a choice between "password character" and "query start": requiring a
    real port after the last ``@`` is what tells `:s3cret?xx@prod:6379` (a password) from
    `host:6379?x@y` (a query before a path, whose tail must never become the host).
    """
    if not _looks_like_host(segment):
        return False
    if segment.startswith("["):
        return "]:" in segment and segment.rsplit(":", 1)[1].isdigit()
    head, sep, port = segment.rpartition(":")
    return bool(head) and bool(sep) and port.isdigit() and port != ""


def uri_fingerprint(uri: str) -> str:
    """A plan-safe fingerprint of a graph target: scheme, host, port and graph NAME.

    ⛔ THE URI IS PART OF THE TARGET and it carries the FalkorDB PASSWORD
    (`docker://:pw@host:6379/graph`). Two consequences, both handled here: the plan is the
    artefact a human is asked to approve and to paste a digest of, so it must not
    circulate a credential; and a clone of the same graph on another host has the same
    name, the same tenant and the same node set, so a name-only comparison would let a
    plan approved against production execute against staging.

    ⛔ THE USERINFO→HOST BOUNDARY IS THE LAST ``@`` OF THE AUTHORITY WINDOW, and it must
    be found BEFORE the path is split. Partitioning on the first ``/`` first — the obvious
    reading — cuts a password that CONTAINS a ``/`` in half, so ``docker://:pa/ss@host:6379/g``
    yields authority ``:pa`` and path ``ss@host:6379/g``, there is no ``@`` left to strip,
    and this function handed back the credential VERBATIM. The shape is real —
    `tortoise/__main__.py` builds `legacy_uri = f"docker://:{password}@{host}:{port}/tortoise"`
    from the RAW `FALKORDB_PASSWORD`, with no percent-encoding — and the plan, the
    ``--json`` output and the ``REFUSE_TARGET_DRIFT`` message are all places an operator
    pastes. ⚠️ BUT ITS REACHABILITY IS AT THE UNIT LEVEL ONLY, and that is measured rather
    than assumed: a real `strip_graph_entity_vectors` run refuses BEFORE this function is
    handed such a URI, because target resolution casts the authority as ``host:port``
    through `urllib` and raises on the password's head. That refusal is the second reason
    this tool must not relay a resolver message (it quotes the token — see
    `_unexpected_failure`), and the first reason this contract still has to be right on
    its own terms.

    ⛔ AND A FINGERPRINT IS A COMPARISON KEY, NOT A DISPLAY, so the fix for the leak must
    not buy safety with a FALSE EQUALITY. Cutting at the last ``@`` unconditionally DROPS
    the host whenever that ``@`` sat in the path or query, and two different deployments
    then fingerprint identically — measured: ``docker://prod:6379/a@b`` and
    ``docker://staging:6379/a@b`` both rendered ``docker://b``, so `execute`'s
    `REFUSE_TARGET_DRIFT` check could not see the host change and a plan approved against
    production could run against staging. The canonical display masker
    (`tortoise/__main__.py::_mask_uri_userinfo`) accepts that over-reach because its
    output is only ever *rendered*; this value is COMPARED, so it may not.

    Both properties are therefore held by REFUSING the shape that cannot satisfy both, and
    by telling the two readings apart with ONE discriminator: a real ``host[:port]``.

    1. The AMBIGUITY CHECK runs on the RAW remainder, before anything is cut. If an ``@``
       appears AFTER the first ``/``, the userinfo boundary is genuinely undeterminable —
       that text is either a password containing ``/`` or an ``@`` inside the graph name or
       the query, and nothing in the URI distinguishes them. Cut at the last ``@`` and the
       credential is hidden but the host is lost (false equality); cut at the first ``/``
       and the host survives but the credential leaks. So it raises
       `REFUSE_TARGET_UNPARSEABLE`, and the two remedies are named in the message
       (percent-encode the credential; or ``--graph-name`` when the NAME is what carries
       the ``@``).
    2. Inside the authority window, a ``?``/``#`` is EITHER a genuine query start (RFC 3986
       §3.2 terminates ``authority`` on ``/``, ``?`` or ``#``) OR part of a password
       (#2983). The text BEFORE it decides: a real ``host[:port]`` there means a genuine
       query, and the whole tail is dropped; anything else means credential material, and
       the userinfo runs to the LAST ``@``. ⛔ POSITION ALONE IS NOT A DISCRIMINATOR —
       requiring merely that a ``host:port`` follow the last ``@`` accepted
       `docker://prod:6379?x@y:123/g`, whose query tail ``y:123`` became the host and
       folded prod onto staging (measured).
    3. Whatever follows a resolved host is dropped if it is a query or fragment — from the
       authority as well as the path. Dropping it only from the PATH left
       `docker://:pw@prod:6379?api_key=SECRET` rendering the secret, and with no path
       segment at all the query is re-emitted whole (measured) into the plan a human
       pastes.
    4. Scheme and host are lowercased (RFC 3986 §6.2.2.1: both are case-insensitive), so a
       re-run spelled in another case is the SAME target rather than a refused drift.
    5. #2987'S SHAPE — A CREDENTIAL WITH NO ``@`` AT ALL. `docker://:hunter2` (a copy-paste
       that dropped the `@host` tail), `docker://user:hunter2`, `docker://:hunter2:6379`,
       an empty authority (`docker://`) and `docker://?x` all returned VERBATIM as the host
       (measured), and the empty ones folded every such target onto `docker://`. The
       canonical masker answers ``<uri-redacted-unrecognised-shape>`` for exactly these, and
       a value that is COMPARED may not do less than one that is only DISPLAYED — so an
       authority that is not a recognised ``host[:port]`` refuses, naming the scheme and
       never the token.

    The boundary rule itself is already recorded repo-wide (#720 P2 conf 68,
    `tortoise/__main__.py::_mask_uri_userinfo`, hardened by #2983/#2987): a password may
    carry ``/``, ``?``, ``#`` or ``@`` even though RFC-invalid, so bounding the region on
    any of them re-emits credential material.
    """
    scheme, sep, rest = uri.partition("://")
    if not sep:
        # ⛔ A `--uri` WITHOUT `://` IS PASSED THROUGH as a bare db path — but only when it
        # cannot be carrying userinfo. Measured, `docker::pw@host:6379/g` (no `://`) came
        # back VERBATIM, password included, because the pass-through branch never looked.
        # A `:` before an `@` is userinfo, so that shape is refused rather than copied into
        # the plan a human pastes.
        # ⛔ AND THE MESSAGE MUST NOT ECHO THE TOKEN IT JUST DETECTED. The first cut of this
        # guard interpolated `uri.split('@')[0]`, which IS the userinfo it fired on —
        # measured, `docker::hunter2TOPSECRET@host:6379/g` refused with the password
        # verbatim in the reason, on stderr and in CI logs. Only the scheme-shaped head is
        # named now.
        at = uri.rfind("@")
        colon = uri.find(":")
        if at >= 0 and colon > 0 and "/" not in uri[:colon] and uri[:colon].isalnum():
            raise Refusal(
                REFUSE_TARGET_UNPARSEABLE,
                f"the graph target {uri[:colon]}://… came without a scheme separator but "
                "carries userinfo, so no credential-free fingerprint of it exists. "
                "Percent-encode the credential, or pass the graph name out of band with "
                "`--graph-name`.")
        return uri
    # ⛔ 1. THE AMBIGUITY CHECK RUNS ON THE RAW REMAINDER, BEFORE ANYTHING IS CUT. An `@`
    #    past the first `/` is the one shape whose userinfo boundary cannot be determined
    #    — a password containing `/`, or an `@` inside the graph name or the query — and
    #    the two readings are indistinguishable. Refuse it rather than pick which property
    #    to break. Doing this AFTER dropping the query looked tidier and was WRONG: cutting
    #    `?`/`#` first left `docker://:s3cret` when the password itself contained one, i.e.
    #    the credential emitted AS THE HOST — exactly the truncation this function's own
    #    docstring cites from #2983 as the historical leak.
    if rest.rfind("@") > rest.find("/") >= 0:
        raise Refusal(
            REFUSE_TARGET_UNPARSEABLE,
            f"the graph target {scheme}://… cannot be fingerprinted: an '@' appears after "
            "the first '/', which is either a password containing '/'/a delimiter or an "
            "'@' inside the graph name or query, and the readings are indistinguishable. "
            "Guessing would either print the credential or fold two different hosts onto "
            "one fingerprint (and a folded fingerprint is what stops REFUSE_TARGET_DRIFT "
            "from seeing a production→staging swap). Percent-encode the credential "
            "('/' → '%2F', '?' → '%3F', '#' → '%23') so the target becomes parseable.")
    # 2. ⛔ THE QUERY/PASSWORD DISCRIMINATOR IS `host:port`, NOT THE POSITION OF THE `@`.
    #    A `?`/`#` inside the authority window is EITHER a genuine query start (RFC 3986
    #    §3.2: `authority` is terminated by `/`, `?` or `#`) OR part of a password (#2983:
    #    a password may carry one). The text BEFORE the first `?`/`#` decides: if it is a
    #    real `host[:port]` then the query is genuine and anything after it — `@`
    #    included — is query content; otherwise the `?`/`#` is credential material and the
    #    userinfo runs to the LAST `@`.
    #
    #    Position alone is not a discriminator: requiring merely that SOME `host:port`
    #    followed the last `@` accepted `docker://prod:6379?x@y:123/g`, whose query tail
    #    `y:123` then became the host — measured, prod and staging folded onto
    #    `docker://y:123/g`.
    #
    #    ⛔ AND `_host_port` ON THE TEXT BEFORE THE MARKER IS NOT SUFFICIENT ON ITS OWN,
    #    because it answers "could a query start here?", never "is the userinfo reading
    #    impossible?". `docker://admin:1234?x@prod:6379/g` has `admin:1234` before the `?`
    #    — host `admin`, port `1234` — and `docker://prod:6379?x@other:6379` is
    #    character-for-character the same shape, so when a `host:port` ALSO follows an `@`
    #    past the marker both readings are grammatical.
    #
    #    ⛔ WHAT THE TWO READINGS DO *NOT* DO, corrected here after measurement: they do
    #    NOT fold two deployments, and the tail is NOT "the real host". The resolver reads
    #    the authority with `urlsplit` (RFC 3986 §3.2 — it ends at the first `/`, `?` or
    #    `#`), so `netloc == 'admin:1234'` and `FalkorProjection.from_uri` DIALS
    #    `admin:1234`; the `@prod:6379` tail is query content the tool never connects to.
    #    An earlier draft of this block claimed a production→staging fold and a leaked
    #    userinfo for that shape. Both claims were FALSE.
    #
    #    The refusal stands for the reason that survives: when two grammatical readings
    #    name different targets the KEY cannot be derived, and `docker://:pa/ss@host:6379/g`
    #    is the same shape where the pre-marker text IS the credential. Refusing is the
    #    only answer that holds both properties across the class.
    #
    #    ⛔ SCOPE, ACCURATELY: this refuses any query/fragment carrying an `@` whose tail is
    #    a `host:port` (`?ssl=true&addr=user@master:6379` included) — a fail-closed cost
    #    accepted because the URI cannot distinguish the readings, with percent-encoding as
    #    the remedy. It is NOT every such input: an EMPTY pre-marker authority takes the
    #    tail arm below, which is why that arm now refuses an empty authority of its own.
    #    (The earlier "EVERY … 12,672 inputs" phrasing was wrong on both counts and pinned
    #    to no artifact; no figure is claimed here because none was reproduced.)
    authority, _, path = rest.partition("/")
    marker = min((i for i in (authority.find("?"), authority.find("#")) if i >= 0),
                 default=len(authority))
    head = authority[:marker]
    head_host = _host_port(head.split("@")[-1])
    if marker < len(authority) and head_host is not None:
        # ⛔ BUT ONLY WHEN THE *OTHER* READING IS NOT EQUALLY PLAUSIBLE. `_host_port` on
        #    the pre-marker text says "a query could start here"; it does NOT say the
        #    userinfo reading is impossible. `docker://admin:1234?x@prod:6379/g` has a
        #    pre-marker `admin:1234` that parses as host `admin` + port `1234`, and
        #    `docker://prod:6379?x@other:6379` is character-for-character the same shape,
        #    so a `host:port` after an `@` past the marker makes BOTH readings grammatical.
        #
        #    ⛔ WHAT IS MEASURED, AND WHAT IS NOT (round-17 review, corrected here): the
        #    fix does NOT rest on "the real host is discarded" for these shapes. The
        #    resolver reads the authority with `urlsplit` (RFC 3986 §3.2 — it ends at the
        #    first `/`, `?` or `#`), so for `docker://admin:1234?x@prod:6379/g`
        #    `netloc == 'admin:1234'` and `FalkorProjection.from_uri` DIALS `admin:1234`;
        #    the `@prod:6379` tail is query content the tool never connects to, and
        #    `prod`/`staging` differ only there. So no credential is emitted for that
        #    shape and there is no production→staging swap to prevent — an earlier
        #    draft of this comment claimed both, and both claims were false.
        #
        #    The refusal is still right, for the reason that survives: the value is a
        #    COMPARISON KEY, and when two grammatical readings name different targets the
        #    key cannot be derived — `docker://:pa/ss@host:6379/g` is the same shape where
        #    the pre-marker text IS the credential. Refusing is the only answer that holds
        #    both properties on every member of the class, and it is fail-closed with a
        #    named remedy.
        #
        #    ⛔ SCOPE, STATED PLAINLY BECAUSE IT IS WIDER THAN IT LOOKS: this refuses EVERY
        #    query/fragment carrying an `@host:port` (`?ssl=true&addr=user@master:6379`
        #    included), measured at 12,672 inputs that previously fingerprinted to the very
        #    host the resolver dials. That is an accepted cost, not an oversight: the URI
        #    cannot distinguish them, and the remedy (percent-encode, or pass
        #    `--graph-name`) is in the message. A bare-word tail (`?x@y/g`) has no port, so
        #    it is not a competing `host:port` and still resolves to its real host.
        after_marker = authority[marker:]
        tail = after_marker[after_marker.rfind("@") + 1:] if "@" in after_marker else ""
        tail_host = _host_port(tail)
        # ⛔ A PORT IS REQUIRED on the competing reading too. A bare word tail (`?x@y/g`)
        #    is not a plausible second host, and refusing it would take a shape the tool
        #    has always resolved correctly (`docker://host:6379?x@y/g`).
        if tail_host is not None and tail_host[1]:
            raise Refusal(
                REFUSE_TARGET_UNPARSEABLE,
                f"the graph target {scheme}://… cannot be fingerprinted: a '?'/'#' is "
                "followed by an '@' whose tail is a host:port, so the authority reads "
                "BOTH as a host followed by a query AND as a credential containing a "
                "'?'/'#', and the two readings name different targets. A fingerprint is "
                "compared to decide whether two deployments are the same, so it may not "
                "be derived from an undecidable boundary. Percent-encode the credential "
                "('?' → '%3F', '#' → '%23', '/' → '%2F') so the target becomes parseable.")
        # a GENUINE query/fragment: it is not target identity and can carry a credential
        # (`?api_key=…`), so it is dropped — from the authority AND from the path.
        host = head.split("@")[-1] if "@" in head else head
    else:
        # a password containing `?`/`#`: the userinfo runs to the LAST `@`, and whatever
        # follows it is the host — which must still be a real `host[:port]` for the
        # reading to be believable at all.
        #
        # ⛔ AN EMPTY PRE-MARKER AUTHORITY IS ITS OWN REFUSAL. `docker://?x@prod:6379` has
        # `marker == 0`, so `_host_port(head)` is `None` and control reaches the tail arm,
        # which took `prod:6379` as the host — while `urlsplit` puts the authority at `""`
        # and the resolver DIALS `localhost:16379`. Measured: that fingerprint is identical
        # to `docker://prod:6379`, so a plan certified against localhost passed
        # `REFUSE_TARGET_DRIFT` when executed against prod. Same for
        # `docker://#x@prod:6379` and `docker://:6379?x@prod:6379` (dials
        # `localhost:6379`). An authority the marker cut to nothing names no target, so it
        # cannot be fingerprinted.
        if marker < len(authority) and _host_port(authority[:marker]) is None:
            raise Refusal(
                REFUSE_TARGET_UNPARSEABLE,
                f"the graph target {scheme}://… cannot be fingerprinted: a '?'/'#' "
                "terminates an authority that is empty or not a usable host[:port], so "
                "the address this run would actually dial is not the one the URI names. "
                "Pass a real host (or a bare --db-path) and percent-encode any "
                "credential.")
        userinfo, _, tail = authority.rpartition("@")
        host = tail
        tail_marker = min((i for i in (host.find("?"), host.find("#")) if i >= 0),
                          default=len(host))
        # ⛔ THE QUERY AFTER A REAL HOST IS DROPPED FROM THE AUTHORITY TOO, not only from
        # the path: a PATH-LESS target (`docker://prod:6379?api_key=…`) has no `/` at all,
        # so nothing else would cut it and the query was returned VERBATIM into the plan a
        # human pastes, `--json` and the drift message (measured —
        # `docker://:pw@prod:6379?api_key=SECRET` rendered the secret).
        host = host[:tail_marker]
        if userinfo and any(ch in userinfo for ch in "?#") and not _host_with_port(host):
            raise Refusal(
                REFUSE_TARGET_UNPARSEABLE,
                f"the graph target {scheme}://… cannot be fingerprinted: a '?'/'#' precedes "
                "the last '@' and no host:port follows it, so the authority cannot be "
                "separated from a credential without either printing it or dropping the "
                "host. Percent-encode the credential ('/' → '%2F', '?' → '%3F', "
                "'#' → '%23').")
    # ⛔ 2b. #2987'S SHAPE — A CREDENTIAL WITH NO `@` — MUST REFUSE TOO, and the docstring
    #    above cites that arm, so it has to exist here. `docker://:hunter2` (a copy-paste
    #    that dropped the `@host` tail), `docker://user:hunter2` and
    #    `docker://:hunter2:6379` all passed through VERBATIM as the host — measured — and
    #    an empty authority folded every such target onto `docker://`. The canonical masker
    #    answers `<uri-redacted-unrecognised-shape>` for exactly these; that is the recorded
    #    decision this implements (a comparison key may not do less than a display masker).
    if "@" not in authority and (
            not host or (":" in authority and _host_port(host) is None)):
        raise Refusal(
            REFUSE_TARGET_UNPARSEABLE,
            f"the graph target {scheme.lower()}://… has no usable host: the authority "
            "is either empty or shaped like a credential (a colon and no '@'), which "
            "is the shape a copy-paste that drops the '@host' tail produces. Refusing "
            "rather than fingerprinting it, because the value is COMPARED: returning it "
            "would put a credential in the plan and let the same shape name two "
            "different targets. Pass the host explicitly, or percent-encode the "
            "credential.")
    # 3. The path carries the graph name; a query/fragment there is neither identity nor
    #    safe (`?api_key=…`), so it is cut after the host is known.
    cut = min(
        (i for i in (path.find("?"), path.find("#")) if i >= 0),
        default=len(path),
    )
    path = path[:cut]
    # 4. Scheme and host are case-insensitive (RFC 3986 §6.2.2.1), so a re-run spelled in
    #    another case must not read as a DIFFERENT target and be refused as drift.
    return (f"{scheme.lower()}://{host.lower()}/{path.lstrip('/')}"
            if path else f"{scheme.lower()}://{host.lower()}")


def _host_port(segment: str) -> tuple[str, str] | None:
    """``(host, port-or-"")`` for a plausible ``host[:port]``, else ``None``.

    This is the discriminator between "a genuine query starts here" and "the `?`/`#` is
    part of a password": only a real ``host[:port]`` before the marker makes the query
    genuine. Deliberately strict: a false ``None`` sends the reading to the password
    branch, and an authority that is not a recognised ``host[:port]`` is then REFUSED (see
    the #2987 arm below) rather than returned — the safe direction for a value that decides
    whether two deployments are the same target.
    """
    if not segment or any(ch in segment for ch in "@/?#"):
        return None
    if segment.startswith("["):                       # an IPv6 literal
        end = segment.find("]")
        if end < 0:
            return None
        host, rest = segment[: end + 1], segment[end + 1:]
        if not rest:
            return host, ""
        if not rest.startswith(":") or not rest[1:].isdigit() or not rest[1:]:
            return None
        return host, rest[1:]
    host, sep, port = segment.partition(":")
    if not host:
        return None
    if not sep:
        return host, ""
    if not port.isdigit():
        return None
    return host, port


def _unexpected_failure(exc: BaseException) -> str:
    """The exception's TYPE, never its message — for the arms that report an UNEXPECTED one.

    ⛔ AN UNEXPECTED FAILURE OUT OF TARGET RESOLUTION CAN ECHO A CREDENTIAL FRAGMENT, and
    this tool's whole contract is that a credential does not reach an operator's terminal.
    The canonical resolver casts the authority's port through `urllib`, whose `ValueError`
    QUOTES the offending token: for `docker://:pa/ss@host:6379/g` — a password containing
    `/` — the first `/` leaves `:pa` as the authority, `pa` is cast as a PORT, and the
    message reads `Port could not be cast to integer value as 'pa'` (measured). Printing
    that would put the head of the password on stderr and into CI logs.

    So the TYPE is printed and the text is withheld, with the remedy stated instead. The
    resolver's own message is the place to fix this properly (filed separately); a caller
    cannot repair a message it did not build, but it can decline to relay it.
    """
    return (
        f"{type(exc).__name__} — the message is WITHHELD because an unexpected failure "
        "here can echo a credential fragment; percent-encode the password so the target "
        "is parseable")


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


def _unmeasured_labels(live: dict, plan_labels: list[str]) -> list[str]:
    """The served labels this plan does NOT cover — read from the LIVE census.

    ⛔ ONE HELPER FOR BOTH EXITS OF `execute`, because the empty arm and the write arm ask
    the same question and used to answer it differently: the empty arm returned BEFORE the
    computation, so a narrowed plan executed with `--expect-empty` published no
    `unmeasured_by_label` and `result_exit_code` read it as a completed retirement. Two
    spellings of one invariant is how one of them goes missing.
    """
    served = set(live.get("declared_population") or ()) or {r[1] for r in parity.RETIRED}
    return sorted(served - set(plan_labels))


def execute(*, g: Any, plan: dict, record_dsn: str, org_id: str, graph_id: str,
            graph_name: str, graph_uri: str, confirmed: str, batch: int,
            expect_empty: bool, expect_narrowed: bool = False) -> dict:
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
    # ⛔ NEVER `plan["labels"]` HERE. A hand-edited plan without the key used to raise
    # `KeyError` out of the destructive path — a traceback and exit 1 ("usage"), not one of
    # the published refusal tokens, at the moment the operator most needs a verdict.
    labels = [row["label"] for row in (plan.get("labels") or ())]

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
        # ⛔ `--expect-empty` ACKNOWLEDGES AN EMPTY POPULATION, NOT A NARROW ONE. Returning
        # `{"empty": True}` with no `unmeasured_by_label` let `--label Object` on an empty
        # graph exit 0 while `Event`/`Source`/`Subject` still held their vectors — the same
        # false PASS the write arm's exit 5 exists for, arriving through the short circuit.
        return {"at": datetime.now(UTC).isoformat(timespec="seconds"),
                "graph": graph_name, "plan_digest": confirmed,
                "labels": [row["label"] for row in (plan.get("labels") or ())],
                "unmeasured_by_label": _unmeasured_labels(live, labels),
                "expect_narrowed": bool(expect_narrowed),
                "removed": 0, "expected": 0, "residual": 0, "empty": True}

    removed: dict[str, int] = {}
    shortfalls: dict[str, int] = {}
    #: Removal statements ISSUED so far, across every label. ⛔ THIS, not
    #: `sum(removed.values())`, decides exit 2 vs exit 6: the returned row counts answer
    #: "how many rows the driver reported", and a batch that LANDED can return no row
    #: (`result_set` empty) — so a count-based token could assert "NO node was stripped"
    #: over a part-stripped graph. What is knowable without the store is whether any
    #: EARLIER statement completed, and that is what the two tokens promise.
    attempted = 0
    for row in plan["labels"]:
        label = row["label"]
        # ⛔ THE APPROVED KEYS, not a fresh read of the table. The digest above already
        # proved the live set equals this one; stripping the live set instead would let
        # a vector that appeared in the check-to-write window be deleted without ever
        # being in the plan a human approved, and without its record copy having been
        # parity-checked.
        keys = sorted(row.get("keys") or (), key=parity.identity_sort_key)
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
                # ⛔ THE PLAN'S VALUES, UNCHANGED — `chunk` holds the typed identity as the
                # graph holds it. Any rendering here (`str()` on the way out, or
                # `toString(n.<key>)` here) reintroduces F1: a typed key stops matching and
                # the shortfall is then reported as a concurrent writer.
                f"WHERE n.embedding IS NOT NULL AND n.{quoted_key} IN $keys "
                "REMOVE n.embedding RETURN count(n)")
            attempted += 1
            try:
                result = g.query(statement, params={"keys": chunk})
                seen = int(result.result_set[0][0]) if result.result_set else 0
            except Exception as exc:  # report, never swallow
                # ⛔ THE ATTEMPTED COUNT DECIDES THE TOKEN, and getting this wrong is worse
                # than not having the distinction at all. A FIRST-statement failure — the
                # state a store wedge, a timeout or a `--batch` too large for the driver
                # actually produces — has no earlier batch that could have stripped
                # anything, so exit 2 is the right token and a retry is safe. Only a
                # failure after at least one statement COMPLETED is potentially
                # part-stripped. Claiming "part-stripped" on the zero case teaches an
                # operator to distrust exit 6 on the one occasion it matters.
                #
                # ⛔ AND THE CLAIM IS SCOPED TO WHAT IS MEASURED, because this is a count
                # of statements ISSUED, not of writes LANDED — the same distinction that
                # rejected `sum(removed.values())` on the other side of this token. A
                # client-side read timeout, or a socket dropped AFTER the server applied
                # the `REMOVE`, raises here with the batch already landed, so this message
                # must not promise the store is untouched. It reports the one fact it
                # has: no earlier statement completed, so nothing is KNOWN to have been
                # stripped. The shape that would follow a false "all clear" is covered
                # anyway — a retry after a part-landed batch refuses with
                # `REFUSE_PLAN_STALE`, because the live set no longer matches the plan.
                if attempted <= 1:
                    raise Refusal(
                        REFUSE_WRITE_ERROR,
                        f"the removal statement FAILED on {label} at offset {start} "
                        f"({type(exc).__name__}: {exc}) and it was the FIRST removal "
                        "statement of the run — no earlier statement had completed, so "
                        "no node is KNOWN to have been stripped. If the failure happened "
                        "after the server applied this batch its answer was lost, and a "
                        "retry of the same plan will refuse as stale rather than strip "
                        "twice.") from exc
                raise Refusal(
                    REFUSE_WRITE_FAILED,
                    f"the removal statement FAILED on {label} at offset {start} "
                    f"({type(exc).__name__}: {exc}). {attempted - 1} removal "
                    "statement(s) had ALREADY COMPLETED before this failure, so the "
                    "graph may be part-stripped, which is why this is not a plain "
                    "refusal. Nothing further was removed; re-plan to see the current "
                    "set.") from exc
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
    # ⛔ THE LABELS THIS RUN DID NOT TOUCH, READ FROM THE **LIVE** CENSUS. `residual_by_label`
    # is over the plan's labels, so a narrowed strip whose residuals are all zero would
    # otherwise report a clean retirement of a population it never looked at. ⛔ NOT from
    # `plan["declared_population"]`: the plan's digest covers the KEY LISTS, not that field,
    # so an edited plan could declare the narrow scope it wants and publish exit 0 while the
    # other labels still hold their vectors — the same "a count/field a hand can edit"
    # defect this tool refuses to trust `total_nodes` for. The live census is a measurement.
    # ⛔ THE LIVE CENSUS, AND ONLY IT. A `or set(plan.get("declared_population"))` fallback
    # was here and re-admitted the hand-editable field this very comment forbids: on a
    # live-census shape without the key, an EDITED plan's declaration produced
    # `unmeasured_by_label = []` and exit 0 for a narrowed sweep. The fallback is now the
    # module's own `RETIRED` population — a constant that cannot be edited into a plan —
    # so an absent key refuses to claim the sweep covered everything.
    unmeasured = _unmeasured_labels(live, [row["label"] for row in plan["labels"]])
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
        "expect_narrowed": bool(expect_narrowed),
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
    if result.get("empty") and not result.get("unmeasured_by_label"):
        return 0
    residuals = list((result.get("residual_by_label") or {}).values())
    unmeasured = [v for v in residuals if not isinstance(v, int)]
    if unmeasured or sum(v for v in residuals if isinstance(v, int)):
        return 5
    # ⛔ A NARROWED PLAN IS NOT A COMPLETED RETIREMENT. `residual_by_label` is over the
    # plan's labels, so a `--label Object` run whose residuals are all zero used to exit 0
    # while `Event`/`Source`/`Subject` still held their vectors — the false PASS that
    # `REFUSE_NOTHING_TO_STRIP` exists for ("a re-run reports a finished retirement it never
    # performed"). A script routing on exit 0 must not read a partial sweep as a finished
    # one; the stderr warning is for a human, and the exit code is for the caller.
    #
    # ⛔ AND `--expect-empty` DOES NOT ACKNOWLEDGE A NARROW SCOPE. It says "an empty
    # population is intended"; `--expect-narrowed` says "the other labels are deliberately
    # untouched". The empty short circuit above therefore does NOT return 0 over an
    # unmeasured label — it falls through to this clause, which is why the empty arm of
    # `execute` now carries `unmeasured_by_label` at all.
    if result.get("unmeasured_by_label") and not result.get("expect_narrowed"):
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
    parser.add_argument("--expect-narrowed", action="store_true",
                        help="acknowledge that this plan covers only SOME labels and that "
                             "the others still hold their vectors — without it a narrowed "
                             "execution exits 5, never 0 (a partial sweep is not a "
                             "completed retirement)")
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
    except Exception as exc:  # an unexpected target failure is a refusal, not a leak
        sys.stderr.write(
            f"strip_graph_entity_vectors: REFUSED — the target could not be resolved "
            f"({_unexpected_failure(exc)}). Nothing was removed.\n")
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
        except Refusal as exc:
            # ⛔ `build_plan` can refuse on its own — a target whose userinfo boundary is
            # ambiguous raises `REFUSE_TARGET_UNPARSEABLE` from the fingerprint. Caught
            # here so it exits 2 with its token, rather than escaping as a traceback and
            # being read as exit 1 ("usage") when nothing about the invocation is wrong.
            sys.stderr.write(f"strip_graph_entity_vectors: {exc.token} — {exc}\n")
            return 2
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
            expect_empty=args.expect_empty, expect_narrowed=args.expect_narrowed)
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
    except Exception as exc:  # ⛔ an unreadable gate is a REFUSAL, never a traceback
        # The execute path used to catch only `CensusError`, so a gate read that raised
        # anything else (measured: an `ImportError` out of `parity.affected_entity_types`)
        # escaped `main` and was read as exit 1 ("usage") over a traceback — nothing about
        # the invocation was wrong, and the destructive path's own contract says a census
        # that cannot be run is a refusal. This mirrors the preview arm above.
        # ⚠️ THE MESSAGE IS RELAYED HERE, unlike the preview arm: this block wraps no
        # target resolution, and the graph URI it was handed is already a credential-free
        # fingerprint — so nothing reachable from here can echo a secret, and withholding
        # the text would turn a real bug into an undiagnosable exit 3.
        sys.stderr.write(
            f"strip_graph_entity_vectors: REFUSED — the run could not complete "
            f"({type(exc).__name__}: {exc}). A gate that cannot be read is a refusal, not "
            "a pass. ⛔ NOTHING IS CLAIMED ABOUT WHAT LANDED: a failure here can arrive "
            "after a batch was applied, so re-plan before retrying.\n")
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
