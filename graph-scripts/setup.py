#!/usr/bin/env python3
# Historical — uses embedded tortoise.db. Do not run against production Docker.
"""Tortoise setup orchestrator — 10-step guided setup.

    python3 scripts/setup.py

Idempotent: safe to re-run, skips completed steps.
Lock: /tmp/tortoise-setup.lock prevents concurrent runs.
Never imports tortoise.* (works before deps are installed).
"""
from __future__ import annotations

import fcntl
import os
import shutil
import socket
import subprocess
import sys
import textwrap
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
LOCKFILE = Path("/tmp/tortoise-setup.lock")

# ── globals set by steps ────────────────────────────────────────────────
MODE = "docker"
ENV_VARS: dict[str, str] = {}

# ── UI helpers ──────────────────────────────────────────────────────────

GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
BOLD = "\033[1m"
RESET = "\033[0m"


def _progress(step: int, label: str) -> None:
    sys.stderr.write(f"\n{BOLD}[{step}/10]{RESET} {label}...")


def _done(elapsed: float, msg: str = "") -> None:
    tag = f" {msg}" if msg else ""
    sys.stderr.write(f" {GREEN}\u2713{RESET} ({elapsed:.1f}s){tag}\n")


def _skip(msg: str) -> None:
    sys.stderr.write(f" {YELLOW}(skipped — {msg}){RESET}\n")


def _fail(reason: str) -> None:
    sys.stderr.write(f"\n{RED}\u2717{RESET} {reason}\n")
    sys.exit(1)


def _confirm(prompt: str) -> bool:
    resp = input(f"  {prompt} [Y/n]: ").strip().lower()
    return resp in ("", "y", "yes")


def _pick(title: str, options: list[str]) -> str:
    """Numbered menu. Returns chosen option text (not index)."""
    print(f"\n  {title}")
    for i, opt in enumerate(options, 1):
        print(f"  [{i}] {opt}")
    while True:
        try:
            c = input(f"  Choose [1-{len(options)}] (default 1): ").strip()
            if not c:
                return options[0]
            idx = int(c) - 1
            if 0 <= idx < len(options):
                return options[idx]
        except (ValueError, EOFError):
            pass
        print("  Invalid choice, try again.")


# ── subprocess helpers ──────────────────────────────────────────────────

def _run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    """Run a command; on failure print stderr and exit cleanly."""
    try:
        return subprocess.run(cmd, check=True, capture_output=True, text=True, **kw)
    except subprocess.CalledProcessError as e:
        err = e.stderr.strip() if e.stderr else str(e)
        _fail(f"command failed: {' '.join(cmd)}\n  {err}")
    except FileNotFoundError:
        _fail(f"command not found: {cmd[0]}")


