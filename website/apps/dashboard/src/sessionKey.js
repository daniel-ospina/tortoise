// #1708 D8: session-key classification from server-provided API data,
// extracted pure so it's unit-testable with node --test (no harness).
// (k: key row, activeKey: the current session's plaintext key, or null)
// #2246 (ADR-010): the dashboard is session-only — the browser never holds an
// API key in session mode. The held-key machinery (isSessionKey, isActiveKey,
// classifyHeldKey, heldKeyClearState, nextRegenInstallState,
// probeClassifyStoredKey) was deleted with its only consumer (main.jsx); the
// anon/key-login carve-out (claim-paste/Protect flows) never imported them.
// This module now holds the surviving pure core: the durable-row table
// predicate (isManagedKey), the connect-step key classifier (durableConnectKey
// — welcome plaintext, paste validation, rows-routed gate), and the rows
// resolution helper (usableDurableRows) the session connect gate sources from.

// #4342/#4359/#4370: the ONE authority for "is this a revealable plaintext?" —
// a non-empty, non-blank STRING. A 2xx can carry a number, an object, or
// padding, and every one of those is non-falsy, so a bare `!plaintext` check
// latched a box the user could not copy.
//
// #4370 (A6): this lives at MODULE scope — not inside main.jsx's App component —
// so the pure module that already reads held plaintext (`durableConnectKey`'s
// `welcome.startsWith`) shares the SAME guard instead of assuming a string. It
// is the single gateway every writer of reveal/embed key material reads before
// it stores: createKey, wizardMintDurableKey, regenerateKey, the create-graph
// envelope, the per-graph mint, copyNewKey, dismissKeyModal and the provisioned
// welcome key. Moving it here is what makes the pure module total; a caller
// that only guards its WRITERS leaves `durableConnectKey` exported and
// throwable on a non-string.
//
// `trim()` alone is NOT a blankness test — it strips whitespace but not
// zero-width / invisible characters, so a string of only those would pass this
// single gateway and reproduce the reported symptom exactly: a visually blank
// `.key-value` under a "Copied ✓". The class is the Unicode FORMAT set plus the
// DEFAULT-IGNORABLE set, plus BRAILLE PATTERN BLANK (U+2800), which is in
// NEITHER property. Only the DECISION uses the stripped copy — the returned
// value is always the verbatim secret.
export function revealablePlaintext(value) {
  return (typeof value === 'string' && value.replace(/[\p{Cf}\p{Default_Ignorable_Code_Point}\u2800]/gu, '').trim()) ? value : ''
}

// #4359: a mint response carries the plaintext on EITHER leg (`key` on the
// POST /v1/team/keys response, `api_key` on the provision envelope). The legs
// are composed through the predicate INDIVIDUALLY: `mk.key || mk.api_key`
// selected a truthy-but-unrevealable primary first, so `{key: {},
// api_key: 'tt_ok'}` was refused while a valid value sat in the other leg.
export function revealableMintPlaintext(response) {
  return revealablePlaintext(response && response.key) || revealablePlaintext(response && response.api_key)
}

// #2166 + #2426: durable product keys are the ONLY rows the API Keys page
// shows. The dashboard's OWN access credentials — auto-minted session keys
// (created_via 'bootstrap') — are never presented as product API keys.
// Durable = user/agent-created keys the user can name, toggle, revoke, and
// (since #2426) give a validity window at creation: created_via is the ONLY
// session-credential signal. An EXPIRING durable row (created_via
// 'provisioned'/'recovery'/NULL with expires_at set, e.g. a 30d mint) IS a
// product key — it must stay listed so the table can show its Expires state,
// and #2426's cap-accounting means an expired one is a rotatable tombstone,
// never a hidden row. Disabled (enabled:false) durable rows stay managed —
// the toggle needs them visible.
export function isManagedKey(k) {
  if (!k) return false
  return !(k.created_via === 'bootstrap')
}

