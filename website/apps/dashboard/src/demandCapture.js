// demandCapture.js — #3514 (epic #1714 J4): the coming-soon click's CLIENT half.
//
// WHY THIS MODULE EXISTS. #3513 shipped the integrations grid and the dialog
// SHELL and deliberately left the posting primary to this issue, with the reason
// written at the call site: "the record itself is the demand endpoint's
// (`POST /v1/data-sources/demand`, #3514); until the client trigger lands,
// claiming 'we recorded it' in the UI would be a false promise." This module is
// that trigger's policy half, kept PURE (no React, no DOM, no imports) so the
// states, the body and the idempotency rule are `node --test`-unit-tested
// without a bundler or a browser (the wizardFlow.js / integrationsGrid.js
// pattern).
//
// THE SERVER CONTRACT IS THE PINNED ONE (`tortoise/demand.py`, `hosted_api.py`):
//   * `POST /v1/data-sources/demand`, body `{integration_key, action, surface}`;
//   * `org_key` is SERVER-derived from the authenticated team and is therefore
//     never sent by this client — a body field would be ignored at best and a
//     forged ranking input at worst;
//   * `action` is a closed vocabulary; the CLIENT sends only `click`. `go_back`
//     is a valid server value but this client never sends it: a dismissal is
//     not a demand click, and the read half now counts clicks, not rows
//     (see `_demand_aggregate`'s action guard);
//   * a repeated request is deduped by the client-supplied
//     `X-Idempotency-Key` header (the body has no field for one). One key per
//     (mount, tile) means a SECOND click on the same tile inside one mount is a
//     replay: the server records nothing new and the dialog shows the existing
//     recorded state rather than a second toast. A fresh page load is a fresh
//     key, which is the honest reading of R4's one-record-per-click.
//
// WHAT THIS IS NOT. There is no delivery promise, no notify-intent state and no
// "we will email you": the click is a recorded demand signal the operator acts
// on by hand (R2/R4). The success copy says the interest is recorded and never
// that anything will be sent.

//: The demand route, exactly as `hosted_api.py` mounts it. A drift here is a
//: 404 that looks like a client bug, so it is asserted against the route table
//: by `tests/test_integrations_grid_inventory.py`.
export const DEMAND_PATH = '/v1/data-sources/demand'

//: The one action this client sends. The server also accepts `go_back`, and
//: deliberately does NOT send it: a dismissal is not a click, and counting it
//: would inflate the cross-org demand that triggers #3541.
export const DEMAND_ACTION_CLICK = 'click'

//: The mounted surfaces. `surface` names the calling UI in the record, so the
//: operator can tell a wizard click from a Settings click — which is also why
//: the component takes it as a prop from BOTH mounts.
export const DEMAND_SURFACE_WIZARD = 'wizard'
export const DEMAND_SURFACE_SETTINGS = 'settings'

//: The post-action states. Pinned (never a silent black hole): while the POST is
//: in flight the primary is DISABLED, and the failure state must not read as
//: recorded.
export const DEMAND_IDLE = 'idle'
export const DEMAND_SENDING = 'sending'
export const DEMAND_RECORDED = 'recorded'
export const DEMAND_FAILED = 'failed'

export const DEMAND_PRIMARY_LABEL = 'Register interest'
export const DEMAND_SENDING_LABEL = 'Registering…'
export const DEMAND_RETRY_LABEL = 'Retry'
export const DEMAND_DISMISS_LABEL = 'Dismiss'

export const DEMAND_SENDING_COPY = 'Recording your interest…'
//: The recorded state. States the FACT (the interest is saved) and nothing
//: about a date or a notification — there is no delivery obligation (R4).
export const DEMAND_RECORDED_COPY =
  "Recorded — your team's interest is saved."
//: The failure state. Names the failure instead of swallowing it, and claims
//: only what the client can actually know: that THIS request was not CONFIRMED.
//:
//: It must NOT say "nothing was saved". The client cannot know that: the
//: in-flight claim arm answers `503` ("this click is still being recorded —
//: please retry shortly") precisely because a peer's write may be landing right
//: now, and the earlier the client states absolute absence, the more the dialog
//: denies a record that may well exist — the same truthfulness defect this
//: copy replaced, mirrored. `Retry` is safe in EVERY server arm (the key is
//: reused, so a write that landed replays instead of recording twice), so the
//: honest instruction is the retry, not a claim about the store. The server's
//: own distinction is deliberately not rendered here (see `demandFailed`): its
//: messages are operator-facing, and this dialog owns its copy.
export const DEMAND_FAILED_COPY =
  'Your request was not confirmed — Retry to send it again.'

/** The pinned request body. `org_key` is absent BY DESIGN (server-derived). */
export function demandPayload(integrationKey, { surface = '', action = DEMAND_ACTION_CLICK } = {}) {
  if (typeof integrationKey !== 'string' || integrationKey.trim() === '') return null
  return { integration_key: integrationKey.trim(), action, surface: surface || '' }
}

