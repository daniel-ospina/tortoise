"""CLI entry point: python -m tortoise <command>"""
from __future__ import annotations  # noqa: I001

import argparse
import sys
from pathlib import Path

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
        from tortoise.log import EventLog  # noqa: I001
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
    from pathlib import Path  # noqa: I001
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
    for pid, p in ordered:  # noqa: B007
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
    import json  # noqa: F401
    from pathlib import Path
    from tempfile import NamedTemporaryFile  # noqa: F401

    from tortoise.api import EventAPI
    from tortoise.log import EventLog
    from tortoise.mining import ConversationMiner

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
            # #1198 P1: route --db through the canonical resolvers so embedded
            # paths (e.g. ~/.tortoise/tortoise.db) work, not just docker:// URIs.
            # #1215 P2 c80: reuse the repo's single routing choke-point
            # _projection_for (URI → from_uri, path → embedded) instead of
            # hand-rolling the routing here. Non-URI targets pre-resolve
            # through resolve_db_path (expand ~, reject relative) first — the
            # same resolved-target contract the other _projection_for callers
            # get via _resolve_db_target; FalkorProjection never sees a raw
            # tilde/relative path (#1215 review gate). Bare except is
            # intentional: projection failure degrades to log-only mode
            # (documented) rather than crashing the CLI.
            from tortoise.config import is_db_uri, resolve_db_path
            target = args.db if is_db_uri(args.db) else resolve_db_path(args.db)
            proj = _projection_for(target)
        except ValueError as e:
            # Path-validation failure (e.g. RELATIVE_PATH_ERROR from
            # resolve_db_path) — name the invalid --db value, not FalkorDB.
            print(f"Warning: invalid --db path ({e}), using log-only mode")
        except Exception as e:
            print(f"Warning: FalkorDB unavailable ({e}), using log-only mode")

    log = EventLog(str(log_path))
    api = EventAPI(log, initiated_by="extractor", agent_id="mining-pilot", projection=proj)

    # Disable idempotency for mining (always process fresh)
    api._ingest_cache = {}

    miner = ConversationMiner()
    miner.mine(text, source_id, api)

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
        print(f"   Per plan WF4: <3 events/session → permanently descoped.")  # noqa: F541
        return 1  # non-zero so scripts can detect a descoped session (#1198)
    else:
        print(f"\u2705  GATE PASSED: {len(event_entries)} events")

    print()
    for ev in event_entries:
        e = ev.get("event", {})
        kind = e.get("eventKind", "unknown")
        obj = e.get("object", "")[:80]
        print(f"  [{kind}] {obj}..." if len(e.get("object","")) > 80 else f"  [{kind}] {obj}")
    print()

    return 0


def _cmd_reconcile(args):
    """Replay unprojected EventRecorded entries from JSONL into FalkorDB."""
    import json, sys  # noqa: E401, F401, I001
    from pathlib import Path

    from tortoise.config import is_docker_uri
    if not is_docker_uri(args.db):
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
    import json as _json  # noqa: I001
    import os, tempfile  # noqa: E401
    from pathlib import Path

    # ── Cloud mode (--api-key) ──────────────────────────────────
    # #304: --json / --harness / --write-mcp-config extend the hosted path.
    json_mode = getattr(args, "json", False)
    harness = getattr(args, "harness", None)
    write_mcp = getattr(args, "write_mcp_config", False)
    mcp_force = getattr(args, "force", False)

    if getattr(args, 'api_key', None):
        config_path = Path.cwd() / ".tortoise"
        if config_path.exists():
            existing = _json.loads(config_path.read_text())
            if existing.get("api_key") == args.api_key:
                # Already connected — but still honor --write-mcp-config /
                # --harness so a re-run provisions the MCP file instead of
                # silently returning (#875 P2).
                existing_api_url = (existing.get("api_url")
                                    or os.environ.get("TORTOISE_API_URL", "https://api.premiselabs.co")).rstrip("/")
                if write_mcp:
                    if not harness:
                        print("--harness required with --write-mcp-config (claude|codex|cursor|pi)", file=sys.stderr)
                        return 1
                    rc = _write_mcp_config_file(args.api_key, existing_api_url, harness, mcp_force,
                                                status_to_stderr=json_mode)
                    if rc != 0:
                        if json_mode:
                            _emit_json({"status": "error", "error": "mcp_config",
                                        "message": "Failed to write MCP config (see stderr)."})
                        return rc
                if json_mode:
                    # Full shape — same contract as a fresh connect (#875 P2).
                    _emit_json({
                        "status": "connected",
                        "already_connected": True,
                        "team_id": existing.get("team_id"),
                        "api_url": existing_api_url,
                        "mcp": {
                            "endpoint": f"{existing_api_url}/mcp/",
                            "auth_header": f"Bearer {args.api_key}",
                            "configs": {
                                "claude": {"file": ".mcp.json", "config": _harness_mcp_config("claude", args.api_key, existing_api_url)},
                                "codex": _harness_mcp_config("codex", args.api_key, existing_api_url),
                                "cursor": {"file": ".mcp.json", "config": _harness_mcp_config("cursor", args.api_key, existing_api_url)},
                                "pi": {"file": ".mcp.json", "config": _harness_mcp_config("pi", args.api_key, existing_api_url)},
                            },
                        },
                        "onboarding_prompt_url": ONBOARDING_PROMPT_URL,
                        "config_path": str(config_path),
                        "next_steps": [
                            "tortoise team keys create",
                            "tortoise team info",
                            'tortoise create-point "hello"',
                        ],
                    })
                else:
                    print("Already connected to Tortoise Cloud with this API key.")
                return 0
            if not json_mode:
                print(f"⚠️  Existing .tortoise config found — overwriting.")  # noqa: F541
        config = {
            "api_key": args.api_key,
            "api_url": os.environ.get("TORTOISE_API_URL", "https://api.premiselabs.co"),
        }

        # Validate the key against the hosted API BEFORE saving (#707).
        # 401/403 → key rejected → hard fail (never silently write an invalid key);
        # 404 → misconfigured URL → hard fail with distinct message;
        # 5xx/429/408 → transient server error → retry once, then warn + save;
        # non-JSON 200 → unvalidated (captive portal/proxy) → hard fail;
        # network errors → warn + save (cannot validate offline).
        from urllib.request import Request, urlopen  # noqa: I001
        from urllib.error import URLError, HTTPError
        from http.client import HTTPException as _HTTPException
        from json import JSONDecodeError as _JSONDecodeError
        import time as _time

        # Normalize base URL: a trailing slash would produce `//v1/team` → 404.
        base_url = config["api_url"].rstrip("/")
        config["api_url"] = base_url

        def _validate_key() -> dict:
            req = Request(
                f"{base_url}/v1/team",
                headers={"Authorization": f"Bearer {args.api_key}"},
            )
            with urlopen(req, timeout=10) as resp:
                return _json.loads(resp.read())

        # Validation outcome: (error_kind, message, http_code) or None when the
        # key validated. Hard-fail kinds → config NOT saved; warn kinds → saved.
        team_id = None
        fail: tuple[str, str, int | None] | None = None

        try:
            for attempt in range(2):
                try:
                    team_data = _validate_key()
                    team_id = (team_data or {}).get("team_id") if isinstance(team_data, dict) else None
                    if not json_mode:
                        print("✅ API key validated against Tortoise Cloud")
                    break
                except HTTPError as e:
                    if e.code in (401, 403):
                        # Only these mean the key itself was rejected.
                        body = ""
                        try:  # noqa: SIM105
                            body = e.read().decode()
                        except Exception:
                            pass
                        # #308: suspended teams get a structured 403 — surface
                        # the appeal link instead of the generic rejection.
                        sus = _suspended_info(body)
                        if sus is not None:
                            fail = ("team_suspended", f"Team suspended — {sus[0]}", e.code)
                        else:
                            fail = ("key_rejected", f"API rejected the key ({e.code}): {body.strip() or e.reason}", e.code)
                        if not json_mode:
                            print(f"❌ {fail[1]}", file=sys.stderr)
                            print(f"   Config NOT saved. Double-check the key or run:", file=sys.stderr)  # noqa: F541
                            print(f"   tortoise init --api-key tt_<key>", file=sys.stderr)  # noqa: F541
                        break
                    if e.code == 404:
                        fail = ("bad_url", f"API URL appears misconfigured — got 404 from {base_url}/v1/team.", 404)
                        if not json_mode:
                            print(f"❌ {fail[1]}", file=sys.stderr)
                            print(f"   Check TORTOISE_API_URL. Config NOT saved.", file=sys.stderr)  # noqa: F541
                        break
                    if e.code in (408, 429) or 500 <= e.code <= 599:
                        # Transient server-side failure — a valid key may be fine.
                        if attempt == 0:
                            _time.sleep(1)
                            continue
                        fail = ("transient", f"Tortoise Cloud returned {e.code} while validating the key (transient error).", e.code)
                        if not json_mode:
                            print(f"⚠️  {fail[1]}")
                            print(f"   Saving config anyway — verify with: tortoise team info")  # noqa: F541
                        break
                    fail = ("transient", f"Unexpected status {e.code} from Tortoise Cloud while validating the key.", e.code)
                    if not json_mode:
                        print(f"⚠️  {fail[1]}")
                        print(f"   Saving config anyway — verify with: tortoise team info")  # noqa: F541
                    break
        except _JSONDecodeError:
            # 200 with a non-JSON body (captive portal / proxy) — unvalidated.
            fail = ("captive_portal",
                    "Tortoise Cloud returned a non-JSON response — key NOT validated "
                    "(possible captive portal or proxy intercepting the request).", 200)
            if not json_mode:
                print(f"❌ Tortoise Cloud returned a non-JSON response — key NOT validated.", file=sys.stderr)  # noqa: F541
                print(f"   (Possible captive portal or proxy intercepting the request.)", file=sys.stderr)  # noqa: F541
                print(f"   Config NOT saved. Check your network / TORTOISE_API_URL and retry.", file=sys.stderr)  # noqa: F541
        except (URLError, _HTTPException, ValueError) as e:
            reason = getattr(e, "reason", e)
            fail = ("offline", f"Could not reach {base_url} to validate the key ({reason}).", None)
            if not json_mode:
                print(f"⚠️  {fail[1]}")
                print(f"   Saving config anyway — verify with: tortoise team info")  # noqa: F541

        if fail and fail[0] in ("key_rejected", "bad_url", "captive_portal"):
            # Hard-fail kinds: config is NOT saved (existing #707 behavior).
            if json_mode:
                _emit_json({"status": "error", "error": fail[0], "message": fail[1], "http_code": fail[2]})
            return 1

        config_path.write_text(_json.dumps(config, indent=2) + "\n")
        os.chmod(config_path, 0o600)

        if json_mode:
            if fail:
                # Warn-and-save paths (transient/offline): config IS saved but
                # the key was not validated — report the error to agents.
                _emit_json({
                    "status": "error",
                    "error": fail[0],
                    "message": fail[1],
                    "http_code": fail[2],
                    "config_saved": True,
                })
                return 0
            # Machine-consumable output — full shape for agents (#304).
            _emit_json({
                "status": "connected",
                "team_id": team_id,
                "api_url": base_url,
                "mcp": {
                    "endpoint": f"{base_url}/mcp/",
                    "auth_header": f"Bearer {args.api_key}",
                    "configs": {
                        "claude": {"file": ".mcp.json", "config": _harness_mcp_config("claude", args.api_key, base_url)},
                        "codex": _harness_mcp_config("codex", args.api_key, base_url),
                        "cursor": {"file": ".mcp.json", "config": _harness_mcp_config("cursor", args.api_key, base_url)},
                        "pi": {"file": ".mcp.json", "config": _harness_mcp_config("pi", args.api_key, base_url)},
                    },
                },
                "onboarding_prompt_url": ONBOARDING_PROMPT_URL,
                "config_path": str(config_path),
                "next_steps": [
                    "tortoise team keys create",
                    "tortoise team info",
                    'tortoise create-point "hello"',
                ],
            })
            if write_mcp:
                if not harness:
                    print("--harness required with --write-mcp-config (claude|codex|cursor|pi)", file=sys.stderr)
                    return 1
                return _write_mcp_config_file(args.api_key, base_url, harness, mcp_force,
                                              status_to_stderr=json_mode)
            return 0

        print("Connected to Tortoise Cloud (team will be resolved from API key)")
        print(f"Config saved to {config_path}")
        print("⚠️  .tortoise contains a plaintext API key — do NOT commit this file.")
        print()
        _print_mcp_configs(args.api_key, base_url, harness)
        print()
        print("── Onboarding Prompt ──")
        print("Paste this into your agent to complete setup:")
        print(f"  {ONBOARDING_PROMPT_URL}")
        print()
        print("Next steps:")
        print("  tortoise team keys create       # create additional keys for team members")
        print("  tortoise team info              # see your team usage and limits")
        print('  tortoise create-point "hello"   # create your first memory')
        if write_mcp:
            if not harness:
                print("--harness required with --write-mcp-config (claude|codex|cursor|pi)", file=sys.stderr)
                return 1
            return _write_mcp_config_file(args.api_key, base_url, harness, mcp_force,
                                          status_to_stderr=json_mode)
        return 0

    print("Tortoise init — resolving DB target…")

    # #705/#715: resolve the DB target ONCE through the shared helper — the
    # same code path the onboarding index step uses — so init and index can
    # never disagree (silent split graph, conf 60). Selection is purely
    # environmental: explicit --path > TORTOISE_DB_URI (any supported scheme)
    # > FALKORDB_* legacy trio > TORTOISE_DB_PATH > canonical default. No
    # connectivity probing — docker reachability never overrides the
    # configured target.
    from tortoise.config import is_db_uri
    try:
        target = _resolve_db_target(args.path)
    except ValueError as e:
        # Bad --path (e.g. relative) — clean CLI error, not a traceback (#715).
        # #720 P2 conf 95: mask userinfo — unsupported-scheme URIs fall into
        # RELATIVE_PATH_ERROR with the RAW URI embedded (no-op for plain paths).
        print(f"  ❌ Invalid DB path: {_mask_uri_userinfo(str(e))}")
        return 1

    graph_ready = False
    uri_mode = False

    if is_db_uri(target):
        # 1. URI mode — connect to the configured URI target itself (never a
        # differently-probed docker); unreachable is a hard error so the
        # index step can't silently split onto a different store.
        try:
            from tortoise.projection import FalkorProjection
            _proj = FalkorProjection.from_uri(target)
            _proj.g.query("RETURN 1")
            print(f"  ✅ FalkorDB reachable via TORTOISE_DB_URI")  # noqa: F541
            graph_ready = True
            uri_mode = True
        except ImportError:
            print(f"  ❌ falkordb not installed — required for URI mode")  # noqa: F541
            print(f"     pip install falkordb")  # noqa: F541
            return 1
        except Exception as e:
            err = str(e).lower()
            if "auth" in err or "password" in err:
                print(f"  ❌ FalkorDB auth failed — check TORTOISE_DB_URI credentials")  # noqa: F541
            else:
                print(f"  ❌ FalkorDB unreachable ({e})")
            print("     Fix TORTOISE_DB_URI, or unset it to use embedded mode.")
            return 1
    else:
        # 2. Fallback: embedded mode (SQLite-backed) at the resolved path
        db_path = target
        try:
            from tortoise.projection import FalkorProjection
            _proj = FalkorProjection(db_path)
            _proj.g.query("RETURN 1")
            print(f"  ✅ Embedded mode initialized at {db_path} (single-writer, eval only — docker compose for durable multi-writer)")
            graph_ready = True
        except ImportError:
            # Reachable when falkordblite is actually missing: tortoise/__init__
            # no longer crashes at import time (issue #716). falkordb Docker
            # mode is handled earlier, so this fires only for the embedded gap.
            print(f"  ❌ Embedded mode unavailable — falkordblite not installed.")  # noqa: F541
            print(f"     pip install falkordb        # for Docker mode (FalkorProjection)")  # noqa: F541
            print(f"     pip install falkordblite    # for embedded mode (FalkorProjection)")  # noqa: F541
            return 1
        except Exception as e:
            print(f"  ❌ Embedded mode init failed: {e}")
            return 1

    if not graph_ready:
        return 1

    # Write welcome Point to the graph
    try:
        from tortoise.sdk import TortoiseSDK
        if uri_mode:
            # Materialize the decision so later commands resolve to the SAME
            # target (single source of truth). The background index spawn
            # below carries the target via the env/argv handoff (#715,
            # conf 60/85): password-bearing URIs travel through the
            # TORTOISE_DB_URI env (never argv — a password in `ps` output
            # leaks the secret); password-less targets travel via --db argv
            # so the child resolves identically even if the parent env
            # changes later. index's --db is optional at argparse since the
            # split, so the child resolves through the shared precedence
            # either way — never a hardcoded default.
            os.environ.setdefault("TORTOISE_DB_URI", target)
            sdk = TortoiseSDK()
        else:
            # Embedded: record the resolved path so later commands hit the
            # same DB init wrote to. The spawn below passes --db explicitly
            # so the child resolves identically (same precedence, conf 60).
            os.environ.setdefault("TORTOISE_DB_PATH", db_path)
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
    # #715 P2 conf 70: skip entirely when the caller (tortoise onboard)
    # indexes inline — prevents the double index (init auto-spawn + inline).
    # `is True` (not truthiness): mock.Mock args in tests return a truthy
    # Mock for unset attrs, which must NOT disable the block.
    if getattr(args, 'no_index', False) is not True:
        import subprocess as _sp
        import sys as _sys  # noqa: F401
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
                    log_f = open(str(Path(tempfile.gettempdir()) / "tortoise-init-index.log"), 'w')  # noqa: SIM115
                    # #715: pass the resolved DB target to the child — env
                    # for password-bearing URIs (never leak in argv), --db
                    # otherwise (index's --db resolves identically, conf 60).
                    child_cmd, child_env = _index_github_child_cmd(
                        target, repo_root)
                    _sp.Popen(
                        child_cmd,
                        stdout=log_f, stderr=_sp.STDOUT,
                        start_new_session=True, env=child_env,
                    )
                    print("Indexing in background. Tortoise is ready to use immediately.")
                else:
                    print()
                    yn = input(f"Found {md_count} markdown files in this repo. Index them into Tortoise? [Y/n]: ").strip().lower()
                    if yn != "n":
                        print("Launching indexer in background…")
                        log_f = open(str(Path(tempfile.gettempdir()) / "tortoise-init-index.log"), 'a')  # noqa: SIM115
                        # #715: same as the --yes path — carry the resolved
                        # DB target so the child indexes the DB init wrote to.
                        child_cmd, child_env = _index_github_child_cmd(
                            target, repo_root)
                        _sp.Popen(
                            child_cmd,
                            stdout=log_f, stderr=_sp.STDOUT,
                            start_new_session=True, env=child_env,
                        )
                        print("Indexing in background. Tortoise is ready to use immediately.")
    return 0



def _read_stored_signup_token_with_source() -> tuple[str | None, Path | None]:
    """Stored st_ token from the active configs (#1709), WITH its source.

    cwd/.tortoise first (legacy shape), then the #1708 global
    ~/.tortoise/credentials.json (the canonical store). The field is
    additive in either file — the two land compatibly. Returns
    (token, source_path); #1752 needs the path to warn about source
    divergence (revoke/recover read the token from a DIFFERENT config
    than the auth key).
    """
    import json as _j
    from pathlib import Path
    for path in (Path.cwd() / ".tortoise",
                 Path.home() / ".tortoise" / "credentials.json"):
        try:
            cfg = _j.loads(path.read_text())
        except Exception:
            continue
        tok = cfg.get("signup_token") if isinstance(cfg, dict) else None
        if isinstance(tok, str) and tok:
            return tok, path
    return None, None


def _read_stored_signup_token() -> str | None:
    """Stored st_ token from the active configs (#1709) — token only.

    Thin wrapper over _read_stored_signup_token_with_source preserving the
    pre-#1752 single-value contract for _cmd_signup.
    """
    return _read_stored_signup_token_with_source()[0]


def _resolve_same_source_token(args, cfg_path, cfg, surface: str = "revoke") -> str | None:
    """#1752: signup token for revoke/recover resolved from the SAME
    config the auth key came from (the #1708 env → cwd → global resolver).

    --token wins (the user's explicit choice — used as-is, silently); else
    the resolved config's OWN signup_token (same file as the key → no
    divergence); else the cwd→global stored read — with a stderr warning
    naming BOTH sources when they diverge (mirrors the #1708 env-shadow
    warning style). An env key has NO token, so any stored token is
    necessarily a different source → always warned. With no key source at
    all (keyless `recover`) the stored token is used silently — there is
    nothing to diverge from.
    """
    import os as _os
    import sys as _sys
    token = getattr(args, "token", None)
    if token:
        return token
    if isinstance(cfg, dict):
        tok = cfg.get("signup_token")
        if isinstance(tok, str) and tok:
            return tok
    stored_token, stored_src = _read_stored_signup_token_with_source()
    if stored_token is None:
        return None
    if stored_src == cfg_path:
        return stored_token  # defensive — same file, nothing to warn about
    if cfg_path is not None:
        key_src = str(cfg_path)
    elif _os.environ.get("TORTOISE_API_KEY", "").strip():
        key_src = "TORTOISE_API_KEY (env)"
    else:
        return stored_token  # no key source at all (keyless recover)
    print(f"⚠️  note: your recovery token comes from {stored_src}, but your "
          f"API key comes from {key_src} — if they are different teams, "
          "revoke/recover will fail with 403.", file=_sys.stderr)
    return stored_token


