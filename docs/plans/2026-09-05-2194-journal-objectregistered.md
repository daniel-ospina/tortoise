<!-- research-path: in-repo (#2194 scoping comment 5551912491 + #2164 fold sweep + sdk._create_entity EventRecorded = the references) -->

# #2194 — Journal `ObjectRegistered` for capture-created Objects — capture folds survive `rebuild_all`

> **For Pi:** Use `executing-plans` to implement this plan task-by-task.

**Goal:** Capture-created Objects (and their #1350/#2164 `ObjectSuperseded` folds) survive `rebuild_all` by journaling `ObjectRegistered` on first canonical registration — closing the OD2 rebuild boundary documented at projection/__init__.py:1417-1434 and test_status_projection.py:160-164.

**Team:** epistemic-team
**Complexity:** standard (Architecture: standard, Ontology: low — no new vocabulary)

**Architecture:** SDK-local, probe-gated emission in `_create_entity` (A1 — scoping decision, verified across problem-verify/solution-verify/second-model/Phase-7 gates). When an event log is configured and the label is Object, a pre-apply existence probe on the canonical deterministic id (`obj-<sha26(name)>`) discriminates a canonical re-mention (row exists → skip; the issue's only-on-create mandate) from a first canonical registration (no row → fresh create OR #1155 stub adoption → journal). `createdAt` is synthesized into the event dict pre-apply so live, journal, and replay carry the identical value (#2164-P4 drift class). Emission is post-apply (phantom-event ordering hazard — a journaled registration whose live apply never happened would replay-create a phantom node) through the existing `_emit_event` JSONL-only path (`ObjectRegistered ∉ _GRAPH_EVENT_TYPES` → no GraphEvent-store double-write). Rebuild consumers already exist: pass-1b `_upsert_object` (projection/__init__.py:1389) + the deferred fold sweep (:1417-1434). No-op when `event_log_path` is unset (S1 bound — journal-less SDKs stay byte-identical; same bound as #2061/#2164/#2193).

### Pattern Research
> **Findings date:** 2026-09-05
> Gate skipped: plan touches zero third-party dependencies — pure in-repo refactor onto existing mechanisms. Axis research (Architecture = medium+) fired 2 external queries; findings in the scoping comment §External Research. PRIOR_RESEARCH: #2164 full scoping + 6 fresh-reviewer code-review cycles of the fold machinery; the #2194 scoping ran problem-verify/solution-verify/second-model(coherence)/Phase-7 review gates — every anchor re-verified against origin/main code by multiple independent reviewers.

**In-repo precedents (load-bearing):**
- **EventRecorded mirror** (sdk.py:14026-14051): emit after `proj.apply`, full applied-dict mirror minus `("type","id","point","payload","event_id","ts","initiated_by","projection_version")`, JSONL-only type, best-effort, no sibling double-emission.
- **EventAPI `add_object`** (api.py:248-261): snake `object_kind`, `createdAt=now_iso()` stamped at emit — the reference producer shape + createdAt convention.
- **Pre-write existence probe** (bundle-ingest sdk.py:6280-6290): `MATCH (n:Object {name:$name}) RETURN n.id` before create — in-repo probe precedent. #2194 probes by **canonical id** (not name): a name-probe would wrongly skip stub adoption (node exists under a random ulid → name-probe hits, but the canonical registration is genuinely new).
- **#2164 pass-1b fold sweep** (projection/__init__.py:1391-1434): `ObjectSuperseded` folds deferred past all registrations; 0-row fold warns "(OD2 capture gap?)" — the exact gap this issue closes (warning + comment reworded in T4).
- **#2164 P4** (entities.py:394-411): replay must prefer the journaled envelope `ts` for `supersededAt` — createdAt synthesis is the same drift class on the registration side.
- **SourceCreated merge-attribution** (entities.py:979-1002; sdk.py:15421-15427): `nodes_created` NOT race-safe on the embedded backend under concurrent same-key MERGEs — reason the solution uses a probe (backend-agnostic, in-process-serial capture) rather than threading MERGE statistics through the shared `apply()` contract (A2, rejected).

### Integration Surface Map
| Surface | Boundary | Test layer | Where |
|---|---|---|---|
| `_create_entity` Object journaling (sdk.py) | in-process seam | integration (docker lane) | T1/T2 new file: ON CREATE only; re-mention no double-journal; stub adoption journaled; no-log gate |
| `rebuild_all` round-trip (capture Object + fold) | event-store replay | integration | T1 round-trip: status + supersededBy + supersededAt == journaled fold `ts` |
| Replay byte-identity | event-store replay | integration | T1: journaled shape (id/name/object_kind/status/createdAt/is_episodic); no envelope-key node pollution; createdAt parity |
| `apply_supersessions` emission (commit_ops.py:440-444) | event stream | integration | T1 round-trip drives the real production fold lane |
| Existing rebuild tests | regression | docker lane | T3 migration + T5: test_status_projection.py (19 baseline) |
| Journal-less SDKs (hosted/MCP/CLI) | no-op gate | integration | T1 no-log test: no line, node props unchanged |
| EventAPI/connector producers (api.py, mining, github) | distinct seam | regression | T5: test_entity_stage / test_semantic_extractor unchanged |

### Failure Modes
- **Probe TOCTOU** (concurrent same-name create, both probe-empty) → two journal lines → replay idempotent (MERGE by name; ON MATCH never touches status; first line's createdAt wins) → accepted, documented in the emission comment.
- **Probe failure (query raises)** → **fail-open-to-journal** with a warning (durable bias — a duplicate line is replay-safe and matches the EventAPI unconditional precedent; a skip would silently re-open the node-loss bug). Wrapped in try/except around the probe only.
- **Log append failure** → `_emit_event` best-effort warn-and-continue (existing sdk.py:1892-1904); the Object is live-but-not-durable for that write (≡ pre-fix; no regression). No Object #548-snapshot backstop exists — **accepted and documented** (loss-backstop tracked in #2296).
- **Re-mention prop churn** (title/objectKind mutated on a later ON MATCH mention) → journal keeps first-registration props; rebuild restores first-registration state (live-only for the mutation). **Accepted divergence** (issue's only-on-create mandate; capture writes only name+kind+is_episodic) — documented in the emission comment + pinned in a test.
- **Pre-#2194-history ghost** (canonical Object created pre-fix, superseded post-fix, re-mentioned post-fix) → probe hits → skip → still no registration in this journal → fold-miss on rebuild. **Accepted** (no backfill in scope — first post-fix rebuild loses pre-fix population; disaster-recovery journal semantics; reworded fold-sweep comment names the residual sources).
- **Mixed-producer id churn** (EventAPI `obj_`-scheme log + SDK `obj-`-scheme log covering one name in one rebuild dir) → last file-sorted registration's id wins on replay; pre-existing cross-producer class (#330-documented) — no mechanism change; probe-by-id retained (a superseded same-name Object must NOT suppress the canonical registration).

**Tech Stack:** Python 3.12+, FalkorDB docker lane (`TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix'`), `uv run pytest`, EventLog JSONL. No new deps.

---

### Task 1: RED — new journal-behavior tests

**Intent:** Pin every mandated behavior BEFORE code changes: capture→rebuild→fold round-trip (indicator 2), plain unfolded Object survival (indicator 1+3), re-mention no-double-journal (only-on-create), stub-adoption canonicalization survival, createdAt parity + no envelope pollution (byte-identity), no-log gate, re-mention-after-fold no-resurrect.
**Acceptance:** New tests exist and FAIL for the right pre-fix reason (zero ObjectRegistered lines → Object absent post-rebuild; no double-journal guard yet). Baseline 19 in test_status_projection.py untouched.
**Files:**
- Create: `tests/test_object_registered_journal.py` (docker lane — NOT in the tests/_embedded.py carve-out)

**Step 1.1** — Write the tests (all use `TortoiseSDK(str(tmp_path / "<n>.db"), event_log_path=str(events / "events.jsonl"))` with `events.mkdir()`; `sdk.close()` in `finally`; distinct DB paths per SDK pairing so docker-lane redirect hashes don't collide):

1. `test_capture_object_and_fold_survive_rebuild_all` — create successor + target Objects via `create_entity("object", name, objectKind=..., is_episodic=False)` (the capture shape), fold via `apply_supersessions(proj, sdk, [{"superseded": "strategy-A", "supersedes_by": "strategy-B", "evidence": "capture fold"}], session_id="s1")`, then `proj.rebuild_all(str(events))`. Assert: node exists; `status == "superseded"`, `supersededBy == "strategy-B"`, `objectKind == "core:strategy"`, `is_episodic is False`; **`supersededAt == the journaled ObjectSuperseded envelope ts`** (NOT the live node's supersededAt — live folds stamp `now()` micros later; assert against `[e for e in EventLog(...).read_all() if e["type"] == "ObjectSuperseded"][-1]["ts"]`).
2. `test_plain_object_survives_rebuild_all_byte_identical` — create one Object (no fold), read the journal: exactly one `ObjectRegistered` with `id == _entity_name_id("Object", name)`, `name`, `object_kind`, `status == "live"`, `createdAt` present, `is_episodic` matching. Rebuild → node present; `createdAt == journaled createdAt` (no rebuild-time drift).
3. `test_remention_does_not_double_journal` — create twice (second = canonical re-mention, ON MATCH). Journal holds EXACTLY ONE `ObjectRegistered` for the name; both calls return the same canonical id (#452); one Object node.
4. `test_remention_after_fold_does_not_journal_or_resurrect` — create A+B, fold A→B, then `create_entity("object", "strategy-A", ...)` again (superseded name re-mentioned): zero new ObjectRegistered lines (total stays 1 for A), live A stays `superseded`, and after `rebuild_all` A is still `superseded` (fold line already in journal; no resurrect).
5. `test_stub_adoption_journals_canonicalization` — pre-create a name-stub Object with a random ulid id (raw `proj.g.query("CREATE (o:Object {name:$n, id:$id})", ...)` simulating a connector produces-edge mint). SDK `create_entity` of the same name adopts/canonicalizes it. Assert: journal has the ObjectRegistered (probe by canonical id found no canonical row); rebuild → node carries `obj-<sha26>` id (not the ulid) — guards the id-probe choice (a name-probe would skip and the canonicalization would die on rebuild).
6. `test_journaled_line_has_no_envelope_prop_pollution` — journal line: envelope keys (`event_id`/`ts`/`type`/`initiated_by`/`projection_version`) at top level; payload keys = applied-dict mirror minus `(type,id,point,payload,event_id,ts,initiated_by,projection_version)`. Post-rebuild the Object node's property set contains none of `event_id`/`ts`/`initiated_by`/`projection_version`.
7. `test_no_log_sdk_no_journal_no_prop_change` — journal-less SDK (no `event_log_path`): create_object → no log file exists / no ObjectRegistered anywhere; node props are the pre-change set (createdAt from the projection path; no synthesis artifacts; no envelope keys).

**Step 1.2** — Run RED: `TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix' uv run pytest tests/test_object_registered_journal.py -v` → every test FAILS on the pre-fix cause (no journal lines → objects absent post-rebuild; no double-journal semantics). Verify no setup-error failures (each RED assertion is the *behavior* assertion, not a fixture problem).

**Step 1.3** — Commit: `git add tests/test_object_registered_journal.py && git commit -m "test(#2194): RED — journal ObjectRegistered behaviors (capture fold round-trip, only-on-create, byte-identity, stub adoption, no-log gate)"`.

### Task 2: GREEN — `_create_entity` probe-gated ObjectRegistered journaling

**Intent:** Implement A1 in `_create_entity` (sdk.py) — probe-gated, mirror-exact, post-apply emission — making Task 1's tests pass.
**Acceptance:** Task 1 file fully green; journal-less SDK behavior byte-identical (no-log test passes); no change to the Event branch's behavior.
**Files:**
- Modify: `tortoise/sdk.py:13969-14073` (`_create_entity`)
- Test: `tests/test_object_registered_journal.py`

**Step 2.1** — Code change (single region; keep the Event block shape and its #2061 comment; re-anchor START via `grep -n "def _create_entity"`, TAIL via `grep -n "# #452: Subject/Object MERGE by name"`):

(a) Extend the reserved-name pop to Object (Event precedent at :14010-14018 — `point`/`payload` are `_emit_event`-reserved kwargs; grep-verified no in-repo caller passes them on Object creates; the pop is REQUIRED for replay parity — without it a caller prop would persist live via `_persist_extra_props` but be dropped from the journal mirror):
```python
if label in ("Event", "Object"):
    event.pop("point", None)
    event.pop("payload", None)
```

(b) Pre-apply `createdAt` synthesis (only when a journal exists; never overrides a caller value; EventAPI `add_object` precedent stamps `createdAt=now_iso()`):
```python
# (#2194) Synthesize createdAt BEFORE apply so live + journal + replay carry
# the identical value (replay would otherwise stamp rebuild time — the
# #2164-P4 drift class). Gated on the journal: journal-less SDKs keep the
# projection's coalesce($now) behavior byte-identical to pre-#2194.
if label == "Object" and self._event_log_path and "createdAt" not in event:
    from .ids import now_iso  # noqa: I001
    event["createdAt"] = now_iso()
```

(c) Pre-apply existence probe on the CANONICAL id + post-apply gated emission (place the probe before `apply_result = proj.apply(event)`, the emission after it — phantom-event ordering):
```python
# (#2194) Journal ObjectRegistered on FIRST canonical registration only —
# probe the deterministic canonical id (obj-<sha26(name)>) before apply. A
# row = canonical re-mention (MERGE by name, ON MATCH — the #1350 clobber
# guard keeps status; journaling again would double-register). No row =
# fresh create OR #1155 stub adoption (name-stub under a random ulid — the
# canonical registration is genuinely new) → journal after apply. Probe
# failure → warning + fail-open-to-journal (a duplicate line is replay-safe;
# a skip would silently re-open the node-loss bug). Accepted divergences
# (only-on-create mandate): re-mention prop mutations and pre-#2194-history
# re-mentions are live-only / never registered — byte-identity holds for the
# first canonical registration.
_journal_object_registration = False
if label == "Object" and self._event_log_path and "name" in event:
    try:
        _journal_object_registration = not proj.g.query(
            "MATCH (o:Object {id:$cid}) RETURN o.id",
            params={"cid": id_val}).result_set
    except Exception:  # noqa: BLE001 — fail-open: journal (durable bias)
        _logger.warning(
            "ObjectRegistered existence probe failed for %s — journaling "
            "optimistically (id=%s)", event.get("name"), id_val)
        _journal_object_registration = True
```
…after `apply_result = proj.apply(event)`:
```python
if label == "Object" and _journal_object_registration:
    # (#2194) Mirror the EventRecorded block below: payload = the exact
    # applied dict (minus type/id + envelope-reserved keys) so replay
    # upserts a byte-identical Object. ObjectRegistered is NOT in
    # _GRAPH_EVENT_TYPES → JSONL-only emission; _emit_event no-ops when the
    # log is unset (S1 bound). Use event["type"] (== "ObjectRegistered" for
    # every Object create) so a future label==Object event_type stays
    # coupled to its own branch.
    self._emit_event(
        event["type"],
        id=event["id"],
        **{k: v for k, v in event.items()
           if k not in ("type", "id", "point", "payload",
                        "event_id", "ts", "initiated_by",
                        "projection_version")},
    )
```

**Step 2.2** — GREEN: rerun `tests/test_object_registered_journal.py` → all 7 pass.

**Step 2.3** — Quick regression sanity (before T3 migrates the scaffolding): `uv run pytest tests/test_status_projection.py -q` — the 4 manual-`_emit_event` sites now coexist with auto-journaling (replay stays idempotent; tests still pass — this is the accidental-proof state T3 cleans up). Expected: 19 passed (the manual scaffolding duplicates are harmless on replay).

**Step 2.4** — Commit: `git add tortoise/sdk.py && git commit -m "feat(#2194): journal ObjectRegistered on first canonical registration in _create_entity (probe-gated, createdAt-synthesized, post-apply)"`.

### Task 3: Migrate manual ObjectRegistered scaffolding + stale docstrings in test_status_projection.py

**Intent:** The 4 manual `_emit_event("ObjectRegistered",...)` sites were written "until the separate OD2 journaling issue lands" — this is that issue. Restore each test's true purpose so the fix is actually verified (redundant manual emissions would mask an auto-journal regression). **T3 DEPENDS ON T2** — the scaffolding only becomes redundant/incorrect after T2 lands; do NOT run T3 before T2.
**Acceptance:** 19 baseline tests green with auto-journaling active; no test journals the same registration twice for the same purpose; docstrings no longer claim "SDK capture Objects are NOT journaled".
**Files:**
- Modify: `tests/test_status_projection.py`

**Step 3.1** — `test_rebuild_all_restores_object_superseded_fold` (:145-209): **drop** the manual `sdk._emit_event("ObjectRegistered", id=oid, ...)` (:184) — `create_entity` at the top now auto-journals the registration; the replay gets its node from the real production line. Rewrite the docstring (delete the "NOT journaled … until the separate OD2 journaling issue lands" note at :158-164); optionally strengthen: assert the auto journal contains exactly one ObjectRegistered for the name.

**Step 3.2** — `test_rebuild_all_fold_before_registration_still_folds` (:211-256): **restructure to preserve the adversarial [fold, registration] journal order with the real producer.** Live `create_entity` now auto-journals its registration BEFORE any later fold emission — dropping :244's manual line would leave [OR, OS] and unpin the two-sweep regression. Instead: journal the `ObjectSuperseded` first (manual `sdk._emit_event` — simulating a connector/journaled producer that supersedes a name not yet registered in this log), THEN `create_entity` on the fresh name (probe misses → auto-journals the registration after the fold) → stream is [OS, OR]. Update the docstring: the adversarial producer is the connector/journaled-producer lane, not an SDK create.

**Step 3.3** — `test_rebuild_all_legacy_6b_id_only_shape_supersedes` (:258-295): **drop** the manual ObjectRegistered (:277) — create_entity auto-journals the node for replay; the test's purpose (legacy id-only ObjectSuperseded shape) is unchanged.

**Step 3.4** — `test_rebuild_all_legacy_idless_object_fold_survives` (:298-365): **keep** the manual ObjectRegistered with the legacy `legacy_reg_id` (:333) — the point is a pre-canonical registration id ≠ the synthesized canonical id (raw id-less Object + re-mention auto-journal would canonicalize — that's the OTHER test's job). Note in the docstring that `create_entity(successor)` now auto-journals (harmless).

**Step 3.5** — Docstrings at :127/:145-164 reworded to the post-fix world (capture Objects ARE journaled from #2194).

**Step 3.6** — Green: `TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix' uv run pytest tests/test_status_projection.py tests/test_object_registered_journal.py -v` → all pass (19 migrated + 7 new).

**Step 3.7** — Commit: `git add tests/test_status_projection.py && git commit -m "test(#2194): migrate manual ObjectRegistered scaffolding to real auto-journaling; stale docstrings"`.

### Task 4: Docs/comment sweep

**Intent:** Remove every now-false "Objects are NOT journaled" / "Events only" artifact and make the fold-sweep warning honest about residual 0-row fold sources (pre-#2194 journals, legacy/raw producers, delete races).
**Acceptance:** `grep -rn "OD2 capture gap\|journaled EventRecorded for Events only\|SDK capture-created Objects are NOT journaled" tortoise/ tests/ docs/` returns only the reworded warning + historical/legacy-format text.
**Files:**
- Modify: `tortoise/sdk.py`, `tortoise/projection/__init__.py`, possibly `docs/ONTOLOGY.md`

**Step 4.1** — sdk.py `_create_entity` docstring (:13971-13973): "...SDK-created Events additionally journal EventRecorded via ``_emit_event`` (#2061)" → add "SDK-created Objects journal ``ObjectRegistered`` on first canonical registration (probe-gated, #2194); Events journal unconditionally (#2061)."

**Step 4.2** — sdk.py class docstring `event_log_path` (:1350-1352): "...restore SDK-created points (#548)" → "...restore SDK-created points (#548), Events (#2061), and Objects (#2194)".

**Step 4.3** — sdk.py EventRecorded block comment (:14027-14043): add a cross-ref sentence — the Object block (above) mirrors this shape with the same exclusion set, probe-gated instead of unconditional.

**Step 4.4** — projection/__init__.py fold-sweep comment (:1417-1424) + warning (:1431-1434): the "pre-#2194 capture OD2 gap" explanation is now historical — capture Objects ARE journaled. Reword the comment + the warning `"(OD2 capture gap?)"` → residual 0-row folds come from pre-#2194 journals, legacy/raw unjournaled producers, or delete races. Keep behavior unchanged. (No test asserts the warning string — verified.)

**Step 4.5** — Check docs/ONTOLOGY.md:132/357 (Object.status cache doctrine) — expected fine (event stream = truth); update only if adjacent text claims capture Objects are not journaled.

**Step 4.6** — Note (docs sweep, no code): MCP `create_object` reserved-name behavior — a tenant passing `point`/`payload` props today persists them live via extras; post-change they are dropped (mirrors Event's pre-existing #2061 behavior) — a consistency improvement, visible narrowing.

**Step 4.7** — Commit: `git add tortoise/sdk.py tortoise/projection/__init__.py && git commit -m "docs(#2194): sweep stale not-journaled claims; honest fold-sweep warning reword"`.

### Task 5: Full verification

**Intent:** Prove no regression across the rebuild/projection/capture surface and meet the issue's 4 indicators.
**Acceptance:** All suites below green on the docker lane; embedded carve-out spot check green.
**Files:** none (verification)

**Step 5.1** — Target suites (docker lane, in order):
```bash
export TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix'
uv run pytest tests/test_object_registered_journal.py tests/test_status_projection.py -v
uv run pytest tests/test_projection.py -q
uv run pytest tests/test_ingest_rebuild_durability.py tests/test_ingest_bundle.py -q
uv run pytest tests/test_capture_session.py tests/test_capture_session_supersession_e2e.py -q
uv run pytest tests/test_entity_stage.py tests/test_semantic_extractor.py tests/test_p1_differentiators.py -q
```

**Step 5.2** — Embedded carve-out spot check: `TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_embedded_lifecycle.py tests/test_guard.py -v` (cross-lane safety).

**Step 5.3** — Acceptance criteria ↔ issue indicators:
| Indicator | Criterion | Test |
|---|---|---|
| 1. `_create_entity` emits ObjectRegistered when it creates an Object | Exactly one line per fresh create; probe skip on canonical re-mention; no-op when `event_log_path` unset | T1 tests 2/3/7 |
| 2. Capture Object + fold survive rebuild_all (status='superseded' + supersededBy restored) | Round-trip via real `apply_supersessions` lane; supersededAt == journaled fold ts | T1 test 1 |
| 3. Replay byte-identity (id/name/object_kind/status; no drift) | Replayed node props == live node props (canonical id, objectKind, status, is_episodic, title); createdAt == journaled; no envelope pollution | T1 tests 2/5/6 |
| 4. Existing rebuild tests green (incl. #2164's fold test) | 19-test baseline green post-T3 migration | T3.6 / T5.1 |

**Step 5.4** — commit-workflow skill (mandatory gate before merge: pre-flight typecheck/tests, PR, code-review + test-review gates, merge). Commit any residual: `git add -A && git commit -m "chore(#2194): verification pass"` if needed before the PR.

## Runtime prerequisites
- Docker FalkorDB up on localhost:6379 (else `docker compose -f ../eldato/operations/memory/docker-compose.yml up -d`).
- `TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix'` for docker-lane runs.
- Worktree `feat/2194-journal-objectregistered` at base ef5d8421; `.venv` symlinked (no install needed).

## Out of scope (documented, not silently expanded — filed as follow-ups)
- **SubjectAdded journaling** — identical `_create_entity` gap for Subjects → **#2295**.
- **Durability write-surface invariant / Object loss backstop (extend the #548 pre-wipe snapshot or journal repair)** + capture aboutObject/CONTAINS/session-link edge durability → **#2296**.
- **EventAPI `add_object` unconditional journaling** — a different producer (own `_emit`), relied on by mining/connector lanes; unchanged.
- **Backfill of pre-#2194 live Objects** — no backfill in scope; the first post-fix rebuild loses the pre-fix population (disaster-recovery journal semantics) — documented in the fold-sweep comment (T4.4) and the #2194 scoping comment.
