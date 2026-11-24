#!/usr/bin/env python
"""Rehearse the capture-spool drain against a LOCAL server — never production.

WHY THIS EXISTS
---------------
`#14232` (ACK/async split, landed in `#14261`) is the gate that makes the parked
spool drainable. The post-gate drain is a one-shot operation against the user's
ONLY copy of 265–516 transcripts, so the path it will take has to be a KNOWN
path, not a hope. This tool is that rehearsal: it copies the spool, brings up a
local `hosted_api` against a private `test_lane.py` FalkorDB, runs the REAL
`tortoise session drain` against it, and records every observable.

⛔ FOUR RECORDED DECISIONS THIS TOOL OBEYS, AND THE CODE THAT ENFORCES EACH
--------------------------------------------------------------------------
1. **Never delete a spooled transcript.** The source spool (`--spool-src`,
   default `~/.tortoise/capture-spool`) is READ ONLY here: it is copied, never
   moved, never drained, never `remove_spool_entry`-ed. `_source_fingerprint`
   is taken before and after and a mismatch fails the run (exit 1) — the
   acceptance criterion is that the original is byte-identical afterwards.
   (`capture_spool`'s own rule: the spool is the only copy.)
2. **Never point the drain at production.** `_gate_uri` refuses any
   `TORTOISE_DB_URI` that is not loopback, refuses `api.premiselabs.co`, and
   refuses a graph name containing `live-v5`. The drain's `TORTOISE_API_URL` is
   built here as `http://127.0.0.1:<port>` and never read from the ambient
   environment.
3. **Never raise the 10 s wait bound** (`#3834`). Nothing in this tool touches
   `_TRANSPORT_WAIT_BOUND_S`; `--mode pre-14261` reproduces the OLD defect by
   disabling the ACK *join window* (`_capture_ack_ceiling_s`), which is the
   thing `#14261` added, precisely so the runbook can show what a 504 at the
   bound looks like without pretending the bound moved.
4. **No capture caps** (`#4010`). No cap is introduced; `--mode backpressure`
   LOWERS `TORTOISE_CAPTURE_IN_FLIGHT` on the local server on purpose, to
   reproduce the `capture_capacity_saturated` refusal that parked the spool
   (`#12992`), so the runbook can name that refusal when it sees it.

MODES
-----
| mode | server | spools | what it answers |
|---|---|---|---|
| `head-keyless` | HEAD, no extraction provider (refuses to start if one is configured) | one copy, 2 passes | the drain as a keyless box runs it: ACK path, store-only extraction |
| `head-mock` | HEAD + `TORTOISE_SESSION_LLM_MOCK=1` | one copy, 2 passes | the same, with the extraction pipeline actually running (offline, zero spend) |
| `pre-14261` | ACK join window disabled + a 12 s extraction | 3 entries | what the 504 looked like, and what `stage=` says now |
| `backpressure` | `TORTOISE_CAPTURE_IN_FLIGHT=1`, slow mock extraction | ONE copy, ONE serial drainer | the `capture_capacity_saturated` 429, and how the drain classifies it |

⛔ `backpressure` is ONE copy drained by ONE serial client — it does not run two
concurrent drainers, and no run in the evidence directory does. The ceiling it
reproduces is a per-capture ADMISSION slot that is held for the WHOLE extraction
(`_release_session`: "a handed-off slot waits for its worker"), so the next POST
of the SAME drain is refused while the previous capture is still extracting.
That is evidence about the CEILING and about one drainer's pace, not about two
concurrent clients: the `ensure ONE drainer` remedy is right for a different
reason, and the refusal would arrive for a single client too.

USAGE
-----
    # 1. a private FalkorDB (never the shared 6379 instance)
    eval "$(uv run python tools/test_lane.py uri)"

    # 2. the rehearsal
    uv run python tools/spool_drain_rehearsal.py run --mode head-mock

    # everything at once (the runbook's order)
    uv run python tools/spool_drain_rehearsal.py all

Exit codes: 0 = the run completed and every invariant held; 1 = an invariant
broke (source spool changed, a non-loopback target, the server never came up);
2 = usage.

⚠️ The report is evidence, not a verdict on `#14232`: `head-keyless` and
`head-mock` grade the DRAIN, and the grader for the ACK's own latency target
stays `tools/acceptance_14232_drain.py`.
"""
from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter before the imports below — a module-level
# 3.12+-only construct would otherwise fail first, and it would fail with a
# traceback instead of this message. The gate that enforces this shape is
# tests/test_entry_point_python_guard.py, not a style rule.
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/spool_drain_rehearsal.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/spool_drain_rehearsal.py`"
    )

import argparse
import contextlib
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SPOOL = Path.home() / ".tortoise" / "capture-spool"
#: The rehearsal's four modes, in the order the evidence documents them. One
#: tuple so the writer, the merger and the `summary` reader cannot disagree about
#: which modes a complete artefact set contains.
MODES = ("head-keyless", "head-mock", "pre-14261", "backpressure")
ORG_ID = "00000000-0000-4000-8000-0000000000aa"
ACTOR_ID = "00000000-0000-4000-8000-000000000142"
#: Extraction latency injected in `pre-14261`, chosen to exceed the transport
#: bound (10 s). It is a REHEARSAL artefact and is named as one in the report.
PRE_BOUND_EXTRACTION_DELAY_S = 12.0
#: A smaller delay for `backpressure`: long enough that the NEXT serial POST of
#: the same drain arrives while the previous capture's extraction still holds the
#: 1-slot admission ceiling. It is a per-capture overlap, not two clients.
BACKPRESSURE_EXTRACTION_DELAY_S = 2.0


# ── invariants ───────────────────────────────────────────────────────────────

def _source_fingerprint(root: Path) -> dict:
    """Count + per-file sha256 + one aggregate digest of a spool tree.

    The aggregate is over `sha256sum` lines in sorted path order, so it changes
    if any byte of any file changes, a file is added, or a file is removed.
    """
    entries = sorted(p for p in (root / "entries").glob("*") if p.is_file())
    lines = []
    total = 0
    for p in entries:
        h = hashlib.sha256()
        with p.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
                total += len(chunk)
        lines.append(f"{h.hexdigest()}  {p.relative_to(root)}")
    agg = hashlib.sha256("\n".join(lines).encode()).hexdigest()
    return {
        "entries_meta": sum(1 for p in entries if p.name.endswith(".meta.json")),
        "entries_turns": sum(1 for p in entries if p.name.endswith(".turns.jsonl")),
        "files": len(entries),
        "bytes": total,
        "aggregate_sha256": agg,
    }


def _source_unchanged(before: dict, after: dict) -> bool:
    """Recorded decision 1: the source spool must be untouched afterwards.

    ONE definition of the acceptance claim, so a test can pin the thing the
    runbook rests on: the fingerprints compare count, byte total AND aggregate
    digest, so a source that changed in any of those can never read as
    untouched. (Previously this was an inline `before == after` in `_one_mode`,
    which no test could reach without running a full drain.)
    """
    return before == after


def _gate_uri(uri: str | None) -> str:
    """Refuse anything that is not a loopback FalkorDB (recorded decision 2)."""
    if not uri:
        raise SystemExit(
            "TORTOISE_DB_URI is not set — start a private lane instance first:\n"
            "  eval \"$(uv run python tools/test_lane.py uri)\"")
    low = uri.lower()
    if "api.premiselabs.co" in low or "live-v5" in low:
        raise SystemExit(f"refusing a production target in TORTOISE_DB_URI: {uri!r}")
    # ⛔ THE HOST IS PARSED, NEVER SUBSTRING-MATCHED (#14442). The old test asked
    # whether the raw URI *contained* `localhost:`/`127.0.0.1:` anywhere — so a
    # PASSWORD or query string carrying it let a production target through:
    # `bolt://u:localhost:x@falkordb.prod:6379/g` was ACCEPTED, and the drain then
    # wrote to `falkordb.prod`. That is a fail-open in the only enforcement of
    # recorded decision 2, so the check is on the parsed HOSTNAME, exactly.
    host = (urlsplit(uri).hostname or "").strip("[]").lower()
    if host not in ("localhost", "127.0.0.1", "::1"):
        # `docker://:pw@127.0.0.1:PORT/graph` and `bolt://localhost:PORT` both pass;
        # anything else — including a host that merely LOOKS loopback — is refused.
        raise SystemExit(
            f"refusing a non-loopback FalkorDB target: {uri!r} (parsed host "
            f"{host or '<none>'!r}; the URI must name localhost / 127.0.0.1 / ::1)")
    return uri


