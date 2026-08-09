import React from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'

const API_BASE = 'https://api.premiselabs.co'
const KEY_STORAGE = 'tortoise_api_key'
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
  const [checkoutPending, setCheckoutPending] = React.useState(false)
  // P5 (code-review): distinguish 'loading' / 'ok' / 'denied' / 'error' so
  // loading and network failures never masquerade as an RBAC denial.
  const [membersStatus, setMembersStatus] = React.useState('loading')
  // P1/P2 (code-review): per-team data-plane key cache. Bootstrap mints are
  // capped at 3 active per team, so cache the minted key per team_id and
  // reuse on switch instead of re-minting (which would 429 after 3 switches).
  const teamKeysRef = React.useRef({})

  const headers = apiKey ? { Authorization: `Bearer ${apiKey}` } : {}

  async function api(path, opts = {}) {
    const res = await fetch(`${API_BASE}${path}`, {
      ...opts,
      headers: { ...headers, ...(opts.headers || {}) },
    })
    if (!res.ok) {
      const body = await res.json().catch(() => ({}))
      throw new Error(body.detail || `HTTP ${res.status}`)
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
      window.open(checkout_url, '_blank')
    } catch (err) {
      setError(err.message)
      setCheckoutPending(false)
    }
  }

  async function manageBilling() {
    try {
      const { portal_url } = await api('/v1/billing/portal', { method: 'POST' })
      window.open(portal_url, '_blank')
    } catch (err) {
      setError(err.message)
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
      const poll = setInterval(async () => {
        tries += 1
        try {
          const t = await refreshTeam()
          if (t && ACTIVE_STATUSES.includes(t.subscription_status)) { tries = 5 }
        } catch { /* webhook may not have landed yet */ }
        if (tries >= 5) {
          clearInterval(poll)
          params.delete('session_id')
          window.history.replaceState({}, '', `${window.location.pathname}${params.toString() ? `?${params}` : ''}`)
        }
      }, 2000)
      return () => clearInterval(poll)
    }
    if (cancelled) {
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
      throw new Error(b.detail || `HTTP ${res.status}`)
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
        if (!supabaseClient) { setChecking(false); return }
        const { data: { session }, error } = await supabaseClient.auth.getSession()
        if (error || !session) { setChecking(false); return }
        sessionTokenRef.current = session.access_token
        // Round-6 (P2): supabase-js auto-refreshes the access token (~1h) into
        // the cookie — keep the ref in sync so JWT-scoped calls never die with
        // a stale token while the dashboard still looks logged in.
        const { data: authSub } = supabaseClient.auth.onAuthStateChange((_evt, s) => {
          if (s?.access_token) sessionTokenRef.current = s.access_token
          else if (_evt === 'SIGNED_OUT') { sessionTokenRef.current = null; setTeams([]) }
        })
        authSubRef.current = authSub?.subscription || null

        // List memberships up front so the mint targets a concrete team
        // (P1: multi-membership users cannot mint without team_id).
        let teamsList = []
        try {
          const teamsRes = await fetch(`${API_BASE}/v1/teams`, {
            headers: { Authorization: `Bearer ${session.access_token}` },
          })
          if (teamsRes.ok) {
            teamsList = await teamsRes.json()
            setTeams(teamsList)
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
          } catch {
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
        setAuthMode('session')
        await completeLogin(key)
      } catch (e) {
        setChecking(false)
      }
    })()
  }, [])

  async function refreshTeam(key) {
    // P1 (code-review): extracted team refetch — the success-return poll loop
    // used an undefined `jl` (dead code); this is the real refetch.
    // P2 (code-review): key param so the refetch targets the selected team —
    // the overview cards + header tier badge read /v1/team, which resolves
    // the team from the API key.
    const t = await api('/v1/team', key ? { headers: { Authorization: `Bearer ${key}` } } : {})
    setTeam(t)
    return t
  }

  async function completeLogin(key) {
    setError('')
    try {
      const t = await api('/v1/team', key ? { headers: { Authorization: `Bearer ${key}` } } : {})
      setTeam(t)
      setAuthed(true)
      await Promise.all([loadAll(key), loadTeams(), loadBackups(key)])
    } catch (e) {
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
      localStorage.setItem(KEY_STORAGE, apiKey)
      await loadAll()
    } catch (e) {
      setError(e.message === 'Invalid API key' ? 'Invalid API key — check your key and try again.' : e.message)
      setAuthed(false)
    } finally {
      setBusy(false)
    }
  }

  async function logout() {
    localStorage.removeItem(KEY_STORAGE)
    setApiKey('')
    setAuthed(false)
    setAuthMode('apikey') // Round-6 (P3): card offers only the key input after logout
    setTeam(null)
    setKeys([])
    setSessions([])
    setNewKey(null)
    setGraphs([])
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
                                          // under team C's header
    // P2 (code-review): /v1/team/keys + /v1/sessions resolve the team from the
    // API key — fetch them with the SELECTED team's key so the overview cards
    // track the team switcher, not the bootstrap team.
    const h = key ? { headers: { Authorization: `Bearer ${key}` } } : {}
    try {
      const [k, s] = await Promise.all([api('/v1/team/keys', h), api('/v1/sessions', h)])
      setKeys(Array.isArray(k) ? k : k.keys || [])
      setSessions(Array.isArray(s) ? s : s.sessions || [])
    } catch (e) {
      setError(e.message)
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
    const prevTeamId = currentTeamId
    const prevKey = apiKey
    const tok = sessionTokenRef.current
    if (!tok) return // Round-3: guard BEFORE wiping state — logout→apikey
                     // login must not blank the dashboard on a stale pick
    setMembers(null)
    setMembersStatus('loading')
    setGraphs([])
    setCurrentGraphId(null)
    setTeam(null)          // Fix B: clear key-scoped overview state too
    setKeys([])
    setSessions([])
    setBackupInfo(null)
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
      setAuthMode('session') // Round-9: a session-minted key IS session auth — no more
                             // 'sign in required' notices beside live session data
      try {
        await refreshTeam(key)
      } catch (e) {
        // Round-10: a cached ephemeral key may have expired (24h) or been
        // revoked — drop it and re-mint once before falling back to revert.
        if (teamIdRef.current === teamId && String(e.message || '').includes('401')) {
          delete teamKeysRef.current[teamId]
          const minted = await mintSessionKey('bootstrap', teamId)
          key = minted.key
          teamKeysRef.current[teamId] = key
          if (teamIdRef.current !== teamId) return
          localStorage.setItem(KEY_STORAGE, key)
          setApiKey(key)
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
          setApiKey(prevKey)
          localStorage.setItem(KEY_STORAGE, prevKey)
          setCurrentTeamId(prevTeamId)
          teamIdRef.current = prevTeamId
          setTeam(null)
          await refreshTeam(prevKey).catch(() => {})
          // Round-3: reload ALL key-scoped data for the reverted team —
          // otherwise keys/sessions/backups stay wiped until reload.
          await Promise.all([loadAll(prevKey), loadBackups(prevKey)]).catch(() => {})
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
        // Fix E (review round 2): auto-select first graph so the dropdown
        // shows a selection after switch; Round-3: only when nothing is
        // selected yet (don't clobber a manual pick on re-load).
        setCurrentGraphId((prev) => prev ?? list[0]?.graph_id ?? null)
      }
    } catch { /* best-effort */ }
  }

  async function createGraph() {
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
    } catch (e) {
      setError(e.message)
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
        setMembers(await res.json())
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
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  async function removeMember(userId) {
    if (!confirm('Remove this member from the team?')) return
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
    } catch (e) {
      setError(e.message)
    }
  }

  async function changeRole(userId, role) {
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
    } catch (e) {
      setError(e.message)
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
    setError('')
    setBusy(true)
    try {
      const k = await api('/v1/team/keys', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' })
      setNewKey(k.api_key || k.key || k)
      await loadAll()
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  async function revokeKey(keyId) {
    if (!confirm('Revoke this API key? Applications using it will stop working.')) return
    setError('')
    try {
      await api(`/v1/team/keys/${keyId}`, { method: 'DELETE' })
      // Fix A (review round 2): if we revoked the active data-plane key, the
      // per-team cache + localStorage now hold a dead key — re-mint so the
      // app doesn't 401 on the next switch/reload.
      const cached = currentTeamId ? teamKeysRef.current[currentTeamId] : null
      if (cached && keyId === keyIdFromValue(cached)) {
        delete teamKeysRef.current[currentTeamId]
        if (localStorage.getItem(KEY_STORAGE) === cached) localStorage.removeItem(KEY_STORAGE)
        const tok = sessionTokenRef.current
        if (tok && currentTeamId) {
          try {
            const minted = await mintSessionKey('bootstrap', currentTeamId)
            teamKeysRef.current[currentTeamId] = minted.key
            localStorage.setItem(KEY_STORAGE, minted.key)
            setApiKey(minted.key)
            await refreshTeam(minted.key).catch(() => {})
            // Round-3: reload with the NEW key, not the revoked-key closure.
            await loadAll(minted.key).catch(() => {})
            return
          } catch { /* leave API-key screen; not fatal */ }
        }
      }
      await loadAll()
    } catch (e) {
      setError(e.message)
    }
  }

  // ── J-2: key recovery via E1 session mint (the #518 fix) ──
  async function recoverKey() {
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
      setAuthMode('session') // Round-10: a recovery-minted key IS session auth — keep tabs consistent
      await Promise.all([loadAll(data.key), refreshTeam(data.key)]).catch(() => {})
    } catch (e) {
      setError(e.message)
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
          <p className="dim">{authMode === 'session' ? 'Sign in with your Tortoise account.' : 'Enter your API key to manage your team, keys, and sessions.'}</p>
          <input
            type="password"
            placeholder="tt_..."
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && login()}
            autoFocus
          />
          <button onClick={login} disabled={busy || !apiKey.trim()}>
            {busy ? 'Connecting…' : 'Connect'}
          </button>
          {error && <div className="error">{error}</div>}
          <p className="dim small">No key? <a href="https://tortoise.premiselabs.co/signup" target="_blank" rel="noreferrer">Sign up</a> — you'll get one on the welcome page.</p>
        </div>
      </div>
    )
  }

  return (
    <div className="app">
      <header>
        <div className="logo">Tortoise</div>
        <nav>
          <button className={tab === 'overview' ? 'active' : ''} onClick={() => setTab('overview')}>Overview</button>
          <button className={tab === 'keys' ? 'active' : ''} onClick={() => setTab('keys')}>API Keys</button>
          <button className={tab === 'graphs' ? 'active' : ''} onClick={() => setTab('graphs')}>Graphs</button>
          <button className={tab === 'members' ? 'active' : ''} onClick={() => setTab('members')}>Members</button>
          <button className={tab === 'sessions' ? 'active' : ''} onClick={() => setTab('sessions')}>Sessions</button>
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
            {team.tier} tier · Upgrade
          </a>
        )}
        {team && team.tier === 'team' && (
          <span className="tier-badge tier-team">Team tier</span>
        )}
        <button className="ghost" onClick={logout}>Log out</button>
      </header>

      <main>
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
              <div className="card"><div className="card-val">{authMode === 'session' ? graphs.length : '—'}</div><div className="card-label">Graphs</div></div>
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
            <div className="row">
              <h2>API Keys</h2>
              <button onClick={createKey} disabled={busy}>+ New key</button>
            </div>
            <p className="dim small">Lost your key? <button className="link" onClick={recoverKey}>Generate a new one</button> — works without an existing key (session-authenticated).</p>
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
                    <td>{!k.revoked_at && !isSessionKey(k) && <button className="ghost small" onClick={() => revokeKey(k.id)}>Revoke</button>}</td>
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

        {tab === 'sessions' && (
          <section>
            <h2>Sessions</h2>
            {sessions.length === 0 ? (
              <p className="dim">No sessions yet. Sessions appear when your agents capture conversations.</p>
            ) : (
              <table>
                <thead><tr><th>ID</th><th>Summary</th><th>Created</th></tr></thead>
                <tbody>
                  {sessions.map((s) => (
                    <tr key={s.id || s.session_id}>
                      <td><code>{(s.id || s.session_id || '').slice(0, 16)}…</code></td>
                      <td>{s.summary || s.title || s.metadata?.summary || '—'}</td>
                      <td>{fmtTime(s.created_at || s.createdAt)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>
        )}
      </main>
    </div>
  )
}

createRoot(document.getElementById('root')).render(<App />)
