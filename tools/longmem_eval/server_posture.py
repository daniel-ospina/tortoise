"""Eval graph-server posture gate — active defrag (#2969, suggested fix 3).

MEASURED (issue #2969): the v2 ingest on the dedicated ``falkordb-eval``
container collapsed from ~25 min/question to >4 h/question, the client parked
in a FalkorDB socket read at 0% CPU with no error and no timeout. ``docker
logs`` showed the container in a **continuous active-defrag loop**, and
``CONFIG GET activedefrag`` answered ``yes`` on a container that had been up
for two days. ``CONFIG SET activedefrag no`` was applied as a mitigation and
throughput partially recovered. The client-side bounds (socket timeout +
heartbeat, #3068) make such a stall *reportable*; they do not make a
defragging server a usable measurement target.

This module turns that posture from an operator observation into a
**run-start gate** for the eval lane:

* ``CONFIG GET activedefrag`` is read once, before any question runs.
* Default policy ``require-off`` **refuses** to start against a server that
  is defragging, and prints the exact remediation. A run on a server in the
  known stall-correlated state is not a measurement, and the alternative
  measured in #2969 was hours of silent grind.
* ``TORTOISE_EVAL_ACTIVEDEFRAG=disable`` applies the incident's own
  mitigation (``CONFIG SET activedefrag no``) and **reads it back** — the
  SET's reply is not evidence. Intended for a container the operator owns
  (the dedicated eval container); it writes a *server-global* config value,
  which is why it is not the default.
* ``TORTOISE_EVAL_ACTIVEDEFRAG=allow`` proceeds anyway, loudly. Needed when
  defrag is a deliberate choice on a shared instance.
* An **unreadable** ``CONFIG`` is reported ``unknown`` and never as clean:
  a managed endpoint that forbids ``CONFIG`` cannot be asserted to be
  defrag-free, and the guard must not become a new hard dependency that
  breaks otherwise valid runs. "We could not look" is not "off". The one
  exception is ``disable``: that policy is an explicit request to APPLY the
  mitigation, so a server it cannot reach, a value it cannot read, and an
  uninterpretable reply are all states the operator asked to remove rather
  than to be waved through — an unopenable probe and an unreadable value are
  REFUSED, and an unrecognised value takes the write + read-back path.
* The guard never fails soft on a *readable* ``yes``: refusing is the point.
* A readable value that is neither ``yes`` nor ``no`` is ``unknown``, not
  ``off``: only ``no`` is a positive verdict, so an uninterpretable reply
  from a proxied or future server can never be reported as defrag-quiet.

Why the client does not simply disable defrag unconditionally: ``CONFIG SET``
is server-global. On a shared instance (the docker test lane, a hosted
deployment) silently flipping it would trade a stall risk for fragmentation
growth on a memory-capped server — a decision that belongs to whoever owns
that container, which is why the default is to refuse and say what to run.
"""
from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter before the imports below — a
# module-level 3.11+-only import would fail first (D9 shape).
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/longmem_eval/server_posture.py requires Python >= 3.12 "
        f"(got {sys.version_info[0]}.{sys.version_info[1]})"
    )

import os
from collections.abc import Callable
from typing import Any

#: The eval lane's active-defrag policy: ``require-off`` (default),
#: ``disable`` or ``allow``.
ENV_ACTIVEDEFRAG = "TORTOISE_EVAL_ACTIVEDEFRAG"

POLICY_REQUIRE_OFF = "require-off"
POLICY_DISABLE = "disable"
POLICY_ALLOW = "allow"
POLICIES = (POLICY_REQUIRE_OFF, POLICY_DISABLE, POLICY_ALLOW)
DEFAULT_ACTIVEDEFRAG_POLICY = POLICY_REQUIRE_OFF

#: The config field #2969 measured (``CONFIG GET activedefrag`` -> ``yes``).
ACTIVEDEFRAG_CONFIG = "activedefrag"

#: Statuses reported by :func:`enforce_server_posture`. Only ``off`` and
#: ``disabled`` are PASSES; ``unknown`` is explicitly NOT one.
STATUS_OFF = "off"
STATUS_DISABLED = "disabled"
STATUS_ALLOWED = "allowed"
STATUS_UNKNOWN = "unknown"


class ServerPostureError(RuntimeError):
    """The eval must not start against this server (#2969).

    Raised only when the posture is *known* to be bad (defrag on, policy
    ``require-off``), when the policy asked us to disable it and the server
    did not end up off, or when ``disable`` cannot even read the value it was
    asked to change. Otherwise an unreadable posture is reported, not raised —
    see the module docstring.
    """


def resolve_activedefrag_policy(explicit: str | None = None) -> str:
    """Resolve the policy (explicit > env > default), failing loud on a typo.

    A misspelt value must never silently fall back to a permission: the
    permissive policy has to be named deliberately.
    """
    raw = explicit if explicit is not None else os.environ.get(ENV_ACTIVEDEFRAG)
    if raw is None or not str(raw).strip():
        return DEFAULT_ACTIVEDEFRAG_POLICY
    token = str(raw).strip().lower()
    if token not in POLICIES:
        raise ValueError(
            f"{ENV_ACTIVEDEFRAG}={raw!r} is not a policy "
            f"(use one of: {', '.join(POLICIES)})")
    return token


