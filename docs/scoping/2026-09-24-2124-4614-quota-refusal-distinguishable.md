---
title: "Scoping #4614 — a quota refusal must be a distinguishable state (double diamond)"
type: engineering
domain: platform
doc_status: live
subjects.team: epistemic-team
aboutSubjects: tortoise-hosted
aboutObjects: quota-refusal, capture, error-contract
created: 2026-09-24
---

# Scoping — #4614: make a quota refusal a distinguishable state

**Lane:** L5 capture-doors (#5097) · **Branch:** `fix/4614-capture-quota-402`
**Issue:** #4614 — *free-tier quota cliff silently refuses session capture (402)*

## Phase 0 — Tier

`complexity:complex` (inherited from the lane issue #5097). Lane file family:
`tortoise/hosted_api.py` · `tortoise/capture_spool.py` · `tools/`.

## Phase 1 — problem-diverge: what is actually silent?

The issue body's framing ("the failure looks silent from the client") is a
hypothesis. Four candidate framings, each checked against evidence:

| # | Framing | Evidence | Verdict |
|---|---|---|---|
| 1 | The refusal is **unbounded** (a cliff, not a bound) | True, but bounding it is a degradation-semantics change — the owner deferred the cap and the pricing | Out of scope (dispatch: "fix the silence, not the limit") |
| 2 | The client **loses the capture** on a 402 | **Fixed** on `main`, PR #4888 / `eaa2a9c52`: `capture_spool.py:366` classifies 402 `retry`, so a refused capture is spooled, not unlinked | Already landed |
| 3 | The client **surfaces nothing** | Partly false: the harness exits 1, prints the refusal, and writes a `capture-errors/<harness>.json` breadcrumb; the TS leg logs deferrals | Already landed (#4714) |
| 4 | The refusal carries **no machine-readable category** | **TRUE — this is the defect.** The 402 body is a bare prose string | **Confirmed root cause** |

### Confirmed problem definition

> A quota refusal is emitted as a bare prose string, so no caller can tell *which
> category* of 402 it received. Every would-be consumer is pushed onto
> prose-matching — which our own clients are documented as forbidden from doing
> — and the refusal therefore cannot be a distinguishable state.

### Evidence for framing 4

- **The clients say so themselves.** `capture_spool.py` (~line 320) and
  `pi-hooks/tortoise-capture.ts` (`classifyFailure`): *"Not detected by prose:
  the client ships independently of the server's wording, and a capacity/billing
  refusal is a **category, not a string**."* Because no category exists, the
  classifier falls back to **status** — every 402 is `retry` — so a transient
  quota refusal and a permanent policy 402 are indistinguishable to the client.
- **The repo already recorded the fix for exactly this.** `_one_free_org_detail`
  (#2789): *"The dashboard's three-option dialog is driven by `code` — never by
  string-matching `detail` (the pre-#2789 contract was a bare string …)."* The
  quota refusal is the surviving instance of the contract #2789 abandoned.
- **A second prose-matcher exists.** `website/apps/dashboard/src/upsellGate.js`
  documents the workaround: *"every QuotaExceededError detail is '… limit
  reached (N).' …"* and regexes it (`LIMIT_SIGNAL_RE`).
- **The pre-cap state is NOT missing.** `website/apps/dashboard/src/nodeUsage.js`
  (#4331) already derives `ok | near | at_limit` from `/v1/team`'s
  `nodes_used`/`max_nodes` at an 80% threshold and renders a nudge. A server-side
  duplicate threshold would be a second writer of one contract — **explicitly
  rejected** (duplication-risk), not deferred.

### Assumptions

| Assumption | Status |
|---|---|
| `count_org_usage(org, "points")` is the predicate the gate enforces | **[validated]** `hosted_api.py:9947`, `quota.py:604` |
| The live org is over the cap | **[validated]** live, 2026-09-24: `CORRECT 24965`, `NAIVE 22436`, `NULLS 2529` (a `NOT n.is_episodic` query undercounts by exactly the NULL-`is_episodic` Points) |
| No deployed consumer string-parses the *points* 402 detail | **[validated]** dashboard greps: capture 402 detail is read nowhere |
| The dashboard tolerates a structured `detail` | **[validated]** `main.jsx:2250-2257` — `api()` maps `detail.message` → `err.message` and `detail.code` → `err.code` (#2789); `apiErrorText` renders `detail.message` |

### Falsification check

If a caller can already distinguish a quota refusal from another 402 **without**
matching prose, this scoping is wrong. It cannot: the only 402 signal is the
status, and the only category signal is the detail string.

## Phase 1.5 — external research

**Trigger assessment:** Architecture axis = medium (a cross-repo error
contract). Library-deps = none. UX = low (no layout/flow change).

The convergent standard for machine-readable API errors is RFC 9457 Problem
Details (`application/problem+json`, a `type`/`code`). **Contradiction test
(run FIRST):** our repo carries a *recorded decision* for the error envelope —
#2789's dict `detail` carrying `code`, applied in `_one_free_org_detail`,
`_push_suspended_detail` (#308 R5) and consumed at `main.jsx:2250`. RFC 9457
replaces that envelope wholesale. **A convergent standard that contradicts a
recorded decision is not a candidate for adoption at all** — the route would be
a reopen in its own home, argued, not a quiet adoption. So: **do not adopt RFC
9457.** The finding is recorded here as the evidence a future reopen would need.

In-repo canonical pattern (the actual precedent to follow):
`_one_free_org_detail` (#2789) · `SUSPENDED` (#308 R5) · `invalid_signup_token`
(#1709) — `detail = {"code": ..., "message": ...}`.

## Phase 4/5 — solution diamond

| # | Approach | Outcome | Verdict |
|---|---|---|---|
| A | Keep prose; add an `X-Tortoise-Refusal` header carrying the category | Additive, but invents a mechanism the repo does not have for refusals, and leaves the body prose-only — a second error contract | Rejected |
| B | Change the detail to `{"code": "quota_exceeded", …, "message": <exact old prose>}` at **every** quota door | One contract, house pattern, `code` for machines and `message` for humans, dashboard already tolerant | **Chosen** |
| C | Change only the *capture* door; leave `_check_org_limit` prose | Two shapes for one contract — a reviewer must work out why one door differs | Rejected |
| D | Bound/degrade the capture instead of refusing | Changes product semantics; contradicts the owner's deferral of the cap/pricing decision | Rejected |

**Why B wins on outcome, not on ease:** the prose survives *inside* the object,
the dashboard's `api()`/`apiErrorText` already extract `.message` — so no
deployed surface regresses while every caller gains `code`. The structured
`used`/`limit`/`estimate` fields have **no dashboard consumer yet**: the
dashboard still regexes the number out of `message`
(`keyAllowance.capLimitFrom`), and `detail.limit` is emitted for the callers that
will read it (the #5208/#5051 follow-ups). Emitting the number is the point —
reading it is a separate, deliberately deferred change.

### Adversarial threat surface

**Declared:** (not adversarial) — this is not gate/enforcement code and has no
"an attacker cannot make it fail open" property. The refusal is *more* likely to
be honoured, never less; no new input is trusted. The one adjacent risk —
a caller branching on `code` to *skip* a write — is not reachable, because the
402 still means "the write was refused".

## Plan — as executed

1. `tortoise/quota.py` — `QuotaExceededError` carries structured fields
   (`resource`, `used`, `limit`, `estimate`) **and its own `code`**;
   `QUOTA_REFUSAL_CODE`; `quota_refusal_payload()`. `enforce_org_limit`
   populates the fields at both raise sites (`used` = `count - slot_credit`, the
   exact left-hand side of the comparison that produced the refusal). A bare
   `QuotaExceededError("msg")` still works.
2. `tortoise/hosted_api.py` — every **`quota_exceeded`** 402 door answers with
   `detail = quota_refusal_payload(...)`: `_check_org_limit` (called with the
   `points`, `api_keys` and `sessions` resources), the capture points-estimate
   gate, the api-keys `_KeyCapExceeded` mint/rotate race backstops, and the two
   `/v1/session/key` recovery lanes (via one `_key_limit_refusal()` builder, so
   the api_keys category has ONE shape). The prose each door emitted before
   survives byte-for-byte as `detail["message"]`.
3. `tortoise/cohort_cost.py` — `CohortCostCapExceeded` overrides the refusal
   `code` to `cohort_cost_cap`. Both are 402s, and a caller branching on `code`
   must not read a **spend** cap as a plan-limit refusal and buy a bigger plan
   that cannot lift it.
4. `tortoise/capture_spool.py` / `tortoise/pi-hooks/tortoise-capture.ts` —
   docstrings only: the quoted prose example becomes the structured shape, and
   both record WHY the classifier still keys on STATUS and does **not** narrow
   to the new `code`.
5. Tests — `tests/test_quota.py` (`TestStructuredRefusal`, the generic points
   branch, the api-keys credit path, the derived documents limit) and
   `tests/test_hosted_api.py` (the live capture 402, the updated
   `TestKeyAllowance3874`, and the dict-detail flattening in
   `_record_capture_last_error`).
6. `tortoise/quota.py` (the assembly layer) — `quota_refusal_payload` returns a
   dict subclass whose `__str__` is the human message, so a consumer that only
   has `str(detail)` (the MCP capture twin) prints the sentence, never a repr,
   WITHOUT editing a registered tool's handler (`surface-guard` reds on any
   change inside a tool function).
7. `website/apps/dashboard/src/upsellGate.js` — `shouldNudgeUpgrade` no longer
   upsells a `cohort_cost_cap` (a spend cap an upgrade cannot lift);
   `normalizeError` exposes the refusal `code`.

### Deliberately NOT in this change (recorded, not overlooked)

- **Dashboard reader code is unchanged except one deliberate branch.** The
  prose is preserved *inside* the payload, so every existing reader keeps
  working — `main.jsx`'s `api()` maps `detail.message` -> `err.message` and
  `detail.code` -> `err.code` for any object detail, `apiErrorText` renders
  `detail.message`, and `keyAllowance.capLimitFrom` regexes that message for the
  number. The exception is `website/apps/dashboard/src/upsellGate.js`:
  `shouldNudgeUpgrade` treated *any* 402 as a plan limit (its rule predates the
  category), so a `cohort_cost_cap` spend refusal would have offered an upgrade
  that cannot lift it. It now excludes the non-plan 402 code (#4614).
- **The capture clients' 402 classification is NOT narrowed.** A structured
  `code` now exists that *could* mark a quota refusal terminal, and acting on
  it would re-open the very data loss #4714 closed (a permanent classification
  made the spool unlink its only copy of the transcript). #5051 holds the
  terminal-refusal question.
- **The clients that print the refusal still print the raw error body.** The
  Python import path (`tortoise/__main__.py`) and the Pi leg
  (`tortoise/pi-hooks/tortoise-capture.ts`) print/store the HTTP body verbatim;
  that body is now the structured JSON, so the message is inside it but a reader
  sees the envelope too. It is honest, not silent — flattening those two display
  seams is part of the #5051 consolidation, not this change.
- **The MCP result carries the coarse `ERR_QUOTA` code, not the fine one.**
  `tortoise_session_capture` maps every 402 to `ERR_QUOTA`; republishing
  `detail["code"]` there would edit a registered tool's handler and red
  `surface-guard` (CONTRIBUTING: add response fields in the assembly layer, not
  inside a tool function). The finer category is REST-only until #5051.
- **The MCP capture twin stringifies the detail at the assembly layer.**
  `quota_refusal_payload` returns a dict subclass whose `__str__` is the human
  message, so the twin's `str(detail)` — unchanged — prints the sentence, not a
  repr, and `surface-guard` stays green.
- **The remaining non-`quota_exceeded` 402 doors** (`users`/member-limit, and
  the tier/entitlement gates such as "invites require the Builder tier" and
  "backups are a Builder feature") are a separate sweep, filed as **#5208**:
  they are a different category with their own code vocabulary, and folding
  them in here would have made a quota refusal and an entitlement refusal the
  SAME code — the exact conflation the code exists to prevent.

## Wiring check

| Touch point | Type | Covered by | Status |
|---|---|---|---|
| 402 body for `points`/`api_keys`/`sessions` (`/v1/sessions` capture gate, `/v1/team/keys`, `/v1/points`, `/v1/objects`, `/v1/subjects`, MCP quota tools) | API | `_check_org_limit` + `quota_refusal_payload` | ✅ |
| Documents gate (`/v1/index/docs`) | API | `enforce_org_limit` documents branch — a background-job `failed`/`quota_hit` status, NOT an HTTP 402; the structured fields ride the exception | ✅ |
| api_keys race backstops + `/v1/session/key` recovery | API | `_key_limit_refusal()` | ✅ |
| Cohort spend cap 402 | API | `CohortCostCapExceeded` code override | ✅ |
| Dashboard at-cap notice (api_keys) | UI | unchanged — `api()` + `capLimitFrom` read `detail.message` | ✅ |
| Dashboard upgrade nudge on a spend cap | UI | `upsellGate.shouldNudgeUpgrade` excludes `cohort_cost_cap` | ✅ |
| Dashboard `Last attempt — <detail>` | UI | unchanged — `_record_capture_last_error` flattens to `message` | ✅ |
| Python + Pi capture clients | client | unchanged by design — body stored verbatim; 402 stays `retry` | ✅ |
| `users`/member-limit + tier/entitlement 402 doors | API | filed as **#5208** (different category, own code vocabulary) | ⚠️ filed |
| Cap / pricing | config | **untouched** (owner deferred) | ✅ |

## Acceptance criteria

- [x] A 402 quota refusal carries `detail.code == "quota_exceeded"` with the
      `resource`, the `used` count and the `limit`.
- [x] `detail.message` is the prose the door emitted before.
- [x] The `used` figure equals what the gate compared — `count_org_usage(...)`
      with the NULL-safe predicate, never `NOT n.is_episodic`.
- [x] The dashboard's at-cap notice still names the enforced limit (unchanged
      reader, preserved `message`).
- [x] The cap and the pricing are unchanged.
- [x] A cohort **spend** cap is distinguishable from a plan-limit refusal.
- [x] The `users`/entitlement 402 doors are recorded as a filed follow-up.
