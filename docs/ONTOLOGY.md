---
title: "Tortoise — Canonical Ontology v3.17"
type: data
domain: data
status: live
created: 2026-08-05
updated: 2026-09-24
ownedBy: epistemic-team
aboutSubjects: epistemic-team
aboutObjects: tortoise
doc_status: live
---

# Tortoise — Canonical Ontology v3.17

> **Status:** LIVE — canonical. Co-located with the code it governs (tortoise repo).
> **Supersedes:** ONTOLOGY_v2.5.md (eldato repo, deprecated).
>
> **⭐ This document states the model we WANT — not a status report on what is built.**
> It is the canonical target: the shape the system is being brought to. **The code
> may lag it, and implementation gaps may exist temporarily** — a field the model
> declares but nothing writes yet, a label the model retires but the code still
> emits, a rule stated but not yet enforced.
>
> **Read it as the specification, not as a description of the current build.** A
> gap between this document and the code is a **work item**, never a licence to
> treat the model as wrong or the code as authoritative — the code is what gets
> changed. Conversely, do not read a capability here as present-tense fact; where
> the status of a field or path matters, the per-field **Impl** column and the
> linked issue are the places to look.
>
> **⭐ If this document and the code disagree, THIS DOCUMENT IS RIGHT and the code
> has a defect.** The single exception is a *factual* error — the model itself
> being wrong — which is corrected here and recorded in the changelog.
>
> **Changelog v3.17 (2026-09-24 — issue #4937, the F1 ruling recorded on #2552 — MITIGATES retires from the operator menu):**
> - §2: the operator KINDS are `IMPL`/`NAND` (+ declared labels). `MITIGATES`
>   leaves the generic operator menu: a **mitigation** is not a peer operator
>   but a Point that attaches to the IMPL/NAND operator bridge it damps
>   (`(op {is_operator:true})-[:mitigated_by]->(m)`, strength ∈ [0.10, 0.50],
>   `w_eff = w × (1 − strength)`, §3.9). An operator kind cannot express that —
>   it carries no strength and no bridge.
> - Code alignment: `sdk.create_operator` refuses `op_type="MITIGATES"` with an
>   explicit error naming `mitigate_operator` (the correct path), and the
>   generic label menu is `IMPL`/`NAND` (+ pack-declared relations). The
>   extractor/commit payload spelling `MITIGATES` (target + strength) is the
>   WIRE name of a bridge-attack record and is routed to the mitigation path
>   (`commit_ops.apply_payload_operators` → `mitigate_operator`), never to
>   `create_operator`.
> - Existing data: the retired entry was the built-in operator **label**
>   exemption (`label not in ("IMPL", "NAND", "MITIGATES")`); the `op_type`
>   allowlist already excluded `MITIGATES`, so `create_operator` with
>   `op_type="MITIGATES"` never created a node. Point-in-time measurement
>   (2026-09-24, the 9 reachable fleet FalkorDB stores / 5,131 graphs):
>   `MATCH (o:Point {is_operator:true}) WHERE o.op_type='MITIGATES'` → **0
>   hits** (the live mechanism is the `mitigated_by` edges the same sweep
>   found). The label-exemption removal is **warning-only** (warn-not-block):
>   an operator carrying `label='MITIGATES'` stays legal and readable, and the
>   change only stops a NEW one from being silently exempted. So **no migration
>   runs and no data is dropped** — the refusal is loud at the write boundary,
>   and the legacy PAYLOAD spelling keeps working.
> - **OVERRIDES:** the "be liberal in what you accept" default at the write
>   boundary — a second spelling of mitigation would make "why is this weaker?"
>   answerable two ways with the strength present in only one; the menu keeps a
>   single spelling and the mitigation attaches to the bridge it damps.
>
> **Changelog v3.16 (2026-09-24 — issues #2726 + #2727, meeting source kinds +
> object-kind alignment; original branch change dated 2026-09-09, renumbered from
> v3.11 on merge because main had taken v3.11 for #3263):**
> - §4.6/§5: sourceKind vocabulary gains `meeting_transcript` (raw, first-hand)
>   and `meeting_minutes` (structured, mediated) — the two meeting-capture
>   kinds. Both register **NEUTRAL** in `SOURCE_KIND_DEFAULTS` (precedent:
>   operational captures are neutral, like the connector kinds). The #398 tier
>   question — is a raw transcript first-hand evidence and are minutes a
>   mediated summary? — is deliberately **left open**: NEUTRAL changes no EP
>   inheritance and stays reversible via `register_source_kind_default`.
> - §4.6/§5: pack-declared `extraction.sourceTypes` now validate against the
>   registered source-kind registry (`KNOWN_SOURCE_TYPES ∪
>   SOURCE_KIND_DEFAULTS`, escape hatch unioned at the check) rather than a
>   narrower hardcoded list — a kind registered at runtime is a first-class
>   sourceKind for packs too (#2726). The operational captures `agentSession`
>   and `meeting_summary` moved from `file_indexer`'s import-time block into
>   `SOURCE_KIND_DEFAULTS`, so registry membership — and therefore pack
>   validation — no longer depends on `file_indexer`'s import order.
> - §1/§4.3/§5/§6 (code alignment + subclassability): `CANONICAL_OBJECT_KINDS`
>   (`tortoise/pack_registry.py`) gains `tag` + the commitment-state family
>   (`strategy`, `plan`, `goal`, `target`) so the runtime set, the extractor's
>   `CORE_OBJECT_KEYS`, §5, §6's core-subclass table, §1's parenthetical and
>   §4.3's `objectKind` row name the same object kinds; packs may now declare
>   `subclassOf` against any of them (#2727).
>   **Count correction on merge:** §5/§6/§1/§4.3 name **16** object kinds —
>   v3.15 (#5013) retired `document` from the Object vocabulary (a document is a
>   `:Source`, §4.4) and the resolutions below follow it. The runtime constant
>   (`CANONICAL_OBJECT_KINDS` — 17 members) and `extractor_v2.CORE_OBJECT_KEYS`
>   still carry `document`, a code lag owned by #5026.
>   The `subclassOf` PascalCase shape check is scoped to allow canonical
>   lowercase object kinds — superseding the R6 §1.1 "parent must be a core
>   PascalCase kind" contract (the `packs/agent-ops` `nearMisses: [standard]`
>   workaround remains valid and need not migrate).
>   **Migration note:** the five newly canonical names can no longer be declared
>   as bare `ontology.objectKinds` (the canonical-collision guard now fires);
>   packs that declared them must express the relationship via `subclassOf`
>   instead.
> - §5 note: the legacy Phase-2 entity stage (`tortoise/extractor.py
>   _OBJECT_KIND_VOCAB`) intentionally keeps a narrower 13-kind vocab —
>   `strategy`/`plan`/`goal`/`target` are extraction surfaces of
>   `extractor_v2.CORE_OBJECT_KEYS` (state-centric), so the legacy intersection
>   filter drops them from the prompt vocab and `_normalize_object_kind`
>   collapses any that still appear to `other` by design. Note added under §5's
>   Object Kind Vocabulary; pinned in tests.
>
> **Changelog v3.15 (2026-09-24, issue #5013 — a document is a `:Source`; sources are versioned):**
> - §4.4: **rewritten.** A document is a `:Source`, not an entity. The
>   `:Document` label and `objectKind: document` are retired; **`content` leaves
>   the graph**; **`doc_status` is retired** — liveness is a read of the entities
>   extracted from the source, never a stored field. **Supersedes the "Document is
>   an Object" model** in force from v3.0 through `PR #129`.
> - §4.4 + §6: the two classification axes are **kept and kept separate** —
>   `sourceKind` (what kind of source) and `documentKind` (what genre of
>   document). Neither absorbs the other.
> - §3.2: `aboutDocument` is **kept**; its target label moves to `:Source` and
>   its replay key becomes `url`. It is **not** merged into `aboutSource` — the
>   two differ in whether `rebuild_all` pass-2b resurrects them (`#2489`).
>   `Document` is removed as an edge source from the other `about*` predicates,
>   and one example that used `uses`→`:Document` is corrected to `:Object`
>   (`uses` is declared `Event → Object`). **(Edge rows: §3.2; the summary line:
>   §3.9.)**
> - §4.6: **source versioning stated.** Identity is `url`; `contentHash`
>   identifies a **version**; raw content is **append-only**; **extraction is
>   version-scoped**; `validFrom`/`validTo`/`expiredAt` become the version's
>   window; **supersession is additive and deferred until the replacement
>   exists**. `contentHash`'s role changes from an *idempotency anchor* to a
>   **version anchor**.
> - §4.7: the Document column is removed from both matrices; its temporal slots
>   are the Source column's, which gains the version window.
> - §6/§12: `document` is removed from the Object core-subclass list; the
>   `documentKind` vocabulary is the **genre axis** over `sourceKind: document`.
> - **Precedence:** `ONTOLOGY.md` (canonical) > `#5013` (the decision) >
>   `docs/architecture/STORAGE-ARCHITECTURE.md` +
>   `docs/architecture/EXTRACTOR-V4-ARCHITECTURE.md` (PR #5016) > the
>   implementing issue.
> - **Implementation status:** the code half is not landed — `#5026` (the label
>   migration), `#5024` (the unjournalled version transition), `#5038` (the
>   version model).
>
> **Changelog v3.14 (2026-09-20, issue #4369 — the "claim" gloss is declared):**
> - §5: **"claim"** is declared as the sanctioned user-facing **gloss** for a logic-layer
>   Point (the asserted belief — the logic layer's canonical kind is `pointKind: statement`;
>   the legacy write kinds remain valid Point kinds). It is **not a distinct kind**: no `claim` type and no
>   `claim` pointKind, and no canonical node write value — the SDK's kind vocabulary is
>   deliberately open (an unrecognized kind is accepted with a warning), so this states the
>   vocabulary rule, not an enforced write rejection; word-carrying identifiers (the EP slot
>   `claim_id`, the pack-manifest `storeAs: claim` bucket) are untouched. The canonical
>   machine vocabulary is unchanged (Point / `statement`) — the declaration makes the
>   document's belief-node usages of the noun resolve against a declared term instead of an
>   accretion. **No rename** (decision option A): the ~775 `claim`/`claims` occurrences in
>   `tortoise/**/*.py` (measured 2026-09-20; identifiers and prose alike),
>   `tortoise/weights.py`'s single-source docstring and the shipped skill keep the word.
>
> **Changelog v3.13 (2026-09-18, issue #3980 — the `valid_from` kwarg precondition):**
> - §4.7/§4.1 (`validTo`): the resolution order is unchanged
>   (`valid_from` kwarg → successor `validFrom` → successor `createdAt` → `now`),
>   but the kwarg is now a **claim** rather than an unconditional input. When the
>   successor carries a stored `validFrom` the two must be **parseable**
>   timestamps naming the **same instant** (compared by instant via
>   `_created_sort_key` — the measure `restore_point_at`'s `_covers` uses), else
>   `supersede_point` raises `ValueError` **before any mutation**. The refusal
>   exists because a disagreeing kwarg silently broke contiguity in either
>   direction: an EARLIER kwarg left a **gap** (a query instant covered by
>   neither window, so `restore_point_at` reports honest absence for a period
>   that *was* covered) and a LATER kwarg left an **overlap** (two covering
>   candidates ⇒ every instant inside it reads `ambiguous`). The kwarg remains
>   the **sole** source when the successor carries no stored `validFrom` (an
>   undated successor) — that half of D2 is unchanged. The comparison keys the
>   value the write **persists**, `str(valid_from)`, because a numeric epoch
>   parses as an instant but its `str()` does not — a distinction that decides
>   whether the predecessor's `validTo` is orderable by `_covers` at all.
>   A date-only value parses as **local** midnight, so the guard's verdict for a
>   date-only-vs-offset-aware pair follows `_covers`'s own host-dependence
>   (issue **#3982**, which owns the date-only semantics decision). The guard's
>   **presence** predicate is also the read path's (`stored_vf is not None`), not
>   the resolution branch's truthiness: a falsey-but-present stored `validFrom`
>   is a real window start to `_covers` (`0` keys as the parseable epoch-0
>   instant), so a kwarg against it is refused rather than written unchecked.
>   The **no-kwarg** falsey case keeps the pre-existing truthiness fallback —
>   that residual read/write divergence is tracked in **#3985**. Deliberate
>   departure from the #1538 plan's unconditional "explicit `valid_from` kwarg
>   wins" pin — recorded on #1538.
>
> **Changelog v3.12 (2026-09-15, issue #3642 — declared temporal model):**
> - §4.7: rewritten as the canonical statement of the temporal model — two
>   orthogonal axes (valid time `validFrom`/`validTo`; transaction time
>   `createdAt`/`expiredAt`), supersession as a **third, separate fact**
>   (`supersededAt` on Object; `status='superseded'` on Point), one canonical
>   name per slot, Event's `startedAt`/`endedAt` declared a named alias of the
>   valid-time pair, and a per-type presence matrix. **Fixes the contradiction
>   with §4.5:** the old map's `createdAt` row mapped Event's record-creation to
>   `startedAt`; Event's transaction-time start is `capturedAt`.
> - §4.7 (correction): Point supersession is `status='superseded'` — the
>   `outdated` flag + `CORRECTS` edge are shared with `invalidate_point` and do
>   not distinguish the two. §4.7's `validFrom` start is ⚠️ — populated by the
>   date-carrying write paths only (hosted commit `when`; mining W-4 session
>   date); an **undated** session stamps no `validFrom` at all, so
>   **absent ⇒ open/unbounded start** (#3654 — the ingest wall clock is never
>   borrowed as a valid-time start), and `when` documented as the occurrence-date
>   input that fills `validFrom` on the commit path, not a second slot.
> - §4.7 (correction): the Document column carries explicit per-cell markers,
>   not "inherits Object" — Document inheritance of the Object column is
>   **conceptual** (`objectKind: document`); Documents carry `:Document` and not
>   `:Object`, so the Object-labelled supersession fold never reaches them and
>   supersession is unreachable for a Document.
> - §4.7/§4.1/§12 (correction): `validTo` is the successor's `validFrom` **only
>   when the successor carries one** — `supersede_point` falls back to the
>   successor's `createdAt`, then `now` (monotone, never a gap), so exact
>   contiguity holds only for a dated successor and an undated one **overlaps**
>   (the companion to the open-start ⚠️ above). The §4.7 start row no longer
>   claims `create_point`'s CREATE map writes only `createdAt`/`updatedAt` — the
>   base map seeds no `validFrom`; caller props (including `validFrom`) are
>   appended.
> - §4.1: `expiredAt` description split — transaction-time expiry (the record
>   stopped being current) is not "when a supersession/withdrawal terminated the
>   record"; supersession is a separate fact (`status='superseded'`) and both
>   can apply. Point `validFrom` split from `validTo` with `prov:generatedAtTime`
>   / `prov:invalidatedAtTime` and ⚠️ partial-coverage marking.
> - §4.2/§4.3/§4.6: `validFrom`/`validTo` + `expiredAt` declared (❌ — the model
>   is declared, not built; implementation tracked separately).
> - §4.4: Document declared to inherit the Object temporal fields conceptually
>   (no duplicate rows; no `:Object` label — see §4.7 †).
> - §4.5: `capturedAt` moved from planned(❌) to the declared transaction-time
>   start (⚠️ — written by the hosted capture/commit + session-index paths, not
>   yet on every Event write path); cross-references §4.7.
> - §12: temporal standards mapping added — `prov:generatedAtTime`/
>   `prov:invalidatedAtTime`, OWL-Time, and the Graphiti/Zep bi-temporal lineage.
> - §10.5/§3.1 (correction, sweep): the §10.5 design-decisions row still
>   described `outdated` as set only by supersession and gave a four-value
>   status list; the §3.1 `CORRECTS` row likewise called the shared edge a
>   supersession. Corrected to the explicit terminalizing writes
>   (`supersede_point` / `invalidate_point`) and §5's six-value vocabulary,
>   with `CORRECTS` framed as the shared replacement marker (§4.7 ‡).
>
> **Changelog v3.11 (2026-09-12, issue #3263 — provenance written by construction):**
> - §3.3: `extractedFrom` cardinality amended **`many→1` → `many→many`**. The
>   edge is written per source; a claim extracted from several sessions carries
>   one `extractedFrom` edge each, with no upper bound. (`create_point` now
>   infers `session:<session_id>` from the write context and accepts a sequence
>   of refs.)
> - §4.1 note: the scalar `Point.extractedFrom` node property is a query
>   convenience only — the **edges are authoritative**. It holds a string when a
>   single string is passed (including the inference path) and an **array**
>   whenever a sequence is passed, *even a one-element one*. Arrays are not
>   equality-matchable (`WHERE n.extractedFrom = '<url>'` will not hit them), so
>   exact-match callers must pass the scalar or traverse the edge.
> - §4.6: `session:<id>` Sources are minted with `sourceKind: agentSession`
>   (already the registered value — this removes the need for the in-place
>   upgrade `_materialize_session_source` performed). NOTE: `agentSession` is
>   still registered tier-neutral, so `inherited-from-source` calibration is
>   NOT yet satisfiable; assigning the tier is a calibration-policy decision,
>   tracked separately.
>
> **Changelog v3.10 (2026-09-05, #2238 dirty-hub salvage landing — Problem family):**
> - §5: registers core object kind `Problem` (deviation between actual and desired
>   state) + Problem-family note — dev `bug`/`incident` and product-strategy
>   `customerProblem` subclass it; `risk` is its potential/claim form (dev point kind).
> - §11.x (*Object Confidence — Compositional Projection*, a **proposed** section that
>   has not been added to this document): **proposed, not yet implemented** (the
>   compositional read path ships separately).
> - §1/§4.3/§6: core-subclass enumerations extended with `Problem`.
>
> **Changelog v3.9 (2026-09-02, issue #2101 / epic #2080 — §5 response-contract vocabulary, W4 why-layer DM-12):**
> - §5: new Response-Contract Vocabulary section — additive response-contract
>   labels (dig_deeper kinds/labels, why-block sections, conflict severity,
>   degraded_reason), NOT new entity kinds. Registered so the W4 why-block
>   assembly (tortoise/why.py) and the S6 contract test share one vocabulary
>   (S15 schema-correctness review prevents drift).
> - §5: conflict severity boundary pinned — `high` when the counter-claim's
>   persisted EP mean ≥ 0.6, else `medium` (repo-wide high-confidence bar,
>   analyze.py consensus pattern).
>
> **Changelog v3.8 (2026-08-13, issue #388 — connector Source nodes):**
> - §3.4: connector events (GitHub/Linear/Slack poll + webhook + entity paths)
>   now materialize Source nodes at the projection choke point (`_upsert_event`,
>   projection/entities.py) — `(Source {url})-[:references]->(Event {eventId})`
>   (+ `(Source)-[:references]->(Object {id})` on the GitHub entity path via an
>   explicit `sourceObjectId` field). Gate fires only on a registered connector
>   `sourceKind` or an explicit `sourceUrl` — never on bare `source` (mining
>   events stay excluded). `sourceKind` is set on CREATE only (#398 never-
>   overwrite contract: an existing Source's kind is authoritative on re-MERGE);
>   re-materialization does not bump `version` (idempotent re-poll).
> - §3.4/§5: `sourceKind` vocabulary gains `github_pr` (PR events — previously
>   mislabeled `github_issue`) and `linear_cycle` (cycles — previously mislabeled
>   `linear_card`); both register neutral in SOURCE_KIND_DEFAULTS (no EP
>   inheritance change).
> - §3.4: `Source.url` may be a container-scope string when no per-entity URL
>   exists (`slack:{channel}` on permalink failure, `linear:{team_key}` for
>   cycles) — deliberate non-URL fallback keying.
>
> **Changelog v3.6 (2026-08-11, epic #909 slice 3 — 13 ontology amendments, issue #948):**
> Registration only — no new design (plan §4.3, numbered exactly 13):
> 1. §4.5/§5: eventKind `AgentSession` (EXACT code spelling — capital A, sdk.py/session_indexer.py) + `sessionCaptured` declared an alias of the same concept — both remain valid kinds, no migration.
> 2. §4.5: `capturedAt` field (transaction time — bi-temporal capture).
> 3. §4.5: content-addressed Event ID (deterministic MERGE anchor for the agentSession Event).
> 4. §4.4: `story_arc` field registered (summary = short, story_arc = arc continuation).
> 5. §3.1: NAND direction policy — extraction-emitted NANDs default `unidirectional`; `bidirectional` only for explicit mutual restatement (SDK creation default stays bidirectional, #807).
> 6. §4.6/§5: `provenance_spans` Source property + `sourceKind: agentSession` value (credibility-tier inheritance keyed on sourceKind, #398).
> 7. §3.9: `mitigated_by` predicate registered (existing `mitigate_operator` edge — currently unregistered).
> 8. §3.4: `references` target extended — Source allowed (producer extension `link_source_to_entity`).
> 9. §5: `pointKind: event` registered (episodic turn Points — regex capture path, sdk.py:924).
> 10. §4.1: content-addressed Point ids (`pt_<sha>`) sanctioned as an id form (ULID preference retained).
> 11. §4.1: `c_cal` (calibrated confidence) + stored `quote` (≤200 chars provenance quote) registered.
> 12. §4.3: `passes_frequency_gate` registered on Object (S5 gate-result flag).
> 13. §4.1/§4.5/§4.6: `is_episodic` registered on Point/Event/Source — plus the `:Session` capture node (§4.5 note) (quota exemption discriminator; NOT on Object — plan §4.3 #13 entity set, review P2 PR #973).
>
> **Changelog v3.5 (2026-08-11, epic #898 — reification rule):**
> - §8: Reification rule added — an edge carries an operator only when it needs
>   mitigation (or is a Point↔Point support/contradict). Structural edges stay
>   plain and carry confidence as an edge attribute. IMPL/NAND may be direct
>   Point→Point (operator-less). Direction lives on the operator node when
>   present, else on the edge. EP note: operator-less edges read direction from
>   the edge and initialize edge messages directly.
>
> **Changelog v3.4 (2026-08-10, issue #690 — status vocabulary reconciliation):**
> - §5: Point status vocabulary upgraded from narrative note to canonical table
>   (six statuses: draft, live, retracted, superseded, outdated, archived).
>   Every status has a defined write path, allowed transitions, and EP semantics.
>   Parity decision: SDK + EventAPI + CLI share a single vocabulary
>   (`POINT_STATUS_VALUES` in `sdk.py`). `challenged` remains a derived
>   condition, not a stored status.
>
> **Changelog v3.2 (2026-08-07, issue #398 — Source credibility):**
> - §4.6/§3.4: Source `sourceKind` clarified — it is the extensible source TYPE
>   vocabulary (connectors write `github_issue`, `slack_message`, `linear_card`);
>   the T0–T4 credibility TIER is carried by `credibilityTier` (the property the
>   inheritance adapter reads). Tier-form values written into `sourceKind`
>   (`create_source(url, "T0")`) mirror to `credibilityTier` (dual-write).
> - §4.6: Source gains `reliability`/`reliabilityComponents`/`reliability_derived_at`
>   (documented derivation cache — see §11) and `sourceDate` (evidence-age clock).
> - §5: pointKind vocabulary gains `assessment` (agent source evaluations).
> - §10: recency-modulation decision log (per-field/per-sourceType decay deferred).
> > **Changelog v3.7 (2026-08-12, core hypothesis — state-centric model):**
> - §2: state-centric model block — the graph stores STATE (Objects + lifecycle
>   + derived confidence) + POINTS (the logic) + EVENTS (timeline incl.
>   decision-as-event); decisions are NOT first-class Points.
> - §5 (v3.8): extraction point kind = `statement` ONLY; `observation` removed
>   (anything can be called one) and `hypothesis` folded into confidence
>   semantics (a conjecture is a low-confidence statement) — both join the
>   legacy write kinds.
> - §5 (v3.7): Point kinds — extraction write kinds = statement/observation/hypothesis;
>   decision/vision/strategy/plan/goal/target/humanApproval/event marked LEGACY
>   (write-compat only). Object kinds gain the commitment-state family
>   (strategy/plan/goal/target). Event kinds gain `occurrence` + `turn`; the
>   decision-as-event semantics documented.
> - Pack-mapping item: product-strategy option pointKinds (useCase/userJourney/
>   jobToBeDone/valueProposition) → objectKinds.

**Convention:** camelCase throughout. `kind` = classification tag on an entity. `predicate` = named edge between entities. Capture-path/pipeline fields keep their code spelling (snake_case, or a leading-underscore internal field) — e.g. `file_hash`, `is_episodic`, `c_cal`, `passes_frequency_gate`, `provenance_spans`, `_searchText` (v3.6, #909).

---
CRITICAL RULE: no modification to this file is allowed without explicit approval of the exact changes by Daniel Ospina.

---

## §1. Entity Types

Five core types.

| # | Type | ISO/PROV Mapping | Definition | Label |
|---|------|-------------------|------------|-------|
| 1 | **Subject** | `prov:Agent` / `org:Organization` / `foaf:Person` | Any entity that can act | Who acts |
| 2 | **Object** | `prov:Entity` / `schema:Thing` | Persistent things that exist, are produced, or are acted upon | What persists |
| 3 | **Point** | `prov:Entity` (specialized) | A node in the belief graph — claims, decisions, structural artifacts (the sanctioned user-facing gloss for a belief Point is **"claim"** — §5) | What we believe |
| 4 | **Event** | `prov:Activity` (instantiated) / `schema:Event` | Temporal occurrence — the verb. Reified middle node: (Subject)-[performs]->(Event)-[produces]->(Object) | What happened |
| 5 | **Source** | `prov:Entity` (provenance) / `pav:Source` | Provenance anchor — where content was extracted from | Where it came from |

**Core subclass model (§6):** Object has core subclasses (Project, WorkItem, Problem, tag, user, skill, tool, agent, workflow, agreement, standard, other, strategy, plan, goal, target — the full §5 object-kind vocabulary). Subject has core subclasses (organization, team, role, legalPerson, naturalPerson, other). Expansion packs declare further subclasses via `subclassOf` (§9). A **document is not an Object subclass** — it is a `:Source` (§4.4); a source's kind axis is `sourceKind` and `documentKind` is its genre.

---

## §2. Four-Ontology Model

Each layer answers a different question. All four are live mechanisms.

| Layer | Question | Entity | How it works |
|-------|----------|--------|--------------|
| **Semantic** | Who/what exists? | Subject, Object, Source (**incl. documents**) | Nouns. Standing structural relations (ownedBy, memberOf, hasPart) via plain edges. |
| **Epistemic** | What do we believe and why? | Point, Operator (IMPL/NAND + label + EP confidence) | Operators connect epistemic targets (Event→Point, Point→Event, Point→Point). Belief strength = EP confidence, computed by propagation. **`MITIGATES` is not an operator kind** (#4937): a mitigation is a Point attached to the operator bridge it damps (`(op {is_operator:true})-[:mitigated_by]->(m)`, §3.9) — it weakens a relationship's relevance, it is not a peer operator. **Point→Event operators are recorded argumentation annotations — write-only in v1, no EP propagation; decision semantics remain on the Event timeline; decisions stay non-first-class Points.** |
| **Episodic** | What happened when? | Event | Verbs. Append-only, timestamped. Reified middle node: (Subject)-[performs]->(Event)-[produces]->(Object). |
| **Procedural** | What is the current state of work? | Event + folded Object status | **Object.status is a write-through cache of lifecycle events** (ObjectRegistered→live; ObjectSuperseded→superseded + `supersededBy`; connector work-item events→in_progress/completed) — the journal/event stream is the reconstruction source for `Object.status` (§11), status is a performance cache, folded keep-first per Object (divergent re-folds never blind-overwrite — #2193 resolved). |

> **State-centric model (core hypothesis, 2026-08-12 — the graph stores STATE, not decisions):**
> The record is three layers. **State** — Objects/options carry their lifecycle
> (promoted/deprecated/superseded — the Episodic layer's events are the truth;
> status is a fold cache over the events — never the truth itself) and their **confidence** (derived from the
> attached Points). **Points** — the logic: statements (pointKind `statement` —
> the only extraction point kind; hypothesis folded into confidence) connected
> to the state they argue about (aboutObject); the IMPL/NAND **operators**
> among them move the object's confidence. A **mitigation** is NOT a third
> operator kind (#4937, the F1 ruling on #2552): it is a Point that attaches
> to the operator bridge it damps — `(op {is_operator:true})-[:mitigated_by]->
> (m)` — weakening the relationship's relevance by
> `w_eff = w × (1 − strength)` (§3.9). It is therefore not a peer in the
> operator menu, and it contributes no confidence of its own. **Events** — what
> happened, for
> context: occurrences AND the **decision-as-event** (eventKind `decision`,
> aboutObject → the object(s) it resolved). The graph says *"this state is
> based on these reasons"* — never *"this decision was made because of these
> reasons"*. The decision dimension stays queryable as a timeline (events),
> but decisions are NOT first-class Points. Point kinds `decision`/`vision`/
> `strategy`/`plan`/`goal`/`target`/`observation`/`hypothesis`/`humanApproval`/
> `event` are LEGACY write kinds (§5) — extraction emits `statement` Points only,
> Event nodes, and
> lifecycle writes on Objects. State confidence is derived at
> read time from the attached Points' EP confidence (§11) — never stored
> independently on the Object.

**Structural vs Epistemic Edges**

| Edge | Type | Confidence | Example |
|------|------|-----------|---------|
| performs / produces / uses / authoredBy / ownedBy / memberOf / managedBy | **Structural** (plain) | None (factual) | (:Subject "Daniel")-[performs]->(:Event), (:Object "Customer Profile")-[ownedBy]->(:Subject "Daniel") |
| Event→Point, Point→Event, Point→Point | **Epistemic** (operator) | EP confidence | (Event:deployFailed)-[NAND]->(Point:"deploy succeeded") · (Point:"argument for X")-[IMPL]->(Event:decision-on-X) — the latter write-only in v1 (argumentation annotation; no EP propagation; the decision stays an Event, never a first-class Point) |

**Principle:** Operators connect only epistemic targets (Event→Point, Point→Event, Point→Point). Subjects connect via plain structural edges. Evaluations of subjects (expertise, reliability) are Statements (Points) with EP confidence — not edges. Reputation is derived at query time. Facts = confidence 1.0.

---

## §3. Edge Topology

### §3.1 Point ↔ Point (Epistemic — Operators)

| Predicate | From → To | Direction | Cardinality | Standard alignment | Meaning |
|-----------|-----------|-----------|-------------|--------------------|---------|
| `IMPL` | Point → Point | default bidirectional; optional unidirectional | N-ary | Epistemic (EP confidence) | A supports/implies B. Direction is an explicit operator flag — **default bidirectional**, option to declare unidirectional (source→target only). Not inferred from label. |
| `NAND` | Point → Point | default bidirectional; optional unidirectional | N-ary | Epistemic (EP confidence) | A contradicts B (logically mutual — "A and B can't both be true"). Default bidirectional; an agent may declare `unidirectional` for a directed attack (attacker's truth penalizes the target, no back-pressure — #753). **Extraction-emitted NANDs default `unidirectional`** — see extraction policy in the direction-flag note below (#909 §4.3 #5). |
| `hasPart` | Point → Point | bidirectional (composition) | N-ary | Structural via operator label | A contains B (parts/whole cascade). |
| `CORRECTS` | Point → Point | unidirectional | 1→1 | — | New point **corrects/replaces** an outdated point — the shared structural replacement marker (supersession *or* invalidation, §4.7 ‡). Marks target `outdated: true`; edge disposition is **restatement-scoped per #2421** (see the shared replacement-edge semantics below — semantic edges are triaged carry/drop/pend; v1 still transfers, the triage is pending). Created by `supersede_point` (sdk.py:4765) / `invalidate_point` (sdk.py:4654). |

> **Supersession / invalidation semantics (the shared `CORRECTS` edge):** `CORRECTS` is the structural replacement edge — both writes below create it, and only `status='superseded'` separates them (§4.7). `supersede_point(old, new)` = mark old `outdated:true` + create `(new)-[:CORRECTS]->(old)` + dispose of old's edges per the **restatement-vs-correction policy** (#2421). `invalidate_point(id, corrected_by)` = mark outdated + CORRECTS only (no edge transfer). Old point retains only the CORRECTS edge as provenance.
>
> **Structural-edge transfer is journaled + replayable (#2489):** the 2b structural-edge transfer (`extractedFrom` + snapshot-derivable `about*` edges) is journaled as flat `DirectEdgeRepoint` descriptors `{src=old_id, tgt=<replay key>, target_label, edge_type}` emitted **before** the transfer, and replayed by `rebuild_all` pass-2b (delete the pass-2 resurrection at old + create at the final successor). The journaled set is the **snapshot-derivable rel set only** — `extractedFrom`, `aboutSubject`/`aboutObject`/`aboutEvent`/`aboutDocument`/`aboutPoint` (the edges rebuild pass-2 re-creates from a point's immutable `extractedFrom` prop / `aboutEntities` list). `aboutAction` (Action dissolved in Ontology v3.0), `aboutSource`, and `wasDerivedFrom` are never snapshot-recreated → no descriptor (they never resurrect at old; the A10 raw-edge family is out of scope). Keys are label-scoped and resolved via the shared resolver in `projection/edges.py` (`stub_key`/`resolve_structural_target` — Subjects MERGE by `name`, Sources **and `aboutDocument`** by `url`; never `target.id`). **⚠️ A predicate's label and its key are one unit: a retargeted label with a stale key resolves to nothing and silently mis-points the rebuilt edge.** `aboutDocument` targets a `:Source` (§4.4), so it resolves by `url` — the same key a `:Source` resolves by. Full `about*` parity additionally depends on #2501 (create_point never live-wires `aboutEntities` — the only lane where 2b sees live `about*` edges is rebuild→supersede→rebuild). The 2b no-self-edge guard (target node == successor) emits a `delete_only` descriptor instead of a transfer. **Pre-fix journal boundary:** descriptors exist only for supersedes journaled post-deploy — a pre-fix journal rebuilt with this consumer replays with no delete-leg, so old's pass-2 resurrection persists (rebuild does NOT repair pre-existing graphs; a #2500-style backfill is out of scope).
>
> **Restatement-vs-correction policy (#2421):** two supersede cases look alike but demand opposite edge handling, so edge disposition is decided **per edge at write time** (never silently bulk-transferred):
> - **Case 1 — restatement:** the new point says the same thing more precisely (better source, fixed typo, merged duplicate). Every connection still applies; edges and belief transfer — belief-preservation is asserted (eval-spec P6.1/P6.2).
> - **Case 2 — substantive correction:** the new point exists *because* a refutation landed. The refuting NAND must **not** re-attach to the successor (it motivated the change); belief recomputes structurally with the refutation gone.
> Deterministic policy is safe only for content-independent edges (identity edges like `aboutObject` when the target is unchanged; merges carry everything). Semantic edges (IMPL/NAND) route to a carry / drop / **pend** triage with the rationale stored per edge; pended edges are non-voting placeholders surfaced by the ask lane only when an answer depends on them. A refutation that motivated a correction never re-attaches; a refutation that still applies is kept. *Implementation status:* `supersede_point` currently performs the legacy universal transfer (all operator + structural edges); the per-edge triage decomposes under #2421.

> **Direction flag (code note):** operator direction is an explicit flag on the operator Point. Creation default is **bidirectional** for all op types (#753 — NAND is logically mutual; `unidirectional` is the agent-declared directed attack). Pre-migration operators lacking the property are read as bidirectional (legacy semantics preserved).
>
> **Edge properties (IMPL/NAND — EP message state, epic 903):** these are **graph-persisted** belief-propagation messages written by `TortoiseEP._flush_cache` and read back by `_load_cache` (warm-start seed, 903-C4). They are load-bearing graph state — documented here so they are not treated as throwaway cache:
>
> | Property | Type | Written by | Meaning |
> |----------|------|-----------|---------|
> | `msg_alpha` / `msg_beta` | float | `TortoiseEP._flush_cache` (ep.py) | Forward EP message natural parameters, operator→claim slot `(op_id, claim_id, rel_type)` |
> | `back_msg_alpha` / `back_msg_beta` | float | `TortoiseEP._flush_cache` (ep.py) | Backward EP message natural parameters — separate slot for bidirectional / operator-less edges (the `back_msg_*` pair on the same edge) |
>
> **Warm-start note (903-C4):** `run(warm_start=True)` loads these graph-persisted messages as seed and skips updates whose delta ≤ fixed threshold γ; the fast path (`compute_confidence`) runs `warm_start=False` and never touches γ-skip state.
>
> **Extraction NAND direction policy (epic #909 §4.3 #5 / research addendum §1 — pipeline spec):** the EXTRACTOR explicitly sets direction per this policy; the SDK creation default stays `bidirectional` (#807 — API-user path):
> - **New-claim-attacks-existing-claim → `unidirectional`** (directed): "you now claim ¬D against D" is an attack on an existing belief — the new claim attacks the old. This is the common, measured-correct case (the one that makes contradiction surfacing work; `nand_precision` A11 measures it).
> - **Mutual restatement → `bidirectional`**: when both claims are asserted together as mutually exclusive (e.g., the conversation itself declares "A and B can't both be true").
> - **Default for extraction-emitted NANDs: `unidirectional`** — extraction is always asserting something NEW against something EXISTING; mutual is the rare explicit case.

### §3.2 Point ↔ Entity (Cross — Semantic ↔ Epistemic)

Per-type edges (chosen over single polymorphic edge — FalkorDB matrix-per-type architecture).

| Predicate | From → To | Direction | Cardinality | Standard alignment | Meaning |
|-----------|-----------|-----------|-------------|--------------------|---------|
| `aboutSubject` | Point/Event → Subject | unidirectional | many→many | `schema:about` (typed) | What Subject this describes. A source does not carry this edge — what a source's *contents* are about is carried by the nodes extracted from it |
| `aboutObject` | Point/Event/**Session** → Object | unidirectional | many→many | `schema:about` (typed) | What Object this describes. A source does not carry this edge — see the row above |
| `aboutEvent` | Point → Event | unidirectional | many→many | `schema:about` (typed) | What Event this describes. Event is a target only — Events don't describe other Events. A source does not carry this edge |
| `aboutPoint` | Event → Point | unidirectional | many→many | `schema:about` (typed) | What Point this Event describes. Event-only edge |
| `aboutDocument` | Event → **Source** | unidirectional | many→many | `schema:about` (typed) | What document this Event is about. **⚠️ It is a distinct predicate from `aboutSource` and must not be merged with it:** `aboutDocument` is in the snapshot-derivable set (`DERIVABLE_STRUCTURAL_RELS`) and is therefore **rebuilt at the old point** by `rebuild_all` pass-2b, whereas `aboutSource` is deliberately excluded and never resurrects — merging them would silently delete that rebuild guarantee (the `#2489` replay rules are in §3.1) |
| `aboutSource` | Point/Event → Source | unidirectional | many→many | `schema:about` (typed) | What Source this describes (e.g., an evaluation of a source). Creatable via `create_edge` (#391); may coexist with `extractedFrom` when the claim's text was also retrieved from that source. **⚠️ NOT snapshot-derivable by design — it gets no replay descriptor** |
| `aboutAction` | Point → Point (legacy) | unidirectional | many→many | `schema:about` (typed) | Legacy predicate retained for pre-v3.0 Action edges (Action entity dissolved in v3.0). No automatic producer — creatable only via explicit `create_edge` (#391); endpoints are whatever the resolver finds |
| `TAGGED` | Point → Tag | unidirectional | many→many | `schema:keywords` | Free-form label on a Point. Tags are `:Tag` nodes (Object subclass, `objectKind: tag`) shared across Points via MERGE. Created by hosted-api point ingestion (hosted_api.py:695-787). ⚠️ **Write-only today — no tag-filter query surfaced yet** (see follow-up). |

> **Legacy:** `aboutEntities` property → per-type `about*` edges. `_create_about_edges()` auto-detects Subject/Object from the legacy property. `schema:about` is polymorphic; we split per-type for graph performance.

> **Session as an aboutObject source (#1727 Slice 2, Task 12; SDK parity + durability #3664):** the capture path links `(s:Session)-[:aboutObject]->(o:Object)` and `(p:Point)-[:aboutObject]->(o:Object)` for episodic turn Points (the stored windowed turns — not LLM-extracted points), driven by a regex trigger over the conversation text (`github.com/{org}/{repo}/issues/{n}`, `{repo}#{n}`, bare `#n` with a false-positive guard). First-match per point is form-priority — the URL form before `{repo}#{n}` before bare `#n`, independent of textual position; all-matches for the Session node (deduped by target id). Name-suffix matches (org-ambiguous) link only when exactly ONE Object matches — zero or multiple ⇒ no-op, honest. Resolution is by the stable WorkItem Object id (`github-issue-{org}/{repo}-{n}` — computable only from the full URL form; suffix forms resolve by name), so aboutObject never dangles on supersede. Outcomes are tracked on the Session node (`entity_links_attempted` / `entity_links_created`) and journaled via a follow-up `SessionRecorded` so a wipe+replay restores them; the pass re-runs on index completion. **#3664:** the SDK `capture_session` now runs the same pass as hosted `_capture_session_impl` (it previously did not — a self-hosted capture was an unattached island). Edges the pass writes are journaled as an `EntityLinked` JSONL record (flat logical ids `{source_label, source_id, target_label, target_id, edge_type}`) **when the SDK is built with an `event_log_path`**; the record is folded idempotently by `FalkorProjection._fold_entity_linked`, and all four whole-journal replay engines (`rebuild_all`, and the `apply()`-based `rebuild`/`recover_from_log`/`backup.restore` JSONL fallback) defer it to a trailing sweep so a forward reference still folds; a link whose endpoint was hard-deleted after it is skipped (the record carries no `seq` — each engine pairs it with its journal position, which the hard-delete boundary is compared against), so a same-id re-creation does not resurrect the deleted link. A journal-less SDK — every hosted-lane SDK (`_make_sdk`/`_data_sdk` set no `event_log_path`; #4240) — emits no record and the edges stay live-only (#2296). The extractor's `(claim Point)-[:aboutObject]->(Object)` edges ride the same journal **only when the resolved Object carries an `id`**; an id-less NAME-matched stub (the hosted path's `MERGE (o:Object {name:$name})` objects) is attached by a raw name-based `MERGE` that emits no `EntityLinked`, so it is live-only and does NOT survive replay. The `:Session` node's journal carrier is `SessionRecorded` (#3664) — with a journal wired, the node (and any aboutObject edge from it) is recreated on rebuild. `aboutSubject` has no producer here: the ONTOLOGY registers Session as an `aboutObject` source only (see the §3.2 table), and the capture entity model mints Objects, not Subjects.

> **`aboutEvent` is content-only (#1417):** the edge means "this Point describes this Event" — never "this Point was produced by this Event". Capture-path provenance (a Point produced from a session/meeting) lives on the Point's **`eventId` property** (the provenance surface, stamped by `capture_session`, hosted `/v1/sessions`, and the mining path), with the full chain `(Point)-[:extractedFrom]->(Source)-[:references]->(Event)` (§3.3/§3.4). Pre-#1417 `aboutEvent`-as-provenance edges remain readable and are semantically reinterpreted (no migration); new writes must not mint them. NOTE: the D10 subject-resolution fallback (point's source-event's subject) now requires the `eventId` property — legacy aboutEvent-only points silently stop resolving that fallback (review P2).

### §3.3 Point → Source (Provenance)

| Predicate | From → To | Direction | Cardinality | Standard alignment | Meaning |
|-----------|-----------|-----------|-------------|--------------------|---------|
| `extractedFrom` | Point → Source | unidirectional | many→many | `pav:retrievedFrom` (inverse) | This claim was extracted from this source. One source backs many Points, **and one Point may be backed by several sources** (amended v3.11, #3263: the edge is written per source — a claim extracted from several sessions carries one `extractedFrom` edge each; there is no upper bound). |

### §3.4 Source → Entity (Provenance)

| Predicate | From → To | Direction | Cardinality | Standard alignment | Meaning |
|-----------|-----------|-----------|-------------|--------------------|---------|
| `references` | Source → Event/Object/Source | unidirectional | 1→many | — | The source links to / references this entity — target may be an Event, an Object, **or another Source** (producer extension in `link_source_to_entity`, #909 §4.3 #8). A document is itself a `:Source`, so it is covered by the Source target. Wired in the ingest path — the source a document arrived through is linked to it, and external artifacts referenced in a captured conversation become external Source nodes that the session Source `references` (referential chain, #909). |

`(Point)-[:extractedFrom]->(Source)-[:references]->(Entity)` — layered provenance. Source carries `sourceKind` (extensible source TYPE vocabulary — canonical list: §5 + `SOURCE_KIND_DEFAULTS`; e.g. `github_issue`, `github_pr`, `linear_card`, `linear_cycle`, `slack_message`, `document`, `agentSession`, `meeting_summary`, `meeting_transcript`, `meeting_minutes`) and `credibilityTier` (T0-T4 credibility tier — see §4.6, #398).

Connector entities (GitHub/Linear/Slack) get Source nodes at the projection choke point: `_upsert_event` (projection/entities.py) materializes `(Source {url})-[:references]->(Event {eventId})` from connector event metadata (`sourceKind` + per-entity `sourceUrl`) — #388. The gate fires only on a registered connector `sourceKind` or an explicit `sourceUrl` (never on bare `source` — mining events stay excluded); `sourceKind` is set on CREATE only — a pre-existing Source's kind is authoritative (#398 never-overwrite contract) — and re-materialization does not bump `version` (no churn on re-poll). When no per-entity URL exists, `Source.url` falls back to a container-scope string (`slack:{channel}`, `linear:{team_key}`) — the reference still resolves; the key is just coarser than a permalink. The GitHub entity path additionally wires `(Source)-[:references]->(Object {id})` via an explicit `sourceObjectId` event field (`event.object` is never used as an Object key — it is the entity title on poll/webhook paths).

### §3.5 Subject → Event → Object (Procedural)

| Predicate | From → To | Direction | Cardinality | Standard alignment | Meaning |
|-----------|-----------|-----------|-------------|--------------------|---------|
| `performs` | Subject → Event | unidirectional | N-ary | **`schema:agent` inverse** — schema.org's "direct performer or driver of the action", reversed (we go Agent→Activity) | X **did** this. The doing relation: subject executes the event. PROV has no Agent→Activity predicate (its `wasAssociatedWith` is Activity→Agent accountability); we name the performer-side verb ourselves, aligned to schema.org's performer concept. |
| `produces` | Event → **Object or Point** | unidirectional | 1→many | `schema:result` (same direction) / `prov:wasGeneratedBy` inverse | Output artifact the event created — an **Object** (a report, a PR, a build) or a **decision Point** (the #531 `humanApproval` pattern: an approval Event produces the decision Point that seeds its grounding) |
| `uses` | Event → Object | unidirectional | N-ary | **`prov:used`** (W3C: Activity→Entity, direction-identical — canonical) / `schema:instrument` for mechanisms | Input the event consumed — **including the mechanism** (skill/tool/agent/workflow Object) that produced the output |
| `wasDerivedFrom` | Object → Object | unidirectional | N-ary | `prov:wasDerivedFrom` | Entity derivation (distinct from Source provenance) |

> **One edge, two names:** `uses` (graph predicate) = `prov:used` (PROV property). Same thing — present tense in our vocabulary, past tense in PROV's. `produces` = `schema:result` (Activity→Entity, matching direction); `prov:wasGeneratedBy` names the reverse (Entity→Activity).
>
> **Mechanism provenance ("how was it produced"):** the producing mechanism is a first-class Object linked via `uses` — `(Event)-[:uses]->(Object {objectKind: skill|tool|agent|workflow})`. The mechanism is therefore searchable and shared (finite skill set, not per-event). Mechanism *specifics* (version, model, config, pipeline hash) live in the immutable event-log record, reachable via the Event's `eventId` — they are NOT materialized as per-event graph nodes (avoids O(events) node growth at scale). Full lineage: `(Point)-[:extractedFrom]->(:Source)` — the source a point was read from, which carries the `eventId` of the Event that ingested it, reaching the log record; a session Source also `references` the document Sources it contains (`Source → Source`, §3.4). (The `references` hop is wired in the ingest path for connector entities at the `_upsert_event` choke point — since #388 — see §3.4.)

### §3.6 Subject ↔ Subject (Organisational)

| Predicate | From → To | Direction | Cardinality | Standard alignment | Meaning |
|-----------|-----------|-----------|-------------|--------------------|---------|
| `participatesIn` | Subject → Event | unidirectional | N-ary | `schema:attendee` | Subjects involved in an event (⚠️ spec-only — no producer yet, tracked in issue #7884) |
| `memberOf` | Subject → Subject | unidirectional | N-ary | `org:membership` | Membership in team/group/organization. **Canonical** — generalizable to teams, orgs, any hierarchy. |
| `managedBy` | Entity → Subject | unidirectional | N-ary | RACI Responsible | Operational responsibility |
| `ownedBy` | Entity → Subject | unidirectional | N-ary | RACI Accountable | Accountability, data boundary |

> **memberOf is canonical.** `get_org_structure` queries `memberOf` for membership (Subject→Subject, member→org). `holdsRole` is retained as a distinct concept (person→role is not membership). The legacy `hasMember` predicate (org→member) remains in valid_predicates for backward compatibility with existing graph edges.

### §3.7 Object ↔ Object

| Predicate | From → To | Direction | Cardinality | Standard alignment | Meaning |
|-----------|-----------|-----------|-------------|--------------------|---------|
| `wasDerivedFrom` | Object → Object | unidirectional | N-ary | `prov:wasDerivedFrom` | Derivation |
| `hasPart` | Object → Object | bidirectional | N-ary | `dcterms:hasPart` | Composition. Inverse traversal (`<-[:hasPart]-`) covers "part of" — no separate `partOf` edge. |

### §3.8 Event Edges

| Predicate | From → To | Direction | Cardinality | Standard alignment | Meaning |
|-----------|-----------|-----------|-------------|--------------------|---------|
| `performs` (in) | Subject → Event | unidirectional | N-ary | `schema:agent` inverse | Actor — who did it |
| `produces` | Event → **Object or Point** | unidirectional | 1→many | `schema:result` | Output artifact — an Object, or a decision Point (#531) |
| `uses` | Event → Object | unidirectional | N-ary | `prov:used` | Input consumed |
| `nextEvent` | Event → Event | unidirectional | 1→1 | — | Sequencing (Graphiti NextEpisode equivalent) — planned |
| `op: IMPL/NAND` | Event → Point, Point → Event | default bidirectional; optional unidirectional | N-ary | Epistemic | Outcome influence on belief (epistemic); Point→Event direction = argumentation annotation, write-only in v1 (no EP propagation) |

> **#531 — canonical Event→Point pattern (`humanApproval`):** a human approval of a planning artifact is recorded as an Event (`eventKind: humanApproval`) + a decision Point (`pointKind: humanApproval`). The Event carries occurrence provenance (approver `performs`, artifact `uses`, claim `aboutPoint`, decision `produces`); the decision Point is a live epistemic claim that seeds the grounding a-vector and receives an EP evidence prior `Beta(10,1)` so dependent claims strengthen. Fan-out is `-[:IMPL {direction: "unidirectional", label: "approvedBy"}]->` per approved claim — deliberately unidirectional so claim weakness never back-propagates into the approval. No stored `approved` status on Objects — approval is derived from the event stream at query time. Worked example (`file_human_approval`, #531):
>
> ```
> (:Subject "Daniel")-[:performs]->(:Event {eventKind:"humanApproval", startedAt:T})
>   (:Event)-[:uses]->(:Object "Customer Profile CP-001")
>   (:Event)-[:aboutPoint]->(:Point "CP-001 targets SMB segment")
>   (:Event)-[:produces]->(:Point {pointKind:"humanApproval", content:"Approved: CP-001"})
> (:Point "Approved: CP-001")-[:IMPL {direction:unidirectional, label:"approvedBy"}]-> approved claim Points
> ```

### §3.9 Valid Predicate Vocabulary (code)

All structural edges must use one of (enforced in `_create_edge`):

```
performs, produces, uses, authoredBy, ownedBy, managedBy,
hasMember, holdsRole, memberOf, reportsTo,
participatesIn, hasPart, related, dependsOn, references,
wasDerivedFrom

> **#214 (2026-08-06):** `instantiates` removed — Event→Action legacy from v2.5;
> Action was dissolved in Ontology v3.0.
>
> **Vocabulary-only edges** (valid predicates with zero producers):
> `reportsTo` (org hierarchy, Subject→Subject), `related` (generic catch-all),
> `dependsOn` (pack-declared — dev:api dependsOn dev:database; used by `list_relations()`
> for kind expansion). All three remain valid for `create_edge()`.
```

Epistemic edges (operators): `IMPL`, `NAND` (+ semantic label).

Mitigation edge: `mitigated_by` — Point → Point (operator → mitigation Point), written by `mitigate_operator` (`TortoiseSDK.mitigate_operator`): `(op:Point {is_operator:true})-[:mitigated_by]->(m:Point)`, with the mitigation Point back-linking `-[:IMPL]->` the operator (#909 §4.3 #7 — registered).

> **Hard rule (#2315, pinned 2026-09-07):** a `mitigated_by` edge can ONLY
> originate from an `is_operator:true` Point. `mitigate_operator` is the
> SINGLE writer gate and enforces it (raises on non-operators); generic
> `create_edge` cannot write the predicate (its allowlist is the §3.9 set
> above, which excludes `mitigated_by`); rebuild/commit paths route through
> `mitigate_operator`. A raw graph write that attaches `mitigated_by` to a
> non-operator violates the ontology — no EP factor ever reads it (factor
> extraction addresses operators only), so such an edge would be dead
> structure.
>
> **Strength semantics (#2315, product decision 2026-09-07 — mitigation is a
> GRADED DAMPENER, not a refutation):** the mitigation Point's
> `mitigation_strength` property is the dampening strength in [0.10, 0.50]
> (0.10 minor caveat … 0.50 major counter-evidence = strongest; >0.50 would
> invert the claim — use NAND). EP reduces the operator's effective weight
> by `w_eff = w × (1 − strength)` in `compute_operator_weight`
> (tortoise/weights.py — single source of the convention; §8 has the
> operator-mediated (op-123) mitigation-anchor diagram showing where the
> operator mediates the IMPL/NAND edge between two claims).
> The strength is NOT fused into the mitigation point's own Beta prior
> (#2199 decision 3).

About edges: `aboutSubject`, `aboutObject`, `aboutEvent`, `aboutPoint`, `aboutDocument`, `aboutSource` (Point/Event → Source — `aboutDocument` targets `:Source`), `aboutAction` (legacy).

---

## §4. Entity Metadata

> Columns: ISO/PROV/DC = standard alignment; Impl = implemented in current code (✅ = yes, ⚠️ = partial, ❌ = spec-only). Only the live field set is listed — legacy JSONL-only fields are dropped from the ontology.

### §4.1 Point

| Field | Type | Required | ISO/PROV/DC | Impl | Meaning |
|-------|------|----------|-------------|------|---------|
| `id` | ULID | ✅ | `dc:identifier` | ✅ | Unique identifier — ULID preferred (`create_point`); content-addressed `pt_<sha>` ids are a **sanctioned id form** (deterministic — the commit endpoint's idempotency anchor, #909 §4.3 #10) |
| `content` | string | ✅ | `schema:text` | ✅ | The claim text |
| `pointKind` | string | ✅ | — | ✅ | Classification tag — extraction writes `statement` (option B); decision/vision/strategy/plan/goal/target/observation/hypothesis/humanApproval/event are legacy write kinds (§5) + pack pointKinds |
| `is_operator` | bool | — | — | ✅ | true for operator Points |
| `op_type` | string | — | — | ✅ | IMPL / NAND (operator Points only) |
| `status` | string | — | `pav:status` | ✅ | Lifecycle: draft, live, retracted, superseded, outdated, archived (#432). **challenged is a derived condition** (presence of a NAND operator edge on a live point), not a stored status (§5). draft inert for computation; retracted/superseded/outdated/archived are terminal, and the legacy `outdated=true` flag is terminal too (#2498) |
| `confidence` | float 0..1 | — | — | ⚠️ | EP posterior mean, computed by propagation |
| `c_cal` | float 0..1 | — | — | ❌ | Calibrated confidence — calibrated counterpart to the EP posterior `confidence` (registered #909 §4.3 #11; written by the calibrated pipeline, slice 5+) |
| `quote` | string ≤200 | — | — | ⚠️ | Provenance quote — the source text this claim was drawn from; payload-level metadata today (SDK extraction path / EventAPI `provenance()` payloads — extractor.py, api.py), stored Point property per #909 §4.3 #11 (secret-scanned) |
| `when` | ISO date ≤40 | — | `prov:atTime` | ⚠️ | Occurrence-time anchor — the conversation date a state-change/decision/date-bearing fact is "as of"; "" = undated (registered #1533 E1; written by extractor_v2 S5 from the session-date-anchored prompts; absent on timeless durable beliefs) |
| `authoredBy` | SubjectID | — | `dc:creator` | ✅ | Who created the claim |
| `validFrom` | ISO8601 | — | `prov:generatedAtTime` | ⚠️ | Valid-time **start** — populated by the date-carrying write paths only (the hosted commit path sets it from the payload `when`; mining W-4 from the session date). Mining W-4's undated leg (`ConversationMiner._temporal_wire`, #3654) writes **no** `validFrom` — **absent ⇒ open/unbounded start** (`restore_point_at`), never a start synthesized from the write wall clock. The `validFrom` → `createdAt` chain is a **render fallback** (`_render_date`), never a create-time stamp. §4.7 |
| `validTo` | ISO8601 | — | `prov:invalidatedAtTime` | ✅ | Valid-time **end** — `supersede_point` stamps the successor's `validFrom` **when it carries one**; an undated successor falls back to its `createdAt`, then to `now` (monotone — never a gap), so the windows are exactly contiguous **only for a dated successor**. `invalidate_point` instead stamps `validTo = now` (no successor ⇒ no contiguity), and refuses with `ValueError` when a stored `validFrom` is after `now` — `retract_point` is the window-agnostic route (#5358). A `valid_from` **kwarg** is refused when it disagrees with a successor that **carries** a stored `validFrom` (same instant required, else `ValueError` before any write — §4.7). §4.7 |
| `expiredAt` | ISO8601 | — | — | ✅ | Transaction-time expiry — **when our record stopped being current** (termination), not *why* it did. Written by both `supersede_point` (replaced by a successor) and `invalidate_point` (withdrawn) — **the timestamp alone cannot tell the two apart**. Supersession is a separate fact: Points carry it as `status='superseded'` (Point has no `supersededAt`); the `outdated` flag + `CORRECTS` edge are shared with `invalidate_point` and do **not** distinguish the two. See §4.7. |
| `createdAt` / `updatedAt` | ISO8601 | ✅ | `dc:created` / `dc:modified` | ✅ | Timestamps |
| `lastDreamedAt` | ISO8601 UTC | — | — | ✅ | Freshness stamp — timestamp of the last EP write-back that **converged** on this claim (epic 903). NULL = never dreamed — **ranks STALEST** in the stale-first scheduler (first-deploy/legacy/crash-mid-pass graphs drain across passes). Non-operator claims only (operators excluded from ranking/stamping). Written **atomically with `confidence`** in the dream write-back (single UNWIND — the write-back's own fields lastDreamedAt+updatedAt are all-or-nothing; `confidence` is also flushed independently by `ep.run`'s `_flush_cache`, per the epic plan's redundancy note); failed/non-converged runs never update it; operator-less claims get a trivial stamp via the scan path. Indexed via the plain `:Point(lastDreamedAt)` index, created idempotently at init on ALL engines — `is_operator` is never indexed (#522 embedded stale bool type table; #3154 docker/server `GRAPH.COPY` drops the `false` postings of a copied boolean RANGE index, zeroing `is_operator = false` on copies whose index set carries it, and leaving the copy destination unable to rebuild it) |
| `embedding` | vector | — | — | ✅ | Semantic embedding (FTS + vector search) |
| `speaker` | string | — | — | ✅ | Role tag on episodic turn Points (user/assistant/…) — written by SDK `capture_session` (delta 5), not by hosted capture |
| `is_episodic` | bool | — | — | ❌ | Quota exemption discriminator — true on episodic turn Points from the regex capture path (the `points` branch counts non-episodic only, #909 §4.3 #13/§4.4; legacy nodes lack the flag — one-query backfill migration ships with #947) |

### §4.2 Subject

| Field | Type | Required | ISO/PROV/DC | Impl | Meaning |
|-------|------|----------|-------------|------|---------|
| `id` | string | ✅ | `dc:identifier` | ✅ | Canonical identifier |
| `name` | string | ✅ | `foaf:name` / `schema:name` | ⚠️ | Human-readable name |
| `subjectKind` | string | ✅ | `dcterms:type` | ✅ | organization, team, role, legalPerson, naturalPerson, other |
| `status` | string | — | `pav:status` | ⚠️ | Lifecycle: draft, live, superseded, deprecated, archived — **projection-owned status is planned, NOT yet implemented on Subject**; SDK-created subjects currently carry `status: "live"` via the extra-props path (`_persist_extra_props` — `status` ∉ `_SUBJECT_HANDLED`, #2295): `_upsert_subject` itself writes only `id`/`subjectKind`/`createdAt`/`embedding` |
| `validFrom` / `validTo` | ISO8601 | — | `prov:generatedAtTime` / `prov:invalidatedAtTime` | ❌ | Valid-time window — when the Subject held in the world (declared §4.7, #3642; not yet written by `_upsert_subject`) |
| `expiredAt` | ISO8601 | — | — | ❌ | Transaction-time expiry — when our record of the Subject stopped being current (declared §4.7, #3642) |
| `createdAt` | ISO8601 | ✅ | `dc:created` | ✅ | Timestamp (set ON CREATE; adopted ON MATCH only when absent — #2295) |
| `updatedAt` | ISO8601 | — | `dc:modified` | ❌ | **Not written by `_upsert_subject`** — planned follow-up |

> **Subject registration durability (#2295)** — SDK-created Subjects (`create_entity("subject")`, the capture/entity write path) journal a `SubjectAdded` event on their FIRST canonical registration **when the SDK is built with an `event_log_path`** (probe-gated on the deterministic `sub-<sha26(name)>` id + name — a canonical re-mention after the fix never double-journals; a probe failure fails open to a replay-safe duplicate line; an EventAPI `add_subject` mention under a random ulid between SDK creates re-ids the live node and double-registers by design — `add_subject` has no canonical-id override, api.py:240-246), so SDK-created Subjects survive `rebuild_all` (the JSONL event stream is the rebuild source; replay consumers pre-date the fix because EventAPI always journaled). Byte-identity is **node-property scope**: Subject org/ownership edges ride the mirror but replay wires node properties only (EventAPI-parity). **Residual non-durable classes**: pre-#2295 journals (no backfill), unjournaled/legacy raw producers and journal-less SDK instances sharing the graph, deleted Subjects — `_delete_entity` leaves no tombstone, so a deleted Subject **resurrects** on the next rebuild and delete→recreate's replay first-wins the earlier incarnation's `createdAt`; and an EventAPI-first-then-SDK create (separate logs) replays a synthesized `createdAt` that differs from the live adopted node's pre-existing value under existing-wins (#2296 scope hook: the durability write-surface invariant must cover the delete side of the lifecycle + the Object/Subject loss backstop). EventAPI `add_subject` remains a separate unconditional producer (random-ulid, #1918). **Reserved-name narrowing**: `point`/`payload` props passed to an SDK Subject create are dropped (unconditional, both lanes — Event-precedent parity, #2061/#2194); they are `_emit_event`-reserved kwargs and must never reach the journal mirror or live node. `is_episodic` is NOT a registered Subject quota discriminator (the §4.7 cell is `—`), but the internal `create_entity(..., is_episodic=)` extras lane does round-trip it on SDK-created Subjects (pinned by the #2295 suite test 1) — same extras quirk as Object.

> **Why no `subjectStatus`?** PROV-O, W3C ORG, and FOAF model agent existence temporally (`validFrom`/`validTo`) with termination events, not status enumerations. A simple shared `status` covers "is this team active?" while Events capture the how/why of changes.

### §4.3 Object

| Field | Type | Required | ISO/PROV/DC | Impl | Meaning |
|-------|------|----------|-------------|------|---------|
| `id` | string | ✅ | `dc:identifier` | ✅ | Canonical identifier |
| `name` | string | ✅ | `schema:name` | ⚠️ | Human-readable name (`_upsert_object` writes `title`; `name` aliased) |
| `objectKind` | string | ✅ | — | ✅ | The §5 object-kind vocabulary (Project, WorkItem, Problem, tag, user, skill, tool, agent, workflow, agreement, standard, other, strategy, plan, goal, target) + pack objectKinds |
| `title` | string | — | `dc:title` | ✅ | Display title (what `_upsert_object` actually stores) |
| `status` | string | — | `pav:status` | ✅ | Write-through cache of lifecycle events (ObjectRegistered→live; ObjectSuperseded→superseded + `supersededBy` + `supersededAt`; connector work-item events→in_progress/completed) — **the event stream is the reconstruction source for `Object.status` (§11 cache doctrine); the property is a performance cache, folded keep-first per Object (divergent re-folds never blind-overwrite — #2193 resolved)** |
| `supersededAt` | ISO8601 | — | — | ✅ | The fold TIMESTAMP written by the supersession fold (`apply_supersessions` / `ObjectSuperseded` projection, pinned after the fold via `SET o.supersededAt` for byte-reproducible state headers). R17 P3-1 (#2165): `supersededAt` is the connected-assembly state-header's date source — **byte-golden renders depend on it being a pinned story date, never a wall-clock fold time** (Task-1 fixture pins `2026-09-01T00:00:00Z`). Absent on non-superseded Objects. A separate fact from `expiredAt` — "replaced by a successor" and "our record stopped being current" are independent, and both can apply (§4.7). |
| `supersededBy` | string | — | — | ✅ | Successor name written by the supersession fold (see `status`); the connected-assembly successor probe verifies a VISIBLE (non-recall-excluded) Object exists before rendering a full supersession clause — a never-created / excluded / empty successor renders a NAME-ONLY annotation, never a fabricated link (R12/C6, #2165 Task 5). |
| `validFrom` / `validTo` | ISO8601 | — | `prov:generatedAtTime` / `prov:invalidatedAtTime` | ❌ | Valid-time window — when the Object held in the world (declared §4.7, #3642; not yet written by `_upsert_object`) |
| `expiredAt` | ISO8601 | — | — | ❌ | Transaction-time expiry — when our record of the Object stopped being current. **Separate from `supersededAt`** (see that row; §4.7) |
| `createdAt` | ISO8601 | ✅ | `dc:created` | ✅ | Timestamp (set ON CREATE; adopted ON MATCH only when absent — #2194) |
| `updatedAt` | ISO8601 | — | `dc:modified` | ❌ | **Not written by `_upsert_object`** — planned follow-up |
| `passes_frequency_gate` | bool | — | — | ❌ | S5 frequency-gate result flag — false entities are still written, flagged (registered #909 §4.3 #12; planned for the capture path, slice 5+) |
> **Responsibility fields (authoredBy / ownedBy / managedBy) are EDGES, not node properties** — see §3.5-3.6. `_upsert_object` does not store them as properties; they exist as graph edges to Subject nodes.

> **Object registration durability (#2194)** — SDK-created Objects (`create_entity("object")`, the capture/entity write path) journal an `ObjectRegistered` event on their FIRST canonical registration **when the SDK is built with an `event_log_path`** (probe-gated on the deterministic `obj-<sha26(name)>` id + name — a canonical re-mention after the fix never double-journals; a probe failure fails open to a replay-safe duplicate line, and an EventAPI `add_object` mention under a non-canonical ulid followed by an SDK create of the same name double-registers by design), so capture-created Objects and their `ObjectSuperseded` folds survive `rebuild_all` (the JSONL event stream is the rebuild source). **Subjects ride the same probe-gated design since #2295 (see the §4.2 note).** Byte-identity is **node-property scope**: `authoredBy`/`ownedBy`/`managedBy` ride the mirror but replay wires node properties only — responsibility edges are not durable for Object replay (EventAPI-parity, §3.5 edges). **Residual non-durable classes** (they apply without journaling, or were never registered — dropped by rebuild without a warning unless a fold references them): pre-#2194 journals (no backfill), unjournaled/legacy raw producers and journal-less SDK instances sharing the graph, and deleted Objects — `_delete_entity` leaves no tombstone, so a deleted Object **resurrects** on the next rebuild and a delete→recreate's replay first-wins the earlier incarnation's `createdAt`/fold state over the live one (#2296 scope hook: the durability write-surface invariant must cover the delete side of the lifecycle). EventAPI `add_object` remains a separate unconditional producer. **Reserved-name narrowing**: `point`/`payload` props passed to an SDK Object create are dropped (unconditional, both lanes — Event-precedent parity, #2061); they are `_emit_event`-reserved kwargs and must never reach the journal mirror or live node. **createdAt synthesis**: first-registration Object creates synthesize `createdAt` pre-apply when the event carries none OR an explicit `None` (the is-None gate — #2309 review hardening, mirrored for Subjects in #2295) so live == journal == replay; re-mentions and journal-less SDKs keep the projection's `coalesce($now)` behavior.

### §4.4 Document — a source, not an entity

A document is a **`:Source`** (§4.6). Its bytes live **outside the graph**, reached through the source; the Points, Events and claims **extracted** from it carry the epistemic weight, and `extractedFrom` ties each extracted Point to the source it was read from.

**A source's fields and its versioning rules are in §4.6.** This section states only what is specific to a document.

**`documentKind` — the genre axis.** When `sourceKind: document`, `documentKind` names the **genre** of the document. The core vocabulary and the pack extension point are in **§5** (single home).

**`documentKind` is not `sourceKind`.** `sourceKind` answers *what kind of source this is* (`document`, `conversation`, `github_issue`, `agentSession` — the full vocabulary is in §5); `documentKind` answers *what genre of document it is*. Different questions — **neither absorbs the other**.

**Liveness is a read, not a field.** *"Is this source still good?"* is answered from the entities extracted from it — high confidence, not superseded, not under a `NAND`. **The source inherits its health from its contents**, so nothing is stored to keep in sync and nothing can drift from the graph it describes. There is no `doc_status`.

**Currency is also a read, and it needs an anchor.** Whether the extracted Points are *about the current content* is decided by the **version recorded on the extraction link** against the source's current version (§4.6) — not by any stored flag.

**`content` is not a graph property.** A document's bytes live in raw storage (D30, §2); the graph holds the entity that references them, never the text.

> **(#2726) Meeting capture — Document ↔ Source pairing (TARGET STATE, writer
> not yet wired):** once the capture/index writer emits the new kinds, a
> captured meeting will land as a Document (`documentKind: transcript` for the
> raw `meeting_transcript` capture; `documentKind: meetingNotes` for structured
> `meeting_minutes`) whose provenance Source carries the matching `sourceKind`
> (`meeting_transcript` / `meeting_minutes` — §4.6/§5). Both Source kinds
> register NEUTRAL (no credibility inheritance); the tier question is deferred
> to #398. TODAY the index/classifier path still emits `meeting_summary` for a
> classified meeting file (`CLASSIFIER_TO_SOURCE_KIND`) — wiring the two new
> kinds into that writer is deliberately out of #2726's scope (registration +
> validation only), so `meeting_summary` remains the kind actually written.

### §4.5 Event

| Field | Type | Required | ISO/PROV/DC | Impl | Meaning |
|-------|------|----------|-------------|------|---------|
| `eventId` | ULID / content-addressed | ✅ | `dc:identifier` | ✅ | Unique occurrence ID — ULID by default; the **agentSession Event uses a content-addressed form** (hash of session_id + captured_at — deterministic MERGE anchor, #909 §4.3 #3) |
| `eventKind` | string | ✅ | — | ✅ | Core vocabulary in **§5** (meeting, decision, deployment, turn, humanApproval, …) + pack eventKinds |
| `format` | string | — | `dc:format` | ✅ | Storage format (jsonl default, markdown) |
| `startedAt` / `endedAt` | ISO8601 | — | `prov:startedAtTime` / `schema:startDate` | ✅ | Valid-time extent — Event's **named alias** of `validFrom`/`validTo` (§4.7), not a distinct axis |
| `capturedAt` | ISO8601 | — | — | ⚠️ | Event's **transaction-time start** (§4.7) — when our record of the occurrence was captured, the bi-temporal complement to `startedAt`/`endedAt` (valid time). Registered #909 §4.3 #2; written on `AgentSession` (+ extracted-occurrence) Events by the hosted capture/commit endpoint and the session indexer; not yet on every Event write path |
| `started_at` | ISO date ≤40 | — | `prov:startedAtTime` | ⚠️ | **Payload-level** valid time on extracted-occurrence events (E1, #1533): the extractor emits it from the session-date anchor; graph property stays `startedAt` (coalesce `started_at → captured_at → now` server-side). Registered #1533 E1; the extractor path gates it via `_valid_iso_date` |
| `subject` | SubjectID | — | `prov:wasAssociatedWith` (inverse) | ✅ | Who performed the event (mirrors `performs` edge) |
| `object_name` / `object_type` | string | — | `prov:used` | ✅ | What was acted on / produced |
| `file_hash` | string | — | — | ✅ | SHA-256 of the file's UTF-8 text content (text-mode, universal newlines — CRLF-immune; #330/#900) |
| `is_episodic` | bool | — | — | ❌ | Quota exemption discriminator — true on capture-path episodic Events (registered #909 §4.3 #13; planned for the capture path, slice 5+) |

> **agentSession Event (#909 §4.3 #1):** the canonical eventKind for session-capture Events is **`AgentSession`** — EXACT code spelling (capital A; sdk.py `ingest_corpus`, session_indexer.py); `sessionCaptured` (the core kind still written by the regex capture path) is an **alias of the same concept** — both remain valid kinds, **no migration**. The capture graph's `:Session` node (session container, `CONTAINS` → turn Points) also carries `is_episodic: true` — the quota `sessions` branch counts `MATCH (s:Session)` (plan §4.4, slice 2).

### §4.6 Source

| Field | Type | Required | ISO/PROV/DC | Impl | Meaning |
|-------|------|----------|-------------|------|---------|
| `url` | string | ✅ | `dc:source` / `pav:retrievedFrom` | ✅ | Permalink back to original |
| `sourceKind` | string | ✅ | — | ✅ | Extensible source TYPE vocabulary — **the core list and the pack extension point are in §5** + `source_credibility.SOURCE_KIND_DEFAULTS` are canonical; e.g. github_issue, github_pr, slack_message, linear_card, linear_cycle, document, agentSession, meeting_summary, meeting_transcript, meeting_minutes. Tier-form values (T0-T4) mirror to `credibilityTier` (dual-write, #398). The meeting kinds register **NEUTRAL** (#2726) — the tier question is deliberately left open |
| `credibilityTier` | string | — | — | ✅ | T0-T4 credibility tier — the property the inheritance adapter reads (v3.2) |
| `contentHash` | string | ✅ | `premis:messageDigest` | ✅ | **Version anchor** — the digest of the content read. A differing hash on re-fetch is a **new version**, not an edit (see *Versioning* below) |
| `title` | string | — | `dc:title` | ⚠️ | Human-readable label. Defaults to url |
| `ingestedAt` | ISO8601 | ✅ | `pav:importedOn` | ✅ | When Tortoise first saw this source — **Source's spelling of the canonical transaction-time start `createdAt`** (§4.7) |
| `updatedAt` | ISO8601 | — | `dc:modified` | ✅ | Last version transition. Set **in place** on `ON MATCH` by `_upsert_source` — **unjournalled today** (`#5024`) |
| `validFrom` / `validTo` | ISO8601 | — | `prov:generatedAtTime` / `prov:invalidatedAtTime` | ❌ | **The CURRENT version's valid-time window** — when the content held in the world (declared §4.7, #3642). A prior version's window is a journal record — see *Versioning* |
| `expiredAt` | ISO8601 | — | — | ❌ | Transaction-time expiry — when our record of this version stopped being current (declared §4.7, #3642) |
| `documentKind` | string | — | `bibo:Document` subclasses | ⚠️ | **Genre**, when `sourceKind: document` — the core vocabulary is in **§5**. Distinct from `sourceKind` (§4.4) |
| `format` | string | — | `dc:format` | ✅ | Storage format (markdown, jsonl, yaml, cypher). In `_SOURCE_HANDLED` since D10 (v3.15) |
| `externalId` | string | — | `dc:identifier` (external) | ⚠️ | System-of-record ID (Slack ts, GitHub issue #) |
| `sourceDate` | ISO8601 | — | `dc:date` | ⚠️ | Evidence-age clock for recency decay (falls back to `ingestedAt` — the pipeline-arrival proxy, #398) |
| `provenance_spans` | JSON | — | — | ❌ | Window spans derived from the capture path's `provenance_refs` (plan-defined, #909 §4.3 #6; written by the capture path, slice 5+) |
| `is_episodic` | bool | — | — | ❌ | Quota exemption discriminator — true on the session Source (registered #909 §4.3 #13; planned for the capture path, slice 5+) |
| `version` | integer | ✅ | — | ✅ | **Monotonic version counter**, 1 at creation, +1 on each content-hash change. The cheap ordinal beside `contentHash`'s identity |
| `topics` | array | — | `dc:subject` | ✅ | Topic list captured from the source |
| `summary` | string | — | `dc:description` | ✅ | Summary captured from the source |
| `sessionId` | string | — | — | ✅ | The session this source arrived in |
| `eventId` | string | — | — | ✅ | The Event that ingested this source — the 1-hop audit hop to the mechanism snapshot (§3.5) |
| `story_arc` | string | — | — | ❌ | Arc continuation, for a source captured as part of a longer session narrative |
| `sourcePath` | string | — | — | ✅ | Filesystem path, for locally-ingested sources |
| `needs_extraction` | bool | — | — | ✅ | Explicit signal that this source is awaiting extraction (`--upgrade-all` discovery) |
| `_searchText` | string | — | — | ✅ | Derived full-text index field (coalesce-on-create, overwrite-on-hash-change) |
| `reliability` | float 0..1 | — | — | ⚠️ | DERIVED query-time projection (mean of the modulated Beta prior) — documented cache, never authoritative (v3.2, #398) |
| `reliabilityComponents` | JSON | — | — | ⚠️ | Cache metadata: tier, decay, factor, assessment_count, derivation time (#398) |
| `reliability_derived_at` | ISO8601 | — | — | ⚠️ | Cache freshness stamp (#398) |

**Versioning.** Identity is `url`; `contentHash` identifies a **version** of that identity. A re-fetched source whose content differs is a **new version, never an edit** — so **identity is stable and version is per-read**, and the two are never conflated.

**The version history is append-only in the journal; the graph shows the current version.** The graph is a *projection* of the journal (§3), so the Source node carries **one** version — the current one — with its `contentHash`, its `version` ordinal, and the **current** version's valid-time window (`validFrom`/`validTo`). A version transition **appends a journal record** that closes the previous version's window and records the new one; it does not rewrite an older graph node, because there is no older graph node to rewrite. **A prior version's window is a journal fact, recoverable by replay** — never a second Source node per `url`.

**What supersedes is the FACTS, not the source.** A version change makes the previously-extracted entities out-of-date; it does not create a Source-to-Source link. The successor facts attach to the standing `:Source` (`extractedFrom` is keyed by `url`, which a version change does not move), and the earlier facts are replaced through the ordinary `CORRECTS` mechanism (§4.7 ‡). **The source is the identity; the entities are the belief.**

**Extraction is version-scoped — the link carries the version read.** Every **Point** derived from a source records **`sourceVersion`** — the `contentHash` of the version it was read from — on its `extractedFrom` link (declared `Point → Source`, §3.3). Currency is then a comparison of that recorded value against the source's current `contentHash`: **a read, never a stored flag** (§4.4), and unanswerable without the version on the link. Derived Events and Objects reach their source through their own links rather than `extractedFrom`, so **the version anchor is a Point-level guarantee** — a class whose provenance does not pass through an `extractedFrom` link is not version-scoped today.

**A Point with several sources is stale when ANY of its links is.** `extractedFrom` is many→many (§3.3), so a Point read from more than one source carries a `sourceVersion` **per link**; the Point is **stale if any** of those links is behind its source's current version, and **current only when every** link is. Partial re-extraction therefore surfaces as staleness rather than passing silently.

**Stale is a read, and `stale` is not `wrong`.** A **Point** is **stale** when its recorded `sourceVersion` differs from its source's current `contentHash` — a **derived predicate**, not a stored status, so nothing can drift. A stale entity **was true of the content that was read** and **remains standing**: supersession is **deferred until the replacement exists**, and when re-inference produces the successor the stale entity is superseded through `CORRECTS`. **Superseded, never deleted** — the previous belief stays queryable alongside the new one. This is the deliberate policy: **a stale belief is strictly better than no belief**, because immediate removal would leave the graph asserting nothing about a subject it previously had a position on.

**Closing the interval is part of the write.** An unclosed `validTo` reads as *"still true"* indefinitely — the failure mode the whole model exists to prevent — so the journal record that opens a new version closes the old one **in the same act**; it is never a later repair.

**`updatedAt` records the last version transition.** It is not a currency flag — currency is computed from `sourceVersion` against `contentHash`.

### §4.7 Temporal Model (canonical)

**Every entity answers two orthogonal temporal questions, and supersession is a
third, separate fact. One canonical name per slot (issue #3642).** This section
is authoritative — never infer a temporal slot from a field's spelling.

**Axis 1 — Valid time: "when did this hold in the world?"** The fact's own
window, independent of when Tortoise learned it. Canonical pair:
`validFrom` / `validTo`.

| Slot | Canonical name | Standard | Notes |
|------|----------------|----------|-------|
| start | `validFrom` | `prov:generatedAtTime` | Populated by the date-carrying write paths — the hosted commit path sets it from the payload `when`, mining W-4 from the session frontmatter date (§4.1). An undated session stamps **no** `validFrom` at all (`ConversationMiner._temporal_wire`, #3654), so an **absent** `validFrom` means an **open/unbounded start** (`restore_point_at`) — the write wall clock is never borrowed as a start. `create_point`'s base CREATE map seeds no `validFrom`; caller props — including `validFrom` — are appended to it, so `create_point` itself never **synthesizes** a clock-stamped start either. The `validFrom` → `createdAt` chain is a **render fallback** (`_render_date`), never a create-time stamp |
| end | `validTo` | `prov:invalidatedAtTime` | On **supersession** set to the successor's `validFrom` **when the successor carries one** — the **contiguous Graphiti (Zep) window intent: the old fact stops being true when the new one starts being true** (`supersede_point`, E6 #1538). An **undated** successor (absent `validFrom` ⇒ open start, row above) falls back to its `createdAt`, then to `now` — so the old `validTo` lands on the successor's `createdAt` and the windows **overlap** rather than being exactly contiguous. Exact contiguity requires a successor `validFrom` (`valid_from` kwarg → successor `validFrom` → successor `createdAt` → `now`). The kwarg is a **claim**, not an unconditional override: when the successor carries a stored `validFrom` the two must be **parseable** timestamps naming the **same instant** (compared by instant via `_created_sort_key`, the measure `_covers` uses), else `supersede_point` raises `ValueError` **before any mutation** — a disagreeing kwarg would otherwise gap or overlap the chain (#3980). The kwarg stays the **sole** source for an undated successor. `invalidate_point` instead stamps `validTo = now` (no successor ⇒ no contiguity), and refuses with `ValueError` when a stored `validFrom` is after `now` — `retract_point` is the window-agnostic route (#5358) |

> **Point's `when` is not a second valid-time slot.** `when` (§4.1) is the
> **occurrence-date input** — the payload-level anchor the hosted commit path
> copies verbatim into `validFrom` on the same node
> (`point_props["validFrom"] = pr.point.when`, hosted_api.py:9461-9484).
> It is the same value under the §4.1 spelling, not a second slot — `validFrom` and the payload's occurrence date are **one slot under two spellings**, which is why they grouped together in the field map.

**Axis 2 — Transaction time: "when did our record of it exist?"** The
graph-write clock. Canonical pair: `createdAt` / `expiredAt`.

| Slot | Canonical name | Standard | Notes |
|------|----------------|----------|-------|
| start | `createdAt` | graph-write clock (not a PROV time property) | `ingestedAt` is **Source's** spelling of this slot; `capturedAt` is **Event's** — both are this slot, never a second axis |
| end | `expiredAt` | graph-write clock (not a PROV time property) | When our **record** stopped being current (termination), not *why* it did |

**Event's named alias (deliberate — do NOT rename).** Event spells the
valid-time pair `startedAt` / `endedAt`: for an occurrence, "started/ended" is
the natural reading. It is **the same concept as `validFrom`/`validTo`** — an
explicit alias, mapped to OWL-Time and `prov:startedAtTime` (§12) — not a third
axis. Event's transaction-time start is `capturedAt` (§4.5); Event declares no
transaction-time end.

**Supersession — a third, separate fact.** "When was it replaced?" (implies a
successor) is **not** the expiry axis. `supersededAt` (Object) is its own
concept and **coexists with `expiredAt`** — an Object can be superseded at T1
and expire at T2, so both apply and neither implies the other. Points record
the same fact differently: `status='superseded'` (set by `supersede_point`,
never by `invalidate_point`), **not** `supersededAt`. The `outdated` flag and
the `CORRECTS` edge are shared with `invalidate_point` — they do **not**
identify a supersession on their own, and a reader following them alone
classifies every invalidated Point as superseded (§4.1, §3.1, §5).

**Per-type presence (canonical matrix)** — ✅ implemented · ⚠️ partial · ❌
declared, not built (implementation is tracked separately):

| Slot | Point | Subject | Object | Event | Source |
|------|-------|---------|--------|-------|--------|
| valid start | `validFrom` ⚠️ | `validFrom` ❌ | `validFrom` ❌ | `startedAt` ✅ (alias) | `validFrom` ❌ |
| valid end | `validTo` ✅ | `validTo` ❌ | `validTo` ❌ | `endedAt` ✅ (alias) | `validTo` ❌ |
| txn start | `createdAt` ✅ | `createdAt` ✅ | `createdAt` ✅ | `capturedAt` ⚠️ | `ingestedAt` ✅ |
| txn end | `expiredAt` ✅ | `expiredAt` ❌ | `expiredAt` ❌ | — | `expiredAt` ❌ |
| supersession | `status='superseded'` ✅ ‡ | — | `supersededAt` ✅ | — | — |

> † **A source's temporal slots are the Source column's.** No `:Object`-labelled write path reaches a Source, so the Object-labelled supersession fold (`_fold_object_superseded` / `apply_supersessions`, which `MATCH`es `(o:Object {id|name})`) **cannot stamp a Source** — **Source supersession is unreachable, not merely unimplemented (`—`).** `_upsert_source` writes no `validFrom`/`validTo`/`expiredAt`. **⚠️ A re-fetched source whose content changed currently mutates in place (`updatedAt`, `version`) with no journal record (`#5024`).**

> **Point valid start is ⚠️, not ✅** — populated by the date-carrying write
> paths only; an undated session (mining W-4) stamps no `validFrom` at all
> (#3654), so an absent `validFrom` is an **open start** and the write wall
> clock is never borrowed as a start. `validFrom` → `createdAt`
> is a *render* fallback (`_render_date`), not a stamp. The open start is why the
> supersession **end** is conditional too: an undated successor contributes its
> `createdAt` (fallback chain above), not a `validFrom`, so its overlap with the
> old window is the open start's consequence — never read contiguity as
> unconditional. This matches the ⚠️ convention `capturedAt` follows; §4.1 marks
> `validFrom` and `validTo` separately.

> ‡ **Point supersession discriminator.** `supersede_point` and
> `invalidate_point` write the **same** four markers — `outdated=true`,
> `validTo`, `expiredAt`, and a `CORRECTS` edge. Only `status='superseded'`
> separates them (`supersede_point` sets it; `invalidate_point` never does) —
> §5's status vocabulary and §3.1 encode the same split. The `outdated` +
> `CORRECTS` pair alone classifies every invalidated Point as superseded.

**Cross-Entity Field Map** (non-temporal fields; the matrix above is
authoritative for the temporal slots. Event's transaction-time start is
`capturedAt`; `startedAt` is its occurrence-time start.)

| Field | Point | Subject | Object | Event | Source |
|-------|-------|---------|--------|-------|--------|
| `id` | ✅ | ✅ | ✅ | ✅ eventId | ✅ url |
| kind tag | pointKind | subjectKind | objectKind | eventKind | sourceKind **(+ `documentKind` — the GENRE axis, D10/Q2)** |
| name/title | — | name | name | — | title |
| updatedAt | ✅ | ❌ | ❌ | — | ✅ |
| status | status | ⚠️ SDK extra-prop (projection-owned planned, #2295) | ✅ (fold cache — event-derived) | — | — |
| responsibility | authoredBy | — | edge (§3.5) | — | — |
| ownership | — | — | edge (§3.5) | — | — |
| management | — | — | edge (§3.5) | — | — |
| format | — | — | — | format | **✅ `format` belongs here** — moved into `_SOURCE_HANDLED` by D10 (v3.15) |
| aboutEdges | ✅ | — | ✅ | ✅ | — |
| occurrence date | `when` (→ `validFrom`, §4.7) | — | — | — | — |
| is_episodic | ❌ | — | — | ❌ | ❌ |
| passes_frequency_gate | — | — | ❌ | — | — |

---

## §5. Core Kind Vocabulary

### Point Kind Vocabulary (core)

```
statement    # the LOGIC layer — THE extraction write kind (state-centric, option B 2026-08-12)
decision, vision, strategy, plan, goal, target, observation, hypothesis, humanApproval, event   # LEGACY write kinds (write-compat only)
```
> **State-centric alignment (2026-08-12, option B):** Points are the LOGIC layer
> only, and the logic is one kind: **`statement`** — the asserted belief.
> `hypothesis` is FOLDED INTO CONFIDENCE semantics (a conjecture is a
> low-confidence statement); `observation` is removed (anything can be called
> one). `decision`/`humanApproval` are TIMELINE kinds → Event nodes (eventKind
> `decision`/`humanApproval`); `vision`/`strategy`/`plan`/`goal`/`target` are
> STATE kinds → Object kinds (commitment-state family, below); `event` is
> removed (issue #1013 — episodic records are Event nodes with eventKind
> `occurrence`/`turn`). The legacy kinds remain valid write kinds for
> compatibility; extraction emits `statement` only.

> **Sanctioned gloss — "claim" (#4369).** Where **"claim"** names a belief node, it is a
> **logic-layer Point** — the asserted belief (the logic layer's canonical kind is
> `pointKind: statement`; the legacy write kinds remain valid Point kinds for write-compat,
> above). "Claim" is the sanctioned plain-English gloss for that node, used where "Point"
> would read as internal jargon to a customer; per option A a belief-node use of the noun is
> correct in canonical prose too, and the machine vocabulary is unchanged
> (Point / `statement`).
> **It is not a distinct kind:** there is no `claim` type and no `claim` pointKind, and
> `claim` is never a **canonical** node write value — the SDK's kind vocabulary is
> deliberately open (an unrecognized kind is accepted with a warning), so this is a
> vocabulary rule, not an enforced write rejection. Word-carrying identifiers are untouched —
> the EP slot `claim_id` (§3) and the pack-manifest `storeAs: claim` stream bucket are not
> node kinds; the bucket is a manifest label.

### Object Kind Vocabulary (core)

```
Project, WorkItem, Problem, tag, user, skill, tool, agent, workflow, agreement, standard, other,
strategy, plan, goal, target    # commitment-state family (state-centric, 2026-08-12) — states that
                                # commitments produce; carry lifecycle + derived confidence
```
> **Legacy extraction path (pinned, #2727):** the Phase-2 entity stage in
> `tortoise/extractor.py` (`_OBJECT_KIND_VOCAB`) intentionally supports a
> narrower 12-kind subset only — it omits the four commitment-state kinds
> (`strategy`/`plan`/`goal`/`target`), which are extraction surfaces of
> `extractor_v2.CORE_OBJECT_KEYS` (state-centric); `_intersect_object_kinds`
> drops them from the prompt vocabulary and `_normalize_object_kind` collapses
> any that still appear to `other`. The test
> `test_legacy_extractor_vocab_is_a_documented_subset` pins exactly that gap.
> **Problem family (2026-08-31):** `Problem` = a deviation between actual and desired
> state (anchored in a core `standard`, SLO/target, or declared need). The problem-family
> parent: packs subclass it (dev:`bug` — code deviates from expected behavior,
> dev:`incident` — a service deviates from its SLO/standard, product-strategy:
> `customerProblem` — an unmet customer need) and `risk` is its potential form — a dev
> `risk` POINT (claim) that a problem may occur; when the problem materializes, the
> claim point is superseded/retracted and the materialized `bug`/`incident` Object
> carries the state (Point supersession is Point→Point; Object creation is separate).
> **State-centric alignment (2026-08-12):** `strategy`/`plan`/`goal`/`target`
> are STATE objects (superseded when a new commitment lands — the old strategy
> is deprecated, the new one promoted). Pack pointKinds used as options
> (product-strategy: useCase, userJourney, jobToBeDone, valueProposition) are
> OPTION/STATE kinds — pack-mapping item: promote to objectKinds (near-miss
> convention until the pack amendment).

### Event Kind Vocabulary (core)

```
meeting, decision, experiment, deployment, review, friction, extraction,
documentCreated, roleCreated, pointAdded, sessionCaptured, AgentSession, humanApproval,  # #531
occurrence, turn    # state-centric (2026-08-12): occurrence = generic extracted occurrence;
                    # turn = capture turn records (replaces pointKind 'event', issue #1013)
```
> **State-centric alignment (2026-08-12):** eventKind `decision` is the
> TIMELINE record of a commitment (the resolution is expressed as lifecycle
> writes on the state objects); `occurrence` covers extracted happenings;
> `turn` covers capture turn records. The Episodic layer is the truth for
> lifecycle: Object status is a fold cache over its event stream — never authoritative on its own (§2).

> **#909 §4.3 #1:** `AgentSession` (EXACT code spelling — capital A; sdk.py `ingest_corpus`/session_indexer.py) is the canonical kind for session-capture Events; `sessionCaptured` (the core kind written by the regex capture path) is an **alias of the same concept** — both remain valid kinds, **no migration**.

### Document Kind Vocabulary — the GENRE axis (core)

> This is a **`documentKind`** vocabulary — the **GENRE** of a document, applied to a `:Source` whose `sourceKind` is `document`. It is **not** an Object subclass vocabulary, and it is **not** the same axis as `sourceKind`.

```
research, reflectPostmortem, strategyDoc, visionDoc, planDoc, decisionDoc,
meetingNotes, experimentResults, evidenceLog, handoff, transcript, roadmap, brief
```

### Subject Kind Vocabulary (core)

```
organization, team, role, legalPerson, naturalPerson, other
```

> **Account layer vs in-graph Subjects (#2311):** the `organization` / `team` kinds above (and their §6 subclasses) are **in-graph Subjects inside a memory** — semantically distinct from the control-plane account unit that owns the graph(s), the **organization account** (billing/tenure; legacy code/API/DB identifiers still read "team"). Subject kinds are not renamed by #2311. Definitions note: docs/registry-graph-schema.md ("Definitions — account layer vs in-graph Subjects").

### Source Type Vocabulary (`sourceKind`) + Credibility Tier

**The canonical `sourceKind` values** — what kind of source this is. The vocabulary is extensible: **core values are here**, pack kinds are declared in the pack manifests (§9) and registered at load time.

```
conversation, document, agentSession,
github_issue, github_pr, slack_message, linear_card, linear_cycle
```

**Credibility tiers** — a *different* axis, carried on `credibilityTier`:

```
T0 (meta-analysis), T1 (peer-reviewed), T2 (expert), T3 (anecdotal), T4 (unverified)
```

> **v3.2 (#398):** `sourceKind` is the extensible source TYPE vocabulary — pack-declared kinds (github_issue, github_pr, linear_card, linear_cycle, slack_message, document, meeting_transcript, meeting_minutes...) resolve to a tier ONLY via explicit registration (`register_source_kind_default`) or an explicit `credibilityTier` assignment; **unknown kinds stay neutral** (no inheritance). Connector kinds register explicitly neutral in `SOURCE_KIND_DEFAULTS` (`source_credibility.py`) — connector Source materialization (#388) therefore never alters EP inheritance. The T0–T4 tier semantics above live on `credibilityTier`. The Beta-prior mapping (T0=(10,1), T1=(5,1), T2=(3,1), T3=(2,1), T4=(1.1,1)) is the validated model (docs/ep-source-credibility-experiment.md §1.1).

> **Expansion-pack kinds live in the packs, not here.** Pack-declared kinds (dev:epic, product-strategy:product, etc.) are defined in their pack manifests (§9) and registered at load time via the pack registry. This file documents only the core vocabulary; it is not the home for pack kinds.

> **(#2726) Meeting-capture kinds:** `meeting_transcript` (raw, first-hand) and
> `meeting_minutes` (structured, mediated) are registered source kinds as of
> v3.16, both **NEUTRAL** (`None`) — operational captures sit outside the
> research-evidence ladder, so they inherit no tier. The tier question (whether
> a raw transcript warrants a first-hand tier and minutes a mediated one) is
> deliberately deferred to #398's mechanism; `register_source_kind_default`
> makes the eventual choice a one-line, reversible registration. Pack manifests
> may declare both in `extraction.sourceTypes` (the validator unions the registry).

> **#909 §4.3 #6:** `sourceKind: agentSession` is a registered source-type VALUE (the four-node capture model's session Source — the provenance bridge — carries it; the value belongs to the `sourceKind` vocabulary above, alongside github_issue/slack_message/linear_card/…). Credibility-tier inheritance is keyed on **sourceKind** (#398): the tier resolves via the kind's registered tier default (`register_source_kind_default`) or an explicit `credibilityTier` assignment; unregistered kinds stay neutral (no inheritance).

### Response-Contract Vocabulary (W4 why-layer, #2101 / epic #2080 DM-12)

Additive response-contract labels — NOT new entity kinds. Registered so the
W4 why-block assembly and the S6 contract test share one vocabulary (vocabulary
drift is prevented by the S15 schema-correctness review).

```
dig_deeper kinds   supports | nand | superseded | tradeoff     # dig_deeper[k].kind (deterministic labels, never LLM prose)
dig_deeper labels  read supports · read the counterargument (NAND)
                   · see what changed · weigh the alternatives  # derived from kind + target verb phrases (UXD 4)
why-block sections why · conflicts · supersession · tradeoffs · dig_deeper · warnings   # enriched-item additive keys (W4 why-layer spec §3.1.1/§6.1)
conflict severity  high | medium                                # deterministic from the counter-claim's persisted EP mean
                                                               # high ⟺ mean ≥ 0.6 (repo high-confidence bar); else medium
degraded_reason    timeout | assembly_error | breaker_open      # degradations only; clean empty = null + empty arrays (W4 why-layer spec §3.1.3)
```

### Point Status Vocabulary (canonical, #432/#690)

| Status | Kind | Write path | Transitions to | Notes |
|--------|------|------------|----------------|-------|
| `draft` | initial | `create_point`, `EventAPI._point` | `live` | Inert for EP computation; promoted on first operator edge |
| `live` | active | `create_operator` (auto-promote source), `update_point` (status='live') | `retracted`, `superseded` | Full EP participation |
| `retracted` | terminal | `retract_point`, `EventAPI.retract_point` | *(none)* | Tombstone — stays in graph, `get_point` returns, `query`/`paginated_query` exclude by default |
| `superseded` | terminal | `supersede_point` (sets alongside `outdated:true`) | *(none)* | Structural replacement via CORRECTS edge + **restatement-scoped edge disposition** (#2421 — semantic edges triaged per-edge, not bulk-transferred) |
| `outdated` | terminal (legacy flag) | `invalidate_point`, `supersede_point` (legacy flag) | *(none)* | Back-compat boolean; co-exists with `status`. Terminal on every read surface and for every lifecycle transition (#2498) — the pre-#2498 `→ retracted` allowance let a dead claim be re-terminalized |
| `archived` | terminal (reserved) | *(no v1 SDK write path)* | *(none)* | Reserved for future lifecycle operations |

> **`challenged` is NOT a state** — it is a DERIVED condition emerging from the presence of a NAND operator edge on a live point, queryable as such:
> ```cypher
> MATCH (p:Point {status:'live'})<-[:NAND]-(:Point {is_operator:true}) RETURN p
> ```
>
> **Tombstone contract:** Retracted points stay in the graph (`get_point` returns them with `status='retracted'`). Default query surfaces (`query`, `paginated_query`) exclude them; pass `include_retracted=True` or an explicit `status='retracted'` filter to surface them. Deletion via `delete_point` hard-deletes (no tombstone).
>
> **Parity decision (#690):** SDK + EventAPI + CLI share a single status vocabulary (`POINT_STATUS_VALUES` in `sdk.py`). EventAPI births `draft` (same as SDK); CLI backfill promotes NULL-status legacy points to `live` (migration-only, not a drift). The `:GraphEvent` label is RESERVED for the #432 change-log stream (`{seq, ts, type, payload, event_id}`, zero relationships — graph islands) — distinct from the `:Event` ontology entity with `eventId` (§3.4). See docs/event-catalog.md.

---

## §6. Subclass Model

Packs declare subclasses of core kinds via manifest `subclassOf`:

```yaml
objectKinds:
  - epic
  - issue
subclassOf:
  epic: Project
  issue: WorkItem
```

At query time, `expand_kind("Project")` returns `["Project", "dev:epic"]`. Queries filter by `pointKind IN [...expanded...]`.

**Core subclasses (no pack needed):**

| Parent | Core subclasses |
|--------|-----------------|
| Object | Project, WorkItem, Problem, tag, user, skill, tool, agent, workflow, agreement, standard, other, strategy, plan, goal, target |
| Document | **Not a subclass** — a document is a `:Source` (§4.4). Its vocabulary is the `documentKind` genre axis over `sourceKind: document` (§5) |
| Subject | organization, team, role, legalPerson, naturalPerson |

> No Object subclass has its own metadata table — they inherit Object fields verbatim. A document's fields are a source's fields, and they are in **§4.6**.

---

## §7. Equivalence Model

Packs declare equivalences between kinds across packs via `equivalentTo`:

```yaml
equivalentTo:
  issue: [pm:task]
```

Bidirectional: querying `dev:issue` also returns `pm:task`, and vice versa.

---

## §8. Semantic-Epistemic Edge Model

A relationship operates on two layers — **semantic** (relation type) and **epistemic**
(confidence / contradiction). It carries an operator **only when it needs one**.

#### Reification rule — when an edge gets an operator

**An edge carries an operator iff it needs mitigation, or is an epistemic
support/contradict between Points and/or Events (Point↔Point, Event→Point,
Point→Event).** All other edges stay plain and carry confidence as an
edge attribute.

| Edge | Operator? | Confidence |
|---|---|---|
| Point↔Point support / contradict (IMPL/NAND) | **Yes** | EP over the IMPL/NAND edge |
| Any edge needing mitigation (+/− relevance) | **Yes** — mitigations attach to the operator | EP over IMPL/NAND |
| Structural edge without mitigation (about\*, performs/produces/uses, memberOf/ownedBy, provenance) | **No** — plain edge | confidence edge attribute |

- **Operator-less propagation:** an IMPL/NAND edge may be direct Point→Point
  (no operator); EP propagates over it the same way.
- **Direction:** `bidirectional` (default) / `unidirectional`. Lives on the
  operator node when present, else on the edge. EP reads the operator node
  first, falls back to the edge.
- **Lazy promotion:** a plain edge gains an operator only when mitigation
  becomes needed.
- **EP:** for operator-less edges, EP reads direction from the edge and
  initializes the edge message directly (operator-mediated edges compute
  messages on operator update).

Operator-mediated case (support/contradict, with mitigation anchor):

```
Semantic:   (Feature) ──[addresses]──→ (CustomerNeed)    ← operator.label
Epistemic:        ↑ IMPL, confidence: 0.85               ← EP propagation
Operator:      (op-123)                                   ← mitigation anchor
```

| Layer | Where | What |
|-------|-------|------|
| Semantic | operator.label | Domain verb: addresses, hasPart, opposes |
| Epistemic | IMPL/NAND edges | Confidence via EP (0-1 continuum) |
| Operator | Point (is_operator:true) | Mitigation target |

### Semantic Types

| Type | Mechanism | Epistemic propagation | Semantic label direction | Example |
|------|-----------|------------|-------------------------|---------|
| hasPart | IMPL | Bidirectional cascade (parts↔whole) | bidirectional | Epic hasPart Issue |
| addresses | IMPL | Unidirectional (A supports B) | unidirectional | Feature addresses Need |
| supports | IMPL | Unidirectional (A supports B) | unidirectional | Evidence supports Claim (CLI default label for IMPL, `__main__.py:81`) |
| opposes | NAND | Bidirectional by default, optional unidirectional (directed attack) | declared by pack | Feature competesWith Competitor |

> **Direction is an explicit operator flag, default bidirectional.** The table above shows typical pack declarations; a pack (or agent) may declare `direction: unidirectional` for a directed attack.

### Pack Relation Declarations

```yaml
relations:
  - predicate: decomposesInto
    mechanism: IMPL
    semantics: hasPart
    fromKind: dev:epic
    toKind: dev:issue
```

---

## §9. Expansion Pack Manifest Format

```yaml
namespace: dev
name: "Development"
version: "0.1.0"
tier: free
ontology:
  extends: core
  objectKinds: [epic, issue, code, bug, incident]
  subclassOf: {epic: Project, issue: WorkItem, bug: Problem, incident: Problem}
  equivalentTo: {issue: [pm:task]}
  pointKinds: [requirement, technicalDebt, risk]
  documentKinds: [architectureDoc, apiSpec]
  kindDefs:                     # extractor prompt material (v3, epic #909)
    epic:
      description: A large body of work decomposed into issues
      nearMisses: [issue]
  relations:
    - predicate: decomposesInto
      mechanism: IMPL
      semantics: hasPart
      fromKind: dev:epic
      toKind: dev:issue
      extractable: true
  chains:                       # business-logic paths (v3)
    - id: epicToCode
      steps: [epic, issue, code]
      enforcement: warn
  memory_granularity: 'Durable: the epic/issue state and the reasoning.
    Ephemeral: sprint mechanics, ticket logistics.'
extraction:                     # extraction activation + enforcement (v3)
  active: true
  sourceTypes: [conversation]
  enforcement:
    default: warn
    kinds:
      requirement: retry
```

Packs are loaded via the pack registry (`PackRegistry`) at startup; their kinds extend the core vocabulary and are queryable via `expand_kind` / `equivalentTo`. The manifest is **declarative only — no code executes on load**; connector/tool entrypoints are code references that are allowlisted (starter packs) and rejected on hosted tenant uploads (ontology-only v1, epic #1891). Authoring guidance: `docs/EXPANSION_PACKS.md` (behavior) + `packs/_template/manifest.yaml` (machine-checkable schema).

---

## §10. Epistemic Recency Modulation

Evidence aging is **user-configurable with a light default** — NOT blunt time decay. Stable facts stay strong regardless of age. Interacts with sourceKind/credibility tier (T0 direct observation ages differently than T4 speculation). Never auto-deprecates old evidence.

> **Decision log (v3.2, #398 open question):** temporal decay granularity = **deferred**
> (per-field / per-sourceType decay curves NOT shipped). Retained: the validated
> `0.95^years` modulation, T0-exempt, keyed on `sourceDate` else `ingestedAt`, recomputed
> per EP run via provenance-marked inherited baselines (`baseline_source='inherited-from-source'`,
> per-point time gate). Differentiated per-tier aging (§10 "ages differently") is a
> §10-implied follow-up; the extension point is the source-type registry's per-kind
> default/tier slot.

---

## §10.5 Cascading Invalidation (Claims 6–7 of the patent)

When an evidence source's confidence changes, downstream propositions that
depend on it through operator chains are **re-evaluated, not stored-flagged**.
Invalidation is a *derived* cascade — consistent with the ontology's
"Object.status is a cache of the event stream, never authoritative" doctrine (§2, §11):

```
supersede_point / invalidate_point          (§3.1: mark old outdated:true,
                                            create (new)-[:CORRECTS]->(old))
   → _mark_dirty(affected)                  (sdk.py — dirty roots queued)
   → EP re-propagation                      (ep.py _affected_claims: reverse
                                            BFS through IMPL|NAND, max_hops=2)
   → re-persist confidence to affected      (dream.py runs EP on dirty roots)
   → contested-claim detection at query     (get_contested_claims(variance),
     time                                   ep.py — variance from persisted
                                            α/β; also surfaced per-result as
                                            ep.contested in search, #580)
```

**Terminal posterior vacuity decay (#2490):** every terminalizing write
(retract/supersede/invalidate/assess_source + the rebuild folds) decays the
terminal claim to VACUITY — `confidence=0.5`, posterior `(1,1)` — atomically
with the status/flag write, so an include-terminal surface never shows a
frozen pre-terminal posterior. Decay is UNIFORM across #2421 Case-1
restatement and Case-2 correction (the old claim is terminal either way; the
successor recomputes independently). `ep_alpha`/`ep_beta` are deliberately
retained as prior history — there is NO unsupersede path that recovers the
old claim's posterior, so the retained prior is the SOLE recovery vector.
Every contested computation (annotate_ep_batch, rankers, `get_contested_claims`,
`_review_prune`, why, analyze) excludes terminal claims via the shared
live.py predicate (status ∈ {retracted, superseded, outdated, archived} OR
`outdated=true`).

**#2488 merge-blocker:** the rebuild-side fold decay applies to the *supersede*
fold only. The `PointInvalidated` rebuild fold (`_fold_point_invalidated`) ships
with **#2488**, so until it lands an invalidate→rebuild cycle **resurrects the
frozen posterior** (no fold re-applies decay). **#2490** is gated on #2488 and
adds `decay_clause('n')` to `_fold_point_invalidated`'s SET when it lands.

**Design decisions (recorded for the patent filing):**

| Question | Decision |
|----------|----------|
| Ontology concept vs implementation detail? | **Derived behavior**, documented here; no new stored entity |
| Dedicated edge type (DEPENDS_ON)? | **No** — reverse traversal of IMPL/NAND operators is sufficient; a stored DEPENDS_ON edge would duplicate structure and drift |
| Representation of "potentially invalidated"? | **Elevated posterior variance** (v > 0.04 → contested), not a stored `pointStatus` — statuses are `{draft, live, retracted, superseded, outdated, archived}` (§5); `outdated` is set only by an explicit terminalizing write (`supersede_point` / `invalidate_point`), never auto-inferred — it does **not** identify a supersession (§4.7) |
| Terminal claims' posterior after terminalization? | **Decay to vacuity** (0.5, posterior (1,1)) at the terminalizing write + rebuild fold (#2490) — never a frozen pre-terminal posterior; `ep_alpha`/`ep_beta` retained as the sole recovery vector |
| Interaction with CORRECTS? | CORRECTS is the *structural* replacement; cascading invalidation is the *belief-level* consequence — both fire from the same write (`supersede_point` → `_mark_dirty`) |

Direction-aware EP (§3.1, #86) is the prerequisite that makes reverse
traversal well-defined: IMPL is unidirectional (source→target), NAND
symmetric by default (directed `unidirectional` NANDs traverse one-way
per §3.1 — extraction-emitted NANDs default directed, #909), hasPart
bidirectional — `_affected_claims` follows these directions.

---

## §11. Reputation (derived, not stored)

`compute_reputation(subject_id)` is a query-time primitive:

```
subject -[:performs]-> events → outcome operators (Event→Point IMPL/NAND)
  → aggregate success/failure, optionally weighted by recency
  → return reputation score
```

- Not stored (would go stale)
- Bridges procedural history to epistemic belief: "how much weight should this agent's claim carry?"
- **Derived values may be CACHED, never authoritative (v3.2, #398):** Source
  `reliability` is a write-through projection of the query-time derivation
  (recomputed on write events, consistency-checked on read, stamped with
  `reliability_derived_at`) — the derivation is the truth, the cache is a
  performance artifact.


---

## §12. Relationship to Standards

| Standard | Alignment |
|----------|-----------|
| **PROV-O** | Subject→Agent, Event→Activity, Object→Entity. `uses`=prov:used (Activity→Entity, direction-identical), `produces`=prov:wasGeneratedBy inverse, `wasDerivedFrom`=prov:wasDerivedFrom, `performs`=inverse of prov:wasAssociatedWith (PROV names Activity→Agent accountability; we name the Agent→Activity doing verb). **Temporal:** the valid-time window (`validFrom`/`validTo`) ≈ `prov:generatedAtTime`/`prov:invalidatedAtTime` (the entity's existence window); transaction time (`createdAt`/`expiredAt`, Source `ingestedAt`, Event `capturedAt`) is the graph-write clock — a record-reification clock, not a PROV time property. |
| **Schema.org** | Event with startTime/endTime. Action pattern: `performs`=schema:agent inverse (the "direct performer or driver of the action"), `produces`=schema:result, `uses`=schema:instrument (mechanisms) / schema:input. |
| **BIBO** | `documentKind` genre vocabulary (v3.15/D10: an axis over `sourceKind: document`, not an Object subclass). |
| **OWL-Time** | Event is the temporal entity (`startedAt`/`endedAt` = the valid-time alias, §4.7). Non-event validity windows (`validFrom`/`validTo`) are intervals on the same axis. Transaction time is the record clock, not an OWL-Time interval. |
| **Bi-temporal (Graphiti/Zep)** | Two orthogonal axes — **valid time** (`validFrom`/`validTo`) and **transaction time** (`createdAt`/`expiredAt`) — plus supersession as a third, separate fact (`supersededAt` on Object; `status='superseded'` on Point — the `outdated` flag + `CORRECTS` edge are shared with invalidation and do not distinguish the two). Window contiguity on **supersession** is the Graphiti (Zep) **intent, conditional**: `validTo` = the successor's `validFrom` **when it carries one**, else its `createdAt`, else `now` (exact contiguity only for a dated successor); `invalidate_point` instead stamps `validTo = now` (no successor ⇒ no contiguity), and refuses with `ValueError` when a stored `validFrom` is after `now` — `retract_point` is the window-agnostic route (#5358) (§4.7). |
| **RDF-star** | Operators are reified edges with metadata (label + confidence) — RDF-star-like reification for epistemic edges. |