def read_activedefrag(connection: Any) -> str | None:
    """The server's ``activedefrag`` value, or ``None`` if it cannot be read.

    ``CONFIG GET`` is issued as an explicit command rather than through
    redis-py's helper: FalkorDB's module overrides reject that form
    ("Unknown configuration field", measured — see ``tortoise/fork_safety.py``
    ``_current_loglevel``). RESP2 answers ``[name, value]``; RESP3 answers
    ``{name: value}``.
    """
    try:
        reply = connection.execute_command(
            "CONFIG", "GET", ACTIVEDEFRAG_CONFIG)
    except Exception:
        return None
    if isinstance(reply, dict):
        value = reply.get(ACTIVEDEFRAG_CONFIG)
    elif isinstance(reply, (list, tuple)) and len(reply) >= 2:
        value = reply[1]
    else:  # pragma: no cover - defensive; shape came back unrecognised
        return None
    if isinstance(value, bytes):  # pragma: no cover - decode_responses=True
        value = value.decode()
    if value is None:  # pragma: no cover - defensive
        return None
    return str(value).strip().lower()


def _connection_from_uri(uri: str) -> Any:
    """A bounded raw client for the posture probe — no SDK, no DDL.

    The eval's own socket bounds apply (``run_main`` presets
    ``TORTOISE_DB_SOCKET_TIMEOUT`` / ``_CONNECT_TIMEOUT`` for ``--db`` runs),
    so the probe itself cannot hang unbounded — the exact failure #2969 is
    about. The SDK is deliberately not used: constructing a
    ``FalkorProjection`` runs index DDL and a health recovery, and a posture
    probe must not touch the graph.
    """
    from falkordb import FalkorDB

    from tortoise.projection import (
        _bounded_server_socket_kwargs,
        resolve_db_endpoint,
    )

    endpoint = resolve_db_endpoint(uri)
    return FalkorDB(
        host=endpoint.host, port=endpoint.port,
        username=endpoint.username, password=endpoint.password,
        ssl=endpoint.ssl,
        **_bounded_server_socket_kwargs(),
    )


def _default_emit(line: str) -> None:
    print(line, file=sys.stderr)


