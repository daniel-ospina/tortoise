"""CLI entry point: python -m tortoise <command>"""
from __future__ import annotations

import argparse
import sys

def _cmd_rebuild(args):
    print(f"Rebuilding from {args.dir} → {args.db}")
    try:
        from tortoise.projection import FalkorProjection
        # skip_health_check: `rebuild` IS the recovery tool — a broken DB must
        # not block its own rebuild (ops safety #428).
        proj = FalkorProjection(args.db, skip_health_check=True)
        counts = proj.rebuild_all(args.dir)
        print(f"Done: {counts['nodes']} nodes, {counts['edges']} edges from {counts['events']} events")
    except ImportError as e:
        print(f"FalkorDB unavailable ({e}). Use InMemory rebuild:", file=sys.stderr)
        from tortoise.log import EventLog
        from tortoise.projection import fold
        import os
        events = []
        for f in sorted(os.listdir(args.dir)):
            if f.endswith('.jsonl'):
                events.extend(EventLog(os.path.join(args.dir, f)).read_all())
        points = fold(events)
        statements, ops = 0, 0
        for p in points.values():
            if p.get('operator'):
                ops += 1
            else:
                statements += 1
        print(f"Done: {len(points)} total ({statements} statements, {ops} operators) [in-memory, no DB]")

def _cmd_demo(args):
    from pathlib import Path
    from tempfile import NamedTemporaryFile
    from tortoise.log import EventLog
    from tortoise.api import EventAPI
    from tortoise.extractor import MockExtractor

    transcript = Path(__file__).parent.parent / "tests" / "sample_transcript.txt"
    text = transcript.read_text(encoding="utf-8")

    with NamedTemporaryFile(suffix=".jsonl", mode="w+", delete=False) as tmp:
        tmp.close()
        log = EventLog(tmp.name)
        api = EventAPI(log, initiated_by="extractor", agent_id="mock@0")
        MockExtractor().run(text, transcript.name, api)
        events = log.read_all()
        Path(tmp.name).unlink()

    points, operators = {}, []
    for ev in events:
        if ev["type"] == "PointAdded":
            p = ev["point"]
            points[p["id"]] = p
        elif ev["type"] == "OperatorAdded":
            operators.append(ev["point"])

    print(f"{'='*60}")
    print(f"Tortoise Demo \u2014 {transcript.name}")
    print(f"Extracted {len(points)} statements, {len(operators)} connections")
    print(f"{'='*60}")

    # Build ID -> label lookup
    lookup = {}
    for pid, p in points.items():
        prov = p.get("provenance", {})
        lookup[pid] = f"{prov.get('speaker','?')}: {prov.get('quote', p['content'])}"

    # Print statements in order of utterance (by span start)
    ordered = sorted(points.items(), key=lambda kv: kv[1]["provenance"]["span"][0])
    for pid, p in ordered:
        prov = p["provenance"]
        print(f"\n  [{prov['speaker']}] {prov['quote']}")

    if operators:
        _sep = '─'*40
        print(f"\n{_sep}")
        print("Connections:")
        for op in operators:
            op_data = op["operator"]
            label = "supports" if op_data["op_type"] == "IMPL" else "contradicts"
            src = lookup.get(op_data["inputs"][0], op_data["inputs"][0])
            dst = lookup.get(op_data["inputs"][1], op_data["inputs"][1])
            _ell = '\u2026'
            print(f"  {op_data['op_type']}: \u201c{src[:70]}{_ell if len(src)>70 else ''}\u201d")
            print(f"        {label} \u2192 \u201c{dst[:70]}{_ell if len(dst)>70 else ''}\u201d")
    print()


def _cmd_mine_conversation(args):
    """Mine conversation transcript → Events + Points (GAP-15 / #7003)."""
    import json
    from pathlib import Path
    from tempfile import NamedTemporaryFile

    from tortoise.api import EventAPI
    from tortoise.log import EventLog
    from tortoise.mining import ConversationMiner
    from tortoise.projection import FalkorProjection

    transcript_path = Path(args.transcript)
    if not transcript_path.exists():
        print(f"Transcript not found: {args.transcript}", file=sys.stderr)
        return 1

    source_id = args.source_id or transcript_path.stem
    text = transcript_path.read_text(encoding="utf-8")

    # Set up log + projection
    proj = None
    log_path = Path(f"mine-{source_id}.jsonl")
    if args.db:
        try:
            proj = FalkorProjection.from_uri(args.db)
        except Exception as e:
            print(f"Warning: FalkorDB unavailable ({e}), using log-only mode")

    log = EventLog(str(log_path))
    api = EventAPI(log, initiated_by="extractor", agent_id="mining-pilot", projection=proj)

    # Disable idempotency for mining (always process fresh)
    api._ingest_cache = {}

    miner = ConversationMiner()
    result = miner.mine(text, source_id, api)

    if proj:
        proj.close()

    events = log.read_all()
    event_entries = [e for e in events if e["type"] == "EventRecorded"]
    point_entries = [e for e in events if e["type"] == "PointAdded"]
    op_entries = [e for e in events if e["type"] == "OperatorAdded"]

    print(f"{'='*60}")
    print(f"Conversation Mining — {source_id}")
    print(f"{'='*60}")
    print(f"Events:    {len(event_entries)} (gate: >=3)")
    print(f"Points:    {len(point_entries)}")
    print(f"Operators: {len(op_entries)}")
    print(f"Log:       {log_path}")
    print()

    if len(event_entries) < 3:
        print(f"\u26a0\ufe0f  GATE FAILED: {len(event_entries)} events < 3 minimum")
        print(f"   Per plan WF4: <3 events/session → permanently descoped.")
    else:
        print(f"\u2705  GATE PASSED: {len(event_entries)} events")

    print()
    for ev in event_entries:
        e = ev.get("event", {})
        kind = e.get("eventKind", "unknown")
        obj = e.get("object", "")[:80]
        print(f"  [{kind}] {obj}..." if len(e.get("object","")) > 80 else f"  [{kind}] {obj}")
    print()

    return result


def _cmd_reconcile(args):
    """Replay unprojected EventRecorded entries from JSONL into FalkorDB."""
    import json, sys
    from pathlib import Path

    if not args.db.startswith("docker://"):
        print("Error: reconcile requires docker:// URI (e.g. docker://:pass@localhost:6379)", file=sys.stderr)
        return 1

    log_path = Path(args.log)
    if not log_path.exists():
        print(f"No event log found at {args.log}. Nothing to reconcile.", file=sys.stderr)
        return 1

    try:
        from tortoise.log import EventLog
        from tortoise.projection import FalkorProjection
    except ImportError:
        print("Tortoise not installed. Run: pip install -e negation-game-explorations/tortoise", file=sys.stderr)
        return 1

    events = EventLog(log_path).read_all()

    proj = None
    try:
        proj = FalkorProjection.from_uri(args.db)

        event_entries = [ev for ev in events if ev["type"] == "EventRecorded"]
        all_ids = [ev["event"]["eventId"] for ev in event_entries]
        existing: set[str] = set()
        for i in range(0, len(all_ids), 500):
            batch = all_ids[i:i+500]
            rows = proj.g.query(
                "UNWIND $ids AS eid MATCH (e:Event {eventId:eid}) RETURN e.eventId",
                params={"ids": batch}
            ).result_set
            existing.update(r[0] for r in rows if r)

        applied = 0
        for ev in event_entries:
            if ev["event"]["eventId"] not in existing:
                proj.apply(ev)
                applied += 1

        total = len(event_entries)
        print(f"Reconciled {applied} events ({total - applied} already projected — {total} total)")
    finally:
        if proj:
            proj.close()
    return 0



