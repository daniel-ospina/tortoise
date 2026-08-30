import React from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
// #1623: plan display data (build-time import of product/pricing.json).
import { planOptions, STATUS_LABELS, TIER_LABELS } from './pricing.js'
import { HARNESS_CAPTURE_INSTALL, HARNESS_CAPTURE_REASON, HARNESS_CAPTURE_STATUS_LABEL, HARNESS_CAPTURE_SUPPORT, HARNESS_CONTINUE_LABEL, HARNESS_COPY_LABEL, HARNESS_INSTALL, HARNESS_INTRO, HARNESS_NAMES, HARNESS_ORDER, HARNESS_PERSIST, HARNESS_SKILLS, HARNESS_SKILLLESS, HARNESS_SKILLS_IN_PROMPT, HARNESS_SKILLS_IN_STEPS, HARNESS_STEPS } from './harnesses.js'
// #1728 Slice 3 (Tasks 16-17): the SHARED 4-state capture-status derivation
// (off → install-pending → waiting → active, probe-driven) — pure, node --test
// unit-tested (captureStatus.test.js). #1927: the re-ask gate predicate was
// removed with the consent gate (default-ON, ToS-covered).
import { captureStatusForHarness, lastErrorForHarness } from './captureStatus.js'
// #1894: indexed-state + job-progress derivations — pure, node --test
// unit-tested (memorySourcesStatus.test.js).
import { docsIndexedLabel, formatRelativeTime, jobStatusLine } from './memorySourcesStatus.js'
// #1708 D8: pure session-key predicate extracted to sessionKey.js (node --test
// unit-tested); imported under an alias to avoid an ESM redeclaration collision
// with the local isSessionKey wrapper below.
import { isSessionKey as isSessionKeyPredicate, isActiveKey } from './sessionKey.js'
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

