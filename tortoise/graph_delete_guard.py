"""Race-safe ``GRAPH.DELETE`` guard + poisoned-AOF detection (#2961).

Root cause — empirically reproduced against ``falkordb/falkordb-server:v4.20.4``
------------------------------------------------------------------------------
FalkorDB persists graph writes to the AOF as *effects* (``GRAPH.EFFECT``),
not as the queries that produced them. A graph created implicitly with no
resulting change therefore leaves **no replayable creation record**, while
its key exists and is perfectly deletable::

    GRAPH.QUERY g "MATCH (n) DETACH DELETE n"   # absent graph -> key is
                                                # created; nothing changed
                                                # -> NO GRAPH.EFFECT
    GRAPH.DELETE g                              # succeeds, IS propagated

On the next AOF load the replay stream contains ``GRAPH.DELETE g`` with no
``g`` to delete, so the command answers ``-ERR Invalid graph operation on
empty key`` and the server logs::

    == CRITICAL == This server is sending an error to its AOF-loading-client:
    '-ERR Invalid graph operation on empty key'
    after processing the command 'graph.DELETE'

The shared dev container then never reaches a serving state (crash-restart
loop) and the whole docker test lane goes red — every session dies at the
``_assert_backend_identity`` probe with an error indistinguishable from a
real outage.

The trigger is the session sweep's blind DETACH: ``wipe_server`` and
``_drop_one_graph`` issue ``GRAPH.QUERY <g> "MATCH (n) DETACH DELETE n"``
against a name taken from a possibly-stale ``GRAPH.LIST``. When a concurrent
session drops that graph in between, the DETACH **recreates** it as an
empty, record-less phantom and the following ``GRAPH.DELETE`` poisons the
AOF. Note that an *errored* ``GRAPH.DELETE`` is NOT propagated (verified),
so the poison needs the key to exist at delete time — which is exactly what
the phantom DETACH guarantees.

Fix
---
:func:`safe_graph_delete` never emits any graph command for a graph that is
not currently present, and re-checks presence inside a cross-process
critical section (:func:`graph_delete_lock`) so a concurrent deleter cannot
slip between the check and the drop. The phantom-creating sequence can
therefore not be produced by this codebase's sweeps.

The lock degrades gracefully: when the server cannot be reached, when the
client exposes no raw connection (unit-test fakes), or when the command is
rejected, the guard proceeds unlocked and logs a warning. The existence gate
still applies — it just loses the atomicity guarantee against cooperating
processes, never correctness.

Recovery
--------
:func:`is_poisoned_aof_log` / :func:`diagnose_poisoned_aof` recognise the
crash-loop signature in a server log and produce a loud, actionable recovery
message. Detection is deliberately **non-destructive**: quarantining
``appendonlydir`` discards every graph on a *shared* container and can
disrupt concurrent agents' runs, so the repair is left to a human/operator
(the emitted instructions say exactly what to run) instead of being
performed silently. This matches the issue's acceptance ("fails fast with an
actionable message — never a silent restart loop").
"""
from __future__ import annotations

import logging
import subprocess
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

logger = logging.getLogger(__name__)

#: Server-global (not per-graph) advisory lock key. A plain Redis key, so it
#: never shows up in ``GRAPH.LIST`` and never counts as a graph.
GRAPH_DELETE_LOCK_KEY = "tortoise:graph-delete-lock"
_GRAPH_DELETE_LOCK_TTL_MS = 10_000
_GRAPH_DELETE_LOCK_WAIT_S = 10.0
_GRAPH_DELETE_LOCK_POLL_S = 0.005
# Compare-and-delete: only the holder releases. EVAL failing is handled by
# the TTL, never by a blind DEL (that would drop a peer's lock).
_LOCK_RELEASE_LUA = (
    "if redis.call('get', KEYS[1]) == ARGV[1] then "
    "return redis.call('del', KEYS[1]) end return 0"
)


def _raw_connection(db: Any) -> Any | None:
    """The underlying ``redis.Redis`` for a FalkorDB client, or None.

    Unit-test fakes expose only ``list_graphs``/``select_graph``; the guard
    must keep working (unlocked) for them.
    """
    conn = getattr(db, "connection", None)
    if conn is None or not hasattr(conn, "execute_command"):
        return None
    return conn