def _org_id(mode: str, nonce: str) -> str:
    """A TENANT PER MODE, so one mode's sessions cannot be counted by the next.

    The app resolves a tenant to its own graph (`org_<id>`), so a shared org id
    would make `head-keyless`'s graph read include `head-mock`'s sessions and
    every "sessions: N" line would be cumulative across modes — an artefact,
    not a measurement. Derived from the mode name so it is stable per mode and
    still a valid UUID shape.
    """
    # ⛔ `:012d` does NOT fit 40 bits in every case: `n` is uniform on
    # `[0, 2**40-1]` and `2**40 - 10**12 = 99 511 627 776`, so 8.99 % of runs need
    # a THIRTEENTH digit — and a `:012` minimum width pads, it never truncates.
    # Measured over 20 000 `(mode, nonce)` pairs: 1 798 tails were not 12 chars
    # (e.g. `head-mock/nonce15 -> 00000000-0000-4000-8000-1021145458251`), which
    # is not the UUID shape this docstring promises. Hex fits by construction:
    # 40 bits is exactly 10 hex digits, so a 12-wide hex field always pads.
    n = int(hashlib.sha256(f"{mode}:{nonce}".encode()).hexdigest()[:10], 16)
    return f"00000000-0000-4000-8000-{n:012x}"


def _mode_knobs(mode: str) -> dict[str, str]:
    """The capture seams THIS mode needs — every one of them, always.

    ⛔ An unset knob is not "the default": it is whatever the operator's shell
    exported. Both of these are read at MODULE IMPORT (`hosted_api` binds
    `_CAPTURE_MAX_IN_FLIGHT` from `TORTOISE_CAPTURE_IN_FLIGHT` at :447, and the
    mock/lane branches read `TORTOISE_SESSION_LLM_MOCK` / `TORTOISE_SESSION_EXTRACTOR`
    per call), so a child process cannot undo an ambient value after the fact.

    The three measured ways an inherited value made a mode grade the opposite of
    its own label:

    * an ambient `TORTOISE_SESSION_LLM_MOCK=1` put **`head-keyless`** on the mock
      extraction path — the mode whose whole claim is "no extraction provider";
    * an ambient `TORTOISE_CAPTURE_IN_FLIGHT=1` put **`head-mock`** on a 1-slot
      ceiling and produced `capture_capacity_saturated` 429s in the mode whose
      claim is ZERO 429s;
    * an ambient `TORTOISE_SESSION_EXTRACTOR=m2` left **`pre-14261`**'s
      `_slow_extraction` seam inert, because the method it patches
      (`_extract_session_v2`) is the v2 lane only.

    A fourth consequence no knob can fix — an ambient PROVIDER KEY, which makes
    `head-keyless` extract for real under a label that says keyless — is refused
    at startup by `_assert_keyless`.

    ⛔ A fifth is the worst of them and it is a knob, just not one this function
    used to state: `RATE_LIMIT_DISABLED=1` sets
    `RateLimitMiddleware._disabled` (`hosted_api.py:5331`) and the middleware
    serves with NO 100/min key budget, so `head-keyless` files 265/265 with zero
    429s and the 50-entry six-hour park — this PR's central operational finding,
    and `#14342` D1's input — silently never happens. It is not hypothetical: the
    repo's own `tests/conftest.py:26` does
    `os.environ.setdefault("RATE_LIMIT_DISABLED", "1")`, so any run started under
    pytest inherits it. `"0"` restores the limiter (the middleware compares
    `== "1"`).

    Setting every knob explicitly is what makes the recorded `<mode>.json`
    reproducible on another box; the effective set is written into it
    (`capture_env`) so a polluted run is distinguishable after the fact.
    """
    return {
        # `head-keyless` IS the store-only lane: extraction must be absent, not mocked.
        "TORTOISE_SESSION_LLM_MOCK": "0" if mode == "head-keyless" else "1",
        # `backpressure` is the only mode that asks for a 1-slot ceiling.
        "TORTOISE_CAPTURE_IN_FLIGHT": "1" if mode == "backpressure" else "8",
        # `_slow_extraction` patches the v2 lane; m2 would make that seam inert.
        "TORTOISE_SESSION_EXTRACTOR": "v2",
        # EVERY mode measures the server's 100/min budget, so the limiter must be
        # ON — see the fifth consequence above (`tests/conftest.py` sets `1`).
        "RATE_LIMIT_DISABLED": "0",
    }


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ── the local server (child process) ─────────────────────────────────────────