def _run_ok(cmd: list[str]) -> bool:
    """Return True if command exits 0, False otherwise (silent)."""
    try:
        subprocess.run(cmd, check=True, capture_output=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def _run_output(cmd: list[str]) -> str:
    """Return stdout or empty string on failure."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, check=True).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


# ── lock ────────────────────────────────────────────────────────────────

def _acquire():
    lock = open(LOCKFILE, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        lock.write(str(os.getpid()))
        lock.flush()
        return lock
    except (IOError, OSError):
        print(f"{RED}\u2717{RESET} Another setup instance is running ({LOCKFILE})")
        sys.exit(1)


def _release(lock):
    try:
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()
        LOCKFILE.unlink(missing_ok=True)
    except OSError:
        pass


# ── .env helpers ────────────────────────────────────────────────────────

def _parse_env(path: Path) -> dict[str, str]:
    d: dict[str, str] = {}
    if not path.exists():
        return d
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, _, v = line.partition("=")
            d[k.strip()] = v.strip()
    return d


def _write_env(path: Path, d: dict[str, str]) -> None:
    lines = [f"{k}={v}" for k, v in d.items()]
    path.write_text("\n".join(lines) + "\n")


# ── pre-flight ──────────────────────────────────────────────────────────

def _preflight_disk() -> None:
    try:
        usage = shutil.disk_usage(PROJECT)
        gb = usage.free / (1024 ** 3)
    except OSError:
        return  # can't check — don't block

    if gb < 10:
        _fail(f"Only {gb:.1f} GB free — need at least 10 GB. "
              f"Your machine doesn't have enough space for Tortoise. "
              f"Request a hosted version — message us.")


def _preflight_ram() -> None:
    total_ram = _detect_ram_gb()
    if total_ram is None:
        return  # can't determine — don't block

    if total_ram < 2:
        _fail(f"Only {total_ram:.1f} GB RAM — need at least 2 GB. "
              f"Request a hosted version — message us.")
    if total_ram < 4:
        print(f"  {YELLOW}\u26a0{RESET} Only {total_ram:.1f} GB RAM — Docker needs ~4GB. "
              f"FalkorDB Lite recommended.")


def _detect_ram_gb() -> float | None:
    """Return total RAM in GB, or None if undetectable."""
    # macOS: sysctl hw.memsize
    try:
        out = _run_output(["sysctl", "-n", "hw.memsize"])
        if out:
            return int(out) / (1024 ** 3)
    except Exception:
        pass

    # Linux: os.sysconf
    try:
        page_size = os.sysconf('SC_PAGE_SIZE')
        total_pages = os.sysconf('SC_PHYS_PAGES')
        return (page_size * total_pages) / (1024 ** 3)
    except (ValueError, AttributeError, OSError):
        pass

    return None


def _preflight_network() -> None:
    try:
        socket.getaddrinfo("github.com", 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        _fail("No network access. Tortoise needs internet for setup. "
              "Request a hosted version — message us.")


# ── WSL2 detection ─────────────────────────────────────────────────────

def _is_wsl2() -> bool:
    """Detect WSL2 from within Linux — sys.platform=='linux' in WSL2."""
    if os.path.exists("/proc/sys/fs/binfmt_misc/WSL"):
        return True
    try:
        with open("/proc/version") as f:
            if "microsoft" in f.read().lower():
                return True
    except OSError:
        pass
    return False


# ── step 1: platform ────────────────────────────────────────────────────

def step1() -> None:
    if _is_wsl2():           # WSL2 reports as Linux — detect first
        return
    if sys.platform == "darwin":
        return
    if sys.platform == "linux":
        return
    if sys.platform == "win32":
        _fail("Windows support requires WSL2. Install WSL2: wsl --install. "
              "Request a hosted version — message us.")
    _fail("macOS, Linux, or Windows (WSL2) required. "
          "Request a hosted version — message us.")


# ── step 2: python ─────────────────────────────────────────────────────

def step2() -> None:
    v = sys.version_info
    if (v.major, v.minor) < (3, 10):
        # git presence is covered by README prerequisites (need git to clone the repo).
        hint = "brew install python@3.14"
        if _is_wsl2() or sys.platform == "linux":
            if shutil.which("apt"):
                hint = "sudo apt install python3"
            elif shutil.which("dnf"):
                hint = "sudo dnf install python3"
            elif shutil.which("pacman"):
                hint = "sudo pacman -S python"
        _fail(f"Python {v.major}.{v.minor} too old — need >= 3.10. Try: {hint}. "
              f"Request a hosted version — message us.")


# ── step 3: venv ────────────────────────────────────────────────────────

def step3() -> None:
    # WSL2 uses Unix paths (venv/bin/pip), so no Windows path handling needed.
    venv = PROJECT / ".venv"
    if venv.exists():
        _skip("already exists")
        return
    try:
        subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
    except Exception as e:
        _fail(f"Failed to create virtual environment: {e}")
    _run([str(venv / "bin" / "pip"), "install", "--upgrade", "pip"])


# ── step 4: docker detection ────────────────────────────────────────────

def step4() -> None:
    global MODE

    if _run_ok(["docker", "info"]):
        MODE = "docker"
        return

    if shutil.which("docker"):
        if sys.platform == "linux":
            _fail("Docker daemon is not running. Start it: sudo systemctl start docker. "
                  "Request a hosted version — message us.")
        _fail("Docker daemon is not running. Start Docker Desktop and re-run. "
              "Request a hosted version — message us.")

    # Docker not installed — OS-specific guidance
    if _is_wsl2():
        _step4_wsl2()
    elif sys.platform == "linux":
        _step4_linux()
    elif sys.platform == "win32":
        _step4_wsl2()
    else:
        _step4_macos()


def _step4_macos() -> None:
    global MODE
    choice = _pick(
        "Docker not found. Container mode needs Docker for FalkorDB.",
        [
            "Install Docker via OrbStack (recommended)",
            "Use FalkorDB Lite (embedded, no container needed)",
            "Quit",
        ],
    )

    if choice.startswith("Install"):
        print("\n  Trying OrbStack (native macOS, fast)...")
        if shutil.which("brew"):
            try:
                subprocess.run(["brew", "install", "--cask", "orbstack"], check=True)
            except Exception as e:
                _fail(f"Failed to install OrbStack: {e}. "
                      f"Request a hosted version — message us.")
            print("  Waiting for Docker daemon...", end="", flush=True)
            for _ in range(30):
                if _run_ok(["docker", "info"]):
                    print(" ready.")
                    return
                time.sleep(1)
            print()
            print("  Docker daemon not responding. Start OrbStack and re-run setup.")
            sys.exit(1)
        print("  Homebrew not found. Install it first:")
        print("    /bin/bash -c \"$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\"")
        print("  Then re-run setup.")
        sys.exit(1)

    elif choice.startswith("Use FalkorDB"):
        MODE = "lite"
    else:
        sys.exit(0)


def _step4_linux() -> None:
    global MODE
    choice = _pick(
        "Docker not found. Container mode needs Docker for FalkorDB.",
        [
            "Install Docker (native Linux)",
            "Use FalkorDB Lite (embedded, no container needed)",
            "Quit",
        ],
    )

    if choice.startswith("Install"):
        if shutil.which("apt"):
            cmd = "sudo apt install docker.io"
        elif shutil.which("dnf"):
            cmd = "sudo dnf install docker"
        elif shutil.which("pacman"):
            cmd = "sudo pacman -S docker"
        else:
            print("  Could not detect package manager. Install Docker manually:")
            print("    https://docs.docker.com/engine/install/")
            print("  Then re-run setup.")
            sys.exit(1)
        print(f"\n  Run: {cmd}")
        print("  Then: sudo systemctl enable --now docker")
        print("  Then re-run setup.")
        sys.exit(1)

    elif choice.startswith("Use FalkorDB"):
        MODE = "lite"
    else:
        sys.exit(0)


def _step4_wsl2() -> None:
    global MODE
    choice = _pick(
        "Docker not found. Container mode needs Docker for FalkorDB.",
        [
            "Install Docker Desktop (WSL2 backend)",
            "Use FalkorDB Lite (embedded, no container needed)",
            "Quit",
        ],
    )

    if choice.startswith("Install"):
        print("\n  Docker Desktop with WSL2 backend:")
        print("    1. Install Docker Desktop: https://docs.docker.com/desktop/windows/install/")
        print("    2. Enable Settings \u2192 Resources \u2192 WSL Integration")
        print("    3. Re-run setup.")
        sys.exit(1)

    elif choice.startswith("Use FalkorDB"):
        MODE = "lite"
    else:
        sys.exit(0)


# ── step 5: docker version ──────────────────────────────────────────────

def step5() -> None:
    if MODE != "docker":
        _skip("lite mode")
        return
    ver = _run_output(["docker", "info", "--format", "{{.ServerVersion}}"])
    if not ver:
        _fail("Could not determine Docker server version.")
    major = int(ver.split(".")[0])
    if major < 20:
        _fail(f"Docker {ver} too old — need >= 20.10. Update Docker Desktop. "
              f"Request a hosted version — message us.")


# ── step 6: LLM detection ───────────────────────────────────────────────

_OLLAMA_PROVIDERS = {
    "DEEPSEEK_API_KEY": ("deepseek", "deepseek-chat"),
    "OPENAI_API_KEY": ("openai", "gpt-4o-mini"),
    "OPENROUTER_API_KEY": ("openrouter", "openai/gpt-4o-mini"),
    "GEMINI_API_KEY": ("gemini", "gemini-2.0-flash"),
}

_RELATION_DEFAULTS = {
    "deepseek": "deepseek:deepseek-reasoner",
    "openai": "openai:gpt-4o",
    "openrouter": "openrouter:openai/gpt-4o",
    "gemini": "gemini:gemini-2.0-flash",
}

_KEY_ENV_MAP = {
    "openai": "OPENAI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "gemini": "GEMINI_API_KEY",
}


def _detect_ollama_models() -> list[str]:
    try:
        out = _run_output(["ollama", "list"])
        models = []
        for line in out.split("\n")[1:]:  # skip header
            if line.strip():
                models.append(line.split()[0])
        return models
    except Exception:
        return []


def _pick_default_provider(keys: dict[str, str]) -> tuple[str, str] | None:
    """Return (provider, default_model) for the first found API key."""
    for env_var, (provider, model) in _OLLAMA_PROVIDERS.items():
        if env_var in keys and keys[env_var]:
            return provider, model
    return None


def step6() -> None:
    """
    Priority:
      1. Autodetect API keys + Ollama → confirm
      2. Nothing found → walk through model selection
    """
    global ENV_VARS

    # Scan environment for API keys
    keys: dict[str, str] = {}
    for var in ["DEEPSEEK_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY", "GEMINI_API_KEY"]:
        val = os.environ.get(var, "")
        if val:
            keys[var] = val

    ollama_models = _detect_ollama_models()

    # ── Nothing found ──
    if not keys and not ollama_models:
        print("\n  No LLM provider detected (no API keys, no Ollama).")

        pm_raw = _pick(
            "Point model (extraction tier — cheap, fast):",
            [
                "ollama:llama3.2:3b         (local, free — install Ollama first)",
                "deepseek:deepseek-chat      ($0.27 / M input tokens)",
                "openrouter:openai/gpt-4o-mini",
                "Enter custom provider:model",
            ],
        )
        if pm_raw.startswith("Enter"):
            pm_raw = input("  point model (provider:model): ").strip()
        else:
            pm_raw = pm_raw.split()[0]

        rm_raw = _pick(
            "Relation model (reasoning tier — needs thinking capability):",
            [
                "deepseek:deepseek-chat      ($0.27 / M input tokens)",
                "openrouter:anthropic/claude-3.5-haiku",
                "openrouter:openai/gpt-4o",
                "ollama:llama3.2:3b          (no reasoning — expect lower quality)",
                "Enter custom provider:model",
            ],
        )
        if rm_raw.startswith("Enter"):
            rm_raw = input("  relation model (provider:model): ").strip()
        else:
            rm_raw = rm_raw.split()[0]

        ENV_VARS["TORTOISE_POINT_MODEL"] = pm_raw
        ENV_VARS["TORTOISE_RELATION_MODEL"] = rm_raw
        _prompt_for_keys(pm_raw, rm_raw)
        return

    # ── Autodetected ──
    parts: list[str] = []
    if keys:
        parts.append(f"{', '.join(keys)}")
    if ollama_models:
        preview = ollama_models[:3]
        extra = f" (+{len(ollama_models) - 3} more)" if len(ollama_models) > 3 else ""
        parts.append(f"Ollama: {', '.join(preview)}{extra}")

    print(f"\n  Autodetected: {' | '.join(parts)}")

    # Pick defaults
    provider_info = _pick_default_provider(keys)
    if provider_info:
        provider, model = provider_info
        pm = f"{provider}:{model}"
        rm = _RELATION_DEFAULTS.get(provider, pm)
    elif ollama_models:
        pm = f"ollama:{ollama_models[0]}"
        rm = f"ollama:{ollama_models[0]}"
    else:
        pm = "mock:cheap"
        rm = "mock:reason"

    if _confirm(f"Point: {pm}   |   Relation: {rm}  — use these?"):
        ENV_VARS["TORTOISE_POINT_MODEL"] = pm
        ENV_VARS["TORTOISE_RELATION_MODEL"] = rm
    else:
        pm = input("  Point model (provider:model): ").strip()
        rm = input("  Relation model (provider:model): ").strip()
        if pm:
            ENV_VARS["TORTOISE_POINT_MODEL"] = pm
        if rm:
            ENV_VARS["TORTOISE_RELATION_MODEL"] = rm

    _prompt_for_keys(
        ENV_VARS.get("TORTOISE_POINT_MODEL", ""),
        ENV_VARS.get("TORTOISE_RELATION_MODEL", ""),
    )


def _prompt_for_keys(pm: str, rm: str) -> None:
    """If a model's provider needs an API key and one isn't set in env, prompt."""
    for spec in [pm, rm]:
        if not spec:
            continue
        provider = spec.split(":")[0]
        env_var = _KEY_ENV_MAP.get(provider)
        if not env_var:
            continue
        if os.environ.get(env_var) or _parse_env(PROJECT / ".env").get(env_var):
            continue  # already set in env or .env
        if _confirm(f"Set {env_var}? (enter now, or skip to add later in .env)"):
            val = input(f"  {env_var}=").strip()
            if val:
                os.environ[env_var] = val
                ENV_VARS[env_var] = val


# ── step 7: install deps ────────────────────────────────────────────────

def step7() -> None:
    reqs = PROJECT / "requirements.txt"
    if not reqs.exists():
        _fail("requirements.txt not found.")
    pip = str(PROJECT / ".venv" / "bin" / "pip")
    _run([pip, "install", "-r", str(reqs)])


# ── step 8: FalkorDB container ──────────────────────────────────────────

_FALKORDB_PORT = "16379"
_FALKORDB_CONTAINER = "falkordb-tortoise"


def _container_status(name: str) -> str:
    """Return 'running', 'exited', or 'missing'."""
    out = _run_output(
        ["docker", "container", "inspect", "-f", "{{.State.Status}}", name]
    )
    return out or "missing"


def _port_collision(port: str) -> str | None:
    """Return container ID using this port, or None."""
    out = _run_output(["docker", "ps", "--format", "{{.ID}} {{.Ports}}"])
    for line in out.split("\n"):
        if f":{port}->" in line or f":{port}/" in line:
            return line.split()[0]
    return None


def step8() -> None:
    if MODE != "docker":
        _skip("lite mode")
        return

    status = _container_status(_FALKORDB_CONTAINER)

    if status == "running":
        _skip("container already running")
        return

    if status == "exited":
        print("  Restarting stopped container...")
        _run(["docker", "start", _FALKORDB_CONTAINER])
        return

    if status not in ("missing",):
        print(f"  Removing stale container ({status})...")
        _run(["docker", "rm", "-f", _FALKORDB_CONTAINER])

    # Port collision
    culprit = _port_collision(_FALKORDB_PORT)
    if culprit:
        choice = _pick(
            f"Port {_FALKORDB_PORT} already in use by container {culprit[:12]}.",
            ["Kill it and use 16379", "Quit"],
        )
        if "Kill" in choice:
            _run(["docker", "kill", culprit])
        else:
            sys.exit(0)

    # Start
    data_dir = PROJECT / "data"
    data_dir.mkdir(exist_ok=True)

    _run([
        "docker", "run", "-d",
        "--name", _FALKORDB_CONTAINER,
        "--restart", "unless-stopped",
        "-p", f"127.0.0.1:{_FALKORDB_PORT}:6379",
        "-v", f"{data_dir}:/var/lib/falkordb/data",
        "-e", "REDIS_ARGS=--appendonly yes --save 60 1000 --maxmemory 512mb --maxmemory-policy noeviction",
        "falkordb/falkordb:latest",
    ])
    ENV_VARS["FALKORDB_PORT"] = _FALKORDB_PORT

    # Wait for readiness (up to 30 s)
    print("  Waiting for FalkorDB...", end="", flush=True)
    for _ in range(30):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1)
        try:
            s.connect(("localhost", int(_FALKORDB_PORT)))
            print(" ready.")
            return
        except Exception:
            time.sleep(1)
        finally:
            s.close()
    _fail(f"FalkorDB started but port {_FALKORDB_PORT} not reachable after 30 s. "
          f"Request a hosted version — message us.")


# ── step 9: .env ───────────────────────────────────────────────────────

def step9() -> None:
    example = PROJECT / ".env.example"
    if not example.exists():
        _fail(".env.example not found — run setup from project root.")

    envfile = PROJECT / ".env"

    if envfile.exists():
        # Merge: keep existing values, add missing keys from example
        print("  Merging with existing .env...")
        existing = _parse_env(envfile)
        example_vars = _parse_env(example)
        for k, v in example_vars.items():
            if k not in existing:
                existing[k] = v
        # Apply keys we prompted for (override existing)
        for k, v in ENV_VARS.items():
            existing[k] = v
        _write_env(envfile, existing)
    else:
        # Fresh
        lines = example.read_text().splitlines()
        out: list[str] = []
        for line in lines:
            out.append(line)
        # Append model settings
        for k in ["TORTOISE_POINT_MODEL", "TORTOISE_RELATION_MODEL"]:
            if k in ENV_VARS:
                out.append(f"{k}={ENV_VARS[k]}")
        # Write prompted keys
        for k, v in ENV_VARS.items():
            if k.startswith("TORTOISE_"):
                continue  # already handled above
            for i, line in enumerate(out):
                if line.startswith(f"{k}="):
                    out[i] = f"{k}={v}"
                    break
            else:
                out.append(f"{k}={v}")
        envfile.write_text("\n".join(out) + "\n")

    os.chmod(envfile, 0o600)
    print(f"  Wrote {envfile}")


# ── step 10: smoke test ────────────────────────────────────────────────

def step10() -> None:
    env = os.environ.copy()
    envfile = PROJECT / ".env"
    if envfile.exists():
        for k, v in _parse_env(envfile).items():
            if k not in env:
                env[k] = v

    python = str(PROJECT / ".venv" / "bin" / "python")
    smoke = PROJECT / "scripts" / "smoke_test.py"

    # smoke_test prints to stderr and stdout; let both through
    r = subprocess.run([python, str(smoke)], env=env, cwd=PROJECT)
    if r.returncode != 0:
        _fail("Smoke test failed — check output above.")


# ── step 11: entity population (ONTOLOGY_v2.5 §1.1) ─────────────────────

_GIT_USER = ""
_GIT_EMAIL = ""
_PRODUCT_NAME = ""
_REPO_URL = ""
_PIPELINES: list[str] = []


def _scan_git() -> tuple[str, str, str]:
    """Derive Person + DataSource from git config. Returns (name, email, repo_url)."""
    name = _run_output(["git", "config", "user.name"])
    email = _run_output(["git", "config", "user.email"])
    url = ""
    try:
        remotes = _run_output(["git", "remote", "-v"])
        for line in remotes.split("\n"):
            if "origin" in line and "(fetch)" in line:
                url = line.split()[1]
                # Normalize to https://github.com/org/repo format
                if url.endswith(".git"):
                    url = url[:-4]
                if url.startswith("git@github.com:"):
                    url = "https://github.com/" + url.split(":")[1]
                break
    except Exception:
        pass
    return name, email, url


def _scan_readme() -> str:
    """Extract product name from README."""
    for readme in ["README.md", "readme.md", "Readme.md"]:
        path = PROJECT / readme
        if path.exists():
            first = path.read_text().split("\n")[0]
            # Strip markdown heading markers
            for prefix in ["# ", "## "]:
                if first.startswith(prefix):
                    return first[len(prefix):].strip()
            return first.strip()
    return ""


def _scan_package() -> str:
    """Extract product name from package.json or pyproject.toml."""
    import json
    for pkg in ["package.json", "pyproject.toml"]:
        path = PROJECT / pkg
        if not path.exists():
            continue
        try:
            if pkg.endswith(".json"):
                data = json.loads(path.read_text())
                return data.get("name", "")
            else:
                for line in path.read_text().split("\n"):
                    if line.startswith("name "):
                        return line.split("=")[1].strip().strip('"').strip("'")
        except Exception:
            pass
    return ""


def _scan_repo_structure() -> dict:
    """Detect monorepo structure (apps/, packages/, services/)."""
    subdirs = {}
    for d in ["apps", "packages", "services", "libs"]:
        path = PROJECT / d
        if path.is_dir():
            children = [c.name for c in path.iterdir() if c.is_dir() and not c.name.startswith(".")]
            if children:
                subdirs[d] = children[:5]  # first 5
    return subdirs


def step11a() -> None:
    """Scan repo — auto-detect Person, Product, DataSource."""
    global _GIT_USER, _GIT_EMAIL, _PRODUCT_NAME, _REPO_URL

    name, email, url = _scan_git()
    _GIT_USER = name
    _GIT_EMAIL = email
    _REPO_URL = url

    # Product: package.json name → README title → repo directory name
    _PRODUCT_NAME = _scan_package() or _scan_readme()
    if not _PRODUCT_NAME:
        _PRODUCT_NAME = PROJECT.name

    subdirs = _scan_repo_structure()

    print(f"\n  {GREEN}Detected:{RESET}")
    print(f"    Person:  {name or '(not set — run git config user.name)'}")
    if email:
        print(f"    Email:   {email}")
    print(f"    Product: {_PRODUCT_NAME}")
    if url:
        short_url = url.replace("https://github.com/", "")
        print(f"    Repo:    {short_url}")
    if subdirs:
        for d, children in subdirs.items():
            print(f"    {d}/: {', '.join(children)}")


def step11b() -> None:
    """Confirm or refine Product name."""
    global _PRODUCT_NAME

    print(f"\n  Product name: {BOLD}{_PRODUCT_NAME}{RESET}")
    resp = input(f"  Press enter to confirm, or type a new name, or 'skip': ").strip()
    if resp.lower() == "skip":
        _PRODUCT_NAME = ""
        print(f"  {YELLOW}Skipped — organize later with 'tortoise pipeline config'{RESET}")
    elif resp:
        _PRODUCT_NAME = resp
        print(f"  Product set to: {_PRODUCT_NAME}")
    else:
        print(f"  Confirmed: {_PRODUCT_NAME}")


def step11c() -> None:
    """Pipeline activation — pre-selected defaults."""
    global _PIPELINES

    print(f"\n  Keep your knowledge graph updated automatically:")
    print(f"    [1] GitHub issues/PRs pipeline (recommended)")
    print(f"    [2] Docs pipeline (auto-tags new docs)")
    print(f"    [3] Both")
    print(f"    [4] Skip — I'll set up later")

    resp = input(f"  Choose [1-4] (default 3): ").strip()
    if resp == "1":
        _PIPELINES = ["github_issues"]
    elif resp == "2":
        _PIPELINES = ["docs"]
    elif resp == "4" or resp.lower() == "skip":
        _PIPELINES = []
    else:
        _PIPELINES = ["github_issues", "docs"]  # default

    if _PIPELINES:
        print(f"  Enabled: {', '.join(_PIPELINES)}")
    else:
        print(f"  {YELLOW}No pipelines enabled — set up later with 'tortoise pipeline config'{RESET}")


def step11d() -> None:
    """Review and confirm."""
    print(f"\n  {BOLD}Summary:{RESET}")
    if _GIT_USER:
        print(f"    {GREEN}✓{RESET} Person: {_GIT_USER}")
    if _PRODUCT_NAME:
        print(f"    {GREEN}✓{RESET} Product: {_PRODUCT_NAME}")
    if _REPO_URL:
        print(f"    {GREEN}✓{RESET} DataSource: {_REPO_URL}")
    if _PIPELINES:
        print(f"    {GREEN}✓{RESET} Pipelines: {', '.join(_PIPELINES)}")
    else:
        print(f"    {YELLOW}⚠{RESET}  No pipelines enabled")
    print(f"    {YELLOW}⚠{RESET}  No teams declared — working in solo mode")

    resp = input(f"\n  Press enter to confirm, or 'back' to redo: ").strip()
    if resp.lower() == "back":
        print("  Re-running pipeline setup...")
        step11c()
        step11d()


def step11e() -> None:
    """Write pipelines.yaml and seed FalkorDB with initial entities."""
    global _GIT_USER, _PRODUCT_NAME, _REPO_URL, _PIPELINES

    # ── Write pipelines.yaml ──
    import yaml
    config_path = PROJECT / "config" / "pipelines.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)

    config = {
        "version": 1,
        "pipelines": {},
    }

    if "github_issues" in _PIPELINES and _REPO_URL:
        repo_path = _REPO_URL.replace("https://github.com/", "")
        config["pipelines"]["github_issues"] = {
            "enabled": True,
            "source": "github",
            "sourceKind": "github_issue",
            "trigger": "manual",
            "entity_mapping": {
                "repo_to_team": {},
                "user_to_role": {},
            },
            "entity_kinds": {
                "issue_object": "pm:issue",
                "pr_object": "pm:card",
                "created_event": "pm:cardCreated",
                "completed_event": "pm:cardCompleted",
            },
            "connector": {
                "module": "tortoise.connectors.github",
                "class": "GitHubConnector",
                "config": {"repo": repo_path, "state": "all", "limit": 100},
            },
        }

    if "docs" in _PIPELINES:
        config["pipelines"]["docs"] = {
            "enabled": True,
            "source": "filesystem",
            "trigger": "manual",
            "strategy": ["path-derive"],
            "entity_mapping": {
                "path_to_team": {},
                "path_to_domain": {},
            },
        }

    config_path.write_text(
        yaml.dump(config, default_flow_style=False, sort_keys=False)
    )
    print(f"\n  Wrote {config_path}")

    # ── Seed FalkorDB ──
    try:
        from tortoise.projection import FalkorProjection
        db_path = str(PROJECT / "tortoise.db")
        proj = FalkorProjection(db_path)

        # Person — Subject (ONTOLOGY_v2.5 §1.1)
        if _GIT_USER:
            person_id = f"person:{_GIT_USER.lower().replace(' ', '-')}"
            proj.apply({
                "type": "SubjectAdded",
                "id": person_id,
                "name": _GIT_USER,
                "subject_kind": "naturalPerson",
            })
            print(f"  Seeded Person: {_GIT_USER}")

        # Product — Object (ONTOLOGY_v2.5 §1.1)
        if _PRODUCT_NAME:
            product_id = f"product:{_PRODUCT_NAME.lower().replace(' ', '-')}"
            proj.apply({
                "type": "ObjectRegistered",
                "id": product_id,
                "name": _PRODUCT_NAME,
                "object_kind": "software",
            })
            # aboutSubject edge: Product → Person (ONTOLOGY_v2.5 §2.2)
            if _GIT_USER:
                proj.g.query(
                    "MATCH (o:Object {id: $oid}) "
                    "MATCH (s:Subject {id: $sid}) "
                    "MERGE (o)-[:aboutSubject]->(s)",
                    params={"oid": product_id, "sid": person_id},
                )
            print(f"  Seeded Product: {_PRODUCT_NAME}")

        print(f"  {GREEN}Graph seeded ✓{RESET}")
    except Exception as e:
        print(f"  {YELLOW}⚠ Graph seeding skipped: {e}{RESET}")
        print(f"    Run 'tortoise pipeline run' to populate the graph later.")


def step11() -> None:
    """Wrapper — dispatches to sub-steps in main loop."""
    pass  # handled inline in main()


# ── main ────────────────────────────────────────────────────────────────

def main() -> None:
    os.chdir(PROJECT)

    print(f"{BOLD}{'=' * 60}{RESET}")
    print(f"{BOLD}  Tortoise Setup{RESET}")
    print(f"{BOLD}{'=' * 60}{RESET}")

    lock = _acquire()
    try:
        # Pre-flight
        _progress(0, "pre-flight checks")
        _preflight_network()
        _preflight_disk()
        _preflight_ram()
        _done(0)

        steps = [
            (1, "Platform check", step1),
            (2, f"Python >= 3.10 (found {sys.version_info.major}.{sys.version_info.minor})", step2),
            (3, "Create virtual environment", step3),
            (4, "Docker detection", step4),
            (5, "Docker version >= 20.10", step5),
            (6, "LLM provider detection", step6),
            (7, "Install dependencies", step7),
            (8, "FalkorDB container", step8),
            (9, ".env configuration", step9),
            (10, "Smoke test", step10),
            (11, "Entity setup — auto-detect your project", step11),
        ]

        for num, label, fn in steps:
            _progress(num, label)
            t0 = time.time()
            if fn == step11:
                step11a()
                step11b()
                step11c()
                step11d()
                step11e()
            else:
                fn()
            _done(time.time() - t0)

        print(f"\n{BOLD}{'=' * 60}{RESET}")
        print(f"  {GREEN}Setup complete!{RESET} 🐢")
        print(f"  Mode: {MODE}")
        print(f"  Run:  .venv/bin/python -m tortoise --help")
        print(f"{BOLD}{'=' * 60}{RESET}\n")

    finally:
        _release(lock)


if __name__ == "__main__":
    main()
