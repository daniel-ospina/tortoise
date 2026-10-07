"""Shared helpers for the #3501 BFF auth suites.

WHY THIS EXISTS
---------------
Every suite here spawns `wrangler pages dev` on a port. Two separate defects
made that unsafe, and both produced GREEN tests asserting nothing:

1. **Fixed ports + `wait(port) is listening`.** `_wait()` succeeds if ANYTHING is
   listening on the port, so on a busy machine a suite could attach to an
   unrelated process and assert against it. Ports are now claimed by binding
   first, and released only when the server we started answers.

2. **Skipping when the toolchain is missing.** A suite that skips because
   `wrangler` is not on PATH is a no-op gate: green, exit 0, zero coverage. That
   happened in CI — `pytest tests/auth/` (the path at the time) reported "33 skipped" and passed, so
   none of the auth security properties were actually enforced.

   The suites therefore require the toolchain and FAIL loudly rather than skip.
   Set `AUTH_ALLOW_NO_TOOLCHAIN=1` only when you are deliberately running a
   toolchain-free subset.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import socket
import subprocess
import time
from pathlib import Path

import pytest

# The dashboard Pages project — the #4054 BFF root. `wrangler pages dev <dir>`
# serves <dir> as the STATIC ROOT but resolves `functions/` against the CWD, so
# serving `dist` from this directory gets production's asset layout AND the
# Functions tree.
REPO_ROOT = Path(__file__).resolve().parents[3]
DASHBOARD_DIR = REPO_ROOT / "website" / "apps" / "dashboard"
DASHBOARD_DIST = "dist"


def ensure_dashboard_dist() -> None:
    """Build the dashboard so the auth suites serve production's upload root.

    WHY `dist` AND NOT `.`
    ---------------------
    `wrangler pages dev .` serves the SOURCE tree. The auth pages live in
    `public/` (`signup.html`, `welcome.html`, `invite-accept.html`), so at the
    source root they are not at `/welcome` and friends — Pages falls through to
    the SPA shell, and a test asserting the reset panel silently asserts against
    `index.html` instead. The deployment uploads `dist/`, which vite builds by
    copying `public/` to the root, so `dist` is the only root that matches
    production.

    Idempotent and mtime-cached: rebuilds only when a source file is newer than
    `dist/index.html`, so a whole session pays for at most one build.
    """
    dist_index = DASHBOARD_DIR / DASHBOARD_DIST / "index.html"
    newest = 0.0
    for p in DASHBOARD_DIR.rglob("*"):
        rel_parts = set(p.relative_to(DASHBOARD_DIR).parts)
        if rel_parts & {"dist", "node_modules", ".wrangler", ".vite"}:
            continue
        if p.is_file():
            newest = max(newest, p.stat().st_mtime)
    if dist_index.is_file() and dist_index.stat().st_mtime >= newest:
        return
    # `npm run build` (the project's own `vite build`) rather than
    # `npx vite build`: npx silently performs an AD-HOC install when the local
    # toolchain is absent, and that install omitted rollup's platform binary
    # (`@rollup/rollup-linux-x64-gnu`) so the whole suite errored at fixture
    # setup with a MODULE_NOT_FOUND far from the cause (#4054, welcome-e2e).
    # A missing local install must fail HERE, naming the missing toolchain.
    proc = subprocess.run(
        ["npm", "run", "build"],
        cwd=str(DASHBOARD_DIR),
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0 or not dist_index.is_file():
        # Fail, never skip: a suite that cannot serve the real asset root would
        # assert against the SPA shell and pass vacuously.
        if not (DASHBOARD_DIR / "node_modules" / ".bin" / "vite").exists():
            pytest.fail(
                "dashboard toolchain is not installed — the auth suites build "
                "and serve the dashboard's dist/ root. Run "
                "`cd website/apps/dashboard && npm ci` first."
            )
        pytest.fail(
            "dashboard build failed — the auth suites serve its dist/ root:\n"
            f"{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}"
        )


def d1_sqlite_files(persist_dir: Path) -> list[Path]:
    """Bound D1 database files under ``<persist_dir>/.wrangler/state/v3/d1/``.

    Excludes ``metadata.sqlite``: Miniflare's own bookkeeping database (D1's
    index plus the cache/observability stores) lives in the SAME tree, is
    touched constantly — so it is usually the NEWEST ``*.sqlite`` — and holds
    none of the bound schema. "Pick the newest ``*.sqlite``" therefore selected
    it and produced ``no such table: sessions`` / ``no such table: auth_flows``
    and a dozen downstream 401s across the auth suites: the seed landed on the
    wrong database and the route read the real (empty) one.

    Use this instead of a bare glob anywhere a spec resolves the local D1.
    """
    return [
        p for p in persist_dir.glob(".wrangler/state/v3/d1/**/*.sqlite")
        if p.name != "metadata.sqlite"
    ]


def require_toolchain() -> None:
    """Fail (do not skip) when a required binary is absent.

    A skipped security suite is indistinguishable from a passing one in CI.
    """
    missing = [b for b in ("node", "wrangler") if not shutil.which(b)]
    if missing and os.environ.get("AUTH_ALLOW_NO_TOOLCHAIN") != "1":
        pytest.fail(
            f"missing required toolchain: {', '.join(missing)} — the auth suites "
            "cannot run and MUST NOT silently pass. Install wrangler (npm i -g "
            "wrangler@4.127.0) or set AUTH_ALLOW_NO_TOOLCHAIN=1 to opt out "
            "explicitly."
        )


def pick_free_port(start: int, taken: set[int] | None = None) -> int:
    """Claim a free port by BINDING it, so two suites cannot collide.

    Merely probing is racy: two calls can both see a port as free and return the
    same value. `taken` closes that hole within a process.
    """
    taken = taken if taken is not None else set()
    for port in range(start, start + 100):
        if port in taken:
            continue
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
                taken.add(port)
                return port
            except OSError:
                continue
    raise RuntimeError(f"no free port in {start}..{start + 100}")


def wait_for_port(port: int, timeout: float = 90.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.4)
    return False


def stop(proc) -> None:
    """Terminate a process group and REAP it.

    Without the wait the next module's readiness probe can succeed against the
    dying server, and its own bind can then hit EADDRINUSE — presenting as a
    confusing failure against a stale mock.
    """
    import signal

    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except Exception:
        return
    try:
        proc.wait(timeout=15)
    except Exception:
        # Escalate to SIGKILL; the process may already be gone, which is fine.
        with contextlib.suppress(Exception):
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
