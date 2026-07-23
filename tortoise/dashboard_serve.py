#!/usr/bin/env python3
"""VSM Dashboard server — launch and open in browser.

Usage:
  python3 tortoise/dashboard_serve.py
  python3 tortoise/dashboard_serve.py --port 8080
"""
import json, sys, yaml, webbrowser
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_ELDATO_ROOT = _PROJECT_ROOT.parent  # eldato-epistemic lives inside eldato repo
_SUBJECTS_DIR = _ELDATO_ROOT / "operations" / "subjects"

if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def load_data():
    """Load all dashboard data from live sources."""
    # Teams from subjects registry
    teams = []
    for yf in sorted(_SUBJECTS_DIR.glob("*.yaml")):
        if yf.name.startswith("_") or yf.name == "templates":
            continue
        try:
            data = yaml.safe_load(yf.read_text())
            if not data or "team" not in data:
                continue
            team = data["team"]
            roles = []
            for rname, rdata in (data.get("roles") or {}).items():
                roles.append({
                    "name": rname,
                    "held_by": rdata.get("held_by", "?"),
                    "loop_type": rdata.get("loop_type", ""),
                    "domains": rdata.get("domains", []),
                    "belief": (rdata.get("belief", "") or "")[:120],
                })
            teams.append({
                "slug": team.get("slug", yf.stem),
                "name": team.get("name", yf.stem),
                "roles": roles,
            })
        except Exception:
            pass

    # Boards from FalkorDB
    boards = {}
    try:
        from projection import FalkorProjection
        proj = FalkorProjection(str(_PROJECT_ROOT / "tortoise.db"))
        result = proj.g.query(
            "MATCH (b:Object {objectKind: 'pm:kanbanBoard'}) RETURN b.role, b.team"
        ).result_set
        for row in result:
            role, team = row[0], row[1]
            key = f"{team}/{role}"
            counts = {"pending": 0, "running": 0, "reviewing": 0, "done": 0, "failed": 0}
            for status in counts:
                c = proj.g.query(
                    "MATCH (c:Object {objectKind: 'pm:card'})-[:ON_BOARD]->"
                    "(b:Object {objectKind: 'pm:kanbanBoard', role: $r, team: $t}) "
                    "WHERE c.status = $s RETURN count(c)",
                    params={"r": role, "t": team, "s": status}
                ).result_set
                counts[status] = c[0][0] if c else 0
            boards[key] = {"role": role, "team": team, "counts": counts}
    except Exception:
        pass

    # Missions
    missions = []
    try:
        coord_path = str(_ELDATO_ROOT / "operations" / "coordination")
        if coord_path not in sys.path:
            sys.path.insert(0, coord_path)
        from mission_registry import MissionRegistry
        reg = MissionRegistry()
        missions = [
            {"id": m.id, "title": m.title, "status": m.status.value,
             "assigned_role": m.assigned_role, "assigned_team": m.assigned_team}
            for m in reg.list_all()
        ]
    except Exception:
        pass

    return {"teams": teams, "boards": boards, "missions": missions}


HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>VSM Dashboard</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0d1117;color:#c9d1d9;display:flex;min-height:100vh}
.sidebar{width:260px;background:#161b22;border-right:1px solid #30363d;padding:16px;overflow-y:auto}
.sidebar h2{color:#58a6ff;font-size:14px;margin-bottom:12px}
.team{margin-bottom:6px}
.team-name{color:#c9d1d9;font-weight:600;font-size:13px;padding:6px 8px;cursor:pointer;border-radius:6px;user-select:none}
.team-name:hover{background:#21262d}
.team-name.active{background:#1f6feb33;color:#58a6ff}
.roles{margin-left:12px;display:none}
.roles.open{display:block}
.role{padding:4px 8px;font-size:12px;color:#8b949e;cursor:pointer;border-radius:4px}
.role:hover{background:#21262d;color:#c9d1d9}
.role.active{background:#1f6feb22;color:#58a6ff}
.role .badge{float:right;font-size:10px;background:#30363d;padding:1px 6px;border-radius:8px}
.main{flex:1;padding:24px;overflow-y:auto}
.header h1{font-size:20px}.header p{color:#8b949e;font-size:13px;margin-top:4px}
.board{display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin-top:16px}
.column{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:16px;text-align:center}
.column h3{font-size:12px;color:#8b949e;margin-bottom:4px;text-transform:uppercase;letter-spacing:0.5px}
.column .count{font-size:36px;font-weight:700}
.column.pending .count{color:#d29922}.column.running .count{color:#58a6ff}
.column.reviewing .count{color:#a371f7}.column.done .count{color:#3fb950}
.column.failed .count{color:#f85149}
.section{margin-top:24px}.section h2{font-size:16px;margin-bottom:12px;color:#58a6ff}
.mission{background:#161b22;border:1px solid #30363d;border-radius:6px;padding:10px 14px;margin-bottom:8px}
.mission .title{font-weight:600;font-size:14px}
.mission .meta{font-size:12px;color:#8b949e;margin-top:4px}
.status{display:inline-block;padding:1px 8px;border-radius:10px;font-size:11px;margin-left:8px}
.status.pending{background:#d2992233;color:#d29922}.status.active{background:#58a6ff33;color:#58a6ff}
.status.completed{background:#3fb95033;color:#3fb950}
.loop{background:#161b22;border:1px solid #30363d;border-radius:6px;padding:10px 14px;margin-bottom:8px}
.loop .loop-type{font-size:11px;color:#8b949e}
.empty{color:#484f58;font-style:italic;padding:40px;text-align:center}
.refresh-btn{background:#21262d;border:1px solid #30363d;color:#c9d1d9;padding:4px 12px;border-radius:6px;cursor:pointer;font-size:12px;float:right;margin-top:-28px}
.refresh-btn:hover{background:#30363d}
</style>
</head>
<body>
<div class="sidebar">
  <h2>Teams</h2>
  <div id="teamList"></div>
</div>
<div class="main">
  <div class="header">
    <h1 id="boardTitle">Select a role</h1>
    <p id="boardSubtitle"></p>
    <button class="refresh-btn" onclick="load()">↻ Refresh</button>
  </div>
  <div id="boardContent"></div>
  <div id="missionsSection" class="section"></div>
  <div id="loopsSection" class="section"></div>
</div>
<script>
let data = null;
let activeTeam = null;
let activeRole = null;

async function load() {
  const r = await fetch('/data');
  data = await r.json();
  renderTeams();
  if (activeTeam && activeRole) selectRole(activeTeam, activeRole);
}

function renderTeams() {
  const el = document.getElementById('teamList');
  el.innerHTML = data.teams.map(t => `
    <div class="team">
      <div class="team-name" onclick="toggleTeam('${t.slug}')" id="team-${t.slug}">
        ${t.name} (${t.roles.length})
      </div>
      <div class="roles" id="roles-${t.slug}">
        ${t.roles.map(r => `
          <div class="role" onclick="selectRole('${t.slug}','${r.name}')" id="role-${t.slug}-${r.name}">
            ${r.name}
            ${r.loop_type ? '<span class="badge">' + r.loop_type + '</span>' : ''}
          </div>
        `).join('')}
      </div>
    </div>
  `).join('');
}

function toggleTeam(slug) {
  const el = document.getElementById('roles-' + slug);
  el.classList.toggle('open');
  document.querySelectorAll('.team-name').forEach(e => e.classList.remove('active'));
  document.getElementById('team-' + slug).classList.add('active');
  activeTeam = slug;
}

function selectRole(team, role) {
  document.querySelectorAll('.role').forEach(e => e.classList.remove('active'));
  const el = document.getElementById('role-' + team + '-' + role);
  if (el) el.classList.add('active');
  activeTeam = team;
  activeRole = role;
  document.getElementById('boardTitle').textContent = role;
  document.getElementById('boardSubtitle').textContent = team;

  const key = team + '/' + role;
  const board = data.boards[key];
  const teamData = data.teams.find(t => t.slug === team);
  const roleData = teamData ? teamData.roles.find(r => r.name === role) : null;

  if (board && board.counts) {
    const c = board.counts;
    document.getElementById('boardContent').innerHTML = `
      <div class="board">
        <div class="column pending"><h3>Pending</h3><div class="count">${c.pending}</div></div>
        <div class="column running"><h3>Running</h3><div class="count">${c.running}</div></div>
        <div class="column reviewing"><h3>Reviewing</h3><div class="count">${c.reviewing}</div></div>
        <div class="column done"><h3>Done</h3><div class="count">${c.done}</div></div>
        <div class="column failed"><h3>Failed</h3><div class="count">${c.failed}</div></div>
      </div>`;
  } else {
    document.getElementById('boardContent').innerHTML = '<div class="empty">No cards yet</div>';
  }

  const roleMissions = data.missions.filter(m => m.assigned_role === role);
  document.getElementById('missionsSection').innerHTML = roleMissions.length ? `
    <h2>Missions (${roleMissions.length})</h2>
    ${roleMissions.map(m => `
      <div class="mission">
        <div class="title">${m.title}<span class="status ${m.status}">${m.status}</span></div>
        <div class="meta">${m.id}</div>
      </div>
    `).join('')}
  ` : '<div class="empty">No missions</div>';

  if (roleData && roleData.loop_type) {
    document.getElementById('loopsSection').innerHTML = `
      <h2>Loop</h2>
      <div class="loop">
        <div class="loop-type">Type: ${roleData.loop_type} · Held by: ${roleData.held_by}</div>
        <div style="margin-top:6px;font-size:13px;color:#c9d1d9">${roleData.belief || ''}</div>
        <div class="meta" style="margin-top:6px">Domains: ${(roleData.domains||[]).join(', ')||'none'}</div>
      </div>`;
  } else {
    document.getElementById('loopsSection').innerHTML = '';
  }
}

load();
</script>
</body>
</html>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/data":
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(load_data()).encode())
        else:
            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            self.wfile.write(HTML.encode())

    def log_message(self, format, *args):
        pass  # quiet


if __name__ == "__main__":
    port = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[1] == "--port" else 8080
    url = f"http://localhost:{port}"
    print(f"VSM Dashboard → {url}")
    webbrowser.open(url)
    HTTPServer(("", port), Handler).serve_forever()
