import React from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
// #1623: plan display data (build-time import of product/pricing.json).
import { planOptions, STATUS_LABELS, TIER_LABELS } from './pricing.js'
import { HARNESS_CAPTURE_INSTALL, HARNESS_CAPTURE_REASON, HARNESS_CAPTURE_STATUS_LABEL, HARNESS_CAPTURE_SUPPORT, HARNESS_CONTINUE_LABEL, HARNESS_COPY_LABEL, HARNESS_FAMILIES, HARNESS_INSTALL, HARNESS_INTRO, HARNESS_NAMES, HARNESS_OAUTH, HARNESS_ORDER, HARNESS_PERSIST, HARNESS_SELF_INSTALL, HARNESS_SKILLS, HARNESS_SKILLLESS, HARNESS_SKILLS_IN_PROMPT, HARNESS_SKILLS_IN_STEPS, HARNESS_STEPS, SKILLS_INSTALL_URL, UNIVERSAL_COMMAND, WORKFLOWS_PROMPT, harnessDisplayName, harnessFamilyOf, preferredSurface } from './harnesses.js'
// #1728 Slice 3 (Tasks 16-17): the SHARED 4-state capture-status derivation
// (off → install-pending → waiting → active, probe-driven) — pure, node --test
// unit-tested (captureStatus.test.js). #1927: the re-ask gate predicate was
// removed with the consent gate (default-ON, ToS-covered).
import { captureStatusForHarness, lastErrorForHarness } from './captureStatus.js'
import { setupGuide } from './setupGuide.js'
// #2000 (W4): the Overview calm — EXACTLY 3 elements (connection status,
// memory digest, next action), zero toggles. Pure derivations, node --test
// unit-tested (overview.test.js).
import { overviewConnection, overviewDigest, overviewNextAction } from './overview.js'
// #1997 (W1): the 4 human onboarding steps — pure structure + copy + fork
// options + org-name validation, node --test unit-tested (wizardFlow.test.js).
import { WIZARD_STEPS, WIZARD_FORK_OPTIONS, resolveBuildCatalog, orgNameError, durableKeyName, wizardStageLabel } from './wizardFlow.js'
// #1894: indexed-state + job-progress derivations — pure, node --test
// unit-tested (memorySourcesStatus.test.js).
import { docsIndexedLabel, formatRelativeTime, jobStatusLine } from './memorySourcesStatus.js'
// #1708 D8: pure session-key predicates extracted to sessionKey.js (node --test
// unit-tested). #2166 + #2426: isManagedKey selects the durable product keys
// the API Keys page shows — bootstrap session credentials excluded, expiring
// durable keys (a #2426 mint with a chosen lifetime) INCLUDED. #2246
// (ADR-010): the held-key machinery (isActiveKey, isSessionKey,
// classifyHeldKey, heldKeyClearState, nextRegenInstallState,
// probeClassifyStoredKey) was deleted — the browser never holds an API key in
// session mode; durableConnectKey + usableDurableRows resolve the connect
// step's gate from the keys-table rows (Never-keys-only embed policy, #2426
// decision 2).
import { isManagedKey, durableConnectKey } from './sessionKey.js'
import {
  canManageGraphKeys,
  deleteTypedMatches,
  graphCanDelete,
  graphKeyPanelEmptyLine,
  graphKeysSuppressed,
  graphMintBody,
  graphsMeter,
  sortedGraphRows,
  sortedTrashRows,
  tierCreateLocked,
  TRASH_GRACE_DAYS,
  trashDaysLeft,
  trashEraseLabel,
} from './graphs.js'
// #1893: pure source-scope reconcile/serialize/job-body helpers (node --test
// unit-tested — sourceScope.test.js).
import {
  reconcileIssuesScope, reconcileDocsScope,
  serializeIssuesScope, serializeDocsScope,
  buildIssuesJobBody, buildDocsJobBody,
  shouldHydrate, shouldPersist, shouldResetBranch,
} from './sourceScope.js'
// #1765: identity surface — pure predicates + presentational components
import { bannerShow, shouldRefetchOnFocus } from './identity.js'
import { RecoveryBanner, ProfileTab, ReauthDialog } from './profile.jsx' 
// #2392: minimal a11y focus management for the dialog family — capture the
// opening trigger, restore focus to it on close (pure, node --test
// unit-tested — dialogFocus.test.js).
import { rememberFocusedTrigger, rememberRestoreTarget, restoreFocus } from './dialogFocus.js'

const API_BASE = 'https://api.premiselabs.co'
// #2246 (ADR-010): KEY_STORAGE ('tortoise_api_key') is the legacy held-key
// slot. Session mode never reads or writes it — at most it is PURGED (the
// one-shot session-mount cleanup + logout wipe). Only removeItem remains in
// main.jsx (mintTripwire.test.js pins zero getItem/setItem).
const KEY_STORAGE = 'tortoise_api_key'
// #1148-ux: remember the last auth method so the login card can surface it
const LAST_AUTH_METHOD = 'tortoise_last_auth_method'
// #1082 (PR1): the pasted claim key must survive the OAuth redirect (same-tab
// PKCE round-trip). sessionStorage is origin-scoped and same-tab — NEVER put
// the key in `redirectTo` (GoTrue embeds it in the OAuth state URL → leak).
// #1082 review P1-2: the raw key lives ONLY in app-origin sessionStorage —
// never a cookie. Cross-origin claim INTENT (welcome/signin/signup on the
// tortoise origin must know a claim is in flight so they don't mint a stray
// team) travels as a NON-SECRET marker cookie (tt_claim_pending=1) — it
// carries no credential, only a routing signal.
const CLAIM_KEY_STORAGE = 'tt_claim_key'
const INVITE_TOKEN_STORAGE = 'tortoise.inviteToken'
const CLAIM_PENDING_COOKIE = 'tt_claim_pending'

// ── #2426: configurable API-key expiration (mint-time, market presets) ─────
// Owner decision ("copy what's common"): 30d (default) / 60d / 90d / 1y /
// Custom date / No expiration (Never). Presets travel as expires_in DAYS on
// the mint body (1-366, GitHub's ceiling — the server computes the absolute
// expires_at; the dual-param server path is avoided). Custom dates are
// converted to whole days from today and clamped to the same 1-366 window.
// Expiry is immutable after creation (Anthropic rule — no PATCH-expiry), so
// the UI only ever sets it at create/rotate time.
const KEY_EXPIRY_PRESETS = [
  { id: '30', label: '30 days' },
  { id: '60', label: '60 days' },
  { id: '90', label: '90 days' },
  { id: '365', label: '1 year' },
  { id: 'custom', label: 'Custom date…' },
  { id: 'never', label: 'No expiration' },
]
const KEY_MAX_EXPIRY_DAYS = 366
const KEY_SOON_DAYS = 14
const _MS_PER_DAY = 86400000
// #2479 code-review fix P2: named constant for max re-auth attempts (spec: 1)
const MAX_REAUTH_ATTEMPTS = 1
const REAUTH_EXCEEDED_MESSAGE = 'Re-authentication failed — try again later or contact support.'

// #2426: Custom date (YYYY-MM-DD) → whole days until that date, clamped to
// 1..366. Null when missing/invalid/out-of-range — the + New key button stays
// disabled until a valid Custom date is picked (never silently mint Never).
function expiryDaysFromDate(dateStr) {
  if (!dateStr) return null
  const t = Date.parse(`${dateStr}T00:00:00Z`)
  if (Number.isNaN(t)) return null
  const days = Math.ceil((t - Date.now()) / _MS_PER_DAY)
  return (days >= 1 && days <= KEY_MAX_EXPIRY_DAYS) ? days : null
}

// #2426: a key row's lifetime span in days (expires_at − created_at at
// mint-time), clamped 1..366 — rotate carries the span over to the
// replacement (Cloudflare 'resets relative to now' semantics; a key minted
// 30d ago expiring in 5d → a fresh 30d replacement). Null when the row
// never expires (Never stays Never) or the timestamps are unreadable.
function lifetimeDaysFromRow(row) {
  if (!row || !row.expires_at) return null
  const start = Date.parse(row.created_at || row.createdAt || '')
  const end = Date.parse(row.expires_at)
  if (Number.isNaN(start) || Number.isNaN(end)) return null
  const days = Math.round((end - start) / _MS_PER_DAY)
  if (days < 1) return null
  return Math.min(KEY_MAX_EXPIRY_DAYS, days)
}

// #2426: expiry display derivation for the keys-table Expires cell.
// Returns { text, cls, title }: never → plain 'Never'; past → terminal
// 'expired' (row stays listed — tombstone, DigitalOcean's delete-on-expire
// is the anti-pattern); ≤14d → absolute date + amber 'in N days'; else the
// absolute date. The server's expires_at is an exact mint-time timestamp,
// so a 30d key reads 'in 30 days' the day it is made (ceil keeps a
// partially-elapsed day from understating).
function fmtExpiry(iso, now = Date.now()) {
  if (!iso) return { text: 'Never', cls: '' }
  const t = Date.parse(iso)
  if (Number.isNaN(t)) return { text: String(iso), cls: '' }
  const dateText = new Date(t).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })
  const ms = t - now
  if (ms <= 0) return { text: 'expired', cls: 'expired', title: `Expired ${dateText}` }
  const days = Math.ceil(ms / _MS_PER_DAY)
  if (days <= KEY_SOON_DAYS) {
    return { text: `${dateText} · in ${days} day${days === 1 ? '' : 's'}`, cls: 'expiring', title: `Expires ${new Date(t).toLocaleString()}` }
  }
  return { text: dateText, cls: '', title: `Expires ${new Date(t).toLocaleString()}` }
}

// #2426: rotate confirm + show-once card share this human date string
// (absolute, locale-formatted) for an expiry instant.
function fmtExpiryDate(iso) {
  const t = Date.parse(iso)
  if (Number.isNaN(t)) return String(iso)
  return new Date(t).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })
}

// #2246 (PM-1): the destructive-key confirms (API-Keys revoke + rotate, and
// the per-graph panel revoke) name their target as "name · prefix · created"
// so a one-click action never silently kills an agent key the user cannot
// identify — rows are hash-only and names may be unset. ONE derivation for
// all three sites (the #2246 contract, not per-site copy).
function keyRowDisclosure(row, fallbackName) {
  const name = (row && row.name) || fallbackName || 'this API key'
  const prefix = (row && row.key_prefix)
    || String((row && (row.id || row.key_id)) || '').slice(0, 8)
  const createdIso = row && (row.created_at || row.createdAt)
  const created = createdIso && !Number.isNaN(Date.parse(createdIso))
    ? new Date(Date.parse(createdIso)).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })
    : ''
  return [name, prefix, created].filter(Boolean).join(' · ')
}


// #2002 (W6): pure captured-sessions view/delete derivations (node --test —
// capturedSessions.test.js) for the Settings Captured-sessions home.
import {
  removeSession, sessionRowMeta, transcriptModel,
  turnRoleClass, kindBadgeClass, DELETE_CONFIRM,
} from './capturedSessions.js'
// #2001 (W5): the Setup-guide card mirror — ONE shared derivation
// (setupGuide.js, node --test) of the graph-held FLOW state. Renders the
// counted checklist (fork-aware), collapses when complete (status-driven,
// incl. grandfathered), DEGRADED when the server reports FLOW 'unavailable'
// (never a false checklist), LOADING during the fetch transient. The reentry
// card defers to this one (the wizard re-opens from the empty state only).
function SetupGuideCard({ state, loading, onResume }) {
  const g = setupGuide(state)
  if (loading) {
    return (
      <div className="card">
        <div className="card-val"><span className="skeleton" style={SKEL_VALUE} aria-hidden="true" /></div>
        <div className="card-label"><span className="skeleton" style={SKEL_LABEL} aria-hidden="true" /></div>
      </div>
    )
  }
  if (!state) {
    // #2000 (W4) review P2-2: onboarding fetch FAILED (state never landed,
    // loading done) → an honest error card — never a bare "0/0" checklist.
    return (
      <div className="card">
        <div className="card-val">—</div>
        <div className="card-label">Setup guide</div>
        <div className="setup-guide-degraded dim">Couldn't load setup status — refresh to retry.</div>
      </div>
    )
  }
  if (g.degraded) {
    return (
      <div className="card">
        <div className="card-val">Unavailable</div>
        <div className="card-label">Setup guide</div>
        <div className="setup-guide-degraded dim">Graph read failed — retry shortly</div>
      </div>
    )
  }
  if (g.collapsed) {
    return (
      <div className="card">
        <div className="card-val" aria-label="Setup complete">✓</div>
        <div className="card-label">Setup guide · complete</div>
      </div>
    )
  }
  return (
    <div className="card setup-guide-card">
      <div className="card-val">{g.done}/{g.total}</div>
      <div className="card-label">Setup guide{g.currentStep ? ` · next: ${g.rows.find((r) => r.id === g.currentStep)?.label || g.currentStep}` : ''}</div>
      <ul className="setup-guide-rows">
        {g.rows.map((r) => (
          <li key={r.id} className={r.done ? 'done' : ''} aria-label={`${r.label}${r.done ? ' — done' : ''}`}>
            {r.done ? '✓ ' : '· '}{r.label}{r.counted ? '' : ' (info)'}
          </li>
        ))}
      </ul>
      {/* #2000 (W4): mid-flight resume affordance — Settings owns the Setup
          guide home (DE2E-6); the resume button re-opens the wizard
          (idempotent-safe re-entry: org-create 409-advance + fork replay is
          a set-once 'same' 200). W9 owns the fork-aware step-MAPPED resume;
          W4 names the seam. */}
      {onResume && g.status === 'active' && (
        <div className="setup-guide-resume" style={{ marginTop: 10 }}>
          <button type="button" className="btn-primary small" onClick={onResume}>Resume setup →</button>
        </div>
      )}
    </div>
  )
}

// #2000 (W4): the calm Overview — EXACTLY 3 elements (DE2E-2: connection
// status, memory digest, next action), zero feature toggles, honest states
// (loading skeleton / unavailable on graph-down, never a fabricated digest
// or checklist). Derivation lives in overview.js (pure, node --test).
//
// A shared skeleton slot for the three elements (mirrors the old stat-card
// shimmer — no CLS while the fetch resolves).
function OverviewElementSkeleton({ label }) {
  return (
    <div className="card">
      <div className="card-val"><span className="skeleton" style={SKEL_VALUE} aria-hidden="true" /></div>
      <div className="card-label"><span className="skeleton" style={SKEL_LABEL} aria-hidden="true" /></div>
      <div className="card-label">{label}</div>
    </div>
  )
}

// Element 1 — connection status (agent reported in via the harness).
function OverviewConnectionCard({ state, loading }) {
  const c = overviewConnection(state)
  // failed-fetch honesty: state never landed → an error variant, never an
  // eternal skeleton (the old panel showed a refresh-to-retry card here).
  if (!state && !loading) {
    return (
      <div className="card overview-element" aria-label="Connection status">
        <div className="card-val">—</div>
        <div className="card-label">Connection status</div>
        <p className="overview-detail dim small" style={{ margin: '6px 0 0' }}>Couldn't load — refresh to retry.</p>
      </div>
    )
  }
  if (c.kind === 'loading') return <OverviewElementSkeleton label="Connection status" />
  return (
    <div className="card overview-element" aria-label="Connection status">
      <div className="card-val" style={c.kind === 'connected' ? { color: 'var(--green,#4ade80)' } : undefined}>{c.value}</div>
      <div className="card-label">Connection status</div>
      {c.detail && <p className="overview-detail dim small" style={{ margin: '6px 0 0' }}>{c.detail}</p>}
    </div>
  )
}

// Element 2 — memory digest (honest in-graph point count).
function OverviewDigestCard({ points }) {
  const d = overviewDigest(points)
  if (d.kind === 'unavailable') {
    return (
      <div className="card overview-element" aria-label="Memory digest">
        <div className="card-val">—</div>
        <div className="card-label">Memory digest</div>
        <p className="overview-detail dim small" style={{ margin: '6px 0 0' }}>{d.detail}</p>
      </div>
    )
  }
  return (
    <div className="card overview-element" aria-label="Memory digest">
      <div className="card-val">{d.value.toLocaleString()}</div>
      <div className="card-label">Memory digest</div>
      <p className="overview-detail dim small" style={{ margin: '6px 0 0' }}>{d.detail}</p>
    </div>
  )
}

// Element 3 — next action (Setup-guide current step; same graph-held state
// the Settings Setup guide renders — DE2E-6). Active flows get ONE calm CTA
// into the Setup guide; done/collapsed collapses to a status; degraded is
// honest (never a false next step).
function OverviewNextActionCard({ state, loading, onOpenSettings }) {
  const g = setupGuide(state)
  const a = overviewNextAction(g)
  if (!state && !loading) {
    return (
      <div className="card overview-element" aria-label="Next action">
        <div className="card-val">—</div>
        <div className="card-label">Next action</div>
        <p className="overview-detail dim small" style={{ margin: '6px 0 0' }}>Couldn't load setup status — refresh to retry.</p>
      </div>
    )
  }
  if (a.kind === 'loading') return <OverviewElementSkeleton label="Next action" />
  return (
    <div className="card overview-element" aria-label="Next action">
      <div className="card-val" style={{ fontSize: 20, color: (a.kind === 'active' || a.kind === 'done') ? 'var(--green,#4ade80)' : undefined }}>{a.value}</div>
      <div className="card-label">Next action</div>
      {a.kind === 'active' && (
        <div style={{ marginTop: 8 }}>
          <button type="button" className="btn-primary small" onClick={onOpenSettings}>Open Setup guide →</button>
        </div>
      )}
      {a.detail && <p className="overview-detail dim small" style={{ margin: '6px 0 0' }}>{a.detail}</p>}
    </div>
  )
}

// #2000 (W4) review P2-6: the Backups summary relocated from the Overview to
// the API Keys tab. It needs its OWN 15s loading floor — the old Overview
// card's frameStale latch only ticks while the Overview tab is mounted
// (#1842/#1923), so the relocated card can never rely on it. Local floor:
// skeleton for 15s, then the honest '—' frame.
function BackupsCard({ status, count }) {
  const [stale, setStale] = React.useState(false)
  React.useEffect(() => {
    if (status !== 'loading') { setStale(false); return undefined }
    const t = setTimeout(() => setStale(true), 15_000)
    return () => clearTimeout(t)
  }, [status])
  const value = status === 'ok' ? (count || 'none')
    : (status === 'loading' && !stale) ? <span className="skeleton" style={SKEL_VALUE} aria-hidden="true" />
      : '—'
  return (
    <div className="card" style={{ marginTop: 16 }}>
      <div className="card-val" style={{ fontSize: 18 }}>{value}</div>
      <div className="card-label">Backups{status === 'error' ? ' — could not load (retry later)' : ''}</div>
    </div>
  )
}

// #2000 (W4): the Settings tab — the 7th tab (R2-11), FOUR homes (epic plan
// P3): Setup guide (DE2E-6 — same graph-held state the Overview next-action
// mirrors), GitHub connect, Memory sources (the ONLY live home of the four
// source toggles — DE2E-2 reachability), Captured sessions (DE2E-11 home).
// Single-owner (R2-10): W6 consumes the capture home (view/delete + DELETE
// /v1/sessions/{id}); W9 consumes the Setup-guide home (fork-aware resume
// mapping). W4 builds the homes + names the seams — no speculative W6/W9
// behavior.
function SettingsTab(props) {
  const {
    state, loading, onResumeSetup,
    github, githubConnected, reposCount, onConnectGithub, githubError,
    sessions, sessionsOn, sessionsLoading,
    memorySourcesProps,
    // #2002 (W6): captured-sessions view/delete consumer props (the W4 seam:
    // W4 built the home + named the seam; W6 consumes it).
    selectedSessionId, onViewSession, onClearSessionDetail,
    sessionDetail, detailLoading, sessionDetailError,
    onDeleteSession, deletingSessionId, sessionsActionError,
  } = props
  const reposNote = githubConnected
    ? (github.repos != null ? `${github.repos} repos available`
      : (reposCount != null && reposCount > 0 ? `${reposCount} repos available`
        : 'repos available'))
    : null
  const fmt = (iso) => { if (!iso) return '—'; try { return new Date(iso).toLocaleString() } catch { return iso } }
  // #2002 (W6): one captured session is expanded at a time — the open row
  // mirrors the App-level sessionDetail/selectedSessionId (kept in the App
  // so team switches + logouts clear it in one place).
  const openSessionId = selectedSessionId ? String(selectedSessionId) : null
  return (
    <section className="settings" aria-label="Settings">
      <h2>Settings</h2>

      {/* ── Home 1: Setup guide (DE2E-6) — renders the SAME graph-held
          OnboardingState node as the Overview next-action element; the
          card's Resume re-opens the wizard (idempotent re-entry). W9 owns
          the fork-aware step-mapped resume. ── */}
      <section className="settings-home" aria-labelledby="settings-setup-guide-heading">
        <h3 id="settings-setup-guide-heading">Setup guide</h3>
        <p className="dim small">Where your Organization is in setup — reopen the wizard any time; what you've done is saved.</p>
        <SetupGuideCard state={state} loading={loading} onResume={onResumeSetup} />
      </section>

      {/* ── Home 2: GitHub connect (P3 — moved off the first-run surface).
          The source toggles + scope/re-index live under Memory sources
          below; this home owns the OAuth connection state. Connect errors
          surface on the issues row (one mechanism — no drift). ── */}
      <section className="settings-home" aria-labelledby="settings-github-heading">
        <h3 id="settings-github-heading">GitHub connect</h3>
        {loading ? (
          <p className="dim">Loading GitHub status…</p>
        ) : githubConnected ? (
          <p className="dim small">
            <span className="live">Connected</span>{reposNote ? ` — ${reposNote}. ` : '. '}
            Issues and docs index to this Organization's graph. Manage scope and re-index under Memory sources below.
          </p>
        ) : (
          <>
            <p className="dim small">
              Connect GitHub to bring issues and repo docs into your Organization as memory sources.
            </p>
            <div style={{ marginTop: '0.5rem' }}>
              <button type="button" className="btn-primary" onClick={onConnectGithub} disabled={github.busy}>
                {github.busy ? 'Connecting…' : 'Connect GitHub'}
              </button>
            </div>
            {/* #2000 (W4) review P2-1: connect errors surface NEXT to the
                connect button too (the Memory-sources issues row below is
                the same state — one mechanism, two render sites, no
                divergence). A failed OAuth round-trip / popup block must
                read here, not one full home down. */}
            {githubError && <p className="error" role="alert" style={{ marginTop: '0.5rem' }}>{githubError}</p>}
          </>
        )}
      </section>

      {/* ── Home 3: Memory sources — the ONLY live home of the four source
          toggles (github_connected / github_indexed / github_docs_indexed /
          session_recording). DE2E-2: reachable only via Settings → Memory
          sources. ── */}
      <section className="settings-home" aria-labelledby="settings-memory-heading">
        <h3 id="settings-memory-heading">Memory sources</h3>
        <p className="dim small">Choose what Tortoise remembers — sources you switch on index to this Organization's graph; session recording is on by default and can be turned off any time.</p>
        <MemorySources {...memorySourcesProps} />
      </section>

      {/* ── Home 4: Captured sessions (DE2E-11 home). W6 consumer seam:
          W6 adds transcript view + DELETE /v1/sessions/{id} + receipt
          cleanup into this home. W4 renders the honest state: recording
          flag + the captured-session list (read-only). ── */}
      <section className="settings-home" aria-labelledby="settings-capture-heading">
        <h3 id="settings-capture-heading">Captured sessions</h3>
        <p className="dim small">
          When session recording is on, sessions from tools with capture installed are filed to this Organization as memory.
        </p>
        {/* #2000 (W4) review P2-3: honest states — never a fabricated
            "recording is off" while the onboarding state is still loading
            or failed to fetch (session_recording defaults ON, #1927). */}
        {sessionsLoading ? (
          <p className="dim">Loading capture status…</p>
        ) : !state ? (
          <p className="dim">Couldn't load capture status — refresh to retry.</p>
        ) : !sessionsOn ? (
          <p className="dim">
            Session recording is off — new sessions are not captured. Turn it on under Memory sources above.
          </p>
        ) : sessions.length === 0 ? (
          <p className="dim">No captured sessions yet — recordings appear here after your agent's first session.</p>
        ) : (
          <>
            <ul className="captured-sessions" style={{ listStyle: 'none', margin: '0.4rem 0 0', padding: 0 }}>
              {sessions.map((s) => {
                const meta = sessionRowMeta(s)
                const sid = String(s.id)
                const open = openSessionId === sid
                const deleting = deletingSessionId === sid
                return (
                  <li key={sid} className="captured-session" style={{ display: 'flex', gap: '0.75rem', alignItems: 'baseline', padding: '0.35rem 0', borderBottom: '1px solid var(--border,#1e293b)' }}>
                    <span className="small">{fmt(s.created_at)}</span>
                    <span className="dim small">{meta.turns} turns · {meta.extracted} extracted</span>
                    {/* #2600: server-resolved actor (membership email when the
                        seam retains it, else the raw id) — legacy null renders
                        nothing, never a crash. */}
                    {meta.actor && (
                      <span className="dim small" style={{
                        marginLeft: '0.4rem',
                        maxWidth: '22ch',
                        minWidth: 0,
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                        whiteSpace: 'nowrap',
                        verticalAlign: 'bottom',
                      }} title={meta.actor}>{meta.actor.includes('@') ? `by ${meta.actor}` : meta.actor}</span>
                    )}
                    {/* #2599: machine_id + model — client-claimed informational
                        fields; absent renders nothing (legacy / no-hook). */}
                    {meta.machineId && meta.model && (
                      <span className="dim small" style={{ marginLeft: '0.4rem' }} title={`${meta.machineId} · ${meta.model}`}>
                        · {meta.machineId} · {meta.model}
                      </span>
                    )}
                    {meta.machineId && !meta.model && (
                      <span className="dim small" style={{ marginLeft: '0.4rem' }} title={meta.machineId}>· {meta.machineId}</span>
                    )}
                    {!meta.machineId && meta.model && (
                      <span className="dim small" style={{ marginLeft: '0.4rem' }} title={meta.model}>· {meta.model}</span>
                    )}
                    <code className="dim small" style={{ marginLeft: 'auto' }}>{sid.slice(0, 12)}…</code>
                    {/* #2002 (W6): per-row View (expands the transcript
                        panel below) + Delete (confirm → DELETE
                        /v1/sessions/{id} + receipt cleanup). */}
                    <span style={{ display: 'flex', gap: '0.4rem' }}>
                      <button type="button" className="ghost small"
                        aria-expanded={open}
                        onClick={() => (open ? onClearSessionDetail() : onViewSession(s.id))}
                        disabled={!!deletingSessionId}>
                        {open ? 'Close' : 'View'}
                      </button>
                      <button type="button" className="ghost small"
                        style={{ color: 'var(--red,#f87171)' }}
                        disabled={!!deletingSessionId}
                        onClick={() => {
                          if (!window.confirm(DELETE_CONFIRM)) return
                          onDeleteSession(s.id)
                        }}>
                        {deleting ? 'Deleting…' : 'Delete'}
                      </button>
                    </span>
                  </li>
                )
              })}
            </ul>
            {/* #2002 (W6): delete action error surfaces under the list (row-
                level, never a fabricated success) + the transcript panel for
                the open session (loading / honest error / transcript body). */}
            {sessionsActionError && (
              <p className="error" role="alert" style={{ marginTop: '0.5rem' }}>{sessionsActionError}</p>
            )}
            {openSessionId && (
              <SessionTranscriptPanel
                sessionId={openSessionId}
                detail={sessionDetail && String(sessionDetail.id) === openSessionId ? sessionDetail : null}
                loading={detailLoading}
                error={sessionDetailError}
                onRetry={onViewSession ? () => onViewSession(openSessionId) : null}
                onClose={onClearSessionDetail}
              />
            )}
          </>
        )}
      </section>
    </section>
  )
}

// #2002 (W6): the transcript panel for one captured session — fed by
// GET /v1/sessions/{id} (turn_points + extracted_points) and rendered with
// the #714 session-detail CSS vocabulary (.turn-* / .kind-* — shipped with
// the original session detail view). Honest states: loading, error (with
// retry), or the transcript body (turns + extracted memory). Never
// fabricates an empty transcript from a failed fetch.
function SessionTranscriptPanel({ sessionId, detail, loading, error, onRetry, onClose }) {
  if (loading && !detail) {
    return (
      <div className="session-detail" aria-label={`Session ${sessionId} transcript`}>
        <p className="dim">Loading transcript…</p>
      </div>
    )
  }
  if (error && !detail) {
    return (
      <div className="session-detail" aria-label={`Session ${sessionId} transcript`}>
        <p className="error" role="alert">{error}</p>
        <div style={{ marginTop: '0.5rem', display: 'flex', gap: '0.5rem' }}>
          {onRetry && <button type="button" className="ghost small" onClick={onRetry}>Retry</button>}
          <button type="button" className="ghost small" onClick={onClose}>Close</button>
        </div>
      </div>
    )
  }
  if (!detail) return null
  const tm = transcriptModel(detail)
  return (
    <div className="session-detail" aria-label={`Session ${sessionId} transcript`}>
      <div className="dim small" style={{ margin: '0.5rem 0 0.25rem' }}>
        Transcript — {tm.counts.turns} turns · {tm.counts.extracted} extracted memory
        {/* #2600: same actor normalization as the row (display or raw id;
            legacy null renders blank, never a crash). */}
        {tm.actor && <span style={{ marginLeft: '0.5rem' }}>{tm.actor.includes('@') ? `by ${tm.actor}` : tm.actor}</span>}
        {/* #2599: machine_id + model — client-claimed informational; absent
            renders nothing (legacy / no-hook). */}
        {tm.machineId && tm.model && (
          <span className="dim small" style={{ marginLeft: '0.4rem' }} title={`${tm.machineId} · ${tm.model}`}>
            · {tm.machineId} · {tm.model}
          </span>
        )}
        {tm.machineId && !tm.model && (
          <span className="dim small" style={{ marginLeft: '0.4rem' }} title={tm.machineId}>· {tm.machineId}</span>
        )}
        {!tm.machineId && tm.model && (
          <span className="dim small" style={{ marginLeft: '0.4rem' }} title={tm.model}>· {tm.model}</span>
        )}
      </div>
      {tm.turns.length === 0 ? (
        <p className="dim small">No conversation turns stored for this session.</p>
      ) : (
        <div className="turn-list" style={{ marginTop: '0.5rem' }}>
          {tm.turns.map((t, i) => (
            <div key={t.id || `t${i}`} className={`turn-item ${turnRoleClass(t.role)}`}>
              <div className="turn-header">
                <span className="turn-role">{t.role || 'unknown'}</span>
                {t.id && <span className="turn-index">{t.id}</span>}
              </div>
              <div className="turn-content">{t.content}</div>
            </div>
          ))}
        </div>
      )}
      {tm.extracted.length > 0 && (
        <div className="session-section">
          <h3>Extracted memory</h3>
          <div className="extracted-list">
            {tm.extracted.map((p) => (
              <div key={p.id || p.content} className="extracted-item">
                <div className="extracted-header">
                  <span className={`kind-badge ${kindBadgeClass(p.kind)}`}>{p.kind || 'statement'}</span>
                </div>
                <div className="turn-content">{p.content}</div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}



// Non-secret claim-intent marker (parent domain): lets the welcome page's
// Phase-2 mint guard and the signin/signup claim-intent routing on
// tortoise.premiselabs.co know a claim is in flight from the dashboard
// (app.premiselabs.co) — without exposing the raw tt_ key (P1-2).
// Short TTL (1h — the OAuth round-trip is minutes); SameSite=Lax, Secure via
// the host-conditional secureAttr() (#1857) so the marker also works on
// localhost/previews.
function setClaimPendingMarker() {
  try {
    const expires = new Date(Date.now() + 60 * 60 * 1000).toUTCString()
    document.cookie = `${CLAIM_PENDING_COOKIE}=1${domainAttr()}; Path=/; SameSite=Lax${secureAttr()}; Expires=${expires}`
  } catch { /* best-effort */ }
}

function clearClaimPendingMarker() {
  try {
    document.cookie = `${CLAIM_PENDING_COOKIE}=;${domainAttr()}; Path=/; SameSite=Lax${secureAttr()}; Max-Age=0`
  } catch { /* best-effort */ }
}
const SUPABASE_URL = 'https://ybetwichurajbfswfeqa.supabase.co'
const SUPABASE_ANON_KEY = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InliZXR3aWNodXJhamJmc3dmZXFhIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODUyNzgzNDYsImV4cCI6MjEwMDg1NDM0Nn0.YHysJAebPualDNDQTU5bnGBUHg5guLe8eBadm0LiEiY'

// ── Parent-domain cookie storage (cross-subdomain session, D5 #572) ──
// supabase-js v2 defaults to localStorage (origin-scoped) — a session created
// on tortoise.premiselabs.co never reaches app.premiselabs.co. This adapter
// persists the session token in a cookie scoped to .premiselabs.co so both
// subdomains share it (plan §5.3 d2: PKCE + parent-domain cookie).
const COOKIE_NAME = 'sb-tortoise-auth-token'
const COOKIE_DOMAIN = '.premiselabs.co'
// #1835: encoded-bytes cap for the 4096-byte cookie limit. Google OAuth
// sessions (provider_token ~1200 chars + full identity) encode to ~5012
// bytes — an oversized cookie is SILENTLY rejected by the browser →
// getSession() returns null → the mount gate bounces to /auth (the GitHub
// loop was never hit because its provider token is shorter). Mirrors
// website/assets/supabase-session.js SIZE_GUARD exactly.
const SIZE_GUARD = 3800
// #1857: host-conditional cookie attributes (RFC 6265). A hardcoded
// `Domain=.premiselabs.co; Secure` is REJECTED by the browser on localhost,
// 127.0.0.1, and *.pages.dev preview origins (non-matching Domain → cookie
// silently dropped; Secure over http → dropped) → getSession() null → bounce
// to /auth on every load. Mirrors website/assets/supabase-session.js (and
// tortoise/oauth.py) — KEEP IN SYNC (tests/test_cross_subdomain_cookie_sync.py
// asserts helper parity across the adapters).
const isLocal = () => {
  const h = window.location.hostname
  if (h === 'localhost' || h === '127.0.0.1' || h === '::1' || h === '[::1]') return true
  if (h.startsWith('10.') || h.startsWith('192.168.')) return true
  return /^172\.(1[6-9]|2\d|3[01])\./.test(h)
}
const isPremiselabsHost = () => {
  const h = window.location.hostname
  return h === 'premiselabs.co' || h.endsWith('.premiselabs.co')
}
// Domain attribute only on premiselabs.co hosts — host-only cookie elsewhere
// (localhost, *.pages.dev previews) so those origins keep working.
const domainAttr = () => (isPremiselabsHost() && !isLocal() ? '; Domain=' + COOKIE_DOMAIN : '')
// Secure only on non-local origins — localhost/loopback/RFC1918 http would
// reject a Secure cookie.
const secureAttr = () => (isLocal() ? '' : '; Secure')

const supabaseStorage = {
  getItem(key) {
    try {
      // #1860 (P3-3): escape the key — same as the shared bridge's
      // readCookie (website/assets/supabase-session.js). Regex metacharacters
      // in a cookie name (e.g. supabase's `sb-...-auth-token` pattern is
      // benign today, but any `[.*+?^${}()|\]` in a key would silently
      // misparse) must not be treated as regex. Keep in sync with
      // supabase-session.js readCookie.
      const m = document.cookie.match(new RegExp('(?:^|; )' + key.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '=([^;]*)'))
      return m ? decodeURIComponent(m[1]) : null
    } catch { return null }
  },
  setItem(key, value) {
    if (!value) { this.removeItem(key); return }
    let encoded = encodeURIComponent(value)
    // Size guard (#1835, mirrors supabase-session.js): an OAuth session with
    // provider tokens AND user metadata can exceed the 4096-byte cookie limit. provider tokens
    // are only needed by the initiating flow — strip them first; if still
    // over the cap, attempt the write anyway with a warning.
    if (encoded.length > SIZE_GUARD) {
      try {
        const obj = JSON.parse(value)
        delete obj.provider_token
        delete obj.provider_refresh_token
        // Strip large metadata bloat — identities array and user_metadata fields
        // are not needed for auth and can exceed the cookie size cap.
        if (obj.user) {
          delete obj.user.identities
          if (obj.user.user_metadata) {
            // Keep only what the dashboard reads (display_name, avatar_url)
            var keep = {}
            if (obj.user.user_metadata.display_name) keep.display_name = obj.user.user_metadata.display_name
            if (obj.user.user_metadata.avatar_url) keep.avatar_url = obj.user.user_metadata.avatar_url
            if (obj.user.user_metadata.full_name) keep.full_name = obj.user.user_metadata.full_name
            if (obj.user.user_metadata.name) keep.name = obj.user.user_metadata.name
            obj.user.user_metadata = keep
          }
        }
        encoded = encodeURIComponent(JSON.stringify(obj))
      } catch { /* not JSON — leave as-is */ }
      if (encoded.length > SIZE_GUARD + 100) {
        console.warn(`${COOKIE_NAME} session exceeds cookie size cap (${encoded.length} bytes) — session may not bridge subdomains`)
      }
    }
    const expires = new Date(Date.now() + 7 * 24 * 3600 * 1000).toUTCString()
    document.cookie = `${key}=${encoded}${domainAttr()}; Path=/; SameSite=Lax${secureAttr()}; Expires=${expires}`
  },
  removeItem(key) {
    // `=;` + domainAttr() yields `;;` when the Domain attribute is present
    // (premiselabs hosts) — intentional, byte-matches supabase-session.js;
    // the empty cookie-av is ignored per RFC 6265 §5.2.
    document.cookie = `${key}=;${domainAttr()}; Path=/; SameSite=Lax${secureAttr()}; Max-Age=0`
  },
}

// #1909: supabase-js implicit flow returns OAuth error params in the URL
// FRAGMENT (#error=…&error_code=…) — not just the search string (a denied
// claim OAuth round-trip returns as ?claim=1#error=… on this origin). The
// client consumes the fragment during init, so snapshot it FIRST (mirrors
// welcome.html's landingHash) and read error params from BOTH surfaces.
const landingHash = window.location.hash
// #2509: known dashboard tab names for URL↔hash sync (deep-linkability).
const KNOWN_TABS = ['overview', 'keys', 'graphs', 'members', 'billing', 'settings', 'profile']
function oauthErrorParams() {
  const p = new URLSearchParams(window.location.search)
  const h = new URLSearchParams(landingHash.replace(/^#/, ''))
  const get = (k) => p.get(k) || h.get(k) || ''
  return { error: get('error'), error_code: get('error_code'), error_description: get('error_description') }
}
// #1909: the /auth bounce may carry an OAuth error FRAGMENT (#error=…).
// Forward it to /auth ONLY when it holds error params — a live token
// fragment (access_token / refresh_token / code) must never be re-ingested
// by the destination (the #1566 invariant).
function oauthErrorHash() {
  if (!landingHash) return ''
  if (/[?&#](?:access_token|refresh_token|code)=/.test(landingHash)) return ''
  return /[?&#](?:error|error_code|error_description)=/.test(landingHash) ? landingHash : ''
}

let supabaseClient = null
try {
  supabaseClient = window.supabase.createClient(SUPABASE_URL, SUPABASE_ANON_KEY, {
    auth: {
      flowType: 'implicit',  // #1566: cross-origin OAuth returns from /auth
      // carry #access_token (a pkce verifier cannot cross subdomains); the
      // claim flow's raw key still rides sessionStorage only (#1082).
      storage: supabaseStorage,
      storageKey: COOKIE_NAME,
      persistSession: true,
      autoRefreshToken: true,
      detectSessionInUrl: true,
    },
  })
} catch (e) {
  console.warn('Supabase client init failed:', e)
}

// #1719 (Task 6): humanize an API error detail body. Server failures carry
// dict details ({"error_code": ..., "message": ...}) — render the message,
// never raw JSON (JSON.stringify of the dict). 5xx → the unified
// unavailable copy (contract with signup.html + hosted_api's
// _control_plane_unavailable()).
const UNAVAILABLE_COPY = 'Sign-in is temporarily unavailable — try again in a moment.'
function apiErrorText(status, b) {
  // #1738: a server-provided STRING detail wins BEFORE the >=500 blanket —
  // /v1/signup/email returns the signup-flavored "Signup service temporarily
  // unavailable — try again in a moment." on 5xx, so a signup failure shows
  // signup copy while sign-in surfaces keep the unified copy (503 dict
  // details → message field, or no detail → blanket below, unchanged).
  if (b && b.detail && typeof b.detail === 'string') return b.detail
  if (status >= 500) return UNAVAILABLE_COPY
  if (b && b.detail) {
    const d = b.detail
    if (typeof d === 'object' && d !== null) {
      if (typeof d.message === 'string' && d.message) return d.message
      return JSON.stringify(d)
    }
  }
  return null
}

// #1841: skeleton shimmer slots for the overview cards — one shared inline
// style per slot size (value ≈ number height, label ≈ 13px caption). Spans
// carry aria-hidden in JSX; the visible progress lives in the role=status cue.
// #1842 P2-2 (CLS): heights are 1.2em/1.2em so the skeleton BOX matches the
// real line box exactly — .card-val line-height 1.2 × 28px = 33.6px and
// .card-label line-height 1.2 × 13px = 15.6px (the old 1.4em/0.9em rendered
// 39.2px/11.7px, so each skeleton→text swap jittered the card height).
const SKEL_VALUE = { width: '60%', height: '1.2em' }
const SKEL_LABEL = { width: '45%', height: '1.2em' }

// #2755: the prompt card is a PLAIN REGION with exactly ONE explicit copy
// control. The #2698 build wrapped the whole card in a `role="button"`
// container holding two real `<button>`s — an interactive element nested in an
// interactive element (WCAG 4.1.2 / 1.3.1: an AT announces a button containing
// buttons, and the container's aria-label competed with the children's names),
// and because the container was the click target, a drag-select inside the card
// fired a copy on mouse-up and silently overwrote the user's clipboard.
//
// Chosen resolution (issue's option b): demote the container, keep one
// control. Rationale: (a) it satisfies BOTH required conditions outright —
// nothing interactive is nested (the single button is the only control) and
// there is no container click handler left to fire on a drag-select, so no
// selection guard is needed; (b) it removes a duplicate tab stop and the
// competing accessible names the three-way design created; (c) the surviving
// control is the primary bottom button (largest target, matches the Copy
// button convention used by the build fork and the key rows). The three-way
// intent is deliberately NOT preserved: keeping "click anywhere copies" would
// require the container to stay a control, which is the violation itself.
function WizardPromptCard({ text, label }) {
  const [copied, setCopied] = React.useState(false)
  // #2912 (PR-gate a11y): the scroll region must have a UNIQUE accessible name
  // per card — the 2-card surfaces (Pi, Cursor) render two `role="region"`
  // landmarks, and a shared "Setup prompt" name made them
  // indistinguishable to a screen-reader user navigating by landmark
  // (axe `landmark-unique`). Derive it from the button's own label, which is
  // already per-card ("Copy step 1 prompt" → "step 1 prompt").
  const regionLabel = label ? label.replace(/^Copy\s+/i, '') : 'Setup prompt'
  const doCopy = React.useCallback(() => {
    navigator.clipboard.writeText(text)
    setCopied(true)
    setTimeout(() => setCopied(false), 1600)
  }, [text])
  return (
    <div className="wizard-prompt-card">
      {/* #2912 (review cycle 2): the long prompts scroll, so the <pre> is a
          scroll REGION — it must be reachable by keyboard (WCAG 2.1.1 /
          axe `scrollable-region-focusable`). The copy control stays OUTSIDE
          the scroll container so it never scrolls away. */}
      <pre className="wizard-prompt-text" tabIndex={0} role="region" aria-label={regionLabel}>{text}</pre>
      {/* #2827 (round-2 P2): the button's own label flips to 'Copied ✓' for
          1.6s — a sighted-only signal. This live region announces the copy
          outcome to screen readers. */}
      <span className="sr-only" role="status" aria-live="polite">{copied ? 'Copied to clipboard' : ''}</span>
      <div className="wizard-prompt-actions">
        <button type="button" className={copied ? 'ghost small' : 'btn-primary small'}
          onClick={doCopy}>
          {copied ? 'Copied ✓' : (label || 'Copy')}
        </button>
      </div>
    </div>
  )
}

// #2912: one numbered block of the connect step ("1 Get your API key" →
// "2 Set up Claude Code"). Before this the step was a flat run of captions,
// cards and buttons with ad-hoc margins, so nothing communicated ORDER.
// The block owns its own spacing + rule; the step body just stacks blocks.
function WizardBlock({ step, title, children }) {
  return (
    <section className="wizard-block">
      <h2 className="wizard-block-title">
        {step != null && <span className="wizard-block-step" aria-hidden="true">{step}</span>}
        {title}
      </h2>
      <div className="wizard-block-body">{children}</div>
    </section>
  )
}

function wizardPromptText(harness, step, key, mode) {
  const url = 'https://api.premiselabs.co/mcp/'
  const docs = 'Docs: https://tortoise.premiselabs.co/docs'
  const keyLine = mode === 'included' ? `Key: ${key}` : 'I\'ll give you the API key when you need it.'
  const twoStepNote = 'Tell me when to restart'
  const step2Text = `Call tortoise_health to verify the connection, then tortoise_create_point to file my first point.\n${docs}`

  // #2827: every body starts at its first actionable instruction. The step
  // heading the user reads ("Give this prompt…", "Restart X…") is the JSX
  // caption above the card — the SINGLE place that sentence may appear.
  if (harness === 'pi') {
    if (step === 1) return `Add Tortoise MCP at ${url}.\n${keyLine}\nSave it to my shell profile (~/.zshrc).\n${twoStepNote} Pi.\nThen install the Tortoise skills (how-to-use-tortoise, tortoise-decide, tortoise-file-finding + tortoise-onboarding) from ${SKILLS_INSTALL_URL}.\n${docs}`
    if (step === 2) return step2Text
  }
  if (harness === 'cursor') {
    if (step === 1) return `Add Tortoise MCP at ${url}.\n${keyLine}\nSave it to my shell profile (export TORTOISE_API_KEY=…) so Cursor can read it from its env.\n${twoStepNote} Cursor.\nThen install the Tortoise skills (how-to-use-tortoise, tortoise-decide, tortoise-file-finding + tortoise-onboarding) from ${SKILLS_INSTALL_URL}.\n${docs}`
    if (step === 2) return step2Text
  }
  if (harness === 'claude') {
    return `Add Tortoise MCP at ${url}.\n${keyLine}\nThen install the Tortoise skills (how-to-use-tortoise, tortoise-decide, tortoise-file-finding + tortoise-onboarding) from ${SKILLS_INSTALL_URL}.\nThen call tortoise_health and tortoise_create_point to file my first point.\n${docs}`
  }
  if (harness === 'codex') {
    return `Add Tortoise MCP at ${url}.\n${keyLine}\nSave it to my shell profile (export TORTOISE_API_KEY=…).\nThen install the Tortoise skills (how-to-use-tortoise, tortoise-decide, tortoise-file-finding + tortoise-onboarding) from ${SKILLS_INSTALL_URL}.\nThen call tortoise_health and tortoise_create_point to file my first point.\n${docs}`
  }
  // #2827: both filesystem-less harnesses (Claude Desktop/Web) need only the
  // verify/file step in the conversation; the workflows body rides
  // wizardWorkflowsText below.
  if (harness === 'claude-desktop' || harness === 'claude-web') {
    if (step === 2) return step2Text
  }
  return ''
}

// #2827: the skills-as-prompt body a filesystem-less harness needs (Claude
// Desktop and Claude Web keep no local skills, so the Tortoise workflows have
// to arrive in the conversation). It ends with the same verify/file step every
// other tab gets — without it a Claude Desktop/Web user never calls
// tortoise_health/tortoise_create_point, so onboarding never auto-completes.
// Rendered via WizardPromptCard so it is COPYABLE (it used to be a bare <pre>
// with no copy affordance).
function wizardWorkflowsText(key, mode) {
  return `${WORKFLOWS_PROMPT}\n\n${wizardPromptText('claude-web', 2, key, mode)}`
}

function App() {
  // #1280 (P0, mirrored from fix/1280): banner state MUST live inside the
  // component — a module-top-level useState crashes the whole bundle.
  const [banner, setBanner] = React.useState('')
  // #1765: identity inventory (login methods + banner) — session-gated
  const [identityInv, setIdentityInv] = React.useState(null)
  const [identityLoading, setIdentityLoading] = React.useState(false)
  const [identityError, setIdentityError] = React.useState('')
  const [profileBusy, setProfileBusy] = React.useState('') // '' | 'oauth' | 'email' | 'unlink' | 'resend'
  const [profileError, setProfileError] = React.useState('')
  const [reauthOpen, setReauthOpen] = React.useState(false)
  const [reauthBusy, setReauthBusy] = React.useState(false)
  const [reauthError, setReauthError] = React.useState('')
  const [reauthPasswordMode, setReauthPasswordMode] = React.useState(false)
  const [recoveryDismissed, setRecoveryDismissed] = React.useState(() => {
    try { return localStorage.getItem('tt_recovery_dismissed') === '1' } catch { return false }
  })
  // pending action resumed after a re-auth round (change-email gate, #1765)
  const pendingReauthRef = React.useRef(null)
  // #2479: re-auth attempt counter (max 1 per session for password re-auth;
  // OAuth round-trips naturally reset via full page navigation)
  const reauthAttemptRef = React.useRef(0)
  // #2479 code-review fix P1: tracks that we're re-executing a pending action
  // after successful re-auth (prevents infinite loop if the API returns 403 again)
  const reauthRetriedRef = React.useRef(false)
  // #2392 (a11y): focus-restore holder for ReauthDialog — the trigger
  // (add-email submit / unlink button) is captured at the gesture start;
  // on close the dialog hands focus back instead of dropping it on <body>.
  const reauthRestoreRef = React.useRef(null)
  // #2392 (a11y): every reauth close path (Escape/backdrop/✕ from the
  // dialog, password success, MAX-attempts bail, provider round-trip with no
  // pending action) restores focus to the trigger. Opens capture separately
  // at each site (see handleAddEmail / handleUnlink) because the trigger may
  // be disabled mid-flight by the time the dialog actually opens.
  function closeReauth() {
    setReauthOpen(false)
    setProfileBusy('')
    // #2392 a11y: defer restoreFocus to after React commits the busy-clearing
    // update — a disabled trigger button is not focusable. Calling
    // synchronously would run against the pre-render DOM where the trigger is
    // still disabled from profileBusy='reauth'. The rAF fires after React
    // reconciles, re-enables the button, and the painted DOM is focusable.
    // (The ref holds a JS reference to the element — it survives the deferred
    // callback: the element isn't GC'd until the ref is cleared.)
    requestAnimationFrame(() => restoreFocus(reauthRestoreRef))
  }
  // #1765 review P1: the pre-reauth session user id (verify the provider
  // round-trip didn't switch accounts before resuming the pending action)
  const beforeUidRef = React.useRef(null)
  // #1765 review-fix: flips true once the session has been loaded (the link-
  // flow commit effect must NOT run before sessionTokenRef is populated —
  // it would silently never fire on the OAuth return)
  const [sessionBooted, setSessionBooted] = React.useState(false)
  const lastIdentityFetchRef = React.useRef(0)
  // #1148-ux: last auth method (login card "Last used" pills). The state
  // reads the legacy app-origin key; the shared helper (called on mount
  // below) performs the one-time migration to the parent-domain cookie so
  // the pill shows on /auth even before the user signs in again (#1511).
  const [lastAuthMethod, setLastAuthMethod] = React.useState(() => {
    try { return window.getLastAuthMethod ? window.getLastAuthMethod() : (localStorage.getItem(LAST_AUTH_METHOD) || '') } catch { return '' }
  })
  // #2246 (ADR-010): the claim-paste input value IS the apiKey state — the
  // ANON (authMode 'apikey') claim screen's paste box, kept in lockstep with
  // the ref so claimSignIn/claimEmailPassword use exactly what's on screen.
  // Session mode never holds a key: the initializer no longer reads the
  // localStorage slot (no residue boot), and the session mount clears the
  // state before the chrome renders.
  const [apiKey, setApiKey] = React.useState('')
  // #1511 (code-review r2, P2): the claim-paste input value IS the apiKey
  // state — keep the ref (read by claimSignIn/claimEmailPassword) in lockstep
  // so the credential used matches what's on screen (no pre-fill mismatch).
  React.useEffect(() => { apiKeyRef.current = apiKey }, [apiKey])
  // #1148-ux review: combined login/signup card
  const [authIsSignup, setAuthIsSignup] = React.useState(false)
  const [authEmail, setAuthEmail] = React.useState('')
  const [authPassword, setAuthPassword] = React.useState('')
  const [authBusy, setAuthBusy] = React.useState(false)
  
// #1511 (code-review P1): claim-intent is IN-FLIGHT ONLY — either the
// ?claim=1 route (the ANON funnel lands here before the key is pasted) or
// a claim key accompanied by the 1h tt_claim_pending marker (an OAuth
// claim in flight). A BARE stale claim key or a BARE stale marker must
// not pin a sessionless user on the claim screen, nor misroute a
// signed-in user to the claim route.
function claimIntentInFlight() {
  const claimKey = (() => { try { return sessionStorage.getItem(CLAIM_KEY_STORAGE) || '' } catch { return '' } })()
  const claimPending = /(?:^|; )tt_claim_pending=/.test(document.cookie)
  const claimParam = new URLSearchParams(window.location.search).get('claim') === '1'
  return claimParam || (!!claimKey && claimPending)
}

  const [authed, setAuthed] = React.useState(false)
  const [authUnavailable, setAuthUnavailable] = React.useState('')
  // #1559: a session-resolution / mount or team-load failure (e.g. 429 rate
  // limit, 5xx, suspension) must surface an actionable error — never the
  // silent "Redirecting to the sign-in page…" shell (which does NOT redirect
  // and left users stuck).
  const [mountError, setMountError] = React.useState('')
  const [team, setTeam] = React.useState(null)
  const [keys, setKeys] = React.useState([])
  const [sessions, setSessions] = React.useState([])
  const [error, setError] = React.useState('')
  const [busy, setBusy] = React.useState(false)
  const [newKey, setNewKey] = React.useState(null)
  const [newKeyName, setNewKeyName] = React.useState('') // key-label: label for the next minted key
  // #2426: expiry choice for the next minted key (presets 30d default /
  // 60d / 90d / 1y / Custom date / Never). Sent as expires_in days; Never
  // sends NO expiry param (absent = never, legacy mint shape preserved).
  const [newKeyExpiryPreset, setNewKeyExpiryPreset] = React.useState('30')
  const [newKeyExpiryDate, setNewKeyExpiryDate] = React.useState('')
  // #2426: the show-once card's authoritative server echo of the minted
  // key's expiry (ISO string, or null = Never). Cleared wherever newKey is.
  const [newKeyExpiresAt, setNewKeyExpiresAt] = React.useState(null)
  // #2735: rotate's one-time replacement reveal — {plaintext, expiresAt}.
  // DELIBERATELY separate state from newKey/newKeyExpiresAt (the create
  // modal's key): opening and cancelling the create modal must never destroy
  // an unread rotate replacement whose old key is already revoked (#2392
  // class "a click must not destroy the one-time secret"). Cleared on the
  // reveal's own Copy & done and on logout/team switch.
  const [rotatedKey, setRotatedKey] = React.useState(null)
  // key-create modal state
  const [keyModalOpen, setKeyModalOpen] = React.useState(false)
  const [keyModalBusy, setKeyModalBusy] = React.useState(false)
  const [keyModalStage, setKeyModalStage] = React.useState('form') // 'form' | 'done'
  // key-label: inline-rename state (which row is being edited + its draft text)
  const [editingKeyId, setEditingKeyId] = React.useState(null)
  const [editingKeyName, setEditingKeyName] = React.useState('')
  const renameCancelRef = React.useRef(false) // key-label: Escape-in-edit suppresses the blur-save
  const [capNotice, setCapNotice] = React.useState('') // #1147: tier-cap upgrade prompt (keys tab)
  // #1287: welcome-as-dashboard-subpage — first-time users land on
  // /welcome (key reveal + MCP/SDK chooser); returning users get home.
  const [welcomeMode, setWelcomeMode] = React.useState(
    () => window.location.pathname === '/welcome' || window.location.pathname === '/welcome/'
  )
  // #1566: in-app provisioning (first-timers) — the reveal is atomic (A13),
  // so the key is displayed here and never elsewhere.
  const [welcomeProvisioning, setWelcomeProvisioning] = React.useState(false)

  const [welcomeKey, setWelcomeKey] = React.useState('')

  // #1591: the first-data snippet (graph-missing card) — full command with
  // the user's own key (their dashboard, their key; copy-able).
  // #2246 (ADR-010): session mode holds no apiKey — the snippet's key source
  // is (welcomeKey || apiKey): the first-timer's in-memory shown-once reveal,
  // or the anon/key-hold carve-out's apiKey state. Never a localStorage read.
  const snippetKey = welcomeKey || apiKey
  const firstDataSnippet = `curl -X POST https://api.premiselabs.co/v1/points \
  -H "Authorization: Bearer ${snippetKey}" \
  -H "Content-Type: application/json" \
  -d '{"content":"hello graph","kind":"statement"}'`
  const [welcomeTeamName, setWelcomeTeamName] = React.useState('')
  const [welcomeGraphName, setWelcomeGraphName] = React.useState('')
  const [welcomeProvisionError, setWelcomeProvisionError] = React.useState('')
  // #2323 (Option B): name-first first-run — the org is provisioned on the
  // org-create step submit (never at mount). welcomeTeamReady marks the
  // just-created org on the first-run path; sessionRef keeps the mount
  // session so the step-1 submit can provision.
  const [welcomeTeamReady, setWelcomeTeamReady] = React.useState(false)
  const sessionRef = React.useRef(null)
  // #1643/#1692: the getting-started wizard (post-key steps: harness →
  // integrations → skills → seed → done). For first-timers it follows the
  // key reveal; for returning empty-graph users it re-opens at step 0
  // (harness); step-0 Back returns to the orientation card.
  const [wizardStep, setWizardStepRaw] = React.useState(0)
  const setWizardStep = React.useCallback((n) => { setWizardStepRaw(n); setWizardCopied((c) => (c === 'harness' ? '' : c)); setWizardCopyFailed(false); setWizardConnectError('') }, [])
  const [wizardHarness, setWizardHarness] = React.useState('claude')

  // Wizard connect step: reset persisted 'chatgpt' value (legacy default) to a valid tab
  React.useEffect(() => {
    // #2912: 'codexDesktop' is a first-class leaf now (the Codex chooser's
    // Desktop surface), so it is a valid persisted value too.
    if (!['pi', 'cursor', 'claude', 'codex', 'codexDesktop', 'claude-desktop', 'claude-web'].includes(wizardHarness)) {
      setWizardHarness('pi')
    }
  }, [])

  // ⛔ #2710 — the connect step's auto-open effect was DELETED here. It ran
  // `setKeyModalOpen(true)` on arrival at the connect step when the user had
  // no in-memory key. During the wizard that was an invisible no-op: the
  // shared key-create modal's JSX lives only in the post-welcome dashboard
  // return tree, so the flag sat queued and then popped a stray
  // "Create new API key" modal — with the API Keys tab's 30-day expiry
  // default — the instant the user exited the wizard. The connect step now
  // owns its key affordance inline (see wizardNoKeyAffordance below: mint
  // CTA + paste row, mint always No-expiration) and never touches the shared
  // modal, so `keyModalOpen` cannot leak past the wizard. Deleting the effect
  // also retires the #2426/#2621/#2709 TDZ hazard it carried (its deps array
  // could not safely list the later-declared harnessKey). The
  // tdzDepsTripwire analyzer still guards the whole file against that class.
  const [wizardCopied, setWizardCopied] = React.useState('')
  // #1701 R2: a failed clipboard write must not strand the ChatGPT flow — the
  // error prescribes a manual ⌘/Ctrl-C copy, so the manual-Continue affordance
  // appears ONLY after a failure (the user explicitly asserts the copy).
  const [wizardCopyFailed, setWizardCopyFailed] = React.useState(false)
  // #2328/#2912: Codex has two surfaces — CLI (shell) and Desktop (GUI app,
  // NO terminal, does not inherit shell exports). Since #2912 the SURFACE is
  // the leaf `wizardHarness` value ('codex' vs 'codexDesktop') chosen by the
  // two-level harness chooser, so the old parallel `wizardCodexDesktop`
  // boolean (a second place a surface choice could get stuck) is gone.
  const [wizardGithub, setWizardGithub] = React.useState({ connected: false, repos: null, busy: false, org: null })
  const wizardGithubPollRef = React.useRef(null)  // #1643 review P1: the status poll handle (hoisted so Cancel/unmount can stop it)
  // #1728 Slice 3: the Memory-sources surface (wizard step-1 + Overview
  // panel share ONE implementation). Full onboarding state drives the three
  // toggles (issues / docs / sessions).
  const [onboarding, setOnboarding] = React.useState(null)
  const [onboardingLoading, setOnboardingLoading] = React.useState(true)
  const [issuesWantOn, setIssuesWantOn] = React.useState(false)  // on-but-not-connected (inline Connect shown)
  const [docsWantOn, setDocsWantOn] = React.useState(false)      // docs toggle-on reveals the Index-docs action
  const [indexJob, setIndexJob] = React.useState(null)           // github re-poll job status (bounded poll)
  const [docsJob, setDocsJob] = React.useState(null)             // docs job status (bounded poll)
  const [indexBusy, setIndexBusy] = React.useState(false)
  const [docsBusy, setDocsBusy] = React.useState(false)
  const [memoryBusy, setMemoryBusy] = React.useState('')          // 'issues' | 'docs' | 'sessions' | ''
  const [memoryErrors, setMemoryErrors] = React.useState({})      // per-ROW errors (role=alert) — never the global banner
  const indexPollRef = React.useRef(null)
  const docsPollRef = React.useRef(null)
  // #1845: source-scope selector state (shared by the docs + issues rows).
  // reposList = SHORT repo names from GET /v1/onboarding/github/repos (loaded
  // once when connected); branchLists[repo] = branches for a repo (lazy-loaded
  // from GET /v1/onboarding/github/branches when the repo is selected).
  // docsScope/issuesScope carry the per-row selection: `repos: []` = "All
  // repos"; a non-empty list = exactly those repos. docsScope.branches[repo]
  // is the per-repo branch choice ('' = default main/master fallback,
  // 'all' = every branch, else a branch name).
  const [reposList, setReposList] = React.useState([])
  const [reposLoaded, setReposLoaded] = React.useState(false)
  const [branchLists, setBranchLists] = React.useState({})
  const [docsScope, setDocsScope] = React.useState({ repos: [], branches: {} })
  const [issuesScope, setIssuesScope] = React.useState({ repos: [] })
  // #1893: persist source-scope selections as allowlisted onboarding_state
  // keys (github_issues_scope / github_docs_scope). scopeReadyRef gates the
  // persist path — nothing persists until the initial GET resolves + the
  // one-shot hydration below has seeded. hydratedTeamIdRef keys hydration
  // to ONE pass per team session: refreshOnboarding() re-fires after every
  // reindex/docs run + finishWelcomeLoads, so seeding inside it would
  // clobber newer selections with the stale server value.
  const hydratedTeamIdRef = React.useRef(null)
  const scopeReadyRef = React.useRef(false)
  // #1893 (code-review P1): onboarding state is TEAM-scoped — during a team
  // switch the `onboarding` object is momentarily the PREVIOUS team's. This
  // stale flag (set by switchTeam, cleared when refreshOnboarding resolves)
  // blocks the scope hydration effect until the new team's state lands, so
  // the new team never seeds from (or persists over) the old team's scope.
  const onboardingStaleRef = React.useRef(false)
  // #1893 (scope-verify P1): PER-KEY pre-hydration touch tracking — a touch
  // on ONE key must never suppress seeding of the OTHER, and must never
  // persist the other key's un-seeded default over its stored server value.
  const scopeTouchedRef = React.useRef({ issues: false, docs: false })
  // #1893: a failed repos fetch must never be treated as an empty org —
  // hydration (and therefore pruning) is skipped and NOT latched, so a
  // reload re-attempts; nothing gets clobbered server-side.
  const [reposLoadFailed, setReposLoadFailed] = React.useState(false)
  // #1893: _update_onboarding_state is a WHOLE-STATE read-modify-write
  // (non-atomic) — issues + docs PATCHes must serialize against each other,
  // so a SINGLE shared FIFO queue (per-key queues would still race cross-key).
  // NOTE: this serializes DASHBOARD writers only — server-side writers (the
  // index job's cursor updates) run their own RMW and can still interleave
  // (pre-existing infra limitation, #1827; not introduced by this PR).
  const scopePersistQueueRef = React.useRef(Promise.resolve())

  // #1893 (code-review P1): ALL onboarding-state / github / index calls pin
  // the SELECTED team (the server defaults to memberships[0] without it) — a
  // multi-membership user must read/write the team the scope surface shows.
  // teamIdRef mirrors currentTeamId but is set synchronously in switchTeam
  // (no render closure race). ONE helper for every call site keeps the
  // surface consistent — a partially-pinned surface makes toggles write one
  // team and read another (review P1: toggleSessionRecording wrote
  // memberships[0] while its follow-up refreshOnboarding read the selected
  // team). Only called post-render (handlers/effects), so the teamIdRef
  // binding below is always initialized.
  function onboardingTeamQ(sep = '?') {
    return teamIdRef.current ? `${sep}team_id=${encodeURIComponent(teamIdRef.current)}` : ''
  }

  function persistScope(payload) {
    // fire-and-forget: a failed persist never blocks the UI; the next
    // change re-persists the full list.
    // #1893 (code-review P1): capture the team at CALL time — the queue
    // drains asynchronously, and a switchTeam before flush must NOT retarget
    // a queued PATCH (built with team A's scope) at team B. Evaluated here,
    // before the .then(), so team+payload stay paired.
    const _teamQ = onboardingTeamQ()
    scopePersistQueueRef.current = scopePersistQueueRef.current
      .then(() => api(`/v1/onboarding/state${_teamQ}`, { method: 'PATCH', useSession: true,
        body: JSON.stringify(payload) }))
      .catch(() => {})
  }

  // scope-verify P2: mirror the latest selection in refs so the hydration
  // effect's touched-branch never serializes a STALE closure value (the
  // effect deps deliberately omit issuesScope/docsScope; the refs make the
  // persist ordering-independent of React passive-effect flush timing).
  const issuesScopeRef = React.useRef({ repos: [] })
  const docsScopeRef = React.useRef({ repos: [], branches: {} })

  function handleIssuesScopeChange(next) {
    // #1893: NO DEBOUNCE by design — the persist fires synchronously on
    // every change so a logout in the REAUTH window (<400ms after a
    // toggle) can never lose the last selection (the observed production
    // incident). Timing is untestable at the pure-node layer; covered by
    // manual clickthrough (documented in the PR body).
    // #1893 (code-review P2): value-diff guard — a same-value no-op toggle
    // (e.g. re-clicking the already-checked "All repos" row while the repos
    // list is still loading) must NOT mark the key touched: the hydration
    // touched-branch would then persist the un-seeded default over the
    // stored selection, silently clobbering it.
    if (JSON.stringify(serializeIssuesScope(next)) ===
        JSON.stringify(serializeIssuesScope(issuesScopeRef.current))) return
    scopeTouchedRef.current.issues = true
    issuesScopeRef.current = next
    setIssuesScope(next)
    if (shouldPersist(scopeReadyRef.current)) persistScope({ github_issues_scope: serializeIssuesScope(next) })
  }

  function handleDocsScopeChange(next) {
    // #1893 (code-review P2): value-diff guard — see handleIssuesScopeChange.
    if (JSON.stringify(serializeDocsScope(next)) ===
        JSON.stringify(serializeDocsScope(docsScopeRef.current))) return
    scopeTouchedRef.current.docs = true
    docsScopeRef.current = next
    setDocsScope(next)
    if (shouldPersist(scopeReadyRef.current)) persistScope({ github_docs_scope: serializeDocsScope(next) })
  }
  const [wizardSeedDone, setWizardSeedDone] = React.useState(false)
  const [wizardSeeding, setWizardSeeding] = React.useState(false)
  // #2361 review-r1/r3 (Bug-1): pending invites were only fetched when the
  // account menu opened — a resumer in welcome mode never saw an invitation
  // on the org-create step. Fetch on welcome entry; no once-per-session latch
  // (a failed fetch retries on the next welcome entry, and a mid-session
  // invite appears without reopening the account menu).
  React.useEffect(() => {
    // #2361 review-r2 (P1): sessionRef is only set for teamless first-timers
    // — org-holders resuming welcome never fetched. sessionTokenRef holds the
    // JWT for every authed account. Re-fetch on each welcome entry so a
    // mid-session invite shows up; no once-per-session latch (a failed fetch
    // must retry, not persist a broken row).
    if (welcomeMode && authed && sessionTokenRef.current) {
      loadPendingInvites().catch(() => {})
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [welcomeMode, authed])

  // #1907: seed-step failure must surface INLINE (the global error banner
  // only renders post-welcome) — the message lives here and the retry is the
  // re-enabled 'Seed my graph' button. Cleared on every attempt.
  const [wizardSeedError, setWizardSeedError] = React.useState('')
  const [wizardDone, setWizardDone] = React.useState(false)
  // #2361 review-r2 (P2): done-step honesty — a user who SKIPPED the connect
  // step is 'set up but not connected'; track it so the done step doesn't
  // claim an agent is taking over.
  const [wizardPaused, setWizardPaused] = React.useState(false)
  const wizardCardRef = React.useRef(null)
  // #2361 review-r4 (P2): connectedOnceRef is session-local — a re-opener
  // whose org ALREADY connected (server checkpoint) must not see the paused
  // 'not connected yet' copy when they skip. Read the projection the client
  // already holds; refreshOnboarding at wizard-open + step-4 keeps it fresh.
  // ⚠️ MUST be declared BEFORE effectivelyPaused (TDZ fix, #2621):
  const connectedOnceRef = React.useRef(false)
  const serverHarnessConnected = Array.isArray(onboarding && onboarding.completed_steps) &&
    onboarding.completed_steps.includes('harness-connected')
  const effectivelyPaused = wizardPaused && !connectedOnceRef.current && !serverHarnessConnected
  const wizardFocusInit = React.useRef(false)
  const lastWizardStepRef = React.useRef(-1)  // #2361 r4: focus only on step change
  const onboardingRefreshedAtDoneRef = React.useRef(false)
  // #2361 r4: refresh the server projection when the user lands on the done
  // step (a re-opener may have connected in a prior session) so the paused
  // gate reads fresh server truth.
  React.useEffect(() => {
    if (welcomeMode && authed && wizardStep === 3 && !onboardingRefreshedAtDoneRef.current) {
      onboardingRefreshedAtDoneRef.current = true
      refreshOnboarding().catch(() => {})
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wizardStep, welcomeMode, authed])
  // #2361 review-r3 (P2-2): wizardPaused must not out-live a real connection —
  // connect → Back → Skip must still show 'connected', not 'paused'.
  // (connectedOnceRef declaration moved up for TDZ fix, #2621)
  const [wizardStepAnnounce, setWizardStepAnnounce] = React.useState('')

  // #2361 review-r2 (a11y P1): announce + move focus on wizard step change.
  // Step 1 (org input) and step 3 (paste field / copy button) carry their own
  // autofocus targets — the container must not steal from them, so those two
  // steps only announce.
  React.useEffect(() => {
    if (!(welcomeMode && authed)) return
    if (LEGACY_WIZARD_ARCHIVED) return  // A0 rollback owns its own steps (#2361 r3 P3-7)
    const label = wizardStageLabel(wizardStep, { hasOrg: welcomeHasOrg, paused: effectivelyPaused })
    setWizardStepAnnounce(`Step ${wizardStep + 1} of 4: ${label}`)
    if (!wizardFocusInit.current) { wizardFocusInit.current = true; return }
    // #2361 review-r4 (P3): focus ONLY on step changes — toggling the paste
    // disclosure (wizardShowPaste) or an invite accept (welcomeHasOrg) must
    // not yank focus from the control the user just activated.
    const stepChanged = lastWizardStepRef.current !== wizardStep
    lastWizardStepRef.current = wizardStep
    if (!stepChanged) return
    // Skip container focus only when a child control autofocuses on mount:
    // step 1 org input (no org yet). The paste escape is auto-rendered now
    // (no disclosure toggle) so no autofocus guard is needed for it.
    if (wizardStep === 0 && !welcomeHasOrg) return
    if (wizardCardRef.current) wizardCardRef.current.focus()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wizardStep, welcomeMode, authed, wizardPaused])
  // ⛔ #2426 e2e catch (P0): welcomeHasOrg (~line 4890) and wizardShowPaste
  // (~1832) are declared LATER in this giant component — a hook dep array
  // evaluates eagerly DURING render, so listing either here threw "Cannot
  // access … before initialization" on every authenticated render and
  // blanked the whole dashboard on main (introduced when 38498ab4 added
  // both to this effect's deps). The effect BODY is a closure that runs
  // post-render (safe to read them there). Semantics of dropping them:
  // toggling the paste disclosure on the SAME step now does not re-run the
  // effect, which is exactly the "must not yank focus on disclosure toggle"
  // intent — the stepChanged guard already no-ops it.
  const [onboardingComplete, setOnboardingComplete] = React.useState(false)
  const [welcomeOriented, setWelcomeOriented] = React.useState(false)
  const [wizardSubject, setWizardSubject] = React.useState('')
  const [copiedStep, setCopiedStep] = React.useState('')
  function wizardCopyStep(text) {
    try { navigator.clipboard.writeText(text) } catch { /* clipboard blocked */ }
    setCopiedStep(text)
    setTimeout(() => { if (mountedRef.current) setCopiedStep('') }, 1600)  // review: mounted-guard the flash timer (setState after unmount)
  }
  const [wizardProject, setWizardProject] = React.useState('')
  const mountedRef = React.useRef(true)  // review: flash-timer guard — flipped false on unmount so late setState is skipped
  React.useEffect(() => () => { mountedRef.current = false; stopGithubPoll && stopGithubPoll(); stopBoundedPoll(indexPollRef); stopBoundedPoll(docsPollRef) }, [])  // unmount cleanup

  // #1147: build the tier-cap notice. The server's 402 detail carries the
  // real limit ('Team api_keys limit reached (N). Upgrade your plan to
  // increase it.') — /v1/team does NOT return max_api_keys, so parse it
  // instead of trusting a client-side hardcode.
  function upgradeNoticeFrom(message, team_) {
    const m = String(message || '').match(/limit reached \((\d+)\)/)
    const limit = m ? m[1] : (team_?.max_api_keys ?? '2')
    return `You've reached your plan's limit of ${limit} API keys. Upgrade to add more — or regenerate an existing key instead.`
  }
  // #2229: rotate-path cap notice. Rotate mints the REPLACEMENT before
  // revoking the old key, so a team AT max_api_keys 402s on the mint leg —
  // the generic notice's "regenerate instead" tail would loop here
  // (regenerating needs the same free slot). Truthful escape: revoke an
  // unused key first (non-held rows have trash) or upgrade.
  function rotateCapNoticeFrom(message, team_) {
    const m = String(message || '').match(/limit reached \((\d+)\)/)
    const limit = m ? m[1] : (team_?.max_api_keys ?? '2')
    return `You're at your plan's limit of ${limit} API keys. Rotating creates the replacement before revoking this one, so revoke an unused key first — or upgrade to add more.`
  }

  // #1147: shared mint — POST /v1/team/keys and return the plaintext key.
  // `name` (optional) is the key label — sent only when non-empty.
  // #2426: optional `expiresInDays` (1-366) rides the body as expires_in
  // (never the expires_at param — the dashboard path is days-only); null /
  // undefined = No expiration (Never) → the body stays the legacy shape so
  // the byte-identical pre-#2426 response (no expires_at key) is preserved.
  // Returns the full response OBJECT {id, key, key_prefix, created_at, name,
  // expires_at?} — callers read .key for the shown-once plaintext and
  // .expires_at (when present) for the expiry echo.
  async function mintKey(activeKey, name, expiresInDays) {
    // #2167 rule 4: session-mode durable-key CREATE (shared by createKey +
    // #2211's wizardMintDurableKey + regenerateKey's rotate mint) rides the
    // session JWT + pins ?team_id=<selected> (multi-membership correctness —
    // server honors it membership-checked with a suspension 403, zero server
    // changes) and NEVER merges a key-preference header (a held key must not
    // shadow the session). #2246 (ADR-010): every mintKey call site passes ''
    // for activeKey — no key-mode/claim caller exists (that surface never
    // calls api()); the activeKey param is vestigial (kept for signature
    // stability).
    const q = (sessionTokenRef.current && currentTeamId) ? `?team_id=${encodeURIComponent(currentTeamId)}` : ''
    const payload = {}
    if (name) payload.name = name
    if (expiresInDays != null && !Number.isNaN(expiresInDays)) payload.expires_in = expiresInDays
    const k = await api(`/v1/team/keys${q}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...(sessionTokenRef.current ? {} : (activeKey ? { Authorization: `Bearer ${activeKey}` } : {})) },
      useSession: true,  // #1148: management → session JWT when signed in
      body: JSON.stringify(payload),
    })
    return k
  }
  // #1082 (PR1): claim-card state — paste tt_ key → OAuth → POST /v1/claim.
  const [claimKey, setClaimKey] = React.useState(() => {
    try { return sessionStorage.getItem(CLAIM_KEY_STORAGE) || '' } catch { return '' }
  })
  const [claimBusy, setClaimBusy] = React.useState(false)
  const [claimError, setClaimError] = React.useState('')
  // #1148-ux review: email+password claim (third identity option)
  const [claimShowEmail, setClaimShowEmail] = React.useState(false)
  const [claimEmail, setClaimEmail] = React.useState('')
  const [claimPassword, setClaimPassword] = React.useState('')

  // #1148: dashboard API-login toggle state (claimed teams). The flag rides
  // the /v1/team response (dashboard_key_login, default true). Toggle is
  // owner-only + session-authed server-side (PATCH /v1/team/dashboard-login);
  // optimistic local update, revert on error.
  const [toggleBusy, setToggleBusy] = React.useState(false)
  const [toggleError, setToggleError] = React.useState('')
  async function toggleDashboardKeyLogin() {
    if (!team || toggleBusy) return
    setToggleBusy(true)
    setToggleError('')
    const next = team.dashboard_key_login === false
    const prev = team
    try {
      // Optimistic flip; the server returns the authoritative team row.
      setTeam({ ...team, dashboard_key_login: next })
      // P3-3 (review): session-authed ONLY — never the raw API key (the key
      // is the very credential being disabled; self-lockout hazard).
      if (!sessionTokenRef.current) {
        setTeam(prev)
        setToggleError('Sign in with your Tortoise account to change this setting.')
        return
      }
      const res = await fetch(`${API_BASE}/v1/team/dashboard-login`, {
        method: 'PATCH',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${sessionTokenRef.current}`,
        },
        body: JSON.stringify({ enabled: next }),
      })
      if (res.ok) {
        const t = await res.json()
        // P2 (review): MERGE, don't replace — the PATCH returns only
        // {team_id, dashboard_key_login}; a full replace would wipe
        // tier/points/anon/checkout_price_id until reload.
        if (t && t.team_id) setTeam((prev) => ({ ...prev, ...t }))
      } else {
        let msg = `Couldn't update (HTTP ${res.status}).`
        try {
          const b = await res.json()
          msg = apiErrorText(res.status, b) || msg
        } catch { /* non-JSON body */ }
        setTeam(prev)
        setToggleError(msg)
      }
    } catch (e) {
      setTeam(prev)
      setToggleError((e && e.message) || `Couldn't update — try again.`)
    } finally {
      setToggleBusy(false)
    }
  }

  const initialTab = (() => {
    // #2509: read tab from landingHash (captured at module scope before
    // supabase.js init, so OAuth fragment stripping doesn't interfere).
    const h = landingHash
    if (h.startsWith('#/')) {
      const candidate = h.slice(2)
      if (KNOWN_TABS.includes(candidate)) return candidate
    }
    return 'overview'
  })()
  const [tab, setTab] = React.useState(initialTab)
  // #2509: sync tab state → URL hash (pushState for tab switches,
  // useRef guard skips initial mount to avoid strict-mode double effect).
  const tabSyncRef = React.useRef(false)
  const programmaticTabChangeRef = React.useRef(false)
  const popProcessingRef = React.useRef(false)
  React.useEffect(() => {
    if (!tabSyncRef.current) { tabSyncRef.current = true; return }
    const hash = '#/' + tab
    if (window.location.hash !== hash) {
      programmaticTabChangeRef.current = true
      window.history.pushState({ tab }, '', hash)
    }
  }, [tab])
  // #2509: sync URL hash → tab on browser back/forward (popstate) or
  // address-bar edits (hashchange). Clears intra-tab sub-state for parity
  // with nav-button clicks.
  React.useEffect(() => {
    function onHashChange() {
      // #2528: dedup guard — browsers that fire both popstate + hashchange
      // for the same URL change must not run the handler twice.
      if (popProcessingRef.current) return
      popProcessingRef.current = true
      setTimeout(() => { popProcessingRef.current = false }, 0)
      // #2528: Safari fires popstate on pushState — skip when the change
      // was self-triggered (tab sync effect sets this ref before pushState).
      if (programmaticTabChangeRef.current) {
        programmaticTabChangeRef.current = false
        return
      }
      const h = window.location.hash
      if (h.startsWith('#/')) {
        const candidate = h.slice(2)
        if (KNOWN_TABS.includes(candidate)) {
          setTab(candidate)
          setSelectedSessionId(null)
          setSessionDetail(null)
          return
        }
      }
      if (!h.startsWith('#/') && h.length > 0) {
        // OAuth fragment or unknown hash — don't override tab.
        return
      }
      // Unknown/malformed hash — fallback with replaceState (avoids
      // phantom history entry that pushState would create).
      setTab('overview')
      if (window.location.hash !== '#/overview') {
        window.history.replaceState({ tab: 'overview' }, '', '#/overview')
      }
    }
    window.addEventListener('popstate', onHashChange)
    window.addEventListener('hashchange', onHashChange)
    return () => {
      window.removeEventListener('popstate', onHashChange)
      window.removeEventListener('hashchange', onHashChange)
    }
  }, [])
  const [authMode, setAuthMode] = React.useState('session') // 'session' | 'apikey'
  const [checking, setChecking] = React.useState(true)
  const sessionTokenRef = React.useRef(null)
  // #1680: the session user metadata is captured at mount for component-
  // scope reads (the seed-step prefill for returning users).
  const sessionMetaRef = React.useRef(null)
  const [teams, setTeams] = React.useState([])
  const [graphs, setGraphs] = React.useState([])
  const [currentTeamId, setCurrentTeamId] = React.useState(null)
  // #1893 one-shot hydration: reconcile the persisted scope against the
  // live org repo list, exactly once per team session. Gated on the pure
  // shouldHydrate predicate (reposLoaded && onboarding && !reposLoadFailed
  // && currentTeamId && not-yet-hydrated) — never seeds the default empty
  // before the GET resolves, never prunes on a failed repos fetch, and
  // NEVER dead-paths on a null team: currentTeamId is STATE (populated by
  // the mount gate / team switcher), so the effect re-fires the moment the
  // team resolves. PER-KEY seeding: a key the user touched pre-hydration is
  // NOT seeded (their choice wins) and is persisted now; a key they did NOT
  // touch is seeded from the persisted server value — never overwritten
  // with the un-seeded default. Touch flags reset after hydration so a team
  // switch re-enables seeding for the new team.
  React.useEffect(() => {
    // #1893 (code-review P1): during a team switch, `onboarding` is
    // momentarily the PREVIOUS team's — the stale flag (set by switchTeam,
    // cleared when refreshOnboarding resolves) blocks hydration until the
    // new team's state lands, so the new team never seeds from (or persists
    // over) the old team's scope.
    if (onboardingStaleRef.current) return
    if (!shouldHydrate({ reposLoaded, onboarding, reposLoadFailed,
        currentTeamId, hydratedTeamId: hydratedTeamIdRef.current })) return
    hydratedTeamIdRef.current = currentTeamId
    if (!scopeTouchedRef.current.issues) {
      const seeded = reconcileIssuesScope(onboarding.github_issues_scope, reposList)
      setIssuesScope(seeded)
      issuesScopeRef.current = seeded  // mirror the ref (code-review P2 —
      // the touched-branch serialize must never read a STALE closure)
    } else {
      // serialize from the ref mirror — never the effect closure (P2)
      persistScope({ github_issues_scope: serializeIssuesScope(issuesScopeRef.current) })
    }
    if (!scopeTouchedRef.current.docs) {
      const seeded = reconcileDocsScope(onboarding.github_docs_scope, reposList)
      setDocsScope(seeded)
      docsScopeRef.current = seeded  // mirror the ref (see the heal effect)
      // code-review P2: hydration must load each seeded repo's branch
      // options — loadBranches fires only from the checkbox onChange, so
      // seeded repos would otherwise render a picker with only the
      // [default, all] options and shouldResetBranch could never heal a
      // stale persisted branch (it trusts values while no options are
      // loaded). The branchLists effect then reconciles + heals on load.
      seeded.repos.forEach((r) => loadBranches(r))
    } else {
      persistScope({ github_docs_scope: serializeDocsScope(docsScopeRef.current) })
    }
    scopeTouchedRef.current = { issues: false, docs: false }
    scopeReadyRef.current = true
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reposLoaded, onboarding, reposList, currentTeamId, reposLoadFailed])

  const [accountMenuOpen, setAccountMenuOpen] = React.useState(false) // #1148-ux: account blob dropdown
  // #1877: create-team dialog state (gated-on-click upgrade UX)
  const [createTeamOpen, setCreateTeamOpen] = React.useState(false)
  const [createTeamName, setCreateTeamName] = React.useState('')
  const [createTeamBusy, setCreateTeamBusy] = React.useState(false)
  const [createTeamError, setCreateTeamError] = React.useState('')
  const [createTeamUpgrade, setCreateTeamUpgrade] = React.useState(false)
  // #2392 (a11y): focus-restore holder for the create-team dialog — the
  // blob trigger button is captured when '+ Create new organization' is
  // clicked (the menu item itself unmounts when the account menu closes
  // under the dialog) and refocused on every close path.
  const createTeamRestoreRef = React.useRef(null)
  // #1875: invitee-side pending invites (account-menu surface)
  const [pendingInvites, setPendingInvites] = React.useState(null)  // null = not loaded
  const [pendingInvitesBusy, setPendingInvitesBusy] = React.useState('')  // '' | invitation_id
  const accountBlobRef = React.useRef(null) // #1148-ux review P2-4/P3-1: outside-click + Escape close
  // #2392 (a11y): the blob BUTTON (not the container) — the always-mounted
  // account-menu trigger. The menu itself unmounts on close and drops focus
  // to <body>; this button is where keyboard/SR focus must return.
  const accountBlobBtnRef = React.useRef(null)
  React.useEffect(() => {
    if (!accountMenuOpen) return
    function onPointerDown(e) {
      if (accountBlobRef.current && !accountBlobRef.current.contains(e.target)) {
        setAccountMenuOpen(false)
        // #2392 (a11y): an outside click that lands on nothing focusable
        // leaves focus on <body>. Reclaim it for the blob trigger — deferred
        // past the browser's default mousedown focus so a click that DID
        // focus a background control (tab, button…) keeps that focus instead
        // of fighting the user's intent.
        setTimeout(() => {
          if (typeof document !== 'undefined' && document.activeElement === document.body) {
            const btn = accountBlobBtnRef.current
            if (btn) btn.focus()
          }
        }, 0)
      }
    }
    function onKeyDown(e) {
      if (e.key === 'Escape') {
        setAccountMenuOpen(false)
        const btn = accountBlobBtnRef.current
        if (btn) btn.focus()
      }
    }
    document.addEventListener('pointerdown', onPointerDown)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('pointerdown', onPointerDown)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [accountMenuOpen])
  // #1148-ux: current team name for the account blob (derived from the
  // teams list; falls back to the team row name).
  const currentTeamName =
    teams.find((t) => t.team_id === currentTeamId)?.team_name
    || team?.team_name
    || ''


  const [members, setMembers] = React.useState(null) // null = not loaded / no access
  const [inviteEmail, setInviteEmail] = React.useState('')
  const [inviteRole, setInviteRole] = React.useState('member')
  const [backupInfo, setBackupInfo] = React.useState(null)
  // #1923: terminal backups status — mirrors graphsStatus/membersStatus so a
  // failed /backups resolves to an immediate '—' card (not an eternal
  // skeleton-then-'—' after frameStale) and counts as complete for the
  // Overview loaded announce. null backupInfo alone could not distinguish
  // 'still loading' from 'failed'.
  const [backupsStatus, setBackupsStatus] = React.useState('loading')
  const [newGraphName, setNewGraphName] = React.useState('')
  // C7 #2116: per-graph key panel + one-time reveal + delete lifecycle.
  // panelKeys is null until the first load for the OPEN row (panelKeysStatus
  // distinguishes closed/loading/ok/error — mirrors graphsStatus).
  const [panelGraphId, setPanelGraphId] = React.useState(null) // open key-panel row's graph_id
  const [panelKeys, setPanelKeys] = React.useState(null)
  const [panelKeysStatus, setPanelKeysStatus] = React.useState('closed') // closed|loading|ok|error
  const [graphKeyName, setGraphKeyName] = React.useState('')
  const [graphBusy, setGraphBusy] = React.useState(false) // panel mint / graph delete in flight
  const [graphMsg, setGraphMsg] = React.useState('') // inline PANEL error (mint/revoke — renders inside the open key panel only; #2301: delete failures are page-level, never here)
  const [confirmDeleteId, setConfirmDeleteId] = React.useState(null) // custom row whose type-to-confirm delete modal is open
  const [deleteConfirmTyped, setDeleteConfirmTyped] = React.useState('') // #2701: typed word gate for the delete modal
  // #2701: inline graph rename (mirrors the API-Keys ✏️ inline edit).
  const [editingGraphId, setEditingGraphId] = React.useState(null) // row in inline rename (null = none)
  const [editingGraphName, setEditingGraphName] = React.useState('')
  const graphRenameCancelRef = React.useRef(false) // Escape-in-edit suppresses the blur-save
  // #2304 trash (delete = 7-day recovery window): rows + restore/inspect.
  const [trash, setTrash] = React.useState([])
  const [trashStatus, setTrashStatus] = React.useState('closed') // closed|loading|ok|error
  const [confirmRestoreId, setConfirmRestoreId] = React.useState(null) // trash row awaiting restore confirm
  const [trashInspectId, setTrashInspectId] = React.useState(null) // rescue panel target (or null)
  const [trashInspect, setTrashInspect] = React.useState(null) // GET /trash/{id}/points payload
  const [trashMsg, setTrashMsg] = React.useState('') // trash-section error/notice
  // Panel-request sequence — a slow open must not clobber a NEWER open's
  // panel (panelGraphId in the handler closure is stale by design).
  const graphPanelReqRef = React.useRef(0)
  // One-time key reveal (C7 indicator 2 / surface 4): {plaintext, title} —
  // the ONLY place a minted plaintext is ever shown. Set from a create-graph
  // or per-graph mint response; cleared on dismiss / team switch; no route
  // ever re-shows it (the API never re-serves plaintext).
  const [revealKey, setRevealKey] = React.useState(null)
  // #2392 (a11y): focus-restore holder for the reveal-key modal. The mint is
  // ASYNC (the modal mounts only when the POST resolves), so the trigger is
  // captured at the mint gesture start in createGraph/mintGraphKey — by the
  // time the response lands, busy has disabled the trigger and focus has
  // already dropped to <body>.
  const revealRestoreRef = React.useRef(null)
  // #2392 (review P1): backdrop-click dismissal must never fire on a drag
  // that STARTED inside the card. The reveal modal shows a long wrapped
  // plaintext the user may select by hand (clipboard-failure fallback); a
  // selection drag that overshoots the card boundary completes on the
  // backdrop, and a plain onClick there would destroy the one-time secret
  // mid-copy. Track the pointerdown origin — only a press that began on the
  // backdrop itself may dismiss.
  const revealBackdropPressRef = React.useRef(false)
  // #2392 (a11y): the single create-team/reveal-key close path — every close
  // (backdrop, Escape, Cancel/Upgrade, success, Copy & done, I saved it)
  // restores focus to the opening trigger instead of dropping it on <body>.
  function closeCreateTeam() {
    setCreateTeamOpen(false)
    setCreateTeamUpgrade(false)
    restoreFocus(createTeamRestoreRef)
  }
  // NOTE: the logout/team-switch revealKey clears (below) deliberately do NOT
  // route through closeRevealKey — they are context changes, not user
  // dismissals, so no focus restore is wanted there.
  function closeRevealKey() {
    setRevealKey(null)
    restoreFocus(revealRestoreRef)
  }
  const teamIdRef = React.useRef(null)
  const teamRefreshSeqRef = React.useRef(0) // #1906 (code-review P2): monotonic seq for the welcome-path team refreshes — a post-seed refire must win over a concurrent exit refresh (a pre-seed point_count must never clobber the post-seed count)
  const authSubRef = React.useRef(null) // Round-6: supabase onAuthStateChange subscription
  const checkoutResetTimerRef = React.useRef(null) // Round-16: popup-flow fallback reset
  const apiKeyRef = React.useRef(null) // Round-21: live apiKey for staleness checks (state is closure-stale)
  const [checkoutPending, setCheckoutPending] = React.useState(false)
  const [billingPending, setBillingPending] = React.useState(false) // Round-25: double-click guard
  // P5 (code-review): distinguish 'loading' / 'ok' / 'denied' / 'error' so
  // loading and network failures never masquerade as an RBAC denial.
  const [membersStatus, setMembersStatus] = React.useState('loading')
  const [graphsLoaded, setGraphsLoaded] = React.useState(false) // Round-26: graphs card shows '—' until first load
  // #1842 P2-1: terminal graphs state — mirrors membersStatus so a failed
  // /v1/graphs resolves to a '—' card instead of an eternal shimmer (the old
  // `if (!res.ok) return` + catch{} left graphsLoaded false on any non-200).
  const [graphsStatus, setGraphsStatus] = React.useState('loading')
  // #714 (main): session detail view state
  const [selectedSessionId, setSelectedSessionId] = React.useState(null)
  const [sessionDetail, setSessionDetail] = React.useState(null)
  const [detailLoading, setDetailLoading] = React.useState(false)
  // #2002 (W6): Settings Captured-sessions view/delete state — the
  // transcript panel's fetch error (inline, per-panel) and the single-flight
  // delete (one session deleting at a time; row-level action error).
  const [sessionDetailError, setSessionDetailError] = React.useState(null)
  const [sessionDeletingId, setSessionDeletingId] = React.useState(null)
  const [sessionsActionError, setSessionsActionError] = React.useState(null)

  // #308 (R5/R7): suspension surfaces from the 403 detail (dict with
  // code === 'SUSPENDED' + appeal_url) — the banner renders it; the alert
  // list comes from the session-authed /v1/team/alerts.
  const [suspended, setSuspended] = React.useState(null)
  const [alerts, setAlerts] = React.useState([])

  function suspendedFromDetail(detail) {
    return detail && typeof detail === 'object' && detail.code === 'SUSPENDED' ? detail : null
  }

  async function api(path, opts = {}) {
    // #1148 review P1-2: management calls pass the SESSION JWT when signed
    // in (the dashboard-login gate rejects key-auth on those when the flag
    // is off — a session always passes). opts.useSession forces it.
    // #2246 (ADR-010): the key-derived default authHeaders is DELETED — the
    // browser never holds a key, so the only Authorization source is the
    // session JWT override below (the anon claim flows use raw fetch).
    let authHeaders = {}
    if (opts.useSession && sessionTokenRef.current) {
      authHeaders = { Authorization: `Bearer ${sessionTokenRef.current}` }
    }
    // #1835: json-body calls (onboarding-state PATCHes, etc.) must send
    // Content-Type: application/json or the server 422s on the body.
    const hasBody = typeof opts.body === 'string'
    const hdrs = { ...authHeaders, ...(opts.headers || {}) }
    // #2167 rule 1 (defense-in-depth): with a session JWT present a key
    // Authorization merged from opts.headers must never override the session
    // on a dual-auth endpoint (the old shape let a held key shadow the
    // session). #2246 (ADR-010): the mount stored-key probe (the old rule-1
    // exemption) is DELETED — a session-authed browser never holds a key, so
    // api() is session-JWT-only in every reachable state. authMode 'apikey'
    // is the sessionless claim-paste screen, which returns before the chrome
    // and never calls api() (the claim flows use raw fetch).
    if (sessionTokenRef.current) hdrs.Authorization = `Bearer ${sessionTokenRef.current}`
    if (hasBody && !hdrs['Content-Type'] && !hdrs['content-type']) hdrs['Content-Type'] = 'application/json'
    const res = await fetch(`${API_BASE}${path}`, { ...opts, headers: hdrs })
    if (!res.ok) {
      const body = await res.json().catch(() => ({}))
      // Round-11: attach the HTTP status — hosted_api.py returns detail strings
      // ('Invalid API key', 'Unauthorized', …), never '401', so status-based
      // checks (switchTeam re-mint) must read e.status, not message content.
      // #308: suspended teams get a dict detail — surface the appeal link.
      const sus = suspendedFromDetail(body.detail)
      const err = new Error(sus ? (sus.message || 'Organization suspended') : (typeof body.detail === 'string' ? body.detail : `HTTP ${res.status}`))
      err.status = res.status
      if (sus) err.suspended = sus
      throw err
    }
    return res.json()
  }

  // ── #1765 identity surface: inventory fetch + link/unlink/resend handlers ──
  async function fetchIdentity() {
    if (authMode !== 'session' || !sessionTokenRef.current) return
    setIdentityLoading(true)
    try {
      const inv = await api('/v1/user/identity', { useSession: true })
      setIdentityInv(inv)
      setIdentityError('')
    } catch (e) {
      // fail-closed: no banner, error surfaced on the profile tab
      setIdentityInv(null)
      setIdentityError(e.message || 'Could not load login methods')
    } finally {
      setIdentityLoading(false)
    }
  }

  async function handleAddOAuth(provider) {
    setProfileBusy('oauth'); setProfileError('')
    try {
      const { intent_ref } = await api('/v1/user/identity/link-intent', {
        method: 'POST', useSession: true,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ provider }),
      })
      // intent-ref contract: vendored supabase-js linkIdentity (REDIRECT
      // flow — flowId is null under implicit, so the app's ?link_flow=
      // search param + sessionStorage marker carry the ref; the mount
      // effect POSTs link-commit on return).
      try { sessionStorage.setItem('tt_link_flow', intent_ref) } catch { /* best-effort */ }
      if (supabaseClient) {
        const { error } = await supabaseClient.auth.linkIdentity({
          provider,
          options: {
            redirectTo: `${window.location.origin}${window.location.pathname}?link_flow=${encodeURIComponent(intent_ref)}`,
          },
        })
        if (error) throw new Error(error.message)
      }
    } catch (e) {
      setProfileError(e.message || 'Could not start linking')
    } finally {
      setProfileBusy('')
    }
  }

  async function handleAddEmail(email, password) {
    setProfileError('')
    if (!supabaseClient) { setProfileError('Auth is unavailable'); return }
    if (identityInv && identityInv.email_confirmed_at) {
      // confirmed email → updateUser({password}) only (#2085: creates no
      // email identity row — has_password is the tracked signal)
      setProfileBusy('email')
      try {
        const { error } = await supabaseClient.auth.updateUser({ password })
        if (error) throw new Error(error.message)
        await fetchIdentity()
      } catch (e) { setProfileError(e.message || 'Could not add email login') }
      finally { setProfileBusy('') }
    } else {
      // unconfirmed/absent email → change-email + confirmation + set-password,
      // gated by the ReauthDialog (stolen-session ATO guardrail, plan-review
      // P1-1 — never bypass double_confirm_changes). The pending action is
      // DATA (not a closure) so it survives the provider OAuth round-trip.
      // #2479: check retry limit before opening re-auth dialog
      if (reauthAttemptRef.current >= MAX_REAUTH_ATTEMPTS) {
        setProfileError(REAUTH_EXCEEDED_MESSAGE)
        return
      }
      // #2392 (a11y): capture the opening trigger (the add-email submit
      // control) while it still owns focus — synchronous here, no await in
      // this branch.
      setProfileBusy('reauth')
      rememberFocusedTrigger(reauthRestoreRef)
      pendingReauthRef.current = { email, password }
      setReauthOpen(true)
    }
  }

  async function doChangeEmail(email, password) {
    if (!supabaseClient) throw new Error('Auth is unavailable')
    const { error } = await supabaseClient.auth.updateUser({ email, password })
    if (error) throw new Error(error.message)
    await fetchIdentity()
  }

  async function handleUnlink(identityId) {
    // #2392 (a11y): capture the opening trigger (the unlink/Remove button)
    // at gesture start — by the time a 403 REAUTH_REQUIRED lands below,
    // profileBusy has disabled the button and focus has dropped to <body>.
    rememberFocusedTrigger(reauthRestoreRef)
    setProfileBusy('unlink'); setProfileError('')
    try {
      await api('/v1/user/identity/unlink', {
        method: 'POST', useSession: true,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ identity_id: identityId }),
      })
      await fetchIdentity()
    } catch (e) {
      // #2479: server returns 403 REAUTH_REQUIRED when session is stale
      if (e.status === 403 && /REAUTH_REQUIRED/i.test(e.message)) {
        if (reauthAttemptRef.current >= MAX_REAUTH_ATTEMPTS) {
          setProfileError(REAUTH_EXCEEDED_MESSAGE)
          return
        }
        // #2479 code-review fix P1: if we already re-executed this pending action
        // after successful re-auth and it failed again, bail without re-opening dialog
        if (reauthRetriedRef.current) {
          setProfileError(REAUTH_EXCEEDED_MESSAGE)
          return
        }
        reauthRetriedRef.current = false
        pendingReauthRef.current = { unlinkIdentityId: identityId }
        setReauthOpen(true)
        return
      }
      setProfileError(e.message || 'Could not remove login method')
    } finally {
      setProfileBusy('')
    }
  }

  async function handleResend() {
    setProfileBusy('resend'); setProfileError('')
    try {
      await api('/v1/user/identity/resend-confirmation', { method: 'POST', useSession: true })
      await fetchIdentity()
    } catch (e) {
      setProfileError(e.message || 'Could not resend confirmation')
    } finally {
      setProfileBusy('')
    }
  }

  async function handleReauthPassword(password) {
    setReauthBusy(true); setReauthError('')
    try {
      if (!supabaseClient) throw new Error('Auth is unavailable')
      const { error } = await supabaseClient.auth.signInWithPassword({
        email: (identityInv && identityInv.email) || '', password,
      })
      if (error) throw new Error(error.message)
      // #2479: success — reset attempt counter
      reauthAttemptRef.current = 0
      closeReauth()
      await fetchIdentity()
      const pending = pendingReauthRef.current
      pendingReauthRef.current = null
      if (pending) {
        if (pending.unlinkIdentityId) {
          // #2479: re-auth was for unlink — re-execute with fresh session
          reauthRetriedRef.current = true
          handleUnlink(pending.unlinkIdentityId)
          return
        }
        if (pending.promptPassword) {
          // #1765 review P1-2: in promptPassword mode the typed password IS
          // the NEW password — apply it directly (never signInWithPassword,
          // which would fail against the not-yet-set password)
          setReauthPasswordMode(false)
          await doChangeEmail(pending.email, password)
          return
        }
        await doChangeEmail(pending.email, pending.password)
      }
    } catch (e) {
      reauthAttemptRef.current += 1
      if (reauthAttemptRef.current >= MAX_REAUTH_ATTEMPTS) {
        closeReauth()
        pendingReauthRef.current = null
        setProfileError(REAUTH_EXCEEDED_MESSAGE)
        return
      }
      setReauthError(e.message || 'Sign-in failed')
    } finally {
      setReauthBusy(false)
    }
  }

  async function handleReauthProvider(provider) {
    setReauthBusy(true); setReauthError('')
    // #1765 review P1: capture the pre-round-trip session uid — if the
    // provider sign-in switches accounts, abort the pending change-email
    try {
      const { data: pre } = await supabaseClient.auth.getUser()
      beforeUidRef.current = (pre && pre.user && pre.user.id) || null
    } catch { beforeUidRef.current = null }
    try {
      if (!supabaseClient) throw new Error('Auth is unavailable')
      // same-provider re-sign-in (a different provider with private email
      // would auto-link a NEW user → account split); resume the pending
      // action after the round-trip via the ?reauth=1 marker
      const pending = pendingReauthRef.current
      if (pending) {
        // #1765 review P1: NEVER persist the new password — store {email}
        // + the pre-round-trip uid (survives the full-page OAuth nav; the
        // return effect compares against it to detect an account switch).
        try {
          sessionStorage.setItem('tt_reauth_pending', JSON.stringify({
            email: pending.email, uid: beforeUidRef.current,
            unlinkIdentityId: pending.unlinkIdentityId }))
        } catch { /* best-effort */ }
      }
      const { error } = await supabaseClient.auth.signInWithOAuth({
        provider,
        options: { redirectTo: `${window.location.origin}${window.location.pathname}?reauth=1` },
      })
      if (error) throw new Error(error.message)
      if (!pending) closeReauth()
    } catch (e) {
      setReauthError(e.message || 'Sign-in failed')
      setReauthBusy(false)
    }
  }

  // #1765: identity fetch on session + window-focus refetch (kills stale
  // banner after confirm-in-another-tab / mutations)
  React.useEffect(() => {
    if (authMode !== 'session') return
    if (!sessionBooted) return  // post-boot deterministic fetch (review-fix:
                                // authMode never transitions for a session
                                // holder — the [authMode]-only effect would
                                // run pre-token and never again)
    fetchIdentity()
    lastIdentityFetchRef.current = Date.now()
    // #1765 review: throttle the focus refetch (the server does an RPC +
    // GoTrue admin GET per call — every alt-tab must not re-run both)
    const onFocus = () => {
      if (shouldRefetchOnFocus(lastIdentityFetchRef.current)) {
        lastIdentityFetchRef.current = Date.now()
        fetchIdentity()
      }
    }
    window.addEventListener('focus', onFocus)
    return () => window.removeEventListener('focus', onFocus)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [authMode, sessionBooted])

  // #1765: OAuth-return intent-ref contract — POST link-commit on return
  // (the server-authority gates must actually run), then land on Profile.
  // Also resumes a re-auth-pending change-email after the provider round.
  React.useEffect(() => {
    if (!sessionBooted) return  // review-fix: the OAuth-return commit must
                                // not fire before the session loads
    const params = new URLSearchParams(window.location.search)
    const linkFlow = params.get('link_flow')
    const reauth = params.get('reauth')
    if (!linkFlow && !reauth) return
    ;(async () => {
      if (linkFlow && sessionTokenRef.current) {
        try {
          const res = await api('/v1/user/identity/link-commit', {
            method: 'POST', useSession: true,
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ intent_ref: linkFlow }),
          })
          await fetchIdentity()
          setTab('profile')
          if (res.adoption_signal) {
            setProfileError("This email is also used by another organization — reach out if that's unexpected.")
          }
        } catch (e) {
          setProfileError(e.message || 'Could not complete linking — refresh your profile')
        } finally {
          try { sessionStorage.removeItem('tt_link_flow') } catch { /* best-effort */ }
          const u = new URL(window.location.href)
          u.searchParams.delete('link_flow')
          window.history.replaceState({}, '', u.pathname + u.search + u.hash)
        }
      } else if (reauth && sessionTokenRef.current) {
        try {
          // #1765 review P1: the provider round-trip must NOT have switched
          // accounts (a different provider auto-links a new user). The uid
          // is restored from tt_reauth_pending (a ref would reset on the
          // full-page OAuth navigation — the app remounts on return).
          let pending = pendingReauthRef.current
          pendingReauthRef.current = null
          if (!pending) {
            try {
              const raw = sessionStorage.getItem('tt_reauth_pending')
              if (raw) pending = JSON.parse(raw)
            } catch { /* best-effort */ }
          }
          await fetchIdentity()
          setTab('profile')
          const { data: sess } = await supabaseClient.auth.getSession()
          const returnedUid = sess && sess.session && sess.session.user && sess.session.user.id
          if (pending && pending.uid && returnedUid && returnedUid !== pending.uid) {
            setProfileError("Signed in as a different account — sign out and retry.")
            return
          }
          if (pending) {
            // #2479: check if re-auth was for unlink
            if (pending.unlinkIdentityId) {
              setReauthPasswordMode(false)
              reauthRetriedRef.current = true
              handleUnlink(pending.unlinkIdentityId)
              return
            }
            // re-prompt the NEW password (never persisted across the round-trip)
            pendingReauthRef.current = { email: pending.email, promptPassword: true }
            setProfileBusy('reauth')
            setReauthOpen(true)
            setReauthPasswordMode(true)
          }
        } catch (e) {
          setProfileError(e.message || 'Could not finish the change')
        } finally {
          try { sessionStorage.removeItem('tt_reauth_pending') } catch { /* best-effort */ }
          const u = new URL(window.location.href)
          u.searchParams.delete('reauth')
          window.history.replaceState({}, '', u.pathname + u.search + u.hash)
        }
      }
    })()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionBooted])

  // ── Billing (#310 Task 9): upgrade CTA + manage billing ──
  const ACTIVE_STATUSES = ['active', 'past_due', 'trialing']
  const hasActiveSubscription = team && ACTIVE_STATUSES.includes(team.subscription_status)
  // #1623 (review P2): canceled/unpaid teams still have a Stripe customer —
  // the portal gives invoice history + cancel management. Upgrade stays for
  // re-subscription.
  const PORTAL_STATUSES = [...ACTIVE_STATUSES, 'canceled', 'unpaid']
  const canManageSubscription = team && PORTAL_STATUSES.includes(team.subscription_status)

  // #1623: parameterized upgrade — the header Upgrade button uses the
  // server-resolved default (team.checkout_price_id); the Billing page and
  // welcome plan step pass a per-tier price id from team.checkout_price_ids.
  async function upgradeToPrice(priceId) {
    if (!priceId || checkoutPending) return
    setCheckoutPending(true)
    try {
      const { checkout_url } = await api('/v1/billing/checkout', {
        useSession: true,  // #1148: management → session JWT when signed in
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ price_id: priceId }),
      })
      const win = window.open(checkout_url, '_blank')
      // Round-13: async-fetch-then-open is popup-blocked in Firefox/Safari —
      // don't leave the Upgrade button stuck at 'Opening checkout…'.
      if (!win) {
        setCheckoutPending(false)
        setError('Popup blocked — allow popups for app.premiselabs.co and try again.')
      } else {
        // Round-16 (P2): Stripe's success redirect (?session_id) lands in the
        // POPUP tab, never this one — the URL-param poll never fires here.
        // Bounded fallback so the button self-heals without a reload; the
        // cancelled/session_id paths clear this timer.
        window.clearTimeout(checkoutResetTimerRef.current)
        checkoutResetTimerRef.current = window.setTimeout(() => setCheckoutPending(false), 90000)
      }
    } catch (err) {
      setError(err.message)
      setCheckoutPending(false)
    }
  }

  async function upgrade() {
    await upgradeToPrice(team?.checkout_price_id)
  }

  async function manageBilling() {
    if (billingPending) return // Round-25: double-click guard — no duplicate portal tabs
    setBillingPending(true)
    try {
      const { portal_url } = await api('/v1/billing/portal', { method: 'POST', useSession: true })
      // Round-14: mirror upgrade() — async-fetch-then-open is popup-blocked in
      // Firefox/Safari; surface it instead of silently no-opping.
      if (!window.open(portal_url, '_blank')) {
        setError('Popup blocked — allow popups for app.premiselabs.co and try again.')
      }
    } catch (err) {
      setError(err.message)
    } finally {
      setBillingPending(false)
    }
  }

  // Success-return path: ?session_id=... triggers a refetch loop until the
  // webhook flips subscription_status to active; ?checkout=cancelled clears
  // the pending flag. Both params are stripped from the URL after handling.
  React.useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const sessionId = params.get('session_id')
    const cancelled = params.get('checkout') === 'cancelled'
    if (sessionId) {
      let tries = 0
      // Round-13 (P2): a mid-poll team switch must not let a tick land the
      // OLD team's data under the NEW team's switcher — capture the team at
      // poll start and bail when it changes.
      const teamAtPollStart = teamIdRef.current
      const poll = setInterval(async () => {
        tries += 1
        try {
          const t = await refreshTeam(undefined, teamAtPollStart)
          if (t && ACTIVE_STATUSES.includes(t.subscription_status)) { tries = 5 }
        } catch { /* webhook may not have landed yet */ }
        if (tries >= 5) {
          clearInterval(poll)
          setCheckoutPending(false) // Round-15: popup flow never returns the param to this tab — don't stay stuck
          params.delete('session_id')
          window.history.replaceState({}, '', `${window.location.pathname}${params.toString() ? `?${params}` : ''}${window.location.hash}`)
        }
      }, 2000)
      return () => clearInterval(poll)
    }
    if (cancelled) {
      window.clearTimeout(checkoutResetTimerRef.current)
      setCheckoutPending(false)
      params.delete('checkout')
      window.history.replaceState({}, '', `${window.location.pathname}${params.toString() ? `?${params}` : ''}${window.location.hash}`)
    }
  }, [team?.subscription_status])

  // ── Session auth: on load, try the shared cookie session ──
  // #1643 (review P2-1): read the onboarding state on mount so completed
  // users never see the re-entry card again (the completion marker is
  // persisted server-side). #1728 Slice 3: the same read now feeds the full
  // Memory-sources surface (three toggles).
  async function refreshOnboarding() {
    // #1893 (code-review P1): response-identity guard — capture the team at
    // CALL time and bail if the team moved before the response lands (a
    // switchTeam during the in-flight GET must not land team A's onboarding
    // under team B, and must not clear the switch-stale flag for a stale
    // team). Mirrors the Round-10 _teamAtCall pattern in loadAll/loadGraphs.
    const _teamAtCall = teamIdRef.current
    try {
      if (!sessionTokenRef.current) {
        // Mount race (#1838): the onboarding-state GET rides the session JWT —
        // the mount gate populates sessionTokenRef.current after getSession()
        // resolves, but this mount effect fires first. Wait for the session to
        // materialize (bounded) instead of firing an unauthenticated GET that
        // 401s ("Missing session token"). A null result means the session is
        // genuinely absent (or the auth lib failed to load — the gate bounces
        // to /auth / renders the auth-unavailable card), so returning is
        // correct: the loading surface just stays in its idle state.
        let session = null
        if (supabaseClient) {
          const { data } = await supabaseClient.auth.getSession()
          session = (data && data.session) || null
        }
        // P2 (review): strict validity check — a non-expired JWT is required,
        // otherwise fall through to the loading-off return below.
        if (session && session.access_token && session.expires_at && session.expires_at * 1000 > Date.now()) {
          sessionTokenRef.current = session.access_token
        } else {
          setOnboardingLoading(false)
          return
        }
      }
      // #1893 (code-review P1): pin the SELECTED team so multi-membership
      // users read/write the team the scope surface shows (the server
      // defaults to memberships[0] without it). teamIdRef mirrors
      // currentTeamId but is set synchronously in switchTeam (no render
      // closure race).
      const st = await api(`/v1/onboarding/state${onboardingTeamQ()}`, { useSession: true })
      // #1893 (code-review P1): apply ONLY a team-pinned response. The mount
      // `[]`-effect GET fires before the mount gate sets teamIdRef (captured
      // _teamAtCall = null) — applying it would seed memberships[0]'s scope
      // under the selected team (wrong for multi-membership users) and let
      // hydration latch the wrong team's data before the currentTeamId
      // effect's pinned refetch lands. The pinned refetch is authoritative;
      // the unpinned mount GET is discarded (harmless — the refetch corrects
      // onboarding, and the first-timer 403-swallow path is unaffected).
      if (st && st.onboarding && _teamAtCall && teamIdRef.current === _teamAtCall) {
        // code-review P1: this response is for the CURRENT team (the team
        // did not move while the GET was in flight) — clear the switch-stale
        // flag so hydration can proceed.
        onboardingStaleRef.current = false
        setOnboarding(st.onboarding)
        if (st.onboarding.onboarding_complete) setOnboardingComplete(true)
      }
      setOnboardingLoading(false)
    } catch (e) {
      // #1847/#2323 (Option B): first-timer org-create race — the mount-time
      // refreshOnboarding() fires BEFORE the org exists (a teamless first-
      // timer is no longer provisioned at mount; the org-create step submit
      // provisions via tenant-provision), so the GET 403s 'No team
      // membership' and the MemorySources panel would render its error card
      // until reload. Swallow ONLY that exact no-team 403, discriminated by
      // ref (Round-11: status-based checks read e.status / refs, not message
      // content): teamIdRef is provably null at swallow time for the
      // first-timer pre-org-create case (provisionInApp never sets it; the
      // currentTeamId effect only fires post-provision), and being a ref it
      // has no stale-closure trap (the mount effect closes over the
      // first-render [], so teamsList.length would wrongly swallow cross-team
      // 403s for returning users). Suspended teams return a dict detail →
      // e.suspended is set, and a successful /v1/teams with a still-403
      // onboarding leaves teamIdRef set — both fall through to the normal
      // error state (honest, retryable card) below: keeping the loading
      // state leaves the panel in its initial state;
      // finishWelcomeLoads() re-fires this after org-create + provisioning
      // and is the authoritative load.
      if (e && e.status === 403 && !e.suspended && !teamIdRef.current) return
      setOnboardingLoading(false)  // best-effort — the surface renders its error state
    }
  }
  React.useEffect(() => { refreshOnboarding() }, [])  // eslint-disable-line react-hooks/exhaustive-deps

  // ── #1643 wizard actions ────────────────────────────────────────────────
  // #1997 (W1): LEGACY #1643 step labels — ARCHIVED-not-deleted (A0 rollback
  // path, epic §8; the DE2E-1 archived-not-deleted assertion greps this +
  // the LEGACY_WIZARD_ARCHIVED marker below). The LIVE wizard renders
  // WIZARD_STEPS (wizardFlow.js) — 4 human steps.
  const wizardSteps = ['Connect your tool', 'Memory sources', 'Your agent\'s toolkit', 'Seed your graph', 'You\'re set']
  // #1997 (W1): ARCHIVED flag — the legacy #1643 wizard render JSX below
  // stays byte-identical for the A0 gate's rollback path (partial revert
  // restores it); it is NEVER rendered by the live wizard. Flipping this
  // back to true + re-enabling the welcomeOriented gate restores the
  // legacy surface (rollback drill, epic §8).
  const LEGACY_WIZARD_ARCHIVED = false
  // #1997 (W1): org-create + fork-card state for the 5-step wizard.
  const [wizardOrgName, setWizardOrgName] = React.useState('')
  const [wizardOrgError, setWizardOrgError] = React.useState('')
  const [wizardOrgBusy, setWizardOrgBusy] = React.useState(false)
  const [wizardForkBusy, setWizardForkBusy] = React.useState(false)
  const [wizardForkError, setWizardForkError] = React.useState('')
  const [wizardForkChosen, setWizardForkChosen] = React.useState('')  // 'self' | 'build' | '' (set once per org)
  // #1998 (W2): connect-consent state — the harness-connected checkpoint
  // write on "I've set it up — Continue" (busy + error mirror handleWizardFork).
  const [wizardConnectBusy, setWizardConnectBusy] = React.useState(false)
  const [wizardConnectError, setWizardConnectError] = React.useState('')
  const [wizardKeyMode, setWizardKeyMode] = React.useState('included')
  // #1998 fold-in (durable connect key, PR #2161 finding): the connect step's
  // universal command must embed a DURABLE key. #2246 (ADR-010): the browser
  // never holds a session credential — the gate sources from the usable
  // durable ROWS or the first-timer welcomeKey, and when a usable durable
  // exists the wizard mints a fresh provisioned key on demand (POST
  // /v1/team/keys) — held HERE in-memory, shown once in the command snippet;
  // cap error routes to the API Keys tab (regenerate).
  const [wizardDurableKey, setWizardDurableKey] = React.useState('')

  // #2361 review-r1 (I6): the connect-step key is shown ONCE — an accidental
  // close right after copy loses it. Warn while a fresh plaintext is live.
  React.useEffect(() => {
    if (!(welcomeMode && (welcomeKey || wizardDurableKey))) return undefined
    const warn = (e) => { e.preventDefault(); e.returnValue = '' }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [welcomeMode, welcomeKey, wizardDurableKey])

  const [wizardDurableBusy, setWizardDurableBusy] = React.useState(false)
  const [wizardDurableError, setWizardDurableError] = React.useState('')
  // #1998 fold-in: optional paste-your-own-durable-key fallback (closes the
  // 402-cap loop — a user at max_api_keys regenerates in the API Keys tab and
  // pastes the shown-once replacement here instead of dead-ending).
  const [wizardDurablePaste, setWizardDurablePaste] = React.useState('')
  // #2325: paste-your-own is an ESCAPE behind a disclosure for owner/admin
  // (their primary is the mint CTA below) — never a parallel third
  // affordance sitting next to the primary action. Members (no in-dashboard
  // mint) always see the paste box: it is their only path.
  const [wizardShowPaste, setWizardShowPaste] = React.useState(false)
  // #1997 (W1): catalog-presented is marked when the build catalog
  // RENDERS (not just on pick) — re-entry with fork=build already set must
  // still mark it (launch-slice build-fork gate evaluable). Ref-guarded:
  // the checkpoint is keyed-MERGE (replay no-op), but a per-ORG guard keeps
  // the network quiet on re-renders (the latch is keyed by team id so a
  // second build org in the same session still marks its own catalog —
  // review P2, #1997).
  const catalogMarkedRef = React.useRef({})
  React.useEffect(() => {
    if (wizardStep !== 2) return
    const teamKey = teamIdRef.current || 'default'
    const buildFork = (onboarding && onboarding.fork === 'build') || wizardForkChosen === 'build'
    if (buildFork && !catalogMarkedRef.current[teamKey]) {
      catalogMarkedRef.current[teamKey] = true
      api(`/v1/onboarding/state/checkpoint${onboardingTeamQ()}`, {
        method: 'POST', useSession: true,
        body: JSON.stringify({ step: 'catalog-presented' }),
      }).catch(() => {})
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wizardStep, wizardForkChosen, onboarding && onboarding.fork])

  // #2004 (W8): the registry-backed builder catalog — fetched ONCE per
  // session from GET /v1/capabilities (tortoise/tool_registry.py
  // CAPABILITY_CATALOG) when the build branch renders on step 2. The static
  // placeholder in wizardFlow.js renders until the fetch resolves and stays
  // as the OFFLINE fallback (same names — never a blank catalog; the
  // registry-presented mark above is untouched: this swap is SOURCE-only,
  // the once-per-org catalog-presented step edge still fires on first
  // build-fork render and is a keyed-MERGE no-op on replay).
  const [wizardCatalog, setWizardCatalog] = React.useState(null)
  const catalogFetchedRef = React.useRef(false)
  React.useEffect(() => {
    if (wizardStep !== 2) return
    const buildFork = (onboarding && onboarding.fork === 'build') || wizardForkChosen === 'build'
    if (!buildFork || catalogFetchedRef.current) return
    catalogFetchedRef.current = true
    api('/v1/capabilities', { useSession: true })
      .then((res) => { if (res && Array.isArray(res.modules) && res.modules.length > 0) setWizardCatalog(res.modules) })
      .catch(() => { catalogFetchedRef.current = false })  // transient failure → retry on next effect run; meanwhile the offline fallback renders (honest degrade)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wizardStep, wizardForkChosen, onboarding && onboarding.fork])

  function wizardCopy(text, label) {
    try { navigator.clipboard.writeText(text) } catch { /* clipboard blocked */ }
    setWizardCopied(label)
    if (label !== 'harness') {
      // #1691: the harness label is STICKY on purpose — the positive
      // 'I've set it up — Continue' affordance must persist after the user
      // copies and goes to paste/run it (the 1.6s flash timer would eat
      // it). It resets on harness-tab switch and on step change instead.
      setTimeout(() => { if (mountedRef.current) setWizardCopied('') }, 1600)  // review: mounted-guard the flash timer
    }
    api(`/v1/onboarding/state${onboardingTeamQ()}`, { method: 'PATCH', useSession: true,
      body: JSON.stringify({ harness: wizardHarness, section: 'config' }) }).catch(() => {})
  }

  // #1701 R2: chatgpt's copy handler is AWAITED — Continue (the checkpoint)
  // must never be reachable without a successful copy, so the clipboard write
  // resolves BEFORE the sticky wizardCopied='harness' state lands (mirrors
  // claude-web's gate: copying ≠ setup done). The PATCH beacon fires on
  // resolution only; wizardCopy above stays fire-and-forget so the 6 keyed
  // harnesses keep byte-identical behavior.
  async function wizardCopyChatgpt() {
    setWizardConnectError('')
    const text = HARNESS_INSTALL.chatgpt()
    try {
      await navigator.clipboard.writeText(text)
    } catch {
      setWizardCopyFailed(true)
      setWizardConnectError('Copy failed — select the prompt below and press ⌘/Ctrl-C, then Continue below')
      return
    }
    setWizardCopyFailed(false)
    setWizardCopied('harness')
    api(`/v1/onboarding/state${onboardingTeamQ()}`, { method: 'PATCH', useSession: true,
      body: JSON.stringify({ harness: 'chatgpt', section: 'config' }) }).catch(() => {})
  }

  const stopGithubPoll = () => {
    if (wizardGithubPollRef.current) { clearInterval(wizardGithubPollRef.current); wizardGithubPollRef.current = null }
  }

  // ── #1728 Slice 3 (Task 16): bounded poll pattern ──
  // tries + terminal-status short-circuit; the handle lives in a ref
  // (cleared on success + unmount); per-team staleness guard. Deliberately
  // does NOT copy the old github connect poll's dangling-timer anti-pattern.
  function startBoundedPoll(ref, { url, interval = 3000, maxTries = 40, isTerminal, onStatus, onDone }) {
    if (ref.current) { clearInterval(ref.current); ref.current = null }
    const teamAtStart = teamIdRef.current
    let tries = 0
    const tick = async () => {
      tries += 1
      if (teamIdRef.current !== teamAtStart) { stopBoundedPoll(ref); return }  // per-team staleness guard
      try {
        const job = await api(url, { useSession: true })
        if (onStatus) onStatus(job)
        if (job && isTerminal(job)) { stopBoundedPoll(ref); if (onDone) onDone(job); return }
      } catch (e) {
        // 404 = the in-memory job was evicted (1h TTL) — a TERMINAL state
        // the UI renders honestly ("status expired — re-check"), not a retry loop.
        if (e && e.status === 404) { stopBoundedPoll(ref); if (onDone) onDone({ status: 'expired', error: e.message }); return }
      }
      if (tries >= maxTries) { stopBoundedPoll(ref); if (onDone) onDone({ status: 'timeout' }) }
    }
    ref.current = setInterval(tick, interval)
  }
  function stopBoundedPoll(ref) {
    if (ref.current) { clearInterval(ref.current); ref.current = null }
  }

  // ── #1728 Slice 3 (Task 16/17): Memory-sources handlers ──
  function setRowError(row, msg) { setMemoryErrors((e) => ({ ...e, [row]: msg })) }

  // ── #1845: load the connected org's repo names for the scope selectors ──
  async function loadRepos() {
    // #1893 (code-review P1): pin the SELECTED team (see refreshOnboarding).
    const _teamAtCall = teamIdRef.current
    try {
      const res = await api(`/v1/onboarding/github/repos${onboardingTeamQ()}`, { useSession: true })
      if (teamIdRef.current !== _teamAtCall) return  // stale switch response
      setReposList(res && Array.isArray(res.repos) ? res.repos : [])
      // #1893 (code-review P1): a server-side resolve failure returns 200
      // with an EMPTY list + resolve_error:true (never a 500) — that is
      // NOT evidence of an empty org and must gate hydration exactly like
      // a transport failure (pruning on it would clobber the stored scope).
      setReposLoadFailed(!!(res && res.resolve_error))
    } catch {
      if (teamIdRef.current !== _teamAtCall) return
      setReposList([])  // best-effort — the selector still shows "All repos"
      setReposLoadFailed(true)
    } finally {
      if (teamIdRef.current === _teamAtCall) setReposLoaded(true)
    }
  }
  // #1845: lazily load a repo's branch list for the docs per-repo branch
  // picker. Best-effort — a failure leaves the picker on its default
  // option. Review P2-4: also records the API-reported default_branch so
  // the default option is labeled truthfully for repos whose default is
  // neither main nor master.
  async function loadBranches(repo) {
    if (Object.prototype.hasOwnProperty.call(branchLists, repo)) return  // already loaded
    try {
      const q = encodeURIComponent(repo)
      // #1893 (code-review P1): pin the SELECTED team (see refreshOnboarding).
      // The URL already carries ?repo= — join team_id with & (a second `?`
      // would corrupt the repo value and silently unpin the team).
      const _teamAtCall = teamIdRef.current
      const res = await api(`/v1/onboarding/github/branches?repo=${q}${onboardingTeamQ('&')}`, { useSession: true })
      if (teamIdRef.current !== _teamAtCall) return  // stale switch response
      const branches = res && Array.isArray(res.branches) ? res.branches : []
      const defaultBranch = (res && res.default_branch) || ''
      setBranchLists((prev) => ({ ...prev, [repo]: { branches, defaultBranch } }))
    } catch {
      if (teamIdRef.current !== _teamAtCall) return
      setBranchLists((prev) => ({ ...prev, [repo]: { branches: [], defaultBranch: '' } }))
    }
  }
  // review P2-4: once a repo's branches + default load, seed the picker's
  // branch choice to the API default ('' = server main/master fallback) so
  // a repo whose default is neither main nor master indexes the right
  // branch out of the box.
  React.useEffect(() => {
    // #1893 (review P3): compute from the closure (docsScope is a dep) and
    // persist from the EFFECT BODY — never inside the state updater (React
    // purity: StrictMode double-invokes updaters, and updaters can run during
    // render-phase eager evaluation).
    let changed = false
    const branches = { ...docsScope.branches }
    docsScope.repos.forEach((r) => {
      if (!Object.prototype.hasOwnProperty.call(branchLists, r)) return
      const info = branchLists[r]
      // #1893: a persisted branch that no longer exists on GitHub must
      // not stick a blank picker + fail the docs job. Runs BEFORE the
      // default fill so a reset lands on the repo's API default option
      // (deterministic — the fill is skipped for a truthy stale branch;
      // '' and 'all' are always-valid markers). Idempotent guard: if the
      // API default itself is not among the options, the heal target is
      // already set — stop (no re-trigger loop under the docsScope dep).
      if (shouldResetBranch(branches[r], info)) {
        const heal = info.defaultBranch || ''
        if (branches[r] !== heal) {
          branches[r] = heal
          changed = true
        }
      }
      if (info && info.defaultBranch && !branches[r]) {
        branches[r] = info.defaultBranch
        changed = true
      }
    })
    if (!changed) return
    const next = { ...docsScope, branches }
    // code-review P2: mirror the ref — the hydration touched-branch
    // serializes docsScopeRef.current (never the effect closure), so a
    // healed/reset value must be visible there or a team-switch persist
    // could re-persist the stale branch it just healed.
    docsScopeRef.current = next
    setDocsScope(next)
    // #1893 (code-review P2): persist the healed value so the server
    // converges — a stale persisted branch would otherwise re-hydrate +
    // re-heal on every session (UI-only fix; the stored value never
    // converges until the next manual docs toggle). Gated on the persist
    // gate (post-hydration) so the un-seeded default is never written.
    if (shouldPersist(scopeReadyRef.current)) {
      persistScope({ github_docs_scope: serializeDocsScope(next) })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [branchLists, docsScope])
  // Load once when the team is connected (re-connect/rotation re-loads via the
  // connected-flip guard below). reposLoaded marks the attempt so a failed load
  // doesn't retry on every render.
  const githubConnected = !!(onboarding && onboarding.github_connected)
  const prevConnectedRef = React.useRef(false)
  React.useEffect(() => {
    if (githubConnected && (!reposLoaded || reposLoadFailed || (githubConnected && !prevConnectedRef.current))) {
      loadRepos()
    }
    prevConnectedRef.current = githubConnected
    // #1893 (code-review P2, round-4): currentTeamId in deps — a team switch
    // while the PREVIOUS team's repos fetch is in-flight leaves the reset in
    // switchTeam a no-op (reposLoaded/reposLoadFailed already false) and the
    // stale response is dropped by the identity guard, so without the team dep
    // the effect never re-fires and the NEW team's selectors stay empty (every
    // toggle silently fails to persist). The team dep re-fires loadRepos with
    // teamIdRef already updated to the new team; githubConnected (stale until
    // refreshOnboarding resolves) keeps the transient fetch gated to the
    // connected surface, and the _teamAtCall guard drops any stale response.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [githubConnected, reposLoaded, reposLoadFailed, currentTeamId])
  // #1893 (code-review P2): a repos-fetch failure must not permanently close
  // the persist gate — once onboarding resolves, the user's REAL toggles
  // should still persist (only PRUNING stays gated on reposLoadFailed via
  // shouldHydrate; a failed fetch is never evidence of an empty org). The
  // connected-flip effect above retries loadRepos when reposLoadFailed flips
  // (bounded: one extra attempt per flip — a second consecutive failure
  // leaves both flags unchanged, so no re-render/no loop). Hydration re-arms
  // on the next successful fetch (reload or reconnect also re-attempts).
  React.useEffect(() => {
    // #1893 (code-review P2): gate the reopen on the switch-stale flag too —
    // during a team switch, onboarding is still the OLD team's object and
    // opening the persist gate would persist a selection built against the
    // un-seeded default under the NEW team's id.
    if (onboarding && currentTeamId && reposLoadFailed && !scopeReadyRef.current && !onboardingStaleRef.current) {
      scopeReadyRef.current = true
    }
  }, [onboarding, currentTeamId, reposLoadFailed]) // eslint-disable-line react-hooks/exhaustive-deps

  async function toggleSessionRecording(next) {
    if (memoryBusy) return
    setMemoryBusy('sessions')
    setRowError('sessions', '')
    try {
      // #1927: session_recording is the off-switch (default ON) — the toggle
      // writes the flag only (the re-ask machinery it used to feed is gone).
      // PATCH MERGE: no read-modify-write, no stale reads.
      await api(`/v1/onboarding/state${onboardingTeamQ()}`, { method: 'PATCH', useSession: true,
        body: JSON.stringify({ session_recording: next }) })
      await refreshOnboarding()
    } catch (e) {
      setRowError('sessions', (e && e.message) || 'Could not update session capture — try again.')
    } finally {
      setMemoryBusy('')
    }
  }

  async function toggleIssues(next) {
    if (memoryBusy) return
    if (!next) {
      // off: PATCH the display flag (no server-side disconnect exists —
      // re-enabling re-runs the OAuth connect).
      setMemoryBusy('issues')
      setRowError('issues', '')
      setIssuesWantOn(false)
      try {
        await api(`/v1/onboarding/state${onboardingTeamQ()}`, { method: 'PATCH', useSession: true,
          body: JSON.stringify({ github_connected: false }) })
        await refreshOnboarding()
      } catch (e) {
        setRowError('issues', (e && e.message) || 'Could not update GitHub issues — try again.')
      } finally {
        setMemoryBusy('')
      }
    } else if (onboarding && onboarding.github_connected) {
      // already connected → re-poll the diff (in-flight single-flight reuse)
      reindexGithub()
    } else {
      // on-but-not-connected: the row renders the inline Connect CTA
      setIssuesWantOn(true)
    }
  }

  async function toggleDocs(next) {
    if (memoryBusy) return
    setDocsWantOn(next)
    setRowError('docs', '')
    if (next && onboarding && onboarding.github_connected && !(onboarding.github_docs_indexed)) {
      // toggle-on reveals the explicit Index-docs action (T1-P7) — the user
      // presses it to run the job (auto-running would surprise); the row
      // already shows the action button when docsWantOn.
    }
  }

  async function reindexGithub() {
    if (indexBusy) return
    setIndexBusy(true)
    setRowError('issues', '')
    setIndexJob({ status: 'starting' })
    try {
      // #1845: send the selected repo scope (list of SHORT names) — empty =
      // all repos (org-wide diff).
      const body = buildIssuesJobBody(issuesScope)
      const res = await api(`/v1/index/github/re-poll${onboardingTeamQ()}`, { method: 'POST', useSession: true,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body) })
      const jobId = res && res.job_id
      if (!jobId) throw new Error('index job did not return a job id')
      setIndexJob({ status: 'started', job_id: jobId })
      startBoundedPoll(indexPollRef, {
        url: `/v1/index/github/${jobId}`,
        isTerminal: (j) => j && (j.status === 'completed' || j.status === 'failed'),
        onStatus: setIndexJob,
        // #1894: refresh onboarding state on terminal so the newly-stamped
        // github_indexed_at appears WITHOUT a manual reload.
        onDone: (job) => { setIndexJob(job); refreshOnboarding().catch(() => {}) },
        // #1894: docs re-index ≈90s is tight at the 40×3s default; index
        // jobs report live progress, so give them a 300s window (bounded).
        maxTries: 100,
      })
    } catch (e) {
      setRowError('issues', (e && e.message) || 'Could not start GitHub indexing — try again.')
      setIndexJob(null)
    } finally {
      setIndexBusy(false)
    }
  }

  async function indexDocs() {
    if (docsBusy) return
    setDocsBusy(true)
    setRowError('docs', '')
    setDocsJob({ status: 'starting' })
    try {
      let org
      try {
        const gs = await api(`/v1/onboarding/github/status${onboardingTeamQ()}`, { useSession: true })
        org = gs && gs.org
      } catch { /* org stays undefined — the server 400s "org is required" and the row error surfaces it */ }
      // #1845: per-repo scope list — each selected repo carries its own
      // branch ('' = default main/master fallback, 'all' = every branch).
      // Empty repos = ALL repos (org-wide, default branch). #1893: pure
      // builder (sourceScope.js) — omit-empty contract node-tested.
      const payload = buildDocsJobBody(docsScope, org)
      const res = await api(`/v1/index/docs${onboardingTeamQ()}`, { method: 'POST', useSession: true,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload) })
      const jobId = res && res.job_id
      if (!jobId) throw new Error('docs job did not return a job id')
      setDocsJob({ status: 'started', job_id: jobId })
      startBoundedPoll(docsPollRef, {
        url: `/v1/index/docs/${jobId}`,
        isTerminal: (j) => j && (j.status === 'completed' || j.status === 'failed'),
        onStatus: setDocsJob,
        // #1894: refresh onboarding state on terminal so the newly-stamped
        // github_docs_indexed_at appears WITHOUT a manual reload.
        onDone: (job) => { setDocsJob(job); refreshOnboarding().catch(() => {}) },
        // #1894: 300s bounded window for live-progress index jobs.
        maxTries: 100,
      })
    } catch (e) {
      setRowError('docs', (e && e.message) || 'Could not start docs indexing — try again.')
      setDocsJob(null)
    } finally {
      setDocsBusy(false)
    }
  }

  // #1927: the misled-user re-ask (YES / NO answer pane) was removed with the
  // consent gate — session recording is default-ON and ToS-covered, so there
  // is no exactly-once gate and no answer path.
  async function wizardConnectGithub() {
    setWizardGithub((g) => ({ ...g, busy: true }))
    setRowError('issues', '')
    try {
      const res = await api(`/v1/onboarding/github/connect${onboardingTeamQ()}`, { method: 'POST', useSession: true })
      const authUrl = (res && (res.auth_url || res.authorize_url)) || null
      if (!authUrl) { setWizardGithub((g) => ({ ...g, busy: false })); return }
      const win = window.open(authUrl, '_blank')
      if (!win) {
        // Popup blocked — no poll to run; reset immediately (review P1).
        setWizardGithub((g) => ({ ...g, busy: false }))
        setRowError('issues', 'Popup blocked — allow popups for app.premiselabs.co and try again.')
        return
      }
      // Poll status until the OAuth round trip completes (the callback
      // redirects to welcome.html, not here). Bounded (tries + terminal
      // short-circuit, handle in a ref, per-team guard) — the old 120s
      // dangling setTimeout is gone.
      startBoundedPoll(wizardGithubPollRef, {
        url: `/v1/onboarding/github/status${onboardingTeamQ()}`,
        isTerminal: (st) => st && st.connected,
        onStatus: (st) => {
          if (st && st.connected) {
            setWizardGithub({ connected: true, repos: st.repos_count, busy: false, org: st.org })
          }
        },
        onDone: (st) => {
          setWizardGithub((g) => ({ ...g, busy: false }))
          if (st && st.connected) {
            setIssuesWantOn(true)
            api(`/v1/onboarding/state${onboardingTeamQ()}`, { method: 'PATCH', useSession: true,
              body: JSON.stringify({ github_connected: true }) }).catch(() => {})
            refreshOnboarding().catch(() => {})
            // connected+indexing: the OAuth callback auto-enqueues the
            // first run — surface it via the re-poll (single-flight reuse
            // returns the in-flight job).
            reindexGithub()
          } else {
            setRowError('issues', "GitHub connect didn't finish — check that you authorized the app, then try again.")
          }
        },
      })
    } catch (e) {
      stopGithubPoll()
      setWizardGithub((g) => ({ ...g, busy: false }))
      setRowError('issues', (e && e.message) || 'Could not start GitHub connect.')
    }
  }

  // #1680: returning users reopen the wizard via the Setup nav — the seed
  // inputs aren't prefilled by the first-timer provisioning path, so derive
  // the defaults from the session when the seed step is first entered
  // (ONCE — a deliberate clear + Back/Next must not re-populate).
  // #1997 (W1): the seed step is ARCHIVED — this effect must not fire on the
  // live connect step (wizardStep 3 is now connect-consent, not seed); gate
  // it on LEGACY_WIZARD_ARCHIVED so the A0 rollback path stays clean
  // (review P2).
  const seedPrefilledRef = React.useRef(false)
  React.useEffect(() => {
    if (!LEGACY_WIZARD_ARCHIVED) return
    if (wizardStep === 3 && !seedPrefilledRef.current && !wizardSubject) {
      seedPrefilledRef.current = true
      const meta = sessionMetaRef.current || {}
      const s = meta.display_name || (meta.email ? meta.email.split('@')[0] : '') || 'me'
      setWizardSubject(s)
      setWizardProject(welcomeTeamName || currentTeamName || 'my-project')
    }
  }, [wizardStep]) // eslint-disable-line react-hooks/exhaustive-deps

  async function wizardSeedGraph() {
    setWizardSeeding(true)
    setWizardSeedError('')  // #1907: a retry must not keep showing the stale error
    try {
      // #1660/ontology: the STATE sample — the user's Subject + their
      // Project as the first graph entities, wired by a statement Point
      // (aboutObject on the project). Idempotent server-side.
      const subjectName = (wizardSubject || 'me').trim() || 'me'
      const projectName = (wizardProject || 'my-project').trim() || 'my-project'
      const subj = await api('/v1/subjects', { method: 'POST', useSession: true,
        body: JSON.stringify({ name: subjectName, subjectKind: 'person' }) })
      const proj = await api('/v1/objects', { method: 'POST', useSession: true,
        body: JSON.stringify({ name: projectName, objectKind: 'project', status: 'in_progress' }) })
      const p = await api('/v1/points', { method: 'POST', useSession: true,
        body: JSON.stringify({ content: `${subjectName} is building ${projectName}`, kind: 'statement',
                               about_object: proj.id, tags: ['onboarding'], dedup: true }) })
      setWizardSeedDone(!!(subj && subj.id && proj && proj.id && p && p.id))
      // #1906: the seeded point must land on the Overview without waiting
      // for reload — team.point_count was captured at provisioning (0).
      // Load-bearing for the header-exit race ('Open my dashboard →' is
      // available on every wizard step): finishWelcomeLoads' refreshTeam
      // can snapshot 0 while the seed commit is in flight; this post-seed
      // refire lands the correct count. Bump the refresh seq so any
      // in-flight pre-seed team response is dropped (code-review P2).
      teamRefreshSeqRef.current += 1
      refreshTeam('', undefined, teamRefreshSeqRef.current).catch(() => {})
      // #1691: reflect the subject in the account username (display_name)
      // — best-effort; the graph Subject is the source of truth.
      if (subj && subj.id && supabaseClient) {
        supabaseClient.auth.updateUser({ data: { display_name: subjectName } }).catch(() => {})
      }
      setWizardSeeding(false)
    } catch (e) {
      setWizardSeeding(false)
      // #1907: the global error banner is invisible in welcome mode — render
      // the failure inline in the seed step instead (button re-enables → retry).
      setWizardSeedError((e && e.message) || 'Could not seed your graph — try again.')
    }
  }

  // #1842 P1 (re-review): the post-welcome load sequence, shared by
  // wizardComplete AND the welcome header's "Open my dashboard →" button
  // (which previously only setWelcomeMode(false), bypassing the loads —
  // currentTeamId stayed null, the currentTeamId effect never fired
  // loadMembers/loadGraphs, and Graphs/Users/Backups shimmered forever on
  // the first-timer path). loadTeams' Round-8 fallback pins currentTeamId
  // (firing that effect); AWAIT it first so loadBackups' staleness guard
  // (teamIdRef at call time) matches, then load the key-scoped backups with
  // the just-revealed key. Idempotent: the Round-8 pin guards on
  // teamIdRef.current, so a call when currentTeamId is already set never
  // re-fires the currentTeamId effect (no duplicated members/graphs loads);
  // the setTeams refresh + loadBackups re-fetch are harmless.
  async function finishWelcomeLoads(tabOverride) {
    await loadTeams().catch(() => {})
    loadBackups('').catch(() => {})
    // #1906: the first-timer path never ran loadAll (keys+sessions) — the
    // Overview 'API Keys' card stayed 0 until reload. loadTeams just
    // pinned currentTeamId (Round-8), so loadAll's ?team_id= targets the
    // new team. Fire-and-forget like completeLogin's card loads; each
    // loader carries its own staleness guard.
    loadAll('').catch(() => {})
    // #1906: refetch the team so the Overview 'Data points' card reflects
    // the seeded graph — team.point_count was captured at provisioning
    // (pre-seed, 0). Also covers the header-exit-without-seed case (0
    // stays 0 — honest).
    // Pass the current refresh seq: if a seed refire bumps it mid-flight,
    // this pre-seed response is dropped (it must not clobber the count).
    refreshTeam('', undefined, teamRefreshSeqRef.current).catch(() => {})
    // #2528: sync the URL hash to the target tab — exits from welcome that
    // navigate to API Keys pass 'keys' through tabOverride so the replaceState
    // uses the correct tab even though the closure holds 'overview'.
    const tabToUse = tabOverride || tab
    if (window.location.hash !== '#/' + tabToUse) {
      window.history.replaceState({ tab: tabToUse }, '', '#/' + tabToUse)
    }
    // #1847/#2323 (Option B): re-fire the onboarding-state load NOW that the
    // team exists — the mount-time refreshOnboarding() fired BEFORE the
    // org-create submit provisioned the team (name-first, tenant-provision)
    // and 403'd 'No team membership' for first-timers, leaving the Overview
    // MemorySources panel on its error card until reload. The team now
    // exists → this fetch succeeds → the toggles render on the first view.
    await refreshOnboarding().catch(() => {})
  }

  async function wizardComplete() {
    setWizardDone(true)
    setWizardPaused(false)
    connectedOnceRef.current = false
    // #2195/#2246 (durable connect key): the done step ends the connect flow —
    // the minted/pasted durable plaintext was shown once in the command; drop
    // it so a later re-entry re-gates (a revoked/regenerated key never
    // re-embeds stale). Nothing persists — the browser never holds the key
    // (#2246): this clears the wizard's in-memory shown-once state.
    setWizardDurableKey('')
    setWizardDurablePaste('')
    setWizardDurableError('')
    setWizardShowPaste(false)    // #2361 review-r2: welcomeKey is the first-timer provisioned plaintext
    // #2710: the wizard never owns the shared key-create modal — clear any
    // queued flag on the way out so it cannot surface on the dashboard. The two
    // paths that clear it are this one (wizardComplete) and the welcome header's
    // "Open my dashboard →" escape. The wizard's other `setWelcomeMode(false)`
    // sites (the build fork's "Manage API keys →" and the archived legacy
    // tree's escapes) do not — harmless today because the wizard has no
    // `setKeyModalOpen(true)` call site left at all (pinned by
    // wizardConnectTripwire.test.js), so nothing can queue it in the first
    // place. Belt-and-braces, deliberately not extended (code-review P2).
    setKeyModalOpen(false)
    setKeyModalStage('form')
    // (owner-only by construction — members never mint) — drop it here too so
    // a later resume re-gates to the rows-aware copy instead of re-showing a
    // 'shown once' key under a fresh 'shown once' disclosure.
    setWelcomeKey('')
    // #1997 (W1): the legacy jsonb completion write is REMOVED — the done
    // step hands off to the graph (accept-and-drop makes a client PATCH
    // inert on node-present orgs; the node's fork-aware gate owns
    // onboarding_complete). The wire follows the node.
    window.history.replaceState({}, '', '#/' + tab)
    setWelcomeMode(false)
    // #1842 P1-1: the first-timer flow (org-create provision → welcome
    // wizard → wizardComplete) never ran loadTeams/loadBackups — those fired
    // only in completeLogin/switchTeam (returning users). currentTeamId
    // stayed null → the currentTeamId effect never fired loadMembers/
    // loadGraphs → Graphs/
    // Users/Backups shimmered forever after "Open my dashboard →".
    await finishWelcomeLoads()
  }

  // #1566: in-app first-time provisioning (ported from welcome.html).
  // The tenant-provision edge function authorizes the app origin; the
  // membership row + the key are created here. The raw tt_ key NEVER leaves
  // the app origin (#1082) — it is revealed here exactly once (atomic
  // reveal+null, A13) and shown in the welcome card.
  async function provisionInApp(session, teamName = '') {
    setWelcomeProvisioning(true)
    setWelcomeProvisionError('')
    // #2323 (code-review P2): use the LIVE session — the org-create submit
    // can happen long after mount (an onboarding tab left open past token
    // expiry), so the mount-captured token may no longer authenticate.
    // supabase-js getSession() returns a fresh token transparently.
    try {
      const { data: live } = await supabaseClient.auth.getSession()
      if (live && live.session && live.session.access_token && live.session.user) {
        session = live.session
      }
    } catch { /* keep the passed session when getSession fails */ }
    // #1082 double-provision guard (fail-closed, mirrors welcome.html's
      // claimStatusGuard): a tt_claim_pending marker means a claimable anon
      // team may exist — never mint a stray team over it.
      if (/(?:^|; )tt_claim_pending=/.test(document.cookie)) {
        // #1566 (code-review P2): the guard must NOT dead-end — offer the
        // claim card (the welcome.html 'Go claim my team' pattern).
        setWelcomeProvisionError(
          'You have an anonymous organization waiting to be claimed — attach your ' +
          'GitHub or Google identity to claim it (same key, same graph).')
        return { routedAway: true }
      }
      const userId = (session.user && session.user.id) || ''
      const meta = (session.user && session.user.user_metadata) || {}
      const isLocal = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1'
      const provisionUrl = isLocal
        ? 'http://127.0.0.1:54321/functions/v1/tenant-provision'
        : SUPABASE_URL + '/functions/v1/tenant-provision'
      const callProvision = () => fetch(provisionUrl, {
        method: 'POST',
        headers: {
          'Authorization': 'Bearer ' + session.access_token,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          user_id: userId,
          email: (session.user && session.user.email) || '',
          ...(meta.display_name ? { display_name: meta.display_name } : {}),
          // #2323 (Option B): name-first — the wizard-typed org name. The
          // edge fn validates it (same regex as the server) and falls back to
          // display-name derivation when absent (older callers).
          ...(teamName ? { team_name: teamName } : {}),
        }),
      })
      // Attempt 1 + exactly ONE retry (a second mint = a second team).
      let response = null
      for (let attempt = 0; attempt < 2; attempt++) {
        try { response = await callProvision() } catch { response = null }
        if (response && response.ok) break
        if (attempt === 0) await new Promise(r => setTimeout(r, 1000))
      }
      if (response && response.status === 401) {
        // #1511 semantic, ported: a 401 from tenant-provision means the
        // session is stale/invalid — welcome must never render for
        // unauthenticated users. Clear the session and go to /auth.
        if (typeof window.clearStoredSession === 'function') window.clearStoredSession()
        // #1860 (P3-5): preserve the search params — /auth's OAuth-error
        // banner reads ?error=... (the mount gate already passes
        // window.location.search on its bounce; the bare call here dropped
        // them, so an OAuth failure during provisioning silently lost the
        // banner's cause). #1909: an error FRAGMENT rides along too.
        if (typeof window.bounceToAuth === 'function') window.bounceToAuth(window.location.search, oauthErrorHash())
        // #1860 (P3-5, review P2-1): the degraded fallback must preserve the
        // params too — mirror the mount gate's fallback exactly, or the
        // OAuth-error banner's cause is lost precisely when the bridge is
        // blocked/unavailable.
        else window.location.replace('https://tortoise.premiselabs.co/auth' + window.location.search + oauthErrorHash())
        return { routedAway: true }
      }
      if (response && response.ok) {
        // The function wrote the membership row before answering — re-query
        // and reveal through the canonical path (atomic reveal+null, A13).
        try {
          for (let attempt = 0; attempt < 3; attempt++) {
            // #1566 (review P2): port the welcome.html poll shape — status
            // filter + newest row, so placeholder (team_id='') and M:N rows
            // can't error the poll (PGRST116).
            const { data, error } = await supabaseClient
              .from('team_memberships')
              .select('team_id, team_name, graph_name, status')
              .eq('user_id', userId)
              .eq('status', 'active')
              .order('created_at', { ascending: false })
              .limit(1)
              .maybeSingle()
            if (!error && data && data.status === 'active' && data.team_id) {
              const { data: key, error: rErr } = await supabaseClient
                .rpc('reveal_api_key', { p_user_id: userId, p_team_id: data.team_id })
              if (rErr) return null
              if (!key || key === 'pending') {
                // Already consumed (a prior reveal elsewhere) — no re-reveal.
                return { api_key: '', team_name: data.team_name, graph_name: data.graph_name }
              }
              return { api_key: key, team_name: data.team_name, graph_name: data.graph_name }
            }
            await new Promise(r => setTimeout(r, 1000))
          }
        } catch {
          // #1566 (code-review P2): a transport error must NOT leave the
          // provisioning spinner forever — fall through to the error card.
          return null
        }
        // The membership write may have failed despite 201 — the 201 body is
        // the only other copy of the plaintext.
        try {
          const body = await response.json()
          if (body && body.api_key && body.team_name) {
            return { api_key: body.api_key, team_name: body.team_name, graph_name: body.graph_name || '' }
          }
        } catch { /* fall through */ }
        return null
      }
      return null
  }

  // #2167 (rule 1): the bootstrap-mint helper (mintSessionKey, four callers:
  // mount / switchTeam / switchTeam 401-re-mint / revokeKey re-mint) is
  // DELETED — the dashboard never auto-mints a 24h session credential; the
  // POST /v1/session/key endpoint + the recovery purpose stay for
  // non-dashboard consumers (selfhost keyless teams, SDK/CLI, and the
  // contract suites are untouched; the dashboard's own recovery-purpose
  // POSTer, recoverKey, was UI-dead and is deleted in #2246). The
  // mintTripwire.test.js static guard pins this.

  React.useEffect(() => {
    ;(async () => {
      try {
        // #1177: stash + strip ?invite_token= UNCONDITIONALLY (before the
        // session guard) so signed-out invitees don't leave the token in the
        // URL bar/history/server logs while they sign in. The accept fires
        // when a session materializes (see acceptStashedInvite below).
        const inviteTokenParam = new URLSearchParams(window.location.search).get('invite_token')
        if (inviteTokenParam) {
          try { sessionStorage.setItem(INVITE_TOKEN_STORAGE, inviteTokenParam) } catch { /* best-effort */ }
          window.history.replaceState({}, '', window.location.pathname + window.location.hash)
        }
        const stashedInvite = (() => {
          try { return sessionStorage.getItem(INVITE_TOKEN_STORAGE) || '' } catch { return '' }
        })()
        const acceptStashedInvite = async (accessToken) => {
          if (!stashedInvite || !accessToken) return
          try {
            const inviteRes = await fetch(`${API_BASE}/v1/invites/accept`, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${accessToken}` },
              body: JSON.stringify({ token: stashedInvite }),
            })
            if (inviteRes.ok) {
              try { sessionStorage.removeItem(INVITE_TOKEN_STORAGE) } catch { /* best-effort */ }
              setBanner('Welcome to the organization! Your membership is active.')
              // #2538: propagate the accepted invite to wizard state so
              // loadTeams fires and the welcomeHasOrg chain triggers the
              // dashboard route guard (invited users skip onboarding).
              await loadTeams().catch(() => {})
            } else {
              let inviteMsg = `Could not accept invite (HTTP ${inviteRes.status}).`
              try {
                const b = await inviteRes.json()
                inviteMsg = apiErrorText(inviteRes.status, b) || inviteMsg
              } catch { /* non-JSON body */ }
              if (inviteRes.status !== 409) { // already a member — not an error worth a banner
                setBanner(inviteMsg)
              }
              try { sessionStorage.removeItem(INVITE_TOKEN_STORAGE) } catch { /* best-effort */ }
            }
          } catch (e) {
            setBanner((e && e.message) || 'Could not accept invite — try again.')
          }
        }

        if (!supabaseClient) {
          // #1511 (code-review P2): the head gate may pass on a valid cookie
          // while the auth library failed to load (blocked CDN/vendor script,
          // offline) — the eternal "Redirecting to the sign-in page…" shell
          // would never redirect. Surface an actionable error instead.
          setChecking(false)
          setAuthUnavailable('Could not load the sign-in library — check your connection and refresh.')
          return
        }
        const { data: { session }, error } = await supabaseClient.auth.getSession()
        if (error || !session || !session.expires_at || session.expires_at * 1000 <= Date.now()) {
          // #1511: NO strictly-valid session (missing OR past expires_at =
          // invalid — the presence-over-validity bug class) → the dashboard
          // never shows auth UI. In-flight claim-intent (paste tt_ → OAuth →
          // claim; D2) renders the claim-paste screen; everyone else goes to
          // /auth via the origin-aware bounceToAuth (Back-proof). The
          // storedKey exemption is gone — a stored key is a "Last used" hint
          // on /auth, not a dashboard credential.
          const claimIntent = claimIntentInFlight()
          if (!claimIntent) {
            // #1224/#1566: OAuth state-expiry errors land as ?error=… (or,
            // #1909, as #error=… in the fragment) on the app origin now —
            // preserve the SEARCH and any ERROR fragment so /auth renders the
            // banner (never a live #access_token fragment: it must not be
            // re-ingested by the destination).
            if (typeof window.bounceToAuth === 'function') window.bounceToAuth(window.location.search, oauthErrorHash())
            else window.location.replace('https://tortoise.premiselabs.co/auth' + window.location.search + oauthErrorHash())
            return
          }
          // Claim-intent: render the claim-paste screen (no session, no team).
          // #1909: a denied claim OAuth round-trip returns with
          // ?claim=1#error=… — surface the reason on the paste screen
          // (fragment params, not just search).
          const urlErr = oauthErrorParams()
          if (urlErr.error || urlErr.error_code) {
            const code = urlErr.error_code || ''
            const desc = urlErr.error_description || ''
            setClaimError(
              code === 'bad_oauth_state' || /state/i.test(desc)
                ? 'Your sign-in session expired while you were on the claim screen. Please try again.'
                : 'Sign-in failed' + (desc ? `: ${desc}` : '. Please try again.'))
          }
          setAuthMode('apikey')
          setChecking(false); return
        }
        sessionTokenRef.current = session.access_token
        setSessionBooted(true)
        sessionMetaRef.current = (session && session.user) ? {
          display_name: (session.user.user_metadata && session.user.user_metadata.display_name) || '',
          email: session.user.email || '',
        } : null
        // #2246 (ADR-010): session-authed users never hold an API key. The
        // legacy localStorage slot is residue now — never read, never probed;
        // purge it ONCE per session mount (the issue: "at most inert residue
        // that gets cleaned") and zero the apiKey state so no render path can
        // act on stored material. The anon/key-login claim branch returned
        // earlier (authMode 'apikey' at L2422) — this cleanup is session-only.
        try { localStorage.removeItem(KEY_STORAGE) } catch { /* best-effort */ }
        setApiKey('')
        apiKeyRef.current = ''
        // #1567: the session is valid — render the app chrome NOW and let the
        // mint + loads hydrate in the background (the multi-second
        // "Checking your session…" card is gone for session holders). The
        // #1559 error paths below setAuthed(false) so the error card still
        // replaces the chrome on failure.
        setAuthed(true)
        setChecking(false)
        // Round-6 (P2): supabase-js auto-refreshes the access token (~1h) into
        // the cookie — keep the ref in sync so JWT-scoped calls never die with
        // a stale token while the dashboard still looks logged in.
        const { data: authSub } = supabaseClient.auth.onAuthStateChange((_evt, s) => {
          if (s?.access_token) {
            sessionTokenRef.current = s.access_token
            // #1177: signed-out invitee completed sign-in → accept the stashed invite.
            if (_evt === 'SIGNED_IN') acceptStashedInvite(s.access_token)
          } else if (_evt === 'SIGNED_OUT') {
            // #2246 (ADR-010): the old key-auth fallback that kept a
            // signed-out tab coherent is gone (the browser never holds a key) —
            // a cross-tab/expired sign-out must not leave the stale-data
            // zombie shell. Mirror logout(): null the ref FIRST (in-flight
            // Round-9/12 guards key off it), flip authed, bounce to /auth.
            sessionTokenRef.current = null
            setTeams([])
            setAuthed(false)
            if (typeof window.clearStoredSession === 'function') window.clearStoredSession()
            if (typeof window.bounceToAuth === 'function') window.bounceToAuth()
            else window.location.replace('https://tortoise.premiselabs.co/auth')
          }
        })
        authSubRef.current = authSub?.subscription || null

        // #1082 (PR1): ?claim=1 claim-intent routing — the OAuth redirect
        // lands here with the pasted key in sessionStorage (same-tab PKCE).
        // POST /v1/claim BEFORE any provisioning: the welcome page's
        // Phase-2 mint is never reached (redirectTo targets the dashboard
        // claim route, NOT welcome.html), so the claimable anon team is
        // never orphaned by a stray mint.
        // #1177: signed-in invitee at mount → accept now (signed-out path is
        // handled by onAuthStateChange SIGNED_IN above).
        if (stashedInvite && session.access_token) {
          await acceptStashedInvite(session.access_token)
        }

        const claimParam = new URLSearchParams(window.location.search).get('claim')
        if (claimParam === '1') {
          let claimKeyStored = ''
          try { claimKeyStored = sessionStorage.getItem(CLAIM_KEY_STORAGE) || '' } catch { /* best-effort */ }
          // sessionStorage is same-tab/same-origin — the OAuth redirect
          // returns to this dashboard origin, so the key is always here.
          if (claimKeyStored.startsWith('tt_') && session.access_token) {
            try {
              const claimRes = await performClaim(session.access_token, claimKeyStored)
              if (claimRes.ok) {
                try { sessionStorage.removeItem(CLAIM_KEY_STORAGE) } catch { /* best-effort */ }
                clearClaimPendingMarker()
                setClaimKey('')
                // Strip ?claim=1 so a reload doesn't re-claim.
                window.history.replaceState({}, '', window.location.pathname)
              } else {
                // #1719 (Task 6): render the dict message (never raw JSON)
                // and the unavailable copy for 5xx (control-plane outage).
                let claimMsg = `Claim failed (HTTP ${claimRes.status}).`
                try {
                  const b = await claimRes.json()
                  claimMsg = apiErrorText(claimRes.status, b) || claimMsg
                } catch { /* non-JSON body */ }
                setClaimError(claimMsg)
                // #1493: a FAILED claim must not leave the tt_claim_pending
                // marker behind — it would hijack the next /auth visit's
                // OAuth redirect + signed-in bounce to the claim route for
                // the full cookie TTL (up to 1h). Clear on failure/abandon.
                clearClaimPendingMarker()
                // #1511 (code-review r2, P2): strip the claim state so a
                // reload does NOT silently re-run the failed claim — the
                // error banner (authed shell) shows the message once.
                try { sessionStorage.removeItem(CLAIM_KEY_STORAGE) } catch { /* best-effort */ }
                window.history.replaceState({}, '', window.location.pathname)
              }
            } catch (e) {
              setClaimError((e && e.message) || 'Claim failed — try again.')
              clearClaimPendingMarker()
              try { sessionStorage.removeItem(CLAIM_KEY_STORAGE) } catch { /* best-effort */ }
              window.history.replaceState({}, '', window.location.pathname)
            }
          } else {
            // #1511 (code-review r2, P3): a valid session + ?claim=1 + NO
            // claim key = stale claim state (the /auth ANON-funnel gate can't
            // see the app-origin sessionStorage key, so it may bounce a
            // signed-in visitor here). Proceed silently — clear the markers
            // and strip the param; the claim-paste screen (pre-session) is
            // the only place 'Claim interrupted' makes sense.
            clearClaimPendingMarker()
            try { sessionStorage.removeItem(CLAIM_KEY_STORAGE) } catch { /* best-effort */ }
            window.history.replaceState({}, '', window.location.pathname)
          }
        }

        // List memberships up front so the mint targets a concrete team
        // (P1: multi-membership users cannot mint without team_id).
        let teamsList = []
        let teamsSuspendDetail = null
        try {
          const teamsRes = await fetch(`${API_BASE}/v1/teams`, {
            headers: { Authorization: `Bearer ${session.access_token}` },
          })
          if (teamsRes.ok) {
            teamsList = await teamsRes.json()
          } else if (teamsRes.status === 403) {
            // #2167 (rule 9, F8 — round-2 reviewer P2): an ALL-suspended
            // membership set makes the server 403 the teams LIST itself
            // (list_my_teams raises the _suspended_detail() dict when every
            // membership is suspended — hosted_api.py). #2246 (ADR-010): this
            // catch is now the SOLE fresh-login suspension vector — the mount
            // stored-key probe + its adopt/5d branches were deleted, so the
            // 403 always lands here. Parse the 403 dict so the appeal CTA
            // renders instead of the generic teams error card.
            try {
              const b = await teamsRes.json()
              teamsSuspendDetail = suspendedFromDetail(b && b.detail)
            } catch {
              // non-JSON 403 body — fall through to the fail-closed generic
            }
          }
          if (teamsRes.ok && Array.isArray(teamsList)) {
            // Round-12: SIGNED_OUT during this fetch must not resurrect teams
            if (sessionTokenRef.current === session.access_token) setTeams(teamsList)
          } else {
            // #1566 (review P1): a 200 with a non-array body is NOT 'no
            // teams' — it must fail CLOSED, never flip an existing user
            // into a surprise provisioning/key rotation.
            // #1566 (code-review P1): a transient API failure is NOT 'no
            // teams' — fail CLOSED to the error card rather than flipping an
            // existing user into a surprise provisioning (key rotation).
            if (teamsSuspendDetail) {
              // #2167 round-3 (P3): the adjacent 200 branch re-checks the
              // session token (Round-12) — a SIGNED_OUT racing the 403 must
              // not land the blocking suspension card on an ended-session
              // tab; fall through to the tail's end-session handling instead.
              if (sessionTokenRef.current !== session.access_token) {
                setAuthed(false)
                setMountError('Your session ended — sign in again.')
                setChecking(false)
                return
              }
              setAuthed(false)
              setMountError(teamsSuspendDetail.message || 'Organization suspended')
              setSuspended(teamsSuspendDetail)
              setChecking(false)
              return
            }
            throw new Error('Could not load your organizations — try again.')
          }
        } catch (e) {
          // #1566 (review P2): fail CLOSED for any non-array/empty result —
          // the throw's premise is 'not a valid teams array'.
          if (!Array.isArray(teamsList) || !teamsList.length) {
            setAuthed(false)
            setMountError((e && e.message) || 'Could not load your organizations — try again.')
            setChecking(false)
            return
          }
        }

        // #2323 (Option B): a first-timer (valid session, NO teams) is NOT
        // provisioned at mount — a display-name phantom org would exist
        // before the user names anything (the two-org confusion this fix
        // removes). The wizard's org-create step IS the provisioning door:
        // its submit calls tenant-provision with the typed name (deterministic
        // team_id), and the welcome key + demo seed land on that one org.
        if (!teamsList.length) {
          if (sessionTokenRef.current === session.access_token) {
            // The welcome card must render: leave the checking state + mark
            // authed (the normal completeLogin path never runs for first-timers).
            sessionRef.current = session
            setChecking(false)
            setAuthed(true)
            setWelcomeMode(true)
            setWelcomeProvisioning(false)
            // #1660: prefill the archived seed step's Subject from the OAuth
            // identity now (no team exists to read); the Project resolves
            // once the org is created.
            {
              const m = (session.user && session.user.user_metadata) || {}
              setWizardSubject(m.display_name || (session.user && session.user.email ? session.user.email.split('@')[0] : '') || 'me')
            }
          }
          return
        }

        // #2246 (ADR-010): session-only landing. The #2167 stored-key probe
        // (the last key-authed browser fetch — the "rule-1 exemption") and its
        // adopt/drop/keep-suspended machinery are DELETED: a session-authed
        // browser never holds or acts on an API key. The KEY_STORAGE residue
        // was already purged at session resolution above; this tail only pins
        // the team and completes the session-only login — every overview read
        // rides the session JWT (#1828/#2167), and keys are MANAGED (create /
        // rotate / revoke / toggle from the API Keys table) but never held.
        // #1912: pin the first HEALTHY team BEFORE completeLogin so its team
        // reads go out pinned (q = '?team_id=...') — without the pin a
        // multi-membership user whose FIRST membership is suspended would 403
        // into the error card on every reload (the old 5b-adopt also pinned).
        // An ALL-suspended session 403s the /v1/teams fetch itself and renders
        // the appeal card there (round-2 reviewer F1) — the teams-fetch catch
        // above is that mechanism; no probe belt is needed.
        if (!teamIdRef.current && teamsList.length) {
          const firstHealthy = teamsList.find((t) => !t.suspended_at) || teamsList[0]
          if (firstHealthy) {
            setCurrentTeamId(firstHealthy.team_id)
            teamIdRef.current = firstHealthy.team_id
          }
        }
        // Round-9: a SIGNED_OUT during the mount must not complete the login
        // on the tab the user just signed out of.
        if (!sessionTokenRef.current) { setAuthed(false); setChecking(false); setMountError('Your session ended — sign in again.'); return }
        // completeLogin('') renders session-only — zero key state, zero slot
        // writes (the apiKey state was cleared at session resolution; a
        // falsy value must never land in localStorage, #1830/#2167).
        setAuthMode('session')
        await completeLogin('')
      } catch (e) {
        // #1559/#1567 (review P0): never leave the user on the silent
        // redirect shell — authed was set EARLY, so an escaped throw (e.g.
        // localStorage blocked in private mode during the mount / team load)
        // would otherwise leave mountError set-but-unrendered (the card is
        // !authed-gated). Flip authed so the card + Retry render.
        setMountError((e && e.message) || 'Something went wrong loading the dashboard — try again.')
        setAuthed(false)
        setChecking(false)
      }
    })()
  }, [])

  async function refreshTeam(key, expectedTeamId, seq) {
    // P1 (code-review): extracted team refetch — the success-return poll loop
    // used an undefined `jl` (dead code); this is the real refetch.
    // P2 (code-review): key param so the refetch targets the selected team —
    // the overview cards + header tier badge read /v1/team, which resolves
    // the team from the session + pinned ?team_id=.
    // #2246 (ADR-010): the read rides the SESSION JWT (dual-auth
    // get_current_team_session) + pinned ?team_id= so multi-membership users
    // resolve the SELECTED team, not the first membership. The key param is
    // unused in session mode — the key-only fallback callers (stored-key
    // reuse / switchTeam with a fresh mint) were deleted with the held key;
    // all call sites pass '' (kept for signature stability).
    const _teamAtCall = expectedTeamId || teamIdRef.current
    const q = _teamAtCall ? `?team_id=${encodeURIComponent(_teamAtCall)}` : ''
    const t = await api(`/v1/team${q}`, sessionTokenRef.current
      ? { useSession: true }
      : (key ? { headers: { Authorization: `Bearer ${key}` } } : {}))
    // Round-13/14 (P2): never land a team's data under a different team's
    // selection. Two guards:
    //  - expectedTeamId (checkout poll pin): null on Stripe-return loads
    //    (poll effect runs before bootstrap sets teamIdRef), so also...
    //  - response-identity: t.team_id vs teamIdRef.current — catches the
    //    null-pin case AND a stale closure key (poll captured team A's key,
    //    user switched to B → /v1/team with A's key returns A's data).
    if (expectedTeamId != null && teamIdRef.current !== expectedTeamId) return t
    if (t?.team_id && teamIdRef.current && t.team_id !== teamIdRef.current) return t
    // #1906 (code-review P2): optional monotonic seq — welcome-path callers
    // that must win over concurrent refreshes (the post-seed refire) tag
    // their call; a response tagged with a stale seq is dropped so a
    // pre-seed point_count can never clobber the post-seed count.
    if (seq != null && seq !== teamRefreshSeqRef.current) return t
    setTeam(t)
    return t
  }

  async function loadAlerts(tid) {
    // #308 (R7): session-authed alert history — reachable even while the
    // team is suspended (API-key routes 403 by design).
    const tok = sessionTokenRef.current
    if (!tok || !tid) return
    try {
      const res = await fetch(`${API_BASE}/v1/team/alerts?team_id=${encodeURIComponent(tid)}`, {
        headers: { Authorization: `Bearer ${tok}` },
      })
      if (res.ok) {
        const d = await res.json()
        setAlerts(d.alerts || [])
      }
    } catch { /* best-effort — alert history never blocks the dashboard */ }
  }

  async function completeLogin(key) {
    setError('')
    const teamAtCompleteLogin = teamIdRef.current
    // #1841: fire the overview card loads in PARALLEL with the /v1/team
    // gate — the old code awaited /v1/team FIRST (waterfall: team → keys/
    // sessions/teams/backups → members/graphs via the currentTeamId
    // effect), so every card sat blank during the team round-trip. Each
    // loader carries its own teamIdRef staleness guard, so racing them
    // against the team fetch is safe; /v1/team still owns setTeam +
    // setAuthed (the section gate) below. (loadGraphs/loadMembers ride
    // the currentTeamId effect, fired by the mount pin of the session-only
    // landing — #1912 first-healthy pin before completeLogin('') — and by
    // switchTeam.)
    const cardLoads = Promise.all([
      loadAll(key),
      loadTeams(),
      loadBackups(key),
    ]).catch(() => {})
    try {
      // #1828 (review) / #2246 (ADR-010): session-driven Team card — the
      // session-only landing's #1912 first-healthy pin fed completeLogin(''),
      // so the read goes out pinned ?team_id=<selected> and resolves the
      // SELECTED team, not the first membership (multi-membership users).
      // api() useSession is the only auth leg — no key fallback exists in
      // session mode (the held-key/stored-key callers are deleted).
      const q = teamAtCompleteLogin ? `?team_id=${encodeURIComponent(teamAtCompleteLogin)}` : ''
      const t = await api(`/v1/team${q}`, sessionTokenRef.current
        ? { useSession: true }
        : (key ? { headers: { Authorization: `Bearer ${key}` } } : {}))
      // #1567 (review P1): the chrome renders early, so a team switch can
      // land DURING this await — never land team A's data under team B's
      // selection (the refreshTeam response-identity guard, applied here).
      // The in-flight cardLoads land only where their own staleness guards
      // allow (the switch's loaders own the data under the new selection).
      if (t?.team_id && teamIdRef.current && t.team_id !== teamIdRef.current) return
      setTeam(t)
      setAuthed(true)
      loadAlerts(t?.team_id)  // fire-and-forget (#308 R7)
      await cardLoads
    } catch (e) {
      // #1856 P0: bail on staleness FIRST — a mid-flight team switch means
      // this catch belongs to the PREVIOUS team's request, and the switch's
      // own flow owns error state under the new selection. Running
      // setAuthed(false) before the bail left the user on the dead
      // "Redirecting to the sign-in page…" shell (mountError never set,
      // authed false, no navigation). The finally below still runs on the
      // return, so the busy/checking cleanup is preserved. Bails before
      // setSuspended too — a stale team's suspension must not stick to the
      // switched session.
      if (teamAtCompleteLogin !== null && teamIdRef.current !== teamAtCompleteLogin) return
      if (e && e.suspended) setSuspended(e.suspended)  // #308
      setError(e.message === 'Invalid API key' ? 'Invalid API key — check your key and try again.' : e.message)
      setAuthed(false)
      // #1559 (review P2): a /v1/team or load 5xx during the session-only
      // mount must NOT leave the silent redirect shell — same class as the
      // mount/load failure. The error card (mountError) is the only
      // renderable state.
      // #1719 (Task 6): 5xx → the honest unavailable copy (never a raw
      // "Internal server error" string).
      // #1842 P2-4: the #1830 null-key path captures teamAtCompleteLogin ===
      // null — loadTeams' Round-8 fallback can pin teamIdRef while /v1/team
      // is in flight, and the old `!==` guard then saw list[0] !== null and
      // bailed silently (stranded on the redirect shell). Only a REAL
      // mid-load switch (a non-null capture that moved) bails now.
      const errStatus = (e && e.status) || 0
      setMountError(errStatus >= 500
        ? UNAVAILABLE_COPY
        : (e && /429|rate limit/i.test(e.message))
          ? 'Too many requests from this network — try again in a minute.'
          : (e && e.message) || 'Could not load your dashboard — try again.')
    } finally {
      setBusy(false)
      setChecking(false)
    }
  }

  // #1148-ux review: OAuth login OR signup (Supabase auto-creates the account
  // on first sign-in — no need to discover which one you are).
  async function authProvider(provider) {
    if (!supabaseClient) { setError('Auth is not configured on this deployment.'); return }
    setError('')
    setAuthBusy(true)
    try { window.setLastAuthMethod(provider); setLastAuthMethod(provider) } catch { /* best-effort */ }
    try {
      const { data, error } = await supabaseClient.auth.signInWithOAuth({
        provider: provider,
        options: { redirectTo: `${window.location.origin}${window.location.pathname}` },
      })
      if (error) { setError(error.message || 'Sign-in failed — try again.') ; return }
      if (data?.url) { window.location.href = data.url }
    } catch (err) {
      setError((err && err.message) || 'Sign-in failed — try again.')
    } finally {
      setAuthBusy(false)
    }
  }

  // #1148-ux review: email+password login or signup
  async function authEmailPassword() {
    if (!supabaseClient) { setError('Auth is not configured on this deployment.'); return }
    setError('')
    setAuthBusy(true)
    try {
      let result
      if (authIsSignup) {
        // #1148 review P2: signup goes through the SERVER /v1/signup/email
        // (#801 admin-create, email_confirm=true — no SMTP bucket, no
        // confirmation email required), THEN logs in with the credentials.
        const sres = await fetch(`${API_BASE}/v1/signup/email`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ email: authEmail.trim(), password: authPassword }),
        })
        if (!sres.ok) {
          let msg = `Signup failed (HTTP ${sres.status}).`
          try {
            const b = await sres.json()
            msg = apiErrorText(sres.status, b) || msg
          } catch { /* non-JSON */ }
          setError(msg)
          return
        }
        result = await supabaseClient.auth.signInWithPassword({ email: authEmail.trim(), password: authPassword })
      } else {
        result = await supabaseClient.auth.signInWithPassword({ email: authEmail.trim(), password: authPassword })
      }
      if (result.error) {
        const m = result.error.message || ''
        if (m.includes('already registered') || m.includes('already been registered')) {
          setError('That email is already registered — log in instead, or continue with GitHub/Google.')
        } else if (m.includes('Invalid login')) {
          setError('Invalid email or password.')
        } else {
          setError(result.error.message || 'Something went wrong — try again.')
        }
        return
      }
      try { window.setLastAuthMethod('email'); setLastAuthMethod('email') } catch { /* best-effort */ }
      // #1148 review P2: the mount effect bootstraps only on first load —
      // reload so the mount re-resolves the fresh session and runs the
      // session-only landing (loadTeams → completeLogin(''), no bootstrap-key
      // mint at login).
      window.location.reload()
    } catch (err) {
      setError((err && err.message) || 'Something went wrong — try again.')
    } finally {
      setAuthBusy(false)
    }
  }

  // #1511: the key-paste `login()` handler was deleted — the dashboard never
  // shows a login/key-only screen (the claim-paste screen handles anon keys).
  // #1082 (PR1): claim-card handlers — attach a provider-verified identity
  // to an anon team (same key, same graph, memories intact).
  async function claimSignIn(provider) {
    setClaimError('')
    // #1511 (code-review P2): the claim uses the VISIBLE input (apiKey
    // state mirrored into the ref) — what's on screen is what's used.
    // #2246 (ADR-010): the apiKey initializer is zeroed and the legacy
    // KEY_STORAGE slot is purged on session mounts, so the paste box starts
    // EMPTY and holds exactly what the user pasted — no pre-filled stored
    // key antecedent. A wrong-team key 403s with the claim error. No hidden
    // localStorage fallback.
    const k = (apiKeyRef.current || '').trim()
    if (!k.startsWith('tt_')) {
      setClaimError('Paste your tt_ API key above, then connect a login to claim your organization.')
      return
    }
    if (!supabaseClient) {
      setClaimError('Auth is not configured on this deployment.')
      return
    }
    // Key survives the OAuth redirect via sessionStorage (same-tab PKCE
    // round-trip). NEVER in redirectTo — GoTrue puts it in the OAuth state
    // URL → leak. Raw key = sessionStorage only (P1-2). The non-secret
    // tt_claim_pending marker (cross-origin intent for welcome/signin/
    // signup routing) is set alongside — it carries NO credential.
    try { sessionStorage.setItem(CLAIM_KEY_STORAGE, k) } catch { /* best-effort */ }
    setClaimPendingMarker()
    try { window.setLastAuthMethod(provider); setLastAuthMethod(provider) } catch { /* best-effort */ }
    setClaimBusy(true)
    try {
      const redirectTo = `${window.location.origin}${window.location.pathname}?claim=1`
      const { data, error } = await supabaseClient.auth.signInWithOAuth({
        provider: provider, // github | google — provider-verified email invariant
        options: { redirectTo },
      })
      if (error) {
        setClaimError(error.message || 'Sign-in failed — try again.')
        setClaimBusy(false)
        return
      }
      if (data?.url) {
        // Same-tab redirect (sessionStorage survives); the popup flow would
        // lose the key — pinned in e2e.
        window.location.href = data.url
        return
      }
      setClaimBusy(false)
    } catch (err) {
      setClaimError((err && err.message) || 'Sign-in failed — try again.')
      setClaimBusy(false)
    }
  }

  async function claimEmailPassword() {
    // #1148-ux review: third identity option — attach email+password. The
    // server creates the Supabase auth user (admin API, #801 path) and links
    // the anonymous membership to it (claim_membership RPC) — same key, same
    // graph. Session key comes from the ref (already authed).
    setClaimError('')
    const k = (apiKeyRef.current || '').trim()  // #1511: pasted key required
    if (!k.startsWith('tt_') || !claimEmail.includes('@') || claimPassword.length < 6) {
      setClaimError('Enter a valid email and a password of at least 6 characters.')
      return
    }
    setClaimBusy(true)
    try {
      const res = await fetch(`${API_BASE}/v1/claim/email`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ api_key: k, email: claimEmail.trim(), password: claimPassword }),
      })
      if (res.ok) {
        // #1511 (code-review r2, P3 hygiene): a successful email claim must
        // clear the claim markers — a stale tt_claim_pending would hijack
        // the next /auth visit's redirect to the claim route.
        try { sessionStorage.removeItem(CLAIM_KEY_STORAGE) } catch { /* best-effort */ }
        clearClaimPendingMarker()
        setClaimKey('')
        // #1148 review P2: sign in with the just-created credentials so the
        // user lands in SESSION mode (not stuck on the claimed-team gate).
        try {
          if (supabaseClient) {
            const { error } = await supabaseClient.auth.signInWithPassword({
              email: claimEmail.trim(), password: claimPassword,
            })
            if (!error) {
              try { window.setLastAuthMethod('email'); setLastAuthMethod('email') } catch { /* best-effort */ }
              window.location.reload()
              return
            }
          }
        } catch { /* fall through to reload */ }
        // If sign-in failed for any reason, reload — the claim is done, the
        // user can log in from the auth card.
        window.location.reload()
        return
      }
      let msg = `Couldn't connect (HTTP ${res.status}).`
      try {
        const b = await res.json()
        msg = apiErrorText(res.status, b) || msg
      } catch { /* non-JSON body */ }
      setClaimError(msg)
    } catch (e) {
      setClaimError((e && e.message) || `Couldn't connect — try again.`)
    } finally {
      setClaimBusy(false)
    }
  }

  async function performClaim(sessionToken, key) {
    // POST /v1/claim — both credentials in ONE request: session JWT
    // (Authorization) + pasted tt_ key (body).
    const res = await fetch(`${API_BASE}/v1/claim`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${sessionToken}`,
      },
      body: JSON.stringify({ api_key: key }),
    })
    return res
  }

  async function logout() {
    setAccountMenuOpen(false) // #1148-ux: close the blob dropdown on logout
    localStorage.removeItem(KEY_STORAGE)
    setApiKey('')
    apiKeyRef.current = null
    setAuthed(false)
    setTeam(null)
    setStaleFired(false) // #1858: null→null when logging out from the terminal '—' state — the reset effect won't fire, so clear the per-load latch directly; the next session's skeleton must get a fresh floor
    setKeys([])
    setSessions([])
    setNewKey(null)
    setNewKeyExpiresAt(null) // #2426: expiry echo rides the show-once card
    setRotatedKey(null) // #2735: the rotate reveal is one-time plaintext — never survives logout
    // #1082: clear the claim intent on logout (a stale pasted key must not
    // auto-claim the next user's session).
    setClaimKey('')
    setClaimError('')
    try { sessionStorage.removeItem(CLAIM_KEY_STORAGE) } catch { /* best-effort */ }
    clearClaimPendingMarker()
    // #2002 (W6): no cross-user transcript/delete-state bleed on logout
    clearSessionDetail()
    setSessionDeletingId(null)
    setSessionsActionError(null)
    // #2195 (durable connect key): drop the wizard's minted/pasted durable key
    // on logout — plaintext key state must never survive to the next session.
    setWizardDurableKey('')
    setWizardDurablePaste('')
    setWizardDurableError('')
    setWizardShowPaste(false)
    // #2246 (review, P1): hygiene — the shown-once welcome plaintext is
    // session-scoped; drop it with the other key state on logout.
    setWelcomeKey('')
    // #2323 (code-review): the first-run provisioning labels are session-
    // scoped too — a logout must not leak the previous session's org name
    // into the next login's welcome header/step-1 summary.
    setWelcomeTeamName('')
    setWelcomeGraphName('')
    setWelcomeTeamReady(false)
    // C7 #2116 (R1): the one-time reveal is session-scoped — a logout must
    // never leave plaintext mounted for the next login.
    setRevealKey(null)
    setGraphs([])
    setGraphsLoaded(false) // Round-27: symmetry with switchTeam
    setMembers(null)
    setMembersStatus('loading')
    setCurrentTeamId(null)
    setTeams([])                       // Round-4: drop the previous session's teams
    // #1893 (code-review P2): a fresh login is a NEW team session — reset
    // the hydration latch + persist gate + scope state so the next session
    // re-hydrates from the server (a change made on another device must be
    // picked up; the stale in-memory scope must never overwrite the server
    // value on the first toggle).
    hydratedTeamIdRef.current = null
    scopeReadyRef.current = false
    scopeTouchedRef.current = { issues: false, docs: false }
    issuesScopeRef.current = { repos: [] }
    docsScopeRef.current = { repos: [], branches: {} }
    onboardingStaleRef.current = false  // #1893 (review P3): a failed-switch stale flag must not leak across logout
    setReposLoadFailed(false)
    setReposLoaded(false)
    setReposList([])
    setBranchLists({})  // #1893 (review P2): don't carry the previous session's cached branches
    setDocsScope({ repos: [], branches: {} })
    setIssuesScope({ repos: [] })
    sessionTokenRef.current = null      // Round-4: never reuse the previous user's JWT
    setError('')                        // Round-4: stale error banner must not survive
    setBackupInfo(null)                 // Round-5: no cross-session backup data leak
    setBackupsStatus('loading')         // #1923: mirror the backupInfo reset
    teamIdRef.current = null            // Round-5: hygiene (inert, but consistent)
    setCheckoutPending(false)           // Round-6: no stuck 'Opening checkout…' for the next user
    setInviteEmail('')                  // Round-9: no half-typed invite from the previous user
    setInviteRole('member')
    setNewGraphName('')

    // Round-7: onAuthStateChange returns {data:{subscription}} with .unsubscribe() —
    // client.auth.removeChannel doesn't exist on GoTrueClient (was a silent no-op).
    if (authSubRef.current) { authSubRef.current.unsubscribe?.(); authSubRef.current = null }
    try { if (supabaseClient) await supabaseClient.auth.signOut() } catch { /* best-effort */ }
    // #1511: the key-only card is gone — after signOut the dashboard has NO
    // !authed UI. Always go to /auth (origin-aware; the app-origin gate emits
    // the absolute target) so the sign-out lands on the login page instead of
    // the dead redirect shell. clearStoredSession is belt-and-braces (signOut
    // already clears the cookie via the adapter; a blocked script is covered
    // by the mount-effect redirect on next load).
    if (typeof window.clearStoredSession === 'function') window.clearStoredSession()
    if (typeof window.bounceToAuth === 'function') window.bounceToAuth()
    else window.location.replace('https://tortoise.premiselabs.co/auth')
  }

  async function loadAll(key) {
    const _teamAtCall = teamIdRef.current // Round-10: staleness guard — a rapid
                                          // A→B→C switch must not land B's data
                                          // under team C's header
    // #1828: overview reads ride the SESSION JWT (get_current_team_session
    // dual-auth) instead of the freshly-minted bootstrap key — the Team /
    // Keys / Sessions cards render without a key mint, and the review-P1
    // ungated reads keep tt_ keys working on flag-off teams, so the
    // 3-active bootstrap cap + max_api_keys deadlock no longer blocks the
    // overview itself (the mint still matters for agent keys + management
    // writes). Multi-team: pin ?team_id= so the cards track the team
    // switcher (session resolution defaults to the first membership).
    // #2246 (ADR-010): the key param is unused in session mode — all call
    // sites pass '' (kept for signature stability only).
    const q = _teamAtCall ? `?team_id=${encodeURIComponent(_teamAtCall)}` : ''
    try {
      const [k, s] = await Promise.all([
        api(`/v1/team/keys${q}`, { useSession: true }),
        api(`/v1/sessions${q}`, { useSession: true }),
      ])
      if (teamIdRef.current !== _teamAtCall) return // stale switch response — don't land B's keys under C
      setKeys(Array.isArray(k) ? k : k.keys || [])
      // #2246 (ADR-010): the rule-5/7 held-key classification hook is DELETED
      // — no held key exists to classify in session mode (apiKey state is ''
      // and the KEY_STORAGE slot was purged at session resolution). keys[]
      // stays UNFILTERED in state (bootstrap/expiring rows included) so
      // managedKeys (isManagedKey render filter) + durableConnectKey's
      // rows-resolution keep working off the full payload.
      setSessions(Array.isArray(s) ? s : s.sessions || [])
    } catch (e) {
      // Round-12: a stale switch's error must not land under the newer team's header
      if (teamIdRef.current === _teamAtCall) setError(e.message)
    }
  }

  // ── E6/E7: team + graph switcher (session JWT authed) ──
  async function loadTeams() {
    const tok = sessionTokenRef.current
    if (!tok) return
    try {
      const res = await fetch(`${API_BASE}/v1/teams`, {
        headers: { Authorization: `Bearer ${tok}` },
      })
      if (res.ok) {
        const list = await res.json()
        // Round-12: a SIGNED_OUT (cross-tab broadcast) during this fetch must
        // not resurrect the previous user's team list after logout's setTeams([]).
        if (sessionTokenRef.current !== tok) return
        setTeams(list)
        // Round-8: guard on teamIdRef (sync write, no render-closure race) —
        // the session-only landing's #1912 first-healthy pin (set before
        // completeLogin('')) / switchTeam / finishWelcomeLoads already set it,
        // so a multi-team reload must NOT clobber it with the first team.
        // #1912: skip suspended rows when auto-selecting — a suspended
        // membership must not become the default team (healthy teams stay
        // selectable).
        const firstSelectable = list.find((t) => !t.suspended_at)
        if (firstSelectable && !teamIdRef.current) {
          setCurrentTeamId(firstSelectable.team_id)
          teamIdRef.current = firstSelectable.team_id
        }
      }
    } catch { /* best-effort */ }
  }

  // #1875: fetch the invitee's pending invites when the account menu opens.
  async function loadPendingInvites() {
    if (!sessionTokenRef.current) return
    try {
      const res = await api('/v1/invites/pending', { useSession: true })
      setPendingInvites((res && res.invites) || [])
    } catch {
      // #1875 review P2: a transient load failure must NOT collapse to the
      // empty state (which hides real invites) — surface a retryable error.
      setPendingInvites((prev) => (prev && prev.length ? prev : [{ _loadError: 'Could not load invites — reopen the menu to retry' }]))
    }
  }

  async function acceptPendingInvite(inv) {
    setPendingInvitesBusy(inv.invitation_id)
    try {
      const res = await api(`/v1/invites/pending/${encodeURIComponent(inv.invitation_id)}/accept`, {
        method: 'POST', useSession: true,
      })
      setPendingInvites((prev) => (prev || []).filter((x) => x.invitation_id !== inv.invitation_id))
      setAccountMenuOpen(false)
      if (res?.team_id) { await loadTeams(); switchTeam(res.team_id) }
    } catch (e) {
      // #1875 review P2-1: a failed accept (free-cap / at-capacity / expired)
      // keeps the invite in the list and surfaces the API detail — it must
      // not silently vanish.
      setPendingInvites((prev) => (prev || []).map((x) =>
        x.invitation_id === inv.invitation_id ? { ...x, error: e?.message || 'Could not accept' } : x))
    } finally {
      setPendingInvitesBusy('')
    }
  }

  async function declinePendingInvite(inv) {
    setPendingInvitesBusy(inv.invitation_id)
    try {
      await api(`/v1/invites/pending/${encodeURIComponent(inv.invitation_id)}`, {
        method: 'DELETE', useSession: true,
      })
    } catch { /* best-effort — remove locally regardless */ }
    setPendingInvites((prev) => (prev || []).filter((x) => x.invitation_id !== inv.invitation_id))
    setPendingInvitesBusy('')
  }

  async function handleCreateTeam() {
    // #1877: create-team dialog submit — validation mirrors POST /v1/teams
    // (≤64 chars, [a-zA-Z0-9_-], spaces rejected); 402 → gated-on-click
    // upgrade UX (the dialog explains "upgrade a team, then create" — the
    // new team doesn't exist until the gate passes).
    const name = createTeamName.trim()
    if (!name) { setCreateTeamError('Organization name required'); return }
    if (name.length > 64 || !/^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$/.test(name)) {
      setCreateTeamError('Invalid organization name — letters, numbers, dash, underscore only')
      return
    }
    setCreateTeamBusy(true)
    setCreateTeamError('')
    setCreateTeamUpgrade(false)
    try {
      const res = await api('/v1/teams', {
        method: 'POST', useSession: true,
        body: JSON.stringify({ name }),
      })
      closeCreateTeam() // #2392 (a11y): also hands focus back to the blob trigger
      setCreateTeamName('')
      await loadTeams()
      if (res?.team_id) switchTeam(res.team_id)
    } catch (e) {
      if (e?.status === 402) {
        setCreateTeamError(e.message || 'Create another organization requires a paid plan')
        setCreateTeamUpgrade(true)
      } else {
        setCreateTeamError(e?.message || 'Could not create the organization')
      }
    } finally {
      setCreateTeamBusy(false)
    }
  }

  // #2323 (Option B): org-create step handler — the FIRST-org door. For a
  // teamless first-timer the submit PROVISIONS the org name-first via
  // tenant-provision (deterministic team_id, welcome key + demo seed land on
  // that one org — POST /v1/onboarding/team is no longer called for first
  // orgs; it stays as the entitlement-gated second-org lane). Accounts that
  // already hold an org never mint here (the step renders read-only). Name
  // REQUIRED (DE2E-3); claim-guard errors route to the claim card.
  async function handleWizardCreateOrg() {
    if (wizardOrgBusy) return  // #2361 review-r1 (Bug-2): double-submit guard
    const name = wizardOrgName.trim()
    const nameErr = orgNameError(name)
    if (nameErr) { setWizardOrgError(nameErr); return }
    // An account that already holds an org never mints via this step — a
    // stray submit on the read-only path just advances.
    if (Array.isArray(teams) && teams.length > 0) { setWizardStep(1); return }
    setWizardOrgBusy(true)
    setWizardOrgError('')
    try {
      const session = sessionRef.current
      if (!session || !session.access_token) {
        setWizardOrgError('Your session ended — reload to sign in again.')
        return
      }
      const provisioned = await provisionInApp(session, name)
      if (!provisioned) {
        setWizardOrgError('Could not create your organization — try again.')
        return
      }
      if (provisioned && provisioned.routedAway) {
        // claim in flight / claimable anon team — the claim card owns the flow
        return
      }
      // Success: the ONE org exists, named by the user. welcomeKey is the
      // provisioned durable plaintext — shown once at the connect step (#2325),
      // never on this card. In-memory only (ADR-010); a mid-wizard reload
      // loses it and the connect step re-gates via the rows.
      setWizardOrgName('')
      setWelcomeKey(provisioned.api_key || '')
      setWelcomeTeamName(provisioned.team_name || name)
      setWelcomeGraphName(provisioned.graph_name || '')
      setWelcomeTeamReady(true)
      // Pin the new org so fork/connect checkpoints + key mint target it, and
      // fetch alerts for the team row (parity with the pre-#2323 provisioned
      // branch, #1860).
      await loadTeams().catch(() => {})
      refreshTeam('', undefined, teamRefreshSeqRef.current)
        .then((t) => { if (t && t.team_id) loadAlerts(t.team_id) })
        .catch(() => {})
      setWizardStep(1)
    } catch (e) {
      // #2323 (code-review): never leave the provisioning overlay + busy
      // button stuck on an unexpected rejection — surface + recover.
      setWizardOrgError((e && e.message) || 'Could not create your organization — try again.')
    } finally {
      setWizardOrgBusy(false)
      setWelcomeProvisioning(false)
    }
  }

  // #1997 (W1): fork-card step handler — checkpoint fork (set-once). Build
  // branch: the catalog render marks catalog-presented via W5's checkpoint
  // (surface 4 write contract — the build-fork gate is evaluable; W8 #2004
  // replaced the placeholder SOURCE with the registry endpoint, the
  // mark/mechanism is unchanged). Fork SEMANTICS are W2-owned — W1 renders
  // the shell only.
  async function handleWizardFork(forkId) {
    setWizardForkBusy(true)
    setWizardForkError('')
    try {
      await api(`/v1/onboarding/state/checkpoint${onboardingTeamQ()}`, {
        method: 'POST', useSession: true,
        body: JSON.stringify({ fork: forkId }),
      })
      setWizardForkChosen(forkId)
      // review P1 (#1997): the catalog-presented mark fires HERE, not in a
      // step-2 effect — React batches setWizardForkChosen + setWizardStep(3)
      // into ONE render where wizardStep===3, so the effect's step-2 guard
      // never observes the fresh build pick. Fire-and-forget; keyed-MERGE
      // (replay no-op). The step-2 effect above still covers RE-ENTRY with a
      // persisted build fork (onboarding.fork === 'build').
      // A build pick also STAYS on step 2 so the catalog renders (the user
      // sees what they can build on before continuing) — the Continue button
      // appears once a fork is chosen; self picks advance.
      if (forkId === 'build') {
        const teamKey = teamIdRef.current || 'default'
        if (!catalogMarkedRef.current[teamKey]) {
          catalogMarkedRef.current[teamKey] = true
          api(`/v1/onboarding/state/checkpoint${onboardingTeamQ()}`, {
            method: 'POST', useSession: true,
            body: JSON.stringify({ step: 'catalog-presented' }),
          }).catch(() => {})
        }
      } else {
        setWizardStep(2)
      }
    } catch (e) {
      if (e?.status === 409) {
        // set-once conflict — the org already chose; refresh the projection
        // so the actual fork renders (the Continue button needs it)
        setWizardForkError('This organization already chose how it uses Tortoise.')
        refreshOnboarding().catch(() => {})
        setWizardStep(1)
      } else if (e?.status === 503) {
        setWizardForkError('The graph is temporarily unavailable — try again in a moment.')
      } else {
        setWizardForkError(e?.message || 'Could not save your choice — try again.')
      }
    } finally {
      setWizardForkBusy(false)
    }
  }

  // #1998 (W2): connect-consent Continue — the harness-connected checkpoint
  // (surface 5/6 contract; W1's plan table left it "W2 owns"). The agent-side
  // tortoise-onboarding skill writes it after tortoise_health passes (CLI
  // harnesses via REST); Claude Desktop/Web have NO REST/curl surface, so the
  // human's Continue writes it (session dual-auth — the checkpoint endpoint
  // accepts session OR tt_ key). FWW keyed-MERGE → replay is a 200 no-op, so
  // the agent write + this write (and repeat clicks) can all fire safely.
  // Mirror handleWizardFork's failure handling: busy/disable, stay on step on
  // 503/error (a fire-and-forget would strand Desktop/Web's gate with no
  // retry affordance), advance on 2xx only.
  async function wizardHarnessContinue() {
    setWizardConnectBusy(true)
    setWizardConnectError('')
    try {
      await api(`/v1/onboarding/state/checkpoint${onboardingTeamQ()}`, {
        method: 'POST', useSession: true,
        body: JSON.stringify({ step: 'harness-connected' }),
      })
      setWizardPaused(false)
      connectedOnceRef.current = true
      setWizardStep(3)
    } catch (e) {
      if (e?.status === 503) {
        setWizardConnectError('The graph is temporarily unavailable — try again in a moment.')
      } else {
        setWizardConnectError(e?.message || 'Could not save your progress — try again.')
      }
    } finally {
      setWizardConnectBusy(false)
    }
  }

  // #1998 fold-in (PR #2161 finding): the connect step must source a DURABLE
  // key — a bootstrap access credential stops authenticating within a day.
  // Mints via POST /v1/team/keys (the same endpoint the API Keys tab's create
  // uses — created_via 'provisioned', durable, counts vs max_api_keys).
  // #2246 (ADR-010): the mint rides the session JWT (rule 4 — mintKey sends
  // NO key header + pins ?team_id=); the session IS the authenticator — no
  // browser-held key is involved. #2297 POLICY A: the server POST
  // /v1/team/keys session lane IS owner/admin-gated (_require_owner_admin,
  // same as PATCH toggle since #1148) — this affordance renders only for
  // owner/admin, matching the server contract. The plaintext is shown ONCE —
  // the connect command embeds it (the reveal); afterwards the key is
  // managed/regenerable from the API Keys tab.
  async function wizardMintDurableKey() {
    if (wizardDurableBusy) return
    setWizardDurableBusy(true)
    setWizardDurableError('')
    try {
      // Round-15 (P2) team pin (createKey pattern): resolve the ACTIVE team
      // explicitly so the mint lands on the onboarding org — a session JWT
      // alone resolves the first membership, wrong team for multi-team users.
      // #2246: mintKey rides the session JWT (rule 4) — no held key involved.
      const _teamAtCall = currentTeamId
      // #2325/#2333: name the mint org + date so every connect key is a
      // DISTINGUISHABLE row (the old fixed 'Setup command' label made
      // rotate/regenerate rows identical under the free cap of 2). The org
      // resolves like the CTA label's shownOrgName (welcome name right after
      // provision → pinned team → first membership) so the row name always
      // agrees with the org the key actually lands on (code-review P3).
      const orgForKey =
        (welcomeTeamReady && welcomeTeamName) ||
        (team && team.team_name) ||
        (Array.isArray(teams) && teams.length
          ? ((teams.find((t) => t.team_id === teamIdRef.current) || teams[0] || {}).team_name || '')
          : '') || ''
      const keyName = durableKeyName(orgForKey, new Date(), (Array.isArray(keys) ? keys : []).map((k) => k && k.name))
      // #2426: the connect-step mint is the embed surface — the wizard stays
      // Never-keys-only (decision 2), so wizardMintDurableKey sends NO expiry
      // param (mk.expires_at absent → the card/table read Never).
      const mk = await mintKey('', keyName)
      // #2326: a team switch during the POST must NOT silently swallow the
      // mint — surface it so the user isn't left believing connect produced
      // a usable key. The key was created on the PREVIOUS org (mintKey pinned
      // ?team_id= at call time); refresh the list so it shows there when the
      // user switches back, and re-gate this step.
      if (teamIdRef.current !== _teamAtCall) {
        // #2326 (code-review): a loadAll('') here would fetch the NEW team's
        // keys — the mint landed on the PREVIOUS org, which reloads its own
        // keys when the user switches back. Surface the error and re-gate.
        setWizardDurableError('The organization changed while the key was being created — the key was created on the previous organization. Switch back to it in the account menu to use it, or create another key here.')
        return
      }
      setWizardDurableKey((mk && (mk.key || mk.api_key)) || '')
      // #2246 (ADR-010): the durable key is NOT installed (no
      // localStorage/teamKeysRef/apiKey write — the browser never holds a
      // key). wizardDurableKey keeps it in-memory so the connect snippet can
      // embed it THIS session; a reload re-gates (rows-resolution shows the
      // new durable exists → 'rows-durable' gate copy) and re-minting burns
      // max_api_keys (free = 2) — the 402 handler routes to regenerate+paste.
      // Refresh the team keys/sessions lists (createKey precedent) so the new
      // durable row lands in keys[] — the API Keys tab shows it and future
      // durableConnectKey rows-resolution matches it.
      await loadAll('').catch(() => {})
    } catch (e) {
      // #1147: a tier-cap 402 is a LIMIT, not an error — surface the upgrade /
      // regenerate path (the API Keys tab's regenerateKey does not grow the
      // key count). Free tier max_api_keys = 2. The remedy ends at the paste
      // box, which for owner/admin sits behind the disclosure (#2325 review
      // P2) — open it so the error's "paste it below" lands on a visible field.
      if (e?.status === 402) {
        setWizardShowPaste(true)
        setWizardDurableError('You\'ve reached your plan\'s limit of API keys — revoke or regenerate one in the API Keys tab (shown once there), then paste a key below.')
      } else {
        // #2246 (review) + #2297 POLICY A: reachable mint failures here are
        // the 402 cap above, a suspension 403, or transport — the server POST
        // /v1/team/keys session lane IS owner/admin-gated
        // (_require_owner_admin since #2297), so a member 403s server-side;
        // this handler's callers are owner/admin render-gated, matching the
        // server contract. A suspension 403 falls through to its own message.
        setWizardDurableError(e?.message || 'Could not create a new key — try again.')
      }
    } finally {
      setWizardDurableBusy(false)
    }
  }

  async function switchTeam(teamId) {
    // P3 (code-review): reset stale team-scoped state at the top so a rapid
    // switch never flashes the previous team's members/graphs, and record the
    // requested team as current for staleness guards.
    setCapNotice('') // #1147: a cap banner from the previous team must not stick
    const prevTeamId = currentTeamId
    const tok = sessionTokenRef.current
    if (!tok) return // Round-3: guard BEFORE wiping state — logout→apikey
                     // login must not blank the dashboard on a stale pick
    setMembers(null)
    setMembersStatus('loading')
    setGraphs([])
    setGraphsLoaded(false) // Round-27: per-team loaded flag — no '0' flash on switch
    setGraphsStatus('loading') // #1842 P2-1: mirror the membersStatus reset
    // C7 #2116: a team switch must not carry the previous team's open panel
    // (its keys are graph-bound to the OLD team) or a mounted one-time
    // plaintext across teams.
    graphPanelReqRef.current += 1 // invalidate any in-flight panel open
    setPanelGraphId(null)
    setPanelKeys(null)
    setPanelKeysStatus('closed')
    setGraphMsg('')
    setConfirmDeleteId(null)
    setDeleteConfirmTyped('')
    setEditingGraphId(null)
    graphRenameCancelRef.current = true // #2701: the unmount-blur must not fire a graph rename for the old team
    // #2304: trash state is team-scoped — never flash the previous team's
    // trash rows / rescue panel / notices under a fresh team.
    setTrash([])
    setTrashStatus('closed')
    setConfirmRestoreId(null)
    setTrashInspectId(null)
    setTrashInspect(null)
    setTrashMsg('')
    setRevealKey(null)
    setTeam(null)          // Fix B: clear key-scoped overview state too
    setStaleFired(false)   // #1858: reset the per-load stale latch on EVERY switch — incl. null→null from the terminal '—' state, where the reset effect's team dep doesn't fire
    setKeys([])
    setSessions([])
    clearSessionDetail()          // #2002 (W6): a switch must never show the previous team's transcript
    setSessionDeletingId(null)
    setSessionsActionError(null)
    setBackupInfo(null)
    setBackupsStatus('loading') // #1923: mirror the backupInfo reset
    setNewKey(null)        // Round-16: the plaintext key card was shown once on the old team
    setNewKeyExpiresAt(null) // #2426: expiry echo rides the show-once card
    setRotatedKey(null)    // #2735: the rotate reveal is one-time plaintext — never survives a team switch
    setNewKeyName('')      // key-label: a typed label must not leak onto another team's mint
    setNewKeyExpiryDate('') // #2426: a picked Custom date must not leak onto another team's mint
    setEditingKeyId(null)  // key-label: close any in-flight inline rename across teams
    renameCancelRef.current = true // key-label: the unmount-blur must not fire a rename for the old team
    // #2195 (durable connect key): the wizard's minted/pasted durable key is
    // TEAM-scoped plaintext (minted under the old team's key). A switch must
    // drop it — otherwise the next empty org's connect step would embed the
    // previous org's durable key in its setup command (silent wrong-org
    // embed). Mirrors the setNewKey(null) plaintext-card convention above.
    setWizardDurableKey('')
    setWizardDurablePaste('')
    setWizardDurableError('')
    setWizardShowPaste(false)
    // #2246 (review, P1): welcomeKey is TEAM-scoped shown-once plaintext — a
    // switch must not leak the previous team's plaintext into the new team's
    // snippet/connect surfaces (welcomeKey feeds snippetKey, the re-entry/
    // first-data cards, and the connect gate). Same convention as the
    // wizardDurableKey drop above.
    setWelcomeKey('')
    // #2323 (code-review): drop the first-run labels with the team switch —
    // welcomeTeamName must never label a DIFFERENT team's header/step-1
    // summary after the user switches.
    setWelcomeTeamName('')
    setWelcomeGraphName('')
    setWelcomeTeamReady(false)
    setError('')
    setCurrentTeamId(teamId)
    teamIdRef.current = teamId
    // #1893 (code-review P1): onboarding state is TEAM-scoped — the switch
    // must re-hydrate the NEW team's persisted scope, never the old team's.
    // The stale flag blocks the hydration effect until refreshOnboarding
    // resolves the new team's state; the scope latches + repos are reset so
    // the new team's repos load fresh (connected-flip effect below re-fires
    // loadRepos when reposLoadFailed/reposLoaded reset).
    onboardingStaleRef.current = true
    hydratedTeamIdRef.current = null
    scopeReadyRef.current = false
    scopeTouchedRef.current = { issues: false, docs: false }
    issuesScopeRef.current = { repos: [] }
    docsScopeRef.current = { repos: [], branches: {} }
    setReposLoadFailed(false)
    setReposLoaded(false)
    setReposList([])
    // #1893 (code-review P2): branchLists is NOT team-keyed and loadBranches
    // short-circuits on hasOwnProperty — without a reset, team B would reuse
    // team A's cached branches for colliding repo names (and the heal-persist
    // would write A's branch data into B's stored scope).
    setBranchLists({})
    setDocsScope({ repos: [], branches: {} })
    setIssuesScope({ repos: [] })
    try {
      // #2246 (ADR-010): team switches are session-only — the browser never
      // holds a per-team durable (teamKeysRef was deleted). Every team's reads
      // ride the session JWT; apiKey/apiKeyRef stay '' across switches so no
      // team-scoped snippet surface leaks a previous team's key material.
      if (teamIdRef.current !== teamId) return // stale — user switched again
      try {
        await refreshTeam('')
      } catch (e) {
        // #2167: the 401 re-mint is DELETED. The team read is session-authed
        // (switchTeam requires a session JWT), so a failure is a session/
        // network problem — the revert catch below re-attaches the previous
        // team (session-only on it; no key material to restore).
        throw e
      }
      if (teamIdRef.current !== teamId) return
      await Promise.all([loadAll(''), loadBackups('')])
      // #1893 (code-review P1): re-fetch the new team's onboarding state —
      // the stale flag set above clears only when refreshOnboarding resolves
      // (the scope hydration for the new team is blocked until then), and
      // the connected-flip effect re-loads the new team's repos.
      await refreshOnboarding().catch(() => {})
      // #1893 (code-review P2): if the refetch failed, the stale flag stays
      // set and hydration would be blocked indefinitely — bounded retry (one
      // re-issue after a short delay) so a transient failure recovers
      // in-session instead of until the next reload.
      if (onboardingStaleRef.current) {
        await new Promise((r) => setTimeout(r, 1500))
        if (teamIdRef.current === teamId) await refreshOnboarding().catch(() => {})
      }
      // members + graphs load via the currentTeamId effect (JWT team-scoped;
      // both loaders carry their own staleness guard).
    } catch (e) {
      if (teamIdRef.current === teamId) {
        // #2167/#2246 (revert re-frame): a failed switch never mints and
        // never blocks. Re-attach the PREVIOUS team session-only — no key
        // material exists to restore (the browser never holds keys), and the
        // slot is never rewritten by a switch.
        if (prevTeamId) {
          setCurrentTeamId(prevTeamId)
          teamIdRef.current = prevTeamId
          setTeam(null)
          setStaleFired(false) // #1858: the revert re-attaches the previous team — clear the latch so the restored team's skeleton gets a fresh floor (team is already null here, so the reset effect won't fire)
          // #1893 (code-review P1): the revert re-attaches the PREVIOUS
          // team — its onboarding is the pre-switch object (still current),
          // so clear the switch-stale flag and let hydration re-run for the
          // restored team (the latches/repos were reset at switch top).
          onboardingStaleRef.current = false
          await refreshTeam('').catch(() => {})
          // Round-3: reload ALL key-scoped data for the reverted team —
          // otherwise keys/sessions/backups stay wiped until reload.
          await Promise.all([loadAll(''), loadBackups('')]).catch(() => {})
        }
        setError(e.message)
      }
    }
  }


  // ── #300: graphs + members + backups (session JWT authed) ──
  function myRole() {
    const t = teams.find((x) => x.team_id === currentTeamId)
    return t ? t.role : ''
  }
  const isOwnerAdmin = myRole() === 'owner' || myRole() === 'admin'

  async function loadGraphs(teamId) {
    const tok = sessionTokenRef.current
    if (!tok || !teamId) return
    try {
      const res = await fetch(`${API_BASE}/v1/graphs?team_id=${teamId}`, {
        headers: { Authorization: `Bearer ${tok}` },
      })
      // #1842 P2-1: terminal state on failure — a non-200 (or a transport
      // error below) must not leave graphsStatus 'loading' forever.
      if (!res.ok) {
        if (teamIdRef.current === teamId) setGraphsStatus('error')
        return
      }
      const list = await res.json()
      if (teamIdRef.current === teamId) {
        setGraphs(list)
        setGraphsLoaded(true) // Round-26
        setGraphsStatus('ok')
        // #2303: post-#2083 C7 reconciliation — loadGraphs is the single
        // funnel every graphs-list refresh passes through (deleteGraphRow,
        // createGraph, restore, team switch). A reloaded list that no
        // longer contains the open key-panel's graph (deleted here, in
        // another tab/session, or by a teammate) must drop the panel — a
        // dangling panelGraphId would otherwise keep the panel's keys
        // list/mint/revoke targeting a deleted graph. deleteGraphRow's
        // synchronous same-row close (below) covers the delete path; this
        // catches every other list-drop path that would strand the panel.
        if (panelGraphId && !list.some((x) => x.graph_id === panelGraphId)) closeGraphPanel()
      }
    } catch {
      // #1842 P2-1: transport/parse failure → terminal 'error', never eternal shimmer
      if (teamIdRef.current === teamId) setGraphsStatus('error')
    }
  }

  async function createGraph() {
    const _teamAtCall = currentTeamId // Round-16: mutation identity guard — a switch mid-flight must not act on the previous team
    if (busy || !newGraphName.trim()) return
    // #2392 (a11y): capture the mint trigger (+ Create button, or the name
    // input when Enter submits) while it still owns focus — setBusy below
    // disables it and the browser drops focus to <body> before the reveal
    // modal mounts from the async response.
    rememberFocusedTrigger(revealRestoreRef)
    setBusy(true)
    setError('')
    try {
      const tok = sessionTokenRef.current
      if (!tok) throw new Error('No session')
      const res = await fetch(`${API_BASE}/v1/graphs`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${tok}` },
        body: JSON.stringify({ team_id: currentTeamId, name: newGraphName.trim() }),
      })
      const b = await res.json().catch(() => ({}))
      if (!res.ok) {
        if (res.status === 402) {
          setError('Graph limit reached for this tier — upgrade to add more graphs.')
          return
        }
        if (res.status === 409) {
          // C2 #2111: the provisioning service owns quota (409) — graph cap
          // OR API-key cap (the create mints the graph's first key; a full
          // key table rolls the graph back with a 409). The detail is
          // authoritative (plan §6.2 contract).
          setError(b.detail || 'Graph limit reached — delete a graph or upgrade.')
          return
        }
        throw new Error(b.detail || `HTTP ${res.status}`)
      }
      // C7 #2116 (surface 4): the C2 nested envelope carries the graph's
      // FIRST key one time ({graph, key, key_plaintext, revealed_once}). The
      // plaintext exists ONLY here — hash-only storage server-side — so the
      // reveal modal is the sole render, never a route (no show-key page).
      if (b && b.key_plaintext) {
        setRevealKey({
          plaintext: b.key_plaintext,
          title: b.graph && b.graph.name ? `Graph ${b.graph.name} created` : 'Graph created',
        })
      }
      setNewGraphName('')
      await Promise.all([loadGraphs(currentTeamId), loadTeams()])
      // Round-16: bail if the user switched teams mid-flight
      if (teamIdRef.current !== _teamAtCall) return

    } catch (e) {
      // Round-18: a stale request's error must not land under the new team
      if (teamIdRef.current === _teamAtCall) setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  // C7 #2116 — per-graph key panel actions (indicator 3). The panel is
  // owner/admin-managed (mirrors the members gate); mint + revoke ride the
  // session JWT like every management call (#2255 ADR-010 posture).
  async function openGraphPanel(graphId) {
    if (panelGraphId === graphId) { closeGraphPanel(); return }
    const seq = ++graphPanelReqRef.current
    setPanelGraphId(graphId)
    setPanelKeysStatus('loading')
    setPanelKeys(null)
    setGraphMsg('')
    setGraphKeyName('')
    const _teamAtCall = currentTeamId
    try {
      const rows = await graphKeysFor(currentTeamId, graphId)
      if (graphPanelReqRef.current !== seq || teamIdRef.current !== _teamAtCall) return // stale open/team
      setPanelKeys(rows)
      setPanelKeysStatus('ok')
    } catch {
      if (graphPanelReqRef.current === seq && teamIdRef.current === _teamAtCall) setPanelKeysStatus('error')
    }
  }

  function closeGraphPanel() {
    setPanelGraphId(null)
    setPanelKeys(null)
    setPanelKeysStatus('closed')
    setGraphMsg('')
    setGraphKeyName('')
  }

  // GET /v1/team/keys?team_id=…&graph_id=… (the server-side per-graph filter
  // — C3 #2112). Mirrors loadKeys' api() call shape.
  async function graphKeysFor(teamId, graphId) {
    if (!teamId || !graphId) return []
    const q = `?team_id=${encodeURIComponent(teamId)}&graph_id=${encodeURIComponent(graphId)}`
    const data = await api(`/v1/team/keys${q}`, { useSession: true })
    const rows = Array.isArray(data) ? data : (data && data.keys) || []
    return rows
  }

  // Per-graph mint → POST /v1/team/keys {graph_id, scopes} (scoped session
  // mint; scopes are the data-plane pair — graphs.js GRAPH_KEY_SCOPES). The
  // response's .key plaintext is shown ONCE in the reveal modal.
  async function mintGraphKey() {
    const gid = panelGraphId
    if (!gid || graphBusy) return
    // #2392 (a11y): capture the panel-mint trigger (+ Mint key button, or
    // the key-name input on Enter) while it still owns focus — graphBusy
    // disables it mid-flight and focus drops to <body> before the reveal
    // modal mounts.
    rememberFocusedTrigger(revealRestoreRef)
    // P2-1 (review): capture the open-sequence at OPERATION START — a panel
    // open landing during the mint POST (before the refresh) must also
    // invalidate the refresh's write.
    const seq = graphPanelReqRef.current
    setGraphBusy(true)
    setGraphMsg('')
    const _teamAtCall = currentTeamId
    try {
      const q = `?team_id=${encodeURIComponent(currentTeamId)}`
      const resp = await api(`/v1/team/keys${q}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        useSession: true,
        body: JSON.stringify(graphMintBody(gid, graphKeyName)),
      })
      const plaintext = resp && (resp.key || resp.api_key)
      if (!plaintext) throw new Error('Mint response did not include a key')
      if (teamIdRef.current !== _teamAtCall) return
      setGraphKeyName('')
      setRevealKey({ plaintext, title: `Key for ${graphNameFor(gid) || 'graph'} created` })
      await refreshPanelAndCounts(gid, seq)
    } catch (e) {
      if (teamIdRef.current === _teamAtCall) {
        const detail = e && e.detail ? e.detail : (e && e.message)
        setGraphMsg(detail || 'Could not mint key — try again.')
      }
    } finally {
      setGraphBusy(false)
    }
  }

  async function refreshPanelAndCounts(gid, seq) {
    // P2-4 (review): a mint/revoke refresh is panel-scoped — a slower
    // refresh must not clobber a NEWER panel open. seq is captured at the
    // OPERATION start (mint/revoke), so a panel open during the mutation
    // await also invalidates this refresh's write. Guard team + seq.
    const _teamAtCall = currentTeamId
    const rows = await graphKeysFor(currentTeamId, gid).catch(() => null)
    if (graphPanelReqRef.current !== seq || teamIdRef.current !== _teamAtCall) return
    if (rows) { setPanelKeys(rows); setPanelKeysStatus('ok') }
    loadGraphs(currentTeamId) // key_count column refresh
  }

  function graphNameFor(gid) {
    const g = (graphs || []).find((x) => x.graph_id === gid)
    return g ? g.name : ''
  }

  // Panel revoke — window.confirm names the row (mirrors the API-Keys
  // revokeKey UX: name · prefix · created). Owner/admin-only render gate.
  async function revokePanelKey(keyId) {
    const seq = graphPanelReqRef.current // P2-1: op-start seq (see mintGraphKey)
    const row = (panelKeys || []).find((k) => (k.id || k.key_id) === keyId)
    // #2246 (PM-1): name · prefix · created — the confirm identifies the key
    // that stops working (mirrors the API-Keys revokeKey UX).
    if (!confirm(`Revoke ${keyRowDisclosure(row, 'this graph key')}? Applications using it will stop working.`)) return
    setGraphMsg('')
    const _teamAtCall = currentTeamId
    try {
      // #2230: pin the SELECTED team on the panel revoke (session mode) —
      // the panel lives on the Graphs tab whose keys are per-graph rows of
      // the selected team; without the pin the server resolves the session
      // team from memberships[0], so a multi-membership user on a non-first
      // team 403s revoking their own panel key (same shape revokeKey had —
      // #2230). Key-mode (no session JWT) stays unpinned.
      const q = (sessionTokenRef.current && _teamAtCall) ? `?team_id=${encodeURIComponent(_teamAtCall)}` : ''
      await api(`/v1/team/keys/${keyId}${q}`, { method: 'DELETE', useSession: true })
      if (teamIdRef.current !== _teamAtCall) return
      await refreshPanelAndCounts(panelGraphId, seq)
    } catch (e) {
      if (teamIdRef.current === _teamAtCall) {
        const detail = e && e.detail ? e.detail : (e && e.message)
        setGraphMsg(detail || 'Could not revoke key — try again.')
      }
    }
  }

  // #2701: rename a graph's display name — the inline-edit commit (Enter
  // or blur; Escape cancels via graphRenameCancelRef). Mirrors renameKey's
  // contract: PATCH /v1/graphs/{id} with ONLY {name} (never echo stale row
  // fields — a rename must not touch recording/auth state), optimistic local
  // update with revert-on-error into the page-level banner, and the #2230
  // team pin captured at call (a mid-flight switch must not rename the new
  // team's row nor land the error under the wrong header). Server: session
  // owner/admin or a team:manage key; 409 on a live-name conflict.
  async function renameGraph(graphId, name) {
    const _teamAtCall = currentTeamId
    setEditingGraphId(null)
    setError('')
    const next = (name || '').trim()
    if (!next) {
      setError('Graph name can\'t be empty.')
      return
    }
    const cur = graphs.find((g2) => g2.graph_id === graphId)
    // No-op guard: also dedupes the Enter→blur double-fire (blur after Enter
    // sees the name already applied via the optimistic update's re-render).
    if (!cur || (cur.name || '') === next) return
    const prev = cur.name || ''
    setGraphs((gs) => gs.map((x) => x.graph_id === graphId ? { ...x, name: next } : x))
    try {
      const q = (sessionTokenRef.current && _teamAtCall) ? `?team_id=${encodeURIComponent(_teamAtCall)}` : ''
      const updated = await api(`/v1/graphs/${encodeURIComponent(graphId)}${q}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        useSession: true, // rename is owner/admin-managed (session JWT — mirror key management)
        body: JSON.stringify({ name: next }),
      })
      if (teamIdRef.current !== _teamAtCall) return // stale switch — don't touch the new team's state
      if (updated && (updated.graph_id || updated.name)) {
        // Reconcile with the server echo (it strips the name) — the row's
        // sort position updates via sortedGraphRows at render.
        setGraphs((gs) => gs.map((x) => x.graph_id === graphId
          ? { ...x, name: (typeof updated.name === 'string' ? updated.name : next) }
          : x))
      }
    } catch (e) {
      if (teamIdRef.current !== _teamAtCall) return // stale switch — error belongs to the old team
      setGraphs((gs) => gs.map((x) => x.graph_id === graphId ? { ...x, name: prev } : x))
      setError((e && e.message) || 'Couldn\'t rename the graph — try again.')
    }
  }

  // Delete a CUSTOM graph (indicator 4). The default graph row never shows
  // the action (graphs.js graphCanDelete — its 🗑 renders disabled) and the
  // server 403s the default as a code guard. #2701: the row's 🗑 opens a
  // type-to-confirm modal (deleteConfirmTyped must equal the literal word
  // "delete" — the destructive gate); confirmDeleteId = the modal-open row.
  async function deleteGraphRow(graphId) {
    const _teamAtCall = currentTeamId
    if (!graphId || !currentTeamId) return
    setGraphBusy(true)
    // #2301: delete errors surface PAGE-LEVEL. graphMsg renders only inside
    // the open key panel (mint/revoke — panel-bound actions), so a failed
    // DELETE with the panel closed would vanish there. The global error
    // banner is the same sink createGraph (402/409/generic) and revokeKey
    // use — destructive graph/row actions stay legible wherever they run.
    setError('')
    try {
      const tok = sessionTokenRef.current
      if (!tok) throw new Error('No session')
      // Raw fetch, not api(): DELETE /v1/graphs returns 204 no-body and
      // api() always calls res.json() (would throw on the empty body).
      const q = `?team_id=${encodeURIComponent(currentTeamId)}`
      const res = await fetch(`${API_BASE}/v1/graphs/${encodeURIComponent(graphId)}${q}`, {
        method: 'DELETE',
        headers: { Authorization: `Bearer ${tok}` },
      })
      if (!res.ok) {
        const b = await res.json().catch(() => ({}))
        throw new Error(b.detail || `HTTP ${res.status}`)
      }
      if (teamIdRef.current !== _teamAtCall) return
      setConfirmDeleteId(null)
      setDeleteConfirmTyped('')
      if (panelGraphId === graphId) closeGraphPanel()
      await Promise.all([loadGraphs(currentTeamId), loadTeams()]) // count meter refresh
      if (isOwnerAdmin) await loadTrash(currentTeamId) // the row just entered the trash
    } catch (e) {
      // #2301: failure keeps the row ARMED (Delete/Cancel = retry/escape)
      // and lands the reason in the page-level banner — visible with the
      // key panel open or closed.
      if (teamIdRef.current === _teamAtCall) setError(e.message || 'Could not delete graph — try again.')
    } finally {
      setGraphBusy(false)
    }
  }

  // #2304: team trash (owner/admin). GET /v1/graphs/trash — the recovery
  // window surface. Non-owner members never call it (server 403s); the
  // section only renders for isOwnerAdmin.
  async function loadTrash(teamId) {
    const tok = sessionTokenRef.current
    if (!tok || !teamId || !isOwnerAdmin) return
    setTrashStatus('loading')
    try {
      const res = await fetch(`${API_BASE}/v1/graphs/trash?team_id=${teamId}`, {
        headers: { Authorization: `Bearer ${tok}` },
      })
      if (!res.ok) {
        if (teamIdRef.current === teamId) setTrashStatus('error')
        return
      }
      const list = await res.json()
      if (teamIdRef.current === teamId) {
        setTrash(Array.isArray(list) ? list : [])
        setTrashStatus('ok')
      }
    } catch {
      if (teamIdRef.current === teamId) setTrashStatus('error')
    }
  }

  // Full restore (owner/admin). 409 (live name conflict) and 410 (purged)
  // ride the server detail straight to the inline notice.
  async function restoreTrashRow(graphId) {
    const _teamAtCall = currentTeamId
    if (!graphId || !currentTeamId) return
    setGraphBusy(true)
    setTrashMsg('')
    try {
      const tok = sessionTokenRef.current
      if (!tok) throw new Error('No session')
      const q = `?team_id=${encodeURIComponent(currentTeamId)}`
      const res = await fetch(`${API_BASE}/v1/graphs/trash/${encodeURIComponent(graphId)}/restore${q}`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${tok}` },
      })
      const body = await res.json().catch(() => ({}))
      if (!res.ok) {
        throw new Error(body.detail || `HTTP ${res.status}`)
      }
      if (teamIdRef.current !== _teamAtCall) return
      const name = body.name || 'graph'
      setConfirmRestoreId(null)
      setTrashInspectId(null)
      setTrashInspect(null)
      setTrashMsg(`"${name}" restored. Mint fresh keys for it under Keys.`)
      await Promise.all([loadGraphs(currentTeamId), loadTrash(currentTeamId)])
    } catch (e) {
      if (teamIdRef.current === _teamAtCall) setTrashMsg(e.message || 'Could not restore graph — try again.')
    } finally {
      setGraphBusy(false)
    }
  }

  // Read-only rescue view (owner/admin): artifact-side metadata for one
  // trash row — never touches the quarantined namespace.
  async function inspectTrashRow(graphId) {
    const _teamAtCall = currentTeamId
    if (!graphId || !currentTeamId) return
    setGraphBusy(true)
    setTrashMsg('')
    try {
      const tok = sessionTokenRef.current
      if (!tok) throw new Error('No session')
      const q = `?team_id=${encodeURIComponent(currentTeamId)}`
      const res = await fetch(`${API_BASE}/v1/graphs/trash/${encodeURIComponent(graphId)}/points${q}`, {
        headers: { Authorization: `Bearer ${tok}` },
      })
      const body = await res.json().catch(() => ({}))
      if (!res.ok) {
        throw new Error(body.detail || `HTTP ${res.status}`)
      }
      if (teamIdRef.current !== _teamAtCall) return
      setTrashInspectId(graphId)
      setTrashInspect(body)
    } catch (e) {
      if (teamIdRef.current === _teamAtCall) {
        // A failed re-inspect (e.g. the graph was purged → 410) must not
        // leave a stale rescue panel open next to the error banner.
        if (trashInspectId === graphId) {
          setTrashInspectId(null)
          setTrashInspect(null)
        }
        setTrashMsg(e.message || 'Could not inspect graph — try again.')
      }
    } finally {
      setGraphBusy(false)
    }
  }

  async function loadMembers(teamId) {
    const tok = sessionTokenRef.current
    if (!tok || !teamId) return
    try {
      const res = await fetch(`${API_BASE}/v1/teams/${teamId}/members`, {
        headers: { Authorization: `Bearer ${tok}` },
      })
      // P3 (code-review): staleness guard — a newer team switch may have
      // landed while this request was in flight.
      if (teamIdRef.current !== teamId) return
      if (res.ok) {
        const data = await res.json()
        // Round-13: re-check after the parse — a switch landing between the
        // guard and the json resolution must not write the old team's members.
        if (teamIdRef.current !== teamId) return
        setMembers(data)
        setMembersStatus('ok')
      } else if (res.status === 403) {
        setMembers(null) // not owner/admin — cannot view
        setMembersStatus('denied')
      } else {
        setMembersStatus('error')
      }
    } catch {
      if (teamIdRef.current === teamId) setMembersStatus('error')
    }
  }

  async function inviteMember() {
    const _teamAtCall = currentTeamId // Round-16: mutation identity guard — a switch mid-flight must not act on the previous team
    if (busy) return // Round-27: in-function double-click guard (disabled attr is click-path only)
    if (!inviteEmail.includes('@')) return
    setBusy(true)
    setError('')
    try {
      const tok = sessionTokenRef.current
      if (!tok) throw new Error('No session')
      const res = await fetch(`${API_BASE}/v1/invites`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${tok}` },
        body: JSON.stringify({ team_id: currentTeamId, email: inviteEmail.trim(), role: inviteRole }),
      })
      if (!res.ok) {
        const b = await res.json().catch(() => ({}))
        if (res.status === 402) {
          // #1875: render the API's detail (upgrade vs at-capacity)
          setError(typeof b.detail === 'string' ? b.detail : 'Invites require the Pro or Team tier — upgrade to invite members.')
          setBusy(false)
          return
        }
        throw new Error(b.detail || `HTTP ${res.status}`)
      }
      setInviteEmail('')
      await loadMembers(currentTeamId)
      // Round-16: bail if the user switched teams mid-flight
      if (teamIdRef.current !== _teamAtCall) return

    } catch (e) {
      // Round-18: a stale request's error must not land under the new team
      if (teamIdRef.current === _teamAtCall) setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  async function removeMember(userId) {
    const _teamAtCall = currentTeamId // Round-16: mutation identity guard — a switch mid-flight must not act on the previous team
    if (busy) return // Round-24/25: double-click guard BEFORE confirm (a second click must not re-pop the dialog)
    if (!confirm('Remove this member from the organization?')) return
    setBusy(true)
    setError('')
    try {
      const tok = sessionTokenRef.current
      if (!tok) throw new Error('No session')
      const res = await fetch(`${API_BASE}/v1/teams/${currentTeamId}/members/${userId}`, {
        method: 'DELETE',
        headers: { Authorization: `Bearer ${tok}` },
      })
      if (!res.ok) {
        const b = await res.json().catch(() => ({}))
        throw new Error(b.detail || `HTTP ${res.status}`)
      }
      await loadMembers(currentTeamId)
      // Round-16: bail if the user switched teams mid-flight
      if (teamIdRef.current !== _teamAtCall) return

    } catch (e) {
      // Round-19: stale DELETE error must not land under the new team
      if (teamIdRef.current === _teamAtCall) setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  async function changeRole(userId, role) {
    const _teamAtCall = currentTeamId // Round-16: mutation identity guard — a switch mid-flight must not act on the previous team
    if (busy) return // Round-24: double-click guard
    setBusy(true)
    setError('')
    try {
      const tok = sessionTokenRef.current
      if (!tok) throw new Error('No session')
      const res = await fetch(`${API_BASE}/v1/teams/${currentTeamId}/members/${userId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${tok}` },
        body: JSON.stringify({ role }),
      })
      if (!res.ok) {
        const b = await res.json().catch(() => ({}))
        throw new Error(b.detail || `HTTP ${res.status}`)
      }
      await loadMembers(currentTeamId)
      // Round-16: bail if the user switched teams mid-flight
      if (teamIdRef.current !== _teamAtCall) return

    } catch (e) {
      // Round-19: stale PATCH error must not land under the new team
      if (teamIdRef.current === _teamAtCall) setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  async function loadBackups(key) {
    const _teamAtCall = teamIdRef.current // Round-10: staleness guard
    // #2167 (rule 2): session-mode /backups pins ?team_id=<selected> and
    // sends NO key header — the old shape team-scoped by the KEY header
    // (a zero-key session whose selected team ≠ first membership rendered
    // the first membership's backups: /backups is ungated server-side, so
    // _session_user_team resolves memberships[0] without the param).
    // Key-mode (authMode 'apikey' — no session JWT exists there) keeps the
    // key header as its authenticator.
    // #1842 P1-2: /backups is session-dual-auth (get_current_team_session_ungated).
    const q = _teamAtCall ? `?team_id=${encodeURIComponent(_teamAtCall)}` : ''
    try {
      const b = await api(`/backups${q}`, sessionTokenRef.current
        ? { useSession: true }
        : (key ? { headers: { Authorization: `Bearer ${key}` } } : {}))
      if (teamIdRef.current !== _teamAtCall) return // stale switch response
      const list = b.backups || []
      setBackupInfo(list.length ? { latest: list[0], count: list.length } : { count: 0 })
      setBackupsStatus('ok')
    } catch {
      // #1923: a transient 503/network failure is TERMINAL — the Backups card
      // flips to '—' immediately (no eternal skeleton) and the Overview loaded
      // announce still fires. Guard on the at-call team: a stale failure from a
      // previous team must not land under the new one. Clear any stale data too
      // — the '—' card must never read as a previous team's count.
      if (teamIdRef.current === _teamAtCall) {
        setBackupInfo(null)
        setBackupsStatus('error')
      }
    }
  }

  // Load team-scoped data whenever the active team changes. Members + graphs
  // are JWT team-scoped; the overview data (team/keys/sessions/backups) is
  // reloaded in switchTeam/completeLogin pinned to the selected team — every
  // read rides the session JWT + ?team_id= (#2246: no held key exists).
  React.useEffect(() => {
    if (currentTeamId) {
      teamIdRef.current = currentTeamId
      // #1893 (code-review P1): the mount-time refreshOnboarding() fired
      // BEFORE the team resolved (unpinned — server resolves memberships[0],
      // which is the WRONG team for a multi-membership user whose SELECTED
      // team is not the first membership). Re-fetch now that the team is
      // known, so onboarding (and the scope surface) track the selected
      // team. teamIdRef is set above, so the pinned GET targets the right
      // team. This also recovers a switch whose onboarding refetch failed
      // (the switchTeam catch swallowed it — the stale flag would otherwise
      // stay set and block hydration forever).
      refreshOnboarding().catch(() => {})
      loadMembers(currentTeamId)
      loadGraphs(currentTeamId)
      loadTrash(currentTeamId) // #2304 owner/admin trash section (no-op for members)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentTeamId])

  // #1842 P2-3: the role=status cue must announce the loading→loaded
  // transition exactly ONCE — keying on `team` alone announced while the
  // per-card shimmers still ran, and keying on the tab re-announced on every
  // switch back to Overview. Gate on data-completeness: the real card frame
  // is up (team) and every card source reached a terminal/loaded state.
  // #1842 P2 (re-review): a REF latch never schedules a render, so the cue
  // text stayed 'Loading overview…' forever unless another re-render
  // happened — STATE, not a ref, so the announce fires via a real update.
  // #1842 P2 (final review): graphs counts as done on ANY terminal state —
  // 'error'/'denied' included — so a failed /v1/graphs still announces
  // "Overview loaded" instead of reading "Loading overview…" forever while
  // the Graphs card shows terminal '—' (mirrors members/backups terminal
  // handling).
  // #1923: backups counts as complete on ANY terminal state — 'error' included —
  // so a failed /backups still announces "Overview loaded" instead of reading
  // "Loading overview…" forever while the Backups card shows terminal '—'
  // (mirrors graphsStatus/membersStatus terminal handling).
  const overviewDataComplete = !!team && graphsStatus !== 'loading' && membersStatus !== 'loading' && backupsStatus !== 'loading'
  const [overviewAnnounced, setOverviewAnnounced] = React.useState(false)
  React.useEffect(() => {
    if (!overviewAnnounced && overviewDataComplete) setOverviewAnnounced(true)
  }, [overviewDataComplete, overviewAnnounced])

  // #1842 P1 (re-review, b): eternal-skeleton floor — if ANY overview
  // skeleton has been showing for > STALE_LOADING_MS (e.g. loadTeams
  // silently no-oped on !tok or a fetch failure, so currentTeamId never
  // pinned and loadMembers/loadGraphs/loadBackups never ran), the card
  // render flips the shimmer to '—' instead of spinning forever.
  // frameStartRef records when the skeleton window began; a 1s interval
  // ticks `now` ONLY while a skeleton is live on the Overview tab (team
  // null, or any card source still loading), then clears itself. The clock
  // resets when the window resolves (a completed frame sets a fresh start
  // for the next skeleton window, e.g. a later team switch).
  const STALE_LOADING_MS = 15000
  const frameStartRef = React.useRef(null)
  const [now, setNow] = React.useState(() => Date.now())
  // #1858 (P3): staleFired LATCHES the floor. frameStale below is derived
  // from the clock (ref + now), so once the tick effect nulls frameStartRef
  // at firing time a later unrelated setState recomputes frameStale=false
  // and resurrects the shimmer for another 15s. STATE, not a ref: the load-
  // site clears (switchTeam / revert / logout) must force a recompute render
  // so the tick effect re-runs and stamps a fresh floor — a ref write
  // schedules no render, so a switch that happened to change no other state
  // would leave the '—' frame up with no clock behind it. (The effect-time
  // set/clear happens to coincide with an already-scheduled render — the
  // clock fire or the data landing — so those two writes add no render.)
  // The latch is per-load: cleared on window resolve, on team→null, and at
  // the three load-initiation sites (covers null→null from the terminal
  // '—' state where the reset effect's team dep doesn't fire).
  const [staleFired, setStaleFired] = React.useState(false)
  // #1842 P2-1 (final review): the clock must NOT run (or stamp frameStart)
  // while the first-timer sits on the welcome screen — the Overview section
  // can't render there, so ticking would re-render the whole app for nothing,
  // AND a lingering welcome (> STALE_LOADING_MS) would open the dashboard
  // with an already-stale frame (instant '—' instead of shimmer for a fast
  // load). Gate overviewSkeletonLive on !welcomeMode; the tick effect's else
  // branch keeps frameStart null while welcome is up.
  // #1858 (review A): also gate on authed — the checking/claim screens
  // render via the early return at !authed, so the Overview skeleton is
  // never visible pre-login. Without the gate, a slow session restore
  // (> STALE_LOADING_MS under the checking screen) would fire the floor and
  // latch staleFired while NO skeleton was ever on screen, then open the
  // dashboard on '—' for a load that just started.
  const overviewSkeletonLive = authed && tab === 'overview' && !welcomeMode &&
    (team === null || graphsStatus === 'loading' || membersStatus === 'loading' || backupsStatus === 'loading')
  // clockStale: raw 15s check against the frame start. frameStale: what the
  // render reads — latched once the floor has ever fired for this load.
  const clockStale = frameStartRef.current !== null && (now - frameStartRef.current) > STALE_LOADING_MS
  const frameStale = staleFired || clockStale
  // #1842 P2-1: reset the frame start when the Overview section actually
  // mounts (team null + Overview tab + not welcome) so no stale stamp carries
  // over from the welcome screen. #1858: every team→null transition here is a
  // genuine new load (switchTeam, its revert path, logout) — clear the stale
  // latch too, or the next team's skeleton would open on '—'. (The window is
  // PER-WINDOW: tab/welcome changes also clear the latch via the tick's
  // !overviewSkeletonLive branch, so leaving Overview and returning re-arms
  // a fresh 15s floor for the same stuck load — pre-fix behavior, accepted.)
  // Declared BEFORE the tick effect so a fresh stamp in the same commit is
  // never clobbered.
  React.useEffect(() => {
    if (tab === 'overview' && !welcomeMode && team === null) {
      frameStartRef.current = null
      setStaleFired(false)
    }
  }, [tab, welcomeMode, team])
  // #1842 P2-2 (final review): gate the tick on !frameStale too — once the
  // skeleton flips to the terminal '—' the clock must terminate (clear the
  // interval), not re-render the app every second forever.
  // #1858 (P2): re-stamp INSIDE the interval callback. The reset effect
  // (deps [tab, welcomeMode, team]) nulls frameStartRef on a mid-load team
  // switch, but neither overviewSkeletonLive nor frameStale change, so this
  // effect never re-runs — without the in-callback re-stamp the old interval
  // would tick with a null ref and frameStale could never become true. The
  // re-stamp fires only when the ref IS null (ref non-null ⟹ the window
  // continues from its original stamp — e.g. a null→null switch mid-load
  // keeps the pre-switch deadline, which is correct: the shimmer has been
  // showing since the load began).
  React.useEffect(() => {
    if (overviewSkeletonLive && !frameStale) {
      if (frameStartRef.current === null) frameStartRef.current = Date.now()
      const id = window.setInterval(() => {
        if (frameStartRef.current === null) frameStartRef.current = Date.now()
        setNow(Date.now())
      }, 1000)
      return () => window.clearInterval(id)
    }
    frameStartRef.current = null
    // #1858 (P3): latch the floor on the raw clock firing, but ONLY while the
    // skeleton window is still live; clear the latch when the window resolves
    // (data landed, tab/welcome changed) so a resolved window never leaves a
    // latch that blocks a legitimate later skeleton.
    if (overviewSkeletonLive && clockStale) setStaleFired(true)
    else if (!overviewSkeletonLive) setStaleFired(false)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [overviewSkeletonLive, frameStale])

  async function createKey() {
    // Round-17 (P3): capture the team AT CALL TIME — the previous guard compared
    // teamIdRef.current to currentTeamId, which are always written together and
    // can never diverge, so it was dead code. Capture to a local and compare
    // against the ref after the await (the round-16 mutation pattern).
    const _teamAtCall = currentTeamId
    if (busy) return // Round-27: in-function double-click guard (disabled attr is click-path only)
    // #2426: a Custom-date preset with no valid in-range date must not mint
    // — the button is also disabled, but Enter-to-create needs the same gate
    // (never silently mint a Never key when the user picked Custom).
    if (newKeyExpiryPreset === 'custom' && !expiryDaysFromDate(newKeyExpiryDate)) return
    setCapNotice('')
    setError('')
    setBusy(true)
    try {
      // #2246 (ADR-010): mintKey rides the session JWT (rule 4) + pins
      // ?team_id= — no held key is involved in session-mode create.
      // #2426: preset → expires_in days (Never → null → no param).
      const days = newKeyExpiryPreset === 'never' ? null
        : (newKeyExpiryPreset === 'custom' ? expiryDaysFromDate(newKeyExpiryDate)
          : Number(newKeyExpiryPreset))
      const mk = await mintKey('', newKeyName.trim() || undefined, days)
      // Identity guard BEFORE any UI write: a team switch during the POST must
      // not render this team's plaintext key card or key table under the new
      // team's header (switchTeam's setNewKey(null) already ran for the new team).
      if (teamIdRef.current !== _teamAtCall) return
      setNewKey((mk && (mk.key || mk.api_key)) || '')
      // #2426: the show-once card states the key's expiry — the authoritative
      // server echo (absent on a Never mint → null → 'never expires').
      setNewKeyExpiresAt((mk && mk.expires_at) || null)
      setNewKeyName('')
      setNewKeyExpiryDate('')
      await loadAll('')
    } catch (e) {
      // Round-18: a stale request's error must not land under the new team
      if (teamIdRef.current === _teamAtCall) {
        // #1147: a tier-cap 402 (hosted_api._check_team_limit) is a LIMIT,
        // not an error — surface the upgrade prompt with the real cap.
        if (e.status === 402) {
          setCapNotice(upgradeNoticeFrom(e.message, team))
          setError('')
        } else {
          setError(e.message)
        }
      }
    } finally {
      setBusy(false)
    }
  }

  async function regenerateKey(keyId) {
    // #1147/#2229: rotate = mint the REPLACEMENT first (the old key still
    // authorizes the request), then revoke the old — a single mint (no
    // bootstrap-pool growth), session-authed. The old row's label carries
    // into the replacement mint so an in-place rotate keeps the row's
    // identity. Available on every tier: regenerating does not grow the key
    // count.
    // #2246 (ADR-010): rotate is now available on EVERY durable row (uniform
    // table actions) and NEVER installs the replacement into the browser — no
    // localStorage/teamKeysRef/apiKey write. The replacement is shown once
    // (setRotatedKey) for the user to configure into their agent; the old key
    // is revoked.
    if (busy) return
    const row0 = (keys || []).find((k) => (k.id || k.key_id) === keyId)
    const rowName = (row0 && row0.name) || 'this API key'
    // #2246 (PM-1): the confirm names the row (name · prefix · created) so a
    // one-click rotate never silently kills an agent key the user cannot
    // identify (rows are hash-only; names may be unset).
    // #2426: the confirm ALSO states the replacement's expiry — the old
    // key's lifetime span is re-applied from mint-time with a fresh clock
    // (Cloudflare 'resets relative to now' semantics); Never stays Never.
    const rowLifetime = lifetimeDaysFromRow(row0)
    const replacementExpiry = rowLifetime
      ? `The replacement expires ${fmtExpiryDate(new Date(Date.now() + rowLifetime * _MS_PER_DAY).toISOString())} (the same ${rowLifetime}-day lifetime as this key).`
      : 'The replacement never expires (same as this key).'
    if (!confirm(`Rotate ${keyRowDisclosure(row0, 'this API key')}? A replacement key is created (shown once) and ${rowName} is revoked — applications using the old key will stop working. ${replacementExpiry}`)) return
    setCapNotice('')
    setError('')
    setBusy(true)
    try {
      const _teamAtCall = currentTeamId
      // #2229: label carry-over — the row may leave the closure list mid-
      // flight (switch/refresh) — degrade to an unlabeled mint.
      const oldRow = (keys || []).find((k) => (k.id || k.key_id) === keyId)
      // #2426: rotate re-applies the old row's lifetime span (expires_in
      // days from expires_at − created_at; Never → null → no param).
      const mk = await mintKey('', (oldRow && oldRow.name) || undefined,
                               lifetimeDaysFromRow(oldRow))
      // Round-29 (review P1): NEVER revoke without a confirmed target — if
      // the team moved during the mint RTT, bail BEFORE the destructive leg
      // (the old row may not belong to the now-selected team). The minted
      // replacement stays as a visible team-A durable (same accepted orphan
      // semantics as createKey's identity guard).
      if (teamIdRef.current !== _teamAtCall) return
      await revokeKey(keyId, { skipConfirm: true })
      if (teamIdRef.current !== _teamAtCall) return
      // #2246: no held install — the replacement is shown once and managed
      // from the table like any other durable. #2735: its OWN reveal state
      // (rotatedKey), never the create modal's newKey — the create modal's
      // dismiss paths clear newKey, which would destroy this unread
      // replacement (the old key is already revoked by this point).
      setRotatedKey({ plaintext: (mk && (mk.key || mk.api_key)) || '', expiresAt: (mk && mk.expires_at) || null })
      await loadAll('')
    } catch (e) {
      if (teamIdRef.current === currentTeamId) {
        if (e.status === 402) {
          // #2229: rotate-specific cap copy — see rotateCapNoticeFrom.
          setCapNotice(rotateCapNoticeFrom(e.message, team))
          setError('')
        } else {
          setError(e.message)
        }
      }
    } finally {
      setBusy(false)
    }
  }

  async function toggleKeyEnabled(keyId, currentEnabled) {
    // #1148-ux review: enable/disable an API key. Server PATCH flips the
    // key's enabled flag (default true = new keys are on). Optimistic local
    // update; revert on error. A disabled key stops authenticating but stays
    // listed (re-enabled anytime).
    setCapNotice('')
    setError('')
    const next = !currentEnabled
    // Round-20: capture the team at call — a mid-flight switch must not land
    // the response/error under the new team's header (mirrors renameKey).
    const _teamAtCall = currentTeamId
    setKeys((prev) => prev.map((k) => k.id === keyId ? { ...k, enabled: next } : k))
    try {
      // #2230: session-mode key-management PATCH pins the SELECTED team —
      // the server PATCH honors ?team_id= membership-checked (fail-closed on
      // a key outside the pinned team), so a multi-membership user on a
      // non-first team toggles the key their table shows, never the default
      // membership's (mirrors #2167 rule-4 create-side pin). The endpoint is
      // session-only (#1148 — a raw key 401s here), so the session-gated
      // pin covers the only reachable auth surface.
      const q = (sessionTokenRef.current && _teamAtCall) ? `?team_id=${encodeURIComponent(_teamAtCall)}` : ''
      const updated = await api(`/v1/team/keys/${keyId}${q}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        useSession: true,  // #1148: management → session JWT when signed in
        body: JSON.stringify({ enabled: next }),
      })
      if (teamIdRef.current !== _teamAtCall) return // stale switch — don't touch the new team's state
      if (updated && (updated.id || updated.key_id)) {
        setKeys((prev) => prev.map((k) => k.id === keyId ? { ...k, enabled: updated.enabled !== false } : k))
      }
    } catch (e) {
      if (teamIdRef.current !== _teamAtCall) return // stale switch — error belongs to the old team
      setKeys((prev) => prev.map((k) => k.id === keyId ? { ...k, enabled: currentEnabled } : k))
      setError((e && e.message) || `Couldn't toggle the key — try again.`)
    }
  }

  async function renameKey(keyId, name) {
    // key-label: PATCH the key's name via the same session-authed endpoint as
    // the enabled toggle (PATCH /v1/team/keys/{id}, body {name}). Optimistic
    // local update; revert on error. Empty/whitespace → unnamed (server
    // stores NULL). 64-char cap mirrors the server's KEY_NAME_MAX.
    // Round-28 (code-review P1): send ONLY {name} — echoing a stale `enabled`
    // snapshot could silently re-enable a key disabled in another session/
    // tab (rename must never touch auth state). Round-20: capture the team at
    // call; a stale rename's error must not land under the new team's header.
    const _teamAtCall = currentTeamId
    setEditingKeyId(null)
    setError('')
    const next = (name || '').trim().slice(0, 64) || null
    const cur = keys.find((k) => k.id === keyId)
    // No-op guard: also dedupes the Enter→blur double-fire (blur after Enter
    // sees the label already applied via the optimistic update's re-render).
    if (!cur || (cur.name || null) === next) return
    const prevName = cur.name || null
    setKeys((ks) => ks.map((k) => k.id === keyId ? { ...k, name: next } : k))
    try {
      // #2230: pin the SELECTED team on the rename PATCH (session mode) —
      // the URL is built from the at-call team so a mid-flight switch can
      // never target the new team's key nor the default membership's (the
      // post-await staleness guard below then drops the stale response).
      const q = (sessionTokenRef.current && _teamAtCall) ? `?team_id=${encodeURIComponent(_teamAtCall)}` : ''
      const updated = await api(`/v1/team/keys/${keyId}${q}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        useSession: true,  // #1148: management → session JWT when signed in
        body: JSON.stringify({ name: next }),
      })
      if (teamIdRef.current !== _teamAtCall) return // stale switch — don't touch the new team's state
      if (updated && (updated.id || updated.key_id)) {
        setKeys((ks) => ks.map((k) => k.id === keyId ? { ...k, name: next } : k))
      }
    } catch (e) {
      if (teamIdRef.current !== _teamAtCall) return // stale switch — error belongs to the old team
      setKeys((ks) => ks.map((k) => k.id === keyId ? { ...k, name: prevName } : k))
      setError((e && e.message) || `Couldn't rename the key — try again.`)
    }
  }

  async function revokeKey(keyId, opts = {}) {
    // Round-20 (P2): capture team at call — a mid-flight switch must not land
    // the old team's key table under the new header.
    const _teamAtCall = currentTeamId
    const row0 = (keys || []).find((k) => (k.id || k.key_id) === keyId)
    // #2246 (PM-1): the confirm names the row (name · prefix · created) so a
    // one-click trash never silently kills an agent key the user cannot
    // identify (rows are hash-only; names may be unset).
    if (!opts.skipConfirm && !confirm(`Revoke ${keyRowDisclosure(row0, 'this API key')}? Applications using it will stop working.`)) return
    setCapNotice('')
    setError('')
    try {
      // #2246 (ADR-010): the rule-7 slot-aware clear (heldKeyClearState) is
      // DELETED — the browser never holds key material, so a revoke has
      // nothing to clear (the KEY_STORAGE slot was purged at session
      // resolution; logout wipes it). #2230: pin the SELECTED team on the
      // revoke DELETE (session mode) — the server resolves the session team
      // from memberships[0] without it, so a multi-membership user whose
      // selected team ≠ first membership got 403 "Not your API key"
      // revoking their own key (mirrors #2167 rule-4 create-side pin + the
      // toggle/rename PATCH pins; the session lane honors it
      // membership-checked). Key-mode (no session JWT) stays unpinned.
      const q = (sessionTokenRef.current && _teamAtCall) ? `?team_id=${encodeURIComponent(_teamAtCall)}` : ''
      await api(`/v1/team/keys/${keyId}${q}`, { method: 'DELETE', useSession: true })
      // Round-20: bail after the DELETE — a switch already reloaded the new
      // team's state; skip the stale loadAll entirely.
      if (teamIdRef.current !== _teamAtCall) return
      // #2246 (review, P2): revoke-to-empty clears the matching in-memory
      // plaintext BEFORE the reload. loadAll landing keys=[] must read as
      // "not loaded" to the row-truth effect below (it gates on
      // keys.length === 0 to preserve the pre-load welcome reveal) — so
      // revoking the LAST/ONLY key would otherwise leave
      // welcomeKey/wizardDurableKey alive past their row's death: the
      // overview "live" claims and the connect step keep embedding the
      // REVOKED key (an empty-tail the effect cannot see). The direct
      // prefix clear closes it. Also covers regenerateKey's rotate (it
      // revokes the old row via revokeKey skipConfirm) — the replacement
      // is shown via setRotatedKey, and the welcome plaintext must not
      // survive its own row's rotation.
      if (row0 && row0.key_prefix) {
        if (welcomeKey && welcomeKey.startsWith(row0.key_prefix)) setWelcomeKey('')
        if (wizardDurableKey && wizardDurableKey.startsWith(row0.key_prefix)) setWizardDurableKey('')
      }
      await loadAll()
    } catch (e) {
      // Round-18/20: a stale revoke's error must not land under the new team
      if (!_teamAtCall || teamIdRef.current === _teamAtCall) setError(e.message)
    }
  }

  // #2246 (ADR-010): the client-side key-recovery flow (recoverKey, the last
  // POST /v1/session/key consumer) is DELETED — it was UI-dead (the "Lost your
  // key?" affordance was removed in #1148; zero call sites) and its held-key
  // install would violate the never-hold invariant if ever re-wired. The POST
  // /v1/session/key endpoint + the recovery purpose remain for non-dashboard
  // consumers (selfhost keyless teams, SDK/CLI, contract suites); dashboard
  // users create/rotate keys from the API Keys tab (each shown once).
  // keys[] stays unfiltered in state so managedKeys (the isManagedKey render
  // filter below) and durableConnectKey's rows-resolution keep working off
  // the full payload (#2246). #2426: managedKeys now INCLUDES expiring
  // durable rows (bootstrap-exclusion only), so the table shows their
  // Expires column state (soon/expired tombstones stay listed).
  const managedKeys = (keys || []).filter(isManagedKey)

  // #2246 (review, P1): row-truth invalidation of in-memory plaintext. Post-
  // #2246 the keys-table actions are uniform one-click (Rotate/revoke/disable
  // on every durable row) — rotating/revoking/disabling the row whose
  // plaintext we hold in memory (welcomeKey — the first-timer's shown-once
  // reveal — or wizardDurableKey — a connect-step mint/paste) must invalidate
  // that in-memory plaintext so the snippet/connect surfaces re-gate. The
  // deleted classifyHeldKey drop hook owned this invariant pre-#2246 (the
  // held slot was cleared when its row died); with no held slot these state
  // strings are the only key material left, so the keys-table reload is the
  // row-truth source. Guard: keys null/[] = not loaded yet — do nothing, so
  // the pre-load welcome reveal is never cleared (the provisioned row IS in
  // the loadAll response after finishWelcomeLoads). Deps [keys] only: a
  // clear changes welcomeKey/wizardDurableKey, never keys, so no loop.
  React.useEffect(() => {
    if (!keys || keys.length === 0) return
    const rowFor = (plaintext) => {
      if (!plaintext) return null
      return keys.find((k) => k && k.key_prefix && plaintext.startsWith(k.key_prefix)) || null
    }
    for (const [plaintext, clear] of [[welcomeKey, setWelcomeKey], [wizardDurableKey, setWizardDurableKey]]) {
      if (!plaintext) continue
      const r = rowFor(plaintext)
      // #2246 (review, P2): a bootstrap/expiring row is dead for embedding
      // too — symmetric with durableConnectKey's paste tail (sessionKey.js,
      // identical predicate): a 24h session credential (or any expiring
      // row) must never back an embed, so a plaintext whose row later
      // resolves to one is invalidated like any revoked/disabled row.
      // First-timer welcome keys are provisioned (created_via
      // 'provisioned'), so this never fires on the legit reveal.
      if (!r || r.revoked_at || r.enabled === false || r.created_via === 'bootstrap' || !!r.expires_at) clear('')
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [keys])

  // #714 (main): session detail view
  async function fetchSessionDetail(sessionId) {
    setDetailLoading(true)
    setSessionDetailError(null)
    try {
      // #2002 (W6): the Settings transcript View (DE2E-11) reads on the
      // session JWT + the selected team (multi-membership parity with
      // list_sessions' ?team_id= pin — a stale team's detail must never
      // land under the current team's Settings home).
      const detail = await api(`/v1/sessions/${encodeURIComponent(sessionId)}${onboardingTeamQ()}`, { useSession: true })
      if (detail && detail.session === null) {
        // #1591 graph fail-soft: the server could not read the graph —
        // honest inline error, never a fabricated empty transcript.
        setSessionDetail(null)
        setSessionDetailError('Could not load this session right now — try again in a moment.')
        return null
      }
      setSessionDetail(detail)
      setSelectedSessionId(sessionId)
      return detail
    } catch (e) {
      setSessionDetail(null)
      setSessionDetailError((e && e.message) || 'Could not load this session.')
      return null
    } finally {
      setDetailLoading(false)
    }
  }

  function clearSessionDetail() {
    setSelectedSessionId(null)
    setSessionDetail(null)
    setSessionDetailError(null)
  }

  // #2002 (W6): delete a captured session (Settings Captured-sessions home,
  // DE2E-11). DELETE /v1/sessions/{id} removes the transcript + extracted
  // memory nodes AND the capture receipt when it was the bucket's last
  // session (server recompute). The list mutates locally via the pure
  // removeSession filter (no refetch race), then onboarding refreshes so the
  // Memory-sources capture-status rows reflect the server's receipt cleanup.
  async function deleteCapturedSession(sessionId) {
    if (sessionDeletingId) return // single-flight: one delete at a time
    setSessionDeletingId(sessionId)
    setSessionsActionError(null)
    try {
      await api(`/v1/sessions/${encodeURIComponent(sessionId)}${onboardingTeamQ()}`, { method: 'DELETE', useSession: true })
      setSessions((prev) => removeSession(prev, sessionId))
      if (selectedSessionId === sessionId) clearSessionDetail()
      await refreshOnboarding()
    } catch (e) {
      setSessionsActionError((e && e.message) || 'Could not delete this session — try again.')
    } finally {
      setSessionDeletingId(null)
    }
  }

  function fmtTime(iso) {
    if (!iso) return '—'
    try {
      return new Date(iso).toLocaleString()
    } catch {
      return iso
    }
  }

  if (checking) {
    return (
      <div className="auth-wrap">
        <div className="auth-card">
          <div className="logo">Tortoise</div>
          <h1>Dashboard</h1>
          <p className="dim">Checking your session…</p>
        </div>
      </div>
    )
  }

  if (!authed) {
    // #1494/#1511: the dashboard NEVER shows a login/key-only screen. The
    // head gate + mount effect redirect every no-session/no-claim visitor to
    // /auth instantly. This render is reachable only when claim-intent is in
    // flight (paste tt_ → OAuth → claim; D2) — the claim-paste screen — or
    // the split-second before the redirect lands (the shell).
    const claimIntent = claimIntentInFlight()
    if (!claimIntent) {
      // #1559: a mount failure (429/5xx on session resolution or team
      // load, auth lib blocked — #2246: no session-key mint runs in the
      // !authed mount window) renders a REAL error card with a retry —
      // never the silent "Redirecting…" shell (which only ever
      // accompanied an ACTUAL navigation).
      if (authUnavailable || mountError) {
        return (
          <div className="auth-wrap">
            <div className="auth-card">
              <div className="logo">Tortoise</div>
              <h1>Dashboard</h1>
              <div role="alert">
                <p className="error">{authUnavailable || mountError}</p>
                {suspended && suspended.appeal_url ? (
                  // #308: the appeal CTA must be reachable even when the
                  // team is suspended pre-render (the authed banner is not
                  // reachable in this state — review P2).
                  <p className="dim" style={{ marginTop: 12 }}>
                    <a href={suspended.appeal_url} target="_blank" rel="noreferrer">Appeal the suspension →</a>
                  </p>
                ) : null}
                <button type="button" className="btn-submit" onClick={() => window.location.reload()}>Try again</button>
                <p className="dim" style={{ marginTop: 12 }}>
                  Still stuck? Contact <a href="mailto:hello@premiselabs.co">hello@premiselabs.co</a>.
                </p>
              </div>
            </div>
          </div>
        )
      }
      // Redirect shell — the mount effect's bounceToAuth() owns the redirect.
      return (
        <div className="auth-wrap">
          <div className="auth-card">
            <div className="logo">Tortoise</div>
            <h1>Dashboard</h1>
            <p className="dim">Redirecting to the sign-in page…</p>
          </div>
        </div>
      )
    }
    // Claim-paste: an unclaimed team's key is pasted HERE (the /auth exchange
    // funnels ANON_TEAM_NO_OWNER → ?claim=1; the raw key never crosses
    // origins — it is re-collected on this origin, #1082). The paste wires
    // apiKeyRef.current so claimSignIn/claimEmailPassword work unchanged.
    const handlePasteKey = (e) => { setApiKey(e.target.value); apiKeyRef.current = e.target.value.trim(); setClaimError('') }
    return (
      <div className="app">
        <header>
          <div className="logo">Tortoise</div>
        </header>
        <main>
          <div className="protect-banner protect-full">
            <h2 className="protect-banner-title">🔑 Claim your organization</h2>
            <p>
              Paste the key for your unclaimed organization, then attach a login to
              finish setting up your account.
            </p>
            <div className="inline-form claim-email-form">
              <input
                type="password"
                placeholder="tt_..."
                aria-label="API key"
                value={apiKey}
                onChange={handlePasteKey}
                autoFocus
                autoComplete="one-time-code"
              />
            </div>
            <div className="claim-actions">
              <button onClick={() => claimSignIn('github')} disabled={claimBusy}>
                {claimBusy ? 'Redirecting…' : 'Connect GitHub'}
              </button>
              <button onClick={() => claimSignIn('google')} disabled={claimBusy}>
                Connect Google login
              </button>
              <button className="ghost" onClick={() => setClaimShowEmail(!claimShowEmail)} disabled={claimBusy}>
                Connect email and password
              </button>
            </div>
            {claimShowEmail && (
              <form
                className="inline-form claim-email-form"
                onSubmit={(e) => { e.preventDefault(); claimEmailPassword() }}
              >
                <input
                  type="email"
                  placeholder="you@example.com"
                  aria-label="Email"
                  value={claimEmail}
                  onChange={(e) => { setClaimEmail(e.target.value); setClaimError('') }}
                  autoComplete="email"
                />
                <input
                  type="password"
                  placeholder="Password (min 6 chars)"
                  aria-label="Password"
                  value={claimPassword}
                  onChange={(e) => { setClaimPassword(e.target.value); setClaimError('') }}
                  autoComplete="new-password"
                  minLength={6}
                />
                <button type="submit" disabled={claimBusy || !claimEmail.includes('@') || claimPassword.length < 6}>
                  {claimBusy ? 'Connecting…' : 'Connect email & password'}
                </button>
              </form>
            )}
            {claimError && <p className="error" role="alert">{claimError}</p>}
            <p className="dim">
              <a href="https://tortoise.premiselabs.co/auth">← Back to sign in</a>
            </p>
          </div>
        </main>
      </div>
    )
  }

  // #1148-ux review: the dashboard is SESSION-gated. A key-login user on a
  // CLAIMED team is redirected to session sign-in (key login is only a
  // bootstrap for anonymous teams). An ANON team sees the full-page Protect
  // screen (the path to session auth) — no tabs, no content.
  if (authed && authMode !== 'session' && team && !team.anon) {
    // Claimed team via key login → require session. Redirect to sign-in.
    return (
      <div className="auth-wrap">
        <div className="auth-card">
          <div className="logo">Tortoise</div>
          <h1>Dashboard</h1>
          <p className="dim">
            This team requires a GitHub/Google sign-in to manage the dashboard
            (API keys remain valid for graph operations).
          </p>
          <p className="dim small">
            <a href="https://tortoise.premiselabs.co/auth" target="_blank" rel="noreferrer">
              Sign in with GitHub or Google →
            </a>
          </p>
        </div>
      </div>
    )
  }
  if (authed && authMode !== 'session' && team && team.anon) {
    // ANON team via key login → full-page Protect screen (connect a login to
    // get session access). No tabs, no content — session is the gate.
    return (
      <div className="app">
        <header>
          <div className="logo">Tortoise</div>
        </header>
        <main>
          <div className="protect-banner protect-full">
            <h2 className="protect-banner-title">🔐 Protect your account</h2>
            <p>
              Attach your GitHub or Google account to enable key rotation and
              recovery. Otherwise your account is unrecoverable if you lose your
              API key.
            </p>
            <div className="claim-actions">
              <button onClick={() => claimSignIn('github')} disabled={claimBusy}>
                {claimBusy ? 'Redirecting…' : 'Connect GitHub'}
              </button>
              <button onClick={() => claimSignIn('google')} disabled={claimBusy}>
                Connect Google login
              </button>
              <button className="ghost" onClick={() => setClaimShowEmail(!claimShowEmail)} disabled={claimBusy}>
                Connect email and password
              </button>
            </div>
            {claimShowEmail && (
              <form
                className="inline-form claim-email-form"
                onSubmit={(e) => { e.preventDefault(); claimEmailPassword() }}
              >
                <input
                  type="email"
                  placeholder="you@example.com"
                  aria-label="Email"
                  value={claimEmail}
                  onChange={(e) => { setClaimEmail(e.target.value); setClaimError('') }}
                  autoComplete="email"
                />
                <input
                  type="password"
                  placeholder="Password (min 6 chars)"
                  aria-label="Password"
                  value={claimPassword}
                  onChange={(e) => { setClaimPassword(e.target.value); setClaimError('') }}
                  autoComplete="new-password"
                  minLength={6}
                />
                <button type="submit" disabled={claimBusy || !claimEmail.includes('@') || claimPassword.length < 6}>
                  {claimBusy ? 'Connecting…' : 'Connect email & password'}
                </button>
              </form>
            )}
            {claimError && <p className="error" role="alert">{claimError}</p>}
            <p className="dim small">
              Prefer zero-email? You can keep using your API key for graph
              operations — this is only about dashboard access.
            </p>
          </div>
        </main>
      </div>
    )
  }

  // #1287: welcome-as-dashboard-subpage — first-time users land on
  // app.premiselabs.co/welcome after signup (welcome.html did the
  // provisioning + key reveal-once; this is the in-dashboard onboarding:
  // chooser + routes to the API Keys tab where the key lives).
  // #1643/#1692: the re-entry card covers EVERY empty-graph state —
  // graph_ready may be false (missing graph — the seed write recovers it)
  // or true with 0 points. It re-opens the wizard at step 0 (harness).
  // When it shows, the legacy empty-state cards hide.
  const showReentryCard = !welcomeMode && !onboardingComplete &&
    team && (team.point_count ?? 0) === 0 && !wizardDone
  // #1831 P2-1 / #2246: the wizard's setup commands embed the user's key —
  // never emit `Bearer ` with an empty key; fall back to a create-a-key
  // message instead (see the wizard step-0 render below).
  // #1998 fold-in (PR #2161 finding): the embedded key must be DURABLE — a
  // bootstrap access credential (created_via 'bootstrap', expires_at =
  // now+24h) stops authenticating within a day and would kill any agent
  // configured with it. #2246 (ADR-010): the browser never holds such a
  // credential (the "minted at login" holder is gone) — the gate sources
  // from the usable durable ROWS or the first-timer welcomeKey, and
  // wizardDurableKey = a durable key minted (or pasted) at the connect step
  // this session. A bootstrap/unknown key is NEVER embedded; the connect step
  // shows the durable-key gate (mint via POST /v1/team/keys or route to the
  // API Keys tab) instead.
  // #2246 (ADR-010): session mode passes apiKey '' (no held key) —
  // durableConnectKey resolves the gate from the keys-table ROWS
  // (usableDurableRows): 'welcome' (first-timer reveal) or wizardDurableKey
  // supply the embeddable plaintext; otherwise the gate copy routes to
  // create/rotate/paste ('rows-durable' when a usable durable exists, 'none'
  // when not).
  const durableConnect = durableConnectKey(welcomeKey, '', keys)
  const harnessKey = wizardDurableKey || durableConnect.key || ''
  // #2323 (Option B): name-first first-run — an org exists once the wizard
  // provisioned it (welcomeTeamReady) or the account already held one
  // (re-entry / invite-held paths). Drives the welcome exit gate + heading.
  const welcomeHasOrg = welcomeTeamReady || (Array.isArray(teams) && teams.length > 0)
  // The org name shown in the welcome header/step-1 summary is the CURRENT
  // team's name — welcomeTeamName (the first-run provision) only wins right
  // after creation, before loadTeams pins `team` (code-review P2: a stale
  // welcomeTeamName across a team switch must never label the wrong org).
  const shownOrgName =
    (welcomeTeamReady && welcomeTeamName) ||
    (team && team.team_name) ||
    (Array.isArray(teams)
      ? ((teams.find((t) => t.team_id === teamIdRef.current) || teams[0] || {}).team_name || '')
      : '') || ''
  // #2328/#2912: the connect step renders one of two Codex surfaces — 'codex'
  // (CLI) or 'codexDesktop' (GUI). The surface IS the leaf harness value now,
  // so this is an identity mapping kept as the single read-point for every
  // per-harness lookup below (the old boolean derivation is gone).
  const wizardConnectHarness = wizardHarness
  // #1998 fork-aware connect: the fork choice determines what the connect step
  // shows — 'build' users see API key + SDK code; 'self' users see harness
  // picker + setup command.
  const wizardFork = wizardForkChosen || (onboarding && onboarding.fork) || ''
  const isBuildFork = wizardFork === 'build'

  // #2710: the wizard's paste escape (shared verbatim with the member/capped
  // branch below — same markup, same validation, no copy or IA change). The
  // connect step exposes it behind "I already have a key — paste it instead"
  // for owner/admins who hold a durable key whose plaintext they still have;
  // wizardMintDurableKey's 402 cap remedy also opens it.
  const wizardPasteRow = (
    <>
      <div id="wizard-paste-row" style={{ marginTop: '0.75rem', display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
        <input type="password" aria-label="Paste an API key" placeholder="Paste an API key (tt_…)"
          value={wizardDurablePaste}
          onChange={(e) => { setWizardDurablePaste(e.target.value); setWizardDurableError('') }}
          onKeyDown={(e) => { if (e.key === 'Enter' && wizardDurablePaste.trim()) { e.preventDefault(); document.querySelector('[data-paste-use]')?.click() } }}
          style={{ flex: 1, minWidth: 0, padding: '0.5rem 0.65rem', background: 'var(--surface,#0d1a2d)', border: '1px solid var(--border,#1e293b)', borderRadius: 8, fontSize: 13, color: 'inherit' }} />
        <button data-paste-use type="button" className="ghost" disabled={!wizardDurablePaste.trim()}
          onClick={() => {
            const pasted = wizardDurablePaste.trim()
            if (!pasted) return
            if (!/^tt_/.test(pasted)) {
              setWizardDurableError('That does not look like a Tortoise API key (tt_…). Paste the full key from the API Keys tab.')
              return
            }
            const check = durableConnectKey('', pasted, keys)
            if (check.source === 'unknown') {
              setWizardDurableError('That key does not match any key in this organization. Paste a key from this organization\'s API Keys tab, or ask an owner/admin to create one.')
              return
            }
            if (check.source === 'bootstrap') {
              setWizardDurableError(`That key can\'t be used — it was created for a login session and stops working after 24 hours. ${isOwnerAdmin ? 'Create a new key in the API Keys tab.' : 'Ask an owner or admin to create a new key for you.'}`)
              return
            }
            if (check.source === 'expiring') {
              setWizardDurableError(`It expires, and a key embedded in an agent must never expire. ${isOwnerAdmin ? 'Rotate it in the API Keys tab and paste the replacement, or create a new key with No expiration.' : 'Ask an owner or admin to create or rotate a key for you.'}`)
              return
            }
            if (check.source === 'revoked' || check.source === 'disabled') {
              setWizardDurableError(`That key can't be used — it is revoked or disabled. ${isOwnerAdmin ? 'Create or rotate a key in the API Keys tab and paste the new one.' : 'Ask an owner or admin to create or rotate a key for you.'}`)
              return
            }
            setWizardDurableKey(pasted)
            setWizardDurablePaste('')
          }}>Use this key</button>
      </div>
      {wizardDurableError && (
        <p className="error" role="alert" style={{ margin: '0.6rem 0 0', fontSize: 13 }}>{wizardDurableError}</p>
      )}
    </>
  )

  // #2711: a shown-once key token is an unbreakable `tt_…` string. One shared
  // `.key-row` style carries every raw-key row (the step-1 key block for each
  // leaf except Codex Desktop, whose key lives in the config block) so the row
  // can shrink and the token can break
  // — without `minWidth: 0` the flex item's min-content width is the whole
  // token and the Copy button is pushed off-screen at 390px.
  const wizardKeyCodeStyle = { flex: 1, minWidth: 0, overflowWrap: 'anywhere', wordBreak: 'break-all', padding: '0.4rem 0.6rem', background: 'var(--surface,#0d1a2d)', border: '1px solid var(--border,#1e293b)', borderRadius: 6, fontSize: 13 }

  // #2827 (round-2 P2): the connector request-header literal is ONE breakable
  // token on the Claude Desktop/Web tabs. It used to be split across two
  // <code> elements joined by `=` — wrong punctuation (`:` is what the
  // connector field expects) and impossible to copy in one gesture.
  const wizardHeaderCodeStyle = { minWidth: 0, overflowWrap: 'anywhere', wordBreak: 'break-all' }

  // #2710: the connect step's no-key affordance. The copy already promised
  // "Create an API key to see the setup prompt." but shipped no button, so an
  // owner/admin with no in-memory key hit a dead end (the prompt cards are
  // key-gated). The mint rides wizardMintDurableKey — the same silent,
  // Never-expiring mint the build fork uses (mintKey with NO expires_in → the
  // key never expires, matching the step's own hint and the Never-only embed
  // contract #2426 decision 2). The paste row covers "I already have one".
  const wizardNoKeyAffordance = (
    <>
      <p className="dim small">Create an API key to see the setup prompt.</p>
      <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center', flexWrap: 'wrap', marginTop: '0.5rem' }}>
        <button type="button" className="btn-primary small" onClick={wizardMintDurableKey} disabled={wizardDurableBusy}>
          {wizardDurableBusy ? 'Creating…' : 'Create an API key'}
        </button>
        <button type="button" className="ghost small" aria-expanded={wizardShowPaste}
          aria-controls={wizardShowPaste ? 'wizard-paste-row' : undefined}
          onClick={() => setWizardShowPaste((v) => !v)}>
          I already have a key — paste it instead
        </button>
      </div>
      {wizardShowPaste && wizardPasteRow}
      {/* #2710 (code-review P1): wizardDurableError is rendered inside
          wizardPasteRow, but only the 402 cap opens the disclosure — a
          suspension 403, a transport failure, or the #2326 team-switch guard
          would otherwise be INVISIBLE next to the new mint CTA (the button just
          flips back from "Creating…"). Render it here while the row is hidden
          so there is exactly one alert. */}
      {!wizardShowPaste && wizardDurableError && (
        <p className="error" role="alert" style={{ margin: '0.6rem 0 0', fontSize: 13 }}>{wizardDurableError}</p>
      )}
    </>
  )

  if (welcomeMode && authed) {
    // #2323 (Option B): name-first first-run — the welcome card renders the
    // W1 wizard, never the key. There is NO mount provisioning (#1566's
    // mount-time provision + ready-card reveal are deleted): the provisioning
    // spinner + inline error below wrap ONLY the org-create step submit, and
    // the provisioned plaintext is held in-memory (welcomeKey) and shown
    // exactly once at the CONNECT step. Teamless first-timers land here from
    // the session gate; org-holding accounts only via re-entry/resume (the
    // read-only steps below).
    return (
      <div className="app">
        <header>
          <div className="logo">Tortoise</div>
          <nav />
          {/* #1906 (code-review P1): disabled while provisioning OR on the
              terminal error/claim cards — exiting fires finishWelcomeLoads
              against nothing (no teamIdRef pin → loadAll 403 → 'API Keys 0'
              + a false error banner until reload). The spinner window is
              transient; the error/claim cards' own CTAs (Try again / Go
              claim my team) own the recovery. */}
          <button
            className="ghost small"
            disabled={welcomeProvisioning || welcomeProvisionError || !welcomeHasOrg}
            onClick={() => { window.history.replaceState({}, '', '#/' + tab); setWelcomeMode(false); setWizardDurableKey(''); setWizardDurablePaste(''); setWizardDurableError(''); setWizardShowPaste(false); setWizardPaused(false); setKeyModalOpen(false); setKeyModalStage('form'); connectedOnceRef.current = false; if (wizardStep >= 2) setWelcomeKey(''); finishWelcomeLoads() }}
          >
            Open my dashboard →
          </button>
        </header>
        <main>
          <div className="welcome-card" style={{ maxWidth: 560, margin: '0 auto', padding: '1rem 0' }}>
            {welcomeProvisioning ? (
              <>
                <h1 style={{ fontFamily: 'var(--serif, Georgia, serif)', fontWeight: 400, marginBottom: '0.5rem' }}>
                  Creating your Organization…
                </h1>
                <p className="dim">Creating your Organization and API key — one moment.</p>
              </>
            ) : welcomeProvisionError ? (
              <>
                <h1 style={{ fontFamily: 'var(--serif, Georgia, serif)', fontWeight: 400, marginBottom: '0.5rem' }}>
                  We couldn't finish setting up your organization
                </h1>
                <p className="error" role="alert">{welcomeProvisionError}</p>
                <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
                  {/anonymous organization waiting/.test(welcomeProvisionError) ? (
                    // #1566 (code-review P2): the claim-guard must not
                    // dead-end — the claim card is the escape.
                    <a className="btn-primary" href="https://app.premiselabs.co/?claim=1">Go claim my organization →</a>
                  ) : (
                    <button className="btn-primary" onClick={() => window.location.reload()}>Try again</button>
                  )}
                  <p className="dim">Still stuck? Contact <a href="mailto:hello@premiselabs.co">hello@premiselabs.co</a>.</p>
                </div>
              </>
            ) : (
              <>
                {/* #2912: the header names the STAGE ("Connect your agent"),
                    not the org's status ("<org> is set up") — the old heading was
                    a receipt for step 0 sitting above every later step, so it
                    said nothing about what the user was doing. The org stays
                    visible as a small eyebrow, and the step's own sub moves up
                    here so the card body starts at the controls. */}
                <div className="welcome-head">
                  {welcomeHasOrg && shownOrgName && (
                    <p className="welcome-eyebrow">{shownOrgName}</p>
                  )}
                  <h1 className="welcome-title">
                    {wizardStageLabel(wizardStep, { hasOrg: welcomeHasOrg, paused: effectivelyPaused })}
                  </h1>
                  {(() => {
                    // Step 0 on an org-holding account is a read-only summary
                    // whose body already says "You're set up in <org>…" — a
                    // second line here would repeat it. Same for the paused
                    // reconnect: its <h1> already names the state, and the step-3
                    // body carries the recovery (PR-gate UX: the old paused lede
                    // restated both).
                    if (wizardStep === 0 && welcomeHasOrg) return null
                    if (wizardStep === 3 && effectivelyPaused) return null
                    // #2912 (review cycle 2): WIZARD_STEPS[2].sub is the harness
                    // pick's copy, but step 2 has THREE bodies — only the
                    // owner/self branch is a harness pick.
                    if (wizardStep === 2) {
                      // #2912 (review cycle 4 P1): the role/cap check runs FIRST.
                      // A member of a build-fork org gets the SDK-lede body
                      // ("Only owners and admins can create API keys"), so
                      // promising them a key here reproduced the exact defect
                      // class #2912 was filed for.
                      // PR-gate UX (P1): this branch's own body says "Paste an
                      // API key below" — so the lede says what the step ASKS.
                      // (The rejected "Connect Tortoise to your Organization."
                      // is the string that said nothing about the step.)
                      if (!isOwnerAdmin || capNotice) return <p className="welcome-lede">Paste an API key to connect your agent.</p>
                      if (isBuildFork) return <p className="welcome-lede">Create an API key and call the Tortoise SDK from your app.</p>
                    }
                    const sub = WIZARD_STEPS[wizardStep].sub
                    return <p className="welcome-lede">{sub}</p>
                  })()}
                </div>
                <span className="sr-only" role="status" aria-live="polite">
                  {welcomeProvisioning ? 'Creating your organization' : (welcomeHasOrg && shownOrgName ? `${shownOrgName} is set up` : '')}
                </span>
                {/* #1997 (W1): the 4 HUMAN steps (epic plan P1) — org-create/join
                    → fork card → connect-consent → done (orientation removed per
                    epic #2534).
                    All other steps (install/seed/decide) are agent-side or
                    archived. Copy from wizardFlow.js (DE2E-2: 'Organization',
                    never 'team'/'workspace' in user-facing labels). */}
                <div className="wizard" ref={wizardCardRef} tabIndex={-1}>
                  <span className="sr-only" role="status" aria-live="polite">{wizardStepAnnounce}</span>
                  <div className="wizard-progress">
                    {WIZARD_STEPS.map((s, i) => (
                      <span key={s.id} className={'wizard-step' + (i === wizardStep ? ' active' : (i < wizardStep ? ' done' : ''))} />
                    ))}
                  </div>

                  {wizardStep === 0 && (
                    <div className="org-create">
                      {welcomeHasOrg ? (
                        // #2323 (Option B): the account already has an org —
                        // this step is a READ-ONLY summary, never a second
                        // mint (re-entry / invite-held paths). The wizard
                        // advances to the fork card.
                        <>
                          <p className="dim" style={{ marginBottom: '0.9rem' }}>
                            You're set up in <strong>{shownOrgName || 'your organization'}</strong>. Next, choose how you'll use Tortoise.
                          </p>
                          <div className="wizard-nav-actions">
                              <button type="button" className="btn-primary" onClick={() => setWizardStep(1)}>Continue →</button>
                            </div>
                        </>
                      ) : (
                        // #2323 (Option B): the first-run provisioning door —
                        // the typed name creates the ONE org (tenant-provision,
                        // deterministic team_id). #2324: value is state-only —
                        // no prefill fallback chain that resurrects text on
                        // delete, and no phantom org name to fight.
                        <>
                          <label className="small" style={{ display: 'flex', flexDirection: 'column', gap: '0.3rem', marginBottom: '0.6rem' }}>
                            <span className="dim small">Organization name</span>
                            <input
                              value={wizardOrgName}
                              onChange={(e) => setWizardOrgName(e.target.value)}
                              placeholder="e.g. acme"
                              aria-label="Organization name"
                              autoFocus
                              maxLength={64}
                              style={{ padding: '0.5rem 0.7rem', background: 'var(--surface,#0d1a2d)', border: '1px solid var(--border,#1e293b)', borderRadius: 8, fontSize: 14 }}
                            />
                          </label>
                          {wizardOrgError && (
                            <p className="error" role="alert" style={{ marginBottom: '0.9rem' }}>{wizardOrgError}</p>
                          )}
                          {(!pendingInvites || pendingInvites.length === 0) && (
                            <p className="dim small" style={{ margin: '0 0 0.9rem', lineHeight: 1.5, fontStyle: 'italic' }}>
                              Looking to join an existing organization? Ask your admin to invite you to your email, then reload this page.
                            </p>
                          )}
                          <div className="wizard-nav-actions">
                              <button type="button" className="btn-primary" onClick={handleWizardCreateOrg} disabled={wizardOrgBusy}>
                                {wizardOrgBusy ? 'Creating…' : 'Create Organization'}
                              </button>
                            </div>
                        </>
                      )}
                      {pendingInvites && pendingInvites.length > 0 && (
                        <div className="pending-invites" style={{ marginTop: '1.25rem', paddingTop: '1rem', borderTop: '1px solid var(--border,#1e293b)' }}>
                          <p className="dim small" style={{ margin: '0 0 0.5rem' }}>Or accept an invitation to join an existing organization:</p>
                          {pendingInvites[0] && pendingInvites[0]._loadError ? (
                            // #2361 review-r2 (P3): a failed load must NOT render
                            // as an actionable 'Invitation' row — mirror the
                            // account-menu error branch.
                            <p className="dim small" role="alert">{pendingInvites[0]._loadError}</p>
                          ) : pendingInvites.map((inv) => (
                            <div key={inv.invitation_id} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.4rem' }}>
                              <span className="small">{inv.team_name || 'Invitation'}</span>
                              {inv.error && <span className="account-invite-error small" role="alert">{inv.error}</span>}
                              <div style={{ display: 'flex', gap: '0.4rem' }}>
                                <button type="button" className="ghost small" onClick={() => acceptPendingInvite(inv)} disabled={pendingInvitesBusy !== ''}>
                                  {pendingInvitesBusy === inv.invitation_id ? 'Joining…' : 'Accept'}
                                </button>
                                <button type="button" className="ghost small" onClick={() => declinePendingInvite(inv)} disabled={pendingInvitesBusy !== ''}>Decline</button>
                              </div>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  )}

                  {wizardStep === 1 && (
                    <div className="fork-card">
                      {wizardForkError && (
                        <p className="error" role="alert" style={{ marginBottom: '0.9rem' }}>{wizardForkError}</p>
                      )}
                      {onboarding && onboarding.fork && (
                        <p className="dim" style={{ marginBottom: '0.9rem' }}>
                          This Organization is set to <strong>{onboarding.fork === 'build' ? 'build an application on top' : 'use Tortoise for your own agents'}</strong>.
                        </p>
                      )}
                      <div className="fork-options" style={{ display: 'flex', flexDirection: 'column', gap: '0.6rem', marginBottom: '1rem' }}>
                        {WIZARD_FORK_OPTIONS.map((opt) => (
                          <button
                            key={opt.id}
                            type="button"
                            className={(wizardForkChosen === opt.id || (onboarding && onboarding.fork === opt.id)) ? 'fork-option active' : 'fork-option'}
                            aria-pressed={wizardForkChosen === opt.id || (onboarding && onboarding.fork === opt.id)}
                            disabled={wizardForkBusy || !!wizardForkChosen || (onboarding && !!onboarding.fork)}
                            onClick={() => handleWizardFork(opt.id)}
                            style={{ display: 'flex', flexDirection: 'column', gap: '0.25rem', textAlign: 'left', padding: '0.7rem 0.9rem', border: '1px solid var(--border,#1e293b)', borderRadius: 8, background: 'var(--surface,#0d1a2d)', cursor: wizardForkBusy || !!wizardForkChosen || (onboarding && !!onboarding.fork) ? 'default' : 'pointer' }}
                          >
                            <strong>{opt.label}</strong>
                            <span className="dim small">{opt.description}</span>
                          </button>
                        ))}
                      </div>
                      {(wizardForkChosen === 'build' || (onboarding && onboarding.fork === 'build')) && (
                        <div className="catalog-placeholder" style={{ marginBottom: '1rem', padding: '0.7rem 0.9rem', border: '1px solid var(--border,#1e293b)', borderRadius: 8 }}>
                          <p className="dim small" style={{ margin: '0 0 0.5rem' }}><strong>Build catalog</strong> — the indexers and extractors you can build with:</p>
                          {resolveBuildCatalog(wizardCatalog).map((mod) => (
                            <div key={mod.name} style={{ marginBottom: '0.35rem' }}>
                              <strong className="small">{mod.name}</strong>{' '}
                              <span className="dim small">({mod.kind}) — {mod.description}</span>
                            </div>
                          ))}
                        </div>
                      )}
                      <div className="wizard-nav">
                        <button type="button" className="ghost" onClick={() => setWizardStep(0)}>← Back</button>
                        <div className="wizard-nav-actions">
                          {wizardForkChosen || (onboarding && onboarding.fork) ? (
                            <button type="button" className="btn-primary" onClick={() => setWizardStep(2)}>Continue →</button>
                          ) : (
                            <p className="dim small" style={{ margin: 0 }}>Pick how you'll use Tortoise — you choose once per Organization.</p>
                          )}
                        </div>
                      </div>
                    </div>
                  )}

{wizardStep === 2 && (isBuildFork ? (
                    <div className="connect-build">
                      {/* #2912 (PR-gate UX): the build fork used to keep the old
                          ad-hoc layout (0.4rem captions, inline margins) while
                          its sibling owner path got h1 → numbered h2 blocks, so
                          the same step looked like two different products. Same
                          block structure here: 1 = key, 2 = the SDK call. */}
                      <WizardBlock step={1} title={harnessKey ? 'Your API key' : 'Get your API key'}>
                        {harnessKey ? (
                          <>
                            <p className="dim small" style={{ marginBottom: '0.4rem' }}>
                              Your API key is shown once — copy it now.
                            </p>
                            <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
                              <code style={{ flex: 1, padding: '0.6rem 0.8rem', background: 'var(--surface,#0d1a2d)', border: '1px solid var(--border,#1e293b)', borderRadius: 8, fontSize: 13, wordBreak: 'break-all' }}>
                                {harnessKey}
                              </code>
                              <button type="button" className="btn-primary" onClick={() => navigator.clipboard?.writeText(harnessKey)}>
                                Copy
                              </button>
                            </div>
                          </>
                        ) : (
                          <>
                            {isOwnerAdmin ? (
                              <>
                                <p className="dim small" style={{ margin: '0 0 0.6rem' }}>
                                  Create an API key to call the SDK from your application.
                                </p>
                                <button type="button" className="btn-primary" onClick={wizardMintDurableKey} disabled={wizardDurableBusy}>
                                  {wizardDurableBusy ? 'Creating…' : `Create an API key for ${shownOrgName || 'your organization'}`}
                                </button>
                              </>
                            ) : (
                              <p className="dim" style={{ margin: 0 }}>
                                Only owners and admins can create API keys. Ask an owner or admin to create one.
                              </p>
                            )}
                            <div style={{ marginTop: '0.6rem' }}>
                              <button type="button" className="ghost small" onClick={() => { window.history.replaceState({}, '', '#/keys'); setWelcomeMode(false); setTab('keys'); finishWelcomeLoads('keys') }}>
                                Manage API keys →
                              </button>
                            </div>
                          </>
                        )}
                      </WizardBlock>

                      {harnessKey && (
                        <WizardBlock step={2} title="Call the SDK">
                          <p className="dim" style={{ marginBottom: '0.75rem', lineHeight: 1.6 }}>
                            Run this to verify your API key and file your first point — it creates your graph and connects your project.
                          </p>
                          <pre className="snippet" style={{ margin: 0 }}>
{`curl https://api.premiselabs.co/v1/points \\
  -H "Authorization: Bearer ${harnessKey}" \\
  -H "Content-Type: application/json" \\
  -d '{\"content\":\"my first application is set up\"}'`}
                          </pre>
                          <div style={{ display: 'flex', gap: '0.75rem', alignItems: 'center', marginTop: '0.9rem' }}>
                            <button type="button" className="btn-primary" onClick={wizardHarnessContinue} disabled={wizardConnectBusy}>
                              {wizardConnectBusy ? 'Saving…' : "I've set it up — Continue →"}
                            </button>
                            <a className="ghost" href="https://tortoise.premiselabs.co/docs" target="_blank" rel="noreferrer">
                              SDK documentation →
                            </a>
                          </div>
                        </WizardBlock>
                      )}

                      {wizardDurableError && (
                        <p className="error" role="alert" style={{ margin: '0.6rem 0 0', fontSize: 13 }}>{wizardDurableError}</p>
                      )}
                      <div className="wizard-nav">
                        <button type="button" className="ghost" onClick={() => setWizardStep(1)}>← Back</button>
                        <div className="wizard-nav-actions">
                          <button type="button" className="ghost" onClick={() => { setWizardPaused(true); setWizardStep(3) }}>Skip for now</button>
                        </div>
                      </div>
                    </div>
                  ) : (isOwnerAdmin && !capNotice ? (
                    (() => {
                      // #2912: two-level chooser — the FAMILY (Claude/Codex/
                      // Cursor/Pi) then the SURFACE (Claude Code/Desktop/Web;
                      // Codex CLI/Desktop). `wizardHarness` stays the leaf id
                      // every payload lookup already keys on.
                      const activeFamily = harnessFamilyOf(wizardHarness) || HARNESS_FAMILIES[0]
                      const surfaces = activeFamily.surfaces
                      const displayName = harnessDisplayName(wizardConnectHarness)
                      const agentDriven2Step = ['pi', 'cursor']
                      // 'codexDesktop' is the Codex GUI leaf — same payload shape
                      // as the CLI (a config block instead of an agent prompt).
                      const agentDriven1Step = ['claude', 'codex', 'codexDesktop']
                      // #2710: pills are pure display-mode toggles — they must
                      // never queue the shared create modal (it renders only in
                      // the dashboard tree, so it would pop after exit).
                      const keyModeToggleable = ['pi', 'cursor', 'claude', 'codex'].includes(wizardHarness)
                      /* #2756 (code-review P1): the Codex DESKTOP surface embeds
                         the key in the config block by construction, so there is
                         no separate key row there. Manual harnesses (no mode
                         choice) always show the key — it is what the user pastes
                         into the connector. */
                      const keyRowVisible = harnessKey && wizardConnectHarness !== 'codexDesktop'
                        && (wizardKeyMode === 'separate' || !keyModeToggleable)
                      const keyDisplayRow = keyRowVisible ? (
                        <div className="key-row">
                          <p className="dim small">Your API key (shown once):</p>
                          <code style={wizardKeyCodeStyle}>{harnessKey}</code>
                          <button type="button" className="btn-primary small" onClick={() => navigator.clipboard?.writeText(harnessKey)}>Copy</button>
                        </div>
                      ) : null

                      // ── step 2 body: the harness's own connect procedure ──
                      let procedureTitle = `Set up ${displayName}`
                      let procedure = null
                      if (agentDriven2Step.includes(wizardHarness)) {
                        procedure = (
                          <>
                            <p className="wizard-caption">Give this prompt to your agent to connect Tortoise:</p>
                            <WizardPromptCard text={wizardPromptText(wizardHarness, 1, harnessKey, wizardKeyMode)} label="Copy step 1 prompt" />
                            <p className="wizard-caption">Then restart {HARNESS_NAMES[wizardHarness]} and give it this prompt to verify and file your first point:</p>
                            <WizardPromptCard text={wizardPromptText(wizardHarness, 2, harnessKey, wizardKeyMode)} label="Copy step 2 prompt" />
                          </>
                        )
                      } else if (agentDriven1Step.includes(wizardHarness)) {
                        // #2827: the Codex Desktop surface configures a TOML file
                        // the HUMAN pastes, so the "give your agent this prompt"
                        // caption would lie there.
                        procedure = wizardConnectHarness === 'codexDesktop' ? (
                          <>
                            <p className="wizard-caption">{HARNESS_INTRO.codexDesktop}</p>
                            <WizardPromptCard text={UNIVERSAL_COMMAND.codexDesktop(harnessKey)} label={HARNESS_COPY_LABEL.codexDesktop} />
                          </>
                        ) : (
                          <>
                            <p className="wizard-caption">Give this prompt to your agent to connect Tortoise:</p>
                            <WizardPromptCard text={wizardPromptText(wizardConnectHarness, 1, harnessKey, wizardKeyMode)} label="Copy prompt" />
                          </>
                        )
                      } else if (wizardHarness === 'claude-desktop' || wizardHarness === 'claude-web') {
                        // #2710: the manual connector flows used to render a
                        // `YOUR_API_KEY` placeholder with a Copy button that
                        // wrote an empty string. The key now lives in step 1
                        // (always, for these harnesses); step 2 pastes the
                        // header value into the connector and the prompt into
                        // the chat.
                        const web = wizardHarness === 'claude-web'
                        procedureTitle = `Add the ${displayName} connector`
                        procedure = (
                          <>
                            <p className="wizard-caption">
                              Open {web ? 'claude.ai' : 'Claude Desktop'} → Settings → Connectors → <em>Add custom connector</em>, then enter:
                            </p>
                            <ul className="wizard-fields">
                              <li>Name: <strong>Tortoise</strong></li>
                              <li>Server URL: <code>https://api.premiselabs.co/mcp/</code></li>
                              <li>Request headers{web ? ' (advanced)' : ''}: <code style={wizardHeaderCodeStyle}>{'Authorization: Bearer ' + harnessKey}</code></li>
                            </ul>
                            <div>
                              <button type="button" className="ghost small" onClick={() => navigator.clipboard?.writeText(`Authorization: Bearer ${harnessKey}`)}>Copy header value</button>
                            </div>
                            <p className="wizard-note">
                              Note: <strong>Request headers</strong> is still rolling out in Anthropic&apos;s beta and may not appear for every account. If Request headers isn&apos;t available on your account yet, use the Claude Code surface instead — that path works on every account.{web ? ' Claude connects from Anthropic\u2019s cloud, so your key is stored by Anthropic — keep the chat and the key private.' : ''}
                            </p>
                            <p className="wizard-caption">Start a new chat and paste this prompt — it {web ? 'teaches Claude the Tortoise workflows' : 'tells Claude how to use Tortoise in this chat'}:</p>
                            <WizardPromptCard text={wizardWorkflowsText(harnessKey, wizardKeyMode)} label="Copy prompt" />
                          </>
                        )
                      }

                      return (
                        <div className="harness-connect">
                          <div className="harness-chooser">
                            <p className="wizard-field-label">Your harness</p>
                            <div className="harness-families" role="group" aria-label="Harness">
                              {HARNESS_FAMILIES.map((f) => (
                                <button key={f.id} type="button"
                                  className={'harness-family' + (activeFamily.id === f.id ? ' active' : '')}
                                  aria-pressed={activeFamily.id === f.id}
                                  onClick={() => { setWizardHarness((cur) => preferredSurface(f, cur)); setWizardCopied(''); setWizardConnectError(''); setWizardDurableError('') }}>
                                  {f.name}
                                </button>
                              ))}
                            </div>
                            {surfaces.length > 0 && (
                              <div className="harness-surfaces" role="group" aria-label={`${activeFamily.name} surface`}>
                                {surfaces.map((s) => (
                                  <button key={s.id} type="button"
                                    className={'harness-surface' + (wizardHarness === s.id ? ' active' : '')}
                                    aria-pressed={wizardHarness === s.id}
                                    onClick={() => { setWizardHarness(s.id); setWizardCopied(''); setWizardConnectError(''); setWizardDurableError('') }}>
                                    <span className="harness-surface-name">{s.name}</span>
                                    {s.hint && <span className="harness-surface-hint">{s.hint}</span>}
                                  </button>
                                ))}
                              </div>
                            )}
                          </div>

                          {/* #2912: KEY FIRST. The step used to caption "Give
                              this prompt…" and then, with no key, replace the
                              prompt with the mint CTA — telling the user to
                              copy something that did not exist and putting the
                              key instructions after the promise. The block
                              order now matches the real dependency:
                              key → procedure.
                              PR-gate UX: the Codex Desktop surface is a SINGLE
                              step (its key lives inside the config block), and
                              an empty "1 Get your API key" would promise an
                              action that does not exist there; with no key there
                              is no procedure block to show either. */}
                          {wizardConnectHarness === 'codexDesktop' ? (
                            <WizardBlock step={1} title={harnessKey ? procedureTitle : 'Get your API key'}>
                              {harnessKey ? procedure : wizardNoKeyAffordance}
                            </WizardBlock>
                          ) : (
                            <>
                              <WizardBlock step={1} title="Get your API key">
                                {harnessKey ? (
                                  <>
                                    <p className="wizard-caption">
                                      This key connects {displayName} to {shownOrgName || 'your Organization'}. It&apos;s shown once — keep it private.
                                    </p>
                                    {keyModeToggleable && (
                                      <div className="key-pills">
                                        <button type="button" className={wizardKeyMode === 'included' ? 'active' : ''}
                                          aria-pressed={wizardKeyMode === 'included'}
                                          onClick={() => setWizardKeyMode('included')}>
                                          <strong>Key included in prompt</strong>
                                          <span>Simple — easiest</span>
                                        </button>
                                        <button type="button" className={wizardKeyMode === 'separate' ? 'active' : ''}
                                          aria-pressed={wizardKeyMode === 'separate'}
                                          onClick={() => setWizardKeyMode('separate')}>
                                          <strong>Key separate from prompt</strong>
                                          <span>Manual — more secure</span>
                                        </button>
                                      </div>
                                    )}
                                    {keyDisplayRow}
                                  </>
                                ) : (
                                  wizardNoKeyAffordance
                                )}
                              </WizardBlock>

                              {harnessKey && (
                                <WizardBlock step={2} title={procedureTitle}>
                                  {procedure}
                                </WizardBlock>
                              )}
                            </>
                          )}

                          <div className="wizard-nav">
                            <button type="button" className="ghost" onClick={() => setWizardStep(1)}>← Back</button>
                            <div className="wizard-nav-actions">
                              {wizardConnectError && <p className="error" role="alert" style={{ margin: '0 0.5rem 0 0', fontSize: 13 }}>{wizardConnectError}</p>}
                              <button type="button" className="btn-primary" onClick={wizardHarnessContinue} disabled={wizardConnectBusy}>
                                {wizardConnectBusy ? 'Saving…' : (['pi','cursor','claude-desktop','claude-web'].includes(wizardHarness) ? 'Done — Continue to dashboard' : "I've set it up — Continue →")}
                              </button>
                              <button type="button" className="ghost" onClick={() => { setWizardPaused(true); setWizardStep(3) }}>Skip for now</button>
                            </div>
                          </div>
                        </div>
                      )
                    })()
                  ) : (
                    <div className="harness">
                      <p className="dim" style={{ margin: '0.9rem 0 0', lineHeight: 1.6 }}>
                        {!isOwnerAdmin
                          ? 'Only owners and admins can create API keys in this dashboard. Paste an API key below from your agent or an owner/admin.'
                          : capNotice}
                      </p>
                      {wizardPasteRow}
                      <div className="wizard-nav" style={{ marginTop: '0.75rem' }}>
                        <button type="button" className="ghost" onClick={() => setWizardStep(1)}>← Back</button>
                        <div className="wizard-nav-actions">
                          {wizardDurableKey && (
                            <button type="button" className="btn-primary" onClick={wizardHarnessContinue} disabled={wizardConnectBusy}>
                              {wizardConnectBusy ? 'Saving…' : 'Continue to dashboard'}
                            </button>
                          )}
                          <button type="button" className="ghost" onClick={() => { setWizardPaused(true); setWizardStep(3) }}>Skip for now</button>
                        </div>
                      </div>
                    </div>
                  )))}

                  {wizardStep === 3 && (
                    <div className="done">
                      {effectivelyPaused ? (
                        <p className="dim">You're set up, but your agent isn't connected yet — nothing was installed on the connect step. Open Settings → Setup guide to follow what happens next — the setup command there creates a fresh key when you do.</p>
                      ) : (
                        <p className="dim">Your agent is connected — it files your decisions and findings to this Organization's graph from here on. Open Settings → Setup guide to follow what happens next.</p>
                      )}
                      <div className="wizard-nav">
                        <button type="button" className="ghost" onClick={() => setWizardStep(2)}>← Back</button>
                      </div>
                      <div className="wizard-actions">
                        <button type="button" className="btn-primary" onClick={wizardComplete}>Open my dashboard →</button>
                        <a className="ghost" href="https://tortoise.premiselabs.co/docs" target="_blank" rel="noreferrer">Read the docs</a>
                      </div>
                    </div>
                  )}
                </div>
                {/* ⛔ ARCHIVED — #1997 (W1): the legacy #1643 wizard render
                    (harness chooser → memory sources → skills → seed → done).
                    NEVER rendered while LEGACY_WIZARD_ARCHIVED is false — the
                    A0 gate's rollback path restores it by re-enabling this
                    gate + the welcomeOriented pre-card (epic §8). DE2E-1: the
                    archived-not-deleted assertion greps this marker + the
                    legacy wizardSteps labels. */}
                {LEGACY_WIZARD_ARCHIVED && welcomeOriented && (
                <div className="wizard">
                  <div className="wizard-progress">
                    {wizardSteps.map((s, i) => (
                      <span key={s} className={'wizard-step' + (i === wizardStep ? ' active' : (i < wizardStep ? ' done' : ''))} />
                    ))}
                  </div>
                  <p className="wizard-title">{wizardSteps[wizardStep]}</p>
                  <p className="wizard-sub" style={{ marginBottom: '1rem' }}>
                    {wizardStep === 0 ? 'Pick your tool — the setup command connects the MCP server and installs the skills in one copy.'
                      : wizardStep === 1 ? 'Choose what Tortoise should remember — all off by default; you can change this any time.'
                      : wizardStep === 2 ? 'These are the three skills your setup command installs — what they do, and when your agent uses them.'
                      : wizardStep === 3 ? 'Add yourself and your project as the first objects on your graph.'
                      : 'Welcome to Tortoise — your graph is live.'}
                  </p>

                  {wizardStep === 0 && (
                    <div className="harness">
                      <div className="harness-tabs">
                        {HARNESS_ORDER.map((h) => (
                          <button key={h} type="button"
                            className={'harness-tab' + (wizardHarness === h ? ' active' : '')}
                            onClick={() => { setWizardHarness(h); setWizardCopied('') }}>
                            {HARNESS_NAMES[h]}
                          </button>
                        ))}
                      </div>
                      {(!harnessKey && !HARNESS_OAUTH.includes(wizardHarness)) ? (
                        // #1831 P2-1: no key after a recoverable mint
                        // failure (#1830) — never emit `Bearer ` with an
                        // empty key. Fall back to a create-a-key message.
                        // #1701: OAuth harnesses (chatgpt) are key-satisfied
                        // — they skip this gate even with no tt_ key.
                        <>
                          <p className="dim" style={{ margin: '0.9rem 0 0', lineHeight: 1.6 }}>
                            The setup command embeds your API key — create one
                            first on the API Keys tab, then come back here to
                            finish setup.
                          </p>
                          <div className="wizard-nav">
                            <button type="button" className="ghost" onClick={() => setWelcomeOriented(false)}>← Back</button>
                            <div className="wizard-nav-actions">
                              <button type="button" className="ghost" onClick={() => { window.history.replaceState({}, '', '#/keys'); setWelcomeMode(false); setTab('keys'); finishWelcomeLoads('keys') }}>Go to API Keys →</button>
                            </div>
                          </div>
                        </>
                      ) : (
                        <>
                      {HARNESS_STEPS(wizardHarness, harnessKey) && (
                        <ol className="harness-steps" style={{ margin: '0.9rem 0 0.25rem 1.1rem', padding: 0, lineHeight: 1.7 }}>
                          {HARNESS_STEPS(wizardHarness, harnessKey).map((s, i) => (
                            <li key={i} style={{ marginBottom: '0.35rem', fontSize: 14, color: 'var(--text,#e2e8f0)' }}>
                              {typeof s === 'string' ? s : (
                                <>
                                  <span>{s.label}</span>{' '}
                                  <code style={{ padding: '2px 6px', background: 'var(--surface,#0d1a2d)', border: '1px solid var(--border,#1e293b)', borderRadius: 5, fontSize: 13 }}>{s.code}</code>{' '}
                                  {s.copy && (
                                    <button type="button" className="ghost small" onClick={() => wizardCopyStep(s.copy)}>
                                      {copiedStep === s.copy ? 'Copied ✓' : 'Copy'}
                                    </button>
                                  )}
                                </>
                              )}
                            </li>
                          ))}
                        </ol>
                      )}
                      {HARNESS_INTRO[wizardHarness] && (
                        <p className="dim small" style={{ margin: '0.9rem 0 0', lineHeight: 1.6 }}>
                          {HARNESS_INTRO[wizardHarness]}
                        </p>
                      )}
                      <pre className="snippet" style={{ marginTop: '0.75rem' }}>
                        {HARNESS_INSTALL[wizardHarness](harnessKey)}
                        {HARNESS_SKILLS(wizardHarness)}
                        {welcomeKey && !HARNESS_SKILLLESS.includes(wizardHarness) && !HARNESS_SKILLS_IN_PROMPT.includes(wizardHarness) && !HARNESS_SKILLS_IN_STEPS.includes(wizardHarness) ? ('\n\n' + HARNESS_PERSIST(harnessKey)) : ''}
                      </pre>
                      <div className="wizard-nav">
                        <button type="button" className="ghost" onClick={() => setWelcomeOriented(false)}>← Back</button>
                        <div className="wizard-nav-actions">
                          <button type="button" className={wizardCopied === 'harness' ? 'ghost' : 'btn-primary'}
                            onClick={() => wizardCopy(HARNESS_INSTALL[wizardHarness](harnessKey) + HARNESS_SKILLS(wizardHarness) + (welcomeKey && !HARNESS_SKILLLESS.includes(wizardHarness) && !HARNESS_SKILLS_IN_PROMPT.includes(wizardHarness) && !HARNESS_SKILLS_IN_STEPS.includes(wizardHarness) ? ('\n\n' + HARNESS_PERSIST(harnessKey)) : ''), 'harness')}>
                            {wizardCopied === 'harness' ? 'Copied ✓' : (HARNESS_COPY_LABEL[wizardHarness] || 'Copy setup')}
                          </button>
                          {wizardCopied === 'harness' && (
                            <button type="button" className="btn-primary" onClick={() => setWizardStep(1)}>{HARNESS_CONTINUE_LABEL[wizardHarness] || "I've set it up — Continue →"}</button>
                          )}
                          <button type="button" className="ghost" onClick={() => setWizardStep(1)}>Skip for now</button>
                        </div>
                      </div>
                        </>
                      )}
                    </div>
                  )}

                  {wizardStep === 2 && (
                    <div className="skills">
                      <p className="dim" style={{ marginBottom: '0.9rem' }}>
                        The setup command in the first step installs these three — here's
                        what they do and when your agent uses them.
                      </p>
                      <div className="skill-row">
                        <strong>how-to-use-tortoise</strong>
                        <span className="dim small">the passive skill — your agent loads it automatically for every graph read/write: points, operators, mitigations, NAND edges, supersede, annotate. Nothing to invoke.</span>
                      </div>
                      <div className="skill-row">
                        <strong>tortoise-decide</strong>
                        <span className="dim small">the invoke skill — run it when you make a decision: it weighs options against the graph\'s state and records the reasoning as Events.</span>
                      </div>
                      <div className="skill-row">
                        <strong>tortoise-file-finding</strong>
                        <span className="dim small">the invoke skill — run it when you add a research finding: it creates a Point, checks for related claims, and surfaces connections.</span>
                      </div>
                      <div className="wizard-nav">
                        <button type="button" className="ghost" onClick={() => setWizardStep(wizardStep - 1)}>← Back</button>
                        <div className="wizard-nav-actions">
                          <button type="button" className="btn-primary" onClick={() => setWizardStep(3)}>Next</button>
                        </div>
                      </div>
                    </div>
                  )}

                  {wizardStep === 1 && (
                    <div className="memory-sources">
                      <MemorySources
                        state={onboarding}
                        loading={onboardingLoading}
                        wizardHarness={wizardHarness}
                        github={wizardGithub}
                        issuesWantOn={issuesWantOn}
                        docsWantOn={docsWantOn}
                        indexJob={indexJob}
                        docsJob={docsJob}
                        memoryBusy={memoryBusy}
                        memoryErrors={memoryErrors}
                        onToggleIssues={toggleIssues}
                        onToggleDocs={toggleDocs}
                        onToggleSessions={toggleSessionRecording}
                        onConnectGithub={wizardConnectGithub}
                        onIndexDocs={indexDocs}
                        onReindexGithub={reindexGithub}
                        reposList={reposList}
                        reposLoaded={reposLoaded}
                        reposLoadFailed={reposLoadFailed}
                        branchLists={branchLists}
                        docsScope={docsScope}
                        issuesScope={issuesScope}
                        onDocsScopeChange={handleDocsScopeChange}
                        onIssuesScopeChange={handleIssuesScopeChange}
                        onLoadBranches={loadBranches}
                      />
                      <div className="wizard-nav">
                        <button type="button" className="ghost" onClick={() => setWizardStep(wizardStep - 1)}>← Back</button>
                        <div className="wizard-nav-actions">
                          {!wizardGithub.connected && wizardGithub.busy && (
                            <button type="button" className="ghost" onClick={() => { stopGithubPoll(); setWizardGithub((g) => ({ ...g, busy: false })) }}>Cancel</button>
                          )}
                          <button type="button" className="ghost" onClick={() => setWizardStep(2)}>Skip →</button>
                        </div>
                      </div>
                    </div>
                  )}

                  {wizardStep === 3 && (
                    <div className="seed">
                      {wizardSeedDone ? (
                        <p className="dim">Your graph is live — it starts with you and your project, and the statement connecting them.</p>
                      ) : (
                        <>
                          <p className="dim" style={{ marginBottom: '0.9rem' }}>
                            Your graph starts with two objects: you (the subject) and your
                            project. We've prefilled them — adjust or keep as they are.
                          </p>
                          <div className="seed-fields" style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem', marginBottom: '1rem' }}>
                            <label className="small" style={{ display: 'flex', flexDirection: 'column', gap: '0.3rem' }}>
                              <span className="dim small">Your name</span>
                              <input
                                value={wizardSubject}
                                onChange={(e) => setWizardSubject(e.target.value)}
                                placeholder="e.g. daniel"
                                aria-label="Your name (subject)"
                                style={{ padding: '0.5rem 0.7rem', background: 'var(--surface,#0d1a2d)', border: '1px solid var(--border,#1e293b)', borderRadius: 8, fontSize: 14 }}
                              />
                            </label>
                            <label className="small" style={{ display: 'flex', flexDirection: 'column', gap: '0.3rem' }}>
                              <span className="dim small">Project name</span>
                              <input
                                value={wizardProject}
                                onChange={(e) => setWizardProject(e.target.value)}
                                placeholder="e.g. tortoise"
                                aria-label="Project name"
                                style={{ padding: '0.5rem 0.7rem', background: 'var(--surface,#0d1a2d)', border: '1px solid var(--border,#1e293b)', borderRadius: 8, fontSize: 14 }}
                              />
                            </label>
                          </div>
                          <p className="dim small">Seeding adds: your subject, the project object (in progress), and a statement connecting them.</p>
                        </>
                      )}
                      {wizardSeedError && (
                        <p className="error" role="alert" style={{ marginBottom: '0.9rem' }}>
                          {wizardSeedError}
                        </p>
                      )}
                      <div className="wizard-nav">
                        <button type="button" className="ghost" onClick={() => setWizardStep(wizardStep - 1)}>← Back</button>
                        <div className="wizard-nav-actions">
                          <button type="button" className="btn-primary" onClick={wizardSeedGraph} disabled={wizardSeeding || wizardSeedDone}>
                            {wizardSeeding ? 'Seeding…' : (wizardSeedDone ? 'Seeded ✓' : 'Seed my graph')}
                          </button>
                          <button type="button" className="ghost" onClick={() => setWizardStep(4)}>
                            {wizardSeedDone ? 'Finish' : 'Skip →'}
                          </button>
                        </div>
                      </div>
                    </div>
                  )}

                  {wizardStep === 4 && (
                    <div className="done">
                      <p className="dim">Welcome to Tortoise — your graph is live and your decisions are being recorded. Once you install the tools, your agent knows how to use them.</p>
                      <div className="wizard-nav">
                        <button type="button" className="ghost" onClick={() => setWizardStep(3)}>← Back</button>
                      </div>
                      <div className="wizard-actions">
                        <button type="button" className="btn-primary" onClick={wizardComplete}>Open my dashboard →</button>
                        <a className="ghost" href="https://tortoise.premiselabs.co/docs" target="_blank" rel="noreferrer">Read the docs</a>
                      </div>
                      {welcomeKey && team && (
                        <div className="welcome-plans" style={{ marginTop: '1.5rem', paddingTop: '1.25rem', borderTop: '1px solid var(--border,#1e293b)' }}>
                          <h2 style={{ fontFamily: 'var(--serif, Georgia, serif)', fontWeight: 400, fontSize: 20, marginBottom: '0.4rem' }}>Choose your plan</h2>
                          <p className="dim" style={{ marginBottom: '0.9rem' }}>
                            You're on the free plan — no card needed. Upgrade any time as you grow.
                          </p>
                          <div className="plans-grid">
                            {planOptions().map((p) => {
                              const hasPrice = Boolean(team.checkout_price_ids?.[p.tier])
                              return (
                                <div key={p.tier} className={`plan-card${p.tier === 'free' ? ' current' : ''}`}>
                                  <div className="plan-card-head">
                                    <strong>{p.label}</strong>
                                    {p.tier === 'free' && <span className="tier-badge" style={{ fontSize: 10, padding: '1px 8px' }}>Default</span>}
                                  </div>
                                  <div className="plan-price">
                                    {p.price === 0 ? '$0' : `$${p.price}`}<span className="dim small">/mo</span>
                                  </div>
                                  <ul className="plan-limits">
                                    {p.limits.map((l) => <li key={l}>{l}</li>)}
                                  </ul>
                                  {p.tier === 'free' ? (
                                    // #1856: same as the header "Open my dashboard →" button — this
                                    // first-timer exit (completeLogin never runs) must still fire
                                    // finishWelcomeLoads() or currentTeamId/teams/apiKey stay unset
                                    // (account blob "No team", Members "Loading…" forever). The plans
                                    // block only renders with welcomeKey set, so it reads the revealed
                                    // key. Fire-and-forget: finishWelcomeLoads never rejects.
                                    <button
                                      className="btn-primary"
                                      onClick={() => { window.clearTimeout(checkoutResetTimerRef.current); setCheckoutPending(false); window.history.replaceState({}, '', '#/keys'); setWelcomeMode(false); setTab('keys'); finishWelcomeLoads('keys') }}
                                    >
                                      Start free
                                    </button>
                                  ) : hasPrice ? (
                                    <button className="ghost" onClick={() => upgradeToPrice(team.checkout_price_ids[p.tier])} disabled={checkoutPending}>
                                      {checkoutPending ? 'Opening checkout…' : 'Upgrade'}
                                    </button>
                                  ) : (
                                    <a className="ghost" href="https://tortoise.premiselabs.co/product.html#pricing" target="_blank" rel="noreferrer">See pricing</a>
                                  )}
                                </div>
                              )
                            })}
                          </div>
                        </div>
                      )}
                    </div>
                  )}
                </div>
                )}
              </>
            )}
          </div>
        </main>
      </div>
    )
  }

  return (
    <div className="app">
      {banner && (
        <div className="banner" style={{ background: 'var(--surface,#0d1a2d)', borderBottom: '1px solid var(--border,#1e293b)', color: 'var(--green,#4ade80)', padding: '0.6rem 1.5rem', fontSize: 13, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <span>{banner}</span>
          <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
            {/* #1831 P2-3: one-click self-heal — after the user revokes an
                old key on the API Keys tab, Retry re-mounts and re-mints. */}
            <button className="ghost small" onClick={() => window.location.reload()}>Try again</button>
            <button className="ghost small" onClick={() => setBanner('')} aria-label="Dismiss">✕</button>
          </div>
        </div>
      )}
      {tab !== 'profile' && bannerShow(identityInv, { anon: team && team.anon, dismissed: recoveryDismissed }) && (
        <RecoveryBanner
          inv={identityInv}
          onCta={() => setTab('profile')}
          onDismiss={() => {
            try { localStorage.setItem('tt_recovery_dismissed', '1') } catch { /* best-effort */ }
            setRecoveryDismissed(true)
          }}
          onResend={handleResend}
          resendBusy={profileBusy === 'resend'}
        />
      )}
      {claimError && (
        // #1511 (code-review r2, P2): a failed claim with a valid session
        // lands on the authed shell — the error must be visible, and the
        // claim state stripped so a reload doesn't silently re-claim.
        <div className="banner" style={{ background: 'rgba(248,113,113,0.1)', borderBottom: '1px solid rgba(248,113,113,0.3)', color: 'var(--red,#f87171)', padding: '0.6rem 1.5rem', fontSize: 13, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <span role="alert">{claimError}</span>
          <button className="ghost small" onClick={() => { setClaimError(''); try { sessionStorage.removeItem(CLAIM_KEY_STORAGE) } catch { /* best-effort */ } window.history.replaceState({}, '', window.location.pathname) }} aria-label="Dismiss">✕</button>
        </div>
      )}
      <header className="dash-header">
        <div className="logo">Tortoise</div>
        <nav>
          <button className={tab === 'overview' ? 'active' : ''} onClick={() => { setTab('overview'); setSelectedSessionId(null); setSessionDetail(null); }}>Overview</button>
          <button className={tab === 'keys' ? 'active' : ''} data-tab="keys" onClick={() => { setTab('keys'); setSelectedSessionId(null); setSessionDetail(null); }}>API Keys</button>
          <button className={tab === 'graphs' ? 'active' : ''} data-tab="graphs" onClick={() => setTab('graphs')}>Graphs</button>
          <button className={tab === 'members' ? 'active' : ''} onClick={() => setTab('members')}>Members</button>
          {/* #1623: Billing — plan, usage, upgrade/portal. Session-gated like
              the rest of the dashboard (anon teams get the Protect screen). */}
          <button className={tab === 'billing' ? 'active' : ''} onClick={() => setTab('billing')}>Billing</button>
          {/* #2000 (W4): Settings — the 7th tab (R2-11), owner of Memory
              sources + GitHub connect + Setup guide + capture view/delete
              homes (P3). W6/W9 consume (single-owner R2-10). */}
          <button className={tab === 'settings' ? 'active' : ''} data-tab="settings" onClick={() => setTab('settings')}>Settings</button>
        </nav>
        {/* #2332: the right-side controls (Setup + account blob) are one
            atomic cluster — the CSS keeps them grouped at every width and
            never lets them scatter onto separate wrapped rows. */}
        <div className="header-cluster">
        {/* #1689: always-visible — OUTSIDE the nav; reopens the wizard at
            step 0 (skills). (The nav wraps via flex-wrap on narrow windows
            since #1874, so Setup is no longer displaced by nav overflow.) */}
        <button className="ghost small setup-header" onClick={() => { setWizardStep(0); setWelcomeMode(true) }}>Setup</button>
        {/* #1148-ux + #1874: account blob — GitHub/Vercel/Linear pattern:
            the avatar menu is the personal-account surface (identity block
            + Profile entry + workspace switch + Log out); the trigger shows
            the current workspace name. Replaces the bare team <select>
            (which read as "No team" and gave no account context). */}
        <div className="account-blob" ref={accountBlobRef}>
          <button
            className="account-blob-btn"
            ref={accountBlobBtnRef}
            onClick={() => {
              const opening = !accountMenuOpen
              setAccountMenuOpen(opening)
              if (opening) loadPendingInvites()  // #1875: refresh on open
            }}
            onKeyDown={(e) => { if (e.key === 'Escape') setAccountMenuOpen(false) }}
            aria-expanded={accountMenuOpen}
            aria-label={`Account menu — ${currentTeamName || 'No organization'}`}
          >
            <span className="account-avatar" aria-hidden="true">
              {(currentTeamName || 'O').charAt(0).toUpperCase()}
            </span>
            <span className="account-name">{currentTeamName || 'No organization'}</span>
            <span className="account-chevron" aria-hidden="true">▾</span>
          </button>
          <ReauthDialog
        open={reauthOpen}
        busy={reauthBusy}
        error={reauthError}
        providers={(identityInv && identityInv.methods || []).map((x) => x.provider)}
        passwordMode={reauthPasswordMode}
        onClose={() => { closeReauth(); pendingReauthRef.current = null; setReauthPasswordMode(false) }}
        onPassword={handleReauthPassword}
        onProvider={handleReauthProvider}
      />
      {/* #api-keys-ux: key creation modal — form stage (name + expiry) transitions to done stage (show key once + copy) */}
      {keyModalOpen && (
        <div className="modal-backdrop" onClick={() => { if (!keyModalBusy) { setKeyModalOpen(false); setNewKey(null); setNewKeyExpiresAt(null) } }}>
          <div className="modal key-create-modal" role="dialog" aria-modal="true" aria-label="Create API key"
               onClick={(e) => e.stopPropagation()}>
            {keyModalStage === 'form' && (
              <>
                <h2>Create new API key</h2>
                <div className="inline-form" style={{ marginTop: 8 }}>
                  <input
                    placeholder="Name (e.g. CI, staging)"
                    aria-label="New key name"
                    value={newKeyName}
                    maxLength={64}
                    onChange={(e) => setNewKeyName(e.target.value)}
                    onKeyDown={(e) => e.key === 'Enter' && !(newKeyExpiryPreset === 'custom' && !expiryDaysFromDate(newKeyExpiryDate)) && (async () => { setKeyModalBusy(true); setKeyModalStage('form'); await createKey(); setKeyModalBusy(false); setKeyModalStage('done') })()}
                  />
                  <select
                    aria-label="Expiry"
                    value={newKeyExpiryPreset}
                    onChange={(e) => setNewKeyExpiryPreset(e.target.value)}
                  >
                    {KEY_EXPIRY_PRESETS.map((p) => (
                      <option key={p.id} value={p.id}>{p.label}</option>
                    ))}
                  </select>
                  {newKeyExpiryPreset === 'custom' && (
                    <input
                      type="date"
                      aria-label="Custom expiry date"
                      value={newKeyExpiryDate}
                      min={new Date(Date.now() + _MS_PER_DAY).toISOString().slice(0, 10)}
                      max={new Date(Date.now() + KEY_MAX_EXPIRY_DAYS * _MS_PER_DAY).toISOString().slice(0, 10)}
                      onChange={(e) => setNewKeyExpiryDate(e.target.value)}
                    />
                  )}
                </div>
                <div className="new-key-actions">
                  <button className="ghost" onClick={() => { setKeyModalOpen(false); setNewKey(null); setNewKeyExpiresAt(null) }} disabled={keyModalBusy}>Cancel</button>
                  <button
                    onClick={async () => { setKeyModalBusy(true); await createKey(); setKeyModalBusy(false); setKeyModalStage('done') }}
                    disabled={keyModalBusy || (newKeyExpiryPreset === 'custom' && !expiryDaysFromDate(newKeyExpiryDate))}
                  >{keyModalBusy ? 'Creating…' : 'Create key'}</button>
                </div>
                {error && <p className="error" role="alert" style={{ marginTop: 8 }}>{error}</p>}
              </>
            )}
            {keyModalStage === 'done' && (
              <>
                <h2>New API key</h2>
                <p className="dim">Copy this key now — it is shown once only.</p>
                <code className="key-value">{newKey}</code>
                {newKeyExpiresAt ? (
                  <span className="dim">expires {fmtExpiryDate(newKeyExpiresAt)}</span>
                ) : (
                  <span className="dim">never expires</span>
                )}
                <div className="new-key-actions">
                  <button onClick={() => { navigator.clipboard.writeText(newKey); setWizardDurableKey(newKey); setNewKey(null); setNewKeyExpiresAt(null); setKeyModalOpen(false) }}>Copy &amp; done</button>
                </div>
              </>
            )}
          </div>
        </div>
      )}
      {accountMenuOpen && (
            /* P2-1 (a11y, cycle-2): drop role=menu/menuitem — the full APG
               menu pattern (arrow-key roving focus) isn't implemented, and a
               declared menu contract without arrow nav is worse than none.
               Disclosure pattern: labeled group + plain buttons (needs no
               arrow-key handling; Tab + Enter work natively). */
            <div className="account-menu" role="group" aria-label="Account actions">
              <div className="account-menu-section">
                <div className="account-menu-label">Personal Account</div>
              {/* #1874: identity block — the PERSON. Session: display_name →
                  email-prefix fallback (pattern main.jsx:1227). Team-name
                  fallback is DEFENSIVE — the menu never renders without a
                  session in the current architecture (no-session → /auth,
                  anon → Protect screen). */}
              <div className="account-identity" role="group" aria-label="Account identity">
                <span className="account-avatar" aria-hidden="true">
                  {(sessionMetaRef.current?.display_name ||
                    (sessionMetaRef.current?.email ? sessionMetaRef.current.email.split('@')[0] : '') ||
                    currentTeamName || 'O').charAt(0).toUpperCase()}
                </span>
                <div className="account-identity-text">
                  <span className="account-identity-name">
                    {sessionMetaRef.current?.display_name ||
                      (sessionMetaRef.current?.email ? sessionMetaRef.current.email.split('@')[0] : '') ||
                      currentTeamName || 'No organization'}
                  </span>
                  {sessionMetaRef.current?.email && (
                    <span className="account-identity-email">{sessionMetaRef.current.email}</span>
                  )}
                </div>
              </div>
              <button className="account-menu-profile" onClick={() => { setTab('profile'); setAccountMenuOpen(false) }}>
                Profile
              </button>
              <div className="account-menu-micro-divider" />
              <button className="account-menu-logout" onClick={logout}>
                Log out
              </button>
              </div>

              <div className="account-menu-divider" />

              <div className="account-menu-section">
                <div className="account-menu-label">Organization</div>
                {/* #2494: org context row — static; tier badge lives here. */}
                <div className="account-menu-org">
                  <span className="account-avatar small" aria-hidden="true">
                    {(currentTeamName || 'O').charAt(0).toUpperCase()}
                  </span>
                  <span className="account-org-name">{currentTeamName || 'No organization'}</span>
                  {team?.tier && <span className="tier-badge">{team.tier}</span>}
                </div>
                {teams.length > 1 && (
                  <>
                    <div className="account-menu-label">Switch organization</div>
                    {teams.map((t) => (
                      <button
                        key={t.team_id}
                        className={t.team_id === currentTeamId ? 'active' : ''}
                        aria-current={t.team_id === currentTeamId ? 'true' : undefined}
                        onClick={() => {
                          if (t.team_id !== currentTeamId) switchTeam(t.team_id)
                          setAccountMenuOpen(false)
                        }}
                      >
                        <span className="account-avatar small" aria-hidden="true">
                          {(t.team_name || 'T').charAt(0).toUpperCase()}
                        </span>
                        <span>{t.team_name}</span>
                        {t.team_id === currentTeamId && <span className="account-check" aria-hidden="true">✓</span>}
                      </button>
                    ))}
                  </>
                )}
                {/* #1875: invitee-side pending invites — OUTSIDE the
                    multi-team gate so single-team users also see invites. */}
                {pendingInvites && pendingInvites.length > 0 && (
                  pendingInvites[0] && pendingInvites[0]._loadError ? (
                    <div className="account-invite" role="alert">
                      <span className="dim small">{pendingInvites[0]._loadError}</span>
                    </div>
                  ) : (
                  <>
                    <div className="account-menu-label">Invites</div>
                    {pendingInvites.map((inv) => (
                      <div key={inv.invitation_id} className="account-invite">
                        <div className="account-invite-text">
                          <span className="account-invite-team">{inv.team_name}</span>
                          <span className="dim small">{inv.inviter_email ? `by ${inv.inviter_email}` : ''}</span>
                          {inv.error && <span className="account-invite-error" role="alert">{inv.error}</span>}
                        </div>
                        <div className="account-invite-actions">
                          <button
                            className="ghost small"
                            disabled={pendingInvitesBusy !== ''}
                            onClick={() => acceptPendingInvite(inv)}
                          >
                            {pendingInvitesBusy === inv.invitation_id ? 'Joining…' : 'Accept'}
                          </button>
                          <button
                            className="ghost small"
                            disabled={pendingInvitesBusy !== ''}
                            onClick={() => declinePendingInvite(inv)}
                          >
                            Decline
                          </button>
                        </div>
                      </div>
                    ))}
                    </>
                ))}
                <button className="account-menu-create" onClick={() => {
                  // #2392 (a11y): capture the focus-restore anchor BEFORE the
                  // menu closes — this item unmounts with the account menu
                  // under the dialog, so close hands focus to the always-
                  // mounted blob trigger button instead.
                  rememberRestoreTarget(createTeamRestoreRef, accountBlobBtnRef.current)
                  setCreateTeamOpen(true); setCreateTeamName(''); setCreateTeamError(''); setCreateTeamUpgrade(false); setAccountMenuOpen(false)
                }}>
                  + Create new organization
                </button>
              </div>
            </div>
          )}
        </div>
        </div>
        {/* #1877: create-team dialog — gated-on-click upgrade UX. The 402
            state explains "upgrade a team, then create" (the new team
            doesn't exist until the gate passes); the CTA lands on Billing
            (#1876's team selector). */}
        {createTeamOpen && (
          <div className="modal-backdrop" onClick={() => { if (!createTeamBusy) closeCreateTeam() }}>
            <div className="modal" role="dialog" aria-modal="true" aria-label="Create a new organization"
                 onClick={(e) => e.stopPropagation()}
                 onKeyDown={(e) => { if (e.key === 'Escape' && !createTeamBusy) closeCreateTeam() }}>
              {createTeamUpgrade ? (
                <>
                  <h3>Create a new organization</h3>
                  <p className="error" role="alert">{createTeamError}</p>
                  <p className="dim">The free plan includes one organization. Upgrade an existing organization to create more.</p>
                  <div className="row" style={{ marginTop: 12 }}>
                    {/* #2392 (a11y): autoFocus moves focus INTO the dialog in
                        the upgrade branch too (the non-upgrade branch's name
                        input already autofocuses). */}
                    <button className="btn-primary" autoFocus onClick={() => { closeCreateTeam(); setTab('billing') }}>
                      Upgrade
                    </button>
                    <button className="ghost" onClick={closeCreateTeam}>Cancel</button>
                  </div>
                </>
              ) : (
                <>
                  <h3>Create a new organization</h3>
                  <input
                    aria-label="Organization name"
                    placeholder="Organization name"
                    autoFocus
                    value={createTeamName}
                    onChange={(e) => setCreateTeamName(e.target.value)}
                    onKeyDown={(e) => { if (e.key === 'Enter' && !createTeamBusy) handleCreateTeam() }}
                  />
                  {createTeamError && <p className="error" role="alert">{createTeamError}</p>}
                  <div className="row" style={{ marginTop: 12 }}>
                    <button className="btn-primary" onClick={handleCreateTeam} disabled={createTeamBusy}>
                      {createTeamBusy ? 'Creating…' : 'Create organization'}
                    </button>
                    <button className="ghost" onClick={closeCreateTeam} disabled={createTeamBusy}>Cancel</button>
                  </div>
                </>
              )}
            </div>
          </div>
        )}
        {team && team.tier !== 'team' && (
          <a className="tier-badge" href="https://tortoise.premiselabs.co/product.html#pricing" target="_blank" rel="noreferrer">
            {team.tier || 'free'} tier · Upgrade
          </a>
        )}
        {/* #1290: manage subscription — Stripe portal (upgrade/downgrade/cancel)
            for teams with an existing Stripe customer (#310 backend exists). */}
        {team && canManageSubscription && (
          <button className="tier-badge tier-manage" onClick={manageBilling} disabled={billingPending}>
            {billingPending ? 'Opening portal…' : 'Manage subscription'}
          </button>
        )}
        {team && team.status === 'flagged' && (
          <span className="tier-badge" title="Suspicious activity detected — see security alerts">⚠ flagged</span>
        )}
        {team && team.tier === 'team' && (
          <span className="tier-badge tier-team">Team tier</span>
        )}
      </header>

      <main>
        {/* #1841: screen-reader progress cue for the overview skeleton —
            role=status is implicitly aria-live=polite, so the transition
            (Loading overview… → Overview loaded) is announced without the
            shimmering frames (which are aria-hidden).
            #1842 P2-3: the text keys on overviewAnnounced state (flipped
            when the overview data actually completes), never on `team` alone
            (announced while per-card shimmers still ran) and never on the
            tab (a text change on tab-switch re-announced). Once flipped the
            text is stable, so the live region speaks exactly once. */}
        <p className="sr-only" role="status">
          {overviewAnnounced ? 'Overview loaded' : 'Loading overview…'}
        </p>
        {suspended && (
          <div className="error banner" role="alert">
            ⚠️ {suspended.message || 'This organization has been suspended due to unusual activity.'}
            {suspended.appeal_url && (
              <span>
                {' '}— <a href={suspended.appeal_url} target="_blank" rel="noreferrer">Appeal suspension</a>
              </span>
            )}
          </div>
        )}
        {error && (
          <div className="error banner">
            {error}
            {/402|upgrade|quota|limit|checkout|billing/i.test(error) && (
              <span>
                {' '}— <button className="ghost" onClick={upgrade}>Upgrade plan</button>
              </span>
            )}
          </div>
        )}

        {tab === 'overview' && alerts.length > 0 && (
          <section className="overview" aria-label="Security alerts">
            <h2>Security alerts</h2>
            <p className="dim small">Suspicious activity detected on this Organization. Revoke any key you don't recognize.</p>
            <ul>
              {alerts.map((a, i) => (
                <li key={i}>
                  <strong>{a.type}</strong> — {a.message}{' '}
                  <span className="dim small">{a.at ? new Date(a.at).toLocaleString() : ''}</span>
                </li>
              ))}
            </ul>
          </section>
        )}
        {tab === 'overview' && team === null && (
          // #1841: frames-first — the overview grid renders IMMEDIATELY as
          // skeleton frames (the old code gated the WHOLE section on /v1/team,
          // leaving the tab empty below the menu until the round-trip
          // resolved, then popping all six cards at once). aria-busy tells
          // assistive tech content is on the way; the role=status cue above
          // announces the transition. The reentry / empty-state / graph-
          // missing branches below take over unchanged once the team lands.
          // #1842 P2-5: a FAILED team switch (restore error) leaves team null
          // forever — render the real card frame with '—' values so it reads
          // as a load failure, not an eternal shimmer.
          // #1842 P1 (re-review, b): frameStale — the same '—' frame after
          // the 15s skeleton floor, covering the no-error eternal shimmer
          // (loadTeams no-op / loader never ran).
          // #2000 (W4): the skeleton mirrors the CALM Overview's EXACTLY-3
          // element shape (DE2E-2: connection status / memory digest / next
          // action) — never the old 6 stat-card frame.
          <section className="overview" aria-busy={!error && !frameStale}>
            <h2>Overview</h2>
            <div className="cards">
              {error || frameStale
                ? ['Connection status', 'Memory digest', 'Next action'].map((label) => (
                    <div className="card" key={label}>
                      <div className="card-val">—</div>
                      <div className="card-label">{label}</div>
                    </div>
                  ))
                : ['Connection status', 'Memory digest', 'Next action'].map((label) => (
                    <div className="card" key={label}>
                      <div className="card-val"><span className="skeleton" style={SKEL_VALUE} aria-hidden="true" /></div>
                      <div className="card-label"><span className="skeleton" style={SKEL_LABEL} aria-hidden="true" /></div>
                    </div>
                  ))}
            </div>
          </section>
        )}
        {tab === 'overview' && team && showReentryCard && (
          // #1643/#1692: re-entry — a returning user with an empty graph gets
          // the getting-started wizard (harness → integrations → skills →
          // seed), not a raw-curl dead end.
          <section className="overview empty-state graph-missing">
            <h2>Continue setting up {shownOrgName || 'your organization'}</h2>
            {/* #2361 review-r3: the card's key copy resolves from durable
                ROWS + role (never the in-memory snippet, which is dropped at
                wizard end): members can't create keys, and a fresh owner
                create would 402 once the org's key allowance is used. */}
            <p className="dim">
              {snippetKey || durableConnect.source === 'rows-durable'
                ? (isOwnerAdmin
                    ? "Your Organization's API key is live — finish the setup below to connect your agent (the setup step shows a fresh key, or you can use an existing one)."
                    : "You're in — finish the setup below to connect your agent (paste the key an owner or admin shared with you).")
                : (isOwnerAdmin
                    ? "Your Organization is live — finish the setup below to connect your agent. No API key yet? One is created on the connect step when you get there."
                    : "Your Organization is live — finish the setup below to connect your agent. You'll need an API key: ask an owner or admin to share one, then paste it on the connect step.")}
            </p>
            <div className="empty-actions">
              <button className="btn-primary" onClick={() => { connectedOnceRef.current = false; setWizardPaused(false); onboardingRefreshedAtDoneRef.current = false; setWizardStep(0); setWelcomeMode(true) }}>
                Continue setup →
              </button>
              {!snippetKey && (
                <button type="button" className="ghost" onClick={() => setTab('keys')}>
                  Go to API Keys →
                </button>
              )}
            </div>
          </section>
        )}
        {tab === 'overview' && team && !showReentryCard && team.graph_ready === false && (team.point_count ?? 0) === 0 && (
          // #1591 (UX design): a clear first-data card — plain copy, a
          // styled copyable snippet, and a single primary action.
          <section className="overview empty-state graph-missing">
            <h2>Continue setting up {shownOrgName || 'your organization'}</h2>
            {snippetKey ? (
              // #1831 P2-1: only show the copyable snippet when a real key
              // exists — after a recoverable mint failure (#1830) the state
              // is '' and the snippet would render `Bearer ` with an empty
              // key (and the "key is live" copy would be false). #2246: the
              // key source is snippetKey (welcomeKey || apiKey) — the
              // first-timer's in-memory reveal, never a localStorage read.
              <>
                <p className="dim">
                  Your Organization and API key are live — the graph is created the moment
                  you add data. Connect your agent, or add a point yourself:
                </p>
                <div className="snippet-wrap">
                  <pre className="snippet">{firstDataSnippet}</pre>
                  <button
                    type="button"
                    className="snippet-copy"
                    onClick={(e) => {
                      try { navigator.clipboard.writeText(firstDataSnippet) } catch { /* clipboard blocked */ }
                      e.currentTarget.textContent = 'Copied'
                      setTimeout(() => { e.currentTarget.textContent = 'Copy' }, 1600)
                    }}
                  >
                    Copy
                  </button>
                </div>
              </>
            ) : (
              // #2361 review-r2: role-aware — a member can't create keys and
              // a second fresh create would 402 once the 2-key allowance is
              // used. Members get the ask-owner path; the keys tab is still
              // one click away for owners (their only first-party surface).
              <p className="dim">
                {isOwnerAdmin
                  ? (durableConnect.source === 'rows-durable'
                      ? "Your Organization's API keys are live — connect your agent below (the setup step can mint up to your plan's key limit, or use an existing one)."
                      : 'Your Organization is live — connect your agent below (its key is created on the connect step, or in the API Keys tab).')
                  : "Your Organization is live — connect your agent below. You'll need an API key to paste: ask an owner or admin to share one."}
              </p>
            )}
            <div className="empty-actions">
              <button type="button" className="btn-primary" onClick={() => setTab('keys')}>
                Go to API Keys →
              </button>
              <a className="ghost" href="https://tortoise.premiselabs.co/welcome" target="_blank" rel="noreferrer">
                Connect your agent →
              </a>
            </div>
          </section>
        )}
        {tab === 'overview' && team && !showReentryCard && team.graph_ready !== false && (team.point_count ?? 0) === 0 && (
          <section className="overview empty-state">
            <h2>Welcome to your Tortoise graph</h2>
            <p className="dim">Connect your agent so it remembers why, not just what.</p>
            <div className="empty-actions">
              <a className="btn-primary" href="https://tortoise.premiselabs.co/welcome" target="_blank" rel="noreferrer">
                Connect your agent →
              </a>
              {snippetKey && (
                <span className="dim small">or run: <code>{`curl -X POST https://api.premiselabs.co/v1/points -H "Authorization: Bearer ${snippetKey.slice(0, 12)}…" -H "Content-Type: application/json" -d '{"content":"hello graph","kind":"statement"}'`}</code></span>
              )}
            </div>
          </section>
        )}
        {tab === 'overview' && team && !showReentryCard && team.graph_ready !== false && (team.point_count ?? 0) > 0 && (
          // #2000 (W4): the CALM Overview — EXACTLY 3 elements (DE2E-2):
          // connection status + memory digest + next action. ZERO feature
          // toggles — every source toggle (github_connected, github_indexed,
          // github_docs_indexed, session_recording) lives in Settings →
          // Memory sources; the Setup-guide card lives in Settings → Setup
          // guide (this grid renders the same state as its next-action
          // element). Stat cards relocated to their tabs (Billing shows
          // usage/points/graphs/users; Graphs/Members/API-Keys tabs show
          // their own lists; Backups moved to the API Keys tab).
          <section className="overview" aria-label="Overview">
            <h2>Overview</h2>
            <div className="cards">
              <OverviewConnectionCard state={onboarding} loading={onboardingLoading} />
              <OverviewDigestCard points={team.point_count ?? 0} />
              <OverviewNextActionCard state={onboarding} loading={onboardingLoading} onOpenSettings={() => setTab('settings')} />
            </div>
          </section>
        )}

        {/* #2000 (W4): Settings — the 7th tab (R2-11), owner of Memory
            sources + GitHub connect + Setup guide + capture view/delete
            homes (P3). Renders when the team is loaded (mirrors the Billing
            tab gate). W6/W9 consume later (single-owner R2-10). */}
        {tab === 'settings' && team && (
          <SettingsTab
            state={onboarding}
            loading={onboardingLoading}
            onResumeSetup={() => { setWizardStep(0); setWelcomeMode(true) }}
            github={wizardGithub}
            githubConnected={!!(onboarding && onboarding.github_connected)}
            reposCount={reposLoaded ? reposList.length : null}
            onConnectGithub={wizardConnectGithub}
            githubError={memoryErrors.issues}
            sessions={sessions}
            sessionsOn={!!(onboarding && onboarding.session_recording)}
            sessionsLoading={onboardingLoading}
            selectedSessionId={selectedSessionId}
            onViewSession={fetchSessionDetail}
            onClearSessionDetail={clearSessionDetail}
            sessionDetail={sessionDetail}
            detailLoading={detailLoading}
            sessionDetailError={sessionDetailError}
            onDeleteSession={deleteCapturedSession}
            deletingSessionId={sessionDeletingId}
            sessionsActionError={sessionsActionError}
            memorySourcesProps={{
              state: onboarding,
              loading: onboardingLoading,
              wizardHarness: null,  // Settings never knows the user's harness — no spurious "current" highlight
              github: wizardGithub,
              issuesWantOn,
              docsWantOn,
              indexJob,
              docsJob,
              memoryBusy,
              memoryErrors,
              onToggleIssues: toggleIssues,
              onToggleDocs: toggleDocs,
              onToggleSessions: toggleSessionRecording,
              onConnectGithub: wizardConnectGithub,
              onIndexDocs: indexDocs,
              onReindexGithub: reindexGithub,
              reposList,
              reposLoaded,
              reposLoadFailed,
              branchLists,
              docsScope,
              issuesScope,
              onDocsScopeChange: handleDocsScopeChange,
              onIssuesScopeChange: handleIssuesScopeChange,
              onLoadBranches: loadBranches,
            }}
          />
        )}

        {tab === 'profile' && (
          <ProfileTab
            inv={identityInv}
            loading={identityLoading}
            error={identityError}
            onRetry={fetchIdentity}
            onUnlink={handleUnlink}
            unlinkBusy={profileBusy === 'unlink'}
            onAddOAuth={handleAddOAuth}
            onAddEmail={handleAddEmail}
            addBusy={profileBusy === 'oauth' || profileBusy === 'email' || profileBusy === 'reauth'}
            addError={profileError}
            onResend={handleResend}
            resendBusy={profileBusy === 'resend'}
          />
        )}
        {tab === 'keys' && (
          <section>
            {/* #1148: dashboard API-login toggle — claimed teams only.
                Anon teams get the prominent Protect banner instead (above
                the tabs); this toggle lets a claimed owner stop using a raw
                API key as a dashboard login credential. Key remains valid
                for graph operations; management actions (keys, backups,
                billing) require session sign-in when disabled. */}
            {/* #1148 review P2-3/P3-2/P3-3: owner-only + session-authed only
                (a key-login user or member must not see/flip this — the
                server enforces session+owner; the API-key auth fallback is
                dropped to avoid self-lockout). Switch is labeled by the
                SETTING ("API key dashboard login"), not the action. */}
            {team && !team.anon && isOwnerAdmin && authMode === 'session' && (
              <div className="toggle-row">
                <button
                  className="switch"
                  role="switch"
                  aria-checked={team.dashboard_key_login !== false}
                  data-on={team.dashboard_key_login !== false}
                  onClick={toggleDashboardKeyLogin}
                  disabled={toggleBusy}
                  aria-label="API key dashboard login"
                />
                <div className="toggle-body">
                  <h4>
                    API key dashboard login{' '}
                    {team.dashboard_key_login !== false && <span style={{ color: 'var(--accent,#06b6d4)' }}>(recommended: disable)</span>}
                    {team.dashboard_key_login === false && <span style={{ color: 'var(--green,#4ade80)' }}>disabled ✓</span>}
                  </h4>
                  <p>
                    We recommend disabling your API key as a dashboard sign-in
                    method. The key stays valid for graph operations — managing
                    keys, restoring backups, and billing will require your
                    GitHub/Google sign-in instead.
                  </p>
                  {toggleError && <p className="error" role="alert">{toggleError}</p>}
                </div>
              </div>
            )}
            <div className="row">
              <h2>API Keys</h2>
              {/* #2246 (review, P1/P2): member key creation is gated
                  CLIENT-side as dashboard policy AND server-side since #2297
                  POLICY A: the POST /v1/team/keys session lane is
                  owner/admin-gated (_require_owner_admin — POST/DELETE since
                  #2297, PATCH since #1148; list stays member-open #1828).
                  Every row action + the create form are isOwnerAdmin
                  render-gated; the dashboard treats key management as
                  owner/admin-managed (Members tab + wizard member gate
                  precedent), so members get a notice + the paste-into-setup
                  escape instead of the create form. P2 (layout): the member
                  notice renders as a FULL-WIDTH paragraph BELOW this .row
                  (Members-tab precedent) — as a span inside the flex .row it
                  wrapped badly beside the h2 on narrow viewports. */}
              {isOwnerAdmin && (
                <button className="ghost" onClick={() => { setKeyModalOpen(true); setKeyModalStage('form'); setError(''); setNewKeyName(''); setNewKeyExpiryPreset('30'); setNewKeyExpiryDate('') }}>+ New key</button>
              )}
            </div>
            {!isOwnerAdmin && (
              <p className="dim small" style={{ margin: '0 0 1rem' }}>
                Only owners and admins can create or rotate keys in this dashboard. Paste an existing key into the setup step to connect an agent.
              </p>
            )}
            {/* #1148-ux review: "Lost your key? Generate a new one" removed — the + New key button already covers it. */}
            {capNotice && (
              <div className="cap-notice" style={{ display: 'flex', gap: '0.75rem', alignItems: 'center', margin: '0.5rem 0 1rem', padding: '0.6rem 0.85rem', border: '1px solid var(--border, #d0d7de)', borderRadius: 8, background: 'var(--bg-soft, #f6f8fa)' }}>
                <span className="dim small">{capNotice}</span>
                {team?.checkout_price_id ? (
                  <button className="ghost small" onClick={upgrade} disabled={checkoutPending}>
                    {checkoutPending ? 'Opening checkout…' : 'Upgrade'}
                  </button>
                ) : (
                  <a className="ghost small" href="https://tortoise.premiselabs.co/product.html#pricing" target="_blank" rel="noreferrer">See pricing</a>
                )}
              </div>
            )}

            {/* #2735: rotate's replacement reveal. #2667 moved create-key
                into the Create API key modal and DELETED the standalone
                `.new-key` block — but regenerateKey still set newKey/
                newKeyExpiresAt, so a rotate minted the replacement and then
                never showed it (the user could not configure the new key).
                Restored inline from its OWN state (rotatedKey), so the create
                modal's dismiss paths (which clear newKey) can never destroy
                this already-revoked-old-key replacement. */}
            {rotatedKey && (
              <div className="new-key">
                <strong>Your new key (shown once):</strong>
                <code className="key-value">{rotatedKey.plaintext}</code>
                {rotatedKey.expiresAt ? (
                  <span className="dim">expires {fmtExpiryDate(rotatedKey.expiresAt)}</span>
                ) : (
                  <span className="dim">never expires</span>
                )}
                <button className="ghost small" onClick={async () => {
                  // #2735: clear the one-time plaintext ONLY after the clipboard
                  // write resolves. The old key is already revoked, so a failed
                  // write that still cleared the reveal would destroy the only
                  // copy of the live replacement (#2392 class). On failure keep
                  // the key visible + select it for a manual copy — mirrors
                  // revealKey's fallback.
                  try {
                    await navigator.clipboard.writeText(rotatedKey.plaintext)
                    setRotatedKey(null)
                  } catch {
                    const el = document.querySelector('.new-key .key-value')
                    if (el) {
                      const range = document.createRange()
                      range.selectNodeContents(el)
                      const sel = window.getSelection()
                      sel.removeAllRanges()
                      sel.addRange(range)
                    }
                  }
                }}>Copy &amp; done</button>
              </div>
            )}
            {/* #2246 (ADR-010): the keys table is uniform — every durable row
                carries the same owner action set (rename / toggle / trash /
                Rotate); no "in use by this dashboard" row, no rotate-only
                suppression (the browser never holds a key, so no row is held).
                .keys-table-wrap = overflow-x:auto for the 4-action cell on
                320-375px viewports. */}
            <div className="keys-table-wrap">
            <table>
              <thead><tr><th scope="col">Name</th><th scope="col">Prefix</th><th scope="col">Created</th><th scope="col">Last used</th><th scope="col">Expires</th><th scope="col">Status</th><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
              <tbody>
                {managedKeys.length === 0 && <tr><td colSpan="7" className="dim">No keys yet.</td></tr>}
                {managedKeys.map((k) => (
                  <tr key={k.id}>
                    <td>
                      {editingKeyId === k.id ? (
                        <input
                          autoFocus
                          maxLength={64}
                          className="key-name-input"
                          value={editingKeyName}
                          placeholder="Label"
                          aria-label={`Label for key ${k.key_prefix || k.id?.slice(0, 8)}`}
                          onChange={(e) => setEditingKeyName(e.target.value)}
                          onKeyDown={(e) => {
                            if (e.key === 'Enter') { e.preventDefault(); e.target.blur() }
                            if (e.key === 'Escape') { renameCancelRef.current = true; setEditingKeyId(null); e.target.blur() }
                          }}
                          onBlur={() => {
                            if (renameCancelRef.current) { renameCancelRef.current = false; return }
                            renameKey(k.id, editingKeyName)
                          }}
                        />
                      ) : (
                        <span className="key-name">
                          {k.name ? k.name : <span className="dim">—</span>}
                          {!k.revoked_at && isOwnerAdmin && (
                            <button
                              className="ghost small key-rename"
                              onClick={() => { renameCancelRef.current = false; setEditingKeyId(k.id); setEditingKeyName(k.name || '') }}
                              aria-label={`Rename key ${k.key_prefix || k.id?.slice(0, 8)}`}
                              title="Rename key"
                            >✏️</button>
                          )}
                        </span>
                      )}
                    </td>
                    <td><code>{k.key_prefix || k.id?.slice(0, 12)}</code></td>
                    <td>{fmtTime(k.created_at || k.createdAt)}</td>
                    {/* #2476: Last used cell — relative time + an absolute-date
                        title tooltip when the row has a last_used_at (#685 writes
                        it through on use; list_api_keys serializes null for
                        never-used keys). 'Never' is PLAIN text — no span.dim (#2426
                        lesson: the status cell's dim identifies 'disabled'; a
                        Never-in-dim cell double-matched the e2e strict mode).
                        Clock: Date.now() per render — NOT App's skeleton-gated
                        `now` (that one ticks only while the Overview tab has a live
                        loading floor; on the keys tab it would freeze and the label
                        could read 'just now' forever after a reload surfaced a newer
                        stamp). Per-render freshness is the same semantics as the
                        sibling Expires cell (fmtExpiry's Date.now() default) and
                        formatRelativeTime's callers in the memory-sources panel. */}
                    <td>{(() => {
                      const lu = k.last_used_at
                      const rel = formatRelativeTime(lu, Date.now())
                      if (!rel) return 'Never'
                      return <span title={`Last used ${fmtTime(lu)}`}>{rel}</span>
                    })()}</td>
                    {/* #2426: Expires cell — absolute date · 'Never' when
                        null · amber 'in N days' at ≤14d · terminal 'expired'
                        (row-dim styling family) when past. Expired rows stay
                        listed (tombstone) and stay rotatable — rotate
                        re-applies the lifetime span with a fresh clock. */}
                    <td>{(() => {
                      const ex = fmtExpiry(k.expires_at)
                      // #2426: plain 'Never' / date text (no dim class — the
                      // status cell's dim identifies 'disabled'; the research
                      // pattern is an unadorned Never). Expiring/expired rows
                      // carry their own classes + title tooltip.
                      if (!ex.cls) return ex.text
                      return <span className={ex.cls} title={ex.title || undefined}>{ex.text}</span>
                    })()}</td>
                    <td>
                      {k.revoked_at ? <span className="revoked">revoked</span>
                        : k.enabled === false ? <span className="dim">disabled</span>
                        : <span className="live">active</span>}
                    </td>
                    <td>{!k.revoked_at && isOwnerAdmin && (
                      <span className="key-actions">
                        {/* #2246 (uniform rows): Rotate is available on EVERY
                            durable row (ADR-010 — no held row exists to be
                            rotate-only). Rotate mints the replacement first
                            (label carried over), revokes this one, and shows
                            the replacement once — it never installs into the
                            browser. */}
                        <button
                          className="ghost small key-rotate"
                          disabled={busy}
                          onClick={() => regenerateKey(k.id)}
                          aria-label={`Rotate key ${k.key_prefix || k.id?.slice(0, 8)}`}
                          title="Rotate key — creates a replacement and revokes this one"
                        >Rotate</button>
                        {/* #1148-ux review: on/off toggle (new keys default on) */}
                        <button
                          className="key-toggle"
                          role="switch"
                          aria-checked={k.enabled !== false}
                          data-on={k.enabled !== false}
                          onClick={() => toggleKeyEnabled(k.id, k.enabled !== false)}
                          aria-label={`Toggle key ${k.key_prefix || k.id?.slice(0, 8)}`}
                        />
                        {/* #1148-ux review: trash = delete with confirmation
                            (revokeKey already confirm()s — the dialog names the
                            row so a one-click delete never kills an agent key
                            the user cannot identify, #2246) */}
                        <button
                          className="ghost small key-trash"
                          onClick={() => revokeKey(k.id)}
                          aria-label={`Delete key ${k.key_prefix || k.id?.slice(0, 8)}`}
                          title="Delete key"
                        >🗑</button>
                      </span>
                    )}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            </div>
            <BackupsCard status={backupsStatus} count={(backupInfo && backupInfo.count) || null} />
          </section>
        )}

        {tab === 'graphs' && (
          <section>
            <div className="row">
              <h2>Graphs</h2>
              {authMode === 'session' ? (
                tierCreateLocked(team && team.tier) ? (
                  /* C7 indicator 5: free/anon create is locked with the 🔒
                     + upgrade CTA (the server's 402 tier gate blocks
                     free/anon — _GRAPH_TIER_BLOCKED; solo is NOT locked,
                     pricing.json max_graphs=2 with a 409 quota gate). */
                  <span className="dim small">
                    🔒 Your plan includes {team && team.max_graphs} graph
                    {(team && team.max_graphs) !== 1 ? 's' : ''} —{' '}
                    <button className="ghost" onClick={upgrade}>Upgrade to add more</button>
                  </span>
                ) : (
                  <div className="inline-form">
                    <input
                      placeholder="New graph name"
                      aria-label="New graph name"
                      value={newGraphName}
                      onChange={(e) => setNewGraphName(e.target.value)}
                      onKeyDown={(e) => e.key === 'Enter' && createGraph()}
                    />
                    <button onClick={createGraph} disabled={busy || !newGraphName.trim()}>+ Create</button>
                  </div>
                )
              ) : (
                <span className="dim small">Sign in required</span>
              )}
            </div>
            {/* #2308: free/anon (tierCreateLocked) SKIP the meter — the 🔒
                "Your plan includes 1 graph" line + upgrade CTA right above
                already states the cap; "1/1 graphs used" would restate it
                in the same screenful. Solo (2) / pro (∞) have no lock line,
                so the meter stays there. Gated on the LIVE tier so a
                mid-session upgrade (checkout poll → refreshTeam → setTeam)
                restores the meter without a reload. */}
            {authMode === 'session' && graphsStatus === 'ok' && graphsLoaded
              && !tierCreateLocked(team && team.tier) && (
              /* C7 indicator 1: the graph-count meter (used · cap).
                 max_graphs null → ∞ (pro/team); solo=2 shows used/total.
                 The server's 409 cap-reject is authoritative. */
              <p className="dim small" aria-label="Graph usage meter">
                {graphsMeter(sortedGraphRows(graphs), team && team.max_graphs).label}
              </p>
            )}
            <table>
              <thead><tr><th>Name</th><th>Kind</th><th>Status</th><th>Keys</th><th><span className="sr-only">Actions</span></th></tr></thead>
              <tbody>
                {graphsStatus === 'loading' && authMode === 'session' && <tr><td colSpan="5" className="dim">Loading graphs…</td></tr>}
                {graphsStatus === 'denied' && <tr><td colSpan="5" className="dim">Graph list is only visible to members.</td></tr>}
                {graphsStatus === 'error' && <tr><td colSpan="5" className="dim">Couldn't load graphs — check your connection and try again.</td></tr>}
                {graphsStatus === 'ok' && graphs.length === 0 && <tr><td colSpan="5" className="dim">No graphs yet — create your first one above.</td></tr>}
                {graphsStatus === 'ok' && sortedGraphRows(graphs).map((g) => (
                  <tr key={g.graph_id} className={confirmDeleteId === g.graph_id ? 'graph-delete-arm' : undefined}>
                    <td>
                      {editingGraphId === g.graph_id ? (
                        /* #2701: inline rename (mirrors the API-Keys ✏️
                        edit: Enter/blur commits via renameGraph, Escape
                        cancels via graphRenameCancelRef). Owner/admin-only
                        (rename is owner/admin-managed server-side). */
                        <input
                          autoFocus
                          className="graph-name-input"
                          value={editingGraphName}
                          placeholder="Graph name"
                          aria-label={`Rename graph ${g.name || g.graph_id}`}
                          onChange={(e) => setEditingGraphName(e.target.value)}
                          onKeyDown={(e) => {
                            if (e.key === 'Enter') { e.preventDefault(); e.target.blur() }
                            if (e.key === 'Escape') { graphRenameCancelRef.current = true; setEditingGraphId(null); e.target.blur() }
                          }}
                          onBlur={() => {
                            if (graphRenameCancelRef.current) { graphRenameCancelRef.current = false; return }
                            renameGraph(g.graph_id, editingGraphName)
                          }}
                        />
                      ) : (
                        <span className="graph-name">
                          <code>{g.name}</code>
                          {isOwnerAdmin && (
                            <button
                              className="ghost small graph-rename"
                              onClick={() => { graphRenameCancelRef.current = false; setEditingGraphId(g.graph_id); setEditingGraphName(g.name || '') }}
                              aria-label={`Rename graph ${g.name || g.graph_id}`}
                              title="Rename graph"
                            >✏️</button>
                          )}
                        </span>
                      )}
                    </td>
                    <td>{g.kind}</td>
                    <td>{g.status === 'active' ? 'active' : <span className="revoked">{g.status}</span>}</td>
                    <td>
                      {graphKeysSuppressed(g) ? (
                        /* #2306: the default graph has NO per-graph keys —
                        its key_count is 0 in both lanes and its keys (the
                        team-wide graph_id-NULL rows) are managed on the
                        API Keys tab, never through a per-graph panel. Suppress
                        the numeric cell on default rows and offer the tab
                        affordance instead of a dead 0 (supabase) or an
                        unmanageable bound-default count (registry capstone). */
                        <span className="default-keys-cell" title="The default graph has no per-graph keys — its team-wide keys are managed on the API Keys tab.">
                          <span className="dim" aria-hidden="true">—</span>{' '}
                          <button
                            type="button"
                            className="ghost small"
                            onClick={() => setTab('keys')}
                            aria-label="The default graph has no per-graph keys — manage its team-wide keys on the API Keys tab"
                          >
                            API Keys tab
                          </button>
                        </span>
                      ) : (
                        g.key_count != null ? g.key_count : '—'
                      )}
                    </td>
                    <td className="graph-actions">
                      {canManageGraphKeys(g) && (
                        <button
                          className="ghost small"
                          onClick={() => openGraphPanel(g.graph_id)}
                          aria-expanded={panelGraphId === g.graph_id}
                          aria-label={`Manage keys for graph ${g.name}`}
                        >
                          {panelGraphId === g.graph_id ? 'Close' : 'Keys'}
                        </button>
                      )}
                      {/* #2701: trash = delete with a TYPE-TO-CONFIRM modal.
                          graphCanDelete(g) gates enablement — the default
                          graph's 🗑 renders DISABLED with the reason (its row
                          is the org's only undeletable graph; the server 403s
                          it as a code guard — the UI lock mirrors, never
                          precedes, the API). */}
                      {isOwnerAdmin && (
                        /* #2701: disabled buttons swallow native title
                           tooltips in major browsers — wrap the locked 🗑
                           in a span carrying the reason so the "why" is
                           still discoverable on hover (aria-label covers AT). */
                        <span
                          className="graph-trash-wrap"
                          title={graphCanDelete(g)
                            ? 'Delete graph — moves it to Trash'
                            : "The default graph can't be deleted"}
                        >
                        <button
                          className="ghost small graph-trash"
                          disabled={!graphCanDelete(g) || graphBusy}
                          aria-disabled={!graphCanDelete(g)}
                          aria-label={graphCanDelete(g)
                            ? `Delete graph ${g.name}`
                            : "The default graph can't be deleted"}
                          onClick={() => {
                            if (!graphCanDelete(g)) return
                            setDeleteConfirmTyped('')
                            setConfirmDeleteId(g.graph_id)
                            setError('')
                          }}
                        >🗑</button>
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {/* #2701: type-to-confirm delete modal. Open only for a
                deletable (custom) row — the default graph's 🗑 is disabled.
                The gate is the literal word 'delete' typed by hand
                (deleteConfirmTyped) — a strong accidental-delete deterrent
                on top of the 7-day Trash recovery window. Failure keeps the
                modal open (armed retry) and lands the reason in the
                page-level banner (#2301). */}
            {isOwnerAdmin && confirmDeleteId && (() => {
              const dg = graphs.find((x) => x.graph_id === confirmDeleteId)
              if (!dg) return null
              const typedOk = deleteTypedMatches(deleteConfirmTyped)
              return (
                <div className="modal-backdrop" onClick={() => { if (!graphBusy) { setConfirmDeleteId(null); setDeleteConfirmTyped('') } }}>
                  <div className="modal graph-delete-modal" role="dialog" aria-modal="true" aria-label="Delete graph" onClick={(e) => e.stopPropagation()}>
                    <h3>Delete {dg.name || dg.graph_id}?</h3>
                    <p className="danger-note">This is destructive and permanent — think before you confirm.</p>
                    <ul className="dim small">
                      <li><strong>Keys are revoked immediately</strong> — applications using {dg.name || 'this graph'}'s keys stop working now.</li>
                      <li>The graph moves to <strong>Trash</strong>, where you can restore it for {TRASH_GRACE_DAYS} days.</li>
                      <li>After {TRASH_GRACE_DAYS} days, the graph <strong>and its backups are permanently erased</strong>.</li>
                    </ul>
                    <p className="dim small" style={{ margin: '0.6rem 0 0.4rem' }}>
                      Type <code>delete</code> to confirm:
                    </p>
                    <input
                      autoFocus
                      className="delete-confirm-input"
                      value={deleteConfirmTyped}
                      aria-label="Type delete to confirm"
                      onChange={(e) => setDeleteConfirmTyped(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter' && typedOk && !graphBusy) { e.preventDefault(); deleteGraphRow(dg.graph_id) }
                        if (e.key === 'Escape' && !graphBusy) { setConfirmDeleteId(null); setDeleteConfirmTyped('') }
                      }}
                    />
                    <div className="new-key-actions" style={{ marginTop: '0.8rem' }}>
                      <button className="ghost" disabled={graphBusy} onClick={() => { setConfirmDeleteId(null); setDeleteConfirmTyped('') }}>Cancel</button>
                      <button
                        className="danger"
                        disabled={!typedOk || graphBusy}
                        onClick={() => deleteGraphRow(dg.graph_id)}
                      >{graphBusy ? 'Deleting…' : 'Delete graph'}</button>
                    </div>
                  </div>
                </div>
              )
            })()}
            {/* #2304 trash (owner/admin): deleted custom graphs inside the
                7-day recovery window. Purged rows never appear; legacy
                tombstones (no deleted_at) show as past-window until the
                purge cadence clears them. */}
            {isOwnerAdmin && trash.length > 0 && (
              <details className="trash-section" open={false}>
                <summary aria-label={`Trash, ${trash.length} item${trash.length === 1 ? '' : 's'}`}>
                  🗑 Trash ({trash.length}) — deleted graphs are kept 7 days, then permanently erased
                </summary>
                {trashMsg && <div className="error banner">{trashMsg}</div>}
                {trashStatus === 'error' && <p className="dim small">Couldn't load trash — check your connection and try again.</p>}
                <table>
                  <thead><tr><th>Name</th><th>Deleted</th><th>Recovery</th><th><span className="sr-only">Actions</span></th></tr></thead>
                  <tbody>
                    {sortedTrashRows(trash).map((t) => {
                      // #2465: past-window + legacy rows are NOT restorable
                      // (server 410s them — pending permanent erasure). Show
                      // Inspect only, so the UI never offers a restore that
                      // the server refuses.
                      const restorable = !!(t.deleted_at && trashDaysLeft(t.deleted_at) > 0)
                      return (
                      <tr key={t.graph_id} className={confirmRestoreId === t.graph_id ? 'graph-delete-arm' : undefined}>
                        <td><code>{t.name}</code></td>
                        <td>{t.deleted_at ? fmtTime(t.deleted_at) : '—'}</td>
                        <td className="dim small">{trashEraseLabel(t.deleted_at)}</td>
                        <td>
                          {confirmRestoreId === t.graph_id ? (
                            <span className="graph-del-confirm">
                              Restore {t.name}? Its keys stay revoked — you'll mint fresh keys after.{' '}
                              <button className="ghost small" disabled={graphBusy} onClick={() => restoreTrashRow(t.graph_id)}>Restore</button>{' '}
                              <button className="ghost small" disabled={graphBusy} onClick={() => setConfirmRestoreId(null)}>Cancel</button>
                            </span>
                          ) : (
                            <>
                              {restorable && (
                                <button
                                  className="ghost small"
                                  disabled={graphBusy}
                                  onClick={() => { setConfirmRestoreId(t.graph_id); setTrashMsg('') }}
                                  aria-label={`Restore graph ${t.name}`}
                                >
                                  Restore
                                </button>
                              )}
                              {restorable && ' '}
                              <button
                                className="ghost small"
                                disabled={graphBusy}
                                onClick={() => inspectTrashRow(t.graph_id)}
                                aria-expanded={trashInspectId === t.graph_id}
                                aria-label={`Inspect graph ${t.name}`}
                              >
                                Inspect
                              </button>
                            </>
                          )}
                        </td>
                      </tr>
                      )
                    })}
                  </tbody>
                </table>
                {trashInspectId && trashInspect && trashInspect.graph_id === trashInspectId && (
                  <div className="graph-key-panel" aria-label={`Inspect ${trashInspect.name || trashInspectId}`}>
                    <div className="graph-key-panel-head">
                      <strong>Inspect {trashInspect.name || trashInspectId}</strong>
                      <button className="ghost small" onClick={() => { setTrashInspectId(null); setTrashInspect(null) }}>Close</button>
                    </div>
                    {(() => {
                      // #2565 (re-audit P3): the rescue copy must match what
                      // the server will actually do for THIS row. Past-window
                      // + legacy rows are NOT restorable (server 410s them) —
                      // the panel must not promise "restore brings everything
                      // back". Restore also never pulls archives (separate
                      // POST /v1/backups step) and a partially-deleted row
                      // restores as an empty graph.
                      const trow = trash.find((x) => x.graph_id === trashInspectId)
                      const rowRestorable = !!(trow && trow.deleted_at && trashDaysLeft(trow.deleted_at) > 0)
                      return (
                        <>
                          <p className="dim small">
                            {rowRestorable
                              ? 'Rescue view — restore brings the graph\'s live state back. Backup archives are restored separately (a partially-deleted graph restores as an empty graph).'
                              : 'Past the recovery window — this graph is pending permanent erasure and cannot be restored. The archives below are its last remaining artifacts.'}
                          </p>
                          <ul className="dim small">
                            <li>Deleted: {trashInspect.deleted_at ? fmtTime(trashInspect.deleted_at) : '—'}</li>
                            <li>Restorable archives: {trashInspect.archive_count != null ? trashInspect.archive_count : 0}</li>
                            {trashInspect.latest_backup ? (
                              <li>
                                Latest backup:{' '}
                                {trashInspect.latest_backup.created_at ? fmtTime(trashInspect.latest_backup.created_at) : 'recently'}
                                {trashInspect.latest_backup.node_count != null
                                  ? ` · ${trashInspect.latest_backup.node_count.toLocaleString()} nodes / ${trashInspect.latest_backup.edge_count != null ? trashInspect.latest_backup.edge_count.toLocaleString() : '?'} edges`
                                  : ''}
                              </li>
                            ) : trashInspect.archive_count > 0 ? (
                              // #2469: archives exist but no readable manifest
                              // (dump-only runs / crash window) — say so honestly.
                              <li>Backups exist — no readable manifest for details.</li>
                            ) : (
                              <li>No backups yet for this graph.</li>
                            )}
                          </ul>
                          <p className="dim small">
                            {rowRestorable
                              ? `After ${TRASH_GRACE_DAYS} days, the graph and its backups are permanently erased.`
                              : 'Kept only until the operator purge runs — nothing here can be restored.'}
                          </p>
                        </>
                      )
                    })()}
                  </div>
                )}
              </details>
            )}
            {panelGraphId && (
              <div className="graph-key-panel" aria-label={`Keys for ${graphNameFor(panelGraphId) || 'graph'}`}>
                <div className="graph-key-panel-head">
                  <strong>Keys for {graphNameFor(panelGraphId) || 'this graph'}</strong>
                  <button className="ghost small" onClick={closeGraphPanel}>Close</button>
                </div>
                {!isOwnerAdmin && <p className="dim small">Only owners and admins can manage graph keys.</p>}
                {isOwnerAdmin && (
                  <div className="inline-form">
                    <input
                      placeholder="Key label (optional)"
                      aria-label="New graph key label"
                      value={graphKeyName}
                      onChange={(e) => setGraphKeyName(e.target.value)}
                      onKeyDown={(e) => e.key === 'Enter' && mintGraphKey()}
                    />
                    <button className="ghost small" onClick={mintGraphKey} disabled={graphBusy}>+ Mint key</button>
                  </div>
                )}
                {graphMsg && <div className="error banner">{graphMsg}</div>}
                {panelKeysStatus === 'loading' && <p className="dim small">Loading keys…</p>}
                {panelKeysStatus === 'error' && <p className="dim small">Couldn't load keys — try again.</p>}
                {panelKeysStatus === 'ok' && panelKeys.length === 0 && (
                  // #2307: role-aware empty copy — the "mint one above" line
                  // is only truthful next to the owner/admin mint form; members
                  // (no mint control) get who-can-create instead.
                  <p className="dim small">{graphKeyPanelEmptyLine(isOwnerAdmin)}</p>
                )}
                {panelKeysStatus === 'ok' && panelKeys.length > 0 && (
                  <table>
                    <thead><tr><th scope="col">Name</th><th scope="col">Prefix</th><th scope="col">Created</th><th scope="col">Status</th><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
                    <tbody>
                      {panelKeys.map((k) => (
                        <tr key={k.id || k.key_id} className={k.revoked_at ? 'row-dim' : undefined}>
                          <td>{k.name ? <span className="key-name">{k.name}</span> : <span className="dim">—</span>}</td>
                          <td><code>{k.key_prefix || (k.id || '').slice(0, 12)}</code></td>
                          <td>{fmtTime(k.created_at || k.createdAt)}</td>
                          <td>{k.revoked_at ? <span className="revoked">revoked</span> : <span>active</span>}</td>
                          <td>
                            {!k.revoked_at && isOwnerAdmin && (
                              <button
                                className="ghost small"
                                disabled={graphBusy}
                                onClick={() => revokePanelKey(k.id || k.key_id)}
                                aria-label={`Revoke key ${k.key_prefix || (k.id || '').slice(0, 8)}`}
                              >
                                Revoke
                              </button>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
            )}
            {revealKey && (
              /* C7 indicator 2: the ONE-TIME reveal modal. Mounted only from
                 a mint response (create-graph envelope or per-graph mint);
                 the plaintext exists nowhere else (hash-only storage) — no
                 show-key route ever re-renders it. Clipboard failure keeps
                 the key visible in the modal text; dismissing clears state. */
              <div className="modal-backdrop"
                   onPointerDown={(e) => {
                     // Track where the press began: only a pointerdown on the
                     // backdrop itself may later dismiss via click. A press that
                     // started inside the card (text-selection drag, coarse
                     // pointer) must never destroy the one-time secret (#2392
                     // review P1) — even if the drag releases over the backdrop,
                     // the click's common-ancestor target is the backdrop and a
                     // plain onClick there would fire mid-copy.
                     revealBackdropPressRef.current = !(e.target && e.target.closest && e.target.closest('.modal'))
                   }}
                   onClick={() => {
                     if (revealBackdropPressRef.current) {
                       revealBackdropPressRef.current = false
                       closeRevealKey()
                     }
                   }}>
                <div className="modal" role="dialog" aria-modal="true" aria-label="New key — shown once"
                     onClick={(e) => e.stopPropagation()}
                     onKeyDown={(e) => { if (e.key === 'Escape') closeRevealKey() }}>
                  <h2>{revealKey.title}</h2>
                  <p className="dim small">Your new key — <strong>shown once</strong>. Copy it now; you won't see it again.</p>
                  <code className="key-value">{revealKey.plaintext}</code>
                  <div className="claim-actions">
                    {/* #2392 (a11y): autoFocus the primary control on open —
                        before this the trigger behind the backdrop kept focus.
                        closeRevealKey (backdrop/Escape/Copy & done/I saved it)
                        restores focus to that trigger. */}
                    <button className="ghost" autoFocus onClick={async () => {
                      try {
                        await navigator.clipboard.writeText(revealKey.plaintext)
                        closeRevealKey()
                      } catch {
                        // Clipboard unavailable (e.g. non-secure context): the
                        // key stays visible so the user can copy by hand — it is
                        // never re-fetched or re-shown elsewhere. Select the key
                        // text itself for a manual Ctrl/Cmd+C.
                        const el = document.querySelector('.modal .key-value')
                        const range = document.createRange()
                        if (el) {
                          range.selectNodeContents(el)
                          const sel = window.getSelection()
                          sel.removeAllRanges()
                          sel.addRange(range)
                        }
                      }
                    }}>Copy &amp; done</button>
                    <button className="ghost" onClick={closeRevealKey}>I saved it</button>
                  </div>
                </div>
              </div>
            )}
          </section>
        )}

        {tab === 'members' && (
          <section>
            <div className="row">
              <h2>Members</h2>
              {isOwnerAdmin && (
                <div className="inline-form">
                  <input
                    type="email"
                    placeholder="member@example.com"
                    aria-label="Member email"
                    value={inviteEmail}
                    onChange={(e) => setInviteEmail(e.target.value)}
                  />
                  <select value={inviteRole} onChange={(e) => setInviteRole(e.target.value)} aria-label="Invite role">
                    <option value="member">member</option>
                    <option value="admin">admin</option>
                  </select>
                  <button onClick={inviteMember} disabled={busy || !inviteEmail.includes('@')}>Invite</button>
                </div>
              )}
            </div>
            {!isOwnerAdmin && (
              <p className="dim small">Only owners and admins can manage members.</p>
            )}
            {/* #1875: Pro CAN invite up to capacity — the notice is only
                for Free/Solo (the old copy rendered for Pro too and
                contradicted the working invite form). */}
            {team && team.tier !== 'pro' && team.tier !== 'team' && isOwnerAdmin && (
              <p className="dim small">Invites require the Pro or Team tier — <a href="https://tortoise.premiselabs.co/product.html#pricing" target="_blank" rel="noreferrer">upgrade to add members</a>.</p>
            )}
            <table>
              <thead><tr><th>Email / User</th><th>Role</th><th>Status</th><th></th></tr></thead>
              <tbody>
                {membersStatus === 'loading' && authMode === 'session' && <tr><td colSpan="4" className="dim">Loading members…</td></tr>}
                {membersStatus === 'denied' && <tr><td colSpan="4" className="dim">Member list is only visible to owners and admins.</td></tr>}
                {membersStatus === 'error' && <tr><td colSpan="4" className="dim">Couldn't load members — check your connection and try again.</td></tr>}
                {membersStatus === 'ok' && members.length === 0 && <tr><td colSpan="4" className="dim">No members yet.</td></tr>}
                {membersStatus === 'ok' && members.map((m) => {
                  const invited = m.status === 'invited'
                  return (
                    <tr key={m.user_id || m.email}>
                      <td><code>{m.email || m.user_id}</code></td>
                      <td>
                        {m.role}
                        {isOwnerAdmin && !invited && m.role !== 'owner' && (
                          <button
                            className="ghost small"
                            onClick={() => changeRole(m.user_id, m.role === 'admin' ? 'member' : 'admin')}
                          >
                            {m.role === 'admin' ? '→ member' : '→ admin'}
                          </button>
                        )}
                      </td>
                      <td>{invited ? <span className="revoked">pending invite</span> : <span className="live">active</span>}</td>
                      <td>{isOwnerAdmin && !invited && m.role !== 'owner' && <button className="ghost small" onClick={() => removeMember(m.user_id)}>Remove</button>}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </section>
        )}

        {/* #1623: Billing — current plan, limits/usage, plan options, upgrade
            + Stripe portal CTAs. Renders only when team is loaded (the rest
            of the dashboard guards the same way). Price ids come from
            team.checkout_price_ids (server-resolved, #310) — never hardcoded. */}
        {tab === 'billing' && team && (
          <section className="billing">
            <div className="row">
              <h2>Billing — {currentTeamName || 'this organization'}</h2>
              {/* #1876: per-tenant billing — in-section context selector
                  (reuses switchTeam; single-team users get the name only). */}
              {teams.length > 1 && (
                <select
                  className="billing-team-select"
                  aria-label="Billing organization"
                  value={currentTeamId || ''}
                  onChange={(e) => { switchTeam(e.target.value); setTab('billing') }}
                >
                  {teams.map((t) => (
                    <option key={t.team_id} value={t.team_id}>{t.team_name}</option>
                  ))}
                </select>
              )}
              {canManageSubscription && (
                <button className="tier-badge tier-manage" onClick={manageBilling} disabled={billingPending}>
                  {billingPending ? 'Opening portal…' : 'Manage subscription'}
                </button>
              )}
            </div>

            {/* Current plan card */}
            <div className="card" style={{ marginBottom: 24 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '0.5rem' }}>
                <div>
                  <span className="tier-badge">{(TIER_LABELS[team.tier] || team.tier || 'free')} plan</span>
                  {team.subscription_status && (
                    <span className="dim small" style={{ marginLeft: '0.5rem' }}>
                      {STATUS_LABELS[team.subscription_status] || team.subscription_status}
                    </span>
                  )}
                </div>
                {team.customer_email && <span className="dim small">Billing: {team.customer_email}</span>}
              </div>
              <div className="cards" style={{ marginTop: 12, marginBottom: 0 }}>
                <div className="card"><div className="card-val">{(team.write_ops_used ?? 0).toLocaleString()}</div><div className="card-label">Write ops used{(team.write_ops_limit ? ` / ${team.write_ops_limit.toLocaleString()}` : '')}{team.write_ops_period ? ` · ${team.write_ops_period}` : ''}</div></div>
                <div className="card"><div className="card-val">{team.point_count ?? 0}</div><div className="card-label">Data points</div></div>
                <div className="card"><div className="card-val">{team.max_graphs == null ? '∞' : team.max_graphs}</div><div className="card-label">Graphs</div></div>
                <div className="card"><div className="card-val">{team.max_users == null ? '∞' : team.max_users}</div><div className="card-label">Users</div></div>
              </div>
              {(team.write_ops_limit ?? 0) > 0 && (
                <div style={{ marginTop: 4 }}>
                  <div style={{ background: 'var(--surface-hover, rgba(255,255,255,0.06))', borderRadius: 6, height: 8, overflow: 'hidden' }}>
                    <div style={{
                      width: `${Math.min(100, Math.round(((team.write_ops_used ?? 0) / team.write_ops_limit) * 100))}%`,
                      background: 'var(--accent, #06b6d4)',
                      height: '100%',
                    }} />
                  </div>
                  <p className="dim small" style={{ marginTop: 6 }}>
                    {Math.round(((team.write_ops_used ?? 0) / team.write_ops_limit) * 100)}% of monthly write ops
                    {team.overage_eligible && team.overage_cost_usd ? ` · overage after limit at $${team.overage_cost_usd}/10k ops` : ''}
                  </p>
                </div>
              )}
              {hasActiveSubscription && (
                <p className="dim small" style={{ marginTop: 8 }}>
                  Changes to your plan (upgrade, downgrade, cancel, invoices) go through the Stripe customer portal.
                </p>
              )}
            </div>

            {/* Plan options */}
            <h3 style={{ fontSize: 15, marginBottom: 10 }}>Plans</h3>
            <div className="plans-grid">
              {planOptions().map((p) => {
                const current = p.tier === team.tier
                const hasPrice = Boolean(team.checkout_price_ids?.[p.tier])
                return (
                  <div key={p.tier} className={`plan-card${current ? ' current' : ''}`}>
                    <div className="plan-card-head">
                      <strong>{p.label}</strong>
                      {p.popular && !current && <span className="dim small">popular</span>}
                      {current && <span className="tier-badge" style={{ fontSize: 10, padding: '1px 8px' }}>Current plan</span>}
                    </div>
                    <div className="plan-price">
                      {p.price === 0 ? '$0' : `$${p.price}`}<span className="dim small">/mo</span>
                    </div>
                    <ul className="plan-limits">
                      {p.limits.map((l) => <li key={l}>{l}</li>)}
                    </ul>
                    {current ? (
                      <button className="ghost" disabled title="You're on this plan">Current plan</button>
                    ) : canManageSubscription ? (
                      <button className="ghost" onClick={manageBilling} disabled={billingPending}>
                        {billingPending ? 'Opening portal…' : 'Manage subscription'}
                      </button>
                    ) : hasPrice ? (
                      <button className="btn-primary" onClick={() => upgradeToPrice(team.checkout_price_ids[p.tier])} disabled={checkoutPending}>
                        {checkoutPending ? 'Opening checkout…' : 'Upgrade'}
                      </button>
                    ) : (
                      <a className="ghost" href="https://tortoise.premiselabs.co/product.html#pricing" target="_blank" rel="noreferrer">See pricing</a>
                    )}
                  </div>
                )
              })}
            </div>
          </section>
        )}

              </main>
    </div>
  )
}

// #1728 Slice 3 (Tasks 16-17): the ONE shared Memory-sources surface — rendered
// on the wizard step-1 AND the dashboard Overview panel (same component, same
// toggle set + state machine). Three toggles (issues / docs / sessions)
// reuse role="switch"/aria-checked; row failures render under the row with
// role="alert" (never the global 402-upgrade banner); status regions carry
// aria-live="polite". #1927: the misled-user re-ask pane (exactly-once gate)
// was removed with the consent gate — sessions are default-ON (ToS-covered)
// and the sessions toggle here is the quiet off-switch.
function MemorySources(props) {
  const {
    state, loading, wizardHarness, github,
    issuesWantOn, docsWantOn,
    indexJob, docsJob,
    memoryBusy, memoryErrors,
    reposList, reposLoaded, reposLoadFailed, docsScope, issuesScope, branchLists,
    onToggleIssues, onToggleDocs, onToggleSessions,
    onConnectGithub, onIndexDocs, onReindexGithub,
    onDocsScopeChange, onIssuesScopeChange, onLoadBranches,
  } = props

  // #1894: relative-time ticker — keeps "Indexed · 2 min ago" and the job
  // status lines fresh WITHOUT a poll or reload. Declared BEFORE the early
  // returns (hooks rules: loading/state guards return early).
  const [now, setNow] = React.useState(Date.now())
  React.useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 30_000)
    return () => clearInterval(t)
  }, [])

  if (loading) {
    return <div className="memory-sources"><p className="dim">Loading memory sources…</p></div>
  }
  if (!state) {
    return (
      <div className="memory-sources">
        <p className="error" role="alert">Couldn't load memory sources — refresh to try again.</p>
      </div>
    )
  }

  const githubConnected = !!state.github_connected
  const sessionsOn = !!state.session_recording
  const docsIndexed = !!state.github_docs_indexed
  // issues state machine: off → on-but-not-connected (inline Connect CTA) →
  // connected+indexing. The switch reads connected OR the user's intent.
  const issuesOn = githubConnected || issuesWantOn
  const docsOn = docsWantOn || docsIndexed
  // #1894: "Indexed · <relative time>" (honest — no time when the persisted
  // timestamp is absent, e.g. legacy indexed teams). Independent of
  // connectivity: the label is a historical claim about indexing.
  const docsLabel = docsIndexedLabel(state, now)
  const githubLastIndexed = formatRelativeTime(state.github_indexed_at, now)

  const status = (h) => captureStatusForHarness(state, h)
  const lastError = (h) => lastErrorForHarness(state, h)

  return (
    <div className="memory-sources">
      {/* ── Issues toggle ── */}
      <div className="toggle-row">
        <button
          type="button"
          className="switch"
          role="switch"
          aria-checked={issuesOn}
          data-on={issuesOn ? 'true' : 'false'}
          aria-label="GitHub issues as a memory source"
          onClick={() => onToggleIssues(!issuesOn)}
          disabled={memoryBusy === 'issues'}
        />
        <div className="toggle-body">
          <h4>GitHub issues</h4>
          <p>Issues become work items with a lifecycle record.</p>
          {githubConnected ? (
            <>
              <p className="dim small" aria-live="polite">
                {github.repos != null ? `Connected — ${github.repos} repos available. ` : 'Connected. '}
                {githubLastIndexed ? `Last indexed ${githubLastIndexed}. ` : ''}
                <button type="button" className="small" onClick={onReindexGithub} disabled={memoryBusy === 'issues' || (indexJob && indexJob.status === 'started')}>
                  {indexJob && indexJob.status === 'started' ? 'Indexing…' : 'Re-index'}
                </button>
              </p>
              {/* #1845: repo-scope selector (multi-select checkboxes, "All
                  repos" default) — the list comes from GET
                  /v1/onboarding/github/repos (server-side token), never a
                  client GitHub call. repos: [] = ALL; a non-empty list =
                  exactly those repos. */}
              {!(indexJob && (indexJob.status === 'starting' || indexJob.status === 'started')) && (
                <div className="scope-selector">
                  <fieldset className="scope-fieldset">
                    <legend className="dim small">Repos to index</legend>
                    <label className="scope-option">
                      <input
                        type="checkbox"
                        checked={issuesScope.repos.length === 0}
                        onChange={(e) => onIssuesScopeChange({ repos: e.target.checked ? [] : [...reposList] })}
                      />
                      All repos
                    </label>
                    {reposList.map((r) => (
                      <label key={r} className="scope-option">
                        <input
                          type="checkbox"
                          checked={issuesScope.repos.includes(r)}
                          onChange={(e) => {
                            const next = e.target.checked
                              ? issuesScope.repos.includes(r) ? issuesScope.repos : [...issuesScope.repos, r]
                              : issuesScope.repos.filter((x) => x !== r)
                            onIssuesScopeChange({ repos: next })
                          }}
                        />
                        {r}
                      </label>
                    ))}
                    {reposLoaded && reposList.length === 0 && (
                      reposLoadFailed
                        ? <span className="dim small">Couldn't load the repo list — re-check your GitHub connection.</span>
                        : <span className="dim small">No repos listed — the index will run org-wide.</span>
                    )}
                  </fieldset>
                  <span className="dim small" aria-live="polite">
                    {issuesScope.repos.length
                      ? `Indexing ${issuesScope.repos.length} selected repo${issuesScope.repos.length > 1 ? 's' : ''}.`
                      : (reposLoaded && reposList.length > 0 ? `Indexing all ${reposList.length} repos.` : 'Indexing all repos.')}
                  </span>
                </div>
              )}
            </>
          ) : issuesWantOn ? (
            <p className="dim small">
              <button type="button" className="small" onClick={onConnectGithub} disabled={github.busy}>
                {github.busy ? 'Connecting…' : 'Connect GitHub'}
              </button>{' '}
              to bring issues in as memory sources.
            </p>
          ) : null}
          {indexJob && <GithubIndexStatus job={indexJob} now={now} />}
          {memoryErrors.issues && <p className="error" role="alert">{memoryErrors.issues}</p>}
        </div>
      </div>

      {/* ── Docs toggle ── */}
      <div className="toggle-row">
        <button
          type="button"
          className="switch"
          role="switch"
          aria-checked={docsOn}
          data-on={docsOn ? 'true' : 'false'}
          data-locked-on={docsIndexed ? 'true' : undefined}  // #1894: terminal indexed docs switch — full-opacity ON (CSS scopes on this attr; the generic disabled busy-dim stays for busy windows)
          aria-label="GitHub docs as a memory source"
          onClick={() => onToggleDocs(!docsOn)}
          disabled={memoryBusy === 'docs' || docsIndexed}  // #1835: connect-inline like issues — not connected just reveals the CTA; review P1-1: docs indexed ⇒ the switch is terminal (re-index refreshes, never un-indexes)
        />
        <div className="toggle-body">
          <h4>GitHub docs</h4>
          <p>Your repos' docs/ folders are fetched server-side and indexed as Sources.</p>
          {!githubConnected && !docsIndexed && docsWantOn ? (
            <p className="dim small">
              <button type="button" className="small" onClick={onConnectGithub} disabled={github.busy}>
                {github.busy ? 'Connecting…' : 'Connect GitHub'}
              </button>{' '}
              to index docs/ as memory sources.
            </p>
          ) : !githubConnected && !docsIndexed ? (
            <p className="dim small">Connect GitHub first to index docs.</p>
          ) : null}
          {docsIndexed && docsLabel && (
            <p className="memory-source-state" aria-live="polite">{docsLabel}</p>
          )}
          {githubConnected && (docsWantOn || docsIndexed) && !docsJob && (
            <>
              {/* #1845: repo + branch scope for the docs index — "All repos"
                  default; when specific repos are picked, each gets its own
                  branch picker ('' = default main/master fallback,
                  'all' = every branch, else a real branch from
                  GET /v1/onboarding/github/branches). */}
              <div className="scope-selector">
                <fieldset className="scope-fieldset">
                  <legend className="dim small">Repos to index</legend>
                  <label className="scope-option">
                    <input
                      type="checkbox"
                      checked={docsScope.repos.length === 0}
                      onChange={(e) => {
                        const repos = e.target.checked ? [] : [...reposList]
                        onDocsScopeChange({ ...docsScope, repos })
                      }}
                    />
                    All repos
                  </label>
                  {reposList.map((r) => {
                    const checked = docsScope.repos.includes(r)
                    const repoInfo = Object.prototype.hasOwnProperty.call(branchLists, r)
                      ? branchLists[r] || { branches: [], defaultBranch: '' }
                      : { branches: [], defaultBranch: '' }
                    const branches = repoInfo.branches || []
                    const defaultBranch = repoInfo.defaultBranch || ''
                    const currentBranch = docsScope.branches[r] || ''
                    return (
                      <div key={r} className="scope-repo-row">
                        <label className="scope-option">
                          <input
                            type="checkbox"
                            checked={checked}
                            onChange={(e) => {
                              const next = e.target.checked
                                ? docsScope.repos.includes(r) ? docsScope.repos : [...docsScope.repos, r]
                                : docsScope.repos.filter((x) => x !== r)
                              onDocsScopeChange({ ...docsScope, repos: next })
                              if (e.target.checked) onLoadBranches(r)
                            }}
                          />
                          {r}
                        </label>
                        {checked && (
                          <label className="scope-branch">
                            <span className="dim small">branch</span>
                            <select
                              value={currentBranch}
                              onChange={(e) => onDocsScopeChange({
                                ...docsScope,
                                branches: { ...docsScope.branches, [r]: e.target.value },
                              })}
                            >
                              {/* review P2-4: the default option carries the
                                  repo's API default branch ('' falls back to
                                  main/master server-side); the seeding effect
                                  sets branches[r] to that same value so the
                                  select matches. */}
                              <option value={defaultBranch || ''}>default ({defaultBranch || 'main'})</option>
                              <option value="all">all branches</option>
                              {branches.filter((b) => b !== defaultBranch && b !== '' && b !== 'all').map((b) => (
                                <option key={b} value={b}>{b}</option>
                              ))}
                            </select>
                          </label>
                        )}
                      </div>
                    )
                  })}
                  {reposLoaded && reposList.length === 0 && (
                    reposLoadFailed
                      ? <span className="dim small">Couldn't load the repo list — re-check your GitHub connection.</span>
                      : <span className="dim small">No repos listed — the index will run org-wide.</span>
                  )}
                </fieldset>
                <span className="dim small" aria-live="polite">
                  {docsScope.repos.length
                    ? `Indexing ${docsScope.repos.length} selected repo${docsScope.repos.length > 1 ? 's' : ''}.`
                    : (reposLoaded && reposList.length > 0 ? `Indexing all ${reposList.length} repos.` : 'Indexing all repos.')}
                </span>
              </div>
              <p className="dim small">Parts you want to keep private stay out — scope the index to the repos you choose.</p>
              <p className="dim small">
                <button type="button" className="small" onClick={onIndexDocs} disabled={memoryBusy === 'docs'}>
                  {memoryBusy === 'docs' ? 'Indexing…' : docsIndexed ? 'Re-index docs' : 'Index docs'}
                </button>{' '}
                {docsIndexed ? 'to refresh indexed docs.' : 'to bring your repo docs in as memory sources.'}
              </p>
            </>
          )}
          {docsJob && <DocsIndexStatus job={docsJob} now={now} />}
          {memoryErrors.docs && <p className="error" role="alert">{memoryErrors.docs}</p>}
        </div>
      </div>

      {/* ── Sessions toggle (off-switch; default ON per ToS — per-harness status) ── */}
      <div className="toggle-row">
        <button
          type="button"
          className="switch"
          role="switch"
          aria-checked={sessionsOn}
          data-on={sessionsOn ? 'true' : 'false'}
          aria-label="Agent session recording"
          onClick={() => onToggleSessions(!sessionsOn)}
          disabled={memoryBusy === 'sessions'}
        />
        <div className="toggle-body">
          <h4>Agent session recording</h4>
          <p>When on, sessions from tools with capture installed are filed to your graph as memory.</p>
          {memoryErrors.sessions && <p className="error" role="alert">{memoryErrors.sessions}</p>}
          <div className="harness-statuses">
            {HARNESS_ORDER.map((h) => {
              const st = status(h)
              const supported = !!HARNESS_CAPTURE_SUPPORT[h]
              const isCurrent = wizardHarness && h === wizardHarness
              // #2912 (review cycle 4 P2): a `wizardHarness === 'codexDesktop'`
              // alias used to live here, aliasing the Codex Desktop surface onto
              // the 'codex' row. It was unreachable: HARNESS_ORDER has no
              // codexDesktop row, the Settings call site passes
              // `wizardHarness: null` on purpose, and the only other call site is
              // the LEGACY_WIZARD_ARCHIVED rollback block, which is fed solely by
              // HARNESS_ORDER leaves. If a surface leaf ever reaches this row,
              // wire the alias through a unit-tested helper instead of a
              // source-grep tripwire.
              return (
                <div key={h} className={`harness-status status-${st}${isCurrent ? ' current' : ''}`}>
                  <div className="harness-status-head">
                    <strong>{HARNESS_NAMES[h]}</strong>
                    {/* review P2-5: aria-live lives on the PILL (the state word
                        only) — the container-level region announced the whole
                        multi-line snippet. review P2-3: unsupported harnesses
                        render the REASON only, no pill (no install path exists
                        for web/cursor — a pill would contradict it). */}
                    {supported && <span className="capture-state" aria-live="polite">{HARNESS_CAPTURE_STATUS_LABEL[st]}</span>}
                  </div>
                  {!supported && <p className="dim small">{HARNESS_CAPTURE_REASON[h]}</p>}
                  {supported && st === 'install-pending' && sessionsOn && (
                    <pre className="snippet">{HARNESS_CAPTURE_INSTALL[h]}</pre>
                  )}
                  {lastError(h) && <p className="error small" role="alert">Last attempt: {lastError(h)}</p>}
                </div>
              )
            })}
          </div>
        </div>
      </div>
    </div>
  )
}

// #1728 (Task 16/17): github index-job status line — terminal states pinned:
// "indexing complete (N issues)" success + "indexing failed — retry"
// (exhausted); eviction-expired = "status expired — re-check" (never a retry
// loop); aria-live on the region.
function GithubIndexStatus({ job, now }) {
  if (!job) return null
  if (job.status === 'starting' || job.status === 'started') {
    // review P2-10: 'starting' is the pre-POST state (job id not yet known) —
    // render the same in-progress line so the ~2s gap isn't silent.
    // #1894: live progress line (elapsed + repos + ETA from REAL signal —
    // ETA suppressed until progress > 0, never fabricated).
    const line = jobStatusLine(job, now)
    return <p className="dim small" aria-live="polite">{line ? `Indexing… · ${line}` : 'Indexing in progress…'}</p>
  }
  if (job.status === 'completed') {
    const repos = job.repos_processed != null ? ` across ${job.repos_processed} repos` : ''
    const beyond = job.issues_beyond_window ? `; ${job.issues_beyond_window} issues beyond window` : ''
    const quota = job.quota_hit ? ' (plan quota reached — index more later)' : ''
    return <p className="dim small" aria-live="polite">Indexing complete{repos}{beyond}{quota}.</p>
  }
  if (job.status === 'failed') {
    return <p className="error small" role="alert">Indexing failed — {job.error || 'retry'}</p>
  }
  if (job.status === 'expired') {
    return <p className="dim small" aria-live="polite">Status expired — re-check</p>
  }
  if (job.status === 'timeout') {
    return <p className="dim small" aria-live="polite">Still running — check back in a moment</p>
  }
  return null
}

// #1728 (Task 16/17): docs-job status line — terminal states distinct: "N
// documents indexed" success / failed-with-reason (in-flight | base-unset |
// exhausted — distinct copy, never a retry loop) / "status expired — re-check".
function DocsIndexStatus({ job, now }) {
  if (!job) return null
  if (job.status === 'starting' || job.status === 'started') {
    // review P2-10: 'starting' is the pre-POST state — same in-progress line.
    // #1894: live progress line (elapsed + repos + ETA).
    const line = jobStatusLine(job, now)
    return <p className="dim small" aria-live="polite">{line ? `Docs indexing… · ${line}` : 'Indexing docs in progress…'}</p>
  }
  if (job.status === 'completed') {
    const quota = job.quota_hit ? ' (plan quota reached)' : ''
    const repos = job.repos_processed != null ? ` across ${job.repos_processed} repos` : ''
    return <p className="dim small" aria-live="polite">{job.documents_indexed ?? 0} documents indexed{repos}{quota}.</p>
  }
  if (job.status === 'failed') {
    const err = job.error || ''
    const reason = /TORTOISE_INGEST_BASE_DIR|base dir|base is not set/i.test(err)
      ? 'docs sandbox not configured'
      : /quota|exhaust/i.test(err)
        ? 'plan quota reached'
        : /in[- ]flight|already running/i.test(err)
          ? 'a docs job is already running'
          : 'retry'
    return <p className="error small" role="alert">Docs indexing failed — {reason}</p>
  }
  if (job.status === 'expired') {
    return <p className="dim small" aria-live="polite">Status expired — re-check</p>
  }
  if (job.status === 'timeout') {
    return <p className="dim small" aria-live="polite">Still running — check back in a moment</p>
  }
  return null
}

createRoot(document.getElementById('root')).render(<App />)
