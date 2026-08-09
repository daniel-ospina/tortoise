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
  const [members, setMembers] = React.useState(null) // null = not loaded / no access
  const [inviteEmail, setInviteEmail] = React.useState('')
  const [inviteRole, setInviteRole] = React.useState('member')
  const [backupInfo, setBackupInfo] = React.useState(null)
  const [newGraphName, setNewGraphName] = React.useState('')
  const teamIdRef = React.useRef(null)

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
      if (teamIdRef.current === teamId) setGraphs(list)
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

  async function loadMembers() {
    const tok = sessionTokenRef.current
    if (!tok || !currentTeamId) return
    try {
      const res = await fetch(`${API_BASE}/v1/teams/${currentTeamId}/members`, {
        headers: { Authorization: `Bearer ${tok}` },
      })
      if (res.ok) setMembers(await res.json())
      else if (res.status === 403) setMembers(null) // not owner/admin — cannot view
    } catch { setMembers(null) }
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
      await loadMembers()
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
      await loadMembers()
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
      await loadMembers()
    } catch (e) {
      setError(e.message)
    }
  }

  async function loadBackups() {
    try {
      const b = await api('/backups')
      const list = b.backups || []
      setBackupInfo(list.length ? { latest: list[0], count: list.length } : { count: 0 })
    } catch { /* tier-gated (Pro) — leave null */ }
  }

  // Load team-scoped data whenever the active team changes.
  React.useEffect(() => {
    if (currentTeamId) {
      teamIdRef.current = currentTeamId
      loadMembers()
      loadGraphs(currentTeamId)
      loadBackups()
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
        {error && <div className="error banner">{error}</div>}

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
              <div className="card"><div className="card-val">{graphs.length}</div><div className="card-label">Graphs</div></div>
              <div className="card"><div className="card-val">{members === null ? '—' : members.length}</div><div className="card-label">Users</div></div>
              <div className="card"><div className="card-val">{backupInfo ? (backupInfo.count || 'none') : '—'}</div><div className="card-label">Backups</div></div>
              <div className="card"><div className="card-val">{team.tier || 'free'}</div><div className="card-label">Tier</div></div>
            </div>
            {team.tier === 'free' && (
              <p className="dim small">Upgrade for more graphs, backups, and team members — <a href="https://tortoise.premiselabs.co/product.html#pricing" target="_blank" rel="noreferrer">see pricing</a>.</p>
            )}
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

        {tab === 'graphs' && (
          <section>
            {authMode !== 'session' && (
              <p className="dim small">Graphs are session-authenticated — sign in with your Tortoise account to view and create them.</p>
            )}
            <div className="row">
              <h2>Graphs</h2>
              <div className="inline-form">
                <input
                  placeholder="New graph name"
                  value={newGraphName}
                  onChange={(e) => setNewGraphName(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && createGraph()}
                />
                <button onClick={createGraph} disabled={busy || !newGraphName.trim()}>+ Create</button>
              </div>
            </div>
            <table>
              <thead><tr><th>Name</th><th>Kind</th><th>Graph ID</th></tr></thead>
              <tbody>
                {graphs.length === 0 && <tr><td colSpan="3" className="dim">No graphs yet — create your first one above.</td></tr>}
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
                {members === null && <tr><td colSpan="4" className="dim">Member list is only visible to owners and admins.</td></tr>}
                {members !== null && members.length === 0 && <tr><td colSpan="4" className="dim">No members yet.</td></tr>}
                {members !== null && members.map((m) => {
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
