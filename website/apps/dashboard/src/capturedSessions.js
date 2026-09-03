// #2002 (W6, epic #1976): pure helpers for the Settings → Captured sessions
// home (W4's DE2E-11 seam — view/delete of captured transcripts). Pure (no
// React), node --test unit-tested (mirrors captureStatus.js / setupGuide.js).
//
// The home renders the /v1/sessions list rows + a transcript panel fed by
// GET /v1/sessions/{id}; the DELETE /v1/sessions/{id} contract (200
// {deleted: true, cleaned_receipts}) drives the row removal. All functions
// here are deterministic derivations the JSX consumes — no DOM, no fetch.

// Row list after a successful delete: drop the session whose id the server
// confirmed deleted (immutable — returns a NEW array, never mutates state).
export function removeSession(sessions, sessionId) {
  if (!Array.isArray(sessions)) return []
  return sessions.filter((s) => String(s && s.id) !== String(sessionId))
}

// Row caption meta: {turns, extracted} with safe defaults (a list row from
// /v1/sessions can omit either count — W4's honest states never fabricate).
export function sessionRowMeta(session) {
  const s = session || {}
  return {
    turns: Number.isFinite(s.turns) ? s.turns : 0,
    extracted: Number.isFinite(s.extracted) ? s.extracted : 0,
    id: s.id || '',
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

// Confirm copy for the per-row delete (server has no undo — the transcript
// + its extracted memory nodes are removed). Keeping the copy here (one
// place) also gives the pure module a stable, testable surface.
export const DELETE_CONFIRM =
  "Delete this captured session? Its transcript and extracted memory are " +
  "removed from this Organization permanently (the tool's capture hook is " +
  "not affected)."
