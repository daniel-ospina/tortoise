---
title: Scoping — #1370 write-time subject binding for points
type: engineering
domain: capability
doc_status: live
subjects.team: epistemic-team
aboutSubjects: epistemic-team
aboutObjects: tortoise-memory-capture, aboutSubject, subject-binding, write-path
created: 2026-09-24
---

# Scoping — #1370: write-time subject binding for points

> **Issue:** #1370 · **Status:** scoped · **Owner decisions (locked 2026-08-17/18):**
> #1353 scoping **D10** (`docs/scoping/2026-08-17-1353-relationships-decoration-scoping.md`)
> + the three #1370 design comments. **Related:** #4934 (independently-filed root),
> #1418/#1466 (slot emission + schema — landed), #1417 (aboutEvent untangle — landed),
> #1353/#1376 (read-side ≤1-hop subject — landed).

## Confirmed problem (root cause, verified)

**The capture write path has no Subject-side producer, so write-time Point→Subject
binding has no target to bind to.**

1. Both capture write seams hand *every* extracted entity — including entities whose
   kind is in the declared §5 Subject vocabulary (`extractor_v2.SUBJECTS` =
   `core:organization`, `core:team`, `core:role`, `core:legalPerson`,
   `core:naturalPerson`) — to `create_entity("object", name, objectKind=<kind>)`
   (`sdk.py:4902`, `hosted_api.py:11648`). No `:Subject` is ever minted on capture
   graphs. Subject identity is **name-keyed** (`_mint_subject_stub` /
   `_upsert_subject`), not `(name, kind)`.
2. The v2 extractor's per-point participant `slots`
   (`{subject/object/event: [{name, kind, confidence}]}`, LLM confidences clamped to
   `[0,1]` by `_clean_slots`) are validated by Layer-1 (`commit_schema.py`) and then
   **dropped by the write path** — `slots` is absent from `_CAPTURE_PASSTHROUGH_PROPS`
   (`sdk.py:438-442`) and is read by no consumer in `sdk.py` / `hosted_api.py` /
   `ingest.py` / `commit_ops.py`.

**Consequence:** `aboutSubject` has zero producers on capture graphs (measured in-tree:
0 edges / 1 `:Subject` over 37,535 Points, `search_engine.py:2231-2235`), so #1353's
`subject` decoration is honestly-absent (`subject_unavailable`) and any attribution must
walk operator chains — the unreliable path D10 forbids.

**Framing rejection.** The issue body frames the root as "read-time operator-chain
resolution attaches facts to the wrong subject". That premise is **unmeasured and
currently unmeasurable** — there is no chain to walk (0 edges). It conflates *never
emitted* with *emitted then discarded*. Chain-resolution is a **symptom** of the empty
Subject layer; the write-time/fail-closed binding intent survives as the **policy layer**
that sits on top of the root fix.

**Root-cause peer.** The kind-routing defect is independently tracked as **#4934**
(OPEN, no in-flight PR — collision pre-flight run 2026-09-24). #1370's indicator 1 cannot
be satisfied without it, so this scope implements the routing **as a prerequisite** and
cross-references #4934 (its measured hosted-graph backfill of 29 existing rows is out of
scope here). No peer issue is filed.

## Locked decisions (implemented, not re-opened)