const API_BASE = 'https://api.premiselabs.co'
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
    // provider tokens can exceed the 4096-byte cookie limit. provider tokens
    // are only needed by the initiating flow — strip them first; if still
    // over the cap, attempt the write anyway with a warning.
    if (encoded.length > SIZE_GUARD) {
      try {
        const obj = JSON.parse(value)
        delete obj.provider_token
        delete obj.provider_refresh_token
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
  const [apiKey, setApiKey] = React.useState(() => localStorage.getItem(KEY_STORAGE) || '')
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
  // #1559: a session-key mint failure (e.g. 429 rate limit) must surface an
  // actionable error — never the silent "Redirecting to the sign-in page…"
  // shell (which does NOT redirect and left users stuck).
  const [mountError, setMountError] = React.useState('')
  const [team, setTeam] = React.useState(null)
  const [keys, setKeys] = React.useState([])
  const [sessions, setSessions] = React.useState([])
  const [error, setError] = React.useState('')
  const [busy, setBusy] = React.useState(false)
  const [newKey, setNewKey] = React.useState(null)
  const [newKeyName, setNewKeyName] = React.useState('') // key-label: label for the next minted key
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
  const firstDataSnippet = `curl -X POST https://api.premiselabs.co/v1/points \
  -H "Authorization: Bearer ${apiKey}" \
  -H "Content-Type: application/json" \
  -d '{"content":"hello graph","kind":"statement"}'`
  const [welcomeTeamName, setWelcomeTeamName] = React.useState('')
  const [welcomeGraphName, setWelcomeGraphName] = React.useState('')
  const [welcomeProvisionError, setWelcomeProvisionError] = React.useState('')
  // #1643/#1692: the getting-started wizard (post-key steps: harness →
  // integrations → skills → seed → done). For first-timers it follows the
  // key reveal; for returning empty-graph users it re-opens at step 0
  // (harness); step-0 Back returns to the orientation card.
  const [wizardStep, setWizardStepRaw] = React.useState(0)
  const setWizardStep = React.useCallback((n) => { setWizardStepRaw(n); setWizardCopied((c) => (c === 'harness' ? '' : c)) }, [])
  const [wizardHarness, setWizardHarness] = React.useState('claude')
  const [wizardCopied, setWizardCopied] = React.useState('')
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
  // #1907: seed-step failure must surface INLINE (the global error banner
  // only renders post-welcome) — the message lives here and the retry is the
  // re-enabled 'Seed my graph' button. Cleared on every attempt.
  const [wizardSeedError, setWizardSeedError] = React.useState('')
  const [wizardDone, setWizardDone] = React.useState(false)
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

  // #1147: shared mint — POST /v1/team/keys and return the plaintext key.
  // `name` (optional) is the key label — sent only when non-empty.
  async function mintKey(activeKey, name) {
    const k = await api('/v1/team/keys', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...(activeKey ? { Authorization: `Bearer ${activeKey}` } : {}) },
      useSession: true,  // #1148: management → session JWT when signed in
      body: name ? JSON.stringify({ name }) : '{}',
    })
    return k.api_key || k.key || k
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

  const [tab, setTab] = React.useState('overview')
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
  const [currentGraphId, setCurrentGraphId] = React.useState(null)
  const [accountMenuOpen, setAccountMenuOpen] = React.useState(false) // #1148-ux: account blob dropdown
  // #1877: create-team dialog state (gated-on-click upgrade UX)
  const [createTeamOpen, setCreateTeamOpen] = React.useState(false)
  const [createTeamName, setCreateTeamName] = React.useState('')
  const [createTeamBusy, setCreateTeamBusy] = React.useState(false)
  const [createTeamError, setCreateTeamError] = React.useState('')
  const [createTeamUpgrade, setCreateTeamUpgrade] = React.useState(false)
  // #1875: invitee-side pending invites (account-menu surface)
  const [pendingInvites, setPendingInvites] = React.useState(null)  // null = not loaded
  const [pendingInvitesBusy, setPendingInvitesBusy] = React.useState('')  // '' | invitation_id
  const accountBlobRef = React.useRef(null) // #1148-ux review P2-4/P3-1: outside-click + Escape close
  React.useEffect(() => {
    if (!accountMenuOpen) return
    function onPointerDown(e) {
      if (accountBlobRef.current && !accountBlobRef.current.contains(e.target)) {
        setAccountMenuOpen(false)
      }
    }
    function onKeyDown(e) {
      if (e.key === 'Escape') setAccountMenuOpen(false)
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
  const teamIdRef = React.useRef(null)
  const teamRefreshSeqRef = React.useRef(0) // #1906 (code-review P2): monotonic seq for the welcome-path team refreshes — a post-seed refire must win over a concurrent exit refresh (a pre-seed point_count must never clobber the post-seed count)
  const fallbackTeamIdRef = React.useRef(null) // Round-4: team auto-selected by recoverKey 400-fallback
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
  // P1/P2 (code-review): per-team data-plane key cache. Bootstrap mints are
  // capped at 3 active per team, so cache the minted key per team_id and
  // reuse on switch instead of re-minting (which would 429 after 3 switches).
  const teamKeysRef = React.useRef({})
  // #714 (main): session detail view state
  const [selectedSessionId, setSelectedSessionId] = React.useState(null)
  const [sessionDetail, setSessionDetail] = React.useState(null)
  const [detailLoading, setDetailLoading] = React.useState(false)

  const headers = apiKey ? { Authorization: `Bearer ${apiKey}` } : {}

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
    let authHeaders = headers
    if (opts.useSession && sessionTokenRef.current) {
      authHeaders = { Authorization: `Bearer ${sessionTokenRef.current}` }
    }
    // #1835: json-body calls (onboarding-state PATCHes, etc.) must send
    // Content-Type: application/json or the server 422s on the body.
    const hasBody = typeof opts.body === 'string'
    const hdrs = { ...authHeaders, ...(opts.headers || {}) }
    if (hasBody && !hdrs['Content-Type'] && !hdrs['content-type']) hdrs['Content-Type'] = 'application/json'
    const res = await fetch(`${API_BASE}${path}`, { ...opts, headers: hdrs })
    if (!res.ok) {
      const body = await res.json().catch(() => ({}))
      // Round-11: attach the HTTP status — hosted_api.py returns detail strings
      // ('Invalid API key', 'Unauthorized', …), never '401', so status-based
      // checks (switchTeam re-mint) must read e.status, not message content.
      // #308: suspended teams get a dict detail — surface the appeal link.
      const sus = suspendedFromDetail(body.detail)
      const err = new Error(sus ? (sus.message || 'Team suspended') : (typeof body.detail === 'string' ? body.detail : `HTTP ${res.status}`))
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
    setProfileBusy('unlink'); setProfileError('')
    try {
      await api('/v1/user/identity/unlink', {
        method: 'POST', useSession: true,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ identity_id: identityId }),
      })
      await fetchIdentity()
    } catch (e) {
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
      setReauthOpen(false)
      await fetchIdentity()
      const pending = pendingReauthRef.current
      pendingReauthRef.current = null
      if (pending) {
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
            email: pending.email, uid: beforeUidRef.current }))
        } catch { /* best-effort */ }
      }
      const { error } = await supabaseClient.auth.signInWithOAuth({
        provider,
        options: { redirectTo: `${window.location.origin}${window.location.pathname}?reauth=1` },
      })
      if (error) throw new Error(error.message)
      if (!pending) setReauthOpen(false)
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
            setProfileError("This email is also used by another team — reach out if that's unexpected.")
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
          const { data: sess } = await supabaseClient.auth.getSession()
          const returnedUid = sess && sess.session && sess.session.user && sess.session.user.id
          if (pending && pending.uid && returnedUid && returnedUid !== pending.uid) {
            setProfileError("Signed in as a different account — sign out and retry.")
            return
          }
          if (pending) {
            // re-prompt the NEW password (never persisted across the round-trip)
            pendingReauthRef.current = { email: pending.email, promptPassword: true }
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
          window.history.replaceState({}, '', `${window.location.pathname}${params.toString() ? `?${params}` : ''}`)
        }
      }, 2000)
      return () => clearInterval(poll)
    }
    if (cancelled) {
      window.clearTimeout(checkoutResetTimerRef.current)
      setCheckoutPending(false)
      params.delete('checkout')
      window.history.replaceState({}, '', `${window.location.pathname}${params.toString() ? `?${params}` : ''}`)
    }
  }, [team?.subscription_status])

  // ── Session auth: on load, try the shared cookie session ──
  // P1 (code-review): POST /v1/session/key returns 400 "team_id required
  // (multiple memberships)" when the user belongs to 2+ teams — and the team
  // switcher exists exactly for those users. Mint for a concrete team_id, and
  // on a 400 auto-select the first membership and retry instead of silently
  // degrading to the API-key screen.
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
      // #1847: first-timer mount race — this mount effect fires BEFORE the
      // mount gate runs provisionInApp (which creates the team), so the GET
      // 403s 'No team membership' and the MemorySources panel would render
      // its error card until reload. Swallow ONLY that exact no-team 403,
      // discriminated by ref (Round-11: status-based checks read e.status /
      // refs, not message content): teamIdRef is provably null at swallow
      // time for the first-timer pre-provision case (provisionInApp never
      // sets it; the currentTeamId effect only fires post-provision), and
      // being a ref it has no stale-closure trap (the mount effect closes
      // over the first-render [], so teamsList.length would wrongly swallow
      // cross-team 403s for returning users). Suspended teams return a dict
      // detail → e.suspended is set, and a successful /v1/teams with a
      // still-403 onboarding leaves teamIdRef set — both fall through to the
      // normal error state (honest, retryable card) below: keeping the
      // loading state leaves the panel in its initial state;
      // finishWelcomeLoads() re-fires this after provisioning and is the
      // authoritative load.
      if (e && e.status === 403 && !e.suspended && !teamIdRef.current) return
      setOnboardingLoading(false)  // best-effort — the surface renders its error state
    }
  }
  React.useEffect(() => { refreshOnboarding() }, [])  // eslint-disable-line react-hooks/exhaustive-deps

  // ── #1643 wizard actions ────────────────────────────────────────────────
  const wizardSteps = ['Connect your tool', 'Memory sources', 'Your agent\'s toolkit', 'Seed your graph', 'You\'re set']

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
  const seedPrefilledRef = React.useRef(false)
  React.useEffect(() => {
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
      refreshTeam(welcomeKey || '', undefined, teamRefreshSeqRef.current).catch(() => {})
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
  async function finishWelcomeLoads() {
    await loadTeams().catch(() => {})
    loadBackups(welcomeKey || '').catch(() => {})
    // #1906: the first-timer path never ran loadAll (keys+sessions) — the
    // Overview 'API Keys' card stayed 0 until reload. loadTeams just
    // pinned currentTeamId (Round-8), so loadAll's ?team_id= targets the
    // new team. Fire-and-forget like completeLogin's card loads; each
    // loader carries its own staleness guard.
    loadAll(welcomeKey || '').catch(() => {})
    // #1906: refetch the team so the Overview 'Data points' card reflects
    // the seeded graph — team.point_count was captured at provisioning
    // (pre-seed, 0). Also covers the header-exit-without-seed case (0
    // stays 0 — honest).
    // Pass the current refresh seq: if a seed refire bumps it mid-flight,
    // this pre-seed response is dropped (it must not clobber the count).
    refreshTeam(welcomeKey || '', undefined, teamRefreshSeqRef.current).catch(() => {})
    // #1847: re-fire the onboarding-state load NOW that the team exists —
    // the mount-time refreshOnboarding() fired BEFORE provisioning (mount
    // gate → provisionInApp) and 403'd 'No team membership' for first-timers,
    // leaving the Overview MemorySources panel on its error card until
    // reload. The team now exists → this fetch succeeds → the toggles render
    // on the first view.
    await refreshOnboarding().catch(() => {})
  }

  async function wizardComplete() {
    setWizardDone(true)
    api(`/v1/onboarding/state${onboardingTeamQ()}`, { method: 'PATCH', useSession: true,
      body: JSON.stringify({ onboarding_complete: true }) }).catch(() => {})
    window.history.replaceState({}, '', '/')
    setWelcomeMode(false)
    // #1842 P1-1: the first-timer flow (provisionInApp → welcome wizard →
    // wizardComplete) never ran loadTeams/loadBackups — those fired only in
    // completeLogin/switchTeam (returning users). currentTeamId stayed null →
    // the currentTeamId effect never fired loadMembers/loadGraphs → Graphs/
    // Users/Backups shimmered forever after "Open my dashboard →".
    await finishWelcomeLoads()
  }

  // #1566: in-app first-time provisioning (ported from welcome.html).
  // The tenant-provision edge function authorizes the app origin; the
  // membership row + the key are created here. The raw tt_ key NEVER leaves
  // the app origin (#1082) — it is revealed here exactly once (atomic
  // reveal+null, A13) and shown in the welcome card.
  async function provisionInApp(session) {
    setWelcomeProvisioning(true)
    setWelcomeProvisionError('')
    // #1082 double-provision guard (fail-closed, mirrors welcome.html's
      // claimStatusGuard): a tt_claim_pending marker means a claimable anon
      // team may exist — never mint a stray team over it.
      if (/(?:^|; )tt_claim_pending=/.test(document.cookie)) {
        // #1566 (code-review P2): the guard must NOT dead-end — offer the
        // claim card (the welcome.html 'Go claim my team' pattern).
        setWelcomeProvisionError(
          'You have an anonymous team waiting to be claimed — attach your ' +
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

  async function mintSessionKey(purpose, teamId) {
    const tok = sessionTokenRef.current
    if (!tok) throw new Error('No session')
    const mint = async (tid, purposeOverride) => {
      const res = await fetch(`${API_BASE}/v1/session/key`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${tok}` },
        body: JSON.stringify(tid ? { purpose: purposeOverride || purpose, team_id: tid } : { purpose: purposeOverride || purpose }),
      })
      return res
    }
    // #1566-fix: the bootstrap mint has a 3-ACTIVE cap (24h keys) — a user
    // who accumulated keys across incognito windows / retries is dead-ended
    // with 'Too many active session keys — wait for expiry' until expiry.
    // The RECOVERY mint is persistent + auto-revokes the oldest key at the
    // cap, so it is the escape hatch: fall back to it on the bootstrap cap.
    // Applied to BOTH the initial mint and the multi-membership 400-retry
    // (review P2); the parsed body is cached so the caller's !res.ok read
    // isn't double-consumed (review P2).
    const maybeRecoveryFallback = async (res, tid) => {
      if (purpose === 'bootstrap' && res.status === 429) {
        const body = await res.json().catch(() => null)
        res._parsedBody = body || {}
        if (body && typeof body.detail === 'string' && /active session keys/i.test(body.detail)) {
          return await mint(tid || null, 'recovery')
        }
      }
      return res
    }
    let res = await maybeRecoveryFallback(await mint(teamId), teamId)
    let mintedTeamId = teamId
    if (res.status === 400 && !teamId) {
      // Multi-membership: server demands a team_id — auto-select the first
      // team and retry (P1 fallback; never degrade to the key screen).
      const teamsRes = await fetch(`${API_BASE}/v1/teams`, {
        headers: { Authorization: `Bearer ${tok}` },
      })
      if (teamsRes.ok) {
        const list = await teamsRes.json()
        // Round-9: SIGNED_OUT (cross-tab broadcast / expiry) during the teams
        // fetch must not resurrect teams or mint with a revoked JWT.
        if (sessionTokenRef.current !== tok) throw new Error('No session')
        if (list.length) {
          setTeams(list)
          // #1912: a suspended membership must not be auto-selected for the
          // mint retry — pick the first healthy team (the all-suspended case
          // 403s the list server-side, so a selectable team always exists
          // here; fall back to list[0] defensively).
          mintedTeamId = (list.find((t) => !t.suspended_at) || list[0]).team_id
          res = await maybeRecoveryFallback(await mint(mintedTeamId), mintedTeamId)
        }
      }
    }
    if (!res.ok) {
      const b = res._parsedBody || (await res.json().catch(() => ({})))
      // #308: a suspended team's mint 403s with a dict detail — carry it so
      // the load path renders the suspension banner (primary load path).
      const sus = suspendedFromDetail(b.detail)
      const err = new Error(sus ? (sus.message || 'Team suspended') : (typeof b.detail === 'string' ? b.detail : `HTTP ${res.status}`))
      if (sus) err.suspended = sus
      // #1719 (Task 6, code-review P1): carry the status so the mint catch
      // renders UNAVAILABLE_COPY for 5xx (a raw "Internal server error"
      // string reads like a client bug on the primary fresh-user path).
      err.status = res.status
      throw err
    }
    const data = await res.json()
    if (!data.key) throw new Error('Session mint returned no key')
    // #1828 (review P3-2): the recovery fallback rotated a session
    // credential to make room — one-time banner so the user knows their
    // setup command key changed (the returned key IS the new one).
    // #1854: the banner names the rotated key via rotated_key_prefix
    // (falls back to a generic message if the prefix is missing).
    if (data.rotated) setBanner(data.rotated_key_prefix
      ? `A recovery key was rotated to make room — agents using the old key (prefix ${data.rotated_key_prefix}) must be re-connected; your setup command now uses a new key.`
      : 'A recovery key was rotated to make room — agents using the old key must be re-connected; your setup command now uses a new key.')
    // Fix C (review round 2): return the team actually minted for so callers
    // cache on the right id even when the 400-fallback picked it (a null
    // firstTeamId previously skipped the cache → cap burn on every switch).
    return { key: data.key, teamId: mintedTeamId }
  }

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
          window.history.replaceState({}, '', window.location.pathname)
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
              setBanner('Welcome to the team! Your membership is active.')
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
          } else if (_evt === 'SIGNED_OUT') { sessionTokenRef.current = null; setTeams([]) }
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
        try {
          const teamsRes = await fetch(`${API_BASE}/v1/teams`, {
            headers: { Authorization: `Bearer ${session.access_token}` },
          })
          if (teamsRes.ok) {
            teamsList = await teamsRes.json()
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
            throw new Error('Could not load your teams — try again.')
          }
        } catch (e) {
          // #1566 (review P2): fail CLOSED for any non-array/empty result —
          // the throw's premise is 'not a valid teams array'.
          if (!Array.isArray(teamsList) || !teamsList.length) {
            setAuthed(false)
            setMountError((e && e.message) || 'Could not load your teams — try again.')
            setChecking(false)
            return
          }
        }

        // #1566: a first-timer (valid session, NO teams) is provisioned
        // IN-APP — the team + membership + key are created here (the
        // tenant-provision edge function authorizes the app origin now) and
        // the key is revealed in the welcome card. Skip the bootstrap mint
        // (it 403s 'No team membership' for teamless users).
        if (!teamsList.length) {
          if (sessionTokenRef.current === session.access_token) {
            // The welcome card must render: leave the checking state + mark
            // authed (the normal completeLogin path never runs for first-timers).
            setChecking(false)
            setAuthed(true)
            setWelcomeMode(true)
            const provisioned = await provisionInApp(session)
            if (provisioned && provisioned.routedAway) {
              // claim in flight / claimable anon team — the claim flow owns it
              setWelcomeProvisioning(false)
            } else if (provisioned) {
              setWelcomeKey(provisioned.api_key)
              setWelcomeTeamName(provisioned.team_name)
              setWelcomeGraphName(provisioned.graph_name || '')
              // #1906: persist the shown-once key NOW — a reload (even
              // mid-wizard, before any dashboard exit) must keep it; the
              // mint path would otherwise mint a DIFFERENT bootstrap key
              // and the revealed key is gone forever (atomic reveal+null,
              // A13 — the server nulls its only plaintext copy on reveal,
              // so the client must persist at reveal time or the shown-once
              // key is unrecoverable). Guard truthy: the
              // consumed-reveal path returns api_key '' (already revealed
              // elsewhere), and a falsy value must never land in
              // localStorage (mint-path convention, #1830). try/catch:
              // private-mode storage throws.
              if (provisioned.api_key) {
                try { localStorage.setItem(KEY_STORAGE, provisioned.api_key) } catch { /* best-effort */ }
                setApiKey(provisioned.api_key)
                apiKeyRef.current = provisioned.api_key
              }
              // #1660: prefill the seed step — the Subject from the OAuth
              // identity (name or email prefix), the Project from the team.
              {
                const m = (session.user && session.user.user_metadata) || {}
                setWizardSubject(m.display_name || (session.user && session.user.email ? session.user.email.split('@')[0] : '') || 'me')
                setWizardProject(provisioned.team_name || 'my-project')
              }
              setWelcomeProvisioning(false)
              // #1623: the welcome plan step needs the team row
              // (checkout_price_ids for per-tier Upgrade CTAs) — the welcome
              // card never fetched /v1/team before. Best-effort: a failure
              // falls back to the "See pricing" link on the plan cards.
              // #1860 (P3-2): first-timers never called loadAlerts (only
              // completeLogin did, and the provisioned branch returns before
              // it) — the security-alerts section stayed empty until reload.
              // The provisioned team_id comes from the refreshTeam response
              // (the team row just created). No double-fire: completeLogin
              // never runs on this path.
              refreshTeam(provisioned.api_key, undefined, teamRefreshSeqRef.current)
                .then((t) => {
                  if (t && t.team_id) {
                    // #1906 (code-review P1): record the revealed key in the
                    // per-team cache — switchTeam/revokeKey consult
                    // teamKeysRef; without the entry, a switch away-and-back
                    // mints a DIFFERENT bootstrap key (replacing the
                    // shown-once key) and revoking the active key skips the
                    // localStorage re-mint branch (stale-key 401 on reload).
                    if (provisioned.api_key) teamKeysRef.current[t.team_id] = provisioned.api_key
                    loadAlerts(t.team_id)
                  }
                })
                .catch(() => {})
            } else {
              setWelcomeProvisionError('Could not create your team — try again.')
              setWelcomeProvisioning(false)
            }
          }
          return
        }

        // Reuse a stored key when it still belongs to one of the user's teams
        // (avoids burning the 3-active bootstrap cap on every reload), else
        // mint a bootstrap key for the first membership.
        let key = null
        const storedKey = localStorage.getItem(KEY_STORAGE)
        // #1567 (review P1): the chrome renders NOW, so a team switch made
        // during this fetch (the switcher is populated by the teams call
        // above) must not be clobbered — capture the selection and skip the
        // writes if it moved.
        const teamAtStoredKeyCheck = teamIdRef.current
        if (storedKey && teamsList.length) {
          try {
            const tRes = await fetch(`${API_BASE}/v1/team`, {
              headers: { Authorization: `Bearer ${storedKey}` },
            })
            if (tRes.ok) {
              const t = await tRes.json()
              if (teamsList.some((x) => x.team_id === t.team_id)
                  && teamIdRef.current === teamAtStoredKeyCheck) {
                key = storedKey
                teamKeysRef.current[t.team_id] = storedKey
                setCurrentTeamId(t.team_id)
                teamIdRef.current = t.team_id // Round-9: sync — loadTeams must not clobber
              }
            }
          } catch { /* fall through to mint */ }
        }
        let mintedTeamId = null
        // #1567 (review P2): a switch made while the mint/loads are in
        // flight owns its own error path — the stale continuation must not
        // flip the live switched session to the error card.
        const teamAtMountMint = teamIdRef.current
        if (!key) {
          // #1912: skip suspended rows when auto-selecting — a suspended
          // membership must not bounce the fresh session to the appeal banner.
          const firstSelectable = teamsList.find((t) => !t.suspended_at) || teamsList[0]
          const firstTeamId = firstSelectable ? firstSelectable.team_id : null
          try {
            const minted = await mintSessionKey('bootstrap', firstTeamId)
            key = minted.key
            mintedTeamId = minted.teamId || null
            if (minted.teamId) {
              teamKeysRef.current[minted.teamId] = key
              // #1841: ALSO select the minted team in STATE (not just the
              // ref) — the currentTeamId effect (loadMembers/loadGraphs)
              // only fires on state changes, so a fresh-mint session (no
              // valid stored key) never loaded the Graphs/Users cards
              // (they sat on '—' forever). Selecting here fires those JWT
              // reads in parallel with completeLogin's /v1/team instead of
              // never — guarded by the same "selection still unset" check
              // as the ref pin below so a mid-mint switch keeps its pick.
              if (!teamIdRef.current) setCurrentTeamId(minted.teamId)
              // #1828 (review): pin the minted team BEFORE completeLogin so
              // session-driven reads (?team_id=) resolve the MINTED team, not
              // the first membership (multi-membership users). Only when the
              // selection is still unset — a mid-mint switch owns it (the
              // guard below bails on the mismatch).
              if (!teamIdRef.current) teamIdRef.current = minted.teamId
            }
          } catch (e) {
            // #308: a suspended team's mint 403s — show the appeal banner.
            if (e && e.suspended) setSuspended(e.suspended)
            // #1559: the mint failed — the dashboard has NO key-only
            // fallback anymore (deleted in #1511), so a silent
            // setChecking(false) stranded users on the fake "Redirecting to
            // the sign-in page…" shell. Surface an actionable error instead
            // (the Retry button re-runs the mount).
            const msg = (e && e.message) || 'Could not prepare your session.'
            if (teamIdRef.current !== teamAtMountMint) return  // switched mid-mint
            // #1719 (Task 6): a 5xx mint failure is the server's fault, not
            // the user's — render the honest unavailable copy (a raw
            // "Internal server error" string reads like a client bug).
            const errStatus = (e && e.status) || 0
            if (errStatus >= 500 || (e && e.suspended)) {
              // 5xx (server fault) or suspension: keep the blocking error
              // card + appeal banner (unchanged behavior).
              setMountError(errStatus >= 500 ? UNAVAILABLE_COPY : msg)
              setAuthed(false)  // #1567 P0: the error card renders in !authed
              setChecking(false)
              return
            }
            // #1830: a NON-5xx, NON-suspension mint failure (402 key limit /
            // 429 bootstrap cap / other 4xx) must NOT block the dashboard —
            // the overview reads ride the session JWT (#1828), so proceeding
            // to completeLogin(null) still renders Team/Keys/Sessions. Set a
            // one-time banner instead: agent connections need a key, but the
            // dashboard works without one.
            // #1831 P3-b: drop the stale stored key — it just failed its
            // /v1/team probe, and keeping it re-fires the probe (and its
            // 401) on every reload.
            try { localStorage.removeItem(KEY_STORAGE) } catch { /* best-effort */ }
            // #1831 P3-a: the remedy depends on the failure — a 402 key-limit
            // never clears on its own (recovery keys don't expire), so
            // "wait for expiry" is wrong advice there; keep it only for the
            // 429 rate-limit case.
            const mintRemedy = errStatus === 402
              ? 'Revoke an old key to make room.'
              : errStatus === 429
                ? 'Wait for expiry, then try again.'
                : 'Revoke an old key or wait for expiry.'
            setBanner(`Couldn't create an agent key: ${msg} — the dashboard works, but agent connections need a key. ${mintRemedy}`)
          }
        }
        // Round-9: a SIGNED_OUT during the mint must not complete the login
        // with a fresh key on the tab the user just signed out of.
        if (!sessionTokenRef.current) { setAuthed(false); setChecking(false); setMountError('Your session ended — sign in again.'); return }
        // #1830: key may be null here — a recoverable mint failure (4xx,
        // non-suspension) proceeds to completeLogin(null): the overview
        // reads ride the session JWT, so the dashboard still renders. The
        // old hard gate (`if (!key) … mountError`) blocked the WHOLE
        // dashboard on a mint that only matters for agent connections.
        // #1567 P1 (verifier gate): the chrome is visible NOW — a team
        // switch made during the mint (multi-membership: the mint-400
        // fallback populated the switcher early) must not be clobbered by
        // this continuation. Bail before any state write if the selection
        // moved away from the key's owner (teamIdRef is null on a fresh
        // session — the stored-key path and Round-8 loadTeams own it then).
        if (mintedTeamId && teamIdRef.current && teamIdRef.current !== mintedTeamId) return
        // #1830: only persist a REAL key — a recoverable mint failure leaves
        // key null, and writing it would clobber the stored credential (a
        // falsy value must never land in localStorage). A null key also
        // clears the apiKey state so snippets never leak a stale/invalid
        // key (or "Bearer null").
        if (key) localStorage.setItem(KEY_STORAGE, key)
        setApiKey(key || '')
        apiKeyRef.current = key || ''
        setAuthMode('session')
        await completeLogin(key)
      } catch (e) {
        // #1559/#1567 (review P0): never leave the user on the silent
        // redirect shell — authed was set EARLY, so an escaped throw (e.g.
        // localStorage blocked in private mode after a successful mint)
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
    // the team from the API key.
    // #1828 (review): the Team card now rides the SESSION JWT (dual-auth
    // get_current_team_session) when signed in — pinned ?team_id= so
    // multi-membership users resolve the SELECTED team, not the first
    // membership; the api() helper's useSession only wins when a session JWT
    // is present, so the key stays the fallback for key-only callers
    // (stored-key reuse / switchTeam with a fresh mint).
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
    // the currentTeamId effect, fired at mount by the stored-key path and
    // now by the mint path too.)
    const cardLoads = Promise.all([
      loadAll(key),
      loadTeams(),
      loadBackups(key),
    ]).catch(() => {})
    try {
      // #1828 (review): session-driven Team card — pinned ?team_id= so the
      // session resolves the MINTED/stored-key team, not the first
      // membership (multi-membership users); the key remains the fallback
      // when no session JWT exists (api() useSession only wins with one).
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
      // #1559 (review P2): a /v1/team or load 5xx after a successful mint
      // must NOT leave the silent redirect shell — same class as the mint
      // failure. The error card (mountError) is the only renderable state.
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
      // reload so it picks up the fresh session and mints the bootstrap key.
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
    // state mirrored into the ref) — what's on screen is what's used. A
    // pre-filled stored key is visible and deliberate; a wrong-team key
    // 403s with the claim error. No hidden localStorage fallback.
    const k = (apiKeyRef.current || '').trim()
    if (!k.startsWith('tt_')) {
      setClaimError('Paste your tt_ API key above, then connect a login to claim your team.')
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
    // #1082: clear the claim intent on logout (a stale pasted key must not
    // auto-claim the next user's session).
    setClaimKey('')
    setClaimError('')
    try { sessionStorage.removeItem(CLAIM_KEY_STORAGE) } catch { /* best-effort */ }
    clearClaimPendingMarker()
    setGraphs([])
    setGraphsLoaded(false) // Round-27: symmetry with switchTeam
    setMembers(null)
    setMembersStatus('loading')
    setCurrentTeamId(null)
    setCurrentGraphId(null)
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
    fallbackTeamIdRef.current = null    // Round-5: no stale team adoption across users
    teamIdRef.current = null            // Round-5: hygiene (inert, but consistent)
    setCheckoutPending(false)           // Round-6: no stuck 'Opening checkout…' for the next user
    setInviteEmail('')                  // Round-9: no half-typed invite from the previous user
    setInviteRole('member')
    setNewGraphName('')

    // Round-7: onAuthStateChange returns {data:{subscription}} with .unsubscribe() —
    // client.auth.removeChannel doesn't exist on GoTrueClient (was a silent no-op).
    if (authSubRef.current) { authSubRef.current.unsubscribe?.(); authSubRef.current = null }
    teamKeysRef.current = {}
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
    // switcher (session resolution defaults to the first membership); the
    // key param is accepted for backwards-compatible callers but unused.
    const q = _teamAtCall ? `?team_id=${encodeURIComponent(_teamAtCall)}` : ''
    try {
      const [k, s] = await Promise.all([
        api(`/v1/team/keys${q}`, { useSession: true }),
        api(`/v1/sessions${q}`, { useSession: true }),
      ])
      if (teamIdRef.current !== _teamAtCall) return // stale switch response — don't land B's keys under C
      setKeys(Array.isArray(k) ? k : k.keys || [])
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
        // the stored-key bootstrap path already set it for the key's team, so
        // a multi-team reload must NOT clobber it with the first team.
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
    if (!name) { setCreateTeamError('Team name required'); return }
    if (name.length > 64 || !/^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$/.test(name)) {
      setCreateTeamError('Invalid team name — letters, numbers, dash, underscore only')
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
      setCreateTeamOpen(false)
      setCreateTeamName('')
      await loadTeams()
      if (res?.team_id) switchTeam(res.team_id)
    } catch (e) {
      if (e?.status === 402) {
        setCreateTeamError(e.message || 'Create another team requires a paid plan')
        setCreateTeamUpgrade(true)
      } else {
        setCreateTeamError(e?.message || 'Could not create the team')
      }
    } finally {
      setCreateTeamBusy(false)
    }
  }

  async function switchTeam(teamId) {
    // P3 (code-review): reset stale team-scoped state at the top so a rapid
    // switch never flashes the previous team's members/graphs, and record the
    // requested team as current for staleness guards.
    setCapNotice('') // #1147: a cap banner from the previous team must not stick
    const prevTeamId = currentTeamId
    const prevKey = apiKey
    const tok = sessionTokenRef.current
    if (!tok) return // Round-3: guard BEFORE wiping state — logout→apikey
                     // login must not blank the dashboard on a stale pick
    setMembers(null)
    setMembersStatus('loading')
    setGraphs([])
    setGraphsLoaded(false) // Round-27: per-team loaded flag — no '0' flash on switch
    setGraphsStatus('loading') // #1842 P2-1: mirror the membersStatus reset
    setCurrentGraphId(null)
    setTeam(null)          // Fix B: clear key-scoped overview state too
    setStaleFired(false)   // #1858: reset the per-load stale latch on EVERY switch — incl. null→null from the terminal '—' state, where the reset effect's team dep doesn't fire
    setKeys([])
    setSessions([])
    setBackupInfo(null)
    setBackupsStatus('loading') // #1923: mirror the backupInfo reset
    setNewKey(null)        // Round-16: the plaintext key card was shown once on the old team
    setNewKeyName('')      // key-label: a typed label must not leak onto another team's mint
    setEditingKeyId(null)  // key-label: close any in-flight inline rename across teams
    renameCancelRef.current = true // key-label: the unmount-blur must not fire a rename for the old team
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
      // P1/P2 (code-review): overview cards + header tier badge read the
      // API-key's team — mint (or reuse) a data-plane key for the selected
      // team so every team-dependent surface tracks the switcher. The cache
      // keeps us under the 3-active bootstrap mint cap across switches.
      let key = teamKeysRef.current[teamId]
      if (!key) {
        const minted = await mintSessionKey('bootstrap', teamId)
        key = minted.key
        teamKeysRef.current[teamId] = key
      }
      if (teamIdRef.current !== teamId) return // stale — user switched again
      localStorage.setItem(KEY_STORAGE, key)
      setApiKey(key)
      apiKeyRef.current = key
      setAuthMode('session') // Round-9: a session-minted key IS session auth — no more
                             // 'sign in required' notices beside live session data
      try {
        await refreshTeam(key)
      } catch (e) {
        // Round-10/11: a cached ephemeral key may have expired (24h) or been
        // revoked — drop it and re-mint once before falling back to revert.
        // Trigger on e.status (api() attaches it) — the message is a detail
        // string like 'Invalid API key', never '401'.
        if (teamIdRef.current === teamId && e?.status === 401) {
          delete teamKeysRef.current[teamId]
          const minted = await mintSessionKey('bootstrap', teamId)
          key = minted.key
          teamKeysRef.current[teamId] = key
          if (teamIdRef.current !== teamId) return
          localStorage.setItem(KEY_STORAGE, key)
          setApiKey(key)
          apiKeyRef.current = key
          await refreshTeam(key)
        } else {
          throw e
        }
      }
      if (teamIdRef.current !== teamId) return
      await Promise.all([loadAll(key), loadBackups(key)])
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
        // Fix B: mint/refresh failed (429 cap or 401) — re-attach the
        // previous team's key so the UI never shows mixed-team data.
        if (prevKey) {
          // Round-22 (P2): restore the key that BELONGS to the reverted team —
          // under rapid A→B→C with B's mint succeeding and C's failing, prevKey
          // is team A's key (captured from the render closure) while prevTeamId
          // is B; re-attaching A's key under B's header mixed teams.
          const restoreKey = (prevTeamId && teamKeysRef.current[prevTeamId]) || prevKey
          setApiKey(restoreKey)
          apiKeyRef.current = restoreKey
          localStorage.setItem(KEY_STORAGE, restoreKey)
          setCurrentTeamId(prevTeamId)
          teamIdRef.current = prevTeamId
          setTeam(null)
          setStaleFired(false) // #1858: the revert re-attaches the previous team — clear the latch so the restored team's skeleton gets a fresh floor (team is already null here, so the reset effect won't fire)
          // #1893 (code-review P1): the revert re-attaches the PREVIOUS
          // team — its onboarding is the pre-switch object (still current),
          // so clear the switch-stale flag and let hydration re-run for the
          // restored team (the latches/repos were reset at switch top).
          onboardingStaleRef.current = false
          await refreshTeam(restoreKey).catch(() => {})
          // Round-3: reload ALL key-scoped data for the reverted team —
          // otherwise keys/sessions/backups stay wiped until reload.
          await Promise.all([loadAll(restoreKey), loadBackups(restoreKey)]).catch(() => {})
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
        // Fix E (review round 2): auto-select first graph so the dropdown
        // shows a selection after switch; Round-3: only when nothing is
        // selected yet (don't clobber a manual pick on re-load).
        setCurrentGraphId((prev) => prev ?? list[0]?.graph_id ?? null)
      }
    } catch {
      // #1842 P2-1: transport/parse failure → terminal 'error', never eternal shimmer
      if (teamIdRef.current === teamId) setGraphsStatus('error')
    }
  }

  async function createGraph() {
    const _teamAtCall = currentTeamId // Round-16: mutation identity guard — a switch mid-flight must not act on the previous team
    if (busy || !newGraphName.trim()) return
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
      if (!res.ok) {
        const b = await res.json().catch(() => ({}))
        if (res.status === 402) {
          setError('Graph limit reached for this tier — upgrade to add more graphs.')
          setBusy(false)
          return
        }
        throw new Error(b.detail || `HTTP ${res.status}`)
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
          setError(typeof b.detail === 'string' ? b.detail : 'Invites require the Pro or Team tier — upgrade to invite teammates.')
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
    if (!confirm('Remove this member from the team?')) return
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
    // P2 (code-review): /backups is scoped to the API-key's team — fetch with
    // the selected team's key so the Overview Backups card tracks the switcher.
    // #1842 P1-2: /backups is session-dual-auth (get_current_team_session_ungated)
    // but the key===null path (#1830 recoverable mint failure) sent NO auth →
    // 401 → backupInfo null → the Backups card shimmered forever. useSession
    // sends the JWT; the api() header merge keeps the key winning when present.
    try {
      const b = await api('/backups', { useSession: true, headers: key ? { Authorization: `Bearer ${key}` } : {} })
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
  // are JWT team-scoped; the key-scoped overview data (team/keys/sessions/
  // backups) is reloaded in switchTeam/completeLogin with the team's key.
  React.useEffect(() => {
    if (currentTeamId) {
      teamIdRef.current = currentTeamId
      // #1893 (code-review P1): the mount-time refreshOnboarding() fired
      // BEFORE the team resolved (unpinned — server resolves memberships[0],
      // which is the WRONG team for a multi-membership user whose stored-key
      // team is not the first membership). Re-fetch now that the team is
      // known, so onboarding (and the scope surface) track the selected
      // team. teamIdRef is set above, so the pinned GET targets the right
      // team. This also recovers a switch whose onboarding refetch failed
      // (the switchTeam catch swallowed it — the stale flag would otherwise
      // stay set and block hydration forever).
      refreshOnboarding().catch(() => {})
      loadMembers(currentTeamId)
      loadGraphs(currentTeamId)
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
    setCapNotice('')
    setError('')
    setBusy(true)
    try {
      // Round-15 (P2): resolve the ACTIVE team's key explicitly — during a
      // mid-switch window apiKey state is still the previous team's key, and
      // a key created with it lands on the wrong team.
      const _apiKeyAtCall = apiKey
      const activeKey = _teamAtCall ? (teamKeysRef.current[_teamAtCall] || _apiKeyAtCall) : _apiKeyAtCall
      const newKeyVal = await mintKey(activeKey, newKeyName.trim() || undefined)
      // Identity guard BEFORE any UI write: a team switch during the POST must
      // not render this team's plaintext key card or key table under the new
      // team's header (switchTeam's setNewKey(null) already ran for the new team).
      if (teamIdRef.current !== _teamAtCall) return
      // Round-19 (P3): the pre-mint guard must actually fire. activeKey is
      // either cache[t] or apiKey-at-call. If this team already has a cached
      // key, the POST must have used it. Otherwise (no cached key), the POST
      // key must still be the current apiKey state — if apiKey moved (a
      // switchTeam mint completed), we hit the OLD team's key: bail.
      const cachedForTeam = _teamAtCall ? teamKeysRef.current[_teamAtCall] : null
      // Round-21: branch 2 compares against the LIVE key (apiKeyRef) — the
      // render-closure apiKey was always equal to _apiKeyAtCall, making the
      // old guard dead code. Live-key comparison catches the post-sync
      // mid-switch window (cache[B] empty, apiKey already moved to A's key
      // from a prior switch).
      if (cachedForTeam ? activeKey !== cachedForTeam : activeKey !== apiKeyRef.current) return
      setNewKey(newKeyVal)
      setNewKeyName('')
      await loadAll(activeKey)
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
    // #1147: rotate = mint the REPLACEMENT first (the old key still
    // authorizes the request), then revoke the old — a single mint (no
    // bootstrap-pool growth), works in both auth modes, and the replacement
    // becomes the active key (shown once). Available on every tier:
    // regenerating does not grow the key count.
    if (busy) return
    if (!confirm('Regenerate this API key? A new key is created and the current one is revoked (shown once). Applications using the old key will stop working.')) return
    setCapNotice('')
    setError('')
    setBusy(true)
    try {
      const _teamAtCall = currentTeamId
      const activeKey = _teamAtCall ? (teamKeysRef.current[_teamAtCall] || apiKey) : apiKey
      const newKeyVal = await mintKey(activeKey)
      // Revoke the old key — skip its bootstrap re-mint (we already hold the
      // replacement; the re-mint exists only for revoke-without-replacement).
      await revokeKey(keyId, { skipConfirm: true, skipBootstrap: true })
      if (teamIdRef.current !== _teamAtCall) return
      // Install the replacement as the team's active data-plane key.
      setNewKey(newKeyVal)
      if (_teamAtCall) teamKeysRef.current[_teamAtCall] = newKeyVal
      if (localStorage.getItem(KEY_STORAGE) === apiKey) localStorage.setItem(KEY_STORAGE, newKeyVal)
      apiKeyRef.current = newKeyVal
      await loadAll(newKeyVal)
    } catch (e) {
      if (teamIdRef.current === currentTeamId) {
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

  async function toggleKeyEnabled(keyId, currentEnabled) {
    // #1148-ux review: enable/disable an API key. Server PATCH flips the
    // key's enabled flag (default true = new keys are on). Optimistic local
    // update; revert on error. A disabled key stops authenticating but stays
    // listed (re-enabled anytime).
    setCapNotice('')
    setError('')
    const next = !currentEnabled
    setKeys((prev) => prev.map((k) => k.id === keyId ? { ...k, enabled: next } : k))
    try {
      const updated = await api(`/v1/team/keys/${keyId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        useSession: true,  // #1148: management → session JWT when signed in
        body: JSON.stringify({ enabled: next }),
      })
      if (updated && (updated.id || updated.key_id)) {
        setKeys((prev) => prev.map((k) => k.id === keyId ? { ...k, enabled: updated.enabled !== false } : k))
      }
    } catch (e) {
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
      const updated = await api(`/v1/team/keys/${keyId}`, {
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
    // Round-20 (P2): capture team at call — a mid-flight switch must not let
    // this revoke's re-mint clobber the new team's active key/localStorage or
    // land the old team's key table under the new header.
    const _teamAtCall = currentTeamId
    if (!opts.skipConfirm && !confirm('Revoke this API key? Applications using it will stop working.')) return
    setCapNotice('')
    setError('')
    try {
      await api(`/v1/team/keys/${keyId}`, { method: 'DELETE', useSession: true })
      // Round-20: bail after the DELETE — a switch already reloaded the new
      // team's state; skip the stale re-mint + loadAll entirely.
      if (teamIdRef.current !== _teamAtCall) return
      // Fix A (review round 2): if we revoked the active data-plane key, the
      // per-team cache + localStorage now hold a dead key — re-mint so the
      // app doesn't 401 on the next switch/reload.
      const cached = _teamAtCall ? teamKeysRef.current[_teamAtCall] : null
      if (cached && keyId === keyIdFromValue(cached) && !opts.skipBootstrap) {
        delete teamKeysRef.current[currentTeamId]
        if (localStorage.getItem(KEY_STORAGE) === cached) localStorage.removeItem(KEY_STORAGE)
        const tok = sessionTokenRef.current
        if (tok && _teamAtCall) {
          try {
            const minted = await mintSessionKey('bootstrap', _teamAtCall)
            // Round-21 (P2): a switch (or logout) landing DURING the mint
            // must not clobber the new team's active key/localStorage —
            // the entry guard passed before this await, so re-check now.
            if (teamIdRef.current !== _teamAtCall || !sessionTokenRef.current) return
            teamKeysRef.current[_teamAtCall] = minted.key
            localStorage.setItem(KEY_STORAGE, minted.key)
            setApiKey(minted.key)
            apiKeyRef.current = minted.key
            await refreshTeam(minted.key).catch(() => {})
            // Round-22 (P2): a switch during refreshTeam's await — loadAll would
            // capture teamIdRef (=new team) and land the OLD team's keys under it.
            if (teamIdRef.current !== _teamAtCall) return
            // Round-3: reload with the NEW key, not the revoked-key closure.
            await loadAll(minted.key).catch(() => {})
            return
          } catch { /* leave API-key screen; not fatal */ }
        }
      }
      if (teamIdRef.current !== _teamAtCall) return // Round-20: stale fallthrough
      await loadAll()
    } catch (e) {
      // Round-18/20: a stale revoke's error must not land under the new team
      if (!_teamAtCall || teamIdRef.current === _teamAtCall) setError(e.message)
    }
  }

  // ── J-2: key recovery via E1 session mint (the #518 fix) ──
  async function recoverKey() {
    // Round-18 (P2): the last mutation that writes the ACTIVE key + localStorage
    // — capture team at call time; bail before any write if the user switched.
    if (busy) return // Round-26: in-function double-click guard (button disabled is click-path only)
    const _teamAtCall = currentTeamId || fallbackTeamIdRef.current
    setError('')
    setBusy(true)
    const tok = sessionTokenRef.current
    if (!tok) { setError('Sign in with your Tortoise account to recover a key.'); setBusy(false); return }
    try {
      let res = await fetch(`${API_BASE}/v1/session/key`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${tok}` },
        // P1 (code-review): multi-membership users need a team_id — mint the
        // recovery key for the currently selected team.
        body: JSON.stringify({ purpose: 'recovery', ...(currentTeamId ? { team_id: currentTeamId } : {}) }),
      })
      // Round-3: 400 "team_id required" with no selected team (e.g. /v1/teams
      // failed during bootstrap) — auto-select first membership and retry.
      if (res.status === 400 && !currentTeamId) {
        const teamsRes = await fetch(`${API_BASE}/v1/teams`, {
          headers: { Authorization: `Bearer ${tok}` },
        })
        if (teamsRes.ok) {
          const list = await teamsRes.json()
          // Round-7: a logout during the teams fetch must not resurrect
          // teams/fallbackTeamIdRef for the signed-out page.
          if (sessionTokenRef.current !== tok) return
          if (list.length) {
            setTeams(list)
            // #1912: skip suspended rows when auto-selecting — a suspended
            // membership must not trigger the recovery mint (it 403s).
            const firstSelectable = list.find((t) => !t.suspended_at) || list[0]
            fallbackTeamIdRef.current = firstSelectable.team_id
            res = await fetch(`${API_BASE}/v1/session/key`, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${tok}` },
              body: JSON.stringify({ purpose: 'recovery', team_id: firstSelectable.team_id }),
            })
          }
        }
      }
      if (!res.ok) {
        const b = await res.json().catch(() => ({}))
        throw new Error(b.detail || `HTTP ${res.status}`)
      }
      const data = await res.json()
      // Round-6 (P3): a logout during the in-flight recovery must not resurrect
      // the team + key for the signed-out user.
      if (sessionTokenRef.current !== tok) return
      // Round-18 (P2): a team switch during the recovery must not make A's
      // recovery key the app's active key under team B's header.
      if (teamIdRef.current !== _teamAtCall) return
      setNewKey(data.key)
      // Round-4: if the 400-fallback auto-selected the first membership,
      // persist it so graphs/members load and the key is cached (otherwise
      // the effect never fires and every manual pick burns a fresh mint).
      const mintedTeamId = currentTeamId || fallbackTeamIdRef.current
      if (mintedTeamId) teamKeysRef.current[mintedTeamId] = data.key
      if (!currentTeamId && fallbackTeamIdRef.current) {
        setCurrentTeamId(fallbackTeamIdRef.current)
        teamIdRef.current = fallbackTeamIdRef.current
      }
      // Fix A (review round 2): adopt the recovery key so the UI keeps
      // working (the old bootstrap key may have just been rotated/revoked).
      localStorage.setItem(KEY_STORAGE, data.key)
      setApiKey(data.key)
      apiKeyRef.current = data.key
      setAuthMode('session') // Round-10: a recovery-minted key IS session auth — keep tabs consistent
      await Promise.all([loadAll(data.key), refreshTeam(data.key)]).catch(() => {})
    } catch (e) {
      // Round-18: a stale recovery's error must not land under the new team
      if (teamIdRef.current === _teamAtCall) setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  // #1708 D8: API-first session-key classification (created_via === 'bootstrap'
  // || expires_at) with the old active-key prefix guard retained ONLY as a
  // fallback when the API fields are absent (stale cached responses / registry
  // lane pre-#1709) so the live session key can never be revoked from the UI.
  function isSessionKey(k) {
    // #1708 fixer (P2): fall back to the apiKey state when this team has no
    // cached key — the live session key must never be revocable even when
    // teamKeysRef is empty (mirrors the `|| apiKey` pattern at mint/create).
    return isSessionKeyPredicate(k, currentTeamId ? (teamKeysRef.current[currentTeamId] || apiKey) : apiKey)
  }

  function keyIdFromValue(value) {
    if (!value) return null
    // GET /v1/team/keys returns {id, key_prefix, ...} — hashes only, no
    // plaintext. Auth pre-filters on token[:10], so match on key_prefix.
    const prefix = String(value).slice(0, 10)
    for (const k of keys || []) {
      if (k.key_prefix === prefix) return k.id || k.key_id
    }
    return null
  }

  // #714 (main): session detail view
  async function fetchSessionDetail(sessionId) {
    setDetailLoading(true)
    setError('')
    try {
      const detail = await api(`/v1/sessions/${sessionId}`)
      setSessionDetail(detail)
    } catch (e) {
      setError(e.message)
      setSessionDetail(null)
    } finally {
      setDetailLoading(false)
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
      // #1559: a mount failure (mint 429/5xx, auth lib blocked) renders a
      // REAL error card with a retry — never the silent "Redirecting…"
      // shell (which only ever accompanied an ACTUAL navigation).
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
            <h2 className="protect-banner-title">🔑 Claim your team</h2>
            <p>
              Paste the key for your unclaimed team, then attach a login to
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
  // #1831 P2-1: the wizard's setup commands embed the user's key. After a
  // recoverable mint failure (#1830) BOTH welcomeKey and apiKey can be empty
  // — never emit `Bearer ` with an empty key; fall back to a create-a-key
  // message instead (see the wizard step-0 render below).
  const harnessKey = welcomeKey || apiKey || ''

  if (welcomeMode && authed) {
    // #1566: first-timers are provisioned IN-APP — show the provisioning
    // spinner, then the revealed key (exactly once, A13), or an actionable
    // error. Returning users (welcomeKey empty) get the ready card.
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
            disabled={welcomeProvisioning || welcomeProvisionError}
            onClick={() => { window.history.replaceState({}, '', '/'); setWelcomeMode(false); finishWelcomeLoads() }}
          >
            Open my dashboard →
          </button>
        </header>
        <main>
          <div className="welcome-card" style={{ maxWidth: 560, margin: '0 auto', padding: '1rem 0' }}>
            {welcomeProvisioning ? (
              <>
                <h1 style={{ fontFamily: 'var(--serif, Georgia, serif)', fontWeight: 400, marginBottom: '0.5rem' }}>
                  Provisioning your Tortoise…
                </h1>
                <p className="dim">Creating your team and API key — one moment.</p>
              </>
            ) : welcomeProvisionError ? (
              <>
                <h1 style={{ fontFamily: 'var(--serif, Georgia, serif)', fontWeight: 400, marginBottom: '0.5rem' }}>
                  We couldn't finish setting up your team
                </h1>
                <p className="error" role="alert">{welcomeProvisionError}</p>
                <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
                  {/anonymous team waiting/.test(welcomeProvisionError) ? (
                    // #1566 (code-review P2): the claim-guard must not
                    // dead-end — the claim card is the escape.
                    <a className="btn-primary" href="https://app.premiselabs.co/?claim=1">Go claim my team →</a>
                  ) : (
                    <button className="btn-primary" onClick={() => window.location.reload()}>Try again</button>
                  )}
                  <p className="dim">Still stuck? Contact <a href="mailto:hello@premiselabs.co">hello@premiselabs.co</a>.</p>
                </div>
              </>
            ) : (
              <>
                <h1 style={{ fontFamily: 'var(--serif, Georgia, serif)', fontWeight: 400, marginBottom: '0.5rem' }}>
                  {welcomeKey ? 'Your Tortoise is ready!' : 'Welcome to Tortoise'}
                </h1>
                {welcomeKey ? (
                  <>
                    <p className="dim" style={{ marginBottom: '1rem' }}>
                      This is the only time this API key is shown — copy it now. The raw key
                      never leaves this page ({welcomeTeamName ? `team: ${welcomeTeamName}` : ''}).
                    </p>
                    <div style={{ display: 'flex', gap: '0.5rem', marginBottom: '1.25rem' }}>
                      <code style={{ flex: 1, padding: '0.6rem 0.8rem', background: 'var(--surface,#0d1a2d)', border: '1px solid var(--border,#1e293b)', borderRadius: 8, overflowWrap: 'anywhere', fontSize: 13 }}>
                        {welcomeKey}
                      </code>
                      <button
                        className="btn-primary"
                        onClick={() => { try { navigator.clipboard.writeText(welcomeKey) } catch { /* clipboard blocked */ } }}
                      >
                        Copy
                      </button>
                    </div>
                  </>
                ) : (
                  <p className="dim" style={{ marginBottom: '1.25rem' }}>
                    Your team is set up. You can manage your API key in the dashboard anytime.
                  </p>
                )}
                {!welcomeOriented && (
                  <div className="wizard-orient" style={{ marginBottom: '1.5rem' }}>
                    <h2 style={{ fontFamily: 'var(--serif, Georgia, serif)', fontWeight: 400, fontSize: 20, marginBottom: '0.4rem' }}>
                      What you're setting up
                    </h2>
                    <p className="dim" style={{ marginBottom: '0.9rem' }}>
                      Tortoise gives your agent a memory — a knowledge graph of what it
                      learns. Here's what we'll set up:
                    </p>
                    <ol className="wizard-intro" style={{ margin: '0 0 1rem 1.1rem', padding: 0, lineHeight: 1.7 }}>
                      <li><strong>Install the tools</strong> — an MCP server so your agent can reach Tortoise, plus the skills so it knows how to use them.</li>
                      <li><strong>Connect your data</strong> — your first sources now; you can always add more by telling your agent.</li>
                      <li><strong>Add you + your project</strong> — the first entities on your graph.</li>
                      <li><strong>Start using it</strong> — ask your agent to record decisions; the graph does the rest.</li>
                    </ol>
                    <button className="btn-primary" onClick={() => setWelcomeOriented(true)}>Continue →</button>
                  </div>
                )}
                {welcomeOriented && (
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
                      {!harnessKey ? (
                        // #1831 P2-1: no key after a recoverable mint
                        // failure (#1830) — never emit `Bearer ` with an
                        // empty key. Fall back to a create-a-key message.
                        <>
                          <p className="dim" style={{ margin: '0.9rem 0 0', lineHeight: 1.6 }}>
                            The setup command embeds your API key — create one
                            first on the API Keys tab, then come back here to
                            finish setup.
                          </p>
                          <div className="wizard-nav">
                            <button type="button" className="ghost" onClick={() => setWelcomeOriented(false)}>← Back</button>
                            <div className="wizard-nav-actions">
                              <button type="button" className="ghost" onClick={() => { window.history.replaceState({}, '', '/'); setWelcomeMode(false); setTab('keys'); finishWelcomeLoads() }}>Go to API Keys →</button>
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
                                      onClick={() => { window.clearTimeout(checkoutResetTimerRef.current); setCheckoutPending(false); window.history.replaceState({}, '', '/'); setWelcomeMode(false); setTab('keys'); finishWelcomeLoads() }}
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
      <header>
        <div className="logo">Tortoise</div>
        <nav>
          <button className={tab === 'overview' ? 'active' : ''} onClick={() => { setTab('overview'); setSelectedSessionId(null); setSessionDetail(null); }}>Overview</button>
          <button className={tab === 'keys' ? 'active' : ''} data-tab="keys" onClick={() => { setTab('keys'); setSelectedSessionId(null); setSessionDetail(null); }}>API Keys</button>
          <button className={tab === 'graphs' ? 'active' : ''} onClick={() => setTab('graphs')}>Graphs</button>
          <button className={tab === 'members' ? 'active' : ''} onClick={() => setTab('members')}>Members</button>
          {/* #1623: Billing — plan, usage, upgrade/portal. Session-gated like
              the rest of the dashboard (anon teams get the Protect screen). */}
          <button className={tab === 'billing' ? 'active' : ''} onClick={() => setTab('billing')}>Billing</button>
        </nav>
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
            onClick={() => {
              const opening = !accountMenuOpen
              setAccountMenuOpen(opening)
              if (opening) loadPendingInvites()  // #1875: refresh on open
            }}
            onKeyDown={(e) => { if (e.key === 'Escape') setAccountMenuOpen(false) }}
            aria-expanded={accountMenuOpen}
            aria-label={`Account menu — ${currentTeamName || 'No team'}`}
          >
            <span className="account-avatar" aria-hidden="true">
              {(currentTeamName || 'T').charAt(0).toUpperCase()}
            </span>
            <span className="account-name">{currentTeamName || 'No team'}</span>
            <span className="account-chevron" aria-hidden="true">▾</span>
          </button>
          <ReauthDialog
        open={reauthOpen}
        busy={reauthBusy}
        error={reauthError}
        providers={(identityInv && identityInv.methods || []).map((x) => x.provider)}
        passwordMode={reauthPasswordMode}
        onClose={() => { setReauthOpen(false); pendingReauthRef.current = null; setReauthPasswordMode(false) }}
        onPassword={handleReauthPassword}
        onProvider={handleReauthProvider}
      />
      {accountMenuOpen && (
            /* P2-1 (a11y, cycle-2): drop role=menu/menuitem — the full APG
               menu pattern (arrow-key roving focus) isn't implemented, and a
               declared menu contract without arrow nav is worse than none.
               Disclosure pattern: labeled group + plain buttons (needs no
               arrow-key handling; Tab + Enter work natively). */
            <div className="account-menu" role="group" aria-label="Account actions">
              {/* #1874: identity block — the PERSON. Session: display_name →
                  email-prefix fallback (pattern main.jsx:1227). Team-name
                  fallback is DEFENSIVE — the menu never renders without a
                  session in the current architecture (no-session → /auth,
                  anon → Protect screen). */}
              <div className="account-identity" role="group" aria-label="Account identity">
                <span className="account-avatar" aria-hidden="true">
                  {(sessionMetaRef.current?.display_name ||
                    (sessionMetaRef.current?.email ? sessionMetaRef.current.email.split('@')[0] : '') ||
                    currentTeamName || 'T').charAt(0).toUpperCase()}
                </span>
                <div className="account-identity-text">
                  <span className="account-identity-name">
                    {sessionMetaRef.current?.display_name ||
                      (sessionMetaRef.current?.email ? sessionMetaRef.current.email.split('@')[0] : '') ||
                      currentTeamName || 'No team'}
                  </span>
                  {sessionMetaRef.current?.email && (
                    <span className="account-identity-email">{sessionMetaRef.current.email}</span>
                  )}
                </div>
                {team?.tier && <span className="tier-badge">{team.tier}</span>}
              </div>
              <div className="account-menu-divider" />
              <button className="account-menu-profile" onClick={() => { setTab('profile'); setAccountMenuOpen(false) }}>
                Profile
              </button>
              <div className="account-menu-divider" />
              {/* P3-4 (review): hide the switch section for single-team users
                  (anon key-login has no session → teams is empty; showing
                  "Switch team → No team" reads broken). */}
              {teams.length > 1 && (
                <>
                  <div className="account-menu-label">Switch team</div>
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
              {/* #1877: create-team entry — UNCONDITIONAL (it's the sole
                  entry for single-team users; the switch label above stays
                  hidden for teams.length ≤ 1). */}
              <button className="account-menu-create" onClick={() => { setCreateTeamOpen(true); setCreateTeamName(''); setCreateTeamError(''); setCreateTeamUpgrade(false); setAccountMenuOpen(false) }}>
                + Create new team
              </button>
              {/* #1875: invitee-side pending invites (Slack/GitHub/Notion
                  workspace-switcher precedent). Renders only when there are
                  pending invites; Accept lands on the team, Decline removes. */}
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
              <div className="account-menu-divider" />
              <button className="account-menu-logout" onClick={logout}>
                Log out
              </button>
            </div>
          )}
        </div>
        {/* #1877: create-team dialog — gated-on-click upgrade UX. The 402
            state explains "upgrade a team, then create" (the new team
            doesn't exist until the gate passes); the CTA lands on Billing
            (#1876's team selector). */}
        {createTeamOpen && (
          <div className="modal-backdrop" onClick={() => { if (!createTeamBusy) setCreateTeamOpen(false) }}>
            <div className="modal" role="dialog" aria-modal="true" aria-label="Create a new team"
                 onClick={(e) => e.stopPropagation()}
                 onKeyDown={(e) => { if (e.key === 'Escape' && !createTeamBusy) setCreateTeamOpen(false) }}>
              {createTeamUpgrade ? (
                <>
                  <h3>Create a new team</h3>
                  <p className="error" role="alert">{createTeamError}</p>
                  <p className="dim">The free plan includes one team. Upgrade an existing team to create more.</p>
                  <div className="row" style={{ marginTop: 12 }}>
                    <button className="btn-primary" onClick={() => { setCreateTeamOpen(false); setCreateTeamUpgrade(false); setTab('billing') }}>
                      Upgrade
                    </button>
                    <button className="ghost" onClick={() => { setCreateTeamOpen(false); setCreateTeamUpgrade(false) }}>Cancel</button>
                  </div>
                </>
              ) : (
                <>
                  <h3>Create a new team</h3>
                  <input
                    aria-label="Team name"
                    placeholder="Team name"
                    autoFocus
                    value={createTeamName}
                    onChange={(e) => setCreateTeamName(e.target.value)}
                    onKeyDown={(e) => { if (e.key === 'Enter' && !createTeamBusy) handleCreateTeam() }}
                  />
                  {createTeamError && <p className="error" role="alert">{createTeamError}</p>}
                  <div className="row" style={{ marginTop: 12 }}>
                    <button className="btn-primary" onClick={handleCreateTeam} disabled={createTeamBusy}>
                      {createTeamBusy ? 'Creating…' : 'Create team'}
                    </button>
                    <button className="ghost" onClick={() => setCreateTeamOpen(false)} disabled={createTeamBusy}>Cancel</button>
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
            ⚠️ {suspended.message || 'This team has been suspended due to unusual activity.'}
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
            <p className="dim small">Suspicious activity detected on this team. Revoke any key you don't recognize.</p>
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
          <section className="overview" aria-busy={!error && !frameStale}>
            <h2>Overview</h2>
            <div className="cards">
              {error || frameStale
                ? ['Data points', 'Graphs', 'Users', 'Backups', 'API Keys', 'Plan'].map((label) => (
                    <div className="card" key={label}>
                      <div className="card-val">—</div>
                      <div className="card-label">{label}</div>
                    </div>
                  ))
                : ['Data points', 'Graphs', 'Users', 'Backups', 'API Keys', 'Plan'].map((label) => (
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
            <h2>Your graph is ready for its first data point</h2>
            <p className="dim">
              Your team and API key are live. Finish the setup to connect your
              tool, learn the three skills, and seed your graph — it only takes a
              minute.
            </p>
            <div className="empty-actions">
              <button className="btn-primary" onClick={() => { setWizardStep(0); setWelcomeMode(true) }}>
                Continue setup →
              </button>
            </div>
          </section>
        )}
        {tab === 'overview' && team && !showReentryCard && team.graph_ready === false && (team.point_count ?? 0) === 0 && (
          // #1591 (UX design): a clear first-data card — plain copy, a
          // styled copyable snippet, and a single primary action.
          <section className="overview empty-state graph-missing">
            <h2>Your graph is ready for its first data point</h2>
            {apiKey ? (
              // #1831 P2-1: only show the copyable snippet when a real key
              // exists — after a recoverable mint failure (#1830) apiKey is
              // '' and the snippet would render `Bearer ` with an empty key
              // (and the "key is live" copy would be false).
              <>
                <p className="dim">
                  Your team and API key are live — the graph is created the moment
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
              <p className="dim">
                Your team is live — create an API key to connect your agent and
                add your first data point. Mint one on the API Keys tab, or try
                again above — the dashboard re-keys once a key can be minted.
              </p>
            )}
            <div className="empty-actions">
              <a className="btn-primary" href="https://tortoise.premiselabs.co/welcome" target="_blank" rel="noreferrer">
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
              {apiKey && (
                <span className="dim small">or run: <code>{`curl -X POST https://api.premiselabs.co/v1/points -H "Authorization: Bearer ${apiKey.slice(0, 12)}…" -H "Content-Type: application/json" -d '{"content":"hello graph","kind":"statement"}'`}</code></span>
              )}
            </div>
          </section>
        )}
        {tab === 'overview' && team && !showReentryCard && team.graph_ready !== false && (team.point_count ?? 0) > 0 && (
          <section className="overview">
            <h2>Overview</h2>
            <div className="cards">
              <div className="card"><div className="card-val">{team.point_count ?? 0}</div><div className="card-label">Data points</div></div>
              <div className="card"><div className="card-val">{graphsStatus === 'ok' ? graphs.length : (graphsStatus === 'loading' ? (frameStale ? '—' : <span className="skeleton" style={SKEL_VALUE} aria-hidden="true" />) : '—')}</div><div className="card-label">Graphs</div></div>
              <div className="card"><div className="card-val">{membersStatus === 'ok' ? members.length : (membersStatus === 'loading' ? (frameStale ? '—' : <span className="skeleton" style={SKEL_VALUE} aria-hidden="true" />) : '—')}</div><div className="card-label">Users</div></div>
              <div className="card"><div className="card-val">{backupsStatus === 'ok' ? (backupInfo.count || 'none') : (backupsStatus === 'loading' ? (frameStale ? '—' : <span className="skeleton" style={SKEL_VALUE} aria-hidden="true" />) : '—')}</div><div className="card-label">Backups</div></div>
              <div className="card"><div className="card-val">{keys.length}</div><div className="card-label">API Keys</div></div>
              <div className="card"><div className="card-val">{team.tier || 'free'}</div><div className="card-label">Plan{team.subscription_status ? ` · ${team.subscription_status}` : ''}</div></div>
            </div>
            {/* #1728 Slice 3 (Task 17): the "Memory sources" panel — ONE
                implementation shared with the wizard step-1. #1927: the re-ask
                variant was removed with the consent gate. */}
            <MemorySources
              state={onboarding}
              loading={onboardingLoading}
              wizardHarness={null}  // review P2-6: Overview never knows the user's harness — no spurious "current" highlight
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
              branchLists={branchLists}
              docsScope={docsScope}
              issuesScope={issuesScope}
              onDocsScopeChange={handleDocsScopeChange}
              onIssuesScopeChange={handleIssuesScopeChange}
              onLoadBranches={loadBranches}
            />
            {/* #1148-ux review: Team ID / Limits / billing-actions / quickstart
                removed — noise (the quickstart lives on the empty state; limits
                are not actionable here). */}
          </section>
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
            addBusy={profileBusy === 'oauth' || profileBusy === 'email'}
            addError={profileError}
            onResend={handleResend}
            resendBusy={profileBusy === 'resend'}
            onOpenReauth={() => setReauthOpen(true)}
          />
        )}
        {tab === 'keys' && (
          <section>
            {/* #1148-ux review: graph selector restyled — page-scoped context
                control, dark theme. */}
            <div className="page-graph-selector">
              <label htmlFor="page-graph-select">Graph</label>
              <select
                id="page-graph-select"
                value={currentGraphId || ''}
                onChange={(e) => e.target.value && setCurrentGraphId(e.target.value)}
              >
                {graphs.length === 0 && <option value="">No graph</option>}
                {graphs.map((g) => (
                  <option key={g.graph_id} value={g.graph_id}>{g.name}</option>
                ))}
              </select>
            </div>
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
              <div className="inline-form">
                <input
                  placeholder="Label (e.g. CI, staging)"
                  aria-label="New key label"
                  value={newKeyName}
                  maxLength={64}
                  onChange={(e) => setNewKeyName(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && createKey()}
                />
                <button onClick={createKey} disabled={busy}>+ New key</button>
              </div>
            </div>
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
            {newKey && (
              <div className="new-key">
                <strong>Your new key (shown once):</strong>
                <code className="key-value">{newKey}</code>
                <button className="ghost small" onClick={() => { navigator.clipboard.writeText(newKey); setNewKey(null) }}>Copy &amp; done</button>
              </div>
            )}
            <table>
              <thead><tr><th>Name</th><th>Prefix</th><th>Created</th><th>Status</th><th></th></tr></thead>
              <tbody>
                {keys.length === 0 && <tr><td colSpan="5" className="dim">No keys yet.</td></tr>}
                {keys.map((k) => (
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
                          {!k.revoked_at && !isSessionKey(k) && isOwnerAdmin && (
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
                    <td>{k.revoked_at ? <span className="revoked">revoked</span> : isSessionKey(k) ? <span className="live">ephemeral · session</span> : <span className="live">active</span>}</td>
                    <td>{!k.revoked_at && !isSessionKey(k) && !isActiveKey(k, teamKeysRef.current[currentTeamId] || apiKey) && isOwnerAdmin && (
                      <span className="key-actions">
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
                            (revokeKey already confirm()s) */}
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
          </section>
        )}

        {tab === 'graphs' && (
          <section>
            <div className="row">
              <h2>Graphs</h2>
              {authMode === 'session' ? (
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
              ) : (
                <span className="dim small">Sign in required</span>
              )}
            </div>
            <table>
              <thead><tr><th>Name</th><th>Kind</th><th>Graph ID</th></tr></thead>
              <tbody>
                {authMode === 'session' && graphs.length === 0 && <tr><td colSpan="3" className="dim">No graphs yet — create your first one above.</td></tr>}
                {graphs.map((g) => (
                  <tr key={g.graph_id}>
                    <td><code>{g.name}</code></td>
                    <td>{g.kind}</td>
                    <td><code>{g.graph_id}</code></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        )}

        {tab === 'members' && (
          <section>
            <div className="row">
              <h2>Team members</h2>
              {isOwnerAdmin && (
                <div className="inline-form">
                  <input
                    type="email"
                    placeholder="teammate@example.com"
                    aria-label="Teammate email"
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
              <p className="dim small">Invites require the Pro or Team tier — <a href="https://tortoise.premiselabs.co/product.html#pricing" target="_blank" rel="noreferrer">upgrade to add teammates</a>.</p>
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
              <h2>Billing — {currentTeamName || 'this team'}</h2>
              {/* #1876: per-tenant billing — in-section context selector
                  (reuses switchTeam; single-team users get the name only). */}
              {teams.length > 1 && (
                <select
                  className="billing-team-select"
                  aria-label="Billing team"
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
    reposList, reposLoaded, docsScope, issuesScope, branchLists,
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
                      <span className="dim small">No repos listed — the index will run org-wide.</span>
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
                    <span className="dim small">No repos listed — the index will run org-wide.</span>
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
              return (
                <div key={h} className={`harness-status status-${st}${wizardHarness && h === wizardHarness ? ' current' : ''}`}>
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
