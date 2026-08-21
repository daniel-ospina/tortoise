<!-- research-path: docs/epics/2026-08-20-1509-extractor-v3/02-research-brief.md -->

# #1529 — P1 Fail-Closed Capture (extraction errors surface; truthful extraction_mode) — Implementation Plan

> **For Pi:** Use `executing-plans` to implement this plan task-by-task.

**Goal:** Make the capture path fail closed on BOTH extraction branches: extraction errors surface on the response (SDK `ok=False` + `errors`/`warnings`; hosted non-200 or additive body fields), `extraction_mode` is truthful (`llm | v2 | empty | error`), never a silent `extracted: 0`, and an empty/blank conversation never reports `ok=True`.

**Team:** epistemic-team

**Architecture:** Three in-repo Python changes. (1) `tortoise/sdk.py` — the two capture extractors become fail-closed: `_extract_session_v2` (the **default** capture extractor, `#1350`; the issue's "_discards `out["errors"]` today" is literal — it does `payload = out.get("payload") or {}` and never reads `out["errors"]`) starts consulting `out["errors"]`/`out["warnings"]` and its silent `except: pass` point-write loop becomes counted; `_extract_session_llm` (M2, behind `TORTOISE_SESSION_EXTRACTOR=m2`) stops raising and returns structured errors. Both return the SAME structured `{"points", "errors", "warnings", "mode"}` dict so the capture assembly is branch-independent. (2) `capture_session` (SDK) + POST /v1/sessions (hosted) gain a pre-mutation empty/blank gate (whole-conversation transcript-emptiness, the same signal the extractors use) and their responses gain `ok`/`errors`/`warnings` with a truthful `extraction_mode`. (3) Hosted additionally: 422 for empty/blank, Pydantic-validator + turn-loop non-string-content hardening (`#721` parity — kills a raw-500/partial-write hole), and the CLI consumer (`_cmd_session_capture`) stops reporting success on `mode="error"`. All new fields are **response-contract only** — zero graph/ontology change. E3's `source_turn_id` passes through capture untouched (whitelisted passthrough on both carriers; no-clobber re-capture guard). The v2 COMMIT path (`_commit_session_v2`) already fails closed (locked by existing tests) — verified, no production change.

### Pattern Research

> **Findings date:** 2026-08-20
> **Gate:** skipped — the plan touches zero NEW third-party dependencies (fastapi, pydantic, httpx, embedded FalkorDBLite all already used in-repo with 2+ call sites; changes are in-repo Python only: `tortoise/sdk.py`, `tortoise/hosted_api.py`, `tortoise/__main__.py`, tests). Prior research intake (Step A, always runs): epic 03-scope (P1 item + boundary rationale), 04-plan §5 Architecture surface ownership (§18 capture events, §26 extraction_mode) + §6 Interfaces (capture-response contract row), 05-detailed-e2e E2E-8 (+ failover variant, owned negatives: empty/blank conversation, dead key, fatal-4xx guard), and test-design #1515 (28-surface map: **surface 18 — "THE P1 silent-partial-capture hole — empty transcript → 200 extracted:0"**; **surface 26 — "lying extraction_mode, warnings clobber"**).

### Integration Surface Map

| Surface (#1515) | Data Flow | Contract | Test Layer |
|---|---|---|---|
| 18 — capture events (SDK `capture_session`) | `conversation` → transcript gate → Session/turn loop → extractor dispatch (`TORTOISE_SESSION_EXTRACTOR`: v2 default / `=m2`) → assembly → response | `ok`/`errors`/`warnings` fields; `extraction_mode ∈ {llm, v2, empty, error}` (truthful per branch); empty/blank → `ok=False`, `mode="empty"`, `turns=0`, **pre-mutation** (no Session stub); extraction failure → `ok=False`, `mode="error"`, errors carry `TypeName: message`, turn points + Event + Source still land; never `ok=True` on failure | Integration (embedded FalkorDBLite + `TORTOISE_SESSION_LLM_MOCK=1`; failure injection per branch: v2 → monkeypatch `tortoise.extractor_v2.extract_session_v2`, M2 → duck-typed extractor + `TORTOISE_SESSION_EXTRACTOR=m2`) |
| 18 — capture events (hosted POST /v1/sessions) | HTTP → provider gate (503) → turn cap (400) → **transcript gate (422)** → quota (402) → turn loop (**content coercion**) → dispatch → body | empty/blank → 422 pre-write; extraction failure → **200 + additive `errors`/`warnings`** + `mode="error"` (turns landed — documented partial capture); non-string turn content coerced (never a mid-loop 500/partial write); success body gains `errors`/`warnings` (additive); `ok` signal = HTTP status (no body `ok` field — hosted convention, see D2) | Integration (FastAPI TestClient + temp DB, `_patch_tortoise_sdk_init` fixture; graph assertions via `ha_mod._make_sdk(namespace=TEST_TEAM_ID)` — the fixture team, NOT `"test-team-722"`) |
| 26 — `extraction_mode` + error/warnings contract | extractor structured result → both surfaces | mode never claims success on failure/empty — `"empty"` **always** co-occurs with `ok=False` + an error entry (self-consistent on every path incl. both internal guards); warnings additive (never clobber); zero-extraction on non-empty transcript → additive warning, `ok=True` (nothing extractable ≠ failure) | Unit (per-branch failure seams) + integration |
| 26 — v2 COMMIT path (`_commit_session_v2` / `extractor_v2.extract_session_v2`) | `out["errors"]` → `ok`/`errors` → POST gate | **Already fail-closed** — `ok=False` + errors on extraction failure (`test_v2_error_path_reports_errors`), empty → `ok=False` + "no payload" (`test_v2_empty_conversation_not_ok`), Layer-1-rejected payload never POSTed (`test_v2_layer1_rejected_payload_not_posted`). This issue only VERIFIES the lock (Task 2 checklist note) — no production change | Existing tests |
| E3 passthrough (internal seam, both carriers) | v2 payload point dicts / M2 folded statement dicts → extracted dicts → response `points` | `source_turn_id` (and any future E3 prop) flows through capture **unchanged** — extracted dicts carry a WHITELISTED `props` (`_CAPTURE_PASSTHROUGH_PROPS`), never a rebuilt `{id, kind, text}`-only shape and never internal projection state; re-capture turn-point MERGE SET list must not include/overwrite `source_turn_id`; idempotency scoped to turn points (extraction points/Event fresh per capture BY DESIGN) | Unit passthrough + integration no-clobber guards (Task 1) |

**Bug pattern flags:** silent-partial-capture (the P1 hole — empty transcript → 200 extracted:0; **and the v2 wrapper dropping `out["errors"]`**), lying `extraction_mode` (mode must distinguish failure/empty from success), warnings clobber (additive-only discipline), exception-swallowing (error string must preserve `TypeName:` for P2's fatal-4xx classification; the v2 point-write `except: pass` must be counted, not invisible), gate/extractor divergence (single transcript-emptiness signal), post-run write-segment failures (fold/wiring/Source/audit must not escape as raw exceptions after partial writes).

---

## Design Decisions

### D1 — The P1 gap is the CAPTURE assembly; the issue's "_extract_session_v2 discards out[errors]" is literal
On the current origin/main (worktree `1509-plans`), `capture_session`/hosted dispatch extraction: `TORTOISE_SESSION_EXTRACTOR == "m2"` → `_extract_session_llm` (M2, two-stage), **else → `_extract_session_v2` (the `#1350` 5-stage pipeline — the DEFAULT, locked by `test_capture_session_v2_default_routes_and_writes`)**. Both return `[{id, kind, text}]`; both are silent on failure:
- **v2 (default):** sdk.py `_extract_session_v2` (L1957) does `payload = out.get("payload") or {}` and never reads `out["errors"]`/`out["warnings"]` — a dead key makes `extractor_v2.extract_session_v2` return `errors=["S1 chunk failed: …"]` + `payload=None`, the wrapper writes nothing, returns `[]`, and `capture_session` reports `{"extracted": 0, "extraction_mode": "llm"}` — **the exact silent-`extracted: 0` / lying-`extraction_mode` hole** the issue targets. Its point-write loop also swallows per-point failures with `except Exception: pass` (invisible partial writes).
- **M2 (behind env var):** `_extract_session_llm` RAISES `RuntimeError` on `extractor.run` failure after turn points landed — SDK callers get a raw exception, hosted an uncaught 500, no structured surface.
- **Both:** empty conversation → `extracted: 0` with hardcoded `mode="llm"`.

The v2 COMMIT path (`_commit_session_v2`) is a SEPARATE surface that already fails closed (consulted `out["errors"]`, `ok=False`, empty → "no payload") — locked by existing tests; this issue verifies it, no change.

### D2 — Capture response contract: add `ok` / `errors` / `warnings`; `extraction_mode` becomes a truthful 4-state enum
SDK `capture_session` response becomes:
`{"session_id", "turns", "extracted", "points", "extraction_mode", "ok", "errors", "warnings"}` (backward-compatible superset).

`extraction_mode` semantics (truthful per branch — what ACTUALLY ran):
- `"v2"` — the v2 5-stage extractor ran and **completed** (points may be 0 only when nothing was extractable; that case carries an additive warning, never a silent 0).
- `"llm"` — the M2 extractor ran and completed (same zero-point rule).
- `"empty"` — the conversation has no extractable content (empty or blank — transcript empty after the same normalization the extractors use); **always** co-occurs with `ok=False` and a non-empty `errors` entry — on every path, including both internal defense-in-depth guards (which return their own error entries; callers additionally map `mode == "empty"` → `ok=False` as belt-and-braces).
- `"error"` — extraction was attempted and **failed**; `ok=False` + `errors` carry `f"{type(e).__name__}: {e}"` (v2: from `out["errors"]`; M2: from the caught exception).

The `"error"` value kills the surface-26 "lying extraction_mode" pattern: a consumer reading only `extraction_mode` can never mistake a failure for success. P2 extends the enum with route values (failover variant) — see Cross-Lane. **Contract note:** `extracted > 0` alongside `ok=False` is possible (partial-emission failure) and must NOT be read as success — `ok` is the success signal, never `extracted`. **Channel semantics (E2E-8 mapping):** on the failure path the surface is the additive `errors` list + `mode="error"` — strictly stronger than the checklist's "additive warnings" parenthetical; `warnings` is reserved for non-fatal degradations (Event/Source write failures, zero-extraction, v2 skipped-points). The `errors` channel is the loud failure surface; do not duplicate errors into warnings.

Hosted: `ok` is the HTTP status (hosted convention — no body `ok` field; the one status-only consumer, the CLI, is fixed explicitly in Task 3 Step 5b). Flagged in Open Questions for review.

### D3 — Empty/blank conversation: one gate on whole-conversation transcript emptiness, pre-mutation
The gate uses the **same signal the extractors use** — `_session_llm_transcript(conversation)` returning an empty string — so the gate and the extractors cannot disagree. **The gate is WHOLE-conversation** (one transcript per conversation, verified by executing `_session_llm_transcript`): "blank" means no turn contributes a ≥3-char sentence (`_SENT` per-sentence floor, sdk.py ~L189) — e.g. `[]`, `[{"content": "ok"}]`, `[{"content": None}]`, a whitespace-only 5000-char turn, single-turn `[{"content": 0}]` (coerces to `"0"`, below floor), `[{"role":"user"}]` (missing content key). A MIXED conversation containing any non-blank sentence (e.g. `"False"` → 5 chars) stays non-blank and stores every coerced turn exactly as today (`test_capture_session_falsy_non_string_content_not_swallowed` stays green — verified).

- **Hosted:** after the provider 503 gate and the turn-cap 400, before the quota estimate and any write → `HTTPException(422, "conversation has no extractable content (empty or blank)")`. (422 over 400: same family as the existing Pydantic 422 for >5000-char content; a handler-level check because blankness is transcript-derived, not a simple `min_length`.)
- **SDK:** early return before the Session MERGE and turn loop → `{"session_id": <generated>, "turns": 0, "extracted": 0, "points": [], "extraction_mode": "empty", "ok": False, "errors": ["no extractable content — empty or blank conversation"], "warnings": []}` — **nothing committed**, not even a Session stub. `turns` reports the **committed** state (0), never the input length. The `session_id` returned is the id a retry would use.

### D4 — Extraction failure: turn points land, errors surface, `mode="error"` — the fail-closed surface covers the WHOLE extraction stage, branch-independently
Both `_extract_session_v2` and `_extract_session_llm` return `{"points", "errors", "warnings", "mode"}` (D5). No extraction-stage exception escapes; the assembly consumes whichever branch ran:

- **v2 (default):** read `out["errors"]` + `out["warnings"]` from `extractor_v2.extract_session_v2`; `mode="error"` when `out["errors"]` non-empty; the point-write loop's `except: pass` becomes **counted** — per-point write failures append a `warnings` entry (e.g. `"N extracted points failed to write"`), never silent. Partial payload writes (some points landed before a failure) are reported (extracted == len(points) ≥ 0 with `ok=False`).
- **M2:** the fail-closed surface wraps `extractor.run(...)`, `split(fold(...))`, and the CONTAINS wiring (partial points collected before a mid-loop failure remain reported).
- The exception/error **class name is preserved** — P2's provider-routing state machine needs it to distinguish fatal 4xx (must NOT fail over, per E2E-8 owned negative) from transient failures.
- **SDK:** capture continues past extraction — Session, turn points, `sessionCaptured` Event, and the agentSession Source all still land (the capture attempt is fully documented), response `ok=False`, errors surfaced.
- **Hosted:** **200 with additive `errors`/`warnings`** rather than non-200 — the mutation already occurred (turn points landed), and E2E-8 explicitly permits "non-200 **or** additive warnings". A non-200 would hide that the turns are in the graph. The empty-conversation case is the non-200 one (nothing mutated). The no-key gate stays 503 (pre-mutation). (Alternative — non-200 for extraction failure — is flagged in Open Questions.)
- **Post-extraction bookkeeping failures are non-fatal and surfaced, not thrown:** the Event write failure, the eventId-stamping failure, and `_materialize_session_source` failure ALL append an additive `warnings` entry (in addition to the existing log line) and continue — never indistinguishable from a clean capture, never a raw exception after partial writes. Hosted's `_async_audit` gets the same log-only treatment: a committed capture must never 500 over audit bookkeeping.

### D5 — Both extractors return ONE structured contract; the assembly is branch-independent
`_extract_session_v2` and `_extract_session_llm` return identical dicts: `{"points": [...], "errors": [...], "warnings": [...], "mode": "v2"|"llm"|"error"|"empty"}`. The dispatch in `capture_session`/hosted is unchanged (`TORTOISE_SESSION_EXTRACTOR`); only the consume-site changes — one assembly reads whichever result. The no-extractor `ValueError` (SDK) / 503 (hosted gate) stay pre-extraction, unchanged. Kept unchanged: `extractor.version` agent_id stamping (M2), the v2 mock seam (`_V2SessionMock`), and the v2 provider gate (`_extract_session_v2`'s own key check).

### D6 — Zero-extraction on a non-empty transcript is an additive warning, not a failure
If extraction completes with no exception/errors but emits 0 points (LLM returning malformed/empty output), append `"LLM extraction produced no points"` to `warnings`; `ok=True`, `mode` = the branch (`v2`/`llm`). This closes the last silent-`extracted: 0` window after the empty gate. Locked at BOTH surfaces.

### D7 — Warnings are additive; never clobbered
The capture response's `warnings` list is additive-by-contract: any future layer (hosted domain rules, P2 route notes, Event/Source failures, v2 skipped-points) concatenates, never overwrites — the discipline already enforced on the commit path. A clobbering `warnings = [...]` reassignment must fail a two-source test (Task 2).

### D8 — E3 `source_turn_id` is never clobbered by capture (owner note)
Two invariants, guarded by tests (Task 1):
1. **Passthrough (WHITELISTED, both carriers):** extracted point dicts carry a `props` superset built from `_CAPTURE_PASSTHROUGH_PROPS = frozenset({"source_turn_id"})` (extended when E3 lands `search_keys`/`when`/`quote`). v2 carrier: the payload point dict `pt` (E3's fields arrive on payload points); M2 carrier: the folded statement dict (E3 writes via the projection). A whitelist (not a blacklist) is deliberate: the folded statement dict also carries internal projection state (`provenance` run_id/source, status, createdAt, operator, speaker) that must never leak into the public capture response. (M2 test injects via `add_point(**fields)` — the real carrier; a synthetic `PointUpdated` event does NOT fold — `projection._apply_one` drops it.)
2. **Re-capture:** the turn-point `MERGE … SET` list (content, pointKind, is_operator, speaker, is_episodic, status, createdAt, updatedAt, content_hash) does **not** include `source_turn_id`, so re-capturing a session leaves an existing `source_turn_id` intact. **Idempotency is scoped to the turn stream**: extracted points are fresh per capture and a new `sessionCaptured` Event is created per capture — existing intended behavior (locked by the worktree's M2 `test_capture_session_llm_points_fresh_per_capture` and the v2-default tests).

### D9 — No graph or ontology change
All new fields are response-contract only: SDK dict keys, HTTP body keys, `HTTPException.detail`, CLI exit codes. `extraction_mode` is **not** persisted to the Session node. If a later issue needs extraction status queryable in the graph, that is an additive Session property (permitted by the epic's ontology invariant — no new kinds/edges) and must be proposed separately (see ⛔ Conditional gates).

### D10 — Non-string turn content fails closed at the hosted layer (validator guard + turn-loop coercion, #721 parity)
`SessionRequest.conversation` is `list[dict]` with an **untyped inner dict**, and `valid_conversation` runs `len(content)` with no isinstance guard — **verified live**: `{"content": None}` / `12345` / `0` / `False` / `3.14` raise `TypeError` INSIDE the Pydantic validator (Pydantic v2 propagates non-ValueError exceptions) → raw 500 before the handler runs; dict content passes (`len(dict)` = key count ≤ 5000) and then `content[:5000]` raises `TypeError` AFTER the Session MERGE → raw 500 with a partial write. Two-layer fix (SDK's #721 pattern, aligned): (1) **validator guard** — `valid_conversation` skips the length check for non-str content (`if not isinstance(content, str): continue`); (2) **turn-loop content coercion** — the handler coerces `content` with the same `isinstance`-first expression as the SDK. **`role` is NOT coerced here** (hosted stores non-str roles raw today; `role` parity is P4's `speaker` lane, not a crash risk) — D10's scope is crash-prevention + blank-gate consistency only. Note: the validator `continue` means the ≤5000 length check no longer rejects non-str content (dicts previously capped via `len(dict)`); the stored turn text is still truncated at 5000, so the new contract is "coerce, then store capped" — a documented widening (OQ12).

---

## Implementation Steps

### Task 1: Fail-closed extractor seam — `_extract_session_v2` consults `out["errors"]`; `_extract_session_llm` structured; point passthrough (both carriers)

**Intent:** Make BOTH capture extractors fail-closed at the seam: the default v2 wrapper surfaces `extractor_v2.extract_session_v2`'s `out["errors"]`/`out["warnings"]` and counts its silent write-skips; the M2 wrapper returns structured errors instead of raising; extracted point dicts carry whitelisted `props` so E3 fields pass through.
**Acceptance:** Both `_extract_session_v2` and `_extract_session_llm` return `{"points", "errors", "warnings", "mode"}`; a v2 run with `out["errors"]` → `mode="error"` with the errors surfaced; a raising M2 extractor → `mode="error"` with `TypeName: message` (no raise); M2 fold/wiring failures → structured; v2 per-point write failures → counted warnings; empty-transcript internal guards → `mode="empty"` WITH an error entry; `source_turn_id` injected via either carrier appears in `props`; the no-extractor `ValueError`s are unchanged.

**Files:**
- Modify: `tortoise/sdk.py` (`_extract_session_v2` ~L1957–2067, `_extract_session_llm` ~L1894–1955; module constant `_CAPTURE_PASSTHROUGH_PROPS`)
- Test: `tests/test_capture_session.py` (new `# ── P1 (#1529) fail-closed capture` section)

**Step 1: Write the failing tests** (append to `tests/test_capture_session.py`):

```python
class _FailingSessionExtractor:
    """Duck-typed M2 extractor whose run() raises — the dead-key / mid-run
    failure P1 must surface, not swallow (E2E-8 dead-key negative)."""
    version = "failing@0"

    def run(self, transcript, source_id, api):
        raise RuntimeError("provider returned 500")


class _PartialFailingSessionExtractor:
    """Emits ONE point then raises — the partial-emission case: extracted>0
    must never be read as success (ok is the signal)."""
    version = "partial@0"

    def run(self, transcript, source_id, api):
        api.add_point("decision: ship serve first", {"source": source_id})
        raise RuntimeError("provider rate limited mid-run")


class _EmptyOutputExtractor:
    """'Succeeds' but emits no points — the last silent extracted:0 window."""
    version = "empty-out@0"

    def run(self, transcript, source_id, api):
        pass


def _v2_out(payload=None, errors=(), warnings=()):
    """Shape-complete extractor_v2.extract_session_v2 output for seams."""
    return {"session_id": "sess_p1", "story_arc": "", "embed_list": {},
            "search": {"mode": "embedded", "degraded": True},
            "payload": payload, "chain_notes": [], "link_before_create": [],
            "supersessions": [], "warnings": list(warnings),
            "minted_kinds": [], "stats": {}, "errors": list(errors)}


# ── v2 branch (DEFAULT — the issue's "_extract_session_v2 discards
#    out[errors]" checklist item) ────────────────────────────────────────

def test_extract_session_v2_consults_out_errors(sdk, monkeypatch):
    """P1: the v2 wrapper must surface extractor_v2 out['errors'] — a dead
    key yields mode='error' + errors, never a silent extracted:0 (E2E-8)."""
    import tortoise.extractor_v2 as ev2
    monkeypatch.setattr(ev2, "extract_session_v2",
                        lambda *a, **kw: _v2_out(errors=["RuntimeError: provider returned 500"]))
    res = sdk._extract_session_v2(CONV, "sess_p1", "2026-08-20T00:00:00+00:00")
    assert res["mode"] == "error"
    assert res["points"] == []
    assert any("provider returned 500" in e for e in res["errors"])
    assert res["warnings"] == [], "failure carries errors, never warnings"


def test_extract_session_v2_surfaces_warnings_and_zero_points(sdk, monkeypatch):
    """P1 (D6): completed-but-empty v2 output (payload None, no errors) is
    an additive warning, not a silent 0."""
    import tortoise.extractor_v2 as ev2
    monkeypatch.setattr(ev2, "extract_session_v2",
                        lambda *a, **kw: _v2_out())
    res = sdk._extract_session_v2(CONV, "sess_p1", "2026-08-20T00:00:00+00:00")
    assert res["mode"] == "v2"
    assert res["points"] == []
    assert any("no points" in w for w in res["warnings"])
    assert res["errors"] == []


def test_extract_session_v2_passthroughs_source_turn_id(sdk, monkeypatch):
    """E3 passthrough (v2 carrier): a payload point carrying source_turn_id
    (E3's fields arrive on payload points) flows through `props` unchanged."""
    import tortoise.extractor_v2 as ev2
    payload = {"session_id": "sess_p1", "story_arc": "", "entities": [],
               "points": [{"id": "p-v2-1", "content": "we decided X",
                           "pointKind": "statement", "source_turn_id": "t0"}],
               "operators": [], "events": [], "supersessions": [],
               "client_commit_id": "ccid"}
    monkeypatch.setattr(ev2, "extract_session_v2",
                        lambda *a, **kw: _v2_out(payload=payload))
    res = sdk._extract_session_v2(CONV, "sess_p1", "2026-08-20T00:00:00+00:00")
    assert res["points"], "payload point must land"
    assert any(p.get("props", {}).get("source_turn_id") == "t0"
               for p in res["points"]), res["points"]


def test_extract_session_v2_counts_point_write_skips(sdk, monkeypatch):
    """P1: the v2 point-write loop's silent `except: pass` becomes counted —
    a point that fails to write surfaces as an additive warning, never an
    invisible partial write."""
    import tortoise.extractor_v2 as ev2
    payload = {"session_id": "sess_p1", "story_arc": "", "entities": [],
               "points": [{"id": "p-ok", "content": "we decided X",
                           "pointKind": "statement"},
                          {"id": "", "content": "no id -> skipped"},
                          {"id": "p-boom", "content": "we decided Y",
                           "pointKind": "statement"}],
               "operators": [], "events": [], "supersessions": [],
               "client_commit_id": "ccid"}
    monkeypatch.setattr(ev2, "extract_session_v2",
                        lambda *a, **kw: _v2_out(payload=payload))
    _real_create_point = sdk.create_point

    def _boom_point(*args, **kwargs):
        if args and args[1] == "we decided Y" or kwargs.get("content") == "we decided Y":
            raise RuntimeError("point write failed")
        return _real_create_point(*args, **kwargs)

    monkeypatch.setattr(sdk, "create_point", _boom_point)
    res = sdk._extract_session_v2(CONV, "sess_p1", "2026-08-20T00:00:00+00:00")
    assert res["points"], "the successful point is reported"
    assert any("failed to write" in w or "skipped" in w for w in res["warnings"]), res


# ── M2 branch (behind TORTOISE_SESSION_EXTRACTOR=m2) — seam tests call the
#    method directly (no env var needed) ─────────────────────────────────

def test_extract_session_llm_failure_is_structured_not_raised(sdk, monkeypatch):
    """P1: M2 LLM failure returns mode='error' with the exception class
    preserved (P2 needs TypeName to classify fatal 4xx) — never raises.
    (`_extract_session_llm` is an INSTANCE method — bound via the sdk
    fixture, which also provides the live projection.)"""
    monkeypatch.setattr("tortoise.sdk._build_session_llm_extractor",
                        lambda: _FailingSessionExtractor())
    res = sdk._extract_session_llm(CONV, "sess_p1", "2026-08-20T00:00:00+00:00")
    assert res["mode"] == "error"
    assert res["points"] == []
    assert any("RuntimeError" in e and "500" in e for e in res["errors"])


def test_extract_session_llm_partial_emission_reports_points(sdk, monkeypatch):
    """P1: a run that emitted points then failed reports them (extracted>0
    with ok=False — the caller's ok flag is the success signal)."""
    monkeypatch.setattr("tortoise.sdk._build_session_llm_extractor",
                        lambda: _PartialFailingSessionExtractor())
    res = sdk._extract_session_llm(CONV, "sess_p1", "2026-08-20T00:00:00+00:00")
    assert res["mode"] == "error"
    assert len(res["points"]) >= 1, "partial points must still be reported"
    assert any("RuntimeError" in e for e in res["errors"])


def test_extract_session_llm_fold_failure_is_structured(sdk, monkeypatch):
    """P1: a failure AFTER run() (fold) stays inside the fail-closed surface
    — no raw exception after partial writes. Partial emitter documents the
    orphan window (projection writes points during run(); a fold failure
    leaves unowned statement nodes — accepted and visible, not silent)."""
    import tortoise.projection as proj

    class _BoomFoldCaller:
        version = "boom-fold@0"
        def run(self, transcript, source_id, api):
            api.add_point("decision: ship serve first", {"source": source_id})

    monkeypatch.setattr("tortoise.sdk._build_session_llm_extractor",
                        lambda: _BoomFoldCaller())
    monkeypatch.setattr(proj, "fold",
                        lambda events: (_ for _ in ()).throw(RuntimeError("fold blew up")))
    res = sdk._extract_session_llm(CONV, "sess_p1", "2026-08-20T00:00:00+00:00")
    assert res["mode"] == "error"
    assert any("fold blew up" in e for e in res["errors"])


def test_extract_session_llm_wiring_failure_is_structured(sdk, monkeypatch):
    """P1: a mid-CONTAINS-wiring failure stays inside the fail-closed
    surface and the response never silently diverges from the graph: points
    wired before the raise are reported; graph-side orphan state is pinned.
    NOTE: the wiring query MATCHes the Session — pre-create it (the seam
    bypasses capture_session, which is what creates the Session)."""
    sdk._get_proj().g.query("MERGE (s:Session {id:'sess_p1'})")

    class _EmitThenBoomWiring:
        version = "wiring-boom@0"
        def run(self, transcript, source_id, api):
            api.add_point("decision: ship serve first", {"source": source_id})
            api.add_point("decision: deploy second", {"source": source_id})

    monkeypatch.setattr("tortoise.sdk._build_session_llm_extractor",
                        lambda: _EmitThenBoomWiring())
    _real_query = sdk._get_proj().g.query
    calls = {"n": 0}

    def _boom_on_second(query, **params):
        if "CONTAINS" in query:
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("falkordb transient write failure")
        return _real_query(query, **params)

    monkeypatch.setattr(sdk._get_proj().g, "query", _boom_on_second)
    res = sdk._extract_session_llm(CONV, "sess_p1", "2026-08-20T00:00:00+00:00")
    assert res["mode"] == "error"
    assert any("transient write failure" in e for e in res["errors"])
    assert len(res["points"]) == 1, "the point wired before the failure stays reported"
    proj = sdk._get_proj()
    stmts = proj.g.query(
        "MATCH (p:Point) WHERE p.pointKind IS NULL RETURN count(p)").result_set
    assert stmts[0][0] == 2, "both emitted points exist in the graph (orphan pinned)"
    wired_n = proj.g.query(
        "MATCH (s:Session {id:$sid})-[:CONTAINS]->(p:Point) "
        "WHERE p.pointKind IS NULL RETURN count(p)",
        params={"sid": "sess_p1"}).result_set
    assert wired_n[0][0] == 1, "exactly one point is CONTAINS-wired (the reported one)"


def test_extract_session_llm_empty_guard_is_self_consistent(sdk):
    """P1 (D2): the internal empty-transcript guard (defense-in-depth) must
    return mode='empty' WITH an error entry — a regression to errors=[] would
    make capture compute ok=True on the empty path (the 'lying extraction_mode'
    bug)."""
    for conv in ([], [{"role": "user", "content": "ok"}]):
        res = sdk._extract_session_llm(conv, "sess_p1", "2026-08-20T00:00:00+00:00")
        assert res["mode"] == "empty", res
        assert any("empty" in e.lower() for e in res["errors"]), res
        assert res["warnings"] == []


def test_extract_session_llm_zero_output_warns(sdk, monkeypatch):
    """P1: completed-but-empty M2 extraction is an additive warning, not a
    silent 0 and not a fake failure."""
    monkeypatch.setattr("tortoise.sdk._build_session_llm_extractor",
                        lambda: _EmptyOutputExtractor())
    res = sdk._extract_session_llm(CONV, "sess_p1", "2026-08-20T00:00:00+00:00")
    assert res["mode"] == "llm"
    assert res["points"] == []
    assert any("no points" in w for w in res["warnings"])
    assert res["errors"] == []


def test_extract_session_llm_passthroughs_source_turn_id(sdk, monkeypatch):
    """E3 passthrough (M2 carrier): source_turn_id injected via
    add_point(**fields) — the carrier E3's projection will use — flows
    through `props` unchanged. (A synthetic PointUpdated event does NOT
    fold; the test uses the real carrier.)"""
    from tortoise.api import EventAPI

    class _StampingAPI(EventAPI):
        def add_point(self, content, provenance, **fields):
            fields["source_turn_id"] = "session_x_t0"
            return super().add_point(content, provenance, **fields)

    monkeypatch.setattr("tortoise.api.EventAPI", _StampingAPI)
    res = sdk._extract_session_llm(CONV, "sess_p1", "2026-08-20T00:00:00+00:00")
    assert res["points"], "CONV must extract points"
    assert any(p.get("props", {}).get("source_turn_id") == "session_x_t0"
               for p in res["points"]), res["points"]
```

**Step 2: Run to verify red**

Run: `uv run pytest tests/test_capture_session.py -k "extract_session_v2 or extract_session_llm" -v`
Expected: FAIL — `_extract_session_v2` returns a bare list and discards errors (no `mode`/`errors`/`warnings` keys); `_extract_session_llm` raises on the failing extractor; no `props` keys.

**Step 3: Implement** — `tortoise/sdk.py`:

Module constant (near the other capture helpers):

```python
_CAPTURE_PASSTHROUGH_PROPS = frozenset({"source_turn_id"})
# E3 whitelist — extend when E3 lands search_keys/when/quote (#1529).
# Deliberately a whitelist: folded statement dicts carry internal projection
# state (provenance run_id/source, status, createdAt, operator, speaker)
# that must never leak into the public capture response.
```

`_extract_session_v2` (L1957): consult `out["errors"]`/`out["warnings"]`, count write-skips, build the structured result:

```python
        out = extract_session_v2(model, conversation, sdk=self,
                                 session_id=session_id)
        payload = out.get("payload") or {}
        errors = [f"{e}" if isinstance(e, str) else f"{type(e).__name__}: {e}"
                  for e in (out.get("errors") or [])]
        warnings = list(out.get("warnings") or [])
        proj = self._get_proj()
        ...  # entities loop unchanged ...

        # ── points + aboutObject edges + session CONTAINS ──
        extracted: list[dict] = []
        skipped = 0
        for pt in payload.get("points", []) or []:
            pid = str(pt.get("id", "")).strip()
            content = str(pt.get("content", "")).strip()
            if not pid or not content:
                skipped += 1
                continue
            try:
                self.create_point(...)  # unchanged
                ...  # aboutObject + CONTAINS unchanged ...
                props = {k: v for k, v in pt.items()
                         if k in _CAPTURE_PASSTHROUGH_PROPS}   # E3 whitelist
                extracted.append({
                    "id": pid, "kind": "statement", "text": content[:200],
                    "props": props})
            except Exception as e:  # P1 #1529: counted, never silent
                skipped += 1
                errors.append(f"{type(e).__name__}: point write failed for {pid}: {e}")
        if skipped:
            warnings.append(f"{skipped} extracted point(s) failed to write")
        ...  # events + operators loops unchanged (keep their existing guards) ...
        if not errors and not extracted:
            warnings.append("LLM extraction produced no points")
        mode = "error" if errors else "v2"
        return {"points": extracted, "errors": errors,
                "warnings": warnings, "mode": mode}
```

`_extract_session_llm` (L1894): structured result, fail-closed surface over the whole stage, whitelisted props (same shape as Task 1 of prior revisions — replace the tail):

```python
        transcript, _est = _session_llm_transcript(conversation)
        if not transcript.strip():
            return {"points": [], "warnings": [],
                    "errors": ["no extractable content — empty or blank conversation"],
                    "mode": "empty"}
        from tortoise.api import EventAPI
        from tortoise.projection import fold, split
        log = _InMemoryEventLog()
        api = EventAPI(log, initiated_by="extractor", agent_id=extractor.version,
                       projection=self._get_proj())
        source_id = f"session:{session_id}"
        errors: list[str] = []
        try:
            extractor.run(transcript, source_id, api)
        except Exception as e:
            errors.append(f"{type(e).__name__}: {e}")
        extracted: list[dict] = []
        proj = self._get_proj()
        try:
            statements, _operators = split(fold(log.read_all()))
            for p in statements:
                pid = p["id"]
                proj.g.query(
                    "MATCH (s:Session {id:$sid}), (p:Point {id:$pid}) "
                    "MERGE (s)-[:CONTAINS]->(p)",
                    params={"sid": session_id, "pid": pid})
                props = {k: v for k, v in p.items()
                         if k in _CAPTURE_PASSTHROUGH_PROPS}
                extracted.append({
                    "id": pid,
                    "kind": p.get("pointKind") or "statement",
                    "text": p.get("content", "")[:200],
                    "props": props})
        except Exception as e:
            errors.append(f"{type(e).__name__}: {e}")
        warnings: list[str] = []
        if not errors and not extracted:
            warnings.append("LLM extraction produced no points")
        mode = "error" if errors else "llm"
        return {"points": extracted, "errors": errors,
                "warnings": warnings, "mode": mode}
```

**Step 4: Run to verify green**

Run: `uv run pytest tests/test_capture_session.py -k "extract_session_v2 or extract_session_llm" -v`
Expected: the ten new seam tests PASS. `capture_session`/hosted still consume the bare-list contract until Task 2 — the pre-existing capture tests fail until then. **Note: the hosted capture surface is ALSO expected-broken between Task 1 and Task 3 — but NOT with a 500.** The stamping comprehension `[p["id"] for p in extracted]` over the now-dict result raises `TypeError`, which lands INSIDE the handler's non-fatal `try/except` around `create_event` and is swallowed: hosted returns a **malformed 200** (`"extracted": 4` = dict length, `"points"` = the whole dict). Tests that type-check `body["points"]` as a list of dicts (e.g. `test_default_llm_extracts_points`) go red; others stay green. Do NOT investigate a malformed-body 200 in this window — it is the expected red for Task 3, not a regression.

### Task 2: `capture_session` fail-closed assembly (empty gate + branch-independent consume + truthful mode)

**Intent:** The SDK capture response tells the truth on BOTH branches: nothing committed on empty/blank (`turns=0`), structured errors + `mode="error"` on extraction failure, `ok`/`errors`/`warnings` on every response — and the v2 commit path's fail-closed behavior is verified as already consistent.
**Acceptance:** `capture_session([])` and all-blank conversations return `ok=False`, `mode="empty"`, `turns=0`, errors set, and commit **nothing** (no Session node); a failing v2 run (via `extractor_v2` seam) or M2 run (via duck-type + `TORTOISE_SESSION_EXTRACTOR=m2`) returns `ok=False`, `mode="error"`, errors surfaced, turn points + Event + Source still landed; success responses carry `ok=True`, `errors==[]`, `warnings==[]`, exactly one `sessionCaptured` Event, every extracted point stamped with its eventId, and graph turn count == response `turns`; a `create_event` failure (or no-id return) and a stamping-query failure each yield additive warnings with correct graph state; a `_materialize_session_source` failure yields an additive warning; the existing v2-default tests (`test_capture_session_v2_default_routes_and_writes`, `test_capture_session_v2_mock_seam_satisfies_provider_gate`, adapter tests) stay green; the no-key `ValueError` and turn-cap `ValueError` are unchanged.

**Files:**
- Modify: `tortoise/sdk.py` (`capture_session`, ~L1730–1892; wrap `_materialize_session_source` and the Event-write warning append)
- Test: `tests/test_capture_session.py`

**Step 1: Write the failing tests** (same new section):

```python
def test_capture_session_empty_conversation_fails_closed(sdk):
    """P1: empty conversation never ok=True — nothing committed, no
    Session stub, extraction_mode 'empty', turns=0 (E2E-8 owned negative)."""
    res = sdk.capture_session([])
    assert res["ok"] is False
    assert res["extraction_mode"] == "empty"
    assert res["turns"] == 0
    assert res["extracted"] == 0
    assert res["points"] == []
    assert any("empty" in e.lower() for e in res["errors"])
    assert res["warnings"] == []
    sessions = sdk._get_proj().g.query(
        "MATCH (s:Session) RETURN count(s)").result_set
    assert sessions[0][0] == 0, "nothing may be committed for an empty capture"


def test_capture_session_blank_conversation_fails_closed(sdk):
    """P1: whole-conversation blank (below-floor / whitespace / None /
    missing-key / 5000-char whitespace / falsy-0) → ok=False, mode='empty',
    turns=0. Floor boundary: exactly-3-char 'abc' is NON-blank; 2-char 'ab'
    and multi-sub-floor 'ab cd ef' are blank."""
    blank_convos = (
        [{"role": "user", "content": "ok"}],
        [{"role": "user", "content": " "}],
        [{"role": None, "content": None}],
        [{"role": "user"}],                      # missing content key
        [{"role": "user", "content": " " * 5000}],  # validator's upper bound, whitespace
        [{"role": "user", "content": 0}],         # str() = "0", below floor
        [{"role": "user", "content": "ab"}],      # 2 chars < floor
        [{"role": "user", "content": "ab cd ef"}],  # segments to <3-char sentences
    )
    for conv in blank_convos:
        res = sdk.capture_session(conv)
        assert res["ok"] is False, conv
        assert res["extraction_mode"] == "empty", conv
        assert res["turns"] == 0, conv
        assert any("empty" in e.lower() for e in res["errors"]), conv
    # floor boundary, non-blank side
    res = sdk.capture_session([{"role": "user", "content": "abc"}])
    assert res["ok"] is True and res["extraction_mode"] in ("v2", "llm"), res


def test_capture_session_v2_failure_surfaces_errors(sdk, monkeypatch):
    """P1 (E2E-8 dead-key, DEFAULT v2 branch): turn points still land,
    errors surface, mode 'error' — never a silent extracted:0."""
    import tortoise.extractor_v2 as ev2
    monkeypatch.setattr(ev2, "extract_session_v2",
                        lambda *a, **kw: _v2_out(errors=["RuntimeError: provider returned 500"]))
    res = sdk.capture_session(CONV)
    assert res["ok"] is False
    assert res["extraction_mode"] == "error"
    assert res["extracted"] == 0
    assert res["points"] == []
    assert any("provider returned 500" in e for e in res["errors"])
    assert res["warnings"] == [], "failure carries errors, never warnings"
    proj = sdk._get_proj()
    turns = proj.g.query(
        "MATCH (t:Point {pointKind:'event'}) RETURN count(t)").result_set
    assert turns[0][0] == 3, "turn points must still land (documented partial)"
    events = proj.g.query(
        "MATCH (e:Event {eventKind:'sessionCaptured'}) RETURN count(e)"
    ).result_set
    assert events[0][0] == 1, "the capture attempt is recorded"


def test_capture_session_m2_failure_surfaces_errors(sdk, monkeypatch):
    """P1 (E2E-8 dead-key, M2 branch): same contract under
    TORTOISE_SESSION_EXTRACTOR=m2 with the duck-typed failing extractor."""
    monkeypatch.setenv("TORTOISE_SESSION_EXTRACTOR", "m2")
    monkeypatch.setattr("tortoise.sdk._build_session_llm_extractor",
                        lambda: _FailingSessionExtractor())
    res = sdk.capture_session(CONV)
    assert res["ok"] is False
    assert res["extraction_mode"] == "error"
    assert any("RuntimeError" in e and "500" in e for e in res["errors"])
    proj = sdk._get_proj()
    turns = proj.g.query(
        "MATCH (t:Point {pointKind:'event'}) RETURN count(t)").result_set
    assert turns[0][0] == 3, "turn points must still land"
    events = proj.g.query(
        "MATCH (e:Event {eventKind:'sessionCaptured'}) RETURN count(e)"
    ).result_set
    assert events[0][0] == 1, "the capture attempt is recorded"


def test_capture_session_partial_emission_ok_false_points_land(sdk, monkeypatch):
    """P1 (D2 contract note, M2 branch): extracted > 0 alongside ok=False —
    partial points ARE wired + eventId-stamped; extracted is never success."""
    monkeypatch.setenv("TORTOISE_SESSION_EXTRACTOR", "m2")
    monkeypatch.setattr("tortoise.sdk._build_session_llm_extractor",
                        lambda: _PartialFailingSessionExtractor())
    res = sdk.capture_session(CONV)
    assert res["ok"] is False
    assert res["extraction_mode"] == "error"
    assert res["extracted"] == len(res["points"]) >= 1
    assert any("RuntimeError" in e for e in res["errors"])
    assert res["warnings"] == [], "failure carries errors, never warnings"
    proj = sdk._get_proj()
    eid = proj.g.query(
        "MATCH (e:Event {eventKind:'sessionCaptured'}) RETURN e.eventId"
    ).result_set
    assert len(eid) == 1
    wired = proj.g.query(
        "MATCH (s:Session {id:$sid})-[:CONTAINS]->(p:Point) WHERE p.pointKind IS NULL "
        "RETURN count(p)", params={"sid": res["session_id"]}).result_set
    assert wired[0][0] == res["extracted"], "partial points must be CONTAINS-wired"
    unstamped = proj.g.query(
        "MATCH (n:Point) WHERE n.id IN $ids AND n.eventId <> $eid RETURN count(n)",
        params={"ids": [p["id"] for p in res["points"]], "eid": eid[0][0]}
    ).result_set
    assert unstamped[0][0] == 0, "partial points must carry the eventId"


def test_capture_session_zero_extraction_is_warning_not_failure(sdk, monkeypatch):
    """P1 (D6, M2 branch): completed run with no points → ok=True, mode
    'llm', additive warning — nothing extractable is not a failure."""
    monkeypatch.setenv("TORTOISE_SESSION_EXTRACTOR", "m2")
    monkeypatch.setattr("tortoise.sdk._build_session_llm_extractor",
                        lambda: _EmptyOutputExtractor())
    res = sdk.capture_session(
        [{"role": "user", "content": "the weather today is fine"}])
    assert res["ok"] is True
    assert res["extraction_mode"] == "llm"
    assert res["extracted"] == 0
    assert any("no points" in w for w in res["warnings"])
    assert res["errors"] == []


def test_capture_session_success_shape_consistent_with_graph(sdk):
    """P1: on ok=True the graph actually has the Event, every extracted
    point carries its eventId, and the turn stream matches the response."""
    res = sdk.capture_session(CONV)
    assert res["ok"] is True and res["errors"] == []
    proj = sdk._get_proj()
    eid = proj.g.query(
        "MATCH (e:Event {eventKind:'sessionCaptured'}) RETURN e.eventId"
    ).result_set
    assert len(eid) == 1, "exactly one sessionCaptured Event on success"
    unstamped = proj.g.query(
        "MATCH (n:Point) WHERE n.id IN $ids AND n.eventId <> $eid RETURN count(n)",
        params={"ids": [p["id"] for p in res["points"]], "eid": eid[0][0]}
    ).result_set
    assert unstamped[0][0] == 0, "every extracted point carries the eventId"
    turns = proj.g.query(
        "MATCH (s:Session {id:$sid})-[:CONTAINS]->(t:Point {pointKind:'event'}) "
        "RETURN count(t)", params={"sid": res["session_id"]}).result_set
    assert turns[0][0] == res["turns"], "graph turn count must match the response"


def test_capture_session_event_write_failure_keeps_structured_success(sdk, monkeypatch):
    """P1: create_event failure is non-fatal (#721) — structured success
    shape + additive warning + correct graph state (points present, no
    dangling eventId, Source eventId null, no references edge)."""
    def boom(*args, **kwargs):
        raise RuntimeError("falkordb down")
    monkeypatch.setattr(sdk, "create_event", boom)
    res = sdk.capture_session(CONV)
    assert res["ok"] is True, res
    assert res["extraction_mode"] in ("v2", "llm")
    assert any("Event" in w or "event" in w.lower() for w in res["warnings"]), res
    proj = sdk._get_proj()
    dangling = proj.g.query(
        "MATCH (n:Point) WHERE n.id IN $ids AND n.eventId IS NOT NULL RETURN count(n)",
        params={"ids": [p["id"] for p in res["points"]]}).result_set
    assert dangling[0][0] == 0, "no point may reference the failed Event"
    src = proj.g.query(
        "MATCH (s:Source {sourceKind:'agentSession'}) RETURN s.eventId").result_set
    assert src and src[0][0] is None, "Source must have no eventId when no Event landed"
    refs = proj.g.query(
        "MATCH (:Source)-[:references]->(:Event) RETURN count(*)").result_set
    assert refs[0][0] == 0, "no references edge when no Event landed"


def test_capture_session_event_write_no_id_warns(sdk, monkeypatch):
    """P1 (D4): create_event succeeding but returning a dict WITHOUT
    id/eventId silently skips stamping — must surface as an additive warning."""
    monkeypatch.setattr(sdk, "create_event",
                        lambda *a, **kw: {"name": "no-id-event"})
    res = sdk.capture_session(CONV)
    assert res["ok"] is True, res
    assert any("Event" in w or "event" in w.lower() for w in res["warnings"]), res
    unstamped = sdk._get_proj().g.query(
        "MATCH (n:Point) WHERE n.id IN $ids AND n.eventId IS NOT NULL RETURN count(n)",
        params={"ids": [p["id"] for p in res["points"]]}).result_set
    assert unstamped[0][0] == 0, "no point may carry a dangling eventId"


def test_capture_session_stamping_failure_warns_and_leaves_points(sdk, monkeypatch):
    """P1 (D4): the eventId-stamping query failing (Event created, points
    unstamped) surfaces an additive warning under ok=True; the degraded graph
    state (Event present, points present, no dangling id) is asserted."""
    proj = sdk._get_proj()
    _real_query = proj.g.query

    def _boom_stamp(query, **params):
        if "SET n.eventId=" in query:
            raise RuntimeError("stamping query failed")
        return _real_query(query, **params)

    monkeypatch.setattr(proj.g, "query", _boom_stamp)
    res = sdk.capture_session(CONV)
    assert res["ok"] is True, res
    assert any("Event" in w or "event" in w.lower() for w in res["warnings"]), res
    unstamped = proj.g.query(
        "MATCH (n:Point) WHERE n.id IN $ids AND n.eventId IS NULL RETURN count(n)",
        params={"ids": [p["id"] for p in res["points"]]}).result_set
    assert unstamped[0][0] == res["extracted"], \
        "stamping failure leaves points present and unstamped (no dangling id)"


def test_capture_session_source_materialization_failure_warns(sdk, monkeypatch):
    """P1: _materialize_session_source failure is additive-warning, never a
    raw exception after partial writes (D4)."""
    def boom(*args, **kwargs):
        raise RuntimeError("source write failed")
    monkeypatch.setattr(sdk, "_materialize_session_source", boom)
    res = sdk.capture_session(CONV)
    assert res["ok"] is True, res
    assert any("Source" in w or "source" in w for w in res["warnings"]), res


def test_capture_session_two_warnings_sources_no_clobber(sdk, monkeypatch):
    """P1 (D7): two simultaneous degradations must BOTH surface — a clobbering
    `warnings = [...]` reassignment drops one and fails this test."""
    monkeypatch.setenv("TORTOISE_SESSION_EXTRACTOR", "m2")
    monkeypatch.setattr("tortoise.sdk._build_session_llm_extractor",
                        lambda: _EmptyOutputExtractor())
    monkeypatch.setattr(sdk, "create_event",
                        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("event down")))
    res = sdk.capture_session(
        [{"role": "user", "content": "the weather today is fine"}])
    joined = " | ".join(res["warnings"])
    assert "no points" in joined, res["warnings"]
    assert "Event" in joined or "event" in joined.lower(), res["warnings"]


def test_capture_session_recapture_never_clobbers_source_turn_id(sdk):
    """E3 (#1529 note): (a) turn-point source_turn_id survives re-capture
    (MERGE SET list excludes it — turn-stream idempotency only; extraction
    points/Event fresh per capture BY DESIGN); (b) eventId stamping touches
    only extracted points; (c) per-capture provenance: each capture's points
    carry that capture's fresh Event eventId (set-identity — no ORDER BY,
    Event nodes carry no guaranteed createdAt)."""
    res1 = sdk.capture_session(CONV)
    sid = res1["session_id"]
    proj = sdk._get_proj()
    turn_id = f"{sid}_t0"
    proj.g.query(
        "MATCH (p:Point {id:$id}) SET p.source_turn_id='turn-42'",
        params={"id": turn_id})
    res2 = sdk.capture_session(CONV, session_id=sid)  # re-capture same session
    rows = proj.g.query(
        "MATCH (p:Point {id:$id}) RETURN p.source_turn_id, p.eventId",
        params={"id": turn_id}).result_set
    assert rows and rows[0][0] == "turn-42", "re-capture must not clobber source_turn_id"
    assert rows[0][1] is None, "turn points carry no eventId (extracted only)"
    evs = proj.g.query(
        "MATCH (e:Event {eventKind:'sessionCaptured'}) RETURN e.eventId"
    ).result_set
    assert len(evs) == 2, "one fresh Event per capture (intended)"
    eids = {ev[0] for ev in evs}
    stamps = set()
    for res in (res1, res2):
        stamped = proj.g.query(
            "MATCH (n:Point) WHERE n.id IN $ids RETURN collect(DISTINCT n.eventId)",
            params={"ids": [p["id"] for p in res["points"]]}).result_set[0][0]
        assert len(stamped) == 1, f"points of one capture share one eventId: {stamped}"
        stamps.add(stamped[0])
    assert stamps == eids, f"per-capture provenance broken: {stamps} vs {eids}"


def test_capture_session_recapture_shorter_conversation_pins_state(sdk):
    """P1 (D3): re-capturing the same session_id with a SHORTER different
    conversation — turn-stream MERGE is keyed {sid}_t{i}, so higher-index
    turns from the prior capture stay CONTAINS-wired (stale residue) while
    response turns report the new length. PIN the accepted state."""
    res = sdk.capture_session([{"role": "user", "content": "first capture with five turns"},
                               {"role": "assistant", "content": "second"},
                               {"role": "user", "content": "third"}])
    sid = res["session_id"]
    sdk.capture_session([{"role": "user", "content": "shorter re-capture"}],
                        session_id=sid)
    wired = sdk._get_proj().g.query(
        "MATCH (s:Session {id:$sid})-[:CONTAINS]->(t:Point {pointKind:'event'}) "
        "RETURN collect(t.id)", params={"sid": sid}).result_set[0][0]
    assert set(wired) == {f"{sid}_t{i}" for i in range(3)}, wired
```

**Step 2: Run to verify red**

Run: `uv run pytest tests/test_capture_session.py -k "fails_closed or blank_conversation or v2_failure or m2_failure or partial_emission or zero_extraction or consistent or structured_success or no_id or stamping or materialization or two_warnings or recapture" -v`
Expected: FAIL — `capture_session` still returns the old shape, commits a Session stub for empty, raises/swallows on extractor failures, and lets `_materialize_session_source` failures escape.

> **Checklist note (no new test):** the issue's "`_extract_session_v2` consults `out[errors]`" item — for the v2 COMMIT path — is ALREADY locked by the existing tests (`test_v2_error_path_reports_errors` — raising model → `ok=False` + errors; `test_v2_empty_conversation_not_ok` — empty → `ok=False` + "no payload"; `test_v2_layer1_rejected_payload_not_posted` — Layer-1 gate). The CAPTURE-path consultation is the new Task 1/2 coverage. A redundant parity test was proposed and removed (it duplicated the existing tests and never exercised the capture surface).

**Step 3: Update the pre-existing SDK tests that the empty gate changes** (verified breakers — do not skip):

| Existing test | Why it breaks | Adaptation |
|---|---|---|
| `test_capture_session_empty_conversation` (worktree L~321) | expects Session recorded + `extracted==0` | Replace with `test_capture_session_empty_conversation_fails_closed` (no Session node, `turns=0`, mode "empty") |
| `test_capture_session_zero_extraction` (L~223) | **Verified: does NOT break** — the empty half gets the new empty dict (`extracted==0` holds), the COUNT query over the empty match returns a 0 row (no IndexError), nothing is committed; the plain half is non-blank | Optional hardening (not required): assert `ok is False`, `mode == "empty"` in the empty half |
| `test_capture_session_shape` (L~17) | old shape has no `ok`/`errors`/`warnings` (runs the v2 default branch) | Add `assert res["ok"] is True`, `res["errors"] == []`, `res["warnings"] == []` |
| `test_capture_session_contains_edges_when_speaker_repeats` (L~253) | turns are `[{"content": "x"}] * 3` — single `"x"` per turn is BELOW the 3-char floor (`len >= 3` inclusive) → whole-conversation transcript empty → blank gate fires pre-mutation → IndexError. (Note: `"x" * 3` would be `"xxx"` — non-blank — so the fix must change the CONTENT, not multiply it) | Use a non-blank repeated utterance, e.g. `[{"content": "okay we proceed"}] * 3` |
| `test_capture_session_none_role_content` (L~333) | `[{"role": None, "content": None}]` is blank → early return → graph query IndexError | Rewrite as the None edge in `test_capture_session_blank_conversation_fails_closed` |
| `test_capture_session_exactly_at_cap` (L~314) | `[{"content": "x"}] * 3` is blank → exercises the empty gate, not the cap boundary (vacuous) | Give it non-blank content, e.g. `"okay"` |
| `test_capture_session_falsy_non_string_content_not_swallowed` (L~385) | **Verified: does NOT break** — the 5-turn mix's transcript is non-blank (`"False"` = 5 chars) so the whole-conversation gate never fires; all turns store as today | Leave unchanged (it is the #721 coerced-store regression lock) |

Also lock the **no-key × empty combination** (contract symmetry with hosted's `test_no_provider_503`): extend `test_capture_session_no_provider_fails_closed` — assert `capture_session([])` raises the same `ValueError` (the no-extractor check precedes the empty gate; an exception is the fail-closed signal on this path, consistent with hosted 503-first). If a reviewer prefers the structured empty response here, that is a one-line reorder — flagged in Open Questions.

**Step 4: Implement `capture_session`** (sdk.py, ~L1730): after the turn-cap check, add the transcript gate; replace the dispatch consume-site with the structured-result assembly; wrap `_materialize_session_source` and the Event-write warning append:

```python
        # P1 #1529: empty/blank conversation fails closed BEFORE any write —
        # whole-conversation transcript emptiness, the SAME signal the
        # extractors use, so the gate and the extractors cannot disagree.
        # turns reports the COMMITTED state (0) — nothing lands.
        transcript, _est = _session_llm_transcript(conversation)
        if not transcript.strip():
            return {
                "session_id": session_id,
                "turns": 0,
                "extracted": 0,
                "points": [],
                "extraction_mode": "empty",
                "ok": False,
                "errors": ["no extractable content — empty or blank conversation"],
                "warnings": [],
            }
        ...  # Session MERGE + turn loop unchanged ...

        # P1 #1529: branch-independent structured result (v2 default / M2
        # behind TORTOISE_SESSION_EXTRACTOR=m2). Never re-raised; turn
        # points have already landed.
        if os.environ.get("TORTOISE_SESSION_EXTRACTOR") == "m2":
            extracted_res = self._extract_session_llm(conversation, session_id, now)
        else:
            extracted_res = self._extract_session_v2(conversation, session_id, now)
        extracted = extracted_res["points"]
        extraction_errors = extracted_res["errors"]
        extraction_warnings = extracted_res["warnings"]
        mode = extracted_res["mode"]
        ...  # Event create + eventId stamping — non-fatal (#721); on failure
        #      (raise OR no-id return) ALSO append an additive warnings entry
        #      (D4), so a missing Event is visible, never silent ...
        try:
            self._materialize_session_source(session_id, event_id, now, conversation)
        except Exception as e:
            _logger.warning(
                "capture_session: session Source materialization failed "
                "(non-fatal) for session %s: %s", session_id, e, exc_info=True)
            extraction_warnings = extraction_warnings + [
                f"session Source materialization failed: {type(e).__name__}: {e}"]

        return {
            "session_id": session_id,
            "turns": len(conversation),
            "extracted": len(extracted),
            "points": extracted,
            "extraction_mode": mode,
            "ok": not extraction_errors,
            "errors": extraction_errors,
            "warnings": extraction_warnings,
        }
```

Note: `ok` is `not extraction_errors` — the internal `mode="empty"` guard's error entry (D2) flows through correctly. The Event-write `except` gains `extraction_warnings.append(f"sessionCaptured Event write failed: {type(e).__name__}: {e}")` (in addition to the existing log), and the no-id return path appends a similar warning.

**Step 5: Run the full capture suite**

Run: `uv run pytest tests/test_capture_session.py -v`
Expected: all PASS (new + updated + pre-existing, including the v2-default lock tests `test_capture_session_v2_default_routes_and_writes` etc.).

### Task 3: Hosted POST /v1/sessions fail-closed (422 empty gate + additive errors/warnings + input coercion + CLI consumer)

**Intent:** The hosted surface fails closed with the same contract as the SDK: empty/blank conversation → 422 before any write; extraction failure → 200 with additive `errors`/`warnings` and `mode="error"` (turns landed); non-string turn content coerced (no raw 500 / partial write); the CLI consumer stops reporting success on `mode="error"`.
**Acceptance:** `POST /v1/sessions` with `[]` or all-blank conversations → 422 with no Session node written; failing extraction (v2 seam or M2 duck-type) → 200 body with `errors`, `mode="error"`, `warnings == []`, turn points + Event + agentSession Source in the graph (asserted via `TEST_TEAM_ID` namespace); completed-but-empty → 200 + additive warning + truthful mode; dict/int/bool content → coerced (never 500); `create_event` failure → 200 + additive warning; stamping-query failure → 200 + warning (hosted's duplicated Event block); `_materialize_session_source` failure → 200 + additive warning; `_async_audit` failure → 200 (log-only wrap); a blank conversation on an over-quota team → 422 (not 402); `_cmd_session_capture` returns exit 1 on `mode="error"`; the 503 no-key gate and 400 turn-cap gate are unchanged and still ordered first.

**Files:**
- Modify: `tortoise/hosted_api.py` (`capture_session` handler, ~L3369+; `SessionRequest.valid_conversation`, ~L3315; import line 41)
- Modify: `tortoise/__main__.py` (`_cmd_session_capture`, ~L1394)
- Test: `tests/test_hosted_api.py` (TestSessionCapture) + `tests/test_session_extraction_modes.py`

**Step 1: Write the failing tests** (all graph assertions use `namespace=TEST_TEAM_ID` — the fixture's team, matching the handler's `_make_sdk(namespace=team["team_id"])`):

```python
# tests/test_hosted_api.py — TestSessionCapture

def test_capture_session_empty_conversation_rejected(self, client):
    """P1: empty conversation never ok=True — 422 before any write
    (E2E-8 owned negative)."""
    r = client.post("/v1/sessions", json={"conversation": []})
    assert r.status_code == 422, r.text
    assert "extractable content" in r.json()["detail"]
    import tortoise.hosted_api as ha_mod
    sdk = ha_mod._make_sdk(namespace=TEST_TEAM_ID)
    sessions = sdk._get_proj().g.query("MATCH (s:Session) RETURN count(s)").result_set
    assert sessions[0][0] == 0, "no Session node may be written for an empty capture"

def test_capture_session_blank_conversation_rejected(self, client):
    """P1: whole-conversation blank → 422. Requires the D10 validator guard
    (None content would otherwise 500 in Pydantic before the handler)."""
    for conv in ([{"role": "user", "content": "ok"}],
                 [{"role": None, "content": None}],
                 [{"role": "user"}],
                 [{"role": "user", "content": " " * 5000}],
                 [{"role": "user", "content": 0}],
                 [{"role": "user", "content": "ab"}]):
        r = client.post("/v1/sessions", json={"conversation": conv})
        assert r.status_code == 422, (conv, r.text)

def test_capture_session_llm_failure_surfaces_errors(self, client, monkeypatch):
    """P1: extraction failure (DEFAULT v2 branch) → 200 + additive errors,
    warnings == [], mode 'error', turn points + Event + agentSession Source
    still land (documented partial, never a silent extracted:0)."""
    import tortoise.extractor_v2 as ev2
    monkeypatch.setattr(ev2, "extract_session_v2",
                        lambda *a, **kw: _v2_out(errors=["RuntimeError: provider returned 500"]))
    r = client.post("/v1/sessions", json={
        "conversation": [{"role": "user", "content": "I think auth is the top issue."}]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["extraction_mode"] == "error"
    assert body["extracted"] == 0
    assert any("provider returned 500" in e for e in body["errors"])
    assert body["warnings"] == [], "failure carries errors, never warnings"
    import tortoise.hosted_api as ha_mod
    sdk = ha_mod._make_sdk(namespace=TEST_TEAM_ID)
    proj = sdk._get_proj()
    turns = proj.g.query(
        "MATCH (t:Point {pointKind:'event'}) RETURN count(t)").result_set
    assert turns[0][0] == 1, "turn points must still land"
    events = proj.g.query(
        "MATCH (e:Event {eventKind:'sessionCaptured'}) RETURN count(e)").result_set
    assert events[0][0] == 1, "the capture attempt is recorded (hosted Event block)"
    sources = proj.g.query(
        "MATCH (s:Source) WHERE s.sourceKind='agentSession' RETURN count(s)").result_set
    assert sources[0][0] == 1, "the agentSession Source is materialized on failure too"

def test_capture_session_partial_emission_surfaces_points(self, client, monkeypatch):
    """P1 (D2 at the HTTP layer, M2 branch): partial emission → 200 + mode
    'error' + warnings == [] + extracted > 0; partial points wired; Event
    recorded."""
    class _Partial:
        version = "partial@0"
        def run(self, transcript, source_id, api):
            api.add_point("decision: ship serve first", {"source": source_id})
            raise RuntimeError("provider rate limited mid-run")
    monkeypatch.setenv("TORTOISE_SESSION_EXTRACTOR", "m2")
    monkeypatch.setattr("tortoise.sdk._build_session_llm_extractor",
                        lambda: _Partial())
    r = client.post("/v1/sessions", json={
        "conversation": [{"role": "user", "content": "I think auth is the top issue."}]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["extraction_mode"] == "error"
    assert body["extracted"] == len(body["points"]) >= 1
    assert any("RuntimeError" in e for e in body["errors"])
    assert body["warnings"] == [], "failure carries errors, never warnings"
    import tortoise.hosted_api as ha_mod
    sdk = ha_mod._make_sdk(namespace=TEST_TEAM_ID)
    proj = sdk._get_proj()
    wired = proj.g.query(
        "MATCH (s:Session {id:$sid})-[:CONTAINS]->(p:Point) "
        "WHERE p.pointKind IS NULL RETURN count(p)",
        params={"sid": body["session_id"]}).result_set
    assert wired[0][0] == body["extracted"], "partial points must be wired"
    events = proj.g.query(
        "MATCH (e:Event {eventKind:'sessionCaptured'}) RETURN count(e)").result_set
    assert events[0][0] == 1, "the capture attempt is recorded on partial failure"

def test_capture_session_zero_extraction_warns(self, client, monkeypatch):
    """P1 (D6 at the HTTP layer): completed-but-empty extraction → 200 +
    additive warning, truthful mode (surface 26 on both surfaces)."""
    class _EmptyOut:
        version = "empty-out@0"
        def run(self, transcript, source_id, api):
            pass
    monkeypatch.setenv("TORTOISE_SESSION_EXTRACTOR", "m2")
    monkeypatch.setattr("tortoise.sdk._build_session_llm_extractor",
                        lambda: _EmptyOut())
    r = client.post("/v1/sessions", json={
        "conversation": [{"role": "user", "content": "the weather today is fine"}]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["extraction_mode"] == "llm"
    assert body["extracted"] == 0
    assert any("no points" in w for w in body["warnings"])
    assert body["errors"] == []

def test_capture_session_non_string_content_coerced(self, client):
    """P1 (#721 parity): non-string turn content must NOT crash — neither in
    the Pydantic validator (D10 guard) nor in the turn loop. Split by the
    whole-conversation blank gate: single-turn 0 is BLANK → 422; non-blank
    coerced forms (12345, False, dict-with-words) → 200 + stored. Explicit
    session ids make graph assertions deterministic."""
    r = client.post("/v1/sessions", json={
        "conversation": [{"role": "user", "content": 0}], "session_id": "coerce-s0"})
    assert r.status_code == 422, r.text
    assert "extractable content" in r.json()["detail"]
    cases = [("coerce-s1", {"text": "we decided to ship v2"}),
             ("coerce-s2", 12345),
             ("coerce-s4", False),
             ("coerce-s5", {"text": None}),
             ("coerce-s6", [1, 2, 3])]
    for sid, content in cases:
        r = client.post("/v1/sessions", json={
            "conversation": [{"role": "user", "content": content}], "session_id": sid})
        assert r.status_code == 200, (content, r.text)
        assert r.json()["turns"] == 1
    import tortoise.hosted_api as ha_mod
    sdk = ha_mod._make_sdk(namespace=TEST_TEAM_ID)
    expected = {"coerce-s1": "[user] {'text': 'we decided to ship v2'}",
                "coerce-s2": "[user] 12345",
                "coerce-s4": "[user] False",
                "coerce-s5": "[user] {'text': None}",
                "coerce-s6": "[user] [1, 2, 3]"}
    for sid, want in expected.items():
        rows = sdk._get_proj().g.query(
            "MATCH (t:Point {id:$id}) RETURN t.content",
            params={"id": f"{sid}_t0"}).result_set
        assert rows and rows[0][0] == want, (sid, rows)

def test_capture_session_event_write_failure_warns(self, client, monkeypatch):
    """P1 (D4 at the HTTP layer): create_event failure is non-fatal (#721)
    AND surfaces an additive warning — a missing Event is visible, never
    indistinguishable from a clean capture."""
    def boom(*args, **kwargs):
        raise RuntimeError("falkordb down")
    monkeypatch.setattr("tortoise.sdk.TortoiseSDK.create_event", boom)
    r = client.post("/v1/sessions", json={
        "conversation": [{"role": "user", "content": "I think auth is the top issue."}]})
    assert r.status_code == 200, r.text
    assert any("Event" in w or "event" in w.lower() for w in r.json()["warnings"])

def test_capture_session_stamping_failure_warns(self, client, monkeypatch):
    """P1 (D4): the HOSTED duplicated stamping block failing (Event created,
    points unstamped) surfaces an additive warning under 200."""
    import tortoise.hosted_api as ha_mod
    sdk = ha_mod._make_sdk(namespace=TEST_TEAM_ID)
    proj = sdk._get_proj()
    _real_query = proj.g.query

    def _boom_stamp(query, **params):
        if "SET n.eventId=" in query:
            raise RuntimeError("stamping query failed")
        return _real_query(query, **params)

    monkeypatch.setattr(proj.g, "query", _boom_stamp)
    r = client.post("/v1/sessions", json={
        "conversation": [{"role": "user", "content": "I think auth is the top issue."}]})
    assert r.status_code == 200, r.text
    assert any("Event" in w or "event" in w.lower() for w in r.json()["warnings"])

def test_capture_session_audit_failure_keeps_structured_200(self, client, monkeypatch):
    """P1 (D4): a post-commit _async_audit failure must not turn a committed
    capture into a raw 500 (wrap log-only)."""
    import tortoise.hosted_api as ha_mod
    def boom(*args, **kwargs):
        raise RuntimeError("audit sink down")
    monkeypatch.setattr(ha_mod, "_async_audit", boom)
    r = client.post("/v1/sessions", json={
        "conversation": [{"role": "user", "content": "I think auth is the top issue."}]})
    assert r.status_code == 200, r.text
    assert r.json()["extraction_mode"] in ("v2", "llm")

def test_capture_session_source_materialization_failure_warns(self, client, monkeypatch):
    """P1 (D4 at the HTTP layer): Source materialization failure is an
    additive warning, never a 500 after writes. (Hosted has no body `ok`
    field — the HTTP status is the success signal.)"""
    import tortoise.sdk as sdk_mod
    def boom(*args, **kwargs):
        raise RuntimeError("source write failed")
    monkeypatch.setattr(sdk_mod.TortoiseSDK, "_materialize_session_source", boom)
    r = client.post("/v1/sessions", json={
        "conversation": [{"role": "user", "content": "I think auth is the top issue."}]})
    assert r.status_code == 200, r.text
    assert any("Source" in w for w in r.json()["warnings"])
```

Also update `tests/test_session_extraction_modes.py::test_default_llm_with_provider_key_200` — it posts `{"conversation": []}` expecting 200; per P1 this is now 422 (rename to `test_default_llm_with_provider_key_422_on_empty`; keep an assertion that the mock-seam + non-empty conversation still yields 200 + a truthful mode).

**Step 2: Run to verify red**

Run: `uv run pytest tests/test_hosted_api.py -k "empty_conversation or blank_conversation or llm_failure or partial_emission or zero_extraction or non_string or event_write_failure or stamping or audit or materialization" tests/test_session_extraction_modes.py -v`
Expected: FAIL — empty conversation currently returns 200; extraction failure currently 200s with a hardcoded `"extraction_mode": "llm"` and no errors; None/int content currently 500s in the Pydantic validator; dict content 500s mid-loop; audit failure 500s.

**Step 3: Update the pre-existing hosted tests that the empty gate changes** (verified breakers — do not skip):

| Existing test | Why it breaks | Adaptation |
|---|---|---|
| `test_capture_session_handles_empty_conversation` (L658) | expects 200 on `[]` | Replace with `test_capture_session_empty_conversation_rejected` (422 + no Session written) |
| `test_capture_session_turn_points_are_session_scoped` (L716) | `"ok"` (2 chars) is blank → 422 | Replace content with `"okay"` (≥3 chars — non-blank); the count query targets `pointKind='event'` only, so the extra extracted point doesn't affect the assertion |
| `test_detail_no_turns_no_extracted` (L840) | empty capture now 422 → no Session created → GET 404s | **Delete** — the "graceful empty session" state is unreachable via capture by design (P1). The 404-on-missing-session behavior is ALREADY covered by `test_detail_404_nonexistent`; no replacement test needed (documented in Open Questions) |

**Step 4: Implement the handler** (`hosted_api.py`): add `_session_llm_transcript` to the sdk import (line 41); insert the gate after the turn-cap check; apply content coercion in the turn loop; adapt the dispatch consume-site; wrap `_materialize_session_source` and `_async_audit`; extend the response:

```python
    # P1 #1529: empty/blank conversation fails closed BEFORE any write —
    # whole-conversation transcript emptiness (E2E-8 negative: empty
    # conversation never ok=True / silent extracted:0).
    transcript, _est = _session_llm_transcript(body.conversation)
    if not transcript.strip():
        raise HTTPException(
            status_code=422,
            detail="conversation has no extractable content (empty or blank)",
        )
    ...  # quota estimate, team limit, Session MERGE unchanged ...

    # P1 #1529 (#721 parity): isinstance-first content coercion so non-string
    # turn content can never crash the loop into a raw 500 after the Session
    # MERGE (the D10 validator guard keeps None/int/bool OUT of this loop;
    # dict content passes the validator and is coerced here).
    for i, turn in enumerate(body.conversation):
        role = turn.get("role", "unknown")
        raw_content = turn.get("content", "")
        content = raw_content if isinstance(raw_content, str) else (
            "" if raw_content is None else str(raw_content))
        ...  # turn MERGE + CONTAINS unchanged, using `content` ...

    # P1 #1529: branch-independent structured result (same dispatch as today)
    if os.environ.get("TORTOISE_SESSION_EXTRACTOR") == "m2":
        extracted_res = sdk._extract_session_llm(body.conversation, session_id, now)
    else:
        extracted_res = sdk._extract_session_v2(body.conversation, session_id, now)
    extracted = extracted_res["points"]
    extraction_errors = extracted_res["errors"]
    extraction_warnings = extracted_res["warnings"]
    effective_mode = extracted_res["mode"]
    ...  # Event + stamping — non-fatal (#721); on failure ALSO append an
    #      additive warnings entry (D4, parity with the SDK) ...
    #      _materialize_session_source wrapped in try/except → additive
    #      warning (D4), never a 500 ...
    #      _async_audit wrapped in try/except → log-only (D4): a committed
    #      capture must never 500 over audit bookkeeping ...

    return {"session_id": session_id, "turns": len(body.conversation),
            "extracted": len(extracted), "points": extracted,
            "extraction_mode": effective_mode,
            "errors": extraction_errors, "warnings": extraction_warnings}
```

The 503 provider gate stays first (existing test `test_no_provider_503` posts an empty conversation and must still get 503, not 422). The 422 gate stays after the turn-cap 400 and BEFORE the quota estimate (locked by the over-quota ordering test below). `_abuse_record_points` (`len(conversation) + len(extracted)`) is unaffected (0 on failure → counts turns only).

**Step 4a: Harden the Pydantic validator (D10 layer 1)** — `SessionRequest.valid_conversation` (hosted_api.py ~L3315) currently runs `len(content)` with no isinstance guard; **verified live**: `{"content": None}` / `12345` / `0` / `False` raise `TypeError` (Pydantic v2 propagates non-ValueError exceptions) → raw 500 before the handler runs, and the plan's own blank test (`None` content → 422) could never reach the gate. Guard it:

```python
    @field_validator("conversation")
    @classmethod
    def valid_conversation(cls, v: list[dict]) -> list[dict]:
        for turn in v:
            content = turn.get("content", "")
            if not isinstance(content, str):
                # P1 #1529 (D10): non-str content is coerced in the handler
                # turn loop; skipping the length check here means None/int/bool
                # can never crash the validator into a raw 500. Dict content
                # (len = key count) also skips — the ≤5000 rule applies to
                # real text; the stored form is still truncated at 5000.
                continue
            if len(content) > 5000:
                raise ValueError("each conversation turn content must be ≤ 5000 characters")
        return v
```

**Step 5: Add the success-shape + ordering + CLI assertions:**

- `test_capture_session_returns_expected_shape` (L619) and `test_default_llm_extracts_points` (test_session_extraction_modes.py): add `assert body["errors"] == []` and `assert body["warnings"] == []`.
- New ordering lock with a **discriminating recipe** (`max_points: 0` is falsy — the handler resolves `team.get("max_points") or 1000`, so use `1`; with ONE pre-filled point the blank request can never 402 regardless of gate order, so TWO are required):

```python
def test_capture_session_blank_over_quota_is_422_not_402(self, client, monkeypatch):
    """P1 ordering lock: the 422 blank gate precedes the quota estimate.
    max_points=1 + TWO pre-existing non-episodic points (count=2): blank →
    est=0 → 2+0 > 1 → gate-after-quota yields 402 while gate-first yields
    422 — the assertion discriminates; non-blank → 2+est > 1 → 402 either
    order (control)."""
    from tortoise.hosted_api import app, get_current_team
    import tortoise.hosted_api as ha_mod
    app.dependency_overrides[get_current_team] = lambda: {
        **TEST_TEAM, "max_points": 1}   # TEST_TEAM is the fixture's auth dict
    sdk = ha_mod._make_sdk(namespace=TEST_TEAM_ID)
    sdk.create_point(kind="statement", content="pre-existing non-episodic point 1")
    sdk.create_point(kind="statement", content="pre-existing non-episodic point 2")
    r = client.post("/v1/sessions", json={"conversation": []})
    assert r.status_code == 422, r.text   # blank gate BEFORE quota: 422 wins
    r2 = client.post("/v1/sessions", json={
        "conversation": [{"role": "user", "content": "I think auth is the top issue."}]})
    assert r2.status_code == 402, r.text  # non-blank over quota: 402 still fires
```

- **CLI consumer fix** (`tortoise/__main__.py::_cmd_session_capture`, ~L1394): today it reads only the HTTP status — on 200 it prints "Captured session: …" and returns 0 even when the body says `mode="error"` + `errors` + `extracted: 0` (the fail-open window the issue exists to kill). Add:

```python
    ...  # after the existing response handling ...
    result = r.json()
    if result.get("mode") == "error" or result.get("errors"):
        print(f"capture failed: {result.get('errors', ['unknown extraction error'])}",
              file=sys.stderr)
        return 1
```

(Test: unit-test `_cmd_session_capture` with a mocked HTTP response body `{"mode": "error", "errors": [...]}` → exit 1 + stderr; the existing happy-path CLI test stays green.)

**Step 6: Run the hosted + modes + CLI suites**

Run: `uv run pytest tests/test_hosted_api.py tests/test_session_extraction_modes.py -v` then the CLI test (find the existing `_cmd_session_capture` tests and run that file).
Expected: all PASS (including the pre-existing 503/400/402/422 order tests, `test_no_provider_503`, and the v2-default hosted capture tests).

**Step 7: Commit** (deferred by worktree instruction — do NOT commit from `.worktrees/1509-plans`; land via the epic branch with `commit-workflow` when the P-cluster batch merges). Intended message:
`feat(capture): P1 fail-closed capture — errors surface, truthful extraction_mode, empty never ok (#1529)`

---

## Tests

| Test | Layer | Asserts |
|---|---|---|
| `test_extract_session_v2_consults_out_errors` | unit (v2 default) | `out["errors"]` → mode="error" + errors surfaced; warnings == [] |
| `test_extract_session_v2_surfaces_warnings_and_zero_points` | unit (v2) | completed-but-empty → mode="v2" + warning "no points" + errors == [] |
| `test_extract_session_v2_passthroughs_source_turn_id` | unit (v2 carrier) | payload point `source_turn_id` appears in `props` (whitelist) |
| `test_extract_session_v2_counts_point_write_skips` | unit (v2) | silent `except: pass` → counted additive warning |
| `test_extract_session_llm_failure_is_structured_not_raised` | unit (M2) | mode="error"; errors carry "RuntimeError"+"500"; no raise |
| `test_extract_session_llm_partial_emission_reports_points` | unit (M2) | partial points reported with mode="error" (extracted>0 ≠ success) |
| `test_extract_session_llm_fold_failure_is_structured` | unit (M2) | fold failure stays structured; orphan window documented |
| `test_extract_session_llm_wiring_failure_is_structured` | unit (M2) | mid-wiring failure → mode="error"; response never diverges from graph; orphan state pinned (pre-created Session) |
| `test_extract_session_llm_empty_guard_is_self_consistent` | unit (M2) | internal empty guard → mode="empty" WITH error entry |
| `test_extract_session_llm_zero_output_warns` | unit (M2) | mode="llm"; warning "no points"; errors == [] |
| `test_extract_session_llm_passthroughs_source_turn_id` | unit (M2 carrier) | `add_point(**fields)` source_turn_id in `props` |
| `test_capture_session_empty_conversation_fails_closed` | integration | ok=False; mode="empty"; turns=0; errors non-empty; **no Session node** |
| `test_capture_session_blank_conversation_fails_closed` | integration | whole-conversation blanks (incl. None, missing key, 5000-char whitespace, `0`, "ab", "ab cd ef") → mode="empty"; floor boundary "abc" → ok |
| `test_capture_session_v2_failure_surfaces_errors` | integration (E2E-8, default v2) | ok=False; mode="error"; errors surface; **turn points + Event land**; never silent extracted:0 |
| `test_capture_session_m2_failure_surfaces_errors` | integration (E2E-8, M2) | same contract under `TORTOISE_SESSION_EXTRACTOR=m2` |
| `test_capture_session_partial_emission_ok_false_points_land` | integration (M2) | extracted>0 + ok=False; partial points wired + eventId-stamped |
| `test_capture_session_zero_extraction_is_warning_not_failure` | integration (M2) | ok=True; mode="llm"; additive warning |
| `test_capture_session_success_shape_consistent_with_graph` | integration | ok=True → one Event; every point eventId-stamped; **graph turn count == response turns** |
| `test_capture_session_event_write_failure_keeps_structured_success` | integration | create_event failure → structured success + warning; no dangling eventId; Source.eventId null; no references edge |
| `test_capture_session_event_write_no_id_warns` | integration | create_event returning no id/eventId → additive warning |
| `test_capture_session_stamping_failure_warns_and_leaves_points` | integration | stamping-query failure → ok=True + warning; points unstamped |
| `test_capture_session_source_materialization_failure_warns` | integration | `_materialize_session_source` failure → additive warning |
| `test_capture_session_two_warnings_sources_no_clobber` | integration (D7) | zero-extraction + Event failure → BOTH warnings present (no clobber) |
| `test_capture_session_recapture_never_clobbers_source_turn_id` | integration guard (E3) | turn-point source_turn_id survives re-capture; **per-capture Event provenance** (set-identity) |
| `test_capture_session_recapture_shorter_conversation_pins_state` | integration guard (D3) | shorter re-capture stale-turn residue pinned |
| Hosted `test_capture_session_empty_conversation_rejected` / `blank_conversation_rejected` | integration (HTTP) | 422 + detail; no Session written (`TEST_TEAM_ID` namespace); validator-guard dependent |
| Hosted `test_capture_session_llm_failure_surfaces_errors` | integration (HTTP) | 200 + additive errors + warnings==[] + mode="error"; turn + Event + agentSession Source land |
| Hosted `test_capture_session_partial_emission_surfaces_points` | integration (HTTP) | 200 + mode="error" + extracted>0; partial points wired; Event recorded |
| Hosted `test_capture_session_zero_extraction_warns` | integration (HTTP) | 200 + warning; truthful mode |
| Hosted `test_capture_session_non_string_content_coerced` | integration (HTTP) | `0` → 422; `12345`/`False`/dict/list → 200 coerced (explicit sids, no flake) |
| Hosted `test_capture_session_event_write_failure_warns` | integration (HTTP) | create_event failure → 200 + additive warning |
| Hosted `test_capture_session_stamping_failure_warns` | integration (HTTP) | hosted duplicated stamping block failure → 200 + warning |
| Hosted `test_capture_session_audit_failure_keeps_structured_200` | integration (HTTP) | `_async_audit` failure → 200 (log-only wrap) |
| Hosted `test_capture_session_source_materialization_failure_warns` | integration (HTTP) | Source failure → 200 + additive warning |
| Hosted `test_capture_session_blank_over_quota_is_422_not_402` | integration (HTTP) | DISCRIMINATES: max_points=1 + two counted points → blank 422-if-first / 402-if-after; non-blank → 402 |
| CLI `_cmd_session_capture` mode="error" → exit 1 | unit (CLI) | status-only consumer can no longer report success on a failed capture |
| `test_session_extraction_modes.py::test_default_llm_with_provider_key_422_on_empty` | integration (HTTP) | empty → 422; non-empty + seam → 200 truthful mode |
| Updated pre-existing: SDK `speaker_repeats`/`exactly_at_cap` (non-blank content), `none_role_content` (empty-mode), hosted `turn_points_are_session_scoped` ("okay"), `detail_no_turns_no_extracted` (deleted → existing `test_detail_404_nonexistent` covers 404) | regression | stay green under the new empty gate |
| Existing v2-default tests (`test_capture_session_v2_default_routes_and_writes`, `test_capture_session_v2_mock_seam_satisfies_provider_gate`, adapter tests; hosted v2-default tests) | regression | **must stay green** — the P1 change must not disturb the default v2 capture path |
| Existing commit-path tests (test_value_extractor.py, test_extractor_v2.py) | regression | unchanged — already lock `out["errors"]` consultation, Layer-1 gate, empty-not-ok |

**Fixture policy:** existing fixtures untouched. `TORTOISE_SESSION_LLM_MOCK=1` stays the offline seam. **Failure injection per branch:** v2 (DEFAULT) → monkeypatch `tortoise.extractor_v2.extract_session_v2` with `_v2_out(...)` (deterministic, no model needed); M2 → duck-typed extractors via `monkeypatch.setattr("tortoise.sdk._build_session_llm_extractor", ...)` + `TORTOISE_SESSION_EXTRACTOR=m2` (the same seam both surfaces resolve through, so one patch covers SDK + hosted). Hosted graph assertions ALWAYS use `ha_mod._make_sdk(namespace=TEST_TEAM_ID)` — the fixture team the handler writes under (the `"test-team-722"` value is only correct in `tests/test_session_extraction_modes.py`).

## Verification Plan (test-routing)

- **Domain:** code. **Tier:** standard. **Layers:** unit (per-branch seam tests) + integration (SDK on embedded FalkorDBLite; hosted via FastAPI TestClient with `_patch_tortoise_sdk_init`).
- **UX verification:** skipped — zero UI surface (SDK dict contract + HTTP body; `ux-design-review` gate also skipped: no UI files, pure backend/API).
- **Non-code domains (content/config/research):** none touched — deferred per #6053 convention.
- **Post-deploy surface:** server API change (hosted) → the TestClient integration suite is the verification; no browser clickthrough (no UI to click). The hosted e2e suite `tests/e2e/hosted/` (`test_session_capture_e2e.py`, `RUN_HOSTED_E2E=1`, CI job `hosted-e2e`) is a real HTTP consumer — its four capture tests are unaffected (non-blank content; gates ordered before the new 422; auth-failure first), but it should be run once post-merge (documented in OQ5).
- **Full gate run:** `uv run pytest tests/test_capture_session.py tests/test_hosted_api.py tests/test_session_extraction_modes.py tests/test_value_extractor.py tests/test_extractor_v2.py -v` — must be green before `commit-workflow`.

## Journey Test Map

### Journey: J1 — Session captured → facts extracted (OP)
1. **Step:** capture a non-empty session (v2 default) → **Acceptance:** turns land, points extracted, `ok=True`, `mode="v2"`, exactly one Event, points eventId-stamped → **Test:** `test_capture_session_shape` + `test_capture_session_success_shape_consistent_with_graph` (+ hosted `TestSessionCapture`)
2. **Step:** capture an empty/blank session → **Acceptance:** SDK `ok=False`/`mode="empty"`/`turns=0`, nothing committed; hosted 422 → **Test:** empty/blank fail-closed tests (Task 2/3)
3. **Step:** capture with a dead LLM key → **Acceptance:** turn points land, errors surface, `mode="error"`, never a silent `extracted: 0` (both branches) → **Test:** `test_capture_session_v2_failure_surfaces_errors` + `test_capture_session_m2_failure_surfaces_errors` (+ hosted)

### Failure Modes
- LLM provider 500 mid-capture (v2 or M2) → **Expected:** turns land; `ok=False` + `mode="error"` + errors; no silent success → **Test:** failure tests above
- v2 `out["errors"]` non-empty (dead key) → **Expected:** surfaced (the issue's core checklist item) → **Test:** `test_extract_session_v2_consults_out_errors`
- LLM emits points then fails (partial emission) → **Expected:** partial points reported + wired + eventId-stamped, `ok=False` — `extracted > 0` is never success → **Test:** seam + SDK + hosted partial tests
- LLM returns empty on a non-empty transcript → **Expected:** additive warning, `ok=True` → **Test:** zero-extraction tests (both surfaces)
- Fold/wiring failure after run() (M2) → **Expected:** structured `mode="error"`; **orphan window (documented):** points the projection wrote during run() may be unreportable — pinned by graph assertions → **Test:** fold/wiring seam tests
- v2 point-write failures → **Expected:** counted additive warning, never invisible → **Test:** `test_extract_session_v2_counts_point_write_skips`
- Source materialization fails → **Expected:** additive warning → **Test:** materialization tests (SDK + hosted)
- Event write fails (raise or no-id) → **Expected:** non-fatal (#721) + additive warning; no dangling eventId; Source.eventId null → **Test:** event-failure tests (SDK + hosted)
- Stamping-query fails → **Expected:** ok=True + warning; points unstamped → **Test:** stamping tests (SDK + hosted duplicated block)
- Audit write fails (hosted) → **Expected:** still 200 (log-only wrap) → **Test:** audit test
- Non-string turn content (hosted) → **Expected:** None → 422 blank (validator guard); int/bool/dict/list → coerced → **Test:** coercion test + blank iterables
- Whitespace-only 5000-char turn (validator's exact upper bound) → **Expected:** blank → `mode="empty"`/422 → **Test:** blank iterables
- Falsy non-string content → **Expected (whole-conversation gate):** blanks ONLY when the entire transcript is empty (single-turn `0` → empty/422); mixed conversations store everything (#721 unchanged) → **Test:** blank iterables + unchanged falsy test
- 3-char floor boundary → **Expected:** "abc" non-blank / "ab" + "ab cd ef" blank (per-sentence `len >= 3`) → **Test:** blank iterables boundary cases
- Re-capture after E3 lands → **Expected:** turn-point `source_turn_id` intact; per-capture Event provenance → **Test:** re-capture guard
- Empty conversation on hosted with no provider key → **Expected:** 503 (provider gate first) → **Test:** `test_no_provider_503`
- Blank conversation on an over-quota team → **Expected:** 422 (not 402) — gate ordering locked → **Test:** `test_capture_session_blank_over_quota_is_422_not_402`
- **Out of scope (documented):** a mid-turn-loop graph-write failure still raises — with Session `turn_count` set to the full input length and fewer wired turns (the session-detail consumer could see phantom turns); acknowledged as the accepted consequence (no transaction). The in-scope input-edge variant of this family (non-string content crashing the loop) IS fixed via coercion. Orphan accumulation across client retries (fresh ULIDs per capture) is likewise documented as accepted.

**Tech Stack:** Python 3.12, FastAPI (hosted), Pydantic v2 (`SessionRequest`), FalkorDBLite (embedded test backend), pytest. Zero new dependencies.

---

## Cross-Lane Interfaces

| Lane | Relationship | Action |
|---|---|---|
| **P2 — provider routing** (separate P-cluster issue; E2E-8 failover variant) | P2 needs a fail-closed base contract + exception classification | P1 defines the response shape P2 extends: `extraction_mode` enum gains route values ("deepseek-direct"/"openrouter") for the failover-success path; `mode="error"` becomes the both-providers-failed state; P1 preserves `TypeName:` in error strings (v2 via `out["errors"]`, M2 via the caught exception) so P2's fatal-4xx-no-failover guard can key on it (E2E-8 negative). Do NOT reshape the enum in P2 — extend it. |
| **E3 — atomic points + `source_turn_id`** | E3 writes `source_turn_id` via the v2 payload / the M2 projection; capture must pass it through | D8 + Task 1 guards: whitelisted `props` passthrough on BOTH carriers + re-capture no-clobber. No hard dependency either direction — the guards are additive. |
| **P4 — hosted/SDK parity** | SDK writes `speaker` on turn points, hosted doesn't; quota/truncation/commit-id parity | P1 makes both surfaces share the response contract shape (errors/warnings/mode) AND aligns hosted turn-loop content coercion with the SDK's #721 pattern (D10). Remaining P4 work (hosted `speaker`, quota/truncation/commit-id) is untouched. |
| **P3 — rebase + CI drift gate** | Global first dependency of the epic; THIS issue's dependency | This plan is written against the CURRENT origin/main (worktree `.worktrees/1509-plans`, merge `21970c6c`) — the state after the #1350 v2-default capture landed and P3 rebased. The v2-default dispatch, `_V2SessionMock`, and the `TORTOISE_SESSION_EXTRACTOR=m2` seam are all current-on-main and must NOT be disturbed. |
| **M2/M3/M4 — measurement + retry** | Capture path separate from the eval harness | No interaction; pre-flight ping (M2) and retry/backoff (M3) live in the harness/provider layers, not `capture_session`. |
| **E1 — session date** | v2 commit path threads `session_date`; capture does not | No conflict — P1 does not thread dates. |
| **Hosted API consumers** | `POST /v1/sessions` stops accepting empty/blank conversations (200 "graceful" → 422); failure now returns 200 + additive errors (was 200 with lying mode) | **Breaking contract changes** — flagged in Open Questions: the "session with no turns and no extracted points" state becomes unreachable via capture; the CLI consumer is fixed in Task 3 Step 5b; session-detail clients must handle the 422 and inspect `mode`/`errors`. |

---

## ⛔ CONDITIONAL GATES

- **No ontology change.** Zero new kinds, edge types, graph properties, or expansion packs. All P1 fields (`ok`, `errors`, `warnings`, `extraction_mode` values) are **response-contract only** (SDK dict keys / HTTP body keys / `HTTPException.detail` / CLI exit codes) — never written to the graph. The epic's ontology invariant (additive properties OK, no new kinds) is respected; P1 adds no properties at all.
- **No new dependencies.** Zero third-party libs — writing-plans Perplexity gate skipped (zero-deps rule).
- **No change to the extractor dispatch.** The `TORTOISE_SESSION_EXTRACTOR` switch (v2 default / `=m2`) and the `_V2SessionMock` seam are current-on-main (`#1350`) and MUST NOT be removed or re-ordered — P1 wraps BOTH branches at the assembly level. Review must reject any change that deletes the v2 default branch or hardcodes the M2 path.
- **Conditional (likely not needed): persisting extraction status on the Session node.** E2E-8 requires response-surface truthfulness only. IF a future issue wants extraction status queryable in the graph, that is an additive Session property (`s.extraction_mode`/`s.extraction_error`) — permitted by the epic's ontology invariant — but it must be proposed as its own change; do NOT add it in this issue.
- **Conditional: P2 ordering.** If P2's provider abstraction lands BEFORE this issue, P2 must not assume the `mode="error"`/`"empty"` values exist yet; keep the enum additive. Conversely P1 must not pre-build P2's routing (YAGNI).
- **Conditional: E3 passthrough shape.** If E3's `source_turn_id` lands via a different carrier than the v2 payload point dict / M2 `add_point(**fields)`, Task 1's whitelisted `props` passthrough is the fallback-safe default; whatever the carrier, the invariant is: **capture never drops or overwrites `source_turn_id`** — review must reject any change that rebuilds a reduced point shape. Do NOT test the M2 passthrough with a synthetic `PointUpdated` event — `projection._apply_one` drops it (verified); always inject via `add_point(**fields)`.
- **HTTP status choice for empty conversation is 422** (not 400): same family as the existing Pydantic 422 (over-length content); handler-level because blankness is transcript-derived. Flagged in Open Questions.
- **Hosted turn-loop coercion is a small behavior change aligned to SDK #721 parity** — it changes what is stored for non-string content on hosted (previously a 500; now `str()` forms). It is NOT a new ontology surface (same `content` property, same string type) — but flag it in the P-cluster PR description so P4's parity review sees it.

---

## Open Questions

1. **Hosted extraction-failure status: 200 + additive errors/warnings vs non-200.** Chosen: **200 + additive** (mutation already occurred — turn points landed; E2E-8 explicitly permits "non-200 or additive warnings"; a non-200 hides the partial write). The alternative (e.g. 502 with the same body) forces client-side reconciliation for a write that DID happen. A related reviewer suggestion — adding a body-level `ok` field to the hosted 200 for SDK parity (so status-only consumers can't misread a failed-but-mutating capture) — was declined to keep the hosted body-convention (HTTP status = ok); the one status-only consumer (the CLI) is fixed explicitly (Task 3 Step 5b). Confirm at review.
2. **`extraction_mode` vocabulary.** `"v2" | "llm" | "empty" | "error"` — the `"error"` value kills the surface-26 "lying extraction_mode" bug; `"v2"` vs `"llm"` reflects which extractor actually ran (truthful per branch). Alternative: a single `"llm"` for both success branches (mode = mechanism class). Chosen the branch-truthful enum. Confirm.
3. **SDK empty-capture posture: early return (nothing committed) vs recording a Session stub.** Chosen early return with `turns=0` — the truest fail-closed (nothing written for a blank capture) and matches hosted's pre-mutation 422. This flips the old `test_capture_session_empty_conversation` behavior (Session with turn_count=0 recorded). Confirm no caller relied on the stub.
4. **422 vs 400 for empty conversation.** Chosen 422 (validation-family consistency). The handler already has 400 for the turn cap — a reviewer may prefer 400 for uniformity; trivially reversible.
5. **Hosted 200 → 422 is a BREAKING API contract change.** The "session with no turns and no extracted points" state becomes unreachable via capture; `test_detail_no_turns_no_extracted` is deleted (its 404-on-missing-session behavior is already locked by `test_detail_404_nonexistent`). Affected consumers enumerated (reviewer-verified): (a) session-detail clients that capture-then-GET a blank session; (b) `tortoise/__main__.py::_cmd_session_capture` — fixed in Task 3 Step 5b (previously: below-floor transcript → 200 → printed success; now 422 → exit 1; and dead-key 200+mode="error" → exit 1); (c) `test_hsts_on_session_capture` — posts "hi" → now 422, but asserts only the HSTS header — still passes, header-only; (d) the in-repo e2e suite `tests/e2e/hosted/` (`test_session_capture_e2e.py`, CI `hosted-e2e`, `RUN_HOSTED_E2E=1`) — its four capture tests are unaffected (non-blank content, gates ordered before the new 422, auth-first), and it should be run once post-merge (the "Full gate run" omits it because it needs a live server; document that). Confirm the break is acceptable (it IS the E2E-8 owned negative).
6. **Zero-extraction warning (D6) scope.** Is the "completed but 0 points" additive warning in scope, or noise? Recommendation: keep — it closes the last silent-`extracted: 0` window after the empty gate.
7. **`session_id` in the empty-capture response** is a generated id that was never written to the graph (the id a retry would use). Harmless, but confirm the contract reads cleanly.
8. **Re-capture idempotency scope** (reviewer-confirmed): only the turn stream is idempotent; extracted points + `sessionCaptured` Event are fresh per capture BY DESIGN. This plan's "idempotent re-capture" language is scoped accordingly (D8, Task 1). Flagging so reviewers do not re-raise it as a P1.
9. **plan-review + label flow.** This plan is Standard tier: run `plan-review` (fresh-context reviewers) before execution; on clean, apply `planned` label and select execution mode (Subagent-Driven — 3 tasks ≤ 8) per writing-plans §05. Do NOT commit from `.worktrees/1509-plans`.
10. **E3 `props` passthrough scope.** Reviewer flagged the response `props` superset as an extra beyond P1's letter (E3 fields don't exist on the v2 payload / M2 projection yet). Kept because the owner's note ("E3's source_turn_id must not be clobbered by capture") is part of this issue and the whitelisted passthrough is the forward-compatible carrier — **whitelisted** (`_CAPTURE_PASSTHROUGH_PROPS`) so no internal projection state leaks. If reviewers prefer a strictly minimal P1, the passthrough + its tests can defer to E3 with zero effect on the fail-closed contract (the no-clobber turn-point guard stays regardless).
11. **Execution mode.** Tasks 2 and 3 are independent after Task 1 (disjoint files: `sdk.py capture_session` vs `hosted_api.py` + `__main__.py`; disjoint test files) — dispatch them as parallel sub-agents after Task 1 instead of serial hops.
12. **Falsy non-string content: the blank gate is WHOLE-conversation (not per-turn).** `_session_llm_transcript` yields one transcript per conversation; a falsy value blanks a capture ONLY when the entire transcript is empty (e.g. single-turn `[{"content": 0}]` → `mode="empty"`/422 — the #721 coerced-store → blank-drop behavior change). A mixed conversation containing any non-blank coerced value (`False` → `"False"`, 5 chars) stays non-blank and stores every coerced turn exactly as #721 does — `test_capture_session_falsy_non_string_content_not_swallowed` stays green unchanged (verified by executing the transcript builder). Also: the D10 validator `continue` removes the ≤5000 length check for non-str content (dicts previously capped via `len(dict)`); the stored turn text is still truncated at 5000, so the new contract is "coerce, then store capped" — a deliberate widening, flagged for review.
13. **Concurrent same-session captures (optional hardening).** The plan locks sequential re-capture invariants (turn-point MERGE idempotency, per-capture Event provenance). A two-thread concurrent capture of the same `session_id` (client retry-after-timeout scenario) is NOT covered: both creates land fresh Events + fresh points (intended accumulation, dedup is later-pipeline E7), and the turn-point MERGE is idempotent — but a concurrent stamping interleave is unverified. Optional test (Task 2): two threads capturing the same explicit session_id → both responses structured, no exception, turn points intact, per-capture eventId invariant holds.
14. **No-key × empty conversation asymmetry (promised flag).** The no-extractor `ValueError` check precedes the empty gate, so `capture_session([])` with NO provider key raises `ValueError` (fail-closed exception), while WITH a key it returns the structured `ok=False`/`mode="empty"` response; hosted's analogue is 503 (no key) vs 422 (key). Confirmed as the chosen contract (hosted 503-first precedent) and locked by extending `test_capture_session_no_provider_fails_closed` — flag for reviewers: is an exception acceptable on this path, or should the empty gate precede the key check for a uniform structured response?

<!-- plan-review: cycles=6, status=pending-final-verification, version=2.3.0 -->
