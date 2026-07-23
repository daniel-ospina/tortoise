#!/usr/bin/env python3
"""VSM Dashboard — teams, roles, Kanban boards, loops.

Generates an HTML dashboard showing the VSM structure:
  Teams → Roles → Kanban boards (cards per column) + active loops/cron.

Usage:
  python3 tortoise/dashboard.py > dashboard.html && open dashboard.html
  python3 tortoise/dashboard.py --serve  # serve on localhost:8080
"""
from __future__ import annotations

import json
import sys
import yaml
from pathlib import Path
from datetime import datetime

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_ELDATO_ROOT = _PROJECT_ROOT.parent.parent  # /Users/home/eldato when run from eldato-epistemic
_SUBJECTS_DIR = _ELDATO_ROOT / "operations" / "subjects"

if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def _load_teams() -> list[dict]:
    """Load team definitions from subjects registry."""
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
                    "belief": (rdata.get("belief", "") or "")[:80],
                })
            teams.append({
                "slug": team.get("slug", yf.stem),
                "name": team.get("name", yf.stem),
                "roles": roles,
            })
        except Exception:
            pass
    return teams


def _load_board_data() -> dict[str, dict]:
    """Load card counts per role from FalkorDB."""
    try:
        from projection import FalkorProjection
        proj = FalkorProjection(str(_PROJECT_ROOT / "tortoise.db"))
    except Exception:
        return {}

    result = proj.g.query(
        "MATCH (b:Object {objectKind: 'pm:kanbanBoard'}) "
        "RETURN b.role, b.team"
    ).result_set

    boards = {}
    for row in result:
        role, team = row[0], row[1]
        key = f"{team}/{role}"

        counts = {"pending": 0, "running": 0, "reviewing": 0, "done": 0, "failed": 0}
        for status in ["pending", "running", "reviewing", "done", "failed"]:
            c = proj.g.query(
                "MATCH (c:Object {objectKind: 'pm:card'})-[:ON_BOARD]->"
                "(b:Object {objectKind: 'pm:kanbanBoard', role: $r, team: $t}) "
                "WHERE c.status = $s RETURN count(c)",
                params={"r": role, "t": team, "s": status}
            ).result_set
            counts[status] = c[0][0] if c else 0

        boards[key] = {"role": role, "team": team, "counts": counts}

    return boards


def _load_missions() -> list[dict]:
    """Load active missions from mission_registry."""
    try:
        import sys as _sys
        coord_path = str(_ELDATO_ROOT / "operations" / "coordination")
        if coord_path not in _sys.path:
            _sys.path.insert(0, coord_path)
        from mission_registry import MissionRegistry
        reg = MissionRegistry()
        return [
            {"id": m.id, "title": m.title, "status": m.status.value,
             "assigned_role": m.assigned_role, "assigned_team": m.assigned_team}
            for m in reg.list_all()
        ]
    except Exception:
        return []