def _cmd_init(args):
    """Auto-detect FalkorDB and create default graph — onboarding."""
    import json as _json
    import os, tempfile
    from pathlib import Path

    # ── Cloud mode (--api-key) ──────────────────────────────────
    if getattr(args, 'api_key', None):
        config_path = Path.cwd() / ".tortoise"
        if config_path.exists():
            existing = _json.loads(config_path.read_text())
            if existing.get("api_key") == args.api_key:
                print("Already connected to Tortoise Cloud with this API key.")
                return 0
            print(f"⚠️  Existing .tortoise config found — overwriting.")
        config = {
            "api_key": args.api_key,
            "api_url": os.environ.get("TORTOISE_API_URL", "https://api.premiselabs.co"),
        }

        # Validate the key against the hosted API BEFORE saving (#707).
        # 401/403 → hard fail (never silently write an invalid key);
        # network errors → warn + save (cannot validate offline).
        from urllib.request import Request, urlopen
        from urllib.error import URLError, HTTPError
        try:
            req = Request(
                f"{config['api_url']}/v1/team",
                headers={"Authorization": f"Bearer {args.api_key}"},
            )
            with urlopen(req, timeout=10) as resp:
                _json.loads(resp.read())
            print("✅ API key validated against Tortoise Cloud")
        except HTTPError as e:
            body = ""
            try:
                body = e.read().decode()
            except Exception:
                pass
            print(f"❌ API rejected the key ({e.code}): {body.strip() or e.reason}", file=sys.stderr)
            print(f"   Config NOT saved. Double-check the key or run:", file=sys.stderr)
            print(f"   tortoise init --api-key tt_<key>", file=sys.stderr)
            return 1
        except (URLError, ValueError) as e:
            reason = getattr(e, "reason", e)
            print(f"⚠️  Could not reach {config['api_url']} to validate the key ({reason}).")
            print(f"   Saving config anyway — verify with: tortoise team info")
        config_path.write_text(_json.dumps(config, indent=2) + "\n")
        os.chmod(config_path, 0o600)
        print("Connected to Tortoise Cloud (team will be resolved from API key)")
        print(f"Config saved to {config_path}")
        print("⚠️  .tortoise contains a plaintext API key — do NOT commit this file.")
        print()
        print("Next steps:")
        print("  tortoise session capture --file transcript.txt   # capture an agent session")
        print("  tortoise session list                             # view your sessions")
        print("  # Connect your agent via MCP (see welcome page)")
        return 0

    print("Tortoise init — auto-detecting FalkorDB…")

    # 1. Try Docker FalkorDB
    docker_host = os.environ.get("FALKORDB_HOST", "localhost")
    docker_pass = os.environ.get("FALKORDB_PASSWORD", "")

    try:
        docker_port = int(os.environ.get("FALKORDB_PORT", "16379"))
    except (ValueError, TypeError):
        print(f"  ❌ Invalid FALKORDB_PORT: {os.environ.get('FALKORDB_PORT')!r}. Must be an integer.")
        return 1

    graph_ready = False
    docker_detected = False

    try:
        from falkordb import FalkorDB
        db = FalkorDB(host=docker_host, port=docker_port,
                      password=docker_pass or None)
        db.select_graph("tortoise").query("RETURN 1")
        print(f"  ✅ Docker FalkorDB detected at {docker_host}:{docker_port}")
        graph_ready = True
        docker_detected = True
    except ImportError:
        pass
    except (ConnectionError, ConnectionRefusedError, OSError) as e:
        print(f"  ⚠️  Docker FalkorDB unreachable at {docker_host}:{docker_port}: {e}")
    except Exception as e:
        err = str(e).lower()
        if "auth" in err or "password" in err:
            print(f"  ⚠️  Docker FalkorDB auth failed — check FALKORDB_PASSWORD env var")
        else:
            print(f"  ⚠️  Docker FalkorDB unreachable ({e})")

    # 2. Fallback: embedded mode (SQLite-backed)
    if not graph_ready:
        from tortoise.config import resolve_db_path
        db_path = args.path or resolve_db_path()
        try:
            from tortoise.projection import FalkorProjection
            _proj = FalkorProjection(db_path)
            _proj.g.query("RETURN 1")
            print(f"  ✅ Embedded mode initialized at {db_path}")
            graph_ready = True
        except ImportError:
            print(f"  ❌ Neither falkordb nor redislite installed.")
            print(f"     pip install falkordb       # for Docker mode (FalkorProjection)")
            print(f"     pip install redislite      # for embedded mode (FalkorProjection)")
            return 1
        except Exception as e:
            print(f"  ❌ Embedded mode init failed: {e}")
            return 1

    if not graph_ready:
        return 1

    # Write welcome Point to the graph
    try:
        from tortoise.sdk import TortoiseSDK
        if docker_detected:
            os.environ.setdefault("TORTOISE_DB_URI",
                f"docker://:{docker_pass}@{docker_host}:{docker_port}/tortoise")
            sdk = TortoiseSDK()
        else:
            sdk = TortoiseSDK(db_path=db_path)
        sdk.create_point(
            kind="observation",
            content="Tortoise graph initialized — file decisions and observations here so your agents remember across sessions.",
            tags=["system", "welcome"],
        )
        status = sdk.status()
        point_count = status.get("counts", {}).get("Point", 0)
    except Exception:
        point_count = "?"

    print(f"  Graph: tortoise  |  Points: {point_count}")
    print()
    print("Graph ready. The graph starts empty — it fills as you and your agents")
    print("file decisions, observations, and findings.")
    print()
    print("Next steps:")
    print("  tortoise setup              — configure per-role memory (~2 min, optional)")
    print("  tortoise doctor             — verify everything is healthy")
    print("  tortoise serve              — start MCP server for agents")

    # Onboarding: detect git repo and offer indexing
    import subprocess as _sp
    import sys as _sys
    result = _sp.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True, text=True, timeout=5,
    )
    if result.returncode == 0:
        repo_root = result.stdout.strip()
        md_count = len(list(__import__("pathlib").Path(repo_root).rglob("*.md")))
        auto_index = getattr(args, 'yes', False)
        if md_count > 0:
            if auto_index:
                print(f"\nFound {md_count} markdown files in this repo. Auto-indexing…")
                log_f = open(str(Path(tempfile.gettempdir()) / "tortoise-init-index.log"), 'w')
                _sp.Popen(
                    [_sys.executable, "-m", "tortoise", "index", "github", repo_root],
                    stdout=log_f, stderr=_sp.STDOUT,
                    start_new_session=True,
                )
                print("Indexing in background. Tortoise is ready to use immediately.")
            else:
                print()
                yn = input(f"Found {md_count} markdown files in this repo. Index them into Tortoise? [Y/n]: ").strip().lower()
                if yn != "n":
                    print("Launching indexer in background…")
                    log_f = open(str(Path(tempfile.gettempdir()) / "tortoise-init-index.log"), 'a')
                    _sp.Popen(
                        [_sys.executable, "-m", "tortoise", "index", "github", repo_root],
                        stdout=log_f, stderr=_sp.STDOUT,
                        start_new_session=True,
                    )
                    print("Indexing in background. Tortoise is ready to use immediately.")
    return 0


def _cmd_signup(args) -> int:
    """Zero-email signup (issue #663): mint a working tt_ key from the CLI.

    No email, no dashboard, no Supabase account — the agent/CLI equivalent
    of Mem0's 4-command key mint and Hindsight's npx self-install. Saves
    the config to .tortoise so `tortoise create-point` etc. work immediately.
    """
    import json, sys, uuid
    from pathlib import Path
    from urllib.request import Request, urlopen
    from urllib.error import URLError, HTTPError

    api_url = os.environ.get("TORTOISE_API_URL", "https://api.premiselabs.co")
    device_id = f"anon-{uuid.uuid4().hex[:12]}"

    print(f"Signing up for a free hosted team (anonymous, no email)…")
    try:
        req = Request(
            f"{api_url}/v1/agent/signup",
            data=json.dumps({"identity": device_id}).encode(),
            headers={"Content-Type": "application/json", "X-Device-Id": device_id},
            method="POST",
        )
        with urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
    except HTTPError as e:
        body = e.read().decode() if e.fp else ""
        print(f"Signup failed ({e.code}): {body}", file=sys.stderr)
        return 1
    except (URLError, ValueError, json.JSONDecodeError) as e:
        print(f"Cannot reach API at {api_url}: {e}", file=sys.stderr)
        return 1

    # Save config so the key works immediately
    config_path = Path.cwd() / ".tortoise"
    config = {
        "api_key": data["key"],
        "api_url": api_url,
        "team_id": data["team_id"],
        "team_name": data["team_name"],
    }
    config_path.write_text(json.dumps(config, indent=2) + "\n")
    os.chmod(config_path, 0o600)

    print(f"✅ Free team created: {data['team_name']}")
    print(f"   API key: {data['key']}")
    print(f"   Config saved to {config_path} (shown once — store it)")
    print(f"   Next: tortoise create-point \"hello world\" --kind statement")
    return 0