// #8228: THE ROW-ALIVENESS ATOM — the ONE declaration of the dead-row
// conditions, and the only place in this module where they are spelled.
// Three surfaces ask "is this key row still alive for MY purpose?" and each
// used to restate the conditions independently:
//   1. `heldRowEmbeddable` — may this row back an EMBED? (the #4371 rule)
//   2. `usableDurableRows` — which rows may the connect gate ROUTE to?
//   3. `durableConnectKey`'s paste tail — which dead SOURCE should we report?
// They coincided, and NOTHING kept them coinciding: a new dead-row condition (a
// `disabled_by` stamp, a grace window, a new `created_via` kind) added to one
// was invisible to the other two, surfacing as a wrong embed or a wrong routing
// decision and never as an error — #4371's failure class one surface over.
// `rowAlivenessAgreementExec.test.js` drives all three over one shared fixture
// matrix and reports which surface(s) disagree with this atom.
//
// The atom answers WHY, not only WHETHER. The paste tail must report a DISTINCT
// source per cause ('revoked'/'disabled'/'bootstrap'/'expiring'), and one
// boolean cannot carry that — so the reason is the primitive and `rowAlive` is
// its totalisation. That is what lets the cascade CONSUME the conditions
// instead of restating them, and it is why a new arm added below is
// automatically reported by every consumer rather than falling through to
// `'durable'` (a dead row embedded as live: strictly worse than the duplication
// it removes). The reason vocabulary is a closed set — the agreement test pins
// it, so a new arm cannot reach main.jsx's paste handler unannounced.
//
// `'absent'` is not a stored column: it is the MISSING-ROW condition (a prefix
// that resolves no row, or a null row). A row we cannot see is dead — never
// embed or route on unknown.
export function rowDeadReason(row) {
  if (!row) return 'absent'
  if (row.revoked_at) return 'revoked'
  if (row.enabled === false) return 'disabled'
  if (row.created_via === 'bootstrap') return 'bootstrap'
  if (row.expires_at) return 'expiring'
  return ''
}

// The atom proper: a row is alive exactly when the atom has no reason to report.
export function rowAlive(row) {
  return rowDeadReason(row) === ''
}

// #2246 (ADR-010): rows-source connect resolution. The API Keys table shows
// rows the browser can MANAGE but never HOLD — GET /v1/team/keys returns
// hashes only, so a "usable" durable row can never supply a plaintext key.
// This predicate picks the rows the connect gate may route to as a REUSABLE
// durable — managed (bootstrap-excluded, #2426) ∧ ALIVE (#8228's atom: ¬expiring
// — an expiring key is not embed-safe, the setup command embeds a key an agent
// authenticates with indefinitely, so the embed policy stays Never-only, #2426
// decision 2 — ∧ ¬revoked ∧ ¬disabled ∧ ¬bootstrap; enabled absent → enabled,
// registry parity), most-recent first by created_at (ISO strings sort
// lexically). Consumers use it for gate copy / routing ONLY — never to derive
// an embeddable key.
//
// #8228: the conditions are NOT restated here any more — `rowAlive` carries
// them. `isManagedKey` stays in the composition on purpose: it is the TABLE
// predicate (which rows the API Keys page shows), a different question, and
// keeping it means a future relaxation of it can change what the gate ROUTES to
// without silently changing what may be EMBEDDED. Routing is therefore always a
// SUBSET of the atom — the safety direction the agreement test pins permanently.
export function usableDurableRows(keyRows) {
  return (keyRows || [])
    .filter((k) => isManagedKey(k) && rowAlive(k))
    .sort((a, b) =>
      String(b.created_at || b.createdAt || '').localeCompare(String(a.created_at || a.createdAt || '')))
}

