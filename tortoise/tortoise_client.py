#!/usr/bin/env python3
"""Tortoise client for skill wiring (S9).

Thin CLI + Python API wrapping TortoiseSDK for the §6.3 skill wiring contracts.
Skills call this module to read/write epistemic graph Points.

Usage:
  # Query prior research for a domain
  python3 operations/memory/tortoise_client.py query-prior-research --domain "competitor-analysis"

  # Query existing strategy Points
  python3 operations/memory/tortoise_client.py query-strategies

  # Query existing vision Points
  python3 operations/memory/tortoise_client.py query-visions --context "product"

  # Write strategy Points
  python3 operations/memory/tortoise_client.py write-points --kind strategy --points-json '[{"content":"..."}]'

  # Write a single claim Point
  python3 operations/memory/tortoise_client.py write-claim --content "X is Y" --context "competitor-analysis" --authored-by "research-skill"

  # Check if the memory system is available
  python3 operations/memory/tortoise_client.py status

Design (ponytail):
- One file, no dependencies outside stdlib + TortoiseSDK
- CLI via argparse subcommands, one function per §6.3 contract
- JSON in/out for agent tool consumption
- Graceful degradation when Tortoise not installed: prints a status word from the
  recorded vocabulary (`degraded` when the store cannot be reached,
  `unconfigured` for a set-up gap) and exits with a DISTINCT code.
  #3832 (D5) supersedes the original "+ exits 0" clause FOR THE CLI PROBE ONLY:
  the library still never raises and still returns the status payload, so
  script callers keep skipping cleanly; the process exit code is the reporting
  concern of the surface a human or an agent harness actually checks.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import threading
from pathlib import Path

# #3805: the recorded status vocabulary (roadmap §7 item 9) — imported from its
# ONE declaration, never re-minted. Same package, so this cannot fail where the
# module itself is importable.
from tortoise import status_vocabulary

# Ensure premise-labs is on path (sibling project)
_TORTOISE_ROOT = Path(__file__).resolve().parent
if str(_TORTOISE_ROOT) not in sys.path:
    sys.path.insert(0, str(_TORTOISE_ROOT))


# Errors that mean "Tortoise is unavailable", not a bug: missing optional DB
# backend (ImportError), unset/invalid TORTOISE_DB_URI (ValueError), an
# unset URI in production (RuntimeError — the SDK's P0 data-loss guard), and
# unreachable DB (builtin ConnectionError/OSError + the redis driver's OWN
# ConnectionError/TimeoutError, which are NOT subclasses of the builtins —
# a real unreachable docker:// URI surfaces as redis.exceptions.ConnectionError;
# + sqlite3.OperationalError for embedded lock contention / DB-state failures).
# These degrade gracefully — the client's contract is a status word from the
# recorded vocabulary (roadmap §7 item 9: `degraded` for an outage,
# `unconfigured` for a set-up gap), never a traceback (issue #343).
# #3832 (D5) supersedes the original "+ exit 0" clause FOR THE CLI PROBE ONLY
# — see the _STATUS_EXIT_CODES block below; the library still never raises.
_UNAVAILABLE_ERRORS = (
    ImportError,
    ValueError,
    RuntimeError,
    ConnectionError,
    OSError,
    sqlite3.OperationalError,
)
try:
    from redis.exceptions import ConnectionError as _RedisConnectionError
    from redis.exceptions import TimeoutError as _RedisTimeoutError

    _UNAVAILABLE_ERRORS += (_RedisConnectionError, _RedisTimeoutError)
except ImportError:
    pass  # redis not installed — SDK import will fail and degrade anyway

_UNAVAILABLE_MESSAGE = (
    "Tortoise SDK unavailable — TORTOISE_DB_URI not set or DB unreachable. "
    "Run `tortoise init` or set the env var."
)

# The never-configured arm gets its OWN prose (#3832 D5): the payload word is
# not the only thing that must stop collapsing — a set-up gap must not blame the
# service, and an outage must not read as a set-up gap. (This is the agent-facing
# `message` field; the _UNAVAILABLE_MESSAGE above is retained verbatim for the
# can't-reach-it arm and for every stderr warning.)
_NOT_CONFIGURED_MESSAGE = (
    "Tortoise is not set up — the SDK could not be constructed from the current "
    "configuration. Run `tortoise init` or set TORTOISE_DB_URI."
)

# ── #3832 (D5) + #3805: the machine-readable driver states ───────────────
#
# #343's crash fix made EVERY failure report one graceful value, so a client
# that was never set up (the SDK cannot be constructed from the current
# configuration) was indistinguishable from one whose daemon is down. D5
# removes that collapse in the PAYLOAD while leaving the contract alone: the
# driver still never raises, and a DOWN / unreachable daemon keeps its own
# distinct word — only the never-configured case gains one.
#
# #3805 moves the WORDS onto the ONE recorded vocabulary (roadmap §7 item 9,
# `available | empty | degraded | unconfigured`), so BOTH client surfaces in
# this repo — this S9 skill-wiring client and the thin `tortoise-client` probe —
# speak the same four terms instead of two different sets. The exit codes were
# already #3832's and are UNCHANGED; only the words moved. The terms are
# imported, never re-declared, so this file cannot drift from the vocabulary.
STATUS_AVAILABLE = status_vocabulary.STATUS_AVAILABLE
STATUS_EMPTY = status_vocabulary.STATUS_EMPTY
STATUS_DEGRADED = status_vocabulary.STATUS_DEGRADED
STATUS_UNCONFIGURED = status_vocabulary.STATUS_UNCONFIGURED

# The status word of the most recent degradation in THIS process (None when the
# last command was clean). Set by _log_unavailable; reset by main(). Lets the
# CLI report the degraded exit code without probing a second time — a second
# probe would re-run the construction failure and emit a SECOND stderr warning
# for a single command, which an agent parsing one JSON line per run reads as
# two failures.
_LAST_DEGRADATION: str | None = None


# ── #1562: one SDK per process (module-level lazy singleton) ────────────
#
# Pre-#1562, _get_sdk() built a FRESH TortoiseSDK() on every call — the CLI's
# main() dispatcher calls it once per subcommand, so a multi-command session
# created a new SDK per command and dropped the previous one. With #1475's
# close-on-GC finalizer, the collected SDK could shut down the shared embedded
# redislite server (its last-client check saw count==1 — the next command's
# SDK had not connected yet) while the fresh SDK of the NEXT command was
# connecting — a race under load that surfaced as ConnectionError →
# _UNAVAILABLE_ERRORS → {"error": "tortoise_unavailable"} mid-session (flaked
# test_cli_write_points_healthy_roundtrip with KeyError: 'confidence').
#
# The cache pins ONE SDK for the process lifetime: no per-command SDK is ever
# collected, so the embedded server never dies out from under a later command.
# A lock serializes check+construct so concurrent contract calls can never both
# miss the cache and build two SDKs (the loser would be unreferenced → GC'd →
# the close-on-GC finalizer could shut the server out from under the winner).
_SDK_CACHE: TortoiseSDK | None = None  # noqa: F821
_SDK_LOCK = threading.RLock()


def _same_db_path(cached: str | None, current: str | None) -> bool:
    """Path equality with the hosted_api._anchor_usable normalization
    (:memory: carve-out + abspath compare — #1502). O(1), no probe.
    """
    if cached is None or current is None:
        return False
    try:
        return (str(cached) == str(current)) or (
            str(cached) != ":memory:" and os.path.abspath(cached) == os.path.abspath(current)
        )
    except (TypeError, ValueError):
        return False


def _sdk_matches_env(sdk) -> bool:
    """True when the cached SDK is still bound to the CURRENT env DB.

    The cache must never serve a stale DB: TORTOISE_DB_URI/PATH can change
    between calls (tests swap temp DBs per test; long-running processes can
    re-point). Full drift check, mirroring hosted_api._anchor_usable (#1502):
    mode (URI vs embedded) AND the bound URI/path must match the current
    env. URI wins over PATH (SDK precedence: TortoiseSDK reads
    TORTOISE_DB_URI first). O(1) attribute/path comparison, no probe.
    """
    uri = os.environ.get("TORTOISE_DB_URI")
    if uri:
        return getattr(sdk, "_db_uri", None) == uri
    if getattr(sdk, "_db_uri", None) is not None:
        return False  # URI-mode cache is stale once the URI env is gone
    path = os.environ.get("TORTOISE_DB_PATH")
    if path:
        # Embedded mode: the bound path must match the current PATH env.
        return _same_db_path(getattr(sdk, "_db_path", None), path)
    # Neither env var set → embedded default-path mode: compare against the
    # CURRENT default (HOME-dependent — resolve_db_path may have moved).
    from tortoise.config import resolve_db_path

    try:
        current = resolve_db_path()
    except Exception:
        return True  # resolution failure → fall back to mode-only match
    return _same_db_path(getattr(sdk, "_db_path", None), current)


def _sdk_usable(sdk) -> bool:
    """True when the cached SDK's DB is still alive (self-heal probe).

    URI mode: never self-heal — an unreachable REMOTE DB is a persistent
    config/infra error; rebuilding cannot help and must not mask it (the
    contract's _UNAVAILABLE_ERRORS path handles it). Embedded mode: probe
    the projection — a dead server (external kill, reaper, long idle) is
    evicted and rebuilt once by _get_sdk. A never-connected SDK (_proj is
    None) is fine — first use connects (mirrors hosted_api's
    `elif anchor._proj is None: anchor._get_proj()` keepalive).
    """
    if getattr(sdk, "_db_uri", None) is not None:
        return True
    proj = getattr(sdk, "_proj", None)
    if proj is None:
        return True
    try:
        return proj._probe_ok()
    except Exception:
        return False


def _get_sdk() -> "TortoiseSDK":  # noqa: F821, UP037
    """Lazy-import TortoiseSDK, cached module-level (#1562). Returns None
    if unavailable.

    The FIRST call constructs and caches one SDK (env-driven); later calls
    reuse it across commands. Self-heal: a cached SDK whose embedded server
    died (external kill, reaper, long idle) is evicted and rebuilt ONCE; a
    rebuild failure (or a first-call failure) returns None — the
    graceful-degradation contract is unchanged.
    """
    global _SDK_CACHE
    with _SDK_LOCK:
        if _SDK_CACHE is not None:
            if _sdk_matches_env(_SDK_CACHE) and _sdk_usable(_SDK_CACHE):
                return _SDK_CACHE
            _close_cached_sdk()  # stale env binding or dead server — evict
        try:
            from tortoise.sdk import TortoiseSDK

            _SDK_CACHE = TortoiseSDK()  # uses TORTOISE_DB_URI from env
            return _SDK_CACHE
        except _UNAVAILABLE_ERRORS as e:
            # Construction failed from the current configuration — the
            # never-set-up arm (#3832 D5), not an outage.
            _log_unavailable(reason=e, status=STATUS_UNCONFIGURED)
            _SDK_CACHE = None
            return None


def _close_cached_sdk() -> None:
    """Close and drop the cached SDK (tests, long-running processes).

    Idempotent; never raises. Embedded close() shuts down the SDK's server
    (SAVE); URI-mode close disconnects. The next _get_sdk() rebuilds from
    the current env.
    """
    global _SDK_CACHE
    with _SDK_LOCK:
        sdk = _SDK_CACHE
        _SDK_CACHE = None
        if sdk is not None:
            try:  # noqa: SIM105
                sdk.close()
            except Exception:
                pass


def _check_available() -> bool:
    """True iff Tortoise operations would work.

    A real first-use probe: status() exercises summarize_structure, so an
    unreachable DB reports unavailable — a construction-only check would
    return True for the #343 failure shape (the SDK constructor is lazy
    and succeeds without a reachable DB)."""
    return status()["available"]


# ── §6.3 Contract: queryPriorResearch ───────────────────


def query_prior_research(domain: str) -> list[dict]:
    """Query epistemic graph for existing claims about a domain.

    Searches Points by kind (fuzzy match). Returns list of dicts:
    {id, content, pointKind, confidence, status}.
    """
    if not domain:
        # Empty domain would hit the SDK's falsy-kind fallback and dump
        # the WHOLE graph — never serve that (agent-facing contract, #343).
        return []
    sdk = _get_sdk()
    if sdk is None:
        return []

    # Query by kind — domain is used as a keyword search in content.
    # Projection init is lazy — a missing/unreachable DB surfaces here,
    # not at construction; degrade instead of crashing (#343).
    try:
        return sdk.query(kind=domain)
    except _UNAVAILABLE_ERRORS as e:
        _log_unavailable(reason=e)
        return []


# ── §6.3 Contract: writeStrategyPoints ──────────────────


def write_strategy_points(points: list[dict], kind: str = "strategy") -> list[dict]:
    """Write Points to the epistemic graph.

    Each point dict: {content (required), context (optional), authoredBy (optional), confidence (float optional)}.
    kind: pointKind to use (default "strategy", use "vision" for vision Points).
    Returns list of created point dicts.
    """
    sdk = _get_sdk()
    if sdk is None:
        return []

    # Client-side input validation happens BEFORE the SDK try/except so an
    # input error (e.g. bad confidence) surfaces loudly instead of being
    # masked as "tortoise unavailable" (#343).
    prepared = [(_prepare_point(p, kind)) for p in points]
    try:
        created: list[dict] = []
        for point_kind_, content, props in prepared:
            created.append(_create_with_retry(sdk, point_kind_, content, **props))
        return created
    except _UNAVAILABLE_ERRORS as e:
        _log_unavailable(reason=e)
        return []


# ── §6.3 Contract: queryExistingStrategies ──────────────


def query_existing_strategies() -> list[dict]:
    """Query epistemic graph for current strategy Points."""
    sdk = _get_sdk()
    if sdk is None:
        return []
    try:
        return sdk.query(kind="strategy")
    except _UNAVAILABLE_ERRORS as e:
        _log_unavailable(reason=e)
        return []


# ── Vision queries (from E2E-10 pattern) ───────────────


def query_existing_visions(point_kind: str | None = None) -> list[dict]:
    """Query epistemic graph for existing vision Points."""
    sdk = _get_sdk()
    if sdk is None:
        return []
    try:
        if point_kind:
            return sdk.query(kind=point_kind)
        return sdk.query(kind="vision")
    except _UNAVAILABLE_ERRORS as e:
        _log_unavailable(reason=e)
        return []


# ── Generic claim writing ──────────────────────────────


def write_claim(
    content: str, kind: str = "statement", *, authored_by: str = "", confidence: float | None = None
) -> dict:
    """Write a single claim Point to the epistemic graph."""
    sdk = _get_sdk()
    if sdk is None:
        # Never set up: the SDK cannot be constructed from the current
        # configuration — a set-up gap, NOT an outage (#3832 D5). Keeping this
        # distinct from the down-daemon value is the whole point of the unit.
        return {"error": STATUS_UNCONFIGURED, "id": "", "written": False}
    props: dict = {}
    if authored_by:
        props["authoredBy"] = authored_by
    if confidence is not None:
        props["confidence"] = float(confidence)
    try:
        return sdk.create_point(kind, content, **props)
    except _UNAVAILABLE_ERRORS as e:
        # Constructed, but the DB is unreachable — a down daemon still reports
        # the OUTAGE term (`degraded`), distinct from a set-up gap (#3832).
        _log_unavailable(reason=e)
        return {"error": STATUS_DEGRADED, "id": "", "written": False}


# ── Status ──────────────────────────────────────────────


def status() -> dict:
    """Report whether Tortoise is available and basic graph stats.

    `status` carries the machine-readable driver state on the recorded
    vocabulary (#3805 / roadmap §7 item 9): `available` (reached) |
    `degraded` (constructed, can't reach it) | `unconfigured` (never set up).
    Never raises.
    """
    sdk = _get_sdk()
    if sdk is None:
        return {
            "available": False,
            "status": STATUS_UNCONFIGURED,
            "message": _NOT_CONFIGURED_MESSAGE,
        }
    try:
        chain = sdk.summarize_structure()
    except _UNAVAILABLE_ERRORS as e:
        # SDK constructed but the DB is unreachable on first use — report
        # unavailability instead of masking it as available (#343). This is the
        # CAN'T-REACH-IT arm: it reports `degraded` (#3832).
        _log_unavailable(reason=e)
        return {
            "available": False,
            "status": STATUS_DEGRADED,
            "message": _UNAVAILABLE_MESSAGE,
        }
    except Exception:
        chain = {"error": "query failed"}
    return {
        "available": True,
        "status": STATUS_AVAILABLE,
        "db_uri": os.environ.get("TORTOISE_DB_URI")
        or os.environ.get("TORTOISE_DB_PATH", "not set"),
        "chain_status": chain,
    }


# ── Helpers ─────────────────────────────────────────────


def _prepare_point(p: dict, kind: str) -> tuple[str, str, dict]:
    """Extract (kind, content, props) from a write-point dict.

    Client-side input normalization — intentionally OUTSIDE the SDK
    degradation try/except so bad inputs raise loudly instead of being
    masked as "tortoise unavailable" (#343)."""
    content = p["content"]
    props = {}
    if p.get("authoredBy"):
        props["authoredBy"] = p["authoredBy"]
    if p.get("confidence") is not None:
        props["confidence"] = float(p["confidence"])
    return kind, content, props


def _create_with_retry(sdk, kind: str, content: str, **props) -> dict:
    """Create point with retry for concurrent-write lock contention."""
    # ponytail: 3 attempts; lock-only retry with 100ms/200ms backoff
    # (attempt 3 re-raises — no 400ms sleep).
    # FalkorDBLite uses SQLite — file lock under concurrent processes.
    import time

    for attempt in range(3):
        try:
            return sdk.create_point(kind, content, **props)
        except Exception as e:
            if "locked" in str(e).lower() and attempt < 2:
                time.sleep(0.1 * (2**attempt))
            else:
                raise
    raise RuntimeError("unreachable")


def _log_unavailable(reason: Exception, status: str = STATUS_DEGRADED) -> None:
    """Emit the graceful-degradation warning and record its state.

    The stderr JSON is unchanged (single line, agent-parseable). `status`
    records WHICH degraded state this was (#3832 D5) so the CLI can report the
    matching exit code without a second probe that would log twice.
    """
    global _LAST_DEGRADATION
    _LAST_DEGRADATION = status
    warning = (
        "tortoise unavailable — TORTOISE_DB_URI not set or DB unreachable. "
        "Run `tortoise init` or set the env var "
        f"({type(reason).__name__}: {reason})"
    )
    print(json.dumps({"warning": warning, "status": "noop"}), file=sys.stderr)


def _to_json(data) -> str:
    """Serialize to JSON for agent consumption."""
    return json.dumps(data, indent=2, default=str)


# ── CLI exit codes (#3832 / D5) ─────────────────────────
#
# This SUPERSEDES #526's "exit 0 on degradation" clause FOR THE CLI PROBE
# ONLY. The library contract is unchanged: probes still never raise and still
# return the status payload, so script callers keep skipping cleanly. The exit
# code is the reporting concern of the surface a human or an agent harness
# actually checks, so a degraded probe must stop looking like success there.
#   0 = available · 3 = configured but can't reach it · 4 = never set up (#3832)
#   1 = a query/input that genuinely fails (kept, never widened)
#   2 = argparse usage errors (argparse owns it)
_EXIT_OK = 0
_EXIT_QUERY_FAILED = 1
_EXIT_UNAVAILABLE = 3
_EXIT_NOT_CONFIGURED = 4

_STATUS_EXIT_CODES = {
    STATUS_AVAILABLE: _EXIT_OK,
    STATUS_EMPTY: _EXIT_OK,
    STATUS_DEGRADED: _EXIT_UNAVAILABLE,
    STATUS_UNCONFIGURED: _EXIT_NOT_CONFIGURED,
}


def _exit_code(word: str | None) -> int:
    """Map the driver's machine-readable state to the CLI exit code (#3832).

    An unknown word degrades to the `degraded` code — fail loud, never silently
    report success for a state we do not recognise.
    """
    return _STATUS_EXIT_CODES.get(word, _EXIT_UNAVAILABLE)


# ── CLI ─────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Tortoise client for skill wiring")
    sub = parser.add_subparsers(dest="command")

    # query-prior-research
    qpr = sub.add_parser(
        "query-prior-research", help="Query epistemic graph for prior claims about a domain"
    )
    qpr.add_argument("--domain", required=True, help="Domain to query (e.g. 'competitor-analysis')")

    # query-strategies
    sub.add_parser("query-strategies", help="Query existing strategy Points")

    # query-visions
    qv = sub.add_parser("query-visions", help="Query existing vision Points")
    qv.add_argument("--point-kind", default=None, help="Optional pointKind filter")

    # write-points
    wp = sub.add_parser("write-points", help="Write Points to epistemic graph")
    wp.add_argument("--kind", required=True, help="Point kind (strategy, vision, statement, etc.)")
    wp.add_argument(
        "--points-json", required=True, help="JSON array of point dicts with 'content' key"
    )

    # write-claim
    wc = sub.add_parser("write-claim", help="Write a single claim Point")
    wc.add_argument("--content", required=True, help="Claim content")
    wc.add_argument("--kind", default="statement", help="Point kind")
    wc.add_argument("--authored-by", default="", help="Who authored this claim")
    wc.add_argument("--confidence", type=str, default=None, help="Confidence 0.0-1.0")

    # status
    sub.add_parser("status", help="Check Tortoise availability and graph stats")

    return parser


def main() -> int:
    global _LAST_DEGRADATION
    _LAST_DEGRADATION = None  # per-invocation: a prior command's state must not leak
    parser = _build_parser()
    args = parser.parse_args()

    if args.command == "query-prior-research":
        results = query_prior_research(args.domain)
        print(_to_json({"domain": args.domain, "count": len(results), "results": results}))

    elif args.command == "query-strategies":
        results = query_existing_strategies()
        print(_to_json({"count": len(results), "results": results}))

    elif args.command == "query-visions":
        results = query_existing_visions(point_kind=args.point_kind)
        print(_to_json({"count": len(results), "results": results}))

    elif args.command == "write-points":
        try:
            points = json.loads(args.points_json)
        except json.JSONDecodeError as e:
            print(_to_json({"error": "invalid-json", "detail": str(e)}), file=sys.stderr)
            sys.exit(_EXIT_QUERY_FAILED)
        try:
            results = write_strategy_points(points, kind=args.kind)
        except (KeyError, TypeError, ValueError) as e:
            # Input errors (bad confidence, missing content) surface as JSON
            # + exit 1 — never a raw traceback (agent-facing contract, #343).
            print(_to_json({"error": "invalid-input", "detail": str(e)}), file=sys.stderr)
            sys.exit(_EXIT_QUERY_FAILED)
        print(_to_json({"written": len(results), "results": results}))

    elif args.command == "write-claim":
        try:
            result = write_claim(
                args.content,
                kind=args.kind,
                authored_by=args.authored_by,
                confidence=args.confidence,
            )
        except (KeyError, TypeError, ValueError) as e:
            print(_to_json({"error": "invalid-input", "detail": str(e)}), file=sys.stderr)
            sys.exit(_EXIT_QUERY_FAILED)
        print(_to_json(result))

    elif args.command == "status":
        result = status()
        print(_to_json(result))
        return _exit_code(result.get("status"))

    else:
        parser.print_help()
        sys.exit(_EXIT_QUERY_FAILED)

    # The command logs any degradation it hit (exactly once). A second probe
    # here would emit a SECOND stderr warning for a single command AND could
    # fail a command that already succeeded (its payload says written: true
    # while the exit code says unavailable) — so the record is the only source.
    # No degradation logged means the command completed against a reachable
    # graph: `available`. (The `status` command returned above with its own
    # probe.)
    return _exit_code(_LAST_DEGRADATION or STATUS_AVAILABLE)


if __name__ == "__main__":
    sys.exit(main())