def _cmd_team_info(args) -> int:
    """Show team info from Tortoise Cloud API."""
    import json, sys
    from pathlib import Path
    from urllib.request import Request, urlopen
    from urllib.error import URLError, HTTPError

    # Read config
    config_path = Path.cwd() / ".tortoise"
    if not config_path.exists():
        print("No .tortoise config found. Run 'tortoise init --api-key <key>' first.", file=sys.stderr)
        return 1

    try:
        config = json.loads(config_path.read_text())
    except json.JSONDecodeError:
        print(f"Invalid .tortoise config at {config_path}", file=sys.stderr)
        return 1

    api_key = config.get("api_key")
    api_url = config.get("api_url") or os.environ.get("TORTOISE_API_URL", "https://api.premiselabs.co")
    if not api_key:
        print("No api_key in .tortoise config.", file=sys.stderr)
        return 1

    # Call API
    try:
        req = Request(
            f"{api_url}/v1/team",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        with urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
    except (json.JSONDecodeError, ValueError) as e:
        print(f"Invalid response from API: {e}", file=sys.stderr)
        return 1
    except HTTPError as e:
        body = e.read().decode() if e.fp else ""
        print(f"API error ({e.code}): {body}", file=sys.stderr)
        return 1
    except URLError as e:
        print(f"Cannot reach API at {api_url}: {e.reason}", file=sys.stderr)
        return 1

    print(f"Team:       {data.get('team_id', '?')}")
    print(f"Tier:       {data.get('tier', 'free')}")
    print(f"Points:     {data.get('point_count', 0)}")
    print(f"Max users:  {data.get('max_users', 1)}")
    print(f"Max graphs: {data.get('max_graphs', 1)}")
    return 0


def _cmd_create_point(args) -> int:
    """Create a Point via Tortoise Cloud API."""
    import json as _json, os, sys as _sys
    from pathlib import Path
    from urllib.request import Request, urlopen
    from urllib.error import URLError, HTTPError

    # Read config
    config_path = Path.cwd() / ".tortoise"
    if not config_path.exists():
        print("No .tortoise config found. Run 'tortoise init --api-key <key>' first.", file=_sys.stderr)
        return 1

    try:
        config = _json.loads(config_path.read_text())
    except _json.JSONDecodeError:
        print(f"Invalid .tortoise config at {config_path}", file=_sys.stderr)
        return 1

    api_key = config.get("api_key")
    api_url = config.get("api_url") or os.environ.get("TORTOISE_API_URL", "https://api.premiselabs.co")
    if not api_key:
        print("No api_key in .tortoise config.", file=_sys.stderr)
        return 1

    payload = {
        "content": args.content,
        "kind": args.kind or "statement",
    }

    try:
        data = _json.dumps(payload).encode("utf-8")
        req = Request(
            f"{api_url}/v1/points",
            data=data,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urlopen(req, timeout=30) as resp:
            result = _json.loads(resp.read())
    except HTTPError as e:
        body = e.read().decode() if e.fp else ""
        print(f"API error ({e.code}): {body}", file=_sys.stderr)
        return 1
    except URLError as e:
        print(f"Cannot reach API at {api_url}: {e.reason}", file=_sys.stderr)
        return 1

    point_id = result.get("id", result.get("point_id", "unknown"))
    print(f"Created point: {point_id}")
    print(f"  Kind: {args.kind or 'statement'}")
    content_preview = args.content[:100] + "..." if len(args.content) > 100 else args.content
    print(f"  Content: {content_preview}")
    return 0


def _cmd_context(args) -> int:
    """Print a compact memory digest for agent session-start injection.

    Used by the Claude Code SessionStart hook: stdout from this command is
    injected into the session context automatically, so the agent starts
    each session knowing what Tortoise already remembers.

    Hosted mode (\".tortoise\" config): calls the hosted API.
    Local mode (embedded/Docker): uses TortoiseSDK.session_context().
    """
    import json as _json, os as _os, sys as _sys
    from pathlib import Path

    config_path = Path.cwd() / ".tortoise"
    api_key = None
    api_url = _os.environ.get("TORTOISE_API_URL", "https://api.premiselabs.co")

    if config_path.exists():
        try:
            cfg = _json.loads(config_path.read_text())
            api_key = cfg.get("api_key")
            api_url = cfg.get("api_url") or api_url
        except _json.JSONDecodeError:
            pass

    if api_key:
        # ── Hosted: query the API ──
        from urllib.request import Request, urlopen
        from urllib.error import URLError, HTTPError
        try:
            req = Request(
                f"{api_url}/v1/context",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            with urlopen(req, timeout=15) as resp:
                data = _json.loads(resp.read())
        except HTTPError as e:
            body = e.read().decode() if e.fp else ""
            print(f"Cannot reach Tortoise API ({e.code}): {body}", file=_sys.stderr)
            return 1
        except (URLError, ValueError) as e:
            print(f"Cannot reach Tortoise API: {e.reason if hasattr(e,'reason') else e}", file=_sys.stderr)
            return 1
    else:
        # ── Local: SDK (embedded or TORTOISE_DB_URI) ──
        try:
            from tortoise.sdk import TortoiseSDK
            if _os.environ.get("TORTOISE_DB_URI"):
                sdk = TortoiseSDK()
            else:
                sdk = TortoiseSDK(db_path=_os.environ.get("TORTOISE_DB_PATH"))
            data = sdk.session_context()
        except Exception as e:
            print(f"Tortoise unavailable: {e}", file=_sys.stderr)
            return 1

    if data.get("no_prior_sessions") or not (data.get("diary_entries") or data.get("recent_points") or data.get("recent_events")):
        print("<Tortoise memory is empty — no prior sessions yet.>")
        return 0

    print("# Tortoise memory (from previous sessions)")

    diary = data.get("diary_entries") or []
    if diary:
        print()
        print("## Recent diary")
        for p in diary[:5]:
            ts = (p.get("createdAt") or "")[:10]
            print(f"- [{ts}] {p.get('content','')[:160]}")

    points = data.get("recent_points") or []
    if points:
        print()
        print("## Recent points/decisions")
        for p in points[:8]:
            kind = p.get("pointKind", "point")
            print(f"- ({kind}) {p.get('content','')[:160]}")

    conf = data.get("confidence_changes") or []
    if conf:
        print()
        print("## Confidence-tracked claims")
        for c in conf[:5]:
            print(f"- [{c.get('confidence',0):.2f}] {c.get('content','')[:120]}")

    print()
    print("Ask me about any of the above; file new decisions with tortoise_create_point.")
    return 0


def _cmd_session(args) -> int:
    """Manage Tortoise Cloud sessions."""
    import json, os, sys
    from pathlib import Path
    from urllib.request import Request, urlopen
    from urllib.error import URLError, HTTPError

    # Read config
    config_path = Path.cwd() / ".tortoise"
    if not config_path.exists():
        print("No .tortoise config found. Run 'tortoise init --api-key <key>' first.", file=sys.stderr)
        return 1

    try:
        config = json.loads(config_path.read_text())
    except json.JSONDecodeError:
        print(f"Invalid .tortoise config at {config_path}", file=sys.stderr)
        return 1

    api_key = config.get("api_key")
    api_url = config.get("api_url") or os.environ.get("TORTOISE_API_URL", "https://api.premiselabs.co")
    if not api_key:
        print("No api_key in .tortoise config.", file=sys.stderr)
        return 1

    if args.session_cmd == "capture":
        return _cmd_session_capture(args, api_key, api_url)
    elif args.session_cmd == "list":
        return _cmd_session_list(api_key, api_url)
    elif args.session_cmd == "view":
        return _cmd_session_view(args, api_key, api_url)
    else:
        print("Unknown session command. Try capture, list, or view.", file=sys.stderr)
        return 1


def _parse_transcript(text: str) -> list:
    """Parse a transcript file into conversation turns.

    Handles common speaker prefixes: User:, Human:, Me:, Assistant:, AI:, You:, System:
    """
    turns = []
    current_role = None
    current_lines = []

    for line in text.split('\n'):
        stripped = line.strip()
        lower = stripped.lower()
        if any(lower.startswith(p) for p in ('user:', 'human:', 'me:')):
            if current_role is not None and current_lines:
                turns.append({"role": current_role, "content": '\n'.join(current_lines).strip()})
                current_lines = []
            colon = stripped.index(':')
            current_role = 'user'
            content = stripped[colon + 1:].strip()
            if content:
                current_lines.append(content)
        elif any(lower.startswith(p) for p in ('assistant:', 'ai:', 'you:')):
            if current_role is not None and current_lines:
                turns.append({"role": current_role, "content": '\n'.join(current_lines).strip()})
                current_lines = []
            colon = stripped.index(':')
            current_role = 'assistant'
            content = stripped[colon + 1:].strip()
            if content:
                current_lines.append(content)
        elif lower.startswith('system:'):
            if current_role is not None and current_lines:
                turns.append({"role": current_role, "content": '\n'.join(current_lines).strip()})
                current_lines = []
            colon = stripped.index(':')
            current_role = 'system'
            content = stripped[colon + 1:].strip()
            if content:
                current_lines.append(content)
        elif stripped:
            if current_role is not None:
                current_lines.append(stripped)

    if current_role is not None and current_lines:
        turns.append({"role": current_role, "content": '\n'.join(current_lines).strip()})

    return turns


def _cmd_session_capture(args, api_key: str, api_url: str) -> int:
    """Read transcript, parse into turns, POST to /v1/sessions."""
    import json as _json, sys as _sys
    from pathlib import Path
    from urllib.request import Request, urlopen
    from urllib.error import URLError, HTTPError

    transcript_path = Path(args.file)
    if not transcript_path.exists():
        print(f"Transcript file not found: {args.file}", file=_sys.stderr)
        return 1

    text = transcript_path.read_text(encoding="utf-8")
    turns = _parse_transcript(text)

    if not turns:
        print("No conversation turns found in transcript.", file=_sys.stderr)
        return 1

    payload = {
        "source": transcript_path.stem,
        "conversation": turns,
    }

    try:
        data = _json.dumps(payload).encode("utf-8")
        req = Request(
            f"{api_url}/v1/sessions",
            data=data,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urlopen(req, timeout=30) as resp:
            result = _json.loads(resp.read())
    except HTTPError as e:
        body = e.read().decode() if e.fp else ""
        print(f"API error ({e.code}): {body}", file=_sys.stderr)
        return 1
    except URLError as e:
        print(f"Cannot reach API at {api_url}: {e.reason}", file=_sys.stderr)
        return 1

    session_id = result.get("session_id", result.get("id", "unknown"))
    print(f"Captured session: {session_id}")
    print(f"  Turns: {len(turns)}")
    print(f"  Source: {transcript_path.stem}")
    return 0


def _cmd_session_list(api_key: str, api_url: str) -> int:
    """GET /v1/sessions — list all sessions."""
    import json as _json, sys as _sys
    from urllib.request import Request, urlopen
    from urllib.error import URLError, HTTPError

    try:
        req = Request(
            f"{api_url}/v1/sessions",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        with urlopen(req, timeout=10) as resp:
            data = _json.loads(resp.read())
    except HTTPError as e:
        body = e.read().decode() if e.fp else ""
        print(f"API error ({e.code}): {body}", file=_sys.stderr)
        return 1
    except URLError as e:
        print(f"Cannot reach API at {api_url}: {e.reason}", file=_sys.stderr)
        return 1

    sessions = data if isinstance(data, list) else data.get("sessions", [])
    if not sessions:
        print("No sessions found.")
        return 0

    print(f"{'ID':<36} {'Turns':<6} {'Created'}")
    print("-" * 60)
    for s in sessions:
        sid = s.get("id", s.get("session_id", "?"))
        turns = s.get("turns", s.get("turn_count", "?"))
        created = s.get("created_at", s.get("created", ""))[:19]
        print(f"{sid:<36} {str(turns):<6} {created}")
    return 0


def _cmd_session_view(args, api_key: str, api_url: str) -> int:
    """GET /v1/sessions/<id> — view session details."""
    import json as _json, sys as _sys
    from urllib.request import Request, urlopen
    from urllib.error import URLError, HTTPError

    session_id = args.id
    try:
        req = Request(
            f"{api_url}/v1/sessions/{session_id}",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        with urlopen(req, timeout=10) as resp:
            data = _json.loads(resp.read())
    except HTTPError as e:
        body = e.read().decode() if e.fp else ""
        print(f"API error ({e.code}): {body}", file=_sys.stderr)
        return 1
    except URLError as e:
        print(f"Cannot reach API at {api_url}: {e.reason}", file=_sys.stderr)
        return 1

    print(f"Session: {session_id}")
    print(f"Created: {data.get('created_at', data.get('created', '?'))}")
    turns = data.get("turns", [])
    print(f"Turns:   {len(turns)}")
    print()
    for i, t in enumerate(turns):
        role = t.get("role", "?").upper()
        content = t.get("content", "")
        if len(content) > 200:
            content = content[:200] + "..."
        print(f"[{i + 1}] {role}: {content}")
    return 0


def _cmd_onboard(args) -> int:
    """Guided onboarding: init → index → demo → doctor.

    Chains existing commands into a cohesive flow.
    Non-interactive — skips prompts, just runs.
    Idempotent — re-running skips already-done steps.
    """
    import os
    import subprocess as _sp
    import sys as _sys
    from pathlib import Path

    step = 0
    total = 5

    def banner(title: str):
        nonlocal step
        step += 1
        print(f"\n{'─'*50}")
        print(f"Step {step}/{total}: {title}")
        print(f"{'─'*50}")

    # Step 1: Ensure SDK installed
    banner("Ensure Tortoise SDK is installed")
    try:
        import tortoise
        print(f"  ✅ Tortoise {tortoise.__version__ if hasattr(tortoise, '__version__') else 'installed'}")
    except ImportError:
        print("  ❌ Tortoise not installed. Run: pip install -e .")
        return 1

    # Step 2: Init — auto-detect FalkorDB, create graph
    banner("Initialize graph")
    rc = _cmd_init(argparse.Namespace(path=getattr(args, 'path', None), yes=True))
    if rc != 0:
        print("  ❌ Init failed")
        return rc

    # Step 3: Index current repo
    banner("Index repository")
    result = _sp.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True, text=True, timeout=5,
        cwd=Path.cwd(),
    )
    if result.returncode == 0:
        repo_root = result.stdout.strip()
        md_count = len(list(Path(repo_root).rglob("*.md")))
        if md_count > 0:
            print(f"  Found {md_count} markdown files. Indexing…")
            idx_args = argparse.Namespace(
                url=repo_root, background=False, branch="main",
                index_cmd="github", cmd="index",
            )
            _cmd_index_github(idx_args)
        else:
            print("  ⊙ No markdown files found — skipping index.")
    else:
        print("  ⊙ Not a git repo — skipping index.")

    # Step 4: First demo — create first memory
    banner("First memory demo")
    _cmd_demo(argparse.Namespace(cmd="demo"))

    # Step 5: Doctor — health check (informational, don't fail on warnings)
    banner("Health check")
    _cmd_doctor(argparse.Namespace(cmd="doctor"))

    print(f"\n{'='*50}")
    print("Onboarding complete.")
    print()
    print("Tortoise is ready. Agents can now:")
    print("  • Query the graph via tortoise_suggest_entry_points()")
    print("  • File decisions with tortoise_create_point()")
    print("  • Auto-capture via tortoise-context extension")
    print()
    print("Next: tortoise serve    — start MCP server for agents")
    print("      tortoise setup    — configure per-role memory")
    print()
    # #544: reference the canonical onboarding prompt — paste into your agent
    # after connecting it to the local MCP server to run the same yes/no flow
    # hosted users get (same tool names over stdio).
    print("Onboarding prompt — paste this into your agent to complete setup:")
    print("  https://premiselabs.co/onboarding-prompt.md")
    return 0


def _cmd_verify(args):
    """Write, read, delete a test Point — health check."""
    from .projection import FalkorProjection
    proj = FalkorProjection.from_uri(args.db)
    try:
        proj.apply([{"type": "PointAdded", "point": {"id": "test-verify", "content": "verify", "pointKind": "observation", "createdAt": "2026-01-01T00:00:00Z"}}])
        print("✓ write OK")
        result = proj.db.query("MATCH (p:Point {id: 'test-verify'}) RETURN p")
        print("✓ read OK" if result.result_set else "✗ read FAILED")
        proj.db.query("MATCH (p:Point {id: 'test-verify'}) DELETE p")
        print("✓ delete OK")
    except Exception as e:
        print(f"✗ {e}")
        return 1
    finally:
        proj.close()
    return 0


def _cmd_backfill(args):
    """Backfill missing properties on existing Points."""
    from .projection import FalkorProjection, _now_iso
    proj = FalkorProjection(args.db)
    try:
        r = proj.g.query("MATCH (p:Point) WHERE p.status IS NULL SET p.status = 'live' RETURN count(p)").result_set
        status_count = r[0][0] if r else 0
        r = proj.g.query(
            "MATCH (p:Point) WHERE p.createdAt IS NULL SET p.createdAt = $now RETURN count(p)",
            params={"now": _now_iso()},
        ).result_set
        created_count = r[0][0] if r else 0
        print(f"Backfilled: {status_count} status + {created_count} createdAt")
    finally:
        proj.close()


def _cmd_setup(args) -> int:
    """Interactive memory_filter configuration per role.

    tortoise setup                  — interactive prompts
    tortoise setup --role developer --team app  — non-interactive, prints YAML
    tortoise setup --role developer --team app --output config.yaml  — saves to file
    """
    try:
        import yaml
    except ImportError:
        print("Error: PyYAML is required. Run: pip install PyYAML", file=sys.stderr)
        return 1

    if args.role:
        # Non-interactive: generate default config for a role
        if not args.team:
            print("Error: --team is required with --role", file=sys.stderr)
            return 1
        config = _default_memory_filter(args.role)
        output = {
            "team": args.team,
            "role": args.role,
            "memory_filter": config,
        }
        yaml_text = yaml.dump(output, default_flow_style=False, sort_keys=False, allow_unicode=True)
        if args.output:
            try:
                with open(args.output, "w") as f:
                    f.write("# Tortoise memory_filter config\n")
                    f.write(f"# Role: {args.role}  Team: {args.team}\n")
                    f.write(yaml_text)
                print(f"Saved to {args.output}")
            except OSError as e:
                print(f"Error writing {args.output}: {e}", file=sys.stderr)
                return 1
        else:
            print(yaml_text)
        return 0

    # Interactive mode
    from pathlib import Path
    home = Path.home()

    print("Tortoise Setup — Agent Memory Configuration")
    print("=" * 50)
    print()

    # ── Harness detection ──────────────────────────────────────
    detections: dict[str, bool] = {}
    if (home / ".pi" / "agent" / "extensions" / "tortoise-context").exists():
        detections["pi"] = True
    if (home / ".claude").exists() or Path(".claude").exists():
        detections["claude"] = True
    if (home / ".codex").exists() or Path(".codex").exists():
        detections["codex"] = True
    if Path(".cursor").exists():
        detections["cursor"] = True

    print("Which agent harness are you using?")
    opts = []
    if detections.get("pi"):
        opts.append("[1] Pi (detected)")
    else:
        opts.append("[1] Pi")
    if detections.get("claude"):
        opts.append("[2] Claude Code (detected)")
    else:
        opts.append("[2] Claude Code")
    if detections.get("codex"):
        opts.append("[3] Codex (detected)")
    else:
        opts.append("[3] Codex")
    if detections.get("cursor"):
        opts.append("[4] Cursor (detected)")
    else:
        opts.append("[4] Cursor")
    opts.append("[5] Multiple — I use several")
    opts.append("[6] Skip — just configure memory, no harness setup")
    for o in opts:
        print(f"  {o}")

    choice = input("\n> ").strip()
    harness = None
    harness_names = {"1": "pi", "2": "claude", "3": "codex", "4": "cursor"}
    if choice in harness_names:
        harness = harness_names[choice]
    elif choice == "5":
        harness = "multiple"
    elif choice == "6":
        harness = None
    else:
        harness = "pi"  # default

    print()

    # ── Role config ─────────────────────────────────────────────
    print("Configure what each role remembers from the graph.")
    print("memory_filter is a FLOOR, not a CEILING — agents can always query more.")
    print()

    role_name = input("Role name (e.g., developer, researcher): ").strip()
    if not role_name:
        print("No role entered. Skipping.")
        return 0

    team_name = input("Team name (e.g., app, org-design): ").strip() or role_name

    config = {}

    # Episodic
    print()
    print("─ Episodic Memory (session history, events) ─")
    yn = input("  Include last N sessions? [Y/n]: ").strip().lower()
    if yn != "n":
        n = input("  How many sessions? [3]: ").strip()
        try:
            n_val = int(n) if n else 3
        except ValueError:
            n_val = 3
        epic = input("  Filter by active epic? [Y/n]: ").strip().lower()
        config["episodic"] = {
            "last_n_sessions": n_val,
            "filter_by_epic": epic != "n",
        }

    # Epistemic
    print()
    print("─ Epistemic Memory (claims, evidence, confidence) ─")
    yn = input("  Include epistemic memory? [Y/n]: ").strip().lower()
    if yn != "n":
        conf = input("  Minimum confidence [0.5]: ").strip()
        try:
            conf_val = float(conf) if conf else 0.5
        except ValueError:
            conf_val = 0.5
        age = input("  Max age in days [30]: ").strip()
        try:
            age_val = int(age) if age else 30
        except ValueError:
            age_val = 30
        kinds = input("  Include kinds (comma-separated) [decision,observation,hypothesis]: ").strip()
        kind_list = [k.strip() for k in kinds.split(",") if k.strip()] if kinds else ["decision", "observation", "hypothesis"]
        config["epistemic"] = {
            "min_confidence": conf_val,
            "max_age_days": age_val,
            "include_kinds": kind_list,
        }

    # Semantic
    print()
    print("─ Semantic Memory (facts, decisions, plans) ─")
    yn = input("  Include decisions? [Y/n]: ").strip().lower()
    dec = yn != "n"
    yn = input("  Include plans? [y/N]: ").strip().lower()
    plans = yn == "y"
    if dec or plans:
        config["semantic"] = {
            "include_decisions": dec,
            "include_plans": plans,
        }

    # Procedural
    print()
    print("─ Procedural Memory (skills, workflows) ─")
    yn = input("  Include workflows? [Y/n]: ").strip().lower()
    if yn != "n":
        config["procedural"] = {"include_workflows": True}

    # Working
    print()
    print("─ Working Memory (active context) ─")
    yn = input("  Include active epics? [Y/n]: ").strip().lower()
    if yn != "n":
        config["working"] = {"include_active_epics": True}

    # Output
    print()
    print("=" * 50)
    output = {
        "team": team_name,
        "role": role_name,
        "memory_filter": config,
    }
    yaml_text = yaml.dump(output, default_flow_style=False, sort_keys=False, allow_unicode=True)
    print(yaml_text)

    yn = input("Save to tortoise-setup.yaml? [Y/n]: ").strip().lower()
    if yn != "n":
        try:
            with open("tortoise-setup.yaml", "w") as f:
                f.write("# Tortoise memory_filter config\n")
                f.write(f"# Role: {role_name}  Team: {team_name}\n")
                f.write(yaml_text)
            print("Saved to tortoise-setup.yaml")
        except OSError as e:
            print(f"Error saving: {e}", file=sys.stderr)

    print()
    print("Add the memory_filter block to your agent manifest (.pi/agents/<name>.md)")
    print("under capabilities.memory_filter.")

    # ── Harness-specific instructions ───────────────────────────
    if harness:
        print()
        print("─ Harness Setup ─")
        _print_harness_instructions(harness)

    return 0


def _print_harness_instructions(harness: str) -> None:
    """Print harness-specific setup instructions."""
    if harness == "pi" or harness == "multiple":
        print()
        print("Pi:")
        print("  ✅ tortoise-context extension auto-injects context when you mention issues.")
        print("  Run /reload in Pi to activate.")
        print("  Or call tortoise_help() anytime.")
    if harness == "claude" or harness == "multiple":
        print()
        print("Claude Code:")
        print("  Add tortoise MCP to your .mcp.json:")
        print('    {"tortoise": {"command": "python3", "args": ["-m", "tortoise.mcp_server"]}}')
        print("  Claude Code will auto-discover MCP tools on restart.")
        print("  Optional: add .claude/hooks/session-start.sh for auto-injection.")
        print("    cp tortoise/claude-hooks/session-start.sh .claude/hooks/session-start.sh")
    if harness == "codex" or harness == "multiple":
        print()
        print("Codex:")
        print("  Add tortoise MCP to ~/.codex/config.toml:")
        print("    [mcp_servers.tortoise]")
        print('    command = "python3"')
        print('    args = ["-m", "tortoise.mcp_server"]')
        print("  AGENTS.md is auto-loaded by Codex — Tortoise instructions are already there.")
        print("  autoRecall will pick up Tortoise Points automatically.")
    if harness == "cursor" or harness == "multiple":
        print()
        print("Cursor:")
        print("  Add tortoise MCP to your .mcp.json (same format as Pi/Claude Code).")
        print("  Create .cursor/rules/tortoise.mdc with agent instructions:")
        print("    When working on issues, call mcp__tortoise__tortoise_suggest_entry_points()")
        print("    to find related context. File decisions with tortoise_create_point().")


def _default_memory_filter(role: str) -> dict:
    """Return sensible defaults per role type."""
    defaults = {
        "developer": {
            "episodic": {"last_n_sessions": 3, "filter_by_epic": True},
            "epistemic": {"min_confidence": 0.5, "max_age_days": 30, "include_kinds": ["decision", "observation"]},
            "semantic": {"include_decisions": True, "include_plans": False},
            "working": {"include_active_epics": True},
        },
        "researcher": {
            "epistemic": {"min_confidence": 0.3, "max_age_days": 90, "include_kinds": ["hypothesis", "observation", "statement"]},
            "semantic": {"include_decisions": False, "include_plans": False},
        },
        "strategist": {
            "epistemic": {"min_confidence": 0.5, "max_age_days": 60, "include_kinds": ["decision", "hypothesis", "strategy", "vision"]},
            "semantic": {"include_decisions": True, "include_plans": True},
            "working": {"include_active_epics": True},
        },
    }
    return defaults.get(role, defaults["developer"])


def _cmd_index_github(args):
    """Clone a GitHub repo, walk .md files, extract into FalkorDB.

    tortoise index github <url> [--background]

    Idempotent: re-running the same repo skips already-indexed files
    (keyed by content hash via idempotency.document_key).
    """
    import atexit
    import os
    import subprocess
    import sys
    import tempfile
    from pathlib import Path

    from tortoise.api import EventAPI
    from tortoise.extraction_pipeline import ExtractionPipeline
    from tortoise.log import EventLog
    from tortoise.projection import FalkorProjection

    url = args.url
    branch = args.branch or "main"

    # Background mode: detach and return immediately
    if args.background:
        cmd = [sys.executable, "-m", "tortoise", "index", "github", url]
        if branch != "main":
            cmd.extend(["--branch", branch])
        pid_file = Path(tempfile.gettempdir()) / f"tortoise-index-{Path(url).stem}.pid"
        log_file = pid_file.with_suffix('.log')
        with open(log_file, 'w') as lf:
            proc = subprocess.Popen(
                cmd, stdout=lf, stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        pid_file.write_text(str(proc.pid))
        print(f"Indexing {url} in background (pid {proc.pid})")
        print(f"  Progress: tail -f {pid_file.with_suffix('.log')}")
        return 0

    # Determine if local path or remote URL
    url_path = Path(url).expanduser().resolve()
    is_local = url_path.is_dir()

    if is_local:
        repo_path = url_path
        repo_name = repo_path.name
        tmpdir = None  # no cleanup needed
        print(f"Indexing local repo: {repo_path}")
    else:
        # Clone repo
        tmpdir = tempfile.mkdtemp(prefix="tortoise-index-")
        atexit.register(lambda: __import__("shutil").rmtree(tmpdir, ignore_errors=True))

        repo_name = url.rstrip("/").split("/")[-1].replace(".git", "")
        repo_path = Path(tmpdir) / repo_name

        print(f"Cloning {url} (branch: {branch})…")
        result = subprocess.run(
            ["git", "clone", "--depth", "1", "--branch", branch, url, str(repo_path)],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            print(f"Clone failed: {result.stderr}", file=sys.stderr)
            return 1

    # Walk .md files
    md_files = sorted(repo_path.rglob("*.md"))
    # ponytail: skip node_modules, .git, venv
    md_files = [f for f in md_files if ".git/" not in str(f)
                and "node_modules/" not in str(f)
                and "venv/" not in str(f)
                and "__pycache__" not in str(f)]
    total = len(md_files)
    if total == 0:
        print("No markdown files found.")
        return 0

    print(f"Found {total} markdown files. Indexing…")

    # Set up projection + API (same detection as _cmd_init)
    proj = None
    # Try Docker FalkorDB first
    try:
        host = os.environ.get("FALKORDB_HOST", "localhost")
        port = int(os.environ.get("FALKORDB_PORT", "16379"))
        password = os.environ.get("FALKORDB_PASSWORD", "")
        from falkordb import FalkorDB as FDB
        db = FDB(host=host, port=port, password=password or None)
        db.select_graph("tortoise").query("RETURN 1")
        proj = FalkorProjection(host=host, port=port, password=password or None)
    except Exception:
        # Fallback: embedded mode (SQLite-backed) or docker:// URI
        try:
            if args.db.startswith("docker://"):
                proj = FalkorProjection.from_uri(args.db)
            else:
                proj = FalkorProjection(path=args.db)
        except Exception as e:
            print(f"tortoise index: Cannot connect to database: {e}", file=sys.stderr)
            print("Set --db to a Docker URI or ensure FalkorDB is running.", file=sys.stderr)
            return 1

    log_path = Path(tempfile.gettempdir()) / f"tortoise-index-{repo_name}.jsonl"
    log = EventLog(str(log_path))
    api = EventAPI(log, initiated_by="extractor", agent_id="github-indexer", projection=proj)

    # Idempotency: track content hashes to avoid re-indexing
    # ponytail: simple JSON file — no DB, no config. Add FalkorDB-backed
    # dedup if per-file tracking across repos becomes necessary.
    from tortoise.idempotency import document_key as doc_key_fn
    import json as _json
    hash_file = Path.home() / ".tortoise" / "indexed_hashes.json"
    hash_file.parent.mkdir(parents=True, exist_ok=True)
    indexed_hashes: set[str] = set()
    if hash_file.exists():
        try:
            indexed_hashes = set(_json.loads(hash_file.read_text()))
        except Exception:
            pass

    pipeline = ExtractionPipeline(enrich=False)
    indexed, skipped, errors = 0, 0, 0

    for i, fp in enumerate(md_files, 1):
        rel = fp.relative_to(repo_path)
        raw_text = fp.read_text(encoding="utf-8")
        # ponytail: strip frontmatter before hashing — pipeline may add
        # frontmatter, which would change the hash on the next run.
        text_for_hash = raw_text
        if raw_text.startswith('---'):
            end = raw_text.find('---', 3)
            if end > 0:
                text_for_hash = raw_text[end + 3:].lstrip('\n')
        content_hash = doc_key_fn(text_for_hash).value
        if content_hash in indexed_hashes:
            print(f"  [{i}/{total}] {rel}… ⊙ (already indexed)")
            skipped += 1
            continue
        print(f"  [{i}/{total}] {rel}…", end=" ", flush=True)
        try:
            stats = pipeline.process_file(fp, api)
            if stats.get("points", 0) > 0:
                indexed += 1
                indexed_hashes.add(content_hash)
                print(f"✓ ({stats['points']} pts, {stats['operators']} ops, kind={stats['documentKind']})")
            else:
                skipped += 1
                print("⊙ (skipped — no claims found)")
        except Exception as e:
            errors += 1
            print(f"✗ ({e})")

    if proj:
        proj.close()

    # Persist indexed hashes for cross-run idempotency
    try:
        hash_file.write_text(_json.dumps(sorted(indexed_hashes)))
    except Exception:
        pass

    print()
    print(f"Done: {indexed} indexed, {skipped} skipped, {errors} errors")
    if indexed > 0:
        print(f"  Log: {log_path}")
        print(f"  Graph: query with tortoise_suggest_entry_points()")

    # Cleanup (only if we cloned)
    if tmpdir:
        __import__("shutil").rmtree(tmpdir, ignore_errors=True)
    return 0 if errors == 0 else 1


def _cmd_doctor(args):
    """Health check — verify Tortoise setup is healthy."""
    import importlib
    import os
    from pathlib import Path

    print("Tortoise Doctor — Health Check")
    print("=" * 50)
    results: list[tuple[str, str, str]] = []  # (check, status, detail)

    # 1. Python deps
    for dep, pkg in [("falkordb", "falkordb"), ("redislite.falkordb_client", "falkordblite"), ("yaml", "PyYAML")]:
        try:
            importlib.import_module(dep)
            results.append((f"Python: {pkg}", "✅", "installed"))
        except ImportError:
            results.append((f"Python: {pkg}", "⚠️", f"not installed — pip install {pkg}"))

    # 2. Docker / FalkorDB
    docker_host = os.environ.get("FALKORDB_HOST", "localhost")
    try:
        docker_port = int(os.environ.get("FALKORDB_PORT", "16379"))
    except (ValueError, TypeError):
        results.append(("Graph: FalkorDB", "❌", f"Invalid FALKORDB_PORT: {os.environ.get('FALKORDB_PORT')!r}"))
        docker_port = 6379
    try:
        from falkordb import FalkorDB
        docker_pass = os.environ.get("FALKORDB_PASSWORD", "")
        db = FalkorDB(host=docker_host, port=docker_port,
                      password=docker_pass or None)
        db.select_graph("tortoise").query("RETURN 1")
        results.append(("Graph: FalkorDB", "✅", f"connected at {docker_host}:{docker_port}"))
    except ImportError:
        results.append(("Graph: FalkorDB", "⚠️", "falkordb package not installed"))
    except Exception as e:
        results.append(("Graph: FalkorDB", "❌", str(e)[:60]))

    # 3. Graph health
    try:
        from tortoise.sdk import TortoiseSDK
        sdk = TortoiseSDK(db_path=args.path)
        status = sdk.status()
        points = status.get("counts", {}).get("Point", 0)
        total = status.get("total_entities", 0)
        if points > 0:
            results.append(("Graph: health", "✅", f"{points} Points, {total} entities"))
        else:
            results.append(("Graph: health", "⚠️", "0 Points — graph is empty (expected for new setups)"))
    except Exception as e:
        results.append(("Graph: health", "❌", str(e)[:60]))

    # 4. MCP server
    mcp_running = False
    try:
        import subprocess
        out = subprocess.run(
            ["pgrep", "-f", "tortoise.mcp_server"],
            capture_output=True, timeout=2
        )
        mcp_running = out.returncode == 0
    except Exception:
        pass
    if mcp_running:
        results.append(("MCP server", "✅", "running"))
    else:
        results.append(("MCP server", "⚠️", "not running — tortoise serve"))

    # 5. Harness detection
    home = Path.home()
    detections: list[str] = []
    if (home / ".pi" / "agent" / "extensions" / "tortoise-context").exists():
        detections.append("Pi (extension found)")
    if (home / ".claude").exists() or Path(".claude").exists():
        detections.append("Claude Code")
    if (home / ".codex").exists() or Path(".codex").exists():
        detections.append("Codex")
    if Path(".cursor").exists():
        detections.append("Cursor")
    if detections:
        results.append(("Harnesses", "✅", ", ".join(detections)))
    else:
        results.append(("Harnesses", "⚠️", "none detected — run tortoise setup to configure"))

    # Print results
    for check, icon, detail in results:
        print(f"  {icon} {check}: {detail}")

    # Summary
    fails = sum(1 for _, icon, _ in results if icon == "❌")
    warns = sum(1 for _, icon, _ in results if icon == "⚠️")
    passes = sum(1 for _, icon, _ in results if icon == "✅")
    print()
    print(f"{passes} pass, {warns} warn, {fails} fail")
    if fails == 0 and warns == 0:
        print("✅ All checks passing!")
    elif fails == 0:
        print("⚠️  Some warnings — review above.")
    else:
        print("❌ Some checks failed — review above.")
    return 0 if fails == 0 else 1


def _cmd_list_kinds(args) -> int:
    """List all pointKinds present in the graph with counts."""
    import os as _os
    from tortoise.sdk import TortoiseSDK
    from tortoise.projection import FalkorProjection

    uri = _os.environ.get("TORTOISE_DB_URI", "docker://:@localhost:16379/tortoise")
    sdk = TortoiseSDK()
    sdk._proj = FalkorProjection.from_uri(uri)

    try:
        kinds = sdk.list_pointkinds()
        if not kinds:
            print("No pointKinds found.")
            return 0
        max_width = max(len(str(k["kind"])) for k in kinds)
        for k in kinds:
            pack_str = f" ({k['pack']})" if k["pack"] else ""
            print(f"{k['count']:>6}  {k['kind']:<{max_width}}{pack_str}")
        print(f"\n{len(kinds)} kind(s) total")
    finally:
        if sdk._proj:
            sdk._proj.close()
    return 0


def _cmd_list_sources(args) -> int:
    """List all Sources with point counts."""
    import os as _os
    from tortoise.sdk import TortoiseSDK
    from tortoise.projection import FalkorProjection

    uri = _os.environ.get("TORTOISE_DB_URI", "docker://:@localhost:16379/tortoise")
    sdk = TortoiseSDK()
    sdk._proj = FalkorProjection.from_uri(uri)

    try:
        sources = sdk.list_sources()
        if not sources:
            print("No sources found.")
            return 0
        max_url_width = max(len(str(s["url"] or "")) for s in sources)
        max_sk_width = max(len(str(s["sourceKind"] or "")) for s in sources)
        for s in sources:
            url = str(s["url"] or "")
            sk = str(s["sourceKind"] or "")
            print(f"{s['points']:>6}  {url:<{max_url_width}}  {sk:<{max_sk_width}}")
        print(f"\n{len(sources)} source(s) total")
    finally:
        if sdk._proj:
            sdk._proj.close()
    return 0


def _cmd_decide(args) -> int:
    """Compare options via EP belief propagation.

    Reads a JSON or YAML input file with:
      {context, options, criteria, findings, edges, truth_edges?, relevance_edges?}

    Wires criteria+findings→options via IMPL/NAND, handles truth challenges
    (NAND on finding points) and relevance mitigations (mitigate on operators),
    then runs EP belief propagation and prints a ranked confidence table.

    MITIGATION SEMANTICS (TRUTH vs RELEVANCE):
      - truth_edges: NAND directly on the target finding point (it's FALSE)
      - relevance_edges: mitigate the OPERATOR (it's TRUE but matters LESS)
        Uses mitigate_operator with strength in [0.10, 0.50] range.
      - Never NAND an option/criterion point for bad fit — express fit on the operator.
    """
    import json as _json
    import sys as _sys
    from pathlib import Path
    from tortoise.sdk import TortoiseSDK
    from tortoise.projection import FalkorProjection

    # Two modes: --input <file> or inline (--options/--criteria/--findings/--edges)
    if args.input:
        input_path = Path(args.input)
        if not input_path.exists():
            print(f"Input file not found: {args.input}", file=_sys.stderr)
            return 1
        raw = input_path.read_text(encoding="utf-8")
        # Parse JSON or YAML
        if input_path.suffix in (".yaml", ".yml"):
            try:
                import yaml
            except ImportError:
                print("Error: PyYAML is required for YAML input. Run: pip install PyYAML", file=_sys.stderr)
                return 1
            data = yaml.safe_load(raw)
        else:
            data = _json.loads(raw)
    else:
        # Inline mode
        data: dict = {}
        if args.options:
            data["options"] = _json.loads(args.options)
        if args.criteria:
            data["criteria"] = _json.loads(args.criteria)
        if args.findings:
            data["findings"] = _json.loads(args.findings)
        if args.edges:
            data["edges"] = _json.loads(args.edges)

    options = data.get("options", {})
    criteria = data.get("criteria", {})
    findings = data.get("findings", {})
    edges = data.get("edges", [])
    truth_edges = data.get("truth_edges", [])
    relevance_edges = data.get("relevance_edges", [])

    if not options:
        print("Error: at least one option required (--input file with 'options' or --options JSON)", file=_sys.stderr)
        return 1

    import os as _os
    uri = getattr(args, "db", None) or _os.environ.get("TORTOISE_DB_URI", "docker://:@localhost:16379/tortoise")
    sdk = TortoiseSDK()
    sdk._proj = FalkorProjection.from_uri(uri)

    # Track all operator IDs for explicit-factor mode
    all_operator_ids: list[str] = []

    try:
        # ── Create all points ──
        all_points: dict[str, str] = {}
        for pid, content in {**options, **criteria, **findings}.items():
            kind = (
                "option" if pid.startswith(("opt:", "option:")) else
                "criterion" if pid.startswith(("crit:", "criterion:")) else
                "evidence"
            )
            try:
                p = sdk.create_point(kind, content, dedup=True)
                all_points[pid] = p["id"]
                print(f"  ✓ {pid} → {p['id']}")
            except Exception as e:
                print(f"  ⚠ {pid}: {e}")

        # ── Resolve helper: name → point_id ──
        def _resolve(name: str) -> str:
            """Resolve a key or point ID to a graph point ID."""
            if name in all_points:
                return all_points[name]
            # Try as a raw graph ID (pass-through)
            return name

        # ── Create regular edges (IMPL/NAND) ──
        # Track created operators so relevance_edges can reuse them instead of
        # creating duplicates (same src/op_type/tgt in both sections).
        created_ops: dict[tuple[str, str, str], str] = {}
        for edge in edges:
            if isinstance(edge, list):
                # Tuple format: [source, op_type, target]
                src, op_type, tgt = edge[0], edge[1], edge[2]
                label = edge[3] if len(edge) > 3 else None
            elif isinstance(edge, dict):
                src = edge["source"]
                op_type = edge["op_type"]
                tgt = edge["target"]
                label = edge.get("label")
            else:
                print(f"  ⚠ Unknown edge format: {edge}")
                continue

            try:
                op = sdk.create_operator(op_type, _resolve(src), [_resolve(tgt)], label=label)
                created_ops[(src, op_type, tgt)] = op["id"]
                all_operator_ids.append(op["id"])
                print(f"  ✓ {src} --{op_type}--> {tgt}")
            except Exception as e:
                print(f"  ⚠ {src} --{op_type}--> {tgt}: {e}")

        # ── Truth edges: NAND the target finding point (it's FALSE) ──
        for te in truth_edges:
            src = te["source"]
            op_type = te.get("op_type", "NAND")
            tgt = te["target"]
            try:
                top = sdk.create_operator(op_type, _resolve(src), [_resolve(tgt)])
                all_operator_ids.append(top["id"])
                print(f"  ⚡ truth: {src} --{op_type}--> {tgt}")
            except Exception as e:
                print(f"  ⚠ truth {src} --{op_type}--> {tgt}: {e}")

        # ── Relevance edges: mitigate the OPERATOR (TRUE but matters LESS) ──
        for re in relevance_edges:
            src = re["source"]
            op_type = re.get("op_type", "NAND")
            tgt = re["target"]
            reason = re.get("reason", "Overstated relevance")
            strength = re.get("strength", 0.30)
            # Clamp to valid mitigation range
            strength = max(0.10, min(0.50, strength))
            try:
                # Reuse the operator if already created in `edges` (prevents
                # duplicate operators feeding EP twice).
                op_id = created_ops.get((src, op_type, tgt))
                if op_id is None:
                    op = sdk.create_operator(op_type, _resolve(src), [_resolve(tgt)])
                    op_id = op["id"]
                    all_operator_ids.append(op_id)
                sdk.mitigate_operator(op_id, reason, strength)
                print(f"  ⚖ relevance: {src} --{op_type}--> {tgt} (mitigated {strength:.2f}: {reason})")
            except Exception as e:
                print(f"  ⚠ relevance {src} --{op_type}--> {tgt}: {e}")

        # ── Compute confidence per option ──
        try:
            if all_operator_ids:
                print(f"  (operator-factor mode: {len(all_operator_ids)} operator factors)")
                result = sdk.compute_confidence(factors=all_operator_ids)
            else:
                result = sdk.compute_confidence()
            print(f"\n✓ EP computed: {result['iterations']} iterations, converged={result['converged']}")
            confs = result.get("confidences", {})

            opt_conf: dict[str, float] = {}
            for pid, cid in all_points.items():
                if pid.startswith(("opt:", "option:")):
                    mean = confs.get(cid, {}).get("mean")
                    if isinstance(mean, (int, float)):
                        opt_conf[pid] = float(mean)

            if opt_conf:
                print("\n=== OPTION CONFIDENCE (higher = more supported) ===")
                ranked = sorted(opt_conf.items(), key=lambda kv: kv[1], reverse=True)
                name_width = max(len(pid) for pid in opt_conf)
                for pid, c in ranked:
                    bar = "█" * int(c * 20) + "░" * (20 - int(c * 20))
                    print(f"  {pid:<{name_width}}  {c:.4f}  {bar}")
        except Exception as e:
            print(f"\n⚠ compute_confidence: {e}")

    finally:
        if sdk._proj:
            sdk._proj.close()

    print(f"\nDone. Decision comparison filed.")
    return 0


def main(argv: list[str] | None = None) -> int:

    p = argparse.ArgumentParser(prog="tortoise", exit_on_error=False)
    sp = p.add_subparsers(dest="cmd")
    rb = sp.add_parser("rebuild", help="Rebuild FalkorDB from all .jsonl files")
    rb.add_argument("--db", required=True)
    rb.add_argument("--dir", default=".")
    sp.add_parser("demo", help="Run mock extractor on sample transcript")
    sp.add_parser("backfill", help="Backfill missing Point properties (status, createdAt)").add_argument("--db", required=True, help="Docker URI or file path")
    vf = sp.add_parser("verify", help="Write/read/delete test Point — health check")
    vf.add_argument("--db", required=True, help="FalkorDB docker:// URI")
    cc = sp.add_parser("check-consistency", help="Verify event log matches graph state")
    cc.add_argument("--db", required=True, help="Docker URI or file path")
    cc.add_argument("--log", required=True, help="Path to events.jsonl")
    rc = sp.add_parser("reconcile", help="Replay unprojected EventRecorded entries from JSONL into FalkorDB")
    rc.add_argument("--db", required=True, help="FalkorDB docker:// URI")
    rc.add_argument("--log", required=True, help="Path to events.jsonl")
    bk = sp.add_parser("backup", help="Backup events.jsonl + FalkorDB to timestamped dir")
    bk.add_argument("--db", required=True, help="Path to database file")
    bk.add_argument("--events", default="events.jsonl", help="Path to event log")
    md = sp.add_parser("migrate-db", help="Migrate legacy embedded.db to canonical tortoise.db (data-safe)")
    md.add_argument("--force", action="store_true",
                    help="Bypass marker / overwrite conflicting tortoise.db")
    rs = sp.add_parser("restore", help="Restore from backup directory")
    rs.add_argument("backup_dir", help="Path to backup directory")
    rs.add_argument("--db", required=True, help="Target database path")
    rs.add_argument("--events", default="events.jsonl", help="Target event log path")
    mc = sp.add_parser("mine-conversation", help="Mine conversation transcript → Events + Points (GAP-15)")
    mc.add_argument("transcript", help="Path to transcript file (Speaker: text format)")
    mc.add_argument("--source-id", default=None, help="Source identifier (default: basename of transcript)")
    mc.add_argument("--db", default=None, help="FalkorDB docker:// URI for projection")
    sr = sp.add_parser("serve", help="Start Tortoise MCP server (stdio)")
    init = sp.add_parser("init", help="Auto-detect FalkorDB and create default graph")
    init.add_argument("--path", default=None, help="Path for embedded mode (opt-in)")
    init.add_argument("--yes", "-y", action="store_true", help="Skip prompts, auto-index repo")
    init.add_argument("--api-key", dest="api_key", default=None, help="Connect to Tortoise Cloud instead of local Docker")
    setup = sp.add_parser("setup", help="Configure memory_filter per role (interactive)")
    setup.add_argument("--role", default=None, help="Role name (non-interactive, outputs YAML)")
    setup.add_argument("--team", default=None, help="Team name (used with --role)")
    setup.add_argument("--output", default=None, help="Save config to file instead of stdout")
    doctor = sp.add_parser("doctor", help="Health check — verify Tortoise setup")
    onboard = sp.add_parser("onboard", help="Guided onboarding: init → index → demo → doctor")
    onboard.add_argument("--path", default=None, help="Path for embedded mode")
    hs = sp.add_parser("health-server", help="Start standalone /health HTTP server")
    hs.add_argument("--port", type=int, default=9090, help="HTTP port (default: 9090)")
    hs.add_argument("--bind", default="127.0.0.1", help="Bind address (default: 127.0.0.1)")
    # tortoise team <subcommand>
    team = sp.add_parser("team", help="Team management (Tortoise Cloud)")
    team_sp = team.add_subparsers(dest="team_cmd")
    team_info_p = team_sp.add_parser("info", help="Show team info and usage")
    # tortoise signup — zero-email free-team mint (issue #663)
    sp.add_parser("signup", help="Mint a free hosted team + API key — no email or dashboard")
    # tortoise index github <url>
    idx = sp.add_parser("index", help="Index content into the graph")
    idx_sp = idx.add_subparsers(dest="index_cmd")
    ig = idx_sp.add_parser("github", help="Index a GitHub repo's markdown files")
    ig.add_argument("url", help="GitHub repo URL (https://github.com/user/repo)")
    ig.add_argument("--db", required=True, help="Docker URI or file path for target database")
    ig.add_argument("--branch", default="main", help="Git branch to index")
    ig.add_argument("--background", action="store_true", help="Run in background")
    # tortoise create-point <content> --kind <kind>
    cp = sp.add_parser("create-point", help="Create a Point via Tortoise Cloud API")
    cp.add_argument("content", help="Point content (text)")
    cp.add_argument("--kind", default="statement", help="Point kind (default: statement)")
    # tortoise session <subcommand>
    session = sp.add_parser("session", help="Manage Tortoise Cloud sessions")
    session_sp = session.add_subparsers(dest="session_cmd")
    session_capture = session_sp.add_parser("capture", help="Capture a session from a transcript file")
    session_capture.add_argument("--file", required=True, help="Path to transcript file")
    session_list = session_sp.add_parser("list", help="List all sessions")
    session_view = session_sp.add_parser("view", help="View a specific session")
    session_view.add_argument("id", help="Session ID")
    # tortoise list-kinds
    lk = sp.add_parser("list-kinds", help="List all pointKinds present in the graph with counts")
    # tortoise context — memory digest for agent session-start hooks
    ctx = sp.add_parser("context", help="Print memory digest for agent session-start injection")
    # tortoise list-sources
    ls = sp.add_parser("list-sources", help="List all Sources with point counts")
    # tortoise decide --input <json|yaml>
    dc = sp.add_parser("decide", help="Compare options via EP belief propagation")
    dc.add_argument("--input", "-i", help="Path to JSON or YAML input file with options/criteria/findings/edges")
    dc.add_argument("--options", help="JSON dict of options, e.g. '{\"opt:a\": \"desc\"}'")
    dc.add_argument("--criteria", help="JSON dict of criteria")
    dc.add_argument("--findings", help="JSON dict of findings")
    dc.add_argument("--edges", help="JSON list of edges, e.g. '[\"crit:1\", \"IMPL\", \"opt:a\"]' or full edge dicts")
    dc.add_argument("--context-free", action="store_true",
                    help="Deprecated no-op — context-free (explicit factors) is the only mode since #49 Phase 2",)
    dc.add_argument("--db", help="FalkorDB URI override (default: TORTOISE_DB_URI or docker://:@localhost:16379/tortoise)")
    try:
        args = p.parse_args(argv)
    except (argparse.ArgumentError, SystemExit) as e:
        if isinstance(e, SystemExit):
            raise
        p.print_usage()
        print(f"error: {e}", file=sys.stderr)
        return 2
    if args.cmd == "rebuild":
        _cmd_rebuild(args)
        return 0
    elif args.cmd == "demo":
        _cmd_demo(args)
        return 0
    elif args.cmd == "check-consistency":
        import sys as _sys
        try:
            from tortoise.consistency import check_consistency
            from tortoise.projection import FalkorProjection
            proj = FalkorProjection.from_uri(args.db)
            try:
                result = check_consistency(args.log, proj)
            finally:
                proj.close()
        except Exception as e:
            print(f"Error: {e}", file=_sys.stderr)
            return 1
        if result["ok"]:
            print(f"\u2713 Consistent: {result['log_points']} points in both log and graph")
            return 0
        else:
            print(f"\u2717 Inconsistent: {result['log_points']} in log, {result['db_points']} in graph (delta: {result['delta']})")
            return 1
    elif args.cmd == "reconcile":
        return _cmd_reconcile(args)
    elif args.cmd == "backfill":
        _cmd_backfill(args)
        return 0
    elif args.cmd == "verify":
        return _cmd_verify(args)
    elif args.cmd == "migrate-db":
        from tortoise.migrate_db import main as _migrate_main
        return _migrate_main(["--force"] if args.force else [])
    elif args.cmd == "backup":
        from tortoise.backup import backup
        target = backup(db_path=args.db, events_path=args.events)
        print(f"Backed up to {target}")
        return 0
    elif args.cmd == "restore":
        from tortoise.backup import restore
        result = restore(args.backup_dir, db_path=args.db, events_path=args.events)
        print(f"Restored {result['events']} events — {result['status']}")
        return 0
    elif args.cmd == "serve":
        from tortoise.mcp_server import main as serve_main
        serve_main()
        return 0
    elif args.cmd == "mine-conversation":
        return _cmd_mine_conversation(args)
    elif args.cmd == "init":
        return _cmd_init(args)
    elif args.cmd == "setup":
        return _cmd_setup(args)
    elif args.cmd == "doctor":
        return _cmd_doctor(args)
    elif args.cmd == "onboard":
        return _cmd_onboard(args)
    elif args.cmd == "health-server":
        from tortoise.monitoring import serve_health
        print(f"Health server on http://{args.bind}:{args.port}/health")
        serve_health(args.port, bind=args.bind)
        return 0
    elif args.cmd == "signup":
        return _cmd_signup(args)
    elif args.cmd == "team":
        if args.team_cmd == "info":
            return _cmd_team_info(args)
        team.print_help()
        return 1
    elif args.cmd == "create-point":
        return _cmd_create_point(args)
    elif args.cmd == "session":
        return _cmd_session(args)
    elif args.cmd == "index":
        if args.index_cmd == "github":
            return _cmd_index_github(args)
        idx.print_help()
        return 1
    elif args.cmd == "list-kinds":
        return _cmd_list_kinds(args)
    elif args.cmd == "context":
        return _cmd_context(args)
    elif args.cmd == "list-sources":
        return _cmd_list_sources(args)
    elif args.cmd == "decide":
        return _cmd_decide(args)
    else:
        p.print_help()
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