/** A header-safe idempotency key for one (mount, tile) pair. */
export function newDemandKey(integrationKey, { random } = {}) {
  const tail = typeof random === 'function'
    ? String(random())
    : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`
  return `demand-${integrationKey}-${tail}`
}

/**
 * The idempotency key for `integrationKey`, minted ONCE per store (the mount).
 *
 * `store` is a `Map`. Reusing the key is what makes a second click a replay
 * (server: one record, `replayed: true`) and makes a Retry after a failed write
 * safe (the server releases its claim on a failed write, so the retry records).
 */
export function demandKeyFor(store, integrationKey, { random } = {}) {
  if (store.has(integrationKey)) return store.get(integrationKey)
  const key = newDemandKey(integrationKey, { random })
  store.set(integrationKey, key)
  return key
}

/**
 * The state a tile's FIRST open starts from.
 *
 * Deliberately takes NO argument and knows nothing about what happened before:
 * a tile that was already recorded (or is mid-request, or failed) on this mount
 * is looked up from the per-tile map by the caller (`demandStates[key]` in
 * main.jsx), which is the only place that map lives. Reading "already recorded"
 * into this function would give the state machine a second, divergent memory.
 */
export function demandInitialState() {
  return { status: DEMAND_IDLE, clickCount: null, replayed: false, status_http: null }
}

/** In flight: the primary is disabled from here until the outcome lands. */
export function demandSending(state = demandInitialState()) {
  return { ...state, status: DEMAND_SENDING }
}

/**
 * The recorded state. `clickCount` is the server's number, or `null` when the
 * server could not read its own store — `null` renders NO number rather than a
 * zero, which would tell the user their click was not counted when it was.
 */
export function demandRecorded(state = demandInitialState(), outcome = {}) {
  const count = Number.isInteger(outcome.clickCount) ? outcome.clickCount : null
  return { ...state, status: DEMAND_RECORDED, clickCount: count, replayed: !!outcome.replayed, status_http: null }
}

/**
 * The failure state: this request was NOT confirmed, and the UI says so.
 *
 * `status_http` is kept because it is a fact the client has; the server's
 * `reason` is NOT carried onto the state (the dialog renders `DEMAND_FAILED_COPY`,
 * never server prose) — what the client must not do is turn "no 2xx" into a
 * claim of ABSENCE, which the in-flight `503` arm contradicts.
 */
export function demandFailed(state = demandInitialState(), outcome = {}) {
  return {
    ...state,
    status: DEMAND_FAILED,
    clickCount: null,
    replayed: false,
    status_http: Number.isInteger(outcome?.status) ? outcome.status : null,
  }
}

/** The inline detail under the recorded copy — omitted, never faked, when null. */
export function demandRecordedDetail(state) {
  const n = state && state.clickCount
  if (!Number.isInteger(n) || n < 1) return ''
  return n === 1 ? '1 click from your team.' : `${n} clicks from your team.`
}

/**
 * Whether this tile's POST is in flight.
 *
 * Gates the PRIMARY only — never the dialog's dismissal. A browser blurs the
 * control that becomes `disabled`, so disabling the ghost Close as well would
 * leave the modal with no focusable node: focus would fall to `<body>` for the
 * whole round trip, where the dialog's own keydown (Escape) cannot see it. The
 * primary's own `disabled` is already a complete double-submit guard, and the
 * request is server-idempotent (`X-Idempotency-Key`) regardless.
 */
export function demandBusy(state) {
  return !!state && state.status === DEMAND_SENDING
}

/**
 * POST one click. NEVER throws and never reports a failed write as recorded.
 *
 * `request(path, opts)` is the App's `api` seam (same-origin BFF proxy, session
 * cookie, `Content-Type: application/json` on a body); it THROWS on a non-2xx
 * with `err.status` attached, which is exactly the signal the failure state
 * needs. A transport failure (no network, proxy down) arrives as a thrown
 * error with no `status`, and is reported as `status: null` rather than being
 * mistaken for a 200.
 */
export async function recordDemand({ integrationKey, surface, idempotencyKey, request }) {
  const body = demandPayload(integrationKey, { surface })
  if (!body) return { ok: false, status: null, reason: 'no integration key' }
  if (typeof request !== 'function') return { ok: false, status: null, reason: 'no request seam' }
  try {
    const response = await request(DEMAND_PATH, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Idempotency-Key': idempotencyKey,
      },
      body: JSON.stringify(body),
    })
    return {
      ok: true,
      status: 200,
      clickCount: Number.isInteger(response?.click_count) ? response.click_count : null,
      replayed: !!response?.replayed,
    }
  } catch (err) {
    return {
      ok: false,
      status: Number.isInteger(err?.status) ? err.status : null,
      reason: String((err && err.message) || 'request failed'),
    }
  }
}
