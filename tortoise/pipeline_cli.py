"""Pipeline CLI — manage entity extraction pipelines.

Usage:
  tortoise pipeline list              — show all pipelines and their status
  tortoise pipeline enable <name>     — enable a pipeline
  tortoise pipeline disable <name>    — disable a pipeline
  tortoise pipeline status <name>     — show detailed status for one pipeline
  tortoise pipeline run <name>        — trigger a pipeline manually

Config: config/pipelines.yaml (ONTOLOGY_v2.5 §1.1 entity kinds, PM domain extension)
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

import yaml

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "pipelines.yaml"
_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load() -> dict:
    if not CONFIG_PATH.exists():
        return {"version": 1, "pipelines": {}}
    return yaml.safe_load(CONFIG_PATH.read_text()) or {"version": 1, "pipelines": {}}


def _save(data: dict) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(yaml.dump(data, default_flow_style=False, sort_keys=False))


def cmd_list() -> None:
    """List all pipelines with status."""
    data = _load()
    pipelines = data.get("pipelines", {})
    if not pipelines:
        print("No pipelines configured.")
        return

    print(f"{'PIPELINE':<25} {'ENABLED':<8} {'SOURCE':<15} {'TRIGGER':<12}")
    print("-" * 60)
    for name, cfg in sorted(pipelines.items()):
        enabled = "✓" if cfg.get("enabled") else "—"
        source = cfg.get("source", "?")
        trigger = cfg.get("trigger", "manual")
        print(f"{name:<25} {enabled:<8} {source:<15} {trigger:<12}")


def cmd_enable(name: str) -> None:
    data = _load()
    if name not in data.get("pipelines", {}):
        print(f"Pipeline '{name}' not found in config.")
        sys.exit(1)
    data["pipelines"][name]["enabled"] = True
    _save(data)
    print(f"Enabled: {name}")


def cmd_disable(name: str) -> None:
    data = _load()
    if name not in data.get("pipelines", {}):
        print(f"Pipeline '{name}' not found in config.")
        sys.exit(1)
    data["pipelines"][name]["enabled"] = False
    _save(data)
    print(f"Disabled: {name}")


def cmd_status(name: str) -> None:
    data = _load()
    cfg = data.get("pipelines", {}).get(name)
    if not cfg:
        print(f"Pipeline '{name}' not found.")
        return

    print(f"Pipeline: {name}")
    print(f"  Enabled: {cfg.get('enabled')}")
    print(f"  Source:  {cfg.get('source')} ({cfg.get('sourceKind', '?')})")
    print(f"  Trigger: {cfg.get('trigger', 'manual')}")
    em = cfg.get("entity_mapping", {})
    if em:
        print(f"  Entity mappings:")
        if em.get("repo_to_team"):
            for repo, team in em["repo_to_team"].items():
                print(f"    repo {repo} → team {team}")
        if em.get("label_to_team"):
            for label, team in em["label_to_team"].items():
                print(f"    label {label} → team {team}")
        if em.get("user_to_role"):
            for user, role in em["user_to_role"].items():
                print(f"    user {user} → role {role}")


def cmd_run(name: str) -> None:
    data = _load()
    cfg = data.get("pipelines", {}).get(name)
    if not cfg:
        print(f"Pipeline '{name}' not found.")
        sys.exit(1)
    if not cfg.get("enabled"):
        print(f"Pipeline '{name}' is disabled. Enable it first: tortoise pipeline enable {name}")
        sys.exit(1)

    module_path = cfg.get("connector", {}).get("module", "")
    class_name = cfg.get("connector", {}).get("class", "")
    if not module_path or not class_name:
        print(f"Pipeline '{name}' has no connector configured.")
        sys.exit(1)

    print(f"Running {name} pipeline...")
    try:
        import importlib
        import sys as _sys
        if str(_PROJECT_ROOT) not in _sys.path:
            _sys.path.insert(0, str(_PROJECT_ROOT))

        mod = importlib.import_module(module_path)
        conn_cls = getattr(mod, class_name)
        connector = conn_cls(config=cfg.get("connector", {}).get("config"))
        if hasattr(connector, "poll"):
            events = connector.poll()
            print(f"  Got {len(events)} events")
            if hasattr(connector, "ingest"):
                # Create projection and ingest
                from tortoise.projection import FalkorProjection
                from tortoise.config import resolve_db_path
                proj = FalkorProjection(resolve_db_path())
                count = connector.ingest(proj)
                print(f"  Ingested {count} entities into FalkorDB")
                # Auto-dispatch: create missions + cards from ingested Objects
                try:
                    import sys as _s
                    _s.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "operations" / "coordination"))
                    from mission_registry import MissionRegistry
                    from coordinator import CoordinatorDaemon
                    reg = MissionRegistry()
                    d = CoordinatorDaemon(reg)
                    missions = d.ingest_from_graph()
                    if missions:
                        print(f"  Auto-dispatched {missions} missions into cards")
                except Exception:
                    pass  # coordinator not available — ingestion-only mode
            else:
                print("  Connector has no ingest method — events logged only")
        else:
            print("  Connector has no poll method")
    except ImportError as e:
        print(f"  Error: {e}")
        print(f"  Is the connector installed? Module: {module_path}")
        print(f"  Check that {_PROJECT_ROOT} is on PYTHONPATH")
    except Exception as e:
        print(f"  Error running {name}: {e}")


# ── CLI ───────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Tortoise pipeline management")
    sub = ap.add_subparsers(dest="command")

    sub.add_parser("list", help="List all pipelines")

    ep = sub.add_parser("enable", help="Enable a pipeline")
    ep.add_argument("name", help="Pipeline name")

    dp = sub.add_parser("disable", help="Disable a pipeline")
    dp.add_argument("name", help="Pipeline name")

    sp = sub.add_parser("status", help="Show pipeline status")
    sp.add_argument("name", help="Pipeline name")

    rp = sub.add_parser("run", help="Run a pipeline manually")
    rp.add_argument("name", help="Pipeline name")

    args = ap.parse_args(argv)

    if args.command == "list":
        cmd_list()
    elif args.command == "enable":
        cmd_enable(args.name)
    elif args.command == "disable":
        cmd_disable(args.name)
    elif args.command == "status":
        cmd_status(args.name)
    elif args.command == "run":
        cmd_run(args.name)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
