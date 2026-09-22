<!-- research-path: /tmp/scopes/2489-scope.md (archived on issue #2489) -->

# #2489 Structural-edge transfer parity — Implementation Plan

**Goal:** Journal supersede_point's 2b structural-edge transfer (extractedFrom + snapshot-derivable about* edges) as replayable DirectEdgeRepoint descriptors so rebuild_all reproduces live transfer state (successor keeps edges, old does not resurrect them).

**Team:** team:epistemic-team
**Role:** implementer

**Architecture:** supersede_point 2b MERGE+DELETEs structural edges with no event → rebuild pass-2 resurrects them at OLD from its immutable snapshot while the successor loses them (verified by live repro). Fix = emit flat descriptors {event_type, src, tgt=<key>, target_label=<label>} at 2b BEFORE the transfer (per-rel key extraction, never target.id), plus a pass-2b structural replay branch that routes on edge_type BEFORE the Point→Point MERGE, _final()s only src, resolves tgt with a SHARED label-scoped resolver (projection/edges.py — home of the rel→(label,key)+stub map, imported by both sdk.py emission and projection replay; never auto-detect `_create_about_edges`), and deletes the pass-2-resurrected edge at old via constrained re-query.

### Pattern Research
Skipped — zero third-party deps. Precedent: #2423 DirectEdgeRepoint machinery (collector 1435-1445, consumer 1688-1727, operator leg 1680-1686).

### Integration Surface Map
| Surface | Layer | Notes |
|---|---|---|
| supersede_point 2b transfer (sdk.py:4479-4505) | unit | descriptor emission + no-self-edge guard |
| pass-2b DirectEdgeRepoint consumer (projection/__init__.py:1688+) | integration (rebuild) | structural branch routes BEFORE Point-MERGE/_final(tgt) |
| entity key resolution (edges.py:114-215, _link_source) | integration | rel→(label,key) map: about*→name, aboutDocument→coalesce(title,name), extractedFrom→url |
| _upsert_point_edges snapshot resurrection (projection/entities.py:225-248) | integration (rebuild) | the edge being de-parity'd at old |
| raw create_edge / DirectEdgeCreated | N/A | out of scope (A10 family; no journal) |
| #2501 create_point aboutEntities live-wiring | N/A | dependency for full about* parity; indicator scoped to edges 2b transfers |

**Tech Stack:** Python 3.12, FalkorDB/Cypher.

---
## Tasks

### Task 0: Shared rel→(label,key)+stub resolver in projection/edges.py

**Intent:** One home for the key map used by emission (sdk.py), replay, and the delete-leg — two divergent copies would drift; `_create_about_edges` auto-detect (Subject-first order) cannot mint a specific aboutObject/aboutEvent/aboutDocument edge for an absent node (would attach the descriptor's rel to a wrong-label node).
**Acceptance:** Resolver exposed from projection/edges.py; sdk.py and projection both import it; no duplicate key map remains.
**Files:**
- Modify: `tortoise/projection/edges.py` (near `_create_about_edges` :114-215)

**Steps:**
1. Add label-scoped resolver: `resolve_structural_target(tx, label, key, rel) -> node_id_or_None` + `stub_key(rel, target) -> (label, key)`. Key map: aboutSubject/aboutObject/aboutEvent/aboutPoint → `name`; aboutDocument → `coalesce(title, name)`; extractedFrom → `url`. Create-if-missing semantics: Subject/Source stubs MERGE by key (live wiring precedent); **never** auto-detect labels and **never** mint Point stubs by name (absent Point target ⇒ skip).
2. `_create_about_edges` stays the live auto-detect label-discoverer (actual detect order per edges.py: Subject→Object→Action→Event→Document→Point — **do NOT pin a truncated order that drops Action**; Document name-OR-title matching; fallback Subject stub at s.id=$name). Action is snapshot-eligible via aboutEntities matching an Action node, but aboutAction edges are NOT in the derivable emission set (Action was dissolved in Ontology v3.0) — note this boundary explicitly. **Route only stub/edge CREATION through the resolver** (one create path for both live and replay); pin detect-order/coalesce/fallback with an equivalence test asserting live-vs-resolver edges match for the derivable rel set. **Equivalence cases to enumerate:** Document-by-name, Document-by-title-only (coalesce order), absent-name → Subject stub, absent-URL → Source stub (extractedFrom). Acceptance: no duplicate key map; resolver owns the create path; auto-detect remains the label-discoverer.
3. Run: `TORTOISE_TEST_CARVE_OUT=1 PYTHONPATH=$PWD .venv/bin/python -m pytest tests/ -q -k "about or source"` — expect PASS.
4. Commit: `git add -A && git commit -m "feat(projection): shared label-scoped structural-edge resolver"`

### Task 1: Descriptor emission in supersede_point 2b

**Intent:** Journal what 2b transfers so rebuild can replay it. Includes the missing no-self-edge guard.
**Acceptance:** 2b emits one flat descriptor per (old)-[rel]->(target) BEFORE transferring, for the snapshot-derivable rel set only; phantom (Y)-[:aboutPoint]->(Y) self-edge no longer mints when target == successor.
**Files:**
- Modify: `tortoise/sdk.py:4479-4505` (2b transfer)

**Steps:**
1. Extend the 2b SELECT (sdk.py:4488) to RETURN the key columns (target name / title / url per rel) — current query returns only id, target.id, labels(target). **Journal the target's LOGICAL id (`target.id`), never FalkorDB `ID(new)`/`id(target)`** — logical ids survive rebuild, internal ids die; the guard's node-identity compare uses `ID(new)` (runtime only, never journaled).
2. Per-rel key extraction via the shared resolver's `stub_key` (Task 0) — **never target.id** (Subjects MERGE by name, webhook stub ids random ulids #1918; Sources by url; Documents by name-or-title).
3. SKIP any target with an unresolvable key (name-less Point from id-targeted create_about_edge; extractedFrom target lacking url) — null-key descriptors are un-replayable; those edges die at rebuild today anyway (zero regression).
4. Guard (evaluate BEFORE emission and BEFORE any graph write): when the structural target IS the successor node (node-identity compare via ID(new)) — emit a **DELETE-ONLY descriptor**: `{event_type: "DirectEdgeRepoint", src=old_id, tgt=<direct successor id literal>, target_label=<label>, edge_type=<rel>, delete_only=true}`. **Scoped to the derivable rel set ONLY** (step 5's list) — a Point-targeted non-derivable rel (wasDerivedFrom/A10 family) must NOT emit a delete_only descriptor (permanent no-op delete-leg journaled for a family this fix doesn't own; live deletes of non-derivable edges need no descriptor). The consumer must recognize `delete_only` BEFORE its malformed-isinstance guard (a bare tgt-less event is currently dropped at the consumer ~1693 as malformed). Replay semantics: delete-leg keys on the DIRECT successor (the descriptor's literal tgt) — resolved through the succ map ONLY if that node id is itself superseded; the create-skip uses `_final(src)` (see Task 2 step 1). If the guard case emitted NO descriptor, the 2nd rebuild's pass-2 would resurrect (X:old)-[:aboutPoint]->(Y) from X's immutable snapshot and nothing would delete it — old-side zero-incident violated in the exact lane this fix targets. Successor pre-exists (validated live at :4262-4275).
5. Emit flat descriptor {event_type: "DirectEdgeRepoint", src=old_id, tgt=<resolved key>, target_label=<label>, edge_type=<rel>} for each surviving (rel, target) — event id gets a target-key suffix (multi-target same-rel id hygiene, sdk.py:2206 id base). Only the snapshot-derivable set: extractedFrom, aboutSubject/Object/Event/Document/Point. Exclude aboutAction/aboutSource/wasDerivedFrom (never snapshot-recreated — do NOT half-own the A10 raw family).
6. Keep the live MERGE+DELETE transfer as-is (with the new guard).
7. Run: `TORTOISE_TEST_CARVE_OUT=1 PYTHONPATH=$PWD .venv/bin/python -m pytest tests/test_pointsuperseded_rebuild.py -q` — expect PASS (no regression).
8. Commit: `git add -A && git commit -m "feat(sdk): journal 2b structural-edge transfer + no-self-edge guard"`

### Task 2: Pass-2b structural replay branch

**Intent:** Rebuild replays the descriptors: edge lands at the final successor; the pass-2 resurrection at old is deleted.
**Acceptance:** Post-rebuild, structural edges exist at the final successor and NOT at old; dedupe keyed on RESOLVED TARGET NODE IDENTITY (never descriptor id, never name/key — Objects/Events/Documents are id-keyed, two distinct same-name targets must not collapse); double-rebuild idempotent; old-side delete-leg always runs even on a dedupe-skip.
**Files:**
- Modify: `tortoise/projection/__init__.py` (DirectEdgeRepoint consumer ~1688-1727)

**Steps:**
1. At the consumer loop top, handle `delete_only` descriptors BEFORE the malformed-isinstance guard (consumer ~1693 currently drops any event whose src/tgt/etype isn't str — the delete_only event carries a literal logical tgt id so it survives, but recognition must be explicit). **Delete-leg keys the DIRECT successor (the descriptor's literal logical tgt), NOT `_final(src)`** — in a chain (guard fires supersede(X→Y), then Y→Z superseded before rebuild), pass-2 resurrects the phantom at old from src's snapshot to the DIRECT successor Y, while `_final(X)=Z`; a final-keyed delete misses and old X keeps its phantom. Resolve the literal tgt through the succ map only if that node id was itself superseded (then follow to its successor — direct node may be gone). **Chain-mechanism pin:** resurrection is name-based (`_create_about_edges`/`_link_source`, entities.py:225-248 — `_try_about_edge` has no status filter), so in X→Y→Z the phantom may attach to dead Y while the delete-leg targets Z — delete BOTH the literal node and the succ-resolved node (or pin the test to assert both are clean; Task 3 step 5 protects it). Use `_final` solely for the create-skip. Run the resurrection-delete `(old:Point{id:src})-[r:edge_type]->(<direct successor node>)` with fresh ID(r) capture, skip create, continue through dedupe/terminal-guard. Never graph-wide.
2. Discriminate structural edge_type ∈ derivable set BEFORE validate_rel_type/Point→Point MERGE/_final(tgt) (a bare entity key run through _final would corrupt on superseded-point-id collisions). Keep a single validation point: still call `validate_rel_type` inside the branch (fixed-set discriminator is an allowlist, not a substitute).
3. Route: `_final()` only src. Resolve tgt via the shared resolver (Task 0) with label-scoped semantics — **never auto-detect `_create_about_edges`** (Subject-first auto-detect would attach an aboutObject descriptor to a same-name Subject; the delete-leg's rel-constrained re-query `(old)-[:rel]->` then misses the drifted resurrection and old keeps a phantom edge). Create-if-missing: Subject/Source stubs MERGE by key; **never** mint Point stubs by name — absent Point target ⇒ skip.
4. Skip attr SET (2b transfers bare). No direction/confidence/weight fields on structural descriptors.
5. Resurrection-delete (normal descriptors): replay-time re-query constrained to `(old:Point{id:src})-[:rel]->(resolved_tgt)` with FRESH ID(r) capture (mirror operator leg 1680-1686) — never a graph-wide pattern delete, never a descriptor-carried rid (internal ids die at rebuild). Run the delete-leg BEFORE any dedupe skip so a dropped duplicate still cleans its old-side resurrection. **Pass-ordering constraint (pinned):** the delete-leg's correctness depends on entities.py snapshot resurrection (which creates the phantom at old) running BEFORE descriptor replay — this holds by pass structure (pass-2 resurrection precedes the pass-2b sweep); if a future phase reorder moves snapshot resurrection later, the delete-leg must tolerate late resurrection — comment this in code.
6. Dedupe seen-set keyed on `(src, edge_type, resolved_target_node_id)` — mirroring the operator leg's (op, type, idx, final). NOT descriptor id (id duplicates across multi-target same-rel; id-keyed dedupe collapses 2 distinct subjects); NOT resolved-target identity alone (two legit transfers to a SHARED node in one journal — X1→S, X2→S — would collapse and silently drop X2's edge after its delete-leg ran). Scope is per-rebuild. MERGE collapses duplicate creates; the seen-set only prevents double-create.
6b. **Route structural descriptors through #2488's survivor filter (2nd-model P1):** #2488's rebuild drops pre-recreation supersede folds (seq ≤ `last_recreate_seq[id]`) so a re-created incarnation stays live, and its succ map binds to survivors only. Structural DirectEdgeRepoint descriptors must obey the SAME drop — unconditional replay would transfer a re-created live incarnation's structural edges to the successor (the id-reuse lane #2488 Task 5 step 6 pins).
7. Dual-label name collision (same-name Subject+Object): label-scoped resolution at replay prevents most drift; document the residual + add a collision test (Task 3).
8. Terminal-guard parity with the operator leg.
9. Run: existing rebuild suites — expect PASS.
10. Commit: `git add -A && git commit -m "feat(projection): structural DirectEdgeRepoint replay branch"`

### Task 3: tests/test_pointsuperseded_rebuild.py structural parity cases

**Intent:** Pin parity end-to-end + the edge cases verified at scope time.
**Acceptance:** listed cases green; old-side zero-incident; phantom self-edge guard + 2nd-rebuild delete verified; id-reuse dedupe; mixed 2a+2b; collision.
**Files:**
- Modify: `tests/test_pointsuperseded_rebuild.py`

**Steps:**
1. `_struct_edges` helper (query structural edges from a point).
2. Parity via extractedFrom: A→B→C chain — post-rebuild successor has {extractedFrom}, old has {} (old-side zero-incident, E2E-11.6 mirror).
3. REBUILD→supersede→REBUILD lane (only lane where 2b sees live about edges): assert about edges follow the successor post-2nd-rebuild AND old carries no about edge after the 2nd rebuild.
4. No-self-edge guard (explicit TWO-rebuild sequence): (i) rebuild 1 makes X-[:aboutPoint]->Y from X's snapshot; (ii) supersede(X→Y) emits the delete_only descriptor; (iii) rebuild 2 replays it → assert old X has NO aboutPoint edge (delete-leg fired on the resurrected (X)-[:aboutPoint]->(Y)) and Y has NO (Y)-[:aboutPoint]->(Y) self-edge. Assert both live and post-rebuild states.
5. **Chain-gated guard case** (delete-leg keys the DIRECT successor, not _final): supersede(X→Y) with the X-[:aboutPoint]->Y phantom, THEN supersede(Y→Z), rebuild 2 → assert old X is clean (delete-leg followed the direct successor Y through the succ map), Y has no self-edge, Z unaffected.
6. Mixed 2a+2b supersede: one supersede transferring BOTH an operator edge and a structural edge — assert both replay (2a regression pinned in-file, not just by full-suite run).
7. Multi-edge: two distinct sources sharing one target node (X1→S, X2→S) — dedupe keyed (src, edge_type, resolved-id) keeps BOTH; two same-name Subject targets (distinct ids) — not collapsed.
8. aboutDocument-title keyed resolution.
9. Name-less aboutPoint target + extractedFrom-without-url target: skipped at emission, no crash.
10. Double-rebuild idempotence.
11. P2-3 id-reuse dedupe for structural: dedupe keyed on resolved node identity, no parallel edges; old-side delete still runs on the duplicate descriptor.
12. Resolver stub-mint equivalence (Task 0.2 mirror): replay against absent entities mints the same stubs live wiring would — Document-by-name, Document-by-title-only, absent-name → Subject stub, absent-URL → Source stub.
13. Dual-label collision (same-name Subject+Object): label-scoped resolution + delete-leg behavior documented/pinned.
14. Pre-fix journal boundary: descriptors exist only for post-deploy supersedes — a pre-fix journal rebuild replays with no delete-leg (old's resurrection persists). Acceptance states rebuild does NOT repair pre-existing graphs; #2500-style backfill is out of scope — note in plan + issue.
15. Run: `TORTOISE_TEST_CARVE_OUT=1 PYTHONPATH=$PWD .venv/bin/python -m pytest tests/test_pointsuperseded_rebuild.py -q` — expect PASS.
16. Commit: `git add -A && git commit -m "test(rebuild): structural-edge transfer parity cases"`

### Task 4: Docs

**Intent:** Document that 2b is journaled/replayable + the derivable-set boundary.
**Acceptance:** ONTOLOGY §3.1 note + INGEST_CONTRACT supersede row updated.
**Files:**
- Modify: `docs/ONTOLOGY.md`, `docs/INGEST_CONTRACT.md`

**Steps:**
1. ONTOLOGY §3.1: structural-edge transfer is journaled (DirectEdgeRepoint) and replayable; derivable-rel set + #2501 dependency noted. **Pre-fix journal boundary stated**: descriptors exist only for supersedes journaled post-deploy; a pre-fix journal rebuild replays with no delete-leg → old's resurrection persists (rebuild does NOT repair pre-existing graphs; backfill out of scope).
2. INGEST_CONTRACT supersede row: 2b journaled/replayable.
3. Commit: `git add -A && git commit -m "docs: supersede 2b structural transfer journaled"`

---
**Notes for executor:** env `TORTOISE_TEST_CARVE_OUT=1 PYTHONPATH=$PWD .venv/bin/python -m pytest ...`. **Merge order: #2488 MUST merge FIRST** — this plan's pass-2b structural branch consumes #2488's survivor-filtered succ map (2nd-model P1: unconditional structural replay would transfer a re-created incarnation's edges in the id-reuse lane); then #2490. #2501 remains the dependency for full about* parity (create_point never live-wires aboutEntities). **Task 1 (emission) and Task 2 (replay branch) must land in ONE release** — between the two commits, a journal holding fresh structural descriptors rebuilt through the OLD Point→Point consumer runs the entity key through `_final(tgt)` and `(b:Point{id:key})` → silent drop or wrong remap on key∩succ-map collision. Task 1's CI passes only because its fixtures predate descriptors — do not ship emission without the branch.

<!-- plan-review: cycles=5+second-model, status=clean, version=2.3.0 -->
