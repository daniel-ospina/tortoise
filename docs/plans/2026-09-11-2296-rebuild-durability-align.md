---
title: "#2296 Align — rebuild-durability write-surface invariant"
aboutSubjects: epistemic-team
aboutObjects: tortoise
status: draft
created: 2026-09-11
issue: 2296
type: Decision
---

# Epic Strategy Alignment — #2296

**Skill:** `epic-align` (epic-workflow Stage 1)
**Issue:** #2296 — rebuild-durability write-surface invariant: audit journal-or-snapshot coverage per write class
**Complexity:** complex · **Team:** epistemic-team · **Depends on:** #2194, #2295 (both merged)

## Step 1 — Adversarial strategy test

### Alternatives considered

1. **Do nothing; keep discovering holes serially.** Each durability gap so far has
   been found the same way — by a later feature tripping over it: #548 (Points) →
   #2061 (Events) → #2164/#2193 (supersession fold) → #2194 (capture Objects) →
   #2295 (Subjects) → #2295's own scope hook (SDK `DocumentCreated`, the last unjournaled
   canonical entity-create through the `_create_entity` funnel (note `:Session`
   is also unjournaled, and is created by a raw `MERGE` outside that funnel —
   `sdk.py:3051`): `create_document` routes through
   `_create_entity`, which journals `EventRecorded` / `ObjectRegistered` /
   `SubjectAdded` but has **no `label == "Document"` journal block**). This
   "works", at the cost of one discovered gap per write class.
   **Rejected:** the discovery order is set by feature traffic, not by risk, so the
   most dangerous unmapped classes are found last, and each discovery happens while
   someone is mid-feature with the least capacity to fix it. (The 2026-08-05 data
   loss is **not** charged to this alternative — its root cause was infra/backup;
   see Step 3.)

2. **Skip the map; implement only the known gaps** (the Object journal-loss
   backstop, capture edge/session-link durability, `DocumentCreated`). Narrow and
   concrete: `ObjectRegistered` — and, by the `#2296`-referencing comment on the
   `SubjectAdded` block, `SubjectAdded` too — has no #548-style pre-wipe snapshot
   analogue, so a torn/lost journal line leaves that record
   **live-but-not-durable** (logged and counted at parse time, but invisible to the user) and it is absent after rebuild.
   **Rejected as the epic's definition, kept as an indicator.** It fixes the holes
   we already know about and leaves the *contract* unwritten — which is the actual
   defect. Framing C in #2194 scoping ("the rebuild-durability contract has
   no invariant") is precisely this epic's definition; the backstop is a
   deliverable, not the objective.