| # | Decision | Implementation |
|---|---|---|
| D10 | Bind at **write time**, never query time; `subject` read ≤1 hop, **fail-closed** | binder runs in the write seam; no read-side change (read half already landed) |
| — | **Fail-closed:** no subject > wrong subject; unsure ⇒ UNBOUND | tri-state gate; a below-band slot writes **no** edge |
| D4 | Tri-state: bound ≥ τ_hi / suspected band [τ_lo, τ_hi) / unbound < τ_lo | `DEFAULT_TAU_HI=0.7`, `DEFAULT_TAU_LO=0.4` (D4 "start ~0.6–0.7"); env-overridable, and **validated fail-closed**: finite with `0.0 < τ_hi ≤ 1.0` and `0.0 ≤ τ_lo ≤ τ_hi`; an out-of-range, inverted or ALL-ZERO (`0.0/0.0`) pair falls back to the shipped defaults |
| D6 | Entity-type-agnostic journaled binding carrying confidence; subject first | bound → `link_entity` (the shared journaled about*-edge writer), extended with `confidence`; object rides the same machinery; event slot left to #1417's content-edge contract |
| — | NIL journaled as "attempted-and-refused" — tracked, never a silent drop | refused/suspected/unbound → JSONL-only `SubjectBindingRefused` audit record |
| D2 | Kinds from the **declared** vocabulary; unknown ⇒ NIL, never invented | `is_subject_kind()` derived from `extractor_v2.SUBJECTS` (the only declared subject vocabulary — `known_kinds("subjectKind")` resolves empty, manifest v3 has no subjectKinds list). Accepts an **exact declared key** or, for a `core:` key, its **bare form**, compared against the **RAW string with no whitespace normalization**; a foreign namespace (`acme:team`), a leading-colon form (`:team`), and a case/whitespace variant (`' team '`, `'core:team\n'`) are all non-subject |
| D8 | Gate the live **fail-open** name-stub minting | the binder never mints: a slot that does not resolve to an existing node is refused (no id-less `MERGE … aboutObject` reachable from the binder) |

**D6 carrier note (deliberate, documented):** the design comment names the payload
`EntityBound {point, entity_id, entity_type, confidence}` and says "auditable, replayable".
Those properties are delivered by the **existing** `EntityLinked` record extended with an
optional `confidence` (it is the ontology-§3.2-triple-validated, JSONL-replayable
journaled-edge carrier), **not** by a parallel `EntityBound` event. Reason: a new event
type would need a deferred-fold sweep in `consistency.py` and `backup.py` — two of the four
replay consumers, and both explicitly forbidden to edit on this issue. Reusing the
existing carrier avoids a second writer of the same state. `entity_id` is dropped from the
shape because identity is name-keyed; the record carries `target_id`/`target_label`
already.

## Chosen solution

**Approach A′ — shared binder + seam-routed kind vocabulary + `EntityLinked` carry.**

- **`tortoise/subject_binding.py` (new, private helpers only)** — the single source of
  policy, including the graph-read audit and the fixture quality-gate metrics:
  - `is_subject_kind(kind)` — EXACT membership in the declared `extractor_v2.SUBJECTS`
    keys, or their bare form for `core:` keys, compared as the **raw string** (no
    whitespace normalization); a foreign/arbitrary namespace, a leading-colon form,
    a case/whitespace variant, and unknown/empty ⇒ `False` (never stripped to a
    bare declared name).
  - `decide_binding(confidence, *, tau_hi, tau_lo)` → `"bound" | "suspected" | "unbound"`
    (pure; non-numeric/None/NaN ⇒ `"unbound"`; out-of-range clamps fail-closed).
  - `resolve_thresholds` validates the resolved pair fail-closed (finite,
    `0.0 < tau_hi <= 1.0`, `0.0 <= tau_lo <= tau_hi`) so no env override can make the
    gate fail open.
  - `_resolve_target` resolves by NAME and returns only an id the `{id:...}` edge
    writer can address: an `eventId`-only/legacy stub is an **unresolved** target, not
    a phantom link.
  - `bind_point_subjects(proj, sdk, *, point_id, slots, tau_hi, tau_lo)` → per-role
    outcome. Bound → `link_entity(..., confidence=c, sdk=sdk)`; refused → journal.
    `link_entity` returns 0 both for an already-existing edge and for an absent
    endpoint pair, so a 0 result is **re-probed**: only a genuinely present edge takes
    the confidence-refresh path (and the journaled `EntityLinked`); an absent pair is
    refused (`unresolved`) and emits no record.
    This is the **shared anti-drift primitive**: both write seams call *this* one
    gated implementation (pinned by the hosted/local parity test). There is
    deliberately no batch wrapper — each seam keeps its own create-gating
    (`created_here` / the resolved id) and per-point best-effort isolation.- **`tortoise/session_link.py`** — `link_entity` gains `confidence: float | None = None`;
  when present the live MERGE `SET r.confidence = $c` and the emitted `EntityLinked`
  carries it.
