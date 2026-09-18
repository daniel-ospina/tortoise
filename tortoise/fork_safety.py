"""Embedded-lane fork safety against the macOS timezone rwlock (#3845, part 2).

THE PRODUCER — measured on this machine, not inferred
----------------------------------------------------
The leak needs two things to coincide; both are inside the **redis-server**
process, and neither is Python.

1. **A module fork.** ``GRAPH.COPY`` is executed by FalkorDB's dedicated cron
   thread (``Cron_Run`` -> ``_Graph_Copy`` -> ``RedisModule_Fork``).  Verified
   on a live embedded daemon by ``sample``-ing the parent: the ``Cron_Run``
   thread runs both ``_Graph_Copy`` (the fork) and the follow-on
   ``LoadGraphFromFile`` install.  The ``redis-module-fork`` child that later
   wedges carries that exact stack.

2. **A thread holding macOS's process-global timezone rwlock at that instant.**
   The dominant holder is redis-server's *own main thread*: every event-loop
   wakeup runs ``aeProcessEvents -> afterSleep -> localtime_r``.  In the
   bundled redis-server 8.6.2 binary the call is **unconditional** — the
   disassembly of ``_afterSleep`` is straight-line from ``_gettimeofday`` to
   ``bl _localtime_r`` at ``afterSleep+0x1b4`` (only a usually-true pointer
   guard precedes it).  Sampling a daemon while copies were in flight showed
   the main thread in ``afterSleep -> localtime_r`` in 30 of its 34 non-idle
   samples.  ``localtime_r`` -> ``strftime`` -> ``tzsetwall_basic`` takes a
   READ lock on the timezone database and (on this libsystem) re-reads
   ``/etc/localtime`` **inside** the hold — so the window is file-I/O wide, not
   a few instructions.  Logging is the other holder class:
   ``serverLogRaw -> strftime_l -> tzsetwall_basic`` runs on any thread that
   logs (measured on the cron thread too, in ``LoadGraphFromFile``'s NOTICE).

If any of those threads holds the rwlock when ``fork()`` runs, the module
child inherits it **held** and blocks forever in its own first ``RM_Log``.
``server.child_pid`` is then never cleared, and every later ``redisFork`` fails
``EEXIST`` ("could not fork") for that daemon's whole life.

Why it is intermittent: the fork is a single instant, so a copy either lands
inside a holder's window or it does not.  Copies are rare in production and
common in drills/CI — hence a leak per few dozen copies rather than per copy.

This is NOT our logging (settled by inspection, not by argument)
----------------------------------------------------------------
``strftime``/``localtime`` in a *Python* formatter takes the tz rwlock of the
**Python** process.  The wedge is in a different process: redislite ``exec``s
``.../redislite/bin/redis-server`` (its parent is ``launchd`` after
``daemonize``; ``ps -o comm`` shows the redis-server binary, not python, and
its ``redis-module-fork`` children are its own children).  A lock is
per-process, so nothing Python does can hold the lock the module child blocks
on.  Making our log formatter use ``time.gmtime`` — the tempting "fork-safe
time source" fix — changes nothing observable about this leak.

WHAT THIS MODULE DOES ABOUT IT
------------------------------
The producer is third-party (redis-server ``afterSleep`` on one side,
FalkorDB ``Cron_Run`` on the other) and neither is ours to change, so the
mitigation is the one we own: **keep the fork child's local-time formatting off
the fork-concurrent path.**

``serverLogRaw`` (redis 8.6.2) gates at its very first instruction::

    level &= 0xff;
    if (level < server.verbosity) return;   # -> the epilogue, before fopen()
    fp = ... fopen(logfile) ...             #   and before strftime()
    ... strftime(buf, sizeof(buf), "%d %b %Y %H:%M:%S", ...) ...

Verified against the bundled binary: the ``b.lt`` out of the verbosity compare
targets the function epilogue at ``0x1000427ec``, which precedes both the
``fopen`` stub and the ``strftime`` stub on every path.  So when the embedded
daemon runs at ``warning``, the module child's NOTICE ``RM_Log`` — the call
that blocks — returns before it can touch the timezone lock, and the child
completes.  ``GRAPH.COPY`` keeps working; the parent's NOTICE logs are the
price (the *warning*-class wedge diagnostics — "Can't fork for module: File
exists", "There is a module fork child. Killing it!" — are unaffected, and
#3845's detection reads process titles, not the log).

Residual limits (declared, not silently carried)
------------------------------------------------
- This removes the *child's* dependency on the lock; it does not remove the
  producer.  A module-fork child that logs at ``warning`` or above would hang
  the same way.  Only an upstream fix (FalkorDB: do not fork from the cron
  thread / make the child's log fork-safe; Redis: drop ``localtime_r`` from
  ``afterSleep``) removes the condition.
- ``CONFIG SET loglevel`` by anyone else re-arms it; the embedded choke point
  re-asserts the level on every client construction for that reason.
- The ``:memory:`` embedded server forks the same way and is covered too.
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

#: ``server.verbosity`` at which ``serverLogRaw`` returns BEFORE ``strftime``,
#: which is what keeps the forked child off the tz rwlock.  Redis verbosity
#: order is debug < verbose < notice < warning < nothing, so this is the
#: lowest level that filters a NOTICE.
FORK_SAFE_LOGLEVEL = "warning"

#: Escape hatch for an operator who needs the embedded daemon's NOTICE logs and
#: accepts that every GRAPH.COPY then races the tz rwlock again (i.e. accepts
#: #3845).  Anything below ``warning`` is logged loudly at use.
ENV_LOGLEVEL = "TORTOISE_EMBEDDED_LOGLEVEL"

_LEVELS = ("debug", "verbose", "notice", "warning", "nothing")
_UNSAFE = frozenset(("debug", "verbose", "notice"))


def embedded_loglevel() -> str:
    """The verbosity the embedded daemon must run at (issue #3845).

    Defaults to :data:`FORK_SAFE_LOGLEVEL`.  ``TORTOISE_EMBEDDED_LOGLEVEL``
    overrides it (validated against Redis's own level names); a value that
    re-arms the leak is accepted but warned about, never silent.
    """
    chosen = os.environ.get(ENV_LOGLEVEL, "").strip().lower()
    if not chosen:
        return FORK_SAFE_LOGLEVEL
    if chosen not in _LEVELS:
        logger.warning(
            "#3845: %s=%r is not a Redis loglevel (%s) — keeping the "
            "fork-safe default %r instead",
            ENV_LOGLEVEL, chosen, "/".join(_LEVELS), FORK_SAFE_LOGLEVEL,
        )
        return FORK_SAFE_LOGLEVEL
    if chosen in _UNSAFE:
        logger.warning(
            "#3845: %s=%s re-arms the embedded module-fork leak — a "
            "GRAPH.COPY child can inherit macOS's timezone rwlock held and "
            "hang forever. Fork-safe level is %r.",
            ENV_LOGLEVEL, chosen, FORK_SAFE_LOGLEVEL,
        )
    return chosen


def fork_safe_serverconfig(base: dict | None = None) -> dict:
    """Merge the fork-safe verbosity into an embedded server's config.

    Used at the embedded choke point (``tortoise.FalkorDB``) so every embedded
    daemon — including ``FalkorProjection``'s — starts at a level where the
    GRAPH.COPY child's ``RM_Log`` cannot reach ``strftime``.
    """
    want = embedded_loglevel()
    config = dict(base or {})
    asked = config.get("loglevel")
    if asked is not None and str(asked).strip().lower() != want:
        logger.warning(
            "#3845: embedded serverconfig asked for loglevel=%s; using %s so "
            "the module-fork child cannot block on the timezone rwlock",
            asked, want,
        )
    config["loglevel"] = want
    return config


def _current_loglevel(client) -> str | None:
    """Effective ``loglevel`` of a live daemon, or None if it cannot be read.

    ``CONFIG GET`` is issued as an explicit command rather than through
    redis-py's ``config_get`` helper: the FalkorDB module's command overrides
    reject that helper's form with "Unknown configuration field" (measured).
    RESP2 answers ``[name, value]``; RESP3 answers ``{name: value}``.
    """
    reply = client.execute_command("CONFIG", "GET", "loglevel")
    if isinstance(reply, dict):
        value = reply.get("loglevel")
    elif isinstance(reply, (list, tuple)) and len(reply) >= 2:
        value = reply[1]
    else:  # pragma: no cover - defensive; shape came back unrecognised
        return None
    if isinstance(value, bytes):  # pragma: no cover - decode_responses=True
        value = value.decode()
    return str(value).strip().lower() if value is not None else None


def enforce_embedded_fork_safety(client) -> bool:
    """Assert the fork-safe verbosity on a LIVE embedded daemon (#3845).

    ``serverconfig`` is only applied at a daemon's COLD start: redislite reuses
    a running daemon from its ``.settings`` registry without re-reading the
    config.  So a daemon started before this fix — or by an older client in the
    same socket dir — keeps ``notice`` and stays exposed.  Re-assert the level
    on every constructed client; the write is idempotent and one round trip.

    Fails SOFT (returns False, warns) — never raises.  A server whose level we
    could not confirm is the one that can still wedge, so it is reported at
    ``warning`` rather than swallowed.
    """
    want = embedded_loglevel()
    try:
        current = _current_loglevel(client)
        if current == want:
            return True
        client.execute_command("CONFIG", "SET", "loglevel", want)
        logger.debug(
            "#3845: embedded loglevel %s -> %s (fork-safe)", current, want)
        return True
    except Exception as exc:
        logger.warning(
            "#3845: could not enforce fork-safe embedded loglevel %r (%s: %s)"
            " — a GRAPH.COPY on this daemon may hang its module-fork child",
            want, type(exc).__name__, exc,
        )
        return False
