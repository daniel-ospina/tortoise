import React from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
// #1623: plan display data (build-time import of product/pricing.json).
import { planOptions, STATUS_LABELS, TIER_LABELS } from './pricing.js'
import { HARNESS_CONTINUE_LABEL, HARNESS_COPY_LABEL, HARNESS_INSTALL, HARNESS_NAMES, HARNESS_ORDER, HARNESS_PERSIST, HARNESS_SKILLS, HARNESS_SKILLLESS, HARNESS_SKILLS_IN_PROMPT, HARNESS_STEPS } from './harnesses.js'

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
// Short TTL (1h — the OAuth round-trip is minutes); Secure + SameSite=Lax.
function setClaimPendingMarker() {
  try {
    const expires = new Date(Date.now() + 60 * 60 * 1000).toUTCString()
    document.cookie = `${CLAIM_PENDING_COOKIE}=1; Domain=.premiselabs.co; Path=/; SameSite=Lax; Secure; Expires=${expires}`
  } catch { /* best-effort */ }
}

function clearClaimPendingMarker() {
  try {
    document.cookie = `${CLAIM_PENDING_COOKIE}=; Domain=.premiselabs.co; Path=/; SameSite=Lax; Secure; Max-Age=0`
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

const supabaseStorage = {
  getItem(key) {
    try {
      const m = document.cookie.match(new RegExp('(?:^|; )' + key + '=([^;]*)'))
      return m ? decodeURIComponent(m[1]) : null
    } catch { return null }
  },
  setItem(key, value) {
    if (!value) { this.removeItem(key); return }
    const expires = new Date(Date.now() + 7 * 24 * 3600 * 1000).toUTCString()
    document.cookie = `${key}=${encodeURIComponent(value)}; Domain=${COOKIE_DOMAIN}; Path=/; SameSite=Lax; Secure; Expires=${expires}`
  },
  removeItem(key) {
    document.cookie = `${key}=; Domain=${COOKIE_DOMAIN}; Path=/; SameSite=Lax; Secure; Max-Age=0`
  },
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

function App() {
  // #1280 (P0, mirrored from fix/1280): banner state MUST live inside the
  // component — a module-top-level useState crashes the whole bundle.
  const [banner, setBanner] = React.useState('')
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
  const [wizardGithub, setWizardGithub] = React.useState({ connected: false, repos: null, busy: false })
  const wizardGithubPollRef = React.useRef(null)  // #1643 review P1: the status poll handle (hoisted so Cancel/unmount can stop it)
  const [wizardSeedDone, setWizardSeedDone] = React.useState(false)
  const [wizardSeeding, setWizardSeeding] = React.useState(false)
  const [wizardDone, setWizardDone] = React.useState(false)
  const [onboardingComplete, setOnboardingComplete] = React.useState(false)
  const [welcomeOriented, setWelcomeOriented] = React.useState(false)
  const [wizardSubject, setWizardSubject] = React.useState('')
  const [copiedStep, setCopiedStep] = React.useState('')
  function wizardCopyStep(text) {
    try { navigator.clipboard.writeText(text) } catch { /* clipboard blocked */ }
    setCopiedStep(text)
    setTimeout(() => setCopiedStep(''), 1600)
  }
  const [wizardProject, setWizardProject] = React.useState('')
  React.useEffect(() => () => { stopGithubPoll && stopGithubPoll() }, [])  // unmount cleanup

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
          if (b && b.detail) msg = typeof b.detail === 'string' ? b.detail : JSON.stringify(b.detail)
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
  const [currentGraphId, setCurrentGraphId] = React.useState(null)
  const [accountMenuOpen, setAccountMenuOpen] = React.useState(false) // #1148-ux: account blob dropdown
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
  const [newGraphName, setNewGraphName] = React.useState('')
  const teamIdRef = React.useRef(null)
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
    const res = await fetch(`${API_BASE}${path}`, {
      ...opts,
      headers: { ...authHeaders, ...(opts.headers || {}) },
    })
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
  // persisted server-side).
  React.useEffect(() => {
    let cancelled = false
    api('/v1/onboarding/state', { useSession: true })
      .then((st) => { if (!cancelled && st && st.onboarding && st.onboarding.onboarding_complete) setOnboardingComplete(true) })
      .catch(() => { /* best-effort */ })
    return () => { cancelled = true }
  }, [])

  // ── #1643 wizard actions ────────────────────────────────────────────────
  const wizardSteps = ['Connect your tool', 'Integrations', 'Your agent\'s toolkit', 'Seed your graph', 'You\'re set']

  function wizardCopy(text, label) {
    try { navigator.clipboard.writeText(text) } catch { /* clipboard blocked */ }
    setWizardCopied(label)
    if (label !== 'harness') {
      // #1691: the harness label is STICKY on purpose — the positive
      // 'I've set it up — Continue' affordance must persist after the user
      // copies and goes to paste/run it (the 1.6s flash timer would eat
      // it). It resets on harness-tab switch and on step change instead.
      setTimeout(() => setWizardCopied(''), 1600)
    }
    api('/v1/onboarding/state', { method: 'PATCH', useSession: true,
      body: JSON.stringify({ harness: wizardHarness, section: 'config' }) }).catch(() => {})
  }

  const stopGithubPoll = () => {
    if (wizardGithubPollRef.current) { clearInterval(wizardGithubPollRef.current); wizardGithubPollRef.current = null }
  }

  async function wizardConnectGithub() {
    setWizardGithub((g) => ({ ...g, busy: true }))
    try {
      const res = await api('/v1/onboarding/github/connect', { method: 'POST', useSession: true })
      const authUrl = (res && (res.auth_url || res.authorize_url)) || null
      if (!authUrl) { setWizardGithub((g) => ({ ...g, busy: false })); return }
      const win = window.open(authUrl, '_blank')
      if (!win) {
        // Popup blocked — no poll to run; reset immediately (review P1).
        setWizardGithub((g) => ({ ...g, busy: false }))
        setError('Popup blocked — allow popups for app.premiselabs.co and try again.')
        return
      }
      // Poll status until the OAuth round trip completes (the callback
      // redirects to welcome.html, not here). Bounded; the handle is a ref
      // so Cancel/unmount can stop it (no stacked intervals).
      stopGithubPoll()
      wizardGithubPollRef.current = setInterval(async () => {
        try {
          const st = await api('/v1/onboarding/github/status', { useSession: true })
          if (st && st.connected) {
            stopGithubPoll()
            setWizardGithub({ connected: true, repos: st.repos_count, busy: false })
            api('/v1/onboarding/state', { method: 'PATCH', useSession: true, body: JSON.stringify({ github_connected: true }) }).catch(() => {})
          }
        } catch { /* transient */ }
      }, 3000)
      setTimeout(() => { stopGithubPoll(); setWizardGithub((g) => ({ ...g, busy: false })) }, 120000)
    } catch (e) {
      stopGithubPoll()
      setWizardGithub((g) => ({ ...g, busy: false }))
      setError((e && e.message) || 'Could not start GitHub connect.')
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
      // #1691: reflect the subject in the account username (display_name)
      // — best-effort; the graph Subject is the source of truth.
      if (subj && subj.id && supabaseClient) {
        supabaseClient.auth.updateUser({ data: { display_name: subjectName } }).catch(() => {})
      }
      setWizardSeeding(false)
    } catch (e) {
      setWizardSeeding(false)
      setError((e && e.message) || 'Could not seed your graph — try again.')
    }
  }

  async function wizardComplete() {
    setWizardDone(true)
    api('/v1/onboarding/state', { method: 'PATCH', useSession: true,
      body: JSON.stringify({ onboarding_complete: true }) }).catch(() => {})
    window.history.replaceState({}, '', '/')
    setWelcomeMode(false)
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
        if (typeof window.bounceToAuth === 'function') window.bounceToAuth()
        else window.location.replace('https://tortoise.premiselabs.co/auth')
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
          mintedTeamId = list[0].team_id
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
      throw err
    }
    const data = await res.json()
    if (!data.key) throw new Error('Session mint returned no key')
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
                if (b && b.detail) inviteMsg = typeof b.detail === 'string' ? b.detail : JSON.stringify(b.detail)
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
            // #1224/#1566: OAuth state-expiry errors land as ?error=… on the
            // app origin now — preserve the SEARCH so /auth renders the
            // banner (never the hash: a live #access_token must not be
            // re-ingested by the destination).
            if (typeof window.bounceToAuth === 'function') window.bounceToAuth(window.location.search)
            else window.location.replace('https://tortoise.premiselabs.co/auth' + window.location.search)
            return
          }
          // Claim-intent: render the claim-paste screen (no session, no team).
          setAuthMode('apikey')
          setChecking(false); return
        }
        sessionTokenRef.current = session.access_token
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
                let claimMsg = `Claim failed (HTTP ${claimRes.status}).`
                try {
                  const b = await claimRes.json()
                  if (b && b.detail) claimMsg = typeof b.detail === 'string' ? b.detail : JSON.stringify(b.detail)
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
              refreshTeam(provisioned.api_key).catch(() => {})
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
          const firstTeamId = teamsList.length ? teamsList[0].team_id : null
          try {
            const minted = await mintSessionKey('bootstrap', firstTeamId)
            key = minted.key
            mintedTeamId = minted.teamId || null
            if (minted.teamId) teamKeysRef.current[minted.teamId] = key
          } catch (e) {
            // #308: a suspended team's mint 403s — show the appeal banner.
            if (e && e.suspended) setSuspended(e.suspended)
            // #1559: the mint failed (429 rate limit / 5xx) — the dashboard
            // has NO key-only fallback anymore (deleted in #1511), so a
            // silent setChecking(false) stranded users on the fake
            // "Redirecting to the sign-in page…" shell. Surface an
            // actionable error instead (the Retry button re-runs the mount).
            const msg = (e && e.message) || 'Could not prepare your session.'
            if (teamIdRef.current !== teamAtMountMint) return  // switched mid-mint
            setMountError(/429|rate limit/i.test(msg)
              ? 'Too many requests from this network — try again in a minute.'
              : msg)
            setAuthed(false)  // #1567 P0: the error card renders in !authed
            setChecking(false)
            return
          }
        }
        // Round-9: a SIGNED_OUT during the mint must not complete the login
        // with a fresh key on the tab the user just signed out of.
        if (!sessionTokenRef.current) { setAuthed(false); setChecking(false); setMountError('Your session ended — sign in again.'); return }
        if (!key) { setAuthed(false); setChecking(false); setMountError('Could not prepare your session — try again.'); return }
        // #1567 P1 (verifier gate): the chrome is visible NOW — a team
        // switch made during the mint (multi-membership: the mint-400
        // fallback populated the switcher early) must not be clobbered by
        // this continuation. Bail before any state write if the selection
        // moved away from the key's owner (teamIdRef is null on a fresh
        // session — the stored-key path and Round-8 loadTeams own it then).
        if (mintedTeamId && teamIdRef.current && teamIdRef.current !== mintedTeamId) return
        localStorage.setItem(KEY_STORAGE, key)
        setApiKey(key)
        apiKeyRef.current = key
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

  async function refreshTeam(key, expectedTeamId) {
    // P1 (code-review): extracted team refetch — the success-return poll loop
    // used an undefined `jl` (dead code); this is the real refetch.
    // P2 (code-review): key param so the refetch targets the selected team —
    // the overview cards + header tier badge read /v1/team, which resolves
    // the team from the API key.
    const t = await api('/v1/team', key ? { headers: { Authorization: `Bearer ${key}` } } : {})
    // Round-13/14 (P2): never land a team's data under a different team's
    // selection. Two guards:
    //  - expectedTeamId (checkout poll pin): null on Stripe-return loads
    //    (poll effect runs before bootstrap sets teamIdRef), so also...
    //  - response-identity: t.team_id vs teamIdRef.current — catches the
    //    null-pin case AND a stale closure key (poll captured team A's key,
    //    user switched to B → /v1/team with A's key returns A's data).
    if (expectedTeamId != null && teamIdRef.current !== expectedTeamId) return t
    if (t?.team_id && teamIdRef.current && t.team_id !== teamIdRef.current) return t
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
    try {
      const t = await api('/v1/team', key ? { headers: { Authorization: `Bearer ${key}` } } : {})
      // #1567 (review P1): the chrome renders early, so a team switch can
      // land DURING this await — never land team A's data under team B's
      // selection (the refreshTeam response-identity guard, applied here).
      if (t?.team_id && teamIdRef.current && t.team_id !== teamIdRef.current) return
      setTeam(t)
      setAuthed(true)
      loadAlerts(t?.team_id)  // fire-and-forget (#308 R7)
      await Promise.all([loadAll(key), loadTeams(), loadBackups(key)])
    } catch (e) {
      if (e && e.suspended) setSuspended(e.suspended)  // #308
      setError(e.message === 'Invalid API key' ? 'Invalid API key — check your key and try again.' : e.message)
      setAuthed(false)
      // #1559 (review P2): a /v1/team or load 5xx after a successful mint
      // must NOT leave the silent redirect shell — same class as the mint
      // failure. The error card (mountError) is the only renderable state.
      if (teamIdRef.current !== teamAtCompleteLogin) return  // switched mid-load
      setMountError((e && /429|rate limit/i.test(e.message))
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
            if (b && b.detail) msg = typeof b.detail === 'string' ? b.detail : JSON.stringify(b.detail)
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
        if (b && b.detail) msg = typeof b.detail === 'string' ? b.detail : JSON.stringify(b.detail)
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
    sessionTokenRef.current = null      // Round-4: never reuse the previous user's JWT
    setError('')                        // Round-4: stale error banner must not survive
    setBackupInfo(null)                 // Round-5: no cross-session backup data leak
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
                                          // under team C's header    // P2 (code-review): /v1/team/keys + /v1/sessions resolve the team from the
    // API key — fetch them with the SELECTED team's key so the overview cards
    // track the team switcher, not the bootstrap team.
    const h = key ? { headers: { Authorization: `Bearer ${key}` } } : {}
    try {
      const [k, s] = await Promise.all([api('/v1/team/keys', h), api('/v1/sessions', h)])
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
        if (list.length > 0 && !teamIdRef.current) {
          setCurrentTeamId(list[0].team_id)
          teamIdRef.current = list[0].team_id
        }
      }
    } catch { /* best-effort */ }
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
    setCurrentGraphId(null)
    setTeam(null)          // Fix B: clear key-scoped overview state too
    setKeys([])
    setSessions([])
    setBackupInfo(null)
    setNewKey(null)        // Round-16: the plaintext key card was shown once on the old team
    setNewKeyName('')      // key-label: a typed label must not leak onto another team's mint
    setEditingKeyId(null)  // key-label: close any in-flight inline rename across teams
    setError('')
    setCurrentTeamId(teamId)
    teamIdRef.current = teamId
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
      if (!res.ok) return
      const list = await res.json()
      if (teamIdRef.current === teamId) {
        setGraphs(list)
        setGraphsLoaded(true) // Round-26
        // Fix E (review round 2): auto-select first graph so the dropdown
        // shows a selection after switch; Round-3: only when nothing is
        // selected yet (don't clobber a manual pick on re-load).
        setCurrentGraphId((prev) => prev ?? list[0]?.graph_id ?? null)
      }
    } catch { /* best-effort */ }
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
          setError('Invites require the Team tier — upgrade to invite teammates.')
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
    try {
      const b = await api('/backups', key ? { headers: { Authorization: `Bearer ${key}` } } : {})
      if (teamIdRef.current !== _teamAtCall) return // stale switch response
      const list = b.backups || []
      setBackupInfo(list.length ? { latest: list[0], count: list.length } : { count: 0 })
    } catch { /* tier-gated (Pro) — leave null */ }
  }

  // Load team-scoped data whenever the active team changes. Members + graphs
  // are JWT team-scoped; the key-scoped overview data (team/keys/sessions/
  // backups) is reloaded in switchTeam/completeLogin with the team's key.
  React.useEffect(() => {
    if (currentTeamId) {
      teamIdRef.current = currentTeamId
      loadMembers(currentTeamId)
      loadGraphs(currentTeamId)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentTeamId])

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
      // key-label: carry the old key's label onto the replacement
      const label = (keys.find((k) => k.id === keyId) || {}).name || undefined
      const newKeyVal = await mintKey(activeKey, label)
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
        body: JSON.stringify({ enabled: cur.enabled !== false, name: next }),
      })
      if (updated && (updated.id || updated.key_id)) {
        setKeys((ks) => ks.map((k) => k.id === keyId ? { ...k, name: next } : k))
      }
    } catch (e) {
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
            fallbackTeamIdRef.current = list[0].team_id
            res = await fetch(`${API_BASE}/v1/session/key`, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${tok}` },
              body: JSON.stringify({ purpose: 'recovery', team_id: list[0].team_id }),
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

  // Fix A (review round 2): map a full key value to its list id (or prefix)
  // so revoke can tell whether the active data-plane key is being revoked.
  // Round-4 (P3): the dashboard mints ephemeral bootstrap keys (≤3/team,
  // 24h). They appear in /v1/team/keys with no created_via/expires_at — flag
  // the one matching our cached active key so it renders 'ephemeral · session'
  // and can't be revoked from the table (it IS the session the app runs on).
  function isSessionKey(k) {
    if (!k || k.revoked_at) return false
    const active = currentTeamId ? teamKeysRef.current[currentTeamId] : null
    return !!active && (k.key_prefix === String(active).slice(0, 10))
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

  if (welcomeMode && authed) {
    // #1566: first-timers are provisioned IN-APP — show the provisioning
    // spinner, then the revealed key (exactly once, A13), or an actionable
    // error. Returning users (welcomeKey empty) get the ready card.
    return (
      <div className="app">
        <header>
          <div className="logo">Tortoise</div>
          <nav />
          <button
            className="ghost small"
            onClick={() => { window.history.replaceState({}, '', '/'); setWelcomeMode(false) }}
          >
            Open dashboard →
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
                      : wizardStep === 1 ? 'Connect your data sources — GitHub issues come in as Events (optional, do it now or later).'
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
                      {HARNESS_STEPS[wizardHarness] && (
                        <ol className="harness-steps" style={{ margin: '0.9rem 0 0.25rem 1.1rem', padding: 0, lineHeight: 1.7 }}>
                          {HARNESS_STEPS[wizardHarness].map((s, i) => (
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
                      <pre className="snippet" style={{ marginTop: '0.75rem' }}>
                        {HARNESS_INSTALL[wizardHarness](apiKey)}
                        {HARNESS_SKILLS(wizardHarness)}
                        {welcomeKey && !HARNESS_SKILLLESS.includes(wizardHarness) && !HARNESS_SKILLS_IN_PROMPT.includes(wizardHarness) ? ('\n\n' + HARNESS_PERSIST(apiKey)) : ''}
                      </pre>
                      <div className="wizard-nav">
                        <button type="button" className="ghost" onClick={() => setWelcomeOriented(false)}>← Back</button>
                        <div className="wizard-nav-actions">
                          <button type="button" className={wizardCopied === 'harness' ? 'ghost' : 'btn-primary'}
                            onClick={() => wizardCopy(HARNESS_INSTALL[wizardHarness](apiKey) + HARNESS_SKILLS(wizardHarness) + (welcomeKey && !HARNESS_SKILLLESS.includes(wizardHarness) && !HARNESS_SKILLS_IN_PROMPT.includes(wizardHarness) ? ('\n\n' + HARNESS_PERSIST(apiKey)) : ''), 'harness')}>
                            {wizardCopied === 'harness' ? 'Copied ✓' : (HARNESS_COPY_LABEL[wizardHarness] || 'Copy setup')}
                          </button>
                          {wizardCopied === 'harness' && (
                            <button type="button" className="btn-primary" onClick={() => setWizardStep(1)}>{HARNESS_CONTINUE_LABEL[wizardHarness] || "I've set it up — Continue →"}</button>
                          )}
                          <button type="button" className="ghost" onClick={() => setWizardStep(1)}>Skip for now</button>
                        </div>
                      </div>
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
                    <div className="github-connect">
                      {wizardGithub.connected ? (
                        <p className="dim">GitHub connected — {wizardGithub.repos ?? ''} repos available to index (issues → Events).</p>
                      ) : (
                        <p className="dim">Connect GitHub to bring your issues in as Events on the graph. Uses your own token — stored encrypted, never shared.</p>
                      )}
                      <div className="wizard-nav">
                        <button type="button" className="ghost" onClick={() => setWizardStep(wizardStep - 1)}>← Back</button>
                        <div className="wizard-nav-actions">
                          {!wizardGithub.connected && wizardGithub.busy && (
                            <button type="button" className="ghost" onClick={() => { stopGithubPoll(); setWizardGithub((g) => ({ ...g, busy: false })) }}>Cancel</button>
                          )}
                          {!wizardGithub.connected && (
                            <button type="button" className="btn-primary" onClick={wizardConnectGithub} disabled={wizardGithub.busy}>
                              {wizardGithub.busy ? 'Connecting…' : 'Connect GitHub'}
                            </button>
                          )}
                          <button type="button" className="ghost" onClick={() => setWizardStep(2)}>
                            {wizardGithub.connected ? 'Next' : 'Skip →'}
                          </button>
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
                                    <button
                                      className="btn-primary"
                                      onClick={() => { window.clearTimeout(checkoutResetTimerRef.current); setCheckoutPending(false); window.history.replaceState({}, '', '/'); setWelcomeMode(false); setTab('keys') }}
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
          <button className="ghost small" onClick={() => setBanner('')} aria-label="Dismiss">✕</button>
        </div>
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
          <button className={tab === 'keys' ? 'active' : ''} onClick={() => { setTab('keys'); setSelectedSessionId(null); setSessionDetail(null); }}>API Keys</button>
          <button className={tab === 'graphs' ? 'active' : ''} onClick={() => setTab('graphs')}>Graphs</button>
          <button className={tab === 'members' ? 'active' : ''} onClick={() => setTab('members')}>Members</button>
          {/* #1623: Billing — plan, usage, upgrade/portal. Session-gated like
              the rest of the dashboard (anon teams get the Protect screen). */}
          <button className={tab === 'billing' ? 'active' : ''} onClick={() => setTab('billing')}>Billing</button>
        </nav>
        {/* #1689: always-visible — OUTSIDE the nav (which can overflow off
            narrow windows), fixed in the header's right side, on every tab.
            Reopens the wizard at step 0 (skills). */}
        <button className="ghost small setup-header" onClick={() => { setWizardStep(0); setWelcomeMode(true) }}>Setup</button>
        {/* #1148-ux: account blob — GitHub/Vercel/Linear pattern: current
            workspace name + avatar top-right; dropdown switches team and
            signs out. Replaces the bare team <select> (which read as "No
            team" and gave no account context). */}
        <div className="account-blob" ref={accountBlobRef}>
          <button
            className="account-blob-btn"
            onClick={() => setAccountMenuOpen(!accountMenuOpen)}
            onKeyDown={(e) => { if (e.key === 'Escape') setAccountMenuOpen(false) }}
            aria-haspopup="menu"
            aria-expanded={accountMenuOpen}
            aria-label={`Account menu — ${currentTeamName || 'No team'}`}
          >
            <span className="account-avatar" aria-hidden="true">
              {(currentTeamName || 'T').charAt(0).toUpperCase()}
            </span>
            <span className="account-name">{currentTeamName || 'No team'}</span>
            <span className="account-chevron" aria-hidden="true">▾</span>
          </button>
          {accountMenuOpen && (
            /* P2-1 (a11y, cycle-2): drop role=menu/menuitem — the full APG
               menu pattern (arrow-key roving focus) isn't implemented, and a
               declared menu contract without arrow nav is worse than none.
               Disclosure pattern: labeled group + plain buttons (needs no
               arrow-key handling; Tab + Enter work natively). */
            <div className="account-menu" role="group" aria-label="Account actions">
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
              <div className="account-menu-divider" />
              <button className="account-menu-logout" onClick={logout}>
                Log out
              </button>
            </div>
          )}
        </div>
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
              <span className="dim small">or run: <code>{`curl -X POST https://api.premiselabs.co/v1/points -H "Authorization: Bearer ${apiKey.slice(0, 12)}…" -H "Content-Type: application/json" -d '{"content":"hello graph","kind":"statement"}'`}</code></span>
            </div>
          </section>
        )}
        {tab === 'overview' && team && !showReentryCard && team.graph_ready !== false && (team.point_count ?? 0) > 0 && (
          <section className="overview">
            <h2>Overview</h2>
            <div className="cards">
              <div className="card"><div className="card-val">{team.point_count ?? 0}</div><div className="card-label">Data points</div></div>
              <div className="card"><div className="card-val">{authMode === 'session' && graphsLoaded ? graphs.length : '—'}</div><div className="card-label">Graphs</div></div>
              <div className="card"><div className="card-val">{membersStatus === 'ok' ? members.length : '—'}</div><div className="card-label">Users</div></div>
              <div className="card"><div className="card-val">{backupInfo ? (backupInfo.count || 'none') : '—'}</div><div className="card-label">Backups</div></div>
              <div className="card"><div className="card-val">{keys.length}</div><div className="card-label">API Keys</div></div>
              <div className="card"><div className="card-val">{team.tier || 'free'}</div><div className="card-label">Plan{team.subscription_status ? ` · ${team.subscription_status}` : ''}</div></div>
            </div>
            {/* #1148-ux review: Team ID / Limits / billing-actions / quickstart
                removed — noise (the quickstart lives on the empty state; limits
                are not actionable here). */}
          </section>
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
                    <td>{!k.revoked_at && !isSessionKey(k) && isOwnerAdmin && (
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
              <h2>Members</h2>
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
            {team && team.tier !== 'team' && isOwnerAdmin && (
              <p className="dim small">Invites require the Team tier — <a href="https://tortoise.premiselabs.co/product.html#pricing" target="_blank" rel="noreferrer">upgrade to add teammates</a>.</p>
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
              <h2>Billing</h2>
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

createRoot(document.getElementById('root')).render(<App />)
