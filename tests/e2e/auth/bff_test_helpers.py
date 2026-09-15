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
import time

import pytest


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