3. **The map + emission-coverage invariant (proposed).** Enumerate every live write
   class with its rebuild carrier. The label set is **measured, not assumed**, by
   two commands run against this worktree:

   ```
   # run from the repo root; the scans cover tortoise/ ONLY (see the scope note below)
   # NB the variable is OPTIONAL (`[a-zA-Z_]*`) — `CREATE (:TeamMeta {...})` binds none,
   # and a required-variable pattern silently misses it (that error was caught in review).
   grep -rhoE "(MERGE|CREATE) \(([a-zA-Z_]*):([A-Z][A-Za-z]*)" tortoise/ --include=*.py | sed -E 's/.*:([A-Z][A-Za-z]*)$/\1/' | sort -u | wc -l   # 26 (union of both verbs)
   ```

   **Measured result — 18 `MERGE` labels:** `Batch`, `CommitRecord`, `Document`,
   `EpMeta`, `Event`, `GraphEventMeta`, `Membership`, `Meta`, `MeteringRecord`,
   `Object`, `OnboardingState`, `OnboardingStep`, `PackInstall`, `Point`,
   `Session`, `Source`, `Subject`, `Tag`.
   **10 `CREATE` labels:** `APIKey`, `Event`, `Graph`, `GraphEvent`, `Invitation`,
   `Membership`, `Point`, `SignupToken`, `Team`, `WebhookEvent`.
   **Union: 26 literal labels** (8 are `CREATE`-only: `APIKey`, `Graph`, `GraphEvent`,
   `Invitation`, `SignupToken`, `Team`, `TeamMeta`, `WebhookEvent`; the 26th literal
   label, `TeamMeta`, is written variable-less as `CREATE (:TeamMeta {...})`) —
   **plus `PackManifest`, which no literal scan can see** (it is interpolated from
   `PACK_MANIFEST_LABEL`, `pack_manifest_store.py:48`), for **27**.

   Two consequences the map must carry:
   - A `MERGE`-only scan is structurally blind to the 8 `CREATE`-only labels, and a
     required-variable pattern is blind to `(:Label)` forms — so the enumeration rule
     must be **`MERGE` OR `CREATE`, variable optional, plus dynamic-label
     constants**.
     Indicator 1 says "every live write class"; a scan that cannot see `CREATE`
     guarantees a hole in exactly the set it claims to bound.
   - `:Tag` (live `MERGE`, no `_emit_event`) and `:EpMeta` (EP epoch state) are the
     two labels that most need a decision, since `rebuild_all` wipes every label
     while the pre-wipe snapshot covers only `:Point` and `:Batch`.

   **Scope of the measurement (stated, not implied):** the scans cover `tortoise/`
   only. The same labels are also written from `tools/`, `graph-scripts/` and
   `battery/`; those callers reuse the same labels, so they do not add classes, but
   the map must record that its enumeration is *SDK-package-scoped*. One further
   literal-invisible path is `tortoise/hosted_backup.py:274`
   (`f"CREATE (n:{safe_labels})"`), which the "dynamic-label constants" rule must
   also sweep.

   **Delete-side durability is in scope** (and is the reason this cannot be a
   create-only map): `docs/ONTOLOGY.md:384` requires that "the
   durability write-surface invariant must cover the delete side of the lifecycle
   + the Object/Subject loss backstop", and `:404` requires the delete side
   separately. Both #2194 and #2295 recorded that `_delete_entity` leaves no
   tombstone, so a deleted node **resurrects on rebuild**. The map therefore classifies each class on BOTH axes (write carrier,
   delete carrier), and `deleted`/`tombstone` evidence is a required column.

   The initial issue text named only the memory-carrying subset and only the create
   path; the map's job is to classify the rest **explicitly**, not to omit them.
   **Chosen:** it is the only option that converts a repeating discovery process
   into a checkable invariant, and it subsumes option 2 as one row of the map.

### Strongest reasons NOT to build this

