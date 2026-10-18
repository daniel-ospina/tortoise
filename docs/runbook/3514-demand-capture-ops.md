---
title: "Demand-capture ops checks — Telegram ping + monthly demand review (#3514)"
type: operations
domain: operations
doc_status: live
created: 2026-10-11
ownedBy: epistemic-team
aboutSubjects: epistemic-team
aboutObjects: tortoise, memory-capture-onboarding
---

# Demand-capture ops checks (#3514)

> Epic #1714 §5 requires that every **ops check** it names carries a checked-in
> recipe bound to a commit identity, a command string containing the named check
> id, and non-empty output matching a pinned marker. **#3515 owns the gate that
> enforces the receipt's shape**; this file is #3514's own recipe + receipt slot.
> Nothing here delivers a notification to a customer — the ping is an internal
> operator signal, and ship-time email is manual (R2).

Two named checks live here: `ops-telegram-live-send` (R3's ping, end to end) and
`ops-monthly-demand-review` (the only "loop" that remains — the demand store read
that decides whether an integration becomes a scoping issue).

## What is already proven in CI (no ops check needed)

`tests/test_data_source_capture.py` (registered under `api` + `onboarding` +
`core` in `config/ci-surfaces.yml`) proves, against the real code paths and a
stubbed transport:

- the click records `which integration, by which team, when` into the canonical
  append-only store, with a **server-derived** `org_key`;
- the ping is dispatched off the request path, exactly once per
  `(integration_key, team)` inside the pinned window, and a transport that
  **raises** cannot fail the request;
- a transport that **neither returns nor raises** does not delay the click
  request (the cycle-7 case), with `DEMAND_SEND_TIMEOUT_S` pinned;
- a failed record write **surfaces** (503, no ping, no local file on hosted) and
  the self-hosted JSONL degradation is taken only when Supabase is unconfigured;
- the debounce marker is retained **only on a 2xx send** (a failed send is
  re-attempted).

### Idempotency, and what a replay is allowed to claim

The client mints one `X-Idempotency-Key` per `(mount, tile)`, and the record row
carries the SERVER-computed hash of the claim in its
`idempotency_fingerprint` prop (`tortoise/demand.py::idempotency_fingerprint`;
registered in `_ALLOWED_ANALYTICS_PROPS`). A replayed request is answered from
**that row** — the write behind the claim either left one or did not — and never
from the claim marker alone, and never from a click count (a `go_back`-only team
holds a stored row and zero clicks, so a click-count predicate reads a real
record as "not recorded" and then duplicates it).

Four arms, and the operator reads them as follows:

| Replay state | Answer | Why |
|---|---|---|
| a row carries this claim's fingerprint | `200 replayed: true` | the write landed — whatever its `action` was |
| store unreadable | `200 replayed: true` | absence is NOT provable, so the honest answer is the replay |
| no row, claim younger than `DEMAND_INFLIGHT_CLAIM_S` | `503 recorded: false`, claim KEPT | a peer may be mid-write; releasing its reservation is what makes a retry a duplicate |
| no row, claim older than that bound | `200 replayed: false` — the retry RECOVERS and IS the first record; `503` only if a peer re-took the claim between the release and this claim, or if the recovered write itself is dropped (the write arm's own `503`) | the holder is provably gone (its write is bounded), so the recovery path re-claims and falls through to the write |

A `503` therefore never means "we wrote nothing" on its own — it means *this
request wrote nothing*. The client renders it as the failure state and offers
`Retry`, which is safe in every arm above: the key is reused, so a write that
DID land replays instead of recording twice. The dialog's copy claims only what
the client can know — *"Your request was not confirmed — Retry to send it
again."* — and never "nothing was saved", because the in-flight arm exists
precisely because a record may be landing right now.

What CI deliberately does NOT do: call the real Telegram API. A live send from CI
would page the operator on every run and could not be told from a real click.

## `ops-telegram-live-send` — the ping reaches the real operator chat

**Actor:** an operator with the deployed `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID`.
**Precondition:** the deployed API is up, and the operator is looking at the chat.
**Run:**

```bash
# The command string must contain the check id — the receipt gate keys on it.
# ops-telegram-live-send
curl -sS -X POST "$API/v1/data-sources/demand" \
  -H "Authorization: Bearer $TT_KEY" -H 'Content-Type: application/json' \
  -d '{"integration_key":"slack","action":"click","surface":"ops-check"}' \
  | tee /tmp/ops-telegram-live-send.json
```

**Expected marker:** HTTP 200 with `"ok": true` in
`/tmp/ops-telegram-live-send.json`, **and** one `[demand] …` message in the
operator chat, **and** exactly one new row for `integration_key=slack` from the
per-team read:

```bash
curl -sS "$API/v1/data-sources/demand" -H "Authorization: Bearer $TT_KEY"
```

A second POST inside `DEMAND_DEDUPE_WINDOW_S` must produce **no second message**
(the dedupe) while the record count still increases (the click is always
recorded). Both directions are the proof.

**Receipt slot:** `docs/runbook/receipts/3514-ops-telegram-live-send.txt` —
the command's exit status and stdout, the observed chat line, and the commit SHA
the run was produced at.

## `ops-monthly-demand-review` — the monthly onboarding-cohort review

**Actor:** an operator holding `FASTAPI_INTERNAL_KEY`.
**Precondition:** the demand store has at least one click.
**Run:**

```bash
# ops-monthly-demand-review
curl -sS "$API/v1/internal/data-sources/demand" \
  -H "Authorization: Bearer $FASTAPI_INTERNAL_KEY" \
  | tee /tmp/ops-monthly-demand-review.json
```

**Expected marker:** `"ok": true` plus a `demand` list ranked by
`org_count` then `click_count`, and a `distinct_orgs` figure that equals the
number of distinct teams in the store.

**What counts as demand:** rows whose `action` is `click`. A `go_back` row (a
dismissal — a value the route accepts) is stored as a funnel fact and is
EXCLUDED from both `click_count` and `org_count`; counting it would let
close-buttons advance the N=5 trigger.

**`distinct_orgs` is deliberately ACTION-AGNOSTIC** (review P3, and pinned by
`test_a_dismissal_only_team_is_store_breadth_not_demand`): it is *store breadth*
— how many teams hold a demand row at all — so a team whose ONLY row is a
dismissal IS counted there and appears in no ranked row. The N=5 trigger reads
the per-integration `org_count`, never this figure. Do not "fix" the asymmetry:
filtering it to clicks would silently redefine a number this review already
reads, and would hide the dismissal rows the funnel keeps on purpose.

**Decision rule (epic #1714 §6):** **N distinct orgs requesting an integration →
it becomes a scoping issue**, with N = 5 to be fixed at the plan gate. That is
the trigger for **#3541** (Drive / Slack / Notion), which stays open while the
threshold is unmet — the deferral instrument's scheduled evaluation reads this
same endpoint.

**Receipt slot:** `docs/runbook/receipts/3514-ops-monthly-demand-review.txt`.

## Receipt status at this commit

| Check | Status | Why |
|---|---|---|
| Automated rows (`tests/test_data_source_capture.py`) | **RUN** — 51 passed | the server half: record, ping, dedupe, caps, read half, the `go_back`-is-not-demand guard, the `distinct_orgs` breadth/demand split, and the replay rows (`a claim with no row behind it is NOT a replay` — in-flight claims are never released, aged ones are recovered; `an idempotent go_back write replays instead of duplicating`; `a raised write releases its claim so the retry records`) — plus the e2e-docstring claim guard (`_wire` must name `_hold_demand_response`, the mechanism the browser samples) |
| Client half (node + browser) | **RUN** | `demandCapture.test.js` (18, the state machine, the wire body, and the copy pins), `demandCaptureRender.test.js` (12, the dialog wiring, the focus moves, the document-level Escape, and the #8975 duplicate-key pin), and the five `#3514` rows in `tests/e2e/test_dashboard_onboarding.py` (real built bundle, real preview — including the in-flight FOCUS row, which holds the response open from the page so `document.activeElement` and Escape can be sampled during `sending`) |
| `ops-telegram-live-send` | **NOT RUN** | no operator Telegram credentials exist in the lane that produced this PR; a live send cannot be fabricated, and the receipt gate (#3515) refuses a fabricated one |
| `ops-monthly-demand-review` | **NOT RUN** | requires a DEPLOYED instance holding real demand rows. The surface now exists end to end (the grid landed in #8327, the click trigger in #3514), so this row is runnable the first time a real user clicks a tile on a deployment — no lane can produce it from a test store without fabricating the receipt |

Both un-run rows are **honest gaps, recorded rather than filled**: they are
manual operator actions with no runner (§5), and the ping's automated contract is
proven in CI by the mocked-transport rows above.