- **`tortoise/projection/entities.py`** — `_fold_entity_linked_reason` reads the optional
  `confidence` and SETs it, so **live == rebuild**. (No edits to `consistency.py` /
  `backup.py` — they only buffer and forward the record type.)
- **`tortoise/sdk.py`** — `_extract_session_v2`: (i) entity loop routes subject kinds to
  `create_entity("subject", …, subjectKind=bare)`, else Object; (ii) the `about_entities`
  resolver becomes label-aware (Object **or** Subject) so no edge is silently dropped;
  (iii) the shared binder is called per point from the payload `slots`.
- **`tortoise/hosted_api.py`** — `_execute_commit_writes` step 5/6/6b: the same kind
  routing + the same shared binder call (anti-drift parity test). The binder is keyed on
  the id `create_point` **resolved** the write to (the canonical's id on a content-hash
  dedup hit), never on the payload id; a point with no resolved id is not bound
  (fail-closed) — mirrored from the local seam's `created_here` gate.
- **`tortoise/audit.py`** — untouched: the graph-read check `audit_subject_binding(graph)`
  lives in `subject_binding.py` (returning
  `{points, bound, suspected, unbound, no_slot, unbound_fraction}`), so no shared-module
  audit surface changes. No new SDK/MCP surface.
- **`tools/subject_binding_audit.py` (new, thin CLI)** — prints the fractions from a graph
  or a journal dir; computes the **misattribution rate** against a labeled fixture and the
  **threshold calibration sweep**. Imports `tortoise.subject_binding` (the audit + metrics
  functions), never re-implements queries.
- **`tests/fixtures/subject_binding_gold.jsonl` (new)** — an authored, deterministic
  ground-truth fixture: rows of `{content, subject_slots, gold_subject|null}` where
  `gold_subject=null` marks an should-stay-unbound row. It is the denominator for the
  documented misattribution rate (see "Quality gate" below).

### Rejected alternatives

- **Approach B — a new `SubjectBound` journal event with an inline self-minting fold.**
  Closest to D6's literal payload name, and refusal-as-first-class. Rejected: it is a
  larger durability surface (must be dispatched in `apply()` / `rebuild` / `rebuild_all`
  and any event-type integrity enumeration; a miss silently drops every binding on
  rebuild), and its self-minting fold duplicates `_upsert_subject` — a second writer of
  the same state. Would be better only if the existing `EntityLinked` carrier could not
  carry confidence; it can.
- **Approach C — lazy binder only, no seam routing.** Rejected: the same real-world entity
  would coexist as an `:Object` (from `entities[]`) and a `:Subject` (from a slot) under
  the same name — a name-join hazard and exactly the label leak #4934 records. Fewer files
  touched, worse data.
- **Read-time resolution / a new query-time search layer.** Rejected by D10 (owner).

## Adversarial Threat Surface

This change contains an **enforcement gate** whose correctness is "the fail-closed
contract cannot be made to fail open" (a wrong subject is worse than no subject), so the
surface is declared and the review is bounded by it.

**In scope — each class is covered by a test, with the adversarial input and the required
behaviour:**

| # | Adversarial input | Required behaviour |
|---|---|---|
| T1 | slot `confidence` below τ_lo (including `0.0`, negative, `None`, `NaN`, a string, a bool) | **no edge**; refusal journaled; never coerced upward |
| T2 | slot `kind` not in the declared subject vocabulary (invented / namespaced-unknown / pack kind / leading-colon / case variant) | treated as non-subject ⇒ **no subject edge**, never minted into `Subject`/`subjectKind`. Accepted forms are an exact declared key or its bare form for `core:` keys ONLY — an arbitrary namespace is never stripped to a bare name |
| T3 | slot `name` that does not resolve to an existing node | **no edge and no stub minted** (the D8 id-less name-stub MERGE is unreachable from the binder) |
| T4 | malformed slots payload (non-dict role value, missing/blank name, non-dict entry, wrong container type) | dropped/refused with a warning; **never raises** and never sinks the commit |
| T5 | threshold moved across a slot's confidence | outcome flips bound↔refused — the threshold is **load-bearing**, asserted by a two-value test |
| T6 | `link_entity` called with a triple the ONTOLOGY §3.2 table forbids | raises (pre-existing backstop, pinned by the drift test) |

