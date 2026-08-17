"""Fast interpreter-exit close for ephemeral embedded servers (#1371).

At interpreter exit, ~200 leaked embedded redislite servers are closed by
their atexit handlers. redislite's `close()` is slow (~3-4s/server): it runs
`shutdown(save=True)` via redis-py's `execute_command`, which blocks waiting
for the server's +OK response, then polls the process for up to 10s. With
hundreds of leaked servers this is a 10-15 minute tail that counts inside
the CI fast gate's 45m watchdog.

This module provides a fast atexit path for the *ephemeral test-tree* case:
send a fire-and-forget `SHUTDOWN SAVE` over the unix socket (the server
saves — durability preserved — then exits; measured ~0.04s death for small
graphs) and bounded-poll the server pid instead of waiting on the response.

Scope (deliberately narrow — the safety boundary):
- ONLY at the interpreter-exit seam (each tortoise atexit handler routes
  through `_atexit_close`, which calls this helper first).
- ONLY when `TORTOISE_FAST_ATEXIT=1` (opt-in; set by tests/conftest.py and
  the CI workflow env — never in hosted/production paths).
- ONLY for servers whose dbdir is an ephemeral test tree
  (`embedded_reaper._is_ephemeral_dir` + `EPHEMERAL_PREFIXES` — the same
  classification the reaper uses for reap-safety).
- Explicit `close()` / `__exit__` keep redislite's exact semantics — the
  cross-process reopen/restore durability classes (test_index_cli,
  test_flip_gate, test_ingest_rebuild_durability, ...) depend on them.
- A failed fire-and-forget send falls through to the normal close (never
  skip close — the #1005 hygiene contract).
"""
from __future__ import annotations

import os
import signal
import socket
import tempfile
import time

from tortoise.embedded_reaper import _is_ephemeral_dir


def _is_ephemeral_test_server(client) -> bool:
    """True when the client's DB dir sits in an ephemeral test tree.

    Mirrors the reaper's containment check on BOTH sides through realpath
    (a symlinked TMPDIR would otherwise fail the strict relative_to test and
    silently disable the fast path — safe direction, but slow).
    """
    dbdir = getattr(client, "dbdir", None)
    if not dbdir:
        return False
    tmpdir_real = os.path.realpath(tempfile.gettempdir())
    dbdir_real = os.path.realpath(dbdir)
    return _is_ephemeral_dir(dbdir_real, tmpdir_real)


def atexit_fast_close(client) -> bool:
    """Fast-close an ephemeral test-tree redislite client at interpreter exit.

    Returns True when the close was handled by the fast path (or there was
    nothing to do); False when the caller must fall through to the normal
    close().

    Gating (all three must hold):
      1. TORTOISE_FAST_ATEXIT=1 (opt-in flag).
      2. Ephemeral test-tree dbdir.
      3. This client is the last connection to the server (replicating
         redislite's own `_cleanup` guard; a failed probe is treated as
         last-client — the fire-and-forget then fails fast on a dead socket
         and the no-op is safe).
    """
    if os.environ.get("TORTOISE_FAST_ATEXIT") != "1":
        return False
    if not _is_ephemeral_test_server(client):
        return False

    # Other clients still connected -> redislite's fast disconnect path
    # (no shutdown). The last of this server's clients will do the shutdown.
    try:
        if client._connection_count() > 1:
            try:
                client.connection_pool.disconnect()
            except Exception:
                pass
            return True
    except Exception:
        pass  # probe failed -> assume last client (fail toward fast path)

    sock_path = getattr(client, "socket_file", None)
    if not sock_path:
        return False  # cannot reach the server socket -> normal close
    pid = getattr(client, "pid", None)

    # Fire-and-forget SHUTDOWN SAVE: the server saves (RDB durability —
    # redislite's default save points never fire for short runs, so this is
    # the ONLY RDB write for leaked servers) then exits. We do NOT wait for
    # the +OK response — that wait is the 3-4s cost we are eliminating.
    # Any connect/send failure (including socket.timeout, an OSError
    # subclass) falls through to the normal close() — the #1005 "never skip
    # close" hygiene contract. A dead server's normal close is fast (its
    # probes fail immediately); a live-but-unresponsive server must still
    # get a real shutdown attempt.
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(0.5)
        s.connect(sock_path)
        s.sendall(b"*2\r\n$8\r\nSHUTDOWN\r\n$4\r\nSAVE\r\n")
        s.close()
    except OSError:
        try:
            client.connection_pool.disconnect()
        except Exception:
            pass
        return False  # fall through to the normal close()

    # Bounded poll for pid death (durability handoff: the save completes,
    # then the server exits). Small graphs die in ~0.04s; the SIGTERM
    # fallback triggers redis's own graceful save+shutdown, so durability
    # still holds for slow big-graph saves (test-slow files).
    if pid:
        deadline = time.time() + 5.0
        while time.time() < deadline:
            try:
                os.kill(pid, 0)
            except (ProcessLookupError, PermissionError):
                break
            time.sleep(0.02)
        else:
            try:
                os.kill(pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass

    try:
        client.connection_pool.disconnect()
    except Exception:
        pass
    return True