@contextmanager
def graph_delete_lock(
    db: Any,
    *,
    ttl_ms: int = _GRAPH_DELETE_LOCK_TTL_MS,
    wait_s: float = _GRAPH_DELETE_LOCK_WAIT_S,
    poll_s: float = _GRAPH_DELETE_LOCK_POLL_S,
) -> Iterator[bool]:
    """Cross-process critical section for graph deletion (#2961).

    Yields True when the lock is held for the duration of the block, False
    when the guard is running unlocked (no raw connection, the lock command
    failed, or ``wait_s`` elapsed). Never raises for lock problems and never
    fails the caller: a sweep must not die because the advisory lock is
    unavailable. The TTL (``ttl_ms``) reclaims the lock if the holder dies
    mid-critical-section, so a crash cannot wedge every later sweep.
    """
    conn = _raw_connection(db)
    if conn is None:
        yield False
        return

    token = uuid.uuid4().hex
    acquired = False
    degraded: str | None = None
    deadline = time.monotonic() + wait_s
    while True:
        try:
            acquired = bool(
                conn.execute_command(
                    "SET", GRAPH_DELETE_LOCK_KEY, token, "NX", "PX", ttl_ms))
        except Exception as exc:  # any failure = degrade
            degraded = f"lock command failed ({exc!r})"
            break
        if acquired:
            break
        if time.monotonic() >= deadline:
            degraded = f"lock wait exceeded {wait_s}s"
            break
        time.sleep(poll_s)

    if degraded is not None:
        logger.warning(
            "graph-delete guard: proceeding WITHOUT the cross-process lock "
            "(%s); the presence re-check still applies (#2961)", degraded)

    try:
        yield acquired
    finally:
        if acquired:
            try:
                conn.execute_command(
                    "EVAL", _LOCK_RELEASE_LUA, 1, GRAPH_DELETE_LOCK_KEY, token)
            except Exception as exc:
                logger.debug(
                    "graph-delete guard: lock release failed (%r) — the "
                    "%dms TTL reclaims it", exc, ttl_ms)


def graph_exists(db: Any, name: str) -> bool:
    """True when ``name`` currently appears in ``GRAPH.LIST``.

    Genuine listing failures propagate: callers treat them as their own
    failure (the sweep logs-and-continues per graph, ``wipe_server``
    collects and re-raises).
    """
    return str(name) in {str(g) for g in (db.list_graphs() or [])}


def safe_graph_delete(
    db: Any, name: str, *, detach: bool = True, drop: bool = True,
) -> bool:
    """Delete ``name`` only if it is present, inside the delete lock (#2961).

    This is the *only* sanctioned way for a sweep to drop a graph. It never
    transmits ``GRAPH.QUERY <g> "MATCH (n) DETACH DELETE n"`` nor
    ``GRAPH.DELETE <g>`` for a graph that is not currently present, because
    the DETACH is what materialises the record-less phantom that poisons the
    AOF (module docstring).

    Returns True when a graph command was issued (the graph was present),
    False when the graph was already absent and nothing was sent. Both mean
    the caller's drop-set entry is satisfied; the distinction exists only so
    callers can report a genuine no-op.

    Raises on genuine errors (auth, dead connection) — the guard must not
    swallow real failures.
    """
    with graph_delete_lock(db):
        if not graph_exists(db, name):
            # #2961: the fix. Skipping here is what keeps a stale
            # GRAPH.LIST name from creating an AOF-visible phantom.
            return False
        graph = db.select_graph(name)
        if detach:
            graph.query("MATCH (n) DETACH DELETE n")
        if drop:
            graph.delete()
        return True


# ── Poisoned-AOF detection + loud recovery guidance ───────────────────────
# The AOF-load error line, verbatim from a reproduced FalkorDB 4.20.4 crash
# loop. Matched case-insensitively; both markers must be on ONE line so a
# truncated/partial log can never produce a false positive.
_AOF_LOAD_ERROR_MARKER = "is sending an error to its aof-loading-client"
_POISON_COMMAND_MARKERS = (
    "'graph.delete'",
    "invalid graph operation on empty key",
)