**Explicitly out of scope:** prompt-injection of adversarial *content* into the LLM
(reduces to T1/T2/T3 — the gate acts on the slot, not on raw text); the ontology-triple
backstop itself (owned by `link_entity`); the read-side ≤1-hop decoration (owned by
#1353); the hosted-graph backfill of the 29 existing mislabelled Objects (owned by
#4934).

**Acceptance:** every declared class covered by a test + green CI. Residuals are filed,
not chased.

## Quality gate (indicator 4)

No subject ground truth exists in-tree (`tests/eval/write_path/gold/*.json` carry
claim-survival + *speaker* attribution; `schema.py::_reject_unknown_keys` forbids extra
fields). The gate therefore ships as:

1. `tests/fixtures/subject_binding_gold.jsonl` — authored labels (deterministic; no model
   call), including below-threshold and unknown-kind rows whose gold subject is `null`, and
   rows which can fail for reasons **other than τ**: a name resolving to a node that is NOT
   the gold subject, an unresolvable name whose gold is non-null, and a kind-mismatched
   target. Each row carries a `graph_nodes` mini-graph.
2. `tools/subject_binding_audit.py --gold <fixture>` — computes
   `misattribution_rate = (bound edges whose resolved subject ≠ gold) / bound edges` and
   `unbound_rate = (gold-subject rows left unbound) / gold-subject rows`, and prints a
   `--calibrate` sweep of τ_hi ∈ {0.3 … 0.9}.
3. The metric is **load-bearing**: `quality_gate_metrics` seeds a throwaway graph from every
   row's `graph_nodes` and runs the REAL resolution/decision path (`bind_point_subjects` →
   `_resolve_target`) per row, reading the resolved subject back off the graph edge — so a
   row's outcome depends on its resolution and the resolved node's stored kind, not on a
   static flag. A test asserts the shipped default thresholds produce a **documented**
   misattribution rate ≤ the committed target on that fixture, with the measured number
   (`1/13 ≈ 0.077`) in the test docstring; another test pins that flipping a row's
   `graph_nodes` changes the computed rate.
3b. `bound_fraction` is a point-level rate (`DISTINCT bound Points / all Points`), so it can
   never exceed 1.0; the raw edge count is reported separately as `bound`.
4. **Honest limitation:** the fixture measures the **policy** (threshold + fail-closed +
   resolution), not LLM extraction quality. Real per-model τ calibration (D4) requires
   model calls against a ~100–500-fact gold set and is **not covered** here — the harness
   is shipped, the calibration run is a follow-up.

## Wiring check

