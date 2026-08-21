<!-- research-path: docs/epics/2026-08-20-1509-extractor-v3/04-plan.md -->

# E1 — Session-Date Anchoring into S1/S2/S4 (+ `when` slot, event `startedAt`) Implementation Plan

**Issue:** #1533 (epic #1509, E1) · **Test-alignment:** E2E-4 · **Test-design ref:** #1515 surface 28 (temporal)
**Team:** epistemic-team
**Goal:** Thread the session date through extraction so extracted points carry a `when` slot and events carry `startedAt` — making "when" questions (E2E-4) answerable instead of abstaining.

**Architecture:** Two additive seams, zero new ontology kinds/edges. (1) `extract_session_v2` gains a `session_date` kwarg (default `None` = date-blind, backward compatible) that injects a bounded "Today is {date}" anchor block into the S1/S2/S4 prompts (mem0 write-time pattern) and an emission rule for `when`/`startedAt` in the OUTPUT_CONTRACT; S5 (`execute_embed`) normalizes the model's output deterministically (events default to the session date, junk dates dropped with warnings). (2) The date rides the Layer-1 payload as two additive-optional fields (`Point.when`, `CommitEvent.started_at`) and lands on graph nodes (`Point.when`, `Event.startedAt` — both already-registered or approved additive Point properties). The eval call site (`tools/longmem_eval/ingest_v2.py`, where `session_date` is already in scope and dropped today) and the SDK `commit_session` path (default = capture time) both thread it.

**Pattern research:** mem0 write-time date anchoring — "Today is {date}; anchor every event/decision/state-change" (write-time resolution of relative expressions against the session date). Consistent with the epic Data Model Research (Hindsight grounds facts on occurrence-time + mention-time — our `when` vs `createdAt` split) and the ontology's bi-temporal distinction (§4.5 `capturedAt` = transaction, `startedAt` = valid). No third-party deps — zero-deps skip applies (per writing-plans `workflow/02` Step B).

### Integration Surface Map (test-design #1515)

| Surface | Component | Change | Test layer |
|---|---|---|---|
| 28 temporal (E1) | `extractor_v2.py` S1/S2/S4 + S5 | `session_date` kwarg; prompt date anchor; `when`/`startedAt` emission + deterministic normalization | unit + integration |
| 12 graph writes (E1–E5) | `ingest_v2.py` `_write_payload` + `commit_schema` + `hosted_api.py` | `when`/`startedAt` carried to nodes | integration |
| 22/24 Layer-1 payload | `commit_schema.py` | additive `Point.when` + `CommitEvent.started_at` (extra="forbid" ⇒ schema change REQUIRED) | unit |
| 25 reader context (UX-1/2/3) | `retrieve.py` `_annotate_hits` | **no E1 change** — `session_date` already surfaced per hit; R5 owns read-side decoration | — |
| 1/2 provider, 21 failover, 26 extraction_mode | — | untouched by E1 | — |

---

## Design Decisions

### D1 — `session_date` kwarg, optional and date-blind by default
`extract_session_v2(model, conversation, *, sdk=None, session_id=None, chunk_size=50, master=None, session_date: str | None = None)`.