- **It is meta-work.** It ships no user-visible behaviour; the honest profit chain
  is indirect (below). A reviewer is right to ask why not spend the same effort on
  retrieval quality (e.g. the #2652–#2655 reader gaps #2578 just measured).
- **A map can rot.** A document of write classes goes stale the moment a new one
  lands silently — which is exactly the failure it exists to prevent. *This is why
  the epic's third indicator is an enforcement mechanism (test or checklist), not
  prose.* A map without the invariant would be theatre.
- **The blast radius may be smaller than feared.** Some classes (MeteringRecord,
  OnboardingState, Membership) are operational, not memory, and may be legitimately
  non-durable — replaying them from a journal could even be *wrong*. The map must be
  allowed to conclude "explicitly non-durable, by design".

### Opportunity cost

The alternative use of this capacity is the measured retrieval/conversion work
(#2652–#2655, #2886 — the negative temporal classes #2578 identified). That work is
higher-variance and has a clearer product return. This epic is insurance, and it is
worth doing *now* specifically because the write surface is still small enough to
enumerate: the cost of the map grows with the number of write classes.

## Step 2 — Eisenhower matrix

| | Urgent | Not Urgent |
|---|---|---|
| **Important** | | **← #2296 (Schedule)** |
| **Not Important** | | |

**Placement: Important / Not Urgent — Schedule.** Justification: the consequence of
the gap is severe and asymmetric (memory can be permanently lost on rebuild, and the
loss is invisible to the user), which
makes it Important. It is not Urgent because no current feature is blocked on it and
rebuilds are rare — *except* that the serial-discovery pattern shows urgency accrues
with every new write class, since each new class is a new chance to find a hole
while someone is mid-feature. The 2026-08-05 incident is **not** evidence here: it
was an infra/backup failure. That tension is the reason to schedule this deliberately
rather than wait for the next discovery to force it.

## Step 3 — Profit growth alignment

**Causal chain:** rebuild durability → memory survives graph loss → the product's
core promise (agent memory that persists) is true → user trust → retention →
revenue.

The failure mode is not degraded quality, it is **data destruction**: the 2026-08-05
incident lost 5,748 points and was only partially recoverable. **That incident's root
cause was an infra/backup failure (a self-hosted FalkorDB with AOF disabled and no
off-box backup), not a missing journal class — a journal-coverage audit would NOT
have prevented it**, and this doc does not claim otherwise. The incident is cited as
evidence of the *consequence class* (memory loss is permanent and trust-destroying),
not as evidence that this epic prevents it. A memory product that loses memory has no
defensible price, so the durability contract is a *precondition* for the memory
product being sellable rather than a feature that adds revenue.

**Orders of magnitude:** not directly monetizable, and no quantitative comparison to
a retrieval win is asserted — none is measurable from this epic. It is insurance
against a class of failure that is permanent and invisible, and should be sized as
such (a bounded audit, not an open-ended hardening programme).

**Faster path to the same outcome?** Operationally, backups (off-box, tested restore)
address loss faster than a per-class journal audit. This epic is the *contract-level*
fix that makes the backup story checkable per class; the two are complements, and the
backup path is out of this epic's scope (it belongs to the durability/infra lane).

## Step 4 — Decision rationale

**Feature:** #2296 rebuild-durability write-surface invariant
**Decision:** **PROCEED** (as a bounded audit, with the enforcement indicator
non-negotiable)

**Alternatives considered:**
1. Do nothing / keep discovering serially — rejected: risk-ordered incorrectly, one
   discovery per write class, each surfacing while someone is mid-feature.
2. Implement only the already-known gaps (Object/Subject loss backstop, capture
   edge + session-link durability, `DocumentCreated`) — rejected as the objective:
   fixes the known holes, leaves the contract unwritten. Retained as indicators.
3. Map + emission-coverage invariant — **chosen:** the only option that turns a
   repeating discovery process into a checkable contract.

**Profit impact:** loss avoidance on the product's core promise — **not directly
monetizable, and not a substitute for the backup/restore work** (the failure that
actually destroyed data). Sized as bounded insurance; no quantitative comparison to a
retrieval win is asserted, because none is measurable from this epic.

**Eisenhower placement:** Important / Not Urgent → **Schedule**. Insurance with an
asymmetric downside; cost grows with each new write class.

**Key assumptions:**
- The write surface is small enough to enumerate completely in one epic — confidence:
  **medium** (the scan already surfaces 18 MERGE labels plus 10 CREATE labels; the true set may be
  larger once conditional paths are followed).
- Some classes are legitimately non-durable, and the map may conclude exactly that —
  confidence: **high** (MeteringRecord / OnboardingState / Membership look
  operational).
- An enforcement test is feasible without excessive coupling — confidence:
  **medium** (depends on whether journal emission is centralized or scattered; the
  scan suggests scattered call sites, which raises the bar).
- The Object/Subject journal-loss backstop is a real gap with a viable snapshot
  analogue — confidence: **high** (surfaced as DA-P3 during #2194 scoping; the
  `SubjectAdded` block's own comment references #2296). Verified in code: the
  pre-wipe snapshot covers `:Point` and `:Batch` only.
- `DocumentCreated` is genuinely unjournaled on the SDK create path — confidence:
  **high** (verified: `_create_entity` has Object/Subject/Event journal blocks and no
  `Document` block). Other `DocumentCreated` emitters exist on unrelated routes
  (file-indexing `sdk.py:17763`; the EventAPI lane `api.py:310`) and do not change
  the conclusion — the `create_document` path is the one that matters.

**Terminology (defined here, used throughout):** a **write class** is one
`(node label | edge type)` that the SDK writes to the graph — 26 node labels above
(25 literal + `PackManifest`) plus the edge types the issue names (`about*`,
`CONTAINS`) and any others the widened scan finds. A class is **memory-carrying** if
losing it on rebuild changes what the product can answer or believe. The
memory-carrying set is the trigger's denominator. What follows is the **initial
scan's set, which the audit must complete** — it is a floor, not a claim of
completeness:

**Nodes (9):** `Point`, `Event`, `Object`, `Subject`, `Document`, `Source`,
`EpMeta` (EP epoch state), `Tag`, `Session` (unjournaled, and reconstructed by no
rebuild branch — see above).

**Edges — discovered by scanning the edge vocabulary, never by guessing:** the
`about*` wildcard is **seven** live types, each its own class (`aboutAction`,
`aboutDocument`, `aboutEvent`, `aboutObject`, `aboutPoint`, `aboutSource`,
`aboutSubject` — authoritative enumeration: `docs/ONTOLOGY.md:214-220` and
`tortoise/security.py:76-78`; `edges.py`'s `_VALID_EDGE_PREDICATES` lists only six,
with `aboutPoint` in `create_about_edge`'s valid set), plus the session `CONTAINS` link,
plus the responsibility edges `authoredBy` / `ownedBy` / `managedBy`, which
`docs/ONTOLOGY.md:404` already records as **not durable for Object replay**
(EventAPI-parity) and which `edges.py` writes. That is 11 edge classes; other
EP/operator edges (`TAGGED`, `IMPL`, `NAND`, `mitigated_by`) are added by the same
scan if the audit finds them written on live paths.

**T ≈ 20 from this initial scan**, which the audit must complete — `TAGGED` is
already written on live paths (`sdk.py:2111`, and `:Tag` has no `_emit_event`, so it
is non-durable by the doc's own definition) and `TAGGED`, `IMPL`, `NAND`,
`mitigated_by` are all added by the same vocabulary scan. *The partition argument
below holds for any T > 0*, so the exact value need not be settled for the trigger to
be well-formed — only that the set is derived by a stated scan, and the audit
replaces the estimate with the scan's result before choosing a branch.

**One edge family is already durable and must not be re-litigated:** `extractedFrom`
plus the five snapshot-derivable `about*` types (`aboutSubject`, `aboutObject`,
`aboutEvent`, `aboutDocument`, `aboutPoint`) are journaled as `DirectEdgeRepoint`
descriptors (#2489) and replayed by rebuild pass-2b; `aboutAction`, `aboutSource`
and `wasDerivedFrom` are explicitly **never** snapshot-recreated (the A10 raw-edge
family). The map's edge rows must cite this, because "no event family exists" is
false for that subset — the gap there is `#2501` (live wiring), not durability.

Everything else is **operational** — deployment/telemetry/identity state whose loss
does not change a memory answer: `MeteringRecord`, `OnboardingState`,
`OnboardingStep`, `PackInstall`, `PackManifest`, `CommitRecord`, `Meta`,
`GraphEventMeta`, `Membership`, `Batch`, `APIKey`, `Graph`, `GraphEvent`,
`Invitation`, `SignupToken`, `Team`, `TeamMeta`, `WebhookEvent`. Operational classes still need a
map row and a carrier — some are durable (`Batch` is operational *and* carried by the
#548-style pre-wipe snapshot), others are legitimately non-durable and need an
explicit rationale. They are excluded from the trigger's denominator because "memory
survives" is the property being protected, not "every label is journalled". The audit assigns each class
one of three carriers: a JSONL event type, a #548-style snapshot, or an explicit
**non-durable** declaration with a one-line rationale.

**Recommendation:** PROCEED, bounded — with the **map never re-scoped down**. The
map must classify **every** class the widened scan finds, on both axes (write carrier
and delete carrier), regardless of how large the set turns out to be; completeness of
the map is the deliverable, and a partial map is a failed epic.

Only the **remediation** flexes, and the threshold is defined against the map itself
rather than a pre-guessed number:

Let *N* = the number of enumerated memory-carrying classes (above) that need a
carrier they do not have today, and *T* = |memory-carrying set|.

- If **N / T ≤ 1/3**, close **all** of them in this epic.
- If **N / T > 1/3**, ship the complete map + the enforcement invariant here and file
  the remaining fixes as individually scoped follow-ups, each already enumerated with
  its exact carrier gap. (The hard case behind the threshold is a class that needs an
  entirely new event family rather than an extension of the existing `_emit_event` /
  pre-wipe-snapshot machinery — e.g. the `about*`/`CONTAINS` edges and
  `DocumentCreated` — but the *condition* is simply the ratio, so the two branches
  partition cleanly.)

The gates below apply in **both** branches:

- **Reject any scope that ends at a document with no enforcement mechanism.** A map
  without the invariant is theatre — the epic's indicator 3 is non-negotiable.
- **Reject any scope that drops the delete side.** `docs/ONTOLOGY.md:384`/`:404`
  assigned it here; `_delete_entity` leaves no tombstone, so a deleted node
  resurrects on rebuild.
