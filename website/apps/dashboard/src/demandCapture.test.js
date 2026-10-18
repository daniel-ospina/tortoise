// demandCapture.test.js — #3514 (epic #1714 J4): the coming-soon click's client
// half, asserted against the PINNED server contract.
//
// WHY THESE ASSERTIONS. The whole feature is a funnel event: a click that is not
// recorded is a demand signal lost with no diagnostic (the epic's silent-drop
// class). So the body's field names, the action vocabulary, the idempotency
// header and the post-action states are pinned here rather than left to the
// dialog's markup — a prop renamed in one place and not the other would still
// render a plausible-looking dialog while the record silently never landed.
// The server side of the same contract is asserted from Python
// (`tests/test_integrations_grid_inventory.py`), which also pins DEMAND_PATH
// against the real route table.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  DEMAND_ACTION_CLICK, DEMAND_DISMISS_LABEL, DEMAND_FAILED, DEMAND_FAILED_COPY,
  DEMAND_IDLE, DEMAND_PATH, DEMAND_PRIMARY_LABEL, DEMAND_RECORDED,
  DEMAND_RECORDED_COPY, DEMAND_RETRY_LABEL, DEMAND_SENDING, DEMAND_SENDING_COPY,
  DEMAND_SURFACE_SETTINGS, DEMAND_SURFACE_WIZARD,
  demandBusy, demandFailed, demandInitialState, demandKeyFor, demandPayload,
  demandRecorded, demandRecordedDetail, demandSending, newDemandKey, recordDemand,
} from './demandCapture.js'

// ── The pinned request body ────────────────────────────────────────────
test('#3514: the body is exactly {integration_key, action, surface}', () => {
  const body = demandPayload('slack', { surface: DEMAND_SURFACE_WIZARD })
  assert.deepEqual(Object.keys(body).sort(), ['action', 'integration_key', 'surface'])
  assert.equal(body.integration_key, 'slack')
  assert.equal(body.action, DEMAND_ACTION_CLICK)
  assert.equal(body.surface, DEMAND_SURFACE_WIZARD)
})

test('#3514: the client NEVER sends org_key — it is server-derived', () => {
  // A client-supplied org key would corrupt the cross-org demand ranking the
  // operator acts on. The server ignores the field; this pin stops it being
  // added "for convenience" and then trusted.
  const body = demandPayload('slack', { surface: DEMAND_SURFACE_SETTINGS })
  assert.equal('org_key' in body, false)
})

test('#3514: the client sends clicks only — never go_back', () => {
  // `go_back` is a valid SERVER value, but a dismissal is not demand: the read
  // half counts clicks, so a client that recorded dismissals as rows would
  // inflate the cross-org count that triggers #3541.
  assert.equal(DEMAND_ACTION_CLICK, 'click')
  assert.deepEqual(demandPayload('slack', { action: 'go_back' }).action, 'go_back',
    'the payload builder forwards whatever it is given — the CLIENT decides, and it decides click')
  assert.equal(demandPayload('  slack  ', {}).integration_key, 'slack', 'the key is trimmed')
})

test('#3514: an absent or blank key yields no body at all', () => {
  assert.equal(demandPayload('', {}), null)
  assert.equal(demandPayload(undefined, {}), null)
  assert.equal(demandPayload(null, {}), null)
})

test('#3514: the route path is the server\'s own', () => {
  assert.equal(DEMAND_PATH, '/v1/data-sources/demand')
})

// ── Idempotency: one key per (mount, tile) ─────────────────────────────
test('#3514: the idempotency key is stable per tile and distinct across tiles', () => {
  const store = new Map()
  const a = demandKeyFor(store, 'slack', { random: () => 'r1' })
  const again = demandKeyFor(store, 'slack', { random: () => 'r2' })
  const other = demandKeyFor(store, 'linear', { random: () => 'r3' })
  assert.equal(a, again, 'a second click on the same tile must REPLAY, not record twice')
  assert.notEqual(a, other)
  assert.equal(demandKeyFor(new Map(), 'slack', { random: () => 'r4' }), 'demand-slack-r4')
})

test('#3514: a minted key is header-safe and names its integration', () => {
  const key = newDemandKey('github_documents', { random: () => 'abc123' })
  assert.match(key, /^[A-Za-z0-9._-]+$/, 'an illegal header byte would 400 at the proxy')
  assert.ok(key.includes('github_documents'))
  // The default (no seam) is unique enough not to collide two mounts.
  assert.notEqual(newDemandKey('slack'), newDemandKey('slack'))
})

// ── The post-action states ─────────────────────────────────────────────
test('#3514: idle -> sending disables the primary', () => {
  const idle = demandInitialState()
  assert.equal(idle.status, DEMAND_IDLE)
  assert.equal(demandBusy(idle), false)
  const sending = demandSending(idle)
  assert.equal(sending.status, DEMAND_SENDING)
  assert.equal(demandBusy(sending), true, 'the primary must be disabled while the POST is in flight')
  assert.equal(demandBusy(null), false)
})

test('#3514: recorded shows the server count; failed must NOT read as recorded', () => {
  const recorded = demandRecorded(demandInitialState(), { clickCount: 3, replayed: false })
  assert.equal(recorded.status, DEMAND_RECORDED)
  assert.equal(recorded.clickCount, 3)
  assert.equal(demandRecordedDetail(recorded), '3 clicks from your team.')
  assert.equal(demandRecordedDetail(demandRecorded(demandInitialState(), { clickCount: 1 })),
    '1 click from your team.')

  const failed = demandFailed(demandSending(demandInitialState()), { status: 503 })
  assert.equal(failed.status, DEMAND_FAILED)
  assert.equal(failed.status_http, 503)
  assert.equal(failed.clickCount, null, 'a failed write must never show a count — it reads as recorded')
  assert.equal(demandRecordedDetail(failed), '')
})

