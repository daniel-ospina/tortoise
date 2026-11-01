#!/usr/bin/env python3
"""#10017 step 9: the PURGE of pre-#10000 turn nodes — preview by default, and
the preview is the token.

This tool is the destructive half of #10000, and it is built so that its
destructive arm cannot run by accident. **`--dry-run` is the DEFAULT**; it
reads the graph and the record through the census that already gates this
issue (`tools/turn_parity_census.py`, #10029 — imported, never re-implemented),
classifies every session that holds turn nodes, and writes an APPROVABLE PLAN
artefact: the exact session list, the exact node count per session, the total,
the excluded sessions with their class and reason, a canonical digest of the
whole set, and the recovery path (or `NEEDS-RECOVERY-PATH(<what is missing>)`).

`--execute` exists. It refuses unless EVERY precondition is asserted at
runtime, and one of those assertions is the **exact-set precondition**: the
census it recomputes live must yield the SAME canonical digest as the plan a
human approved. A single node more, a single node less, a session added or
dropped — any difference refuses and names it.

WHY THIS IS NOT A SECOND CENSUS
===============================
`tools/turn_parity_census.py` already owns the two store legs, the paging
guard against FalkorDB's silent `RESULTSET_SIZE` truncation, the tenant-scoped
record read (`record_projection.scope_connection`, #10024), the field-by-field
parity definition and the anomaly partition. This module imports all of it.
What it adds is the layer the census deliberately does not have:

    census      → "are these two stores equivalent, per session?"
    this tool   → "which sessions may be touched, exactly, and what is the
                   token that authorises touching exactly those?"

so the parity rule, the turn shape and the read legs have ONE implementation
in this repo, and the purge inherits every refusal the census already makes
(an unreadable side is exit 3 there and `REFUSED unreadable-side` here; a
non-turn `is_episodic` node refuses rather than being silently skipped).

THE CLASSES (what the plan is made of, per session holding graph turn nodes)
===========================================================================
    PARITY                     every graph turn node has a record row whose
                               key AND fields match exactly. PURGE-ELIGIBLE.
    PARITY + RECORD-AHEAD      …and the record holds MORE turns than the graph
                               (post-cutover captures appended to the same
                               session). Still PARITY — the record is a
                               superset, so deleting the graph copies loses
                               nothing — but it is COUNTED SEPARATELY, because
                               it is the one shape where "the graph and the
                               record hold the same number of turns" is false
                               for a session we are about to purge, and an
                               operator reading only totals would not see it.
    RECORD-BEHIND(<n>)         the graph holds <n> turn nodes the record does
                               not. ⛔ MUST NOT BE PURGED — deleting those
                               destroys the only copy of the transcript. The
                               answer is a backfill (`tools/backfill_graph_turns
                               .py` / `tools/backfill_copy_turns.py`), never a
                               delete.
    MISMATCH(<n>)              key present in both, but <n> nodes disagree on a
                               parity field (text, speaker, kind, status,
                               createdAt, or a vector the graph holds and the
                               record lost). ⛔ MUST NOT BE PURGED: the record
                               holds a DIFFERENT transcript, so a delete
                               substitutes one for the other silently.

WHAT PARITY IS MATCHED ON (the brief's "not just the count" rule)
================================================================
`node_key` equality (the record's own key, `public.point.node_key`, against
the graph's `t.id`) PLUS field equality on `content`, `speaker`, `point_kind`,
`status`, `created_at`, and `embedding` (component-wise through binary32 — the
graph's `vecf32` storage). The definition is the census's, stated field by
field in `turn_parity_census.py`'s docstring and pinned in
`tests/test_10017_turn_parity_census.py`; this tool does not restate it in
code, it consumes the census's verdict. A COUNT-ONLY comparison cannot be
expressed here: `classify_sessions` reads `problems` (per-node, per-field),
never `graph_turn_nodes == record_turn_rows`.

THE EXACT-SET PRECONDITION (the shape the test-reap rule uses)
=============================================================
A destructive sweep must re-derive its victim set IMMEDIATELY before it acts
and refuse if that set is not the one that was approved. The token is a
SHA-256 over a canonical serialisation of the purge set:

    plan_digest = sha256( "v1\\n" + "\\n".join(sorted(f"{session}\\x00{node_id}"))
                          + "\\n" )

One line per (session, node) pair, sorted, so the digest is order-independent
and changes if ANY pair is added, removed or re-homed to another session. The
human approves the digest; `--confirm-plan-digest <digest>` hands it back;
`--execute` recomputes the set from the live stores and refuses unless the two
digests are equal, printing the first symmetric differences.

RECOVERY — what brings a PARITY session's transcript back if the record is
wrong (§4 of the brief)
=======================================================================
There are two halves and the plan states both, because they are NOT
symmetrical:

  * RECORD (Postgres) — the rows are NOT deleted by this tool. The deletion
    is journaled as `EntityMutated` op=delete carrying `cache_tier =
    'record_only'` (`tortoise/sdk.py::_journal_turn_removal`, #10076), whose
    record-side fold is a TIER DEMOTE (`record_projection._tier_demote_
    statement`), not a row DELETE. `record_read._turns` applies no tier
    predicate, so the transcript keeps serving; `cache_projection.
    read_record_view` skips `record_only`, so a rebuild does not re-node-ify
    it. The record's own durability is `python -m tortoise record-backup`
    (dump → encrypt → R2, `docs/infra-runbook.md` §3.5) with a `--drill`
    restore into a scratch database.
  * GRAPH — a purged turn node CANNOT be rebuilt from the journal: the delete
    is journaled (precondition 4), so replay correctly does not resurrect it.
    The only reverse path is record → graph, i.e. flip the demoted rows back
    to `cache_tier = 'graph'` and run `rebuild_from_record`, or restore a
    graph backup taken before the purge.

⛔ **Neither half is PROVEN for any target deployment as of this writing.**
No `record-backup --drill` run against the target is recorded on #10017, and
the reverse (tier flip → `rebuild_from_record`) has never been exercised for a
turn tier. So the tool prints `NEEDS-RECOVERY-PATH(...)` naming exactly what
is missing, and `--execute` REFUSES without a `--restore-proof` artefact (the
issue's own precondition 3: one sampled session deleted from a scratch graph
and re-served from the record). A green census opens the gate; it does not
walk through it.

WHAT THE EXECUTE ARM IS, AND WHAT IT IS NOT
===========================================
⛔ **It has never been run, in this lane or any other, and it must not be run
until the owner approves the plan this tool produces.** It is written so that
the ordering is the landed one rather than an invention:

    1. delete the planned ids (exact `t.id IN $ids` per session) and VERIFY
       absence — the ids that come back are the ids that get journaled, never
       an earlier gather (#7951's TOCTOU lesson);
    2. journal each removed id through `sdk._journal_turn_removal`, which is
       the ONE landed removal record (#10076) — `None` (no journal configured)
       or `False` (the append raised) REFUSES rather than reporting a removal
       the journal cannot replay;
    3. refuse unless the purged total equals the plan's stated total.

The residual (a graph that still holds an `is_episodic` node after the purge)
is established by re-running the census with `--expect-empty`, which is step 5
of the issue's ordering and is NOT this tool's claim to make about itself.

Because it is unexercised, its own refusals are what CI pins
(`tests/test_10017_purge_guard.py`), not its deletions: a guard that has never
refused anything is decoration.

USAGE
=====
    # 1. PREVIEW (the default — no flag needed, nothing is written to a store)
    python tools/purge_legacy_turns.py \
        --uri '<target FalkorDB URI>' --graph-name <graph> \
        --record-dsn "$TORTOISE_RECORD_DSN" \
        --org-id <org> --graph-id <graph> \
        --health-json /tmp/health.json \
        --target-note 'prod: tortoise-y4mjjq, approved by <owner> <date>' \
        --plan-out /tmp/purge-plan.json [--json /tmp/census.json]

    # 2. APPROVE — a human reads the plan and its digest.
    # 3. EXECUTE (refuses unless every precondition holds, including a restore
    #    proof and the plan's digest):
    python tools/purge_legacy_turns.py --execute --plan /tmp/purge-plan.json \
        --confirm-plan-digest <the digest the plan states> \
        --restore-proof /tmp/restore-proof.json --health-json /tmp/health.json \
        ... (the same target flags, re-derived live)

EXIT CODES
==========
    0  a plan was produced (dry run) — or, under `--execute`, the planned set
       was purged and journaled
    1  usage / configuration (bad flags; the tool never guesses a target)
    2  REFUSED by the guard — the message names which precondition failed
    3  a side could not be READ: an absence is NOT established, so neither a
       plan nor a purge is honest
"""

