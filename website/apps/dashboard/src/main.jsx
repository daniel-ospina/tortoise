import React from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'

const API_BASE = 'https://api.premiselabs.co'
const KEY_STORAGE = 'tortoise_api_key'
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
const [banner, setBanner] = React.useState('')
const CLAIM_PENDING_COOKIE = 'tt_claim_pending'

function readClaimKeyStorage() {
  try { return sessionStorage.getItem(CLAIM_KEY_STORAGE) || '' } catch { return '' }
}

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
      flowType: 'pkce',
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
  const [apiKey, setApiKey] = React.useState(() => localStorage.getItem(KEY_STORAGE) || '')
  const [authed, setAuthed] = React.useState(false)
  const [team, setTeam] = React.useState(null)
  const [keys, setKeys] = React.useState([])
  const [sessions, setSessions] = React.useState([])
  const [error, setError] = React.useState('')
  const [busy, setBusy] = React.useState(false)
  const [newKey, setNewKey] = React.useState(null)
  const [capNotice, setCapNotice] = React.useState('') // #1147: tier-cap upgrade prompt (keys tab)

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
  async function mintKey(activeKey) {
    const k = await api('/v1/team/keys', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...(activeKey ? { Authorization: `Bearer ${activeKey}` } : {}) },
      body: '{}',
    })
    return k.api_key || k.key || k
  }
  // #1082 (PR1): claim-card state — paste tt_ key → OAuth → POST /v1/claim.
  const [claimKey, setClaimKey] = React.useState(() => {
    try { return sessionStorage.getItem(CLAIM_KEY_STORAGE) || '' } catch { return '' }
  })
  const [claimBusy, setClaimBusy] = React.useState(false)
  const [claimError, setClaimError] = React.useState('')
  const [tab, setTab] = React.useState('overview')
  const [authMode, setAuthMode] = React.useState('session') // 'session' | 'apikey'
  const [checking, setChecking] = React.useState(true)
  const sessionTokenRef = React.useRef(null)
  const [teams, setTeams] = React.useState([])
  const [graphs, setGraphs] = React.useState([])
  const [currentTeamId, setCurrentTeamId] = React.useState(null)
  const [currentGraphId, setCurrentGraphId] = React.useState(null)


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
    const res = await fetch(`${API_BASE}${path}`, {
      ...opts,
      headers: { ...headers, ...(opts.headers || {}) },
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

  async function upgrade() {
    if (!team?.checkout_price_id || checkoutPending) return
    setCheckoutPending(true)
    try {
      const { checkout_url } = await api('/v1/billing/checkout', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ price_id: team.checkout_price_id }),
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

  async function manageBilling() {
    if (billingPending) return // Round-25: double-click guard — no duplicate portal tabs
    setBillingPending(true)
    try {
      const { portal_url } = await api('/v1/billing/portal', { method: 'POST' })
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
  async function mintSessionKey(purpose, teamId) {
    const tok = sessionTokenRef.current
    if (!tok) throw new Error('No session')
    const mint = async (tid) => {
      const res = await fetch(`${API_BASE}/v1/session/key`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${tok}` },
        body: JSON.stringify(tid ? { purpose, team_id: tid } : { purpose }),
      })
      return res
    }
    let res = await mint(teamId)
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
          res = await mint(mintedTeamId)
        }
      }
    }
    if (!res.ok) {
      const b = await res.json().catch(() => ({}))
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

        if (!supabaseClient) { setChecking(false); return }
        const { data: { session }, error } = await supabaseClient.auth.getSession()
        if (error || !session) {
          // Round-24: no session → the card's only affordance is the key input;
          // don't show the misleading 'Sign in with your Tortoise account.'
          setAuthMode('apikey')
          setChecking(false); return
        }
        sessionTokenRef.current = session.access_token
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
              }
            } catch (e) {
              setClaimError((e && e.message) || 'Claim failed — try again.')
            }
          } else {
            setClaimError('Claim interrupted — paste your tt_ key again to continue.')
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
            // Round-12: SIGNED_OUT during this fetch must not resurrect teams
            if (sessionTokenRef.current === session.access_token) setTeams(teamsList)
          }
        } catch { /* treated as no teams below */ }

        // Reuse a stored key when it still belongs to one of the user's teams
        // (avoids burning the 3-active bootstrap cap on every reload), else
        // mint a bootstrap key for the first membership.
        let key = null
        const storedKey = localStorage.getItem(KEY_STORAGE)
        if (storedKey && teamsList.length) {
          try {
            const tRes = await fetch(`${API_BASE}/v1/team`, {
              headers: { Authorization: `Bearer ${storedKey}` },
            })
            if (tRes.ok) {
              const t = await tRes.json()
              if (teamsList.some((x) => x.team_id === t.team_id)) {
                key = storedKey
                teamKeysRef.current[t.team_id] = storedKey
                setCurrentTeamId(t.team_id)
                teamIdRef.current = t.team_id // Round-9: sync — loadTeams must not clobber
              }
            }
          } catch { /* fall through to mint */ }
        }
        if (!key) {
          const firstTeamId = teamsList.length ? teamsList[0].team_id : null
          try {
            const minted = await mintSessionKey('bootstrap', firstTeamId)
            key = minted.key
            if (minted.teamId) teamKeysRef.current[minted.teamId] = key
          } catch (e) {
            // #308: a suspended team's mint 403s — show the appeal banner
            // instead of silently degrading to the API-key screen.
            if (e && e.suspended) setSuspended(e.suspended)
            // No usable session key — fall back to the API-key screen
            setChecking(false)
            return
          }
        }
        // Round-9: a SIGNED_OUT during the mint must not complete the login
        // with a fresh key on the tab the user just signed out of.
        if (!sessionTokenRef.current) { setChecking(false); return }
        if (!key) { setChecking(false); return }
        localStorage.setItem(KEY_STORAGE, key)
        setApiKey(key)
        apiKeyRef.current = key
        setAuthMode('session')
        await completeLogin(key)
      } catch (e) {
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
    try {
      const t = await api('/v1/team', key ? { headers: { Authorization: `Bearer ${key}` } } : {})
      setTeam(t)
      setAuthed(true)
      loadAlerts(t?.team_id)  // fire-and-forget (#308 R7)
      await Promise.all([loadAll(key), loadTeams(), loadBackups(key)])
    } catch (e) {
      if (e && e.suspended) setSuspended(e.suspended)  // #308
      setError(e.message === 'Invalid API key' ? 'Invalid API key — check your key and try again.' : e.message)
      setAuthed(false)
    } finally {
      setBusy(false)
      setChecking(false)
    }
  }

  async function login() {
    setError('')
    setBusy(true)
    try {
      const t = await api('/v1/team')
      setTeam(t)
      setAuthed(true)
      setAuthMode('apikey') // P5 (code-review): makes the session-only notices live for API-key users
      setMembersStatus('denied') // Fix D (review round 2): no session → members view unavailable; never 'Loading…' forever
      apiKeyRef.current = apiKey // Round-22 (P1): createKey's branch-2 guard compares against the LIVE key — API-key auth never populated the ref, so every createKey bailed and keys were silently invisible
      localStorage.setItem(KEY_STORAGE, apiKey)
      await loadAll()
    } catch (e) {
      // Round-23: a failed key attempt must not keep the misleading
      // 'Sign in with your Tortoise account.' headline above the key input.
      setAuthMode('apikey')
      setError(e.message === 'Invalid API key' ? 'Invalid API key — check your key and try again.' : e.message)
      setAuthed(false)
    } finally {
      setBusy(false)
    }
  }

  // #1082 (PR1): claim-card handlers — attach a provider-verified identity
  // to an anon team (same key, same graph, memories intact).
  async function claimSignIn(provider) {
    setClaimError('')
    const k = (claimKey || '').trim()
    if (!k.startsWith('tt_')) {
      setClaimError('Enter a valid tt_ key — the one printed when the team was minted.')
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
    localStorage.removeItem(KEY_STORAGE)
    setApiKey('')
    apiKeyRef.current = null
    setAuthed(false)
    setAuthMode('apikey') // Round-6 (P3): card offers only the key input after logout
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
      const newKeyVal = await mintKey(activeKey)
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

  async function revokeKey(keyId, opts = {}) {
    // Round-20 (P2): capture team at call — a mid-flight switch must not let
    // this revoke's re-mint clobber the new team's active key/localStorage or
    // land the old team's key table under the new header.
    const _teamAtCall = currentTeamId
    if (!opts.skipConfirm && !confirm('Revoke this API key? Applications using it will stop working.')) return
    setCapNotice('')
    setError('')
    try {
      await api(`/v1/team/keys/${keyId}`, { method: 'DELETE' })
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
    return (
      <div className="auth-wrap">
        <div className="auth-card">
          <div className="logo">Tortoise</div>
          <h1>Dashboard</h1>
          <p className="dim">{authMode === 'session' ? 'Sign in with your account key, or enter an API key to manage your team.' : 'Enter your API key to manage your team, keys, and sessions.'}</p>
          <form onSubmit={(e) => { e.preventDefault(); login() }}>
            <input
              type="password"
              placeholder="tt_..."
              aria-label="API key"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              autoFocus
              autoComplete="one-time-code" // Round-26: one-shot secret — accurate hint, silences the username-field heuristic
            />
            <button type="submit" disabled={busy || !apiKey.trim()}>
              {busy ? 'Connecting…' : 'Connect'}
            </button>
          </form>
          {error && <div className="error">{error}</div>}
          <p className="dim small">No key? <a href="https://tortoise.premiselabs.co/signup" target="_blank" rel="noreferrer">Create a free account</a> (GitHub, Google, or email) — you'll get one on the welcome page. Prefer zero-email? Run <code>tortoise signup</code> in your terminal.</p>
        </div>
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
      <header>
        <div className="logo">Tortoise</div>
        <nav>
          <button className={tab === 'overview' ? 'active' : ''} onClick={() => { setTab('overview'); setSelectedSessionId(null); setSessionDetail(null); }}>Overview</button>
          <button className={tab === 'keys' ? 'active' : ''} onClick={() => { setTab('keys'); setSelectedSessionId(null); setSessionDetail(null); }}>API Keys</button>
          <button className={tab === 'graphs' ? 'active' : ''} onClick={() => setTab('graphs')}>Graphs</button>
          <button className={tab === 'members' ? 'active' : ''} onClick={() => setTab('members')}>Members</button>
          <button className={tab === 'sessions' ? 'active' : ''} onClick={() => { setTab('sessions'); setSelectedSessionId(null); setSessionDetail(null); }}>Sessions</button>
        </nav>
        <div className="switchers">
          <select
            value={currentTeamId || ''}
            onChange={(e) => e.target.value && switchTeam(e.target.value)}
            aria-label="Team"
          >
            {teams.length === 0 && <option value="">No team</option>}
            {teams.map((t) => (
              <option key={t.team_id} value={t.team_id}>{t.team_name}</option>
            ))}
          </select>
          <select
            value={currentGraphId || ''}
            onChange={(e) => setCurrentGraphId(e.target.value)}
            aria-label="Graph"
          >
            {graphs.length === 0 && <option value="">No graph</option>}
            {graphs.map((g) => (
              <option key={g.graph_id} value={g.graph_id}>{g.name}</option>
            ))}
          </select>
        </div>
        {team && team.tier !== 'team' && (
          <a className="tier-badge" href="https://tortoise.premiselabs.co/product.html#pricing" target="_blank" rel="noreferrer">
            {team.tier || 'free'} tier · Upgrade
          </a>
        )}
        {team && team.status === 'flagged' && (
          <span className="tier-badge" title="Suspicious activity detected — see security alerts">⚠ flagged</span>
        )}
        {team && team.tier === 'team' && (
          <span className="tier-badge tier-team">Team tier</span>
        )}
        <button className="ghost" onClick={logout}>Log out</button>
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
        {tab === 'overview' && team && (team.point_count ?? 0) === 0 && (
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
        {tab === 'overview' && team && (team.point_count ?? 0) > 0 && (
          <section className="overview">
            <h2>Overview</h2>
            <div className="cards">
              <div className="card"><div className="card-val">{team.point_count ?? 0}</div><div className="card-label">Points</div></div>
              <div className="card"><div className="card-val">{authMode === 'session' && graphsLoaded ? graphs.length : '—'}</div><div className="card-label">Graphs</div></div>
              <div className="card"><div className="card-val">{membersStatus === 'ok' ? members.length : '—'}</div><div className="card-label">Users</div></div>
              <div className="card"><div className="card-val">{backupInfo ? (backupInfo.count || 'none') : '—'}</div><div className="card-label">Backups</div></div>
              <div className="card"><div className="card-val">{keys.length}</div><div className="card-label">API Keys</div></div>
              <div className="card"><div className="card-val">{sessions.length}</div><div className="card-label">Sessions</div></div>
              <div className="card"><div className="card-val">{team.tier || 'free'}</div><div className="card-label">Plan{team.subscription_status ? ` · ${team.subscription_status}` : ''}</div></div>
            </div>
            {team.tier === 'free' && (
              <p className="dim small">Upgrade for more graphs, backups, and team members — <a href="https://tortoise.premiselabs.co/product.html#pricing" target="_blank" rel="noreferrer">see pricing</a>.</p>
            )}
            <p className="dim small">Team ID: <code>{team.team_id}</code></p>
            <p className="dim small">
              Limits: {team.max_points ?? '—'} points · {team.max_api_keys ?? '—'} API keys · {team.max_graphs ?? '—'} graphs
            </p>
            <div className="billing-actions">
              {hasActiveSubscription ? (
                <button className="ghost" onClick={manageBilling}>Manage billing</button>
              ) : (
                <button className="ghost" onClick={upgrade} disabled={checkoutPending}>
                  {checkoutPending ? 'Opening checkout…' : 'Upgrade'}
                </button>
              )}
            </div>
            <div className="quickstart">
              <h3>Your first point</h3>
              <pre>{`curl -X POST https://api.premiselabs.co/v1/points \\
  -H "Authorization: Bearer ${apiKey.slice(0, 12)}…" \\
  -H "Content-Type: application/json" \\
  -d '{"content": "hello graph", "kind": "statement"}'`}</pre>
            </div>
          </section>
        )}

        {tab === 'keys' && (
          <section>
            {/* #1082 (PR1): claim card — renders when the resolved team is
                anon (apikey-mode entry — a pre-claim anon owner has NO
                session by definition). Paste the tt_ key → fresh OAuth
                (GitHub/Google) → POST /v1/claim. redirectTo is the dashboard
                claim route (?claim=1), NEVER welcome.html (its Phase-2 mint
                would orphan the claimable team). */}
            {team && team.anon && (
              <div className="claim-card">
                <h3>Claim this anonymous team</h3>
                <p className="dim">
                  This team was minted without a verified identity (zero-email).
                  Attach your GitHub or Google account to unlock dashboard
                  session access — same key, same graph, memories intact.
                </p>
                {!claimKey ? (
                  <div className="inline-form">
                    <input
                      placeholder="Paste your tt_ key"
                      aria-label="Paste your tt_ key"
                      value={claimKey}
                      onChange={(e) => setClaimKey(e.target.value)}
                      autoComplete="one-time-code"
                    />
                    <button onClick={() => claimSignIn('github')} disabled={claimBusy}>
                      {claimBusy ? 'Redirecting…' : 'Save key & sign in with GitHub'}
                    </button>
                    <button className="ghost" onClick={() => claimSignIn('google')} disabled={claimBusy}>
                      with Google
                    </button>
                  </div>
                ) : (
                  <div className="inline-form">
                    <code className="key-value">{claimKey.slice(0, 12)}…</code>
                    <button onClick={() => claimSignIn('github')} disabled={claimBusy}>
                      {claimBusy ? 'Redirecting…' : 'Sign in with GitHub'}
                    </button>
                    <button className="ghost" onClick={() => claimSignIn('google')} disabled={claimBusy}>
                      Sign in with Google
                    </button>
                    <button
                      className="ghost small"
                      onClick={() => { setClaimKey(''); setClaimError('') }}
                      disabled={claimBusy}
                    >
                      Change key
                    </button>
                  </div>
                )}
                {claimError && <div className="error">{claimError}</div>}
                <p className="dim small">
                  You'll sign in with GitHub or Google (a fresh provider-verified
                  identity), then come back here with the team unlocked.
                </p>
              </div>
            )}
            <div className="row">
              <h2>API Keys</h2>
              <button onClick={createKey} disabled={busy}>+ New key</button>
            </div>
            <p className="dim small">Lost your key? <button className="link" onClick={recoverKey} disabled={busy}>Generate a new one</button> — works without an existing key (session-authenticated).</p>
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
              <thead><tr><th>Prefix</th><th>Created</th><th>Status</th><th></th></tr></thead>
              <tbody>
                {keys.length === 0 && <tr><td colSpan="4" className="dim">No keys yet.</td></tr>}
                {keys.map((k) => (
                  <tr key={k.id}>
                    <td><code>{k.key_prefix || k.id?.slice(0, 12)}</code></td>
                    <td>{fmtTime(k.created_at || k.createdAt)}</td>
                    <td>{k.revoked_at ? <span className="revoked">revoked</span> : isSessionKey(k) ? <span className="live">ephemeral · session</span> : <span className="live">active</span>}</td>
                    <td>{!k.revoked_at && !isSessionKey(k) && (
                      <span style={{ display: 'inline-flex', gap: '0.35rem' }}>
                        <button className="ghost small" onClick={() => regenerateKey(k.id)}>Regenerate</button>
                        <button className="ghost small" onClick={() => revokeKey(k.id)}>Revoke</button>
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
            {authMode !== 'session' && (
              <p className="dim small">Graphs are session-authenticated — sign in with your Tortoise account to view and create them.</p>
            )}
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
                {authMode !== 'session' && <tr><td colSpan="3" className="dim">Sign in with your Tortoise account to view graphs.</td></tr>}
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
            {authMode !== 'session' && (
              <p className="dim small">Members are session-authenticated — sign in with your Tortoise account to view them.</p>
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

        {tab === 'sessions' && !selectedSessionId && (
          <section>
            <h2>Sessions</h2>
            {sessions.length === 0 ? (
              <p className="dim">No sessions yet. Sessions appear when your agents capture conversations.</p>
            ) : (
              <table>
                <thead><tr><th>ID</th><th>Turns / Extracted</th><th>Created</th></tr></thead>
                <tbody>
                  {sessions.map((s) => (
                    <tr
                      key={s.id || s.session_id}
                      className="clickable"
                      tabIndex={0}
                      role="button"
                      aria-label={`View session ${(s.id || s.session_id || '').slice(0, 16)} details`}
                      onClick={() => {
                        setSelectedSessionId(s.id || s.session_id)
                        setSessionDetail(null)
                        fetchSessionDetail(s.id || s.session_id)
                      }}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter' || e.key === ' ') {
                          e.preventDefault()
                          setSelectedSessionId(s.id || s.session_id)
                          setSessionDetail(null)
                          fetchSessionDetail(s.id || s.session_id)
                        }
                      }}
                    >
                      <td><code>{(s.id || s.session_id || '').slice(0, 16)}…</code></td>
                      <td>{s.turns != null ? `${s.turns} turn${s.turns !== 1 ? 's' : ''}` : '—'}{s.extracted != null ? ` · ${s.extracted} extracted` : ''}</td>
                      <td>{fmtTime(s.created_at || s.createdAt)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>
        )}

        {tab === 'sessions' && selectedSessionId && (
          <section>
            <div className="row">
              <h2>Session Detail</h2>
              <button className="ghost" onClick={() => { setSelectedSessionId(null); setSessionDetail(null); }}>← Back to sessions</button>
            </div>
            {detailLoading ? (
              <p className="dim">Loading session…</p>
            ) : sessionDetail ? (
              <div className="session-detail">
                <div className="cards">
                  <div className="card"><div className="card-val">{sessionDetail.turns ?? 0}</div><div className="card-label">Turns</div></div>
                  <div className="card"><div className="card-val">{sessionDetail.extracted ?? 0}</div><div className="card-label">Extracted Points</div></div>
                </div>
                <p className="dim small">Session ID: <code>{sessionDetail.id}</code> · Created: {fmtTime(sessionDetail.created_at)}</p>

                {sessionDetail.turn_points && sessionDetail.turn_points.length > 0 && (
                  <div className="session-section">
                    <h3>Conversation Turns ({sessionDetail.turn_points.length})</h3>
                    <div className="turn-list">
                      {sessionDetail.turn_points.map((turn, i) => (
                        <div key={turn.id || i} className={`turn-item turn-${turn.role}`}>
                          <div className="turn-header">
                            <span className="turn-role">{turn.role}</span>
                            <span className="turn-index">Turn {i + 1}</span>
                          </div>
                          <div className="turn-content">{turn.content}</div>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {sessionDetail.extracted_points && sessionDetail.extracted_points.length > 0 && (
                  <div className="session-section">
                    <h3>Extracted Points ({sessionDetail.extracted_points.length})</h3>
                    <div className="extracted-list">
                      {sessionDetail.extracted_points.map((ep, i) => (
                        <div key={ep.id || i} className={`extracted-item extracted-${ep.kind}`}>
                          <div className="extracted-header">
                            <span className={`kind-badge kind-${ep.kind}`}>{ep.kind}</span>
                            <span className="dim small">{ep.id ? ep.id.slice(0, 12) + '…' : ''}</span>
                          </div>
                          <div className="extracted-content">{ep.content}</div>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {(!sessionDetail.turn_points || sessionDetail.turn_points.length === 0) &&
                 (!sessionDetail.extracted_points || sessionDetail.extracted_points.length === 0) && (
                  <p className="dim">No turns or extracted points found for this session.</p>
                )}
              </div>
            ) : (
              <p className="dim">Could not load session detail.</p>
            )}
          </section>
        )}
      </main>
    </div>
  )
}

createRoot(document.getElementById('root')).render(<App />)