def enforce_server_posture(
    *,
    db_uri: str | None = None,
    connection: Any = None,
    policy: str | None = None,
    emit: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Check (and under ``disable``, apply) the server defrag posture.

    Exactly one of ``db_uri`` / ``connection`` supplies the server; tests pass
    a fake ``connection``. Returns a dict with ``status`` (one of ``off`` /
    ``disabled`` / ``allowed`` / ``unknown``), ``policy``, ``detail`` (the raw
    value or the read error) and ``banner`` (the line to print).

    Raises :class:`ServerPostureError` when the posture is KNOWN bad under
    ``require-off``, when ``disable`` was requested and the read-back does
    not confirm ``no``, or when ``disable`` cannot read the value at all.
    """
    emit = _default_emit if emit is None else emit
    resolved = resolve_activedefrag_policy(policy)
    prefix = "[longmem_eval] graph-server posture (activedefrag)"

    if connection is None:
        if db_uri is None:
            raise ValueError("enforce_server_posture needs db_uri or connection")
        try:
            connection = _connection_from_uri(db_uri)
        except Exception as exc:
            detail = f"could not open a posture probe: {type(exc).__name__}: {exc}"
            # Same polarity as the unreadable-CONFIG branch below: an operator
            # who opted in to APPLYING the mitigation must not be handed a run
            # that proceeds without it.
            if resolved == POLICY_DISABLE:
                raise ServerPostureError(
                    f"{prefix}: {detail}, and "
                    f"{ENV_ACTIVEDEFRAG}={POLICY_DISABLE} needs a reachable "
                    f"server to disable defrag on. Refusing to start: the run "
                    f"would proceed with defrag possibly ON, which is the "
                    f"#2969 stall condition this policy was set to remove. "
                    f"Fix: point --db at a reachable server, or set "
                    f"{ENV_ACTIVEDEFRAG}={POLICY_ALLOW} to accept the risk "
                    f"explicitly.") from exc
            banner = (f"{prefix}: UNKNOWN — {detail}. The guard did not run; "
                      f"this run is NOT a defrag-clean assertion "
                      f"({ENV_ACTIVEDEFRAG}).")
            emit(banner)
            return {"status": STATUS_UNKNOWN, "policy": resolved,
                    "detail": detail, "banner": banner}

    value = read_activedefrag(connection)
    if value is None:
        detail = f"CONFIG GET {ACTIVEDEFRAG_CONFIG} could not be read"
        # ``disable`` is an explicit request to APPLY the mitigation, and it
        # cannot confirm its own precondition without a read. Waving the run
        # through here would hand the operator the exact state the opt-in
        # exists to remove (defrag possibly ON), so it fails CLOSED — the same
        # polarity the read-back branch below already has. ``require-off`` /
        # ``allow`` keep the documented soft UNKNOWN-and-proceed behaviour,
        # because there the guard's own non-availability must not break a run.
        if resolved == POLICY_DISABLE:
            raise ServerPostureError(
                f"{prefix}: {detail} and {ENV_ACTIVEDEFRAG}={POLICY_DISABLE} "
                f"needs to read the current value to disable it. Refusing to "
                f"start: the run would proceed with defrag possibly ON, which "
                f"is the #2969 stall condition this policy was set to remove. "
                f"Fix on the server: allow CONFIG, or set "
                f"{ENV_ACTIVEDEFRAG}={POLICY_ALLOW} to accept the risk "
                f"explicitly.")
        banner = (f"{prefix}: UNKNOWN — {detail} (the server refused CONFIG or "
                  f"the command is unavailable). Treating this run as NOT "
                  f"defrag-verified.")
        emit(banner)
        return {"status": STATUS_UNKNOWN, "policy": resolved,
                "detail": detail, "banner": banner}

    if value == "no":
        banner = f"{prefix}: {value!r} — measurement target is defrag-quiet"
        emit(banner)
        return {"status": STATUS_OFF, "policy": resolved,
                "detail": value, "banner": banner}

    if value != "yes" and resolved != POLICY_DISABLE:
        # READABLE but unrecognised (a truthy "1"/"true"/"on" from a future
        # or proxied server, an unexpected RESP3 shape). Only the literal
        # ``no`` is a PASS: classifying anything else as ``off`` would be the
        # fail-open this module's docstring rules out — "we could not interpret
        # it" is not "off" any more than "we could not look" is.
        #
        # ``disable`` is exempt from this verdict, not from the policy: the
        # value is not ``no``, so it is a state the operator explicitly asked
        # to remove, and guessing "unknown" there would skip the mitigation
        # they opted into. It falls through to the disable branch below, which
        # writes and read-back-verifies.
        detail = (f"CONFIG GET {ACTIVEDEFRAG_CONFIG} answered {value!r}, which "
                  f"is neither 'yes' nor 'no'")
        banner = (f"{prefix}: UNKNOWN — {detail}, so the server cannot be "
                  f"asserted defrag-quiet. Treating this run as NOT "
                  f"defrag-verified.")
        emit(banner)
        return {"status": STATUS_UNKNOWN, "policy": resolved,
                "detail": value, "banner": banner}

    # ── defrag is ON ──────────────────────────────────────────────────────
    remediation = (
        f"CONFIG SET {ACTIVEDEFRAG_CONFIG} no   "
        f"(or point --db at a container started with --activedefrag no)")

    if resolved == POLICY_ALLOW:
        banner = (f"{prefix}: {value!r} — ALLOWED by "
                  f"{ENV_ACTIVEDEFRAG}={POLICY_ALLOW}. #2969 measured a "
                  f"continuous defrag loop coinciding with an unbounded "
                  f"ingest stall; this run accepts that risk explicitly.")
        emit(banner)
        return {"status": STATUS_ALLOWED, "policy": resolved,
                "detail": value, "banner": banner}

    if resolved == POLICY_DISABLE:
        try:
            connection.execute_command(
                "CONFIG", "SET", ACTIVEDEFRAG_CONFIG, "no")
        except Exception as exc:
            raise ServerPostureError(
                f"{prefix}: {value!r} and {ENV_ACTIVEDEFRAG}={POLICY_DISABLE} "
                f"could not disable it ({type(exc).__name__}: {exc}). Fix on "
                f"the server: {remediation}") from exc
        # READ IT BACK: the SET's own reply is not evidence (the incident's
        # mitigation is only useful if it actually took effect).
        after = read_activedefrag(connection)
        if after != "no":
            raise ServerPostureError(
                f"{prefix}: CONFIG SET {ACTIVEDEFRAG_CONFIG} no was issued but "
                f"the read-back is {after!r} — the server did not end up "
                f"defrag-off, so #2969's stall condition may still be present. "
                f"Fix on the server: {remediation}")
        banner = (f"{prefix}: was {value!r} — DISABLED by "
                  f"{ENV_ACTIVEDEFRAG}={POLICY_DISABLE} and verified by "
                  f"read-back")
        emit(banner)
        return {"status": STATUS_DISABLED, "policy": resolved,
                "detail": value, "banner": banner}

    # default: require-off → refuse, and name the exact remediation.
    raise ServerPostureError(
        f"{prefix}: {ACTIVEDEFRAG_CONFIG}={value!r} on the measurement "
        f"server. #2969: a container in a continuous active-defrag loop "
        f"coincided with an ingest that stalled in a socket read for hours "
        f"(silently); #3068 bounds the wait but does not make this a valid "
        f"measurement target. Refusing to start. Remediation: {remediation}, "
        f"or set {ENV_ACTIVEDEFRAG}={POLICY_ALLOW} to accept it explicitly, "
        f"or {ENV_ACTIVEDEFRAG}={POLICY_DISABLE} to disable it from the "
        f"eval (server-global).")
