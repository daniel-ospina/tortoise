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
  const [showCreateTeam, setShowCreateTeam] = React.useState(false)
  const [newTeamName, setNewTeamName] = React.useState('')
  const [checkoutPending, setCheckoutPending] = React.useState(false)
  const [selectedSessionId, setSelectedSessionId] = React.useState(null)
  const [sessionDetail, setSessionDetail] = React.useState(null)
  const [detailLoading, setDetailLoading] = React.useState(false)

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
  React.useEffect(() => {
    ;(async () => {
      try {
        if (!supabaseClient) { setChecking(false); return }
        const { data: { session }, error } = await supabaseClient.auth.getSession()
        if (error || !session) { setChecking(false); return }
        sessionTokenRef.current = session.access_token

        // Session found — mint a data-plane key via E1 (POST /v1/session/key)
        // using the session access token (JWKS-verified server-side).
        const res = await fetch(`${API_BASE}/v1/session/key`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            Authorization: `Bearer ${session.access_token}`,
          },
          body: JSON.stringify({ purpose: 'bootstrap' }),
        })
        if (res.ok) {
          const data = await res.json()
          if (data.key) {
            localStorage.setItem(KEY_STORAGE, data.key)
            setApiKey(data.key)
            setAuthMode('session')
            await completeLogin(data.key)
          }
        } else {
          // No usable session key — fall back to the API-key screen
          setChecking(false)
        }
      } catch (e) {
        setChecking(false)
      }
    })()
  }, [])

  async function refreshTeam() {
    // P1 (code-review): extracted team refetch — the success-return poll loop
    // used an undefined `jl` (dead code); this is the real refetch.
    const t = await api('/v1/team')
    setTeam(t)
    return t
  }

  async function completeLogin(key) {
    setError('')
    try {
      const t = await api('/v1/team', key ? { headers: { Authorization: `Bearer ${key}` } } : {})
      setTeam(t)
      setAuthed(true)
      await Promise.all([loadAll(), loadTeams()])
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
    setTeam(null)
    setKeys([])
    setSessions([])
    setNewKey(null)
    try { if (supabaseClient) await supabaseClient.auth.signOut() } catch { /* best-effort */ }
  }

  async function loadAll() {
    try {
      const [k, s] = await Promise.all([api('/v1/team/keys'), api('/v1/sessions')])
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
        if (list.length > 0 && !currentTeamId) {
          setCurrentTeamId(list[0].team_id)
        }
      }
    } catch { /* best-effort */ }
  }

  async function switchTeam(teamId) {
    setCurrentTeamId(teamId)
    setCurrentGraphId(null)
    const tok = sessionTokenRef.current
    if (!tok) return
    try {
      const res = await fetch(`${API_BASE}/v1/graphs?team_id=${teamId}`, {
        headers: { Authorization: `Bearer ${tok}` },
      })
      if (res.ok) {
        const list = await res.json()
        setGraphs(list)
        if (list.length > 0) setCurrentGraphId(list[0].graph_id)
      }
    } catch { /* best-effort */ }
  }

  async function createTeamFromUI() {
    if (!newTeamName.trim()) return
    setBusy(true)
    setError('')
    try {
      const tok = sessionTokenRef.current
      if (!tok) throw new Error('No session')
      const res = await fetch(`${API_BASE}/v1/teams`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${tok}` },
        body: JSON.stringify({ name: newTeamName.trim() }),
      })
      if (!res.ok) {
        const b = await res.json().catch(() => ({}))
        throw new Error(b.detail || `HTTP ${res.status}`)
      }
      setNewTeamName('')
      setShowCreateTeam(false)
      await loadTeams()
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

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
      const res = await fetch(`${API_BASE}/v1/session/key`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${tok}` },
        body: JSON.stringify({ purpose: 'recovery' }),
      })
      if (!res.ok) {
        const b = await res.json().catch(() => ({}))
        throw new Error(b.detail || `HTTP ${res.status}`)
      }
      const data = await res.json()
      setNewKey(data.key)
      await loadAll()
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

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
          <button className={tab === 'overview' ? 'active' : ''} onClick={() => { setTab('overview'); setSelectedSessionId(null); setSessionDetail(null); }}>Overview</button>
          <button className={tab === 'keys' ? 'active' : ''} onClick={() => { setTab('keys'); setSelectedSessionId(null); setSessionDetail(null); }}>API Keys</button>
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
              <div className="card"><div className="card-val">{keys.length}</div><div className="card-label">API Keys</div></div>
              <div className="card"><div className="card-val">{sessions.length}</div><div className="card-label">Sessions</div></div>
              <div className="card"><div className="card-val">{team.tier || 'free'}</div><div className="card-label">Plan{team.subscription_status ? ` · ${team.subscription_status}` : ''}</div></div>
            </div>
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
                    <td>{k.revoked_at ? <span className="revoked">revoked</span> : <span className="live">active</span>}</td>
                    <td>{!k.revoked_at && <button className="ghost small" onClick={() => revokeKey(k.id)}>Revoke</button>}</td>
                  </tr>
                ))}
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