def _serve(argv: list[str] | None = None) -> int:
    """Child entry point: apply the mode's rehearsal seams, then run uvicorn.

    ⛔ Every patch here is a REHEARSAL SEAM and is named in the report. The two
    that touch product code do so on documented seams:
      * `_capture_ack_ceiling_s` — the function `#14261`'s own tests override,
        set to a value larger than the transport bound so the handler waits for
        the extraction exactly as it did before the split;
      * `MockModel.complete` — wrapped with a sleep so the extraction is SLOW
        enough to be abandoned at the bound. Without a slow extraction the
        window is never paid and no 504 can occur.
    """
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", required=True)
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--log", required=True, help="JSONL of observed requests")
    args = ap.parse_args(argv)

    # ⛔ `_serve` is a REACHABLE entry point of the shipped CLI (an argparse
    # subcommand), not only an internal helper: reached directly it starts the REAL
    # app — writing real `Session`/turn rows — against whatever `TORTOISE_DB_URI`
    # the shell exports. The parent gates first, so on the documented path this is
    # idempotent defence in depth; but recorded decision 2 is stated as a property
    # of this FILE, and `_run` dispatches here BEFORE its own gate, so this reach
    # was the one it did not cover.
    _gate_uri(os.environ.get("TORTOISE_DB_URI"))

    # ⛔ THE ENVIRONMENT MUST BE SET BEFORE THE APP IS IMPORTED. Several of these
    # knobs are read at MODULE IMPORT into constants —
    # `hosted_api._CAPTURE_MAX_IN_FLIGHT = max(1, min(_int_env(
    # "TORTOISE_CAPTURE_IN_FLIGHT", 8), 16))` is the clearest — so setting them
    # after `import tortoise.hosted_api` silently leaves the default in force.
    # Measured: `backpressure` set IN_FLIGHT=1 post-import and produced 4/4 200s
    # with the ceiling still at 8 — the mode graded the default, not the mode.
    #
    # ⛔ Every knob is set for EVERY mode, including the ones that want the
    # default value: `_mode_knobs` states all four, because an inherited value is
    # not "the default" — it is the operator's shell. See that function's docstring
    # for the measured ways it made a mode grade its own opposite.
    os.environ.update(_mode_knobs(args.mode))

    import uvicorn

    import tortoise.hosted_api as ha

    # ⛔ The `head-keyless` label is a claim about the extraction path, and the
    # knobs above only PIN the mock: a provider key in the ambient environment
    # still lets the capture run REAL extraction (real spend) under a label that
    # says keyless, and the evidence in `head-keyless.json` stops reproducing.
    # The third measured consequence of ambient pollution is the only one no
    # knob can fix, so it fails closed and names the remedy.
    if args.mode == "head-keyless":
        _assert_keyless(ha._llm_provider_available())

    if args.mode == "pre-14261":
        ha._capture_ack_ceiling_s = lambda: 3600.0  # REHEARSAL SEAM (see docstring)
        _slow_extraction(PRE_BOUND_EXTRACTION_DELAY_S)
    elif args.mode == "backpressure":
        _slow_extraction(BACKPRESSURE_EXTRACTION_DELAY_S)

    _install_observer(ha.app, Path(args.log))
    uvicorn.run(ha.app, host="127.0.0.1", port=args.port, log_level="warning")
    return 0


def _assert_keyless(provider_available: bool) -> None:
    """`head-keyless` means NO provider — refuse rather than measure another mode.

    Split out from `_serve` so the refusal is assertable without importing the
    app or starting a server: `hosted_api._llm_provider_available()` is the ONLY
    reading of that question in the product, and it is read here rather than
    re-derived (with `TORTOISE_SESSION_LLM_MOCK=0` pinned, it answers purely from
    the provider keys).
    """
    if provider_available:
        raise SystemExit(
            "head-keyless requires NO extraction provider, but one is configured "
            "in this environment (a provider API key is set). This mode would "
            "extract for real under a label that says keyless — spending on the "
            "tool whose whole subject is extraction spend, and writing evidence "
            "that no longer reproduces. Unset the provider key, or run "
            "`--mode head-mock` for the extracting path.")


def _slow_extraction(delay_s: float) -> None:
    """Make the whole extraction take `delay_s` (REHEARSAL SEAM, server-side).

    ⛔ The first cut wrapped `MockModel.complete` and measured NOTHING: the
    hosted capture lane defaults to `TORTOISE_SESSION_EXTRACTOR=v2`
    (`extractor_v2.extract_session_v2`), which does not go through that class at
    all — so the "slow" extraction finished in milliseconds and both modes that
    depend on latency silently graded a fast path (measured: `pre-14261` 3
    sessions in 3.74 s, zero 504s). Wrapping the ONE method the tail calls
    (`TortoiseSDK._extract_session_v2`) is lane-agnostic and is the seam this
    report names.

    It runs inside the capture worker thread, so sleeping here blocks THAT
    session's extraction and nothing else — which is exactly the shape of a slow
    model call.
    """
    from tortoise.sdk import TortoiseSDK

    real = TortoiseSDK._extract_session_v2

    def slow(self, *a, **kw):
        time.sleep(delay_s)
        return real(self, *a, **kw)

    TortoiseSDK._extract_session_v2 = slow


def _install_observer(app, log_path: Path) -> None:
    """Record every request: status, duration, and the body of every FAILURE.

    The 504's own body carries the stage string the issue asks be recorded
    VERBATIM (`stage=capture_extract spent_ms=…`), so a status-only observer
    could not produce the evidence. Non-2xx bodies are read and re-emitted.
    """
    from starlette.responses import Response

    log = log_path.open("a", encoding="utf-8")

    @app.middleware("http")
    async def _rehearsal_observer(request, call_next):
        t0 = time.perf_counter()
        response = await call_next(request)
        dur_ms = (time.perf_counter() - t0) * 1000.0
        body = ""
        if response.status_code >= 400:
            chunks = [chunk async for chunk in response.body_iterator]
            raw = b"".join(
                c if isinstance(c, bytes) else c.encode() for c in chunks)
            body = raw.decode("utf-8", "replace")[:1200]
            headers = {k: v for k, v in response.headers.items()
                       if k.lower() not in ("content-length", "content-encoding")}
            response = Response(content=raw, status_code=response.status_code,
                                headers=headers,
                                media_type=response.media_type)
        log.write(json.dumps({
            "t": round(time.time(), 3),
            "method": request.method,
            "path": request.url.path,
            "status": response.status_code,
            "dur_ms": round(dur_ms, 1),
            "body": body,
        }) + "\n")
        log.flush()
        return response

    # The handle is deliberately NOT closed on shutdown: this FastAPI version has
    # no `add_event_handler`, and the process is terminated by the parent anyway
    # (the OS closes the fd). Every write is followed by a flush, so a hard kill
    # cannot lose an observed request.


# ── seeding a local tenant ───────────────────────────────────────────────────

def _seed_tenant(org: str) -> str:
    """Seed the graph rows a real tenant has and mint a key. Returns the key.

    Same seed the acceptance tool performs, for the same measured reason: the
    capture lane reads its quota straight off the authenticated org, so a stub
    org makes every capture a fail-closed 500 and the rehearsal would grade the
    quota gate instead of the drain. ⛔ The key is never printed.
    """
    from tortoise.billing import apply_limits
    from tortoise.hosted_api import (
        _get_onboarding_state,
        _make_sdk,
        _update_onboarding_state,
    )

    registry = _make_sdk(namespace="registry")._get_registry()
    registry.query(
        "MERGE (t:Team {id:$id}) SET t.onboarding_state=$st",
        params={"id": org, "st": "{}"})
    apply_limits(_make_sdk(namespace="registry"), org, "free")
    _update_onboarding_state(org, session_recording=True)
    state = _get_onboarding_state(org)
    if state.get("session_recording") is not True:
        raise SystemExit(f"tenant seed did not persist: {state}")
    key = _make_sdk(namespace="registry")._apikey_create(org, ACTOR_ID)
    return key["api_key"]


