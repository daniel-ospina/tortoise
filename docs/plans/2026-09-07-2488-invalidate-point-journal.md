<!-- research-path: /tmp/scopes/2488-scope.md (archived on issue #2488) -->

# #2488 invalidate_point unjournaled — Implementation Plan

**Goal:** Journal `invalidate_point` so `rebuild_all` replays the outdated-flag + CORRECTS (no ghost resurrection), with a cross-family chronological fold ordering rule shared by supersede+invalidate.

**Team:** team:epistemic-team
**Role:** implementer

**Architecture:** invalidate currently writes `outdated=true` + CORRECTS live but emits zero events → rebuild replays the pre-invalidate PointAdded and resurrects the claim to EP voting. Fix = new `PointInvalidated` graph event (kwargs-style), a `_fold_point_invalidated` mirror of `_fold_point_superseded` (no status write), and a pass-1b trailing-sweep fold governed by a cross-family survivor rule (keep terminalizing folds whose seq is after the id's last PointAdded re-creation; journal-append order).

### Pattern Research
Skipped — plan touches zero third-party deps (pure Python + Cypher). Prior research: #2422 (EP ghost) and #2423 (supersede rebuild) shipped; this mirrors #2423's fold machinery.

### Integration Surface Map
| Surface | Layer | Notes |
|---|---|---|
| invalidate_point (sdk.py:4124) | unit + event-store | kwargs emit, no status write |
| GraphEvent codec registries | unit | _GRAPH_EVENT_TYPES + CLAIM_EVENT_TYPES + stale enumerations |
| rebuild pass-1a/b + sweep (projection/__init__.py) | integration (rebuild) | survivor filter replaces last_per_oid; raw fold_seq enumerate deleted |
| pass-2b succ/fold_seq derivation | integration (rebuild) | consumes pre-filtered survivor list; fold_seq kind-bound to supersede survivor |
| _fold_point_invalidated (entities.py) | integration | fold parity test vs live state |
| event store append (event_store.py) | none (schema-free) | append_event takes arbitrary type |
| Hosted poll consumer | N/A | codec registry validates (sdk.py:2072); apply()-parity out-of-scope by design |

**Tech Stack:** Python 3.12, FalkorDB/Cypher, tortoise journal + projection.

---
## Tasks

### Task 1: Add PointInvalidated to event registries

**Intent:** Register the new graph-event type so emission + rebuild replay are first-class.
**Acceptance:** PointInvalidated present in both type sets; stale enumerations refreshed.
**Files:**
- Modify: `tortoise/shared_state/events.py` (CLAIM_EVENT_TYPES tuple — append "PointInvalidated" after the last member ~:172, BEFORE the close paren at :173; the :165 cite in earlier drafts pointed at the PointRetracted entry, not the tail)
- Modify: `tortoise/sdk.py:720-733` (_GRAPH_EVENT_TYPES), `:2147-2148` (docstring)

**Steps:**
1. Add `"PointInvalidated"` to `_GRAPH_EVENT_TYPES` (sdk.py:720-733) and `CLAIM_EVENT_TYPES` (events.py — append at the tuple tail after the last member ~:172, before the close paren :173; both registries currently hold 10 — post-append 11).
2. Refresh stale enumerations — replace the literal "five concrete event types" comment at events.py:161 (tuple now has 11) and the sdk.py:2147-2148 docstring listing 5 of 10; ALSO mcp_server.py:1670 and tool_registry.py:509 (`tortoise_events_poll` surfaces list 5 of 11 types); ALSO `tortoise/shared_state/tests/test_events_claim.py` (module docstring + CLAIM_TYPES const) and the event-catalog.md ⛔ note. Each fix states the new count (11).
3. Run: `TORTOISE_TEST_CARVE_OUT=1 PYTHONPATH=$PWD .venv/bin/python -m pytest tortoise/shared_state/tests/test_events_claim.py tests/test_event_store.py -q` — expect PASS (no type-set equality pins exist).
4. Commit: `git add -A && git commit -m "feat(events): register PointInvalidated graph event type"`

### Task 2: Emit PointInvalidated from invalidate_point (kwargs-style)

**Intent:** Journal the invalidation so rebuild can replay it. Kwargs-style emission is mandatory — dict payload returns before the JSONL write (sdk.py:2176-2178).
**Acceptance:** invalidate_point emits PointInvalidated after validation, before graph writes; live writes unchanged.
**Files:**
- Modify: `tortoise/sdk.py:4124-4191` (invalidate_point)

**Steps:**
1. In `invalidate_point`, after validation and BEFORE the SET/MERGE graph writes, kwargs-emit: `_emit_event("PointInvalidated", id=id, corrected_by=corrected_by_id, ts=now, valid_to=now, expired_at=now)` — **`ts=now` MUST be passed** (the same `now` the graph write uses) or the fold's updatedAt (`ev.get("ts")` fallback clock) drifts microseconds from the live SET clock and the Task 5 step-1 exact-stamp parity assertion fails. Mirrors supersede_point's validated-emit-then-mutate pattern (#432 anti-phantom). Crash after emit/before write is convergent: re-run revalidates + re-emits; duplicate events fold idempotently; double-invalidate already legal.
2. Keep #2422's drop-messages-before-_mark_dirty ordering intact (do not move the EP drop relative to the epoch bump).
3. Run: `TORTOISE_TEST_CARVE_OUT=1 PYTHONPATH=$PWD .venv/bin/python -m pytest tests/test_ep_terminal_ghost.py -q` — expect PASS (13/13, no behavior change).
4. Commit: `git add -A && git commit -m "feat(sdk): emit PointInvalidated in invalidate_point"`

### Task 3: _fold_point_invalidated (entities.py)

**Intent:** Rebuild-time fold applying outdated=true + CORRECTS with no status change.
**Acceptance:** Fold writes exactly: outdated=true (hardcoded — not payload-carried), validTo/expiredAt/updatedAt from payload (ts fallback), CORRECTS MERGE (keyed corrected_by); does NOT write status or validFrom.
**Files:**
- Modify: `tortoise/projection/entities.py` (near `_fold_point_superseded` :269-321)

**Steps:**
1. Add `_fold_point_invalidated(self, ev: dict, skip_updated_at: bool = False) -> int` mirroring `_fold_point_superseded(self, ev: dict)` (entities.py:269-321) — parses payload inside with fallbacks. Writes: `outdated=true` (hardcoded), validTo/expiredAt/updatedAt, CORRECTS MERGE keyed `corrected_by`. Does NOT write status or validFrom. **updatedAt write is seq-gated, NOT clock-conditional** (a `$ts >= n.updatedAt` CASE can never fire: pass-1a's `_upsert_point_props` stamps every replayed node with rebuild-time `updatedAt=$now` BEFORE the trailing sweep runs, and rebuild-now always postdates the journaled invalidate ts — the ELSE arm would win every time, making rebuilt updatedAt = rebuild-now ≠ live invalidate ts). When `skip_updated_at=True` (a LATER same-id PointRevised/PointPromoted exists in the journal — see Task 4 step 4), the fold omits updatedAt so it cannot clobber the later inline stamp; otherwise it writes the journaled ts UNCONDITIONALLY (the sweep fold is the last writer on the id, achieving exact live parity like supersede's unconditional fold). Early-return 0 guard (`not id or not corrected_by`). Return matched-row count.
2. Mirror the superseded fold's 0-row fold-miss warning semantics (warn-then-continue — a 0-row fold = point absent = nothing resurrects).
3. Docstring: note the divergence from _fold_point_superseded (status+successor vs flag+stamps+corrected_by) so the next terminalizer doesn't blind-copy.
4. Run: full ghost + rebuild suites (`tests/test_ep_terminal_ghost.py tests/test_pointsuperseded_rebuild.py`) — expect PASS.
5. Commit: `git add -A && git commit -m "feat(projection): add _fold_point_invalidated rebuild fold"`

### Task 4: Pass-1b trailing-sweep fold with cross-family survivor rule

**Intent:** Rebuild replays invalidation. Core P1 of this issue — the fold-ordering rule resolves double-invalidate, id-reuse, and mixed supersede+invalidate.
**Acceptance:** (a) invalidate→rebuild reproduces identical live state (outdated+stamps+CORRECTS, no status change); (b) double-invalidate with distinct corrected_by folds both CORRECTS; (c) delete+recreate id-reuse drops pre-recreation terminalizing folds; (d) supersede+invalidate mixed (both orders) converges; (e) pass-2b sees only survivors.
**Files:**
- Modify: `tortoise/projection/__init__.py` (pass-1a ~1306-1331, deferrals 1414-1434, trailing sweep 1477-1513, pass-2 raw fold_seq enumerate ~1574-1575, pass-2b succ 1584/1611)

**Steps:**
1. In pass-1a (which sees PointAdded in journal order over the SAME `events` list pass-1b iterates), add `enumerate()` and record `last_recreate_seq[id]` for each PointAdded — record after the isinstance guard yields the str id (the anchor needs the parsed id; "before the malformed-skips" is imprecise — the skip for malformed events is a continue, record only for valid PointAdded rows). `last_recreate_seq.get(id)` defaults to None for anchor-less ids → a None anchor means "no re-creation seen → keep all folds" (never raise on `seq > None`). PointAdded ONLY — **PointPromoted must NOT be a drop boundary** (promote is same-node draft→live; invalidate-on-draft→promote is legal live; promote never clears outdated/CORRECTS so seeding from it would silently drop the pre-promote invalidate fold).
2. Defer PointInvalidated events to the trailing sweep alongside PointSuperseded (both families, one deferred list). **Record the enumerate() index on each deferred PointSuperseded/PointInvalidated** (same `events` list pass-1a iterates ⇒ identical seq space) — this is the ONLY source of the journal seq the survivor rule needs after pass-2's raw fold_seq enumerate is deleted (step 5). Pass-1b's deferral currently appends bare `ev` at :1434 — extend it to carry the index.
2b. **Record a per-id `max_inline_seq[id]` in pass-1b** at the PointRevised (:1383) and PointPromoted (:1353) branches — the sweep's skip_updated_at gate (step 4) needs to know whether a same-id revise/promote event has seq AFTER the invalidate's seq; revise/promote are inline events that never enter the deferred list, so without this structure the gate has no data source. Computable pre-sweep (pass-1b iterates the same ordered events list); pass-2's :1539 enumerate runs too late.
3. Build ONE pre-sweep survivor list: per old_id, keep re-stamping folds (PointSuperseded + PointInvalidated — invalidate is a re-stamp on a possibly-recreated id, not a status terminalizer) with seq > `last_recreate_seq[id]` (drop seq ≤ anchor; a None anchor = "no re-creation seen → keep all folds"). **The fold loop retains `(seq, ev)` pairs** (the seq-gate at step 4 needs the invalidate's seq); pass-2b-facing structures (succ/repoint/fold_seq) consume bare `ev` dicts — the index does NOT leak into those loops (pass-2b consumers call ev.get(...) on dicts). **This REPLACES the existing `last_per_oid` dedup (1486-1497)** — one cross-family filter, not two mechanisms.
3b. **max_inline_seq omission caveat (document):** the structure omits later same-id PointRetracted — live-legal invalidate→delete leaves updatedAt drift on the tombstoned node (rebuilt _retract stamps rebuild-now, not the delete ts). Pre-existing tombstone-stamp behavior; #2422/#2423 parity unaffected — documented, not fixed.
4. Fold survivors in journal-append order (ascending event index). Do NOT sort by ts (ts collides within the same ms; JSONL carries no seq). Stamps (validTo/expiredAt) come from the journaled payload ts — NOT rebuild time — parity with _fold_point_superseded's `ev.get("ts") or _now_iso()` fallback (pins the #2164-P4 drift class). **updatedAt seq-gate (skip_updated_at), NOT clock comparison:** pass-1a's `_upsert_point_props` stamps every replayed node updatedAt=rebuild-now BEFORE the sweep runs, so a `$ts >= n.updatedAt` comparison can never fire (rebuild-now always wins the ELSE) — a clock gate is dead code. Unlike a superseded old (status terminal, frozen), an invalidated point stays status='live' — a LATER same-id PointRevised (inline _revise_point) or PointPromoted (:1353, promote CAS sets updatedAt) is a legitimate newer writer. **skip_updated_at fires when `max_inline_seq[id]` (step 2b) > the invalidate's seq** — the inline fold already stamped rebuild-now; do not clobber with the older invalidate ts. outdated/validTo/expiredAt/CORRECTS ALWAYS fold — the seq-gate suppresses ONLY the updatedAt column, never the whole fold (or live-legal invalidate→PointRevised loses its outdated flag and the ghost silently returns). Otherwise (invalidate is the id's last journal writer) the fold writes journaled ts UNCONDITIONALLY — the sweep fold is the last writer, achieving exact live parity (supersede's unconditional fold precedent). **Known limitation (out of scope, #2164-class):** a revised/promoted point's rebuilt updatedAt = rebuild-now (inherent `_upsert_point_props` stamping at entities.py:179, discards the journaled promote/revise ts) — NOT equal to live promote/revise time. #2488 does not fix this; it only guarantees the invalidate fold never clobbers with an OLDER ts.
5. Delete the raw fold_seq enumerate in pass-2 (~1574-1575 — only the PointSuperseded elif + fold_seq dict are deletable; the :1539 enumerate ALSO computes operator_created_seq + edge wiring — keep those). **Succ map (:1611), repoint iteration (:1635), AND fold_seq lookups (:1652) ALL bind to the supersede-kind survivor subset of the one pre-filtered deferred list** — a pre-recreate supersede dropped by the survivor filter must not leave a raw-list entry whose `.get(oid)` fold_seq = None (the `op_seq > fseq` order guard would silently disable → ghost re-point of the fresh incarnation, breaking acceptance c/e). fold_seq binds to the supersede-kind survivor (mixed invalidate→supersede would otherwise bind the invalidate seq → wrong re-point skip).
6. P2-caveats (comment, in-issue): operator-id re-creation (supersede-of-operator, #1080) regresses to folding pre-recreation supersedes — operator anchors out of scope v1; same-id re-emission ambiguity (SDK emits PointAdded only for new points — reachable via raw/capture producers only). **SDK delete_point emits PointRetracted → pass-1b inline _retract tombstones a re-created incarnation pre-sweep — breaks the id-reuse premise for SDK-journaled deletes** (Task 5 step 6 uses the RAW hard-delete lane instead).
7. Run: existing suites (`tests/test_pointsuperseded_rebuild.py tests/test_ep_terminal_ghost.py`) — expect PASS (no regression to #2423's 9 parity tests).
8. Revise-after-invalidate parity test (pins the seq-gate skip_updated_at): invalidate(A) → PointRevised(A) → rebuild → assert outdated=true AND validTo/expiredAt survive AND updatedAt != the journaled invalidate ts (the fold did NOT clobber with the older ts — skip_updated_at fired). **Do NOT assert rebuilt updatedAt == live revise time** — pass-1a stamps rebuild-now on the revised point (entities.py:179), so exact live parity for revised points is unachievable without an out-of-scope `_upsert_point_props` change; assert instead that updatedAt is in the rebuild epoch (post-invalidate).
9. Commit: `git add -A && git commit -m "feat(projection): cross-family re-stamp fold survivor rule (invalidate+supersede)"`

### Task 5: tests/test_pointinvalidated_rebuild.py

**Intent:** Pin the new behavior: rebuild parity, no-resurrection, idempotency, mixed cases, id-reuse.
**Acceptance:** All listed cases covered and green. Core indicator: invalidate→rebuild → identical state INCLUDING no status change + EP no-resurrection.
**Files:**
- Create: `tests/test_pointinvalidated_rebuild.py`

**Steps:**
1. Core indicator test: create point → invalidate (corrected_by B) → rebuild_all → assert outdated=true, validTo/expiredAt from journaled ts, **updatedAt == journaled invalidate ts** (invalidate is the id's last journal writer → the sweep fold writes ts unconditionally → exact live parity; scope: this holds for the plain case with no later same-id revise/promote), CORRECTS edge, status still 'live', EP exclusion (never in affected set / no vote).
2. EP no-resurrection: after rebuild, the claim does not re-enter EP participation (mirror #2422 ghost assertions).
3. Idempotency: rebuild twice → identical state (fold re-runs write the same journaled ts — stable across rebuilds, unlike pass-1a's rebuild-now stamps which the sweep fold overwrites).
4. Mixed supersede+invalidate parity: supersede(A,B) then invalidate(A,C) AND reverse order → rebuild → assert live-parity (A: superseded+outdated+stamps of last event + 2 CORRECTS; edge transfer only from supersede survivor).
5. Double-invalidate both-CORRECTS parity (distinct corrected_by → 2 CORRECTS edges; identical corrected_by → 1).
6. Id-reuse drop-pre-recreation via the RAW hard-delete lane (raw producer delete — NOT SDK delete_point, which emits PointRetracted and tombstones the fresh incarnation pre-sweep).
7. Re-emission ambiguity pin (raw producer re-emits same-id PointAdded after invalidate → rebuilt node resurrects live while live stays outdated — pin + document).
8. Invalidate-on-draft → promote → rebuild: pre-promote PointInvalidated fold survives (PointPromoted is NOT a boundary) AND rebuilt updatedAt is the promote fold's rebuild-now stamp, NOT the older journaled invalidate ts (skip_updated_at fired via max_inline_seq — the sweep fold did NOT clobber the later promote stamp; exact promote-time parity is out of scope per Task 4 step 4's known limitation). Pins the seq-gate.
9. Corrected_by-terminalized / missing endpoint: CORRECTS MERGE silently skips, fold returns ≥1, no warn — parity with #2423, document.
10. Extend `tests/test_event_store.py::test_all_mutations_emit` (84-90) to include invalidate (membership assertions — safe).
11. Run: `TORTOISE_TEST_CARVE_OUT=1 PYTHONPATH=$PWD .venv/bin/python -m pytest tests/test_pointinvalidated_rebuild.py tests/test_event_store.py tests/test_pointsuperseded_rebuild.py -q` — expect PASS.
12. Commit: `git add -A && git commit -m "test(rebuild): PointInvalidated fold parity, mixed, id-reuse"`

### Task 6: Docs

**Intent:** Catalog the new event + updated semantics.
**Acceptance:** event-catalog + ONTOLOGY rows updated with correct citations.
**Files:**
- Modify: `docs/event-catalog.md`, `docs/ONTOLOGY.md:181` (CORRECTS row + supersession-semantics para cites 3785→4124, 3870→4209)

**Steps:**
1. event-catalog.md: add PointInvalidated row (semantics: flag+CORRECTS, no status).
2. ONTOLOGY.md CORRECTS row + supersession-semantics paragraph: fix BOTH cites in THIS base — invalidate sdk.py:3785 → :4124 AND supersede sdk.py:3870 → :4209 (the #2421 docs amendment did not land in this worktree base; do not assume it).
3. Commit: `git add -A && git commit -m "docs: PointInvalidated event catalog + cite refresh"`

---
**Notes for executor:** env `TORTOISE_TEST_CARVE_OUT=1 PYTHONPATH=$PWD .venv/bin/python -m pytest ...` (worktree .venv may map to a sibling — verify `import tortoise` resolves to $PWD). CI docker is the authoritative golden-fixture lane. **Merge order: #2488 MUST merge BEFORE #2490** — #2490's invalidate rebuild-decay rides this issue's `_fold_point_invalidated` fold; merged second, the new fold replays without #2490's vacuity triple → rebuilt-invalidated claims read frozen posteriors (the class #2490 eliminates). Flag Task 3's `_fold_point_invalidated` as the decay-handoff surface #2490 must extend on rebase.

<!-- plan-review: cycles=6, status=clean, version=2.3.0 -->
