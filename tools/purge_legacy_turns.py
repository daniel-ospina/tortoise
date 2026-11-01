#!/usr/bin/env python3
"""#10017 step 9: the PURGE of pre-#10000 turn nodes — preview by default, and
the preview is the token.

This tool is the destructive half of #10000, and it is built so that its
destructive arm cannot run by accident. **A PREVIEW is the DEFAULT — there is
no `--dry-run` flag: the ABSENCE of `--execute` is the preview**; it
reads the graph and the record through the census that already gates this
issue (`tools/turn_parity_census.py`, #10029 — imported, never re-implemented),
classifies every session that holds turn nodes, and writes an APPROVABLE PLAN
artefact: the exact session list, the exact node count per session, the total,
the excluded sessions with their class and reason, a canonical digest of the
whole set, the journal path the target's rebuild reads, and the recovery path
(or `NEEDS-RECOVERY-PATH(<what is missing>)`).

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
    # 1. PREVIEW (the default — no flag needed, nothing is written to a store).
    #    --event-log-path is REQUIRED here and recorded in the plan: the journal
    #    is named by the artefact a human approves, not chosen at delete time.
    python tools/purge_legacy_turns.py \
        --uri '<target FalkorDB URI>' --graph-name <graph> \
        --record-dsn "$TORTOISE_RECORD_DSN" \
        --org-id <org> --graph-id <graph> \
        --health-url https://<the deployment's own host>/health \
        --event-log-path '<the JSONL journal a rebuild of THIS graph replays>' \
        --target-note 'prod: tortoise-y4mjjq, approved by <owner> <date>' \
        --plan-out /tmp/purge-plan.json [--json /tmp/census.json]

    # 2. APPROVE — a human reads the plan and its digest, and records an
    #    APPROVAL out of band (an approval record kept somewhere the plan file
    #    is not: `{"plan_digest":…, "approved_by":…, "recorded_at":…}`).
    #    The plan file alone is NEVER sufficient: both sides of the
    #    --confirm-plan-digest comparison were read out of it otherwise.
    # 3. EXECUTE (refuses unless every precondition holds, including a fresh
    #    restore proof, fresh armed evidence, the out-of-band approval, and the
    #    plan's digest):
    python tools/purge_legacy_turns.py --execute --plan /tmp/purge-plan.json \
        --confirm-plan-digest <the digest the plan states> \
        --approval-record /tmp/approval.json \
        --restore-proof /tmp/restore-proof.json \
        --health-url https://<the deployment's own host>/health \
        --event-log-path '<the same journal the plan names>' \
        ... (the same target flags, re-derived live)

EVIDENCE HAS AN AGE AND AN ADDRESS
=================================
`--health-url`/`--health-json` and `--restore-proof` are both EVIDENCE, so each
must say (a) WHEN it was read and (b) WHICH deployment it describes, or it is
not evidence for this run:

  * `/health` — TWO ways in, and they are not equally strong:

    - `--health-url` — THIS RUN reads the deployment's own `/health` and
      stamps `read_at` with its own clock. Preferred for `--execute`: the
      arming fact and its age are then a measurement, not a claim.
    - `--health-json` — a captured body. It carries the deployment's own
      `fetched_at` (#9548 added it for exactly this reader); a capture that
      states no instant at all (a body saved before that field existed) is
      refused `REFUSE_ARMED_EVIDENCE_STALE` — re-read the endpoint, or pass
      `--health-url` and let this run read it.

    Either way `record.armed` must be the boolean `true`, and the body must
    state `record.dsn` — the fingerprint of the record DSN the deployment is
    armed with — which must be the fingerprint of the DSN this run reads and
    deletes around (`REFUSE_TARGET_DRIFT`). That fingerprint is the ADDRESS a
    real `/health` actually carries: the endpoint does not know the tenant (a
    multi-tenant deployment serves several) and publishes no other identity, so
    a guard that demanded `org_id`/`graph_id` from it refused every real body
    and could only ever be satisfied by a hand-written file (#10483 review
    round 4, P1). Those two fields are still honoured when a collector adds
    them — and must match the live target when present.

    The read must be at most `_HEALTH_MAX_AGE_SECONDS` old
    (`REFUSE_ARMED_EVIDENCE_STALE`; a body from whenever the record was last
    armed would otherwise satisfy precondition 1 forever).
  * restore proof — `scratch_session` (one this plan purges),
    `served_from_record: true`, `org_id`, `graph_id`, `graph`/`uri_host`, and
    `recorded_at`, at most `_RESTORE_PROOF_MAX_AGE_SECONDS` old, on the same
    tenant and graph as the live target (`REFUSE_RESTORE_PROOF_STALE`).

The two bounds are not measured safe intervals — they exist so that "when was
this read, and about what?" has an answer. Raising one is a one-line diff
someone reviews; omitting the check is what let a foreign file pass.

A plan generated WITHOUT `--health-url`/`--health-json` records no armed
evidence, and then cannot authorise an execute even if a fresh `/health` passes
today
(`REFUSE_NO_ARMED_EVIDENCE`): what the human approved is the artefact set, and
an unarmed record at approval time is not repaired by arming it afterwards.

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
REFUSE_NO_ARMED_EVIDENCE = "no-armed-evidence"
REFUSE_NO_RESTORE_PROOF = "no-restore-proof"
REFUSE_RESTORE_PROOF_STALE = "restore-proof-stale"
REFUSE_CONFIRMATION_MISMATCH = "confirmation-mismatch"
REFUSE_EXACT_SET_MISMATCH = "exact-set-mismatch"
REFUSE_RECORD_BEHIND = "record-behind"
REFUSE_MISMATCH = "mismatch"
REFUSE_NOT_IN_PLAN = "session-not-in-plan"
REFUSE_JOURNAL_UNCONFIRMED = "journal-unconfirmed"
REFUSE_NO_JOURNAL = "no-journal"
REFUSE_JOURNAL_UNUSABLE = "journal-unusable"
REFUSE_CENSUS_NOT_CLEAN = "census-not-clean"
REFUSE_CENSUS_UNREADABLE = "census-unreadable"
REFUSE_TARGET_DRIFT = "target-drift"
REFUSE_ARMED_EVIDENCE_STALE = "armed-evidence-stale"
REFUSE_NO_APPROVAL = "no-approval"
REFUSE_PARTIAL_SWEEP = "partial-sweep"

#: How old a caller-supplied artefact may be before it stops describing the
#: deployment this run is about to mutate. See the module docstring, "EVIDENCE
#: HAS AN AGE AND AN ADDRESS".
_HEALTH_MAX_AGE_SECONDS = 24 * 3600
_RESTORE_PROOF_MAX_AGE_SECONDS = 7 * 24 * 3600

#: A timestamp a few minutes in the FUTURE is clock skew between the collector
#: and this host, not evidence from a later run; beyond this it is refused.
_CLOCK_SKEW_TOLERANCE_SECONDS = 300


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
               recovery_missing: list[str],
               journal_path: str | None = None) -> dict:
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
        # The journal a rebuild of THIS graph replays. Recorded in the plan so
        # the artefact a human approves NAMES it: `--event-log-path` at execute
        # time must be this path, or a typo would create a fresh file whose
        # removals the target's rebuild never reads (#10483 review, P1).
        "journal": {"path": journal_path},
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
                  health: dict | None, restore_proof: dict | None,
                  approval: dict | None, confirm_digest: str,
                  target_note: str, live_target: dict,
                  record_dsn: str = "") -> tuple[str, str]:
    """The `--execute` gate. Pure: no I/O, no deletion, one verdict.

    Returns `(refusal_code, message)` where an EMPTY code means "every
    precondition holds". Every branch is a named refusal reachable in
    isolation, which is what `tests/test_10017_purge_guard.py` asserts — the
    three the brief mandates (record not armed, a target session
    RECORD-BEHIND, a live census one node off the approved set) plus the ones
    the issue adds (no restore proof, no matching confirmation token, no
    out-of-band approval).

    ⛔ `live_target` IS REQUIRED AND HAS NO DEFAULT. The target binding shipped
    with `live_target=None`, i.e. a check any caller could omit by forgetting a
    keyword — a fail-open default in a guard whose doctrine is fail-closed
    (#10483 review round 2 addendum, P2 on finding #1). A guard that is opt-out
    by omission is not a guard, so the parameter has no default and an empty
    target is itself a `REFUSE_TARGET_DRIFT`.
    """
    if not target_note or not str(target_note).strip():
        return (REFUSE_NO_TARGET_NOTE,
                "no --target-note: a purge must name the deployment it is "
                "aimed at, so the plan and the run cannot drift apart")
    if not plan:
        return (REFUSE_NO_PLAN,
                "no --plan: --execute acts on an APPROVED plan artefact "
                "only. Run without --execute to produce one")
    # ⛔ SHAPE BEFORE ANY DEREFERENCE. A plan that parses but has an entry with
    # no `ids` used to reach `plan_digest` and escape as a `KeyError` traceback
    # — indistinguishable from a crash, and the documented refusal never
    # printed (#10483 review, P1).
    plan_problem = _plan_problem(plan)
    if plan_problem:
        return (REFUSE_PLAN_UNREADABLE,
                f"the plan artefact cannot be read as a plan: "
                f"{plan_problem}. Nothing was deleted — regenerate the plan "
                f"and re-approve")
    if not isinstance(live_target, dict) or not live_target:
        return (REFUSE_TARGET_DRIFT,
                "no live target was supplied, so the plan cannot be compared "
                "to the deployment this run would mutate — an unverified "
                "target is not the approved target")
    armed_code, armed_problem = _armed_evidence_problem(
        health, live_target, record_dsn=record_dsn)
    if armed_code:
        return (armed_code, armed_problem)
    if not restore_proof:
        return (REFUSE_NO_RESTORE_PROOF,
                "no --restore-proof: #10017 precondition 3 requires one "
                "sampled session deleted from a SCRATCH graph and re-served "
                "from the record before any real pass. A green census opens "
                "the gate; it does not prove the way back")

    planned_sessions = [e["session"] for e in
                        (plan.get("purge", {}) or {}).get("sessions") or []]
    proof_problem = _restore_proof_problem(restore_proof, planned_sessions,
                                           live_target)
    if proof_problem:
        return (REFUSE_RESTORE_PROOF_STALE,
                f"the restore proof does not establish precondition 3: "
                f"{proof_problem}")

    # ⛔ THE PLAN IS BOUND TO A DEPLOYMENT, NOT ONLY TO A SET. The digest covers
    # (session, node) pairs, and those are not unique to a host: a restored
    # snapshot, a staging clone or a re-provisioned graph re-derives a
    # BYTE-IDENTICAL digest. Executing an approval given for one of those
    # against another is the same set applied to the wrong store, so the target
    # block is compared field by field (#10483 review round 2, P1-A) — and the
    # operator's own --target-note is compared too, because a plan approved for
    # "prod A" must not execute under "prod B"'s note (#10483 review, P2).
    approved_target = plan.get("target") or {}
    drift = [k for k in ("graph", "uri_host", "db_path", "org_id",
                         "graph_id")
             if approved_target.get(k) != live_target.get(k)]
    if (plan.get("target_note") or "") != str(target_note):
        drift.append("target_note")
    if drift:
        return (REFUSE_TARGET_DRIFT,
                "the plan was approved for a DIFFERENT deployment: "
                + ", ".join(
                    f"{k} (plan {(plan.get('target_note') if k == 'target_note' else approved_target.get(k))!r} "
                    f"vs live {(target_note if k == 'target_note' else live_target.get(k))!r})"
                    for k in drift))

    # ⛔ THE ARMED EVIDENCE THE HUMAN SAW IS PART OF THE APPROVAL. A plan
    # generated without one was approved over an unarmed record, so it cannot
    # authorise a delete even if a fresher /health passes today.
    approved_armed = plan.get("armed_evidence")
    if not isinstance(approved_armed, dict) or not approved_armed:
        return (REFUSE_NO_ARMED_EVIDENCE,
                "the plan records no armed evidence — it was generated "
                "without a /health artefact, so the record was not proven "
                "ARMED when it was approved. Regenerate it with "
                "--health-url and re-approve")
    plan_armed_address = _evidence_address(approved_armed)
    if not plan_armed_address.get("record_dsn_fingerprint"):
        return (REFUSE_NO_ARMED_EVIDENCE,
                "the plan's approved armed evidence names no record "
                "fingerprint (record.dsn), so it cannot be tied to the record "
                "this run deletes around — regenerate the plan against the "
                "deployment's own /health and re-approve")
    live_armed_address = _evidence_address(health)
    armed_drift = [k for k in ("record_dsn_fingerprint", "org_id", "graph_id")
                   if k in plan_armed_address and k in live_armed_address
                   and plan_armed_address.get(k) != live_armed_address.get(k)]
    if armed_drift:
        return (REFUSE_TARGET_DRIFT,
                "the ARMED evidence in this run was read from a DIFFERENT "
                "deployment than the one the plan was approved against: "
                + ", ".join(f"{k} (plan {plan_armed_address.get(k)!r} vs "
                            f"live {live_armed_address.get(k)!r})"
                            for k in armed_drift))

    approved = plan.get("plan_digest") or ""
    # ⛔ THE ARTEFACT MUST BE SELF-CONSISTENT. The digest is what a human
    # approved; the `purge.sessions` list is what `_execute` deletes. They are
    # two fields of one JSON file, so an edited, merged or regenerated plan
    # could carry a set that never serialised to the digest it still displays —
    # and every check below compares the LIVE set to that digest, never the
    # list to it. Re-derive it here, first, before anything trusts either.
    purge_block = plan.get("purge", {}) or {}
    if plan_digest(purge_block.get("sessions") or []) != approved:
        return (REFUSE_NOT_IN_PLAN,
                "the plan's own purge.sessions do not serialise to the "
                "digest it states — the artefact has been edited since it "
                "was approved; regenerate it and re-approve")
    # …and the rest of `purge` that the run DEREFERENCES must agree with the
    # ids too. A plan whose `turn_node_total` was edited down would otherwise
    # delete the whole set and refuse afterwards — acting first, refusing
    # second, which is the shape P0-1 was.
    entries = purge_block.get("sessions") or []
    id_total = sum(len(e.get("ids") or ()) for e in entries)
    inconsistent = []
    if int(purge_block.get("turn_node_total") or 0) != id_total:
        inconsistent.append(f"turn_node_total {purge_block.get('turn_node_total')}"
                            f" != {id_total} id(s)")
    if int(purge_block.get("session_count") or 0) != len(entries):
        inconsistent.append(f"session_count {purge_block.get('session_count')}"
                            f" != {len(entries)} session(s)")
    for entry in entries:
        if int(entry.get("graph_turn_nodes") or 0) != len(entry.get("ids") or ()):
            inconsistent.append(f"session {entry.get('session')}: "
                                f"graph_turn_nodes != id count")
    if inconsistent:
        return (REFUSE_NOT_IN_PLAN,
                "the plan's counts do not agree with its own id list "
                f"({'; '.join(inconsistent)}) — a run would act on one and "
                "report the other. Regenerate the plan and re-approve")
    if not confirm_digest or confirm_digest != approved:
        return (REFUSE_CONFIRMATION_MISMATCH,
                f"--confirm-plan-digest does not match the approved plan "
                f"(plan {approved[:12]}…, given "
                f"{(confirm_digest or '<none>')[:12]}…)")

    # ⛔ THE APPROVAL IS NOT A FIELD OF THE PLAN. `--confirm-plan-digest` and
    # `plan["plan_digest"]` are both read from the artefact under review, so
    # together they prove the operator OPENED the file, not that a human
    # approved THAT SET: a plan whose `purge.sessions` and `plan_digest` were
    # both rewritten passes both (#10483 review, P2). The approval is therefore
    # a separate artefact, kept somewhere the plan file is not.
    approval_problem = _approval_problem(approval, approved)
    if approval_problem:
        code = (REFUSE_NO_APPROVAL if not approval
                else REFUSE_CONFIRMATION_MISMATCH)
        return (code, approval_problem)

    if live_report is None:
        return (REFUSE_EXACT_SET_MISMATCH,
                "the live census produced no report, so the approved set "
                "cannot be re-derived — an unverifiable set is not an "
                "approved set")
    report_problem = _report_problem(live_report)
    if report_problem:
        return (REFUSE_CENSUS_UNREADABLE,
                f"the live census report cannot be read as a census: "
                f"{report_problem}. Nothing was deleted")

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


def _restore_proof_problem(proof, planned_sessions: list[str],
                          live_target: dict | None = None) -> str:
    """Why this restore-proof artefact is not evidence — or `""`.

    `--restore-proof` used to be truthiness-checked, which made an arbitrary
    `{"garbage": 1}` satisfy #10017's precondition 3. The artefact must name
    the session it restored (and that session must be in the plan), state that
    the transcript was re-served FROM THE RECORD, name the tenant/graph it was
    done on, and say WHEN — the refusal code is `restore-proof-stale`, so a
    `recorded_at` that was never read made the name a lie (#10483, P1).
    """
    if not isinstance(proof, dict):
        return "the artefact is not a JSON object"
    missing = [k for k in ("scratch_session", "served_from_record",
                           "recorded_at", "org_id", "graph_id")
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
    if live_target is not None:
        # A proof produced on tenant A says nothing about tenant B's record, so
        # the ADDRESS is part of the evidence (#10483, P1).
        drift = [k for k in ("org_id", "graph_id")
                 if proof.get(k) != live_target.get(k)]
        if not (proof.get("graph") or proof.get("uri_host")):
            return "it names neither the graph nor the host it was done on"
        if (proof.get("graph") is not None
                and proof.get("graph") != live_target.get("graph")):
            drift.append("graph")
        if (proof.get("uri_host") is not None
                and proof.get("uri_host") != live_target.get("uri_host")):
            drift.append("uri_host")
        if drift:
            return ("it describes a DIFFERENT deployment than the plan "
                    "targets: " + ", ".join(
                        f"{k} (proof {proof.get(k)!r} vs target "
                        f"{live_target.get(k)!r})" for k in drift))
    age = _age_seconds(proof.get("recorded_at"))
    if age is None:
        return ("recorded_at is not an ISO-8601 timestamp, so the proof's age "
                "is not established")
    if age < -_CLOCK_SKEW_TOLERANCE_SECONDS:
        return (f"recorded_at is {abs(age):.0f}s in the FUTURE — it cannot be "
                "a record of a restore that happened")
    if age > _RESTORE_PROOF_MAX_AGE_SECONDS:
        return (f"it was recorded {age / 86400:.1f} day(s) ago, beyond the "
                f"{_RESTORE_PROOF_MAX_AGE_SECONDS / 86400:.0f}-day bound — a "
                "restore path is re-exercised before a pass, not once ever")
    return ""


def _age_seconds(value) -> float | None:
    """How old an ISO-8601 timestamp is, or `None` when it is not one."""
    if not isinstance(value, str) or not value.strip():
        return None
    from datetime import UTC, datetime
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        stamp = datetime.fromisoformat(text)
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    return (datetime.now(UTC) - stamp).total_seconds()


def _evidence_address(artifact) -> dict:
    """The deployment a `/health` body says it is, as far as it CAN say.

    `record.dsn` is the handle the endpoint really publishes: the 8-hex
    fingerprint of the record DSN the process is armed with (#9548 /
    `record_writer.dsn_fingerprint`), which names the record without disclosing
    it. `org_id`/`graph_id` are read too, but OPTIONALLY — a multi-tenant
    deployment does not know the tenant, so demanding those two fields refused
    every real body and could only be satisfied by a hand-written file (#10483
    review round 4, P1).
    """
    if not isinstance(artifact, dict):
        return {}
    block = (artifact.get("record")
             if isinstance(artifact.get("record"), dict) else artifact)
    address = {"record_dsn_fingerprint": block.get("dsn")}
    for key in ("org_id", "graph_id"):
        if block.get(key):
            address[key] = block.get(key)
    return address


def _read_instant(artifact) -> object:
    """WHEN the artefact was read: this run's own stamp first, then its claim.

    `read_at` is written by this tool when it fetches `/health` itself
    (`--health-url`), so it cannot predate the run. `fetched_at` is the
    endpoint's own serve time (added in #9548 for exactly this reader) and is
    what a captured body carries; `recorded_at` is accepted for a collector
    that says it that way.
    """
    if not isinstance(artifact, dict):
        return None
    blocks = [artifact]
    for key in ("record", "target"):
        if isinstance(artifact.get(key), dict):
            blocks.append(artifact[key])
    for name in ("read_at", "fetched_at", "recorded_at"):
        for block in blocks:
            if block.get(name):
                return block.get(name)
    return None


def _record_fingerprint(dsn: str) -> str | None:
    """The seam's OWN `dsn_fingerprint`, or `None` when it cannot be imported.

    Imported, never re-derived: its docstring says the copy that drifts is the
    one that prints a DSN.
    """
    if not dsn:
        return None
    try:
        from tortoise.record_writer import dsn_fingerprint
    except Exception:  # pragma: no cover - the record seam always imports
        return None
    try:
        return dsn_fingerprint(dsn)
    except Exception:  # pragma: no cover - a gate never crashes on a string
        return None


def _armed_evidence_problem(health, live_target: dict, *,
                           record_dsn: str = "") -> tuple[str, str]:
    """The ARMED precondition, with its AGE and its ADDRESS (#10483, P1).

    Returns `("", "")` when the evidence stands. Each defect has its own code:
    not armed at all, armed for some OTHER record, and armed so long ago that it
    no longer describes the record this run would delete from.

    ⛔ THE ADDRESS IS `record.dsn`, NOT THE TENANT. `record.dsn` is the
    fingerprint of the record DSN the deployment is armed with; it must be the
    fingerprint of the DSN this run reads and deletes around. That binds the
    arming evidence to the RECORD, which `plan["target"]` cannot: two
    deployments can target one graph, and one record can serve several graphs.
    The previous requirement (`org_id`/`graph_id` INSIDE the body) was a
    category error — `/health` serves a multi-tenant process and carries
    neither — so it refused every real body and left hand-authoring as the only
    passing input (#10483 review round 4, P1).
    """
    if not isinstance(health, dict):
        return (REFUSE_RECORD_NOT_ARMED,
                "no /health evidence was supplied (--health-url or "
                "--health-json), so the record is not proven ARMED for this "
                "deployment (#9548 / PR #9989). An unarmed record holds no "
                "turns, so the delete would have no surviving copy")
    if not _health_armed(health):
        return (REFUSE_RECORD_NOT_ARMED,
                "the /health artefact does not report record.armed as the "
                "boolean true (#9548 / PR #9989). An unarmed record holds no "
                "turns, so the delete would have no surviving copy")
    address = _evidence_address(health)
    fingerprint = address.get("record_dsn_fingerprint")
    if not isinstance(fingerprint, str) or not fingerprint.strip():
        return (REFUSE_RECORD_NOT_ARMED,
                "the /health artefact is armed but does not state record.dsn "
                "— the fingerprint of the record DSN it is armed with — so "
                "nothing ties it to the record this run reads and deletes "
                "around. Re-read /health for this target")
    expected = _record_fingerprint(record_dsn)
    if expected is not None and fingerprint.strip() != expected:
        return (REFUSE_TARGET_DRIFT,
                "the ARMED evidence describes a DIFFERENT record: /health is "
                f"armed with dsn={fingerprint.strip()}, and this run reads "
                f"dsn={expected}. Passing the second would delete around a "
                "record nobody proved armed — pass the DSN the deployment is "
                "armed with")
    drift = [k for k in ("org_id", "graph_id")
             if address.get(k) and address.get(k) != live_target.get(k)]
    if drift:
        return (REFUSE_TARGET_DRIFT,
                "the ARMED evidence was read from a DIFFERENT deployment: "
                + ", ".join(f"{k} (health {address.get(k)!r} vs live "
                            f"{live_target.get(k)!r})" for k in drift))
    instant = _read_instant(health)
    if instant is None:
        return (REFUSE_ARMED_EVIDENCE_STALE,
                "the /health artefact states no read instant (fetched_at or "
                "recorded_at), so when it was read is not established and it "
                "would describe any run for ever. Re-read /health (it stamps "
                "fetched_at), or pass --health-url so this run reads it")
    age = _age_seconds(instant)
    if age is None:
        return (REFUSE_ARMED_EVIDENCE_STALE,
                f"the /health artefact's read instant ({instant!r}) is not an "
                "ISO-8601 timestamp, so its age is not established")
    if age < -_CLOCK_SKEW_TOLERANCE_SECONDS:
        return (REFUSE_ARMED_EVIDENCE_STALE,
                f"the /health artefact is dated {abs(age):.0f}s in the FUTURE "
                "— it cannot be a record of a read that happened")
    if age > _HEALTH_MAX_AGE_SECONDS:
        return (REFUSE_ARMED_EVIDENCE_STALE,
                f"the ARMED evidence was read {age / 3600:.1f}h ago, beyond "
                f"the {_HEALTH_MAX_AGE_SECONDS / 3600:.0f}h bound — it "
                "describes the record as it was then, not as it is before "
                "this delete. Re-read /health for this target")
    return ("", "")


def _approval_problem(approval, approved_digest: str) -> str:
    """Why the out-of-band approval does not authorise THIS set — or `""`.

    The approval is a separate artefact (kept somewhere the plan file is not),
    because `--confirm-plan-digest` compared against `plan["plan_digest"]` reads
    BOTH sides out of the artefact under review: a plan whose `purge.sessions`
    and digest were rewritten together passes that check (#10483, P2).
    """
    if not isinstance(approval, dict) or not approval:
        return ("no --approval-record: the approval must be recorded in an "
                "artefact kept OUTSIDE the plan file (an approval comment on "
                "#10017, saved separately). The plan's own digest field "
                "cannot approve itself")
    for key in ("plan_digest", "approved_by", "recorded_at"):
        if not approval.get(key):
            return f"the approval record does not state {key}"
    digest = str(approval.get("plan_digest")).strip()
    if digest != approved_digest:
        return (f"the approval record authorises {digest[:12]}…, not this "
                f"plan ({approved_digest[:12]}…) — the set the human approved "
                "is not the set this artefact would delete")
    if _age_seconds(approval.get("recorded_at")) is None:
        return ("the approval record's recorded_at is not an ISO-8601 "
                "timestamp, so when it was given is not established")
    return ""


def _report_problem(report) -> str:
    """Why this is not a census report — or `""`.

    `--census-json` accepted any JSON, and an EMPTY one collapsed "I never held
    a census" into "empty, so nothing to purge": `{}` exited 0 and printed a
    signable plan with the empty-set digest (#3175's defect, in the artefact a
    human approves — #10483, P1). A missing side is not an empty side.
    """
    if not isinstance(report, dict):
        return f"it is a {type(report).__name__}, not a JSON object"
    for key in ("population", "parity", "dry_run"):
        if not isinstance(report.get(key), dict):
            return f"it carries no `{key}` block"
    for key in ("clean_sessions", "failed_sessions"):
        if not isinstance(report["parity"].get(key), list):
            return f"parity.{key} is missing or is not a list"
    if not isinstance(report["dry_run"].get("would_delete_ids"), list):
        return ("dry_run.would_delete_ids is missing — the purge set has ONE "
                "derivation, and a report without it describes no set")
    if report.get("verdict") in (None, ""):
        return ("it carries no census VERDICT — a report that never ran a "
                "census is not a clean one")
    if not isinstance(report.get("refused_for"), list):
        return "refused_for is missing or is not a list"
    return ""


def _plan_problem(plan) -> str:
    """Why this is not a plan artefact — or `""`.

    Every field the run dereferences is checked BEFORE the first dereference:
    a plan entry with no `ids` used to reach `plan_digest` and escape `main` as
    a `KeyError` traceback (#10483, P1).
    """
    if not isinstance(plan, dict):
        return f"it is a {type(plan).__name__}, not a JSON object"
    digest = plan.get("plan_digest")
    if not isinstance(digest, str) or len(digest) != 64:
        return "it carries no 64-hex plan_digest"
    purge_block = plan.get("purge")
    if not isinstance(purge_block, dict):
        return "it has no `purge` block"
    entries = purge_block.get("sessions")
    if not isinstance(entries, list):
        return "purge.sessions is missing or is not a list"
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            return f"purge.sessions[{index}] is not an object"
        session = entry.get("session")
        if not isinstance(session, str) or not session:
            return f"purge.sessions[{index}] has no session id"
        ids = entry.get("ids")
        if not isinstance(ids, list) or not all(isinstance(t, str) for t in ids):
            return (f"purge.sessions[{index}] ({session}) has no `ids` list of "
                    "strings — the digest and the delete both dereference it")
    return ""


class _HealthUnreadable(Exception):
    """`--health-url` could not be read — exit 3, an absence is NOT established."""


def _fetch_health(url: str, *, timeout: float = 10.0) -> dict:
    """Read the deployment's own `/health` AT RUN TIME and stamp the read.

    This is the strong form of precondition (a): the arming fact comes from the
    deployment, and the age is this run's own clock (`read_at`), not something
    the operator wrote. A captured body (`--health-json`) can only carry the
    capturer's word for both — which is why a file is the fallback and this is
    the documented path for `--execute`.

    Only `host[:port]` is published onto the evidence (`health_url`): a URL can
    carry credentials in its userinfo, and a plan artefact is shared — the same
    leak the target block already refuses (#10483 review round 2, P1-B).
    """
    import urllib.request
    from datetime import UTC, datetime
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise _HealthUnreadable(f"{type(exc).__name__}: {exc}") from exc
    if not isinstance(body, dict):
        raise _HealthUnreadable(
            f"the response was a {type(body).__name__}, not a JSON object")
    body["read_at"] = datetime.now(UTC).isoformat()
    body["health_url"] = _uri_host(url)
    return body


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
    """`host[:port]` — NEVER `netloc`.

    `urlparse(uri).netloc` carries the USERINFO, i.e. the FalkorDB password, and
    a plan artefact is a file meant to be shared and attached (#10483 review
    round 2, P1-B). The plan states the presence of a credential, never the
    credential.
    """
    if not uri:
        return None
    from urllib.parse import urlparse
    try:
        parts = urlparse(uri)
    except Exception:  # pragma: no cover - malformed URI
        return None
    if not parts.hostname:
        return None
    return f"{parts.hostname}:{parts.port}" if parts.port else parts.hostname


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
    if not getattr(args, "event_log_path", None):
        missing.append(
            "no --event-log-path: the journal the target's rebuild replays is "
            "not named in this plan, so `--execute` will refuse it")
    missing.append(
        "no recorded `python -m tortoise record-backup --drill` run against "
        "this target (the record's restore is implemented and documented, "
        "not proven here)")
    missing.append(
        "the reverse graph path (cache_tier 'record_only' → 'graph' + "
        "rebuild_from_record) has never been exercised for a turn tier")
    return missing


def _build_parser() -> _UsageParser:
    """The ONE argument surface.

    Extracted from `main` so a test can assert that every flag the module
    docstring names actually exists — the docstring documented a `--dry-run`
    flag the parser never had, and a reader following it got an argparse usage
    error (#10483 review, P2).
    """
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
                        help="a CAPTURED /health body (record.armed evidence) — "
                             "it must state when it was read (fetched_at / "
                             "recorded_at). Preferred for --execute: "
                             "--health-url, which this run reads live")
    parser.add_argument("--health-url", default=None,
                        help="read the deployment's own /health at RUN time "
                             "(strongest armed-record evidence: the fact and "
                             "its age are measured by this run)")
    parser.add_argument("--restore-proof", default=None,
                        help="a restore-proof artefact (issue precondition 3)")
    parser.add_argument("--approval-record", default=None,
                        help="the OUT-OF-BAND approval of the plan digest "
                             "(--execute): {plan_digest, approved_by, "
                             "recorded_at}. A plan cannot approve itself")
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
                             "(--execute). Required at PREVIEW time too: the "
                             "plan records it, so the artefact a human "
                             "approves names the journal the deletions land "
                             "in (an unjournaled removal is a resurrection, "
                             "#3316)")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
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
            try:
                report = _load_json(args.census_json)
            except Exception as exc:
                # A mistyped path is a CONFIG fault: exit 3 and a named
                # refusal, never an uncaught FileNotFoundError a caller reads
                # as a crash (the module documents exit 3 for this).
                sys.stderr.write(
                    f"purge_legacy_turns: REFUSED {REFUSE_CENSUS_UNREADABLE}: "
                    f"--census-json could not be read ({type(exc).__name__}: "
                    f"{exc}) — an absence is NOT established, so no plan can "
                    f"be written.\n")
                return 3
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

    # ⛔ A REPORT THAT NEVER HELD A CENSUS IS NOT AN EMPTY CENSUS. `{}` used to
    # produce exit 0 and a signable "0 node(s) would be purged" plan with the
    # empty-set digest — #3175's collapse, in the artefact a human signs
    # (#10483 review, P1).
    report_problem = _report_problem(report)
    if report_problem:
        sys.stderr.write(
            f"purge_legacy_turns: REFUSED {REFUSE_CENSUS_UNREADABLE}: the "
            f"census report cannot be read as a census: {report_problem}. "
            f"Nothing was deleted and no plan was written.\n")
        return 3

    if args.health_url and args.health_json:
        sys.stderr.write(
            "purge_legacy_turns: --health-url and --health-json are two "
            "sources for ONE fact — pass the live one (--health-url) or the "
            "captured one, not both. Nothing was read and nothing was "
            "deleted.\n")
        return 1
    try:
        health = (_fetch_health(args.health_url) if args.health_url
                  else _load_json(args.health_json))
        restore_proof = _load_json(args.restore_proof)
        approval = _load_json(args.approval_record)
    except _HealthUnreadable as exc:
        # A deployment whose own /health cannot be read has NOT shown its record
        # armed: exit 3, never "unarmed" (which would be a different, false
        # fact) and never a pass.
        sys.stderr.write(
            f"purge_legacy_turns: --health-url could not be read ({exc}) — "
            f"an absence is NOT established, so the record is not proven "
            f"ARMED. Nothing was deleted.\n")
        return 3
    except Exception as exc:
        # A mistyped path or malformed JSON is a CONFIG fault: exit 3 (a side
        # could not be read), never an uncaught traceback a caller reads as a
        # crash.
        sys.stderr.write(
            f"purge_legacy_turns: --health-json/--health-url/--restore-proof/"
            f"--approval-record could not be read ({type(exc).__name__}: "
            f"{exc}) — an absence is NOT established.\n")
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
            plan=plan, live_report=report, health=health,
            restore_proof=restore_proof, approval=approval,
            confirm_digest=args.confirm_plan_digest or "",
            target_note=args.target_note, live_target=target,
            record_dsn=record_dsn)
        if code:
            sys.stderr.write(f"purge_legacy_turns: REFUSED {code}: "
                             f"{message}\nNothing was deleted.\n")
            return 2
        return _execute(plan, args, target)

    try:
        plan = build_plan(report, target=target, health=health,
                          restore_proof=restore_proof,
                          target_note=args.target_note,
                          recovery_missing=_recovery_missing(args, restore_proof,
                                                             health),
                          journal_path=args.event_log_path)
    except (ValueError, KeyError) as exc:
        # A census report whose counts and id lists disagree cannot describe a
        # purge set. Named refusal, never an uncaught traceback (`main`'s own
        # contract: the guard's refusals are names).
        sys.stderr.write(f"purge_legacy_turns: REFUSED "
                         f"{REFUSE_CENSUS_NOT_CLEAN}: the census report cannot "
                         f"be turned into a purge set ({exc}).\n")
        return 3
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
    print(f"journal    : "
          f"{plan['journal']['path'] or '<NOT NAMED — --execute will refuse>'}")
    print("dry run    : NOTHING WAS DELETED. This is a preview; the purge "
          "requires --execute with the digest above, --restore-proof, an "
          "armed-record artefact (--health-url) and an out-of-band approval "
          "(--approval-record, kept outside the plan file).")
    return 0


def _execute(plan: dict, args, target: dict) -> int:
    """The destructive arm. ⛔ NEVER RUN IN THE LANE THAT WROTE IT (#10017).

    Order is the landed one, and both the JOURNAL and the per-session RECORD
    re-verify are preconditions checked BEFORE the delete they protect:

        0a. the journal the plan NAMES exists, is not empty, and is the one this
            run was given. A mistyped `--event-log-path` silently CREATES a
            fresh file (`EventLog.append`), so the removals would land in a log
            the target's rebuild never reads — `record != replay(journal)`
            forever, while the run reports success (#10483 review, P1);
        0b. the SDK must be addressing the SAME graph the census read, and it
            must have a journal — an unjournalable target is refused up front,
            never discovered after a session has already been deleted (a
            rebuild would resurrect those turns while this run failed);
        1. PER SESSION, immediately before its delete: the RECORD must still
            hold every planned id. The record is read once in `main`, and
            between that census and session k's delete sit a paged graph read,
            a tenant record read and k-1 deletes — a row lost in that window
            makes RECORD-BEHIND invisible and the graph copy the only copy
            (#10483 review, P0). Kubernetes states the same rule on the delete
            itself: `DeleteOptions.preconditions` — "must be fulfilled before a
            deletion is carried out" (`k8s.io/apimachinery/pkg/apis/meta/v1`);
        2. delete the exact planned ids per session and VERIFY absence;
        3. journal every VERIFIED-ABSENT id — including the ones a partially
            failing session did delete — through the SDK's own
            `_journal_turn_removal` (#10076), the ONE removal record, and
            refuse if the journal cannot carry the removal;
        4. fail unless the deleted total equals the plan's stated total, with
            its OWN code (`partial-sweep`): "the set drifted before the delete"
            and "only part of the set is gone after it" are different
            diagnoses and an operator cannot act on one shared name.

    ⛔ Residual, stated rather than left to be assumed: step 1 re-verifies that
    each planned KEY is still in the record; it does not re-compare the parity
    FIELDS. A row whose text changed after the live census would pass step 1,
    and the census's field comparison is the instrument that covers it.
    """
    from tortoise.projection import FalkorProjection
    from tortoise.sdk import TortoiseSDK

    # ⛔ 0a. THE JOURNAL IS NAMED BY THE APPROVED PLAN, AND MUST ALREADY EXIST.
    journal_path = args.event_log_path
    journal_block = plan.get("journal")
    planned_journal = (journal_block.get("path")
                       if isinstance(journal_block, dict) else None)
    if not journal_path:
        sys.stderr.write(
            f"purge_legacy_turns: REFUSED {REFUSE_NO_JOURNAL}: no journal is "
            "configured for the target, so a removal could not be recorded "
            "and a later rebuild would resurrect it. Pass --event-log-path "
            "naming the journal the target's rebuild reads. Nothing was "
            "deleted.\n")
        return 2
    if not planned_journal:
        sys.stderr.write(
            f"purge_legacy_turns: REFUSED {REFUSE_JOURNAL_UNUSABLE}: the plan "
            f"names no journal — it was generated without --event-log-path, so "
            f"nothing the human approved says which log these removals belong "
            f"in. Regenerate the plan with --event-log-path. Nothing was "
            f"deleted.\n")
        return 2
    if planned_journal != journal_path:
        sys.stderr.write(
            f"purge_legacy_turns: REFUSED {REFUSE_JOURNAL_UNUSABLE}: this run "
            f"would journal into {journal_path!r} but the approved plan names "
            f"{planned_journal!r}. A removal recorded anywhere but the target's "
            f"own journal is a removal its rebuild never sees. Nothing was "
            f"deleted.\n")
        return 2
    journal_file = Path(journal_path)
    if not journal_file.exists():
        sys.stderr.write(
            f"purge_legacy_turns: REFUSED {REFUSE_JOURNAL_UNUSABLE}: "
            f"{journal_path!r} DOES NOT EXIST — and an append CREATES it, so a "
            f"typo'd path would silently start a fresh journal whose ordinals "
            f"begin at 1 (the #10017 ruling: the copy and the delete append to "
            f"the graph's OWN journal). If the target really has no journal "
            f"yet, that is a setup fault to resolve first, not a file to "
            f"create here. Nothing was deleted.\n")
        return 2
    if journal_file.stat().st_size == 0:
        sys.stderr.write(
            f"purge_legacy_turns: REFUSED {REFUSE_JOURNAL_UNUSABLE}: "
            f"{journal_path!r} is EMPTY — a journal with no history is not the "
            f"target's replay source, and removals recorded into it are not "
            f"removals the target's rebuild can replay. Nothing was "
            f"deleted.\n")
        return 2

    # ⛔ 0a-bis. The per-session re-verify needs the record. Without a DSN the
    # TOCTOU gate cannot run, so the delete cannot start: a side that cannot be
    # read is not a side that agrees.
    record_dsn = args.record_dsn or os.environ.get("TORTOISE_RECORD_DSN", "")
    if not record_dsn:
        sys.stderr.write(
            "purge_legacy_turns: no record DSN — pass --record-dsn or set "
            "TORTOISE_RECORD_DSN. The record cannot be re-read before each "
            "delete, so parity is not established at the moment of the delete "
            "and nothing was deleted.\n")
        return 3

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
            f"purge_legacy_turns: REFUSED {REFUSE_NO_JOURNAL}: the SDK reports "
            "no journal for this target, so a removal could not be recorded "
            "and a later rebuild would resurrect it. Nothing was deleted.\n")
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

    # Read BEFORE the first delete: a plan whose `turn_node_total` is missing
    # must not be discovered after 20,000 removals (#10483 review, P1).
    planned_total = int(plan["purge"]["turn_node_total"])
    deleted_total = 0
    journaled_total = 0
    survivors_left: list[str] = []
    for entry in plan["purge"]["sessions"]:
        sid, ids = entry["session"], list(entry["ids"])
        if not ids:
            continue
        # ⛔ THE TOCTOU GATE: re-verify the RECORD for this session immediately
        # before its DELETE, through the census's own record leg (one
        # implementation of the read, `node_keys=` scoped to the planned ids).
        try:
            held = census_mod.read_record_turns(
                record_dsn, target.get("org_id"), target.get("graph_id"),
                node_keys=ids)["rows_by_key"]
        except census_mod.CensusError as exc:
            sys.stderr.write(
                f"purge_legacy_turns: the record could not be re-read before "
                f"session {sid}'s delete ({exc}) — nothing in this session was "
                f"deleted.\n")
            return 3
        lost = [tid for tid in ids if tid not in held]
        if lost:
            sys.stderr.write(
                f"purge_legacy_turns: REFUSED {REFUSE_RECORD_BEHIND}: session "
                f"{sid} is RECORD-BEHIND at delete time — {len(lost)} planned "
                f"id(s) have no record row NOW, so deleting the graph copies "
                f"destroys the only copy of those turns: "
                f"{', '.join(lost[:10])}. The census that authorised this run "
                f"is stale; backfill first. {deleted_total} node(s) were "
                f"deleted and journaled BEFORE this check, and this run stops "
                f"here.\n")
            return 2
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
        # The same RESULTSET_SIZE guard the census uses: a SILENTLY truncated
        # survivor list would report fewer survivors than exist, journal ids
        # that still stand, and print a false "purged". One aggregate row is
        # cap-immune, so the two must agree.
        counted = int(proj.g.query(
            "MATCH (t:Point) WHERE t.id IN $ids RETURN count(t)",
            params={"ids": ids},
        ).result_set[0][0])
        survivors = {str(row[0]) for row in remaining or ()}
        if len(survivors) != counted:
            sys.stderr.write(
                f"purge_legacy_turns: REFUSED {REFUSE_EXACT_SET_MISMATCH}: the "
                f"survivor read for session {sid} returned {len(survivors)} "
                f"row(s) but the store counts {counted} — the read was "
                f"TRUNCATED, so which ids are gone is not established. No "
                f"removal in this session was journaled.\n")
            return 2
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
        if survivors:
            survivors_left.extend(sorted(survivors))
            sys.stderr.write(
                f"purge_legacy_turns: session {sid}: {len(survivors)} planned "
                f"node(s) SURVIVED the delete (and were NOT journaled): "
                f"{', '.join(sorted(survivors)[:10])}\n")

    if deleted_total != planned_total:
        # ⛔ ITS OWN CODE. "The set drifted before the delete" (the
        # exact-set check) and "only part of the set is gone after it" are
        # different diagnoses; sharing one name was a guard whose refusals were
        # not distinguishable (#10483 review, P2). The deletes ALREADY HAPPENED
        # and are journaled, so the message says so instead of reading like a
        # refusal that prevented one.
        sys.stderr.write(
            f"purge_legacy_turns: REFUSED {REFUSE_PARTIAL_SWEEP}: the plan "
            f"states {planned_total} node(s) but {deleted_total} were purged. "
            f"The {deleted_total} removals above ALREADY HAPPENED and are "
            f"journaled, so re-running the same plan will not finish them — "
            f"regenerate the plan from a fresh census. Still standing: "
            f"{', '.join(survivors_left[:10]) or '<none reported>'}\n")
        return 2
    # Only now is "purged" true: printing it before the checks made the log read
    # as success with a refusal after it (#10483 review, P2).
    print(f"purged     : {deleted_total} turn node(s) from "
          f"{plan['purge']['session_count']} session(s), "
          f"{journaled_total} journaled removal(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