| Touch point | Type | Covered by | Status |
|---|---|---|---|
| Local capture seam (`_extract_session_v2`) | code | this PR | ✅ |
| Hosted commit seam (`_execute_commit_writes`) | code | this PR (shared binder) | ✅ |
| Journal replay (`EntityLinked` fold) | durability | this PR (`confidence` SET, live==rebuild) | ✅ |
| Read surface (#1353 `subject`) | read | already landed (#1376) | ✅ |
| Subject *node* producer (#4934) | code | this PR (kind routing); backfill out of scope | ✅ |
| Audit tooling | tooling | `tools/subject_binding_audit.py` + `tortoise/subject_binding.py::audit_subject_binding` | ✅ |
| CI selection | infra | `config/ci-surfaces.yml` (`core`) | ✅ |
| **Not covered:** hosted 29-row backfill | data migration | #4934 | ⚠️ |
| **Not covered:** real LLM τ calibration | measurement | follow-up | ⚠️ |

## Test plan (Class B — mechanical architecture conformance)

Every test states, in its docstring: **(1) what value makes it fail?** and **(2) does the
fixture contain a row where that value is reachable?** The cardinal tests are the negative
contract (below-threshold ⇒ UNBOUND; unknown kind ⇒ UNBOUND/no Subject; unresolvable ⇒
UNBOUND/no stub) and the threshold load-bearing test.

Layers: decision unit (pure), binder integration (FalkorDB + journal), seam integration
(capture + hosted parity), replay parity (`rebuild`), read-surface integration (search
`subject` appears / `subject_unavailable` self-clears), audit tool + quality gate.

## Post-review revisions (solution-verify cycle 1 + duplication/architecture review)

Two independent reviews of the solution diamond both raised the SAME **P0**, plus five P1s.
Revisions applied to the chosen solution (the plan doc carries the locked v2 form):

- **P0 (both reviewers) — the un-gated `about_entities` → `aboutSubject` producer.** Making
  the `about_entities` resolver label-aware (the original (ii)) would emit `aboutSubject`
  edges with NO confidence gate — bypassing the very fail-closed contract the issue exists
  for — and the read surface cannot tell them apart from binder edges. **Fixed:** the binder
  is declared the *only* `aboutSubject` producer; every `about_entities` resolver (local point
  loop, hosted point loop, hosted event loop) **skips subject-kind names entirely** (no edge,
  no stub). The legacy channel stays topic annotation (D6).
- **P1 — D8 legacy path.** `_create_about_edges` / `backfill_about_entities` remain live
  fail-open minters on the *opt-in* path (pinned by
  `tests/test_capture_entity_attachment_3664.py:1088`). The capture seam's live fail-open
  (the id-less `MERGE (p)-[:aboutObject]->(o)` name stub) IS closed for subject-kind names;
  the legacy/opt-in minters are explicitly **out of scope** (separate behavior change, not
  the live capture path). Recorded as a residual.
- **P1 — hosted loops + citation.** Fixed: the hosted point resolver is at `hosted_api.py:11703`
  (not "6b"); the hosted **event** resolver (`hosted_api.py:11489`) is a raw id-less name
  `MERGE` that would re-mint the #4934 Object leak — both now skip subject-kind names. For the
  hosted **event** path the skip is a DELIBERATE drop, not a hand-off: that loop writes
  `aboutObject` only and the gated binder reads a Point's `slots`, so nothing replaces an
  Event's subject-kind attribution. It is not replaced because the only permitted
  Event→Subject writer would have to be UN-GATED — the P0 this design closed — so an Event's
  subject-kind `about_entities` correctly produces no edge and no id-less stub. Pinned by a
  test and recorded as a residual below.
- **P1 — entity supersession for subject kinds.** `apply_supersessions` / `_fold_object_superseded`
  are `:Object`-only; routing makes a subject-kind successor a `:Subject`, so such records hit
  a (warn-grade) skip. Now an **explicit, accurately-worded** skip with a test — not a
  misleading "dangling successor".
- **P1 — `SubjectBindingRefused` durability surface.** Now registered in
  `projection/__init__.py::_NO_PROJECTION_FOLD` (the documented recognized-and-not-folded set)
  so replay does not warn per record; added to the wiring table / plan touch points.
- **P1 — `live==rebuild` scoping.** The fold must `SET r.confidence` **only when the record
  carries it** (unconditional SET would let a later no-confidence `EntityLinked` for the same
  edge clear it on replay while live keeps it — the pre-probe short-circuits). A rebuild-parity
  test pins it. The claim is scoped to journal-configured SDKs; hosted SDKs are journal-less
  and live-only (pre-existing).
- **P1 — subject vocabulary drift.** `extractor_v2.SUBJECTS` is the single declaration;
  ONTOLOGY §5 / `extractor.py` / `probe_extractor.py` include `other`. Decision: **`other` is
  deliberately NOT a bindable subject kind** (it is the fail-closed NIL bucket), pinned by a
  drift test asserting the exclusion.
- **P2 — numeric target.** The quality gate commits to **misattribution ≤ 10%** on the
  authored fixture (D4's ≤5–10% upper bound).
- **P2 — third lane.** `tools/longmem_eval/ingest_v2.py:160` has the same routing defect and
  never reads `slots`; recorded as an explicit non-goal (the quality gate uses the authored
  fixture, not the eval lane).
- **P2/P3 — rejected-alternative rationales corrected.** B's real objection is the extra
  `apply()/rebuild_all` dispatch surface + a second `:Subject` minter, not an unsupported
  "forbidden to edit" claim; C's failure is "binds nothing, or mints in violation of D8".
- **P3 — event slot.** The binder contract states `event` is explicitly not bound (test added).

### solution-verify — Cycle 1
- Verifier: P0=1 (about_entities un-gated producer), P1=4, P2=4, P3=3
- Controller: **Fixed** P0 + all P1s + the actionable P2/P3s (revised above); the D4 real-LLM
  calibration is recorded as an explicit partial (policy-only) and the #4934 backfill as out of scope.
- Re-dispatch: no re-verify cycle run — all findings were incorporated into the plan as
  *design changes* to be verified again at the implementation review (`plan-review` / `code-review`),
  which is this lane's next gate.

### duplication & architecture review (advisory) — Cycle 1
- `ISSUES` — 2 duplication (P1: hosted raw `about_entities` MERGE bypasses `link_entity`;
  subject vocabulary declared in 4 disagreeing places) + 3 architecture (P0: un-gated producer;
  P1: `SubjectBindingRefused` replay warning; P1: unscoped `live==rebuild`).
- Verdicts: 1 `unify`, 1 `keep separate`, 4 `unify-contract-keep-drivers`.
- Controller disposition: all blocking-shaped findings fixed as above. The hosted-raw-MERGE
  `unify` is **partially** taken (subject-kind names are skipped there; the Object path stays
  name-keyed — the pre-existing local/hosted writer divergence is NOT introduced by this change
  and is recorded as a residual, not silently absorbed). Vocabulary `unify-contract-keep-drivers`
  taken via the drift test against `extractor_v2.SUBJECTS`.

## Residuals (filed, not chased)

- **Hosted Event → Subject attribution is dropped.** A subject-kind name in an Event's
  `about_entities` yields no edge (and no Object stub) on the hosted commit lane: the legacy
  loop writes `aboutObject`, the gated binder reads a Point's `slots`, and the only permitted
  Event→Subject writer would be un-gated. The drop is explicit, commented, and test-pinned;
  replacing it with a gated Eventslot binder is a follow-up.
- **Hosted SDKs are journal-less** (`hosted_api._make_sdk` / `_data_sdk` set no
  `event_log_path`), so binder refusals there are live-only and `live == rebuild` does not
  apply — the same #3664 qualifier `session_link.py` carries.
- **The `D8` legacy/opt-in name-stub minters** (`_create_about_edges` /
  `backfill_about_entities`) remain live fail-open on the opt-in path (out of scope).
- **`tools/longmem_eval/ingest_v2.py`** has the same routing defect and never reads `slots`.
- **#4934's hosted 29-row backfill** — a data migration.
- **Real LLM τ calibration (D4)** — the harness ships; the calibration run is a follow-up.
- **The `already_present` path is re-probed (G1).** `link_entity` returns 0 for both an
  already-existing edge and an absent endpoint pair, so the binder re-probes the edge. A
  Point id that addresses nothing, and a Subject stub carrying only an `eventId`, are
  both refused (`unresolved`) and emit no `EntityLinked` — no phantom record a replay
  would resurrect. The hosted seam binds only the id `create_point` resolved the write to.
- **The audit CLI's unbound denominator needs a readable journal.** An existing but
  unreadable `--journal` path (a directory, a chmod-000 file) does not crash the report:
  it prints the honest UNKNOWN rather than formatting a `None` metric.

## Complexity

| Domain | Rating | Why |
|---|---|---|
| Ontology | standard | reuses `aboutSubject`; adds kind→label routing already documented in ONTOLOGY §3.2/§5 |
| Architecture | standard | one new private module, two seam call sites, one optional journal field, one audit check |
| Overall | standard | |
