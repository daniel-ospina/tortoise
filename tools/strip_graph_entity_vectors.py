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
`--execute` refuses — always before the first write, never part-way through —
unless every one of these holds at the moment it runs:

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
    way to tell a completed retirement from a racing writer. The write half is `#13610`
    option 2 (PR #13617) for `object`/`subject`; for `event`/`source` it is unimplemented,
    and this tool says so rather than stripping into a race.
``REFUSE_TARGET_DRIFT``
    The graph this run resolved is not the graph the plan was built against.
``REFUSE_NOTHING_TO_STRIP``
    The plan is empty. An empty plan executed to a vacuous "success" is the shape that
    makes a re-run report a finished retirement it never performed; pass
    `--expect-empty` when an empty population is the intended state.

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

Exit status: ``0`` preview printed, or execution completed · ``1`` usage · ``2``
execution REFUSED (nothing written) · ``3`` a side could not be read · ``5`` execution
completed but residual vectors were observed afterwards (a concurrent writer).
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
            f"(including ON MATCH). That is #13610's write half (PR #13617) for "
            f"object/subject; for event/source no write-stop exists at all. Stripping "
            "now would be undone by the next write of the same node")
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
    return False, (
        f"{WRITE_GATE_MODULE}.{WRITE_GATE_SYMBOL}({entity_type!r}) answers False: the "
        "record has proved coverage for this label, so the fold no longer writes the "
        "graph copy")


def label_write_gate(label: str) -> tuple[bool, str]:
    """``(still_emits, reason)`` for a LABEL, over every `entity_type` that addresses it.

    `:Source` serves both `source` and `document` (`tortoise.security.ENTITY_TYPE_LABELS`),
    so a label is write-stopped only when EVERY type addressing it is. Checking one and
    assuming the other is how a strip half-lands on the document half of `:Source`.
    """
    types = parity.affected_entity_types(label)
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
    """The canonical digest of an exact node set: one ``label<TAB>key`` line per node.

    Sorted, tab-separated, newline-joined, `sha256`-prefixed. Two properties matter and
    both are load-bearing: the digest is over the WHOLE set (so a single key moving
    changes it), and it is over ROWS rather than a JSON blob (so a re-serialisation of
    the plan file cannot change what was approved).
    """
    body = "\n".join(f"{label}\t{key}" for label, key in sorted(set(entries)))
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
        keys = sorted(parity.read_graph_vector_keys(g, row["label"],
                                                   _key_prop(row["label"]))["keys"])
        if row["unkeyed_graph_nodes"]:
            # An unkeyed vector-bearing node cannot be named in a plan, so it cannot
            # be stripped — and it must not be silently left out of the population a
            # human is approving either. It blocks (the census already counts it into
            # graph_only), and the count is stated here in the plan.
            per_label.append({
                "label": row["label"],
                "nodes": 0,
                "unkeyed_nodes_blocking": row["unkeyed_graph_nodes"],
                "strip_ready": False,
                "blocked_reason": row["blocked_reason"],
            })
            continue
        entries.extend((row["label"], key) for key in keys)
        per_label.append({
            "label": row["label"],
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
        "graph_uri": graph_uri,
        "org_id": org_id,
        "graph_id": graph_id,
        "batch": batch,
        "population": list(report["population"]),
        "census": report["labels"],
        "labels": per_label,
        "total_nodes": len(entries),
        "digest": digest(entries),
        "digest_scope": "one `label<TAB>key` line per node, sorted, sha256",
        "ready": all(row["strip_ready"] for row in per_label) and bool(entries),
    }


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
    if not plan["ready"]:
        lines += [
            "",
            "⛔ NOT READY: at least one label is blocked. Execution would REFUSE. "
            "Resolve the block (backfill the record, or land the write-stop) and "
            "re-plan; a partial strip is not available by design.",
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
    return plan


def _live_entries(g: Any, labels: list[str]) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    for label in labels:
        keys = parity.read_graph_vector_keys(g, label, _key_prop(label))["keys"]
        entries.extend((label, key) for key in keys)
    return entries


def execute(*, g: Any, plan: dict, record_dsn: str, org_id: str, graph_id: str,
            graph_name: str, confirmed: str, batch: int,
            expect_empty: bool) -> dict:
    """Every precondition, then the batched removal. Refuses BEFORE the first write."""
    if plan["graph"] != graph_name:
        raise Refusal(
            REFUSE_TARGET_DRIFT,
            f"the plan was built against graph {plan['graph']!r} but this run resolves "
            f"{graph_name!r}. A plan is an authority over ONE target. Nothing was "
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
    total = int(plan.get("total_nodes") or 0)
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
        keys = sorted(parity.read_graph_vector_keys(
            g, label, _key_prop(label))["keys"])
        quoted_label = parity._quote_identifier(label)
        quoted_key = parity._quote_identifier(_key_prop(label))
        removed[label] = 0
        shortfalls[label] = 0
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
                raise Refusal(
                    REFUSE_GRAPH_ONLY,
                    f"the removal statement FAILED on {label} at offset {start} "
                    f"({type(exc).__name__}: {exc}). {sum(removed.values())} node(s) "
                    "were already stripped; re-plan to see the current set. Nothing "
                    "further was removed.") from exc
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
    return {
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
        "graph": graph_name,
        "plan_digest": confirmed,
        "labels": [row["label"] for row in plan["labels"]],
        "children": len(plan["labels"]),
        "removed": sum(removed.values()),
        "expected": total,
        "removed_by_label": removed,
        "shortfall_by_label": shortfalls,
        "residual_by_label": residual,
        "empty": False,
    }


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
    parser.add_argument("--batch", type=int, default=DEFAULT_BATCH,
                        help=f"nodes per removal statement (default {DEFAULT_BATCH})")
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
    if args.batch <= 0:
        parser.error("--batch must be > 0")
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
                batch=args.batch)
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
        result = execute(
            g=proj.g, plan=plan, record_dsn=record_dsn, org_id=args.org_id,
            graph_id=args.graph_id, graph_name=graph_name,
            confirmed=args.confirm_plan_digest, batch=args.batch,
            expect_empty=args.expect_empty)
    except Refusal as exc:
        sys.stderr.write(
            f"strip_graph_entity_vectors: {exc.token} — {exc}\n")
        return 2
    except CensusError as exc:
        sys.stderr.write(f"strip_graph_entity_vectors: REFUSED — {exc}\n")
        return 3

    print(json.dumps(result, indent=2))
    if args.json:
        Path(args.json).write_text(json.dumps(result, indent=2) + "\n")
    residual = sum(v for v in result["residual_by_label"].values()
                   if isinstance(v, int))
    if residual and not result["empty"]:
        sys.stderr.write(
            f"strip_graph_entity_vectors: {residual} vector(s) remain after the "
            "removal — a CONCURRENT WRITER re-added them, or the write gate is not "
            "actually closed on the running deployment. Re-plan; do not re-execute "
            "this plan (its preconditions no longer hold).\n")
        return 5
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