// #4371: the ROW-TRUTH rule for an in-memory plaintext — declared ONCE here.
// It answers one question: "is the row behind a plaintext we still HOLD in
// memory usable for EMBEDDING?" A held plaintext is only as good as its row:
// once that row is revoked, disabled, a bootstrap session credential, or
// carries ANY expiry, the plaintext must not back a snippet or a connect
// command any more.
//
// TWO drivers ask it, and they must answer identically — #2246 (review) added
// the question to both, #2426 added the expiring arm to both:
//   - `durableConnectKey`'s welcome-stale tail (below), and
//   - `main.jsx`'s row-truth invalidation effect, which clears `welcomeKey` /
//     `wizardDurableKey` when a keys reload shows their row died.
// Before #4371 each INLINED the predicate and only PROSE claimed they were
// "symmetric / identical" (main.jsx said so in a comment, sessionKey.js said
// it back) — so a future dead-row condition added to one surface (a new
// created_via kind, `disabled_by`, a grace window) would silently
// mis-classify a held key on the other, showing up as a wrong embed and never
// as an error. Both now consume this one declaration; the agreement is held by
// `heldRowTruthExec.test.js`, which drives BOTH entry points over the same
// fixtures.
//
// Deliberately NOT expressed via `isManagedKey` (`… && !row.expires_at && …`):
// isManagedKey answers a DIFFERENT question — which rows the API Keys table
// shows — and #2426 relaxed it to bootstrap-exclusion-only, which is exactly
// when the embed rule had to grow its own explicit `expires_at` arm. Delegating
// here would re-couple the two, so a future table-predicate relaxation would
// silently relax what may be embedded.
//
// #8228: the conditions themselves now live in `rowDeadReason`/`rowAlive` (the
// row-aliveness atom above) and this function is a NAMED alias of the atom, kept
// because "embeddable" is the purpose-specific question its two drivers
// (`durableConnectKey`'s welcome tail and main.jsx's row-truth effect) ask. The
// routing rule (`usableDurableRows`) and the paste tail compose the SAME atom;
// before #8228 all three re-spelled the conditions and only prose held them
// together, so a condition added to one was invisible to the others.
export function heldRowEmbeddable(row) {
  return rowAlive(row)
}

