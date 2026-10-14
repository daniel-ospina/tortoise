// #2002 (W6, epic #1976): pure helpers for the Settings → Captured sessions
// home (W4's DE2E-11 seam — view/delete of captured transcripts). Pure (no
// React), node --test unit-tested (mirrors captureStatus.js / setupGuide.js).
//
// The home renders the /v1/sessions list rows + a transcript panel fed by
// GET /v1/sessions/{id}; the DELETE /v1/sessions/{id} contract (200
// {deleted: true, cleaned_receipts}) drives the row removal. All functions
// here are deterministic derivations the JSX consumes — no DOM, no fetch.

// ── #3520: the session-scoped EXTRACTION vocabulary ──────────────────────
//
// The SECOND of the two status axes, and deliberately not the first one. The
// per-HARNESS INSTALL axis (`off` / `install-pending` / `waiting` / `active`)
// lives in captureStatus.js and is UNCHANGED at four members: it answers "is
// capture installed for this harness?", which is a different question from
// "what happened to THIS session's memory?". Conflating them would leave the
// install word undefined whenever some sessions processed and some did not, so
// an install pill stays `active` beside a `stored-extraction-failed` session
// and there is no harness-level rollup of this axis anywhere.
//
// The members are the SERVER's vocabulary (the value of `extraction.status` on
// GET /v1/sessions and GET /v1/sessions/{id}; declared server-side in
// tortoise/session_projection.py::SESSION_EXTRACTION_STATES and derived in ONE
// place, `session_extraction_status`). EXACTLY THREE — a fourth member for
// "stored, outcome unrecorded" was considered and refused; that state is the
// `outcome_known` QUALIFIER's job, which rides BESIDE the set.
export const SESSION_EXTRACTION_STATES = Object.freeze([
  'stored', 'stored-and-processed', 'stored-extraction-failed',
])

// The rendered words. Plain, like the install labels: the STATE carries the
// claim, the word does not editorialise about who caused it.
export const SESSION_EXTRACTION_STATUS_LABEL = Object.freeze({
  'stored': 'stored, not extracted',
  'stored-and-processed': 'stored and processed',
  'stored-extraction-failed': 'stored, extraction failed',
})

// The QUALIFIER's own word — rendered INSTEAD of the status, never beside it.
// A commit-created, budget-hold, demo-seeded or longmem-ingest session carries
// `capture_ok = NULL`: it was written by a driver that records no extraction
// outcome, so `stored, not extracted` would be a FALSE NEGATIVE (a session
// that may well have been processed, reported as not processed).
export const SESSION_EXTRACTION_UNKNOWN_LABEL = 'outcome not recorded'

// The raw state member a row carries, or null when the payload predates #3520
// (or names a member outside the vocabulary — a server that grew a fourth
// state must render nothing here rather than a fabricated word).
export function sessionExtractionStatusFor(session) {
  const e = (session || {}).extraction
  if (!e || typeof e !== 'object') return null
  const status = String(e.status || '')
  return SESSION_EXTRACTION_STATES.includes(status) ? status : null
}

// True/False when the server recorded an outcome; null when the payload is
// pre-#3520 and carries no qualifier at all. Never coerced: `outcome_known`
// absent is NOT the same fact as `outcome_known: false`.
export function sessionExtractionOutcomeKnown(session) {
  const e = (session || {}).extraction
  if (!e || typeof e !== 'object') return null
  return typeof e.outcome_known === 'boolean' ? e.outcome_known : null
}

// The label a row renders for its extraction status, or null when there is
// nothing honest to say (a pre-#3520 payload). AN UNKNOWN OUTCOME IS NOT
// `stored` (R10): the qualifier REPLACES the status word, so a reader can never
// mistake "nobody recorded what happened" for "it was not processed".
export function sessionExtractionLabelFor(session) {
  const status = sessionExtractionStatusFor(session)
  if (status === null) return null
  if (sessionExtractionOutcomeKnown(session) === false) {
    return SESSION_EXTRACTION_UNKNOWN_LABEL
  }
  return SESSION_EXTRACTION_STATUS_LABEL[status]
}

// Row list after a successful delete: drop the session whose id the server
// confirmed deleted (immutable — returns a NEW array, never mutates state).
export function removeSession(sessions, sessionId) {
  if (!Array.isArray(sessions)) return []
  return sessions.filter((s) => String(s && s.id) !== String(sessionId))
}