test('#3514: an unreadable store count renders NO number, never a zero', () => {
  // The server answers click_count: null when it could not read its own store.
  // Rendering 0 would tell the user their click was not counted when it was.
  const state = demandRecorded(demandInitialState(), { clickCount: null })
  assert.equal(state.clickCount, null)
  assert.equal(demandRecordedDetail(state), '')
  assert.equal(demandRecordedDetail(demandRecorded(demandInitialState(), {})), '')
})

test('#3514: retry goes back to a disabled-primary sending state with the SAME key', () => {
  // The retry reuses the key, so a write that DID land (a lost response) replays
  // instead of recording twice. This is why the key lives per (mount, tile).
  const store = new Map()
  const key = demandKeyFor(store, 'slack', { random: () => 'k' })
  const failed = demandFailed(demandSending(demandInitialState()), {})
  const retry = demandSending(failed)
  assert.equal(retry.status, DEMAND_SENDING)
  assert.equal(demandBusy(retry), true)
  assert.equal(demandKeyFor(store, 'slack', { random: () => 'different' }), key)
})

// ── The copy: no delivery promise, no claim the client cannot support ──
test('#3514: the copy names the failure and promises no delivery', () => {
  assert.match(DEMAND_FAILED_COPY, /not confirmed/)
  assert.match(DEMAND_FAILED_COPY, /Retry/)
  assert.match(DEMAND_RECORDED_COPY, /interest is saved/)
  // R4 deleted the notify machinery: no label or copy may promise a send.
  const copy = [DEMAND_PRIMARY_LABEL, DEMAND_SENDING_COPY, DEMAND_RECORDED_COPY,
    DEMAND_FAILED_COPY, DEMAND_RETRY_LABEL, DEMAND_DISMISS_LABEL].join(' ')
  assert.doesNotMatch(copy, /we('| wi)ll (email|notify|let you know)/i)
  assert.doesNotMatch(copy, /email you/i)
})

test('#3514: the failure copy does NOT claim nothing was saved', () => {
  // The client cannot know this. The in-flight claim arm answers 503 ("this
  // click is still being recorded") precisely because a peer's write may be
  // landing right now, so an absolute "nothing was saved" renders that arm as
  // "not recorded" — the same truthfulness defect this copy replaced, mirrored.
  // Retry is safe in every arm (the same key replays), so state the retry.
  assert.doesNotMatch(DEMAND_FAILED_COPY, /nothing was saved/i)
  assert.doesNotMatch(DEMAND_FAILED_COPY, /was not recorded/i)
})

// ── recordDemand: never throws, never fakes a record ───────────────────
const SEAM_OK = (responses) => async (path, opts) => {
  responses.calls.push({ path, opts })
  return responses.body
}

test('#3514: a 2xx maps the server count and REPLAYS the idempotency header', async () => {
  const calls = []
  const outcome = await recordDemand({
    integrationKey: 'slack',
    surface: DEMAND_SURFACE_WIZARD,
    idempotencyKey: 'demand-slack-x1',
    request: SEAM_OK({ calls, body: { ok: true, click_count: 2, replayed: true } }),
  })
  assert.equal(outcome.ok, true)
  assert.equal(outcome.clickCount, 2)
  assert.equal(outcome.replayed, true)
  const [{ path, opts }] = calls
  assert.equal(path, DEMAND_PATH)
  assert.equal(opts.method, 'POST')
  assert.equal(opts.headers['X-Idempotency-Key'], 'demand-slack-x1')
  assert.deepEqual(JSON.parse(opts.body), {
    integration_key: 'slack', action: 'click', surface: DEMAND_SURFACE_WIZARD,
  })
})

test('#3514: a rejected write is reported as a failure, with its status', async () => {
  // The seam throws on non-2xx (the App's `api` attaches `.status`) — a 503 here
  // means THIS request did not record. The dialog surfaces it as not-confirmed,
  // never as "nothing was saved": the in-flight claim arm answers 503 while a
  // peer's write may be landing, so absence is not something the client knows.
  const err = new Error('the click was not recorded — please retry')
  err.status = 503
  const outcome = await recordDemand({
    integrationKey: 'slack', surface: DEMAND_SURFACE_WIZARD,
    idempotencyKey: 'k', request: async () => { throw err },
  })
  assert.equal(outcome.ok, false)
  assert.equal(outcome.status, 503)
  const state = demandFailed(demandSending(demandInitialState()), outcome)
  assert.equal(state.status, DEMAND_FAILED)
})

test('#3514: a transport failure (no status) is not mistaken for success', async () => {
  const outcome = await recordDemand({
    integrationKey: 'slack', surface: '', idempotencyKey: 'k',
    request: async () => { throw new TypeError('Failed to fetch') },
  })
  assert.equal(outcome.ok, false)
  assert.equal(outcome.status, null)
})

test('#3514: a missing seam or a blank key records nothing and does not throw', async () => {
  const noSeam = await recordDemand({ integrationKey: 'slack', idempotencyKey: 'k', request: null })
  assert.equal(noSeam.ok, false)
  const noKey = await recordDemand({
    integrationKey: '', idempotencyKey: 'k', request: async () => ({ click_count: 1 }),
  })
  assert.equal(noKey.ok, false)
})

test('#3514: a response without a count yields null, not a fabricated zero', async () => {
  const outcome = await recordDemand({
    integrationKey: 'slack', surface: '', idempotencyKey: 'k',
    request: async () => ({ ok: true, click_count: null }),
  })
  assert.equal(outcome.ok, true)
  assert.equal(outcome.clickCount, null)
})
