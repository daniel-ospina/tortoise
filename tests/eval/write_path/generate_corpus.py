"""W2 planted-gold corpus generator (epic #2080, issue #2097, W2-a).

Hermetic, deterministic, idempotent fixture generator for the write-path
planted-gold corpus (plan DM-3/4/5, §4.3.1-4.3.3).  The corpus is a FIXTURE
generator, NOT a test suite: it produces the committed fixtures + sealed gold
+ ``_manifest.json`` + first-run-pending ``baselines/{main,m2}.json`` (one per
benchmark runner (#2098) consumes.

Authoring discipline (why the answer key is safe here):
* The transcript + answer-key authoring lives in THIS module (source code) —
  the only committed DATA artifacts are ``fixtures/`` (adapter-visible fields
  only: ``{session_id, harness, conversation}``), the sealed ``gold/`` dir
  extractor posture) that the W2-b first run publishes over.
* A ``gold`` key inside a committed fixture is a validation error; the corpus
  never carries the answer key outside ``gold/`` (sealed-key discipline).
* Every ``verbatim_anchor`` / distractor ``anchor`` / hazard ``quote`` is
  verified (normalized-ws, case-insensitive) to be a substring of the
  conversation turn named by ``planted_turn`` — fixture/gold drift cannot
  ship silently (the generator fails loudly, listing every violation).
* ``depth_bucket`` is DERIVED from ``planted_turn`` vs session length
  (research-grounded Cat-35 enumeration) and written into the sealed gold.
* Output is byte-deterministic (sorted keys, fixed indent, no timestamps) so
  re-running the generator reproduces the committed corpus exactly — the
  fix-wave protocol guarantee (re-run the SAME frozen corpus; corpus change
  is intentional and goes through corpus-bless review).

Run from the repo root:
    uv run python tests/eval/write_path/generate_corpus.py            # write (idempotent)
    uv run python tests/eval/write_path/generate_corpus.py --check    # drift check (exit 1 on drift)
    uv run python tests/eval/write_path/generate_corpus.py --validate # full committed-dir validation
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

if __package__ in (None, ""):  # direct script run — repo root on sys.path
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tests.eval.write_path import corpus, schema

# ── Corpus identity ─────────────────────────────────────────────────────────
# Benchmark-first decision: NO preset quality bar.  Targets are set FROM
# first-run data (directional context only: gbrain 88.1% / Mem0 42.9% /
# Supermemory 41.5% — see plan §2.1 / epic body).  The generator commits the
# first-run-pending baseline below; the W2-b runner publishes the first
# (expected-bad) number and blesses a real baseline with a justification.
CORPUS_SEED = 42  # deterministic ordering only; authoring is fixed content

# Floor targets (issue #2097 Indicators): the decomposition-set MINIMUM floors
# so E2E-2's percentage-based assertions have stable denominators.
MIN_SESSIONS = 4
MIN_PLANTED_SALIENT_UNITS = 60


# ── Fictional world (Peregrine Systems — clearly fictional) ─────────────────
# People: Maya Chen (platform), Diego Alvarez (quarry backend), Priya Sharma
# (data/registry), Tomás Rivera (SRE), Aisha Bello (PM), Jonah Kim (lumen
# frontend), Felix Ortega (design), Nora Haddad (security), Sam Okafor
# (customer engineer).  Products: quarry (ingest), lumen (dev portal),
# ember (alerting), aurora (dashboard).  Customers: Halcyon Retail,
# Bluepeak Logistics.  All names/orgs/systems are invented; any resemblance
# to real entities is coincidental.


# ── Authoring helpers ───────────────────────────────────────────────────────


def _t(role: str, content: str) -> dict:
    """One conversation turn (role must be 'user' | 'assistant')."""
    return {"role": role, "content": content}


def _unit(unit_id: str, kind: str, anchor: str, *, turn: int, notability: str = "high",
          accepts_rephrase_linked: bool = True, provenance_required: bool = True,
          ep_update_required: bool = True) -> dict:
    """One planted-unit authoring spec (short id; kind/notability enums).

    ``anchor`` MUST be a verbatim fragment of ``turns[turn - 1].content``
    (case-insensitive, whitespace-normalized) — the generator verifies it.
    ``accepts_rephrase_linked=False`` marks claim-critical anchors whose
    paraphrase would not preserve the claim — commonly date/numeric-critical,
    but also named-entity ownership, decisions, and root-cause statements
    (see the sealed gold's false-flag mix).  This is THIS corpus's
    claim-preservation carve-out on the
    REPHRASE-link concept (borrowed from docs/epistemic-layer-eval-spec.md
    §P5 dedup-without-deletion); survival is graded at the POINT level (the
    research-brief/plan write-path unit assumption).
    """
    return {
        "id": unit_id,
        "kind": kind,
        "verbatim_anchor": anchor,
        "notability": notability,
        "planted_turn": turn,
        "survival": {
            "accepts_rephrase_linked": accepts_rephrase_linked,
            "provenance_required": provenance_required,
            "ep_update_required": ep_update_required,
        },
    }


def _distractor(distractor_id: str, statement: str, anchor: str, *, turn: int) -> dict:
    """True-but-routine distractor (leakage probe): present in the session,
    unremarkable, must NOT surface as a salient point."""
    return {"id": distractor_id, "statement": statement, "anchor": anchor, "planted_turn": turn}


def _hazard(hazard_id: str, quote: str, source: str, *, turn: int) -> dict:
    """Attribution trap: ``quote`` grounds in the session at ``turn``; the trap
    is recalling/summarizing it as originating from anyone other than
    ``source`` (a human named in the session or the operating persona)."""
    return {"id": hazard_id, "quote": quote, "source": source, "planted_turn": turn}


def _operator(op_id: str, kind: str, from_anchor: str, from_turn: int,
              to_anchor: str, to_turn: int, *, relation_turn: int,
              reason: str, from_session: str | None = None,
              to_session: str | None = None) -> dict:
    """One planted-OPERATOR authoring spec (issue #2514, layer-2 gold).

    ``kind`` is the task-language operator name (schema.PLANTED_OPERATOR_KIND
    vocabulary: SUPERSEDE/NEGATE/MITIGATES/SUPPORTS); the mechanical grader
    maps it to the graph surface it asserts (grading.operator_edge_detail).
    ``from`` is the epistemically ACTIVE endpoint (newer decision / attacker /
    evidence / mitigation action), ``to`` the object it acts on.  ``reason``
    cites the ontology semantics (scoping note 2026-09-07-2514-operator-
    corpus.md table).  Anchors MUST be verbatim fragments of the named
    sessions' turns; ``relation_turn`` is the OWN-session turn that STATES the
    relation.  Cross-session endpoints (the SUPERSEDE planted wp07 -> wp06)
    carry an explicit ``*_session``; they are grounded at corpus level.
    """
    return {
        "id": op_id,
        "expected_kind": kind,
        "from": {"verbatim_anchor": from_anchor, "planted_turn": from_turn,
                 **({"session_id": from_session} if from_session else {})},
        "to": {"verbatim_anchor": to_anchor, "planted_turn": to_turn,
               **({"session_id": to_session} if to_session else {})},
        "relation_turn": relation_turn,
        "reason": reason,
    }


# Corpus floor for the planted-operator (layer-2) gold (issue #2514): every
# operator kind the issue names must be planted at least once across the
# corpus, and each planted edge must be unambiguous in its transcript.
MIN_PLANTED_OPERATOR_EDGES = 4
REQUIRED_OPERATOR_KINDS = {"SUPERSEDE", "NEGATE", "MITIGATES", "SUPPORTS"}


# ── Session authoring ───────────────────────────────────────────────────────
# Each spec: {session_id, harness, scenario, turns, units, distractors,
# hazards}.  planted_turn is 1-based over ``turns``; anchors are verified.
# Units are 1:1 with the gold's salient_units (every planted unit carries
# point-level survival semantics — the graded set).

WP01 = {
    "session_id": "wp01_quarry_debug",
    "harness": "codex",
    "scenario": "Diego debugs a stalled quarry (ingest pipeline) backfill replay for customer Halcyon Retail; root cause is a duplicate-processing race on the wire queue.",
    "turns": [
        _t("user", "Let us dig into the quarry backfill stall. We kicked off a full re-ingest of the Halcyon event archive last night and it has been stuck at 62 percent since 3am. Sorry for the late start, morning standup ran over. I want to understand why it stopped making progress and whether the partial batches are safe to keep."),
        _t("assistant", "Can you confirm whether the stall is in the wire stage or the hydrate stage? The progress counter is shared, so a stuck hydrate queue can look identical to a stuck wire queue."),
        _t("user", "It is the wire stage. We run four workers on the wire queue and they are all at full CPU while the queue depth barely moves."),
        _t("assistant", "Four workers with full CPU and a flat queue depth points at contention rather than starvation. What does the lock table look like for the batch lease keys?"),
        _t("user", "That is the weird part, there are no lease rows at all. Every worker is processing, but none of them appear to hold a lease."),
        _t("assistant", "No lease rows with active processing is a red flag. The batch ownership check was probably bypassed on the retry path. Do you have the commit that added the retry loop?"),
        _t("user", "Yes, it is the retry loop added in the batch runner. The staging cluster is running the latest release, so this is not a stale deploy artifact. I grepped the logs and every worker is retrying the same handful of batches in a loop."),
        _t("assistant", "Then you have a duplicate-processing race. Let me reproduce it against a staging queue with two workers and the same batch ids."),
        _t("user", "Reproduced. I can see it now, two workers grabbed the same batch twice from the ingest queue and each marked its own copy complete. Honestly I have been losing sleep over this backfill all week and it is a relief to finally see the root cause. The completion race is what made progress stall, every batch that finished got replayed by the second worker."),
        _t("assistant", "Confirmed root cause: no lease is held before processing, so nothing prevents two workers from claiming one batch. The fix is to make ownership explicit before the work starts."),
        _t("user", "Agreed. Let us make each batch claim a single-owner lease before processing and have the claim be the only thing that can complete it. I do not want a distributed lock service, a simple lease row with an expiry is enough."),
        _t("assistant", "A lease row keyed by batch id with a thirty-second expiry and a compare-and-swap update on completion gives you single-owner semantics without a lock service. The worker that does not own the lease skips and backs off."),
        _t("user", "What should the losing worker do when the lease is held? I would rather it retry the batch after a sixty second backoff than drop it. Losing the batch silently is how we lose events."),
        _t("assistant", "Sixty-second backoff on the non-owner path, then re-check the lease. If the lease expired and the owner never completed, the batch becomes claimable again, that gives you crash recovery for free."),
        _t("user", "Good. And we need this shipped before the October 15 freeze, so no scope creep. Is the lease change alone enough, or do we touch the completion path too?"),
        _t("assistant", "The completion path needs the owner check, but nothing else changes. One caveat: the wire queue processes batches of five thousand events each, and with the lease round-trip you should budget roughly a second per batch."),
        _t("user", "Five thousand per batch is fine. We are re-ingesting about twelve million events for Halcyon, so that is twenty-four hundred batches end to end. Grabbing coffee from the third floor machine before I read the logs."),
        _t("assistant", "At a second per batch with four workers that is about ten minutes of pure processing, well inside the maintenance window. Who owns the ingest queue service if we need a second pair of eyes on the lease table?"),
        _t("user", "Maya Chen owns the ingest queue service now. She took it over when the old platform group split. I will cc her on the PR."),
        _t("assistant", "Good. Let me also check the hydrate stage for a similar missing-lease pattern, since the code is shared between the two stages."),
        _t("user", "The hydrate stage was clean, I already audited it. One more thing, we should set the backfill cutoff at September 30 so we do not re-ingest events that already landed after the incident window."),
        _t("assistant", "Cutoff at September 30 means the re-ingest covers the outage window only. I will add the cutoff as a constant so the next backfill does not guess at it."),
        _t("user", "The DB maintenance window is Friday at 2am, and if the lease table migration is part of this change we should land it before then so the schema is stable during the window."),
        _t("assistant", "The migration is additive, a new lease table with an index on batch id. Landing it before Friday's maintenance window avoids any interaction with the vacuum job."),
        _t("user", "Sounds right. If the stall comes back I will page Tomas rather than waking the whole team, he knows the queue internals from the last incident."),
        _t("assistant", "Final plan: lease-before-claim on the wire queue, sixty-second backoff for non-owners, completion guarded by the owner check, backfill cutoff at September 30, migration landed before Friday. Anything you want me to adjust before I open the PR?"),
    ],
    "units": [
        _unit("u_01", "fact", "stuck at 62 percent since 3am", turn=1, notability="medium"),
        _unit("u_02", "fact", "four workers on the wire queue", turn=3, notability="medium"),
        _unit("u_03", "fact", "no lease rows at all", turn=5, notability="low"),
        _unit("u_04", "idea", "batch ownership check was probably bypassed on the retry path", turn=6),
        _unit("u_05", "fact", "two workers grabbed the same batch twice from the ingest queue", turn=9, accepts_rephrase_linked=False),
        _unit("u_06", "vibe", "losing sleep over this backfill all week and it is a relief", turn=9),
        _unit("u_07", "decision", "make each batch claim a single-owner lease", turn=11, accepts_rephrase_linked=False),
        _unit("u_08", "decision", "I do not want a distributed lock service", turn=11),
        _unit("u_09", "decision", "retry the batch after a sixty second backoff", turn=13, accepts_rephrase_linked=False),
        _unit("u_10", "idea", "that gives you crash recovery for free", turn=14),
        _unit("u_11", "fact", "shipped before the October 15 freeze", turn=15, accepts_rephrase_linked=False),
        _unit("u_12", "fact", "batches of five thousand events each", turn=16, accepts_rephrase_linked=False),
        _unit("u_13", "fact", "twelve million events for Halcyon", turn=17),
        _unit("u_14", "entity", "Maya Chen owns the ingest queue service", turn=19, accepts_rephrase_linked=False),
        _unit("u_15", "decision", "backfill cutoff at September 30", turn=21, accepts_rephrase_linked=False),
        _unit("u_16", "fact", "maintenance window is Friday at 2am", turn=23, accepts_rephrase_linked=False),
    ],
    "distractors": [
        _distractor("d_01", "The standup ran long this morning.", "morning standup ran over", turn=1),
        _distractor("d_02", "The staging cluster is on the latest release.", "staging cluster is running the latest release", turn=7),
        _distractor("d_03", "Routine trip to the break room coffee machine.", "coffee from the third floor machine", turn=17),
    ],
    "hazards": [
        _hazard("h_01", "The completion race is what made progress stall, every batch that finished got replayed by the second worker", "diego", turn=9),
        _hazard("h_02", "the hydrate stage was clean, I already audited it", "diego", turn=21),
        _hazard("h_03", "he knows the queue internals from the last incident", "diego", turn=25),
    ],
}

WP02 = {
    "session_id": "wp02_lumen_refactor",
    "harness": "codex",
    "scenario": "Jonah migrates lumen (internal dev portal) auth from shared team tokens to per-graph API keys; two-phase cutover with a pilot customer.",
    "turns": [
        _t("user", "Let us scope the lumen auth migration. We are moving lumen auth to per-graph API keys so each graph gets its own credential and its own audit trail. I sketched this after the last cross-team token incident and the team signed off last Friday."),
        _t("assistant", "Per-graph keys change the issuer shape: the key now encodes the graph id, not the team id. What prefix scheme do you want on the public key material so the registry can route a key back to its graph without a lookup?"),
        _t("user", "I like the tt_ prefix that the platform keys already use, so operators cannot mistake one for the other. Wait, actually let me double check what the platform team standardized on."),
        _t("assistant", "The platform standard is the tt_ prefix with a random 32-byte secret after it. Reusing it for graph keys keeps the operator mental model to a single key family."),
        _t("user", "Then graph keys get the tt_ prefix as well. Decision recorded: every graph key starts with tt_ and nothing else in the system may mint that prefix."),
        _t("assistant", "Locked. Next: token lifetime. The current team tokens never expire, which is exactly how the leaked token stayed valid for weeks. What lifetime do you want for graph keys?"),
        _t("user", "Give every graph key a token lifetime of twenty-four hours and require rotation on the boundary. Short-lived keys mean a leak is a one-day problem, not a quarterly audit finding."),
        _t("assistant", "Twenty-four-hour lifetime with rotation on the boundary. Do legacy team tokens get revoked at cutover, or do they keep working while teams rotate their callers?"),
        _t("user", "Legacy keys remain valid until the rotation window closes, but we stop minting new ones the day we flip. Forcing every team to rotate on day one would break the pilot."),
        _t("assistant", "Grace period model: stop minting, keep honoring, revoke at window close. Who is piloting the cutover so we can watch a real traffic shape?"),
        _t("user", "Bluepeak Logistics is the pilot customer for the per-graph rollout. Their integration team is small and they were already asking for scoped credentials."),
        _t("assistant", "Bluepeak as pilot, noted. The registry service will need to resolve a key to exactly one graph, which is a change from the team-scoped resolver. Who owns the registry service?"),
        _t("user", "Priya owns the registry service now, she inherited it with the data platform handoff. I will loop her into the interface review before we touch the resolver. Also the weather finally cleared up, I managed to run outside both mornings."),
        _t("assistant", "Before the resolver work, we should pin the failure semantics. What should happen when a caller presents a revoked key mid-request?"),
        _t("user", "Fail closed with a 401, no fallback to team scope. If a key is revoked it must be dead on the next request, otherwise the audit story falls apart."),
        _t("assistant", "Fail-closed 401 on revoked keys. For the audit trail, what retention do you need on key-usage events? The registry already logs issuance and revocation."),
        _t("user", "Keep key usage events for ninety days, same as the rest of the audit log. Anything shorter and we cannot answer the incident questions we get asked. Also, reminder that the quarterly demo is next Tuesday."),
        _t("assistant", "Ninety-day retention on key usage. Deployment shape: this touches issuance, resolution, and enforcement, so a single-commit flip is risky. How do you want to stage it?"),
        _t("user", "Two-phase deploy: mint graph keys in phase one with enforcement off, then flip enforcement in phase two after the sandbox org runs on them for a week."),
        _t("assistant", "Phase one mints, phase two enforces, sandbox org exercises the full path in between. I can scaffold the phase flag now. Is there anything in lumen that still assumes a team token shape?"),
        _t("user", "Only the admin screen, it renders the team token on the settings page. We will replace that with a per-graph key table, honestly it is overdue, I have been meaning to clean that page up for months."),
        _t("assistant", "Admin screen swap to the per-graph key table. Anything else before I write the migration plan?"),
    ],
    "units": [
        _unit("u_01", "decision", "moving lumen auth to per-graph API keys", turn=1, accepts_rephrase_linked=False),
        _unit("u_02", "fact", "the team signed off last Friday", turn=1, notability="medium"),
        _unit("u_03", "decision", "graph keys get the tt_ prefix", turn=5, accepts_rephrase_linked=False),
        _unit("u_04", "decision", "token lifetime of twenty-four hours", turn=7, accepts_rephrase_linked=False),
        _unit("u_05", "decision", "Legacy keys remain valid until the rotation window closes", turn=9, accepts_rephrase_linked=False),
        _unit("u_06", "entity", "Bluepeak Logistics is the pilot customer", turn=11, accepts_rephrase_linked=False),
        _unit("u_07", "entity", "Priya owns the registry service", turn=13, accepts_rephrase_linked=False),
        _unit("u_08", "decision", "Fail closed with a 401", turn=15, accepts_rephrase_linked=False),
        _unit("u_09", "fact", "Keep key usage events for ninety days", turn=17, accepts_rephrase_linked=False),
        _unit("u_10", "decision", "Two-phase deploy", turn=19, accepts_rephrase_linked=False),
        _unit("u_11", "fact", "the sandbox org runs on them for a week", turn=19, notability="medium"),
        _unit("u_12", "vibe", "it is overdue, I have been meaning to clean that page up for months", turn=21),
        _unit("u_13", "fact", "the leaked token stayed valid for weeks", turn=6, notability="medium"),
    ],
    "distractors": [
        _distractor("d_01", "Routine note about the weather during the auth migration session.", "managed to run outside both mornings", turn=13),
        _distractor("d_02", "Routine calendar reminder mentioned mid-session.", "the quarterly demo is next Tuesday", turn=17),
    ],
    "hazards": [
        _hazard("h_01", "the team signed off last Friday", "jonah", turn=1),
        _hazard("h_02", "their integration team is small and they were already asking for scoped credentials", "jonah", turn=11),
    ],
}

WP03 = {
    "session_id": "wp03_ember_design",
    "harness": "pi",
    "scenario": "Maya and Tomas review the ember alerting redesign: route alerts by service ownership, cut unactionable pages, and target a sustainable on-call load.",
    "turns": [
        _t("user", "Alright, let us do the ember redesign review. I pulled the on-call numbers for the last quarter and they are not great, the pager fired twelve times per week on average and almost a third of those were not actionable. The on-call room has been quiet lately at least."),
        _t("assistant", "Twelve pages a week with a third unactionable matches what I saw in the ember dashboard. Before we talk routing, who is carrying that load right now?"),
        _t("user", "It falls on whoever is in the ember on-call rotation, mostly the platform team. Tomas pulled double duty for three weeks in September while we were understaffed."),
        _t("assistant", "So the redesign has to cut both noise and load. What is the biggest source of unactionable pages?"),
        _t("user", "Pages that reference a service but no owner, so the on-call has to hunt for the right team. Roughly thirty percent of alerts are unactionable because the ownership lookup fails."),
        _t("assistant", "Thirty percent unactionable from missing ownership data. If the routing rule used the service ownership table in lumen instead of page groups, would that fix the lookup gap?"),
        _t("user", "Yes, and that is the core of the redesign. We route ember alerts by service ownership instead of by page group, and the ownership table in lumen becomes the single source of truth for where an alert goes."),
        _t("assistant", "Routing by the lumen ownership table. What about services with no owner row yet, they will fall through any ownership-based router."),
        _t("user", "A service with no owner row falls to the default rotation and we flag it in the weekly ownership report. No silent drops, every alert still lands somewhere."),
        _t("assistant", "Fallback to default rotation with a weekly report. Now the load target: twelve pages a week is high even when actionable. What is the sustainable number?"),
        _t("user", "I want to get the pager down to four actionable pages per week before the end of the quarter. That means cutting noise at the source, not just routing it better."),
        _t("assistant", "Four actionable pages per week is a two-thirds cut from today. Noise reduction at the source points at severity, what would you consider preflight gating?"),
        _t("user", "Add a severity preflight that requires a real user impact statement before a page goes out. If an alert cannot state the impact, it is an incident log entry, not a page. Also the team demo is Thursday afternoon."),
        _t("assistant", "Severity preflight with an impact statement as the paging gate. Maintenance windows are the other classic noise source, do you want ember to auto-suppress during declared windows?"),
        _t("user", "Yes, auto-suppress alerts during declared maintenance windows. We declare windows in lumen anyway, so ember reading them costs us nothing."),
        _t("assistant", "Auto-suppress during lumen-declared windows. Who owns declaring windows today, is that consistent enough to rely on?"),
        _t("user", "Owners declare windows for their own services and the coverage has been solid since the SLO review. Aisha suggested we keep the declarations human-owned rather than automated, which I agree with."),
        _t("assistant", "Human-owned window declarations, agreed. Rollout: changing routing for the whole fleet at once is risky, how do you want to stage the ownership-table migration?"),
        _t("user", "Migrate service by service over two sprints, starting with the ten noisiest services. We re-check the pager load after each sprint so we can tell if the routing change is actually helping."),
        _t("assistant", "Two-sprint migration starting with the ten noisiest services. When is the first cutover review so the on-call team can weigh in on the routing behavior?"),
        _t("user", "The first cutover review is scheduled for October 2 with the whole on-call team. Tomas wants to watch the first two weeks of ownership-based routing before we expand."),
        _t("assistant", "October 2 review with the on-call team. One more thing worth deciding now: when an alert is suppressed, should the on-call still see it in the digest the next morning?"),
        _t("user", "Yes, suppressed alerts still land in the morning digest so nothing disappears. Blameless handling all around, we want people to page early, not hide alerts."),
        _t("assistant", "Morning digest keeps suppressed alerts visible. That closes the loop: route by ownership, default-rotation fallback, severity preflight, maintenance auto-suppress, two-sprint migration, October 2 review. Shall I write the design doc?"),
    ],
    "units": [
        _unit("u_01", "fact", "the pager fired twelve times per week", turn=1, accepts_rephrase_linked=False),
        _unit("u_02", "fact", "thirty percent of alerts are unactionable", turn=5, accepts_rephrase_linked=False),
        _unit("u_03", "decision", "We route ember alerts by service ownership instead of by page group", turn=7, accepts_rephrase_linked=False),
        _unit("u_04", "fact", "the ownership table in lumen becomes the single source of truth", turn=7, accepts_rephrase_linked=False),
        _unit("u_05", "decision", "falls to the default rotation and we flag it in the weekly ownership report", turn=9),
        _unit("u_06", "decision", "get the pager down to four actionable pages per week", turn=11, accepts_rephrase_linked=False),
        _unit("u_07", "decision", "Add a severity preflight that requires a real user impact statement", turn=13, accepts_rephrase_linked=False),
        _unit("u_08", "decision", "auto-suppress alerts during declared maintenance windows", turn=15, accepts_rephrase_linked=False),
        _unit("u_09", "idea", "Aisha suggested we keep the declarations human-owned rather than automated", turn=17),
        _unit("u_10", "entity", "Tomas pulled double duty for three weeks in September", turn=3, notability="medium"),
        _unit("u_11", "decision", "Migrate service by service over two sprints", turn=19, accepts_rephrase_linked=False),
        _unit("u_12", "fact", "starting with the ten noisiest services", turn=19, notability="medium"),
        _unit("u_13", "fact", "first cutover review is scheduled for October 2", turn=21, accepts_rephrase_linked=False),
        _unit("u_14", "decision", "suppressed alerts still land in the morning digest", turn=23),
        _unit("u_15", "vibe", "we want people to page early, not hide alerts", turn=23),
    ],
    "distractors": [
        _distractor("d_01", "Routine aside that the on-call room has been quiet.", "The on-call room has been quiet lately", turn=1),
        _distractor("d_02", "Routine scheduling note about the team demo.", "the team demo is Thursday afternoon", turn=13),
    ],
    "hazards": [
        _hazard("h_01", "roughly thirty percent of alerts are unactionable because the ownership lookup fails", "maya", turn=5),
        _hazard("h_02", "Tomas wants to watch the first two weeks of ownership-based routing before we expand", "maya", turn=21),
    ],
}

WP04 = {
    "session_id": "wp04_aurora_perf",
    "harness": "pi",
    "scenario": "Priya investigates aurora (customer dashboard) latency regressions: N+1 embedding calls behind the p95 spike, fixed with a bounded cache and a batch lookup.",
    "turns": [
        _t("user", "Let us look at the aurora dashboard latency. The p95 on the load endpoint has been creeping up all week and it hit 4.2 seconds yesterday afternoon, which is way past what the customer-facing SLO allows."),
        _t("assistant", "4.2 seconds at p95 is an order of magnitude past the dashboard target. When did the creep start relative to the recent deploys?"),
        _t("user", "The spike correlates with the October 5 release, the one that added the related-memory sidebar. Before that release the p95 was comfortably under a second. Also, thanks for the lunch recommendation, the ramen place was great."),
        _t("assistant", "So the sidebar feature is the prime suspect. What does the request path do when it renders the related items?"),
        _t("user", "For every visible memory row the dashboard fires one embedding lookup to rank the related items, so a page with forty rows issues forty separate calls. The N+1 embedding calls are what blew up the p95."),
        _t("assistant", "Forty sequential embedding calls per page is the whole story. The embedding service is fast, but forty round-trips at even twenty milliseconds each is nearly a second before any ranking happens."),
        _t("user", "Right, and then the ranking runs on top of that. I want to batch the lookup into a single call per page instead of one call per row, the embedding service supports a batch endpoint."),
        _t("assistant", "Batch the lookup to one call per page. The other lever is caching, do the sidebar queries repeat across requests from the same team?"),
        _t("user", "They repeat constantly, the same dashboards get opened all day by the same account managers. We should cache the embedding results in memory with a ten minute TTL and refresh them ahead of the next view."),
        _t("assistant", "In-memory cache with a ten-minute TTL plus refresh-ahead covers the repeat-view pattern. What is the read volume the cache has to sustain?"),
        _t("user", "The dashboard serves about forty thousand load requests per day across all customers, and the sidebar fires on most of them. That is the volume we need to absorb."),
        _t("assistant", "Forty thousand requests a day with sidebar lookups on most of them. A ten-minute TTL cache will absorb nearly all of the repeat traffic, the batch call only runs on a cache miss."),
        _t("user", "Exactly. And I want to ship this behind a flag so we can compare the p95 before and after without a full rollout. The flag defaults off until the perf review signs off."),
        _t("assistant", "Flag-gated rollout with the perf review as the gate. Who runs the perf review for aurora these days?"),
        _t("user", "Felix runs the dashboard perf review since he owns the frontend budget. He flagged the sidebar as a risk in the design review, I should have listened. The design review ran long but the notes were thorough."),
        _t("assistant", "Felix owns the frontend perf review. One risk with the cache: stale related items if the underlying memory changes between refreshes. How do you want to handle invalidation?"),
        _t("user", "Invalidate the cache entry for a team whenever a write lands in that team's graph. Writes are rare compared to reads, so the invalidation traffic is tiny."),
        _t("assistant", "Write-triggered invalidation per team. That keeps the ten-minute TTL as a backstop rather than the primary consistency mechanism."),
        _t("user", "Yes. Realistically this gets the p95 back under 800 milliseconds, which is the number we promised the account teams. Honestly I am glad we finally measured it, the dashboard has been getting slower for months and nobody could point at one cause."),
        _t("assistant", "Target under 800 milliseconds at p95. Summary: batch the embedding lookup per page, ten-minute TTL in-memory cache with refresh-ahead, write-triggered invalidation, flag-gated rollout. Want me to draft the implementation plan?"),
    ],
    "units": [
        _unit("u_01", "fact", "it hit 4.2 seconds yesterday afternoon", turn=1, accepts_rephrase_linked=False),
        _unit("u_02", "fact", "The spike correlates with the October 5 release", turn=3, accepts_rephrase_linked=False),
        _unit("u_03", "fact", "a page with forty rows issues forty separate calls", turn=5),
        _unit("u_04", "fact", "The N+1 embedding calls are what blew up the p95", turn=5, accepts_rephrase_linked=False),
        _unit("u_05", "decision", "batch the lookup into a single call per page", turn=7, accepts_rephrase_linked=False),
        _unit("u_06", "decision", "cache the embedding results in memory with a ten minute TTL", turn=9, accepts_rephrase_linked=False),
        _unit("u_07", "idea", "refresh them ahead of the next view", turn=9, notability="medium"),
        _unit("u_08", "fact", "forty thousand load requests per day", turn=11, accepts_rephrase_linked=False),
        _unit("u_09", "decision", "ship this behind a flag", turn=13),
        _unit("u_10", "entity", "Felix runs the dashboard perf review", turn=15, accepts_rephrase_linked=False),
        _unit("u_11", "idea", "Invalidate the cache entry for a team whenever a write lands", turn=17, accepts_rephrase_linked=False),
        _unit("u_12", "fact", "gets the p95 back under 800 milliseconds", turn=19, accepts_rephrase_linked=False),
        _unit("u_13", "vibe", "glad we finally measured it, the dashboard has been getting slower for months", turn=19),
    ],
    "distractors": [
        _distractor("d_01", "Routine aside thanking for a lunch recommendation.", "the ramen place was great", turn=3),
        _distractor("d_02", "Routine note about a long design review.", "the notes were thorough", turn=15),
    ],
    "hazards": [
        _hazard("h_01", "he flagged the sidebar as a risk in the design review", "priya", turn=15),
        _hazard("h_02", "the number we promised the account teams", "priya", turn=19),
    ],
}

WP05 = {
    "session_id": "wp05_retro_writeup",
    "harness": "claude-desktop",
    "scenario": "Sam drafts the Bluepeak incident retro with Aisha: timeline, root cause, follow-ups with owners and dates, published as an opt-out incident writeup.",
    "turns": [
        _t("user", "Let us draft the Bluepeak incident retro. The outage ran from 9:12am to 12:53pm on Monday, so about three hours and forty-one minutes of degraded search for their team."),
        _t("assistant", "Three hours forty-one minutes of degraded search. Let us capture the timeline first. What was the first signal the on-call saw?"),
        _t("user", "The first signal was the error budget burn alert at 9:15am, three minutes after the deploy that introduced the bad configuration landed. The deploy went out at 9:12am."),
        _t("assistant", "Deploy at 9:12, burn alert at 9:15. What did the initial investigation blame before the actual root cause surfaced?"),
        _t("user", "The initial investigation blamed the search index, which made us chase a reindex for the first forty minutes. Aisha kept pushing us to check the config diff instead, and that is where the real cause was."),
        _t("assistant", "So the config diff was the real cause. Can you state the root cause precisely for the retro?"),
        _t("user", "The config partition was the root cause, the deploy shipped a config that split the query traffic unevenly across the search shards and one shard saturated."),
        _t("assistant", "Config partition confirmed. What was the mitigation that brought the system back?"),
        _t("user", "We rolled the config back to the previous partition at 12:40pm and traffic normalized within thirteen minutes. Search was fully healthy again by 12:53pm."),
        _t("assistant", "Rollback at 12:40, healthy by 12:53. Now the follow-ups. What actions do we commit to so this class of incident does not repeat?"),
        _t("user", "First, add a pre-deploy check that compares partition weights against the shard capacity model. Second, make the config diff visible in the deploy review. Nora suggested both and they are both good."),
        _t("assistant", "Pre-deploy partition check and visible config diff. Who owns each follow-up and by when?"),
        _t("user", "Maya owns the pre-deploy check and it needs to land by November 2. Diego owns the config diff surfacing, due November 9."),
        _t("assistant", "Maya by November 2, Diego by November 9. Was the SLO breached, and do we owe the customer anything contractual?"),
        _t("user", "The search SLO is 99.9 percent and we landed at 99.86 for the month, so yes we breached it. We already offered Bluepeak a service credit and their account team accepted. The account call ran smoothly, good Wi-Fi in the new meeting room."),
        _t("assistant", "SLO breach confirmed with a credit offered and accepted. Do you want the retro published to the whole company, or restricted to the engineering org?"),
        _t("user", "Publish it company-wide. Incident writeups are opt-out by default now, that was the decision after the August incident, so the whole company sees the timeline and the follow-ups. I will book the retro room for Friday morning in case anyone wants to talk it through."),
        _t("assistant", "Company-wide publication with opt-out default. One more section retros often miss: what went well under pressure?"),
        _t("user", "The rollback went smoothly because the deploy tooling kept the previous config one click away. And the on-call stayed calm, honestly that matters more than any tooling."),
        _t("assistant", "Rollback tooling and on-call calmness under pressure. Anything the timeline still misses before I assemble the writeup?"),
        _t("user", "Add the comms timeline: we posted the first customer-facing status at 9:40am and the all-clear at 1:05pm. Sam from customer engineering wrote the status updates with Aisha."),
        _t("assistant", "Customer status at 9:40, all-clear at 1:05. That is a complete retro: timeline, root cause, mitigation, follow-ups with owners and dates, SLO breach with credit, company-wide publication."),
        _t("user", "Yes, and let us also record that the search team will run a shard capacity review before the end of the year, Priya volunteered to lead it."),
    ],
    "units": [
        _unit("u_01", "fact", "three hours and forty-one minutes of degraded search", turn=1, accepts_rephrase_linked=False),
        _unit("u_02", "fact", "The first signal was the error budget burn alert at 9:15am", turn=3, accepts_rephrase_linked=False),
        _unit("u_03", "fact", "the deploy went out at 9:12am", turn=3, accepts_rephrase_linked=False),
        _unit("u_04", "fact", "The initial investigation blamed the search index", turn=5, notability="medium"),
        _unit("u_05", "fact", "The config partition was the root cause", turn=7, accepts_rephrase_linked=False),
        _unit("u_06", "fact", "We rolled the config back to the previous partition at 12:40pm", turn=9, accepts_rephrase_linked=False),
        _unit("u_07", "decision", "add a pre-deploy check that compares partition weights against the shard capacity model", turn=11, accepts_rephrase_linked=False),
        _unit("u_08", "decision", "make the config diff visible in the deploy review", turn=11, accepts_rephrase_linked=False),
        _unit("u_09", "idea", "Nora suggested both", turn=11, notability="medium"),
        _unit("u_10", "entity", "Maya owns the pre-deploy check and it needs to land by November 2", turn=13, accepts_rephrase_linked=False),
        _unit("u_11", "fact", "Diego owns the config diff surfacing, due November 9", turn=13, accepts_rephrase_linked=False),
        _unit("u_12", "fact", "the search SLO is 99.9 percent", turn=15, accepts_rephrase_linked=False),
        _unit("u_13", "decision", "Incident writeups are opt-out by default now", turn=17, accepts_rephrase_linked=False),
        _unit("u_14", "vibe", "the on-call stayed calm, honestly that matters more than any tooling", turn=19),
        _unit("u_15", "fact", "Sam from customer engineering wrote the status updates", turn=21),
    ],
    "distractors": [
        _distractor("d_01", "Routine note about booking a retro room.", "book the retro room for Friday morning", turn=17),
        _distractor("d_02", "Routine aside about the meeting room during the account call.", "good Wi-Fi in the new meeting room", turn=15),
    ],
    "hazards": [
        _hazard("h_01", "Aisha kept pushing us to check the config diff instead", "sam", turn=5),
        _hazard("h_02", "their account team accepted", "sam", turn=15),
    ],
}


WP06 = {
    "session_id": "wp06_quarry_rollout",
    "harness": "codex",
    "scenario": "Diego and Maya decide the rollout shape for the quarry lease fix: the chaos harness supports the scope-independence claim (SUPPORTS) and they commit to all-at-once behind a single global kill flag (the decision the follow-up session overturns).",
    "turns": [
        _t("user", "Let us decide the rollout for the quarry lease fix. The lease-before-claim change is merged and the staging matrix is green. We have until the October freeze to ship it to the Halcyon queue, so this is a today decision, not a next-week one."),
        _t("assistant", "Two levers shape the rollout: scope, all shards at once versus one shard at a time, and reversibility, a kill flag we can pull versus a canary we watch. Which do you want to decide on data?"),
        _t("user", "Data first. I want to run the chaos harness against both rollout shapes before we pick. Same fault set, four shards, killed workers mid-claim."),
        _t("assistant", "The all-at-once shape is the lease fix enabled fleet-wide behind one global kill flag. The per-shard shape enables it shard by shard over a week, each shard watched before the next one flips."),
        _t("user", "My worry with any shape is the stall coming back. If we flip mid-rollout and the flip glitches, we could re-enter the duplicate-processing race while the queue drains."),
        _t("assistant", "The original stall was a no-lease bug, not a flip bug, but the glitch scenario is fair to test. The chaos harness covers it: it kills workers, it does not toggle flags."),
        _t("user", "Right, and I want the harness to prove the boundary case: two workers claiming one batch at the exact moment a region toggles."),
        _t("assistant", "That boundary is the lease CAS: only the owner can complete, so a second claim attempt fails at compare-and-swap regardless of what toggles around it."),
        _t("user", "That is my read too: the race only re-enters when two workers can claim one batch, and the lease makes that impossible no matter how we roll out."),
        _t("assistant", "Hypothesis recorded. The chaos run either confirms it at the shard boundary or it does not, scope independence is the claim under test."),
        _t("user", "If the run confirms it, all-at-once is tempting: one day of rollout instead of a week of canary watching."),
        _t("assistant", "All-at-once buys the shortest exposure window and the flag is the reversibility backstop. Per-shard buys a smaller blast radius per flip. The chaos numbers should decide."),
        _t("user", "The chaos run just finished. We killed a lease-holding worker mid-batch on four shards and every batch was claimed exactly once after the expiry, no duplicates anywhere. It took longer than expected because I queued it behind the nightly embedding job."),
        _t("assistant", "So the chaos run supports the claim that the race cannot re-enter once the lease is held, rollout scope does not change the guarantee."),
        _t("user", "Then I am not going to argue for a week of canary watching. Scope is settled by the data. My shift ends at six, so if the flip lands today I want the runbook link in the channel."),
        _t("assistant", "Scope settled: the lease guarantee is scope-independent. That leaves reversibility: one global flag versus per-service flags."),
        _t("user", "We ship the lease fix all-at-once behind a single global kill flag. One flag is one switch to pull, and the chaos run says the fix itself cannot re-race."),
        _t("assistant", "Decision recorded: all-at-once rollout behind the global kill flag, watch the queue drain after the flip, pull the flag on any regression."),
        _t("user", "And who drives the flip? I would rather it be one person with the runbook open, not a group chat decision at 2am."),
        _t("assistant", "One driver with the runbook. Maya is the natural driver for the flip, she owns the ingest queue service and ran the staging matrix."),
        _t("user", "Cutoff stays September 30 as agreed, and the lease migration already landed ahead of Friday's maintenance window."),
        _t("assistant", "Rollout plan: all-at-once behind the global kill flag, driver Maya, cutoff unchanged, migration landed before Friday. Shall I open the rollout PR?"),
    ],
    "units": [
        _unit("u_01", "fact", "the staging matrix is green", turn=1, notability="medium"),
        _unit("u_02", "fact", "until the October freeze", turn=1, notability="medium"),
        _unit("u_03", "decision", "run the chaos harness against both rollout shapes", turn=3, accepts_rephrase_linked=False),
        _unit("u_04", "idea", "we could re-enter the duplicate-processing race while the queue drains", turn=5),
        _unit("u_05", "fact", "two workers claiming one batch at the exact moment a region toggles", turn=7),
        _unit("u_06", "idea", "one day of rollout instead of a week of canary watching", turn=11),
        _unit("u_07", "decision", "one person with the runbook open", turn=19, accepts_rephrase_linked=False),
        _unit("u_08", "entity", "Maya is the natural driver for the flip", turn=20, accepts_rephrase_linked=False),
        _unit("u_09", "fact", "lease migration already landed ahead of Friday's maintenance window", turn=21, accepts_rephrase_linked=False),
    ],
    "distractors": [
        _distractor("d_01", "The chaos run waited for the nightly embedding job.", "queued it behind the nightly embedding job", turn=13),
        _distractor("d_02", "Routine note that the shift ends at six.", "my shift ends at six", turn=15),
    ],
    "hazards": [
        _hazard("h_01", "we killed a lease-holding worker mid-batch on four shards", "maya", turn=13),
        _hazard("h_02", "I would rather it be one person with the runbook open", "diego", turn=19),
    ],
    "planted_operators": [
        _operator("op_01", "SUPPORTS",
                  "every batch was claimed exactly once after the expiry", 13,
                  "the lease makes that impossible no matter how we roll out", 9,
                  relation_turn=14,
                  reason="chaos-run observation (t13) supports the scope-independence hypothesis (t9) — ONTOLOGY §3.1 IMPL 'A supports/implies B' + §8 semantic type supports|IMPL (Evidence supports Claim); asst t14 states the relation explicitly ('the chaos run supports the claim that...'). from = observation/evidence, to = hypothesis."),
    ],
}

WP07 = {
    "session_id": "wp07_bluepeak_followup",
    "harness": "codex",
    "scenario": "Maya and Diego investigate a six-minute duplicate anomaly after the all-at-once rollout: the flag hypothesis is counter-claimed by clock-skew evidence (NEGATE), a grace period closes the skew risk (MITIGATES), and the all-at-once global-flag decision from the prior session is overturned for per-service flags (SUPERSEDE, cross-session).",
    "turns": [
        _t("user", "The all-at-once rollout shipped clean on Friday, but there was an anomaly overnight in the Bluepeak region: duplicate claims for about six minutes on the hydrate queue. We need a cause before the next rollout. The on-call handoff was smooth at least, no pages overnight."),
        _t("assistant", "Duplicates on the hydrate queue post-fix is the exact class the lease was supposed to kill. Six minutes is short, a window rather than a drain. Let us timeline it against the flag state."),
        _t("user", "We toggled the global flag once at cutover and never touched it after. I pulled the toggle log to be sure."),
        _t("assistant", "One toggle at cutover. Then the anomaly is not a mid-flight flip, unless the flag state itself was the trigger."),
        _t("user", "My first hypothesis is the global flag: the flip must have raced the lease renewal when the region cut over, leaving a renewal window where two workers could claim one batch. Honestly I have been second-guessing the all-at-once call since we made it."),
        _t("assistant", "Flag-raced-renewal is testable: compare the toggle timestamp against the lease renewal stamps in the region around the anomaly."),
        _t("user", "I pulled both. The toggle is two hours before the first duplicate, and the renewal stamps are all clean, no overlap, no double renewal."),
        _t("assistant", "Two hours of stable flag state before the duplicates already weakens the flag hypothesis. What does differ in that region?"),
        _t("user", "Whatever the cause, here is the risk that matters for the next rollout: clock skew between regions can make lease expiry unsafe, a lagging region renews after the owner's lease truly expired, and its renewal can clobber a live lease in another region."),
        _t("assistant", "Skew is a real expiry hazard: the CAS compares against wall-clock expiry, so a drifted clock changes who wins. Is that what the timestamps show?"),
        _t("user", "The flag did not cause the duplicates, it had been stable for two hours before the anomaly, and the renewal stamps show the region clock drifted eleven seconds, which is exactly the six-minute duplicate window on the hydrate queue."),
        _t("assistant", "So the flag hypothesis is out, the clock drift explains the window, and the lease behaved as designed under a drifted clock: it expired early, the second worker claimed the batch, and the hydrate side saw the duplicate."),
        _t("user", "Then the fix is not about the flag at all. A skew-tolerant lease: the expiry check needs a grace period so a lagging region cannot clobber a live lease."),
        _t("assistant", "Grace period on the lease expiry, sized to the worst observed drift. What is the biggest skew we have seen between regions?"),
        _t("user", "Eleven seconds is the worst we have logged, but the SLO review assumed sub-second. I would size the grace to thirty seconds and log any expiry that lands inside it."),
        _t("assistant", "Thirty-second grace with an internal log on grace expiries. That bounds the skew hazard without making the lease lax."),
        _t("user", "We shipped the skew-tolerant lease grace period to every region last night, with the grace window a lagging region's renewal cannot clobber a live lease, so the clock-skew risk is covered."),
        _t("assistant", "Risk closed by the grace period: expiry is now skew-tolerant, and the duplicates are attributed to the drift, not to the fix."),
        _t("user", "And one more decision while we are here: for the next rollout, reverse the all-at-once call, per-service flags instead of one global flag, so a single bad region cannot take the whole fleet down with it. Honestly the global flag has been the single point of failure all along."),
        _t("assistant", "Per-service flags changes the reversibility model: each service's flag is pulled independently instead of one fleet-wide switch."),
        _t("user", "Right. The global flag made one bad region a fleet-wide event; per-service flags keep a bad region from taking the whole fleet down."),
        _t("user", "Good, and the skew fix ships with the hydrate queue re-run before the next rollout. Priya will track the grace-expiry log for a week. Also the demo room is booked Thursday if you want to show the dashboards."),
    ],
    "units": [
        _unit("u_01", "fact", "duplicate claims for about six minutes on the hydrate queue", turn=1, accepts_rephrase_linked=False),
        _unit("u_02", "fact", "The on-call handoff was smooth", turn=1, notability="low"),
        _unit("u_03", "fact", "toggled the global flag once at cutover", turn=3, accepts_rephrase_linked=False),
        _unit("u_04", "vibe", "second-guessing the all-at-once call since we made it", turn=5),
        _unit("u_05", "fact", "the toggle is two hours before the first duplicate", turn=7, accepts_rephrase_linked=False),
        _unit("u_06", "fact", "Eleven seconds is the worst we have logged", turn=15, accepts_rephrase_linked=False),
        _unit("u_07", "decision", "size the grace to thirty seconds", turn=15, accepts_rephrase_linked=False),
        _unit("u_08", "entity", "Priya will track the grace-expiry log for a week", turn=22, accepts_rephrase_linked=False),
        _unit("u_09", "vibe", "the global flag has been the single point of failure all along", turn=19),
    ],
    "distractors": [
        _distractor("d_01", "Routine aside that the demo room is booked Thursday.", "the demo room is booked Thursday", turn=22),
        _distractor("d_02", "Routine on-call note from the handoff.", "no pages overnight", turn=1),
    ],
    "hazards": [
        _hazard("h_01", "the region clock drifted eleven seconds, which is exactly the six-minute duplicate window", "diego", turn=11),
        _hazard("h_02", "the renewal stamps are all clean, no overlap, no double renewal", "maya", turn=7),
    ],
    "planted_operators": [
        _operator("op_02", "NEGATE",
                  "The flag did not cause the duplicates, it had been stable for two hours before the anomaly", 11,
                  "the flip must have raced the lease renewal when the region cut over", 5,
                  relation_turn=12,
                  reason="counter-claim with an explicit negation marker (t11 'did not cause') attacks the flag hypothesis (t5) — ONTOLOGY §3.1 NAND 'A contradicts B'; extraction-emitted NANDs default unidirectional (#909: new-claim-attacks-existing). from = counter-claim/attacker, to = the attacked hypothesis; asst t12 states the contradiction ('the flag hypothesis is out')."),
        _operator("op_03", "MITIGATES",
                  "a lagging region's renewal cannot clobber a live lease", 17,
                  "clock skew between regions can make lease expiry unsafe", 9,
                  relation_turn=18,
                  reason="the shipped grace period (t17) reduces the clock-skew risk claim (t9) — ONTOLOGY §3.1/§3.9 mitigate_operator / mitigated_by; FLAGGED (scoping finding F1): core §3.1 vocabulary (IMPL/NAND/CORRECTS) has no point-to-point 'action reduces risk' operator and registered MITIGATES is relevance-on-an-operator-edge (extractor S2: claim true but matters less than it seems). Gold asserts the write path's MITIGATES form; NAND/supersede of the risk is the ontology-owner question."),
        _operator("op_04", "SUPERSEDE",
                  "per-service flags instead of one global flag", 19,
                  "ship the lease fix all-at-once behind a single global kill flag", 17,
                  relation_turn=19, to_session="wp06_quarry_rollout",
                  reason="decision D2 (t19 'reverse the all-at-once call') overturns decision D1 made in the prior session (wp06 t17) — ONTOLOGY §3.1 CORRECTS (new point corrects/replaces an outdated point; supersede_point marks old outdated). FLAGGED (scoping finding F2): §2 state-centric + extractor S2 supersede ENTITY/state-level (one 'B supersedes A' statement point + ObjectSuperseded); point-level CORRECTS forms when S3 resolves the superseded claim already in-graph. Planted CROSS-SESSION (wp07 -> wp06) because that is the only mechanism by which today's v2 extractor can emit point-level CORRECTS in one sequential corpus capture. from = the new decision (own session), to = the superseded decision (wp06)."),
    ],
}


AUTHORED_SESSIONS = [WP01, WP02, WP03, WP04, WP05, WP06, WP07]


# ── Rendering ───────────────────────────────────────────────────────────────


def _build_session_docs(spec: dict) -> tuple[dict, dict]:
    """Derive the committed fixture doc + sealed gold doc for one session.

    Validates authoring invariants loudly (missing anchors list every
    violation) so fixture/gold drift can never ship silently.  Emitted gold
    ids are globally-unique by prefixing the session stem (``wp01_x_u_01``) —
    a corpus-wide aggregation in the W2-b runner can key on bare ids safely.
    """
    turns = spec["turns"]
    n_turns = len(turns)
    fixture = {"session_id": spec["session_id"], "harness": spec["harness"], "conversation": turns}
    prefix = spec["session_id"]

    planted_units = []
    salient_units = []
    ordered_units = sorted(spec["units"], key=lambda u: (u["planted_turn"], u["id"]))
    for unit in ordered_units:
        anchor = unit["verbatim_anchor"]
        planted_turn = unit["planted_turn"]
        if not (1 <= planted_turn <= n_turns):
            raise ValueError(
                f"{spec['session_id']}: {unit['id']} planted_turn {planted_turn} "
                f"out of range for {n_turns} turns"
            )
        content = turns[planted_turn - 1]["content"]
        if not schema.anchor_present(anchor, content):
            raise ValueError(
                f"{spec['session_id']}: {unit['id']} verbatim_anchor {anchor!r} is NOT a "
                f"normalized substring of turn {planted_turn} content:\n    {content[:200]}"
            )
        unit_id = f"{prefix}_{unit['id']}"
        planted_units.append(
            {
                "id": unit_id,
                "kind": unit["kind"],
                "verbatim_anchor": anchor,
                "notability": unit["notability"],
                "depth_bucket": schema.depth_bucket_for(planted_turn, n_turns),
                "planted_turn": planted_turn,
            }
        )
        salient_units.append(
            {
                "id": unit_id,
                "survival": {
                    "via_anchor": anchor,
                    "accepts_rephrase_linked": unit["survival"]["accepts_rephrase_linked"],
                    "provenance_required": unit["survival"]["provenance_required"],
                    "ep_update_required": unit["survival"]["ep_update_required"],
                },
            }
        )

    distractors = []
    for distractor in spec["distractors"]:
        planted_turn = distractor["planted_turn"]
        if not (1 <= planted_turn <= n_turns):
            raise ValueError(
                f"{spec['session_id']}: distractor {distractor['id']} planted_turn "
                f"{planted_turn} out of range for {n_turns} turns"
            )
        content = turns[planted_turn - 1]["content"]
        if not schema.anchor_present(distractor["anchor"], content):
            raise ValueError(
                f"{spec['session_id']}: distractor {distractor['id']} anchor "
                f"{distractor['anchor']!r} is NOT a normalized substring of turn {planted_turn}"
            )
        distractors.append(
            {
                "id": f"{prefix}_{distractor['id']}",
                "statement": distractor["statement"],
                "anchor": distractor["anchor"],
                "planted_turn": planted_turn,
            }
        )

    hazards = []
    for hazard in spec["hazards"]:
        planted_turn = hazard["planted_turn"]
        if not (1 <= planted_turn <= n_turns):
            raise ValueError(
                f"{spec['session_id']}: hazard {hazard['id']} planted_turn "
                f"{planted_turn} out of range for {n_turns} turns"
            )
        content = turns[planted_turn - 1]["content"]
        if not schema.anchor_present(hazard["quote"], content):
            raise ValueError(
                f"{spec['session_id']}: hazard {hazard['id']} quote {hazard['quote']!r} is NOT "
                f"a normalized substring of turn {planted_turn}"
            )
        hazards.append(
            {
                "id": f"{prefix}_{hazard['id']}",
                "quote": hazard["quote"],
                "source": hazard["source"],
                "planted_turn": planted_turn,
            }
        )

    gold = {
        "schema_version": schema.SCHEMA_VERSION,
        "session_id": spec["session_id"],
        "scenario": spec["scenario"],
        "planted_units": planted_units,
        "distractors": distractors,
        "attribution_hazards": hazards,
        "salient_units": salient_units,
        "distractor_leakage_tolerance": 1,  # research-recommended ≤1/run (gbrain 1/86)
    }
    planted_operators = _build_planted_operators(spec, turns, n_turns, prefix)
    if planted_operators:
        gold["planted_operators"] = planted_operators
    return fixture, gold


def _build_planted_operators(spec: dict, turns: list[dict], n_turns: int,
                             prefix: str) -> list[dict]:
    """Derive the sealed ``planted_operators`` gold section for one session.

    Issue #2514 layer-2 gold.  Each spec entry names the expected operator
    kind + two anchored endpoints + the OWN-session ``relation_turn`` where
    the relation is stated.  Anchors grounded in the own session are verified
    here (normalized-substring); anchors that point at ANOTHER session
    (``session_id`` set — the cross-session SUPERSEDE) are verified by
    ``validate_committed`` corpus-wide.  Emitted ids are globally unique via
    the session-stem prefix (``wp07_x_op_02``) like every other gold id.
    """
    ops = spec.get("planted_operators") or []
    if not isinstance(ops, list):
        raise ValueError(f"{prefix}: planted_operators must be a list")
    own = spec["session_id"]
    rendered: list[dict] = []
    seen: set[str] = set()
    for op in sorted(ops, key=lambda o: (o.get("relation_turn", 0), o.get("id", ""))):
        kind = op["expected_kind"]
        if kind not in schema.PLANTED_OPERATOR_KIND_VALUES:
            raise ValueError(f"{prefix}: unknown planted-operator kind {kind!r}")
        relation_turn = op["relation_turn"]
        if not (1 <= relation_turn <= n_turns):
            raise ValueError(
                f"{prefix}: op {op.get('id')} relation_turn {relation_turn} "
                f"out of range for {n_turns} turns"
            )
        endpoints: list[tuple[str, dict]] = []
        for side in ("from", "to"):
            endpoint = op[side]
            endpoint_session = endpoint.get("session_id") or own
            anchor = endpoint["verbatim_anchor"]
            planted_turn = endpoint["planted_turn"]
            if endpoint_session == own:
                if not (1 <= planted_turn <= n_turns):
                    raise ValueError(
                        f"{prefix}: op {op.get('id')} {side} planted_turn "
                        f"{planted_turn} out of range for {n_turns} turns"
                    )
                content = turns[planted_turn - 1]["content"]
                if not schema.anchor_present(anchor, content):
                    raise ValueError(
                        f"{prefix}: op {op.get('id')} {side} verbatim_anchor "
                        f"{anchor!r} is NOT a normalized substring of turn "
                        f"{planted_turn} content:\n    {content[:200]}"
                    )
            rendered_endpoint = {"verbatim_anchor": anchor, "planted_turn": planted_turn}
            if endpoint_session != own:
                rendered_endpoint["session_id"] = endpoint_session
            endpoints.append((side, rendered_endpoint))
        op_id = f"{prefix}_{op['id']}"
        if op_id in seen:
            raise ValueError(f"{prefix}: duplicate planted-operator id {op_id!r}")
        seen.add(op_id)
        rendered.append({
            "id": op_id,
            "expected_kind": kind,
            "from": endpoints[0][1],
            "to": endpoints[1][1],
            "relation_turn": relation_turn,
            "reason": op["reason"],
        })
    return rendered


def _dump_json_bytes(doc: dict) -> bytes:
    """Byte-deterministic JSON rendering (sorted keys, indent 2, LF)."""
    text = json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=True)
    return (text + "\n").encode("utf-8")


def render_corpus() -> dict[str, bytes]:
    """Render the full corpus to ``{relative_path: bytes}`` (no disk writes).

    Deterministic — includes fixtures, sealed gold, ``_manifest.json`` and the
    first-run-pending ``baselines/main.json`` (its fixtures_hash is computed
    over the freshly rendered fixture + gold digests).
    """
    outputs: dict[str, bytes] = {}
    file_digests: dict[str, str] = {}
    session_count = 0
    unit_count = 0
    for spec in AUTHORED_SESSIONS:
        fixture, gold = _build_session_docs(spec)
        fixture_rel = f"fixtures/{spec['session_id']}.json"
        gold_rel = f"gold/{spec['session_id']}.gold.json"
        outputs[fixture_rel] = _dump_json_bytes(fixture)
        outputs[gold_rel] = _dump_json_bytes(gold)
        file_digests[fixture_rel] = schema.sha256_bytes(outputs[fixture_rel])
        file_digests[gold_rel] = schema.sha256_bytes(outputs[gold_rel])
        session_count += 1
        unit_count += len(gold["planted_units"])

    if session_count < MIN_SESSIONS:
        raise AssertionError(f"corpus has {session_count} sessions < {MIN_SESSIONS} floor")
    if unit_count < MIN_PLANTED_SALIENT_UNITS:
        raise AssertionError(
            f"corpus has {unit_count} planted salient units < {MIN_PLANTED_SALIENT_UNITS} floor"
        )
    _assert_operator_floors(outputs)

    # fixtures_hash covers fixture AND gold files (a gold-only edit changes it
    # ⇒ invalidates committed baselines — E2E-2 negative gate).
    digest_payload = "\n".join(
        f"{rel}:{file_digests[rel]}" for rel in sorted(file_digests)
    ).encode("utf-8")
    fixtures_hash = schema.sha256_bytes(digest_payload)

    manifest = {
        "schema_version": schema.SCHEMA_VERSION,
        "corpus": "write_path",
        "seed": CORPUS_SEED,
        "generator": "tests/eval/write_path/generate_corpus.py",
        "fixtures_hash": fixtures_hash,
        "files": {rel: digest for rel, digest in sorted(file_digests.items())},
    }
    outputs["_manifest.json"] = _dump_json_bytes(manifest)

    # First-run-pending baselines for BOTH extractor postures (REVIEW-FIX,
    # PR #2183 findings 1+4): main.json = product (llm) lane, m2.json =
    # deterministic echo (m2) CI lane. Each config snapshot pins its posture
    # so a run on the other lane is a config mismatch ⇒ inconclusive.
    for posture in ("llm", "m2"):
        baseline = corpus.first_run_pending_baseline(posture=posture)
        baseline["fixtures_hash"] = fixtures_hash  # computed over the SAME digests
        rel = "baselines/main.json" if posture == "llm" else "baselines/m2.json"
        outputs[rel] = _dump_json_bytes(baseline)
    return outputs


def _assert_operator_floors(outputs: dict[str, bytes]) -> None:
    """Issue #2514 floor: the corpus plants every operator kind at least once
    (SUPERSEDE/NEGATE/MITIGATES/SUPPORTS) across the seeded golds.
    """
    kinds: set[str] = set()
    total = 0
    for rel, data in outputs.items():
        if not rel.startswith("gold/") or not rel.endswith(".gold.json"):
            continue
        gold = json.loads(data)
        ops = gold.get("planted_operators") or []
        total += len(ops)
        kinds.update(op.get("expected_kind") for op in ops)
    if total < MIN_PLANTED_OPERATOR_EDGES:
        raise AssertionError(
            f"corpus has {total} planted operator edges < "
            f"{MIN_PLANTED_OPERATOR_EDGES} floor (issue #2514)"
        )
    missing = sorted(REQUIRED_OPERATOR_KINDS - kinds)
    if missing:
        raise AssertionError(
            f"corpus planted operators missing kinds {missing} — every issue-#2514 "
            "operator kind must be planted at least once"
        )


# ── Disk write + CLI ────────────────────────────────────────────────────────


def write_corpus(outputs: dict[str, bytes] | None = None, root: Path | None = None) -> list[str]:
    """Write the rendered corpus under a root dir; returns rel paths.

    ``root`` defaults to the committed write_path dir.  Tests render into a
    temp root to exercise the hash-mismatch gates without touching committed
    bytes.

    ``baselines/main.json`` + ``baselines/m2.json`` are NOT part of the
    frozen-corpus drift scope (they change legitimately when W2-b blesses a
    published run): each pending baseline is written only when the file is
    missing or still first-run-pending (empty metrics) — a PUBLISHED baseline
    (non-empty metrics, e.g. the W2-b fix-wave trail) is never clobbered by a
    generator re-run.
    """
    root = root or corpus.WRITE_PATH_DIR
    outputs = outputs or render_corpus()
    written: list[str] = []
    for rel, data in outputs.items():
        path = root / rel
        if rel.startswith("baselines/") and path.exists():
            existing = schema.read_json(path)
            if existing.get("metrics") or existing.get("history"):
                continue  # published/blessed baseline — never clobber
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        written.append(rel)
    return written


def check_drift(root: Path | None = None) -> list[str]:
    """Compare a fresh in-memory render against committed (or root) files.

    Returns the list of drifted relative paths (empty = byte-identical).
    Re-running the generator reproduces the committed corpus exactly — the
    fix-wave protocol guarantee.
    """
    root = root or corpus.WRITE_PATH_DIR
    committed = {rel: path for rel, path in _iter_committed(root)}
    fresh = render_corpus()
    drifted = []
    for rel, data in fresh.items():
        if rel.startswith("baselines/"):
            continue  # outside the frozen-corpus drift scope (see _iter_committed)
        if rel not in committed:
            drifted.append(f"{rel} (missing on disk)")
        elif committed[rel].read_bytes() != data:
            drifted.append(f"{rel} (content differs)")
    for rel in committed:
        if rel not in fresh:
            drifted.append(f"{rel} (orphan on disk)")
    return drifted


def _iter_committed(root: Path):
    """Yield the frozen-corpus JSON files under ``root``.

    Scope = fixtures + gold + ``_manifest.json`` — deliberately EXCLUDING
    ``baselines/`` (main.json + m2.json): baselines change legitimately when
    W2-b blesses a published run, so they are not part of the byte-
    idempotency guarantee (their integrity is enforced separately by
    ``validate_baseline`` and the fixtures_hash cross-check in
    ``validate_committed``).
    """
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.suffix == ".json":
            rel = path.relative_to(root).as_posix()
            if rel == "_manifest.json" or rel.startswith(("fixtures/", "gold/")):
                yield rel, path


def validate_committed(root: Path | None = None) -> list[str]:
    """Full schema validation of the committed corpus (fixture ↔ gold, hash).

    Returns the list of issues (empty = valid).  Every fixture validates; a
    ``gold`` key in a fixture is a validation error; anchors are checked
    against planted turns; filename stems must match their embedded
    ``session_id``; the manifest shape + fixtures_hash are verified.
    """
    root = root or corpus.WRITE_PATH_DIR
    issues: list[str] = []
    fixtures = {p.stem: p for p in sorted((root / "fixtures").glob("*.json"))}
    golds = {p.name.removesuffix(".gold.json"): p for p in sorted((root / "gold").glob("*.gold.json"))}
    for session_id in sorted(set(fixtures) | set(golds)):
        fixture_path = fixtures.get(session_id)
        gold_path = golds.get(session_id)
        if fixture_path is None:
            issues.append(f"missing fixture for gold session {session_id}")
            continue
        if gold_path is None:
            issues.append(f"missing gold for fixture session {session_id}")
            continue
        fixture = schema.read_json(fixture_path)
        gold = schema.read_json(gold_path)
        issues += [f"{fixture_path.name}: {issue}" for issue in schema.validate_fixture(fixture)]
        issues += [
            f"{gold_path.name}: {issue}" for issue in schema.validate_gold(gold, fixture=fixture)
        ]
        # #2514 cross-session operator anchors (an endpoint naming another
        # session) ground corpus-wide — the one-gold validator cannot see the
        # other fixture.  Run after all fixtures are loaded (below).
        # Corpus paths are keyed by file stem (corpus.session_ids, fixture_path,
        # gold_path) — the embedded session_id must match so content and paths
        # can never disagree.
        if fixture.get("session_id") != fixture_path.stem:
            issues.append(
                f"{fixture_path.name}: embedded session_id {fixture.get('session_id')!r} "
                f"!= filename stem {fixture_path.stem!r}"
            )
        gold_stem = gold_path.name[: -len(".gold.json")]
        if gold.get("session_id") != gold_stem:
            issues.append(
                f"{gold_path.name}: embedded session_id {gold.get('session_id')!r} "
                f"!= filename stem {gold_stem!r}"
            )
    # #2514 cross-session operator-anchor grounding (endpoints naming a
    # session other than the gold's own — the SUPERSEDE planted wp07 → wp06).
    fixtures_by_session = {
        sid: schema.read_json(path) for sid, path in fixtures.items()
    }
    for session_id in sorted(golds):
        gold = schema.read_json(golds[session_id])
        issues += [
            f"{golds[session_id].name}: {issue}"
            for issue in schema.validate_planted_operators_cross_session(
                gold, fixtures_by_session
            )
        ]
    # Both posture baselines validate + their fixtures_hash must match the
    # committed fixture + gold files (REVIEW-FIX: a gold-only edit
    # invalidates BOTH lanes' baselines; the m2 CI lane is not exempt).
    committed_hash = corpus.compute_fixtures_hash(root)
    for posture in ("llm", "m2"):
        rel = "baselines/main.json" if posture == "llm" else "baselines/m2.json"
        baseline = schema.read_json(root / rel)
        issues += [f"{rel}: {issue}" for issue in schema.validate_baseline(baseline)]
        if baseline.get("fixtures_hash") != committed_hash:
            issues.append(
                f"{rel} fixtures_hash does not match the committed "
                "fixture + gold files (a gold-only edit invalidates baselines)"
            )
        cfg_posture = (baseline.get("config") or {}).get("extractor_posture")
        if cfg_posture != posture:
            issues.append(
                f"{rel}: config.extractor_posture {cfg_posture!r} != file posture "
                f"{posture!r} (posture-keyed baseline file)"
            )
    manifest = corpus.load_manifest(root)
    issues += [f"_manifest.json: {issue}" for issue in schema.validate_manifest(manifest)]
    verification = corpus.verify_manifest(root)
    if not verification["ok"]:
        issues.append(
            f"_manifest.json verification failed (missing={verification['missing']}, "
            f"extra={verification['extra']}, mismatched={verification['mismatched']}, "
            f"malformed={verification['malformed']})"
        )
    # Issue #2514 floors hold on the COMMITTED files (kind coverage + edge
    # count).  The render-time assertion guards fresh renders; this re-checks
    # the on-disk corpus so a hand-edit cannot silently drop the coverage.
    kinds: set[str] = set()
    total = 0
    for session_id in sorted(golds):
        gold = schema.read_json(golds[session_id])
        ops = gold.get("planted_operators") or []
        if not isinstance(ops, list):
            continue
        total += len(ops)
        kinds.update(op.get("expected_kind") for op in ops if isinstance(op, dict))
    if total < MIN_PLANTED_OPERATOR_EDGES:
        issues.append(
            f"committed corpus has {total} planted operator edges < "
            f"{MIN_PLANTED_OPERATOR_EDGES} floor (issue #2514)"
        )
    missing_kinds = sorted(REQUIRED_OPERATOR_KINDS - kinds)
    if missing_kinds:
        issues.append(
            f"committed corpus planted operators missing kinds {missing_kinds} "
            "— every issue-#2514 operator kind must be planted at least once"
        )
    return issues


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--check" in argv:
        drifted = check_drift()
        if drifted:
            print("CORPUS DRIFT:\n  " + "\n  ".join(drifted))
            return 1
        print("corpus is byte-identical to a fresh deterministic render")
        return 0
    if "--validate" in argv:
        issues = validate_committed()
        if issues:
            print("VALIDATION ISSUES:\n  " + "\n  ".join(issues))
            return 1
        print(f"committed corpus valid ({len(AUTHORED_SESSIONS)} sessions)")
        return 0
    written = write_corpus()
    issues = validate_committed()
    if issues:
        print("GENERATOR OUTPUT FAILED VALIDATION:\n  " + "\n  ".join(issues))
        return 1
    print("wrote " + ", ".join(written))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
