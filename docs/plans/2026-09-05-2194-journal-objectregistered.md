<!-- research-path: in-repo (#2194 scoping comment 5551912491 + #2164 fold sweep + sdk._create_entity EventRecorded = the references) -->

# #2194 — Journal `ObjectRegistered` for capture-created Objects — capture folds survive `rebuild_all`

> **For Pi:** Use `executing-plans` to implement this plan task-by-task.

**Goal:** Capture-created Objects (and their #1350/#2164 `ObjectSuperseded` folds) survive `rebuild_all` by journaling `ObjectRegistered` on first canonical registration — closing the OD2 rebuild boundary documented at projection/__init__.py:1417-1434 and test_status_projection.py:160-164.

**Team:** epistemic-team
**Complexity:** standard (Architecture: standard, Ontology: low — no new vocabulary)

**Architecture:** SDK-local, probe-gated emission in `_create_entity` (A1 — scoping decision, verified across problem-verify/solution-verify/second-model/Phase-7 gates). When an event log is configured and the label is Object, a pre-apply existence probe on the canonical deterministic id + name (`MATCH (o:Object {id:$cid, name:$name})` — the name conjunct hardens against a cross-name sha-digest collision, which would otherwise fail-closed on a genuinely-new registration) discriminates a canonical re-mention (row exists → skip; the issue's only-on-create mandate) from a first canonical registration (no row → fresh create OR #1155 stub adoption → journal). `createdAt` is synthesized into the event dict pre-apply ONLY on the journaling path (probe-no-row), so live, journal, and replay carry the identical value (#2164-P4 drift class) while re-mentions and journal-less SDKs stay byte-identical to pre-#2194. Stub adoption: `_upsert_object` ON MATCH adopts the synthesized `createdAt` via `coalesce(o.createdAt, $ca)` (idempotent — existing created value wins; the #1155 coalesce-id pattern in the same clause) so the adopted live node, the journal, and replay all carry it. Emission is post-apply (phantom-event ordering hazard — a journaled registration whose live apply never happened would replay-create a phantom node) through the existing `_emit_event` JSONL-only path (`ObjectRegistered ∉ _GRAPH_EVENT_TYPES` → no GraphEvent-store double-write). Rebuild consumers already exist: pass-1b `_upsert_object` (projection/__init__.py:1389) + the deferred fold sweep (:1417-1434). No-op when `event_log_path` is unset (S1 bound — journal-less SDKs stay byte-identical EXCEPT the unconditional `point`/`payload` drop, a deliberate tenant-visible narrowing identical on both lanes to avoid divergent persistence — see T4.6; same bound as #2061/#2164/#2193).

### Pattern Research
> **Findings date:** 2026-09-05
> Gate skipped: plan touches zero third-party dependencies — pure in-repo refactor onto existing mechanisms. Axis research (Architecture = medium+) fired 2 external queries (scoping §External Research); a pre-approval external validation pass fired 3 more (below). PRIOR_RESEARCH: #2164 full scoping + 6 fresh-reviewer code-review cycles of the fold machinery; the #2194 scoping ran problem-verify/solution-verify/second-model(coherence)/Phase-7 review gates — every anchor re-verified against origin/main code by multiple independent reviewers.

**External validation pass (2026-09-05, pre-approval — 3 queries, canonical sources):**
1. **Emit-once creation facts + idempotent consumers** (Microsoft event-sourcing: consumers MUST be idempotent under at-least-once; CockroachDB idempotency-and-ordering: naturally-idempotent events or txn-id dedupe; idempotency-key literature: deterministic hash of identifying fields). → Validates only-on-create emission (at-most-once fact log) + replay MERGE-by-name idempotency (safety net for TOCTOU duplicates) + the `obj-<sha26(name)>` deterministic id; corroborates the A3 rejection (avoid double-emitted create facts in the fact log even when consumers tolerate them).
2. **FalkorDB MERGE stats** (docs.falkordb.com: idempotent MERGE + "Nodes created: N"; Go clients expose `NodesCreated()`; DeepWiki OpMerge: concurrent same-key MERGEs → postponed matches). → Confirms docker-lane `nodes_created` attribution exists (A2 would work there) and the embedded quirk root cause; the probe design is backend-agnostic (depends on no statistics) — A2 rejection stands.
3. **Dual-write compensation without outbox** (Confluent dual-write; AWS transactional outbox; Kleppmann log-as-source-of-truth; Microsoft compensating-transaction). → Cross-store outbox impossible; the accepted-loss structure (apply-then-emit ordering, fold-miss warnings as missing-event detection, #548 Point snapshot, #2296 Object backstop) is the documented compensating set for outbox-less dual writes.
> Two intermediate queries hit 429 rate limits and were retried; all three surfaces validated with no plan changes warranted.

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
- **Log append failure (registration side)** → `_emit_event` best-effort warn-and-continue (existing sdk.py:1892-1904); the Object is live-but-not-durable for that write (≡ pre-fix; no regression) — pinned by T2 test 11 (append raises → create succeeds + warning + live node; rebuild omits it). No Object #548-snapshot backstop exists — **accepted and documented** (loss-backstop tracked in #2296).
- **Log append failure (fold side — `apply_supersessions` emits ObjectSuperseded best-effort THEN folds live)**: if the fold line's append fails, the live graph has A superseded while the journal holds only the registrations → `rebuild_all` restores A **live with no warning** (the 0-row fold-miss warning only fires for folds present in the journal; test 8's warning pin is orthogonal). Journal-consistent outcome, warn-once-at-capture — **accepted and pinned** by T2 test 12 (registration-without-fold → rebuild restores live, no phantom fold, no fold-miss warning).
- **Re-mention prop churn** (title/objectKind mutated on a later ON MATCH mention) → journal keeps first-registration props; rebuild restores first-registration state (live-only for the mutation). **Accepted divergence** (issue's only-on-create mandate; capture writes only name+kind+is_episodic) — documented in the emission comment + **pinned in T1 test 3** (mutate objectKind/title on the second mention → journal holds first values → rebuild reverts to them).
- **Stub-adoption title + status asymmetry** (connector stub adopted by a capture mention): stubs are minted by `_event_plain_merge` with id+objectKind only — no title, no status. Live adoption keeps title/status ABSENT (ON MATCH writes title only via `coalesce($title, o.title)` with `$title=None` → no-op; never writes status — #1350 clobber guard; `status ∈ _OBJECT_HANDLED` blocks extras); rebuild ON CREATE writes `title=''` + `status='live'` (EventAPI-parity, pre-existing) — accepted + documented (benign: status-absent and 'live' are both outside the recall-exclusion tuple); createdAt is NOT asymmetric (ON MATCH `coalesce(o.createdAt, $ca)` adopts the synthesized value).
- **Ownership edges never replayed for Objects** (authoredBy/ownedBy/managedBy): live `_create_entity` wires them as EDGES post-apply; replay `_persist_extra_props` SKIPS them (`_META_KEYS` — edge-managed keys) → a journaled ownership-bearing Object (reachable via `create_object(ownedBy=...)` on a journal-enabled SDK, NOT the capture shape — capture passes no ownership props) rebuilds node-without-edges. Pre-existing EventAPI-parity class (api producers journal ownership and replay drops it identically) — **accepted + documented**; the property-set byte-identity methodology cannot see edges.
- **Pre-#2194-history ghost** (canonical Object created pre-fix, superseded post-fix, re-mentioned post-fix) → probe hits → skip → still no registration in this journal → fold-miss on rebuild. **Accepted** (no backfill in scope — first post-fix rebuild loses pre-fix population; disaster-recovery journal semantics; reworded fold-sweep comment names the residual sources). **The 0-row fold-miss warning firing path is pinned by a test** (T1 test 8 — caplog level, not string, so the T4.4 reword survives).
- **Mixed-producer id churn** (EventAPI `obj_`-scheme log + SDK `obj-`-scheme log covering one name in one rebuild dir) → last file-sorted registration's id wins on replay; pre-existing cross-producer class (#330-documented) — intentionally untested (documented deferral; a determinism test would pin pre-existing behavior outside this issue's scope). Probe-by-id+name retained (a superseded same-name Object must NOT suppress the canonical registration).
- **Cross-dir duplicate registrations + journal growth**: two SDK logs for the same graph name in DIFFERENT dirs each synthesize their own `createdAt` on first registration — a single-dir rebuild restores that dir's value (test 9's first-line-wins applies within a file only); pre-existing multi-log semantics, no test. Journal growth is bounded by distinct canonical registrations + folds per log lifetime (re-mentions correctly skipped); no rotation/compaction in scope (pre-existing #548-family behavior).

**Tech Stack:** Python 3.12+, FalkorDB docker lane (`TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix'`), `uv run pytest`, EventLog JSONL. No new deps.

---

### Task 1: RED — new journal-behavior tests

**Intent:** Pin every mandated behavior BEFORE code changes: capture→rebuild→fold round-trip (indicator 2), plain unfolded Object survival (indicator 1+3), re-mention no-double-journal (only-on-create), stub-adoption canonicalization survival, createdAt parity + no envelope pollution (byte-identity), no-log gate, re-mention-after-fold no-resurrect.
**Acceptance:** New tests exist and FAIL for the right pre-fix reason (zero ObjectRegistered lines → Object absent post-rebuild; no double-journal guard yet). Baseline 19 in test_status_projection.py untouched.
**Files:**
- Create: `tests/test_object_registered_journal.py` (docker lane — NOT in the tests/_embedded.py carve-out)

**Step 1.1** — Write the tests (all use `TortoiseSDK(str(tmp_path / "<n>.db"), event_log_path=str(events / "events.jsonl"))` with `events.mkdir()`; `sdk.close()` in `finally`; distinct DB paths per SDK pairing so docker-lane redirect hashes don't collide). **RED acceptance applies to tests 1-7; tests 8-9 are green-pin guards** (see Step 1.2). Tests 6/7 pass **scalar** reserved props (`point='x'`, `payload='y'`) — dict values would raise the FalkorDB non-primitive-property error pre-fix, tripping the no-setup-error RED rule. Boolean asserts on DB-read scalars use equality (`== False`) not identity (`is False`) — the JSONL+FalkorDB round-trip can return `0`/`None` instead of Python `False`:

1. `test_capture_object_and_fold_survive_rebuild_all` (RED) — create successor + target Objects via `create_entity("object", name, objectKind=..., is_episodic=False)` (the capture shape), fold via `apply_supersessions(proj, sdk, [{"superseded": "strategy-A", "supersedes_by": "strategy-B", "evidence": "capture fold"}], session_id="s1")`, then `proj.rebuild_all(str(events))`. Assert: node exists; `status == "superseded"`, `supersededBy == "strategy-B"`, `objectKind == "core:strategy"`, `is_episodic == False` (equality — see Step 1.1 note); **`supersededAt == the journaled ObjectSuperseded envelope ts`** (NOT the live node's supersededAt — live folds stamp `now()` micros later; assert against `[e for e in EventLog(...).read_all() if e["type"] == "ObjectSuperseded"][-1]["ts"]`).
2. `test_plain_object_survives_rebuild_all_byte_identical` (RED) — create one Object (no fold), parametrized over an ASCII name (`"strategy-A"`) and a non-ASCII name (`"estrategia-ñ-日本語-💡"`) so the probe's new name-conjunct + sha-digest + JSONL mirror get unicode coverage (a digest/param regression would silently fail-closed on genuinely-new registrations); snapshot the FULL live node property set BEFORE the wipe; read the journal: exactly one `ObjectRegistered` with `id == _entity_name_id("Object", name)`, `name`, `object_kind`, `status == "live"`, `createdAt` present, `is_episodic` matching. Rebuild → node present; **full replayed property set == live snapshot** (embedding compared via same-call recompute or explicitly excluded with a comment — the point-path precedent); `createdAt == journaled createdAt` (no rebuild-time drift).
3. `test_remention_does_not_double_journal_and_prop_churn_is_live_only` (RED on the double-journal half) — create "strategy-A" with `objectKind="dev:issue"`; re-create the same name with `objectKind="core:strategy"` (+ a `title`) — the second call is a canonical re-mention (ON MATCH). Assert: live node `objectKind == "core:strategy"` (the mutation IS live); journal holds EXACTLY ONE ObjectRegistered whose `object_kind` is the FIRST value `"dev:issue"` (no double-journal, no mirror-of-latest); **id-equality asserted via post-call graph query** (`MATCH (o:Object {name:$n}) RETURN o.id` for each call — the suite's established convention; `create_entity` returns `{"node": ..., "nudges": [...]}`, NOT the node, so never index `result["id"]`); one Object node. Rebuild → node reverts to `"dev:issue"` — **the accepted only-on-create divergence pinned as behavior**.
4. `test_remention_after_fold_does_not_journal_or_resurrect` (RED) — create A+B, fold A→B, then `create_entity("object", "strategy-A", ...)` again (superseded name re-mentioned): zero new ObjectRegistered lines (total stays 1 for A), live A stays `superseded`, and after `rebuild_all` A is still `superseded` (fold line already in journal; no resurrect). *(Note: the live-status asserts hold pre-fix; the journal-total + rebuild asserts are the RED half.)*
5. `test_stub_adoption_journals_canonicalization` (RED) — pre-create a name-stub Object with a random ulid id (raw `proj.g.query("CREATE (o:Object {name:$n, id:$id})", ...)` simulating a connector produces-edge mint). SDK `create_entity` of the same name adopts/canonicalizes it. Assert: journal has the ObjectRegistered (probe by canonical id+name found no canonical row); **live adopted node `createdAt == the journaled createdAt`** (ON MATCH `coalesce(o.createdAt, $ca)` adoption — the byte-identity invariant holds on the stub path); rebuild → node carries `obj-<sha26>` id (not the ulid) and the same createdAt.
6. `test_journaled_line_and_live_node_drop_reserved_props` (RED) — pass scalar `point='x'`/`payload='y'` props on the Object create. Assert: the journal line EXCLUDES them (mirror minus the exclusion tuple) AND the live node does not carry them (`_persist_extra_props` can't persist what the pop removed — pins the parity rationale for the unconditional pop); envelope keys (`event_id`/`ts`/`type`/`initiated_by`/`projection_version`) at top level of the line only; post-rebuild the node's property set contains none of `event_id`/`ts`/`initiated_by`/`projection_version`. **Add the GraphEvent-store membership pin here**: after the create on a journaled SDK, assert `MATCH (e:GraphEvent {type:'ObjectRegistered'}) RETURN count(e)` == 0 — pinning `ObjectRegistered ∉ _GRAPH_EVENT_TYPES` (JSONL-only; a future edit adding it to the set would silently double-write the #432 store with full synthesized payloads — the BatchIdStamped precedent test_ingest_bundle.py:1086).
7. `test_no_log_sdk_no_journal_and_unconditional_pop` (RED — the journal-less reserved-prop drop is NEW behavior: pre-fix only `label == "Event"` pops, so a journal-less Object create with scalar `point`/`payload` persists them live via `_persist_extra_props`): journal-less SDK (no `event_log_path`), `create_object` **passing scalar `point="x"`/`payload="y"`** → no log file exists / no ObjectRegistered anywhere; the reserved props are NOT persisted live (the unconditional pop applies on journal-less SDKs too — pinning that half of the reserved-name narrowing so a regression to a journal-gated pop, which would reintroduce divergent journaled/journal-less live persistence, fails loudly); node props otherwise the pre-change set (createdAt from the projection path; no synthesis artifacts; no envelope keys).
8. `test_fold_miss_warning_fires_for_unregistered_target` (**green-pin guard** — the 0-row fold-miss warning fires pre-fix and must keep firing) — journal ONLY an `ObjectSuperseded` (manual `_emit_event`, no ObjectRegistered for the name, no node pre-seeded) → `rebuild_all` → assert via `caplog` that the fold-miss warning fires at rebuild (assert on log level/event, NOT the string — the T4.4 reword must survive) AND the Object is absent (no phantom resurrection). Pins the warning firing path for the accepted residual-loss classes (pre-#2194 ghosts, legacy/raw producers).
9. `test_duplicate_registration_lines_replay_idempotently` (**green-pin guard** — replay idempotency already holds pre-fix): two `ObjectRegistered` lines for the same name + a fold → `rebuild_all` → exactly one Object node, `status == "superseded"`, `createdAt ==` the FIRST line's value. **The manual second emit must carry an explicit DIFFERENT `createdAt` (e.g. a second `now_iso()` or a fixed sentinel)** — a manual `_emit_event` without createdAt carries `$ca=None`, so the ON MATCH `coalesce(o.createdAt, $ca)` branch would never face a competing value and the assertion would pass vacuously. With distinct synthesized createdAt values (the real TOCTOU duplicate shape), the coalesce-first-wins branch is genuinely exercised.

**Step 1.2** — Run RED: `TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix' uv run pytest tests/test_object_registered_journal.py -v` → **tests 1-7 FAIL on the pre-fix cause**: 1-6 fail on the missing journal (no lines → objects absent post-rebuild / no double-journal semantics); test 7 fails on the missing unconditional pop (reserved props persist live on journal-less SDKs pre-fix). **Tests 8-9 PASS pre-fix by construction** (green-pin guards: the fold-miss warning firing and replay idempotency are pre-existing behavior the fix must not break) — do NOT force-fail the guards; their RED-phase value is the no-setup-error run. Verify no setup-error failures (each RED assertion is the *behavior* assertion, not a fixture problem; scalar reserved props in tests 6/7 keep the pre-fix failure a clean assertion, not a raised FalkorDB error).

**Step 1.3** — Commit: `git add tests/test_object_registered_journal.py && git commit -m "test(#2194): RED — journal ObjectRegistered behaviors (capture fold round-trip, only-on-create, byte-identity, stub adoption, no-log gate)"`.

### Task 2: GREEN — `_create_entity` probe-gated ObjectRegistered journaling

**Intent:** Implement A1 in `_create_entity` (sdk.py) — probe-gated, mirror-exact, post-apply emission — making Task 1's tests pass.
**Acceptance:** Task 1 file fully green; journal-less SDK behavior byte-identical (no-log test passes); no change to the Event branch's behavior.
**Files:**
- Modify: `tortoise/sdk.py:13969-14073` (`_create_entity`)
- Modify: `tortoise/projection/entities.py:316-367` (`_upsert_object` ON MATCH — createdAt adoption clause)
- Test: `tests/test_object_registered_journal.py` (adds tests 10-13 post-T2)

**Step 2.1** — Code change (single region; keep the Event block shape and its #2061 comment; re-anchor START via `grep -n "def _create_entity"`, TAIL via `grep -n "# #452: Subject/Object MERGE by name"`). **Acceptance wording note**: "journal-less SDK byte-identical" means identical EXCEPT the unconditional `point`/`payload` drop (a deliberate narrowing — test 7 asserts the drop on the journal-less lane too; a journal-gated pop would create divergent journaled/journal-less live persistence, the exact drift class this issue fights):

(a) Extend the reserved-name pop to Object (Event precedent at :14010-14018 — `point`/`payload` are `_emit_event`-reserved kwargs; grep-verified no in-repo caller passes them on Object creates; the pop is REQUIRED for replay parity — without it a caller prop would persist live via `_persist_extra_props` but be dropped from the journal mirror):
```python
if label in ("Event", "Object"):
    event.pop("point", None)
    event.pop("payload", None)
```

(b) Pre-apply existence probe on the CANONICAL id + name FIRST (determines the journal decision), then synthesize `createdAt` ONLY on the journaling path, then apply, then emit (phantom-event ordering: emission strictly after `proj.apply`):
```python
# (#2194) Journal ObjectRegistered on FIRST canonical registration only —
# probe the deterministic canonical id + name (obj-<sha26(name)>) before
# apply. The name conjunct hardens against a cross-name sha-digest
# collision (would otherwise fail-closed on a genuinely new registration).
# A row = canonical re-mention (MERGE by name, ON MATCH — the #1350 clobber
# guard keeps status; journaling again would double-register). No row =
# fresh create OR #1155 stub adoption (name-stub under a random ulid — the
# canonical registration is genuinely new) → journal after apply. Probe
# failure → warning + fail-open-to-journal (a duplicate line is replay-safe;
# a skip would silently re-open the node-loss bug). Accepted divergences
# (only-on-create mandate): re-mention prop mutations and pre-#2194-history
# re-mentions are live-only / never registered — byte-identity holds for the
# first canonical registration.
_journal_object_registration = False
# Truthy-name gate mirrors _upsert_object's persistence predicate (it no-ops on
# falsy names) — a falsy-name create must not mint a phantom ObjectRegistered
# line (junk-line journal growth on a no-op path).
if label == "Object" and self._event_log_path and event.get("name"):
    try:
        _journal_object_registration = not proj.g.query(
            "MATCH (o:Object {id:$cid, name:$name}) RETURN o.id",
            params={"cid": id_val, "name": event["name"]}).result_set
    except Exception:  # noqa: BLE001 — fail-open: journal (durable bias)
        _logger.warning(
            "ObjectRegistered existence probe failed for %s — journaling "
            "optimistically (id=%s)", event.get("name"), id_val)
        _journal_object_registration = True
if _journal_object_registration and "createdAt" not in event:
    # (#2194) Synthesize createdAt BEFORE apply ONLY on the journaling path
    # (probe-no-row), so live + journal + replay carry the identical value
    # (replay would otherwise stamp rebuild time — the #2164-P4 drift
    # class). Re-mentions (skip path) and journal-less SDKs keep the
    # projection's coalesce($now) behavior byte-identical to pre-#2194.
    from .ids import now_iso  # noqa: I001
    event["createdAt"] = now_iso()
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

**Step 2.2** — Add tests 10-13 to the new file (post-implementation pins — they only become meaningful once journaling exists, so they land here):
10. `test_probe_failure_fails_open_to_journal` — monkeypatch `proj.g.query` with a `side_effect` that raises ONLY when the query string contains the probe's distinctive fragment (`"id:$cid, name:$name"`) and otherwise delegates to the original (an unconditional raise would break `apply`'s MERGE + `_persist_extra_props` + the #452 re-fetch → the create fails for the wrong reason) → create an Object → assert (a) the create still succeeds (no probe exception escapes), (b) a warning is logged, (c) the ObjectRegistered line IS journaled (durable bias), (d) a subsequent `rebuild_all` restores the node. Pins the try/except — a regression to fail-closed (silent skip) would re-open the node-loss bug with no test catching it.
11. `test_log_append_failure_warns_and_keeps_live` — monkeypatch `EventLog.append` to raise (no test anywhere exercises this branch for any event type today — grep `"failed to append"` matches only sdk.py) → create an Object → assert the create succeeds, caplog shows the append warning, the node is live; and (the accepted consequence) a subsequent `rebuild_all` omits it (the #2296 backstop covers the loss). A regression turning the warn into a hard raise would crash every Object create mid-capture — this pins the best-effort contract.
12. `test_registration_without_fold_line_restores_live_on_rebuild` — create A+B (auto-registrations journaled), then `apply_supersessions` with the fold line's append failing (monkeypatch) → live A is superseded but the journal holds only the two registrations → `rebuild_all` → A restored LIVE (journal-consistent), no phantom fold, NO fold-miss warning (the warning only fires for folds present in the journal). Pins the fold-side append-failure signature — a future "fix" that warns or resurrects would trip this.
13. `test_capture_journal_line_order_registration_before_fold` — a capture-style sequence (create Objects then `apply_supersessions`) → assert via `read_all()` that the `ObjectRegistered` lines PRECEDE the `ObjectSuperseded` line, THEN run `proj.rebuild(log)` (the single-log chronological fold, projection/__init__.py:1148) and assert the fold applied (`status == "superseded"`) — making the load-bearing claim true-by-test: the synchronous post-apply emission point is what guarantees the single-log path (NO deferred sweep) folds correctly instead of resurrecting a superseded Object from an [OS, OR] journal.

**Step 2.3** — GREEN: rerun `tests/test_object_registered_journal.py` → all 13 pass.

**Step 2.4** — Quick regression sanity (before T3 migrates the scaffolding): `uv run pytest tests/test_status_projection.py -q` — the 4 manual-`_emit_event` sites now coexist with auto-journaling (replay stays idempotent; tests still pass — this is the accidental-proof state T3 cleans up). Expected: 19 passed (the manual scaffolding duplicates are harmless on replay). **api-lane note for the entities.py clause**: the ON MATCH `o.createdAt=coalesce(o.createdAt, $ca)` addition is shared with the EventAPI/connector lane — api.add_object always sends a fresh `createdAt`, so canonical re-mentions keep their existing value (idempotent) and a createdAt-less stub adopted by an EventAPI re-mention now gets stamped (previously stayed absent) — benign, arguably more consistent; test_entity_stage/test_semantic_extractor in T5 catch any drift.

**Step 2.5** — Commit: `git add tortoise/sdk.py tortoise/projection/entities.py && git commit -m "feat(#2194): journal ObjectRegistered on first canonical registration in _create_entity (probe-gated, createdAt-synthesized, post-apply) + stub-adoption createdAt coalesce"`.

### Task 3: Migrate manual ObjectRegistered scaffolding + stale docstrings in test_status_projection.py

**Intent:** The 4 manual `_emit_event("ObjectRegistered",...)` sites were written "until the separate OD2 journaling issue lands" — this is that issue. Restore each test's true purpose so the fix is actually verified (redundant manual emissions would mask an auto-journal regression). **T3 DEPENDS ON T2** — the scaffolding only becomes redundant/incorrect after T2 lands; do NOT run T3 before T2.
**Acceptance:** 19 baseline tests green with auto-journaling active; no test journals the same registration twice for the same purpose; docstrings no longer claim "SDK capture Objects are NOT journaled".
**Files:**
- Modify: `tests/test_status_projection.py`

**Step 3.1** — `test_rebuild_all_restores_object_superseded_fold` (:145-209): **drop** the manual `sdk._emit_event("ObjectRegistered", id=oid, ...)` (:184) — `create_entity` at the top now auto-journals the registration; the replay gets its node from the real production line. Rewrite the docstring (delete the "NOT journaled … until the separate OD2 journaling issue lands" note at :158-164); optionally strengthen: assert the auto journal contains exactly one ObjectRegistered for the name.

**Step 3.2** — `test_rebuild_all_fold_before_registration_still_folds` (:211-256): **restructure to preserve the adversarial [OS, OR] journal order with the real producer.** The current body starts with a create-if-missing preamble that graph-resolves `oid` — under auto-journaling that preamble create would journal its ObjectRegistered FIRST → [OR, OS] → the test passes for the wrong reason and the two-sweep fold regression goes unpinned (this is the plan's ONLY [OS, OR] adversarial-order coverage). Exact new body: (1) DELETE the create-first preamble entirely; (2) emit the manual `ObjectSuperseded` FIRST with `id=_entity_name_id("Object", "strategy-A")` (synthesized — no live node exists yet, so the old graph-resolution pattern would raise IndexError; the id-branch fold must match the node the later auto-registration recreates) + `name`; (3) THEN `create_entity("object", "strategy-A", ...)` on the fresh graph (probe misses → auto-ObjectRegistered lands after the fold) → stream is [OS, OR]; (4) add an explicit journal-order assertion (the two line types appear in `read_all()` order as [OS, OR]) so a future migration that reverts to the preamble fails loudly. Update the docstring: the adversarial producer is the connector/journaled-producer lane, not an SDK create.

**Step 3.3** — `test_rebuild_all_legacy_6b_id_only_shape_supersedes` (:258-295): **drop** the manual ObjectRegistered (:277) — create_entity auto-journals the node for replay; the test's purpose (legacy id-only ObjectSuperseded shape) is unchanged.

**Step 3.4** — `test_rebuild_all_legacy_idless_object_fold_survives` (:298-365): **keep** the manual ObjectRegistered with the legacy `legacy_reg_id` (:333) — the point is a pre-canonical registration id ≠ the synthesized canonical id (raw id-less Object + re-mention auto-journal would canonicalize — that's the OTHER test's job). Note in the docstring that `create_entity(successor)` now auto-journals (harmless).

**Step 3.5** — Docstrings at :127/:145-164 reworded to the post-fix world (capture Objects ARE journaled from #2194).

**Step 3.6** — Green: `TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix' uv run pytest tests/test_status_projection.py tests/test_object_registered_journal.py -v` → all pass (19 migrated + 13 in the new file).

**Step 3.7** — Commit: `git add tests/test_status_projection.py && git commit -m "test(#2194): migrate manual ObjectRegistered scaffolding to real auto-journaling; stale docstrings"`.

### Task 4: Docs/comment sweep

**Intent:** Remove every now-false "Objects are NOT journaled" / "Events only" artifact and make the fold-sweep warning honest about residual 0-row fold sources (pre-#2194 journals, legacy/raw producers, delete races).
**Acceptance:** `grep -rn "OD2 capture gap\|journaled EventRecorded for Events only\|SDK capture-created Objects are NOT journaled" tortoise/ tests/ docs/` returns only the reworded warning + historical/legacy-format text.
**Files:**
- Modify: `tortoise/sdk.py`, `tortoise/projection/__init__.py`, possibly `docs/ONTOLOGY.md`

**Step 4.1** — sdk.py `_create_entity` docstring (:13971-13973): "...SDK-created Events additionally journal EventRecorded via ``_emit_event`` (#2061)" → add "SDK-created Objects journal ``ObjectRegistered`` on first canonical registration (probe-gated, #2194); Events journal unconditionally (#2061)."

**Step 4.2** — sdk.py class docstring `event_log_path` (:1350-1352): "...restore SDK-created points (#548)" → "...restore SDK-created points (#548), Events (#2061), and Objects (#2194)".

**Step 4.3** — sdk.py EventRecorded block comment (:14027-14043): add a cross-ref sentence — the Object block (above) mirrors this shape with the same exclusion set, probe-gated instead of unconditional.

**Step 4.4** — projection/__init__.py fold-sweep comment (:1417-1424) + warning (:1431-1434): the "pre-#2194 capture OD2 gap" explanation is now historical — capture Objects ARE journaled. Reword the comment + the warning `"(OD2 capture gap?)"` → residual 0-row folds come from pre-#2194 journals, legacy/raw unjournaled producers, or delete races. Keep behavior unchanged. The warning's FIRING path is pinned by T1 test 8 (caplog level, not string) — the reword must preserve the log level/event structure the test asserts.

**Step 4.5** — Check docs/ONTOLOGY.md:132/357 (Object.status cache doctrine) — expected fine (event stream = truth); update only if adjacent text claims capture Objects are not journaled.

**Step 4.6** — Docs entry (not just a note) landing in **docs/ONTOLOGY.md** (the ObjectRegistered/event-shape doctrine section near the Object.status cache notes ~:132): MCP `create_object` reserved-name behavior — the unconditional `point`/`payload` pop (Task 2 Step 2.1(a)) is a real tenant-visible narrowing: today a tenant passing them persists them live via `_persist_extra_props`; post-change they are dropped — mirroring Event's pre-existing #2061 behavior (unconditional, regardless of journal config — consistent persistence semantics across journaled/journal-less SDKs is the point: a journal-gated pop would create divergent live persistence, the exact drift class this issue fights). Pinned by T1 tests 6/7 (both the journal line and the live node exclude them, on both lanes).

**Step 4.7** — Commit: `git add tortoise/sdk.py tortoise/projection/__init__.py docs/ONTOLOGY.md && git commit -m "docs(#2194): sweep stale not-journaled claims; honest fold-sweep warning reword; reserved-name narrowing entry"`.

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
| 3. Replay byte-identity (id/name/object_kind/status; no drift) | FULL replayed property set == live snapshot on the FRESH-create path (test 2's methodology — the stub path carries documented title/status asymmetries: Failure Modes); createdAt == journaled (fresh + stub-adoption createdAt); no envelope pollution; reserved-name drop parity (journaled + journal-less) | T1 tests 2/5/6/7 + guard 9; append-failure durability pinned by T2 tests 11/12 (not byte-identity legs) |
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


---

<!-- plan-review: cycles=5, status=clean, version=2.3.0 -->
<!-- 🔍 second-model final gate: clean (deepseek-v4-pro) — 0 P0/P1/P2; P3 (acceptance wording) + P4s resolved in the final fix cycle -->