// Row caption meta: {turns, extracted, actor} with safe defaults (a list row from
// /v1/sessions can omit either count — W4's honest states never fabricate).
// #2600: actor = the server-resolved display (membership email) when the
// seam provides it, else the raw actor_user_id; legacy null → '' (blank
// caption, never a crash).
// #2599: machine_id + model are client-claimed informational fields
// (forgeable, never security-trusted); absent → '' (blank display,
// "—" in the renderer).
export function sessionRowMeta(session) {
  const s = session || {}
  return {
    turns: Number.isFinite(s.turns) ? s.turns : 0,
    extracted: Number.isFinite(s.extracted) ? s.extracted : 0,
    id: s.id || '',
    actor: (s.actor_display || '').trim() || (s.actor_user_id || ''),
    machineId: (s.machine_id || '').trim() || '',
    model: (s.model || '').trim() || '',
    // #3520: the honest per-session extraction status. `extractionStatus` is
    // the server's state member (null on a pre-#3520 payload or an unknown
    // member), `extractionOutcomeKnown` the qualifier BESIDE it, and
    // `extractionLabel` the word the row renders — null when there is nothing
    // honest to say, never a fabricated `stored`.
    extractionStatus: sessionExtractionStatusFor(s),
    extractionOutcomeKnown: sessionExtractionOutcomeKnown(s),
    extractionLabel: sessionExtractionLabelFor(s),
  }
}

// Transcript panel model from GET /v1/sessions/{id} — normalizes the wire
// shape ({turn_points, extracted_points, turns, extracted}) into what the
// panel renders. Missing arrays → [] (the transcript may legitimately have
// zero extracted points; never render "undefined"). Boundary: a detail with
// null turn_points (graph fail-soft returns {session: null} — guarded by the
// caller) degrades to an empty panel, never a crash.
export function transcriptModel(detail) {
  const d = detail || {}
  const turns = Array.isArray(d.turn_points) ? d.turn_points : []
  const extracted = Array.isArray(d.extracted_points) ? d.extracted_points : []
  return {
    id: d.id || '',
    turns,
    extracted,
    counts: {
      turns: Number.isFinite(d.turns) ? d.turns : turns.length,
      extracted: Number.isFinite(d.extracted) ? d.extracted : extracted.length,
    },
    // #2600: same actor normalization as the list row (display when
    // present, else raw id; legacy null → '' — blank header, no crash).
    // #2599: machine_id + model — client-claimed informational;
    // absent → '' (blank display).
    actor: (d.actor_display || '').trim() || (d.actor_user_id || ''),
    machineId: (d.machine_id || '').trim() || '',
    model: (d.model || '').trim() || '',
    // #3520: the SAME extraction-status derivation as the list row — the
    // detail payload carries the same object, and the transcript panel must
    // not disagree with the row that opened it (list and detail agree).
    extractionStatus: sessionExtractionStatusFor(d),
    extractionOutcomeKnown: sessionExtractionOutcomeKnown(d),
    extractionLabel: sessionExtractionLabelFor(d),
  }
}

// Per-turn role → the .turn-* CSS class the transcript panel applies (#714
// vocabulary: user/assistant/system/tool; anything else is 'unknown').
export function turnRoleClass(role) {
  const r = String(role || '').toLowerCase()
  return { user: 'turn-user', assistant: 'turn-assistant',
           system: 'turn-system', tool: 'turn-tool' }[r] || ''
}

// Extracted point kind → the .kind-badge CSS class (decision/statement —
// untyped M2/v2 points are reported as 'statement' by the server).
export function kindBadgeClass(kind) {
  const k = String(kind || '').toLowerCase()
  return k === 'decision' ? 'kind-decision' : 'kind-statement'
}

// #3885 (e): deleting the RAW requires a DELIBERATE confirmation — the same
// type-to-confirm gate the product already uses for deleting a whole graph
// (#2701), not a native `window.confirm`. The owner's requirement covers EVERY
// storage mode (local / hosted-by-us / the user's own store): the confirmation
// is about the CONSEQUENCE, not about who holds the bytes. The required word
// and the consequence lines live here (one place): the modal RENDERS the word
// from DELETE_CONFIRM_WORD and passes that same constant into the gate
// (`deleteTypedMatches(typed, DELETE_CONFIRM_WORD)`), and its tripwire reads
// the constant too — so the word shown, the word required and the word pinned
// cannot drift apart.
export const DELETE_CONFIRM_WORD = 'delete'

// What the delete actually does, in the order the user meets it. The last
// line is the residual the retention promise carries and must NOT be hidden:
// backups of a deleted session are not actively purged — they age out on the
// ordinary backup cycle. Its length is stated once, in the canonical policy
// (docs/retention-and-deletion.md, "Backups"), and linked rather than
// restated here.
export const DELETE_CONSEQUENCES = Object.freeze([
  'The captured transcript is removed from the live service immediately.',
  'The memory extracted from it goes with it — points owned by this session are deleted.',
  'There is no Trash and no undo: unlike a deleted graph, a captured session cannot be restored.',
  "Your machine's capture hook is not affected — only what is already filed is removed.",
  'Copies in our backups are not actively deleted; they age out on the regular backup cycle.',
])