from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter before the imports below — a module-level
# 3.11+-only import would fail first with an UNATTRIBUTED error. Must be the
# first statement after the docstring and `import sys`
# (`tests/test_entry_point_python_guard.py` pins that for every tools/ entry
# point).
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/purge_legacy_turns.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/purge_legacy_turns.py`"
    )

import argparse
import hashlib
import json
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import turn_parity_census as census_mod  # the ONE parity implementation

#: The digest's format tag. Bumping it invalidates every previously approved
#: plan on purpose: a plan approved under a different canonicalisation must not
#: silently re-bind to a set this tool now serialises differently.
_PLAN_DIGEST_VERSION = "v1"

#: The classes a session can be in. PARITY (with its RECORD-AHEAD flag) is the
#: ONLY one that may be purged; the signature of this tuple is that "eligible"
#: is a property of the class and not of a caller's judgement.
CLASS_PARITY = "PARITY"
CLASS_RECORD_AHEAD = "RECORD-AHEAD"
CLASS_RECORD_BEHIND = "RECORD-BEHIND"
CLASS_MISMATCH = "MISMATCH"

#: Refusal codes. Each is a NAME, not a sentence, so a test asserts the reason
#: and an operator greps the log — a guard whose refusals are not
#: distinguishable is a single "no" for every failure mode.
REFUSE_NO_PLAN = "no-plan"
REFUSE_PLAN_UNREADABLE = "plan-unreadable"
REFUSE_NO_TARGET_NOTE = "no-target-note"
REFUSE_RECORD_NOT_ARMED = "record-not-armed"
REFUSE_NO_RESTORE_PROOF = "no-restore-proof"
REFUSE_RESTORE_PROOF_STALE = "restore-proof-stale"
REFUSE_CONFIRMATION_MISMATCH = "confirmation-mismatch"
REFUSE_EXACT_SET_MISMATCH = "exact-set-mismatch"
REFUSE_RECORD_BEHIND = "record-behind"
REFUSE_MISMATCH = "mismatch"
REFUSE_NOT_IN_PLAN = "session-not-in-plan"
REFUSE_JOURNAL_UNCONFIRMED = "journal-unconfirmed"
REFUSE_NO_JOURNAL = "no-journal"
REFUSE_CENSUS_NOT_CLEAN = "census-not-clean"
REFUSE_TARGET_DRIFT = "target-drift"


def classify_sessions(report: dict) -> dict:
    """Split the census report into PURGE-ELIGIBLE and EXCLUDED, per session.

    Reads the census's per-node, per-field `problems` — never a count
    comparison. Returns::

        {"eligible": [ {session, class, graph_turn_nodes, record_turn_rows,
                        record_ahead, ids} ],
         "excluded": [ {session, class, reason, missing, mismatched,
                        graph_turn_nodes, sample_ids} ],
         "counts": {CLASS_PARITY: n, ...}}

    `ids` is the exact node list a purge of that session would delete, sorted,
    derived from the census report's own `dry_run.would_delete_ids` so there is
    one statement of the purge set per session.
    """
    report = report or {}
    dry = report.get("dry_run") or {}
    per_session_ids: dict[str, list[str]] = {}
    for tid in dry.get("would_delete_ids") or ():
        # The census's OWN derivation (`_TURN_ID_RE`, "the ONE derivation #3551"),
        # never a local re-spelling: a second split rule that disagreed with the
        # census's would place a node in one session's purge list while the gate
        # compared it under another.
        match = census_mod._TURN_ID_RE.match(str(tid))
        if match is None:  # pragma: no cover - the census only emits turn keys
            continue
        per_session_ids.setdefault(match.group("sid"), []).append(str(tid))

    eligible: list[dict] = []
    excluded: list[dict] = []
    for verdict in (report.get("parity") or {}).get("clean_sessions") or ():
        sid = verdict["session"]
        ids = sorted(per_session_ids.get(sid, []))
        ahead = max(0, int(verdict.get("record_turn_rows") or 0)
                    - int(verdict.get("graph_turn_nodes") or 0))
        # ⛔ The per-session NODE TOTAL and the per-session ID LIST must be the
        # same set. They are produced by the same census, so a divergence means
        # the id list is the smaller one — i.e. a plan whose headline count says
        # "N nodes would be purged" while the digest (and the delete) cover
        # fewer. Fail closed on the divergence rather than publishing a count
        # nothing will act on.
        if len(ids) != int(verdict.get("graph_turn_nodes") or 0):
            raise ValueError(
                f"census session {sid!r}: {verdict.get('graph_turn_nodes')} "
                f"graph turn node(s) but {len(ids)} deletable id(s) — the "
                "census's count and its id list are not the same set, so no "
                "plan can state what a purge would touch")
        eligible.append({
            "session": sid,
            "class": CLASS_PARITY,
            "record_ahead": ahead,
            "graph_turn_nodes": int(verdict.get("graph_turn_nodes") or 0),
            "record_turn_rows": int(verdict.get("record_turn_rows") or 0),
            "ids": ids,
        })

    for verdict in (report.get("parity") or {}).get("failed_sessions") or ():
        sid = verdict["session"]
        problems = verdict.get("problems") or []
        missing = [p for p in problems if p.get("problem") == "missing_in_record"]
        mismatched = [p for p in problems if p.get("problem") != "missing_in_record"]
        if missing:
            # RECORD-BEHIND wins the class over MISMATCH: the dangerous half is
            # the transcript the record does not hold at all, and an operator
            # who reads "MISMATCH" would think a field repair is the remedy
            # when the remedy is a BACKFILL.
            klass = CLASS_RECORD_BEHIND
            reason = (f"{len(missing)} graph turn node(s) have no record row — "
                      "purging would destroy the only copy of those turns; "
                      "backfill first (tools/backfill_graph_turns.py)")
        else:
            klass = CLASS_MISMATCH
            reason = (f"{len(problems)} parity field disagreement(s) on "
                      f"{len({p.get('node') for p in problems})} node(s) — the "
                      "record holds a DIFFERENT transcript for those turns")
        excluded.append({
            "session": sid,
            "class": klass,
            "reason": reason,
            "missing": len(missing),
            "mismatched": len(mismatched),
            "graph_turn_nodes": int(verdict.get("graph_turn_nodes") or 0),
            "record_turn_rows": int(verdict.get("record_turn_rows") or 0),
            "sample_ids": sorted({str(p.get("node")) for p in problems})[:10],
        })

    # Anomalies are nodes in the DELETE SET the census could not compare. They
    # are not sessions, so they cannot be planned — and they are already
    # carried into the plan's `census.anomalies` counts, so a plan is never
    # "complete" over a graph it did not classify.

    counts = {CLASS_PARITY: len(eligible),
              CLASS_RECORD_AHEAD: sum(1 for e in eligible if e["record_ahead"]),
              CLASS_RECORD_BEHIND: sum(1 for e in excluded
                                       if e["class"] == CLASS_RECORD_BEHIND),
              CLASS_MISMATCH: sum(1 for e in excluded
                                  if e["class"] == CLASS_MISMATCH)}
    return {"eligible": eligible, "excluded": excluded, "counts": counts}


def plan_digest(eligible: list[dict]) -> str:
    """SHA-256 over the canonical (session, node) set — the approval token.

    Order-independent by construction (sorted pairs), and sensitive to ANY
    change: one pair added, removed, or moved to another session changes the
    digest. This is the "exact-set precondition": the human approves this
    string, and `--execute` refuses unless the live set re-derives it.
    """
    pairs = sorted(f"{e['session']}\x00{tid}"
                   for e in eligible for tid in e["ids"])
    payload = _PLAN_DIGEST_VERSION + "\n" + "\n".join(pairs) + "\n"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def digest_set(eligible: list[dict]) -> set[str]:
    """The same canonical pairs as `plan_digest`, as a set — for diffing.

    A digest can say "not equal"; only the set can say WHICH pair differs, and
    an operator refusing an execute needs the difference, not the hash.
    """
    return {f"{e['session']}\x00{tid}" for e in eligible for tid in e["ids"]}


def build_plan(report: dict, *, target: dict, health: dict | None,
               restore_proof: dict | None, target_note: str,
               recovery_missing: list[str]) -> dict:
    """The approvable artefact: pairs, counts, exclusions, digest, recovery."""
    classified = classify_sessions(report)
    eligible = classified["eligible"]
    pop = report.get("population") or {}
    record = report.get("record") or {}
    total_nodes = sum(e["graph_turn_nodes"] for e in eligible)
    return {
        "tool": "tools/purge_legacy_turns.py",
        "issue": 10017,
        "issue_context": ("the destructive half of #10000; IRREVERSIBLE — the "
                          "record is the only surviving copy"),
        "target": target,
        "target_note": target_note,
        "armed_evidence": health,
        "census": {
            "episodic_graph_nodes": pop.get("episodic_graph_nodes"),
            "turn_shaped_nodes": pop.get("turn_shaped_nodes"),
            "sessions_with_graph_turns": pop.get("sessions_with_graph_turns"),
            "outside_population": pop.get("outside_population"),
            "record_turn_rows_total": record.get("turn_rows_total"),
            "record_episodic_rows_not_turn_shaped":
                record.get("episodic_rows_not_turn_shaped"),
            "verdict": report.get("verdict"),
            "refused_for": report.get("refused_for") or [],
            "anomalies": {k: len(v) for k, v in (report.get("anomalies") or {})
                          .items() if v},
        },
        "parity_census": classified["counts"],
        "purge": {
            "sessions": eligible,
            "session_count": len(eligible),
            "turn_node_total": total_nodes,
            "per_session_node_counts": {e["session"]: e["graph_turn_nodes"]
                                        for e in eligible},
            "record_ahead_sessions": {e["session"]: e["record_ahead"]
                                      for e in eligible if e["record_ahead"]},
        },
        "excluded": {
            "sessions": classified["excluded"],
            "session_count": len(classified["excluded"]),
            "turn_nodes_held_back": sum(e["graph_turn_nodes"]
                                        for e in classified["excluded"]),
        },
        "plan_digest": plan_digest(eligible),
        "recovery": {
            "record_side": ("journaled EntityMutated op=delete with "
                            "cache_tier='record_only' → tier DEMOTE, not a row "
                            "delete; record_read._turns keeps serving it"),
            "record_durability": ("python -m tortoise record-backup "
                                  "(dump→encrypt→R2) with --drill restore "
                                  "(docs/infra-runbook.md §3.5)"),
            "graph_side": ("a purged turn CANNOT be rebuilt from the journal "
                           "(the delete is journaled) — only a tier flip back "
                           "to 'graph' + rebuild_from_record, or a pre-purge "
                           "graph backup"),
            "restore_proof": restore_proof,
            "verdict": (f"NEEDS-RECOVERY-PATH({'; '.join(recovery_missing)})"
                        if recovery_missing else "RECOVERY-PATH-STATED"),
            "missing": recovery_missing,
        },
    }


def evaluate_gate(*, plan: dict | None, live_report: dict | None,
                  armed: bool, restore_proof: dict | None,
                  confirm_digest: str, target_note: str) -> tuple[str, str]:
    """The `--execute` gate. Pure: no I/O, no deletion, one verdict.

    Returns `(refusal_code, message)` where an EMPTY code means "every
    precondition holds". Every branch is a named refusal reachable in
    isolation, which is what `tests/test_10017_purge_guard.py` asserts — the
    three the brief mandates (record not armed, a target session
    RECORD-BEHIND, a live census one node off the approved set) plus the two
    the issue adds (no restore proof, no matching confirmation token).
    """
    if not target_note or not str(target_note).strip():
        return (REFUSE_NO_TARGET_NOTE,
                "no --target-note: a purge must name the deployment it is "
                "aimed at, so the plan and the run cannot drift apart")
    if not plan:
        return (REFUSE_NO_PLAN,
                "no --plan: --execute acts on an APPROVED plan artefact "
                "only. Run without --execute to produce one")
    if not armed:
        return (REFUSE_RECORD_NOT_ARMED,
                "the record is not proven ARMED for this deployment (a "
                "/health artefact with record.armed=true is required, #9548 / "
                "PR #9989). An unarmed record holds no turns, so the delete "
                "would have no surviving copy")
    if not restore_proof:
        return (REFUSE_NO_RESTORE_PROOF,
                "no --restore-proof: #10017 precondition 3 requires one "
                "sampled session deleted from a SCRATCH graph and re-served "
                "from the record before any real pass. A green census opens "
                "the gate; it does not prove the way back")

    planned_sessions = [e["session"] for e in
                        (plan.get("purge", {}) or {}).get("sessions") or []]
    proof_problem = _restore_proof_problem(restore_proof, planned_sessions)
    if proof_problem:
        return (REFUSE_RESTORE_PROOF_STALE,
                f"the restore proof does not establish precondition 3: "
                f"{proof_problem}")

    approved = plan.get("plan_digest") or ""
    # ⛔ THE ARTEFACT MUST BE SELF-CONSISTENT. The digest is what a human
    # approved; the `purge.sessions` list is what `_execute` deletes. They are
    # two fields of one JSON file, so an edited, merged or regenerated plan
    # could carry a set that never serialised to the digest it still displays —
    # and every check below compares the LIVE set to that digest, never the
    # list to it. Re-derive it here, first, before anything trusts either.
    if plan_digest(plan.get("purge", {}).get("sessions") or []) != approved:
        return (REFUSE_NOT_IN_PLAN,
                "the plan's own purge.sessions do not serialise to the "
                "digest it states — the artefact has been edited since it "
                "was approved; regenerate it and re-approve")
    if not confirm_digest or confirm_digest != approved:
        return (REFUSE_CONFIRMATION_MISMATCH,
                f"--confirm-plan-digest does not match the approved plan "
                f"(plan {approved[:12]}…, given "
                f"{(confirm_digest or '<none>')[:12]}…)")

    if live_report is None:
        return (REFUSE_EXACT_SET_MISMATCH,
                "the live census produced no report, so the approved set "
                "cannot be re-derived — an unverifiable set is not an "
                "approved set")

    classified = classify_sessions(live_report)

    # A census that REFUSED on its ANOMALY partition is not clean: those nodes
    # are in the delete set (`is_episodic = true`) and were never compared, and
    # they belong to NO session, so they cannot be excluded by name either.
    # A refusal on PARITY, by contrast, IS handled class-by-class below — that is
    # what the per-session classes exist for.
    refused_for = live_report.get("refused_for") or []
    if "outside_population" in refused_for:
        return (REFUSE_CENSUS_NOT_CLEAN,
                "the live census REFUSED on its anomaly partition: the graph "
                "holds is_episodic node(s) the census could not classify "
                "(orphan / cross-wired / non-turn kind / non-string id). They "
                "are in the delete set and in no session's plan, so no "
                "exact-set check can cover them — resolve them first")

    # CLASS BEFORE SET. A planned session that the live census now EXCLUDES is
    # a more specific diagnosis than "the set differs": the graph still holds
    # the approved ids, and it is the RECORD side that moved. Reporting that as
    # `exact-set-mismatch` would send an operator to re-approve a set that is
    # still correct, when the remedy is a backfill.
    planned = {e["session"] for e in plan.get("purge", {}).get("sessions") or []}
    behind = [e["session"] for e in classified["excluded"]
              if e["class"] == CLASS_RECORD_BEHIND and e["session"] in planned]
    if behind:
        return (REFUSE_RECORD_BEHIND,
                "target session(s) RECORD-BEHIND — the record does not hold "
                "these turns, so deleting them destroys the transcript: "
                + ", ".join(behind[:10]))
    mismatched = [e["session"] for e in classified["excluded"]
                  if e["class"] == CLASS_MISMATCH and e["session"] in planned]
    if mismatched:
        return (REFUSE_MISMATCH,
                "target session(s) MISMATCH — the record holds a DIFFERENT "
                "transcript for these turns: " + ", ".join(mismatched[:10]))

    live_digest = plan_digest(classified["eligible"])
    if live_digest != approved:
        approved_set = digest_set(plan.get("purge", {}).get("sessions") or [])
        live_set = digest_set(classified["eligible"])
        added = sorted(live_set - approved_set)[:5]
        removed = sorted(approved_set - live_set)[:5]
        detail = []
        if added:
            detail.append("in the live census but NOT approved: "
                          + ", ".join(p.replace("\x00", " / ") for p in added))
        if removed:
            detail.append("approved but absent live: "
                          + ", ".join(p.replace("\x00", " / ") for p in removed))
        return (REFUSE_EXACT_SET_MISMATCH,
                f"the live census does not re-derive the approved set "
                f"(plan {approved[:12]}…, live {live_digest[:12]}…; "
                f"approved {len(approved_set)} node(s), live "
                f"{len(live_set)}). One node of difference is enough to "
                "refuse — re-run the preview and re-approve. "
                + "; ".join(detail))

    return ("", "every precondition asserted; the approved set is the live set")


def _restore_proof_problem(proof, planned_sessions: list[str]) -> str:
    """Why this restore-proof artefact is not evidence — or `""`.

    `--restore-proof` used to be truthiness-checked, which made an arbitrary
    `{"garbage": 1}` satisfy #10017's precondition 3. The artefact must name
    the session it restored (and that session must be in the plan), state that
    the transcript was re-served FROM THE RECORD, and name the tenant/graph it
    was done on.
    """
    if not isinstance(proof, dict):
        return "the artefact is not a JSON object"
    missing = [k for k in ("scratch_session", "served_from_record")
               if k not in proof]
    if missing:
        return f"it does not state {', '.join(missing)}"
    if proof.get("served_from_record") is not True:
        return ("it does not state that the transcript was re-served from the "
                "record (served_from_record must be true)")
    if proof.get("scratch_session") not in planned_sessions:
        return (f"the session it restored ({proof.get('scratch_session')!r}) "
                "is not one of the sessions this plan purges, so it proves "
                "nothing about them")
    return ""


def _load_json(path: str | None) -> dict | None:
    if not path:
        return None
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _health_armed(health: dict | None) -> bool:
    """Is the record ARMED per a `/health` artefact (PR #9989)?

    Accepts both shapes a caller can legitimately hold: the nested
    `{"record": {"armed": true}}` the endpoint emits, and a flattened
    `{"record.armed": true}` some collectors produce. Anything else — including
    an `armed` that is `"true"` (a STRING) — is NOT armed: this gate's refusal
    is the safe direction, so truthiness is deliberately not used.
    """
    if not isinstance(health, dict):
        return False
    nested = health.get("record")
    if isinstance(nested, dict) and nested.get("armed") is True:
        return True
    return health.get("record.armed") is True


def _target_from_args(args) -> dict:
    dsn = args.record_dsn or os.environ.get("TORTOISE_RECORD_DSN", "")
    return {
        "graph": args.graph_name or ("<uri default>"),
        "uri_host": _uri_host(args.uri),
        "db_path": args.db_path,
        "org_id": args.org_id,
        "graph_id": args.graph_id,
        # NEVER the DSN itself: a plan artefact is shared, and a credential in
        # it is a leak. The presence/shape of the DSN is all the plan needs.
        "record_dsn_present": bool(dsn),
    }


def _uri_host(uri: str | None) -> str | None:
    if not uri:
        return None
    from urllib.parse import urlparse
    try:
        return urlparse(uri).netloc or None
    except Exception:  # pragma: no cover - malformed URI
        return None


class _UsageParser(argparse.ArgumentParser):
    """Usage errors exit ``1``; ``2`` is the guard's REFUSED code, ``3`` the
    unreadable-side code (the census's own contract, kept identical)."""

    def error(self, message: str):
        self.print_usage(sys.stderr)
        print(f"{self.prog}: error: {message}", file=sys.stderr)
        raise SystemExit(1)


def _recovery_missing(args, restore_proof: dict | None,
                      health: dict | None) -> list[str]:
    """What is still absent on the recovery axis — named, never assumed."""
    missing: list[str] = []
    if not restore_proof:
        missing.append(
            "no --restore-proof artefact: #10017 precondition 3 (one sampled "
            "session deleted from a scratch graph and re-served from the "
            "record) has not been run for this target")
    if not _health_armed(health):
        missing.append(
            "no /health artefact showing record.armed=true for this target "
            "(#9548 / PR #9989)")
    missing.append(
        "no recorded `python -m tortoise record-backup --drill` run against "
        "this target (the record's restore is implemented and documented, "
        "not proven here)")
    missing.append(
        "the reverse graph path (cache_tier 'record_only' → 'graph' + "
        "rebuild_from_record) has never been exercised for a turn tier")
    return missing


def main(argv: list[str] | None = None) -> int:
    parser = _UsageParser(
        prog="purge_legacy_turns",
        description="#10017 step 9 — the gated purge of pre-#10000 turn "
                    "nodes. DRY RUN IS THE DEFAULT: nothing is deleted unless "
                    "--execute is passed AND every precondition holds.",
    )
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--uri", help="FalkorDB URI of the target graph")
    src.add_argument("--db-path", help="embedded graph path instead of a URI")
    parser.add_argument("--graph-name", default=None,
                        help="the graph inside the store")
    parser.add_argument("--record-dsn", default=None,
                        help="record DSN (default: TORTOISE_RECORD_DSN)")
    parser.add_argument("--org-id", default=os.environ.get(
        "TORTOISE_RECORD_ORG_ID", ""), help="record tenant org")
    parser.add_argument("--graph-id", default=os.environ.get(
        "TORTOISE_RECORD_GRAPH_ID", ""), help="record tenant graph")
    parser.add_argument("--target-note", default="",
                        help="REQUIRED: name the deployment this plan is for "
                             "(recorded verbatim in the plan)")
    parser.add_argument("--health-json", default=None,
                        help="a /health artefact (record.armed evidence)")
    parser.add_argument("--restore-proof", default=None,
                        help="a restore-proof artefact (issue precondition 3)")
    parser.add_argument("--census-json", default=None,
                        help="reuse a census report instead of reading the "
                             "stores (preview only — --execute ALWAYS "
                             "re-derives live)")
    parser.add_argument("--json", default=None,
                        help="write the full census report to this path")
    parser.add_argument("--plan-out", default=None,
                        help="write the plan artefact to this path")
    parser.add_argument("--sample", type=int, default=3,
                        help="sample size per session in the census report")
    parser.add_argument("--expect-empty", action="store_true",
                        help="allow a graph holding zero episodic nodes")
    parser.add_argument("--execute", action="store_true",
                        help="PURGE the approved set. Refuses unless every "
                             "precondition is asserted at runtime and the "
                             "live census re-derives the plan's exact set")
    parser.add_argument("--plan", default=None,
                        help="the approved plan artefact (--execute)")
    parser.add_argument("--confirm-plan-digest", default=None,
                        help="the plan digest the human approved (--execute)")
    parser.add_argument("--event-log-path", default=None,
                        help="the JSONL journal a rebuild of THIS graph reads "
                             "(--execute). Required: an unjournaled removal is "
                             "a resurrection (#3316)")
    args = parser.parse_args(argv)

    if args.sample < 0:
        parser.error("--sample must be >= 0")
    record_dsn = args.record_dsn or os.environ.get("TORTOISE_RECORD_DSN", "")
    if not args.org_id or not args.graph_id:
        sys.stderr.write(
            "purge_legacy_turns: --org-id/--graph-id (or "
            "TORTOISE_RECORD_ORG_ID/TORTOISE_RECORD_GRAPH_ID) are required — "
            "the census must be filed under the deployment's real tenant, "
            "never a guess. Nothing was read and nothing was deleted.\n")
        return 1

    # ── the census (imported, never re-implemented) ──────────────────────
    try:
        if args.census_json:
            if args.execute:
                sys.stderr.write(
                    "purge_legacy_turns: --census-json is a PREVIEW aid; "
                    "--execute re-derives the census from the live stores "
                    "every time (a cached census is exactly the stale set the "
                    "exact-set precondition exists to refuse).\n")
                return 1
            report = _load_json(args.census_json)
        else:
            if not record_dsn:
                sys.stderr.write(
                    "purge_legacy_turns: no record DSN — pass --record-dsn or "
                    "set TORTOISE_RECORD_DSN. The record cannot be read, so an "
                    "absence is NOT established.\n")
                return 1
            graph = census_mod.read_graph_turns(args.uri, args.db_path,
                                                args.graph_name)
            record = census_mod.read_record_turns(record_dsn, args.org_id,
                                                  args.graph_id)
            if (not graph["nodes"] and not graph.get("unidentifiable")
                    and not args.expect_empty):
                sys.stderr.write(
                    "purge_legacy_turns: the graph holds ZERO is_episodic "
                    "nodes — an absence is NOT established (a wrong "
                    "--graph-name/--db-path is indistinguishable from a "
                    "drained graph). Pass --expect-empty if the graph is known "
                    "to hold no turn nodes.\n")
                return 3
            report = census_mod.census(graph, record, sample=args.sample)
    except census_mod.CensusError as exc:
        sys.stderr.write(f"purge_legacy_turns: {exc}\n")
        return 3

    try:
        health = _load_json(args.health_json)
        restore_proof = _load_json(args.restore_proof)
    except Exception as exc:
        # A mistyped path or malformed JSON is a CONFIG fault: exit 3 (a side
        # could not be read), never an uncaught traceback a caller reads as a
        # crash.
        sys.stderr.write(f"purge_legacy_turns: --health-json/--restore-proof "
                         f"could not be read ({type(exc).__name__}: {exc}) — "
                         f"an absence is NOT established.\n")
        return 3
    target = _target_from_args(args)

    if args.execute:
        plan = None
        try:
            plan = _load_json(args.plan)
        except Exception as exc:
            sys.stderr.write(f"purge_legacy_turns: REFUSED "
                             f"{REFUSE_PLAN_UNREADABLE}: {exc}\n")
            return 2
        code, message = evaluate_gate(
            plan=plan, live_report=report, armed=_health_armed(health),
            restore_proof=restore_proof,
            confirm_digest=args.confirm_plan_digest or "",
            target_note=args.target_note)
        if code:
            sys.stderr.write(f"purge_legacy_turns: REFUSED {code}: "
                             f"{message}\nNothing was deleted.\n")
            return 2
        return _execute(plan, args, target)

    plan = build_plan(report, target=target, health=health,
                      restore_proof=restore_proof,
                      target_note=args.target_note,
                      recovery_missing=_recovery_missing(args, restore_proof,
                                                         health))
    if args.json:
        Path(args.json).write_text(
            json.dumps(report, indent=2, default=str), encoding="utf-8")
    if args.plan_out:
        Path(args.plan_out).write_text(
            json.dumps(plan, indent=2, default=str), encoding="utf-8")

    counts = plan["parity_census"]
    purge = plan["purge"]
    print(f"target     : graph={target['graph']} host={target['uri_host']} "
          f"tenant={target['org_id']}/{target['graph_id']}")
    print(f"population : {plan['census']['episodic_graph_nodes']} episodic "
          f"graph node(s), {plan['census']['turn_shaped_nodes']} turn-shaped, "
          f"across {plan['census']['sessions_with_graph_turns']} session(s)")
    print(f"record     : {plan['census']['record_turn_rows_total']} turn row(s)")
    print(f"census     : PARITY({counts[CLASS_PARITY]}, of which "
          f"{counts[CLASS_RECORD_AHEAD]} RECORD-AHEAD) · "
          f"RECORD-BEHIND({counts[CLASS_RECORD_BEHIND]}) · "
          f"MISMATCH({counts[CLASS_MISMATCH]})")
    for entry in plan["excluded"]["sessions"]:
        print(f"  EXCLUDED  {entry['class']} {entry['session']}: "
              f"{entry['graph_turn_nodes']} graph node(s) — {entry['reason']}")
    print(f"plan       : {purge['session_count']} session(s), "
          f"{purge['turn_node_total']} turn node(s) would be purged "
          f"(HELD BACK: {plan['excluded']['turn_nodes_held_back']})")
    print(f"digest     : {plan['plan_digest']}")
    print(f"recovery   : {plan['recovery']['verdict']}")
    for item in plan["recovery"]["missing"]:
        print(f"  MISSING   {item}")
    print("dry run    : NOTHING WAS DELETED. This is a preview; the purge "
          "requires --execute with the digest above, a restore proof and an "
          "armed-record artefact.")
    return 0


def _execute(plan: dict, args, target: dict) -> int:
    """The destructive arm. ⛔ NEVER RUN IN THE LANE THAT WROTE IT (#10017).

    Order is the landed one, and the JOURNAL is a precondition checked BEFORE
    the first delete:

        0. the SDK must be addressing the SAME graph the census read, and it
           must have a journal — an unjournalable target is refused up front,
           never discovered after a session has already been deleted (a
           rebuild would resurrect those turns while this run reported a
           failure);
        1. delete the exact planned ids per session and VERIFY absence;
        2. journal every VERIFIED-ABSENT id — including the ones a partially
           failing session did delete — through the SDK's own
           `_journal_turn_removal` (#10076), the ONE removal record, and
           refuse if the journal cannot carry the removal;
        3. fail unless the deleted total equals the plan's stated total.
    """
    from tortoise.projection import FalkorProjection
    from tortoise.sdk import TortoiseSDK

    if args.uri:
        proj = FalkorProjection.from_uri(args.uri, graph_name=args.graph_name)
        # The SDK has no `db_uri` kwarg (it resolves its target from
        # TORTOISE_DB_URI): set the variable from --uri so the SDK and the
        # census's projection cannot address two different stores.
        os.environ["TORTOISE_DB_URI"] = args.uri
        sdk_db_path = None
    else:
        proj = FalkorProjection(args.db_path, graph_name=args.graph_name
                                or "tortoise")
        sdk_db_path = args.db_path

    sdk = TortoiseSDK(db_path=sdk_db_path,
                      event_log_path=args.event_log_path)

    # ⛔ BEFORE THE FIRST DELETE. `_journal_turn_removal` returns `None` per id
    # when no journal is configured, and a refusal discovered after the delete
    # is a purge whose removals nothing records — a rebuild then resurrects
    # every one of them, silently, while the graph looks drained (#3316).
    if sdk._get_event_log() is None:
        sys.stderr.write(
            f"purge_legacy_turns: REFUSED {REFUSE_NO_JOURNAL}: no journal is "
            "configured for the target, so a removal could not be recorded "
            "and a later rebuild would resurrect it. Pass --event-log-path "
            "naming the journal the target's rebuild reads. Nothing was "
            "deleted.\n")
        return 2
    sdk_proj = sdk._get_proj()
    sdk_graph = getattr(sdk_proj, "graph_name", None)
    if args.graph_name and sdk_graph != args.graph_name:
        sys.stderr.write(
            f"purge_legacy_turns: REFUSED {REFUSE_TARGET_DRIFT}: the SDK "
            f"addresses graph {sdk_graph!r} but the census read "
            f"{args.graph_name!r}. Journaling into one graph's journal while "
            f"deleting from another would record removals a rebuild of the "
            f"target never sees. Nothing was deleted.\n")
        return 2

    deleted_total = 0
    journaled_total = 0
    unconfirmed: list[str] = []
    for entry in plan["purge"]["sessions"]:
        sid, ids = entry["session"], list(entry["ids"])
        if not ids:
            continue
        # EXACT-SET DELETE: the id list is the approved one, and the statement
        # is scoped both by the session wiring and by `t.id IN $ids`, so a turn
        # that appeared after the census is not swept in.
        proj.g.query(
            "MATCH (s:Session {id:$sid})-[:CONTAINS]->(t:Point) "
            "WHERE t.is_episodic = true AND t.id IN $ids "
            "DETACH DELETE t",
            params={"sid": sid, "ids": ids},
        )
        remaining = proj.g.query(
            "MATCH (t:Point) WHERE t.id IN $ids RETURN t.id",
            params={"ids": ids},
        ).result_set
        survivors = {str(row[0]) for row in remaining or ()}
        # A partially failing session still deleted SOME ids: those are journaled
        # (they are gone from the graph, so a rebuild must not resurrect them),
        # and the survivors are named and never journaled — journaling an id
        # whose node still stands claims a removal that did not happen.
        absent = [tid for tid in ids if tid not in survivors]
        if absent:
            outcomes = sdk._journal_turn_removal(absent)
            failed = [tid for tid, ok in outcomes.items() if ok is not True]
            if failed:
                sys.stderr.write(
                    f"purge_legacy_turns: REFUSED {REFUSE_JOURNAL_UNCONFIRMED}: "
                    f"{len(failed)} removal(s) in session {sid} were deleted "
                    f"from the graph but NOT journaled (a rebuild would "
                    f"resurrect them). {deleted_total} node(s) were deleted "
                    f"and journaled BEFORE this failure, and this run stops "
                    f"here: {', '.join(failed[:10])}\n")
                return 2
            deleted_total += len(absent)
            journaled_total += len(outcomes)
        unconfirmed.extend(sorted(survivors))
        if survivors:
            sys.stderr.write(
                f"purge_legacy_turns: session {sid}: {len(survivors)} planned "
                f"node(s) SURVIVED the delete (and were NOT journaled): "
                f"{', '.join(sorted(survivors)[:10])}\n")

    print(f"purged     : {deleted_total} turn node(s) from "
          f"{plan['purge']['session_count']} session(s), "
          f"{journaled_total} journaled removal(s)")
    if deleted_total != int(plan["purge"]["turn_node_total"]):
        # The plan's headline total and what was actually purged must agree: a
        # shortfall is a partial sweep, and reporting it as success is how a
        # "done" claim outlives the work.
        sys.stderr.write(
            f"purge_legacy_turns: REFUSED {REFUSE_EXACT_SET_MISMATCH}: the "
            f"plan states {plan['purge']['turn_node_total']} node(s) but "
            f"{deleted_total} were purged.\n")
        return 2
    if unconfirmed:
        sys.stderr.write(
            f"purge_legacy_turns: REFUSED {REFUSE_EXACT_SET_MISMATCH}: "
            f"{len(unconfirmed)} planned node(s) were NOT gone after the "
            f"delete: {', '.join(unconfirmed[:10])}\n")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
