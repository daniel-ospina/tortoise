<!-- research-path: in-repo (#2194 merged A1 design as the template + #2295 scoping comment 5555938610) -->

# #2295 — journal SubjectAdded for SDK-created Subjects

> **Pipeline:** project-workflow (Level: project, complexity: standard). Scope approved by human gate. **Template:** the merged #2194 fix (PR #2309) — same `_create_entity` funnel, probe-gated A1 design, test suite shape. This plan is the Subject twin; deltas vs #2194 are enumerated in §Delta and are the ONLY places this plan differs from the fully-reviewed Object work.
> **Base:** worktree `feat/2295-subjectadded-journaling` @ origin/main (83d96301 — #2194 IS in this tree).
> **Scope comment:** #2295 comment 5555938610 (3-agent scope, all converged, no P0/P1).

## Problem (confirmed, verified against code)

SDK Subject creates (`create_entity("subject")` → `_create_entity` sdk.py:14459 with `"SubjectAdded"` + canonical `sub-<sha26>` id via `_entity_name_id("Subject", name)` sdk.py:1081-1093) are **live-graph-only** on journal-enabled SDKs. `rebuild_all` pass-1b HAS the replay consumer (`SubjectAdded → _upsert_subject`, projection/__init__.py:1135-1136 apply + :1387-1388 pass-1b — pre-existing because EventAPI `add_subject` journals unconditionally, api.py:240-246) — but no SDK write ever produces the record → journaled-SDK Subjects **vanish silently on rebuild**. Subjects have no fold/supersede vocabulary → no fold-miss warning surfaces the loss (total + silent; worse than the pre-fix Object case). `SubjectAdded ∉ _GRAPH_EVENT_TYPES` (sdk.py:718-728) → JSONL-only emission, no GraphEvent-store double-write.

## Architecture (A1-mirror — 3 code edits)

When an event log is configured, `label == "Subject"`, `event["name"]` truthy, and `event["type"] == "SubjectAdded"` (conjunct mirrors pass-1b's literal dispatch — a future Subject-label event type can never journal an unreplayable line): pre-apply probe `MATCH (s:Subject {id:$cid, name:$name}) RETURN s.id` discriminates first canonical registration (no row → fresh create OR stub adoption → journal) from canonical re-mention (row → skip; the issue's only-on-create mandate). Probe failure → warning + fail-open-to-journal (durable bias). On the journaling path only, synthesize `event["createdAt"] = now_iso()` pre-apply (live == journal == replay; #2164-P4 drift class closed). Post-apply `_emit_event(event["type"], id=event["id"], **mirror-minus-exclusion)` with the identical exclusion tuple as the Object block. Journal-less SDKs (hosted/MCP/CLI — no `event_log_path`) stay byte-identical EXCEPT the unconditional `point`/`payload` drop (delta 2 — same S1 bound as #2061/#2164/#2193/#2194).

### Δ Deltas vs #2194 (the places the mirror differs — all verified)

1. **entities.py `_upsert_subject` ON MATCH createdAt adoption is MISSING** — #2194 added `o.createdAt=coalesce(o.createdAt, $ca)` to `_upsert_object` (entities.py:353) for stub-adoption byte-identity, but `_upsert_subject` (entities.py:271-313) never got it: ON CREATE sets `s.createdAt=coalesce($ca,$now)` (:300-301), ON MATCH sets only `id`/`subjectKind`/`embedding` (:302-304). **Load-bearing**: a raw-created name-stub under a random ulid (the real connector shape, `_event_plain_merge` entities.py:657-666 — no createdAt) adopted by an SDK create would keep a createdAt-less live node while replay's ON CREATE stamps the journaled value → byte-identity (indicator 2) fails on the stub path. **Edit: add `s.createdAt=coalesce(s.createdAt, $ca)` to the ON MATCH clause** (+ the #2194-style comment; idempotent — existing value wins; EventAPI-mention parity: a createdAt-less stub adopted by any mention gets stamped).
2. **The `point`/`payload` reserved-pop tuple widens** from `("Event", "Object")` to `("Event", "Object", "Subject")` (sdk.py:14502) — unconditional, both lanes (the #2194 divergence rationale: without it a tenant passing `point`/`payload` on `create_subject` persists them live via `_persist_extra_props` while the journal mirror drops them → divergent live/replay). Tenant-visible narrowing → ONTOLOGY note.
3. **`status` is an extra-prop for Subject, NOT projection-owned** — `_SUBJECT_HANDLED` (entities.py:74-76) lacks `status` (contrast `_OBJECT_HANDLED` :77-82, #1350 clobber guard). SDK `create_entity("subject")` always injects `status: "live"` (sdk.py:14753) → persists via `_persist_extra_props` (`SET n += $extra`, monotone-union, order-independent) on BOTH live and replay lanes. Consequences: (a) the stub-adoption test asserts **status PRESENT live** (INVERTED from #2194 Object test 5's status-absent); (b) ONTOLOGY §4.2 status row (docs/ONTOLOGY.md:349 — "❌ planned, not implemented") is stale — SDK subjects already carry `status:'live'`; updated.
4. **EventAPI `add_subject` has no canonical id override** (api.py:240-246 mints a random ulid; contrast `add_object`'s deterministic `id=` at api.py:248-257). A random-ulid SubjectAdded mention landing between SDK creates re-ids the live node (#1918 accepted trade-off, entities.py:283-291) → the next SDK create probe-misses → a second canonical line. Replay converges (id coalesce last-write-wins on both lanes; `_persist_extra_props` union order-independent). **Pinned as by-design** (net-new test — the Object suite has no precedent because Object producers use canonical ids). The #2194 mirror exclusion tuple is reused unchanged.
5. **No fold vocabulary** → #2194's fold-specific machinery/tests deliberately NOT ported (ObjectSuperseded sweep, fold-miss warning, registration-before-fold line order, fold-side append failure).

## Constraints (verified anchors, current tree)

| # | Constraint | Anchor |
|---|---|---|
| C1 | Probe: canonical id + name conjunct (`MATCH (s:Subject {id:$cid, name:$name})`), truthy-name gate, event-log guard, fail-open-to-journal on exception | sdk.py probe block mirrors :14547-14561 |
| C2 | createdAt synthesized INTO the event dict pre-apply ONLY on the journaling path | sdk.py :14562-14572 pattern |
| C3 | Emission post-apply, JSONL-only (`SubjectAdded ∉ _GRAPH_EVENT_TYPES` — no GraphEvent double-write) | sdk.py :14605-14631 pattern; `_emit_event` :1948+ |
| C4 | Exclusion tuple identical to Object: `("type","id","point","payload","event_id","ts","initiated_by","projection_version")` | sdk.py :14627 |
| C5 | ON MATCH createdAt coalesce added to `_upsert_subject` (existing value wins — never clobbers) | entities.py :302-304 (delta 1) |
| C6 | Replay consumers pre-exist (apply + pass-1b) — no projection/__init__.py change | projection/__init__.py :1135-1136, :1387-1388 |
| C7 | Journal-less SDKs: probe never fires (no `event_log_path`); `point`/`payload` pop unconditional both lanes (S1 bound) | sdk.py :14502 gate + `_event_log_path` |
| C8 | `event["type"] == "SubjectAdded"` conjunct in the gate (pass-1b dispatches the literal) | sdk.py :14547 pattern / projection :1387 |
| C9 | status rides extra-props on both lanes → no MERGE status clause (C3 delta); stub test asserts status PRESENT | entities.py :74-76, :117-133 |
| C10 | `create_entity("subject")` always sets `subjectKind` (default `"other"`) + `subject_kind` snake injected pre-apply | sdk.py :14750-14756, normalize :14490-14491 |

## Test plan (Subject twin → `tests/test_subjectadded_journal.py`)

Docker lane (`TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix'`); helpers generalize from the Object file (`_journaled` generic; add `_name_sas(journal, name)`, `_subject_row(proj, name, *props)` — `RETURN properties(o)` row-shape quirk noted; per-test `tmp_path` + distinct DB names; `==` not `is` on DB bools).

**T1 — RED tests (fail at base for the right reasons; ~11 methods / 12 cases):**
1. `test_plain_subject_survives_rebuild_all_byte_identical` — parametrized ASCII + `estrategia-ñ-日本語-💡`: exactly one `SubjectAdded`; `line["id"] == _entity_name_id("Subject", name)` (sub- prefix); `subject_kind`; `status == "live"`; `is_episodic` when passed; synthesized `createdAt`; live props == rebuilt props minus `embedding`; `createdAt == line["createdAt"]` (no rebuild-time drift). RED at base (no journal line → node vanishes).
2. `test_remenion_does_not_double_journal_and_prop_churn_is_live_only` — second create of same name → still one line; journal keeps FIRST-registration props; live `subjectKind` churn live-only; rebuild reverts.
3. `test_stub_adoption_journals_canonicalization` — raw `CREATE (s:Subject {name, id:ulid})` (faithful to entities.py:657-666) → SDK create → one line; live `id == line["id"]`; **live `createdAt == line["createdAt"]`** ← RED specifically for delta 1 pre-fix (stub node createdAt-less live); **live `status == "live"` PRESENT** (delta 3 inversion); post-rebuild id/createdAt/status match; journal carries status/title-free line the rebuilt node gets via extras.
4. `test_duplicate_registration_lines_replay_idempotently` — two manual canonical lines with different createdAt + rebuild → one node, createdAt == FIRST (delta-1 clause shape pin; first-wins).
5. `test_journaled_line_and_live_node_drop_reserved_props` — scalar `point="x"`/`payload="y"` props: journal line excludes them AND live node lacks them (delta 2); envelope keys top-level only; post-rebuild no envelope pollution; **GraphEvent pin: `MATCH (e:GraphEvent {type:'SubjectAdded'}) RETURN count(e)` == 0**.
6. `test_no_log_sdk_no_journal_and_unconditional_pop` — journal-less SDK: no journal dir; `point`/`payload` still dropped live (delta 2 unconditional); no synthesis.
7. `test_probe_failure_fails_open_to_journal` — fragment-scoped monkeypatch (`"s:Subject {id:$cid, name:$name}"` — label-scoped so the Object probe tests aren't affected) raising → warning + optimistic journal; create succeeds; rebuild restores.
8. `test_log_append_failure_warns_and_keeps_live` — class-level `EventLog.append` raise → warning; live node present; rebuild omits (accepted: live-but-not-durable ≡ pre-fix for that write).
9. `test_deleted_subject_resurrects_on_rebuild` + `test_delete_recreate_replays_first_incarnation` — accepted-divergence pins (no tombstone; `_delete_entity` covers Subject id): delete → rebuild resurrects live with journaled createdAt; delete→recreate → 2 lines, live carries second createdAt, replay first-wins first. (#2296 hook.)
10. `test_eventapi_random_ulid_mention_between_sdk_creates_is_by_design` — **net-new**: SDK create S (line A) → `api.add_subject(S)` random ulid (re-ids live node) → SDK re-mention S (probe misses → line C) → rebuild replays A,B,C → node canonical id + first-line createdAt, byte-identical to live; exactly 2 SubjectAdded lines on the SDK log (B lives on the API's own log). Documents the by-design double-registration class (delta 4).

**T2 — GREEN (3 edits):** sdk.py probe+journal gate extension (`label in ("Object","Subject")` — the Object branch already exists; add the Subject conjunct set mirroring C1/C2/C3/C4/C8 — cleanest as a shared `_journal_first_registration` path keyed on the (label, event_type) pair OR a second gate block mirroring the Object one; PREFER the shared-path refactor ONLY if it leaves the Object block byte-identical — otherwise duplicate the block with a Subject label guard, matching the reviewed Object shape exactly); pop tuple widen (C7/delta 2); entities.py ON MATCH createdAt coalesce (C5/delta 1). Post-GREEN pins: tests 1-10 all pass; Object suite (16) + status-projection baseline (19) still pass — the Subject changes must not perturb the Object path (a shared refactor must be diff-minimal or re-run the full Object suite).

**T3 — docs sweep:** ONTOLOGY §4.2 Subject "registration durability (#2295)" note mirroring §4.3 (:371 — probe gate/fail-open/EventAPI-random-ulid by-design class/node-property byte-identity scope/residual non-durable classes incl. delete resurrection + first-wins/reserved narrowing); §4.2 status-row correction (delta 3b: `status:'live'` rides the extra-props path for SDK-created subjects — the projection-owned status is the planned follow-up); §4.3 residual-class cross-ref ("and Subjects, #2295"); sdk.py `_create_entity` docstring + `event_log_path` class comment mention Subjects. event-catalog.md: NO change (GraphEvent catalog only; SubjectAdded has no row — verified). Grep-sweep acceptance: `grep -rn "journal EventRecorded for Events only\|Objects are NOT journaled\|SDK capture-created Objects" tortoise/ tests/ docs/` → only historical/plan text remains.

**T4 — verification:** Subject twin + test_object_registered_journal.py (16) + test_status_projection.py (19) + test_projection.py + test_semantic_extractor.py + test_p1_differentiators.py + test_entity_stage.py + capture/ingest/commit suites (docker lane) + embedded carve-out spot check + ruff clean on touched files + `config/ci-surfaces.yml` registration (drift gate #1262 — new test file MUST be registered or python-ci's Manifest integrity check fails).

## Accepted divergences (documented, not silently expanded — mirror #2194's list, Subject-shaped)

- **Pre-#2295-history ghost**: canonical Subjects created pre-fix + first post-fix re-mention probe-skips (no registration in this journal) → rebuild drops pre-fix population. No backfill in scope (#2296: Object/Subject backstop).
- **Re-mention prop mutations live-only** (only-on-create mandate); journal keeps first registration.
- **Delete non-durability**: `_delete_entity` leaves no tombstone → deleted Subjects resurrect on rebuild; delete→recreate replay first-wins the earlier incarnation's createdAt. Pinned as accepted by tests 9a/9b; **#2296 scope hook** (write-surface invariant must cover deletion).
- **Mixed-producer id churn**: EventAPI random-ulid `add_subject` between SDK creates → by-design second canonical line + re-id (delta 4, pinned by test 10); pre-existing #1918/#330 class.
- **Ownership/org edges not replayed** for Subjects (node-property byte-identity scope — SDK `create_subject(org_of=...)`-class edges are SDK-layer, EventAPI parity).
- **Unjournaled/journal-less producers** in a shared graph: rebuild drops their Subjects regardless (no canonical registration ever journaled).
- **`status:'live'` extra-prop regime** stays as-is (projection-owned Subject status is a planned ontology follow-up, NOT this issue).

## Failure modes (each → guarded)

| Mode | Guard |
|---|---|
| Probe raise (DB hiccup) | fail-open-to-journal + warning (test 7) — duplicate replay-safe |
| Log append failure | warn + live node kept; rebuild omits (test 8) — ≡ pre-fix for that write |
| Duplicate lines (probe TOCTOU / by-design EventAPI class) | idempotent MERGE + first-wins createdAt (test 4, 10) |
| Falsy name | truthy-name gate (no phantom line) — mirrors `_upsert_subject` early-return entities.py:274-276 |
| Future Subject-label event_type ≠ SubjectAdded | event-type conjunct in gate (C8) — never journals an unreplayable line |
| SubjectAdded added to `_GRAPH_EVENT_TYPES` later | test 5 GraphEvent count == 0 pin |
| ON MATCH createdAt clause direction flipped | test 4 first-wins pin (a reversed coalesce would fail it) |
| Shared-path refactor perturbing Object behavior | Object suite (16) + status-projection (19) green after T2 |

## Acceptance criteria ↔ indicators

| Indicator (issue) | Proof |
|---|---|
| 1. `_create_entity` journals SubjectAdded on first canonical registration (probe-gated, createdAt/is_episodic parity) | T2 edits; tests 1-3, 7 |
| 2. SDK-created Subject survives `rebuild_all` byte-identically | tests 1 (full prop-set), 3 (stub path), 4 (dups) |
| 3. Existing rebuild tests green | T4 suites (Object 16 + status-projection 19 + projection/semantic/p1/entity/capture/ingest/commit) |

## Out of scope (documented — filed/known follow-ups)

- **Document + Source SDK journaling** — separate gaps (create_document/create_source route `_create_entity` with `DocumentCreated`/`SourceCreated` but no journal block — sdk.py:17106/:17118 region). **Owned by #2296** (the durability write-surface audit) — NOT one-off issues.
- **Edges durability** (Object/Subject ownership edges, capture aboutObject/CONTAINS) → #2296.
- **Projection-owned Subject status** (ontology marks planned) — future feature.
- **EventAPI `add_subject` canonical id override** — #1918 accepted trade-off; blast-radius limited.

## Runtime prerequisites

- Docker FalkorDB up on localhost:6379; `TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix'` for docker-lane runs.
- Worktree `feat/2295-subjectadded-journaling` @ 83d96301; `.venv` symlinked.

## Commit plan (mirror #2194's history shape)

1. `test(#2295): RED — SubjectAdded journaling behaviors (tests 1-10)`
2. `feat(#2295): journal SubjectAdded on first canonical registration (probe-gated, createdAt-synthesized, post-apply) + pop widening + _upsert_subject ON MATCH createdAt adoption`
3. `docs(#2295): ONTOLOGY §4.2 durability note + status-row correction + docstring sweep`
4. `chore(#2295): ci-surfaces registration + ruff` (or folded into 1/3 as needed)
5. commit-workflow: PR + code-review gates + merge.

<!-- plan-review: pending -->