def is_poisoned_aof_log(log_text: str) -> bool:
    """True when ``log_text`` shows the #2961 AOF-load poison signature.

    Pure and hermetic — takes already-collected text, no Docker, no I/O.
    """
    for line in (log_text or "").splitlines():
        low = line.lower()
        if _AOF_LOAD_ERROR_MARKER not in low:
            continue
        if any(marker in low for marker in _POISON_COMMAND_MARKERS):
            return True
    return False


def poisoned_aof_recovery_hint(container: str, volume: str) -> str:
    """Loud, actionable recovery instructions for a poisoned AOF (#2961).

    Deliberately does NOT perform the repair: quarantining ``appendonlydir``
    discards every graph on the shared container and would disrupt any
    concurrently running agent, so it stays an explicit operator action.
    """
    return (
        "POISONED AOF DETECTED (#2961) — this is NOT a backend outage.\n"
        f"  Container {container!r} is crash-looping because its append-only\n"
        "  file replays a GRAPH.DELETE for a graph that no longer exists (a\n"
        "  session-sweep race). It will never become healthy on its own, so\n"
        "  every test session on this lane will keep failing at the probe.\n"
        "\n"
        "  Recovery — quarantine the AOF and restart. This DISCARDS every\n"
        "  test graph on the shared volume, so only do it when no other\n"
        "  agent's run is live:\n"
        f"      docker stop {container}\n"
        f"      docker run --rm -v {volume}:/data alpine:3 sh -c \\\n"
        "        'mv /data/appendonlydir "
        "/data/appendonlydir.corrupt-$(date +%Y%m%d)-2961'\n"
        f"      docker start {container}\n"
    )


def _docker(*args: str, timeout_s: float = 5.0) -> str | None:
    """Best-effort ``docker`` invocation; None when unavailable or failed.

    Never raises and never blocks for long: the diagnosis is an aid, not a
    gate, and must not turn a connection failure into a hang.
    """
    try:
        proc = subprocess.run(
            ["docker", *args], capture_output=True, text=True,
            timeout=timeout_s, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("docker %s unavailable: %r", args[:1], exc)
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def find_container_for_port(port: int) -> str | None:
    """Best-effort name of the local container publishing ``port``.

    Searches running containers first, then all (a crash-looping container
    is frequently in ``Restarting``, which ``docker ps`` alone can miss).
    """
    needle = f":{port}->"
    for args in (("ps", "--format", "{{.Names}}\t{{.Ports}}"),
                 ("ps", "-a", "--format", "{{.Names}}\t{{.Ports}}")):
        out = _docker(*args)
        if not out:
            continue
        for line in out.splitlines():
            name, _, ports = line.partition("\t")
            if needle in ports:
                return name.strip()
    return None


def find_container_volume(container: str) -> str | None:
    """Best-effort name of the first named volume mounted into ``container``."""
    out = _docker("inspect", container, "--format",
                  "{{range .Mounts}}{{.Name}}\n{{end}}")
    if not out:
        return None
    for line in out.splitlines():
        if line.strip():
            return line.strip()
    return None


def diagnose_poisoned_aof(
    port: int = 6379, *, container: str | None = None,
    volume: str | None = None,
) -> str | None:
    """Recovery hint when the backend on ``port`` shows the poison, else None.

    Best-effort and side-effect free: reads container logs only. Returns
    None whenever the diagnosis cannot be made (no Docker, no matching
    container, clean log) so callers can fall back to their generic error.
    """
    if container is None:
        container = find_container_for_port(port)
    if not container:
        return None
    # A short tail is deliberate: a crash loop re-logs the boot sequence on
    # every restart, so the signature sits within a few dozen lines. Callers
    # only invoke this AFTER a probe failure, so a signature found here
    # always describes a backend that is failing right now — a stale entry
    # in a long-running healthy container's history is never in the tail.
    log = _docker("logs", "--tail", "200", container)
    if not log or not is_poisoned_aof_log(log):
        return None
    if volume is None:
        volume = find_container_volume(container)
    return poisoned_aof_recovery_hint(
        container=container, volume=volume or "<the container's volume>")