def _render(teams: list[dict], boards: dict, missions: list[dict]) -> str:
    """Render full HTML dashboard."""
    teams_json = json.dumps(teams)
    boards_json = json.dumps(boards)
    missions_json = json.dumps(missions)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>VSM Dashboard — El Dato</title>
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background:#0d1117; color:#c9d1d9; display:flex; min-height:100vh; }}
.sidebar {{ width:260px; background:#161b22; border-right:1px solid #30363d; padding:16px; overflow-y:auto; }}
.sidebar h2 {{ color:#58a6ff; font-size:14px; margin-bottom:12px; }}
.team {{ margin-bottom:8px; }}
.team-name {{ color:#c9d1d9; font-weight:600; font-size:13px; padding:6px 8px; cursor:pointer; border-radius:6px; }}
.team-name:hover {{ background:#21262d; }}
.team-name.active {{ background:#1f6feb33; color:#58a6ff; }}
.roles {{ margin-left:12px; display:none; }}
.roles.open {{ display:block; }}
.role {{ padding:4px 8px; font-size:12px; color:#8b949e; cursor:pointer; border-radius:4px; }}
.role:hover {{ background:#21262d; color:#c9d1d9; }}
.role.active {{ background:#1f6feb22; color:#58a6ff; }}
.role .badge {{ float:right; font-size:10px; background:#30363d; padding:1px 6px; border-radius:8px; }}
.main {{ flex:1; padding:24px; overflow-y:auto; }}
.header {{ margin-bottom:20px; }}
.header h1 {{ font-size:20px; }}
.header p {{ color:#8b949e; font-size:13px; margin-top:4px; }}
.board {{ display:grid; grid-template-columns: repeat(5,1fr); gap:12px; margin-top:16px; }}
.column {{ background:#161b22; border:1px solid #30363d; border-radius:8px; padding:12px; }}
.column h3 {{ font-size:13px; color:#8b949e; margin-bottom:8px; }}
.column .count {{ font-size:28px; font-weight:700; }}
.column.pending .count {{ color:#d29922; }}
.column.running .count {{ color:#58a6ff; }}
.column.reviewing .count {{ color:#a371f7; }}
.column.done .count {{ color:#3fb950; }}
.column.failed .count {{ color:#f85149; }}
.section {{ margin-top:24px; }}
.section h2 {{ font-size:16px; margin-bottom:12px; color:#58a6ff; }}
.mission {{ background:#161b22; border:1px solid #30363d; border-radius:6px; padding:10px 14px; margin-bottom:8px; }}
.mission .title {{ font-weight:600; font-size:14px; }}
.mission .meta {{ font-size:12px; color:#8b949e; margin-top:4px; }}
.status {{ display:inline-block; padding:1px 8px; border-radius:10px; font-size:11px; margin-left:8px; }}
.status.pending {{ background:#d2992233; color:#d29922; }}
.status.active {{ background:#58a6ff33; color:#58a6ff; }}
.status.completed {{ background:#3fb95033; color:#3fb950; }}
.loops-section {{ margin-top:24px; }}
.loop {{ background:#161b22; border:1px solid #30363d; border-radius:6px; padding:10px 14px; margin-bottom:8px; }}
.loop .loop-type {{ font-size:11px; color:#8b949e; }}
.empty {{ color:#484f58; font-style:italic; padding:20px; text-align:center; }}
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
  </div>
  <div id="boardContent"></div>
  <div id="missionsSection" class="section"></div>
  <div id="loopsSection" class="loops-section"></div>
</div>
<script>
const teams = {teams_json};
const boards = {boards_json};
const missions = {missions_json};

let activeTeam = null;
let activeRole = null;

function renderTeams() {{
  const el = document.getElementById('teamList');
  el.innerHTML = teams.map(t => `
    <div class="team">
      <div class="team-name" onclick="selectTeam('${{t.slug}}')" id="team-${{t.slug}}">
        ${{t.name}}
      </div>
      <div class="roles" id="roles-${{t.slug}}">
        ${{t.roles.map(r => `
          <div class="role" onclick="selectRole('${{t.slug}}', '${{r.name}}')" id="role-${{t.slug}}-${{r.name}}">
            ${{r.name}}
            ${{r.loop_type ? '<span class="badge">' + r.loop_type + '</span>' : ''}}
          </div>
        `).join('')}}
      </div>
    </div>
  `).join('');
}}

function selectTeam(slug) {{
  document.querySelectorAll('.team-name').forEach(e => e.classList.remove('active'));
  document.getElementById('team-' + slug).classList.add('active');
  document.querySelectorAll('.roles').forEach(e => e.classList.remove('open'));
  document.getElementById('roles-' + slug).classList.add('open');
  activeTeam = slug;
}}

function selectRole(team, role) {{
  document.querySelectorAll('.role').forEach(e => e.classList.remove('active'));
  document.getElementById('role-' + team + '-' + role).classList.add('active');
  activeRole = role;

  document.getElementById('boardTitle').textContent = role;
  document.getElementById('boardSubtitle').textContent = team;

  const key = team + '/' + role;
  const board = boards[key];
  const teamData = teams.find(t => t.slug === team);
  const roleData = teamData ? teamData.roles.find(r => r.name === role) : null;

  // Board
  if (board) {{
    const c = board.counts;
    document.getElementById('boardContent').innerHTML = `
      <div class="board">
        <div class="column pending"><h3>Pending</h3><div class="count">${{c.pending}}</div></div>
        <div class="column running"><h3>Running</h3><div class="count">${{c.running}}</div></div>
        <div class="column reviewing"><h3>Reviewing</h3><div class="count">${{c.reviewing}}</div></div>
        <div class="column done"><h3>Done</h3><div class="count">${{c.done}}</div></div>
        <div class="column failed"><h3>Failed</h3><div class="count">${{c.failed}}</div></div>
      </div>`;
  }} else {{
    document.getElementById('boardContent').innerHTML = '<div class="empty">No cards yet — pull missions to populate</div>';
  }}

  // Missions for this role
  const roleMissions = missions.filter(m => m.assigned_role === role || m.assigned_team === team);
  document.getElementById('missionsSection').innerHTML = roleMissions.length ? `
    <h2>Missions</h2>
    ${{roleMissions.map(m => `
      <div class="mission">
        <div class="title">${{m.title}}<span class="status ${{m.status}}">${{m.status}}</span></div>
        <div class="meta">${{m.id}} · ${{m.assigned_role}}</div>
      </div>
    `).join('')}}
  ` : '';

  // Loops for this role
  if (roleData && roleData.loop_type) {{
    document.getElementById('loopsSection').innerHTML = `
      <h2>Loops</h2>
      <div class="loop">
        <div class="loop-type">${{roleData.loop_type}}</div>
        <div style="margin-top:4px;font-size:13px;">${{roleData.belief || 'No belief statement'}}</div>
        <div class="meta" style="margin-top:4px;">Domains: ${{(roleData.domains || []).join(', ') || 'none'}}</div>
      </div>
    `;
  }} else {{
    document.getElementById('loopsSection').innerHTML = '';
  }}
}}

renderTeams();
</script>
</body>
</html>"""


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="VSM Dashboard generator")
    ap.add_argument("--serve", action="store_true", help="Serve on localhost:8080")
    ap.add_argument("-o", "--output", default="", help="Output file (default: stdout)")
    args = ap.parse_args()

    teams = _load_teams()
    boards = _load_board_data()
    missions = _load_missions()

    html = _render(teams, boards, missions)

    if args.serve:
        import http.server
        import tempfile
        tmp = Path(tempfile.mkstemp(suffix=".html")[1])
        tmp.write_text(html)
        print(f"Serving at http://localhost:8080")
        import webbrowser
        webbrowser.open(f"file://{tmp}")
        # Simple server that serves the file
        class Handler(http.server.SimpleHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-type", "text/html")
                self.end_headers()
                self.wfile.write(html.encode())
        http.server.HTTPServer(("", 8080), Handler).serve_forever()
    elif args.output:
        Path(args.output).write_text(html)
        print(f"Written to {args.output}")
    else:
        print(html)