def _read_stored_config_api_url() -> str | None:
    """Stored api_url from the active configs (#1749 token-only fallback).

    Same candidates as _read_stored_signup_token (cwd/.tortoise legacy
    shape, then the #1708 global ~/.tortoise/credentials.json). Used when
    _resolve_config_path raises _ConfigError on a config that parses but
    carries no api_key — recover authenticates with the signup token and
    only needs the URL, so a keyless config must not block recovery.
    """
    import json as _j
    from pathlib import Path
    for path in (Path.cwd() / ".tortoise",
                 Path.home() / ".tortoise" / "credentials.json"):
        try:
            cfg = _j.loads(path.read_text())
        except Exception:
            continue
        url = cfg.get("api_url") if isinstance(cfg, dict) else None
        if isinstance(url, str) and url.strip():
            return url.strip()
    return None


def _token_only_config_path():
    """First active config carrying a signup_token but NO usable api_key —
    the exact recoverable config-loss state (#1756). Scans the same
    candidates as _read_stored_signup_token (cwd/.tortoise legacy shape,
    then the #1708 global ~/.tortoise/credentials.json).

    A token-bearing candidate that ALSO has a usable key is not this state
    (the resolver would have accepted it — the hint would be wrong); a
    genuinely corrupt file (unparseable JSON / invalid UTF-8) is skipped
    here and stays on the corrupt-config path. The resolver raises
    _ConfigError on this shape (missing/non-str api_key trips its
    invariant), so _cmd_signup can point at `tortoise recover` instead of
    the destructive "delete it / --force" boilerplate.

    Returns (token_only_path, shadow_path): shadow_path is a LATER
    candidate holding a usable api_key (the user's key is NOT lost — a
    legacy token-only file shadows it); None when the key is genuinely
    lost.
    """
    import json as _j
    from pathlib import Path
    candidates = (Path.cwd() / ".tortoise",
                  Path.home() / ".tortoise" / "credentials.json")
    seen_token_only = None
    for path in candidates:
        try:
            cfg = _j.loads(path.read_text())
        except Exception:
            continue
        if not isinstance(cfg, dict):
            continue
        key = cfg.get("api_key")
        if isinstance(key, str) and key.strip():
            if seen_token_only is not None:
                # a later candidate holds a usable key — the token-only file
                # only SHADOWS it; recover would write the global store and
                # leave the shadow in place (the next command fails the same
                # way). The handler prints a shadow note instead of the
                # "key lost" hint (review P2, #1756).
                return seen_token_only, path
            continue
        tok = cfg.get("signup_token")
        if isinstance(tok, str) and tok and seen_token_only is None:
            seen_token_only = path
    if seen_token_only is not None:
        return seen_token_only, None
    return None, None


def _is_invalid_signup_token(body: str) -> bool:
    """True when a 422 body is the uniform invalid_signup_token detail (#1709)."""
    try:
        import json as _j
        detail = (_j.loads(body) or {}).get("detail")
    except Exception:
        return False
    return isinstance(detail, dict) and detail.get("error_code") == "invalid_signup_token"