# ── reading the run back ─────────────────────────────────────────────────────

def _spool_state(root: Path) -> dict:
    """Per-entry spool bookkeeping after a drain: the drain's own ledger."""
    metas = sorted((root / "entries").glob("*.meta.json"))
    filed = terminal = 0
    attempts: list[int] = []
    windows: list[float] = []
    reasons: dict[str, int] = {}
    now_ms = time.time() * 1000.0
    for p in metas:
        try:
            meta = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # an unreadable meta is itself a finding
            reasons["meta_unreadable"] = reasons.get("meta_unreadable", 0) + 1
            continue
        if meta.get("filed_key"):
            filed += 1
        tf = meta.get("terminal_failure")
        if tf:
            terminal += 1
            reason = str((tf or {}).get("reason") or "unknown")
            reasons[reason] = reasons.get(reason, 0) + 1
        attempts.append(int(meta.get("attempts") or 0))
        #: How long THIS entry is now parked for. After a refusal the writer
        #: sets `now + backoff_delay(attempts)`, and `backoff_delay` saturates at
        #: `RETRY_MAX_SECONDS` (6 h) — so an entry that already carried a high
        #: `attempts` from the parked spool lands AT the cap. This number is what
        #: makes "re-run it" advice falsifiable: a second pass cannot touch an
        #: entry whose window has not elapsed, whatever the exit code says.
        try:
            remaining = (float(meta.get("next_attempt_at_ms") or 0) - now_ms) / 1000.0
        except (TypeError, ValueError):
            remaining = 0.0
        if remaining > 0:
            windows.append(remaining)
    attempts.sort()
    windows.sort()

    def pct(v: float) -> int | None:
        if not attempts:
            return None
        idx = min(len(attempts) - 1, max(0, int(len(attempts) * v) - 1))
        return attempts[idx]

    return {
        "entries": len(metas),
        "filed_key_present": filed,
        "terminal_failure": terminal,
        "terminal_reasons": reasons,
        "attempts_min": attempts[0] if attempts else None,
        "attempts_p50": pct(0.5),
        "attempts_max": attempts[-1] if attempts else None,
        "attempts_gt_0": sum(1 for a in attempts if a > 0),
        "backoff_waiting": len(windows),
        "backoff_max_seconds": round(windows[-1], 0) if windows else 0,
        "backoff_p50_seconds": round(windows[len(windows) // 2], 0) if windows else 0,
    }


def _graph_state(org: str) -> dict:
    """What the local graph holds after the drain — the product's own view."""
    from tortoise.hosted_api import _make_sdk

    proj = _make_sdk(namespace=org)._get_proj()
    q = proj.g.query
    sessions = q("MATCH (s:Session) RETURN count(s)").result_set[0][0]
    turns = q("MATCH (s:Session)-[:CONTAINS]->(p:Point) RETURN count(p)").result_set[0][0]
    extracted = q(
        "MATCH (s:Session)-[:CONTAINS]->(p:Point) "
        "WHERE p.pointKind IS NULL OR p.pointKind <> 'event' "
        "RETURN count(p)").result_set[0][0]
    ok = q("MATCH (s:Session) WHERE s.capture_ok = true RETURN count(s)").result_set[0][0]
    extracting = q(
        "MATCH (s:Session) WHERE s.capture_extraction_phase IS NOT NULL "
        "AND s.capture_extraction_phase <> '' RETURN count(s)").result_set[0][0]
    entities = q("MATCH (e:Entity) RETURN count(e)").result_set[0][0]
    return {
        "sessions": int(sessions), "turn_points": int(turns),
        "extracted_points": int(extracted), "capture_ok_true": int(ok),
        "phase_in_flight": int(extracting), "entities": int(entities),
    }


def _wait_quiescent(org: str, timeout_s: float = 300.0,
                    interval_s: float = 2.0) -> dict:
    """Poll until no session publishes an in-flight extraction phase.

    This is the runbook's stage-4 observable ("every session reaches a terminal
    state ON ITS OWN"): the drain returns as soon as the RECORD is durable, so
    the only honest way to observe completion is to wait for the phase to clear
    without touching the client. A timeout is reported, never raised — a stuck
    extraction is a finding, not a crash.
    """
    t0 = time.time()
    last = None
    while time.time() - t0 < timeout_s:
        last = _graph_state(org)
        if last["phase_in_flight"] == 0:
            return {"reached_zero": True,
                    "seconds": round(time.time() - t0, 1), **last}
        time.sleep(interval_s)
    return {"reached_zero": False, "seconds": round(time.time() - t0, 1),
            **(last or {})}


def _observations(log_path: Path) -> dict:
    rows = []
    if log_path.exists():
        for line in log_path.read_text(encoding="utf-8").splitlines():
            with contextlib.suppress(Exception):
                rows.append(json.loads(line))
    posts = [r for r in rows if r["method"] == "POST" and r["path"] == "/v1/sessions"]
    durs = sorted(r["dur_ms"] for r in posts)
    statuses: dict[str, int] = {}
    for r in posts:
        statuses[str(r["status"])] = statuses.get(str(r["status"]), 0) + 1
    bodies = [{"status": r["status"], "body": r["body"]}
              for r in posts if r["status"] >= 400]
    # Two shapes exist and BOTH must be read: the wait-bound refusal answers JSON
    # (`"stage":"capture_extract"`), while the older prose form was
    # `stage=unmarked`. A regex for one shape silently reports "no stages seen"
    # on the other — exactly the kind of blind observer this tool exists not to be.
    # ⛔ The alternative whose group did NOT match is `None`, so the element is
    # `group(1) or group(2)`: collecting `group(1)` unconditionally put a `None`
    # into the set, and the prose form — the one shape this regex was widened
    # for — then crashed `sorted()` / `', '.join()` with `TypeError`.
    stages: set[str] = set()
    for r in posts:
        m = re.search(r'"stage"\s*:\s*"([^"]+)"|stage=([A-Za-z0-9_]+)',
                      r["body"] or "")
        if m:
            stages.add(m.group(1) or m.group(2))
    stages = sorted(stages)

    def pct(v: float) -> float | None:
        if not durs:
            return None
        idx = min(len(durs) - 1, max(0, int(len(durs) * v) - 1))
        return round(durs[idx], 1)

    return {
        "posts": len(posts),
        "status_counts": statuses,
        "posts_per_min": _posts_per_min(posts),
        "rate_limited": sum(1 for r in posts if r["status"] == 429),
        "ack_ms_p50": pct(0.5), "ack_ms_p95": pct(0.95),
        "ack_ms_max": round(durs[-1], 1) if durs else None,
        "stage_strings": stages,
        "refusal_bodies": bodies[:5],
    }


def _posts_per_min(posts: list[dict]) -> float | None:
    """Observed POST rate — the number the 100/min key budget acts on.

    Two modes with identical work produced different rates (the extracting one
    is slower per session), and the slower one was the ONLY one that never got a
    single 429. That is the evidence a client-side pace has to exist, so the
    rate is recorded rather than inferred from wall-clock.
    """
    if len(posts) < 2:
        return None
    span = posts[-1]["t"] - posts[0]["t"]
    if span <= 0:
        return None
    return round(len(posts) / span * 60.0, 1)


def _run_drain(env: dict, workdir: Path, label: str) -> dict:
    """Run the REAL CLI drain. Returns its verbatim streams and timing."""
    t0 = time.time()
    proc = subprocess.run(
        [sys.executable, "-m", "tortoise", "session", "drain", "--retry-terminal"],
        env=env, cwd=str(workdir), capture_output=True, text=True, timeout=1800)
    return {
        "label": label,
        "exit": proc.returncode,
        "seconds": round(time.time() - t0, 2),
        "stdout": proc.stdout[-4000:],
        "stderr": proc.stderr[-8000:],
    }


# ── corpus accounting (the cost question) ────────────────────────────────────

def _corpus_cost(root: Path) -> dict:
    """Per-entry size distribution and the extraction spend it implies.

    Tokens are reported two ways and NEITHER is presented as the other: the
    `chars/4` estimate is a bound that needs no dependency, and tiktoken's
    `o200k_base` (when importable) is the one the extractor's own accounting
    uses. The median is over PER-SESSION totals — the drain pays per session,
    and the issue's 36 415-token figure is a per-session median.
    """
    totals: list[int] = []
    turns: list[int] = []
    for meta_p in sorted((root / "entries").glob("*.meta.json")):
        turns_p = meta_p.with_name(meta_p.name.replace(".meta.json", ".turns.jsonl"))
        if not turns_p.exists():
            continue
        chars = 0
        n = 0
        with turns_p.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                n += 1
                with contextlib.suppress(Exception):
                    rec = json.loads(line)
                    chars += len(str(rec.get("content") or ""))
        totals.append(chars)
        turns.append(n)
    enc = None
    with contextlib.suppress(Exception):
        import tiktoken
        enc = tiktoken.get_encoding("o200k_base")
    est_tokens = []
    for meta_p in sorted((root / "entries").glob("*.meta.json")):
        turns_p = meta_p.with_name(meta_p.name.replace(".meta.json", ".turns.jsonl"))
        if not turns_p.exists():
            continue
        text = turns_p.read_text(encoding="utf-8", errors="replace")
        if enc is not None:
            est_tokens.append(len(enc.encode(text, disallowed_special=())))
        else:
            est_tokens.append(len(text) // 4)

    def stat(xs: list[int]) -> dict:
        if not xs:
            return {"n": 0}
        xs = sorted(xs)
        def p(v: float) -> int:
            return xs[min(len(xs) - 1, max(0, int(len(xs) * v) - 1))]
        return {"n": len(xs), "min": xs[0], "p50": p(0.5), "p90": p(0.9),
                "p99": p(0.99), "max": xs[-1], "sum": sum(xs),
                "mean": round(sum(xs) / len(xs), 1)}

    by_size = stat(est_tokens)
    top10 = sum(sorted(est_tokens, reverse=True)[:max(1, len(est_tokens) // 10 or 1)])
    return {
        "encoder": "tiktoken:o200k_base" if enc is not None else "chars/4 estimate",
        "sessions": by_size.get("n", 0),
        "tokens_per_session": by_size,
        "turns_per_session": stat(turns),
        "chars_per_session": stat(totals),
        "top_decile_share_of_tokens": (
            round(top10 / by_size["sum"], 3) if by_size.get("sum") else None),
    }


# ── orchestration ────────────────────────────────────────────────────────────

def _copy_spool(src: Path, dst: Path, limit: int | None = None) -> dict:
    """Copy the spool. The SOURCE is only ever read."""
    (dst / "entries").mkdir(parents=True, exist_ok=True)
    metas = sorted((src / "entries").glob("*.meta.json"))
    if limit is not None:
        metas = metas[:limit]
    copied = 0
    for meta_p in metas:
        turns_p = meta_p.with_name(meta_p.name.replace(".meta.json", ".turns.jsonl"))
        shutil.copy2(meta_p, dst / "entries" / meta_p.name)
        if turns_p.exists():
            shutil.copy2(turns_p, dst / "entries" / turns_p.name)
        copied += 1
    # `status.json` describes the source box's own health; carrying it into the
    # copy would make the rehearsal's first status read a stale claim about a
    # different spool. It is deliberately NOT copied.
    return {"copied_entries": copied}


def _wait_health(port: int, timeout_s: float = 180.0) -> bool:
    import urllib.error
    import urllib.request

    deadline = time.time() + timeout_s
    url = f"http://127.0.0.1:{port}/health"
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5) as r:
                if r.status == 200:
                    return True
        except (urllib.error.URLError, OSError):
            time.sleep(1.0)
    return False


def _start_server(mode: str, log_path: Path) -> tuple[subprocess.Popen, int]:
    """Start the child server with its output on a FILE, not an unread `PIPE`.

    A `PIPE` nobody drains blocks the child on `write(2)` once it fills (~64 KiB)
    and the server stops serving MID-DRAIN — and the symptom is a silent hang,
    not a red, because the parent only ever read the pipe on the health-failure
    path. `log_level="warning"` keeps today's runs under the bound (no warnings
    were emitted), so this was a latent hazard rather than a live one; the file
    removes it for free and is still read when health never arrives.
    """
    port = _free_port()
    server_log = log_path.with_name("server.log")
    with server_log.open("wb") as sink:
        proc = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "_serve",
             "--mode", mode, "--port", str(port), "--log", str(log_path)],
            env={**os.environ, **_mode_knobs(mode),
                 "TORTOISE_API_URL": f"http://127.0.0.1:{port}"},
            stdout=sink, stderr=subprocess.STDOUT)
    if not _wait_health(port):
        out = ""
        with contextlib.suppress(Exception):
            proc.terminate()
            proc.wait(timeout=20)
            out = server_log.read_text(
                encoding="utf-8", errors="replace")[-3000:]
        raise SystemExit(f"local server never became healthy (port {port}):\n{out}")
    return proc, port


def _drain_env(port: int, key: str, spool: Path, events_dir: Path,
               mode: str) -> dict:
    """The drain's identity: loopback URL, minted key, the COPY as the spool.

    The mode's knobs are applied here too: the client reads the same
    `TORTOISE_SESSION_EXTRACTOR` branch when it decides what is retryable, so a
    client left on an ambient `m2` would grade a lane the server is not running.
    """
    return {
        **os.environ,
        **_mode_knobs(mode),
        "TORTOISE_API_URL": f"http://127.0.0.1:{port}",
        "TORTOISE_API_KEY": key,
        "TORTOISE_CAPTURE": "1",                      # consent (transmission gate)
        "TORTOISE_CAPTURE_SPOOL_DIR": str(spool),     # the COPY
        "TORTOISE_SESSION_EVENTS_DIR": str(events_dir),
        "TORTOISE_DB_URI": os.environ.get("TORTOISE_DB_URI", ""),
    }


def _one_mode(mode: str, *, spool_src: Path, out_dir: Path,
              limit: int | None, replay: bool, nonce: str = "") -> dict:
    work = Path(tempfile.mkdtemp(prefix=f"rehearse-{mode}-"))
    spool_copy = work / "spool"
    events_dir = work / "session-events"
    events_dir.mkdir(parents=True, exist_ok=True)
    log_path = work / "requests.jsonl"
    before = _source_fingerprint(spool_src)
    copied = _copy_spool(spool_src, spool_copy, limit=limit)
    org = _org_id(mode, nonce)
    key = _seed_tenant(org)
    proc = None
    try:
        proc, port = _start_server(mode, log_path)
        env = _drain_env(port, key, spool_copy, events_dir, mode)
        passes = [_run_drain(env, work, "drain-1")]
        state_1 = _spool_state(spool_copy)
        graph_1 = _graph_state(org)
        # The extraction finishes BEHIND the ack, so a read taken the instant the
        # drain returns can legitimately still show sessions in flight. Polling to
        # quiescence is what makes "terminal on its own" an observation instead of
        # a race, and its duration is the runbook's own number.
        # The pre-split path gets a SHORT bound (20 s, not five minutes) because
        # the abandoned request was expected to leave its sessions stuck. It does
        # not: the `504` arrives AFTER the record landed, so the client's confirm
        # path files the entry and the run settles — measured in the committed
        # `pre-14261.json`, `settle.reached_zero = True` in 4.1 s, `filed_key 3/3`,
        # `attempts_max 0`. The short bound is a guard against a slow wait, not a
        # prediction of `reached_zero: false` (an earlier version of this comment
        # said otherwise, contradicting both this artefact and the runbook's
        # `pre-14261` row, which was corrected alongside it).
        settle = _wait_quiescent(
            org, timeout_s=20.0 if mode == "pre-14261" else 300.0)
        graph_settled = _graph_state(org)
        if replay:
            passes.append(_run_drain(env, work, "drain-2 (replay)"))
            state_2 = _spool_state(spool_copy)
            graph_2 = _graph_state(org)
        else:
            state_2, graph_2 = None, None
        obs = _observations(log_path)
    finally:
        if proc is not None:
            proc.terminate()
            with contextlib.suppress(Exception):
                proc.wait(timeout=30)
            if proc.poll() is None:
                proc.kill()
    after = _source_fingerprint(spool_src)
    result = {
        "mode": mode,
        # The seams this run actually executed under, so a `<mode>.json` says
        # whether it measured its own label or an inherited one.
        "capture_env": _mode_knobs(mode),
        "corpus": {**copied, "source_before": before},
        "invariant_source_untouched": _source_unchanged(before, after),
        "source_after": after,
        "passes": passes,
        "spool_after_pass_1": state_1,
        "spool_after_pass_2": state_2,
        "graph_after_pass_1": graph_1,
        "settle": settle,
        "graph_settled": graph_settled,
        "graph_after_pass_2": graph_2,
        "observations": obs,
    }
    (out_dir / f"{mode}.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def _summarise(results: list[dict], cost: dict) -> str:
    lines = []
    for r in results:
        obs = r["observations"]
        p1 = r["passes"][0]
        p2 = r["passes"][1] if len(r["passes"]) > 1 else None
        lines.append(f"## mode `{r['mode']}`")
        lines.append("")
        lines.append(f"- corpus copied: {r['corpus']['copied_entries']} entries "
                     f"(source untouched: **{r['invariant_source_untouched']}**)")
        lines.append(f"- drain pass 1: exit {p1['exit']}, {p1['seconds']} s")
        lines.append(f"- POST /v1/sessions: {obs['posts']} — statuses "
                     f"`{obs['status_counts']}`, observed rate "
                     f"{obs['posts_per_min']} /min")
        if obs.get("rate_limited"):
            lines.append(f"- ⚠️ rate-limited (429): {obs['rate_limited']} "
                         f"— deferred, kept, and NOT re-attempted until "
                         f"their backoff window elapses")
        lines.append(f"- ack ms: p50 {obs['ack_ms_p50']} · p95 {obs['ack_ms_p95']} · "
                     f"max {obs['ack_ms_max']}")
        if obs["stage_strings"]:
            lines.append(f"- stages seen: `{', '.join(obs['stage_strings'])}`")
        if obs["refusal_bodies"]:
            lines.append("- refusal bodies (verbatim, truncated):")
            for b in obs["refusal_bodies"]:
                lines.append(f"  - `{b['status']}: {b['body'][:400]}`")
        s1 = r["spool_after_pass_1"]
        lines.append(f"- spool after pass 1: filed_key {s1['filed_key_present']}"
                     f"/{s1['entries']}, terminal {s1['terminal_failure']} "
                     f"`{s1['terminal_reasons']}`, attempts "
                     f"min/p50/max {s1['attempts_min']}/{s1['attempts_p50']}/"
                     f"{s1['attempts_max']} ({s1['attempts_gt_0']} entries >0)")
        if s1.get("backoff_waiting"):
            lines.append(f"- parked by backoff: {s1['backoff_waiting']} entries, "
                         f"p50 {s1['backoff_p50_seconds']} s, max "
                         f"{s1['backoff_max_seconds']} s")
        g1 = r["graph_after_pass_1"]
        lines.append(f"- graph after pass 1: sessions {g1['sessions']}, "
                     f"turn points {g1['turn_points']}, extracted points "
                     f"{g1['extracted_points']}, capture_ok {g1['capture_ok_true']}, "
                     f"phase in flight {g1['phase_in_flight']}, entities "
                     f"{g1['entities']}")
        settle = r.get("settle") or {}
        if settle:
            lines.append(f"- settled on its own: **{settle.get('reached_zero')}** "
                         f"after {settle.get('seconds')} s → in flight "
                         f"{settle.get('phase_in_flight')}, capture_ok "
                         f"{settle.get('capture_ok_true')}, extracted points "
                         f"{settle.get('extracted_points')}")
        if p2 is not None:
            g2 = r["graph_after_pass_2"]
            s2 = r.get("spool_after_pass_2") or {}
            # The replay's delta is measured from the SETTLED graph, never from
            # `graph_after_pass_1`: the settle poll runs BETWEEN pass 1 and the
            # replay, so a `g2 - g1` delta credits the replay with extraction that
            # landed during the poll. Measured in this repo's own `head-mock.json`:
            # g1 526 extracted / settled 528 / after replay 528 — and the old row
            # printed `Δ 2` under "replay pass" in the very row whose purpose is to
            # prove the replay added nothing.
            base = r.get("graph_settled") or g1
            lines.append(f"- replay pass: exit {p2['exit']}, {p2['seconds']} s → "
                         f"sessions {g2['sessions']} "
                         f"(Δ {g2['sessions'] - base['sessions']} vs settled), "
                         f"extracted points {g2['extracted_points']} "
                         f"(Δ {g2['extracted_points'] - base['extracted_points']} "
                         f"vs settled), filed_key {s2.get('filed_key_present')}")
            lines.append(f"  - replay stderr: `{p2['stderr'].strip().splitlines()[-1] if p2['stderr'].strip() else ''}`")
        lines.append("")
    lines.append("## corpus cost")
    lines.append("")
    lines.append(f"- encoder: `{cost['encoder']}`")
    lines.append(f"- sessions: {cost['sessions']}")
    for key in ("tokens_per_session", "turns_per_session", "chars_per_session"):
        st = cost[key]
        if st.get("n"):
            lines.append(f"- {key}: min {st['min']} · p50 {st['p50']} · p90 "
                         f"{st['p90']} · p99 {st['p99']} · max {st['max']} · "
                         f"sum {st['sum']} · mean {st['mean']}")
    if cost.get("top_decile_share_of_tokens") is not None:
        lines.append(f"- top-decile share of tokens: "
                     f"{cost['top_decile_share_of_tokens']:.1%}")
    return "\n".join(lines)


def _summary_document(results: list[dict], cost: dict,
                      created: str | None = None) -> str:
    """The bytes of SUMMARY.md — a pure projection of the artefacts.

    Separate from `_write_summary` so the `summary` verb can re-render the prose
    WITHOUT rewriting `corpus-cost.json` from a live spool no mode measured.

    `created` is the ONE input the artefacts cannot supply: no `<mode>.json`
    carries a wall-clock date (they carry durations, not instants), so it is
    passed in — read back from the file being replaced, which is what makes a
    re-render idempotent and the committed bytes pinnable.
    """
    stamp = created or time.strftime("%Y-%m-%d", time.gmtime())
    return (
        "---\n"
        'title: "Spool drain rehearsal — measured results (#14232 / #14261)"\n'
        "type: evidence\n"
        "domain: platform\n"
        "doc_status: live\n"
        "subjects.team: epistemic-team\n"
        "aboutSubjects: capture-spool, session-drain, capture-ingest\n"
        # ⛔ A quoted scalar followed by `, …` on the SAME line is not YAML, and
        # every `run`/`summary` wrote it: measured with this box's PyYAML, the
        # committed file and the generator's output BOTH parsed as
        # `expected <block end>, but found ','` at the `aboutObjects` line. The
        # runbook writes the same field correctly one file over (a bare list), so
        # the generator — not the convention — was the defect.
        'aboutObjects: tortoise, "#14232", "#14261", "#14342", '
        "tools/spool_drain_rehearsal.py, tortoise/hosted_api.py\n"
        f"created: {stamp}\n"
        "---\n\n"
        "# Spool drain rehearsal — results\n\n"
        "Generated by `tools/spool_drain_rehearsal.py`. Every number below is "
        "read from the run's own artefacts (`<mode>.json`, `corpus-cost.json`), "
        "not retyped. ⛔ The source spool is never written to: each mode records "
        "a before/after fingerprint and the run fails if they differ.\n\n"
        + _summarise(results, cost) + "\n")


def _committed_created(path: Path) -> str | None:
    """The `created:` the file already states, so a re-render does not re-date it.

    `created` is the document's birth date. Reading it back is what makes
    `summary` idempotent: without it every re-render restamped the file and the
    committed bytes could never be pinned (which is how the shipped SUMMARY.md
    drifted two review rounds without anything going red).
    """
    if not path.exists():
        return None
    m = re.search(r"^created: (\d{4}-\d{2}-\d{2})$",
                  path.read_text(encoding="utf-8"), re.MULTILINE)
    return m.group(1) if m else None


def _merge_results(out_dir: Path, fresh: list[dict]) -> list[dict]:
    """This run's results, plus every OTHER mode's artefact already on disk.

    ⛔ `run --mode X` is a documented form (the runbook offers it), and the
    pre-fix tail built `results` from THIS invocation alone and handed it to the
    single writer — so one single-mode run replaced SUMMARY.md's four sections
    with the one it had just measured, while the other three `<mode>.json` files
    stayed on disk. Measured: after `run --mode head-mock`, modes present =
    `['head-mock']`; `head-keyless`, `pre-14261` and `backpressure` were gone from
    the prose but not from the directory. `summary` already merges (it reads all
    four); this makes `run` agree with it instead of deleting evidence it did not
    re-measure.
    """
    by_mode = {r["mode"]: r for r in fresh}
    for mode in MODES:
        if mode in by_mode:
            continue
        p = out_dir / f"{mode}.json"
        if p.exists():
            by_mode[mode] = json.loads(p.read_text(encoding="utf-8"))
    return [by_mode[m] for m in MODES if m in by_mode]


def _write_summary(results: list[dict], cost: dict, out_dir: Path,
                   *, write_cost: bool = True) -> None:
    """Record a MEASURED corpus cost and render the evidence artefacts.

    The ONLY writer of SUMMARY.md. One writer so a formatting fix (e.g. the
    `###` → `##` heading level `MD001` rejects under an H1) never needs a
    re-measure, and so the JSON and the prose cannot disagree about the same run.

    `corpus-cost.json` is written only when the run DRAINED the corpus it claims
    to describe (`write_cost`): a `--limit` run drains a slice, so re-measuring
    the live spool there would describe a corpus no mode drained.
    """
    if write_cost:
        (out_dir / "corpus-cost.json").write_text(
            json.dumps(cost, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (out_dir / "SUMMARY.md").write_text(
        _summary_document(results, cost,
                          created=_committed_created(out_dir / "SUMMARY.md")),
        encoding="utf-8")


def _run(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("run", "all", "summary"):
        s = sub.add_parser(name)
        if name == "summary":
            s.add_argument("--spool-src", type=Path, default=DEFAULT_SPOOL)
            s.add_argument("--out", type=Path, default=REPO_ROOT / "docs" / "evidence"
                           / "14232-spool-drain-rehearsal")
            continue
        s.add_argument("--mode", default="head-mock",
                       choices=["head-keyless", "head-mock", "pre-14261",
                                "backpressure"])
        s.add_argument("--spool-src", type=Path, default=DEFAULT_SPOOL)
        s.add_argument("--out", type=Path, default=REPO_ROOT / "docs" / "evidence"
                       / "14232-spool-drain-rehearsal")
        s.add_argument("--limit", type=int, default=None)
        s.add_argument("--no-replay", action="store_true")
    s = sub.add_parser("_serve")
    s.add_argument("--mode", required=True)
    s.add_argument("--port", type=int, required=True)
    s.add_argument("--log", required=True)
    args = ap.parse_args(argv)
    if args.cmd == "_serve":
        return _serve(["--mode", args.mode, "--port", str(args.port),
                       "--log", args.log])

    # ⛔ BOTH of these preconditions belong to the verbs that DRAIN. `summary` is
    # the verb whose whole contract is "re-render the prose from the artefacts on
    # disk and never touch the live spool" — `args.spool_src` is unread on that
    # path. A `is_dir()` guard placed OUTSIDE the branch made the verb refuse for
    # want of a spool it never reads, on exactly the box the failing
    # committed-artefact pin sends the maintainer to (fresh clone, CI runner,
    # reviewer's laptop, container — none has ever captured), and it named a
    # condition the verb does not depend on. Same defect class as the `all` path's
    # "the remedy it prints is the command that just failed".
    if args.cmd != "summary":
        _gate_uri(os.environ.get("TORTOISE_DB_URI"))
        if not (args.spool_src / "entries").is_dir():
            raise SystemExit(f"no spool at {args.spool_src}")
    args.out.mkdir(parents=True, exist_ok=True)

    if args.cmd == "summary":
        # Re-render SUMMARY.md from the JSONs already on disk. The report is a
        # projection of the artefacts, so regenerating it must never require
        # re-running a 10-minute drain — and a formatting fix to the renderer
        # must not be a reason to re-measure.
        # ⛔ That means `corpus-cost.json` is READ here, never recomputed: the
        # live spool can have grown since the run, and rewriting the committed
        # cost from it would describe a corpus NO mode measured (measured: run on
        # 265 entries, spool +30, `summary` → SUMMARY.md reports 295 beside
        # `head-keyless.json`'s 265). Absent is a refusal, not a re-measure.
        results = []
        for mode in MODES:
            p = args.out / f"{mode}.json"
            if not p.exists():
                raise SystemExit(f"missing artefact: {p}")
            results.append(json.loads(p.read_text(encoding="utf-8")))
        cost_path = args.out / "corpus-cost.json"
        if not cost_path.exists():
            raise SystemExit(
                f"missing artefact: {cost_path} — `summary` re-renders the prose "
                f"and never re-measures, so it needs the cost this run recorded; "
                f"run `all` (or `run --mode …`) against this spool first")
        cost = json.loads(cost_path.read_text(encoding="utf-8"))
        (args.out / "SUMMARY.md").write_text(
            _summary_document(results, cost,
                              created=_committed_created(args.out / "SUMMARY.md")),
            encoding="utf-8")
        print(_summarise(results, cost))
        return 0

    plan = ([("head-keyless", None, True), ("head-mock", None, True),
             ("pre-14261", 3, False), ("backpressure", 4, False)]
            if args.cmd == "all"
            else [(args.mode,
                   args.limit if args.limit is not None
                   else (3 if args.mode == "pre-14261" else
                         4 if args.mode == "backpressure" else None),
                   not args.no_replay)])
    # ONE run = ONE tenant set. A mode-derived org id alone is stable across
    # runs, so a second run of the same mode re-POSTed sessions that already
    # existed and the server took the REPLAY lane (no extraction, no join, no
    # 504) — measured: a `pre-14261` re-run produced 200s at 250 ms with the ack
    # window deliberately disabled. The nonce is what keeps each run's graph its
    # own.
    nonce = time.strftime("%Y%m%dT%H%M%S", time.gmtime())
    fresh = []
    for mode, limit, replay in plan:
        print(f"[rehearse] mode={mode} limit={limit}", file=sys.stderr)
        fresh.append(_one_mode(mode, spool_src=args.spool_src,
                               out_dir=args.out, limit=limit, replay=replay,
                               nonce=nonce))

    # A single-mode `run` may not delete the modes it did not re-measure.
    results = _merge_results(args.out, fresh)
    # `corpus-cost.json` describes the corpus the modes DRAINED. `pre-14261`
    # defaults to a 3-entry slice and `backpressure` to 4, so re-measuring the
    # live spool on such a run describes a corpus no mode drained — the same
    # defect `162e9cd9` fixed on the `summary` path, still live here.
    # ⛔ `any`, not `all`: `all` is the DEFAULT mode and its plan always includes
    # the two SLICED modes (`pre-14261` 3 entries, `backpressure` 4), so `all(...)`
    # is False on every `all` invocation — the two full-corpus modes are outvoted by
    # the two sliced ones. Measured consequence: on a clean output dir `all` drained
    # all four modes and then exited with "records no corpus cost … run `all`" (the
    # remedy it prints is the command that just failed) and wrote no `SUMMARY.md`
    # either; on an existing dir it kept a stale cost while re-rendering a summary
    # beside it. A mode that drained the WHOLE corpus is what licenses a cost.
    writes_cost = any(limit is None for _, limit, _ in plan)
    if writes_cost:
        # ⛔ The cost is measured from the LIVE spool AFTER every mode ran, while
        # each mode copied the corpus at its OWN start — so a spool that grows
        # mid-run (a live capture box does exactly that) yields a cost no mode
        # drained, printed beside per-mode `corpus copied: N` with nothing red.
        # That is the class `162e9cd9` closed on the `summary` path. Tie the two
        # together: every full-corpus mode's own `source_before` digest must still
        # be the digest the cost was computed from — and the fingerprint is taken
        # AFTER `_corpus_cost` (which tokenises the whole spool, so it is the long
        # window), because a digest read BEFORE the measurement attests a corpus
        # the measurement did not see. Measured in that order: an entry appended
        # during `_corpus_cost` exited 0 with `sessions: 4` while every mode had
        # attested 3.
        cost = _corpus_cost(args.spool_src)
        live = _source_fingerprint(args.spool_src)
        full = {m for m, lim, _ in plan if lim is None}
        disagreed = [
            r.get("mode") for r in fresh
            if r.get("mode") in full
            and (((r.get("corpus") or {}).get("source_before") or {})
                 .get("aggregate_sha256")) != live["aggregate_sha256"]
        ]
        if disagreed:
            raise SystemExit(
                f"the source spool {args.spool_src} changed while the run was in "
                f"flight — {disagreed} copied a corpus whose digest is no longer "
                f"the spool's, so a cost taken now describes a corpus NO mode "
                f"drained. Re-run against a quiesced spool (stop capture), or run "
                f"a SLICED mode (`--mode pre-14261`, which reuses the recorded "
                f"cost instead of measuring one) — a full-corpus mode would hit "
                f"this same refusal. Do not read `corpus-cost.json` as this "
                f"run's")
    else:
        cost_path = args.out / "corpus-cost.json"
        if not cost_path.exists():
            sliced = [m for m, lim, _ in plan if lim is not None]
            raise SystemExit(
                f"{args.out} records no corpus cost and {sliced} "
                f"drains only a slice, so it cannot state one — run `all` (or a "
                f"mode with no --limit) against a full corpus first")
        cost = json.loads(cost_path.read_text(encoding="utf-8"))
    _write_summary(results, cost, args.out, write_cost=writes_cost)
    print(_summarise(results, cost))
    bad = [r["mode"] for r in fresh if not r["invariant_source_untouched"]]
    if bad:
        print(f"\n⛔ INVARIANT BROKEN — the source spool changed under: {bad}",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(_run())