- `None` / `""` → **date-blind**: S1/S2/S4 system prompts byte-identical to today; `execute_embed` emits no `when` / `started_at` keys. This preserves the payload byte-identical guarantee for undated sessions (same discipline as the #1350 supersessions additive-optional contract) and satisfies E2E-4's owned negative (undated session → no false date-answer).
- Semantics: the ISO date/datetime the conversation "happened on" (the eval's `haystack_dates[si]`). Passed through as-is; no timezone math in E1.

### D2 — Prompt anchoring is a bounded insert, not a rewrite
`S1_TMPL`/`S2_TMPL`/`S4_TMPL` are validated + owner-approved. Add ONE `{date_anchor}` placeholder paragraph (rendered to `""` when undated — byte-identical prompt text), carrying the mem0 rule:

> **DATE ANCHOR — today is `{session_date}`.** Anchor every state change, decision, and event to this date. Express time with ABSOLUTE ISO dates (YYYY-MM-DD); resolve relative expressions ("yesterday", "last week", "recently") against today. Never leave relative time in the narrative.

S1 must produce dates in the story; S2/S4 must map them onto `when`/`startedAt` per D3.

### D3 — `when` / `startedAt` emission rules (S2 + S4, OUTPUT_CONTRACT)
OUTPUT_CONTRACT gains two optional fields:

```json
"points":  [{"content": str, "pointKind": "statement", "about_entities": [str], "when": "YYYY-MM-DD|null"}],
"events":  [{"content": str, "eventKind": str, "about_entities": [str], "startedAt": "YYYY-MM-DD|YYYY-MM-DDThh:mm:ss|null"}],
```

Prompt guidance (identical text in S2_TMPL and S4_TMPL):
- **EVENT `startedAt`** — every event is a time-bound occurrence/decision: use the conversation's stated date when present, else **default to the session date**; `null` only when the session date is unknown.
- **POINT `when`** — emit an ISO date when the point is a state-change, decision, or date-bearing fact ("as of {date}", "on {date}", "since {date}"); `null` for timeless durable beliefs (operational lessons, stable facts) — do NOT stamp every point.

### D4 — Deterministic normalization in S5 (`execute_embed`) — events always dated, points only when anchored
The model output is untrusted; `execute_embed` is deterministic (never blocks — design §7.4):

- New helper `_valid_iso_date(v)` — accepts `^\d{4}-\d{2}-\d{2}([Tt ].*)?$`; anything else → warning + dropped.
- **Events:** `started_at = ev.get("startedAt") or session_date` when the session is dated → payload `"started_at"`. Events in a dated session ALWAYS get a date (guarantees E2E-4's "dated Events appear in the pool"). Undated session → no key.
- **Points:** `when = p.get("when")` only when `_valid_iso_date` passes → payload `"when"`. No default (timeless beliefs stay un-stamped). Junk → warning + no key.
- Keys are emitted **only when non-empty** — undated-session payloads stay byte-identical to today.
- `execute_embed` gains `session_date: str | None = None` kwarg (threaded from `extract_session_v2`). `derive_supersessions`/`_supersession_records` untouched.

### D5 — Layer-1 payload schema (commit_schema, REQUIRED — `extra="forbid"`)
`Point` and `CommitEvent` models use `ConfigDict(extra="forbid")` — a payload carrying `when`/`started_at` **rejects validation without the field additions**. Both additive-optional (defaults = absent → old payloads byte-identical):

```python
class Point(BaseModel):
    ...
    when: str = Field(default="", max_length=40)   # "" = undated

class CommitEvent(BaseModel):
    ...
    started_at: str | None = None                  # snake_case — matches captured_at convention
```

Naming: `started_at` on the payload (matches the existing `captured_at` payload field); the graph property stays `startedAt` (ontology §4.5). Eval path doesn't validate via commit_schema — it writes nodes directly — but the commit path (`commit_session` → POST /v1/sessions/commit) must validate.

### D6 — Server-side commit writes event `startedAt`
`tortoise/hosted_api.py` extracted-occurrences block (~line 3805): add `e.startedAt=coalesce(e.startedAt, $sat)` to the MERGE SET with `"sat": ev.started_at or ev.captured_at or now`. P4 parity: commit path events carry the same date semantics as the eval path's direct writes. The sessionCaptured Event already gets `startedAt=$cap` (block 3) — untouched.

### D7 — Eval call site (the "dropped today" fix)
`ingest_haystack_v2` (ingest_v2.py:191) already computes `session_date = dates[si] if si < len(dates) else ""` — currently only used for the Session node's `created_at`. Changes:
- Pass it: `out = extract_session_v2(model, turns, sdk=sdk, session_id=s_node, session_date=session_date or None)`.
- `_write_payload(..., session_date: str | None = None)`: points → `when=p.get("when") or None` on `create_point` (only when non-empty); events → `startedAt=ev.get("started_at") or ev.get("startedAt") or session_date` on `create_event`.
- `_sanitize_props` does not block `when` (verified — only rejects sourcePath/source_path/id), so the prop lands on the node.

### D8 — SDK `commit_session` path (production BYOK)
`commit_session(..., session_date: str | None = None)` → `_commit_session_v2` resolves `None` → `datetime.now(timezone.utc).isoformat()` (capture time = session date; consistent with `capture_session`'s `created_at=$now`). Effect: production commits get date-anchored extraction by default — this IS the E1 product behavior (01-align: "production capture already runs v2"). Reversibility: pass `session_date` explicitly; undated behavior remains available to direct `extract_session_v2` callers.

### D9 — Scope boundary: write-side only
E1 ships the WRITE side (dated points/events in the graph). Read-side decoration ("as of {when}" rendering, date-weight RRF, time-ordered hits) is R5's issue; the reader already receives `session_date` per hit (`_annotate_hits`, retrieve.py:38). `capture_session` (M2 extractor path, `_extract_session_llm`) is NOT touched — it doesn't call `extract_session_v2`; its Session/Event already carry `now`. Cross-lane contract in the Cross-lane section.

---

## Implementation Steps

### Task 1: `extractor_v2.py` — kwarg + prompt anchoring + S5 normalization

**Intent:** Make the extractor date-aware: `session_date` threads into S1/S2/S4 prompts and into deterministic S5 output, without changing undated behavior.
**Acceptance:** `extract_session_v2` accepts `session_date`; dated runs produce prompts containing the anchor and payloads carrying `when`/`started_at`; undated runs are byte-identical to today (prompts AND payloads).
**Files:**
- Modify: `tortoise/extractor_v2.py` — `S1_TMPL` (200), `run_s1` (258), `OUTPUT_CONTRACT` (336), `S2_TMPL` (348), `render_s2_prompt` (443), `run_s2` (451), `S4_TMPL` (631), `render_s4_prompt` (691), `run_s4` (703), `execute_embed` (997), `extract_session_v2` (1303)
- Test: `tests/test_extractor_v2.py`

**Step 1 — Add `_date_anchor(session_date)` + `_valid_iso_date(v)` helpers** (near `_granularity_text`, ~line 266). `_date_anchor` returns `""` when the date is falsy, else the D2 paragraph with the date substituted.

**Step 2 — Insert `{date_anchor}` block into `S1_TMPL`** (one paragraph, after the memory-granularity intro, before the Focus block — D2 text verbatim). Update `run_s1` to accept `session_date: str | None = None` and render `S1_TMPL.replace("{memory_granularity}", ...).replace("{date_anchor}", _date_anchor(session_date))`.

**Step 3 — Extend `OUTPUT_CONTRACT`** with `"when"` (points) and `"startedAt"` (events) per D3. Add the D3 emission-rule paragraph to `S2_TMPL` (via the `{date_anchor}` block, which for S2/S4 renders the D2+D3 text combined) and to `S4_TMPL`. Update `render_s2_prompt(master=None, session_date=None)` and `render_s4_prompt(story, search, embed_list, master=None, session_date=None)` and `run_s2`/`run_s4` to take and render `session_date`.

**Step 4 — `execute_embed` gains `session_date` and normalizes output:**
- Signature: `execute_embed(embed_list, search, *, session_id, story_arc="", summary="", extractor_version="value@0.5.0+v2", master=None, session_date: str | None = None)`.
- Events loop (~line 1108): `started_at = str(ev.get("startedAt") or "").strip(); if not started_at and session_date: started_at = session_date; if started_at and _valid_iso_date(started_at): payload event gains "started_at": started_at; elif started_at: warning "event startedAt not a valid ISO date → dropped"`.
- Points loop (~line 1140): `when = str(p.get("when") or "").strip(); if when and _valid_iso_date(when): payload point gains "when": when; elif when: warning "point when not a valid ISO date → dropped"`.

**Step 5 — `extract_session_v2` threads `session_date`:** kwarg added to the signature (D1); pass to `run_s1`, `run_s2`, `run_s4`, and `execute_embed`. Also thread into the empty-conversation short-circuit return? **No** — it returns no payload; leave shape unchanged.

**Step 6 — Update `__all__`** (line 1449) if helpers are exported (only if tests import them; prefer testing through public functions).

**Step 7 — Run the existing suite** — `uv run pytest tests/test_extractor_v2.py -v` must pass with NO changes to existing tests (byte-identical undated rendering is the regression guard). Then write the new tests (Tests section).

**Step 8 — Commit** (orchestrator commits; run `commit-workflow` per repo rules).

### Task 2: `commit_schema.py` + `hosted_api.py` — payload fields + server write

**Intent:** Layer-1 validation accepts the new date fields (extra="forbid" would otherwise reject dated payloads) and the server persists event dates.
**Acceptance:** `validate_payload_dict` passes payloads with AND without `when`/`started_at`; commit-path Event nodes carry `startedAt`.
**Files:**
- Modify: `tortoise/commit_schema.py` — `Point` (~218), `CommitEvent` (~330)
- Modify: `tortoise/hosted_api.py` — extracted-occurrences block (~3805)
- Test: `tests/test_extractor_v2.py` (schema assertion) or `tests/test_ingest_validation.py`

**Step 1 — `Point`:** add `when: str = Field(default="", max_length=40)` after `quote`.
**Step 2 — `CommitEvent`:** add `started_at: str | None = None` after `captured_at`.
**Step 3 — `hosted_api.py`:** in the per-event MERGE SET add `e.startedAt=coalesce(e.startedAt, $sat)` and add `"sat": ev.started_at or ev.captured_at or now` to params.
**Step 4 — Tests:** unit-validate a payload dict containing `when`/`started_at` (passes) and one without (passes, byte-identical).

### Task 3: `ingest_v2.py` — the dropped-at-call fix (eval path)

**Intent:** The eval harness actually hands the dataset's session date to the extractor and writes the resulting dates onto nodes.
**Acceptance:** A dated haystack session produces graph Points with `when` and Events with `startedAt`; an undated session writes neither (E2E-4 negative).
**Files:**
- Modify: `tools/longmem_eval/ingest_v2.py` — `_write_payload` (73), `ingest_haystack_v2` (179)
- Test: `tests/test_longmem_runner.py` (extend the #1369 v2-ingest test at ~661)

**Step 1 — `ingest_haystack_v2`:** pass `session_date=session_date or None` to `extract_session_v2` (line ~227).
**Step 2 — `_write_payload`:** add `session_date: str | None = None` kwarg; points → `when=p.get("when") or None` (only when truthy); events → `startedAt=ev.get("started_at") or ev.get("startedAt") or session_date` (only when truthy). Thread `session_date` at the call site (~line 248).
**Step 3 — Integration tests** (Tests section).

### Task 4: `sdk.py` — production BYOK commit path

**Intent:** Production `commit_session` sessions get date-anchored extraction (default = capture time).
**Acceptance:** `commit_session` passes a `session_date` (ISO now by default) through to `extract_session_v2`; explicit `session_date=` is honored.
**Files:**
- Modify: `tortoise/sdk.py` — `commit_session` (1523), `_commit_session_v2` (1567)
- Test: `tests/test_sdk_group3.py` or `tests/test_capture_session.py`

**Step 1 — `commit_session`:** add `session_date: str | None = None` kwarg (after `extractor`); pass to `_commit_session_v2`.
**Step 2 — `_commit_session_v2`:** accept `session_date`; resolve `None → datetime.now(timezone.utc).isoformat()`; pass to `extract_session_v2`.
**Step 3 — Unit test:** monkeypatched `extract_session_v2` records `session_date` — assert ISO now when unset, explicit value when passed.

---

## Tests

**Verification checklist (S28, #1515):** `session_date` kwarg on `extract_session_v2`; prompts anchor events to the date; points carry `when`; events carry `startedAt`.

| # | Layer | Test | Assertion |
|---|---|---|---|
| T1 | unit | `extract_session_v2` accepts `session_date` (mock model records prompts) | S1 system prompt contains `DATE ANCHOR` + the date when passed |
| T2 | unit | undated rendering is byte-identical | same mock; `session_date=None`/`""` → S1/S2/S4 prompts contain no date text; T1's dated run differs only by the anchor block |
| T3 | unit | OUTPUT_CONTRACT + S2/S4 guidance | `OUTPUT_CONTRACT` contains `"when"` and `"startedAt"`; S2_TMPL/S4_TMPL contain the date-anchor rules (assert substrings, mirroring `test_prompt_strip_dont_drop_operational`) |
| T4 | unit | `execute_embed` — points carry `when` | embed_list point with `when: "2026-08-01"` → payload point `"when": "2026-08-01"`; junk `when: "next tuesday"` → no key + warning; undated → no key |
| T5 | unit | `execute_embed` — events default to session_date | event without `startedAt` + `session_date="2026-08-01"` → payload `"started_at": "2026-08-01"`; explicit `startedAt` preserved; no session_date → no key |
| T6 | unit | `commit_schema` | payload points/events WITH `when`/`started_at` validate; WITHOUT validate (byte-identical) |
| T7 | integration | v2 ingest writes dates (extend `test_v2_ingest_writes_payload_with_evidence_marks`, test_longmem_runner.py:661) | `haystack_dates=["2026-08-01"]` + fake extractor returning `when`/`started_at` → graph `MATCH (p:Point {id:'pt_alpha'}) RETURN p.when` = `2026-08-01`; `MATCH (e:Event {content:'we decided X'}) RETURN e.startedAt` = `2026-08-01` |
| T8 | integration | undated session → no false date (E2E-4 owned negative) | `haystack_dates=[]` → no `when`/`startedAt` props on written nodes; `_write_payload` written with `session_date=None` writes no date props |
| T9 | integration | call-site threading | monkeypatched `extract_session_v2` (kwarg-recording) receives `session_date="2026-08-01"` from `ingest_haystack_v2` |
| T10 | unit | SDK commit path | monkeypatched `extract_session_v2` receives ISO-now `session_date` by default; explicit value honored |
| T11 | regression | full suites | `uv run pytest tests/test_extractor_v2.py tests/test_longmem_runner.py -v` green with no pre-existing test edits |

---

## Cross-Lane Interfaces

| Consumer lane | Contract E1 provides | Notes |
|---|---|---|
| **R5** (retrieval temporal — epic issue) | `Point.when` = ISO date string (absent when undated); `Event.startedAt` = ISO (present for all events in dated sessions) | R5 consumes via `point_props_for_hits` + event queries; date-weight RRF, time-ordered rendering, TR pool inclusion are R5's own issue |
| **E2** (state-value facts) | reuses the `when` slot — points carry verbatim value + `quote` + `when` | shared additive Point property; no per-lane field |
| **E5 / E7** (supersession / 4-way consolidation) | `when` is parseable (ISO) for newer-date-wins UPDATE decisions | E1 guarantees the parseable form; E5/E7 do the comparison |
| **E6** (bi-temporal, last) | `valid_at`/`invalid_at` seeded from `startedAt`/`when` | post-baseline; E1 is the prerequisite data |
| **P4** (parity) | commit path (Task 2/4) and eval path (Task 3) both persist dates | server `coalesce(ev.started_at, ev.captured_at, now)` = no regression for undated events |
| **Reader (M5/A1/A2)** | none — reader untouched in E1; `session_date` per-hit already surfaced (retrieve.py `_annotate_hits`) | E2E-4's "answers from date-anchored points" completes with R5's rendering |

---

## ⛔ CONDITIONAL GATE NOTES

- **`when` on Point — APPROVED additive property** (owner, per #1533). No new kind, no new edge type, no expansion pack — the epic ontology invariant holds. It DOES require, in this issue: (a) `commit_schema.Point.when` — **mandatory**, `extra="forbid"` rejects the field otherwise; (b) `docs/ONTOLOGY.md` §4.1 registration row for `when` (docs debt — fold into Task 2 as a one-line doc edit, or a tracked follow-up; the §4.7 cross-entity table's Point temporal column also gains `when`). `create_point` prop passthrough verified: `_sanitize_props` does not block `when`.
- **`startedAt` on payload events — no gate.** `startedAt` is a registered Event property (§4.5); the change is an additive `CommitEvent.started_at` payload field + a server-side `coalesce` write. Old payloads stay byte-identical.
- **Prompt edits to validated S1/S2/S4 templates — additive insert only** (D2). `S1_TMPL` is owner-approved; the `{date_anchor}` placeholder renders to `""` when undated so undated prompts are byte-identical. The S1/S2/S4 content-assertion tests (`test_prompt_strip_dont_drop_operational`, `test_prompt_supersession_rules`) must keep passing unchanged.
- **No architecture change** — `session_date` is a kwarg with a default; no new module, no signature breakage. `capture_session`/M2 extractor deliberately NOT in scope (it does not call `extract_session_v2`).
- **Payload byte-identical guarantee** — `when`/`started_at` keys are emitted only when non-empty (D4); undated-session payloads are byte-identical to today (same discipline as the #1350 supersessions field).

---

## Open Questions

1. **`commit_session` default = now vs date-blind until the eval proves value (D8).** Decided: default to capture time (anchoring is the point of E1; an undated production session is the bug). Alternative (keep `None` → date-blind for SDK callers) is one line away — flag if the 50-Q pilot (run protocol step 3) shows prompt drift hurts.
2. **`when` and content-addressing:** point ids are content-only (`pt_<sha>`), so a fact re-stated across sessions keeps the FIRST writer's `when` (cross-session collision, same as the #1369 P2 first-writer rule). Rejected: including `when` in the id fragments dedup. E5/E7 supersession is the correction path. Confirm the first-writer-wins semantics are acceptable for R5's date-weight.
3. **sessionCaptured Event `startedAt` = `captured_at` (now) for replayed historical sessions** — a bi-temporal wart (the session-event claims "now", the content says "then"). Out of scope (hosted-capture semantics, P4 adjacency); track separately.
4. **`haystack_dates` format variance** (bare date vs full ISO datetime): E1 passes through as-is and `_valid_iso_date` accepts the `YYYY-MM-DD` prefix — no normalization. If a dataset mixes formats, R5's date-weight comparison needs a shared normalization; decide there.
5. **M2 capture path (`_extract_session_llm`) date anchoring** — out of scope (v2 extractor is the product path since #1385). Follow-up candidate if capture-path recall lags the BYOK path.
