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

  // ── Session auth: on load, try the shared cookie session ──
  React.useEffect(() => {
    ;(async () => {
      try {
        if (!supabaseClient) { setChecking(false); return }
        const { data: { session }, error } = await supabaseClient.auth.getSession()
        if (error || !session) { setChecking(false); return }

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

  async function completeLogin(key) {
    setError('')
    try {
      const t = await api('/v1/team', key ? { headers: { Authorization: `Bearer ${key}` } } : {})
      setTeam(t)
      setAuthed(true)
      await loadAll()
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
          <button className={tab === 'sessions' ? 'active' : ''} onClick={() => setTab('sessions')}>Sessions</button>
        </nav>
        <button className="ghost" onClick={logout}>Log out</button>
      </header>

      <main>
        {error && <div className="error banner">{error}</div>}

        {tab === 'overview' && team && (
          <section className="overview">
            <h2>Overview</h2>
            <div className="cards">
              <div className="card"><div className="card-val">{team.point_count ?? 0}</div><div className="card-label">Points</div></div>
              <div className="card"><div className="card-val">{keys.length}</div><div className="card-label">API Keys</div></div>
              <div className="card"><div className="card-val">{sessions.length}</div><div className="card-label">Sessions</div></div>
              <div className="card"><div className="card-val">{team.tier || 'free'}</div><div className="card-label">Tier</div></div>
            </div>
            <p className="dim small">Team ID: <code>{team.team_id}</code></p>
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