// #1998 fold-in (durable connect key): the wizard connect step's universal
// command embeds an API key the user's agent will authenticate with. It must
// be a DURABLE key — a 24h bootstrap session credential (created_via
// 'bootstrap', expires_at = now+24h) stops authenticating within a day,
// killing any agent configured with it. #2426: expiring DURABLE keys are also
// not embed-safe (they stop authenticating at their chosen expiry — the
// connect step is the Never-keys-only embed surface, decision 2), so the
// classifier refuses them exactly like bootstrap rows but reports the
// DISTINCT source 'expiring' so the wizard's paste error can say why (an
// expiring 30d durable is NOT a "login session" key). This classifies the
// key the connect step should embed from server key rows (GET /v1/team/keys
// — which lists ALL rows incl. bootstrap, kept unfiltered in state so prefix
// matching works, #2166). Returns { key, durable, source }:
//   - welcomeKey present → durable (first-time A13 provisioned key) — UNLESS
//     keyRows is a loaded non-empty array and the welcome plaintext's prefix
//     row is absent/revoked/disabled/bootstrap/expiring: the shown-once
//     reveal is then STALE
//     (rotated/revoked/disabled after it was shown) and must fall through to
//     the rows/paste resolution as if welcomeKey were absent (#2246 review)
//   - apiKey (session-held plaintext OR a pasted candidate) matches a durable
//     row (created_via != bootstrap, no expires_at, not revoked, not disabled)
//     → { key: apiKey, durable: true, source: 'durable' } — the connect step
//     may embed it (session-held: pre-#2246 reuse; paste: self-paste trust)
//   - apiKey bootstrap/expiring/revoked/disabled/absent/UNKNOWN (no row — keys
//     not loaded yet or stale) → { key: '', durable: false } so the caller
//     shows the durable gate instead of embedding a possibly-24h or dead key.
//     Never embed on unknown.
// #2246 (session-mode): session users pass apiKey '' — no plaintext is ever
// held — so the gate resolves from the ROWS (usableDurableRows): a usable
// durable exists → source 'rows-durable' (gate copy routes to create/rotate;
// the plaintext was shown once at mint and is unrecoverable from rows);
// none → source 'none' (create-one). Paste validation keeps using the
// apiKey param with the pasted plaintext — revoked/disabled/bootstrap/
// expiring rows must still reject (the escape hatch's whole point).
export function durableConnectKey(welcomeKey, apiKey, keyRows) {
  // #2246 (review, P1/P2): row-truth stale check on the in-memory welcome
  // plaintext — post-#2246 the table actions are uniform one-click (rotate /
  // revoke / disable), so a welcomeKey whose prefix row is gone/revoked/
  // disabled must NOT win. P2: a bootstrap/expiring row is dead for embedding
  // too (a 24h session credential, or any key with an expiry, must never back
  // an embed); welcome keys are provisioned without expiry (created_via
  // 'provisioned', expires_at null), so this arm is a harmless belt. The rule
  // itself is `heldRowEmbeddable` (#4371) — the SAME declaration main.jsx's
  // row-truth effect consumes, so the two surfaces cannot drift.
  // Check only when keyRows is a LOADED non-empty array (null/[] = not loaded
  // yet — the pre-load welcome reveal must keep working; the provisioned row
  // lands in the same loadAll response). When stale, set the welcome plaintext
  // aside and proceed as if it were absent so the normal rows/paste resolution
  // (rows-durable / none / durable / bootstrap / expiring / revoked / disabled
  // / unknown) runs.
  // #4370 (A6): read the held plaintext through the SHARED predicate. Before
  // this, `welcome` was the raw argument, so a truthy non-string (or a blank
  // string) survived to `welcome.startsWith(...)` below — `TypeError:
  // …startsWith is not a function` — and a blank string WON as `source:
  // 'welcome'`, embedding whitespace into the connect snippet. This function is
  // exported and unit-tested, so the guard has to sit here, not only at
  // main.jsx's writer.
  let welcome = revealablePlaintext(welcomeKey)
  if (welcome && Array.isArray(keyRows) && keyRows.length > 0) {
    const row = keyRows.find((k) => k && k.key_prefix && welcome.startsWith(k.key_prefix))
    if (!heldRowEmbeddable(row)) welcome = ''
  }
  if (welcome) return { key: welcome, durable: true, source: 'welcome' }
  if (!apiKey) {
    const usable = usableDurableRows(keyRows)
    return { key: '', durable: false, source: usable.length ? 'rows-durable' : 'none' }
  }
  const row = (keyRows || []).find((k) => k && k.key_prefix === String(apiKey).slice(0, 10))
  if (!row) return { key: '', durable: false, source: 'unknown' }
  // #8228: the cascade READS the row-aliveness atom instead of restating its
  // conditions. Its arms used to be a fourth declaration of the same four
  // predicates — plausible, byte-different, and unpinned against the embed rule
  // and `usableDurableRows`. Now the reason IS the source: 'revoked' /
  // 'disabled' / 'bootstrap' / 'expiring' keep their distinct wizard copy, and
  // a NEW atom arm reports its own reason here without this site changing (the
  // vocabulary pin in `rowAlivenessAgreementExec.test.js` makes that new source
  // a deliberate decision — main.jsx's paste handler refuses ANY classification
  // that is not 'durable', so the failure direction can only be closed).
  const dead = rowDeadReason(row)
  if (dead) return { key: '', durable: false, source: dead }
  return { key: apiKey, durable: true, source: 'durable' }
}

