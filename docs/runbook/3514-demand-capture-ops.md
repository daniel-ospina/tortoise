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

**Decision rule (epic #1714 §6):** **N distinct orgs requesting an integration →
it becomes a scoping issue**, with N = 5 to be fixed at the plan gate. That is
the trigger for **#3541** (Drive / Slack / Notion), which stays open while the
threshold is unmet — the deferral instrument's scheduled evaluation reads this
same endpoint.

**Receipt slot:** `docs/runbook/receipts/3514-ops-monthly-demand-review.txt`.

## Receipt status at this commit

| Check | Status | Why |
|---|---|---|
| Automated rows (`tests/test_data_source_capture.py`) | **RUN** — 40 passed | the evidence in this PR |
| `ops-telegram-live-send` | **NOT RUN** | no operator Telegram credentials exist in the lane that produced this PR; a live send cannot be fabricated, and the receipt gate (#3515) refuses a fabricated one |
| `ops-monthly-demand-review` | **NOT RUN** | requires a deployed instance holding real demand rows; nothing has clicked a tile yet (#3513's grid is not merged) |

Both un-run rows are **honest gaps, recorded rather than filled**: they are
manual operator actions with no runner (§5), and the ping's automated contract is
proven in CI by the mocked-transport rows above.