def _cmd_recover(args) -> int:
    """Keyless config-loss recovery (#1709): POST /v1/agent/recover with the
    saved st_ token → a NEW key on the SAME team; config rewritten, data
    intact. The token is persisted back into the config — the recover
    endpoint does NOT re-issue tokens (rotation rejected), so without
    persistence this surface would be one-shot-only and the NEXT key-loss
    would silently fresh-mint and orphan the recovered team.
    """
    import json, os, sys, uuid  # noqa: E401, I001
    from pathlib import Path
    from urllib.request import Request, urlopen
    from urllib.error import URLError, HTTPError

    # #1752: resolve the token from the SAME config the auth key comes from
    # (env → cwd → global). A corrupt config must NOT block keyless
    # recovery via --token — fall back to the stored-token read with no
    # key source, which prints no divergence warning (nothing to diverge).
    try:
        _cfg_path, _cfg, _api_key, _api_url = _resolve_config_path()
    except _ConfigError:
        _cfg_path, _cfg = None, None
    token = _resolve_same_source_token(args, _cfg_path, _cfg, surface="recover")
    if not token:
        print("No recovery token found. Pass --token st_... or run "
              "'tortoise signup' first.", file=sys.stderr)
        return 1
    # API host via the #1708 resolver chain (env → cwd → global), mirroring
    # _cmd_token_revoke — the stored config's api_url must beat the default
    # host (#1749: env-only resolution made recovery dead on any non-default
    # host — it POSTed to prod and 422'd with a misleading message).
    api_url = os.environ.get("TORTOISE_API_URL", "https://api.premiselabs.co")
    try:
        _cfg_path, _cfg, _api_key, resolved_url = _resolve_config_path()
        if resolved_url:
            api_url = resolved_url
    except _ConfigError:
        # A token-only config (valid JSON, no api_key) trips the resolver's
        # api_key invariant — recover authenticates with the signup token
        # (no key needed), so read the stored api_url directly instead of
        # failing on the recover surface (#1749).
        stored_url = _read_stored_config_api_url()
        if stored_url:
            api_url = stored_url
    base = api_url.rstrip("/")
    print("Recovering your team key with the saved recovery token…")
    try:
        req = Request(
            f"{base}/v1/agent/recover",
            data=json.dumps({"signup_token": token}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
    except HTTPError as e:
        body = e.read().decode() if e.fp else ""
        if e.code == 422:
            print("Recovery failed: invalid signup token. (Truncated or revoked? "
                  "The old team may still be reachable via its key.)",
                  file=sys.stderr)
            return 1
        if e.code == 403:
            sus = _suspended_info(body)
            if sus is not None:
                print(f"Recovery failed: {sus[0]}", file=sys.stderr)
                return 1
            print(f"Recovery failed (403): {body}", file=sys.stderr)
            return 1
        print(f"Recovery failed ({e.code}): {body}", file=sys.stderr)
        return 1
    except (URLError, ValueError, json.JSONDecodeError) as e:
        print(f"Cannot reach API at {base}: {e}", file=sys.stderr)
        return 1

    if not (isinstance(data, dict) and "key" in data and "team_id" in data):
        # #1709 fixer P2.2: a 200 with valid JSON but no key/team_id (proxy/
        # edge garbage) must not KeyError-traceback on the derefs below —
        # mirror _cmd_signup's malformed-response guard (fail-soft: the
        # recovery may have committed server-side, so never blindly retry).
        print("Recovery may have succeeded but the response was malformed "
              "(missing 'key' or 'team_id') — check the dashboard or support "
              "before re-running; do NOT blindly retry.", file=sys.stderr)
        return 1

    config = {
        "api_key": data["key"],
        "api_url": api_url,
        "team_id": data["team_id"],
        "team_name": data.get("team_name"),
        "signup_token": token,  # ⛔ persist — recovery must not be one-shot
    }
    # Write to the #1708 global store (0600, dir 0700, atomic) — same shape
    # _cmd_signup uses, so resolver precedence stays consistent.
    try:
        home_dir = Path.home() / ".tortoise"
        home_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(home_dir, 0o700)
        config_path = home_dir / "credentials.json"
        tmp_path = home_dir / f"credentials.json.tmp-{uuid.uuid4().hex}"
        fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(json.dumps(config, indent=2) + "\n")
        os.replace(tmp_path, config_path)
    except OSError as e:
        print(f"Recovered key could NOT be saved to config: {e}", file=sys.stderr)
        print(f"   API key (save it now): {data['key']}", file=sys.stderr)
        return 1
    print(f"✅ Key recovered on team {data.get('team_name')} (data intact)")
    print(f"   API key: {data['key']}")
    print(f"   Config saved to {config_path} (shown once — store it)")
    print(f"   Recovery token kept: {token[:14]}…")
    return 0


def _cmd_token_revoke(args) -> int:
    """User-facing signup-token revocation (#1715): POST
    /v1/agent/token/revoke with the saved (or --token) st_ token → the
    token can no longer recover keys on the team. The request is
    authenticated with the stored team key (env → cwd → global resolver,
    #1708) — the same credential that proves team ownership server-side;
    the endpoint is team-scoped, so a leaked token is killable the moment
    it is noticed. Prints confirmation; the stored config is left intact
    (the revoked token simply 422s on any later recover).
    """
    import json, sys  # noqa: E401, I001
    from urllib.error import HTTPError, URLError
    from urllib.request import Request, urlopen

    try:
        _cfg_path, _cfg, api_key, api_url = _resolve_config_path()
    except _ConfigError as e:
        print(f"Config at {e} is corrupt or unreadable — cannot authenticate "
              "the revoke request.", file=sys.stderr)
        return 1
    if not api_key:
        print("No stored API key found. Run 'tortoise signup' or "
              "'tortoise init --api-key <key>' first — the revoke request "
              "must be authenticated by the team's key.", file=sys.stderr)
        return 1
    # #1752: the token must come from the SAME config the auth key came
    # from — an env key has no token, so a stored token from another source
    # is used only with a warning naming the shadow source (no silent 403
    # "Not your signup token" dead-end when the sources are different teams).
    token = _resolve_same_source_token(args, _cfg_path, _cfg)
    if not token:
        print("No recovery token found. Pass --token st_... or run "
              "'tortoise signup' first.", file=sys.stderr)
        return 1
    base = (api_url or "https://api.premiselabs.co").rstrip("/")
    print("Revoking the signup token — it can no longer recover keys on this team…")
    try:
        req = Request(
            f"{base}/v1/agent/token/revoke",
            data=json.dumps({"signup_token": token}).encode(),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {api_key}"},
            method="POST",
        )
        with urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
    except HTTPError as e:
        body = e.read().decode() if e.fp else ""
        if e.code == 422:
            print("Revoke failed: invalid signup token.", file=sys.stderr)
            return 1
        if e.code == 404:
            print("Revoke failed: signup token not found on this team.",
                  file=sys.stderr)
            return 1
        if e.code == 401:
            print("Revoke failed (401): the stored API key was rejected. Run "
                  "'tortoise signup' or 'tortoise init --api-key <key>'.",
                  file=sys.stderr)
            return 1
        if e.code == 403:
            sus = _suspended_info(body)
            if sus is not None:
                print(f"Revoke failed: {sus[0]}", file=sys.stderr)
                return 1
            print(f"Revoke failed (403): {body}", file=sys.stderr)
            return 1
        print(f"Revoke failed ({e.code}): {body}", file=sys.stderr)
        return 1
    except (URLError, ValueError, json.JSONDecodeError) as e:
        print(f"Cannot reach API at {base}: {e}", file=sys.stderr)
        return 1

    if not isinstance(data, dict):
        # #1715 fixer guard (mirrors _cmd_recover P2.2): a 200 with valid
        # JSON but no fields must not KeyError-traceback.
        print("Revoke may have succeeded but the response was malformed — "
              "check the dashboard or retry once.", file=sys.stderr)
        return 1
    if data.get("already"):
        print(f"ℹ️  The signup token {token[:14]}… was already revoked.")
    else:
        print(f"✅ Signup token {token[:14]}… revoked — it can no longer "
              f"recover keys on this team.")
    print("   If this was your saved recovery token, the next "
          "'tortoise recover' or token-present signup will return an "
          "invalid-signup-token error.")
    return 0


def _cmd_signup(args) -> int:
    """Zero-email signup (issue #663): mint a working tt_ key from the CLI.

    No email, no dashboard, no Supabase account — the agent/CLI equivalent
    of Mem0's 4-command key mint and Hindsight's npx self-install. Saves
    the config to ~/.tortoise/credentials.json (#1708) so `tortoise
    create-point` etc. work immediately.
    """
    import json
    import os
    import sys
    import time
    import uuid
    from pathlib import Path
    from urllib.error import HTTPError, URLError
    from urllib.request import Request, urlopen

    # Normalize once (#1708 fixer, P2): the reuse-GET rstrips its base but the
    # mint POST used the raw env URL — a trailing-slash TORTOISE_API_URL hit
    # `//v1/agent/signup` → 404. Both URLs derive from this normalized base.
    api_url = os.environ.get("TORTOISE_API_URL", "https://api.premiselabs.co").rstrip("/")
    home_dir = Path.home() / ".tortoise"
    config_path = home_dir / "credentials.json"

    # ── Reuse-before-mint (#1708 D2/D3) ────────────────────────────────────
    # A stored key (env → cwd/.tortoise → ~/.tortoise/credentials.json per D1)
    # is validated with the same REQUEST SHAPE _cmd_init uses (GET /v1/team,
    # Bearer header, timeout=10) — but the error semantics deliberately
    # diverge: init warns-and-saves offline (an unvalidatable key still gets
    # written), while signup FAIL-CLOSES on an unvalidatable existing key
    # (#1708 — the duplicate-mint incident). 200 = reuse, 0 new keys;
    # 401/non-suspended-403 = re-mint (global-store fallback below);
    # SUSPENDED-403 and cannot-validate (429/5xx/network/timeout/garbage-200)
    # = fail-closed exit 1 — never mint on an unvalidatable existing key.
    force = getattr(args, "force", False)
    mint_url = api_url  # may be overridden when re-minting after a 401/403
    reminting_after_401 = False
    legacy_device_id = None

    def _global_key_status(base: str) -> str:
        """Validate the key in the GLOBAL store the mint would overwrite
        (#1708 fixer, P1: env/cwd key-shadow defeats remint idempotency).

        The 401/403 source may be a higher-precedence key (env or a legacy
        cwd/.tortoise config) that still shadows the global store at read
        time — re-running would re-validate the dead source and mint ANOTHER
        team (the exact duplicate-mint incident #1708 fixes). When the
        shadowing source is rejected, check the store the mint would write:

          "valid"         — GET /v1/team 200 → reuse instead of minting.
          "invalid"       — no store / no key / 401 / non-SUSPENDED 403 → mint.
          "suspended"     — SUSPENDED 403 → fail closed, never mint over it.
          "unvalidatable" — 429/5xx/network/timeout/garbage → fail closed.
        """
        if not config_path.is_file():
            return "invalid"
        try:
            store = json.loads(config_path.read_text())
        except (json.JSONDecodeError, ValueError, OSError):
            # ValueError: UnicodeDecodeError from read_text (invalid UTF-8) is
            # a ValueError subclass — a corrupt store is FAIL-CLOSED (D6):
            # never mint over an unreadable store (the team it belonged to
            # would be orphaned and its signup budget silently burned).
            return "unvalidatable"
        if not isinstance(store, dict):
            return "unvalidatable"
        store_key = store.get("api_key")
        if not isinstance(store_key, str) or not store_key.strip():
            return "invalid"
        store_base = (store.get("api_url") or base).rstrip("/")
        try:
            req = Request(
                f"{store_base}/v1/team",
                headers={"Authorization": f"Bearer {store_key}"},
            )
            with urlopen(req, timeout=10) as resp:
                json.loads(resp.read())
            return "valid"
        except HTTPError as e:
            body = e.read().decode() if e.fp else ""
            if e.code in (401, 403):
                if e.code == 403 and _suspended_info(body) is not None:
                    return "suspended"
                return "invalid"
            return "unvalidatable"
        except (URLError, ValueError, json.JSONDecodeError, TimeoutError, OSError):
            return "unvalidatable"
    if not force:
        try:
            cfg_path, cfg, existing_key, existing_url = _resolve_config_path()
        except _ConfigError as e:
            # #1756: a config that parses with a signup_token but no api_key
            # is the EXACT state `tortoise recover` is designed for — the
            # corrupt-config boilerplate ("fix or delete it, or use --force")
            # is destructive here: deleting destroys the recovery token and
            # --force mints a NEW team, orphaning the old one. Point at
            # recovery; genuinely corrupt files keep the boilerplate below.
            token_only, shadow = _token_only_config_path()
            if token_only is not None:
                if shadow is not None:
                    # "key lost" is false here: a later candidate holds a
                    # usable key and the token-only file only shadows it —
                    # recover would write the global store and leave the
                    # shadow in place (the next command fails identically).
                    print(f"⚠️  Found a recovery token but no API key in "
                          f"{token_only} — however, a usable API key exists "
                          f"in {shadow}. Your key is NOT lost: you can delete "
                          f"{token_only} (or run `tortoise recover` to mint a "
                          f"fresh key on the same team).",
                          file=sys.stderr)
                else:
                    print(f"⚠️  Found a recovery token but no API key in "
                          f"{token_only} — your key was likely lost. Run "
                          "`tortoise recover` to get a NEW key on the SAME team "
                          "(data intact). Do NOT delete this file and do NOT use "
                          "--force — that would orphan your team.",
                          file=sys.stderr)
                return 1
            print(f"Config at {e} is corrupt or unreadable — fix or delete it, "
                  "or use --force.", file=sys.stderr)
            return 1  # never mint on a corrupt config (D6)
        if existing_key:
            base = (existing_url or api_url).rstrip("/")
            try:
                req = Request(
                    f"{base}/v1/team",
                    headers={"Authorization": f"Bearer {existing_key}"},
                )
                with urlopen(req, timeout=10) as resp:
                    json.loads(resp.read())
                src = str(cfg_path) if cfg_path else "TORTOISE_API_KEY"
                print(f"✅ Already have a Tortoise Cloud key ({src}) — reusing it.")
                print("   Run 'tortoise team keys' or 'tortoise create-point \"hello\"' to use it.")
                print("   To mint a fresh key instead: tortoise signup --force")
                return 0
            except HTTPError as e:
                body = e.read().decode() if e.fp else ""
                if e.code in (401, 403):
                    # #308: SUSPENDED 403 must NOT mint (mirrors the other
                    # _cmd_* team handlers — a suspended team must not be
                    # silently orphaned by a fresh anonymous mint).
                    sus = _suspended_info(body)
                    if sus is not None:
                        print(f"{sus[0]}", file=sys.stderr)
                        return 1
                    # #1708 fixer (P1): before minting, check the GLOBAL store
                    # the mint would write to. When the 401/403 came from a
                    # higher-precedence source (env/cwd), a valid global key
                    # must be REUSED — otherwise every re-run re-validates the
                    # dead source and mints ANOTHER team (the duplicate-mint
                    # incident). The store's own host is used when it differs.
                    if cfg_path != config_path:
                        gs = _global_key_status(base)
                        if gs == "valid":
                            shadow_src = ("TORTOISE_API_KEY" if cfg_path is None
                                          else f"{cfg_path} (cwd config)")
                            print(f"✅ Reusing your stored key at {config_path} — "
                                  f"{shadow_src} was rejected ({e.code}) and shadows it.")
                            print(f"   Unset TORTOISE_API_KEY or remove "
                                  f"{Path.cwd() / '.tortoise'} to use your stored key.",
                                  file=sys.stderr)
                            return 0
                        if gs == "suspended":
                            print("The stored key at ~/.tortoise/credentials.json is "
                                  "SUSPENDED — not minting over it. Resolve the "
                                  "suspension, delete the file, or use --force.",
                                  file=sys.stderr)
                            return 1
                        if gs == "unvalidatable":
                            print("Cannot validate the stored key at "
                                  "~/.tortoise/credentials.json — not minting to "
                                  "avoid duplicate keys. Retry later or use --force.",
                                  file=sys.stderr)
                            return 1
                    print(f"Stored key is invalid ({e.code}) — minting a fresh one.",
                          file=sys.stderr)
                    reminting_after_401 = True
                    mint_url = base  # re-mint against the validated config's host (D2)
                    # device_id backfill: a legacy cwd/.tortoise has none —
                    # persist it so client identity stays anchored (future #1709).
                    if isinstance(cfg, dict):
                        legacy_device_id = cfg.get("device_id")
                else:
                    print(f"Cannot validate existing key (API error {e.code}) — not "
                          "minting to avoid duplicate keys. Retry later or use --force.",
                          file=sys.stderr)
                    return 1
            except (URLError, ValueError, json.JSONDecodeError, TimeoutError, OSError) as e:
                # TimeoutError/OSError: socket.timeout from resp.read() after
                # headers arrive (flaky proxy/captive-portal stall) is NOT a
                # URLError — without it the D2 contract degrades into a traceback.
                print(f"Cannot validate existing key ({e}) — not minting to avoid "
                      "duplicate keys. Retry later or use --force.", file=sys.stderr)
                return 1

    # Stable device_id (#1708): reuse a previously stored one (server still
    # ignores it — #741(a) unchanged; it anchors CLIENT-side reuse and the
    # future #1709 dedupe). Read from the GLOBAL credentials store, plus the
    # legacy-config backfill captured above.
    stored = {}
    if config_path.is_file():
        try:
            stored = json.loads(config_path.read_text())
        except (json.JSONDecodeError, ValueError, OSError):
            # ValueError: UnicodeDecodeError (invalid UTF-8) — same as a parse
            # error: treat the store as absent, never traceback.
            stored = {}
        if not isinstance(stored, dict):
            stored = {}
    if not stored.get("device_id") and legacy_device_id:
        stored["device_id"] = legacy_device_id
    device_id = stored.get("device_id") or f"anon-{uuid.uuid4().hex[:12]}"

    # #1709: a stored st_ signup token re-presents the SAME team on re-signup
    # (keyless recovery — the dedupe check). Read from the active configs.
    stored_token = _read_stored_signup_token()
    if force:
        # #1709 fixer P2.4: --force is the documented escape hatch — a FRESH
        # mint, never a recovery. Without this the stored token was still
        # re-presented and --force silently performed a RECOVERY (a suspended
        # team + dead token could never be escaped). Clearing it here also
        # makes the recovery/fresh-mint branch distinction below purely
        # request-shaped (P2.5).
        stored_token = None

    while True:
        print(f"Signing up for a free hosted team (anonymous, no email)…")  # noqa: F541
        payload = {"identity": device_id}
        if stored_token:
            # #1709: token possession = the dedupe credential — the server
            # RECOVERS the same team (new key, no second team).
            payload["signup_token"] = stored_token
        try:
            req = Request(
                f"{mint_url}/v1/agent/signup",
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json", "X-Device-Id": device_id},
                method="POST",
            )
            with urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())
        except HTTPError as e:
            body = e.read().decode() if e.fp else ""
            if e.code == 429:
                # #1081 P3: friendly retry window + support pointer instead of the
                # raw JSON body. Retry-After is the RFC 7231 contract; tolerate
                # both seconds and (hypothetically) HTTP-date via isdigit guard.
                retry = (e.headers.get("Retry-After")
                         if e.headers and e.headers.get("Retry-After") else None)
                when = f"{int(retry)}s" if (retry and retry.isdigit()) else "later"
                if reminting_after_401:
                    # D2: a revoked stored key + exhausted budget — the user must
                    # not be told to "wait" as if nothing else is wrong.
                    print(f"Signup rate limit reached — try again in {when}. "
                          "Note: your stored key is ALSO invalid — fix or revoke it, "
                          "or --force a fresh mint after the window. "
                          "Need more keys? Contact support@premiselabs.co.",
                          file=sys.stderr)
                else:
                    print(f"Signup rate limit reached — try again in {when}. "
                          "Need more keys? Contact support@premiselabs.co.",
                          file=sys.stderr)
                return 1
            if e.code == 422 and stored_token and _is_invalid_signup_token(body):
                # #1709 P3: a revoked/truncated token must NOT silently orphan the
                # original team — warn FIRST and require confirmation before
                # clearing the token + minting a NEW team. Non-interactive runs
                # fail CLOSED (no mint, no orphan).
                print("⚠️  Your recovery token is invalid — this will create a NEW "
                      "team; the old team will be unreachable.", file=sys.stderr)
                if not sys.stdin.isatty():
                    print("Non-interactive — aborting. Remove the invalid token from "
                          "your config and re-run to mint fresh.", file=sys.stderr)
                    return 1
                ans = input("Type YES to continue with a fresh team: ")
                if ans.strip() != "YES":
                    print("Aborted — no new team created.", file=sys.stderr)
                    return 1
                stored_token = None  # cleared; fresh mint below
                continue
            if e.code == 403:
                sus = _suspended_info(body)
                if sus is not None:
                    print(f"This team is suspended: {sus[0]}", file=sys.stderr)
                    return 1
            print(f"Signup failed ({e.code}): {body}", file=sys.stderr)
            return 1
        except URLError as e:
            print(f"Cannot reach API at {mint_url}: {e}", file=sys.stderr)
            return 1
        except (ValueError, json.JSONDecodeError, TimeoutError, OSError):
            # 200-with-garbage (proxy/mitm) or a stall after the server accepted:
            # the server DID mint — "Cannot reach API" would mislead the user into
            # retrying (the double-fire pattern).
            print("A key may have been minted but the response was unreadable — "
                  "check the dashboard or support before re-running; "
                  "do NOT blindly retry.", file=sys.stderr)
            return 1
    
        break
    if not (isinstance(data, dict) and "key" in data):
        # 200 with valid JSON but no `key` (edge/proxy): the server may still
        # have minted — same fail-soft contract as the garbage-200 leg above.
        # data["key"] must never be dereferenced outside the try (that was an
        # unhandled KeyError traceback, not the documented fail-soft path).
        print("A key may have been minted but the response was malformed "
              "(no 'key' field) — check the dashboard or support before "
              "re-running; do NOT blindly retry.", file=sys.stderr)
        return 1

    # Save config so the key works immediately — global credentials store
    # (#1708 D4): ~/.tortoise/credentials.json (0600), dir 0700, atomic
    # unique-tmp write — fixes the IsADirectoryError crash when cwd == ~
    # (previously wrote to cwd/.tortoise which IS the data home directory).
    # #1709: the signup_token is an ADDITIVE field (mint → the fresh token;
    # recovery → the stored token is kept — the server never re-issues tokens).
    # #1709 fixer P2.5: a response signup_token is ONLY authoritative on the
    # fresh-mint branch — distinguish by whether the request PRESENTED a token
    # (stored_token non-None here ⟺ the successful request was a recovery; the
    # 422-confirm branch cleared it before continuing). A proxy-injected
    # signup_token on the recovery branch must never overwrite the real
    # stored credential.
    new_token = data.get("signup_token") if not stored_token else None
    config = {
        "api_key": data["key"],
        "api_url": mint_url,
        "team_id": data.get("team_id"),
        "team_name": data.get("team_name"),
        "device_id": device_id,
    }
    if new_token:
        config["signup_token"] = new_token
    elif stored_token:
        config["signup_token"] = stored_token
    try:
        home_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(home_dir, 0o700)
        # Sweep stale tmp-* files (a crashed writer leaves one behind — key
        # material residue; unbounded accumulation is the failure mode).
        # Age-guarded (#1708 fixer, P2): sweeping FRESH tmps lets concurrent
        # writers delete each other's in-flight file → FileNotFoundError on
        # os.replace → a spurious "minted but could NOT be saved" orphan.
        now = time.time()
        for stale in home_dir.glob("credentials.json.tmp-*"):
            try:
                if now - stale.stat().st_mtime > 3600:
                    stale.unlink()
            except OSError:
                pass
        # tmp born at 0600 via os.open(O_EXCL) — write_text would create at
        # umask (0644) with a plaintext-key window before the chmod; the
        # unique per-writer name prevents concurrent-writer clobbering.
        tmp_path = home_dir / f"credentials.json.tmp-{uuid.uuid4().hex}"
        fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(json.dumps(config, indent=2) + "\n")
        # NO suppress(FileNotFoundError) here: os.replace can never lose a
        # rename race (POSIX last-writer-wins; the target being absent is
        # fine). The only way it raises FileNotFoundError is a MISSING SOURCE
        # — the fresh unique tmp was deleted (e.g. forward clock jump makes
        # the age-guarded sweep eat it) — which means nothing was saved, and
        # a silent exit-0 would be a minted-but-lost key with a false
        # success. Let it fall through to the except OSError orphan handler
        # (echo key + exit 1) — the correct fail-closed outcome.
        os.replace(tmp_path, config_path)
    except OSError as e:
        # Orphan class: the key was minted but cannot be saved — echo it AND
        # the recovery token (the success-path shown-once contract) and fail
        # closed so the user never loses either and never silently re-mints
        # (the incident pattern #1750 fixes: a re-run minted a SECOND team
        # and the first's token was never shown).
        print(f"A key was minted but could NOT be saved to {config_path}: {e}",
              file=sys.stderr)
        print(f"Your API key (store it manually): {data['key']}", file=sys.stderr)
        orphan_token = config.get("signup_token")
        if orphan_token:
            print(f"   Recovery token (save it now): {orphan_token}", file=sys.stderr)
            print("   RECOVERY TOKEN — save this: it is the only way back into "
                  "this team if your key is lost.", file=sys.stderr)
        if stored_token:
            # recovery-orphan leg: the server recovered the SAME team, only the
            # save failed — re-running re-presents the token and re-recovers
            # the SAME team; no new team is created (review P2, #1750).
            print("Fix the path permissions and re-run — recovery is re-attempted "
                  "on the SAME team (no new team is created). "
                  "The key+token above are your only access until then.",
                  file=sys.stderr)
        elif orphan_token:
            # fresh-mint-orphan leg: a re-run with no stored key mints a SECOND
            # team and orphans the first — warn hard (the incident pattern).
            print("Fix the path permissions and re-run, or use the key directly — "
                  "but do NOT re-run blindly: this creates a NEW team; the old "
                  "team's key+token above are your only access.", file=sys.stderr)
        else:
            # fresh mint returned no token — only the key exists above.
            print("Fix the path permissions and re-run, or use the key directly — "
                  "but do NOT re-run blindly: this creates a NEW team; the API "
                  "key above is your only access.", file=sys.stderr)
        return 1

    # D3 (generalized, #1708 fixer P1): whenever a mint happened while a
    # higher-precedence source (env or a legacy cwd/.tortoise config) still
    # shadows the global store at read time (env → cwd → global per D1), warn —
    # the new key won't be used until the shadow is removed. Previously gated
    # on --force only, so the env-401 remint path exited 0 with NO warning
    # (the dead shadow that re-401s every subsequent run → duplicate mints).
    shadow_srcs = []
    if os.environ.get("TORTOISE_API_KEY", "").strip():
        shadow_srcs.append("TORTOISE_API_KEY (env wins)")
    cwd_cfg = Path.cwd() / ".tortoise"
    if cwd_cfg.is_file():
        shadow_srcs.append(f"{cwd_cfg} (cwd wins over ~/.tortoise)")
    if shadow_srcs:
        print(f"⚠️  {' and '.join(shadow_srcs)} shadow{'s' if len(shadow_srcs) == 1 else ''} "
              "this new key at read time — unset/remove it to use the key just minted.",
              file=sys.stderr)

    # #1751: the success message must key off whether a token was PRESENTED
    # (stored_token — a recovery) vs whether the mint RETURNED one (new_token —
    # a fresh mint). A fresh mint whose response lacks signup_token (server
    # version skew / a field-stripping proxy) is NOT a recovery — "data intact"
    # would be false — so say "Free team created" and warn the recovery
    # backdoor was not issued (the same fail-soft contract as the missing-key
    # leg: never misreport, never silently drop a credential).
    if stored_token:
        print(f"✅ Key recovered on existing team: {data.get('team_name')} (data intact)")
    else:
        print(f"✅ Free team created: {data.get('team_name')}")
        if not new_token:
            print("⚠️  Recovery backdoor NOT created — the server did not return "
                  "a signup token; you cannot use `tortoise recover` for this "
                  "team. The API key above is your only access — store it safely.",
                  file=sys.stderr)
    print(f"   API key: {data['key']}")
    print(f"   Config saved to {config_path} (shown once — store it)")
    if new_token:
        # #1709: the recovery token is the SINGLE save point (the only way
        # back into this team if the key is lost). Shown once, like the key.
        print(f"   Recovery token: {new_token}")
        print("   RECOVERY TOKEN — save this: it is the only way back into "
              "this team if your key is lost.")
    if getattr(args, "claim", False):
        # #1082: the anonymous team can attach a verified identity (same key,
        # same team, memories intact) — one-time human act, no device flow.
        dashboard = os.environ.get(
            "TORTOISE_DASHBOARD_URL", "https://app.premiselabs.co")
        print()
        print("🔐 Claim your team (optional but recommended):")
        print(f"   1. Open {dashboard}")
        print(f"   2. Sign in with GitHub or Google")  # noqa: F541
        print(f"   3. Paste this key when prompted:")  # noqa: F541
        print(f"      {data['key']}")
        print("   Your verified identity attaches to THIS team — same key,"
              " same graph, memories intact.")
    print(f"   Next: tortoise create-point \"hello world\" --kind statement")  # noqa: F541
    return 0


def _cmd_team_info(args) -> int:
    """Show team info from Tortoise Cloud API."""
    import json, sys  # noqa: E401, I001
    from urllib.request import Request, urlopen
    from urllib.error import URLError, HTTPError

    # Read config
    config, api_key, api_url = _read_config()  # noqa: RUF059
    if api_key is None:
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


ONBOARDING_PROMPT_URL = "https://premiselabs.co/onboarding-prompt.md"


class _ConfigError(Exception):
    """Candidate config file exists but is corrupt or unreadable."""


def _resolve_config_path(include_env: bool = True) -> tuple[Path | None, dict | None, str | None, str | None]:
    """Shared config resolver — env → cwd/.tortoise → ~/.tortoise/credentials.json (#1708 D1/D5/D6).

    Returns (config_path, config, api_key, api_url) or (None, None, None, None).
    INVARIANT: whenever api_key is not None, config is a dict (env candidate is
    synthesized as {"api_key": key, "api_url": url} — callers like
    _cmd_team_keys_list do config.get(...) unconditionally and must never see None).
    - Empty/whitespace env TORTOISE_API_KEY (.strip()) is treated as unset
      (prevents a lockout shadow where a bad env key beats a good stored one).
    - A candidate file that exists but fails JSON parse / is unreadable / has a
      non-string api_key raises _ConfigError(path) (catch (OSError, JSONDecodeError,
      TypeError)). An empty api_key in a file is treated as "no config here" —
      the next candidate wins.
    - A directory at a candidate path (cwd/.tortoise in some repos; the global
      path's directory is by design) is skipped as "no config here" (D5).
    - include_env=False: file candidates only (used by _cmd_context, D1b — env
      alone must never flip the local-memory SessionStart hook to hosted mode).
    """
    import json as _json
    import os as _os
    from pathlib import Path

    if include_env:
        env_key = _os.environ.get("TORTOISE_API_KEY")
        if env_key and env_key.strip():
            env_url = _os.environ.get("TORTOISE_API_URL", "https://api.premiselabs.co")
            return None, {"api_key": env_key, "api_url": env_url}, env_key, env_url

    candidates = (Path.cwd() / ".tortoise", Path.home() / ".tortoise" / "credentials.json")
    for path in candidates:
        if not path.is_file():
            continue
        try:
            config = _json.loads(path.read_text())
        except (OSError, _json.JSONDecodeError, ValueError, TypeError) as e:
            # ValueError: UnicodeDecodeError from read_text (invalid UTF-8) is
            # a ValueError subclass — same corrupt-config contract as a parse error.
            raise _ConfigError(path) from e
        if not isinstance(config, dict):
            # Parses but isn't an object ([1,2,3]) — config.get would raise
            # AttributeError outside the try; the documented contract is
            # _ConfigError (fixer cycle 1, P2).
            raise _ConfigError(path)
        api_key = config.get("api_key")
        if not isinstance(api_key, str):
            raise _ConfigError(path)
        if not api_key.strip():
            continue
        api_url = config.get("api_url") or _os.environ.get("TORTOISE_API_URL", "https://api.premiselabs.co")
        return path, config, api_key, api_url
    return None, None, None, None


def _read_config(json_mode: bool = False) -> tuple[dict | None, str | None, str | None]:
    """Read the resolved config → (config, api_key, api_url) (env → cwd → global).

    Thin wrapper over _resolve_config_path preserving the legacy 3-tuple +
    _cmd_fail contract for the hosted-team commands (team info, team keys *).
    Prints the failure reason to stderr; callers must return 1 when api_key is
    None. With json_mode, also emits the machine-readable error on stdout so
    the --json contract holds even for config failures (#875 P2).
    """
    try:
        _path, config, api_key, api_url = _resolve_config_path()
    except _ConfigError as e:
        reason = str(e.__cause__) if e.__cause__ else "api_key missing or invalid"
        return (_cmd_fail(json_mode, "no_config",
                          f"Invalid config at {e}: {reason}"), None, None)
    if api_key is None:
        return (_cmd_fail(json_mode, "no_config",
                          "No .tortoise config found. Run 'tortoise init --api-key <key>' first, "
                          "or run 'tortoise signup' for a free hosted key."), None, None)
    return config, api_key, api_url


def _emit_json(obj: dict) -> None:
    """Print a JSON object to stdout (machine-consumable output)."""
    import json as _json
    print(_json.dumps(obj, indent=2))


def _cmd_fail(json_mode: bool, error: str, message: str, **extra: object) -> int:
    """CLI failure contract (#875 P2): human text on stderr, and when --json
    is passed the same machine JSON on stdout that init --json emits
    ({status: "error", error, message, ...}). Returns 1 for the caller.
    """
    print(message, file=sys.stderr)
    if json_mode:
        payload: dict = {"status": "error", "error": error, "message": message}
        payload.update(extra)
        _emit_json(payload)
    return 1


def _suspended_info(body: str) -> tuple[str, str | None] | None:
    """#308 (R5): parse a 403 body for the SUSPENDED detail code.

    Returns (message, appeal_url) when the team is suspended, else None —
    unparseable/other bodies keep each caller's pre-#308 behavior."""
    try:
        import json as _j
        detail = (_j.loads(body) or {}).get("detail")
    except Exception:
        return None
    if not isinstance(detail, dict) or detail.get("code") != "SUSPENDED":
        return None
    msg = detail.get("message") or "This team has been suspended due to unusual activity."
    url = detail.get("appeal_url")
    return (f"{msg} Appeal: {url}" if url else msg, url)


def _harness_mcp_config(harness: str, api_key: str, api_url: str) -> dict:
    """MCP config for one harness — mirrors website/welcome.html (#497/#529).

    Hosted (HTTP) shapes — pinned by tests/test_onboarding_variants.py T3:
    - claude: {"mcpServers": {"tortoise": {"type": "http", ...}}} — a url
      entry WITHOUT "type" is skipped by Claude Code; the page's .mcp.json
      alternative pins type:http.
    - cursor / pi: same but WITHOUT `type` (Cursor/Pi remote servers take
      url+headers; the page's canonical blocks carry no type for these).
    - codex: shell command — Codex manages its own config (no file to write).

    Headers use env expansion (${TORTOISE_API_KEY} / ${env:TORTOISE_API_KEY})
    exactly like the page's canonical blocks — no literal key on disk.
    """
    endpoint = api_url.rstrip("/") + "/mcp/"
    if harness == "codex":
        return {"command": f"codex mcp add tortoise --url {endpoint} --bearer-token-env-var TORTOISE_API_KEY"}
    header = "${env:TORTOISE_API_KEY}" if harness == "cursor" else "${TORTOISE_API_KEY}"
    server: dict = {
        "url": endpoint,
        "headers": {"Authorization": f"Bearer {header}"},
    }
    if harness == "claude":
        server = {"type": "http", **server}
    return {"mcpServers": {"tortoise": server}}


def _harness_stdio_config(harness: str) -> dict:
    """Stdio MCP config for one harness (self-hosted) — mirrors website/self-hosted.html (#529).

    Shapes:
    - claude / pi: {"command", "args", "env"} — neither client requires `type`
    - cursor: same but WITH `type: "stdio"` — Cursor docs mark `type` required
      for stdio servers (remote url-based servers must NOT have it)
    - codex: shell command — Codex manages its own config (no file to write)
    """
    if harness == "codex":
        return {"command": "codex mcp add tortoise -- python3 -m tortoise.mcp_server"}
    server: dict = {
        "command": "python3",
        "args": ["-m", "tortoise.mcp_server"],
        "env": {"TORTOISE_DB_URI": "docker://localhost:6379"},
    }
    if harness == "cursor":
        server = {"type": "stdio", **server}
    return {"mcpServers": {"tortoise": server}}


def _harness_label(harness: str) -> str:
    return {"claude": "Claude Code", "codex": "Codex", "cursor": "Cursor", "pi": "Pi"}.get(harness, harness)


def _print_mcp_configs(api_key: str, api_url: str, harness: str | None) -> None:
    """Print per-harness MCP config (hosted HTTP shape, #304/#981).

    With --harness, print only that harness; without, print the selector UI.
    Shapes mirror website/welcome.html Block A (T3-pinned): claude's CLI
    one-liner + type:http .mcp.json alternative, env-expansion forms for
    cursor/pi, codex mcp add command.
    """
    import json as _json
    endpoint = api_url.rstrip("/") + "/mcp/"
    if harness:
        print(f"── MCP Configuration (HTTP) — {_harness_label(harness)} ──")
        if harness == "codex":
            print("Codex manages its own config — run:")
            print(f"  export TORTOISE_API_KEY={api_key}")
            cfg = _harness_mcp_config("codex", api_key, api_url)
            print(f"  {cfg['command']}")
        elif harness == "claude":
            print("Run this ONE command in your terminal:")
            print(f'  claude mcp add --transport http tortoise {endpoint} --header "Authorization: Bearer {api_key}"')
            print()
            print("File alternative (.mcp.json) — env expansion, no literal key on disk:")
            print(f"  export TORTOISE_API_KEY={api_key}")
            print(_json.dumps(_harness_mcp_config(harness, api_key, api_url), indent=2))
        else:  # cursor / pi
            print("Add this to your project's MCP config (env expansion — no literal key on disk):")
            print(f"  export TORTOISE_API_KEY={api_key}")
            print(_json.dumps(_harness_mcp_config(harness, api_key, api_url), indent=2))
        print()
        print(f"→ MCP endpoint: {endpoint}")
        print("→ Auth: Bearer <key> (or $TORTOISE_API_KEY)")
        return
    print("── MCP Configuration (HTTP) ──")
    print("Connect your agent to Tortoise Cloud. Pick your harness:")
    print()
    print("[1] Claude Code")
    print("[2] Codex")
    print("[3] Cursor")
    print("[4] Pi")
    print()
    print(f"→ MCP endpoint: {endpoint}")
    print("→ Auth: Bearer <key> (or $TORTOISE_API_KEY)")


def _write_mcp_config_file(api_key: str, api_url: str, harness: str, force: bool,
                           status_to_stderr: bool = False) -> int:
    """Write/merge .mcp.json for file-based harnesses (claude/cursor/pi).

    Codex is command-based (#497) — prints the registration command instead.
    Merge strategy: preserve existing mcpServers entries, overwrite only the
    tortoise entry (refused if it exists unless --force).

    The written entry uses env expansion (${TORTOISE_API_KEY} — page #529
    shape, no literal key on disk); the CLI prints the export line so the
    var is set in the user's shell. status_to_stderr keeps stdout pure JSON
    for --json callers.
    """
    import json as _json
    import os
    from pathlib import Path

    if harness == "codex":
        print("Codex manages its own config — run:")
        print(f"  export TORTOISE_API_KEY={api_key}")
        cfg = _harness_mcp_config("codex", api_key, api_url)
        print(f"  {cfg['command']}")
        return 0

    target = Path.cwd() / ".mcp.json"
    entry = _harness_mcp_config(harness, api_key, api_url)["mcpServers"]["tortoise"]
    if target.exists():
        try:
            data = _json.loads(target.read_text())
        except _json.JSONDecodeError:
            print(f"❌ {target} is not valid JSON — fix or remove it, then retry.", file=sys.stderr)
            return 1
        if not isinstance(data, dict):
            print(f"❌ {target} is not a JSON object — fix or remove it, then retry.", file=sys.stderr)
            return 1
        servers = data.setdefault("mcpServers", {})
        if "tortoise" in servers and not force:
            print(f"⚠️  tortoise MCP server already configured in {target}. Use --force to overwrite.", file=sys.stderr)
            return 1
        servers["tortoise"] = entry
    else:
        data = {"mcpServers": {"tortoise": entry}}
    status = sys.stderr if status_to_stderr else sys.stdout
    # The written config references $TORTOISE_API_KEY (page #529 env form) —
    # make sure the var is set in the shell that will launch the agent.
    print(f"ℹ️  This config reads $TORTOISE_API_KEY — export it first:", file=status)  # noqa: F541
    print(f"   export TORTOISE_API_KEY={api_key}", file=status)
    target.write_text(_json.dumps(data, indent=2) + "\n")
    os.chmod(target, 0o600)
    print(f"✅ Wrote MCP config to {target}", file=status)
    print("ℹ️  No literal key in the file — keep $TORTOISE_API_KEY exported in your shell.", file=status)
    return 0


def _cmd_team_keys_list(args) -> int:
    """List team API keys (GET /v1/team/keys). Hashes only — no plaintext."""
    import json as _json  # noqa: I001
    from urllib.request import Request, urlopen
    from urllib.error import URLError, HTTPError

    json_mode = getattr(args, "json", False)
    config, api_key, api_url = _read_config(json_mode=json_mode)
    if api_key is None:
        return 1

    try:
        req = Request(
            f"{api_url}/v1/team/keys",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        with urlopen(req, timeout=10) as resp:
            data = _json.loads(resp.read())
    except HTTPError as e:
        body = e.read().decode() if e.fp else ""
        if e.code in (401, 403):
            sus = _suspended_info(body)  # #308
            if sus is not None:
                return _cmd_fail(json_mode, "team_suspended",
                                 f"Team suspended — {sus[0]}", http_code=e.code,
                                 appeal_url=sus[1])
            return _cmd_fail(json_mode, "key_rejected",
                             "API key rejected — re-run tortoise init --api-key", http_code=e.code)
        return _cmd_fail(json_mode, "api_error", f"API error ({e.code}): {body}", http_code=e.code)
    except (_json.JSONDecodeError, ValueError) as e:
        return _cmd_fail(json_mode, "invalid_response", f"Invalid response from API: {e}")
    except URLError as e:
        return _cmd_fail(json_mode, "network", f"Cannot reach API at {api_url}: {e.reason}")

    if not isinstance(data, dict):
        # 2xx with a non-object body ([]/null) — clean error, not AttributeError.
        return _cmd_fail(
            json_mode, "invalid_response",
            "Invalid response from API: expected a JSON object, got "
            f"{type(data).__name__}.")

    keys = data.get("keys", [])
    if getattr(args, "json", False):
        _emit_json({"team_id": config.get("team_id"), "keys": keys})
        return 0

    team_id = config.get("team_id")
    print(f"API keys for team {team_id}:" if team_id else "API keys:")
    print(f"  {'ID':<14}{'Name':<20}{'Prefix':<14}{'Created':<26}{'Last used':<26}Status")
    for k in keys:
        status = "revoked" if k.get("revoked_at") else "active"
        last = k.get("last_used_at") or "never"
        name = str(k.get('name') or '')[:20]
        print(f"  {str(k.get('id') or ''):<14}{name:<20}{str(k.get('key_prefix') or ''):<14}"  # noqa: RUF010
              f"{str(k.get('created_at') or ''):<26}{str(last):<26}{status}")  # noqa: RUF010
    return 0


def _cmd_team_keys_create(args) -> int:
    """Mint a new team API key (POST /v1/team/keys). Key shown exactly once."""
    import json as _json  # noqa: I001
    from urllib.request import Request, urlopen
    from urllib.error import URLError, HTTPError

    json_mode = getattr(args, "json", False)
    config, api_key, api_url = _read_config(json_mode=json_mode)
    if api_key is None:
        return 1

    # Optional label (20260825000001) — clamp client-side to the same 64-char
    # cap the API enforces, so a long label is truncated instead of erroring.
    name = getattr(args, "name", None)
    body = {}
    if name:
        body["name"] = str(name)[:64]
    try:
        req = Request(
            f"{api_url}/v1/team/keys",
            data=_json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(req, timeout=10) as resp:
            data = _json.loads(resp.read())
    except HTTPError as e:
        body = e.read().decode() if e.fp else ""
        if e.code == 402:
            return _cmd_fail(json_mode, "limit_reached",
                             "API key limit reached (max 3 for free tier). Revoke an existing key first.",
                             http_code=402)
        if e.code == 429:
            return _cmd_fail(json_mode, "rate_limited",
                             "Too many keys created recently — try again in 60s.", http_code=429)
        if e.code in (401, 403):
            sus = _suspended_info(body)  # #308
            if sus is not None:
                return _cmd_fail(json_mode, "team_suspended",
                                 f"Team suspended — {sus[0]}", http_code=e.code,
                                 appeal_url=sus[1])
            return _cmd_fail(json_mode, "key_rejected",
                             "API key rejected — re-run tortoise init --api-key", http_code=e.code)
        return _cmd_fail(json_mode, "api_error", f"API error ({e.code}): {body}", http_code=e.code)
    except (_json.JSONDecodeError, ValueError) as e:
        return _cmd_fail(json_mode, "invalid_response", f"Invalid response from API: {e}")
    except URLError as e:
        return _cmd_fail(json_mode, "network", f"Cannot reach API at {api_url}: {e.reason}")

    if not isinstance(data, dict):
        # 2xx with a non-object body ([]/null) — clean error, not AttributeError.
        return _cmd_fail(
            json_mode, "invalid_response",
            "Invalid response from API: expected a JSON object, got "
            f"{type(data).__name__}.")

    if getattr(args, "json", False):
        _emit_json({
            "key": data.get("key"),
            "key_prefix": data.get("key_prefix"),
            "id": data.get("id"),
            "created_at": data.get("created_at"),
            "name": data.get("name"),
            "team_id": config.get("team_id"),
        })
        return 0

    print(f"Created new API key: {data.get('key')}")
    print(f"  Key prefix:  {data.get('key_prefix')}")
    print(f"  Key ID:      {data.get('id')}")
    if data.get("name"):
        print(f"  Name:        {data.get('name')}")
    print(f"  ⚠️  Store this key — it won't be shown again.")  # noqa: F541
    print(f"  ⚠️  This key has full access to your team's graph.")  # noqa: F541
    return 0


def _cmd_team_keys_revoke(args) -> int:
    """Revoke a team API key (DELETE /v1/team/keys/{id}). Soft delete."""
    import json as _json  # noqa: I001
    from urllib.request import Request, urlopen
    from urllib.error import URLError, HTTPError

    json_mode = getattr(args, "json", False)
    config, api_key, api_url = _read_config(json_mode=json_mode)  # noqa: RUF059
    if api_key is None:
        return 1

    if not json_mode and not getattr(args, "force", False):
        try:
            answer = input(f"Revoke API key {args.key_id}? This cannot be undone. [y/N]: ")
        except EOFError:
            answer = "n"
        if answer.strip().lower() not in ("y", "yes"):
            return _cmd_fail(json_mode, "aborted", "Aborted.")

    try:
        req = Request(
            f"{api_url}/v1/team/keys/{args.key_id}",
            headers={"Authorization": f"Bearer {api_key}"},
            method="DELETE",
        )
        with urlopen(req, timeout=10) as resp:
            data = _json.loads(resp.read())
    except HTTPError as e:
        body = e.read().decode() if e.fp else ""
        if e.code == 404:
            return _cmd_fail(json_mode, "not_found", "API key not found", http_code=404)
        if e.code == 403:
            # #308: a suspended team's revoke also 403s — the SUSPENDED
            # detail (with appeal link) must not masquerade as cross-team.
            sus = _suspended_info(body)
            if sus is not None:
                return _cmd_fail(json_mode, "team_suspended",
                                 f"Team suspended — {sus[0]}", http_code=403,
                                 appeal_url=sus[1])
            return _cmd_fail(json_mode, "cross_team",
                             "Cannot revoke — this key belongs to a different team", http_code=403)
        if e.code == 401:
            return _cmd_fail(json_mode, "key_rejected",
                             "API key rejected — re-run tortoise init --api-key", http_code=401)
        return _cmd_fail(json_mode, "api_error", f"API error ({e.code}): {body}", http_code=e.code)
    except (_json.JSONDecodeError, ValueError) as e:
        return _cmd_fail(json_mode, "invalid_response", f"Invalid response from API: {e}")
    except URLError as e:
        return _cmd_fail(json_mode, "network", f"Cannot reach API at {api_url}: {e.reason}")

    if not isinstance(data, dict):
        # 2xx with a non-object body ([]/null) — clean error, not AttributeError.
        return _cmd_fail(
            json_mode, "invalid_response",
            "Invalid response from API: expected a JSON object, got "
            f"{type(data).__name__}.")

    if json_mode:
        out: dict = {"revoked": True, "key_id": args.key_id}
        if data.get("revoked_at"):
            out["revoked_at"] = data["revoked_at"]
        if data.get("already"):
            out["already"] = True
        _emit_json(out)
        return 0

    if data.get("already"):
        print(f"✅ API key {args.key_id} was already revoked (idempotent).")
    else:
        print(f"✅ API key {args.key_id} revoked.")
    return 0


def _cmd_create_point(args) -> int:
    """Create a Point via Tortoise Cloud API."""
    import json as _json, sys as _sys  # noqa: E401, I001
    from urllib.request import Request, urlopen
    from urllib.error import URLError, HTTPError

    # Shared resolver (#1708 D1): env → cwd/.tortoise → ~/.tortoise/credentials.json
    try:
        _cfg_path, _config, api_key, api_url = _resolve_config_path()
    except _ConfigError as e:
        print(f"Invalid config at {e} — fix or delete it, or run "
              "'tortoise init --api-key <key>'.", file=_sys.stderr)
        return 1
    if api_key is None:
        print("No .tortoise config found. Run 'tortoise init --api-key <key>' first.", file=_sys.stderr)
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
    import json as _json, os as _os, sys as _sys  # noqa: E401, I001

    # Shared resolver (#1708 D1b): file candidates only (cwd → global) — a
    # TORTOISE_API_KEY in dev shells must never silently flip this documented
    # local-memory SessionStart hook to hosted mode. Global-config presence
    # still flips it (a machine that ran `signup` has a hosted identity).
    api_key = None
    api_url = _os.environ.get("TORTOISE_API_URL", "https://api.premiselabs.co")
    try:
        _cfg_path, _cfg, api_key, api_url = _resolve_config_path(include_env=False)
    except _ConfigError as e:
        print(f"Warning: config at {e} is corrupt or unreadable — falling back "
              "to local memory mode.", file=_sys.stderr)
        api_key = None

    if api_key:
        # ── Hosted: query the API ──
        from urllib.request import Request, urlopen  # noqa: I001
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
    import sys  # noqa: I001
    from urllib.request import Request, urlopen  # noqa: F401
    from urllib.error import URLError, HTTPError  # noqa: F401

    # Shared resolver (#1708 D1): env → cwd/.tortoise → ~/.tortoise/credentials.json
    try:
        _cfg_path, _config, api_key, api_url = _resolve_config_path()
    except _ConfigError as e:
        print(f"Invalid config at {e} — fix or delete it, or run "
              "'tortoise init --api-key <key>'.", file=sys.stderr)
        return 1
    if api_key is None:
        print("No .tortoise config found. Run 'tortoise init --api-key <key>' first.", file=sys.stderr)
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
    import json as _json, sys as _sys  # noqa: E401, I001
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
    # P1 #1529: a status-only consumer must not report success on a failed
    # capture — extraction failures surface as HTTP 200 + additive body
    # errors with extraction_mode "error"/"empty" (the turn mutation already
    # happened; the body is the failure surface). Exit 1 with the errors on
    # stderr, never "Captured session: …" with extracted: 0.
    if result.get("extraction_mode") in ("error", "empty") or result.get("errors"):
        print(
            f"capture failed: {result.get('errors') or result.get('extraction_mode')}",
            file=_sys.stderr,
        )
        return 1
    print(f"Captured session: {session_id}")
    print(f"  Turns: {len(turns)}")
    print(f"  Source: {transcript_path.stem}")
    return 0


def _cmd_session_list(api_key: str, api_url: str) -> int:
    """GET /v1/sessions — list all sessions."""
    import json as _json, sys as _sys  # noqa: E401, I001
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
        print(f"{sid:<36} {str(turns):<6} {created}")  # noqa: RUF010
    return 0


def _cmd_session_view(args, api_key: str, api_url: str) -> int:
    """GET /v1/sessions/<id> — view session details."""
    import json as _json, sys as _sys  # noqa: E401, I001
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


def _resolve_db_target(explicit: str | None = None) -> str:
    """Resolve the DB target for `init` and the onboarding `index` step.

    Single source of truth (#705/#715): both _cmd_init and the onboard index
    step resolve through THIS function with ONE precedence, so they can never
    disagree (silent split graph, conf 60). Selection is purely environmental
    — no connectivity probing, because docker reachability must never override
    the configured target:

        explicit --path > TORTOISE_DB_URI (any supported scheme: docker://,
        redis://, rediss://) > FALKORDB_* legacy trio (constructed docker://
        URI, #715 migration guard) > TORTOISE_DB_PATH > canonical default

    Returns a URI string (URI mode) or an absolute embedded path (embedded
    mode). Raises ValueError for invalid input (e.g. a relative --path, or an
    invalid FALKORDB_PORT) via resolve_db_path's _abs guard.
    """
    import os  # noqa: I001
    from tortoise.config import resolve_db_path, is_db_uri

    if explicit:
        return resolve_db_path(explicit)
    uri = os.environ.get("TORTOISE_DB_URI", "")
    if is_db_uri(uri):
        return uri
    # Legacy FALKORDB_* trio (still in .env.example, still probed by doctor):
    # honor it as a docker:// target so users with ONLY FALKORDB_* set do NOT
    # silently switch Docker→embedded on upgrade (the split-graph failure mode
    # #705/#715 exists to kill — conf 55, migration must be loud, not silent).
    # Only triggers when the trio is EXPLICITLY set; a clean environment keeps
    # the embedded default (no localhost probe like the pre-#705 init did).
    # #715 P2 conf 68: empty-string FALKORDB_* values count as UNSET —
    # FALKORDB_PORT="" previously tripped the `any(...)` check and then
    # blew up with int('') instead of falling through to the default.
    if any((os.environ.get(k) or "").strip() for k in
           ("FALKORDB_HOST", "FALKORDB_PORT", "FALKORDB_PASSWORD")):
        host = os.environ.get("FALKORDB_HOST") or "localhost"
        port_raw = os.environ.get("FALKORDB_PORT") or "16379"
        try:
            port = int(port_raw)
        except (ValueError, TypeError):
            raise ValueError(  # noqa: B904
                f"Invalid FALKORDB_PORT={port_raw!r} — must be an integer")
        password = os.environ.get("FALKORDB_PASSWORD") or ""
        legacy_uri = f"docker://:{password}@{host}:{port}/tortoise"
        # #715 P2 conf 95: never print the password — a terminal/log line
        # must not leak the credential (masked display only).
        if password:  # noqa: SIM108
            display_uri = f"docker://:***@{host}:{port}/tortoise"
        else:
            display_uri = legacy_uri
        print(
            f"  ⚠️  FALKORDB_* env vars are legacy — constructed "
            f"{display_uri} (set TORTOISE_DB_URI to silence)")
        return legacy_uri
    return resolve_db_path(None)


def _uri_has_password(target: str) -> bool:
    """True if a DB URI embeds a password in its userinfo (:pw@ or user:pw@).

    #715 P2 conf 85: password-bearing targets must be handed to child
    processes via the TORTOISE_DB_URI env var, never via --db argv — argv
    leaks the secret into `ps` output.

    #715 P2 conf 75: a URI without userinfo has no `@` in its authority, so
    it cannot carry a password — docker://host:6379/db's ":6379" is a
    host:port, not a credential. Requiring the `@` separator prevents false
    password-bearing classification (which would route the target to the env
    handoff instead of the documented --db argv branch).
    """
    from tortoise.config import is_db_uri
    if not is_db_uri(target):
        return False
    authority = target.split("://", 1)[1]
    if "@" not in authority:
        return False
    userinfo = authority.split("@", 1)[0]
    return ":" in userinfo and userinfo.rsplit(":", 1)[1] != ""


def _mask_uri_userinfo(target: str) -> str:
    """Redact userinfo (user:password@) from a DB URI for display.

    #720 P2 conf 78: an error line must never print a credential embedded
    in the URI — docker://:pw@host:notaport/graph would leak ':pw@' to a
    terminal/log. Host/port/path stay visible for debuggability (matches
    the FALKORDB_* legacy display mask, conf 95).

    #720 P2 conf 68: the userinfo→host boundary is the LAST '@' of the
    authority region (everything before the first '?'/'#'), NOT
    urlsplit's netloc — a password may contain '/' (docker://user:p/ss@
    host:... is RFC-invalid but urlparse/redis-py accept it, and
    urlsplit's netloc cuts at the first '/'), which would split
    mid-credential and leak the tail. The '://' may also sit mid-message
    (RELATIVE_PATH_ERROR embeds the raw URI in prose), so every
    scheme:// pattern in the string is scanned, not just a leading one.
    """
    from urllib.parse import urlsplit

    def _scheme_ok(scheme: str) -> bool:
        # RFC 3986 scheme: alpha-led, alnum/+-. thereafter.
        return (scheme[:1].isalpha()
                and all(c.isalnum() or c in "+-." for c in scheme))

    out: list[str] = []
    i = 0
    while True:
        j = target.find("://", i)
        if j < 0:
            out.append(target[i:])
            break
        # Recover the scheme token by walking back from '://' over scheme
        # characters (stops at prose when the URI is embedded in a message).
        k = j
        while k > i and (target[k - 1].isalnum() or target[k - 1] in "+-."):
            k -= 1
        scheme = target[k:j]
        if not _scheme_ok(scheme):
            out.append(target[i:j + 3])
            i = j + 3
            continue
        if i == 0 and k == 0:
            # Bare URI — urlsplit is the authoritative scheme parse.
            try:
                if not urlsplit(target).scheme:
                    out.append(target[i:])
                    break
            except ValueError:
                pass  # malformed authority (e.g. unmatched '[') — mask below
        # The authority region runs to the earliest of: the start of the
        # next URI's scheme token (a second URI in the same message), or a
        # '?'/'#' delimiter (query/fragment never belong to userinfo — an
        # '@' in a query value must not swallow the host).
        rest_start = j + 3
        cut = None
        for c in ("?", "#"):
            pos = target.find(c, rest_start)
            if pos >= 0 and (cut is None or pos < cut):
                cut = pos
        nxt = target.find("://", rest_start)
        if nxt >= 0:
            s = nxt
            while s > rest_start and (target[s - 1].isalnum()
                                      or target[s - 1] in "+-."):
                s -= 1
            if s < nxt and (cut is None or s < cut):
                cut = s
        region_end = cut if cut is not None else len(target)
        authority = target[rest_start:region_end]
        at = authority.rfind("@")
        if at < 0:
            out.append(target[i:j + 3])
            i = j + 3
            continue
        out.append(target[i:k])
        out.append(f"{scheme}://:***@{authority[at + 1:]}")
        i = region_end
    return "".join(out)


def _index_github_child_cmd(target: str, repo_root: str,
                            branch: str | None = None
                            ) -> tuple[list[str], dict | None]:
    """Build argv/env for a background `index github` child spawn.

    #715 conf 60/75/85: the child must resolve the SAME target init/index
    chose. Password-bearing URI targets go via TORTOISE_DB_URI env (never
    argv); password-less targets keep the explicit --db so the child
    resolves identically even if the parent env changes later.
    """
    import os as _os
    import sys as _sys
    cmd = [_sys.executable, "-m", "tortoise", "index", "github", repo_root]
    if branch and branch != "main":
        cmd.extend(["--branch", branch])
    if _uri_has_password(target):
        env = dict(_os.environ)
        env["TORTOISE_DB_URI"] = target
        return cmd, env
    cmd.extend(["--db", target])
    return cmd, None


def _projection_for(target: str):
    """Build a FalkorProjection for a resolved target (URI or embedded path).

    #715 P2 conf 75: the single routing choke-point — URI targets go through
    from_uri, everything else is an embedded path. Commands must never
    hardcode a localhost default or silently fall back to embedded while a
    TORTOISE_DB_URI points elsewhere.
    """
    from tortoise.config import is_db_uri
    from tortoise.projection import FalkorProjection
    if is_db_uri(target):
        return FalkorProjection.from_uri(target)
    return FalkorProjection(path=target)


def _cmd_onboard(args) -> int:
    """Guided onboarding: init → index → demo → doctor.

    Chains existing commands into a cohesive flow.
    Non-interactive — skips prompts, just runs.
    Idempotent — re-running skips already-done steps.
    """
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
    # #715 P2 conf 70: no_index=True disables init's own background
    # auto-index — onboard indexes ONCE, inline in step 3 below. Without it
    # a git repo gets indexed twice (init spawn + inline run).
    banner("Initialize graph")
    rc = _cmd_init(argparse.Namespace(
        path=getattr(args, 'path', None), yes=True, no_index=True))
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
            # #1362: frontmatter validation is OPTIONAL + warn-only — surface
            # the shared flag so onboard users know the gate is on.
            from tortoise.frontmatter_validator import validation_enabled
            if validation_enabled():
                print("  frontmatter validation ON — session files must carry "
                      "sessionId/topics/summary (warnings only, never a hard fail)")
            # #705: pass the SAME resolved DB target init used (docker:// URI
            # or embedded path honoring --path / TORTOISE_DB_PATH) so index
            # never silently writes to the default DB.
            try:
                db_target = _resolve_db_target(getattr(args, 'path', None))
            except ValueError as e:
                # Bad --path (e.g. relative) — clean CLI error, no traceback
                # (#715). init rejects it first, but guard this call site too.
                # #720 P2 conf 95: mask userinfo (no-op for plain paths).
                print(f"  ❌ Invalid DB target: {_mask_uri_userinfo(str(e))}", file=_sys.stderr)
                return 1
            idx_args = argparse.Namespace(
                url=repo_root, background=False, branch="main",
                index_cmd="github", cmd="index",
                db=db_target,
            )
            rc_index = _cmd_index_github(idx_args)
            if rc_index != 0:
                print("  ❌ Index failed — see messages above")
                return rc_index
        else:
            print("  ⊙ No markdown files found — skipping index.")
    else:
        print("  ⊙ Not a git repo — skipping index.")

    # Step 4: First demo — create first memory
    banner("First memory demo")
    _cmd_demo(argparse.Namespace(cmd="demo"))

    # Step 5: Doctor — health check (informational, don't fail on warnings)
    # #720 conf 80: forward the SAME resolved target onboard wrote to
    # (--path, TORTOISE_DB_URI, FALKORDB_* or TORTOISE_DB_PATH) into doctor
    # — previously doctor re-resolved with a bare Namespace and health-
    # checked the DEFAULT graph, not the one onboard just initialized and
    # indexed (single-source violation, #715). Passed as args.db for URI
    # targets / args.path for embedded paths so doctor probes and checks
    # exactly the graph onboard wrote to.
    banner("Health check")
    try:
        db_target = _resolve_db_target(getattr(args, 'path', None))
    except ValueError as e:
        # #720 P2 conf 95: mask userinfo — unsupported-scheme URIs fall into
        # RELATIVE_PATH_ERROR with the RAW URI embedded (no-op for plain paths).
        print(f"  ❌ Invalid DB target: {_mask_uri_userinfo(str(e))}", file=_sys.stderr)
        return 1
    from tortoise.config import is_db_uri
    if is_db_uri(db_target):
        _cmd_doctor(argparse.Namespace(cmd="doctor", db=db_target, path=None))
    else:
        _cmd_doctor(argparse.Namespace(cmd="doctor", db=None, path=db_target))

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


def _cmd_export(args) -> int:
    """tortoise export — versioned, encrypted, portable graph artifact (#1388).

    Wraps the production-verified logical dump (hosted_backup.dump_graph) in a
    ``tortoise-export-v1`` envelope and encrypts by default (AES-256-GCM).
    Key: TORTOISE_BACKUP_KEY env if set, else a fresh ephemeral key printed
    once on the stdout JSON line (never written to disk).

    Exit codes: 0 ok · 1 pre-walk or graph-unavailable failure.
    Stdout: ONE JSON line (machine contract) unless --no-json.
    """
    import base64 as _b64  # noqa: I001
    import json as _json
    import os as _os
    from datetime import datetime, timezone as _tz
    from pathlib import Path

    from tortoise.config import is_db_uri, resolve_db_path
    from tortoise.export import (
        ARTIFACT_VERSION,
        EXPORT_FORMAT,
        artifact_bytes,
        build_artifact,
    )
    from tortoise.hosted_backup import dump_graph
    from tortoise.projection import FalkorProjection

    encrypted = not args.no_encrypt

    try:
        target = _resolve_db_target(args.db)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    is_uri = is_db_uri(target)
    try:
        proj = (
            FalkorProjection.from_uri(target)
            if is_uri
            else FalkorProjection(resolve_db_path(target))
        )
    except Exception as e:
        print(f"Error: cannot open graph {target!r}: {e}", file=sys.stderr)
        return 1

    source_surface = "selfhost" if is_uri else "embedded"

    if encrypted:
        try:
            from tortoise.export import resolve_export_key
            key, ephemeral = resolve_export_key(
                _os.environ.get("TORTOISE_BACKUP_KEY", "")
            )
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            proj.close()
            return 1
    else:
        key, ephemeral = None, False

    try:
        dump = dump_graph(proj.g, graph_name=proj.graph_name)
    except Exception as e:
        print(f"Error: graph dump failed: {e}", file=sys.stderr)
        proj.close()
        return 1
    proj.close()

    try:
        artifact = build_artifact(
            dump, key=key, source_surface=source_surface, encrypted=encrypted
        )
    except Exception as e:
        print(f"Error: envelope build failed: {e}", file=sys.stderr)
        return 1

    if args.output is None:
        args.output = f"graph-{datetime.now(_tz.utc).strftime('%Y-%m-%d')}.tortoise"  # noqa: UP017
    out = Path(args.output)
    try:
        out.write_bytes(artifact_bytes(artifact))
    except OSError as e:
        print(f"Error: cannot write {out}: {e}", file=sys.stderr)
        return 1

    summary = {
        "status": "ok",
        "output": str(out),
        "format": EXPORT_FORMAT,
        "artifact_version": ARTIFACT_VERSION,
        "encrypted": encrypted,
        "algorithm": artifact["algorithm"],
        "key_fingerprint": artifact["key_fingerprint"],
        "source_surface": source_surface,
        "node_count": dump["node_count"],
        "edge_count": dump["edge_count"],
        "blob_sha256": artifact["blob_sha256"],
        "exported_at": artifact["exported_at"],
    }
    if ephemeral:
        # The one and only place the fresh key is printed — never persisted.
        summary["key_b64"] = _b64.b64encode(key).decode("ascii")
    if args.json:
        print(_json.dumps(summary))
    else:
        print(
            f"Exported {dump['node_count']} nodes / {dump['edge_count']} edges → {out}"
        )
    if ephemeral:
        print(
            "⚠️  Fresh export key generated (never stored): keep the key_b64 value "
            "from the JSON line above — you need it to decrypt/import this artifact.",
            file=sys.stderr,
        )
    if not encrypted:
        print(
            "⚠️  WARNING: --no-encrypt — the artifact contains the FULL graph in "
            "plaintext. Anyone with file access can read every point. Prefer the "
            "default encryption.",
            file=sys.stderr,
        )
    return 0


def _cmd_validate(args) -> int:
    """tortoise validate --domain <slug> — advisory, read-only domain
    integrity validation (issue #405). Runs the domain's graph-surface
    validators (orphan useCase, dangling refs, draft hygiene) against the
    live graph; drift warnings flag manifest chains with no registered rule.

    Exit codes (documented contract — CI-actionable): 0 clean · 1 violations
    found · 2 usage error (missing/unknown domain) · 3 runtime/DB failure.
    ``--warn-only`` reports violations but exits 0 (pure advisory escape).
    """
    import sys as _sys
    domain = getattr(args, "domain", None)
    if not domain:
        print("tortoise validate: --domain is required "
              "(e.g. 'tortoise validate --domain product-strategy')",
              file=_sys.stderr)
        return 2
    from tortoise.domain_loader import (  # noqa: I001
        SURFACE_GRAPH, domain_chain_spec, domain_validators, known_domains,  # noqa: F401
    )
    # Import the production validators module BEFORE the guard: its import-time
    # registrations populate the registry the guard reads. Without this, a
    # packs-less environment (pip wheel without packs/) sees an empty registry
    # and a VALID domain exits 2 (review P1, PR #1271).
    import tortoise.domain_validators  # noqa: F401  (side-effect registration)
    # Unknown domain = no registered validators AND no loaded pack (the
    # registry is the source of truth — validators register at import time,
    # so a pip-installed wheel without packs/ still validates).
    if not domain_validators(domain) and domain not in known_domains():
        known = ", ".join(sorted(known_domains())) or "none"
        print(f"tortoise validate: unknown domain {domain!r} "
              f"(known packs: {known})", file=_sys.stderr)
        return 2
    proj = None
    try:
        target = _resolve_db_target(getattr(args, "db", None))
        proj = _projection_for(target)
    except Exception as e:
        # Exit 3 = runtime/DB failure (connection refused, embedded probe
        # failure, invalid target) — advisory validate must never crash.
        print(f"tortoise validate: DB target failure: "
              f"{_mask_uri_userinfo(str(e))}", file=_sys.stderr)
        return 3
    try:
        from tortoise.domain_validators import run_domain_graph_validators
        violations, drift = run_domain_graph_validators(domain, proj)
    except Exception as e:
        print(f"tortoise validate: failed to run (domain={domain}): "
              f"{_mask_uri_userinfo(str(e))}", file=_sys.stderr)
        return 3
    finally:
        if proj is not None:
            try:  # noqa: SIM105
                proj.close()
            except Exception:
                pass

    chains = domain_chain_spec(domain)
    if getattr(args, "json", False):
        import json as _json
        print(_json.dumps({
            "domain": domain,
            "ok": not violations,
            "violations": violations,
            "drift": drift,
            "chains": chains,
        }, indent=2))
    else:
        print(f"tortoise validate: domain={domain}")
        for cid, cspec in chains.items():
            print(f"  chain {cid}: {', '.join(cspec['steps'])}"
                  f" (enforcement: {cspec['enforcement']})")
        for i, v in enumerate(violations, 1):  # noqa: B007
            print(f"  ✗ [{v.get('rule', '?')}] ({v.get('kind', '?')}) "
                  f"ref={v.get('ref', '?')}: {v.get('message', '?')}")
            fix = v.get("fix")
            if fix:
                print(f"      fix: {fix}")
        for d in drift:
            print(f"  ⚠ [{d.get('rule', '?')}] ref={d.get('ref', '?')}: "
                  f"{d.get('message', '?')}")
        if violations:
            print(f"\n{len(violations)} violation(s) · "
                  f"{len(drift)} drift warning(s)")
        else:
            print(f"\n✓ Clean — no violations "
                  f"({len(drift)} drift warning(s))")
    if violations and not getattr(args, "warn_only", False):
        return 1
    return 0


def _cmd_audit(args) -> int:
    """tortoise audit — graph wiring quality audit (exit 0 clean, 1 issues).

    Wraps the shared SDK audit() method (same checks the MCP tortoise_audit
    tool runs). Exit-code semantics follow check-consistency: any issue fails.

    #1258 review-fix (conf 75): DB resolution + SDK construction + audit()
    failures surface as CLEAN CLI errors (one line, exit 1), never raw
    tracebacks — invalid FALKORDB_PORT (ValueError), relative --db path
    (ValueError), EmbeddedStoreBusyError, and unreachable URI hosts all
    print a single actionable line.
    """
    import json as _json  # noqa: I001
    import os as _os

    from tortoise.audit import AuditResult, print_audit
    from tortoise.sdk import TortoiseSDK

    # DB routing follows the canonical precedence (explicit --db > TORTOISE_DB_URI
    # > FALKORDB_* legacy trio > TORTOISE_DB_PATH > embedded default, #715) —
    # URI targets go through from_uri, everything else is an embedded path.
    # Construct the SDK against the RESOLVED target so the busy-probe guards
    # the store actually read (a no-arg TortoiseSDK() probes the DEFAULT
    # store and would fail while the default path is held elsewhere).
    from tortoise.config import is_db_uri as _is_uri
    try:
        target = _resolve_db_target(args.db)
    except ValueError as e:
        # conf 75: bad --db / env (relative path, invalid FALKORDB_PORT) is a
        # clean CLI error, not a traceback. Mask URI userinfo (#720 conf 95).
        print(f"  ❌ Invalid DB target: {_mask_uri_userinfo(str(e))}",
              file=sys.stderr)
        return 1
    sdk: TortoiseSDK | None = None
    try:
        if _is_uri(target):
            # conf 60: route the URI through the constructor's env resolution
            # so the embedded busy-probe NEVER fires on the DEFAULT store for
            # a URI target — a bare TortoiseSDK() with TORTOISE_DB_URI unset
            # resolves to the canonical default embedded path and probes THAT
            # (spurious EmbeddedStoreBusyError while the default store is held
            # elsewhere). _projection_for(target) still pins the exact target.
            _prev_uri = _os.environ.get("TORTOISE_DB_URI")
            _os.environ["TORTOISE_DB_URI"] = target
            try:
                sdk = TortoiseSDK()
            finally:
                if _prev_uri is None:
                    _os.environ.pop("TORTOISE_DB_URI", None)
                else:
                    _os.environ["TORTOISE_DB_URI"] = _prev_uri
        else:
            sdk = TortoiseSDK(db_path=target)
        sdk._proj = _projection_for(target)
        report = sdk.audit(point_kinds=args.kinds)
    except Exception as e:
        # conf 75: construction/audit failures (EmbeddedStoreBusyError,
        # unreachable URI host, init errors) — one clean line, exit 1.
        print(f"  ❌ audit failed: {e}", file=sys.stderr)
        return 1
    finally:
        if sdk is not None:
            sdk.close()
    if args.json:
        print(_json.dumps(report, indent=2, default=str))
    else:
        print_audit(AuditResult.from_dict(report))
    return report["exit_code"]


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
    """Print harness-specific setup instructions (self-hosted stdio, #529).

    Uses the canonical _harness_stdio_config() shapes — cursor includes
    `type: "stdio"` (Cursor docs require it), codex uses `codex mcp add`.
    """
    import json as _json

    if harness == "pi" or harness == "multiple":
        print()
        print("Pi:")
        print("  ✅ tortoise-context extension auto-injects context when you mention issues.")
        print("  Run /reload in Pi to activate.")
        print("  Or call tortoise_help() anytime.")
        print("  Add tortoise MCP to your .mcp.json:")
        print("    " + _json.dumps(_harness_stdio_config("pi"), indent=4).replace("\n", "\n    "))
    if harness == "claude" or harness == "multiple":
        print()
        print("Claude Code:")
        print("  Add tortoise MCP to your .mcp.json:")
        print("    " + _json.dumps(_harness_stdio_config("claude"), indent=4).replace("\n", "\n    "))
        print("  Claude Code will auto-discover MCP tools on restart.")
        print("  Optional: add .claude/hooks/session-start.sh for auto-injection.")
        print("    cp tortoise/claude-hooks/session-start.sh .claude/hooks/session-start.sh")
    if harness == "codex" or harness == "multiple":
        print()
        print("Codex:")
        print("  Add tortoise MCP (Codex manages its own config):")
        print("    " + _harness_stdio_config("codex")["command"])
        print("  AGENTS.md is auto-loaded by Codex — Tortoise instructions are already there.")
        print("  autoRecall will pick up Tortoise Points automatically.")
    if harness == "cursor" or harness == "multiple":
        print()
        print("Cursor:")
        print("  Add tortoise MCP to your .mcp.json (type: stdio is required):")
        print("    " + _json.dumps(_harness_stdio_config("cursor"), indent=4).replace("\n", "\n    "))
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
    import os  # noqa: F401
    import subprocess
    import sys
    import tempfile
    from pathlib import Path

    from tortoise.api import EventAPI
    from tortoise.extractor import MockModel, extract_from_document
    from tortoise.log import EventLog
    from tortoise.projection import FalkorProjection

    url = args.url
    branch = args.branch or "main"

    # Background mode: detach and return immediately
    if args.background:
        # #715 P2 conf 90: the re-spawn must carry the resolved DB target —
        # it previously omitted --db entirely, so the child could index a
        # different store than the parent (split graph).
        try:
            bg_target = args.db or _resolve_db_target(None)
        except ValueError as e:
            # #720 P2 conf 95: mask userinfo (no-op for plain paths).
            print(f"  ❌ Invalid DB target: {_mask_uri_userinfo(str(e))}", file=sys.stderr)
            return 1
        cmd, child_env = _index_github_child_cmd(bg_target, url, branch)
        pid_file = Path(tempfile.gettempdir()) / f"tortoise-index-{Path(url).stem}.pid"
        log_file = pid_file.with_suffix('.log')
        with open(log_file, 'w') as lf:
            proc = subprocess.Popen(
                cmd, stdout=lf, stderr=subprocess.STDOUT,
                start_new_session=True, env=child_env,
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
        import shutil as _shutil
        if _shutil.which("git") is None:
            print("git is required to index GitHub repos but was not found on PATH", file=sys.stderr)
            return 1
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

    # ── DB target: same single-source resolution as init (#705/#715, conf 60)
    # args.db is authoritative when given (onboard passes the target init
    # resolved); standalone falls back to the shared env precedence. No
    # connectivity probing — a reachable local docker must never override the
    # configured target or init and index silently split onto different stores.
    # Guarded like the background branch above (#715): a bad FALKORDB_PORT
    # (or other env) must surface as a clean CLI error, not a ValueError
    # traceback.
    try:
        target = args.db or _resolve_db_target(None)
    except ValueError as e:
        # #720 P2 conf 95: mask userinfo (no-op for plain paths).
        print(f"  ❌ Invalid DB target: {_mask_uri_userinfo(str(e))}", file=sys.stderr)
        return 1
    from tortoise.config import is_db_uri
    try:
        if is_db_uri(target):
            proj = FalkorProjection.from_uri(target)
        else:
            proj = FalkorProjection(path=target)
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
    from tortoise.idempotency import document_key as doc_key_fn  # noqa: I001
    import json as _json
    hash_file = Path.home() / ".tortoise" / "indexed_hashes.json"
    hash_file.parent.mkdir(parents=True, exist_ok=True)
    indexed_hashes: set[str] = set()
    if hash_file.exists():
        try:  # noqa: SIM105
            indexed_hashes = set(_json.loads(hash_file.read_text()))
        except Exception:
            pass

    # Use deterministic mock models by default (offline, self-hosted friendly).
    # Users can swap to LLM models via tortoise ingest for richer extraction.
    point_model = MockModel("cheap")
    relation_model = MockModel("reason")
    indexed, skipped, errors = 0, 0, 0

    for i, fp in enumerate(md_files, 1):
        rel = fp.relative_to(repo_path)
        raw_text = fp.read_text(encoding="utf-8")
        # ponytail: strip frontmatter before hashing — extractor may add
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
            stats = extract_from_document(
                raw_text, str(rel), api,
                point_model=point_model,
                relation_model=relation_model,
                authored_by="github-indexer",
            )
            if stats.get("points", 0) > 0:
                indexed += 1
                indexed_hashes.add(content_hash)
                # Persist incrementally — avoid duplicate Points on crash
                try:  # noqa: SIM105
                    hash_file.write_text(_json.dumps(sorted(indexed_hashes)))
                except Exception:
                    pass
                print(f"✓ ({stats['points']} pts, {stats['operators']} ops, {stats['sections']} sections)")
            else:
                skipped += 1
                print("⊙ (skipped — no claims found)")
        except Exception as e:
            errors += 1
            print(f"✗ ({e})")

    if proj:
        proj.close()

    # Persist indexed hashes for cross-run idempotency
    try:  # noqa: SIM105
        hash_file.write_text(_json.dumps(sorted(indexed_hashes)))
    except Exception:
        pass

    print()
    print(f"Done: {indexed} indexed, {skipped} skipped, {errors} errors")
    if indexed > 0:
        print(f"  Log: {log_path}")
        print(f"  Graph: query with tortoise_suggest_entry_points()")  # noqa: F541

    # Cleanup (only if we cloned)
    if tmpdir:
        __import__("shutil").rmtree(tmpdir, ignore_errors=True)
    return 0 if errors == 0 else 1


def _cmd_doctor(args):
    """Health check — verify Tortoise setup is healthy."""
    import importlib
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

    # Resolve the DB target ONCE — shared by Step 2 (Docker probe) and
    # Step 3 (graph health) (#720 conf 78). Step 2 previously probed a
    # hardcoded FALKORDB_*/localhost:16379 while Step 3 used the resolved
    # target, so `doctor --db docker://<healthy-remote>` reported
    # "FalkorDB ❌ localhost:16379" + exit 1 while "Graph: health ✅"
    # confirmed the configured target — a self-contradictory verdict.
    # Precedence (#705/#715, conf 88): explicit --db (URI → from_uri, plain
    # path → embedded) > --path > TORTOISE_DB_URI > FALKORDB_* trio >
    # TORTOISE_DB_PATH > canonical EMBEDDED default (conf 70 — no local
    # docker://localhost default). Routes through the shared
    # _resolve_db_target(explicit) + config.is_db_uri; attributes read via
    # getattr so bare Namespace(cmd="doctor") callers never raise (#703).
    from tortoise.config import is_db_uri
    db = getattr(args, "db", None)
    path = getattr(args, "path", None)
    try:
        target = db if (db and is_db_uri(db)) else _resolve_db_target(db or path)
    except ValueError as e:
        # #720 P2 conf 95: unsupported-scheme URIs (bolt://, mongodb://, …)
        # fall through is_db_uri → resolve_db_path → RELATIVE_PATH_ERROR with
        # the RAW URI embedded — mask userinfo so a credential in --db /
        # --path / TORTOISE_DB_URI never reaches the terminal (no-op for
        # plain paths).
        results.append(("Graph: health", "❌", _mask_uri_userinfo(str(e))[:60]))
        target = None

    # 2. Docker / FalkorDB — probe the RESOLVED target, never a hardcoded
    # localhost (#720 conf 78): URI targets get a live connect probe at
    # their OWN host/port, user/pass, ssl, AND graph name — all parsed from
    # the URI with the same derivation from_uri uses (Step 3), so the probe
    # and the health check can never disagree; embedded targets have no
    # Docker server to probe.
    if target is not None and is_db_uri(target):
        from urllib.parse import urlparse
        # urlparse raises ValueError on malformed URIs (e.g. dangling IPv6
        # bracket `docker://:pw@[abc`) — keep ALL parsing inside the guard
        # so it surfaces as a clean ❌ + rc 1, never a traceback (#720 P2
        # conf 95). Sentinel: parsed stays None when urlparse itself fails.
        parsed = None
        probe_host = "localhost"
        probe_user = None
        probe_pass = None
        graph_name = "tortoise"
        probe_port = None
        try:
            parsed = urlparse(target)
            probe_host = parsed.hostname or "localhost"
            probe_user = parsed.username or None
            probe_pass = parsed.password or None
            # Same graph derivation from_uri uses (parsed.path.lstrip('/') or
            # "tortoise") — probe the URI path's graph, never a hardcoded
            # "tortoise" (#720 P2 conf 62): a non-default graph name must be
            # probed, and a remote server must not get a stray "tortoise" graph
            # created by the probe.
            graph_name = parsed.path.lstrip("/") or "tortoise"
            # parsed.port raises ValueError on a non-numeric port — keep it
            # inside the guard so `doctor --db docker://host:notaport/...`
            # surfaces as a clean ❌ + rc 1, never a traceback (#720 P2 conf 75).
            probe_port = parsed.port or 16379
            from falkordb import FalkorDB
            dbc = FalkorDB(host=probe_host, port=probe_port,
                           username=probe_user, password=probe_pass,
                           ssl=(parsed.scheme == "rediss"),
                           socket_connect_timeout=5, socket_timeout=10)
            dbc.select_graph(graph_name).query("RETURN 1")
            results.append(("Graph: FalkorDB", "✅", f"connected at {probe_host}:{probe_port} (graph {graph_name})"))
        except ImportError:
            results.append(("Graph: FalkorDB", "⚠️", "falkordb package not installed"))
        except Exception as e:
            if parsed is None:
                # Malformed URI — urlparse never completed; mask userinfo so
                # a credential in --db never reaches the terminal.
                results.append(("Graph: FalkorDB", "❌", f"bad URI '{_mask_uri_userinfo(target)}': {str(e)[:60]}"))
            elif probe_port is None:
                # Non-numeric port — probe never reached the client.
                results.append(("Graph: FalkorDB", "❌", f"bad port in URI '{_mask_uri_userinfo(target)}': {str(e)[:60]}"))
            else:
                results.append(("Graph: FalkorDB", "❌", f"{probe_host}:{probe_port} (graph {graph_name}) — {str(e)[:60]}"))
    elif target is not None:
        # Embedded mode (resolved to a file path) — no Docker server to
        # probe. ⚠️ (not ❌) keeps embedded setups healthy; the graph-health
        # check below verifies the actual DB.
        results.append(("Graph: FalkorDB", "⚠️", f"embedded mode ({target}) — no Docker probe"))

    # 3. Graph health — verify the SAME resolved target above (probe + check
    # can never diverge, #720 conf 78). URI → from_uri projection, plain
    # path → embedded projection via _projection_for.
    if target is not None:
        try:
            from tortoise.sdk import TortoiseSDK
            sdk = TortoiseSDK()
            sdk._proj = _projection_for(target)
            try:
                status = sdk.status()
                points = status.get("counts", {}).get("Point", 0)
                total = status.get("total_entities", 0)
                if points > 0:
                    results.append(("Graph: health", "✅", f"{points} Points, {total} entities"))
                else:
                    results.append(("Graph: health", "⚠️", "0 Points — graph is empty (expected for new setups)"))
                # #280 check 4: session-indexing health runs HERE, while the
                # projection is still open — #720's finally-close would kill
                # the embedded server before the session check could connect.
                try:
                    _chk4 = sdk.session_index_health()
                    _fc = _chk4["file_count"]
                    if _fc == 0:
                        results.append(("Session indexing", "⚠️",
                                        "corpus empty — nothing indexed (expected for new setups)"))
                    else:
                        _delta = len(_chk4["unindexed"]) + len(_chk4["stale"])
                        _dup = (f" — {len(_chk4.get('duplicates', []))} duplicate "
                                f"sessionId(s) surfaced (merge/remove copies)"
                                if _chk4.get("duplicates") else "")
                        if _delta == 0:
                            results.append(("Session indexing", "✅",
                                            f"{_fc} corpus files all indexed "
                                            f"({_chk4['indexed_events']} AgentSession Events total){_dup}"))
                        else:
                            results.append(("Session indexing", "❌",
                                            f"{_fc} files vs {_chk4['indexed_events']} Events — {_delta} unindexed/stale "
                                            f"(run `tortoise index sessions`){_dup}"))
                except Exception as _e:
                    results.append(("Session indexing", "⚠️",
                                    f"check unavailable: {str(_e)[:60]}"))

            finally:
                # conf 52: close the projection in BOTH branches — the URI
                # branch's from_uri projection must not leak.
                if sdk._proj:
                    sdk._proj.close()
        except Exception as e:
            results.append(("Graph: health", "❌", str(e)[:60]))

    # 5. MCP server
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

    # 5.5 Session extraction — LLM provider (#1197)
    # POST /v1/sessions (capture) fails closed with 503 when no LLM provider
    # key is configured (#822 — regex extraction removed as a product path;
    # this is the beta testers' most-critical feature). Doctor surfaces the
    # configured provider/model BEFORE testers hit a silent 503. Hosted mode
    # (FLY_APP_NAME — precedent: hosted_api.py, sdk.py) treats
    # provider-missing as a HARD failure: the flagship feature cannot work at
    # all. Local/selfhosted is a warning — capture still fails closed, but
    # there is no hosted SLA at stake. Mirrors hosted_api._llm_provider_available
    # + sdk._build_session_llm_extractor exactly (the seam they must agree on).
    import os as _os
    hosted = bool(_os.environ.get("FLY_APP_NAME"))
    mock_seam = _os.environ.get("TORTOISE_SESSION_LLM_MOCK", "").strip().lower() == "1"
    try:
        from tortoise.sdk import (  # noqa: I001
            _SESSION_LLM_DEFAULT_MODELS,
            _build_session_llm_extractor,
            _session_llm_model_shape_warning,
            _session_llm_provider,
        )
        from tortoise.hosted_api import _LLM_PROVIDER_KEYS, _llm_provider_available

        if _llm_provider_available():
            provider = _session_llm_provider()
            if mock_seam:
                if hosted:
                    results.append((
                        "Session extraction", "❌",
                        "TORTOISE_SESSION_LLM_MOCK=1 is SET in hosted mode — "
                        "captures would write offline MockModel points; "
                        "REMOVE the test seam and set a real provider key.",
                    ))
                else:
                    results.append((
                        "Session extraction", "⚠️",
                        "LLM provider via TORTOISE_SESSION_LLM_MOCK=1 test "
                        "seam (offline MockModel — NOT production-grade).",
                    ))
            else:
                # Validate the REAL seam: sdk._build_session_llm_extractor
                # raises ValueError on provider/model mismatch (sdk.py) — the
                # doctor must not print ✅ for a config that crashes capture
                # with a 500 (only a matching provider/model builds).
                try:
                    if _build_session_llm_extractor() is None:
                        raise RuntimeError("extractor is None despite provider available")
                except Exception as e:
                    results.append((
                        "Session extraction", "❌" if hosted else "⚠️",
                        f"provider/model misconfig: {str(e)[:120]}",
                    ))
                else:
                    spec = _os.environ.get("TORTOISE_SESSION_LLM_MODEL", "").strip()
                    model = spec or _SESSION_LLM_DEFAULT_MODELS.get(provider or "", "")
                    results.append((
                        "Session extraction", "✅",
                        f"LLM provider configured ({provider}, model {model or '?'})",
                    ))
                    # OpenRouter route-shape warning (PR #1220 review P2 c65):
                    # a bare <model> spec (openrouter:deepseek-chat) builds an
                    # extractor fine but the FIRST capture 404s — OpenRouter
                    # routes are family-prefixed (<family>/<model>). Warn, never
                    # fail: the config is valid, only the route id shape is
                    # suspect (and capture failure is a 404, not a 503).
                    warning = _session_llm_model_shape_warning(spec, provider)
                    if warning:
                        results.append(("OpenRouter model", "⚠️", warning))
        else:
            detail = (
                "no LLM provider key — POST /v1/sessions fails closed (503). "
                f"Set one of: {' / '.join(_LLM_PROVIDER_KEYS)} "
                "(docs/infra-runbook.md §4.6)."
            )
            results.append(("Session extraction", "❌" if hosted else "⚠️", detail))
    except Exception as e:
        results.append(("Session extraction", "⚠️", f"check unavailable: {str(e)[:60]}"))

    # 6. Harness detection
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
    import sys as _sys  # noqa: I001
    from tortoise.sdk import TortoiseSDK

    # #715 P2 conf 75: no hardcoded docker://localhost default — resolve the
    # same target init/index use (env URI > FALKORDB_* > embedded path).
    try:
        target = _resolve_db_target(None)
    except ValueError as e:
        # #720 P2 conf 95: mask userinfo (no-op for plain paths).
        print(f"  ❌ Invalid DB target: {_mask_uri_userinfo(str(e))}", file=_sys.stderr)
        return 1
    sdk = TortoiseSDK()
    sdk._proj = _projection_for(target)

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
    import sys as _sys  # noqa: I001
    from tortoise.sdk import TortoiseSDK

    # #715 P2 conf 75: no hardcoded docker://localhost default — resolve the
    # same target init/index use (env URI > FALKORDB_* > embedded path).
    try:
        target = _resolve_db_target(None)
    except ValueError as e:
        # #720 P2 conf 95: mask userinfo (no-op for plain paths).
        print(f"  ❌ Invalid DB target: {_mask_uri_userinfo(str(e))}", file=_sys.stderr)
        return 1
    sdk = TortoiseSDK()
    sdk._proj = _projection_for(target)

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


def _cmd_index_sessions(args) -> int:
    """Reconciliation sweep — index unindexed/stale session files (#280 item 3).

    tortoise index sessions [--dir DIR] [--db URI] [--metadata]

    Scan-then-replay: reports the corpus-vs-graph delta, re-indexes
    missing/hash-stale files via the SDK (dedup + per-session flock), and
    prints the report. The sweep is the "periodic scan + retry" surface —
    trigger it manually, from cron, or from session-end.sh (align decision:
    no cron infra in-tree).
    """
    import sys as _sys

    from tortoise.sdk import TortoiseSDK

    # #715 P2 conf 75: resolve the same target init/index use (env URI >
    # FALKORDB_* > embedded path).
    try:
        target = _resolve_db_target(args.db)
    except ValueError as e:
        print(f"  ❌ Invalid DB target: {e}", file=_sys.stderr)
        return 1
    # Round-9: an unreachable graph (dead host, down DB) must produce a clean
    # CLI error, not a raw ConnectionError traceback — mirroring doctor
    # check-3's pattern. The hook fires this on every session end, so a down
    # DB would otherwise spawn one noisy failing process per close.
    # Round-11: sdk pre-initialized to None, constructor INSIDE the try — a
    # constructor raise (FLY_APP_NAME production guard with an empty URI)
    # must produce a clean CLI error, not a raw traceback; the finally never
    # sees an unbound sdk.
    sdk: TortoiseSDK | None = None
    try:
        sdk = TortoiseSDK()
        sdk._proj = _projection_for(target)
        report = sdk.reconcile_sessions(directory=args.dir,
                                        extract_metadata=args.metadata)
    except Exception as e:
        print(f"  ❌ graph unreachable: {e}", file=_sys.stderr)
        return 1
    finally:
        if sdk is not None and sdk._proj:
            sdk._proj.close()

    print(f"Session corpus: {report['directory']}")
    print(f"  .md files:          {report['file_count']}")
    print(f"  AgentSession Events: {report['indexed_events']}")
    print(f"  up-to-date:         {report['matched']}")
    print(f"  unindexed:          {len(report['unindexed'])}")
    print(f"  stale (hash drift): {len(report['stale'])}")
    for f in report["unindexed"]:
        print(f"    - {f}")
    for f in report["stale"]:
        print(f"    ~ {f}")
    if report.get("duplicates"):
        print(f"  duplicate sessionIds: {len(report['duplicates'])}")
        for d in report["duplicates"]:
            print(f"    ! {d['session_id']}: {', '.join(d['files'])}")
    if report.get("reindex"):
        r = report["reindex"]
        print(f"Re-index: {r.get('ingested', 0)} ingested, "
              f"{r.get('updated', 0)} updated, "
              f"{r.get('skipped', 0)} skipped, "
              f"{r.get('failed', 0)} failed")
        # Review follow-up: surface per-file errors (lock unavailable / held /
        # duplicate sessionId) — a sweep that skipped everything must not look
        # green. Retryable errors are retried on the next sweep.
        if r.get("errors"):
            _n_retry = sum(1 for e in r["errors"] if e.get("retryable"))
            print(f"  {len(r['errors'])} error(s) "
                  f"({_n_retry} retryable — retried next sweep):")
            for e in r["errors"][:5]:
                print(f"    ! {e['file']}: {e['error']}")
            if len(r["errors"]) > 5:
                print(f"    ... and {len(r['errors']) - 5} more")
    else:
        print("Re-index: nothing to do (corpus fully indexed)")
    return 1 if (report.get("reindex") or {}).get("failed") else 0


def _cmd_index_directory(args) -> int:
    """The unified index path CLI (epic #900 T8, §6.5 canonical).

    tortoise index directory <corpus-dir> [--db URI] [--corpus-name NAME]
                                 [--metadata]

    Stdout contract (cycle-21/23 pin): the §3.1 report dict as ONE JSON line
    (deterministic key order: directory, corpus_name, file_count, indexed,
    updated, skipped, failed, aborted, ignored, errors, by_kind,
    aborted_reason) + a human-readable multi-line rendering to stderr.
    Exit codes: 0 = completed run (with or without failed>0); 1 = pre-walk
    argument error OR graph unreachable. A completed run with failed>0 still
    exits 0 — per-file failures are REPORTED in the summary, never encoded
    in the exit code (the backgrounded hook path must never see exit-1
    ambiguity between argument errors and data failures).

    Corpus-dir resolution: POSITIONAL argument → TORTOISE_INGEST_BASE_DIR
    fallback → explicit pre-walk error when both absent (exit 1, naming
    BOTH corpus_root and TORTOISE_INGEST_BASE_DIR — the actionability
    assertion, E2E-16(iv)). The migrated hook passes the corpus dir
    POSITIONALLY (resolved from session_corpus_dir()/TORTOISE_SESSION_CORPUS).

    Env: TORTOISE_DB_URI, TORTOISE_INGEST_BASE_DIR,
    TORTOISE_EMBEDDING_REPAIR_BACKOFF_HOURS, TORTOISE_MAX_FILE_MB,
    TORTOISE_INDEX_NO_NETWORK (forces extract_metadata=False regardless of
    --metadata — cycle-12 precedence pin), TORTOISE_INDEX_CHILD_STDERR
    (debug-redirect: captures the child's FULL output — stdout+stderr — to
    the target file, TRUNCATE-ON-OPEN; fail-safe rejects relative targets
    and targets whose PARENT dir is missing/unwritable; a NONEXISTENT
    TARGET FILE is valid — the nohup'd child creates it).
    """
    import os as _os  # noqa: I001
    import sys as _sys
    import json as _json
    from pathlib import Path as _Path
    from tortoise.sdk import TortoiseSDK

    # ── TORTOISE_INDEX_CHILD_STDERR debug-redirect (cycle-15/16 pins) ──
    # Captures the backgrounded child's FULL output (stdout+stderr → file,
    # the semantics extend beyond the name's literal reading). TRUNCATE-ON-
    # OPEN (a `>`-style, never append — the hook fires at every session
    # close; unbounded growth is disk exhaustion). Fail-safe: reject ONLY
    # relative targets and targets whose PARENT directory is missing or
    # unwritable — a NONEXISTENT TARGET FILE is VALID (the nohup'd child
    # creates it); existing-DIRECTORY and non-regular-file (FIFO/device)
    # targets are also rejected before the redirect open (an operator
    # setting the var to a directory would raise EISDIR inside the child
    # with fd 1/2 still /dev/null — an invisible crash killing the #280
    # sweep).
    _redirect = _os.environ.get("TORTOISE_INDEX_CHILD_STDERR", "").strip()
    _stdout = _sys.stdout
    _stderr = _sys.stderr
    if _redirect:
        _rt = _Path(_redirect)
        if not _rt.is_absolute():
            print(f"TORTOISE_INDEX_CHILD_STDERR target must be absolute: "
                  f"{_redirect!r} (fail-safe)", file=_sys.stderr)
            return 1
        _parent = _rt.parent
        if not _parent.is_dir() or not _os.access(str(_parent), _os.W_OK):
            print(f"TORTOISE_INDEX_CHILD_STDERR parent dir missing/unwritable: "
                  f"{_parent} (fail-safe)", file=_sys.stderr)
            return 1
        if _rt.exists() and _rt.is_dir():
            print(f"TORTOISE_INDEX_CHILD_STDERR target is a directory: "
                  f"{_redirect!r} (fail-safe)", file=_sys.stderr)
            return 1
        if _rt.exists() and not _rt.is_file():
            print(f"TORTOISE_INDEX_CHILD_STDERR target is not a regular file: "
                  f"{_redirect!r} (fail-safe)", file=_sys.stderr)
            return 1
        try:
            _f = open(_redirect, "w", encoding="utf-8")  # TRUNCATE-ON-OPEN  # noqa: SIM115
        except OSError as _e:
            print(f"TORTOISE_INDEX_CHILD_STDERR cannot open target "
                  f"{_redirect!r}: {_e} (fail-safe)", file=_sys.stderr)
            return 1
        _stdout = _f
        _stderr = _f

    # ── corpus-dir resolution (positional → env fallback → error) ──
    # E2E-15(g)/§8.6 cycle-13 pin: a NONEXISTENT env-resolved fallback dir
    # (manual TORTOISE_INGEST_BASE_DIR typo) = PRE-WALK ERROR (exit 1, clear
    # message) — the zero-count no-op applies ONLY to an explicitly
    # POSITIONALLY-passed nonexistent dir (the hook passes positionally).
    positional = args.corpus_dir
    corpus_dir = positional
    env_resolved = False
    if corpus_dir is None or not str(corpus_dir).strip():
        corpus_dir = _os.environ.get("TORTOISE_INGEST_BASE_DIR", "").strip()
        env_resolved = bool(corpus_dir)
    if not corpus_dir:
        _msg = ("tortoise index directory: no corpus directory given — pass "
                "<corpus-dir> positionally or set TORTOISE_INGEST_BASE_DIR.")
        print(_msg, file=_sys.stderr)
        if _redirect:
            _stdout.close()
        return 1
    if not _os.path.isabs(corpus_dir):
        print(f"tortoise index directory: corpus directory must be absolute: "
              f"{corpus_dir!r}", file=_sys.stderr)
        if _redirect:
            _stdout.close()
        return 1
    if env_resolved and not _os.path.isdir(corpus_dir):
        print(f"tortoise index directory: TORTOISE_INGEST_BASE_DIR points at a "
              f"nonexistent directory: {corpus_dir!r} (pre-walk error — pass "
              f"the corpus dir positionally for a zero-count no-op)",
              file=_sys.stderr)
        if _redirect:
            _stdout.close()
        return 1

    # ── graph target + run ──
    try:
        target = _resolve_db_target(args.db)
    except ValueError as e:
        print(f"  ❌ Invalid DB target: {e}", file=_stderr)
        if _redirect:
            _stdout.close()
        return 1
    sdk: TortoiseSDK | None = None
    try:
        # REVIEW-FIX (re-review gate P1): construct the SDK against the
        # RESOLVED target so the §5.3 busy-probe guards the store actually
        # written — a no-arg TortoiseSDK() probes the DEFAULT store (the
        # `--db` contract would silently bypass the guard on the real
        # target; E2E-16 legs fail deterministically while the default
        # store is held). URI targets route through from_uri (no embedded
        # probe); embedded paths are probed at the resolved path.
        from tortoise.config import is_db_uri as _is_uri
        if _is_uri(target):  # noqa: SIM108
            sdk = TortoiseSDK()
        else:
            sdk = TortoiseSDK(db_path=target)
        sdk._proj = _projection_for(target)
        report = sdk.index_directory(
            corpus_dir,
            extract_metadata=bool(args.metadata),
            corpus_name=args.corpus_name,
        )
    except ValueError as _e:
        # §6.5/T8 pin (E2E-15(d2) contract): the RESOLUTION ValueError
        # (unsafe directory, corpus-root symlink resolving outside
        # TORTOISE_INGEST_BASE_DIR, progress_file bounds) must surface its
        # TRACEBACK — the capture file (TORTOISE_INDEX_CHILD_STDERR) must
        # contain the traceback naming the ValueError class + the symlink
        # path, so a backgrounded sweep failure is INSPECTABLE. A
        # clean-error implementation FAILS (d2) on purpose. The child's
        # real stderr is /dev/null under the hook's nohup discipline, so
        # the CLI writes the traceback to the redirect target itself
        # (falling back to the real stderr for direct operators); exit 1 =
        # pre-walk error.
        import traceback as _tb
        print(f"tortoise index directory: pre-walk error ({type(_e).__name__}):"
              f" {_e}", file=_stderr)
        print(_tb.format_exc(), file=_stderr)
        if _redirect:
            _stdout.close()
        return 1
    except Exception as e:
        print(f"  ❌ graph unreachable: {e}", file=_stderr)
        if _redirect:
            _stdout.close()
        return 1
    finally:
        if sdk is not None and sdk._proj:
            sdk._proj.close()

    # ── stdout contract: ONE JSON line (deterministic key order) ──
    _order = ("directory", "corpus_name", "file_count", "indexed", "updated",
              "skipped", "failed", "aborted", "ignored", "errors",
              "by_kind", "aborted_reason")
    _line = {k: report.get(k) for k in _order}
    print(_json.dumps(_line), file=_stdout)
    # human-readable rendering to stderr
    print(f"Indexed corpus: {report.get('directory')}", file=_stderr)
    print(f"  file_count: {report.get('file_count')}  indexed: "
          f"{report.get('indexed')}  updated: {report.get('updated')}  "
          f"skipped: {report.get('skipped')}  failed: {report.get('failed')}  "
          f"aborted: {report.get('aborted')}  ignored: {report.get('ignored')}",
          file=_stderr)
    if report.get("aborted_reason"):
        print(f"  aborted_reason: {report['aborted_reason']}", file=_stderr)
    for e in (report.get("errors") or [])[:5]:
        print(f"    ! {e.get('file', e.get('dir'))}: {e.get('error')}",
              file=_stderr)
    if _redirect:
        _stdout.close()
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
    import json as _json  # noqa: I001
    import sys as _sys
    from pathlib import Path
    from tortoise.sdk import TortoiseSDK

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

    # #715 P2 conf 75: --db override or the shared env resolution — never a
    # hardcoded docker://localhost default. URI targets go through from_uri,
    # paths through the embedded constructor (same parity as init/index).
    try:
        target = getattr(args, "db", None) or _resolve_db_target(None)
        sdk = TortoiseSDK()
        # Inside the guard: _projection_for rejects relative --db paths with
        # the shared RELATIVE_PATH_ERROR (ValueError) — surface it as a
        # clean CLI error, not a traceback (#715).
        sdk._proj = _projection_for(target)
    except ValueError as e:
        # #720 P2 conf 95: mask userinfo (no-op for plain paths).
        print(f"  ❌ Invalid DB target: {_mask_uri_userinfo(str(e))}", file=_sys.stderr)
        return 1

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
            from tortoise.exceptions import CalibrationError
            if isinstance(e, CalibrationError):
                # #344: fail-closed — never let uncalibrated EP run silently.
                print(f"\n⚠ Calibration required — EP not run: {e}")
                print("  Calibrate the graph first: run calibrate_summary() for "
                      "per-point guidance (set_point_baseline(), credibility on "
                      "recreate, or set_source_tier() for sourced points), or "
                      "pass require_calibration=False to explicitly opt out.")
                # PR #1212: fail-closed — the calibration failure is NOT a
                # successful run. Skip the "Done." summary and exit non-zero
                # (main() returns this value → SystemExit(main())) so scripts
                # and CI can detect the uncalibrated state instead of treating
                # the empty comparison as success.
                return 1
            else:
                print(f"\n⚠ compute_confidence: {e}")

    finally:
        if sdk._proj:
            sdk._proj.close()

    print(f"\nDone. Decision comparison filed.")  # noqa: F541
    return 0


def _parse_bind_ip(bind: str):
    """Parse `--bind` as an IP address, normalizing IPv4-mapped IPv6
    (::ffff:x.x.x.x) to the embedded IPv4.

    CPython only reports is_loopback=True for mapped addresses on
    3.11.13+/3.12.7+ (gh-117566 backports); normalizing first gives every
    supported Python the same classification — and lets `_bind_allowed_hosts`
    seed the plain embedded IPv4 (e.g. 192.168.1.50) into the Host guard,
    which is what clients actually send in the Host header. Returns None for
    hostnames/non-IP values (#719).
    """
    import ipaddress

    try:
        ip = ipaddress.ip_address(bind)
    except ValueError:
        return None
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip


def _bind_allowed_hosts(bind: str, extra: str | None) -> list[str]:
    """Host values fastmcp's Host guard must accept for `serve --http --bind ...`.

    fastmcp's HostOriginGuardMiddleware auto-admits loopback hosts
    (DEFAULT_HOSTS) and appends the socket's own bind address from
    scope["server"] — so a plain specific-IP non-loopback bind passes even
    unseeded. Tortoise always passes an explicit allowed_hosts list, which
    flips the guard's has_explicit_allowed_hosts flag: host validation is
    then ALWAYS on (fail-closed), and the allowlist is DEFAULT_HOSTS +
    these entries + scope["server"]. Seeding is therefore load-bearing
    exactly where the Host header a client sends differs from
    scope["server"]: any-interface wildcards (0.0.0.0/:: — scope["server"]
    is unspecified and NOT auto-appended, so clients' hostname/LAN-address
    Host headers would 421), hostname binds (Host carries the DNS name,
    scope carries the resolved IP), IPv4-mapped binds (scope carries the
    ::ffff: form, clients send the plain IPv4), and any extra
    --allowed-hosts names (#719). Returns those seeded values plus the
    machine's own hostname/LAN addresses for wildcard binds.
    """
    import ipaddress
    import socket

    hosts: list[str] = []
    seen: set[str] = set()

    def _add(h: str) -> None:
        h = h.strip().lower().rstrip(".")
        if h and h not in seen:
            seen.add(h)
            hosts.append(h)

    for part in (extra or "").split(","):
        _add(part)

    ip = _parse_bind_ip(bind)

    if ip is not None:
        if ip.is_unspecified:
            # 0.0.0.0 / :: — any interface: clients reach us by the machine's
            # hostname or a LAN address, not by the wildcard. Seed the guard
            # with the machine's own identity so the "reachable on your
            # network" warning is actually TRUE.
            _add(socket.gethostname())
            try:
                for info in socket.getaddrinfo(socket.gethostname(), None):
                    cand = info[4][0]
                    try:
                        if not ipaddress.ip_address(cand).is_loopback:
                            _add(cand)
                    except ValueError:
                        _add(cand)
            except OSError:
                pass
        elif not ip.is_loopback:
            _add(str(ip))
    elif bind.strip().lower().rstrip(".") != "localhost":
        # Hostname bind (e.g. mybox.local) — honor it directly.
        _add(bind)

    return hosts


def _is_loopback_bind(bind: str) -> bool:
    """True when `--bind` exposes only the loopback interface.

    Covers the explicit loopback aliases — 127.0.0.1, localhost, ::1, the
    whole 127.0.0.0/8 range, and the IPv4-mapped ::ffff:127.0.0.1. Anything
    else — a LAN/cloud IP, a real hostname, or the 0.0.0.0/:: any-interface
    wildcards (which ARE reachable beyond the local machine) — returns False
    so the "reachable on your network" warning and the auth=none refusal fire
    only when they are actually true (#719).
    """
    if bind.strip().lower().rstrip(".") == "localhost":
        return True
    ip = _parse_bind_ip(bind)
    if ip is None:
        # Unknown hostname — assume it resolves to something reachable.
        return False
    return ip.is_loopback


def _db_uri_remote(db_uri: str) -> bool:
    """True when the TORTOISE_DB_URI target lives on a remote (non-loopback)
    machine, so the "Remote/cloud DB target" warnings fire whenever an
    operator's local key/process genuinely drives a shared graph.

    redis:// and rediss:// targets are always remote by convention (managed
    cloud / non-local Redis instances). docker:// is local ONLY when its host
    is a loopback address (localhost / 127.0.0.0/8 / ::1, incl. IPv4-mapped
    ::ffff:127.0.0.1) — docker://user:pass@remote-host:6379/... is a valid
    remote FalkorDB target and must warn just like redis:// (#719).

    Scheme membership derives from config (is_db_uri / is_docker_uri over
    SUPPORTED_URI_SCHEMES) — a scheme added there can never silently skip
    the remote-target warning (#715 no-drift invariant).
    """
    if not db_uri:
        return False
    from tortoise.config import is_db_uri, is_docker_uri
    if not is_db_uri(db_uri):
        return False
    if is_docker_uri(db_uri):
        from urllib.parse import urlsplit

        host = urlsplit(db_uri).hostname
        if host is None:
            return False  # unparseable authority — don't invent a warning
        if host.strip().lower().rstrip(".") == "localhost":
            return False
        ip = _parse_bind_ip(host)
        if ip is None:
            # Unknown hostname — assume it resolves to something reachable.
            return True
        return not ip.is_loopback
    # Every other supported scheme (redis://, rediss://, and any future
    # addition to config.SUPPORTED_URI_SCHEMES) is remote by convention
    # (managed cloud / non-local instances) — conservative: warn rather
    # than silently treat an unknown scheme as local.
    return True


def _cmd_serve_http(args) -> int:
    """Serve the Tortoise MCP server over HTTP (streamable) for self-hosted use.

    Auth modes (mirrors create_http_app):
      tenant (default) — registry tt_ keys (bootstrap with `tortoise key create`)
      static           — single key (--api-key or TORTOISE_API_KEY)
      none             — no auth, localhost-bound eval only; a non-loopback
                         --bind is REFUSED unless --allow-insecure-no-auth
    """
    import os  # noqa: I001
    import sys

    from tortoise.sdk import TortoiseSDK
    from tortoise.config import resolve_db_path, is_db_uri
    from tortoise.mcp_server import create_http_app

    # ── Resolve the DB target (single canonical source; print for diagnostics) ──
    db_uri = os.environ.get("TORTOISE_DB_URI", "")
    if is_db_uri(db_uri):
        print(f"serve --http: DB target = {db_uri.split('@')[-1]}")
        if _db_uri_remote(db_uri):
            print("  ⚠️  Remote/cloud DB target — any local process holding a key drives this graph.")
    else:
        # Tilde-expand the raw TORTOISE_DB_PATH: the env shortcut bypasses
        # resolve_db_path() (which expands), and the quickstart documents the
        # literal ~/.tortoise/tortoise.db form — the diagnostic must show the
        # real path, not a shell-unescaped '~' (fixes the exists-check miss).
        db_path = os.path.expanduser(os.environ.get("TORTOISE_DB_PATH") or resolve_db_path())
        print(f"serve --http: DB target = {db_path}")
        # #942: embedded FalkorDBLite is SINGLE-WRITER / EVAL-ONLY. Loud,
        # auth-mode-independent, stderr-only (no stdout asserts break; the
        # literal 'reachable on your network' is asserted absent elsewhere).
        from tortoise._embedded import EMBEDDED_EVAL_BANNER
        print(EMBEDDED_EVAL_BANNER, file=sys.stderr)

    # ── HTTP mode: note the fresh-namespace semantics for existing stdio data ──
    # EVERY HTTP auth mode serves an isolated namespace, never the stdio
    # 'tortoise' graph: tenant → team_{id}; static/none → team_selfhost
    # (SELFHOST_TEAM_ID, see tortoise/mcp_auth.py). A stdio → static-auth LAN
    # switch would otherwise land on a silently empty graph — say it out loud.
    if not is_db_uri(db_uri):
        db_path = os.path.expanduser(os.environ.get("TORTOISE_DB_PATH") or resolve_db_path())
        try:
            if os.path.exists(db_path):
                namespace = "team_{id}" if args.auth == "tenant" else "team_selfhost"
                print(f"  ℹ️  HTTP ({args.auth}) mode uses a fresh {namespace} namespace — existing stdio data")
                print("      remains in the 'tortoise' graph. See docs/infra-runbook.md §4.5.")
        except Exception:
            pass

    origins = ["http://127.0.0.1:*", "http://localhost:*"]
    # P1 #719: fastmcp's Host guard (host_origin_protection=True → strict mode)
    # rejects any Host header outside its allowlist with 421 Misdirected
    # Request. Loopback is allowed by default, but a non-loopback --bind is
    # not — pass the derived hosts so LAN clients actually work.
    allowed_hosts = _bind_allowed_hosts(args.bind, args.allowed_hosts)
    bind_loopback = _is_loopback_bind(args.bind)

    # P2 #719 (security): --auth none on a non-loopback bind would expose full
    # MCP access with no auth to enforce — the old code only warned with text
    # that was unfulfillable in none mode. Refuse (exit non-zero) unless the
    # user explicitly opts into the insecure setup. Loopback stays allowed.
    if args.auth == "none" and not bind_loopback and not args.allow_insecure_no_auth:
        print(
            f"❌ serve --http --auth none refuses non-loopback --bind {args.bind}: "
            "there is no auth to enforce, so anyone who can reach the port would "
            "get full MCP access. Bind a loopback address (127.0.0.1/localhost/::1) "
            "or pass --allow-insecure-no-auth to override (UNSAFE — trusted networks only).",
            file=sys.stderr,
        )
        return 1

    # P2 #719: --api-key is only meaningful for static auth. Silently ignoring
    # it under tenant (the default) would leave the user believing a key is
    # enforced when it isn't — error out instead of silently switching modes.
    if args.api_key and args.auth != "static":
        print(
            f"❌ serve --http --api-key requires --auth static (got --auth {args.auth}). "
            "Pass --auth static to use a single static key, or drop --api-key "
            "to keep tenant auth (registry tt_ keys; bootstrap with `tortoise key create`).",
            file=sys.stderr,
        )
        return 1

    if args.auth == "tenant":
        # Inject the registry SDK built from the SAME canonical DB as the team
        # SDK (avoids the /data default divergence — #702).
        registry_sdk = TortoiseSDK(namespace="registry")
        app = create_http_app(allowed_origins=origins, allowed_hosts=allowed_hosts,
                              _registry_sdk=registry_sdk,
                              auth_mode="tenant")
        print("serve --http: auth = tenant (Bearer tt_ keys; bootstrap with `tortoise key create`)")
    elif args.auth == "static":
        api_key = args.api_key or os.environ.get("TORTOISE_API_KEY")
        if not api_key:
            print("❌ serve --http --auth static requires --api-key or TORTOISE_API_KEY", file=sys.stderr)
            return 1
        app = create_http_app(allowed_origins=origins, allowed_hosts=allowed_hosts,
                              auth_mode="static", api_key=api_key)
        print("serve --http: auth = static (single key)")
    else:  # none
        app = create_http_app(allowed_origins=origins, allowed_hosts=allowed_hosts,
                              auth_mode="none")
        print("serve --http: auth = none (localhost eval — NO auth; do not expose on a network)")

    if not bind_loopback:
        if args.auth == "none":
            # Only reachable here with the --allow-insecure-no-auth override.
            print(f"⚠️  Non-loopback bind {args.bind} — MCP is reachable on your network and auth=none is NOT enforced; anyone who can reach the port has full access.")
        else:
            print(f"⚠️  Non-loopback bind {args.bind} — MCP is reachable on your network; ensure auth is enforced.")
        if allowed_hosts:
            note = ""
            if args.bind in ("0.0.0.0", "::"):
                note = " — add more with --allowed-hosts"
            print(f"    Host guard allows: {', '.join(allowed_hosts)}{note}")

    import uvicorn  # noqa: I001
    from fastapi import FastAPI
    from contextlib import asynccontextmanager

    # FastMCP's StreamableHTTPSessionManager lifespan must be composed into the
    # parent app (Starlette Mount does not run mounted-app lifespans — same
    # pattern as hosted_api._lifespan).
    @asynccontextmanager
    async def _local_lifespan(_app):
        async with app.lifespan(app):
            yield

    # Wrap and mount at /mcp for parity with the hosted endpoint (keeps the
    # mcp_metadata route truthful and the client URL familiar).
    wrapper = FastAPI(lifespan=_local_lifespan)
    wrapper.mount("/mcp", app)
    print(f"Tortoise MCP (streamable-http) → http://{args.bind}:{args.port}/mcp")
    uvicorn.run(wrapper, host=args.bind, port=args.port, log_level="info")
    return 0


def _cmd_key_create(args) -> int:
    """Bootstrap a local registry team + tt_ API key for `serve --http --auth tenant`.

    Mirrors hosted /internal/provision: Team + APIKey nodes in the registry
    graph, TeamMeta in the team_{team_id} graph. Prints ONLY the apikey_create
    key (the one apikey_verify actually matches — team_create's returned key
    is stored on the Team node and never verifies).
    """
    import os
    import sys
    from datetime import datetime, timezone

    from tortoise.config import is_db_uri
    from tortoise.exceptions import ControlPlaneError
    from tortoise.sdk import TortoiseSDK

    db_uri = os.environ.get("TORTOISE_DB_URI", "")
    if is_db_uri(db_uri):
        print(f"key create: registry at {db_uri.split('@')[-1]}")
        if _db_uri_remote(db_uri):
            print("  ⚠️  Remote/cloud registry — key created on that instance.")
    else:
        from tortoise.config import resolve_db_path
        print(f"key create: registry at {os.environ.get('TORTOISE_DB_PATH') or resolve_db_path()}")
        # #942: team keys on embedded = single-writer eval only. The key-mint
        # moment is the enforcement point — interactive/foreground, unlike a
        # daemonized serve's stderr.
        from tortoise._embedded import EMBEDDED_EVAL_BANNER
        print(EMBEDDED_EVAL_BANNER, file=sys.stderr)

    sdk = TortoiseSDK(namespace="registry")
    reg = sdk._get_registry()

    # Find an existing team with this name (idempotent re-runs), else create.
    team_id = None
    rows = reg.query("MATCH (t:Team) RETURN t.id, t.name").result_set or []
    for tid, tname in rows:
        if tname == args.name:
            team_id = tid
            print(f"  ℹ️  Team {args.name!r} already exists — reusing.")
            break
    if team_id is None:
        try:
            result = sdk.team_create(args.name)
        except ControlPlaneError as e:
            # conf 85: an invalid --name (spaces/punctuation, >64 chars,
            # blank) must surface as a clean CLI error, never a raw
            # ControlPlaneError traceback.
            print(f"  ❌ {e}", file=sys.stderr)
            return 1
        team_id = result["id"]
        print(f"  ✅ Team {args.name!r} created (id {team_id})")

    # Seed the team_{team_id} graph the tools actually resolve (hosted parity).
    try:
        now = datetime.now(timezone.utc).isoformat()  # noqa: UP017
        team_graph = sdk._get_proj().db.select_graph(f"team_{team_id}")
        team_graph.query(
            "CREATE (:TeamMeta {name: $name, created: $now})",
            params={"name": args.name, "now": now},
        )
    except Exception as e:
        print(f"  ⚠️  Could not seed team graph: {e}", file=sys.stderr)

    # Create the verifiable API key and print ONLY this one.
    key = sdk.apikey_create(team_id, created_by="local-cli")
    print()
    print(f"✅ Created API key: {key['api_key']}")
    print("   Store it securely — the plaintext is shown once.")
    print()
    print("Use it with:")
    print("   tortoise serve --http --auth tenant")
    print(f"   MCP client url:      http://{args.bind}:{args.port}/mcp")
    if args.bind in ("0.0.0.0", "::"):
        # Wildcard bind mirrors 'serve --http --bind 0.0.0.0' — the printed
        # URL is unusable for clients, so ALWAYS show the LAN correction.
        print("   (URL above is the server's wildcard bind — clients connect to the")
        print(f"    machine's LAN address instead, e.g. http://<lan-ip>:{args.port}/mcp,")
        print(f"    never {args.bind})")
    elif args.bind == "127.0.0.1" and args.port == 8000:
        print("   (hint assumes the default serve bind/port — pass --bind/--port to")
        print("    'key create' to match a custom 'serve --http' setup, e.g. LAN")
        print("    --bind 0.0.0.0, in which case clients connect to the machine's")
        print("    LAN address, not 0.0.0.0)")
    print("   Authorization header: Bearer <key>")
    return 0


def main(argv: list[str] | None = None) -> int:
    import os as _os  # noqa: I001
    from tortoise.config import SUPPORTED_URI_SCHEMES

    uri_schemes_hint = ", ".join(f"{s}://" for s in SUPPORTED_URI_SCHEMES)

    p = argparse.ArgumentParser(prog="tortoise", exit_on_error=False)
    sp = p.add_subparsers(dest="cmd")
    rb = sp.add_parser("rebuild", help="Rebuild FalkorDB from all .jsonl files")
    rb.add_argument("--db", required=True)
    rb.add_argument("--dir", default=".")
    sp.add_parser("demo", help="Run mock extractor on sample transcript")
    sp.add_parser("backfill", help="Backfill missing Point properties (status, createdAt)").add_argument("--db", required=True, help="Docker URI or file path")
    vf = sp.add_parser("verify", help="Write/read/delete test Point — health check")
    vf.add_argument("--db", required=True, help="FalkorDB docker:// URI")
    # tortoise validate --domain <slug> (#405) — advisory domain integrity
    # tortoise export (#1388, epic #1230 Task 1) — versioned encrypted artifact
    ex = sp.add_parser(
        "export",
        help="Export the graph as a versioned, encrypted artifact (tortoise-export-v1)",
    )
    ex.add_argument(
        "--output", "-o", default=None,
        help="Output artifact path (default: graph-<YYYY-MM-DD>.tortoise)",
    )
    ex.add_argument(
        "--no-encrypt", action="store_true",
        help="DANGER: write the artifact unencrypted (plaintext graph) — warns loudly",
    )
    ex.add_argument(
        "--db", default=None,
        help=f"DB target override — URI ({uri_schemes_hint}) or absolute path "
        "(default: TORTOISE_DB_URI / FALKORDB_* / embedded path)",
    )
    ex.add_argument(
        "--json", action=argparse.BooleanOptionalAction, default=True,
        help="Machine-readable JSON stdout (default: on; --no-json for human text)",
    )
    vld = sp.add_parser("validate", help="Advisory domain integrity validation (graph-global rules, read-only)")
    vld.add_argument("--domain", required=True, help="Domain namespace (pack) to validate, e.g. product-strategy")
    vld.add_argument("--db", default=None, help="Docker URI or file path (default: TORTOISE_DB_URI / FALKORDB_* / embedded path)")
    vld.add_argument("--json", action="store_true", help="Machine-readable JSON output")
    vld.add_argument("--warn-only", action="store_true", help="Report violations but exit 0 (pure advisory — never gates)")
    cc = sp.add_parser("check-consistency", help="Verify event log matches graph state")
    cc.add_argument("--db", required=True, help="Docker URI or file path")
    cc.add_argument("--log", required=True, help="Path to events.jsonl")
    au = sp.add_parser("audit", help="Audit graph wiring quality (8 checks: source tiering, superseded gaps, mitigation coverage)")
    au.add_argument("--db", default=None, help=(
        f"DB target override — URI ({uri_schemes_hint}) or absolute path "
        "(default: TORTOISE_DB_URI / FALKORDB_* / embedded path)"))
    au.add_argument("--kind", action="append", dest="kinds", default=None,
                    help="pointKind scope (repeatable; default: all Points)")
    au.add_argument("--json", action="store_true",
                    help="Machine-readable JSON output (exit 0 clean / 1 issues)")
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
    mc = sp.add_parser("mine-conversation",
                        help="Mine meeting transcript → Events + draft Points (manual flow). "
                             "Tutorial: docs/quickstart-selfhosted.md 'Meeting transcripts' "
                             "(sample: tests/sample_transcript.txt)")
    mc.add_argument("transcript",
                    help="Path to transcript file (Speaker: text format, one line each)")
    mc.add_argument("--source-id", default=None,
                    help="Source identifier (default: transcript filename without extension)")
    mc.add_argument("--db", default=None,
                    help="Graph to project into: FalkorDB docker:// URI or embedded path "
                         "(e.g. ~/.tortoise/tortoise.db). Omit for log-only mode "
                         "(writes mine-<source-id>.jsonl, no W-3 gate)")
    sr = sp.add_parser("serve", help="Start Tortoise MCP server (stdio, default) or local HTTP (--http)")
    sr.add_argument("--http", action="store_true",
                    help="Serve MCP over HTTP (streamable) instead of stdio — self-hosted authenticated mode")
    sr.add_argument("--bind", default="127.0.0.1", help="HTTP bind address (default 127.0.0.1)")
    sr.add_argument("--port", type=int, default=8000, help="HTTP port (default 8000)")
    sr.add_argument("--auth", choices=["tenant", "static", "none"], default="tenant",
                    help="HTTP auth mode: tenant = registry tt_ keys (default; bootstrap with 'tortoise key create'), "
                         "static = single key (--api-key or TORTOISE_API_KEY), "
                         "none = localhost eval (NO auth; non-loopback --bind refused unless --allow-insecure-no-auth)")
    sr.add_argument("--api-key", dest="api_key", default=None,
                    help="Static auth key (requires --auth static)")
    sr.add_argument("--allowed-hosts", dest="allowed_hosts", default=None, metavar="HOST[,HOST...]",
                    help="Extra Host-header values the MCP guard accepts (comma-separated). Use when "
                         "clients connect via a hostname/IP that differs from --bind (e.g. --bind "
                         "0.0.0.0 with clients using the machine's LAN name) — without this the "
                         "fastmcp Host guard answers 421 Misdirected Request (#719).")
    sr.add_argument("--allow-insecure-no-auth", action="store_true",
                    help="DANGER: allow --auth none on a non-loopback --bind. Refused by default because "
                         "there is no auth to enforce — anyone who can reach the port gets full MCP "
                         "access. Only for trusted networks.")
    kr = sp.add_parser("key", help="API-key management (self-hosted HTTP auth)")
    key_sp = kr.add_subparsers(dest="key_cmd")
    kc = key_sp.add_parser("create", help="Create a local registry team + tt_ API key for 'serve --http --auth tenant'")
    kc.add_argument("--name", default="local", help="Team name (default 'local')")
    kc.add_argument("--bind", default="127.0.0.1",
                    help="Expected serve HTTP bind address for the MCP URL hint "
                         "(default 127.0.0.1; mirror the --bind you pass to 'serve --http')")
    kc.add_argument("--port", type=int, default=8000,
                    help="Expected serve HTTP port for the MCP URL hint "
                         "(default 8000; mirror the --port you pass to 'serve --http')")
    init = sp.add_parser("init", help="Auto-detect FalkorDB and create default graph")
    init.add_argument("--path", default=None, help="Path for embedded mode (opt-in)")
    init.add_argument("--yes", "-y", action="store_true", help="Skip prompts, auto-index repo")
    init.add_argument("--api-key", dest="api_key", default=None, help="Connect to Tortoise Cloud instead of local Docker")
    # #304 hosted-path extras
    init.add_argument("--json", action="store_true", help="Machine-readable JSON output (cloud mode)")
    init.add_argument("--harness", choices=["claude", "codex", "cursor", "pi"], default=None,
                      help="Target agent harness for MCP config output (cloud mode)")
    init.add_argument("--write-mcp-config", dest="write_mcp_config", action="store_true",
                      help="Write .mcp.json for the --harness target (cloud mode; requires --harness)")
    init.add_argument("--force", action="store_true",
                      help="Overwrite an existing tortoise entry in .mcp.json (cloud mode)")
    setup = sp.add_parser("setup", help="Configure memory_filter per role (interactive)")
    setup.add_argument("--role", default=None, help="Role name (non-interactive, outputs YAML)")
    setup.add_argument("--team", default=None, help="Team name (used with --role)")
    setup.add_argument("--output", default=None, help="Save config to file instead of stdout")
    doctor = sp.add_parser("doctor", help="Health check — verify Tortoise setup")
    doctor.add_argument("--path", default=None, help="Path for embedded mode")
    doctor.add_argument(
        "--db", default=None,
        help="Docker URI (docker://, redis://, rediss://) or embedded DB file "
             "path for the graph-health check",
    )
    onboard = sp.add_parser("onboard", help="Guided onboarding: init → index → demo → doctor")
    onboard.add_argument("--path", default=None, help="Path for embedded mode")
    hs = sp.add_parser("health-server", help="Start standalone /health HTTP server")
    hs.add_argument("--port", type=int, default=9090, help="HTTP port (default: 9090)")
    hs.add_argument("--bind", default="127.0.0.1", help="Bind address (default: 127.0.0.1)")
    # tortoise team <subcommand>
    team = sp.add_parser("team", help="Team management (Tortoise Cloud)")
    team_sp = team.add_subparsers(dest="team_cmd")
    team_info_p = team_sp.add_parser("info", help="Show team info and usage")  # noqa: F841
    # tortoise team keys {list,create,revoke} (#304)
    team_keys = team_sp.add_parser("keys", help="Manage API keys")
    team_keys_sp = team_keys.add_subparsers(dest="team_keys_cmd")
    team_keys_list_p = team_keys_sp.add_parser("list", help="List API keys")
    team_keys_list_p.add_argument("--json", action="store_true", help="Machine-readable JSON output")
    team_keys_create_p = team_keys_sp.add_parser("create", help="Create a new API key (shown once)")
    team_keys_create_p.add_argument("--json", action="store_true", help="Machine-readable JSON output")
    team_keys_create_p.add_argument("--name", metavar="LABEL", default="",
                                    help="Optional label (max 64 chars) to remember which key is which")
    team_keys_revoke_p = team_keys_sp.add_parser("revoke", help="Revoke an API key")
    team_keys_revoke_p.add_argument("key_id", help="Key ID to revoke")
    team_keys_revoke_p.add_argument("--json", action="store_true", help="Machine-readable JSON output")
    team_keys_revoke_p.add_argument("--force", "-f", action="store_true", help="Skip the confirmation prompt")
    # tortoise signup — zero-email free-team mint (issue #663)
    signup_p = sp.add_parser("signup", help="Mint a free hosted team + API key — no email or dashboard (2 free teams/IP/24h)")
    signup_p.add_argument(
        "--force", action="store_true",
        help="Mint a fresh key even if a stored key exists (#1708)",
    )
    signup_p.add_argument(
        "--claim", action="store_true",
        help="After minting, print the dashboard claim instructions: sign in "
             "with GitHub/Google on the dashboard and paste this key to attach "
             "a verified identity to the anonymous team (#1082)",
    )
    recover_p = sp.add_parser(
        "recover",
        help="Keyless team-key recovery with the saved st_ signup token (#1709).",
    )
    recover_p.add_argument(
        "--token", type=str, default=None,
        help="The st_ signup token (defaults to the stored one).",
    )
    token_revoke_p = sp.add_parser(
        "token-revoke",
        help="Revoke the saved st_ signup token — it can no longer recover "
             "keys on the team (#1715).",
    )
    token_revoke_p.add_argument(
        "--token", type=str, default=None,
        help="The st_ signup token (defaults to the stored one).",
    )
    # tortoise index github <url>
    idx = sp.add_parser("index", help="Index content into the graph")
    idx_sp = idx.add_subparsers(dest="index_cmd")
    ig = idx_sp.add_parser("github", help="Index a GitHub repo's markdown files")
    ig.add_argument("url", help="GitHub repo URL (https://github.com/user/repo)")
    # #715 P2 conf 85: --db is optional so password-bearing targets can be
    # handed to background children via TORTOISE_DB_URI env (never argv).
    ig.add_argument("--db", default=None,
                    help="Docker URI or file path for target database "
                         "(default: TORTOISE_DB_URI / FALKORDB_* / embedded path)")
    ig.add_argument("--branch", default="main", help="Git branch to index")
    ig.add_argument("--background", action="store_true", help="Run in background")
    # tortoise index sessions — reconciliation sweep (#280 item 3)
    isess = idx_sp.add_parser("sessions", help="Reconciliation sweep: index unindexed/stale session files")
    isess.add_argument("--dir", default=None,
                       help="Session corpus directory (default: ~/.tortoise/docs/conversations)")
    isess.add_argument("--db", default=None,
                       help="DB target override (default: TORTOISE_DB_URI / FALKORDB_* / embedded path)")
    isess.add_argument("--metadata", action="store_true",
                       help="Run LLM metadata extraction (default: keyword-only — sweep never burns LLM tokens)")
    # tortoise index directory — the unified index path (epic #900 T8, §6.5)
    idir = idx_sp.add_parser("directory",
                             help="Index a corpus directory of .md files as Sources + Events/Documents (unified index path)")
    idir.add_argument("corpus_dir", nargs="?", default=None,
                      help="Corpus directory (default: TORTOISE_INGEST_BASE_DIR — the hook passes positionally)")
    idir.add_argument("--db", default=None,
                      help="DB target override (default: TORTOISE_DB_URI / FALKORDB_* / embedded path)")
    idir.add_argument("--metadata", action="store_true",
                      help="Run metadata extraction + embeddings (default: extract_metadata=False — the NO-NETWORK mode)")
    idir.add_argument("--corpus-name", default=None,
                      help="Explicit corpus_name override (default: basename of the resolved corpus root; "
                           "give same-basename corpora distinct names on a shared graph — §8.2)")
    # tortoise create-point <content> --kind <kind>
    cp = sp.add_parser("create-point", help="Create a Point via Tortoise Cloud API")
    cp.add_argument("content", help="Point content (text)")
    cp.add_argument("--kind", default="statement", help="Point kind (default: statement)")
    # tortoise session <subcommand>
    session = sp.add_parser("session", help="Manage Tortoise Cloud sessions")
    session_sp = session.add_subparsers(dest="session_cmd")
    session_capture = session_sp.add_parser("capture", help="Capture a session from a transcript file")
    session_capture.add_argument("--file", required=True, help="Path to transcript file")
    session_list = session_sp.add_parser("list", help="List all sessions")  # noqa: F841
    session_view = session_sp.add_parser("view", help="View a specific session")
    session_view.add_argument("id", help="Session ID")
    # tortoise list-kinds
    lk = sp.add_parser("list-kinds", help="List all pointKinds present in the graph with counts")  # noqa: F841
    # tortoise context — memory digest for agent session-start hooks
    ctx = sp.add_parser("context", help="Print memory digest for agent session-start injection")  # noqa: F841
    # tortoise list-sources
    ls = sp.add_parser("list-sources", help="List all Sources with point counts")  # noqa: F841
    # tortoise decide --input <json|yaml>
    dc = sp.add_parser("decide", help="Compare options via EP belief propagation")
    dc.add_argument("--input", "-i", help="Path to JSON or YAML input file with options/criteria/findings/edges")
    dc.add_argument("--options", help="JSON dict of options, e.g. '{\"opt:a\": \"desc\"}'")
    dc.add_argument("--criteria", help="JSON dict of criteria")
    dc.add_argument("--findings", help="JSON dict of findings")
    dc.add_argument("--edges", help="JSON list of edges, e.g. '[\"crit:1\", \"IMPL\", \"opt:a\"]' or full edge dicts")
    dc.add_argument("--context-free", action="store_true",
                    help="Deprecated no-op — context-free (explicit factors) is the only mode since #49 Phase 2",)
    dc.add_argument("--db", help=(
        f"DB target override — URI ({uri_schemes_hint}) or absolute path "
        "(default: TORTOISE_DB_URI, else legacy FALKORDB_*, else embedded path)"))
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
    elif args.cmd == "audit":
        return _cmd_audit(args)
    elif args.cmd == "reconcile":
        return _cmd_reconcile(args)
    elif args.cmd == "backfill":
        _cmd_backfill(args)
        return 0
    elif args.cmd == "verify":
        return _cmd_verify(args)
    elif args.cmd == "export":
        return _cmd_export(args)
    elif args.cmd == "validate":
        return _cmd_validate(args)
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
        if getattr(args, "http", False):
            return _cmd_serve_http(args)
        # Startup warning for the #702 trap: stdio + TORTOISE_API_KEY rejects
        # every tool (or crashes at import without a pepper).
        if _os.environ.get("TORTOISE_API_KEY"):
            print("⚠️  TORTOISE_API_KEY is set — stdio MCP will reject all tools.", file=sys.stderr)
            print("    Self-hosted auth: use 'tortoise serve --http' instead. Dev mode: unset TORTOISE_API_KEY.", file=sys.stderr)
        from tortoise.mcp_server import main as serve_main
        serve_main()
        return 0
    elif args.cmd == "key":
        if args.key_cmd == "create":
            return _cmd_key_create(args)
        print("tortoise key: unknown subcommand. Try: tortoise key create --help", file=sys.stderr)
        return 1
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
    elif args.cmd == "recover":
        return _cmd_recover(args)
    elif args.cmd == "token-revoke":
        return _cmd_token_revoke(args)
    elif args.cmd == "team":
        if args.team_cmd == "info":
            return _cmd_team_info(args)
        elif args.team_cmd == "keys":
            if args.team_keys_cmd == "list":
                return _cmd_team_keys_list(args)
            elif args.team_keys_cmd == "create":
                return _cmd_team_keys_create(args)
            elif args.team_keys_cmd == "revoke":
                return _cmd_team_keys_revoke(args)
            team_keys.print_help()
            return 1
        team.print_help()
        return 1
    elif args.cmd == "create-point":
        return _cmd_create_point(args)
    elif args.cmd == "session":
        return _cmd_session(args)
    elif args.cmd == "index":
        if args.index_cmd == "github":
            return _cmd_index_github(args)
        elif args.index_cmd == "sessions":
            return _cmd_index_sessions(args)
        elif args.index_cmd == "directory":
            return _cmd_index_directory(args)
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