// #3783: the connect step's KEY-SOURCE GATE. `durableConnectKey` answers
// "which plaintext, if any, may we EMBED" — it returns an empty key for BOTH
// `'none'` (the org holds no key at all) and `'rows-durable'` (a usable
// durable row exists, but its plaintext was displayed once and is not in this
// browser). The wizard rendered ONE affordance for both: the mint CTA. So an
// owner/admin whose organization was provisioned with a key at org creation
// (`created_via: 'provisioned'`, revealed in the org-create→connect window and
// lost by any reload before the connect step) minted a SECOND key at connect —
// spending the free tier's entire 2-key allowance on a key the user never got
// to use — while the Overview banner read the SAME `'rows-durable'` source and
// said an existing key was usable. This gate separates the two so the connect
// step can offer the existing key (rotate reuses its slot) instead of minting.
// Returns { mode, key, existing }:
//   - front-channel plaintext held → { mode: 'embed',  key, existing: null }
//   - usable durable row, no held plaintext → { mode: 'existing', existing: row }
//   - no usable key anywhere → { mode: 'mint', existing: null }
//   - rows not loaded yet → { mode: 'loading', existing: null }
//   - the rows GET FAILED → { mode: 'error', existing: null }
// Consumers may mint ONLY on `'mint'`; `'existing'` must route to a reuse path,
// and `'loading'`/`'error'` must offer NEITHER — neither is a resolved answer.
//
// #3783 (review P2): `keysLoaded` marks whether the rows payload has actually
// ARRIVED. Without it, `keys` initialises to `[]`, so a slow or failed `GET
// /v1/team/keys` made the gate resolve `'mint'` for an organization that may
// already hold a usable row — the UI offered a mint CTA that can burn the free
// tier's last slot in exactly the window it must not. The default is `true` so
// existing callers (and the unit tests) that pass a real rows array keep the
// loaded meaning; `main.jsx` passes the live load state explicitly.
//
// #3783 (review P2, second pass): `keysLoaded` flips on SUCCESS only, so it
// cannot tell "still in flight" from "the GET failed". Collapsing both into
// `'loading'` gave that arm no failure exit — a failed read rendered the wait
// state forever, its only recovery a full page reload. `keysError` (main.jsx's
// recorded failure) therefore resolves `'error'`: still NO mint (an unresolved
// read is not "no key", the slot-burn this gate exists to prevent), but a
// state the caller can act on with a retry. Default `false` keeps the callers
// that never observe a fetch (and the unit tests) unchanged.
export function connectKeyGate(welcomeKey, keyRows, keysLoaded = true, keysError = false) {
  const dc = durableConnectKey(welcomeKey, '', keyRows)
  if (dc.key) return { mode: 'embed', key: dc.key, existing: null }
  if (!keysLoaded) return { mode: keysError ? 'error' : 'loading', key: '', existing: null }
  if (dc.source === 'rows-durable') {
    return { mode: 'existing', key: '', existing: usableDurableRows(keyRows)[0] || null }
  }
  return { mode: 'mint', key: '', existing: null }
}

// #3783: a display identity for a key row the user never named. The org-create
// provisioning mints the organization's first key with `name: null`, so the
// API Keys table rendered it as "—" — the user could see it (the issue's own
// walk did) but could not ACCOUNT for it, and could not tell it apart from any
// other unnamed row. A `'provisioned'` row with no name IS the organization's
// setup key; name it. Review P2: `'recovery'` (the owner-level keyless-mint
// lane) and legacy rows minted before `created_via` existed are ALSO unnamed
// real, slot-consuming keys — leaving them as an unaccountable "—" is the same
// defect one row over, so they get a source-specific label too. The terminal
// fallback covers any future unnamed mint source: every row the API Keys table
// lists is a durable product key (bootstrap rows are excluded by
// `isManagedKey`), so none should render as nothing. Returns null only when
// there is no row at all; a user-named row always wins.
export function keyDisplayName(k) {
  if (!k) return null
  if (k.name) return k.name
  if (k.created_via === 'provisioned') return 'Organization key'
  if (k.created_via === 'recovery') return 'Recovery key'
  return 'API key'
}
